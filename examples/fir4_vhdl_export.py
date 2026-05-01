"""FIR4Tap VHDL export + GHDL co-simulation.

This script:
  1. Loads the FIR4Tap primitive from JSON
  2. Runs the Python behavioral simulation (same as fir4_example.py)
  3. Calls VhdlGenerator to write entity + testbench VHDL files
  4. Optionally compiles and runs them with GHDL if it is available on PATH

Run from the pipeline_designer repo root:
    python examples/fir4_vhdl_export.py

To skip GHDL (write VHDL only):
    python examples/fir4_vhdl_export.py --no-ghdl
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fixedpoint import FPFormat, FixedPointArray  # noqa: E402
from pipeline_designer.domain.models.behavior import ComponentBehavior  # noqa: E402
from pipeline_designer.domain.models.component import ComponentDefinition  # noqa: E402
from pipeline_designer.domain.simulation import (  # noqa: E402
    BehaviorExecutor,
    StimulusCase,
    VhdlGenerator,
)

# ── Load primitive ────────────────────────────────────────────────────────────

PRIM_PATH = REPO_ROOT / "library" / "primitives" / "fir4tap.json"
with PRIM_PATH.open() as f:
    prim_data = json.load(f)

component = ComponentDefinition.model_validate(prim_data)
behavior  = component.behavior
ptypes    = behavior.port_types

# ── Compile behavior + derive formats ────────────────────────────────────────

executor = BehaviorExecutor(
    code_body=behavior.code,
    param_names=["x0", "x1", "x2", "x3"],
    name="fir4tap",
)

fmt_in  = ptypes["x0"].to_fpformat()   # S1.15
fmt_out = ptypes["y"].to_fpformat()    # S2.15


def to_fp(fmt: FPFormat, v: float) -> FixedPointArray:
    return fmt.quantize(np.array(v))


def scalar(fp: FixedPointArray) -> float:
    return float(fp.values)


# ── Python simulation → StimulusCase list ────────────────────────────────────

def build_cases() -> list[StimulusCase]:
    """Run the FIR cycle-by-cycle and collect stimulus + golden output."""
    delay: deque[FixedPointArray] = deque([to_fp(fmt_in, 0.0)] * 4, maxlen=4)
    cases: list[StimulusCase] = []

    sample_sequence = [
        ("impulse",  [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ("dc_half",  [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]),
    ]

    for seq_name, samples in sample_sequence:
        delay = deque([to_fp(fmt_in, 0.0)] * 4, maxlen=4)
        for cycle, s in enumerate(samples):
            delay.appendleft(to_fp(fmt_in, s))
            x0, x1, x2, x3 = list(delay)
            y_fp: FixedPointArray = executor(x0, x1, x2, x3)
            cases.append(StimulusCase(
                label=f"{seq_name}_c{cycle}",
                inputs={
                    "x0": scalar(x0),
                    "x1": scalar(x1),
                    "x2": scalar(x2),
                    "x3": scalar(x3),
                },
                outputs={"y": scalar(y_fp)},
            ))

    return cases


# ── Write VHDL ────────────────────────────────────────────────────────────────

def write_vhdl(output_dir: Path, cases: list[StimulusCase]) -> dict[str, Path]:
    gen   = VhdlGenerator(component, pkg_library="work")
    paths = gen.write_all(output_dir, cases, tolerance_lsb=1)
    print(f"Entity + architecture : {paths['entity']}")
    print(f"Testbench             : {paths['testbench']}")
    return paths


# ── GHDL co-simulation ────────────────────────────────────────────────────────

PKG_VHDL = (
    REPO_ROOT
    / "fixed_point_evaluation"
    / "vhdl"
    / "src"
    / "fixed_point_pkg.vhd"
)

GHDL_STD = "--std=08"

# Well-known installation paths tried in order when ghdl is not on PATH.
# Add new entries here as needed.
_GHDL_FALLBACK_PATHS = [
    Path("/opt/ghdl22/bin/ghdl"),
    Path("/opt/ghdl/bin/ghdl"),
    Path.home() / ".local/ghdl/bin/ghdl",
]


def _find_ghdl() -> str | None:
    """Return path to ghdl executable, or None if not found."""
    on_path = shutil.which("ghdl")
    if on_path:
        return on_path
    for candidate in _GHDL_FALLBACK_PATHS:
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return str(candidate)
    return None


def run_ghdl(output_dir: Path, paths: dict[str, Path]) -> bool:
    """Analyse, elaborate, and run the testbench with GHDL.

    Returns True on success (SIMPASS reported), False otherwise.
    """
    ghdl_bin = _find_ghdl()
    if ghdl_bin is None:
        print("GHDL not found.")
        return False

    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    def ghdl(*args: str) -> subprocess.CompletedProcess:
        cmd = [ghdl_bin, *args]
        print(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, cwd=work_dir)

    # 1. Analyse
    for src in [PKG_VHDL, paths["entity"], paths["testbench"]]:
        r = ghdl("-a", GHDL_STD, str(src))
        if r.returncode != 0:
            print("GHDL analyse failed:")
            print(r.stderr)
            return False

    # 2. Elaborate testbench
    tb_name = f"tb_{component.name.lower()}"
    r = ghdl("-e", GHDL_STD, tb_name)
    if r.returncode != 0:
        print("GHDL elaborate failed:")
        print(r.stderr)
        return False

    # 3. Run
    r = ghdl("-r", GHDL_STD, tb_name)
    stdout = r.stdout + r.stderr
    print(stdout)

    if "SIMPASS" in stdout:
        print("GHDL co-simulation: PASS — Python and VHDL agree.")
        return True
    elif "SIMFAIL" in stdout or r.returncode != 0:
        print("GHDL co-simulation: FAIL")
        return False
    else:
        print("GHDL co-simulation: unexpected output (no SIMPASS/SIMFAIL)")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="FIR4Tap VHDL export + GHDL co-sim")
    parser.add_argument(
        "--no-ghdl", action="store_true",
        help="Write VHDL files only; do not run GHDL",
    )
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / "build" / "vhdl" / "fir4tap"),
        help="Directory to write generated VHDL files",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("=== FIR4Tap: Python behavioral simulation ===")
    cases = build_cases()
    print(f"Generated {len(cases)} stimulus cases from Python simulation.")
    for c in cases[:4]:
        print(f"  {c.label}: y = {c.outputs['y']:.6f}")
    print()

    print("=== VHDL export ===")
    paths = write_vhdl(output_dir, cases)
    print()

    if args.no_ghdl:
        print("Skipping GHDL (--no-ghdl).")
        return 0

    if _find_ghdl() is None:
        print("GHDL not found on PATH or in known locations — skipping co-simulation.")
        print("Options:")
        print("  sudo cp -r /tmp/ghdl22 /opt/ghdl22  (then add /opt/ghdl22/bin to PATH)")
        print("  mkdir -p ~/.local/ghdl && cp -r /tmp/ghdl22/. ~/.local/ghdl/")
        return 0

    print("=== GHDL co-simulation ===")
    ok = run_ghdl(output_dir, paths)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
