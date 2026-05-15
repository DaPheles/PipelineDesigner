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
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Connection,
    Design,
    Generic,
    InterfaceDirection,
    InterfacePort,
    Port,
    PortDirection,
    PortSignalClass,
)
from pipeline_designer.domain.models.behavior import SignalKind, SignalType
from pipeline_designer.domain.models.signal_constraints import (
    ALLOWED_KINDS,
    default_signal_type,
)
from pipeline_designer.presentation.panels.component_tables import (
    InstanceGenericTable,
    InterfacePortDisplayTable,
    PortInfoTable,
)
from pipeline_designer.presentation.primitive_editor.generic_table import GenericTable


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold; color: #aaa; margin-top: 4px;")
    return lbl


class PropertyEditor(QWidget):
    """Panel for editing properties of selected components or connections.

    Shows editable properties when a single item is selected; shows design-level
    entity generics when nothing is selected.
    """

    property_changed = Signal(object, str, object)
    connection_changed = Signal(object, str, object)
    port_changed = Signal(object, str, str, object)
    interface_port_changed = Signal(object, str, object)
    rename_requested = Signal()
    clone_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._current_instance:    ComponentInstance | None  = None
        self._current_definition:  ComponentDefinition | None = None
        self._current_connection:  Connection | None          = None
        self._current_port:        Port | None                = None
        self._current_port_component_id: UUID | None          = None
        self._current_interface_port: InterfacePort | None    = None
        self._current_design:      Design | None              = None

        self._property_widgets: dict[str, QWidget] = {}

        self._data_data_types = [
            SignalKind.UFIXED.value,
            SignalKind.SFIXED.value,
        ]

        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
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
        layout.addWidget(_sep())

        # Scrollable property area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll)

        self._container = QWidget()
        self._form_layout = QVBoxLayout(self._container)
        self._form_layout.setContentsMargins(8, 8, 8, 8)
        self._form_layout.setSpacing(4)
        scroll.setWidget(self._container)

        self._title_label = QLabel("No Selection")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        self._form_layout.addWidget(self._title_label)
        self._form_layout.addWidget(_sep())

        # Content widget — holds the dynamic property form
        self._content_widget = QWidget()
        self._content_layout = QFormLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        self._form_layout.addWidget(self._content_widget)
        self._form_layout.addStretch()

        self._show_empty()

    # ── State management ──────────────────────────────────────────────────────

    def _clear_content(self) -> None:
        while self._content_layout.rowCount() > 0:
            self._content_layout.removeRow(0)
        self._property_widgets.clear()

    def _show_empty(self) -> None:
        self._clear_content()
        self._current_instance       = None
        self._current_definition     = None
        self._current_connection     = None
        self._current_port           = None
        self._current_port_component_id = None
        self._current_interface_port = None
        if self._current_design is not None:
            self._show_design_generics()
        else:
            self._title_label.setText("No Selection")

    def clear(self) -> None:
        self._show_empty()

    def set_design_name(self, name: str) -> None:
        self._design_name_label.setText(name)

    def set_design(self, design: Design) -> None:
        """Call whenever a new design is loaded or its port list changes.

        Updates the design reference and refreshes the panel if no item is
        currently selected (so the design-level generics and port table stay
        current after canvas-side port additions or removals).
        """
        self._current_design = design
        if (self._current_instance is None
                and self._current_connection is None
                and self._current_port is None
                and self._current_interface_port is None):
            self._show_design_generics()

    # ── Design-level generics view (no-selection state) ───────────────────────

    def _show_design_generics(self) -> None:
        """Show design entity generics and interface ports when nothing is selected."""
        self._clear_content()
        self._title_label.setText("Design Properties")

        if self._current_design is None:
            return

        # ── Entity generics ───────────────────────────────────────────────
        self._content_layout.addRow(_section_label("Entity Generics"))
        gen_info = QLabel(
            "VHDL entity generics for export.  "
            "Instance values may reference these by name."
        )
        gen_info.setWordWrap(True)
        gen_info.setStyleSheet("color: #888; font-size: 9pt;")
        self._content_layout.addRow(gen_info)

        gen_table = GenericTable()
        gen_table.set_generics(self._current_design.component_config.generics)
        gen_table.data_changed.connect(self._on_design_generics_changed)
        self._content_layout.addRow(gen_table)
        self._property_widgets["design_generics"] = gen_table

        # ── Interface ports ───────────────────────────────────────────────
        self._content_layout.addRow(_sep())
        self._content_layout.addRow(_section_label("Interface Ports"))
        port_info = QLabel(
            "Click a port on the canvas to edit its properties.\n"
            "Width and LSB accept numbers or design-generic names (e.g. WIDTH)."
        )
        port_info.setWordWrap(True)
        port_info.setStyleSheet("color: #888; font-size: 9pt;")
        self._content_layout.addRow(port_info)

        iport_table = InterfacePortDisplayTable()
        iport_table.set_interface_ports(self._current_design.interface_ports)
        iport_table.set_generics(self._design_generics_dict())
        iport_table.port_changed.connect(
            lambda pid, field, val: self.interface_port_changed.emit(pid, field, val)
        )
        iport_table.port_reordered.connect(self._on_interface_ports_reordered)
        self._content_layout.addRow(iport_table)
        self._property_widgets["design_ports"] = iport_table

    def _design_generics_dict(self) -> dict:
        if self._current_design is None:
            return {}
        return {
            g.name: g.default_value
            for g in self._current_design.component_config.generics
            if g.default_value is not None
        }

    def _on_design_generics_changed(self) -> None:
        tbl = self._property_widgets.get("design_generics")
        if isinstance(tbl, GenericTable) and self._current_design is not None:
            self._current_design.component_config.generics = tbl.get_generics()
        # Push updated generics to the port table so notation resolves immediately
        port_tbl = self._property_widgets.get("design_ports")
        if isinstance(port_tbl, InterfacePortDisplayTable):
            port_tbl.set_generics(self._design_generics_dict())

    def _on_interface_ports_reordered(self, new_order: list) -> None:
        if self._current_design is None:
            return
        port_map = {p.id: p for p in self._current_design.interface_ports}
        self._current_design.interface_ports = [
            port_map[uid] for uid in new_order if uid in port_map
        ]

    # ── Component view ────────────────────────────────────────────────────────

    def set_component(
        self,
        instance: ComponentInstance,
        definition: ComponentDefinition | None = None,
    ) -> None:
        self._clear_content()
        self._current_instance   = instance
        self._current_definition = definition
        self._current_connection = None

        type_name = definition.name if definition else instance.definition_ref
        self._title_label.setText(f"Component: {type_name}")

        # ── Instance name ─────────────────────────────────────────────────
        name_edit = QLineEdit()
        name_edit.setText(instance.instance_name or "")
        name_edit.setPlaceholderText(instance.get_display_name())
        name_edit.editingFinished.connect(
            lambda: self._on_instance_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Instance Name:", name_edit)
        self._property_widgets["instance_name"] = name_edit

        # ── Type (read-only) ──────────────────────────────────────────────
        type_label = QLabel(type_name)
        type_label.setStyleSheet("color: #888;")
        self._content_layout.addRow("Type:", type_label)

        # ── Composite info ────────────────────────────────────────────────
        if instance.is_composite:
            comp_lbl = QLabel("Yes")
            comp_lbl.setStyleSheet("color: #9b59b6; font-weight: bold;")
            self._content_layout.addRow("Composite:", comp_lbl)
            lat_lbl = QLabel(f"{instance.stage_count} stage(s)")
            lat_lbl.setStyleSheet("color: #888;")
            self._content_layout.addRow("Latency:", lat_lbl)

        # ── Pipeline stage ────────────────────────────────────────────────
        if instance.pipeline_stage is not None:
            if instance.is_composite and instance.stage_count > 1:
                stage_text = f"Stage {instance.pipeline_stage} – {instance.get_end_stage()}"
            else:
                stage_text = f"Stage {instance.pipeline_stage}"
            stage_lbl = QLabel(stage_text)
            stage_lbl.setStyleSheet("color: #888;")
            self._content_layout.addRow("Pipeline Stage:", stage_lbl)

        # ── Position (read-only) ──────────────────────────────────────────
        pos_lbl = QLabel(
            f"({instance.position[0]:.0f}, {instance.position[1]:.0f})"
        )
        pos_lbl.setStyleSheet("color: #888;")
        self._content_layout.addRow("Position:", pos_lbl)
        self._property_widgets["position"] = pos_lbl

        # ── Generics table ────────────────────────────────────────────────
        if definition and definition.generics:
            self._content_layout.addRow(_sep())
            self._content_layout.addRow(_section_label("Generics"))

            design_gens = (
                [g.name for g in self._current_design.component_config.generics]
                if self._current_design else []
            )
            hint = ""
            if design_gens:
                hint = "  (design generics: " + ", ".join(design_gens) + ")"
            hint_lbl = QLabel("Enter a number or a design-generic name." + hint)
            hint_lbl.setWordWrap(True)
            hint_lbl.setStyleSheet("color: #666; font-size: 8pt;")
            self._content_layout.addRow(hint_lbl)

            gen_table = InstanceGenericTable()
            gen_table.set_data(definition.generics, instance.generic_values)
            gen_table.value_changed.connect(self._on_generic_changed)
            self._content_layout.addRow(gen_table)
            self._property_widgets["instance_generics"] = gen_table

        # ── Ports table ───────────────────────────────────────────────────
        if definition and definition.ports:
            self._content_layout.addRow(_sep())
            self._content_layout.addRow(_section_label("Ports"))

            resolved = {
                g.name: g.default_value
                for g in (definition.generics if definition else [])
                if g.default_value is not None
            }
            resolved.update(instance.generic_values)

            port_table = PortInfoTable()
            port_table.set_ports(definition.ports, resolved)
            self._content_layout.addRow(port_table)
            self._property_widgets["port_info"] = port_table

    def _on_instance_name_changed(self, name: str) -> None:
        if self._current_instance:
            new_name = name.strip() if name.strip() else None
            self._current_instance.instance_name = new_name
            self.property_changed.emit(
                self._current_instance.id, "instance_name", new_name
            )

    def _on_generic_changed(self, generic_name: str, value: Any) -> None:
        if self._current_instance:
            self._current_instance.generic_values[generic_name] = value
            self.property_changed.emit(
                self._current_instance.id, f"generic.{generic_name}", value
            )

    # ── Connection view ───────────────────────────────────────────────────────

    def set_connection(
        self,
        connection: Connection,
        source_name: str = "",
        target_name: str = "",
    ) -> None:
        self._clear_content()
        self._current_instance   = None
        self._current_definition = None
        self._current_connection = connection

        self._title_label.setText("Connection")

        signal_edit = QLineEdit()
        signal_edit.setText(connection.signal_name or "")
        signal_edit.setPlaceholderText(connection.get_display_name())
        signal_edit.editingFinished.connect(
            lambda: self._on_signal_name_changed(signal_edit.text())
        )
        self._content_layout.addRow("Signal Name:", signal_edit)
        self._property_widgets["signal_name"] = signal_edit

        self._content_layout.addRow(_sep())

        src_lbl = QLabel(f"{source_name}.{connection.source.port_name}")
        src_lbl.setStyleSheet("color: #ed7d31;")
        self._content_layout.addRow("Source:", src_lbl)

        tgt_lbl = QLabel(f"{target_name}.{connection.target.port_name}")
        tgt_lbl.setStyleSheet("color: #70ad47;")
        self._content_layout.addRow("Target:", tgt_lbl)

        id_lbl = QLabel(str(connection.id)[:8])
        id_lbl.setStyleSheet("color: #888; font-family: monospace;")
        self._content_layout.addRow("ID:", id_lbl)

    def _on_signal_name_changed(self, name: str) -> None:
        if self._current_connection:
            new_name = name.strip() if name.strip() else None
            self._current_connection.signal_name = new_name
            self.connection_changed.emit(
                self._current_connection.id, "signal_name", new_name
            )

    # ── Port view ─────────────────────────────────────────────────────────────

    def set_port(
        self,
        port: Port,
        component_id: UUID,
        component_name: str = "",
        generic_values: dict | None = None,
    ) -> None:
        self._clear_content()
        self._current_instance   = None
        self._current_definition = None
        self._current_connection = None
        self._current_port       = port
        self._current_port_component_id = component_id

        direction_str = "Input" if port.direction == PortDirection.IN else "Output"
        self._title_label.setText(f"Port ({direction_str})")

        name_edit = QLineEdit()
        name_edit.setText(port.name)
        name_edit.editingFinished.connect(
            lambda: self._on_port_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Name:", name_edit)
        self._property_widgets["port_name"] = name_edit

        sc_combo = QComboBox()
        for member in PortSignalClass:
            sc_combo.addItem(member.value)
        sc_combo.setCurrentText(port.signal_class.value)
        sc_combo.currentTextChanged.connect(self._on_port_signal_class_changed)
        self._content_layout.addRow("Signal Class:", sc_combo)
        self._property_widgets["port_signal_class"] = sc_combo

        st = port.signal_type
        g  = generic_values or {}
        is_data = port.signal_class == PortSignalClass.DATA
        type_text = st.kind if is_data else st.to_vhdl_type(g)
        type_lbl = QLabel(type_text)
        type_lbl.setStyleSheet("color: #888;")
        self._content_layout.addRow("Signal Type:", type_lbl)

        if is_data:
            notation = st.notation(g)
            vhdl     = st.to_vhdl_type(g)
            fmt_text = f"{notation}  ·  {vhdl}" if notation else vhdl
            fmt_lbl  = QLabel(fmt_text)
            fmt_lbl.setStyleSheet("color: #89dceb; font-family: monospace;")
            self._content_layout.addRow("Format:", fmt_lbl)

        self._content_layout.addRow(_sep())

        dir_lbl = QLabel(port.direction.value)
        if port.direction == PortDirection.IN:
            dir_lbl.setStyleSheet("color: #70ad47;")
        else:
            dir_lbl.setStyleSheet("color: #ed7d31;")
        self._content_layout.addRow("Direction:", dir_lbl)

        if component_name:
            comp_lbl = QLabel(component_name)
            comp_lbl.setStyleSheet("color: #888;")
            self._content_layout.addRow("Component:", comp_lbl)

        if port.position:
            pos_lbl = QLabel(f"({port.position[0]}, {port.position[1]})")
            pos_lbl.setStyleSheet("color: #888;")
            self._content_layout.addRow("Position:", pos_lbl)

    def _on_port_name_changed(self, name: str) -> None:
        if self._current_port and self._current_port_component_id:
            old_name = self._current_port.name
            new_name = name.strip()
            if new_name and new_name != old_name:
                self._current_port.name = new_name
                self.port_changed.emit(
                    self._current_port_component_id, old_name, "name", new_name
                )

    def _on_port_signal_class_changed(self, value: str) -> None:
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

    # ── Interface port view ───────────────────────────────────────────────────

    def set_interface_port(self, interface_port: InterfacePort) -> None:
        self._clear_content()
        self._current_instance       = None
        self._current_definition     = None
        self._current_connection     = None
        self._current_port           = None
        self._current_port_component_id = None
        self._current_interface_port = interface_port

        direction_str = (
            "Input"
            if interface_port.direction == InterfaceDirection.INPUT
            else "Output"
        )
        self._title_label.setText(f"Interface Port ({direction_str})")

        name_edit = QLineEdit()
        name_edit.setText(interface_port.name)
        name_edit.editingFinished.connect(
            lambda: self._on_interface_port_name_changed(name_edit.text())
        )
        self._content_layout.addRow("Name:", name_edit)
        self._property_widgets["interface_port_name"] = name_edit

        isc_combo = QComboBox()
        for member in PortSignalClass:
            isc_combo.addItem(member.value)
        isc_combo.setCurrentText(interface_port.signal_class.value)
        self._content_layout.addRow("Signal Class:", isc_combo)
        self._property_widgets["interface_port_signal_class"] = isc_combo

        self._rebuild_interface_type_widget(
            interface_port.signal_class, interface_port.data_type
        )
        isc_combo.currentTextChanged.connect(
            self._on_interface_port_signal_class_changed
        )

        self._content_layout.addRow(_sep())

        dir_lbl = QLabel(interface_port.direction.value)
        if interface_port.direction == InterfaceDirection.INPUT:
            dir_lbl.setStyleSheet("color: #27ae60;")
        else:
            dir_lbl.setStyleSheet("color: #e67e22;")
        self._content_layout.addRow("Direction:", dir_lbl)

        if interface_port.position:
            pos_lbl = QLabel(
                f"({interface_port.position[0]}, {interface_port.position[1]})"
            )
            pos_lbl.setStyleSheet("color: #888;")
            self._content_layout.addRow("Position:", pos_lbl)

        id_lbl = QLabel(str(interface_port.id)[:8])
        id_lbl.setStyleSheet("color: #888; font-family: monospace;")
        self._content_layout.addRow("ID:", id_lbl)

    def _canonical_type_for_class(self, signal_class: PortSignalClass) -> str:
        match signal_class:
            case PortSignalClass.CLOCK | PortSignalClass.RESET:
                return SignalKind.STD_LOGIC.value
            case PortSignalClass.CONTROL:
                return SignalKind.STD_LOGIC.value
            case _:
                return SignalKind.UFIXED.value

    def _rebuild_interface_type_widget(
        self,
        signal_class: PortSignalClass,
        current_type: str,
    ) -> None:
        for key in (
            "interface_port_format",
            "interface_port_lsb",
            "interface_port_width",
            "interface_port_type",
        ):
            old_w = self._property_widgets.pop(key, None)
            if old_w is not None:
                for i in range(self._content_layout.rowCount()):
                    field = self._content_layout.itemAt(
                        i, QFormLayout.ItemRole.FieldRole
                    )
                    if field and field.widget() is old_w:
                        self._content_layout.removeRow(i)
                        break

        allowed = ALLOWED_KINDS[signal_class]
        is_data = signal_class == PortSignalClass.DATA

        if is_data:
            iport = self._current_interface_port
            if iport is not None and iport.signal_type is not None:
                init_width_str = iport.signal_type.width
                init_lsb_str   = iport.signal_type.lsb
            else:
                init_width_str, init_lsb_str = "16", "-8"

            type_combo = QComboBox()
            type_combo.addItems(self._data_data_types)
            kind_to_use = current_type if current_type in allowed else self._data_data_types[0]
            idx = type_combo.findText(kind_to_use)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            if iport and iport.data_type != kind_to_use:
                iport.data_type = kind_to_use
            type_combo.currentTextChanged.connect(self._on_interface_port_type_changed)
            self._content_layout.insertRow(2, "Signal Type:", type_combo)
            self._property_widgets["interface_port_type"] = type_combo

            format_lbl = QLabel()
            format_lbl.setStyleSheet("color: #89dceb; font-family: monospace;")
            self._content_layout.insertRow(3, "Format:", format_lbl)
            self._property_widgets["interface_port_format"] = format_lbl

            # QLineEdit allows both numeric literals and generic-name references
            width_edit = QLineEdit()
            width_edit.setPlaceholderText("e.g. 16 or WIDTH")
            width_edit.setText(init_width_str)
            width_edit.textChanged.connect(self._on_interface_port_fp_changed)
            self._content_layout.insertRow(4, "Width (bits):", width_edit)
            self._property_widgets["interface_port_width"] = width_edit

            lsb_edit = QLineEdit()
            lsb_edit.setPlaceholderText("e.g. -8 or -FRAC_BITS")
            lsb_edit.setText(init_lsb_str)
            lsb_edit.setToolTip("LSB position: 0 = integer, negative = fractional bits")
            lsb_edit.textChanged.connect(self._on_interface_port_fp_changed)
            self._content_layout.insertRow(5, "LSB:", lsb_edit)
            self._property_widgets["interface_port_lsb"] = lsb_edit

            self._sync_interface_port_signal_type()
            self._update_interface_format_label()
        else:
            canonical = self._canonical_type_for_class(signal_class)
            type_lbl = QLabel(canonical)
            type_lbl.setStyleSheet("color: #888;")
            if (
                self._current_interface_port
                and self._current_interface_port.data_type != canonical
            ):
                self._current_interface_port.data_type = canonical
                self.interface_port_changed.emit(
                    self._current_interface_port.id, "data_type", canonical
                )
            self._content_layout.insertRow(2, "Signal Type:", type_lbl)
            self._property_widgets["interface_port_type"] = type_lbl

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
        self._sync_interface_port_signal_type()
        self._update_interface_format_label()

    def _on_interface_port_fp_changed(self) -> None:
        self._sync_interface_port_signal_type()
        self._update_interface_format_label()

    def _sync_interface_port_signal_type(self) -> None:
        if not self._current_interface_port:
            return
        kind_w  = self._property_widgets.get("interface_port_type")
        width_w = self._property_widgets.get("interface_port_width")
        lsb_w   = self._property_widgets.get("interface_port_lsb")
        if not (
            isinstance(kind_w, QComboBox)
            and isinstance(width_w, QLineEdit)
            and isinstance(lsb_w, QLineEdit)
        ):
            return
        st = SignalType(
            kind=kind_w.currentText(),
            width=width_w.text() or "1",
            lsb=lsb_w.text() or "0",
        )
        self._current_interface_port.signal_type = st
        self.interface_port_changed.emit(
            self._current_interface_port.id, "signal_type", st
        )

    def _update_interface_format_label(self) -> None:
        lbl = self._property_widgets.get("interface_port_format")
        if not isinstance(lbl, QLabel):
            return
        kind_w  = self._property_widgets.get("interface_port_type")
        width_w = self._property_widgets.get("interface_port_width")
        lsb_w   = self._property_widgets.get("interface_port_lsb")
        if not (
            isinstance(kind_w, QComboBox)
            and isinstance(width_w, QLineEdit)
            and isinstance(lsb_w, QLineEdit)
        ):
            lbl.setText("")
            return
        st       = SignalType(
            kind=kind_w.currentText(),
            width=width_w.text() or "1",
            lsb=lsb_w.text() or "0",
        )
        notation = st.notation()
        vhdl     = st.to_vhdl_type()
        lbl.setText(f"{notation}  ·  {vhdl}" if notation else vhdl)

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
        self._rebuild_interface_type_widget(
            new_sc, self._current_interface_port.data_type
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def update_position(self, x: float, y: float) -> None:
        """Update the position display when the selected component moves."""
        pos_lbl = self._property_widgets.get("position")
        if isinstance(pos_lbl, QLabel):
            pos_lbl.setText(f"({x:.0f}, {y:.0f})")
