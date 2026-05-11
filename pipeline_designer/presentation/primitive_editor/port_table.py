"""Port table widget for the primitive editor."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Port, PortDirection

_COMMON_TYPES = [
    "std_logic",
    "std_logic_vector",
    "unsigned",
    "signed",
    "sfixed",
    "ufixed",
    "integer",
    "natural",
    "positive",
    "boolean",
]

_VECTOR_TYPES = frozenset(
    {"std_logic_vector", "unsigned", "signed", "sfixed", "ufixed"}
)


class PortTable(QWidget):
    """Editable table of port definitions for a primitive component.

    Columns:
        Name | Direction | Data Type | Range | X | Y | Clock | Reset

    Range is enabled only for vector-like data types.
    X and Y spinboxes are the secondary position editors (primary = canvas drag).
    """

    data_changed = Signal()
    # Emitted when spinboxes change, so the canvas can sync: (port_name, x, y)
    position_edited = Signal(str, int, int)

    _COL_NAME = 0
    _COL_DIR = 1
    _COL_TYPE = 2
    _COL_RANGE = 3
    _COL_X = 4
    _COL_Y = 5
    _COL_CLK = 6
    _COL_RST = 7
    _HEADERS = ["Name", "Direction", "Data Type", "Range (MSB:LSB)", "X", "Y", "Clk", "Rst"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Port")
        add_btn.setFixedWidth(70)
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("− Remove")
        del_btn.setFixedWidth(80)
        del_btn.clicked.connect(self._on_remove)
        up_btn = QPushButton("↑")
        up_btn.setFixedWidth(30)
        up_btn.setToolTip("Move selected port up")
        up_btn.clicked.connect(lambda: self._move_row(-1))
        down_btn = QPushButton("↓")
        down_btn.setFixedWidth(30)
        down_btn.setToolTip("Move selected port down")
        down_btn.clicked.connect(lambda: self._move_row(1))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._table = QTableWidget(0, len(self._HEADERS))
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self._COL_RANGE, QHeaderView.ResizeMode.Stretch)
        for col in (self._COL_DIR, self._COL_TYPE, self._COL_X, self._COL_Y,
                    self._COL_CLK, self._COL_RST):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(120)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_ports(self, ports: list[Port]) -> None:
        """Populate table from a list of Port objects."""
        self._syncing = True
        self._table.setRowCount(0)
        for port in ports:
            self._append_row(port)
        self._syncing = False

    def get_ports(self) -> list[Port]:
        """Collect Port objects from the current table contents."""
        ports = []
        for row in range(self._table.rowCount()):
            port = self._row_to_port(row)
            if port is not None:
                ports.append(port)
        return ports

    def update_port_position(self, name: str, x: int, y: int) -> None:
        """Update the X/Y spinboxes for *name* (called when canvas port is dragged)."""
        self._syncing = True
        for row in range(self._table.rowCount()):
            if self._get_name(row) == name:
                self._get_spin(row, self._COL_X).setValue(x)
                self._get_spin(row, self._COL_Y).setValue(y)
                break
        self._syncing = False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append_row(self, port: Port | None = None) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Name
        self._table.setItem(
            row, self._COL_NAME, QTableWidgetItem(port.name if port else "port")
        )
        name_item = self._table.item(row, self._COL_NAME)
        if name_item:
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)

        # Direction
        dir_combo = QComboBox()
        for d in PortDirection:
            dir_combo.addItem(d.value)
        if port:
            idx = dir_combo.findText(port.direction.value)
            if idx >= 0:
                dir_combo.setCurrentIndex(idx)
        dir_combo.currentIndexChanged.connect(self._emit_changed)
        self._table.setCellWidget(row, self._COL_DIR, dir_combo)

        # Data type
        type_combo = QComboBox()
        type_combo.setEditable(True)
        for t in _COMMON_TYPES:
            type_combo.addItem(t)
        if port:
            idx = type_combo.findText(port.data_type)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            else:
                type_combo.setCurrentText(port.data_type)
        type_combo.currentTextChanged.connect(lambda text, r=row: self._on_type_changed(r, text))
        self._table.setCellWidget(row, self._COL_TYPE, type_combo)

        # Range
        range_edit = QLineEdit()
        range_edit.setPlaceholderText("7:0 or WIDTH-1:0")
        if port and port.vector_range:
            range_edit.setText(port.vector_range)
        is_vector = port.data_type in _VECTOR_TYPES if port else False
        range_edit.setEnabled(is_vector)
        range_edit.textChanged.connect(self._emit_changed)
        self._table.setCellWidget(row, self._COL_RANGE, range_edit)

        # X / Y position spinboxes
        for col, val in (
            (self._COL_X, port.position[0] if port and port.position else 0),
            (self._COL_Y, port.position[1] if port and port.position else 0),
        ):
            spin = QSpinBox()
            spin.setRange(0, 999)
            spin.setValue(val)
            spin.valueChanged.connect(lambda _, r=row: self._on_position_spin_changed(r))
            self._table.setCellWidget(row, col, spin)

        # Clock / Reset checkboxes
        for col, flag in (
            (self._COL_CLK, port.is_clock if port else False),
            (self._COL_RST, port.is_reset if port else False),
        ):
            self._table.setCellWidget(row, col, self._make_checkbox(flag))

        return row

    def _make_checkbox(self, checked: bool) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox()
        cb.setChecked(checked)
        cb.stateChanged.connect(self._emit_changed)
        layout.addWidget(cb)
        layout.setAlignment(cb, Qt.AlignmentFlag.AlignCenter)
        return container

    def _on_type_changed(self, row: int, text: str) -> None:
        range_edit = self._table.cellWidget(row, self._COL_RANGE)
        if isinstance(range_edit, QLineEdit):
            range_edit.setEnabled(text in _VECTOR_TYPES)
        self._emit_changed()

    def _on_position_spin_changed(self, row: int) -> None:
        if self._syncing:
            return
        name = self._get_name(row)
        x = self._get_spin(row, self._COL_X).value()
        y = self._get_spin(row, self._COL_Y).value()
        self.position_edited.emit(name, x, y)
        self._emit_changed()

    def _move_row(self, direction: int) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return
        row = rows[0]
        target = row + direction
        if target < 0 or target >= self._table.rowCount():
            return
        ports = self.get_ports()
        ports[row], ports[target] = ports[target], ports[row]
        self.set_ports(ports)
        self._table.selectRow(target)
        self._emit_changed()

    def _on_add(self) -> None:
        self._append_row()
        self._emit_changed()

    def _on_remove(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self._table.removeRow(row)
        self._emit_changed()

    def _emit_changed(self) -> None:
        if not self._syncing:
            self.data_changed.emit()

    # ------------------------------------------------------------------
    # Row accessors
    # ------------------------------------------------------------------

    def _get_name(self, row: int) -> str:
        item = self._table.item(row, self._COL_NAME)
        return item.text().strip() if item else ""

    def _get_spin(self, row: int, col: int) -> QSpinBox:
        return self._table.cellWidget(row, col)  # type: ignore[return-value]

    def _get_checkbox(self, row: int, col: int) -> bool:
        container = self._table.cellWidget(row, col)
        if container:
            cb = container.findChild(QCheckBox)
            return cb.isChecked() if cb else False
        return False

    def _row_to_port(self, row: int) -> Port | None:
        name = self._get_name(row)
        if not name:
            return None

        dir_combo = self._table.cellWidget(row, self._COL_DIR)
        type_combo = self._table.cellWidget(row, self._COL_TYPE)
        range_edit = self._table.cellWidget(row, self._COL_RANGE)

        direction = (
            PortDirection(dir_combo.currentText())
            if isinstance(dir_combo, QComboBox)
            else PortDirection.IN
        )
        data_type = (
            type_combo.currentText()
            if isinstance(type_combo, QComboBox)
            else "std_logic"
        )
        vector_range = (
            range_edit.text().strip()
            if isinstance(range_edit, QLineEdit) and range_edit.isEnabled()
            else None
        ) or None

        x = self._get_spin(row, self._COL_X).value()
        y = self._get_spin(row, self._COL_Y).value()

        return Port(
            name=name,
            direction=direction,
            data_type=data_type,
            vector_range=vector_range,
            position=(x, y),
            is_clock=self._get_checkbox(row, self._COL_CLK),
            is_reset=self._get_checkbox(row, self._COL_RST),
        )
