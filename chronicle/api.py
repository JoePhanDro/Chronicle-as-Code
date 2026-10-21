"""HTTP app: pages, login, read API for topics and events, admin API for users and audit log."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from . import __version__, accounts, repository, security
from .auth import client_ip, make_auth
from .db import connect

DEFAULT_DB = "/data/chronicle.db"
STATIC_DIR = Path(__file__).parent / "static"


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    role: Literal["admin", "analyst", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "analyst", "viewer"] | None = None
    active: bool | None = None
    password: str | None = None


def create_app(db_path: str | Path) -> FastAPI:
    path = Path(db_path)
    app = FastAPI(title="Chronicle", version=__version__)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(path)
        try:
            yield conn
        finally:
            conn.close()

    cookie_secure = os.environ.get("CHRONICLE_COOKIE_SECURE", "1") != "0"
    auth = make_auth(get_conn, cookie_secure=cookie_secure)
    current_user = auth.current_user
    admin_only = auth.require_role("admin")

    app.include_router(auth.router)
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
    def events(conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> list[dict]:
        return repository.list_events(conn)

    @app.get("/api/v1/events/{key}")
    def event(key: str, conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(current_user)) -> dict:
        found = repository.get_event(conn, key)
        if found is None:
            raise HTTPException(status_code=404, detail="Event nicht gefunden")
        return found

    # Users and audit log: admins only.

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
        try:
            accounts.validate_username(body.username)
            password_hash = security.hash_password(body.password)
            user_id = accounts.create_user(conn, body.username, password_hash, body.role)
        except security.PasswordPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Benutzername ist bereits vergeben") from exc

        accounts.audit(
            conn, "user_created", user_id=admin["id"], target=body.username, detail=f"role={body.role}", ip=client_ip(request)
        )
        return accounts.get_user(conn, user_id)

    @app.patch("/api/v1/users/{user_id}")
    def update_user(
        user_id: int,
        body: UserUpdate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> dict:
        target = accounts.get_user(conn, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        changes: dict = {}
        if body.role is not None:
            changes["role"] = body.role
        if body.active is not None:
            changes["active"] = int(body.active)
        if body.password is not None:
            try:
                changes["password_hash"] = security.hash_password(body.password)
            except security.PasswordPolicyError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Never leave the system without an active admin.
        was_active_admin = target["role"] == "admin" and target["active"]
        loses_admin = (body.role is not None and body.role != "admin") or body.active is False
        if was_active_admin and loses_admin and accounts.count_active_admins(conn) <= 1:
            raise HTTPException(status_code=409, detail="Der letzte aktive Admin kann nicht herabgestuft oder deaktiviert werden")

        accounts.update_user(conn, user_id, **changes)

        # Changing role, deactivating or resetting the password ends the user's sessions.
        if changes:
            accounts.delete_sessions_of_user(conn, user_id)
        if "password_hash" in changes:
            accounts.audit(conn, "password_reset", user_id=admin["id"], target=target["username"], ip=client_ip(request))
        other_changes = sorted(name for name in changes if name != "password_hash")
        if other_changes:
            accounts.audit(
                conn,
                "user_updated",
                user_id=admin["id"],
                target=target["username"],
                detail=",".join(other_changes),
                ip=client_ip(request),
            )
        return accounts.get_user(conn, user_id)

    @app.get("/api/v1/audit")
    def audit_log(
        limit: int = 200,
        conn: sqlite3.Connection = Depends(get_conn),
        _: dict = Depends(admin_only),
    ) -> list[dict]:
        return accounts.list_audit(conn, limit=min(max(limit, 1), 1000))

    return app


def _bootstrap_admin(path: Path) -> None:
    """Create the first admin from environment variables, only if no account exists yet."""
    username = os.environ.get("CHRONICLE_ADMIN_USER")
    password = os.environ.get("CHRONICLE_ADMIN_PASSWORD")
    if not username or not password:
        return
    with closing(connect(path)) as conn:
        if accounts.count_users(conn) == 0:
            accounts.create_user(conn, username, security.hash_password(password), "admin")
            accounts.audit(conn, "bootstrap_admin", target=username)


# Module-level app for `uvicorn chronicle.api:app`. The database path comes from the environment.
app = create_app(os.environ.get("CHRONICLE_DB", DEFAULT_DB))
