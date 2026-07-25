"""Durable catalog and generation contracts (ADR-001/002/005).

Pydantic models are the schema SSOT. No parallel JSON Schema tree is maintained;
optional JSON Schema export can be generated from these models if needed later.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paul_graham_essay_feeds.types import require_aware_utc


class Lifecycle(StrEnum):
    ACTIVE = "active"
    MISSING_CANDIDATE = "missing_candidate"
    TOMBSTONED = "tombstoned"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ResourceState(_StrictModel):
    """HTTP resource cache evidence for index or essay pages."""

    etag: str | None = Field(default=None, description="Last observed ETag validator.")
    last_modified: str | None = Field(
        default=None, description="Last observed Last-Modified validator."
    )
    raw_sha256: str | None = Field(default=None, description="SHA-256 of raw response body.")
    decoded_sha256: str | None = Field(
        default=None, description="SHA-256 of decoded text when HTML was decoded."
    )
    last_checked_at: datetime | None = Field(
        default=None, description="UTC time of the last request/check attempt."
    )
    status_code: int | None = Field(default=None, description="Last HTTP status when known.")
    selected_encoding: str | None = Field(
        default=None, description="Encoding chosen by the HTML decoder when applicable."
    )

    @field_validator("last_checked_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware_utc(value)


class CatalogEntry(_StrictModel):
    """One durable essay (or protected chapter) record."""

    stable_id: str = Field(min_length=1, description="Stable feed id / guid.")
    url: str = Field(min_length=1, description="Normalized absolute allowlisted URL.")
    title: str = Field(min_length=1, description="Display title from discovery or page.")
    position: int = Field(ge=0, description="0-based catalog order (newest first).")
    lifecycle: Lifecycle = Field(description="Catalog lifecycle state.")
    first_seen_at: datetime | None = Field(
        default=None, description="First successful index observation (UTC)."
    )
    last_seen_at: datetime | None = Field(
        default=None, description="Latest successful index observation (UTC)."
    )
    observed_updated_at: datetime | None = Field(
        default=None, description="Latest material metadata change (UTC)."
    )
    published_at: datetime | None = Field(
        default=None, description="Exact trustworthy publication instant only (UTC)."
    )
    published_hint: str | None = Field(
        default=None, description="Month-year or other non-feed date hint."
    )
    summary: str | None = Field(default=None, description="Short source-derived summary.")
    summary_source: str | None = Field(
        default=None, description="Where the summary was derived (meta/og/paragraph)."
    )
    summary_quality: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Quality score in [0, 1]."
    )
    prior_good_summary: str | None = Field(
        default=None, description="Last good summary retained across recoverable failures."
    )
    page: ResourceState = Field(
        default_factory=ResourceState, description="Per-page fetch/cache evidence."
    )

    @field_validator(
        "first_seen_at",
        "last_seen_at",
        "observed_updated_at",
        "published_at",
    )
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware_utc(value)


class Catalog(_StrictModel):
    """Schema-versioned durable catalog (SSOT for generation inputs)."""

    schema_version: int = Field(ge=1, description="Catalog schema version.")
    material_config_fingerprint: str = Field(
        min_length=1, description="Fingerprint of material generator settings."
    )
    versions: dict[str, str] = Field(
        default_factory=dict,
        description="Component versions (generator, decoder, extractor, …).",
    )
    index: ResourceState = Field(
        default_factory=ResourceState, description="Index page resource state."
    )
    entry_order: list[str] = Field(
        default_factory=list, description="stable_id order, newest first."
    )
    entries: dict[str, CatalogEntry] = Field(
        default_factory=dict, description="Map of stable_id → entry."
    )
    last_generation_id: str | None = Field(
        default=None, description="Last successfully published generation id."
    )
    migration_history: list[dict[str, Any]] = Field(
        default_factory=list, description="Idempotent migration records."
    )


class FeedEntrySnapshot(_StrictModel):
    """Normalized item projected into all feed formats."""

    id: str = Field(min_length=1, description="Stable item id.")
    url: str = Field(min_length=1, description="Item URL.")
    title: str = Field(min_length=1, description="Item title.")
    summary: str = Field(min_length=1, description="Short summary / content_text.")
    observed_updated_at: datetime = Field(description="Truthful Atom updated time (UTC).")
    published_at: datetime | None = Field(
        default=None, description="Exact published time when known (UTC)."
    )

    @field_validator("observed_updated_at", "published_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware_utc(value)


class FeedSnapshot(_StrictModel):
    """Single immutable projection used by RSS/Atom/JSON renderers."""

    logical_updated_at: datetime = Field(
        description="Generation logical update time (not wall-clock build)."
    )
    generator: str = Field(min_length=1, description="Generator product string.")
    title: str = Field(default="Paul Graham Essays", description="Feed title.")
    home_page_url: str = Field(
        default="https://paulgraham.com/articles.html",
        description="Feed home / alternate page.",
    )
    feed_url: str | None = Field(default=None, description="Public self/feed URL when hosted.")
    public_base_url: str | None = Field(
        default=None, description="Configured public base URL for self links."
    )
    items: list[FeedEntrySnapshot] = Field(min_length=1, description="Ordered feed items.")

    @field_validator("logical_updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value)


class ArtifactSpec(_StrictModel):
    """One published file inside a generation."""

    path: str = Field(description="Path relative to the generation root.")
    sha256: str = Field(min_length=64, max_length=64, description="Hex SHA-256 digest.")
    size_bytes: int = Field(ge=0, description="File size in bytes.")
    media_type: str | None = Field(default=None, description="MIME type when known.")


class ArtifactManifest(_StrictModel):
    """Deterministic manifest for an immutable generation."""

    generation_id: str = Field(min_length=1, description="Content-addressed generation id.")
    schema_version: int = Field(ge=1, description="Manifest schema version.")
    logical_updated_at: datetime = Field(description="Generation logical update time (UTC).")
    catalog_sha256: str = Field(
        min_length=64, max_length=64, description="SHA-256 of the catalog JSON."
    )
    artifacts: dict[str, ArtifactSpec] = Field(description="Named artifacts (rss, atom, json, …).")

    @field_validator("logical_updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value)
