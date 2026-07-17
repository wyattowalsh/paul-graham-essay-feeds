"""Typer CLI: update / check Paul Graham essay feeds."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from tqdm import tqdm

from paul_graham_essay_feeds.enrich import enrich_essays
from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.feeds import (
    feed_paths,
    feeds_exist,
    load_index_skip_state,
    render_atom,
    render_json,
    render_rss,
    verify_feed_artifacts,
    write_feeds,
)
from paul_graham_essay_feeds.fetch import decode_html, fetch_html
from paul_graham_essay_feeds.model import Essay, FeedError, content_sha256, utc_now
from paul_graham_essay_feeds.settings import Settings
from paul_graham_essay_feeds.validate import validate_essays_live

console = Console(stderr=True)


def _read_source_file(path: Path, *, max_bytes: int) -> str:
    """Read local index HTML, enforcing the same size cap as network fetches."""
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    if size > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    return decode_html(raw)


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
) -> Settings:
    """Merge CLI overrides onto pydantic-settings (COMMANDLINE > env/.env > defaults)."""
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
    # Prefer quiet when both end up true (CLI quiet wins over verbose).
    if data.get("quiet") and data.get("verbose"):
        data["verbose"] = False
    return Settings.model_validate(data)


def _index_fingerprint(essays: list[Essay]) -> str:
    """Stable index-only fingerprint for no-op detection."""
    return "\n".join(e.index_fingerprint() for e in essays)


def _should_skip_update(
    *,
    root: Path,
    index_hash: str,
    essays: list[Essay],
    force: bool,
) -> bool:
    """True when index hash/fingerprint match ``feed.json`` and feeds already exist."""
    if force:
        return False
    if not feeds_exist(root):
        return False
    prior = load_index_skip_state(root)
    if prior is None:
        return False
    prior_hash, prior_fp, prior_count = prior
    if prior_hash != index_hash:
        return False
    if prior_count != len(essays):
        return False
    return prior_fp == _index_fingerprint(essays)


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
            help="Bypass hash skip when index is unchanged",
        ),
    ] = None,
    validate_links: Annotated[
        bool | None,
        typer.Option(
            "--validate-links/--no-validate-links",
            help="Live HEAD/GET each essay URL",
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Errors only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logs")] = False,
) -> None:
    """Fetch index, extract essays, enrich from each page, write feeds/."""
    settings = _settings(
        repo_root=repo_root,
        min_items=min_items,
        quiet=quiet if _is_cmdline(ctx, "quiet") else None,
        verbose=verbose if _is_cmdline(ctx, "verbose") else None,
        timeout=timeout,
        retries=retries,
        validate_links=validate_links,
        enrich=enrich,
        force=force,
    )
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        if source_file is not None:
            logger.info("Reading local HTML {}", source_file)
            html = _read_source_file(source_file, max_bytes=settings.max_bytes)
        else:
            logger.info("Fetching {}", settings.source_url)
            html = fetch_html(
                settings.source_url,
                timeout=settings.timeout,
                retries=settings.retries,
                max_bytes=settings.max_bytes,
            )

        index_hash = content_sha256(html)
        essays = extract_essays(
            html,
            base_url=settings.source_url,
            min_items=settings.min_items,
        )

        if _should_skip_update(
            root=settings.repo_root,
            index_hash=index_hash,
            essays=essays,
            force=settings.force,
        ):
            logger.info(
                "Index unchanged (hash {}); skipping enrich/write",
                index_hash[:12],
            )
            if not settings.quiet:
                console.print(
                    f"[yellow]UNCHANGED[/yellow] index hash {index_hash[:12]}… "
                    f"— skipped ({len(essays)} essays)"
                )
            return

        if settings.enrich:
            logger.info("Enriching {} essays from page metadata…", len(essays))
            essays = enrich_essays(
                essays,
                timeout=settings.enrich_timeout,
                workers=settings.enrich_workers,
                retries=settings.retries,
                max_bytes=settings.max_bytes,
                quiet=settings.quiet,
            )
        if settings.validate_links:
            validate_essays_live(
                essays,
                timeout=settings.link_timeout,
                retries=settings.retries,
                workers=settings.link_workers,
                max_bytes=settings.max_bytes,
            )

        now = utc_now()
        fingerprint = _index_fingerprint(essays)
        with tqdm(total=3, desc="Render", unit="fmt", disable=settings.quiet) as bar:
            rss = render_rss(essays, built_at=now)
            bar.update(1)
            atom = render_atom(essays, built_at=now)
            bar.update(1)
            jf = render_json(
                essays,
                built_at=now,
                index_hash=index_hash,
                index_fingerprint=fingerprint,
            )
            bar.update(1)

        write_feeds(
            settings.repo_root,
            rss=rss,
            atom=atom,
            json_feed=jf,
        )
        if not settings.quiet:
            console.print(
                f"[green]UPDATED[/green] {len(essays)} essays → "
                f"[bold]{settings.repo_root / 'feeds'}[/bold]"
            )
    except FeedError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=1) from exc


@app.command("check")
def check_cmd(
    ctx: typer.Context,
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", help="Root containing feeds/"),
    ] = None,
    min_items: Annotated[int | None, typer.Option("--min-items")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Sanity-check feeds under the repo root via ``verify_feed_artifacts``."""
    settings = _settings(
        repo_root=repo_root,
        min_items=min_items,
        quiet=quiet if _is_cmdline(ctx, "quiet") else None,
        verbose=verbose if _is_cmdline(ctx, "verbose") else None,
    )
    configure_logging(verbose=settings.verbose, quiet=settings.quiet)
    try:
        verify_feed_artifacts(settings.repo_root, min_items=settings.min_items)
        if not settings.quiet:
            payload = json.loads(feed_paths(settings.repo_root)["json"].read_text(encoding="utf-8"))
            count = len(payload["items"])
            console.print(
                f"[green]VALID[/green] {count} items in [bold]{settings.repo_root / 'feeds'}[/bold]"
            )
    except FeedError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        logger.error("{}", exc)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console script entrypoint."""
    app()


if __name__ == "__main__":
    main()
