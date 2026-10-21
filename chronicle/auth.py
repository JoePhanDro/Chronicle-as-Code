"""Login, logout and the dependencies that protect routes (session cookie, CSRF token, roles)."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from . import accounts, security

SESSION_COOKIE = "chronicle_session"
LOGIN_CSRF_COOKIE = "chronicle_login_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOGIN_CSRF_TTL_SECONDS = 15 * 60

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


@dataclass
class Auth:
    router: APIRouter
    current_user: Callable[..., dict]
    require_role: Callable[..., Callable[..., dict]]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def make_auth(get_conn: Callable[..., Iterator[sqlite3.Connection]], cookie_secure: bool) -> Auth:
    router = APIRouter()

    def session_user(request: Request, conn: sqlite3.Connection) -> dict | None:
        token = request.cookies.get(SESSION_COOKIE)
        return accounts.find_session(conn, token) if token else None

    def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        session = session_user(request, conn)
        if session is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(sent, session["csrf_token"]):
                raise HTTPException(status_code=403, detail="CSRF-Token fehlt oder ist ungültig")
        return session

    def require_role(*roles: str) -> Callable[..., dict]:
        def dependency(user: dict = Depends(current_user)) -> dict:
            if user["role"] not in roles:
                raise HTTPException(status_code=403, detail="Keine Berechtigung")
            return user

        return dependency

    def authenticate(conn: sqlite3.Connection, username: str, password: str, ip: str) -> tuple[dict, str]:
        if accounts.login_blocked(conn, username, ip):
            accounts.audit(conn, "login_blocked", target=username, ip=ip)
            raise HTTPException(status_code=429, detail="Zu viele Fehlversuche. Bitte später erneut versuchen.")

        row = accounts.get_user_by_name(conn, username)
        # Check the hash even for unknown users, so response times do not reveal which names exist.
        stored_hash = row["password_hash"] if row else security.DUMMY_HASH
        password_ok = security.verify_password(stored_hash, password)

        if row is None or not password_ok or not row["active"]:
            accounts.record_login_failure(conn, username, ip)
            accounts.audit(conn, "login_failed", target=username, ip=ip)
            raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch")

        accounts.clear_login_failures(conn, username)
        token, _ = accounts.create_session(conn, row["id"])
        accounts.audit(conn, "login", user_id=row["id"], ip=ip)
        return {"id": row["id"], "username": row["username"], "role": row["role"]}, token

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=accounts.SESSION_TTL_SECONDS,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/",
        )

    def render_login(request: Request, error: str | None, status_code: int) -> HTMLResponse:
        login_csrf = secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request, "login.html", {"csrf_token": login_csrf, "error": error}, status_code=status_code
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            login_csrf,
            max_age=LOGIN_CSRF_TTL_SECONDS,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/login",
        )
        return response

    # HTML pages

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return render_login(request, error=None, status_code=200)

    @router.post("/login", response_class=HTMLResponse)
    def login_form(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        expected = request.cookies.get(LOGIN_CSRF_COOKIE, "")
        if not expected or not secrets.compare_digest(csrf_token, expected):
            raise HTTPException(status_code=403, detail="Ungültiges Formular. Bitte Seite neu laden.")
        try:
            _, token = authenticate(conn, username, password, client_ip(request))
        except HTTPException as exc:
            return render_login(request, error=str(exc.detail), status_code=exc.status_code)

        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
        return response

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        session = session_user(request, conn)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(request, "home.html", {"user": session})

    @router.post("/logout")
    def logout_form(
        request: Request,
        csrf_token: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        session = session_user(request, conn)
        if session is None or not secrets.compare_digest(csrf_token, session["csrf_token"]):
            raise HTTPException(status_code=403, detail="Ungültiges Formular")
        accounts.delete_session(conn, request.cookies[SESSION_COOKIE])
        accounts.audit(conn, "logout", user_id=session["id"], ip=client_ip(request))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # JSON API

    @router.post("/api/v1/auth/login")
    def login_api(
        body: LoginRequest,
        request: Request,
        response: Response,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        user, token = authenticate(conn, body.username, body.password, client_ip(request))
        set_session_cookie(response, token)
        session = accounts.find_session(conn, token)
        return {"user": user, "csrf_token": session["csrf_token"]}

    @router.post("/api/v1/auth/logout", status_code=204)
    def logout_api(
        request: Request,
        user: dict = Depends(current_user),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        accounts.delete_session(conn, request.cookies[SESSION_COOKIE])
        accounts.audit(conn, "logout", user_id=user["id"], ip=client_ip(request))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/api/v1/auth/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return {"id": user["id"], "username": user["username"], "role": user["role"], "csrf_token": user["csrf_token"]}

    return Auth(router=router, current_user=current_user, require_role=require_role)
