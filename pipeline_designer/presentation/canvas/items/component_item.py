"""Component graphics item for the design canvas."""

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
from pipeline_designer.domain.models import ComponentDefinition, ComponentInstance, PortDirection

from .port_item import PortItem


class ComponentItem(QGraphicsRectItem):
    """Graphics item representing a component instance.

    Component sizes and port positions are defined in grid units.
    This item converts grid units to pixels for rendering.
    Snap-to-grid is enabled by default when moving components.

    For registers, stage-aware snapping is applied to the x coordinate,
    and movements trigger stage reassignment via the register_moved callback.
    """

    CORNER_RADIUS = 8.0
    SELECTION_PADDING = 4.0
    HEADER_HEIGHT = 24.0

    def __init__(
        self,
        instance: ComponentInstance,
        definition: ComponentDefinition | None = None,
        grid: GridConfig | None = None,
        snap_to_grid: bool = True,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the component item.

        Args:
            instance: The component instance model.
            definition: The component definition (optional).
            grid: Grid configuration for unit conversion.
            snap_to_grid: Whether to snap to grid when moving (default True).
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._instance = instance
        self._definition = definition
        self._grid = grid or DEFAULT_GRID
        self._snap_to_grid = snap_to_grid
        self._port_items: dict[str, PortItem] = {}
        self._is_register = instance.definition_ref == "Register"
        self._last_x: float = instance.position[0]

        # Callback for register movement: (instance, old_x) -> None
        self.register_moved: Callable[[ComponentInstance, float], None] | None = None

        # Callback for stage-aware x snapping: (x) -> x
        self.snap_register_x: Callable[[float], float] | None = None

        # Callbacks for undo tracking
        self.on_move_start: Callable[[], None] | None = None
        self.on_move_end: Callable[[], None] | None = None

        self._setup_item()
        self._create_ports()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)

        width_px = self._grid.to_pixels(6)
        height_px = self._grid.to_pixels(4)
        color = "#4a90d9"

        if self._definition:
            width_px, height_px = self._definition.visual.get_pixel_size(self._grid)
            color = self._definition.visual.color

        self.setRect(0, 0, width_px, height_px)
        self._base_color = QColor(color)
        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
        if self.isSelected():
            pen = QPen(QColor("#ffffff"))
            pen.setWidth(3)
        else:
            pen = QPen(self._base_color.darker(150))
            pen.setWidth(2)

        self.setPen(pen)
        self.setBrush(QBrush(self._base_color))

    def _create_ports(self) -> None:
        """Create port items for all ports.

        Port positions are specified in grid units and converted to pixels.
        Ports without explicit positions are auto-placed on left/right edges.
        """
        if self._definition is None:
            return

        rect = self.rect()
        width_units = self._definition.visual.width
        height_units = self._definition.visual.height

        input_ports = [p for p in self._definition.ports if p.direction == PortDirection.IN]
        output_ports = [p for p in self._definition.ports if p.direction == PortDirection.OUT]

        for port in self._definition.ports:
            port_item = PortItem(port, self)

            if port.position is not None:
                x_px, y_px = port.get_pixel_position(self._grid)
            else:
                if port.direction == PortDirection.IN:
                    idx = input_ports.index(port)
                    x_px = 0
                    y_px = self._calculate_auto_port_y(idx, len(input_ports), height_units)
                else:
                    idx = output_ports.index(port)
                    x_px = rect.width()
                    y_px = self._calculate_auto_port_y(idx, len(output_ports), height_units)

            port_item.setPos(x_px, y_px)
            self._port_items[port.name] = port_item

    def _calculate_auto_port_y(self, index: int, total: int, height_units: int) -> float:
        """Calculate Y position for auto-placed ports.

        Auto-placed ports are distributed evenly and snapped to grid.

        Args:
            index: Port index (0-based).
            total: Total number of ports on this side.
            height_units: Component height in grid units.

        Returns:
            Y position in pixels.
        """
        if total == 1:
            y_units = height_units // 2
        else:
            available_units = height_units - 2
            if total <= available_units:
                spacing = available_units // (total + 1)
                y_units = 1 + spacing * (index + 1)
            else:
                y_units = 1 + index
        return self._grid.to_pixels(y_units)

    def get_instance(self) -> ComponentInstance:
        """Get the component instance model."""
        return self._instance

    def get_definition(self) -> ComponentDefinition | None:
        """Get the component definition."""
        return self._definition

    def is_register(self) -> bool:
        """Check if this component is a register."""
        return self._is_register

    def get_port_item(self, port_name: str) -> PortItem | None:
        """Get a port item by name."""
        return self._port_items.get(port_name)

    def get_port_scene_pos(self, port_name: str) -> tuple[float, float] | None:
        """Get a port's position in scene coordinates."""
        port_item = self._port_items.get(port_name)
        if port_item is None:
            return None
        scene_pos = port_item.scenePos()
        return (scene_pos.x(), scene_pos.y())

    def set_snap_to_grid(self, enabled: bool) -> None:
        """Enable or disable snap-to-grid when moving."""
        self._snap_to_grid = enabled

    def itemChange(self, change, value):
        """Handle item changes."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self._snap_to_grid:
                new_x = value.x()
                new_y = self._grid.snap_to_grid(value.y())

                # For registers, use stage-aware x snapping
                if self._is_register and self.snap_register_x:
                    new_x = self.snap_register_x(new_x)
                else:
                    new_x = self._grid.snap_to_grid(new_x)

                return QPointF(new_x, new_y)

        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = value
            new_x = pos.x()
            old_x = self._last_x

            # Update instance position
            self._instance.position = (new_x, pos.y())

            # For registers, notify if x position changed (stage change)
            if self._is_register and new_x != old_x:
                self._last_x = new_x
                if self.register_moved:
                    self.register_moved(self._instance, old_x)

        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_appearance()

        return super().itemChange(change, value)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the component item."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        pen = self.pen()
        brush = self.brush()

        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        header_rect = QRectF(rect.x(), rect.y(), rect.width(), self.HEADER_HEIGHT)
        painter.setBrush(QBrush(self._base_color.darker(120)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            header_rect.x(),
            header_rect.y(),
            header_rect.width(),
            header_rect.height() + self.CORNER_RADIUS,
            self.CORNER_RADIUS,
            self.CORNER_RADIUS,
        )
        painter.drawRect(
            header_rect.x(),
            header_rect.y() + self.CORNER_RADIUS,
            header_rect.width(),
            header_rect.height() - self.CORNER_RADIUS,
        )

        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)

        name = self._instance.definition_ref
        if self._definition:
            name = self._definition.name

        # For registers, show stage number
        if self._is_register and self._instance.pipeline_stage is not None:
            name = f"{name} [S{self._instance.pipeline_stage}]"

        painter.drawText(header_rect, Qt.AlignmentFlag.AlignCenter, name)

    def boundingRect(self) -> QRectF:
        """Return the bounding rectangle including selection padding."""
        rect = self.rect()
        return rect.adjusted(
            -self.SELECTION_PADDING,
            -self.SELECTION_PADDING,
            self.SELECTION_PADDING,
            self.SELECTION_PADDING,
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse press - record start position for undo."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.on_move_start:
                self.on_move_start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse release - record end position for undo."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.on_move_end:
                self.on_move_end()
        super().mouseReleaseEvent(event)
