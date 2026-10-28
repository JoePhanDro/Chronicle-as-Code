"""Events and their evidence: reading for every logged-in user, writing for admins and analysts.

Evidence is served only to logged-in users, never as a public file (ADR 0004)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile

from .. import repository
from ..auth import Auth, client_ip
from ..event_input import EventCreate, EventUpdate, event_of, new_event_key, topic_list
from ..evidence import MAX_EVIDENCE_BYTES, check_file, delivery_headers, display_name
from ..models import Phase, Status
from .common import write


def make_event_routes(get_conn: Callable, auth: Auth) -> APIRouter:
    router = APIRouter()
    current_user = auth.current_user
    writer = auth.require_role("admin", "analyst")

    @router.get("/api/v1/events")
    def events(
        topic: str | None = None,
        q: str | None = Query(None, description="Words to find in action, details and actor"),
        phase: Phase | None = None,
        status: Status | None = None,
        actor: str | None = Query(None, description="Part of the actor name"),
        technique: str | None = Query(None, pattern=r"^[Tt]\d{4}(\.\d{3})?$",
                                      description="ATT&CK technique ID; T1059 also finds T1059.001"),
        since: date | None = Query(None, description="t0 on or after this UTC day"),
        until: date | None = Query(None, description="t0 on or before this UTC day"),
        limit: int | None = Query(None, ge=1, le=500, description="Without a limit, all events are returned"),
        offset: int = Query(0, ge=0),
        conn: sqlite3.Connection = Depends(get_conn),
        _: dict = Depends(current_user),
    ) -> list[dict]:
        if topic is not None and repository.get_topic(conn, topic) is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        flt = repository.EventFilter(topic=topic, query=q, phase=phase, status=status, actor=actor,
                                     technique=technique, since=since, until=until)
        return repository.find_events(conn, flt, limit=limit, offset=offset)

    @router.get("/api/v1/events/{key}")
    def event(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> dict:
        found = repository.get_event(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Event nicht gefunden")
        return found

    @router.get("/api/v1/evidence/{evidence_id}")
    def evidence_file(
        evidence_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn),
        _: dict = Depends(current_user),
    ) -> Response:
        try:
            found = repository.get_evidence_content(conn, evidence_id, request.app.state.data_dir)
        except repository.Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if found is None or found["content"] is None:
            raise HTTPException(
                status_code=404,
                detail="Evidence nicht gefunden oder ohne gespeicherten Inhalt. Bitte den Import erneut ausführen.",
            )
        return Response(
            content=found["content"],
            media_type=found["mime"],
            headers=delivery_headers(display_name(found["file"]), found["mime"]),
        )

    @router.post("/api/v1/events", status_code=201)
    def create_event(
        body: EventCreate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> dict:
        event = body.model_copy(update={"key": body.key or new_event_key()})
        return write(
            lambda: repository.create_event(conn, event_of(event), topic_list(event), user["id"], client_ip(request),
                                            data_dir=request.app.state.data_dir)
        )

    @router.put("/api/v1/events/{key}")
    def update_event(
        key: str,
        body: EventUpdate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> dict:
        if body.key != key:
            raise HTTPException(status_code=422, detail="Der Key im Pfad und im Body müssen übereinstimmen")
        return write(
            lambda: repository.update_event(
                conn, key, event_of(body), topic_list(body), body.version, user["id"], client_ip(request),
                data_dir=request.app.state.data_dir,
            )
        )

    @router.delete("/api/v1/events/{key}", status_code=204, response_class=Response)
    def delete_event(
        key: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        write(lambda: repository.delete_event(conn, key, user["id"], client_ip(request),
                                              data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @router.post("/api/v1/events/{key}/evidence", status_code=201)
    async def upload_evidence(
        key: str,
        request: Request,
        file: UploadFile = File(...),
        evidence_type: str = Form(..., alias="type"),
        description: str = Form(""),
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> dict:
        data = await file.read(MAX_EVIDENCE_BYTES + 1)  # one byte over the limit is enough to reject
        return write(
            lambda: repository.add_evidence(
                conn, key, check_file(file.filename or "", data), evidence_type, description, user["id"], client_ip(request),
                data_dir=request.app.state.data_dir,
            )
        )

    @router.delete("/api/v1/evidence/{evidence_id}", status_code=204, response_class=Response)
    def delete_evidence(
        evidence_id: int,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        write(lambda: repository.delete_evidence(conn, evidence_id, user["id"], client_ip(request),
                                                 data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    return router
