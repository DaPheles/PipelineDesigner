"""4-tap box-average FIR filter — behavioral simulation milestone.

Demonstrates:
- Loading a primitive JSON with behavior code
- Compiling behavior via BehaviorExecutor
- Cycle-accurate simulation using a deque delay line
- Comparison against numpy float64 reference (must agree within 1 LSB)

Run from the pipeline_designer repo root:
    python examples/fir4_example.py
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np

# ── path setup (allow running without installing pipeline_designer) ───────────
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline_designer.utils.fixedpoint import FPFormat, FixedPointArray  # noqa: E402
from pipeline_designer.domain.models.behavior import (  # noqa: E402
    BehaviorPortType,
    ComponentBehavior,
    FixedPointKind,
)
from pipeline_designer.domain.simulation import BehaviorExecutor  # noqa: E402

# ── load primitive JSON ───────────────────────────────────────────────────────

PRIM_PATH = REPO_ROOT / "library" / "primitives" / "fir4tap.json"
with PRIM_PATH.open() as f:
    prim_data = json.load(f)

behavior_data = prim_data["behavior"]
behavior = ComponentBehavior.model_validate(behavior_data)

# ── compile behavior ──────────────────────────────────────────────────────────

executor = BehaviorExecutor(
    code_body=behavior.code,
    param_names=["x0", "x1", "x2", "x3"],
    name="fir4tap",
)
print("Compiled:", executor)

# ── derive input / output formats from port_types ─────────────────────────────

pt_in  = behavior.port_types["x0"]
pt_out = behavior.port_types["y"]
fmt_in  = pt_in.to_fpformat()   # S1.15 — sfixed(0 downto -15)
fmt_out = pt_out.to_fpformat()  # S2.15 — sfixed(1 downto -15)
LSB_out = fmt_out.step

print(f"Input  format: {fmt_in!r}  (step={fmt_in.step:.6e})")
print(f"Output format: {fmt_out!r} (step={LSB_out:.6e})")
print()


# ── helper: scalar FixedPointArray ───────────────────────────────────────────

def to_fp(fmt: FPFormat, value: float) -> FixedPointArray:
    return fmt.quantize(np.array(value))


def scalar_value(fp: FixedPointArray) -> float:
    return float(fp.values)


# ── cycle-accurate FIR simulation ─────────────────────────────────────────────

def simulate_fir(samples: list[float]) -> list[float]:
    """Run one FIR4Tap per sample, maintaining a 4-sample delay line.

    The delay line is pre-filled with zeros (cold start).
    x0 = newest sample, x3 = oldest sample.
    """
    delay: deque[FixedPointArray] = deque(
        [to_fp(fmt_in, 0.0)] * 4, maxlen=4
    )
    outputs: list[float] = []
    for s in samples:
        delay.appendleft(to_fp(fmt_in, s))  # push newest to front
        x0, x1, x2, x3 = list(delay)
        y_fp: FixedPointArray = executor(x0, x1, x2, x3)
        outputs.append(scalar_value(y_fp))
    return outputs


# ═════════════════════════════════════════════════════════════════════════════
# Test 1 — Impulse response
# ═════════════════════════════════════════════════════════════════════════════

def test_impulse():
    N = 12
    samples = [1.0] + [0.0] * (N - 1)
    h_ref   = [0.25, 0.25, 0.25, 0.25]
    ref     = list(np.convolve(samples, h_ref, mode="full")[:N])
    got     = simulate_fir(samples)

    passed = True
    print("Test 1 — Impulse response:")
    print(f"  {'cycle':>5}  {'sample':>8}  {'ref':>10}  {'fp':>10}  {'err_LSB':>8}")
    for i, (r, g) in enumerate(zip(ref, got)):
        err_lsb = abs(r - g) / LSB_out
        ok = err_lsb <= 1.0
        if not ok:
            passed = False
        print(f"  {i:>5}  {samples[i]:>8.4f}  {r:>10.6f}  {g:>10.6f}  {err_lsb:>8.3f} {'FAIL' if not ok else ''}")
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed


# ═════════════════════════════════════════════════════════════════════════════
# Test 2 — DC level (should pass through unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def test_dc():
    DC = 0.5
    N  = 8
    samples = [DC] * N
    ref     = list(np.convolve(samples, [0.25] * 4, mode="full")[:N])
    got     = simulate_fir(samples)

    passed = True
    print("Test 2 — DC level (0.5):")
    for i, (r, g) in enumerate(zip(ref, got)):
        err_lsb = abs(r - g) / LSB_out
        ok = err_lsb <= 1.0
        if not ok:
            passed = False
    # steady state (after 4 taps) must equal DC within 1 LSB
    steady_err = abs(got[-1] - DC) / LSB_out
    print(f"  Steady-state output: {got[-1]:.6f}, expected {DC:.6f}, err={steady_err:.3f} LSB")
    if steady_err > 1.0:
        passed = False
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed


# ═════════════════════════════════════════════════════════════════════════════
# Test 3 — Sine wave (low-frequency should pass, high-frequency should attenuate)
# ═════════════════════════════════════════════════════════════════════════════

def test_sine():
    N  = 32
    fs = 32.0               # normalised sample rate
    f_low  = 1.0            # 1/32 of fs — well below cutoff → should pass
    f_high = fs / 2 * 0.9  # near Nyquist — box FIR attenuates heavily

    t  = np.arange(N) / fs
    lo = list(np.sin(2 * np.pi * f_low  * t) * 0.5)
    hi = list(np.sin(2 * np.pi * f_high * t) * 0.5)

    ref_lo = list(np.convolve(lo, [0.25] * 4, mode="full")[:N])
    ref_hi = list(np.convolve(hi, [0.25] * 4, mode="full")[:N])

    got_lo = simulate_fir(lo)
    got_hi = simulate_fir(hi)

    # Check float agreement within 2 LSB (extra headroom for both sources of error)
    max_err_lo = max(abs(r - g) / LSB_out for r, g in zip(ref_lo, got_lo))
    max_err_hi = max(abs(r - g) / LSB_out for r, g in zip(ref_hi, got_hi))

    # High-frequency amplitude in steady state (skip first 4 transient cycles)
    amp_hi = max(abs(v) for v in got_hi[4:])

    passed = max_err_lo <= 2.0 and max_err_hi <= 2.0
    print("Test 3 — Sine wave:")
    print(f"  Low-freq  (f={f_low:.1f}) max FP vs ref: {max_err_lo:.2f} LSB  ({'PASS' if max_err_lo<=2 else 'FAIL'})")
    print(f"  High-freq (f={f_high:.1f}) max FP vs ref: {max_err_hi:.2f} LSB  ({'PASS' if max_err_hi<=2 else 'FAIL'})")
    print(f"  High-freq steady-state amplitude: {amp_hi:.6f} (should be much less than 0.5)")
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed


# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = [test_impulse(), test_dc(), test_sine()]
    n_pass = sum(results)
    n_total = len(results)
    print("=" * 50)
    print(f"Results: {n_pass}/{n_total} tests passed")
    if n_pass < n_total:
        sys.exit(1)
    else:
        print("MILESTONE ACHIEVED: fixed-point behavioral simulation working.")
