"""Admins only: users, the audit log and the reports of a topic. The rules live in chronicle.administration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from .. import accounts, administration
from ..auth import Auth, client_ip
from ..pdf_report import make_pdf_report
from ..report import make_report, report_headers
from .common import write


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str
    password: str
    role: Literal["admin", "analyst", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    role: Literal["admin", "analyst", "viewer"] | None = None
    active: bool | None = None
    password: str | None = None


def make_admin_routes(get_conn: Callable, auth: Auth) -> APIRouter:
    router = APIRouter()
    admin_only = auth.require_role("admin")

    @router.get("/api/v1/users")
    def list_users(conn: sqlite3.Connection = Depends(get_conn), _: dict = Depends(admin_only)) -> list[dict]:
        return accounts.list_users(conn)

    @router.post("/api/v1/users", status_code=201)
    def create_user(
        body: UserCreate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> dict:
        return write(lambda: administration.create_account(conn, admin["id"], body.username, body.password, body.role, client_ip(request)))

    @router.patch("/api/v1/users/{user_id}")
    def update_user(
        user_id: int,
        body: UserUpdate,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> dict:
        return write(lambda: administration.update_account(
            conn, admin["id"], user_id, client_ip(request),
            role=body.role, active=body.active, password=body.password,
        ))

    @router.post("/api/v1/users/{user_id}/unlock", status_code=204, response_class=Response)
    def unlock_user(
        user_id: int,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> Response:
        write(lambda: administration.unlock_account(conn, admin["id"], user_id, client_ip(request)))
        return Response(status_code=204)

    @router.get("/api/v1/topics/{key}/report")
    def topic_report(
        key: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> Response:
        filename, body = write(lambda: make_report(conn, key, admin["id"], client_ip(request),
                                                   data_dir=request.app.state.data_dir))
        return Response(content=body, media_type="text/html; charset=utf-8", headers=report_headers(filename))

    @router.get("/api/v1/topics/{key}/report.pdf")
    def topic_report_pdf(
        key: str,
        request: Request,
        art: str = "technisch",
        conn: sqlite3.Connection = Depends(get_conn),
        admin: dict = Depends(admin_only),
    ) -> Response:
        filename, body = write(lambda: make_pdf_report(conn, key, admin["id"], client_ip(request), art,
                                                       data_dir=request.app.state.data_dir))
        return Response(content=body, media_type="application/pdf", headers=report_headers(filename))

    @router.get("/api/v1/audit")
    def audit_log(
        limit: int = 200,
        action: str | None = None,
        actor: str | None = None,
        since: int | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
        _: dict = Depends(admin_only),
    ) -> list[dict]:
        return accounts.list_audit(conn, limit=min(max(limit, 1), 1000), action=action, actor=actor, since=since)

    return router
