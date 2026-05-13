"""Simulation evaluation panel for primitive behavior testing.

Provides a cycle-accurate Python-mode simulation of behavior pseudo-code.
Inputs are set per-cycle in a table; outputs are computed by the BehaviorExecutor
and displayed in a WaveDrom-inspired waveform view.

Signal-type interpretation
--------------------------
  signed / unsigned ports   → quantized to FPFormat via signal_type.to_fpformat()
                               (requires fixedpoint package)
  std_logic / std_ulogic    → bool  (cell value: 0 or 1)
  integer / boolean         → Python int / bool
  std_logic_vector and other vector types → plain float fallback

Generic types (``signal_kind``) must be set concretely in the
"Simulation Generics" section before running.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Generic, Port, PortDirection
from pipeline_designer.domain.models.behavior import (
    SignalKind,
    SignalType,
    _eval_index,
)
from pipeline_designer.domain.simulation.executor import BehaviorExecutor

# ── Colour palette ────────────────────────────────────────────────────────────

_C_BG         = QColor("#1e1e2e")
_C_BG_ALT     = QColor("#252538")
_C_GRID       = QColor("#45475a")
_C_LABEL_IN   = QColor("#89b4fa")   # blue   — input ports
_C_LABEL_OUT  = QColor("#a6e3a1")   # green  — computed outputs
_C_BIT_HIGH   = QColor("#a6e3a1")   # green wire high
_C_BIT_LOW    = QColor("#313244")   # dark   wire low (drawn on dark bg)
_C_BUS        = QColor("#89dceb")   # cyan   bus rails
_C_BUS_FILL   = QColor("#1e2e3e")   # dark bus fill
_C_UNKNOWN    = QColor("#f38ba8")   # red    unknown / X
_C_TEXT       = QColor("#cdd6f4")   # light  text on bus
_C_HEADER     = QColor("#313244")   # header row background
_C_HDR_TEXT   = QColor("#6c7086")   # header cycle-number text


# ── Waveform data model ───────────────────────────────────────────────────────

@dataclass
class WaveSignal:
    """One signal lane in the waveform view."""

    name:     str
    is_input: bool
    is_bit:   bool             # True → two-level digital wave
    values:   list[Any | None] # per-cycle; None = unknown / X


# ── Waveform widget ───────────────────────────────────────────────────────────

class WaveformWidget(QWidget):
    """QPainter-based WaveDrom-inspired waveform renderer.

    Call ``set_data(signals, n_cycles)`` to refresh.  Sized automatically to
    fit all signals and cycles; wrap in a ``QScrollArea`` for overflow.
    """

    HEADER_H = 20    # cycle-number header row
    LANE_H   = 36    # height per signal lane
    LABEL_W  = 130   # left label area
    CYCLE_W  = 64    # pixels per cycle
    PAD_V    = 7     # vertical padding inside each lane
    CHEV_W   = 9     # chevron half-width at bus transitions

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._signals:  list[WaveSignal] = []
        self._n_cycles: int = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {_C_BG.name()};")

    def set_data(self, signals: list[WaveSignal], n_cycles: int) -> None:
        self._signals  = signals
        self._n_cycles = n_cycles
        total_h = self.HEADER_H + max(1, len(signals)) * self.LANE_H
        total_w = self.LABEL_W  + max(1, n_cycles) * self.CYCLE_W
        self.setFixedSize(total_w, total_h)
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._signals or self._n_cycles == 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        W = self.width()
        H = self.height()

        p.fillRect(0, 0, W, H, _C_BG)
        self._draw_header(p)
        for idx, sig in enumerate(self._signals):
            lane_y = self.HEADER_H + idx * self.LANE_H
            if idx % 2 == 1:
                p.fillRect(0, lane_y, W, self.LANE_H, _C_BG_ALT)
            self._draw_grid(p, lane_y)
            self._draw_label(p, sig, lane_y)
            if sig.is_bit:
                self._draw_bit_wave(p, sig, lane_y)
            else:
                self._draw_bus_wave(p, sig, lane_y)

        # Vertical separator: label | waveform
        p.setPen(QPen(_C_GRID, 1))
        p.drawLine(self.LABEL_W, 0, self.LABEL_W, H)
        p.end()

    # ------------------------------------------------------------------
    def _draw_header(self, p: QPainter) -> None:
        p.fillRect(0, 0, self.width(), self.HEADER_H, _C_HEADER)
        font = QFont("Monospace", 7)
        p.setFont(font)
        p.setPen(QPen(_C_HDR_TEXT))
        fm = QFontMetrics(font)
        # Cycle numbers
        for c in range(self._n_cycles):
            x = self.LABEL_W + c * self.CYCLE_W
            label = str(c)
            tw = fm.horizontalAdvance(label)
            p.drawText(x + (self.CYCLE_W - tw) // 2, self.HEADER_H - 4, label)
        # Horizontal separator
        p.setPen(QPen(_C_GRID, 1))
        p.drawLine(0, self.HEADER_H - 1, self.width(), self.HEADER_H - 1)

    def _draw_grid(self, p: QPainter, lane_y: int) -> None:
        pen = QPen(_C_GRID, 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        for c in range(self._n_cycles + 1):
            x = self.LABEL_W + c * self.CYCLE_W
            p.drawLine(x, lane_y, x, lane_y + self.LANE_H)

    def _draw_label(self, p: QPainter, sig: WaveSignal, lane_y: int) -> None:
        font = QFont("Monospace", 8)
        p.setFont(font)
        fm = QFontMetrics(font)
        color = _C_LABEL_IN if sig.is_input else _C_LABEL_OUT
        p.setPen(QPen(color))
        text = fm.elidedText(sig.name, Qt.TextElideMode.ElideRight, self.LABEL_W - 8)
        ty = lane_y + (self.LANE_H + fm.ascent() - fm.descent()) // 2
        p.drawText(4, ty, text)

    # ── Bit (digital two-level) wave ──────────────────────────────────
    def _draw_bit_wave(self, p: QPainter, sig: WaveSignal, lane_y: int) -> None:
        y_hi = lane_y + self.PAD_V
        y_lo = lane_y + self.LANE_H - self.PAD_V
        y_md = (y_hi + y_lo) // 2

        pen_hi  = QPen(_C_BIT_HIGH, 2)
        pen_lo  = QPen(QColor("#89b4fa"), 1)  # faint low rail
        pen_unk = QPen(_C_UNKNOWN,  2)

        prev = None
        for c, val in enumerate(sig.values):
            x0 = self.LABEL_W + c * self.CYCLE_W
            x1 = x0 + self.CYCLE_W

            if val is None:
                p.fillRect(x0 + 1, y_hi, self.CYCLE_W - 1, y_lo - y_hi,
                           QColor(243, 139, 168, 60))
                p.setPen(pen_unk)
                p.drawRect(x0 + 1, y_hi, self.CYCLE_W - 2, y_lo - y_hi)
                font = QFont("Monospace", 8, QFont.Weight.Bold)
                p.setFont(font)
                p.setPen(QPen(_C_UNKNOWN))
                p.drawText(x0 + (self.CYCLE_W - 8) // 2, y_md + 4, "X")
                prev = None
            else:
                bit = bool(val)
                y_cur = y_hi if bit else y_lo
                y_prv = y_hi if (bool(prev) if prev is not None else not bit) else y_lo

                p.setPen(pen_hi if bit else QPen(QColor("#585b70"), 1))
                # Transition vertical line
                if prev is not None and bool(prev) != bit:
                    p.drawLine(x0, y_prv, x0, y_cur)
                p.drawLine(x0 if c == 0 else x0, y_cur, x1, y_cur)
                prev = val

    # ── Bus (numeric) wave ────────────────────────────────────────────
    def _draw_bus_wave(self, p: QPainter, sig: WaveSignal, lane_y: int) -> None:
        y_top = lane_y + self.PAD_V + 3
        y_bot = lane_y + self.LANE_H - self.PAD_V - 3
        y_mid = (y_top + y_bot) // 2

        pen_bus = QPen(_C_BUS, 2)
        pen_unk = QPen(_C_UNKNOWN, 2)

        font = QFont("Monospace", 8)
        p.setFont(font)
        fm = QFontMetrics(font)

        prev = _SENTINEL = object()
        for c, val in enumerate(sig.values):
            x0 = self.LABEL_W + c * self.CYCLE_W
            x1 = x0 + self.CYCLE_W
            changed = (val != prev) if prev is not _SENTINEL else True
            cw = self.CHEV_W if (changed and c > 0) else 0
            body_x = x0 + cw

            if val is None:
                p.fillRect(body_x + 1, y_top, x1 - body_x - 1, y_bot - y_top,
                           QColor(243, 139, 168, 50))
                p.setPen(pen_unk)
                p.drawLine(body_x, y_top, x1, y_top)
                p.drawLine(body_x, y_bot, x1, y_bot)
                if cw:
                    p.drawLine(x0, y_mid, body_x, y_top)
                    p.drawLine(x0, y_mid, body_x, y_bot)
                elif c == 0:
                    p.drawLine(x0, y_top, x0, y_bot)
                txt = "X"
                p.setPen(QPen(_C_UNKNOWN))
                tw = fm.horizontalAdvance(txt)
                avail = x1 - body_x - 4
                if avail > tw:
                    p.drawText(body_x + (avail - tw) // 2 + 2, y_mid + 4, txt)
            else:
                p.fillRect(body_x + 1, y_top + 1, x1 - body_x - 1, y_bot - y_top - 1,
                           _C_BUS_FILL)
                p.setPen(pen_bus)
                p.drawLine(body_x, y_top, x1, y_top)
                p.drawLine(body_x, y_bot, x1, y_bot)
                if cw:
                    p.drawLine(x0, y_mid, body_x, y_top)
                    p.drawLine(x0, y_mid, body_x, y_bot)
                elif c == 0:
                    p.drawLine(x0, y_top, x0, y_bot)
                txt = _fmt_value(val)
                p.setPen(QPen(_C_TEXT))
                avail = x1 - body_x - 6
                txt = fm.elidedText(txt, Qt.TextElideMode.ElideRight, max(avail, 4))
                tw = fm.horizontalAdvance(txt)
                if avail > 4:
                    p.drawText(body_x + (avail - tw) // 2 + 4, y_mid + 4, txt)
            prev = val

        # Right end-cap
        x_end = self.LABEL_W + self._n_cycles * self.CYCLE_W
        last = sig.values[-1] if sig.values else None
        p.setPen(pen_bus if last is not None else pen_unk)
        if last is not None:
            p.drawLine(x_end, y_top, x_end, y_bot)


def _fmt_value(val: Any) -> str:
    if val is None:
        return "X"
    if hasattr(val, "item"):
        val = val.item()
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, float):
        return f"{val:.5g}"
    if isinstance(val, int):
        return str(val)
    return str(val)[:14]


# ── Simulation panel ──────────────────────────────────────────────────────────

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

        # Input table
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

        # Waveform
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
        # Clear old widgets
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
                default = str(g.default_value or "signed")
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
            if p.direction == PortDirection.IN and not p.is_clock
        ]
        # Preserve existing cell values by port name before clearing
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
        """Return raw cell strings [row][col]."""
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
        in_ports  = [p for p in self._ports if p.direction == PortDirection.IN  and not p.is_clock]
        out_ports = [p for p in self._ports if p.direction == PortDirection.OUT]

        if not in_ports and not out_ports:
            self._show_error("No ports defined.")
            return

        # Read raw input strings from table
        raw_data = self._get_input_data()

        # Per-cycle input values: port_name → [v0, v1, ...]
        input_vals: dict[str, list[Any | None]] = {p.name: [] for p in in_ports}
        for row, port in enumerate(in_ports):
            row_strs = raw_data[row] if row < len(raw_data) else []
            for c in range(self._n_cycles):
                txt = row_strs[c] if c < len(row_strs) else ""
                input_vals[port.name].append(
                    self._parse_input(txt, port, sim_generics)
                )

        # Per-cycle output values: port_name → [v0, v1, ...]
        output_vals: dict[str, list[Any | None]] = {p.name: [] for p in out_ports}

        # Detect register: structural delay + reset, no executor
        port_names_lower = {p.name.lower() for p in self._ports}
        if {"d", "q", "clk"}.issubset(port_names_lower):
            self._fill_register_outputs(input_vals, output_vals, sim_generics)
        else:
            code = self._behavior_getter().strip()
            if not code:
                self._show_error("No behavior code to simulate.")
                return

            exec_ports = [p for p in in_ports if not p.is_reset]
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

            # Stateful behavior (shift registers, accumulators) manages its own
            # timing via `state`; latency shifting must not be applied on top.
            is_stateful = "state" in code

            for cyc in range(self._n_cycles):
                # Pure pipeline latency: output at cycle N comes from input at N-latency.
                # Stateful code handles its own delay internally — no shift needed.
                src = cyc if is_stateful else (cyc - self._latency)
                if src < 0:
                    # Pipeline not yet filled — output is unknown
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
                        output_vals[out_ports[0].name].append(
                            self._extract_value(result)
                        )
                    elif len(out_ports) > 1 and isinstance(result, (tuple, list)):
                        for port, r in zip(out_ports, result):
                            output_vals[port.name].append(self._extract_value(r))
                    else:
                        for port in out_ports:
                            output_vals[port.name].append(None)
                except Exception:
                    for port in out_ports:
                        output_vals[port.name].append(None)

        # Build wave signals
        wave_signals: list[WaveSignal] = []
        for port in in_ports:
            kind_str = self._resolve_kind_str(port, sim_generics)
            wave_signals.append(WaveSignal(
                name=port.name,
                is_input=True,
                is_bit=(kind_str in _BIT_KINDS),
                values=input_vals[port.name],
            ))
        for port in out_ports:
            kind_str = self._resolve_kind_str(port, sim_generics)
            wave_signals.append(WaveSignal(
                name=port.name,
                is_input=False,
                is_bit=(kind_str in _BIT_KINDS),
                values=output_vals[port.name],
            ))

        self._waveform.set_data(wave_signals, self._n_cycles)
        self._wave_scroll.update()

    def _fill_register_outputs(
        self,
        input_vals: dict[str, list[Any | None]],
        output_vals: dict[str, list[Any | None]],
        sim_generics: dict[str, Any],
    ) -> None:
        """Populate output_vals for a register using structural delay + reset semantics.

        Async reset overrides q immediately (same cycle, no clock edge needed).
        Sync reset is sampled at the clock edge (takes effect the cycle after rst asserts).
        """
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
                q_vals.append(None)    # initial state unknown before first clock edge
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

    def _parse_input(
        self, text: str, port: Port, generics: dict[str, Any]
    ) -> Any | None:
        text = text.strip()
        if not text:
            return None

        kind_str = self._resolve_kind_str(port, generics)

        try:
            raw = float(text)
        except ValueError:
            return None

        if kind_str in (SignalKind.STD_LOGIC.value, SignalKind.STD_ULOGIC.value):
            return bool(int(raw))
        if kind_str == SignalKind.BOOLEAN.value:
            return bool(raw)
        if kind_str == SignalKind.INTEGER.value:
            return int(round(raw))

        # Fixed-point: try to quantize
        if kind_str in (SignalKind.SIGNED.value, SignalKind.UNSIGNED.value):
            try:
                fmt = port.signal_type.to_fpformat(generics)
                return fmt.quantize(np.array(raw))
            except Exception:
                pass

        return raw

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
