"""Foreign YAML, patterns and error messages (part 0b of the vault plan, ADR 0021 points 4, 19 and 24)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chronicle import merge
from chronicle.accounts import validate_username
from chronicle.check import check_folder
from chronicle.event_input import EventCreate
from chronicle.loader import TimelineError, load_timeline, parse_timeline
from chronicle.models import Topic
from chronicle.store import StoreError, file_version, update_event_in_file

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EVENT = ("  - key: a1\n    t0: 2026-10-14T08:40:00+02:00\n    phase: Exploit\n    actor: X\n"
         "    action: Schritt\n    status: open\n")
TOPIC = f"topic:\n  key: a\n  title: A\nevents:\n{EVENT}"
SECRET = "hunter2-very-secret"
DEEP = "[" * 120_000  # about 120 KB: the C loader crashes with SIGSEGV on this
DUPLICATE = TOPIC.replace("status: open", "status: open\n    status: resolved")


def python(code: str) -> subprocess.CompletedProcess:
    """Run code in a fresh interpreter. A crash of the C loader must not take the test run down with it."""
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)


def problem(text: str) -> str:
    """The message for a topic file with this text."""
    with pytest.raises(TimelineError) as caught:
        parse_timeline(text, "a.yaml")
    return str(caught.value)


# --- the scan ----------------------------------------------------------------------------------------------------

def test_deep_nesting_is_an_error_not_a_crash(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text(DEEP, encoding="utf-8")

    result = python(f"from pathlib import Path; from chronicle.check import check_folder; print(check_folder(Path({str(folder)!r})))")

    assert result.returncode == 0, result.stderr
    assert "Verschachtelung" in result.stdout


def nested(levels: int) -> str:
    """A document of this many nested mappings."""
    return "".join("  " * level + "a:\n" for level in range(levels - 1)) + "  " * (levels - 1) + "a: 1\n"


def test_nesting_is_limited_to_12_levels():
    assert "Verschachtelung" not in problem(nested(12))
    assert "Verschachtelung" in problem(nested(13))


@pytest.mark.parametrize(("text", "rule", "line"), [
    ("topic: &t\n  key: a\n", "Anker", 1),
    ("topic:\n  key: a\n  title: &t A\n", "Anker", 3),
    ("topic:\n  key: a\n  title: !!str A\n", "Tags", 3),
    ("topic:\n  key: a\n  title: !!python/object/apply:os.getcwd []\n", "Tags", 3),
    ("topic:\n  key: a\n  <<: {title: A}\n", "Merge", 3),
    ("topic:\n  key: a\n  \"<<\": {title: A}\n", "Merge", 3),
    ("topic:\n  key: a\n  title: A\n  title: B\n", "doppelter Schlüssel", 4),
    ("topic:\n  key: a\n  title: A\n  \"title\": B\n", "doppelter Schlüssel", 4),
    ("topic: {key: a, title: A, key: b}\n", "doppelter Schlüssel", 1),
    ("topic:\n  key: a\n  1: A\n", "Schlüssel müssen Text sein", 3),
    ("topic:\n  key: a\n  no: A\n", "Schlüssel müssen Text sein", 3),
    ("topic:\n  key: a\n  ? [x]\n  : A\n", "Schlüssel müssen Text sein", 3),
    ("topic:\n  key: a\n  title: A\n---\ntopic: {}\n", "mehrere Dokumente", 4),
    ("topic:\n  key: a\n  title: A\n  # &a im Kommentar ist keiner\n  x: *a\n", "Alias", 5),
])
def test_the_scan_refuses_what_the_schema_does_not_use(text, rule, line):
    message = problem(text)

    assert rule in message
    assert f"Zeile {line}" in message
    assert message.startswith("a.yaml: ")


def test_an_alias_bomb_is_refused_before_it_expands():
    levels = ["a: &a0 [x, x, x, x, x, x, x, x, x]"]
    levels += [f"{chr(97 + i)}: &{chr(97 + i)}{i} [*{chr(96 + i)}{i - 1}, *{chr(96 + i)}{i - 1}, *{chr(96 + i)}{i - 1}]"
               for i in range(1, 7)]

    assert "Anker" in problem("\n".join(levels) + "\n")


def test_more_nodes_than_the_limit_are_refused():
    assert "Knoten" in problem("topic: {key: a, title: A}\nrefs: [" + ", ".join(["x"] * 20_001) + "]\n")


def test_a_file_over_256_kib_is_refused(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text(TOPIC + "# " + "x" * 256 * 1024 + "\n", encoding="utf-8")

    with pytest.raises(TimelineError, match="256 KiB"):
        load_timeline(path)


def test_a_large_file_of_multibyte_characters_is_refused_as_too_large(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text("# " + "ä" * 200_000 + "\n", encoding="utf-8")  # the limit cuts through a character

    with pytest.raises(TimelineError, match="256 KiB"):
        load_timeline(path)


def test_a_file_that_is_no_utf8_is_refused_without_its_content(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_bytes(b"topic: " + SECRET.encode() + b"\xff\n")

    with pytest.raises(TimelineError) as caught:
        load_timeline(path)

    assert SECRET not in str(caught.value)


def test_legitimate_files_still_load():
    assert check_folder(DATA_DIR) == []
    for path in sorted((ROOT / "tests").rglob("*.yaml")):
        load_timeline(path)  # whatever test fixtures the repository has as files
    assert parse_timeline(TOPIC, "a.yaml").events[0].key == "a1"
    assert parse_timeline(TOPIC.replace("title: A", 'title: "A"').replace("key: a", "'key': a"), "a.yaml").topic.title == "A"
    assert parse_timeline(TOPIC.replace("  title: A", "  title: {x}").replace("{x}", "'a: b'"), "a.yaml").topic.title == "a: b"


def test_flow_style_and_quoted_keys_are_read_as_before():
    flow = "{topic: {key: a, title: A}, events: [{key: a1, t0: 2026-10-14T08:40:00+02:00, phase: Exploit, actor: X, " \
           "action: Schritt, status: open}]}\n"

    assert parse_timeline(flow, "a.yaml").events[0].key == "a1"


# --- every place that reads foreign YAML -------------------------------------------------------------------------

def test_the_merge_driver_refuses_foreign_input(tmp_path):
    base, ours, theirs = (tmp_path / name for name in ("base", "ours", "theirs"))
    base.write_text(TOPIC, encoding="utf-8")
    ours.write_text(TOPIC, encoding="utf-8")
    theirs.write_text(DEEP, encoding="utf-8")

    result = python(f"import sys; from chronicle import merge; sys.exit(merge.main({[str(base), str(ours), str(theirs)]!r}))")

    assert result.returncode == 1, result.stderr
    assert "Verschachtelung" in result.stderr


def test_the_merge_driver_refuses_duplicate_keys():
    with pytest.raises(StoreError, match="doppelter Schlüssel"):
        merge.merge_texts(TOPIC, TOPIC, DUPLICATE)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    for name, value in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}.items():
        monkeypatch.setenv(name, value)
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")
@pytest.mark.parametrize("content", [DEEP, DUPLICATE])
def test_the_state_in_git_is_scanned_too(repository, content):
    (repository / "a.yaml").write_text(content, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "Stand"]):
        subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)

    result = python(f"from pathlib import Path; from chronicle import sync\n"
                    f"try: sync.base_timelines(Path({str(repository)!r}))\n"
                    f"except sync.SyncError as exc: print(exc)")

    assert result.returncode == 0, result.stderr
    assert "Zeile" in result.stdout


def test_editing_a_file_with_deep_nesting_does_not_crash(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text(DEEP, encoding="utf-8")

    result = python("from pathlib import Path; from chronicle.store import StoreError, update_event_in_file\n"
                    "from chronicle.models import Event\n"
                    "event = Event(key='a1', t0='2026-10-14T08:40:00+02:00', phase='Exploit', actor='X', action='x', status='open')\n"
                    f"try: update_event_in_file(Path({str(path)!r}), 'a1', event, expected_version='x')\n"
                    "except StoreError as exc: print(exc)")

    assert result.returncode == 0, result.stderr


def test_editing_checks_the_file_before_it_parses_it(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text(DUPLICATE, encoding="utf-8")
    event = parse_timeline(TOPIC, "a.yaml").events[0]
    with pytest.raises(StoreError, match="doppelter Schlüssel"):
        update_event_in_file(path, "a1", event, expected_version=file_version(path))


# --- patterns ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["anna\n", "anna\r\n", "\nanna", "anna ", "an"])
def test_a_user_name_must_match_completely(name):
    with pytest.raises(ValueError):
        validate_username(name)


def test_a_plain_user_name_is_valid():
    validate_username("anna.b-c_1")


@pytest.mark.parametrize("key", ["abc\n", "abc\r\n", "\nabc"])
def test_keys_with_a_line_break_are_refused_by_the_models(key):
    with pytest.raises(ValueError):
        Topic(key=key, title="A")
    with pytest.raises(ValueError):
        EventCreate(key=key, topic="a", t0="2026-10-14T08:40:00+02:00", phase="Exploit", actor="X", action="x", status="open")
    with pytest.raises(ValueError):
        EventCreate(topic=key, t0="2026-10-14T08:40:00+02:00", phase="Exploit", actor="X", action="x", status="open")


# --- messages ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    TOPIC.replace("actor: X", f"actor: [{SECRET}]"),
    TOPIC.replace("status: open", f"status: {SECRET}"),
    TOPIC.replace("t0: 2026-10-14T08:40:00+02:00", f"t0: {SECRET}"),
    TOPIC.replace("phase: Exploit", f"phase: {SECRET}"),
    TOPIC + f"refs: {SECRET}\n",
    TOPIC.replace("key: a1", f"key: {SECRET}\n    secret: {SECRET}"),
    TOPIC + f"events2: {SECRET}\nsecret_field: '{SECRET}'\n",
    f"topic: [{SECRET}\n",
    f"topic: {SECRET}: {SECRET}\n",
    f"topic:\n  key: a\n  title: \"{SECRET}\n",
    TOPIC.replace("title: A", f"title: {SECRET}\x00"),
    TOPIC.replace("t0: 2026-10-14", f"t0: {SECRET} 2026-13-45"),
])
def test_a_message_holds_no_content_of_the_file(text):
    message = problem(text)

    assert SECRET not in message
    assert "\n" not in message
    assert len(message) < 600


def test_the_model_does_not_hold_the_input_in_its_error():
    with pytest.raises(ValueError) as caught:
        Topic.model_validate({"key": "a", "title": ["geheim"], "secret_field": SECRET})

    assert SECRET not in str(caught.value)
    assert "geheim" not in str(caught.value)


def test_the_check_names_the_file_and_the_field(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text(TOPIC.replace("status: open", f"status: {SECRET}"), encoding="utf-8")

    [message] = check_folder(folder)

    assert "a.yaml" in message
    assert "events.0.status" in message
    assert SECRET not in message


def test_control_characters_in_a_file_name_stay_out_of_the_message():
    with pytest.raises(TimelineError) as caught:
        parse_timeline("[", "evil\n\x1b[31m\u202e.yaml")

    assert "\n" not in str(caught.value)
    assert "\x1b" not in str(caught.value)
    assert "\u202e" not in str(caught.value)


# --- YAML 1.1 habits of PyYAML (already caught by the schema: text fields do not take booleans or numbers) ---------

@pytest.mark.parametrize("value", ["no", "on", "yes", "1:30", "017"])
def test_values_that_yaml_1_1_reads_as_other_types_are_no_text(value):
    message = problem(TOPIC.replace("actor: X", f"actor: {value}"))

    assert "events.0.actor" in message
