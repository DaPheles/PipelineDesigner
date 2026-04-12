"""Interface stage item for input/output port layers."""

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain import DEFAULT_GRID, GridConfig


class InterfaceStageItem(QGraphicsRectItem):
    """Graphics item representing an input or output interface stage.

    Interface stages are vertical bars at the left (input) or right (output)
    of the design that carry interface ports. They can be moved horizontally
    but extend vertically to match the component bounds.
    """

    STAGE_WIDTH = 40.0

    def __init__(
        self,
        is_input: bool,
        x_position: float,
        height: float,
        grid: GridConfig | None = None,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the interface stage item.

        Args:
            is_input: True for input stage, False for output stage.
            x_position: X position in pixels.
            height: Height in pixels.
            grid: Grid configuration.
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._is_input = is_input
        self._grid = grid or DEFAULT_GRID
        self._height = height
        self._port_items: list["InterfacePortItem"] = []

        # Callbacks
        self.on_position_changed: Callable[[float], None] | None = None

        # Track if user is dragging (vs programmatic position change)
        self._user_dragging = False

        self._setup_item()
        self.setPos(x_position - self.STAGE_WIDTH / 2, 0)

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)  # Behind components but above bounds

        self.setRect(0, 0, self.STAGE_WIDTH, self._height)
        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
        if self._is_input:
            color = QColor("#27ae60")  # Green for input
        else:
            color = QColor("#e67e22")  # Orange for output

        if self.isSelected():
            pen = QPen(QColor("#ffffff"))
            pen.setWidth(2)
        else:
            pen = QPen(color.darker(150))
            pen.setWidth(1)

        self.setPen(pen)
        self.setBrush(QBrush(color.lighter(150)))
        self.setOpacity(0.6)

    def is_input(self) -> bool:
        """Check if this is an input stage."""
        return self._is_input

    def get_x_position(self) -> float:
        """Get the x position."""
        return self.pos().x()

    def set_height(self, height: float, y_offset: float = 0) -> None:
        """Set the height and vertical position of the stage.

        The stage will exactly match the given height and y_offset,
        aligning with the component bounds rectangle.
        """
        self._height = height
        self.setRect(0, 0, self.STAGE_WIDTH, self._height)
        self.setY(y_offset)
        self._reposition_ports()

    def add_port(self, port_item: "InterfacePortItem", auto_position: bool = True) -> None:
        """Add a port item to this stage.

        Args:
            port_item: The interface port item to add.
            auto_position: If True, automatically reposition all ports evenly.
                          If False, preserve the port's current position.
        """
        port_item.setParentItem(self)
        self._port_items.append(port_item)
        if auto_position:
            self._reposition_ports()

    def get_ports(self) -> list["InterfacePortItem"]:
        """Get all port items."""
        return self._port_items.copy()

    def _reposition_ports(self) -> None:
        """Reposition port items evenly along the stage."""
        if not self._port_items:
            return

        count = len(self._port_items)
        spacing = self._height / (count + 1)

        for i, port_item in enumerate(self._port_items):
            y = spacing * (i + 1)
            if self._is_input:
                x = self.STAGE_WIDTH # Right edge for input
            else:
                x = 0  # Left edge for output
            port_item.setPos(x, y)

    def itemChange(self, change, value):
        """Handle item changes."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # Snap X to grid, but only constrain Y during user drag
            new_x = self._grid.snap_to_grid(value.x())
            if self._user_dragging:
                # Keep current Y when user is dragging (horizontal only)
                return QPointF(new_x, self.pos().y())
            else:
                # Allow programmatic Y changes
                return QPointF(new_x, value.y())

        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.on_position_changed and self._user_dragging:
                self.on_position_changed(self.pos().x())

        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_appearance()

        return super().itemChange(change, value)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the interface stage."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        pen = self.pen()
        brush = self.brush()

        # Draw background
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRect(rect)

        # Draw label
        painter.setPen(QPen(QColor("#333333")))
        font = QFont("Arial", 8, QFont.Weight.Bold)
        painter.setFont(font)

        label = "INPUTS" if self._is_input else "OUTPUTS"

        # Draw vertical text
        painter.save()
        painter.translate(rect.center().x(), rect.center().y())
        painter.rotate(-90)
        painter.drawText(
            QRectF(-rect.height() / 2, -10, rect.height(), 20),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )
        painter.restore()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse press - start user drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_dragging = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse release - end user drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_dragging = False
            # Trigger position changed callback after drag completes
            if self.on_position_changed:
                self.on_position_changed(self.pos().x())
        super().mouseReleaseEvent(event)


# Import here to avoid circular imports
from .interface_port_item import InterfacePortItem
