"""Checklist templates per incident type (issue #226, ADR 0031).

One file per template in `checklists/` in the root of the data folder, e.g.:

    label: Ransomware
    tasks:
      - title: Systeme isolieren
        deadline_hours: 4
      - title: Backups prüfen
        assignee: IR-Team

"Apply template" on the topic page copies the tasks into the topic file with fresh keys; `deadline_hours`
becomes an absolute deadline from the moment of applying. The loader skips the folder (loader.CHECKLIST_DIR),
so a template never becomes a topic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .loader import CHECKLIST_DIR
from .yamlscan import MAX_YAML_BYTES, TOO_LARGE, YamlRejected, describe_validation_error, load_yaml, one_line

CHECKLIST_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
CHECKLIST_SUFFIXES = (".yaml", ".yml")
MAX_TEMPLATE_TASKS = 100


class ChecklistRejected(ValueError):
    """A template file or template name is not usable."""


class TemplateTask(BaseModel):
    """One task of a template. `deadline_hours` is relative to the moment the template is applied."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)
    assignee: str = Field(default="", max_length=100)
    deadline_hours: float | None = Field(default=None, gt=0, le=24 * 366 * 10)

    @field_validator("title", "note", "assignee")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class ChecklistFile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    label: str = Field(default="", max_length=200)
    tasks: list[TemplateTask] = Field(min_length=1, max_length=MAX_TEMPLATE_TASKS)


@dataclass(frozen=True)
class Checklist:
    name: str
    label: str
    tasks: list[TemplateTask]


def checklist_dir(data_dir: Path) -> Path:
    return data_dir / CHECKLIST_DIR


def load_checklist(path: Path) -> Checklist:
    """One template file. The scan gates of the topic files apply (size, nodes, depth, no aliases or tags)."""
    where = one_line(path.name)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ChecklistRejected(f"{where}: nicht lesbar ({exc.strerror})") from exc
    if len(raw) > MAX_YAML_BYTES:
        raise ChecklistRejected(f"{where}: {TOO_LARGE}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChecklistRejected(f"{where}: kein gültiges UTF-8") from exc
    try:
        data = load_yaml(text) or {}
    except YamlRejected as exc:
        raise ChecklistRejected(f"{where}: {exc}") from exc
    try:
        parsed = ChecklistFile.model_validate(data)
    except ValidationError as exc:
        raise ChecklistRejected(f"{where}: {describe_validation_error(exc)}") from exc
    return Checklist(name=path.stem, label=parsed.label.strip() or path.stem, tasks=parsed.tasks)


def list_checklists(data_dir: Path) -> tuple[list[Checklist], list[str]]:
    """All templates, sorted by name, plus one problem line per file that could not be read."""
    templates: list[Checklist] = []
    problems: list[str] = []
    folder = checklist_dir(data_dir)
    if not folder.is_dir():
        return templates, problems
    for entry in sorted(os.scandir(folder), key=lambda item: item.name):
        if entry.is_symlink():
            problems.append(f"{CHECKLIST_DIR}/{one_line(entry.name)}: Links sind im Datenordner nicht erlaubt")
        elif entry.is_file(follow_symlinks=False) and entry.name.endswith(CHECKLIST_SUFFIXES):
            try:
                templates.append(load_checklist(Path(entry.path)))
            except ChecklistRejected as exc:
                problems.append(str(exc))
    return templates, problems


def get_checklist(data_dir: Path, name: str) -> Checklist:
    """One template by name; the name is checked first, so it can never leave the checklists folder."""
    if not CHECKLIST_NAME_PATTERN.fullmatch(name or ""):
        raise ChecklistRejected("Vorlagen-Name: 2 bis 64 Zeichen, Kleinbuchstaben, Ziffern, Punkt, Unterstrich, Bindestrich")
    return load_checklist(checklist_dir(data_dir) / f"{name}.yaml")
