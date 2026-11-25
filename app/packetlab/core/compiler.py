"""Topology compiler: NSTopo -> Containerlab, Kathara and per-device FRRouting configs.

The output lets the same design run on real network operating systems
(FRRouting in containers) on a Linux host or WSL2, for emulation mode.
"""
from __future__ import annotations

import ipaddress
import os
import re
from typing import Dict, List, Tuple

import yaml

from .model import Topology

DEFAULT_IMAGES = {
    "router": "quay.io/frrouting/frr:10.2.1",
    "l3switch": "quay.io/frrouting/frr:10.2.1",
    "firewall": "quay.io/frrouting/frr:10.2.1",
    "cloud": "quay.io/frrouting/frr:10.2.1",
    "switch": "ghcr.io/hellt/network-multitool",
    "ap": "ghcr.io/hellt/network-multitool",
    "host": "ghcr.io/hellt/network-multitool",
    "server": "ghcr.io/hellt/network-multitool",
    "external": "ghcr.io/hellt/network-multitool",
}


def _safe(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", name.lower())[:30]


def port_map(t: Topology) -> Dict[Tuple[str, str], str]:
    """Map node:interface -> ethN in link order (containers number their ports eth1..)."""
    counters: Dict[str, int] = {}
    out: Dict[Tuple[str, str], str] = {}
    for l in t.links:
        for n, i in ((l.a_node, l.a_if), (l.b_node, l.b_if)):
            counters[n] = counters.get(n, 0) + 1
            out[(n, i)] = f"eth{counters[n]}"
    return out


def frr_config(t: Topology, node: str, pm: Dict[Tuple[str, str], str]) -> str:
    n = t.nodes[node]
    lines = ["frr defaults datacenter", f"hostname {node}", "service integrated-vtysh-config", "!"]
    ifnames = {}
    for ifn, ifc in n.ifaces.items():
        if n.member_of.get(ifn):
            continue
        if ifc.svi_vlan is not None:
            dev = f"vlan{ifc.svi_vlan}"
        elif ifc.loopback:
            dev = "lo"
        elif ifc.tunnel is not None:
            dev = f"tun{re.sub(r'[^0-9]', '', ifn) or '0'}"
        else:
            dev = pm.get((node, ifn), ifn)
        ifnames[ifn] = dev
        lines.append(f"interface {dev}")
        lines.append(f" description {ifn}")
        if ifc.ip is not None:
            lines.append(f" ip address {ifc.ip}")
        if ifc.shutdown:
            lines.append(" shutdown")
        lines.append("!")
    r = n.routing or {}
    for s in r.get("static", []) or []:
        lines.append(f"ip route {s.get('prefix')} {s.get('next_hop', '')}".rstrip())
    o = r.get("ospf")
    if isinstance(o, dict):
        lines.append("router ospf")
        for area, lst in (o.get("areas") or {}).items():
            for ifn in lst if isinstance(lst, list) else []:
                ifc = n.ifaces.get(ifn)
                if ifc and ifc.ip:
                    lines.append(f" network {ifc.ip.network} area {area}")
        for p in o.get("passive", []) or []:
            if p in ifnames:
                lines.append(f" passive-interface {ifnames[p]}")
        for rd in o.get("redistribute", []) or []:
            lines.append(f" redistribute {rd}")
        if o.get("default_originate"):
            lines.append(" default-information originate always")
        lines.append("!")
    i = r.get("isis")
    if i:
        lines.append("router isis CORE")
        sysid = sum((k + 1) * ord(ch) for k, ch in enumerate(node)) % 10000
        lines.append(f" net 49.0001.0000.0000.{sysid:04d}.00")
        lines.append(" is-type level-2-only")
        lines.append("!")
        for ifn, dev in ifnames.items():
            lst = i.get("interfaces", "all") if isinstance(i, dict) else "all"
            if lst == "all" or ifn in lst:
                lines += [f"interface {dev}", " ip router isis CORE", "!"]
    if (r.get("mpls") or {}).get("ldp"):
        lo = next((c.ip.ip for c in n.ifaces.values() if c.loopback and c.ip), None)
        lines.append("mpls ldp")
        if lo:
            lines.append(f" router-id {lo}")
        lines.append(" address-family ipv4")
        if lo:
            lines.append(f"  discovery transport-address {lo}")
        for ifn, dev in ifnames.items():
            if dev != "lo":
                lines.append(f"  interface {dev}")
        lines += [" exit-address-family", "!"]
    b = r.get("bgp")
    if isinstance(b, dict):
        lines.append(f"router bgp {b.get('asn')}")
        lines.append(" no bgp ebgp-requires-policy")
        for nb in b.get("neighbors", []) or []:
            lines.append(f" neighbor {nb.get('peer')} remote-as {nb.get('remote_as')}")
            if int(nb.get("remote_as", 0)) == int(b.get("asn", -1)):
                lines.append(f" neighbor {nb.get('peer')} next-hop-self")
            if nb.get("bfd"):
                lines.append(f" neighbor {nb.get('peer')} bfd")
        lines.append(" address-family ipv4 unicast")
        for p in b.get("networks", []) or []:
            lines.append(f"  network {p}")
        for rd in b.get("redistribute", []) or []:
            lines.append(f"  redistribute {rd}")
        for nb in b.get("neighbors", []) or []:
            if nb.get("local_pref"):
                lines.append(f"  neighbor {nb.get('peer')} route-map LP{nb.get('local_pref')} in")
        lines += [" exit-address-family", "!"]
        for lp in sorted({nb.get("local_pref") for nb in b.get("neighbors", []) or [] if nb.get("local_pref")}):
            lines += [f"route-map LP{lp} permit 10", f" set local-preference {lp}", "!"]
    if r.get("vrrp"):
        for g in r["vrrp"]:
            dev = ifnames.get(g.get("interface"), g.get("interface"))
            lines += [f"interface {dev}", f" vrrp {g.get('group')} version 3", f" vrrp {g.get('group')} ip {g.get('vip')}",
                      f" vrrp {g.get('group')} priority {g.get('priority', 100)}", "!"]
    lines.append("end")
    return "\n".join(lines) + "\n"


def linux_exec(t: Topology, node: str, pm) -> List[str]:
    """Shell commands that configure a Linux-based node (bridges for switches, IPs for hosts)."""
    n = t.nodes[node]
    cmds: List[str] = []
    if n.bridges:
        cmds.append("ip link add br0 type bridge vlan_filtering 1")
        cmds.append("ip link set br0 up")
        lag_dev = {}
        for k, (lag, members) in enumerate(sorted({g: [m for m, x in n.member_of.items() if x == g]
                                                    for g in set(n.member_of.values())}.items())):
            bond = f"bond{k + 1}"
            lag_dev[lag] = bond
            cmds.append(f"ip link add {bond} type bond mode 802.3ad")
            for m in members:
                if (node, m) in pm:
                    cmds.append(f"ip link set {pm[(node, m)]} down")
                    cmds.append(f"ip link set {pm[(node, m)]} master {bond}")
        for ifn, ifc in n.ifaces.items():
            if ifn in n.member_of:
                continue
            dev = lag_dev.get(ifn) or pm.get((node, ifn))
            if not dev:
                continue
            cmds.append(f"ip link set {dev} master br0")
            cmds.append(f"ip link set {dev} up")
            if ifc.mode == "access":
                cmds.append(f"bridge vlan del dev {dev} vid 1")
                cmds.append(f"bridge vlan add dev {dev} vid {ifc.vlan} pvid untagged")
            elif ifc.mode == "trunk":
                vl = sorted(n.vlans) if ifc.allowed is None else sorted(ifc.allowed)
                for v in vl:
                    if v == ifc.native:
                        cmds.append(f"bridge vlan add dev {dev} vid {v} pvid untagged")
                    else:
                        cmds.append(f"bridge vlan add dev {dev} vid {v}")
        for ifn, ifc in n.ifaces.items():
            if ifc.svi_vlan is not None and ifc.ip is not None:
                v = ifc.svi_vlan
                cmds.append(f"bridge vlan add dev br0 vid {v} self")
                cmds.append(f"ip link add link br0 name vlan{v} type vlan id {v}")
                cmds.append(f"ip addr add {ifc.ip} dev vlan{v}")
                cmds.append(f"ip link set vlan{v} up")
    else:
        for ifn, ifc in n.ifaces.items():
            dev = pm.get((node, ifn))
            if not dev:
                continue
            if ifc.ip is not None:
                cmds.append(f"ip addr add {ifc.ip} dev {dev}")
            elif ifc.dhcp:
                cmds.append(f"udhcpc -i {dev} -b -q || dhclient {dev} || true")
            cmds.append(f"ip link set {dev} up")
            if ifc.gateway is not None:
                cmds.append(f"ip route replace default via {ifc.gateway}")
    return cmds


def to_containerlab(t: Topology, outdir: str, name: str = "", images: Dict[str, str] = None) -> str:
    images = dict(DEFAULT_IMAGES, **(images or {}))
    name = _safe(name or (t.meta or {}).get("name") or "packetlab")
    os.makedirs(outdir, exist_ok=True)
    pm = port_map(t)
    nodes = {}
    for nname, n in t.nodes.items():
        cn = _safe(nname)
        img = n.image or images.get(n.kind, images["host"])
        entry = {"kind": "linux", "image": img}
        if "frr" in img:
            cfg_dir = os.path.join(outdir, "configs", cn)
            os.makedirs(cfg_dir, exist_ok=True)
            with open(os.path.join(cfg_dir, "frr.conf"), "w", encoding="utf-8") as f:
                f.write(frr_config(t, nname, pm))
            with open(os.path.join(cfg_dir, "daemons"), "w", encoding="utf-8") as f:
                f.write(_daemons(n))
            entry["binds"] = [f"configs/{cn}/frr.conf:/etc/frr/frr.conf", f"configs/{cn}/daemons:/etc/frr/daemons"]
            exec_cmds = []
            if n.bridges:
                exec_cmds = linux_exec(t, nname, pm)
            if exec_cmds:
                entry["exec"] = exec_cmds
        else:
            ex = linux_exec(t, nname, pm)
            if ex:
                entry["exec"] = ex
        entry["labels"] = {"packetlab.kind": n.kind, "packetlab.site": n.site or ""}
        nodes[cn] = entry
    links = [{"endpoints": [f"{_safe(l.a_node)}:{pm[(l.a_node, l.a_if)]}", f"{_safe(l.b_node)}:{pm[(l.b_node, l.b_if)]}"]}
             for l in t.links]
    doc = {"name": name, "topology": {"nodes": nodes, "links": links}}
    path = os.path.join(outdir, f"{name}.clab.yml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by VU3INI PacketLab - deploy with: sudo containerlab deploy -t "
                f"{name}.clab.yml\n")
        f.write("# Interface map (PacketLab name -> container port):\n")
        for (n, i), dev in sorted(pm.items()):
            f.write(f"#   {n}:{i} -> {_safe(n)}:{dev}\n")
        yaml.safe_dump(doc, f, sort_keys=False)
    _write_impairments(t, outdir, pm, name)
    return path


def _daemons(n) -> str:
    r = n.routing or {}
    on = {"zebra": True, "staticd": True, "bgpd": bool(r.get("bgp")), "ospfd": bool(r.get("ospf")),
          "isisd": bool(r.get("isis")), "ldpd": bool((r.get("mpls") or {}).get("ldp")), "vrrpd": bool(r.get("vrrp")),
          "bfdd": any(nb.get("bfd") for nb in ((r.get("bgp") or {}).get("neighbors") or []))}
    lines = [f"{k}={'yes' if v else 'no'}" for k, v in on.items()]
    lines += ["vtysh_enable=yes", 'zebra_options="  -A 127.0.0.1 -s 90000000"', 'bgpd_options="   -A 127.0.0.1"',
              'ospfd_options="  -A 127.0.0.1"', 'isisd_options="  -A 127.0.0.1"', 'ldpd_options="   -A 127.0.0.1"',
              'staticd_options="-A 127.0.0.1"', 'vrrpd_options="  -A 127.0.0.1"', 'bfdd_options="   -A 127.0.0.1"']
    return "\n".join(lines) + "\n"


def _write_impairments(t: Topology, outdir: str, pm, name: str) -> None:
    lines = ["#!/bin/sh", "# Apply PacketLab link profiles with tc netem (run after deploy)", "set -e"]
    for l in t.links:
        p = l.profile
        args = [f"delay {l.delay_ms:.3f}ms"]
        if p.jitter_ms:
            args[0] += f" {p.jitter_ms:.3f}ms"
        if p.loss:
            args.append(f"loss {p.loss * 100:.4f}%")
        rate = f"rate {int(p.bandwidth / 1000)}kbit"
        for n, i in ((l.a_node, l.a_if), (l.b_node, l.b_if)):
            lines.append(f"docker exec clab-{name}-{_safe(n)} tc qdisc replace dev {pm[(n, i)]} root netem "
                         f"{' '.join(args)} {rate} || true")
    path = os.path.join(outdir, "apply-impairments.sh")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def to_kathara(t: Topology, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    pm = port_map(t)
    lab = ["# Generated by VU3INI PacketLab - start with: kathara lstart", f'LAB_NAME="{(t.meta or {}).get("name", "packetlab")}"',
           f'LAB_AUTHOR="{(t.meta or {}).get("author", "VU3INI")}"', ""]
    per_node: Dict[str, List[Tuple[int, str]]] = {}
    for k, l in enumerate(t.links):
        dom = f"L{k:03d}"
        for n, i in ((l.a_node, l.a_if), (l.b_node, l.b_if)):
            idx = int(pm[(n, i)][3:]) - 1
            per_node.setdefault(n, []).append((idx, dom))
    for n, lst in sorted(per_node.items()):
        kn = _safe(n)
        for idx, dom in sorted(lst):
            lab.append(f"{kn}[{idx}]={dom}")
        node = t.nodes[n]
        img = "kathara/frr" if node.routes else "kathara/base"
        lab.append(f'{kn}[image]="{img}"')
        lab.append("")
        startup = []
        pm0 = {k: f"eth{int(v[3:]) - 1}" for k, v in pm.items()}
        if node.routes:
            os.makedirs(os.path.join(outdir, kn, "etc", "frr"), exist_ok=True)
            with open(os.path.join(outdir, kn, "etc", "frr", "frr.conf"), "w", encoding="utf-8") as f:
                f.write(frr_config(t, n, pm0))
            with open(os.path.join(outdir, kn, "etc", "frr", "daemons"), "w", encoding="utf-8") as f:
                f.write(_daemons(node))
            if node.bridges:
                startup += linux_exec(t, n, pm0)
            startup.append("systemctl start frr")
        else:
            startup += linux_exec(t, n, pm0)
        with open(os.path.join(outdir, f"{kn}.startup"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(startup) + "\n")
    path = os.path.join(outdir, "lab.conf")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lab) + "\n")
    return path
