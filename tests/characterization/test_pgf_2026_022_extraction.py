"""PGF-2026-022: reject translation/nav/promo chrome; prefer essay paragraph."""

from __future__ import annotations

import pytest

from paul_graham_essay_feeds.enrich import extract_page_metadata
from paul_graham_essay_feeds.verify import (
    SEMANTIC_SUMMARY,
    semantic_summary_violations,
    summary_passes_semantic_gate,
)

_PAGE = "https://paulgraham.com/{slug}.html"


def _pg_shell(
    *,
    title: str,
    date: str,
    opening: str,
    chrome_labels: tuple[str, ...],
    book_promo: str | None = None,
) -> str:
    """Minimized paulgraham.com table layout: YC banner, one ``<p>``, link chrome."""
    links = []
    for i, label in enumerate(chrome_labels):
        links.append(
            "<tr valign='top'>"
            "<td><img src='mark.gif' width='6' height='14' /></td>"
            f"<td><font size='2'><a href='https://example.com/t{i}'>{label}</a></font></td>"
            "</tr>"
        )
    if book_promo:
        links.append(f"<tr><td colspan='2'><font size='2'>{book_promo}</font></td></tr>")
    chrome = "\n".join(links)
    return f"""<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body bgcolor="#ffffff">
<table border="0"><tr valign="top"><td>
<table width="100%" cellspacing="0">
<tr><td bgcolor="#ff9922">
<b>Want to start a startup?</b> Get funded by
<a href="http://ycombinator.com/apply.html">Y Combinator</a>.
</td></tr>
</table>
<p>
{date}<br /><br />
{opening}
 Later in the same paragraph: if you want to start a startup one day,
 ignore the banner and the link list at the bottom of the page.
</p>
<table border="0" cellspacing="0" cellpadding="0" width="435">
{chrome}
</table>
</td></tr></table>
</body></html>
"""


# Known-bad catalog summaries at SHA c136c497 (must fail the semantic gate).
_CASES: tuple[dict[str, object], ...] = (
    {
        "title": "Before the Startup",
        "slug": "before",
        "date": "October 2014",
        "opening": (
            "(This essay is derived from a guest lecture in Sam Altman's startup "
            "class at Stanford.) One of the advantages of having kids is that when "
            "you have to give advice, you can ask yourself what you would tell them."
        ),
        "chrome": ("Arabic Translation",),
        "bad": "Arabic Translation",
        "must_include": "advantages of having kids",
    },
    {
        "title": "Organic Startup Ideas",
        "slug": "organic",
        "date": "April 2010",
        "opening": (
            "The best way to come up with startup ideas is to ask yourself the "
            "question: what do you wish someone would make for you?"
        ),
        "chrome": (),
        "bad": "? Get funded by Y Combinator .",
        "must_include": "best way to come up with startup ideas",
    },
    {
        "title": "Why to Not Not Start a Startup",
        "slug": "notnot",
        "date": "March 2007",
        "opening": (
            "(This essay is derived from talks at the 2007 Startup School.) "
            "We've now been doing Y Combinator long enough to have some data "
            "about success rates."
        ),
        "chrome": ("Russian Translation", "Japanese Translation", "Korean Translation"),
        "bad": "Russian Translation Japanese Translation Korean Translation",
        "must_include": "doing Y Combinator long enough",
    },
    {
        "title": "The 18 Mistakes That Kill Startups",
        "slug": "startupmistakes",
        "date": "October 2006",
        "opening": (
            "In the Q & A period after a recent talk, someone asked what made "
            "startups fail. After standing there gaping for a few seconds I "
            "realized this was kind of a trick question."
        ),
        "chrome": (
            "Japanese Translation",
            "Spanish Translation",
            "Romanian Translation",
            "Chinese Translation",
            "Arabic Translation",
        ),
        "bad": (
            "Japanese Translation Spanish Translation Romanian Translation "
            "Chinese Translation Arabic Translation"
        ),
        "must_include": "what made startups fail",
    },
    {
        "title": "Ideas for Startups",
        "slug": "ideas",
        "date": "October 2005",
        "opening": (
            "(This essay is derived from a talk at the 2005 Startup School.) "
            "How do you get good ideas for startups? That's probably the number "
            "one question people ask me."
        ),
        "chrome": (
            "One Specific Idea",
            "Romanian Translation",
            "Japanese Translation",
            "Traditional Chinese Translation",
            "Russian Translation",
            "Arabic Translation",
        ),
        "bad": (
            "One Specific Idea Romanian Translation Japanese Translation "
            "Traditional Chinese Translation Russian Translation Arabic Translation"
        ),
        "must_include": "good ideas for startups",
    },
    {
        "title": "How to Start a Startup",
        "slug": "start",
        "date": "March 2005",
        "opening": (
            "(This essay is derived from a talk at the Harvard Computer Society.) "
            "You need three things to create a successful startup: to start with "
            "good people, to make something customers actually want, and to spend "
            "as little money as possible."
        ),
        "chrome": (
            "Domain Name Search",
            "Turkish Translation",
            "Hebrew Translation",
            "Russian Translation",
            "Chinese Translation",
            "French Translation",
            "Japanese Translation",
            "Arabic Translation",
        ),
        "bad": (
            "Domain Name Search Turkish Translation Hebrew Translation Russian "
            "Translation Chinese Translation French Translation Japanese "
            "Translation Arabic Translation"
        ),
        "must_include": "three things to create a successful startup",
    },
    {
        "title": "How to Make Wealth",
        "slug": "wealth",
        "date": "May 2004",
        "opening": (
            "(This essay was originally published in Hackers & Painters.) "
            "If you wanted to get rich, how would you do it? I think your best "
            "bet would be to start or join a startup."
        ),
        "chrome": ("Russian Translation", "Arabic Translation", "Spanish Translation"),
        "book_promo": "You'll find this essay and 14 others in Hackers & Painters .",
        "bad": (
            "Russian Translation Arabic Translation Spanish Translation "
            "You'll find this essay and 14 others in Hackers & Painters ."
        ),
        "must_include": "If you wanted to get rich",
    },
)


def _html_for(case: dict[str, object]) -> str:
    chrome = case["chrome"]
    assert isinstance(chrome, tuple)
    book = case.get("book_promo")
    return _pg_shell(
        title=str(case["title"]),
        date=str(case["date"]),
        opening=str(case["opening"]),
        chrome_labels=tuple(str(label) for label in chrome),
        book_promo=str(book) if isinstance(book, str) else None,
    )


@pytest.mark.characterization
@pytest.mark.parametrize("case", _CASES, ids=[str(c["slug"]) for c in _CASES])
def test_extracts_essay_paragraph_not_chrome(case: dict[str, object]) -> None:
    html = _html_for(case)
    url = _PAGE.format(slug=case["slug"])
    meta = extract_page_metadata(html, page_url=url)
    summary = meta.summary or ""
    must_include = str(case["must_include"])
    bad = str(case["bad"])

    assert meta.summary_source == "content_paragraph"
    assert must_include.lower() in summary.lower()
    assert "Arabic Translation" not in summary
    assert "Domain Name Search" not in summary
    assert "Get funded by Y Combinator" not in summary
    assert "You'll find this essay and 14 others" not in summary
    assert summary != bad
    assert meta.quality_score >= 0.6
    assert "translation_menu" not in meta.quality_flags
    assert "promo" not in meta.quality_flags
    assert "domain_search" not in meta.quality_flags
    assert "book_promo" not in meta.quality_flags
    assert summary_passes_semantic_gate(
        summary,
        score=meta.quality_score,
        flags=meta.quality_flags,
    )


@pytest.mark.characterization
@pytest.mark.parametrize("case", _CASES, ids=[str(c["slug"]) for c in _CASES])
def test_known_bad_summaries_fail_semantic_gate(case: dict[str, object]) -> None:
    bad = str(case["bad"])
    assert summary_passes_semantic_gate(bad) is False
    violations = semantic_summary_violations(bad)
    assert violations
    assert all(item.code == SEMANTIC_SUMMARY for item in violations)
