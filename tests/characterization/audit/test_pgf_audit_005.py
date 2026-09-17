"""PGF-AUDIT-005: Pages dest policy (W0 overrides zip refuse-empty-dir).

Zip sketch refused any existing dest without ``.pgf-pages-output``. That would
fail the first CI/local assemble after ``mkdir _site`` (empty leftover). W0:

- missing dest → assemble
- empty unmarked directory → assemble (write the marker at success)
- nonempty unmarked dest → FeedError, no rmtree
- dest is a file → FeedError
- dest or any ancestor is a symlink → FeedError
- marked dest → sibling temp assemble then replace
- never repo-root / feeds/; no --force
"""

from __future__ import annotations

from pathlib import Path

_PAGES = Path(__file__).resolve().parents[3] / "src" / "paul_graham_essay_feeds" / "pages.py"


def test_managed_marker_and_no_unmanaged_dest_wipe() -> None:
    text = _PAGES.read_text(encoding="utf-8")
    assert '_MANAGED_MARKER: Final[str] = ".pgf-pages-output"' in text
    assert "shutil.rmtree(dest)" not in text
    assert "dest.unlink()" not in text
    assert "add_argument" in text
    assert "--force" not in text
