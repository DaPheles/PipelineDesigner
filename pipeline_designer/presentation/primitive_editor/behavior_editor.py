"""Behavior editor widget: generated signature + editable code body.

The function signature is derived automatically from the port ``signal_type``
declarations — no separate type annotation table is needed.  The editor shows
the signature read-only above an editable code body.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Port, PortDirection
from pipeline_designer.domain.models.behavior import ComponentBehavior


class BehaviorEditor(QWidget):
    """Code-body editor for a component's behavior.

    The function signature is read-only and derived from the current port list
    via ``port.signal_type.to_python_annotation()``.

    Usage::

        editor = BehaviorEditor()
        editor.set_behavior(component.behavior, component.ports)
        behavior = editor.get_behavior()
    """

    data_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ports: list[Port] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        layout.addWidget(QLabel("Generated signature (derived from port declarations):"))
        self._signature_label = QLabel()
        self._signature_label.setFont(QFont("Monospace", 9))
        self._signature_label.setStyleSheet(
            "background: #1a1a2e; color: #a0c4ff; padding: 6px; border-radius: 4px;"
        )
        self._signature_label.setWordWrap(True)
        layout.addWidget(self._signature_label)

        layout.addWidget(QLabel("Functional pseudo-code (body only):"))
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
        layout.addWidget(self._code_edit)

    # ------------------------------------------------------------------
    # Public interface

    def set_behavior(self, behavior: ComponentBehavior, ports: list[Port]) -> None:
        """Populate from a ComponentBehavior and the current port list."""
        self._ports = list(ports)
        self._code_edit.blockSignals(True)
        self._code_edit.setPlainText(behavior.code)
        self._code_edit.blockSignals(False)
        self._refresh_signature()

    def get_behavior(self) -> ComponentBehavior:
        """Collect a ComponentBehavior from the current UI state."""
        return ComponentBehavior(code=self._code_edit.toPlainText())

    def refresh_ports(self, ports: list[Port]) -> None:
        """Call when the port list changes — updates the derived signature."""
        self._ports = list(ports)
        self._refresh_signature()

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
