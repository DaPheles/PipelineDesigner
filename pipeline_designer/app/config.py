"""Application configuration settings."""

from pathlib import Path

from pydantic import BaseModel, Field

from pipeline_designer.domain import DEFAULT_GRID, GridConfig


class WindowConfig(BaseModel):
    """Window configuration settings."""

    width: int = Field(default=1280, description="Window width")
    height: int = Field(default=800, description="Window height")
    title: str = Field(default="Pipeline Designer", description="Window title")


class CanvasConfig(BaseModel):
    """Canvas configuration settings.

    Uses the shared GridConfig for grid settings to ensure consistency
    with component definitions.
    """

    grid: GridConfig = Field(
        default_factory=lambda: DEFAULT_GRID,
        description="Grid configuration",
    )
    snap_to_grid: bool = Field(default=True, description="Enable grid snapping")
    background_color: str = Field(default="#2b2b2b", description="Background color")
    grid_color: str = Field(default="#3a3a3a", description="Grid line color")

    @property
    def grid_size(self) -> int:
        """Get the grid size in pixels."""
        return self.grid.size


class AppConfig(BaseModel):
    """Application configuration."""

    window: WindowConfig = Field(default_factory=WindowConfig)
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    library_path: Path | None = Field(
        default=None, description="Path to component library"
    )
    recent_files: list[Path] = Field(
        default_factory=list, description="Recently opened files"
    )
    max_recent_files: int = Field(default=10, description="Maximum recent files to track")

    def add_recent_file(self, path: Path) -> None:
        """Add a file to the recent files list."""
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[: self.max_recent_files]

    @classmethod
    def get_default_library_path(cls) -> Path:
        """Get the default library path."""
        return Path(__file__).parent.parent.parent / "library"
