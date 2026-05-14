"""Scene-level signal-class tests.

Each test corresponds to a concrete bug that was found and fixed:

  1. ComponentItem creates independent Port copies — fixes shared-object
     contamination (mutating one instance's port was silently changing every
     other instance of the same definition).

  2. Per-instance port_signal_classes override applied at item creation time.

  3. _sync_interface_port_types() must NOT reset interface port signal_class —
     fixes the revert-to-data bug on re-selection.

  4. _is_valid_connection_target() blocks cross-class connections during drag.

  5. _is_valid_interface_target() blocks interface-port cross-class connections.

  6. _validate_all_connections() flags existing connections after a class change.

  7. get_invalid_connection_ids() returns the correct UUID set.

  8. revalidate_connections() triggers validation_warnings signal.
"""

import pytest

from pipeline_designer.domain.models.component import (
    ComponentDefinition,
    Port,
    PortDirection,
    PortSignalClass,
    VisualConfig,
)
from pipeline_designer.domain.models.instance import (
    ComponentInstance,
    Connection,
    InterfaceDirection,
    InterfacePort,
    PortReference,
)
from pipeline_designer.presentation.canvas.items.component_item import ComponentItem


# ── helpers ───────────────────────────────────────────────────────────────────


def _add(scene, name: str, x_px: float = 0.0, y_px: float = 0.0):
    """Add a named component and return (instance, comp_item)."""
    item = scene.add_component_at(name, x_px, y_px)
    assert item is not None, f"add_component_at('{name}') returned None"
    return item.get_instance(), item


def _connect_direct(scene, src_inst, src_port_name: str, tgt_inst, tgt_port_name: str):
    """Bypass the drag machinery and wire two ports directly via the internal API."""
    conn = Connection(
        source=PortReference(component_id=src_inst.id, port_name=src_port_name),
        target=PortReference(component_id=tgt_inst.id, port_name=tgt_port_name),
    )
    scene._add_connection_internal(conn)
    return conn


# ── 1. Port copy independence ─────────────────────────────────────────────────


class TestPortCopyIndependence:
    """ComponentItem._create_ports() must create a private Port copy per instance.

    Bug: the original code only copied when an override existed.  A first
    signal_class change on instance-A mutated the shared Port from
    ComponentDefinition.ports, silently altering instance-B.
    """

    def test_port_objects_are_distinct(self, qapp, add_def):
        inst1 = ComponentInstance(definition_ref="Add", position=(0, 0))
        inst2 = ComponentInstance(definition_ref="Add", position=(5, 0))
        item1 = ComponentItem(inst1, add_def)
        item2 = ComponentItem(inst2, add_def)

        port1 = item1._port_items["out"].get_port()
        port2 = item2._port_items["out"].get_port()
        assert port1 is not port2, "Both instances share the same Port object"

    def test_mutating_one_port_does_not_affect_other(self, qapp, add_def):
        inst1 = ComponentInstance(definition_ref="Add", position=(0, 0))
        inst2 = ComponentInstance(definition_ref="Add", position=(5, 0))
        item1 = ComponentItem(inst1, add_def)
        item2 = ComponentItem(inst2, add_def)

        item1._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK

        assert item2._port_items["out"].get_port().signal_class == PortSignalClass.DATA

    def test_mutation_does_not_corrupt_definition(self, qapp, add_def):
        inst = ComponentInstance(definition_ref="Add", position=(0, 0))
        item = ComponentItem(inst, add_def)

        item._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK

        # The shared ComponentDefinition must be clean.
        assert add_def.get_port_by_name("out").signal_class == PortSignalClass.DATA


# ── 2. Per-instance override applied at item creation ────────────────────────


class TestPortOverrideApplication:
    """port_signal_classes on ComponentInstance must be reflected in PortItem."""

    def test_override_applied_at_creation(self, qapp, add_def):
        inst = ComponentInstance(
            definition_ref="Add",
            position=(0, 0),
            port_signal_classes={"out": "clock"},
        )
        item = ComponentItem(inst, add_def)

        assert item._port_items["out"].get_port().signal_class == PortSignalClass.CLOCK

    def test_unoverridden_port_keeps_definition_class(self, qapp, add_def):
        inst = ComponentInstance(
            definition_ref="Add",
            position=(0, 0),
            port_signal_classes={"out": "clock"},  # only 'out' overridden
        )
        item = ComponentItem(inst, add_def)

        assert item._port_items["a"].get_port().signal_class == PortSignalClass.DATA

    def test_invalid_override_value_falls_back_gracefully(self, qapp, add_def):
        """An unrecognised override string must not crash — port keeps its default class."""
        inst = ComponentInstance(
            definition_ref="Add",
            position=(0, 0),
            port_signal_classes={"out": "not_a_real_class"},
        )
        item = ComponentItem(inst, add_def)
        # Falls back to model_copy() without update, so the definition's class is kept.
        assert item._port_items["out"].get_port().signal_class == PortSignalClass.DATA


# ── 3. Interface port signal_class stability ──────────────────────────────────


class TestInterfacePortStability:
    """_sync_interface_port_types() must NOT overwrite a user-set signal_class.

    Bug: the method started with a loop that reset all interface ports to DATA.
    This caused any clock/reset assignment to be undone on the next
    revalidate_connections() call (which is wired to every property change).
    """

    def test_clock_class_survives_sync(self, scene):
        iport = InterfacePort(
            name="clk_in",
            direction=InterfaceDirection.INPUT,
            signal_class=PortSignalClass.CLOCK,
        )
        scene._design.add_interface_port(iport)

        scene._sync_interface_port_types()

        assert iport.signal_class == PortSignalClass.CLOCK

    def test_reset_class_survives_sync(self, scene):
        iport = InterfacePort(
            name="rst_in",
            direction=InterfaceDirection.INPUT,
            signal_class=PortSignalClass.RESET,
        )
        scene._design.add_interface_port(iport)

        scene._sync_interface_port_types()

        assert iport.signal_class == PortSignalClass.RESET

    def test_class_survives_revalidate_connections(self, scene):
        """Full revalidate_connections() call must not reset interface port class."""
        iport = InterfacePort(
            name="clk_in",
            direction=InterfaceDirection.INPUT,
            signal_class=PortSignalClass.CLOCK,
        )
        scene._design.add_interface_port(iport)

        scene.revalidate_connections()

        assert iport.signal_class == PortSignalClass.CLOCK


# ── 4. _is_valid_connection_target blocks cross-class ────────────────────────


class TestConnectionTargetValidation:
    """During connection drag, cross-class targets must be rejected."""

    def _arm_drag(self, scene, src_item, src_inst):
        """Set scene state as if the user started a drag from src_item."""
        scene._connection_source_port = src_item
        scene._connection_source_component_id = src_inst.id
        scene._connection_source_interface_port = None

    def test_same_class_is_valid(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)

        self._arm_drag(scene, item1._port_items["out"], inst1)
        assert scene._is_valid_connection_target(item2._port_items["a"])

    def test_clock_to_data_is_blocked(self, scene):
        inst1, item1 = _add(scene, "ClkSrc", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)

        self._arm_drag(scene, item1._port_items["clk_out"], inst1)
        # Add's "a" port is DATA — must be rejected
        assert not scene._is_valid_connection_target(item2._port_items["a"])

    def test_data_to_clock_is_blocked(self, scene, add_def):
        """An output DATA port cannot connect to an input CLOCK port."""
        clk_sink_def = ComponentDefinition(
            name="ClkSink",
            visual=VisualConfig(width=4, height=3),
            ports=[Port(name="clk_in", direction=PortDirection.IN,
                        signal_class=PortSignalClass.CLOCK)],
        )
        scene._library["ClkSink"] = clk_sink_def

        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "ClkSink", 200, 0)

        self._arm_drag(scene, item1._port_items["out"], inst1)
        assert not scene._is_valid_connection_target(item2._port_items["clk_in"])

    def test_self_connection_is_blocked(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)

        self._arm_drag(scene, item1._port_items["out"], inst1)
        assert not scene._is_valid_connection_target(item1._port_items["a"])

    def test_duplicate_connection_is_blocked(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        _connect_direct(scene, inst1, "out", inst2, "a")

        self._arm_drag(scene, item1._port_items["out"], inst1)
        assert not scene._is_valid_connection_target(item2._port_items["a"])


# ── 5. _is_valid_interface_target blocks cross-class ─────────────────────────


class TestInterfaceTargetValidation:
    """Component output → interface output: signal class must match."""

    def _add_output_interface(self, scene, name, signal_class):
        iport = InterfacePort(
            name=name,
            direction=InterfaceDirection.OUTPUT,
            signal_class=signal_class,
        )
        scene._design.add_interface_port(iport)
        # Minimal stub item for validation — the mixin reads get_interface_port()
        from pipeline_designer.presentation.canvas.items import InterfacePortItem
        from pipeline_designer.domain import DEFAULT_GRID
        iface_item = InterfacePortItem(iport, grid=DEFAULT_GRID)
        scene._interface_port_items[iport.id] = iface_item
        return iport, iface_item

    def _arm_drag_from_component(self, scene, port_item, comp_id):
        scene._connection_source_port = port_item
        scene._connection_source_component_id = comp_id
        scene._connection_source_interface_port = None

    def test_data_to_data_interface_is_valid(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)
        _, iface_item = self._add_output_interface(scene, "data_out", PortSignalClass.DATA)

        self._arm_drag_from_component(scene, item1._port_items["out"], inst1.id)
        assert scene._is_valid_interface_target(iface_item)

    def test_data_to_clock_interface_is_blocked(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)
        _, iface_item = self._add_output_interface(scene, "clk_out", PortSignalClass.CLOCK)

        self._arm_drag_from_component(scene, item1._port_items["out"], inst1.id)
        assert not scene._is_valid_interface_target(iface_item)

    def test_clock_to_clock_interface_is_valid(self, scene):
        inst1, item1 = _add(scene, "ClkSrc", 0, 0)
        _, iface_item = self._add_output_interface(scene, "clk_out", PortSignalClass.CLOCK)

        self._arm_drag_from_component(scene, item1._port_items["clk_out"], inst1.id)
        assert scene._is_valid_interface_target(iface_item)


# ── 6 & 7. Existing connections flagged after class change ───────────────────


class TestConnectionMismatchFlagging:
    """After a port's signal class is changed, revalidate_connections() must mark
    any now-mismatched ConnectionItem as invalid and include its ID in
    get_invalid_connection_ids().
    """

    def test_matching_connection_is_valid(self, scene):
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        _connect_direct(scene, inst1, "out", inst2, "a")

        scene.revalidate_connections()

        assert len(scene.get_invalid_connection_ids()) == 0

    def test_mismatch_after_class_change_flagged(self, scene):
        """Changing source port from DATA to CLOCK must flag the existing DATA connection."""
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        _connect_direct(scene, inst1, "out", inst2, "a")

        # Simulate what _on_port_changed does when the user edits signal_class
        src_port_item = item1._port_items["out"]
        src_port_item.get_port().signal_class = PortSignalClass.CLOCK
        inst1.port_signal_classes["out"] = "clock"

        scene.revalidate_connections()

        invalid_ids = scene.get_invalid_connection_ids()
        assert len(invalid_ids) == 1

    def test_invalid_connection_item_is_marked(self, scene):
        """The ConnectionItem._is_invalid flag must be set for a mismatched connection."""
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        conn = _connect_direct(scene, inst1, "out", inst2, "a")

        item1._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK
        inst1.port_signal_classes["out"] = "clock"
        scene.revalidate_connections()

        conn_item = scene._connection_items[conn.id]
        assert conn_item._is_invalid

    def test_only_mismatched_connections_flagged(self, scene, add_def):
        """When multiple connections exist, only the mismatched one turns invalid."""
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        inst3, item3 = _add(scene, "Add", 400, 0)

        conn_bad  = _connect_direct(scene, inst1, "out", inst2, "a")
        conn_good = _connect_direct(scene, inst2, "out", inst3, "a")

        # Make only the first connection a mismatch
        item1._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK
        inst1.port_signal_classes["out"] = "clock"
        scene.revalidate_connections()

        assert conn_bad.id  in scene.get_invalid_connection_ids()
        assert conn_good.id not in scene.get_invalid_connection_ids()

    def test_fixing_class_clears_invalid_flag(self, scene):
        """After restoring matching classes, revalidate must clear the invalid flag."""
        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        conn = _connect_direct(scene, inst1, "out", inst2, "a")

        # Break it
        item1._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK
        inst1.port_signal_classes["out"] = "clock"
        scene.revalidate_connections()
        assert len(scene.get_invalid_connection_ids()) == 1

        # Fix it
        item1._port_items["out"].get_port().signal_class = PortSignalClass.DATA
        inst1.port_signal_classes["out"] = "data"
        scene.revalidate_connections()
        assert len(scene.get_invalid_connection_ids()) == 0


# ── 8. validation_warnings signal ────────────────────────────────────────────


class TestValidationWarningsSignal:
    """revalidate_connections() must emit the validation_warnings signal."""

    def test_no_warnings_for_valid_design(self, scene):
        received = []
        scene.validation_warnings.connect(received.append)

        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        _connect_direct(scene, inst1, "out", inst2, "a")

        scene.revalidate_connections()

        assert received, "Signal was never emitted"
        assert received[-1] == [], "Expected no warning messages"

    def test_warning_emitted_for_mismatch(self, scene):
        received = []
        scene.validation_warnings.connect(received.append)

        inst1, item1 = _add(scene, "Add", 0, 0)
        inst2, item2 = _add(scene, "Add", 200, 0)
        _connect_direct(scene, inst1, "out", inst2, "a")

        item1._port_items["out"].get_port().signal_class = PortSignalClass.CLOCK
        inst1.port_signal_classes["out"] = "clock"
        scene.revalidate_connections()

        assert received, "Signal was never emitted"
        assert len(received[-1]) == 1
        assert "clock" in received[-1][0]
        assert "data" in received[-1][0]
