"""Smoke: subprocess entrypoints."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.html_samples import synthetic_index_html

pytestmark = pytest.mark.smoke


def test_module_main_smoke(tmp_path: Path) -> None:
    html = tmp_path / "articles.html"
    html.write_text(synthetic_index_html(), encoding="utf-8")
    root = tmp_path / "out"
    root.mkdir()
    update = subprocess.run(
        [
            sys.executable,
            "-m",
            "paul_graham_essay_feeds",
            "update",
            "--repo-root",
            str(root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(html),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert update.returncode == 0, update.stderr + update.stdout
    check = subprocess.run(
        [
            sys.executable,
            "-m",
            "paul_graham_essay_feeds",
            "check",
            "--repo-root",
            str(root),
            "--quiet",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr + check.stdout
