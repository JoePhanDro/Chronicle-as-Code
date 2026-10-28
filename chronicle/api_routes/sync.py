"""Abgleichen (issue #90): what a synchronisation with the Git remote would change, and the synchronisation itself."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import repository, sync
from ..auth import Auth, client_ip


def make_sync_routes(get_conn: Callable, auth: Auth) -> APIRouter:
    router = APIRouter()
    writer = auth.require_role("admin", "analyst")

    @router.get("/api/v1/sync/preview")
    def sync_preview(request: Request, _: dict = Depends(writer)) -> dict:
        try:
            result = sync.preview(request.app.state.data_dir)
        except sync.SyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "problems": result.problems,
            "changes": [{"action": item.action, "key": item.key, "file": item.file} for item in result.changes],
        }

    @router.post("/api/v1/sync")
    def sync_now(request: Request, conn: sqlite3.Connection = Depends(get_conn), user: dict = Depends(writer)) -> dict:
        """Commit the topic files, take in the remote's state, push. After a merge the index is rebuilt (issue #90)."""
        data_dir = request.app.state.data_dir
        try:
            result = sync.synchronise(data_dir)
        except sync.SyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result.merged:
            repository.reindex(conn, data_dir)
        sync.audit_run(conn, user["id"], client_ip(request), result)
        return {"committed": result.committed, "merged": result.merged, "conflicts": result.conflicts,
                "problems": result.problems}

    return router
