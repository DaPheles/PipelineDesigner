"""Design document model."""

from datetime import datetime
from uuid import UUID

from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from .component import Generic
from .instance import ComponentInstance, Connection, InterfaceDirection, InterfacePort
from .stage import Stage


class DesignMetadata(BaseModel):
    """Metadata for a design document."""

    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)
    author: str = Field(default="")
    version: str = Field(default="1.0.0")


class VisualExtent(BaseModel):
    """Visual extent of the design (component bounds).

    Represents the bounding rectangle of the design in grid units.
    When used as a component, this defines the visual size.
    """

    width: int = Field(default=20, description="Width in grid units")
    height: int = Field(default=10, description="Height in grid units")
    input_stage_x: int = Field(default=-10, description="Input stage X position in grid units")
    output_stage_x: int = Field(default=10, description="Output stage X position in grid units")


class ComponentConfig(BaseModel):
    """Configuration for using a design as a reusable component.

    When a design has this configuration, it can be loaded as a component
    and placed in other designs like a primitive.
    """

    enabled: bool = Field(default=False, description="Whether this design is a component")
    category: str = Field(default="components", description="Component category")
    description: str = Field(default="", description="Component description")
    color: str = Field(default="#9b59b6", description="Component color (hex)")
    width: int = Field(default=8, description="Visual width in grid units")
    height: int = Field(default=6, description="Visual height in grid units")
    generics: list[Generic] = Field(default_factory=list, description="VHDL generics for the exported entity")


class Design(BaseModel):
    """A complete pipeline design document.

    Includes pipeline stages that are defined by register placements.
    Stages represent vertical boundaries for each clock cycle.

    When component_config.enabled is True, this design can be used as a
    reusable component in other designs. The interface_ports define the
    external connections, and the latency equals the number of stages.
    """

    name: str = Field(default="Untitled", description="Design name")
    components: list[ComponentInstance] = Field(
        default_factory=list, description="Component instances in the design"
    )
    connections: list[Connection] = Field(
        default_factory=list, description="Connections between components"
    )
    stages: list[Stage] = Field(
        default_factory=list, description="Pipeline stages defined by registers"
    )
    interface_ports: list[InterfacePort] = Field(
        default_factory=list, description="External interface ports for component mode"
    )
    component_config: ComponentConfig = Field(
        default_factory=ComponentConfig, description="Component configuration"
    )
    visual: VisualExtent = Field(
        default_factory=VisualExtent, description="Visual extent of the design bounds"
    )
    metadata: DesignMetadata = Field(
        default_factory=DesignMetadata, description="Design metadata"
    )

    # O(1) lookup indices — not serialised, rebuilt from lists on init/mutation
    _component_index: dict[UUID, ComponentInstance] = PrivateAttr(default_factory=dict)
    _stage_id_index: dict[UUID, Stage] = PrivateAttr(default_factory=dict)
    _stage_num_index: dict[int, Stage] = PrivateAttr(default_factory=dict)
    _interface_port_index: dict[UUID, InterfacePort] = PrivateAttr(default_factory=dict)
    _connection_index: dict[UUID, Connection] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self._component_index = {c.id: c for c in self.components}
        self._stage_id_index = {s.id: s for s in self.stages}
        self._stage_num_index = {s.index: s for s in self.stages}
        self._interface_port_index = {p.id: p for p in self.interface_ports}
        self._connection_index = {c.id: c for c in self.connections}

    @property
    def latency(self) -> int:
        """Get the latency (number of pipeline stages)."""
        return len(self.stages)

    @property
    def is_component(self) -> bool:
        """Check if this design is configured as a component."""
        return self.component_config.enabled

    def get_input_interfaces(self) -> list[InterfacePort]:
        """Get all input interface ports."""
        return [p for p in self.interface_ports if p.direction == InterfaceDirection.INPUT]

    def get_output_interfaces(self) -> list[InterfacePort]:
        """Get all output interface ports."""
        return [p for p in self.interface_ports if p.direction == InterfaceDirection.OUTPUT]

    def get_component_by_id(self, component_id: UUID) -> ComponentInstance | None:
        """Get a component instance by ID."""
        return self._component_index.get(component_id)

    def add_component(self, component: ComponentInstance) -> None:
        """Add a component instance to the design."""
        self.components.append(component)
        self._component_index[component.id] = component
        self.metadata.modified_at = datetime.now()

    def remove_component(self, component_id: UUID) -> bool:
        """Remove a component instance and its connections."""
        component = self._component_index.pop(component_id, None)
        if component is None:
            return False

        self.components.remove(component)
        removed_conn_ids = {
            conn.id
            for conn in self.connections
            if conn.source.component_id == component_id
            or conn.target.component_id == component_id
        }
        self.connections = [c for c in self.connections if c.id not in removed_conn_ids]
        for cid in removed_conn_ids:
            self._connection_index.pop(cid, None)
        self.metadata.modified_at = datetime.now()
        return True

    def add_connection(self, connection: Connection) -> None:
        """Add a connection to the design."""
        self.connections.append(connection)
        self._connection_index[connection.id] = connection
        self.metadata.modified_at = datetime.now()

    def remove_connection(self, connection_id: UUID) -> bool:
        """Remove a connection from the design."""
        conn = self._connection_index.pop(connection_id, None)
        if conn is None:
            return False
        self.connections.remove(conn)
        self.metadata.modified_at = datetime.now()
        return True

    def get_stage_by_id(self, stage_id: UUID) -> Stage | None:
        """Get a stage by ID."""
        return self._stage_id_index.get(stage_id)

    def get_stage_at_x(self, x: float) -> Stage | None:
        """Get the stage at a given x position."""
        for stage in self.stages:
            if stage.contains_x(x):
                return stage
        return None

    def get_stage_by_index(self, index: int) -> Stage | None:
        """Get a stage by its index."""
        return self._stage_num_index.get(index)

    def get_registers(self) -> list[ComponentInstance]:
        """Get all register component instances."""
        return [c for c in self.components if c.definition_ref == "Register"]

    def reindex_stages(self) -> None:
        """Re-index stages left to right, starting from 1."""
        sorted_stages = sorted(self.stages, key=lambda s: s.x_position)
        for i, stage in enumerate(sorted_stages, start=1):
            stage.index = i
        self.stages = sorted_stages
        self._stage_id_index = {s.id: s for s in self.stages}
        self._stage_num_index = {s.index: s for s in self.stages}
        self.metadata.modified_at = datetime.now()

    def remove_empty_stages(self) -> list[Stage]:
        """Remove stages with no registers and return removed stages."""
        removed = [s for s in self.stages if not s.register_ids]
        self.stages = [s for s in self.stages if s.register_ids]
        if removed:
            self.reindex_stages()
        return removed

    def add_interface_port(self, port: InterfacePort) -> None:
        """Add an interface port to the design."""
        self.interface_ports.append(port)
        self._interface_port_index[port.id] = port
        self.metadata.modified_at = datetime.now()

    def remove_interface_port(self, port_id: UUID) -> bool:
        """Remove an interface port from the design."""
        port = self._interface_port_index.pop(port_id, None)
        if port is None:
            return False
        self.interface_ports.remove(port)
        self.metadata.modified_at = datetime.now()
        return True

    def get_interface_port_by_id(self, port_id: UUID) -> InterfacePort | None:
        """Get an interface port by ID."""
        return self._interface_port_index.get(port_id)

    def update_visual_extent(
        self,
        input_stage_x: int,
        output_stage_x: int,
        top_y: int,
        bottom_y: int,
    ) -> None:
        """Update the visual extent from scene bounds.

        Args:
            input_stage_x: Input stage X position in grid units.
            output_stage_x: Output stage X position in grid units.
            top_y: Top boundary in grid units.
            bottom_y: Bottom boundary in grid units.
        """
        self.visual.input_stage_x = input_stage_x
        self.visual.output_stage_x = output_stage_x
        self.visual.width = output_stage_x - input_stage_x
        self.visual.height = bottom_y - top_y
        self.metadata.modified_at = datetime.now()

    def get_ports_as_primitive_format(self) -> list[dict]:
        """Export interface ports in primitive-compatible JSON format.

        Returns a list of port dictionaries with:
        - name: port name
        - direction: "in" or "out" (primitive format)
        - data_type: data type string
        - position: [x, y] in grid units
        """
        ports = []
        for iface_port in self.interface_ports:
            # Convert direction to primitive format ("in"/"out" instead of "input"/"output")
            direction = "in" if iface_port.direction == InterfaceDirection.INPUT else "out"
            port_dict = {
                "name": iface_port.name,
                "direction": direction,
                "data_type": iface_port.data_type,
            }
            if iface_port.position:
                port_dict["position"] = list(iface_port.position)
            ports.append(port_dict)
        return ports
