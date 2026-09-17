"""PGF-AUDIT-010 / RV-S-003: Colab zip members stay under feeds/; uv is exact-pinned."""

from __future__ import annotations

import json
from pathlib import Path

_NOTEBOOK = Path(__file__).resolve().parents[3] / "notebook.ipynb"


def _generate_source() -> str:
    notebook = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    generate = next(
        cell for cell in notebook["cells"] if "generate" in cell.get("metadata", {}).get("tags", [])
    )
    return "".join(generate.get("source", []))


def test_notebook_zip_members_are_feeds_prefixed_and_rooted() -> None:
    source = _generate_source()
    assert 'arcname=f"feeds/{name}"' in source
    assert "arcname=name" not in source
    assert "_must_stay_in_root" in source
    assert "is_relative_to(root)" in source
    assert "path escapes ROOT" in source
    assert '!pip install -q "uv==0.12.15"' in source
    assert "uv>=0.12" not in source
    assert "@v1.0.0" in source
    zip_block = source[source.index("def _must_stay_in_root") : source.index("downloaded")]
    assert "catalog.json" not in zip_block
    assert 'zf.write(src, arcname=f"feeds/{name}")' in zip_block
