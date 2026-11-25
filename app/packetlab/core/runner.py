"""Test runner and failure-scenario timeline.

Tests are assertions from the ``tests:`` section of a topology.  They run in three
phases (static, functional, performance).  A test may name a scenario in
``during:``; the runner then replays the scenario timeline and measures behaviour
while things fail, using the convergence timers in the settings.

Outage model (documented in Help > Testing):
  * VRRPv3 master-down interval = 3 x advertisement + skew (RFC 5798)
  * RSTP re-convergence ~0.6 s, legacy 802.1D 30 s, G.8032 ring switch 50 ms
  * OSPF / IS-IS: carrier-loss detection on a directly attached link (~0.2 s SPF)
    otherwise dead / hold interval; BFD (if enabled on the neighbour) 150 ms
  * BGP: directly attached failure ~0.5 s (fast external fall-over), otherwise hold time
"""
from __future__ import annotations

import ipaddress
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .compute import Compute
from .engine import Engine
from .model import Topology, fmt_bw, parse_bandwidth, parse_percent, parse_time_ms

DEFAULT_TIMERS = {
    "rstp_s": 0.6, "stp_s": 30.0, "g8032_s": 0.05,
    "ospf_direct_s": 0.2, "ospf_dead_s": 40.0, "isis_direct_s": 0.2, "isis_hold_s": 30.0,
    "bgp_direct_s": 0.5, "bgp_hold_s": 90.0, "bfd_s": 0.15, "lease_reacquire_s": 4.0,
}

PHASE = {"static": 1, "functional": 2, "performance": 3}


def phase_of(ttype: str) -> int:
    if ttype.startswith("static."):
        return 1
    if ttype in ("rfc2544.throughput", "rfc2544.latency", "y1564", "twamp"):
        return 3
    return 2


@dataclass
class TestResult:
    id: str
    type: str
    status: str               # pass | fail | error | skip
    detail: str = ""
    measured: str = ""
    expected: str = ""
    duration_ms: float = 0.0
    phase: int = 2
    standard: str = ""
    during: str = ""


@dataclass
class Timeline:
    events: List[Tuple[float, str, str, dict]] = field(default_factory=list)  # (t_s, action, target, params)


class Scenario:
    def __init__(self, topo: Topology, name: str, settings: Optional[dict] = None):
        self.t = topo
        self.name = name
        self.settings = settings or {}
        spec = (topo.scenarios or {}).get(name)
        if spec is None:
            raise KeyError(f"unknown scenario '{name}'")
        evs = []
        for st in spec.get("steps", []) or []:
            at = parse_time_ms(st.get("at", "0s")) / 1000.0
            act = st.get("action")
            tgt = str(st.get("target", ""))
            params = st.get("params") or {}
            if act == "node_reboot":
                dur = parse_time_ms(params.get("duration", "60s")) / 1000.0
                evs.append((at, "node_down", tgt, params))
                evs.append((at + dur, "node_up", tgt, params))
            elif act == "traffic":
                continue
            else:
                evs.append((at, act, tgt, params))
        evs.sort(key=lambda e: e[0])
        self.events = evs
        self.end = (max([e[0] for e in evs]) + 30.0) if evs else 30.0

    def state_at(self, t_s: float) -> Tuple[set, set, dict]:
        down_links, down_nodes, prof = set(), set(), {}
        for at, act, tgt, params in self.events:
            if at > t_s:
                break
            self._apply(act, tgt, params, down_links, down_nodes, prof)
        return down_links, down_nodes, prof

    def _apply(self, act, tgt, params, down_links, down_nodes, prof):
        if act in ("link_down", "link_up", "set_profile"):
            l = self.t.link_by_name(tgt)
            if l is None:
                raise KeyError(f"scenario {self.name}: unknown link '{tgt}'")
            if act == "link_down":
                down_links.add(l.idx)
            elif act == "link_up":
                down_links.discard(l.idx)
            else:
                pname = params.get("profile")
                if pname in self.t.profiles:
                    prof[l.idx] = self.t.profiles[pname]
        elif act in ("node_down", "node_up"):
            if tgt not in self.t.nodes:
                raise KeyError(f"scenario {self.name}: unknown node '{tgt}'")
            (down_nodes.add if act == "node_down" else down_nodes.discard)(tgt)

    def engines(self, base: Engine) -> List[Tuple[float, Engine, Optional[tuple]]]:
        out = [(0.0, base, None)]
        dl, dn, pr = set(), set(), {}
        for ev in self.events:
            at, act, tgt, params = ev
            self._apply(act, tgt, params, dl, dn, pr)
            e = Engine(self.t, self.settings, set(dl), set(dn), dict(pr), sticky_leases=base.dhcp_leases)
            out.append((at, e, ev))
        return out


class Runner:
    def __init__(self, topo: Topology, settings: Optional[dict] = None, compute: Optional[Compute] = None,
                 log=None):
        self.t = topo
        self.settings = settings or {}
        self.timers = dict(DEFAULT_TIMERS)
        self.timers.update({k: float(v) for k, v in (self.settings.get("timers") or {}).items() if k in DEFAULT_TIMERS})
        self.compute = compute or Compute("cpu")
        self.samples = int(self.settings.get("mc_samples", 20000))
        self.seed = int(self.settings.get("seed", 1))
        self.log = log or (lambda m: None)
        self.base = Engine(topo, self.settings)
        self._scn_cache: Dict[str, list] = {}

    # ------------------------------------------------------------------ public
    def run_all(self, stop_on_phase_failure: bool = True, only: Optional[List[str]] = None) -> List[TestResult]:
        tests = [t for t in self.t.tests if isinstance(t, dict)]
        if only:
            tests = [t for t in tests if t.get("id") in only]
        tests.sort(key=lambda x: phase_of(str(x.get("type", ""))))
        results: List[TestResult] = []
        failed_phase = None
        for spec in tests:
            ph = phase_of(str(spec.get("type", "")))
            if failed_phase is not None and ph > failed_phase and stop_on_phase_failure:
                results.append(TestResult(str(spec.get("id")), str(spec.get("type")), "skip",
                                          f"skipped: phase {failed_phase} had failures", phase=ph,
                                          standard=str(spec.get("standard", "")), during=str(spec.get("during", ""))))
                continue
            r = self.run_one(spec)
            results.append(r)
            if r.status in ("fail", "error") and failed_phase is None and ph == 1:
                failed_phase = ph
        return results

    def run_one(self, spec: dict) -> TestResult:
        tid, ttype = str(spec.get("id", "?")), str(spec.get("type", ""))
        res = TestResult(tid, ttype, "error", phase=phase_of(ttype), standard=str(spec.get("standard", "")),
                         during=str(spec.get("during", "") or ""))
        t0 = time.perf_counter()
        try:
            fn = getattr(self, "_t_" + ttype.replace(".", "_"), None)
            if fn is None:
                res.detail = f"unknown test type '{ttype}'"
            else:
                status, detail, measured, expected = fn(spec)
                res.status, res.detail, res.measured, res.expected = status, detail, measured, expected
        except Exception as e:  # noqa: BLE001  - a broken test must not stop the run
            res.status, res.detail = "error", f"{type(e).__name__}: {e}"
        res.duration_ms = (time.perf_counter() - t0) * 1000
        self.log(f"[{res.status.upper()}] {tid} ({ttype}) {res.detail}")
        return res

    # ------------------------------------------------------------------ helpers
    def _engine_for(self, spec: dict) -> Engine:
        if spec.get("during") and spec.get("at") is not None:
            scn = Scenario(self.t, spec["during"], self.settings)
            at = parse_time_ms(spec["at"]) / 1000.0
            dl, dn, pr = scn.state_at(at)
            return Engine(self.t, self.settings, dl, dn, pr, sticky_leases=self.base.dhcp_leases)
        return self.base

    @staticmethod
    def _exp(spec) -> dict:
        return spec.get("expect") or {}

    def _node(self, spec, key="on") -> str:
        n = spec.get(key) or spec.get("from")
        if n not in self.t.nodes:
            raise KeyError(f"unknown node '{n}'")
        return n

    # ------------------------------------------------------------------ static
    def _t_static_addressing(self, spec):
        probs = []
        nets: Dict[str, list] = {}
        e = self.base
        for (n, i), l3 in e.l3.items():
            if l3.kind == "lo":
                continue
            nets.setdefault(str(l3.net), []).append((n, i, l3.domain))
        for net, members in nets.items():
            doms = {m[2] for m in members if m[2] is not None and not (isinstance(m[2], tuple))}
            if len(doms) > 1:
                probs.append(f"{net} used on {len(doms)} separate segments: " +
                             ", ".join(f"{m[0]}:{m[1]}" for m in members))
        dups = [x for x in self.t.errors if x.startswith("duplicate IPv4")]
        probs += dups
        nl = sorted({ipaddress.ip_network(k) for k in nets}, key=lambda n: (int(n.network_address), n.prefixlen))
        for a in range(len(nl)):
            for b in range(a + 1, len(nl)):
                if nl[a] != nl[b] and nl[a].overlaps(nl[b]):
                    probs.append(f"{nl[a]} overlaps {nl[b]}")
        if probs:
            return "fail", "; ".join(probs[:5]), f"{len(probs)} problems", "none"
        return "pass", f"{len(nets)} subnets, no overlaps", "0 problems", "none"

    def _t_static_vlan_consistency(self, spec):
        probs = []
        for l in self.t.links:
            a = self.t.iface(l.a_node, l.a_if)
            b = self.t.iface(l.b_node, l.b_if)
            an, bn = self.t.nodes[l.a_node], self.t.nodes[l.b_node]
            if an.bridges and bn.bridges and a.mode in ("access", "trunk") and b.mode in ("access", "trunk"):
                if a.mode != b.mode:
                    probs.append(f"{l.name}: {a.mode} vs {b.mode} port mode mismatch")
                elif a.mode == "trunk":
                    if a.native != b.native:
                        probs.append(f"{l.name}: native VLAN mismatch {a.native} vs {b.native}")
                    av = a.allowed if a.allowed is not None else set(an.vlans)
                    bv = b.allowed if b.allowed is not None else set(bn.vlans)
                    if av != bv:
                        probs.append(f"{l.name}: allowed VLANs differ {sorted(av ^ bv)}")
                    for v in av:
                        if v not in an.vlans:
                            probs.append(f"{an.name}: trunk carries VLAN {v} not defined on switch")
                elif a.vlan != b.vlan and (l.a_node, l.b_node) and not self._is_service_uni(l):
                    probs.append(f"{l.name}: access VLAN {a.vlan} vs {b.vlan}")
        for e in [x for x in self.t.errors if "VLAN" in x]:
            probs.append(e)
        if probs:
            return "fail", "; ".join(probs[:5]), f"{len(probs)} problems", "consistent"
        return "pass", "trunks and access ports consistent", "consistent", "consistent"

    def _is_service_uni(self, link) -> bool:
        for s in self.t.services.values():
            mem = set((s or {}).get("members", []) or [])
            if f"{link.a_node}:{link.a_if}" in mem or f"{link.b_node}:{link.b_if}" in mem:
                return True
        return False

    # ------------------------------------------------------------------ protocol state
    def _t_ospf_neighbor(self, spec, proto="ospf"):
        e = self._engine_for(spec)
        n = self._node(spec)
        nb = (e.ospf_nbrs if proto == "ospf" else e.isis_nbrs).get(n, [])
        exp = self._exp(spec)
        want = str(exp.get("state", "FULL"))
        good = [x for x in nb if x["state"] == want]
        if exp.get("neighbor"):
            ok = any(x["neighbor"] == exp["neighbor"] for x in good)
            return ("pass" if ok else "fail"), f"{len(good)} {want} neighbours: " + \
                ", ".join(x["neighbor"] for x in good), ", ".join(x["neighbor"] for x in good), exp["neighbor"]
        cnt = int(exp.get("count", 1))
        ok = len(good) >= cnt
        return ("pass" if ok else "fail"), ", ".join(f"{x['neighbor']}({x['interface']})" for x in nb) or \
            "no neighbours", f"{len(good)} {want}", f">= {cnt} {want}"

    def _t_isis_neighbor(self, spec):
        return self._t_ospf_neighbor(spec, "isis")

    def _route_test(self, spec, protos):
        e = self._engine_for(spec)
        n = self._node(spec)
        pfx = ipaddress.ip_network(str(spec.get("prefix")), strict=False)
        r = next((x for x in e.rib.get(n, []) if x.prefix == pfx), None)
        exp = self._exp(spec)
        present = exp.get("present", True)
        if r is None or r.proto not in protos:
            return ("pass" if not present else "fail"), f"{pfx} not in {protos[0].upper()} table", "absent", \
                "present" if present else "absent"
        if exp.get("next_hop") and str(r.nexthop) != str(exp["next_hop"]):
            return "fail", f"next hop {r.nexthop}", str(r.nexthop), str(exp["next_hop"])
        return ("pass" if present else "fail"), f"{pfx} via {r.nexthop} ({r.proto})", "present", \
            "present" if present else "absent"

    def _t_ospf_route(self, spec):
        return self._route_test(spec, ("ospf",))

    def _t_bgp_route(self, spec):
        return self._route_test(spec, ("ebgp", "ibgp"))

    def _t_bgp_session(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        peer = str(spec.get("peer"))
        st = next((s for s in e.bgp_sessions.get(n, []) if str(s["peer"]) == peer), None)
        want = str(self._exp(spec).get("state", "Established"))
        if st is None:
            return "fail", f"no BGP neighbour {peer} configured on {n}", "missing", want
        return ("pass" if st["state"] == want else "fail"), \
            f"{n} -> {peer}: {st['state']} {st['reason']}".strip(), st["state"], want

    def _t_vrrp_state(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        g = int(spec.get("group", 1))
        st = next((v for (nn, ifn, gg), v in e.vrrp_state.items() if nn == n and gg == g), None)
        want = str(self._exp(spec).get("state", "MASTER"))
        if st is None:
            return "fail", f"{n} has no VRRP group {g}", "missing", want
        return ("pass" if st["state"] == want else "fail"), f"group {g} VIP {st['vip']} prio {st['priority']}", \
            st["state"], want

    def _t_stp_root(self, spec):
        e = self._engine_for(spec)
        vlan = int(spec.get("vlan", 1))
        want = str(self._exp(spec).get("bridge"))
        anchor = spec.get("on") or want
        info = e.stp_info.get(("br", anchor, vlan))
        if info is None:
            return "fail", f"{anchor} has no spanning-tree instance for VLAN {vlan}", "none", want
        ok = info["root"] == want
        return ("pass" if ok else "fail"), f"VLAN {vlan} root bridge seen from {anchor}: {info['root']} " \
            f"(priority {e._bridge_id(info['root_vertex'])[0]})", info["root"], want

    def _t_stp_port_state(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        port = str(spec.get("port"))
        vlan = int(spec.get("vlan", 1))
        role = e.port_roles.get((n, port, vlan), "none")
        want = str(self._exp(spec).get("role", "designated"))
        return ("pass" if role == want else "fail"), f"{n} {port} VLAN {vlan}: {role}", role, want

    def _t_lag_members(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        lag = str(spec.get("lag"))
        node = self.t.nodes[n]
        members = [m for m, l in node.member_of.items() if l == lag]
        up = 0
        for l in self.t.links:
            for nn, ii in ((l.a_node, l.a_if), (l.b_node, l.b_if)):
                if nn == n and ii in members and e.link_up(l):
                    up += 1
        want = int(self._exp(spec).get("up", len(members)))
        return ("pass" if up >= want else "fail"), f"{lag}: {up}/{len(members)} members up", str(up), f">= {want}"

    def _t_dhcp_lease(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        lease = next((v for (nn, i), v in e.dhcp_leases.items() if nn == n), None)
        if lease is None:
            return "fail", f"{n} is not a DHCP client", "none", "lease"
        exp = self._exp(spec)
        if lease.get("ip") is None:
            return "fail", f"no lease: {lease.get('reason')}", "none", "lease"
        if exp.get("pool") and lease.get("pool") != exp["pool"]:
            return "fail", f"lease from pool {lease.get('pool')}", str(lease.get("pool")), str(exp["pool"])
        return "pass", f"{lease['ip']} gw {lease['gateway']} from {lease['server']}", str(lease["ip"]), \
            str(exp.get("pool", "lease"))

    def _t_dns_resolve(self, spec):
        e = self._engine_for(spec)
        n = self._node(spec)
        r = e.dns_resolve(n, str(spec.get("name")))
        want = self._exp(spec).get("address")
        if not r["ok"]:
            return "fail", r["reason"], "no answer", str(want)
        ok = want is None or str(want) == r["address"]
        return ("pass" if ok else "fail"), f"{spec.get('name')} -> {r['address']} (server {r['server']})", \
            r["address"], str(want)

    # ------------------------------------------------------------------ reachability
    def _t_ping(self, spec):
        exp = self._exp(spec)
        if spec.get("during") and spec.get("at") is None:
            return self._outage_test(spec)
        e = self._engine_for(spec)
        r = e.ping(self._node(spec, "from"), spec.get("to"), record=False)
        loss_max = parse_percent(exp.get("loss_max", "0%"))
        want_ok = exp.get("reachable", True)
        if not r["ok"]:
            return ("pass" if not want_ok else "fail"), r["reason"], "unreachable", \
                "reachable" if want_ok else "unreachable"
        st = self.compute.sample_path(r["rtt_ms"] / 2, r.get("jitter_ms", 0.0), r.get("loss", 0.0),
                                      min(self.samples, 5000), self.seed)
        loss = st["loss"]
        ok = want_ok and loss <= loss_max + 1e-12
        if exp.get("rtt_max"):
            ok = ok and r["rtt_ms"] <= parse_time_ms(exp["rtt_max"])
        return ("pass" if ok else "fail"), \
            f"RTT {r['rtt_ms']:.3f} ms, modelled loss {loss * 100:.3f}% via {' > '.join(r['request'].l3_path)}", \
            f"{r['rtt_ms']:.3f} ms, {loss * 100:.3f}% loss", \
            f"loss <= {loss_max * 100:g}%" + (f", RTT <= {exp['rtt_max']}" if exp.get("rtt_max") else "")

    def _t_traceroute(self, spec):
        e = self._engine_for(spec)
        hops = e.traceroute(self._node(spec, "from"), spec.get("to"))
        path = [h["node"] for h in hops if h["node"]]
        via = [str(x) for x in (self._exp(spec).get("via") or [])]
        # 'via' must appear in order as consecutive routers before the destination
        it = iter(path)
        ordered = all(any(v == p for p in it) for v in via)
        exact = path[:len(via)] == via
        ok = exact or (ordered and not self._exp(spec).get("exact", False))
        return ("pass" if ok else "fail"), " > ".join(path) or hops[-1].get("note", ""), " > ".join(path), \
            " > ".join(via)

    def _t_tcp_connect(self, spec, proto="tcp"):
        e = self._engine_for(spec)
        r = e.connect(self._node(spec, "from"), spec.get("to"), int(spec.get("port", 80)), proto=proto)
        want = bool(self._exp(spec).get("open", True))
        return ("pass" if r["open"] == want else "fail"), r["reason"], "open" if r["open"] else "closed", \
            "open" if want else "closed"

    def _t_udp_reach(self, spec):
        return self._t_tcp_connect(spec, "udp")

    # ------------------------------------------------------------------ resilience
    def _scenario_engines(self, name: str):
        if name not in self._scn_cache:
            self._scn_cache[name] = Scenario(self.t, name, self.settings).engines(self.base)
        return self._scn_cache[name]

    def _outage_test(self, spec):
        name = spec["during"]
        src, dst = self._node(spec, "from"), spec.get("to")
        steps = self._scenario_engines(name)
        scn = Scenario(self.t, name, self.settings)
        worst = 0.0
        notes = []
        prev_eng = steps[0][1]
        prev = prev_eng.ping(src, dst, record=False)
        if not prev["ok"]:
            return "fail", f"not reachable before scenario: {prev['reason']}", "unreachable", "reachable"
        down_since = None
        for at, eng, ev in steps[1:]:
            cur = eng.ping(src, dst, record=False)
            if not cur["ok"]:
                if down_since is None:
                    down_since = at
                notes.append(f"t={at:g}s {ev[1]} {ev[2]}: unreachable ({cur['reason']})")
            else:
                if down_since is not None:
                    out = at - down_since + self._convergence(prev_eng, eng, prev, cur, ev)
                    worst = max(worst, out)
                    notes.append(f"t={at:g}s {ev[1]} {ev[2]}: restored after {out:.3f}s")
                    down_since = None
                else:
                    c = self._convergence(prev_eng, eng, prev, cur, ev)
                    worst = max(worst, c)
                    notes.append(f"t={at:g}s {ev[1]} {ev[2]}: {c * 1000:.0f} ms outage")
            prev_eng, prev = eng, cur
        if down_since is not None:
            worst = float("inf")
            notes.append(f"never restored before scenario end ({scn.end:g}s)")
        lim = parse_time_ms(self._exp(spec).get("max_outage", "1s")) / 1000.0
        meas = "never restored" if worst == float("inf") else f"{worst * 1000:.0f} ms"
        return ("pass" if worst <= lim else "fail"), "; ".join(notes), meas, f"<= {lim * 1000:g} ms"

    def _convergence(self, old: Engine, new: Engine, oldp: dict, newp: dict, ev) -> float:
        """Estimate the forwarding outage caused by one timeline event."""
        if ev is None:
            return 0.0
        act, tgt = ev[1], ev[2]
        used_links, used_nodes = set(), set()
        for w in (oldp.get("request"), oldp.get("reply")):
            if w:
                used_links.update(w.links)
                used_nodes.update(w.l3_path)
                used_nodes.update(self.t.links[i].a_node for i in w.links)
                used_nodes.update(self.t.links[i].b_node for i in w.links)
        failed_link = self.t.link_by_name(tgt) if act in ("link_down",) else None
        failed_node = tgt if act == "node_down" else None
        hit = (failed_link is not None and failed_link.idx in used_links) or (failed_node in used_nodes)
        if act in ("link_up", "node_up"):
            # make-before-break when the network re-converges onto a restored element
            return 0.0
        if not hit:
            return 0.0
        tm = self.timers
        cands = [0.0]
        # first-hop redundancy
        if old.vips != new.vips:
            for vip, own in old.vips.items():
                if new.vips.get(vip) != own:
                    adv = 1.0
                    prio = 100
                    for (n, ifn, g), st in old.vrrp_state.items():
                        if st["vip"] == vip and st["state"] == "BACKUP" and new.vips.get(vip, (None,))[0] == n:
                            adv, prio = st["advert"], st["priority"]
                    skew = ((256 - prio) / 256.0) * adv
                    cands.append(3 * adv + skew)
        # layer 2 re-convergence
        old_l2 = set(oldp["request"].links) if oldp.get("request") else set()
        new_l2 = set(newp["request"].links) if newp.get("request") else set()
        if failed_link is not None and failed_link.idx in old_l2:
            an, bn = self.t.nodes[failed_link.a_node], self.t.nodes[failed_link.b_node]
            if an.bridges and bn.bridges:
                if self._in_ring(failed_link):
                    cands.append(tm["g8032_s"])
                else:
                    mode = (an.stp or {}).get("mode", "rstp")
                    cands.append(tm["stp_s"] if mode == "stp" else tm["rstp_s"])
        # layer 3 re-convergence
        op, np_ = oldp["request"].l3_path, newp["request"].l3_path
        if op != np_ or (failed_link is not None and failed_link.idx in old_l2 and
                         not (self.t.nodes[failed_link.a_node].bridges and self.t.nodes[failed_link.b_node].bridges)):
            k = 0
            while k < min(len(op), len(np_)) and op[k] == np_[k]:
                k += 1
            div = op[k - 1] if k > 0 else op[0]
            dst = oldp["dst_ip"]
            r = old.lookup(div, dst)
            proto = r.proto if r else "static"
            direct = failed_link is not None and failed_link.touches(div)
            if failed_node is not None:
                direct = any(l.touches(div) and l.touches(failed_node) for l in self.t.links)
            bfd = False
            for nb in ((self.t.nodes[div].routing or {}).get("bgp") or {}).get("neighbors", []) or []:
                bfd = bfd or bool(nb.get("bfd"))
            if proto in ("ospf",):
                cands.append(tm["ospf_direct_s"] if direct else (tm["bfd_s"] if bfd else tm["ospf_dead_s"]))
            elif proto == "isis":
                cands.append(tm["isis_direct_s"] if direct else (tm["bfd_s"] if bfd else tm["isis_hold_s"]))
            elif proto in ("ebgp", "ibgp"):
                cands.append(tm["bgp_direct_s"] if direct else (tm["bfd_s"] if bfd else tm["bgp_hold_s"]))
            elif proto in ("connected", "static", "default-cloud", "dhcp"):
                cands.append(0.0)
        return max(cands)

    def _in_ring(self, link) -> bool:
        for s in self.t.services.values():
            prot = (s or {}).get("protection") or {}
            if prot.get("type") in ("g8032", "erps"):
                owner = str(prot.get("rpl_owner", "")).split(":")[0]
                site = self.t.nodes.get(owner).site if owner in self.t.nodes else None
                if site and self.t.nodes[link.a_node].site == site and self.t.nodes[link.b_node].site == site:
                    return True
        return False

    # ------------------------------------------------------------------ performance
    def _link_or_path(self, spec):
        if spec.get("link"):
            l = self.t.link_by_name(str(spec["link"]))
            if l is None:
                raise KeyError(f"unknown link '{spec['link']}'")
            e = self._engine_for(spec)
            if not e.link_up(l):
                return None, "link is down"
            p = e.link_profile(l)
            return {"bw": p.bandwidth, "delay": e.link_delay(l), "jitter": p.jitter_ms, "loss": p.loss}, ""
        e = self._engine_for(spec)
        r = e.ping(self._node(spec, "from"), spec.get("to"), record=False)
        if not r["ok"]:
            return None, r["reason"]
        w = r["request"]
        return {"bw": w.min_bw, "delay": w.delay_ms, "jitter": w.jitter_ms, "loss": w.loss}, ""

    def _t_rfc2544_throughput(self, spec):
        p, why = self._link_or_path(spec)
        if p is None:
            return "fail", why, "0", ""
        sizes = [int(x) for x in (spec.get("frame_sizes") or [64, 128, 256, 512, 1024, 1280, 1518])]
        rows = []
        # RFC 2544: max frame rate = line rate / ((frame + 20 bytes preamble+IFG) * 8); throughput reported
        # as payload L2 frame rate * frame size, then reduced by the modelled loss
        worst = None
        for fs in sizes:
            fps = p["bw"] / ((fs + 20) * 8)
            thr = fps * fs * 8 * (1 - p["loss"])
            rows.append(f"{fs}B: {fmt_bw(thr)} ({fps:,.0f} fps)")
            worst = thr if worst is None else min(worst, thr)
        want = parse_bandwidth(self._exp(spec).get("min", "0bps"))
        return ("pass" if worst >= want else "fail"), "; ".join(rows), fmt_bw(worst), f">= {fmt_bw(want)}"

    def _t_rfc2544_latency(self, spec):
        p, why = self._link_or_path(spec)
        if p is None:
            return "fail", why, "", ""
        want = parse_time_ms(self._exp(spec).get("max", "10ms"))
        return ("pass" if p["delay"] <= want else "fail"), f"one-way {p['delay']:.3f} ms", \
            f"{p['delay']:.3f} ms", f"<= {want:g} ms"

    def _t_twamp(self, spec):
        p, why = self._link_or_path(spec)
        if p is None:
            return "fail", why, "", ""
        st = self.compute.sample_path(p["delay"], p["jitter"], p["loss"], self.samples, self.seed)
        exp = self._exp(spec)
        ok = True
        parts = []
        if exp.get("one_way_max"):
            lim = parse_time_ms(exp["one_way_max"])
            ok &= (st["p99"] or 1e9) <= lim
            parts.append(f"p99 one-way {st['p99']:.2f} ms (<= {lim:g})")
        if exp.get("jitter_max"):
            lim = parse_time_ms(exp["jitter_max"])
            ok &= (st["jitter"] or 0) <= lim
            parts.append(f"IPDV {st['jitter']:.2f} ms (<= {lim:g})")
        if exp.get("loss_max"):
            lim = parse_percent(exp["loss_max"])
            ok &= st["loss"] <= lim
            parts.append(f"loss {st['loss'] * 100:.3f}%")
        return ("pass" if ok else "fail"), f"{st['n']} samples on {st['device']}: " + ", ".join(parts), \
            f"mean {st['mean']:.2f} ms, p99 {st['p99']:.2f} ms, IPDV {st['jitter']:.2f} ms", \
            ", ".join(f"{k} {v}" for k, v in exp.items())

    def _t_y1564(self, spec):
        sid = spec.get("service")
        svc = (self.t.services or {}).get(sid)
        if not svc:
            raise KeyError(f"unknown service '{sid}'")
        members = [str(m) for m in (svc.get("members") or svc.get("attachments") or [])]
        if len(members) < 2:
            raise ValueError("service needs at least two members")
        e = self._engine_for(spec)
        cir = parse_bandwidth(svc.get("cir", "0bps"), 0)
        exp = self._exp(spec)
        worst = {"bw": float("inf"), "delay": 0.0, "jitter": 0.0, "loss": 0.0}
        pairs = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                na, ia = members[a].split(":", 1)
                nb, ib = members[b].split(":", 1)
                va = self._uni_vertex(e, na, ia)
                vb = self._uni_vertex(e, nb, ib)
                path = e.l2_path(va, vb) if va and vb else None
                if path is None:
                    return "fail", f"no EVC path {members[a]} <-> {members[b]}", "down", "up"
                bw = min([s["edge"]["bw"] for s in path if s["edge"]["link"] is not None] or [1e10])
                d = sum(s["edge"]["delay"] for s in path)
                j = sum(s["edge"]["jitter"] ** 2 for s in path) ** 0.5
                lo = 1.0
                for s in path:
                    lo *= 1 - s["edge"]["loss"]
                worst = {"bw": min(worst["bw"], bw), "delay": max(worst["delay"], d),
                         "jitter": max(worst["jitter"], j), "loss": max(worst["loss"], 1 - lo)}
                pairs.append(f"{members[a]}<->{members[b]} {d:.3f} ms")
        st = self.compute.sample_path(worst["delay"], worst["jitter"], worst["loss"], self.samples, self.seed)
        ok = True
        msgs = [f"CIR {fmt_bw(cir)} vs capacity {fmt_bw(worst['bw'])}"]
        if exp.get("cir") == "met":
            ok &= worst["bw"] >= cir
        if exp.get("flr_max"):
            ok &= st["loss"] <= parse_percent(exp["flr_max"])
            msgs.append(f"FLR {st['loss'] * 100:.4f}%")
        if exp.get("ftd_max"):
            ok &= (st["p99"] or 0) <= parse_time_ms(exp["ftd_max"])
            msgs.append(f"FTD p99 {st['p99']:.3f} ms")
        if exp.get("fdv_max"):
            ok &= (st["jitter"] or 0) <= parse_time_ms(exp["fdv_max"])
            msgs.append(f"FDV {st['jitter']:.3f} ms")
        return ("pass" if ok else "fail"), "; ".join(msgs + pairs[:3]), \
            f"FTD {worst['delay']:.3f} ms, capacity {fmt_bw(worst['bw'])}", \
            ", ".join(f"{k} {v}" for k, v in exp.items())

    @staticmethod
    def _uni_vertex(e: Engine, node: str, ifn: str):
        n = e.t.nodes.get(node)
        if not n:
            return None
        ifc = n.ifaces.get(ifn)
        if ifc is None:
            return None
        if n.bridges and ifc.mode in ("access", "trunk"):
            return ("br", node, ifc.vlan if ifc.mode == "access" else ifc.native)
        return ("ep", node, ifn)


# ---------------------------------------------------------------------- reports
def to_json(results: List[TestResult], topo_name: str = "") -> str:
    return json.dumps({"topology": topo_name, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "summary": summarize(results),
                       "results": [r.__dict__ for r in results]}, indent=2, default=str)


def summarize(results: List[TestResult]) -> dict:
    out = {"total": len(results)}
    for k in ("pass", "fail", "error", "skip"):
        out[k] = sum(1 for r in results if r.status == k)
    return out


def to_junit(results: List[TestResult], suite: str = "packetlab") -> str:
    s = summarize(results)
    ts = ET.Element("testsuite", name=suite, tests=str(s["total"]), failures=str(s["fail"]),
                    errors=str(s["error"]), skipped=str(s["skip"]),
                    time=f"{sum(r.duration_ms for r in results) / 1000:.3f}")
    for r in results:
        tc = ET.SubElement(ts, "testcase", classname=f"{suite}.{r.type}", name=r.id, time=f"{r.duration_ms / 1000:.3f}")
        if r.status == "fail":
            ET.SubElement(tc, "failure", message=r.detail[:200]).text = \
                f"measured: {r.measured}\nexpected: {r.expected}\n{r.detail}"
        elif r.status == "error":
            ET.SubElement(tc, "error", message=r.detail[:200]).text = r.detail
        elif r.status == "skip":
            ET.SubElement(tc, "skipped", message=r.detail)
        else:
            ET.SubElement(tc, "system-out").text = r.detail
    return ET.tostring(ts, encoding="unicode")
