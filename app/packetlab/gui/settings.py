"""Persistent preferences with a declarative schema (drives the Settings window)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from .. import __version__


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "VU3INI PacketLab")
    os.makedirs(d, exist_ok=True)
    return d


def local_dir(*parts: str) -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "VU3INI PacketLab", *parts)
    os.makedirs(d, exist_ok=True)
    return d


def documents_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Documents", "VU3INI PacketLab")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = local_dir("projects")
    return d


# (group, key, label, type, default, extra, tooltip)
#   types: bool, int, float, str, choice, color, dir, file, font, password
SCHEMA: List[Tuple[str, str, str, str, Any, Any, str]] = [
    # ---------------------------------------------------------------- General
    ("General", "theme", "Theme", "choice", "dark", ["dark", "light"], "Colour theme of the whole application"),
    ("General", "accent", "Accent colour", "choice", "VU3INI blue",
     ["VU3INI blue", "VU3INI red", "Teal", "Purple", "Orange", "Green"], "Highlight colour for buttons and selection"),
    ("General", "ui_font_family", "Interface font", "font", "Segoe UI", None, "Font used by menus and panels"),
    ("General", "ui_font_size", "Interface font size", "int", 10, (7, 18), "Point size of the interface font"),
    ("General", "show_splash", "Show splash screen at start", "bool", True, None,
     "The splash also shows the Terms of Use; they must be accepted at least once"),
    ("General", "confirm_exit", "Ask before closing with unsaved changes", "bool", True, None, ""),
    ("General", "restore_last", "Open the last project at start", "bool", True, None, ""),
    ("General", "recent_max", "Recent files to remember", "int", 10, (1, 30), ""),
    ("General", "autosave_minutes", "Autosave every (minutes, 0 = off)", "int", 3, (0, 60),
     "Autosaves go to the local app-data folder, never over your file"),
    ("General", "projects_dir", "Default projects folder", "dir", "", None, "Where Open/Save dialogs start"),
    ("General", "check_updates", "Check for updates at start (uses the Internet)", "bool", False, None, ""),
    ("General", "update_url", "Update information URL", "str",
     "https://api.github.com/repos/hamvu3ini/VU3INI-PacketLab/releases/latest", None,
     "GitHub releases API URL or any JSON with a tag_name field"),
    # ---------------------------------------------------------------- Canvas
    ("Canvas", "grid_show", "Show grid", "bool", True, None, ""),
    ("Canvas", "grid_size", "Grid size (px)", "int", 20, (5, 100), ""),
    ("Canvas", "snap", "Snap devices to grid", "bool", True, None, ""),
    ("Canvas", "icon_size", "Device icon size (px)", "int", 46, (28, 96), ""),
    ("Canvas", "show_iface_labels", "Show interface names on links", "bool", True, None, ""),
    ("Canvas", "show_link_labels", "Show link profile / bandwidth labels", "bool", False, None, ""),
    ("Canvas", "show_ip_labels", "Show device IP addresses", "bool", False, None, ""),
    ("Canvas", "show_site_zones", "Show site zones (LAN / MAN / WAN)", "bool", True, None, ""),
    ("Canvas", "show_stp_blocked", "Mark spanning-tree blocked links", "bool", True, None, ""),
    ("Canvas", "antialias", "Smooth (antialiased) drawing", "bool", True, None, ""),
    ("Canvas", "link_width", "Link line width", "float", 2.0, (0.5, 8.0), ""),
    ("Canvas", "anim_ms", "Packet animation time per hop (ms)", "int", 450, (50, 3000), ""),
    ("Canvas", "zoom_step", "Mouse-wheel zoom step", "float", 1.15, (1.02, 2.0), ""),
    ("Canvas", "lan_color", "LAN zone colour", "color", "#1e88e5", None, ""),
    ("Canvas", "man_color", "MAN zone colour", "color", "#43a047", None, ""),
    ("Canvas", "wan_color", "WAN zone colour", "color", "#fb8c00", None, ""),
    ("Canvas", "default_profile", "Link profile for new cables", "str", "access-1g", None,
     "Name of a link profile; used when you draw a new cable"),
    # ---------------------------------------------------------------- Editor
    ("Editor", "editor_font", "YAML editor font", "font", "Consolas", None, ""),
    ("Editor", "editor_font_size", "YAML editor font size", "int", 10, (7, 24), ""),
    ("Editor", "editor_wrap", "Wrap long lines", "bool", False, None, ""),
    ("Editor", "live_validate", "Validate while typing", "bool", True, None, ""),
    ("Editor", "live_validate_ms", "Validation delay (ms)", "int", 600, (100, 5000), ""),
    # ---------------------------------------------------------------- Console
    ("Console", "console_font_size", "Console font size", "int", 10, (7, 24), ""),
    ("Console", "console_bg", "Console background", "color", "#0b1220", None, ""),
    ("Console", "console_fg", "Console text", "color", "#c8f7c5", None, ""),
    ("Console", "console_history", "Command history length", "int", 200, (10, 5000), ""),
    ("Console", "default_cli_style", "CLI style for new devices", "choice", "ios", ["ios", "junos", "frr"], ""),
    ("Console", "ping_count", "Echo requests per ping", "int", 5, (1, 100), ""),
    # ---------------------------------------------------------------- Simulation
    ("Simulation", "default_sim_mode", "Default mode", "choice", "realtime", ["realtime", "simulation"],
     "Realtime gives the answer at once; Simulation steps through every frame"),
    ("Simulation", "seed", "Random seed (repeatable runs)", "int", 1, (0, 2 ** 31 - 1), ""),
    ("Simulation", "proc_delay_ms", "Forwarding delay per router (ms)", "float", 0.02, (0.0, 50.0), ""),
    ("Simulation", "switch_delay_ms", "Forwarding delay per switch (ms)", "float", 0.005, (0.0, 50.0), ""),
    ("Simulation", "ospf_ref_bw_gbps", "OSPF reference bandwidth (Gbps)", "float", 100.0, (0.1, 4000.0), ""),
    ("Simulation", "dhcp_offset", "First host number handed out by DHCP", "int", 100, (2, 250), ""),
    ("Simulation", "max_hops", "Maximum hops (TTL limit)", "int", 64, (8, 255), ""),
    ("Simulation", "show_arp_events", "Show ARP events in Simulation mode", "bool", True, None, ""),
    ("Simulation", "show_l2_events", "Show switch-by-switch frame events", "bool", True, None, ""),
    ("Timers", "rstp_s", "RSTP re-convergence (s)", "float", 0.6, (0.01, 60.0), "IEEE 802.1w"),
    ("Timers", "stp_s", "Legacy 802.1D STP re-convergence (s)", "float", 30.0, (1.0, 120.0), ""),
    ("Timers", "g8032_s", "G.8032 ring switch time (s)", "float", 0.05, (0.005, 5.0), "ITU-T G.8032 target 50 ms"),
    ("Timers", "ospf_direct_s", "OSPF failover on carrier loss (s)", "float", 0.2, (0.01, 30.0), ""),
    ("Timers", "ospf_dead_s", "OSPF dead interval (s)", "float", 40.0, (1.0, 600.0), "RFC 2328 default 40 s"),
    ("Timers", "isis_direct_s", "IS-IS failover on carrier loss (s)", "float", 0.2, (0.01, 30.0), ""),
    ("Timers", "isis_hold_s", "IS-IS hold time (s)", "float", 30.0, (1.0, 600.0), ""),
    ("Timers", "bgp_direct_s", "BGP fast external fall-over (s)", "float", 0.5, (0.01, 30.0), ""),
    ("Timers", "bgp_hold_s", "BGP hold time (s)", "float", 90.0, (3.0, 65535.0), "RFC 4271 default 90 s"),
    ("Timers", "bfd_s", "BFD detection time (s)", "float", 0.15, (0.003, 10.0), "RFC 5880, 3 x 50 ms"),
    # ---------------------------------------------------------------- Tests
    ("Tests", "stop_on_static_fail", "Skip later phases when static checks fail", "bool", True, None, ""),
    ("Tests", "mc_samples", "Monte-Carlo samples per performance test", "int", 20000, (100, 5000000),
     "More samples = smoother statistics; the GPU helps with very large counts"),
    ("Tests", "auto_export", "Save JSON + JUnit results after each run", "bool", False, None, ""),
    ("Tests", "results_dir", "Results folder", "dir", "", None, "Empty = next to the topology file"),
    # ---------------------------------------------------------------- Compute
    ("Compute", "compute_mode", "Compute device", "choice", "auto", ["auto", "cpu", "gpu", "hybrid"],
     "auto = GPU if available else CPU; hybrid = GPU and CPU together; any GPU error falls back to CPU"),
    ("Compute", "gpu_mem_limit_mb", "GPU memory limit (MB, 0 = no limit)", "int", 0, (0, 131072), ""),
    ("Compute", "cpu_threads", "CPU threads (0 = automatic)", "int", 0, (0, 256), ""),
    ("Compute", "hybrid_gpu_share", "Hybrid mode: share of work on the GPU", "float", 0.7, (0.05, 0.95), ""),
    ("Compute", "cuda_variant", "GPU add-on CUDA variant", "choice", "cuda12x", ["cuda12x"],
     "CUDA 12.9 runtime: needs an NVIDIA driver 525 or newer (RTX 20xx-50xx)"),
    # ---------------------------------------------------------------- Emulation
    ("Emulation", "emu_backend", "Emulation back end", "choice", "none", ["none", "wsl", "ssh", "local"],
     "Run exported labs on real router software with Containerlab via WSL2, SSH or a local install"),
    ("Emulation", "emu_wsl_distro", "WSL distribution (empty = default)", "str", "", None, ""),
    ("Emulation", "emu_ssh_target", "SSH target (user@host)", "str", "", None, "For example lab@192.168.1.50"),
    ("Emulation", "emu_remote_dir", "Remote lab folder", "str", "~/packetlab", None, ""),
    ("Emulation", "emu_clab_cmd", "Containerlab command", "str", "containerlab", None, ""),
    ("Emulation", "emu_sudo", "Use sudo", "bool", True, None, ""),
    ("Emulation", "export_dir", "Export folder", "dir", "", None, "Empty = <project>_export next to the file"),
    ("Emulation", "image_router", "Router / L3 switch image", "str", "quay.io/frrouting/frr:10.2.1", None, ""),
    ("Emulation", "image_host", "Host / switch image", "str", "ghcr.io/hellt/network-multitool", None, ""),
    # ---------------------------------------------------------------- Captures
    ("Captures", "capture_dir", "Capture folder", "dir", "", None, "Empty = Documents/VU3INI PacketLab/captures"),
    ("Captures", "open_in_wireshark", "Open captures in Wireshark after saving", "bool", False, None, ""),
    ("Captures", "wireshark_path", "Wireshark program", "file", "C:/Program Files/Wireshark/Wireshark.exe", None, ""),
]

DEFAULTS: Dict[str, Any] = {k: d for (_g, k, _l, _t, d, _x, _tt) in SCHEMA}
DEFAULTS.update({"terms_accepted": "", "recent": [], "last_project": "", "geometry": "", "state": ""})


class Settings:
    def __init__(self, path: str = ""):
        self.path = path or os.path.join(data_dir(), "settings.json")
        self.v: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, val in data.items():
                    if k in DEFAULTS:
                        self.v[k] = val
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.v, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def __getitem__(self, k):
        return self.v.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, val):
        self.v[k] = val

    def get(self, k, default=None):
        return self.v.get(k, DEFAULTS.get(k, default))

    def reset(self) -> None:
        keep = {k: self.v.get(k) for k in ("terms_accepted", "recent", "last_project")}
        self.v = dict(DEFAULTS)
        self.v.update(keep)

    def add_recent(self, path: str) -> None:
        rec = [p for p in self.v.get("recent", []) if p != path]
        rec.insert(0, path)
        self.v["recent"] = rec[: int(self["recent_max"])]

    def engine_settings(self) -> dict:
        """Settings handed to the simulation engine and test runner."""
        return {
            "ospf_ref_bw": float(self["ospf_ref_bw_gbps"]) * 1e9,
            "dhcp_offset": int(self["dhcp_offset"]),
            "proc_delay_ms": float(self["proc_delay_ms"]),
            "switch_delay_ms": float(self["switch_delay_ms"]),
            "max_hops": int(self["max_hops"]),
            "ping_count": int(self["ping_count"]),
            "seed": int(self["seed"]),
            "mc_samples": int(self["mc_samples"]),
            "timers": {k: float(self[k]) for k in ("rstp_s", "stp_s", "g8032_s", "ospf_direct_s", "ospf_dead_s",
                                                    "isis_direct_s", "isis_hold_s", "bgp_direct_s", "bgp_hold_s",
                                                    "bfd_s")},
        }

    @property
    def terms_ok(self) -> bool:
        return self.v.get("terms_accepted") == TERMS_VERSION


TERMS_VERSION = "2026-11-25"
VERSION = __version__
