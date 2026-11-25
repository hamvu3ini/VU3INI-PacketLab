"""NSTopo topology model: loading, normalisation, IPAM and validation.

The topology file (``*.nstopo.yaml``) is the single source of truth.  This module
turns the raw YAML dictionary into typed objects the engine can work with and
collects every validation problem with a precise location.
"""
from __future__ import annotations

import copy
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

SCHEMA_VERSION = "0.1"

TOP_KEYS = {
    "nstopo", "meta", "defaults", "sites", "link_profiles", "addressing", "nodes",
    "links", "services", "policies", "scenarios", "tests", "capture", "layout",
}
NODE_KEYS = {
    "kind", "inherit", "role", "site", "model", "image", "mode", "interfaces", "vlans",
    "stp", "routing", "services", "policies", "config", "cli_style", "description",
}
IFACE_KEYS = {
    "ipv4", "ipv6", "mode", "vlan", "allowed_vlans", "native_vlan", "lag", "vrf",
    "voice_vlan", "portfast", "gateway", "tunnel", "shutdown", "description", "helper",
    "acl_in", "acl_out", "ospf_cost", "dns", "bandwidth",
}
KINDS = {"router", "l3switch", "switch", "firewall", "host", "server", "ap", "cloud", "external"}
BRIDGING_KINDS = {"switch", "l3switch", "ap"}
ROUTING_KINDS = {"router", "l3switch", "firewall", "cloud"}
ENDPOINT_KINDS = {"host", "server", "external"}
SCOPES = {"lan", "man", "wan"}

DEFAULT_PROFILE = {"bandwidth": "1Gbps", "delay": "0.05ms"}


class TopologyError(Exception):
    """Raised when a topology cannot be loaded at all (e.g. YAML syntax)."""


# --------------------------------------------------------------------------- units
_BW = {"bps": 1, "kbps": 1e3, "mbps": 1e6, "gbps": 1e9, "tbps": 1e12}
_TIME = {"us": 1e-3, "µs": 1e-3, "ms": 1.0, "s": 1000.0, "sec": 1000.0}


def parse_bandwidth(v: Any, default: float = 1e9) -> float:
    """Return bits per second from '10Gbps', '100 Mbps', 1e9 ..."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([kKmMgGtT]?bps)\s*", str(v))
    if not m:
        raise ValueError(f"bad bandwidth '{v}' (use e.g. 100Mbps, 10Gbps)")
    return float(m.group(1)) * _BW[m.group(2).lower()]


def parse_time_ms(v: Any, default: float = 0.0) -> float:
    """Return milliseconds from '5ms', '50us', '1s', 0.2 ..."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(us|µs|ms|s|sec)\s*", str(v))
    if not m:
        raise ValueError(f"bad time '{v}' (use e.g. 5ms, 50us, 1s)")
    return float(m.group(1)) * _TIME[m.group(2)]


def parse_percent(v: Any, default: float = 0.0) -> float:
    """Return a fraction 0..1 from '0.3%', 0.003 ..."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v) / 100.0 if v > 1 else float(v)
    s = str(v).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    return float(s)


def fmt_bw(bps: float) -> str:
    for unit, div in (("Tbps", 1e12), ("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if bps >= div:
            val = bps / div
            return f"{val:g}{unit}" if val == int(val) else f"{val:.2f}{unit}"
    return f"{bps:g}bps"


def deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = copy.deepcopy(base)
        for k, v in over.items():
            out[k] = deep_merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def parse_vlan_list(v: Any) -> Optional[set]:
    """[10,20,'30-35'] or '10,20,30-35' or 'all' -> set; None means all."""
    if v is None or v == "all":
        return None
    items = v if isinstance(v, list) else str(v).split(",")
    out = set()
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        if "-" in s:
            a, b = s.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(s))
    return out


# --------------------------------------------------------------------------- types
@dataclass
class LinkProfile:
    name: str
    bandwidth: float = 1e9
    delay_ms: Optional[float] = None
    jitter_ms: float = 0.0
    loss: float = 0.0
    mtu: int = 1500
    media: str = "copper"


@dataclass
class Iface:
    node: str
    name: str
    mode: str = "routed"           # access | trunk | routed | host
    vlan: int = 1
    allowed: Optional[set] = None  # None = all VLANs
    native: int = 1
    voice_vlan: Optional[int] = None
    ip: Optional[ipaddress.IPv4Interface] = None
    ipv6: Optional[str] = None
    dhcp: bool = False
    gateway: Optional[ipaddress.IPv4Address] = None
    dns: Optional[ipaddress.IPv4Address] = None
    shutdown: bool = False
    lag_members: List[str] = field(default_factory=list)
    svi_vlan: Optional[int] = None
    tunnel: Optional[dict] = None
    helper: List[ipaddress.IPv4Address] = field(default_factory=list)
    acl_in: Optional[str] = None
    acl_out: Optional[str] = None
    ospf_cost: Optional[int] = None
    description: str = ""
    loopback: bool = False
    portfast: bool = False
    declared: bool = True
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.node}:{self.name}"

    @property
    def is_l3(self) -> bool:
        return self.mode in ("routed", "host") or self.svi_vlan is not None or self.loopback \
            or self.tunnel is not None


@dataclass
class Node:
    name: str
    kind: str
    site: Optional[str] = None
    role: str = ""
    mode: str = "sim"
    cli_style: str = "ios"
    model: str = ""
    image: str = ""
    ifaces: Dict[str, Iface] = field(default_factory=dict)
    vlans: Dict[int, str] = field(default_factory=dict)
    stp: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    services: dict = field(default_factory=dict)
    policies: dict = field(default_factory=dict)
    member_of: Dict[str, str] = field(default_factory=dict)  # physical port -> LAG
    raw: dict = field(default_factory=dict)

    @property
    def bridges(self) -> bool:
        return self.kind in BRIDGING_KINDS

    @property
    def routes(self) -> bool:
        return self.kind in ROUTING_KINDS

    def port_of(self, ifname: str) -> str:
        """Logical port (LAG) a physical interface belongs to."""
        return self.member_of.get(ifname, ifname)


@dataclass
class Link:
    idx: int
    a_node: str
    a_if: str
    b_node: str
    b_if: str
    profile: LinkProfile
    type: str = "copper"
    length_km: Optional[float] = None
    label: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.label or f"{self.a_node}:{self.a_if}--{self.b_node}:{self.b_if}"

    @property
    def delay_ms(self) -> float:
        if self.profile.delay_ms is not None:
            d = self.profile.delay_ms
            if self.length_km:
                d = max(d, self.length_km * 0.005)
            return d
        if self.length_km:
            return self.length_km * 0.005  # 5 us per km of fibre
        return 0.01

    def other(self, node: str, ifname: str) -> Tuple[str, str]:
        if (node, ifname) == (self.a_node, self.a_if):
            return self.b_node, self.b_if
        return self.a_node, self.a_if

    def touches(self, node: str) -> bool:
        return node in (self.a_node, self.b_node)


@dataclass
class Topology:
    raw: dict
    meta: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    sites: Dict[str, dict] = field(default_factory=dict)
    profiles: Dict[str, LinkProfile] = field(default_factory=dict)
    pools: Dict[str, dict] = field(default_factory=dict)
    nodes: Dict[str, Node] = field(default_factory=dict)
    links: List[Link] = field(default_factory=list)
    services: Dict[str, dict] = field(default_factory=dict)
    policies: dict = field(default_factory=dict)
    scenarios: Dict[str, dict] = field(default_factory=dict)
    tests: List[dict] = field(default_factory=list)
    capture: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    def iface(self, node: str, ifname: str) -> Optional[Iface]:
        n = self.nodes.get(node)
        return n.ifaces.get(ifname) if n else None

    def link_by_name(self, name: str) -> Optional[Link]:
        for l in self.links:
            if l.label == name or l.name == name:
                return l
        return None

    def links_of(self, node: str) -> List[Link]:
        return [l for l in self.links if l.touches(node)]

    def ip_owner(self, ip) -> Optional[Iface]:
        ip = ipaddress.ip_address(str(ip))
        for n in self.nodes.values():
            for i in n.ifaces.values():
                if i.ip is not None and i.ip.ip == ip:
                    return i
        return None

    def resolve_host(self, target: str) -> Optional[ipaddress.IPv4Address]:
        """Node name, node:iface or IP -> IPv4 address (first usable)."""
        try:
            return ipaddress.ip_address(str(target))
        except ValueError:
            pass
        if ":" in target:
            n, i = target.split(":", 1)
            ifc = self.iface(n, i)
            return ifc.ip.ip if ifc and ifc.ip else None
        n = self.nodes.get(target)
        if not n:
            return None
        cands = [i for i in n.ifaces.values() if i.ip is not None and not i.shutdown]
        # prefer loopbacks for routers, then non-tunnel interfaces
        cands.sort(key=lambda i: (not i.loopback, i.tunnel is not None, i.name))
        return cands[0].ip.ip if cands else None


# --------------------------------------------------------------------------- loader
def load_file(path: str) -> Topology:
    with open(path, "r", encoding="utf-8") as f:
        return load_text(f.read())


def load_text(text: str) -> Topology:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise TopologyError(f"YAML syntax error: {e}") from e
    if not isinstance(raw, dict):
        raise TopologyError("Topology file must be a YAML mapping at the top level")
    return build(fix_yaml_keys(raw))


def fix_yaml_keys(obj):
    """YAML 1.1 turns keys like on/off/yes/no into booleans; turn them back into words."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k is True:
                k = "on"
            elif k is False:
                k = "off"
            out[k] = fix_yaml_keys(v)
        return out
    if isinstance(obj, list):
        return [fix_yaml_keys(x) for x in obj]
    return obj


def dump_text(raw: dict) -> str:
    return yaml.safe_dump(raw, sort_keys=False, default_flow_style=None, width=110, allow_unicode=True)


def _ip_iface(v: Any, where: str, errors: List[str]) -> Optional[ipaddress.IPv4Interface]:
    try:
        ifc = ipaddress.ip_interface(str(v))
        if ifc.version != 4:
            errors.append(f"{where}: ipv4 field holds an IPv6 address '{v}'")
            return None
        if "/" not in str(v):
            errors.append(f"{where}: ipv4 '{v}' needs a prefix length (e.g. /24)")
            return None
        return ifc
    except ValueError:
        errors.append(f"{where}: invalid IPv4 address '{v}'")
        return None


class _Ipam:
    def __init__(self, pools: Dict[str, dict], errors: List[str]):
        self.gen: Dict[str, Any] = {}
        self.pools = pools
        self.errors = errors
        self.used: set = set()

    def mark_used(self, net):
        self.used.add(net)

    def take(self, purpose: str, plen: int):
        for name, p in self.pools.items():
            if p.get("purpose") != purpose or not p.get("ipv4"):
                continue
            try:
                parent = ipaddress.ip_network(str(p["ipv4"]), strict=False)
            except ValueError:
                continue
            key = (name, plen)
            if key not in self.gen:
                self.gen[key] = parent.subnets(new_prefix=plen)
            for sub in self.gen[key]:
                if not any(sub.overlaps(u) for u in self.used):
                    self.used.add(sub)
                    return sub
            self.errors.append(f"addressing.{name}: pool exhausted")
            return None
        return None


def build(raw: dict) -> Topology:
    """Normalise a raw NSTopo dictionary into a Topology. Never raises for content errors."""
    raw = fix_yaml_keys(raw)
    t = Topology(raw=raw)
    err, warn = t.errors, t.warnings

    ver = raw.get("nstopo")
    if ver is None:
        err.append("nstopo: schema version missing (add  nstopo: \"0.1\")")
    elif str(ver) != SCHEMA_VERSION:
        warn.append(f"nstopo: version {ver} differs from supported {SCHEMA_VERSION}")
    for k in raw:
        if k not in TOP_KEYS:
            err.append(f"{k}: unknown top-level key")

    t.meta = raw.get("meta") or {}
    t.defaults = raw.get("defaults") or {}
    t.sites = raw.get("sites") or {}
    for sid, s in t.sites.items():
        if not isinstance(s, dict):
            err.append(f"sites.{sid}: must be a mapping")
            continue
        if s.get("scope") and s["scope"] not in SCOPES:
            err.append(f"sites.{sid}.scope: must be lan, man or wan (got '{s['scope']}')")

    # link profiles
    for pid, p in (raw.get("link_profiles") or {}).items():
        p = p or {}
        try:
            t.profiles[pid] = LinkProfile(
                name=pid,
                bandwidth=parse_bandwidth(p.get("bandwidth"), 1e9),
                delay_ms=parse_time_ms(p["delay"]) if p.get("delay") is not None else None,
                jitter_ms=parse_time_ms(p.get("jitter"), 0.0),
                loss=parse_percent(p.get("loss"), 0.0),
                mtu=int(p.get("mtu", 1500)),
                media=str(p.get("media", "copper")),
            )
        except ValueError as e:
            err.append(f"link_profiles.{pid}: {e}")
    t.pools = raw.get("addressing") or {}
    t.services = raw.get("services") or {}
    t.policies = raw.get("policies") or {}
    t.scenarios = raw.get("scenarios") or {}
    t.tests = raw.get("tests") or []
    t.capture = raw.get("capture") or {}

    ipam = _Ipam(t.pools, err)

    # ------------------------------------------------------------ nodes
    raw_nodes = raw.get("nodes") or {}
    if not isinstance(raw_nodes, dict):
        err.append("nodes: must be a mapping of hostname -> node")
        raw_nodes = {}
    resolved: Dict[str, dict] = {}

    def resolve(name: str, stack=()):
        if name in resolved:
            return resolved[name]
        nd = raw_nodes.get(name) or {}
        if not isinstance(nd, dict):
            err.append(f"nodes.{name}: must be a mapping")
            nd = {}
        parent = nd.get("inherit")
        if parent:
            if parent in stack or parent == name:
                err.append(f"nodes.{name}.inherit: circular inheritance via '{parent}'")
                base = {}
            elif parent not in raw_nodes:
                err.append(f"nodes.{name}.inherit: unknown node '{parent}'")
                base = {}
            else:
                base = resolve(parent, stack + (name,))
            nd = deep_merge(base, {k: v for k, v in nd.items() if k != "inherit"})
        defaults = {k: v for k, v in t.defaults.items() if k in ("mode", "cli_style")}
        resolved[name] = deep_merge(defaults, nd)
        return resolved[name]

    for name in raw_nodes:
        nd = resolve(str(name))
        where = f"nodes.{name}"
        for k in (raw_nodes.get(name) or {}):
            if k not in NODE_KEYS:
                err.append(f"{where}.{k}: unknown key")
        kind = nd.get("kind")
        if kind not in KINDS:
            err.append(f"{where}.kind: '{kind}' is not one of {sorted(KINDS)}")
            kind = "router"
        site = nd.get("site")
        if site and site not in t.sites:
            err.append(f"{where}.site: unknown site '{site}'")
        node = Node(
            name=str(name), kind=kind, site=site, role=str(nd.get("role", "")),
            mode=str(nd.get("mode", "sim")), cli_style=str(nd.get("cli_style", "ios")),
            model=str(nd.get("model", "")), image=str(nd.get("image", "")),
            stp=nd.get("stp") or {}, routing=nd.get("routing") or {},
            services=nd.get("services") or {}, policies=nd.get("policies") or {}, raw=nd,
        )
        if node.mode not in ("sim", "emu", "hybrid"):
            err.append(f"{where}.mode: must be sim, emu or hybrid")
        for vid, vname in (nd.get("vlans") or {}).items():
            try:
                v = int(vid)
                if not 1 <= v <= 4094:
                    raise ValueError
                node.vlans[v] = str(vname)
            except (TypeError, ValueError):
                err.append(f"{where}.vlans: invalid VLAN id '{vid}' (1-4094)")
        if node.bridges and 1 not in node.vlans:
            node.vlans[1] = "default"
        for ifname, idata in (nd.get("interfaces") or {}).items():
            node.ifaces[str(ifname)] = _make_iface(node, str(ifname), idata or {}, err)
        t.nodes[node.name] = node

    # ------------------------------------------------------------ links
    raw_links = raw.get("links") or []
    used_ports: Dict[str, int] = {}
    for idx, ld in enumerate(raw_links):
        where = f"links[{idx}]"
        if not isinstance(ld, dict):
            err.append(f"{where}: must be a mapping with a and b")
            continue
        ends = []
        ok = True
        for side in ("a", "b"):
            ep = ld.get(side)
            if not ep or ":" not in str(ep):
                err.append(f"{where}.{side}: endpoint must be 'host:interface' (got '{ep}')")
                ok = False
                continue
            n, i = str(ep).split(":", 1)
            if n not in t.nodes:
                err.append(f"{where}.{side}: unknown node '{n}'")
                ok = False
                continue
            ends.append((n, i))
        if not ok:
            continue
        for n, i in ends:
            key = f"{n}:{i}"
            if key in used_ports:
                err.append(f"{where}: interface {key} already used by links[{used_ports[key]}]")
            used_ports[key] = idx
            node = t.nodes[n]
            if i not in node.ifaces:
                node.ifaces[i] = _make_iface(node, i, {}, err, declared=False)
        pname = ld.get("profile")
        if pname and pname not in t.profiles:
            err.append(f"{where}.profile: unknown link profile '{pname}'")
            pname = None
        if pname:
            prof = t.profiles[pname]
        else:
            prof = LinkProfile(name="default", bandwidth=parse_bandwidth(DEFAULT_PROFILE["bandwidth"]),
                               delay_ms=None)
        try:
            length = float(ld["length_km"]) if ld.get("length_km") is not None else None
        except (TypeError, ValueError):
            err.append(f"{where}.length_km: must be a number")
            length = None
        t.links.append(Link(idx=idx, a_node=ends[0][0], a_if=ends[0][1], b_node=ends[1][0], b_if=ends[1][1],
                            profile=prof, type=str(ld.get("type", prof.media)), length_km=length,
                            label=str(ld.get("label", "") or ""), raw=ld))
    labels = [l.label for l in t.links if l.label]
    for lb in set(labels):
        if labels.count(lb) > 1:
            err.append(f"links: label '{lb}' used more than once")

    # LAG membership
    for node in t.nodes.values():
        for ifc in list(node.ifaces.values()):
            for m in ifc.lag_members:
                node.member_of[m] = ifc.name
                if m not in node.ifaces:
                    node.ifaces[m] = _make_iface(node, m, {}, err, declared=False)
                mem = node.ifaces[m]
                mem.mode, mem.vlan, mem.allowed, mem.native = ifc.mode, ifc.vlan, ifc.allowed, ifc.native

    # ------------------------------------------------------------ IPAM
    for node in t.nodes.values():
        for ifc in node.ifaces.values():
            if ifc.ip is not None:
                ipam.mark_used(ifc.ip.network)
    for link in t.links:
        a = t.iface(link.a_node, link.a_if)
        b = t.iface(link.b_node, link.b_if)
        an, bn = t.nodes[link.a_node], t.nodes[link.b_node]
        a_auto = a.raw.get("ipv4") == "auto"
        b_auto = b.raw.get("ipv4") == "auto"
        if a_auto or b_auto:
            if a_auto and b.ip is not None:
                a.ip = _neighbor_addr(b.ip)
            elif b_auto and a.ip is not None:
                b.ip = _neighbor_addr(a.ip)
            else:
                sub = ipam.take("p2p", 31)
                if sub is None:
                    err.append(f"{link.name}: ipv4 auto needs an addressing pool with purpose p2p")
                    continue
                hosts = list(sub)
                if a_auto:
                    a.ip = ipaddress.ip_interface(f"{hosts[0]}/31")
                if b_auto:
                    b.ip = ipaddress.ip_interface(f"{hosts[1]}/31")
        # cloud ports adopt a neighbour address automatically
        for me, peer, mn in ((a, b, an), (b, a, bn)):
            if mn.kind == "cloud" and me.ip is None and peer.ip is not None:
                me.ip = _neighbor_addr(peer.ip)
    for node in t.nodes.values():
        for ifc in node.ifaces.values():
            if ifc.raw.get("ipv4") == "auto" and ifc.ip is None and ifc.loopback:
                sub = ipam.take("loopback", 32)
                if sub is not None:
                    ifc.ip = ipaddress.ip_interface(f"{sub.network_address}/32")
            if ifc.raw.get("ipv4") == "auto" and ifc.ip is None:
                link = next((l for l in t.links if (l.a_node, l.a_if) == (node.name, ifc.name)
                             or (l.b_node, l.b_if) == (node.name, ifc.name)), None)
                if link is None and not ifc.loopback:
                    err.append(f"nodes.{node.name}.interfaces.{ifc.name}: ipv4 auto but interface has no link")

    _validate(t)
    return t


def _neighbor_addr(peer: ipaddress.IPv4Interface) -> ipaddress.IPv4Interface:
    net = peer.network
    if net.prefixlen >= 31:
        hosts = list(net)
    else:
        hosts = list(net.hosts())
    for h in hosts:
        if h != peer.ip:
            return ipaddress.ip_interface(f"{h}/{net.prefixlen}")
    return peer


def _make_iface(node: Node, name: str, d: dict, err: List[str], declared: bool = True) -> Iface:
    where = f"nodes.{node.name}.interfaces.{name}"
    if not isinstance(d, dict):
        err.append(f"{where}: must be a mapping")
        d = {}
    for k in d:
        if k not in IFACE_KEYS:
            err.append(f"{where}.{k}: unknown key")
    lname = name.lower()
    is_svi = re.fullmatch(r"vlan(\d+)", lname) is not None
    is_lo = lname.startswith("lo")
    if node.kind in ENDPOINT_KINDS:
        default_mode = "host"
    elif node.bridges and not is_svi and not is_lo and not d.get("tunnel"):
        default_mode = "access"
    else:
        default_mode = "routed"
    mode = str(d.get("mode", default_mode))
    if mode not in ("access", "trunk", "routed", "host"):
        err.append(f"{where}.mode: must be access, trunk or routed")
        mode = default_mode
    if mode in ("access", "trunk") and not node.bridges:
        err.append(f"{where}.mode: {mode} ports exist only on switches (node kind {node.kind})")
        mode = "routed"
    ifc = Iface(node=node.name, name=name, mode=mode, raw=d, declared=declared,
                description=str(d.get("description", "")), shutdown=bool(d.get("shutdown", False)),
                portfast=bool(d.get("portfast", False)), loopback=is_lo)
    try:
        ifc.vlan = int(d.get("vlan", 1))
        ifc.native = int(d.get("native_vlan", 1))
        ifc.allowed = parse_vlan_list(d.get("allowed_vlans"))
        if d.get("voice_vlan") is not None:
            ifc.voice_vlan = int(d["voice_vlan"])
    except (TypeError, ValueError):
        err.append(f"{where}: VLAN values must be numbers")
    if is_svi:
        ifc.svi_vlan = int(re.fullmatch(r"vlan(\d+)", lname).group(1))
        ifc.mode = "routed"
    v4 = d.get("ipv4")
    if v4 is not None and v4 != "auto":
        if str(v4).lower() == "dhcp":
            ifc.dhcp = True
        else:
            ifc.ip = _ip_iface(v4, where + ".ipv4", err)
    if d.get("ipv6") is not None:
        ifc.ipv6 = str(d["ipv6"])
    for key in ("gateway", "dns"):
        if d.get(key) is not None:
            try:
                setattr(ifc, key, ipaddress.ip_address(str(d[key])))
            except ValueError:
                err.append(f"{where}.{key}: invalid IPv4 address '{d[key]}'")
    helpers = d.get("helper")
    if helpers is not None:
        for h in (helpers if isinstance(helpers, list) else [helpers]):
            try:
                ifc.helper.append(ipaddress.ip_address(str(h)))
            except ValueError:
                err.append(f"{where}.helper: invalid IPv4 address '{h}'")
    lag = d.get("lag")
    if lag:
        if not isinstance(lag, dict) or not isinstance(lag.get("members"), list):
            err.append(f"{where}.lag: needs members: [..]")
        else:
            ifc.lag_members = [str(m) for m in lag["members"]]
    if d.get("tunnel"):
        if not isinstance(d["tunnel"], dict) or not d["tunnel"].get("peer"):
            err.append(f"{where}.tunnel: needs peer: <node>")
        else:
            ifc.tunnel = dict(d["tunnel"])
            ifc.mode = "routed"
    ifc.acl_in = d.get("acl_in")
    ifc.acl_out = d.get("acl_out")
    if d.get("ospf_cost") is not None:
        ifc.ospf_cost = int(d["ospf_cost"])
    return ifc


def _validate(t: Topology) -> None:
    err = t.errors
    # duplicate addresses
    seen: Dict[str, str] = {}
    for n in t.nodes.values():
        for i in n.ifaces.values():
            if i.ip is None:
                continue
            k = str(i.ip.ip)
            if k in seen:
                err.append(f"duplicate IPv4 {k} on {seen[k]} and {i.key}")
            else:
                seen[k] = i.key
    # access VLANs exist on switch
    for n in t.nodes.values():
        if not n.bridges:
            continue
        for i in n.ifaces.values():
            if i.mode == "access" and i.vlan not in n.vlans:
                err.append(f"nodes.{n.name}.interfaces.{i.name}: access VLAN {i.vlan} is not defined in vlans")
            if i.svi_vlan is not None and i.svi_vlan not in n.vlans:
                err.append(f"nodes.{n.name}.interfaces.{i.name}: VLAN {i.svi_vlan} is not defined in vlans")
    # tunnels
    for n in t.nodes.values():
        for i in n.ifaces.values():
            if i.tunnel and i.tunnel.get("peer") not in t.nodes:
                err.append(f"nodes.{n.name}.interfaces.{i.name}.tunnel.peer: unknown node '{i.tunnel.get('peer')}'")
    # services
    for sid, s in t.services.items():
        if not isinstance(s, dict) or "type" not in s:
            err.append(f"services.{sid}: needs a type")
    # scenarios
    for sid, s in t.scenarios.items():
        for k, st in enumerate((s or {}).get("steps", [])):
            if st.get("action") not in ("link_down", "link_up", "node_reboot", "node_down", "node_up",
                                        "set_profile", "traffic"):
                err.append(f"scenarios.{sid}.steps[{k}].action: unknown action '{st.get('action')}'")
            try:
                parse_time_ms(st.get("at", "0s"))
            except ValueError as e:
                err.append(f"scenarios.{sid}.steps[{k}].at: {e}")
    ids = [x.get("id") for x in t.tests if isinstance(x, dict)]
    for tid in set(ids):
        if ids.count(tid) > 1:
            err.append(f"tests: id '{tid}' used more than once")
