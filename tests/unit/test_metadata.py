"""Unit tests for content-root metadata extraction and summary quality."""

from __future__ import annotations

from pathlib import Path

from paul_graham_essay_feeds.metadata import (
    PageMetadata,
    extract_page_metadata,
    score_summary_quality,
)
from paul_graham_essay_feeds.model import FEED_SUMMARY_CHARS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "upstream"
PAGE_URL = "https://paulgraham.com/earn.html"


def test_clean_meta_description_wins() -> None:
    """meta description is preferred over og:description and body text."""
    html = (FIXTURES / "page-meta-description.html").read_text(encoding="utf-8")
    meta = extract_page_metadata(html, page_url=PAGE_URL)

    assert isinstance(meta, PageMetadata)
    assert meta.meta_description is not None
    assert "billionaires" in meta.meta_description
    assert meta.summary == meta.meta_description
    assert meta.summary_source == "meta_description"
    assert meta.og_description is not None
    assert "OG description" in meta.og_description
    # Summary is the clean meta path, not the OG loser or body.
    assert meta.summary is not None
    assert "OG description" not in meta.summary
    assert meta.quality_score >= 0.9
    assert "empty" not in meta.quality_flags
    assert "nav_like" not in meta.quality_flags


def test_promo_chrome_excluded_and_low_quality_when_contaminated() -> None:
    """Nav/footer/promo regions are skipped; pure promo summaries score low."""
    html = (FIXTURES / "page-promo-chrome.html").read_text(encoding="utf-8")
    meta = extract_page_metadata(html, page_url="https://paulgraham.com/relres.html")

    assert meta.summary is not None
    assert meta.summary_source == "content_paragraph"
    # Content root should be the essay paragraph, not YC promo / subscribe chrome.
    assert "Want to start a startup" not in meta.summary
    assert "Get funded by Y Combinator" not in meta.summary
    assert "Subscribe" not in meta.summary
    assert "Click here" not in meta.summary
    assert "relentlessly resourceful" in meta.summary.lower()
    assert meta.quality_score >= 0.9
    assert "nav_like" not in meta.quality_flags
    assert "subscribe" not in meta.quality_flags

    # Contaminated candidate itself is flagged low quality.
    dirty = "Want to start a startup? Subscribe now. Click here."
    score, flags = score_summary_quality(dirty)
    assert score < 0.5
    assert "nav_like" in flags or "subscribe" in flags
    assert "click_here" in flags


def test_month_year_only_published_hint_not_published_at() -> None:
    """Month+year becomes published_hint only; no day-1 published_at field."""
    html = (FIXTURES / "page-meta-description.html").read_text(encoding="utf-8")
    meta = extract_page_metadata(html, page_url=PAGE_URL)

    assert meta.published_hint == "June 2026"
    # PageMetadata deliberately omits published_at so month+year cannot invent day-1.
    assert not hasattr(meta, "published_at")
    assert "published_at" not in type(meta).model_fields


def test_replacement_char_lowers_quality_and_is_flagged() -> None:
    """U+FFFD in summary reduces quality_score and sets replacement_char flag."""
    html = (
        "<html><head><title>Broken</title>"
        '<meta name="description" '
        'content="A solid essay about startups and\ufffd encoding issues." />'
        "</head><body>"
        "<p>March 2009 Some longer body text that is not used because meta wins here.</p>"
        "</body></html>"
    )
    meta = extract_page_metadata(html, page_url=PAGE_URL)

    assert meta.summary is not None
    assert "\ufffd" in meta.summary
    assert "replacement_char" in meta.quality_flags
    assert meta.quality_score < 0.6

    score_clean, flags_clean = score_summary_quality(
        "A solid essay about startups and encoding issues in production."
    )
    score_bad, flags_bad = score_summary_quality(
        "A solid essay about startups and\ufffd encoding issues in production."
    )
    assert score_bad < score_clean
    assert "replacement_char" in flags_bad
    assert "replacement_char" not in flags_clean


def test_no_full_body_retained_summary_length_cap() -> None:
    """Summary is always ≤ FEED_SUMMARY_CHARS; full body is never stored."""
    words = " ".join(f"word{i:04d}" for i in range(500))
    assert len(words) > FEED_SUMMARY_CHARS
    html = (
        f"<html><head><title>Long</title>"
        f'<meta name="description" content="{words}" />'
        f"</head><body><p>{words}</p></body></html>"
    )
    meta = extract_page_metadata(html, page_url=PAGE_URL)

    assert meta.summary is not None
    assert len(meta.summary) <= FEED_SUMMARY_CHARS
    # Model has no content_text / body field to hold a full essay.
    assert not hasattr(meta, "content_text")
    assert "content_text" not in type(meta).model_fields
    if meta.meta_description is not None:
        assert len(meta.meta_description) <= FEED_SUMMARY_CHARS


def test_bytes_html_decoded_via_decode_html_document() -> None:
    """bytes input is decoded (ADR-004) before parsing."""
    html = (
        b"<html><head><title>Cafe</title>"
        b'<meta name="description" content="A short note about caf\xc3\xa9 culture." />'
        b"</head><body><p>June 2020 body paragraph about coffee shops and writing.</p>"
        b"</body></html>"
    )
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary is not None
    assert "café" in meta.summary or "caf" in meta.summary.lower()
    assert meta.summary_source == "meta_description"


def test_canonical_token_list_parsed_as_hint() -> None:
    """rel may be a space-separated token list containing canonical."""
    html = (FIXTURES / "page-meta-description.html").read_text(encoding="utf-8")
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    # Fixture uses rel="canonical prefetch".
    assert meta.canonical_url == "https://paulgraham.com/earn.html"


def test_og_description_fallback_when_no_meta() -> None:
    html = """
    <html><head>
    <title>T</title>
    <meta property="og:description" content="Open graph summary of the essay topic here." />
    </head><body><p>Body fallback that should not win over og description text.</p></body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary_source == "og_description"
    assert meta.summary is not None
    assert "Open graph summary" in meta.summary


def test_empty_summary_scores_zero() -> None:
    score, flags = score_summary_quality(None)
    assert score == 0.0
    assert flags == ("empty",)
    score2, flags2 = score_summary_quality("   ")
    assert score2 == 0.0
    assert "empty" in flags2


def test_too_short_flag() -> None:
    score, flags = score_summary_quality("Too short.")
    assert score < 1.0
    assert "too_short" in flags


def test_twitter_description_when_no_meta_or_og() -> None:
    """twitter:description is used when meta and og descriptions are absent."""
    html = """
    <html><head>
    <title>T</title>
    <meta name="twitter:description"
          content="Twitter card summary about founders building products." />
    </head><body><p>Body text that loses to twitter description for the feed.</p></body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary_source == "twitter_description"
    assert meta.summary is not None
    assert "Twitter card summary" in meta.summary


def test_content_paragraph_from_loose_body_without_p_tags() -> None:
    """PG-style bare text (no <p>) becomes content_paragraph after promo skip."""
    html = """
    <html><head><title>Bare Essay Title</title></head>
    <body>
    Want to start a startup? Get funded by Y Combinator.
    Bare Essay Title
    The real essay opens with a long enough paragraph about resourcefulness
    and the habits that separate good founders from hapless ones in practice.
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary_source == "content_paragraph"
    assert meta.summary is not None
    assert "resourcefulness" in meta.summary.lower()
    assert "Want to start a startup" not in meta.summary


def test_short_paragraphs_and_nav_crumbs_skipped() -> None:
    """Short <p> crumbs and ultra-short nav tokens are not used as summary."""
    html = """
    <html><head><title>X</title></head>
    <body>
      <p>Home</p>
      <p>About</p>
      <p>Short</p>
      <p>This paragraph is long enough to qualify as a content paragraph for the feed.</p>
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary_source == "content_paragraph"
    assert meta.summary is not None
    assert "long enough to qualify" in meta.summary
    assert meta.summary.strip() not in {"Home", "About", "Short"}


def test_role_and_class_chrome_regions_skipped() -> None:
    """role=navigation / banner / chrome class regions are excluded from body."""
    html = """
    <html><head><title>Roles</title></head>
    <body>
      <div role="navigation">Home | About | Essays | RSS</div>
      <div role="banner">Site header chrome</div>
      <div role="contentinfo">Footer info chrome</div>
      <div class="sidebar-promo">Subscribe to our newsletter today please.</div>
      <p>Actual essay body paragraph about Lisp and startups that is long enough.</p>
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary is not None
    assert "Lisp and startups" in meta.summary
    assert "Subscribe" not in meta.summary
    assert "Home | About" not in meta.summary


def test_script_style_skipped_and_br_inside_paragraph() -> None:
    """script/style text is ignored; <br> inside <p> becomes whitespace."""
    html = """
    <html><head><title>S</title>
    <script>var promo = "Want to start a startup Subscribe now";</script>
    <style>.nav { display: none }</style>
    </head>
    <body>
      <p>First line of the essay<br>continues after a break with enough characters
      to pass the minimum paragraph length threshold for content selection.</p>
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary_source == "content_paragraph"
    assert meta.summary is not None
    assert "continues after a break" in meta.summary
    assert "var promo" not in (meta.summary or "")


def test_nested_chrome_and_void_chrome_attrs() -> None:
    """Nested chrome depth and void chrome-tagged elements do not leak text."""
    html = """
    <html><head><title>N</title></head>
    <body>
      <div class="promo">
        Outer promo
        <div class="newsletter">Inner newsletter subscribe text</div>
        <img class="promo-banner" src="x.gif" alt="promo" />
      </div>
      <p>Non-chrome content paragraph that is sufficiently long for extraction here.</p>
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary is not None
    assert "Non-chrome content" in meta.summary
    assert "Outer promo" not in meta.summary
    assert "newsletter" not in meta.summary.lower()


def test_relative_canonical_resolved_against_page_url() -> None:
    html = """
    <html><head>
      <title>Rel</title>
      <link rel="canonical" href="rel.html" />
      <meta name="description"
            content="Relative canonical page with a usable meta description here." />
    </head><body></body></html>
    """
    meta = extract_page_metadata(html, page_url="https://paulgraham.com/dir/page.html")
    assert meta.canonical_url == "https://paulgraham.com/dir/rel.html"


def test_empty_canonical_and_empty_meta_ignored() -> None:
    html = """
    <html><head>
      <title>   </title>
      <link rel="canonical" href="  " />
      <meta name="description" content="   " />
      <meta property="og:title" content="" />
    </head>
    <body>
      <p>Only body content is available when meta fields are blank or whitespace.</p>
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.canonical_url is None
    assert meta.meta_description is None
    assert meta.page_title is None
    assert meta.summary_source == "content_paragraph"


def test_published_hint_absent_when_no_month_year() -> None:
    html = """
    <html><head>
      <title>No Date</title>
      <meta name="description" content="An essay without any month year marker at all." />
    </head><body><p>Just words about startups without a calendar month year pair.</p></body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.published_hint is None


def test_all_promo_loose_body_still_yields_tail() -> None:
    """When loose body is promo-heavy, trailing non-promo text can still surface."""
    html = """
    <html><head><title>Promo Heavy</title></head>
    <body>
    Want to start a startup? Get funded by Y Combinator.
    Subscribe to the list. Click here for more.
    Eventually a real sentence appears about writing and thinking carefully about work.
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    # Prefer any non-empty summary for scoring rather than crashing.
    assert meta.summary is not None
    assert meta.summary_source == "content_paragraph"


def test_loose_body_scan_bound_and_title_prefix_strip() -> None:
    """Loose body strips page title prefix and caps scan material length."""
    long_tail = "word " * 600  # well over _MAX_SCAN_CHARS (2000)
    html = f"""
    <html><head><title>Long Loose</title></head>
    <body>
    Long Loose - {long_tail}
    </body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.summary is not None
    assert meta.summary_source == "content_paragraph"
    assert len(meta.summary) <= FEED_SUMMARY_CHARS
    # Title prefix should not dominate the summary start.
    assert not meta.summary.startswith("Long Loose -")


def test_score_summary_quality_stacks_all_penalties() -> None:
    dirty = "Subscribe and Click here. Want to start a startup?\ufffd"
    score, flags = score_summary_quality(dirty)
    assert score == 0.0
    assert "subscribe" in flags
    assert "click_here" in flags
    assert "nav_like" in flags
    assert "replacement_char" in flags


def test_score_summary_quality_too_short_flag() -> None:
    score, flags = score_summary_quality("Too short")
    assert "too_short" in flags
    assert score < 1.0


def test_duplicate_meta_name_keeps_first() -> None:
    html = """
    <html><head>
      <meta name="description" content="First description wins and is long enough overall." />
      <meta name="description" content="Second description must not replace the first one." />
    </head><body></body></html>
    """
    meta = extract_page_metadata(html, page_url=PAGE_URL)
    assert meta.meta_description is not None
    assert "First description" in meta.meta_description
    assert "Second description" not in meta.meta_description


def test_page_metadata_summary_validator_caps_and_empty() -> None:
    """PageMetadata.summary validator truncates and maps empty truncations to None."""
    long_summary = "word " * 200
    meta = PageMetadata(summary=long_summary)
    assert meta.summary is not None
    assert len(meta.summary) <= FEED_SUMMARY_CHARS

    emptyish = PageMetadata(summary="   ")
    assert emptyish.summary is None
