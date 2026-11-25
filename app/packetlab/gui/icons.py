"""Vector-drawn device and toolbar icons (no external image files needed)."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

KIND_COLORS = {
    "router": "#1e88e5", "l3switch": "#00897b", "switch": "#3949ab", "firewall": "#d32f2f",
    "host": "#546e7a", "server": "#6d4c41", "ap": "#8e24aa", "cloud": "#78909c", "external": "#5d4037",
}
KIND_LABELS = {
    "router": "Router", "l3switch": "Layer-3 switch", "switch": "Switch", "firewall": "Firewall",
    "host": "PC / host", "server": "Server", "ap": "Wi-Fi access point", "cloud": "Internet / ISP cloud",
    "external": "External network",
}


def _grad(r: QRectF, c: str) -> QBrush:
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    base = QColor(c)
    g.setColorAt(0, base.lighter(135))
    g.setColorAt(1, base.darker(115))
    return QBrush(g)


def _arrow(p: QPainter, a: QPointF, b: QPointF, w: float):
    p.drawLine(a, b)
    d = b - a
    ln = (d.x() ** 2 + d.y() ** 2) ** 0.5 or 1
    ux, uy = d.x() / ln, d.y() / ln
    s = w * 0.9
    left = QPointF(b.x() - ux * s - uy * s * 0.6, b.y() - uy * s + ux * s * 0.6)
    right = QPointF(b.x() - ux * s + uy * s * 0.6, b.y() - uy * s - ux * s * 0.6)
    p.drawPolygon(QPolygonF([b, left, right]))


def paint_device(p: QPainter, kind: str, r: QRectF) -> None:
    c = KIND_COLORS.get(kind, "#607d8b")
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    white = QColor("white")
    w = r.width()
    pen = QPen(QColor(c).darker(140), max(1.0, w / 40))
    p.setPen(pen)
    if kind == "router":
        e = r.adjusted(w * 0.04, w * 0.16, -w * 0.04, -w * 0.16)
        p.setBrush(_grad(e, c))
        p.drawEllipse(e)
        p.setPen(QPen(white, w / 22, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(white)
        cx, cy = e.center().x(), e.center().y()
        s = e.height() * 0.32
        _arrow(p, QPointF(cx - s * 1.5, cy - s * 0.45), QPointF(cx - s * 0.35, cy - s * 0.45), w / 18)
        _arrow(p, QPointF(cx + s * 1.5, cy + s * 0.45), QPointF(cx + s * 0.35, cy + s * 0.45), w / 18)
        _arrow(p, QPointF(cx + s * 0.35, cy - s * 0.45), QPointF(cx + s * 1.5, cy - s * 0.45), w / 18)
        _arrow(p, QPointF(cx - s * 0.35, cy + s * 0.45), QPointF(cx - s * 1.5, cy + s * 0.45), w / 18)
    elif kind in ("switch", "l3switch"):
        b = r.adjusted(w * 0.03, w * 0.22, -w * 0.03, -w * 0.22)
        p.setBrush(_grad(b, c))
        p.drawRoundedRect(b, w * 0.08, w * 0.08)
        p.setPen(QPen(white, w / 22, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(white)
        cx, cy = b.center().x(), b.center().y()
        dy = b.height() * 0.2
        _arrow(p, QPointF(cx - w * 0.3, cy - dy), QPointF(cx + w * 0.3, cy - dy), w / 18)
        _arrow(p, QPointF(cx + w * 0.3, cy + dy), QPointF(cx - w * 0.3, cy + dy), w / 18)
        if kind == "l3switch":
            f = p.font()
            f.setBold(True)
            f.setPixelSize(max(6, int(w * 0.2)))
            p.setFont(f)
            p.setPen(QColor(c))
            p.setBrush(white)
            tag = QRectF(b.right() - w * 0.3, b.top() - w * 0.14, w * 0.3, w * 0.24)
            p.drawRoundedRect(tag, 3, 3)
            p.drawText(tag, Qt.AlignCenter, "L3")
    elif kind == "firewall":
        b = r.adjusted(w * 0.1, w * 0.12, -w * 0.1, -w * 0.12)
        p.setBrush(_grad(b, c))
        p.drawRoundedRect(b, w * 0.05, w * 0.05)
        p.setPen(QPen(white, w / 36))
        rows = 4
        hh = b.height() / rows
        for i in range(1, rows):
            p.drawLine(QPointF(b.left(), b.top() + i * hh), QPointF(b.right(), b.top() + i * hh))
        for i in range(rows):
            off = 0.5 if i % 2 else 0.0
            for k in range(1, 3):
                x = b.left() + (k + off - 0.25) * b.width() / 2.6
                if b.left() < x < b.right():
                    p.drawLine(QPointF(x, b.top() + i * hh), QPointF(x, b.top() + (i + 1) * hh))
    elif kind == "host":
        scr = QRectF(r.left() + w * 0.12, r.top() + w * 0.12, w * 0.76, w * 0.52)
        p.setBrush(_grad(scr, c))
        p.drawRoundedRect(scr, w * 0.05, w * 0.05)
        p.setBrush(QColor("#b3e5fc"))
        p.drawRect(scr.adjusted(w * 0.06, w * 0.06, -w * 0.06, -w * 0.06))
        p.setBrush(_grad(scr, c))
        p.drawRect(QRectF(r.center().x() - w * 0.06, scr.bottom(), w * 0.12, w * 0.12))
        p.drawRoundedRect(QRectF(r.center().x() - w * 0.25, scr.bottom() + w * 0.12, w * 0.5, w * 0.08), 3, 3)
    elif kind == "server":
        b = QRectF(r.center().x() - w * 0.24, r.top() + w * 0.06, w * 0.48, w * 0.88)
        p.setBrush(_grad(b, c))
        p.drawRoundedRect(b, w * 0.05, w * 0.05)
        p.setPen(QPen(white, w / 30))
        for i in range(3):
            y = b.top() + b.height() * (0.2 + i * 0.22)
            p.drawLine(QPointF(b.left() + w * 0.06, y), QPointF(b.right() - w * 0.14, y))
            p.setBrush(QColor("#69f0ae"))
            p.drawEllipse(QPointF(b.right() - w * 0.08, y), w * 0.025, w * 0.025)
    elif kind == "ap":
        base = QRectF(r.left() + w * 0.18, r.top() + w * 0.55, w * 0.64, w * 0.25)
        p.setBrush(_grad(base, c))
        p.drawRoundedRect(base, w * 0.1, w * 0.1)
        p.setPen(QPen(QColor(c), w / 18, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for k in range(3):
            rad = w * (0.14 + k * 0.12)
            p.drawArc(QRectF(r.center().x() - rad, r.top() + w * 0.5 - rad, 2 * rad, 2 * rad), 45 * 16, 90 * 16)
    elif kind == "cloud":
        path = QPainterPath()
        cx, cy = r.center().x(), r.center().y() + w * 0.06
        for dx, dy, rr in ((-0.22, 0.05, 0.2), (0.0, -0.1, 0.26), (0.22, 0.03, 0.21), (-0.05, 0.12, 0.2),
                           (0.15, 0.14, 0.17)):
            path.addEllipse(QPointF(cx + dx * w, cy + dy * w), rr * w, rr * w)
        p.setBrush(_grad(r, c))
        p.drawPath(path.simplified())
    else:
        e = r.adjusted(w * 0.1, w * 0.1, -w * 0.1, -w * 0.1)
        p.setBrush(_grad(e, c))
        p.drawEllipse(e)
        p.setPen(QPen(white, w / 30))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(e.adjusted(e.width() * 0.3, 0, -e.width() * 0.3, 0))
        p.drawLine(QPointF(e.left(), e.center().y()), QPointF(e.right(), e.center().y()))
    p.restore()


@lru_cache(maxsize=128)
def device_pixmap(kind: str, size: int = 48) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_device(p, kind, QRectF(0, 0, size, size))
    p.end()
    return pm


def device_icon(kind: str) -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64):
        ic.addPixmap(device_pixmap(kind, s))
    return ic


@lru_cache(maxsize=64)
def glyph_icon(name: str, color: str = "#9aa3b2", size: int = 22) -> QIcon:
    """Simple line-art toolbar glyphs."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size / 11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    s = size
    m = s * 0.18
    if name == "new":
        p.drawRect(QRectF(m * 1.4, m, s - 2.8 * m, s - 2 * m))
        p.drawLine(QPointF(s / 2, s * 0.38), QPointF(s / 2, s * 0.66))
        p.drawLine(QPointF(s * 0.36, s * 0.52), QPointF(s * 0.64, s * 0.52))
    elif name == "open":
        p.drawPolyline(QPolygonF([QPointF(m, s * 0.3), QPointF(m, s - m), QPointF(s - m, s - m),
                                  QPointF(s - m, s * 0.4), QPointF(s * 0.5, s * 0.4), QPointF(s * 0.4, s * 0.3),
                                  QPointF(m, s * 0.3)]))
    elif name == "save":
        p.drawRect(QRectF(m, m, s - 2 * m, s - 2 * m))
        p.drawRect(QRectF(s * 0.32, m, s * 0.36, s * 0.26))
        p.drawRect(QRectF(s * 0.3, s * 0.58, s * 0.4, s * 0.24))
    elif name == "connect":
        p.drawEllipse(QPointF(s * 0.25, s * 0.72), s * 0.1, s * 0.1)
        p.drawEllipse(QPointF(s * 0.75, s * 0.28), s * 0.1, s * 0.1)
        p.drawLine(QPointF(s * 0.32, s * 0.65), QPointF(s * 0.68, s * 0.35))
    elif name == "select":
        p.drawPolygon(QPolygonF([QPointF(s * 0.3, m), QPointF(s * 0.3, s * 0.78), QPointF(s * 0.45, s * 0.62),
                                 QPointF(s * 0.58, s * 0.86), QPointF(s * 0.68, s * 0.8), QPointF(s * 0.55, s * 0.58),
                                 QPointF(s * 0.75, s * 0.56)]))
    elif name == "play":
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF([QPointF(s * 0.32, m), QPointF(s * 0.32, s - m), QPointF(s * 0.8, s / 2)]))
    elif name == "test":
        p.drawPolyline(QPolygonF([QPointF(m, s * 0.55), QPointF(s * 0.4, s * 0.8), QPointF(s - m, s * 0.25)]))
    elif name == "validate":
        p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        p.drawLine(QPointF(s / 2, s * 0.32), QPointF(s / 2, s * 0.56))
        p.drawPoint(QPointF(s / 2, s * 0.7))
    elif name == "fit":
        for (x, y, dx, dy) in ((m, m, 1, 1), (s - m, m, -1, 1), (m, s - m, 1, -1), (s - m, s - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * s * 0.22, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * s * 0.22))
    elif name == "console":
        p.drawRect(QRectF(m * 0.8, m * 1.2, s - 1.6 * m, s - 2.4 * m))
        p.drawPolyline(QPolygonF([QPointF(s * 0.3, s * 0.4), QPointF(s * 0.44, s * 0.52), QPointF(s * 0.3, s * 0.64)]))
        p.drawLine(QPointF(s * 0.5, s * 0.66), QPointF(s * 0.7, s * 0.66))
    elif name == "restore":
        p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 30 * 16, 300 * 16)
        p.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.78, s * 0.42))
        p.drawLine(QPointF(s * 0.78, s * 0.42), QPointF(s * 0.58, s * 0.42))
    elif name == "help":
        p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        f = p.font()
        f.setPixelSize(int(s * 0.55))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "?")
    elif name == "sun":
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.18, s * 0.18)
        import math
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.3, s / 2 + math.sin(a) * s * 0.3),
                       QPointF(s / 2 + math.cos(a) * s * 0.42, s / 2 + math.sin(a) * s * 0.42))
    elif name == "moon":
        path = QPainterPath()
        path.addEllipse(QPointF(s / 2, s / 2), s * 0.34, s * 0.34)
        cut = QPainterPath()
        cut.addEllipse(QPointF(s * 0.66, s * 0.38), s * 0.3, s * 0.3)
        p.setBrush(QColor(color))
        p.drawPath(path.subtracted(cut))
    elif name == "settings":
        import math
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.14, s * 0.14)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.22, s / 2 + math.sin(a) * s * 0.22),
                       QPointF(s / 2 + math.cos(a) * s * 0.38, s / 2 + math.sin(a) * s * 0.38))
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.26, s * 0.26)
    elif name == "undo":
        p.drawArc(QRectF(m, s * 0.3, s - 2 * m, s * 0.55), 0, 180 * 16)
        p.drawLine(QPointF(m, s * 0.58), QPointF(m, s * 0.36))
        p.drawLine(QPointF(m, s * 0.58), QPointF(m + s * 0.2, s * 0.56))
    elif name == "redo":
        p.drawArc(QRectF(m, s * 0.3, s - 2 * m, s * 0.55), 0, 180 * 16)
        p.drawLine(QPointF(s - m, s * 0.58), QPointF(s - m, s * 0.36))
        p.drawLine(QPointF(s - m, s * 0.58), QPointF(s - m - s * 0.2, s * 0.56))
    elif name == "export":
        p.drawLine(QPointF(s / 2, s * 0.62), QPointF(s / 2, m))
        p.drawPolyline(QPolygonF([QPointF(s * 0.32, s * 0.32), QPointF(s / 2, m), QPointF(s * 0.68, s * 0.32)]))
        p.drawPolyline(QPolygonF([QPointF(m, s * 0.55), QPointF(m, s - m), QPointF(s - m, s - m),
                                  QPointF(s - m, s * 0.55)]))
    p.end()
    return QIcon(pm)
