"""Topology canvas: devices, cables, site zones, connect mode and packet animation."""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QEasingCurve, QLineF, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen,
                           QTransform)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
                               QInputDialog, QMenu)

from ..core.lab import Lab
from .icons import KIND_LABELS, paint_device
from .theme import palette

MIME_KIND = "application/x-packetlab-kind"


class NodeItem(QGraphicsObject):
    moved = Signal(str)

    def __init__(self, name: str, kind: str, size: int, view: "Canvas"):
        super().__init__()
        self.name, self.kind, self.size, self.view = name, kind, size, view
        self.down = False
        self.ip_text = ""
        self.links: List["LinkItem"] = []
        self.flash = 0.0
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.setToolTip(f"{name} ({KIND_LABELS.get(kind, kind)})\nDouble-click to open the console")

    def boundingRect(self) -> QRectF:
        s = self.size
        return QRectF(-s / 2 - 40, -s / 2 - 6, s + 80, s + 44)

    def paint(self, p: QPainter, opt, widget=None):
        pal = self.view.pal
        s = self.size
        r = QRectF(-s / 2, -s / 2, s, s)
        if self.isSelected() or self.flash > 0:
            col = QColor(self.view.accent)
            if self.flash > 0:
                col = QColor("#ffca28")
                col.setAlphaF(min(1.0, self.flash))
            p.setPen(QPen(col, 2.5))
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 40))
            p.drawRoundedRect(r.adjusted(-6, -6, 6, 6), 10, 10)
        if self.down:
            p.setOpacity(0.35)
        paint_device(p, self.kind, r)
        p.setOpacity(1.0)
        if self.down:
            p.setPen(QPen(QColor(pal["bad"]), 3))
            p.drawLine(r.topLeft() + QPointF(6, 6), r.bottomRight() - QPointF(6, 6))
            p.drawLine(r.topRight() + QPointF(-6, 6), r.bottomLeft() + QPointF(6, -6))
        f = QFont(self.view.font())
        f.setBold(True)
        f.setPointSizeF(max(7.0, f.pointSizeF()))
        p.setFont(f)
        p.setPen(QColor(pal["text"]))
        p.drawText(QRectF(-s / 2 - 40, s / 2 + 2, s + 80, 16), Qt.AlignHCenter | Qt.AlignTop, self.name)
        if self.ip_text and self.view.s["show_ip_labels"]:
            f.setBold(False)
            f.setPointSizeF(max(6.5, f.pointSizeF() - 1.5))
            p.setFont(f)
            p.setPen(QColor(pal["muted"]))
            p.drawText(QRectF(-s / 2 - 40, s / 2 + 18, s + 80, 16), Qt.AlignHCenter | Qt.AlignTop, self.ip_text)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.view.s["snap"] and self.view.dragging_nodes:
            g = max(1, int(self.view.s["grid_size"]))
            return QPointF(round(value.x() / g) * g, round(value.y() / g) * g)
        if change == QGraphicsItem.ItemPositionHasChanged:
            for l in self.links:
                l.update_path()
            self.view.zones_dirty()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, ev):
        self.view.open_console.emit(self.name)
        ev.accept()


class LinkItem(QGraphicsPathItem):
    def __init__(self, idx: int, a: NodeItem, b: NodeItem, a_if: str, b_if: str, view: "Canvas"):
        super().__init__()
        self.idx, self.a, self.b, self.a_if, self.b_if, self.view = idx, a, b, a_if, b_if, view
        self.offset = 0.0
        self.state = "up"
        self.label = ""
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(5)
        self.setAcceptHoverEvents(True)
        a.links.append(self)
        b.links.append(self)

    def curve(self) -> QPainterPath:
        pa, pb = self.a.pos(), self.b.pos()
        path = QPainterPath(pa)
        if abs(self.offset) < 0.1:
            path.lineTo(pb)
        else:
            mid = (pa + pb) / 2
            d = pb - pa
            ln = math.hypot(d.x(), d.y()) or 1
            nx, ny = -d.y() / ln, d.x() / ln
            ctrl = QPointF(mid.x() + nx * self.offset, mid.y() + ny * self.offset)
            path.quadTo(ctrl, pb)
        return path

    def update_path(self):
        self.prepareGeometryChange()
        self.setPath(self.curve())

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(12)
        return st.createStroke(self.path())

    def boundingRect(self):
        return self.shape().boundingRect().adjusted(-60, -30, 60, 30)

    def paint(self, p: QPainter, opt, widget=None):
        pal = self.view.pal
        w = float(self.view.s["link_width"])
        if self.state == "down":
            pen = QPen(QColor(pal["bad"]), w, Qt.DashLine)
        elif self.state == "blocked":
            pen = QPen(QColor(pal["warn"]), w, Qt.DashDotLine)
        else:
            col = QColor(pal["ok"]) if self.view.theme == "dark" else QColor("#2e7d32")
            if self.label.startswith("wan") or "wan" in self.label:
                col = QColor(self.view.s["wan_color"])
            pen = QPen(col, w)
        if self.isSelected():
            sel = QPen(QColor(self.view.accent), w + 5)
            sel.setColor(QColor(self.view.accent).lighter(120))
            p.setPen(sel)
            p.drawPath(self.path())
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())
        path = self.path()
        f = QFont(self.view.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() - 2))
        p.setFont(f)
        if self.view.s["show_iface_labels"]:
            p.setPen(QColor(pal["muted"]))
            for t, name in ((0.2, self.a_if), (0.8, self.b_if)):
                pt = path.pointAtPercent(t)
                p.drawText(QRectF(pt.x() - 45, pt.y() - 16, 90, 14), Qt.AlignCenter, name)
        if self.view.s["show_link_labels"] and self.label:
            pt = path.pointAtPercent(0.5)
            p.setPen(QColor(pal["text"]))
            p.drawText(QRectF(pt.x() - 70, pt.y() + 2, 140, 14), Qt.AlignCenter, self.label)


class Canvas(QGraphicsView):
    node_selected = Signal(str)
    link_selected = Signal(int)
    nothing_selected = Signal()
    open_console = Signal(str)
    status = Signal(str)

    def __init__(self, lab: Lab, settings, parent=None):
        super().__init__(parent)
        self.lab, self.s = lab, settings
        self.theme = settings["theme"]
        self.pal = palette(self.theme)
        self.accent = "#1e88e5"
        self.sc = QGraphicsScene(self)
        self.sc.setSceneRect(-5000, -5000, 10000, 10000)
        self.setScene(self.sc)
        self.setRenderHint(QPainter.Antialiasing, bool(settings["antialias"]))
        self.setRenderHint(QPainter.TextAntialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setAcceptDrops(True)
        self.nodes: Dict[str, NodeItem] = {}
        self.links: Dict[int, LinkItem] = {}
        self.zone_items = []
        self.connect_mode = False
        self.pending: Optional[tuple] = None
        self.rubber: Optional[QGraphicsPathItem] = None
        self.place_kind: Optional[str] = None
        self.dragging_nodes = False
        self._panning = None
        self._zones_dirty = False
        self.sc.selectionChanged.connect(self._sel_changed)

    # ------------------------------------------------------------------ theme
    def apply_theme(self, theme: str, accent: str):
        self.theme = theme
        self.pal = palette(theme)
        self.accent = accent
        self.setBackgroundBrush(QColor(self.pal["canvas"]))
        self.setRenderHint(QPainter.Antialiasing, bool(self.s["antialias"]))
        self.rebuild()

    def drawBackground(self, p: QPainter, rect: QRectF):
        p.fillRect(rect, QColor(self.pal["canvas"]))
        if not self.s["grid_show"]:
            return
        g = max(5, int(self.s["grid_size"]))
        scale = self.transform().m11()
        if g * scale < 6:
            return
        p.setPen(QPen(QColor(self.pal["grid"]), 1))
        left = int(math.floor(rect.left() / g)) * g
        top = int(math.floor(rect.top() / g)) * g
        lines = []
        x = left
        while x < rect.right():
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
            x += g
        y = top
        while y < rect.bottom():
            lines.append(QLineF(rect.left(), y, rect.right(), y))
            y += g
        p.drawLines(lines)

    # ------------------------------------------------------------------ build
    def rebuild(self):
        sel_nodes = {n for n, it in self.nodes.items() if it.isSelected()}
        sel_links = {i for i, it in self.links.items() if it.isSelected()}
        self.sc.blockSignals(True)
        self.sc.clear()
        self.sc.blockSignals(False)
        self.nodes.clear()
        self.links.clear()
        self.zone_items = []
        self.rubber = None
        self.pending = None
        t = self.lab.topo
        try:
            eng = self.lab.engine
        except Exception:  # noqa: BLE001 - draw even if the engine cannot converge
            eng = None
        lay = (self.lab.raw.get("layout") or {}).get("nodes") or {}
        size = int(self.s["icon_size"])
        auto_i = 0
        for name, n in t.nodes.items():
            it = NodeItem(name, n.kind, size, self)
            pos = lay.get(name)
            if isinstance(pos, dict) and "x" in pos:
                it.setPos(float(pos["x"]), float(pos["y"]))
            else:
                it.setPos(80 + (auto_i % 6) * 140, 80 + (auto_i // 6) * 130)
                auto_i += 1
                self.lab.move_node(name, it.x(), it.y())
            it.down = name in self.lab.down_nodes
            if eng is not None:
                ip = t.resolve_host(name) or eng._lease_ip(name)
                it.ip_text = str(ip) if ip else ""
            self.sc.addItem(it)
            self.nodes[name] = it
            if name in sel_nodes:
                it.setSelected(True)
        blocked = set()
        if eng is not None:
            for k in eng.blocked:
                li = eng.edges[k]["link"]
                if li is not None:
                    blocked.add(li)
        pair_count: Dict[tuple, int] = {}
        for l in t.links:
            a, b = self.nodes.get(l.a_node), self.nodes.get(l.b_node)
            if not a or not b:
                continue
            key = tuple(sorted((l.a_node, l.b_node)))
            k = pair_count.get(key, 0)
            pair_count[key] = k + 1
            li = LinkItem(l.idx, a, b, l.a_if, l.b_if, self)
            li.offset = 0 if k == 0 else (((k + 1) // 2) * 26 * (1 if k % 2 else -1))
            if (l.a_node, l.b_node) != key:
                li.offset = -li.offset
            up = eng.link_up(l) if eng is not None else True
            li.state = "down" if not up else ("blocked" if (l.idx in blocked and self.s["show_stp_blocked"]) else "up")
            from ..core.model import fmt_bw
            li.label = f"{l.label + ': ' if l.label else ''}{l.profile.name} {fmt_bw(l.profile.bandwidth)}"
            li.setToolTip(f"{l.a_node}:{l.a_if}  <->  {l.b_node}:{l.b_if}\nprofile {l.profile.name}, "
                          f"{fmt_bw(l.profile.bandwidth)}, {l.delay_ms:.3f} ms" + (f"\nlabel {l.label}" if l.label else "")
                          + ("\nSTATE: DOWN" if li.state == "down" else "") +
                          ("\nspanning tree: blocked (alternate)" if li.state == "blocked" else ""))
            li.update_path()
            self.sc.addItem(li)
            self.links[l.idx] = li
            if l.idx in sel_links:
                li.setSelected(True)
        self._build_zones()

    def zones_dirty(self):
        if not self._zones_dirty:
            self._zones_dirty = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(30, self._build_zones)

    def _build_zones(self):
        self._zones_dirty = False
        for z in self.zone_items:
            self.sc.removeItem(z)
        self.zone_items = []
        if not self.s["show_site_zones"]:
            return
        t = self.lab.topo
        for sid, site in (t.sites or {}).items():
            members = [self.nodes[n] for n, nd in t.nodes.items() if nd.site == sid and n in self.nodes]
            if not members:
                continue
            r = QRectF()
            for m in members:
                r = r.united(m.sceneBoundingRect())
            r = r.adjusted(-18, -30, 18, 8)
            scope = (site or {}).get("scope", "lan")
            col = QColor(self.s.get(f"{scope}_color", "#1e88e5"))
            path = QPainterPath()
            path.addRoundedRect(r, 16, 16)
            fill = QColor(col)
            fill.setAlpha(28 if self.theme == "dark" else 22)
            item = self.sc.addPath(path, QPen(QColor(col.red(), col.green(), col.blue(), 140), 1.4, Qt.DashLine),
                                   QBrush(fill))
            item.setZValue(-10)
            label = f"{(site or {}).get('label', sid)}  ·  {scope.upper()}"
            txt = self.sc.addSimpleText(label)
            f = QFont(self.font())
            f.setBold(True)
            txt.setFont(f)
            txt.setBrush(QBrush(col.lighter(115) if self.theme == "dark" else col.darker(120)))
            txt.setPos(r.left() + 12, r.top() + 6)
            txt.setZValue(-9)
            self.zone_items += [item, txt]

    # ------------------------------------------------------------------ selection
    def _sel_changed(self):
        items = self.sc.selectedItems()
        nodes = [i for i in items if isinstance(i, NodeItem)]
        links = [i for i in items if isinstance(i, LinkItem)]
        if len(nodes) == 1 and not links:
            self.node_selected.emit(nodes[0].name)
        elif len(links) == 1 and not nodes:
            self.link_selected.emit(links[0].idx)
        elif not items:
            self.nothing_selected.emit()

    def selected_nodes(self) -> List[str]:
        return [i.name for i in self.sc.selectedItems() if isinstance(i, NodeItem)]

    def selected_links(self) -> List[int]:
        return [i.idx for i in self.sc.selectedItems() if isinstance(i, LinkItem)]

    def select_node(self, name: str):
        self.sc.clearSelection()
        if name in self.nodes:
            self.nodes[name].setSelected(True)
            self.centerOn(self.nodes[name])

    # ------------------------------------------------------------------ view helpers
    def fit(self):
        r = QRectF()
        for it in list(self.nodes.values()) + self.zone_items:
            r = r.united(it.sceneBoundingRect())
        if r.isEmpty():
            self.resetTransform()
            self.centerOn(0, 0)
            return
        self.fitInView(r.adjusted(-60, -60, 60, 60), Qt.KeepAspectRatio)
        if self.transform().m11() > 1.6:
            self.setTransform(QTransform().scale(1.6, 1.6))
            self.centerOn(r.center())

    def wheelEvent(self, ev):
        step = float(self.s["zoom_step"])
        f = step if ev.angleDelta().y() > 0 else 1 / step
        cur = self.transform().m11()
        if 0.08 < cur * f < 6:
            self.scale(f, f)

    def to_image(self) -> QImage:
        r = QRectF()
        for it in self.sc.items():
            r = r.united(it.sceneBoundingRect())
        r = r.adjusted(-30, -30, 30, 30)
        img = QImage(int(r.width() * 2), int(r.height() * 2), QImage.Format_ARGB32)
        img.fill(QColor(self.pal["canvas"]))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self.sc.render(p, QRectF(0, 0, img.width(), img.height()), r)
        p.end()
        return img

    # ------------------------------------------------------------------ mouse
    def set_connect_mode(self, on: bool):
        self.connect_mode = on
        self.place_kind = None
        self._cancel_pending()
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.status.emit("Connect mode: click the first device (Esc to stop)" if on else "")

    def set_place_kind(self, kind: Optional[str]):
        self.place_kind = kind
        if kind:
            self.set_connect_mode(False)
            self.place_kind = kind
            self.viewport().setCursor(Qt.CrossCursor)
            self.status.emit(f"Click on the canvas to place a {KIND_LABELS.get(kind, kind)} (Esc to cancel)")

    def _cancel_pending(self):
        self.pending = None
        if self.rubber is not None:
            self.sc.removeItem(self.rubber)
            self.rubber = None

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            if self.connect_mode or self.place_kind:
                self.set_connect_mode(False)
                self.place_kind = None
                self.viewport().setCursor(Qt.ArrowCursor)
                self.status.emit("")
                return
        super().keyPressEvent(ev)

    def _node_at(self, pos) -> Optional[NodeItem]:
        for it in self.items(pos):
            if isinstance(it, NodeItem):
                return it
        return None

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton:
            self._panning = ev.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.LeftButton and self.place_kind:
            p = self.mapToScene(ev.position().toPoint())
            g = int(self.s["grid_size"]) if self.s["snap"] else 1
            kind = self.place_kind
            self.place_kind = None
            self.viewport().setCursor(Qt.ArrowCursor)
            self.status.emit("")
            self.lab.add_node(kind, round(p.x() / g) * g, round(p.y() / g) * g)
            return
        if ev.button() == Qt.LeftButton and self.connect_mode:
            node = self._node_at(ev.position().toPoint())
            if node is None:
                self._cancel_pending()
                self.status.emit("Connect mode: click the first device (Esc to stop)")
                return
            ifn = self.ask_iface(node.name)
            if not ifn:
                return
            if self.pending is None:
                self.pending = (node.name, ifn)
                self.rubber = self.sc.addPath(QPainterPath(), QPen(QColor(self.accent), 2, Qt.DashLine))
                self.status.emit(f"Connect {node.name}:{ifn} to ... click the second device")
            else:
                a, a_if = self.pending
                self._cancel_pending()
                if a == node.name and a_if == ifn:
                    self.status.emit("Cannot connect an interface to itself")
                    return
                prof = self.s["default_profile"] if self.s["default_profile"] in self.lab.topo.profiles else None
                self.lab.add_link(a, a_if, node.name, ifn, prof)
                self.status.emit(f"Connected {a}:{a_if} <-> {node.name}:{ifn}. Click the next device or press Esc.")
            return
        if ev.button() == Qt.LeftButton:
            self.dragging_nodes = self._node_at(ev.position().toPoint()) is not None
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._panning is not None:
            d = ev.position() - self._panning
            self._panning = ev.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        if self.rubber is not None and self.pending:
            a = self.nodes.get(self.pending[0])
            if a:
                path = QPainterPath(a.pos())
                path.lineTo(self.mapToScene(ev.position().toPoint()))
                self.rubber.setPath(path)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MiddleButton and self._panning is not None:
            self._panning = None
            self.viewport().setCursor(Qt.CrossCursor if self.connect_mode else Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(ev)
        if self.dragging_nodes:
            self.dragging_nodes = False
            lay = (self.lab.raw.get("layout") or {}).get("nodes") or {}
            for name, it in self.nodes.items():
                old = lay.get(name) or {}
                if round(it.x()) != old.get("x") or round(it.y()) != old.get("y"):
                    self.lab.move_node(name, it.x(), it.y())

    def ask_iface(self, node: str) -> Optional[str]:
        t = self.lab.topo
        n = t.nodes.get(node)
        used = set()
        for l in t.links:
            if l.a_node == node:
                used.add(l.a_if)
            if l.b_node == node:
                used.add(l.b_if)
        free = [i for i, ifc in n.ifaces.items() if i not in used and ifc.svi_vlan is None and not ifc.loopback
                and not ifc.tunnel and not ifc.lag_members] if n else []
        new = self.lab.next_iface(node)
        items = free + [f"{new}  (new)"]
        it, ok = QInputDialog.getItem(self, "Choose interface", f"Interface on {node}:", items, 0, True)
        if not ok or not it:
            return None
        return it.replace("(new)", "").strip()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(MIME_KIND):
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, ev):
        if ev.mimeData().hasFormat(MIME_KIND):
            kind = bytes(ev.mimeData().data(MIME_KIND)).decode()
            p = self.mapToScene(ev.position().toPoint())
            g = int(self.s["grid_size"]) if self.s["snap"] else 1
            self.lab.add_node(kind, round(p.x() / g) * g, round(p.y() / g) * g)
            ev.acceptProposedAction()
        else:
            super().dropEvent(ev)

    # ------------------------------------------------------------------ context menu
    def contextMenuEvent(self, ev):
        items = self.items(ev.pos())
        node = next((i for i in items if isinstance(i, NodeItem)), None)
        link = next((i for i in items if isinstance(i, LinkItem)), None)
        m = QMenu(self)
        if node is not None:
            n = node.name
            m.addAction(f"Open console - {n}", lambda: self.open_console.emit(n))
            if n in self.lab.down_nodes:
                m.addAction("Power on", lambda: self.lab.set_failure(node=n, down=False))
            else:
                m.addAction("Power off (simulate failure)", lambda: self.lab.set_failure(node=n, down=True))
            m.addSeparator()
            m.addAction("Delete device", lambda: self.lab.delete_node(n))
        elif link is not None:
            i = link.idx
            if i in self.lab.down_links:
                m.addAction("Bring link up", lambda: self.lab.set_failure(link=i, down=False))
            else:
                m.addAction("Bring link down (simulate cut)", lambda: self.lab.set_failure(link=i, down=True))
            m.addSeparator()
            m.addAction("Delete link", lambda: self.lab.delete_link(i))
        else:
            m.addAction("Fit to window", self.fit)
            if self.lab.down_links or self.lab.down_nodes:
                m.addAction("Restore all failed links and devices", self.lab.clear_failures)
        m.exec(ev.globalPos())

    # ------------------------------------------------------------------ animation
    def flash_node(self, name: str):
        it = self.nodes.get(name)
        if not it:
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(max(300, int(self.s["anim_ms"]) * 2))

        def step(v):
            it.flash = float(v)
            it.update()
        anim.valueChanged.connect(step)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def animate(self, a: str, b: str, color: str = "#ffca28", link_idx: Optional[int] = None,
                done: Optional[Callable] = None):
        na, nb = self.nodes.get(a), self.nodes.get(b)
        if not na or not nb:
            if done:
                done()
            return
        path = None
        if link_idx is not None and link_idx in self.links:
            li = self.links[link_idx]
            path = li.path()
            if li.a is not na:
                path = path.toReversed()
        if path is None:
            path = QPainterPath(na.pos())
            path.lineTo(nb.pos())
        env = QPainterPath()
        env.addRoundedRect(QRectF(-9, -6, 18, 12), 3, 3)
        dot = self.sc.addPath(env, QPen(QColor("#000000"), 1), QBrush(QColor(color)))
        dot.setZValue(50)
        dot.setPos(path.pointAtPercent(0))
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(self.s["anim_ms"]))
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.valueChanged.connect(lambda v: dot.setPos(path.pointAtPercent(float(v))))

        def fin():
            if dot.scene() is not None:
                self.sc.removeItem(dot)
            if done:
                done()
        anim.finished.connect(fin)
        anim.start(QVariantAnimation.DeleteWhenStopped)
