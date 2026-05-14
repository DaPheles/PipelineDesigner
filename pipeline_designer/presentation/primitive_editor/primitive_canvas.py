"""Interactive visual editor for a primitive's shape and port positions.

Scene coordinate system
-----------------------
  origin      = component top-left  (scene 0, 0)
  x increases = right
  y increases = down
  1 grid unit = CELL pixels

Resize handles
--------------
  Three handles allow resizing the component body:
  * right-mid  → drag horizontally to change width
  * bottom-mid → drag vertically  to change height
  * bottom-right corner → drag to change both

  Positions snap to the nearest integer grid unit; minimum size is 1×1.

Port handles
------------
  Each port is a draggable coloured dot constrained to its edge
  (left / right / top / bottom), detected from the initial port position.

  * Left edge  (x == 0)     → drag moves y within [0, height]
  * Right edge (x == width) → drag moves y within [0, height], x tracks width
  * Top edge   (y == 0)     → drag moves x within [0, width]
  * Bottom edge(y == height)→ drag moves x within [0, width], y tracks height

  On width/height change, right-edge and bottom-edge ports are updated so
  they remain clamped to the new boundary.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import ComponentDefinition, Port, PortDirection, PortSignalClass

CELL = 30       # pixels per grid unit
HEADER_H = CELL  # title bar is 1 grid unit tall, matching main canvas convention


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(pixels: float) -> int:
    """Round pixel value to nearest grid unit (returns grid-unit int)."""
    return max(0, round(pixels / CELL))


def _px(grid_units: int) -> float:
    return float(grid_units * CELL)


# ---------------------------------------------------------------------------
# Scene items
# ---------------------------------------------------------------------------

class _PortHandle(QGraphicsEllipseItem):
    """Draggable port dot constrained to one edge of the component body."""

    RADIUS = 6.0
    _COLORS = {
        PortDirection.IN:    QColor("#3498db"),
        PortDirection.OUT:   QColor("#2ecc71"),
        PortDirection.INOUT: QColor("#e67e22"),
    }

    def __init__(
        self,
        port_name: str,
        gx: int,
        gy: int,
        direction: PortDirection,
        scene: "_PrimitiveScene",
        signal_class: PortSignalClass = PortSignalClass.DATA,
    ) -> None:
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self._name = port_name
        self._gx = gx
        self._gy = gy
        self._direction = direction
        self._signal_class = signal_class
        self._scene_ref = scene
        self._dragging = False
        self._edge: str = "none"

        color = self._COLORS.get(direction, QColor("#888888"))
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(150), 1.5))
        self.setZValue(5)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{port_name} ({direction.value})")
        self.setAcceptHoverEvents(True)

        # Name label
        self._label = QGraphicsTextItem(port_name, self)
        font = QFont("sans-serif", 7)
        self._label.setFont(font)
        self._label.setDefaultTextColor(QColor("#ffffff"))

        self._refresh_pos()

    # ------------------------------------------------------------------

    def port_name(self) -> str:
        return self._name

    def grid_pos(self) -> tuple[int, int]:
        return (self._gx, self._gy)

    def _refresh_pos(self) -> None:
        self.setPos(_px(self._gx), _px(self._gy))
        self._edge = self._detect_edge()
        self._reposition_label()
        self.update()

    def _reposition_label(self) -> None:
        w, h = self._scene_ref.grid_size()
        lw = self._label.boundingRect().width()
        lh = self._label.boundingRect().height()
        r = self.RADIUS

        if self._gx == 0:
            self._label.setPos(r + 2, -lh / 2)
        elif self._gx == w:
            self._label.setPos(-lw - r - 2, -lh / 2)
        elif self._gy == 0:
            self._label.setPos(-lw / 2, r + 2)
        else:
            self._label.setPos(-lw / 2, -lh - r - 2)

    def update_for_resize(self, old_w: int, old_h: int, new_w: int, new_h: int) -> None:
        """Clamp port position to the new component size, keeping off corners."""
        if self._gx == old_w:
            self._gx = new_w
        elif self._gx > new_w:
            self._gx = new_w

        if self._gy == old_h:
            self._gy = new_h
        elif self._gy > new_h:
            self._gy = new_h

        # Push off corners
        if self._gx == 0 or self._gx == new_w:
            self._gy = max(1, min(max(new_h - 1, 1), self._gy))
        if self._gy == 0 or self._gy == new_h:
            self._gx = max(1, min(max(new_w - 1, 1), self._gx))

        self._refresh_pos()

    def _detect_edge(self) -> str:
        w, h = self._scene_ref.grid_size()
        if self._gx == 0:
            return "left"
        if self._gx == w:
            return "right"
        if self._gy == 0:
            return "top"
        if self._gy == h:
            return "bottom"
        return "none"

    # ------------------------------------------------------------------
    # Mouse interaction

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._dragging = True
        self._edge = self._detect_edge()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._dragging:
            return
        sp = event.scenePos()
        w, h = self._scene_ref.grid_size()

        if self._edge == "left":
            self._gx = 0
            self._gy = max(1, min(max(h - 1, 1), round(sp.y() / CELL)))
        elif self._edge == "right":
            self._gx = w
            self._gy = max(1, min(max(h - 1, 1), round(sp.y() / CELL)))
        elif self._edge == "top":
            self._gx = max(1, min(max(w - 1, 1), round(sp.x() / CELL)))
            self._gy = 0
        elif self._edge == "bottom":
            self._gx = max(1, min(max(w - 1, 1), round(sp.x() / CELL)))
            self._gy = h

        self._refresh_pos()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self._scene_ref._update_clock_marks()
            self._scene_ref.port_position_changed.emit(self._name, self._gx, self._gy)
        event.accept()

    def hoverEnterEvent(self, event) -> None:
        self.setPen(QPen(QColor("#ffffff"), 2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        color = self._COLORS.get(self._direction, QColor("#888888"))
        self.setPen(QPen(color.darker(150), 1.5))
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._signal_class == PortSignalClass.CLOCK:
            color = self._COLORS.get(self._direction, QColor("#888888"))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color.darker(150), 1.5))
            painter.drawPolygon(self._clock_triangle())
        else:
            super().paint(painter, option, widget)

    def _clock_triangle(self) -> QPolygonF:
        r = self.RADIUS
        if self._edge == "bottom":
            pts = [QPointF(-r, 0.0), QPointF(0.0, -r), QPointF(r, 0.0)]
        elif self._edge == "top":
            pts = [QPointF(-r, 0.0), QPointF(0.0,  r), QPointF(r, 0.0)]
        elif self._edge == "right":
            pts = [QPointF(0.0, -r), QPointF(-r, 0.0), QPointF(0.0,  r)]
        else:  # left or none — default
            pts = [QPointF(0.0, -r), QPointF( r, 0.0), QPointF(0.0,  r)]
        return QPolygonF(pts)


class _ResizeHandle(QGraphicsRectItem):
    """Drag handle for resizing the component body."""

    SIZE = 10.0

    _CURSORS = {
        "right":  Qt.CursorShape.SizeHorCursor,
        "bottom": Qt.CursorShape.SizeVerCursor,
        "corner": Qt.CursorShape.SizeFDiagCursor,
    }

    def __init__(self, kind: str, scene: "_PrimitiveScene") -> None:
        s = self.SIZE
        super().__init__(-s / 2, -s / 2, s, s)
        self._kind = kind
        self._scene_ref = scene
        self._dragging = False
        self._drag_scene_start = QPointF()
        self._initial_w = 0
        self._initial_h = 0

        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#333333"), 1))
        self.setZValue(10)
        self.setCursor(self._CURSORS.get(kind, Qt.CursorShape.SizeAllCursor))
        self.setAcceptHoverEvents(True)

    def refresh_position(self, w: int, h: int) -> None:
        if self._kind == "right":
            self.setPos(_px(w), _px(h) / 2)
        elif self._kind == "bottom":
            self.setPos(_px(w) / 2, _px(h))
        else:  # corner
            self.setPos(_px(w), _px(h))

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._dragging = True
        self._drag_scene_start = event.scenePos()
        self._initial_w, self._initial_h = self._scene_ref.grid_size()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._dragging:
            return
        delta = event.scenePos() - self._drag_scene_start
        w, h = self._initial_w, self._initial_h

        if self._kind in ("right", "corner"):
            new_w = max(1, round((w * CELL + delta.x()) / CELL))
        else:
            new_w = w

        if self._kind in ("bottom", "corner"):
            new_h = max(1, round((h * CELL + delta.y()) / CELL))
        else:
            new_h = h

        self._scene_ref.apply_resize(new_w, new_h)
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self._scene_ref.size_changed.emit(*self._scene_ref.grid_size())
        event.accept()

    def hoverEnterEvent(self, event) -> None:
        self.setBrush(QBrush(QColor("#4a90d9")))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setBrush(QBrush(QColor("#ffffff")))
        super().hoverLeaveEvent(event)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

class _PrimitiveScene(QGraphicsScene):
    """QGraphicsScene that holds the editable component preview."""

    size_changed = Signal(int, int)             # grid_w, grid_h
    port_position_changed = Signal(str, int, int)  # name, grid_x, grid_y

    _GRID_COLOR = QColor("#3a3a3a")
    _BODY_BORDER = QPen(QColor("#cccccc"), 2)

    def __init__(self) -> None:
        super().__init__()
        self.setBackgroundBrush(QBrush(QColor("#1e1e1e")))

        self._grid_w = 4
        self._grid_h = 4
        self._body_color = QColor("#4a90d9")
        self._comp_name = ""

        self._body: QGraphicsRectItem | None = None
        self._header: QGraphicsRectItem | None = None
        self._header_text: QGraphicsTextItem | None = None
        self._port_handles: dict[str, _PortHandle] = {}
        self._resize_handles: dict[str, _ResizeHandle] = {}
        self._clock_marks: list[QGraphicsPolygonItem] = []
        self._auto_extended = False

    # ------------------------------------------------------------------
    # Public API

    def grid_size(self) -> tuple[int, int]:
        return (self._grid_w, self._grid_h)

    def set_component(self, comp: ComponentDefinition) -> None:
        """Build the scene from a ComponentDefinition."""
        self.clear()
        self._port_handles = {}
        self._resize_handles = {}
        self._header = None
        self._header_text = None

        self._grid_w = comp.visual.width
        self._grid_h = comp.visual.height
        self._body_color = QColor(comp.visual.color)
        self._comp_name = comp.name

        orig_w, orig_h = self._grid_w, self._grid_h
        port_positions = self._compute_port_positions(comp.ports)
        self._auto_extended = (self._grid_w != orig_w or self._grid_h != orig_h)

        self._clock_marks = []
        self._build_body()
        self._build_header()
        self._build_port_handles(comp.ports, port_positions)
        self._update_clock_marks()
        self._build_resize_handles()
        self._refresh_scene_rect()

    def was_auto_extended(self) -> bool:
        return self._auto_extended

    def apply_resize(self, new_w: int, new_h: int) -> None:
        """Update scene to reflect a new component size (from drag)."""
        old_w, old_h = self._grid_w, self._grid_h
        self._grid_w, self._grid_h = new_w, new_h

        if self._body:
            self._body.setRect(0, 0, _px(new_w), _px(new_h))

        if self._header:
            self._header.setRect(0, 0, _px(new_w), float(HEADER_H))
            self._reposition_header_text()

        for handle in self._port_handles.values():
            handle.update_for_resize(old_w, old_h, new_w, new_h)

        self._update_clock_marks()

        for handle in self._resize_handles.values():
            handle.refresh_position(new_w, new_h)

        self._refresh_scene_rect()

    def update_port_position(self, name: str, gx: int, gy: int) -> None:
        """Move port handle to the given grid position (called from table spinbox)."""
        handle = self._port_handles.get(name)
        if handle:
            handle._gx = gx
            handle._gy = gy
            handle._refresh_pos()

    def get_port_positions(self) -> dict[str, tuple[int, int]]:
        """Return {name: (gx, gy)} for all port handles."""
        return {name: h.grid_pos() for name, h in self._port_handles.items()}

    # ------------------------------------------------------------------
    # Build helpers

    def _build_body(self) -> None:
        self._body = QGraphicsRectItem(0, 0, _px(self._grid_w), _px(self._grid_h))
        self._body.setBrush(QBrush(self._body_color))
        self._body.setPen(self._BODY_BORDER)
        self._body.setZValue(0)
        self.addItem(self._body)

    def _build_header(self) -> None:
        header_color = self._body_color.darker(130)
        self._header = QGraphicsRectItem(0, 0, _px(self._grid_w), float(HEADER_H))
        self._header.setBrush(QBrush(header_color))
        self._header.setPen(Qt.PenStyle.NoPen)
        self._header.setZValue(1)
        self.addItem(self._header)

        self._header_text = QGraphicsTextItem(self._comp_name)
        font = QFont("Arial", 8, QFont.Weight.Bold)
        self._header_text.setFont(font)
        self._header_text.setDefaultTextColor(QColor("#ffffff"))
        self._header_text.setZValue(2)
        self._reposition_header_text()
        self.addItem(self._header_text)

    def _reposition_header_text(self) -> None:
        if self._header_text is None:
            return
        br = self._header_text.boundingRect()
        self._header_text.setPos(
            (_px(self._grid_w) - br.width()) / 2,
            (HEADER_H - br.height()) / 2,
        )

    def _build_port_handles(self, ports: list[Port], positions: dict[str, tuple[int, int]]) -> None:
        for port in ports:
            gx, gy = positions[port.name]
            handle = _PortHandle(port.name, gx, gy, port.direction, self, signal_class=port.signal_class)
            self.addItem(handle)
            self._port_handles[port.name] = handle

    def _compute_port_positions(self, ports: list[Port]) -> dict[str, tuple[int, int]]:
        positions: dict[str, tuple[int, int]] = {}
        taken: set[tuple[int, int]] = set()
        for port in ports:
            if port.position is not None:
                positions[port.name] = port.position
                taken.add(port.position)
        for port in ports:
            if port.position is None:
                pos = self._auto_assign_position(port, taken)
                positions[port.name] = pos
                taken.add(pos)
        return positions

    def _port_border(self, port: Port) -> str:
        if port.signal_class == PortSignalClass.CLOCK:
            return "bottom"
        if port.signal_class == PortSignalClass.RESET:
            return "top"
        if port.direction == PortDirection.IN:
            return "left"
        return "right"

    def _find_free_on_border(self, border: str, taken: set) -> tuple[int, int] | None:
        w, h = self._grid_w, self._grid_h
        if border == "left":
            return next(((0, y) for y in range(1, h) if (0, y) not in taken), None)
        if border == "right":
            return next(((w, y) for y in range(1, h) if (w, y) not in taken), None)
        if border == "top":
            return next(((x, 0) for x in range(1, w) if (x, 0) not in taken), None)
        return next(((x, h) for x in range(1, w) if (x, h) not in taken), None)

    def _auto_assign_position(self, port: Port, taken: set) -> tuple[int, int]:
        border = self._port_border(port)
        while True:
            pos = self._find_free_on_border(border, taken)
            if pos is not None:
                return pos
            if border in ("left", "right"):
                self._grid_h += 1
            else:
                self._grid_w += 1

    def _update_clock_marks(self) -> None:
        for mark in self._clock_marks:
            self.removeItem(mark)
        self._clock_marks.clear()
        r = 10.0
        border_color = QColor("#cccccc")  # matches _BODY_BORDER
        for handle in self._port_handles.values():
            if handle._signal_class != PortSignalClass.CLOCK:
                continue
            cx, cy = handle.pos().x(), handle.pos().y()
            pts = self._clock_triangle_pts(cx, cy, handle._edge, r)
            mark = QGraphicsPolygonItem(QPolygonF(pts))
            mark.setBrush(QBrush(border_color))
            mark.setPen(Qt.PenStyle.NoPen)
            mark.setZValue(3)  # above body and header, below port handles (z=5)
            self.addItem(mark)
            self._clock_marks.append(mark)

    @staticmethod
    def _clock_triangle_pts(cx: float, cy: float, edge: str, r: float) -> list[QPointF]:
        if edge == "bottom":
            return [QPointF(cx - r, cy), QPointF(cx, cy - r), QPointF(cx + r, cy)]
        if edge == "top":
            return [QPointF(cx - r, cy), QPointF(cx, cy + r), QPointF(cx + r, cy)]
        if edge == "right":
            return [QPointF(cx, cy - r), QPointF(cx - r, cy), QPointF(cx, cy + r)]
        return [QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r)]  # left

    def _build_resize_handles(self) -> None:
        for kind in ("right", "bottom", "corner"):
            h = _ResizeHandle(kind, self)
            h.refresh_position(self._grid_w, self._grid_h)
            self.addItem(h)
            self._resize_handles[kind] = h

    def _refresh_scene_rect(self) -> None:
        margin = CELL * 2
        self.setSceneRect(
            -margin, -margin,
            _px(self._grid_w) + 2 * margin,
            _px(self._grid_h) + 2 * margin,
        )

    # ------------------------------------------------------------------
    # Grid background

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)

        pen = QPen(self._GRID_COLOR, 0.5)
        painter.setPen(pen)

        left = int(rect.left() / CELL) * CELL
        top = int(rect.top() / CELL) * CELL

        x = left
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += CELL

        y = top
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += CELL


# ---------------------------------------------------------------------------
# Public widget
# ---------------------------------------------------------------------------

class PrimitiveCanvas(QWidget):
    """Widget wrapping the interactive primitive scene and a size readout.

    Signals
    -------
    size_changed(w, h)
        Emitted when a resize-handle drag finishes.  w, h are in grid units.
    port_position_changed(name, x, y)
        Emitted when a port is dragged to a new position.  x, y are grid units.
    """

    size_changed = Signal(int, int)
    port_position_changed = Signal(str, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = _PrimitiveScene()
        self._scene.size_changed.connect(self._on_scene_size_changed)
        self._scene.port_position_changed.connect(self.port_position_changed)

        self._view = QGraphicsView(self._scene)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Size spinboxes (secondary edit path; primary is drag handles)
        self._w_spin = QSpinBox()
        self._w_spin.setRange(1, 200)
        self._w_spin.setPrefix("W: ")
        self._w_spin.setSuffix(" gu")

        self._h_spin = QSpinBox()
        self._h_spin.setRange(1, 200)
        self._h_spin.setPrefix("H: ")
        self._h_spin.setSuffix(" gu")

        self._w_spin.valueChanged.connect(self._on_spin_changed)
        self._h_spin.valueChanged.connect(self._on_spin_changed)
        self._spin_updating = False

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Size:"))
        spin_row.addWidget(self._w_spin)
        spin_row.addWidget(self._h_spin)
        spin_row.addStretch()

        hint = QLabel(
            "Drag resize handles (□) to change size · Drag port dots to reposition"
        )
        hint.setStyleSheet("color: #888; font-size: 10px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(spin_row)
        layout.addWidget(self._view)
        layout.addWidget(hint)

    # ------------------------------------------------------------------
    # Public interface

    def set_component(self, comp: ComponentDefinition) -> None:
        """Load a ComponentDefinition into the canvas."""
        self._scene.set_component(comp)
        self._spin_updating = True
        w, h = self._scene.grid_size()  # may differ from comp if auto-extended
        self._w_spin.setValue(w)
        self._h_spin.setValue(h)
        self._spin_updating = False
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def was_auto_extended(self) -> bool:
        """Return True if the last set_component auto-extended the grid size."""
        return self._scene.was_auto_extended()

    def update_port_position(self, name: str, x: int, y: int) -> None:
        """Move a port handle to (x, y) in grid units (called from port table)."""
        self._scene.update_port_position(name, x, y)

    def get_port_positions(self) -> dict[str, tuple[int, int]]:
        """Return {port_name: (gx, gy)} for all ports on the canvas."""
        return self._scene.get_port_positions()

    def get_size(self) -> tuple[int, int]:
        """Return current (width, height) in grid units."""
        return self._scene.grid_size()

    # ------------------------------------------------------------------
    # Internal slots

    def _on_scene_size_changed(self, w: int, h: int) -> None:
        self._spin_updating = True
        self._w_spin.setValue(w)
        self._h_spin.setValue(h)
        self._spin_updating = False
        self.size_changed.emit(w, h)

    def _on_spin_changed(self) -> None:
        if self._spin_updating:
            return
        w = self._w_spin.value()
        h = self._h_spin.value()
        self._scene.apply_resize(w, h)
        self.size_changed.emit(w, h)
