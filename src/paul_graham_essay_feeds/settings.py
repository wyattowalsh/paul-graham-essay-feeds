"""Application settings via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
    max_page_fetches: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Cap page enrich fetches per run (None = all due). Fair cursor persists "
            "across runs via catalog.versions page_fetch_cursor."
        ),
    )
    max_link_validations: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Cap dedicated live link probes per run (None = all non-enrich probes). "
            "Independent of max_page_fetches."
        ),
    )
    host_cooldown_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Minimum seconds between requests to the same host (0 = off).",
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _resolve_root(cls, value: Path | str) -> Path:
        return Path(value).expanduser().resolve()

    @model_validator(mode="after")
    def _validate_public_urls(self) -> Settings:
        """Reject unsafe/malformed public_base_url and source_url at construct."""
        from urllib.parse import urlsplit

        from paul_graham_essay_feeds.models import ConfigurationError

        for field_name, raw in (
            ("source_url", self.source_url),
            ("public_base_url", self.public_base_url),
        ):
            if raw is None:
                continue
            text = raw.strip()
            if not text:
                if field_name == "public_base_url":
                    object.__setattr__(self, "public_base_url", None)
                    continue
                raise ConfigurationError(f"{field_name} must not be blank")
            parts = urlsplit(text)
            if parts.scheme not in {"https", "http"}:
                raise ConfigurationError(
                    f"{field_name} must use https (or http for tests): {raw!r}"
                )
            if parts.username or parts.password:
                raise ConfigurationError(f"{field_name} must not include userinfo: {raw!r}")
            if not parts.netloc or not parts.hostname:
                raise ConfigurationError(f"{field_name} must be absolute: {raw!r}")
            if field_name == "public_base_url" and parts.scheme != "https":
                # Allow http only for localhost/loopback test harnesses.
                host = (parts.hostname or "").lower()
                if host not in {"localhost", "127.0.0.1", "::1"}:
                    raise ConfigurationError(f"public_base_url must be https (got {raw!r})")
        return self
