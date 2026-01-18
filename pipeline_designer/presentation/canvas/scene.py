"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsSceneMouseEvent

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Connection,
    Design,
    PortReference,
    Stage,
)

from .items import ComponentItem, ConnectionItem, StageItem, TempConnectionItem
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
        self._component_items: dict[UUID, ComponentItem] = {}
        self._stage_items: dict[UUID, StageItem] = {}
        self._connection_items: dict[UUID, ConnectionItem] = {}
        self._snap_to_grid = True
        self._register_width: float = 80.0  # Default, updated from library

        # Connection creation state
        self._temp_connection: TempConnectionItem | None = None
        self._connection_source_port: PortItem | None = None
        self._connection_source_component_id: UUID | None = None

        self._setup_scene()

    def _setup_scene(self) -> None:
        """Configure scene settings."""
        self.setSceneRect(QRectF(-5000, -5000, 10000, 10000))
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))

    @property
    def grid(self) -> GridConfig:
        """Get the grid configuration."""
        return self._grid

    def set_library(self, library: dict[str, ComponentDefinition]) -> None:
        """Set the component library."""
        self._library = library
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
        self._design = design

        # Create stage items first (they're behind components)
        for stage in design.stages:
            self._create_stage_item(stage)

        # Create component items
        for instance in design.components:
            self._create_component_item(instance)

        # Create connection items
        for connection in design.connections:
            self._create_connection_item(connection)

    def new_design(self) -> None:
        """Create a new empty design."""
        self.clear()
        self._component_items.clear()
        self._stage_items.clear()
        self._connection_items.clear()
        self._design = Design()

    def add_component_at(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Add a component instance at the specified position.

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

        # Snap y to grid
        if self._snap_to_grid:
            y = self._grid.snap_to_grid(y)

        # For registers, handle stage-aware x positioning
        if component_name == "Register":
            x = self._get_register_x_position(x)
        elif self._snap_to_grid:
            x = self._grid.snap_to_grid(x)

        instance = ComponentInstance(
            definition_ref=component_name,
            position=(x, y),
        )

        self._design.add_component(instance)
        item = self._create_component_item(instance)

        # Handle stage assignment for registers
        if component_name == "Register":
            self._assign_register_to_stage(instance)

        self.component_added.emit(instance)
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
        item = ComponentItem(
            instance,
            definition,
            grid=self._grid,
            snap_to_grid=self._snap_to_grid,
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

        # Disable movement on all components during connection
        self._set_components_movable(False)

        # Create temporary connection line
        start_pos = port_item.scenePos()
        self._temp_connection = TempConnectionItem(start_pos)
        self.addItem(self._temp_connection)

    def _is_valid_connection_target(self, target_port: PortItem) -> bool:
        """Check if a port is a valid connection target."""
        if self._connection_source_port is None:
            return False

        # Must be an input port
        if not target_port.is_input():
            return False

        # Cannot connect to same component
        source_comp_id = self._connection_source_component_id
        target_comp_id = target_port.get_component_id()
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

        return True

    def _create_connection(
        self,
        source_port: PortItem,
        source_comp_id: UUID,
        target_port: PortItem,
    ) -> None:
        """Create a new connection between ports."""
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

        self._design.add_connection(connection)
        self._create_connection_item(connection)
        self.connection_added.emit(connection)

    def _create_connection_item(self, connection: Connection) -> ConnectionItem | None:
        """Create a graphics item for a connection."""
        # Get source and target positions
        source_pos = self._get_port_position(
            connection.source.component_id,
            connection.source.port_name,
        )
        target_pos = self._get_port_position(
            connection.target.component_id,
            connection.target.port_name,
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

    def _get_port_position(self, component_id: UUID, port_name: str) -> tuple[float, float] | None:
        """Get the scene position of a port."""
        comp_item = self._component_items.get(component_id)
        if comp_item is None:
            return None
        return comp_item.get_port_scene_pos(port_name)

    def _cancel_connection(self) -> None:
        """Cancel the current connection creation."""
        if self._temp_connection:
            self.removeItem(self._temp_connection)
            self._temp_connection = None
        self._connection_source_port = None
        self._connection_source_component_id = None

        # Re-enable movement on all components
        self._set_components_movable(True)

        # Reset any highlighted ports
        for comp_item in self._component_items.values():
            for port_item in comp_item._port_items.values():
                port_item.set_connection_target(False)

    def _set_components_movable(self, movable: bool) -> None:
        """Enable or disable movement on all component items."""
        from PySide6.QtWidgets import QGraphicsItem
        for comp_item in self._component_items.values():
            comp_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, movable)

    def remove_connection(self, connection_id: UUID) -> bool:
        """Remove a connection from the scene."""
        item = self._connection_items.get(connection_id)
        if item is None:
            return False

        self.removeItem(item)
        del self._connection_items[connection_id]
        self._design.remove_connection(connection_id)
        self.connection_removed.emit(connection_id)
        return True

    def update_connection_positions(self) -> None:
        """Update all connection positions after components move."""
        for conn_id, conn_item in self._connection_items.items():
            conn = conn_item.get_connection()
            source_pos = self._get_port_position(
                conn.source.component_id,
                conn.source.port_name,
            )
            target_pos = self._get_port_position(
                conn.target.component_id,
                conn.target.port_name,
            )
            if source_pos and target_pos:
                conn_item.update_positions(
                    QPointF(source_pos[0], source_pos[1]),
                    QPointF(target_pos[0], target_pos[1]),
                )

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse move for connection dragging."""
        super().mouseMoveEvent(event)

        # Update temporary connection line
        if self._temp_connection:
            self._temp_connection.set_end_pos(event.scenePos())

            # Check if we're over a valid target port
            items = self.items(event.scenePos())
            target_port = None
            for item in items:
                if isinstance(item, PortItem) and item.is_input():
                    target_port = item
                    break

            # Update port highlighting
            for comp_item in self._component_items.values():
                for port_item in comp_item._port_items.values():
                    if port_item == target_port:
                        is_valid = self._is_valid_connection_target(port_item)
                        port_item.set_connection_target(True, is_valid)
                        self._temp_connection.set_target_state(True, is_valid)
                    else:
                        port_item.set_connection_target(False)

            if target_port is None:
                self._temp_connection.set_target_state(False)

        # Update connection positions when components are being moved
        self.update_connection_positions()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse release for connection creation."""
        if self._temp_connection and self._connection_source_port:
            # Check if we released over a valid input port
            items = self.items(event.scenePos())
            for item in items:
                if isinstance(item, PortItem) and item.is_input():
                    if self._is_valid_connection_target(item):
                        self._create_connection(
                            self._connection_source_port,
                            self._connection_source_component_id,
                            item,
                        )
                        self._cancel_connection()
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
        """Remove a component instance from the scene."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        is_register = instance.definition_ref == "Register"

        # Remove connections involving this component
        conns_to_remove = [
            conn.id for conn in self._design.connections
            if conn.source.component_id == component_id or conn.target.component_id == component_id
        ]
        for conn_id in conns_to_remove:
            self.remove_connection(conn_id)

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
