"""End-to-end: Typer CLI update + check (local file + mocked network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner

from paul_graham_essay_feeds.cli import app
from paul_graham_essay_feeds.model import SOURCE_URL
from tests.html_samples import synthetic_index_html

pytestmark = pytest.mark.e2e
runner = CliRunner()

_SMALL_INDEX = synthetic_index_html(essay_count=1)
_ESSAY_PAGE = """\
<html><head>
<title>Essay 0</title>
<meta name="description" content="A short summary for essay zero." />
</head><body>
Essay 0
January 2020
Body text for the mocked essay page.
</body></html>
"""
_TXT_BODY = "Chapter text for Ansi Common Lisp.\n"


def _mock_index() -> None:
    respx.get(SOURCE_URL).mock(return_value=httpx.Response(200, text=_SMALL_INDEX))


def _mock_essay_pages() -> None:
    respx.get("https://paulgraham.com/essay-0.html").mock(
        return_value=httpx.Response(200, text=_ESSAY_PAGE)
    )
    respx.get("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt").mock(
        return_value=httpx.Response(200, text=_TXT_BODY)
    )
    respx.get("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt").mock(
        return_value=httpx.Response(200, text=_TXT_BODY)
    )


def _mock_link_probes(*, status: int = 200) -> None:
    for url in (
        "https://paulgraham.com/essay-0.html",
        "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
        "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt",
    ):
        respx.head(url).mock(return_value=httpx.Response(status))


def test_cli_update_then_check(repo_root: Path, sample_html_path: Path) -> None:
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
    feeds = repo_root / "feeds"
    assert (feeds / "rss.xml").is_file()
    assert (feeds / "atom.xml").is_file()
    assert (feeds / "feed.json").is_file()
    assert not (repo_root / "data" / "essays.json").exists()
    assert not (feeds / ".manifest.json").exists()
    check = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert check.exit_code == 0, check.output


def test_cli_check_fails_before_update(repo_root: Path) -> None:
    result = runner.invoke(app, ["check", "--repo-root", str(repo_root), "--quiet"])
    assert result.exit_code == 1


def test_cli_update_bad_html(repo_root: Path, tmp_path: Path) -> None:
    bad = tmp_path / "empty.html"
    bad.write_text("<html></html>", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--min-items",
            "10",
            "--source-file",
            str(bad),
        ],
    )
    assert result.exit_code == 1


@respx.mock
def test_cli_update_network_fetch(repo_root: Path) -> None:
    """update without --source-file fetches the index over HTTP."""
    _mock_index()
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--min-items",
            "3",
            "--retries",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repo_root / "feeds" / "rss.xml").is_file()
    assert respx.calls.call_count >= 1


@respx.mock
def test_cli_update_enrich_with_mocked_pages(repo_root: Path) -> None:
    """--enrich scrapes each essay URL (respx-mocked pages)."""
    _mock_index()
    _mock_essay_pages()
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--enrich",
            "--min-items",
            "3",
            "--retries",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    feed_json = (repo_root / "feeds" / "feed.json").read_text(encoding="utf-8")
    assert "short summary for essay zero" in feed_json
    # Index + three essay pages.
    assert respx.calls.call_count >= 4


@respx.mock
def test_cli_update_validate_links(repo_root: Path) -> None:
    """--validate-links probes each essay URL via HEAD."""
    _mock_index()
    _mock_link_probes(status=200)
    result = runner.invoke(
        app,
        [
            "update",
            "--repo-root",
            str(repo_root),
            "--quiet",
            "--no-enrich",
            "--validate-links",
            "--min-items",
            "3",
            "--retries",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repo_root / "feeds" / "rss.xml").is_file()
    head_calls = [c for c in respx.calls if c.request.method == "HEAD"]
    assert len(head_calls) == 3


def test_cli_update_oserror_exits_1(
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


def test_cli_check_oserror_exits_1(
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
