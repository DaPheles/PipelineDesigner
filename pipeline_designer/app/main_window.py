"""Main application window."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QStatusBar,
    QWidget,
)

from pipeline_designer.domain.models import ComponentDefinition, Design
from pipeline_designer.infrastructure.persistence import LibraryLoader
from pipeline_designer.presentation.canvas import DesignScene, DesignView
from pipeline_designer.presentation.canvas.items import ComponentItem, ConnectionItem, InterfacePortItem, PortItem
from pipeline_designer.presentation.panels import ComponentPalette, PropertyEditor
from pipeline_designer.presentation.simulation import DesignSimulationPanel
from pipeline_designer.presentation.vhdl_export import VhdlExportPanel

from .config import AppConfig


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(
        self,
        config: AppConfig | None = None,
        parent: QWidget | None = None,
    ):
        """Initialize the main window.

        Args:
            config: Application configuration.
            parent: Parent widget.
        """
        super().__init__(parent)

        self._config = config or AppConfig()
        self._library_loader = LibraryLoader()
        self._library_dict: dict[str, ComponentDefinition] = {}
        self._current_file: Path | None = None
        self._primitive_editor = None  # lazy-created singleton

        self._setup_ui()
        self._setup_menus()
        self._setup_status_bar()
        self._load_library()
        self._update_title()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setMinimumSize(800, 600)
        self.resize(self._config.window.width, self._config.window.height)

        self._scene = DesignScene()
        self._view = DesignView(self._scene)
        self.setCentralWidget(self._view)

        self._palette = ComponentPalette()
        self._palette_dock = QDockWidget("Components", self)
        self._palette_dock.setWidget(self._palette)
        self._palette_dock.setMinimumWidth(200)
        self._palette_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._palette_dock)

        # Property editor panel (right dock)
        self._property_editor = PropertyEditor()
        self._property_dock = QDockWidget("Properties", self)
        self._property_dock.setWidget(self._property_editor)
        self._property_dock.setMinimumWidth(200)
        self._property_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._property_dock)

        # Simulation panel (bottom dock)
        self._sim_panel = DesignSimulationPanel(design_getter=self._scene.get_design)
        self._sim_dock = QDockWidget("Simulation", self)
        self._sim_dock.setWidget(self._sim_panel)
        self._sim_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._sim_dock)
        self.resizeDocks([self._sim_dock], [260], Qt.Orientation.Vertical)

        # VHDL export panel (bottom dock, hidden by default)
        self._vhdl_panel = VhdlExportPanel(
            design_getter=self._scene.get_design,
            library_getter=lambda: self._library_dict,
        )
        self._vhdl_dock = QDockWidget("VHDL Export", self)
        self._vhdl_dock.setWidget(self._vhdl_panel)
        self._vhdl_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._vhdl_dock)
        self.tabifyDockWidget(self._property_dock, self._vhdl_dock)
        self._vhdl_dock.hide()

        # Connect signals
        self._view.zoom_changed.connect(self._on_zoom_changed)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._property_editor.port_changed.connect(self._on_port_changed)
        self._property_editor.interface_port_changed.connect(self._on_interface_port_changed)
        self._property_editor.rename_requested.connect(self._on_rename)
        self._property_editor.clone_requested.connect(self._on_clone)

        # Keep simulation panel in sync with topology changes
        self._scene.component_added.connect(lambda _: self._sim_panel.mark_dirty())
        self._scene.component_removed.connect(lambda _: self._sim_panel.mark_dirty())
        self._scene.connection_added.connect(lambda _: self._sim_panel.mark_dirty())
        self._scene.connection_removed.connect(lambda _: self._sim_panel.mark_dirty())
        self._scene.stages_changed.connect(self._sim_panel.mark_dirty)
        self._scene.validation_warnings.connect(self._on_validation_warnings)

    def _setup_menus(self) -> None:
        """Set up the menu bar."""
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("&File")

        new_action = QAction("&New", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._on_new)
        file_menu.addAction(new_action)

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._on_open)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._on_save_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        rename_action = QAction("Re&name Design...", self)
        rename_action.triggered.connect(self._on_rename)
        file_menu.addAction(rename_action)

        clone_action = QAction("&Clone Design", self)
        clone_action.triggered.connect(self._on_clone)
        file_menu.addAction(clone_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menu_bar.addMenu("&Tools")

        primitive_editor_action = QAction("&Primitive Editor...", self)
        primitive_editor_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        primitive_editor_action.triggered.connect(self._on_primitive_editor)
        tools_menu.addAction(primitive_editor_action)

        properties_action = QAction("&Properties Panel", self)
        properties_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        properties_action.setCheckable(True)
        properties_action.setChecked(True)
        def _toggle_properties_dock(checked: bool) -> None:
            if checked:
                self._property_dock.show()
                self._property_dock.raise_()
            else:
                self._property_dock.hide()
        properties_action.triggered.connect(_toggle_properties_dock)
        self._property_dock.visibilityChanged.connect(properties_action.setChecked)
        tools_menu.addAction(properties_action)

        export_vhdl_action = QAction("&Export VHDL…", self)
        export_vhdl_action.setShortcut(QKeySequence("Ctrl+Shift+V"))
        export_vhdl_action.setCheckable(True)
        def _toggle_vhdl_dock(checked: bool) -> None:
            if checked:
                self._vhdl_dock.show()
                self._vhdl_dock.raise_()
            else:
                self._vhdl_dock.hide()
        export_vhdl_action.triggered.connect(_toggle_vhdl_dock)
        self._vhdl_dock.visibilityChanged.connect(export_vhdl_action.setChecked)
        tools_menu.addAction(export_vhdl_action)

        toggle_sim_action = QAction("&Show Simulation Panel", self)
        toggle_sim_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        toggle_sim_action.setCheckable(True)
        toggle_sim_action.setChecked(True)
        toggle_sim_action.triggered.connect(lambda checked: self._sim_dock.setVisible(checked))
        self._sim_dock.visibilityChanged.connect(lambda vis: toggle_sim_action.setChecked(vis))
        tools_menu.addAction(toggle_sim_action)

        view_menu = menu_bar.addMenu("&View")

        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self._scene.refresh_view)
        view_menu.addAction(refresh_action)

        view_menu.addSeparator()

        fit_action = QAction("&Fit to Content", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(self._view.fit_to_content)
        view_menu.addAction(fit_action)

        reset_zoom_action = QAction("&Reset Zoom", self)
        reset_zoom_action.setShortcut(QKeySequence("Ctrl+1"))
        reset_zoom_action.triggered.connect(self._view.reset_zoom)
        view_menu.addAction(reset_zoom_action)

    def _setup_status_bar(self) -> None:
        """Set up the status bar."""
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    def _load_library(self) -> None:
        """Load the component library."""
        library_path = self._config.library_path
        if library_path is None:
            library_path = AppConfig.get_default_library_path()

        self._library_loader = LibraryLoader(library_path)
        self._library_loader.load_all()

        components = self._library_loader.get_all_components()
        self._palette.set_components(components)

        # Set library on scene with loader for composite component support
        self._library_dict = {c.name: c for c in components}
        self._scene.set_library(self._library_dict, self._library_loader)
        self._sim_panel.set_library(self._library_dict)

        self._status_bar.showMessage(f"Loaded {len(components)} components")

    def _update_title(self) -> None:
        """Update the window title and property panel header."""
        title = self._config.window.title
        design = self._scene.get_design()
        design_name = design.name

        if self._current_file:
            title = f"{self._current_file.name} - {title}"
        else:
            title = f"{design_name} - {title}"

        self.setWindowTitle(title)
        self._property_editor.set_design_name(design_name)
        self._property_editor.set_design(design)

    def _on_new(self) -> None:
        """Handle new design action."""
        self._scene.new_design()
        self._current_file = None
        self._sim_panel.refresh_ports()
        self._update_title()
        self._status_bar.showMessage("New design created")

    def _on_open(self) -> None:
        """Handle open design action."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Design",
            str(Path.cwd()),
            "Pipeline Design (*.json);;All Files (*)",
        )

        if file_path:
            self._load_from_file(Path(file_path))

    def _load_from_file(self, path: Path) -> None:
        """Load a design from a file."""
        try:
            json_str = path.read_text()
            design = Design.model_validate_json(json_str)

            self._scene.set_design(design)
            self._sim_panel.refresh_ports()

            self._current_file = path
            self._config.add_recent_file(path)
            self._update_title()
            self._status_bar.showMessage(
                f"Loaded {path.name}: {len(design.components)} components, "
                f"{len(design.stages)} stages"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load: {e}")

    def _on_save(self) -> None:
        """Handle save design action."""
        if self._current_file:
            self._save_to_file(self._current_file)
        else:
            self._on_save_as()

    def _on_save_as(self) -> None:
        """Handle save as action."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Design",
            str(Path.home()),
            "Pipeline Design (*.json);;All Files (*)",
        )

        if file_path:
            self._save_to_file(Path(file_path))

    def _save_to_file(self, path: Path) -> None:
        """Save the design to a file."""
        try:
            design = self._scene.get_design()
            json_str = design.model_dump_json(indent=2)
            path.write_text(json_str)

            self._current_file = path
            self._config.add_recent_file(path)
            self._update_title()
            self._status_bar.showMessage(f"Saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save: {e}")

    def _on_rename(self) -> None:
        """Rename the current design."""
        design = self._scene.get_design()
        new_name, ok = QInputDialog.getText(
            self, "Rename Design", "Design name:", text=design.name
        )
        if ok and new_name.strip():
            design.name = new_name.strip()
            self._update_title()
            self._status_bar.showMessage(f"Design renamed to '{design.name}'")

    def _on_clone(self) -> None:
        """Clone the current design with an auto-generated unique name."""
        design = self._scene.get_design()
        existing_names = {c.name for c in self._library_loader.get_all_components()}
        existing_names.add(design.name)

        base = design.name
        candidate = f"{base} Copy"
        counter = 2
        while candidate in existing_names:
            candidate = f"{base} Copy {counter}"
            counter += 1

        json_str = design.model_dump_json()
        clone = Design.model_validate_json(json_str)
        clone.name = candidate

        self._scene.set_design(clone)
        self._current_file = None
        self._update_title()
        self._status_bar.showMessage(f"Cloned design as '{candidate}'")

    def _on_zoom_changed(self, zoom: float) -> None:
        """Handle zoom level changes."""
        self._status_bar.showMessage(f"Zoom: {zoom * 100:.0f}%")

    def _on_validation_warnings(self, warnings: list) -> None:
        """Show signal-class mismatch warnings in the status bar."""
        if not warnings:
            return
        count = len(warnings)
        summary = warnings[0] if count == 1 else f"{count} invalid connections"
        self._status_bar.showMessage(f"⚠ {summary}", 8000)

    def _on_selection_changed(self) -> None:
        """Handle scene selection changes."""
        selected = self._scene.selectedItems()

        # Clear if nothing or multiple items selected
        if len(selected) != 1:
            self._property_editor.clear()
            return

        item = selected[0]

        if isinstance(item, InterfacePortItem):
            # Interface port selected - show interface port properties
            interface_port = item.get_interface_port()
            self._property_editor.set_interface_port(interface_port)
        elif isinstance(item, PortItem):
            # Port selected - show port properties
            port = item.get_port()
            component_id = item.get_component_id()
            component_name = ""
            generic_values: dict = {}
            if component_id:
                component_name = self._get_component_display_name(component_id)
                comp_item = self._scene.get_component_item(component_id)
                if comp_item:
                    instance = comp_item.get_instance()
                    definition = comp_item.get_definition()
                    # Seed with definition defaults so symbolic width/lsb expressions
                    # can be resolved even when the user hasn't overridden them yet.
                    if definition:
                        for gen in definition.generics:
                            if gen.default_value is not None:
                                generic_values[gen.name] = gen.default_value
                    # Instance overrides take precedence
                    generic_values.update(instance.generic_values)
                    # Resolve string-valued generics (e.g. "LSB", "WIDTH+2") using
                    # the outer design's concrete defaults so notation() can evaluate
                    # to a concrete S/U format string.
                    from pipeline_designer.domain.models.behavior import (
                        _eval_index, _substitute_generics,
                    )
                    outer_concrete = {
                        g.name: g.default_value
                        for g in self._scene.get_design().component_config.generics
                        if g.default_value is not None
                        and isinstance(g.default_value, (int, float))
                        and not isinstance(g.default_value, bool)
                    }
                    for gname, gval in list(generic_values.items()):
                        if isinstance(gval, str):
                            try:
                                substituted = _substitute_generics(gval, outer_concrete)
                                generic_values[gname] = _eval_index(substituted, outer_concrete)
                            except (ValueError, KeyError):
                                pass
            self._property_editor.set_port(port, component_id, component_name, generic_values)
        elif isinstance(item, ComponentItem):
            instance = item.get_instance()
            definition = item.get_definition()
            self._property_editor.set_component(instance, definition)
        elif isinstance(item, ConnectionItem):
            connection = item.get_connection()
            # Get display names for source and target components
            source_name = self._get_component_display_name(connection.source.component_id)
            target_name = self._get_component_display_name(connection.target.component_id)
            self._property_editor.set_connection(connection, source_name, target_name)
        else:
            self._property_editor.clear()

    def _get_component_display_name(self, component_id) -> str:
        """Get display name for a component by ID."""
        item = self._scene.get_component_item(component_id)
        if item:
            return item.get_instance().get_display_name()
        return str(component_id)[:8]

    def _on_port_changed(
        self, component_id, port_name: str, property_name: str, new_value
    ) -> None:
        """Handle port property changes from the property editor.

        Args:
            component_id: UUID of the component containing the port.
            port_name: Name of the port (the old name if name was changed).
            property_name: Name of the property that changed ('name' or 'data_type').
            new_value: The new value of the property.
        """
        if component_id is None:
            return

        # Get the component item
        comp_item = self._scene.get_component_item(component_id)
        if comp_item is None:
            return

        # Find the port item by port_name (which is the dictionary key)
        # Note: when name changes, port_name is the OLD name (still the dict key)
        port_item = comp_item._port_items.get(port_name)

        if port_item:
            # Update the tooltip to reflect the new property value
            port_item.update_tooltip()

            # If name changed, update the dictionary key
            if property_name == "name" and new_value != port_name:
                del comp_item._port_items[port_name]
                comp_item._port_items[new_value] = port_item

            # If signal_class changed, write it onto the authoritative port object,
            # persist it in the instance override dict, and re-validate connections.
            if property_name == "signal_class":
                port_item.get_port().signal_class = new_value
                comp_item.get_instance().port_signal_classes[port_name] = new_value.value
                port_item.refresh_appearance()
                self._scene.revalidate_connections()

    def _on_interface_port_changed(
        self, port_id, property_name: str, new_value
    ) -> None:
        """Handle interface port property changes from the property editor.

        Args:
            port_id: UUID of the interface port.
            property_name: Name of the property that changed ('name' or 'data_type').
            new_value: The new value of the property.
        """
        if port_id is None:
            return

        self._sim_panel.mark_dirty()

        port_item = self._scene.get_interface_port_item(port_id)
        if port_item:
            if property_name == "signal_class":
                # Write value onto the authoritative model object, refresh colour,
                # and re-validate all connections for mismatches.
                port_item.get_interface_port().signal_class = new_value
                port_item._update_appearance()
                self._scene.revalidate_connections()
            else:
                port_item.update()

    def _on_primitive_editor(self) -> None:
        """Open (or raise) the primitive editor window."""
        if self._primitive_editor is None:
            from pipeline_designer.presentation.primitive_editor import PrimitiveEditorWindow

            library_path = self._config.library_path or AppConfig.get_default_library_path()
            self._primitive_editor = PrimitiveEditorWindow(
                self._library_loader,
                library_path,
                parent=None,
            )
            self._primitive_editor.primitives_changed.connect(self._on_primitives_changed)

        self._primitive_editor.show()
        self._primitive_editor.raise_()
        self._primitive_editor.activateWindow()

    def _on_primitives_changed(self) -> None:
        """Reload the library after primitives are created, edited, or deleted."""
        self._load_library()
        # set_library is called inside _load_library(); mark ports dirty too
        self._sim_panel.mark_dirty()
        self._status_bar.showMessage("Library reloaded")

    def get_scene(self) -> DesignScene:
        """Get the design scene."""
        return self._scene

    def get_view(self) -> DesignView:
        """Get the design view."""
        return self._view
