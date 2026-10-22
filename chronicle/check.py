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
    """Read and check every topic file below data_dir.

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

    items: list[tuple[Timeline, dict[str, Blob]]] = []
    for _, timeline in loaded:
        blobs: dict[str, Blob] = {}
        if evidence:
            try:
                blobs = load_evidence(timeline, data_dir)
            except TimelineError as exc:
                problems.append(str(exc))
        items.append((timeline, blobs))

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

    # One trigger per event for now (issue #87 makes it a list). The graph is only built from known targets.
    triggers: dict[str, str] = {}
    for name, timeline in items:
        for event in timeline.events:
            target = event.triggered_by
            if target is None:
                continue
            if target == event.key:
                problems.append(f"{name}: Event {event.key!r} kann sich nicht selbst auslösen")
            elif target not in known:
                problems.append(f"{name}: triggered_by {target!r} bei {event.key!r} ist kein bekannter Event-Key")
            elif target in event_owner:
                triggers[event.key] = target
            # A target defined only outside the folder cannot close a cycle within it.

    problems += cycle_problems(triggers)
    return problems


def cycle_problems(triggers: dict[str, str]) -> list[str]:
    """Report each cycle in the chain of triggers. `triggers` maps an event key to the key that triggered it."""
    problems: list[str] = []
    finished: set[str] = set()
    for start in triggers:
        path: list[str] = []
        position: dict[str, int] = {}
        node = start
        while node in triggers and node not in finished:
            if node in position:
                cycle = path[position[node] :] + [node]
                problems.append("Auslösungen bilden einen Kreis: " + " → ".join(cycle))
                break
            position[node] = len(path)
            path.append(node)
            node = triggers[node]
        finished.update(path)
    return problems
