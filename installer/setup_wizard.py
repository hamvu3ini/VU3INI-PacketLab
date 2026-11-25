"""VU3INI PacketLab setup wizard and uninstaller (Windows-style, with progress bar and round logo).

Built twice by build.py:
  * "VU3INI_PacketLab_Setup_v<version>.exe" - carries app.zip (+ optional gpu.zip) and the uninstaller
  * "Uninstall VU3INI PacketLab.exe"         - no payload; runs in uninstall mode
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QProgressBar, QPushButton, QRadioButton, QStackedWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

import installer_core as core


def read_text(name: str, default: str = "") -> str:
    try:
        with open(core.resource(name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


VERSION = read_text("VERSION", "1.0.0").strip()
TERMS = read_text("terms.html", "<p>Terms of Use: personal use only. The user is solely responsible for its use.</p>")

QSS = """
QWidget { font-family: "Segoe UI"; font-size: 10pt; color: #1d2330; }
QDialog { background: #ffffff; }
QLabel#h1 { font-size: 17pt; font-weight: 700; color: #0d47a1; }
QLabel#muted { color: #5d6778; }
QPushButton { background: #f3f5f9; border: 1px solid #c9d2df; border-radius: 6px; padding: 7px 18px; min-width: 80px; }
QPushButton:hover { border-color: #1e88e5; }
QPushButton:disabled { color: #a0a8b5; }
QPushButton#primary { background: #1e88e5; color: white; border-color: #1e88e5; font-weight: 600; }
QPushButton#primary:disabled { background: #9ec5ee; border-color: #9ec5ee; }
QLineEdit, QTextBrowser, QListWidget { border: 1px solid #c9d2df; border-radius: 6px; padding: 4px; background: white; }
QProgressBar { border: 1px solid #c9d2df; border-radius: 7px; text-align: center; height: 22px; background: #eef1f6; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1e88e5, stop:1 #d32f2f);
                      border-radius: 6px; }
QFrame#line { background: #e2e7ef; max-height: 1px; }
"""


def round_pixmap(size: int) -> QPixmap:
    pm = QPixmap(core.resource("logo_round.png"))
    if pm.isNull():
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setBrush(QColor("#1e88e5"))
        p.drawEllipse(0, 0, size, size)
        p.end()
        return pm
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class Banner(QWidget):
    """Left side panel: gradient like the VU3INI logo, round logo, product name."""

    def __init__(self, subtitle: str):
        super().__init__()
        self.setFixedWidth(210)
        self.subtitle = subtitle
        self.logo = round_pixmap(150)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#42a5f5"))
        g.setColorAt(0.6, QColor("#1e6fbf"))
        g.setColorAt(1, QColor("#0d3c78"))
        p.fillRect(self.rect(), QBrush(g))
        p.fillRect(QRectF(0, self.height() - 8, self.width(), 8), QColor("#d32f2f"))
        x = (self.width() - self.logo.width()) / 2
        p.drawPixmap(int(x), 34, self.logo)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", 15, QFont.Bold)
        p.setFont(f)
        p.drawText(QRectF(10, 200, self.width() - 20, 60), Qt.AlignHCenter | Qt.TextWordWrap, "VU3INI\nPacketLab")
        f.setPointSize(9)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#e3f2fd"))
        p.drawText(QRectF(14, 268, self.width() - 28, 80), Qt.AlignHCenter | Qt.TextWordWrap,
                   f"Version {VERSION}\n{self.subtitle}")
        p.setPen(QColor("#ffcdd2"))
        p.drawText(QRectF(10, self.height() - 52, self.width() - 20, 30), Qt.AlignHCenter,
                   "By Sasanka Boruah - VU3INI")


class Bridge(QObject):
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(bool, str)


class Wizard(QDialog):
    def __init__(self, mode: str, target: str = ""):
        super().__init__()
        self.mode = mode
        self.target_dir = target
        self.result_exe = ""
        self.working = False
        self.setWindowTitle(f"{core.APP_NAME} {VERSION} " + ("Setup" if mode == "install" else "Uninstall"))
        self.setWindowIcon(QIcon(core.resource("app.ico")))
        self.setFixedSize(760, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(Banner("Setup wizard" if mode == "install" else "Uninstall"))
        right = QVBoxLayout()
        right.setContentsMargins(26, 22, 22, 16)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        line = QFrame()
        line.setObjectName("line")
        right.addWidget(line)
        btns = QHBoxLayout()
        self.b_back = QPushButton("< Back")
        self.b_next = QPushButton("Next >")
        self.b_next.setObjectName("primary")
        self.b_cancel = QPushButton("Cancel")
        btns.addStretch(1)
        btns.addWidget(self.b_back)
        btns.addWidget(self.b_next)
        btns.addSpacing(10)
        btns.addWidget(self.b_cancel)
        right.addLayout(btns)
        outer.addLayout(right, 1)
        self.b_back.clicked.connect(self.back)
        self.b_next.clicked.connect(self.next)
        self.b_cancel.clicked.connect(self.close)
        self.bridge = Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.log.connect(lambda m: self.details.addItem(m))
        self.bridge.done.connect(self._on_done)
        if mode == "install":
            self._pages_install()
        else:
            self._pages_uninstall()
        self._update_buttons()

    # ------------------------------------------------------------------ pages
    def _page(self, title: str, subtitle: str = "") -> QVBoxLayout:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        h = QLabel(title)
        h.setObjectName("h1")
        v.addWidget(h)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("muted")
            s.setWordWrap(True)
            v.addWidget(s)
        v.addSpacing(8)
        self.stack.addWidget(w)
        return v

    def _pages_install(self):
        ex = core.existing_install()
        v = self._page(f"Welcome to {core.APP_NAME}",
                       "This wizard installs VU3INI PacketLab on your computer.")
        txt = QLabel(
            "<p>VU3INI PacketLab lets you design LAN, MAN and WAN networks, simulate them packet by packet and test "
            "them against current industry standards - completely offline.</p>"
            "<p>Everything it needs is inside this installer. No administrator rights are required: it installs for "
            "your Windows user account only.</p>"
            + (f"<p><b>Version {ex['version']} is already installed.</b> It will be replaced; your settings and "
               f"saved networks are kept.</p>" if ex else "")
            + "<p>Close other programs before continuing, then click <b>Next</b>.</p>")
        txt.setWordWrap(True)
        v.addWidget(txt)
        v.addStretch(1)
        self.p_welcome = 0
        # licence
        v = self._page("Terms of Use", "Please read the Terms of Use. You must accept them to install.")
        tb = QTextBrowser()
        tb.setHtml(TERMS)
        tb.setOpenExternalLinks(True)
        v.addWidget(tb, 1)
        self.r_accept = QRadioButton("I accept the Terms of Use (personal use only; I am responsible for its use)")
        self.r_decline = QRadioButton("I do not accept the Terms of Use")
        self.r_decline.setChecked(True)
        self.r_accept.toggled.connect(self._update_buttons)
        v.addWidget(self.r_accept)
        v.addWidget(self.r_decline)
        # location
        v = self._page("Choose install location", "VU3INI PacketLab will be installed in this folder.")
        row = QHBoxLayout()
        self.ed_dir = QLineEdit(ex["path"] if ex and ex.get("path") else core.default_install_dir())
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row.addWidget(self.ed_dir, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.lbl_space = QLabel("")
        self.lbl_space.setObjectName("muted")
        self.ed_dir.textChanged.connect(self._space)
        v.addWidget(self.lbl_space)
        v.addStretch(1)
        self._space()
        # components
        v = self._page("Select options", "Choose shortcuts and optional components.")
        self.c_start = QCheckBox("Create Start menu shortcuts")
        self.c_start.setChecked(True)
        self.c_desk = QCheckBox("Create a desktop shortcut")
        self.c_desk.setChecked(True)
        self.c_assoc = QCheckBox("Add PacketLab to 'Open with' for topology (.yaml) files")
        self.c_assoc.setChecked(True)
        self.gpu_zip = core.resource("gpu.zip")
        self.c_gpu = QCheckBox(f"NVIDIA GPU add-on: CuPy + CUDA 12 ({core.zip_size_mb(self.gpu_zip)} MB)")
        has_gpu = os.path.exists(self.gpu_zip)
        self.c_gpu.setEnabled(has_gpu)
        self.c_gpu.setChecked(has_gpu)
        if not has_gpu:
            self.c_gpu.setText("NVIDIA GPU add-on (not in this installer)")
            self.c_gpu.setToolTip("It can be downloaded later from Settings > Compute")
        for c in (self.c_start, self.c_desk, self.c_assoc, self.c_gpu):
            v.addWidget(c)
        note = QLabel("Without the GPU add-on PacketLab uses the CPU, and it falls back to the CPU automatically "
                      "if the GPU is missing or runs out of memory.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addSpacing(8)
        v.addWidget(note)
        v.addStretch(1)
        # ready
        v = self._page("Ready to install", "Review your choices, then click Install.")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        v.addWidget(self.summary)
        v.addStretch(1)
        self.p_ready = 4
        # progress
        v = self._page("Installing", "Please wait while VU3INI PacketLab is installed.")
        self.status = QLabel("Preparing…")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.details = QListWidget()
        v.addWidget(self.status)
        v.addWidget(self.bar)
        v.addWidget(self.details, 1)
        self.p_progress = 5
        # finish
        v = self._page("Setup complete", "")
        self.fin = QLabel("")
        self.fin.setWordWrap(True)
        v.addWidget(self.fin)
        self.c_launch = QCheckBox("Launch VU3INI PacketLab now")
        self.c_launch.setChecked(True)
        v.addWidget(self.c_launch)
        v.addStretch(1)
        self.p_finish = 6

    def _pages_uninstall(self):
        v = self._page(f"Uninstall {core.APP_NAME}", "This removes VU3INI PacketLab from your computer.")
        info = QLabel(f"<p>Program folder:<br><b>{self.target_dir}</b></p>"
                      "<p>Your saved topology files (in Documents or wherever you saved them) are never removed.</p>")
        info.setWordWrap(True)
        v.addWidget(info)
        self.c_data = QCheckBox("Also remove my settings, autosaves, logs and the GPU add-on")
        v.addWidget(self.c_data)
        v.addStretch(1)
        self.p_ready = 0
        v = self._page("Uninstalling", "Please wait…")
        self.status = QLabel("")
        self.bar = QProgressBar()
        self.details = QListWidget()
        v.addWidget(self.status)
        v.addWidget(self.bar)
        v.addWidget(self.details, 1)
        self.p_progress = 1
        v = self._page("Uninstall complete", "")
        self.fin = QLabel("")
        self.fin.setWordWrap(True)
        v.addWidget(self.fin)
        v.addStretch(1)
        self.p_finish = 2

    # ------------------------------------------------------------------ navigation
    def _update_buttons(self):
        i = self.stack.currentIndex()
        if self.mode == "install":
            self.b_back.setEnabled(0 < i < self.p_progress)
            if i == 1:
                self.b_next.setEnabled(self.r_accept.isChecked())
            else:
                self.b_next.setEnabled(i != self.p_progress)
            self.b_next.setText("Install" if i == self.p_ready else ("Finish" if i == self.p_finish else "Next >"))
        else:
            self.b_back.setEnabled(False)
            self.b_next.setEnabled(i != self.p_progress)
            self.b_next.setText("Uninstall" if i == self.p_ready else ("Finish" if i == self.p_finish else "Next >"))
        self.b_cancel.setEnabled(i not in (self.p_progress, self.p_finish))

    def back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_buttons()

    def next(self):
        i = self.stack.currentIndex()
        if i == self.p_finish:
            if self.mode == "install" and self.c_launch.isChecked() and self.result_exe:
                try:
                    subprocess.Popen([self.result_exe], cwd=os.path.dirname(self.result_exe), close_fds=True)
                except OSError as e:
                    QMessageBox.warning(self, "Launch", str(e))
            self.working = False
            self.accept()
            return
        if i == self.p_ready:
            self.stack.setCurrentIndex(self.p_progress)
            self._update_buttons()
            self._start()
            return
        if self.mode == "install" and i == 2:
            d = self.ed_dir.text().strip()
            if not d:
                return
            if os.path.isdir(d) and os.listdir(d) and not os.path.exists(os.path.join(d, core.EXE_NAME)):
                QMessageBox.warning(self, "Install location",
                                    "That folder is not empty. Please choose an empty folder or a new folder name.")
                return
        if self.mode == "install" and i + 1 == self.p_ready:
            self.summary.setText(
                f"<p><b>Destination:</b><br>{self.ed_dir.text()}</p>"
                f"<p><b>Shortcuts:</b> {'Start menu' if self.c_start.isChecked() else ''}"
                f"{', ' if self.c_start.isChecked() and self.c_desk.isChecked() else ''}"
                f"{'Desktop' if self.c_desk.isChecked() else ''}"
                f"{'none' if not (self.c_start.isChecked() or self.c_desk.isChecked()) else ''}</p>"
                f"<p><b>File association (.nstopo):</b> {'yes' if self.c_assoc.isChecked() else 'no'}</p>"
                f"<p><b>GPU add-on:</b> {'yes' if self.c_gpu.isChecked() else 'no (CPU only)'}</p>"
                f"<p><b>Disk space needed:</b> about "
                f"{core.zip_size_mb(core.resource('app.zip')) + (core.zip_size_mb(self.gpu_zip) if self.c_gpu.isChecked() else 0)} MB</p>")
        self.stack.setCurrentIndex(i + 1)
        self._update_buttons()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", os.path.dirname(self.ed_dir.text()))
        if d:
            if os.path.basename(d) != core.APP_NAME:
                d = os.path.join(d, core.APP_NAME)
            self.ed_dir.setText(os.path.normpath(d))

    def _space(self):
        need = core.zip_size_mb(core.resource("app.zip"))
        free = core.free_space_mb(self.ed_dir.text())
        self.lbl_space.setText(f"Space required: about {need} MB.  Space available: "
                               f"{free if free >= 0 else '?'} MB.")

    # ------------------------------------------------------------------ work
    def _start(self):
        self.working = True

        def prog(p, m):
            self.bridge.progress.emit(p, m)

        def log(m):
            self.bridge.log.emit(m)

        def run():
            try:
                if self.mode == "install":
                    self.result_exe = core.install(
                        self.ed_dir.text().strip(), VERSION, core.resource("app.zip"),
                        core.resource(core.UNINSTALL_EXE),
                        self.gpu_zip if self.c_gpu.isChecked() and os.path.exists(self.gpu_zip) else None,
                        self.c_start.isChecked(), self.c_desk.isChecked(), self.c_assoc.isChecked(), prog, log)
                    self.bridge.done.emit(True, "")
                else:
                    core.uninstall(self.target_dir, self.c_data.isChecked(), prog, log)
                    self.bridge.done.emit(True, "")
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc().splitlines()[-1])
                self.bridge.done.emit(False, str(e))
        threading.Thread(target=run, daemon=True).start()

    def _on_progress(self, p, m):
        self.bar.setValue(p)
        self.status.setText(m)

    def _on_done(self, ok, err):
        self.working = False
        if ok:
            if self.mode == "install":
                self.fin.setText(f"<p><b>{core.APP_NAME} {VERSION}</b> has been installed.</p>"
                                 f"<p>Start it from the Start menu or the desktop shortcut. To remove it later, use "
                                 f"Windows Settings > Apps > Installed apps, or the Uninstall shortcut.</p>"
                                 f"<p>By Sasanka Boruah - VU3INI · {core.CONTACT}</p>")
            else:
                self.fin.setText(f"<p>{core.APP_NAME} has been removed from your computer.</p>"
                                 "<p>Thank you for using it! - Sasanka Boruah, VU3INI</p>")
            self.stack.setCurrentIndex(self.p_finish)
        else:
            self.status.setText("Failed: " + err)
            QMessageBox.critical(self, self.windowTitle(), f"The operation failed:\n\n{err}")
            self.b_cancel.setEnabled(True)
            self.b_cancel.setText("Close")
            return
        self._update_buttons()

    def closeEvent(self, ev):
        if self.working:
            ev.ignore()
            return
        i = self.stack.currentIndex()
        if i not in (self.p_finish,) and self.b_cancel.text() != "Close":
            if QMessageBox.question(self, self.windowTitle(), "Exit setup?" if self.mode == "install"
                                    else "Cancel the uninstall?") != QMessageBox.Yes:
                ev.ignore()
                return
        super().closeEvent(ev)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setWindowIcon(QIcon(core.resource("app.ico")))
    args = sys.argv[1:]
    has_payload = os.path.exists(core.resource("app.zip"))
    is_uninstaller = "uninstall" in os.path.basename(sys.executable).lower() or "--uninstall" in args
    if not has_payload and not is_uninstaller:
        QMessageBox.critical(None, "VU3INI PacketLab Setup",
                             "The installation files were not found.\n\nPlease extract the WHOLE zip file to a folder "
                             "first (right-click the zip > Extract All), then run the Setup program from that folder. "
                             "The 'data' folder must stay next to the Setup program.")
        return 1
    if has_payload and not is_uninstaller:
        w = Wizard("install")
        w.show()
        return app.exec()
    # ---- uninstall mode
    exe = os.path.abspath(sys.executable)
    if "--from-temp" not in args:
        target = os.path.dirname(exe) if getattr(sys, "frozen", False) else (args[args.index("--dir") + 1]
                                                                           if "--dir" in args else "")
        if getattr(sys, "frozen", False) and os.name == "nt":
            # run from a temporary copy so the program folder (and this exe) can be deleted
            tmp = os.path.join(tempfile.gettempdir(), f"packetlab_uninstall_{os.getpid()}.exe")
            shutil.copy2(exe, tmp)
            extra = ["--quiet"] if "--quiet" in args else []
            subprocess.Popen([tmp, "--from-temp", "--dir", target] + extra, close_fds=True)
            return 0
    else:
        target = args[args.index("--dir") + 1]
    if "--quiet" in args:
        try:
            core.uninstall(target, False, lambda p, m: None, lambda m: None)
        finally:
            if "--from-temp" in args:
                core.schedule_self_delete(exe)
        return 0
    w = Wizard("uninstall", target)
    w.show()
    rc = app.exec()
    if "--from-temp" in args:
        core.schedule_self_delete(exe)
    return rc


if __name__ == "__main__":
    sys.exit(main())
