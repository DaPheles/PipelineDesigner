"""Pipeline stage model."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Stage(BaseModel):
    """A pipeline stage defined by register boundaries.

    Stages are vertical boundaries in the pipeline design, each representing
    one clock cycle of latency. Registers placed within a stage are considered
    to be at the same pipeline depth.

    All position and dimension values are stored in grid units.
    """

    id: UUID = Field(default_factory=uuid4, description="Unique stage identifier")
    index: int = Field(..., description="Stage index (1-based, left to right)")
    x_position: float = Field(..., description="X position in grid units")
    width: float = Field(..., description="Width in grid units (matches register width)")
    register_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of registers belonging to this stage",
    )
    # Extra X space added to accommodate sub-component spacing requirements.
    # The stage may move left by at most this amount (down to x_position - additional_offset).
    additional_offset: float = Field(
        default=0.0,
        description="Extra X offset in grid units added for sub-component spacing",
    )

    def contains_x(self, x: float) -> bool:
        """Check if an x coordinate falls within this stage."""
        return self.x_position <= x < self.x_position + self.width

    def overlaps(self, x: float, width: float) -> bool:
        """Check if a rectangle overlaps with this stage."""
        return not (x + width <= self.x_position or x >= self.x_position + self.width)

    def center_x(self) -> float:
        """Get the center x coordinate of this stage."""
        return self.x_position + self.width / 2
