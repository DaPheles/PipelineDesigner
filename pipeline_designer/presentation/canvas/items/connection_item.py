"""Connection graphics item for wires between ports."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain.models import Connection


# Edge → unit vector pointing OUT OF the component from that edge.
# Qt y-axis: positive y is DOWN.
_EDGE_VEC: dict[str, tuple[int, int]] = {
    "left":   (-1,  0),
    "right":  ( 1,  0),
    "top":    ( 0, -1),
    "bottom": ( 0,  1),
}

_ORTHO_STUB = 20   # px — initial stub length at each port end


class ConnectionItem(QGraphicsPathItem):
    """Graphics item representing a connection (wire) between two ports.

    Data wires use a smooth cubic bezier curve.
    Clock and reset wires use orthogonal (right-angle) routing whose initial
    direction follows the port's edge position on the component.
    """

    COLOR_NORMAL   = QColor("#a0a0a0")   # gray        — data nets
    COLOR_CLOCK    = QColor("#5b9bd5")   # blue        — clock nets
    COLOR_RESET    = QColor("#c45911")   # dark orange — reset nets
    COLOR_CONTROL  = QColor("#9b59b6")   # purple      — control nets
    COLOR_SELECTED = QColor("#ffffff")   # white
    COLOR_HOVER    = QColor("#ffcc00")   # yellow
    LINE_WIDTH          = 2.0
    LINE_WIDTH_SELECTED = 3.0

    def __init__(
        self,
        connection: Connection,
        source_pos: QPointF,
        target_pos: QPointF,
        wire_kind: str = "data",
        source_edge: str = "right",
        target_edge: str = "left",
        parent: QGraphicsItem | None = None,
    ):
        """Initialise the connection item.

        Args:
            connection:   The connection model.
            source_pos:   Scene position of the source port centre.
            target_pos:   Scene position of the target port centre.
            wire_kind:    ``"data"``, ``"clock"``, ``"reset"``, or ``"control"``.
            source_edge:  Edge of the source component the port sits on.
            target_edge:  Edge of the target component the port sits on.
            parent:       Parent graphics item.
        """
        super().__init__(parent)

        self._connection   = connection
        self._source_pos   = source_pos
        self._target_pos   = target_pos
        self._wire_kind    = wire_kind
        self._source_edge  = source_edge
        self._target_edge  = target_edge
        self._is_hovered   = False

        self._setup_item()
        self._update_path()

    # ── item setup ────────────────────────────────────────────────────────────

    def _setup_item(self) -> None:
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self._update_appearance()

    def _update_appearance(self) -> None:
        if self.isSelected():
            color = self.COLOR_SELECTED
            width = self.LINE_WIDTH_SELECTED
        elif self._is_hovered:
            color = self.COLOR_HOVER
            width = self.LINE_WIDTH_SELECTED
        elif self._wire_kind == "clock":
            color = self.COLOR_CLOCK
            width = self.LINE_WIDTH
        elif self._wire_kind == "reset":
            color = self.COLOR_RESET
            width = self.LINE_WIDTH
        elif self._wire_kind == "control":
            color = self.COLOR_CONTROL
            width = self.LINE_WIDTH
        else:
            color = self.COLOR_NORMAL
            width = self.LINE_WIDTH

        pen = QPen(color)
        pen.setWidth(int(width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        self.setPen(pen)

    # ── path building ─────────────────────────────────────────────────────────

    def _update_path(self) -> None:
        if self._wire_kind in ("clock", "reset"):
            self.setPath(self._build_orthogonal_path())
        else:
            self.setPath(self._build_bezier_path())  # data and control

    def _build_bezier_path(self) -> QPainterPath:
        """Smooth cubic bezier for data wires."""
        path = QPainterPath()
        path.moveTo(self._source_pos)
        dx = self._target_pos.x() - self._source_pos.x()
        ctrl = max(abs(dx) * 0.5, 50)
        path.cubicTo(
            QPointF(self._source_pos.x() + ctrl, self._source_pos.y()),
            QPointF(self._target_pos.x() - ctrl, self._target_pos.y()),
            self._target_pos,
        )
        return path

    def _build_orthogonal_path(self) -> QPainterPath:
        """Orthogonal (right-angle) router for clock / reset wires.

        Strategy
        --------
        Each end has a short *stub* that exits perpendicularly from its
        component edge.  The stubs' outer endpoints (SE, TE) are then
        connected with at most two axis-aligned segments:

        * src horiz + tgt vert  → vertical first, then horizontal
          (avoids crossing through the target port from the wrong side)
        * src vert  + tgt horiz → horizontal first, then vertical
        * both horiz (opposite) → mid-x column
        * both horiz (same dir) → U-shape in x
        * both vert  (opposite) → mid-y row
        * both vert  (same dir) → U-shape using the extreme y (or x) value
        """
        S = _ORTHO_STUB
        sx, sy = self._source_pos.x(), self._source_pos.y()
        tx, ty = self._target_pos.x(), self._target_pos.y()

        sdx, sdy = _EDGE_VEC.get(self._source_edge, (1, 0))
        tdx, tdy = _EDGE_VEC.get(self._target_edge, (-1, 0))

        # Stub endpoints (first turn on each side)
        sex, sey = sx + sdx * S, sy + sdy * S
        tex, tey = tx + tdx * S, ty + tdy * S

        path = QPainterPath()
        path.moveTo(sx, sy)
        path.lineTo(sex, sey)

        src_h = (sdy == 0)
        tgt_h = (tdy == 0)

        if src_h and tgt_h:
            # Both horizontal exits
            if sdx == tdx:                       # same direction → U-shape in x
                ext = (max(sex, tex) + S) if sdx > 0 else (min(sex, tex) - S)
                path.lineTo(ext, sey)
                path.lineTo(ext, tey)
            else:                                # opposite → mid-x column
                mid_x = (sex + tex) / 2
                path.lineTo(mid_x, sey)
                path.lineTo(mid_x, tey)
            path.lineTo(tex, tey)

        elif not src_h and not tgt_h:
            # Both vertical exits
            if sdy == tdy:                       # same direction → U-shape in y
                ext = max(sey, tey) if sdy > 0 else min(sey, tey)
                path.lineTo(sex, ext)
                path.lineTo(tex, ext)
            else:                                # opposite → mid-y row
                mid_y = (sey + tey) / 2
                path.lineTo(sex, mid_y)
                path.lineTo(tex, mid_y)
            path.lineTo(tex, tey)

        elif src_h:
            # Source horizontal, target vertical:
            # go vertical first to reach the target's y-level, then horizontal.
            # This prevents the wire crossing through the target port from above/below.
            path.lineTo(sex, tey)
            path.lineTo(tex, tey)

        else:
            # Source vertical, target horizontal:
            # go horizontal first to the target's x-level, then vertical.
            path.lineTo(tex, sey)
            path.lineTo(tex, tey)

        path.lineTo(tx, ty)
        return path

    # ── public API ────────────────────────────────────────────────────────────

    def get_connection(self) -> Connection:
        return self._connection

    def set_source_pos(self, pos: QPointF) -> None:
        self._source_pos = pos
        self._update_path()

    def set_target_pos(self, pos: QPointF) -> None:
        self._target_pos = pos
        self._update_path()

    def update_positions(self, source_pos: QPointF, target_pos: QPointF) -> None:
        self._source_pos = source_pos
        self._target_pos = target_pos
        self._update_path()

    # ── hover / paint ─────────────────────────────────────────────────────────

    def hoverEnterEvent(self, event) -> None:
        self._is_hovered = True
        self._update_appearance()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._is_hovered = False
        self._update_appearance()
        super().hoverLeaveEvent(event)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        super().paint(painter, option, widget)


class TempConnectionItem(QGraphicsPathItem):
    """Temporary connection line shown while dragging to create a connection."""

    COLOR         = QColor("#ffcc00")   # yellow
    COLOR_VALID   = QColor("#00ff00")   # green  — over valid target
    COLOR_INVALID = QColor("#ff6666")   # red    — over invalid target
    LINE_WIDTH    = 2.0

    def __init__(self, start_pos: QPointF, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self._start_pos       = start_pos
        self._end_pos         = start_pos
        self._is_valid_target = False
        self._is_over_target  = False
        self._setup_item()
        self._update_path()

    def _setup_item(self) -> None:
        self.setZValue(100)
        self._update_appearance()

    def _update_appearance(self) -> None:
        if self._is_over_target:
            color = self.COLOR_VALID if self._is_valid_target else self.COLOR_INVALID
        else:
            color = self.COLOR

        pen = QPen(color)
        pen.setWidth(int(self.LINE_WIDTH))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)

    def _update_path(self) -> None:
        path = QPainterPath()
        path.moveTo(self._start_pos)
        dx = self._end_pos.x() - self._start_pos.x()
        ctrl = max(abs(dx) * 0.5, 30)
        path.cubicTo(
            QPointF(self._start_pos.x() + ctrl, self._start_pos.y()),
            QPointF(self._end_pos.x() - ctrl,   self._end_pos.y()),
            self._end_pos,
        )
        self.setPath(path)

    def set_end_pos(self, pos: QPointF) -> None:
        self._end_pos = pos
        self._update_path()

    def set_target_state(self, is_over_target: bool, is_valid: bool = False) -> None:
        self._is_over_target  = is_over_target
        self._is_valid_target = is_valid
        self._update_appearance()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint(painter, option, widget)
