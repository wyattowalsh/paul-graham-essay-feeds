"""Unit tests for Typer CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from paul_graham_essay_feeds.cli import _settings, app
from paul_graham_essay_feeds.feeds import render_snapshot_feeds, write_feeds
from paul_graham_essay_feeds.models import FeedEntrySnapshot, FeedSnapshot, utc_now
from paul_graham_essay_feeds.settings import DEFAULT_MAX_LINK_VALIDATIONS, DEFAULT_MAX_PAGE_FETCHES

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FROM_FEEDS_HELP = (
    "Seed the in-memory catalog candidate from existing feeds; "
    "persist only after successful verification/publication."
)
_ABANDON_RECOVERY_HELP = (
    "Explicit repair for irrecoverable `.cache/materialize.json` "
    "(quarantines pointer + generation)."
)
_ALL_PAGES_HELP = (
    "Uncap page fetches and dedicated link probes for a full-corpus "
    "refresh (default caps match CI at 40)"
)


def _plain_help(output: str) -> str:
    """Strip ANSI and box-drawing so wrapped Rich help is searchable."""
    return " ".join(_ANSI.sub("", output).replace("│", " ").split())


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "update" in result.output
    assert "check" in result.output
    assert "bootstrap" not in result.output
    assert "site" not in result.output
    assert "legacy-pipeline" not in result.output
    assert "catalog-pipeline" not in result.output


def test_check_help_requires_catalog() -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    plain = _ANSI.sub("", result.output)
    assert "when present" not in plain
    assert "optional catalog" not in plain.lower()
    assert "catalog.json" in plain


def test_update_exposes_from_feeds_option() -> None:
    """`--from-feeds` is a real update flag (param + help; ANSI-safe)."""
    click_app = get_command(app)
    assert isinstance(click_app, TyperGroup)
    update = click_app.commands["update"]
    from_feeds = cast(Any, next(p for p in update.params if p.name == "from_feeds"))
    assert "--from-feeds" in from_feeds.opts
    assert from_feeds.help == _FROM_FEEDS_HELP

    result = runner.invoke(app, ["update", "--help"])
    assert result.exit_code == 0
    plain = _ANSI.sub("", result.output)
    collapsed = _plain_help(result.output)
    assert "from-feeds" in plain
    assert "Seed the in-memory catalog candidate from existing feeds" in collapsed
    assert "persist only after successful" in collapsed
    assert "verification/public" in collapsed


def test_update_from_feeds_help_does_not_claim_durable_bootstrap() -> None:
    """AUD-012: help seeds in-memory candidate; does not persist catalog first."""
    result = runner.invoke(app, ["update", "--help"])
    assert result.exit_code == 0
    collapsed = _plain_help(result.output)
    assert "Seed the in-memory catalog candidate from existing feeds" in collapsed
    assert "persist only after successful" in collapsed
    assert "verification/public" in collapsed
    assert "bootstrap durable catalog from existing feeds/ before update" not in collapsed


def test_update_exposes_abandon_recovery_option() -> None:
    """`--abandon-recovery` is a real update flag (param + help; ANSI-safe)."""
    click_app = get_command(app)
    assert isinstance(click_app, TyperGroup)
    update = click_app.commands["update"]
    flag = cast(Any, next(p for p in update.params if p.name == "abandon_recovery"))
    assert "--abandon-recovery" in flag.opts
    assert "--no-abandon-recovery" in flag.secondary_opts
    assert flag.default is False
    assert flag.help == _ABANDON_RECOVERY_HELP

    result = runner.invoke(app, ["update", "--help"])
    assert result.exit_code == 0
    plain = _ANSI.sub("", result.output)
    collapsed = _plain_help(result.output)
    assert "abandon-recovery" in plain
    assert ".cache/materialize" in collapsed
    assert "quarantines pointer" in collapsed


def test_update_exposes_all_pages_option() -> None:
    """`--all-pages` is a real update flag (param + help; ANSI-safe)."""
    click_app = get_command(app)
    assert isinstance(click_app, TyperGroup)
    update = click_app.commands["update"]
    flag = cast(Any, next(p for p in update.params if p.name == "all_pages"))
    assert "--all-pages" in flag.opts
    assert "--no-all-pages" in flag.secondary_opts
    assert flag.default is None
    assert flag.help == _ALL_PAGES_HELP

    result = runner.invoke(app, ["update", "--help"])
    assert result.exit_code == 0
    collapsed = _plain_help(result.output)
    assert "all-pages" in collapsed
    assert "full-corpus" in collapsed
    assert "40" in collapsed


def test_check_missing_feeds(repo_root: Path) -> None:
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
    assert result.exit_code == 4


def _seed_bad_feeds(repo_root: Path) -> Path:
    """Write intentionally invalid feed bodies under ``feeds/`` (no generations)."""
    feeds = repo_root / "feeds"
    feeds.mkdir(parents=True, exist_ok=True)
    return feeds


def test_check_bad_rss(repo_root: Path) -> None:
    feeds = _seed_bad_feeds(repo_root)
    (feeds / "rss.xml").write_text("not rss", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry/></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text('{"version":"x","items":[]}', encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 2


def test_check_bad_atom(repo_root: Path) -> None:
    feeds = _seed_bad_feeds(repo_root)
    (feeds / "rss.xml").write_text("<rss><item/></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<notfeed/>", encoding="utf-8")
    (feeds / "feed.json").write_text('{"version":"x","items":[]}', encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 2


def test_check_bad_json(repo_root: Path) -> None:
    feeds = _seed_bad_feeds(repo_root)
    (feeds / "rss.xml").write_text("<rss><item/></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text("<feed><entry/></feed>", encoding="utf-8")
    (feeds / "feed.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 2


def test_check_below_min_items(repo_root: Path) -> None:
    feeds = _seed_bad_feeds(repo_root)
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
    assert result.exit_code == 2


def test_check_count_parity_mismatch(repo_root: Path) -> None:
    """RSS/Atom/JSON item counts must agree."""
    feeds = _seed_bad_feeds(repo_root)
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
    assert result.exit_code == 2


def test_check_invalid_json_items(repo_root: Path) -> None:
    feeds = _seed_bad_feeds(repo_root)
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
    assert result.exit_code == 2


def test_check_corrupt_catalog(repo_root: Path) -> None:
    """When catalog.json exists, check fail-closes on corrupt catalog."""
    _write_one_essay(repo_root)
    catalog = repo_root / "catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("{not-json", encoding="utf-8")
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 1


def test_check_catalog_feed_id_mismatch(repo_root: Path) -> None:
    """Catalog entry_order must match enriched and simple JSON feed ids."""
    from datetime import UTC, datetime

    from paul_graham_essay_feeds.catalog import save_catalog
    from paul_graham_essay_feeds.models import Catalog, CatalogEntry

    _write_one_essay(repo_root)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    # Catalog entry_order id differs from the on-disk feed item id.
    catalog = Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        entry_order=["https://paulgraham.com/other.html"],
        entries={
            "https://paulgraham.com/other.html": CatalogEntry(
                stable_id="https://paulgraham.com/other.html",
                url="https://paulgraham.com/other.html",
                title="Other",
                position=0,
                first_seen_at=t0,
                last_seen_at=t0,
                observed_updated_at=t0,
            )
        },
    )
    save_catalog(repo_root / "catalog.json", catalog)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 2


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
    assert (repo_root / "catalog.json").is_file()
    assert not (repo_root / "state" / "current.json").exists()


def test_update_source_file_oversize(tmp_path: Path) -> None:
    """RV-S-002: local source file over max_bytes fails."""
    from paul_graham_essay_feeds.models import FeedError
    from paul_graham_essay_feeds.pipeline import _read_source_file

    huge = tmp_path / "big.html"
    huge.write_bytes(b"x" * 2000)
    with pytest.raises(FeedError, match="over"):
        _read_source_file(huge, max_bytes=512)


def test_update_from_feeds_bootstraps_catalog(
    repo_root: Path,
    sample_html_path: Path,
) -> None:
    """--from-feeds materializes catalog from feeds/ then runs the pipeline."""
    # First publish so feeds/ exist.
    first = runner.invoke(
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
    assert first.exit_code == 0, first.output
    catalog = repo_root / "catalog.json"
    catalog.unlink()
    assert not catalog.is_file()

    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--from-feeds",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert catalog.is_file()


def test_update_from_feeds_failure_before_publish_leaves_no_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI does not persist catalog.json when the pipeline raises after --from-feeds."""
    from paul_graham_essay_feeds.models import FeedError

    pipeline = MagicMock(side_effect=FeedError("verification failed"))
    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", pipeline)
    catalog = tmp_path / "catalog.json"
    assert not catalog.exists()

    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(tmp_path),
            "--quiet",
            "--from-feeds",
        ],
    )
    assert result.exit_code == 1
    assert pipeline.call_args.kwargs["from_feeds"] is True
    assert not catalog.exists()


def test_update_abandon_recovery_runs_before_pipeline_quietly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--abandon-recovery --quiet` calls repair before the pipeline with empty streams."""
    order: list[str] = []

    def fake_abandon(root: Path) -> None:
        order.append("abandon")
        assert root == tmp_path.expanduser().resolve()

    def fake_pipeline(*_args: object, **_kwargs: object) -> MagicMock:
        order.append("pipeline")
        outcome = MagicMock()
        outcome.action = "updated"
        outcome.essay_count = 1
        return outcome

    monkeypatch.setattr("paul_graham_essay_feeds.cli.abandon_publication_recovery", fake_abandon)
    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", fake_pipeline)

    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(tmp_path),
            "--quiet",
            "--abandon-recovery",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    assert order == ["abandon", "pipeline"]


@pytest.mark.parametrize("extra", [[], ["--no-abandon-recovery"]])
def test_update_does_not_abandon_recovery_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
) -> None:
    abandon = MagicMock()
    pipeline = MagicMock()
    pipeline.return_value.action = "updated"
    pipeline.return_value.essay_count = 0
    monkeypatch.setattr("paul_graham_essay_feeds.cli.abandon_publication_recovery", abandon)
    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", pipeline)

    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet", *extra],
    )
    assert result.exit_code == 0, result.output
    abandon.assert_not_called()
    pipeline.assert_called_once()


def test_update_abandon_recovery_error_skips_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paul_graham_essay_feeds.models import FeedError

    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.abandon_publication_recovery",
        MagicMock(side_effect=FeedError("stuck pointer")),
    )
    pipeline = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", pipeline)

    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(tmp_path),
            "--quiet",
            "--abandon-recovery",
        ],
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    combined = f"{result.stderr or ''}{result.output or ''}"
    assert "stuck pointer" in combined
    pipeline.assert_not_called()


def test_update_result_file_not_written_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paul_graham_essay_feeds.models import FeedError

    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.run_catalog_pipeline",
        MagicMock(side_effect=FeedError("stale finalize")),
    )
    result_path = tmp_path / "result.txt"
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(tmp_path),
            "--quiet",
            "--result-file",
            str(result_path),
        ],
    )
    assert result.exit_code == 1
    combined = f"{result.stderr or ''}{result.output or ''}"
    assert "stale finalize" in combined
    assert not result_path.exists()


def test_update_result_file_and_github_output(
    repo_root: Path,
    sample_html_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_path = tmp_path / "out" / "result.txt"
    github_path = tmp_path / "gha" / "output.txt"
    github_path.parent.mkdir(parents=True)
    github_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_path))

    args = [
        "update",
        "--repo-root",
        str(repo_root),
        "--quiet",
        "--no-enrich",
        "--source-file",
        str(sample_html_path),
        "--result-file",
        str(result_path),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert first.stdout == ""
    assert not (first.stderr or "").strip()
    assert "action=updated\n" in result_path.read_text(encoding="utf-8")
    assert "action=updated\n" in github_path.read_text(encoding="utf-8")

    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert second.stdout == ""
    assert not (second.stderr or "").strip()
    assert result_path.read_text(encoding="utf-8").endswith("action=unchanged\n")
    assert github_path.read_text(encoding="utf-8").endswith("action=unchanged\n")
    first_block = result_path.read_text(encoding="utf-8")
    assert "links_checked=" in first_block
    assert "links_skipped=" in first_block


def test_update_skips_when_refresh_not_due(repo_root: Path, sample_html_path: Path) -> None:
    """Second update with same source skips rewrite when refresh planner says not due."""
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
    # Second --force must remain idempotent (same gen id; no immutable mismatch).
    forced_again = runner.invoke(app, [*args, "--force"])
    assert forced_again.exit_code == 0, forced_again.output


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


def _write_one_essay(repo_root: Path) -> None:
    from paul_graham_essay_feeds.catalog import save_catalog
    from paul_graham_essay_feeds.models import Catalog, CatalogEntry

    now = utc_now()
    snapshot = FeedSnapshot(
        logical_updated_at=now,
        generator="pg-essay-feeds/test",
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="Short summary for check tests.",
                observed_updated_at=now,
            ),
        ],
    )
    rss, atom, jf = render_snapshot_feeds(snapshot)
    write_feeds(
        repo_root,
        rss=rss,
        atom=atom,
        json_feed=jf,
        simple_rss=rss,
        simple_atom=atom,
        simple_json_feed=jf,
    )
    # Repository check requires catalog.json parity with feed ids (M-25).
    save_catalog(
        repo_root / "catalog.json",
        Catalog(
            schema_version=2,
            material_config_fingerprint="test",
            entry_order=["https://paulgraham.com/a.html"],
            entries={
                "https://paulgraham.com/a.html": CatalogEntry(
                    stable_id="https://paulgraham.com/a.html",
                    url="https://paulgraham.com/a.html",
                    title="A",
                    position=0,
                    first_seen_at=now,
                    last_seen_at=now,
                    observed_updated_at=now,
                    summary="Short summary for check tests.",
                )
            },
        ),
    )


def test_check_ok(repo_root: Path) -> None:
    _write_one_essay(repo_root)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


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
    assert result.exit_code == 2


def test_env_enrich_false_without_flag(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_ENRICH=false without CLI flags keeps enrich off."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH", "false")
    enrich = MagicMock(side_effect=lambda essays, **_: essays)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)
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
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)
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
    """PG_ESSAY_FEEDS_VALIDATE_LINKS=true without CLI flag still probes."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "true")
    validate = MagicMock(return_value=None)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)
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


def test_no_validate_links_flag_skips_probes(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-validate-links skips probes even when env default would probe."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "true")
    validate = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--no-validate-links",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    validate.assert_not_called()


def _mock_update_pipeline(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    pipeline = MagicMock()
    pipeline.return_value.action = "unchanged"
    pipeline.return_value.essay_count = 0
    pipeline.return_value.links_checked = 0
    pipeline.return_value.links_skipped = 0
    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", pipeline)
    return pipeline


def test_update_default_fetch_budgets_match_ci(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PGF-2026-014: omitted --all-pages keeps the conservative CI caps."""
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_ALL_PAGES", raising=False)
    pipeline = _mock_update_pipeline(monkeypatch)
    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet"],
    )
    assert result.exit_code == 0, result.output
    settings = pipeline.call_args.args[0]
    assert settings.max_page_fetches == DEFAULT_MAX_PAGE_FETCHES
    assert settings.max_link_validations == DEFAULT_MAX_LINK_VALIDATIONS
    assert settings.all_pages is False


def test_update_all_pages_uncaps_fetch_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "40")
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "40")
    pipeline = _mock_update_pipeline(monkeypatch)
    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet", "--all-pages"],
    )
    assert result.exit_code == 0, result.output
    settings = pipeline.call_args.args[0]
    assert settings.all_pages is True
    assert settings.max_page_fetches is None
    assert settings.max_link_validations is None


def test_env_all_pages_without_flag_uncaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_ALL_PAGES", "true")
    pipeline = _mock_update_pipeline(monkeypatch)
    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet"],
    )
    assert result.exit_code == 0, result.output
    settings = pipeline.call_args.args[0]
    assert settings.all_pages is True
    assert settings.max_page_fetches is None
    assert settings.max_link_validations is None


def test_no_all_pages_restores_conservative_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_ALL_PAGES", "true")
    pipeline = _mock_update_pipeline(monkeypatch)
    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet", "--no-all-pages"],
    )
    assert result.exit_code == 0, result.output
    settings = pipeline.call_args.args[0]
    assert settings.all_pages is False
    assert settings.max_page_fetches == DEFAULT_MAX_PAGE_FETCHES
    assert settings.max_link_validations == DEFAULT_MAX_LINK_VALIDATIONS


def test_update_prints_planned_request_counts_when_not_quiet(
    repo_root: Path,
    sample_html_path: Path,
) -> None:
    """PGF-2026-014: non-quiet update prints caps and planned counts before work."""
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
    combined = f"{result.output}\n{result.stderr}"
    assert "Request budget:" in combined
    assert "40 page fetches" in combined
    assert "40 dedicated link probes" in combined
    assert "Planned requests:" in combined


def test_quiet_update_hides_planned_request_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_update_pipeline(monkeypatch)
    result = runner.invoke(
        app,
        ["update", "--repo-root", str(tmp_path), "--quiet"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    assert "Request budget:" not in (result.output or "")
    assert "Planned requests:" not in (result.output or "")


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


def test_env_force_true_without_flag(
    repo_root: Path,
    sample_html_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_FORCE=true without --force still bypasses refresh no-op."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_FORCE", "true")
    result_path = tmp_path / "force-result.txt"
    args = [
        "update",
        "--repo-root",
        str(repo_root),
        "--quiet",
        "--no-enrich",
        "--source-file",
        str(sample_html_path),
        "--result-file",
        str(result_path),
    ]
    assert runner.invoke(app, args).exit_code == 0
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert result_path.read_text(encoding="utf-8").endswith("action=updated\n")


def test_no_force_clears_env_force(
    repo_root: Path,
    sample_html_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-force clears PG_ESSAY_FEEDS_FORCE=true so refresh no-op can skip."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_FORCE", "true")
    result_path = tmp_path / "no-force-result.txt"
    base = [
        "update",
        "--repo-root",
        str(repo_root),
        "--quiet",
        "--no-enrich",
        "--source-file",
        str(sample_html_path),
        "--result-file",
        str(result_path),
    ]
    assert runner.invoke(app, base).exit_code == 0
    second = runner.invoke(app, [*base, "--no-force"])
    assert second.exit_code == 0, second.output
    assert result_path.read_text(encoding="utf-8").endswith("action=unchanged\n")


def test_env_validate_links_false_skips_probes(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_VALIDATE_LINKS=false without CLI flag skips probes."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "false")
    validate = MagicMock()
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)
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
    validate.assert_not_called()


def test_cli_validate_links_overrides_env_false(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--validate-links forces probes on even when env disables them."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "false")
    validate = MagicMock(return_value=None)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--validate-links",
            "--source-file",
            str(sample_html_path),
        ],
    )
    assert result.exit_code == 0, result.output
    validate.assert_called_once()


def test_env_verbose_not_clobbered_by_cli_default(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env verbose survives when -v is not on the command line (default False)."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_VERBOSE", "true")
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
    assert configure.call_args.kwargs["verbose"] is True
    assert configure.call_args.kwargs["quiet"] is False


def test_check_env_quiet_without_flag(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PG_ESSAY_FEEDS_QUIET=true without -q → quiet success empty output."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_QUIET", "true")
    _write_one_essay(repo_root)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1"],
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_no_enrich_overrides_env_true(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-enrich keeps enrichment off even when env enables it."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH", "true")
    enrich = MagicMock(side_effect=lambda essays, **_: essays)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)
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
    enrich.assert_not_called()


def test_env_timeout_survives_without_flag(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_TIMEOUT survives when --timeout is omitted."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_TIMEOUT", "12.5")
    captured: dict[str, Any] = {}

    def fake_pipeline(settings: Any, **kwargs: Any) -> Any:
        captured["timeout"] = settings.timeout
        from paul_graham_essay_feeds.pipeline import run_catalog_pipeline as real

        return real(settings, **kwargs)

    monkeypatch.setattr("paul_graham_essay_feeds.cli.run_catalog_pipeline", fake_pipeline)
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
    assert captured["timeout"] == 12.5


def test_env_min_items_survives_on_check(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG_ESSAY_FEEDS_MIN_ITEMS survives when --min-items is omitted on check."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_MIN_ITEMS", "5")
    _write_one_essay(repo_root)
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--quiet"],
    )
    assert result.exit_code == 2


def test_discovery_passes_source_url_as_base_url(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_discover(html: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        from paul_graham_essay_feeds.discover import discover_essays as real

        return real(html, **kwargs)

    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.discover_essays", fake_discover)
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


def test_update_oserror_exits_4(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline._publish_catalog_and_feeds",
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
    assert result.exit_code == 4


def test_check_oserror_exits_4(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_one_essay(repo_root)

    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.verify_feed_artifacts",
        MagicMock(side_effect=OSError("permission denied")),
    )
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 4


def test_update_unexpected_exception_exits_4(
    repo_root: Path,
    sample_html_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.run_catalog_pipeline",
        MagicMock(side_effect=RuntimeError("boom")),
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
    assert result.exit_code == 4


def test_check_unexpected_exception_exits_4(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_one_essay(repo_root)
    monkeypatch.setattr(
        "paul_graham_essay_feeds.cli.verify_feed_artifacts",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    assert result.exit_code == 4


def _run_entrypoint(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    import os
    import sys

    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "paul_graham_essay_feeds", *args],
        check=False,
        capture_output=True,
        cwd=cwd,
        env=merged,
    )


def test_entrypoint_help_exits_0() -> None:
    proc = _run_entrypoint(["--help"])
    assert proc.returncode == 0
    assert b"Traceback" not in proc.stderr
    assert b"update" in proc.stdout


def test_entrypoint_unknown_option_exits_1() -> None:
    proc = _run_entrypoint(["check", "--definitely-not-an-option"])
    assert proc.returncode == 1
    assert b"Traceback" not in proc.stdout + proc.stderr
    assert proc.stderr


def test_entrypoint_invalid_typed_option_exits_1() -> None:
    proc = _run_entrypoint(["check", "--min-items", "not-an-int"])
    assert proc.returncode == 1
    assert b"Traceback" not in proc.stdout + proc.stderr
    assert proc.stderr


def test_entrypoint_invalid_public_base_url_exits_1() -> None:
    proc = _run_entrypoint(
        ["check", "--quiet"],
        env={"PG_ESSAY_FEEDS_PUBLIC_BASE_URL": "http://example.com/feeds"},
    )
    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert b"Traceback" not in combined
    assert b"public_base_url" in combined or b"https" in combined
    assert proc.stderr


def test_entrypoint_quiet_successful_check_empty_streams(repo_root: Path) -> None:
    _write_one_essay(repo_root)
    proc = _run_entrypoint(
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
        cwd=repo_root,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout == b""
    assert proc.stderr == b""


def test_entrypoint_quiet_successful_update_empty_streams(
    repo_root: Path, sample_html_path: Path
) -> None:
    proc = _run_entrypoint(
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--source-file",
            str(sample_html_path),
        ],
        cwd=repo_root,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout == b""
    assert proc.stderr == b""
