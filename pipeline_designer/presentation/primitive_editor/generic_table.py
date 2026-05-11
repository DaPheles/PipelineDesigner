"""Generic-parameter table widget for the primitive editor."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Generic

_GENERIC_TYPES = [
    "integer",
    "natural",
    "positive",
    "boolean",
    "std_logic",
    "real",
    "string",
]


class GenericTable(QWidget):
    """Editable table of generic parameters for a primitive component.

    Columns: Name | Data Type | Default Value
    """

    data_changed = Signal()

    _COL_NAME = 0
    _COL_TYPE = 1
    _COL_DEFAULT = 2
    _HEADERS = ["Name", "Data Type", "Default Value"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Generic")
        add_btn.setFixedWidth(80)
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("− Remove")
        del_btn.setFixedWidth(80)
        del_btn.clicked.connect(self._on_remove)
        up_btn = QPushButton("↑")
        up_btn.setFixedWidth(30)
        up_btn.setToolTip("Move selected generic up")
        up_btn.clicked.connect(lambda: self._move_row(-1))
        down_btn = QPushButton("↓")
        down_btn.setFixedWidth(30)
        down_btn.setToolTip("Move selected generic down")
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
        hdr.setSectionResizeMode(self._COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_DEFAULT, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(80)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_generics(self, generics: list[Generic]) -> None:
        """Populate table from a list of Generic objects."""
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for g in generics:
            self._append_row(g)
        self._table.blockSignals(False)

    def get_generics(self) -> list[Generic]:
        """Collect Generic objects from the current table contents."""
        result = []
        for row in range(self._table.rowCount()):
            g = self._row_to_generic(row)
            if g is not None:
                result.append(g)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append_row(self, generic: Generic | None = None) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        self._table.setItem(
            row, self._COL_NAME,
            QTableWidgetItem(generic.name if generic else "GENERIC"),
        )

        type_combo = QComboBox()
        type_combo.setEditable(True)
        for t in _GENERIC_TYPES:
            type_combo.addItem(t)
        if generic:
            idx = type_combo.findText(generic.data_type)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            else:
                type_combo.setCurrentText(generic.data_type)
        type_combo.currentTextChanged.connect(lambda _: self.data_changed.emit())
        self._table.setCellWidget(row, self._COL_TYPE, type_combo)

        default_text = (
            str(generic.default_value)
            if generic and generic.default_value is not None
            else ""
        )
        self._table.setItem(row, self._COL_DEFAULT, QTableWidgetItem(default_text))

    def _move_row(self, direction: int) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return
        row = rows[0]
        target = row + direction
        if target < 0 or target >= self._table.rowCount():
            return
        generics = self.get_generics()
        generics[row], generics[target] = generics[target], generics[row]
        self._table.blockSignals(True)
        self.set_generics(generics)
        self._table.blockSignals(False)
        self._table.selectRow(target)
        self.data_changed.emit()

    def _on_add(self) -> None:
        self._append_row()
        self.data_changed.emit()

    def _on_remove(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self._table.removeRow(row)
        self.data_changed.emit()

    def _on_item_changed(self) -> None:
        self.data_changed.emit()

    def _row_to_generic(self, row: int) -> Generic | None:
        name_item = self._table.item(row, self._COL_NAME)
        if not name_item or not name_item.text().strip():
            return None

        type_combo = self._table.cellWidget(row, self._COL_TYPE)
        default_item = self._table.item(row, self._COL_DEFAULT)

        data_type = (
            type_combo.currentText()
            if isinstance(type_combo, QComboBox)
            else "integer"
        )
        raw_default = default_item.text().strip() if default_item else ""

        # Try to parse as int, then float, then keep as string
        parsed: int | float | str | None = None
        if raw_default:
            try:
                parsed = int(raw_default)
            except ValueError:
                try:
                    parsed = float(raw_default)
                except ValueError:
                    parsed = raw_default

        return Generic(
            name=name_item.text().strip(),
            data_type=data_type,
            default_value=parsed,
        )
