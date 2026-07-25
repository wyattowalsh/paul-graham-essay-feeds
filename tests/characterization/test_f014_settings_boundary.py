"""F-014: invalid env Settings must not dump a full traceback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.characterization
def test_invalid_env_min_items_has_no_traceback() -> None:
    env = os.environ.copy()
    env["PG_ESSAY_FEEDS_MIN_ITEMS"] = "0"
    proc = subprocess.run(
        [sys.executable, "-m", "paul_graham_essay_feeds", "check", "--quiet"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0
    assert "Traceback" not in combined
