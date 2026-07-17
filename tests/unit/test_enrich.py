"""Unit tests for page metadata enrichment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import respx

from paul_graham_essay_feeds.enrich import (
    _MAX_CONTENT_CHARS,
    _SUMMARY_CHARS,
    enrich_essays,
    parse_page_metadata,
)
from paul_graham_essay_feeds.model import Essay, content_sha256

SAMPLE_HTML = """
<html><head>
<title>How to Earn a Billion Dollars</title>
<meta name="description" content="A talk about how people become billionaires." />
<meta property="og:image" content="https://paulgraham.com/img.png" />
<meta name="keywords" content="startups, wealth" />
<link rel="canonical" href="https://paulgraham.com/earn.html" />
</head><body>
How to Earn a Billion Dollars
June 2026
(This is based on a talk I gave at the Oxford Union.)
Since this is apparently the future prime ministers' club, I'm going to tell
you about something it would be good if more politicians understood.
</body></html>
"""


def _essay(
    *,
    position: int = 1,
    title: str = "How to Earn a Billion Dollars",
    path: str = "earn.html",
) -> Essay:
    url = f"https://paulgraham.com/{path}"
    return Essay(
        position=position,
        title=title,
        url=url,
        stable_id=url,
        is_permalink=True,
    )


def test_parse_page_metadata_extracts_fields() -> None:
    """Meta summary + date; full body never returned as content_text (RV-002)."""
    meta = parse_page_metadata(SAMPLE_HTML, page_url="https://paulgraham.com/earn.html")
    assert meta["page_title"] == "How to Earn a Billion Dollars"
    assert meta["summary"] and "billionaires" in meta["summary"]
    assert meta["image_url"] == "https://paulgraham.com/img.png"
    assert meta["keywords"] == "startups, wealth"
    assert meta["canonical_url"] == "https://paulgraham.com/earn.html"
    assert meta["published_hint"] == "June 2026"
    assert meta["published_at"] is None
    assert meta["content_text"] is None


def test_parse_page_metadata_body_fallback_summary() -> None:
    html = (
        "<html><head><title>X</title></head>"
        "<body>Hello world from the essay body text.</body></html>"
    )
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/x.html")
    assert meta["summary"] and "Hello world" in meta["summary"]
    assert meta["content_text"] is None


def test_parse_page_metadata_long_meta_description_capped() -> None:
    """RV-S-001: meta/OG description is capped like body-derived summary."""
    long = "word " * 200
    html = (
        f"<html><head><title>T</title>"
        f'<meta name="description" content="{long}"/>'
        f"</head><body>June 2026 ignored body</body></html>"
    )
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/x.html")
    summary = meta["summary"] or ""
    assert len(summary) <= _SUMMARY_CHARS + 1
    assert meta["content_text"] is None


def test_parse_page_metadata_strips_script_and_style() -> None:
    html = """
    <html><head>
    <title>Clean</title>
    <style>.secret{color:red}</style>
    <script>var leak = "should-not-appear";</script>
    </head><body>
    Visible essay prose here.
    <noscript>noscript noise</noscript>
    </body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/clean.html")
    # Body text is not stored; ensure scripts did not leak into summary.
    summary = meta["summary"] or ""
    assert "Visible essay prose" in summary
    assert "should-not-appear" not in summary
    assert "color:red" not in summary
    assert "noscript noise" not in summary
    assert "leak" not in summary
    assert meta["content_text"] is None


def test_parse_page_metadata_truncates_overlong_body() -> None:
    """Long page body is truncated for summary; content_text stays None."""
    words = " ".join(f"w{i:04d}" for i in range(800))
    assert len(words) > _MAX_CONTENT_CHARS
    html = f"<html><head><title>Long</title></head><body>{words}</body></html>"
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/long.html")
    assert meta["content_text"] is None
    summary = meta["summary"] or ""
    assert "w0000" in summary
    assert summary.endswith("…")
    assert len(summary) <= _SUMMARY_CHARS + 1


def test_parse_page_metadata_body_summary_truncates() -> None:
    # Mid-size body: longer than summary budget, shorter than content cap.
    n = max(50, (_SUMMARY_CHARS // 6) + 10)
    words = " ".join(f"s{i:04d}" for i in range(n))
    assert len(words) > _SUMMARY_CHARS
    assert len(words) < _MAX_CONTENT_CHARS
    html = f"<html><head><title>T</title></head><body>{words}</body></html>"
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/sum.html")
    summary = meta["summary"] or ""
    assert summary.endswith("…")
    assert len(summary) <= _SUMMARY_CHARS + 1
    assert meta["content_text"] is None


def test_parse_page_metadata_prefers_og_description() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta name="description" content="generic description" />
    <meta property="og:description" content="og wins here" />
    <meta name="twitter:description" content="twitter loses" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["summary"] == "og wins here"


def test_parse_page_metadata_twitter_description_fallback() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta name="twitter:description" content="twitter desc fallback" />
    </head><body>body only</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["summary"] == "twitter desc fallback"


def test_parse_page_metadata_twitter_image_fallback() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta name="twitter:image" content="https://paulgraham.com/tw.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["image_url"] == "https://paulgraham.com/tw.png"


def test_parse_page_metadata_prefers_og_image() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:image" content="https://paulgraham.com/og.png" />
    <meta name="twitter:image" content="https://paulgraham.com/tw.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["image_url"] == "https://paulgraham.com/og.png"


def test_parse_page_metadata_relative_og_image_urljoin() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:image" content="/img/rel.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/essays/t.html")
    assert meta["image_url"] == "https://paulgraham.com/img/rel.png"


def test_parse_page_metadata_http_image_rejected() -> None:
    """http: og/twitter image fails the https gate → image_url None."""
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:image" content="http://paulgraham.com/img.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["image_url"] is None


def test_parse_page_metadata_off_host_image_rejected() -> None:
    """Allowlisted https only; off-host image → image_url None."""
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:image" content="https://evil.example/img.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["image_url"] is None


def test_parse_page_metadata_allowlisted_https_image_ok() -> None:
    """Allowlisted https image (incl. turbify CDN) is kept."""
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:image"
          content="https://sep.turbifycdn.com/ty/cdn/paulgraham/img.png" />
    </head><body>body</body></html>
    """
    meta = parse_page_metadata(html, page_url="https://paulgraham.com/t.html")
    assert meta["image_url"] == "https://sep.turbifycdn.com/ty/cdn/paulgraham/img.png"


@respx.mock
def test_enrich_essays_fetches_and_merges() -> None:
    respx.get("https://paulgraham.com/earn.html").mock(
        return_value=httpx.Response(200, text=SAMPLE_HTML)
    )
    base = _essay()
    out = enrich_essays([base], workers=2, retries=0, quiet=True)
    assert len(out) == 1
    e = out[0]
    assert e.summary and "billionaires" in e.summary
    assert e.content_text is None
    assert e.published_hint == "June 2026"
    assert e.published_at is None
    assert e.image_url == "https://paulgraham.com/img.png"
    assert "billionaires" in e.feed_summary()
    assert e.content_hash == content_sha256(SAMPLE_HTML)


def test_enrich_essays_empty_list_early_return() -> None:
    assert enrich_essays([], workers=1, retries=0, quiet=True) == []


@respx.mock
def test_enrich_404_soft_fails() -> None:
    respx.get("https://paulgraham.com/missing.html").mock(
        return_value=httpx.Response(404, text="not found")
    )
    base = _essay(path="missing.html", title="Missing")
    out = enrich_essays([base], workers=1, retries=0, quiet=True)
    assert len(out) == 1
    assert out[0] == base
    assert out[0].summary is None


@respx.mock
def test_enrich_oversize_body_soft_fails() -> None:
    respx.get("https://paulgraham.com/huge.html").mock(
        return_value=httpx.Response(200, text="x" * 200)
    )
    base = _essay(path="huge.html", title="Huge")
    out = enrich_essays([base], workers=1, retries=0, max_bytes=50, quiet=True)
    assert len(out) == 1
    assert out[0] == base
    assert out[0].summary is None
    assert out[0].content_text is None


@respx.mock
def test_enrich_off_host_redirect_soft_fails() -> None:
    """Open-redirect hop off ALLOWED_HOSTS → soft-fail; original essay kept."""
    respx.get("https://paulgraham.com/earn.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    base = _essay()
    out = enrich_essays([base], workers=1, retries=0, quiet=True)
    assert len(out) == 1
    assert out[0] == base
    assert out[0].summary is None


def test_enrich_worker_exception_keeps_essay() -> None:
    """fut.result raising a non-FeedError soft-fails and keeps the original essay."""
    base = _essay()
    boom_fut = MagicMock()
    boom_fut.result.side_effect = RuntimeError("worker boom")

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=None)

    pool = MagicMock()
    pool.submit.return_value = boom_fut

    with (
        patch("paul_graham_essay_feeds.enrich.httpx.Client", return_value=client),
        patch("paul_graham_essay_feeds.enrich.ThreadPoolExecutor") as pool_cls,
        patch(
            "paul_graham_essay_feeds.enrich.as_completed",
            return_value=[boom_fut],
        ),
        patch(
            "paul_graham_essay_feeds.enrich.tqdm",
            side_effect=lambda it, **_kw: it,
        ),
    ):
        pool_cls.return_value.__enter__.return_value = pool
        pool_cls.return_value.__exit__.return_value = None
        out = enrich_essays([base], workers=1, retries=0, quiet=True)

    assert out == [base]
    boom_fut.result.assert_called()
