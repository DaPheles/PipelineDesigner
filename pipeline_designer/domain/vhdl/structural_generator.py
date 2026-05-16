"""Structural VHDL generator for composite designs.

Translates a ``Design`` (composition of component instances) into a VHDL
entity + structural architecture.  The output uses ieee.fixed_pkg for
sfixed/ufixed data ports and ieee.std_logic_1164 for clock/reset/control
signals.

Output structure
----------------
  library clause
  entity DesignName is … end entity;
  architecture structural of DesignName is
      component declarations …
      signal declarations …
  begin
      component instantiations …
  end architecture structural;
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pipeline_designer.domain.models.behavior import SignalKind, _FIXED_POINT_KINDS
from pipeline_designer.domain.models.component import ComponentDefinition, PortDirection
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.models.instance import ComponentInstance, InterfaceDirection


# VHDL-2008 reserved words that must not be used as plain identifiers
_VHDL_RESERVED = frozenset({
    "abs", "access", "after", "alias", "all", "and", "architecture", "array",
    "assert", "attribute", "begin", "block", "body", "buffer", "bus", "case",
    "component", "configuration", "constant", "disconnect", "downto", "else",
    "elsif", "end", "entity", "exit", "file", "for", "force", "function",
    "generate", "generic", "group", "guarded", "if", "impure", "in", "inertial",
    "inout", "is", "label", "library", "linkage", "literal", "loop", "map",
    "mod", "nand", "new", "next", "nor", "not", "null", "of", "on", "open",
    "or", "others", "out", "package", "parameter", "port", "postponed",
    "procedure", "process", "property", "protected", "pure", "range", "record",
    "register", "reject", "release", "rem", "report", "restrict", "return",
    "rol", "ror", "select", "sequence", "severity", "signal", "shared", "sla",
    "sll", "sra", "srl", "subtype", "then", "to", "transport", "type",
    "unaffected", "units", "until", "use", "variable", "wait", "when",
    "while", "with", "xnor", "xor",
})


def _vhdl_ident(name: str) -> str:
    """Convert an arbitrary string to a safe lowercase VHDL identifier."""
    ident = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower().strip("_")
    if not ident or ident[0].isdigit():
        ident = "sig_" + ident
    if ident in _VHDL_RESERVED:
        ident = ident + "_sig"
    return ident or "unnamed"


def _dir_str(direction: PortDirection) -> str:
    """VHDL direction keyword, padded to 5 chars for alignment."""
    match direction:
        case PortDirection.IN:    return "in   "
        case PortDirection.OUT:   return "out  "
        case PortDirection.INOUT: return "inout"
    return "in   "


def _resolve_generics(
    instance: ComponentInstance,
    definition: ComponentDefinition,
) -> dict[str, Any]:
    """Merge definition default values with per-instance overrides."""
    generics: dict[str, Any] = {
        g.name: g.default_value
        for g in definition.generics
        if g.default_value is not None
    }
    generics.update(instance.generic_values)
    return generics


def _generic_vhdl_type(data_type: str) -> str:
    """Map a Generic.data_type string to a VHDL type keyword."""
    match data_type:
        case "integer" | "signal_kind": return "integer"
        case "float":                   return "real"
        case "boolean":                 return "boolean"
        case "string":                  return "string"
        case _:                         return "integer"


def _generic_value_str(value: Any, data_type: str) -> str:
    """Format a generic value for VHDL emission (string literals get quotes)."""
    if value is None:
        return ""
    if data_type == "string":
        return f'"{value}"'
    if data_type == "boolean":
        return "true" if value else "false"
    return str(value)


class StructuralVhdlGenerator:
    """Generate a VHDL structural entity from a ``Design``.

    Usage::

        gen = StructuralVhdlGenerator(design, library)
        vhdl_text = gen.generate()
        print("\\n".join(gen.warnings))
    """

    def __init__(
        self,
        design: Design,
        library: dict[str, ComponentDefinition],
    ) -> None:
        self._design = design
        self._library = library
        self._warnings: list[str] = []

        # (instance_id, port_name) → VHDL signal or entity port name
        self._port_map: dict[tuple[UUID, str], str] = {}
        # internal signals: list of (vhdl_name, vhdl_type)
        self._signals: list[tuple[str, str]] = []

        self._build_connectivity()
        self._check_signal_consistency()

    # ── Generic resolution ────────────────────────────────────────────────────

    def _effective_generics(
        self,
        instance: ComponentInstance,
        definition: ComponentDefinition,
    ) -> dict[str, Any]:
        """Resolve generics, forwarding outer design generic names where the
        instance value matches the design's outer generic default for the same name.

        A component generic named ``LSB`` whose resolved value equals the design's
        ``LSB`` default is emitted as the identifier ``LSB`` rather than the literal
        ``-15``, making the generated signal types and generic maps parameterizable
        by the enclosing entity's generics.
        """
        resolved = _resolve_generics(instance, definition)
        outer = {
            g.name: g.default_value
            for g in self._design.component_config.generics
            if g.default_value is not None
        }
        return {
            name: (name if (name in outer and val == outer[name]) else val)
            for name, val in resolved.items()
        }

    # ── Signal consistency ────────────────────────────────────────────────────

    def _endpoint_vhdl_type(self, ep, inst_by_id: dict, iface_by_id: dict) -> str | None:
        """Return the VHDL type string for one side of a connection, or None."""
        if ep.is_interface_port():
            ip = iface_by_id.get(ep.interface_port_id)
            return ip.effective_signal_type().to_vhdl_type() if ip else None
        if ep.component_id is not None:
            inst = inst_by_id.get(ep.component_id)
            if inst is None:
                return None
            defn = self._library.get(inst.definition_ref)
            if defn is None:
                return None
            port = defn.get_port_by_name(ep.port_name)
            if port is None:
                return None
            return port.signal_type.to_vhdl_type(self._effective_generics(inst, defn))
        return None

    def _endpoint_label(self, ep, inst_by_id: dict, iface_by_id: dict) -> str:
        if ep.is_interface_port():
            ip = iface_by_id.get(ep.interface_port_id)
            return ip.name if ip else "?"
        if ep.component_id is not None:
            inst = inst_by_id.get(ep.component_id)
            name = inst.get_display_name() if inst else str(ep.component_id)[:8]
            return f"{name}.{ep.port_name}"
        return "?"

    def _signal_type_key(self, ep, inst_by_id: dict, iface_by_id: dict) -> tuple | None:
        """Return a ``(kind, width_int, lsb_int)`` tuple evaluated against concrete generics.

        Used so algebraically equivalent expressions like ``WIDTH+4+1`` and
        ``WIDTH+5`` compare equal.  Returns ``None`` when the type cannot be
        fully resolved to integers.
        """
        from pipeline_designer.domain.models.behavior import (
            _eval_index, _substitute_generics, _SCALAR_KINDS,
        )

        outer_concrete: dict[str, int] = {
            g.name: int(g.default_value)
            for g in self._design.component_config.generics
            if g.default_value is not None
            and isinstance(g.default_value, (int, float))
            and not isinstance(g.default_value, bool)
        }

        if ep.is_interface_port():
            ip = iface_by_id.get(ep.interface_port_id)
            if ip is None:
                return None
            st = ip.effective_signal_type()
            int_g = outer_concrete
        elif ep.component_id is not None:
            inst = inst_by_id.get(ep.component_id)
            if inst is None:
                return None
            defn = self._library.get(inst.definition_ref)
            if defn is None:
                return None
            port = defn.get_port_by_name(ep.port_name)
            if port is None:
                return None
            st = port.signal_type
            resolved = _resolve_generics(inst, defn)
            for name, val in list(resolved.items()):
                if isinstance(val, str):
                    try:
                        resolved[name] = _eval_index(
                            _substitute_generics(val, outer_concrete), outer_concrete
                        )
                    except (ValueError, KeyError):
                        pass
            int_g = {k: int(v) for k, v in {**outer_concrete, **resolved}.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool)}
        else:
            return None

        k = st.resolved_kind(int_g)
        if k is None:
            return None
        if k in _SCALAR_KINDS:
            return (k.value, 0, 0)
        try:
            w = _eval_index(_substitute_generics(st.width, int_g), int_g)
            l = _eval_index(_substitute_generics(st.lsb,   int_g), int_g)
            return (k.value, w, l)
        except (ValueError, KeyError):
            return None

    def _check_signal_consistency(self) -> None:
        """Warn on connections where source and target port types differ."""
        inst_by_id  = {c.id: c for c in self._design.components}
        iface_by_id = {p.id: p for p in self._design.interface_ports}

        for conn in self._design.connections:
            src_type = self._endpoint_vhdl_type(conn.source, inst_by_id, iface_by_id)
            tgt_type = self._endpoint_vhdl_type(conn.target, inst_by_id, iface_by_id)

            if src_type is None or tgt_type is None:
                continue
            if src_type == tgt_type:
                continue

            # String comparison failed — try integer-tuple comparison to catch
            # algebraically equivalent expressions like WIDTH+4+1 vs WIDTH+5.
            src_key = self._signal_type_key(conn.source, inst_by_id, iface_by_id)
            tgt_key = self._signal_type_key(conn.target, inst_by_id, iface_by_id)
            if src_key is not None and tgt_key is not None and src_key == tgt_key:
                continue

            src_label = self._endpoint_label(conn.source, inst_by_id, iface_by_id)
            tgt_label = self._endpoint_label(conn.target, inst_by_id, iface_by_id)
            self._warnings.append(
                f"Type mismatch: {src_label} ({src_type}) → {tgt_label} ({tgt_type})"
            )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def generate(self) -> str:
        """Return the complete VHDL source string."""
        return "\n".join([
            self._library_clause(),
            self._entity(),
            "",
            self._architecture(),
        ])

    # ── Connectivity map ──────────────────────────────────────────────────────

    def _build_connectivity(self) -> None:
        """One pass over all connections → fill _port_map and _signals."""
        iface_by_id = {p.id: p for p in self._design.interface_ports}
        inst_by_id  = {c.id: c  for c in self._design.components}
        used_names:  set[str] = set()

        for conn in self._design.connections:
            src, tgt = conn.source, conn.target

            src_is_iface = src.is_interface_port()
            tgt_is_iface = tgt.is_interface_port()

            # ── Entity input → component input ────────────────────────────
            if src_is_iface and not tgt_is_iface:
                iface = iface_by_id.get(src.interface_port_id)
                if iface and tgt.component_id is not None:
                    self._port_map[(tgt.component_id, tgt.port_name)] = (
                        _vhdl_ident(iface.name)
                    )
                continue

            # ── Component output → entity output ──────────────────────────
            if tgt_is_iface and not src_is_iface:
                iface = iface_by_id.get(tgt.interface_port_id)
                if iface and src.component_id is not None:
                    self._port_map[(src.component_id, src.port_name)] = (
                        _vhdl_ident(iface.name)
                    )
                continue

            # ── Internal signal ───────────────────────────────────────────
            if src.component_id is None or tgt.component_id is None:
                continue

            sig_name = self._unique_signal_name(conn, inst_by_id, used_names)
            used_names.add(sig_name)

            self._port_map[(src.component_id, src.port_name)] = sig_name
            self._port_map[(tgt.component_id, tgt.port_name)] = sig_name

            vhdl_type = self._signal_type_from_source(src.component_id, src.port_name, inst_by_id)
            self._signals.append((sig_name, vhdl_type))

    def _unique_signal_name(self, conn, inst_by_id, used: set[str]) -> str:
        if conn.signal_name:
            base = _vhdl_ident(conn.signal_name)
        else:
            src_inst = inst_by_id.get(conn.source.component_id)
            src_display = src_inst.get_display_name() if src_inst else str(conn.source.component_id)[:8]
            base = _vhdl_ident(f"sig_{src_display}_{conn.source.port_name}")

        name, counter = base, 1
        while name in used:
            name = f"{base}_{counter}"
            counter += 1
        return name

    def _signal_type_from_source(
        self, src_id: UUID, port_name: str, inst_by_id: dict
    ) -> str:
        inst = inst_by_id.get(src_id)
        if inst is None:
            return "std_logic"
        defn = self._library.get(inst.definition_ref)
        if defn is None:
            self._warnings.append(
                f"Definition '{inst.definition_ref}' not in library — "
                f"signal type for '{port_name}' defaulted to std_logic"
            )
            return "std_logic"
        port = defn.get_port_by_name(port_name)
        if port is None:
            self._warnings.append(
                f"Port '{port_name}' not found on '{inst.definition_ref}'"
            )
            return "std_logic"
        return port.signal_type.to_vhdl_type(self._effective_generics(inst, defn))

    # ── Library clause ────────────────────────────────────────────────────────

    def _uses_fixed_point(self) -> bool:
        for _, vtype in self._signals:
            if "sfixed" in vtype or "ufixed" in vtype:
                return True
        for ip in self._design.interface_ports:
            k = ip.effective_signal_type().resolved_kind()
            if k in _FIXED_POINT_KINDS:
                return True
        return False

    def _library_clause(self) -> str:
        lines = [
            "library ieee;",
            "use ieee.std_logic_1164.all;",
            "use ieee.numeric_std.all;",
        ]
        if self._uses_fixed_point():
            lines.append("use ieee.fixed_pkg.all;")
        return "\n".join(lines)

    # ── Entity ────────────────────────────────────────────────────────────────

    def _entity(self) -> str:
        design = self._design
        ename  = _vhdl_ident(design.name)
        lines  = ["", f"entity {ename} is"]

        # Design-level generics (from component_config.generics)
        cfg_generics = design.component_config.generics
        if cfg_generics:
            lines.append("  generic (")
            gen_parts = []
            for g in cfg_generics:
                vtype   = _generic_vhdl_type(g.data_type)
                dval    = _generic_value_str(g.default_value, g.data_type)
                default = f" := {dval}" if dval else ""
                gen_parts.append(f"    {g.name} : {vtype}{default}")
            lines.append(";\n".join(gen_parts))
            lines.append("  );")

        # Ports
        ifaces = design.interface_ports
        if ifaces:
            lines.append("  port (")
            port_parts = []
            for ip in ifaces:
                direction = "in   " if ip.direction == InterfaceDirection.INPUT else "out  "
                vtype     = ip.effective_signal_type().to_vhdl_type()
                pname     = _vhdl_ident(ip.name)
                port_parts.append(f"    {pname:<24}: {direction} {vtype}")
            lines.append(";\n".join(port_parts))
            lines.append("  );")

        lines.append(f"end entity {ename};")
        return "\n".join(lines)

    # ── Architecture ──────────────────────────────────────────────────────────

    def _architecture(self) -> str:
        ename = _vhdl_ident(self._design.name)
        lines = [f"architecture structural of {ename} is", ""]

        comp_block = self._component_declarations()
        if comp_block:
            lines.append(comp_block)
            lines.append("")

        sig_block = self._signal_declarations()
        if sig_block:
            lines.append(sig_block)
            lines.append("")

        lines += ["begin", ""]

        pm_block = self._instantiations()
        if pm_block:
            lines.append(pm_block)
            lines.append("")

        lines.append(f"end architecture structural;")
        return "\n".join(lines)

    # ── Component declarations ────────────────────────────────────────────────

    def _component_declarations(self) -> str:
        seen:   set[str] = set()
        blocks: list[str] = []

        for inst in self._design.components:
            ref = inst.definition_ref
            if ref in seen:
                continue
            seen.add(ref)

            defn = self._library.get(ref)
            if defn is None:
                self._warnings.append(f"No library definition for '{ref}' — component declaration omitted")
                continue

            cname = _vhdl_ident(defn.name)
            b = [f"  component {cname} is"]

            if defn.generics:
                b.append("    generic (")
                gen_parts = []
                for g in defn.generics:
                    vtype   = _generic_vhdl_type(g.data_type)
                    dval    = _generic_value_str(g.default_value, g.data_type)
                    default = f" := {dval}" if dval else ""
                    gen_parts.append(f"      {g.name} : {vtype}{default}")
                b.append(";\n".join(gen_parts))
                b.append("    );")

            b.append("    port (")
            port_parts = []
            for port in defn.ports:
                vtype = port.signal_type.to_vhdl_type()
                port_parts.append(
                    f"      {port.name:<20}: {_dir_str(port.direction)} {vtype}"
                )
            b.append(";\n".join(port_parts))
            b.append("    );")
            b.append(f"  end component {cname};")
            blocks.append("\n".join(b))

        return "\n\n".join(blocks)

    # ── Signal declarations ───────────────────────────────────────────────────

    def _signal_declarations(self) -> str:
        if not self._signals:
            return ""
        return "\n".join(
            f"  signal {name:<32} : {vtype};"
            for name, vtype in self._signals
        )

    # ── Component instantiations ──────────────────────────────────────────────

    def _instantiations(self) -> str:
        blocks: list[str] = []

        for inst in self._design.components:
            defn = self._library.get(inst.definition_ref)
            if defn is None:
                blocks.append(f"  -- WARNING: no definition for '{inst.definition_ref}'")
                continue

            inst_name = _vhdl_ident(inst.get_display_name())
            comp_name = _vhdl_ident(defn.name)
            generics  = self._effective_generics(inst, defn)

            b = [f"  {inst_name} : {comp_name}"]

            # Generic map
            if defn.generics:
                b.append("    generic map (")
                gm_parts = []
                for g in defn.generics:
                    val = generics.get(g.name, g.default_value)
                    if val is not None:
                        gm_parts.append(
                            f"      {g.name} => {_generic_value_str(val, g.data_type)}"
                        )
                b.append(",\n".join(gm_parts))
                b.append("    )")

            # Port map
            b.append("    port map (")
            pm_parts = []
            for port in defn.ports:
                sig = self._port_map.get((inst.id, port.name))
                if sig is None:
                    if port.direction == PortDirection.OUT:
                        sig = "open"
                    else:
                        sig = "/* UNCONNECTED */"
                        self._warnings.append(
                            f"Input '{port.name}' on '{inst.get_display_name()}' is unconnected"
                        )
                pm_parts.append(f"      {port.name:<20} => {sig}")
            b.append(",\n".join(pm_parts))
            b.append("    );")
            blocks.append("\n".join(b))

        return "\n\n".join(blocks)
