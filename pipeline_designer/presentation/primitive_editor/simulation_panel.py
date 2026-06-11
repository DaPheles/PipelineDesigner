"""Simulation evaluation panel for primitive behavior testing.

Always runs two passes per click:
  1. Float (ideal) pass — inputs are plain floats; executor uses FloatSimNamespace
     so quantize() is an identity and all math runs at full Python float precision.
  2. Fixed-point pass  — inputs are pre-quantized to the port's declared sfixed/ufixed
                         format (truncated float), then the executor uses the real
                         fixedpoint namespace.  Explicit SFixed()/UFixed() calls in
                         the behavior code produce clamping and truncation.

Both results are shown in the same waveform lane (white = ideal, orange = fixed-point),
matching the format used by the main-window design simulation panel.

Generic types (``signal_kind``) must be set concretely in the
"Simulation Generics" section before running.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Generic, Port, PortDirection, PortSignalClass
from pipeline_designer.domain.models.behavior import SignalKind
from pipeline_designer.domain.simulation.executor import BehaviorExecutor
from pipeline_designer.presentation.shared.waveform import (
    WaveSignal,
    WaveformWidget,
    fmt_value as _fmt_value,
)

# ── Signal kind classification ────────────────────────────────────────────────

_SIGNAL_KIND_OPTIONS = [k.value for k in SignalKind]

_BIT_KINDS = frozenset({
    SignalKind.STD_LOGIC.value,
    SignalKind.STD_ULOGIC.value,
    SignalKind.BOOLEAN.value,
})
_SCALAR_KINDS = frozenset({
    SignalKind.STD_LOGIC.value,
    SignalKind.STD_ULOGIC.value,
    SignalKind.BOOLEAN.value,
    SignalKind.INTEGER.value,
})


_OOB_BRUSH = QBrush(QColor(160, 40, 40))  # dark red for out-of-range cells


# ── SimulationPanel ───────────────────────────────────────────────────────────

class SimulationPanel(QWidget):
    """Multi-cycle Python-mode simulation for a single primitive.

    Typical usage (called by BehaviorEditor)::

        panel = SimulationPanel(behavior_getter=lambda: editor.code)
        panel.set_context(ports, generics)
        # user clicks Simulate → results shown in waveform view
    """

    DEFAULT_CYCLES = 1

    def __init__(
        self,
        behavior_getter: Callable[[], str],
        ideal_code_getter: Callable[[], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._behavior_getter   = behavior_getter
        self._ideal_code_getter = ideal_code_getter or (lambda: None)
        self._ports:    list[Port]    = []
        self._generics: list[Generic] = []
        self._n_cycles: int = self.DEFAULT_CYCLES
        self._latency:  int = 0
        self._generic_widgets: dict[str, QWidget] = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public interface

    def set_context(
        self,
        ports: list[Port],
        generics: list[Generic],
        latency: int = 0,
    ) -> None:
        """Update ports, generics and pipeline latency; rebuilds generic widgets and table rows."""
        self._ports    = list(ports)
        self._generics = list(generics)
        self._latency  = max(0, latency)
        self._rebuild_generics_section()
        self._rebuild_input_table()
        self._waveform.set_data([], 0)

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
        hbar.addStretch()
        root.addLayout(hbar)

        # ── Generics override ─────────────────────────────────────────
        self._gen_box = QGroupBox("Simulation Generics")
        self._gen_box.setStyleSheet("QGroupBox { font-size:8pt; color:#6c7086; }")
        self._gen_grid = QGridLayout(self._gen_box)
        self._gen_grid.setContentsMargins(4, 4, 4, 4)
        self._gen_grid.setSpacing(4)
        self._gen_box.setVisible(False)
        root.addWidget(self._gen_box)

        # ── Status label ──────────────────────────────────────────────
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
        in_label = QLabel("Input values  (empty cell = unknown / X)")
        in_label.setStyleSheet("color:#6c7086; font-size:8pt;")
        in_layout.addWidget(in_label)
        self._in_table = QTableWidget(0, self._n_cycles)
        self._in_table.setMaximumHeight(180)
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
        wave_label = QLabel("Waveform  (blue = input, green = output [float / orange = fixed-point])")
        wave_label.setStyleSheet("color:#6c7086; font-size:8pt;")
        wave_layout.addWidget(wave_label)
        self._wave_scroll = QScrollArea()
        self._wave_scroll.setWidgetResizable(False)
        self._wave_scroll.setStyleSheet("QScrollArea { background:#1e1e2e; }")
        self._waveform = WaveformWidget()
        self._wave_scroll.setWidget(self._waveform)
        wave_layout.addWidget(self._wave_scroll)
        splitter.addWidget(wave_frame)

        splitter.setSizes([160, 300])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Generic widgets

    def _rebuild_generics_section(self) -> None:
        while self._gen_grid.count():
            item = self._gen_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._generic_widgets.clear()

        visible_generics = [
            g for g in self._generics
            if g.data_type not in ("", None)
        ]
        if not visible_generics:
            self._gen_box.setVisible(False)
            return

        for col, g in enumerate(visible_generics):
            lbl = QLabel(f"{g.name}:")
            lbl.setStyleSheet("color:#cdd6f4; font-size:8pt;")
            self._gen_grid.addWidget(lbl, 0, col * 2)

            if g.data_type == "signal_kind":
                w: QWidget = QComboBox()
                for k in _SIGNAL_KIND_OPTIONS:
                    w.addItem(k)
                default = str(g.default_value or SignalKind.UFIXED.value)
                idx = w.findText(default)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif g.options:
                w = QComboBox()
                for opt in g.options:
                    w.addItem(opt)
                default = str(g.default_value or g.options[0])
                idx = w.findText(default)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            else:
                w = QLineEdit()
                w.setFixedWidth(70)
                w.setPlaceholderText("value")
                if g.default_value is not None:
                    w.setText(str(g.default_value))

            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._validate_all_cells)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(self._validate_all_cells)
            self._gen_grid.addWidget(w, 0, col * 2 + 1)
            self._generic_widgets[g.name] = w

        self._gen_box.setVisible(True)

    def _get_sim_generics(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for g in self._generics:
            w = self._generic_widgets.get(g.name)
            if w is None:
                if g.default_value is not None:
                    result[g.name] = g.default_value
                continue
            if isinstance(w, QComboBox):
                result[g.name] = w.currentText()
            elif isinstance(w, QLineEdit):
                text = w.text().strip()
                if text:
                    try:
                        result[g.name] = int(text)
                    except ValueError:
                        try:
                            result[g.name] = float(text)
                        except ValueError:
                            result[g.name] = text
                elif g.default_value is not None:
                    result[g.name] = g.default_value
        return result

    # ------------------------------------------------------------------
    # Input table

    def _rebuild_input_table(self) -> None:
        in_ports = [
            p for p in self._ports
            if p.direction == PortDirection.IN and p.signal_class != PortSignalClass.CLOCK
        ]
        old_values: dict[str, list[str]] = {}
        for row in range(self._in_table.rowCount()):
            header = self._in_table.verticalHeaderItem(row)
            if header:
                port_name = header.text().split(" (")[0]
                old_values[port_name] = []
                for c in range(self._in_table.columnCount()):
                    it = self._in_table.item(row, c)
                    old_values[port_name].append(it.text().strip() if it else "0")

        self._in_table.blockSignals(True)
        self._in_table.setRowCount(0)
        self._in_table.setColumnCount(self._n_cycles)
        self._set_table_col_headers()
        for port in in_ports:
            row = self._in_table.rowCount()
            self._in_table.insertRow(row)
            k = port.signal_type.kind
            self._in_table.setVerticalHeaderItem(row, QTableWidgetItem(f"{port.name} ({k})"))
            prev = old_values.get(port.name, [])
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
        data = self._get_input_data()
        self._in_table.blockSignals(True)
        self._in_table.setColumnCount(n)
        self._set_table_col_headers()
        for row, values in enumerate(data):
            for c in range(min(len(values), n)):
                item = QTableWidgetItem(values[c])
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)
            last = values[-1] if values else "0"
            for c in range(len(values), n):
                item = QTableWidgetItem(last)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._in_table.setItem(row, c, item)
        self._in_table.blockSignals(False)
        self._validate_all_cells()

    def _get_input_data(self) -> list[list[str]]:
        result = []
        for row in range(self._in_table.rowCount()):
            row_vals = []
            for c in range(self._in_table.columnCount()):
                item = self._in_table.item(row, c)
                row_vals.append(item.text().strip() if item else "")
            result.append(row_vals)
        return result

    def _get_input_ports(self) -> list[Port]:
        return [p for p in self._ports
                if p.direction == PortDirection.IN and p.signal_class != PortSignalClass.CLOCK]

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        row = self._in_table.row(item)
        col = self._in_table.column(item)
        self._validate_cell(row, col)

    def _validate_cell(self, row: int, col: int) -> None:
        in_ports = self._get_input_ports()
        if row < 0 or row >= len(in_ports):
            return
        item = self._in_table.item(row, col)
        if item is None:
            return
        port = in_ports[row]
        text = item.text().strip()
        oob, tooltip = self._cell_out_of_range(text, port, self._get_sim_generics())
        self._in_table.blockSignals(True)
        try:
            item.setBackground(_OOB_BRUSH if oob else QBrush())
            item.setToolTip(tooltip)
        finally:
            self._in_table.blockSignals(False)

    def _validate_all_cells(self) -> None:
        in_ports = self._get_input_ports()
        sim_generics = self._get_sim_generics()
        self._in_table.blockSignals(True)
        try:
            for row, port in enumerate(in_ports):
                for col in range(self._in_table.columnCount()):
                    item = self._in_table.item(row, col)
                    if item is None:
                        continue
                    oob, tooltip = self._cell_out_of_range(item.text().strip(), port, sim_generics)
                    item.setBackground(_OOB_BRUSH if oob else QBrush())
                    item.setToolTip(tooltip)
        finally:
            self._in_table.blockSignals(False)

    @staticmethod
    def _cell_out_of_range(text: str, port: Port, generics: dict[str, Any]) -> tuple[bool, str]:
        """Return (is_out_of_range, tooltip_hint) for a cell value."""
        if not text:
            return False, ""
        try:
            raw = float(text)
        except ValueError:
            return True, "Not a valid number"

        kind_str = SimulationPanel._resolve_kind_str(port, generics)
        if kind_str in _BIT_KINDS:
            if raw not in (0.0, 1.0):
                return True, "Must be 0 or 1"
            return False, ""

        if kind_str not in (SignalKind.SFIXED.value, SignalKind.UFIXED.value):
            return False, ""

        try:
            fmt = port.signal_type.to_fpformat(generics)
            if raw < fmt.real_min or raw > fmt.real_max:
                return True, f"Out of range [{fmt.real_min:.6g}, {fmt.real_max:.6g}]"
            return False, ""
        except Exception:
            return False, ""

    # ------------------------------------------------------------------
    # Simulation

    def _run_simulation(self) -> None:
        self._status.setVisible(False)

        sim_generics = self._get_sim_generics()
        in_ports  = [p for p in self._ports if p.direction == PortDirection.IN  and p.signal_class != PortSignalClass.CLOCK]
        out_ports = [p for p in self._ports if p.direction == PortDirection.OUT]

        if not in_ports and not out_ports:
            self._show_error("No ports defined.")
            return

        raw_data = self._get_input_data()

        # Float pass: plain floats passed to FloatSimNamespace (identity quantize).
        # Fixed pass: inputs pre-quantized to port precision (plain float with
        #             reduced precision), executor uses real fixedpoint namespace.
        float_inputs: dict[str, list[Any | None]] = {p.name: [] for p in in_ports}
        fp_inputs:    dict[str, list[Any | None]] = {p.name: [] for p in in_ports}

        for row, port in enumerate(in_ports):
            row_strs = raw_data[row] if row < len(raw_data) else []
            for c in range(self._n_cycles):
                txt = row_strs[c] if c < len(row_strs) else ""
                fval = self._parse_float(txt, port, sim_generics)
                float_inputs[port.name].append(fval)
                fp_inputs[port.name].append(self._quantize(fval, port, sim_generics))

        float_outputs: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}
        fp_outputs:    dict[str, list[Any | None]] = {p.name: [] for p in out_ports}

        code = self._behavior_getter().strip()
        if not code:
            self._show_error("No behavior code to simulate.")
            return

        exec_ports = [p for p in in_ports if p.signal_class != PortSignalClass.RESET]

        # Float/ideal executor: use ideal_code when available, otherwise
        # run the same code under FloatSimNamespace (quantize → identity).
        ideal_code = self._ideal_code_getter()
        try:
            executor_float = BehaviorExecutor(
                code_body=ideal_code if ideal_code else code,
                param_names=[p.name for p in exec_ports],
                name="sim_float",
                extra_ns=sim_generics,
                float_mode=True,
            )
        except Exception as exc:
            self._show_error(f"Compile error (float):\n{exc}")
            return

        self._run_executor(executor_float, exec_ports, float_inputs, float_outputs, out_ports)

        # Fixed-point executor: original code with real fixedpoint namespace.
        try:
            executor_fixed = BehaviorExecutor(
                code_body=code,
                param_names=[p.name for p in exec_ports],
                name="sim_fixed",
                extra_ns=sim_generics,
                float_mode=False,
            )
            self._run_executor(executor_fixed, exec_ports, fp_inputs, fp_outputs, out_ports)
        except Exception:
            # If the fixed executor fails to compile (e.g. bit-op-only code),
            # fill with None so the float lane still shows.
            for port in out_ports:
                fp_outputs[port.name] = [None] * self._n_cycles

        # Build wave signals — inputs as plain lanes, outputs as combined
        # (float_val, fixed_val) tuples matching the design simulation panel.
        wave_signals: list[WaveSignal] = []
        for port in in_ports:
            kind_str = self._resolve_kind_str(port, sim_generics)
            wave_signals.append(WaveSignal(
                name=port.name,
                is_input=True,
                is_bit=(kind_str in _BIT_KINDS),
                values=float_inputs[port.name],
            ))

        for port in out_ports:
            kind_str = self._resolve_kind_str(port, sim_generics)
            is_bit = kind_str in _BIT_KINDS
            if is_bit:
                wave_signals.append(WaveSignal(
                    name=port.name,
                    is_input=False,
                    is_bit=True,
                    values=float_outputs[port.name],
                ))
            else:
                combined: list[Any | None] = []
                for fv, xv in zip(float_outputs[port.name], fp_outputs[port.name]):
                    combined.append(None if fv is None else (fv, xv))
                wave_signals.append(WaveSignal(
                    name=port.name,
                    is_input=False,
                    is_bit=False,
                    values=combined,
                ))

        self._waveform.set_data(wave_signals, self._n_cycles)
        self._wave_scroll.update()

    def _run_executor(
        self,
        executor: BehaviorExecutor,
        exec_ports: list[Port],
        input_vals: dict[str, list[Any | None]],
        output_vals: dict[str, list[Any | None]],
        out_ports: list[Port],
    ) -> None:
        for cyc in range(self._n_cycles):
            src = cyc - self._latency
            if src < 0:
                for port in out_ports:
                    output_vals[port.name].append(None)
                continue

            args = []
            any_none = False
            for port in exec_ports:
                v = input_vals[port.name][src]
                if v is None:
                    any_none = True
                args.append(v)

            if any_none:
                for port in out_ports:
                    output_vals[port.name].append(None)
                continue

            try:
                result = executor(*args)
                if len(out_ports) == 1:
                    output_vals[out_ports[0].name].append(self._extract_value(result))
                elif len(out_ports) > 1 and isinstance(result, (tuple, list)):
                    for port, r in zip(out_ports, result):
                        output_vals[port.name].append(self._extract_value(r))
                else:
                    for port in out_ports:
                        output_vals[port.name].append(None)
            except Exception as exc:
                self._show_error(f"Cycle {cyc}: {exc}")
                for port in out_ports:
                    output_vals[port.name].append(None)

    # ------------------------------------------------------------------
    # Value conversion helpers

    @staticmethod
    def _resolve_kind_str(port: Port, generics: dict[str, Any]) -> str:
        k = port.signal_type.resolved_kind(generics)
        return k.value if k else port.signal_type.kind

    @staticmethod
    def _parse_float(text: str, port: Port, generics: dict[str, Any]) -> Any | None:
        """Parse cell text as a Python value. Always returns plain float for data ports."""
        text = text.strip()
        if not text:
            return None
        try:
            raw = float(text)
        except ValueError:
            return None

        kind_str = SimulationPanel._resolve_kind_str(port, generics)
        if kind_str in (SignalKind.STD_LOGIC.value, SignalKind.STD_ULOGIC.value):
            return bool(int(raw))
        if kind_str == SignalKind.BOOLEAN.value:
            return bool(raw)
        if kind_str == SignalKind.INTEGER.value:
            return int(round(raw))
        # sfixed, ufixed, or any other type → plain float
        return raw

    @staticmethod
    def _quantize(value: Any | None, port: Port, generics: dict[str, Any]) -> Any | None:
        """Pre-quantize *value* to the nearest representable plain float for the port.

        Uses the fixedpoint library for format resolution so the result is
        consistent with the fixed-point simulation pass.
        """
        if value is None:
            return None
        kind_str = SimulationPanel._resolve_kind_str(port, generics)
        if kind_str not in (SignalKind.SFIXED.value, SignalKind.UFIXED.value):
            return value
        try:
            int_g = {n: int(v) for n, v in generics.items() if isinstance(v, (int, float))}
            fmt = port.signal_type.to_fpformat(int_g)
            return float(fmt.quantize(np.array(float(value))))
        except Exception:
            return value

    @staticmethod
    def _extract_value(result: Any) -> Any | None:
        """Convert any BehaviorExecutor result to a plain Python scalar (float/bool/int)."""
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
