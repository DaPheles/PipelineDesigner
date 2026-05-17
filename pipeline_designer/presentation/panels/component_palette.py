"""Component palette for drag-and-drop component selection."""

from PySide6.QtCore import QMimeData, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from pipeline_designer.domain.models import ComponentDefinition


# Special port type identifiers (not real components)
PORT_INPUT = "port:input"
PORT_OUTPUT = "port:output"

_SETTINGS_KEY = "component_palette/expanded"


class ComponentPalette(QTreeWidget):
    """Tree widget for selecting and dragging components.

    Structure::

        Primitives
          ├── <category>
          │     ├── ComponentA
          │     └── ComponentB
          └── ...
        Components
          ├── <category>
          │     └── ...
          └── ...
        Ports
          ├── Input
          └── Output
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._components: dict[str, ComponentDefinition] = {}
        self._setup_widget()

    def _setup_widget(self) -> None:
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIndentation(14)
        self.setIconSize(QSize(20, 20))
        self.setAnimated(True)

        self.setStyleSheet("""
            QTreeWidget {
                background-color: #2b2b2b;
                border: none;
                color: #ffffff;
            }
            QTreeWidget::item {
                padding: 4px 8px;
                border-radius: 3px;
            }
            QTreeWidget::item:selected {
                background-color: #4a90d9;
            }
            QTreeWidget::item:hover:!selected {
                background-color: #3a3a3a;
            }
            QTreeWidget::branch {
                background-color: #2b2b2b;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                border-image: none;
                image: url(none);
            }
        """)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def set_components(
        self,
        primitives: list[ComponentDefinition],
        composites: list[ComponentDefinition],
    ) -> None:
        """Rebuild the tree from separate primitive and composite lists."""
        self.clear()
        self._components.clear()

        self._build_section("Primitives", primitives)
        self._build_section("Components", composites)
        self._build_ports_section()

        self._restore_expand_state()

    def get_component(self, name: str) -> ComponentDefinition | None:
        return self._components.get(name)

    # ------------------------------------------------------------------ #
    # Tree building                                                        #
    # ------------------------------------------------------------------ #

    def _build_section(self, title: str, components: list[ComponentDefinition]) -> None:
        section = QTreeWidgetItem(self, [title])
        section.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = section.font(0)
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        section.setFont(0, font)
        section.setForeground(0, QColor("#aaaaaa"))

        categories: dict[str, list[ComponentDefinition]] = {}
        for comp in components:
            categories.setdefault(comp.category, []).append(comp)

        for cat_name in sorted(categories.keys()):
            cat_item = QTreeWidgetItem(section, [cat_name])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cat_item.setForeground(0, QColor("#888888"))
            cat_font = cat_item.font(0)
            cat_font.setBold(True)
            cat_item.setFont(0, cat_font)

            for comp in sorted(categories[cat_name], key=lambda c: c.name):
                self._add_component_item(cat_item, comp)

            cat_item.setExpanded(True)

        section.setExpanded(True)

    def _add_component_item(
        self, parent: QTreeWidgetItem, component: ComponentDefinition
    ) -> None:
        item = QTreeWidgetItem(parent, [component.name])
        item.setData(0, Qt.ItemDataRole.UserRole, component.name)
        item.setToolTip(0, component.description or f"Drag to add {component.name}")
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )
        item.setIcon(0, self._create_component_icon(component))
        self._components[component.name] = component

    def _build_ports_section(self) -> None:
        section = QTreeWidgetItem(self, ["Ports"])
        section.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = section.font(0)
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        section.setFont(0, font)
        section.setForeground(0, QColor("#aaaaaa"))

        input_item = QTreeWidgetItem(section, ["Input"])
        input_item.setData(0, Qt.ItemDataRole.UserRole, PORT_INPUT)
        input_item.setToolTip(0, "Drag to input stage to add an input port")
        input_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )
        input_item.setIcon(0, self._create_port_icon(is_input=True))

        output_item = QTreeWidgetItem(section, ["Output"])
        output_item.setData(0, Qt.ItemDataRole.UserRole, PORT_OUTPUT)
        output_item.setToolTip(0, "Drag to output stage to add an output port")
        output_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )
        output_item.setIcon(0, self._create_port_icon(is_input=False))

        section.setExpanded(True)

    # ------------------------------------------------------------------ #
    # Expand / collapse persistence                                        #
    # ------------------------------------------------------------------ #

    def _collect_expand_state(self) -> dict[str, bool]:
        """Return {item_text: is_expanded} for all top-level and category items."""
        state: dict[str, bool] = {}
        for i in range(self.topLevelItemCount()):
            section = self.topLevelItem(i)
            key = section.text(0)
            state[key] = section.isExpanded()
            for j in range(section.childCount()):
                cat = section.child(j)
                state[f"{key}/{cat.text(0)}"] = cat.isExpanded()
        return state

    def _restore_expand_state(self) -> None:
        settings = QSettings("PipelineDesigner", "ComponentPalette")
        saved: dict = settings.value(_SETTINGS_KEY, {})
        if not isinstance(saved, dict):
            return
        for i in range(self.topLevelItemCount()):
            section = self.topLevelItem(i)
            key = section.text(0)
            if key in saved:
                section.setExpanded(bool(saved[key]))
            for j in range(section.childCount()):
                cat = section.child(j)
                cat_key = f"{key}/{cat.text(0)}"
                if cat_key in saved:
                    cat.setExpanded(bool(saved[cat_key]))

    def save_expand_state(self) -> None:
        """Persist current expand state to QSettings (call on app close)."""
        settings = QSettings("PipelineDesigner", "ComponentPalette")
        settings.setValue(_SETTINGS_KEY, self._collect_expand_state())

    # ------------------------------------------------------------------ #
    # Drag & drop                                                          #
    # ------------------------------------------------------------------ #

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return

        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if item_data is None:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(item_data)
        drag.setMimeData(mime_data)

        if item_data in (PORT_INPUT, PORT_OUTPUT):
            pixmap = self._create_port_drag_pixmap(item_data == PORT_INPUT)
        else:
            component = self._components.get(item_data)
            pixmap = self._create_drag_pixmap(component) if component else QPixmap()

        drag.setPixmap(pixmap)
        drag.setHotSpot(pixmap.rect().center())
        drag.exec(Qt.DropAction.CopyAction)

    # ------------------------------------------------------------------ #
    # Icon helpers                                                         #
    # ------------------------------------------------------------------ #

    def _create_component_icon(self, component: ComponentDefinition) -> QIcon:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(component.visual.color)
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawRoundedRect(1, 1, 18, 18, 3, 3)
        painter.end()
        return QIcon(pixmap)

    def _create_port_icon(self, is_input: bool) -> QIcon:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#27ae60") if is_input else QColor("#e67e22")
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawEllipse(1, 1, 18, 18)
        painter.end()
        return QIcon(pixmap)

    def _create_drag_pixmap(self, component: ComponentDefinition) -> QPixmap:
        w, h = int(component.visual.width), int(component.visual.height)
        pixmap = QPixmap(max(w, 60), max(h, 30))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(component.visual.color)
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawRoundedRect(1, 1, pixmap.width() - 2, pixmap.height() - 2, 8, 8)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, component.name)
        painter.end()
        return pixmap

    def _create_port_drag_pixmap(self, is_input: bool) -> QPixmap:
        size = 30
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#27ae60") if is_input else QColor("#e67e22")
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.end()
        return pixmap


# ------------------------------------------------------------------ #
# Module-level helpers (unchanged interface for callers)              #
# ------------------------------------------------------------------ #

def is_port_item(item_data: str) -> bool:
    return item_data in (PORT_INPUT, PORT_OUTPUT)


def is_input_port_item(item_data: str) -> bool:
    return item_data == PORT_INPUT


def is_output_port_item(item_data: str) -> bool:
    return item_data == PORT_OUTPUT
