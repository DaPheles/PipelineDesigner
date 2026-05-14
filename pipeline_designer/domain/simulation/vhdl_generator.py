"""VHDL export for ComponentDefinition primitives.

Generates two artefacts from a ComponentDefinition:

  1. Entity declaration  — port types come from ``port.signal_type`` resolved
     against the component's generic default values.
  2. Testbench          — GHDL-compatible self-checking testbench driven by
     Python-computed golden values.

Bit-width convention (ieee.fixed_pkg)
--------------------------------------
  sfixed(M downto L)   →  M = width + lsb - 1,  L = lsb
  ufixed(M downto L)   →  same convention

For ``sfixed(3 downto -8)`` (S4.8):   width=12, lsb=-8  →  M=3, L=-8
For ``sfixed(15 downto 0)`` (S16.0):  width=16, lsb=0   →  M=15, L=0

Data ports always use sfixed/ufixed from ieee.fixed_pkg.
Clock/reset ports use std_logic.  Control ports use std_logic[_vector].
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_designer.domain.models.behavior import (
    SignalKind,
    SignalType,
    _eval_index,
)
from pipeline_designer.domain.models.component import ComponentDefinition, Port


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_generics(component: ComponentDefinition) -> dict[str, Any]:
    """Build a generics dict from the component's generic default values."""
    return {
        g.name: g.default_value
        for g in component.generics
        if g.default_value is not None
    }


def _int_generics(generics: dict[str, Any]) -> dict[str, int]:
    return {k: int(v) for k, v in generics.items() if isinstance(v, (int, float))}


def _vhdl_signal_type(port: Port, generics: dict[str, Any]) -> str:
    """Return the VHDL type string for a port, e.g. ``signed(3 downto -8)``."""
    return port.signal_type.to_vhdl_type(generics)


def _fp_format_constant(port: Port, generics: dict[str, Any]) -> str:
    """Return an inline fp_format_t aggregate, e.g. ``(4, 8, true, 0.0)``."""
    st = port.signal_type
    g  = _int_generics(generics)
    w  = _eval_index(st.width, g)
    l  = _eval_index(st.lsb,   g)
    int_bits  = w + l
    frac_bits = -l
    k = st.resolved_kind(generics)
    signed = "true" if k == SignalKind.SFIXED else "false"
    return f"({int_bits}, {frac_bits}, {signed}, 0.0)"


def _port_total_bits(port: Port, generics: dict[str, Any]) -> int:
    g = _int_generics(generics)
    return _eval_index(port.signal_type.width, g)


def _real_to_raw(value: float, port: Port, generics: dict[str, Any]) -> int:
    """Convert a real value to raw integer for this port (truncate)."""
    st    = port.signal_type
    g     = _int_generics(generics)
    l     = _eval_index(st.lsb,   g)
    w     = _eval_index(st.width, g)
    step  = 2.0 ** l
    total = w
    k     = st.resolved_kind(generics)
    signed_type = (k == SignalKind.SFIXED)
    raw_real = value / step
    raw_int  = math.floor(raw_real)
    if signed_type:
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
    """Generate a VHDL entity declaration for *component*."""
    name     = component.name.lower()
    generics = _default_generics(component)

    lines: list[str] = [
        f"-- Auto-generated entity for {component.name}",
        f"-- Do not edit — regenerate via VhdlGenerator.generate_entity()",
        "",
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.fixed_pkg.all;",
        f"library {pkg_library};",
        f"use {pkg_library}.fixed_point_pkg.all;",
        "",
        f"entity {name} is",
        "    port (",
    ]

    port_decls: list[str] = []
    for i, port in enumerate(component.ports):
        vtype   = _vhdl_signal_type(port, generics)
        dir_kw  = "in " if port.direction.value.upper() == "IN" else "out"
        sep     = "" if i == len(component.ports) - 1 else ";"
        port_decls.append(f"        {port.name:<12} : {dir_kw} {vtype}{sep}")

    lines += port_decls
    lines += ["    );", f"end entity {name};", ""]
    return "\n".join(lines)


# ── FIR4Tap architecture (specialised) ───────────────────────────────────────

def _generate_fir4tap_architecture(component: ComponentDefinition) -> str:
    """RTL architecture for a 4-tap box-average FIR."""
    generics = _default_generics(component)
    name     = component.name.lower()

    x0   = component.get_port_by_name("x0")
    y    = component.get_port_by_name("y")
    if x0 is None or y is None:
        raise ValueError("FIR4Tap architecture requires x0 and y ports")

    g        = _int_generics(generics)
    lsb_x    = _eval_index(x0.signal_type.lsb,   g)
    w_x      = _eval_index(x0.signal_type.width,  g)
    w_y      = _eval_index(y.signal_type.width,   g)
    total_x  = w_x
    total_y  = w_y
    msb_x    = w_x + lsb_x - 1
    frac_x   = -lsb_x

    coef_raw = 2 ** (frac_x - 2)
    mul_bits = total_x + (total_x + 1)
    add2_bits = mul_bits + 1
    add4_bits = add2_bits + 1

    int_bits_x   = msb_x + 1
    int_bits_mul  = int_bits_x + int_bits_x + 1
    frac_mul      = frac_x + frac_x
    int_bits_sum4 = int_bits_mul + 2
    lsb_y         = _eval_index(y.signal_type.lsb, g)
    msb_y         = w_y + lsb_y - 1
    int_bits_y    = msb_y + 1
    frac_y        = -lsb_y

    lines: list[str] = [
        f"architecture rtl of {name} is",
        "",
        f"    constant FMT_X    : fp_format_t := ({int_bits_x}, {frac_x}, true,  0.0);",
        f"    constant FMT_COEF : fp_format_t := ({int_bits_x}, {frac_x}, false, 0.0);",
        f"    constant FMT_MUL  : fp_format_t := ({int_bits_mul}, {frac_mul}, true,  0.0);",
        f"    constant FMT_SUM4 : fp_format_t := ({int_bits_sum4}, {frac_mul}, true,  0.0);",
        f"    constant FMT_Y    : fp_format_t := ({int_bits_y}, {frac_y}, true,  0.0);",
        "",
        f"    constant C_COEF : ufixed({total_x - 1} downto 0) :=",
        f"        to_ufixed({coef_raw}, {total_x - 1}, 0);",
        "",
        f"    signal m0, m1, m2, m3 : sfixed({mul_bits - 1} downto 0);",
        f"    signal s01, s23       : sfixed({add2_bits - 1} downto 0);",
        f"    signal acc            : sfixed({add4_bits - 1} downto 0);",
        "",
        "begin",
        "",
        "    m0 <= fp_mul_su(x0, C_COEF);",
        "    m1 <= fp_mul_su(x1, C_COEF);",
        "    m2 <= fp_mul_su(x2, C_COEF);",
        "    m3 <= fp_mul_su(x3, C_COEF);",
        "",
        "    s01 <= fp_add_s(m0, m1);",
        "    s23 <= fp_add_s(m2, m3);",
        "    acc <= fp_add_s(s01, s23);",
        "",
        "    quantize: process(acc)",
        f"        variable tmp : sfixed({total_y - 1} downto 0);",
        "        variable sta : fp_status_t;",
        "    begin",
        "        fp_quantize_s(acc, FMT_SUM4, FMT_Y, TRUNCATE, SAT_WRAP, tmp, sta);",
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
    """One combinational test vector."""
    label:   str
    inputs:  dict[str, float]
    outputs: dict[str, float]


def generate_testbench(
    component: ComponentDefinition,
    cases: list[StimulusCase],
    pkg_library: str = "work",
    dut_library: str = "work",
    tolerance_lsb: int = 1,
) -> str:
    name     = component.name.lower()
    tb_name  = f"tb_{name}"
    generics = _default_generics(component)
    ports    = component.ports

    in_ports  = [p for p in ports if p.direction.value.upper() == "IN"]
    out_ports = [p for p in ports if p.direction.value.upper() == "OUT"]

    lines: list[str] = [
        f"-- Auto-generated testbench for {component.name}",
        f"-- Run with: ghdl -a --std=08 fixed_point_pkg.vhd {name}.vhd {tb_name}.vhd",
        f"--           ghdl -e --std=08 {tb_name}",
        f"--           ghdl -r --std=08 {tb_name}",
        "",
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.fixed_pkg.all;",
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

    for port in ports:
        vtype = _vhdl_signal_type(port, generics)
        if port.direction.value.upper() == "IN":
            lines.append(f"    signal {port.name:<12} : {vtype} := (others => '0');")
        else:
            lines.append(f"    signal {port.name:<12} : {vtype};")

    lines += [""]
    lines.append("    -- Format constants for real_to_sfp / sfp_to_real")
    for port in ports:
        if port.signal_type.has_range(generics):
            lines.append(
                f"    constant FMT_{port.name.upper():<8} : fp_format_t := "
                f"{_fp_format_constant(port, generics)};"
            )

    lines += [
        "",
        "begin",
        "",
        f"    dut: entity {dut_library}.{name}",
        "        port map (",
    ]
    port_maps = []
    for i, port in enumerate(ports):
        sep = "" if i == len(ports) - 1 else ","
        port_maps.append(f"            {port.name} => {port.name}{sep}")
    lines += port_maps
    lines += ["        );", ""]

    lines += [
        "    stim: process",
        "        variable expected : integer;",
        "        variable actual   : integer;",
        "        variable err      : integer;",
        "        variable pass     : boolean := true;",
        "    begin",
        "",
    ]

    for case in cases:
        lines.append(f"        -- {case.label}")
        for port in in_ports:
            val = case.inputs.get(port.name, 0.0)
            if port.signal_type.has_range(generics):
                lines.append(
                    f"        {port.name} <= real_to_sfp({val:.10f}, "
                    f"FMT_{port.name.upper()});"
                )
            else:
                lines.append(f"        -- {port.name}: scalar input skipped")

        lines.append("        wait for 10 ns;")

        for port in out_ports:
            val = case.outputs.get(port.name, 0.0)
            if port.signal_type.has_range(generics):
                raw_expected = _real_to_raw(val, port, generics)
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


# ── Convenience wrapper ───────────────────────────────────────────────────────

class VhdlGenerator:
    """Generates entity, architecture, and testbench for one component."""

    def __init__(self, component: ComponentDefinition, pkg_library: str = "work"):
        self.component   = component
        self.pkg_library = pkg_library

    def entity(self) -> str:
        return generate_entity(self.component, pkg_library=self.pkg_library)

    def architecture(self) -> str:
        name   = self.component.name
        name_l = name.lower()
        port_names = {p.name for p in self.component.ports}
        if all(k in port_names for k in ("x0", "x1", "x2", "x3", "y")):
            try:
                return _generate_fir4tap_architecture(self.component)
            except Exception:
                pass
        return textwrap.dedent(f"""\
            architecture rtl of {name_l} is
            begin
                -- TODO: implement {name} behavior
            end architecture rtl;
            """)

    def testbench(self, cases: list[StimulusCase], tolerance_lsb: int = 1) -> str:
        return generate_testbench(
            self.component, cases,
            pkg_library=self.pkg_library, tolerance_lsb=tolerance_lsb,
        )

    def write_all(
        self,
        output_dir: Path,
        cases: list[StimulusCase],
        tolerance_lsb: int = 1,
    ) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        name = self.component.name.lower()

        entity_path = output_dir / f"{name}.vhd"
        tb_path     = output_dir / f"tb_{name}.vhd"

        entity_path.write_text(self.entity() + "\n" + self.architecture())
        tb_path.write_text(self.testbench(cases, tolerance_lsb=tolerance_lsb))

        return {"entity": entity_path, "testbench": tb_path}
