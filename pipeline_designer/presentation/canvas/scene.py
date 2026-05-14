"""Design canvas scene for managing components and connections."""

from uuid import UUID

from PySide6.QtCore import QLine, QPointF, QRectF, Qt, Signal
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

from ._scene_interface_mixin import _SceneInterfaceMixin
from ._scene_component_mixin import _SceneComponentMixin
from ._scene_connection_mixin import _SceneConnectionMixin
from ._scene_alignment_mixin import _SceneAlignmentMixin


class DesignScene(
    _SceneAlignmentMixin,
    _SceneConnectionMixin,
    _SceneComponentMixin,
    _SceneInterfaceMixin,
    QGraphicsScene,
):
    """Graphics scene for the design canvas.

    Uses GridConfig to ensure all positions align to grid intersections.
    Manages pipeline stages that are defined by register placements.
    Handles connection creation by dragging from output to input ports.

    Behaviour is split across four mixins:
    - _SceneInterfaceMixin   : interface stages, ports, component bounds
    - _SceneComponentMixin   : component/stage lifecycle, snapping, conflicts
    - _SceneConnectionMixin  : connection drag-creation and lifecycle
    - _SceneAlignmentMixin   : undo/redo, stage group-move, composite alignment
    """

    component_added = Signal(object)    # ComponentInstance
    component_removed = Signal(object)  # UUID
    component_selected = Signal(object) # ComponentInstance or None
    stages_changed = Signal()           # Emitted when stage configuration changes
    connection_added = Signal(object)   # Connection
    connection_removed = Signal(object) # UUID
    validation_warnings = Signal(list)  # list[str] — signal-class mismatch warnings

    def __init__(self, grid: GridConfig | None = None, parent=None):
        super().__init__(parent)

        self._grid = grid or DEFAULT_GRID
        self._design = Design()
        self._library: dict[str, ComponentDefinition] = {}
        self._library_loader: LibraryLoader | None = None
        self._component_items: dict[UUID, ComponentItem] = {}
        self._stage_items: dict[UUID, StageItem] = {}
        self._connection_items: dict[UUID, ConnectionItem] = {}
        self._snap_to_grid = True
        self._register_width: float = 80.0

        # Connection creation state
        self._temp_connection: TempConnectionItem | None = None
        self._connection_source_port: PortItem | None = None
        self._connection_source_component_id: UUID | None = None
        self._connection_source_interface_port: InterfacePortItem | None = None

        self._undo_stack = UndoStack()
        self._move_start_positions: dict[UUID, tuple[float, float]] = {}

        # Interface stages and bounds
        self._input_stage: InterfaceStageItem | None = None
        self._output_stage: InterfaceStageItem | None = None
        self._component_bounds: ComponentBoundsItem | None = None
        self._interface_enabled = True
        self._interface_port_items: dict[UUID, InterfacePortItem] = {}
        self._preview_port_item: QGraphicsEllipseItem | None = None

        self._conflict_items: set[UUID] = set()

        # Stage group-move state
        self._moving_stage: Stage | None = None
        self._stage_move_mouse_start: QPointF = QPointF()
        self._stage_move_stage_start_x: float = 0.0
        self._stage_move_new_x: float = 0.0
        self._stage_group_items: list[ComponentItem] = []
        self._stage_group_original_positions: dict[UUID, tuple[float, float]] = {}
        self._in_stage_group_move: bool = False

        # Composite drag alignment state
        self._dragging_composite_id: UUID | None = None
        self._composite_drag_overlays: list[TempPositionOverlayItem] = []
        self._composite_proposed_shifts: dict[UUID, float] = {}
        self._last_drag_was_aligned: bool = True

        # Accepted temporary positions
        self._temp_position_overlays: dict[UUID, TempPositionOverlayItem] = {}

        # Composite → stage bindings
        self._composite_stage_bindings: dict[UUID, dict[int, UUID]] = {}

        self._setup_scene()

    def _setup_scene(self) -> None:
        """Configure scene settings."""
        self.setSceneRect(QRectF(-5000, -5000, 10000, 10000))
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))

        if self._interface_enabled:
            self._create_interface_items()

    @property
    def grid(self) -> GridConfig:
        """Get the grid configuration."""
        return self._grid

    # ── Design lifecycle ──────────────────────────────────────────────────────

    def get_design(self) -> Design:
        """Get the current design."""
        return self._design

    def get_invalid_connection_ids(self) -> set:
        """Return the IDs of all currently-invalid (signal-class mismatch) connections."""
        from uuid import UUID
        return {
            item.get_connection().id
            for item in self._connection_items.values()
            if item._is_invalid
        }

    def revalidate_connections(self) -> None:
        """Re-check all connections for signal-class mismatches and emit warnings."""
        self._sync_interface_port_types()
        self._emit_validation_warnings(self._validate_all_connections())

    def set_design(self, design: Design) -> None:
        """Set a new design, clearing existing items."""
        # Reset alignment state FIRST while Qt objects are still alive.
        # self.clear() below destroys all C++ wrappers; calling .scene()
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

        if self._interface_enabled:
            self._create_interface_items()

        for stage in design.stages:
            self._create_stage_item(stage)

        for instance in design.components:
            self._create_component_item(instance)

        for connection in design.connections:
            self._create_connection_item(connection)

        self._sync_interface_port_types()
        self._update_all_component_alignments()
        self._update_component_bounds()
        self._emit_validation_warnings(self._validate_all_connections())

    def new_design(self) -> None:
        """Create a new empty design."""
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

        if self._interface_enabled:
            self._create_interface_items()

    # ── Qt event handlers ─────────────────────────────────────────────────────

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse move for connection dragging and stage group-moves."""
        if self._moving_stage is not None:
            self._update_stage_group_move(event.scenePos())
            self.update_connection_positions()
            return

        super().mouseMoveEvent(event)

        if self._temp_connection:
            self._temp_connection.set_end_pos(event.scenePos())

            items = self.items(event.scenePos())
            target_port = None
            target_interface_port = None

            for item in items:
                if isinstance(item, PortItem) and item.is_input():
                    target_port = item
                    break
                elif isinstance(item, InterfacePortItem) and item.is_output():
                    target_interface_port = item
                    break

            for comp_item in self._component_items.values():
                for port_item in comp_item._port_items.values():
                    port_item.set_connection_target(False)
            for iface_port_item in self._interface_port_items.values():
                iface_port_item.set_highlighted(False)

            found_valid_target = False
            if target_port is not None:
                is_valid = self._is_valid_connection_target(target_port)
                target_port.set_connection_target(True, is_valid)
                self._temp_connection.set_target_state(True, is_valid)
                found_valid_target = True
            elif target_interface_port is not None and self._connection_source_port is not None:
                is_valid = self._is_valid_interface_target(target_interface_port)
                target_interface_port.set_highlighted(is_valid)
                self._temp_connection.set_target_state(True, is_valid)
                found_valid_target = True

            if not found_valid_target:
                self._temp_connection.set_target_state(False)

        self.update_connection_positions()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle mouse release for connection creation and stage group-moves."""
        if self._moving_stage is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._commit_stage_group_move()
            return

        if self._temp_connection:
            items = self.items(event.scenePos())

            if self._connection_source_port is not None:
                for item in items:
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

            elif self._connection_source_interface_port is not None:
                for item in items:
                    if isinstance(item, PortItem) and item.is_input():
                        if self._is_valid_connection_target(item):
                            self._create_interface_to_component_connection(
                                self._connection_source_interface_port,
                                item,
                            )
                            self._cancel_connection()
                            super().mouseReleaseEvent(event)
                            return

            self._cancel_connection()

        super().mouseReleaseEvent(event)

    # ── Misc scene-level settings ─────────────────────────────────────────────

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
        right = int(rect.right())
        bottom = int(rect.bottom())

        lines = []
        x = left
        while x <= right:
            lines.append(QLine(x, top, x, bottom))
            x += grid_size
        y = top
        while y <= bottom:
            lines.append(QLine(left, y, right, y))
            y += grid_size

        painter.drawLines(lines)
