"""Port graphics item for component blocks."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain.models import Port, PortDirection


class PortItem(QGraphicsEllipseItem):
    """Graphics item representing a port on a component."""

    PORT_RADIUS = 6.0

    COLOR_INPUT = QColor("#70ad47")  # Green
    COLOR_OUTPUT = QColor("#ed7d31")  # Orange
    COLOR_CLOCK = QColor("#5b9bd5")  # Blue
    COLOR_RESET = QColor("#c45911")  # Dark orange
    COLOR_INOUT = QColor("#7030a0")  # Purple
    COLOR_HOVER = QColor("#ffcc00")  # Yellow highlight

    def __init__(self, port: Port, parent: QGraphicsItem | None = None):
        """Initialize the port item.

        Args:
            port: The port model.
            parent: Parent graphics item.
        """
        rect = QRectF(
            -self.PORT_RADIUS,
            -self.PORT_RADIUS,
            self.PORT_RADIUS * 2,
            self.PORT_RADIUS * 2,
        )
        super().__init__(rect, parent)

        self._port = port
        self._is_hovered = False

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setZValue(10)

        self._update_appearance()

        tooltip = f"{self._port.name}\nType: {self._port.data_type}\nDirection: {self._port.direction.value}"
        self.setToolTip(tooltip)

    def _get_port_color(self) -> QColor:
        """Get the color for this port based on its type."""
        if self._port.is_clock:
            return self.COLOR_CLOCK
        if self._port.is_reset:
            return self.COLOR_RESET
        if self._port.direction == PortDirection.IN:
            return self.COLOR_INPUT
        if self._port.direction == PortDirection.OUT:
            return self.COLOR_OUTPUT
        return self.COLOR_INOUT

    def _update_appearance(self) -> None:
        """Update the visual appearance of the port."""
        color = self._get_port_color()
        if self._is_hovered:
            color = self.COLOR_HOVER

        self.setBrush(QBrush(color))
        pen = QPen(color.darker(150))
        pen.setWidth(2)
        self.setPen(pen)

    def get_port(self) -> Port:
        """Get the port model."""
        return self._port

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
        """Paint the port item."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint(painter, option, widget)
