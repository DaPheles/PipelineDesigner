"""Design canvas view with pan and zoom support."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter
from PySide6.QtWidgets import QGraphicsView

from pipeline_designer.presentation.panels.component_palette import (
    is_input_port_item,
    is_output_port_item,
    is_port_item,
)

from .items import ComponentItem, ConnectionItem, InterfacePortItem
from .scene import DesignScene


class DesignView(QGraphicsView):
    """Graphics view for the design canvas with pan and zoom."""

    zoom_changed = Signal(float)

    def __init__(self, scene: DesignScene | None = None, parent=None):
        """Initialize the design view.

        Args:
            scene: The design scene to display.
            parent: Parent widget.
        """
        super().__init__(parent)

        if scene is None:
            scene = DesignScene()
        self.setScene(scene)

        self._zoom_factor = 1.0
        self._min_zoom = 0.1
        self._max_zoom = 10.0
        self._is_panning = False
        self._pan_start_x = 0
        self._pan_start_y = 0

        self._setup_view()

    def _setup_view(self) -> None:
        """Configure view settings."""
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        """Handle mouse wheel for zooming."""
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor

        if event.angleDelta().y() > 0:
            zoom_factor = zoom_in_factor
        else:
            zoom_factor = zoom_out_factor

        new_zoom = self._zoom_factor * zoom_factor

        if self._min_zoom <= new_zoom <= self._max_zoom:
            self._zoom_factor = new_zoom
            self.scale(zoom_factor, zoom_factor)
            self.zoom_changed.emit(self._zoom_factor)

    def mousePressEvent(self, event) -> None:
        """Handle mouse press for panning."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._pan_start_x = event.x()
            self._pan_start_y = event.y()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for panning."""
        if self._is_panning:
            dx = event.x() - self._pan_start_x
            dy = event.y() - self._pan_start_y
            self._pan_start_x = event.x()
            self._pan_start_y = event.y()

            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - dx
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - dy
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Handle mouse release for panning."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle key press events for deletion and undo/redo."""
        if event.key() == Qt.Key.Key_Delete:
            self._delete_selected_items()
            event.accept()
        elif event.key() == Qt.Key.Key_Z and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._undo()
            event.accept()
        elif event.key() == Qt.Key.Key_Y and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _undo(self) -> None:
        """Undo the last action."""
        scene = self.scene()
        if isinstance(scene, DesignScene):
            scene.undo()

    def _redo(self) -> None:
        """Redo the last undone action."""
        scene = self.scene()
        if isinstance(scene, DesignScene):
            scene.redo()

    def _delete_selected_items(self) -> None:
        """Delete all selected items from the scene."""
        scene = self.scene()
        if not isinstance(scene, DesignScene):
            return

        selected_items = scene.selectedItems()
        if not selected_items:
            return

        # Collect IDs to delete (components first, then connections, then interface ports)
        component_ids = []
        connection_ids = []
        interface_port_ids = []

        for item in selected_items:
            if isinstance(item, ComponentItem):
                component_ids.append(item.get_instance().id)
            elif isinstance(item, ConnectionItem):
                connection_ids.append(item.get_connection().id)
            elif isinstance(item, InterfacePortItem):
                interface_port_ids.append(item.get_port_id())

        # Delete components (this also removes their connections)
        for comp_id in component_ids:
            scene.remove_component(comp_id)

        # Delete remaining selected connections
        for conn_id in connection_ids:
            scene.remove_connection(conn_id)

        # Delete interface ports
        for port_id in interface_port_ids:
            scene.remove_interface_port(port_id)

    def fit_to_content(self) -> None:
        """Fit the view to show all content."""
        self.fitInView(self.scene().itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_factor = self.transform().m11()
        self.zoom_changed.emit(self._zoom_factor)

    def reset_zoom(self) -> None:
        """Reset zoom to 100%."""
        self.resetTransform()
        self._zoom_factor = 1.0
        self.zoom_changed.emit(self._zoom_factor)

    def get_zoom_factor(self) -> float:
        """Get the current zoom factor."""
        return self._zoom_factor

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter for component drops."""
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        """Handle drag move — show port placement preview when dragging a port."""
        if event.mimeData().hasText():
            item_data = event.mimeData().text()
            scene = self.scene()
            if isinstance(scene, DesignScene) and is_port_item(item_data):
                scene_pos = self.mapToScene(event.position().toPoint())
                scene.show_interface_port_preview(
                    scene_pos.x(), scene_pos.y(), is_input_port_item(item_data)
                )
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        """Clear port preview when drag leaves the view."""
        scene = self.scene()
        if isinstance(scene, DesignScene):
            scene.clear_interface_port_preview()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        """Handle drop for component or port creation."""
        if event.mimeData().hasText():
            item_data = event.mimeData().text()
            scene_pos = self.mapToScene(event.position().toPoint())
            scene = self.scene()
            if isinstance(scene, DesignScene):
                scene.clear_interface_port_preview()
                if is_port_item(item_data):
                    # Handle port drops - must be on the correct stage
                    is_input = is_input_port_item(item_data)
                    scene.add_interface_port_at(scene_pos.x(), scene_pos.y(), is_input)
                else:
                    # Handle regular component drops
                    scene.add_component_at(item_data, scene_pos.x(), scene_pos.y())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
