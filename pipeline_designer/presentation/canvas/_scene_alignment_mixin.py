"""Undo/redo, stage group-move, and composite alignment mixin for DesignScene."""

from uuid import UUID

from PySide6.QtCore import QPointF

from pipeline_designer.domain.models import ComponentInstance, Stage

from .commands import MoveComponentCommand, MoveStageCommand
from .items import TempPositionOverlayItem


class _SceneAlignmentMixin:
    """Manages undo stack, stage group-move, composite alignment, and temporary overlays."""

    # ── Undo / Redo ───────────────────────────────────────────────────────────

    def undo(self) -> bool:
        """Undo the last action."""
        return self._undo_stack.undo()

    def redo(self) -> bool:
        """Redo the last undone action."""
        return self._undo_stack.redo()

    def can_undo(self) -> bool:
        """Check if there are actions to undo."""
        return self._undo_stack.can_undo()

    def can_redo(self) -> bool:
        """Check if there are actions to redo."""
        return self._undo_stack.can_redo()

    def get_undo_description(self) -> str | None:
        """Get description of the next action to undo."""
        return self._undo_stack.get_undo_description()

    def get_redo_description(self) -> str | None:
        """Get description of the next action to redo."""
        return self._undo_stack.get_redo_description()

    def clear_undo_stack(self) -> None:
        """Clear all undo/redo history."""
        self._undo_stack.clear()

    # ── Move tracking ─────────────────────────────────────────────────────────

    def record_move_start(self, component_id: UUID) -> None:
        """Record the start position of a component move."""
        item = self._component_items.get(component_id)
        if item:
            instance = item.get_instance()
            self._move_start_positions[component_id] = instance.position

    def record_move_end(self, component_id: UUID) -> None:
        """Record the end of a component move and create undo command."""
        if component_id not in self._move_start_positions:
            return

        item = self._component_items.get(component_id)
        if item is None:
            del self._move_start_positions[component_id]
            return

        old_pos = self._move_start_positions[component_id]
        new_pos = item.get_instance().position
        del self._move_start_positions[component_id]

        if old_pos != new_pos:
            instance = item.get_instance()

            if instance.is_composite and component_id == self._dragging_composite_id:
                self._commit_composite_drag_alignment(instance)

            command = MoveComponentCommand(
                scene=self,
                component_id=component_id,
                old_pos=old_pos,
                new_pos=new_pos,
            )
            # Movement already happened; just record for undo without re-executing
            self._undo_stack._undo_stack.append(command)
            self._undo_stack._redo_stack.clear()

            self._update_component_alignment(instance)

    # ── Stage group-move ──────────────────────────────────────────────────────

    def _on_stage_click(self, stage: Stage, scene_pos: QPointF) -> None:
        """Initiate a group-move of every register in a stage."""
        self._moving_stage = stage
        self._stage_move_mouse_start = scene_pos
        self._stage_move_stage_start_x = stage.x_position
        self._stage_move_new_x = stage.x_position

        self._stage_group_items = []
        self._stage_group_original_positions = {}
        for reg_id in stage.register_ids:
            item = self._component_items.get(reg_id)
            if item is not None:
                self._stage_group_items.append(item)
                self._stage_group_original_positions[reg_id] = item.get_instance().position

        if not self._stage_group_items:
            self._moving_stage = None
            return

        self._in_stage_group_move = True

        stage_item = self._stage_items.get(stage.id)
        if stage_item:
            stage_item.set_being_moved(True)

        self._set_non_group_items_movable(False)

    def _update_stage_group_move(self, scene_pos: QPointF) -> None:
        """Update register positions during a stage group-drag."""
        if self._moving_stage is None:
            return

        stage = self._moving_stage
        delta_px = scene_pos.x() - self._stage_move_mouse_start.x()
        delta_grid = self._grid.to_grid_units(delta_px)
        proposed_x = self._grid.snap_to_grid_units(
            self._stage_move_stage_start_x + delta_grid
        )

        left_bound = self._get_stage_left_bound(stage)
        proposed_x = max(proposed_x, left_bound)

        snap_x = self._get_stage_snap_x(stage, proposed_x)
        if snap_x is not None:
            proposed_x = snap_x

        self._stage_move_new_x = proposed_x
        delta_applied = proposed_x - self._stage_move_stage_start_x

        for item in self._stage_group_items:
            orig = self._stage_group_original_positions[item.get_instance().id]
            new_x_grid = orig[0] + delta_applied
            new_x_px = self._grid.to_pixels(new_x_grid)
            new_y_px = self._grid.to_pixels(orig[1])
            item.set_position_no_callbacks(new_x_px, new_y_px)

        stage.x_position = proposed_x
        stage_item = self._stage_items.get(stage.id)
        if stage_item:
            stage_item.update_stage(stage)

        is_invalid = self._check_stage_group_overlaps(stage, proposed_x)
        if stage_item:
            stage_item.set_invalid_overlap(is_invalid)

    def _commit_stage_group_move(self) -> None:
        """Commit the current stage group-move and record an undo command."""
        if self._moving_stage is None:
            return

        stage = self._moving_stage
        new_x = self._stage_move_new_x
        old_x = self._stage_move_stage_start_x
        delta_grid = new_x - old_x

        new_reg_positions: dict[UUID, tuple[float, float]] = {}
        for item in self._stage_group_items:
            inst = item.get_instance()
            orig = self._stage_group_original_positions[inst.id]
            new_reg_positions[inst.id] = (orig[0] + delta_grid, orig[1])

        old_composite_offsets: dict[UUID, float] = {}
        new_composite_offsets: dict[UUID, float] = {}
        for comp_id, binding in self._composite_stage_bindings.items():
            if stage.id in binding.values():
                inst = self._design.get_component_by_id(comp_id)
                if inst:
                    old_composite_offsets[comp_id] = inst.stage_position_offset
                    new_composite_offsets[comp_id] = inst.stage_position_offset + delta_grid

        command = MoveStageCommand(
            scene=self,
            stage_id=stage.id,
            old_stage_x=old_x,
            new_stage_x=new_x,
            old_additional_offset=stage.additional_offset,
            new_additional_offset=max(0.0, stage.additional_offset + delta_grid),
            old_reg_positions={str(k): v for k, v in self._stage_group_original_positions.items()},
            new_reg_positions={str(k): v for k, v in new_reg_positions.items()},
            old_composite_offsets={str(k): v for k, v in old_composite_offsets.items()},
            new_composite_offsets={str(k): v for k, v in new_composite_offsets.items()},
        )
        # State already applied visually; record without re-executing
        self._undo_stack._undo_stack.append(command)
        self._undo_stack._redo_stack.clear()

        stage.x_position = new_x
        stage.additional_offset = max(0.0, stage.additional_offset + delta_grid)

        for inst_id, pos in new_reg_positions.items():
            inst = self._design.get_component_by_id(inst_id)
            if inst:
                inst.position = pos

        for inst_id, offset in new_composite_offsets.items():
            inst = self._design.get_component_by_id(inst_id)
            if inst:
                inst.stage_position_offset = offset

        self._design.reindex_stages()
        self._rebuild_all_stages()
        self._update_register_displays()
        self._update_all_component_alignments()
        self._apply_stage_position_offsets()
        self.update_connection_positions()
        self.stages_changed.emit()

        self._end_stage_group_move()

    def _end_stage_group_move(self) -> None:
        """Clean up after a stage group-move."""
        stage = self._moving_stage
        if stage:
            stage_item = self._stage_items.get(stage.id)
            if stage_item:
                stage_item.set_being_moved(False)
                stage_item.set_invalid_overlap(False)

        self._moving_stage = None
        self._stage_group_items = []
        self._stage_group_original_positions = {}
        self._in_stage_group_move = False
        self._set_non_group_items_movable(True)

    def _get_stage_left_bound(self, stage: Stage) -> float:
        """Return the minimum x_position (grid units) for stage.

        Constraint only applies when a composite is bound to this stage.
        """
        has_binding = any(
            stage.id in binding.values()
            for binding in self._composite_stage_bindings.values()
        )
        if not has_binding:
            return float("-inf")
        return stage.x_position - stage.additional_offset

    def _get_stage_snap_x(self, stage: Stage, proposed_x: float) -> float | None:
        """Return snap x when proposed_x is near the natural alignment point."""
        has_binding = any(
            stage.id in binding.values()
            for binding in self._composite_stage_bindings.values()
        )
        if not has_binding:
            return None
        snap_threshold = 1.5
        natural_x = stage.x_position - stage.additional_offset
        if abs(proposed_x - natural_x) <= snap_threshold:
            return natural_x
        return None

    def _check_stage_group_overlaps(self, moving_stage: Stage, proposed_x: float) -> bool:
        """Return True if moving_stage at proposed_x overlaps any other stage."""
        width = moving_stage.width
        for other in self._design.stages:
            if other.id == moving_stage.id:
                continue
            if other.overlaps(proposed_x, width):
                return True
        return False

    def _set_non_group_items_movable(self, movable: bool) -> None:
        """Enable/disable ItemIsMovable on all items not in the current group."""
        group_ids = {item.get_instance().id for item in self._stage_group_items}
        for comp_id, item in self._component_items.items():
            if comp_id not in group_ids:
                item.setFlag(item.GraphicsItemFlag.ItemIsMovable, movable)

    def _move_register_direct(self, inst_id: UUID, pos: tuple[float, float]) -> None:
        """Move a register item directly (used by MoveStageCommand undo/redo)."""
        item = self._component_items.get(inst_id)
        inst = self._design.get_component_by_id(inst_id)
        if item and inst:
            inst.position = pos
            x_px, y_px = self._grid.pos_to_pixels(pos)
            item.set_position_no_callbacks(x_px, y_px)

    # ── Composite drag alignment ──────────────────────────────────────────────

    def _on_composite_drag_update(
        self, instance: ComponentInstance, new_pos: QPointF
    ) -> None:
        """Called each pixel-move while dragging a composite component."""
        self._dragging_composite_id = instance.id
        self._clear_composite_drag_previews()
        self._composite_proposed_shifts.clear()

        comp_x_px = new_pos.x()
        internal_offsets = self._get_composite_internal_offsets_for(instance)

        item = self._component_items.get(instance.id)
        if item is None:
            return

        is_aligned = False
        aligned_label = "Not aligned to any stage"
        nearest: Stage | None = None

        if internal_offsets and self._design.stages:
            first_world_x = comp_x_px + internal_offsets[0]
            nearest = self._find_nearest_stage_wide(first_world_x)
            if nearest is not None:
                is_aligned = True
                aligned_label = f"Aligned → Stage {nearest.index}"

                if len(internal_offsets) > 1:
                    by_index = {
                        s.index: s
                        for s in sorted(self._design.stages, key=lambda s: s.x_position)
                    }
                    for i, int_off in enumerate(internal_offsets[1:], start=1):
                        target_idx = nearest.index + i
                        main_st = by_index.get(target_idx)
                        if main_st is None:
                            continue
                        internal_world_x = comp_x_px + int_off
                        main_x_px = self._grid.to_pixels(main_st.x_position)
                        needed_shift_px = internal_world_x - main_x_px
                        if needed_shift_px > self._grid.size:
                            shift_grid = self._grid.to_grid_units(needed_shift_px)
                            self._composite_proposed_shifts[main_st.id] = shift_grid
                            aligned_label += " (stages will shift →)"

        elif not self._design.stages:
            is_aligned = True
            aligned_label = "Will create stages on drop"

        self._last_drag_was_aligned = is_aligned
        item.set_invalid(not is_aligned)

        rect = item.sceneBoundingRect().adjusted(-5, -5, 5, 5)
        overlay = TempPositionOverlayItem(rect, label=aligned_label, invalid=not is_aligned)
        self.addItem(overlay)
        self._composite_drag_overlays.append(overlay)

    def _commit_composite_drag_alignment(self, instance: ComponentInstance) -> None:
        """Apply proposed stage shifts after a composite is dropped."""
        if not self._library_loader:
            self._clear_composite_drag_previews()
            self._dragging_composite_id = None
            return

        composite_design = self._library_loader.get_composite_design(instance.definition_ref)
        if not composite_design:
            self._clear_composite_drag_previews()
            self._dragging_composite_id = None
            return

        for stage_id, shift_grid in self._composite_proposed_shifts.items():
            if shift_grid > 0:
                self._shift_stage_right_permanent(stage_id, shift_grid, instance.id)

        internal_offsets = self._get_composite_internal_stage_offsets(composite_design)
        binding: dict[int, UUID] = {}
        item = self._component_items.get(instance.id)
        if item and internal_offsets:
            comp_x_px = item.pos().x()
            for i, off in enumerate(internal_offsets):
                world_x = comp_x_px + off
                nearest = self._find_nearest_stage_wide(world_x)
                if nearest:
                    binding[i] = nearest.id
        if binding:
            self._composite_stage_bindings[instance.id] = binding

        self._add_temp_position_overlay(instance.id)

        comp_item = self._component_items.get(instance.id)
        if comp_item:
            comp_item.set_invalid(False)

        self._clear_composite_drag_previews()
        self._composite_proposed_shifts.clear()
        self._dragging_composite_id = None

        self._apply_stage_position_offsets()
        self.update_connection_positions()

    def _shift_stage_right_permanent(
        self, stage_id: UUID, shift_grid: float, source_composite_id: UUID
    ) -> None:
        """Shift a stage and all elements to its right by shift_grid units."""
        stage = self._design.get_stage_by_id(stage_id)
        if stage is None or shift_grid <= 0:
            return

        stage.x_position += shift_grid
        stage.additional_offset += shift_grid

        from_x_grid = stage.x_position - shift_grid
        for other_stage in self._design.stages:
            if other_stage.id != stage_id and other_stage.x_position > from_x_grid:
                other_stage.x_position += shift_grid
                other_stage.additional_offset += shift_grid

        for comp_id, comp_item in self._component_items.items():
            inst = comp_item.get_instance()
            if inst.definition_ref == "Register":
                continue
            if inst.position[0] > from_x_grid:
                inst.stage_position_offset += shift_grid
                inst.is_position_temporary = True
                comp_item.set_temporary(True)

        self._design.reindex_stages()
        self._rebuild_all_stages()

    def _release_composite_stage_bindings(self, composite_id: UUID) -> None:
        """Release stage bindings when a composite is removed."""
        binding = self._composite_stage_bindings.pop(composite_id, None)
        if not binding:
            return

        for stage_id in binding.values():
            stage = self._design.get_stage_by_id(stage_id)
            if stage is None or stage.additional_offset <= 0:
                continue

            still_needed = any(
                stage_id in b.values()
                for other_id, b in self._composite_stage_bindings.items()
                if other_id != composite_id
            )
            if still_needed:
                continue

            collapse = stage.additional_offset
            stage.x_position -= collapse
            stage.additional_offset = 0.0

            for comp_id, comp_item in self._component_items.items():
                inst = comp_item.get_instance()
                if inst.definition_ref == "Register":
                    continue
                if inst.stage_position_offset > 0:
                    inst.stage_position_offset = max(
                        0.0, inst.stage_position_offset - collapse
                    )
                    if inst.stage_position_offset == 0.0:
                        inst.is_position_temporary = False
                        comp_item.set_temporary(False)

        self._design.reindex_stages()
        self._rebuild_all_stages()
        self._apply_stage_position_offsets()
        self.update_connection_positions()

    def _clear_composite_drag_previews(self) -> None:
        """Remove all temporary overlay items shown during composite drag."""
        self._remove_overlays_safe(self._composite_drag_overlays)
        self._composite_drag_overlays.clear()

    def _find_nearest_stage_wide(self, x_px: float) -> Stage | None:
        """Find the nearest stage within a wider snap threshold (used during drag)."""
        if not self._design.stages:
            return None
        threshold = self._grid.to_pixels(8)
        nearest = None
        min_dist = float("inf")
        for stage in self._design.stages:
            dist = abs(self._grid.to_pixels(stage.x_position) - x_px)
            if dist < min_dist:
                min_dist = dist
                nearest = stage
        return nearest if min_dist <= threshold else None

    # ── Temporary-position helpers ────────────────────────────────────────────

    def _apply_stage_position_offsets(self) -> None:
        """Re-render every non-register instance using its effective position."""
        for comp_id, item in self._component_items.items():
            inst = item.get_instance()
            if inst.definition_ref == "Register" or inst.stage_position_offset == 0.0:
                continue
            eff_x = inst.position[0] + inst.stage_position_offset
            x_px = self._grid.to_pixels(eff_x)
            y_px = self._grid.to_pixels(inst.position[1])
            item.set_position_no_callbacks(x_px, y_px)

    def _add_temp_position_overlay(self, component_id: UUID) -> None:
        """Add (or refresh) an accepted-temporary overlay around a component."""
        item = self._component_items.get(component_id)
        if item is None:
            return

        old = self._temp_position_overlays.pop(component_id, None)
        if old:
            self._remove_overlays_safe([old])

        rect = item.sceneBoundingRect().adjusted(-5, -5, 5, 5)
        if self._last_drag_was_aligned:
            overlay = TempPositionOverlayItem(rect, "")
        else:
            overlay = TempPositionOverlayItem(
                rect,
                "Invalid location – correct it for consistency!",
                invalid=True,
            )
        self.addItem(overlay)
        self._temp_position_overlays[component_id] = overlay

    def finalize_temporary_positions(self) -> None:
        """Accept all provisional positions, clearing the temporary visual state."""
        for comp_id, overlay in list(self._temp_position_overlays.items()):
            self._remove_overlays_safe([overlay])
            item = self._component_items.get(comp_id)
            if item:
                inst = item.get_instance()
                inst.is_position_temporary = False
                if inst.stage_position_offset != 0.0:
                    inst.position = (
                        inst.position[0] + inst.stage_position_offset,
                        inst.position[1],
                    )
                    inst.stage_position_offset = 0.0
                item.set_temporary(False)
        self._temp_position_overlays.clear()

    # ── Shared reset ──────────────────────────────────────────────────────────

    def _reset_alignment_state(self) -> None:
        """Clear all alignment-related runtime state (used on design load/new).

        Must be called BEFORE QGraphicsScene.clear() so Qt C++ objects
        backing overlay items are still alive when we call removeItem.
        """
        self._moving_stage = None
        self._stage_move_mouse_start = QPointF()
        self._stage_move_stage_start_x = 0.0
        self._stage_move_new_x = 0.0
        self._stage_group_items = []
        self._stage_group_original_positions = {}
        self._in_stage_group_move = False

        self._remove_overlays_safe(self._composite_drag_overlays)
        self._composite_drag_overlays = []

        self._remove_overlays_safe(list(self._temp_position_overlays.values()))
        self._temp_position_overlays = {}

        self._composite_proposed_shifts = {}
        self._composite_stage_bindings = {}
        self._dragging_composite_id = None
        self._last_drag_was_aligned = True

    def _remove_overlays_safe(self, overlays: list) -> None:
        """Remove overlay items from the scene, tolerating already-deleted objects."""
        for overlay in overlays:
            try:
                if overlay.scene() is not None:
                    self.removeItem(overlay)
            except RuntimeError:
                pass
