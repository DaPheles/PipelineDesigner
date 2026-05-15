"""Connection management mixin for DesignScene."""

from uuid import UUID

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from pipeline_designer.domain.models import Connection, PortReference
from pipeline_designer.domain.models.component import Port, PortSignalClass

from .commands import AddConnectionCommand, RemoveConnectionCommand
from .items import ConnectionItem, InterfacePortItem, TempConnectionItem
from .items.port_item import PortItem

# Signal-kind names that are concrete (not a generic placeholder).
# Anything not in this set is treated as a generic reference and skipped.
_KNOWN_KINDS = frozenset({
    "std_logic", "std_logic_vector",
    "std_ulogic", "std_ulogic_vector",
    "sfixed", "ufixed",
    "integer", "natural", "positive",
    "boolean", "bit", "bit_vector", "real",
})


class _SceneConnectionMixin:
    """Manages connection drag-creation, lifecycle, and position updates."""

    # ── Connection drag start ─────────────────────────────────────────────────

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

    # ── Validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _signal_types_compatible(src: Port, tgt: Port) -> bool:
        """Return False if signal types are demonstrably incompatible.

        Skips the check when either side uses a generic reference (unknown
        kind) or when widths/lsbs are symbolic expressions.
        """
        sk = src.signal_type.kind.lower()
        tk = tgt.signal_type.kind.lower()
        if sk not in _KNOWN_KINDS or tk not in _KNOWN_KINDS:
            return True  # can't determine statically
        if sk != tk:
            return False
        # For vector types check concrete widths if available
        if sk not in ("std_logic", "bit", "boolean"):
            try:
                if int(src.signal_type.width) != int(tgt.signal_type.width):
                    return False
            except (ValueError, TypeError):
                pass  # symbolic expression — skip width check
        # For fixed-point types also check LSB (fractional bit count)
        if sk in ("sfixed", "ufixed"):
            try:
                if int(src.signal_type.lsb) != int(tgt.signal_type.lsb):
                    return False
            except (ValueError, TypeError):
                pass  # symbolic — skip
        return True

    def _iface_port_has_connections(self, iface_id: UUID) -> bool:
        """True if the interface port already participates in any connection."""
        return any(
            conn.source.interface_port_id == iface_id
            or conn.target.interface_port_id == iface_id
            for conn in self._design.connections
        )

    def _is_valid_connection_target(self, target_port: PortItem) -> bool:
        """Check if a component port is a valid connection target."""
        if (self._connection_source_port is None
                and self._connection_source_interface_port is None):
            return False

        if not target_port.is_input():
            return False

        target_comp_id = target_port.get_component_id()
        tgt = target_port.get_port()

        if self._connection_source_port is not None:
            source_comp_id = self._connection_source_component_id
            if source_comp_id == target_comp_id:
                return False

            src = self._connection_source_port.get_port()

            # Signal class must match (clock↔clock, reset↔reset, etc.)
            if src.signal_class != tgt.signal_class:
                return False

            # Signal kind / width compatibility (data and control ports only)
            if src.signal_class in (PortSignalClass.CLOCK, PortSignalClass.RESET):
                pass  # no type check for clock/reset
            elif not self._signal_types_compatible(src, tgt):
                return False

            # Duplicate check
            src_name = src.name
            tgt_name = tgt.name
            for conn in self._design.connections:
                if (conn.source.component_id == source_comp_id
                        and conn.source.port_name == src_name
                        and conn.target.component_id == target_comp_id
                        and conn.target.port_name == tgt_name):
                    return False

        elif self._connection_source_interface_port is not None:
            src_iface = self._connection_source_interface_port.get_interface_port()
            tgt_name = tgt.name

            # Signal class must always match.
            if src_iface.signal_class != tgt.signal_class:
                return False

            # Duplicate check
            for conn in self._design.connections:
                if (conn.source.interface_port_id == src_iface.id
                        and conn.target.component_id == target_comp_id
                        and conn.target.port_name == tgt_name):
                    return False

        return True

    def _is_valid_interface_target(self, target_interface_port: InterfacePortItem) -> bool:
        """Check if an output interface port is a valid connection target."""
        if self._connection_source_port is None:
            return False

        if not target_interface_port.is_output():
            return False

        src = self._connection_source_port.get_port()
        tgt_iface = target_interface_port.get_interface_port()
        source_comp_id = self._connection_source_component_id
        src_name = src.name

        # Signal class must always match.
        if src.signal_class != tgt_iface.signal_class:
            return False

        # Duplicate check
        for conn in self._design.connections:
            if (conn.source.component_id == source_comp_id
                    and conn.source.port_name == src_name
                    and conn.target.interface_port_id == tgt_iface.id):
                return False

        return True

    # ── Connection creation ───────────────────────────────────────────────────

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

    # ── Connection item creation ──────────────────────────────────────────────

    def _wire_kind_for_connection(self, connection: Connection) -> str:
        """Return 'clock', 'reset', 'control', or 'data' for a connection's source port."""
        from pipeline_designer.domain.models.component import PortSignalClass

        src = connection.source
        sc: PortSignalClass | None = None

        if src.component_id is not None:
            comp_item = self._component_items.get(src.component_id)
            if comp_item:
                port_item = comp_item._port_items.get(src.port_name)
                if port_item:
                    sc = port_item.get_port().signal_class
        elif src.interface_port_id is not None:
            iport = next(
                (p for p in self._design.interface_ports
                 if p.id == src.interface_port_id),
                None,
            )
            if iport:
                sc = iport.signal_class

        if sc is not None:
            return sc.value  # "clock", "reset", "control", or "data"
        return "data"

    def _get_port_edge(
        self,
        component_id: UUID | None,
        port_name: str,
        interface_port_id: UUID | None = None,
    ) -> str:
        """Return which component edge ('left','right','top','bottom') a port is on.

        Interface ports always exit horizontally: input stage → 'right',
        output stage → 'left'.
        """
        if interface_port_id is not None:
            iface_item = self._interface_port_items.get(interface_port_id)
            if iface_item:
                return "right" if iface_item.is_input() else "left"
            return "right"

        if component_id is not None:
            comp_item = self._component_items.get(component_id)
            if comp_item:
                port_item = comp_item._port_items.get(port_name)
                if port_item:
                    edge = port_item._edge
                    if edge and edge not in ("none", ""):
                        return edge
        return "right"

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

        wire_kind   = self._wire_kind_for_connection(connection)
        source_edge = self._get_port_edge(
            connection.source.component_id,
            connection.source.port_name,
            connection.source.interface_port_id,
        )
        target_edge = self._get_port_edge(
            connection.target.component_id,
            connection.target.port_name,
            connection.target.interface_port_id,
        )
        item = ConnectionItem(
            connection,
            QPointF(source_pos[0], source_pos[1]),
            QPointF(target_pos[0], target_pos[1]),
            wire_kind=wire_kind,
            source_edge=source_edge,
            target_edge=target_edge,
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

    # ── Interface port type sync ──────────────────────────────────────────────

    def _sync_interface_port_types(self) -> None:
        """Refresh the visual appearance of all interface port items.

        Signal class on interface ports is fully user-controlled and is never
        auto-derived from connections.  This method exists only to keep port
        colours up-to-date after connection add/remove events.
        """
        for iface_item in self._interface_port_items.values():
            iface_item._update_appearance()

    # ── Cancel / cleanup ──────────────────────────────────────────────────────

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

    # ── Connection lifecycle ──────────────────────────────────────────────────

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
        self._sync_interface_port_types()
        self.connection_removed.emit(connection_id)
        self._emit_validation_warnings(self._validate_all_connections())
        return True

    def _add_connection_internal(self, connection: Connection) -> ConnectionItem | None:
        """Internal method to add a connection (used by commands)."""
        self._design.add_connection(connection)
        self._sync_interface_port_types()
        item = self._create_connection_item(connection)
        self.connection_added.emit(connection)
        self._emit_validation_warnings(self._validate_all_connections())
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

    # ── Connection validation ─────────────────────────────────────────────────

    def _effective_generics_for_ref(self, ref: PortReference) -> dict | None:
        """Return effective generics for a component port reference.

        Merges definition defaults with instance overrides, then substitutes
        outer design generic names wherever the instance value matches the
        design's outer default (same name, same value).  Returns ``None`` if
        the component or definition cannot be resolved.
        """
        if ref.component_id is None:
            return None
        comp_item = self._component_items.get(ref.component_id)
        if comp_item is None:
            return None
        inst = comp_item._instance
        defn = self._library.get(inst.definition_ref)
        if defn is None:
            return inst.generic_values
        resolved: dict = {g.name: g.default_value for g in defn.generics if g.default_value is not None}
        resolved.update(inst.generic_values)
        outer = {g.name: g.default_value for g in self._design.component_config.generics if g.default_value is not None}
        return {name: (name if (name in outer and val == outer[name]) else val) for name, val in resolved.items()}

    def _resolve_vhdl_type(self, ref: PortReference) -> str | None:
        """Return the VHDL type string for a port reference.

        Handles both component ports (with full generic resolution) and
        interface ports.  Returns ``None`` when the type cannot be determined.
        """
        if ref.interface_port_id is not None:
            iport = next(
                (p for p in self._design.interface_ports if p.id == ref.interface_port_id),
                None,
            )
            if iport is None:
                return None
            return iport.effective_signal_type().to_vhdl_type()
        if ref.component_id is not None:
            comp_item = self._component_items.get(ref.component_id)
            if comp_item is None:
                return None
            port_item = comp_item._port_items.get(ref.port_name)
            if port_item is None:
                return None
            generics = self._effective_generics_for_ref(ref)
            return port_item.get_port().signal_type.to_vhdl_type(generics)
        return None

    def _resolve_fp_notation(self, ref: PortReference) -> str | None:
        """Return the FP notation (e.g. ``'U8.8'``) for a DATA port reference.

        Returns ``None`` when the port is not DATA class, when the format cannot
        be resolved (symbolic generics), or when the interface port has not yet
        had its ``signal_type`` explicitly set by the user.
        """
        if ref.component_id is not None:
            comp_item = self._component_items.get(ref.component_id)
            if comp_item is None:
                return None
            port_item = comp_item._port_items.get(ref.port_name)
            if port_item is None:
                return None
            port = port_item.get_port()
            if port.signal_class != PortSignalClass.DATA:
                return None
            generics = comp_item._instance.generic_values
            return port.signal_type.notation(generics)
        elif ref.interface_port_id is not None:
            iport = next(
                (p for p in self._design.interface_ports
                 if p.id == ref.interface_port_id),
                None,
            )
            if iport is None or iport.signal_class != PortSignalClass.DATA:
                return None
            if iport.signal_type is None:
                return None  # format not yet explicitly specified
            return iport.signal_type.notation()
        return None

    def _resolve_signal_class(self, ref: PortReference) -> PortSignalClass | None:
        """Return the PortSignalClass for a port reference, or None if unresolvable."""
        if ref.component_id is not None:
            comp_item = self._component_items.get(ref.component_id)
            if comp_item:
                port_item = comp_item._port_items.get(ref.port_name)
                if port_item:
                    return port_item.get_port().signal_class
        elif ref.interface_port_id is not None:
            iport = next(
                (p for p in self._design.interface_ports
                 if p.id == ref.interface_port_id),
                None,
            )
            if iport:
                return iport.signal_class
        return None

    def _validate_all_connections(self) -> list[str]:
        """Check every connection for signal-class and FP-format mismatches.

        Marks each ConnectionItem invalid/valid and returns a list of human-readable
        warning strings for any mismatches found.
        """
        warnings: list[str] = []
        for conn_item in self._connection_items.values():
            conn = conn_item.get_connection()
            src_class = self._resolve_signal_class(conn.source)
            tgt_class = self._resolve_signal_class(conn.target)

            # Signal-class mismatch (highest priority)
            if src_class is not None and tgt_class is not None and src_class != tgt_class:
                src_label = conn.source.port_name
                tgt_label = conn.target.port_name
                reason = (
                    f"'{src_label}' ({src_class.value}) → '{tgt_label}' ({tgt_class.value})"
                )
                conn_item.set_invalid(True, reason)
                warnings.append(f"Signal-class mismatch: {reason}")
                continue

            # VHDL type mismatch — checked for DATA↔DATA connections.
            # Uses full resolved generics (definition defaults + instance overrides
            # + outer-design generic substitution) so symbolic generics like
            # "WIDTH+2" are compared correctly.  If both sides resolve to the
            # same type string the connection is valid; if notation is available
            # for both it is used in the tooltip for readability.
            if src_class == PortSignalClass.DATA == tgt_class:
                src_vtype = self._resolve_vhdl_type(conn.source)
                tgt_vtype = self._resolve_vhdl_type(conn.target)
                if src_vtype is not None and tgt_vtype is not None and src_vtype != tgt_vtype:
                    src_fmt = self._resolve_fp_notation(conn.source)
                    tgt_fmt = self._resolve_fp_notation(conn.target)
                    reason = (
                        f"Format mismatch: {src_fmt} → {tgt_fmt}"
                        if src_fmt and tgt_fmt
                        else f"Type mismatch: {src_vtype} → {tgt_vtype}"
                    )
                    conn_item.set_invalid(True, reason)
                    warnings.append(
                        f"Signal type mismatch on "
                        f"'{conn.source.port_name}' → '{conn.target.port_name}': {reason}"
                    )
                    continue

            conn_item.set_invalid(False)

        return warnings

    def _emit_validation_warnings(self, warnings: list[str]) -> None:
        """Emit the validation_warnings signal if the scene has one."""
        if hasattr(self, "validation_warnings"):
            self.validation_warnings.emit(warnings)
