"""Unified staged publish tests (RV-009)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from paul_graham_essay_feeds.domain import FeedError
from paul_graham_essay_feeds.io import (
    publish_artifacts,
    recover_pending_publish,
    scrub_legacy_staging,
)


def test_publish_writes_multiple_parents(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    data = tmp_path / "data"
    a = feeds / "a.xml"
    b = feeds / "b.xml"
    essays = data / "essays.json"
    written = publish_artifacts(
        {a: b"<a/>\n", b: b"<b/>\n", essays: b'{"ok":true}\n'},
        stage_base=tmp_path,
        only_changed=True,
        backup=False,
    )
    assert set(written) == {a, b, essays}
    assert a.read_bytes() == b"<a/>\n"
    assert b.read_bytes() == b"<b/>\n"
    assert essays.read_bytes() == b'{"ok":true}\n'
    assert not list(tmp_path.glob(".publish-staging-*"))
    assert not list(feeds.glob(".staging-*"))
    assert not list(data.glob(".staging-*"))


def test_publish_skips_unchanged_preserves_mtime(tmp_path: Path) -> None:
    target = tmp_path / "feeds" / "rss.xml"
    publish_artifacts({target: b"same\n"}, stage_base=tmp_path, only_changed=False, backup=False)
    mtime = target.stat().st_mtime_ns
    written = publish_artifacts(
        {target: b"same\n"}, stage_base=tmp_path, only_changed=True, backup=False
    )
    assert written == []
    assert target.stat().st_mtime_ns == mtime


def test_only_one_parent_dirty_preserves_sibling_mtime(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    data = tmp_path / "data"
    rss = feeds / "rss.xml"
    essays = data / "essays.json"
    publish_artifacts(
        {rss: b"<rss/>\n", essays: b'{"v":1}\n'},
        stage_base=tmp_path,
        only_changed=False,
        backup=False,
    )
    rss_mtime = rss.stat().st_mtime_ns
    written = publish_artifacts(
        {rss: b"<rss/>\n", essays: b'{"v":2}\n'},
        stage_base=tmp_path,
        only_changed=True,
        backup=False,
    )
    assert written == [essays]
    assert essays.read_bytes() == b'{"v":2}\n'
    assert rss.read_bytes() == b"<rss/>\n"
    assert rss.stat().st_mtime_ns == rss_mtime


def test_incomplete_staging_without_manifest_is_discarded(tmp_path: Path) -> None:
    live = tmp_path / "feeds" / "rss.xml"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"live\n")
    stage = tmp_path / ".publish-staging-1-dead"
    stage.mkdir()
    (stage / "feeds").mkdir()
    (stage / "feeds" / "rss.xml").write_bytes(b"staged-but-incomplete\n")
    recover_pending_publish(tmp_path)
    assert live.read_bytes() == b"live\n"
    assert not stage.exists()


def test_recover_completes_manifest_multi_relpath(tmp_path: Path) -> None:
    live_rss = tmp_path / "feeds" / "rss.xml"
    live_essays = tmp_path / "data" / "essays.json"
    live_rss.parent.mkdir(parents=True)
    live_essays.parent.mkdir(parents=True)
    live_rss.write_bytes(b"old-rss\n")
    live_essays.write_bytes(b"old-essays\n")

    stage = tmp_path / ".publish-staging-9-finish"
    (stage / "feeds").mkdir(parents=True)
    (stage / "data").mkdir(parents=True)
    rss_payload = b"new-rss\n"
    essays_payload = b"new-essays\n"
    (stage / "feeds" / "rss.xml").write_bytes(rss_payload)
    (stage / "data" / "essays.json").write_bytes(essays_payload)
    files = {
        "feeds/rss.xml": hashlib.sha256(rss_payload).hexdigest(),
        "data/essays.json": hashlib.sha256(essays_payload).hexdigest(),
    }
    (stage / "MANIFEST.json").write_text(
        json.dumps({"version": 2, "complete": True, "files": files}),
        encoding="utf-8",
    )
    recovered = recover_pending_publish(tmp_path)
    assert set(recovered) == {live_rss, live_essays}
    assert live_rss.read_bytes() == rss_payload
    assert live_essays.read_bytes() == essays_payload
    assert not stage.exists()


def test_crash_mid_replace_then_recover(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    data = tmp_path / "data"
    a = feeds / "a.xml"
    b = data / "essays.json"
    publish_artifacts(
        {a: b"v1a\n", b: b"v1b\n"},
        stage_base=tmp_path,
        only_changed=False,
        backup=False,
    )

    real_replace = __import__("os").replace
    calls = {"n": 0}

    def flaky_replace(src: str | Path, dst: str | Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash mid-replace")
        return real_replace(src, dst)

    with (
        patch("paul_graham_essay_feeds.io.os.replace", side_effect=flaky_replace),
        pytest.raises(OSError, match="simulated crash"),
    ):
        publish_artifacts(
            {a: b"v2a\n", b: b"v2b\n"},
            stage_base=tmp_path,
            only_changed=False,
            backup=False,
        )

    stages = list(tmp_path.glob(".publish-staging-*"))
    assert len(stages) == 1
    recover_pending_publish(tmp_path)
    assert a.read_bytes() == b"v2a\n"
    assert b.read_bytes() == b"v2b\n"
    assert not list(tmp_path.glob(".publish-staging-*"))


def test_path_outside_stage_base_raises(tmp_path: Path) -> None:
    inside = tmp_path / "repo"
    outside = tmp_path / "other" / "x.xml"
    inside.mkdir()
    outside.parent.mkdir()
    with pytest.raises(FeedError, match="outside stage_base"):
        publish_artifacts(
            {outside: b"nope\n"},
            stage_base=inside,
            only_changed=False,
            backup=False,
        )


def test_scrub_legacy_staging_discards_incomplete(tmp_path: Path) -> None:
    parent = tmp_path / "feeds"
    parent.mkdir()
    live = parent / "rss.xml"
    live.write_bytes(b"live\n")
    stage = parent / ".staging-1-dead"
    stage.mkdir()
    (stage / "rss.xml").write_bytes(b"staged\n")
    scrub_legacy_staging(parent)
    assert live.read_bytes() == b"live\n"
    assert not stage.exists()


def test_scrub_legacy_staging_completes_manifest(tmp_path: Path) -> None:
    parent = tmp_path / "feeds"
    parent.mkdir()
    live = parent / "rss.xml"
    live.write_bytes(b"old\n")
    stage = parent / ".staging-9-finish"
    stage.mkdir()
    payload = b"new\n"
    (stage / "rss.xml").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (stage / "MANIFEST.json").write_text(
        json.dumps({"version": 1, "complete": True, "files": {"rss.xml": digest}}),
        encoding="utf-8",
    )
    recovered = scrub_legacy_staging(parent)
    assert recovered == [live]
    assert live.read_bytes() == payload
    assert not stage.exists()
