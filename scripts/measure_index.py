"""Messung für Index und Topic-Seiten (issue #96). Nicht Teil der Testsuite.

    python scripts/measure_index.py                       # 10.000 Events in 100 Dateien, Seite mit 5.000 Events
    python scripts/measure_index.py --events 2000 --files 10

Erzeugt synthetische Daten in einem temporären Ordner und räumt danach auf.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chronicle.check import load_folder  # noqa: E402
from chronicle.importer import import_folder  # noqa: E402
from chronicle.models import Timeline  # noqa: E402
from chronicle.renderer import render_dashboard  # noqa: E402

PHASES = ["Identification", "Exploit", "Analysis", "Containment", "Mitigation", "Recovery", "Lessons Learned", "Activity"]


def generate(folder: Path, files: int, per_file: int) -> None:
    """Topic files with per_file events each. Every tenth event is triggered by its predecessor,
    and each topic refers to two events of the next topic, so links cross file boundaries."""
    folder.mkdir(parents=True, exist_ok=True)
    for f in range(files):
        lines = [f"topic:\n  key: topic-{f:04d}\n  title: 'Topic {f}'\n  kind: incident\nevents:"]
        for j in range(per_file):
            lines += [
                f"  - key: t{f:04d}-e{j:05d}",
                f"    t0: 2026-10-{1 + (j // 1440) % 28:02d}T{(j // 60) % 24:02d}:{j % 60:02d}:00+02:00",
                f"    phase: {PHASES[j % len(PHASES)]}",
                f"    actor: Akteur {j % 20}",
                f"    system: system-{j % 7}",
                f"    action: 'Schritt {j} in Topic {f}'",
                f"    status: {['open', 'in_progress', 'resolved'][j % 3]}",
                "    details: |",
                f"      Details zu Schritt {j}.",
            ]
            if j % 10 == 1:
                lines += ["    triggered_by:", f"      - t{f:04d}-e{j - 1:05d}"]
        nxt = (f + 1) % files
        lines += ["refs:", f"  - t{nxt:04d}-e00000", f"  - t{nxt:04d}-e00001"]
        (folder / f"topic-{f:04d}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def timed(label: str, action):
    start = time.perf_counter()
    result = action()
    print(f"  {label:<46} {time.perf_counter() - start:6.2f} s")
    return result


def render_time(folder: Path, topic_key: str) -> tuple[int, float]:
    """Time to render one topic page, as the app and the report do it. Client-side drawing is not included."""
    items = load_folder(folder, evidence=False)
    events = {event.key: event for timeline, _ in items for event in timeline.events}
    timeline = next(t for t, _ in items if t.topic.key == topic_key)
    view = Timeline(topic=timeline.topic, events=[*timeline.events, *(events[k] for k in dict.fromkeys(timeline.refs))])
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        render_dashboard(view, inline_css="")
        best = min(best, time.perf_counter() - start)
    return len(view.events), best


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the index import and topic page rendering.")
    parser.add_argument("--events", type=int, default=10_000, help="events in the import folder")
    parser.add_argument("--files", type=int, default=100, help="topic files in the import folder")
    parser.add_argument("--page-events", type=int, default=5_000, help="events in the topic for the page measurement")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="chronicle-measure-") as tmp:
        tmp_path = Path(tmp)
        data = tmp_path / "data"
        generate(data, args.files, args.events // args.files)
        db = tmp_path / "chronicle.db"

        print(f"Index, {args.events} Events in {args.files} Dateien:")
        timed("import into a fresh database", lambda: import_folder(db, data))
        timed("import again, nothing changed", lambda: import_folder(db, data))
        changed = tmp_path / "changed"
        shutil.copytree(data, changed)
        target = changed / "topic-0001.yaml"
        target.write_text(target.read_text(encoding="utf-8").replace(
            "\nrefs:", "\n  - key: neu-1\n    t0: 2026-10-27T10:00:00+02:00\n    phase: Activity\n    actor: X\n    action: Neu\n    status: open\nrefs:"
        ), encoding="utf-8")
        timed("import after one file changed", lambda: import_folder(db, changed))

        page_data = tmp_path / "page"
        generate(page_data, 2, args.page_events)  # topic-0000 gets page_events events of its own
        count, seconds = render_time(page_data, "topic-0000")
        print(f"Topic-Seite, {count} Events: {seconds * 1000:.0f} ms (bestes von 3 Läufen, ohne Zeichnen im Browser)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
