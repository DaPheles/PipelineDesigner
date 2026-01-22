"""Component palette for drag-and-drop component selection."""

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from pipeline_designer.domain.models import ComponentDefinition


# Special port type identifiers (not real components)
PORT_INPUT = "port:input"
PORT_OUTPUT = "port:output"


class ComponentPalette(QListWidget):
    """List widget for selecting and dragging components."""

    def __init__(self, parent: QWidget | None = None):
        """Initialize the component palette."""
        super().__init__(parent)

        self._components: dict[str, ComponentDefinition] = {}

        self._setup_widget()

    def _setup_widget(self) -> None:
        """Configure widget settings."""
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIconSize(self.iconSize().scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio))
        self.setSpacing(2)

        self.setStyleSheet("""
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                color: #ffffff;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #4a90d9;
            }
            QListWidget::item:hover {
                background-color: #3a3a3a;
            }
        """)

    def set_components(self, components: list[ComponentDefinition]) -> None:
        """Set the list of available components.

        Args:
            components: List of component definitions to display.
        """
        self.clear()
        self._components.clear()

        categories: dict[str, list[ComponentDefinition]] = {}
        for comp in components:
            if comp.category not in categories:
                categories[comp.category] = []
            categories[comp.category].append(comp)

        for category in sorted(categories.keys()):
            category_item = QListWidgetItem(f"-- {category.upper()} --")
            category_item.setFlags(Qt.ItemFlag.NoItemFlags)
            category_item.setForeground(QColor("#888888"))
            self.addItem(category_item)

            for comp in sorted(categories[category], key=lambda c: c.name):
                self._add_component_item(comp)

        # Add the special "ports" category at the end
        self._add_ports_category()

    def _add_component_item(self, component: ComponentDefinition) -> None:
        """Add a component to the list."""
        item = QListWidgetItem(component.name)
        item.setData(Qt.ItemDataRole.UserRole, component.name)
        item.setToolTip(component.description or f"Drag to add {component.name}")

        icon = self._create_component_icon(component)
        item.setIcon(icon)

        self._components[component.name] = component
        self.addItem(item)

    def _add_ports_category(self) -> None:
        """Add the special ports category with input/output items."""
        # Category header
        category_item = QListWidgetItem("-- PORTS --")
        category_item.setFlags(Qt.ItemFlag.NoItemFlags)
        category_item.setForeground(QColor("#888888"))
        self.addItem(category_item)

        # Input port item
        input_item = QListWidgetItem("Input")
        input_item.setData(Qt.ItemDataRole.UserRole, PORT_INPUT)
        input_item.setToolTip("Drag to input stage to add an input port")
        input_item.setIcon(self._create_port_icon(is_input=True))
        self.addItem(input_item)

        # Output port item
        output_item = QListWidgetItem("Output")
        output_item.setData(Qt.ItemDataRole.UserRole, PORT_OUTPUT)
        output_item.setToolTip("Drag to output stage to add an output port")
        output_item.setIcon(self._create_port_icon(is_input=False))
        self.addItem(output_item)

    def _create_port_icon(self, is_input: bool) -> QIcon:
        """Create a colored icon for a port item."""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if is_input:
            color = QColor("#27ae60")  # Green for input
        else:
            color = QColor("#e67e22")  # Orange for output

        painter.setBrush(color)
        painter.setPen(color.darker(150))
        # Draw a circle for ports (different from component rectangles)
        painter.drawEllipse(2, 2, 20, 20)

        painter.end()
        return QIcon(pixmap)

    def _create_component_icon(self, component: ComponentDefinition) -> QIcon:
        """Create a colored icon for a component."""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(component.visual.color)
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawRoundedRect(2, 2, 20, 20, 4, 4)

        painter.end()
        return QIcon(pixmap)

    def startDrag(self, supportedActions) -> None:
        """Start a drag operation for the selected component or port."""
        item = self.currentItem()
        if item is None:
            return

        item_data = item.data(Qt.ItemDataRole.UserRole)
        if item_data is None:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(item_data)
        drag.setMimeData(mime_data)

        # Handle port items vs component items
        if item_data in (PORT_INPUT, PORT_OUTPUT):
            pixmap = self._create_port_drag_pixmap(item_data == PORT_INPUT)
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())
        else:
            component = self._components.get(item_data)
            if component:
                pixmap = self._create_drag_pixmap(component)
                drag.setPixmap(pixmap)
                drag.setHotSpot(pixmap.rect().center())

        drag.exec(Qt.DropAction.CopyAction)

    def _create_port_drag_pixmap(self, is_input: bool) -> QPixmap:
        """Create a pixmap for dragging a port item."""
        size = 30

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if is_input:
            color = QColor("#27ae60")  # Green for input
        else:
            color = QColor("#e67e22")  # Orange for output

        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawEllipse(2, 2, size - 4, size - 4)

        painter.end()
        return pixmap

    def _create_drag_pixmap(self, component: ComponentDefinition) -> QPixmap:
        """Create a pixmap for the drag preview."""
        width = int(component.visual.width)
        height = int(component.visual.height)

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(component.visual.color)
        painter.setBrush(color)
        painter.setPen(color.darker(150))
        painter.drawRoundedRect(1, 1, width - 2, height - 2, 8, 8)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, component.name)

        painter.end()
        return pixmap

    def get_component(self, name: str) -> ComponentDefinition | None:
        """Get a component definition by name."""
        return self._components.get(name)


def is_port_item(item_data: str) -> bool:
    """Check if the item data represents a port (not a component)."""
    return item_data in (PORT_INPUT, PORT_OUTPUT)


def is_input_port_item(item_data: str) -> bool:
    """Check if the item data represents an input port."""
    return item_data == PORT_INPUT


def is_output_port_item(item_data: str) -> bool:
    """Check if the item data represents an output port."""
    return item_data == PORT_OUTPUT
