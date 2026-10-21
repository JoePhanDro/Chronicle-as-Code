"""HTML report of one topic, built from the database (ADR 0006).

The file holds the topic, its events, the events it refers to and all evidence as data URIs,
so it can be sent on its own. It needs no login and no server, and it shows the state at the time it was made.
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import accounts, repository
from .evidence import check_file
from .exporter import topic_document
from .models import Timeline
from .renderer import render_dashboard

STYLESHEET = Path(__file__).parent / "static" / "app.css"


def make_report(conn: sqlite3.Connection, topic_key: str, actor_id: int, ip: str) -> tuple[str, bytes]:
    """Build the report and write an audit entry. Raises NotFound for an unknown topic."""
    found = topic_document(conn, topic_key, expand_refs=True)
    if found is None:
        raise repository.NotFound(f"Topic {topic_key!r} nicht gefunden")
    document, evidence = found
    timeline = Timeline.model_validate(document)

    def data_uri(item) -> str:
        content = evidence[item.file]
        mime = check_file(item.file, content).mime
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    html = render_dashboard(
        timeline,
        inline_css=STYLESHEET.read_text(encoding="utf-8"),
        src_for=data_uri,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    accounts.audit(conn, "report_created", user_id=actor_id, target=topic_key, detail=f"evidence={len(evidence)}", ip=ip)
    return f"bericht-{topic_key}.html", html.encode("utf-8")


def make_dashboard(conn: sqlite3.Connection, topic_key: str) -> str:
    """The dashboard for the app. Evidence is loaded from the login-protected API, not embedded."""
    evidence_ids: dict[str, int] = {}
    found = topic_document(conn, topic_key, expand_refs=True, evidence_ids=evidence_ids)
    if found is None:
        raise repository.NotFound(f"Topic {topic_key!r} nicht gefunden")
    document, _ = found
    return render_dashboard(
        Timeline.model_validate(document),
        inline_css=STYLESHEET.read_text(encoding="utf-8"),
        src_for=lambda item: f"/api/v1/evidence/{evidence_ids[item.file]}",
        snapshot=False,
    )


def report_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
