"""Component definition models for pipeline designer.

All sizes and positions are specified in grid units (integers).
Grid units are converted to pixels using the GridConfig.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeline_designer.domain.grid import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models.behavior import (
    ComponentBehavior,
    SignalKind,
    SignalType,
    _LEGACY_KIND_MAP,
)


class PortDirection(str, Enum):
    """Direction of a port on a component."""

    IN    = "in"
    OUT   = "out"
    INOUT = "inout"


class PortSignalClass(str, Enum):
    """Semantic classification of a port's signal, driving connection rules and VHDL generation.

    - CLOCK:   Must only connect to other clock ports; driven by clock routing.
    - RESET:   Controls reset behaviour; must only connect to other reset ports.
    - CONTROL: Carries control/enable signals; must have a defined reset condition in VHDL.
    - DATA:    General data path; no mandatory reset condition.
    """

    CLOCK   = "clock"
    RESET   = "reset"
    CONTROL = "control"
    DATA    = "data"


class Port(BaseModel):
    """A port on a component definition.

    Port positions are in grid units relative to the component's top-left
    corner.  Signal type information lives entirely in ``signal_type``; the
    old ``data_type`` / ``vector_range`` fields are accepted on load for
    backward compatibility and converted transparently.
    """

    name:         str             = Field(..., description="Port name")
    direction:    PortDirection   = Field(..., description="Port direction")
    signal_class: PortSignalClass = Field(
        default=PortSignalClass.DATA,
        description="Signal classification: clock, reset, control, or data",
    )
    signal_type:  SignalType      = Field(
        default_factory=lambda: SignalType(kind="std_logic"),
        description="Signal type (kind, width, lsb)",
    )
    position:     tuple[int, int] | None = Field(default=None, description="Grid-unit position (x, y)")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        """Convert old data_type/vector_range fields."""
        if not isinstance(data, dict):
            return data

        data = dict(data)  # don't mutate the original

        if "signal_type" in data:
            return data

        raw_kind   = data.pop("data_type", "std_logic") or "std_logic"
        raw_range  = data.pop("vector_range", None)

        # Normalise legacy kind names
        kind = _LEGACY_KIND_MAP.get(raw_kind, raw_kind)

        if raw_range and ":" in str(raw_range):
            parts    = str(raw_range).split(":", 1)
            msb_str  = parts[0].strip()
            lsb_str  = parts[1].strip()
            try:
                msb   = int(msb_str)
                lsb   = int(lsb_str)
                width = str(msb - lsb + 1)
                lsb_s = str(lsb)
            except ValueError:
                # Generic expression — keep raw but compute width symbolically
                width = f"({msb_str})-({lsb_str})+1"
                lsb_s = lsb_str
            data["signal_type"] = {"kind": kind, "width": width, "lsb": lsb_s}
        else:
            data["signal_type"] = {"kind": kind}

        return data

    def get_pixel_position(self, grid: GridConfig | None = None) -> tuple[float, float]:
        if grid is None:
            grid = DEFAULT_GRID
        if self.position is None:
            return (0.0, 0.0)
        return (grid.to_pixels(self.position[0]), grid.to_pixels(self.position[1]))


class Generic(BaseModel):
    """A generic parameter for a component.

    Use ``data_type = "signal_kind"`` for generics whose value is a
    ``SignalKind`` name (e.g. ``"sfixed"`` / ``"ufixed"``).  The port's
    ``signal_type.kind`` field can then reference this generic by name.
    """

    name:          str             = Field(..., description="Generic parameter name")
    data_type:     str             = Field(default="integer", description="Data type (integer, signal_kind, …)")
    default_value: Any             = Field(default=None, description="Default value")
    options:       list[str] | None = Field(default=None, description="Allowed values; renders as a drop-down when set")


class VisualConfig(BaseModel):
    """Visual configuration for a component (grid units)."""

    width:  int = Field(default=6, description="Width in grid units")
    height: int = Field(default=4, description="Height in grid units")
    color:  str = Field(default="#4a90d9", description="Background color (hex)")

    def get_pixel_size(self, grid: GridConfig | None = None) -> tuple[float, float]:
        if grid is None:
            grid = DEFAULT_GRID
        return (grid.to_pixels(self.width), grid.to_pixels(self.height))


class ComponentDefinition(BaseModel):
    """Definition of a reusable component type."""

    name:        str               = Field(..., description="Component name")
    category:    str               = Field(default="general",  description="Component category")
    description: str               = Field(default="",        description="Component description")
    ports:       list[Port]        = Field(default_factory=list)
    generics:    list[Generic]     = Field(default_factory=list)
    visual:      VisualConfig      = Field(default_factory=VisualConfig)
    latency:     int               = Field(default=0, description="Pipeline latency in cycles")
    behavior:    ComponentBehavior = Field(default_factory=ComponentBehavior)

    def get_input_ports(self) -> list[Port]:
        return [p for p in self.ports if p.direction == PortDirection.IN]

    def get_output_ports(self) -> list[Port]:
        return [p for p in self.ports if p.direction == PortDirection.OUT]

    def get_port_by_name(self, name: str) -> Port | None:
        for port in self.ports:
            if port.name == name:
                return port
        return None

    def validate_port_positions(self) -> list[str]:
        errors = []
        for port in self.ports:
            if port.position is not None:
                x, y = port.position
                if x < 0 or x > self.visual.width:
                    errors.append(
                        f"Port '{port.name}' x={x} outside width {self.visual.width}"
                    )
                if y < 0 or y > self.visual.height:
                    errors.append(
                        f"Port '{port.name}' y={y} outside height {self.visual.height}"
                    )
        return errors
