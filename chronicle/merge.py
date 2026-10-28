"""Git merge driver for topic files (ADR 0008, point 3; issue #94).

Two working copies that change different fields of the same event merge without a conflict. Only a field that both
sides changed differently is a conflict. The driver merges against the common ancestor (%O), per topic, per event and
per field.

Decisions:
- A deletion on one side and a change on the other is a conflict, not a silent deletion.
- Events keep the order of our side; events only the other side added follow, in its order.
- A link (refs) removed by either side is removed. Links never conflict. The same holds for the techniques of an event.
- Comments of our side stay. Comments that only the other side has are not carried over.

Git setup for a data repository (per clone, git does not share it):

    # .gitattributes
    *.yaml merge=chronicle

    git config merge.chronicle.name "Chronicle: Feldebene"
    git config merge.chronicle.driver "python -P -m chronicle.merge %O %A %B"

The app sets this itself (sync._driver_command); `-P` keeps the data folder, where git runs the driver, off the
module search path.

On a conflict the driver exits with 1 and leaves our version in %A. Git marks the file as conflicted.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from pydantic import ValidationError

from .loader import TimelineError, read_topic_text
from .models import Event, Timeline, Topic
from .store import (
    _event_document,
    _evidence_document,
    _load_document,
    _put,
    _read_model,
    _set_changed_fields,
    _text,
    _atomic_write,
    _yaml,
)
from .yamlscan import MAX_YAML_BYTES, TOO_LARGE, describe_validation_error

TOPIC_FIELDS = ("title", "kind", "summary")
EVENT_FIELDS = tuple(name for name in Event.model_fields if name != "key")
SET_FIELDS = ("techniques",)  # merged like refs: what either side adds is in, what either side removes is out


class MergeConflict(Exception):
    """Both sides changed the same thing differently. Nothing is merged for it."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def _three_way(base, ours, theirs, where: str, problems: list[str]):
    """The value that both sides agree on, or the one side that changed. Both changed differently: a conflict."""
    if ours == theirs:
        return ours
    if base == ours:
        return theirs
    if base == theirs:
        return ours
    problems.append(f"{where}: auf beiden Seiten unterschiedlich geändert")
    return ours


def _merge_topic(base: Topic | None, ours: Topic, theirs: Topic, problems: list[str]) -> Topic:
    if ours.key != theirs.key:
        problems.append(f"Topic-Key: {ours.key!r} gegen {theirs.key!r}")
        return ours
    values = {
        name: _three_way(getattr(base, name) if base else None, getattr(ours, name), getattr(theirs, name),
                         f"topic.{name}", problems)
        for name in TOPIC_FIELDS
    }
    return ours.model_copy(update=values)


def _merge_event(key: str, base: Event | None, ours: Event | None, theirs: Event | None,
                 problems: list[str]) -> Event | None:
    if ours == theirs:
        return ours
    if base == ours:
        return theirs
    if base == theirs:
        return ours
    if ours is None or theirs is None:
        problems.append(f"{key}: auf einer Seite gelöscht, auf der anderen geändert")
        return ours or theirs
    return ours.model_copy(update=_merge_fields(key, base, ours, theirs, problems))


def _merge_fields(key: str, base: Event | None, ours: Event, theirs: Event, problems: list[str]) -> dict:
    """The merged value of each field: three-way, except the set fields, which are merged like refs."""
    values = {
        name: _three_way(getattr(base, name) if base else None, getattr(ours, name), getattr(theirs, name),
                         f"{key}.{name}", problems)
        for name in EVENT_FIELDS if name not in SET_FIELDS
    }
    for name in SET_FIELDS:
        values[name] = sorted(_merge_refs(getattr(base, name) if base else [], getattr(ours, name), getattr(theirs, name)))
    return values


def _merge_refs(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
    """Added by either side: in. Removed by either side: out. Order: ours first, then theirs."""
    result = []
    for ref in dict.fromkeys([*ours, *theirs]):
        if ref in base and not (ref in ours and ref in theirs):
            continue
        result.append(ref)
    return result


def _merge_events(base: list[Event], ours: list[Event], theirs: list[Event], problems: list[str]) -> list[Event]:
    """The events of our file in our order, then those that only their side has."""
    base_by_key = {event.key: event for event in base}
    ours_by_key = {event.key: event for event in ours}
    theirs_by_key = {event.key: event for event in theirs}
    pairs = [(key, ours_by_key[key], theirs_by_key.get(key)) for key in ours_by_key]
    pairs += [(key, None, event) for key, event in theirs_by_key.items() if key not in ours_by_key]
    merged = (_merge_event(key, base_by_key.get(key), ours_event, theirs_event, problems)
              for key, ours_event, theirs_event in pairs)
    return [event for event in merged if event is not None]


def merge_timelines(base: Timeline | None, ours: Timeline, theirs: Timeline) -> Timeline:
    """Merge three versions of one topic file. Raises MergeConflict if a change cannot be merged."""
    problems: list[str] = []
    topic = _merge_topic(base.topic if base else None, ours.topic, theirs.topic, problems)
    events = _merge_events(base.events if base else [], ours.events, theirs.events, problems)
    refs = _merge_refs(base.refs if base else [], ours.refs, theirs.refs)
    if problems:
        raise MergeConflict(problems)
    try:
        return Timeline(topic=topic, events=events, refs=refs)
    except ValidationError as exc:
        raise MergeConflict([describe_validation_error(exc)]) from exc


def _apply_topic(section, ours: Topic, merged: Topic) -> None:
    if merged.title != ours.title:
        section["title"] = merged.title
    if merged.kind != ours.kind:
        section["kind"] = merged.kind
    if merged.summary != ours.summary:
        _put(section, "summary", _text(merged.summary) or None)


def _apply_event(item, ours: Event, merged: Event) -> None:
    if merged != ours:
        _set_changed_fields(item, ours, merged)
    if merged.evidence != ours.evidence:
        _put(item, "evidence", [_evidence_document(evidence) for evidence in merged.evidence] or None)


def _apply_events(document, ours: list[Event], merged: list[Event]) -> None:
    ours_by_key = {event.key: event for event in ours}
    merged_by_key = {event.key: event for event in merged}
    for item in list(document.get("events", [])):
        if item["key"] in merged_by_key:
            _apply_event(item, ours_by_key[item["key"]], merged_by_key[item["key"]])
        else:
            document["events"].remove(item)

    added = [_event_document(event) for event in merged if event.key not in ours_by_key]
    if added:
        document.setdefault("events", []).extend(added)
    if "events" in document and not document["events"]:
        del document["events"]


def _apply(document, ours: Timeline, merged: Timeline) -> None:
    """Write the merged values into our YAML document, so its comments and layout stay."""
    _apply_topic(document["topic"], ours.topic, merged.topic)
    if merged.refs != ours.refs:
        _put(document, "refs", list(merged.refs) or None)
    _apply_events(document, ours.events, merged.events)


def merge_texts(base_text: str, ours_text: str, theirs_text: str) -> str:
    """The merged text of a topic file. base_text is empty when both sides created the file."""
    base = _read_model(base_text) if base_text.strip() else None
    ours = _read_model(ours_text)
    theirs = _read_model(theirs_text)
    merged = merge_timelines(base, ours, theirs)

    document = _load_document(ours_text)
    _apply(document, ours, merged)
    stream = io.StringIO()
    _yaml().dump(document, stream)
    if len(stream.getvalue().encode("utf-8")) > MAX_YAML_BYTES:
        raise MergeConflict([f"Das Ergebnis des Zusammenführens würde die Grenze für eine Topic-Datei überschreiten ({TOO_LARGE})"])
    if _read_model(stream.getvalue()) != merged:
        raise MergeConflict(["Das zusammengeführte Ergebnis liest sich anders als berechnet."])
    return stream.getvalue()


def main(argv: list[str]) -> int:
    """Git calls the driver with the base (%O), our (%A) and their (%B) version. The result goes to %A."""
    base_path, ours_path, theirs_path = argv[:3]
    try:
        merged = merge_texts(read_topic_text(Path(base_path)),
                             read_topic_text(Path(ours_path)),
                             read_topic_text(Path(theirs_path)))
    except MergeConflict as exc:
        print("Chronicle: Konflikt, bitte von Hand auflösen:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except TimelineError as exc:  # includes StoreError
        print(f"Chronicle: Datei nicht lesbar: {exc}", file=sys.stderr)
        return 1
    _atomic_write(Path(ours_path), merged.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
