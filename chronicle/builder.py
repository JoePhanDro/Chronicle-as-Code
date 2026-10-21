"""Assemble the static site: copy evidence files and write index.html."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .loader import collect_evidence, find_timeline, load_timeline
from .renderer import render_dashboard


def build_site(data_dir: Path, out_dir: Path) -> Path:
    timeline_path = find_timeline(data_dir)
    timeline = load_timeline(timeline_path)
    evidence = collect_evidence(timeline, timeline_path.parent)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Evidence keeps its relative layout, so the browser can use the same path as in the YAML.
    for relative, source in evidence.items():
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Write to a temp file and swap it in, so a browser never reads a half-written page.
    index = out_dir / "index.html"
    tmp = out_dir / "index.html.tmp"
    tmp.write_text(render_dashboard(timeline), encoding="utf-8")
    os.replace(tmp, index)
    return index
