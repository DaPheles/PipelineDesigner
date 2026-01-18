"""Property editor panel for editing selected component/connection properties."""

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Connection,
    Generic,
)


class PropertyEditor(QWidget):
    """Panel for editing properties of selected components or connections.

    Shows editable properties when a single item is selected.
    Clears when nothing is selected or multiple items are selected.
    """

    # Emitted when a component property changes: (instance_id, property_name, new_value)
    property_changed = Signal(object, str, object)
    # Emitted when a connection property changes: (connection_id, property_name, new_value)
    connection_changed = Signal(object, str, object)

    def __init__(self, parent: QWidget | None = None):
        """Initialize the property editor.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        self._current_instance: ComponentInstance | None = None
        self._current_definition: ComponentDefinition | None = None
        self._current_connection: Connection | None = None
        self._property_widgets: dict[str, QWidget] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for properties
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll)

        # Container widget for form
        self._container = QWidget()
        self._form_layout = QFormLayout(self._container)
        self._form_layout.setContentsMargins(8, 8, 8, 8)
        self._form_layout.setSpacing(8)
        scroll.setWidget(self._container)

        # Title label
        self._title_label = QLabel("No Selection")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        self._form_layout.addRow(self._title_label)

        # Separator
        self._separator = QFrame()
        self._separator.setFrameShape(QFrame.Shape.HLine)
        self._separator.setFrameShadow(QFrame.Shadow.Sunken)
        self._form_layout.addRow(self._separator)

        # Content area (will be populated dynamically)
        self._content_widget = QWidget()
        self._content_layout = QFormLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        self._form_layout.addRow(self._content_widget)

        self._show_empty()

    def _clear_content(self) -> None:
        """Clear all property widgets."""
        while self._content_layout.rowCount() > 0:
            self._content_layout.removeRow(0)
        self._property_widgets.clear()

    def _show_empty(self) -> None:
        """Show empty state (no selection)."""
        self._clear_content()
        self._title_label.setText("No Selection")
        self._current_instance = None
        self._current_definition = None
        self._current_connection = None

    def clear(self) -> None:
        """Clear the property editor (public interface)."""
        self._show_empty()

    def set_component(
        self,
        instance: ComponentInstance,
        definition: ComponentDefinition | None = None,
    ) -> None:
        """Set the component to edit.

        Args:
            instance: The component instance to edit.
            definition: The component definition (for generic info).
        """
        self._clear_content()
        self._current_instance = instance
        self._current_definition = definition
        self._current_connection = None

        # Title
        type_name = definition.name if definition else instance.definition_ref
        self._title_label.setText(f"Component: {type_name}")

        # Instance name
        name_edit = QLineEdit()
        name_edit.setText(instance.instance_name or "")
        name_edit.setPlaceholderText(instance.get_display_name())
        name_edit.editingFinished.connect(
            lambda: self._on_instance_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Instance Name:", name_edit)
        self._property_widgets["instance_name"] = name_edit

        # Component type (read-only)
        type_label = QLabel(type_name)
        type_label.setStyleSheet("color: #888;")
        self._content_layout.addRow("Type:", type_label)

        # Pipeline stage (read-only, if set)
        if instance.pipeline_stage is not None:
            stage_label = QLabel(f"Stage {instance.pipeline_stage}")
            stage_label.setStyleSheet("color: #888;")
            self._content_layout.addRow("Pipeline Stage:", stage_label)

        # Position (read-only)
        pos_label = QLabel(f"({instance.position[0]:.0f}, {instance.position[1]:.0f})")
        pos_label.setStyleSheet("color: #888;")
        self._content_layout.addRow("Position:", pos_label)

        # Generics section
        if definition and definition.generics:
            # Separator before generics
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            self._content_layout.addRow(sep)

            generics_label = QLabel("Generics")
            generics_label.setStyleSheet("font-weight: bold; color: #aaa;")
            self._content_layout.addRow(generics_label)

            for generic in definition.generics:
                widget = self._create_generic_widget(generic, instance)
                self._content_layout.addRow(f"{generic.name}:", widget)
                self._property_widgets[f"generic_{generic.name}"] = widget

    def _create_generic_widget(
        self, generic: Generic, instance: ComponentInstance
    ) -> QWidget:
        """Create an appropriate widget for a generic parameter.

        Args:
            generic: The generic definition.
            instance: The component instance.

        Returns:
            Widget for editing the generic value.
        """
        current_value = instance.generic_values.get(generic.name, generic.default_value)

        if generic.data_type == "integer":
            widget = QSpinBox()
            widget.setRange(-2147483648, 2147483647)
            widget.setValue(int(current_value) if current_value is not None else 0)
            widget.valueChanged.connect(
                lambda v: self._on_generic_changed(generic.name, v)
            )
        elif generic.data_type == "real" or generic.data_type == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1e308, 1e308)
            widget.setDecimals(6)
            widget.setValue(float(current_value) if current_value is not None else 0.0)
            widget.valueChanged.connect(
                lambda v: self._on_generic_changed(generic.name, v)
            )
        elif generic.data_type == "boolean" or generic.data_type == "bool":
            widget = QSpinBox()
            widget.setRange(0, 1)
            widget.setValue(1 if current_value else 0)
            widget.valueChanged.connect(
                lambda v: self._on_generic_changed(generic.name, bool(v))
            )
        else:
            # Default to string/text
            widget = QLineEdit()
            widget.setText(str(current_value) if current_value is not None else "")
            widget.editingFinished.connect(
                lambda: self._on_generic_changed(generic.name, widget.text())
            )

        return widget

    def _on_instance_name_changed(self, name: str) -> None:
        """Handle instance name change."""
        if self._current_instance:
            new_name = name.strip() if name.strip() else None
            self._current_instance.instance_name = new_name
            self.property_changed.emit(
                self._current_instance.id, "instance_name", new_name
            )

    def _on_generic_changed(self, generic_name: str, value: Any) -> None:
        """Handle generic value change."""
        if self._current_instance:
            self._current_instance.generic_values[generic_name] = value
            self.property_changed.emit(
                self._current_instance.id, f"generic.{generic_name}", value
            )

    def set_connection(
        self,
        connection: Connection,
        source_name: str = "",
        target_name: str = "",
    ) -> None:
        """Set the connection to display.

        Args:
            connection: The connection to display.
            source_name: Display name for source component.
            target_name: Display name for target component.
        """
        self._clear_content()
        self._current_instance = None
        self._current_definition = None
        self._current_connection = connection

        self._title_label.setText("Connection")

        # Signal name (editable)
        signal_edit = QLineEdit()
        signal_edit.setText(connection.signal_name or "")
        signal_edit.setPlaceholderText(connection.get_display_name())
        signal_edit.editingFinished.connect(
            lambda: self._on_signal_name_changed(signal_edit.text())
        )
        self._content_layout.addRow("Signal Name:", signal_edit)
        self._property_widgets["signal_name"] = signal_edit

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._content_layout.addRow(sep)

        # Source info (read-only)
        source_text = f"{source_name}.{connection.source.port_name}"
        source_label = QLabel(source_text)
        source_label.setStyleSheet("color: #ed7d31;")  # Orange for output
        self._content_layout.addRow("Source:", source_label)

        # Target info (read-only)
        target_text = f"{target_name}.{connection.target.port_name}"
        target_label = QLabel(target_text)
        target_label.setStyleSheet("color: #70ad47;")  # Green for input
        self._content_layout.addRow("Target:", target_label)

        # Connection ID (read-only, abbreviated)
        id_label = QLabel(str(connection.id)[:8])
        id_label.setStyleSheet("color: #888; font-family: monospace;")
        self._content_layout.addRow("ID:", id_label)

    def _on_signal_name_changed(self, name: str) -> None:
        """Handle signal name change."""
        if self._current_connection:
            new_name = name.strip() if name.strip() else None
            self._current_connection.signal_name = new_name
            self.connection_changed.emit(
                self._current_connection.id, "signal_name", new_name
            )

    def update_position(self, x: float, y: float) -> None:
        """Update the displayed position (called when component moves)."""
        if self._current_instance:
            # Find and update position label
            for i in range(self._content_layout.rowCount()):
                label_item = self._content_layout.itemAt(i, QFormLayout.ItemRole.LabelRole)
                if label_item and label_item.widget():
                    label_text = label_item.widget().text() if hasattr(label_item.widget(), 'text') else ""
                    if label_text == "Position:":
                        field_item = self._content_layout.itemAt(
                            i, QFormLayout.ItemRole.FieldRole
                        )
                        if field_item and field_item.widget():
                            field_item.widget().setText(f"({x:.0f}, {y:.0f})")
                        break
