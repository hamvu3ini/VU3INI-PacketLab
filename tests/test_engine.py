import os
import unittest

import _paths  # noqa: F401
from packetlab.core import engine, model


def sample(name):
    return model.load_file(os.path.join(_paths.SAMPLES, name))


class TestModel(unittest.TestCase):
    def test_units(self):
        self.assertEqual(model.parse_bandwidth("10Gbps"), 10e9)
        self.assertEqual(model.parse_bandwidth("100 Mbps"), 100e6)
        self.assertAlmostEqual(model.parse_time_ms("50us"), 0.05)
        self.assertAlmostEqual(model.parse_time_ms("2s"), 2000)
        self.assertAlmostEqual(model.parse_percent("0.3%"), 0.003)
        with self.assertRaises(ValueError):
            model.parse_bandwidth("fast")

    def test_all_samples_valid(self):
        for f in os.listdir(_paths.SAMPLES):
            t = sample(f)
            self.assertEqual(t.errors, [], f)

    def test_validation_errors_are_located(self):
        t = model.load_text("""
nstopo: "0.1"
nodes:
  R1: { kind: router, interfaces: { Gi0/0: { ipv4: 10.0.0.300/24 } } }
  R2: { kind: toaster }
links:
  - { a: R1:Gi0/0, b: R9:eth0 }
""")
        joined = "\\n".join(t.errors)
        self.assertIn("nodes.R1.interfaces.Gi0/0.ipv4", joined)
        self.assertIn("nodes.R2.kind", joined)
        self.assertIn("unknown node 'R9'", joined)

    def test_yaml_syntax_error(self):
        with self.assertRaises(model.TopologyError):
            model.load_text("nodes: [unclosed")

    def test_yaml_on_key(self):
        t = model.load_text('nstopo: "0.1"\ntests:\n  - { id: x, type: ping, on: R1 }\n')
        self.assertEqual(t.tests[0]["on"], "R1")

    def test_auto_p2p_addresses(self):
        t = sample("hq-metro-branch.nstopo.yaml")
        a = t.iface("core1", "Gi1/0/48").ip
        b = t.iface("hq-edge", "Gi0/0").ip
        self.assertEqual(a.network, b.network)
        self.assertEqual(a.network.prefixlen, 31)


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = sample("hq-metro-branch.nstopo.yaml")
        cls.e = engine.Engine(cls.t)

    def test_vrrp_master_and_vip(self):
        self.assertEqual(self.e.vips[__import__("ipaddress").ip_address("10.10.10.1")][0], "core1")

    def test_dhcp_relay_lease(self):
        lease = self.e.dhcp_leases[("pc1", "eth0")]
        self.assertEqual(str(lease["ip"]), "10.10.10.100/24")
        self.assertEqual(str(lease["gateway"]), "10.10.10.1")

    def test_stp_blocks_redundant_links(self):
        self.assertTrue(self.e.blocked)
        roles = dict(((n, p, v), r) for (n, p, v), r in self.e.port_roles.items())
        self.assertEqual(roles[("acc1", "Te0/49", 10)], "root")
        self.assertEqual(roles[("acc1", "Te0/50", 10)], "alternate")
        self.assertEqual(roles[("m3", "ring-e", 1)], "alternate")   # G.8032 RPL owner

    def test_ping_lan_metro_wan(self):
        for src, dst in (("pc1", "dc-srv"), ("pc1", "br-pc1"), ("pc2", "srv1"), ("pc1", "internet")):
            r = self.e.ping(src, dst, record=False)
            self.assertTrue(r["ok"], f"{src}->{dst}: {r['reason']}")

    def test_mpls_label_switching(self):
        r = self.e.ping("pc1", "br-pc1", record=True)
        kinds = [ev.summary for ev in r["events"] if ev.kind == "mpls"]
        self.assertTrue(any("push" in k for k in kinds) and any("pop" in k for k in kinds))

    def test_traceroute_path(self):
        path = [h["node"] for h in self.e.traceroute("pc1", "br-pc1")]
        self.assertEqual(path, ["core1", "hq-edge", "pe1", "p1", "pe2", "br-edge", "br-pc1"])

    def test_acl_blocks_ssh_allows_https(self):
        self.assertFalse(self.e.connect("br-pc1", "srv1", 22)["open"])
        self.assertTrue(self.e.connect("br-pc1", "srv1", 443)["open"])

    def test_nat_to_internet(self):
        r = self.e.ping("pc1", "internet", record=True)
        self.assertTrue(any(ev.kind == "nat" for ev in r["events"]))

    def test_ipsec_backup_when_mpls_down(self):
        e2 = engine.Engine(self.t, down_links={self.t.link_by_name("hq-mpls").idx})
        r = e2.ping("pc1", "br-pc1", record=False)
        self.assertTrue(r["ok"])
        self.assertIn("br-edge", r["request"].l3_path)
        self.assertNotIn("pe1", r["request"].l3_path)

    def test_core_failure_moves_vrrp(self):
        e2 = engine.Engine(self.t, down_nodes={"core1"}, sticky_leases=self.e.dhcp_leases)
        self.assertEqual(e2.vips[__import__("ipaddress").ip_address("10.10.10.1")][0], "core2")
        self.assertTrue(e2.ping("pc1", "dc-srv", record=False)["ok"])

    def test_bgp_sessions(self):
        st = {str(s["peer"]): s["state"] for s in self.e.bgp_sessions["hq-edge"]}
        self.assertEqual(st["172.16.1.1"], "Established")
        self.assertEqual(st["10.254.9.2"], "Established")

    def test_unknown_destination(self):
        r = self.e.ping("pc1", "no-such-host", record=False)
        self.assertFalse(r["ok"])


class TestTroubleshootingLab(unittest.TestCase):
    def test_faults_then_fixed(self):
        from packetlab.core import runner
        t = sample("03-troubleshooting-lab.nstopo.yaml")
        res = {r.id: r.status for r in runner.Runner(t).run_all(False)}
        self.assertEqual(res["trunk-ok"], "fail")
        self.assertEqual(res["sales-to-eng"], "fail")
        raw = t.raw
        raw["nodes"]["DIST"]["interfaces"]["Gi0/1"]["native_vlan"] = 1
        raw["nodes"]["ACC"]["interfaces"]["Gi0/24"]["allowed_vlans"] = [10, 20]
        raw["nodes"]["SALES-PC"]["interfaces"]["eth0"]["gateway"] = "172.16.10.1"
        t2 = model.build(raw)
        res2 = runner.Runner(t2).run_all(False)
        self.assertTrue(all(r.status == "pass" for r in res2), [(r.id, r.detail) for r in res2])


if __name__ == "__main__":
    unittest.main()
