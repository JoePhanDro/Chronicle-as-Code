"""Read-only REST API under /api/v1. Writes and authentication follow in later phases."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from . import __version__, repository
from .db import connect

DEFAULT_DB = "/data/chronicle.db"


def create_app(db_path: str | Path) -> FastAPI:
    path = Path(db_path)
    app = FastAPI(title="Chronicle", version=__version__)

    def get_connection() -> Iterator[sqlite3.Connection]:
        conn = connect(path)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/incidents")
    def incidents(conn: sqlite3.Connection = Depends(get_connection)) -> list[dict]:
        return repository.list_incidents(conn)

    @app.get("/api/v1/incidents/{incident_id}")
    def incident(incident_id: int, conn: sqlite3.Connection = Depends(get_connection)) -> dict:
        found = repository.get_incident(conn, incident_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Incident nicht gefunden")
        return found

    @app.get("/api/v1/incidents/{incident_id}/events")
    def events(incident_id: int, conn: sqlite3.Connection = Depends(get_connection)) -> list[dict]:
        if repository.get_incident(conn, incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident nicht gefunden")
        return repository.list_events(conn, incident_id)

    return app


# Module-level app for `uvicorn chronicle.api:app`. The database path comes from the environment.
app = create_app(os.environ.get("CHRONICLE_DB", DEFAULT_DB))
