"""Undo/Redo command infrastructure for canvas operations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from PySide6.QtCore import QPointF

from pipeline_designer.domain.models import ComponentInstance, Connection

if TYPE_CHECKING:
    from .scene import DesignScene


class Command(ABC):
    """Base class for undoable commands."""

    @abstractmethod
    def execute(self) -> None:
        """Execute the command."""
        pass

    @abstractmethod
    def undo(self) -> None:
        """Undo the command."""
        pass

    @property
    def description(self) -> str:
        """Human-readable description of the command."""
        return self.__class__.__name__


@dataclass
class AddComponentCommand(Command):
    """Command for adding a component to the scene."""

    scene: "DesignScene"
    component_name: str
    x: float
    y: float
    _instance: ComponentInstance | None = None

    def execute(self) -> None:
        """Add the component to the scene."""
        item = self.scene._add_component_internal(self.component_name, self.x, self.y)
        if item:
            self._instance = item.get_instance()

    def undo(self) -> None:
        """Remove the component from the scene."""
        if self._instance:
            self.scene._remove_component_internal(self._instance.id)

    @property
    def description(self) -> str:
        return f"Add {self.component_name}"


@dataclass
class RemoveComponentCommand(Command):
    """Command for removing a component from the scene."""

    scene: "DesignScene"
    component_id: UUID
    _instance: ComponentInstance | None = None
    _connections: list[Connection] | None = None

    def execute(self) -> None:
        """Remove the component and store state for undo."""
        # Store the instance and its connections before removal
        item = self.scene.get_component_item(self.component_id)
        if item:
            self._instance = item.get_instance().model_copy(deep=True)
            # Store connections involving this component
            self._connections = [
                conn.model_copy(deep=True)
                for conn in self.scene._design.connections
                if conn.source.component_id == self.component_id
                or conn.target.component_id == self.component_id
            ]
        self.scene._remove_component_internal(self.component_id)

    def undo(self) -> None:
        """Restore the component and its connections."""
        if self._instance:
            self.scene._restore_component_internal(self._instance)
            # Restore connections
            if self._connections:
                for conn in self._connections:
                    self.scene._restore_connection_internal(conn)

    @property
    def description(self) -> str:
        return f"Remove component"


@dataclass
class MoveComponentCommand(Command):
    """Command for moving a component."""

    scene: "DesignScene"
    component_id: UUID
    old_pos: tuple[float, float]
    new_pos: tuple[float, float]

    def execute(self) -> None:
        """Move the component to the new position."""
        self.scene._move_component_internal(self.component_id, self.new_pos)

    def undo(self) -> None:
        """Move the component back to the old position."""
        self.scene._move_component_internal(self.component_id, self.old_pos)

    @property
    def description(self) -> str:
        return "Move component"


@dataclass
class AddConnectionCommand(Command):
    """Command for adding a connection."""

    scene: "DesignScene"
    connection: Connection

    def execute(self) -> None:
        """Add the connection to the scene."""
        self.scene._add_connection_internal(self.connection)

    def undo(self) -> None:
        """Remove the connection from the scene."""
        self.scene._remove_connection_internal(self.connection.id)

    @property
    def description(self) -> str:
        return "Add connection"


@dataclass
class RemoveConnectionCommand(Command):
    """Command for removing a connection."""

    scene: "DesignScene"
    connection_id: UUID
    _connection: Connection | None = None

    def execute(self) -> None:
        """Remove the connection and store state for undo."""
        item = self.scene._connection_items.get(self.connection_id)
        if item:
            self._connection = item.get_connection().model_copy(deep=True)
        self.scene._remove_connection_internal(self.connection_id)

    def undo(self) -> None:
        """Restore the connection."""
        if self._connection:
            self.scene._restore_connection_internal(self._connection)

    @property
    def description(self) -> str:
        return "Remove connection"


class UndoStack:
    """Manages undo/redo history for commands."""

    def __init__(self, max_size: int = 100):
        """Initialize the undo stack.

        Args:
            max_size: Maximum number of commands to keep in history.
        """
        self._undo_stack: list[Command] = []
        self._redo_stack: list[Command] = []
        self._max_size = max_size

    def push(self, command: Command) -> None:
        """Execute a command and add it to the undo stack.

        This clears the redo stack since we're branching history.
        """
        command.execute()
        self._undo_stack.append(command)
        self._redo_stack.clear()

        # Limit stack size
        if len(self._undo_stack) > self._max_size:
            self._undo_stack.pop(0)

    def undo(self) -> bool:
        """Undo the last command.

        Returns:
            True if a command was undone, False if nothing to undo.
        """
        if not self._undo_stack:
            return False

        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        return True

    def redo(self) -> bool:
        """Redo the last undone command.

        Returns:
            True if a command was redone, False if nothing to redo.
        """
        if not self._redo_stack:
            return False

        command = self._redo_stack.pop()
        command.execute()
        self._undo_stack.append(command)
        return True

    def can_undo(self) -> bool:
        """Check if there are commands to undo."""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """Check if there are commands to redo."""
        return len(self._redo_stack) > 0

    def clear(self) -> None:
        """Clear all undo/redo history."""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def get_undo_description(self) -> str | None:
        """Get description of the next command to undo."""
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    def get_redo_description(self) -> str | None:
        """Get description of the next command to redo."""
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None
