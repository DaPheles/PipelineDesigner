"""
fixedpoint/quant.py
===================
Fixed-point type system and quantization primitives.

Design principles
-----------------
1. Every value in the pipeline has an *explicit* fixed-point format attached.
2. Operations are *not* implicitly quantized — the architect must state the
   output format after every stage.  The framework raises if they forget.
3. All computation happens in float64 (the "ideal" reference) and the
   quantization is applied as a discrete, auditable step.
4. The framework records every quantization decision and can replay any
   stage to measure the error introduced.

Type hierarchy
--------------
``FixedPointArray``
    A numpy array paired with an FPFormat.  Designed for batch / ISP-pipeline
    work where an entire channel or row of pixels is quantized together.
    Supports numpy ufuncs, broadcasting, and format-tracking arithmetic.

``FixedPoint``  (subclass of ``FixedPointArray``)
    A *single* quantized real value.  Has the same arithmetic operators as
    ``FixedPointArray`` but additionally behaves like a Python number:
    ``__eq__`` returns ``bool``, instances are hashable, and the constructor
    accepts a plain Python ``float`` rather than a numpy array.

    ``isinstance(fp, FixedPointArray)`` is always ``True`` for ``FixedPoint``
    objects so existing code needs no changes.

    ``FPFormat.quantize()`` returns ``FixedPoint`` when the input is a Python
    scalar or a 0-d numpy array, and ``FixedPointArray`` otherwise.

``UnquantizedResult``
    Intermediate from arithmetic.  Forces the architect to choose a target
    format before the value can be used as a signal.  Supports chained
    arithmetic (``a + b + carry_in``) without losing the invariant.
"""

from __future__ import annotations
import re
import numpy as np
from dataclasses import dataclass
from typing import Literal, Optional
import warnings


# ─── Rounding modes (must match FPGA RTL choice) ────────────────────────────

RoundMode    = Literal["truncate", "round_half_up", "round_half_even", "round_away"]
SaturateMode = Literal["wrap", "saturate", "assert"]


def _apply_round(x: np.ndarray, mode: RoundMode) -> np.ndarray:
    if mode == "truncate":
        return np.floor(x)
    elif mode == "round_half_up":
        return np.floor(x + 0.5)
    elif mode == "round_half_even":
        return np.round(x)          # numpy uses banker's rounding
    elif mode == "round_away":
        return np.sign(x) * np.floor(np.abs(x) + 0.5)
    else:
        raise ValueError(f"Unknown rounding mode: {mode!r}")


# ─── Core fixed-point format descriptor ─────────────────────────────────────

@dataclass(frozen=True)
class FPFormat:
    """
    Describes a fixed-point format.

    Parameters
    ----------
    int_bits   : signed integer bits (including sign bit for signed formats).
                 0 means pure fractional (U0.frac or S0.frac).
    frac_bits  : fractional bits below the binary point.
    signed     : True for two's-complement signed, False for unsigned.

    Examples
    --------
    U0.8     : FPFormat(0, 8,  signed=False)
    S2.24    : FPFormat(2, 24, signed=True)
    """
    int_bits:  int
    frac_bits: int
    signed:    bool  = True
    offset:    float = 0.0

    # ── derived properties ──────────────────────────────────────────────────

    @property
    def total_bits(self) -> int:
        return self.int_bits + self.frac_bits

    @property
    def step(self) -> float:
        return 2.0 ** -self.frac_bits

    @property
    def raw_min(self) -> int:
        if self.signed:
            return -(2 ** (self.total_bits - 1))
        return 0

    @property
    def raw_max(self) -> int:
        if self.signed:
            return 2 ** (self.total_bits - 1) - 1
        return 2 ** self.total_bits - 1

    @property
    def real_min(self) -> float:
        return self.raw_min * self.step

    @property
    def real_max(self) -> float:
        return self.raw_max * self.step

    def __repr__(self) -> str:
        sign = "S" if self.signed else "U"
        return f"{sign}{self.int_bits}.{self.frac_bits}"

    def __str__(self) -> str:
        float32_ok = self.fits_in_float32()
        float64_ok = self.fits_in_float64()

        float32_str = "yes" if float32_ok else f"no  ({self.total_bits} bits > 24)  — use float64"
        float64_str = (
            "yes" if float64_ok
            else f"no  ({self.total_bits} bits > 53)  *** precision loss — use integer arithmetic ***"
        )

        lines = [
            f"FPFormat  : {self!r}",
            f"  total bits : {self.total_bits}",
            f"  signed     : {'yes' if self.signed else 'no'}",
            f"  step (LSB) : {self.step:.6e}",
            f"  real range : [{self.real_min:.10g}, {self.real_max:.10g}]",
            f"  raw range  : [{self.raw_min}, {self.raw_max}]",
        ]
        lines += [
            f"  float32 ok : {float32_str}",
            f"  float64 ok : {float64_str}",
        ]
        return "\n".join(lines)

    # ── factory constructors ────────────────────────────────────────────────

    @classmethod
    def from_str(cls, fmt: str) -> "FPFormat":
        """
        Parse a format string into an FPFormat.

        Supported forms
        ---------------
        ``S<I>.<F>``          signed, e.g. ``S2.24``, ``S0.15``
        ``U<I>.<F>``          unsigned, e.g. ``U0.8``, ``U8.0``
        """
        s = fmt.strip()

        # signed / unsigned: [SU]<I>.<F>
        m = re.fullmatch(r'([SU])(\d+)\.(\d+)', s)
        if m:
            signed = m.group(1) == 'S'
            return cls(int(m.group(2)), int(m.group(3)), signed=signed)

        raise ValueError(
            f"Cannot parse FPFormat from {fmt!r}. "
            "Expected a string like 'S2.24' or 'U0.8'."
        )

    @classmethod
    def from_sfixed(cls, msb: int, lsb: int) -> "FPFormat":
        """Create from VHDL sfixed(msb downto lsb) convention.

        VHDL index convention: int_bits = msb + 1, frac_bits = -lsb.
        Example: sfixed(7 downto -8) → FPFormat(8, 8, signed=True).
        """
        if lsb > 0:
            raise ValueError(
                f"from_sfixed({msb}, {lsb}): lsb must be ≤ 0 for fractional formats. "
                "Use FPFormat directly for integer-only formats."
            )
        return cls(int_bits=msb + 1, frac_bits=-lsb, signed=True)

    @classmethod
    def from_ufixed(cls, msb: int, lsb: int) -> "FPFormat":
        """Create from VHDL ufixed(msb downto lsb) convention.

        VHDL index convention: int_bits = msb + 1, frac_bits = -lsb.
        Example: ufixed(7 downto -8) → FPFormat(8, 8, signed=False).
        """
        if lsb > 0:
            raise ValueError(
                f"from_ufixed({msb}, {lsb}): lsb must be ≤ 0 for fractional formats."
            )
        return cls(int_bits=msb + 1, frac_bits=-lsb, signed=False)

    # ── float carrier check ─────────────────────────────────────────────────

    def fits_in_float32(self) -> bool:
        """True if all values of this format are exactly representable in float32."""
        return self.total_bits <= 24

    def fits_in_float64(self) -> bool:
        return self.total_bits <= 53

    def carrier_warning(self) -> Optional[str]:
        if not self.fits_in_float64():
            return (
                f"{self!r}: {self.total_bits} bits exceeds float64 mantissa (53). "
                "Use integer arithmetic."
            )
        if not self.fits_in_float32():
            return (
                f"{self!r}: {self.total_bits} bits exceeds float32 mantissa (24). "
                "Use float64."
            )
        return None

    # ── quantization ────────────────────────────────────────────────────────

    def zero(self) -> "FixedPointArray":
        """Return a zero-valued scalar FixedPointArray in this format."""
        return self.quantize(np.array(0.0))

    def quantize(
        self,
        x:        np.ndarray,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> "FixedPointArray":
        """Quantize a float64 array to this format.

        Returns a FixedPointArray (values stored as float64, format attached).
        Accepts plain Python scalars, numpy arrays, FixedPointArray, or
        UnquantizedResult as input.
        """
        warn = self.carrier_warning()
        if warn:
            warnings.warn(warn, stacklevel=2)

        # Accept FixedPointArray or UnquantizedResult — extract float64 values
        if isinstance(x, FixedPointArray):
            x = x.values
        elif hasattr(x, "_values") and hasattr(x, "ideal_fmt"):
            x = x._values

        # shift to raw integer domain
        raw_real = np.asarray(x, dtype=np.float64) / self.step
        raw_int  = _apply_round(raw_real, round)

        # saturation / wrap
        if saturate == "saturate":
            raw_int = np.clip(raw_int, self.raw_min, self.raw_max)
        elif saturate == "wrap":
            span = self.raw_max - self.raw_min + 1
            raw_int = ((raw_int - self.raw_min) % span) + self.raw_min
        elif saturate == "assert":
            if np.any(raw_int < self.raw_min) or np.any(raw_int > self.raw_max):
                lo = float(np.min(raw_int))
                hi = float(np.max(raw_int))
                raise OverflowError(
                    f"{self!r}: value out of range [{lo}, {hi}] "
                    f"vs [{self.raw_min}, {self.raw_max}]"
                )

        # convert back to real domain (the stored representation)
        real_val = raw_int * self.step
        if real_val.ndim == 0:
            return FixedPoint(float(real_val), self)
        return FixedPointArray(real_val, self)

    # ── post-operation format inference ────────────────────────────────────

    def after_multiply(self, other: "FPFormat") -> "FPFormat":
        """Exact output format after self × other (before requantization)."""
        int_b  = self.int_bits + other.int_bits + (1 if self.signed or other.signed else 0)
        frac_b = self.frac_bits + other.frac_bits
        signed = self.signed or other.signed
        return FPFormat(int_b, frac_b, signed=signed, offset=0.0)

    def after_add(self, other: "FPFormat") -> "FPFormat":
        """Format needed to hold self + other without overflow."""
        frac_b = max(self.frac_bits, other.frac_bits)
        int_b  = max(self.int_bits, other.int_bits) + 1
        signed = self.signed or other.signed
        return FPFormat(int_b, frac_b, signed=signed)

    def after_divide(self, other: "FPFormat") -> "FPFormat":
        """Conservative format for self / other (full precision, always signed)."""
        int_b  = self.int_bits + other.frac_bits + 1
        frac_b = self.frac_bits + other.int_bits
        return FPFormat(int_b, frac_b, signed=True)


# ─── Fixed-point array (values + format) ────────────────────────────────────

class FixedPointArray:
    """A numpy array paired with its FPFormat.

    Values are stored as float64 (the exact decoded real values).
    This is the *single correct type* to pass between pipeline stages.

    Simulation extensions
    ---------------------
    Supports the Python scalar protocol (``float()``, ``int()``, ``bool()``,
    ``format()``) and numpy interop (``np.asarray()``, ``item()``) so behavior
    pseudo-code can treat a scalar FixedPointArray like an ordinary number.

    Arithmetic with plain Python scalars (``int``, ``float``) is supported on
    both sides — ``fp + 1`` and ``1 + fp`` both work and return an
    ``UnquantizedResult`` just like ``fp + other_fp`` would.
    """

    def __init__(self, values: np.ndarray, fmt: FPFormat) -> None:
        self.values = np.asarray(values, dtype=np.float64)
        self.fmt    = fmt

    # ── scalar protocol ─────────────────────────────────────────────────────

    def item(self) -> float:
        """Return the value as a plain Python float (like ``ndarray.item()``)."""
        return float(self.values.flat[0])

    def __float__(self) -> float:
        """Convert to Python float.  Works for scalar (0-d or size-1) arrays."""
        return float(self.values.flat[0])

    def __int__(self) -> int:
        """Convert to Python int (truncates toward zero)."""
        return int(float(self))

    def __bool__(self) -> bool:
        """Truthiness: non-zero value is truthy.  Consistent with numpy scalars."""
        return bool(float(self) != 0.0)

    def __format__(self, spec: str) -> str:
        """Format the value using a standard Python format spec (e.g. ``'.4f'``)."""
        return format(float(self), spec)

    @property
    def scalar(self) -> float:
        """The real value as a Python float (alias for ``float(self)``)."""
        return float(self)

    # ── numpy interop ───────────────────────────────────────────────────────

    def __array__(self, dtype=None, copy=False) -> np.ndarray:
        """Enable ``np.asarray(fp)`` — returns the underlying float64 values.

        Format information is intentionally dropped; use ``FixedPointArray``
        throughout the pipeline to preserve it.
        """
        arr = self.values
        if dtype is not None:
            arr = arr.astype(dtype, copy=False)
        return arr

    # ── forward arithmetic ──────────────────────────────────────────────────
    # Returns UnquantizedResult — architect must call .quantize() before storage.

    def _coerce(self, other) -> "FixedPointArray":
        """Coerce a plain scalar to FixedPointArray with the same format."""
        if isinstance(other, FixedPointArray):
            return other
        return FixedPointArray(np.asarray(float(other), np.float64), self.fmt)

    def __add__(self, other) -> "UnquantizedResult":
        other = self._coerce(other)
        result    = self.values + other.values
        ideal_fmt = self.fmt.after_add(other.fmt)
        return UnquantizedResult(result, ideal_fmt, op="add",
                                 src_a=self.fmt, src_b=other.fmt)

    def __sub__(self, other) -> "UnquantizedResult":
        other  = self._coerce(other)
        result = self.values - other.values
        # Subtraction can go negative even for unsigned operands.
        base_fmt  = self.fmt.after_add(other.fmt)
        ideal_fmt = FPFormat(base_fmt.int_bits, base_fmt.frac_bits, signed=True)
        return UnquantizedResult(result, ideal_fmt, op="subtract",
                                 src_a=self.fmt, src_b=other.fmt)

    def __mul__(self, other) -> "UnquantizedResult":
        other     = self._coerce(other)
        result    = self.values * other.values
        ideal_fmt = self.fmt.after_multiply(other.fmt)
        return UnquantizedResult(result, ideal_fmt, op="multiply",
                                 src_a=self.fmt, src_b=other.fmt)

    def __truediv__(self, other) -> "UnquantizedResult":
        """True division — result is full-precision, always signed."""
        other     = self._coerce(other)
        # Guard against division by zero: produce 0 when divisor is 0.
        result    = np.where(other.values != 0.0, self.values / other.values, 0.0)
        ideal_fmt = self.fmt.after_divide(other.fmt)
        return UnquantizedResult(result, ideal_fmt, op="divide",
                                 src_a=self.fmt, src_b=other.fmt)

    def __floordiv__(self, other) -> "UnquantizedResult":
        """Floor division — truncates the quotient toward −∞."""
        other     = self._coerce(other)
        result    = np.where(other.values != 0.0,
                             np.floor(self.values / other.values), 0.0)
        ideal_fmt = self.fmt.after_divide(other.fmt)
        return UnquantizedResult(result, ideal_fmt, op="floordiv",
                                 src_a=self.fmt, src_b=other.fmt)

    def __neg__(self) -> "UnquantizedResult":
        """Negation.  For unsigned sources the result is forced signed."""
        result    = -self.values
        ideal_fmt = (
            self.fmt if self.fmt.signed
            else FPFormat(self.fmt.int_bits + 1, self.fmt.frac_bits, signed=True)
        )
        return UnquantizedResult(result, ideal_fmt, op="negate",
                                 src_a=self.fmt, src_b=None)

    def __abs__(self) -> "UnquantizedResult":
        """Absolute value."""
        result    = np.abs(self.values)
        ideal_fmt = FPFormat(self.fmt.int_bits, self.fmt.frac_bits, signed=False)
        return UnquantizedResult(result, ideal_fmt, op="abs",
                                 src_a=self.fmt, src_b=None)

    # ── reverse arithmetic (plain scalar on the left) ───────────────────────

    def __radd__(self, other) -> "UnquantizedResult":
        return self.__add__(other)           # addition is commutative

    def __rsub__(self, other) -> "UnquantizedResult":
        other = self._coerce(other)
        return other.__sub__(self)

    def __rmul__(self, other) -> "UnquantizedResult":
        return self.__mul__(other)           # multiplication is commutative

    def __rtruediv__(self, other) -> "UnquantizedResult":
        other = self._coerce(other)
        return other.__truediv__(self)

    def __rfloordiv__(self, other) -> "UnquantizedResult":
        other = self._coerce(other)
        return other.__floordiv__(self)

    # ── comparison operators ────────────────────────────────────────────────
    # Return plain numpy bool (scalar → usable in ``if`` / ``while``).

    def _rval(self, other) -> np.ndarray:
        """Extract comparable float64 value from another operand."""
        if isinstance(other, FixedPointArray):
            return other.values
        return np.asarray(float(other), np.float64)

    def __lt__(self, other) -> np.ndarray:   return self.values <  self._rval(other)  # noqa: E701
    def __le__(self, other) -> np.ndarray:   return self.values <= self._rval(other)  # noqa: E701
    def __gt__(self, other) -> np.ndarray:   return self.values >  self._rval(other)  # noqa: E701
    def __ge__(self, other) -> np.ndarray:   return self.values >= self._rval(other)  # noqa: E701
    def __eq__(self, other) -> np.ndarray:   return self.values == self._rval(other)  # noqa: E701
    def __ne__(self, other) -> np.ndarray:   return self.values != self._rval(other)  # noqa: E701

    # ── explicit requantization ─────────────────────────────────────────────

    def requantize(
        self,
        target:   FPFormat,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> "FixedPointArray":
        """Re-quantize to a different format."""
        return target.quantize(self.values, round=round, saturate=saturate)

    def cast(
        self,
        target:   FPFormat,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> "FixedPointArray":
        """Alias for requantize — convert to *target* format."""
        return target.quantize(self.values, round=round, saturate=saturate)

    # ── introspection ───────────────────────────────────────────────────────

    @property
    def shape(self):
        return self.values.shape

    def __repr__(self) -> str:
        if self.values.ndim == 0 or self.values.size == 1:
            return f"FixedPointArray({self.fmt!r}, value={float(self):.6g})"
        return f"FixedPointArray({self.fmt!r}, shape={self.shape})"


# ─── Scalar fixed-point value ───────────────────────────────────────────────

class FixedPoint(FixedPointArray):
    """A single quantized scalar value.

    Subclass of ``FixedPointArray`` — ``isinstance(fp, FixedPointArray)`` is
    always ``True``, so existing code that checks for ``FixedPointArray`` needs
    no changes.

    Differences from ``FixedPointArray``:
    - Constructor takes a plain ``float`` (not a numpy array).
    - ``__eq__`` / ``__ne__`` return ``bool`` so instances can be used in ``if``
      statements and as dict keys.
    - ``__hash__`` is defined — ``FixedPoint`` objects are hashable.
    - ``.value`` property for explicit float extraction.
    - ``__repr__`` says ``FixedPoint`` instead of ``FixedPointArray``.

    Created automatically by ``FPFormat.quantize()`` when the input is a Python
    scalar or a 0-d numpy array.
    """

    def __init__(self, value: float, fmt: FPFormat) -> None:
        super().__init__(np.asarray(float(value), dtype=np.float64), fmt)

    @property
    def value(self) -> float:
        """The quantized real value as a plain Python float."""
        return float(self.values)

    def __eq__(self, other) -> bool:
        try:
            return bool(float(self) == float(other))
        except (TypeError, ValueError):
            return NotImplemented

    def __ne__(self, other) -> bool:
        eq = self.__eq__(other)
        return not eq if eq is not NotImplemented else NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"FixedPoint({self.fmt!r}, {self.value:.6g})"


# ─── Unquantized intermediate result ────────────────────────────────────────

class UnquantizedResult:
    """Intermediate result of an arithmetic operation — NOT yet a FixedPointArray.

    Forces the architect to make an explicit quantization decision before the
    value can be used as a signal.  Arithmetic with plain Python scalars and
    with other ``UnquantizedResult`` objects is supported to allow expressions
    like ``a + b + carry_in`` to chain naturally — the result remains
    unquantized and must still be explicitly quantized before storage.

    The ``__array__`` method intentionally raises so that ``np.asarray()`` on
    an unquantized result fails loudly rather than silently producing garbage.
    """

    def __init__(
        self,
        values:     np.ndarray,
        ideal_fmt:  FPFormat,
        op:         str,
        src_a:      Optional[FPFormat],
        src_b:      Optional[FPFormat],
    ) -> None:
        self._values   = np.asarray(values, dtype=np.float64)
        self.ideal_fmt = ideal_fmt
        self._op       = op
        self._src_a    = src_a
        self._src_b    = src_b

    # ── explicit quantization ───────────────────────────────────────────────

    def quantize(
        self,
        target:   FPFormat,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> FixedPointArray:
        """Quantize to *target* format."""
        return target.quantize(self._values, round=round, saturate=saturate)

    def keep_full(self) -> FixedPointArray:
        """Accept the full-precision intermediate format without requantization."""
        if self._values.ndim == 0:
            return FixedPoint(float(self._values), self.ideal_fmt)
        return FixedPointArray(self._values, self.ideal_fmt)

    @property
    def ideal_values(self) -> np.ndarray:
        return self._values

    # ── chained arithmetic (scalar or FixedPointArray on either side) ────────
    # Produces a new UnquantizedResult so that ``(a + b) + carry_in`` chains
    # work in behavior pseudo-code without breaking the explicit-quantization
    # invariant: the result still must be quantized before storage.

    def _scalar_fmt(self, value: float) -> FPFormat:
        """Minimal integer FPFormat that holds a plain Python scalar."""
        absv = abs(value)
        int_b = max(1, int(np.ceil(np.log2(absv + 1))) + 1) if absv > 0 else 1
        return FPFormat(int_b, 0, signed=(value < 0))

    def __add__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            new_val = self._values + other.values
            new_fmt = self.ideal_fmt.after_add(other.fmt)
        elif isinstance(other, UnquantizedResult):
            new_val = self._values + other._values
            new_fmt = self.ideal_fmt.after_add(other.ideal_fmt)
        else:
            new_val = self._values + float(other)
            new_fmt = self.ideal_fmt.after_add(self._scalar_fmt(float(other)))
        return UnquantizedResult(new_val, new_fmt, "add", self.ideal_fmt, None)

    def __radd__(self, other) -> "UnquantizedResult":
        return self.__add__(other)

    def __sub__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            new_val = self._values - other.values
            new_fmt = self.ideal_fmt.after_add(other.fmt)
        elif isinstance(other, UnquantizedResult):
            new_val = self._values - other._values
            new_fmt = self.ideal_fmt.after_add(other.ideal_fmt)
        else:
            new_val = self._values - float(other)
            new_fmt = self.ideal_fmt.after_add(self._scalar_fmt(float(other)))
        base = new_fmt
        return UnquantizedResult(
            new_val,
            FPFormat(base.int_bits, base.frac_bits, signed=True),
            "subtract", self.ideal_fmt, None,
        )

    def __rsub__(self, other) -> "UnquantizedResult":
        if isinstance(other, (int, float)):
            new_val = float(other) - self._values
            new_fmt = self.ideal_fmt.after_add(self._scalar_fmt(float(other)))
            return UnquantizedResult(
                new_val,
                FPFormat(new_fmt.int_bits, new_fmt.frac_bits, signed=True),
                "subtract", None, self.ideal_fmt,
            )
        return NotImplemented

    def __mul__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            new_val = self._values * other.values
            new_fmt = self.ideal_fmt.after_multiply(other.fmt)
        elif isinstance(other, UnquantizedResult):
            new_val = self._values * other._values
            new_fmt = self.ideal_fmt.after_multiply(other.ideal_fmt)
        else:
            new_val = self._values * float(other)
            new_fmt = self.ideal_fmt   # scalar multiply doesn't expand format significantly
        return UnquantizedResult(new_val, new_fmt, "multiply", self.ideal_fmt, None)

    def __rmul__(self, other) -> "UnquantizedResult":
        return self.__mul__(other)

    def __neg__(self) -> "UnquantizedResult":
        new_fmt = FPFormat(
            self.ideal_fmt.int_bits, self.ideal_fmt.frac_bits, signed=True
        )
        return UnquantizedResult(-self._values, new_fmt, "negate", self.ideal_fmt, None)

    # ── scalar protocol ─────────────────────────────────────────────────────
    # These return the ideal (full-precision) value.  They exist so that
    # behavior code like ``float(a + b)`` can produce a usable number when the
    # format is wide enough to carry full precision.

    def item(self) -> float:
        """Return the ideal full-precision value as a Python float."""
        return float(self._values.flat[0])

    def __float__(self) -> float:
        return float(self._values.flat[0])

    def __int__(self) -> int:
        return int(float(self))

    def __bool__(self) -> bool:
        return bool(float(self) != 0.0)

    def __format__(self, spec: str) -> str:
        return format(float(self), spec)

    # ── guard against silent misuse ─────────────────────────────────────────

    def __array__(self, *a, **kw):
        raise TypeError(
            f"UnquantizedResult from '{self._op}' must be explicitly quantized "
            f"via .quantize(target_fmt) before use.\n"
            f"  Ideal full-precision format: {self.ideal_fmt} "
            f"({self.ideal_fmt.total_bits} bits)\n"
            f"  Hint: call .keep_full() to retain full precision, "
            f"or .quantize(FPFormat(...)) to truncate."
        )

    def __repr__(self) -> str:
        src = f"{self._src_a!r}" if self._src_a else "?"
        if self._src_b:
            src += f" ⊗ {self._src_b!r}"
        return (
            f"UnquantizedResult(op={self._op!r}, {src} → ideal={self.ideal_fmt!r})"
        )


# ─── Common format presets ───────────────────────────────────────────────────

class Fmt:
    """Commonly used format presets."""
    U8     = FPFormat(0, 8,  signed=False)   # standard 8-bit image
    U10    = FPFormat(0, 10, signed=False)
    U12    = FPFormat(0, 12, signed=False)
    U16    = FPFormat(0, 16, signed=False)
    S2_24  = FPFormat(2, 24, signed=True)    # HDR scene-linear

    @staticmethod
    def u(total_bits: int) -> FPFormat:
        return FPFormat(0, total_bits, signed=False)

    @staticmethod
    def s(int_bits: int, frac_bits: int) -> FPFormat:
        return FPFormat(int_bits, frac_bits, signed=True)

    @staticmethod
    def sfixed(msb: int, lsb: int) -> FPFormat:
        """VHDL sfixed(msb downto lsb) shorthand."""
        return FPFormat.from_sfixed(msb, lsb)

    @staticmethod
    def ufixed(msb: int, lsb: int) -> FPFormat:
        """VHDL ufixed(msb downto lsb) shorthand."""
        return FPFormat.from_ufixed(msb, lsb)
