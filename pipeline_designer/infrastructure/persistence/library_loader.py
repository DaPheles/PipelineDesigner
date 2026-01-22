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

    def load_all(self) -> None:
        """Load all component definitions from the library directory."""
        self._components.clear()
        self._categories.clear()
        self._composite_designs.clear()

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
