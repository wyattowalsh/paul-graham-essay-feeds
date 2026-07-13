"""RSS 2.0 pure renderer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paul_graham_essay_feeds.domain import (
    ATOM_NS,
    CHANNEL_URL,
    DC_NS,
    RSS_SPEC_URL,
    BuildContext,
    description_for,
    rfc822_utc,
)

__all__ = ["render_rss"]


def render_rss(context: BuildContext) -> bytes:
    """Render an RSS 2.0 document from ``context``.

    Omits item ``pubDate``. Includes channel and item ``category``. Emits
    ``atom:link rel="self"`` only when public URLs are configured.
    """
    # Prefer explicit prefixes so Atom's default xmlns never leaks into RSS.
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("atom", ATOM_NS)

    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")

    metadata = (
        ("title", context.feed_title),
        ("link", CHANNEL_URL),
        ("description", context.feed_description),
        ("language", "en-US"),
        ("lastBuildDate", rfc822_utc(context.build_updated_at)),
        ("category", context.category),
        ("generator", context.generator),
        ("docs", RSS_SPEC_URL),
        ("ttl", "1440"),
    )
    for tag, value in metadata:
        ET.SubElement(channel, tag).text = value

    if context.public is not None:
        ET.SubElement(
            channel,
            f"{{{ATOM_NS}}}link",
            {
                "href": context.public.rss,
                "rel": "self",
                "type": "application/rss+xml",
            },
        )

    for source_item in context.items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = source_item.title
        ET.SubElement(item, "link").text = source_item.url
        ET.SubElement(item, "description").text = description_for(source_item)
        ET.SubElement(item, f"{{{DC_NS}}}creator").text = context.author_name
        ET.SubElement(item, "category").text = context.category
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true" if source_item.is_permalink else "false"},
        ).text = source_item.stable_id

    ET.indent(root, space="  ")
    xml_body = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=False,
        short_empty_elements=True,
    )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + b"\n"
