"""Unit tests for catalog_models (Pydantic schema SSOT)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.catalog_models import (
    ArtifactManifest,
    ArtifactSpec,
    Catalog,
    CatalogEntry,
    FeedEntrySnapshot,
    FeedSnapshot,
    Lifecycle,
)
from paul_graham_essay_feeds.model import FeedError


def test_catalog_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Catalog.model_validate(
            {
                "schema_version": 1,
                "material_config_fingerprint": "x",
                "unexpected": True,
            }
        )


def test_catalog_entry_lifecycle_and_utc() -> None:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
        lifecycle=Lifecycle.ACTIVE,
        first_seen_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert entry.lifecycle is Lifecycle.ACTIVE
    assert entry.first_seen_at is not None
    assert entry.first_seen_at.tzinfo is not None


def test_naive_datetime_rejected() -> None:
    with pytest.raises(FeedError, match="Naive"):
        CatalogEntry(
            stable_id="id",
            url="https://paulgraham.com/a.html",
            title="A",
            position=0,
            lifecycle=Lifecycle.ACTIVE,
            first_seen_at=datetime(2024, 1, 1),
        )


def test_feed_snapshot_requires_items() -> None:
    with pytest.raises(ValidationError):
        FeedSnapshot(
            logical_updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            generator="pg-essay-feeds/0.1.0",
            items=[],
        )


def test_feed_snapshot_roundtrip() -> None:
    snap = FeedSnapshot(
        logical_updated_at=datetime(2024, 6, 1, tzinfo=UTC),
        generator="pg-essay-feeds/0.1.0",
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="A short summary for the feed item.",
                observed_updated_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        ],
    )
    restored = FeedSnapshot.model_validate(snap.model_dump(mode="json"))
    assert restored.items[0].title == "A"


def test_artifact_manifest() -> None:
    man = ArtifactManifest(
        generation_id="gen1",
        schema_version=1,
        logical_updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        catalog_sha256="a" * 64,
        artifacts={
            "rss": ArtifactSpec(path="feeds/rss.xml", sha256="b" * 64, size_bytes=10),
        },
    )
    assert man.artifacts["rss"].size_bytes == 10
