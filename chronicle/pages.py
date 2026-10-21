"""HTML pages for events: list, detail, create, edit and delete. Forms are protected by the session CSRF token."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.datastructures import UploadFile  # form uploads are Starlette objects

from . import repository
from .auth import Auth, client_ip
from .evidence import EVIDENCE_TYPES, MAX_EVIDENCE_BYTES, check_file
from .report import make_report, report_headers
from .models import Phase, Status
from .event_input import EventWrite, event_of, topic_list

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
WRITER_ROLES = ("admin", "analyst")


def _payload_from_form(form, topic_home: str | None = None) -> dict:
    topics = form.getlist("topics")
    return {
        "key": (form.get("key") or "").strip(),
        "t0": (form.get("t0") or "").strip(),
        "phase": form.get("phase") or "",
        "actor": (form.get("actor") or "").strip(),
        "system": (form.get("system") or "").strip() or None,
        "action": (form.get("action") or "").strip(),
        "status": form.get("status") or "",
        "triggered_by": (form.get("triggered_by") or "").strip() or None,
        "details": form.get("details") or "",
        "topic": topic_home or form.get("topic") or "",
        "topics": topics,
    }


def _form_dict(form) -> dict:
    """Plain dict of a submitted form, with the checkbox list kept as a list."""
    values = {name: form.get(name) for name in form.keys()}
    values["topics"] = form.getlist("topics")
    return values


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return f"{field}: {first['msg']}"
    return str(exc)


def make_pages(get_conn: Callable[..., Iterator[sqlite3.Connection]], auth: Auth) -> APIRouter:
    router = APIRouter()

    def page_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict | None:
        return auth.session_user(request, conn)

    def ok_csrf(user: dict, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(token, user["csrf_token"])

    def render(request: Request, template: str, user: dict, status_code: int = 200, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            template,
            {"user": user, "can_write": user["role"] in WRITER_ROLES, **context},
            status_code=status_code,
        )

    def topic_choices(conn: sqlite3.Connection) -> list[dict]:
        return [{"key": key, "title": repository.get_topic(conn, key)["title"]} for key in repository.list_all_topic_keys(conn)]

    def form_context(conn: sqlite3.Connection, **extra) -> dict:
        return {"phases": [phase.value for phase in Phase], "statuses": [status.value for status in Status],
                "evidence_types": list(EVIDENCE_TYPES), "topic_choices": topic_choices(conn), **extra}

    @router.get("/events", response_class=HTMLResponse)
    def events_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return render(request, "events.html", user, events=repository.list_events(conn))

    @router.get("/events/new", response_class=HTMLResponse)
    def new_event_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        return render(request, "event.html", user, mode="new", event=None, errors=None,
                      **form_context(conn, form={}))

    @router.post("/events/new", response_class=HTMLResponse)
    async def create_event_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        if not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            body = EventWrite.model_validate(_payload_from_form(form))
            created = repository.create_event(conn, event_of(body), topic_list(body), user["id"], client_ip(request))
        except (ValidationError, ValueError, repository.RepositoryError) as exc:
            return render(request, "event.html", user, status_code=422, mode="new", event=None,
                          errors=_error_text(exc), **form_context(conn, form=_form_dict(form)))
        return RedirectResponse(f"/events/{created['key']}", status_code=303)

    @router.get("/events/{key}", response_class=HTMLResponse)
    def event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        event = repository.get_event(conn, key)
        if event is None:
            return render(request, "not_found.html", user, status_code=404)
        return render(request, "event.html", user, mode="view", event=event, errors=None,
                      **form_context(conn, form=None))

    @router.post("/events/{key}/edit", response_class=HTMLResponse)
    async def update_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        current = repository.get_event(conn, key)
        if current is None:
            return render(request, "not_found.html", user, status_code=404)
        try:
            version = int(form.get("version") or 0)
            payload = _payload_from_form(form, topic_home=current["home_topic"])
            payload["key"] = key
            body = EventWrite.model_validate(payload)
            repository.update_event(conn, key, event_of(body), topic_list(body), version,
                                    user["id"], client_ip(request))
        except (ValidationError, ValueError, repository.RepositoryError) as exc:
            status = 409 if isinstance(exc, repository.Conflict) else 422
            return render(request, "event.html", user, status_code=status, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse(f"/events/{key}", status_code=303)

    @router.post("/events/{key}/delete", response_class=HTMLResponse)
    async def delete_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.delete_event(conn, key, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            return render(request, "event.html", user, status_code=409, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse("/events", status_code=303)

    @router.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return render(request, "forbidden.html", user, status_code=403)
        return render(request, "reports.html", user, topics=repository.list_topics(conn))

    @router.get("/reports/{key}")
    def report_download(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return render(request, "forbidden.html", user, status_code=403)
        try:
            filename, body = make_report(conn, key, user["id"], client_ip(request))
        except repository.NotFound:
            return render(request, "not_found.html", user, status_code=404)
        return Response(content=body, media_type="text/html; charset=utf-8", headers=report_headers(filename))

    @router.post("/events/{key}/evidence", response_class=HTMLResponse)
    async def add_evidence_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        current = repository.get_event(conn, key)
        if current is None:
            return render(request, "not_found.html", user, status_code=404)
        upload = form.get("file")
        try:
            if not isinstance(upload, UploadFile):
                raise ValueError("Bitte eine Datei auswählen")
            data = await upload.read(MAX_EVIDENCE_BYTES + 1)  # one byte over the limit is enough to reject
            repository.add_evidence(conn, key, check_file(upload.filename or "", data), form.get("type") or "",
                                    (form.get("description") or "").strip(), user["id"], client_ip(request))
        except (ValueError, repository.RepositoryError) as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return render(request, "event.html", user, status_code=status, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse(f"/events/{key}", status_code=303)

    @router.post("/events/{key}/evidence/{evidence_id}/delete", response_class=HTMLResponse)
    async def delete_evidence_page(key: str, evidence_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.delete_evidence(conn, evidence_id, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            return render(request, "event.html", user, status_code=404, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse(f"/events/{key}", status_code=303)

    return router
