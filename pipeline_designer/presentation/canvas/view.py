"""Design canvas view with pan and zoom support."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

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
        """Handle drag move for component drops."""
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """Handle drop for component creation."""
        if event.mimeData().hasText():
            component_name = event.mimeData().text()
            scene_pos = self.mapToScene(event.position().toPoint())
            scene = self.scene()
            if isinstance(scene, DesignScene):
                scene.add_component_at(component_name, scene_pos.x(), scene_pos.y())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
