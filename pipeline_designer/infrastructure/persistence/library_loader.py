"""Library loader for component definitions."""

import json
import re
from pathlib import Path
from typing import Literal

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

LibType = Literal["primitives", "components"]


class LibraryLoader:
    """Loads and manages component definitions from a prioritised list of library roots.

    Each root must have the shape::

        <root>/
          primitives/<category>/<name>.json   # ComponentDefinition files
          components/<category>/<name>.json   # Design files (composite components)

    Flat files directly under ``primitives/`` or ``components/`` (no category subdir)
    are still accepted with a deprecation warning and assigned to "uncategorized".

    Multiple roots are scanned in order.  Later roots can shadow earlier ones if they
    define a component with the same name (last-wins).  Built-in library is typically
    ``roots[0]``; user libraries follow.
    """

    def __init__(
        self,
        library_path: Path | None = None,
        grid: GridConfig | None = None,
        extra_roots: list[Path] | None = None,
    ):
        """Initialise the loader.

        Args:
            library_path: Primary library root.  Defaults to the bundled ``library/``
                directory next to the package.
            grid: Grid configuration for port-position validation.
            extra_roots: Additional library roots appended after *library_path*.
        """
        if library_path is None:
            library_path = Path(__file__).parent.parent.parent.parent / "library"

        self.library_roots: list[Path] = [library_path] + (extra_roots or [])
        self.grid = grid or DEFAULT_GRID

        # name → ComponentDefinition (primitives + synthetic composites merged)
        self._components: dict[str, ComponentDefinition] = {}
        # name → category
        self._categories: dict[str, list[str]] = {}
        # Separate type tracking so the palette can split primitives from composites
        self._primitive_names: set[str] = set()
        self._composite_names: set[str] = set()
        # Original Design objects for composites
        self._composite_designs: dict[str, Design] = {}
        # File paths for CRUD (both primitives and composites)
        self._primitive_file_paths: dict[str, Path] = {}
        self._composite_file_paths: dict[str, Path] = {}
        # Root index for each item (0 = primary/built-in)
        self._item_roots: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Loading                                                              #
    # ------------------------------------------------------------------ #

    def load_all(self) -> None:
        """Reload all component definitions from all library roots."""
        self._components.clear()
        self._categories.clear()
        self._primitive_names.clear()
        self._composite_names.clear()
        self._composite_designs.clear()
        self._primitive_file_paths.clear()
        self._composite_file_paths.clear()
        self._item_roots.clear()

        for root_idx, root in enumerate(self.library_roots):
            if not root.exists():
                continue
            self._load_root(root, root_idx)

    def _load_root(self, root: Path, root_idx: int) -> None:
        primitives_path = root / "primitives"
        if primitives_path.exists():
            # New: category subdirectories
            for cat_dir in sorted(primitives_path.iterdir()):
                if cat_dir.is_dir():
                    for json_file in sorted(cat_dir.glob("*.json")):
                        try:
                            self._load_component_file(json_file, cat_dir.name, root_idx)
                        except Exception as e:
                            print(f"Warning: Failed to load {json_file}: {e}")
            # Backward compat: flat files directly under primitives/
            for json_file in sorted(primitives_path.glob("*.json")):
                print(f"Deprecation: {json_file} should be in a category subdir")
                try:
                    self._load_component_file(json_file, "uncategorized", root_idx)
                except Exception as e:
                    print(f"Warning: Failed to load {json_file}: {e}")

        components_path = root / "components"
        if components_path.exists():
            for cat_dir in sorted(components_path.iterdir()):
                if cat_dir.is_dir():
                    for json_file in sorted(cat_dir.glob("*.json")):
                        try:
                            self._load_composite_component(json_file, cat_dir.name, root_idx)
                        except Exception as e:
                            print(f"Warning: Failed to load composite {json_file}: {e}")
            for json_file in sorted(components_path.glob("*.json")):
                print(f"Deprecation: {json_file} should be in a category subdir")
                try:
                    self._load_composite_component(json_file, "uncategorized", root_idx)
                except Exception as e:
                    print(f"Warning: Failed to load composite {json_file}: {e}")

    def _load_component_file(self, file_path: Path, category: str, root_idx: int) -> None:
        with open(file_path, "r") as f:
            data = json.load(f)

        # Directory name is authoritative; override whatever is in the JSON
        data["category"] = category
        component = ComponentDefinition.model_validate(data)

        errors = component.validate_port_positions()
        for err in errors:
            print(f"Warning: {file_path.name}: {err}")

        self._register_component(component, root_idx)
        self._primitive_file_paths[component.name] = file_path
        self._primitive_names.add(component.name)
        self._composite_names.discard(component.name)

    def _load_composite_component(self, file_path: Path, category: str, root_idx: int) -> None:
        with open(file_path, "r") as f:
            data = json.load(f)

        design = Design.model_validate(data)

        if not design.is_component:
            return

        # Directory name is authoritative for category
        design.component_config.category = category

        self._composite_designs[design.name] = design

        config = design.component_config
        visual_width = design.visual.width if design.visual.width > 0 else config.width
        visual_height = design.visual.height if design.visual.height > 0 else config.height

        ports: list[Port] = []
        for i, iface in enumerate(design.get_input_interfaces()):
            y_pos = self._calculate_port_y(i, len(design.get_input_interfaces()), visual_height)
            ports.append(Port(
                name=iface.name,
                direction=PortDirection.IN,
                data_type=iface.data_type,
                position=(0, y_pos) if iface.position is None else (0, iface.position[1]),
            ))
        for i, iface in enumerate(design.get_output_interfaces()):
            y_pos = self._calculate_port_y(i, len(design.get_output_interfaces()), visual_height)
            ports.append(Port(
                name=iface.name,
                direction=PortDirection.OUT,
                data_type=iface.data_type,
                position=(visual_width, y_pos) if iface.position is None else (visual_width, iface.position[1]),
            ))

        component = ComponentDefinition(
            name=design.name,
            category=category,
            description=config.description,
            ports=ports,
            generics=[],
            visual=VisualConfig(width=visual_width, height=visual_height, color=config.color),
            latency=design.latency,
        )

        self._register_component(component, root_idx)
        self._composite_file_paths[component.name] = file_path
        self._composite_names.add(component.name)
        self._primitive_names.discard(component.name)

    def _register_component(self, component: ComponentDefinition, root_idx: int) -> None:
        self._components[component.name] = component
        self._item_roots[component.name] = root_idx
        cat = component.category
        if cat not in self._categories:
            self._categories[cat] = []
        if component.name not in self._categories[cat]:
            self._categories[cat].append(component.name)

    @staticmethod
    def _calculate_port_y(index: int, total: int, height: int) -> int:
        if total <= 1:
            return height // 2
        spacing = (height - 2) // (total + 1)
        return 1 + spacing * (index + 1)

    def reload(self) -> None:
        """Reload all component definitions."""
        self.load_all()

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    def get_component(self, name: str) -> ComponentDefinition | None:
        return self._components.get(name)

    def get_all_components(self) -> list[ComponentDefinition]:
        return list(self._components.values())

    def get_primitives(self) -> list[ComponentDefinition]:
        """Return only primitive (non-composite) component definitions."""
        return [self._components[n] for n in self._primitive_names if n in self._components]

    def get_composites(self) -> list[ComponentDefinition]:
        """Return only composite component definitions."""
        return [self._components[n] for n in self._composite_names if n in self._components]

    def get_categories(self) -> list[str]:
        return list(self._categories.keys())

    def get_primitive_categories(self) -> list[str]:
        cats: set[str] = set()
        for name in self._primitive_names:
            comp = self._components.get(name)
            if comp:
                cats.add(comp.category)
        return sorted(cats)

    def get_composite_categories(self) -> list[str]:
        cats: set[str] = set()
        for name in self._composite_names:
            comp = self._components.get(name)
            if comp:
                cats.add(comp.category)
        return sorted(cats)

    def get_components_by_category(self, category: str) -> list[ComponentDefinition]:
        names = self._categories.get(category, [])
        return [self._components[name] for name in names if name in self._components]

    def is_composite(self, name: str) -> bool:
        return name in self._composite_designs

    def get_composite_design(self, name: str) -> Design | None:
        return self._composite_designs.get(name)

    def get_all_composite_designs(self) -> dict[str, Design]:
        return self._composite_designs.copy()

    def is_builtin(self, name: str) -> bool:
        """Return True if the component comes from the primary (built-in) library root."""
        return self._item_roots.get(name, 0) == 0

    # ------------------------------------------------------------------ #
    # Primitive CRUD                                                       #
    # ------------------------------------------------------------------ #

    def get_primitive_names(self) -> list[str]:
        return sorted(self._primitive_names)

    def get_primitive_file_path(self, name: str) -> Path | None:
        return self._primitive_file_paths.get(name)

    @staticmethod
    def _name_to_filename(name: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_", name.lower())

    def save_primitive(
        self,
        component: ComponentDefinition,
        old_path: Path | None = None,
    ) -> Path:
        """Persist a primitive definition to JSON and update in-memory state.

        The file is written to ``<primary_root>/primitives/<category>/<name>.json``.
        If *old_path* points to a different location (rename/move) the old file is
        deleted after the new one is successfully written.
        """
        stem = self._name_to_filename(component.name)
        category_dir = self.library_roots[0] / "primitives" / component.category
        target_path = category_dir / f"{stem}.json"

        if old_path is None:
            old_path = self._primitive_file_paths.get(component.name)

        category_dir.mkdir(parents=True, exist_ok=True)
        data = component.model_dump(mode="json", exclude_none=True)
        target_path.write_text(json.dumps(data, indent=4))

        if old_path is not None and old_path != target_path and old_path.exists():
            old_path.unlink()

        # Purge stale registry entry for old name (if any)
        if old_path is not None:
            old_name = next(
                (n for n, p in self._primitive_file_paths.items() if p == old_path),
                None,
            )
            if old_name and old_name != component.name:
                self._components.pop(old_name, None)
                self._primitive_file_paths.pop(old_name, None)
                self._primitive_names.discard(old_name)
                for cat_list in self._categories.values():
                    if old_name in cat_list:
                        cat_list.remove(old_name)

        self._register_component(component, self._item_roots.get(component.name, 0))
        self._primitive_file_paths[component.name] = target_path
        self._primitive_names.add(component.name)

        return target_path

    def delete_primitive(self, name: str) -> bool:
        """Remove a primitive from the library and delete its JSON file."""
        if name in self._composite_designs:
            return False

        component = self._components.pop(name, None)
        file_path = self._primitive_file_paths.pop(name, None)
        self._primitive_names.discard(name)
        self._item_roots.pop(name, None)

        if component:
            cat_list = self._categories.get(component.category, [])
            if name in cat_list:
                cat_list.remove(name)

        if file_path and file_path.exists():
            file_path.unlink()

        return component is not None

    # ------------------------------------------------------------------ #
    # Composite CRUD                                                       #
    # ------------------------------------------------------------------ #

    def get_composite_file_path(self, name: str) -> Path | None:
        return self._composite_file_paths.get(name)

    def save_composite(self, design: Design) -> Path:
        """Persist a composite component design to JSON and update in-memory state."""
        stem = self._name_to_filename(design.name)
        category = design.component_config.category or "uncategorized"
        category_dir = self.library_roots[0] / "components" / category
        target_path = category_dir / f"{stem}.json"

        old_path = self._composite_file_paths.get(design.name)

        category_dir.mkdir(parents=True, exist_ok=True)
        data = design.model_dump(mode="json", exclude_none=True)
        target_path.write_text(json.dumps(data, indent=4))

        if old_path is not None and old_path != target_path and old_path.exists():
            old_path.unlink()

        self._composite_file_paths[design.name] = target_path
        # Reload so the synthetic ComponentDefinition is refreshed
        self._load_composite_component(target_path, category, self._item_roots.get(design.name, 0))

        return target_path
