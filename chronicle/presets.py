"""Saved search filters as YAML files in presets/ below the data folder (issue #260).

A preset is one file <name>.yaml with an optional display label and the fields of an event-list filter.
The files are ordinary YAML in the data folder: versioned, reviewable and synced with Git like the topic
files. The loader skips the folder (loader.PRESET_DIR), so a preset never becomes a topic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .loader import PRESET_DIR
from .models import TECHNIQUE_ID, Phase, Status
from .queries import EventFilter
from .store import folder_lock, write_new_file
from .yamlscan import MAX_YAML_BYTES, TOO_LARGE, YamlRejected, describe_validation_error, load_yaml, one_line

PRESET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
PRESET_SUFFIXES = (".yaml", ".yml")
# The fields of EventFilter a preset may set; the topic field included, so a preset can pin one topic.
FILTER_FIELDS = ("topic", "query", "tag", "phase", "status", "actor", "system", "technique", "entity", "since", "until")


class PresetRejected(ValueError):
    """A preset file or preset name is not usable."""


class PresetFilter(BaseModel):
    """The filter of a preset. Same fields as the event-list query; unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    topic: str | None = None
    query: str | None = None
    tag: str | None = None
    phase: Phase | None = None
    status: Status | None = None
    actor: str | None = None
    system: str | None = None
    technique: str | None = None
    entity: str | None = None
    since: date | None = None
    until: date | None = None

    @field_validator("technique")
    @classmethod
    def _check_technique(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper()
        if not TECHNIQUE_ID.fullmatch(value):
            raise ValueError("Technik muss eine ATT&CK-ID sein, z. B. T1059 oder T1059.001")
        return value

    def to_event_filter(self) -> EventFilter:
        return EventFilter(**{name: value for name in FILTER_FIELDS if (value := getattr(self, name)) is not None})


class PresetFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    filter: PresetFilter = Field(default_factory=PresetFilter)


@dataclass(frozen=True)
class SearchPreset:
    name: str  # the file stem, used in URLs
    label: str  # display name, the file stem when the file has none
    filter: EventFilter


def preset_dir(data_dir: Path) -> Path:
    return data_dir / PRESET_DIR


def preset_path(data_dir: Path, name: str) -> Path:
    """The file of a preset. The name is checked first, so it can never leave the presets folder."""
    if not PRESET_NAME_PATTERN.fullmatch(name or ""):
        raise PresetRejected("Preset-Name: 2 bis 64 Zeichen, Kleinbuchstaben, Ziffern, Punkt, Unterstrich, Bindestrich")
    return preset_dir(data_dir) / f"{name}.yaml"


def load_preset(path: Path) -> SearchPreset:
    """One preset file. The scan gates of the topic files apply (size, nodes, depth, no aliases or tags)."""
    where = one_line(path.name)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PresetRejected(f"{where}: nicht lesbar ({exc.strerror})") from exc
    if len(raw) > MAX_YAML_BYTES:
        raise PresetRejected(f"{where}: {TOO_LARGE}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PresetRejected(f"{where}: kein gültiges UTF-8") from exc
    try:
        data = load_yaml(text) or {}
    except YamlRejected as exc:
        raise PresetRejected(f"{where}: {exc}") from exc
    try:
        parsed = PresetFile.model_validate(data)
    except ValidationError as exc:
        raise PresetRejected(f"{where}: {describe_validation_error(exc)}") from exc
    return SearchPreset(name=path.stem, label=parsed.label or path.stem, filter=parsed.filter.to_event_filter())


def list_presets(data_dir: Path) -> tuple[list[SearchPreset], list[str]]:
    """All presets, sorted by name, plus one problem line per file that could not be read."""
    presets: list[SearchPreset] = []
    problems: list[str] = []
    folder = preset_dir(data_dir)
    if not folder.is_dir():
        return presets, problems
    for entry in sorted(os.scandir(folder), key=lambda item: item.name):
        if entry.is_symlink():
            problems.append(f"{PRESET_DIR}/{one_line(entry.name)}: Links sind im Datenordner nicht erlaubt")
        elif entry.is_file(follow_symlinks=False) and entry.name.endswith(PRESET_SUFFIXES):
            try:
                presets.append(load_preset(Path(entry.path)))
            except PresetRejected as exc:
                problems.append(str(exc))
    return presets, problems


def save_preset(data_dir: Path, name: str, label: str | None, flt: EventFilter) -> Path:
    """Write a new preset file, atomically under the folder lock. An existing name is refused (StoreError)."""
    path = preset_path(data_dir, name)  # PresetRejected for a bad name
    values = {}
    for field in FILTER_FIELDS:
        value = getattr(flt, field)
        if value is not None:
            values[field] = value.isoformat() if isinstance(value, date) else str(value)
    document: dict = {"filter": values}
    if label:
        document["label"] = label
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False)
    with folder_lock(data_dir):
        write_new_file(path, text.encode("utf-8"), root=data_dir)
    return path
