"""Static view of a chronicle folder: one HTML page per topic and an index (ADR 0009, issue #92).

Each page is self-contained, like the report from the database (ADR 0006): the stylesheet is inline and the
evidence is embedded as data URIs. So a page can be opened or sent on its own, without a server. Nothing is
read from or written to the database. The pages are marked as snapshots, and they hold confidential content.

The output is reproducible: the same folder gives the same bytes, so a diff shows what really changed.
"""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

from .check import load_folder
from .evidence import Blob
from .loader import TimelineError
from .models import Timeline
from .renderer import render_dashboard

STYLESHEET = Path(__file__).parent / "static" / "app.css"


class BuildError(TimelineError):
    """The site cannot be built, for example because the stylesheet has not been generated yet."""


def build_site(data_dir: Path, out_dir: Path) -> list[Path]:
    """Write one page per topic and an index.html into out_dir. Returns the written files.

    The folder is checked first (see check.py). If anything is wrong, nothing is written.
    """
    if not STYLESHEET.is_file():
        raise BuildError("Stylesheet fehlt: 'npm run build:css' ausführen, dann erneut bauen")

    items = load_folder(data_dir)
    timelines = sorted((timeline for timeline, _ in items), key=lambda timeline: timeline.topic.key)
    blobs: dict[str, Blob] = {}
    for _, found in items:
        blobs.update(found)
    events = {event.key: event for timeline in timelines for event in timeline.events}
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for timeline in timelines:
        # The page shows the topic's own events and the ones it refers to (refs), as the report does.
        view = Timeline(
            topic=timeline.topic,
            events=[*timeline.events, *(events[key] for key in dict.fromkeys(timeline.refs))],
        )
        page = render_dashboard(view, inline_css=stylesheet, src_for=lambda item: _data_uri(item.file, blobs))
        path = out_dir / f"{timeline.topic.key}.html"
        path.write_text(page, encoding="utf-8")
        written.append(path)

    index = out_dir / "index.html"
    index.write_text(_index(timelines), encoding="utf-8")
    return [index, *written]


def _data_uri(file: str, blobs: dict[str, Blob]) -> str:
    blob = blobs[file]
    return f"data:{blob.mime};base64,{base64.b64encode(blob.content).decode('ascii')}"


def _index(timelines: list[Timeline]) -> str:
    rows = "\n".join(
        f'    <li><a href="{escape(timeline.topic.key)}.html">{escape(timeline.topic.title)}</a>'
        f" · {escape(timeline.topic.kind)} · {len(timeline.events)} Events</li>"
        for timeline in timelines
    )
    return (
        "<!doctype html>\n"
        '<html lang="de">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Chronicle</title>\n"
        '<style>body { font-family: system-ui, sans-serif; margin: 16px; line-height: 1.5; }</style>\n'
        "</head>\n"
        "<body>\n"
        "<h1>Chronicle</h1>\n"
        "<ul>\n"
        f"{rows}\n"
        "</ul>\n"
        "</body>\n"
        "</html>\n"
    )
