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

    CORNER_RADIUS = 2.0
    SELECTION_PADDING = 4.0
    HEADER_HEIGHT = 20.0  # used for non-composite components

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
        self._is_invalid: bool = False         # red dashed border when True
        self._title_override: str | None = None  # shown instead of component name when set
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
        elif self._is_invalid:
            pen = QPen(QColor("#ff4444"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
        elif self._is_temporary:
            pen = QPen(QColor("#ff8800"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
        else:
            pen = QPen(self._base_color.darker(150))
            pen.setWidth(2)

        self.setPen(pen)
        self.setBrush(QBrush(self._base_color))

    def _composite_header_h(self) -> float:
        """Header height for composite components: 1 grid unit."""
        return float(self._grid.size)

    def _composite_port_zone_w(self) -> float:
        """Port zone width for composite components: 1 grid unit."""
        return float(self._grid.size)

    def _compute_composite_y_offset(self) -> float:
        """Compute the y_offset used to vertically center internal content.

        Must mirror the formula in CompositeViewItem._create_internal_visualization
        so that port positions computed here land on the same pixels as the
        internal connection endpoints drawn by the view.

        The y_offset transforms design-coordinate y → view-local y:
            view_y = grid.to_pixels(design_y) + y_offset
        """
        if not self._composite_design or not self._composite_design.components:
            return 0.0

        rect = self.rect()
        header_h = self._composite_header_h()
        available_h = rect.height() - header_h

        min_y = float("inf")
        max_y = float("-inf")
        for comp in self._composite_design.components:
            y = self._grid.to_pixels(comp.position[1])
            definition = self._library.get(comp.definition_ref)
            if definition:
                _, h = definition.visual.get_pixel_size(self._grid)
            else:
                h = self._grid.to_pixels(3)
            min_y = min(min_y, y)
            max_y = max(max_y, y + h)

        content_h = max_y - min_y
        return (available_h - content_h) / 2 - min_y

    def _create_ports(self) -> None:
        """Create port items for all ports.

        Port positions are specified in grid units and converted to pixels.
        Ports without explicit positions are auto-placed on left/right edges.

        For composite components the external port circles are always placed:
          - x: at the center of the port zone (grid.size / 2 from the edge)
          - y: header_h + to_pixels(design_y) + y_offset, matching the endpoint
            that CompositeViewItem draws for the same interface port connection.

        NOTE: the library loader always sets explicit port.position values on
        composite ports, so the composite branch must be checked FIRST, before
        the generic port.position branch, to avoid bypassing the centering logic.
        """
        if self._definition is None:
            return

        rect = self.rect()
        height_units = self._definition.visual.height

        input_ports = [p for p in self._definition.ports if p.direction == PortDirection.IN]
        output_ports = [p for p in self._definition.ports if p.direction == PortDirection.OUT]

        # Pre-compute y_offset once for all composite ports
        composite_y_offset: float | None = None
        if self._is_composite and self._composite_design:
            composite_y_offset = self._compute_composite_y_offset()

        for port in self._definition.ports:
            port_item = PortItem(port, self)

            if self._is_composite:
                # Composite: always derive position from port zone center + y_offset.
                # Must come before the port.position check because the library loader
                # always sets explicit positions (x=0/width, y=iface_y) which are
                # in the design coordinate system, not the component view system.
                header_h = self._composite_header_h()
                sw = float(self._grid.size)
                x_px = sw / 2 if port.direction == PortDirection.IN else rect.width() - sw / 2

                # Resolve y from the matching interface port in the composite design
                iface = next(
                    (
                        ip
                        for ip in self._composite_design.interface_ports
                        if ip.name == port.name
                    ),
                    None,
                ) if self._composite_design else None

                if iface and iface.position is not None and composite_y_offset is not None:
                    y_px = header_h + self._grid.to_pixels(iface.position[1]) + composite_y_offset
                elif iface and iface.position is not None:
                    # No y_offset (no internal components to center around)
                    y_px = header_h + self._grid.to_pixels(iface.position[1])
                else:
                    # Fallback: distribute evenly in body area
                    if port.direction == PortDirection.IN:
                        idx = input_ports.index(port)
                        y_px = self._calculate_composite_port_y(
                            idx, len(input_ports), rect.height(), header_h
                        )
                    else:
                        idx = output_ports.index(port)
                        y_px = self._calculate_composite_port_y(
                            idx, len(output_ports), rect.height(), header_h
                        )

            elif port.position is not None:
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
            port_item.add_label(rect.width(), rect.height())
            self._port_items[port.name] = port_item

    def _calculate_composite_port_y(
        self, index: int, total: int, total_height_px: float, header_h: float
    ) -> float:
        """Calculate Y position for composite ports when no interface position is available.

        Distributes ports evenly in the body area below the header.
        """
        body_h = total_height_px - header_h
        if total == 1:
            return header_h + body_h / 2
        spacing = body_h / (total + 1)
        return header_h + spacing * (index + 1)

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

        rect = self.rect()
        header_h = self._composite_header_h()

        # The view spans the full component width so that internal interface-port
        # connection endpoints (at x=0 and x=bounds.width()) land exactly on top
        # of the external PortItem circles.  Port zones are purely visual decorations
        # drawn by paint() and do not shift the view origin.
        self._composite_view = CompositeViewItem(
            design=self._composite_design,
            library=self._library,
            bounds=QRectF(0, 0, rect.width(), rect.height() - header_h),
            grid=self._grid,
            parent=self,
        )
        self._composite_view.setPos(0, header_h)

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

    def set_invalid(self, is_invalid: bool) -> None:
        """Toggle the red dashed 'invalid position' border.

        Args:
            is_invalid: True to show the invalid indicator, False to clear it.
        """
        if self._is_invalid == is_invalid:
            return
        self._is_invalid = is_invalid
        if not is_invalid:
            self._title_override = None
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

        if self._is_composite:
            header_h = self._composite_header_h()
            zone_w = self._composite_port_zone_w()

            # Input port zone (left): green, matching InterfaceStageItem input color
            input_zone_color = QColor("#27ae60").lighter(150)
            input_zone_color.setAlpha(180)
            painter.setBrush(QBrush(input_zone_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                rect.x(), rect.y() + header_h,
                zone_w, rect.height() - header_h,
                0, 0,
            )
            # Output port zone (right): orange, matching InterfaceStageItem output color
            output_zone_color = QColor("#e67e22").lighter(150)
            output_zone_color.setAlpha(180)
            painter.setBrush(QBrush(output_zone_color))
            painter.drawRoundedRect(
                rect.x() + rect.width() - zone_w, rect.y() + header_h,
                zone_w, rect.height() - header_h,
                0, 0,
            )

            # Draw stage divider lines in the content area (between port zones)
            if self._instance.stage_count > 1:
                content_x = rect.x() + zone_w
                content_w = rect.width() - 2 * zone_w
                stage_width = content_w / self._instance.stage_count
                painter.setPen(QPen(self._base_color.darker(150), 1, Qt.PenStyle.DashLine))
                for i in range(1, self._instance.stage_count):
                    x = content_x + stage_width * i
                    painter.drawLine(
                        int(x), int(rect.y() + header_h),
                        int(x), int(rect.y() + rect.height()),
                    )

            # Header (1 grid unit tall, full width)
            header_rect = QRectF(rect.x(), rect.y(), rect.width(), header_h)
            painter.setBrush(QBrush(self._base_color.darker(120)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                header_rect.x(), header_rect.y(),
                header_rect.width(), header_rect.height() + self.CORNER_RADIUS,
                self.CORNER_RADIUS, self.CORNER_RADIUS,
            )
            painter.drawRect(
                header_rect.x(), header_rect.y() + self.CORNER_RADIUS,
                header_rect.width(), header_rect.height() - self.CORNER_RADIUS,
            )

            # Composite indicator icon (small layered squares)
            icon_size = 8
            icon_x = rect.x() + 3
            icon_y = rect.y() + (header_h - icon_size) / 2
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(int(icon_x), int(icon_y), icon_size - 2, icon_size - 2)
            painter.drawRect(int(icon_x + 2), int(icon_y + 2), icon_size - 2, icon_size - 2)

            # Label text
            if self._title_override:
                name = self._title_override
            else:
                name = self._definition.name if self._definition else self._instance.definition_ref
                if self._instance.stage_count > 1:
                    name = f"{name} (L={self._instance.stage_count})"
            painter.setPen(QPen(QColor("#ffffff")))
            font = QFont("Arial", 8, QFont.Weight.Bold)
            painter.setFont(font)
            text_rect = QRectF(
                header_rect.x() + icon_size + 4, header_rect.y(),
                header_rect.width() - icon_size - 6, header_rect.height(),
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, name)

        else:
            # Non-composite: standard 24 px header
            header_rect = QRectF(rect.x(), rect.y(), rect.width(), self.HEADER_HEIGHT)
            painter.setBrush(QBrush(self._base_color.darker(120)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                header_rect.x(), header_rect.y(),
                header_rect.width(), header_rect.height() + self.CORNER_RADIUS,
                self.CORNER_RADIUS, self.CORNER_RADIUS,
            )
            painter.drawRect(
                header_rect.x(), header_rect.y() + self.CORNER_RADIUS,
                header_rect.width(), header_rect.height() - self.CORNER_RADIUS,
            )

            if self._title_override:
                name = self._title_override
            else:
                name = self._instance.definition_ref
                if self._definition:
                    name = self._definition.name
                if self._is_register and self._instance.pipeline_stage is not None:
                    name = f"{name} [S{self._instance.pipeline_stage}]"

            painter.setPen(QPen(QColor("#ffffff")))
            font = QFont("Arial", 10, QFont.Weight.Bold)
            painter.setFont(font)
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
            if self._is_invalid:
                self._title_override = "Invalid location – correct it for consistency!"
                self.update()
            if self.on_move_end:
                self.on_move_end()
            # Clear distance conflict highlighting for registers
            if self._is_register and self.clear_distance_conflicts:
                self.clear_distance_conflicts()
        super().mouseReleaseEvent(event)
