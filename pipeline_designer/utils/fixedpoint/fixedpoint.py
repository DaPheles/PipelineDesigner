"""
fixedpoint/quant.py
===================
Fixed-point type system and quantization primitives.

Design principles
-----------------
1. Every value in the pipeline has an *explicit* fixed-point format attached.
2. Operations are *not* implicitly quantized — the architect must state the
   output format after every stage.  The framework raises if they forget.
3. All arithmetic (add, subtract, multiply) is performed on raw scaled integers
   so results are exact at any bit width.  Float64 views are computed on demand
   for display and interop; they are exact for ≤53-bit formats and approximate
   for wider formats.
4. The framework records every quantization decision and can replay any
   stage to measure the error introduced.

Type hierarchy
--------------
``FixedPointArray``
    A numpy array paired with an FPFormat.  Designed for batch / ISP-pipeline
    work where an entire channel or row of pixels is quantized together.
    Supports numpy ufuncs, broadcasting, and format-tracking arithmetic.

    Internal storage: raw scaled integers (``np.int64`` for ≤63-bit formats,
    Python ``int`` objects via ``np.object_`` for wider formats).  The
    ``.values`` property returns float64 real values computed on demand.

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

    For wide formats (>53 bits) it carries raw integers internally so that
    chained arithmetic and final quantization remain exact.
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


# ─── Integer-domain helpers ──────────────────────────────────────────────────

def _raw_dtype(total_bits: int):
    """numpy dtype for exact raw-integer storage at the given format width."""
    return np.int64 if total_bits <= 63 else object


def _ishift(raw: np.ndarray, shift: int) -> np.ndarray:
    """Left (shift > 0) or right (shift < 0) integer shift of a raw array.

    Handles both np.int64 and Python-int object arrays.  Promotes to object
    automatically when a left-shift would overflow int64.
    """
    if shift == 0:
        return raw
    is_obj = raw.dtype == object
    if shift > 0:
        if is_obj:
            return np.vectorize(lambda v: v << shift)(raw)
        if shift >= 62:
            # Promote to Python int to avoid int64 overflow
            obj = np.vectorize(int)(raw.astype(object))
            return np.vectorize(lambda v: v << shift)(obj)
        return raw << np.int64(shift)
    else:
        s = -shift
        if is_obj:
            return np.vectorize(lambda v: v >> s)(raw)
        return raw >> np.int64(s)


def _ishift_round(raw: np.ndarray, shift: int, mode: RoundMode) -> np.ndarray:
    """Right-shift with rounding (shift > 0 always)."""
    is_obj = raw.dtype == object
    if mode == "truncate":
        return _ishift(raw, -shift)
    half = 1 << (shift - 1)
    if mode == "round_half_up":
        if is_obj:
            return np.vectorize(lambda v: (v + half) >> shift)(raw)
        return (raw + np.int64(half)) >> np.int64(shift)
    elif mode == "round_half_even":
        mask = (1 << shift) - 1
        if is_obj:
            def _banker(v):
                r = (v + half) >> shift
                return r - 1 if (v & mask) == half and (r & 1) else r
            return np.vectorize(_banker)(raw)
        rounded = (raw + np.int64(half)) >> np.int64(shift)
        is_tie = (raw & np.int64(mask)) == np.int64(half)
        is_odd  = (rounded & np.int64(1)).astype(bool)
        return np.where(is_tie & is_odd, rounded - np.int64(1), rounded)
    elif mode == "round_away":
        if is_obj:
            def _away(v):
                return (v + half) >> shift if v >= 0 else -((-v + half) >> shift)
            return np.vectorize(_away)(raw)
        pos = (raw + np.int64(half)) >> np.int64(shift)
        neg = -((-raw + np.int64(half)) >> np.int64(shift))
        return np.where(raw >= np.int64(0), pos, neg)
    else:
        raise ValueError(f"Unknown rounding mode: {mode!r}")


def _clip_raw(raw: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """Saturate a raw integer array to [lo, hi]."""
    if raw.dtype == object:
        return np.vectorize(lambda v: max(lo, min(hi, v)))(raw)
    return np.clip(raw, lo, hi)


def _promote(raw: np.ndarray, total_bits: int) -> np.ndarray:
    """Ensure raw array uses np.int64 (≤63 bits) or Python-int objects (>63 bits).

    Preserves shape including 0-d arrays.
    """
    if total_bits <= 63:
        return raw if raw.dtype == np.int64 else raw.astype(np.int64)
    if raw.dtype == object:
        return raw
    # int64 → Python int object array; must preserve shape (incl. 0-d)
    if raw.ndim == 0:
        return np.array(int(raw), dtype=object)
    return np.vectorize(lambda v: int(v), otypes=[object])(raw)


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
        """Informational string about float carrier precision (used by __str__)."""
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

        Returns a FixedPointArray (raw integers stored exactly, float64 view
        on demand).  Accepts plain Python scalars, numpy arrays,
        FixedPointArray, or UnquantizedResult as input.

        For FixedPointArray / UnquantizedResult inputs that carry raw integers
        internally the integer path is taken automatically for exact results at
        any bit width.
        """
        # Warn only when float64 itself is insufficient (>53 bits).
        # The old float32 warning was spurious: the code already uses float64.
        if not self.fits_in_float64():
            warnings.warn(
                f"{self!r}: {self.total_bits} bits exceeds float64 mantissa (53). "
                "Float64 input is limited to 53-bit precision; "
                "arithmetic on results will be exact in the integer domain.",
                stacklevel=2,
            )

        # ── integer path: source carries raw ints ───────────────────────────
        if isinstance(x, FixedPointArray):
            return self._quantize_from_raw(x._raw, x.fmt.frac_bits, round, saturate)
        if isinstance(x, UnquantizedResult) and x._raw is not None:
            return self._quantize_from_raw(x._raw, x.ideal_fmt.frac_bits, round, saturate)

        # ── float64 path ─────────────────────────────────────────────────────
        if hasattr(x, "_values") and hasattr(x, "ideal_fmt"):
            x = x._values   # UnquantizedResult without _raw

        raw_real = np.asarray(x, dtype=np.float64) / self.step
        raw_int  = _apply_round(raw_real, round)

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

        # Convert float64 result to integer storage.
        # float64 input is bounded to ≤53-bit precision so int64 is always safe.
        raw_arr = _promote(raw_int.astype(np.int64), self.total_bits)
        if raw_arr.ndim == 0:
            return FixedPoint._from_raw(raw_arr, self)
        return FixedPointArray._from_raw(raw_arr, self)

    def _quantize_from_raw(
        self,
        src_raw:  np.ndarray,
        src_frac: int,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> "FixedPointArray":
        """Exact quantization from a raw-integer source (no float conversion).

        src_raw : integer array representing values at src_frac fractional bits.
        """
        shift = src_frac - self.frac_bits   # +ve → right-shift, −ve → left-shift

        if shift > 0:
            raw_int = _ishift_round(src_raw, shift, round)
        elif shift < 0:
            raw_int = _ishift(src_raw, -shift)  # upscale: exact
        else:
            raw_int = src_raw

        # saturation / wrap on integers
        if saturate == "saturate":
            raw_int = _clip_raw(raw_int, self.raw_min, self.raw_max)
        elif saturate == "wrap":
            span = self.raw_max - self.raw_min + 1
            if raw_int.dtype == object:
                raw_int = np.vectorize(
                    lambda v: ((v - self.raw_min) % span) + self.raw_min
                )(raw_int)
            else:
                raw_int = ((raw_int - self.raw_min) % span) + self.raw_min
        elif saturate == "assert":
            if raw_int.dtype == object:
                oob = any(v < self.raw_min or v > self.raw_max for v in raw_int.flat)
            else:
                oob = bool(np.any(raw_int < self.raw_min) or np.any(raw_int > self.raw_max))
            if oob:
                raise OverflowError(
                    f"{self!r}: value out of range vs [{self.raw_min}, {self.raw_max}]"
                )

        raw_arr = _promote(raw_int, self.total_bits)
        if raw_arr.ndim == 0:
            return FixedPoint._from_raw(raw_arr, self)
        return FixedPointArray._from_raw(raw_arr, self)

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

    Internally stores raw scaled integers for exact representation at any
    bit width.  The ``.values`` property returns float64 real values on
    demand (exact for ≤53-bit formats, approximate for wider formats).

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
        self.fmt = fmt
        fv  = np.asarray(values, dtype=np.float64)
        # Convert real values → raw integers.  For values produced by
        # quantize() this round-trip is exact for ≤53-bit formats.
        raw = np.round(fv / fmt.step)
        self._raw = raw.astype(_raw_dtype(fmt.total_bits))

    @classmethod
    def _from_raw(cls, raw: np.ndarray, fmt: FPFormat) -> "FixedPointArray":
        """Construct directly from a raw-integer array — no float conversion."""
        obj = object.__new__(cls)
        obj.fmt  = fmt
        obj._raw = np.asarray(raw)
        return obj

    # ── real-value view ─────────────────────────────────────────────────────

    @property
    def values(self) -> np.ndarray:
        """Real values as float64.

        Exact for formats ≤53 bits.  For wider formats the float64 view is
        approximate; use ``._raw`` directly for exact integer arithmetic.
        """
        if self.fmt.total_bits <= 53:
            return self._raw.astype(np.float64) * self.fmt.step
        # longdouble (80-bit on x86-64) gives one extra guard word
        return (
            self._raw.astype(np.longdouble) * np.longdouble(self.fmt.step)
        ).astype(np.float64)

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
        """Enable ``np.asarray(fp)`` — returns the underlying float64 values."""
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
        other     = self._coerce(other)
        ideal_fmt = self.fmt.after_add(other.fmt)
        if ideal_fmt.total_bits > 53:
            a_sh  = ideal_fmt.frac_bits - self.fmt.frac_bits
            b_sh  = ideal_fmt.frac_bits - other.fmt.frac_bits
            a_raw = _promote(self._raw,  ideal_fmt.total_bits)
            b_raw = _promote(other._raw, ideal_fmt.total_bits)
            raw   = _ishift(a_raw, a_sh) + _ishift(b_raw, b_sh)
            return UnquantizedResult._from_raw(raw, ideal_fmt, "add",
                                               self.fmt, other.fmt)
        result = self.values + other.values
        return UnquantizedResult(result, ideal_fmt, op="add",
                                 src_a=self.fmt, src_b=other.fmt)

    def __sub__(self, other) -> "UnquantizedResult":
        other     = self._coerce(other)
        base_fmt  = self.fmt.after_add(other.fmt)
        ideal_fmt = FPFormat(base_fmt.int_bits, base_fmt.frac_bits, signed=True)
        if ideal_fmt.total_bits > 53:
            a_sh  = ideal_fmt.frac_bits - self.fmt.frac_bits
            b_sh  = ideal_fmt.frac_bits - other.fmt.frac_bits
            a_raw = _promote(self._raw,  ideal_fmt.total_bits)
            b_raw = _promote(other._raw, ideal_fmt.total_bits)
            raw   = _ishift(a_raw, a_sh) - _ishift(b_raw, b_sh)
            return UnquantizedResult._from_raw(raw, ideal_fmt, "subtract",
                                               self.fmt, other.fmt)
        result = self.values - other.values
        return UnquantizedResult(result, ideal_fmt, op="subtract",
                                 src_a=self.fmt, src_b=other.fmt)

    def __mul__(self, other) -> "UnquantizedResult":
        other     = self._coerce(other)
        ideal_fmt = self.fmt.after_multiply(other.fmt)
        if ideal_fmt.total_bits > 53:
            # a_raw × b_raw is exact; format tracks combined frac bits
            a_raw = _promote(self._raw,  ideal_fmt.total_bits)
            b_raw = _promote(other._raw, ideal_fmt.total_bits)
            raw   = a_raw * b_raw
            return UnquantizedResult._from_raw(raw, ideal_fmt, "multiply",
                                               self.fmt, other.fmt)
        result = self.values * other.values
        return UnquantizedResult(result, ideal_fmt, op="multiply",
                                 src_a=self.fmt, src_b=other.fmt)

    def __truediv__(self, other) -> "UnquantizedResult":
        """True division — result is full-precision, always signed."""
        other     = self._coerce(other)
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
        ideal_fmt = (
            self.fmt if self.fmt.signed
            else FPFormat(self.fmt.int_bits + 1, self.fmt.frac_bits, signed=True)
        )
        if ideal_fmt.total_bits > 53:
            raw = _promote(-self._raw, ideal_fmt.total_bits)
            return UnquantizedResult._from_raw(raw, ideal_fmt, "negate",
                                               self.fmt, None)
        return UnquantizedResult(-self.values, ideal_fmt, op="negate",
                                 src_a=self.fmt, src_b=None)

    def __abs__(self) -> "UnquantizedResult":
        """Absolute value."""
        ideal_fmt = FPFormat(self.fmt.int_bits, self.fmt.frac_bits, signed=False)
        if ideal_fmt.total_bits > 53:
            if self._raw.dtype == object:
                raw = np.vectorize(abs)(self._raw)
            else:
                raw = np.abs(self._raw)
            return UnquantizedResult._from_raw(raw, ideal_fmt, "abs",
                                               self.fmt, None)
        return UnquantizedResult(np.abs(self.values), ideal_fmt, op="abs",
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
        return target._quantize_from_raw(self._raw, self.fmt.frac_bits,
                                         round=round, saturate=saturate)

    def cast(
        self,
        target:   FPFormat,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> "FixedPointArray":
        """Alias for requantize — convert to *target* format."""
        return self.requantize(target, round=round, saturate=saturate)

    # ── introspection ───────────────────────────────────────────────────────

    @property
    def shape(self):
        return self._raw.shape

    def __repr__(self) -> str:
        if self._raw.ndim == 0 or self._raw.size == 1:
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

    @classmethod
    def _from_raw(cls, raw, fmt: FPFormat) -> "FixedPoint":
        """Construct from a raw integer scalar."""
        obj = object.__new__(cls)
        obj.fmt  = fmt
        obj._raw = np.asarray(raw)
        return obj

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

    For wide formats (>53 bits) raw integers are carried internally so that
    chained arithmetic and ``quantize()`` / ``keep_full()`` remain exact.

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
        self._raw      = None   # set by _from_raw() for integer-backed results
        self.ideal_fmt = ideal_fmt
        self._op       = op
        self._src_a    = src_a
        self._src_b    = src_b

    @classmethod
    def _from_raw(
        cls,
        raw:       np.ndarray,
        ideal_fmt: FPFormat,
        op:        str,
        src_a:     Optional[FPFormat],
        src_b:     Optional[FPFormat],
    ) -> "UnquantizedResult":
        """Construct from a raw-integer array (exact, no float conversion)."""
        obj            = cls.__new__(cls)
        obj._raw       = np.asarray(raw)
        obj.ideal_fmt  = ideal_fmt
        obj._op        = op
        obj._src_a     = src_a
        obj._src_b     = src_b
        # Compute approximate float64 view for scalar protocol / display
        if ideal_fmt.total_bits <= 53:
            obj._values = obj._raw.astype(np.float64) * ideal_fmt.step
        else:
            obj._values = (
                obj._raw.astype(np.longdouble) * np.longdouble(ideal_fmt.step)
            ).astype(np.float64)
        return obj

    # ── explicit quantization ───────────────────────────────────────────────

    def quantize(
        self,
        target:   FPFormat,
        round:    RoundMode    = "truncate",
        saturate: SaturateMode = "saturate",
    ) -> FixedPointArray:
        """Quantize to *target* format."""
        if self._raw is not None:
            return target._quantize_from_raw(self._raw, self.ideal_fmt.frac_bits,
                                             round, saturate)
        return target.quantize(self._values, round=round, saturate=saturate)

    def keep_full(self) -> FixedPointArray:
        """Accept the full-precision intermediate format without requantization."""
        if self._raw is not None:
            if self._raw.ndim == 0:
                return FixedPoint._from_raw(self._raw, self.ideal_fmt)
            return FixedPointArray._from_raw(self._raw, self.ideal_fmt)
        if self._values.ndim == 0:
            return FixedPoint(float(self._values), self.ideal_fmt)
        return FixedPointArray(self._values, self.ideal_fmt)

    @property
    def ideal_values(self) -> np.ndarray:
        return self._values

    # ── chained arithmetic ──────────────────────────────────────────────────
    # Produces a new UnquantizedResult.  Integer path taken when the result
    # format exceeds 53 bits AND integer operands are available.

    def _scalar_fmt(self, value: float) -> FPFormat:
        """Minimal integer FPFormat that holds a plain Python scalar."""
        absv = abs(value)
        int_b = max(1, int(np.ceil(np.log2(absv + 1))) + 1) if absv > 0 else 1
        return FPFormat(int_b, 0, signed=(value < 0))

    def _scalar_to_raw(self, value: float, frac_bits: int):
        """Convert a plain Python scalar to a raw integer at frac_bits resolution."""
        return round(value * (1 << frac_bits))

    def __add__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            new_fmt = self.ideal_fmt.after_add(other.fmt)
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                b_sh  = new_fmt.frac_bits - other.fmt.frac_bits
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = _ishift(a_raw, a_sh) + _ishift(b_raw, b_sh)
                return UnquantizedResult._from_raw(raw, new_fmt, "add",
                                                   self.ideal_fmt, None)
            return UnquantizedResult(self._values + other.values, new_fmt,
                                     "add", self.ideal_fmt, None)
        elif isinstance(other, UnquantizedResult):
            new_fmt = self.ideal_fmt.after_add(other.ideal_fmt)
            if (new_fmt.total_bits > 53
                    and self._raw is not None and other._raw is not None):
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                b_sh  = new_fmt.frac_bits - other.ideal_fmt.frac_bits
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = _ishift(a_raw, a_sh) + _ishift(b_raw, b_sh)
                return UnquantizedResult._from_raw(raw, new_fmt, "add",
                                                   self.ideal_fmt, None)
            return UnquantizedResult(self._values + other._values, new_fmt,
                                     "add", self.ideal_fmt, None)
        else:
            sv      = float(other)
            new_fmt = self.ideal_fmt.after_add(self._scalar_fmt(sv))
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_sh    = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                s_raw   = np.asarray(self._scalar_to_raw(sv, new_fmt.frac_bits))
                a_raw   = _promote(self._raw, new_fmt.total_bits)
                s_raw   = _promote(s_raw,     new_fmt.total_bits)
                raw     = _ishift(a_raw, a_sh) + s_raw
                return UnquantizedResult._from_raw(raw, new_fmt, "add",
                                                   self.ideal_fmt, None)
            return UnquantizedResult(self._values + sv, new_fmt,
                                     "add", self.ideal_fmt, None)

    def __radd__(self, other) -> "UnquantizedResult":
        return self.__add__(other)

    def __sub__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            base    = self.ideal_fmt.after_add(other.fmt)
            new_fmt = FPFormat(base.int_bits, base.frac_bits, signed=True)
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                b_sh  = new_fmt.frac_bits - other.fmt.frac_bits
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = _ishift(a_raw, a_sh) - _ishift(b_raw, b_sh)
                return UnquantizedResult._from_raw(raw, new_fmt, "subtract",
                                                   self.ideal_fmt, None)
            new_val = self._values - other.values
        elif isinstance(other, UnquantizedResult):
            base    = self.ideal_fmt.after_add(other.ideal_fmt)
            new_fmt = FPFormat(base.int_bits, base.frac_bits, signed=True)
            if (new_fmt.total_bits > 53
                    and self._raw is not None and other._raw is not None):
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                b_sh  = new_fmt.frac_bits - other.ideal_fmt.frac_bits
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = _ishift(a_raw, a_sh) - _ishift(b_raw, b_sh)
                return UnquantizedResult._from_raw(raw, new_fmt, "subtract",
                                                   self.ideal_fmt, None)
            new_val = self._values - other._values
        else:
            sv      = float(other)
            base    = self.ideal_fmt.after_add(self._scalar_fmt(sv))
            new_fmt = FPFormat(base.int_bits, base.frac_bits, signed=True)
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                s_raw = np.asarray(self._scalar_to_raw(sv, new_fmt.frac_bits))
                a_raw = _promote(self._raw, new_fmt.total_bits)
                s_raw = _promote(s_raw,     new_fmt.total_bits)
                raw   = _ishift(a_raw, a_sh) - s_raw
                return UnquantizedResult._from_raw(raw, new_fmt, "subtract",
                                                   self.ideal_fmt, None)
            new_val = self._values - sv
        return UnquantizedResult(
            new_val,
            FPFormat(new_fmt.int_bits, new_fmt.frac_bits, signed=True),
            "subtract", self.ideal_fmt, None,
        )

    def __rsub__(self, other) -> "UnquantizedResult":
        if isinstance(other, (int, float)):
            sv      = float(other)
            base    = self.ideal_fmt.after_add(self._scalar_fmt(sv))
            new_fmt = FPFormat(base.int_bits, base.frac_bits, signed=True)
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_sh  = new_fmt.frac_bits - self.ideal_fmt.frac_bits
                s_raw = np.asarray(self._scalar_to_raw(sv, new_fmt.frac_bits))
                a_raw = _promote(self._raw, new_fmt.total_bits)
                s_raw = _promote(s_raw,     new_fmt.total_bits)
                raw   = s_raw - _ishift(a_raw, a_sh)
                return UnquantizedResult._from_raw(raw, new_fmt, "subtract",
                                                   None, self.ideal_fmt)
            return UnquantizedResult(
                sv - self._values, new_fmt, "subtract", None, self.ideal_fmt,
            )
        return NotImplemented

    def __mul__(self, other) -> "UnquantizedResult":
        if isinstance(other, FixedPointArray):
            new_fmt = self.ideal_fmt.after_multiply(other.fmt)
            if new_fmt.total_bits > 53 and self._raw is not None:
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = a_raw * b_raw
                return UnquantizedResult._from_raw(raw, new_fmt, "multiply",
                                                   self.ideal_fmt, None)
            return UnquantizedResult(self._values * other.values, new_fmt,
                                     "multiply", self.ideal_fmt, None)
        elif isinstance(other, UnquantizedResult):
            new_fmt = self.ideal_fmt.after_multiply(other.ideal_fmt)
            if (new_fmt.total_bits > 53
                    and self._raw is not None and other._raw is not None):
                a_raw = _promote(self._raw,  new_fmt.total_bits)
                b_raw = _promote(other._raw, new_fmt.total_bits)
                raw   = a_raw * b_raw
                return UnquantizedResult._from_raw(raw, new_fmt, "multiply",
                                                   self.ideal_fmt, None)
            return UnquantizedResult(self._values * other._values, new_fmt,
                                     "multiply", self.ideal_fmt, None)
        else:
            new_val = self._values * float(other)
            return UnquantizedResult(new_val, self.ideal_fmt,
                                     "multiply", self.ideal_fmt, None)

    def __rmul__(self, other) -> "UnquantizedResult":
        return self.__mul__(other)

    def __neg__(self) -> "UnquantizedResult":
        new_fmt = FPFormat(
            self.ideal_fmt.int_bits, self.ideal_fmt.frac_bits, signed=True
        )
        if self._raw is not None and new_fmt.total_bits > 53:
            raw = _promote(-self._raw, new_fmt.total_bits)
            return UnquantizedResult._from_raw(raw, new_fmt, "negate",
                                               self.ideal_fmt, None)
        return UnquantizedResult(-self._values, new_fmt, "negate",
                                 self.ideal_fmt, None)

    # ── scalar protocol ─────────────────────────────────────────────────────

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
