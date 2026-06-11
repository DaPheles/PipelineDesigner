"""Design-level cycle-accurate behavioral simulator.

Given a ``Design`` (instances + connections) and a library dict
``{name: ComponentDefinition}``, this module runs the design graph
clock-by-clock using two-phase simulation:

  Phase 1 — combinational: all non-register components are evaluated in
    topological order.  Each reads the signal values produced during the
    *previous* cycle (or the initial/reset state).

  Phase 2 — clocked capture: every register instance latches its D input
    into its Q output, making those new values visible next cycle.

Register detection
------------------
A ``ComponentDefinition`` is treated as a register (flip-flop) when it has
ports named ``"d"``, ``"q"``, and ``"clk"`` (case-insensitive).  These
instances are excluded from the combinational topological sort; their Q
outputs are always pre-populated before Phase 1 runs.

Signal representation
---------------------
Signals are ``FixedPoint`` scalars (from the fixedpoint package) or plain
Python scalars (``float`` / ``int`` / ``bool``) for std_logic ports.
Unconnected inputs are passed as ``None``; behavior code that receives
``None`` for a required port will raise at runtime, surfacing wiring errors.

Interface ports
---------------
``Design.interface_ports`` describe the external boundary.  Call
``set_input(name, value)`` to drive them and ``get_output(name)`` to read
driven outputs after each ``step()``.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from uuid import UUID

import numpy as np

from pipeline_designer.utils.fixedpoint import FixedPoint, UnquantizedResult
from pipeline_designer.domain.models.component import ComponentDefinition, PortSignalClass
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import InterfaceDirection
from pipeline_designer.domain.simulation.executor import BehaviorExecutor


# ── Type alias ────────────────────────────────────────────────────────────────

# A signal value is whatever a BehaviorExecutor returns (FixedPoint scalar, float, bool…)
SignalValue = Any

# Signal net key: (instance_uuid, port_name)
_NetKey = tuple[UUID, str]

# Sentinel used in the driver map for interface-driven signals
_IFACE = "__interface__"


# ── DesignSimulator ───────────────────────────────────────────────────────────

class DesignSimulator:
    """Cycle-accurate behavioral simulator for a Design graph.

    Parameters
    ----------
    design  : the Design to simulate
    library : flat dict of {component_name: ComponentDefinition}
               (as returned by LibraryLoader or built manually in tests)

    Usage
    -----
    sim = DesignSimulator(design, library)
    sim.reset()
    for sample in samples:
        sim.set_input("x_in", fmt_in.quantize(np.array(sample)))
        sim.step()
        y = sim.get_output("y_out")
    """

    def __init__(
        self,
        design: Design,
        library: dict[str, ComponentDefinition],
        float_mode: bool = False,
    ):
        self._design      = design
        self._library     = library
        self._float_mode  = float_mode

        # UUID → ComponentDefinition
        self._inst_def: dict[UUID, ComponentDefinition] = {}
        # UUID → human-readable instance label for error messages
        self._inst_label: dict[UUID, str] = {}
        # UUID → resolved generic values for each instance
        self._inst_generics: dict[UUID, dict[str, Any]] = {}
        # UUIDs of register instances (two-phase semantics)
        self._regs: set[UUID] = set()
        # UUID → BehaviorExecutor (only for non-register instances)
        self._executors: dict[UUID, BehaviorExecutor] = {}
        # UUID → ordered list of input port names for the executor call
        self._exec_input_ports: dict[UUID, list[str]] = {}
        # UUID → ordered list of output port names
        self._exec_output_ports: dict[UUID, list[str]] = {}

        # Connection tables
        # (target_id, port_name) → (source_id, port_name) | (_IFACE, iface_name)
        self._drivers: dict[_NetKey, tuple[str | UUID, str]] = {}
        # interface_name → (source_id, port_name)  for output interface ports
        self._iface_out_src: dict[str, _NetKey] = {}
        # interface_name → interface port id  (for display / debugging)
        self._iface_in_names: set[str] = set()

        # Signal state — written by phase 1 (comb) and phase 2 (registers)
        self._signals: dict[_NetKey, SignalValue] = {}
        # Interface input values set by the test driver
        self._iface_inputs: dict[str, SignalValue] = {}

        self._build(design, library)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self, design: Design, library: dict[str, ComponentDefinition]) -> None:
        from pipeline_designer.domain.models.behavior import _eval_index, _substitute_generics

        iface_by_id = {ip.id: ip for ip in design.interface_ports}

        # Outer design generics that are concrete integers — used to evaluate
        # string-valued instance generics like "WIDTH+5" or "LSB".
        outer_concrete: dict[str, int] = {
            g.name: int(g.default_value)
            for g in design.component_config.generics
            if g.default_value is not None
            and isinstance(g.default_value, (int, float))
            and not isinstance(g.default_value, bool)
        }

        for inst in design.components:
            defn = library.get(inst.definition_ref)
            if defn is None:
                raise KeyError(
                    f"Component '{inst.definition_ref}' not found in library. "
                    f"Available: {sorted(library)}"
                )
            self._inst_def[inst.id]   = defn
            self._inst_label[inst.id] = inst.get_display_name()

            # Merge component-definition defaults with per-instance overrides so
            # that generics like H0/WIDTH are always present by name in the
            # executor namespace even when the instance uses all defaults.
            resolved = {g.name: g.default_value for g in defn.generics}
            resolved.update(inst.generic_values or {})

            # Evaluate any string-valued generics (e.g. "WIDTH+5", "LSB") against
            # the outer design's concrete defaults so behavior code receives ints.
            for name, val in list(resolved.items()):
                if isinstance(val, str):
                    try:
                        resolved[name] = _eval_index(
                            _substitute_generics(val, outer_concrete), outer_concrete
                        )
                    except (ValueError, KeyError):
                        pass

            self._inst_generics[inst.id] = resolved

            if self._is_register(defn):
                self._regs.add(inst.id)

            in_ports  = [p.name for p in defn.get_input_ports()
                         if p.signal_class not in (PortSignalClass.CLOCK, PortSignalClass.RESET)]
            out_ports = [p.name for p in defn.get_output_ports()]
            self._exec_input_ports[inst.id]  = in_ports
            self._exec_output_ports[inst.id] = out_ports

            if defn.behavior is not None:
                # In float/ideal mode prefer ideal_code when the primitive
                # provides one (e.g. Adder_Carry which uses bit operations).
                code_body = (
                    defn.behavior.ideal_code
                    if self._float_mode and defn.behavior.ideal_code
                    else defn.behavior.code
                )
                self._executors[inst.id] = BehaviorExecutor(
                    code_body=code_body,
                    param_names=in_ports,
                    name=defn.name,
                    extra_ns=resolved,
                    float_mode=self._float_mode,
                )

        # Build interface name sets
        for ip in design.interface_ports:
            if ip.direction == InterfaceDirection.INPUT:
                self._iface_in_names.add(ip.name)

        # Build driver map from connections
        for conn in design.connections:
            src = conn.source
            tgt = conn.target

            # Determine driver key
            if src.is_component_port():
                driver: tuple[str | UUID, str] = (src.component_id, src.port_name)
            else:
                # Source is an interface port
                ip = iface_by_id.get(src.interface_port_id)
                iname = ip.name if ip else src.port_name
                driver = (_IFACE, iname)

            # Assign to target
            if tgt.is_component_port():
                self._drivers[(tgt.component_id, tgt.port_name)] = driver
            else:
                # Target is an interface port (output side)
                ip = iface_by_id.get(tgt.interface_port_id)
                iname = ip.name if ip else tgt.port_name
                if src.is_component_port():
                    self._iface_out_src[iname] = (src.component_id, src.port_name)

        # Topological sort of combinational nodes
        self._topo_order = self._topo_sort()

        # Per-(instance, port) output FIFO for latency > 0 components.
        # Each deque accumulates computed results; the output visible this cycle
        # is the oldest entry once the buffer is full (depth == latency).
        self._latency_buffers: dict[_NetKey, deque] = {}
        for inst_id in self._topo_order:
            defn = self._inst_def[inst_id]
            if defn.latency > 0:
                for port in defn.get_output_ports():
                    self._latency_buffers[(inst_id, port.name)] = deque()

        # Pre-compute zero values for each register output port.
        # Keyed by (reg_id, out_port_name) so any port layout is supported.
        self._reg_zero: dict[_NetKey, SignalValue] = {}
        for reg_id in self._regs:
            for out_port in self._exec_output_ports[reg_id]:
                self._reg_zero[(reg_id, out_port)] = self._infer_reg_zero(reg_id, out_port)

    def _infer_reg_zero(self, reg_id: UUID, out_port: str) -> SignalValue:
        """Return a zero signal value for one output port of a register.

        In float mode always returns plain 0.0.  In fixed-point mode traces the
        output to a consumer port, reads its SignalType, and quantizes 0.0 to
        that format.  Falls back to numpy float64(0.0) if unavailable.
        """
        if self._float_mode:
            return np.float64(0.0)

        for (tgt_id, tgt_port), driver in self._drivers.items():
            src_id, src_port = driver
            if src_id != reg_id or src_port != out_port:
                continue
            if tgt_id == _IFACE or tgt_id not in self._inst_def:
                continue
            consumer_defn = self._inst_def[tgt_id]
            port = consumer_defn.get_port_by_name(tgt_port)
            if port is None:
                continue
            generics = {
                k: v for k, v in self._inst_generics.get(tgt_id, {}).items()
                if isinstance(v, (int, float))
            }
            if not port.signal_type.has_range(generics):
                continue
            try:
                return port.signal_type.to_fpformat(generics).zero()
            except Exception:
                continue

        return np.float64(0.0)

    @staticmethod
    def _is_register(defn: ComponentDefinition) -> bool:
        """True if this definition behaves like a D flip-flop."""
        names = {p.name.lower() for p in defn.ports}
        return {"d", "q", "clk"}.issubset(names)

    def _topo_sort(self) -> list[UUID]:
        """Kahn's topological sort of non-register component instances.

        Registers and unresolved (no-code) components are excluded; their
        Q outputs are pre-seeded and read as constants during phase 1.
        """
        comb_ids = [
            inst.id for inst in self._design.components
            if inst.id not in self._regs
        ]
        comb_set = set(comb_ids)

        # in_degree counts how many other comb nodes drive this node's inputs
        in_degree: dict[UUID, int] = {iid: 0 for iid in comb_ids}
        # successors: for each comb node, which comb nodes does it feed?
        successors: dict[UUID, list[UUID]] = {iid: [] for iid in comb_ids}

        for iid in comb_ids:
            for port_name in self._exec_input_ports.get(iid, []):
                driver = self._drivers.get((iid, port_name))
                if driver is None:
                    continue
                src_id = driver[0]
                if src_id in comb_set:
                    in_degree[iid] += 1
                    successors[src_id].append(iid)

        queue: deque[UUID] = deque(
            iid for iid in comb_ids if in_degree[iid] == 0
        )
        order: list[UUID] = []
        while queue:
            iid = queue.popleft()
            order.append(iid)
            for succ in successors[iid]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(order) != len(comb_ids):
            remaining = set(comb_ids) - set(order)
            names = [self._inst_def[r].name for r in remaining]
            raise ValueError(
                f"Combinational cycle detected among: {names}. "
                "Insert a register to break the cycle."
            )

        return order

    # ── Runtime ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all signals, executor state, and seed register outputs with zeros."""
        self._signals.clear()
        self._iface_inputs.clear()
        for (reg_id, out_port), zero in self._reg_zero.items():
            self._signals[(reg_id, out_port)] = zero
        for executor in self._executors.values():
            executor.reset_state()
        for (inst_id, _port_name), buf in self._latency_buffers.items():
            buf.clear()
            buf.extend([np.float64(0.0)] * self._inst_def[inst_id].latency)

    def set_input(self, name: str, value: SignalValue) -> None:
        """Set an interface input port value for the next ``step()`` call."""
        self._iface_inputs[name] = value

    def get_output(self, name: str) -> SignalValue | None:
        """Read an interface output port value (valid after ``step()``)."""
        src = self._iface_out_src.get(name)
        if src is None:
            return None
        return self._signals.get(src)

    def get_signal(self, instance_id: UUID, port_name: str) -> SignalValue | None:
        """Read any internal signal by (instance_id, port_name)."""
        return self._signals.get((instance_id, port_name))

    def _is_rst_active(self, reg_id: UUID) -> bool:
        """Return True if the reset input of reg_id is currently asserted."""
        rst_val = self._resolve(reg_id, "rst")
        if rst_val is None:
            return False
        polarity = str(self._inst_generics[reg_id].get("RESET_POLARITY", "high")).lower()
        active = bool(rst_val)
        return active if polarity == "high" else not active

    def step(self) -> None:
        """Execute one clock cycle.

        Async-reset pre-pass: registers with RESET_TYPE=async whose rst is
        asserted have their Q pre-seeded to zero before Phase 1 runs, so that
        combinational logic downstream sees the reset value immediately.

        Phase 1: evaluate all combinational instances in topological order.

        Phase 2: registers latch D→Q (or Q→0 on sync reset).
        """
        # ── Async reset pre-pass ──────────────────────────────────────────────
        for reg_id in self._regs:
            reset_type = str(self._inst_generics[reg_id].get("RESET_TYPE", "sync")).lower()
            if reset_type == "async" and self._is_rst_active(reg_id):
                for out_port in self._exec_output_ports[reg_id]:
                    self._signals[(reg_id, out_port)] = self._reg_zero[(reg_id, out_port)]

        # ── Phase 1: combinational ────────────────────────────────────────────
        for inst_id in self._topo_order:
            executor   = self._executors[inst_id]
            in_ports   = self._exec_input_ports[inst_id]
            out_ports  = self._exec_output_ports[inst_id]
            latency    = self._inst_def[inst_id].latency

            args = [self._resolve(inst_id, p) for p in in_ports]
            try:
                result = executor(*args)
            except Exception as exc:
                label = self._inst_label.get(inst_id, str(inst_id))
                raise type(exc)(f"[{label}] {exc}") from exc

            # Unpack result into per-port dict first
            raw: dict[str, Any] = {}
            if len(out_ports) == 1:
                raw[out_ports[0]] = result
            elif len(out_ports) > 1:
                for i, pname in enumerate(out_ports):
                    raw[pname] = result[i]

            # Apply latency buffering when declared; otherwise pass through
            for pname, val in raw.items():
                val = self._quantize_signal(val, inst_id, pname)
                key = (inst_id, pname)
                if latency > 0:
                    buf = self._latency_buffers[key]
                    self._signals[key] = buf.popleft()
                    buf.append(val)
                else:
                    self._signals[key] = val

        # ── Phase 2: register capture ─────────────────────────────────────────
        new_q: dict[_NetKey, SignalValue] = {}
        for reg_id in self._regs:
            out_ports = self._exec_output_ports[reg_id]
            if self._is_rst_active(reg_id):
                for out_port in out_ports:
                    new_q[(reg_id, out_port)] = self._reg_zero[(reg_id, out_port)]
            else:
                executor = self._executors.get(reg_id)
                if executor is not None:
                    in_ports = self._exec_input_ports[reg_id]
                    args = [self._resolve(reg_id, p) for p in in_ports]
                    try:
                        result = executor(*args)
                    except Exception as exc:
                        label = self._inst_label.get(reg_id, str(reg_id))
                        raise type(exc)(f"[{label}] {exc}") from exc
                    if len(out_ports) == 1:
                        new_q[(reg_id, out_ports[0])] = self._quantize_signal(result, reg_id, out_ports[0])
                    else:
                        for i, pname in enumerate(out_ports):
                            new_q[(reg_id, pname)] = self._quantize_signal(result[i], reg_id, pname)
                else:
                    # Fallback: no behavior code — pass data inputs to outputs positionally
                    in_ports = self._exec_input_ports[reg_id]
                    for i, out_port in enumerate(out_ports):
                        new_q[(reg_id, out_port)] = self._resolve(reg_id, in_ports[i]) if i < len(in_ports) else None
        self._signals.update(new_q)

    def _quantize_signal(self, val: Any, inst_id: UUID, port_name: str) -> Any:
        """Quantize an executor output to its port's fixed-point format.

        Only active in fixed-point mode.  Passes through values unchanged in
        float mode or when the port format cannot be determined (e.g. std_logic).
        """
        if self._float_mode:
            return val
        defn = self._inst_def[inst_id]
        port_obj = next((p for p in defn.get_output_ports() if p.name == port_name), None)
        if port_obj is None:
            return val
        try:
            fmt = port_obj.signal_type.to_fpformat(self._inst_generics[inst_id])
        except Exception:
            return val
        if isinstance(val, UnquantizedResult):
            return val.quantize(fmt)
        if isinstance(val, (FixedPoint, int, float, np.floating, np.integer)):
            return fmt.quantize(np.array(float(val)))
        return val

    def _resolve(self, consumer_id: UUID, port_name: str) -> SignalValue | None:
        """Look up the current value driving (consumer_id, port_name)."""
        key = (consumer_id, port_name)
        driver = self._drivers.get(key)
        if driver is None:
            return None
        src_id, src_port = driver
        if src_id == _IFACE:
            return self._iface_inputs.get(src_port)
        return self._signals.get((src_id, src_port))

    # ── Introspection ─────────────────────────────────────────────────────────

    def describe(self) -> str:
        """Return a human-readable summary of the compiled simulator."""
        lines = [f"DesignSimulator: {self._design.name}"]
        lines.append(f"  Instances   : {len(self._design.components)}")
        lines.append(f"  Registers   : {len(self._regs)}")
        lines.append(f"  Comb nodes  : {len(self._topo_order)}")
        lines.append(f"  Topo order  : "
                     + " → ".join(self._inst_def[i].name for i in self._topo_order))
        lines.append(f"  Connections : {len(self._design.connections)}")
        lines.append(f"  Inputs      : {sorted(self._iface_in_names)}")
        lines.append(f"  Outputs     : {sorted(self._iface_out_src)}")
        return "\n".join(lines)
