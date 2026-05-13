"""Shared WaveDrom-inspired waveform renderer.

Used by both the primitive simulation panel and the design simulation panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget


# ── Colour palette ────────────────────────────────────────────────────────────

_C_BG         = QColor("#1e1e2e")
_C_BG_ALT     = QColor("#252538")
_C_GRID       = QColor("#45475a")
_C_LABEL_IN   = QColor("#89b4fa")   # blue   — input ports
_C_LABEL_OUT  = QColor("#a6e3a1")   # green  — computed outputs
_C_BIT_HIGH   = QColor("#a6e3a1")   # green wire high
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
    is_bit:   bool              # True → two-level digital wave
    values:   list[Any | None]  # per-cycle; None = unknown / X


# ── Value formatter ───────────────────────────────────────────────────────────

def fmt_value(val: Any) -> str:
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

        p.setPen(QPen(_C_GRID, 1))
        p.drawLine(self.LABEL_W, 0, self.LABEL_W, H)
        p.end()

    def _draw_header(self, p: QPainter) -> None:
        p.fillRect(0, 0, self.width(), self.HEADER_H, _C_HEADER)
        font = QFont("Monospace", 7)
        p.setFont(font)
        p.setPen(QPen(_C_HDR_TEXT))
        fm = QFontMetrics(font)
        for c in range(self._n_cycles):
            x = self.LABEL_W + c * self.CYCLE_W
            label = str(c)
            tw = fm.horizontalAdvance(label)
            p.drawText(x + (self.CYCLE_W - tw) // 2, self.HEADER_H - 4, label)
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

    def _draw_bit_wave(self, p: QPainter, sig: WaveSignal, lane_y: int) -> None:
        y_hi = lane_y + self.PAD_V
        y_lo = lane_y + self.LANE_H - self.PAD_V
        y_md = (y_hi + y_lo) // 2

        pen_hi  = QPen(_C_BIT_HIGH, 2)
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
                if prev is not None and bool(prev) != bit:
                    p.drawLine(x0, y_prv, x0, y_cur)
                p.drawLine(x0, y_cur, x1, y_cur)
                prev = val

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
                txt = fmt_value(val)
                p.setPen(QPen(_C_TEXT))
                avail = x1 - body_x - 6
                txt = fm.elidedText(txt, Qt.TextElideMode.ElideRight, max(avail, 4))
                tw = fm.horizontalAdvance(txt)
                if avail > 4:
                    p.drawText(body_x + (avail - tw) // 2 + 4, y_mid + 4, txt)
            prev = val

        x_end = self.LABEL_W + self._n_cycles * self.CYCLE_W
        last = sig.values[-1] if sig.values else None
        p.setPen(pen_bus if last is not None else pen_unk)
        if last is not None:
            p.drawLine(x_end, y_top, x_end, y_bot)
