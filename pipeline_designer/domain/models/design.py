"""Design document model."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from .instance import ComponentInstance, Connection


class DesignMetadata(BaseModel):
    """Metadata for a design document."""

    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)
    author: str = Field(default="")
    version: str = Field(default="1.0.0")


class Design(BaseModel):
    """A complete pipeline design document."""

    name: str = Field(default="Untitled", description="Design name")
    components: list[ComponentInstance] = Field(
        default_factory=list, description="Component instances in the design"
    )
    connections: list[Connection] = Field(
        default_factory=list, description="Connections between components"
    )
    metadata: DesignMetadata = Field(
        default_factory=DesignMetadata, description="Design metadata"
    )

    def get_component_by_id(self, component_id: UUID) -> ComponentInstance | None:
        """Get a component instance by ID."""
        for component in self.components:
            if component.id == component_id:
                return component
        return None

    def add_component(self, component: ComponentInstance) -> None:
        """Add a component instance to the design."""
        self.components.append(component)
        self.metadata.modified_at = datetime.now()

    def remove_component(self, component_id: UUID) -> bool:
        """Remove a component instance and its connections."""
        component = self.get_component_by_id(component_id)
        if component is None:
            return False

        self.components.remove(component)
        self.connections = [
            conn
            for conn in self.connections
            if conn.source.component_id != component_id
            and conn.target.component_id != component_id
        ]
        self.metadata.modified_at = datetime.now()
        return True

    def add_connection(self, connection: Connection) -> None:
        """Add a connection to the design."""
        self.connections.append(connection)
        self.metadata.modified_at = datetime.now()

    def remove_connection(self, connection_id: UUID) -> bool:
        """Remove a connection from the design."""
        for conn in self.connections:
            if conn.id == connection_id:
                self.connections.remove(conn)
                self.metadata.modified_at = datetime.now()
                return True
        return False
