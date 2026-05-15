"""Read-optimised table widgets for the design canvas property panel.

Two widgets live here:

``InstanceGenericTable``
    Shows a component definition's generics with an editable *Value* column
    for per-instance overrides.  Name and Type are read-only.  Values may be
    integers, floats, or arbitrary strings — string values let users reference
    a design-level generic by name (e.g. typing ``WIDTH`` passes the entity
    generic down, just as in VHDL ``generic map (WIDTH => WIDTH)``).

``PortInfoTable``
    Read-only display of a component's port list with resolved VHDL type
    notation.  Used to give a quick overview when a component is selected in
    the canvas without leaving the property panel.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models.component import Port, PortDirection
from pipeline_designer.domain.models.component import Generic


def _parse_value(raw: str) -> int | float | str | None:
    """Try int → float → str.  Empty string returns None."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


class InstanceGenericTable(QWidget):
    """Table of a component's generics with an editable per-instance Value column.

    The *Name* and *Type* columns are read-only (drawn from the definition).
    The *Value* column is editable and may hold an integer, float, or a string
    that references a design-level generic name.

    Emits ``value_changed(name, value)`` whenever a Value cell is committed.
    """

    value_changed = Signal(str, object)

    _COL_NAME  = 0
    _COL_TYPE  = 1
    _COL_VALUE = 2
    _HEADERS   = ["Generic", "Type", "Value"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._generics: list[Generic] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_NAME,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_TYPE,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_VALUE, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(50)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_data(
        self,
        generics: list[Generic],
        instance_values: dict[str, Any],
    ) -> None:
        """Populate from *generics* definition; show instance_values in Value column."""
        self._generics = list(generics)
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        for g in generics:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # Name — read-only, dimmed
            name_item = QTableWidgetItem(g.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name_item.setForeground(QColor(180, 180, 180))
            self._table.setItem(row, self._COL_NAME, name_item)

            # Type — read-only, dimmed
            type_item = QTableWidgetItem(g.data_type)
            type_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            type_item.setForeground(QColor(140, 140, 140))
            self._table.setItem(row, self._COL_TYPE, type_item)

            # Value — editable; use instance override, else definition default
            raw_val = instance_values.get(g.name, g.default_value)
            val_str = str(raw_val) if raw_val is not None else ""
            val_item = QTableWidgetItem(val_str)
            val_item.setData(Qt.ItemDataRole.UserRole, g.name)
            self._table.setItem(row, self._COL_VALUE, val_item)

        self._table.blockSignals(False)
        self._adjust_height()

    def get_values(self) -> dict[str, Any]:
        """Collect current values from the table."""
        result: dict[str, Any] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, self._COL_NAME)
            val_item  = self._table.item(row, self._COL_VALUE)
            if name_item and val_item:
                v = _parse_value(val_item.text().strip())
                if v is not None:
                    result[name_item.text()] = v
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != self._COL_VALUE:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if name is None:
            return
        raw   = item.text().strip()
        value = _parse_value(raw)
        if value is None:
            gen   = next((g for g in self._generics if g.name == name), None)
            value = gen.default_value if gen else None
        self.value_changed.emit(name, value)

    def _adjust_height(self) -> None:
        row_h  = self._table.verticalHeader().defaultSectionSize()
        head_h = self._table.horizontalHeader().height()
        total  = head_h + row_h * max(self._table.rowCount(), 1) + 4
        self._table.setFixedHeight(min(total, 220))


class PortInfoTable(QWidget):
    """Read-only table listing a component's ports with resolved VHDL types."""

    _COL_NAME  = 0
    _COL_DIR   = 1
    _COL_CLASS = 2
    _COL_TYPE  = 3
    _HEADERS   = ["Port", "Dir", "Class", "Type"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_NAME,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_DIR,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_CLASS, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_TYPE,  QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(50)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_ports(self, ports: list[Port], generics: dict[str, Any] | None = None) -> None:
        """Populate from *ports*, resolving VHDL types with *generics*."""
        g = generics or {}
        self._table.setRowCount(0)

        for port in ports:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # Name
            self._table.setItem(row, self._COL_NAME, QTableWidgetItem(port.name))

            # Direction — colour-coded
            dir_item = QTableWidgetItem(port.direction.value)
            if port.direction == PortDirection.IN:
                dir_item.setForeground(QColor(112, 173, 71))   # green
            elif port.direction == PortDirection.OUT:
                dir_item.setForeground(QColor(237, 125, 49))   # orange
            else:
                dir_item.setForeground(QColor(180, 180, 180))
            self._table.setItem(row, self._COL_DIR, dir_item)

            # Signal class
            self._table.setItem(
                row, self._COL_CLASS, QTableWidgetItem(port.signal_class.value)
            )

            # VHDL type with notation prefix when available
            vhdl_type = port.signal_type.to_vhdl_type(g)
            notation  = port.signal_type.notation(g)
            type_text = f"{notation}  {vhdl_type}" if notation else vhdl_type
            type_item = QTableWidgetItem(type_text)
            type_item.setForeground(QColor(137, 220, 235))   # cyan
            self._table.setItem(row, self._COL_TYPE, type_item)

        self._adjust_height()

    # ── Private ───────────────────────────────────────────────────────────────

    def _adjust_height(self) -> None:
        row_h  = self._table.verticalHeader().defaultSectionSize()
        head_h = self._table.horizontalHeader().height()
        total  = head_h + row_h * max(self._table.rowCount(), 1) + 4
        self._table.setFixedHeight(min(total, 280))
