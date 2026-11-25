"""Lab controller: owns the editable topology dictionary, the compiled model and the engine.

Both the GUI and the CLI change the network only through this class so that the
canvas, the YAML editor and the device consoles always show the same state.
"""
from __future__ import annotations

import copy
import os
from typing import Callable, Dict, List, Optional

from . import model
from .compute import Compute
from .engine import Engine

BLANK = {
    "nstopo": "0.1",
    "meta": {"name": "untitled", "author": "VU3INI", "description": ""},
    "defaults": {"mode": "sim", "cli_style": "ios"},
    "sites": {},
    "link_profiles": {
        "access-1g": {"bandwidth": "1Gbps", "delay": "0.02ms", "media": "copper"},
        "campus-10g": {"bandwidth": "10Gbps", "delay": "0.05ms", "media": "fibre"},
        "wan-100m": {"bandwidth": "100Mbps", "delay": "15ms", "jitter": "1ms", "loss": "0.01%"},
    },
    "addressing": {"p2p": {"ipv4": "10.254.0.0/24", "purpose": "p2p"},
                   "loopbacks": {"ipv4": "10.255.0.0/24", "purpose": "loopback"}},
    "nodes": {},
    "links": [],
    "tests": [],
    "layout": {"nodes": {}},
}


class Lab:
    def __init__(self, settings: Optional[dict] = None, compute: Optional[Compute] = None):
        self.settings = settings or {}
        self.compute = compute or Compute("cpu")
        self.raw: dict = copy.deepcopy(BLANK)
        self.path: Optional[str] = None
        self.dirty = False
        self.topo: model.Topology = model.build(self.raw)
        self.down_links: set = set()
        self.down_nodes: set = set()
        self._engine: Optional[Engine] = None
        self.listeners: List[Callable[[str], None]] = []
        self.undo_stack: List[dict] = []
        self.redo_stack: List[dict] = []

    # ------------------------------------------------------------------ notify
    def subscribe(self, fn: Callable[[str], None]) -> None:
        self.listeners.append(fn)

    def _emit(self, what: str) -> None:
        for fn in list(self.listeners):
            try:
                fn(what)
            except Exception:  # noqa: BLE001 - a broken view must not break the model
                pass

    # ------------------------------------------------------------------ load/save
    def new(self) -> None:
        self.raw = copy.deepcopy(BLANK)
        self.path = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._rebuild(dirty=False)

    def load_file(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.load_text(text)
        self.path = path
        self.dirty = False
        self._emit("loaded")

    def load_text(self, text: str, keep_undo: bool = False) -> None:
        import yaml
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise model.TopologyError(f"YAML syntax error: {e}") from e
        if not isinstance(raw, dict):
            raise model.TopologyError("Topology file must be a YAML mapping")
        raw = model.fix_yaml_keys(raw)
        if keep_undo:
            self._push_undo()
        else:
            self.undo_stack.clear()
            self.redo_stack.clear()
        self.raw = raw
        self.raw.setdefault("layout", {}).setdefault("nodes", {})
        self.down_links.clear()
        self.down_nodes.clear()
        self._rebuild(dirty=keep_undo)

    def save(self, path: Optional[str] = None) -> str:
        path = path or self.path
        if not path:
            raise ValueError("no file name")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.text())
        os.replace(tmp, path)
        self.path = path
        self.dirty = False
        self._emit("saved")
        return path

    def text(self) -> str:
        return model.dump_text(self.raw)

    # ------------------------------------------------------------------ edits
    def _push_undo(self) -> None:
        self.undo_stack.append(copy.deepcopy(self.raw))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def edit(self, fn: Callable[[dict], None], what: str = "edited") -> None:
        """Apply fn(raw) as one undoable change."""
        self._push_undo()
        fn(self.raw)
        self._rebuild(dirty=True, what=what)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(copy.deepcopy(self.raw))
        self.raw = self.undo_stack.pop()
        self._rebuild(dirty=True)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(copy.deepcopy(self.raw))
        self.raw = self.redo_stack.pop()
        self._rebuild(dirty=True)
        return True

    def _rebuild(self, dirty: bool = True, what: str = "changed") -> None:
        self.topo = model.build(self.raw)
        self.down_links = {i for i in self.down_links if i < len(self.topo.links)}
        self.down_nodes = {n for n in self.down_nodes if n in self.topo.nodes}
        self._engine = None
        if dirty:
            self.dirty = True
        self._emit(what)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = Engine(self.topo, self.settings, set(self.down_links), set(self.down_nodes))
        return self._engine

    def set_failure(self, link: Optional[int] = None, node: Optional[str] = None, down: bool = True) -> None:
        if link is not None:
            (self.down_links.add if down else self.down_links.discard)(link)
        if node is not None:
            (self.down_nodes.add if down else self.down_nodes.discard)(node)
        self._engine = None
        self._emit("state")

    def clear_failures(self) -> None:
        self.down_links.clear()
        self.down_nodes.clear()
        self._engine = None
        self._emit("state")

    # ------------------------------------------------------------------ helpers used by GUI and CLI
    def unique_name(self, base: str) -> str:
        nodes = self.raw.get("nodes") or {}
        k = 1
        while f"{base}{k}" in nodes:
            k += 1
        return f"{base}{k}"

    def add_node(self, kind: str, x: float, y: float, name: Optional[str] = None) -> str:
        prefix = {"router": "R", "l3switch": "L3SW", "switch": "SW", "firewall": "FW", "host": "PC",
                  "server": "SRV", "ap": "AP", "cloud": "Cloud", "external": "EXT"}.get(kind, "N")
        name = name or self.unique_name(prefix)

        def fn(raw):
            nd = {"kind": kind}
            if kind in ("switch", "l3switch", "ap"):
                nd["vlans"] = {1: "default"}
            if kind in ("host", "server"):
                nd["interfaces"] = {"eth0": {"ipv4": "dhcp"}}
            raw.setdefault("nodes", {})[name] = nd
            raw.setdefault("layout", {}).setdefault("nodes", {})[name] = {"x": round(x), "y": round(y)}
        self.edit(fn, "node-added")
        return name

    def next_iface(self, node: str) -> str:
        n = (self.raw.get("nodes") or {}).get(node) or {}
        kind = n.get("kind", "router")
        linked = set()
        for l in self.raw.get("links") or []:
            for side in ("a", "b"):
                ep = str(l.get(side, ""))
                if ep.startswith(node + ":"):
                    linked.add(ep.split(":", 1)[1])
        # an interface that is declared but not yet cabled is offered first
        for ifn, d in (n.get("interfaces") or {}).items():
            low = str(ifn).lower()
            if ifn not in linked and not low.startswith(("vlan", "lo", "tun", "po")) and \
                    not (isinstance(d, dict) and (d.get("tunnel") or d.get("lag"))):
                return str(ifn)
        used = set((n.get("interfaces") or {}).keys()) | linked
        if kind in ("host", "server", "external"):
            pat = "eth{}"
            start = 0
        elif kind == "cloud":
            pat, start = "port{}", 1
        elif kind in ("switch", "l3switch", "ap"):
            pat, start = "Gi0/{}", 1
        else:
            pat, start = "Gi0/{}", 0
        k = start
        while pat.format(k) in used:
            k += 1
        return pat.format(k)

    def add_link(self, a: str, a_if: str, b: str, b_if: str, profile: Optional[str] = None) -> None:
        def fn(raw):
            l = {"a": f"{a}:{a_if}", "b": f"{b}:{b_if}"}
            if profile:
                l["profile"] = profile
            raw.setdefault("links", []).append(l)
        self.edit(fn, "link-added")

    def delete_node(self, name: str) -> None:
        def fn(raw):
            (raw.get("nodes") or {}).pop(name, None)
            raw["links"] = [l for l in raw.get("links") or []
                            if not str(l.get("a", "")).startswith(name + ":") and
                            not str(l.get("b", "")).startswith(name + ":")]
            (raw.get("layout") or {}).get("nodes", {}).pop(name, None)
        self.edit(fn, "node-deleted")

    def delete_link(self, idx: int) -> None:
        def fn(raw):
            if 0 <= idx < len(raw.get("links") or []):
                raw["links"].pop(idx)
        self.down_links = {i if i < idx else i - 1 for i in self.down_links if i != idx}
        self.edit(fn, "link-deleted")

    def rename_node(self, old: str, new: str) -> None:
        if not new or new == old:
            return
        if new in (self.raw.get("nodes") or {}):
            raise ValueError(f"a node named '{new}' already exists")

        def fn(raw):
            nodes = raw.get("nodes") or {}
            raw["nodes"] = {(new if k == old else k): v for k, v in nodes.items()}
            for v in raw["nodes"].values():
                if isinstance(v, dict) and v.get("inherit") == old:
                    v["inherit"] = new
                for it in ((v or {}).get("interfaces") or {}).values() if isinstance(v, dict) else []:
                    if isinstance(it, dict) and isinstance(it.get("tunnel"), dict) and it["tunnel"].get("peer") == old:
                        it["tunnel"]["peer"] = new
            for l in raw.get("links") or []:
                for side in ("a", "b"):
                    ep = str(l.get(side, ""))
                    if ep.startswith(old + ":"):
                        l[side] = new + ep[len(old):]
            lay = (raw.get("layout") or {}).get("nodes", {})
            if old in lay:
                lay[new] = lay.pop(old)
            for t in raw.get("tests") or []:
                for k in ("on", "from", "to"):
                    if t.get(k) == old:
                        t[k] = new
        if old in self.down_nodes:
            self.down_nodes.discard(old)
            self.down_nodes.add(new)
        self.edit(fn, "node-renamed")

    def move_node(self, name: str, x: float, y: float) -> None:
        lay = self.raw.setdefault("layout", {}).setdefault("nodes", {})
        lay[name] = {"x": round(x), "y": round(y)}
        self.dirty = True

    def node_raw(self, name: str) -> dict:
        return self.raw.setdefault("nodes", {}).setdefault(name, {})

    def iface_raw(self, node: str, ifname: str) -> dict:
        n = self.node_raw(node)
        return n.setdefault("interfaces", {}).setdefault(ifname, {})
