"""Regressions for the security-audit fixes beyond retention:

- default docs routes are off (confidential by default, audit lead chronic/api.py:default-docs...)
- cycle_problems walks the trigger graph iteratively (no RecursionError on deep legal chains)
- evidence deletion unlinks inside the folder transaction (no orphaned index row over a deleted file)
- live database files are created owner-only (0600 like the backup copies)
"""

import asyncio
import json
import os
import stat
import threading

import pytest
from httpx2 import ASGITransport, AsyncClient

from chronicle import accounts, evidence_store, security
from chronicle.check import check_folder, cycle_problems
from chronicle.db import connect
from chronicle.evidence import check_file
from chronicle.importer import import_folder
from tests.conftest import example_folder


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "audit-fixes.db"
    conn = connect(path)
    try:
        accounts.create_user(conn, "anna", security.hash_password("correct horse battery"), "analyst")
    finally:
        conn.close()
    return path


@pytest.fixture
def conn_and_folder(db_path):
    folder = example_folder(db_path)
    import_folder(db_path, folder)
    conn = connect(db_path)
    yield conn, folder
    conn.close()


class TestDocsEndpointsAreOff:
    def test_no_openapi_schema_for_anonymous_visitors(self, db_path):
        from chronicle.api import create_app

        app = create_app(db_path, data_dir=example_folder(db_path))

        async def run():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                schema = await client.get("/openapi.json")
                docs = await client.get("/docs")
                redoc = await client.get("/redoc")
                gated = await client.get("/api/v1/topics")
                return schema.status_code, docs.status_code, redoc.status_code, gated.status_code

        schema, docs, redoc, gated = asyncio.run(run())
        assert (schema, docs, redoc) == (404, 404, 404)  # a confidential app has no anonymous API map
        assert gated == 401  # the own routes still answer


class TestCycleCheckIsIterative:
    def _chain(self, count, reverse):
        events = []
        for index in range(count):
            minute, second = divmod(index, 60)
            events.append({
                "key": f"k{index:05d}",
                "t0": f"2026-01-01T{minute // 60:02d}:{minute % 60:02d}:{second:02d}Z",
                "phase": "Identification", "actor": "t", "action": f"event {index}", "status": "resolved",
                "triggered_by": [f"k{index - 1:05d}"] if index else [],
            })
        if reverse:
            events.reverse()
        return events

    @pytest.mark.parametrize("reverse", [False, True])
    def test_deep_legal_chain_reports_no_problems(self, db_path, tmp_path, reverse):
        data = tmp_path / "chain"
        data.mkdir()
        # Well past the default recursion limit of 1000; before the fix this raised RecursionError.
        events = self._chain(2500, reverse)
        (data / "chain.yaml").write_text(
            json.dumps({"topic": {"key": "chain", "title": "chain", "kind": "project", "summary": ""},
                        "events": events}),
            encoding="utf-8",
        )
        assert check_folder(data, evidence=False) == []

    def test_cycle_problems_still_find_every_cycle(self):
        assert cycle_problems({"a": ["b"], "b": ["a"], "c": ["c"]}) == [
            "Auslösungen bilden einen Kreis: a → b → a",
            "Auslösungen bilden einen Kreis: c → c",
        ]
        assert cycle_problems({"a": ["b"], "b": []}) == []


class TestEvidenceDeleteIsTransactional:
    def test_concurrent_identical_upload_during_delete_cannot_orphan(self, conn_and_folder):
        conn, data_dir = conn_and_folder
        event_key = conn.execute("SELECT key FROM events LIMIT 1").fetchone()["key"]
        blob = check_file("beispiel.txt", b"raced-bytes\n")
        evidence_store.add_evidence(conn, event_key, blob, "other", "r", 1, "127.0.0.1", data_dir)
        evidence_id = conn.execute("SELECT pk FROM evidence ORDER BY pk DESC LIMIT 1").fetchone()["pk"]
        rel_file = conn.execute("SELECT file FROM evidence WHERE pk = ?", (evidence_id,)).fetchone()["file"]

        # Park the delete thread between the committed transaction and its return; with the fix the
        # unlink has already run, so a concurrent identical upload re-creates the file and stays valid.
        real_all_or_nothing = evidence_store.all_or_nothing
        committed = threading.Barrier(2)
        resumed = threading.Barrier(2)
        parking = {"on": True}

        class Parking:
            def __init__(self, cm):
                self.cm = cm

            def __enter__(self):
                return self.cm.__enter__()

            def __exit__(self, *exc):
                result = self.cm.__exit__(*exc)
                if parking["on"]:
                    parking["on"] = False
                    committed.wait()
                    resumed.wait()
                return result

        def wrapper(*args, **kwargs):
            return Parking(real_all_or_nothing(*args, **kwargs))

        evidence_store.all_or_nothing = wrapper
        try:
            errors = []

            def delete():
                try:
                    evidence_store.delete_evidence(conn, evidence_id, 1, "127.0.0.1", data_dir)
                except Exception as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            thread = threading.Thread(target=delete)
            thread.start()
            committed.wait()  # the delete transaction (including the unlink) is complete and parked

            evidence_store.add_evidence(conn, event_key, blob, "other", "r2", 1, "127.0.0.1", data_dir)
            resumed.wait()
            thread.join()
            assert errors == []
        finally:
            evidence_store.all_or_nothing = real_all_or_nothing

        stored = conn.execute("SELECT COUNT(*) FROM evidence WHERE file = ?", (rel_file,)).fetchone()[0]
        assert stored == 1  # exactly the concurrent upload's row; its file exists and the folder stays valid
        assert (data_dir / rel_file).is_file()
        assert check_folder(data_dir, evidence=False) == []


class TestLiveDatabasesAreOwnerOnly:
    def test_new_live_databases_are_created_0600(self, tmp_path):
        os.umask(0o022)
        conn = connect(tmp_path / "chronicle.db")
        try:
            accounts.create_user(conn, "dummy", security.hash_password("dummy-passwort-42"), "viewer")
        finally:
            conn.close()
        assert stat.S_IMODE(os.stat(tmp_path / "chronicle.db").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "chronicle.ops.db").st_mode) == 0o600
