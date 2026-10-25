"""Login, logout and the dependencies that protect routes (session cookie, CSRF token, roles)."""

from __future__ import annotations

import ipaddress
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

from . import accounts, reading_lock, security

SESSION_COOKIE = "chronicle_session"
LOGIN_CSRF_COOKIE = "chronicle_login_csrf"
SETUP_CSRF_COOKIE = "chronicle_setup_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# While a password must be changed, only these paths are open to the user.
PASSWORD_CHANGE_PATHS = {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/password", "/logout", "/account/password"}
LOGIN_CSRF_TTL_SECONDS = 15 * 60

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str
    password: str


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    current_password: str
    new_password: str


@dataclass
class SetupState:
    """The one-time code for the first-start page. Set while no account exists, None afterwards."""

    token: str | None = None


class PasswordChangeRequired(Exception):
    """Raised for pages while the user must change the password. The app redirects to the change form."""


@dataclass
class Auth:
    router: APIRouter
    current_user: Callable[..., dict]
    require_role: Callable[..., Callable[..., dict]]
    session_user: Callable[[Request, sqlite3.Connection], dict | None]
    page_session: Callable[[Request, sqlite3.Connection], dict | None]


def parse_trusted_proxies(value: str | None) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Comma separated IP addresses or networks, e.g. '10.0.0.1, 172.18.0.0/16'. Empty means no proxy is trusted."""
    networks = []
    for item in (value or "").split(","):
        item = item.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


def client_ip(request: Request) -> str:
    """The address of the client. X-Forwarded-For counts only if the connection comes from a trusted proxy."""
    peer = request.client.host if request.client else "unknown"
    trusted = getattr(request.app.state, "trusted_proxies", ())
    if not _is_trusted(peer, trusted):
        return peer

    # Walk from the nearest hop back to the client; the first address that is not a trusted proxy is the client.
    for hop in reversed([part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]):
        try:
            address = ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not _is_trusted(str(address), trusted):
            return str(address)
    return peer


def _is_trusted(address: str, networks: tuple) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def make_auth(get_conn: Callable[..., Iterator[sqlite3.Connection]], cookie_secure: bool, setup: SetupState | None = None) -> Auth:
    router = APIRouter()
    setup = setup or SetupState()

    def session_user(request: Request, conn: sqlite3.Connection) -> dict | None:
        token = request.cookies.get(SESSION_COOKIE)
        return accounts.find_session(conn, token) if token else None

    def page_session(request: Request, conn: sqlite3.Connection) -> dict | None:
        session = session_user(request, conn)
        if session and session["must_change_password"] and request.url.path not in PASSWORD_CHANGE_PATHS:
            raise PasswordChangeRequired()
        return session

    def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        session = session_user(request, conn)
        if session is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        if session["must_change_password"] and request.url.path not in PASSWORD_CHANGE_PATHS:
            raise HTTPException(status_code=403, detail="Passwortwechsel erforderlich")
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

        accounts.clear_login_failures(conn, username, ip)
        token, _ = accounts.create_session(conn, row["id"])
        accounts.audit(conn, "login", user_id=row["id"], ip=ip)
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }, token

    def change_password(conn: sqlite3.Connection, user: dict, token: str, current: str, new: str, ip: str) -> None:
        stored = accounts.password_hash_of(conn, user["id"])
        if stored is None or not security.verify_password(stored, current):
            raise HTTPException(status_code=403, detail="Das aktuelle Passwort ist falsch")
        if current == new:
            raise HTTPException(status_code=422, detail="Das neue Passwort muss sich vom alten unterscheiden")
        try:
            new_hash = security.hash_password(new)
        except security.PasswordPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        accounts.set_password(conn, user["id"], new_hash, must_change=False)
        # Other devices lose their sessions; this one stays signed in.
        accounts.delete_other_sessions(conn, user["id"], token)
        accounts.audit(conn, "password_changed", user_id=user["id"], ip=ip)

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

    def render_login(request: Request, error: str | None, status_code: int, lock_enabled: bool = False) -> HTMLResponse:
        login_csrf = secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": login_csrf, "error": error, "lock_enabled": lock_enabled},
            status_code=status_code,
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

    # First start: while no account exists, /setup creates the first admin. It needs the one-time code that the
    # server printed at startup, so whoever reaches the page first cannot take over the system.

    def render_setup(request: Request, error: str | None, status_code: int) -> HTMLResponse:
        setup_csrf = secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request,
            "setup.html",
            {"csrf_token": setup_csrf, "error": error, "min_length": security.MIN_PASSWORD_LENGTH},
            status_code=status_code,
        )
        response.set_cookie(
            SETUP_CSRF_COOKIE,
            setup_csrf,
            max_age=LOGIN_CSRF_TTL_SECONDS,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/setup",
        )
        return response

    @router.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        if accounts.count_users(conn) > 0 or setup.token is None:
            return RedirectResponse("/login", status_code=303)
        return render_setup(request, error=None, status_code=200)

    @router.post("/setup", response_class=HTMLResponse)
    def setup_form(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        setup_code: str = Form(...),
        csrf_token: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        if accounts.count_users(conn) > 0 or setup.token is None:
            return RedirectResponse("/login", status_code=303)
        expected = request.cookies.get(SETUP_CSRF_COOKIE, "")
        if not expected or not secrets.compare_digest(csrf_token, expected):
            raise HTTPException(status_code=403, detail="Ungültiges Formular. Bitte Seite neu laden.")
        if accounts.setup_blocked(conn):
            return render_setup(request, error="Zu viele Fehlversuche. Bitte später erneut versuchen.", status_code=429)
        if not secrets.compare_digest(setup_code.strip().encode(), setup.token.encode()):
            accounts.record_setup_failure(conn, client_ip(request))
            return render_setup(request, error="Der Einmal-Code stimmt nicht. Er steht im Log des Servers.", status_code=403)

        try:
            password_hash = security.hash_password(password)
            user_id = accounts.create_first_admin(conn, username.strip(), password_hash)
        except ValueError as exc:  # includes the password policy
            return render_setup(request, error=str(exc), status_code=422)
        if user_id is None:  # someone else was faster
            return RedirectResponse("/login", status_code=303)

        setup.token = None
        accounts.audit(conn, "setup_completed", user_id=user_id, target=username.strip(), ip=client_ip(request))
        token, _ = accounts.create_session(conn, user_id)
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(SETUP_CSRF_COOKIE, path="/setup")
        return response

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        if accounts.count_users(conn) == 0:
            return RedirectResponse("/setup", status_code=303)
        return render_login(request, error=None, status_code=200, lock_enabled=accounts.reading_lock_enabled(conn))

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
            return render_login(request, error=str(exc.detail), status_code=exc.status_code,
                                lock_enabled=accounts.reading_lock_enabled(conn))

        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
        return response

    # Under /login, so that the login CSRF cookie (path /login) is sent with it.
    @router.post("/login/entsperren", response_class=HTMLResponse)
    def unlock_form(
        request: Request,
        password: str = Form(...),
        csrf_token: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        expected = request.cookies.get(LOGIN_CSRF_COOKIE, "")
        if not expected or not secrets.compare_digest(csrf_token, expected):
            raise HTTPException(status_code=403, detail="Ungültiges Formular. Bitte Seite neu laden.")
        try:
            token = reading_lock.unlock(conn, password, client_ip(request))
        except reading_lock.Refused as exc:
            return render_login(request, error=exc.message, status_code=exc.status_code, lock_enabled=True)

        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
        return response

    @router.get("/account/password", response_class=HTMLResponse)
    def password_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        session = session_user(request, conn)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(request, "password.html", {"user": session, "error": None})

    @router.post("/account/password", response_class=HTMLResponse)
    def password_form(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        csrf_token: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        session = session_user(request, conn)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        if not secrets.compare_digest(csrf_token, session["csrf_token"]):
            raise HTTPException(status_code=403, detail="Ungültiges Formular")
        try:
            change_password(conn, session, request.cookies[SESSION_COOKIE], current_password, new_password, client_ip(request))
        except HTTPException as exc:
            return TEMPLATES.TemplateResponse(
                request, "password.html", {"user": session, "error": exc.detail}, status_code=exc.status_code
            )
        return RedirectResponse("/", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        session = page_session(request, conn)
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

    @router.post("/api/v1/auth/password", status_code=204, response_class=Response)
    def password_api(
        body: PasswordChange,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        user: dict = Depends(current_user),
    ) -> Response:
        change_password(conn, user, request.cookies[SESSION_COOKIE], body.current_password, body.new_password, client_ip(request))
        return Response(status_code=204)

    @router.get("/api/v1/auth/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return {"id": user["id"], "username": user["username"], "role": user["role"], "csrf_token": user["csrf_token"]}

    return Auth(
        router=router,
        current_user=current_user,
        require_role=require_role,
        session_user=session_user,
        page_session=page_session,
    )
