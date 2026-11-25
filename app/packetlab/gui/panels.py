"""Dockable panels of the main window."""
from __future__ import annotations

import html
import ipaddress
import os
import time
from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import QMimeData, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QDrag, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSlider,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import compiler, pcapng, runner
from ..core.cli import WORDS, Console
from ..core.lab import Lab
from ..core.model import KINDS, TopologyError, fmt_bw
from .canvas import MIME_KIND
from .icons import KIND_LABELS, device_icon
from .theme import palette

PALETTE_ORDER = ["router", "l3switch", "switch", "firewall", "host", "server", "ap", "cloud", "external"]


# =========================================================================== palette
class DevicePalette(QListWidget):
    place = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(40, 40))
        self.setDragEnabled(True)
        self.setSpacing(2)
        for k in PALETTE_ORDER:
            it = QListWidgetItem(device_icon(k), KIND_LABELS[k])
            it.setData(Qt.UserRole, k)
            it.setToolTip(f"Drag onto the canvas, or click and then click the canvas to place a {KIND_LABELS[k]}")
            self.addItem(it)
        self.itemClicked.connect(lambda it: self.place.emit(it.data(Qt.UserRole)))

    def startDrag(self, actions):
        it = self.currentItem()
        if not it:
            return
        md = QMimeData()
        md.setData(MIME_KIND, it.data(Qt.UserRole).encode())
        d = QDrag(self)
        d.setMimeData(md)
        d.setPixmap(it.icon().pixmap(48, 48))
        d.exec(Qt.CopyAction)


# =========================================================================== properties
class PropertiesPanel(QWidget):
    open_console = Signal(str)

    def __init__(self, lab: Lab, settings, log, parent=None):
        super().__init__(parent)
        self.lab, self.s, self.log = lab, settings, log
        self.kind_sel: Optional[str] = None
        self.sel = None
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("Network")
        self.title.setObjectName("title")
        lay.addWidget(self.title)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.body, 1)
        self.show_summary()

    def _clear(self):
        while self.body_lay.count():
            it = self.body_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def refresh(self):
        if self.kind_sel == "node" and self.sel in self.lab.topo.nodes:
            self.show_node(self.sel)
        elif self.kind_sel == "link" and isinstance(self.sel, int) and self.sel < len(self.lab.topo.links):
            self.show_link(self.sel)
        else:
            self.show_summary()

    # ------------------------------------------------------------------ summary
    def show_summary(self):
        self.kind_sel, self.sel = None, None
        self._clear()
        t = self.lab.topo
        self.title.setText((t.meta or {}).get("name", "Network"))
        info = QLabel()
        info.setWordWrap(True)
        kinds: Dict[str, int] = {}
        for n in t.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        scopes = {}
        for s in (t.sites or {}).values():
            sc = (s or {}).get("scope", "lan")
            scopes[sc] = scopes.get(sc, 0) + 1
        txt = (f"<b>{len(t.nodes)}</b> devices, <b>{len(t.links)}</b> links, <b>{len(t.sites)}</b> sites "
               f"({', '.join(f'{v} {k.upper()}' for k, v in scopes.items()) or 'none'}), "
               f"<b>{len(t.tests)}</b> tests, <b>{len(t.scenarios)}</b> scenarios.<br>"
               + ", ".join(f"{v} {KIND_LABELS.get(k, k).lower()}" for k, v in sorted(kinds.items())))
        if (t.meta or {}).get("description"):
            txt += f"<p style='opacity:.8'>{html.escape(str(t.meta['description']))}</p>"
        info.setText(txt)
        self.body_lay.addWidget(info)
        box = QGroupBox(f"Validation: {len(t.errors)} problems, {len(t.warnings)} warnings")
        bl = QVBoxLayout(box)
        lst = QListWidget()
        pal = palette(self.s["theme"])
        for e in t.errors:
            it = QListWidgetItem("✖ " + e)
            it.setForeground(QBrush(QColor(pal["bad"])))
            lst.addItem(it)
        for w in t.warnings:
            it = QListWidgetItem("⚠ " + w)
            it.setForeground(QBrush(QColor(pal["warn"])))
            lst.addItem(it)
        if not t.errors and not t.warnings:
            lst.addItem("✔ The topology is valid.")
        lst.setWordWrap(True)
        bl.addWidget(lst)
        self.body_lay.addWidget(box, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Add site…")
        b1.clicked.connect(self.add_site)
        b2 = QPushButton("Add link profile…")
        b2.clicked.connect(self.add_profile)
        row.addWidget(b1)
        row.addWidget(b2)
        w = QWidget()
        w.setLayout(row)
        self.body_lay.addWidget(w)
        if self.lab.down_links or self.lab.down_nodes:
            b = QPushButton(f"Restore {len(self.lab.down_links)} links / {len(self.lab.down_nodes)} devices")
            b.clicked.connect(self.lab.clear_failures)
            self.body_lay.addWidget(b)

    def add_site(self):
        sid, ok = QInputDialog.getText(self, "New site", "Site id (no spaces):")
        if not ok or not sid.strip():
            return
        sid = sid.strip().replace(" ", "-")
        scope, ok = QInputDialog.getItem(self, "New site", "Scope:", ["lan", "man", "wan"], 0, False)
        if not ok:
            return
        label, ok = QInputDialog.getText(self, "New site", "Label shown on the canvas:", text=sid)
        self.lab.edit(lambda raw: raw.setdefault("sites", {}).__setitem__(sid, {"scope": scope, "label": label or sid}),
                      "site")
        return sid

    def add_profile(self):
        pid, ok = QInputDialog.getText(self, "New link profile", "Profile name:", text="wan-100m")
        if not ok or not pid.strip():
            return
        bw, ok = QInputDialog.getText(self, "New link profile", "Bandwidth (e.g. 100Mbps, 10Gbps):", text="100Mbps")
        if not ok:
            return
        delay, ok = QInputDialog.getText(self, "New link profile", "One-way delay (e.g. 5ms):", text="5ms")
        if not ok:
            return
        jitter, _ = QInputDialog.getText(self, "New link profile", "Jitter (e.g. 1ms, blank = none):", text="")
        loss, _ = QInputDialog.getText(self, "New link profile", "Loss (e.g. 0.1%, blank = none):", text="")
        prof = {"bandwidth": bw, "delay": delay}
        if jitter.strip():
            prof["jitter"] = jitter.strip()
        if loss.strip():
            prof["loss"] = loss.strip()
        self.lab.edit(lambda raw: raw.setdefault("link_profiles", {}).__setitem__(pid.strip(), prof), "profile")

    # ------------------------------------------------------------------ node
    def show_node(self, name: str):
        self.kind_sel, self.sel = "node", name
        self._clear()
        n = self.lab.topo.nodes.get(name)
        if n is None:
            return self.show_summary()
        raw = self.lab.node_raw(name)
        self.title.setText(f"{name}")
        form = QFormLayout()
        f = QWidget()
        f.setLayout(form)
        ed_name = QLineEdit(name)
        ed_name.editingFinished.connect(lambda: self._rename(name, ed_name.text().strip()))
        form.addRow("Name", ed_name)
        cb_kind = QComboBox()
        cb_kind.addItems(sorted(KINDS))
        cb_kind.setCurrentText(n.kind)
        cb_kind.currentTextChanged.connect(lambda v: self._set(name, "kind", v))
        form.addRow("Kind", cb_kind)
        cb_site = QComboBox()
        cb_site.addItem("(none)")
        cb_site.addItems(list((self.lab.topo.sites or {}).keys()))
        cb_site.addItem("New site…")
        cb_site.setCurrentText(n.site or "(none)")
        cb_site.currentTextChanged.connect(lambda v: self._site(name, v))
        form.addRow("Site", cb_site)
        ed_role = QLineEdit(n.role)
        ed_role.editingFinished.connect(lambda: self._set(name, "role", ed_role.text().strip() or None))
        form.addRow("Role", ed_role)
        cb_mode = QComboBox()
        cb_mode.addItems(["sim", "emu", "hybrid"])
        cb_mode.setCurrentText(n.mode)
        cb_mode.currentTextChanged.connect(lambda v: self._set(name, "mode", v))
        form.addRow("Run mode", cb_mode)
        cb_cli = QComboBox()
        cb_cli.addItems(["ios", "junos", "frr"])
        cb_cli.setCurrentText(n.cli_style)
        cb_cli.currentTextChanged.connect(lambda v: self._set(name, "cli_style", v))
        form.addRow("CLI style", cb_cli)
        if n.bridges:
            sp = QSpinBox()
            sp.setRange(0, 61440)
            sp.setSingleStep(4096)
            sp.setValue(int((n.stp or {}).get("priority", 32768)))
            sp.editingFinished.connect(lambda: self._set_stp(name, sp.value()))
            form.addRow("STP priority", sp)
            vl = QLineEdit(", ".join(f"{k}:{v}" for k, v in sorted(n.vlans.items())))
            vl.setToolTip("VLAN list as id:name, separated by commas")
            vl.editingFinished.connect(lambda: self._set_vlans(name, vl.text()))
            form.addRow("VLANs", vl)
        self.body_lay.addWidget(f)
        # interfaces table
        box = QGroupBox("Interfaces")
        bl = QVBoxLayout(box)
        cols = ["Interface", "Mode", "IPv4", "VLAN", "Allowed", "Gateway", "Shutdown"]
        tb = QTableWidget(0, len(cols))
        tb.setHorizontalHeaderLabels(cols)
        tb.verticalHeader().setVisible(False)
        tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tb.horizontalHeader().setStretchLastSection(True)
        eng = self.lab.engine
        for ifn, ifc in sorted(n.ifaces.items()):
            r = tb.rowCount()
            tb.insertRow(r)
            v4 = ifc.raw.get("ipv4")
            l3 = eng.l3.get((name, ifn))
            shown = str(v4) if v4 is not None else ""
            if v4 in ("auto", "dhcp") and l3 is not None:
                shown = f"{v4} -> {l3.ip}"
            vals = [ifn, ifc.mode, shown, str(ifc.vlan) if ifc.mode == "access" else "",
                    ("all" if ifc.allowed is None else ",".join(map(str, sorted(ifc.allowed)))) if ifc.mode == "trunk"
                    else "", str(ifc.gateway or ""), ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c == 6:
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    it.setCheckState(Qt.Checked if ifc.shutdown else Qt.Unchecked)
                tb.setItem(r, c, it)
        tb.cellChanged.connect(lambda r, c: self._iface_cell(name, tb, r, c))
        bl.addWidget(tb)
        row = QHBoxLayout()
        b_add = QPushButton("Add interface")
        b_add.clicked.connect(lambda: self._add_iface(name))
        b_con = QPushButton("Open console")
        b_con.clicked.connect(lambda: self.open_console.emit(name))
        b_pw = QPushButton("Power on" if name in self.lab.down_nodes else "Power off")
        b_pw.clicked.connect(lambda: self.lab.set_failure(node=name, down=name not in self.lab.down_nodes))
        for b in (b_add, b_con, b_pw):
            row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        bl.addWidget(w)
        self.body_lay.addWidget(box, 2)
        # advanced YAML for this node
        adv = QGroupBox("Advanced (YAML of this device)")
        al = QVBoxLayout(adv)
        from ..core.model import dump_text
        ed = QPlainTextEdit(dump_text(raw))
        ed.setFont(QFont(self.s["editor_font"], int(self.s["editor_font_size"])))
        YamlHighlighter(ed.document(), self.s["theme"])
        al.addWidget(ed)
        b_ap = QPushButton("Apply device YAML")
        b_ap.clicked.connect(lambda: self._apply_node_yaml(name, ed.toPlainText()))
        al.addWidget(b_ap)
        self.body_lay.addWidget(adv, 2)

    def _rename(self, old, new):
        if not new or new == old:
            return
        try:
            self.lab.rename_node(old, new)
            self.sel = new
        except ValueError as e:
            QMessageBox.warning(self, "Rename", str(e))

    def _set(self, name, key, value):
        if self._busy:
            return

        def fn(raw):
            nd = self.lab.node_raw(name)
            if value is None:
                nd.pop(key, None)
            else:
                nd[key] = value
        self.lab.edit(fn, "props")

    def _site(self, name, v):
        if v == "New site…":
            sid = self.add_site()
            if sid:
                self._set(name, "site", sid)
            return
        self._set(name, "site", None if v == "(none)" else v)

    def _set_stp(self, name, prio):
        self.lab.edit(lambda raw: self.lab.node_raw(name).setdefault("stp", {}).__setitem__("priority", int(prio)),
                      "props")

    def _set_vlans(self, name, text):
        vl = {}
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                a, b = part.split(":", 1)
            else:
                a, b = part, f"VLAN{part}"
            try:
                vl[int(a)] = b.strip()
            except ValueError:
                QMessageBox.warning(self, "VLANs", f"'{part}' is not id:name")
                return
        self.lab.edit(lambda raw: self.lab.node_raw(name).__setitem__("vlans", vl), "props")

    def _iface_cell(self, name, tb, r, c):
        ifn = tb.item(r, 0).text()
        val = tb.item(r, c).text().strip() if c != 6 else tb.item(r, c).checkState() == Qt.Checked
        key = {1: "mode", 2: "ipv4", 3: "vlan", 4: "allowed_vlans", 5: "gateway", 6: "shutdown"}[c]
        if key == "ipv4" and "->" in str(val):
            val = str(val).split("->")[0].strip()

        def fn(raw):
            d = self.lab.iface_raw(name, ifn)
            if val in ("", None, False):
                d.pop(key, None)
            elif key == "vlan":
                d[key] = int(val)
            elif key == "allowed_vlans":
                d[key] = "all" if val == "all" else [x.strip() for x in str(val).split(",") if x.strip()]
            else:
                d[key] = val
        try:
            QTimer.singleShot(0, lambda: self.lab.edit(fn, "props"))
        except ValueError as e:
            QMessageBox.warning(self, "Interface", str(e))

    def _add_iface(self, name):
        nxt = self.lab.next_iface(name)
        ifn, ok = QInputDialog.getText(self, "Add interface", "Interface name (e.g. Gi0/2, Vlan10, Lo0):", text=nxt)
        if ok and ifn.strip():
            self.lab.edit(lambda raw: self.lab.iface_raw(name, ifn.strip()), "props")

    def _apply_node_yaml(self, name, text):
        import yaml
        try:
            d = yaml.safe_load(text) or {}
            if not isinstance(d, dict):
                raise ValueError("device YAML must be a mapping")
        except (yaml.YAMLError, ValueError) as e:
            QMessageBox.warning(self, "Device YAML", f"Not applied:\n{e}")
            return
        self.lab.edit(lambda raw: raw.setdefault("nodes", {}).__setitem__(name, d), "props")

    # ------------------------------------------------------------------ link
    def show_link(self, idx: int):
        self.kind_sel, self.sel = "link", idx
        self._clear()
        t = self.lab.topo
        if idx >= len(t.links):
            return self.show_summary()
        l = t.links[idx]
        raw = (self.lab.raw.get("links") or [])[idx]
        self.title.setText(f"Link {l.label or idx + 1}")
        form = QFormLayout()
        f = QWidget()
        f.setLayout(form)
        form.addRow("End A", QLabel(f"{l.a_node} : {l.a_if}"))
        form.addRow("End B", QLabel(f"{l.b_node} : {l.b_if}"))
        cb = QComboBox()
        cb.addItem("(default 1Gbps)")
        cb.addItems(list(t.profiles))
        cb.setCurrentText(raw.get("profile") or "(default 1Gbps)")
        cb.currentTextChanged.connect(lambda v: self._link_set(idx, "profile", None if v.startswith("(") else v))
        form.addRow("Profile", cb)
        ty = QComboBox()
        ty.addItems(["copper", "fibre", "wan-circuit", "wireless", "serial"])
        ty.setCurrentText(str(raw.get("type", l.profile.media)))
        ty.currentTextChanged.connect(lambda v: self._link_set(idx, "type", v))
        form.addRow("Type", ty)
        km = QDoubleSpinBox()
        km.setRange(0, 40000)
        km.setDecimals(2)
        km.setSuffix(" km")
        km.setValue(float(raw.get("length_km") or 0))
        km.editingFinished.connect(lambda: self._link_set(idx, "length_km", km.value() or None))
        form.addRow("Length", km)
        lb = QLineEdit(raw.get("label", ""))
        lb.editingFinished.connect(lambda: self._link_set(idx, "label", lb.text().strip() or None))
        form.addRow("Label", lb)
        eng = self.lab.engine
        up = eng.link_up(l)
        form.addRow("State", QLabel("<b>UP</b>" if up else "<b style='color:#e53935'>DOWN</b>"))
        form.addRow("Characteristics", QLabel(f"{fmt_bw(l.profile.bandwidth)}, {l.delay_ms:.3f} ms delay, "
                                              f"{l.profile.jitter_ms:g} ms jitter, {l.profile.loss * 100:g}% loss"))
        self.body_lay.addWidget(f)
        row = QHBoxLayout()
        b = QPushButton("Bring link up" if idx in self.lab.down_links else "Bring link down")
        b.clicked.connect(lambda: self.lab.set_failure(link=idx, down=idx not in self.lab.down_links))
        d = QPushButton("Delete link")
        d.setObjectName("danger")
        d.clicked.connect(lambda: self.lab.delete_link(idx))
        row.addWidget(b)
        row.addWidget(d)
        w = QWidget()
        w.setLayout(row)
        self.body_lay.addWidget(w)
        self.body_lay.addStretch(1)

    def _link_set(self, idx, key, value):
        def fn(raw):
            d = raw["links"][idx]
            if value is None:
                d.pop(key, None)
            else:
                d[key] = value
        self.lab.edit(fn, "props")


# =========================================================================== YAML editor
class YamlHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, theme: str):
        super().__init__(doc)
        dark = theme == "dark"

        def fmt(c, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(c))
            if bold:
                f.setFontWeight(QFont.Bold)
            f.setFontItalic(italic)
            return f
        self.rules = [
            (QRegularExpression(r"^\s*-?\s*[A-Za-z0-9_\-/\.]+(?=\s*:)"), fmt("#64b5f6" if dark else "#1565c0", True)),
            (QRegularExpression(r"[{,]\s*[A-Za-z0-9_\-/\.]+(?=\s*:)"), fmt("#64b5f6" if dark else "#1565c0")),
            (QRegularExpression(r"\b\d+(\.\d+)?(ms|us|s|Gbps|Mbps|kbps|bps|%)?\b"), fmt("#ffb74d" if dark else "#e65100")),
            (QRegularExpression(r"\b(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?\b"), fmt("#81c784" if dark else "#2e7d32")),
            (QRegularExpression(r"\b(true|false|auto|dhcp|all|null)\b"), fmt("#ce93d8" if dark else "#6a1b9a", True)),
            (QRegularExpression(r"\"[^\"]*\"|'[^']*'"), fmt("#a5d6a7" if dark else "#33691e")),
            (QRegularExpression(r"#.*$"), fmt("#78909c", italic=True)),
        ]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class YamlPanel(QWidget):
    def __init__(self, lab: Lab, settings, log, parent=None):
        super().__init__(parent)
        self.lab, self.s, self.log = lab, settings, log
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.b_apply = QPushButton("Apply  (Ctrl+Enter)")
        self.b_apply.setObjectName("primary")
        self.b_apply.clicked.connect(self.apply)
        self.b_revert = QPushButton("Revert")
        self.b_revert.clicked.connect(self.load_from_lab)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bar.addWidget(self.b_apply)
        bar.addWidget(self.b_revert)
        bar.addWidget(self.status, 1)
        lay.addLayout(bar)
        split = QSplitter(Qt.Vertical)
        self.ed = QPlainTextEdit()
        self.ed.setFont(QFont(self.s["editor_font"], int(self.s["editor_font_size"])))
        self.ed.setLineWrapMode(QPlainTextEdit.WidgetWidth if self.s["editor_wrap"] else QPlainTextEdit.NoWrap)
        self.ed.setTabStopDistance(self.ed.fontMetrics().horizontalAdvance(" ") * 2)
        self.hl = YamlHighlighter(self.ed.document(), self.s["theme"])
        self.errs = QListWidget()
        self.errs.setMaximumHeight(110)
        split.addWidget(self.ed)
        split.addWidget(self.errs)
        split.setSizes([400, 80])
        lay.addWidget(split, 1)
        QShortcut(QKeySequence("Ctrl+Return"), self.ed, activated=self.apply)
        QShortcut(QKeySequence("Ctrl+Enter"), self.ed, activated=self.apply)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.validate)
        self.ed.textChanged.connect(self._changed)
        self._loading = False
        self._last = ""
        self.load_from_lab()

    def _changed(self):
        if self._loading or self.ed.toPlainText() == self._last:
            return
        self.status.setText("modified - press Apply")
        if self.s["live_validate"]:
            self.timer.start(int(self.s["live_validate_ms"]))

    def load_from_lab(self):
        self._loading = True
        sb = self.ed.verticalScrollBar().value()
        self._last = self.lab.text()
        self.ed.setPlainText(self._last)
        self.ed.verticalScrollBar().setValue(sb)
        self._loading = False
        self.status.setText("in sync with canvas")
        self._show_errors(self.lab.topo.errors, self.lab.topo.warnings)

    def _show_errors(self, errors, warnings):
        self.errs.clear()
        pal = palette(self.s["theme"])
        for e in errors:
            it = QListWidgetItem("✖ " + e)
            it.setForeground(QBrush(QColor(pal["bad"])))
            self.errs.addItem(it)
        for w in warnings:
            it = QListWidgetItem("⚠ " + w)
            it.setForeground(QBrush(QColor(pal["warn"])))
            self.errs.addItem(it)
        if not errors and not warnings:
            self.errs.addItem("✔ valid")

    def validate(self):
        from ..core import model
        try:
            t = model.load_text(self.ed.toPlainText())
            self._show_errors(t.errors, t.warnings)
        except TopologyError as e:
            self._show_errors([str(e)], [])

    def apply(self):
        try:
            self.lab.load_text(self.ed.toPlainText(), keep_undo=True)
            self.log("YAML applied")
            self.status.setText("applied")
        except TopologyError as e:
            QMessageBox.warning(self, "YAML", str(e))


# =========================================================================== consoles
class ConsoleWidget(QPlainTextEdit):
    def __init__(self, console: Console, settings, parent=None):
        super().__init__(parent)
        self.c, self.s = console, settings
        self.hist: List[str] = []
        self.hpos = 0
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(int(settings["console_font_size"]))
        self.setFont(f)
        self.setStyleSheet(f"QPlainTextEdit {{ background: {settings['console_bg']}; color: {settings['console_fg']}; "
                           f"border: none; }}")
        self.setUndoRedoEnabled(False)
        self.insertPlainText(console.banner() + "\n")
        self._prompt()

    def _prompt(self):
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(self.c.prompt())
        self.start = self.textCursor().position()
        self.ensureCursorVisible()

    def _line(self) -> str:
        doc = self.toPlainText()
        return doc[self.start:]

    def keyPressEvent(self, ev):
        cur = self.textCursor()
        if cur.position() < self.start and ev.key() not in (Qt.Key_C,) and not ev.matches(QKeySequence.Copy):
            self.moveCursor(QTextCursor.End)
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            line = self._line()
            self.moveCursor(QTextCursor.End)
            self.insertPlainText("\n")
            if line.strip():
                self.hist.append(line)
                self.hist = self.hist[-int(self.s["console_history"]):]
            self.hpos = len(self.hist)
            out = self.c.run(line)
            if out == "\f":
                self.clear()
            elif out:
                self.insertPlainText(out + "\n")
            self._prompt()
            return
        if ev.key() == Qt.Key_Backspace and self.textCursor().position() <= self.start:
            return
        if ev.key() == Qt.Key_Left and self.textCursor().position() <= self.start:
            return
        if ev.key() == Qt.Key_Home:
            c = self.textCursor()
            c.setPosition(self.start)
            self.setTextCursor(c)
            return
        if ev.key() in (Qt.Key_Up, Qt.Key_Down):
            if not self.hist:
                return
            self.hpos = max(0, min(len(self.hist), self.hpos + (-1 if ev.key() == Qt.Key_Up else 1)))
            txt = self.hist[self.hpos] if self.hpos < len(self.hist) else ""
            c = self.textCursor()
            c.setPosition(self.start)
            c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            c.insertText(txt)
            return
        if ev.key() == Qt.Key_Tab:
            line = self._line()
            parts = line.split(" ")
            last = parts[-1].lower()
            cands = [w for w in WORDS if w.startswith(last)] if last else []
            if self.c.n is not None:
                cands += [i for i in self.c.n.ifaces if i.lower().startswith(last)]
            if len(cands) == 1:
                self.insertPlainText(cands[0][len(last):] + " ")
            elif cands:
                self.insertPlainText("\n" + "  ".join(sorted(set(cands))) + "\n")
                self._prompt()
                self.insertPlainText(line)
            return
        if ev.key() == Qt.Key_L and ev.modifiers() & Qt.ControlModifier:
            self.clear()
            self._prompt()
            return
        super().keyPressEvent(ev)


class ConsolePanel(QTabWidget):
    def __init__(self, lab: Lab, settings, parent=None):
        super().__init__(parent)
        self.lab, self.s = lab, settings
        self.setTabsClosable(True)
        self.tabCloseRequested.connect(lambda i: self.removeTab(i))
        self.setDocumentMode(True)
        self.empty = QLabel("Double-click a device on the canvas (or press Ctrl+T) to open its console.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")
        self.addTab(self.empty, "Consoles")
        self.tabBar().setTabButton(0, self.tabBar().ButtonPosition.RightSide, None)

    def open(self, node: str):
        for i in range(self.count()):
            w = self.widget(i)
            if isinstance(w, ConsoleWidget) and w.c.node == node:
                self.setCurrentIndex(i)
                w.setFocus()
                return
        w = ConsoleWidget(Console(self.lab, node), self.s)
        i = self.addTab(w, node)
        self.setCurrentIndex(i)
        w.setFocus()

    def renamed(self):
        for i in range(self.count()):
            w = self.widget(i)
            if isinstance(w, ConsoleWidget):
                self.setTabText(i, w.c.node)

    def restyle(self):
        for i in range(self.count()):
            w = self.widget(i)
            if isinstance(w, ConsoleWidget):
                w.setStyleSheet(f"QPlainTextEdit {{ background: {self.s['console_bg']}; color: {self.s['console_fg']};"
                                f" border: none; }}")


# =========================================================================== simulation
class SimulationPanel(QWidget):
    def __init__(self, lab: Lab, settings, canvas, log, parent=None):
        super().__init__(parent)
        self.lab, self.s, self.canvas, self.log = lab, settings, canvas, log
        self.events = []
        self.pos = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.src = QComboBox()
        self.dst = QComboBox()
        self.dst.setEditable(True)
        self.proto = QComboBox()
        self.proto.addItems(["ICMP ping", "Traceroute", "TCP connect", "UDP"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(443)
        self.b_rt = QPushButton("Realtime")
        self.b_rt.setObjectName("primary")
        self.b_rt.clicked.connect(self.realtime)
        self.b_sim = QPushButton("Simulate")
        self.b_sim.clicked.connect(self.simulate)
        for w, lbl in ((self.src, "From"), (self.dst, "To"), (self.proto, ""), (self.port, "port")):
            if lbl:
                bar.addWidget(QLabel(lbl))
            bar.addWidget(w, 1 if w in (self.src, self.dst) else 0)
        bar.addWidget(self.b_rt)
        bar.addWidget(self.b_sim)
        lay.addLayout(bar)
        bar2 = QHBoxLayout()
        self.b_first = QPushButton("⏮")
        self.b_prev = QPushButton("◀ Step")
        self.b_play = QPushButton("▶ Play")
        self.b_next = QPushButton("Step ▶")
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 20)
        self.speed.setValue(6)
        self.speed.setMaximumWidth(140)
        self.b_pcap = QPushButton("Save .pcapng")
        self.chk_arp = QCheckBox("ARP")
        self.chk_arp.setChecked(bool(self.s["show_arp_events"]))
        self.chk_l2 = QCheckBox("Switch hops")
        self.chk_l2.setChecked(bool(self.s["show_l2_events"]))
        for b in (self.b_first, self.b_prev, self.b_play, self.b_next):
            bar2.addWidget(b)
        bar2.addWidget(QLabel("speed"))
        bar2.addWidget(self.speed)
        bar2.addWidget(self.chk_arp)
        bar2.addWidget(self.chk_l2)
        bar2.addStretch(1)
        bar2.addWidget(self.b_pcap)
        lay.addLayout(bar2)
        self.b_first.clicked.connect(lambda: self.goto(0))
        self.b_prev.clicked.connect(lambda: self.goto(self.pos - 1))
        self.b_next.clicked.connect(lambda: self.goto(self.pos + 1))
        self.b_play.clicked.connect(self.toggle_play)
        self.b_pcap.clicked.connect(self.save_pcap)
        self.chk_arp.toggled.connect(lambda _: self._fill_events(self.all_events))
        self.chk_l2.toggled.connect(lambda _: self._fill_events(self.all_events))
        self.all_events = []
        self.result = QLabel("Choose a source and a destination, then press Realtime or Simulate.")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result)
        split = QSplitter(Qt.Horizontal)
        self.tab = QTableWidget(0, 5)
        self.tab.setHorizontalHeaderLabels(["Time (ms)", "At device", "Layer", "Type", "Summary"])
        self.tab.verticalHeader().setVisible(False)
        self.tab.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tab.horizontalHeader().setStretchLastSection(True)
        self.tab.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tab.itemSelectionChanged.connect(self._sel)
        right = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["PDU field", "Value"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.hex = QPlainTextEdit()
        self.hex.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        self.hex.setFont(f)
        right.addWidget(self.tree)
        right.addWidget(self.hex)
        split.addWidget(self.tab)
        split.addWidget(right)
        split.setSizes([620, 380])
        lay.addWidget(split, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.refresh_nodes()

    def refresh_nodes(self):
        cur_s, cur_d = self.src.currentText(), self.dst.currentText()
        names = sorted(self.lab.topo.nodes)
        self.src.blockSignals(True)
        self.dst.blockSignals(True)
        self.src.clear()
        self.dst.clear()
        self.src.addItems(names)
        self.dst.addItems(names)
        if cur_s in names:
            self.src.setCurrentText(cur_s)
        if cur_d:
            self.dst.setCurrentText(cur_d)
        elif len(names) > 1:
            self.dst.setCurrentIndex(1)
        self.src.blockSignals(False)
        self.dst.blockSignals(False)

    def preset(self, src: str, dst: str, proto: str = "ICMP ping", port: int = 443):
        self.src.setCurrentText(src)
        self.dst.setCurrentText(str(dst))
        self.proto.setCurrentText(proto)
        self.port.setValue(int(port))

    def _proto(self):
        p = self.proto.currentText()
        return {"ICMP ping": "icmp", "TCP connect": "tcp", "UDP": "udp"}.get(p, "icmp")

    def realtime(self):
        src, dst = self.src.currentText(), self.dst.currentText().strip()
        if not src or not dst:
            return
        e = self.lab.engine
        t0 = time.perf_counter()
        if self.proto.currentText() == "Traceroute":
            hops = e.traceroute(src, dst)
            rows = [f"{h['hop']:>2}  {h['node'] or '*'}  {h['ip'] or ''}  "
                    f"{(str(round(h['rtt_ms'], 2)) + ' ms') if h['rtt_ms'] else h['note']}" for h in hops]
            self.result.setText("<b>Traceroute</b><pre>" + html.escape("\n".join(rows)) + "</pre>")
            for h in hops:
                if h["node"]:
                    self.canvas.flash_node(h["node"])
            return
        if self._proto() in ("tcp", "udp"):
            r = e.connect(src, dst, self.port.value(), proto=self._proto())
            col = "#43a047" if r["open"] else "#e53935"
            self.result.setText(f"<b style='color:{col}'>{self._proto().upper()}/{self.port.value()} "
                                f"{'OPEN' if r['open'] else 'CLOSED'}</b> - {html.escape(r['reason'])}")
            return
        r = e.ping(src, dst, record=False)
        dt = (time.perf_counter() - t0) * 1000
        if r["ok"]:
            w = r["request"]
            self.result.setText(
                f"<b style='color:#43a047'>Reply received</b> from {r['dst_ip']} - RTT <b>{r['rtt_ms']:.3f} ms</b>, "
                f"jitter {r['jitter_ms']:.2f} ms, path loss {r['loss'] * 100:.3f}%, bottleneck {fmt_bw(r['min_bw'])}"
                f"<br>Path: {' → '.join(w.l3_path)}<br><span style='opacity:.7'>computed in {dt:.1f} ms</span>")
            for n in w.l3_path:
                self.canvas.flash_node(n)
        else:
            self.result.setText(f"<b style='color:#e53935'>Failed</b> - {html.escape(r['reason'])}")

    def simulate(self):
        src, dst = self.src.currentText(), self.dst.currentText().strip()
        if not src or not dst:
            return
        e = self.lab.engine
        if self._proto() in ("tcp", "udp"):
            r = e.ping(src, dst, proto=self._proto(), dport=self.port.value(), record=True)
        else:
            r = e.ping(src, dst, record=True)
        self.all_events = r.get("events", [])
        self._fill_events(self.all_events)
        self.result.setText((f"<b style='color:#43a047'>Round trip complete</b> - {r['rtt_ms']:.3f} ms"
                             if r["ok"] else f"<b style='color:#e53935'>Failed</b> - {html.escape(r['reason'])}")
                            + f" &nbsp; ({len(self.events)} events; use Step or Play)")
        self.goto(0)

    def _fill_events(self, evs):
        evs = [e for e in evs if (self.chk_arp.isChecked() or e.kind != "arp") and
               (self.chk_l2.isChecked() or not (e.kind == "frame" and self.lab.topo.nodes.get(e.node) and
                                                  self.lab.topo.nodes[e.node].bridges))]
        self.events = evs
        self.tab.setRowCount(0)
        pal = palette(self.s["theme"])
        colors = {"arp": "#8e24aa", "frame": pal["text"], "deliver": pal["ok"], "drop": pal["bad"], "ttl": pal["bad"],
                  "nat": "#00897b", "mpls": "#1e88e5", "tunnel": "#ef6c00"}
        for k, ev in enumerate(evs):
            self.tab.insertRow(k)
            for c, v in enumerate([f"{ev.t_ms:.3f}", ev.node, ev.layer, ev.kind, ev.summary]):
                it = QTableWidgetItem(v)
                it.setForeground(QBrush(QColor(colors.get(ev.kind, pal["text"]))))
                self.tab.setItem(k, c, it)
        self.pos = -1

    def goto(self, k: int):
        if not self.events:
            return
        k = max(0, min(len(self.events) - 1, k))
        self.tab.selectRow(k)

    def _sel(self):
        rows = self.tab.selectionModel().selectedRows()
        if not rows:
            return
        k = rows[0].row()
        prev = self.pos
        self.pos = k
        ev = self.events[k]
        self.tree.clear()
        for layer, fields in ev.pdu.items():
            top = QTreeWidgetItem([layer, ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for kk, vv in fields.items():
                QTreeWidgetItem(top, [str(kk), str(vv)])
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        try:
            fr = pcapng.frame_from_pdu(ev.pdu, k + 1)
            lines = []
            for i in range(0, len(fr), 16):
                chunk = fr[i:i + 16]
                lines.append(f"{i:04x}  {' '.join(f'{b:02x}' for b in chunk):<48}  "
                             f"{''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)}")
            self.hex.setPlainText("\n".join(lines))
        except Exception as ex:  # noqa: BLE001
            self.hex.setPlainText(f"(no frame: {ex})")
        if ev.from_node and ev.to_node and k == prev + 1:
            self.canvas.animate(ev.from_node, ev.to_node, "#ffca28" if ev.kind != "arp" else "#ce93d8", ev.link)
        else:
            self.canvas.flash_node(ev.node)

    def toggle_play(self):
        if self.timer.isActive():
            self.timer.stop()
            self.b_play.setText("▶ Play")
        else:
            if self.pos >= len(self.events) - 1:
                self.goto(0)
            self.timer.start(int(2200 / self.speed.value()))
            self.b_play.setText("⏸ Pause")

    def _tick(self):
        if self.pos >= len(self.events) - 1:
            self.toggle_play()
            return
        self.goto(self.pos + 1)

    def save_pcap(self):
        if not self.all_events:
            QMessageBox.information(self, "Capture", "Run Simulate first.")
            return
        d = self.s["capture_dir"] or os.path.join(os.path.expanduser("~"), "Documents", "VU3INI PacketLab", "captures")
        os.makedirs(d, exist_ok=True)
        name = f"{self.src.currentText()}_to_{self.dst.currentText()}_{time.strftime('%Y%m%d-%H%M%S')}.pcapng"
        path, _ = QFileDialog.getSaveFileName(self, "Save capture", os.path.join(d, name), "pcapng (*.pcapng)")
        if not path:
            return
        n = pcapng.write(path, self.all_events, f"VU3INI PacketLab: {self.src.currentText()} -> {self.dst.currentText()}")
        self.log(f"Saved {n} frames to {path}")
        if self.s["open_in_wireshark"] and os.path.exists(self.s["wireshark_path"]):
            import subprocess
            subprocess.Popen([self.s["wireshark_path"], path])


# =========================================================================== tests
class TestsPanel(QWidget):
    replay = Signal(dict)

    def __init__(self, lab: Lab, settings, compute_fn, log, parent=None):
        super().__init__(parent)
        self.lab, self.s, self.compute_fn, self.log = lab, settings, compute_fn, log
        self.results: List[runner.TestResult] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.b_run = QPushButton("Run all tests  (F5)")
        self.b_run.setObjectName("primary")
        self.b_run.clicked.connect(self.run_all)
        self.b_sel = QPushButton("Run selected")
        self.b_sel.clicked.connect(self.run_selected)
        self.b_add = QPushButton("Add test…")
        self.b_add.clicked.connect(self.add_test)
        self.b_json = QPushButton("Export JSON")
        self.b_json.clicked.connect(lambda: self.export("json"))
        self.b_junit = QPushButton("Export JUnit XML")
        self.b_junit.clicked.connect(lambda: self.export("junit"))
        self.summary = QLabel("No results yet.")
        for b in (self.b_run, self.b_sel, self.b_add):
            bar.addWidget(b)
        bar.addWidget(self.summary, 1)
        bar.addWidget(self.b_json)
        bar.addWidget(self.b_junit)
        lay.addLayout(bar)
        cols = ["Result", "Test", "Type", "Phase", "During", "Measured", "Expected", "Details", "Standard", "ms"]
        self.tab = QTableWidget(0, len(cols))
        self.tab.setHorizontalHeaderLabels(cols)
        self.tab.verticalHeader().setVisible(False)
        self.tab.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tab.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tab.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.tab.setWordWrap(False)
        self.tab.cellDoubleClicked.connect(self._dbl)
        lay.addWidget(self.tab, 1)
        self.show_defined()

    def show_defined(self):
        self.results = []
        self.tab.setRowCount(0)
        for k, t in enumerate(self.lab.topo.tests):
            if not isinstance(t, dict):
                continue
            self.tab.insertRow(k)
            vals = ["–", str(t.get("id")), str(t.get("type")), str(runner.phase_of(str(t.get("type", "")))),
                    str(t.get("during", "")), "", "", "", str(t.get("standard", "")), ""]
            for c, v in enumerate(vals):
                self.tab.setItem(k, c, QTableWidgetItem(v))
        self.summary.setText(f"{len(self.lab.topo.tests)} tests defined. Press Run.")

    def _runner(self):
        return runner.Runner(self.lab.topo, self.s.engine_settings(), compute=self.compute_fn(), log=self.log)

    def run_all(self):
        self._run(None)

    def run_selected(self):
        ids = {self.tab.item(r.row(), 1).text() for r in self.tab.selectionModel().selectedRows()}
        if not ids:
            QMessageBox.information(self, "Tests", "Select one or more tests first.")
            return
        self._run(list(ids))

    def _run(self, only):
        if self.lab.topo.errors:
            if QMessageBox.question(self, "Tests", f"The topology has {len(self.lab.topo.errors)} validation problems. "
                                                   "Run the tests anyway?") != QMessageBox.Yes:
                return
        t0 = time.perf_counter()
        r = self._runner()
        self.results = r.run_all(bool(self.s["stop_on_static_fail"]), only)
        self._fill()
        s = runner.summarize(self.results)
        self.summary.setText(f"<b>{s['pass']}</b> passed, <b style='color:#e53935'>{s['fail']}</b> failed, "
                             f"{s['error']} errors, {s['skip']} skipped in {(time.perf_counter() - t0):.2f} s "
                             f"(compute: {r.compute.last_device})")
        if self.s["auto_export"] and self.lab.path:
            base = os.path.splitext(self.lab.path)[0]
            d = self.s["results_dir"] or os.path.dirname(self.lab.path)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            with open(os.path.join(d, f"{os.path.basename(base)}_results_{stamp}.json"), "w", encoding="utf-8") as f:
                f.write(runner.to_json(self.results, (self.lab.topo.meta or {}).get("name", "")))
            with open(os.path.join(d, f"{os.path.basename(base)}_junit_{stamp}.xml"), "w", encoding="utf-8") as f:
                f.write(runner.to_junit(self.results))

    def _fill(self):
        pal = palette(self.s["theme"])
        col = {"pass": pal["ok"], "fail": pal["bad"], "error": pal["warn"], "skip": pal["muted"]}
        self.tab.setRowCount(0)
        for k, r in enumerate(self.results):
            self.tab.insertRow(k)
            vals = [r.status.upper(), r.id, r.type, str(r.phase), r.during, r.measured, r.expected, r.detail,
                    r.standard, f"{r.duration_ms:.1f}"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setForeground(QBrush(QColor(col.get(r.status, pal["text"]))))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                it.setToolTip(r.detail)
                self.tab.setItem(k, c, it)

    def _dbl(self, row, col):
        tid = self.tab.item(row, 1).text()
        spec = next((t for t in self.lab.topo.tests if isinstance(t, dict) and str(t.get("id")) == tid), None)
        if spec and spec.get("type") in ("ping", "traceroute", "tcp.connect", "udp.reach"):
            self.replay.emit(spec)

    def export(self, kind):
        if not self.results:
            QMessageBox.information(self, "Export", "Run the tests first.")
            return
        name = (self.lab.topo.meta or {}).get("name", "results")
        ext = "json" if kind == "json" else "xml"
        path, _ = QFileDialog.getSaveFileName(self, "Export results", f"{name}_results.{ext}", f"*.{ext}")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(runner.to_json(self.results, name) if kind == "json" else runner.to_junit(self.results, name))
        self.log(f"Results exported to {path}")

    TEMPLATES = {
        "ping": "{ id: my-ping, type: ping, from: PC1, to: SRV1, expect: { loss_max: 0%, rtt_max: 50ms } }",
        "traceroute": "{ id: my-path, type: traceroute, from: PC1, to: SRV1, expect: { via: [R1, R2] } }",
        "tcp.connect": "{ id: web-open, type: tcp.connect, from: PC1, to: SRV1, port: 443, expect: { open: true } }",
        "ospf.neighbor": "{ id: ospf-up, type: ospf.neighbor, on: R1, expect: { state: FULL, count: 1 } }",
        "bgp.session": "{ id: bgp-up, type: bgp.session, on: R1, peer: 10.0.0.2, expect: { state: Established } }",
        "vrrp.state": "{ id: vrrp-master, type: vrrp.state, on: R1, group: 10, expect: { state: MASTER } }",
        "stp.root": "{ id: stp-root, type: stp.root, vlan: 10, expect: { bridge: SW1 } }",
        "dhcp.lease": "{ id: dhcp, type: dhcp.lease, on: PC1, expect: { pool: staff } }",
        "dns.resolve": "{ id: dns, type: dns.resolve, on: PC1, name: SRV1.lan, expect: { address: 10.0.0.10 } }",
        "static.addressing": "{ id: addressing, type: static.addressing }",
        "static.vlan_consistency": "{ id: vlans, type: static.vlan_consistency }",
        "rfc2544.throughput": "{ id: thr, type: rfc2544.throughput, link: uplink, frame_sizes: [64, 1518], "
                              "expect: { min: 90Mbps } }",
        "twamp": "{ id: voice, type: twamp, from: PC1, to: PC2, expect: { one_way_max: 150ms, jitter_max: 30ms } }",
        "y1564": "{ id: sla, type: y1564, service: my-evc, expect: { cir: met, flr_max: 0.01%, ftd_max: 5ms } }",
    }

    def add_test(self):
        typ, ok = QInputDialog.getItem(self, "Add test", "Test type:", list(self.TEMPLATES), 0, False)
        if not ok:
            return
        txt, ok = QInputDialog.getMultiLineText(self, "Add test", "Edit the test (YAML):", self.TEMPLATES[typ])
        if not ok:
            return
        import yaml
        try:
            d = yaml.safe_load(txt)
            if not isinstance(d, dict) or "id" not in d or "type" not in d:
                raise ValueError("a test needs id and type")
        except (yaml.YAMLError, ValueError) as e:
            QMessageBox.warning(self, "Add test", str(e))
            return
        self.lab.edit(lambda raw: raw.setdefault("tests", []).append(d), "tests")


# =========================================================================== analysis
class AnalysisPanel(QWidget):
    def __init__(self, lab: Lab, settings, compute_fn, log, parent=None):
        super().__init__(parent)
        self.lab, self.s, self.compute_fn, self.log = lab, settings, compute_fn, log
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.which = QComboBox()
        self.which.addItems(["End devices (hosts and servers)", "All devices"])
        self.metric_cb = QComboBox()
        self.metric_cb.addItems(["Physical one-way latency (ms)", "Hop count"])
        b = QPushButton("Compute path matrix")
        b.setObjectName("primary")
        b.clicked.connect(self.matrix)
        b2 = QPushButton("Benchmark CPU vs GPU")
        b2.clicked.connect(self.bench)
        b3 = QPushButton("Routing tables")
        b3.clicked.connect(self.tables)
        for w in (self.which, self.metric_cb, b, b2, b3):
            bar.addWidget(w)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.tab = QTableWidget(0, 0)
        self.tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.tab, 1)
        self.text = QTextBrowser()
        self.text.hide()
        lay.addWidget(self.text, 1)

    def matrix(self):
        t = self.lab.topo
        e = self.lab.engine
        names = sorted(t.nodes)
        idx = {n: i for i, n in enumerate(names)}
        N = len(names)
        if N == 0:
            return
        w = np.full((N, N), np.inf)
        hop = self.metric_cb.currentIndex() == 1
        for l in t.links:
            if not e.link_up(l):
                continue
            a, b = idx[l.a_node], idx[l.b_node]
            v = 1.0 if hop else e.link_delay(l)
            w[a, b] = min(w[a, b], v)
            w[b, a] = min(w[b, a], v)
        comp = self.compute_fn()
        t0 = time.perf_counter()
        d = comp.all_pairs(w)
        dt = (time.perf_counter() - t0) * 1000
        show = names if self.which.currentIndex() == 1 else [n for n in names if t.nodes[n].kind in
                                                              ("host", "server", "external", "cloud")]
        if not show:
            show = names
        self.text.hide()
        self.tab.show()
        self.tab.setRowCount(len(show))
        self.tab.setColumnCount(len(show))
        self.tab.setHorizontalHeaderLabels(show)
        self.tab.setVerticalHeaderLabels(show)
        finite = d[np.isfinite(d)]
        mx = float(finite.max()) if finite.size else 1.0
        for r, a in enumerate(show):
            for c, b in enumerate(show):
                v = d[idx[a], idx[b]]
                it = QTableWidgetItem("∞" if not np.isfinite(v) else (f"{int(v)}" if hop else f"{v:.3f}"))
                if np.isfinite(v) and mx > 0:
                    q = v / mx
                    it.setBackground(QBrush(QColor(int(40 + 180 * q), int(140 - 60 * q), int(200 - 150 * q), 90)))
                else:
                    it.setBackground(QBrush(QColor(229, 57, 53, 90)))
                it.setTextAlignment(Qt.AlignCenter)
                self.tab.setItem(r, c, it)
        self.tab.resizeColumnsToContents()
        self.info.setText(f"Floyd-Warshall over {N} devices on <b>{comp.last_device.upper()}</b> in {dt:.1f} ms. "
                          f"This is the shortest physical path; the routed path (OSPF/BGP policy) can be longer — "
                          f"use the Simulation tab for routed latency. ∞ = no physical path.")

    def bench(self):
        comp = self.compute_fn()
        lines = [comp.describe()] + comp.benchmark(400)
        st = comp.sample_path(20.0, 2.0, 0.001, 1_000_000, 1)
        lines.append(f"Monte-Carlo 1,000,000 packets on {st['device']}: mean {st['mean']:.3f} ms, p99 {st['p99']:.3f} ms")
        self.info.setText("<br>".join(html.escape(x) for x in lines))
        self.log(" | ".join(lines))

    def tables(self):
        e = self.lab.engine
        out = []
        for n in sorted(self.lab.topo.nodes):
            routes = e.rib.get(n, [])
            if not routes:
                continue
            out.append(f"<h4>{html.escape(n)}</h4><table cellspacing=0 cellpadding=3 border=0>")
            for r in routes:
                out.append(f"<tr><td><b>{r.code()}</b></td><td>{r.prefix}</td><td>{r.proto} [{r.ad}/{int(r.metric)}]"
                           f"</td><td>{r.nexthop or 'connected'}</td><td>{r.out_if or ''}</td>"
                           f"<td style='opacity:.7'>{html.escape(r.detail)}</td></tr>")
            out.append("</table>")
        self.tab.hide()
        self.text.show()
        self.text.setHtml("".join(out) or "No routes.")


# =========================================================================== log
class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        self.setFont(f)

    def add(self, msg: str):
        self.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")
