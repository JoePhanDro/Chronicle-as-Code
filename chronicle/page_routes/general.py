"""Pages that belong to no single object: search, help and reading the topic files again."""

from __future__ import annotations

import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import accounts, repository
from ..auth import client_ip
from ..loader import TimelineError
from .common import WRITER_ROLES, PageContext, _text_param


SEARCH_EVENT_LIMIT = 20


def _local_path(value) -> str:
    """The address of a page of this app, from a form field; anything else (other host, odd characters) is the start page."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


def _with_param(path: str, name: str, value: str) -> str:
    """`path` with one query parameter set, others kept."""
    base, _, query = path.partition("?")
    kept = [pair for pair in query.split("&") if pair and not pair.startswith(f"{name}=")]
    return base + "?" + "&".join([*kept, urlencode({name: value})])


def make_general_pages(ctx: PageContext) -> APIRouter:
    get_conn, page_user, ok_csrf, render = ctx.get_conn, ctx.page_user, ctx.ok_csrf, ctx.render
    router = APIRouter()

    @router.get("/suche", response_class=HTMLResponse)
    def search_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        text = _text_param(request.query_params, "q")
        if text is None:
            return render(request, "search.html", user, text="", events=[], total=0, actors=[], systems=[])
        flt = repository.EventFilter(query=text)
        return render(request, "search.html", user, text=text,
                      events=repository.find_events(conn, flt, limit=SEARCH_EVENT_LIMIT),
                      total=repository.count_events(conn, flt),
                      actors=repository.search_actors(conn, text), systems=repository.search_systems(conn, text))

    @router.get("/hilfe", response_class=HTMLResponse)
    def help_page(request: Request, user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return render(request, "help.html", user)

    @router.post("/neu-laden", response_class=HTMLResponse)
    async def reload_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        """Read the topic files again, for changes made in an editor or by git. The page the person came from reopens."""
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        back = _local_path(form.get("next"))
        try:
            repository.reindex(conn, request.app.state.data_dir)
        except TimelineError as exc:  # a file that fails the checks: nothing was written, the index stays as it was
            problems = getattr(exc, "problems", None) or [str(exc)]
            return render(request, "reload.html", user, status_code=422, back=back, problems=problems)
        except (repository.RepositoryError, ValueError, OSError) as exc:
            return render(request, "reload.html", user, status_code=422, back=back, problems=[str(exc)])
        accounts.audit(conn, "reindex", user_id=user["id"], ip=client_ip(request))
        return RedirectResponse(_with_param(back, "neu_geladen", "1"), status_code=303)

    return router
