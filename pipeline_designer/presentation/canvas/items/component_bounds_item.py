"""Component bounds item for visualizing the design extent."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain import DEFAULT_GRID, GridConfig


class ComponentBoundsItem(QGraphicsRectItem):
    """Graphics item showing the bounding rectangle of the component.

    This rectangle extends from the input stage to the output stage
    horizontally, and encompasses all components vertically with padding.
    """

    CORNER_RADIUS = 12.0
    PADDING_GRID_UNITS = 5  # Padding in grid units

    def __init__(
        self,
        grid: GridConfig | None = None,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize the component bounds item.

        Args:
            grid: Grid configuration.
            parent: Parent graphics item.
        """
        super().__init__(parent)

        self._grid = grid or DEFAULT_GRID
        self._padding = self._grid.to_pixels(self.PADDING_GRID_UNITS)

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item settings."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setZValue(-10)  # Behind everything

        self._update_appearance()

    def _update_appearance(self) -> None:
        """Update the visual appearance."""
        pen = QPen(QColor("#4a4a4a"))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

        # Semi-transparent background
        brush_color = QColor("#1a1a2e")
        brush_color.setAlpha(100)
        self.setBrush(QBrush(brush_color))

    def update_bounds(
        self,
        left_x: float,
        right_x: float,
        top_y: float,
        bottom_y: float,
    ) -> None:
        """Update the bounds rectangle.

        Args:
            left_x: Left boundary (input stage x position).
            right_x: Right boundary (output stage x position + width).
            top_y: Top boundary (minimum y of all components - padding).
            bottom_y: Bottom boundary (maximum y of all components + padding).
        """
        # Add padding to top and bottom
        padded_top = top_y - self._padding
        padded_bottom = bottom_y + self._padding

        rect = QRectF(
            left_x,
            padded_top,
            right_x - left_x,
            padded_bottom - padded_top,
        )
        self.setRect(rect)

    def update_from_components(
        self,
        input_stage_x: float,
        output_stage_x: float,
        output_stage_width: float,
        component_rects: list[QRectF],
    ) -> tuple[float, float]:
        """Update bounds from component positions.

        Args:
            input_stage_x: X position of input stage.
            output_stage_x: X position of output stage.
            output_stage_width: Width of output stage.
            component_rects: List of component bounding rectangles.

        Returns:
            Tuple of (top_y, bottom_y) for the bounds.
        """
        if not component_rects:
            # Default bounds if no components - reasonable vertical span
            top_y = -50
            bottom_y = 150
        else:
            top_y = min(r.top() for r in component_rects)
            bottom_y = max(r.bottom() for r in component_rects)

        self.update_bounds(
            input_stage_x,
            output_stage_x + output_stage_width,
            top_y,
            bottom_y,
        )

        return (top_y - self._padding, bottom_y + self._padding)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the bounds rectangle."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        pen = self.pen()
        brush = self.brush()

        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        # Draw corner markers
        marker_size = 10
        marker_pen = QPen(QColor("#5a5a6a"))
        marker_pen.setWidth(2)
        painter.setPen(marker_pen)

        # Top-left corner
        painter.drawLine(
            int(rect.left()), int(rect.top() + marker_size),
            int(rect.left()), int(rect.top()),
        )
        painter.drawLine(
            int(rect.left()), int(rect.top()),
            int(rect.left() + marker_size), int(rect.top()),
        )

        # Top-right corner
        painter.drawLine(
            int(rect.right()), int(rect.top() + marker_size),
            int(rect.right()), int(rect.top()),
        )
        painter.drawLine(
            int(rect.right()), int(rect.top()),
            int(rect.right() - marker_size), int(rect.top()),
        )

        # Bottom-left corner
        painter.drawLine(
            int(rect.left()), int(rect.bottom() - marker_size),
            int(rect.left()), int(rect.bottom()),
        )
        painter.drawLine(
            int(rect.left()), int(rect.bottom()),
            int(rect.left() + marker_size), int(rect.bottom()),
        )

        # Bottom-right corner
        painter.drawLine(
            int(rect.right()), int(rect.bottom() - marker_size),
            int(rect.right()), int(rect.bottom()),
        )
        painter.drawLine(
            int(rect.right()), int(rect.bottom()),
            int(rect.right() - marker_size), int(rect.bottom()),
        )
