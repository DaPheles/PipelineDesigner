"""Stage graphics item for the design canvas."""

from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
    QGraphicsSceneHoverEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain import DEFAULT_GRID, GridConfig
from pipeline_designer.domain.models import Stage


class StageItem(QGraphicsRectItem):
    """Graphics item representing a pipeline stage.

    Stages are visualized as full-height vertical bands that span
    the entire visible area of the canvas.

    Clicking on the stage band initiates a group-move of all registers
    that belong to it.  The scene wires up the ``on_stage_click`` callback
    after creating this item.

    Visual states:
    - Normal:       semi-transparent band with dashed border
    - Being moved:  slightly more opaque, SizeAll cursor
    - Invalid:      red dashed outer border (overlapping another stage)
    """

    STAGE_COLORS = [
        "#3d5a80",  # Stage 1 - Blue
        "#5e6472",  # Stage 2 - Gray-blue
        "#4a6670",  # Stage 3 - Teal-gray
        "#556b7a",  # Stage 4 - Steel blue
        "#4d6066",  # Stage 5 - Dark gray
    ]
    LABEL_HEIGHT = 25.0
    STAGE_ALPHA = 40        # Normal transparency (0-255)
    MOVING_ALPHA = 80       # While being dragged
    INVALID_BORDER_COLOR = QColor("#ff4444")

    def __init__(
        self,
        stage: Stage,
        view_height: float = 10000.0,
        grid: GridConfig | None = None,
        parent: QGraphicsItem | None = None,
    ):
        """Initialise the stage item.

        Args:
            stage:       The stage model (positions in grid units).
            view_height: Height of the stage band (should span view).
            grid:        Grid configuration for unit conversion.
            parent:      Parent graphics item.
        """
        super().__init__(parent)

        self._stage = stage
        self._view_height = view_height
        self._grid = grid or DEFAULT_GRID

        # Visual states
        self._is_invalid: bool = False
        self._is_being_moved: bool = False

        # Callback – scene registers this after creating the item.
        # Signature: (stage: Stage, scene_pos: QPointF) -> None
        self.on_stage_click: Callable | None = None

        self._setup_item()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptHoverEvents(True)
        self.setZValue(-10)  # Behind components

        self._update_geometry()
        self._update_appearance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_stage(self) -> Stage:
        """Return the stage model."""
        return self._stage

    def update_stage(self, stage: Stage) -> None:
        """Replace the stage model and refresh geometry/appearance."""
        self._stage = stage
        self._update_geometry()
        self._update_appearance()

    def set_view_height(self, height: float) -> None:
        """Update the view height (full-scene spanning mode)."""
        self._view_height = height
        self._update_geometry()

    def set_bounds(self, top_y: float, bottom_y: float) -> None:
        """Set the vertical bounds to match component bounds.

        Args:
            top_y:    Top boundary in pixels (includes padding).
            bottom_y: Bottom boundary in pixels (includes padding).
        """
        height = bottom_y - top_y
        self._view_height = height
        width_px = self._grid.to_pixels(self._stage.width)
        self.setRect(0, 0, width_px, height)
        x_px = self._grid.to_pixels(self._stage.x_position)
        self.setPos(x_px, top_y)

    def set_invalid_overlap(self, is_invalid: bool) -> None:
        """Toggle the red-dashed invalid-overlap border.

        Args:
            is_invalid: True to show the invalid indicator, False to clear it.
        """
        if self._is_invalid == is_invalid:
            return
        self._is_invalid = is_invalid
        self._update_appearance()
        self.update()

    def set_being_moved(self, is_moving: bool) -> None:
        """Toggle the 'being moved' visual state.

        Args:
            is_moving: True while the stage group-move is active.
        """
        if self._is_being_moved == is_moving:
            return
        self._is_being_moved = is_moving
        self._update_appearance()
        if is_moving:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()
        self.update()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_geometry(self) -> None:
        """Recompute the stage rectangle from the model."""
        half_height = self._view_height / 2
        width_px = self._grid.to_pixels(self._stage.width)
        self.setRect(0, -half_height, width_px, self._view_height)
        x_px = self._grid.to_pixels(self._stage.x_position)
        self.setPos(x_px, 0)

    def _update_appearance(self) -> None:
        """Rebuild pen/brush based on current visual state."""
        color_index = (self._stage.index - 1) % len(self.STAGE_COLORS)
        base_color = QColor(self.STAGE_COLORS[color_index])

        alpha = self.MOVING_ALPHA if self._is_being_moved else self.STAGE_ALPHA
        base_color.setAlpha(alpha)
        self.setBrush(QBrush(base_color))

        if self._is_invalid:
            pen = QPen(self.INVALID_BORDER_COLOR)
            pen.setWidth(3)
            pen.setStyle(Qt.PenStyle.DashLine)
        else:
            pen = QPen(base_color.darker(150))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)

        self.setPen(pen)

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        """Show move cursor when hovering over the stage band."""
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        """Restore default cursor when leaving the stage band."""
        if not self._is_being_moved:
            self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Initiate stage group-move by calling the scene callback."""
        if event.button() == Qt.MouseButton.LeftButton and self.on_stage_click:
            self.on_stage_click(self._stage, event.scenePos())
            event.accept()
        else:
            super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        """Paint the stage band and label."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()

        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(rect)

        # Stage label background
        label_rect = QRectF(rect.x(), rect.y(), rect.width(), self.LABEL_HEIGHT)

        color_index = (self._stage.index - 1) % len(self.STAGE_COLORS)
        label_color = QColor(self.STAGE_COLORS[color_index])
        label_color.setAlpha(120)
        painter.setBrush(QBrush(label_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(label_rect)

        # Stage number text
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"Stage {self._stage.index}",
        )

        # Extra visual hint while moving: "MOVING" label
        if self._is_being_moved:
            hint_color = QColor("#ffffff")
            hint_color.setAlpha(160)
            painter.setPen(QPen(hint_color))
            font2 = QFont("Arial", 8)
            painter.setFont(font2)
            painter.drawText(
                QRectF(rect.x(), rect.y() + self.LABEL_HEIGHT + 2, rect.width(), 16),
                Qt.AlignmentFlag.AlignCenter,
                "MOVING",
            )
