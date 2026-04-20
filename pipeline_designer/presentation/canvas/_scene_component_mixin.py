"""Component, stage, and library management mixin for DesignScene."""

from uuid import UUID

from pipeline_designer.domain.models import ComponentDefinition, ComponentInstance, Stage
from pipeline_designer.infrastructure.persistence import LibraryLoader

from .commands import AddComponentCommand, RemoveComponentCommand
from .items import ComponentItem, StageItem
from .items.port_item import PortItem


class _SceneComponentMixin:
    """Manages component/stage lifecycle, library, snapping, and conflict detection."""

    def set_library(
        self,
        library: dict[str, ComponentDefinition],
        loader: LibraryLoader | None = None,
    ) -> None:
        """Set the component library.

        Args:
            library: Dictionary mapping component names to definitions.
            loader: Optional library loader for composite component support.
        """
        self._library = library
        self._library_loader = loader
        register_def = library.get("Register")
        if register_def:
            self._register_width = self._grid.to_pixels(register_def.visual.width)

    def add_component_at(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Add a component instance at the specified position (with undo support).

        Args:
            component_name: Name of the component definition.
            x: X position in pixels.
            y: Y position in pixels.

        Returns:
            The created ComponentItem, or None if component not found.
        """
        definition = self._library.get(component_name)
        if definition is None:
            return None

        if self._snap_to_grid:
            y = self._grid.snap_to_grid(y)

        if component_name == "Register":
            x = self._get_register_x_position(x)
        elif self._snap_to_grid:
            x = self._grid.snap_to_grid(x)

        command = AddComponentCommand(scene=self, component_name=component_name, x=x, y=y)
        self._undo_stack.push(command)

        if command._instance:
            return self._component_items.get(command._instance.id)
        return None

    def _add_component_internal(self, component_name: str, x: float, y: float) -> ComponentItem | None:
        """Internal method to add a component (used by commands).

        Args:
            component_name: Name of the component definition.
            x: X position in pixels (already snapped).
            y: Y position in pixels (already snapped).

        Returns:
            The created ComponentItem, or None if component not found.
        """
        definition = self._library.get(component_name)
        if definition is None:
            return None

        is_composite = False
        stage_count = 1
        if self._library_loader and self._library_loader.is_composite(component_name):
            is_composite = True
            composite_design = self._library_loader.get_composite_design(component_name)
            if composite_design:
                stage_count = max(1, composite_design.latency)

        if component_name != "Register" and not is_composite:
            comp_width, _ = definition.visual.get_pixel_size(self._grid)
            x = self._avoid_stage_overlap(x, comp_width)

        pos_grid = self._grid.pos_to_grid((x, y))
        instance = ComponentInstance(
            definition_ref=component_name,
            position=pos_grid,
            is_composite=is_composite,
            stage_count=stage_count,
        )

        self._design.add_component(instance)
        item = self._create_component_item(instance)

        if component_name == "Register":
            self._assign_register_to_stage(instance)
            self._update_all_component_alignments()
        elif is_composite and stage_count > 1:
            self._assign_composite_to_stages(instance)
            self._update_all_component_alignments()
        else:
            self._update_component_alignment(instance)

        self.component_added.emit(instance)
        self._update_component_bounds()
        return item

    def _get_register_x_position(self, x: float) -> float:
        """Get the x position for a register, snapping to existing stages."""
        x_grid = self._grid.to_grid_units(x)
        stage = self._design.get_stage_at_x(x_grid)
        if stage:
            return self._grid.to_pixels(stage.x_position)
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _assign_register_to_stage(self, instance: ComponentInstance) -> None:
        """Assign a register instance to a stage, creating one if needed."""
        x_grid = instance.position[0]
        stage = self._design.get_stage_at_x(x_grid)

        if stage is None:
            width_grid = self._grid.to_grid_units(self._register_width)
            stage = Stage(
                index=0,
                x_position=x_grid,
                width=width_grid,
                register_ids=[instance.id],
            )
            self._design.stages.append(stage)
            self._design.reindex_stages()
            self._rebuild_all_stages()
        else:
            if instance.id not in stage.register_ids:
                stage.register_ids.append(instance.id)

        instance.pipeline_stage = stage.index
        self._update_register_displays()
        self.stages_changed.emit()

    def _assign_composite_to_stages(self, instance: ComponentInstance) -> None:
        """Assign a composite component to pipeline stages."""
        if not self._library_loader:
            return

        composite_design = self._library_loader.get_composite_design(instance.definition_ref)
        if not composite_design:
            return

        internal_stage_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_stage_offsets:
            return

        drop_x_px = self._grid.to_pixels(instance.position[0])
        drop_y_px = self._grid.to_pixels(instance.position[1])

        first_internal_offset = internal_stage_offsets[0]
        first_internal_stage_x = drop_x_px + first_internal_offset

        nearest_stage = self._find_nearest_stage(first_internal_stage_x)

        if nearest_stage is not None:
            stage_x_px = self._grid.to_pixels(nearest_stage.x_position)
            component_x_px = stage_x_px - first_internal_offset
            component_x_px = self._grid.snap_to_grid(component_x_px)

            instance.pipeline_stage = nearest_stage.index

            if len(internal_stage_offsets) > 1:
                self._synchronize_stage_spacing(
                    composite_design, instance, component_x_px,
                    nearest_stage.index, internal_stage_offsets
                )

            item = self._component_items.get(instance.id)
            if item:
                item.setPos(component_x_px, drop_y_px)
                instance.position = self._grid.pos_to_grid((component_x_px, drop_y_px))
        else:
            self._create_stages_from_composite(composite_design, instance, drop_x_px)

        self._rebuild_all_stages()
        self.stages_changed.emit()

    def _get_composite_internal_stage_offsets(self, composite_design) -> list[float]:
        """Get the x offsets of internal register stages from component origin (pixels)."""
        if not composite_design.stages:
            return []

        origin_x = self._grid.to_pixels(composite_design.visual.input_stage_x)
        offsets = []
        for stage in sorted(composite_design.stages, key=lambda s: s.x_position):
            stage_x_px = self._grid.to_pixels(stage.x_position)
            offsets.append(stage_x_px - origin_x)
        return offsets

    def _find_nearest_stage(self, x: float) -> Stage | None:
        """Find the nearest stage within snapping distance to x (pixels)."""
        if not self._design.stages:
            return None

        nearest = None
        min_distance = float('inf')
        for stage in self._design.stages:
            stage_x_px = self._grid.to_pixels(stage.x_position)
            distance = abs(stage_x_px - x)
            if distance < min_distance:
                min_distance = distance
                nearest = stage

        snap_threshold = self._grid.to_pixels(5)
        return nearest if min_distance <= snap_threshold else None

    def _synchronize_stage_spacing(
        self,
        composite_design,
        instance: ComponentInstance,
        component_x: float,
        first_stage_index: int,
        internal_offsets: list[float],
    ) -> None:
        """Synchronize stage spacing between composite and main design."""
        if len(internal_offsets) < 2:
            return

        internal_spacings = [
            internal_offsets[i] - internal_offsets[i - 1]
            for i in range(1, len(internal_offsets))
        ]

        sorted_stages = sorted(self._design.stages, key=lambda s: s.x_position)
        main_spacings = [
            self._grid.to_pixels(sorted_stages[i].x_position - sorted_stages[i - 1].x_position)
            for i in range(1, len(sorted_stages))
        ]

        main_spacing = (
            sum(main_spacings) / len(main_spacings)
            if main_spacings
            else self._grid.to_pixels(10)
        )

        first_internal_spacing = internal_spacings[0] if internal_spacings else 0
        if first_internal_spacing > main_spacing + self._grid.size:
            extra_space = first_internal_spacing - main_spacing
            self._shift_elements_right(component_x, first_stage_index, extra_space)

    def _shift_elements_right(
        self,
        from_x: float,
        from_stage_index: int,
        shift_amount: float,
    ) -> None:
        """Shift all stages and components to the right of a position.

        Args:
            from_x: X position from which to start shifting (pixels).
            from_stage_index: Stage index from which to start shifting.
            shift_amount: Amount to shift in pixels.
        """
        shift_amount = self._grid.snap_to_grid(shift_amount)
        if shift_amount <= 0:
            return

        shift_grid = self._grid.to_grid_units(shift_amount)

        for stage in self._design.stages:
            if stage.index > from_stage_index:
                stage.x_position += shift_grid

        for comp_id, item in self._component_items.items():
            comp_instance = item.get_instance()
            comp_x_px = self._grid.to_pixels(comp_instance.position[0])

            definition = self._library.get(comp_instance.definition_ref)
            comp_width = (
                definition.visual.get_pixel_size(self._grid)[0]
                if definition
                else self._grid.to_pixels(4)
            )

            if comp_x_px > from_x + comp_width / 2:
                new_x_px = comp_x_px + shift_amount
                new_y_px = self._grid.to_pixels(comp_instance.position[1])
                comp_instance.position = self._grid.pos_to_grid((new_x_px, new_y_px))
                item.setPos(new_x_px, new_y_px)

        self.update_connection_positions()

    def _ensure_stages_for_composite(
        self,
        composite_design,
        instance: ComponentInstance,
        component_x: float,
        first_stage_index: int,
    ) -> None:
        """Ensure main design has stages for all internal composite stages."""
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if len(internal_offsets) <= 1:
            return

        for i, offset in enumerate(internal_offsets[1:], start=1):
            target_x_px = component_x + offset
            target_stage_index = first_stage_index + i

            existing_stage = next(
                (s for s in self._design.stages if s.index == target_stage_index), None
            )

            if existing_stage is None:
                internal_stage = (
                    composite_design.stages[i] if i < len(composite_design.stages) else None
                )
                stage_width_grid = (
                    internal_stage.width
                    if internal_stage
                    else self._grid.to_grid_units(self._register_width)
                )
                target_x_grid = self._grid.to_grid_units(
                    self._grid.snap_to_grid(target_x_px)
                )
                new_stage = Stage(
                    index=target_stage_index,
                    x_position=target_x_grid,
                    width=stage_width_grid,
                    register_ids=[],
                )
                self._design.stages.append(new_stage)

        self._design.reindex_stages()

    def _create_stages_from_composite(
        self,
        composite_design,
        instance: ComponentInstance,
        drop_x: float,
    ) -> None:
        """Create main design stages based on composite's internal stages."""
        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        if not internal_offsets:
            return

        component_x_px = self._grid.snap_to_grid(drop_x)
        component_y_px = self._grid.to_pixels(instance.position[1])

        item = self._component_items.get(instance.id)
        if item:
            item.setPos(component_x_px, component_y_px)
            instance.position = self._grid.pos_to_grid((component_x_px, component_y_px))

        for i, offset in enumerate(internal_offsets):
            stage_x_px = self._grid.snap_to_grid(component_x_px + offset)
            stage_x_grid = self._grid.to_grid_units(stage_x_px)
            internal_stage = (
                composite_design.stages[i] if i < len(composite_design.stages) else None
            )
            stage_width_grid = (
                internal_stage.width
                if internal_stage
                else self._grid.to_grid_units(self._register_width)
            )
            new_stage = Stage(
                index=i,
                x_position=stage_x_grid,
                width=stage_width_grid,
                register_ids=[],
            )
            self._design.stages.append(new_stage)

        self._design.reindex_stages()
        instance.pipeline_stage = 0

    def _create_stage_item(self, stage: Stage) -> StageItem:
        """Create a graphics item for a stage."""
        item = StageItem(stage, view_height=10000.0, grid=self._grid)
        item.on_stage_click = self._on_stage_click
        self.addItem(item)
        self._stage_items[stage.id] = item
        return item

    def _rebuild_all_stages(self) -> None:
        """Sync stage items incrementally: update existing, add new, remove deleted."""
        model_ids = {s.id for s in self._design.stages}

        for stage_id in set(self._stage_items) - model_ids:
            self.removeItem(self._stage_items.pop(stage_id))

        for stage in self._design.stages:
            item = self._stage_items.get(stage.id)
            if item is not None:
                item.update_stage(stage)
            else:
                self._create_stage_item(stage)

    def _update_all_pipeline_stages(self) -> None:
        """Update pipeline_stage for all registers based on current stages."""
        for stage in self._design.stages:
            for reg_id in stage.register_ids:
                instance = self._design.get_component_by_id(reg_id)
                if instance:
                    instance.pipeline_stage = stage.index

    def _update_register_displays(self) -> None:
        """Force all register component items to repaint."""
        for comp_id, item in self._component_items.items():
            if item.is_register():
                item.update()

    def _calculate_alignment_index(self, x_grid: float) -> int:
        """Calculate the alignment index for a component at position x.

        Alignment indices:
        - 0: Left of the first stage
        - 1: Between stage 1 and stage 2
        - N: Right of stage N
        """
        if not self._design.stages:
            return 0
        sorted_stages = sorted(self._design.stages, key=lambda s: s.x_position)
        for i, stage in enumerate(sorted_stages):
            if x_grid < stage.x_position:
                return i
        return len(sorted_stages)

    def _update_component_alignment(self, instance: ComponentInstance) -> None:
        """Update the alignment index for a single component."""
        if instance.definition_ref == "Register":
            return
        instance.alignment_index = self._calculate_alignment_index(instance.position[0])

    def _update_all_component_alignments(self) -> None:
        """Update alignment indices for all non-register components."""
        for comp_id, item in self._component_items.items():
            instance = item.get_instance()
            if instance.definition_ref != "Register":
                instance.alignment_index = self._calculate_alignment_index(instance.position[0])

    def _create_component_item(self, instance: ComponentInstance) -> ComponentItem:
        """Create a graphics item for a component instance."""
        definition = self._library.get(instance.definition_ref)

        composite_design = None
        if instance.is_composite and self._library_loader:
            composite_design = self._library_loader.get_composite_design(
                instance.definition_ref
            )

        item = ComponentItem(
            instance,
            definition,
            grid=self._grid,
            snap_to_grid=self._snap_to_grid,
            composite_design=composite_design,
            library=self._library,
        )
        pos_px = self._grid.pos_to_pixels(instance.position)
        item.setPos(pos_px[0], pos_px[1])
        self.addItem(item)
        self._component_items[instance.id] = item

        if instance.definition_ref == "Register":
            item.register_moved = self._on_register_moved
            item.snap_register_x = self.snap_register_x
            item.check_distance_conflicts = self._check_distance_conflicts
            item.clear_distance_conflicts = self._clear_distance_conflicts
        elif instance.is_composite:
            internal_offsets = self._get_composite_internal_offsets_for(instance)
            item.snap_composite_x = lambda x, off=internal_offsets: (
                self._snap_composite_x(x, off)
            )
            item.on_composite_drag_update = self._on_composite_drag_update
        else:
            item.avoid_stage_overlap = self._avoid_stage_overlap

        self._wire_port_callbacks(item)

        item.on_move_start = lambda: self.record_move_start(instance.id)
        item.on_move_end = lambda: self.record_move_end(instance.id)

        return item

    def _wire_port_callbacks(self, component_item: ComponentItem) -> None:
        """Wire up port callbacks for connection handling."""
        for port_name, port_item in component_item._port_items.items():
            port_item.on_connection_start = lambda pi=port_item: self._start_connection(pi)

    def _on_register_moved(self, instance: ComponentInstance, old_x: float) -> None:
        """Handle a register being moved."""
        if self._in_stage_group_move:
            return

        new_x_grid = instance.position[0]

        for stage in self._design.stages:
            if instance.id in stage.register_ids:
                stage.register_ids.remove(instance.id)
                break

        existing_stage = self._design.get_stage_at_x(new_x_grid)

        if existing_stage is not None:
            if instance.id not in existing_stage.register_ids:
                existing_stage.register_ids.append(instance.id)
        else:
            width_grid = self._grid.to_grid_units(self._register_width)
            new_stage = Stage(
                index=0,
                x_position=new_x_grid,
                width=width_grid,
                register_ids=[instance.id],
            )
            self._design.stages.append(new_stage)

        self._design.remove_empty_stages()
        self._design.reindex_stages()
        self._update_all_pipeline_stages()
        self._rebuild_all_stages()
        self._update_register_displays()
        self._update_all_component_alignments()
        self.stages_changed.emit()

    def remove_component(self, component_id: UUID) -> bool:
        """Remove a component instance from the scene (with undo support)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        command = RemoveComponentCommand(scene=self, component_id=component_id)
        self._undo_stack.push(command)
        return True

    def _remove_component_internal(self, component_id: UUID) -> bool:
        """Internal method to remove a component (used by commands)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        is_register = instance.definition_ref == "Register"

        conns_to_remove = [
            conn.id for conn in self._design.connections
            if conn.source.component_id == component_id
            or conn.target.component_id == component_id
        ]
        for conn_id in conns_to_remove:
            self._remove_connection_internal(conn_id)

        if is_register:
            for stage in self._design.stages:
                if component_id in stage.register_ids:
                    stage.register_ids.remove(component_id)
                    break

        if instance.is_composite:
            self._release_composite_stage_bindings(component_id)

        self.removeItem(item)
        del self._component_items[component_id]
        self._design.remove_component(component_id)

        overlay = self._temp_position_overlays.pop(component_id, None)
        if overlay and overlay.scene():
            self.removeItem(overlay)

        if is_register:
            self._design.remove_empty_stages()
            self._design.reindex_stages()
            self._update_all_pipeline_stages()
            self._rebuild_all_stages()
            self._update_register_displays()
            self.stages_changed.emit()

        self.component_removed.emit(component_id)
        self._update_component_bounds()
        return True

    def _restore_component_internal(self, instance: ComponentInstance) -> ComponentItem | None:
        """Internal method to restore a component (used by undo)."""
        self._design.add_component(instance)
        item = self._create_component_item(instance)

        if instance.definition_ref == "Register":
            self._assign_register_to_stage(instance)

        self.component_added.emit(instance)
        self._update_component_bounds()
        return item

    def _move_component_internal(self, component_id: UUID, pos: tuple[float, float]) -> bool:
        """Internal method to move a component (used by undo/redo)."""
        item = self._component_items.get(component_id)
        if item is None:
            return False

        instance = item.get_instance()
        old_x = instance.position[0]
        is_register = instance.definition_ref == "Register"

        instance.position = pos
        pos_px = self._grid.pos_to_pixels(pos)
        item.setPos(pos_px[0], pos_px[1])

        if is_register:
            self._on_register_moved(instance, old_x)

        self.update_connection_positions()
        return True

    def get_component_item(self, component_id: UUID) -> ComponentItem | None:
        """Get a component item by ID."""
        return self._component_items.get(component_id)

    def get_stage_at_position(self, x: float) -> Stage | None:
        """Get the stage at a given x position."""
        return self._design.get_stage_at_x(x)

    def snap_to_grid(self, x: float, y: float) -> tuple[float, float]:
        """Snap coordinates to the grid."""
        if not self._snap_to_grid:
            return x, y
        return (self._grid.snap_to_grid(x), self._grid.snap_to_grid(y))

    def _avoid_stage_overlap(self, x: float, width: float, _depth: int = 0) -> float:
        """Adjust x position to avoid overlapping with register stages.

        Maintains a minimum distance of 2 grid units from any stage.
        """
        if not self._design.stages or _depth > 10:
            return x

        min_gap = self._grid.to_pixels(2)
        comp_left = x
        comp_right = x + width

        for stage in sorted(self._design.stages, key=lambda s: s.x_position):
            stage_left = self._grid.to_pixels(stage.x_position)
            stage_right = self._grid.to_pixels(stage.x_position + stage.width)

            if comp_left < stage_right + min_gap and comp_right > stage_left - min_gap:
                dist_to_left = comp_right - (stage_left - min_gap)
                dist_to_right = (stage_right + min_gap) - comp_left

                if dist_to_left <= dist_to_right:
                    new_x = stage_left - min_gap - width
                else:
                    new_x = stage_right + min_gap

                new_x = self._grid.snap_to_grid(new_x)
                return self._avoid_stage_overlap(new_x, width, _depth + 1)

        return x

    def snap_register_x(self, x: float) -> float:
        """Snap x coordinate for a register (stage-aware)."""
        if self._in_stage_group_move:
            return self._grid.snap_to_grid(x) if self._snap_to_grid else x

        x_grid = self._grid.to_grid_units(x)
        stage = self._design.get_stage_at_x(x_grid)
        if stage:
            return self._grid.to_pixels(stage.x_position)
        if self._snap_to_grid:
            return self._grid.snap_to_grid(x)
        return x

    def _get_composite_internal_offsets_for(
        self, instance: ComponentInstance
    ) -> list[float]:
        """Return the internal stage offsets (pixels from left edge) for instance."""
        if not self._library_loader:
            return []
        composite_design = self._library_loader.get_composite_design(instance.definition_ref)
        if not composite_design:
            return []
        return self._get_composite_internal_stage_offsets(composite_design)

    def _snap_composite_x(self, x_px: float, internal_offsets: list[float]) -> float:
        """Snap a composite's left-edge pixel x so its first internal stage
        aligns with the nearest main-design stage.
        """
        if not internal_offsets:
            return self._grid.snap_to_grid(x_px) if self._snap_to_grid else x_px

        first_world_x = x_px + internal_offsets[0]
        nearest = self._find_nearest_stage_wide(first_world_x)
        if nearest is not None:
            stage_x_px = self._grid.to_pixels(nearest.x_position)
            return self._grid.snap_to_grid(stage_x_px - internal_offsets[0])

        return self._grid.snap_to_grid(x_px) if self._snap_to_grid else x_px

    def _check_distance_conflicts(
        self, register_x: float, aligned_stage_index: int | None
    ) -> None:
        """Check for distance conflicts during register/stage movement.

        Makes conflicting components semi-transparent.
        """
        min_gap = self._grid.to_pixels(2)
        register_x_grid = self._grid.to_grid_units(register_x)
        stage_at_x = self._design.get_stage_at_x(register_x_grid)

        if stage_at_x:
            stage_left = self._grid.to_pixels(stage_at_x.x_position)
            stage_right = self._grid.to_pixels(stage_at_x.x_position + stage_at_x.width)
        else:
            stage_left = register_x
            stage_right = register_x + self._register_width

        for comp_id, item in self._component_items.items():
            instance = item.get_instance()
            if instance.definition_ref == "Register":
                continue

            definition = self._library.get(instance.definition_ref)
            comp_width = (
                definition.visual.get_pixel_size(self._grid)[0]
                if definition
                else self._grid.to_pixels(4)
            )

            comp_left = self._grid.to_pixels(instance.position[0])
            comp_right = comp_left + comp_width
            is_conflict = (
                comp_left < stage_right + min_gap and comp_right > stage_left - min_gap
            )

            if is_conflict:
                if comp_id not in self._conflict_items:
                    self._conflict_items.add(comp_id)
                    item.setOpacity(0.5)
            else:
                if comp_id in self._conflict_items:
                    self._conflict_items.discard(comp_id)
                    item.setOpacity(1.0)

    def _clear_distance_conflicts(self) -> None:
        """Clear all distance conflict highlighting."""
        for comp_id in list(self._conflict_items):
            item = self._component_items.get(comp_id)
            if item:
                item.setOpacity(1.0)
        self._conflict_items.clear()
