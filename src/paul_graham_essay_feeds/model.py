"""Pydantic models, constants, and pure URL helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator

from paul_graham_essay_feeds import __version__

SOURCE_URL: Final = "https://paulgraham.com/articles.html"
JSON_FEED_VERSION: Final = "https://jsonfeed.org/version/1.1"
ATOM_NS: Final = "http://www.w3.org/2005/Atom"
DC_NS: Final = "http://purl.org/dc/elements/1.1/"
# Safety floor for extract/check — not the live catalog size (that grows over time).
MIN_ITEMS: Final = 233
MAX_BYTES: Final = 5 * 1024 * 1024
ALLOWED_HOSTS: Final = frozenset({"paulgraham.com", "sep.turbifycdn.com"})
EXCLUDED_PATHS: Final = frozenset({"/", "/index.html", "/articles.html", "/rss.html"})
PROTECTED_PATHS: Final = frozenset({"/ty/cdn/paulgraham/acl1.txt", "/ty/cdn/paulgraham/acl2.txt"})
FEED_ID: Final = "tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds"
FEED_TITLE: Final = "Paul Graham: Essays"
FEED_DESCRIPTION: Final = (
    "Unofficial metadata feeds for Paul Graham's essays, "
    "ordered newest to oldest from the official index."
)
AUTHOR: Final = "Paul Graham"
AUTHOR_URL: Final = "https://paulgraham.com/"
# Single source of truth: ``__version__`` in ``__init__.py``.
GENERATOR: Final = f"pg-essay-feeds/{__version__}"
# Constant epoch for undated Atom ``<updated>`` (not chronological; preserves index order).
STABLE_UNPUBLISHED_UPDATED: Final = datetime(1970, 1, 1, tzinfo=UTC)
FEED_SUMMARY_CHARS: Final = 400
_REPO_URL: Final = "https://github.com/wyattowalsh/paul-graham-essay-feeds"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class FeedError(RuntimeError):
    """Pipeline failure (user-facing message; non-retryable by default)."""


class Essay(BaseModel):
    """One essay (or protected Turbify chapter), newest-first.

    Core identity fields come from the index; optional enrichment fields are
    filled by scraping each essay page (title, meta, short summary — never a
    full body stored for feeds).
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    position: int = Field(
        ge=1,
        description="1-based position in the index (1 = newest).",
    )
    title: str = Field(
        min_length=1,
        description="Display title from the essays index anchor text.",
    )
    url: str = Field(
        description="Absolute https essay URL on an allowlisted host.",
    )
    stable_id: str = Field(
        min_length=1,
        description="Stable guid/id: permalink URL or Turbify path UUID URN.",
    )
    is_permalink: bool = Field(
        description="True when stable_id is the essay URL (paulgraham.com).",
    )
    page_title: str | None = Field(
        default=None,
        description="Optional HTML <title> from the essay page (enrichment).",
    )
    summary: str | None = Field(
        default=None,
        description=(
            "Short description for feeds (≤ FEED_SUMMARY_CHARS). "
            "From meta tags or body head; never a full essay body."
        ),
    )
    content_text: str | None = Field(
        default=None,
        description=(
            "Unused by enrich (always None); kept for feed_summary fallback only. "
            "Feeds never emit full body text."
        ),
    )
    image_url: str | None = Field(
        default=None,
        description="Optional og/twitter image absolute URL (enrich metadata; not in feeds).",
    )
    keywords: str | None = Field(
        default=None,
        description="Optional meta keywords string (enrich metadata; not in feeds).",
    )
    canonical_url: str | None = Field(
        default=None,
        description="Optional canonical link from the essay page.",
    )
    published_hint: str | None = Field(
        default=None,
        description=('Month+year human hint from the page (e.g. "June 2026"); not a feed date.'),
    )
    published_at: datetime | None = Field(
        default=None,
        description=(
            "Full calendar day (UTC) only when a real day source exists; "
            "unset by enrich today (month+year does NOT invent day-1)."
        ),
    )
    content_hash: str | None = Field(
        default=None,
        description="SHA-256 hex of the essay page HTML used for enrichment.",
    )

    @field_validator("url")
    @classmethod
    def _https_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError(f"Essay url must be absolute https: {value!r}")
        host = parts.hostname.lower()
        if host not in ALLOWED_HOSTS:
            raise ValueError(f"Essay host not allowed: {host}")
        return value

    @field_validator("image_url")
    @classmethod
    def _image_url(cls, value: str | None) -> str | None:
        """Keep None; coerce invalid absolute-https allowlisted URLs to None.

        Invalid values become None so poisoned ``image_url`` values do not
        fail model construction. Enrich should set None before constructing
        when the scraped URL fails the same host/scheme policy.
        """
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            return None
        host = parts.hostname.lower()
        if host == "www.paulgraham.com":
            host = "paulgraham.com"
        if host not in ALLOWED_HOSTS:
            return None
        return value

    def feed_summary(self) -> str:
        """Short description only (≤ ``FEED_SUMMARY_CHARS``; never full essay body)."""
        if self.summary:
            return truncate_text(self.summary, FEED_SUMMARY_CHARS)
        if self.content_text:
            return truncate_text(self.content_text, FEED_SUMMARY_CHARS)
        return truncate_text(blurb(self.title), FEED_SUMMARY_CHARS)

    def index_fingerprint(self) -> str:
        """Stable identity line for index-change detection (no enrichment)."""
        return f"{self.position}\t{self.stable_id}\t{self.url}\t{self.title}"


def content_sha256(data: bytes | str) -> str:
    """Return lowercase SHA-256 hex digest of ``data`` (UTF-8 if str)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def user_agent(suffix: str = "") -> str:
    """HTTP User-Agent derived from ``__version__`` (optional suffix, e.g. `` link-check``)."""
    base = f"pg-essay-feeds/{__version__}"
    if not suffix:
        return f"{base} (+{_REPO_URL})"
    return f"{base}{suffix}"


def stable_updated(stable_id: str) -> datetime:
    """Deterministic ``<updated>`` for undated Atom entries (never wall-clock).

    ``stable_id`` is accepted for API clarity; the sentinel is a constant epoch so
    undated entries do not invent relative chronology across rebuilds.
    """
    _ = stable_id
    return STABLE_UNPUBLISHED_UPDATED


def truncate_text(text: str, max_chars: int = FEED_SUMMARY_CHARS) -> str:
    """Truncate at a word boundary; result length is always ``≤ max_chars``.

    When truncating, reserves one character for the ellipsis so verify/check
    length caps (``[1, FEED_SUMMARY_CHARS]``) cannot reject generated feeds.
    """
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"[:max_chars]
    budget = max_chars - 1  # room for trailing "…"
    truncated = text[:budget].rsplit(" ", 1)[0].rstrip()
    if not truncated:
        truncated = text[:budget]
    return truncated + "…"


def utc_now() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(tz=UTC)


def rfc822(value: datetime) -> str:
    """Format ``value`` as RSS-style RFC 822 GMT."""
    return format_datetime(value.astimezone(UTC), usegmt=True)


def rfc3339(value: datetime) -> str:
    """Format ``value`` as Atom/JSON Feed RFC 3339 UTC (no fractional seconds)."""
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value: str) -> str:
    """NFC-normalize, strip controls, collapse whitespace."""
    text = unicodedata.normalize("NFC", value)
    text = _CONTROL.sub("", text)
    return " ".join(text.split())


def canonicalize_url(base: str, href: str) -> str:
    """Resolve ``href`` against ``base``; enforce https + allowlisted host."""
    absolute = urljoin(base, href.strip())
    parts = urlsplit(absolute)
    if parts.scheme != "https" or not parts.hostname:
        raise FeedError(f"Bad URL: {absolute!r}")
    host = parts.hostname.lower()
    if host == "www.paulgraham.com":
        host = "paulgraham.com"
    if host not in ALLOWED_HOSTS:
        raise FeedError(f"Host not allowed: {host}")
    path = parts.path or "/"
    query = "" if host == "sep.turbifycdn.com" else parts.query
    return urlunsplit(("https", host, path, query, ""))


def is_content_candidate(url: str, title: str) -> bool:
    """True when the link looks like an essay (not nav chrome)."""
    if not title:
        return False
    path = urlsplit(url).path or "/"
    host = (urlsplit(url).hostname or "").lower()
    return not (host == "paulgraham.com" and path in EXCLUDED_PATHS)


def make_stable_id(url: str) -> tuple[str, bool]:
    """Return ``(stable_id, is_permalink)`` for feed guid/id."""
    parts = urlsplit(url)
    if parts.hostname == "paulgraham.com":
        return url, True
    stable = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, stable)}", False


def blurb(title: str) -> str:
    """Generic one-line description when no page summary is available."""
    return f"Read “{title}” by Paul Graham."


def validate_essay_link(essay: Essay) -> None:
    """Structural validation of a final included link (always-on)."""
    # Re-run host/scheme rules (defense in depth after extract).
    Essay.model_validate(essay.model_dump())
    parts = urlsplit(essay.url)
    if parts.fragment:
        raise FeedError(f"Fragment not allowed on essay url: {essay.url}")
    if "paulgraham.com/https://" in essay.url:
        raise FeedError(f"Malformed double-prefixed url: {essay.url}")
    TypeAdapter(HttpUrl).validate_python(essay.url)
