## Welcome to VU3INI PacketLab
VU3INI PacketLab lets you design a LAN, MAN or WAN, simulate it, and test it against current industry standards, entirely on your own computer.

**What you can do**
- Draw networks with routers, Layer-3 switches, switches, firewalls, PCs, servers, Wi-Fi access points and an Internet cloud.
- Configure devices from a Cisco IOS-style console, from the Properties panel, or by editing the topology file (YAML).
- Ping, traceroute and connect between any two devices in **Realtime** mode, or step through every frame in **Simulation** mode with a Wireshark-style PDU inspector.
- Run automated tests: addressing, VLANs, OSPF/BGP state, DHCP, DNS, ACLs, failover times during failures, RFC 2544 throughput, ITU-T Y.1564 service activation and TWAMP-style delay and jitter.
- Save captures as .pcapng and open them in Wireshark.
- Export the same design to Containerlab or Kathará and run it on real FRRouting routers through WSL2 or a Linux machine.

**Quick start:** File > Open Sample > *hq-metro-branch*, then press **F5** (Run Tests). All 18 tests should pass. Then open the *03-troubleshooting-lab* sample and fix it.

## The main window
- **Top left:** the VU3INI logo and app name. **Top right:** theme toggle (dark/light) and Help.
- **Left panel - Devices:** drag a device onto the canvas, or click it and then click the canvas.
- **Centre - Canvas:** your network. Coloured zones show sites (blue = LAN, green = MAN, orange = WAN).
- **Right panel - Properties:** settings of the selected device or link; with nothing selected it shows a summary and any validation problems.
- **Bottom panel - tabs:** Topology YAML, Consoles, Simulation, Tests, Analysis and Log.
- **Bottom left footer:** "By Sasanka Boruah -VU3INI". The rest of the status bar shows the compute device and validation status.

## Building a network
1. Drag devices from the **Devices** panel onto the canvas.
2. Press **C** or click **Connect** on the toolbar. Click the first device, choose or create its interface, then click the second device and choose its interface. Press **Esc** to stop connecting.
3. Select a device to set its name, kind, site, interfaces and IP addresses in **Properties**.
4. Group devices into **sites** (Properties > Site > New site...). Each site has a scope: LAN, MAN or WAN.
5. Save with **Ctrl+S**. The file is plain YAML (*.nstopo.yaml) and can be edited in any text editor.

**Tips**
- Delete removes the selected devices or links. Ctrl+Z / Ctrl+Y undo and redo.
- Mouse wheel zooms; drag the empty canvas to pan; **F** fits the whole network in view.
- Double-click a device to open its console.
- Right-click a device or link to power it off/on or bring the link down/up for failure testing.

## Device kinds
| Kind | Behaviour |
|---|---|
| router | Routes between interfaces; OSPF, IS-IS, BGP, static routes, VRRP, NAT, ACLs, IPsec/GRE tunnels, DHCP server, MPLS LDP |
| l3switch | A switch (VLANs, trunks, spanning tree) that also routes between VLAN interfaces (SVIs such as Vlan10) |
| switch | Layer-2 switch: VLANs, access/trunk ports, 802.1Q, RSTP, LACP port-channels, voice VLAN |
| firewall | A router intended for ACL and NAT policies |
| host / server | End devices with a default gateway or DHCP; servers listen on ports 22, 53, 80 and 443 by default |
| ap | Wireless access point (bridges like a switch) |
| cloud | The Internet or an ISP; addresses its ports automatically to match the device it is connected to |
| external | A placeholder for a real network outside the lab |

## Topology file (NSTopo YAML)
The topology file is the single source of truth. The canvas, the Properties panel and the consoles all edit it.
Top-level sections: `nstopo` (version), `meta`, `defaults`, `sites`, `link_profiles`, `addressing`, `nodes`, `links`, `services`, `policies`, `scenarios`, `tests`, `capture`, `layout`.

**Editing in the app:** open the **Topology YAML** tab, edit, then press **Ctrl+Enter** (Apply). Problems are listed below the editor with their exact location, for example `nodes.R1.interfaces.Gi0/0.ipv4: invalid IPv4 address`.

**Units:** bandwidth `100Mbps`, `10Gbps`; time `5ms`, `50us`, `1s`; loss `0.1%`.

**Automatic addresses:** `ipv4: auto` on a point-to-point link takes a /31 from an `addressing` pool with `purpose: p2p`; on a loopback it takes a /32 from a `purpose: loopback` pool. `ipv4: dhcp` makes the interface a DHCP client.

**Inheritance:** `inherit: core1` copies another node, then your keys override it.

**Interface keys:** ipv4, ipv6, mode (access, trunk, routed), vlan, allowed_vlans, native_vlan, voice_vlan, lag, gateway, dns, helper (DHCP relay), acl_in, acl_out, tunnel, ospf_cost, shutdown, description, portfast.

**Link keys:** a, b (`node:interface`), profile, type, length_km (adds 5 us per km of fibre), label.

## Switching: VLANs, trunks, spanning tree, LAG
- Access ports carry one VLAN untagged (`vlan: 10`); a `voice_vlan` is carried tagged.
- Trunks carry `allowed_vlans` tagged and the `native_vlan` untagged (IEEE 802.1Q). A native VLAN mismatch joins two VLANs together, and the VLAN check test reports it.
- Spanning tree runs per VLAN (RSTP by default). The bridge with the lowest priority (then lowest bridge MAC) is the root. Set `stp: { priority: 4096 }` on your core switch. Blocked (alternate) links are drawn dashed orange.
- Port-channels: `Po1: { mode: trunk, lag: { protocol: lacp, members: [Te1/0/1, Te1/0/2] } }`. Members share the bandwidth and count as one spanning-tree port.
- Metro rings: a service with `protection: { type: g8032, rpl_owner: m3:ring-e }` blocks the ring protection link, as in ITU-T G.8032.

## Routing
- **Connected and static:** `routing: { static: [ { prefix: 0.0.0.0/0, next_hop: 203.0.113.1 } ] }`.
- **OSPF:** `routing: { ospf: { process: 1, areas: { 0: [Gi0/0, Lo0] }, passive: [Vlan10], redistribute: [bgp], default_originate: true } }`. Cost = reference bandwidth / interface bandwidth (reference 100 Gbps by default, Settings > Simulation).
- **IS-IS:** `routing: { isis: { level: 2, interfaces: [eth1, Lo0] } }`.
- **BGP:** `routing: { bgp: { asn: 65010, networks: [10.10.10.0/24], neighbors: [ { peer: 172.16.1.1, remote_as: 65000, local_pref: 100, bfd: true } ] } }`. Path choice: highest local-preference, shortest AS path, eBGP over iBGP, lowest neighbour address. iBGP uses next-hop-self.
- **MPLS:** `mpls: { ldp: true }` on provider routers. Customer traffic between PEs is label-switched, so P routers need no customer routes. Label push and pop appear in Simulation mode.
- **VRRP (first-hop redundancy):** `vrrp: [ { group: 10, interface: Vlan10, vip: 10.10.10.1, priority: 120, advert: 1 } ]`. The highest priority is MASTER and answers ARP for the virtual IP with MAC 00:00:5e:00:01:xx.
- **Tunnels (IPsec / GRE):** `Tun0: { ipv4: 10.254.9.1/30, tunnel: { type: ipsec, peer: br-edge, source: Gi0/2 } }` on both ends. The tunnel comes up when the underlay addresses can reach each other.
- **Administrative distance:** connected 0, static 1, eBGP 20, OSPF 110, IS-IS 115, iBGP 200.

## Services: DHCP, DNS, NAT, ACLs
- **DHCP:** a server or router with `services: { dhcp: { pools: [staff, guests] } }` (pool names from `addressing`). On other subnets, set `helper: <server IP>` on the gateway interface (DHCP relay). Leases start at host .100 (Settings). The gateway handed out is the VRRP virtual IP when one exists.
- **DNS:** `services: { dns: { zone: corp.lan, records: { web: 10.0.0.5 } } }`. Every device name also resolves automatically.
- **NAT overload (PAT):** `policies: { nat: { inside: [Gi0/0], outside: Gi0/2, overload: true } }`.
- **ACLs:** define rules under top-level `policies: { acl: { NAME: [ { action: deny, proto: tcp, src: 10.30.10.0/24, dst: 10.10.30.10/32, dport: 22 }, { action: permit, proto: ip } ] } }` and bind them with `acl_in: NAME` or `acl_out: NAME` on an interface. An implicit deny ends every ACL. Return traffic of permitted sessions is allowed (stateful).

## Consoles (CLI)
Double-click a device, or use Tools > Open Console. Network devices use a Cisco IOS-style CLI; hosts use a Windows-style prompt.

**Exec commands:** `enable`, `show running-config`, `show ip interface brief`, `show ip route`, `show vlan brief`, `show interfaces trunk`, `show spanning-tree`, `show mac address-table`, `show arp`, `show ip ospf neighbor`, `show isis neighbors`, `show ip bgp summary`, `show ip bgp`, `show vrrp brief`, `show ip dhcp binding`, `show lldp neighbors`, `show mpls ldp neighbor`, `ping`, `traceroute`.

**Configuration:** `configure terminal`, `hostname`, `vlan 10` / `name`, `interface Gi0/1`, `ip address 10.0.0.1 255.255.255.0`, `ip address dhcp`, `shutdown` / `no shutdown`, `switchport mode access|trunk`, `switchport access vlan 10`, `switchport trunk allowed vlan 10,20`, `switchport trunk native vlan 99`, `ip helper-address`, `ip access-group NAME in`, `vrrp 10 ip 10.10.10.1`, `vrrp 10 priority 120`, `ip route ...`, `router ospf 1` / `network 10.0.0.0 0.0.0.255 area 0`, `router bgp 65010` / `neighbor ... remote-as ...` / `network ... mask ...`.

**Host prompt:** `ipconfig`, `ipconfig /all`, `ipconfig /renew`, `ping`, `tracert`, `arp -a`, `nslookup`.

Abbreviations work (`sh ip int br`, `conf t`, `int gi0/1`). Up/Down recall history; Tab completes words. Every change you make in a console is written to the topology file at once.

## Realtime and Simulation modes
Open the **Simulation** tab, choose a source, a destination and a protocol (ICMP ping, traceroute, TCP or UDP port), then:
- **Realtime** shows the result at once: round-trip time, path, bottleneck bandwidth, or the exact reason it failed (no route, ARP failed, ACL rule, NAT, TTL expired...).
- **Simulate** lists every event: ARP requests and replies, each switch hop with its 802.1Q tag, MPLS label push and pop, NAT translation, delivery and the reply. Click an event to see its headers layer by layer and in hex. Use **Step**, **Play** and the speed slider to animate the packet on the canvas.
- **Save .pcapng** writes the frames to a capture file that opens in Wireshark.

## Failure testing and scenarios
Right-click a link and choose **Bring link down**, or right-click a device and choose **Power off**, then ping again to see how the network reroutes. **Simulate > Restore all** brings everything back.

Scenarios in the file replay timed failures, and tests can run *during* them:
```
scenarios:
  core1-fails:
    steps:
      - { at: 10s, action: node_reboot, target: core1, params: { duration: 60s } }
tests:
  - { id: fhrp-failover, type: ping, from: pc1, to: dc-srv, during: core1-fails, expect: { max_outage: 3s } }
```
Actions: link_down, link_up, node_down, node_up, node_reboot, set_profile. Outages are estimated from the protocol timers in Settings > Timers: VRRP master-down interval (3 x advertisement + skew), RSTP, G.8032 (50 ms), OSPF/IS-IS carrier-loss or dead interval, BGP fall-over, hold time or BFD.

## Tests
Tests live in the `tests:` section and run with **F5** (Tests tab > Run all). They run in three phases, cheapest first: static checks, functional checks, then performance checks. If static checks fail, later phases are skipped (Settings > Tests).

| Type | Checks | Key fields |
|---|---|---|
| static.addressing | duplicate or overlapping subnets | - |
| static.vlan_consistency | trunk/access, native and allowed VLAN mismatches | - |
| ping | reachability, loss, RTT; outage during a scenario | from, to, expect: loss_max, rtt_max, max_outage, reachable |
| traceroute | path through named devices | from, to, expect: via [..], exact |
| tcp.connect / udp.reach | port open through ACLs and NAT | from, to, port, expect: open |
| ospf.neighbor / isis.neighbor | adjacency state and count | on, expect: state, count, neighbor |
| ospf.route / bgp.route | route present, next hop | on, prefix, expect: present, next_hop |
| bgp.session | session state | on, peer, expect: state |
| vrrp.state | MASTER / BACKUP | on, group, expect: state |
| stp.root / stp.port_state | root bridge, port role | vlan, on, port, expect: bridge, role |
| lag.members | port-channel members up | on, lag, expect: up |
| dhcp.lease | client got a lease from a pool | on, expect: pool |
| dns.resolve | name resolution | on, name, expect: address |
| rfc2544.throughput | RFC 2544 frame-size throughput | link or from/to, frame_sizes, expect: min |
| rfc2544.latency | one-way latency | link or from/to, expect: max |
| twamp | one-way delay p99 and jitter (IPDV) | from, to, expect: one_way_max, jitter_max, loss_max |
| y1564 | MEF service: CIR, FLR, FTD, FDV | service, expect: cir: met, flr_max, ftd_max, fdv_max |

Add `at: 30s` with `during:` to check the state at a moment inside a scenario. Double-click a ping, traceroute or connect test to replay it in the Simulation tab. Results can be exported as JSON and JUnit XML (for GitHub Actions or other CI).

## Analysis and GPU / CPU computing
The **Analysis** tab computes an all-pairs latency matrix between devices and runs a compute benchmark.
Performance tests use Monte-Carlo sampling (Settings > Tests > samples).

**Compute device** (Settings > Compute):
- **auto**: NVIDIA GPU when available, otherwise CPU.
- **cpu**: NumPy on all CPU cores.
- **gpu**: NVIDIA GPU through CuPy.
- **hybrid**: GPU and CPU together; the work is split by the "share of work on the GPU" setting.

If the GPU runs out of memory or reports any error, PacketLab automatically falls back to the CPU and notes it in the Log. The GPU code is an optional add-on (about 240 MB: CuPy plus the NVIDIA CUDA 12 runtime, NVRTC and cuRAND wheels). Install it from Settings > Compute > **Download GPU add-on** (needs the Internet once and a recent NVIDIA driver). Without it everything runs on the CPU. For normal-sized labs the CPU is already fast; the GPU helps with very large matrices and millions of samples.

## Emulation with real router software
PacketLab can export your design so it runs on real network operating systems:
- **File > Export > Containerlab** writes `<name>.clab.yml`, FRRouting configs (`configs/<node>/frr.conf`) and `apply-impairments.sh` (tc netem delay, jitter, loss and rate per link).
- **File > Export > Kathará** writes `lab.conf`, startup files and FRR configs.

To deploy from PacketLab, choose a back end in Settings > Emulation:
- **wsl**: Windows 11 with WSL2 (Ubuntu) plus Docker and Containerlab installed inside WSL.
- **ssh**: a Linux machine on your network (for example a lab server) with Docker and Containerlab; PacketLab copies the files with scp and runs Containerlab over SSH.
- **local**: Containerlab already on the PATH.

Then use **Emulation > Deploy / Inspect / Destroy**. Output streams into the Log tab. Containers are downloaded by Docker the first time, which needs the Internet.

## Practice labs
**03-troubleshooting-lab** has three planted faults:
1. The trunk's native VLAN differs on the two ends (99 vs 1).
2. The trunk from ACC does not allow VLAN 20.
3. SALES-PC has the wrong default gateway.

Fix them in the consoles (`switchport trunk native vlan 1`, `switchport trunk allowed vlan 10,20`) and in Properties or YAML (gateway 172.16.10.1), then run the tests again.

## Settings
Settings > Preferences (Ctrl+,) holds every option, grouped as General, Canvas, Editor, Console, Simulation, Timers, Tests, Compute, Emulation and Captures. **Restore defaults** resets them (your accepted Terms and recent files are kept). Settings are stored in `%APPDATA%\VU3INI PacketLab\settings.json`; autosaves and add-ons are in `%LOCALAPPDATA%\VU3INI PacketLab`.

## Keyboard shortcuts
| Keys | Action |
|---|---|
| Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S | New, Open, Save, Save As |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Delete | Delete selected devices or links |
| C / Esc | Connect mode on / off |
| F | Fit network in view |
| Ctrl+Enter | Apply YAML |
| F5 | Run all tests |
| F6 | Validate topology |
| Ctrl+T | Open console for the selected device |
| Ctrl+, | Settings |
| Ctrl+D | Toggle dark / light theme |
| F1 | Help |

## Troubleshooting
- **"ARP ... failed (host unreachable)"**: the next hop is not on the same broadcast domain. Check the VLAN, trunk allowed list, native VLAN and the gateway address.
- **"no route to ..."**: the router has no route; check routing protocol neighbours (`show ip ospf neighbor`, `show ip bgp summary`) and redistribution.
- **"ACL ... deny"**: an access list dropped the packet; the message names the rule number.
- **DHCP "no DHCP server"**: add a server on the segment or a `helper` (relay) on the gateway interface, and make sure the pool covers that subnet.
- **BGP "Active"**: the peer address is unreachable, the peer has no neighbour statement for you, or the AS numbers differ.
- **GPU not used**: install the GPU add-on, update the NVIDIA driver, or set Compute device to cpu. Errors are shown in the Log tab.
- **Emulation fails**: test the back end in a terminal first (`wsl -- containerlab version` or `ssh user@host containerlab version`).
- Logs are written to `%LOCALAPPDATA%\VU3INI PacketLab\logs`.

## Limitations (version 1)
PacketLab models protocol behaviour; it is not a bit-exact copy of any vendor's software.
- OSPF uses one link-state database for all areas (area types and inter-area summaries are not modelled). IS-IS is modelled as a second link-state IGP.
- L3VPN customer routes share the global table on PE routers (VRF isolation is planned).
- IPv6 addresses are stored and exported but not yet routed in the simulator.
- Wi-Fi radio propagation is not modelled; access points bridge like switches.
- Performance figures are statistical models from link profiles, not traffic generated through real queues. For queue-accurate results, use emulation mode.

## About and contact
VU3INI PacketLab is written by **Sasanka Boruah (VU3INI)**. Contact: hamvu3ini@gmail.com. See Help > About for the version, credits and Terms of Use.
