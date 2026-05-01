"""Behavior editor widget: port-type table + functional pseudo-code body.

The port-type table assigns a fixed-point kind (SFixed / UFixed /
Bits / Bit / int / bool) and MSB/LSB indices to each port.  The editor
auto-generates the function signature from that table and shows it as a
read-only header above the editable code body.

Fixed-point type notation
-------------------------
  SFixed[msb:lsb]   →  sfixed(msb downto lsb)   (signed, lsb may be negative)
  UFixed[msb:lsb]   →  ufixed(msb downto lsb)   (unsigned)
  Bits[msb:lsb]     →  std_logic_vector(msb downto lsb)
  Bit               →  std_logic
  int               →  integer
  bool              →  boolean
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import (
    BehaviorPortType,
    ComponentBehavior,
    FixedPointKind,
    Port,
    PortDirection,
)

# Kinds that do NOT use MSB/LSB
_SCALAR_KINDS = frozenset(
    {FixedPointKind.STD_LOGIC, FixedPointKind.BOOLEAN, FixedPointKind.INTEGER}
)

_KIND_LABELS = [
    ("sfixed  – SFixed[msb:lsb]",  FixedPointKind.SFIXED),
    ("ufixed  – UFixed[msb:lsb]",  FixedPointKind.UFIXED),
    ("std_logic_vector – Bits[msb:lsb]", FixedPointKind.STD_LOGIC_VECTOR),
    ("std_logic  – Bit",            FixedPointKind.STD_LOGIC),
    ("integer  – int",              FixedPointKind.INTEGER),
    ("boolean  – bool",             FixedPointKind.BOOLEAN),
]


class BehaviorEditor(QWidget):
    """Port-type table and code body editor for a component's behavior.

    Usage::

        editor = BehaviorEditor()
        editor.set_behavior(component.behavior, component.ports)
        ...
        behavior = editor.get_behavior()
    """

    data_changed = Signal()

    _COL_NAME = 0
    _COL_KIND = 1
    _COL_MSB = 2
    _COL_LSB = 3
    _HEADERS = ["Port", "Kind", "MSB", "LSB"]

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

        # --- Port-type table ---
        layout.addWidget(QLabel("Port type annotations (for simulation):"))

        self._type_table = QTableWidget(0, len(self._HEADERS))
        self._type_table.setHorizontalHeaderLabels(self._HEADERS)
        hdr = self._type_table.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_KIND, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self._COL_MSB, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_LSB, QHeaderView.ResizeMode.ResizeToContents)
        self._type_table.setMaximumHeight(180)
        self._type_table.setAlternatingRowColors(True)
        self._type_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._type_table)

        # --- Generated signature (read-only) ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        layout.addWidget(QLabel("Generated signature (read-only):"))
        self._signature_label = QLabel()
        self._signature_label.setFont(QFont("Monospace", 9))
        self._signature_label.setStyleSheet(
            "background: #1a1a2e; color: #a0c4ff; padding: 6px; border-radius: 4px;"
        )
        self._signature_label.setWordWrap(True)
        layout.addWidget(self._signature_label)

        # --- Code body ---
        layout.addWidget(QLabel("Functional pseudo-code (body only):"))
        self._code_edit = QTextEdit()
        self._code_edit.setFont(QFont("Monospace", 10))
        self._code_edit.setPlaceholderText(
            "# Write the function body here.\n"
            "# Port names are available as variables.\n"
            "# Example:\n"
            "#   return saturate(a + b, SFixed[WIDTH:0])"
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
        self._rebuild_table(behavior)
        self._code_edit.blockSignals(True)
        self._code_edit.setPlainText(behavior.code)
        self._code_edit.blockSignals(False)
        self._refresh_signature()

    def get_behavior(self) -> ComponentBehavior:
        """Collect a ComponentBehavior from the current UI state."""
        port_types: dict[str, BehaviorPortType] = {}
        for row in range(self._type_table.rowCount()):
            name_item = self._type_table.item(row, self._COL_NAME)
            kind_combo = self._type_table.cellWidget(row, self._COL_KIND)
            msb_edit = self._type_table.cellWidget(row, self._COL_MSB)
            lsb_edit = self._type_table.cellWidget(row, self._COL_LSB)

            if not isinstance(name_item, QTableWidgetItem):
                continue
            name = name_item.text()
            kind = kind_combo.currentData() if isinstance(kind_combo, QComboBox) else FixedPointKind.STD_LOGIC_VECTOR
            msb = msb_edit.text().strip() if isinstance(msb_edit, QLineEdit) else "0"
            lsb = lsb_edit.text().strip() if isinstance(lsb_edit, QLineEdit) else "0"

            port_types[name] = BehaviorPortType(kind=kind, msb=msb, lsb=lsb)

        return ComponentBehavior(
            code=self._code_edit.toPlainText(),
            port_types=port_types,
        )

    def refresh_ports(self, ports: list[Port]) -> None:
        """Call when the port list changes (adds/removes/renames ports).

        Keeps existing type annotations for unchanged port names.
        """
        old_behavior = self.get_behavior()
        self._ports = list(ports)
        self._rebuild_table(old_behavior)
        self._refresh_signature()

    # ------------------------------------------------------------------
    # Private helpers

    def _rebuild_table(self, behavior: ComponentBehavior) -> None:
        self._type_table.blockSignals(True)
        self._type_table.setRowCount(0)

        for port in self._ports:
            row = self._type_table.rowCount()
            self._type_table.insertRow(row)

            # Name (read-only)
            name_item = QTableWidgetItem(port.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            color = (
                QColor("#3498db")
                if port.direction == PortDirection.IN
                else QColor("#2ecc71")
            )
            name_item.setForeground(color)
            self._type_table.setItem(row, self._COL_NAME, name_item)

            # Kind combo
            existing = behavior.port_types.get(port.name)
            kind_combo = QComboBox()
            for label, kind_val in _KIND_LABELS:
                kind_combo.addItem(label, kind_val)
            if existing:
                for i in range(kind_combo.count()):
                    if kind_combo.itemData(i) == existing.kind:
                        kind_combo.setCurrentIndex(i)
                        break
            kind_combo.currentIndexChanged.connect(
                lambda _, r=row: self._on_kind_changed(r)
            )
            self._type_table.setCellWidget(row, self._COL_KIND, kind_combo)

            # MSB / LSB
            for col, attr in ((self._COL_MSB, "msb"), (self._COL_LSB, "lsb")):
                edit = QLineEdit()
                edit.setPlaceholderText("0")
                edit.setFixedWidth(70)
                if existing:
                    edit.setText(getattr(existing, attr))
                edit.textChanged.connect(self._on_changed)
                self._type_table.setCellWidget(row, col, edit)

            # Disable MSB/LSB for scalar kinds
            current_kind = kind_combo.currentData()
            self._set_range_enabled(row, current_kind not in _SCALAR_KINDS)

        self._type_table.blockSignals(False)

    def _on_kind_changed(self, row: int) -> None:
        kind_combo = self._type_table.cellWidget(row, self._COL_KIND)
        if isinstance(kind_combo, QComboBox):
            kind = kind_combo.currentData()
            self._set_range_enabled(row, kind not in _SCALAR_KINDS)
        self._on_changed()

    def _set_range_enabled(self, row: int, enabled: bool) -> None:
        for col in (self._COL_MSB, self._COL_LSB):
            w = self._type_table.cellWidget(row, col)
            if isinstance(w, QLineEdit):
                w.setEnabled(enabled)

    def _refresh_signature(self) -> None:
        behavior = self.get_behavior()
        in_names = [p.name for p in self._ports if p.direction == PortDirection.IN]
        out_names = [p.name for p in self._ports if p.direction != PortDirection.IN]
        sig = behavior.generate_signature(in_names, out_names)
        self._signature_label.setText(sig)

    def _on_changed(self) -> None:
        self._refresh_signature()
        self.data_changed.emit()
