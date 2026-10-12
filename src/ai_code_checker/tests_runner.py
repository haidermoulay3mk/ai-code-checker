"""Run the project's own test command and capture the result."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import TestRun

OUTPUT_TAIL_CHARS = 4000


def run_tests(command: str, root: Path, timeout_s: int = 900) -> TestRun:
    try:
        proc = subprocess.run(
            command, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
        return TestRun(command=command, passed=False, exit_code=-1, output_tail=out[-OUTPUT_TAIL_CHARS:], timed_out=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return TestRun(
        command=command, passed=proc.returncode == 0, exit_code=proc.returncode, output_tail=out[-OUTPUT_TAIL_CHARS:]
    )
