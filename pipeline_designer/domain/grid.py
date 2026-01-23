"""Grid configuration for the pipeline designer.

This module defines the grid system used throughout the application.
All component sizes and port positions are specified in grid units,
ensuring consistent alignment across the design.
"""

from pydantic import BaseModel, Field


class GridConfig(BaseModel):
    """Grid configuration - single source of truth for grid settings.

    All measurements in the library (component sizes, port positions)
    are specified in grid units. This configuration defines how those
    units translate to pixels.
    """

    size: int = Field(default=20, description="Grid cell size in pixels")

    def to_pixels(self, grid_units: float | int) -> float:
        """Convert grid units to pixels."""
        return float(grid_units * self.size)

    def to_grid_units(self, pixels: float) -> float:
        """Convert pixels to grid units."""
        return pixels / self.size

    def to_grid_units_int(self, pixels: float) -> int:
        """Convert pixels to grid units (rounded to nearest integer)."""
        return round(pixels / self.size)

    def snap_to_grid(self, pixels: float) -> float:
        """Snap a pixel value to the nearest grid intersection."""
        return round(pixels / self.size) * self.size

    def pos_to_pixels(self, pos: tuple[float, float]) -> tuple[float, float]:
        """Convert a position from grid units to pixels."""
        return (self.to_pixels(pos[0]), self.to_pixels(pos[1]))

    def pos_to_grid(self, pos: tuple[float, float]) -> tuple[float, float]:
        """Convert a position from pixels to grid units."""
        return (self.to_grid_units(pos[0]), self.to_grid_units(pos[1]))


# Default grid configuration instance
DEFAULT_GRID = GridConfig()
