"""Locked staged publication for catalog + six flat feed artifacts.

Private layout (gitignored under ``.cache/``)::

    .cache/write.lock
    .cache/generations/<gen_id>/{catalog.json,feeds/*,MANIFEST.json}
    .cache/materialize.json

Public compatibility paths remain root ``catalog.json`` + ``feeds/*``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from paul_graham_essay_feeds.catalog import (
    atomic_write_bytes,
    catalog_to_json,
    default_catalog_path,
    save_catalog,
)
from paul_graham_essay_feeds.feeds import (
    DEFAULT_FEEDS_RELATIVE_DIR,
    ENRICHED_FEED_NAMES,
    SIMPLE_FEED_NAMES,
    write_feeds,
)
from paul_graham_essay_feeds.models import NULL_REPORTER, Catalog, FeedError, ProgressReporter

_LOCK_REL = Path(".cache") / "write.lock"
_GEN_ROOT_REL = Path(".cache") / "generations"
_POINTER_REL = Path(".cache") / "materialize.json"
_LOCK_TIMEOUT_S = 120.0
_LOCK_STALE_S = 3600.0


def acquire_write_lock(root: Path, *, timeout: float = _LOCK_TIMEOUT_S) -> Path:
    """Acquire an exclusive interprocess lock file under ``.cache/write.lock``."""
    lock_path = Path(root) / _LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
            finally:
                os.close(fd)
            return lock_path
        except FileExistsError:
            # Stale lock recovery: abandon locks older than one hour.
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _LOCK_STALE_S:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise FeedError(f"Timed out acquiring write lock: {lock_path}") from None
            time.sleep(0.05)


def release_write_lock(lock_path: Path) -> None:
    """Release a lock acquired by :func:`acquire_write_lock`."""
    with suppress(OSError):
        Path(lock_path).unlink(missing_ok=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_staging_generation(
    root: Path,
    *,
    catalog: Catalog,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
) -> str:
    """Write a complete staged generation; return generation id."""
    gen_id = uuid.uuid4().hex
    gen_dir = Path(root) / _GEN_ROOT_REL / gen_id
    feeds_dir = gen_dir / DEFAULT_FEEDS_RELATIVE_DIR
    feeds_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[tuple[str, bytes]] = [
        (f"feeds/{ENRICHED_FEED_NAMES['rss']}", rss),
        (f"feeds/{ENRICHED_FEED_NAMES['atom']}", atom),
        (f"feeds/{ENRICHED_FEED_NAMES['json']}", json_feed),
        (f"feeds/{SIMPLE_FEED_NAMES['rss']}", simple_rss),
        (f"feeds/{SIMPLE_FEED_NAMES['atom']}", simple_atom),
        (f"feeds/{SIMPLE_FEED_NAMES['json']}", simple_json_feed),
    ]
    catalog_blob = catalog_to_json(catalog).encode("utf-8")
    atomic_write_bytes(gen_dir / "catalog.json", catalog_blob)
    for rel, blob in artifacts:
        atomic_write_bytes(gen_dir / rel, blob)

    manifest: dict[str, Any] = {
        "gen_id": gen_id,
        "files": {
            "catalog.json": _sha256(catalog_blob),
            **{rel: _sha256(blob) for rel, blob in artifacts},
        },
    }
    atomic_write_bytes(
        gen_dir / "MANIFEST.json",
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    logger.debug("Staged generation {} under {}", gen_id, gen_dir)
    return gen_id


def materialize_generation(
    root: Path,
    *,
    gen_id: str,
    reporter: ProgressReporter | None = None,
) -> None:
    """Copy staged generation into public flat paths; record recovery pointer."""
    progress = reporter or NULL_REPORTER
    root = Path(root)
    gen_dir = root / _GEN_ROOT_REL / gen_id
    if not gen_dir.is_dir():
        raise FeedError(f"Missing staged generation: {gen_dir}")

    pointer = {
        "gen_id": gen_id,
        "phase": "materializing",
    }
    pointer_path = root / _POINTER_REL
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    # Materialize feeds first, catalog last (SSOT flips after projections exist).
    write_feeds(
        root,
        rss=(gen_dir / "feeds" / ENRICHED_FEED_NAMES["rss"]).read_bytes(),
        atom=(gen_dir / "feeds" / ENRICHED_FEED_NAMES["atom"]).read_bytes(),
        json_feed=(gen_dir / "feeds" / ENRICHED_FEED_NAMES["json"]).read_bytes(),
        simple_rss=(gen_dir / "feeds" / SIMPLE_FEED_NAMES["rss"]).read_bytes(),
        simple_atom=(gen_dir / "feeds" / SIMPLE_FEED_NAMES["atom"]).read_bytes(),
        simple_json_feed=(gen_dir / "feeds" / SIMPLE_FEED_NAMES["json"]).read_bytes(),
        reporter=progress,
    )
    catalog_blob = (gen_dir / "catalog.json").read_bytes()
    atomic_write_bytes(default_catalog_path(root), catalog_blob)

    pointer["phase"] = "complete"
    pointer_path.write_text(json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # Clear pointer after successful materialize.
    with suppress(OSError):
        pointer_path.unlink(missing_ok=True)


def recover_materialize(root: Path) -> bool:
    """Re-materialize an incomplete generation if a recovery pointer exists.

    Returns True when recovery ran.
    """
    root = Path(root)
    pointer_path = root / _POINTER_REL
    if not pointer_path.is_file():
        return False
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        with suppress(OSError):
            pointer_path.unlink(missing_ok=True)
        return False
    if not isinstance(data, dict):
        return False
    gen_id = data.get("gen_id")
    phase = data.get("phase")
    if not isinstance(gen_id, str) or not gen_id:
        return False
    gen_dir = root / _GEN_ROOT_REL / gen_id
    if not gen_dir.is_dir():
        with suppress(OSError):
            pointer_path.unlink(missing_ok=True)
        return False
    if phase in {"materializing", "complete"}:
        logger.warning("Recovering publication from generation {}", gen_id)
        materialize_generation(root, gen_id=gen_id)
        return True
    return False


# Re-export save helper name for type checkers / tests.
__all__ = [
    "acquire_write_lock",
    "materialize_generation",
    "recover_materialize",
    "release_write_lock",
    "save_catalog",
    "write_staging_generation",
]
