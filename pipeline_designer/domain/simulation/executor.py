"""Behavioral simulation executor.

Compiles a ComponentBehavior code body into a callable Python function and
provides a controlled namespace that maps the pseudo-code primitives to real
fixedpoint operations.

Namespace provided to behavior code
------------------------------------
SFixed(msb, lsb)   -> FPFormat (signed, ieee.fixed_pkg index convention)
UFixed(msb, lsb)   -> FPFormat (unsigned)
Bits(n)            -> FPFormat (n-bit unsigned std_logic_vector)
Const(fmt, value)  -> FixedPointArray scalar (0-d)
truncate / round_half_up / round_half_even / round_away  (RoundMode strings)
wrap / saturate / assert_no_overflow                     (SaturateMode strings)
FPFormat / FixedPointArray / UnquantizedResult           (types)
np                                                       (numpy)

Behavior code signature
-----------------------
The code body is compiled as the body of:

    def _behavior(port_a: ..., port_b: ...) -> ...:
        <code>

The architect's code may use any of the namespace symbols.  Ports arrive as
FixedPointArray objects (or plain numpy scalars for scalar port types) and the
function must return the same.
"""

from __future__ import annotations

import textwrap
import types
from typing import Any, Callable

import numpy as np

from fixedpoint import FPFormat, FixedPointArray, UnquantizedResult


# ── Namespace factories ──────────────────────────────────────────────────────

def _sfixed(msb: int, lsb: int) -> FPFormat:
    """sfixed(msb downto lsb) → FPFormat with signed=True."""
    int_bits  = msb + 1
    frac_bits = -lsb
    if frac_bits < 0:
        raise ValueError(f"SFixed({msb}, {lsb}): lsb must be ≤ 0")
    return FPFormat(int_bits=int_bits, frac_bits=frac_bits, signed=True)


def _ufixed(msb: int, lsb: int) -> FPFormat:
    """ufixed(msb downto lsb) → FPFormat with signed=False."""
    int_bits  = msb + 1
    frac_bits = -lsb
    if frac_bits < 0:
        raise ValueError(f"UFixed({msb}, {lsb}): lsb must be ≤ 0")
    return FPFormat(int_bits=int_bits, frac_bits=frac_bits, signed=False)


def _bits(n: int) -> FPFormat:
    """std_logic_vector(n-1 downto 0) — treated as n-bit unsigned integer."""
    return FPFormat(int_bits=n, frac_bits=0, signed=False)


def _const(fmt: FPFormat, value: float) -> FixedPointArray:
    """Scalar fixed-point constant quantized to fmt."""
    return fmt.quantize(np.array(value))


class SimNamespace(dict):
    """dict subclass that forms the exec namespace for behavior code."""

    def __init__(self, extra: dict[str, Any] | None = None):
        super().__init__()
        self.update({
            # type constructors (new names)
            "Signed":  _sfixed,
            "Unsigned": _ufixed,
            "Bits":    _bits,
            "Const":   _const,
            # legacy aliases for backward compatibility with existing behavior code
            "SFixed":  _sfixed,
            "UFixed":  _ufixed,
            # raw types (useful for isinstance checks in behavior code)
            "FPFormat":          FPFormat,
            "FixedPointArray":   FixedPointArray,
            "UnquantizedResult": UnquantizedResult,
            # rounding mode strings (match fixedpoint.quant.RoundMode)
            "truncate":          "truncate",
            "round_half_up":     "round_half_up",
            "round_half_even":   "round_half_even",
            "round_away":        "round_away",
            # saturation mode strings (match fixedpoint.quant.SaturateMode)
            "wrap":              "wrap",
            "saturate":          "saturate",
            "assert_no_overflow": "assert",
            # numpy
            "np": np,
        })
        if extra:
            self.update(extra)


# ── Executor ─────────────────────────────────────────────────────────────────

class BehaviorExecutor:
    """Compiles and caches a ComponentBehavior as a callable.

    Parameters
    ----------
    code_body : str
        The indented function body from ``ComponentBehavior.code``.
    param_names : list[str]
        Ordered list of port/parameter names that become the function's
        positional arguments.
    name : str
        Label used in error messages and ``__name__``.

    Usage
    -----
    exec = BehaviorExecutor(behavior.code, ["x0","x1","x2","x3"], "fir4tap")
    y = exec(x0_fp, x1_fp, x2_fp, x3_fp)
    """

    def __init__(
        self,
        code_body: str,
        param_names: list[str],
        name: str = "behavior",
        extra_ns: dict[str, Any] | None = None,
    ):
        self._name   = name
        self._params = list(param_names)
        self._state: dict[str, Any] = {}
        combined = {**(extra_ns or {}), "state": self._state}
        self._ns  = SimNamespace(combined)
        self._fn  = self._compile(code_body)

    def reset_state(self) -> None:
        """Clear the persistent state dict (shift registers, accumulators, etc.)."""
        self._state.clear()

    # ── compilation ──────────────────────────────────────────────────────────

    def _compile(self, code_body: str) -> Callable:
        sig = ", ".join(self._params)
        # Dedent the body in case the JSON stores it with extra indentation.
        body = textwrap.dedent(code_body)
        # Re-indent by 4 spaces for the function body.
        indented = textwrap.indent(body, "    ")
        src = f"def _{self._name}({sig}):\n{indented}\n"

        try:
            code = compile(src, f"<behavior:{self._name}>", "exec")
        except SyntaxError as exc:
            raise SyntaxError(
                f"Syntax error in behavior '{self._name}':\n{exc}"
            ) from exc

        exec(code, self._ns)  # noqa: S102
        fn = self._ns[f"_{self._name}"]
        fn.__name__ = self._name
        return fn

    # ── call ─────────────────────────────────────────────────────────────────

    def __call__(self, *args: Any) -> Any:
        if len(args) != len(self._params):
            raise TypeError(
                f"{self._name}() takes {len(self._params)} argument(s), "
                f"got {len(args)}"
            )
        return self._fn(*args)

    def __repr__(self) -> str:
        return f"BehaviorExecutor(name={self._name!r}, params={self._params})"
