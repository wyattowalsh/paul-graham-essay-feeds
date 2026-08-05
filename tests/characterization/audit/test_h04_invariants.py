"""H-04: catalog relational invariants fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.models import Catalog, CatalogEntry


def test_h04_orphan_entry_rejected() -> None:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
    )
    with pytest.raises(ValidationError, match="missing from entry_order"):
        Catalog(
            schema_version=2,
            material_config_fingerprint="fp",
            entry_order=[],
            entries={entry.stable_id: entry},
        )


def test_h04_key_mismatch_rejected() -> None:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
    )
    with pytest.raises(ValidationError, match="does not match"):
        Catalog(
            schema_version=2,
            material_config_fingerprint="fp",
            entry_order=["https://paulgraham.com/other.html"],
            entries={"https://paulgraham.com/other.html": entry},
        )
