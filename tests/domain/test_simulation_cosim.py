"""Co-simulation tests: float/ideal vs fixed-point DesignSimulator runs.

Each test builds a minimal Design + library dict in-memory, runs the
DesignSimulator in both float_mode=True and float_mode=False, and asserts that:

  1. The float result matches the mathematical expectation (ideal).
  2. The fixed-point result shows quantization effects (diverges from ideal
     when the format is narrow enough to introduce rounding/truncation).
  3. The two results are numerically distinct for scenarios where
     quantization makes a material difference.

No Qt, no filesystem, no network access — pure domain-layer tests.

FPFormat.quantize() signature (fixedpoint library):
    quantize(x: np.ndarray, round: RoundMode = 'truncate', saturate: SaturateMode = 'saturate')
"""

from __future__ import annotations

import math
from uuid import uuid4

import numpy as np
import pytest

from pipeline_designer.domain.models.behavior import ComponentBehavior, SignalType
from pipeline_designer.domain.models.component import (
    ComponentDefinition,
    Generic,
    Port,
    PortDirection,
    PortSignalClass,
    VisualConfig,
)
from pipeline_designer.domain.models.design import ComponentConfig, Design
from pipeline_designer.domain.models.instance import (
    ComponentInstance,
    Connection,
    InterfaceDirection,
    InterfacePort,
    PortReference,
)
from pipeline_designer.domain.simulation.graph_sim import DesignSimulator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sfixed_type(width: int, lsb: int) -> SignalType:
    return SignalType(kind="sfixed", width=str(width), lsb=str(lsb))


def _ufixed_type(width: int, lsb: int) -> SignalType:
    return SignalType(kind="ufixed", width=str(width), lsb=str(lsb))


def _iport(name: str, sig: SignalType) -> InterfacePort:
    return InterfacePort(
        name=name,
        direction=InterfaceDirection.INPUT,
        data_type=sig.kind,
        signal_type=sig,
    )


def _oport(name: str, sig: SignalType) -> InterfacePort:
    return InterfacePort(
        name=name,
        direction=InterfaceDirection.OUTPUT,
        data_type=sig.kind,
        signal_type=sig,
    )


def _connect_in(iface: InterfacePort, inst_id, port_name: str) -> Connection:
    return Connection(
        source=PortReference(interface_port_id=iface.id, port_name=iface.name),
        target=PortReference(component_id=inst_id, port_name=port_name),
    )


def _connect_out(inst_id, port_name: str, iface: InterfacePort) -> Connection:
    return Connection(
        source=PortReference(component_id=inst_id, port_name=port_name),
        target=PortReference(interface_port_id=iface.id, port_name=iface.name),
    )


def _quantize_for_fixed(value: float, port: InterfacePort) -> float:
    """Pre-quantize a float to the nearest representable value for the port's format."""
    if port.signal_type is None:
        return float(value)
    try:
        return float(port.signal_type.to_fpformat().quantize(np.array(float(value))))
    except Exception:
        return float(value)


def _to_float(v) -> float | None:
    """Extract a plain Python float from any simulator output value."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_both(design: Design, library: dict, inputs: dict, n_cycles: int = 1):
    """Run float and fixed-point simulators; return per-output per-cycle lists.

    Float sim:  raw Python floats (unlimited precision, no saturation).
    Fixed sim:  pre-quantized Python floats (rounded to port precision).
                Output signals are then quantized to port format by
                DesignSimulator._quantize_signal, giving the hardware effect.
    """
    out_names  = [p.name for p in design.get_output_interfaces()]
    in_by_name = {p.name: p for p in design.get_input_interfaces()}

    sim_f = DesignSimulator(design, library, float_mode=True)
    sim_x = DesignSimulator(design, library, float_mode=False)
    sim_f.reset()
    sim_x.reset()

    float_history: dict[str, list] = {n: [] for n in out_names}
    fixed_history: dict[str, list] = {n: [] for n in out_names}

    for cyc in range(n_cycles):
        for name, vals in inputs.items():
            v = vals[cyc] if cyc < len(vals) else vals[-1]
            port = in_by_name.get(name)
            sim_f.set_input(name, float(v))
            sim_x.set_input(name, _quantize_for_fixed(float(v), port) if port else float(v))

        sim_f.step()
        sim_x.step()

        for name in out_names:
            vf = sim_f.get_output(name)
            vx = sim_x.get_output(name)
            float_history[name].append(_to_float(vf))
            fixed_history[name].append(_to_float(vx))

    return float_history, fixed_history


def _run_float_only(design: Design, library: dict, inputs: dict, n_cycles: int = 1):
    """Run the float/ideal simulator only; return per-output per-cycle dict."""
    out_names = [p.name for p in design.get_output_interfaces()]
    sim_f = DesignSimulator(design, library, float_mode=True)
    sim_f.reset()
    history: dict[str, list] = {n: [] for n in out_names}
    for cyc in range(n_cycles):
        for name, vals in inputs.items():
            v = vals[cyc] if cyc < len(vals) else vals[-1]
            sim_f.set_input(name, float(v))
        sim_f.step()
        for name in out_names:
            history[name].append(_to_float(sim_f.get_output(name)))
    return history


# ── Test 1: Simple adder — no quantization error ──────────────────────────────

class TestAdderCoSim:
    """Adder with wide output: both modes should agree (no truncation)."""

    def _make(self):
        defn = ComponentDefinition(
            name="Adder",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="sum", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(17, -8), signal_class=PortSignalClass.DATA),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return a + b"),
        )
        inst = ComponentInstance(definition_ref="Adder")
        ia = _iport("a", _sfixed_type(16, -8))
        ib = _iport("b", _sfixed_type(16, -8))
        oy = _oport("sum", _sfixed_type(17, -8))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, ib, oy],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_in(ib, inst.id, "b"),
                _connect_out(inst.id, "sum", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Adder": defn}

    def test_float_matches_ideal(self):
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"a": [0.5], "b": [0.25]})
        assert math.isclose(fh["sum"][0], 0.75, rel_tol=1e-9)

    def test_fixed_matches_ideal_wide_format(self):
        """sfixed(17,-8) LSB = 2^-8 ≈ 0.004 — 0.75 is representable exactly."""
        design, lib = self._make()
        _, xh = _run_both(design, lib, {"a": [0.5], "b": [0.25]})
        assert math.isclose(xh["sum"][0], 0.75, abs_tol=2 ** -8)

    def test_float_equals_fixed_for_representable_values(self):
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"a": [0.5], "b": [0.25]})
        assert math.isclose(fh["sum"][0], xh["sum"][0], rel_tol=1e-6)


# ── Test 2: Multiplier truncation — modes diverge ─────────────────────────────

class TestMultiplierTruncation:
    """Multiplier with a narrow output format that truncates the exact product."""

    def _make(self, prod_lsb: int = -4):
        # sfixed(16,-8) * sfixed(16,-8) exact product lives in sfixed(32,-16);
        # narrow prod_lsb=-4 (step=0.0625) discards precision → visible error.
        defn = ComponentDefinition(
            name="Mult",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="prod", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(16, prod_lsb), signal_class=PortSignalClass.DATA),
            ],
            generics=[Generic(name="PROD_LSB", data_type="integer", default_value=prod_lsb)],
            visual=VisualConfig(),
            behavior=ComponentBehavior(
                # FPFormat.quantize(x, round, saturate) — defaults truncate+saturate
                code=(
                    "fmt = SFixed(15, PROD_LSB)\n"
                    "return fmt.quantize(np.array(a * b))"
                ),
            ),
        )
        inst = ComponentInstance(definition_ref="Mult")
        ia = _iport("a", _sfixed_type(16, -8))
        ib = _iport("b", _sfixed_type(16, -8))
        oy = _oport("prod", _sfixed_type(16, prod_lsb))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, ib, oy],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_in(ib, inst.id, "b"),
                _connect_out(inst.id, "prod", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Mult": defn}

    def test_float_gives_exact_product(self):
        design, lib = self._make(prod_lsb=-4)
        fh, _ = _run_both(design, lib, {"a": [0.3], "b": [0.7]})
        assert math.isclose(fh["prod"][0], 0.3 * 0.7, rel_tol=1e-9)

    def test_fixed_truncates_product(self):
        design, lib = self._make(prod_lsb=-4)
        _, xh = _run_both(design, lib, {"a": [0.3], "b": [0.7]})
        # LSB = 2^-4 = 0.0625; exact product 0.21 → truncated to 0.1875
        step = 2 ** -4
        expected = math.floor(0.3 * 0.7 / step) * step
        assert math.isclose(xh["prod"][0], expected, abs_tol=step * 0.5)

    def test_float_and_fixed_diverge(self):
        design, lib = self._make(prod_lsb=-4)
        fh, xh = _run_both(design, lib, {"a": [0.3], "b": [0.7]})
        assert not math.isclose(fh["prod"][0], xh["prod"][0], abs_tol=1e-6)

    def test_float_and_fixed_agree_on_representable_product(self):
        """When the exact product is representable the two modes agree."""
        design, lib = self._make(prod_lsb=-8)  # step = 2^-8
        # 0.5 * 0.5 = 0.25, representable in sfixed(16,-8)
        fh, xh = _run_both(design, lib, {"a": [0.5], "b": [0.5]})
        assert math.isclose(fh["prod"][0], 0.25, rel_tol=1e-9)
        assert math.isclose(xh["prod"][0], 0.25, abs_tol=2 ** -8)


# ── Test 3: FP_S2U — clamping and quantization ────────────────────────────────

class TestFpS2U:
    """Signed→unsigned conversion.

    Ideal (float): real_min/max = ±inf → no clamping, no quantization.
    Fixed:         clamped to output format range and quantized.
    """

    _CODE = (
        "q_fmt  = UFixed(Q_WIDTH + Q_LSB - 1, Q_LSB)\n"
        "a_real = float(a)\n"
        "if CLAMP_EN:\n"
        "    a_real = max(q_fmt.real_min, min(q_fmt.real_max, a_real))\n"
        "return q_fmt.quantize(np.array(a_real))\n"
    )

    def _make(self, q_width: int = 8, q_lsb: int = -6, clamp: int = 1):
        defn = ComponentDefinition(
            name="FP_S2U",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="q", direction=PortDirection.OUT,
                     signal_type=_ufixed_type(q_width, q_lsb), signal_class=PortSignalClass.DATA),
            ],
            generics=[
                Generic(name="Q_WIDTH",  data_type="integer", default_value=q_width),
                Generic(name="Q_LSB",    data_type="integer", default_value=q_lsb),
                Generic(name="CLAMP_EN", data_type="integer", default_value=clamp),
            ],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code=self._CODE),
        )
        inst = ComponentInstance(definition_ref="FP_S2U")
        ia = _iport("a", _sfixed_type(16, -8))
        oy = _oport("q", _ufixed_type(q_width, q_lsb))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, oy],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_out(inst.id, "q", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"FP_S2U": defn}

    def test_negative_clamped_to_zero_in_fixed_not_float(self):
        """Fixed mode clamps negative to 0; float mode is ideal (no clamping)."""
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"a": [-0.5]})
        # Ideal (float): UFixed fmt has real_min=-inf → no clamping → passes through
        assert math.isclose(fh["q"][0], -0.5, abs_tol=1e-9)
        # Fixed: UFixed fmt has real_min=0 → clamped to 0
        assert math.isclose(xh["q"][0], 0.0, abs_tol=2 ** -6)

    def test_float_passes_through_without_quantization(self):
        """Float mode: 0.3 is returned exactly (real_max=inf, quantize=no-op)."""
        design, lib = self._make(q_lsb=-2)  # step = 0.25 — coarse
        fh, xh = _run_both(design, lib, {"a": [0.3]})
        assert math.isclose(fh["q"][0], 0.3, rel_tol=1e-9)  # ideal: exact
        # fixed: input is FixedPointArray → quantized, then output quantized too
        assert math.isclose(xh["q"][0], 0.25, abs_tol=0.25)

    def test_float_and_fixed_diverge_on_non_representable(self):
        """The key property: for a coarse output format the two differ."""
        design, lib = self._make(q_lsb=-2)
        fh, xh = _run_both(design, lib, {"a": [0.3]})
        assert not math.isclose(fh["q"][0], xh["q"][0], abs_tol=1e-6)

    def test_float_ideal_ignores_saturation_clamp(self):
        """Float mode real_max=+inf — values beyond output range pass through."""
        # q_width=3: UFixed(2, 0) → int_bits=3, frac_bits=0 → range [0, 7]
        # 10.0 > 7 so fixed mode saturates; float mode is unbounded
        design, lib = self._make(q_width=3, q_lsb=0)
        fh, xh = _run_both(design, lib, {"a": [10.0]})
        assert math.isclose(fh["q"][0], 10.0, rel_tol=1e-9)   # no clamping in ideal
        assert xh["q"][0] <= 7.5                                # saturated to 7


# ── Test 4: FIR4Tap — ideal gives exact convolution ──────────────────────────

class TestFIR4TapCoSim:
    """FIR filter: float gives exact convolution; fixed shows quantization."""

    _CODE = (
        "if 'buf' not in state:\n"
        "    state['buf'] = [0.0, 0.0, 0.0, 0.0]\n"
        "state['buf'] = [float(x)] + state['buf'][:3]\n"
        "return H0 * state['buf'][0] + H1 * state['buf'][1] + "
        "H2 * state['buf'][2] + H3 * state['buf'][3]\n"
    )

    def _make(self, h0=0.1, h1=0.4, h2=0.4, h3=0.1):
        defn = ComponentDefinition(
            name="FIR4Tap",
            ports=[
                Port(name="x", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -15), signal_class=PortSignalClass.DATA),
                Port(name="y", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(18, -15), signal_class=PortSignalClass.DATA),
            ],
            generics=[
                Generic(name="H0", data_type="float", default_value=h0),
                Generic(name="H1", data_type="float", default_value=h1),
                Generic(name="H2", data_type="float", default_value=h2),
                Generic(name="H3", data_type="float", default_value=h3),
            ],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code=self._CODE),
            latency=2,
        )
        inst = ComponentInstance(definition_ref="FIR4Tap")
        ix = _iport("x", _sfixed_type(16, -15))
        oy = _oport("y", _sfixed_type(18, -15))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ix, oy],
            connections=[
                _connect_in(ix, inst.id, "x"),
                _connect_out(inst.id, "y", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"FIR4Tap": defn}

    def test_float_steady_state_is_exact(self):
        """Float: constant 1.0 → steady-state = sum(taps) = 1.0."""
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"x": [1.0] * 8}, n_cycles=8)
        y_last = fh["y"][-1]
        assert y_last is not None
        assert math.isclose(y_last, 0.1 + 0.4 + 0.4 + 0.1, rel_tol=1e-9)

    def test_fixed_steady_state_close_but_may_differ(self):
        """Fixed: steady-state close to 1.0 despite quantization."""
        design, lib = self._make()
        _, xh = _run_both(design, lib, {"x": [1.0] * 8}, n_cycles=8)
        y_last = xh["y"][-1]
        assert y_last is not None
        assert abs(y_last - 1.0) < 0.01

    def test_float_precise_non_unity_sum(self):
        """Float: asymmetric taps — output matches Python arithmetic exactly."""
        design, lib = self._make(h0=0.25, h1=0.5, h2=0.2, h3=0.05)
        fh, _ = _run_both(design, lib, {"x": [1.0] * 8}, n_cycles=8)
        expected = 0.25 + 0.5 + 0.2 + 0.05  # = 1.0 but with different taps
        assert math.isclose(fh["y"][-1], expected, rel_tol=1e-9)


# ── Test 5: FP_S2S — narrow output, ideal keeps full value ───────────────────

class TestFpS2SNarrowOutput:
    """FP_S2S with narrow output: float keeps full value, fixed saturates."""

    _CODE = (
        "q_fmt  = SFixed(Q_WIDTH + Q_LSB - 1, Q_LSB)\n"
        "a_real = float(a)\n"
        "if CLAMP_EN:\n"
        "    a_real = max(q_fmt.real_min, min(q_fmt.real_max, a_real))\n"
        "return q_fmt.quantize(np.array(a_real))\n"
    )

    def _make(self):
        # Input sfixed(16,-8) ≈ range [-128,128); output sfixed(4,0) range [-8,7]
        defn = ComponentDefinition(
            name="FP_S2S",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="q", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(4, 0), signal_class=PortSignalClass.DATA),
            ],
            generics=[
                Generic(name="Q_WIDTH",  data_type="integer", default_value=4),
                Generic(name="Q_LSB",    data_type="integer", default_value=0),
                Generic(name="CLAMP_EN", data_type="integer", default_value=1),
            ],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code=self._CODE),
        )
        inst = ComponentInstance(definition_ref="FP_S2S")
        ia = _iport("a", _sfixed_type(16, -8))
        oy = _oport("q", _sfixed_type(4, 0))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, oy],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_out(inst.id, "q", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"FP_S2S": defn}

    def test_float_passes_value_beyond_output_range(self):
        """Ideal: real_max=+inf so 20.0 is not clamped."""
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"a": [20.0]})
        assert math.isclose(fh["q"][0], 20.0, rel_tol=1e-9)

    def test_fixed_saturates_to_output_max(self):
        """Fixed: 20.0 > sfixed(4,0).max=7 → saturated to 7."""
        design, lib = self._make()
        _, xh = _run_both(design, lib, {"a": [20.0]})
        assert math.isclose(xh["q"][0], 7.0, abs_tol=0.5)

    def test_divergence_on_out_of_range_input(self):
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"a": [20.0]})
        assert not math.isclose(fh["q"][0], xh["q"][0], abs_tol=1e-6)

    def test_in_range_value_agrees_in_both(self):
        """For inputs within output range both modes agree (within LSB)."""
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"a": [3.0]})
        assert math.isclose(fh["q"][0], 3.0, abs_tol=1e-9)
        assert math.isclose(xh["q"][0], 3.0, abs_tol=0.5)


# ── Test 6: Register chain — latency respected in both modes ──────────────────

class TestRegisterChain:
    """Adder → Register: register latches in phase 2 of the same cycle.

    After step 0:  adder.sum=3 → register.q=3 (latched in phase 2)
    After step 1:  adder.sum=0 (a=b=0) → register.q=0
    """

    def _make(self):
        adder_defn = ComponentDefinition(
            name="Adder",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="sum", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(17, -8), signal_class=PortSignalClass.DATA),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return a + b"),
        )
        reg_defn = ComponentDefinition(
            name="Register",
            ports=[
                Port(name="clk", direction=PortDirection.IN,
                     signal_type=SignalType(kind="std_logic", width="1", lsb="0"),
                     signal_class=PortSignalClass.CLOCK),
                Port(name="d", direction=PortDirection.IN,
                     signal_type=_sfixed_type(17, -8), signal_class=PortSignalClass.DATA),
                Port(name="q", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(17, -8), signal_class=PortSignalClass.DATA),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return d"),
        )
        adder_inst = ComponentInstance(definition_ref="Adder")
        reg_inst   = ComponentInstance(definition_ref="Register")
        ia = _iport("a", _sfixed_type(16, -8))
        ib = _iport("b", _sfixed_type(16, -8))
        oy = _oport("q", _sfixed_type(17, -8))
        design = Design(
            name="test",
            components=[adder_inst, reg_inst],
            interface_ports=[ia, ib, oy],
            connections=[
                _connect_in(ia, adder_inst.id, "a"),
                _connect_in(ib, adder_inst.id, "b"),
                Connection(
                    source=PortReference(component_id=adder_inst.id, port_name="sum"),
                    target=PortReference(component_id=reg_inst.id,   port_name="d"),
                ),
                _connect_out(reg_inst.id, "q", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Adder": adder_defn, "Register": reg_defn}

    def test_output_is_latched_same_cycle_float(self):
        """Phase 2 latches adder output in step 0 → q[0]=3."""
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"a": [1.0, 0.0], "b": [2.0, 0.0]}, n_cycles=2)
        assert math.isclose(fh["q"][0], 3.0, rel_tol=1e-9)

    def test_output_is_latched_same_cycle_fixed(self):
        design, lib = self._make()
        _, xh = _run_both(design, lib, {"a": [1.0, 0.0], "b": [2.0, 0.0]}, n_cycles=2)
        assert math.isclose(xh["q"][0], 3.0, rel_tol=1e-6)

    def test_register_tracks_adder_output_float(self):
        """Multi-cycle: register tracks adder; both modes agree for exact values."""
        inputs = {"a": [1.0, 2.0, 3.0, 4.0], "b": [0.0, 0.0, 0.0, 0.0]}
        design, lib = self._make()
        fh, xh = _run_both(design, lib, inputs, n_cycles=4)
        # float mode: exact
        for cyc, expected in enumerate([1.0, 2.0, 3.0, 4.0]):
            assert math.isclose(fh["q"][cyc], expected, rel_tol=1e-9)
        # fixed mode: same values are exactly representable in sfixed(17,-8)
        for cyc, expected in enumerate([1.0, 2.0, 3.0, 4.0]):
            assert math.isclose(xh["q"][cyc], expected, rel_tol=1e-6)


# ── Test 7: Adder_Carry ideal_code ─────────────────────────────────────────────

class TestAdderCarryIdealCode:
    """Adder_Carry uses ideal_code in float mode.

    ideal_code: infinite precision, no carry out.
    fixed code: bit masking + carry extraction (requires int-compatible inputs).
    """

    def _make(self):
        defn = ComponentDefinition(
            name="Adder_Carry",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(8, 0), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(8, 0), signal_class=PortSignalClass.DATA),
                Port(name="cin", direction=PortDirection.IN,
                     signal_type=_sfixed_type(1, 0), signal_class=PortSignalClass.DATA),
                Port(name="sum", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(9, 0), signal_class=PortSignalClass.DATA),
                Port(name="cout", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(1, 0), signal_class=PortSignalClass.DATA),
            ],
            generics=[
                Generic(name="WIDTH", data_type="natural", default_value=8),
                Generic(name="LSB",   data_type="integer", default_value=0),
            ],
            visual=VisualConfig(),
            behavior=ComponentBehavior(
                code=(
                    "carry_in = 1 if cin else 0\n"
                    "total = a + b + carry_in\n"
                    "sum = total & ((1 << WIDTH)-1)\n"
                    "cout = total >> WIDTH\n"
                    "return sum, cout"
                ),
                ideal_code=(
                    "carry_in = 1.0 if cin else 0.0\n"
                    "return float(a) + float(b) + carry_in, 0.0"
                ),
            ),
        )
        inst = ComponentInstance(definition_ref="Adder_Carry")
        ia  = _iport("a",   _sfixed_type(8, 0))
        ib  = _iport("b",   _sfixed_type(8, 0))
        ic  = _iport("cin", _sfixed_type(1, 0))
        os_ = _oport("sum",  _sfixed_type(9, 0))
        oc  = _oport("cout", _sfixed_type(1, 0))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, ib, ic, os_, oc],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_in(ib, inst.id, "b"),
                _connect_in(ic, inst.id, "cin"),
                _connect_out(inst.id, "sum",  os_),
                _connect_out(inst.id, "cout", oc),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Adder_Carry": defn}

    def test_float_uses_ideal_code_no_carry_out(self):
        """Float mode runs ideal_code: sum = exact float, cout = 0."""
        design, lib = self._make()
        fh = _run_float_only(design, lib, {"a": [200.0], "b": [200.0], "cin": [0.0]})
        # ideal: no masking → exact sum
        assert math.isclose(fh["sum"][0], 400.0, rel_tol=1e-9)
        assert math.isclose(fh["cout"][0], 0.0, abs_tol=1e-9)

    def test_float_small_values_no_carry(self):
        """Non-overflowing add: ideal returns exact sum."""
        design, lib = self._make()
        fh = _run_float_only(design, lib, {"a": [10.0], "b": [20.0], "cin": [0.0]})
        assert math.isclose(fh["sum"][0], 30.0, rel_tol=1e-9)
        assert math.isclose(fh["cout"][0], 0.0, abs_tol=1e-9)

    def test_float_ideal_carry_in(self):
        """carry_in=1 adds 1 to the sum in ideal mode."""
        design, lib = self._make()
        fh = _run_float_only(design, lib, {"a": [5.0], "b": [3.0], "cin": [1.0]})
        assert math.isclose(fh["sum"][0], 9.0, rel_tol=1e-9)

    def test_float_mode_does_not_crash(self):
        """Float mode must not crash on values that would fail with bit ops."""
        design, lib = self._make()
        # 200.0 + 200.0 would crash with float & int if ideal_code weren't used
        fh = _run_float_only(design, lib, {"a": [200.0], "b": [200.0], "cin": [0.0]})
        assert fh["sum"][0] is not None


# ── Test 8: Subtract ──────────────────────────────────────────────────────────

class TestSubtractCoSim:
    def _make(self):
        defn = ComponentDefinition(
            name="Subtract",
            ports=[
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="diff", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(17, -8), signal_class=PortSignalClass.DATA),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return a - b"),
        )
        inst = ComponentInstance(definition_ref="Subtract")
        ia = _iport("a", _sfixed_type(16, -8))
        ib = _iport("b", _sfixed_type(16, -8))
        oy = _oport("diff", _sfixed_type(17, -8))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[ia, ib, oy],
            connections=[
                _connect_in(ia, inst.id, "a"),
                _connect_in(ib, inst.id, "b"),
                _connect_out(inst.id, "diff", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Subtract": defn}

    def test_float_exact(self):
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"a": [1.0], "b": [0.3]})
        assert math.isclose(fh["diff"][0], 0.7, rel_tol=1e-9)

    def test_both_modes_agree_on_representable_difference(self):
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"a": [1.0], "b": [0.5]})
        assert math.isclose(fh["diff"][0], 0.5, rel_tol=1e-9)
        assert math.isclose(xh["diff"][0], 0.5, rel_tol=1e-6)


# ── Test 9: Mux2 ──────────────────────────────────────────────────────────────

class TestMux2CoSim:
    def _make(self):
        defn = ComponentDefinition(
            name="Mux2",
            ports=[
                Port(name="sel", direction=PortDirection.IN,
                     signal_type=_sfixed_type(1, 0), signal_class=PortSignalClass.DATA),
                Port(name="a", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="b", direction=PortDirection.IN,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
                Port(name="y", direction=PortDirection.OUT,
                     signal_type=_sfixed_type(16, -8), signal_class=PortSignalClass.DATA),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return a if sel else b"),
        )
        inst = ComponentInstance(definition_ref="Mux2")
        # sfixed(1,0) is 1-bit signed (range [-1,0]); use sfixed(2,0) for the
        # interface so that sel=1 is representable after pre-quantization.
        is_ = _iport("sel", _sfixed_type(2, 0))
        ia  = _iport("a",   _sfixed_type(16, -8))
        ib  = _iport("b",   _sfixed_type(16, -8))
        oy  = _oport("y",   _sfixed_type(16, -8))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[is_, ia, ib, oy],
            connections=[
                _connect_in(is_, inst.id, "sel"),
                _connect_in(ia,  inst.id, "a"),
                _connect_in(ib,  inst.id, "b"),
                _connect_out(inst.id, "y", oy),
            ],
            component_config=ComponentConfig(),
        )
        return design, {"Mux2": defn}

    def test_sel_high_selects_a_float(self):
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"sel": [1.0], "a": [3.5], "b": [7.0]})
        assert math.isclose(fh["y"][0], 3.5, rel_tol=1e-9)

    def test_sel_low_selects_b_float(self):
        design, lib = self._make()
        fh, _ = _run_both(design, lib, {"sel": [0.0], "a": [3.5], "b": [7.0]})
        assert math.isclose(fh["y"][0], 7.0, rel_tol=1e-9)

    def test_both_modes_agree_on_select(self):
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {"sel": [1.0], "a": [2.0], "b": [5.0]})
        assert math.isclose(fh["y"][0], xh["y"][0], rel_tol=1e-6)


# ── Test 10: GND constant ────────────────────────────────────────────────────

class TestGndCoSim:
    def _make(self):
        defn = ComponentDefinition(
            name="GND",
            ports=[
                Port(name="gnd", direction=PortDirection.OUT,
                     signal_type=SignalType(kind="std_logic", width="1", lsb="0"),
                     signal_class=PortSignalClass.CONTROL),
            ],
            generics=[],
            visual=VisualConfig(),
            behavior=ComponentBehavior(code="return 0"),
        )
        inst = ComponentInstance(definition_ref="GND")
        oy = _oport("gnd", SignalType(kind="std_logic", width="1", lsb="0"))
        design = Design(
            name="test",
            components=[inst],
            interface_ports=[oy],
            connections=[_connect_out(inst.id, "gnd", oy)],
            component_config=ComponentConfig(),
        )
        return design, {"GND": defn}

    def test_gnd_is_zero_in_both_modes(self):
        design, lib = self._make()
        fh, xh = _run_both(design, lib, {})
        assert fh["gnd"][0] == 0.0
        assert xh["gnd"][0] == 0.0
