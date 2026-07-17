"""Unit tests for Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from paul_graham_essay_feeds.cli import _settings, app
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.model import Essay, utc_now

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "update" in result.output
    assert "check" in result.output


def test_check_missing_feeds(repo_root: Path) -> None:
    result = runner.invoke(app, ["--repo-root", str(repo_root), "--quiet", "check"])
    # Typer puts options after command often; try command-first style
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 1


def test_update_missing_source(repo_root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--source-file",
            str(repo_root / "nope.html"),
        ],
    )
    assert result.exit_code == 1


def test_check_bad_rss(repo_root: Path) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("not rss", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry/></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text('{"version":"x","items":[]}', encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 1


def test_check_bad_atom(repo_root: Path) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("<rss><item/></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<notfeed/>", encoding="utf-8")
    (feeds / "feed.json").write_text('{"version":"x","items":[]}', encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 1


def test_check_bad_json(repo_root: Path) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("<rss><item/></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry/></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 1


def test_check_below_min_items(repo_root: Path) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("<rss><item></item></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry></entry></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text(
        '{"version":"x","items":[{"id":"1"}]}',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "5", "--quiet"],
    )
    assert result.exit_code == 1


def test_check_count_parity_mismatch(repo_root: Path) -> None:
    """RSS/Atom/JSON item counts must agree."""
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text(
        "<rss><item></item><item></item></rss>",
        encoding="utf-8",
    )
    (feeds / "atom.xml").write_text("<feed><entry></entry></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text(
        '{"version":"x","items":[{"id":"1"}]}',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 1


def test_check_invalid_json_items(repo_root: Path) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("<rss><item></item></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry></entry></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text(
        '{"version":"x","items":"not-a-list"}',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 1


def test_update_from_source_file(repo_root: Path, sample_html_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repo_root / "feeds" / "rss.xml").is_file()


def test_update_source_file_oversize(tmp_path: Path) -> None:
    """RV-S-002: local source file over max_bytes fails."""
    from paul_graham_essay_feeds.cli import _read_source_file
    from paul_graham_essay_feeds.model import FeedError

    huge = tmp_path / "big.html"
    huge.write_bytes(b"x" * 2000)
    with pytest.raises(FeedError, match="over"):
        _read_source_file(huge, max_bytes=512)


def test_update_skips_when_index_hash_unchanged(repo_root: Path, sample_html_path: Path) -> None:
    """Second update with same source skips rewrite when feeds exist with matching hash."""
    args = [
        "update",
        "--repo-root",
        str(repo_root),
        "--quiet",
        "--no-enrich",
        "--source-file",
        str(sample_html_path),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    feed_json = (repo_root / "feeds" / "feed.json").read_text(encoding="utf-8")
    mtime = (repo_root / "feeds" / "rss.xml").stat().st_mtime_ns
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert (repo_root / "feeds" / "feed.json").read_text(encoding="utf-8") == feed_json
    assert (repo_root / "feeds" / "rss.xml").stat().st_mtime_ns == mtime
    assert not (repo_root / "data" / "essays.json").exists()


def test_update_force_rewrites(repo_root: Path, sample_html_path: Path) -> None:
    args = [
        "update",
        "--repo-root",
        str(repo_root),
        "--quiet",
        "--no-enrich",
        "--source-file",
        str(sample_html_path),
    ]
    assert runner.invoke(app, args).exit_code == 0
    forced = runner.invoke(app, [*args, "--force"])
    assert forced.exit_code == 0, forced.output


def test_update_verbose(repo_root: Path, sample_html_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--verbose",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output


def _write_one_essay(repo_root: Path) -> list[Essay]:
    essays = [
        Essay(
            position=1,
            title="A",
            url="https://paulgraham.com/a.html",
            stable_id="https://paulgraham.com/a.html",
            is_permalink=True,
            summary="Short summary for check tests.",
        ),
    ]
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays, built_at=now),
    )
    return essays


def test_check_ok(repo_root: Path) -> None:
    _write_one_essay(repo_root)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 0


def test_check_fails_wrong_content_text(repo_root: Path) -> None:
    _write_one_essay(repo_root)
    path = repo_root / "feeds" / "feed.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["content_text"] = "does not match summary"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 1


def test_env_enrich_false_without_flag(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_ENRICH=false without CLI flags keeps enrich off."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH", "false")
    enrich = MagicMock(side_effect=lambda essays, **_: essays)
    monkeypatch.setattr("paul_graham_essay_feeds.cli.enrich_essays", enrich)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    enrich.assert_not_called()


def test_cli_enrich_overrides_env_false(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--enrich forces enrichment on even when env disables it."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH", "false")
    enrich = MagicMock(side_effect=lambda essays, **_: essays)
    monkeypatch.setattr("paul_graham_essay_feeds.cli.enrich_essays", enrich)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    enrich.assert_called_once()
    kwargs = enrich.call_args.kwargs
    assert "max_bytes" in kwargs
    assert kwargs["retries"] is not None


def test_env_validate_links_true_without_flag(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_VALIDATE_LINKS=true without flag still probes."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "true")
    validate = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.cli.validate_essays_live", validate)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    validate.assert_called_once()
    kwargs = validate.call_args.kwargs
    assert "timeout" in kwargs
    assert "retries" in kwargs
    assert "workers" in kwargs
    assert "max_bytes" in kwargs
    assert kwargs["workers"] == 4


def test_quiet_preferred_when_both_set() -> None:
    settings = _settings(
        repo_root=None,
        min_items=None,
        quiet=True,
        verbose=True,
    )
    assert settings.quiet is True
    assert settings.verbose is False


def test_env_quiet_not_clobbered_by_cli_default(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env quiet survives when -q is not on the command line (default False)."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_QUIET", "true")
    configure = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.cli.configure_logging", configure)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    configure.assert_called_once()
    assert configure.call_args.kwargs["quiet"] is True


def test_cli_quiet_and_verbose_prefer_quiet(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both -q and -v on the CLI → quiet wins."""
    configure = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.cli.configure_logging", configure)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--verbose",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    configure.assert_called_once()
    assert configure.call_args.kwargs["quiet"] is True
    assert configure.call_args.kwargs["verbose"] is False


def test_extract_passes_source_url_as_base_url(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_extract(html: str, **kwargs: Any) -> list[Essay]:
        captured.update(kwargs)
        from paul_graham_essay_feeds.extract import extract_essays as real

        return real(html, **kwargs)

    monkeypatch.setattr("paul_graham_essay_feeds.cli.extract_essays", fake_extract)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "base_url" in captured
    assert captured["base_url"]


def test_update_oserror_exits_1(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.write_feeds",
        MagicMock(side_effect=OSError("disk full")),
    )
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 1


def test_check_oserror_exits_1(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feeds = repo_root / "feeds"
    feeds.mkdir()
    (feeds / "rss.xml").write_text("<rss><item/></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry/></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text('{"version":"x","items":[]}', encoding="utf-8")

    real_read_text = Path.read_text

    def flaky_read(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if self.name == "rss.xml":
            raise OSError("permission denied")
        return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", flaky_read)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 1
