"""Topic pages: any logged-in user reads; analysts and admins change topics and links."""

from __future__ import annotations

import sqlite3
from typing import get_args

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from .. import repository
from ..auth import client_ip
from ..exporter import ExportError
from ..report import make_dashboard
from ..models import Topic, TopicKind
from ..event_input import EventWrite, event_of, new_topic_key, topic_list
from .common import WRITER_ROLES, PageContext, _error_text


TOPIC_KINDS = list(get_args(TopicKind))


def _topic_values(form) -> dict:
    return {
        "title": (form.get("title") or "").strip(),
        "kind": form.get("kind") or "other",
        "summary": form.get("summary") or "",
    }


def make_topic_pages(ctx: PageContext) -> APIRouter:
    get_conn, page_user, ok_csrf, render = ctx.get_conn, ctx.page_user, ctx.ok_csrf, ctx.render
    router = APIRouter()

    def topic_page(request: Request, conn: sqlite3.Connection, user: dict, key: str, status_code: int = 200, errors: str | None = None, closed: int | None = None):
        topic = repository.get_topic(conn, key)
        if topic is None:
            return render(request, "not_found.html", user, status_code=404)
        events = repository.list_topic_events(conn, key)
        linked = {event["key"] for event in events}
        addable = [event for event in repository.list_events(conn) if event["key"] not in linked]
        delete_plan = repository.topic_delete_plan(conn, key)
        unresolved = [event for event in events if event["status"] != "resolved"]
        closable = [event for event in unresolved if event["home_topic"] == key]
        return render(request, "topic.html", user, status_code=status_code, mode="view", topic=topic,
                      events=events, addable=addable, errors=errors, kinds=TOPIC_KINDS, form=None,
                      closable=len(closable), foreign_open=len(unresolved) - len(closable), closed=closed,
                      status=repository.most_pressing_status(event["status"] for event in events),
                      delete_plan=delete_plan)

    @router.get("/topics", response_class=HTMLResponse)
    def topics_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return render(request, "topics.html", user, topics=repository.list_topics(conn, by_attention=True))

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
            # The UI never asks for a key. A clash with an existing one is a one in four billion chance per day; try again.
            for attempt in range(3):
                try:
                    created = repository.create_topic(conn, Topic.model_validate({**values, "key": new_topic_key()}),
                                                      user["id"], client_ip(request), data_dir=request.app.state.data_dir)
                    break
                except repository.Conflict:
                    if attempt == 2:
                        raise
        except (ValidationError, repository.RepositoryError) as exc:
            status = 409 if isinstance(exc, repository.Conflict) else 422
            return render(request, "topic.html", user, status_code=status, mode="new", topic=None, events=[], addable=[],
                          errors=_error_text(exc), kinds=TOPIC_KINDS, form=values)
        return RedirectResponse(f"/topics/{created['key']}", status_code=303)

    @router.get("/topics/{key}", response_class=HTMLResponse)
    def topic_view(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        closed = request.query_params.get("abgeschlossen", "")
        return topic_page(request, conn, user, key, closed=int(closed) if closed.isdigit() else None)

    @router.get("/topics/{key}/dashboard", response_class=HTMLResponse)
    def topic_dashboard_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            return HTMLResponse(make_dashboard(conn, key, embedded=request.query_params.get("embed") == "1"))
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
            repository.update_topic(conn, key, Topic.model_validate(values), user["id"], client_ip(request), data_dir=request.app.state.data_dir)
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
            repository.delete_topic(conn, key, user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except repository.RepositoryError as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 409
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse("/topics", status_code=303)

    @router.post("/topics/{key}/close-events", response_class=HTMLResponse)
    async def close_topic_events_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        """Set every unresolved event that has this topic as its home to resolved, one event after the other."""
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        if repository.get_topic(conn, key) is None:
            return render(request, "not_found.html", user, status_code=404)
        done = 0
        try:
            for listed in repository.list_topic_events(conn, key):
                if listed["home_topic"] != key or listed["status"] == "resolved":
                    continue
                current = repository.get_event(conn, listed["key"])
                body = EventWrite.model_validate({
                    "key": current["key"], "t0": current["t0"], "phase": current["phase"], "actor": current["actor"],
                    "system": current["system"], "action": current["action"], "status": "resolved",
                    "triggered_by": current["triggered_by"], "details": current["details"] or "",
                    "topic": current["home_topic"], "topics": current["topics"],
                })
                repository.update_event(conn, current["key"], event_of(body), topic_list(body), current["version"],
                                        user["id"], client_ip(request), data_dir=request.app.state.data_dir)
                done += 1
        except (ValidationError, ValueError, repository.RepositoryError) as exc:
            code = 409 if isinstance(exc, repository.Conflict) else 422
            return topic_page(request, conn, user, key, status_code=code, closed=done,
                              errors=f"{_error_text(exc)} Abgeschlossen wurden bis dahin {done} Events.")
        return RedirectResponse(f"/topics/{key}?abgeschlossen={done}", status_code=303)

    @router.post("/topics/{key}/link", response_class=HTMLResponse)
    async def link_event_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        try:
            repository.link_event(conn, key, (form.get("event") or "").strip(), user["id"], client_ip(request),
                                  data_dir=request.app.state.data_dir)
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
            repository.unlink_event(conn, key, event_key, user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except repository.RepositoryError as exc:
            status = 404 if isinstance(exc, repository.NotFound) else 422
            return topic_page(request, conn, user, key, status_code=status, errors=_error_text(exc))
        return RedirectResponse(f"/topics/{key}", status_code=303)

    return router
