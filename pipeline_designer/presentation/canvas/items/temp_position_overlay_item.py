"""Temporary position overlay item for the design canvas.

Used to visualize provisional positions during alignment previews and after
stage-shift commits (while the user has not yet formally accepted them).
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QStyleOptionGraphicsItem,
    QWidget,
)


class TempPositionOverlayItem(QGraphicsRectItem):
    """Orange dashed border overlay that marks a temporary/provisional position.

    Painted above all other items (Z = 100).  No fill – just the border and an
    optional text label so the user knows the position has not been confirmed.
    """

    TEMP_COLOR = QColor("#ff8800")
    INVALID_COLOR = QColor("#ff4444")
    LABEL_MARGIN = 4.0

    def __init__(
        self,
        rect: QRectF | None = None,
        label: str = "Temporary",
        invalid: bool = False,
        parent: QGraphicsItem | None = None,
    ) -> None:
        """Initialise the overlay.

        Args:
            rect:    Scene-coordinate bounding rect to draw around (optional).
            label:   Short text shown near the top edge of the rect.
            invalid: When True use the red invalid colour instead of orange.
            parent:  Parent graphics item (usually None → scene-level).
        """
        super().__init__(parent)

        self._label = label
        self._invalid = invalid

        self.setZValue(100)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self._apply_pen()
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

        if rect is not None:
            self.setRect(rect)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_invalid(self, invalid: bool) -> None:
        """Switch between the orange (temp) and red (invalid) colour scheme."""
        self._invalid = invalid
        self._apply_pen()
        self.update()

    def set_label(self, label: str) -> None:
        """Update the label text."""
        self._label = label
        self.update()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_pen(self) -> None:
        color = self.INVALID_COLOR if self._invalid else self.TEMP_COLOR
        pen = QPen(color)
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

    def paint(
        self,
        painter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the dashed border and optional label."""
        painter.setRenderHint(painter.RenderHint.Antialiasing, False)

        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(self.rect())

        if self._label:
            color = self.INVALID_COLOR if self._invalid else self.TEMP_COLOR
            painter.setPen(QPen(color))
            font = QFont("Arial", 7)
            painter.setFont(font)
            label_rect = QRectF(
                self.rect().x() + self.LABEL_MARGIN,
                self.rect().y() + self.LABEL_MARGIN,
                self.rect().width() - 2 * self.LABEL_MARGIN,
                16.0,
            )
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                self._label,
            )
