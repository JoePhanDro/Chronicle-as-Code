"""PDF report of one topic, built from the database like the HTML report (ADR 0006).

Two variants: "technisch" holds everything (facts, causes, details, evidence), "management" only the event titles
with their time and a few figures. The PDF has no graph and no live content. It states the time it was made.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

from . import accounts, repository
from .evidence import check_file
from .exporter import topic_document
from .models import PHASE_COLORS, STATUS_LABELS, Event, Phase, Timeline

VARIANTS = {"technisch": "technischer Bericht", "management": "Management Summary"}
IMAGE_MIMES = {"image/png", "image/jpeg"}

_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["strikethrough"])
_base = getSampleStyleSheet()
INK, MUTED, RULE = colors.HexColor("#0f172a"), colors.HexColor("#64748b"), colors.HexColor("#cbd5e1")

BODY = ParagraphStyle("Body", parent=_base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, leading=11.5, textColor=MUTED)
TITLE = ParagraphStyle("Title", parent=BODY, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=2)
EVENT_TITLE = ParagraphStyle("EventTitle", parent=BODY, fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceAfter=2)
CODE = ParagraphStyle("Code", parent=BODY, fontName="Courier", fontSize=8, leading=10.5, backColor=colors.HexColor("#f1f5f9"), borderPadding=4)


def _plain(value: str) -> str:
    """The built-in fonts hold Latin-1 and a few typographic marks; other characters become '?'."""
    return "".join(ch if ord(ch) < 256 or ch in "„“”‚‘’–—•…€" else "?" for ch in value)


def _text(value: str) -> str:
    """Escaped for Paragraph markup."""
    return escape(_plain(value))


def _inline(token) -> str:
    out: list[str] = []
    for child in token.children or []:
        match child.type:
            case "text":
                out.append(_text(child.content))
            case "softbreak" | "hardbreak":
                out.append("<br/>")
            case "code_inline":
                out.append(f'<font face="Courier">{_text(child.content)}</font>')
            case "strong_open":
                out.append("<b>")
            case "strong_close":
                out.append("</b>")
            case "em_open":
                out.append("<i>")
            case "em_close":
                out.append("</i>")
            case "s_open":
                out.append("<strike>")
            case "s_close":
                out.append("</strike>")
            case "image":
                out.append(_text(child.content))  # evidence images are listed below the details, not placed in the text
    return "".join(out)


def _blocks(markdown: str) -> list:
    """The event details as flowables: paragraphs, headings, lists and code. Tables and links come out as plain text."""
    tokens = _markdown.parse(markdown)
    flowables: list = []
    stack: list[tuple[str, list[ListItem]]] = []
    item_parts: list[list] = []
    index = 0

    def add(flowable) -> None:
        (item_parts[-1] if item_parts else flowables).append(flowable)

    while index < len(tokens):
        token = tokens[index]
        match token.type:
            case "heading_open":
                add(Paragraph(_inline(tokens[index + 1]), H3))
            case "paragraph_open":
                add(Paragraph(_inline(tokens[index + 1]), BODY))
            case "fence" | "code_block":
                add(Preformatted(_plain(token.content.rstrip("\n")), CODE))
            case "bullet_list_open" | "ordered_list_open":
                stack.append(("1" if token.type == "ordered_list_open" else "bullet", []))
            case "list_item_open":
                item_parts.append([])
            case "list_item_close":
                parts = item_parts.pop()
                stack[-1][1].append(ListItem(parts or [Paragraph("", BODY)]))
            case "bullet_list_close" | "ordered_list_close":
                kind, items = stack.pop()
                add(ListFlowable(items, bulletType=kind, bulletFontSize=8, leftIndent=14))
            case "blockquote_open":
                pass
            case "hr":
                add(HRFlowable(width="100%", color=RULE))
        index += 1
    return flowables


def _when(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _counts(events: list[Event]) -> list[tuple[str, int]]:
    status = {label: sum(1 for event in events if event.status == key) for key, label in STATUS_LABELS.items()}
    return [("Events", len(events)), ("Akteure", len({e.actor for e in events})),
            ("Systeme", len({e.system for e in events if e.system})), *status.items()]


def _facts_table(rows: list[tuple[str, int]]) -> Table:
    table = Table([[label for label, _ in rows], [str(count) for _, count in rows]], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica"), ("FONTSIZE", (0, 0), (-1, 0), 8), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("FONTSIZE", (0, 1), (-1, 1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 22), ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _header(timeline: Timeline, variant: str, made_at: str) -> list:
    topic = timeline.topic
    flowables = [
        Paragraph(f"{_text(VARIANTS[variant]).upper()} · {_text(topic.kind)} · {_text(topic.key)}", SMALL),
        Paragraph(_text(topic.title), TITLE),
        Paragraph(f"Stand {made_at}", SMALL),
    ]
    if topic.summary:
        flowables += [Spacer(1, 6), Paragraph(_text(topic.summary), BODY)]
    events = timeline.events
    flowables += [Spacer(1, 10), _facts_table(_counts(events))]
    if events:
        first, last = min(e.t0 for e in events), max(e.t0 for e in events)
        flowables.append(Spacer(1, 4))
        flowables.append(Paragraph(f"Zeitraum: {_when(first)} bis {_when(last)}", SMALL))
    return flowables


def _event_dot(phase: Phase) -> str:
    return f'<font color="{PHASE_COLORS[phase]}">&#9679;</font>'


def _management(timeline: Timeline) -> list:
    events = sorted(timeline.events, key=lambda e: (e.t0, e.key))
    flowables: list = [Paragraph("Ablauf", H2)]
    if not events:
        return flowables + [Paragraph("Noch keine Events.", BODY)]
    rows = [[Paragraph(_text(_when(event.t0)), SMALL), Paragraph(f"{_event_dot(event.phase)} {_text(event.action)}", BODY)]
            for event in events]
    table = Table(rows, colWidths=[38 * mm, None], repeatRows=0)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return flowables + [table]


def _image(content: bytes, max_width: float) -> Image | None:
    try:
        width, height = ImageReader(io.BytesIO(content)).getSize()
    except Exception:  # a damaged image is left out, the file is still listed
        return None
    scale = min(1.0, max_width / width, 90 * mm / height)
    return Image(io.BytesIO(content), width=width * scale, height=height * scale, hAlign="LEFT")


def _technical(timeline: Timeline, evidence: dict[str, bytes], content_width: float) -> list:
    events = sorted(timeline.events, key=lambda e: (e.t0, e.key))
    titles = {event.key: event.action for event in events}
    flowables: list = [Paragraph("Events", H2)]
    if not events:
        return flowables + [Paragraph("Noch keine Events.", BODY)]
    for event in events:
        facts = [_when(event.t0), event.phase.value, STATUS_LABELS[event.status], event.actor]
        if event.system:
            facts.append(event.system)
        block: list = [
            HRFlowable(width="100%", color=RULE, spaceBefore=6, spaceAfter=6),
            Paragraph(f"{_event_dot(event.phase)} {_text(event.action)}", EVENT_TITLE),
            Paragraph(" · ".join(_text(fact) for fact in facts) + f" · <font face=\"Courier\">{_text(event.key)}</font>", SMALL),
        ]
        if event.triggered_by:
            causes = ", ".join(f"{_text(titles.get(key, key))}" for key in event.triggered_by)
            block.append(Paragraph(f"Ausgelöst durch: {causes}", SMALL))
        effects = [other.action for other in events if event.key in other.triggered_by]
        if effects:
            block.append(Paragraph("Löste aus: " + ", ".join(_text(title) for title in effects), SMALL))
        block.append(Spacer(1, 4))
        details = _blocks(event.details)
        flowables.append(KeepTogether(block + details[:1]))  # the heading never stands alone at the foot of a page
        flowables += details[1:]
        for item in event.evidence:
            flowables += _evidence(item, evidence.get(item.file), content_width)
    return flowables


def _evidence(item, content: bytes | None, content_width: float) -> list:
    note = f"Evidence ({_text(item.type)}): <font face=\"Courier\">{_text(item.file)}</font>"
    if item.description:
        note += f" – {_text(item.description)}"
    parts: list = [Spacer(1, 3), Paragraph(note, SMALL)]
    if content is not None and check_file(item.file, content).mime in IMAGE_MIMES:
        image = _image(content, content_width)
        if image is not None:
            parts.append(image)
    return [KeepTogether(parts)]


def render_pdf(timeline: Timeline, evidence: dict[str, bytes], variant: str, made_at: str) -> bytes:
    buffer = io.BytesIO()
    margin = 20 * mm
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=timeline.topic.title, author="Chronicle",
                            subject=VARIANTS[variant])
    width = A4[0] - 2 * margin

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 10 * mm, f"Vertraulich · {timeline.topic.key} · {made_at}")
        canvas.drawRightString(A4[0] - margin, 10 * mm, f"Seite {document.page}")
        canvas.restoreState()

    body = _technical(timeline, evidence, width) if variant == "technisch" else _management(timeline)
    doc.build(_header(timeline, variant, made_at) + body, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def make_pdf_report(
    conn: sqlite3.Connection, topic_key: str, actor_id: int, ip: str, variant: str, data_dir=None
) -> tuple[str, bytes]:
    """Build the PDF and write an audit entry. Raises NotFound for an unknown topic or variant."""
    if variant not in VARIANTS:
        raise repository.NotFound(f"Berichtsart {variant!r} gibt es nicht")
    found = topic_document(conn, topic_key, expand_refs=True, data_dir=data_dir)
    if found is None:
        raise repository.NotFound(f"Topic {topic_key!r} nicht gefunden")
    document, evidence = found
    timeline = Timeline.model_validate(document)
    body = render_pdf(timeline, evidence, variant, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    accounts.audit(conn, "report_created", user_id=actor_id, target=topic_key, detail=f"pdf={variant} evidence={len(evidence)}", ip=ip)
    return f"{variant}-{topic_key}.pdf", body
