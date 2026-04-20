"""Interface stage and port management mixin for DesignScene."""

from uuid import UUID

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem

from pipeline_designer.domain.models import InterfaceDirection, InterfacePort

from .items import ComponentBoundsItem, InterfacePortItem, InterfaceStageItem


class _SceneInterfaceMixin:
    """Manages interface stages, interface ports, and component bounds."""

    def _create_interface_items(self) -> None:
        """Create the interface stages and component bounds."""
        default_height = 400.0
        default_input_x = -200.0
        default_output_x = 200.0

        self._component_bounds = ComponentBoundsItem(self._grid)
        self.addItem(self._component_bounds)

        self._input_stage = InterfaceStageItem(
            is_input=True,
            x_position=default_input_x,
            height=default_height,
            grid=self._grid,
        )
        self._input_stage.on_position_changed = self._on_interface_stage_moved
        self.addItem(self._input_stage)

        self._output_stage = InterfaceStageItem(
            is_input=False,
            x_position=default_output_x,
            height=default_height,
            grid=self._grid,
        )
        self._output_stage.on_position_changed = self._on_interface_stage_moved
        self.addItem(self._output_stage)

        self._update_interface_ports()
        self._update_component_bounds()

    def _on_interface_stage_moved(self, x: float) -> None:
        """Handle interface stage movement."""
        self._update_component_bounds()

    def _update_interface_ports(self) -> None:
        """Update interface ports from the design."""
        if not self._input_stage or not self._output_stage:
            return

        for port in self._input_stage.get_ports():
            self.removeItem(port)
        for port in self._output_stage.get_ports():
            self.removeItem(port)

        self._interface_port_items.clear()

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

        port_item.on_position_changed = lambda y: self._on_interface_port_moved(iface_port.id)
        port_item.on_reposition_preview = lambda sy: self._on_reposition_preview(
            iface_port.id, sy, is_input
        )
        port_item.on_reposition_commit = lambda sy: self._on_reposition_commit(
            iface_port.id, sy, is_input
        )

        if is_input:
            port_item.on_connection_start = lambda: self._start_interface_connection(port_item)

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

        target_stage = None
        if is_input:
            input_rect = self._input_stage.sceneBoundingRect()
            if input_rect.contains(x, y):
                target_stage = self._input_stage
        else:
            output_rect = self._output_stage.sceneBoundingRect()
            if output_rect.contains(x, y):
                target_stage = self._output_stage

        if target_stage is None:
            return False

        snapped_y = self._grid.snap_to_grid(y)

        existing_names = {p.name for p in self._design.interface_ports}
        base_name = "in" if is_input else "out"
        port_name = base_name
        counter = 1
        while port_name in existing_names:
            port_name = f"{base_name}{counter}"
            counter += 1

        stage_y = target_stage.pos().y()
        rel_y = snapped_y - stage_y
        grid_x = int(x / self._grid.size)
        grid_y = int(rel_y / self._grid.size)

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

        self.removeItem(port_item)
        del self._interface_port_items[port_id]

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

        component_rects = [
            comp_item.sceneBoundingRect()
            for comp_item in self._component_items.values()
        ]

        self._auto_fit_interface_stages(component_rects)

        input_x = self._input_stage.get_x_position()
        output_x = self._output_stage.get_x_position()

        top_y, bottom_y = self._component_bounds.update_from_components(
            input_x,
            output_x,
            InterfaceStageItem.STAGE_WIDTH,
            component_rects,
        )

        height = bottom_y - top_y
        self._input_stage.set_height(height, top_y)
        self._output_stage.set_height(height, top_y)

        for stage_item in self._stage_items.values():
            stage_item.set_bounds(top_y, bottom_y)

        self._update_visual_extent(input_x, output_x, top_y, bottom_y)

    def _auto_fit_interface_stages(self, component_rects: list[QRectF]) -> None:
        """Auto-fit input/output stages to contain all components."""
        if not component_rects or not self._input_stage or not self._output_stage:
            return

        min_x = min(rect.left() for rect in component_rects)
        max_x = max(rect.right() for rect in component_rects)

        padding = self._grid.to_pixels(2)
        input_x = self._input_stage.get_x_position()
        output_x = self._output_stage.get_x_position()

        required_input_x = min_x - InterfaceStageItem.STAGE_WIDTH - padding
        required_output_x = max_x + padding

        if required_input_x < input_x:
            self._input_stage.setX(self._grid.snap_to_grid(required_input_x))

        if required_output_x > output_x:
            self._output_stage.setX(self._grid.snap_to_grid(required_output_x))

    def _update_visual_extent(
        self, input_x: float, output_x: float, top_y: float, bottom_y: float
    ) -> None:
        """Update the design's visual extent from scene coordinates."""
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
