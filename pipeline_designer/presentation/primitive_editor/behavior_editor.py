"""Behavior editor widget: generated signature + code body + simulation panel.

Layout (vertical splitter):
  Top  — read-only generated signature + editable pseudo-code body
  Bottom — multi-cycle Python-mode simulation panel
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Generic, Port, PortDirection
from pipeline_designer.domain.models.behavior import ComponentBehavior
from .simulation_panel import SimulationPanel


class BehaviorEditor(QWidget):
    """Code-body editor for a component's behavior plus simulation panel.

    Usage::

        editor = BehaviorEditor()
        editor.set_behavior(component.behavior, component.ports, component.generics)
        behavior = editor.get_behavior()
    """

    data_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ports:    list[Port]    = []
        self._generics: list[Generic] = []
        self._latency:  int           = 0
        self._behavior: ComponentBehavior = ComponentBehavior()
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Top pane: signature + code ────────────────────────────────
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(4, 4, 4, 4)
        top_layout.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        top_layout.addWidget(sep)

        top_layout.addWidget(QLabel("Generated signature (derived from port declarations):"))
        self._signature_label = QLabel()
        self._signature_label.setFont(QFont("Monospace", 9))
        self._signature_label.setStyleSheet(
            "background: #1a1a2e; color: #a0c4ff; padding: 6px; border-radius: 4px;"
        )
        self._signature_label.setWordWrap(True)
        top_layout.addWidget(self._signature_label)

        top_layout.addWidget(QLabel("Functional pseudo-code (body only):"))
        self._code_edit = QTextEdit()
        self._code_edit.setFont(QFont("Monospace", 10))
        self._code_edit.setPlaceholderText(
            "# Write the function body here.\n"
            "# Port names are available as variables.\n"
            "# Signed(msb, lsb) / Unsigned(msb, lsb) / Bits(n) / Const(fmt, val)\n"
            "# Example:\n"
            "#   return saturate(a + b, Signed[msb:-frac])"
        )
        self._code_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._code_edit.textChanged.connect(self._on_changed)
        top_layout.addWidget(self._code_edit)
        splitter.addWidget(top)

        # ── Bottom pane: simulation panel ─────────────────────────────
        self._sim_panel = SimulationPanel(
            behavior_getter=lambda: self._code_edit.toPlainText(),
            ideal_code_getter=lambda: self._behavior.ideal_code,
        )
        splitter.addWidget(self._sim_panel)

        splitter.setSizes([300, 420])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Public interface

    def set_behavior(
        self,
        behavior: ComponentBehavior,
        ports: list[Port],
        generics: list[Generic] | None = None,
        latency: int = 0,
    ) -> None:
        """Populate from a ComponentBehavior, port list, optional generics and pipeline latency."""
        self._ports    = list(ports)
        self._generics = list(generics or [])
        self._latency  = max(0, latency)
        self._behavior = behavior
        self._code_edit.blockSignals(True)
        self._code_edit.setPlainText(behavior.code)
        self._code_edit.blockSignals(False)
        self._refresh_signature()
        self._sim_panel.set_context(self._ports, self._generics, self._latency)

    def get_behavior(self) -> ComponentBehavior:
        """Collect a ComponentBehavior from the current UI state."""
        return ComponentBehavior(code=self._code_edit.toPlainText())

    def refresh_ports(
        self,
        ports: list[Port],
        generics: list[Generic] | None = None,
    ) -> None:
        """Call when the port list changes — updates the derived signature."""
        self._ports = list(ports)
        if generics is not None:
            self._generics = list(generics)
        self._refresh_signature()
        self._sim_panel.set_context(self._ports, self._generics, self._latency)

    # ------------------------------------------------------------------
    # Private helpers

    def _refresh_signature(self) -> None:
        args: list[str] = []
        out_types: list[str] = []

        for port in self._ports:
            ann = port.signal_type.to_python_annotation()
            if port.direction == PortDirection.IN:
                args.append(f"{port.name}: {ann}")
            else:
                out_types.append(ann)

        if not out_types:
            ret = "None"
        elif len(out_types) == 1:
            ret = out_types[0]
        else:
            ret = f"tuple[{', '.join(out_types)}]"

        sig = f"def compute({', '.join(args)}) -> {ret}:"
        self._signature_label.setText(sig)

    def _on_changed(self) -> None:
        self.data_changed.emit()
