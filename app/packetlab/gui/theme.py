"""Dark and light themes (Qt style sheets) with the VU3INI accent colours."""
from __future__ import annotations

ACCENTS = {
    "VU3INI blue": "#1e88e5", "VU3INI red": "#d32f2f", "Teal": "#00897b", "Purple": "#7e57c2",
    "Orange": "#ef6c00", "Green": "#2e7d32",
}

PALETTES = {
    "dark": {"bg": "#161a22", "panel": "#1f2430", "panel2": "#262c3a", "text": "#e6e9ef", "muted": "#9aa3b2",
             "border": "#323a4a", "input": "#141821", "canvas": "#121620", "grid": "#202736",
             "ok": "#43a047", "warn": "#fb8c00", "bad": "#e53935", "sel": "#2b3446"},
    "light": {"bg": "#eef1f6", "panel": "#ffffff", "panel2": "#f6f8fb", "text": "#1d2330", "muted": "#5d6778",
              "border": "#d3d9e3", "input": "#ffffff", "canvas": "#fbfcfe", "grid": "#e8ecf3",
              "ok": "#2e7d32", "warn": "#e65100", "bad": "#c62828", "sel": "#dbe8fb"},
}


def palette(theme: str) -> dict:
    return PALETTES.get(theme, PALETTES["dark"])


def stylesheet(theme: str, accent_name: str, font_family: str, font_size: int) -> str:
    p = palette(theme)
    a = ACCENTS.get(accent_name, ACCENTS["VU3INI blue"])
    return f"""
* {{ font-family: "{font_family}"; font-size: {font_size}pt; }}
QMainWindow, QDialog, QWidget {{ background: {p['bg']}; color: {p['text']}; }}
QDockWidget {{ color: {p['text']}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {p['panel2']}; padding: 5px 8px; border-bottom: 1px solid {p['border']};
                      font-weight: 600; }}
QMenuBar {{ background: {p['panel']}; border-bottom: 1px solid {p['border']}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {p['sel']}; border-radius: 4px; }}
QMenu {{ background: {p['panel']}; border: 1px solid {p['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {a}; color: white; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
QToolBar {{ background: {p['panel']}; border: none; border-bottom: 1px solid {p['border']}; spacing: 4px; padding: 3px; }}
QToolButton {{ padding: 5px 8px; border-radius: 6px; border: 1px solid transparent; }}
QToolButton:hover {{ background: {p['sel']}; border-color: {p['border']}; }}
QToolButton:checked {{ background: {a}; color: white; }}
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {a}; }}
QPushButton:pressed {{ background: {p['sel']}; }}
QPushButton:disabled {{ color: {p['muted']}; }}
QPushButton#primary {{ background: {a}; color: white; border-color: {a}; font-weight: 600; }}
QPushButton#danger {{ background: {p['bad']}; color: white; border-color: {p['bad']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox {{
    background: {p['input']}; border: 1px solid {p['border']}; border-radius: 5px; padding: 4px;
    selection-background-color: {a}; selection-color: white; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {a}; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; selection-background-color: {a}; }}
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: {p['panel2']}; padding: 6px 14px; border: 1px solid {p['border']};
                border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {p['panel']}; border-top: 2px solid {a}; }}
QHeaderView::section {{ background: {p['panel2']}; padding: 5px; border: none; border-right: 1px solid {p['border']};
                        border-bottom: 1px solid {p['border']}; font-weight: 600; }}
QTableWidget, QTreeWidget, QListWidget, QTableView, QTreeView, QListView {{
    background: {p['input']}; alternate-background-color: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 5px; gridline-color: {p['border']}; selection-background-color: {a}; selection-color: white; }}
QListWidget::item {{ padding: 6px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {p['sel']}; }}
QStatusBar {{ background: {p['panel']}; border-top: 1px solid {p['border']}; color: {p['muted']}; }}
QStatusBar QLabel {{ color: {p['muted']}; padding: 0 6px; }}
QLabel#footer {{ color: {p['text']}; font-weight: 600; }}
QLabel#title {{ font-size: {font_size + 5}pt; font-weight: 700; }}
QLabel#subtitle {{ color: {p['muted']}; }}
QLabel#muted {{ color: {p['muted']}; }}
QFrame#header {{ background: {p['panel']}; border-bottom: 1px solid {p['border']}; }}
QFrame#header QLabel, QFrame#header QToolButton {{ background: transparent; }}
QStatusBar QLabel {{ background: transparent; }}
QFrame#card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 10px; }}
QGroupBox {{ border: 1px solid {p['border']}; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p['muted']}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {p['border']}; }}
QProgressBar {{ border: 1px solid {p['border']}; border-radius: 6px; text-align: center; background: {p['input']}; }}
QProgressBar::chunk {{ background: {a}; border-radius: 5px; }}
QToolTip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; padding: 4px; }}
"""
