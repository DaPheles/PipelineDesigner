"""Component definition models for pipeline designer.

All sizes and positions are specified in grid units (integers).
Grid units are converted to pixels using the GridConfig.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from pipeline_designer.domain.grid import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models.behavior import ComponentBehavior


class PortDirection(str, Enum):
    """Direction of a port on a component."""

    IN = "in"
    OUT = "out"
    INOUT = "inout"


class Port(BaseModel):
    """A port on a component definition.

    Port positions are specified in grid units relative to the component's
    top-left corner. This ensures ports always land on grid intersections.
    """

    name: str = Field(..., description="Port name")
    direction: PortDirection = Field(..., description="Port direction")
    data_type: str = Field(default="std_logic", description="Data type of the port")
    position: tuple[int, int] | None = Field(
        default=None,
        description="Position in grid units (x, y) relative to component origin",
    )
    is_clock: bool = Field(default=False, description="Whether this is a clock port")
    is_reset: bool = Field(default=False, description="Whether this is a reset port")
    vector_range: str | None = Field(
        default=None,
        description=(
            "Vector index range as 'MSB:LSB' (e.g. '7:0' or 'WIDTH-1:0'). "
            "Only meaningful for std_logic_vector and similar array types. "
            "Maps to VHDL: std_logic_vector(MSB downto LSB)."
        ),
    )

    def get_pixel_position(self, grid: GridConfig | None = None) -> tuple[float, float]:
        """Get the port position in pixels.

        Args:
            grid: Grid configuration. Uses DEFAULT_GRID if not provided.

        Returns:
            Position in pixels (x, y).
        """
        if grid is None:
            grid = DEFAULT_GRID
        if self.position is None:
            return (0.0, 0.0)
        return (grid.to_pixels(self.position[0]), grid.to_pixels(self.position[1]))


class Generic(BaseModel):
    """A generic parameter for a component."""

    name: str = Field(..., description="Generic parameter name")
    data_type: str = Field(default="integer", description="Data type of the generic")
    default_value: Any = Field(default=None, description="Default value for the generic")


class VisualConfig(BaseModel):
    """Visual configuration for a component.

    Width and height are specified in grid units to ensure components
    align properly on the grid.
    """

    width: int = Field(default=6, description="Width in grid units")
    height: int = Field(default=4, description="Height in grid units")
    color: str = Field(default="#4a90d9", description="Background color (hex)")

    def get_pixel_size(self, grid: GridConfig | None = None) -> tuple[float, float]:
        """Get the component size in pixels.

        Args:
            grid: Grid configuration. Uses DEFAULT_GRID if not provided.

        Returns:
            Size in pixels (width, height).
        """
        if grid is None:
            grid = DEFAULT_GRID
        return (grid.to_pixels(self.width), grid.to_pixels(self.height))


class ComponentDefinition(BaseModel):
    """Definition of a reusable component type.

    All sizes and positions are in grid units for consistent alignment.
    """

    name: str = Field(..., description="Component name")
    category: str = Field(default="general", description="Component category")
    description: str = Field(default="", description="Component description")
    ports: list[Port] = Field(default_factory=list, description="List of ports")
    generics: list[Generic] = Field(
        default_factory=list, description="List of generic parameters"
    )
    visual: VisualConfig = Field(
        default_factory=VisualConfig, description="Visual configuration"
    )
    latency: int = Field(default=0, description="Pipeline latency in clock cycles")
    behavior: ComponentBehavior = Field(
        default_factory=ComponentBehavior,
        description="Functional pseudo-code description with typed fixed-point port annotations",
    )

    def get_input_ports(self) -> list[Port]:
        """Get all input ports."""
        return [p for p in self.ports if p.direction == PortDirection.IN]

    def get_output_ports(self) -> list[Port]:
        """Get all output ports."""
        return [p for p in self.ports if p.direction == PortDirection.OUT]

    def get_port_by_name(self, name: str) -> Port | None:
        """Get a port by name."""
        for port in self.ports:
            if port.name == name:
                return port
        return None

    def validate_port_positions(self) -> list[str]:
        """Validate that all port positions are within component bounds.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []
        for port in self.ports:
            if port.position is not None:
                x, y = port.position
                if x < 0 or x > self.visual.width:
                    errors.append(
                        f"Port '{port.name}' x position {x} is outside "
                        f"component width {self.visual.width}"
                    )
                if y < 0 or y > self.visual.height:
                    errors.append(
                        f"Port '{port.name}' y position {y} is outside "
                        f"component height {self.visual.height}"
                    )
        return errors
