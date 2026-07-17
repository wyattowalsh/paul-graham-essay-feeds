"""Cover package __main__ entry."""

from __future__ import annotations

import runpy
from unittest.mock import patch


def test_run_module_main() -> None:
    with (
        patch("paul_graham_essay_feeds.cli.main") as mocked,
        patch("sys.argv", ["paul_graham_essay_feeds", "check", "--help"]),
    ):
        # run __main__ which calls main()
        runpy.run_module("paul_graham_essay_feeds", run_name="__main__")
    mocked.assert_called_once()
