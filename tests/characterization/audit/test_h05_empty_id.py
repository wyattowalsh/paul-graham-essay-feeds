"""H-05: blank feed item ids are rejected by verification."""

from __future__ import annotations

from paul_graham_essay_feeds.verify import EMPTY_ID, verify_feed_bytes


def test_h05_blank_guid_rejected() -> None:
    rss = b"""<?xml version="1.0"?><rss><channel>
    <item><title>T</title><link>https://paulgraham.com/a.html</link>
    <guid></guid><description>Summary text here long enough.</description></item>
    </channel></rss>"""
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>T</title><id></id><summary>Summary text here long enough.</summary>
    <link href="https://paulgraham.com/a.html"/></entry></feed>"""
    jf = b"""{"version":"https://jsonfeed.org/version/1.1","items":[
    {"id":"","url":"https://paulgraham.com/a.html","title":"T",
     "summary":"Summary text here long enough.","content_text":"Summary text here long enough."}
    ]}"""
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert not report.ok
    assert any(v.code == EMPTY_ID for v in report.violations)
