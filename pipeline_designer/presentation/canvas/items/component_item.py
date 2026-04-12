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
from pipeline_designer.domain.models import ComponentDefinition, ComponentInstance, Design, PortDirection

from .composite_view_item import CompositeViewItem
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
        composite_design: Design | None = None,
        library: dict[str, ComponentDefinition] | None = None,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the component item.

        Args:
            instance: The component instance model.
            definition: The component definition (optional).
            grid: Grid configuration for unit conversion.
            snap_to_grid: Whether to snap to grid when moving (default True).
            composite_design: For composite components, the internal design.
            library: Component library for looking up definitions.
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._instance = instance
        self._definition = definition
        self._grid = grid or DEFAULT_GRID
        self._snap_to_grid = snap_to_grid
        self._port_items: dict[str, PortItem] = {}
        self._is_register = instance.definition_ref == "Register"
        self._is_composite = instance.is_composite
        # Store last_x in grid units (same as instance.position)
        self._last_x: float = instance.position[0]
        self._composite_design = composite_design
        self._library = library or {}
        self._composite_view: CompositeViewItem | None = None

        # Callback for register movement: (instance, old_x) -> None
        self.register_moved: Callable[[ComponentInstance, float], None] | None = None

        # Callback for stage-aware x snapping: (x) -> x
        self.snap_register_x: Callable[[float], float] | None = None

        # Callback to avoid stage overlap: (x, width) -> adjusted_x
        self.avoid_stage_overlap: Callable[[float, float], float] | None = None

        # Callback to check distance conflicts during register movement: (x, stage_index) -> None
        self.check_distance_conflicts: Callable[[float, int | None], None] | None = None

        # Callback to clear distance conflict highlighting: () -> None
        self.clear_distance_conflicts: Callable[[], None] | None = None

        # Callback fired during drag of a composite component: (instance, pos) -> None
        self.on_composite_drag_update: Callable[[ComponentInstance, "QPointF"], None] | None = None  # noqa: F821

        # Callback that snaps a composite's x position to align its internal
        # stages with main-design stages: (x_px) -> snapped_x_px
        self.snap_composite_x: Callable[[float], float] | None = None

        # Callbacks for undo tracking
        self.on_move_start: Callable[[], None] | None = None
        self.on_move_end: Callable[[], None] | None = None

        # Internal state flags
        self._is_temporary: bool = False       # orange dashed border when True
        self._suppress_callbacks: bool = False  # set True during programmatic moves

        self._setup_item()
        self._create_ports()
        self._create_composite_view()

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
        elif self._is_temporary:
            pen = QPen(QColor("#ff8800"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
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

    def _create_composite_view(self) -> None:
        """Create the composite view for visualizing internal structure."""
        if not self._is_composite or not self._composite_design:
            return

        # Create the composite view as a child item
        rect = self.rect()
        self._composite_view = CompositeViewItem(
            design=self._composite_design,
            library=self._library,
            bounds=rect,
            grid=self._grid,
            parent=self,
        )

    def get_instance(self) -> ComponentInstance:
        """Get the component instance model."""
        return self._instance

    def get_definition(self) -> ComponentDefinition | None:
        """Get the component definition."""
        return self._definition

    def is_register(self) -> bool:
        """Check if this component is a register."""
        return self._is_register

    def is_composite(self) -> bool:
        """Check if this component is a composite."""
        return self._is_composite

    def get_port_item(self, port_name: str) -> PortItem | None:
        """Get a port item by name."""
        return self._port_items.get(port_name)

    def set_temporary(self, is_temp: bool) -> None:
        """Toggle the orange dashed 'temporary position' border.

        Args:
            is_temp: True to show the temporary indicator, False to clear it.
        """
        if self._is_temporary == is_temp:
            return
        self._is_temporary = is_temp
        self._update_appearance()
        self.update()

    def set_position_no_callbacks(self, x: float, y: float) -> None:
        """Set position programmatically without triggering stage/undo callbacks.

        Used during stage group-moves when the scene positions registers directly.

        Args:
            x: X position in pixels.
            y: Y position in pixels.
        """
        self._suppress_callbacks = True
        self.setPos(x, y)
        self._suppress_callbacks = False

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

                if self._is_register and self.snap_register_x:
                    # Registers snap to the stage they belong to
                    new_x = self.snap_register_x(new_x)
                elif self._is_composite and self.snap_composite_x:
                    # Composites snap so their first internal stage aligns with a
                    # main-design stage when close enough; otherwise plain grid snap
                    new_x = self.snap_composite_x(new_x)
                else:
                    # Plain grid snap for all other non-register components.
                    # Position is NOT hard-constrained by stage bands – invalid
                    # placements are communicated visually, not blocked.
                    new_x = self._grid.snap_to_grid(new_x)

                return QPointF(new_x, new_y)

        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # When _suppress_callbacks is set the scene is moving this item
            # programmatically (e.g. stage group-move) – skip all side-effects.
            if self._suppress_callbacks:
                return super().itemChange(change, value)

            pos = value
            # pos is in pixels (scene coordinates)
            new_x_px = pos.x()
            new_y_px = pos.y()

            # Convert to grid units for storage
            new_x_grid = self._grid.to_grid_units(new_x_px)
            new_y_grid = self._grid.to_grid_units(new_y_px)
            old_x_grid = self._last_x

            # Update instance position in grid units
            self._instance.position = (new_x_grid, new_y_grid)

            # For registers, check distance conflicts and notify if x position changed
            if self._is_register:
                # Check for distance conflicts with other components (uses pixels)
                if self.check_distance_conflicts:
                    self.check_distance_conflicts(new_x_px, self._instance.pipeline_stage)

                if new_x_grid != old_x_grid:
                    self._last_x = new_x_grid
                    if self.register_moved:
                        self.register_moved(self._instance, old_x_grid)

            # For composites, notify the scene so it can update alignment previews
            elif self._is_composite and self.on_composite_drag_update:
                self.on_composite_drag_update(self._instance, pos)

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

        # For composite components, draw stage divider lines
        if self._is_composite and self._instance.stage_count > 1:
            stage_width = rect.width() / self._instance.stage_count
            painter.setPen(QPen(self._base_color.darker(150), 1, Qt.PenStyle.DashLine))
            for i in range(1, self._instance.stage_count):
                x = rect.x() + stage_width * i
                painter.drawLine(
                    int(x), int(rect.y() + self.HEADER_HEIGHT),
                    int(x), int(rect.y() + rect.height())
                )

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

        # Draw composite indicator icon (small layered squares)
        if self._is_composite:
            icon_size = 10
            icon_x = rect.x() + 4
            icon_y = rect.y() + (self.HEADER_HEIGHT - icon_size) / 2
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(int(icon_x), int(icon_y), icon_size - 2, icon_size - 2)
            painter.drawRect(int(icon_x + 2), int(icon_y + 2), icon_size - 2, icon_size - 2)

        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)

        name = self._instance.definition_ref
        if self._definition:
            name = self._definition.name

        # For registers, show stage number
        if self._is_register and self._instance.pipeline_stage is not None:
            name = f"{name} [S{self._instance.pipeline_stage}]"
        # For composite components, show latency
        elif self._is_composite and self._instance.stage_count > 1:
            name = f"{name} (L={self._instance.stage_count})"

        # Adjust text position for composite (account for icon)
        if self._is_composite:
            text_rect = QRectF(header_rect.x() + 16, header_rect.y(),
                              header_rect.width() - 16, header_rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, name)
        else:
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
            # Clear distance conflict highlighting for registers
            if self._is_register and self.clear_distance_conflicts:
                self.clear_distance_conflicts()
        super().mouseReleaseEvent(event)
