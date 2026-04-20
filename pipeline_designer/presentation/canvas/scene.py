"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene, QGraphicsSceneMouseEvent

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
    MoveStageCommand,
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
    TempPositionOverlayItem,
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
        self._preview_port_item: QGraphicsEllipseItem | None = None

        # Track items with distance conflicts (made semi-transparent)
        self._conflict_items: set[UUID] = set()

        # ── Stage group-move state (Priority 1 & 3) ──────────────────────────
        # Set while the user drags an entire stage band.
        self._moving_stage: Stage | None = None
        self._stage_move_mouse_start: QPointF = QPointF()
        self._stage_move_stage_start_x: float = 0.0   # grid units
        self._stage_move_new_x: float = 0.0           # grid units (updated each tick)
        self._stage_group_items: list[ComponentItem] = []
        self._stage_group_original_positions: dict[UUID, tuple[float, float]] = {}
        # When True, _on_register_moved and snap_register_x skip their logic
        self._in_stage_group_move: bool = False

        # ── Composite drag alignment state (Priority 2) ──────────────────────
        # ID of the composite currently being dragged (if any).
        self._dragging_composite_id: UUID | None = None
        # Temporary overlay items shown during drag
        self._composite_drag_overlays: list[TempPositionOverlayItem] = []
        # Proposed stage shifts: {stage_id → delta_grid_units} — only positive
        self._composite_proposed_shifts: dict[UUID, float] = {}
        # Whether the last composite drag position was aligned (used on drop)
        self._last_drag_was_aligned: bool = True

        # ── Accepted temporary positions (Priority 2) ────────────────────────
        # Overlays kept after accepting the drag (orange dashed around composites)
        self._temp_position_overlays: dict[UUID, TempPositionOverlayItem] = {}

        # ── Composite → stage bindings (Priority 3) ──────────────────────────
        # composite_instance_id → { internal_stage_index → main_stage_id }
        self._composite_stage_bindings: dict[UUID, dict[int, UUID]] = {}

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
        default_output_x = 200.0

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

        # Create ports from design interface_ports, restoring saved positions
        for iface_port in self._design.get_input_interfaces():
            port_item = self._create_interface_port_item(iface_port, is_input=True)
            self._input_stage.add_port(port_item)
            rel_y = (iface_port.position[1] * self._grid.size
                     if iface_port.position
                     else self._input_stage.rect().height() / 2)
            port_item.set_pos_exact(InterfaceStageItem.STAGE_WIDTH / 2, rel_y)

        for iface_port in self._design.get_output_interfaces():
            port_item = self._create_interface_port_item(iface_port, is_input=False)
            self._output_stage.add_port(port_item)
            rel_y = (iface_port.position[1] * self._grid.size
                     if iface_port.position
                     else self._output_stage.rect().height() / 2)
            port_item.set_pos_exact(InterfaceStageItem.STAGE_WIDTH / 2, rel_y)

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

        # Wire up reposition callbacks (Ctrl+drag)
        port_item.on_reposition_preview = lambda sy: self._on_reposition_preview(
            iface_port.id, sy, is_input
        )
        port_item.on_reposition_commit = lambda sy: self._on_reposition_commit(
            iface_port.id, sy, is_input
        )

        # Wire up connection callback
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

    def _on_reposition_preview(self, port_id: UUID, scene_y: float, is_input: bool) -> None:
        """Show reposition preview clamped and snapped within the stage."""
        stage = self._input_stage if is_input else self._output_stage
        if stage is None:
            return
        stage_rect = stage.sceneBoundingRect()
        clamped_y = max(stage_rect.top(), min(stage_rect.bottom(), scene_y))
        snapped_y = self._grid.snap_to_grid(clamped_y)
        preview_x = stage.scenePos().x() + InterfaceStageItem.STAGE_WIDTH / 2
        radius = InterfacePortItem.PORT_RADIUS
        color = QColor("#27ae60") if is_input else QColor("#e67e22")

        if self._preview_port_item is None:
            self._preview_port_item = QGraphicsEllipseItem(
                -radius, -radius, radius * 2, radius * 2
            )
            self._preview_port_item.setZValue(20)
            self.addItem(self._preview_port_item)

        pen = QPen(color.darker(120))
        pen.setWidth(2)
        self._preview_port_item.setPen(pen)
        self._preview_port_item.setBrush(QBrush(color.lighter(150)))
        self._preview_port_item.setOpacity(0.5)
        self._preview_port_item.setPos(preview_x, snapped_y)

    def _on_reposition_commit(self, port_id: UUID, scene_y: float, is_input: bool) -> None:
        """Commit a repositioned port to the clamped, snapped location."""
        self.clear_interface_port_preview()
        stage = self._input_stage if is_input else self._output_stage
        port_item = self._interface_port_items.get(port_id)
        if port_item is None or stage is None:
            return
        stage_rect = stage.sceneBoundingRect()
        clamped_y = max(stage_rect.top(), min(stage_rect.bottom(), scene_y))
        snapped_y = self._grid.snap_to_grid(clamped_y)
        rel_y = snapped_y - stage.scenePos().y()
        port_item.set_pos_exact(InterfaceStageItem.STAGE_WIDTH / 2, rel_y)
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
        self.clear_interface_port_preview()

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

        # Position is stage-relative (y stored in grid units relative to stage top)
        stage_y = target_stage.pos().y()
        rel_y = snapped_y - stage_y
        grid_x = int(x / self._grid.size)
        grid_y = int(rel_y / self._grid.size)

        # Create the interface port model
        direction = InterfaceDirection.INPUT if is_input else InterfaceDirection.OUTPUT
        iface_port = InterfacePort(
            name=port_name,
            direction=direction,
            data_type="std_logic_vector",
            position=(grid_x, grid_y),
        )

        self._design.interface_ports.append(iface_port)

        port_item = self._create_interface_port_item(iface_port, is_input)
        target_stage.add_port(port_item)
        port_item.set_pos_exact(InterfaceStageItem.STAGE_WIDTH / 2, rel_y)

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

    def show_interface_port_preview(self, x: float, y: float, is_input: bool) -> None:
        """Show a semi-transparent preview circle for a port about to be placed."""
        target_stage = self._input_stage if is_input else self._output_stage
        if target_stage is None:
            self.clear_interface_port_preview()
            return

        if not target_stage.sceneBoundingRect().contains(x, y):
            self.clear_interface_port_preview()
            return

        snapped_y = self._grid.snap_to_grid(y)
        preview_x = target_stage.scenePos().x() + InterfaceStageItem.STAGE_WIDTH / 2
        radius = InterfacePortItem.PORT_RADIUS
        color = QColor("#27ae60") if is_input else QColor("#e67e22")

        if self._preview_port_item is None:
            self._preview_port_item = QGraphicsEllipseItem(
                -radius, -radius, radius * 2, radius * 2
            )
            self._preview_port_item.setZValue(20)
            self.addItem(self._preview_port_item)

        pen = QPen(color.darker(120))
        pen.setWidth(2)
        self._preview_port_item.setPen(pen)
        self._preview_port_item.setBrush(QBrush(color.lighter(150)))
        self._preview_port_item.setOpacity(0.5)
        self._preview_port_item.setPos(preview_x, snapped_y)

    def clear_interface_port_preview(self) -> None:
        """Remove the port placement preview."""
        if self._preview_port_item is not None:
            self.removeItem(self._preview_port_item)
            self._preview_port_item = None

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
        # Reset alignment state FIRST while Qt objects are still alive.
        # self.clear() below will destroy all C++ wrappers; calling .scene()
        # or removeItem() on dead wrappers raises RuntimeError.
        self._reset_alignment_state()
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

        # Update alignment indices for all components
        self._update_all_component_alignments()

        # Update bounds after all items are created
        self._update_component_bounds()

    def new_design(self) -> None:
        """Create a new empty design."""
        # Same ordering requirement as set_design: reset before clear.
        self._reset_alignment_state()
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

        # For non-register primitives, avoid placing on top of stages
        if component_name != "Register" and not is_composite:
            comp_width, _ = definition.visual.get_pixel_size(self._grid)
            x = self._avoid_stage_overlap(x, comp_width)

        # Convert pixel position to grid units for storage
        pos_grid = self._grid.pos_to_grid((x, y))
        instance = ComponentInstance(
            definition_ref=component_name,
            position=pos_grid,
            is_composite=is_composite,
            stage_count=stage_count,
        )

        self._design.add_component(instance)
        item = self._create_component_item(instance)

        # Handle stage assignment for registers
        if component_name == "Register":
            self._assign_register_to_stage(instance)
            # Adding a register may create a new stage, update all alignments
            self._update_all_component_alignments()
        # Handle stage alignment for composite components
        elif is_composite and stage_count > 1:
            self._assign_composite_to_stages(instance)
            self._update_all_component_alignments()
        else:
            # Update alignment index for non-register primitives
            self._update_component_alignment(instance)

        self.component_added.emit(instance)

        # Update component bounds
        self._update_component_bounds()

        return item

    def _get_register_x_position(self, x: float) -> float:
        """Get the x position for a register, snapping to existing stages.

        Args:
            x: X position in pixels.

        Returns:
            Snapped x position in pixels.
        """
        # Convert to grid units to check stage
        x_grid = self._grid.to_grid_units(x)
        stage = self._design.get_stage_at_x(x_grid)
        if stage:
            # Return stage position in pixels
            return self._grid.to_pixels(stage.x_position)
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _assign_register_to_stage(self, instance: ComponentInstance) -> None:
        """Assign a register instance to a stage, creating one if needed."""
        # Position is in grid units
        x_grid = instance.position[0]
        stage = self._design.get_stage_at_x(x_grid)

        if stage is None:
            # Width in grid units
            width_grid = self._grid.to_grid_units(self._register_width)
            stage = Stage(
                index=0,
                x_position=x_grid,
                width=width_grid,
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
        design's stages. Handles spacing differences by either:
        - Shifting main design stages/elements if composite needs more space
        - Stretching the composite if main design has larger stage spacing
        """
        if not self._library_loader:
            return

        composite_design = self._library_loader.get_composite_design(
            instance.definition_ref
        )
        if not composite_design:
            return

        # Get internal stage offsets (relative to component origin, in pixels)
        internal_stage_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_stage_offsets:
            # No internal stages - nothing to synchronize
            return

        # Instance position is in grid units, convert to pixels for scene operations
        drop_x_px = self._grid.to_pixels(instance.position[0])
        drop_y_px = self._grid.to_pixels(instance.position[1])

        # Calculate where the first internal stage would be at current position (in pixels)
        first_internal_offset = internal_stage_offsets[0]
        first_internal_stage_x = drop_x_px + first_internal_offset

        # Find the nearest main design stage to align with
        nearest_stage = self._find_nearest_stage(first_internal_stage_x)

        if nearest_stage is not None:
            # Align the composite so its first internal stage matches the main stage
            # Stage x_position is in grid units, convert to pixels
            stage_x_px = self._grid.to_pixels(nearest_stage.x_position)
            component_x_px = stage_x_px - first_internal_offset
            component_x_px = self._grid.snap_to_grid(component_x_px)

            instance.pipeline_stage = nearest_stage.index

            # Handle spacing differences for multi-stage composites
            if len(internal_stage_offsets) > 1:
                self._synchronize_stage_spacing(
                    composite_design, instance, component_x_px,
                    nearest_stage.index, internal_stage_offsets
                )

            # Update position - convert back to grid units for storage
            item = self._component_items.get(instance.id)
            if item:
                item.setPos(component_x_px, drop_y_px)
                instance.position = self._grid.pos_to_grid((component_x_px, drop_y_px))
        else:
            # No stages exist - create stages based on internal stage positions
            self._create_stages_from_composite(composite_design, instance, drop_x_px)

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

        # Get the origin offset (input_stage_x in grid units, convert to pixels)
        origin_x = self._grid.to_pixels(composite_design.visual.input_stage_x)

        # Calculate offset from component origin to each internal stage
        offsets = []
        for stage in sorted(composite_design.stages, key=lambda s: s.x_position):
            # Internal stage x_position is in grid units, convert to pixels
            stage_x_px = self._grid.to_pixels(stage.x_position)
            # Component origin is at input_stage_x in those coordinates
            offset = stage_x_px - origin_x
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
            # Convert stage position from grid units to pixels for comparison
            stage_x_px = self._grid.to_pixels(stage.x_position)
            distance = abs(stage_x_px - x)
            if distance < min_distance:
                min_distance = distance
                nearest = stage

        # Only return if within a reasonable snapping distance
        snap_threshold = self._grid.to_pixels(5)  # 5 grid units
        if min_distance <= snap_threshold:
            return nearest

        return None

    def _synchronize_stage_spacing(
        self,
        composite_design: Design,
        instance: ComponentInstance,
        component_x: float,
        first_stage_index: int,
        internal_offsets: list[float],
    ) -> None:
        """Synchronize stage spacing between composite and main design.

        Compares the composite's internal stage spacing with the main design's
        stage spacing and adjusts accordingly:
        - If composite needs more space: shift stages/elements to the right
        - If main design has more space: stretch the composite

        Args:
            composite_design: The composite component's internal design.
            instance: The composite component instance.
            component_x: The component's x position in pixels.
            first_stage_index: Index of the first aligned stage.
            internal_offsets: List of internal stage x offsets from component origin (in pixels).
        """
        if len(internal_offsets) < 2:
            return

        # Calculate internal stage spacing (distance between consecutive internal stages, in pixels)
        internal_spacings = []
        for i in range(1, len(internal_offsets)):
            spacing = internal_offsets[i] - internal_offsets[i - 1]
            internal_spacings.append(spacing)

        # Get main design stage spacing at the insertion point
        # Stage positions are in grid units, convert to pixels
        sorted_stages = sorted(self._design.stages, key=lambda s: s.x_position)
        main_spacings = []
        for i in range(1, len(sorted_stages)):
            spacing_px = self._grid.to_pixels(sorted_stages[i].x_position - sorted_stages[i - 1].x_position)
            main_spacings.append(spacing_px)

        if not main_spacings:
            # Only one stage exists, use default spacing
            main_spacing = self._grid.to_pixels(10)
        else:
            # Use average spacing or spacing at insertion point
            main_spacing = sum(main_spacings) / len(main_spacings)

        # Compare first internal spacing with main spacing
        first_internal_spacing = internal_spacings[0] if internal_spacings else 0

        if first_internal_spacing > main_spacing + self._grid.size:
            # Composite needs more space - shift stages and elements
            extra_space = first_internal_spacing - main_spacing
            self._shift_elements_right(component_x, first_stage_index, extra_space)

    def _shift_elements_right(
        self,
        from_x: float,
        from_stage_index: int,
        shift_amount: float,
    ) -> None:
        """Shift all stages and components to the right of a position.

        Args:
            from_x: X position from which to start shifting (in pixels).
            from_stage_index: Stage index from which to start shifting.
            shift_amount: Amount to shift in pixels.
        """
        shift_amount = self._grid.snap_to_grid(shift_amount)
        if shift_amount <= 0:
            return

        # Convert shift amount to grid units
        shift_grid = self._grid.to_grid_units(shift_amount)

        # Shift stages at or after from_stage_index (except the first aligned one)
        # Stage positions are in grid units
        for stage in self._design.stages:
            if stage.index > from_stage_index:
                stage.x_position += shift_grid

        # Shift components to the right of from_x
        for comp_id, item in self._component_items.items():
            comp_instance = item.get_instance()
            # Position is in grid units, convert to pixels for comparison
            comp_x_px = self._grid.to_pixels(comp_instance.position[0])

            # Get component width to check if it's fully to the right
            definition = self._library.get(comp_instance.definition_ref)
            if definition:
                comp_width, _ = definition.visual.get_pixel_size(self._grid)
            else:
                comp_width = self._grid.to_pixels(4)

            # Shift if component starts after from_x (with some tolerance)
            if comp_x_px > from_x + comp_width / 2:
                new_x_px = comp_x_px + shift_amount
                new_y_px = self._grid.to_pixels(comp_instance.position[1])
                # Convert back to grid units for storage
                comp_instance.position = self._grid.pos_to_grid((new_x_px, new_y_px))
                item.setPos(new_x_px, new_y_px)

        # Update connections
        self.update_connection_positions()

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
            component_x: The component's x position in pixels.
            first_stage_index: Index of the first aligned stage.
        """
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if len(internal_offsets) <= 1:
            return

        # For each additional internal stage, ensure a main design stage exists
        for i, offset in enumerate(internal_offsets[1:], start=1):
            target_x_px = component_x + offset
            target_stage_index = first_stage_index + i

            # Check if a stage exists at this index
            existing_stage = None
            for stage in self._design.stages:
                if stage.index == target_stage_index:
                    existing_stage = stage
                    break

            if existing_stage is None:
                # Get width from internal stage if available (already in grid units)
                internal_stage = composite_design.stages[i] if i < len(composite_design.stages) else None
                stage_width_grid = internal_stage.width if internal_stage else self._grid.to_grid_units(self._register_width)

                # Create new stage - convert x position to grid units
                target_x_grid = self._grid.to_grid_units(self._grid.snap_to_grid(target_x_px))
                new_stage = Stage(
                    index=target_stage_index,
                    x_position=target_x_grid,
                    width=stage_width_grid,
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
            drop_x: Where the component was dropped (in pixels).
        """
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_offsets:
            return

        # Snap the component position (in pixels)
        component_x_px = self._grid.snap_to_grid(drop_x)
        component_y_px = self._grid.to_pixels(instance.position[1])

        # Update component position - convert to grid units for storage
        item = self._component_items.get(instance.id)
        if item:
            item.setPos(component_x_px, component_y_px)
            instance.position = self._grid.pos_to_grid((component_x_px, component_y_px))

        # Create stages for each internal stage
        for i, offset in enumerate(internal_offsets):
            stage_x_px = component_x_px + offset
            stage_x_px = self._grid.snap_to_grid(stage_x_px)
            # Convert to grid units for storage
            stage_x_grid = self._grid.to_grid_units(stage_x_px)

            # Get width from internal stage (already in grid units)
            internal_stage = composite_design.stages[i] if i < len(composite_design.stages) else None
            stage_width_grid = internal_stage.width if internal_stage else self._grid.to_grid_units(self._register_width)

            new_stage = Stage(
                index=i,
                x_position=stage_x_grid,
                width=stage_width_grid,
                register_ids=[],
            )
            self._design.stages.append(new_stage)

        self._design.reindex_stages()
        instance.pipeline_stage = 0

    def _create_stage_item(self, stage: Stage) -> StageItem:
        """Create a graphics item for a stage."""
        item = StageItem(stage, view_height=10000.0, grid=self._grid)
        # Wire up stage group-move initiation
        item.on_stage_click = self._on_stage_click
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

    def _calculate_alignment_index(self, x_grid: float) -> int:
        """Calculate the alignment index for a component at position x.

        Alignment indices:
        - 0: Left of the first stage
        - 1: Between stage 1 and stage 2
        - 2: Between stage 2 and stage 3
        - ...
        - N: Right of stage N (where N is the last stage index)

        Args:
            x_grid: X position of the component in grid units.

        Returns:
            The alignment index (0 to number of stages).
        """
        if not self._design.stages:
            return 0

        sorted_stages = sorted(self._design.stages, key=lambda s: s.x_position)

        # Check each stage boundary (positions are in grid units)
        for i, stage in enumerate(sorted_stages):
            if x_grid < stage.x_position:
                return i

        # Component is to the right of all stages
        return len(sorted_stages)

    def _update_component_alignment(self, instance: ComponentInstance) -> None:
        """Update the alignment index for a single component.

        Args:
            instance: The component instance to update.
        """
        # Registers don't need alignment index - they define stages
        if instance.definition_ref == "Register":
            return

        instance.alignment_index = self._calculate_alignment_index(instance.position[0])

    def _update_all_component_alignments(self) -> None:
        """Update alignment indices for all non-register components."""
        for comp_id, item in self._component_items.items():
            instance = item.get_instance()
            if instance.definition_ref != "Register":
                instance.alignment_index = self._calculate_alignment_index(instance.position[0])

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
        # Convert position from grid units to pixels
        pos_px = self._grid.pos_to_pixels(instance.position)
        item.setPos(pos_px[0], pos_px[1])
        self.addItem(item)
        self._component_items[instance.id] = item

        # Connect callbacks for registers
        if instance.definition_ref == "Register":
            item.register_moved = self._on_register_moved
            item.snap_register_x = self.snap_register_x
            item.check_distance_conflicts = self._check_distance_conflicts
            item.clear_distance_conflicts = self._clear_distance_conflicts
        elif instance.is_composite:
            # Composites snap TO stages (their internal stages align with main
            # design stages) and get a drag-update callback for visual feedback.
            # They must NOT get avoid_stage_overlap – that fights alignment.
            internal_offsets = self._get_composite_internal_offsets_for(instance)
            item.snap_composite_x = lambda x, off=internal_offsets: (
                self._snap_composite_x(x, off)
            )
            item.on_composite_drag_update = self._on_composite_drag_update
        else:
            # Plain operation components: no hard position constraint.
            # invalid placements are shown visually (see _on_composite_drag_update
            # pattern), not enforced by blocking movement.
            item.avoid_stage_overlap = self._avoid_stage_overlap

        # Restore temporary visual state if the instance was marked temporary
        #if instance.is_position_temporary:
        #    item.set_temporary(True)

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
        """Handle mouse move for connection dragging and stage group-moves."""
        # ── Stage group-move takes priority ──────────────────────────────────
        if self._moving_stage is not None:
            self._update_stage_group_move(event.scenePos())
            self.update_connection_positions()
            return  # Do not deliver to items – prevents accidental other drags

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
        """Handle mouse release for connection creation and stage group-moves."""
        # ── Commit stage group-move ───────────────────────────────────────────
        if self._moving_stage is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._commit_stage_group_move()
            return

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
        """Handle a register being moved.

        Args:
            instance: The register instance that was moved.
            old_x: Previous x position in grid units.
        """
        # During a stage group-move the scene handles stage reassignment in bulk
        # at commit time, so individual callbacks must be suppressed here.
        if self._in_stage_group_move:
            return

        # Position is now in grid units
        new_x_grid = instance.position[0]

        for stage in self._design.stages:
            if instance.id in stage.register_ids:
                stage.register_ids.remove(instance.id)
                break

        existing_stage = self._design.get_stage_at_x(new_x_grid)

        if existing_stage is not None:
            if instance.id not in existing_stage.register_ids:
                existing_stage.register_ids.append(instance.id)
        else:
            # Width in grid units
            width_grid = self._grid.to_grid_units(self._register_width)
            new_stage = Stage(
                index=0,
                x_position=new_x_grid,
                width=width_grid,
                register_ids=[instance.id],
            )
            self._design.stages.append(new_stage)

        self._design.remove_empty_stages()
        self._design.reindex_stages()
        self._update_all_pipeline_stages()
        self._rebuild_all_stages()
        self._update_register_displays()
        self._update_all_component_alignments()
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

        # For composites: release stage bindings and collapse additional_offset
        if instance.is_composite:
            self._release_composite_stage_bindings(component_id)

        self.removeItem(item)
        del self._component_items[component_id]
        self._design.remove_component(component_id)

        # Remove any temporary overlay for this instance
        overlay = self._temp_position_overlays.pop(component_id, None)
        if overlay and overlay.scene():
            self.removeItem(overlay)

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
        """Internal method to move a component (used by undo/redo).

        Args:
            component_id: ID of the component to move.
            pos: New position in grid units.

        Returns:
            True if the component was moved, False otherwise.
        """
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        old_x = instance.position[0]
        is_register = instance.definition_ref == "Register"

        # Update position (stored in grid units)
        instance.position = pos
        # Convert to pixels for Qt scene
        pos_px = self._grid.pos_to_pixels(pos)
        item.setPos(pos_px[0], pos_px[1])

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

    def _avoid_stage_overlap(self, x: float, width: float, _depth: int = 0) -> float:
        """Adjust x position to avoid overlapping with register stages.

        Maintains a minimum distance of 2 grid units from any stage.

        Args:
            x: Proposed x position of the component in pixels.
            width: Width of the component in pixels.
            _depth: Recursion depth (internal use).

        Returns:
            Adjusted x position in pixels that avoids stage overlap.
        """
        if not self._design.stages or _depth > 10:
            return x

        min_gap = self._grid.to_pixels(2)  # 2 grid units minimum distance
        comp_left = x
        comp_right = x + width

        # Sort stages by position for consistent behavior
        sorted_stages = sorted(self._design.stages, key=lambda s: s.x_position)

        # Check each stage for potential overlap
        for stage in sorted_stages:
            # Convert stage position from grid units to pixels
            stage_left = self._grid.to_pixels(stage.x_position)
            stage_right = self._grid.to_pixels(stage.x_position + stage.width)

            # Check if component overlaps with stage (including gap)
            if comp_left < stage_right + min_gap and comp_right > stage_left - min_gap:
                # Determine which side to push the component
                # Prefer the direction with more space
                dist_to_left = comp_right - (stage_left - min_gap)
                dist_to_right = (stage_right + min_gap) - comp_left

                if dist_to_left <= dist_to_right:
                    # Push component to the left of the stage
                    new_x = stage_left - min_gap - width
                else:
                    # Push component to the right of the stage
                    new_x = stage_right + min_gap

                # Snap to grid
                new_x = self._grid.snap_to_grid(new_x)

                # Recursively check if new position overlaps with other stages
                return self._avoid_stage_overlap(new_x, width, _depth + 1)

        return x

    def snap_register_x(self, x: float) -> float:
        """Snap x coordinate for a register (stage-aware).

        During a stage group-move the normal stage-snapping is bypassed so
        registers can be repositioned freely to their new group position.

        Args:
            x: X position in pixels.

        Returns:
            Snapped x position in pixels.
        """
        if self._in_stage_group_move:
            return self._grid.snap_to_grid(x) if self._snap_to_grid else x

        x_grid = self._grid.to_grid_units(x)
        stage = self._design.get_stage_at_x(x_grid)
        if stage:
            return self._grid.to_pixels(stage.x_position)
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _get_composite_internal_offsets_for(
        self, instance: ComponentInstance
    ) -> list[float]:
        """Return the internal stage offsets (pixels from left edge) for *instance*.

        Returns an empty list when the composite design is unavailable.
        """
        if not self._library_loader:
            return []
        composite_design = self._library_loader.get_composite_design(
            instance.definition_ref
        )
        if not composite_design:
            return []
        return self._get_composite_internal_stage_offsets(composite_design)

    def _snap_composite_x(self, x_px: float, internal_offsets: list[float]) -> float:
        """Snap a composite's left-edge pixel x so its first internal stage
        aligns with the nearest main-design stage.

        Falls back to plain grid snap when no stage is close enough.

        Args:
            x_px:             Proposed left-edge position in pixels.
            internal_offsets: Pixel offsets of each internal stage from the
                              composite's left edge (from
                              _get_composite_internal_stage_offsets).

        Returns:
            Adjusted x position in pixels.
        """
        if not internal_offsets:
            return self._grid.snap_to_grid(x_px) if self._snap_to_grid else x_px

        first_world_x = x_px + internal_offsets[0]
        nearest = self._find_nearest_stage_wide(first_world_x)
        if nearest is not None:
            # Snap the composite so its first internal stage sits exactly on
            # the nearest main stage.
            stage_x_px = self._grid.to_pixels(nearest.x_position)
            return self._grid.snap_to_grid(stage_x_px - internal_offsets[0])

        return self._grid.snap_to_grid(x_px) if self._snap_to_grid else x_px

    def _check_distance_conflicts(self, register_x: float, aligned_stage_index: int | None) -> None:
        """Check for distance conflicts during register/stage movement.

        Makes conflicting components semi-transparent (alpha 0.5).
        Excludes the register's aligned stage from conflict checking.

        Args:
            register_x: Current x position of the register being moved (in pixels).
            aligned_stage_index: Index of the stage this register is aligned to (excluded from check).
        """
        min_gap = self._grid.to_pixels(2)  # 2 grid units minimum distance

        # Convert register_x to grid units for stage lookup
        register_x_grid = self._grid.to_grid_units(register_x)

        # Get the stage at the register's position (or where it will create one)
        stage_at_x = self._design.get_stage_at_x(register_x_grid)
        if stage_at_x:
            # Convert stage positions to pixels for comparison
            stage_left = self._grid.to_pixels(stage_at_x.x_position)
            stage_right = self._grid.to_pixels(stage_at_x.x_position + stage_at_x.width)
        else:
            # Register will create a new stage here
            stage_left = register_x
            stage_right = register_x + self._register_width

        # Check all non-register components for conflicts
        for comp_id, item in self._component_items.items():
            instance = item.get_instance()

            # Skip registers - they can overlap with stages
            if instance.definition_ref == "Register":
                continue

            definition = self._library.get(instance.definition_ref)
            if definition:
                comp_width, _ = definition.visual.get_pixel_size(self._grid)
            else:
                comp_width = self._grid.to_pixels(4)

            # Convert component position from grid units to pixels
            comp_left = self._grid.to_pixels(instance.position[0])
            comp_right = comp_left + comp_width

            # Check if component is too close to the stage
            is_conflict = (
                comp_left < stage_right + min_gap and
                comp_right > stage_left - min_gap
            )

            if is_conflict:
                if comp_id not in self._conflict_items:
                    self._conflict_items.add(comp_id)
                    item.setOpacity(0.5)
            else:
                if comp_id in self._conflict_items:
                    self._conflict_items.discard(comp_id)
                    item.setOpacity(1.0)

    def _clear_distance_conflicts(self) -> None:
        """Clear all distance conflict highlighting.

        Restores opacity to 1.0 for all previously conflicting items.
        """
        for comp_id in list(self._conflict_items):
            item = self._component_items.get(comp_id)
            if item:
                item.setOpacity(1.0)
        self._conflict_items.clear()

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
            instance = item.get_instance()

            # For composites: apply proposed stage shifts before recording
            if instance.is_composite and component_id == self._dragging_composite_id:
                self._commit_composite_drag_alignment(instance)

            command = MoveComponentCommand(
                scene=self,
                component_id=component_id,
                old_pos=old_pos,
                new_pos=new_pos,
            )
            # Don't execute - movement already happened, just record for undo
            self._undo_stack._undo_stack.append(command)
            self._undo_stack._redo_stack.clear()

            # Update alignment index for the moved component
            self._update_component_alignment(instance)

    # ══════════════════════════════════════════════════════════════════════════
    # Stage group-move  (Priority 1 & 3)
    # ══════════════════════════════════════════════════════════════════════════

    def _on_stage_click(self, stage: Stage, scene_pos: QPointF) -> None:
        """Called by StageItem when the user presses on a stage band.

        Initiates a group-move of every register in that stage.
        """
        self._moving_stage = stage
        self._stage_move_mouse_start = scene_pos
        self._stage_move_stage_start_x = stage.x_position
        self._stage_move_new_x = stage.x_position

        # Gather all register ComponentItems that belong to this stage
        self._stage_group_items = []
        self._stage_group_original_positions = {}
        for reg_id in stage.register_ids:
            item = self._component_items.get(reg_id)
            if item is not None:
                self._stage_group_items.append(item)
                self._stage_group_original_positions[reg_id] = item.get_instance().position

        if not self._stage_group_items:
            # Nothing to move – clear immediately
            self._moving_stage = None
            return

        # Suppress per-register callbacks during the group move
        self._in_stage_group_move = True

        # Visual feedback: mark stage as being moved
        stage_item = self._stage_items.get(stage.id)
        if stage_item:
            stage_item.set_being_moved(True)

        # Disable movement on all other non-group items so nothing else drifts
        self._set_non_group_items_movable(False)

    def _update_stage_group_move(self, scene_pos: QPointF) -> None:
        """Update register positions during a stage group-drag.

        Also enforces the left-side constraint from sub-component bindings
        and shows the red invalid-overlap indicator when needed.
        """
        if self._moving_stage is None:
            return

        stage = self._moving_stage
        delta_px = scene_pos.x() - self._stage_move_mouse_start.x()
        delta_grid = self._grid.to_grid_units(delta_px)
        proposed_x = self._grid.snap_to_grid_units(
            self._stage_move_stage_start_x + delta_grid
        )

        # ── Priority 3: left constraint ──────────────────────────────────────
        left_bound = self._get_stage_left_bound(stage)
        proposed_x = max(proposed_x, left_bound)

        # ── Auto-snap to perfect alignment with bound composites ─────────────
        snap_x = self._get_stage_snap_x(stage, proposed_x)
        if snap_x is not None:
            proposed_x = snap_x

        self._stage_move_new_x = proposed_x
        delta_applied = proposed_x - self._stage_move_stage_start_x

        # Move each register item programmatically (no callbacks)
        for item in self._stage_group_items:
            orig = self._stage_group_original_positions[item.get_instance().id]
            new_x_grid = orig[0] + delta_applied
            new_x_px = self._grid.to_pixels(new_x_grid)
            new_y_px = self._grid.to_pixels(orig[1])
            item.set_position_no_callbacks(new_x_px, new_y_px)

        # ── Update the ghost stage position for overlap check ────────────────
        # (move the actual model x temporarily so the stage item repositions)
        stage.x_position = proposed_x
        stage_item = self._stage_items.get(stage.id)
        if stage_item:
            stage_item.update_stage(stage)

        # ── Overlap detection ────────────────────────────────────────────────
        is_invalid = self._check_stage_group_overlaps(stage, proposed_x)
        if stage_item:
            stage_item.set_invalid_overlap(is_invalid)

    def _commit_stage_group_move(self) -> None:
        """Commit the current stage group-move and record an undo command."""
        if self._moving_stage is None:
            return

        stage = self._moving_stage
        new_x = self._stage_move_new_x
        old_x = self._stage_move_stage_start_x
        delta_grid = new_x - old_x

        # Build new register positions
        new_reg_positions: dict[UUID, tuple[float, float]] = {}
        for item in self._stage_group_items:
            inst = item.get_instance()
            orig = self._stage_group_original_positions[inst.id]
            new_reg_positions[inst.id] = (orig[0] + delta_grid, orig[1])

        # Composite offsets: adjust stage_position_offset for bound composites
        old_composite_offsets: dict[UUID, float] = {}
        new_composite_offsets: dict[UUID, float] = {}
        for comp_id, binding in self._composite_stage_bindings.items():
            if stage.id in binding.values():
                inst = self._design.get_component_by_id(comp_id)
                if inst:
                    old_composite_offsets[comp_id] = inst.stage_position_offset
                    new_composite_offsets[comp_id] = inst.stage_position_offset + delta_grid

        # Record undo command (state already applied visually)
        command = MoveStageCommand(
            scene=self,
            stage_id=stage.id,
            old_stage_x=old_x,
            new_stage_x=new_x,
            old_additional_offset=stage.additional_offset,
            new_additional_offset=max(0.0, stage.additional_offset + delta_grid),
            old_reg_positions={str(k): v for k, v in self._stage_group_original_positions.items()},
            new_reg_positions={str(k): v for k, v in new_reg_positions.items()},
            old_composite_offsets={str(k): v for k, v in old_composite_offsets.items()},
            new_composite_offsets={str(k): v for k, v in new_composite_offsets.items()},
        )
        self._undo_stack._undo_stack.append(command)
        self._undo_stack._redo_stack.clear()

        # Apply model updates
        stage.x_position = new_x
        stage.additional_offset = max(0.0, stage.additional_offset + delta_grid)

        for inst_id, pos in new_reg_positions.items():
            inst = self._design.get_component_by_id(inst_id)
            if inst:
                inst.position = pos

        for inst_id, offset in new_composite_offsets.items():
            inst = self._design.get_component_by_id(inst_id)
            if inst:
                inst.stage_position_offset = offset

        # Rebuild and update
        self._design.reindex_stages()
        self._rebuild_all_stages()
        self._update_register_displays()
        self._update_all_component_alignments()
        self._apply_stage_position_offsets()
        self.update_connection_positions()
        self.stages_changed.emit()

        self._end_stage_group_move()

    def _end_stage_group_move(self) -> None:
        """Clean up after a stage group-move (commit or cancel)."""
        stage = self._moving_stage
        if stage:
            stage_item = self._stage_items.get(stage.id)
            if stage_item:
                stage_item.set_being_moved(False)
                stage_item.set_invalid_overlap(False)

        self._moving_stage = None
        self._stage_group_items = []
        self._stage_group_original_positions = {}
        self._in_stage_group_move = False
        self._set_non_group_items_movable(True)

    def _get_stage_left_bound(self, stage: Stage) -> float:
        """Return the minimum x_position (grid units) for *stage*.

        The constraint only applies when at least one composite is bound to this
        stage.  In that case the stage cannot move further left than the position
        where its ``additional_offset`` would reach zero (i.e. where the
        composite's internal stage would land exactly on the main stage without
        any extra space).

        When no composite is bound the stage is free to move left, so we return
        negative infinity (no design-imposed constraint).
        """
        has_binding = any(
            stage.id in binding.values()
            for binding in self._composite_stage_bindings.values()
        )
        if not has_binding:
            return float("-inf")
        # Composite bound: can slide left until additional_offset reaches 0
        return stage.x_position - stage.additional_offset

    def _get_stage_snap_x(self, stage: Stage, proposed_x: float) -> float | None:
        """Return the snap x (grid units) when *proposed_x* is near a natural
        alignment point for a bound composite, otherwise None.

        The 'natural' position is where additional_offset would become exactly 0.
        """
        has_binding = any(
            stage.id in binding.values()
            for binding in self._composite_stage_bindings.values()
        )
        if not has_binding:
            return None
        snap_threshold = 1.5  # grid units
        natural_x = stage.x_position - stage.additional_offset
        if abs(proposed_x - natural_x) <= snap_threshold:
            return natural_x
        return None

    def _check_stage_group_overlaps(self, moving_stage: Stage, proposed_x: float) -> bool:
        """Return True if *moving_stage* at *proposed_x* overlaps any other stage."""
        width = moving_stage.width
        for other in self._design.stages:
            if other.id == moving_stage.id:
                continue
            if other.overlaps(proposed_x, width):
                return True
        return False

    def _set_non_group_items_movable(self, movable: bool) -> None:
        """Enable/disable ItemIsMovable on all items not in the current group."""
        group_ids = {item.get_instance().id for item in self._stage_group_items}
        for comp_id, item in self._component_items.items():
            if comp_id not in group_ids:
                item.setFlag(
                    item.GraphicsItemFlag.ItemIsMovable, movable
                )

    def _move_register_direct(self, inst_id: UUID, pos: tuple[float, float]) -> None:
        """Move a register item directly (used by MoveStageCommand undo/redo)."""
        item = self._component_items.get(inst_id)
        inst = self._design.get_component_by_id(inst_id)
        if item and inst:
            inst.position = pos
            x_px, y_px = self._grid.pos_to_pixels(pos)
            item.set_position_no_callbacks(x_px, y_px)

    # ══════════════════════════════════════════════════════════════════════════
    # Composite drag alignment  (Priority 2)
    # ══════════════════════════════════════════════════════════════════════════

    def _on_composite_drag_update(
        self, instance: ComponentInstance, new_pos: QPointF
    ) -> None:
        """Called each pixel-move while dragging a composite component.

        - Computes proposed stage shifts for multi-stage spacing conflicts.
        - Shows an overlay that reflects whether the current position is
          aligned (orange border + label) or not (red border + label).
        - The overlay is always shown while dragging so the user has continuous
          visual feedback.
        """
        self._dragging_composite_id = instance.id
        self._clear_composite_drag_previews()
        self._composite_proposed_shifts.clear()

        comp_x_px = new_pos.x()

        # Compute internal stage offsets (needed for both snap and feedback)
        internal_offsets = self._get_composite_internal_offsets_for(instance)

        item = self._component_items.get(instance.id)
        if item is None:
            return

        # ── Determine alignment status ───────────────────────────────────────
        is_aligned = False
        aligned_label = "Not aligned to any stage"
        nearest: Stage | None = None

        if internal_offsets and self._design.stages:
            first_world_x = comp_x_px + internal_offsets[0]
            nearest = self._find_nearest_stage_wide(first_world_x)
            if nearest is not None:
                is_aligned = True
                aligned_label = f"Aligned → Stage {nearest.index}"

                # For multi-stage composites check if internal spacing forces
                # later stages to shift right.
                if len(internal_offsets) > 1:
                    by_index = {
                        s.index: s
                        for s in sorted(self._design.stages, key=lambda s: s.x_position)
                    }
                    for i, int_off in enumerate(internal_offsets[1:], start=1):
                        target_idx = nearest.index + i
                        main_st = by_index.get(target_idx)
                        if main_st is None:
                            continue
                        internal_world_x = comp_x_px + int_off
                        main_x_px = self._grid.to_pixels(main_st.x_position)
                        needed_shift_px = internal_world_x - main_x_px
                        if needed_shift_px > self._grid.size:
                            shift_grid = self._grid.to_grid_units(needed_shift_px)
                            self._composite_proposed_shifts[main_st.id] = shift_grid
                            aligned_label += " (stages will shift →)"

        elif not self._design.stages:
            # No stages yet – composite will create them on drop
            is_aligned = True
            aligned_label = "Will create stages on drop"

        self._last_drag_was_aligned = is_aligned
        item.set_invalid(not is_aligned)

        # ── Show overlay ─────────────────────────────────────────────────────
        rect = item.sceneBoundingRect().adjusted(-4, -4, 4, 4)
        overlay = TempPositionOverlayItem(
            rect,
            label=aligned_label,
            invalid=not is_aligned,
        )
        self.addItem(overlay)
        self._composite_drag_overlays.append(overlay)

    def _commit_composite_drag_alignment(self, instance: ComponentInstance) -> None:
        """Apply proposed stage shifts after a composite is dropped.

        - Shifts main design stages right (never left) to fit the composite.
        - Marks displaced non-register elements as temporary.
        - Stores the stage binding for constraint calculations.
        - Clears drag previews and shows accepted-temporary overlays.
        """
        if not self._library_loader:
            self._clear_composite_drag_previews()
            self._dragging_composite_id = None
            return

        composite_design = self._library_loader.get_composite_design(
            instance.definition_ref
        )
        if not composite_design:
            self._clear_composite_drag_previews()
            self._dragging_composite_id = None
            return

        # Apply each proposed shift
        for stage_id, shift_grid in self._composite_proposed_shifts.items():
            if shift_grid > 0:
                self._shift_stage_right_permanent(stage_id, shift_grid, instance.id)

        # Build and store the binding: internal stage idx → main stage id
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        binding: dict[int, UUID] = {}
        item = self._component_items.get(instance.id)
        if item and internal_offsets:
            comp_x_px = item.pos().x()
            for i, off in enumerate(internal_offsets):
                world_x = comp_x_px + off
                nearest = self._find_nearest_stage_wide(world_x)
                if nearest:
                    binding[i] = nearest.id
        if binding:
            self._composite_stage_bindings[instance.id] = binding

        # Show accepted-temporary overlay on the composite itself
        self._add_temp_position_overlay(instance.id)

        # Clear the item's own invalid border — the overlay now conveys the state
        comp_item = self._component_items.get(instance.id)
        if comp_item:
            comp_item.set_invalid(False)

        # Clear drag-time previews
        self._clear_composite_drag_previews()
        self._composite_proposed_shifts.clear()
        self._dragging_composite_id = None

        self._apply_stage_position_offsets()
        self.update_connection_positions()

    def _shift_stage_right_permanent(
        self, stage_id: UUID, shift_grid: float, source_composite_id: UUID
    ) -> None:
        """Shift a stage and all elements to its right by *shift_grid* units.

        Updates ``stage.additional_offset``, displaces non-register components
        sitting to the right of the stage, and marks them temporary.
        """
        stage = self._design.get_stage_by_id(stage_id)
        if stage is None or shift_grid <= 0:
            return

        stage.x_position += shift_grid
        stage.additional_offset += shift_grid

        # Shift subsequent stages and non-register components
        from_x_grid = stage.x_position - shift_grid  # original x
        for other_stage in self._design.stages:
            if other_stage.id != stage_id and other_stage.x_position > from_x_grid:
                other_stage.x_position += shift_grid
                other_stage.additional_offset += shift_grid

        # Non-register instances to the right of the original stage position
        for comp_id, comp_item in self._component_items.items():
            inst = comp_item.get_instance()
            if inst.definition_ref == "Register":
                continue
            if inst.position[0] > from_x_grid:
                inst.stage_position_offset += shift_grid
                inst.is_position_temporary = True
                comp_item.set_temporary(True)

        self._design.reindex_stages()
        self._rebuild_all_stages()

    def _release_composite_stage_bindings(self, composite_id: UUID) -> None:
        """Release stage bindings when a composite is removed.

        Checks each bound stage and, if no other composite still binds it,
        collapses ``additional_offset`` to zero and repositions the design.
        """
        binding = self._composite_stage_bindings.pop(composite_id, None)
        if not binding:
            return

        for stage_id in binding.values():
            stage = self._design.get_stage_by_id(stage_id)
            if stage is None or stage.additional_offset <= 0:
                continue

            # Check if any remaining composite still binds this stage
            still_needed = any(
                stage_id in b.values()
                for other_id, b in self._composite_stage_bindings.items()
                if other_id != composite_id
            )
            if still_needed:
                continue

            # Collapse the extra offset: move stage left by additional_offset
            collapse = stage.additional_offset
            stage.x_position -= collapse
            stage.additional_offset = 0.0

            # Adjust non-register instances that carried this offset
            for comp_id, comp_item in self._component_items.items():
                inst = comp_item.get_instance()
                if inst.definition_ref == "Register":
                    continue
                if inst.stage_position_offset > 0:
                    inst.stage_position_offset = max(
                        0.0, inst.stage_position_offset - collapse
                    )
                    if inst.stage_position_offset == 0.0:
                        inst.is_position_temporary = False
                        comp_item.set_temporary(False)

        self._design.reindex_stages()
        self._rebuild_all_stages()
        self._apply_stage_position_offsets()
        self.update_connection_positions()

    def _clear_composite_drag_previews(self) -> None:
        """Remove all temporary overlay items shown during composite drag."""
        self._remove_overlays_safe(self._composite_drag_overlays)
        self._composite_drag_overlays.clear()

    def _find_nearest_stage_wide(self, x_px: float) -> Stage | None:
        """Find the nearest stage within a wider snap threshold (used during drag).

        Args:
            x_px: X position in pixels.

        Returns:
            Nearest Stage within 8 grid units, or None.
        """
        if not self._design.stages:
            return None
        threshold = self._grid.to_pixels(8)
        nearest = None
        min_dist = float("inf")
        for stage in self._design.stages:
            dist = abs(self._grid.to_pixels(stage.x_position) - x_px)
            if dist < min_dist:
                min_dist = dist
                nearest = stage
        return nearest if min_dist <= threshold else None

    # ══════════════════════════════════════════════════════════════════════════
    # Temporary-position helpers  (Priority 2 & 3)
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_stage_position_offsets(self) -> None:
        """Re-render every non-register instance using its effective position.

        Effective X = instance.position[0] + instance.stage_position_offset
        """
        for comp_id, item in self._component_items.items():
            inst = item.get_instance()
            if inst.definition_ref == "Register":
                continue
            if inst.stage_position_offset == 0.0:
                continue
            eff_x = inst.position[0] + inst.stage_position_offset
            x_px = self._grid.to_pixels(eff_x)
            y_px = self._grid.to_pixels(inst.position[1])
            item.set_position_no_callbacks(x_px, y_px)

    def _add_temp_position_overlay(self, component_id: UUID) -> None:
        """Add (or refresh) an accepted-temporary overlay around a component."""
        item = self._component_items.get(component_id)
        if item is None:
            return
        # Remove old overlay if any
        old = self._temp_position_overlays.pop(component_id, None)
        if old:
            self._remove_overlays_safe([old])
        rect = item.sceneBoundingRect().adjusted(-4, -4, 4, 4)
        if self._last_drag_was_aligned:
            overlay = TempPositionOverlayItem(rect, "")
        else:
            overlay = TempPositionOverlayItem(
                rect,
                "Invalid location – correct it for consistency!",
                invalid=True,
            )
        self.addItem(overlay)
        self._temp_position_overlays[component_id] = overlay

    def finalize_temporary_positions(self) -> None:
        """Accept all provisional positions, clearing the temporary visual state.

        Call this from a toolbar button or context-menu action after the user
        is satisfied with the current layout.
        """
        for comp_id, overlay in list(self._temp_position_overlays.items()):
            self._remove_overlays_safe([overlay])
            item = self._component_items.get(comp_id)
            if item:
                inst = item.get_instance()
                inst.is_position_temporary = False
                # Absorb the offset into the base position so the model is clean
                if inst.stage_position_offset != 0.0:
                    inst.position = (
                        inst.position[0] + inst.stage_position_offset,
                        inst.position[1],
                    )
                    inst.stage_position_offset = 0.0
                item.set_temporary(False)
        self._temp_position_overlays.clear()

    # ══════════════════════════════════════════════════════════════════════════
    # Shared reset helper
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_alignment_state(self) -> None:
        """Clear all alignment-related runtime state (used on design load/new).

        Must be called BEFORE QGraphicsScene.clear() so that the Qt C++ objects
        backing the overlay items are still alive when we call removeItem / scene().
        """
        self._moving_stage = None
        self._stage_move_mouse_start = QPointF()
        self._stage_move_stage_start_x = 0.0
        self._stage_move_new_x = 0.0
        self._stage_group_items = []
        self._stage_group_original_positions = {}
        self._in_stage_group_move = False

        self._remove_overlays_safe(self._composite_drag_overlays)
        self._composite_drag_overlays = []

        self._remove_overlays_safe(list(self._temp_position_overlays.values()))
        self._temp_position_overlays = {}

        self._composite_proposed_shifts = {}
        self._composite_stage_bindings = {}
        self._dragging_composite_id = None
        self._last_drag_was_aligned = True

    def _remove_overlays_safe(self, overlays: list) -> None:
        """Remove overlay items from the scene, tolerating already-deleted objects."""
        for overlay in overlays:
            try:
                if overlay.scene() is not None:
                    self.removeItem(overlay)
            except RuntimeError:
                # C++ object was already deleted (e.g. after scene.clear()) – ignore.
                pass
