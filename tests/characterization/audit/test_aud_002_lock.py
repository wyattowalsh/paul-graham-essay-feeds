"""AUD-002 / PGF-2026-001: POSIX flock writer lock (stable inode, no mtime steal)."""

from __future__ import annotations

import errno
import fcntl
import inspect
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest

from paul_graham_essay_feeds.models import FeedError
from paul_graham_essay_feeds.publication import (
    WriteLock,
    acquire_write_lock,
    release_write_lock,
)

_ACTOR_WAIT_S = 10.0


def _actor_b_open_then_lock(
    lock_path: str,
    opened: Any,
    go_lock: Any,
    result: Any,
) -> None:
    """Open inode X, wait, then try exclusive flock on that same fd."""
    fd = os.open(lock_path, os.O_RDWR)
    try:
        ino = os.fstat(fd).st_ino
        opened.set()
        if not go_lock.wait(timeout=_ACTOR_WAIT_S):
            result.put({"actor": "B", "ino": ino, "locked": False, "error": "timeout"})
            return
        locked = False
        lock_errno: int | None = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            lock_errno = exc.errno
        result.put({"actor": "B", "ino": ino, "locked": locked, "errno": lock_errno})
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _actor_c_acquire_and_hold(
    root: str,
    go_acquire: Any,
    holding: Any,
    done: Any,
    result: Any,
) -> None:
    """Create/lock the path after A released (inode Y if A unlinked)."""
    if not go_acquire.wait(timeout=_ACTOR_WAIT_S):
        result.put({"actor": "C", "error": "timeout"})
        return
    lock = acquire_write_lock(Path(root), timeout=5.0)
    try:
        ino = os.fstat(lock.fd).st_ino
        result.put({"actor": "C", "ino": ino, "locked": True})
        holding.set()
        done.wait(timeout=_ACTOR_WAIT_S)
    finally:
        release_write_lock(lock)


def test_live_lock_older_than_one_hour_cannot_be_stolen(tmp_path: Path) -> None:
    held = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        past = time.time() - 3600 - 30
        os.utime(held.path, (past, past))
        with pytest.raises(FeedError, match="Timed out"):
            acquire_write_lock(tmp_path, timeout=0.2)
    finally:
        release_write_lock(held)


def test_owner_a_cannot_release_owner_b_replacement(tmp_path: Path) -> None:
    a = acquire_write_lock(tmp_path, timeout=1.0)
    fcntl.flock(a.fd, fcntl.LOCK_UN)
    b = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert a.token != b.token
        release_write_lock(a)
        on_disk = json.loads(b.path.read_text(encoding="utf-8"))
        assert on_disk["token"] == b.token
        with pytest.raises(FeedError, match="Timed out"):
            acquire_write_lock(tmp_path, timeout=0.2)
    finally:
        release_write_lock(b)


def test_wall_clock_jump_does_not_drop_live_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 400 * 24 * 3600)
        os.utime(held.path, (0, 0))
        with pytest.raises(FeedError, match="Timed out"):
            acquire_write_lock(tmp_path, timeout=0.2)
    finally:
        release_write_lock(held)


def test_close_owner_fd_lets_waiter_acquire(tmp_path: Path) -> None:
    held = acquire_write_lock(tmp_path, timeout=1.0)
    os.close(held.fd)
    held.fd = -1
    waiter = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert isinstance(waiter, WriteLock)
        assert waiter.token != held.token
    finally:
        release_write_lock(waiter)


def test_no_mtime_stale_reclaim_constant() -> None:
    import paul_graham_essay_feeds.publication as pub

    assert not hasattr(pub, "_LOCK_STALE_S")


def test_release_write_lock_source_does_not_unlink() -> None:
    src = inspect.getsource(release_write_lock)
    assert "unlink" not in src


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock inode identity")
def test_three_actor_lock_inode_two_exclusive_owners_impossible(tmp_path: Path) -> None:
    """B opens X; A would-unlink/unlocks X; C locks path; B locks X — one owner.

    If release unlinked, C would create inode Y while B still flocked X.
    """
    start_method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    ctx: Any = multiprocessing.get_context(start_method)
    result: Any = ctx.Queue()
    b_opened = ctx.Event()
    b_go_lock = ctx.Event()
    c_go_acquire = ctx.Event()
    c_holding = ctx.Event()
    c_done = ctx.Event()

    held = acquire_write_lock(tmp_path, timeout=1.0)
    inode_x = os.fstat(held.fd).st_ino
    lock_path = str(held.path)
    b = ctx.Process(
        target=_actor_b_open_then_lock,
        args=(lock_path, b_opened, b_go_lock, result),
    )
    c = ctx.Process(
        target=_actor_c_acquire_and_hold,
        args=(str(tmp_path), c_go_acquire, c_holding, c_done, result),
    )
    b.start()
    c.start()
    try:
        assert b_opened.wait(timeout=_ACTOR_WAIT_S)
        release_write_lock(held)
        assert held.path.is_file()
        assert held.path.stat().st_ino == inode_x
        c_go_acquire.set()
        assert c_holding.wait(timeout=_ACTOR_WAIT_S)
        b_go_lock.set()
        b.join(timeout=_ACTOR_WAIT_S)
        assert b.exitcode == 0
        reports: list[dict[str, Any]] = []
        for _ in range(2):
            row = result.get(timeout=2.0)
            assert isinstance(row, dict)
            reports.append(row)
        by_actor = {str(row["actor"]): row for row in reports if "actor" in row}
        assert "B" in by_actor
        assert "C" in by_actor
        b_row, c_row = by_actor["B"], by_actor["C"]
        assert c_row.get("locked") is True
        assert c_row["ino"] == inode_x
        two_owners = bool(
            b_row.get("locked") and c_row.get("locked") and b_row["ino"] != c_row["ino"]
        )
        assert two_owners is False
        assert b_row.get("locked") is False
        assert b_row.get("errno") in {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}
        assert held.path.stat().st_ino == inode_x
    finally:
        if held.fd >= 0:
            release_write_lock(held)
        c_done.set()
        b_go_lock.set()
        c_go_acquire.set()
        b.join(timeout=_ACTOR_WAIT_S)
        c.join(timeout=_ACTOR_WAIT_S)
        if b.is_alive():
            b.terminate()
        if c.is_alive():
            c.terminate()
