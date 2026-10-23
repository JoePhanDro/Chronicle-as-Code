"""Checks for a folder of topic files. Nothing here writes to the database (ADR 0009).

`check_folder` is what `chronicle check` runs. `load_folder` is what the importer runs before it writes.
Both report every problem they find, not only the first one.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from .evidence import Blob
from .loader import CheckFailed, TimelineError, find_topic_files, load_evidence, load_timeline
from .models import Timeline


def load_folder(
    data_dir: Path, *, evidence: bool = True, known_keys: Collection[str] = ()
) -> list[tuple[Timeline, dict[str, Blob]]]:
    """Read and check every topic file below data_dir. See load_named_folder."""
    return [(timeline, blobs) for _, timeline, blobs in load_named_folder(
        data_dir, evidence=evidence, known_keys=known_keys)]


def load_named_folder(
    data_dir: Path, *, evidence: bool = True, known_keys: Collection[str] = ()
) -> list[tuple[str, Timeline, dict[str, Blob]]]:
    """Read and check every topic file below data_dir. Each item carries the file name relative to data_dir.

    `known_keys` are event keys that exist outside the folder (the importer passes the keys already in the
    database). With `evidence=False` the attachments are not required, which the CI needs because they are not in git.
    Raises CheckFailed with all problems found.
    """
    problems: list[str] = []
    loaded: list[tuple[str, Timeline]] = []
    for path in find_topic_files(data_dir):
        name = path.relative_to(data_dir).as_posix()
        try:
            loaded.append((name, load_timeline(path)))
        except TimelineError as exc:
            problems.append(str(exc))

    problems += link_problems(loaded, known_keys)

    items: list[tuple[str, Timeline, dict[str, Blob]]] = []
    for name, timeline in loaded:
        blobs: dict[str, Blob] = {}
        if evidence:
            try:
                blobs = load_evidence(timeline, data_dir)
            except TimelineError as exc:
                problems.append(str(exc))
        items.append((name, timeline, blobs))

    if problems:
        raise CheckFailed(problems)
    return items


def check_folder(data_dir: Path, *, evidence: bool = True) -> list[str]:
    """The problems in the folder, empty if it is valid. Writes nothing."""
    try:
        load_folder(data_dir, evidence=evidence)
    except CheckFailed as exc:
        return exc.problems
    except TimelineError as exc:
        return [str(exc)]
    return []


def link_problems(items: Sequence[tuple[str, Timeline]], known_keys: Collection[str] = ()) -> list[str]:
    """Problems between files: duplicate keys, unresolvable references, cycles in triggered_by.

    `items` pairs each timeline with its file name for the messages.
    """
    problems: list[str] = []
    topic_owner: dict[str, str] = {}
    event_owner: dict[str, str] = {}

    for name, timeline in items:
        key = timeline.topic.key
        if key in topic_owner:
            problems.append(f"Topic {key!r} ist in {topic_owner[key]} und {name} definiert")
        else:
            topic_owner[key] = name
        for event in timeline.events:
            if event.key in event_owner:
                problems.append(f"Event {event.key!r} ist in {event_owner[event.key]} und {name} definiert")
            else:
                event_owner[event.key] = name

    known = set(event_owner) | set(known_keys)
    for name, timeline in items:
        for key in timeline.refs:
            if key not in known:
                problems.append(f"{name}: refs verweist auf {key!r}, das in keiner Datei definiert ist")

    # The graph has an edge from each event to each event that triggered it. Targets outside the folder
    # cannot close a cycle within it, so they are not part of the graph.
    triggers: dict[str, list[str]] = {}
    for name, timeline in items:
        for event in timeline.events:
            for target in event.triggered_by:
                if target == event.key:
                    problems.append(f"{name}: Event {event.key!r} kann sich nicht selbst auslösen")
                elif target not in known:
                    problems.append(f"{name}: triggered_by {target!r} bei {event.key!r} ist kein bekannter Event-Key")
                elif target in event_owner:
                    triggers.setdefault(event.key, []).append(target)

    problems += cycle_problems(triggers)
    return problems


def cycle_problems(triggers: dict[str, list[str]]) -> list[str]:
    """Report each cycle in the triggers. `triggers` maps an event key to the keys that triggered it."""
    problems: list[str] = []
    finished: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in path:
            cycle = path[path.index(node) :] + [node]
            problems.append("Auslösungen bilden einen Kreis: " + " → ".join(cycle))
            return
        if node in finished:
            return
        for target in triggers.get(node, []):
            visit(target, [*path, node])
        finished.add(node)

    for start in triggers:
        visit(start, [])
    return problems
