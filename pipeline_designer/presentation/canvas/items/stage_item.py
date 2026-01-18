"""Stage graphics item for the design canvas."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain.models import Stage


class StageItem(QGraphicsRectItem):
    """Graphics item representing a pipeline stage.

    Stages are visualized as full-height vertical bands that span
    the entire visible area of the canvas.
    """

    STAGE_COLORS = [
        "#3d5a80",  # Stage 1 - Blue
        "#5e6472",  # Stage 2 - Gray-blue
        "#4a6670",  # Stage 3 - Teal-gray
        "#556b7a",  # Stage 4 - Steel blue
        "#4d6066",  # Stage 5 - Dark gray
    ]
    LABEL_HEIGHT = 24.0
    STAGE_ALPHA = 40  # Transparency (0-255)

    def __init__(
        self,
        stage: Stage,
        view_height: float = 10000.0,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the stage item.

        Args:
            stage: The stage model.
            view_height: Height of the stage band (should span view).
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._stage = stage
        self._view_height = view_height

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setZValue(-10)  # Behind components

        self._update_geometry()
        self._update_appearance()

    def _update_geometry(self) -> None:
        """Update the stage rectangle geometry."""
        half_height = self._view_height / 2
        self.setRect(0, -half_height, self._stage.width, self._view_height)
        self.setPos(self._stage.x_position, 0)

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
        color_index = (self._stage.index - 1) % len(self.STAGE_COLORS)
        base_color = QColor(self.STAGE_COLORS[color_index])
        base_color.setAlpha(self.STAGE_ALPHA)

        self.setBrush(QBrush(base_color))

        pen = QPen(base_color.darker(150))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

    def get_stage(self) -> Stage:
        """Get the stage model."""
        return self._stage

    def update_stage(self, stage: Stage) -> None:
        """Update with new stage data."""
        self._stage = stage
        self._update_geometry()
        self._update_appearance()

    def set_view_height(self, height: float) -> None:
        """Update the view height."""
        self._view_height = height
        self._update_geometry()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the stage item."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()

        # Draw the stage background
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(rect)

        # Draw stage label at the top
        label_rect = QRectF(
            rect.x(),
            rect.y(),
            rect.width(),
            self.LABEL_HEIGHT,
        )

        color_index = (self._stage.index - 1) % len(self.STAGE_COLORS)
        label_color = QColor(self.STAGE_COLORS[color_index])
        label_color.setAlpha(120)
        painter.setBrush(QBrush(label_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(label_rect)

        # Draw stage number
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"Stage {self._stage.index}",
        )
