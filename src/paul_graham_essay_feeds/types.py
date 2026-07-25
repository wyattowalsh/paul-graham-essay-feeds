"""Strict UTC datetime and URL value helpers (ADR-003 / ADR-004)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from paul_graham_essay_feeds.model import ALLOWED_HOSTS, FeedError


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
