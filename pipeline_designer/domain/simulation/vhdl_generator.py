"""VHDL export for ComponentDefinition primitives.

Generates three artefacts from a ComponentDefinition that has BehaviorPortType
annotations:

  1. Entity declaration  (entity + port declarations)
  2. RTL architecture   (synthesizable; currently auto-generated only for
                         components whose behavior maps cleanly to fixed-point
                         building blocks in fixed_point_pkg)
  3. Testbench          (GHDL-compatible; driven by Python-computed golden data)

All generated files use the project's `fixed_point_pkg` (numeric_std-based)
rather than ieee.fixed_pkg so they compile with the existing VHDL library.

Bit-width convention (matches fixed_point_pkg arithmetic):
  sfixed(M downto L)  →  signed(M-L  downto 0)   = M-L+1 bits total
  ufixed(M downto L)  →  unsigned(M-L downto 0)   = M-L+1 bits total
  M = msb index (int), L = lsb index (int, usually ≤ 0)

fp_mul_su width note:
  fp_mul_su(a: signed(N-1..0), b: unsigned(M-1..0))
    →  signed-extends b to M+1 bits, then multiplies
    →  result width = N + (M+1) = N + M + 1
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_designer.domain.models.behavior import (
    BehaviorPortType,
    ComponentBehavior,
    FixedPointKind,
)
from pipeline_designer.domain.models.component import ComponentDefinition, Port


# ── Helpers ──────────────────────────────────────────────────────────────────

def _port_total_bits(pt: BehaviorPortType) -> int:
    """Return total physical bits for a BehaviorPortType with range."""
    msb = int(pt.msb)
    lsb = int(pt.lsb)
    return msb - lsb + 1


def _vhdl_signal_type(pt: BehaviorPortType) -> str:
    """Return the VHDL signal type string, e.g. 'signed(15 downto 0)'."""
    total = _port_total_bits(pt)
    high  = total - 1
    if pt.kind == FixedPointKind.SFIXED:
        return f"signed({high} downto 0)"
    if pt.kind in (FixedPointKind.UFIXED, FixedPointKind.STD_LOGIC_VECTOR):
        return f"unsigned({high} downto 0)"
    if pt.kind == FixedPointKind.STD_LOGIC:
        return "std_logic"
    if pt.kind == FixedPointKind.BOOLEAN:
        return "boolean"
    if pt.kind == FixedPointKind.INTEGER:
        return "integer"
    return "std_logic_vector(0 downto 0)"


def _fp_format_constant(pt: BehaviorPortType) -> str:
    """Return inline fp_format_t aggregate, e.g. '(1, 15, true, 0.0)'."""
    msb     = int(pt.msb)
    lsb     = int(pt.lsb)
    int_b   = msb + 1
    frac_b  = -lsb
    signed  = "true" if pt.kind == FixedPointKind.SFIXED else "false"
    return f"({int_b}, {frac_b}, {signed}, 0.0)"


def _real_to_raw(value: float, pt: BehaviorPortType) -> int:
    """Convert a real value to a raw integer for this port type (truncate)."""
    lsb   = int(pt.lsb)
    step  = 2.0 ** lsb          # step = 2^lsb  (lsb ≤ 0 → step < 1)
    total = _port_total_bits(pt)
    signed = pt.kind == FixedPointKind.SFIXED
    raw_real = value / step
    raw_int  = math.floor(raw_real)
    if signed:
        lo = -(2 ** (total - 1))
        hi =  (2 ** (total - 1)) - 1
    else:
        lo = 0
        hi = (2 ** total) - 1
    return max(lo, min(hi, raw_int))


# ── Entity generator ──────────────────────────────────────────────────────────

def generate_entity(
    component: ComponentDefinition,
    pkg_library: str = "work",
) -> str:
    """Generate a VHDL entity declaration for *component*.

    Port types come from ``component.behavior.port_types``; any port not
    listed there falls back to ``std_logic``.
    """
    name   = component.name.lower()
    ports  = component.ports
    ptypes = component.behavior.port_types

    lines: list[str] = []
    lines += [
        f"-- Auto-generated entity for {component.name}",
        f"-- Do not edit — regenerate via VhdlGenerator.generate_entity()",
        "",
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.numeric_std.all;",
        f"library {pkg_library};",
        f"use {pkg_library}.fixed_point_pkg.all;",
        "",
        f"entity {name} is",
        "    port (",
    ]

    port_decls: list[str] = []
    for i, port in enumerate(ports):
        pt      = ptypes.get(port.name)
        vtype   = _vhdl_signal_type(pt) if pt and pt.has_range() else "std_logic"
        dir_kw  = "in " if port.direction.upper() == "IN" else "out"
        sep     = "" if i == len(ports) - 1 else ";"
        port_decls.append(f"        {port.name:<12} : {dir_kw} {vtype}{sep}")

    lines += port_decls
    lines += ["    );", f"end entity {name};", ""]
    return "\n".join(lines)


# ── Architecture generator (FIR4Tap specialisation) ──────────────────────────

def _generate_fir4tap_architecture(component: ComponentDefinition) -> str:
    """Generate an RTL architecture for a 4-tap FIR with equal coefficients.

    Detects the FIR4Tap pattern: 4 inputs x0-x3 (same sfixed format),
    1 output y, and one unsigned coefficient constant.
    """
    ptypes = component.behavior.port_types
    name   = component.name.lower()

    pt_x    = ptypes["x0"]
    pt_y    = ptypes["y"]
    msb_x   = int(pt_x.msb)
    lsb_x   = int(pt_x.lsb)
    total_x = msb_x - lsb_x + 1         # 16
    total_y = _port_total_bits(pt_y)     # 17

    # 0.25 in U(msb_x).(−lsb_x) format = 2^(lsb_x+2) as raw integer
    # e.g. msb_x=0, lsb_x=-15 → 0.25 = 2^(-15+(-2)) ...
    # 0.25 * 2^15 = 8192
    frac_x   = -lsb_x                   # 15
    coef_raw = 2 ** (frac_x - 2)        # 0.25 * 2^15 = 8192

    # fp_mul_su(x0: total_x bits, coef: total_x bits)
    #   → coef sign-extended to total_x+1 bits, product = total_x + (total_x+1)
    mul_bits = total_x + (total_x + 1)  # 33

    # fp_add_s widens by 1
    add2_bits = mul_bits + 1            # 34
    add4_bits = add2_bits + 1           # 35

    # Formats for fp_quantize_s
    int_bits_x   = msb_x + 1           # 1
    int_bits_mul  = int_bits_x + int_bits_x + 1  # 3  (mixed sign → +1)
    frac_mul      = frac_x + frac_x    # 30
    int_bits_sum4 = int_bits_mul + 2   # 5  (two adds, each +1)
    int_bits_y    = int(pt_y.msb) + 1  # 2
    frac_y        = -int(pt_y.lsb)     # 15

    lines: list[str] = [
        f"architecture rtl of {name} is",
        "",
        f"    -- fp_format_t constants (int_bits, frac_bits, is_signed, offset)",
        f"    constant FMT_X    : fp_format_t := ({int_bits_x}, {frac_x}, true,  0.0);",
        f"    constant FMT_COEF : fp_format_t := ({int_bits_x}, {frac_x}, false, 0.0);",
        f"    constant FMT_MUL  : fp_format_t := ({int_bits_mul}, {frac_mul}, true,  0.0);",
        f"    constant FMT_SUM4 : fp_format_t := ({int_bits_sum4}, {frac_mul}, true,  0.0);",
        f"    constant FMT_Y    : fp_format_t := ({int_bits_y}, {frac_y}, true,  0.0);",
        "",
        f"    -- Box-filter coefficient: 0.25 in U{int_bits_x}.{frac_x}",
        f"    constant C_COEF : unsigned({total_x - 1} downto 0) :=",
        f"        to_unsigned({coef_raw}, {total_x});",
        "",
        f"    -- Exact-precision intermediates",
        f"    signal m0, m1, m2, m3 : signed({mul_bits - 1} downto 0);",
        f"    signal s01, s23       : signed({add2_bits - 1} downto 0);",
        f"    signal acc            : signed({add4_bits - 1} downto 0);",
        "",
        "begin",
        "",
        "    -- Multiply each tap by 0.25 (exact: no rounding needed here)",
        "    m0 <= fp_mul_su(x0, C_COEF);",
        "    m1 <= fp_mul_su(x1, C_COEF);",
        "    m2 <= fp_mul_su(x2, C_COEF);",
        "    m3 <= fp_mul_su(x3, C_COEF);",
        "",
        "    -- Pairwise accumulation (exact)",
        "    s01 <= fp_add_s(m0, m1);",
        "    s23 <= fp_add_s(m2, m3);",
        "    acc <= fp_add_s(s01, s23);",
        "",
        "    -- Requantize to output format (truncate + saturate)",
        "    quantize: process(acc)",
        f"        variable tmp : signed({total_y - 1} downto 0);",
        "        variable sta : fp_status_t;",
        "    begin",
        "        fp_quantize_s(acc, FMT_SUM4, FMT_Y, TRUNCATE, SAT_SATURATE, tmp, sta);",
        "        y <= tmp;",
        "    end process quantize;",
        "",
        f"end architecture rtl;",
        "",
    ]
    return "\n".join(lines)


# ── Testbench generator ───────────────────────────────────────────────────────

@dataclass
class StimulusCase:
    """One combinational test vector: input port values and expected outputs."""
    label:   str
    inputs:  dict[str, float]   # port_name → real value
    outputs: dict[str, float]   # port_name → expected real value


def generate_testbench(
    component: ComponentDefinition,
    cases: list[StimulusCase],
    pkg_library: str = "work",
    dut_library: str = "work",
    tolerance_lsb: int = 1,
) -> str:
    """Generate a self-checking VHDL testbench driven by *cases*.

    The testbench is purely combinational (no clock) — it applies each
    stimulus vector, waits 10 ns, then asserts the output is within
    *tolerance_lsb* raw counts of the expected value.

    Parameters
    ----------
    component       : ComponentDefinition with behavior.port_types filled
    cases           : list of StimulusCase (from Python simulation)
    pkg_library     : library containing fixed_point_pkg (default "work")
    dut_library     : library containing the DUT entity (default "work")
    tolerance_lsb   : allowed error in raw output counts (default 1)
    """
    name   = component.name.lower()
    tb_name = f"tb_{name}"
    ptypes = component.behavior.port_types
    ports  = component.ports

    in_ports  = [p for p in ports if p.direction.upper() == "IN"]
    out_ports = [p for p in ports if p.direction.upper() == "OUT"]

    lines: list[str] = []
    lines += [
        f"-- Auto-generated testbench for {component.name}",
        f"-- Generated from Python behavioral simulation (BehaviorExecutor).",
        f"-- Run with: ghdl -a --std=08 fixed_point_pkg.vhd {name}.vhd {tb_name}.vhd",
        f"--           ghdl -e --std=08 {tb_name}",
        f"--           ghdl -r --std=08 {tb_name}",
        "",
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.numeric_std.all;",
        "use ieee.math_real.all;",
        f"library {pkg_library};",
        f"use {pkg_library}.fixed_point_pkg.all;",
        "",
        f"entity {tb_name} is",
        f"end entity {tb_name};",
        "",
        f"architecture sim of {tb_name} is",
        "",
        "    -- DUT ports",
    ]

    # Signal declarations
    for port in ports:
        pt = ptypes.get(port.name)
        vtype = _vhdl_signal_type(pt) if pt and pt.has_range() else "std_logic"
        if port.direction.upper() == "IN":
            lines.append(f"    signal {port.name:<12} : {vtype} := (others => '0');")
        else:
            lines.append(f"    signal {port.name:<12} : {vtype};")

    lines += [""]

    # fp_format_t constants for input ports (for real_to_sfp)
    lines.append("    -- Format constants for real_to_sfp / sfp_to_real")
    for port in ports:
        pt = ptypes.get(port.name)
        if pt and pt.has_range():
            lines.append(
                f"    constant FMT_{port.name.upper():<8} : fp_format_t := "
                f"{_fp_format_constant(pt)};"
            )

    lines += [
        "",
        "begin",
        "",
        f"    -- DUT instantiation",
        f"    dut: entity {dut_library}.{name}",
        "        port map (",
    ]

    port_maps = []
    for i, port in enumerate(ports):
        sep = "" if i == len(ports) - 1 else ","
        port_maps.append(f"            {port.name} => {port.name}{sep}")
    lines += port_maps
    lines += ["        );", ""]

    # Stimulus process
    lines += [
        "    stim: process",
        "        variable expected : integer;",
        "        variable actual   : integer;",
        "        variable err      : integer;",
        "        variable pass     : boolean := true;",
        "    begin",
        "",
    ]

    for ci, case in enumerate(cases):
        lines.append(f"        -- {case.label}")
        for port in in_ports:
            pt = ptypes.get(port.name)
            val = case.inputs.get(port.name, 0.0)
            if pt and pt.has_range():
                lines.append(
                    f"        {port.name} <= real_to_sfp({val:.10f}, "
                    f"FMT_{port.name.upper()});"
                )
            else:
                lines.append(f"        -- {port.name}: non-fixed-point input skipped")

        lines.append("        wait for 10 ns;")

        for port in out_ports:
            pt = ptypes.get(port.name)
            val = case.outputs.get(port.name, 0.0)
            if pt and pt.has_range():
                raw_expected = _real_to_raw(val, pt)
                lines += [
                    f"        expected := {raw_expected};",
                    f"        actual   := to_integer({port.name});",
                    f"        err      := actual - expected;",
                    f"        if err < 0 then err := -err; end if;",
                    f"        assert err <= {tolerance_lsb}",
                    f"            report \"{case.label}: {port.name} mismatch got \" "
                    f"& integer'image(actual) & \" expected \" "
                    f"& integer'image(expected)",
                    f"            severity error;",
                    f"        if err > {tolerance_lsb} then pass := false; end if;",
                ]
        lines.append("")

    lines += [
        "        if pass then",
        f"            report \"SIMPASS: all {len(cases)} {name} test cases passed\" severity note;",
        "        else",
        f"            report \"SIMFAIL: one or more {name} test cases failed\" severity failure;",
        "        end if;",
        "        wait;",
        "    end process stim;",
        "",
        f"end architecture sim;",
        "",
    ]

    return "\n".join(lines)


# ── Top-level convenience class ───────────────────────────────────────────────

class VhdlGenerator:
    """Convenience wrapper that generates all artefacts for one component."""

    def __init__(self, component: ComponentDefinition, pkg_library: str = "work"):
        self.component   = component
        self.pkg_library = pkg_library

    def entity(self) -> str:
        return generate_entity(self.component, pkg_library=self.pkg_library)

    def architecture(self) -> str:
        """Return RTL architecture; currently specialised for FIR4Tap pattern."""
        name = self.component.name
        ptypes = self.component.behavior.port_types
        # Detect FIR4Tap-like pattern: 4 sfixed inputs + 1 sfixed output
        fir_inputs = [k for k in ("x0", "x1", "x2", "x3") if k in ptypes]
        if len(fir_inputs) == 4 and "y" in ptypes:
            return _generate_fir4tap_architecture(self.component)
        # Generic stub for other components
        name_l = name.lower()
        return textwrap.dedent(f"""\
            architecture rtl of {name_l} is
            begin
                -- TODO: implement {name} behavior
            end architecture rtl;
            """)

    def testbench(
        self,
        cases: list[StimulusCase],
        tolerance_lsb: int = 1,
    ) -> str:
        return generate_testbench(
            self.component,
            cases,
            pkg_library=self.pkg_library,
            tolerance_lsb=tolerance_lsb,
        )

    def write_all(
        self,
        output_dir: Path,
        cases: list[StimulusCase],
        tolerance_lsb: int = 1,
    ) -> dict[str, Path]:
        """Write entity, architecture, and testbench to *output_dir*.

        Returns a dict mapping role → file path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        name = self.component.name.lower()

        entity_path = output_dir / f"{name}.vhd"
        tb_path     = output_dir / f"tb_{name}.vhd"

        entity_src = self.entity() + "\n" + self.architecture()
        entity_path.write_text(entity_src)
        tb_path.write_text(self.testbench(cases, tolerance_lsb=tolerance_lsb))

        return {"entity": entity_path, "testbench": tb_path}
