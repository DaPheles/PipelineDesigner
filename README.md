# Pipeline Designer

A graphical tool for designing cycle-accurate FPGA/ASIC pipelines. Draw component graphs, define pipeline stages, simulate behavior with fixed-point arithmetic, and export synthesizable VHDL.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Domain Models](#domain-models)
4. [Canvas and Scene](#canvas-and-scene)
5. [Signal Class System](#signal-class-system)
6. [Library and File Formats](#library-and-file-formats)
7. [Simulation Framework](#simulation-framework)
8. [VHDL Export](#vhdl-export)
9. [Running and Installing](#running-and-installing)
10. [Testing Strategy](#testing-strategy)

---

## Features

- **Graphical pipeline editor** — drag-and-drop components onto a grid canvas, draw connections between ports
- **Pipeline stages** — place register components to define pipeline stage boundaries; stages are numbered left-to-right and visualised as vertical bands
- **Composite components** — any design can be exported as a reusable `ComponentDefinition` and placed in other designs, enabling hierarchical pipelines
- **Signal class routing** — ports carry a semantic class (clock, reset, control, data); the editor blocks cross-class connections and flags existing mismatches in red
- **Undo/redo** — all canvas mutations go through a `Command` pattern stack
- **Cycle-accurate simulation** — two-phase (combinational + clocked capture) simulator evaluates the full design graph; results are shown as waveforms
- **Fixed-point arithmetic** — port types are expressed as `sfixed`/`ufixed`/`std_logic_vector` with MSB/LSB expressions; the simulator uses the bundled `fixedpoint` package
- **VHDL generation** — generates synthesizable entity + architecture plus a self-checking GHDL testbench from Python golden values
- **Property editor** — click any component, port, or interface port to inspect and edit its properties in a docked panel
- **Primitive editor** — a separate window for creating and editing leaf-level component definitions, including port tables, generic tables, and behavior code

---

## Architecture

The codebase follows a strict four-layer architecture. Qt must not be imported from `domain/` or `infrastructure/`.

```
pipeline_designer/
├── domain/             — Pydantic models, grid math, simulation. No Qt.
│   ├── grid.py         — GridConfig: px↔grid-unit conversion, snap helpers
│   ├── models/         — Core data types (see Domain Models)
│   └── simulation/     — BehaviorExecutor, DesignSimulator, VhdlGenerator
├── infrastructure/
│   └── persistence/
│       └── library_loader.py  — Loads primitives + composite designs from JSON
├── presentation/
│   ├── canvas/         — DesignScene (QGraphicsScene) + items + commands
│   ├── panels/         — PropertyEditor, ComponentPalette (docked panels)
│   ├── primitive_editor/ — Standalone window for editing primitives
│   ├── simulation/     — DesignSimulationPanel (waveform display)
│   └── shared/         — WaveformWidget
└── app/
    └── main_window.py  — Wires together all presentation components
```

### Coordinate system

All domain data uses **integer grid units**. Pixels exist only inside Qt items. Convert at the boundary with `GridConfig`:

```python
grid = DEFAULT_GRID         # 20 px/unit
grid.to_pixels(5)           # → 100.0
grid.to_grid_units_int(100) # → 5
grid.snap_to_grid(QPointF(103, 47))  # → QPointF(100, 40)
```

---

## Domain Models

All models live in `domain/models/` and are Pydantic v2 `BaseModel` subclasses. They are serialised to/from JSON for persistence.

### `ComponentDefinition` (`component.py`)

The reusable type definition for a leaf primitive or composite component.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique identifier used as library key |
| `category` | `str` | Palette grouping |
| `ports` | `list[Port]` | Port specifications in grid-unit positions |
| `generics` | `list[Generic]` | Parameterised widths/types |
| `visual` | `VisualConfig` | Width, height (grid units), color |
| `latency` | `int` | Pipeline latency in cycles |
| `behavior` | `ComponentBehavior` | Simulation behavior code |

### `Port` (`component.py`)

A port on a `ComponentDefinition`. Position is relative to the component's top-left corner in grid units.

| Field | Type | Default |
|-------|------|---------|
| `name` | `str` | — |
| `direction` | `PortDirection` (`in`/`out`/`inout`) | — |
| `signal_type` | `SignalType` | `std_logic` |
| `position` | `tuple[int,int] \| None` | `None` |
| `signal_class` | `PortSignalClass` | `data` |

Legacy `is_clock`/`is_reset` and `data_type`/`vector_range` fields are transparently migrated on load.

### `ComponentInstance` (`instance.py`)

A placed instance of a `ComponentDefinition` in a `Design`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | Unique instance key |
| `definition_ref` | `str` | Name of the `ComponentDefinition` |
| `position` | `tuple[int,int]` | Top-left in grid units |
| `generic_values` | `dict[str,Any]` | Override values for generics |
| `port_signal_classes` | `dict[str,str]` | Per-instance signal_class overrides (port name → class) |

### `Connection` (`instance.py`)

A directed edge between two ports.

```
source: PortReference  →  target: PortReference
```

`PortReference` identifies either a component port (`component_id + port_name`) or an interface port (`interface_port_id`).

### `InterfacePort` (`instance.py`)

An external port on a `Design` when used as a component. Carries `direction` (input/output), `signal_class`, `data_type`, and optional `position`. Can reference an internal component port via `internal_component_id + internal_port_name`.

### `Design` (`design.py`)

The top-level document. Contains instances, connections, stages, interface ports, and metadata. Maintains O(1) lookup indices (not serialised) that are rebuilt on `model_post_init`.

### `Stage` (`stage.py`)

A pipeline stage defined by one or more register placements. Each stage has an `x_position` (grid units), `index` (left-to-right order starting at 1), and a set of `register_ids`. Stages are created/removed automatically when registers are placed or deleted.

---

## Canvas and Scene

`DesignScene` is the authoritative source of truth for the current design. It inherits behaviour from four mixins:

| Mixin | Responsibilities |
|-------|-----------------|
| `_SceneComponentMixin` | Add/remove/move components; grid snapping; conflict detection |
| `_SceneConnectionMixin` | Connection drag-create; signal-class validation; connection item lifecycle |
| `_SceneInterfaceMixin` | Interface stage items; interface port placement and bounds |
| `_SceneAlignmentMixin` | Undo/redo; stage group-move; composite alignment across stages |

### Command pattern

Every mutation goes through a `Command` subclass in `commands.py` and is executed via `UndoStack`. Never mutate `Design` or scene items directly from outside the scene.

```
AddComponentCommand
RemoveComponentCommand
MoveComponentCommand
AddConnectionCommand
RemoveConnectionCommand
MoveStageCommand
```

### Scene items hierarchy

```
QGraphicsScene
├── StageItem          — vertical stage band with drag handle
├── InterfaceStageItem — input / output boundary rails
├── ComponentBoundsItem — dashed bounding box in component mode
├── ComponentItem       — rendered component rectangle + port dots
│   └── PortItem        — port dot with label
├── InterfacePortItem   — interface port diamond on boundary rail
└── ConnectionItem      — bezier/line between two ports
    └── TempConnectionItem — in-progress drag wire
```

### Connection lifecycle

1. User drags from a `PortItem` (output) or `InterfacePortItem` (input side).
2. `_SceneConnectionMixin._start_connection()` creates a `TempConnectionItem`.
3. On `mouseMoveEvent`, candidate targets are highlighted; `_is_valid_connection_target()` / `_is_valid_interface_target()` enforce signal-class matching.
4. On `mouseReleaseEvent`, if the target is valid, `AddConnectionCommand` is executed.
5. `_validate_all_connections()` runs post-creation and marks any mismatched `ConnectionItem` as invalid (red dashed).

---

## Signal Class System

Every `Port` and `InterfacePort` carries a `PortSignalClass`:

| Class | Colour | Routing rule |
|-------|--------|-------------|
| `clock` | Yellow | Only connects to `clock` ports |
| `reset` | Orange | Only connects to `reset` ports |
| `control` | Cyan | Only connects to `control` ports |
| `data` | Default blue | Only connects to `data` ports |

### Rules

- **New connections** — cross-class drags are blocked; the temporary wire turns red while hovering an incompatible port.
- **Existing connections** — changing a port's class is allowed, but any now-mismatched connections are immediately flagged red and a warning is emitted via `DesignScene.validation_warnings`.
- **Save** — `MainWindow._save_to_file()` drops invalid connections before serialising; the status bar reports how many were dropped.
- **Signal class is user-controlled** — it is never auto-derived from connections.

### Per-instance port overrides

`ComponentDefinition.ports` is shared across all instances of the same type. Each `ComponentInstance` stores overrides in `port_signal_classes: dict[str,str]`. When a `ComponentItem` is created, `_create_ports()` always produces an independent `Port` copy per instance and applies the stored override — preventing shared-object mutation.

---

## Library and File Formats

### Primitives — `library/primitives/*.json`

Leaf `ComponentDefinition` objects. Direct fields: `name`, `category`, `ports`, `generics`, `visual`, `latency`, `behavior`. Loaded by `LibraryLoader._load_component_file()`.

### Designs — `library/components/*.json` or user files

`Design` objects: instances + connections + stages + interface ports. A design with `component_config.enabled = true` is also exported as a composite `ComponentDefinition` by `LibraryLoader._load_composite_component()`.

**`_load_from_file` always parses as `Design`.** It cannot open primitive JSON — Pydantic silently discards primitive-only fields. There is no primitive editor file-open flow.

### Library loading

```python
loader = LibraryLoader(paths=[...])
library: dict[str, ComponentDefinition] = loader.load_all()
```

Both primitives and enabled composites appear as `ComponentDefinition` in the flat `library` dict. Use `loader.is_composite(name)` and `loader.get_composite_design(name)` to distinguish them when needed.

---

## Simulation Framework

### `BehaviorExecutor` (`domain/simulation/executor.py`)

Compiles a primitive's `behavior.code` Python snippet into a callable. Exposes a `SimNamespace` with `SFixed`, `UFixed`, `Bits`, `Const`, and numpy.

```python
executor = BehaviorExecutor(definition)
result = executor(arg0, arg1, ...)   # positional: matches port order
```

For combinational primitives, `code` must `return` the output value. Registers use `return d` — the simulator's Phase 2 handles the actual latch.

### `DesignSimulator` (`domain/simulation/graph_sim.py`)

Cycle-accurate two-phase simulator for a full `Design`.

```python
sim = DesignSimulator(design, library)
sim.reset()
sim.set_input("data_in", value)
sim.step()
out = sim.get_output("data_out")
```

**Phase 1** — combinational: evaluates all non-register instances in topological order.
**Phase 2** — clocked capture: registers latch D→Q atomically.

Register detection is duck-typed: any definition with ports `{d, q, clk}` (case-insensitive) is treated as a register.

### Fixed-point types

Port signal types use `sfixed(M downto L)` / `ufixed` / `std_logic_vector` notation. `BehaviorPortType.to_fpformat(generics)` converts MSB/LSB expressions (which may reference generic names) to a `fixedpoint.FPFormat`. The `fixedpoint` package lives in `fixed_point_evaluation/python/src/` and is linked via a `.pth` file.

---

## VHDL Export

`VhdlGenerator` (`domain/simulation/vhdl_generator.py`) produces:

- A synthesizable VHDL entity + architecture for the design
- A self-checking GHDL testbench with Python golden simulation values

```python
gen = VhdlGenerator(design, library)
gen.write(output_dir)
```

See `examples/fir4_vhdl_export.py` for a complete worked example.

---

## Running and Installing

**Requirements:** Python 3.11+ and the `fixedpoint` package linked via a `.pth` file (see below).

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python3 -m pipeline_designer.main

# Run tests
pytest

# Run a single test file
pytest tests/test_domain_models.py
```

### fixedpoint package

The simulation framework requires the `fixedpoint` package from `fixed_point_evaluation/python/src/`. It is not installed as a normal package — instead, add a `.pth` file so Python can find it:

```bash
echo "$(pwd)/fixed_point_evaluation/python/src" \
  > .venv/lib/python*/site-packages/fixedpoint.pth
```

---

## Testing Strategy

The application has three distinct test layers. Each targets a different part of the stack and requires different tooling.

### Layer 1 — Domain model tests (pure Python, no Qt)

These are the fastest, most reliable tests. `domain/` has zero Qt dependencies — any Pydantic model or simulation class can be instantiated and asserted on directly.

- refer to 'tests/test_domain_models.py'

### Layer 2 — Scene logic tests (headless Qt, no display)

These tests exercise `DesignScene` mutations without showing a window. They need a `QApplication` but no display server (use `QT_QPA_PLATFORM=offscreen`).

```python
# tests/conftest.py
import os
import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
```

- refer to 'tests/test_scene.py'

### Layer 3 — UI interaction tests (pytest-qt)

`pytest-qt` provides `qtbot`, which simulates mouse/keyboard events and waits for Qt signals. Install with `pip install pytest-qt`.

- refer to 'tests/test_ui_interactions.py'

### Recommended directory layout

```
tests/
├── conftest.py           — QApplication fixture, shared helpers
├── domain/
│   ├── test_models.py    — Port, Design, Connection, Stage model tests
│   ├── test_grid.py      — GridConfig math
│   └── test_simulation.py — DesignSimulator cycle tests
├── infrastructure/
│   └── test_library_loader.py — JSON loading, composite export
├── presentation/
│   ├── test_scene_components.py — add/remove/move + undo
│   ├── test_scene_connections.py — connection creation, validation
│   ├── test_scene_interface.py   — interface port placement
│   └── test_property_editor.py  — signal/slot wiring
└── integration/
    └── test_save_load.py — full roundtrip: design → JSON → reload
```

### Key tooling additions

Add to `pyproject.toml` dev dependencies:

```
pip install pytest pytest-qt pytest-cov
```

Run with coverage:

```bash
QT_QPA_PLATFORM=offscreen pytest --cov=pipeline_designer --cov-report=term-missing
```

### Testing principles

- **Domain tests need no fixtures** — instantiate models directly; they are plain Pydantic objects.
- **Scene tests need `qapp`** — one session-scoped `QApplication` is sufficient; create a fresh `DesignScene` per test.
- **UI tests use `qtbot.waitSignal`** — never `time.sleep`; let Qt's event loop process events naturally.
- **Test the contract, not the implementation** — assert on `Design` state and emitted signals, not on internal `_` attributes. The one exception is `_port_items` and `_component_items` dicts, which are the natural access path for scene item tests.
- **Simulation tests are pure Python** — `DesignSimulator` has no Qt dependency; test combinational and registered paths independently.
