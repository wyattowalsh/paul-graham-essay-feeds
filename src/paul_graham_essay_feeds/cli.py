"""Typer CLI: update / check for Paul Graham essay feeds."""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler

from paul_graham_essay_feeds.catalog import (
    default_catalog_path,
    load_catalog,
    require_contained_path,
)
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
    UserFacingError,
    VerificationError,
    exit_code_for_exception,
    format_validation_error,
)
from paul_graham_essay_feeds.pipeline import PipelineAction, run_catalog_pipeline
from paul_graham_essay_feeds.publication import abandon_recovery as abandon_publication_recovery
from paul_graham_essay_feeds.settings import (
    DEFAULT_MAX_LINK_VALIDATIONS,
    DEFAULT_MAX_PAGE_FETCHES,
    Settings,
    budget_label,
)

console = Console(stderr=True)
_DEBUG = False


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


_SIDE_CHANNEL_ACTIONS: frozenset[str] = frozenset(item.value for item in PipelineAction)


def _side_channel_action(action: str) -> str:
    """Allowlist ``unchanged|state_changed|updated`` so GITHUB_OUTPUT cannot inject keys."""
    if action not in _SIDE_CHANNEL_ACTIONS:
        raise FeedError(f"Invalid update action for side-channel: {action!r}")
    return action


def _side_channel_count(value: object) -> int:
    """Render a non-negative integer token; non-ints become 0."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0:
        raise FeedError(f"Invalid side-channel count: {value}")
    return value


def _side_channel_failed_ids(ids: object) -> tuple[str, ...]:
    """Keep at most 50 failed-id strings."""
    if not isinstance(ids, list | tuple):
        return ()
    cleaned: list[str] = []
    for item in ids[:50]:
        text = str(item)
        if not text:
            continue
        cleaned.append(text)
    return tuple(cleaned)


def _result_file_failed_ids(ids: tuple[str, ...]) -> tuple[str, ...]:
    """Omit ids that would break ``key=value`` lines (newline / CR / NUL)."""
    return tuple(item for item in ids if not any(ch in item for ch in "\n\r\0"))


def _contained_result_file(repo_root: Path, result_file: Path) -> Path:
    """Resolve ``--result-file`` under ``repo_root`` and reject escapes / symlink parents."""
    root = Path(repo_root).expanduser().resolve()
    target = Path(result_file).expanduser()
    if not target.is_absolute():
        target = root / target
    return require_contained_path(root, target)


def _github_heredoc(name: str, value: str) -> str:
    """GitHub Actions multiline output block (delimiter cannot appear in ``value``)."""
    delimiter = f"PGF_{uuid.uuid4().hex}"
    while delimiter in value:
        delimiter = f"PGF_{uuid.uuid4().hex}"
    return f"{name}<<{delimiter}\n{value}\n{delimiter}\n"


def _side_channel_counts_block(
    *,
    links_checked: int,
    links_attempted: int,
    links_healthy: int,
    links_failed: int,
    links_skipped: int,
) -> str:
    return (
        f"links_checked={links_checked}\n"
        f"links_attempted={links_attempted}\n"
        f"links_healthy={links_healthy}\n"
        f"links_failed={links_failed}\n"
        f"links_skipped={links_skipped}\n"
    )


def _emit_update_action(
    action: str,
    *,
    repo_root: Path,
    result_file: Path | None,
    links_checked: int = 0,
    links_skipped: int = 0,
    links_healthy: int = 0,
    links_failed: int = 0,
    links_failed_ids: tuple[str, ...] = (),
    links_attempted: int | None = None,
) -> None:
    """Append machine side-channel keys for ``--result-file`` / ``$GITHUB_OUTPUT``.

    ``action`` is last so existing consumers that ``endswith`` still match.
    ``--result-file`` is contained under ``repo_root``. ``$GITHUB_OUTPUT`` uses
    id-only count/action tokens and a heredoc for ``links_failed_ids``.
    """
    action = _side_channel_action(action)
    links_checked = _side_channel_count(links_checked)
    links_skipped = _side_channel_count(links_skipped)
    links_healthy = _side_channel_count(links_healthy)
    links_failed = _side_channel_count(links_failed)
    attempted = links_checked if links_attempted is None else _side_channel_count(links_attempted)
    failed_ids = _side_channel_failed_ids(links_failed_ids)
    counts = _side_channel_counts_block(
        links_checked=links_checked,
        links_attempted=attempted,
        links_healthy=links_healthy,
        links_failed=links_failed,
        links_skipped=links_skipped,
    )
    if result_file is not None:
        contained = _contained_result_file(repo_root, result_file)
        file_ids = _result_file_failed_ids(failed_ids)
        file_payload = counts
        if file_ids:
            file_payload += "links_failed_ids=" + ",".join(file_ids) + "\n"
        file_payload += f"action={action}\n"
        contained.parent.mkdir(parents=True, exist_ok=True)
        with contained.open("a", encoding="utf-8") as handle:
            handle.write(file_payload)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        gha_payload = counts
        if failed_ids:
            gha_payload += _github_heredoc("links_failed_ids", ",".join(failed_ids))
        gha_payload += f"action={action}\n"
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(gha_payload)


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
    all_pages: bool | None = None,
    allow_bootstrap_fallback: bool | None = None,
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
        if allow_bootstrap_fallback is not None:
            data["allow_bootstrap_fallback"] = allow_bootstrap_fallback
        if all_pages is True:
            data["all_pages"] = True
        elif all_pages is False:
            data["all_pages"] = False
            if data.get("max_page_fetches") is None:
                data["max_page_fetches"] = DEFAULT_MAX_PAGE_FETCHES
            if data.get("max_link_validations") is None:
                data["max_link_validations"] = DEFAULT_MAX_LINK_VALIDATIONS
        # Prefer quiet when both end up true (CLI quiet wins over verbose).
        if data.get("quiet") and data.get("verbose"):
            data["verbose"] = False
        return Settings.model_validate(data)
    except ValidationError as exc:
        # Concise expected-config failure (no traceback) — F-014 / AD-006.
        print(format_validation_error(exc), file=sys.stderr)
        raise typer.Exit(code=int(ExitCode.USAGE)) from None
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
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
                "Live-probe essay URLs even on no-op/skip-network runs "
                "(default on; report-only). Use --no-validate-links to skip probes."
            ),
        ),
    ] = None,
    public_base_url: Annotated[
        str | None,
        typer.Option("--public-base-url", help="Public base URL for feed self links"),
    ] = None,
    all_pages: Annotated[
        bool | None,
        typer.Option(
            "--all-pages/--no-all-pages",
            help=(
                "Uncap page fetches and dedicated link probes for a full-corpus "
                "refresh (default caps match CI at 40)"
            ),
        ),
    ] = None,
    from_feeds: Annotated[
        bool,
        typer.Option(
            "--from-feeds/--no-from-feeds",
            help=(
                "Seed the in-memory catalog candidate from existing feeds; "
                "persist only after successful verification/publication."
            ),
        ),
    ] = False,
    abandon_recovery: Annotated[
        bool,
        typer.Option(
            "--abandon-recovery/--no-abandon-recovery",
            help=(
                "Explicit repair for irrecoverable `.cache/materialize.json` "
                "(quarantines pointer + generation)."
            ),
        ),
    ] = False,
    result_file: Annotated[
        Path | None,
        typer.Option(
            "--result-file",
            help=(
                "Append links_checked/links_skipped and "
                "action=unchanged|state_changed|updated for machine consumers"
            ),
        ),
    ] = None,
    allow_bootstrap_fallback: Annotated[
        bool,
        typer.Option(
            "--allow-bootstrap-fallback/--no-allow-bootstrap-fallback",
            help="Allow discovery fallback when no prior catalog exists",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Print tracebacks for unexpected errors"),
    ] = False,
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
        all_pages=all_pages,
        allow_bootstrap_fallback=_cmdline_or_none(
            ctx, "allow_bootstrap_fallback", allow_bootstrap_fallback
        ),
    )
    global _DEBUG
    _DEBUG = bool(debug)
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        if result_file is not None:
            result_file = _contained_result_file(settings.repo_root, result_file)
        reporter = ProgressReporter(
            OutputPolicy(quiet=settings.quiet, machine=not sys.stderr.isatty())
        )
        if not settings.quiet:
            logger.info(
                "Request budget: {} page fetches, {} dedicated link probes",
                budget_label(settings.max_page_fetches),
                budget_label(settings.max_link_validations),
            )
        if abandon_recovery:
            abandon_publication_recovery(settings.repo_root)
        result = run_catalog_pipeline(
            settings,
            source_file=source_file,
            reporter=reporter,
            from_feeds=from_feeds,
        )
        action = result.action
        count = result.essay_count

        _emit_update_action(
            action,
            repo_root=settings.repo_root,
            result_file=result_file,
            links_checked=result.links_checked,
            links_skipped=result.links_skipped,
            links_healthy=result.links_healthy,
            links_failed=result.links_failed,
            links_failed_ids=result.links_failed_ids,
            links_attempted=result.links_checked,
        )
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
    except Exception as exc:
        if _DEBUG:
            logger.exception("{}", exc)
        else:
            logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc


@app.command("check")
def check_cmd(
    ctx: typer.Context,
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", help="Root containing feeds/ and required catalog.json"),
    ] = None,
    min_items: Annotated[
        int | None,
        typer.Option("--min-items", help="Safety floor for essay count"),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Print tracebacks for unexpected errors"),
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Errors only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logs")] = False,
) -> None:
    """Deep-verify ``feeds/`` and required root ``catalog.json`` (M-25)."""
    settings = _settings(
        repo_root=repo_root,
        min_items=min_items,
        quiet=_cmdline_or_none(ctx, "quiet", quiet),
        verbose=_cmdline_or_none(ctx, "verbose", verbose),
    )
    global _DEBUG
    _DEBUG = bool(debug)
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        root = settings.repo_root
        feeds = root / "feeds"
        if not feeds.is_dir():
            raise ConfigurationError(f"Missing feeds directory: {feeds}")
        verify_feed_artifacts(
            root,
            min_items=settings.min_items,
            public_base_url=settings.public_base_url,
        )
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
    except Exception as exc:
        if _DEBUG:
            logger.exception("{}", exc)
        else:
            logger.error("{}", exc)
        raise typer.Exit(code=exit_code_for_exception(exc)) from exc


def _parser_usage_error(exc: BaseException) -> bool:
    """True for Click/Typer parser failures (unknown option, bad value, …)."""
    if isinstance(exc, typer.BadParameter):
        return True
    name = type(exc).__name__
    module = type(exc).__module__
    return (
        name
        in {
            "UsageError",
            "NoSuchOption",
            "MissingParameter",
            "NoSuchCommand",
        }
        and "click" in module
    )


def _run_cli(args: list[str] | None = None) -> int:
    """AD-006 executable boundary: map parser/config/runtime failures to 0-4."""
    try:
        # Typer/Click returns Exit.exit_code instead of raising when
        # standalone_mode is False (typer.core._main).
        result = app(args=args, standalone_mode=False)
    except typer.Abort:
        return int(ExitCode.USAGE)
    except typer.Exit as exc:
        return int(exc.exit_code)
    except ValidationError as exc:
        print(format_validation_error(exc), file=sys.stderr)
        return int(ExitCode.USAGE)
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.exit_code)
    except FeedError as exc:
        print(str(exc), file=sys.stderr)
        return int(ExitCode.USAGE)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return int(ExitCode.INTERNAL)
    except Exception as exc:
        if _parser_usage_error(exc):
            formatter = getattr(exc, "format_message", None)
            message = formatter() if callable(formatter) else str(exc)
            print(f"Error: {message}", file=sys.stderr)
            return int(ExitCode.USAGE)
        if _DEBUG or "--debug" in (args or sys.argv):
            traceback.print_exc()
        print(str(exc), file=sys.stderr)
        return int(ExitCode.INTERNAL)
    if isinstance(result, int):
        return result
    return int(ExitCode.SUCCESS)


def main() -> None:
    """Console script entrypoint (installed ``pg-essay-feeds`` / ``python -m``)."""
    raise SystemExit(_run_cli())


if __name__ == "__main__":
    main()
