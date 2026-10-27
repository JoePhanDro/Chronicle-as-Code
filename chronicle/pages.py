"""HTML pages: the routers of chronicle.page_routes put together. Forms are protected by the session CSRF token."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

from fastapi import APIRouter

from .auth import Auth
from .page_routes.common import TEMPLATES, PageContext, not_found_page
from .page_routes.events import make_event_pages
from .page_routes.general import make_general_pages
from .page_routes.reports import make_report_pages
from .page_routes.topics import make_topic_pages

__all__ = ["TEMPLATES", "make_pages", "not_found_page"]


def make_pages(get_conn: Callable[..., Iterator[sqlite3.Connection]], auth: Auth) -> APIRouter:
    ctx = PageContext(get_conn, auth)
    router = APIRouter()
    # The order matters where a fixed address and a {key} address meet (/events/new before /events/{key}), and that is
    # settled inside each router. The areas do not share addresses.
    router.include_router(make_event_pages(ctx))
    router.include_router(make_general_pages(ctx))
    router.include_router(make_report_pages(ctx))
    router.include_router(make_topic_pages(ctx))
    return router
