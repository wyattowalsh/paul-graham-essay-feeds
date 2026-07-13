"""CLI happy paths, fixture build, and no-op stability."""

from __future__ import annotations

from pathlib import Path

from paul_graham_essay_feeds.build import run_check, run_update
from paul_graham_essay_feeds.cli import main
from paul_graham_essay_feeds.config import load_config
from paul_graham_essay_feeds.domain import sha256_bytes


def test_cli_parser_and_help() -> None:
    from paul_graham_essay_feeds.cli import build_parser

    parser = build_parser()
    assert parser.prog == "pg-essay-feeds"
    help_text = parser.format_help()
    assert "update" in help_text
    assert "check" in help_text


def test_fixture_update_and_noop_mtime(
    tmp_repo: Path,
    fixture_html: Path,
    public_base: str,
) -> None:
    # Lower min_items via config already 1; use real 233 extract into essays first
    # via update --source-file with min_items override through load_config.
    cfg = load_config(
        repo_root=tmp_repo,
        config_path=tmp_repo / "config.toml",
        cli_overrides={
            "public_base_url": public_base,
            "min_items": 233,
        },
    )
    # Point source-file update
    rc = run_update(
        cfg,
        source_file=fixture_html,
        dry_run=False,
        force=True,
        quiet=True,
    )
    assert rc == 0
    assert cfg.path_rss.is_file()
    assert cfg.path_atom.is_file()
    assert cfg.path_json_feed.is_file()
    assert cfg.path_opml.is_file()
    assert cfg.path_essays.is_file()

    stats1 = {
        name: (path.stat().st_mtime_ns, sha256_bytes(path.read_bytes()))
        for name, path in {
            "rss": cfg.path_rss,
            "atom": cfg.path_atom,
            "json": cfg.path_json_feed,
            "opml": cfg.path_opml,
        }.items()
    }

    rc2 = run_update(
        cfg,
        source_file=fixture_html,
        dry_run=False,
        force=False,
        quiet=True,
    )
    assert rc2 == 0
    for name, path in {
        "rss": cfg.path_rss,
        "atom": cfg.path_atom,
        "json": cfg.path_json_feed,
        "opml": cfg.path_opml,
    }.items():
        mtime, digest = stats1[name]
        assert path.stat().st_mtime_ns == mtime, f"{name} mtime changed on no-op"
        assert sha256_bytes(path.read_bytes()) == digest, f"{name} hash changed"

    assert run_check(cfg, write_report=True, quiet=True) == 0


def test_cli_check_failure_without_artifacts(tmp_repo: Path, public_base: str) -> None:
    code = main(
        [
            "check",
            "--repo-root",
            str(tmp_repo),
            "--config",
            str(tmp_repo / "config.toml"),
            "--public-base-url",
            public_base,
            "--min-items",
            "1",
        ]
    )
    assert code == 1


def test_diff_with_source_file(tmp_repo: Path, fixture_html: Path, public_base: str) -> None:
    # Seed essays from fixture via update first
    cfg = load_config(
        repo_root=tmp_repo,
        config_path=tmp_repo / "config.toml",
        cli_overrides={"public_base_url": public_base, "min_items": 233},
    )
    run_update(cfg, source_file=fixture_html, force=True, quiet=True)
    code = main(
        [
            "diff",
            "--repo-root",
            str(tmp_repo),
            "--config",
            str(tmp_repo / "config.toml"),
            "--source-file",
            str(fixture_html),
            "--min-items",
            "233",
            "--public-base-url",
            public_base,
        ]
    )
    assert code == 0
