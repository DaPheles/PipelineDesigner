"""FIR Pipeline Design-level behavioral simulation.

Loads ``library/designs/fir_pipeline.json`` — a Design graph containing:
  - FIR4Tap instance (combinational, 4 inputs x0-x3, 1 output y)
  - 3 Register instances (reg1, reg2, reg3) forming the delay shift register

Runs it cycle-by-cycle using DesignSimulator and compares against:
  a) the deque-based reference from fir4_example.py
  b) numpy float64 ideal convolution

The design topology is:
  x_in ──┬──────────────────────────── fir.x0
         └─► reg1.d → reg1.q ──┬──── fir.x1
                               └─► reg2.d → reg2.q ──┬── fir.x2
                                                     └─► reg3.d → reg3.q ── fir.x3
  fir.y ─────────────────────────────────────────────────────────────────────── y_out

Run from repo root:
    python examples/fir_pipeline_sim.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fixedpoint import FPFormat  # noqa: E402
from pipeline_designer.domain.models.component import ComponentDefinition  # noqa: E402
from pipeline_designer.domain.models.design import Design  # noqa: E402
from pipeline_designer.domain.simulation import DesignSimulator  # noqa: E402

# ── Load library (just the two primitives this design needs) ─────────────────

PRIM_DIR = REPO_ROOT / "library" / "primitives"

def _load_library() -> dict[str, ComponentDefinition]:
    lib: dict[str, ComponentDefinition] = {}
    for path in PRIM_DIR.glob("*.json"):
        with path.open() as f:
            data = json.load(f)
        try:
            defn = ComponentDefinition.model_validate(data)
            lib[defn.name] = defn
        except Exception:
            pass
    return lib

library = _load_library()
print(f"Library loaded: {sorted(library)}")

# ── Load design ───────────────────────────────────────────────────────────────

DESIGN_PATH = REPO_ROOT / "library" / "designs" / "fir_pipeline.json"
with DESIGN_PATH.open() as f:
    design = Design.model_validate(json.load(f))

print(f"Design loaded: {design.name!r}  "
      f"({len(design.components)} instances, {len(design.connections)} connections)")

# ── Build simulator ───────────────────────────────────────────────────────────

sim = DesignSimulator(design, library)
print()
print(sim.describe())
print()

# ── Formats (must match fir4tap.json port_types) ─────────────────────────────

fmt_in  = FPFormat(int_bits=1, frac_bits=15, signed=True)   # sfixed(0 downto -15)
fmt_out = FPFormat(int_bits=2, frac_bits=15, signed=True)   # sfixed(1 downto -15)
LSB_out = fmt_out.step

def fp_in(v: float):
    return fmt_in.quantize(np.array(v))

def scalar(fp) -> float:
    return float(fp.values)

# ── Reference: numpy ideal FIR ───────────────────────────────────────────────

H = [0.25, 0.25, 0.25, 0.25]

def ideal_fir(samples: list[float]) -> list[float]:
    out = np.convolve(samples, H, mode="full")[:len(samples)]
    return list(out)

# ═════════════════════════════════════════════════════════════════════════════
# Test 1 — Impulse response via Design graph
# ═════════════════════════════════════════════════════════════════════════════

def test_impulse():
    N = 10
    samples = [1.0] + [0.0] * (N - 1)
    ref = ideal_fir(samples)

    sim.reset()
    got: list[float] = []
    for s in samples:
        sim.set_input("x_in", fp_in(s))
        sim.step()
        y = sim.get_output("y_out")
        got.append(scalar(y) if y is not None else 0.0)

    passed = True
    print("Test 1 — Impulse response (Design graph sim):")
    print(f"  {'cycle':>5}  {'input':>7}  {'ref':>10}  {'got':>10}  {'err_LSB':>8}")
    for i, (r, g) in enumerate(zip(ref, got)):
        err = abs(r - g) / LSB_out
        ok  = err <= 1.0
        if not ok:
            passed = False
        print(f"  {i:>5}  {samples[i]:>7.4f}  {r:>10.6f}  {g:>10.6f}  {err:>8.3f}"
              + (" FAIL" if not ok else ""))
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed

# ═════════════════════════════════════════════════════════════════════════════
# Test 2 — DC level
# ═════════════════════════════════════════════════════════════════════════════

def test_dc():
    DC = 0.5
    N  = 8
    samples = [DC] * N
    ref = ideal_fir(samples)

    sim.reset()
    got: list[float] = []
    for s in samples:
        sim.set_input("x_in", fp_in(s))
        sim.step()
        y = sim.get_output("y_out")
        got.append(scalar(y) if y is not None else 0.0)

    steady_err = abs(got[-1] - DC) / LSB_out
    passed = steady_err <= 1.0
    print("Test 2 — DC level (0.5):")
    print(f"  Steady-state output : {got[-1]:.6f}  expected {DC:.6f}  "
          f"err={steady_err:.3f} LSB")
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed

# ═════════════════════════════════════════════════════════════════════════════
# Test 3 — Cross-check Design sim vs fir4_example deque sim (cycle-exact)
# ═════════════════════════════════════════════════════════════════════════════

def test_cross_check():
    """Design graph sim must produce identical results to the manual deque sim."""
    from collections import deque
    from pipeline_designer.domain.simulation import BehaviorExecutor
    from pipeline_designer.domain.models.behavior import ComponentBehavior

    # Re-compile behavior directly (same code path as fir4_example.py)
    fir_defn = library["FIR4Tap"]
    behavior = fir_defn.behavior
    ex = BehaviorExecutor(behavior.code, ["x0", "x1", "x2", "x3"], "fir4tap")

    N = 24
    rng = np.random.default_rng(42)
    samples = list(rng.uniform(-0.8, 0.8, N))

    # Reference: manual deque (mirrors fir4_example.py)
    delay: deque = deque([fp_in(0.0)] * 4, maxlen=4)
    ref_out: list[float] = []
    for s in samples:
        delay.appendleft(fp_in(s))
        x0, x1, x2, x3 = list(delay)
        y = ex(x0, x1, x2, x3)
        ref_out.append(scalar(y))

    # Design sim — note: the design has a 1-cycle latency due to reg1.
    # On cycle 0, x0=current input but x1/x2/x3 are still 0 (undriven).
    # The deque-based reference also starts with zeros.
    # Both should match after accounting for the 1-cycle register stage.
    sim.reset()
    design_out: list[float] = []
    for s in samples:
        sim.set_input("x_in", fp_in(s))
        sim.step()
        y = sim.get_output("y_out")
        design_out.append(scalar(y) if y is not None else 0.0)

    max_err_lsb = max(abs(r - g) / LSB_out for r, g in zip(ref_out, design_out))
    passed = max_err_lsb <= 1.0
    print("Test 3 — Design sim vs deque-based manual sim (24 random samples):")
    print(f"  Max difference: {max_err_lsb:.3f} LSB")
    print(f"  → {'PASS' if passed else 'FAIL'}\n")
    return passed

# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = [test_impulse(), test_dc(), test_cross_check()]
    n_pass  = sum(results)
    n_total = len(results)
    print("=" * 55)
    print(f"Results: {n_pass}/{n_total} tests passed")
    if n_pass < n_total:
        sys.exit(1)
    else:
        print("MILESTONE: Design-level graph simulation working.")
