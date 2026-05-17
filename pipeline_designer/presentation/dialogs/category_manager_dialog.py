"""Category manager dialog for administering library category directories."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.infrastructure.persistence.category_manager import (
    CategoryError,
    CategoryManager,
)


class _CategoryTab(QWidget):
    """Single tab managing categories for one library type (primitives or components)."""

    changed = Signal()

    def __init__(self, manager: CategoryManager, lib_type: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._manager = manager
        self._lib_type = lib_type  # "primitives" | "components"
        self._setup_ui()
        self._refresh()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter()

        # Left: category list + buttons
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Categories:"))
        self._cat_list = QListWidget()
        self._cat_list.currentItemChanged.connect(self._on_category_selected)
        left_layout.addWidget(self._cat_list)

        btn_row = QHBoxLayout()
        self._btn_new = QPushButton("New…")
        self._btn_rename = QPushButton("Rename…")
        self._btn_delete = QPushButton("Delete…")
        self._btn_rename.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._btn_new.clicked.connect(self._on_new)
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_new)
        btn_row.addWidget(self._btn_rename)
        btn_row.addWidget(self._btn_delete)
        left_layout.addLayout(btn_row)
        splitter.addWidget(left)

        # Right: items in selected category
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._items_label = QLabel("Items:")
        right_layout.addWidget(self._items_label)
        self._item_list = QListWidget()
        self._item_list.setEnabled(False)
        right_layout.addWidget(self._item_list)
        splitter.addWidget(right)

        splitter.setSizes([200, 200])
        layout.addWidget(splitter)

    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        current = self._cat_list.currentItem()
        current_name = current.text() if current else None

        self._cat_list.clear()
        for cat in self._manager.list_categories(self._lib_type):
            item = QListWidgetItem(cat)
            is_primary = self._manager.is_primary_category(self._lib_type, cat)
            if not is_primary:
                item.setToolTip("Read-only (from a user library root)")
                from PySide6.QtGui import QColor
                item.setForeground(QColor("#888888"))
            self._cat_list.addItem(item)

        # Restore selection
        if current_name:
            matches = self._cat_list.findItems(current_name, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.MatchFlag.MatchExactly)
            if matches:
                self._cat_list.setCurrentItem(matches[0])

    def _on_category_selected(self, item: QListWidgetItem | None) -> None:
        has_selection = item is not None
        is_primary = has_selection and self._manager.is_primary_category(
            self._lib_type, item.text()
        )
        self._btn_rename.setEnabled(is_primary)
        self._btn_delete.setEnabled(is_primary)

        self._item_list.clear()
        if has_selection:
            cat = item.text()
            items = self._manager.list_items(self._lib_type, cat)
            self._items_label.setText(f"Items ({len(items)}):")
            for name in items:
                self._item_list.addItem(name)
            self._item_list.setEnabled(True)
        else:
            self._items_label.setText("Items:")
            self._item_list.setEnabled(False)

    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, "New Category", "Category name:")
        if not ok or not name.strip():
            return
        try:
            self._manager.create_category(self._lib_type, name.strip())
            self._refresh()
            self.changed.emit()
        except CategoryError as e:
            QMessageBox.warning(self, "Cannot Create Category", str(e))

    def _on_rename(self) -> None:
        item = self._cat_list.currentItem()
        if item is None:
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(
            self, "Rename Category", "New name:", text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self._manager.rename_category(self._lib_type, old_name, new_name.strip())
            self._refresh()
            self.changed.emit()
        except CategoryError as e:
            QMessageBox.warning(self, "Cannot Rename", str(e))

    def _on_delete(self) -> None:
        item = self._cat_list.currentItem()
        if item is None:
            return
        cat = item.text()
        count = self._manager.item_count(self._lib_type, cat)

        if count > 0:
            reply = QMessageBox.question(
                self,
                "Delete Non-empty Category",
                f"Category '{cat}' contains {count} item(s).\n\n"
                "Delete the category and all its files? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            force = True
        else:
            reply = QMessageBox.question(
                self,
                "Delete Category",
                f"Delete empty category '{cat}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            force = False

        try:
            self._manager.delete_category(self._lib_type, cat, force=force)
            self._refresh()
            self.changed.emit()
        except CategoryError as e:
            QMessageBox.warning(self, "Cannot Delete", str(e))


class CategoryManagerDialog(QDialog):
    """Modal dialog for administering library categories.

    Emits ``library_changed`` when any category is created, renamed, or deleted,
    so the caller can trigger a library reload.
    """

    library_changed = Signal()

    def __init__(self, manager: CategoryManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Manage Library Categories")
        self.setMinimumSize(520, 400)
        self._manager = manager
        self._changed = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Categories map to subdirectories inside each library root.\n"
            "Grayed-out categories belong to read-only library roots and cannot be edited here."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(info)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444444;")
        layout.addWidget(line)

        self._tabs = QTabWidget()

        prim_tab = _CategoryTab(self._manager, "primitives")
        prim_tab.changed.connect(self._on_changed)
        self._tabs.addTab(prim_tab, "Primitives")

        comp_tab = _CategoryTab(self._manager, "components")
        comp_tab.changed.connect(self._on_changed)
        self._tabs.addTab(comp_tab, "Components")

        layout.addWidget(self._tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _on_changed(self) -> None:
        self._changed = True
        self.library_changed.emit()
