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


def pytest_collection_modifyitems(config, items):
    """Auto-mark by directory so unit/ mirrors modules without repeating markers."""
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path or path.endswith("\\unit\\"):
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
        elif "/tests/smoke/" in path:
            item.add_marker(pytest.mark.smoke)
        elif "/tests/live/" in path:
            item.add_marker(pytest.mark.live)
