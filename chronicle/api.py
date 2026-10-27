"""HTTP app: assembles pages, login and the API routers (chronicle.api_routes), and handles start-up."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, accounts, security
from .admin_pages import make_admin_pages
from .api_routes.admin import make_admin_routes
from .api_routes.events import make_event_routes
from .api_routes.sync import make_sync_routes
from .api_routes.topics import make_topic_routes
from .auth import PasswordChangeRequired, SetupState, make_auth, parse_trusted_proxies
from .db import connect
from .pages import make_pages, not_found_page

DEFAULT_DB = "/data/chronicle.db"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | Path, trusted_proxies: str | None = None, *, data_dir: str | Path) -> FastAPI:
    """`trusted_proxies` defaults to CHRONICLE_TRUSTED_PROXIES, see README.

    The topic files in `data_dir` are the source of truth: edits are written to them, then the index is rebuilt
    (ADR 0008, ADR 0011)."""
    path = Path(db_path)
    setup_state = SetupState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _announce_setup(path, setup_state)  # at server start, not at import
        yield

    app = FastAPI(title="Chronicle", version=__version__, lifespan=lifespan)
    app.state.data_dir = Path(data_dir)
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

    app.include_router(auth.router)
    # Pages of a user who must change the password go to the change form instead.
    app.add_exception_handler(PasswordChangeRequired, lambda request, exc: RedirectResponse("/account/password", status_code=303))
    app.include_router(make_pages(get_conn, auth))

    @app.exception_handler(StarletteHTTPException)
    async def not_found_for_browsers(request: Request, exc: StarletteHTTPException):
        """An unknown address in a browser gets the 404 page (or the login when nobody is signed in), not JSON."""
        wants_page = (
            exc.status_code == 404
            and not request.url.path.startswith("/api/")
            and "text/html" in request.headers.get("accept", "")
        )
        if not wants_page:
            return await http_exception_handler(request, exc)
        try:
            with closing(connect(path)) as conn:
                user = auth.page_session(request, conn)
        except PasswordChangeRequired:
            return RedirectResponse("/account/password", status_code=303)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return not_found_page(request, user)

    app.include_router(make_admin_pages(get_conn, auth))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    _bootstrap_admin(path)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(make_topic_routes(get_conn, auth))
    app.include_router(make_event_routes(get_conn, auth))
    app.include_router(make_sync_routes(get_conn, auth))
    app.include_router(make_admin_routes(get_conn, auth))

    return app


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


def create_app_from_environment() -> FastAPI:
    """The app for `uvicorn --factory chronicle.api:create_app_from_environment`. The paths come from the environment."""
    data_dir = os.environ.get("CHRONICLE_DATA")
    if not data_dir:
        raise RuntimeError("CHRONICLE_DATA fehlt: Chronicle braucht einen Datenordner (ADR 0011).")
    return create_app(os.environ.get("CHRONICLE_DB", DEFAULT_DB), data_dir=data_dir)
