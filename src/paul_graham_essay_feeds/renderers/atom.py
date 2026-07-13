"""Atom 1.0 pure renderer (RFC 4287)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paul_graham_essay_feeds.domain import (
    ATOM_NS,
    BuildContext,
    description_for,
    rfc3339_utc,
)

__all__ = ["render_atom"]


def render_atom(context: BuildContext) -> bytes:
    """Render an Atom 1.0 feed document.

    Entry ``updated`` maps to observation ``last_changed_at``. ``published`` is
    never emitted. Feed-level author satisfies entry author inheritance.
    """
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    feed.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    ET.SubElement(feed, f"{{{ATOM_NS}}}title").text = context.feed_title
    ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = context.feed_id
    ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = rfc3339_utc(context.build_updated_at)
    ET.SubElement(feed, f"{{{ATOM_NS}}}subtitle").text = context.feed_description

    author = ET.SubElement(feed, f"{{{ATOM_NS}}}author")
    ET.SubElement(author, f"{{{ATOM_NS}}}name").text = context.author_name
    ET.SubElement(author, f"{{{ATOM_NS}}}uri").text = context.author_url

    ET.SubElement(
        feed,
        f"{{{ATOM_NS}}}link",
        {
            "rel": "alternate",
            "type": "text/html",
            "href": context.home_page_url,
        },
    )
    if context.public is not None:
        ET.SubElement(
            feed,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "self",
                "type": "application/atom+xml",
                "href": context.public.atom,
            },
        )

    ET.SubElement(feed, f"{{{ATOM_NS}}}generator").text = context.generator

    for source_item in context.items:
        entry = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")
        ET.SubElement(entry, f"{{{ATOM_NS}}}title").text = source_item.title
        ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = source_item.stable_id
        ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = rfc3339_utc(
            source_item.last_changed_at
        )
        ET.SubElement(
            entry,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "alternate",
                "type": "text/html",
                "href": source_item.url,
            },
        )
        ET.SubElement(entry, f"{{{ATOM_NS}}}summary").text = description_for(source_item)

    ET.indent(feed, space="  ")
    xml_body = ET.tostring(
        feed,
        encoding="utf-8",
        xml_declaration=False,
        short_empty_elements=True,
    )
    text = xml_body.decode("utf-8")
    # Collapse ElementTree's ns0 default into a clean Atom default xmlns.
    text = (
        text.replace("ns0:", "")
        .replace("xmlns:ns0=", "xmlns=")
        .replace(f'xmlns:ns0="{ATOM_NS}"', f'xmlns="{ATOM_NS}"')
    )
    if f'xmlns="{ATOM_NS}"' not in text and text.lstrip().startswith("<feed"):
        text = text.replace("<feed", f'<feed xmlns="{ATOM_NS}"', 1)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + text + "\n").encode("utf-8")
