"""Markdown in event details. One renderer for the detail page and the dashboard, sanitized before it is shown.

Raw HTML in the source is escaped, and the sanitizer keeps only the tags below. Images are only shown when they point to
an evidence file of the same event. Any other image source (a web address, a path outside the event) shows its alt text.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup, escape

_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])

ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "img", "li", "ol", "p",
    "pre", "s", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt"}, "code": {"class"}}


def _image_url(source: str, images: Mapping[str, str]) -> str | None:
    """The URL of an evidence file of this event, or None. Addresses with a scheme or a path are never accepted."""
    if ":" in source or source.startswith(("/", "\\")):
        return None
    return images.get(PurePosixPath(source).name)


def _render_image(tokens, index, options, env) -> str:
    token = tokens[index]
    alt = _markdown.renderer.renderInlineAsText(token.children, options, env)
    url = _image_url(token.attrGet("src") or "", env.get("images", {}))
    if url is None:
        return str(escape(alt))
    return f'<img src="{escape(url)}" alt="{escape(alt)}">'


_markdown.renderer.rules["image"] = _render_image


def _filter_attribute(tag: str, attribute: str, value: str) -> str | None:
    # Keeps the language class of code blocks (`language-mermaid`), drops everything else.
    if tag == "code" and attribute == "class":
        return value if value.startswith("language-") else None
    return value


def render_markdown(text: str | None, images: Mapping[str, str] | None = None) -> str:
    """Sanitized HTML for a Markdown text.

    `images` maps the file name of each evidence item to the URL it is served from. Links open without giving the
    new page access to this one.
    """
    html = _markdown.render(text or "", {"images": images or {}})
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_filter_attribute,
        link_rel="noopener noreferrer",
    )


def markdown_filter(text: str | None, images: Mapping[str, str] | None = None) -> Markup:
    """Jinja filter: `{{ text | markdown(images) }}`. The output is already sanitized, so it is not escaped again."""
    return Markup(render_markdown(text, images))
