"""Connection graphics item for wires between ports."""

from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain.models import Connection


class ConnectionItem(QGraphicsPathItem):
    """Graphics item representing a connection (wire) between two ports.

    Draws a smooth bezier curve from source to target port.
    """

    COLOR_NORMAL = QColor("#a0a0a0")  # Gray
    COLOR_SELECTED = QColor("#ffffff")  # White
    COLOR_HOVER = QColor("#ffcc00")  # Yellow
    LINE_WIDTH = 2.0
    LINE_WIDTH_SELECTED = 3.0

    def __init__(
        self,
        connection: Connection,
        source_pos: QPointF,
        target_pos: QPointF,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the connection item.

        Args:
            connection: The connection model.
            source_pos: Position of the source port.
            target_pos: Position of the target port.
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._connection = connection
        self._source_pos = source_pos
        self._target_pos = target_pos
        self._is_hovered = False

        self._setup_item()
        self._update_path()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)  # Below components, above stages

        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
        if self.isSelected():
            color = self.COLOR_SELECTED
            width = self.LINE_WIDTH_SELECTED
        elif self._is_hovered:
            color = self.COLOR_HOVER
            width = self.LINE_WIDTH_SELECTED
        else:
            color = self.COLOR_NORMAL
            width = self.LINE_WIDTH

        pen = QPen(color)
        pen.setWidth(int(width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)

    def _update_path(self) -> None:
        """Update the bezier curve path."""
        path = QPainterPath()
        path.moveTo(self._source_pos)

        # Calculate control points for smooth bezier curve
        dx = self._target_pos.x() - self._source_pos.x()
        control_offset = max(abs(dx) * 0.5, 50)

        control1 = QPointF(
            self._source_pos.x() + control_offset,
            self._source_pos.y(),
        )
        control2 = QPointF(
            self._target_pos.x() - control_offset,
            self._target_pos.y(),
        )

        path.cubicTo(control1, control2, self._target_pos)
        self.setPath(path)

    def get_connection(self) -> Connection:
        """Get the connection model."""
        return self._connection

    def set_source_pos(self, pos: QPointF) -> None:
        """Update the source position."""
        self._source_pos = pos
        self._update_path()

    def set_target_pos(self, pos: QPointF) -> None:
        """Update the target position."""
        self._target_pos = pos
        self._update_path()

    def update_positions(self, source_pos: QPointF, target_pos: QPointF) -> None:
        """Update both endpoint positions."""
        self._source_pos = source_pos
        self._target_pos = target_pos
        self._update_path()

    def hoverEnterEvent(self, event) -> None:
        """Handle hover enter."""
        self._is_hovered = True
        self._update_appearance()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        """Handle hover leave."""
        self._is_hovered = False
        self._update_appearance()
        super().hoverLeaveEvent(event)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the connection."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint(painter, option, widget)


class TempConnectionItem(QGraphicsPathItem):
    """Temporary connection line shown while dragging to create a connection."""

    COLOR = QColor("#ffcc00")  # Yellow
    COLOR_VALID = QColor("#00ff00")  # Green when over valid target
    COLOR_INVALID = QColor("#ff6666")  # Light red when over invalid target
    LINE_WIDTH = 2.0

    def __init__(self, start_pos: QPointF, parent: QGraphicsItem | None = None):
        """Initialize the temporary connection.

        Args:
            start_pos: Starting position (source port).
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._start_pos = start_pos
        self._end_pos = start_pos
        self._is_valid_target = False
        self._is_over_target = False

        self._setup_item()
        self._update_path()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setZValue(100)  # Above everything
        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
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
        """Update the bezier curve path."""
        path = QPainterPath()
        path.moveTo(self._start_pos)

        # Calculate control points for smooth bezier curve
        dx = self._end_pos.x() - self._start_pos.x()
        control_offset = max(abs(dx) * 0.5, 30)

        control1 = QPointF(
            self._start_pos.x() + control_offset,
            self._start_pos.y(),
        )
        control2 = QPointF(
            self._end_pos.x() - control_offset,
            self._end_pos.y(),
        )

        path.cubicTo(control1, control2, self._end_pos)
        self.setPath(path)

    def set_end_pos(self, pos: QPointF) -> None:
        """Update the end position (follows mouse)."""
        self._end_pos = pos
        self._update_path()

    def set_target_state(self, is_over_target: bool, is_valid: bool = False) -> None:
        """Set the target state for visual feedback.

        Args:
            is_over_target: Whether the mouse is over a potential target port.
            is_valid: Whether the target would be a valid connection.
        """
        self._is_over_target = is_over_target
        self._is_valid_target = is_valid
        self._update_appearance()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the temporary connection."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint(painter, option, widget)
