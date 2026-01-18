"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsScene

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import ComponentDefinition, ComponentInstance, Design

from .items import ComponentItem


class DesignScene(QGraphicsScene):
    """Graphics scene for the design canvas.

    Uses GridConfig to ensure all positions align to grid intersections.
    """

    component_added = Signal(object)  # ComponentInstance
    component_removed = Signal(object)  # UUID
    component_selected = Signal(object)  # ComponentInstance or None

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
        self._snap_to_grid = True

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

    def get_design(self) -> Design:
        """Get the current design."""
        return self._design

    def set_design(self, design: Design) -> None:
        """Set a new design, clearing existing items."""
        self.clear()
        self._component_items.clear()
        self._design = design

        for instance in design.components:
            self._create_component_item(instance)

    def new_design(self) -> None:
        """Create a new empty design."""
        self.clear()
        self._component_items.clear()
        self._design = Design()

    def add_component_at(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Add a component instance at the specified position.

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

        if self._snap_to_grid:
            x = self._grid.snap_to_grid(x)
            y = self._grid.snap_to_grid(y)

        instance = ComponentInstance(
            definition_ref=component_name,
            position=(x, y),
        )

        self._design.add_component(instance)
        item = self._create_component_item(instance)
        self.component_added.emit(instance)
        return item

    def _create_component_item(self, instance: ComponentInstance) -> ComponentItem:
        """Create a graphics item for a component instance."""
        definition = self._library.get(instance.definition_ref)
        item = ComponentItem(instance, definition, grid=self._grid)
        item.setPos(instance.position[0], instance.position[1])
        self.addItem(item)
        self._component_items[instance.id] = item
        return item

    def remove_component(self, component_id: UUID) -> bool:
        """Remove a component instance from the scene."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        self.removeItem(item)
        del self._component_items[component_id]
        self._design.remove_component(component_id)
        self.component_removed.emit(component_id)
        return True

    def get_component_item(self, component_id: UUID) -> ComponentItem | None:
        """Get a component item by ID."""
        return self._component_items.get(component_id)

    def snap_to_grid(self, x: float, y: float) -> tuple[float, float]:
        """Snap coordinates to the grid."""
        if not self._snap_to_grid:
            return x, y
        return (
            self._grid.snap_to_grid(x),
            self._grid.snap_to_grid(y),
        )

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
