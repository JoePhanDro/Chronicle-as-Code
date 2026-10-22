"""Markdown in event details. One renderer for the detail page and the dashboard, sanitized before it is shown.

Raw HTML in the source is escaped, and the sanitizer keeps only the tags below. Images are dropped on purpose:
an external image would load from another server each time the page is opened.
"""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])

ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p",
    "pre", "s", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
}


def render_markdown(text: str) -> str:
    """Sanitized HTML for a Markdown text. Links open without giving the new page access to this one."""
    return nh3.clean(_markdown.render(text or ""), tags=ALLOWED_TAGS, link_rel="noopener noreferrer")


def markdown_filter(text: str | None) -> Markup:
    """Jinja filter: `{{ event.details | markdown }}`. The output is already sanitized, so it is not escaped again."""
    return Markup(render_markdown(text or ""))
