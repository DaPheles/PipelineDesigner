"""Shared fixtures for presentation-layer tests."""

import pytest

from pipeline_designer.domain.models.component import (
    ComponentDefinition,
    Port,
    PortDirection,
    PortSignalClass,
    VisualConfig,
)
from pipeline_designer.presentation.canvas.scene import DesignScene


@pytest.fixture
def add_def():
    """A minimal two-input, one-output combinational component definition."""
    return ComponentDefinition(
        name="Add",
        visual=VisualConfig(width=6, height=4),
        ports=[
            Port(name="a",   direction=PortDirection.IN,  signal_class=PortSignalClass.DATA),
            Port(name="b",   direction=PortDirection.IN,  signal_class=PortSignalClass.DATA),
            Port(name="out", direction=PortDirection.OUT, signal_class=PortSignalClass.DATA),
        ],
    )


@pytest.fixture
def clk_src_def():
    """A component whose sole output is a clock signal."""
    return ComponentDefinition(
        name="ClkSrc",
        visual=VisualConfig(width=4, height=3),
        ports=[
            Port(name="clk_out", direction=PortDirection.OUT, signal_class=PortSignalClass.CLOCK),
        ],
    )


@pytest.fixture
def library(add_def, clk_src_def):
    return {"Add": add_def, "ClkSrc": clk_src_def}


@pytest.fixture
def scene(qapp, library):
    """A fresh DesignScene with the minimal test library, torn down after each test."""
    s = DesignScene()
    s.set_library(library)
    yield s
    s.clear()
