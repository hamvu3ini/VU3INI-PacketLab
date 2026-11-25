import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

import _paths  # noqa: F401
from PySide6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])


class TestGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from packetlab.gui.mainwindow import MainWindow, samples_dir
        from packetlab.gui.settings import Settings
        cls.s = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        cls.s["terms_accepted"] = "2026-11-25"
        cls.w = MainWindow(cls.s)
        cls.w.load(os.path.join(samples_dir(), "hq-metro-branch.nstopo.yaml"))
        cls.w.show()
        APP.processEvents()

    def test_logo_footer_title(self):
        self.assertFalse(self.w.logo.pixmap().isNull())
        self.assertEqual(self.w.footer.text(), "By Sasanka Boruah -VU3INI")
        self.assertIn("VU3INI PacketLab", self.w.windowTitle())

    def test_canvas_items(self):
        self.assertEqual(len(self.w.canvas.nodes), 20)
        self.assertEqual(len(self.w.canvas.links), 25)

    def test_add_connect_delete(self):
        lab = self.w.lab
        a = lab.add_node("router", 0, 0)
        b = lab.add_node("host", 100, 0)
        lab.add_link(a, lab.next_iface(a), b, lab.next_iface(b))
        APP.processEvents()
        self.assertIn(a, self.w.canvas.nodes)
        lab.delete_node(a)
        lab.delete_node(b)
        self.assertNotIn(a, lab.topo.nodes)

    def test_theme_toggle(self):
        before = self.s["theme"]
        self.w.toggle_theme()
        self.assertNotEqual(self.s["theme"], before)
        self.w.toggle_theme()
        self.assertEqual(self.s["theme"], before)

    def test_run_tests_panel(self):
        self.w.tests.run_all()
        self.assertTrue(all(r.status == "pass" for r in self.w.tests.results))

    def test_simulation_panel(self):
        self.w.sim.preset("pc1", "br-pc1")
        self.w.sim.simulate()
        self.assertGreater(len(self.w.sim.events), 10)
        self.w.sim.goto(3)
        self.assertGreater(self.w.sim.tree.topLevelItemCount(), 0)

    def test_yaml_apply_roundtrip(self):
        txt = self.w.yaml.ed.toPlainText()
        self.w.yaml.ed.setPlainText(txt.replace("hq-metro-branch", "renamed-net", 1))
        self.w.yaml.apply()
        self.assertEqual(self.w.lab.raw["meta"]["name"], "renamed-net")
        self.w.lab.undo()

    def test_dialogs_open(self):
        from packetlab.gui import dialogs
        for d in (dialogs.HelpDialog(self.w, "BGP"), dialogs.SettingsDialog(self.s, self.w),
                  dialogs.AboutDialog("cpu", self.w)):
            d.show()
            APP.processEvents()
            d.close()
        h = dialogs.HelpDialog(self.w, "VRRP")
        self.assertIn("matches", h.count.text())

    def test_splash_needs_agreement(self):
        from packetlab.gui import dialogs
        from packetlab.gui.settings import Settings
        s = Settings(os.path.join(tempfile.mkdtemp(), "t.json"))
        sp = dialogs.SplashDialog(s)
        sp.show()
        APP.processEvents()
        self.assertFalse(sp.b_ok.isEnabled())
        sp.close()
        sp.reject()
        APP.processEvents()
        self.assertTrue(sp.isVisible())          # cannot be closed without OK
        sp.chk.setChecked(True)
        sp.b_ok.click()
        APP.processEvents()
        self.assertFalse(sp.isVisible())
        self.assertTrue(s.terms_ok)

    def test_save_and_reopen(self):
        p = os.path.join(tempfile.mkdtemp(), "net.nstopo.yaml")
        self.w.lab.path = p
        self.w.save_file()
        self.assertTrue(self.w.load(p))
        self.assertEqual(len(self.w.lab.topo.nodes), 20)

    def test_export_picture(self):
        img = self.w.canvas.to_image()
        self.assertGreater(img.width(), 100)


class TestInstaller(unittest.TestCase):
    def test_install_uninstall_cycle(self):
        import installer_core as core
        d = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(d, "ad")
        os.environ["LOCALAPPDATA"] = os.path.join(d, "lad")
        z = os.path.join(d, "app.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr(core.EXE_NAME, "x")
            f.writestr("_internal/a.bin", "y" * 1000)
        target = os.path.join(d, "Programs", core.APP_NAME)
        steps = []
        core.install(target, "9.9.9", z, None, None, True, True, False, lambda p, m: steps.append(p), lambda m: None)
        self.assertTrue(os.path.exists(os.path.join(target, core.EXE_NAME)))
        self.assertEqual(steps[-1], 100)
        core.uninstall(target, True, lambda p, m: None, lambda m: None)
        self.assertFalse(os.path.exists(target))

    def test_refuses_foreign_folder(self):
        import installer_core as core
        d = tempfile.mkdtemp()
        open(os.path.join(d, "important.docx"), "w").write("keep me")
        with self.assertRaises(RuntimeError):
            core.uninstall(d, False, lambda p, m: None, lambda m: None)
        self.assertTrue(os.path.exists(os.path.join(d, "important.docx")))


if __name__ == "__main__":
    unittest.main()
