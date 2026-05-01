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
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
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

from pipeline_designer.domain.models import ComponentDefinition, Port, PortDirection

CELL = 30  # pixels per grid unit


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
    ) -> None:
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self._name = port_name
        self._gx = gx
        self._gy = gy
        self._direction = direction
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
        self._reposition_label()

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
        """Clamp port position to the new component size."""
        if self._gx == old_w:
            self._gx = new_w
        elif self._gx > new_w:
            self._gx = new_w

        if self._gy == old_h:
            self._gy = new_h
        elif self._gy > new_h:
            self._gy = new_h

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
            self._gx, self._gy = 0, max(0, min(h, round(sp.y() / CELL)))
        elif self._edge == "right":
            self._gx, self._gy = w, max(0, min(h, round(sp.y() / CELL)))
        elif self._edge == "top":
            self._gx, self._gy = max(0, min(w, round(sp.x() / CELL))), 0
        elif self._edge == "bottom":
            self._gx, self._gy = max(0, min(w, round(sp.x() / CELL))), h

        self._refresh_pos()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self._scene_ref.port_position_changed.emit(self._name, self._gx, self._gy)
        event.accept()

    def hoverEnterEvent(self, event) -> None:
        self.setPen(QPen(QColor("#ffffff"), 2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        color = self._COLORS.get(self._direction, QColor("#888888"))
        self.setPen(QPen(color.darker(150), 1.5))
        super().hoverLeaveEvent(event)


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

        self._body: QGraphicsRectItem | None = None
        self._port_handles: dict[str, _PortHandle] = {}
        self._resize_handles: dict[str, _ResizeHandle] = {}

    # ------------------------------------------------------------------
    # Public API

    def grid_size(self) -> tuple[int, int]:
        return (self._grid_w, self._grid_h)

    def set_component(self, comp: ComponentDefinition) -> None:
        """Build the scene from a ComponentDefinition."""
        self.clear()
        self._port_handles = {}
        self._resize_handles = {}

        self._grid_w = comp.visual.width
        self._grid_h = comp.visual.height
        self._body_color = QColor(comp.visual.color)

        self._build_body()
        self._build_port_handles(comp.ports)
        self._build_resize_handles()
        self._refresh_scene_rect()

    def apply_resize(self, new_w: int, new_h: int) -> None:
        """Update scene to reflect a new component size (from drag)."""
        old_w, old_h = self._grid_w, self._grid_h
        self._grid_w, self._grid_h = new_w, new_h

        if self._body:
            self._body.setRect(0, 0, _px(new_w), _px(new_h))

        for handle in self._port_handles.values():
            handle.update_for_resize(old_w, old_h, new_w, new_h)

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

    def _build_port_handles(self, ports: list[Port]) -> None:
        for port in ports:
            gx, gy = port.position if port.position else (0, self._grid_h // 2)
            handle = _PortHandle(port.name, gx, gy, port.direction, self)
            self.addItem(handle)
            self._port_handles[port.name] = handle

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
        self._w_spin.setValue(comp.visual.width)
        self._h_spin.setValue(comp.visual.height)
        self._spin_updating = False
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

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
