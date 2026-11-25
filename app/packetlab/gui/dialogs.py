"""Splash (terms + credits), Settings, Help, About and the GPU add-on downloader."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import urllib.request
import zipfile
from typing import Dict, List

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap,
                           QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from .. import APP_NAME, AUTHOR, CALLSIGN, CONTACT_EMAIL, __version__
from ..core import compute
from .settings import SCHEMA, TERMS_VERSION, Settings
from .texts import TERMS_HTML, credits_html


def asset(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "packetlab", "assets", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", name)


def help_path() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "packetlab", "help", "help.md")
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "help", "help.md")


def round_logo(size: int) -> QPixmap:
    pm = QPixmap(asset("logo_round.png"))
    if pm.isNull():
        pm = QPixmap(size, size)
        pm.fill(QColor("#1e88e5"))
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


# =========================================================================== splash
class SplashDialog(QDialog):
    """Rounded splash in the VU3INI style. Closes only with OK after the Terms are accepted."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.declined = False
        self._ok = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setStyleSheet(
            "QWidget { background: transparent; color: white; }"
            "QTextBrowser { background: #ffffff; color: #1d2330; border: none; border-radius: 10px; padding: 8px;"
            " font-size: 9.5pt; selection-background-color: #1e88e5; }"
            "QTextBrowser QWidget { background: #ffffff; }"
            "QTabWidget::pane { border: none; background: #ffffff; border-radius: 10px; }"
            "QTabBar::tab { background: rgba(255,255,255,0.25); color: white; padding: 6px 16px; margin-right: 3px;"
            " border-top-left-radius: 8px; border-top-right-radius: 8px; border: none; font-weight: 600; }"
            "QTabBar::tab:selected { background: #ffffff; color: #0d47a1; }"
            "QCheckBox { color: white; font-weight: 600; spacing: 8px; }"
            "QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid white; border-radius: 4px;"
            " background: rgba(255,255,255,0.15); }"
            "QCheckBox::indicator:checked { background: #d32f2f; border-color: white; }"
            "QScrollBar:vertical { background: #eef1f6; width: 10px; }"
            "QScrollBar::handle:vertical { background: #90a4ae; border-radius: 5px; min-height: 30px; }")
        scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        w = 820
        if scr is not None:
            w = max(640, min(880, int(scr.width() * 0.55)))
        self.resize(w, int(w * 3 / 4))      # 4:3 like the cover image
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 26, 30, 24)
        lay.setSpacing(10)
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(round_logo(132))
        logo.setFixedSize(136, 136)
        logo.setStyleSheet("background: transparent;")
        top.addWidget(logo)
        tl = QVBoxLayout()
        t1 = QLabel(APP_NAME)
        t1.setStyleSheet("color: white; font-size: 26pt; font-weight: 800; background: transparent;")
        t2 = QLabel(f"Version {__version__}  ·  Design, simulate and test LAN, MAN and WAN networks")
        t2.setStyleSheet("color: #e3f2fd; font-size: 10.5pt; background: transparent;")
        t2.setWordWrap(True)
        t3 = QLabel(f"By {AUTHOR} - {CALLSIGN}  ·  {CONTACT_EMAIL}")
        t3.setStyleSheet("color: #ffcdd2; font-size: 10.5pt; font-weight: 700; background: transparent;")
        for x in (t1, t2, t3):
            tl.addWidget(x)
        tl.addStretch(1)
        top.addLayout(tl, 1)
        lay.addLayout(top)
        tabs = QTabWidget()
        self.terms = QTextBrowser()
        self.terms.setOpenExternalLinks(True)
        self.terms.setHtml(TERMS_HTML)
        cred = QTextBrowser()
        cred.setOpenExternalLinks(True)
        cred.setHtml(credits_html())
        tabs.addTab(self.terms, "Terms of Use")
        tabs.addTab(cred, "With thanks to")
        lay.addWidget(tabs, 1)
        bottom = QHBoxLayout()
        self.chk = QCheckBox("I have read and agree to the Terms of Use. I will use this software for personal use "
                             "only and I am solely responsible for its use.")
        self.chk.setChecked(settings.terms_ok)
        self.chk.toggled.connect(self._toggle)
        bottom.addWidget(self.chk, 1)
        lay.addLayout(bottom)
        btns = QHBoxLayout()
        self.b_decline = QPushButton("Decline and exit")
        self.b_decline.setStyleSheet("QPushButton { background: rgba(255,255,255,0.18); color: white; border: 1px solid "
                                     "rgba(255,255,255,0.5); border-radius: 8px; padding: 8px 16px; }")
        self.b_decline.clicked.connect(self._decline)
        self.b_ok = QPushButton("I Agree  -  OK")
        self.b_ok.setStyleSheet("QPushButton { background: #d32f2f; color: white; border: none; border-radius: 8px; "
                                "padding: 9px 28px; font-weight: 800; font-size: 11pt; }"
                                "QPushButton:disabled { background: #9e9e9e; color: #eeeeee; }")
        self.b_ok.clicked.connect(self._accept)
        btns.addWidget(self.b_decline)
        btns.addStretch(1)
        btns.addWidget(self.b_ok)
        lay.addLayout(btns)
        self._toggle(self.chk.isChecked())
        self._drag = None

    def _toggle(self, on):
        self.b_ok.setEnabled(bool(on))

    def _accept(self):
        if not self.chk.isChecked():
            return
        self.s["terms_accepted"] = TERMS_VERSION
        self.s.save()
        self._ok = True
        self.accept()

    def _decline(self):
        if QMessageBox.question(self, APP_NAME, "You must accept the Terms of Use to use VU3INI PacketLab.\n"
                                                "Exit the application?") == QMessageBox.Yes:
            self.declined = True
            self._ok = True
            self.reject()

    def reject(self):
        if self._ok:
            super().reject()        # only reachable through Decline

    def closeEvent(self, ev):
        if not self._ok:
            ev.ignore()
            return
        super().closeEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            return
        super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, 26, 26)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#42a5f5"))
        g.setColorAt(0.55, QColor("#1e6fbf"))
        g.setColorAt(1.0, QColor("#0d3c78"))
        p.fillPath(path, QBrush(g))
        p.setPen(QColor(255, 255, 255, 120))
        p.drawPath(path)
        # red accent bar like the logo's sweater
        bar = QPainterPath()
        bar.addRoundedRect(QRectF(r.left() + 24, r.top() + 10, 120, 6), 3, 3)
        p.fillPath(bar, QColor("#d32f2f"))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None


# =========================================================================== settings
class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Settings / Preferences")
        self.resize(760, 620)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings…")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.widgets: Dict[str, QWidget] = {}
        self.rows: Dict[str, tuple] = {}
        groups: Dict[str, QFormLayout] = {}
        for (grp, key, label, typ, default, extra, tip) in SCHEMA:
            if grp not in groups:
                page = QWidget()
                form = QFormLayout(page)
                form.setLabelAlignment(Qt.AlignRight)
                sc = QScrollArea()
                sc.setWidgetResizable(True)
                sc.setWidget(page)
                self.tabs.addTab(sc, grp)
                groups[grp] = form
            w = self._widget(key, typ, extra, settings[key])
            w.setToolTip(tip)
            lbl = QLabel(label)
            lbl.setToolTip(tip)
            groups[grp].addRow(lbl, w)
            self.widgets[key] = w
            self.rows[key] = (lbl, w, grp, label.lower() + " " + tip.lower())
        # compute extras
        cform = groups["Compute"]
        self.gpu_lbl = QLabel("")
        self.gpu_lbl.setWordWrap(True)
        cform.addRow("GPU status", self.gpu_lbl)
        row = QHBoxLayout()
        b1 = QPushButton("Detect GPU")
        b1.clicked.connect(self._detect)
        b2 = QPushButton("Download GPU add-on…")
        b2.clicked.connect(self._download)
        b3 = QPushButton("Remove add-on")
        b3.clicked.connect(self._remove)
        for b in (b1, b2, b3):
            row.addWidget(b)
        holder = QWidget()
        holder.setLayout(row)
        cform.addRow("", holder)
        self._detect()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply |
                              QDialogButtonBox.RestoreDefaults)
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(bb)

    def _widget(self, key, typ, extra, val):
        if typ == "bool":
            w = QCheckBox()
            w.setChecked(bool(val))
        elif typ == "int":
            w = QSpinBox()
            w.setRange(*(extra or (0, 10 ** 9)))
            w.setValue(int(val))
        elif typ == "float":
            w = QDoubleSpinBox()
            lo, hi = extra or (0.0, 1e9)
            w.setRange(lo, hi)
            w.setDecimals(3)
            w.setSingleStep((hi - lo) / 100 if hi - lo < 10 else 1.0)
            w.setValue(float(val))
        elif typ == "choice":
            w = QComboBox()
            w.addItems(list(extra))
            w.setCurrentText(str(val))
        elif typ == "font":
            w = QFontComboBox()
            w.setCurrentFont(QFont(str(val)))
        elif typ == "color":
            w = QPushButton(str(val))
            w.setProperty("color", str(val))
            w.setStyleSheet(f"background: {val}; color: white;")
            w.clicked.connect(lambda _=False, b=w: self._pick_color(b))
        elif typ in ("dir", "file"):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            ed = QLineEdit(str(val or ""))
            b = QPushButton("Browse…")
            b.clicked.connect(lambda _=False, e=ed, t=typ: self._browse(e, t))
            h.addWidget(ed, 1)
            h.addWidget(b)
            w.edit = ed
        else:
            w = QLineEdit(str(val or ""))
        return w

    def _pick_color(self, b):
        c = QColorDialog.getColor(QColor(b.property("color")), self)
        if c.isValid():
            b.setProperty("color", c.name())
            b.setText(c.name())
            b.setStyleSheet(f"background: {c.name()}; color: white;")

    def _browse(self, ed, typ):
        if typ == "dir":
            p = QFileDialog.getExistingDirectory(self, "Choose folder", ed.text())
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", ed.text())
        if p:
            ed.setText(p)

    def _value(self, key, typ):
        w = self.widgets[key]
        if typ == "bool":
            return w.isChecked()
        if typ in ("int", "float"):
            return w.value()
        if typ == "choice":
            return w.currentText()
        if typ == "font":
            return w.currentFont().family()
        if typ == "color":
            return w.property("color")
        if typ in ("dir", "file"):
            return w.edit.text().strip()
        return w.text()

    def _apply(self):
        for (grp, key, label, typ, default, extra, tip) in SCHEMA:
            self.s[key] = self._value(key, typ)
        self.s.save()
        compute.reset_detection()
        self.applied.emit()

    def _ok(self):
        self._apply()
        self.accept()

    def _defaults(self):
        if QMessageBox.question(self, "Settings", "Restore every setting to its default value?") != QMessageBox.Yes:
            return
        self.s.reset()
        self.s.save()
        self.applied.emit()
        self.accept()

    def _filter(self, text):
        q = text.lower().strip()
        for key, (lbl, w, grp, hay) in self.rows.items():
            vis = not q or q in hay
            lbl.setVisible(vis)
            w.setVisible(vis)
        if q:
            for i in range(self.tabs.count()):
                grp = self.tabs.tabText(i)
                if any(q in hay for (_l, _w, g, hay) in self.rows.values() if g == grp):
                    self.tabs.setCurrentIndex(i)
                    break

    def _detect(self):
        compute.reset_detection()
        info = compute.gpu_info()
        if info.get("available"):
            self.gpu_lbl.setText(f"✔ {info['name']} - {info['free_mb']} / {info['total_mb']} MB free, CuPy {info['cupy']}")
        else:
            self.gpu_lbl.setText(f"No GPU in use: {info.get('reason')}. Everything runs on the CPU.\n"
                                 f"Add-on folder: {compute.addons_dir()}")

    def _download(self):
        variant = self.widgets["cuda_variant"].currentText()
        if QMessageBox.question(
                self, "GPU add-on",
                f"Download the GPU add-on (CuPy {variant} and the NVIDIA CUDA runtime wheels) from pypi.org?\n\n"
                "About 240 MB. Needs the Internet now and an up-to-date NVIDIA driver. "
                "The files are published by Preferred Networks and NVIDIA under their own licences.") != QMessageBox.Yes:
            return
        dlg = AddonDownloadDialog(variant, self)
        dlg.exec()
        self._detect()

    def _remove(self):
        d = compute.addons_dir()
        if os.path.isdir(d) and QMessageBox.question(self, "GPU add-on", f"Delete {d}?\n(Restart the app afterwards.)") \
                == QMessageBox.Yes:
            shutil.rmtree(d, ignore_errors=True)
            self._detect()


class _Downloader(QThread):
    progress = Signal(int, str)
    finished_ok = Signal(bool, str)

    PACKAGES = {
        "cuda12x": ["cupy-cuda12x", "cuda-pathfinder", "nvidia-cuda-runtime-cu12", "nvidia-cuda-nvrtc-cu12",
                    "nvidia-curand-cu12"],
    }

    def __init__(self, variant: str):
        super().__init__()
        self.variant = variant
        self.stop = False

    def _pick(self, pkg: str):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=60) as r:
            data = json.load(r)
        pyver = f"cp{sys.version_info.major}{sys.version_info.minor}"
        best = None
        for f in data.get("urls", []):
            fn = f["filename"]
            if not fn.endswith(".whl"):
                continue
            if "win_amd64" in fn and (pyver in fn or "abi3" in fn or "py3-none" in fn):
                best = f
                break
            if fn.endswith("py3-none-any.whl") or "py3-none-win_amd64" in fn:
                best = best or f
        return best

    def run(self):
        try:
            target = compute.addons_dir()
            os.makedirs(target, exist_ok=True)
            pkgs = self.PACKAGES.get(self.variant, self.PACKAGES["cuda12x"])
            for k, pkg in enumerate(pkgs):
                if self.stop:
                    raise RuntimeError("cancelled")
                f = self._pick(pkg)
                if f is None:
                    raise RuntimeError(f"no Windows wheel of {pkg} for Python {sys.version_info.major}."
                                       f"{sys.version_info.minor}")
                tmp = os.path.join(target, f["filename"] + ".part")
                size = int(f.get("size") or 0)
                with urllib.request.urlopen(f["url"], timeout=120) as r, open(tmp, "wb") as out:
                    got = 0
                    while True:
                        if self.stop:
                            raise RuntimeError("cancelled")
                        b = r.read(1 << 20)
                        if not b:
                            break
                        out.write(b)
                        got += len(b)
                        pct = int(100 * (k + (got / size if size else 0.5)) / len(pkgs))
                        self.progress.emit(pct, f"{pkg}: {got / 2 ** 20:.0f} / {size / 2 ** 20:.0f} MB")
                self.progress.emit(int(100 * (k + 1) / len(pkgs)), f"unpacking {pkg}…")
                with zipfile.ZipFile(tmp) as z:
                    z.extractall(target)
                os.remove(tmp)
            self.finished_ok.emit(True, "GPU add-on installed. Restart VU3INI PacketLab to use the GPU.")
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, f"Download failed: {e}")


class AddonDownloadDialog(QDialog):
    def __init__(self, variant: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloading GPU add-on")
        self.resize(520, 150)
        lay = QVBoxLayout(self)
        self.lbl = QLabel("Starting…")
        self.bar = QProgressBar()
        lay.addWidget(self.lbl)
        lay.addWidget(self.bar)
        self.btn = QPushButton("Cancel")
        self.btn.clicked.connect(self._cancel)
        lay.addWidget(self.btn, 0, Qt.AlignRight)
        self.th = _Downloader(variant)
        self.th.progress.connect(lambda p, m: (self.bar.setValue(p), self.lbl.setText(m)))
        self.th.finished_ok.connect(self._done)
        self.th.start()

    def _cancel(self):
        if self.th.isRunning():
            self.th.stop = True
        else:
            self.accept()

    def _done(self, ok, msg):
        self.lbl.setText(msg)
        self.bar.setValue(100 if ok else self.bar.value())
        self.btn.setText("Close")


# =========================================================================== help
class HelpDialog(QDialog):
    def __init__(self, parent=None, topic: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} Help")
        self.resize(1040, 720)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the help (for example: VLAN, BGP, DHCP relay, GPU, pcapng)…")
        self.search.textChanged.connect(self._search)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        top.addWidget(self.search, 1)
        top.addWidget(self.count)
        lay.addLayout(top)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        split.addWidget(self.list)
        split.addWidget(self.view)
        split.setSizes([260, 780])
        lay.addWidget(split, 1)
        self.sections = self._load()
        for title, _ in self.sections:
            self.list.addItem(title)
        self.list.currentRowChanged.connect(self._show)
        self.list.setCurrentRow(0)
        if topic:
            self.search.setText(topic)

    def _load(self):
        try:
            with open(help_path(), "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = "## Help\nThe help file could not be found."
        parts = re.split(r"^## ", text, flags=re.M)
        out = []
        for p in parts:
            if not p.strip():
                continue
            title, _, body = p.partition("\n")
            out.append((title.strip(), body))
        return out

    def _show(self, row):
        if row < 0:
            return
        item = self.list.item(row)
        title = item.text()
        body = next((b for t, b in self.sections if t == title), "")
        self.view.setMarkdown(f"# {title}\n{body}")
        q = self.search.text().strip()
        if q:
            self._highlight(q)

    def _highlight(self, q):
        doc = self.view.document()
        sels = []
        cur = QTextCursor(doc)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffeb3b"))
        fmt.setForeground(QColor("#000000"))
        first = None
        while True:
            cur = doc.find(q, cur)
            if cur.isNull():
                break
            s = QTextEdit.ExtraSelection()
            s.cursor = cur
            s.format = fmt
            sels.append(s)
            first = first or QTextCursor(cur)
        self.view.setExtraSelections(sels)
        if first is not None:
            self.view.setTextCursor(first)
            self.view.ensureCursorVisible()

    def _search(self, text):
        q = text.lower().strip()
        hits = 0
        first = -1
        for i, (title, body) in enumerate(self.sections):
            n = (title + " " + body).lower().count(q) if q else 0
            it = self.list.item(i)
            it.setHidden(bool(q) and n == 0)
            it.setText(title)
            if q and n:
                hits += n
                if first < 0:
                    first = i
        self.count.setText(f"{hits} matches" if q else "")
        if first >= 0:
            self.list.setCurrentRow(first)
            self._show(first)
        elif not q:
            self._show(self.list.currentRow())


# =========================================================================== about
class AboutDialog(QDialog):
    def __init__(self, compute_desc: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(620, 520)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(round_logo(120))
        top.addWidget(logo)
        info = QLabel(
            f"<h2 style='margin:0'>{APP_NAME}</h2>"
            f"<p>Version <b>{__version__}</b><br>Design, simulate and test LAN, MAN and WAN networks to current "
            f"industry standards.</p>"
            f"<p>Written by <b>{AUTHOR} ({CALLSIGN})</b><br>"
            f"Contact: <a href='mailto:{CONTACT_EMAIL}'>{CONTACT_EMAIL}</a></p>"
            f"<p style='opacity:.75'>For personal use only. See the Terms of Use.</p>")
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        top.addWidget(info, 1)
        lay.addLayout(top)
        tabs = QTabWidget()
        cred = QTextBrowser()
        cred.setOpenExternalLinks(True)
        cred.setHtml(credits_html())
        terms = QTextBrowser()
        terms.setOpenExternalLinks(True)
        terms.setHtml(TERMS_HTML)
        import PySide6
        import numpy
        import yaml
        sysinfo = QTextBrowser()
        sysinfo.setPlainText(
            f"{APP_NAME} {__version__}\nPython {platform.python_version()} ({platform.architecture()[0]})\n"
            f"Qt / PySide6 {PySide6.__version__}\nNumPy {numpy.__version__}\nPyYAML {yaml.__version__}\n"
            f"OS: {platform.platform()}\nCPU threads: {os.cpu_count()}\nCompute: {compute_desc}\n"
            f"GPU add-on folder: {compute.addons_dir()}")
        tabs.addTab(cred, "Credits")
        tabs.addTab(terms, "Terms of Use")
        tabs.addTab(sysinfo, "System")
        lay.addWidget(tabs, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        mail = bb.addButton("Email the author", QDialogButtonBox.ActionRole)
        mail.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"mailto:{CONTACT_EMAIL}?subject={APP_NAME}")))
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        lay.addWidget(bb)


def check_updates(url: str) -> str:
    """Return a human message about the latest version (needs the Internet)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    tag = str(data.get("tag_name") or data.get("version") or "").lstrip("vV")
    if not tag:
        return "The update server gave no version number."

    def parse(v):
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
    if parse(tag) > parse(__version__):
        return f"Version {tag} is available (you have {__version__}). {data.get('html_url', '')}"
    return f"You have the latest version ({__version__})."
