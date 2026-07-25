"""F-011: package claims typed but must ship py.typed (PEP 561)."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.characterization
def test_built_wheel_contains_py_typed(tmp_path: Path) -> None:
    """Contract: wheel contents include paul_graham_essay_feeds/py.typed."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    out = tmp_path / "dist"
    out.mkdir()
    proc = subprocess.run(
        [uv, "build", "--wheel", "--no-sources", "-o", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    wheels = sorted(out.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    with zipfile.ZipFile(wheels[-1]) as zf:
        names = set(zf.namelist())
    assert "paul_graham_essay_feeds/py.typed" in names
