"""Topics: reading for every logged-in user, creating, changing, deleting and linking for admins and analysts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import ioc_export, repository, tasks
from ..auth import Auth, client_ip
from ..event_input import new_topic_key
from ..ioc_export import FORMATS
from ..models import Task, Topic, TopicCreate
from .common import write


class TopicLink(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    event: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def make_topic_routes(get_conn: Callable, auth: Auth) -> APIRouter:
    router = APIRouter()
    current_user = auth.current_user
    writer = auth.require_role("admin", "analyst")

    @router.get("/api/v1/topics")
    def topics(conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        return repository.list_topics(conn)

    @router.get("/api/v1/topics/{key}")
    def topic(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> dict:
        found = repository.get_topic(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        # tasks live on the timeline, not on the Topic model; the API returns them separately (issue #226),
        # so a GET->PUT round trip of the topic body stays valid.
        found = dict(found)
        found.pop("tasks", None)
        return found

    @router.get("/api/v1/topics/{key}/tasks")
    def topic_tasks(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        """The checklist tasks of a topic (issue #226)."""
        found = repository.get_topic(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        return found["tasks"]

    @router.get("/api/v1/topics/{key}/events")
    def topic_events(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        if repository.get_topic(conn, key) is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        return repository.list_topic_events(conn, key)

    @router.post("/api/v1/topics", status_code=201)
    def create_topic(body: TopicCreate, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        topic = Topic.model_validate({**body.model_dump(), "key": body.key or new_topic_key()})
        return write(lambda: repository.create_topic(conn, topic, user["id"], client_ip(request), data_dir=request.app.state.data_dir))

    @router.put("/api/v1/topics/{key}")
    def update_topic(key: str, body: Topic, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        if body.key != key:
            raise HTTPException(status_code=422, detail="Der Key im Pfad und im Body müssen übereinstimmen")
        return write(lambda: repository.update_topic(conn, key, body, user["id"], client_ip(request), data_dir=request.app.state.data_dir))

    @router.delete("/api/v1/topics/{key}", status_code=204, response_class=Response)
    def delete_topic(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> Response:
        write(lambda: repository.delete_topic(conn, key, user["id"], client_ip(request), data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @router.post("/api/v1/topics/{key}/events", status_code=201)
    def link_event(key: str, body: TopicLink, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        write(lambda: repository.link_event(conn, key, body.event, user["id"], client_ip(request), data_dir=request.app.state.data_dir))
        return {"topic": key, "event": body.event}

    @router.delete("/api/v1/topics/{key}/events/{event_key}", status_code=204, response_class=Response)
    def unlink_event(key: str, event_key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> Response:
        write(lambda: repository.unlink_event(conn, key, event_key, user["id"], client_ip(request), data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @router.post("/api/v1/topics/{key}/tasks/from-template", status_code=201)
    def apply_template(key: str, body: dict, request: Request, conn: sqlite3.Connection = Depends(get_conn),
                       user: dict = Depends(writer)) -> dict:
        """Copy the tasks of a checklist template (checklists/<name>.yaml) into the topic (issue #226)."""
        name = str(body.get("template") or "").strip()
        added = write(lambda: tasks.apply_template(conn, key, name, user["id"], client_ip(request),
                                                    data_dir=request.app.state.data_dir))
        return {"added": added}

    @router.post("/api/v1/topics/{key}/tasks", status_code=201)
    def create_task(key: str, body: Task, request: Request, conn: sqlite3.Connection = Depends(get_conn),
                    user: dict = Depends(writer)) -> dict:
        """Add a task to a topic (issue #226)."""
        return write(lambda: tasks.create_task(conn, key, body, user["id"], client_ip(request),
                                               data_dir=request.app.state.data_dir))

    @router.put("/api/v1/topics/{key}/tasks/{task_key}")
    def update_task(key: str, task_key: str, body: Task, request: Request, conn: sqlite3.Connection = Depends(get_conn),
                    user: dict = Depends(writer)) -> dict:
        """Change one task, e.g. check it off (issue #226)."""
        return write(lambda: tasks.update_task(conn, key, task_key, body, user["id"], client_ip(request),
                                               data_dir=request.app.state.data_dir))

    @router.delete("/api/v1/topics/{key}/tasks/{task_key}", status_code=204, response_class=Response)
    def delete_task(key: str, task_key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn),
                    user: dict = Depends(writer)) -> Response:
        write(lambda: tasks.delete_task(conn, key, task_key, user["id"], client_ip(request),
                                        data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @router.get("/api/v1/iocs")
    def all_iocs(
        request: Request,
        format: str = "csv",
        since: date | None = None,
        until: date | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        """All entities of all topics as CSV or STIX 2.1 (issue #259); every export is audited."""
        if format not in FORMATS:
            raise HTTPException(status_code=422, detail=f"format muss sein: {', '.join(FORMATS)}")
        filename, body, media = ioc_export.make_ioc_export(conn, None, format, since, until, user["id"], client_ip(request))
        return Response(content=body, media_type=media, headers=ioc_export.export_headers(filename))

    @router.get("/api/v1/topics/{key}/iocs")
    def topic_iocs(
        key: str,
        request: Request,
        format: str = "csv",
        since: date | None = None,
        until: date | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        """The entities of one topic as CSV or STIX 2.1 (issue #259)."""
        if repository.get_topic(conn, key) is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        if format not in FORMATS:
            raise HTTPException(status_code=422, detail=f"format muss sein: {', '.join(FORMATS)}")
        filename, body, media = ioc_export.make_ioc_export(conn, key, format, since, until, user["id"], client_ip(request))
        return Response(content=body, media_type=media, headers=ioc_export.export_headers(filename))

    return router
