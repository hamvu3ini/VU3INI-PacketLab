"""VU3INI PacketLab entry point."""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import traceback


def _setup_logging():
    from .gui.settings import local_dir
    d = local_dir("logs")
    path = os.path.join(d, "packetlab.log")
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        crash = open(os.path.join(d, "crash.log"), "a", encoding="utf-8")
        faulthandler.enable(crash)
    except OSError:
        pass
    return path


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VU3INI.PacketLab")
        except Exception:  # noqa: BLE001
            pass
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, __version__
    from .gui import dialogs
    from .gui.mainwindow import MainWindow, samples_dir
    from .gui.settings import Settings
    from .gui.theme import stylesheet

    log_path = _setup_logging()
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("VU3INI")
    app.setWindowIcon(QIcon(dialogs.asset("app.ico")))

    def excepthook(et, ev, tb):
        txt = "".join(traceback.format_exception(et, ev, tb))
        logging.error(txt)
        try:
            QMessageBox.critical(None, APP_NAME, f"An unexpected error occurred:\n\n{ev}\n\n"
                                                 f"Details were written to:\n{log_path}")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = excepthook

    s = Settings()
    app.setStyleSheet(stylesheet(s["theme"], s["accent"], s["ui_font_family"], int(s["ui_font_size"])))
    if s["show_splash"] or not s.terms_ok:
        sp = dialogs.SplashDialog(s)
        sp.exec()
        if sp.declined or not s.terms_ok:
            return 0
    w = MainWindow(s)
    opened = False
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args and os.path.exists(args[0]):
        opened = w.load(args[0])
    elif s["restore_last"] and s["last_project"] and os.path.exists(s["last_project"]):
        opened = w.load(s["last_project"])
    if not opened:
        sample = os.path.join(samples_dir(), "hq-metro-branch.nstopo.yaml")
        if os.path.exists(sample):
            w.load(sample)
    w.show()
    if s["check_updates"]:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: w._log(_quiet_update(s)))
    return app.exec()


def _quiet_update(s) -> str:
    from .gui.dialogs import check_updates
    try:
        return check_updates(s["update_url"])
    except Exception as e:  # noqa: BLE001
        return f"Update check failed: {e}"


if __name__ == "__main__":
    sys.exit(main())
