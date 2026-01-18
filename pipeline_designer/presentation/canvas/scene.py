"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsScene

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Design,
    Stage,
)

from .items import ComponentItem, StageItem


class DesignScene(QGraphicsScene):
    """Graphics scene for the design canvas.

    Uses GridConfig to ensure all positions align to grid intersections.
    Manages pipeline stages that are defined by register placements.
    """

    component_added = Signal(object)  # ComponentInstance
    component_removed = Signal(object)  # UUID
    component_selected = Signal(object)  # ComponentInstance or None
    stages_changed = Signal()  # Emitted when stage configuration changes

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
        self._snap_to_grid = True
        self._register_width: float = 80.0  # Default, updated from library

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
        self._design = design

        # Create stage items first (they're behind components)
        for stage in design.stages:
            self._create_stage_item(stage)

        # Create component items
        for instance in design.components:
            self._create_component_item(instance)

    def new_design(self) -> None:
        """Create a new empty design."""
        self.clear()
        self._component_items.clear()
        self._stage_items.clear()
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
        """Get the x position for a register, snapping to existing stages.

        Args:
            x: Desired x position.

        Returns:
            Adjusted x position (snapped to stage or grid).
        """
        # Check if position falls within an existing stage
        stage = self._design.get_stage_at_x(x)
        if stage:
            return stage.x_position

        # Snap to grid if no stage
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _assign_register_to_stage(self, instance: ComponentInstance) -> None:
        """Assign a register instance to a stage, creating one if needed.

        Args:
            instance: The register component instance.
        """
        x = instance.position[0]

        # Check for existing stage at this position
        stage = self._design.get_stage_at_x(x)

        if stage is None:
            # Create a new stage
            stage = Stage(
                index=0,  # Will be set by reindex
                x_position=x,
                width=self._register_width,
                register_ids=[instance.id],
            )
            self._design.stages.append(stage)
            self._design.reindex_stages()
            self._rebuild_all_stages()
        else:
            # Add to existing stage
            if instance.id not in stage.register_ids:
                stage.register_ids.append(instance.id)

        # Update instance's pipeline_stage
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
        # Remove all existing stage items
        for stage_id, item in list(self._stage_items.items()):
            self.removeItem(item)
        self._stage_items.clear()

        # Create new stage items
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

        return item

    def _on_register_moved(self, instance: ComponentInstance, old_x: float) -> None:
        """Handle a register being moved.

        Args:
            instance: The register instance that moved.
            old_x: The previous x position.
        """
        new_x = instance.position[0]

        # Remove from old stage
        for stage in self._design.stages:
            if instance.id in stage.register_ids:
                stage.register_ids.remove(instance.id)
                break

        # Check if there's an existing stage at the new position
        existing_stage = self._design.get_stage_at_x(new_x)

        if existing_stage is not None:
            # Add to existing stage
            if instance.id not in existing_stage.register_ids:
                existing_stage.register_ids.append(instance.id)
        else:
            # Create new stage at this position
            new_stage = Stage(
                index=0,  # Will be set by reindex
                x_position=new_x,
                width=self._register_width,
                register_ids=[instance.id],
            )
            self._design.stages.append(new_stage)

        # Clean up empty stages
        self._design.remove_empty_stages()

        # Reindex stages and rebuild visuals
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

        # Remove from stage if it's a register
        if is_register:
            for stage in self._design.stages:
                if component_id in stage.register_ids:
                    stage.register_ids.remove(component_id)
                    break

        self.removeItem(item)
        del self._component_items[component_id]
        self._design.remove_component(component_id)

        # Clean up stages if register was removed
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
        """Snap x coordinate for a register (stage-aware).

        Args:
            x: The x coordinate to snap.

        Returns:
            Snapped x coordinate (to stage or grid).
        """
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
