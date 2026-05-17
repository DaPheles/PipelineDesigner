"""
fixedpoint — Fixed-point arithmetic type system.

Public API
----------
FPFormat          : Immutable format descriptor (int_bits, frac_bits, signed, offset).
FixedPointArray   : numpy array paired with its FPFormat (values as float64).
FixedPoint        : Single quantized scalar — subclass of FixedPointArray, hashable.
UnquantizedResult : Intermediate from arithmetic — forces explicit quantization.
Fmt               : Preset format constants (U8, U12, U16, S2_24, OB1_17, ...).
RoundMode         : Literal type for rounding mode strings.
SaturateMode      : Literal type for saturation mode strings.
"""

from .fixedpoint import (
    FPFormat,
    FixedPointArray,
    FixedPoint,
    UnquantizedResult,
    Fmt,
    RoundMode,
    SaturateMode,
)

__all__ = [
    "FPFormat",
    "FixedPointArray",
    "FixedPoint",
    "UnquantizedResult",
    "Fmt",
    "RoundMode",
    "SaturateMode",
]
