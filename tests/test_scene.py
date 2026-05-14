import pytest
from pipeline_designer.domain.models.component import ComponentDefinition, Port, PortDirection, PortSignalClass
from pipeline_designer.presentation.canvas.scene import DesignScene


@pytest.fixture
def library():
    """Minimal component library with one primitive."""
    add_def = ComponentDefinition(
        name="Add",
        ports=[
            Port(name="a", direction=PortDirection.IN,  position=(0, 1)),
            Port(name="b", direction=PortDirection.IN,  position=(0, 2)),
            Port(name="out", direction=PortDirection.OUT, position=(4, 1)),
        ],
    )
    return {"Add": add_def}


@pytest.fixture
def scene(qapp, library):
    s = DesignScene()
    s.set_library(library)
    return s


def test_add_component_emits_signal(scene, library):
    received = []
    scene.component_added.connect(lambda inst: received.append(inst))
    scene.add_component_from_definition(library["Add"], (0, 0))
    assert len(received) == 1
    assert received[0].definition_ref == "Add"


def test_undo_remove_component(scene, library):
    scene.add_component_from_definition(library["Add"], (0, 0))
    assert len(scene.get_design().components) == 1
    scene.undo()
    assert len(scene.get_design().components) == 0


def test_signal_class_mismatch_blocks_connection(scene, library):
    """A clock→data connection must not be created."""
    clk_def = ComponentDefinition(
        name="ClkGen",
        ports=[Port(name="clk_out", direction=PortDirection.OUT,
                    position=(4, 1), signal_class=PortSignalClass.CLOCK)],
    )
    scene._library["ClkGen"] = clk_def
    scene.add_component_from_definition(clk_def, (0, 0))
    scene.add_component_from_definition(library["Add"], (10, 0))

    design = scene.get_design()
    assert len(design.connections) == 0  # nothing connected yet

    src_inst, tgt_inst = design.components
    src_port = scene._component_items[src_inst.id]._port_items["clk_out"]
    tgt_port = scene._component_items[tgt_inst.id]._port_items["a"]

    is_valid = scene._is_valid_connection_target_for_ports(src_port, tgt_port)
    assert not is_valid
