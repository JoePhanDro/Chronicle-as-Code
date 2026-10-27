"""Topics: reading for every logged-in user, creating, changing, deleting and linking for admins and analysts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import repository
from ..auth import Auth, client_ip
from ..event_input import new_topic_key
from ..models import Topic, TopicCreate
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
        return found

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

    return router
