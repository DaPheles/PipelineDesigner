"""Composite view item for displaying internal structure of composite components."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pipeline_designer.domain import GridConfig
from pipeline_designer.domain.models import (
    ComponentDefinition,
    ComponentInstance,
    Connection,
    Design,
    PortDirection,
    Stage,
)


class InternalComponentItem(QGraphicsRectItem):
    """Read-only visualization of an internal component."""

    CORNER_RADIUS = 4.0
    HEADER_HEIGHT = 10.0

    def __init__(
        self,
        instance: ComponentInstance,
        definition: ComponentDefinition | None,
        grid: GridConfig,
        parent: QGraphicsItem | None = None,
    ):
        super().__init__(parent)
        self._instance = instance
        self._definition = definition
        self._grid = grid
        self._is_register = instance.definition_ref == "Register"

        # Make non-interactive but ensure visibility
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(2)  # Above parent background

        self._setup_item()

    def _setup_item(self) -> None:
        """Configure item appearance."""
        width_px = self._grid.to_pixels(4)
        height_px = self._grid.to_pixels(3)
        color = "#4a90d9"

        if self._definition:
            width_px, height_px = self._definition.visual.get_pixel_size(self._grid)
            color = self._definition.visual.color

        self.setRect(0, 0, width_px, height_px)
        self._base_color = QColor(color)

        pen = QPen(self._base_color.darker(150))
        pen.setWidthF(1.5)
        self.setPen(pen)
        self.setBrush(QBrush(self._base_color))

    def get_port_position(self, port_name: str) -> QPointF | None:
        """Get the position of a port in local coordinates."""
        if not self._definition:
            return None

        for port in self._definition.ports:
            if port.name == port_name:
                if port.position:
                    x_px = self._grid.to_pixels(port.position[0])
                    y_px = self._grid.to_pixels(port.position[1])
                    return QPointF(x_px, y_px)
                else:
                    # Auto-position based on direction
                    rect = self.rect()
                    if port.direction == PortDirection.OUT:
                        return QPointF(rect.width(), rect.height() / 2)
                    else:
                        return QPointF(0, rect.height() / 2)
        return None

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the internal component."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        radius = self.CORNER_RADIUS

        # Draw body
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRoundedRect(rect, radius, radius)

        # Draw header
        header_height = self.HEADER_HEIGHT
        header_rect = QRectF(rect.x(), rect.y(), rect.width(), header_height)
        painter.setBrush(QBrush(self._base_color.darker(120)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            header_rect.x(), header_rect.y(),
            header_rect.width(), header_rect.height() + radius,
            radius, radius
        )
        painter.drawRect(
            header_rect.x(), header_rect.y() + radius,
            header_rect.width(), header_rect.height() - radius
        )

        # Draw name
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Arial", 8)
        painter.setFont(font)
        name = self._definition.name if self._definition else self._instance.definition_ref
        painter.drawText(header_rect, Qt.AlignmentFlag.AlignCenter, name)


class InternalStageItem(QGraphicsRectItem):
    """Read-only visualization of an internal pipeline stage."""

    def __init__(
        self,
        stage: Stage,
        height: float,
        parent: QGraphicsItem | None = None,
    ):
        super().__init__(parent)
        self._stage = stage

        # Make non-interactive
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1)  # Behind components but visible

        self.setRect(0, 0, stage.width, height)

        # Semi-transparent blue, matching the main pipeline stage appearance
        color = QColor("#4a90d9")
        color.setAlpha(40)
        self.setBrush(QBrush(color))

        pen = QPen(QColor("#4a90d9"))
        pen.setWidthF(1.5)
        self.setPen(pen)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the internal stage."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(rect)

        # Draw stage label
        painter.setPen(QPen(QColor("#4a90d9").darker(120)))
        font = QFont("Arial", 7)
        painter.setFont(font)
        label = f"S{self._stage.index}"
        painter.drawText(rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, label)


class InternalConnectionItem(QGraphicsPathItem):
    """Read-only visualization of an internal connection."""

    def __init__(
        self,
        start_pos: QPointF,
        end_pos: QPointF,
        parent: QGraphicsItem | None = None,
    ):
        super().__init__(parent)
        self._start_pos = start_pos
        self._end_pos = end_pos

        # Make non-interactive
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1.5)  # Between stages and components

        self._setup_path()

    def _setup_path(self) -> None:
        """Create the connection path."""
        path = QPainterPath()
        path.moveTo(self._start_pos)

        # Simple bezier curve
        dx = self._end_pos.x() - self._start_pos.x()
        ctrl_offset = abs(dx) * 0.4

        ctrl1 = QPointF(self._start_pos.x() + ctrl_offset, self._start_pos.y())
        ctrl2 = QPointF(self._end_pos.x() - ctrl_offset, self._end_pos.y())
        path.cubicTo(ctrl1, ctrl2, self._end_pos)

        self.setPath(path)

        pen = QPen(QColor("#3498db"))
        pen.setWidthF(2.0)
        self.setPen(pen)


class CompositeViewItem(QGraphicsRectItem):
    """Container for visualizing composite component internals.

    Creates read-only child items for all internal components, stages,
    and connections using the original coordinate system.
    Supports horizontal stretching when main design stage spacing differs.
    The design's visual.input_stage_x defines the origin offset.
    """

    def __init__(
        self,
        design: Design,
        library: dict[str, ComponentDefinition],
        bounds: QRectF,
        grid: GridConfig,
        parent: QGraphicsItem | None = None,
    ):
        """Initialize composite view.

        Args:
            design: The composite component's internal design.
            library: Component library for looking up definitions.
            bounds: Bounding rectangle in parent coordinates.
            grid: Grid configuration.
            parent: Parent graphics item.
        """
        super().__init__(parent)
        self._design = design
        self._library = library
        self._grid = grid
        self._bounds = bounds

        # Make non-interactive
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(0.5)

        self.setRect(bounds)
        self.setPen(Qt.PenStyle.NoPen)
        self.setBrush(Qt.BrushStyle.NoBrush)

        self._internal_items: list[QGraphicsItem] = []
        self._component_items: dict[str, InternalComponentItem] = {}
        self._create_internal_visualization()

    def _create_internal_visualization(self) -> None:
        """Create child items for all internal components, stages, and connections."""
        if not self._design.components:
            return

        # Get the origin offset from the design's visual config
        # input_stage_x is the left edge of the component bounds in design grid coordinates
        origin_x = self._grid.to_pixels(self._design.visual.input_stage_x)

        # Calculate the y offset to center content vertically within this view's bounds.
        # The view itself is already positioned below the header (via setPos in ComponentItem),
        # so no additional header offset is needed here.

        # Find vertical bounds of content (positions are in grid units, convert to pixels)
        min_y = float('inf')
        max_y = float('-inf')
        for comp in self._design.components:
            # Convert position from grid units to pixels
            y = self._grid.to_pixels(comp.position[1])
            definition = self._library.get(comp.definition_ref)
            if definition:
                _, h = definition.visual.get_pixel_size(self._grid)
            else:
                h = self._grid.to_pixels(3)
            min_y = min(min_y, y)
            max_y = max(max_y, y + h)

        content_height = max_y - min_y if max_y > min_y else 0
        available_height = self._bounds.height()
        y_offset = (available_height - content_height) / 2 - min_y

        # Create internal stage items first (background)
        # Stage positions are in grid units, convert to pixels
        for stage in self._design.stages:
            # Convert stage position from grid units to pixels, then offset by origin
            stage_x_px = self._grid.to_pixels(stage.x_position) - origin_x
            stage_width_px = self._grid.to_pixels(stage.width)

            # Stages extend the full body height (from just below the header to the bottom)
            stretched_stage = Stage(
                id=stage.id,
                index=stage.index,
                x_position=stage_x_px,
                width=stage_width_px,
                register_ids=stage.register_ids,
            )
            item = InternalStageItem(stretched_stage, self._bounds.height(), parent=self)
            item.setPos(stage_x_px, 0)
            self._internal_items.append(item)

        # Create internal component items
        for comp in self._design.components:
            definition = self._library.get(comp.definition_ref)

            item = InternalComponentItem(comp, definition, self._grid, parent=self)

            # Convert position from grid units to pixels, offset by origin
            x = self._grid.to_pixels(comp.position[0]) - origin_x
            y = self._grid.to_pixels(comp.position[1]) + y_offset
            item.setPos(x, y)

            self._internal_items.append(item)
            self._component_items[str(comp.id)] = item

        # Create internal connection items
        for conn in self._design.connections:
            start_pos = self._get_connection_endpoint(
                conn.source.component_id,
                conn.source.port_name,
                conn.source.interface_port_id,
                is_source=True,
                origin_x=origin_x,
                y_offset=y_offset,
            )
            end_pos = self._get_connection_endpoint(
                conn.target.component_id,
                conn.target.port_name,
                conn.target.interface_port_id,
                is_source=False,
                origin_x=origin_x,
                y_offset=y_offset,
            )

            if start_pos is not None and end_pos is not None:
                conn_item = InternalConnectionItem(start_pos, end_pos, parent=self)
                self._internal_items.append(conn_item)

    def _get_connection_endpoint(
        self,
        component_id: str | None,
        port_name: str,
        interface_port_id: str | None,
        is_source: bool,
        origin_x: float,
        y_offset: float,
    ) -> QPointF | None:
        """Get the position of a connection endpoint.

        Args:
            component_id: ID of the component (if connecting to a component).
            port_name: Name of the port.
            interface_port_id: ID of the interface port (if connecting to interface).
            is_source: True if this is the source endpoint (output port).
            origin_x: X offset for coordinate transformation.
            y_offset: Y offset for coordinate transformation.

        Returns:
            Position in local coordinates, or None if not found.
        """
        if component_id:
            comp_id_str = str(component_id)
            if comp_id_str in self._component_items:
                item = self._component_items[comp_id_str]
                port_pos = item.get_port_position(port_name)
                if port_pos:
                    # Convert to parent coordinates
                    return QPointF(
                        item.pos().x() + port_pos.x(),
                        item.pos().y() + port_pos.y()
                    )
                else:
                    # Fallback: use center of left/right edge
                    rect = item.rect()
                    if is_source:
                        return QPointF(
                            item.pos().x() + rect.width(),
                            item.pos().y() + rect.height() / 2
                        )
                    else:
                        return QPointF(
                            item.pos().x(),
                            item.pos().y() + rect.height() / 2
                        )

        elif interface_port_id:
            # Find the interface port to get its position
            for iface in self._design.interface_ports:
                if str(iface.id) == str(interface_port_id):
                    # x at center of port zone; y derived from design position
                    # is_source=True → input interface (left side)
                    # is_source=False → output interface (right side)
                    zone_w = float(self._grid.size)
                    x = zone_w / 2 if is_source else self._bounds.width() - zone_w / 2
                    if iface.position:
                        y = self._grid.to_pixels(iface.position[1]) + y_offset
                    else:
                        y = self._bounds.height() / 2
                    return QPointF(x, y)

        return None

    def clear_internal_items(self) -> None:
        """Remove all internal visualization items."""
        for item in self._internal_items:
            if item.scene():
                item.scene().removeItem(item)
        self._internal_items.clear()
        self._component_items.clear()
