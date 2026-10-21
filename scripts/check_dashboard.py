"""Render the dashboard from the sample timeline and syntax-check its script (used in CI).

Usage: python scripts/check_dashboard.py [output_dir]
Without an output directory, a temporary one is created.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `python scripts/...` puts scripts/ on the path, not the repo root

from chronicle.loader import load_timeline  # noqa: E402
from chronicle.renderer import render_dashboard  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp())
    out.mkdir(parents=True, exist_ok=True)

    html = render_dashboard(load_timeline(ROOT / "data" / "timeline.yaml"))
    page = out / "dashboard.html"
    page.write_text(html, encoding="utf-8")

    # The dashboard code is the last inline <script> without attributes; the payload and CDN scripts are skipped.
    script = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    js = out / "dashboard.js"
    js.write_text(script, encoding="utf-8")
    subprocess.run(["node", "--check", str(js)], check=True)

    print(f"dashboard ok: {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
