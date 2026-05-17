"""Primitive Editor main window.

Opens as an independent QMainWindow (non-modal).
Workflow:
  1. Select a primitive from the left list (or click New).
  2. Edit attributes in the Properties tab or drag handles in the Visual tab.
  3. Add behavior pseudo-code in the Behavior tab.
  4. Click Save (Ctrl+S) — the JSON file is written and the main window
     reloads its library via the `primitives_changed` signal.
  5. Click Delete to remove the currently selected primitive.

Port position sync
------------------
  Canvas drag  → port table X/Y spinboxes  (via `port_position_changed`)
  Table spinbox edit → canvas handle moves (via `position_edited`)
  Canvas resize → width/height spinboxes in Visual tab and visual.width/height
                  are written back on Save.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import ComponentDefinition, VisualConfig
from pipeline_designer.infrastructure.persistence.library_loader import LibraryLoader

from .behavior_editor import BehaviorEditor
from .generic_table import GenericTable
from .port_table import PortTable
from .primitive_canvas import PrimitiveCanvas


class PrimitiveEditorWindow(QMainWindow):
    """Standalone editor for creating, modifying, and deleting primitives.

    Emits `primitives_changed` when a save or delete occurs so the main
    application can reload its component library.
    """

    primitives_changed = Signal()

    def __init__(
        self,
        library_loader: LibraryLoader,
        library_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = library_loader
        self._library_path = library_path
        self._current: ComponentDefinition | None = None
        self._modified = False
        self._color = "#4a90d9"

        self.setWindowTitle("Primitive Editor")
        self.resize(1100, 720)

        self._setup_toolbar()
        self._setup_central()
        self._setup_status_bar()
        self._populate_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_toolbar(self) -> None:
        tb = QToolBar("Actions", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        new_act = QAction("New", self)
        new_act.setShortcut(QKeySequence("Ctrl+N"))
        new_act.setToolTip("Create a new primitive (Ctrl+N)")
        new_act.triggered.connect(self._on_new)
        tb.addAction(new_act)

        save_act = QAction("Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.setToolTip("Save current primitive (Ctrl+S)")
        save_act.triggered.connect(self._on_save)
        tb.addAction(save_act)

        tb.addSeparator()

        self._rename_act = QAction("Rename", self)
        self._rename_act.setToolTip("Rename the current primitive")
        self._rename_act.triggered.connect(self._on_rename)
        self._rename_act.setEnabled(False)
        tb.addAction(self._rename_act)

        self._clone_act = QAction("Clone", self)
        self._clone_act.setToolTip("Clone the current primitive with an auto-generated name")
        self._clone_act.triggered.connect(self._on_clone)
        self._clone_act.setEnabled(False)
        tb.addAction(self._clone_act)

        tb.addSeparator()

        self._delete_act = QAction("Delete", self)
        self._delete_act.setToolTip("Delete the selected primitive from the library")
        self._delete_act.triggered.connect(self._on_delete)
        self._delete_act.setEnabled(False)
        tb.addAction(self._delete_act)

    def _setup_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: primitive list
        self._list = QListWidget()
        self._list.setMinimumWidth(180)
        self._list.setMaximumWidth(260)
        self._list.currentItemChanged.connect(self._on_list_selection_changed)
        splitter.addWidget(self._list)

        # Right: tab widget
        self._tabs = QTabWidget()
        self._tabs.addTab(self._make_properties_tab(), "Properties")
        self._tabs.addTab(self._make_visual_tab(), "Visual")
        self._tabs.addTab(self._make_behavior_tab(), "Behavior")
        splitter.addWidget(self._tabs)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def _make_properties_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        # --- Basic form ---
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Adder")
        self._name_edit.textChanged.connect(self._mark_modified)
        form.addRow("Name:", self._name_edit)

        self._category_combo = QComboBox()
        self._category_combo.setEditable(True)
        self._category_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._category_combo.setToolTip(
            "Select an existing category or use Library → Manage Categories to create one."
        )
        self._category_combo.currentTextChanged.connect(self._mark_modified)
        self._refresh_category_combo()
        form.addRow("Category:", self._category_combo)

        self._desc_edit = QTextEdit()
        self._desc_edit.setMaximumHeight(60)
        self._desc_edit.setPlaceholderText("Short description (optional)")
        self._desc_edit.textChanged.connect(self._mark_modified)
        form.addRow("Description:", self._desc_edit)

        self._latency_spin = QSpinBox()
        self._latency_spin.setRange(0, 1000)
        self._latency_spin.setToolTip("Pipeline latency in clock cycles")
        self._latency_spin.valueChanged.connect(self._mark_modified)
        form.addRow("Latency (cycles):", self._latency_spin)

        layout.addLayout(form)

        # --- Color picker ---
        color_row_widget = QWidget()
        color_layout = QFormLayout(color_row_widget)
        self._color_preview = QLabel("  ")
        self._color_preview.setFixedSize(28, 20)
        self._color_preview.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid #555; border-radius: 3px;"
        )
        self._color_preview.mousePressEvent = lambda _: self._on_pick_color()
        self._color_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color_preview.setToolTip("Click to change color")
        color_layout.addRow("Color:", self._color_preview)
        layout.addWidget(color_row_widget)

        # --- Divider ---
        layout.addWidget(self._make_separator("Generics"))

        # --- Generic table ---
        self._generic_table = GenericTable()
        self._generic_table.data_changed.connect(self._mark_modified)
        self._generic_table.data_changed.connect(self._on_generics_changed)
        layout.addWidget(self._generic_table)

        # --- Divider ---
        layout.addWidget(self._make_separator("Ports"))

        # --- Port table ---
        self._port_table = PortTable()
        self._port_table.data_changed.connect(self._on_ports_changed)
        self._port_table.position_edited.connect(self._on_port_position_edited_in_table)
        layout.addWidget(self._port_table)

        layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _make_visual_tab(self) -> QWidget:
        self._canvas = PrimitiveCanvas()
        self._canvas.size_changed.connect(self._mark_modified)
        self._canvas.port_position_changed.connect(self._on_canvas_port_moved)
        return self._canvas

    def _make_behavior_tab(self) -> QWidget:
        self._behavior_editor = BehaviorEditor()
        self._behavior_editor.data_changed.connect(self._mark_modified)
        return self._behavior_editor

    def _setup_status_bar(self) -> None:
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready — select a primitive or create a new one.")

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _populate_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for name in self._loader.get_primitive_names():
            item = QListWidgetItem(name)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _select_by_name(self, name: str) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).text() == name:
                self._list.setCurrentRow(i)
                return

    # ------------------------------------------------------------------
    # Load / populate form
    # ------------------------------------------------------------------

    def _load_component(self, comp: ComponentDefinition) -> None:
        """Populate all UI panels from *comp* without emitting data_changed."""
        self._current = copy.deepcopy(comp)
        self._color = comp.visual.color

        # Properties tab
        self._name_edit.blockSignals(True)
        self._name_edit.setText(comp.name)
        self._name_edit.blockSignals(False)

        self._refresh_category_combo()
        self._category_combo.blockSignals(True)
        self._category_combo.setCurrentText(comp.category)
        self._category_combo.blockSignals(False)

        self._desc_edit.blockSignals(True)
        self._desc_edit.setPlainText(comp.description)
        self._desc_edit.blockSignals(False)

        self._latency_spin.blockSignals(True)
        self._latency_spin.setValue(comp.latency)
        self._latency_spin.blockSignals(False)

        self._color_preview.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid #555; border-radius: 3px;"
        )

        self._generic_table.set_generics(comp.generics)
        self._port_table.set_generic_defaults(self._generic_defaults_from(comp.generics))
        self._port_table.set_ports(comp.ports)

        # Visual tab
        self._canvas.set_component(comp)

        # Behavior tab
        self._behavior_editor.set_behavior(comp.behavior, comp.ports, comp.generics, comp.latency)

        self._modified = self._canvas.was_auto_extended()
        self._update_title()
        self._delete_act.setEnabled(True)
        self._rename_act.setEnabled(True)
        self._clone_act.setEnabled(True)

    # ------------------------------------------------------------------
    # Collect form → ComponentDefinition
    # ------------------------------------------------------------------

    def _collect_component(self) -> ComponentDefinition:
        """Read all UI panels and return a new ComponentDefinition."""
        ports = self._port_table.get_ports()

        # Merge canvas positions back into ports
        canvas_positions = self._canvas.get_port_positions()
        for port in ports:
            if port.name in canvas_positions:
                gx, gy = canvas_positions[port.name]
                object.__setattr__(port, "position", (gx, gy))

        w, h = self._canvas.get_size()
        return ComponentDefinition(
            name=self._name_edit.text().strip() or "Unnamed",
            category=self._category_combo.currentText().strip() or "arithmetic",
            description=self._desc_edit.toPlainText().strip(),
            latency=self._latency_spin.value(),
            visual=VisualConfig(width=w, height=h, color=self._color),
            generics=self._generic_table.get_generics(),
            ports=ports,
            behavior=self._behavior_editor.get_behavior(),
        )

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        new_comp = ComponentDefinition(
            name="NewPrimitive",
            category=self._category_combo.currentText().strip() or "arithmetic",
            description="",
            latency=0,
            visual=VisualConfig(width=4, height=6, color="#4a90d9"),
        )
        self._load_component(new_comp)
        self._modified = True
        self._update_title()
        self._status.showMessage("New primitive — fill in the fields and Save.")

    def _on_save(self) -> None:
        if self._current is None:
            return

        comp = self._collect_component()

        # Validate name
        if not comp.name or comp.name == "Unnamed":
            QMessageBox.warning(self, "Save", "Please enter a valid component name.")
            return

        # If the name changed, check for collisions
        if (
            self._current.name != comp.name
            and comp.name in {n for n in self._loader.get_primitive_names()}
        ):
            reply = QMessageBox.question(
                self,
                "Name conflict",
                f"A primitive named '{comp.name}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        old_path = self._loader.get_primitive_file_path(self._current.name)
        saved_path = self._loader.save_primitive(comp, old_path)

        self._current = copy.deepcopy(comp)
        self._modified = False
        self._update_title()
        self._populate_list()
        self._select_by_name(comp.name)
        self._status.showMessage(f"Saved → {saved_path}")
        self.primitives_changed.emit()

    def _on_delete(self) -> None:
        if self._current is None:
            return
        name = self._current.name
        reply = QMessageBox.question(
            self,
            "Delete primitive",
            f"Permanently delete '{name}'?\nThis also removes the JSON file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = self._loader.delete_primitive(name)
        if deleted:
            self._current = None
            self._modified = False
            self._populate_list()
            self._clear_form()
            self._status.showMessage(f"Deleted '{name}'.")
            self.primitives_changed.emit()
        else:
            QMessageBox.warning(self, "Delete", f"Could not delete '{name}'.")

    def _on_rename(self) -> None:
        if self._current is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Primitive", "New name:", text=self._current.name
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == self._current.name:
            return
        if new_name in set(self._loader.get_primitive_names()):
            QMessageBox.warning(self, "Rename", f"A primitive named '{new_name}' already exists.")
            return
        self._name_edit.blockSignals(True)
        self._name_edit.setText(new_name)
        self._name_edit.blockSignals(False)
        comp = self._collect_component()
        old_path = self._loader.get_primitive_file_path(self._current.name)
        saved_path = self._loader.save_primitive(comp, old_path)
        self._current = copy.deepcopy(comp)
        self._modified = False
        self._update_title()
        self._populate_list()
        self._select_by_name(new_name)
        self._status.showMessage(f"Renamed to '{new_name}' → {saved_path}")
        self.primitives_changed.emit()

    def _on_clone(self) -> None:
        if self._current is None:
            return
        existing = set(self._loader.get_primitive_names())
        base = self._current.name
        candidate = f"{base} Copy"
        counter = 2
        while candidate in existing:
            candidate = f"{base} Copy {counter}"
            counter += 1
        clone = self._current.model_copy(update={"name": candidate})
        saved_path = self._loader.save_primitive(clone)
        self._load_component(clone)
        self._populate_list()
        self._select_by_name(candidate)
        self._status.showMessage(f"Cloned as '{candidate}' → {saved_path}")
        self.primitives_changed.emit()

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Component color")
        if color.isValid():
            self._color = color.name()
            self._color_preview.setStyleSheet(
                f"background-color: {self._color}; border: 1px solid #555; border-radius: 3px;"
            )
            self._mark_modified()

    # ------------------------------------------------------------------
    # Sync: canvas ↔ port table
    # ------------------------------------------------------------------

    def _on_canvas_port_moved(self, name: str, x: int, y: int) -> None:
        """Canvas drag finished — update port table spinboxes."""
        self._port_table.update_port_position(name, x, y)
        self._mark_modified()

    def _on_port_position_edited_in_table(self, name: str, x: int, y: int) -> None:
        """Port table spinbox changed — move canvas handle."""
        self._canvas.update_port_position(name, x, y)

    def _on_generics_changed(self) -> None:
        """Generic table edited — refresh behavior editor generics and port notation."""
        generics = self._generic_table.get_generics()
        ports    = self._port_table.get_ports()
        self._behavior_editor.refresh_ports(ports, generics)
        self._port_table.set_generic_defaults(self._generic_defaults_from(generics))

    @staticmethod
    def _generic_defaults_from(generics) -> dict:
        """Build a ``{name: default_value}`` dict from a list of Generic objects."""
        return {
            g.name: g.default_value
            for g in generics
            if g.default_value is not None
        }

    def _on_ports_changed(self) -> None:
        """Port table edited — refresh behavior editor port list and canvas."""
        ports    = self._port_table.get_ports()
        generics = self._generic_table.get_generics()
        self._behavior_editor.refresh_ports(ports, generics)
        self._mark_modified()
        if self._current is not None:
            self._canvas.set_component(self._collect_component())

    # ------------------------------------------------------------------
    # List selection
    # ------------------------------------------------------------------

    def _on_list_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        if not self._confirm_discard():
            # Revert selection
            if _previous:
                self._list.setCurrentItem(_previous)
            return
        comp = self._loader.get_component(current.text())
        if comp:
            self._load_component(comp)
            self._status.showMessage(f"Loaded '{comp.name}'.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mark_modified(self) -> None:
        self._modified = True
        self._update_title()

    def _update_title(self) -> None:
        name = self._current.name if self._current else "—"
        star = " *" if self._modified else ""
        self.setWindowTitle(f"Primitive Editor — {name}{star}")

    def _refresh_category_combo(self) -> None:
        """Repopulate the category combobox from the loader's current primitive categories."""
        current_text = self._category_combo.currentText() if self._category_combo.count() > 0 else ""
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        categories = self._loader.get_primitive_categories()
        for cat in categories:
            self._category_combo.addItem(cat)
        if current_text:
            idx = self._category_combo.findText(current_text)
            if idx >= 0:
                self._category_combo.setCurrentIndex(idx)
            else:
                self._category_combo.setCurrentText(current_text)
        self._category_combo.blockSignals(False)

    def _clear_form(self) -> None:
        self._name_edit.clear()
        self._category_combo.setCurrentIndex(0)
        self._desc_edit.clear()
        self._latency_spin.setValue(0)
        self._generic_table.set_generics([])
        self._port_table.set_ports([])
        self._delete_act.setEnabled(False)
        self._rename_act.setEnabled(False)
        self._clone_act.setEnabled(False)
        self._update_title()

    def _confirm_discard(self) -> bool:
        """Return True if it is safe to switch away from the current primitive."""
        if not self._modified:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "You have unsaved changes. Discard and continue?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Discard

    @staticmethod
    def _make_separator(title: str) -> QLabel:
        lbl = QLabel(f" {title}")
        lbl.setStyleSheet(
            "background: #3a3a3a; color: #aaaaaa; font-weight: bold;"
            " padding: 2px 4px; border-radius: 2px; margin-top: 6px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # Close event
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._modified:
            reply = QMessageBox.question(
                self,
                "Unsaved changes",
                "Close without saving?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        super().closeEvent(event)
