"""Design-level cycle-accurate simulation panel.

Drives the full DesignSimulator (all primitives connected via the design graph)
from a per-cycle input table and renders results in the shared WaveformWidget.

Simulation modes
----------------
  Float (ideal)   — inputs are plain Python floats; behavior code runs with
                    native float arithmetic.  This gives the mathematically
                    ideal result and is the primary / default mode.

  Fixed-point     — inputs are quantized to the interface port's sfixed/ufixed
                    format before being passed to the simulator.  Behavior code
                    then operates on FixedPointArray objects.

  Co-simulation   — runs both modes in the same step and displays the
                    fixed-point output alongside the ideal float output so the
                    representation error can be seen directly on the waveform.

Single clock domain is assumed.  Interface ports are the external boundary:
  - INPUT interface ports  → editable stimulus table (one row per port)
  - OUTPUT interface ports → waveform lanes (green)
  - INPUT interface ports  → waveform lanes (blue) — mirrored from the table
"""

from __future__ import annotations

import enum
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models.behavior import SignalKind, _FIXED_POINT_KINDS
from pipeline_designer.domain.models.component import ComponentDefinition, PortSignalClass
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import InterfaceDirection, InterfacePort
from pipeline_designer.domain.simulation.graph_sim import DesignSimulator
from pipeline_designer.presentation.shared.waveform import (
    WaveSignal,
    WaveformWidget,
)

_BIT_TYPES = frozenset({
    SignalKind.STD_LOGIC.value,
    SignalKind.STD_ULOGIC.value,
    "boolean",
})


class SimMode(enum.Enum):
    FLOAT = "float"
    FIXED = "fixed"
    COSIM = "cosim"


class DesignSimulationPanel(QWidget):
    """Multi-cycle simulation panel for a full Design graph."""

    DEFAULT_CYCLES = 8

    def __init__(
        self,
        design_getter: Callable[[], Design],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._design_getter = design_getter
        self._library: dict[str, ComponentDefinition] = {}
        self._simulator_float: DesignSimulator | None = None
        self._simulator_fixed: DesignSimulator | None = None
        self._dirty = True
        self._n_cycles = self.DEFAULT_CYCLES
        self._mode = SimMode.FLOAT
        self._input_ports:  list[InterfacePort] = []
        self._output_ports: list[InterfacePort] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public interface

    def set_library(self, library: dict[str, ComponentDefinition]) -> None:
        self._library  = library
        self._simulator_float = None
        self._simulator_fixed = None
        self._dirty    = True

    def mark_dirty(self) -> None:
        self._simulator_float = None
        self._simulator_fixed = None
        self._dirty    = True
        self._stale_label.setVisible(True)

    def refresh_ports(self) -> None:
        """Re-read interface ports from the current design and rebuild the input table."""
        design = self._design_getter()
        self._input_ports  = [p for p in design.get_input_interfaces()
                               if p.signal_class != PortSignalClass.CLOCK]
        self._output_ports = design.get_output_interfaces()
        self._rebuild_input_table()
        self._waveform.set_data([], 0)
        self.mark_dirty()

    # ------------------------------------------------------------------
    # UI construction

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Header bar ────────────────────────────────────────────────
        hbar = QHBoxLayout()
        hbar.addWidget(QLabel("Cycles:"))

        self._cycle_spin = QSpinBox()
        self._cycle_spin.setRange(1, 200)
        self._cycle_spin.setValue(self.DEFAULT_CYCLES)
        self._cycle_spin.setFixedWidth(60)
        self._cycle_spin.valueChanged.connect(self._on_cycles_changed)
        hbar.addWidget(self._cycle_spin)
        hbar.addSpacing(12)

        self._sim_btn = QPushButton("▶  Simulate")
        self._sim_btn.setStyleSheet(
            "QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " padding:3px 12px; border-radius:3px; }"
            "QPushButton:hover { background:#45475a; }"
            "QPushButton:pressed { background:#585b70; }"
        )
        self._sim_btn.clicked.connect(self._run_simulation)
        hbar.addWidget(self._sim_btn)
        hbar.addSpacing(12)

        self._stale_label = QLabel("⚠  Design changed — re-run to update")
        self._stale_label.setStyleSheet("color:#f9e2af; font-size:8pt;")
        self._stale_label.setVisible(False)
        hbar.addWidget(self._stale_label)
        hbar.addStretch()
        root.addLayout(hbar)

        # ── Mode selector ─────────────────────────────────────────────
        mode_bar = QHBoxLayout()
        mode_bar.addWidget(QLabel("Mode:"))
        self._mode_group = QButtonGroup(self)
        for label, mode in (
            ("Float (ideal)", SimMode.FLOAT),
            ("Fixed-point", SimMode.FIXED),
            ("Co-sim", SimMode.COSIM),
        ):
            rb = QRadioButton(label)
            rb.setChecked(mode == self._mode)
            rb.toggled.connect(lambda checked, m=mode: self._on_mode_toggled(checked, m))
            self._mode_group.addButton(rb)
            mode_bar.addWidget(rb)
        mode_bar.addStretch()
        root.addLayout(mode_bar)

        # ── Status / error label ──────────────────────────────────────
        self._status = QLabel("")
        self._status.setStyleSheet("color:#f38ba8; font-size:8pt;")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        root.addWidget(self._status)

        # ── Splitter: input table (top) | waveform (bottom) ──────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        in_frame = QWidget()
        in_layout = QVBoxLayout(in_frame)
        in_layout.setContentsMargins(0, 0, 0, 0)
        in_layout.setSpacing(2)
        in_lbl = QLabel("Design inputs  (empty cell = 0)")
        in_lbl.setStyleSheet("color:#6c7086; font-size:8pt;")
        in_layout.addWidget(in_lbl)
        self._in_table = QTableWidget(0, self._n_cycles)
        self._in_table.setMaximumHeight(160)
        self._in_table.setAlternatingRowColors(True)
        self._in_table.horizontalHeader().setDefaultSectionSize(WaveformWidget.CYCLE_W)
        self._in_table.verticalHeader().setDefaultSectionSize(22)
        self._set_table_col_headers()
        in_layout.addWidget(self._in_table)
        splitter.addWidget(in_frame)

        wave_frame = QWidget()
        wave_layout = QVBoxLayout(wave_frame)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        wave_layout.setSpacing(2)
        wave_lbl = QLabel("Waveform  (blue = input, green = output, orange = fixed-point)")
        wave_lbl.setStyleSheet("color:#6c7086; font-size:8pt;")
        wave_layout.addWidget(wave_lbl)
        self._wave_scroll = QScrollArea()
        self._wave_scroll.setWidgetResizable(False)
        self._wave_scroll.setStyleSheet("QScrollArea { background:#1e1e2e; }")
        self._waveform = WaveformWidget()
        self._wave_scroll.setWidget(self._waveform)
        wave_layout.addWidget(self._wave_scroll)
        splitter.addWidget(wave_frame)

        splitter.setSizes([120, 260])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Input table management

    def _rebuild_input_table(self) -> None:
        old: dict[str, list[str]] = {}
        for row in range(self._in_table.rowCount()):
            h = self._in_table.verticalHeaderItem(row)
            if h:
                name = h.text().split(" (")[0]
                old[name] = [
                    (self._in_table.item(row, c).text().strip()
                     if self._in_table.item(row, c) else "0")
                    for c in range(self._in_table.columnCount())
                ]

        self._in_table.setRowCount(0)
        self._in_table.setColumnCount(self._n_cycles)
        self._set_table_col_headers()

        for port in self._input_ports:
            row = self._in_table.rowCount()
            self._in_table.insertRow(row)
            self._in_table.setVerticalHeaderItem(
                row, QTableWidgetItem(f"{port.name} ({port.data_type})")
            )
            prev = old.get(port.name, [])
            for c in range(self._n_cycles):
                val = prev[c] if c < len(prev) else "0"
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)

    def _set_table_col_headers(self) -> None:
        self._in_table.setHorizontalHeaderLabels(
            [str(c) for c in range(self._in_table.columnCount())]
        )

    def _on_cycles_changed(self, n: int) -> None:
        self._n_cycles = n
        old = self._get_input_data()
        self._in_table.setColumnCount(n)
        self._set_table_col_headers()
        for row, values in enumerate(old):
            last = values[-1] if values else "0"
            for c in range(n):
                val = values[c] if c < len(values) else last
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)

    def _on_mode_toggled(self, checked: bool, mode: SimMode) -> None:
        if checked:
            self._mode = mode

    def _get_input_data(self) -> list[list[str]]:
        return [
            [
                (self._in_table.item(row, c).text().strip()
                 if self._in_table.item(row, c) else "")
                for c in range(self._in_table.columnCount())
            ]
            for row in range(self._in_table.rowCount())
        ]

    # ------------------------------------------------------------------
    # Simulation

    def _run_simulation(self) -> None:
        self._status.setVisible(False)
        self._stale_label.setVisible(False)

        if not self._library:
            self._show_error("No library loaded.")
            return

        design = self._design_getter()
        in_ports  = [p for p in design.get_input_interfaces() if p.signal_class != PortSignalClass.CLOCK]
        out_ports = design.get_output_interfaces()

        if not in_ports and not out_ports:
            self._show_error("Design has no interface ports.  Add inputs/outputs on the canvas.")
            return

        run_float = self._mode in (SimMode.FLOAT, SimMode.COSIM)
        run_fixed = self._mode in (SimMode.FIXED, SimMode.COSIM)

        if run_float and (self._dirty or self._simulator_float is None):
            try:
                self._simulator_float = DesignSimulator(design, self._library)
            except Exception as exc:
                self._show_error(f"Build error: {exc}")
                return

        if run_fixed and (self._dirty or self._simulator_fixed is None):
            try:
                self._simulator_fixed = DesignSimulator(design, self._library)
            except Exception as exc:
                self._show_error(f"Build error (fixed): {exc}")
                return

        self._dirty = False

        raw = self._get_input_data()
        input_vals_float: dict[str, list[Any | None]] = {p.name: [] for p in in_ports}
        input_vals_fixed: dict[str, list[Any | None]] = {p.name: [] for p in in_ports}

        for row, port in enumerate(in_ports):
            row_strs = raw[row] if row < len(raw) else []
            for c in range(self._n_cycles):
                txt = row_strs[c] if c < len(row_strs) else ""
                fval = self._parse_float(txt, port)
                input_vals_float[port.name].append(fval)
                input_vals_fixed[port.name].append(
                    self._quantize_input(fval, port) if run_fixed else None
                )

        output_float: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}
        output_fixed: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}

        if run_float:
            sim = self._simulator_float
            sim.reset()
            for cyc in range(self._n_cycles):
                for port in in_ports:
                    v = input_vals_float[port.name][cyc]
                    if v is not None:
                        sim.set_input(port.name, v)
                try:
                    sim.step()
                except Exception as exc:
                    self._show_error(f"Float sim cycle {cyc}: {exc}")
                    remaining = self._n_cycles - cyc
                    for port in out_ports:
                        output_float[port.name].extend([None] * remaining)
                    break
                for port in out_ports:
                    output_float[port.name].append(
                        self._extract_value(sim.get_output(port.name))
                    )

        if run_fixed:
            sim = self._simulator_fixed
            sim.reset()
            for cyc in range(self._n_cycles):
                for port in in_ports:
                    v = input_vals_fixed[port.name][cyc]
                    if v is not None:
                        sim.set_input(port.name, v)
                try:
                    sim.step()
                except Exception as exc:
                    self._show_error(f"Fixed sim cycle {cyc}: {exc}")
                    remaining = self._n_cycles - cyc
                    for port in out_ports:
                        output_fixed[port.name].extend([None] * remaining)
                    break
                for port in out_ports:
                    output_fixed[port.name].append(
                        self._extract_value(sim.get_output(port.name))
                    )

        # Build waveform lanes
        signals: list[WaveSignal] = []
        is_bit_fn = lambda p: p.data_type.lower() in _BIT_TYPES  # noqa: E731

        for port in in_ports:
            signals.append(WaveSignal(
                name=port.name,
                is_input=True,
                is_bit=is_bit_fn(port),
                values=input_vals_float[port.name],
            ))

        for port in out_ports:
            is_bit = is_bit_fn(port)
            if run_float:
                signals.append(WaveSignal(
                    name=port.name + (" (float)" if self._mode == SimMode.COSIM else ""),
                    is_input=False,
                    is_bit=is_bit,
                    values=output_float[port.name],
                ))
            if run_fixed:
                signals.append(WaveSignal(
                    name=port.name + (" (fp)" if self._mode == SimMode.COSIM else ""),
                    is_input=False,
                    is_bit=is_bit,
                    values=output_fixed[port.name],
                ))

        self._waveform.set_data(signals, self._n_cycles)
        self._wave_scroll.update()

    # ------------------------------------------------------------------
    # Value helpers

    @staticmethod
    def _parse_float(text: str, port: InterfacePort) -> Any | None:
        """Parse a cell string into a Python float (or bool/int for bit types)."""
        text = text.strip()
        if not text:
            return None
        try:
            val = float(text)
        except ValueError:
            return None
        dt = port.data_type.lower()
        if dt in (SignalKind.STD_LOGIC.value, SignalKind.STD_ULOGIC.value):
            return bool(int(val))
        if dt == "boolean":
            return bool(val)
        if dt == "integer":
            return int(round(val))
        # data ports (sfixed/ufixed) and others → plain float for ideal sim
        return val

    @staticmethod
    def _quantize_input(value: Any | None, port: InterfacePort) -> Any | None:
        """Quantize *value* to the port's fixed-point format for fixed-point sim."""
        if value is None:
            return None
        st = port.effective_signal_type()
        k = st.resolved_kind()
        if k not in _FIXED_POINT_KINDS:
            return value  # not a fixed-point type, pass through as-is
        try:
            fmt = st.to_fpformat()
            return fmt.quantize(np.array(float(value)))
        except Exception:
            return value

    @staticmethod
    def _extract_value(result: Any) -> Any | None:
        if result is None:
            return None
        try:
            if hasattr(result, "item"):
                return float(result.item())
            if isinstance(result, (bool, int, float)):
                return result
            return float(result)
        except Exception:
            return None

    def _show_error(self, msg: str) -> None:
        self._status.setText(msg)
        self._status.setVisible(True)
