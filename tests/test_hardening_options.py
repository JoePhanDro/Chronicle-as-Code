"""Regressions for the hardening options implemented after audit run-1 (see OPTIONS.md):

- request-body limit: oversized Content-Length is rejected before any framework parsing or gate
- the new-event form refuses more than MAX_FORM_FILES files
- the synchronisation refuses a .git object store over CHRONICLE_SYNC_MAX_GIT_MB
"""

import asyncio
import os

import pytest
from httpx2 import ASGITransport, AsyncClient
from starlette.datastructures import UploadFile


from chronicle import accounts, security, sync
from chronicle.db import connect
from chronicle.evidence import MAX_FORM_FILES
from chronicle.page_routes.events import _attachments_from_form
from tests.conftest import example_folder
from tests.gitutil import remote_of


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "options.db"
    conn = connect(path)
    try:
        accounts.create_user(conn, "anna", security.hash_password("correct horse battery"), "analyst")
    finally:
        conn.close()
    return path


class FakeForm:
    def __init__(self, files):
        self.uploads = files

    def getlist(self, name):
        assert name == "files"
        return self.uploads

    def get(self, name, default=None):
        if name == "evidence_type":
            return "other"
        return default


class TestBodyLimit:
    def make_app(self, db_path, max_bytes=None):
        from chronicle.api import create_app

        if max_bytes is not None:
            os.environ["CHRONICLE_MAX_BODY_BYTES"] = str(max_bytes)
        try:
            return create_app(db_path, data_dir=example_folder(db_path))
        finally:
            if max_bytes is not None:
                os.environ.pop("CHRONICLE_MAX_BODY_BYTES", None)

    def test_oversized_content_length_is_answered_before_the_body(self, db_path):
        app = self.make_app(db_path, max_bytes=1024 * 1024)  # 1 MiB

        async def run():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                declared = {"Content-Type": "application/json", "Content-Length": str(5 * 1024 * 1024)}
                request = client.build_request("POST", "/api/v1/events", headers=declared, content=b"")
                return (await client.send(request)).status_code

        assert asyncio.run(run()) == 413

    def test_normal_requests_pass_the_limit(self, db_path):
        app = self.make_app(db_path, max_bytes=5 * 1024 * 1024)

        async def run():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.post("/api/v1/auth/login", json={"username": "anna",
                                                                          "password": "correct horse battery"})
                return response.status_code

        assert asyncio.run(run()) == 200


class TestFileCountLimit:
    def test_eleven_files_are_refused(self):
        files = []
        for index in range(MAX_FORM_FILES + 1):
            handle = open(os.devnull, "rb")  # pragma: no cover - read is never reached
            files.append((handle, UploadFile(handle, filename=f"f{index}.txt")))
        try:
            with pytest.raises(ValueError, match=f"Höchstens {MAX_FORM_FILES} Dateien"):
                asyncio.run(_attachments_from_form(FakeForm([upload for _, upload in files])))
        finally:
            for handle, _ in files:
                handle.close()

    def test_ten_files_still_work(self, tmp_path):
        files = []
        for index in range(MAX_FORM_FILES):
            path = tmp_path / f"f{index}.txt"
            path.write_text(f"inhalt {index}", encoding="utf-8")
            handle = path.open("rb")
            files.append((handle, UploadFile(handle, filename=f"f{index}.txt")))
        try:
            blobs = asyncio.run(_attachments_from_form(FakeForm([upload for _, upload in files])))
            assert len(blobs) == MAX_FORM_FILES
        finally:
            for handle, _ in files:
                handle.close()


class TestSyncGitVolumeGuard:
    def test_sync_refuses_an_oversized_object_store(self, tmp_path, git_folder, monkeypatch):
        remote_of(tmp_path, git_folder)
        monkeypatch.setenv("CHRONICLE_SYNC_MAX_GIT_MB", "0.01")  # far below any real .git
        with pytest.raises(sync.SyncError, match="CHRONICLE_SYNC_MAX_GIT_MB"):
            sync.synchronise(git_folder)

    def test_a_small_repository_syncs_normally(self, tmp_path, git_folder, monkeypatch):
        remote_of(tmp_path, git_folder)
        monkeypatch.setenv("CHRONICLE_SYNC_MAX_GIT_MB", str(sync.DEFAULT_MAX_GIT_MB))
        result = sync.synchronise(git_folder)
        assert result.problems == [] and result.conflicts == []
