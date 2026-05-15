"""GHDL subprocess wrapper for syntax checking.

Writes VHDL source to a temporary file, runs ``ghdl -a`` (analysis), and
parses the error output back into structured ``GhdlError`` objects.

GHDL error line format::

    /tmp/tmpXXXXXX.vhd:42:5: error: ...
    /tmp/tmpXXXXXX.vhd:17:1: warning[...]: ...
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


@dataclass
class GhdlError:
    """A single diagnostic from GHDL analysis."""
    line:     int   # 1-based line number (0 = not file-specific)
    col:      int   # 1-based column number
    message:  str
    severity: str   # "error" | "warning" | "note"


class GhdlRunner:
    """Run GHDL analysis on a VHDL source string."""

    @staticmethod
    def is_available() -> bool:
        """Return True if ``ghdl`` is found on PATH."""
        return shutil.which("ghdl") is not None

    def check(self, vhdl_source: str, std: str = "08") -> list[GhdlError]:
        """Analyse *vhdl_source* and return a (possibly empty) list of errors.

        If GHDL is not installed a single synthetic error is returned
        explaining the situation rather than raising an exception.
        """
        if not self.is_available():
            return [GhdlError(
                line=0, col=0, severity="error",
                message="GHDL not found — install it and make sure it is on PATH",
            )]

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".vhd")
        try:
            with os.fdopen(tmp_fd, "w") as fh:
                fh.write(vhdl_source)

            result = subprocess.run(
                ["ghdl", "-a", f"--std={std}", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            return self._parse(result.stderr, tmp_path)

        except subprocess.TimeoutExpired:
            return [GhdlError(line=0, col=0, severity="error", message="GHDL timed out (30 s)")]
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Private ───────────────────────────────────────────────────────────────

    _LINE_RE = re.compile(
        r".+:(\d+):(\d+):\s+(error|warning(?:\[[^\]]*\])?|note):\s+(.*)"
    )

    def _parse(self, stderr: str, tmp_path: str) -> list[GhdlError]:
        errors: list[GhdlError] = []
        escaped = re.escape(tmp_path)
        line_re = re.compile(
            rf"{escaped}:(\d+):(\d+):\s+(error|warning(?:\[[^\]]*\])?|note):\s+(.*)"
        )
        for line in stderr.splitlines():
            m = line_re.match(line)
            if m:
                lineno, col, sev_raw, msg = m.groups()
                severity = "error" if sev_raw == "error" else (
                    "note" if sev_raw == "note" else "warning"
                )
                errors.append(GhdlError(
                    line=int(lineno), col=int(col),
                    severity=severity, message=msg.strip(),
                ))
        return errors
