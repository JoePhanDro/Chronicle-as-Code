"""File mode (ADR 0008, issue #89): the index keeps no copy of the attachments. The bytes come from the data folder."""

import sqlite3

import pytest

from chronicle import repository
from chronicle.exporter import ExportError, export_database
from chronicle.models import Event
from chronicle.repository import Conflict
from chronicle.report import make_report


def edit_in_file_mode(conn, folder, key="analysis"):
    current = repository.get_event(conn, key)
    data = {k: current[k] for k in ("key", "t0", "phase", "actor", "system", "action", "status", "triggered_by",
                                    "details")}
    data["action"] = data["action"] + " (geändert)"
    return repository.update_event(conn, key, Event.model_validate(data), current["topics"], current["version"],
                                   actor_id=None, ip="test", data_dir=folder)


def stored_contents(folder):
    raw = sqlite3.connect(folder.parent / "chronicle.db")
    try:
        return [row[0] for row in raw.execute("SELECT content FROM evidence")]
    finally:
        raw.close()


def test_the_index_holds_no_attachment_bytes_after_a_file_mode_edit(folder, conn):
    assert any(content is not None for content in stored_contents(folder))  # the apply run stored them

    edit_in_file_mode(conn, folder)

    assert all(content is None for content in stored_contents(folder))
    assert folder.joinpath("evidence").is_dir()  # the files are still there


def test_the_download_reads_the_bytes_from_the_file(folder, conn):
    edit_in_file_mode(conn, folder)
    item = repository.get_event(conn, "analysis")["evidence"][0]

    found = repository.get_evidence_content(conn, item["id"], folder)

    assert found["content"] == (folder / item["file"]).read_bytes()


def test_a_changed_file_is_not_served(folder, conn):
    edit_in_file_mode(conn, folder)
    item = repository.get_event(conn, "analysis")["evidence"][0]
    (folder / item["file"]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"veraendert" * 4)

    with pytest.raises(Conflict, match="außerhalb"):
        repository.get_evidence_content(conn, item["id"], folder)


def test_export_and_report_take_the_bytes_from_the_data_folder(folder, conn, tmp_path):
    edit_in_file_mode(conn, folder)

    with pytest.raises(ExportError, match="Datenordner"):
        export_database(folder.parent / "chronicle.db", tmp_path / "export-ohne")
    export_database(folder.parent / "chronicle.db", tmp_path / "export-mit", folder)
    assert any((tmp_path / "export-mit").rglob("*.png"))

    filename, body = make_report(conn, "inc-2026-042", actor_id=None, ip="test", data_dir=folder)
    assert filename == "bericht-inc-2026-042.html"
    assert b"data:image/png;base64," in body
