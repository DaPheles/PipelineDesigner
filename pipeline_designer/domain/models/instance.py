"""Component instance and connection models."""

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PortReference(BaseModel):
    """Reference to a specific port on a component instance."""

    component_id: UUID = Field(..., description="ID of the component instance")
    port_name: str = Field(..., description="Name of the port")


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
        default=None, description="Assigned pipeline stage"
    )
    instance_name: str | None = Field(
        default=None, description="Optional instance name"
    )

    def get_display_name(self) -> str:
        """Get the display name for this instance."""
        if self.instance_name:
            return self.instance_name
        return f"{self.definition_ref}_{str(self.id)[:8]}"


class Connection(BaseModel):
    """A connection between two ports."""

    id: UUID = Field(default_factory=uuid4, description="Unique connection ID")
    source: PortReference = Field(..., description="Source port reference")
    target: PortReference = Field(..., description="Target port reference")
    waypoints: list[tuple[float, float]] = Field(
        default_factory=list, description="Intermediate routing points"
    )
