"""Compute backend: CPU (NumPy), NVIDIA GPU (CuPy) or both at once.

Used for the heavy numeric parts of PacketLab:
  * all-pairs latency / hop matrices for large topologies (Floyd-Warshall, vectorised)
  * Monte-Carlo traffic sampling for TWAMP / Y.1564 / RFC 2544 style statistics

GPU support is optional.  CuPy is looked for in the normal import path and in the
user add-ons folder (Settings > Compute > Download GPU add-on).  Any GPU error,
including running out of GPU memory, falls back to the CPU automatically and is
reported in the log rather than failing the operation.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

import numpy as np

_cp = None
_cp_error: Optional[str] = None
_lock = threading.Lock()


def addons_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "VU3INI PacketLab", "addons")


def _try_cupy():
    global _cp, _cp_error
    with _lock:
        if _cp is not None or _cp_error is not None:
            return _cp
        ad = addons_dir()
        if os.path.isdir(ad) and ad not in sys.path:
            sys.path.insert(0, ad)
            # CUDA runtime DLLs shipped as nvidia-* wheels live under addons/nvidia/*/bin
            nv = os.path.join(ad, "nvidia")
            if os.path.isdir(nv) and hasattr(os, "add_dll_directory"):
                for sub in os.listdir(nv):
                    b = os.path.join(nv, sub, "bin")
                    if os.path.isdir(b):
                        try:
                            os.add_dll_directory(b)
                        except OSError:
                            pass
        try:
            import cupy  # type: ignore
            n = cupy.cuda.runtime.getDeviceCount()
            if n < 1:
                raise RuntimeError("no CUDA device")
            cupy.zeros(1).sum()  # touch the device
            _cp = cupy
        except Exception as e:  # noqa: BLE001 - any failure means "no GPU"
            _cp_error = f"{type(e).__name__}: {e}"
            _cp = None
        return _cp


def gpu_info() -> dict:
    cp = _try_cupy()
    if cp is None:
        return {"available": False, "reason": _cp_error or "CuPy not installed"}
    try:
        props = cp.cuda.runtime.getDeviceProperties(0)
        free, total = cp.cuda.runtime.memGetInfo()
        name = props.get("name", b"GPU")
        name = name.decode() if isinstance(name, bytes) else str(name)
        return {"available": True, "name": name, "total_mb": total // 2 ** 20, "free_mb": free // 2 ** 20,
                "cupy": cp.__version__}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def reset_detection() -> None:
    global _cp, _cp_error
    with _lock:
        _cp, _cp_error = None, None


class Compute:
    """mode: auto | cpu | gpu | hybrid"""

    def __init__(self, mode: str = "auto", gpu_mem_limit_mb: int = 0, threads: int = 0,
                 hybrid_gpu_share: float = 0.7, log: Optional[Callable[[str], None]] = None):
        self.mode = mode
        self.gpu_mem_limit_mb = int(gpu_mem_limit_mb or 0)
        self.threads = int(threads or 0) or max(1, (os.cpu_count() or 2) - 1)
        self.hybrid_gpu_share = min(0.95, max(0.05, float(hybrid_gpu_share)))
        self.log = log or (lambda m: None)
        self.last_device = "cpu"
        self.fallbacks = 0

    # ------------------------------------------------------------- device choice
    def _gpu(self):
        if self.mode == "cpu":
            return None
        cp = _try_cupy()
        if cp is None:
            if self.mode in ("gpu", "hybrid"):
                self.log(f"GPU not available ({_cp_error}); using CPU")
            return None
        if self.gpu_mem_limit_mb > 0:
            try:
                cp.get_default_memory_pool().set_limit(size=self.gpu_mem_limit_mb * 2 ** 20)
            except Exception:  # noqa: BLE001
                pass
        return cp

    def describe(self) -> str:
        info = gpu_info() if self.mode != "cpu" else {"available": False, "reason": "CPU mode selected"}
        if info.get("available"):
            return f"{self.mode}: GPU {info['name']} ({info['free_mb']} MB free) + CPU {self.threads} threads"
        return f"{self.mode}: CPU {self.threads} threads ({info.get('reason')})"

    def _run(self, fn_gpu, fn_cpu, *args):
        cp = self._gpu()
        if cp is not None:
            try:
                t = time.perf_counter()
                out = fn_gpu(cp, *args)
                self.last_device = "gpu"
                self.log(f"GPU computation finished in {1000 * (time.perf_counter() - t):.1f} ms")
                return out
            except Exception as e:  # noqa: BLE001  (OutOfMemoryError, CUDA errors)
                self.fallbacks += 1
                kind = "out of GPU memory" if "OutOfMemory" in type(e).__name__ else type(e).__name__
                self.log(f"GPU failed ({kind}); falling back to CPU")
                try:
                    cp.get_default_memory_pool().free_all_blocks()
                except Exception:  # noqa: BLE001
                    pass
        self.last_device = "cpu"
        return fn_cpu(*args)

    # ------------------------------------------------------------- all pairs
    def all_pairs(self, w: np.ndarray) -> np.ndarray:
        """Floyd-Warshall on a dense weight matrix (inf = no edge)."""
        w = np.asarray(w, dtype=np.float64)

        def cpu(m):
            d = m.copy()
            np.fill_diagonal(d, 0.0)
            for k in range(d.shape[0]):
                np.minimum(d, d[:, k:k + 1] + d[k:k + 1, :], out=d)
            return d

        def gpu(cp, m):
            d = cp.asarray(m)
            cp.fill_diagonal(d, 0.0)
            for k in range(d.shape[0]):
                cp.minimum(d, d[:, k:k + 1] + d[k:k + 1, :], out=d)
            return cp.asnumpy(d)

        return self._run(gpu, cpu, w)

    # ------------------------------------------------------------- monte carlo
    def sample_path(self, delay_ms: float, jitter_ms: float, loss: float, n: int, seed: int = 1) -> dict:
        """Sample one-way delay for n packets: returns stats dict."""
        n = max(1, int(n))

        def cpu_part(count, sd):
            rng = np.random.default_rng(sd)
            d = delay_ms + np.abs(rng.normal(0.0, jitter_ms, count)) if jitter_ms > 0 else np.full(count, delay_ms)
            lost = rng.random(count) < loss
            return d[~lost], int(lost.sum())

        def gpu_part(cp, count, sd):
            rs = cp.random.RandomState(sd)
            d = delay_ms + cp.abs(rs.normal(0.0, jitter_ms, count)) if jitter_ms > 0 else cp.full(count, delay_ms)
            lost = rs.random_sample(count) < loss
            return cp.asnumpy(d[~lost]), int(lost.sum())

        parts = []
        cp = self._gpu() if self.mode in ("hybrid",) else None
        if cp is not None and n > 1000:
            g = int(n * self.hybrid_gpu_share)
            c = n - g
            with ThreadPoolExecutor(max_workers=2) as ex:
                fg = ex.submit(lambda: self._run(gpu_part, lambda a, b: cpu_part(a, b), g, seed))
                fc = ex.submit(cpu_part, c, seed + 7919)
                parts = [fg.result(), fc.result()]
            self.last_device = "gpu+cpu"
        else:
            parts = [self._run(gpu_part, cpu_part, n, seed)]
        delays = np.concatenate([p[0] for p in parts]) if parts else np.array([])
        lost = sum(p[1] for p in parts)
        if delays.size == 0:
            return {"n": n, "lost": lost, "loss": 1.0, "mean": None, "p95": None, "p99": None, "max": None,
                    "jitter": None, "device": self.last_device}
        diffs = np.abs(np.diff(delays)) if delays.size > 1 else np.array([0.0])
        return {"n": n, "lost": lost, "loss": lost / n, "mean": float(delays.mean()),
                "p95": float(np.percentile(delays, 95)), "p99": float(np.percentile(delays, 99)),
                "max": float(delays.max()), "min": float(delays.min()),
                "jitter": float(diffs.mean()),  # RFC 3550-style mean inter-packet delay variation
                "device": self.last_device}

    def benchmark(self, size: int = 400) -> List[str]:
        rng = np.random.default_rng(0)
        m = rng.random((size, size)) * 10
        m[m > 7] = np.inf
        out = []
        for mode in ("cpu", "gpu"):
            old = self.mode
            self.mode = mode
            t = time.perf_counter()
            self.all_pairs(m)
            out.append(f"{mode.upper()} ({self.last_device}): Floyd-Warshall {size}x{size} in "
                       f"{1000 * (time.perf_counter() - t):.0f} ms")
            self.mode = old
        return out
