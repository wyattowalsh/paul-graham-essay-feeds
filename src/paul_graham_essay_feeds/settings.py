"""Application settings via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paul_graham_essay_feeds.models import MAX_BYTES, MIN_ITEMS, SOURCE_URL


class Settings(BaseSettings):
    """Env-overridable defaults (prefix ``PG_ESSAY_FEEDS_``)."""

    model_config = SettingsConfigDict(
        env_prefix="PG_ESSAY_FEEDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    source_url: str = Field(
        default=SOURCE_URL,
        description="Official essays index URL (https, paulgraham.com).",
    )
    repo_root: Path = Field(
        default_factory=Path.cwd,
        description="Output root for feeds/ (resolved absolute path).",
    )
    min_items: int = Field(
        default=MIN_ITEMS,
        ge=1,
        description="Safety floor: fail extract if fewer essays than this.",
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        description="HTTP timeout seconds for the index fetch.",
    )
    retries: int = Field(
        default=3,
        ge=0,
        description="Extra Tenacity retries after the first attempt (attempts = retries+1).",
    )
    max_bytes: int = Field(
        default=MAX_BYTES,
        ge=1024,
        description="Max response/source body size in bytes (index, pages, local file).",
    )
    validate_links: bool = Field(
        default=True,
        description=(
            "Live HEAD/GET each essay URL during update (default on). "
            "Failures are reported only — essays are never dropped. "
            "Opt out with --no-validate-links or PG_ESSAY_FEEDS_VALIDATE_LINKS=false."
        ),
    )
    link_timeout: float = Field(
        default=10.0,
        gt=0,
        description="Per-URL timeout seconds for live link probes.",
    )
    link_workers: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Thread pool size for live link probes (not enrich).",
    )
    enrich: bool = Field(
        default=True,
        description="Scrape each essay page for a short summary (not full body).",
    )
    enrich_workers: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Thread pool size for per-page enrichment GETs.",
    )
    enrich_timeout: float = Field(
        default=15.0,
        gt=0,
        description="Per-page timeout seconds during enrichment.",
    )
    force: bool = Field(
        default=False,
        description="Bypass refresh planner no-op when nothing is due",
    )
    quiet: bool = Field(
        default=False,
        description="Log errors only; suppress progress UI.",
    )
    verbose: bool = Field(
        default=False,
        description="Debug-level logging.",
    )
    public_base_url: str | None = Field(
        default=None,
        description="Optional public base URL for feed self links / feed_url (https).",
    )
    stale_after_days: int = Field(
        default=30,
        ge=1,
        description="Refresh planner: re-fetch page metadata after this many days.",
    )
    allow_discovery_fallback: bool = Field(
        default=True,
        description="Allow discovery fallback extraction when markers are sparse.",
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _resolve_root(cls, value: Path | str) -> Path:
        return Path(value).expanduser().resolve()
