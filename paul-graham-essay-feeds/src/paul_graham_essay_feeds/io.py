"""Atomic writes, locks, backups, checksums, and multi-artifact publish."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from paul_graham_essay_feeds.domain import FeedError, sha256_bytes

__all__ = [
    "acquire_lock",
    "atomic_write",
    "atomic_write_json",
    "backup_file",
    "file_sha256",
    "publish_artifacts",
    "recover_pending_publish",
    "scrub_legacy_staging",
    "write_checksums",
]


@contextmanager
def acquire_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive repository-local update lock.

    Uses ``fcntl.flock`` on POSIX. Raises :class:`FeedError` if the lock cannot
    be acquired.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except BlockingIOError as exc:
            raise FeedError(f"Another update holds the lock at {lock_path}.") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        handle.close()


def atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write ``data`` to ``path`` via temp file + ``os.replace`` (same filesystem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any] | list[Any]) -> None:
    """Atomically write pretty-printed UTF-8 JSON with trailing newline."""
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write(path, payload)


def backup_file(path: Path, *, backup_count: int = 3) -> None:
    """Keep a simple ``.bak`` copy of an existing file (bounded by rename chain)."""
    if not path.exists() or backup_count < 1:
        return
    bak = path.with_name(path.name + ".bak")
    if backup_count > 1:
        for index in range(backup_count - 1, 0, -1):
            older = path.with_name(f"{path.name}.bak.{index}")
            newer = path.with_name(f"{path.name}.bak.{index - 1}") if index > 1 else bak
            if newer.exists():
                shutil.copy2(newer, older)
    shutil.copy2(path, bak)


def file_sha256(path: Path) -> str | None:
    """Return hex digest of an existing file, else ``None``."""
    if not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


def _write_staged_file(stage_path: Path, data: bytes) -> str:
    """Write bytes to a staging path with fsync; return hex digest."""
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    with stage_path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return sha256_bytes(data)


def _is_under(path: Path, base: Path) -> bool:
    """Return True when ``path`` is ``base`` or a descendant of ``base``."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _relpath_under(path: Path, base: Path) -> str:
    """Return a POSIX relative path of ``path`` under ``base`` (no ``..``)."""
    base_resolved = base.resolve()
    path_resolved = path.resolve()
    try:
        rel = path_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise FeedError(
            f"Refusing to publish outside stage_base {base_resolved}: {path_resolved}"
        ) from exc
    rel_str = rel.as_posix()
    if rel_str.startswith("../") or rel_str == ".." or Path(rel_str).is_absolute():
        raise FeedError(f"Unsafe relative publish path: {rel_str!r}")
    if any(part == ".." for part in rel.parts):
        raise FeedError(f"Unsafe relative publish path: {rel_str!r}")
    return rel_str


def _common_ancestor(paths: Sequence[Path]) -> Path:
    """Return the deepest common **directory** ancestor of absolute paths."""
    if not paths:
        raise FeedError("publish_artifacts requires at least one path.")
    resolved = [path.resolve() for path in paths]
    try:
        common = Path(os.path.commonpath([str(path) for path in resolved]))
    except ValueError as exc:
        raise FeedError("Artifact paths do not share a common filesystem root.") from exc
    # commonpath of a single file path returns the file itself; stage under its parent.
    if common.is_file() or (not common.exists() and common.suffix):
        return common.parent
    return common


def scrub_legacy_staging(parent: Path) -> list[Path]:
    """Finish or discard legacy per-parent ``.staging-*`` directories.

    Completes MANIFEST v1 (basename keys under the parent) when ``complete``,
    otherwise removes incomplete staging so live artifacts stay untouched.
    """
    recovered: list[Path] = []
    if not parent.is_dir():
        return recovered
    for stage_dir in sorted(parent.glob(".staging-*")):
        if not stage_dir.is_dir():
            continue
        manifest_path = stage_dir / "MANIFEST.json"
        if not manifest_path.is_file():
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        if not isinstance(manifest, dict) or not manifest.get("complete"):
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        files = manifest.get("files")
        if not isinstance(files, dict):
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        for name, expected_sha in files.items():
            rel = str(name)
            if "/" in rel or rel.startswith(".."):
                # Not a legacy basename layout; leave for unified recover if misplaced.
                continue
            staged = stage_dir / rel
            final = parent / rel
            if not staged.is_file():
                continue
            live_sha = file_sha256(final)
            if live_sha == expected_sha:
                continue
            os.replace(staged, final)
            recovered.append(final)
        shutil.rmtree(stage_dir, ignore_errors=True)
    return recovered


def recover_pending_publish(stage_base: Path) -> list[Path]:
    """Complete or discard half-finished unified publishes under ``stage_base``.

    Looks for ``.publish-staging-*`` directories with MANIFEST v2 relative paths.
    Incomplete staging is removed so live artifacts stay untouched.
    """
    recovered: list[Path] = []
    base = stage_base.resolve()
    if not base.is_dir():
        return recovered
    for stage_dir in sorted(base.glob(".publish-staging-*")):
        if not stage_dir.is_dir():
            continue
        manifest_path = stage_dir / "MANIFEST.json"
        if not manifest_path.is_file():
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        if not isinstance(manifest, dict) or not manifest.get("complete"):
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        files = manifest.get("files")
        if not isinstance(files, dict):
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        for rel_name, expected_sha in sorted(files.items(), key=lambda item: str(item[0])):
            rel = str(rel_name)
            if any(part == ".." for part in Path(rel).parts) or Path(rel).is_absolute():
                continue
            staged = stage_dir / rel
            final = base / rel
            if not staged.is_file():
                continue
            live_sha = file_sha256(final)
            if live_sha == expected_sha:
                continue
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, final)
            recovered.append(final)
        shutil.rmtree(stage_dir, ignore_errors=True)
    return recovered


def publish_artifacts(
    artifacts: Mapping[Path, bytes],
    *,
    stage_base: Path | None = None,
    backup: bool = True,
    backup_count: int = 3,
    only_changed: bool = True,
) -> list[Path]:
    """Publish artifacts via unified two-phase staging (dirty-subset transaction).

    1. Skip byte-identical paths when ``only_changed`` (preserves mtimes).
    2. Stage **all dirty paths** under one ``stage_base/.publish-staging-*`` tree
       using relative path keys (works across ``feeds/`` + ``data/`` parents).
    3. Write MANIFEST v2 with ``complete: true`` after all staged files fsync.
    4. Backup live targets, then ``os.replace`` each staged file into place.
    5. Remove staging. Incomplete staging never touches live paths until
       MANIFEST is complete; ``recover_pending_publish`` can finish a crash
       mid-replace.

    Returns
    -------
    list of Path
        Paths that were actually rewritten.
    """
    if not artifacts:
        return []

    abs_items: list[tuple[Path, bytes]] = [
        (path.resolve(), data) for path, data in artifacts.items()
    ]
    base = (
        stage_base.resolve()
        if stage_base is not None
        else _common_ancestor([p for p, _ in abs_items])
    )

    for path, _ in abs_items:
        if not _is_under(path, base):
            raise FeedError(f"Refusing to publish outside stage_base {base}: {path}")

    to_write: list[tuple[Path, bytes, str]] = []
    for path, data in abs_items:
        if only_changed and path.is_file() and path.read_bytes() == data:
            continue
        rel = _relpath_under(path, base)
        to_write.append((path, data, rel))

    if not to_write:
        return []

    # Scrub legacy per-parent stages and recover any unified pending publish.
    parents = {path.parent for path, _, _ in to_write}
    for parent in parents:
        scrub_legacy_staging(parent)
    recover_pending_publish(base)

    token = secrets.token_hex(4)
    stage_dir = base / f".publish-staging-{os.getpid()}-{token}"
    stage_dir.mkdir(parents=True, exist_ok=False)
    written: list[Path] = []
    try:
        file_map: dict[str, str] = {}
        staged_pairs: list[tuple[Path, Path]] = []
        for path, data, rel in sorted(to_write, key=lambda item: item[2]):
            staged = stage_dir / rel
            digest = _write_staged_file(staged, data)
            file_map[rel] = digest
            staged_pairs.append((staged, path))

        manifest = {
            "version": 2,
            "complete": True,
            "files": file_map,
        }
        manifest_path = stage_dir / "MANIFEST.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        if backup:
            for _, path in staged_pairs:
                backup_file(path, backup_count=backup_count)

        for staged, path in staged_pairs:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, path)
            written.append(path)
    except Exception:
        # Leave complete MANIFEST for recovery if replaces started; else drop.
        if not (stage_dir / "MANIFEST.json").is_file():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    else:
        shutil.rmtree(stage_dir, ignore_errors=True)

    return written


def write_checksums(path: Path, files: Sequence[Path]) -> None:
    """Write ``SHA256SUMS``-style lines for existing files (sorted by name)."""
    rows: list[str] = []
    for file_path in sorted(files, key=lambda value: value.name):
        if file_path.exists() and file_path != path:
            rows.append(f"{sha256_bytes(file_path.read_bytes())}  {file_path.name}")
    atomic_write(path, ("\n".join(rows) + "\n").encode("utf-8"))
