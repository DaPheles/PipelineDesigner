"""Connection management mixin for DesignScene."""

from uuid import UUID

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from pipeline_designer.domain.models import Connection, PortReference

from .commands import AddConnectionCommand, RemoveConnectionCommand
from .items import ConnectionItem, InterfacePortItem, TempConnectionItem
from .items.port_item import PortItem


class _SceneConnectionMixin:
    """Manages connection drag-creation, lifecycle, and position updates."""

    def _start_connection(self, port_item: PortItem) -> None:
        """Start creating a connection from an output port."""
        if not port_item.is_output():
            return

        self._connection_source_port = port_item
        self._connection_source_component_id = port_item.get_component_id()
        self._connection_source_interface_port = None

        self._set_components_movable(False)

        start_pos = port_item.scenePos()
        self._temp_connection = TempConnectionItem(start_pos)
        self.addItem(self._temp_connection)

    def _start_interface_connection(self, interface_port_item: InterfacePortItem) -> None:
        """Start creating a connection from an input interface port."""
        if not interface_port_item.is_input():
            return

        self._connection_source_interface_port = interface_port_item
        self._connection_source_port = None
        self._connection_source_component_id = None

        self._set_components_movable(False)

        start_pos = interface_port_item.scenePos()
        self._temp_connection = TempConnectionItem(start_pos)
        self.addItem(self._temp_connection)

    def _is_valid_connection_target(self, target_port: PortItem) -> bool:
        """Check if a component port is a valid connection target."""
        if (self._connection_source_port is None
                and self._connection_source_interface_port is None):
            return False

        if not target_port.is_input():
            return False

        target_comp_id = target_port.get_component_id()

        if self._connection_source_port is not None:
            source_comp_id = self._connection_source_component_id
            if source_comp_id == target_comp_id:
                return False

            source_port_name = self._connection_source_port.get_port().name
            target_port_name = target_port.get_port().name
            for conn in self._design.connections:
                if (conn.source.component_id == source_comp_id
                        and conn.source.port_name == source_port_name
                        and conn.target.component_id == target_comp_id
                        and conn.target.port_name == target_port_name):
                    return False

        elif self._connection_source_interface_port is not None:
            source_iface_port = self._connection_source_interface_port.get_interface_port()
            target_port_name = target_port.get_port().name
            for conn in self._design.connections:
                if (conn.source.interface_port_id == source_iface_port.id
                        and conn.target.component_id == target_comp_id
                        and conn.target.port_name == target_port_name):
                    return False

        return True

    def _is_valid_interface_target(self, target_interface_port: InterfacePortItem) -> bool:
        """Check if an output interface port is a valid connection target."""
        if self._connection_source_port is None:
            return False

        if not target_interface_port.is_output():
            return False

        source_comp_id = self._connection_source_component_id
        source_port_name = self._connection_source_port.get_port().name
        target_iface_port = target_interface_port.get_interface_port()

        for conn in self._design.connections:
            if (conn.source.component_id == source_comp_id
                    and conn.source.port_name == source_port_name
                    and conn.target.interface_port_id == target_iface_port.id):
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

    def _get_port_position(
        self,
        component_id: UUID | None,
        port_name: str,
        interface_port_id: UUID | None = None,
    ) -> tuple[float, float] | None:
        """Get the scene position of a port (component or interface)."""
        if interface_port_id is not None:
            iface_item = self._interface_port_items.get(interface_port_id)
            if iface_item is not None:
                pos = iface_item.scenePos()
                return (pos.x(), pos.y())
            return None

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

        self._set_components_movable(True)

        for comp_item in self._component_items.values():
            for port_item in comp_item._port_items.values():
                port_item.set_connection_target(False)

        for iface_port_item in self._interface_port_items.values():
            iface_port_item.set_highlighted(False)

    def _set_components_movable(self, movable: bool) -> None:
        """Enable or disable movement on all component items."""
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

        self._update_component_bounds()
