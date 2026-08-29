"""Application settings via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paul_graham_essay_feeds.models import MAX_BYTES, MIN_ITEMS, SOURCE_URL, ConfigurationError

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_absolute_http_url(field_name: str, raw: str) -> SplitResult:
    """Parse an absolute http(s) URL; reject userinfo and empty hosts."""
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"https", "http"}:
        raise ConfigurationError(f"{field_name} must use https (or http for tests): {raw!r}")
    if parts.username or parts.password:
        raise ConfigurationError(f"{field_name} must not include userinfo: {raw!r}")
    try:
        host = parts.hostname
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} must be absolute: {raw!r}") from exc
    if not parts.netloc or not host:
        raise ConfigurationError(f"{field_name} must be absolute: {raw!r}")
    return parts


def _ascii_netloc(parts: SplitResult, *, field_name: str, raw: str) -> str:
    """IDNA-encode hostname; keep an explicit port; bracket IPv6."""
    try:
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} must be absolute: {raw!r}") from exc
    if not host:
        raise ConfigurationError(f"{field_name} must be absolute: {raw!r}")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConfigurationError(
            f"{field_name} host is not a valid IDNA hostname: {raw!r}"
        ) from exc
    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return netloc


def _last_segment_looks_like_file(path: str) -> bool:
    """True when the last path segment looks like ``name.ext`` (ambiguous as a base)."""
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    if not segment or "." not in segment or segment.startswith("."):
        return False
    ext = segment.rsplit(".", 1)[-1]
    return bool(ext) and ext.isascii() and ext.isalnum() and ext[:1].isalpha()


def _normalize_public_base_url(raw: str) -> str:
    """Validate and canonicalize ``public_base_url`` as an IDNA directory URL."""
    text = raw.strip()
    if "?" in text:
        raise ConfigurationError(f"public_base_url must not include a query string: {raw!r}")
    if "#" in text:
        raise ConfigurationError(f"public_base_url must not include a fragment: {raw!r}")
    parts = _require_absolute_http_url("public_base_url", text)
    if parts.query:
        raise ConfigurationError(f"public_base_url must not include a query string: {raw!r}")
    if parts.fragment:
        raise ConfigurationError(f"public_base_url must not include a fragment: {raw!r}")
    if parts.scheme != "https":
        host = (parts.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise ConfigurationError(f"public_base_url must be https (got {raw!r})")
    if _last_segment_looks_like_file(parts.path or ""):
        raise ConfigurationError(
            f"public_base_url must be a directory base URL, not a file: {raw!r}"
        )
    netloc = _ascii_netloc(parts, field_name="public_base_url", raw=raw)
    directory_path = (parts.path or "").rstrip("/") + "/"
    return urlunsplit((parts.scheme, netloc, directory_path, "", ""))


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
        description=(
            "Optional public base URL for feed self links / feed_url "
            "(https directory URL; no query, fragment, or userinfo)."
        ),
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
        default=0.05,
        ge=0.0,
        description="Minimum seconds between requests to the same host.",
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _resolve_root(cls, value: Path | str) -> Path:
        return Path(value).expanduser().resolve()

    @model_validator(mode="after")
    def _validate_public_urls(self) -> Settings:
        """Reject unsafe/malformed public_base_url and source_url at construct."""
        source = self.source_url.strip()
        if not source:
            raise ConfigurationError("source_url must not be blank")
        _require_absolute_http_url("source_url", source)

        raw = self.public_base_url
        if raw is None:
            return self
        text = raw.strip()
        if not text:
            object.__setattr__(self, "public_base_url", None)
            return self
        object.__setattr__(self, "public_base_url", _normalize_public_base_url(text))
        return self
