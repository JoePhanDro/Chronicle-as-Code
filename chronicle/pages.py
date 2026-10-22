"""HTML pages for events: list, detail, create, edit and delete. Forms are protected by the session CSRF token."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from typing import get_args
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError
from starlette.datastructures import UploadFile  # form uploads are Starlette objects

from . import repository
from .auth import Auth, client_ip
from .evidence import EVIDENCE_TYPES, MAX_EVIDENCE_BYTES, Blob, check_file
from .exporter import ExportError
from .markdown import markdown_filter
from .report import make_dashboard, make_report, report_headers
from .models import Phase, Status, Topic, TopicKind
from .event_input import EVENT_TIMEZONES, EventWrite, event_of, new_event_key, t0_from_parts, t0_parts, topic_list

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
def _event_details(event: dict) -> Markup:
    """Details of an event as HTML. Images resolve to the evidence files of this event only."""
    images = {PurePosixPath(item["file"]).name: f"/api/v1/evidence/{item['id']}" for item in event.get("evidence", [])}
    return markdown_filter(event.get("details"), images)


TEMPLATES.env.filters["event_details"] = _event_details
WRITER_ROLES = ("admin", "analyst")
TOPIC_KINDS = list(get_args(TopicKind))
HOURS = [f"{hour:02d}" for hour in range(24)]
MINUTES = [f"{minute:02d}" for minute in range(60)]


def _payload_from_form(form, topic_home: str | None = None) -> dict:
    topics = form.getlist("topics")
    return {
        "key": (form.get("key") or "").strip(),
        "t0": t0_from_parts(form.get("t0_date") or "", form.get("t0_hour") or "",
                            form.get("t0_minute") or "", form.get("t0_zone") or ""),
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


def _topic_values(form) -> dict:
    return {
        "key": (form.get("key") or "").strip(),
        "title": (form.get("title") or "").strip(),
        "kind": form.get("kind") or "other",
        "summary": form.get("summary") or "",
    }


def _form_dict(form) -> dict:
    """Plain dict of a submitted form, with the checkbox list kept as a list."""
    values = {name: form.get(name) for name in form.keys()}
    values["topics"] = form.getlist("topics")
    return values


def edit_form(event: dict) -> dict:
    """A stored event as the edit form shows it. The timestamp is shown in the default zone."""
    return {
        "key": event["key"], "phase": event["phase"], "actor": event["actor"], "system": event["system"] or "",
        "action": event["action"], "status": event["status"], "triggered_by": event["triggered_by"] or "",
        "details": event["details"] or "", "topics": event["topics"], "topic": event["home_topic"],
        "version": event["version"], **t0_parts(event["t0"]),
    }


TEMPLATES.env.globals["edit_form"] = edit_form


async def _attachments_from_form(form) -> list[Blob]:
    """Files chosen in the new-event form, checked before the event is written."""
    uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile) and item.filename]
    if uploads and (form.get("evidence_type") or "") not in EVIDENCE_TYPES:
        raise ValueError("Bitte einen Typ für die Anhänge wählen")
    blobs = []
    for upload in uploads:
        data = await upload.read(MAX_EVIDENCE_BYTES + 1)  # one byte over the limit is enough to reject
        blobs.append(check_file(upload.filename or "", data))
    return blobs


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return f"{field}: {first['msg']}"
    return str(exc)


def make_pages(get_conn: Callable[..., Iterator[sqlite3.Connection]], auth: Auth) -> APIRouter:
    router = APIRouter()

    def page_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict | None:
        return auth.page_session(request, conn)

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
                "evidence_types": list(EVIDENCE_TYPES), "topic_choices": topic_choices(conn),
                "event_choices": repository.list_events(conn), "timezones": EVENT_TIMEZONES,
                "hours": HOURS, "minutes": MINUTES, **extra}

    @router.get("/events", response_class=HTMLResponse)
    def events_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        topic_key = (request.query_params.get("topic") or "").strip() or None
        topic = None
        if topic_key is not None:
            topic = repository.get_topic(conn, topic_key)
            if topic is None:
                return render(request, "not_found.html", user, status_code=404)
        return render(request, "events.html", user, events=repository.list_events(conn, topic_key),
                      topic=topic, topics=repository.list_topics(conn))

    @router.get("/events/new", response_class=HTMLResponse)
    def new_event_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        now = t0_parts(datetime.now(UTC).isoformat())
        return render(request, "event.html", user, mode="new", event=None, errors=None,
                      **form_context(conn, form=now))

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
            payload = _payload_from_form(form)
            payload["key"] = new_event_key()  # the UI never asks for a key
            body = EventWrite.model_validate(payload)
            attachments = await _attachments_from_form(form)  # checked first, so a bad file writes nothing
            created = repository.create_event(conn, event_of(body), topic_list(body), user["id"], client_ip(request))
            for blob in attachments:
                repository.add_evidence(conn, created["key"], blob, form.get("evidence_type") or "", "",
                                        user["id"], client_ip(request))
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

    # Topics: any logged-in user reads; analysts and admins change topics and links.

    def topic_page(request: Request, conn: sqlite3.Connection, user: dict, key: str, status_code: int = 200, errors: str | None = None):
        topic = repository.get_topic(conn, key)
        if topic is None:
            return render(request, "not_found.html", user, status_code=404)
        events = repository.list_topic_events(conn, key)
        linked = {event["key"] for event in events}
        addable = [event for event in repository.list_events(conn) if event["key"] not in linked]
        return render(request, "topic.html", user, status_code=status_code, mode="view", topic=topic,
                      events=events, addable=addable, errors=errors, kinds=TOPIC_KINDS, form=None)

    @router.get("/topics", response_class=HTMLResponse)
    def topics_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return render(request, "topics.html", user, topics=repository.list_topics(conn))

    @router.get("/topics/new", response_class=HTMLResponse)
    def new_topic_page(request: Request, user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        return render(request, "topic.html", user, mode="new", topic=None, events=[], addable=[], errors=None,
                      kinds=TOPIC_KINDS, form={"kind": "incident"})

    @router.post("/topics/new", response_class=HTMLResponse)
    async def create_topic_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        values = _topic_values(form)
        try:
            created = repository.create_topic(conn, Topic.model_validate(values), user["id"], client_ip(request))
        except (ValidationError, repository.RepositoryError) as exc:
            status = 409 if isinstance(exc, repository.Conflict) else 422
            return render(request, "topic.html", user, status_code=status, mode="new", topic=None, events=[], addable=[],
                          errors=_error_text(exc), kinds=TOPIC_KINDS, form=values)
        return RedirectResponse(f"/topics/{created['key']}", status_code=303)

    @router.get("/topics/{key}", response_class=HTMLResponse)
    def topic_view(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return topic_page(request, conn, user, key)

    @router.get("/topics/{key}/dashboard", response_class=HTMLResponse)
    def topic_dashboard_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            return HTMLResponse(make_dashboard(conn, key))
        except repository.NotFound:
            return render(request, "not_found.html", user, status_code=404)
        except ExportError as exc:  # shown inside the frame on the topic page
            return HTMLResponse(str(exc), status_code=409)

    @router.post("/topics/{key}/edit", response_class=HTMLResponse)
    async def edit_topic_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        values = {**_topic_values(form), "key": key}
        try:
            repository.update_topic(conn, key, Topic.model_validate(values), user["id"], client_ip(request))
        except (ValidationError, repository.RepositoryError) as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse(f"/topics/{key}", status_code=303)

    @router.post("/topics/{key}/delete", response_class=HTMLResponse)
    async def delete_topic_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.delete_topic(conn, key, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 409
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse("/topics", status_code=303)

    @router.post("/topics/{key}/link", response_class=HTMLResponse)
    async def link_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.link_event(conn, key, (form.get("event") or "").strip(), user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse(f"/topics/{key}", status_code=303)

    @router.post("/topics/{key}/unlink/{event_key}", response_class=HTMLResponse)
    async def unlink_event_page(key: str, event_key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.unlink_event(conn, key, event_key, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse(f"/topics/{key}", status_code=303)

    return router
