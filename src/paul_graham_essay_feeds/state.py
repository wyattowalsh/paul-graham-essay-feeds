"""Persistence for canonical essays catalog and updater transport state."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from paul_graham_essay_feeds.domain import (
    BASELINE_IMPORT_AT,
    ESSAYS_SCHEMA_VERSION,
    SOURCE_URL,
    STATE_SCHEMA_VERSION,
    EssayItem,
    FeedError,
    dt_to_iso,
    utc_now,
)
from paul_graham_essay_feeds.io import atomic_write_json

__all__ = [
    "essays_bytes",
    "essays_payload",
    "load_essays",
    "load_json_object",
    "load_optional_json",
    "load_state",
    "merge_items",
    "save_essays",
    "save_state",
]


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a JSON object file or raise :class:`FeedError`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeedError(f"Required file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FeedError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FeedError(f"Expected a JSON object in {path}.")
    return data


def load_optional_json(path: Path) -> dict[str, Any]:
    """Load JSON object if present, else empty dict."""
    if not path.exists():
        return {}
    return load_json_object(path)


def load_state(path: Path) -> dict[str, Any]:
    """Load updater state.json (empty if missing)."""
    return load_optional_json(path)


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomically write state.json."""
    atomic_write_json(path, state)


def _items_from_payload(data: dict[str, Any]) -> tuple[EssayItem, ...]:
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise FeedError("Essays payload does not contain an items array.")
    items: list[EssayItem] = []
    for index, row in enumerate(raw_items, start=1):
        if not isinstance(row, dict):
            raise FeedError(f"Essays item {index} is not an object.")
        typed_row: dict[str, Any] = {str(k): v for k, v in row.items()}
        items.append(EssayItem.from_dict(typed_row, position=index))
    # Normalize positions to contiguous 1..n
    normalized = [
        EssayItem(
            position=i,
            title=item.title,
            url=item.url,
            stable_id=item.stable_id,
            is_permalink=item.is_permalink,
            first_seen_at=item.first_seen_at,
            last_changed_at=item.last_changed_at,
        )
        for i, item in enumerate(items, start=1)
    ]
    return tuple(normalized)


def load_essays(
    essays_path: Path,
    *,
    baseline_path: Path | None = None,
) -> tuple[EssayItem, ...]:
    """Load canonical essays, optionally falling back to baseline seed import."""
    if essays_path.is_file():
        return _items_from_payload(load_json_object(essays_path))
    if baseline_path is not None and baseline_path.is_file():
        return _items_from_payload(load_json_object(baseline_path))
    return tuple()


def essays_payload(
    items: tuple[EssayItem, ...] | list[EssayItem],
    *,
    source_url: str = SOURCE_URL,
    logical_signature: str | None = None,
    imported_at: str | None = None,
) -> dict[str, Any]:
    """Build the ``data/essays.json`` object (without writing)."""
    payload: dict[str, Any] = {
        "schema_version": ESSAYS_SCHEMA_VERSION,
        "source_url": source_url,
        "timestamp_semantics": (
            "first_seen_at and last_changed_at are feed-observation metadata, "
            "not original publication dates"
        ),
        "imported_at": imported_at or BASELINE_IMPORT_AT,
        "item_count": len(items),
        "items": [item.to_dict() for item in items],
    }
    if logical_signature is not None:
        payload["logical_signature_sha256"] = logical_signature
    return payload


def essays_bytes(
    items: tuple[EssayItem, ...] | list[EssayItem],
    *,
    source_url: str = SOURCE_URL,
    logical_signature: str | None = None,
    imported_at: str | None = None,
) -> bytes:
    """Serialize essays catalog to pretty JSON bytes for co-publish."""
    payload = essays_payload(
        items,
        source_url=source_url,
        logical_signature=logical_signature,
        imported_at=imported_at,
    )
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def save_essays(
    path: Path,
    items: tuple[EssayItem, ...] | list[EssayItem],
    *,
    source_url: str = SOURCE_URL,
    logical_signature: str | None = None,
    imported_at: str | None = None,
) -> None:
    """Atomically write ``data/essays.json``."""
    atomic_write_json(
        path,
        essays_payload(
            items,
            source_url=source_url,
            logical_signature=logical_signature,
            imported_at=imported_at,
        ),
    )


def merge_items(
    previous: tuple[EssayItem, ...] | list[EssayItem],
    extracted: tuple[EssayItem, ...] | list[EssayItem],
    *,
    now: datetime | None = None,
) -> tuple[EssayItem, ...]:
    """Merge extraction results with prior catalog timestamps.

    * New IDs receive ``now`` for both observation timestamps.
    * Existing IDs keep ``first_seen_at``.
    * ``last_changed_at`` updates only when title or URL changes.
    """
    when = now or utc_now()
    prev_by_id = {item.identity: item for item in previous}
    merged: list[EssayItem] = []
    for index, item in enumerate(extracted, start=1):
        prior = prev_by_id.get(item.identity)
        if prior is None:
            merged.append(
                EssayItem(
                    position=index,
                    title=item.title,
                    url=item.url,
                    stable_id=item.stable_id,
                    is_permalink=item.is_permalink,
                    first_seen_at=when,
                    last_changed_at=when,
                )
            )
            continue
        material = prior.title != item.title or prior.url != item.url
        merged.append(
            EssayItem(
                position=index,
                title=item.title,
                url=item.url,
                stable_id=item.stable_id,
                is_permalink=item.is_permalink,
                first_seen_at=prior.first_seen_at,
                last_changed_at=when if material else prior.last_changed_at,
            )
        )
    return tuple(merged)


def default_state_payload(
    *,
    source_url: str,
    etag: str | None,
    last_modified: str | None,
    source_sha256: str | None,
    min_items_floor: int,
    public_base_url: str | None,
    logical_signature: str | None,
    last_status: str,
    last_built_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a state.json payload."""
    now = utc_now()
    payload: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "source_url": source_url,
        "etag": etag,
        "last_modified": last_modified,
        "source_sha256": source_sha256,
        "min_items_floor": min_items_floor,
        "public_base_url": public_base_url,
        "logical_signature_sha256": logical_signature,
        "last_checked_at": dt_to_iso(now),
        "last_status": last_status,
    }
    if last_built_at is not None:
        payload["last_built_at"] = dt_to_iso(last_built_at)
    return payload
