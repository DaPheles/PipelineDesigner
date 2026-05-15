"""Signal type and behavior models for primitive components.

Signal type system
------------------
Each port carries a ``SignalType`` that encodes three orthogonal concerns:

  kind   — what the bits represent (sfixed/ufixed/std_logic_vector/…).
           Can be a concrete ``SignalKind`` value *or* the name of a
           ``signal_kind`` generic so a single primitive works for multiple
           numeric types.

  width  — total bit count as an integer-arithmetic expression, possibly
           referencing generic names (e.g. ``"WIDTH"`` or
           ``"INT_BITS+FRAC_BITS"``).

  lsb    — position of the least-significant bit; 0 for plain integers,
           negative for fractional fixed-point (e.g. ``"-FRAC_BITS"``).
           MSB is always derived: ``msb = width + lsb - 1``.

Signal-class constraints
------------------------
  clock / reset  →  std_logic only (scalar, no width/lsb)
  control        →  std_logic (width=1) or std_logic_vector (width>1)
  data           →  sfixed or ufixed only (ieee.fixed_pkg fixed-point types)

VHDL mapping examples (ieee.fixed_pkg):
  kind=sfixed,  width=12, lsb=-8  →  sfixed(3 downto -8)   (S4.8)
  kind=ufixed,  width=16, lsb=-8  →  ufixed(7 downto -8)   (U8.8)
  kind=std_logic_vector, width=8, lsb=0 →  std_logic_vector(7 downto 0)
  kind=std_logic                        →  std_logic
"""

from __future__ import annotations

import ast
import operator
import re
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fixedpoint import FPFormat


# ── Safe integer-expression evaluator ────────────────────────────────────────

_ALLOWED_OPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.floordiv,
    ast.FloorDiv: operator.floordiv,
    ast.USub:     operator.neg,
}


def _eval_index(expr: str, generics: dict[str, int]) -> int:
    """Evaluate a width/lsb expression using only integer arithmetic.

    Allowed: integer literals, names from *generics*, +, -, *, //, unary -.
    """
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse expression {expr!r}: {exc}") from exc

    def _visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return node.value
            raise ValueError(f"Non-integer constant {node.value!r} in {expr!r}")
        if isinstance(node, ast.Name):
            if node.id in generics:
                return int(generics[node.id])
            raise ValueError(
                f"Unknown name {node.id!r} in {expr!r}; provide it via generics="
            )
        if isinstance(node, ast.UnaryOp):
            op_fn = _ALLOWED_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported unary operator in {expr!r}")
            return op_fn(_visit(node.operand))
        if isinstance(node, ast.BinOp):
            op_fn = _ALLOWED_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator in {expr!r}")
            return op_fn(_visit(node.left), _visit(node.right))
        raise ValueError(
            f"Unsupported AST node {type(node).__name__} in {expr!r}"
        )

    return _visit(tree)


def _substitute_ints(expr: str, int_generics: dict[str, int]) -> str:
    """Replace whole-word generic names with their concrete integer values.

    String-valued generics (outer-scope names forwarded by an instance) are
    absent from *int_generics* and are therefore left as identifiers so that
    the enclosing entity's generics remain visible in the emitted expression.
    """
    result = expr
    for name, val in int_generics.items():
        result = re.sub(r"\b" + re.escape(name) + r"\b", str(val), result)
    return result


# ── Signal kind ───────────────────────────────────────────────────────────────

class SignalKind(str, Enum):
    """Concrete VHDL signal type family.

    Data ports must use SFIXED or UFIXED (ieee.fixed_pkg).
    Clock/reset ports use STD_LOGIC only.
    Control ports use STD_LOGIC (1-bit) or STD_LOGIC_VECTOR (multi-bit).
    """

    SFIXED            = "sfixed"             # ieee.fixed_pkg.sfixed — signed fixed-point
    UFIXED            = "ufixed"             # ieee.fixed_pkg.ufixed — unsigned fixed-point
    STD_LOGIC_VECTOR  = "std_logic_vector"
    STD_ULOGIC_VECTOR = "std_ulogic_vector"
    STD_LOGIC         = "std_logic"
    STD_ULOGIC        = "std_ulogic"
    INTEGER           = "integer"
    BOOLEAN           = "boolean"


# Kinds that do not carry a width/lsb range
_SCALAR_KINDS: frozenset[SignalKind] = frozenset({
    SignalKind.STD_LOGIC,
    SignalKind.STD_ULOGIC,
    SignalKind.INTEGER,
    SignalKind.BOOLEAN,
})

# Kinds that have a binary point (can produce FPFormat)
_FIXED_POINT_KINDS: frozenset[SignalKind] = frozenset({
    SignalKind.SFIXED,
    SignalKind.UFIXED,
})

# Legacy name mapping for JSON backward compatibility
_LEGACY_KIND_MAP: dict[str, str] = {
    "signed":   SignalKind.SFIXED.value,    # old internal name for sfixed
    "unsigned": SignalKind.UFIXED.value,    # old internal name for ufixed
    "slv":      SignalKind.STD_LOGIC_VECTOR.value,
    # sfixed, ufixed, std_logic_vector, std_logic, integer, boolean: keep as-is
}


# ── SignalType ────────────────────────────────────────────────────────────────

class SignalType(BaseModel):
    """Complete type description for a single port.

    ``kind`` is either a concrete ``SignalKind`` value (e.g. ``"sfixed"``) or
    the name of a ``signal_kind`` generic (e.g. ``"SIG_TYPE"``), allowing a
    single component definition to be instantiated with different numeric types.

    ``width`` and ``lsb`` are integer-arithmetic expressions that may reference
    generic names.  MSB is always derived: ``msb = width + lsb - 1``.
    """

    kind:  str = Field(default="std_logic", description="SignalKind value or type-generic name")
    width: str = Field(default="1",         description="Total bit width (expression)")
    lsb:   str = Field(default="0",         description="LSB position; negative → fractional bits")

    def resolved_kind(self, generics: dict[str, Any] | None = None) -> SignalKind | None:
        """Return a concrete ``SignalKind``, resolving a generic reference if needed."""
        try:
            return SignalKind(self.kind)
        except ValueError:
            val = (generics or {}).get(self.kind)
            if val is not None:
                try:
                    return SignalKind(str(val))
                except ValueError:
                    pass
            return None

    def has_range(self, generics: dict[str, Any] | None = None) -> bool:
        """True when this type carries a bit-width (not a scalar like std_logic)."""
        k = self.resolved_kind(generics)
        if k is None:
            return True  # unresolved generic reference — assume it has a range
        return k not in _SCALAR_KINDS

    def msb_expr(self) -> str:
        """MSB as a string expression derived from width and lsb."""
        try:
            msb = int(self.width) + int(self.lsb) - 1
            return str(msb)
        except ValueError:
            if self.lsb == "0":
                return f"({self.width})-1"
            return f"({self.width})+({self.lsb})-1"

    @staticmethod
    def _make_int_generics(generics: dict[str, Any] | None) -> dict[str, int]:
        """Convert generic values to a ``{name: int}`` dict for expression evaluation.

        Accepts int, float, and numeric strings so that definition defaults
        (which may be stored as strings in JSON) are also resolved.
        """
        result: dict[str, int] = {}
        for name, v in (generics or {}).items():
            if isinstance(v, bool):
                continue  # booleans are subclass of int — exclude them
            if isinstance(v, (int, float)):
                result[name] = int(v)
            elif isinstance(v, str):
                try:
                    result[name] = int(float(v))
                except (ValueError, TypeError):
                    pass
        return result

    def notation(self, generics: dict[str, Any] | None = None) -> str | None:
        """Return ``'S4.8'`` / ``'U4.8'`` notation when dimensions are concrete."""
        k = self.resolved_kind(generics)
        if k not in _FIXED_POINT_KINDS:
            return None
        try:
            int_g = self._make_int_generics(generics)
            w = _eval_index(self.width, int_g)
            l = _eval_index(self.lsb,  int_g)
            int_bits  = w + l
            frac_bits = -l
            prefix = "S" if k == SignalKind.SFIXED else "U"
            return f"{prefix}{int_bits}.{frac_bits}"
        except (ValueError, KeyError):
            return None

    def to_vhdl_type(self, generics: dict[str, Any] | None = None) -> str:
        """Return a VHDL type string, e.g. ``signed(3 downto -8)``."""
        g = generics or {}
        k = self.resolved_kind(g)
        if k is None:
            return "std_logic"
        if k in _SCALAR_KINDS:
            return k.value
        int_g = self._make_int_generics(g)
        try:
            w   = _eval_index(self.width, int_g)
            l   = _eval_index(self.lsb,   int_g)
            msb = w + l - 1
            return f"{k.value}({msb} downto {l})"
        except (ValueError, KeyError):
            # Partially substitute: numeric generics become literals; string
            # generics (outer-scope names forwarded by the instance) remain
            # as identifiers valid in the enclosing entity's scope.
            w_expr = _substitute_ints(self.width, int_g)
            l_expr = _substitute_ints(self.lsb,   int_g)
            try:
                msb = int(w_expr) + int(l_expr) - 1
                return f"{k.value}({msb} downto {l_expr})"
            except ValueError:
                msb_expr = (
                    f"({w_expr})-1" if l_expr == "0"
                    else f"({w_expr})+({l_expr})-1"
                )
                return f"{k.value}({msb_expr} downto {l_expr})"

    def to_python_annotation(self, generics: dict[str, Any] | None = None) -> str:
        """Return a pseudo-code type annotation for the behavior signature."""
        g = generics or {}
        k = self.resolved_kind(g)
        kind_label = k.value if k else self.kind
        match k:
            case SignalKind.STD_LOGIC | SignalKind.STD_ULOGIC:
                return "Bit"
            case SignalKind.BOOLEAN:
                return "bool"
            case SignalKind.INTEGER:
                return "int"
            case SignalKind.SFIXED:
                return f"Signed[{self.msb_expr()}:{self.lsb}]"
            case SignalKind.UFIXED:
                return f"Unsigned[{self.msb_expr()}:{self.lsb}]"
            case _:
                if self.has_range(g):
                    return f"{kind_label}[{self.msb_expr()}:{self.lsb}]"
                return kind_label

    def to_fpformat(self, generics: dict[str, int] | None = None) -> "FPFormat":
        """Convert to a ``fixedpoint.FPFormat`` for simulation.

        Convention: ``signed(M downto L)`` → int_bits=M+1, frac_bits=-L.
        Only valid for ``signed`` / ``unsigned`` kinds.
        Raises ``TypeError`` for scalar kinds, ``ValueError`` for unresolvable
        generic expressions.
        """
        from fixedpoint import FPFormat  # late import — optional dependency

        g = generics or {}
        k = self.resolved_kind(g)

        if k is None:
            raise TypeError(
                f"to_fpformat(): kind={self.kind!r} references an unresolved generic"
            )
        if k not in _FIXED_POINT_KINDS:
            raise TypeError(
                f"to_fpformat() is not defined for kind={k.value!r}; "
                "only sfixed / ufixed carry a fixed-point format."
            )

        int_g = {n: int(v) for n, v in g.items() if isinstance(v, (int, float))}
        w   = _eval_index(self.width, int_g)
        l   = _eval_index(self.lsb,   int_g)
        msb = w + l - 1

        int_bits  = msb + 1
        frac_bits = -l
        if frac_bits < 0:
            raise ValueError(
                f"LSB index {l} yields negative frac_bits ({frac_bits}). "
                "LSB must be ≤ 0 for fractional formats."
            )
        return FPFormat(int_bits=int_bits, frac_bits=frac_bits, signed=(k == SignalKind.SFIXED))


# ── ComponentBehavior ─────────────────────────────────────────────────────────

class ComponentBehavior(BaseModel):
    """Python-like pseudo-code description of a component's function.

    ``code`` is only the function *body*.  The signature is derived at
    display time from the port ``SignalType`` declarations.
    """

    model_config = ConfigDict(extra="ignore")  # silently drop legacy port_types

    code: str = Field(
        default="",
        description="Function body in Python-like pseudo-code",
    )
