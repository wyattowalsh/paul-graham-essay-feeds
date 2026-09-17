"""PGF-AUDIT-001: tag-create preflight on fixtures (never --live)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / ".github" / "scripts" / "tag_create_preflight.py"
_FIXTURES = _REPO / "tests" / "fixtures" / "audit"
_BLOCKED = _FIXTURES / "ruleset_vstar_blocked.json"
_CREATE_OK = _FIXTURES / "ruleset_vstar_create_ok.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO,
    )


def test_blocked_fixture_exits_2() -> None:
    proc = _run("--ruleset-json", str(_BLOCKED))
    assert proc.returncode == 2
    assert "creation rule present" in proc.stderr


def test_create_ok_fixture_exits_0() -> None:
    proc = _run("--ruleset-json", str(_CREATE_OK))
    assert proc.returncode == 0
    assert "creation rule absent" in proc.stdout
