import os
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET

import _paths  # noqa: F401
from packetlab.core import compiler, compute, engine, model, pcapng, runner
from packetlab.core.cli import Console
from packetlab.core.lab import Lab


class TestRunner(unittest.TestCase):
    def test_all_sample_tests_pass(self):
        for f in ("hq-metro-branch.nstopo.yaml", "01-small-office-lan.nstopo.yaml",
                  "02-ospf-wan-triangle.nstopo.yaml"):
            t = model.load_file(os.path.join(_paths.SAMPLES, f))
            res = runner.Runner(t, compute=compute.Compute("cpu")).run_all()
            bad = [(r.id, r.status, r.detail) for r in res if r.status != "pass"]
            self.assertEqual(bad, [], f)

    def test_outage_measurement(self):
        t = model.load_file(os.path.join(_paths.SAMPLES, "hq-metro-branch.nstopo.yaml"))
        r = runner.Runner(t)
        res = {x.id: x for x in r.run_all()}
        self.assertIn("ms", res["fhrp-failover"].measured)
        self.assertEqual(res["ring-heals"].measured, "50 ms")

    def test_reports(self):
        t = model.load_file(os.path.join(_paths.SAMPLES, "01-small-office-lan.nstopo.yaml"))
        res = runner.Runner(t).run_all()
        root = ET.fromstring(runner.to_junit(res))
        self.assertEqual(root.get("tests"), str(len(res)))
        self.assertIn('"summary"', runner.to_json(res))

    def test_bad_test_spec_is_error_not_crash(self):
        t = model.load_text('nstopo: "0.1"\ntests:\n  - { id: x, type: ping, from: nobody, to: 1.2.3.4 }\n'
                            '  - { id: y, type: warp.drive }\n')
        res = runner.Runner(t).run_all()
        self.assertEqual([r.status for r in res], ["error", "error"])


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()
        self.lab.load_file(os.path.join(_paths.SAMPLES, "hq-metro-branch.nstopo.yaml"))

    def test_show_commands(self):
        c = Console(self.lab, "core1")
        c.run("en")
        self.assertIn("Vlan10", c.run("sh ip int br"))
        self.assertIn("Gateway of last resort", c.run("sh ip route"))
        self.assertIn("MASTER", c.run("sh vrrp br"))
        self.assertIn("FULL", c.run("sh ip ospf nei"))
        self.assertIn("hostname core1", c.run("sh run"))
        self.assertIn("Success rate is 100", c.run("ping 10.20.30.10"))

    def test_config_changes_topology(self):
        c = Console(self.lab, "acc1")
        for cmd in ("en", "conf t", "int gi0/1", "switchport access vlan 20", "end"):
            c.run(cmd)
        self.assertEqual(self.lab.raw["nodes"]["acc1"]["interfaces"]["Gi0/1"]["vlan"], 20)
        self.assertTrue(self.lab.undo())
        self.assertEqual(self.lab.raw["nodes"]["acc1"]["interfaces"]["Gi0/1"]["vlan"], 10)

    def test_rename_updates_links_and_tests(self):
        c = Console(self.lab, "core1")
        for cmd in ("en", "conf t", "hostname CORE-A"):
            c.run(cmd)
        self.assertIn("CORE-A", self.lab.topo.nodes)
        self.assertTrue(any(l.a_node == "CORE-A" for l in self.lab.topo.links))
        self.assertTrue(any(t.get("on") == "CORE-A" for t in self.lab.raw["tests"]))

    def test_host_prompt(self):
        h = Console(self.lab, "pc1")
        self.assertIn("10.10.10.100", h.run("ipconfig"))
        self.assertIn("Received = 4", h.run("ping dc-srv"))
        self.assertIn("10.20.30.10", h.run("nslookup dc-srv.corp.lan"))
        self.assertIn("not recognized", h.run("format c:"))

    def test_bad_input(self):
        c = Console(self.lab, "core1")
        c.run("en")
        self.assertTrue(c.run("show gibberish").startswith("%"))
        c.run("conf t")
        self.assertTrue(c.run("ip route 1.2.3").startswith("%"))


class TestExports(unittest.TestCase):
    def test_containerlab_and_kathara(self):
        t = model.load_file(os.path.join(_paths.SAMPLES, "hq-metro-branch.nstopo.yaml"))
        d = tempfile.mkdtemp()
        p = compiler.to_containerlab(t, os.path.join(d, "clab"))
        import yaml
        doc = yaml.safe_load(open(p, encoding="utf-8"))
        self.assertEqual(len(doc["topology"]["links"]), len(t.links))
        frr = open(os.path.join(d, "clab", "configs", "hq-edge", "frr.conf"), encoding="utf-8").read()
        self.assertIn("router bgp 65010", frr)
        self.assertTrue(os.path.exists(os.path.join(d, "clab", "apply-impairments.sh")))
        k = compiler.to_kathara(t, os.path.join(d, "kat"))
        self.assertIn("core1[0]=", open(k, encoding="utf-8").read())

    def test_pcapng(self):
        t = model.load_file(os.path.join(_paths.SAMPLES, "hq-metro-branch.nstopo.yaml"))
        r = engine.Engine(t).ping("pc1", "br-pc1", record=True)
        path = os.path.join(tempfile.mkdtemp(), "x.pcapng")
        n = pcapng.write(path, r["events"])
        data = open(path, "rb").read()
        self.assertGreater(n, 10)
        self.assertEqual(struct.unpack("<I", data[:4])[0], 0x0A0D0D0A)
        # walk the blocks: every block length must be consistent
        off, blocks = 0, 0
        while off < len(data):
            btype, blen = struct.unpack("<II", data[off:off + 8])
            self.assertEqual(struct.unpack("<I", data[off + blen - 4:off + blen])[0], blen)
            off += blen
            blocks += 1
        self.assertEqual(blocks, n + 2)
        fr = pcapng.frame_from_pdu(r["events"][2].pdu)
        self.assertEqual(pcapng._csum(fr[14 + (4 if fr[12:14] == b"\x81\x00" else 0):][:20]), 0)


class TestCompute(unittest.TestCase):
    def test_all_pairs_cpu(self):
        import numpy as np
        inf = np.inf
        w = np.array([[0, 1, inf], [1, 0, 2], [inf, 2, 0]], float)
        d = compute.Compute("cpu").all_pairs(w)
        self.assertEqual(d[0, 2], 3)

    def test_gpu_falls_back_to_cpu(self):
        c = compute.Compute("gpu")
        st = c.sample_path(10, 1, 0.01, 5000, 3)
        self.assertEqual(st["device"], "cpu")       # no GPU in the test environment
        self.assertAlmostEqual(st["loss"], 0.01, delta=0.01)

    def test_hybrid(self):
        st = compute.Compute("hybrid").sample_path(5, 0.5, 0.0, 20000, 1)
        self.assertGreater(st["mean"], 5)


if __name__ == "__main__":
    unittest.main()
