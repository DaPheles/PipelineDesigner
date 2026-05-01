"""Functional behavior model for primitive components.

Uses a typed fixed-point number system so pseudo-code can describe
numerical semantics in a way that later enables simulation.

Fixed-point types mirror VHDL ieee.fixed_pkg:
  SFixed[msb:lsb]  →  sfixed(msb downto lsb)
  UFixed[msb:lsb]  →  ufixed(msb downto lsb)

MSB/LSB are strings to allow generic expressions (e.g. 'WIDTH-1', '-FRAC').
Negative LSB means fractional bits (e.g. SFixed[7:-8] is 8.8 signed).
"""

import ast
import operator
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fixedpoint import FPFormat


# ── Safe integer-expression evaluator for MSB/LSB index strings ─────────────

_ALLOWED_OPS = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.floordiv,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
}


def _eval_index(expr: str, generics: dict[str, int]) -> int:
    """Evaluate an MSB/LSB index expression using only integer arithmetic.

    Allowed: integer literals, names from *generics*, +, -, *, //, unary minus.
    Raises ValueError for anything else (no builtins, no attribute access).
    """
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse index expression {expr!r}: {exc}") from exc

    def _visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return node.value
            raise ValueError(f"Non-integer constant {node.value!r} in expression {expr!r}")
        if isinstance(node, ast.Name):
            if node.id in generics:
                return int(generics[node.id])
            raise ValueError(f"Unknown name {node.id!r} in expression {expr!r}; provide it via generics=")
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
        raise ValueError(f"Unsupported AST node {type(node).__name__} in expression {expr!r}")

    return _visit(tree)


class FixedPointKind(str, Enum):
    """Number representation for behavior port type annotations."""

    SFIXED = "sfixed"
    UFIXED = "ufixed"
    STD_LOGIC_VECTOR = "std_logic_vector"
    STD_LOGIC = "std_logic"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class BehaviorPortType(BaseModel):
    """Fixed-point type annotation for a single port."""

    kind: FixedPointKind = Field(
        default=FixedPointKind.STD_LOGIC_VECTOR,
        description="Number representation kind",
    )
    msb: str = Field(
        default="0",
        description="MSB index — integer literal or generic expression e.g. 'WIDTH-1'",
    )
    lsb: str = Field(
        default="0",
        description="LSB index — may be negative for fractional bits e.g. '-FRAC'",
    )

    def to_vhdl_type(self) -> str:
        """Return the VHDL type string."""
        match self.kind:
            case FixedPointKind.STD_LOGIC:
                return "std_logic"
            case FixedPointKind.BOOLEAN:
                return "boolean"
            case FixedPointKind.INTEGER:
                return "integer"
            case _:
                return f"{self.kind.value}({self.msb} downto {self.lsb})"

    def to_python_annotation(self) -> str:
        """Return the Python pseudo-code type annotation string."""
        match self.kind:
            case FixedPointKind.STD_LOGIC:
                return "Bit"
            case FixedPointKind.BOOLEAN:
                return "bool"
            case FixedPointKind.INTEGER:
                return "int"
            case FixedPointKind.STD_LOGIC_VECTOR:
                return f"Bits[{self.msb}:{self.lsb}]"
            case FixedPointKind.SFIXED:
                return f"SFixed[{self.msb}:{self.lsb}]"
            case FixedPointKind.UFIXED:
                return f"UFixed[{self.msb}:{self.lsb}]"
            case _:
                return "Any"

    def has_range(self) -> bool:
        """Return True if this kind carries MSB/LSB parameters."""
        return self.kind not in (
            FixedPointKind.STD_LOGIC,
            FixedPointKind.BOOLEAN,
            FixedPointKind.INTEGER,
        )

    def to_fpformat(self, generics: dict[str, int] | None = None) -> "FPFormat":
        """Convert to a fixedpoint.FPFormat using ieee.fixed_pkg index rules.

        ieee.fixed_pkg convention:
          sfixed(M downto L)  →  int_bits = M + 1,  frac_bits = -L,  signed = True
          ufixed(M downto L)  →  int_bits = M + 1,  frac_bits = -L,  signed = False

        L ≤ 0 means the LSB is fractional (common case).
        L > 0 would mean the integer part is further truncated (unusual).

        For std_logic_vector the interpretation is unsigned with
        int_bits = M + 1, frac_bits = 0 (all integer).

        Raises TypeError if called on std_logic / boolean / integer kinds.
        Raises ValueError if expressions cannot be evaluated (missing generics).
        """
        from fixedpoint import FPFormat  # late import — optional dependency

        g = generics or {}

        if self.kind in (FixedPointKind.STD_LOGIC, FixedPointKind.BOOLEAN, FixedPointKind.INTEGER):
            raise TypeError(
                f"to_fpformat() is not defined for kind={self.kind.value!r}; "
                "it only applies to sfixed / ufixed / std_logic_vector."
            )

        M = _eval_index(self.msb, g)
        L = _eval_index(self.lsb, g)

        int_bits  = M + 1
        frac_bits = -L          # positive when L is negative (fractional)
        signed    = self.kind == FixedPointKind.SFIXED

        if frac_bits < 0:
            raise ValueError(
                f"LSB index {L} yields negative frac_bits ({frac_bits}). "
                "LSB must be ≤ 0 for fractional formats."
            )

        return FPFormat(int_bits=int_bits, frac_bits=frac_bits, signed=signed)


class ComponentBehavior(BaseModel):
    """Python-like pseudo-code description of a component's function.

    `code` is only the function *body* (indented lines).  The editor
    auto-generates the signature from `port_types` and displays it as a
    read-only header above the editable body.

    Example for a saturating adder:
        port_types = {
            "a":   BehaviorPortType(kind=SFIXED, msb="WIDTH-1", lsb="0"),
            "b":   BehaviorPortType(kind=SFIXED, msb="WIDTH-1", lsb="0"),
            "sum": BehaviorPortType(kind=SFIXED, msb="WIDTH",   lsb="0"),
        }
        code = "return saturate(a + b, SFixed[WIDTH:0])"
    """

    code: str = Field(
        default="",
        description="Function body in Python-like pseudo-code",
    )
    port_types: dict[str, BehaviorPortType] = Field(
        default_factory=dict,
        description="Fixed-point type annotations keyed by port name",
    )

    def generate_signature(
        self,
        input_names: list[str],
        output_names: list[str],
    ) -> str:
        """Build a Python def-line from the stored port type annotations."""
        args = []
        for name in input_names:
            pt = self.port_types.get(name)
            ann = pt.to_python_annotation() if pt else "Any"
            args.append(f"{name}: {ann}")

        if not output_names:
            ret = "None"
        elif len(output_names) == 1:
            pt = self.port_types.get(output_names[0])
            ret = pt.to_python_annotation() if pt else "Any"
        else:
            parts = []
            for name in output_names:
                pt = self.port_types.get(name)
                parts.append(pt.to_python_annotation() if pt else "Any")
            ret = f"tuple[{', '.join(parts)}]"

        return f"def compute({', '.join(args)}) -> {ret}:"
