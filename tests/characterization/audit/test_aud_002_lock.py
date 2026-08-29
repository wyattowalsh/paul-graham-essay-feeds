"""AUD-002: OS flock writer lock (no mtime steal)."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import FeedError
from paul_graham_essay_feeds.publication import (
    WriteLock,
    acquire_write_lock,
    release_write_lock,
)


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
