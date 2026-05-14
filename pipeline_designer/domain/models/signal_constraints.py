"""Signal-type constraints derived from PortSignalClass.

Rules
-----
  CLOCK   → std_logic only (scalar)
  RESET   → std_logic only (scalar)
  CONTROL → std_logic (width = 1) or std_logic_vector (width > 1)
  DATA    → sfixed or ufixed only (ieee.fixed_pkg fixed-point)

Use these helpers everywhere the UI or the model needs to validate or reset a
port's signal_type when its signal_class is changed.
"""

from __future__ import annotations

from pipeline_designer.domain.models.behavior import SignalKind, SignalType
from pipeline_designer.domain.models.component import PortSignalClass

# Maps each class to the set of allowed SignalKind string values.
ALLOWED_KINDS: dict[PortSignalClass, frozenset[str]] = {
    PortSignalClass.CLOCK:   frozenset({SignalKind.STD_LOGIC.value}),
    PortSignalClass.RESET:   frozenset({SignalKind.STD_LOGIC.value}),
    PortSignalClass.CONTROL: frozenset({
        SignalKind.STD_LOGIC.value,
        SignalKind.STD_LOGIC_VECTOR.value,
    }),
    PortSignalClass.DATA: frozenset({
        SignalKind.SFIXED.value,
        SignalKind.UFIXED.value,
    }),
}


def allowed_kinds(signal_class: PortSignalClass) -> frozenset[str]:
    """Return the set of allowed SignalKind values for *signal_class*."""
    return ALLOWED_KINDS[signal_class]


def is_valid_for_class(signal_type: SignalType, signal_class: PortSignalClass) -> bool:
    """Return True when *signal_type.kind* is permitted for *signal_class*."""
    return signal_type.kind in ALLOWED_KINDS[signal_class]


def default_signal_type(
    signal_class: PortSignalClass,
    width: int = 1,
    lsb: int = 0,
) -> SignalType:
    """Return the canonical default SignalType for *signal_class*.

    For DATA the default is ufixed with lsb=0 (integer-valued fixed-point).
    For CONTROL the kind is chosen by *width*: std_logic for 1, std_logic_vector
    for wider.  CLOCK and RESET are always scalar std_logic.
    """
    match signal_class:
        case PortSignalClass.CLOCK | PortSignalClass.RESET:
            return SignalType(kind=SignalKind.STD_LOGIC.value)
        case PortSignalClass.CONTROL:
            if width > 1:
                return SignalType(
                    kind=SignalKind.STD_LOGIC_VECTOR.value,
                    width=str(width),
                    lsb="0",
                )
            return SignalType(kind=SignalKind.STD_LOGIC.value)
        case PortSignalClass.DATA:
            return SignalType(
                kind=SignalKind.UFIXED.value,
                width=str(max(1, width)),
                lsb=str(lsb),
            )
        case _:
            return SignalType(kind=SignalKind.STD_LOGIC.value)


def coerce_signal_type(
    signal_type: SignalType,
    signal_class: PortSignalClass,
) -> SignalType:
    """Return *signal_type* unchanged if it is valid; otherwise return the default.

    The default preserves the width/lsb of the original type where meaningful.
    """
    if is_valid_for_class(signal_type, signal_class):
        return signal_type

    try:
        w = int(signal_type.width)
    except (ValueError, TypeError):
        w = 1
    try:
        l = int(signal_type.lsb)
    except (ValueError, TypeError):
        l = 0
    return default_signal_type(signal_class, width=w, lsb=l)
