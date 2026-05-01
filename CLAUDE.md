# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install

# Run the app
python3 -m pipeline_designer.main

# Run tests
pytest

# Run a single test file
pytest tests/test_foo.py
```

## Architecture

This is a PySide6 graphical tool for designing FPGA/ASIC pipelines. The codebase follows a strict layered architecture — do not import Qt from `domain/` or `infrastructure/`.

```
domain/         — Pydantic models, grid math. No Qt.
infrastructure/ — Library loading, file persistence. No Qt.
presentation/   — Qt canvas, items, panels.
app/            — Wiring: MainWindow composes scene, palette, property editor, menus.
```

### Two distinct file formats

**Primitives** (`library/primitives/*.json`) are `ComponentDefinition` objects: leaf building blocks with `name`, `category`, `ports`, `generics`, `visual` (width/height/color), and `latency`. Loaded by `LibraryLoader._load_component_file()`.

**Designs** (`library/components/*.json` or user files) are `Design` objects: compositions of component instances, connections, and pipeline stages. A Design with `component_config.enabled = true` is also exported as a composite `ComponentDefinition` by `LibraryLoader._load_composite_component()`. Opened/saved by `MainWindow._load_from_file()` / `_save_to_file()`.

`_load_from_file` always parses files as `Design`. **It cannot open primitive JSON** — Pydantic silently discards all primitive-specific fields and you get an empty design. There is intentionally no primitive editor yet.

### Coordinate system

All domain data (port positions, component sizes, stage x-positions) is in **integer grid units**. Pixels are only used inside Qt items. Convert at UI boundaries using `GridConfig` (`domain/grid.py`): `grid.to_pixels()`, `grid.to_grid_units_int()`, `grid.snap_to_grid()`. The default is 20 px/unit (`DEFAULT_GRID`).

### Canvas scene

`DesignScene` is the source of truth for the current design. It inherits from four mixins (each a separate file) to keep file size manageable:

- `_SceneComponentMixin` — add/remove/move components
- `_SceneConnectionMixin` — connection drag-creation and wiring
- `_SceneInterfaceMixin` — interface port placement
- `_SceneAlignmentMixin` — register-to-stage alignment

All mutations go through `commands.py` (Command pattern with `UndoStack`) so undo/redo works. Never mutate `Design` or scene items directly from outside the scene — always call scene methods which in turn create and execute commands.

### Library loading flow

`LibraryLoader.load_all()` populates `_components` (dict keyed by name). Primitives and enabled composite designs both end up in this dict as `ComponentDefinition`. The palette and scene see a flat `dict[str, ComponentDefinition]`; the distinction is only relevant for the loader itself (and for `is_composite()` / `get_composite_design()`).

### Port types — critical distinction

`Port` (in `domain/models/component.py`) is a port on a `ComponentDefinition` — its position is relative to the component's top-left corner.

`InterfacePort` (in `domain/models/instance.py`) is a port on a `Design` when it is used as a component — it connects internal logic to the outside. These are different model types. Do not conflate them.

## Conventions

- Port positions and component sizes: always integer grid units in domain models.
- UUIDs identify component instances, stages, connections, and interface ports. Preserve them — they are used as dict keys throughout.
- New Qt actions → add to `MainWindow._setup_menus()`; new persistent panels → dock widgets in `MainWindow._setup_ui()`.
- New canvas mutations → add a `Command` subclass in `commands.py`, execute via `UndoStack`.

## Known pitfalls

- Mixing pixels and grid units in domain data causes misaligned ports and broken connections.
- `VisualExtent` (on `Design`) and `VisualConfig` (on `ComponentDefinition`) have different field sets despite similar names — do not swap them.
- `pyproject.toml` references a `README.md` that does not exist; packaging will fail until it is created.
