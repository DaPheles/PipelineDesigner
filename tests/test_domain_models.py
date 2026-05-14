from pipeline_designer.domain.models.component import Port, PortDirection, PortSignalClass
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import ComponentInstance, Connection, PortReference


def test_port_signal_class_default():
    port = Port(name="d", direction=PortDirection.IN)
    assert port.signal_class == PortSignalClass.DATA



def test_design_add_remove_component():
    design = Design()
    inst = ComponentInstance(definition_ref="Add", position=(0, 0))
    design.add_component(inst)
    assert design.get_component_by_id(inst.id) is inst
    design.remove_component(inst.id)
    assert design.get_component_by_id(inst.id) is None


def test_design_remove_component_removes_connections():
    design = Design()
    a = ComponentInstance(definition_ref="Add", position=(0, 0))
    b = ComponentInstance(definition_ref="Mul", position=(5, 0))
    design.add_component(a)
    design.add_component(b)
    conn = Connection(
        source=PortReference(component_id=a.id, port_name="out"),
        target=PortReference(component_id=b.id, port_name="in"),
    )
    design.add_connection(conn)
    assert len(design.connections) == 1
    design.remove_component(a.id)
    assert len(design.connections) == 0


def test_port_signal_class_serialisation_roundtrip():
    from pipeline_designer.domain.models.instance import ComponentInstance
    inst = ComponentInstance(
        definition_ref="Reg",
        position=(0, 0),
        port_signal_classes={"clk": "clock", "d": "data"},
    )
    raw = inst.model_dump_json()
    restored = ComponentInstance.model_validate_json(raw)
    assert restored.port_signal_classes == {"clk": "clock", "d": "data"}
