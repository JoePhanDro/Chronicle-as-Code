"""Admin pages: accounts, password reset, login unlock, deleted events and the audit log. Admins only.

Every change calls the same functions as the API (chronicle.administration), so both paths follow the same rules.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import accounts, administration, reading_lock, repository
from .auth import Auth, client_ip
from .pages import TEMPLATES

ROLES = list(accounts.ROLES)
AUDIT_LIMIT = 200


def _forbidden(request: Request, user: dict) -> Response:
    return TEMPLATES.TemplateResponse(request, "forbidden.html", {"user": user}, status_code=403)


def _render_home(request: Request, user: dict, status_code: int = 200, errors: str | None = None) -> Response:
    return TEMPLATES.TemplateResponse(request, "home.html", {"user": user, "errors": errors}, status_code=status_code)


def _render_users(request: Request, conn: sqlite3.Connection, user: dict, status_code: int = 200,
                  errors: str | None = None) -> Response:
    return TEMPLATES.TemplateResponse(
        request,
        "admin_users.html",
        {"user": user, "users": accounts.list_users(conn), "roles": ROLES, "errors": errors},
        status_code=status_code,
    )


def _render_tombstones(request: Request, conn: sqlite3.Connection, user: dict, status_code: int = 200,
                       errors: str | None = None, notice: str | None = None) -> Response:
    return TEMPLATES.TemplateResponse(
        request,
        "admin_tombstones.html",
        {"user": user, "tombstones": repository.list_tombstones(conn), "errors": errors, "notice": notice},
        status_code=status_code,
    )


def _error_status(exc: Exception) -> int:
    if isinstance(exc, repository.NotFound):
        return 404
    if isinstance(exc, repository.Conflict):
        return 409
    return 422


def make_admin_pages(get_conn: Callable[..., Iterator[sqlite3.Connection]], auth: Auth) -> APIRouter:
    router = APIRouter()

    def ok_csrf(user: dict, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(token, user["csrf_token"])

    @router.get("/admin/users", response_class=HTMLResponse)
    def users_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return _forbidden(request, user)
        return _render_users(request, conn, user)

    @router.post("/admin/users/new", response_class=HTMLResponse)
    async def create_user_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            administration.create_account(
                conn, user["id"], (form.get("username") or "").strip(), form.get("password") or "",
                form.get("role") or "viewer", client_ip(request),
            )
        except repository.RepositoryError as exc:
            return _render_users(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/users/{user_id}", response_class=HTMLResponse)
    async def update_user_page(user_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        target = accounts.get_user(conn, user_id)
        if target is None:
            return _render_users(request, conn, user, status_code=404, errors="Benutzer nicht gefunden")

        # Only send what actually changes, so that an unchanged row does not end sessions.
        role = form.get("role") or target["role"]
        active = "active" in form
        try:
            administration.update_account(
                conn, user["id"], user_id, client_ip(request),
                role=role if role != target["role"] else None,
                active=active if active != bool(target["active"]) else None,
            )
        except repository.RepositoryError as exc:
            return _render_users(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/users/{user_id}/password", response_class=HTMLResponse)
    async def reset_password_page(user_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            administration.update_account(conn, user["id"], user_id, client_ip(request), password=form.get("password") or "")
        except repository.RepositoryError as exc:
            return _render_users(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/users/{user_id}/unlock", response_class=HTMLResponse)
    async def unlock_user_page(user_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            administration.unlock_account(conn, user["id"], user_id, client_ip(request))
        except repository.RepositoryError as exc:
            return _render_users(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    # The reading lock is set in the Einstellungen dialog, so these forms answer with the home page.
    @router.post("/admin/sperre", response_class=HTMLResponse)
    async def set_lock_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            reading_lock.set_lock(conn, form.get("password") or "", user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            return _render_home(request, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/", status_code=303)

    @router.post("/admin/sperre/aufheben", response_class=HTMLResponse)
    async def clear_lock_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        reading_lock.clear_lock(conn, user["id"], client_ip(request))
        return RedirectResponse("/", status_code=303)

    @router.get("/admin/grabsteine", response_class=HTMLResponse)
    def tombstones_page(request: Request, notice: str = "", conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return _forbidden(request, user)
        return _render_tombstones(request, conn, user, notice=notice or None)

    @router.post("/admin/grabsteine/{key}/wiederherstellen", response_class=HTMLResponse)
    async def restore_tombstone_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            _, dropped = repository.restore_event(conn, key, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            return _render_tombstones(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        notice = f"Event {key} wiederhergestellt."
        if dropped:
            notice += f" Nicht übernommen, weil es sie nicht mehr gibt: {', '.join(dropped)}."
        return RedirectResponse("/admin/grabsteine?" + urlencode({"notice": notice}), status_code=303)

    @router.post("/admin/grabsteine/{key}/entfernen", response_class=HTMLResponse)
    async def purge_tombstone_page(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] != "admin" or not ok_csrf(user, form.get("csrf_token")):
            return _forbidden(request, user)
        try:
            repository.purge_tombstone(conn, key, user["id"], client_ip(request))
        except repository.RepositoryError as exc:
            return _render_tombstones(request, conn, user, status_code=_error_status(exc), errors=str(exc))
        return RedirectResponse("/admin/grabsteine?" + urlencode({"notice": f"Grabstein {key} entfernt."}), status_code=303)

    @router.get("/admin/audit", response_class=HTMLResponse)
    def audit_page(
        request: Request,
        action: str = "",
        actor: str = "",
        day: str = "",
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        user = auth.page_session(request, conn)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return _forbidden(request, user)

        since = None
        error = None
        if day:
            try:
                since = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
            except ValueError:
                error = "Das Datum muss im Format JJJJ-MM-TT sein"
        entries = accounts.list_audit(conn, limit=AUDIT_LIMIT, action=action or None, actor=actor or None, since=since)
        for entry in entries:
            entry["when"] = datetime.fromtimestamp(entry["ts"], UTC).strftime("%Y-%m-%d %H:%M:%S")
        return TEMPLATES.TemplateResponse(
            request,
            "admin_audit.html",
            {"user": user, "entries": entries, "action": action, "actor": actor, "day": day,
             "errors": error, "limit": AUDIT_LIMIT},
        )

    return router
