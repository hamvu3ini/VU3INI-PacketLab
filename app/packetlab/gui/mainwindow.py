"""VU3INI PacketLab main window."""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMenuBar, QMessageBox, QScrollArea, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, AUTHOR, CALLSIGN, __version__
from ..core import compiler
from ..core.compute import Compute
from ..core.emulation import Emulator
from ..core.lab import Lab
from ..core.model import TopologyError
from . import dialogs
from .canvas import Canvas
from .icons import glyph_icon
from .panels import (AnalysisPanel, ConsolePanel, DevicePalette, LogPanel, PropertiesPanel, SimulationPanel,
                     TestsPanel, YamlPanel)
from .settings import Settings, documents_dir, local_dir
from .theme import ACCENTS, palette, stylesheet

FOOTER_TEXT = "By Sasanka Boruah -VU3INI"


def samples_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base and os.path.isdir(os.path.join(base, "packetlab", "samples")):
        return os.path.join(base, "packetlab", "samples")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.compute = self._make_compute()
        self.lab = Lab(self.s.engine_settings(), self.compute)
        self.emulator = Emulator(self.s.v, self._log)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(dialogs.asset("app.ico")))
        self.resize(1500, 920)
        self._in_update = False
        self._build_header()
        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        self.lab.subscribe(self._on_lab)
        self.apply_theme()
        self.autosave = QTimer(self)
        self.autosave.timeout.connect(self._autosave)
        self._restart_autosave()
        if self.s["geometry"]:
            try:
                self.restoreGeometry(QByteArray.fromBase64(self.s["geometry"].encode()))
                self.restoreState(QByteArray.fromBase64(self.s["state"].encode()))
            except Exception:  # noqa: BLE001
                pass
        self._log(f"{APP_NAME} {__version__} started. Compute: {self.compute.describe()}")

    # ------------------------------------------------------------------ construction
    def _make_compute(self) -> Compute:
        return Compute(self.s["compute_mode"], int(self.s["gpu_mem_limit_mb"]), int(self.s["cpu_threads"]),
                       float(self.s["hybrid_gpu_share"]), log=lambda m: self._log(m) if hasattr(self, "logp") else None)

    def _build_header(self):
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setObjectName("header")
        h = QHBoxLayout(head)
        h.setContentsMargins(10, 6, 10, 6)
        self.logo = QLabel()
        self.logo.setPixmap(dialogs.round_logo(44))
        self.logo.setToolTip(f"{APP_NAME} by {AUTHOR} ({CALLSIGN})")
        h.addWidget(self.logo)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        self.subtitle = QLabel(f"v{__version__} · design · simulate · test LAN / MAN / WAN")
        self.subtitle.setObjectName("subtitle")
        tv.addWidget(t)
        tv.addWidget(self.subtitle)
        h.addLayout(tv)
        h.addStretch(1)
        self.file_lbl = QLabel("")
        self.file_lbl.setObjectName("muted")
        h.addWidget(self.file_lbl)
        self.btn_theme = QToolButton()
        self.btn_theme.setToolTip("Switch dark / light theme (Ctrl+D)")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_help = QToolButton()
        self.btn_help.setText(" Help")
        self.btn_help.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_help.setToolTip("Searchable help (F1)")
        self.btn_help.clicked.connect(lambda: self.show_help())
        h.addWidget(self.btn_theme)
        h.addWidget(self.btn_help)
        v.addWidget(head)
        self.menubar = QMenuBar()
        v.addWidget(self.menubar)
        self.setMenuWidget(holder)

    def _build_central(self):
        self.canvas = Canvas(self.lab, self.s)
        self.setCentralWidget(self.canvas)
        self.canvas.open_console.connect(self.open_console)
        self.canvas.status.connect(lambda m: self.statusBar().showMessage(m, 8000) if m else
                                   self.statusBar().clearMessage())

    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        self.logp = LogPanel()
        self.palette_w = DevicePalette()
        self.palette_w.place.connect(self.canvas.set_place_kind)
        self.d_pal = self._dock("Devices", self.palette_w, Qt.LeftDockWidgetArea, "dock_devices")
        self.props = PropertiesPanel(self.lab, self.s, self._log)
        self.props.open_console.connect(self.open_console)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.props)
        self.d_props = self._dock("Properties", sc, Qt.RightDockWidgetArea, "dock_props")
        self.canvas.node_selected.connect(self.props.show_node)
        self.canvas.link_selected.connect(self.props.show_link)
        self.canvas.nothing_selected.connect(self.props.show_summary)
        self.bottom = QTabWidget()
        self.yaml = YamlPanel(self.lab, self.s, self._log)
        self.consoles = ConsolePanel(self.lab, self.s)
        self.sim = SimulationPanel(self.lab, self.s, self.canvas, self._log)
        self.tests = TestsPanel(self.lab, self.s, lambda: self.compute, self._log)
        self.tests.replay.connect(self._replay)
        self.analysis = AnalysisPanel(self.lab, self.s, lambda: self.compute, self._log)
        self.bottom.addTab(self.yaml, "Topology YAML")
        self.bottom.addTab(self.consoles, "Consoles")
        self.bottom.addTab(self.sim, "Simulation")
        self.bottom.addTab(self.tests, "Tests")
        self.bottom.addTab(self.analysis, "Analysis")
        self.bottom.addTab(self.logp, "Log")
        self.d_bottom = self._dock("Workbench", self.bottom, Qt.BottomDockWidgetArea, "dock_bottom")
        self.resizeDocks([self.d_pal, self.d_props], [190, 380], Qt.Horizontal)
        self.resizeDocks([self.d_bottom], [330], Qt.Vertical)

    def _act(self, text, slot, shortcut=None, icon=None, tip=None, checkable=False):
        a = QAction(text, self)
        if icon:
            a.setIcon(glyph_icon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("&New", self.new_file, "Ctrl+N", "new", "New empty topology")
        self.a_open = A("&Open…", self.open_file, "Ctrl+O", "open", "Open a topology file")
        self.a_save = A("&Save", self.save_file, "Ctrl+S", "save", "Save the topology")
        self.a_saveas = A("Save &As…", self.save_as, "Ctrl+Shift+S")
        self.a_undo = A("&Undo", lambda: self.lab.undo(), "Ctrl+Z", "undo")
        self.a_redo = A("&Redo", lambda: self.lab.redo(), "Ctrl+Y", "redo")
        self.a_del = A("&Delete selected", self.delete_selected, "Delete")
        self.a_select = A("Select / move", lambda: self.canvas.set_connect_mode(False), None, "select",
                          "Select and move devices")
        self.a_connect = A("Connect (cable)", self._toggle_connect, "C", "connect",
                           "Draw cables: click two devices (Esc to stop)", checkable=True)
        self.a_fit = A("&Fit to window", self.canvas.fit, "F", "fit")
        self.a_validate = A("&Validate", self.validate, "F6", "validate", "Check the topology for problems")
        self.a_tests = A("Run all &tests", self.run_tests, "F5", "test", "Run every test in the topology")
        self.a_restore = A("Restore all failures", self.lab.clear_failures, None, "restore",
                           "Bring every failed link and device back up")
        self.a_console = A("Open &console", self.console_selected, "Ctrl+T", "console")
        self.a_settings = A("&Settings / Preferences…", self.show_settings, "Ctrl+,", "settings")
        self.a_theme = A("Toggle dark / light theme", self.toggle_theme, "Ctrl+D")
        self.a_help = A("&Help…", lambda: self.show_help(), "F1", "help")
        self.a_export_clab = A("Containerlab (FRRouting)…", lambda: self.export("clab"), None, "export")
        self.a_export_kat = A("Kathará…", lambda: self.export("kathara"))
        self.a_export_png = A("Picture of the canvas (PNG)…", lambda: self.export("png"))
        for a in (self.a_select, self.a_connect, self.a_fit, self.a_del):
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.canvas.addAction(a)

    def _build_menus(self):
        mb = self.menubar
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.m_samples = m.addMenu("Open &sample")
        self.m_recent = m.addMenu("Open &recent")
        m.addSeparator()
        m.addAction(self.a_save)
        m.addAction(self.a_saveas)
        mx = m.addMenu("&Export")
        mx.addAction(self.a_export_clab)
        mx.addAction(self.a_export_kat)
        mx.addAction(self.a_export_png)
        m.addSeparator()
        m.addAction(self._act("E&xit", self.close, "Alt+F4"))
        m.aboutToShow.connect(self._fill_file_menus)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, self.a_del):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self._act("Select &all", lambda: [it.setSelected(True) for it in self.canvas.nodes.values()],
                              "Ctrl+A"))
        m.addAction(self.a_settings)
        m = mb.addMenu("&View")
        m.addAction(self.a_fit)
        m.addAction(self.a_theme)
        m.addSeparator()
        for d in (self.d_pal, self.d_props, self.d_bottom):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        m.addAction(self._act("Reset window layout", self._reset_layout))
        m = mb.addMenu("&Simulate")
        m.addAction(self.a_validate)
        m.addAction(self.a_tests)
        m.addAction(self._act("Ping / Simulation tab", lambda: self.bottom.setCurrentWidget(self.sim)))
        m.addSeparator()
        m.addAction(self.a_restore)
        m = mb.addMenu("&Tools")
        m.addAction(self.a_select)
        m.addAction(self.a_connect)
        m.addAction(self.a_console)
        m.addSeparator()
        m.addAction(self._act("Path matrix (CPU / GPU)", lambda: (self.bottom.setCurrentWidget(self.analysis),
                                                                  self.analysis.matrix())))
        m.addAction(self._act("Compute benchmark", lambda: (self.bottom.setCurrentWidget(self.analysis),
                                                           self.analysis.bench())))
        m.addAction(self._act("All routing tables", lambda: (self.bottom.setCurrentWidget(self.analysis),
                                                            self.analysis.tables())))
        m = mb.addMenu("E&mulation")
        m.addAction(self._act("Deploy on Containerlab", lambda: self.emulate("deploy")))
        m.addAction(self._act("Inspect running lab", lambda: self.emulate("inspect")))
        m.addAction(self._act("Destroy lab", lambda: self.emulate("destroy")))
        m.addAction(self._act("Stop running command", self.emulator.stop))
        m.addSeparator()
        m.addAction(self._act("Emulation settings…", lambda: self.show_settings("Emulation")))
        m = mb.addMenu("&Help")
        m.addAction(self.a_help)
        m.addAction(self._act("Getting started", lambda: self.show_help("Quick start")))
        m.addAction(self._act("Keyboard shortcuts", lambda: self.show_help("Keyboard shortcuts")))
        m.addSeparator()
        m.addAction(self._act("Terms of Use…", self.show_terms))
        m.addAction(self._act("Check for updates (Internet)", self.check_updates))
        m.addAction(self._act("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(local_dir()))))
        m.addSeparator()
        m.addAction(self._act("&About VU3INI PacketLab…", self.show_about))

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_select)
        tb.addAction(self.a_connect)
        tb.addAction(self.a_fit)
        tb.addSeparator()
        tb.addAction(self.a_validate)
        tb.addAction(self.a_tests)
        tb.addAction(self.a_restore)
        tb.addAction(self.a_console)
        tb.addSeparator()
        tb.addAction(self.a_export_clab)
        tb.addAction(self.a_settings)
        for a in (self.a_new, self.a_open, self.a_save, self.a_undo, self.a_redo, self.a_fit, self.a_restore,
                  self.a_export_clab, self.a_settings):
            btn = tb.widgetForAction(a)
            if btn is not None:
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
                btn.setToolTip(a.text().replace("&", "") + (f"  ({a.shortcut().toString()})"
                                                            if not a.shortcut().isEmpty() else ""))
        self.toolbar = tb

    def _build_status(self):
        sb = self.statusBar()
        self.footer = QLabel(FOOTER_TEXT)
        self.footer.setObjectName("footer")
        sb.addWidget(self.footer)
        self.st_valid = QLabel("")
        self.st_compute = QLabel("")
        self.st_fail = QLabel("")
        sb.addPermanentWidget(self.st_fail)
        sb.addPermanentWidget(self.st_valid)
        sb.addPermanentWidget(self.st_compute)
        self._update_status()

    # ------------------------------------------------------------------ theme
    def apply_theme(self):
        theme = self.s["theme"]
        acc = ACCENTS.get(self.s["accent"], "#1e88e5")
        QApplication.instance().setStyleSheet(stylesheet(theme, self.s["accent"], self.s["ui_font_family"],
                                                         int(self.s["ui_font_size"])))
        f = QFont(self.s["ui_font_family"], int(self.s["ui_font_size"]))
        QApplication.instance().setFont(f)
        pal = palette(theme)
        for a in self.toolbar.actions() if hasattr(self, "toolbar") else []:
            pass
        icon_col = pal["text"]
        names = {self.a_new: "new", self.a_open: "open", self.a_save: "save", self.a_undo: "undo", self.a_redo: "redo",
                 self.a_select: "select", self.a_connect: "connect", self.a_fit: "fit", self.a_validate: "validate",
                 self.a_tests: "test", self.a_restore: "restore", self.a_console: "console",
                 self.a_settings: "settings", self.a_help: "help", self.a_export_clab: "export"}
        for a, n in names.items():
            a.setIcon(glyph_icon(n, icon_col))
        self.btn_theme.setIcon(glyph_icon("sun" if theme == "dark" else "moon", icon_col))
        self.btn_help.setIcon(glyph_icon("help", icon_col))
        self.canvas.apply_theme(theme, acc)
        self.yaml.hl.setDocument(None)
        from .panels import YamlHighlighter
        self.yaml.hl = YamlHighlighter(self.yaml.ed.document(), theme)
        self.consoles.restyle()
        self.props.refresh()

    def toggle_theme(self):
        self.s["theme"] = "light" if self.s["theme"] == "dark" else "dark"
        self.s.save()
        self.apply_theme()

    # ------------------------------------------------------------------ lab events
    def _on_lab(self, what: str):
        if self._in_update:
            return
        self._in_update = True
        try:
            self.canvas.rebuild()
            self.props.refresh()
            if what != "state":
                self.yaml.load_from_lab()
                self.sim.refresh_nodes()
                if what in ("loaded", "tests", "node-renamed", "changed") or not self.tests.results:
                    self.tests.show_defined()
                self.consoles.renamed()
            if what == "loaded":
                QTimer.singleShot(50, self.canvas.fit)
            self._update_status()
        finally:
            self._in_update = False

    def _update_status(self):
        t = self.lab.topo
        pal = palette(self.s["theme"])
        if t.errors:
            self.st_valid.setText(f"<span style='color:{pal['bad']}'>✖ {len(t.errors)} problems</span>")
        else:
            self.st_valid.setText(f"<span style='color:{pal['ok']}'>✔ valid</span> · {len(t.nodes)} devices · "
                                  f"{len(t.links)} links")
        nf = len(self.lab.down_links) + len(self.lab.down_nodes)
        self.st_fail.setText(f"<span style='color:{pal['warn']}'>⚠ {nf} simulated failures</span>" if nf else "")
        dev = self.compute.mode.upper()
        self.st_compute.setText(f"Compute: {dev}")
        self.st_compute.setToolTip(self.compute.describe())
        name = os.path.basename(self.lab.path) if self.lab.path else (
            f"sample: {getattr(self, 'sample_name', '')}" if getattr(self, "sample_name", "") else "untitled")
        self.file_lbl.setText(name + (" •" if self.lab.dirty else ""))
        self.setWindowTitle(f"{name}{' *' if self.lab.dirty else ''} - {APP_NAME}")

    def _log(self, msg: str):
        if hasattr(self, "logp"):
            self.logp.add(msg)

    # ------------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        if not self.lab.dirty or not self.s["confirm_exit"]:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current topology?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_file()
        return r == QMessageBox.Discard

    def new_file(self):
        if not self._confirm_discard():
            return
        self.sample_name = ""
        self.lab.new()
        self.tests.show_defined()
        self._log("New topology")

    def open_file(self):
        if not self._confirm_discard():
            return
        start = self.s["projects_dir"] or documents_dir()
        path, _ = QFileDialog.getOpenFileName(self, "Open topology", start,
                                              "PacketLab topology (*.nstopo.yaml *.yaml *.yml);;All files (*)")
        if path:
            self.load(path)

    def load(self, path: str) -> bool:
        try:
            self.lab.load_file(path)
        except (OSError, TopologyError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
            return False
        is_sample = os.path.abspath(path).startswith(os.path.abspath(samples_dir()))
        self.sample_name = os.path.basename(path).replace(".nstopo.yaml", "") if is_sample else ""
        if is_sample:
            self.lab.path = None       # samples are read-only templates: Save asks for a new name
            self.lab.dirty = False
            self._log(f"Opened sample {os.path.basename(path)} (Save will ask for a new file name)")
        else:
            self.s.add_recent(path)
            self.s["last_project"] = path
            self.s.save()
            self._log(f"Opened {path}")
        self.lab.raw.setdefault("meta", {})
        self._update_status()
        self.tests.show_defined()
        t = self.lab.topo
        if t.errors:
            self._log(f"{len(t.errors)} validation problems - see Properties or the YAML tab")
        return True

    def save_file(self) -> bool:
        if not self.lab.path:
            return self.save_as()
        try:
            self.lab.save()
            self._log(f"Saved {self.lab.path}")
            self._update_status()
            return True
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{e}")
            return False

    def save_as(self) -> bool:
        name = (self.lab.raw.get("meta") or {}).get("name", "network")
        start = os.path.join(self.s["projects_dir"] or documents_dir(), f"{name}.nstopo.yaml")
        path, _ = QFileDialog.getSaveFileName(self, "Save topology", start, "PacketLab topology (*.nstopo.yaml)")
        if not path:
            return False
        if not path.endswith((".yaml", ".yml")):
            path += ".nstopo.yaml"
        self.lab.path = path
        self.sample_name = ""
        ok = self.save_file()
        if ok:
            self.s.add_recent(path)
            self.s["last_project"] = path
            self.s.save()
        return ok

    def _fill_file_menus(self):
        self.m_samples.clear()
        d = samples_dir()
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".yaml"):
                p = os.path.join(d, f)
                self.m_samples.addAction(f.replace(".nstopo.yaml", ""),
                                         lambda checked=False, x=p: self._confirm_discard() and self.load(x))
        self.m_recent.clear()
        rec = [p for p in self.s["recent"] if os.path.exists(p)]
        for p in rec:
            self.m_recent.addAction(p, lambda checked=False, x=p: self._confirm_discard() and self.load(x))
        if not rec:
            a = self.m_recent.addAction("(none)")
            a.setEnabled(False)

    def _autosave(self):
        if not self.lab.dirty:
            return
        try:
            name = os.path.basename(self.lab.path) if self.lab.path else "untitled.nstopo.yaml"
            d = local_dir("autosave")
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(self.lab.text())
        except OSError:
            pass

    def _restart_autosave(self):
        mins = int(self.s["autosave_minutes"])
        self.autosave.stop()
        if mins > 0:
            self.autosave.start(mins * 60_000)

    # ------------------------------------------------------------------ actions
    def _toggle_connect(self):
        on = self.a_connect.isChecked()
        self.canvas.set_connect_mode(on)
        self.canvas.setFocus()

    def delete_selected(self):
        nodes = self.canvas.selected_nodes()
        links = sorted(self.canvas.selected_links(), reverse=True)
        if not nodes and not links:
            return

        def fn(raw):
            for n in nodes:
                (raw.get("nodes") or {}).pop(n, None)
                (raw.get("layout") or {}).get("nodes", {}).pop(n, None)
            keep = []
            for i, l in enumerate(raw.get("links") or []):
                if i in links:
                    continue
                if any(str(l.get(s, "")).startswith(n + ":") for n in nodes for s in ("a", "b")):
                    continue
                keep.append(l)
            raw["links"] = keep
        self.lab.down_links.clear()
        self.lab.edit(fn, "deleted")
        self._log(f"Deleted {len(nodes)} devices and {len(links)} links")

    def validate(self):
        t = self.lab.topo
        self.props.show_summary()
        if t.errors:
            QMessageBox.warning(self, "Validation", f"{len(t.errors)} problems found:\n\n" + "\n".join(t.errors[:15]))
        else:
            QMessageBox.information(self, "Validation", f"The topology is valid: {len(t.nodes)} devices, "
                                                        f"{len(t.links)} links, {len(t.warnings)} warnings.")

    def run_tests(self):
        self.bottom.setCurrentWidget(self.tests)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.tests.run_all()
        finally:
            QApplication.restoreOverrideCursor()

    def console_selected(self):
        sel = self.canvas.selected_nodes()
        if sel:
            self.open_console(sel[0])
        else:
            QMessageBox.information(self, "Console", "Select a device first (or double-click it).")

    def open_console(self, node: str):
        self.bottom.setCurrentWidget(self.consoles)
        self.consoles.open(node)
        self.d_bottom.show()

    def _replay(self, spec: dict):
        typ = spec.get("type")
        proto = {"ping": "ICMP ping", "traceroute": "Traceroute", "tcp.connect": "TCP connect",
                 "udp.reach": "UDP"}.get(typ, "ICMP ping")
        self.sim.preset(str(spec.get("from")), str(spec.get("to")), proto, int(spec.get("port", 443)))
        self.bottom.setCurrentWidget(self.sim)
        if typ == "traceroute":
            self.sim.realtime()
        else:
            self.sim.simulate()

    def _reset_layout(self):
        for d in (self.d_pal, self.d_props, self.d_bottom):
            d.setFloating(False)
            d.show()
        self.addDockWidget(Qt.LeftDockWidgetArea, self.d_pal)
        self.addDockWidget(Qt.RightDockWidgetArea, self.d_props)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.d_bottom)
        self.resizeDocks([self.d_pal, self.d_props], [190, 380], Qt.Horizontal)
        self.resizeDocks([self.d_bottom], [330], Qt.Vertical)

    def export(self, kind: str):
        base = self.s["export_dir"] or (os.path.splitext(self.lab.path)[0].replace(".nstopo", "") + "_export"
                                        if self.lab.path else os.path.join(documents_dir(), "exports"))
        if kind == "png":
            path, _ = QFileDialog.getSaveFileName(self, "Save picture", os.path.join(documents_dir(), "network.png"),
                                                  "PNG (*.png)")
            if path:
                self.canvas.to_image().save(path)
                self._log(f"Saved picture {path}")
            return
        folder = QFileDialog.getExistingDirectory(self, "Export to folder", os.path.dirname(base) or documents_dir())
        if not folder:
            return
        try:
            if kind == "clab":
                out = os.path.join(folder, "containerlab")
                p = compiler.to_containerlab(self.lab.topo, out, images={
                    "router": self.s["image_router"], "l3switch": self.s["image_router"],
                    "firewall": self.s["image_router"], "cloud": self.s["image_router"],
                    "host": self.s["image_host"], "server": self.s["image_host"], "switch": self.s["image_host"]})
            else:
                out = os.path.join(folder, "kathara")
                p = compiler.to_kathara(self.lab.topo, out)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export", f"Export failed:\n{e}")
            return
        self.last_export = p
        self._log(f"Exported to {p}")
        QMessageBox.information(self, "Export", f"Exported to:\n{p}")

    def emulate(self, action: str):
        self.emulator.s = self.s.v
        ok = self.emulator.available()
        if ok != "ok":
            QMessageBox.information(self, "Emulation", f"Emulation is not ready: {ok}.\n\nSee Help > Emulation.")
            return
        if action == "deploy" or not getattr(self, "last_export", None):
            folder = os.path.join(local_dir("emulation"), (self.lab.raw.get("meta") or {}).get("name", "lab"))
            self.last_export = compiler.to_containerlab(self.lab.topo, folder, images={
                "router": self.s["image_router"], "l3switch": self.s["image_router"], "firewall": self.s["image_router"],
                "cloud": self.s["image_router"], "host": self.s["image_host"], "server": self.s["image_host"],
                "switch": self.s["image_host"]})
            up = self.emulator.upload_command(os.path.dirname(self.last_export))
            if up:
                self.bottom.setCurrentWidget(self.logp)
                self.emulator.run(up, lambda rc: rc == 0 and self.emulator.run(
                    self.emulator.command(action, self.last_export)))
                return
        self.bottom.setCurrentWidget(self.logp)
        self.emulator.run(self.emulator.command(action, self.last_export))

    # ------------------------------------------------------------------ dialogs
    def show_settings(self, tab: Optional[str] = None):
        dlg = dialogs.SettingsDialog(self.s, self)
        if tab:
            for i in range(dlg.tabs.count()):
                if dlg.tabs.tabText(i) == tab:
                    dlg.tabs.setCurrentIndex(i)
        dlg.applied.connect(self._settings_applied)
        dlg.exec()

    def _settings_applied(self):
        self.compute = self._make_compute()
        self.lab.compute = self.compute
        self.lab.settings = self.s.engine_settings()
        self.lab._engine = None
        self.emulator.s = self.s.v
        self.apply_theme()
        self.yaml.ed.setFont(QFont(self.s["editor_font"], int(self.s["editor_font_size"])))
        self._restart_autosave()
        self._update_status()
        self._log(f"Settings applied. Compute: {self.compute.describe()}")

    def show_help(self, topic: str = ""):
        dialogs.HelpDialog(self, topic).exec()

    def show_about(self):
        dialogs.AboutDialog(self.compute.describe(), self).exec()

    def show_terms(self):
        dlg = dialogs.SplashDialog(self.s, self)
        dlg.exec()
        if dlg.declined:
            self.s["confirm_exit"] = False
            QApplication.quit()

    def check_updates(self):
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            msg = dialogs.check_updates(self.s["update_url"])
        except Exception as e:  # noqa: BLE001
            msg = f"Could not check for updates: {e}"
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Updates", msg)

    # ------------------------------------------------------------------ close
    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.s["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.s["state"] = bytes(self.saveState().toBase64()).decode()
        self.s.save()
        super().closeEvent(ev)
