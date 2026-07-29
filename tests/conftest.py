"""Shared fixtures for offline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.html_samples import synthetic_index_html


@pytest.fixture
def sample_html() -> str:
    """Full synthetic index meeting the default min_items floor."""
    return synthetic_index_html()


@pytest.fixture
def sample_html_path(tmp_path: Path, sample_html: str) -> Path:
    path = tmp_path / "articles.html"
    path.write_text(sample_html, encoding="utf-8")
    return path


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Empty working root for feed outputs."""
    return tmp_path


@pytest.fixture(autouse=True)
def _offline_default_no_live_probes(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Keep offline suites from live-probing essay URLs (default validate_links=True).

    Live-marked tests keep the production default. Opt-in probe tests set the env
    or pass ``--validate-links`` explicitly (and mock HTTP).
    """
    if "live" in request.keywords:
        return
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "false")


def pytest_collection_modifyitems(config, items):
    """Auto-mark by directory so unit/ mirrors modules without repeating markers.

    Flat singleton suites (``tests/test_cli_e2e.py``, ``tests/test_live_fetch.py``)
    carry ``pytestmark`` in-module; directory heuristics cover nested layers only.
    """
    for item in items:
        parts = set(Path(str(item.fspath)).parts)
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        elif "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "smoke" in parts:
            item.add_marker(pytest.mark.smoke)
        elif "characterization" in parts:
            item.add_marker(pytest.mark.characterization)
