"""Library loader for component definitions."""

import json
from pathlib import Path

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import ComponentDefinition


class LibraryLoader:
    """Loads and manages component definitions from JSON files.

    Component definitions use grid units for all sizes and positions.
    The loader validates that all values are proper grid-aligned integers.
    """

    def __init__(
        self,
        library_path: Path | None = None,
        grid: GridConfig | None = None,
    ):
        """Initialize the library loader.

        Args:
            library_path: Path to the library directory. If None, uses default.
            grid: Grid configuration for validation. Uses DEFAULT_GRID if None.
        """
        if library_path is None:
            library_path = Path(__file__).parent.parent.parent.parent / "library"
        self.library_path = library_path
        self.grid = grid or DEFAULT_GRID
        self._components: dict[str, ComponentDefinition] = {}
        self._categories: dict[str, list[str]] = {}

    def load_all(self) -> None:
        """Load all component definitions from the library directory."""
        self._components.clear()
        self._categories.clear()

        if not self.library_path.exists():
            return

        for json_file in self.library_path.rglob("*.json"):
            try:
                self._load_component_file(json_file)
            except Exception as e:
                print(f"Warning: Failed to load {json_file}: {e}")

    def _load_component_file(self, file_path: Path) -> None:
        """Load a single component definition file."""
        with open(file_path, "r") as f:
            data = json.load(f)

        component = ComponentDefinition.model_validate(data)

        validation_errors = component.validate_port_positions()
        if validation_errors:
            for error in validation_errors:
                print(f"Warning: {file_path.name}: {error}")

        self._components[component.name] = component

        if component.category not in self._categories:
            self._categories[component.category] = []
        self._categories[component.category].append(component.name)

    def get_component(self, name: str) -> ComponentDefinition | None:
        """Get a component definition by name."""
        return self._components.get(name)

    def get_all_components(self) -> list[ComponentDefinition]:
        """Get all loaded component definitions."""
        return list(self._components.values())

    def get_categories(self) -> list[str]:
        """Get all component categories."""
        return list(self._categories.keys())

    def get_components_by_category(self, category: str) -> list[ComponentDefinition]:
        """Get all components in a category."""
        names = self._categories.get(category, [])
        return [self._components[name] for name in names if name in self._components]

    def reload(self) -> None:
        """Reload all component definitions."""
        self.load_all()
