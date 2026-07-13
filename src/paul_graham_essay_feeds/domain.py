"""Canonical domain models and pure helpers.

All models are immutable. Timestamps are **feed-observation metadata**, never
fabricated original publication dates.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

__all__ = [
    "ALLOWED_HOSTS",
    "ATOM_NS",
    "CHANNEL_URL",
    "DC_NS",
    "ESSAYS_SCHEMA_VERSION",
    "EXCLUDED_INTERNAL_PATHS",
    "JSON_FEED_VERSION",
    "MIN_BASELINE_ITEMS",
    "PROTECTED_EXTERNAL_PATHS",
    "SOURCE_URL",
    "STATE_SCHEMA_VERSION",
    "BuildContext",
    "ChangeSet",
    "EssayItem",
    "ExtractionResult",
    "FeedError",
    "FetchResult",
    "PublicUrls",
    "ValidationReport",
    "canonicalize_public_url",
    "canonicalize_url",
    "content_text_for",
    "description_for",
    "dt_to_iso",
    "is_content_candidate",
    "logical_signature",
    "logical_signature_sha256",
    "make_stable_id",
    "normalize_text",
    "parse_iso_dt",
    "rfc822_utc",
    "rfc3339_utc",
    "sha256_bytes",
    "utc_now",
]

SOURCE_URL: Final = "https://paulgraham.com/articles.html"
CHANNEL_URL: Final = SOURCE_URL
RSS_SPEC_URL: Final = "https://www.rssboard.org/rss-specification"
JSON_FEED_VERSION: Final = "https://jsonfeed.org/version/1.1"
ATOM_NS: Final = "http://www.w3.org/2005/Atom"
DC_NS: Final = "http://purl.org/dc/elements/1.1/"
MIN_BASELINE_ITEMS: Final = 233
MAX_SOURCE_BYTES: Final = 5 * 1024 * 1024
ALLOWED_HOSTS: Final = frozenset({"paulgraham.com", "sep.turbifycdn.com"})
EXCLUDED_INTERNAL_PATHS: Final = frozenset({"/", "/index.html", "/articles.html", "/rss.html"})
PROTECTED_EXTERNAL_PATHS: Final = frozenset(
    {
        "/ty/cdn/paulgraham/acl1.txt",
        "/ty/cdn/paulgraham/acl2.txt",
    }
)
RETRYABLE_HTTP_CODES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
ESSAYS_SCHEMA_VERSION: Final = 1
STATE_SCHEMA_VERSION: Final = 1
DEFAULT_FEED_ID: Final = "tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds"
DEFAULT_PUBLIC_BASE_URL: Final = "https://paul-graham-essay-feeds.vercel.app/"
DEFAULT_CATEGORY: Final = "Essays"
BASELINE_IMPORT_AT: Final = "2026-07-11T07:24:19+00:00"


class FeedError(RuntimeError):
    """Raised when fetch, extraction, reconciliation, validation, or I/O fails."""


@dataclass(frozen=True, slots=True)
class EssayItem:
    """One canonical essay (or protected external chapter) in newest-to-oldest order.

    Attributes
    ----------
    position :
        One-based contiguous index in the canonical sequence (1 = newest).
    title :
        Unicode NFC, whitespace-normalized, XML-safe display title.
    url :
        Absolute HTTPS canonical URL (may retain Turbify cache-bust query).
    stable_id :
        Immutable cross-format identity (HTTPS URL or UUIDv5 URN).
    is_permalink :
        Whether ``stable_id`` is a dereferenceable permalink (RSS guid flag).
    first_seen_at :
        UTC observation time when this item first entered the catalog.
    last_changed_at :
        UTC observation time of the last material metadata change.
    """

    position: int
    title: str
    url: str
    stable_id: str
    is_permalink: bool
    first_seen_at: datetime
    last_changed_at: datetime

    @property
    def identity(self) -> str:
        """Stable identity used for reconciliation (alias of ``stable_id``)."""
        return self.stable_id

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-ready dict with ISO-8601 timestamps."""
        return {
            "position": self.position,
            "title": self.title,
            "url": self.url,
            "stable_id": self.stable_id,
            "is_permalink": self.is_permalink,
            "first_seen_at": dt_to_iso(self.first_seen_at),
            "last_changed_at": dt_to_iso(self.last_changed_at),
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any], *, position: int | None = None) -> EssayItem:
        """Deserialize one item from essays.json / baseline import row."""
        title = normalize_text(str(row.get("title", "")))
        url = str(row.get("url", "")).strip()
        stable_id = str(row.get("stable_id") or row.get("guid") or "").strip()
        if not stable_id:
            stable_id, is_permalink = make_stable_id(url)
        else:
            is_permalink_raw = row.get("is_permalink", row.get("guid_is_permalink"))
            if is_permalink_raw is None:
                is_permalink = urlsplit(url).hostname == "paulgraham.com"
            else:
                is_permalink = bool(is_permalink_raw)
        pos = int(row["position"]) if "position" in row else (position or 0)
        first = parse_iso_dt(str(row.get("first_seen_at") or BASELINE_IMPORT_AT))
        last = parse_iso_dt(
            str(row.get("last_changed_at") or row.get("first_seen_at") or BASELINE_IMPORT_AT)
        )
        return cls(
            position=pos,
            title=title,
            url=url,
            stable_id=stable_id,
            is_permalink=is_permalink,
            first_seen_at=first,
            last_changed_at=last,
        )


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Outcome of HTML extraction from the official essays index.

    Attributes
    ----------
    items :
        Normalized essay items in source order.
    mode :
        ``essay-row-marker`` or ``filtered-anchor-fallback``.
    anchor_count :
        Total anchors seen by the parser.
    marked_anchor_count :
        Anchors preceded by the site essay-row marker image.
    duplicate_count :
        Duplicates removed during fallback deduplication.
    """

    items: tuple[EssayItem, ...]
    mode: str
    anchor_count: int
    marked_anchor_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    """HTTP fetch outcome for the source index page.

    Attributes
    ----------
    body :
        Response body bytes, or ``None`` on HTTP 304.
    final_url :
        Final URL after redirects (or request URL on 304).
    etag :
        Response or prior ETag for conditional requests.
    last_modified :
        Response or prior Last-Modified header value.
    status :
        HTTP status code.
    not_modified :
        True when the server returned 304 Not Modified.
    """

    body: bytes | None
    final_url: str
    etag: str | None
    last_modified: str | None
    status: int
    not_modified: bool


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Reconciliation diff between previous and current item sequences.

    Attributes
    ----------
    added :
        Stable IDs present only in the new sequence.
    removed :
        Stable IDs present only in the previous sequence.
    title_changed :
        Stable IDs whose titles differ.
    url_changed :
        Stable IDs whose canonical URLs differ.
    order_changed :
        True when retained items reordered (always rejected by default).
    """

    added: tuple[str, ...]
    removed: tuple[str, ...]
    title_changed: tuple[str, ...]
    url_changed: tuple[str, ...]
    order_changed: bool

    @property
    def changed(self) -> bool:
        """True when any structural or material metadata change occurred."""
        return bool(
            self.added
            or self.removed
            or self.title_changed
            or self.url_changed
            or self.order_changed
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for validation reports."""
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "title_changed": list(self.title_changed),
            "url_changed": list(self.url_changed),
            "order_changed": self.order_changed,
        }


@dataclass(frozen=True, slots=True)
class PublicUrls:
    """Derived absolute public feed URLs from a configured base.

    Attributes
    ----------
    base :
        Normalized public site base ending with ``/``.
    rss :
        Absolute URL of ``feeds/rss.xml``.
    atom :
        Absolute URL of ``feeds/atom.xml``.
    json_feed :
        Absolute URL of ``feeds/feed.json``.
    opml :
        Absolute URL of ``feeds/subscriptions.opml``.
    """

    base: str
    rss: str
    atom: str
    json_feed: str
    opml: str

    @classmethod
    def from_base(cls, base_url: str) -> PublicUrls:
        """Build public feed URLs from a site base URL."""
        base = base_url.rstrip("/") + "/"
        return cls(
            base=base,
            rss=base + "feeds/rss.xml",
            atom=base + "feeds/atom.xml",
            json_feed=base + "feeds/feed.json",
            opml=base + "feeds/subscriptions.opml",
        )


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Immutable input to pure feed renderers.

    Attributes
    ----------
    items :
        Canonical essay sequence.
    feed_title :
        Channel/feed title string.
    feed_description :
        Human description of the feed set.
    author_name :
        Author display name (Paul Graham).
    author_url :
        Author homepage URL.
    language :
        BCP 47 language tag for JSON Feed (``en``).
    home_page_url :
        Official essays index URL.
    public :
        Deployed public URLs, or ``None`` when not configured.
    feed_id :
        Stable Atom feed ``id`` (tag URI).
    generator :
        Generator product string including version.
    build_updated_at :
        Feed-level logical build timestamp (UTC).
    category :
        Category label applied to RSS channel/items.
    """

    items: tuple[EssayItem, ...]
    feed_title: str
    feed_description: str
    author_name: str
    author_url: str
    language: str
    home_page_url: str
    public: PublicUrls | None
    feed_id: str
    generator: str
    build_updated_at: datetime
    category: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Machine-readable validation / build assurance report.

    Attributes
    ----------
    valid :
        Overall pass/fail.
    status :
        ``updated`` | ``unchanged`` | ``checked`` | ``dry_run`` | ``failed``.
    validated_at :
        When this report was produced (UTC).
    source_url :
        Source index URL used.
    source_sha256 :
        SHA-256 of source body when available.
    extraction :
        Optional extraction metadata dict.
    changes :
        Optional change-set dict.
    item_count :
        Number of canonical items.
    formats :
        Per-format path/hash/ok map.
    parity :
        Cross-format parity check results.
    first_item :
        Boundary item summary or ``None``.
    last_item :
        Boundary item summary or ``None``.
    checks :
        Named boolean check results.
    errors :
        Hard validation errors.
    warnings :
        Non-fatal warnings.
    """

    valid: bool
    status: str
    validated_at: datetime
    source_url: str
    source_sha256: str | None
    extraction: dict[str, Any] | None
    changes: dict[str, Any] | None
    item_count: int
    formats: dict[str, Any]
    parity: dict[str, bool]
    first_item: dict[str, str] | None
    last_item: dict[str, str] | None
    checks: dict[str, bool]
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable report payload."""
        return {
            "valid": self.valid,
            "status": self.status,
            "validated_at": dt_to_iso(self.validated_at),
            "source_url": self.source_url,
            "source_sha256": self.source_sha256,
            "extraction": self.extraction,
            "changes": self.changes,
            "item_count": self.item_count,
            "formats": self.formats,
            "parity": self.parity,
            "first_item": self.first_item,
            "last_item": self.last_item,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def utc_now() -> datetime:
    """Return current UTC time with microsecond=0."""
    return datetime.now(UTC).replace(microsecond=0)


def dt_to_iso(value: datetime) -> str:
    """Serialize a timezone-aware datetime to ISO-8601 with offset."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_iso_dt(value: str) -> datetime:
    """Parse ISO-8601 timestamps into UTC datetimes."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def rfc822_utc(value: datetime) -> str:
    """Format datetime as RFC 822 / RSS lastBuildDate UTC string."""
    from email.utils import format_datetime

    return format_datetime(value.astimezone(UTC), usegmt=True)


def rfc3339_utc(value: datetime) -> str:
    """Format datetime as RFC 3339 UTC with trailing Z."""
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(value: bytes) -> str:
    """Return hex SHA-256 digest of bytes."""
    import hashlib

    return hashlib.sha256(value).hexdigest()


def normalize_text(value: str) -> str:
    """NFC-normalize, strip illegal XML 1.0 controls, collapse whitespace."""
    normalized = unicodedata.normalize("NFC", value)
    xml_safe = "".join(
        character
        for character in normalized
        if (
            character in "\t\n\r"
            or 0x20 <= ord(character) <= 0xD7FF
            or 0xE000 <= ord(character) <= 0xFFFD
            or 0x10000 <= ord(character) <= 0x10FFFF
        )
    )
    return " ".join(xml_safe.split())


def canonicalize_url(base_url: str, href: str) -> str:
    """Resolve, HTTPS-normalize, de-fragment, and clean a source URL.

    Raises
    ------
    FeedError
        On unsupported schemes, credentials, or disallowed hosts.
    """
    import html as html_module

    parts = urlsplit(urljoin(base_url, html_module.unescape(href.strip())))
    if parts.scheme.lower() not in {"http", "https"}:
        raise FeedError(f"Unsupported URL scheme in {href!r}.")
    if parts.username or parts.password:
        raise FeedError(f"User-info is not permitted in item URL {href!r}.")

    host = (parts.hostname or "").lower().rstrip(".")
    if host == "www.paulgraham.com":
        host = "paulgraham.com"
    if host not in ALLOWED_HOSTS:
        raise FeedError(f"Unexpected item host {host!r} in {href!r}.")

    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"

    if host == "paulgraham.com":
        query = ""
    else:
        query = urlencode(parse_qsl(parts.query, keep_blank_values=False))

    return urlunsplit(("https", netloc, path, query, ""))


def canonicalize_public_url(value: str, *, field: str) -> str:
    """Validate and return an absolute HTTP(S) public URL without user-info/fragment."""
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise FeedError(f"{field} must be an absolute HTTP(S) URL: {value!r}")
    if parts.username or parts.password or parts.fragment:
        raise FeedError(f"{field} must not contain user-info or a fragment.")
    return urlunsplit(parts)


def is_content_candidate(url: str, title: str) -> bool:
    """Return whether a URL/title pair is essay-index feed content."""
    if not title:
        return False
    parts = urlsplit(url)
    if parts.hostname == "paulgraham.com":
        return parts.path not in EXCLUDED_INTERNAL_PATHS
    return parts.hostname == "sep.turbifycdn.com" and parts.path in PROTECTED_EXTERNAL_PATHS


def make_stable_id(url: str) -> tuple[str, bool]:
    """Derive ``(stable_id, is_permalink)`` from a canonical item URL.

    Internal essays use the HTTPS URL as a permalink GUID. Turbify chapters use
    a UUIDv5 URN from the **queryless** URL so cache-busting queries never churn
    identity.
    """
    parts = urlsplit(url)
    if parts.hostname == "paulgraham.com":
        return url, True
    stable_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, stable_url)}", False


def description_for(item: EssayItem) -> str:
    """Concise metadata-only RSS/Atom description/summary."""
    return f"Read “{item.title}” by Paul Graham."


def content_text_for(item: EssayItem) -> str:
    """Non-empty JSON Feed ``content_text`` (metadata-only, no essay body)."""
    return f"Read “{item.title}” by Paul Graham at the official source:\n{item.url}"


def logical_signature(
    items: tuple[EssayItem, ...] | list[EssayItem],
    *,
    public_base_url: str | None,
    feed_title: str,
    feed_description: str,
    generator: str,
) -> dict[str, Any]:
    """Build the canonical logical signature dict for no-op detection."""
    return {
        "items": [
            {
                "title": item.title,
                "url": item.url,
                "stable_id": item.stable_id,
                "is_permalink": item.is_permalink,
            }
            for item in items
        ],
        "public_base_url": public_base_url,
        "feed_title": feed_title,
        "feed_description": feed_description,
        "generator": generator,
    }


def logical_signature_sha256(
    items: tuple[EssayItem, ...] | list[EssayItem],
    *,
    public_base_url: str | None,
    feed_title: str,
    feed_description: str,
    generator: str,
) -> str:
    """SHA-256 of the deterministic logical signature JSON."""
    payload = logical_signature(
        items,
        public_base_url=public_base_url,
        feed_title=feed_title,
        feed_description=feed_description,
        generator=generator,
    )
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256_bytes(raw)


def item_boundary(
    items: tuple[EssayItem, ...] | list[EssayItem], which: str
) -> dict[str, str] | None:
    """Return first or last item summary for reports."""
    if not items:
        return None
    item = items[0] if which == "first" else items[-1]
    return {
        "title": item.title,
        "url": item.url,
        "stable_id": item.stable_id,
    }


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_controls(value: str) -> str:
    """Remove residual C0 controls (defensive)."""
    return _CONTROL_RE.sub("", value)


# Silence unused import warning for asdict if re-exported for callers
_ = asdict
