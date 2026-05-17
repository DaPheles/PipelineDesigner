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
FPFormat / FixedPoint / FixedPointArray / UnquantizedResult  (types)
np                                                           (numpy)

Float / ideal mode
------------------
When ``float_mode=True`` the executor uses ``FloatSimNamespace`` instead of
``SimNamespace``.  Every fixed-point factory (SFixed / UFixed / Bits / Const)
returns a ``_FloatFmt`` stub whose ``quantize()`` is an identity operation, so
the same behavior code runs with unlimited intermediate precision.  Values
entering and leaving the executor are plain Python ``float`` objects.

Behavior code signature
-----------------------
The code body is compiled as the body of:

    def _behavior(port_a: ..., port_b: ...) -> ...:
        <code>

The architect's code may use any of the namespace symbols.  Ports arrive as
``FixedPoint`` scalars (or plain Python ``float`` for non-fixed-point ports)
and the function must return a ``FixedPoint`` or ``UnquantizedResult``.
"""

from __future__ import annotations

import math
import textwrap
import types
from typing import Any, Callable

import numpy as np

from fixedpoint import FPFormat, FixedPoint, FixedPointArray, UnquantizedResult


# ── Namespace factories ──────────────────────────────────────────────────────
# FPFormat.from_sfixed / from_ufixed already validate lsb ≤ 0, so no wrappers
# needed.  _bits and _const are thin helpers that do not exist on FPFormat.

def _bits(n: int) -> FPFormat:
    """std_logic_vector(n-1 downto 0) — treated as n-bit unsigned integer."""
    return FPFormat(int_bits=n, frac_bits=0, signed=False)


def _const(fmt: FPFormat, value: float) -> FixedPoint:
    """Scalar fixed-point constant quantized to fmt."""
    return fmt.quantize(np.array(value))


class SimNamespace(dict):
    """dict subclass that forms the exec namespace for behavior code."""

    def __init__(self, extra: dict[str, Any] | None = None):
        super().__init__()
        self.update({
            # fixed-point format constructors (VHDL sfixed/ufixed index convention)
            "SFixed":   FPFormat.from_sfixed,
            "UFixed":   FPFormat.from_ufixed,
            "Signed":   FPFormat.from_sfixed,   # more explicit alias
            "Unsigned": FPFormat.from_ufixed,
            "Bits":     _bits,
            "Const":    _const,
            # raw types — useful for isinstance checks in behavior code
            "FPFormat":          FPFormat,
            "FixedPoint":        FixedPoint,
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


# ── Float / ideal-mode namespace ─────────────────────────────────────────────

class _FloatFmt:
    """Drop-in stub for FPFormat used in float/ideal simulation mode.

    Every quantize() call is an identity — values pass through at full Python
    float precision.  real_min / real_max are ±inf so any saturation or
    clamping logic in behavior code never activates, which is the correct
    "ideal" semantic (unlimited word length, no overflow).
    """

    real_min: float = -math.inf
    real_max: float =  math.inf
    int_bits:  int = 0
    frac_bits: int = 0

    def quantize(self, x: Any, round: Any = None, overflow: Any = None) -> float:
        v = np.asarray(x)
        return float(v.flat[0])

    def __call__(self, x: Any) -> float:
        return float(x)


_FLOAT_FMT = _FloatFmt()


class FloatSimNamespace(SimNamespace):
    """SimNamespace variant for ideal/float simulation.

    Fixed-point type factories (SFixed, UFixed, Bits, Const) are replaced by
    stubs that return plain Python floats, making every quantize() a no-op.
    All other symbols (numpy, rounding-mode strings, etc.) are inherited.
    """

    def __init__(self, extra: dict[str, Any] | None = None):
        super().__init__(extra)
        self.update({
            "SFixed":   lambda msb, lsb: _FLOAT_FMT,
            "UFixed":   lambda msb, lsb: _FLOAT_FMT,
            "Signed":   lambda msb, lsb: _FLOAT_FMT,
            "Unsigned": lambda msb, lsb: _FLOAT_FMT,
            "Bits":     lambda n:        _FLOAT_FMT,
            "Const":    lambda fmt, v:   float(v),
        })


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
        float_mode: bool = False,
    ):
        self._name   = name
        self._params = list(param_names)
        self._state: dict[str, Any] = {}
        combined = {**(extra_ns or {}), "state": self._state}
        ns_cls   = FloatSimNamespace if float_mode else SimNamespace
        self._ns  = ns_cls(combined)
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
