"""Report pages for admins: the list of topics and the downloads of the HTML and PDF reports."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import repository
from ..auth import client_ip
from ..pdf_report import make_pdf_report
from ..report import make_report, report_headers
from .common import PageContext


def make_report_pages(ctx: PageContext) -> APIRouter:
    get_conn, page_user, render = ctx.get_conn, ctx.page_user, ctx.render
    router = APIRouter()

    @router.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return render(request, "forbidden.html", user, status_code=403)
        return render(request, "reports.html", user, topics=repository.list_topics(conn))

    @router.get("/reports/{key}")
    def report_download(key: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return render(request, "forbidden.html", user, status_code=403)
        try:
            filename, body = make_report(conn, key, user["id"], client_ip(request), data_dir=request.app.state.data_dir)
        except repository.NotFound:
            return render(request, "not_found.html", user, status_code=404)
        return Response(content=body, media_type="text/html; charset=utf-8", headers=report_headers(filename))

    @router.get("/reports/{key}/pdf")
    def report_pdf_download(key: str, request: Request, art: str = "technisch", conn: sqlite3.Connection = Depends(get_conn), user: dict | None = Depends(page_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "admin":
            return render(request, "forbidden.html", user, status_code=403)
        try:
            filename, body = make_pdf_report(conn, key, user["id"], client_ip(request), art, data_dir=request.app.state.data_dir)
        except repository.NotFound:
            return render(request, "not_found.html", user, status_code=404)
        return Response(content=body, media_type="application/pdf", headers=report_headers(filename))

    return router
