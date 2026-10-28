"""The Abgleichen page: the preview of what a synchronisation would change, the button, and the outcome of the run."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .. import repository, sync
from ..auth import client_ip
from .common import WRITER_ROLES, PageContext


CHOICE_PREFIX = "wahl:"  # the form field of a conflicting file is "wahl:<file name>", its value "ours" or "theirs"


def make_sync_pages(ctx: PageContext) -> APIRouter:
    get_conn, page_user, ok_csrf, render = ctx.get_conn, ctx.page_user, ctx.ok_csrf, ctx.render
    router = APIRouter()

    def render_sync(request: Request, user: dict, *, status_code: int = 200, error: str | None = None,
                    result: sync.SyncResult | None = None) -> HTMLResponse:
        """The page with the preview of what would change, and the outcome of the last run."""
        preview = None
        try:
            preview = sync.preview(request.app.state.data_dir)
        except sync.SyncError as exc:
            error = error or str(exc)
        return render(request, "sync.html", user, status_code=status_code, error=error, preview=preview, result=result)

    @router.get("/abgleich", response_class=HTMLResponse)
    def sync_page(request: Request, user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] not in WRITER_ROLES:
            return render(request, "forbidden.html", user, status_code=403)
        return render_sync(request, user)

    @router.post("/abgleich", response_class=HTMLResponse)
    async def sync_run_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user["role"] not in WRITER_ROLES or not ok_csrf(user, form.get("csrf_token")):
            return render(request, "forbidden.html", user, status_code=403)
        data_dir = request.app.state.data_dir
        choices = {key[len(CHOICE_PREFIX):]: value for key, value in form.items() if key.startswith(CHOICE_PREFIX)}
        try:
            # Git talks to the remote: that must not block the event loop. The index is rebuilt on this thread,
            # because the database connection belongs to it.
            result = await run_in_threadpool(sync.synchronise, data_dir, choices)
            if result.merged:
                repository.reindex(conn, data_dir)
        except sync.SyncError as exc:
            return await run_in_threadpool(render_sync, request, user, status_code=409, error=str(exc))
        sync.audit_run(conn, user["id"], client_ip(request), result, choices)
        return await run_in_threadpool(render_sync, request, user, result=result)

    return router
