"""Application configuration settings."""

import base64
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline_designer.domain import DEFAULT_GRID, GridConfig

log = logging.getLogger(__name__)


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

    # Session state — populated on close, restored on next launch
    last_file: Path | None = Field(
        default=None, description="Last opened design file"
    )
    session_geometry: str | None = Field(
        default=None, description="Window geometry (base64-encoded QByteArray)"
    )
    session_state: str | None = Field(
        default=None, description="Dock/toolbar layout (base64-encoded QByteArray)"
    )
    view_zoom: float = Field(default=1.0, description="Canvas zoom factor")
    view_scroll_x: float = Field(default=0.0, description="Canvas viewport centre X in scene coordinates")
    view_scroll_y: float = Field(default=0.0, description="Canvas viewport centre Y in scene coordinates")

    # Tools panel open/closed states
    panel_properties: bool = Field(default=True, description="Properties panel open")
    panel_simulation: bool = Field(default=True, description="Simulation panel open")
    panel_vhdl_export: bool = Field(default=False, description="VHDL Export panel open")

    def add_recent_file(self, path: Path) -> None:
        """Add a file to the recent files list."""
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[: self.max_recent_files]

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def encode_geometry(self, qbytearray) -> None:
        """Store a QByteArray from saveGeometry() as a base64 string."""
        self.session_geometry = base64.b64encode(bytes(qbytearray)).decode()

    def decode_geometry(self):
        """Return a QByteArray for restoreGeometry(), or None."""
        if not self.session_geometry:
            return None
        from PySide6.QtCore import QByteArray
        return QByteArray(base64.b64decode(self.session_geometry))

    def encode_state(self, qbytearray) -> None:
        """Store a QByteArray from saveState() as a base64 string."""
        self.session_state = base64.b64encode(bytes(qbytearray)).decode()

    def decode_state(self):
        """Return a QByteArray for restoreState(), or None."""
        if not self.session_state:
            return None
        from PySide6.QtCore import QByteArray
        return QByteArray(base64.b64decode(self.session_state))

    # ── Persistence ───────────────────────────────────────────────────────────

    @classmethod
    def get_config_path(cls) -> Path:
        return Path.home() / ".config" / "pipeline_designer" / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        path = cls.get_config_path()
        try:
            return cls.model_validate_json(path.read_text())
        except FileNotFoundError:
            return cls()
        except Exception:
            log.warning("config.json is corrupt or unreadable — using defaults")
            return cls()

    def save(self) -> None:
        path = self.get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def get_default_library_path(cls) -> Path:
        """Get the default library path."""
        return Path(__file__).parent.parent.parent / "library"
