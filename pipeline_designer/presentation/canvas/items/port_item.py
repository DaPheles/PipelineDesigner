"""Port graphics item for component blocks."""

from typing import Callable
from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain.models import Port, PortDirection


class PortItem(QGraphicsEllipseItem):
    """Graphics item representing a port on a component.

    Supports connection creation by dragging from output ports to input ports.
    """

    PORT_RADIUS = 6.0

    COLOR_INPUT = QColor("#70ad47")  # Green
    COLOR_OUTPUT = QColor("#ed7d31")  # Orange
    COLOR_CLOCK = QColor("#5b9bd5")  # Blue
    COLOR_RESET = QColor("#c45911")  # Dark orange
    COLOR_INOUT = QColor("#7030a0")  # Purple
    COLOR_HOVER = QColor("#ffcc00")  # Yellow highlight
    COLOR_CONNECT_VALID = QColor("#00ff00")  # Bright green for valid connection
    COLOR_CONNECT_INVALID = QColor("#ff0000")  # Red for invalid connection

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
        self._is_connection_target = False
        self._is_valid_target = False
        self._edge = "none"  # set by add_label; used to orient the clock triangle

        # Callback for connection start (from output ports)
        self.on_connection_start: Callable[[], None] | None = None

        # Callback for port selection
        self.on_port_selected: Callable[["PortItem"], None] | None = None

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(10)

        self._update_appearance()

        st = self._port.signal_type
        type_str = st.kind if st.width == "1" else f"{st.kind}[{st.width}:{st.lsb}]"
        tooltip = f"{self._port.name}\nType: {type_str}\nDirection: {self._port.direction.value}"
        self.setToolTip(tooltip)

    def _get_port_color(self) -> QColor:
        """Get the color for this port based on its type."""
        if self._is_connection_target:
            return self.COLOR_CONNECT_VALID if self._is_valid_target else self.COLOR_CONNECT_INVALID
        if self._is_hovered:
            return self.COLOR_HOVER
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

        self.setBrush(QBrush(color))
        pen = QPen(color.darker(150))
        pen.setWidth(2)
        self.setPen(pen)

    def get_port(self) -> Port:
        """Get the port model."""
        return self._port

    def update_tooltip(self) -> None:
        """Update the tooltip after port properties change."""
        st = self._port.signal_type
        type_str = st.kind if st.width == "1" else f"{st.kind}[{st.width}:{st.lsb}]"
        tooltip = f"{self._port.name}\nType: {type_str}\nDirection: {self._port.direction.value}"
        self.setToolTip(tooltip)

    def is_output(self) -> bool:
        """Check if this is an output port."""
        return self._port.direction == PortDirection.OUT

    def is_input(self) -> bool:
        """Check if this is an input port."""
        return self._port.direction == PortDirection.IN

    def get_component_id(self) -> UUID | None:
        """Get the ID of the parent component instance."""
        parent = self.parentItem()
        if parent and hasattr(parent, "get_instance"):
            return parent.get_instance().id
        return None

    def set_connection_target(self, is_target: bool, is_valid: bool = False) -> None:
        """Set whether this port is being targeted for a connection.

        Args:
            is_target: Whether a connection is being dragged over this port.
            is_valid: Whether this would be a valid connection target.
        """
        self._is_connection_target = is_target
        self._is_valid_target = is_valid
        self._update_appearance()

    def hoverEnterEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle hover enter."""
        self._is_hovered = True
        self._update_appearance()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle hover leave."""
        self._is_hovered = False
        self._update_appearance()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse press - select port or start connection from output ports."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Clear other selections and select this port
            scene = self.scene()
            if scene:
                scene.clearSelection()
            self.setSelected(True)

            # Notify selection callback
            if self.on_port_selected:
                self.on_port_selected(self)

            # For output ports, also start connection
            if self.is_output() and self.on_connection_start:
                self.on_connection_start()

            # Accept the event to prevent propagation to parent
            event.accept()
            return

        super().mousePressEvent(event)

    def add_label(self, comp_width_px: float, comp_height_px: float) -> None:
        """Add the port name as a text label child, positioned inside the component.

        Must be called *after* setPos() so self.pos() reflects the final position.
        The label is non-interactive and stays behind the port circle.
        """
        label = QGraphicsSimpleTextItem(self._port.name, self)
        label.setFont(QFont("sans-serif", 6))
        label.setBrush(QColor("#ffffff"))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        label.setZValue(-1)

        r  = self.PORT_RADIUS
        br = label.boundingRect()
        lw, lh = br.width(), br.height()

        x, y = self.pos().x(), self.pos().y()
        tol  = r + 4  # tolerance to classify a port as on an edge

        if x < tol:                       # left edge → label to the right
            self._edge = "left"
            label.setPos(r + 3, -lh / 2)
        elif x > comp_width_px - tol:     # right edge → label to the left
            self._edge = "right"
            label.setPos(-lw - r - 3, -lh / 2)
        elif y < tol:                     # top edge → label below
            self._edge = "top"
            label.setPos(-lw / 2, r + 2)
        else:                             # bottom edge → label above
            self._edge = "bottom"
            label.setPos(-lw / 2, -lh - r - 2)

    def _clock_triangle(self) -> QPolygonF:
        """Return a triangle polygon pointing into the component for a clock port.

        Triangle shape from user spec (-0.5,0), (0,+0.5), (+0.5,0) (y-up math coords),
        rotated to point inward based on which edge the port sits on.
        """
        r = self.PORT_RADIUS
        if self._edge == "bottom":   # apex points up (into component)
            pts = [QPointF(-r, 0.0), QPointF(0.0, -r), QPointF(r, 0.0)]
        elif self._edge == "top":    # apex points down
            pts = [QPointF(-r, 0.0), QPointF(0.0,  r), QPointF(r, 0.0)]
        elif self._edge == "right":  # apex points left
            pts = [QPointF(0.0, -r), QPointF(-r, 0.0), QPointF(0.0,  r)]
        else:                        # left (default) — apex points right
            pts = [QPointF(0.0, -r), QPointF( r, 0.0), QPointF(0.0,  r)]
        return QPolygonF(pts)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the port item."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._port.is_clock:
            color = self._get_port_color()
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color.darker(150), 2))
            painter.drawPolygon(self._clock_triangle())
        else:
            super().paint(painter, option, widget)
