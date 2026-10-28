"""Event pages: list, detail, create, edit, delete and the evidence of an event. Forms carry the session CSRF token."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile  # form uploads are Starlette objects

from .. import repository
from ..auth import client_ip
from ..evidence import EVIDENCE_TYPES, MAX_EVIDENCE_BYTES, Blob, check_file
from ..models import TECHNIQUE_ID, Phase, Status
from ..event_input import EVENT_TIMEZONES, EventWrite, event_of, new_event_key, t0_from_parts, t0_parts, topic_list
from .common import TEMPLATES, WRITER_ROLES, PageContext, _text_param, _choice_param, _day_param, _page_param, _error_text


HOURS = [f"{hour:02d}" for hour in range(24)]
MINUTES = [f"{minute:02d}" for minute in range(60)]
EVENTS_PAGE_SIZE = 50


def _raw(form, name: str) -> str:
    return form.get(name) or ""


def _field(form, name: str) -> str:
    return _raw(form, name).strip()


def _technique_param(params) -> str | None:
    """The technique filter from the query string; a value that is no ATT&CK ID is ignored."""
    value = (_text_param(params, "technique") or "").upper()
    return value if TECHNIQUE_ID.fullmatch(value) else None


def _payload_from_form(form, topic_home: str | None = None) -> dict:
    return {
        "key": _field(form, "key"),
        "t0": t0_from_parts(_raw(form, "t0_date"), _raw(form, "t0_hour"), _raw(form, "t0_minute"), _raw(form, "t0_zone")),
        "phase": _raw(form, "phase"),
        "actor": _field(form, "actor"),
        "system": _field(form, "system") or None,
        "action": _field(form, "action"),
        "status": _raw(form, "status"),
        "triggered_by": [key for key in map(str.strip, form.getlist("triggered_by")) if key],
        "techniques": [item for item in re.split(r"[,;\s]+", _raw(form, "techniques")) if item],
        "details": _raw(form, "details"),
        "topic": topic_home or _raw(form, "topic"),
        "topics": form.getlist("topics"),
    }


def _form_dict(form) -> dict:
    """Plain dict of a submitted form, with the multi-value fields kept as lists."""
    values = {name: form.get(name) for name in form.keys()}
    values["topics"] = form.getlist("topics")
    values["triggered_by"] = form.getlist("triggered_by")
    return values


def edit_form(event: dict) -> dict:
    """A stored event as the edit form shows it. The timestamp is shown in the default zone."""
    return {
        "key": event["key"], "phase": event["phase"], "actor": event["actor"], "system": event["system"] or "",
        "action": event["action"], "status": event["status"], "triggered_by": event["triggered_by"],
        "techniques": ", ".join(event["techniques"]), "details": event["details"] or "", "topics": event["topics"], "topic": event["home_topic"],
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


def make_event_pages(ctx: PageContext) -> APIRouter:
    get_conn, page_user, ok_csrf, render = ctx.get_conn, ctx.page_user, ctx.ok_csrf, ctx.render
    router = APIRouter()

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
        params = request.query_params
        topic_key = _text_param(params, "topic")
        topic = None
        if topic_key is not None:
            topic = repository.get_topic(conn, topic_key)
            if topic is None:
                return render(request, "not_found.html", user, status_code=404)
        flt = repository.EventFilter(
            topic=topic_key, query=_text_param(params, "q"), phase=_choice_param(params, "phase", Phase),
            status=_choice_param(params, "status", Status), actor=_text_param(params, "actor"),
            system=_text_param(params, "system"), technique=_technique_param(params),
            since=_day_param(params, "since"), until=_day_param(params, "until"),
        )
        # Filters go into every page link; empty ones are left out of the URL.
        query = {
            "topic": topic_key, "q": flt.query, "phase": flt.phase, "status": flt.status, "actor": flt.actor,
            "system": flt.system, "technique": flt.technique,
            "since": flt.since.isoformat() if flt.since else None,
            "until": flt.until.isoformat() if flt.until else None,
        }
        query = {name: value for name, value in query.items() if value}
        total = repository.count_events(conn, flt)
        pages = max(1, -(-total // EVENTS_PAGE_SIZE))
        page = min(_page_param(params), pages)
        events = repository.find_events(conn, flt, limit=EVENTS_PAGE_SIZE, offset=(page - 1) * EVENTS_PAGE_SIZE)
        return render(request, "events.html", user, events=events, total=total, page=page, pages=pages,
                      page_url=lambda number: "/events?" + urlencode({**query, "page": number}),
                      query=query, filtered=bool(query), phases=list(Phase), statuses=list(Status),
                      topic=topic, topics=repository.list_topics(conn))

    @router.get("/events/new", response_class=HTMLResponse)
    def new_event_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        now = t0_parts(datetime.now(UTC).isoformat())
        topic = request.query_params.get("topic")  # the topic page's empty state links here with its key
        if topic and repository.get_topic(conn, topic):
            now["topic"] = topic
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
            created = repository.create_event(conn, event_of(body), topic_list(body), user["id"], client_ip(request), data_dir=request.app.state.data_dir)
            for blob in attachments:
                repository.add_evidence(conn, created["key"], blob, form.get("evidence_type") or "", "",
                                        user["id"], client_ip(request), data_dir=request.app.state.data_dir)
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
        saved = {"phase": "Phase", "status": "Status"}.get(request.query_params.get("gespeichert") or "")
        return render(request, "event.html", user, mode="view", event=event, errors=None, saved=saved,
                      links=repository.get_event_links(conn, key), **form_context(conn, form=None))

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
                                    user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except (ValidationError, ValueError, repository.RepositoryError) as exc:
            status = 409 if isinstance(exc, repository.Conflict) else 422
            return render(request, "event.html", user, status_code=status, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse(f"/events/{key}", status_code=303)

    @router.post("/events/{key}/mark", response_class=HTMLResponse)
    async def mark_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        """Change only phase and status, as the chips on the event page do; everything else stays as stored."""
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        current = repository.get_event(conn, key)
        if current is None:
            return render(request, "not_found.html", user, status_code=404)
        phase, status = form.get("phase") or current["phase"], form.get("status") or current["status"]
        try:
            version = int(form.get("version") or 0)
            payload = {
                "key": key, "t0": current["t0"], "phase": phase, "actor": current["actor"], "system": current["system"],
                "action": current["action"], "status": status, "triggered_by": current["triggered_by"],
                "techniques": current["techniques"], "details": current["details"] or "", "topic": current["home_topic"], "topics": current["topics"],
            }
            body = EventWrite.model_validate(payload)
            repository.update_event(conn, key, event_of(body), topic_list(body), version,
                                    user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except (ValidationError, ValueError, repository.RepositoryError) as exc:
            code = 409 if isinstance(exc, repository.Conflict) else 422
            return render(request, "event.html", user, status_code=code, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc), saved=None,
                          links=repository.get_event_links(conn, key), **form_context(conn, form=None))
        changed = "phase" if phase != current["phase"] else "status"
        return RedirectResponse(f"/events/{key}?gespeichert={changed}", status_code=303)

    @router.post("/events/{key}/delete", response_class=HTMLResponse)
    async def delete_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.delete_event(conn, key, user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except repository.RepositoryError as exc:
            return render(request, "event.html", user, status_code=409, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse("/events", status_code=303)

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
                                    (form.get("description") or "").strip(), user["id"], client_ip(request),
                                    data_dir=request.app.state.data_dir)
        except (ValueError, repository.RepositoryError) as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return render(request, "event.html", user, status_code=status, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc), upload_failed=True,
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
            repository.delete_evidence(conn, evidence_id, user["id"], client_ip(request),
                                       data_dir=request.app.state.data_dir)
        except repository.RepositoryError as exc:
            return render(request, "event.html", user, status_code=404, mode="view",
                          event=repository.get_event(conn, key), errors=_error_text(exc),
                          **form_context(conn, form=None))
        return RedirectResponse(f"/events/{key}", status_code=303)

    return router
