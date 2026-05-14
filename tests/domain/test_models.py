"""Domain model tests — pure Python, no Qt required.

Covers the signal_class persistence and model invariants that were the root
cause of the bugs fixed during the session:
- per-instance port_signal_classes field
- signal_class serialisation roundtrip
- design mutation (add/remove components and connections)
"""

import json

import pytest

from pipeline_designer.domain.models.component import (
    Port,
    PortDirection,
    PortSignalClass,
    ComponentDefinition,
    VisualConfig,
)
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import (
    ComponentInstance,
    Connection,
    InterfaceDirection,
    InterfacePort,
    PortReference,
)


# ── PortSignalClass ───────────────────────────────────────────────────────────


class TestPortSignalClass:
    def test_values_are_strings(self):
        assert PortSignalClass.CLOCK.value == "clock"
        assert PortSignalClass.RESET.value == "reset"
        assert PortSignalClass.CONTROL.value == "control"
        assert PortSignalClass.DATA.value == "data"

    def test_is_string_subclass(self):
        assert isinstance(PortSignalClass.CLOCK, str)

    def test_round_trip_from_string(self):
        assert PortSignalClass("clock") == PortSignalClass.CLOCK

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PortSignalClass("banana")


# ── Port model ────────────────────────────────────────────────────────────────


class TestPort:
    def test_default_signal_class_is_data(self):
        port = Port(name="d", direction=PortDirection.IN)
        assert port.signal_class == PortSignalClass.DATA

    def test_explicit_signal_class(self):
        port = Port(name="clk", direction=PortDirection.IN, signal_class=PortSignalClass.CLOCK)
        assert port.signal_class == PortSignalClass.CLOCK

    def test_model_copy_is_independent(self):
        """model_copy() must produce a separate object — mutating it must not affect original."""
        original = Port(name="out", direction=PortDirection.OUT, signal_class=PortSignalClass.DATA)
        copy = original.model_copy(update={"signal_class": PortSignalClass.CLOCK})
        assert original.signal_class == PortSignalClass.DATA
        assert copy.signal_class == PortSignalClass.CLOCK


# ── ComponentInstance.port_signal_classes ─────────────────────────────────────


class TestComponentInstancePortSignalClasses:
    def test_default_is_empty_dict(self):
        inst = ComponentInstance(definition_ref="Add", position=(0, 0))
        assert inst.port_signal_classes == {}

    def test_override_stored_and_read_back(self):
        inst = ComponentInstance(
            definition_ref="Reg",
            position=(0, 0),
            port_signal_classes={"clk": "clock", "d": "data"},
        )
        assert inst.port_signal_classes["clk"] == "clock"
        assert inst.port_signal_classes["d"] == "data"

    def test_serialisation_roundtrip(self):
        """port_signal_classes must survive a JSON roundtrip."""
        inst = ComponentInstance(
            definition_ref="Reg",
            position=(0, 0),
            port_signal_classes={"clk": "clock", "rst": "reset"},
        )
        raw = inst.model_dump_json()
        restored = ComponentInstance.model_validate_json(raw)
        assert restored.port_signal_classes == {"clk": "clock", "rst": "reset"}

    def test_serialised_json_contains_field(self):
        inst = ComponentInstance(
            definition_ref="Foo",
            position=(0, 0),
            port_signal_classes={"out": "control"},
        )
        data = json.loads(inst.model_dump_json())
        assert "port_signal_classes" in data
        assert data["port_signal_classes"]["out"] == "control"

    def test_empty_overrides_still_serialised(self):
        """An empty dict must be preserved (not dropped) so loading is stable."""
        inst = ComponentInstance(definition_ref="Foo", position=(0, 0))
        data = json.loads(inst.model_dump_json())
        assert "port_signal_classes" in data
        assert data["port_signal_classes"] == {}


# ── InterfacePort ─────────────────────────────────────────────────────────────


class TestInterfacePort:
    def test_default_signal_class_is_data(self):
        port = InterfacePort(name="in0", direction=InterfaceDirection.INPUT)
        assert port.signal_class == PortSignalClass.DATA

    def test_explicit_clock_class(self):
        port = InterfacePort(
            name="clk", direction=InterfaceDirection.INPUT,
            signal_class=PortSignalClass.CLOCK,
        )
        assert port.signal_class == PortSignalClass.CLOCK

    def test_signal_class_survives_design_roundtrip(self):
        """InterfacePort.signal_class must persist through Design JSON serialisation."""
        design = Design(name="TestDesign")
        iport = InterfacePort(
            name="clk_in",
            direction=InterfaceDirection.INPUT,
            signal_class=PortSignalClass.CLOCK,
        )
        design.add_interface_port(iport)

        raw = design.model_dump_json()
        restored = Design.model_validate_json(raw)

        restored_port = restored.get_interface_port_by_id(iport.id)
        assert restored_port is not None
        assert restored_port.signal_class == PortSignalClass.CLOCK


# ── Design mutations ──────────────────────────────────────────────────────────


class TestDesignMutations:
    def _make_design_with_two_components(self):
        design = Design()
        a = ComponentInstance(definition_ref="Add", position=(0, 0))
        b = ComponentInstance(definition_ref="Mul", position=(5, 0))
        design.add_component(a)
        design.add_component(b)
        return design, a, b

    def test_add_component_is_findable(self):
        design, a, _ = self._make_design_with_two_components()
        assert design.get_component_by_id(a.id) is a

    def test_remove_component_removes_it(self):
        design, a, _ = self._make_design_with_two_components()
        design.remove_component(a.id)
        assert design.get_component_by_id(a.id) is None

    def test_remove_component_cascades_to_connections(self):
        """Removing a component must also remove all its connections."""
        design, a, b = self._make_design_with_two_components()
        conn = Connection(
            source=PortReference(component_id=a.id, port_name="out"),
            target=PortReference(component_id=b.id, port_name="in"),
        )
        design.add_connection(conn)
        assert len(design.connections) == 1

        design.remove_component(a.id)
        assert len(design.connections) == 0

    def test_design_serialisation_preserves_connections(self):
        design, a, b = self._make_design_with_two_components()
        conn = Connection(
            source=PortReference(component_id=a.id, port_name="out"),
            target=PortReference(component_id=b.id, port_name="in"),
        )
        design.add_connection(conn)

        raw = design.model_dump_json()
        restored = Design.model_validate_json(raw)
        assert len(restored.connections) == 1
        assert restored.connections[0].source.component_id == a.id

    def test_model_copy_for_save_filtering(self):
        """Simulate the save-time connection-drop pattern used in _save_to_file."""
        design, a, b = self._make_design_with_two_components()
        conn_good = Connection(
            source=PortReference(component_id=a.id, port_name="out"),
            target=PortReference(component_id=b.id, port_name="in_a"),
        )
        conn_bad = Connection(
            source=PortReference(component_id=a.id, port_name="out2"),
            target=PortReference(component_id=b.id, port_name="in_b"),
        )
        design.add_connection(conn_good)
        design.add_connection(conn_bad)

        invalid_ids = {conn_bad.id}
        filtered = design.model_copy(
            update={"connections": [c for c in design.connections if c.id not in invalid_ids]}
        )

        assert len(filtered.connections) == 1
        assert filtered.connections[0].id == conn_good.id
        # Original design must be unchanged
        assert len(design.connections) == 2
