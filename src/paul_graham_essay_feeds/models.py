"""Schema SSOT: durable catalog contracts, feed DTOs, errors, and helpers.

Pydantic models are the schema SSOT. No parallel JSON Schema tree is maintained;
optional JSON Schema export can be generated from these models if needed later.

``FeedSnapshot`` / ``FeedEntrySnapshot`` are in-memory DTOs used by feed
renderers only (not durable catalog storage).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from enum import IntEnum, StrEnum
from typing import Any, Final, Literal, TypeVar
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema
from tqdm import tqdm

from paul_graham_essay_feeds import __version__

# --- Constants ---

SOURCE_URL: Final = "https://paulgraham.com/articles.html"
JSON_FEED_VERSION: Final = "https://jsonfeed.org/version/1.1"
ATOM_NS: Final = "http://www.w3.org/2005/Atom"
DC_NS: Final = "http://purl.org/dc/elements/1.1/"
# Safety floor for discovery/check — not the live catalog size (that grows over time).
MIN_ITEMS: Final = 233
# PGF-2026-013: 1-4 one-run omissions are held; 2nd observation hard-deletes.
# ≥ this many removals may quarantine (ratio gate in discover).
ABSENCE_CONFIRMATIONS_TO_DELETE: Final[int] = 2
ABSENCE_QUARANTINE_MIN_REMOVED: Final[int] = 5
ABSENCE_HYSTERESIS_MAX_REMOVED: Final[int] = ABSENCE_QUARANTINE_MIN_REMOVED - 1
MAX_BYTES: Final = 5 * 1024 * 1024
ALLOWED_HOSTS: Final = frozenset({"paulgraham.com", "sep.turbifycdn.com"})
EXCLUDED_PATHS: Final = frozenset({"/", "/index.html", "/articles.html", "/rss.html"})
PROTECTED_PATHS: Final = frozenset({"/ty/cdn/paulgraham/acl1.txt", "/ty/cdn/paulgraham/acl2.txt"})
FEED_ID: Final = "tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds"
FEED_ID_SIMPLE: Final = "tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds:simple"
FEED_TITLE: Final = "Paul Graham: Essays"
FEED_DESCRIPTION: Final = (
    "Unofficial metadata feeds for Paul Graham's essays, "
    "ordered newest to oldest from the official index."
)
AUTHOR: Final = "Paul Graham"
AUTHOR_URL: Final = "https://paulgraham.com/"
# Single source of truth: ``__version__`` in ``__init__.py``.
GENERATOR: Final = f"pg-essay-feeds/{__version__}"
FEED_SUMMARY_CHARS: Final = 400
SUMMARY_QUALITY_THRESHOLD: Final = 0.6
SummarySource = Literal[
    "meta_description",
    "og_description",
    "twitter_description",
    "content_paragraph",
    "title",
]
_REPO_URL: Final = "https://github.com/wyattowalsh/paul-graham-essay-feeds"
STAGING_MANIFEST_SCHEMA_VERSION: Final[Literal[1]] = 1
MATERIALIZE_POINTER_SCHEMA_VERSION: Final[Literal[1]] = 1
# Exact public artifact set (catalog + six flat feeds), stable POSIX order.
STAGING_ARTIFACT_RELS: Final[tuple[str, ...]] = (
    "catalog.json",
    "feeds/rss.xml",
    "feeds/atom.xml",
    "feeds/feed.json",
    "feeds/rss.simple.xml",
    "feeds/atom.simple.xml",
    "feeds/feed.simple.json",
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_GENERATION_ID_HEX = re.compile(r"[0-9a-f]{32}")
_T = TypeVar("_T")


class FeedError(RuntimeError):
    """Pipeline failure (user-facing message; non-retryable by default)."""


class ExitCode(IntEnum):
    """Stable process exit codes for automation."""

    SUCCESS = 0
    USAGE = 1
    VERIFICATION = 2
    NETWORK = 3
    INTERNAL = 4


class UserFacingError(FeedError):
    """Expected operational failure with a concise message and exit code."""

    def __init__(self, message: str, *, exit_code: ExitCode = ExitCode.USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ConfigurationError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.USAGE)


class VerificationError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.VERIFICATION)


class NetworkSourceError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.NETWORK)


def format_validation_error(exc: ValidationError) -> str:
    """One-line-ish Settings/model validation diagnostic without traceback."""
    errors = exc.errors()
    if not errors:
        return "Invalid configuration."
    parts: list[str] = []
    for err in errors[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    extra = len(errors) - len(parts)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return "Invalid configuration: " + "; ".join(parts) + suffix


def exit_code_for_exception(exc: BaseException) -> int:
    """Map known exceptions to ExitCode integers."""
    if isinstance(exc, UserFacingError):
        return int(exc.exit_code)
    if isinstance(exc, ValidationError):
        return int(ExitCode.USAGE)
    if isinstance(exc, FeedError):
        return int(ExitCode.USAGE)
    return int(ExitCode.INTERNAL)


def require_aware_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalize aware values to UTC."""
    if value.tzinfo is None:
        raise FeedError("Naive datetime rejected; timezone-aware UTC required")
    return value.astimezone(UTC)


def normalize_essay_url(url: str, *, allow_loopback: bool = False) -> str:
    """Normalize allowlisted essay URLs consistently.

    - Require absolute http(s) URL
    - Strip fragments
    - Lowercase host; map www.paulgraham.com → paulgraham.com
    - Disallow userinfo
    - HTTPS required except loopback HTTP when allow_loopback
    """
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise FeedError(f"URL must be absolute http(s): {url!r}")
    if parts.username or parts.password:
        raise FeedError(f"URL must not include userinfo: {url!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise FeedError(f"URL missing host: {url!r}")
    if host.startswith("www.") and host.count(".") >= 2:
        host = host[4:]
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if is_loopback:
        if not allow_loopback:
            raise FeedError(f"Loopback URL not allowed: {url!r}")
        if parts.scheme not in {"http", "https"}:
            raise FeedError(f"Invalid loopback scheme: {url!r}")
    else:
        if parts.scheme != "https":
            raise FeedError(f"URL must be https: {url!r}")
        if host not in ALLOWED_HOSTS:
            raise FeedError(f"Host not allowed: {host!r}")
    # Rebuild without fragment; preserve path/query/port.
    netloc = host
    if parts.port and not (
        (parts.scheme == "https" and parts.port == 443)
        or (parts.scheme == "http" and parts.port == 80)
    ):
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


class UtcDateTime(datetime):
    """datetime subclass marker for pydantic annotations requiring aware UTC."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: type, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        datetime_schema = core_schema.datetime_schema()

        def _validate(value: datetime) -> datetime:
            return require_aware_utc(value)

        return core_schema.no_info_after_validator_function(_validate, datetime_schema)


@dataclass(frozen=True, slots=True)
class OutputPolicy:
    """How a command should present progress and diagnostics."""

    quiet: bool = False
    verbose: bool = False
    machine: bool = False  # JSON / non-TTY: no progress bars

    @property
    def show_progress(self) -> bool:
        return not self.quiet and not self.machine


class ProgressReporter:
    """Thread-safe-enough progress facade; quiet/machine emit nothing."""

    def __init__(self, policy: OutputPolicy | None = None) -> None:
        self.policy = policy or OutputPolicy()

    def track(
        self,
        iterable: Iterable[_T],
        *,
        desc: str = "",
        unit: str = "it",
        total: int | None = None,
    ) -> Iterable[_T]:
        if not self.policy.show_progress:
            return iterable
        return tqdm(iterable, desc=desc, unit=unit, total=total)

    @contextmanager
    def spinner(self, desc: str = "") -> Iterator[None]:
        if not self.policy.show_progress:
            yield
            return
        bar = tqdm(total=0, desc=desc, bar_format="{desc}")
        try:
            yield
        finally:
            bar.close()


NULL_REPORTER = ProgressReporter(OutputPolicy(quiet=True))


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _require_relative_posix_path(path: str) -> str:
    """Reject absolute, traversing, Windows, or empty-segment POSIX paths."""
    if not path:
        raise ValueError("artifact path must be a non-empty relative POSIX path")
    if path.startswith("/"):
        raise ValueError(f"artifact path must be relative (no leading /): {path!r}")
    if "\\" in path:
        raise ValueError(f"artifact path must be POSIX (no backslash): {path!r}")
    parts = path.split("/")
    if any(part == "" for part in parts):
        raise ValueError(f"artifact path has empty segment: {path!r}")
    if any(part == ".." for part in parts):
        raise ValueError(f"artifact path must not contain '..': {path!r}")
    return path


def _require_sha256_hex(digest: str) -> str:
    """Reject empty or malformed SHA-256 hex (exactly 64 lowercase hex chars)."""
    if _SHA256_HEX.fullmatch(digest) is None:
        raise ValueError("digest must be 64 lowercase hexadecimal characters")
    return digest


def require_generation_id(value: str) -> str:
    """Reject generation ids that are not a 32-character lowercase hex token."""
    if _GENERATION_ID_HEX.fullmatch(value) is None:
        raise ValueError("gen_id must be a 32-character lowercase hex generation id")
    return value


class MaterializePhase(StrEnum):
    """Recovery pointer phase for staged → public materialize."""

    MATERIALIZING = "materializing"
    COMPLETE = "complete"


class StagingManifest(_StrictModel):
    """Private staging ``MANIFEST.json``: exact seven public artifact digests."""

    schema_version: Literal[1] = Field(
        description="Staging manifest schema version (1 = seven-file digest map).",
    )
    gen_id: str = Field(
        min_length=1,
        description="32-character lowercase hex generation id (uuid4.hex).",
    )
    files: dict[str, str] = Field(
        description="Relative POSIX path → lowercase SHA-256 hex of file bytes.",
    )

    @field_validator("gen_id")
    @classmethod
    def _generation_id(cls, value: str) -> str:
        return require_generation_id(value)

    @model_validator(mode="after")
    def _exact_artifact_files(self) -> StagingManifest:
        """Fail closed unless ``files`` is exactly ``STAGING_ARTIFACT_RELS``."""
        for rel, digest in self.files.items():
            _require_relative_posix_path(rel)
            _require_sha256_hex(digest)
        expected = frozenset(STAGING_ARTIFACT_RELS)
        actual = frozenset(self.files)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("extra " + ", ".join(extra))
            raise ValueError(
                "files keys must be exactly the seven staging artifact paths ("
                + "; ".join(details)
                + ")"
            )
        return self


class MaterializePointer(_StrictModel):
    """Private ``.cache/materialize.json`` recovery pointer (fail closed)."""

    schema_version: Literal[1] = Field(
        description="Materialize pointer schema version (1 = gen_id + phase).",
    )
    gen_id: str = Field(
        min_length=1,
        description="32-character lowercase hex generation id (uuid4.hex).",
    )
    phase: MaterializePhase = Field(description="Materialize recovery phase.")

    @field_validator("gen_id")
    @classmethod
    def _generation_id(cls, value: str) -> str:
        return require_generation_id(value)


class ResourceState(_StrictModel):
    """HTTP resource cache evidence for index or essay pages.

    Schema v2 separates attempt/response clocks from successful validation so
    failed refreshes never mint a success TTL.
    """

    etag: str | None = Field(default=None, description="Last observed ETag validator.")
    last_modified: str | None = Field(
        default=None, description="Last observed Last-Modified validator."
    )
    raw_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 of wire/raw transfer bytes (pre-content-decode); "
            "never the decoded HTML entity."
        ),
    )
    decoded_sha256: str | None = Field(
        default=None,
        description="SHA-256 of decoded text/entity bytes when HTML was decoded.",
    )
    raw_bytes_received: int | None = Field(
        default=None,
        ge=0,
        description="Wire byte count of the raw transfer when captured; unset when unknown.",
    )
    decoded_bytes_received: int | None = Field(
        default=None,
        ge=0,
        description="Decoded entity size in bytes when known; unset when unknown.",
    )
    last_checked_at: datetime | None = Field(
        default=None,
        description=(
            "UTC time of the latest request attempt (success or failure). "
            "Never used as content time. Synchronized with last_attempted_at "
            "on schema-v2 writes."
        ),
    )
    last_attempted_at: datetime | None = Field(
        default=None, description="UTC time of the last request attempt (success or failure)."
    )
    last_response_at: datetime | None = Field(
        default=None, description="UTC time of the last HTTP response or transport outcome."
    )
    last_success_at: datetime | None = Field(
        default=None,
        description="UTC time of the last successful validation (304 or accepted 200).",
    )
    failure_count: int = Field(
        default=0,
        ge=0,
        description="Consecutive failure count since last successful validation.",
    )
    last_error_kind: str | None = Field(
        default=None, description="Typed last error kind (timeout, http_5xx, parse, …)."
    )
    last_error_message: str | None = Field(
        default=None, description="Short last error message for diagnostics."
    )
    next_retry_at: datetime | None = Field(
        default=None, description="UTC earliest time the resource is due after failure backoff."
    )
    status_code: int | None = Field(default=None, description="Last HTTP status when known.")
    selected_encoding: str | None = Field(
        default=None,
        description=(
            "Encoding chosen by the HTML decoder on an accepted 200. "
            "Preserved on 304 (not inferred from an empty body)."
        ),
    )

    @field_validator(
        "last_checked_at",
        "last_attempted_at",
        "last_response_at",
        "last_success_at",
        "next_retry_at",
    )
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware_utc(value)


class CatalogEntry(_StrictModel):
    """One durable essay (or protected chapter) record."""

    stable_id: str = Field(min_length=1, description="Stable feed id / guid.")
    url: str = Field(min_length=1, description="Normalized absolute allowlisted URL.")
    title: str = Field(min_length=1, description="Display title from discovery or page.")
    position: int = Field(
        ge=0,
        description=(
            "0-based catalog order (newest first). In-memory only for schema 3: "
            "derived from entry_order on load and omitted from catalog JSON."
        ),
    )
    first_seen_at: datetime | None = Field(
        default=None, description="First successful index observation (UTC)."
    )
    last_seen_at: datetime | None = Field(
        default=None,
        description=(
            "Latest successful index observation (UTC). Omitted from schema-3 JSON "
            "when it equals catalog index.last_success_at."
        ),
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
        default=None,
        description=(
            "Where the summary was derived (meta_description, og_description, "
            "twitter_description, content_paragraph, or title)."
        ),
    )
    summary_quality: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Quality score in [0, 1]."
    )
    quality_flags: tuple[str, ...] = Field(
        default=(),
        description=(
            "Stable summary quality flag tokens from extraction "
            "(translation_menu, promo, nav_like, domain_search, book_promo, "
            "related_links, high_link_density, too_short, replacement_char, …)."
        ),
    )
    prior_good_summary: str | None = Field(
        default=None, description="Last good summary retained across recoverable failures."
    )
    page: ResourceState = Field(
        default_factory=ResourceState, description="Per-page fetch/cache evidence."
    )
    consecutive_absences: int = Field(
        default=0,
        ge=0,
        description=(
            "Private consecutive index-omission count. 0 when the id is present "
            "on the latest successful index. A one-run omission of 1-4 items "
            "increments this without deleting; a second consecutive observation "
            "hard-deletes (current-index mirror). Omitted from catalog JSON when 0."
        ),
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


def _fill_omitted_catalog_entry_fields(payload: dict[str, Any]) -> None:
    """Fill omitted ``position`` / ``last_seen_at`` on raw entry dicts (in-place).

    Schema 3 omits redundant ``position`` (derived from ``entry_order``) and
    ``last_seen_at`` when it matches ``index.last_success_at``. Load/migrate
    must restore both before ``CatalogEntry`` validation.
    """
    order = payload.get("entry_order")
    entries = payload.get("entries")
    if not isinstance(order, list) or not isinstance(entries, dict):
        return
    index = payload.get("index")
    shared_last_seen: object
    if isinstance(index, dict):
        shared_last_seen = index.get("last_success_at")
    elif index is None:
        shared_last_seen = None
    else:
        shared_last_seen = getattr(index, "last_success_at", None)
    for position, sid in enumerate(order):
        if not isinstance(sid, str):
            continue
        raw = entries.get(sid)
        if not isinstance(raw, dict):
            continue
        if raw.get("position") is None:
            raw["position"] = position
        if raw.get("last_seen_at") is None and shared_last_seen is not None:
            raw["last_seen_at"] = shared_last_seen


class Catalog(_StrictModel):
    """Schema-versioned durable catalog (SSOT for generation inputs)."""

    schema_version: Literal[1, 2, 3] = Field(
        description=(
            "Catalog schema version (2 = resource lifecycle clocks; "
            "3 = compact JSON: omit position and shared last_seen_at)."
        ),
    )
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

    @model_validator(mode="before")
    @classmethod
    def _fill_omitted_entry_fields(cls, data: Any) -> Any:
        """Restore compact-JSON omissions before nested CatalogEntry validate."""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        entries = payload.get("entries")
        if isinstance(entries, dict):
            payload["entries"] = {
                sid: dict(raw) if isinstance(raw, dict) else raw for sid, raw in entries.items()
            }
        _fill_omitted_catalog_entry_fields(payload)
        return payload

    @model_validator(mode="after")
    def _relational_invariants(self) -> Catalog:
        """Fail closed on order↔entries bijection, key/stable_id match, positions."""
        missing = [sid for sid in self.entry_order if sid not in self.entries]
        if missing:
            raise ValueError(
                "entry_order references stable_ids missing from entries: "
                + ", ".join(repr(s) for s in missing[:5])
            )
        orphans = [sid for sid in self.entries if sid not in set(self.entry_order)]
        if orphans:
            raise ValueError(
                "entries missing from entry_order: " + ", ".join(repr(s) for s in orphans[:5])
            )
        if len(self.entry_order) != len(set(self.entry_order)):
            raise ValueError("entry_order contains duplicate stable_ids")
        for sid, entry in self.entries.items():
            if entry.stable_id != sid:
                raise ValueError(
                    f"entries key {sid!r} does not match entry.stable_id {entry.stable_id!r}"
                )
            if not entry.url.strip():
                raise ValueError(f"entry {sid!r} has blank url")
        # Positions are rewritten by reconcile; require uniqueness only.
        # Temporary order/position drift is allowed until the next reconcile.
        positions = [self.entries[sid].position for sid in self.entry_order]
        if len(positions) != len(set(positions)):
            raise ValueError("catalog entries have duplicate position values")
        return self


class FeedEntrySnapshot(_StrictModel):
    """Normalized item projected into all feed formats (in-memory DTO)."""

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
    """Single immutable projection used by RSS/Atom/JSON renderers (in-memory DTO)."""

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
    variant: Literal["enriched", "simple"] = Field(
        default="enriched",
        description=(
            "Feed product variant for Atom feed id selection "
            "(enriched vs simple); never inferred from URL substrings."
        ),
    )
    index_hash: str | None = Field(
        default=None, description="SHA-256 of the decoded index document when known."
    )
    index_fingerprint: str | None = Field(
        default=None, description="Stable index identity fingerprint when known."
    )
    items: list[FeedEntrySnapshot] = Field(min_length=1, description="Ordered feed items.")

    @field_validator("logical_updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value)


class DiscoveryItem(_StrictModel):
    """One discovered index row before enrichment (identity only)."""

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
    summary_source: SummarySource | None = Field(
        default=None,
        description=(
            "Provenance of summary: meta_description, og_description, "
            "twitter_description, content_paragraph, or title."
        ),
    )
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Heuristic quality in [0, 1] from extraction; unset when not enriched.",
    )
    quality_flags: tuple[str, ...] = Field(
        default=(),
        description=(
            "Stable quality flag tokens from extraction (empty, too_short, promo, "
            "nav_like, translation_menu, domain_search, book_promo, related_links, "
            "high_link_density, replacement_char, …)."
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
    observed_updated_at: datetime | None = Field(
        default=None,
        description=(
            "Truthful material observation time (UTC) for Atom entry "
            "``<updated>`` / JSON ``date_modified``. Catalog projection "
            "always sets this; never invent day-1 or 1970 here."
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


def discovery_item_to_essay(item: DiscoveryItem) -> Essay:
    """Promote a discovery identity row to an Essay (no enrichment yet)."""
    return Essay(
        position=item.position,
        title=item.title,
        url=item.url,
        stable_id=item.stable_id,
        is_permalink=item.is_permalink,
    )


def validate_essay_link(essay: Essay) -> None:
    """Structural validation of a final included link (always-on)."""
    # Re-run host/scheme rules (defense in depth after discovery).
    Essay.model_validate(essay.model_dump())
    parts = urlsplit(essay.url)
    if parts.fragment:
        raise FeedError(f"Fragment not allowed on essay url: {essay.url}")
    if "paulgraham.com/https://" in essay.url:
        raise FeedError(f"Malformed double-prefixed url: {essay.url}")
    TypeAdapter(HttpUrl).validate_python(essay.url)
