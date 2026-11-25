"""Device consoles: Cisco IOS-style CLI for network devices and a Windows-style prompt for hosts.

Every configuration command edits the shared topology through the Lab, so the
canvas, the YAML editor and every other console update immediately.
Abbreviations work as on real gear (``sh ip int br``, ``conf t``, ``int gi0/1``).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Callable, List, Optional

from .engine import Engine, _mac
from .lab import Lab
from .model import fmt_bw

WORDS = [
    "show", "running-config", "startup-config", "interface", "interfaces", "ip", "route", "brief", "vlan",
    "spanning-tree", "mac", "address-table", "arp", "ospf", "neighbor", "bgp", "summary", "vrrp", "standby",
    "dhcp", "binding", "lldp", "cdp", "neighbors", "version", "trunk", "configure", "terminal", "enable",
    "disable", "exit", "end", "ping", "traceroute", "hostname", "shutdown", "no", "switchport", "mode",
    "access", "allowed", "native", "description", "address", "helper-address", "access-group", "router",
    "network", "remote-as", "area", "passive-interface", "priority", "write", "memory", "copy", "do", "help",
    "isis", "mpls", "ldp", "tunnel", "clear", "protocols", "history", "logging", "redistribute", "connected",
    "static", "default-information", "originate", "name", "mask", "tunnels", "nat", "translations", "lease",
]

HOST_HELP = """Commands:
  ipconfig [/all]          show IP configuration
  ping <host|ip>           send ICMP echo (4 packets)
  tracert <host|ip>        trace the route
  arp -a                   show ARP cache
  nslookup <name>          resolve a name with the DNS server
  ipconfig /renew          request a new DHCP lease
  cls | help | exit"""

IOS_HELP = """Exec commands (abbreviations accepted):
  enable / disable / configure terminal / end / exit
  show running-config | ip interface brief | ip route | vlan brief | interfaces trunk
  show spanning-tree | mac address-table | arp | ip ospf neighbor | isis neighbors
  show ip bgp summary | ip bgp | vrrp brief | ip dhcp binding | lldp neighbors | mpls ldp neighbor
  show ip nat translations | version | history
  ping <ip|host> [source <if>] | traceroute <ip|host>
Configuration (configure terminal):
  hostname <name> | vlan <id> [name <n>] | no vlan <id> | interface <name>
  ip route <prefix> <mask> <next-hop> | no ip route ...
  router ospf <id>: network <ip> <wildcard> area <a>, passive-interface <if>, redistribute <proto>
  router bgp <asn>: neighbor <ip> remote-as <asn>, network <ip> mask <mask>
Interface mode:
  ip address <ip> <mask> | ip address dhcp | no ip address | [no] shutdown | description <text>
  switchport mode access|trunk | switchport access vlan <id> | switchport trunk allowed vlan <list>
  switchport trunk native vlan <id> | ip helper-address <ip> | ip access-group <acl> in|out
  vrrp <group> ip <vip> | vrrp <group> priority <n>"""


ALIASES = {"en": "enable", "ena": "enable", "conf": "configure", "config": "configure", "t": "terminal",
           "term": "terminal", "sh": "show", "int": "interface", "br": "brief", "nei": "neighbor",
           "run": "running-config", "start": "startup-config", "span": "spanning-tree",
           "wr": "write", "sw": "switchport", "shut": "shutdown", "desc": "description", "tr": "traceroute",
           "trace": "traceroute", "ex": "exit", "di": "disable", "dis": "disable", "pri": "priority",
           "vl": "vlan", "acc": "access"}


def expand(tokens: List[str]) -> List[str]:
    out = []
    for t in tokens:
        low = t.lower()
        if low in ALIASES:
            out.append(ALIASES[low])
            continue
        if low in WORDS or not re.fullmatch(r"[a-z\-]+", low):
            out.append(low if low in WORDS else t)
            continue
        cands = [w for w in WORDS if w.startswith(low)]
        if len(cands) == 1:
            out.append(cands[0])
        elif cands:
            out.append(sorted(cands, key=len)[0])
        else:
            out.append(t)
    return out


def mask_to_len(mask: str) -> int:
    return ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen


class Console:
    def __init__(self, lab: Lab, node: str, write: Optional[Callable[[str], None]] = None):
        self.lab = lab
        self.node = node
        self.mode = "user"           # user | priv | config | if | router-ospf | router-bgp | vlan
        self.ctx: Optional[str] = None
        self.history: List[str] = []

    # ------------------------------------------------------------------ basics
    @property
    def n(self):
        return self.lab.topo.nodes.get(self.node)

    @property
    def is_host(self) -> bool:
        n = self.n
        return n is not None and n.kind in ("host", "server", "external")

    @property
    def style(self) -> str:
        n = self.n
        return n.cli_style if n else "ios"

    def prompt(self) -> str:
        if self.is_host:
            return "C:\\>"
        if self.style == "junos":
            return f"user@{self.node}{'#' if self.mode != 'user' else '>'} "
        return {"user": f"{self.node}>", "priv": f"{self.node}#", "config": f"{self.node}(config)#",
                "if": f"{self.node}(config-if)#", "router-ospf": f"{self.node}(config-router)#",
                "router-bgp": f"{self.node}(config-router)#", "vlan": f"{self.node}(config-vlan)#"}[self.mode]

    def banner(self) -> str:
        if self.n is None:
            return f"% device {self.node} no longer exists"
        if self.is_host:
            return f"VU3INI PacketLab host console - {self.node}\nType 'help' for commands.\n"
        return (f"VU3INI PacketLab {self.style.upper()}-style CLI - {self.node} ({self.n.kind})\n"
                f"Type '?' or 'help' for commands. Abbreviations work (sh ip int br).\n")

    @property
    def e(self) -> Engine:
        return self.lab.engine

    def run(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        if self.n is None:
            return f"% device {self.node} no longer exists"
        self.history.append(line)
        try:
            if self.is_host:
                return self._host(line)
            if self.style == "junos":
                return self._junos(line)
            return self._ios(line)
        except (ValueError, KeyError, IndexError) as ex:
            return f"% Invalid input: {ex}"

    # ------------------------------------------------------------------ host
    def _host(self, line: str) -> str:
        parts = line.split()
        cmd = parts[0].lower()
        e = self.e
        if cmd in ("help", "?"):
            return HOST_HELP
        if cmd == "cls":
            return "\f"
        if cmd == "exit":
            return "(close the console tab to exit)"
        if cmd in ("ipconfig", "ifconfig", "ip"):
            if len(parts) > 1 and parts[1].lower() == "/renew":
                self.lab._engine = None
                lease = next((v for (n, i), v in self.e.dhcp_leases.items() if n == self.node), None)
                if not lease:
                    return "This host uses a static address."
                return f"DHCP: {lease['reason']}" + (f" {lease['ip']} from {lease['server']}" if lease.get("ip") else "")
            out = [f"Windows IP Configuration ({self.node})", ""]
            for ifn, ifc in self.n.ifaces.items():
                l3 = e.l3.get((self.node, ifn))
                lease = e.dhcp_leases.get((self.node, ifn))
                out.append(f"Ethernet adapter {ifn}:")
                if l3 is None or not l3.up:
                    out.append("   Media State . . . . . . . . . . . : Media disconnected" if l3 is None and not lease
                               else "   IPv4 Address. . . . . . . . . . . : (none)  " +
                               (lease.get("reason", "") if lease else ""))
                else:
                    out.append(f"   IPv4 Address. . . . . . . . . . . : {l3.ip.ip}")
                    out.append(f"   Subnet Mask . . . . . . . . . . . : {l3.ip.netmask}")
                    gw = (lease or {}).get("gateway") or ifc.gateway
                    out.append(f"   Default Gateway . . . . . . . . . : {gw or ''}")
                    if len(parts) > 1 and parts[1].lower() == "/all":
                        out.append(f"   Physical Address. . . . . . . . . : {_mac(self.node, ifn).replace(':', '-').upper()}")
                        out.append(f"   DHCP Enabled. . . . . . . . . . . : {'Yes' if ifc.dhcp else 'No'}")
                        if lease and lease.get("server"):
                            out.append(f"   DHCP Server . . . . . . . . . . . : {lease['server']}")
                            out.append(f"   DNS Servers . . . . . . . . . . . : {lease.get('dns') or ''}")
                out.append("")
            return "\n".join(out)
        if cmd == "ping" and len(parts) > 1:
            return self._ping_out(parts[1], windows=True)
        if cmd in ("tracert", "traceroute") and len(parts) > 1:
            return self._trace_out(parts[1], windows=True)
        if cmd == "arp":
            rows = e.arp_table(self.node)
            return "\n".join([f"Interface: {self.node}", "  Internet Address      Physical Address      Type"] +
                             [f"  {r['ip']:<21} {r['mac'].replace(':', '-'):<21} dynamic" for r in rows])
        if cmd == "nslookup" and len(parts) > 1:
            r = e.dns_resolve(self.node, parts[1])
            if not r["ok"]:
                return f"*** can't find {parts[1]}: {r['reason']}"
            return f"Server:  {r['server']}\n\nName:    {parts[1]}\nAddress: {r['address']}"
        return f"'{parts[0]}' is not recognized as an internal or external command. Type help."

    # ------------------------------------------------------------------ ping/trace output
    def _ping_out(self, target: str, windows: bool = False, source: Optional[str] = None) -> str:
        e = self.e
        r = e.ping(self.node, target, record=False)
        dst = r.get("dst_ip") or target
        cnt = int(self.lab.settings.get("ping_count", 5 if not windows else 4))
        if windows:
            out = [f"Pinging {dst} with 32 bytes of data:"]
            if r["ok"]:
                for _ in range(cnt):
                    out.append(f"Reply from {dst}: bytes=32 time={max(1, round(r['rtt_ms']))}ms TTL={64 - len(r['request'].l3_path) + 1}")
                out.append(f"\nPing statistics for {dst}:\n    Packets: Sent = {cnt}, Received = {cnt}, Lost = 0 (0% loss)")
            else:
                for _ in range(cnt):
                    out.append(f"Request timed out.  ({r['reason']})")
                out.append(f"\nPing statistics for {dst}:\n    Packets: Sent = {cnt}, Received = 0, Lost = {cnt} (100% loss)")
            return "\n".join(out)
        out = ["Type escape sequence to abort.",
               f"Sending {cnt}, 100-byte ICMP Echos to {dst}, timeout is 2 seconds:"]
        if r["ok"]:
            rtt = r["rtt_ms"]
            out.append("!" * cnt)
            out.append(f"Success rate is 100 percent ({cnt}/{cnt}), round-trip min/avg/max = "
                       f"{rtt:.0f}/{rtt:.0f}/{rtt:.0f} ms")
        else:
            out.append("." * cnt)
            out.append(f"Success rate is 0 percent (0/{cnt})")
            out.append(f"% {r['reason']}")
        return "\n".join(out)

    def _trace_out(self, target: str, windows: bool = False) -> str:
        hops = self.e.traceroute(self.node, target)
        out = [("Tracing route to " if windows else "Tracing the route to ") + str(target), ""]
        for h in hops:
            if h["node"] is None:
                out.append(f"  {h['hop']:>2}     *        *        *     {h['note']}")
            else:
                ms = max(1, round(h["rtt_ms"] or 0))
                out.append(f"  {h['hop']:>2} {h['ip']} ({h['node']}) {ms} msec {ms} msec {ms} msec")
        return "\n".join(out)

    # ------------------------------------------------------------------ IOS
    def _ios(self, line: str) -> str:
        if line in ("?", "help"):
            return IOS_HELP
        toks = expand(line.split())
        if toks[0] == "do" and self.mode not in ("user", "priv"):
            return self._exec(toks[1:])
        if self.mode in ("user", "priv"):
            return self._exec(toks)
        if toks[0] == "end":
            self.mode, self.ctx = "priv", None
            return ""
        if toks[0] == "exit":
            if self.mode == "config":
                self.mode = "priv"
            else:
                self.mode, self.ctx = "config", None
            return ""
        if self.mode == "if":
            return self._cfg_if(toks)
        if self.mode == "router-ospf":
            return self._cfg_ospf(toks)
        if self.mode == "router-bgp":
            return self._cfg_bgp(toks)
        if self.mode == "vlan":
            if toks[0] == "name" and len(toks) > 1:
                vid = int(self.ctx)
                self.lab.edit(lambda raw: self.lab.node_raw(self.node).setdefault("vlans", {}).__setitem__(vid, toks[1]),
                              "cli")
                return ""
            self.mode, self.ctx = "config", None
            return self._cfg(toks)
        return self._cfg(toks)

    def _exec(self, t: List[str]) -> str:
        e = self.e
        c = t[0]
        if c == "enable":
            self.mode = "priv"
            return ""
        if c in ("disable", "exit"):
            self.mode = "user"
            return "" if c == "disable" else "(close the console tab to log out)"
        if c == "configure":
            if self.mode == "user":
                return "% Use 'enable' first"
            self.mode = "config"
            return "Enter configuration commands, one per line.  End with 'end'."
        if c == "ping" and len(t) > 1:
            return self._ping_out(t[1])
        if c == "traceroute" and len(t) > 1:
            return self._trace_out(t[1])
        if c in ("write", "copy"):
            if self.lab.path:
                self.lab.save()
                return "Building configuration...\n[OK]  (topology file saved)"
            return "Building configuration...\n[OK]  (use File > Save to store the topology)"
        if c == "clear":
            self.lab._engine = None
            return ""
        if c != "show" or len(t) < 2:
            return "% Unrecognized command. Type ? for help."
        s = t[1:]
        key = " ".join(s)
        if key.startswith("running-config") or key.startswith("startup-config"):
            return self.running_config()
        if key.startswith("version"):
            return (f"VU3INI PacketLab simulated {self.n.kind} ({self.n.model or 'generic'})\n"
                    f"{self.node} uptime is {'down' if self.node in self.lab.down_nodes else 'up'}\n"
                    f"Interfaces: {len(self.n.ifaces)}  CLI style: {self.style}")
        if key.startswith("history"):
            return "\n".join(self.history[-30:])
        if key.startswith("ip interface brief") or key.startswith("ip interface") or key.startswith("interfaces brief"):
            return self.ip_int_brief()
        if key.startswith("ip route"):
            return self.show_route()
        if key.startswith("ip ospf neighbor") or key.startswith("ospf neighbor"):
            return self._nbrs(e.ospf_nbrs.get(self.node, []), "OSPF")
        if key.startswith("isis neighbors") or key.startswith("isis neighbor"):
            return self._nbrs(e.isis_nbrs.get(self.node, []), "IS-IS")
        if key.startswith("ip bgp summary") or key.startswith("bgp summary"):
            return self.bgp_summary()
        if key.startswith("ip bgp") or key == "bgp":
            return self.bgp_table()
        if key.startswith("vlan"):
            return self.vlan_brief()
        if key.startswith("interface") and not key.startswith("interfaces"):
            key = "interfaces" + key[len("interface"):]
        if key.startswith("interfaces trunk"):
            return self.trunks()
        if key.startswith("interfaces"):
            return self.interfaces()
        if key.startswith("spanning-tree"):
            return self.spanning_tree()
        if key.startswith("mac"):
            rows = e.mac_table(self.node)
            out = ["          Mac Address Table", "-------------------------------------------",
                   "Vlan    Mac Address       Type        Ports"]
            out += [f"{r['vlan']:<7} {r['mac']:<17} DYNAMIC     {r['port']}" for r in rows]
            out.append(f"Total Mac Addresses for this criterion: {len(rows)}")
            return "\n".join(out)
        if key.startswith("arp") or key.startswith("ip arp"):
            out = ["Protocol  Address          Age (min)  Hardware Addr      Type   Interface"]
            out += [f"Internet  {r['ip']:<16} 0          {r['mac']:<18} ARPA   {r['interface']}"
                    for r in e.arp_table(self.node)]
            return "\n".join(out)
        if key.startswith("vrrp") or key.startswith("standby"):
            out = ["Interface          Grp Pri  State   Virtual IP"]
            for (n, ifn, g), st in sorted(e.vrrp_state.items()):
                if n == self.node:
                    out.append(f"{ifn:<18} {g:<3} {st['priority']:<4} {st['state']:<7} {st['vip']}")
            return "\n".join(out)
        if key.startswith("ip dhcp binding") or key.startswith("dhcp"):
            out = ["IP address       Client           Server"]
            for (n, ifn), lease in e.dhcp_leases.items():
                if lease.get("server") == self.node or lease.get("gateway") and self.node in [
                        x[0] for x in [e.vips.get(lease["gateway"], (None,))]]:
                    out.append(f"{str(lease['ip'].ip) if lease.get('ip') else '-':<16} {n + ':' + ifn:<16} "
                               f"{lease.get('server')}")
            return "\n".join(out)
        if key.startswith("lldp") or key.startswith("cdp"):
            out = ["Device ID        Local Intf     Port ID        Capability"]
            for l in self.lab.topo.links_of(self.node):
                mine = l.a_if if l.a_node == self.node else l.b_if
                on, oi = l.other(self.node, mine)
                if e.link_up(l):
                    cap = self.lab.topo.nodes[on].kind
                    out.append(f"{on:<16} {mine:<14} {oi:<14} {cap}")
            return "\n".join(out)
        if key.startswith("mpls"):
            mp = ((self.n.routing or {}).get("mpls") or {})
            if not mp.get("ldp"):
                return "% MPLS LDP is not enabled on this router"
            out = ["Peer LDP Ident     Local LDP Ident   State"]
            for nb in e.isis_nbrs.get(self.node, []) + e.ospf_nbrs.get(self.node, []):
                pn = self.lab.topo.nodes.get(nb["neighbor"])
                if pn and ((pn.routing or {}).get("mpls") or {}).get("ldp"):
                    out.append(f"{str(nb['address']) + ':0':<18} {self.node:<17} Oper")
            return "\n".join(out)
        if key.startswith("ip nat"):
            nat = (self.n.policies or {}).get("nat")
            if not nat:
                return "% NAT is not configured"
            return (f"NAT overload: inside {', '.join(nat.get('inside', []))} -> outside {nat.get('outside')}\n"
                    "Translations are created on demand by traffic (see the Simulation tab).")
        return "% Unknown show command. Type ? for help."

    # ------------------------------------------------------------------ show helpers
    def ip_int_brief(self) -> str:
        e = self.e
        out = [f"{'Interface':<22} {'IP-Address':<16} {'OK?':<4} {'Method':<7} {'Status':<22} Protocol"]
        for ifn, ifc in sorted(self.n.ifaces.items(), key=lambda kv: _ifsort(kv[0])):
            l3 = e.l3.get((self.node, ifn))
            ip = str(l3.ip.ip) if l3 else ("dhcp" if ifc.dhcp else "unassigned")
            method = "manual" if ifc.raw.get("ipv4") not in (None, "auto", "dhcp") else (
                "auto" if ifc.raw.get("ipv4") == "auto" else ("DHCP" if ifc.dhcp else "unset"))
            if ifc.shutdown:
                st, pr = "administratively down", "down"
            else:
                up = l3.up if l3 else self._port_up(ifn)
                st, pr = ("up", "up") if up else ("down", "down")
            out.append(f"{ifn:<22} {ip:<16} {'YES':<4} {method:<7} {st:<22} {pr}")
        return "\n".join(out)

    def _port_up(self, ifn: str) -> bool:
        for l in self.lab.topo.links_of(self.node):
            if (l.a_node, l.a_if) == (self.node, ifn) or (l.b_node, l.b_if) == (self.node, ifn):
                return self.e.link_up(l)
        lag = [m for m, g in self.n.member_of.items() if g == ifn]
        return any(self._port_up(m) for m in lag)

    def show_route(self) -> str:
        e = self.e
        out = ["Codes: L - local, C - connected, S - static, O - OSPF, i - IS-IS, B - BGP, * - candidate default",
               ""]
        defs = [r for r in e.rib.get(self.node, []) if r.prefix.prefixlen == 0]
        out.append(f"Gateway of last resort is {defs[0].nexthop} to network 0.0.0.0" if defs
                   else "Gateway of last resort is not set")
        out.append("")
        for r in sorted(e.rib.get(self.node, []), key=lambda r: (int(r.prefix.network_address), r.prefix.prefixlen)):
            code = r.code()
            if r.proto == "connected":
                out.append(f"{code:<4} {str(r.prefix):<20} is directly connected, {r.out_if}")
            else:
                oif, nh = e._resolve(self.node, r)
                out.append(f"{code:<4} {str(r.prefix):<20} [{r.ad}/{int(r.metric)}] via {r.nexthop}"
                           + (f", {oif}" if oif else "") + (f"   ({r.detail})" if r.detail else ""))
        return "\n".join(out)

    def _nbrs(self, nb: list, proto: str) -> str:
        if not nb:
            return f"% No {proto} neighbours (is {proto} enabled on this device?)"
        out = [f"{'Neighbor':<16} {'State':<20} {'Address':<16} Interface"]
        out += [f"{x['neighbor']:<16} {x['state']:<20} {str(x['address']):<16} {x['interface']}" for x in nb]
        return "\n".join(out)

    def bgp_summary(self) -> str:
        e = self.e
        cfg = (self.n.routing or {}).get("bgp")
        if not cfg:
            return "% BGP not active"
        out = [f"BGP router identifier {self.lab.topo.resolve_host(self.node)}, local AS number {cfg.get('asn')}",
               "", f"{'Neighbor':<16} {'AS':<7} {'Up/Down':<10} State/PfxRcd"]
        for s in e.bgp_sessions.get(self.node, []):
            state = str(s["rcvd"]) if s["state"] == "Established" else s["state"]
            out.append(f"{str(s['peer']):<16} {s['remote_as']:<7} {'00:10:00' if s['state'] == 'Established' else 'never':<10} "
                       f"{state}" + (f"   ({s['reason']})" if s['reason'] else ""))
        return "\n".join(out)

    def bgp_table(self) -> str:
        e = self.e
        best = e.bgp_best.get(self.node)
        if best is None:
            return "% BGP not active"
        out = ["   Network            Next Hop         LocPrf Path"]
        for pfx, p in sorted(best.items(), key=lambda kv: int(kv[0].network_address)):
            nh = str(p["nh"]) if p["nh"] else "0.0.0.0"
            out.append(f"*> {str(pfx):<18} {nh:<16} {p['local_pref']:<6} {' '.join(map(str, p['as_path']))} i")
        return "\n".join(out)

    def vlan_brief(self) -> str:
        if not self.n.bridges:
            return "% This device has no VLAN database"
        out = [f"{'VLAN':<5} {'Name':<20} {'Status':<10} Ports"]
        for v, name in sorted(self.n.vlans.items()):
            ports = [ifn for ifn, ifc in sorted(self.n.ifaces.items(), key=lambda kv: _ifsort(kv[0]))
                     if ifc.mode == "access" and ifc.vlan == v and ifn not in self.n.member_of]
            out.append(f"{v:<5} {name:<20} {'active':<10} {', '.join(ports)}")
        return "\n".join(out)

    def trunks(self) -> str:
        out = [f"{'Port':<12} {'Mode':<8} {'Native':<7} Vlans allowed"]
        for ifn, ifc in sorted(self.n.ifaces.items(), key=lambda kv: _ifsort(kv[0])):
            if ifc.mode == "trunk" and ifn not in self.n.member_of:
                allowed = "1-4094" if ifc.allowed is None else ",".join(map(str, sorted(ifc.allowed)))
                out.append(f"{ifn:<12} {'on':<8} {ifc.native:<7} {allowed}")
        return "\n".join(out)

    def interfaces(self) -> str:
        out = []
        for ifn, ifc in sorted(self.n.ifaces.items(), key=lambda kv: _ifsort(kv[0])):
            l3 = self.e.l3.get((self.node, ifn))
            up = (l3.up if l3 else self._port_up(ifn)) and not ifc.shutdown
            bw = l3.bw if l3 else self.e._port_bw(self.n, ifn)
            out.append(f"{ifn} is {'up' if up else 'down'}, line protocol is {'up' if up else 'down'}")
            out.append(f"  Hardware address is {_mac(self.node, ifn)}" + (f", Description: {ifc.description}"
                                                                          if ifc.description else ""))
            if l3:
                out.append(f"  Internet address is {l3.ip}")
            out.append(f"  MTU 1500 bytes, BW {fmt_bw(bw)}, mode {ifc.mode}")
        return "\n".join(out)

    def spanning_tree(self) -> str:
        rows = self.e.stp_summary(self.node)
        if not rows:
            return "% No spanning tree instance exists on this device"
        out = []
        for r in rows:
            out.append(f"VLAN{r['vlan']:04d}  ({r['mode'].upper()})")
            out.append(f"  Root ID    {r['root']}" + ("  (This bridge is the root)" if r["is_root"] else
                                                       f"  Cost {r['cost']}  Port {r['root_port']}"))
            out.append(f"  Bridge ID  Priority {r['bridge_priority']}")
            out.append("  Interface        Role  Sts")
            for p in r["ports"]:
                role = {"root": "Root", "designated": "Desg", "alternate": "Altn"}.get(p["role"], p["role"])
                sts = "BLK" if p["role"] == "alternate" else "FWD"
                out.append(f"  {p['port']:<16} {role:<5} {sts}")
            out.append("")
        return "\n".join(out)

    def running_config(self) -> str:
        n = self.n
        out = ["Building configuration...", "", "!", f"hostname {self.node}", "!"]
        for v, name in sorted(n.vlans.items()):
            if v != 1:
                out += [f"vlan {v}", f" name {name}", "!"]
        for ifn, ifc in sorted(n.ifaces.items(), key=lambda kv: _ifsort(kv[0])):
            out.append(f"interface {ifn}")
            if ifc.description:
                out.append(f" description {ifc.description}")
            if n.member_of.get(ifn):
                out.append(f" channel-group {re.sub(r'[^0-9]', '', n.member_of[ifn]) or 1} mode active")
            elif ifc.mode == "access":
                out.append(" switchport mode access")
                out.append(f" switchport access vlan {ifc.vlan}")
                if ifc.voice_vlan:
                    out.append(f" switchport voice vlan {ifc.voice_vlan}")
                if ifc.portfast:
                    out.append(" spanning-tree portfast")
            elif ifc.mode == "trunk":
                out.append(" switchport mode trunk")
                if ifc.allowed is not None:
                    out.append(f" switchport trunk allowed vlan {','.join(map(str, sorted(ifc.allowed)))}")
                if ifc.native != 1:
                    out.append(f" switchport trunk native vlan {ifc.native}")
            else:
                if n.bridges and ifc.svi_vlan is None and not ifc.loopback:
                    out.append(" no switchport")
                l3 = self.e.l3.get((self.node, ifn))
                if ifc.dhcp:
                    out.append(" ip address dhcp")
                elif l3 is not None:
                    out.append(f" ip address {l3.ip.ip} {l3.ip.netmask}")
                for h in ifc.helper:
                    out.append(f" ip helper-address {h}")
                if ifc.acl_in:
                    out.append(f" ip access-group {ifc.acl_in} in")
                if ifc.acl_out:
                    out.append(f" ip access-group {ifc.acl_out} out")
                for g in (n.routing or {}).get("vrrp", []) or []:
                    if g.get("interface") == ifn:
                        out.append(f" vrrp {g.get('group')} ip {g.get('vip')}")
                        out.append(f" vrrp {g.get('group')} priority {g.get('priority', 100)}")
                if ifc.tunnel:
                    out.append(f" tunnel destination {ifc.tunnel.get('peer')}  ! {ifc.tunnel.get('type', 'gre')}")
            out.append(" shutdown" if ifc.shutdown else " no shutdown")
            out.append("!")
        o = (n.routing or {}).get("ospf")
        if isinstance(o, dict):
            out.append(f"router ospf {o.get('process', 1)}")
            for area, lst in (o.get("areas") or {}).items():
                for ifn in lst if isinstance(lst, list) else []:
                    l3 = self.e.l3.get((self.node, ifn))
                    if l3:
                        out.append(f" network {l3.ip.ip} 0.0.0.0 area {area}")
            for p in o.get("passive", []) or []:
                out.append(f" passive-interface {p}")
            for r in o.get("redistribute", []) or []:
                out.append(f" redistribute {r} subnets")
            if o.get("default_originate"):
                out.append(" default-information originate")
            out.append("!")
        b = (n.routing or {}).get("bgp")
        if isinstance(b, dict):
            out.append(f"router bgp {b.get('asn')}")
            for nb in b.get("neighbors", []) or []:
                out.append(f" neighbor {nb.get('peer')} remote-as {nb.get('remote_as')}")
                if nb.get("bfd"):
                    out.append(f" neighbor {nb.get('peer')} fall-over bfd")
            for p in b.get("networks", []) or []:
                net = ipaddress.ip_network(str(p), strict=False)
                out.append(f" network {net.network_address} mask {net.netmask}")
            out.append("!")
        for s in (n.routing or {}).get("static", []) or []:
            net = ipaddress.ip_network(str(s.get("prefix")), strict=False)
            out.append(f"ip route {net.network_address} {net.netmask} {s.get('next_hop', s.get('interface', ''))}")
        out.append("end")
        return "\n".join(out)

    # ------------------------------------------------------------------ configuration
    def _find_if(self, name: str) -> str:
        n = self.n
        low = name.lower()
        for ifn in n.ifaces:
            if ifn.lower() == low:
                return ifn
        m = re.fullmatch(r"([a-z\-]+)\s*([0-9/\.]+)", low)
        if m:
            pre, num = m.groups()
            cands = [ifn for ifn in n.ifaces if re.sub(r"^[A-Za-z\-]+", "", ifn) == num and
                     (ifn.lower().startswith(pre) or pre.startswith(ifn.lower().rstrip("0123456789/.")[:2]))]
            if len(cands) == 1:
                return cands[0]
            # e.g. GigabitEthernet0/1 -> Gi0/1 ; Vlan10 ; Loopback0 -> Lo0
            short = {"gigabitethernet": "Gi", "tengigabitethernet": "Te", "fastethernet": "Fa", "loopback": "Lo",
                     "port-channel": "Po", "vlan": "Vlan", "tunnel": "Tun", "ethernet": "eth"}
            for k, v in short.items():
                if k.startswith(pre) and len(pre) >= 2:
                    return f"{v}{num}"
        return name

    def _cfg(self, t: List[str]) -> str:
        c = t[0]
        lab = self.lab
        if c == "hostname" and len(t) > 1:
            old = self.node
            lab.rename_node(old, t[1])
            self.node = t[1]
            return ""
        if c == "interface" and len(t) > 1:
            name = self._find_if("".join(t[1:]))
            self.mode, self.ctx = "if", name
            if name not in self.n.ifaces:
                lab.edit(lambda raw: lab.iface_raw(self.node, name), "cli")
            return ""
        if c == "vlan" and len(t) > 1:
            vid = int(t[1])
            if not self.n.bridges:
                return "% VLANs exist only on switches"
            lab.edit(lambda raw: lab.node_raw(self.node).setdefault("vlans", {}).setdefault(vid, f"VLAN{vid:04d}"),
                     "cli")
            self.mode, self.ctx = "vlan", str(vid)
            return ""
        if c == "no" and len(t) > 2 and t[1] == "vlan":
            vid = int(t[2])
            lab.edit(lambda raw: (lab.node_raw(self.node).get("vlans") or {}).pop(vid, None), "cli")
            return ""
        if c == "ip" and len(t) > 4 and t[1] == "route":
            pfx = ipaddress.ip_network(f"{t[2]}/{mask_to_len(t[3])}", strict=False)
            nh = t[4]
            lab.edit(lambda raw: lab.node_raw(self.node).setdefault("routing", {}).setdefault("static", []).append(
                {"prefix": str(pfx), "next_hop": nh}), "cli")
            return ""
        if c == "no" and len(t) > 4 and t[1] == "ip" and t[2] == "route":
            pfx = str(ipaddress.ip_network(f"{t[3]}/{mask_to_len(t[4])}", strict=False))

            def fn(raw):
                r = lab.node_raw(self.node).setdefault("routing", {})
                r["static"] = [s for s in r.get("static", []) or [] if str(s.get("prefix")) != pfx]
            lab.edit(fn, "cli")
            return ""
        if c == "router" and len(t) > 2 and t[1] == "ospf":
            lab.edit(lambda raw: lab.node_raw(self.node).setdefault("routing", {}).setdefault(
                "ospf", {"process": int(t[2]), "areas": {}}), "cli")
            self.mode = "router-ospf"
            return ""
        if c == "router" and len(t) > 2 and t[1] == "bgp":
            asn = int(t[2])
            lab.edit(lambda raw: lab.node_raw(self.node).setdefault("routing", {}).setdefault(
                "bgp", {"asn": asn, "neighbors": [], "networks": []}), "cli")
            self.mode = "router-bgp"
            return ""
        if c == "no" and len(t) > 1 and t[1] == "router" and len(t) > 2:
            proto = t[2]
            lab.edit(lambda raw: (lab.node_raw(self.node).get("routing") or {}).pop(proto, None), "cli")
            return ""
        return "% Invalid input detected. Type ? for help."

    def _cfg_if(self, t: List[str]) -> str:
        lab, ifn = self.lab, self.ctx
        neg = t[0] == "no"
        if neg:
            t = t[1:]
        if not t:
            return "% Incomplete command."

        def setk(**kv):
            def fn(raw):
                d = lab.iface_raw(self.node, ifn)
                for k, v in kv.items():
                    if v is None:
                        d.pop(k, None)
                    else:
                        d[k] = v
            lab.edit(fn, "cli")
        c = t[0]
        if c == "shutdown":
            setk(shutdown=None if neg else True)
            return ""
        if c == "description":
            setk(description=None if neg else " ".join(t[1:]))
            return ""
        if c == "ip" and len(t) > 1 and t[1] == "address":
            if neg:
                setk(ipv4=None)
            elif len(t) > 2 and t[2].lower() == "dhcp":
                setk(ipv4="dhcp")
            elif len(t) > 3:
                ip = ipaddress.ip_interface(f"{t[2]}/{mask_to_len(t[3])}")
                setk(ipv4=str(ip), mode="routed" if self.n.bridges and not ifn.lower().startswith(("vlan", "lo"))
                     else None)
            else:
                return "% Incomplete command."
            return ""
        if c == "ip" and len(t) > 2 and t[1] == "helper-address":
            setk(helper=None if neg else t[2])
            return ""
        if c == "ip" and len(t) > 3 and t[1] == "access-group":
            key = "acl_in" if t[3] == "in" else "acl_out"
            setk(**{key: None if neg else t[2]})
            return ""
        if c == "switchport":
            if not self.n.bridges:
                return "% switchport commands exist only on switches"
            if len(t) > 2 and t[1] == "mode":
                setk(mode=t[2] if t[2] in ("access", "trunk") else "access")
                return ""
            if len(t) > 3 and t[1] == "access" and t[2] == "vlan":
                setk(vlan=int(t[3]), mode="access")
                return ""
            if len(t) > 4 and t[1] == "trunk" and t[2] == "allowed" and t[3] == "vlan":
                setk(allowed_vlans=t[4] if t[4] == "all" else [x for x in t[4].split(",")])
                return ""
            if len(t) > 4 and t[1] == "trunk" and t[2] == "native":
                setk(native_vlan=int(t[4]))
                return ""
            return "% Incomplete switchport command"
        if c == "vrrp" and len(t) > 3:
            g = int(t[1])

            def fn(raw):
                lst = lab.node_raw(self.node).setdefault("routing", {}).setdefault("vrrp", [])
                ent = next((x for x in lst if x.get("group") == g and x.get("interface") == ifn), None)
                if neg:
                    if ent:
                        lst.remove(ent)
                    return
                if ent is None:
                    ent = {"group": g, "interface": ifn, "priority": 100}
                    lst.append(ent)
                if t[2] == "ip":
                    ent["vip"] = t[3]
                elif t[2] == "priority":
                    ent["priority"] = int(t[3])
            lab.edit(fn, "cli")
            return ""
        return "% Invalid input detected. Type ? for help."

    def _cfg_ospf(self, t: List[str]) -> str:
        lab = self.lab
        if t[0] == "network" and len(t) > 4:
            net = ipaddress.ip_network(f"{t[1]}/{_wild_len(t[2])}", strict=False)
            area = t[4]
            names = [ifn for (n, ifn), l3 in self.e.l3.items() if n == self.node and l3.ip.ip in net]

            def fn(raw):
                o = lab.node_raw(self.node).setdefault("routing", {}).setdefault("ospf", {})
                areas = o.setdefault("areas", {})
                lst = areas.setdefault(area, [])
                for x in names:
                    if x not in lst:
                        lst.append(x)
            lab.edit(fn, "cli")
            return f"% matched interfaces: {', '.join(names) or 'none'}"
        if t[0] == "passive-interface" and len(t) > 1:
            ifn = self._find_if(t[1])
            lab.edit(lambda raw: lab.node_raw(self.node)["routing"]["ospf"].setdefault("passive", []).append(ifn),
                     "cli")
            return ""
        if t[0] == "redistribute" and len(t) > 1:
            lab.edit(lambda raw: lab.node_raw(self.node)["routing"]["ospf"].setdefault("redistribute", []).append(t[1]),
                     "cli")
            return ""
        if t[0] == "default-information":
            lab.edit(lambda raw: lab.node_raw(self.node)["routing"]["ospf"].__setitem__("default_originate", True),
                     "cli")
            return ""
        return "% Invalid input detected."

    def _cfg_bgp(self, t: List[str]) -> str:
        lab = self.lab
        if t[0] == "neighbor" and len(t) > 3 and t[2] == "remote-as":
            peer, ras = t[1], int(t[3])

            def fn(raw):
                b = lab.node_raw(self.node)["routing"]["bgp"]
                lst = b.setdefault("neighbors", [])
                lst[:] = [x for x in lst if str(x.get("peer")) != peer]
                lst.append({"peer": peer, "remote_as": ras})
            lab.edit(fn, "cli")
            return ""
        if t[0] == "network" and len(t) > 1:
            plen = mask_to_len(t[3]) if len(t) > 3 and t[2] == "mask" else 24
            net = str(ipaddress.ip_network(f"{t[1]}/{plen}", strict=False))
            lab.edit(lambda raw: lab.node_raw(self.node)["routing"]["bgp"].setdefault("networks", []).append(net),
                     "cli")
            return ""
        if t[0] == "redistribute" and len(t) > 1:
            lab.edit(lambda raw: lab.node_raw(self.node)["routing"]["bgp"].setdefault("redistribute", []).append(t[1]),
                     "cli")
            return ""
        return "% Invalid input detected."

    # ------------------------------------------------------------------ Junos (operational subset)
    def _junos(self, line: str) -> str:
        t = line.split()
        key = " ".join(t).lower()
        if key in ("?", "help"):
            return ("show route | show interfaces terse | show ospf neighbor | show isis adjacency | "
                    "show bgp summary | show vrrp | show configuration | ping <ip> | traceroute <ip>")
        if key.startswith("show route"):
            return self.show_route()
        if key.startswith("show interfaces terse"):
            return self.ip_int_brief()
        if key.startswith("show ospf"):
            return self._nbrs(self.e.ospf_nbrs.get(self.node, []), "OSPF")
        if key.startswith("show isis"):
            return self._nbrs(self.e.isis_nbrs.get(self.node, []), "IS-IS")
        if key.startswith("show bgp"):
            return self.bgp_summary()
        if key.startswith("show vrrp"):
            return self._ios("show vrrp")
        if key.startswith("show configuration"):
            return self.running_config()
        if t[0] == "ping" and len(t) > 1:
            return self._ping_out(t[1])
        if t[0] == "traceroute" and len(t) > 1:
            return self._trace_out(t[1])
        if t[0] in ("configure", "edit"):
            return "Configuration mode is IOS-style in PacketLab: set cli_style: ios on this node, or edit the YAML."
        return "syntax error."


def _wild_len(w: str) -> int:
    inv = ".".join(str(255 - int(x)) for x in w.split("."))
    return mask_to_len(inv)


def _ifsort(name: str):
    nums = [int(x) for x in re.findall(r"\d+", name)]
    return (re.sub(r"[\d/\.]+", "", name), nums)
