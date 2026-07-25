"""Packaging: PEP 561 py.typed is present in the source tree."""

from __future__ import annotations

from importlib import resources

import paul_graham_essay_feeds


def test_installed_package_exposes_py_typed() -> None:
    root = resources.files(paul_graham_essay_feeds)
    assert (root / "py.typed").is_file()
