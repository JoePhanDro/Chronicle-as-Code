#!/usr/bin/env python3
"""Build raster brand assets from chronicle/static/brand-mark.svg."""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "chronicle" / "static"
DOCS = ROOT / "docs" / "screenshots"
SOURCE = STATIC / "brand-mark.svg"


def chromium_binary() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("No Chromium binary found. Install chromium/chromium-browser first.")


def render_png(svg_markup: str, target: Path, size: int, background: str = "transparent") -> None:
    img_style = "display:block;width:100%;height:100%;"
    body_style = "margin:0;"
    if background == "transparent":
        body_style += "background:transparent;"
        default_background = "00000000"
    else:
        body_style += f"background:{background};"
        default_background = "000000"

    html = (
        "<!doctype html><html><body style=\""
        + body_style
        + "\"><img alt=\"Chronicle mark\" src=\"data:image/svg+xml;base64,"
        + base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
        + "\" style=\""
        + img_style
        + "\"></body></html>"
    )

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as handle:
        handle.write(html)
        html_path = Path(handle.name)

    cmd = [
        chromium_binary(),
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        f"--window-size={size},{size}",
        f"--default-background-color={default_background}",
        f"--screenshot={target}",
        html_path.as_uri(),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        html_path.unlink(missing_ok=True)


def render_social_preview(svg_markup: str, target: Path) -> None:
    html = f"""<!doctype html>
<html>
  <body style=\"margin:0;width:1280px;height:640px;background:#020617;color:#e2e8f0;font-family:Inter,Segoe UI,sans-serif;\">
    <div style=\"width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:
      radial-gradient(920px 360px at 15% -10%, rgba(56,189,248,.22), transparent 60%),
      radial-gradient(920px 360px at 95% 110%, rgba(139,92,246,.24), transparent 62%),
      #020617;\">
      <div style=\"display:flex;align-items:center;gap:34px;padding:40px 68px;border:1px solid rgba(148,163,184,.24);border-radius:28px;background:rgba(15,23,42,.7);\">
        <img alt=\"Chronicle mark\" src=\"data:image/svg+xml;base64,{base64.b64encode(svg_markup.encode('utf-8')).decode('ascii')}\" style=\"width:144px;height:144px;display:block;\" />
        <div>
          <div style=\"font-size:28px;letter-spacing:.18em;text-transform:uppercase;color:#7dd3fc;font-weight:700;\">Chronicle</div>
          <div style=\"margin-top:6px;font-size:70px;line-height:1;font-weight:800;color:#f8fafc;\">as Code</div>
        </div>
      </div>
    </div>
  </body>
</html>
"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as handle:
        handle.write(html)
        html_path = Path(handle.name)

    cmd = [
        chromium_binary(),
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--window-size=1280,640",
        "--default-background-color=000000",
        f"--screenshot={target}",
        html_path.as_uri(),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        html_path.unlink(missing_ok=True)


def main() -> None:
    svg = SOURCE.read_text(encoding="utf-8")

    render_png(svg, STATIC / "favicon-32x32.png", 32)
    render_png(svg, STATIC / "favicon-48x48.png", 48)
    render_png(svg, STATIC / "icon-192.png", 192)
    render_png(svg, STATIC / "icon-512.png", 512)
    # Backward-compatible path still used by docs and external links.
    render_png(svg, STATIC / "icon.png", 512)

    apple_svg = svg.replace("viewBox=\"0 0 64 64\"", "viewBox=\"0 0 64 64\"")
    apple_html_svg = (
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\">"
        "<rect width=\"64\" height=\"64\" fill=\"#0f172a\"/>"
        + svg.split("<defs>", 1)[1].split("</svg>", 1)[0].join(["<defs>", ""])
    )
    # Simpler and clearer: use the original mark over a solid tile.
    apple_composite = (
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\">"
        "<rect width=\"64\" height=\"64\" fill=\"#0f172a\"/>"
        + svg.replace("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\" role=\"img\" aria-labelledby=\"title desc\">", "").replace("<title id=\"title\">Chronicle Mark</title>", "").replace("<desc id=\"desc\">A timeline with three nodes and a code bracket.</desc>", "").replace("</svg>", "")
        + "</svg>"
    )
    render_png(apple_composite, STATIC / "apple-touch-icon.png", 180, background="#0f172a")

    DOCS.mkdir(parents=True, exist_ok=True)
    render_social_preview(svg, DOCS / "social-preview.png")


if __name__ == "__main__":
    main()
