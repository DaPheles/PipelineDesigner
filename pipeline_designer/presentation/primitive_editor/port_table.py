"""Port table widget for the primitive editor."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import Port, PortDirection, PortSignalClass
from pipeline_designer.domain.models.behavior import SignalKind, SignalType
from pipeline_designer.domain.models.signal_constraints import (
    ALLOWED_KINDS,
    coerce_signal_type,
    default_signal_type,
)

# All concrete kind choices (for DATA ports; others are locked automatically)
_ALL_KIND_OPTIONS: list[str] = [
    SignalKind.SFIXED.value,
    SignalKind.UFIXED.value,
    SignalKind.STD_LOGIC_VECTOR.value,
    SignalKind.STD_ULOGIC_VECTOR.value,
    SignalKind.STD_LOGIC.value,
    SignalKind.STD_ULOGIC.value,
    SignalKind.INTEGER.value,
    SignalKind.BOOLEAN.value,
]

# Kind options shown for each signal class (ordered; first = default)
_CLASS_KIND_OPTIONS: dict[PortSignalClass, list[str]] = {
    PortSignalClass.CLOCK:   [SignalKind.STD_LOGIC.value],
    PortSignalClass.RESET:   [SignalKind.STD_LOGIC.value],
    PortSignalClass.CONTROL: [SignalKind.STD_LOGIC.value, SignalKind.STD_LOGIC_VECTOR.value],
    PortSignalClass.DATA:    [SignalKind.SFIXED.value, SignalKind.UFIXED.value],
}

# Kinds that carry no bit-width (width/lsb fields disabled)
_SCALAR_KIND_VALUES: frozenset[str] = frozenset({
    SignalKind.STD_LOGIC.value,
    SignalKind.STD_ULOGIC.value,
    SignalKind.INTEGER.value,
    SignalKind.BOOLEAN.value,
})

_SIGNAL_CLASS_OPTIONS: list[str] = [
    PortSignalClass.DATA.value,
    PortSignalClass.CONTROL.value,
    PortSignalClass.RESET.value,
    PortSignalClass.CLOCK.value,
]


class PortTable(QWidget):
    """Editable table of port definitions for a primitive component.

    Columns:
        Name | Direction | Class | Kind | Width | LSB | Notation | X | Y

    Signal-class constraints are enforced automatically:
      - CLOCK / RESET  → kind locked to std_logic; width/LSB disabled.
      - CONTROL        → kind locked to std_logic or std_logic_vector; user
                         may switch between them but nothing else.
      - DATA           → kind limited to sfixed / ufixed; width/LSB enabled.

    When the signal class changes the kind is auto-corrected to the first
    allowed option if the current kind would be invalid.
    """

    data_changed = Signal()
    # (port_name, x, y) — emitted when spinbox position changes
    position_edited = Signal(str, int, int)

    _COL_NAME         = 0
    _COL_DIR          = 1
    _COL_SIGNAL_CLASS = 2
    _COL_KIND         = 3
    _COL_WIDTH        = 4
    _COL_LSB          = 5
    _COL_NOTATION     = 6
    _COL_X            = 7
    _COL_Y            = 8
    _HEADERS = [
        "Name", "Direction", "Class", "Kind", "Width", "LSB",
        "Notation", "X", "Y",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False
        self._generic_defaults: dict = {}
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
        hdr.setSectionResizeMode(self._COL_NAME,     QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self._COL_KIND,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_WIDTH,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_LSB,      QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_NOTATION, QHeaderView.ResizeMode.ResizeToContents)
        for col in (self._COL_DIR, self._COL_X, self._COL_Y, self._COL_SIGNAL_CLASS):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(120)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Public interface

    def set_ports(self, ports: list[Port]) -> None:
        self._syncing = True
        self._table.setRowCount(0)
        for port in ports:
            self._append_row(port)
        self._syncing = False

    def set_generic_defaults(self, defaults: dict) -> None:
        """Update the generic default values used to resolve symbolic notation.

        Call this whenever the primitive's generic table changes so that
        port notation (e.g. ``U8.8``) is recomputed using the new defaults.
        """
        self._generic_defaults = defaults
        for row in range(self._table.rowCount()):
            self._update_notation(row, self._get_kind(row))

    def get_ports(self) -> list[Port]:
        return [p for p in (self._row_to_port(r) for r in range(self._table.rowCount())) if p]

    def update_port_position(self, name: str, x: int, y: int) -> None:
        self._syncing = True
        for row in range(self._table.rowCount()):
            if self._get_name(row) == name:
                self._get_spin(row, self._COL_X).setValue(x)
                self._get_spin(row, self._COL_Y).setValue(y)
                break
        self._syncing = False

    # ------------------------------------------------------------------
    # Private helpers

    def _append_row(self, port: Port | None = None) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Name
        name_item = QTableWidgetItem(port.name if port else "port")
        name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, self._COL_NAME, name_item)

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

        # Signal class
        sc_combo = QComboBox()
        for opt in _SIGNAL_CLASS_OPTIONS:
            sc_combo.addItem(opt)
        sc_val = port.signal_class.value if port else PortSignalClass.DATA.value
        idx = sc_combo.findText(sc_val)
        if idx >= 0:
            sc_combo.setCurrentIndex(idx)
        # Connect AFTER setting initial value to avoid premature coercion
        sc_combo.currentIndexChanged.connect(lambda _, r=row: self._on_signal_class_changed(r))
        self._table.setCellWidget(row, self._COL_SIGNAL_CLASS, sc_combo)

        # Kind — managed by _apply_class_constraints; not directly editable by user
        # for CLOCK/RESET/CONTROL.  Data ports get a two-item sfixed/ufixed combo.
        kind_combo = QComboBox()
        kind_val = port.signal_type.kind if port else SignalKind.UFIXED.value
        self._table.setCellWidget(row, self._COL_KIND, kind_combo)
        # Populate combo and enforce constraints for the initial class
        sc = port.signal_class if port else PortSignalClass.DATA
        self._apply_class_constraints(row, sc, kind_val)

        # Width
        width_edit = QLineEdit()
        width_edit.setPlaceholderText("e.g. 8 or WIDTH")
        width_edit.setFixedWidth(80)
        width_edit.setText(port.signal_type.width if port else "1")
        width_edit.textChanged.connect(lambda _, r=row: self._on_range_changed(r))
        self._table.setCellWidget(row, self._COL_WIDTH, width_edit)

        # LSB
        lsb_edit = QLineEdit()
        lsb_edit.setPlaceholderText("0 or -FRAC")
        lsb_edit.setFixedWidth(70)
        lsb_edit.setText(port.signal_type.lsb if port else "0")
        lsb_edit.textChanged.connect(lambda _, r=row: self._on_range_changed(r))
        self._table.setCellWidget(row, self._COL_LSB, lsb_edit)

        # Notation (read-only label)
        notation_lbl = QLabel()
        notation_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notation_lbl.setStyleSheet("color: #aaaaaa; font-size: 9pt;")
        self._table.setCellWidget(row, self._COL_NOTATION, notation_lbl)

        # X / Y position
        for col, val in (
            (self._COL_X, port.position[0] if port and port.position else 0),
            (self._COL_Y, port.position[1] if port and port.position else 0),
        ):
            spin = QSpinBox()
            spin.setRange(0, 999)
            spin.setValue(val)
            spin.valueChanged.connect(lambda _, r=row: self._on_position_spin_changed(r))
            self._table.setCellWidget(row, col, spin)

        self._update_range_state(row, self._get_kind(row))
        return row

    def _apply_class_constraints(
        self,
        row: int,
        signal_class: PortSignalClass,
        preferred_kind: str | None = None,
    ) -> None:
        """Rebuild the kind combo for *row* to match *signal_class* constraints.

        If *preferred_kind* is in the allowed set it is selected; otherwise the
        first allowed option is used.  The combo is disabled for classes where
        the kind is unambiguous (CLOCK, RESET).
        """
        kind_combo = self._table.cellWidget(row, self._COL_KIND)
        if not isinstance(kind_combo, QComboBox):
            return

        # Temporarily disconnect to avoid recursive signals
        kind_combo.blockSignals(True)

        options = _CLASS_KIND_OPTIONS.get(signal_class, _ALL_KIND_OPTIONS)
        kind_combo.clear()
        for opt in options:
            kind_combo.addItem(opt)

        # Select preferred if valid, else default to first
        selected = preferred_kind if preferred_kind in options else options[0]
        idx = kind_combo.findText(selected)
        if idx >= 0:
            kind_combo.setCurrentIndex(idx)

        # Lock the combo when there is only one valid choice or the class is
        # unambiguous (clock / reset)
        locked = signal_class in (PortSignalClass.CLOCK, PortSignalClass.RESET)
        kind_combo.setEnabled(not locked)

        kind_combo.blockSignals(False)

        # Re-wire the changed signal (was blocked during rebuild)
        try:
            kind_combo.currentTextChanged.disconnect()
        except RuntimeError:
            pass
        kind_combo.currentTextChanged.connect(
            lambda text, r=row: self._on_kind_changed(r, text)
        )

        self._update_range_state(row, selected)

    def _on_signal_class_changed(self, row: int) -> None:
        """Enforce kind constraints when the signal class changes."""
        sc = self._get_signal_class(row)
        current_kind = self._get_kind(row)
        # Coerce: keep current kind only if it is valid for the new class
        allowed = ALLOWED_KINDS[sc]
        preferred = current_kind if current_kind in allowed else None
        self._apply_class_constraints(row, sc, preferred)
        self._update_notation(row, self._get_kind(row))
        self._emit_changed()

    def _on_kind_changed(self, row: int, text: str) -> None:
        self._update_range_state(row, text)
        self._emit_changed()

    def _on_range_changed(self, row: int) -> None:
        kind = self._get_kind(row)
        self._update_notation(row, kind)
        self._emit_changed()

    def _update_range_state(self, row: int, kind_text: str) -> None:
        is_scalar = kind_text in _SCALAR_KIND_VALUES
        for col in (self._COL_WIDTH, self._COL_LSB):
            w = self._table.cellWidget(row, col)
            if isinstance(w, QLineEdit):
                w.setEnabled(not is_scalar)
        self._update_notation(row, kind_text)

    def _update_notation(self, row: int, kind_text: str) -> None:
        lbl = self._table.cellWidget(row, self._COL_NOTATION)
        if not isinstance(lbl, QLabel):
            return
        width_w = self._table.cellWidget(row, self._COL_WIDTH)
        lsb_w   = self._table.cellWidget(row, self._COL_LSB)
        if not isinstance(width_w, QLineEdit) or not isinstance(lsb_w, QLineEdit):
            lbl.setText("")
            return
        st = SignalType(kind=kind_text, width=width_w.text() or "1", lsb=lsb_w.text() or "0")
        notation = st.notation(self._generic_defaults or None)
        lbl.setText(notation or "")

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

    def _get_name(self, row: int) -> str:
        item = self._table.item(row, self._COL_NAME)
        return item.text().strip() if item else ""

    def _get_kind(self, row: int) -> str:
        w = self._table.cellWidget(row, self._COL_KIND)
        return w.currentText() if isinstance(w, QComboBox) else SignalKind.UFIXED.value

    def _get_spin(self, row: int, col: int) -> QSpinBox:
        return self._table.cellWidget(row, col)  # type: ignore[return-value]

    def _get_signal_class(self, row: int) -> PortSignalClass:
        w = self._table.cellWidget(row, self._COL_SIGNAL_CLASS)
        if isinstance(w, QComboBox):
            try:
                return PortSignalClass(w.currentText())
            except ValueError:
                pass
        return PortSignalClass.DATA

    def _row_to_port(self, row: int) -> Port | None:
        name = self._get_name(row)
        if not name:
            return None

        dir_combo   = self._table.cellWidget(row, self._COL_DIR)
        width_edit  = self._table.cellWidget(row, self._COL_WIDTH)
        lsb_edit    = self._table.cellWidget(row, self._COL_LSB)

        direction = (
            PortDirection(dir_combo.currentText())
            if isinstance(dir_combo, QComboBox)
            else PortDirection.IN
        )
        signal_class = self._get_signal_class(row)
        kind  = self._get_kind(row)
        width = width_edit.text().strip() if isinstance(width_edit, QLineEdit) else "1"
        lsb   = lsb_edit.text().strip()  if isinstance(lsb_edit,  QLineEdit) else "0"

        # Always validate kind against class before building the model
        raw_st = SignalType(kind=kind, width=width or "1", lsb=lsb or "0")
        signal_type = coerce_signal_type(raw_st, signal_class)

        x = self._get_spin(row, self._COL_X).value()
        y = self._get_spin(row, self._COL_Y).value()

        return Port(
            name=name,
            direction=direction,
            signal_type=signal_type,
            position=(x, y),
            signal_class=signal_class,
        )
