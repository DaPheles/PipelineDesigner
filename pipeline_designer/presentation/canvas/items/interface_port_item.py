"""Interface port item for input/output stage ports."""

from typing import Callable
from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import InterfaceDirection, InterfacePort


class InterfacePortItem(QGraphicsEllipseItem):
    """Graphics item for interface ports on input/output stages.

    These are larger than regular ports to indicate they are external
    interface points of the component. They are moveable vertically only.
    """

    # Larger radius for interface ports
    PORT_RADIUS = 12.0
    LABEL_OFFSET = 16.0

    def __init__(
        self,
        interface_port: InterfacePort,
        grid: GridConfig | None = None,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the interface port item.

        Args:
            interface_port: The interface port model.
            grid: Grid configuration for snapping.
            parent: Parent graphics item.
        """
        radius = self.PORT_RADIUS
        super().__init__(-radius, -radius, radius * 2, radius * 2, parent)

        self._interface_port = interface_port
        self._is_connected = False
        self._grid = grid or DEFAULT_GRID

        # Callbacks for connection creation and position changes
        self.on_connection_start: Callable[[], None] | None = None
        self.on_position_changed: Callable[[float], None] | None = None

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)  # Above most items

        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance based on direction and selection."""
        if self._interface_port.direction == InterfaceDirection.INPUT:
            color = QColor("#27ae60")  # Green for input
        else:
            color = QColor("#e67e22")  # Orange for output

        if self._is_connected:
            brush_color = color
        else:
            brush_color = color.lighter(150)

        if self.isSelected():
            pen = QPen(QColor("#ffffff"))
            pen.setWidth(3)
        else:
            pen = QPen(color.darker(120))
            pen.setWidth(2)

        self.setPen(pen)
        self.setBrush(QBrush(brush_color))

    def get_interface_port(self) -> InterfacePort:
        """Get the interface port model."""
        return self._interface_port

    def get_port_id(self) -> UUID:
        """Get the port ID."""
        return self._interface_port.id

    def get_name(self) -> str:
        """Get the port name."""
        return self._interface_port.name

    def is_input(self) -> bool:
        """Check if this is an input port."""
        return self._interface_port.direction == InterfaceDirection.INPUT

    def is_output(self) -> bool:
        """Check if this is an output port."""
        return self._interface_port.direction == InterfaceDirection.OUTPUT

    def set_connected(self, connected: bool) -> None:
        """Set the connection state."""
        self._is_connected = connected
        self._update_appearance()

    def set_highlighted(self, highlighted: bool) -> None:
        """Set the highlight state for connection targeting."""
        if highlighted:
            pen = QPen(QColor("#ffffff"))
            pen.setWidth(3)
            self.setPen(pen)
        else:
            self._update_appearance()

    def itemChange(self, change, value):
        """Handle item changes for vertical-only movement."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # Snap y to grid; preserve whatever x was given (supports programmatic centering)
            new_y = self._grid.snap_to_grid(value.y())
            return QPointF(value.x(), new_y)

        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # Update the interface port model position
            if self.on_position_changed:
                self.on_position_changed(self.pos().y())

        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_appearance()

        return super().itemChange(change, value)

    def update_model_position(self) -> None:
        """Update the interface port model with the current scene position."""
        scene_pos = self.scenePos()
        # Store position in grid units
        grid_x = int(scene_pos.x() / self._grid.size)
        grid_y = int(scene_pos.y() / self._grid.size)
        self._interface_port.position = (grid_x, grid_y)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the interface port."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw the port circle
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawEllipse(self.rect())

        # Draw inner indicator
        inner_rect = self.rect().adjusted(4, 4, -4, -4)
        if self._interface_port.direction == InterfaceDirection.INPUT:
            # Arrow pointing inward (right)
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.setPen(Qt.PenStyle.NoPen)
            center = self.rect().center()
            size = 6
            points = [
                center + QRectF(-size, -size/2, 0, 0).topLeft(),
                center + QRectF(size/2, 0, 0, 0).topLeft(),
                center + QRectF(-size, size/2, 0, 0).topLeft(),
            ]
            # Simple triangle indicator
            painter.drawEllipse(inner_rect)
        else:
            # Arrow pointing outward (right)
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(inner_rect)

        # Draw label
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 9, QFont.Weight.Bold)
        painter.setFont(font)

        label = self._interface_port.name
        if self._interface_port.direction == InterfaceDirection.INPUT:
            # Label to the left of the port
            label_rect = QRectF(
                -self.PORT_RADIUS - 100 - self.LABEL_OFFSET,
                -10,
                100,
                20,
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        else:
            # Label to the right of the port
            label_rect = QRectF(
                self.PORT_RADIUS + self.LABEL_OFFSET,
                -10,
                100,
                20,
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse press for connection creation."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Interface output ports can start connections (they feed INTO the design)
            # Interface input ports receive connections (they receive FROM the design)
            if self.is_input() and self.on_connection_start:
                # Input interface ports act as outputs (they provide data to the design)
                self.on_connection_start()
                return
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event) -> None:
        """Handle hover enter."""
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        """Handle hover leave."""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(event)
