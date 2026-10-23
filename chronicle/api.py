"""HTTP app: pages, login, read API for topics and events, admin API for users and audit log."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, accounts, administration, repository, security
from .admin_pages import make_admin_pages
from .auth import PasswordChangeRequired, SetupState, client_ip, make_auth, parse_trusted_proxies
from .db import connect
from .evidence import MAX_EVIDENCE_BYTES, EvidenceRejected, check_file, delivery_headers, display_name
from .models import Phase, Status, Topic
from .pages import make_pages
from .report import make_report, report_headers
from .event_input import EventCreate, EventUpdate, event_of, new_event_key, topic_list

DEFAULT_DB = "/data/chronicle.db"
STATIC_DIR = Path(__file__).parent / "static"


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    role: Literal["admin", "analyst", "viewer"] = "viewer"


class TopicLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "analyst", "viewer"] | None = None
    active: bool | None = None
    password: str | None = None


def create_app(db_path: str | Path, trusted_proxies: str | None = None, data_dir: str | Path | None = None) -> FastAPI:
    """`trusted_proxies` defaults to CHRONICLE_TRUSTED_PROXIES, see README.

    With `data_dir` the topic files are the source of truth: edits are written to them, then the index is rebuilt
    (ADR 0008). Without it the database is the source, as before."""
    path = Path(db_path)
    setup_state = SetupState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _announce_setup(path, setup_state)  # at server start, not at import
        yield

    app = FastAPI(title="Chronicle", version=__version__, lifespan=lifespan)
    app.state.data_dir = Path(data_dir) if data_dir else None
    app.state.trusted_proxies = parse_trusted_proxies(
        os.environ.get("CHRONICLE_TRUSTED_PROXIES") if trusted_proxies is None else trusted_proxies
    )

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(path)
        try:
            yield conn
        finally:
            conn.close()

    cookie_secure = os.environ.get("CHRONICLE_COOKIE_SECURE", "1") != "0"
    auth = make_auth(get_conn, cookie_secure=cookie_secure, setup=setup_state)
    current_user = auth.current_user
    admin_only = auth.require_role("admin")

    app.include_router(auth.router)
    # Pages of a user who must change the password go to the change form instead.
    app.add_exception_handler(PasswordChangeRequired, lambda request, exc: RedirectResponse("/account/password", status_code=303))
    app.include_router(make_pages(get_conn, auth))
    app.include_router(make_admin_pages(get_conn, auth))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    _bootstrap_admin(path)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    # Topics and events: any logged-in user may read.

    @app.get("/api/v1/topics")
    def topics(conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        return repository.list_topics(conn)

    @app.get("/api/v1/topics/{key}")
    def topic(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> dict:
        found = repository.get_topic(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        return found

    @app.get("/api/v1/topics/{key}/events")
    def topic_events(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        if repository.get_topic(conn, key) is None:
            raise HTTPException(status_code=404, detail="Topic nicht gefunden")
        return repository.list_topic_events(conn, key)

    @app.get("/api/v1/events")
    def events(
        topic: str | None = None,
        q: str | None = Query(None, description="Words to find in action, details and actor"),
        phase: Phase | None = None,
        status: Status | None = None,
        actor: str | None = Query(None, description="Part of the actor name"),
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
                                     since=since, until=until)
        return repository.find_events(conn, flt, limit=limit, offset=offset)

    @app.get("/api/v1/events/{key}")
    def event(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> dict:
        found = repository.get_event(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Event nicht gefunden")
        return found

    # Evidence is served only to logged-in users, never as a public file (ADR 0004).
    @app.get("/api/v1/evidence/{evidence_id}")
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

    # Writing events: admins and analysts. Validation is the same as for imports.

    writer = auth.require_role("admin", "analyst")

    @app.post("/api/v1/topics", status_code=201)
    def create_topic(body: Topic, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        return _write(lambda: repository.create_topic(conn, body, user["id"], client_ip(request), data_dir=request.app.state.data_dir))

    @app.put("/api/v1/topics/{key}")
    def update_topic(key: str, body: Topic, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        if body.key != key:
            raise HTTPException(status_code=422, detail="Der Key im Pfad und im Body müssen übereinstimmen")
        return _write(lambda: repository.update_topic(conn, key, body, user["id"], client_ip(request), data_dir=request.app.state.data_dir))

    @app.delete("/api/v1/topics/{key}", status_code=204, response_class=Response)
    def delete_topic(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> Response:
        _write(lambda: repository.delete_topic(conn, key, user["id"], client_ip(request), data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @app.post("/api/v1/topics/{key}/events", status_code=201)
    def link_event(key: str, body: TopicLink, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        _write(lambda: repository.link_event(conn, key, body.event, user["id"], client_ip(request)))
        return {"topic": key, "event": body.event}

    @app.delete("/api/v1/topics/{key}/events/{event_key}", status_code=204, response_class=Response)
    def unlink_event(key: str, event_key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> Response:
        _write(lambda: repository.unlink_event(conn, key, event_key, user["id"], client_ip(request)))
        return Response(status_code=204)

    @app.post("/api/v1/events/{key}/evidence", status_code=201)
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
        return _write(
            lambda: repository.add_evidence(
                conn, key, check_file(file.filename or "", data), evidence_type, description, user["id"], client_ip(request),
                data_dir=request.app.state.data_dir,
            )
        )

    @app.delete("/api/v1/evidence/{evidence_id}", status_code=204, response_class=Response)
    def delete_evidence(
        evidence_id: int,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        _write(lambda: repository.delete_evidence(conn, evidence_id, user["id"], client_ip(request),
                                                  data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    @app.post("/api/v1/events", status_code=201)
    def create_event(
        body: EventCreate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> dict:
        event = body.model_copy(update={"key": body.key or new_event_key()})
        return _write(
            lambda: repository.create_event(conn, event_of(event), topic_list(event), user["id"], client_ip(request),
                                            data_dir=request.app.state.data_dir)
        )

    @app.put("/api/v1/events/{key}")
    def update_event(
        key: str,
        body: EventUpdate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> dict:
        if body.key != key:
            raise HTTPException(status_code=422, detail="Der Key im Pfad und im Body müssen übereinstimmen")
        return _write(
            lambda: repository.update_event(
                conn, key, event_of(body), topic_list(body), body.version, user["id"], client_ip(request),
                data_dir=request.app.state.data_dir,
            )
        )

    @app.delete("/api/v1/events/{key}", status_code=204, response_class=Response)
    def delete_event(
        key: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(writer),
    ) -> Response:
        _write(lambda: repository.delete_event(conn, key, user["id"], client_ip(request),
                                               data_dir=request.app.state.data_dir))
        return Response(status_code=204)

    # Users and audit log: admins only. The rules live in chronicle.administration.

    @app.get("/api/v1/users")
    def list_users(conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(admin_only)) -> list[dict]:
        return accounts.list_users(conn)

    @app.post("/api/v1/users", status_code=201)
    def create_user(
        body: UserCreate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> dict:
        return _write(lambda: administration.create_account(conn, admin["id"], body.username, body.password, body.role, client_ip(request)))

    @app.patch("/api/v1/users/{user_id}")
    def update_user(
        user_id: int,
        body: UserUpdate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> dict:
        return _write(lambda: administration.update_account(
            conn, admin["id"], user_id, client_ip(request),
            role=body.role, active=body.active, password=body.password,
        ))

    @app.post("/api/v1/users/{user_id}/unlock", status_code=204, response_class=Response)
    def unlock_user(
        user_id: int,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> Response:
        _write(lambda: administration.unlock_account(conn, admin["id"], user_id, client_ip(request)))
        return Response(status_code=204)

    @app.get("/api/v1/topics/{key}/report")
    def topic_report(
        key: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> Response:
        filename, body = _write(lambda: make_report(conn, key, admin["id"], client_ip(request),
                                                    data_dir=request.app.state.data_dir))
        return Response(content=body, media_type="text/html; charset=utf-8", headers=report_headers(filename))

    @app.get("/api/v1/audit")
    def audit_log(
        limit: int = 200,
        action: str | None = None,
        actor: str | None = None,
        since: int | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
        _: dict = Depends(admin_only),
    ) -> list[dict]:
        return accounts.list_audit(conn, limit=min(max(limit, 1), 1000), action=action, actor=actor, since=since)

    return app


def _write(action):
    try:
        return action()
    except repository.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (repository.Invalid, EvidenceRejected) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _bootstrap_admin(path: Path) -> None:
    """Create the first admin from environment variables, only if no account exists yet."""
    username = os.environ.get("CHRONICLE_ADMIN_USER")
    password = os.environ.get("CHRONICLE_ADMIN_PASSWORD")
    if not username or not password:
        return
    with closing(connect(path)) as conn:
        if accounts.count_users(conn) == 0:
            accounts.create_user(conn, username, security.hash_password(password), "admin", must_change=True)
            accounts.audit(conn, "bootstrap_admin", target=username)


def _announce_setup(path: Path, setup: SetupState) -> None:
    """Without any account, print a one-time code for /setup to the server log. Only someone with access to the log can use it."""
    with closing(connect(path)) as conn:
        if accounts.count_users(conn) > 0:
            setup.token = None
            return
    setup.token = security.new_token()
    print(
        "Chronicle: Es gibt noch kein Konto. Den ersten Admin legst du unter /setup an.\n"
        f"Einmal-Code für /setup: {setup.token}",
        file=sys.stderr,
        flush=True,
    )


# Module-level app for `uvicorn chronicle.api:app`. The database path comes from the environment.
app = create_app(os.environ.get("CHRONICLE_DB", DEFAULT_DB), data_dir=os.environ.get("CHRONICLE_DATA") or None)
