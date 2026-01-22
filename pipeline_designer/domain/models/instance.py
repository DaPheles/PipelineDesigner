"""Component instance and connection models."""

from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class InterfaceDirection(str, Enum):
    """Direction of an interface port."""

    INPUT = "input"
    OUTPUT = "output"


class InterfacePort(BaseModel):
    """An interface port that exposes internal signals externally.

    Interface ports connect the component's internal logic to external ports,
    allowing the component to be used as a primitive in larger designs.
    """

    id: UUID = Field(default_factory=uuid4, description="Unique interface port ID")
    name: str = Field(..., description="External port name")
    direction: InterfaceDirection = Field(..., description="Port direction")
    data_type: str = Field(default="std_logic_vector", description="Data type")
    position: tuple[int, int] | None = Field(
        default=None, description="Position in grid units (x, y)"
    )
    # Reference to internal component port this interface connects to
    internal_component_id: UUID | None = Field(
        default=None, description="Internal component ID this port connects to"
    )
    internal_port_name: str | None = Field(
        default=None, description="Internal port name this interface connects to"
    )


class PortReference(BaseModel):
    """Reference to a specific port on a component instance or interface port.

    Either component_id/port_name OR interface_port_id should be set, not both.
    - For component ports: set component_id and port_name
    - For interface ports: set interface_port_id (port_name is the interface port name)
    """

    component_id: UUID | None = Field(default=None, description="ID of the component instance")
    port_name: str = Field(..., description="Name of the port")
    interface_port_id: UUID | None = Field(
        default=None, description="ID of the interface port (if connecting to/from interface)"
    )

    def is_interface_port(self) -> bool:
        """Check if this reference points to an interface port."""
        return self.interface_port_id is not None

    def is_component_port(self) -> bool:
        """Check if this reference points to a component port."""
        return self.component_id is not None


class ComponentInstance(BaseModel):
    """An instance of a component placed in a design."""

    id: UUID = Field(default_factory=uuid4, description="Unique instance ID")
    definition_ref: str = Field(..., description="Reference to component definition name")
    position: tuple[float, float] = Field(
        default=(0.0, 0.0), description="Position (x, y) on the canvas"
    )
    generic_values: dict[str, Any] = Field(
        default_factory=dict, description="Values for generic parameters"
    )
    pipeline_stage: int | None = Field(
        default=None, description="Assigned pipeline stage (first stage for composites)"
    )
    instance_name: str | None = Field(
        default=None, description="Optional instance name"
    )
    # Composite component fields
    is_composite: bool = Field(
        default=False, description="Whether this is a composite component"
    )
    stage_count: int = Field(
        default=1, description="Number of stages this component spans"
    )

    def get_display_name(self) -> str:
        """Get the display name for this instance."""
        if self.instance_name:
            return self.instance_name
        return f"{self.definition_ref}_{str(self.id)[:8]}"

    def get_end_stage(self) -> int | None:
        """Get the last stage this component occupies."""
        if self.pipeline_stage is None:
            return None
        return self.pipeline_stage + self.stage_count - 1


class Connection(BaseModel):
    """A connection between two ports."""

    id: UUID = Field(default_factory=uuid4, description="Unique connection ID")
    source: PortReference = Field(..., description="Source port reference")
    target: PortReference = Field(..., description="Target port reference")
    signal_name: str | None = Field(
        default=None, description="Optional signal/wire name"
    )
    waypoints: list[tuple[float, float]] = Field(
        default_factory=list, description="Intermediate routing points"
    )

    def get_display_name(self) -> str:
        """Get the display name for this connection."""
        if self.signal_name:
            return self.signal_name
        return f"wire_{str(self.id)[:8]}"
