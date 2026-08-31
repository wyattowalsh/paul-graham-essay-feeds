"""Locked staged publication for catalog + six flat feed artifacts.

Private layout (gitignored under ``.cache/``)::

    .cache/write.lock
    .cache/generations/<gen_id>/{catalog.json,feeds/*,MANIFEST.json}
    .cache/materialize.json
    .cache/quarantine/<timestamp>-<gen_id>/

Public compatibility paths remain root ``catalog.json`` + ``feeds/*``.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import stat
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from paul_graham_essay_feeds.catalog import (
    atomic_write_bytes,
    atomic_write_text,
    catalog_to_json,
    default_catalog_path,
    save_catalog,
    stamp_state_revision,
)
from paul_graham_essay_feeds.feeds import write_feeds
from paul_graham_essay_feeds.models import (
    MATERIALIZE_POINTER_SCHEMA_VERSION,
    NULL_REPORTER,
    STAGING_ARTIFACT_RELS,
    STAGING_MANIFEST_SCHEMA_VERSION,
    Catalog,
    FeedError,
    MaterializePhase,
    MaterializePointer,
    ProgressReporter,
    StagingManifest,
    require_generation_id,
)

_LOCK_REL = Path(".cache") / "write.lock"
_GEN_ROOT_REL = Path(".cache") / "generations"
_POINTER_REL = Path(".cache") / "materialize.json"
_QUARANTINE_REL = Path(".cache") / "quarantine"
_LOCK_TIMEOUT_S = 120.0
_LOCK_POLL_S = 0.05
_LOCK_BUSY_ERRNOS = {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}
_GC_KEEP_COUNT = 2
_GC_MAX_BYTES = 50 * 1024 * 1024
_PROCESS_START_IDENTITY = f"{os.getpid()}:{time.monotonic_ns()}"


@dataclass(slots=True)
class WriteLock:
    """Held exclusive writer lock (open fd + owner token)."""

    path: Path
    fd: int
    token: str


def _process_start_identity() -> str:
    """Pid plus OS start fingerprint (token remains the owner key)."""
    pid = os.getpid()
    with suppress(OSError):
        return f"{pid}:{Path('/proc/self').stat().st_ctime_ns}"
    return _PROCESS_START_IDENTITY


def _lock_payload(token: str) -> bytes:
    body = {
        "pid": os.getpid(),
        "start": _process_start_identity(),
        "token": token,
    }
    return (json.dumps(body, sort_keys=True) + "\n").encode("utf-8")


def _write_lock_fd(fd: int, token: str) -> None:
    payload = _lock_payload(token)
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fsync(fd)


def acquire_write_lock(root: Path, *, timeout: float = _LOCK_TIMEOUT_S) -> WriteLock:
    """Acquire an exclusive POSIX ``fcntl.flock`` on ``.cache/write.lock``.

    The fd stays open for the hold. Contenders retry ``LOCK_EX | LOCK_NB`` until
    ``timeout`` on the monotonic clock. Live locks are never stolen by mtime.
    The lock file inode is stable across release; this owner rewrites the token.
    """
    lock_path = Path(root) / _LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(timeout, 0.0)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    while True:
        fd = -1
        try:
            fd = os.open(str(lock_path), flags, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
            if exc.errno in _LOCK_BUSY_ERRNOS:
                now = time.monotonic()
                if now >= deadline:
                    raise FeedError(f"Timed out acquiring write lock: {lock_path}") from None
                time.sleep(min(_LOCK_POLL_S, max(deadline - now, 0.0)))
                continue
            raise FeedError(f"Failed to acquire write lock: {lock_path}") from exc
        token = secrets.token_hex()
        try:
            _write_lock_fd(fd, token)
        except OSError as exc:
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(fd)
            raise FeedError(f"Failed to write write lock: {lock_path}") from exc
        return WriteLock(path=lock_path, fd=fd, token=token)


def release_write_lock(lock: WriteLock) -> None:
    """Release a lock acquired by :func:`acquire_write_lock`.

    Unlocks and closes the fd only. The ``.cache/write.lock`` inode is kept so a
    waiter that already opened the path cannot lock a stale inode while a new
    file is created at the same name. The next owner rewrites the token in place.
    """
    fd = lock.fd
    if fd >= 0:
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(fd)
        lock.fd = -1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel_path(gen_dir: Path, rel: str) -> Path:
    return Path(gen_dir).joinpath(*rel.split("/"))


def _safe_segment(value: str | None) -> str:
    raw = (value or "unknown").strip() or "unknown"
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in raw)
    return cleaned[:64] or "unknown"


def _contained_generation_dir(root: Path, gen_id: str) -> Path:
    """Resolve ``gen_id`` to a directory strictly under ``.cache/generations``."""
    try:
        gen_id = require_generation_id(gen_id)
    except ValueError as exc:
        raise FeedError(f"Unsafe generation id: {gen_id!r}") from exc
    gen_root = (Path(root) / _GEN_ROOT_REL).resolve()
    candidate = (gen_root / gen_id).resolve()
    try:
        candidate.relative_to(gen_root)
    except ValueError as exc:
        raise FeedError(f"Generation path escapes staging root: {gen_id!r}") from exc
    if candidate == gen_root:
        raise FeedError(f"Generation path escapes staging root: {gen_id!r}")
    return candidate


def _staging_payloads(
    *,
    catalog_blob: bytes,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {
        "catalog.json": catalog_blob,
        "feeds/rss.xml": rss,
        "feeds/atom.xml": atom,
        "feeds/feed.json": json_feed,
        "feeds/rss.simple.xml": simple_rss,
        "feeds/atom.simple.xml": simple_atom,
        "feeds/feed.simple.json": simple_json_feed,
    }
    if frozenset(payloads) != frozenset(STAGING_ARTIFACT_RELS):
        raise RuntimeError("internal staging artifact set mismatch")
    return payloads


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
    """Write a complete staged generation; return generation id.

    Allocates ``gen_id`` first, stamps ``catalog.last_generation_id`` and a
    fresh ``state_revision``, then serializes the catalog and writes artifacts
    + MANIFEST so the staged catalog, MANIFEST, pointer, and public catalog
    share that id.
    """
    gen_id = uuid.uuid4().hex
    stamped = stamp_state_revision(catalog.model_copy(update={"last_generation_id": gen_id}))
    catalog_blob = catalog_to_json(stamped).encode("utf-8")
    gen_dir = Path(root) / _GEN_ROOT_REL / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    payloads = _staging_payloads(
        catalog_blob=catalog_blob,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
    )
    for rel, blob in payloads.items():
        atomic_write_bytes(_rel_path(gen_dir, rel), blob)

    files = {rel: _sha256(payloads[rel]) for rel in STAGING_ARTIFACT_RELS}
    manifest = StagingManifest(
        schema_version=STAGING_MANIFEST_SCHEMA_VERSION,
        gen_id=gen_id,
        files=files,
    )
    atomic_write_bytes(
        gen_dir / "MANIFEST.json",
        (json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
    logger.debug("Staged generation {} under {}", gen_id, gen_dir)
    return gen_id


def _require_contained_regular_file(gen_dir: Path, rel: str) -> Path:
    """Reject absolute, traversal, symlink-escape, and non-regular paths."""
    gen_dir = Path(gen_dir)
    if not rel or rel.startswith("/") or Path(rel).is_absolute():
        raise FeedError(f"Staging path must be relative: {rel}")
    path = _rel_path(gen_dir, rel)
    try:
        resolved = path.resolve()
        resolved.relative_to(gen_dir.resolve())
    except (OSError, ValueError) as exc:
        raise FeedError(f"Staging path escapes generation directory: {rel}") from exc
    try:
        st = path.lstat()
    except OSError as exc:
        raise FeedError(f"Staged file missing for MANIFEST entry: {rel}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise FeedError(f"Staging path must not be a symlink: {rel}")
    if not stat.S_ISREG(st.st_mode):
        raise FeedError(f"Staged path is not a regular file: {rel}")
    return path


def _disk_file_rels(gen_dir: Path) -> set[str]:
    rels: set[str] = set()
    gen_dir = Path(gen_dir)
    for dirpath, _dirnames, filenames in os.walk(gen_dir, followlinks=False):
        base = Path(dirpath)
        for name in filenames:
            full = base / name
            rels.add(full.relative_to(gen_dir).as_posix())
    return rels


def verify_staging_manifest(gen_dir: Path) -> None:
    """Fail closed when staged files do not match ``MANIFEST.json`` digests."""
    gen_dir = Path(gen_dir)
    manifest_path = gen_dir / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FeedError(f"Missing staging MANIFEST: {manifest_path}")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        manifest = StagingManifest.model_validate(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedError(f"Unreadable staging MANIFEST: {manifest_path}") from exc
    except ValidationError as exc:
        raise FeedError(f"Invalid staging MANIFEST: {manifest_path}") from exc

    allowed = set(STAGING_ARTIFACT_RELS) | {"MANIFEST.json"}
    extras = sorted(_disk_file_rels(gen_dir) - allowed)
    if extras:
        raise FeedError(f"Unexpected extra staged files: {', '.join(extras)}")

    for rel in STAGING_ARTIFACT_RELS:
        path = _require_contained_regular_file(gen_dir, rel)
        actual = _sha256(path.read_bytes())
        expected = manifest.files[rel]
        if actual != expected:
            raise FeedError(
                f"Staged digest mismatch for {rel}: expected {expected[:12]}… got {actual[:12]}…"
            )


def _pointer_json(pointer: MaterializePointer) -> str:
    return json.dumps(pointer.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"


def _write_pointer(pointer_path: Path, *, gen_id: str, phase: MaterializePhase) -> None:
    pointer = MaterializePointer(
        schema_version=MATERIALIZE_POINTER_SCHEMA_VERSION,
        gen_id=gen_id,
        phase=phase,
    )
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pointer_path, _pointer_json(pointer))


def _peek_gen_id(pointer_path: Path) -> str | None:
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        gen_id = data.get("gen_id")
        if isinstance(gen_id, str) and gen_id.strip():
            try:
                return require_generation_id(gen_id.strip())
            except ValueError:
                return None
    return None


def _parse_materialize_pointer(pointer_path: Path) -> MaterializePointer:
    try:
        raw = pointer_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FeedError(f"Unreadable materialize pointer: {pointer_path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FeedError(f"Unreadable materialize pointer: {pointer_path}") from exc
    try:
        return MaterializePointer.model_validate(data)
    except ValidationError as exc:
        raise FeedError(f"Invalid materialize pointer: {pointer_path}") from exc


def _quarantine_dir_name(gen_id: str | None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{_safe_segment(gen_id)}"


def _quarantine_pointer_and_generation(
    root: Path,
    *,
    pointer_path: Path,
    gen_id: str | None,
) -> Path:
    qroot = Path(root) / _QUARANTINE_REL
    qroot.mkdir(parents=True, exist_ok=True)
    dest = qroot / _quarantine_dir_name(gen_id)
    if dest.exists():
        dest = qroot / f"{dest.name}-{time.time_ns()}"
    dest.mkdir(parents=True, exist_ok=False)
    if pointer_path.is_file():
        os.replace(pointer_path, dest / "materialize.json")
    if gen_id:
        try:
            gen_dir = _contained_generation_dir(root, gen_id)
        except FeedError:
            gen_dir = None
        if gen_dir is not None and gen_dir.is_dir() and not gen_dir.is_symlink():
            os.rename(gen_dir, dest / "generation")
    return dest


def _quarantine_best_effort(root: Path, *, pointer_path: Path, gen_id: str | None) -> None:
    try:
        _quarantine_pointer_and_generation(root, pointer_path=pointer_path, gen_id=gen_id)
    except OSError as exc:
        logger.error("Failed to quarantine materialize state: {}", exc)


def _generation_size_bytes(gen_dir: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(gen_dir, followlinks=False):
        base = Path(dirpath)
        for name in filenames:
            with suppress(OSError):
                total += (base / name).lstat().st_size
    return total


def _is_known_good_generation(gen_dir: Path) -> bool:
    try:
        verify_staging_manifest(gen_dir)
    except FeedError:
        return False
    return True


def _gc_staged_generations(
    root: Path,
    *,
    current_gen_id: str,
    max_keep: int = _GC_KEEP_COUNT,
    max_bytes: int = _GC_MAX_BYTES,
) -> None:
    """Keep current (+ pointed) and at most one previous known-good generation.

    Caller is assumed to hold the writer lock. Never deletes a generation still
    named by ``materialize.json``. Size cap never drops protected gens.
    """
    gen_root = Path(root) / _GEN_ROOT_REL
    if not gen_root.is_dir():
        return
    protected: set[str] = {current_gen_id}
    pointer_path = Path(root) / _POINTER_REL
    if pointer_path.is_file():
        pointed = _peek_gen_id(pointer_path)
        if pointed:
            protected.add(pointed)

    by_id: dict[str, Path] = {}
    ranked: list[tuple[float, str, Path]] = []
    for child in gen_root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        by_id[child.name] = child
        ranked.append((mtime, child.name, child))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    keep: set[str] = {name for name in protected if name in by_id}
    for _mtime, name, path in ranked:
        if name in keep:
            continue
        if len(keep) >= max_keep:
            break
        if _is_known_good_generation(path):
            keep.add(name)

    def _size_of(names: set[str]) -> int:
        return sum(_generation_size_bytes(by_id[name]) for name in names if name in by_id)

    droppable = [
        name for _mtime, name, _path in reversed(ranked) if name in keep and name not in protected
    ]
    while droppable and _size_of(keep) > max_bytes:
        drop = droppable.pop(0)
        keep.discard(drop)

    for _mtime, name, path in ranked:
        if name in keep:
            continue
        logger.debug("GC staged generation {}", name)
        shutil.rmtree(path, ignore_errors=True)


def materialize_generation(
    root: Path,
    *,
    gen_id: str,
    reporter: ProgressReporter | None = None,
) -> None:
    """Copy staged generation into public flat paths; record recovery pointer."""
    progress = reporter or NULL_REPORTER
    root = Path(root)
    gen_dir = _contained_generation_dir(root, gen_id)
    if not gen_dir.is_dir():
        raise FeedError(f"Missing staged generation: {gen_dir}")

    # Integrity gate before any public write (RV-R-003 / AUD-004).
    verify_staging_manifest(gen_dir)

    pointer_path = root / _POINTER_REL
    _write_pointer(pointer_path, gen_id=gen_id, phase=MaterializePhase.MATERIALIZING)

    blobs = {rel: _rel_path(gen_dir, rel).read_bytes() for rel in STAGING_ARTIFACT_RELS}
    write_feeds(
        root,
        rss=blobs["feeds/rss.xml"],
        atom=blobs["feeds/atom.xml"],
        json_feed=blobs["feeds/feed.json"],
        simple_rss=blobs["feeds/rss.simple.xml"],
        simple_atom=blobs["feeds/atom.simple.xml"],
        simple_json_feed=blobs["feeds/feed.simple.json"],
        reporter=progress,
    )
    atomic_write_bytes(default_catalog_path(root), blobs["catalog.json"])

    _write_pointer(pointer_path, gen_id=gen_id, phase=MaterializePhase.COMPLETE)
    with suppress(OSError):
        pointer_path.unlink(missing_ok=True)
    try:
        _gc_staged_generations(root, current_gen_id=gen_id)
    except OSError as exc:
        logger.warning("Staged generation GC failed: {}", exc)


def recover_materialize(root: Path) -> bool:
    """Re-materialize an incomplete generation if a recovery pointer exists.

    Returns True when recovery ran. Malformed or unverifiable pointers raise
    :class:`FeedError` (fail closed) after a best-effort quarantine.
    """
    root = Path(root)
    pointer_path = root / _POINTER_REL
    if not pointer_path.is_file():
        return False
    gen_id_hint = _peek_gen_id(pointer_path)
    try:
        pointer = _parse_materialize_pointer(pointer_path)
    except FeedError:
        _quarantine_best_effort(root, pointer_path=pointer_path, gen_id=gen_id_hint)
        raise
    try:
        logger.warning("Recovering publication from generation {}", pointer.gen_id)
        materialize_generation(root, gen_id=pointer.gen_id)
        return True
    except FeedError:
        _quarantine_best_effort(root, pointer_path=pointer_path, gen_id=pointer.gen_id)
        raise


def abandon_recovery(root: Path) -> None:
    """Explicit repair: quarantine pointer + generation so recover is a no-op."""
    root = Path(root)
    lock = acquire_write_lock(root)
    try:
        pointer_path = root / _POINTER_REL
        if not pointer_path.is_file():
            return
        gen_id = _peek_gen_id(pointer_path)
        try:
            _quarantine_pointer_and_generation(root, pointer_path=pointer_path, gen_id=gen_id)
            return
        except OSError as exc:
            logger.warning("Quarantine during abandon_recovery failed: {}", exc)
        with suppress(OSError):
            pointer_path.unlink(missing_ok=True)
        if gen_id:
            with suppress(FeedError):
                shutil.rmtree(_contained_generation_dir(root, gen_id), ignore_errors=True)
        if pointer_path.is_file():
            raise FeedError(f"Could not remove materialize pointer: {pointer_path}")
    finally:
        release_write_lock(lock)


__all__ = [
    "WriteLock",
    "abandon_recovery",
    "acquire_write_lock",
    "materialize_generation",
    "recover_materialize",
    "release_write_lock",
    "save_catalog",
    "verify_staging_manifest",
    "write_staging_generation",
]
