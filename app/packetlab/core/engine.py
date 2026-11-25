"""PacketLab simulation engine.

The engine computes the *converged* state of a topology (L2 broadcast domains,
spanning tree, routing tables, VRRP masters, DHCP leases, tunnels) and then walks
individual packets through it, producing hop-by-hop events for the PDU inspector.

Standards modelled (behaviourally): IEEE 802.1Q VLANs/trunks, IEEE 802.1w/802.1D
spanning tree (per VLAN instance), IEEE 802.1AX LAG, ITU-T G.8032 ring protection
(RPL blocking), RFC 826 ARP, RFC 791 IPv4 forwarding with longest-prefix match,
RFC 2328 OSPFv2 SPF (single LSDB), ISO 10589 IS-IS (as a second link-state IGP),
RFC 4271 BGP-4 path selection (local-pref, AS-path, eBGP>iBGP, next-hop-self),
RFC 5798 VRRPv3 master election, RFC 2131 DHCP with relay, RFC 3022 NAT overload,
stateful ACLs, IPsec tunnels as overlay links.
"""
from __future__ import annotations

import heapq
import ipaddress
import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .model import Iface, Link, Node, Topology, fmt_bw

AD = {"connected": 0, "static": 1, "ebgp": 20, "ospf": 110, "isis": 115, "ibgp": 200, "default-cloud": 250,
      "dhcp": 254}

DEFAULT_SETTINGS = {
    "ospf_ref_bw": 100e9,       # 100 Gbps reference bandwidth (modern default)
    "dhcp_offset": 100,         # first address handed out by DHCP pools
    "proc_delay_ms": 0.02,      # per L3 hop forwarding delay
    "switch_delay_ms": 0.005,   # per L2 hop forwarding delay
    "max_hops": 64,
}

ANY = ipaddress.ip_network("0.0.0.0/0")


@dataclass
class L3If:
    node: str
    name: str
    ip: ipaddress.IPv4Interface
    vertex: tuple
    domain: Optional[int]
    up: bool
    kind: str            # phys | svi | lo | tun | host
    bw: float = 1e9

    @property
    def net(self):
        return self.ip.network


@dataclass
class Route:
    prefix: ipaddress.IPv4Network
    proto: str
    ad: int
    metric: float
    nexthop: Optional[ipaddress.IPv4Address]
    out_if: Optional[str]
    detail: str = ""

    def code(self) -> str:
        return {"connected": "C", "static": "S", "ospf": "O", "isis": "i", "ebgp": "B", "ibgp": "B",
                "default-cloud": "S*", "dhcp": "S*", "local": "L"}.get(self.proto, "?")


@dataclass
class Hop:
    node: str
    in_if: Optional[str]
    out_if: Optional[str]
    note: str = ""


@dataclass
class Event:
    t_ms: float
    node: str
    layer: str
    kind: str
    summary: str
    pdu: dict
    link: Optional[int] = None
    from_node: Optional[str] = None
    to_node: Optional[str] = None


@dataclass
class WalkResult:
    ok: bool
    reason: str
    hops: List[Hop] = field(default_factory=list)
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    loss: float = 0.0
    min_bw: float = float("inf")
    events: List[Event] = field(default_factory=list)
    src_ip: Optional[ipaddress.IPv4Address] = None
    dst_ip: Optional[ipaddress.IPv4Address] = None
    final_node: Optional[str] = None
    nat: Dict[str, str] = field(default_factory=dict)
    links: List[int] = field(default_factory=list)
    l3_path: List[str] = field(default_factory=list)


def _mac(node: str, ifname: str) -> str:
    h = abs(hash((node, ifname))) & 0xFFFFFFFFFF
    # deterministic across runs: use a stable hash instead of Python's randomised hash
    s = f"{node}:{ifname}".encode()
    v = 0
    for b in s:
        v = (v * 131 + b) & 0xFFFFFFFFFF
    del h
    octs = [0x02] + [(v >> (8 * i)) & 0xFF for i in range(4, -1, -1)]
    return ":".join(f"{o:02x}" for o in octs)


class Engine:
    """Converged-state network model for one topology + its current failure state."""

    def __init__(self, topo: Topology, settings: Optional[dict] = None,
                 down_links: Optional[Set[int]] = None, down_nodes: Optional[Set[str]] = None,
                 profile_overrides: Optional[Dict[int, object]] = None,
                 sticky_leases: Optional[Dict[Tuple[str, str], dict]] = None):
        self.t = topo
        self.sticky_leases = sticky_leases or {}
        self.s = dict(DEFAULT_SETTINGS)
        if settings:
            self.s.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
        self.down_links: Set[int] = set(down_links or ())
        self.down_nodes: Set[str] = set(down_nodes or ())
        self.profile_overrides = dict(profile_overrides or {})
        self.log: List[str] = []
        self.converge()

    # ================================================================== state
    def node_up(self, n: str) -> bool:
        return n not in self.down_nodes

    def link_up(self, l: Link) -> bool:
        if l.idx in self.down_links or not self.node_up(l.a_node) or not self.node_up(l.b_node):
            return False
        for n, i in ((l.a_node, l.a_if), (l.b_node, l.b_if)):
            ifc = self.t.iface(n, i)
            if ifc is None or ifc.shutdown:
                return False
            node = self.t.nodes[n]
            lag = node.member_of.get(i)
            if lag and node.ifaces[lag].shutdown:
                return False
        return True

    def link_profile(self, l: Link):
        return self.profile_overrides.get(l.idx, l.profile)

    def link_delay(self, l: Link) -> float:
        p = self.link_profile(l)
        if p is not l.profile and p.delay_ms is not None:
            return p.delay_ms
        return l.delay_ms

    # ================================================================== converge
    def converge(self) -> None:
        self._build_l2()
        self._stp()
        self._domains()
        self._l3_interfaces()
        self._vrrp()
        self.tunnels_up: Dict[Tuple[str, str], dict] = {}
        self.dhcp_leases: Dict[Tuple[str, str], dict] = {}
        for _ in range(4):
            self._routing()
            changed = self._tunnels()
            if not changed:
                break
            self._domains_add_tunnels()
        self._dhcp()
        self._routing(hosts_only=True)

    # ------------------------------------------------------------------ L2
    def _frames(self, node: Node, ifc: Iface):
        """Frames an interface puts on the wire: list of (tagged_vlan or None, local_vertex)."""
        if node.bridges and ifc.mode in ("access", "trunk"):
            out = []
            if ifc.mode == "access":
                out.append((None, ("br", node.name, ifc.vlan)))
                if ifc.voice_vlan:
                    out.append((ifc.voice_vlan, ("br", node.name, ifc.voice_vlan)))
            else:
                vl = sorted(node.vlans) if ifc.allowed is None else sorted(ifc.allowed)
                for v in vl:
                    if v == ifc.native:
                        out.append((None, ("br", node.name, v)))
                    else:
                        out.append((v, ("br", node.name, v)))
                if ifc.native not in vl and ifc.allowed is None:
                    out.append((None, ("br", node.name, ifc.native)))
            return [(tag, vx) for tag, vx in out if vx[2] in node.vlans]
        return [(None, ("ep", node.name, ifc.name))]

    def _accepts(self, node: Node, ifc: Iface, tag):
        """Vertex a frame (tag) is delivered to on the receiving interface, or None."""
        if node.bridges and ifc.mode in ("access", "trunk"):
            if ifc.mode == "access":
                if tag is None:
                    v = ifc.vlan
                elif tag == ifc.voice_vlan:
                    v = tag
                else:
                    return None
            else:
                if tag is None:
                    v = ifc.native
                else:
                    if ifc.allowed is not None and tag not in ifc.allowed:
                        return None
                    v = tag
            return ("br", node.name, v) if v in node.vlans else None
        if tag is None:
            return ("ep", node.name, ifc.name)
        return None

    def _build_l2(self) -> None:
        self.edges: List[dict] = []
        self.adj: Dict[tuple, List[dict]] = {}
        self.vertex_ports: Dict[tuple, Set[str]] = {}
        for l in self.t.links:
            if not self.link_up(l):
                continue
            an, bn = self.t.nodes[l.a_node], self.t.nodes[l.b_node]
            ai, bi = an.ifaces[l.a_if], bn.ifaces[l.b_if]
            pairs = set()
            for tag, va in self._frames(an, ai):
                vb = self._accepts(bn, bi, tag)
                if vb is not None:
                    pairs.add((va, vb, tag))
            for tag, vb in self._frames(bn, bi):
                va = self._accepts(an, ai, tag)
                if va is not None:
                    pairs.add((va, vb, tag))
            prof = self.link_profile(l)
            for va, vb, tag in pairs:
                e = {"u": va, "v": vb, "link": l.idx, "u_port": an.port_of(l.a_if), "v_port": bn.port_of(l.b_if),
                     "u_if": l.a_if, "v_if": l.b_if, "tag": tag, "bw": prof.bandwidth,
                     "delay": self.link_delay(l), "jitter": prof.jitter_ms, "loss": prof.loss}
                self.edges.append(e)
        # SVIs attach to their bridge vertex
        for n in self.t.nodes.values():
            if not self.node_up(n.name):
                continue
            for i in n.ifaces.values():
                if i.svi_vlan is not None and n.bridges and i.svi_vlan in n.vlans and not i.shutdown:
                    self.edges.append({"u": ("svi", n.name, i.name), "v": ("br", n.name, i.svi_vlan), "link": None,
                                       "u_port": i.name, "v_port": i.name, "u_if": i.name, "v_if": i.name,
                                       "tag": None, "bw": 1e10, "delay": 0.0, "jitter": 0.0, "loss": 0.0})
        for e in self.edges:
            self.adj.setdefault(e["u"], []).append(e)
            self.adj.setdefault(e["v"], []).append(e)

    def _stp(self) -> None:
        """Per-instance spanning tree over bridge vertices (PVST/RSTP style)."""
        self.blocked: Set[int] = set()          # indexes into self.edges
        self.stp_info: Dict[tuple, dict] = {}   # bridge vertex -> {root, cost, root_port}
        self.port_roles: Dict[Tuple[str, str, int], str] = {}
        rpl_ports = set()
        for s in self.t.services.values():
            prot = (s or {}).get("protection") or {}
            if prot.get("type") in ("g8032", "erps") and prot.get("rpl_owner"):
                n, p = str(prot["rpl_owner"]).split(":", 1)
                rpl_ports.add((n, p))
        # group bridge-to-bridge edges by logical port pair
        br_edges: Dict[tuple, List[int]] = {}
        for k, e in enumerate(self.edges):
            if e["u"][0] == "br" and e["v"][0] == "br" and e["u"][1] != e["v"][1]:
                key = (e["u"], e["u_port"], e["v"], e["v_port"])
                br_edges.setdefault(key, []).append(k)
        port_graph: Dict[tuple, List[tuple]] = {}
        for key, idxs in br_edges.items():
            u, up, v, vp = key
            bw = sum(self.edges[k]["bw"] for k in idxs)
            cost = max(1, int(20e9 / bw)) if bw else 200000000
            if (u[1], up) in rpl_ports or (v[1], vp) in rpl_ports:
                cost = 10 ** 12
            port_graph.setdefault(u, []).append((v, cost, up, vp, key))
            port_graph.setdefault(v, []).append((u, cost, vp, up, key))
        seen: Set[tuple] = set()
        bridges = [v for v in self.adj if v[0] == "br"]
        for start in sorted(bridges):
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                x = stack.pop()
                comp.append(x)
                for y, *_ in port_graph.get(x, []):
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            root = min(comp, key=self._bridge_id)
            dist = {root: 0}
            parent: Dict[tuple, tuple] = {}
            pq = [(0, self._bridge_id(root), root)]
            done = set()
            while pq:
                d, _, x = heapq.heappop(pq)
                if x in done:
                    continue
                done.add(x)
                for y, cost, xp, yp, key in sorted(port_graph.get(x, []), key=lambda z: (z[2], z[3])):
                    nd = d + cost
                    better = nd < dist.get(y, math.inf) or (
                        nd == dist.get(y) and y in parent and self._bridge_id(x) < self._bridge_id(parent[y][0]))
                    if y not in done and better:
                        dist[y] = nd
                        parent[y] = (x, key, yp)
                        heapq.heappush(pq, (nd, self._bridge_id(y), y))
            tree_keys = {parent[y][1] for y in parent}
            for x in comp:
                self.stp_info[x] = {"root": root[1], "root_vertex": root, "cost": dist.get(x, 0),
                                    "root_port": parent[x][2] if x in parent else None,
                                    "mode": (self.t.nodes[x[1]].stp or {}).get("mode", "rstp")}
            for key, idxs in br_edges.items():
                u, up, v, vp = key
                if u not in self.stp_info or self.stp_info[u]["root_vertex"] != root:
                    continue
                if key in tree_keys:
                    for side, port in ((u, up), (v, vp)):
                        role = "root" if self.stp_info[side]["root_port"] == port and side != root else "designated"
                        self.port_roles[(side[1], port, side[2])] = role
                else:
                    for k in idxs:
                        self.blocked.add(k)
                    # side farther from root (or higher bridge id) is the alternate/blocked port
                    cu, cv = dist.get(u, 0), dist.get(v, 0)
                    blk = v if (cv, self._bridge_id(v)) > (cu, self._bridge_id(u)) else u
                    for side, port in ((u, up), (v, vp)):
                        self.port_roles[(side[1], port, side[2])] = "alternate" if side == blk else "designated"
        # edge ports towards end devices are designated
        for e in self.edges:
            for side, port in ((e["u"], e["u_port"]), (e["v"], e["v_port"])):
                if side[0] == "br" and (side[1], port, side[2]) not in self.port_roles and e["link"] is not None:
                    self.port_roles[(side[1], port, side[2])] = "designated"

    def _bridge_id(self, vertex: tuple):
        node = self.t.nodes[vertex[1]]
        stp = node.stp or {}
        prio = stp.get("priority", 32768)
        vp = stp.get("vlan_priority") or {}
        if isinstance(vp, dict) and len(vertex) > 2:
            prio = vp.get(vertex[2], vp.get(str(vertex[2]), prio))
        return (int(prio), _mac(node.name, "bridge"))

    def _domains(self) -> None:
        self.fwd_adj: Dict[tuple, List[Tuple[tuple, dict]]] = {}
        for k, e in enumerate(self.edges):
            if k in self.blocked:
                continue
            self.fwd_adj.setdefault(e["u"], []).append((e["v"], e))
            self.fwd_adj.setdefault(e["v"], []).append((e["u"], e))
        self.domain_of: Dict[tuple, int] = {}
        did = 0
        for v in sorted(self.fwd_adj, key=str):
            if v in self.domain_of:
                continue
            stack = [v]
            self.domain_of[v] = did
            while stack:
                x = stack.pop()
                for y, _ in self.fwd_adj.get(x, []):
                    if y not in self.domain_of:
                        self.domain_of[y] = did
                        stack.append(y)
            did += 1
        self._next_domain = did

    def _domains_add_tunnels(self) -> None:
        for (a, b), info in self.tunnels_up.items():
            did = info.setdefault("domain", self._next_domain)
            if did == self._next_domain:
                self._next_domain += 1
            for key in (info["a"], info["b"]):
                l3 = self.l3.get(key)
                if l3:
                    l3.domain, l3.up = did, True

    def _l3_interfaces(self) -> None:
        self.l3: Dict[Tuple[str, str], L3If] = {}
        for n in self.t.nodes.values():
            for i in n.ifaces.values():
                if i.ip is None or n.member_of.get(i.name):
                    continue
                up = self.node_up(n.name) and not i.shutdown
                if i.svi_vlan is not None:
                    vx, kind = ("svi", n.name, i.name), "svi"
                    bw = 1e9
                elif i.loopback:
                    vx, kind, bw = ("lo", n.name, i.name), "lo", 1e10
                elif i.tunnel is not None:
                    vx, kind, bw = ("tun", n.name, i.name), "tun", 1e8
                    up = False   # decided by _tunnels()
                else:
                    vx = ("ep", n.name, i.name)
                    kind = "host" if n.kind in ("host", "server", "external") else "phys"
                    bw = self._port_bw(n, i.name)
                    up = up and vx in self.domain_of
                dom = self.domain_of.get(vx)
                if kind == "lo":
                    dom = ("lo", n.name, i.name)
                if kind == "svi":
                    up = up and vx in self.domain_of
                self.l3[(n.name, i.name)] = L3If(n.name, i.name, i.ip, vx, dom, up, kind, bw)

    def _port_bw(self, n: Node, ifname: str) -> float:
        members = [m for m, lag in n.member_of.items() if lag == ifname] or [ifname]
        bw = 0.0
        for l in self.t.links:
            if (l.a_node == n.name and l.a_if in members) or (l.b_node == n.name and l.b_if in members):
                if self.link_up(l):
                    bw += self.link_profile(l).bandwidth
        return bw or 1e9

    # ------------------------------------------------------------------ VRRP
    def _vrrp(self) -> None:
        self.vips: Dict[ipaddress.IPv4Address, Tuple[str, str]] = {}
        self.vrrp_state: Dict[Tuple[str, str, int], dict] = {}
        groups: Dict[tuple, list] = {}
        for n in self.t.nodes.values():
            for g in (n.routing or {}).get("vrrp", []) or []:
                try:
                    vip = ipaddress.ip_address(str(g["vip"]))
                    gid = int(g["group"])
                except (KeyError, ValueError, TypeError):
                    self.log.append(f"{n.name}: invalid vrrp entry {g}")
                    continue
                l3 = self.l3.get((n.name, g.get("interface")))
                dom = l3.domain if (l3 and l3.up) else None
                prio = int(g.get("priority", 100))
                groups.setdefault((gid, vip), []).append((n.name, g.get("interface"), prio, l3, dom,
                                                          float(g.get("advert", g.get("advertise", 1.0)))))
        for (gid, vip), members in groups.items():
            for name, ifn, prio, l3, dom, adv in members:
                self.vrrp_state[(name, ifn, gid)] = {"state": "INIT" if dom is None else "BACKUP",
                                                     "vip": vip, "priority": prio, "advert": adv}
            live = [m for m in members if m[4] is not None]
            by_dom: Dict[int, list] = {}
            for m in live:
                by_dom.setdefault(m[4], []).append(m)
            for dom, ms in by_dom.items():
                master = max(ms, key=lambda m: (m[2], int(m[3].ip.ip)))
                self.vrrp_state[(master[0], master[1], gid)]["state"] = "MASTER"
                self.vips[vip] = (master[0], master[1])

    # ------------------------------------------------------------------ routing
    def my_ips(self, node: str) -> Dict[ipaddress.IPv4Address, str]:
        out = {}
        for (n, ifn), l3 in self.l3.items():
            if n == node and l3.up:
                out[l3.ip.ip] = ifn
        for vip, (n, ifn) in self.vips.items():
            if n == node:
                out[vip] = ifn
        return out

    def _routing(self, hosts_only: bool = False) -> None:
        if not hosts_only:
            self.rib: Dict[str, List[Route]] = {}
            self.rib_all: Dict[str, List[Route]] = {}
            self.ospf_nbrs: Dict[str, List[dict]] = {}
            self.isis_nbrs: Dict[str, List[dict]] = {}
            self.bgp_sessions: Dict[str, List[dict]] = {}
            self.bgp_best: Dict[str, Dict[ipaddress.IPv4Network, dict]] = {}
            redistributed_bgp: Dict[str, List[Route]] = {}
            for it in range(6):
                base = {n: self._connected_static(n) for n in self.t.nodes}
                igp = {}
                for proto in ("ospf", "isis"):
                    res = self._link_state(proto, base, redistributed_bgp)
                    for n, routes in res.items():
                        igp.setdefault(n, []).extend(routes)
                pre = {n: self._select(base[n] + igp.get(n, [])) for n in self.t.nodes}
                bgp_routes = self._bgp(pre)
                new_redis = {n: [r for r in rs] for n, rs in bgp_routes.items()}
                for n in self.t.nodes:
                    allr = base[n] + igp.get(n, []) + bgp_routes.get(n, [])
                    self.rib_all[n] = allr
                    self.rib[n] = self._select(allr)
                key_old = {n: sorted(str(r.prefix) for r in v) for n, v in redistributed_bgp.items()}
                key_new = {n: sorted(str(r.prefix) for r in v) for n, v in new_redis.items()}
                redistributed_bgp = new_redis
                if key_old == key_new and it > 0:
                    break
        else:
            for n in self.t.nodes.values():
                if n.kind in ("host", "server", "external"):
                    allr = self._connected_static(n.name)
                    self.rib_all[n.name] = allr
                    self.rib[n.name] = self._select(allr)

    def _connected_static(self, node: str) -> List[Route]:
        n = self.t.nodes[node]
        routes: List[Route] = []
        if not self.node_up(node):
            return routes
        for (nn, ifn), l3 in self.l3.items():
            if nn != node or not l3.up:
                continue
            routes.append(Route(l3.net, "connected", 0, 0, None, ifn, "directly connected"))
        # DHCP-learned addresses on hosts
        for (nn, ifn), lease in getattr(self, "dhcp_leases", {}).items():
            if nn == node and lease.get("ip") is not None:
                routes.append(Route(lease["ip"].network, "connected", 0, 0, None, ifn, "dhcp"))
                if lease.get("gateway"):
                    routes.append(Route(ANY, "dhcp", AD["dhcp"], 0, lease["gateway"], None, "dhcp default"))
        for s in (n.routing or {}).get("static", []) or []:
            try:
                pfx = ipaddress.ip_network(str(s.get("prefix", "0.0.0.0/0")), strict=False)
                nh = ipaddress.ip_address(str(s["next_hop"])) if s.get("next_hop") else None
            except (ValueError, KeyError):
                self.log.append(f"{node}: invalid static route {s}")
                continue
            routes.append(Route(pfx, "static", int(s.get("ad", AD["static"])), 0, nh, s.get("interface"), "static"))
        if n.kind in ("host", "server", "external"):
            for i in n.ifaces.values():
                if i.gateway is not None and not i.shutdown:
                    routes.append(Route(ANY, "static", 1, 0, i.gateway, None, "default gateway"))
        # floating default towards an attached Internet cloud
        if n.routes and n.kind != "cloud" and not any(r.prefix == ANY for r in routes):
            for l in self.t.links_of(node):
                on, oi = l.other(node, l.a_if if l.a_node == node else l.b_if)
                if self.t.nodes[on].kind == "cloud" and self.link_up(l):
                    oip = self.t.iface(on, oi)
                    if oip and oip.ip:
                        routes.append(Route(ANY, "default-cloud", AD["default-cloud"], 0, oip.ip.ip, None,
                                            "default via Internet cloud"))
                        break
        return routes

    @staticmethod
    def _select(routes: List[Route]) -> List[Route]:
        best: Dict[ipaddress.IPv4Network, Route] = {}
        for r in routes:
            cur = best.get(r.prefix)
            if cur is None or (r.ad, r.metric, str(r.nexthop)) < (cur.ad, cur.metric, str(cur.nexthop)):
                best[r.prefix] = r
        return sorted(best.values(), key=lambda r: (-r.prefix.prefixlen, int(r.prefix.network_address)))

    def _proto_ifaces(self, n: Node, proto: str) -> Dict[str, str]:
        """Interfaces enabled for a link-state protocol -> area/level."""
        cfg = (n.routing or {}).get(proto)
        if not cfg or not self.node_up(n.name):
            return {}
        if cfg is True:
            cfg = {}
        out: Dict[str, str] = {}
        names = [ifn for (nn, ifn), l3 in self.l3.items() if nn == n.name and l3.up]
        if proto == "ospf" and cfg.get("areas"):
            for area, lst in cfg["areas"].items():
                if lst in ("all", None):
                    lst = names
                for ifn in lst:
                    if ifn in names:
                        out[ifn] = str(area)
                    elif ifn in n.member_of.values() or ifn in n.ifaces:
                        pass
        else:
            lst = cfg.get("interfaces", "all")
            if lst in ("all", None):
                lst = names
            for ifn in lst:
                if ifn in names:
                    out[ifn] = str(cfg.get("area", cfg.get("level", "0")))
        return out

    def _if_cost(self, n: Node, ifn: str) -> int:
        ifc = n.ifaces.get(ifn)
        if ifc and ifc.ospf_cost:
            return int(ifc.ospf_cost)
        l3 = self.l3.get((n.name, ifn))
        if l3 and l3.kind == "lo":
            return 1
        bw = l3.bw if l3 else 1e9
        if l3 and l3.kind == "tun":
            bw = 1e8
        return max(1, int(self.s["ospf_ref_bw"] / max(bw, 1)))

    def _link_state(self, proto: str, base: Dict[str, List[Route]], redis_bgp: Dict[str, List[Route]]):
        ad = AD[proto]
        speakers = {n.name: self._proto_ifaces(n, proto) for n in self.t.nodes.values()}
        speakers = {k: v for k, v in speakers.items() if v}
        nbrs: Dict[str, List[dict]] = {k: [] for k in speakers}
        passive = {k: set(((self.t.nodes[k].routing or {}).get(proto) or {}).get("passive", []) or [])
                   if isinstance((self.t.nodes[k].routing or {}).get(proto), dict) else set() for k in speakers}
        # adjacencies on shared broadcast domains / tunnels
        by_dom: Dict[object, List[Tuple[str, str, str]]] = {}
        for n, ifs in speakers.items():
            for ifn, area in ifs.items():
                l3 = self.l3[(n, ifn)]
                if l3.kind == "lo" or ifn in passive[n]:
                    continue
                by_dom.setdefault((l3.domain, l3.net), []).append((n, ifn, area))
        graph: Dict[str, List[Tuple[str, int, ipaddress.IPv4Address, str]]] = {k: [] for k in speakers}
        for (dom, net), members in by_dom.items():
            for (a, ai, aa), (b, bi, ba) in itertools.permutations(members, 2):
                if a == b:
                    continue
                state = "FULL" if aa == ba else "DOWN (area mismatch)"
                nbrs[a].append({"neighbor": b, "address": self.l3[(b, bi)].ip.ip, "interface": ai,
                                "state": state, "area": aa})
                if state == "FULL":
                    graph[a].append((b, self._if_cost(self.t.nodes[a], ai), self.l3[(b, bi)].ip.ip, ai))
        if proto == "ospf":
            self.ospf_nbrs = nbrs
        else:
            self.isis_nbrs = nbrs
        # what each speaker advertises
        adverts: Dict[str, List[Tuple[ipaddress.IPv4Network, int, bool]]] = {}
        for n, ifs in speakers.items():
            node = self.t.nodes[n]
            lst = [(self.l3[(n, ifn)].net, self._if_cost(node, ifn), False) for ifn in ifs]
            cfg = (node.routing or {}).get(proto) or {}
            redis = cfg.get("redistribute", []) if isinstance(cfg, dict) else []
            for r in base.get(n, []):
                if r.proto == "connected" and "connected" in redis:
                    lst.append((r.prefix, 20, True))
                if r.proto in ("static", "default-cloud") and "static" in redis:
                    lst.append((r.prefix, 20, True))
            if "bgp" in redis:
                for r in redis_bgp.get(n, []):
                    lst.append((r.prefix, 20, True))
            if cfg.get("default_originate") if isinstance(cfg, dict) else False:
                lst.append((ANY, 1, True))
            adverts[n] = lst
        result: Dict[str, List[Route]] = {}
        for src in speakers:
            dist = {src: 0}
            first: Dict[str, Tuple[ipaddress.IPv4Address, str]] = {}
            pq = [(0, src)]
            done = set()
            while pq:
                d, x = heapq.heappop(pq)
                if x in done:
                    continue
                done.add(x)
                for y, c, yip, xif in sorted(graph.get(x, []), key=lambda z: (z[0], str(z[2]))):
                    nd = d + c
                    if nd < dist.get(y, math.inf):
                        dist[y] = nd
                        first[y] = (yip, xif) if x == src else first[x]
                        heapq.heappush(pq, (nd, y))
            local_nets = {r.prefix for r in base.get(src, []) if r.proto == "connected"}
            routes: Dict[ipaddress.IPv4Network, Route] = {}
            for r_node, d in dist.items():
                if r_node == src:
                    continue
                nh, oif = first[r_node]
                for pfx, cost, ext in adverts.get(r_node, []):
                    if pfx in local_nets:
                        continue
                    metric = cost if ext else d + cost
                    tie = d
                    cur = routes.get(pfx)
                    if cur is None or (metric, tie, str(nh)) < (cur.metric, cur._tie, str(cur.nexthop)):  # type: ignore
                        rt = Route(pfx, proto, ad, metric, nh, oif,
                                   f"{'E2 ' if ext else ''}via {r_node}")
                        rt._tie = tie  # type: ignore
                        routes[pfx] = rt
            result[src] = list(routes.values())
        return result

    def _bgp(self, pre: Dict[str, List[Route]]) -> Dict[str, List[Route]]:
        speakers = {n.name: n.routing["bgp"] for n in self.t.nodes.values()
                    if isinstance((n.routing or {}).get("bgp"), dict) and self.node_up(n.name)
                    and n.routing["bgp"].get("asn") is not None}
        self.bgp_sessions = {k: [] for k in speakers}
        sessions: Dict[str, List[dict]] = {k: [] for k in speakers}
        for n, cfg in speakers.items():
            asn = int(cfg["asn"])
            for nb in cfg.get("neighbors", []) or []:
                try:
                    peer_ip = ipaddress.ip_address(str(nb["peer"]))
                except (KeyError, ValueError):
                    continue
                st = {"peer": peer_ip, "remote_as": int(nb.get("remote_as", 0)), "state": "Idle",
                      "local_pref": int(nb.get("local_pref", 100)), "bfd": bool(nb.get("bfd", False)),
                      "peer_node": None, "rcvd": 0, "reason": ""}
                owner = self._ip_owner_up(peer_ip)
                if owner is None:
                    st["reason"] = "peer address not reachable/owned"
                elif owner[0] not in speakers:
                    st["reason"] = f"{owner[0]} does not run BGP"
                else:
                    pn = owner[0]
                    pcfg = speakers[pn]
                    if int(pcfg["asn"]) != st["remote_as"]:
                        st["reason"] = f"remote-as mismatch (peer is AS{pcfg['asn']})"
                    elif not self._reachable_rib(n, peer_ip, pre) or not self._reachable_rib(
                            pn, self._local_src(n, peer_ip, pre) or peer_ip, pre):
                        st["state"] = "Active"
                        st["reason"] = "no route to peer"
                    else:
                        mine = set(self.my_ips(n))
                        back = [x for x in pcfg.get("neighbors", []) or []
                                if str(x.get("peer")) in {str(i) for i in mine}]
                        if not back:
                            st["state"], st["reason"] = "Active", "peer has no neighbor statement for us"
                        elif int(back[0].get("remote_as", 0)) != asn:
                            st["state"], st["reason"] = "Idle", "peer expects a different AS"
                        else:
                            st["state"] = "Established"
                            st["peer_node"] = pn
                            st["peer_local_ip"] = ipaddress.ip_address(str(back[0]["peer"]))
                sessions[n].append(st)
        # origination
        rib: Dict[str, Dict[ipaddress.IPv4Network, dict]] = {}
        for n, cfg in speakers.items():
            own: Dict[ipaddress.IPv4Network, dict] = {}
            prefixes = {r.prefix for r in pre.get(n, [])}
            for p in cfg.get("networks", []) or []:
                try:
                    pfx = ipaddress.ip_network(str(p), strict=False)
                except ValueError:
                    continue
                if pfx in prefixes:
                    own[pfx] = {"as_path": [], "local_pref": 100, "nh": None, "from": None, "ebgp": True,
                                "origin": n}
            for kind in cfg.get("redistribute", []) or []:
                for r in pre.get(n, []):
                    if (kind == "connected" and r.proto == "connected" and self.l3.get((n, r.out_if)) and
                            self.l3[(n, r.out_if)].kind != "tun") or (kind == r.proto) or (
                            kind == "static" and r.proto == "static"):
                        if r.prefix != ANY:
                            own.setdefault(r.prefix, {"as_path": [], "local_pref": 100, "nh": None, "from": None,
                                                      "ebgp": True, "origin": n})
            rib[n] = dict(own)
        # path-vector propagation to a fixed point
        best = {n: dict(v) for n, v in rib.items()}
        for _ in range(32):
            changed = False
            new_best = {n: dict(rib[n]) for n in speakers}
            for n in speakers:
                asn = int(speakers[n]["asn"])
                cands: Dict[ipaddress.IPv4Network, List[dict]] = {}
                for st in sessions[n]:
                    if st["state"] != "Established":
                        continue
                    pn = st["peer_node"]
                    p_asn = int(speakers[pn]["asn"])
                    ebgp = p_asn != asn
                    for pfx, path in best.get(pn, {}).items():
                        if path["from"] is not None and not path["ebgp"] and not ebgp:
                            continue  # iBGP split horizon
                        if path["from"] == n:
                            continue
                        aspath = ([p_asn] + path["as_path"]) if ebgp else list(path["as_path"])
                        if asn in aspath:
                            continue
                        lp = st["local_pref"] if ebgp or st["local_pref"] != 100 else path["local_pref"]
                        cands.setdefault(pfx, []).append({"as_path": aspath, "local_pref": lp, "nh": st["peer"],
                                                          "from": pn, "ebgp": ebgp, "origin": path["origin"]})
                for pfx, lst in cands.items():
                    if pfx in rib[n]:
                        continue
                    lst.sort(key=lambda p: (-p["local_pref"], len(p["as_path"]), not p["ebgp"], int(p["nh"])))
                    new_best[n][pfx] = lst[0]
                    new_best[n][pfx]["alternatives"] = len(lst)
            for n in speakers:
                if {k: (v["nh"], tuple(v["as_path"]), v["local_pref"]) for k, v in new_best[n].items()} != \
                        {k: (v["nh"], tuple(v["as_path"]), v["local_pref"]) for k, v in best.get(n, {}).items()}:
                    changed = True
            best = new_best
            if not changed:
                break
        out: Dict[str, List[Route]] = {}
        for n in speakers:
            for st in sessions[n]:
                if st["state"] == "Established":
                    st["rcvd"] = sum(1 for p in best[n].values() if p["from"] == st["peer_node"])
            self.bgp_sessions[n] = sessions[n]
            self.bgp_best[n] = best[n]
            routes = []
            local = {r.prefix for r in pre.get(n, []) if r.proto == "connected"}
            for pfx, p in best[n].items():
                if p["from"] is None or pfx in local:
                    continue
                proto = "ebgp" if p["ebgp"] else "ibgp"
                routes.append(Route(pfx, proto, AD[proto], 0, p["nh"], None,
                                    f"AS-path {' '.join(map(str, p['as_path'])) or 'i'} lp {p['local_pref']}"))
            out[n] = routes
        return out

    def _ip_owner_up(self, ip) -> Optional[Tuple[str, str]]:
        for (n, ifn), l3 in self.l3.items():
            if l3.up and l3.ip.ip == ip:
                return n, ifn
        return None

    def _reachable_rib(self, node: str, ip, pre: Dict[str, List[Route]]) -> bool:
        """Is ip reachable from node using RIB `pre` (recursive, follow next hops)?"""
        cur, seen = node, set()
        for _ in range(32):
            if cur in seen:
                return False
            seen.add(cur)
            if ip in self.my_ips(cur):
                return True
            r = self._lookup_in(pre.get(cur, []), ip)
            if r is None:
                return False
            out_if, nh = self._resolve(cur, r, pre)
            if out_if is None:
                return False
            target = nh or ip
            l3 = self.l3.get((cur, out_if))
            if l3 is None:
                return False
            owner = None
            for (n2, i2), x in self.l3.items():
                if x.up and x.ip.ip == target and x.domain == l3.domain and n2 != cur:
                    owner = n2
            if owner is None and target in self.vips and self.vips[target][0] != cur:
                owner = self.vips[target][0]
            if owner is None:
                return False
            cur = owner
        return False

    def _local_src(self, node: str, dst, pre) -> Optional[ipaddress.IPv4Address]:
        r = self._lookup_in(pre.get(node, []), dst)
        if r is None:
            return None
        out_if, _ = self._resolve(node, r, pre)
        l3 = self.l3.get((node, out_if)) if out_if else None
        return l3.ip.ip if l3 else None

    @staticmethod
    def _lookup_in(routes: List[Route], ip) -> Optional[Route]:
        best = None
        for r in routes:
            if ip in r.prefix and (best is None or r.prefix.prefixlen > best.prefix.prefixlen):
                best = r
        return best

    def _resolve(self, node: str, route: Route, rib=None) -> Tuple[Optional[str], Optional[ipaddress.IPv4Address]]:
        """Recursive next-hop resolution -> (out interface, directly connected next hop)."""
        rib = rib if rib is not None else self.rib
        routes = rib.get(node, [])
        r, nh = route, route.nexthop
        for _ in range(8):
            if r.out_if and (nh is None or (self.l3.get((node, r.out_if)) and nh in self.l3[(node, r.out_if)].net)):
                return r.out_if, nh
            if nh is None:
                return r.out_if, None
            for (n, ifn), l3 in self.l3.items():
                if n == node and l3.up and nh in l3.net and l3.kind != "lo":
                    return ifn, nh
            nxt = self._lookup_in([x for x in routes if x is not r], nh)
            if nxt is None:
                return None, None
            r = nxt
            if r.nexthop is not None:
                nh = r.nexthop
        return None, None

    def lookup(self, node: str, ip) -> Optional[Route]:
        return self._lookup_in(self.rib.get(node, []), ip)

    # ------------------------------------------------------------------ tunnels
    def _tunnels(self) -> bool:
        changed = False
        pairs = {}
        for n in self.t.nodes.values():
            for i in n.ifaces.values():
                if i.tunnel and i.ip is not None:
                    peer = i.tunnel.get("peer")
                    pn = self.t.nodes.get(peer)
                    if not pn:
                        continue
                    back = [j for j in pn.ifaces.values() if j.tunnel and j.tunnel.get("peer") == n.name]
                    if back:
                        key = tuple(sorted([(n.name, i.name), (pn.name, back[0].name)]))
                        pairs[key] = (n, i, pn, back[0])
        for key, (an, ai, bn, bi) in pairs.items():
            src_a = self._tunnel_source(an, ai)
            src_b = self._tunnel_source(bn, bi)
            up = False
            delay = jitter = loss = 0.0
            bw = 1e8
            if src_a and src_b and self.node_up(an.name) and self.node_up(bn.name) and not ai.shutdown \
                    and not bi.shutdown:
                w = self.walk(an.name, src_b, src_ip=src_a, record=False, use_tunnels=False)
                w2 = self.walk(bn.name, src_a, src_ip=src_b, record=False, use_tunnels=False)
                if w.ok and w2.ok:
                    up = True
                    delay, jitter, loss, bw = w.delay_ms, w.jitter_ms, w.loss, w.min_bw
            prev = self.tunnels_up.get(key)
            if up:
                info = {"a": (an.name, ai.name), "b": (bn.name, bi.name), "delay": delay, "jitter": jitter,
                        "loss": loss, "bw": bw, "type": ai.tunnel.get("type", "gre")}
                if prev:
                    info["domain"] = prev.get("domain")
                if prev is None:
                    changed = True
                self.tunnels_up[key] = info
            else:
                if prev is not None:
                    changed = True
                    self.tunnels_up.pop(key, None)
                    for k in (prev["a"], prev["b"]):
                        if k in self.l3:
                            self.l3[k].up = False
                            self.l3[k].domain = None
        return changed

    def _tunnel_source(self, n: Node, ifc: Iface):
        src = ifc.tunnel.get("source")
        if src:
            l3 = self.l3.get((n.name, src))
            return l3.ip.ip if l3 and l3.up else None
        for (nn, ifn), l3 in sorted(self.l3.items()):
            if nn == n.name and l3.up and l3.kind == "phys":
                for l in self.t.links_of(n.name):
                    other = l.other(n.name, ifn)
                    if (l.a_node, l.a_if) == (n.name, ifn) or (l.b_node, l.b_if) == (n.name, ifn):
                        if self.t.nodes[other[0]].kind == "cloud":
                            return l3.ip.ip
        return None

    # ------------------------------------------------------------------ DHCP
    def _dhcp(self) -> None:
        used = {l3.ip.ip for l3 in self.l3.values()}
        servers = [n for n in self.t.nodes.values() if (n.services or {}).get("dhcp") and self.node_up(n.name)]
        for n in sorted(self.t.nodes.values(), key=lambda x: x.name):
            for i in n.ifaces.values():
                if not i.dhcp or not self.node_up(n.name) or i.shutdown:
                    continue
                vx = ("ep", n.name, i.name)
                dom = self.domain_of.get(vx)
                lease = {"ip": None, "server": None, "gateway": None, "dns": None, "reason": "no DHCP server"}
                if dom is None:
                    lease["reason"] = "interface not connected"
                    self.dhcp_leases[(n.name, i.name)] = lease
                    continue
                old = self.sticky_leases.get((n.name, i.name))
                if old and old.get("ip") is not None:
                    # an existing lease survives network failures until it expires
                    lease = dict(old)
                    lease["reason"] = "bound (existing lease)"
                    used.add(lease["ip"].ip)
                    self.l3[(n.name, i.name)] = L3If(n.name, i.name, lease["ip"], vx, dom, True, "host",
                                                     self._port_bw(n, i.name))
                    self.dhcp_leases[(n.name, i.name)] = lease
                    continue
                net = gw_ip = None
                srv_node = None
                # 1) server on the same segment
                for s in servers:
                    for (sn, sif), l3 in self.l3.items():
                        if sn == s.name and l3.up and l3.domain == dom and self._pool_for(s, l3.net):
                            srv_node, net = s, l3.net
                            gw_ip = l3.ip.ip if s.routes else None
                            break
                    if srv_node:
                        break
                # 2) relay agent (ip helper-address) on the segment
                if srv_node is None:
                    for (rn, rif), l3 in sorted(self.l3.items()):
                        if l3.up and l3.domain == dom:
                            ifc = self.t.iface(rn, rif)
                            for h in (ifc.helper if ifc else []):
                                owner = self._ip_owner_up(h)
                                if owner and owner[0] in {x.name for x in servers}:
                                    w = self.walk(rn, h, src_ip=l3.ip.ip, record=False)
                                    if w.ok and self._pool_for(self.t.nodes[owner[0]], l3.net):
                                        srv_node, net, gw_ip = self.t.nodes[owner[0]], l3.net, l3.ip.ip
                                        break
                                    lease["reason"] = f"relay {rn} cannot reach {h}"
                            if srv_node:
                                break
                if srv_node is not None and net is not None:
                    pool = self._pool_for(srv_node, net)
                    for vip, (vn, vif) in self.vips.items():
                        if vip in net:
                            gw_ip = vip
                    if pool.get("gateway"):
                        gw_ip = ipaddress.ip_address(str(pool["gateway"]))
                    hosts = list(net.hosts())
                    off = int(pool.get("offset", self.s["dhcp_offset"]))
                    ip = next((h for h in hosts[off - 1:] if h not in used), None)
                    if ip is None:
                        lease["reason"] = "pool exhausted"
                    else:
                        used.add(ip)
                        dns = None
                        dsrv = [x for x in self.t.nodes.values() if (x.services or {}).get("dns")]
                        if dsrv:
                            dns = self.t.resolve_host(dsrv[0].name)
                        lease = {"ip": ipaddress.ip_interface(f"{ip}/{net.prefixlen}"), "server": srv_node.name,
                                 "gateway": gw_ip, "dns": dns, "reason": "bound", "pool": pool.get("name")}
                        self.l3[(n.name, i.name)] = L3If(n.name, i.name, lease["ip"], vx, dom, True, "host",
                                                         self._port_bw(n, i.name))
                self.dhcp_leases[(n.name, i.name)] = lease

    def _pool_for(self, server: Node, net) -> Optional[dict]:
        cfg = (server.services or {}).get("dhcp") or {}
        for pname in cfg.get("pools", []) or []:
            p = self.t.pools.get(pname) if isinstance(pname, str) else pname
            if not p:
                continue
            try:
                pnet = ipaddress.ip_network(str(p.get("ipv4")), strict=False)
            except ValueError:
                continue
            if pnet == net or net.subnet_of(pnet):
                d = dict(p)
                d["name"] = pname if isinstance(pname, str) else str(pnet)
                return d
        return None

    # ================================================================== packet walk
    def l2_path(self, a: tuple, b: tuple) -> Optional[List[dict]]:
        """Forwarding-tree path between two vertices: list of edges in order."""
        if a == b:
            return []
        prev: Dict[tuple, Tuple[tuple, dict]] = {a: (None, None)}
        q = [a]
        while q:
            nq = []
            for x in q:
                for y, e in sorted(self.fwd_adj.get(x, []), key=lambda z: (str(z[0]), z[1]["u_port"])):
                    if y not in prev:
                        prev[y] = (x, e)
                        if y == b:
                            path = []
                            cur = b
                            while prev[cur][0] is not None:
                                path.append((prev[cur][0], cur, prev[cur][1]))
                                cur = prev[cur][0]
                            return [{"from": f, "to": t, "edge": e2} for f, t, e2 in reversed(path)]
                        nq.append(y)
            q = nq
        return None

    def _acl_permits(self, node: str, ifn: str, direction: str, src, dst, proto: str, dport) -> Tuple[bool, str]:
        ifc = self.t.iface(node, ifn)
        if ifc is None:
            return True, ""
        name = ifc.acl_in if direction == "in" else ifc.acl_out
        if not name:
            return True, ""
        acls = (self.t.policies or {}).get("acl", {}) or {}
        nacl = (self.t.nodes[node].policies or {}).get("acl", {}) or {}
        rules = nacl.get(name, acls.get(name))
        if rules is None:
            return True, f"ACL {name} not defined (permit)"
        if isinstance(rules, dict):
            rules = rules.get("rules", [])
        for k, r in enumerate(rules):
            if self._acl_match(r, src, dst, proto, dport):
                act = str(r.get("action", "permit"))
                return act == "permit", f"ACL {name} rule {k + 1} {act}"
        return False, f"ACL {name} implicit deny"

    @staticmethod
    def _acl_match(r: dict, src, dst, proto, dport) -> bool:
        rp = str(r.get("proto", "ip"))
        if rp not in ("ip", "any") and rp != proto:
            return False
        for key, ip in (("src", src), ("dst", dst)):
            spec = r.get(key, "any")
            if spec in ("any", None):
                continue
            try:
                if ip not in ipaddress.ip_network(str(spec), strict=False):
                    return False
            except ValueError:
                return False
        dp = r.get("dport")
        if dp is not None:
            ports = dp if isinstance(dp, list) else [dp]
            if dport is None or int(dport) not in [int(x) for x in ports]:
                return False
        return True

    def _nat_outside(self, node: str, out_if: str, in_if: Optional[str]) -> bool:
        nat = (self.t.nodes[node].policies or {}).get("nat")
        if not nat:
            return False
        inside = nat.get("inside", []) or []
        return nat.get("outside") == out_if and in_if is not None and in_if in inside

    def walk(self, src_node: str, dst, src_ip=None, proto: str = "icmp", dport=None, ttl: int = 64,
             record: bool = True, use_tunnels: bool = True, nat_state: Optional[dict] = None,
             t0: float = 0.0, kind: str = "echo-request") -> WalkResult:
        dst = ipaddress.ip_address(str(dst))
        res = WalkResult(False, "", src_ip=None, dst_ip=dst)
        if src_node not in self.t.nodes:
            res.reason = f"unknown node {src_node}"
            return res
        if not self.node_up(src_node):
            res.reason = f"{src_node} is down"
            return res
        mine = self.my_ips(src_node)
        if src_ip is None:
            r = self.lookup(src_node, dst)
            if dst in mine:
                src_ip = dst
            elif r is not None:
                oif, _ = self._resolve(src_node, r)
                l3 = self.l3.get((src_node, oif)) if oif else None
                src_ip = l3.ip.ip if l3 else (next(iter(mine)) if mine else None)
            else:
                src_ip = next(iter(mine), None)
        src_ip = ipaddress.ip_address(str(src_ip)) if src_ip is not None else None
        res.src_ip = src_ip
        if src_ip is None:
            res.reason = f"{src_node} has no IPv4 address"
            return res
        pkt_src, pkt_dst = src_ip, dst
        nat_state = nat_state if nat_state is not None else {}
        cur, in_if = src_node, None
        tclock = t0
        jit2 = 0.0
        surv = 1.0
        arp_cache: Set[Tuple[str, str]] = set()
        lsp_to = None
        res.l3_path.append(src_node)
        for hop_no in range(self.s["max_hops"]):
            node = self.t.nodes[cur]
            if not self.node_up(cur):
                res.reason = f"{cur} is down"
                break
            mine = self.my_ips(cur)
            if pkt_dst in mine:
                key = str(pkt_dst)
                if key in nat_state and nat_state[key][0] == cur:
                    orig = ipaddress.ip_address(nat_state[key][1])
                    if record:
                        res.events.append(Event(tclock, cur, "L3", "nat", f"NAT un-translate {pkt_dst} -> {orig}",
                                                self._pdu(pkt_src, orig, ttl, proto, kind, None, None, dport)))
                    pkt_dst = orig
                    res.nat[key] = str(orig)
                    continue
                res.ok, res.reason, res.final_node = True, "delivered", cur
                res.hops.append(Hop(cur, in_if, None, "destination"))
                if record:
                    res.events.append(Event(tclock, cur, "L3", "deliver", f"{proto.upper()} {kind} delivered to {cur}",
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                break
            if hop_no > 0 and not node.routes:
                res.reason = f"{cur} is an end host and does not forward packets"
                break
            if lsp_to is not None and lsp_to in mine:
                if record:
                    res.events.append(Event(tclock, cur, "L2.5", "mpls", f"MPLS pop label at egress PE {cur}",
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                lsp_to = None
            lookup_ip = lsp_to if lsp_to is not None else pkt_dst
            route = self.lookup(cur, lookup_ip)
            if (lsp_to is None and route is not None and route.proto == "ibgp" and route.nexthop is not None
                    and ((node.routing or {}).get("mpls") or {}).get("ldp")):
                lsp_to = route.nexthop
                label = 16 + (int(lsp_to) & 0xFFF)
                if record:
                    res.events.append(Event(tclock, cur, "L2.5", "mpls",
                                            f"MPLS push label {label} (LSP to {lsp_to})",
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport,
                                                      mpls=label)))
                route = self.lookup(cur, lsp_to)
            if route is None:
                res.reason = f"{cur}: no route to {lookup_ip} (destination unreachable)"
                res.hops.append(Hop(cur, in_if, None, "no route"))
                if record:
                    res.events.append(Event(tclock, cur, "L3", "drop", res.reason,
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                break
            out_if, nh = self._resolve(cur, route)
            out_l3 = self.l3.get((cur, out_if)) if out_if else None
            if out_l3 is None or not out_l3.up:
                res.reason = f"{cur}: next hop for {pkt_dst} unresolved"
                res.hops.append(Hop(cur, in_if, out_if, "unresolved"))
                break
            if out_l3.kind == "tun" and not use_tunnels:
                res.reason = f"{cur}: tunnel not usable for underlay"
                break
            ok, why = self._acl_permits(cur, out_if, "out", pkt_src, pkt_dst, proto, dport)
            if not ok:
                res.reason = f"{cur} {out_if}: {why}"
                res.hops.append(Hop(cur, in_if, out_if, why))
                if record:
                    res.events.append(Event(tclock, cur, "L3", "drop", res.reason,
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                break
            if self._nat_outside(cur, out_if, in_if):
                new_src = out_l3.ip.ip
                nat_state[str(new_src)] = (cur, str(pkt_src))
                if record:
                    res.events.append(Event(tclock, cur, "L3", "nat", f"NAT {pkt_src} -> {new_src}",
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                pkt_src = new_src
            if hop_no > 0:
                ttl -= 1
                tclock += self.s["proc_delay_ms"]
                if ttl <= 0:
                    res.reason = f"TTL expired at {cur}"
                    res.final_node = cur
                    res.hops.append(Hop(cur, in_if, None, "ttl-exceeded"))
                    if record:
                        res.events.append(Event(tclock, cur, "L3", "ttl", f"Time exceeded at {cur}",
                                                self._pdu(pkt_src, pkt_dst, 0, proto, kind, None, None, dport)))
                    break
            target = nh or pkt_dst
            # ARP: find the owner of the next hop in the same broadcast domain
            tgt = None
            for (n2, i2), l3 in self.l3.items():
                if l3.up and l3.ip.ip == target and l3.domain == out_l3.domain and n2 != cur:
                    tgt = l3
            if tgt is None and target in self.vips:
                vn, vif = self.vips[target]
                cand = self.l3.get((vn, vif))
                if cand and cand.domain == out_l3.domain and vn != cur:
                    tgt = cand
            if tgt is None:
                res.reason = f"{cur}: ARP for {target} on {out_if} failed (host unreachable)"
                res.hops.append(Hop(cur, in_if, out_if, "arp failed"))
                if record:
                    res.events.append(Event(tclock, cur, "L2", "arp", f"ARP who-has {target}? no reply",
                                            self._arp_pdu(cur, out_if, out_l3.ip.ip, target)))
                break
            src_mac = _mac(cur, out_if)
            dst_mac = _mac(tgt.node, tgt.name) if target not in self.vips else \
                "00:00:5e:00:01:" + f"{self._vrid(target):02x}"
            if record and (cur, str(target)) not in arp_cache:
                arp_cache.add((cur, str(target)))
                res.events.append(Event(tclock, cur, "L2", "arp", f"ARP who-has {target} tell {out_l3.ip.ip}",
                                        self._arp_pdu(cur, out_if, out_l3.ip.ip, target)))
                res.events.append(Event(tclock + 0.001, tgt.node, "L2", "arp", f"ARP {target} is-at {dst_mac}",
                                        self._arp_pdu(tgt.node, tgt.name, target, out_l3.ip.ip, reply=True,
                                                      mac=dst_mac, tmac=src_mac)))
            res.hops.append(Hop(cur, in_if, out_if))
            # L2 transport
            if out_l3.kind == "tun":
                info = next((v for v in self.tunnels_up.values()
                             if (cur, out_if) in (v["a"], v["b"])), None)
                if info:
                    tclock += info["delay"]
                    jit2 += info["jitter"] ** 2
                    surv *= (1 - info["loss"])
                    res.min_bw = min(res.min_bw, info["bw"])
                if record:
                    res.events.append(Event(tclock, tgt.node, "L3", "tunnel",
                                            f"{info['type'] if info else 'tunnel'} {cur} -> {tgt.node} over underlay",
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, src_mac, dst_mac, dport,
                                                      tunnel=info), from_node=cur, to_node=tgt.node))
            else:
                path = self.l2_path(out_l3.vertex, tgt.vertex)
                if path is None:
                    res.reason = f"{cur}: no L2 path to {tgt.node}"
                    break
                for step in path:
                    e = step["edge"]
                    if e["link"] is None:
                        continue
                    tclock += e["delay"] + self.s["switch_delay_ms"]
                    jit2 += e["jitter"] ** 2
                    surv *= (1 - e["loss"])
                    res.min_bw = min(res.min_bw, e["bw"])
                    res.links.append(e["link"])
                    fn, tn = step["from"][1], step["to"][1]
                    if record:
                        vlan_tag = e["tag"]
                        res.events.append(Event(tclock, tn, "L2", "frame",
                                                f"{fn} -> {tn}" + (f" [802.1Q VLAN {vlan_tag}]" if vlan_tag else ""),
                                                self._pdu(pkt_src, pkt_dst, ttl, proto, kind, src_mac, dst_mac, dport,
                                                          vlan=vlan_tag),
                                                link=e["link"], from_node=fn, to_node=tn))
            ok, why = self._acl_permits(tgt.node, tgt.name, "in", pkt_src, pkt_dst, proto, dport)
            if not ok:
                res.reason = f"{tgt.node} {tgt.name}: {why}"
                res.hops.append(Hop(tgt.node, tgt.name, None, why))
                if record:
                    res.events.append(Event(tclock, tgt.node, "L3", "drop", res.reason,
                                            self._pdu(pkt_src, pkt_dst, ttl, proto, kind, None, None, dport)))
                break
            cur, in_if = tgt.node, tgt.name
            res.l3_path.append(cur)
        else:
            res.reason = "routing loop (hop limit reached)"
        res.delay_ms = tclock - t0
        res.jitter_ms = math.sqrt(jit2)
        res.loss = 1 - surv
        if res.min_bw == float("inf"):
            res.min_bw = 1e10
        return res

    def _vrid(self, vip) -> int:
        for (n, ifn, g), st in self.vrrp_state.items():
            if st["vip"] == vip:
                return g & 0xFF
        return 1

    @staticmethod
    def _pdu(src, dst, ttl, proto, kind, smac, dmac, dport, vlan=None, tunnel=None, mpls=None) -> dict:
        pdu = {}
        if mpls is not None:
            pdu["MPLS"] = {"Label": mpls, "Traffic class": 0, "Bottom of stack": 1, "TTL": ttl}
        if smac:
            pdu["Ethernet II"] = {"Source MAC": smac, "Destination MAC": dmac,
                                  "EtherType": "0x8100 (802.1Q)" if vlan else "0x0800 (IPv4)"}
        if vlan:
            pdu["IEEE 802.1Q"] = {"Priority (PCP)": 0, "VLAN ID": vlan, "EtherType": "0x0800 (IPv4)"}
        if tunnel:
            pdu["Tunnel"] = {"Type": str(tunnel.get("type", "gre")).upper(), "Outer path delay": f"{tunnel['delay']:.3f} ms"}
        ip_proto = {"icmp": 1, "tcp": 6, "udp": 17}.get(proto, 0)
        pdu["IPv4"] = {"Version": 4, "IHL": 5, "TTL": ttl, "Protocol": f"{ip_proto} ({proto.upper()})",
                       "Source": str(src), "Destination": str(dst)}
        if proto == "icmp":
            pdu["ICMP"] = {"Type": 8 if kind == "echo-request" else 0,
                           "Code": 0, "Message": "Echo request" if kind == "echo-request" else "Echo reply"}
        elif proto == "tcp":
            pdu["TCP"] = {"Destination port": dport, "Flags": "SYN" if kind == "echo-request" else "SYN,ACK"}
        elif proto == "udp":
            pdu["UDP"] = {"Destination port": dport}
        return pdu

    @staticmethod
    def _arp_pdu(node, ifn, sip, tip, reply=False, mac=None, tmac=None) -> dict:
        m = mac or _mac(node, ifn)
        return {"Ethernet II": {"Source MAC": m, "Destination MAC": tmac if reply else "ff:ff:ff:ff:ff:ff",
                                "EtherType": "0x0806 (ARP)"},
                "ARP": {"Opcode": "2 (reply)" if reply else "1 (request)", "Sender MAC": m, "Sender IP": str(sip),
                        "Target MAC": tmac if reply else "00:00:00:00:00:00", "Target IP": str(tip)}}

    # ================================================================== high level operations
    def ping(self, src: str, dst, proto: str = "icmp", dport=None, record: bool = True) -> dict:
        dip = self.t.resolve_host(str(dst)) if not _is_ip(dst) else ipaddress.ip_address(str(dst))
        if dip is None:
            dip = self._lease_ip(str(dst))
        if dip is None:
            return {"ok": False, "reason": f"cannot resolve '{dst}'", "rtt_ms": None, "events": []}
        nat: dict = {}
        req = self.walk(src, dip, proto=proto, dport=dport, record=record, nat_state=nat)
        out = {"ok": False, "reason": req.reason, "request": req, "reply": None, "dst_ip": dip,
               "rtt_ms": None, "events": list(req.events)}
        if not req.ok:
            return out
        reply_dst = req.src_ip
        for k, (nnode, orig) in nat.items():
            reply_dst = ipaddress.ip_address(k)
        rep = self.walk(req.final_node, reply_dst, src_ip=dip, proto=proto, dport=dport, record=record,
                        nat_state=nat, t0=req.delay_ms, kind="echo-reply")
        out["reply"] = rep
        out["events"] += rep.events
        if rep.ok and rep.final_node == src:
            out["ok"] = True
            out["reason"] = "reply received"
            out["rtt_ms"] = req.delay_ms + rep.delay_ms
            out["jitter_ms"] = math.sqrt(req.jitter_ms ** 2 + rep.jitter_ms ** 2)
            out["loss"] = 1 - (1 - req.loss) * (1 - rep.loss)
            out["min_bw"] = min(req.min_bw, rep.min_bw)
        else:
            out["reason"] = "reply lost: " + (rep.reason or f"reply delivered to {rep.final_node}")
        return out

    def _lease_ip(self, name: str):
        for (n, i), lease in self.dhcp_leases.items():
            if n == name and lease.get("ip") is not None:
                return lease["ip"].ip
        return None

    def traceroute(self, src: str, dst) -> List[dict]:
        dip = self.t.resolve_host(str(dst)) if not _is_ip(dst) else ipaddress.ip_address(str(dst))
        if dip is None:
            dip = self._lease_ip(str(dst))
        if dip is None:
            return [{"hop": 1, "node": None, "ip": None, "rtt_ms": None, "note": f"cannot resolve {dst}"}]
        full = self.walk(src, dip, record=False)
        hops = []
        for ttl in range(1, 31):
            w = self.walk(src, dip, ttl=ttl, record=False)
            node = w.final_node
            ip = None
            if node:
                if w.ok:
                    ip = dip
                else:
                    h = next((h for h in w.hops if h.node == node), None)
                    l3 = self.l3.get((node, h.in_if)) if h and h.in_if else None
                    ip = l3.ip.ip if l3 else None
            hops.append({"hop": ttl, "node": node, "ip": ip, "rtt_ms": (w.delay_ms * 2) if node else None,
                         "note": "" if node else w.reason})
            if w.ok or not node:
                break
        if not full.ok and hops and hops[-1]["node"] is not None and hops[-1]["ip"] != dip:
            hops.append({"hop": len(hops) + 1, "node": None, "ip": None, "rtt_ms": None, "note": full.reason})
        return hops

    def service_listens(self, node: str, proto: str, port: int) -> bool:
        n = self.t.nodes.get(node)
        if not n:
            return False
        lst = (n.services or {}).get("listen")
        if lst is None:
            if n.kind == "server":
                lst = [22, 53, 80, 443]
            elif n.routes:
                lst = [22]
            else:
                lst = []
        if (n.services or {}).get("dns") and port == 53:
            return True
        if (n.services or {}).get("dhcp") and proto == "udp" and port == 67:
            return True
        return int(port) in [int(x) for x in lst]

    def connect(self, src: str, dst, port: int, proto: str = "tcp") -> dict:
        r = self.ping(src, dst, proto=proto, dport=port, record=False)
        if not r["ok"]:
            return {"open": False, "reason": r["reason"]}
        node = r["request"].final_node
        if not self.service_listens(node, proto, port):
            return {"open": False, "reason": f"{node} is not listening on {proto}/{port} (RST)"}
        return {"open": True, "reason": f"{proto}/{port} open on {node}", "rtt_ms": r["rtt_ms"]}

    def dns_resolve(self, client: str, name: str) -> dict:
        srv = [n for n in self.t.nodes.values() if (n.services or {}).get("dns")]
        if not srv:
            return {"ok": False, "reason": "no DNS server in topology"}
        s = srv[0]
        zone = str((s.services["dns"] or {}).get("zone", "lan")) if isinstance(s.services["dns"], dict) else "lan"
        c = self.connect(client, s.name, 53, proto="udp")
        if not c["open"]:
            return {"ok": False, "reason": f"DNS server {s.name} unreachable: {c['reason']}"}
        records = (s.services["dns"] or {}).get("records", {}) if isinstance(s.services["dns"], dict) else {}
        short = name[:-len(zone) - 1] if name.endswith("." + zone) else name
        if name in records or short in records:
            return {"ok": True, "address": str(records.get(name, records.get(short))), "server": s.name}
        ip = self.t.resolve_host(short) or self._lease_ip(short)
        if ip is None:
            return {"ok": False, "reason": f"NXDOMAIN {name}"}
        return {"ok": True, "address": str(ip), "server": s.name}

    # ================================================================== tables for CLI/GUI
    def mac_table(self, node: str) -> List[dict]:
        out = []
        for (n, ifn), l3 in self.l3.items():
            if n == node or not l3.up or l3.kind in ("lo", "tun"):
                continue
            for vx in [v for v in self.fwd_adj if v[0] == "br" and v[1] == node]:
                if self.domain_of.get(vx) != l3.domain:
                    continue
                path = self.l2_path(vx, l3.vertex)
                if not path:
                    continue
                e = path[0]["edge"]
                port = e["u_port"] if e["u"] == vx else e["v_port"]
                out.append({"vlan": vx[2], "mac": _mac(n, ifn), "port": port, "host": f"{n}:{ifn}"})
        return sorted(out, key=lambda r: (r["vlan"], r["port"], r["mac"]))

    def arp_table(self, node: str) -> List[dict]:
        out = []
        for (n, ifn), l3 in self.l3.items():
            if n != node or not l3.up or l3.kind in ("lo", "tun"):
                continue
            for (n2, i2), x in self.l3.items():
                if n2 != node and x.up and x.domain == l3.domain and x.ip.ip in l3.net:
                    out.append({"ip": str(x.ip.ip), "mac": _mac(n2, i2), "interface": ifn, "node": n2})
            for vip, (vn, vif) in self.vips.items():
                v3 = self.l3.get((vn, vif))
                if vn != node and v3 and v3.domain == l3.domain and vip in l3.net:
                    out.append({"ip": str(vip), "mac": f"00:00:5e:00:01:{self._vrid(vip):02x}", "interface": ifn,
                                "node": f"{vn} (VRRP)"})
        return sorted(out, key=lambda r: ipaddress.ip_address(r["ip"]))

    def stp_summary(self, node: str) -> List[dict]:
        out = []
        for vx, info in sorted(self.stp_info.items(), key=lambda z: (z[0][1], z[0][2])):
            if vx[1] != node:
                continue
            ports = [{"port": p, "role": r} for (n, p, v), r in sorted(self.port_roles.items()) if n == node
                     and v == vx[2]]
            out.append({"vlan": vx[2], "root": info["root"], "is_root": info["root"] == node, "cost": info["cost"],
                        "root_port": info["root_port"], "mode": info["mode"], "ports": ports,
                        "bridge_priority": self._bridge_id(vx)[0]})
        return out

    def describe_path(self, w: WalkResult) -> str:
        return " -> ".join(w.l3_path) + (f"  ({w.delay_ms:.3f} ms, bottleneck {fmt_bw(w.min_bw)})" if w.ok else "")


def _is_ip(x) -> bool:
    try:
        ipaddress.ip_address(str(x))
        return True
    except ValueError:
        return False
