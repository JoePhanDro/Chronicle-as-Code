"""Foreign YAML, patterns and error messages (part 0b of the vault plan, ADR 0021 points 4, 19 and 24)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import ruamel.yaml
import yaml

from chronicle import merge, store, sync, yamlscan
from chronicle.accounts import validate_username
from chronicle.check import check_folder
from chronicle.event_input import EventCreate
from chronicle.loader import TimelineError, load_timeline, parse_timeline
from chronicle.models import Topic
from chronicle.store import StoreError, add_event_to_file, file_version, update_event_in_file

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
    assert "Knoten" in problem("topic: {key: a, title: A}\nrefs: [" + ", ".join(["x"] * 100_001) + "]\n")


def test_a_file_over_4_mib_is_refused(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text(TOPIC + "# " + "x" * 4 * 1024 * 1024 + "\n", encoding="utf-8")

    with pytest.raises(TimelineError, match="4 MiB"):
        load_timeline(path)


def test_a_large_file_of_multibyte_characters_is_refused_as_too_large(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text("# " + "ä" * 2_200_000 + "\n", encoding="utf-8")  # the limit cuts through a character

    with pytest.raises(TimelineError, match="4 MiB"):
        load_timeline(path)


def test_a_file_that_is_no_utf8_is_refused_without_its_content(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_bytes(b"topic: " + SECRET.encode() + b"\xff\n")

    with pytest.raises(TimelineError) as caught:
        load_timeline(path)

    assert SECRET not in str(caught.value)


def test_legitimate_files_still_load():
    assert check_folder(DATA_DIR) == []
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

    result = python("from pathlib import Path; from chronicle.store import StoreError, file_version, update_event_in_file\n"
                    "from chronicle.models import Event\n"
                    "event = Event(key='a1', t0='2026-10-14T08:40:00+02:00', phase='Exploit', actor='X', action='x', status='open')\n"
                    f"path = Path({str(path)!r})\n"
                    "try: update_event_in_file(path, 'a1', event, expected_version=file_version(path))\n"
                    "except StoreError as exc: print(exc)")

    assert result.returncode == 0, result.stderr
    assert "Verschachtelung" in result.stdout


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


# --- size of a topic file: one topic is one file (ADR 0005) --------------------------------------------------------

def events_text(count: int, pad: int) -> str:
    """A topic with count events, 17 nodes each, about 200 bytes plus pad."""
    lines = ["topic:\n  key: big\n  title: Big\nevents:"]
    for index in range(count):
        lines += [f"  - key: e{index:05d}", "    t0: 2026-10-14T08:00:00+02:00", "    phase: Analysis", f"    actor: Akteur {index}",
                  f"    action: 'Schritt {index}'", "    status: open", "    details: |", f"      {'p' * pad}"]
    return "\n".join(lines) + "\n"


def test_a_topic_with_1200_events_loads(tmp_path):
    path = tmp_path / "big.yaml"
    path.write_text(events_text(1200, 150), encoding="utf-8")

    assert path.stat().st_size > 256 * 1024  # more than the first limit of the scan allowed
    assert len(load_timeline(path).events) == 1200


def test_the_editor_does_not_write_a_file_over_the_limit(tmp_path):
    path = tmp_path / "big.yaml"
    text = events_text(100, 20)
    path.write_text(text + "# " + "x" * (yamlscan.MAX_YAML_BYTES - len(text.encode()) - 4 - 100) + "\n", encoding="utf-8")
    before = path.read_bytes()
    assert len(before) < yamlscan.MAX_YAML_BYTES
    event = load_timeline(path).events[10].model_copy(update={"details": "q" * 500})

    with pytest.raises(StoreError, match=r"4 MiB|MiB"):
        update_event_in_file(path, "e00010", event, expected_version=file_version(path))

    assert path.read_bytes() == before


def test_adding_an_event_over_the_limit_names_the_limit(tmp_path):
    path = tmp_path / "big.yaml"
    text = events_text(100, 20)
    path.write_text(text + "# " + "x" * (yamlscan.MAX_YAML_BYTES - len(text.encode()) - 4 - 100) + "\n", encoding="utf-8")
    before = path.read_bytes()
    event = load_timeline(path).events[10].model_copy(update={"key": "neu", "details": "q" * 500})

    with pytest.raises(StoreError) as caught:
        add_event_to_file(path, event, expected_version=file_version(path))

    assert "MiB" in str(caught.value)
    assert "nicht lesbar" not in str(caught.value)
    assert path.read_bytes() == before


# --- ruamel reads what libyaml accepted (the editor and the merge driver) ---------------------------------------------

@pytest.mark.parametrize("tail", ["\t", "\x85x", "\u2028x"])
def test_an_editor_error_of_ruamel_holds_no_content(tmp_path, tail):
    path = tmp_path / "a.yaml"
    path.write_text(TOPIC.replace("title: A", f"title: {SECRET}{tail}"), encoding="utf-8", newline="")
    event = parse_timeline(TOPIC, "a.yaml").events[0].model_copy(update={"key": "a2"})

    try:
        add_event_to_file(path, event, expected_version=file_version(path))
    except StoreError as exc:  # ruamel reads it differently from libyaml: a message about the file, not its content
        assert SECRET not in str(exc)
        assert "\n" not in str(exc)


@pytest.mark.parametrize("tail", ["\t", "\x85x", "\u2028x"])
def test_a_merge_error_of_ruamel_holds_no_content(tail):
    text = TOPIC.replace("title: A", f"title: {SECRET}{tail}")

    try:
        merge.merge_texts(TOPIC, text, TOPIC)
    except StoreError as exc:
        assert SECRET not in str(exc)
    except merge.MergeConflict as exc:
        assert SECRET not in str(exc)


def test_the_editor_translates_an_error_of_ruamel(tmp_path, monkeypatch):
    path = tmp_path / "a.yaml"
    path.write_text(TOPIC, encoding="utf-8")
    event = parse_timeline(TOPIC, "a.yaml").events[0].model_copy(update={"key": "a2"})

    class Broken:
        def load(self, text):
            mark = ruamel.yaml.error.StringMark("x", 0, 2, 3, SECRET, 0)  # ruamel quotes this line in str(exc)
            raise ruamel.yaml.scanner.ScannerError(None, None, "found character that cannot start any token", mark)

    monkeypatch.setattr(store, "_yaml", Broken)

    with pytest.raises(StoreError) as caught:
        add_event_to_file(path, event, expected_version=file_version(path))

    assert SECRET not in str(caught.value)
    assert "Zeile 3" in str(caught.value)


# --- without libyaml the messages of PyYAML quote the line ---------------------------------------------------------------

def test_the_entry_lookup_holds_no_content_without_libyaml(tmp_path, monkeypatch):
    monkeypatch.setattr(yamlscan, "YAML_LOADER", yaml.SafeLoader)
    monkeypatch.setattr(store, "YAML_LOADER", yaml.SafeLoader)
    path = tmp_path / "evil\nname.yaml"
    path.write_text(f"topic: [{SECRET}\n", encoding="utf-8")
    event = parse_timeline(TOPIC, "a.yaml").events[0]

    with pytest.raises(StoreError) as caught:
        update_event_in_file(path, "a1", event, expected_version=file_version(path))

    assert SECRET not in str(caught.value)
    assert "\n" not in str(caught.value)


# --- the cache of key resolutions ----------------------------------------------------------------------------------------

def test_long_keys_are_not_kept_in_memory():
    yamlscan._is_text.cache_clear()
    keys = "".join(f"{index:03d}{'k' * 200}: 1\n" for index in range(50))

    yamlscan.scan(keys)

    assert yamlscan._is_text.cache_info().currsize == 0


# --- bytes from Git ---------------------------------------------------------------------------------------------------------

def commit(folder: Path, content: bytes) -> None:
    (folder / "a.yaml").write_bytes(content)
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "Stand"]):
        subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")
@pytest.mark.parametrize("content", [
    pytest.param(b"topic: " + SECRET.encode() + b"\xff\n", id="no-utf8"),
    pytest.param(b"# " + b"x" * (4 * 1024 * 1024 + 10) + b"\n", id="too-large"),  # an id with the content would fill the environment
])
def test_a_bad_blob_in_git_is_a_sync_error(repository, content):
    commit(repository, content)

    with pytest.raises(sync.SyncError) as caught:
        sync.base_timelines(repository)

    assert SECRET not in str(caught.value)
    assert "a.yaml" in str(caught.value)


# --- characters ------------------------------------------------------------------------------------------------------------

def test_a_lone_surrogate_stays_out_of_a_message():
    assert yamlscan.one_line("a\ud800b") == "ab"
