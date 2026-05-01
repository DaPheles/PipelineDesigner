"""Library loader for component definitions."""

import json
from pathlib import Path

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import (
    ComponentDefinition,
    Design,
    Generic,
    InterfaceDirection,
    Port,
    PortDirection,
    VisualConfig,
)


class LibraryLoader:
    """Loads and manages component definitions from JSON files.

    Component definitions use grid units for all sizes and positions.
    The loader validates that all values are proper grid-aligned integers.

    Also loads composite components from library/components which are
    Design files that can be used as reusable components.
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
        # Store original designs for composite components
        self._composite_designs: dict[str, Design] = {}
        # Track file path for each primitive (non-composite) for save/delete
        self._primitive_file_paths: dict[str, Path] = {}

    def load_all(self) -> None:
        """Load all component definitions from the library directory."""
        self._components.clear()
        self._categories.clear()
        self._composite_designs.clear()
        self._primitive_file_paths.clear()

        if not self.library_path.exists():
            return

        # Load primitives first
        primitives_path = self.library_path / "primitives"
        if primitives_path.exists():
            for json_file in primitives_path.glob("*.json"):
                try:
                    self._load_component_file(json_file)
                except Exception as e:
                    print(f"Warning: Failed to load {json_file}: {e}")

        # Load composite components from library/components
        components_path = self.library_path / "components"
        if components_path.exists():
            for json_file in components_path.glob("*.json"):
                try:
                    self._load_composite_component(json_file)
                except Exception as e:
                    print(f"Warning: Failed to load composite {json_file}: {e}")

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
        self._primitive_file_paths[component.name] = file_path

        if component.category not in self._categories:
            self._categories[component.category] = []
        self._categories[component.category].append(component.name)

    def _load_composite_component(self, file_path: Path) -> None:
        """Load a composite component from a Design file.

        Converts a Design with component_config.enabled=True into a
        ComponentDefinition that can be used like a primitive.
        """
        with open(file_path, "r") as f:
            data = json.load(f)

        design = Design.model_validate(data)

        # Only load if configured as a component
        if not design.is_component:
            return

        # Store the original design for later use
        self._composite_designs[design.name] = design

        # Convert interface ports to component ports
        ports: list[Port] = []
        config = design.component_config

        # Use the design's visual extent for sizing
        visual_width = design.visual.width if design.visual.width > 0 else config.width
        visual_height = design.visual.height if design.visual.height > 0 else config.height

        # Calculate port positions
        input_ports = design.get_input_interfaces()
        output_ports = design.get_output_interfaces()

        for i, iface in enumerate(input_ports):
            y_pos = self._calculate_port_y(i, len(input_ports), visual_height)
            port = Port(
                name=iface.name,
                direction=PortDirection.IN,
                data_type=iface.data_type,
                position=(0, y_pos) if iface.position is None else (0, iface.position[1]),
            )
            ports.append(port)

        for i, iface in enumerate(output_ports):
            y_pos = self._calculate_port_y(i, len(output_ports), visual_height)
            port = Port(
                name=iface.name,
                direction=PortDirection.OUT,
                data_type=iface.data_type,
                position=(visual_width, y_pos) if iface.position is None else (visual_width, iface.position[1]),
            )
            ports.append(port)

        # Use the design's visual extent for the component size
        # This ensures the component is large enough to show internal structure
        visual_width = design.visual.width if design.visual.width > 0 else config.width
        visual_height = design.visual.height if design.visual.height > 0 else config.height

        # Create component definition
        component = ComponentDefinition(
            name=design.name,
            category=config.category,
            description=config.description,
            ports=ports,
            generics=[],  # Composite components inherit generics from sub-components
            visual=VisualConfig(
                width=visual_width,
                height=visual_height,
                color=config.color,
            ),
            latency=design.latency,
        )

        self._components[component.name] = component

        if component.category not in self._categories:
            self._categories[component.category] = []
        self._categories[component.category].append(component.name)

    def _calculate_port_y(self, index: int, total: int, height: int) -> int:
        """Calculate Y position for auto-placed ports."""
        if total == 0:
            return height // 2
        if total == 1:
            return height // 2
        # Distribute evenly
        spacing = (height - 2) // (total + 1)
        return 1 + spacing * (index + 1)

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

    def is_composite(self, name: str) -> bool:
        """Check if a component is a composite (has a design)."""
        return name in self._composite_designs

    def get_composite_design(self, name: str) -> Design | None:
        """Get the underlying design for a composite component."""
        return self._composite_designs.get(name)

    def get_all_composite_designs(self) -> dict[str, Design]:
        """Get all composite component designs."""
        return self._composite_designs.copy()

    # ------------------------------------------------------------------ #
    # Primitive CRUD (non-composite components only)                       #
    # ------------------------------------------------------------------ #

    def get_primitive_names(self) -> list[str]:
        """Return names of all loaded primitives (non-composite)."""
        return sorted(
            name
            for name in self._components
            if name not in self._composite_designs
        )

    def get_primitive_file_path(self, name: str) -> Path | None:
        """Return the JSON file path for a primitive, or None if unknown."""
        return self._primitive_file_paths.get(name)

    def save_primitive(
        self,
        component: ComponentDefinition,
        file_path: Path | None = None,
    ) -> Path:
        """Persist a primitive definition to JSON and update in-memory state.

        If *file_path* is not given, the existing path for this component name
        is reused; otherwise a new file is created under library/primitives/.
        """
        if file_path is None:
            file_path = self._primitive_file_paths.get(component.name)
        if file_path is None:
            safe = component.name.lower().replace(" ", "_")
            file_path = self.library_path / "primitives" / f"{safe}.json"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        data = component.model_dump(mode="json", exclude_none=True)
        file_path.write_text(json.dumps(data, indent=4))

        # Update in-memory registry
        old_name = next(
            (n for n, p in self._primitive_file_paths.items() if p == file_path),
            None,
        )
        if old_name and old_name != component.name:
            # Component was renamed — remove old entry
            self._components.pop(old_name, None)
            self._primitive_file_paths.pop(old_name, None)
            for cat_list in self._categories.values():
                if old_name in cat_list:
                    cat_list.remove(old_name)

        self._components[component.name] = component
        self._primitive_file_paths[component.name] = file_path

        cat = component.category
        if cat not in self._categories:
            self._categories[cat] = []
        if component.name not in self._categories[cat]:
            self._categories[cat].append(component.name)

        return file_path

    def delete_primitive(self, name: str) -> bool:
        """Remove a primitive from the library and delete its JSON file.

        Returns True if the primitive existed and was removed.
        """
        if name in self._composite_designs:
            return False  # refuse to delete composites this way

        component = self._components.pop(name, None)
        file_path = self._primitive_file_paths.pop(name, None)

        if component:
            cat_list = self._categories.get(component.category, [])
            if name in cat_list:
                cat_list.remove(name)

        if file_path and file_path.exists():
            file_path.unlink()

        return component is not None
