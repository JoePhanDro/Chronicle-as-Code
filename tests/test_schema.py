"""The JSON Schema of a topic file stays generated from the models and reaches the editors (issue #93)."""

import json
from pathlib import Path

from chronicle.cli import main
from chronicle.exporter import HEADER
from chronicle.schema import SCHEMA_URL, schema_text

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "schema" / "timeline.schema.json"


def test_committed_schema_is_current():
    # If this fails, regenerate: python -m chronicle schema > schema/timeline.schema.json
    assert COMMITTED.read_text(encoding="utf-8") == schema_text()


def test_top_level_and_events_forbid_unknown_fields():
    schema = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Event"]["additionalProperties"] is False
    assert schema["$defs"]["Topic"]["additionalProperties"] is False


def test_schema_lists_the_phases_of_the_notation():
    schema = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert "Lessons Learned" in schema["$defs"]["Phase"]["enum"]


def test_export_and_demo_data_point_to_the_published_schema():
    assert HEADER.splitlines()[0] == f"# yaml-language-server: $schema={SCHEMA_URL}"
    demo_first_line = (ROOT / "data" / "timeline.yaml").read_text(encoding="utf-8").splitlines()[0]
    assert demo_first_line == HEADER.splitlines()[0]


def test_cli_schema_prints_the_schema(capsys):
    assert main(["schema"]) == 0
    assert capsys.readouterr().out == schema_text()
