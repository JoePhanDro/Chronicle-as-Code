import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "chronicle" / "static"
TEMPLATES = ROOT / "chronicle" / "templates"


def test_generated_icons_match_logo_svg():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_icons.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_template_draws_its_own_logo():
    # the mark lives in logo.svg and reaches the pages only through the generated _logo.html macro
    for path in TEMPLATES.glob("*.html"):
        if path.name == "_logo.html":
            continue
        html = path.read_text(encoding="utf-8")
        # the bolt stays as the event glyph of the graph, but not as a logo
        assert 'class="h-5 w-5 text-ink" fill="currentColor"><path d="M13 2' not in html, f"{path.name} still draws the old bolt logo"
        assert "bg-gradient-to-br from-sky-400 to-violet-500" not in html, f"{path.name} still draws the gradient logo tile"
    for name in ("base.html", "login.html", "setup.html", "dashboard.html"):
        assert "import logo" in (TEMPLATES / name).read_text(encoding="utf-8")


def test_icon_links_point_to_existing_files():
    for name in ("base.html", "login.html", "setup.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        for href in re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)"[^>]*href="/static/([^"]+)"', html):
            assert (STATIC / href).exists(), f"{name}: /static/{href} is missing"


def test_png_icons_have_transparent_corners_except_apple_touch():
    for name in ("favicon-32.png", "favicon-48.png", "icon-192.png", "icon-512.png"):
        image = Image.open(STATIC / name)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0, f"{name} has an opaque corner, it would show a light frame on dark pages"
    apple = Image.open(STATIC / "apple-touch-icon.png")
    assert apple.size == (180, 180) and apple.getpixel((0, 0))[3] == 255  # iOS rounds the corners itself
