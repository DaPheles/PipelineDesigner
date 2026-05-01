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
Signals are ``FixedPointArray`` objects (from the fixedpoint package) or
plain Python scalars (``float`` / ``int`` / ``bool``) for std_logic ports.
Unconnected inputs are passed as ``None``; behavior code that receives
``None`` for a required port will raise at runtime, surfacing wiring errors.

Interface ports
---------------
``Design.interface_ports`` describe the external boundary.  Call
``set_input(name, value)`` to drive them and ``get_output(name)`` to read
driven outputs after each ``step()``.
"""

from __future__ import annotations

import textwrap
from collections import deque
from typing import Any
from uuid import UUID

import numpy as np

from pipeline_designer.domain.models.behavior import FixedPointKind
from pipeline_designer.domain.models.component import ComponentDefinition
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import InterfaceDirection
from pipeline_designer.domain.simulation.executor import BehaviorExecutor


# ── Type alias ────────────────────────────────────────────────────────────────

# A signal value is whatever a BehaviorExecutor returns (FixedPointArray, scalar…)
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

    def __init__(self, design: Design, library: dict[str, ComponentDefinition]):
        self._design  = design
        self._library = library

        # UUID → ComponentDefinition
        self._inst_def: dict[UUID, ComponentDefinition] = {}
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
        iface_by_id = {ip.id: ip for ip in design.interface_ports}

        for inst in design.components:
            defn = library.get(inst.definition_ref)
            if defn is None:
                raise KeyError(
                    f"Component '{inst.definition_ref}' not found in library. "
                    f"Available: {sorted(library)}"
                )
            self._inst_def[inst.id] = defn

            if self._is_register(defn):
                self._regs.add(inst.id)
            else:
                in_ports  = [p.name for p in defn.get_input_ports()
                             if not p.is_clock and not p.is_reset]
                out_ports = [p.name for p in defn.get_output_ports()]
                self._exec_input_ports[inst.id]  = in_ports
                self._exec_output_ports[inst.id] = out_ports

                generics = inst.generic_values or {}
                self._executors[inst.id] = BehaviorExecutor(
                    code_body=defn.behavior.code,
                    param_names=in_ports,
                    name=defn.name,
                    extra_ns={"__generics__": generics},
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

        # Pre-compute zero values for register Q outputs.
        # Traces each register's Q connection to find the consumer port's
        # BehaviorPortType, then quantizes 0.0 to that format.
        # Falls back to np.float64(0.0) if the format cannot be determined.
        self._reg_zero: dict[UUID, SignalValue] = {}
        for reg_id in self._regs:
            self._reg_zero[reg_id] = self._infer_reg_zero(reg_id)

    def _infer_reg_zero(self, reg_id: UUID) -> SignalValue:
        """Return a zero signal value for a register's Q output.

        Traces the Q output to a consumer component port, reads its
        BehaviorPortType, and returns a zero FixedPointArray in that format.
        Falls back to numpy float64(0.0) if the format is unavailable.
        """
        from fixedpoint import FPFormat  # late import — optional dep

        # Find a connection driven by this register's Q output
        for (tgt_id, tgt_port), driver in self._drivers.items():
            src_id, src_port = driver
            if src_id != reg_id or src_port != "q":
                continue
            # tgt_id is the consumer; look up its BehaviorPortType
            if tgt_id == _IFACE or tgt_id not in self._inst_def:
                continue
            consumer_defn = self._inst_def[tgt_id]
            pt = consumer_defn.behavior.port_types.get(tgt_port)
            if pt is None or not pt.has_range():
                continue
            try:
                fmt = pt.to_fpformat()
                return fmt.quantize(np.array(0.0))
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
        """Clear all signals; seed register Q outputs with format-matched zeros."""
        self._signals.clear()
        self._iface_inputs.clear()
        for reg_id, zero in self._reg_zero.items():
            self._signals[(reg_id, "q")] = zero

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

    def step(self) -> None:
        """Execute one clock cycle.

        Phase 1: evaluate all combinational instances in topological order.
        Phase 2: registers latch their D input into Q.
        """
        # ── Phase 1: combinational ────────────────────────────────────────────
        for inst_id in self._topo_order:
            executor   = self._executors[inst_id]
            in_ports   = self._exec_input_ports[inst_id]
            out_ports  = self._exec_output_ports[inst_id]

            args = [self._resolve(inst_id, p) for p in in_ports]
            result = executor(*args)

            # Store outputs — single return value or tuple
            if len(out_ports) == 1:
                self._signals[(inst_id, out_ports[0])] = result
            elif len(out_ports) > 1:
                for i, name in enumerate(out_ports):
                    self._signals[(inst_id, name)] = result[i]

        # ── Phase 2: register capture ─────────────────────────────────────────
        new_q: dict[_NetKey, SignalValue] = {}
        for reg_id in self._regs:
            d_val = self._resolve(reg_id, "d")
            new_q[(reg_id, "q")] = d_val
        self._signals.update(new_q)

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
