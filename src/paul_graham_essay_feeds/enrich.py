"""Scrape per-essay page metadata for short feed summaries (not full bodies)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger
from tqdm import tqdm

from paul_graham_essay_feeds.fetch import hop_safe_get, run_with_retry
from paul_graham_essay_feeds.model import (
    ALLOWED_HOSTS,
    FEED_SUMMARY_CHARS,
    Essay,
    FeedError,
    content_sha256,
    normalize_text,
    truncate_text,
    user_agent,
)

_USER_AGENT = user_agent()
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
    re.I,
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg"})
# Parse budget for body text used only to build a short summary + date hint.
# Full essay text is never persisted on Essay (feeds use summary only).
_MAX_CONTENT_CHARS = 600
# Alias the feed-wide summary cap (single source of truth in model).
_SUMMARY_CHARS = FEED_SUMMARY_CHARS


class _MetaBodyParser(HTMLParser):
    """Collect title, meta tags, and visible body text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.body_parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            name = (amap.get("name") or amap.get("property") or "").lower()
            content = amap.get("content", "").strip()
            if name and content:
                self.meta[name] = content
            return
        if tag == "link" and amap.get("rel", "").lower() == "canonical":
            href = amap.get("href", "").strip()
            if href:
                self.meta["canonical"] = href

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.body_parts.append(text)


def parse_page_metadata(html: str, *, page_url: str) -> dict[str, str | None]:
    """Extract title, description-ish fields, image, and body text from HTML."""
    parser = _MetaBodyParser()
    parser.feed(html)
    parser.close()

    page_title = normalize_text(" ".join(parser.title_parts)) or None
    meta = parser.meta
    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
        or None
    )
    if description:
        description = normalize_text(description)

    image = meta.get("og:image") or meta.get("twitter:image") or None
    if image:
        image = urljoin(page_url, image.strip())
        parts = urlsplit(image)
        host = (parts.hostname or "").lower()
        if host == "www.paulgraham.com":
            host = "paulgraham.com"
        if parts.scheme != "https" or host not in ALLOWED_HOSTS:
            image = None

    keywords = meta.get("keywords") or meta.get("news_keywords") or None
    if keywords:
        keywords = normalize_text(keywords) or None

    canonical = meta.get("canonical")
    if canonical:
        canonical = urljoin(page_url, canonical.strip())

    body = normalize_text(" ".join(parser.body_parts))
    # Drop nav chrome that often prefixes PG pages ("Want to start a startup?...").
    if page_title and body.startswith(page_title):
        body = body[len(page_title) :].lstrip(" -|:")
    if len(body) > _MAX_CONTENT_CHARS:
        body = body[:_MAX_CONTENT_CHARS].rsplit(" ", 1)[0] + "…"

    published_hint: str | None = None
    # Prefer a month+year near the start of the body (common on PG essays).
    # Hint only — never invent a day-1 published_at from month+year.
    head = body[:200] if body else ""
    m = _MONTH_YEAR.search(head)
    if m:
        published_hint = f"{m.group(1).title()} {m.group(2)}"

    summary = description
    if not summary and body:
        summary = body
    # Cap meta and body-derived summaries (RV-S-001); single FEED_SUMMARY_CHARS budget.
    if summary:
        summary = truncate_text(summary, FEED_SUMMARY_CHARS)

    # Never return long body text — feeds use summary only (RV-002).
    return {
        "page_title": page_title,
        "summary": summary,
        "content_text": None,
        "image_url": image,
        "keywords": keywords,
        "canonical_url": canonical,
        "published_hint": published_hint,
        "published_at": None,
    }


def _fetch_page(client: httpx.Client, url: str, *, max_bytes: int) -> str:
    """GET essay page with hop-safe redirects (essay host allowlist)."""
    response = hop_safe_get(
        client,
        url,
        allowed_hosts=ALLOWED_HOSTS,
        max_bytes=max_bytes,
    )
    response.raise_for_status()
    return response.text


def _enrich_one(
    client: httpx.Client,
    essay: Essay,
    *,
    max_bytes: int,
    attempts: int,
) -> Essay:
    """Scrape one page; soft-fail to the original essay on network/parse errors."""

    def _load() -> str:
        return _fetch_page(client, essay.url, max_bytes=max_bytes)

    try:
        html = run_with_retry(_load, attempts=attempts, what=f"enrich {essay.url}")
    except FeedError as exc:
        logger.warning("Enrichment failed for {}: {}", essay.url, exc)
        return essay

    page_hash = content_sha256(html)
    meta = parse_page_metadata(html, page_url=essay.url)

    return essay.model_copy(
        update={
            "page_title": meta.get("page_title"),
            "summary": meta.get("summary"),
            # Explicitly clear any prior body text; not emitted in feeds.
            "content_text": None,
            "image_url": meta.get("image_url"),
            "keywords": meta.get("keywords"),
            "canonical_url": meta.get("canonical_url") or essay.url,
            "published_hint": meta.get("published_hint"),
            "published_at": None,
            "content_hash": page_hash,
        }
    )


def enrich_essays(
    essays: list[Essay],
    *,
    timeout: float = 15.0,
    workers: int = 4,
    retries: int = 2,
    max_bytes: int = 2 * 1024 * 1024,
    quiet: bool = False,
) -> list[Essay]:
    """Fetch each essay page and attach short summaries (order preserved).

    Network-heavy: one GET per essay by default. Use ``--no-enrich`` for
    index-only generation. Does not store full essay bodies on ``Essay``.
    """
    if not essays:
        return essays
    attempts = max(1, retries + 1)
    out: dict[int, Essay] = {}
    with (
        httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain,*/*;q=0.8"},
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {
            pool.submit(
                _enrich_one,
                client,
                e,
                max_bytes=max_bytes,
                attempts=attempts,
            ): e.position
            for e in essays
        }
        for fut in tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Enrich pages",
            unit="page",
            disable=quiet,
        ):
            pos = futures[fut]
            try:
                out[pos] = fut.result()
            except Exception as exc:
                logger.warning("Enrich worker failed for position {}: {}", pos, exc)
                out[pos] = next(e for e in essays if e.position == pos)

    enriched = [out[e.position] for e in essays]
    ok = sum(1 for e in enriched if e.summary)
    logger.info("Enriched {}/{} essays with page metadata", ok, len(enriched))
    return enriched
