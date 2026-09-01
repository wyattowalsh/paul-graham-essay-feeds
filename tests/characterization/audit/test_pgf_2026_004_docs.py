"""PGF-2026-004 / 015 / 016 / 018 / 019 / 020 / 021: docs and version coherence."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from paul_graham_essay_feeds import __version__

_REPO = Path(__file__).resolve().parents[3]
_RAW = "https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/"
_SIMPLE = ("rss.simple.xml", "atom.simple.xml", "feed.simple.json")
_ENRICHED = ("rss.xml", "atom.xml", "feed.json")
_GIT_MAIN = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@main"
_GIT_V020 = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@v0.2.0"
_GIT_V100 = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@v1.0.0"
_PIN_SENTENCE = "until the `v1.0.0` tag exists, install from `main`"


def _text(name: str) -> str:
    return (_REPO / name).read_text(encoding="utf-8")


def test_package_version_is_1_0_0() -> None:
    assert __version__ == "1.0.0"
    data = tomllib.loads(_text("pyproject.toml"))
    assert data["tool"]["hatch"]["version"]["path"] == ("src/paul_graham_essay_feeds/__init__.py")
    assert data["project"]["dynamic"] == ["version"]


def test_changelog_1_0_0_lists_pgf_2026_001_through_022() -> None:
    changelog = _text("CHANGELOG.md")
    start = changelog.index("## [1.0.0]")
    end = changelog.index("## [0.2.0]")
    section = changelog[start:end]
    for n in range(1, 23):
        assert f"PGF-2026-{n:03d}" in section, f"missing PGF-2026-{n:03d} in [1.0.0]"
    assert "advertised-but-untagged" in section
    assert "## [0.2.0]" in changelog
    assert changelog.index("## [Unreleased]") < start


def test_readme_and_notebook_install_from_main_until_tag() -> None:
    readme = _text("README.md")
    notebook = _text("notebook.ipynb")
    assert _GIT_MAIN in readme
    assert _GIT_V020 not in readme
    assert _GIT_V100 not in readme
    assert _PIN_SENTENCE in readme
    assert _GIT_MAIN in notebook
    assert _GIT_V020 not in notebook
    assert _GIT_V100 not in notebook
    assert "v1.0.0" in notebook
    assert "1.0.0" in notebook


def test_readme_subscribe_simple_first_six_raw_feeds() -> None:
    readme = _text("README.md")
    h2 = [line[3:].strip() for line in readme.splitlines() if line.startswith("## ")]
    assert h2[0] == "Subscribe"
    assert readme.index("## Subscribe") < readme.index("## Maintainer / custom generation")
    assert readme.index("## Subscribe") < readme.lower().index("colab")
    assert "**Simple (recommended)**" in readme
    assert readme.index("**Simple (recommended)**") < readme.index("**Enriched**")
    for name in _SIMPLE + _ENRICHED:
        url = _RAW + name
        assert url in readme, url
        assert f"[Subscribe]({url})" in readme
    simple_pos = min(readme.index(_RAW + name) for name in _SIMPLE)
    enriched_pos = min(readme.index(_RAW + name) for name in _ENRICHED)
    assert simple_pos < enriched_pos
    subscribe_block = readme[readme.index("## Subscribe") : readme.index("## What you get")]
    assert "No Python required" in subscribe_block
    assert "uvx" not in subscribe_block
    assert "pip install" not in subscribe_block.lower()


def test_notice_does_not_relicense_essay_text() -> None:
    notice = _text("NOTICE")
    assert "MIT" in notice
    assert "Paul Graham" in notice
    assert "does not relicense" in notice
    readme = _text("README.md")
    assert "[NOTICE](./NOTICE)" in readme
    assert "does **not** relicense" in readme
    data = tomllib.loads(_text("pyproject.toml"))
    assert "NOTICE" in data["project"]["license-files"]
    assert "LICENSE" in data["project"]["license-files"]


def test_security_and_contributing_exist() -> None:
    security = _text("SECURITY.md")
    contributing = _text("CONTRIBUTING.md")
    assert "security/advisories" in security
    assert "Do not open a public issue" in security
    assert "DOCS.md" in contributing
    assert "no `docs/` tree" in contributing
    assert "`update` + `check`" in contributing


def test_docs_record_lock_generation_cas_coverage_sha_and_accepted_risks() -> None:
    docs = _text("DOCS.md")
    assert "inode is never unlinked" in docs
    assert "PGF-2026-001" in docs
    assert "last_generation_id" in docs
    assert "PGF-2026-003" in docs
    assert "slower older candidate" in docs
    assert "PGF-2026-002" in docs
    assert "precision = 2" in docs or "precision 2" in docs
    assert "product_sha" in docs
    assert "candidate digest" in docs
    assert "PGF-2026-012" in docs
    assert "PGF-2026-015" in docs
    assert "text/plain" in docs
    assert "PGF-2026-016" in docs
    assert "gh api" in docs
    assert "protect-main" in docs
    assert "conditions.file_path" in docs
    assert "does **not** execute `gh api`" in docs
    assert "Agents must" in docs
    assert "not `gh api` apply rulesets" in docs
    assert "PGF-2026-032" in docs
    assert "PGF-2026-018" in docs
    assert "local seven-file visibility" in docs
    notebook = json.loads(_text("notebook.ipynb"))
    assert isinstance(notebook["cells"], list)
