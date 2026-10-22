"""The JSON Schema of a topic file, generated from the models (ADR 0008, point 9; issue #93).

Editors read it through the `yaml-language-server` comment at the top of each file, which gives completion and
error marks. The committed copy in schema/ is kept equal to this output by tests/test_schema.py.
"""

from __future__ import annotations

import json

from .models import Timeline

SCHEMA_URL = "https://raw.githubusercontent.com/JoePhanDro/Chronicle-as-Code/main/schema/timeline.schema.json"


def schema_text() -> str:
    return json.dumps(Timeline.model_json_schema(), indent=2, ensure_ascii=False) + "\n"
