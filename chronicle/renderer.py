"""Turn a validated Timeline into a self-contained HTML dashboard."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

from jinja2 import Environment, PackageLoader, select_autoescape

from .models import PHASE_COLORS, Event, Phase, Timeline

ACTOR_COLOR = "#38bdf8"
SYSTEM_COLOR = "#a78bfa"
EVIDENCE_COLOR = "#64748b"
TRIGGER_COLOR = "#f97316"

_env = Environment(
    loader=PackageLoader("chronicle", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_dashboard(timeline: Timeline) -> str:
    events = sorted(timeline.events, key=lambda event: event.t0)
    nodes, edges = _build_graph(events)

    payload = {
        "events": [_event_payload(event) for event in events],
        "nodes": nodes,
        "edges": edges,
    }
    phases = [{"name": phase.value, "color": PHASE_COLORS[phase]} for phase in Phase]

    return _env.get_template("dashboard.html").render(
        title=timeline.topic.title,
        summary=timeline.topic.summary,
        phases=phases,
        payload=payload,
    )


def _event_key(event: Event) -> str:
    return f"event:{event.key}"


def _evidence_key(event: Event, file: str) -> str:
    return f"evidence:{event.key}:{file}"


def _event_payload(event: Event) -> dict:
    return {
        "id": event.key,
        "t0": event.t0.isoformat(),
        "phase": event.phase.value,
        "color": PHASE_COLORS[event.phase],
        "actor": event.actor,
        "system": event.system,
        "action": event.action,
        "status": event.status.value,
        "triggered_by": event.triggered_by,
        "details": event.details,
        "evidence": [
            {
                # Relative to index.html; builder copies each file to the same path.
                "src": quote(item.file),
                "file": item.file,
                "type": item.type,
                "description": item.description,
            }
            for item in event.evidence
        ],
    }


def _build_graph(events: list[Event]) -> tuple[list[dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    event_keys = {event.key for event in events}

    def add_node(key: str, **attributes: object) -> None:
        nodes.setdefault(key, {"id": key, **attributes})

    # Pass 1: nodes, so that edges can reference any of them.
    for event in events:
        add_node(f"actor:{event.actor}", label=event.actor, shape="ellipse", color=ACTOR_COLOR)
        if event.system:
            add_node(f"system:{event.system}", label=event.system, shape="box", color=SYSTEM_COLOR)
        add_node(
            _event_key(event),
            label=f"{event.t0:%H:%M} {_shorten(event.action)}",
            shape="diamond",
            color=PHASE_COLORS[event.phase],
            event_id=event.key,
        )
        for item in event.evidence:
            add_node(
                _evidence_key(event, item.file),
                label=PurePosixPath(item.file).name,
                shape="box",
                color=EVIDENCE_COLOR,
                event_id=event.key,
            )

    # Pass 2: edges.
    for event in events:
        key = _event_key(event)
        edges.append({"from": f"actor:{event.actor}", "to": key, "label": "führt aus"})
        if event.system:
            edges.append({"from": f"system:{event.system}", "to": key, "label": "betrifft", "dashes": True})
        for item in event.evidence:
            edges.append({"from": key, "to": _evidence_key(event, item.file), "label": "Beleg"})
        # Only draw the link when the cause is part of this view; otherwise the node would be missing.
        if event.triggered_by in event_keys:
            source = f"event:{event.triggered_by}"
            edges.append({"from": source, "to": key, "label": "löst aus", "color": {"color": TRIGGER_COLOR}})

    return list(nodes.values()), edges


def _shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
