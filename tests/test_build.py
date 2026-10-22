"""chronicle build: a static view from the files, without database or server (ADR 0009, issue #92)."""

from pathlib import Path

import pytest

from chronicle.build import STYLESHEET, build_site
from chronicle.cli import main
from chronicle.loader import CheckFailed

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def event(key, action, triggered_by=None, evidence=None):
    lines = [
        f"  - key: {key}",
        "    t0: 2026-10-14T08:40:00+02:00",
        "    phase: Exploit",
        "    actor: Angreifer",
        f"    action: {action}",
        "    status: open",
    ]
    if triggered_by:
        lines.append(f"    triggered_by: {triggered_by}")
    if evidence:
        lines += ["    evidence:", f"      - file: {evidence}", "        type: screenshot"]
    return "\n".join(lines)


def write_topic(folder, name, topic_key, title="Titel", events=(), refs=()):
    body = f"topic:\n  key: {topic_key}\n  title: '{title}'\n"
    if events:
        body += "events:\n" + "\n".join(events) + "\n"
    if refs:
        body += "refs:\n" + "".join(f"  - {ref}\n" for ref in refs)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(body, encoding="utf-8")


@pytest.fixture
def folder(tmp_path):
    if not STYLESHEET.is_file():
        pytest.skip("stylesheet not built (npm run build:css)")
    data = tmp_path / "data"
    (data / "evidence").mkdir(parents=True)
    (data / "evidence" / "shot.png").write_bytes(PNG)
    write_topic(
        data,
        "inc.yaml",
        "inc",
        title="Vorfall",
        events=[
            event("exploit-1", "Ausnutzung erfolgreich", triggered_by="cve-1", evidence="evidence/shot.png"),
        ],
        refs=["cve-1"],
    )
    write_topic(data, "cve.yaml", "cve", title="Schwachstelle", events=[event("cve-1", "Advisory published")])
    return data


def test_one_page_per_topic_and_an_index(folder, tmp_path):
    out = tmp_path / "site"
    written = build_site(folder, out)

    assert {path.name for path in written} == {"index.html", "inc.html", "cve.html"}
    assert (out / "inc.html").is_file()


def test_topic_page_shows_its_own_and_referenced_events(folder, tmp_path):
    build_site(folder, tmp_path / "site")
    page = (tmp_path / "site" / "inc.html").read_text(encoding="utf-8")

    assert "Ausnutzung erfolgreich" in page
    assert "Advisory published" in page  # referenced via refs, defined in cve.yaml


def test_pages_are_self_contained(folder, tmp_path):
    build_site(folder, tmp_path / "site")
    page = (tmp_path / "site" / "inc.html").read_text(encoding="utf-8")

    assert 'href="/static/' not in page and 'src="/static/' not in page  # no server assets
    assert "data:image/png;base64," in page  # evidence embedded
    assert "Berichts-Snapshot" in page  # marked as a snapshot, like the report


def test_index_lists_topics_and_escapes_titles(tmp_path):
    if not STYLESHEET.is_file():
        pytest.skip("stylesheet not built (npm run build:css)")
    data = tmp_path / "data"
    write_topic(data, "x.yaml", "x", title="<script>alert(1)</script>", events=[event("x-1", "Schritt")])
    build_site(data, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")

    assert "&lt;script&gt;" in index
    assert "<script>alert(1)" not in index
    assert 'href="x.html"' in index


def test_build_is_reproducible(folder, tmp_path):
    build_site(folder, tmp_path / "first")
    build_site(folder, tmp_path / "second")
    for name in ("index.html", "inc.html", "cve.html"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_invalid_folder_writes_nothing(tmp_path):
    if not STYLESHEET.is_file():
        pytest.skip("stylesheet not built (npm run build:css)")
    data = tmp_path / "data"
    write_topic(data, "a.yaml", "gleich", events=[event("a-1", "A")])
    write_topic(data, "b.yaml", "gleich", events=[event("b-1", "B")])
    out = tmp_path / "site"

    with pytest.raises(CheckFailed):
        build_site(data, out)
    assert not out.exists()


def test_build_writes_no_database(folder, tmp_path):
    build_site(folder, tmp_path / "site")
    assert not list(tmp_path.glob("*.db"))


def test_cli_build_reports_success_and_failure(folder, tmp_path, capsys):
    if not STYLESHEET.is_file():
        pytest.skip("stylesheet not built (npm run build:css)")
    assert main(["build", str(folder), "--out", str(tmp_path / "site")]) == 0
    assert "2 Topic-Seite(n)" in capsys.readouterr().out

    write_topic(folder, "kaputt.yaml", "kaputt", events=[event("exploit-1", "doppelt")])  # duplicate event key
    assert main(["build", str(folder), "--out", str(tmp_path / "site2")]) == 1
    assert "exploit-1" in capsys.readouterr().err
