"""Table widgets for the design canvas property panel.

Three widgets live here:

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

``InterfacePortDisplayTable``
    Editable table for a design's interface ports.  Name, Signal Class, Kind,
    Width, and LSB are editable in-place.  Direction is always read-only.
    Notation is computed and read-only.  Up/Down buttons allow reordering.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models.behavior import SignalKind, SignalType
from pipeline_designer.domain.models.component import Generic, Port, PortDirection, PortSignalClass
from pipeline_designer.domain.models.instance import InterfaceDirection, InterfacePort


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


class InterfacePortDisplayTable(QWidget):
    """Editable table for a design's interface ports.

    Columns: Name | Dir | Class | Kind | Width | LSB | Notation
    - Name, Width, LSB: editable text cells
    - Class, Kind: inline QComboBox widgets
    - Dir: always read-only (direction is set at port-creation time)
    - Notation: computed and read-only

    Changes are applied directly to the InterfacePort objects and announced
    via ``port_changed``.  Up/Down buttons reorder the port list and emit
    ``port_reordered`` with the new UUID order.
    """

    port_changed   = Signal(object, str, object)  # (port_id, field, new_value)
    port_reordered = Signal(list)                 # list[UUID] in new order

    _COL_NAME     = 0
    _COL_DIR      = 1
    _COL_CLASS    = 2
    _COL_KIND     = 3
    _COL_WIDTH    = 4
    _COL_LSB      = 5
    _COL_NOTATION = 6
    _HEADERS = ["Name", "Dir", "Class", "Kind", "Width", "LSB", "Notation"]

    _DATA_KINDS = [SignalKind.UFIXED.value, SignalKind.SFIXED.value]
    _STD_KIND   = SignalKind.STD_LOGIC.value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._iports:    list[InterfacePort] = []
        self._generics:  dict[str, Any]      = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._table = QTableWidget(0, len(self._HEADERS))
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_NAME,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_DIR,      QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_CLASS,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_KIND,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_WIDTH,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_LSB,      QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_NOTATION, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(50)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.addStretch()
        self._up_btn = QPushButton("▲")
        self._up_btn.setFixedWidth(28)
        self._up_btn.setToolTip("Move selected port up")
        self._up_btn.clicked.connect(self._move_up)
        self._down_btn = QPushButton("▼")
        self._down_btn.setFixedWidth(28)
        self._down_btn.setToolTip("Move selected port down")
        self._down_btn.clicked.connect(self._move_down)
        btn_row.addWidget(self._up_btn)
        btn_row.addWidget(self._down_btn)
        layout.addLayout(btn_row)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_interface_ports(self, iports: list[InterfacePort]) -> None:
        """Populate from *iports* (design.interface_ports)."""
        self._iports = list(iports)
        self._rebuild_table()

    def set_generics(self, generics: dict[str, Any]) -> None:
        """Update the design-level generics used to resolve symbolic Width/LSB.

        Call this after ``set_interface_ports`` and whenever the design's
        entity generics change so that notation values are recomputed.
        """
        self._generics = generics
        for row in range(self._table.rowCount()):
            if row < len(self._iports):
                st = self._iports[row].effective_signal_type()
                self._update_notation_cell(row, st)

    # ── Table construction ────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for row, iport in enumerate(self._iports):
            self._table.insertRow(row)
            self._populate_row(row, iport)
        self._table.blockSignals(False)
        self._adjust_height()

    def _populate_row(self, row: int, iport: InterfacePort) -> None:
        is_input = iport.direction == InterfaceDirection.INPUT
        is_data  = iport.signal_class == PortSignalClass.DATA
        st       = iport.effective_signal_type()

        # ── Name — editable ───────────────────────────────────────────────
        name_item = QTableWidgetItem(iport.name)
        name_item.setData(Qt.ItemDataRole.UserRole, ("name", iport.id))
        self._table.setItem(row, self._COL_NAME, name_item)

        # ── Dir — read-only, colour-coded ─────────────────────────────────
        dir_item = QTableWidgetItem("in" if is_input else "out")
        dir_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        dir_item.setForeground(
            QColor(112, 173, 71) if is_input else QColor(237, 125, 49)
        )
        self._table.setItem(row, self._COL_DIR, dir_item)

        # ── Class — QComboBox ─────────────────────────────────────────────
        class_combo = QComboBox()
        for sc in PortSignalClass:
            class_combo.addItem(sc.value)
        class_combo.setCurrentText(iport.signal_class.value)
        class_combo.currentTextChanged.connect(
            lambda val, r=row: self._on_class_changed(r, val)
        )
        self._table.setCellWidget(row, self._COL_CLASS, class_combo)

        # ── Kind — QComboBox (meaningful only for DATA) ───────────────────
        kind_combo = QComboBox()
        if is_data:
            kind_combo.addItems(self._DATA_KINDS)
            current_kind = st.kind if st.kind in self._DATA_KINDS else self._DATA_KINDS[0]
            kind_combo.setCurrentText(current_kind)
        else:
            kind_combo.addItem(self._STD_KIND)
            kind_combo.setCurrentText(self._STD_KIND)
            kind_combo.setEnabled(False)
        kind_combo.currentTextChanged.connect(
            lambda val, r=row: self._on_kind_changed(r, val)
        )
        self._table.setCellWidget(row, self._COL_KIND, kind_combo)

        # ── Width — editable for DATA ports ──────────────────────────────
        width_item = QTableWidgetItem(st.width if is_data else "—")
        if is_data:
            width_item.setData(Qt.ItemDataRole.UserRole, ("width", iport.id))
        else:
            width_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            width_item.setForeground(QColor(100, 100, 100))
        self._table.setItem(row, self._COL_WIDTH, width_item)

        # ── LSB — editable for DATA ports ────────────────────────────────
        lsb_item = QTableWidgetItem(st.lsb if is_data else "—")
        if is_data:
            lsb_item.setData(Qt.ItemDataRole.UserRole, ("lsb", iport.id))
        else:
            lsb_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            lsb_item.setForeground(QColor(100, 100, 100))
        self._table.setItem(row, self._COL_LSB, lsb_item)

        # ── Notation — read-only, computed ────────────────────────────────
        g = self._generics or None
        notation = st.notation(g) or (st.to_vhdl_type(g) if st.has_range(g) else "")
        notation_item = QTableWidgetItem(notation)
        notation_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        notation_item.setForeground(QColor(137, 220, 235))
        self._table.setItem(row, self._COL_NOTATION, notation_item)

    # ── Cell-change handlers ──────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        # PySide6 round-trips Python tuples as lists through QVariant
        if not isinstance(data, (tuple, list)) or len(data) != 2:
            return
        field, port_id = data
        iport = next((p for p in self._iports if p.id == port_id), None)
        if iport is None:
            return

        if field == "name":
            new_name = item.text().strip()
            if new_name and new_name != iport.name:
                iport.name = new_name
                self.port_changed.emit(port_id, "name", new_name)

        elif field in ("width", "lsb"):
            st = iport.effective_signal_type()
            if field == "width":
                new_st = SignalType(kind=st.kind, width=item.text() or "1", lsb=st.lsb)
            else:
                new_st = SignalType(kind=st.kind, width=st.width, lsb=item.text() or "0")
            iport.signal_type = new_st
            self.port_changed.emit(port_id, "signal_type", new_st)
            self._update_notation_cell(item.row(), new_st)

    def _on_class_changed(self, row: int, value: str) -> None:
        if row >= len(self._iports):
            return
        iport = self._iports[row]
        try:
            new_sc = PortSignalClass(value)
        except ValueError:
            return
        if new_sc == iport.signal_class:
            return

        iport.signal_class = new_sc
        self.port_changed.emit(iport.id, "signal_class", new_sc)

        if new_sc == PortSignalClass.DATA:
            canonical = SignalKind.UFIXED.value
        else:
            canonical = self._STD_KIND
            iport.signal_type = None

        if iport.data_type != canonical:
            iport.data_type = canonical
            self.port_changed.emit(iport.id, "data_type", canonical)

        self._table.blockSignals(True)
        self._populate_row(row, iport)
        self._table.blockSignals(False)

    def _on_kind_changed(self, row: int, value: str) -> None:
        if row >= len(self._iports):
            return
        iport = self._iports[row]
        st = iport.effective_signal_type()
        if st.kind == value:
            return
        new_st = SignalType(kind=value, width=st.width, lsb=st.lsb)
        iport.signal_type = new_st
        iport.data_type   = value
        self.port_changed.emit(iport.id, "signal_type", new_st)
        self.port_changed.emit(iport.id, "data_type",   value)
        self._update_notation_cell(row, new_st)

    def _update_notation_cell(self, row: int, st: SignalType) -> None:
        g = self._generics or None
        notation = st.notation(g) or (st.to_vhdl_type(g) if st.has_range(g) else "")
        item = self._table.item(row, self._COL_NOTATION)
        if item is not None:
            self._table.blockSignals(True)
            item.setText(notation)
            self._table.blockSignals(False)

    # ── Reorder buttons ───────────────────────────────────────────────────────

    def _move_up(self) -> None:
        row = self._table.currentRow()
        if row <= 0 or not self._iports:
            return
        self._iports[row - 1], self._iports[row] = self._iports[row], self._iports[row - 1]
        self._rebuild_table()
        self._table.setCurrentCell(row - 1, self._table.currentColumn())
        self.port_reordered.emit([p.id for p in self._iports])

    def _move_down(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._iports) - 1:
            return
        self._iports[row], self._iports[row + 1] = self._iports[row + 1], self._iports[row]
        self._rebuild_table()
        self._table.setCurrentCell(row + 1, self._table.currentColumn())
        self.port_reordered.emit([p.id for p in self._iports])

    # ── Private ───────────────────────────────────────────────────────────────

    def _adjust_height(self) -> None:
        row_h  = self._table.verticalHeader().defaultSectionSize()
        head_h = self._table.horizontalHeader().height()
        total  = head_h + row_h * max(self._table.rowCount(), 1) + 4
        self._table.setFixedHeight(min(total, 260))
