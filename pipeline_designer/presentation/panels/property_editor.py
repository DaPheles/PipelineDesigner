"""Property editor panel for editing selected component/connection properties."""

from typing import Any
from uuid import UUID

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
    InterfaceDirection,
    InterfacePort,
    Port,
    PortDirection,
    PortSignalClass,
)
from pipeline_designer.domain.models.behavior import SignalKind
from pipeline_designer.domain.models.signal_constraints import (
    ALLOWED_KINDS,
    default_signal_type,
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
    # Emitted when a port property changes: (component_id, port_name, property_name, new_value)
    port_changed = Signal(object, str, str, object)
    # Emitted when an interface port property changes: (port_id, property_name, new_value)
    interface_port_changed = Signal(object, str, object)
    # Emitted when the user requests rename or clone
    rename_requested = Signal()
    clone_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        """Initialize the property editor.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        self._current_instance: ComponentInstance | None = None
        self._current_definition: ComponentDefinition | None = None
        self._current_connection: Connection | None = None
        self._current_port: Port | None = None
        self._current_port_component_id: UUID | None = None
        self._current_interface_port: InterfacePort | None = None
        self._property_widgets: dict[str, QWidget] = {}

        # Data types selectable for DATA-class interface ports
        self._data_data_types = [
            SignalKind.UFIXED.value,
            SignalKind.SFIXED.value,
        ]

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Always-visible design header
        design_header = QWidget()
        design_header.setStyleSheet("background-color: #2d2d2d;")
        header_layout = QHBoxLayout(design_header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(6)

        self._design_name_label = QLabel("Untitled")
        self._design_name_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        header_layout.addWidget(self._design_name_label, stretch=1)

        rename_btn = QPushButton("Rename")
        rename_btn.setFixedHeight(22)
        rename_btn.clicked.connect(self.rename_requested)
        header_layout.addWidget(rename_btn)

        layout.addWidget(design_header)

        header_sep = QFrame()
        header_sep.setFrameShape(QFrame.Shape.HLine)
        header_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(header_sep)

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
        self._current_port = None
        self._current_port_component_id = None
        self._current_interface_port = None

    def clear(self) -> None:
        """Clear the property editor (public interface)."""
        self._show_empty()

    def set_design_name(self, name: str) -> None:
        """Update the design name shown in the header."""
        self._design_name_label.setText(name)

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

        # Composite component info
        if instance.is_composite:
            composite_label = QLabel("Yes")
            composite_label.setStyleSheet("color: #9b59b6; font-weight: bold;")
            self._content_layout.addRow("Composite:", composite_label)

            latency_label = QLabel(f"{instance.stage_count} stage(s)")
            latency_label.setStyleSheet("color: #888;")
            self._content_layout.addRow("Latency:", latency_label)

        # Pipeline stage (read-only, if set)
        if instance.pipeline_stage is not None:
            if instance.is_composite and instance.stage_count > 1:
                end_stage = instance.get_end_stage()
                stage_text = f"Stage {instance.pipeline_stage} - {end_stage}"
            else:
                stage_text = f"Stage {instance.pipeline_stage}"
            stage_label = QLabel(stage_text)
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

    def set_port(
        self,
        port: Port,
        component_id: UUID,
        component_name: str = "",
    ) -> None:
        """Set the port to edit.

        Args:
            port: The port to edit.
            component_id: ID of the component this port belongs to.
            component_name: Display name of the parent component.
        """
        self._clear_content()
        self._current_instance = None
        self._current_definition = None
        self._current_connection = None
        self._current_port = port
        self._current_port_component_id = component_id

        # Title
        direction_str = "Input" if port.direction == PortDirection.IN else "Output"
        self._title_label.setText(f"Port ({direction_str})")

        # Port name (editable)
        name_edit = QLineEdit()
        name_edit.setText(port.name)
        name_edit.editingFinished.connect(
            lambda: self._on_port_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Name:", name_edit)
        self._property_widgets["port_name"] = name_edit

        # Signal type (read-only)
        st = port.signal_type
        notation = st.notation()
        type_text = notation if notation else st.kind
        type_label = QLabel(type_text)
        type_label.setStyleSheet("color: #888;")
        self._content_layout.addRow("Signal Type:", type_label)

        # Signal class (editable combo)
        sc_combo = QComboBox()
        for member in PortSignalClass:
            sc_combo.addItem(member.value)
        sc_combo.setCurrentText(port.signal_class.value)
        sc_combo.currentTextChanged.connect(self._on_port_signal_class_changed)
        self._content_layout.addRow("Signal Class:", sc_combo)
        self._property_widgets["port_signal_class"] = sc_combo

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._content_layout.addRow(sep)

        # Direction (read-only)
        direction_label = QLabel(port.direction.value)
        if port.direction == PortDirection.IN:
            direction_label.setStyleSheet("color: #70ad47;")  # Green for input
        else:
            direction_label.setStyleSheet("color: #ed7d31;")  # Orange for output
        self._content_layout.addRow("Direction:", direction_label)

        # Parent component (read-only)
        if component_name:
            comp_label = QLabel(component_name)
            comp_label.setStyleSheet("color: #888;")
            self._content_layout.addRow("Component:", comp_label)

        # Position (read-only, if set)
        if port.position:
            pos_label = QLabel(f"({port.position[0]}, {port.position[1]})")
            pos_label.setStyleSheet("color: #888;")
            self._content_layout.addRow("Position:", pos_label)

    def _on_port_name_changed(self, name: str) -> None:
        """Handle port name change."""
        if self._current_port and self._current_port_component_id:
            old_name = self._current_port.name
            new_name = name.strip()
            if new_name and new_name != old_name:
                self._current_port.name = new_name
                self.port_changed.emit(
                    self._current_port_component_id, old_name, "name", new_name
                )

    def _on_port_signal_class_changed(self, value: str) -> None:
        """Handle signal-class combo change."""
        if not self._current_port or not self._current_port_component_id:
            return
        try:
            new_sc = PortSignalClass(value)
        except ValueError:
            return
        if new_sc == self._current_port.signal_class:
            return
        self._current_port.signal_class = new_sc
        self.port_changed.emit(
            self._current_port_component_id,
            self._current_port.name,
            "signal_class",
            new_sc,
        )

    def set_interface_port(self, interface_port: InterfacePort) -> None:
        """Set the interface port to edit."""
        self._clear_content()
        self._current_instance = None
        self._current_definition = None
        self._current_connection = None
        self._current_port = None
        self._current_port_component_id = None
        self._current_interface_port = interface_port

        direction_str = "Input" if interface_port.direction == InterfaceDirection.INPUT else "Output"
        self._title_label.setText(f"Interface Port ({direction_str})")

        # Port name (editable)
        name_edit = QLineEdit()
        name_edit.setText(interface_port.name)
        name_edit.editingFinished.connect(
            lambda: self._on_interface_port_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Name:", name_edit)
        self._property_widgets["interface_port_name"] = name_edit

        # Signal class (editable — drives whether type widget is read-only)
        isc_combo = QComboBox()
        for member in PortSignalClass:
            isc_combo.addItem(member.value)
        isc_combo.setCurrentText(interface_port.signal_class.value)
        self._content_layout.addRow("Signal Class:", isc_combo)
        self._property_widgets["interface_port_signal_class"] = isc_combo

        # Signal type — read-only label for clock/reset/control, combo for data
        self._rebuild_interface_type_widget(interface_port.signal_class, interface_port.data_type)

        # Connect signal class AFTER type widget is created so the rebuild sees it
        isc_combo.currentTextChanged.connect(self._on_interface_port_signal_class_changed)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._content_layout.addRow(sep)

        # Direction (read-only)
        direction_label = QLabel(interface_port.direction.value)
        if interface_port.direction == InterfaceDirection.INPUT:
            direction_label.setStyleSheet("color: #27ae60;")
        else:
            direction_label.setStyleSheet("color: #e67e22;")
        self._content_layout.addRow("Direction:", direction_label)

        if interface_port.position:
            pos_label = QLabel(f"({interface_port.position[0]}, {interface_port.position[1]})")
            pos_label.setStyleSheet("color: #888;")
            self._content_layout.addRow("Position:", pos_label)

        id_label = QLabel(str(interface_port.id)[:8])
        id_label.setStyleSheet("color: #888; font-family: monospace;")
        self._content_layout.addRow("ID:", id_label)

    def _canonical_type_for_class(self, signal_class: PortSignalClass) -> str:
        """Return the fixed data_type string for unambiguous signal classes."""
        match signal_class:
            case PortSignalClass.CLOCK | PortSignalClass.RESET:
                return SignalKind.STD_LOGIC.value
            case PortSignalClass.CONTROL:
                return SignalKind.STD_LOGIC.value  # default; user can switch to slv
            case _:
                return SignalKind.UFIXED.value

    def _rebuild_interface_type_widget(
        self,
        signal_class: PortSignalClass,
        current_type: str,
    ) -> None:
        """Replace the Signal Type row with the appropriate widget for signal_class.

        Clock/reset/control → read-only label (type is fixed or auto-selected).
        Data → editable combo limited to sfixed/ufixed.
        """
        # Remove existing type widget row if present
        old_w = self._property_widgets.pop("interface_port_type", None)
        if old_w is not None:
            for i in range(self._content_layout.rowCount()):
                field = self._content_layout.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if field and field.widget() is old_w:
                    self._content_layout.removeRow(i)
                    break

        allowed = ALLOWED_KINDS[signal_class]
        is_data = signal_class == PortSignalClass.DATA

        if is_data:
            # Editable combo: sfixed / ufixed
            type_combo = QComboBox()
            type_combo.addItems(self._data_data_types)
            if current_type in allowed:
                idx = type_combo.findText(current_type)
                if idx >= 0:
                    type_combo.setCurrentIndex(idx)
            else:
                type_combo.setCurrentIndex(0)
                # Correct the model too
                if self._current_interface_port:
                    self._current_interface_port.data_type = type_combo.currentText()
            type_combo.currentTextChanged.connect(self._on_interface_port_type_changed)
            # Insert after the signal-class row (row index 1, 0-based)
            self._content_layout.insertRow(2, "Signal Type:", type_combo)
            self._property_widgets["interface_port_type"] = type_combo
        else:
            # Fixed, read-only label
            canonical = self._canonical_type_for_class(signal_class)
            type_label = QLabel(canonical)
            type_label.setStyleSheet("color: #888;")
            # Correct the model if needed
            if self._current_interface_port and self._current_interface_port.data_type != canonical:
                self._current_interface_port.data_type = canonical
                self.interface_port_changed.emit(
                    self._current_interface_port.id, "data_type", canonical
                )
            self._content_layout.insertRow(2, "Signal Type:", type_label)
            self._property_widgets["interface_port_type"] = type_label

    def _on_interface_port_name_changed(self, name: str) -> None:
        if self._current_interface_port:
            new_name = name.strip()
            if new_name and new_name != self._current_interface_port.name:
                self._current_interface_port.name = new_name
                self.interface_port_changed.emit(
                    self._current_interface_port.id, "name", new_name
                )

    def _on_interface_port_type_changed(self, data_type: str) -> None:
        if self._current_interface_port:
            new_type = data_type.strip()
            if new_type and new_type != self._current_interface_port.data_type:
                self._current_interface_port.data_type = new_type
                self.interface_port_changed.emit(
                    self._current_interface_port.id, "data_type", new_type
                )

    def _on_interface_port_signal_class_changed(self, value: str) -> None:
        if not self._current_interface_port:
            return
        try:
            new_sc = PortSignalClass(value)
        except ValueError:
            return
        if new_sc == self._current_interface_port.signal_class:
            return
        self._current_interface_port.signal_class = new_sc
        self.interface_port_changed.emit(
            self._current_interface_port.id, "signal_class", new_sc
        )
        # Rebuild type widget for the new class (auto-corrects data_type if needed)
        current_type = self._current_interface_port.data_type
        self._rebuild_interface_type_widget(new_sc, current_type)

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
