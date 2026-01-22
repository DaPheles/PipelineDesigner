"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsSceneMouseEvent

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Connection,
    Design,
    InterfaceDirection,
    InterfacePort,
    PortReference,
    Stage,
)
from pipeline_designer.infrastructure.persistence import LibraryLoader

from .commands import (
    AddComponentCommand,
    AddConnectionCommand,
    MoveComponentCommand,
    RemoveComponentCommand,
    RemoveConnectionCommand,
    UndoStack,
)
from .items import (
    ComponentBoundsItem,
    ComponentItem,
    ConnectionItem,
    InterfacePortItem,
    InterfaceStageItem,
    StageItem,
    TempConnectionItem,
)
from .items.port_item import PortItem


class DesignScene(QGraphicsScene):
    """Graphics scene for the design canvas.

    Uses GridConfig to ensure all positions align to grid intersections.
    Manages pipeline stages that are defined by register placements.
    Handles connection creation by dragging from output to input ports.
    """

    component_added = Signal(object)  # ComponentInstance
    component_removed = Signal(object)  # UUID
    component_selected = Signal(object)  # ComponentInstance or None
    stages_changed = Signal()  # Emitted when stage configuration changes
    connection_added = Signal(object)  # Connection
    connection_removed = Signal(object)  # UUID

    def __init__(self, grid: GridConfig | None = None, parent=None):
        """Initialize the design scene.

        Args:
            grid: Grid configuration. Uses DEFAULT_GRID if not provided.
            parent: Parent QObject.
        """
        super().__init__(parent)

        self._grid = grid or DEFAULT_GRID
        self._design = Design()
        self._library: dict[str, ComponentDefinition] = {}
        self._library_loader: LibraryLoader | None = None
        self._component_items: dict[UUID, ComponentItem] = {}
        self._stage_items: dict[UUID, StageItem] = {}
        self._connection_items: dict[UUID, ConnectionItem] = {}
        self._snap_to_grid = True
        self._register_width: float = 80.0  # Default, updated from library

        # Connection creation state
        self._temp_connection: TempConnectionItem | None = None
        self._connection_source_port: PortItem | None = None
        self._connection_source_component_id: UUID | None = None
        # Interface port connection state
        self._connection_source_interface_port: InterfacePortItem | None = None

        # Undo/Redo stack
        self._undo_stack = UndoStack()

        # Track component movement for undo
        self._move_start_positions: dict[UUID, tuple[float, float]] = {}

        # Interface stages and bounds
        self._input_stage: InterfaceStageItem | None = None
        self._output_stage: InterfaceStageItem | None = None
        self._component_bounds: ComponentBoundsItem | None = None
        self._interface_enabled = True  # Enable interface stages by default
        self._interface_port_items: dict[UUID, InterfacePortItem] = {}

        self._setup_scene()

    def _setup_scene(self) -> None:
        """Configure scene settings."""
        self.setSceneRect(QRectF(-5000, -5000, 10000, 10000))
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))

        # Create interface stages and bounds
        if self._interface_enabled:
            self._create_interface_items()

    @property
    def grid(self) -> GridConfig:
        """Get the grid configuration."""
        return self._grid

    def _create_interface_items(self) -> None:
        """Create the interface stages and component bounds."""
        default_height = 400.0
        default_input_x = -200.0
        default_output_x = 400.0

        # Create component bounds (background)
        self._component_bounds = ComponentBoundsItem(self._grid)
        self.addItem(self._component_bounds)

        # Create input stage (left)
        self._input_stage = InterfaceStageItem(
            is_input=True,
            x_position=default_input_x,
            height=default_height,
            grid=self._grid,
        )
        self._input_stage.on_position_changed = self._on_interface_stage_moved
        self.addItem(self._input_stage)

        # Create output stage (right)
        self._output_stage = InterfaceStageItem(
            is_input=False,
            x_position=default_output_x,
            height=default_height,
            grid=self._grid,
        )
        self._output_stage.on_position_changed = self._on_interface_stage_moved
        self.addItem(self._output_stage)

        # Create interface ports from design
        self._update_interface_ports()

        # Initial bounds update
        self._update_component_bounds()

    def _on_interface_stage_moved(self, x: float) -> None:
        """Handle interface stage movement."""
        self._update_component_bounds()

    def _update_interface_ports(self) -> None:
        """Update interface ports from the design."""
        if not self._input_stage or not self._output_stage:
            return

        # Clear existing port items from stages (but keep the tracking dict)
        for port in self._input_stage.get_ports():
            self.removeItem(port)
        for port in self._output_stage.get_ports():
            self.removeItem(port)

        # Clear the tracking dictionary
        self._interface_port_items.clear()

        # Create ports from design interface_ports
        for iface_port in self._design.get_input_interfaces():
            port_item = self._create_interface_port_item(iface_port, is_input=True)
            self._input_stage.add_port(port_item)

        for iface_port in self._design.get_output_interfaces():
            port_item = self._create_interface_port_item(iface_port, is_input=False)
            self._output_stage.add_port(port_item)

    def _create_interface_port_item(
        self, iface_port: InterfacePort, is_input: bool
    ) -> InterfacePortItem:
        """Create an interface port item and wire up callbacks."""
        port_item = InterfacePortItem(iface_port, grid=self._grid)
        self._interface_port_items[iface_port.id] = port_item

        # Wire up position change callback
        port_item.on_position_changed = lambda y: self._on_interface_port_moved(
            iface_port.id
        )

        # Wire up connection callback
        # Input interface ports act as sources (they provide data to the design)
        # Output interface ports act as targets (they receive data from the design)
        if is_input:
            port_item.on_connection_start = lambda: self._start_interface_connection(
                port_item
            )

        return port_item

    def _on_interface_port_moved(self, port_id: UUID) -> None:
        """Handle interface port position change."""
        port_item = self._interface_port_items.get(port_id)
        if port_item:
            port_item.update_model_position()

    def add_interface_port_at(self, x: float, y: float, is_input: bool) -> bool:
        """Add an interface port at the given position.

        The port will only be created if dropped on the correct stage
        (input ports on input stage, output ports on output stage).

        Args:
            x: X position in scene coordinates.
            y: Y position in scene coordinates.
            is_input: True to create an input port, False for output port.

        Returns:
            True if the port was created, False if dropped in wrong location.
        """
        if not self._input_stage or not self._output_stage:
            return False

        # Determine which stage the drop is on
        target_stage = None
        if is_input:
            # Check if drop is on the input stage
            input_rect = self._input_stage.sceneBoundingRect()
            if input_rect.contains(x, y):
                target_stage = self._input_stage
        else:
            # Check if drop is on the output stage
            output_rect = self._output_stage.sceneBoundingRect()
            if output_rect.contains(x, y):
                target_stage = self._output_stage

        if target_stage is None:
            # Not dropped on the correct stage
            return False

        # Snap y position to grid
        snapped_y = self._grid.snap_to_grid(y)

        # Create a unique name for the port
        existing_names = {p.name for p in self._design.interface_ports}
        base_name = "in" if is_input else "out"
        port_name = base_name
        counter = 1
        while port_name in existing_names:
            port_name = f"{base_name}{counter}"
            counter += 1

        # Calculate position in grid units
        grid_x = int(x / self._grid.size)
        grid_y = int(snapped_y / self._grid.size)

        # Create the interface port model
        direction = InterfaceDirection.INPUT if is_input else InterfaceDirection.OUTPUT
        iface_port = InterfacePort(
            name=port_name,
            direction=direction,
            data_type="std_logic_vector",
            position=(grid_x, grid_y),
        )

        # Add to design
        self._design.interface_ports.append(iface_port)

        # Create the graphics item
        port_item = self._create_interface_port_item(iface_port, is_input)

        # Position the port - centered horizontally on the stage
        stage_x = target_stage.get_x_position()
        if is_input:
            # Input ports on right edge of input stage
            port_x = stage_x + InterfaceStageItem.STAGE_WIDTH
        else:
            # Output ports on left edge of output stage
            port_x = stage_x

        # Set the position (relative to stage because it will be added as child)
        if is_input:
            rel_x = InterfaceStageItem.STAGE_WIDTH
        else:
            rel_x = 0

        # Calculate relative y position within the stage
        stage_y = target_stage.pos().y()
        rel_y = snapped_y - stage_y

        # Add to stage without auto-positioning (preserve manual position)
        target_stage.add_port(port_item, auto_position=False)
        port_item.setPos(rel_x, rel_y)

        # Update bounds
        self._update_component_bounds()

        return True

    def remove_interface_port(self, port_id: UUID) -> bool:
        """Remove an interface port from the scene.

        Args:
            port_id: ID of the interface port to remove.

        Returns:
            True if the port was removed, False if not found.
        """
        port_item = self._interface_port_items.get(port_id)
        if port_item is None:
            return False

        # Remove from scene
        self.removeItem(port_item)
        del self._interface_port_items[port_id]

        # Remove from design
        self._design.interface_ports = [
            p for p in self._design.interface_ports if p.id != port_id
        ]

        return True

    def get_interface_port_item(self, port_id: UUID) -> InterfacePortItem | None:
        """Get an interface port item by ID."""
        return self._interface_port_items.get(port_id)

    def _update_component_bounds(self) -> None:
        """Update the component bounds rectangle.

        Only ComponentItems (primitives/components) affect the vertical extent,
        not the register stage items which extend independently.

        Also auto-fits the input/output stages to contain all components.
        """
        if not self._component_bounds or not self._input_stage or not self._output_stage:
            return

        # Collect only component rectangles (not stage items)
        component_rects = []
        for comp_item in self._component_items.values():
            rect = comp_item.sceneBoundingRect()
            component_rects.append(rect)

        # Auto-fit input/output stages to contain all components
        self._auto_fit_interface_stages(component_rects)

        # Get updated positions after auto-fit
        input_x = self._input_stage.get_x_position()
        output_x = self._output_stage.get_x_position()

        top_y, bottom_y = self._component_bounds.update_from_components(
            input_x,
            output_x,
            InterfaceStageItem.STAGE_WIDTH,
            component_rects,
        )

        # Update interface stage heights to exactly match the bounds rectangle
        height = bottom_y - top_y
        self._input_stage.set_height(height, top_y)
        self._output_stage.set_height(height, top_y)

        # Also update pipeline stage items to match bounds height
        for stage_item in self._stage_items.values():
            stage_item.set_bounds(top_y, bottom_y)

        # Update the design's visual extent
        self._update_visual_extent(input_x, output_x, top_y, bottom_y)

    def _auto_fit_interface_stages(self, component_rects: list[QRectF]) -> None:
        """Auto-fit input/output stages to contain all components.

        Args:
            component_rects: List of component bounding rectangles.
        """
        if not component_rects or not self._input_stage or not self._output_stage:
            return

        # Find the leftmost and rightmost component extents
        min_x = min(rect.left() for rect in component_rects)
        max_x = max(rect.right() for rect in component_rects)

        # Add padding
        padding = self._grid.to_pixels(2)

        # Get current stage positions
        input_x = self._input_stage.get_x_position()
        output_x = self._output_stage.get_x_position()

        # Calculate where stages should be
        required_input_x = min_x - InterfaceStageItem.STAGE_WIDTH - padding
        required_output_x = max_x + padding

        # Move input stage left if components extend beyond it
        if required_input_x < input_x:
            snapped_x = self._grid.snap_to_grid(required_input_x)
            self._input_stage.setX(snapped_x)

        # Move output stage right if components extend beyond it
        if required_output_x > output_x:
            snapped_x = self._grid.snap_to_grid(required_output_x)
            self._output_stage.setX(snapped_x)

    def _update_visual_extent(
        self, input_x: float, output_x: float, top_y: float, bottom_y: float
    ) -> None:
        """Update the design's visual extent from scene coordinates."""
        # Convert to grid units
        input_grid_x = int(input_x / self._grid.size)
        output_grid_x = int((output_x + InterfaceStageItem.STAGE_WIDTH) / self._grid.size)
        top_grid_y = int(top_y / self._grid.size)
        bottom_grid_y = int(bottom_y / self._grid.size)

        self._design.update_visual_extent(
            input_grid_x, output_grid_x, top_grid_y, bottom_grid_y
        )

    def set_interface_enabled(self, enabled: bool) -> None:
        """Enable or disable interface stages."""
        if enabled == self._interface_enabled:
            return

        self._interface_enabled = enabled

        if enabled:
            self._create_interface_items()
        else:
            if self._input_stage:
                self.removeItem(self._input_stage)
                self._input_stage = None
            if self._output_stage:
                self.removeItem(self._output_stage)
                self._output_stage = None
            if self._component_bounds:
                self.removeItem(self._component_bounds)
                self._component_bounds = None

    def get_input_stage(self) -> InterfaceStageItem | None:
        """Get the input interface stage."""
        return self._input_stage

    def get_output_stage(self) -> InterfaceStageItem | None:
        """Get the output interface stage."""
        return self._output_stage

    def set_library(
        self,
        library: dict[str, ComponentDefinition],
        loader: LibraryLoader | None = None,
    ) -> None:
        """Set the component library.

        Args:
            library: Dictionary mapping component names to definitions.
            loader: Optional library loader for composite component support.
        """
        self._library = library
        self._library_loader = loader
        # Cache the register width from the library
        register_def = library.get("Register")
        if register_def:
            self._register_width = self._grid.to_pixels(register_def.visual.width)

    def get_design(self) -> Design:
        """Get the current design."""
        return self._design

    def set_design(self, design: Design) -> None:
        """Set a new design, clearing existing items."""
        self.clear()
        self._component_items.clear()
        self._stage_items.clear()
        self._connection_items.clear()
        self._interface_port_items.clear()
        self._undo_stack.clear()
        self._move_start_positions.clear()
        self._input_stage = None
        self._output_stage = None
        self._component_bounds = None
        self._design = design

        # Create interface stages and bounds
        if self._interface_enabled:
            self._create_interface_items()

        # Create stage items first (they're behind components)
        for stage in design.stages:
            self._create_stage_item(stage)

        # Create component items
        for instance in design.components:
            self._create_component_item(instance)

        # Create connection items
        for connection in design.connections:
            self._create_connection_item(connection)

        # Update bounds after all items are created
        self._update_component_bounds()

    def new_design(self) -> None:
        """Create a new empty design."""
        self.clear()
        self._component_items.clear()
        self._stage_items.clear()
        self._connection_items.clear()
        self._interface_port_items.clear()
        self._undo_stack.clear()
        self._move_start_positions.clear()
        self._input_stage = None
        self._output_stage = None
        self._component_bounds = None
        self._design = Design()

        # Create interface stages and bounds
        if self._interface_enabled:
            self._create_interface_items()

    def add_component_at(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Add a component instance at the specified position (with undo support).

        For registers, this also handles stage assignment/creation.

        Args:
            component_name: Name of the component definition.
            x: X position in pixels.
            y: Y position in pixels.

        Returns:
            The created ComponentItem, or None if component not found.
        """
        definition = self._library.get(component_name)
        if definition is None:
            return None

        # Snap coordinates
        if self._snap_to_grid:
            y = self._grid.snap_to_grid(y)

        if component_name == "Register":
            x = self._get_register_x_position(x)
        elif self._snap_to_grid:
            x = self._grid.snap_to_grid(x)

        # Use command for undo support
        command = AddComponentCommand(scene=self, component_name=component_name, x=x, y=y)
        self._undo_stack.push(command)

        # Return the created item
        if command._instance:
            return self._component_items.get(command._instance.id)
        return None

    def _add_component_internal(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Internal method to add a component (used by commands).

        Args:
            component_name: Name of the component definition.
            x: X position in pixels (already snapped).
            y: Y position in pixels (already snapped).

        Returns:
            The created ComponentItem, or None if component not found.
        """
        definition = self._library.get(component_name)
        if definition is None:
            return None

        # Check if this is a composite component
        is_composite = False
        stage_count = 1
        if self._library_loader and self._library_loader.is_composite(component_name):
            is_composite = True
            composite_design = self._library_loader.get_composite_design(component_name)
            if composite_design:
                stage_count = max(1, composite_design.latency)

        instance = ComponentInstance(
            definition_ref=component_name,
            position=(x, y),
            is_composite=is_composite,
            stage_count=stage_count,
        )

        self._design.add_component(instance)
        item = self._create_component_item(instance)

        # Handle stage assignment for registers
        if component_name == "Register":
            self._assign_register_to_stage(instance)
        # Handle stage alignment for composite components
        elif is_composite and stage_count > 1:
            self._assign_composite_to_stages(instance)

        self.component_added.emit(instance)

        # Update component bounds
        self._update_component_bounds()

        return item

    def _get_register_x_position(self, x: float) -> float:
        """Get the x position for a register, snapping to existing stages."""
        stage = self._design.get_stage_at_x(x)
        if stage:
            return stage.x_position
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _assign_register_to_stage(self, instance: ComponentInstance) -> None:
        """Assign a register instance to a stage, creating one if needed."""
        x = instance.position[0]
        stage = self._design.get_stage_at_x(x)

        if stage is None:
            stage = Stage(
                index=0,
                x_position=x,
                width=self._register_width,
                register_ids=[instance.id],
            )
            self._design.stages.append(stage)
            self._design.reindex_stages()
            self._rebuild_all_stages()
        else:
            if instance.id not in stage.register_ids:
                stage.register_ids.append(instance.id)

        instance.pipeline_stage = stage.index
        self._update_register_displays()
        self.stages_changed.emit()

    def _assign_composite_to_stages(self, instance: ComponentInstance) -> None:
        """Assign a composite component to pipeline stages.

        Synchronizes the composite's internal register stages with the main
        design's stages. The component is positioned so that its internal
        stages align with existing or newly created main design stages.
        """
        if not self._library_loader:
            return

        composite_design = self._library_loader.get_composite_design(
            instance.definition_ref
        )
        if not composite_design:
            return

        # Get internal stage offsets (relative to component origin)
        internal_stage_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_stage_offsets:
            # No internal stages - nothing to synchronize
            return

        drop_x = instance.position[0]
        drop_y = instance.position[1]

        # Calculate where the first internal stage would be at current position
        first_internal_offset = internal_stage_offsets[0]
        first_internal_stage_x = drop_x + first_internal_offset

        # Find the nearest main design stage to align with
        nearest_stage = self._find_nearest_stage(first_internal_stage_x)

        if nearest_stage is not None:
            # Align the composite so its first internal stage matches the main stage
            component_x = nearest_stage.x_position - first_internal_offset
            component_x = self._grid.snap_to_grid(component_x)

            instance.pipeline_stage = nearest_stage.index

            # Update position
            item = self._component_items.get(instance.id)
            if item:
                item.setPos(component_x, drop_y)
                instance.position = (component_x, drop_y)

            # Create additional stages if needed for remaining internal stages
            self._ensure_stages_for_composite(
                composite_design, instance, component_x, nearest_stage.index
            )
        else:
            # No stages exist - create stages based on internal stage positions
            self._create_stages_from_composite(composite_design, instance, drop_x)

        self._rebuild_all_stages()
        self.stages_changed.emit()

    def _get_composite_internal_stage_offsets(self, composite_design: Design) -> list[float]:
        """Get the x offsets of internal register stages from component origin.

        Args:
            composite_design: The composite component's internal design.

        Returns:
            List of x offsets (in pixels) from the component's left edge to each
            internal stage, sorted by position.
        """
        if not composite_design.stages:
            return []

        # Get the origin offset (input_stage_x in pixels)
        origin_x = self._grid.to_pixels(composite_design.visual.input_stage_x)

        # Calculate offset from component origin to each internal stage
        offsets = []
        for stage in sorted(composite_design.stages, key=lambda s: s.x_position):
            # Internal stage x_position is in design pixel coordinates
            # Component origin is at input_stage_x in those coordinates
            offset = stage.x_position - origin_x
            offsets.append(offset)

        return offsets

    def _find_nearest_stage(self, x: float) -> Stage | None:
        """Find the nearest stage to a given x position.

        Args:
            x: X position in pixels.

        Returns:
            The nearest Stage, or None if no stages exist.
        """
        if not self._design.stages:
            return None

        nearest = None
        min_distance = float('inf')

        for stage in self._design.stages:
            distance = abs(stage.x_position - x)
            if distance < min_distance:
                min_distance = distance
                nearest = stage

        # Only return if within a reasonable snapping distance
        snap_threshold = self._grid.to_pixels(5)  # 5 grid units
        if min_distance <= snap_threshold:
            return nearest

        return None

    def _ensure_stages_for_composite(
        self,
        composite_design: Design,
        instance: ComponentInstance,
        component_x: float,
        first_stage_index: int,
    ) -> None:
        """Ensure main design has stages for all internal composite stages.

        Args:
            composite_design: The composite component's internal design.
            instance: The composite component instance.
            component_x: The component's x position.
            first_stage_index: Index of the first aligned stage.
        """
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if len(internal_offsets) <= 1:
            return

        # For each additional internal stage, ensure a main design stage exists
        for i, offset in enumerate(internal_offsets[1:], start=1):
            target_x = component_x + offset
            target_stage_index = first_stage_index + i

            # Check if a stage exists at this index
            existing_stage = None
            for stage in self._design.stages:
                if stage.index == target_stage_index:
                    existing_stage = stage
                    break

            if existing_stage is None:
                # Get width from internal stage if available
                internal_stage = composite_design.stages[i] if i < len(composite_design.stages) else None
                stage_width = internal_stage.width if internal_stage else self._register_width

                # Create new stage
                new_stage = Stage(
                    index=target_stage_index,
                    x_position=self._grid.snap_to_grid(target_x),
                    width=stage_width,
                    register_ids=[],
                )
                self._design.stages.append(new_stage)

        self._design.reindex_stages()

    def _create_stages_from_composite(
        self,
        composite_design: Design,
        instance: ComponentInstance,
        drop_x: float,
    ) -> None:
        """Create main design stages based on composite's internal stages.

        Args:
            composite_design: The composite component's internal design.
            instance: The composite component instance.
            drop_x: Where the component was dropped.
        """
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_offsets:
            return

        # Snap the component position
        component_x = self._grid.snap_to_grid(drop_x)

        # Update component position
        item = self._component_items.get(instance.id)
        if item:
            item.setPos(component_x, instance.position[1])
            instance.position = (component_x, instance.position[1])

        # Create stages for each internal stage
        for i, offset in enumerate(internal_offsets):
            stage_x = component_x + offset
            stage_x = self._grid.snap_to_grid(stage_x)

            # Get width from internal stage
            internal_stage = composite_design.stages[i] if i < len(composite_design.stages) else None
            stage_width = internal_stage.width if internal_stage else self._register_width

            new_stage = Stage(
                index=i,
                x_position=stage_x,
                width=stage_width,
                register_ids=[],
            )
            self._design.stages.append(new_stage)

        self._design.reindex_stages()
        instance.pipeline_stage = 0

    def _create_stage_item(self, stage: Stage) -> StageItem:
        """Create a graphics item for a stage."""
        item = StageItem(stage, view_height=10000.0)
        self.addItem(item)
        self._stage_items[stage.id] = item
        return item

    def _rebuild_all_stages(self) -> None:
        """Rebuild all stage items from scratch."""
        for stage_id, item in list(self._stage_items.items()):
            self.removeItem(item)
        self._stage_items.clear()
        for stage in self._design.stages:
            self._create_stage_item(stage)

    def _update_all_pipeline_stages(self) -> None:
        """Update pipeline_stage for all registers based on current stages."""
        for stage in self._design.stages:
            for reg_id in stage.register_ids:
                instance = self._design.get_component_by_id(reg_id)
                if instance:
                    instance.pipeline_stage = stage.index

    def _update_register_displays(self) -> None:
        """Force all register component items to repaint."""
        for comp_id, item in self._component_items.items():
            if item.is_register():
                item.update()

    def _create_component_item(self, instance: ComponentInstance) -> ComponentItem:
        """Create a graphics item for a component instance."""
        definition = self._library.get(instance.definition_ref)

        # Get composite design if this is a composite component
        composite_design = None
        if instance.is_composite and self._library_loader:
            composite_design = self._library_loader.get_composite_design(
                instance.definition_ref
            )

        item = ComponentItem(
            instance,
            definition,
            grid=self._grid,
            snap_to_grid=self._snap_to_grid,
            composite_design=composite_design,
            library=self._library,
        )
        item.setPos(instance.position[0], instance.position[1])
        self.addItem(item)
        self._component_items[instance.id] = item

        # Connect callbacks for registers
        if instance.definition_ref == "Register":
            item.register_moved = self._on_register_moved
            item.snap_register_x = self.snap_register_x

        # Wire up port callbacks for connections
        self._wire_port_callbacks(item)

        # Wire up move callbacks for undo tracking
        item.on_move_start = lambda: self.record_move_start(instance.id)
        item.on_move_end = lambda: self.record_move_end(instance.id)

        return item

    def _wire_port_callbacks(self, component_item: ComponentItem) -> None:
        """Wire up port callbacks for connection handling."""
        for port_name, port_item in component_item._port_items.items():
            port_item.on_connection_start = lambda pi=port_item: self._start_connection(pi)

    def _start_connection(self, port_item: PortItem) -> None:
        """Start creating a connection from an output port."""
        if not port_item.is_output():
            return

        self._connection_source_port = port_item
        self._connection_source_component_id = port_item.get_component_id()
        self._connection_source_interface_port = None

        # Disable movement on all components during connection
        self._set_components_movable(False)

        # Create temporary connection line
        start_pos = port_item.scenePos()
        self._temp_connection = TempConnectionItem(start_pos)
        self.addItem(self._temp_connection)

    def _start_interface_connection(self, interface_port_item: InterfacePortItem) -> None:
        """Start creating a connection from an input interface port.

        Input interface ports act as sources - they provide data INTO the design.
        """
        if not interface_port_item.is_input():
            return

        self._connection_source_interface_port = interface_port_item
        self._connection_source_port = None
        self._connection_source_component_id = None

        # Disable movement on all components during connection
        self._set_components_movable(False)

        # Create temporary connection line
        start_pos = interface_port_item.scenePos()
        self._temp_connection = TempConnectionItem(start_pos)
        self.addItem(self._temp_connection)

    def _is_valid_connection_target(self, target_port: PortItem) -> bool:
        """Check if a component port is a valid connection target."""
        # Must have a source (either component port or interface port)
        if self._connection_source_port is None and self._connection_source_interface_port is None:
            return False

        # Must be an input port
        if not target_port.is_input():
            return False

        target_comp_id = target_port.get_component_id()

        # If source is a component port
        if self._connection_source_port is not None:
            source_comp_id = self._connection_source_component_id
            # Cannot connect to same component
            if source_comp_id == target_comp_id:
                return False

            # Check if connection already exists
            source_port_name = self._connection_source_port.get_port().name
            target_port_name = target_port.get_port().name
            for conn in self._design.connections:
                if (conn.source.component_id == source_comp_id and
                    conn.source.port_name == source_port_name and
                    conn.target.component_id == target_comp_id and
                    conn.target.port_name == target_port_name):
                    return False

        # If source is an interface port
        elif self._connection_source_interface_port is not None:
            source_iface_port = self._connection_source_interface_port.get_interface_port()
            target_port_name = target_port.get_port().name

            # Check if connection already exists
            for conn in self._design.connections:
                if (conn.source.interface_port_id == source_iface_port.id and
                    conn.target.component_id == target_comp_id and
                    conn.target.port_name == target_port_name):
                    return False

        return True

    def _is_valid_interface_target(self, target_interface_port: InterfacePortItem) -> bool:
        """Check if an output interface port is a valid connection target."""
        # Must have a component port source
        if self._connection_source_port is None:
            return False

        # Target must be an output interface port (it receives data from the design)
        if not target_interface_port.is_output():
            return False

        source_comp_id = self._connection_source_component_id
        source_port_name = self._connection_source_port.get_port().name
        target_iface_port = target_interface_port.get_interface_port()

        # Check if connection already exists
        for conn in self._design.connections:
            if (conn.source.component_id == source_comp_id and
                conn.source.port_name == source_port_name and
                conn.target.interface_port_id == target_iface_port.id):
                return False

        return True

    def _create_connection(
        self,
        source_port: PortItem,
        source_comp_id: UUID,
        target_port: PortItem,
    ) -> None:
        """Create a new connection between component ports (with undo support)."""
        target_comp_id = target_port.get_component_id()
        if target_comp_id is None:
            return

        connection = Connection(
            source=PortReference(
                component_id=source_comp_id,
                port_name=source_port.get_port().name,
            ),
            target=PortReference(
                component_id=target_comp_id,
                port_name=target_port.get_port().name,
            ),
        )

        command = AddConnectionCommand(scene=self, connection=connection)
        self._undo_stack.push(command)

    def _create_interface_to_component_connection(
        self,
        source_interface_port: InterfacePortItem,
        target_port: PortItem,
    ) -> None:
        """Create a connection from an interface port to a component port."""
        target_comp_id = target_port.get_component_id()
        if target_comp_id is None:
            return

        iface_port = source_interface_port.get_interface_port()
        connection = Connection(
            source=PortReference(
                interface_port_id=iface_port.id,
                port_name=iface_port.name,
            ),
            target=PortReference(
                component_id=target_comp_id,
                port_name=target_port.get_port().name,
            ),
        )

        command = AddConnectionCommand(scene=self, connection=connection)
        self._undo_stack.push(command)

    def _create_component_to_interface_connection(
        self,
        source_port: PortItem,
        source_comp_id: UUID,
        target_interface_port: InterfacePortItem,
    ) -> None:
        """Create a connection from a component port to an interface port."""
        iface_port = target_interface_port.get_interface_port()
        connection = Connection(
            source=PortReference(
                component_id=source_comp_id,
                port_name=source_port.get_port().name,
            ),
            target=PortReference(
                interface_port_id=iface_port.id,
                port_name=iface_port.name,
            ),
        )

        command = AddConnectionCommand(scene=self, connection=connection)
        self._undo_stack.push(command)

    def _create_connection_item(self, connection: Connection) -> ConnectionItem | None:
        """Create a graphics item for a connection."""
        # Get source and target positions (handle both component and interface ports)
        source_pos = self._get_port_position(
            connection.source.component_id,
            connection.source.port_name,
            connection.source.interface_port_id,
        )
        target_pos = self._get_port_position(
            connection.target.component_id,
            connection.target.port_name,
            connection.target.interface_port_id,
        )

        if source_pos is None or target_pos is None:
            return None

        item = ConnectionItem(
            connection,
            QPointF(source_pos[0], source_pos[1]),
            QPointF(target_pos[0], target_pos[1]),
        )
        self.addItem(item)
        self._connection_items[connection.id] = item
        return item

    def _get_port_position(self, component_id: UUID | None, port_name: str, interface_port_id: UUID | None = None) -> tuple[float, float] | None:
        """Get the scene position of a port (component or interface)."""
        # If it's an interface port
        if interface_port_id is not None:
            iface_item = self._interface_port_items.get(interface_port_id)
            if iface_item is not None:
                pos = iface_item.scenePos()
                return (pos.x(), pos.y())
            return None

        # If it's a component port
        if component_id is not None:
            comp_item = self._component_items.get(component_id)
            if comp_item is None:
                return None
            return comp_item.get_port_scene_pos(port_name)

        return None

    def _cancel_connection(self) -> None:
        """Cancel the current connection creation."""
        if self._temp_connection:
            self.removeItem(self._temp_connection)
            self._temp_connection = None
        self._connection_source_port = None
        self._connection_source_component_id = None
        self._connection_source_interface_port = None

        # Re-enable movement on all components
        self._set_components_movable(True)

        # Reset any highlighted ports (component ports)
        for comp_item in self._component_items.values():
            for port_item in comp_item._port_items.values():
                port_item.set_connection_target(False)

        # Reset any highlighted interface ports
        for iface_port_item in self._interface_port_items.values():
            iface_port_item.set_highlighted(False)

    def _set_components_movable(self, movable: bool) -> None:
        """Enable or disable movement on all component items."""
        from PySide6.QtWidgets import QGraphicsItem
        for comp_item in self._component_items.values():
            comp_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, movable)

    def remove_connection(self, connection_id: UUID) -> bool:
        """Remove a connection from the scene (with undo support)."""
        item = self._connection_items.get(connection_id)
        if item is None:
            return False

        command = RemoveConnectionCommand(scene=self, connection_id=connection_id)
        self._undo_stack.push(command)
        return True

    def _remove_connection_internal(self, connection_id: UUID) -> bool:
        """Internal method to remove a connection (used by commands)."""
        item = self._connection_items.get(connection_id)
        if item is None:
            return False

        self.removeItem(item)
        del self._connection_items[connection_id]
        self._design.remove_connection(connection_id)
        self.connection_removed.emit(connection_id)
        return True

    def _add_connection_internal(self, connection: Connection) -> ConnectionItem | None:
        """Internal method to add a connection (used by commands)."""
        self._design.add_connection(connection)
        item = self._create_connection_item(connection)
        self.connection_added.emit(connection)
        return item

    def _restore_connection_internal(self, connection: Connection) -> ConnectionItem | None:
        """Internal method to restore a connection (used by undo)."""
        return self._add_connection_internal(connection)

    def update_connection_positions(self) -> None:
        """Update all connection positions after components move."""
        for conn_id, conn_item in self._connection_items.items():
            conn = conn_item.get_connection()
            source_pos = self._get_port_position(
                conn.source.component_id,
                conn.source.port_name,
                conn.source.interface_port_id,
            )
            target_pos = self._get_port_position(
                conn.target.component_id,
                conn.target.port_name,
                conn.target.interface_port_id,
            )
            if source_pos and target_pos:
                conn_item.update_positions(
                    QPointF(source_pos[0], source_pos[1]),
                    QPointF(target_pos[0], target_pos[1]),
                )

        # Update component bounds when components move
        self._update_component_bounds()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse move for connection dragging."""
        super().mouseMoveEvent(event)

        # Update temporary connection line
        if self._temp_connection:
            self._temp_connection.set_end_pos(event.scenePos())

            # Check if we're over a valid target
            items = self.items(event.scenePos())
            target_port = None
            target_interface_port = None

            for item in items:
                if isinstance(item, PortItem) and item.is_input():
                    target_port = item
                    break
                elif isinstance(item, InterfacePortItem) and item.is_output():
                    # Output interface ports can be targets (from component output ports)
                    target_interface_port = item
                    break

            # Reset all highlighting first
            for comp_item in self._component_items.values():
                for port_item in comp_item._port_items.values():
                    port_item.set_connection_target(False)
            for iface_port_item in self._interface_port_items.values():
                iface_port_item.set_highlighted(False)

            # Highlight valid target
            found_valid_target = False
            if target_port is not None:
                is_valid = self._is_valid_connection_target(target_port)
                target_port.set_connection_target(True, is_valid)
                self._temp_connection.set_target_state(True, is_valid)
                found_valid_target = True
            elif target_interface_port is not None and self._connection_source_port is not None:
                # Connecting from component output to output interface port
                is_valid = self._is_valid_interface_target(target_interface_port)
                target_interface_port.set_highlighted(is_valid)
                self._temp_connection.set_target_state(True, is_valid)
                found_valid_target = True

            if not found_valid_target:
                self._temp_connection.set_target_state(False)

        # Update connection positions when components are being moved
        self.update_connection_positions()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse release for connection creation."""
        if self._temp_connection:
            items = self.items(event.scenePos())

            # Case 1: Source is a component output port
            if self._connection_source_port is not None:
                for item in items:
                    # Target is a component input port
                    if isinstance(item, PortItem) and item.is_input():
                        if self._is_valid_connection_target(item):
                            self._create_connection(
                                self._connection_source_port,
                                self._connection_source_component_id,
                                item,
                            )
                            self._cancel_connection()
                            super().mouseReleaseEvent(event)
                            return
                    # Target is an output interface port
                    elif isinstance(item, InterfacePortItem) and item.is_output():
                        if self._is_valid_interface_target(item):
                            self._create_component_to_interface_connection(
                                self._connection_source_port,
                                self._connection_source_component_id,
                                item,
                            )
                            self._cancel_connection()
                            super().mouseReleaseEvent(event)
                            return

            # Case 2: Source is an input interface port
            elif self._connection_source_interface_port is not None:
                for item in items:
                    # Target is a component input port
                    if isinstance(item, PortItem) and item.is_input():
                        if self._is_valid_connection_target(item):
                            self._create_interface_to_component_connection(
                                self._connection_source_interface_port,
                                item,
                            )
                            self._cancel_connection()
                            super().mouseReleaseEvent(event)
                            return

            # No valid target - cancel connection
            self._cancel_connection()

        super().mouseReleaseEvent(event)

    def _on_register_moved(self, instance: ComponentInstance, old_x: float) -> None:
        """Handle a register being moved."""
        new_x = instance.position[0]

        for stage in self._design.stages:
            if instance.id in stage.register_ids:
                stage.register_ids.remove(instance.id)
                break

        existing_stage = self._design.get_stage_at_x(new_x)

        if existing_stage is not None:
            if instance.id not in existing_stage.register_ids:
                existing_stage.register_ids.append(instance.id)
        else:
            new_stage = Stage(
                index=0,
                x_position=new_x,
                width=self._register_width,
                register_ids=[instance.id],
            )
            self._design.stages.append(new_stage)

        self._design.remove_empty_stages()
        self._design.reindex_stages()
        self._update_all_pipeline_stages()
        self._rebuild_all_stages()
        self._update_register_displays()
        self.stages_changed.emit()

    def remove_component(self, component_id: UUID) -> bool:
        """Remove a component instance from the scene (with undo support)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        command = RemoveComponentCommand(scene=self, component_id=component_id)
        self._undo_stack.push(command)
        return True

    def _remove_component_internal(self, component_id: UUID) -> bool:
        """Internal method to remove a component (used by commands)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        is_register = instance.definition_ref == "Register"

        # Remove connections involving this component (without undo tracking)
        conns_to_remove = [
            conn.id for conn in self._design.connections
            if conn.source.component_id == component_id or conn.target.component_id == component_id
        ]
        for conn_id in conns_to_remove:
            self._remove_connection_internal(conn_id)

        if is_register:
            for stage in self._design.stages:
                if component_id in stage.register_ids:
                    stage.register_ids.remove(component_id)
                    break

        self.removeItem(item)
        del self._component_items[component_id]
        self._design.remove_component(component_id)

        if is_register:
            self._design.remove_empty_stages()
            self._design.reindex_stages()
            self._update_all_pipeline_stages()
            self._rebuild_all_stages()
            self._update_register_displays()
            self.stages_changed.emit()

        self.component_removed.emit(component_id)

        # Update component bounds
        self._update_component_bounds()

        return True

    def _restore_component_internal(self, instance: ComponentInstance) -> ComponentItem | None:
        """Internal method to restore a component (used by undo)."""
        # Re-add to design
        self._design.add_component(instance)

        # Create the graphics item
        item = self._create_component_item(instance)

        # Handle stage assignment for registers
        if instance.definition_ref == "Register":
            self._assign_register_to_stage(instance)

        self.component_added.emit(instance)

        # Update component bounds
        self._update_component_bounds()

        return item

    def _move_component_internal(self, component_id: UUID, pos: tuple[float, float]) -> bool:
        """Internal method to move a component (used by undo/redo)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        old_x = instance.position[0]
        is_register = instance.definition_ref == "Register"

        # Update position
        instance.position = pos
        item.setPos(pos[0], pos[1])

        # Handle stage updates for registers
        if is_register:
            self._on_register_moved(instance, old_x)

        # Update connections
        self.update_connection_positions()
        return True

    def get_component_item(self, component_id: UUID) -> ComponentItem | None:
        """Get a component item by ID."""
        return self._component_items.get(component_id)

    def get_stage_at_position(self, x: float) -> Stage | None:
        """Get the stage at a given x position."""
        return self._design.get_stage_at_x(x)

    def snap_to_grid(self, x: float, y: float) -> tuple[float, float]:
        """Snap coordinates to the grid."""
        if not self._snap_to_grid:
            return x, y
        return (
            self._grid.snap_to_grid(x),
            self._grid.snap_to_grid(y),
        )

    def snap_register_x(self, x: float) -> float:
        """Snap x coordinate for a register (stage-aware)."""
        stage = self._design.get_stage_at_x(x)
        if stage:
            return stage.x_position
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def set_snap_to_grid(self, enabled: bool) -> None:
        """Enable or disable grid snapping."""
        self._snap_to_grid = enabled

    def drawBackground(self, painter, rect) -> None:
        """Draw the grid background."""
        super().drawBackground(painter, rect)

        grid_size = self._grid.size
        pen = QPen(QColor("#3a3a3a"))
        pen.setWidth(1)
        painter.setPen(pen)

        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)

        x = left
        while x < rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += grid_size

        y = top
        while y < rect.bottom():
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += grid_size

    # Undo/Redo methods

    def undo(self) -> bool:
        """Undo the last action."""
        return self._undo_stack.undo()

    def redo(self) -> bool:
        """Redo the last undone action."""
        return self._undo_stack.redo()

    def can_undo(self) -> bool:
        """Check if there are actions to undo."""
        return self._undo_stack.can_undo()

    def can_redo(self) -> bool:
        """Check if there are actions to redo."""
        return self._undo_stack.can_redo()

    def get_undo_description(self) -> str | None:
        """Get description of the next action to undo."""
        return self._undo_stack.get_undo_description()

    def get_redo_description(self) -> str | None:
        """Get description of the next action to redo."""
        return self._undo_stack.get_redo_description()

    def clear_undo_stack(self) -> None:
        """Clear all undo/redo history."""
        self._undo_stack.clear()

    # Component movement tracking for undo

    def record_move_start(self, component_id: UUID) -> None:
        """Record the start position of a component move."""
        item = self._component_items.get(component_id)
        if item:
            instance = item.get_instance()
            self._move_start_positions[component_id] = instance.position

    def record_move_end(self, component_id: UUID) -> None:
        """Record the end of a component move and create undo command."""
        if component_id not in self._move_start_positions:
            return

        item = self._component_items.get(component_id)
        if item is None:
            del self._move_start_positions[component_id]
            return

        old_pos = self._move_start_positions[component_id]
        new_pos = item.get_instance().position
        del self._move_start_positions[component_id]

        # Only create command if position actually changed
        if old_pos != new_pos:
            command = MoveComponentCommand(
                scene=self,
                component_id=component_id,
                old_pos=old_pos,
                new_pos=new_pos,
            )
            # Don't execute - movement already happened, just record for undo
            self._undo_stack._undo_stack.append(command)
            self._undo_stack._redo_stack.clear()
