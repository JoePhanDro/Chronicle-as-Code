"""What the page routers share: the templates, the checks of a request and the rendering of a page."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path, PurePosixPath

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError

from ..auth import Auth
from ..markdown import markdown_filter
from ..models import PHASE_COLORS, STATUS_LABELS, Phase


TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _event_details(event: dict) -> Markup:
    """Details of an event as HTML. Images resolve to the evidence files of this event only."""
    images = {PurePosixPath(item["file"]).name: f"/api/v1/evidence/{item['id']}" for item in event.get("evidence", [])}
    return markdown_filter(event.get("details"), images)


TEMPLATES.env.filters["event_details"] = _event_details


TEMPLATES.env.filters["event_details"] = _event_details
WRITER_ROLES = ("admin", "analyst")


def _text_param(params, name: str) -> str | None:
    return (params.get(name) or "").strip() or None


def _choice_param(params, name: str, options) -> str | None:
    value = _text_param(params, name)
    return value if value in {str(option) for option in options} else None


def _day_param(params, name: str) -> date | None:
    value = _text_param(params, name)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _page_param(params) -> int:
    try:
        return max(1, int(params.get("page") or 1))
    except ValueError:
        return 1


# Phase colour for the dot next to a phase name; a name outside the enum (old data) gets the neutral colour.
TEMPLATES.env.globals["phase_color"] = lambda name: next(
    (color for phase, color in PHASE_COLORS.items() if phase.value == name), PHASE_COLORS[Phase.ACTIVITY])


# One German wording for a status wherever it is shown or chosen; a value outside the enum (old data) is shown as stored.
TEMPLATES.env.globals["status_text"] = {status.value: label for status, label in STATUS_LABELS.items()}
TEMPLATES.env.filters["attack_url"] = lambda technique: (
    "https://attack.mitre.org/techniques/" + str(technique).replace(".", "/") + "/"  # T1059.001 -> T1059/001
)
TEMPLATES.env.filters["count_events"] = lambda number: f"{number} {'Event' if number == 1 else 'Events'}"


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return f"{field}: {first['msg']}"
    return str(exc)


def not_found_page(request: Request, user: dict) -> HTMLResponse:
    """The 404 page for an address that matches no route (pages that know their topic or event render their own)."""
    return TEMPLATES.TemplateResponse(
        request, "not_found.html", {"user": user, "can_write": user["role"] in WRITER_ROLES}, status_code=404
    )


class PageContext:
    """What every page router needs of a request: the signed-in user, the CSRF check and the rendering of a page."""

    def __init__(self, get_conn: Callable[..., Iterator[sqlite3.Connection]], auth: Auth):
        self.get_conn = get_conn

        def page_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict | None:
            return auth.page_session(request, conn)

        self.page_user = page_user

    @staticmethod
    def ok_csrf(user: dict, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(token, user["csrf_token"])

    @staticmethod
    def render(request: Request, template: str, user: dict, status_code: int = 200, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            template,
            {
                "user": user,
                "can_write": user["role"] in WRITER_ROLES,
                "reloaded": request.query_params.get("neu_geladen") == "1",
                **context,
            },
            status_code=status_code,
        )
