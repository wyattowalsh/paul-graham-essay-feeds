"""Command-line interface for ``pg-essay-feeds``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.build import run_build, run_check, run_diff, run_update
from paul_graham_essay_feeds.config import load_config
from paul_graham_essay_feeds.domain import FeedError

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse command tree."""
    parser = argparse.ArgumentParser(
        prog="pg-essay-feeds",
        description=(
            "Safely generate and update unofficial multi-format feeds for "
            "Paul Graham's essays index."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="Path to config.toml")
    common.add_argument(
        "--public-base-url",
        help="Public site base URL for self links and OPML (no placeholders).",
    )
    common.add_argument("--min-items", type=int, help="Safety floor for item count.")
    common.add_argument("--repo-root", type=Path, help="Repository root path.")
    common.add_argument("--quiet", action="store_true", help="Reduce stdout noise.")
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute results without writing artifacts.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    update = sub.add_parser(
        "update",
        parents=[common],
        help="Fetch, extract, reconcile, build, validate, and publish.",
    )
    update.add_argument(
        "--source-file",
        type=Path,
        help="Read HTML from a local file instead of fetching.",
    )
    update.add_argument("--timeout", type=float, help="HTTP timeout seconds.")
    update.add_argument("--retries", type=int, help="HTTP retry count.")
    update.add_argument(
        "--force",
        action="store_true",
        help="Ignore conditional HTTP 304 and rewrite even if unchanged.",
    )
    update.add_argument(
        "--allow-removals",
        action="store_true",
        help="Accept item removals after review.",
    )
    update.add_argument(
        "--allow-nonprefix-additions",
        action="store_true",
        help="Accept non-prefix historical insertions after review.",
    )

    sub.add_parser(
        "build",
        parents=[common],
        help="Build all formats from persisted canonical data (no network).",
    )
    sub.add_parser(
        "check",
        parents=[common],
        help="Validate local state and outputs without network access.",
    )

    diff = sub.add_parser(
        "diff",
        parents=[common],
        help="Report proposed changes without writing artifacts.",
    )
    diff.add_argument(
        "--source-file",
        type=Path,
        help="Read HTML from a local file instead of fetching.",
    )
    diff.add_argument("--timeout", type=float, help="HTTP timeout seconds.")
    diff.add_argument("--retries", type=int, help="HTTP retry count.")
    diff.add_argument("--allow-removals", action="store_true")
    diff.add_argument("--allow-nonprefix-additions", action="store_true")

    return parser


def _cli_overrides(args: argparse.Namespace) -> dict:
    overrides: dict = {}
    if getattr(args, "public_base_url", None) is not None:
        overrides["public_base_url"] = args.public_base_url
    if getattr(args, "min_items", None) is not None:
        overrides["min_items"] = args.min_items
    if getattr(args, "repo_root", None) is not None:
        overrides["repo_root"] = args.repo_root
    if getattr(args, "timeout", None) is not None:
        overrides["timeout"] = args.timeout
    if getattr(args, "retries", None) is not None:
        overrides["retries"] = args.retries
    if getattr(args, "allow_removals", False):
        overrides["allow_removals"] = True
    if getattr(args, "allow_nonprefix_additions", False):
        overrides["allow_nonprefix_additions"] = True
    return overrides


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point returning a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "min_items", None) is not None and args.min_items < 1:
        parser.error("--min-items must be at least 1.")
    if getattr(args, "retries", None) is not None and args.retries < 0:
        parser.error("--retries must be non-negative.")
    if getattr(args, "timeout", None) is not None and args.timeout <= 0:
        parser.error("--timeout must be positive.")

    try:
        cfg = load_config(
            repo_root=args.repo_root if getattr(args, "repo_root", None) else None,
            config_path=args.config if getattr(args, "config", None) else None,
            cli_overrides=_cli_overrides(args),
        )
        if args.command == "update":
            return run_update(
                cfg,
                source_file=args.source_file,
                dry_run=args.dry_run,
                force=args.force,
                quiet=args.quiet,
            )
        if args.command == "build":
            return run_build(cfg, dry_run=args.dry_run, quiet=args.quiet)
        if args.command == "check":
            return run_check(cfg, write_report=not args.dry_run, quiet=args.quiet)
        if args.command == "diff":
            return run_diff(
                cfg,
                source_file=args.source_file,
                quiet=args.quiet,
            )
        parser.error(f"Unknown command {args.command!r}")
    except FeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, PermissionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
