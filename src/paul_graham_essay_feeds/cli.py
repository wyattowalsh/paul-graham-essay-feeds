"""Typer CLI: update / check for Paul Graham essay feeds."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog
from paul_graham_essay_feeds.feeds import (
    ENRICHED_FEED_NAMES,
    SIMPLE_FEED_NAMES,
    verify_feed_artifacts,
)
from paul_graham_essay_feeds.models import (
    Catalog,
    ConfigurationError,
    ExitCode,
    FeedError,
    OutputPolicy,
    ProgressReporter,
    VerificationError,
    exit_code_for_exception,
    format_validation_error,
)
from paul_graham_essay_feeds.pipeline import run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings

console = Console(stderr=True)


def _catalog_order_ids(catalog: Catalog) -> list[str]:
    """Ordered stable ids from ``entry_order`` (must exist in ``entries``)."""
    ids: list[str] = []
    for stable_id in catalog.entry_order:
        if stable_id not in catalog.entries:
            raise VerificationError(f"Catalog entry_order references missing entry: {stable_id!r}")
        ids.append(stable_id)
    return ids


def _feed_json_ids(feed_path: Path) -> list[str]:
    try:
        payload = json.loads(feed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Unable to read feed ids from {feed_path}: {exc}") from exc
    items = payload.get("items")
    if not isinstance(items, list):
        raise VerificationError(f"feed.json items must be a list: {feed_path}")
    feed_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            raise VerificationError(f"feed item missing id: {feed_path}")
        feed_ids.append(str(item["id"]))
    return feed_ids


def _assert_catalog_feed_id_parity(catalog: Catalog, root: Path) -> None:
    """Fail check when catalog order disagrees with enriched or simple JSON feeds."""
    catalog_ids = _catalog_order_ids(catalog)
    for name in (ENRICHED_FEED_NAMES["json"], SIMPLE_FEED_NAMES["json"]):
        feed_path = root / "feeds" / name
        feed_ids = _feed_json_ids(feed_path)
        if catalog_ids != feed_ids:
            raise VerificationError(
                "Catalog entry_order ids do not match ordered ids in "
                f"{name} (catalog={len(catalog_ids)}, feed={len(feed_ids)})"
            )


def _emit_update_action(action: str, *, result_file: Path | None) -> None:
    """Append ``action=unchanged|state_changed|updated`` for machine consumers.

    Writes to ``--result-file`` and/or ``$GITHUB_OUTPUT`` when set.
    """
    line = f"action={action}\n"
    if result_file is not None:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        with result_file.open("a", encoding="utf-8") as handle:
            handle.write(line)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(line)


def configure_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    """Configure loguru to emit through Rich on stderr."""
    logger.remove()
    if quiet:
        level = "ERROR"
    elif verbose:
        level = "DEBUG"
    else:
        level = "INFO"
    if sys.stderr.isatty():
        handler = RichHandler(
            console=console,
            show_time=verbose,
            show_path=verbose,
            rich_tracebacks=True,
            markup=True,
        )
        logger.add(handler, level=level, format="{message}")
    else:
        logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")


def _is_cmdline(ctx: typer.Context, name: str) -> bool:
    """True when ``name`` was set on the command line (not default/env)."""
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


def _cmdline_or_none[T](ctx: typer.Context, name: str, value: T) -> T | None:
    """Return ``value`` when set on the command line; otherwise ``None`` (keep Settings)."""
    return value if _is_cmdline(ctx, name) else None


def _settings(
    *,
    repo_root: Path | None,
    min_items: int | None,
    quiet: bool | None = None,
    verbose: bool | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    validate_links: bool | None = None,
    enrich: bool | None = None,
    force: bool | None = None,
    public_base_url: str | None = None,
) -> Settings:
    """Merge CLI overrides onto pydantic-settings (COMMANDLINE > env/.env > defaults)."""
    try:
        base = Settings()
        data = base.model_dump()
        if repo_root is not None:
            data["repo_root"] = repo_root.expanduser().resolve()
        if min_items is not None:
            data["min_items"] = min_items
        if timeout is not None:
            data["timeout"] = timeout
        if retries is not None:
            data["retries"] = retries
        if validate_links is not None:
            data["validate_links"] = validate_links
        if enrich is not None:
            data["enrich"] = enrich
        if force is not None:
            data["force"] = force
        if quiet is not None:
            data["quiet"] = quiet
        if verbose is not None:
            data["verbose"] = verbose
        if public_base_url is not None:
            data["public_base_url"] = public_base_url
        # Prefer quiet when both end up true (CLI quiet wins over verbose).
        if data.get("quiet") and data.get("verbose"):
            data["verbose"] = False
        return Settings.model_validate(data)
    except ValidationError as exc:
        # Concise expected-config failure (no traceback) — F-014 / ADR-006.
        print(format_validation_error(exc), file=sys.stderr)
        raise typer.Exit(code=int(ExitCode.USAGE)) from None


app = typer.Typer(
    name="pg-essay-feeds",
    help="Unofficial RSS/Atom/JSON feeds for paulgraham.com/articles.html",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("update")
def update_cmd(
    ctx: typer.Context,
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", help="Output root (default: cwd / env)"),
    ] = None,
    source_file: Annotated[
        Path | None,
        typer.Option("--source-file", help="Local HTML instead of network fetch"),
    ] = None,
    min_items: Annotated[
        int | None,
        typer.Option("--min-items", help="Safety floor for essay count"),
    ] = None,
    timeout: Annotated[float | None, typer.Option("--timeout", help="HTTP timeout")] = None,
    retries: Annotated[int | None, typer.Option("--retries", help="HTTP retries")] = None,
    enrich: Annotated[
        bool | None,
        typer.Option("--enrich/--no-enrich", help="Scrape each essay page for metadata"),
    ] = None,
    force: Annotated[
        bool | None,
        typer.Option(
            "--force/--no-force",
            help="Bypass refresh planner no-op when nothing is due",
        ),
    ] = None,
    validate_links: Annotated[
        bool | None,
        typer.Option(
            "--validate-links/--no-validate-links",
            help=(
                "Live HEAD/GET each essay URL (default on; report-only). "
                "Use --no-validate-links to skip probes."
            ),
        ),
    ] = None,
    public_base_url: Annotated[
        str | None,
        typer.Option("--public-base-url", help="Public base URL for feed self links"),
    ] = None,
    from_feeds: Annotated[
        bool,
        typer.Option(
            "--from-feeds/--no-from-feeds",
            help=(
                "Bootstrap catalog in memory from existing feeds/; "
                "persist only after a successful publish"
            ),
        ),
    ] = False,
    result_file: Annotated[
        Path | None,
        typer.Option(
            "--result-file",
            help="Append action=unchanged|state_changed|updated for machine consumers",
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Errors only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logs")] = False,
) -> None:
    """Fetch index, reconcile catalog, enrich as planned, publish catalog + feeds."""
    settings = _settings(
        repo_root=repo_root,
        min_items=min_items,
        quiet=_cmdline_or_none(ctx, "quiet", quiet),
        verbose=_cmdline_or_none(ctx, "verbose", verbose),
        timeout=timeout,
        retries=retries,
        validate_links=validate_links,
        enrich=enrich,
        force=force,
        public_base_url=public_base_url,
    )
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        reporter = ProgressReporter(
            OutputPolicy(quiet=settings.quiet, machine=not sys.stderr.isatty())
        )
        result = run_catalog_pipeline(
            settings,
            source_file=source_file,
            reporter=reporter,
            from_feeds=from_feeds,
        )
        action = result.action
        count = result.essay_count

        _emit_update_action(action, result_file=result_file)
        if settings.quiet:
            return
        if action == "unchanged":
            console.print(
                f"[yellow]UNCHANGED[/yellow] — no durable write ({count} essays; refresh not due)"
            )
        elif action == "state_changed":
            console.print(
                f"[cyan]STATE[/cyan] — catalog state written, feeds unchanged ({count} essays)"
            )
        else:
            console.print(
                f"[green]UPDATED[/green] {count} essays → "
                f"[bold]{settings.repo_root / 'feeds'}[/bold]"
            )
    except FeedError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc
    except OSError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc


@app.command("check")
def check_cmd(
    ctx: typer.Context,
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", help="Root containing feeds/ (+ optional catalog.json)"),
    ] = None,
    min_items: Annotated[
        int | None,
        typer.Option("--min-items", help="Safety floor for essay count"),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Errors only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logs")] = False,
) -> None:
    """Deep-verify ``feeds/``; validate root ``catalog.json`` when present."""
    settings = _settings(
        repo_root=repo_root,
        min_items=min_items,
        quiet=_cmdline_or_none(ctx, "quiet", quiet),
        verbose=_cmdline_or_none(ctx, "verbose", verbose),
    )
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        root = settings.repo_root
        feeds = root / "feeds"
        if not feeds.is_dir():
            raise ConfigurationError(f"Missing feeds directory: {feeds}")
        verify_feed_artifacts(root, min_items=settings.min_items)
        catalog_path = default_catalog_path(root)
        # Normal repository bundles require catalog.json (M-25).
        if not catalog_path.is_file():
            raise ConfigurationError(
                f"Missing required catalog.json for repository check: {catalog_path}"
            )
        catalog = load_catalog(catalog_path)
        if catalog is None:
            raise ConfigurationError(f"Unable to load catalog: {catalog_path}")
        _assert_catalog_feed_id_parity(catalog, root)
        if not settings.quiet:
            payload = json.loads((feeds / "feed.json").read_text(encoding="utf-8"))
            count = len(payload["items"])
            console.print(f"[green]VALID[/green] {count} items in [bold]{feeds}[/bold]")
    except FeedError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc
    except OSError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc


def main() -> None:
    """Console script entrypoint."""
    app()


if __name__ == "__main__":
    main()
