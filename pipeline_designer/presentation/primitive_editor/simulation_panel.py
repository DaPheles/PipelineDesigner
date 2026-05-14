"""Simulation evaluation panel for primitive behavior testing.

Provides a cycle-accurate Python-mode simulation of behavior pseudo-code.
Inputs are parsed as plain Python floats and passed directly to the
BehaviorExecutor — no fixed-point quantization in the hot path.  This
gives ideal floating-point results that match the mathematical intent.

Co-simulation mode
------------------
When the Co-sim toggle is active the panel runs two passes per click:
  1. Float pass  — inputs are plain floats; executor runs normal Python arithmetic.
  2. Fixed pass  — inputs are quantized to the port's declared sfixed/ufixed
                   format (truncation + wrapping) before being passed to the
                   executor.  The executor still runs with plain floats (the
                   quantized value IS a Python float with reduced precision).

Both results appear as separate waveform lanes so the representation error is
visible directly.  No external fixed-point library is required — quantization
is implemented with integer bit-masking.

Generic types (``signal_kind``) must be set concretely in the
"Simulation Generics" section before running.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
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
from pipeline_designer.domain.models.behavior import SignalKind, _eval_index
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


# ── Fixed-point quantization (no external library) ────────────────────────────

def _fp_quantize(value: float, width: int, lsb: int, signed: bool) -> float:
    """Quantize *value* to a fixed-point format and return the representable float.

    Uses truncation-toward-zero and wrapping overflow — matches typical HDL
    behaviour for arithmetic results that overflow the destination format.

    Args:
        value:  Real-valued input.
        width:  Total bit width (integer + fractional bits).
        lsb:    Position of the LSB (negative for fractional bits).
        signed: True for sfixed (two's complement), False for ufixed.
    """
    step = 2.0 ** lsb
    raw = math.trunc(value / step)   # truncate toward zero
    mask = (1 << width) - 1
    raw = int(raw) & mask            # wrap to width bits
    if signed and raw >= (1 << (width - 1)):
        raw -= 1 << width
    return raw * step


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._behavior_getter = behavior_getter
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
        hbar.addSpacing(12)

        self._cosim_cb = QCheckBox("Co-sim (float + fixed-point)")
        self._cosim_cb.setStyleSheet("color:#cdd6f4; font-size:8pt;")
        self._cosim_cb.setToolTip(
            "Run a second pass with inputs quantized to the declared sfixed/ufixed\n"
            "format so you can compare ideal vs. fixed-point results."
        )
        hbar.addWidget(self._cosim_cb)
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
        in_layout.addWidget(self._in_table)
        splitter.addWidget(in_frame)

        wave_frame = QWidget()
        wave_layout = QVBoxLayout(wave_frame)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        wave_layout.setSpacing(2)
        wave_label = QLabel("Waveform  (blue = input, green = output)")
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

    def _set_table_col_headers(self) -> None:
        self._in_table.setHorizontalHeaderLabels(
            [str(c) for c in range(self._in_table.columnCount())]
        )

    def _on_cycles_changed(self, n: int) -> None:
        self._n_cycles = n
        data = self._get_input_data()
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

    def _get_input_data(self) -> list[list[str]]:
        result = []
        for row in range(self._in_table.rowCount()):
            row_vals = []
            for c in range(self._in_table.columnCount()):
                item = self._in_table.item(row, c)
                row_vals.append(item.text().strip() if item else "")
            result.append(row_vals)
        return result

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
        cosim = self._cosim_cb.isChecked()

        # Parse inputs as plain floats (primary mode)
        float_inputs: dict[str, list[Any | None]] = {p.name: [] for p in in_ports}
        fp_inputs:    dict[str, list[Any | None]] = {p.name: [] for p in in_ports} if cosim else {}

        for row, port in enumerate(in_ports):
            row_strs = raw_data[row] if row < len(raw_data) else []
            for c in range(self._n_cycles):
                txt = row_strs[c] if c < len(row_strs) else ""
                fval = self._parse_float(txt, port, sim_generics)
                float_inputs[port.name].append(fval)
                if cosim:
                    fp_inputs[port.name].append(
                        self._quantize(fval, port, sim_generics)
                    )

        float_outputs: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}
        fp_outputs:    dict[str, list[Any | None]] = {p.name: [] for p in out_ports} if cosim else {}

        port_names_lower = {p.name.lower() for p in self._ports}
        is_register = {"d", "q", "clk"}.issubset(port_names_lower)

        if is_register:
            self._fill_register_outputs(float_inputs, float_outputs, sim_generics)
            if cosim:
                self._fill_register_outputs(fp_inputs, fp_outputs, sim_generics)
        else:
            code = self._behavior_getter().strip()
            if not code:
                self._show_error("No behavior code to simulate.")
                return

            exec_ports = [p for p in in_ports if p.signal_class != PortSignalClass.RESET]
            try:
                executor = BehaviorExecutor(
                    code_body=code,
                    param_names=[p.name for p in exec_ports],
                    name="sim",
                    extra_ns=sim_generics,
                )
            except SyntaxError as exc:
                self._show_error(f"Syntax error:\n{exc}")
                return
            except Exception as exc:
                self._show_error(f"Compile error:\n{exc}")
                return

            is_stateful = "state" in code
            self._run_executor(executor, exec_ports, float_inputs, float_outputs, out_ports, is_stateful)

            if cosim:
                # Re-use same compiled executor with fp-quantized inputs
                executor.reset_state()
                self._run_executor(executor, exec_ports, fp_inputs, fp_outputs, out_ports, is_stateful)

        # Build wave signals
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
            label = port.name + " (float)" if cosim else port.name
            wave_signals.append(WaveSignal(
                name=label,
                is_input=False,
                is_bit=is_bit,
                values=float_outputs[port.name],
            ))
            if cosim:
                wave_signals.append(WaveSignal(
                    name=port.name + " (fp)",
                    is_input=False,
                    is_bit=is_bit,
                    values=fp_outputs[port.name],
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
        is_stateful: bool,
    ) -> None:
        for cyc in range(self._n_cycles):
            src = cyc if is_stateful else (cyc - self._latency)
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
                for port in out_ports:
                    output_vals[port.name].append(None)

    def _fill_register_outputs(
        self,
        input_vals: dict[str, list[Any | None]],
        output_vals: dict[str, list[Any | None]],
        sim_generics: dict[str, Any],
    ) -> None:
        reset_type = str(sim_generics.get("RESET_TYPE", "sync")).lower()
        polarity   = str(sim_generics.get("RESET_POLARITY", "high")).lower()

        d_vals   = next((v for k, v in input_vals.items() if k.lower() == "d"),
                        [None] * self._n_cycles)
        rst_vals = next((v for k, v in input_vals.items() if k.lower() == "rst"),
                        [None] * self._n_cycles)

        def rst_active(val: Any | None) -> bool:
            if val is None:
                return False
            return bool(val) if polarity == "high" else not bool(val)

        q_vals: list[Any | None] = []
        for n in range(self._n_cycles):
            rst_now  = rst_vals[n]     if n < len(rst_vals) else None
            rst_prev = rst_vals[n - 1] if n > 0 and (n - 1) < len(rst_vals) else None
            d_prev   = d_vals[n - 1]   if n > 0 and (n - 1) < len(d_vals)   else None

            if reset_type == "async" and rst_active(rst_now):
                q_vals.append(0)
            elif n == 0:
                q_vals.append(None)
            elif reset_type == "sync" and rst_active(rst_prev):
                q_vals.append(0)
            else:
                q_vals.append(d_prev)

        q_key = next((k for k in output_vals if k.lower() == "q"), None)
        if q_key is not None:
            output_vals[q_key] = q_vals

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
        """Quantize *value* to the port's fixed-point format for the co-sim pass."""
        if value is None:
            return None
        kind_str = SimulationPanel._resolve_kind_str(port, generics)
        if kind_str not in (SignalKind.SFIXED.value, SignalKind.UFIXED.value):
            return value  # not a fixed-point port — pass through as-is
        try:
            int_g = {
                n: int(v) for n, v in generics.items()
                if isinstance(v, (int, float))
            }
            w = _eval_index(port.signal_type.width, int_g)
            l = _eval_index(port.signal_type.lsb,   int_g)
            signed = (kind_str == SignalKind.SFIXED.value)
            return _fp_quantize(float(value), w, l, signed)
        except Exception:
            return value  # fall back to float if format can't be resolved

    @staticmethod
    def _extract_value(result: Any) -> Any | None:
        """Convert a BehaviorExecutor return value to a plain Python scalar."""
        if result is None:
            return None
        # numpy scalar or array with .item()
        if hasattr(result, "item"):
            try:
                return float(result.item())
            except Exception:
                pass
        # Plain scalar
        if isinstance(result, bool):
            return result
        if isinstance(result, (int, float)):
            return result
        # FixedPointArray: access the float representation via .values
        if hasattr(result, "values"):
            try:
                import numpy as np
                return float(np.asarray(result.values).flat[0])
            except Exception:
                pass
        # Last resort
        try:
            return float(result)
        except Exception:
            return None

    def _show_error(self, msg: str) -> None:
        self._status.setText(msg)
        self._status.setVisible(True)
