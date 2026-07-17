"""Unit tests for logging_setup."""

from __future__ import annotations

from unittest.mock import patch

from paul_graham_essay_feeds.cli import configure_logging


def test_configure_logging_quiet() -> None:
    configure_logging(quiet=True)


def test_configure_logging_verbose_tty() -> None:
    with patch("paul_graham_essay_feeds.cli.sys.stderr.isatty", return_value=True):
        configure_logging(verbose=True, quiet=False)


def test_configure_logging_non_tty() -> None:
    with patch("paul_graham_essay_feeds.cli.sys.stderr.isatty", return_value=False):
        configure_logging(verbose=False, quiet=False)
