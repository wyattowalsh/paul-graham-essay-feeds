"""F-011 (src marker) — see also test_f011_py_typed for wheel."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.characterization
def test_py_typed_marker_exists_in_source_tree() -> None:
    assert (ROOT / "src" / "paul_graham_essay_feeds" / "py.typed").is_file()
