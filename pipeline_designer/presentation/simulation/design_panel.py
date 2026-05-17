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
                    then operates on FixedPoint scalars.

  Co-simulation   — runs both modes in the same step and displays the
                    fixed-point output alongside the ideal float output so the
                    representation error can be seen directly on the waveform.

Single clock domain is assumed.  Interface ports are the external boundary:
  - INPUT interface ports  → editable stimulus table (one row per port)
  - OUTPUT interface ports → waveform lanes (green)
  - INPUT interface ports  → waveform lanes (blue) — mirrored from the table
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
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

_OOB_BRUSH = QBrush(QColor(160, 40, 40))  # dark red for out-of-range cells


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

    def get_sim_config(self) -> dict:
        """Return serialisable simulation configuration (cycles, stimuli)."""
        stimuli: dict[str, list[str]] = {}
        for row in range(self._in_table.rowCount()):
            h = self._in_table.verticalHeaderItem(row)
            if h:
                port_name = h.text().split(" (")[0]
                stimuli[port_name] = [
                    (self._in_table.item(row, c).text().strip()
                     if self._in_table.item(row, c) else "0")
                    for c in range(self._in_table.columnCount())
                ]
        return {"n_cycles": self._n_cycles, "stimuli": stimuli}

    def apply_sim_config(self, n_cycles: int, mode: str, stimuli: dict[str, list[str]]) -> None:
        """Restore simulation configuration; must be called after refresh_ports()."""
        self._cycle_spin.setValue(n_cycles)  # triggers _on_cycles_changed → column rebuild

        if not stimuli:
            return
        self._in_table.blockSignals(True)
        for row in range(self._in_table.rowCount()):
            h = self._in_table.verticalHeaderItem(row)
            if not h:
                continue
            port_name = h.text().split(" (")[0]
            values = stimuli.get(port_name, [])
            for c in range(self._in_table.columnCount()):
                val = values[c] if c < len(values) else "0"
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)
        self._in_table.blockSignals(False)
        self._validate_all_cells()

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
        self._in_table.itemChanged.connect(self._on_cell_changed)
        in_layout.addWidget(self._in_table)
        splitter.addWidget(in_frame)

        wave_frame = QWidget()
        wave_layout = QVBoxLayout(wave_frame)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        wave_layout.setSpacing(2)
        wave_lbl = QLabel("Waveform  (blue = input, green = output [float / orange = fixed-point])")
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

        self._in_table.blockSignals(True)
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
        self._in_table.blockSignals(False)
        self._validate_all_cells()

    def _set_table_col_headers(self) -> None:
        self._in_table.setHorizontalHeaderLabels(
            [str(c) for c in range(self._in_table.columnCount())]
        )

    def _on_cycles_changed(self, n: int) -> None:
        self._n_cycles = n
        old = self._get_input_data()
        self._in_table.blockSignals(True)
        self._in_table.setColumnCount(n)
        self._set_table_col_headers()
        for row, values in enumerate(old):
            last = values[-1] if values else "0"
            for c in range(n):
                val = values[c] if c < len(values) else last
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)
        self._in_table.blockSignals(False)
        self._validate_all_cells()

    def _get_input_data(self) -> list[list[str]]:
        return [
            [
                (self._in_table.item(row, c).text().strip()
                 if self._in_table.item(row, c) else "")
                for c in range(self._in_table.columnCount())
            ]
            for row in range(self._in_table.rowCount())
        ]

    def _get_design_generics(self) -> dict[str, Any]:
        """Return default generic values from the current design's component_config."""
        design = self._design_getter()
        cc = design.component_config
        if cc is None:
            return {}
        return {g.name: g.default_value for g in cc.generics if g.default_value is not None}

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        row = self._in_table.row(item)
        col = self._in_table.column(item)
        self._validate_cell(row, col)

    def _validate_cell(self, row: int, col: int) -> None:
        if row < 0 or row >= len(self._input_ports):
            return
        item = self._in_table.item(row, col)
        if item is None:
            return
        port = self._input_ports[row]
        generics = self._get_design_generics()
        oob, tooltip = self._cell_out_of_range(item.text().strip(), port, generics)
        self._in_table.blockSignals(True)
        try:
            item.setBackground(_OOB_BRUSH if oob else QBrush())
            item.setToolTip(tooltip)
        finally:
            self._in_table.blockSignals(False)

    def _validate_all_cells(self) -> None:
        generics = self._get_design_generics()
        self._in_table.blockSignals(True)
        try:
            for row, port in enumerate(self._input_ports):
                for col in range(self._in_table.columnCount()):
                    item = self._in_table.item(row, col)
                    if item is None:
                        continue
                    oob, tooltip = self._cell_out_of_range(item.text().strip(), port, generics)
                    item.setBackground(_OOB_BRUSH if oob else QBrush())
                    item.setToolTip(tooltip)
        finally:
            self._in_table.blockSignals(False)

    @staticmethod
    def _cell_out_of_range(text: str, port: InterfacePort, generics: dict[str, Any]) -> tuple[bool, str]:
        """Return (is_out_of_range, tooltip_hint) for a cell value."""
        if not text:
            return False, ""
        try:
            raw = float(text)
        except ValueError:
            return True, "Not a valid number"

        dt = port.data_type.lower()
        if dt in _BIT_TYPES:
            if raw not in (0.0, 1.0):
                return True, "Must be 0 or 1"
            return False, ""

        try:
            st = port.effective_signal_type()
            k = st.resolved_kind(generics)
            if k not in _FIXED_POINT_KINDS:
                return False, ""
            fmt = st.to_fpformat(generics)
            if raw < fmt.real_min or raw > fmt.real_max:
                return True, f"Out of range [{fmt.real_min:.6g}, {fmt.real_max:.6g}]"
            return False, ""
        except Exception:
            return False, ""

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

        if self._dirty or self._simulator_float is None:
            try:
                self._simulator_float = DesignSimulator(design, self._library, float_mode=True)
            except Exception as exc:
                self._show_error(f"Build error: {exc}")
                return

        if self._dirty or self._simulator_fixed is None:
            try:
                self._simulator_fixed = DesignSimulator(design, self._library, float_mode=False)
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
                input_vals_fixed[port.name].append(self._quantize_input(fval, port))

        output_float: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}
        output_fixed: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}

        sim_f = self._simulator_float
        sim_f.reset()
        for cyc in range(self._n_cycles):
            for port in in_ports:
                v = input_vals_float[port.name][cyc]
                if v is not None:
                    sim_f.set_input(port.name, v)
            try:
                sim_f.step()
            except Exception as exc:
                self._show_error(f"Float sim cycle {cyc}: {exc}")
                remaining = self._n_cycles - cyc
                for port in out_ports:
                    output_float[port.name].extend([None] * remaining)
                break
            for port in out_ports:
                output_float[port.name].append(
                    self._extract_value(sim_f.get_output(port.name))
                )

        sim_x = self._simulator_fixed
        sim_x.reset()
        for cyc in range(self._n_cycles):
            for port in in_ports:
                v = input_vals_fixed[port.name][cyc]
                if v is not None:
                    sim_x.set_input(port.name, v)
            try:
                sim_x.step()
            except Exception as exc:
                self._show_error(f"Fixed sim cycle {cyc}: {exc}")
                remaining = self._n_cycles - cyc
                for port in out_ports:
                    output_fixed[port.name].extend([None] * remaining)
                break
            for port in out_ports:
                output_fixed[port.name].append(
                    self._extract_value(sim_x.get_output(port.name))
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
            if is_bit:
                signals.append(WaveSignal(
                    name=port.name,
                    is_input=False,
                    is_bit=True,
                    values=output_float[port.name],
                ))
            else:
                combined: list[Any | None] = []
                for f, x in zip(output_float[port.name], output_fixed[port.name]):
                    combined.append(None if f is None else (f, x))
                signals.append(WaveSignal(
                    name=port.name,
                    is_input=False,
                    is_bit=False,
                    values=combined,
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
        """Pre-quantize *value* to the nearest representable plain float for the port.

        Returns a plain Python float rounded to the port's fixed-point precision so
        that arithmetic behavior codes (e.g. ``return a + b``) work without producing
        UnquantizedResult objects.
        """
        if value is None:
            return None
        st = port.effective_signal_type()
        if st.resolved_kind() not in _FIXED_POINT_KINDS:
            return value
        try:
            return float(st.to_fpformat().quantize(np.array(float(value))))
        except Exception:
            return value

    @staticmethod
    def _extract_value(result: Any) -> Any | None:
        """Convert any executor result to a plain Python scalar (float/bool/int)."""
        if result is None:
            return None
        if isinstance(result, bool):
            return result
        try:
            return float(result)
        except (TypeError, ValueError):
            return None

    def _show_error(self, msg: str) -> None:
        self._status.setText(msg)
        self._status.setVisible(True)
