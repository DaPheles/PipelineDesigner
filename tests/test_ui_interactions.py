import pytest
from PySide6.QtCore import Qt, QPointF
from pipeline_designer.presentation.canvas.scene import DesignScene
from pipeline_designer.presentation.canvas.view import DesignView


@pytest.fixture
def view(qtbot, library):
    scene = DesignScene()
    scene.set_library(library)
    v = DesignView(scene)
    qtbot.addWidget(v)
    v.show()
    return v, scene


def test_drop_component_onto_canvas(qtbot, view, library):
    """Simulate a component being dropped from the palette."""
    v, scene = view
    with qtbot.waitSignal(scene.component_added, timeout=1000):
        scene.add_component_from_definition(library["Add"], (5, 5))

    assert len(scene.get_design().components) == 1


def test_property_editor_shows_port_signal_class(qtbot, view, library):
    """Selecting a port must populate the property editor with its signal class."""
    from pipeline_designer.presentation.panels.property_editor import PropertyEditor
    v, scene = view
    editor = PropertyEditor()
    qtbot.addWidget(editor)

    scene.add_component_from_definition(library["Add"], (5, 5))
    inst = scene.get_design().components[0]
    comp_item = scene._component_items[inst.id]
    port_item = comp_item._port_items["a"]

    editor.show_port(port_item)
    # Find the signal_class combo
    combo = editor.findChild(type(editor._signal_class_combo), "signal_class_combo")
    assert combo.currentText().lower() == "data"


def test_invalid_connection_turns_red_after_class_change(qtbot, view, library):
    """After changing a port to clock, an existing data connection must be invalid."""
    v, scene = view

    # Place two Add components and connect them
    scene.add_component_from_definition(library["Add"], (0, 0))
    scene.add_component_from_definition(library["Add"], (10, 0))
    insts = scene.get_design().components

    # Wire out→a directly via scene API
    src = scene._component_items[insts[0].id]._port_items["out"]
    tgt = scene._component_items[insts[1].id]._port_items["a"]
    scene._create_connection(src, insts[0].id, tgt)
    assert len(scene.get_design().connections) == 1

    # Change source port class to clock
    src.get_port().signal_class = PortSignalClass.CLOCK
    insts[0].port_signal_classes["out"] = "clock"
    scene.revalidate_connections()

    invalid_ids = scene.get_invalid_connection_ids()
    assert len(invalid_ids) == 1
