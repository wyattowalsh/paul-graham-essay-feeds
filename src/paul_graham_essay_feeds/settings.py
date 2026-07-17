"""Application settings via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paul_graham_essay_feeds.model import MAX_BYTES, MIN_ITEMS, SOURCE_URL


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
        description="Output root for feeds/ and data/ (resolved absolute path).",
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
        default=False,
        description="HEAD/GET each essay URL after generation (slow; intentional).",
    )
    link_timeout: float = Field(
        default=10.0,
        gt=0,
        description="Per-URL timeout seconds for live link probes.",
    )
    link_workers: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Thread pool size for live link probes (not enrich).",
    )
    enrich: bool = Field(
        default=True,
        description="Scrape each essay page for a short summary (not full body).",
    )
    enrich_workers: int = Field(
        default=12,
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
        description=(
            "Bypass hash-based no-op skip when index/pages are unchanged "
            "(always re-enrich and rewrite)."
        ),
    )
    quiet: bool = Field(
        default=False,
        description="Log errors only; suppress progress UI.",
    )
    verbose: bool = Field(
        default=False,
        description="Debug-level logging.",
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _resolve_root(cls, value: Path | str) -> Path:
        return Path(value).expanduser().resolve()
