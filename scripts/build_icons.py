"""Builds every icon of the app from the one mark in chronicle/static/logo.svg.

    python scripts/build_icons.py          # writes the files
    python scripts/build_icons.py --check  # exits 1 when a generated file is out of date

Pillow has no SVG support and the build image carries no other rasteriser, so the PNGs are drawn by a small
renderer for the subset logo.svg uses: rect, circle and straight-line paths with round caps, hex colours.
Anything else in the source stops the build instead of drawing something wrong.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "chronicle" / "static"
SOURCE = STATIC / "logo.svg"
FONT = STATIC / "vendor" / "fonts" / "inter-latin-wght-normal.woff2"
SOCIAL_PREVIEW = ROOT / "docs" / "social-preview.png"
LOGO_PARTIAL = ROOT / "chronicle" / "templates" / "_logo.html"
SVG_NS = "{http://www.w3.org/2000/svg}"
SUPERSAMPLE = 8

# colour name -> (dark surface, light surface); the favicon has no tile and follows the browser theme
FAVICON_COLORS = {
    "#38bdf8": ("sky", "#38bdf8", "#0284c7"),
    "#8b5cf6": ("violet", "#8b5cf6", "#7c3aed"),
    "#e2e8f0": ("soft", "#e2e8f0", "#334155"),
}

# file name -> (edge in px, square corners). Corners stay transparent except for the full-bleed apple icon,
# iOS rounds that one itself.
PNG_SIZES = {
    "favicon-32.png": (32, False),
    "favicon-48.png": (48, False),
    "apple-touch-icon.png": (180, True),
    "icon-192.png": (192, False),
    "icon-512.png": (512, False),
}

MANIFEST = {
    "name": "Chronicle",
    "short_name": "Chronicle",
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
    "theme_color": "#020617",
    "background_color": "#020617",
    "display": "browser",
}


def hex_color(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise SystemExit(f"logo.svg: only #rrggbb colours are supported, got {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def path_segments(d):
    """Yields (x1, y1, x2, y2) for each stroke; M/H/V/L, absolute, one subpath per M."""
    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d)
    x = y = 0.0
    command = None
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command not in "MHVL":
                raise SystemExit(f"logo.svg: path command {command!r} is not supported")
            continue
        if command == "H":
            nx, ny = float(tokens[i]), y
            i += 1
        elif command == "V":
            nx, ny = x, float(tokens[i])
            i += 1
        else:
            nx, ny = float(tokens[i]), float(tokens[i + 1])
            i += 2
        if command == "M":
            command = "L"  # further pairs after a move are lines
        else:
            yield x, y, nx, ny
        x, y = nx, ny


def draw_shapes(draw, root, scale, square_corners):
    for element in root:
        tag = element.tag.removeprefix(SVG_NS)
        if tag == "title":
            continue
        fill, stroke = element.get("fill"), element.get("stroke")
        if tag == "rect":
            width, height = float(element.get("width")), float(element.get("height"))
            radius = 0 if square_corners else float(element.get("rx", 0))
            draw.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1), radius * scale, fill=hex_color(fill))
        elif tag == "circle":
            cx, cy, r = (float(element.get(name)) * scale for name in ("cx", "cy", "r"))
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hex_color(fill))
        elif tag == "path":
            width = float(element.get("stroke-width")) * scale
            if element.get("stroke-linecap") != "round" or fill != "none":
                raise SystemExit("logo.svg: paths must be round-capped strokes with fill=\"none\"")
            color = hex_color(stroke)
            for x1, y1, x2, y2 in path_segments(element.get("d")):
                points = [(x1 * scale, y1 * scale), (x2 * scale, y2 * scale)]
                draw.line(points, fill=color, width=round(width))
                for px, py in points:
                    draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)
        else:
            raise SystemExit(f"logo.svg: element <{tag}> is not supported")


def render_png(root, edge, square_corners):
    big = edge * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    # the viewBox is 64 units wide
    draw_shapes(ImageDraw.Draw(image), root, big / 64, square_corners)
    return image.resize((edge, edge), Image.LANCZOS)


def social_preview(root):
    """1280x640 card for the repository's social preview: mark, name and one line on the app's slate background."""
    card = Image.new("RGBA", (1280, 640), hex_color("#0f172a"))
    mark = render_png(root, 256, False)
    card.alpha_composite(mark, (150, 192))
    draw = ImageDraw.Draw(card)
    name = ImageFont.truetype(str(FONT), 120)
    name.set_variation_by_axes([650])
    line = ImageFont.truetype(str(FONT), 36)
    line.set_variation_by_axes([400])
    draw.text((470, 215), "Chronicle", font=name, fill=hex_color("#f1f5f9"))
    for row, text in enumerate(("Timeline und Entitäten-Graph", "für Sicherheitswissen")):
        draw.text((474, 355 + row * 52), text, font=line, fill=hex_color("#94a3b8"))
    return card


def favicon_svg(source):
    """The mark without its tile, recoloured through classes so it reads on light and dark tab strips."""
    body = re.sub(r"\s*<rect [^>]*/>", "", source)
    body = re.sub(r"\s*<title>.*?</title>", "", body)

    def classed(match):
        attribute, color = match.groups()
        return f'class="{attribute[0]}-{FAVICON_COLORS[color.lower()][0]}"'

    body = re.sub(r'(fill|stroke)="(#[0-9a-fA-F]{6})"', classed, body)
    rules = {"f": "fill", "s": "stroke"}
    dark = "".join(f".{k}-{n}{{{p}:{d}}}" for n, d, _light in FAVICON_COLORS.values() for k, p in rules.items())
    light_rules = "".join(f".{k}-{n}{{{p}:{light_color}}}" for n, _dark, light_color in FAVICON_COLORS.values() for k, p in rules.items())
    style = f"<style>{dark}@media (prefers-color-scheme: light){{{light_rules}}}</style>"
    return body.replace(">", f">\n  {style}", 1)


def logo_partial(source):
    """Jinja macro with the full mark, so the page templates carry no drawing of their own."""
    inner = re.sub(r"\s*<title>.*?</title>", "", source.split(">", 1)[1].rsplit("</svg>", 1)[0])
    inner = re.sub(r"\s+", " ", inner).strip()
    return (
        "{# generated by scripts/build_icons.py from chronicle/static/logo.svg, do not edit #}\n"
        "{% macro logo(classes='h-10 w-10') -%}\n"
        f'<svg viewBox="0 0 64 64" class="{{{{ classes }}}}" aria-hidden="true">{inner}</svg>\n'
        "{%- endmacro %}\n"
    )


def build():
    source = SOURCE.read_text(encoding="utf-8")
    root = ET.fromstring(source)
    files = {
        STATIC / "favicon.svg": favicon_svg(source).encode(),
        STATIC / "site.webmanifest": (json.dumps(MANIFEST, indent=2) + "\n").encode(),
        LOGO_PARTIAL: logo_partial(source).encode(),
    }
    for name, (edge, square) in PNG_SIZES.items():
        out = STATIC / name
        # PNG bytes depend on the Pillow version, so the check compares pixels, see main()
        files[out] = render_png(root, edge, square)
    files[SOCIAL_PREVIEW] = social_preview(root)
    return files


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    for path, content in build().items():
        if isinstance(content, Image.Image):
            if check:
                if not path.exists() or Image.open(path).convert("RGBA").tobytes() != content.tobytes():
                    stale.append(path)
            else:
                content.save(path, optimize=True)
        elif check:
            if not path.exists() or path.read_bytes() != content:
                stale.append(path)
        else:
            path.write_bytes(content)
    if stale:
        names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
        raise SystemExit(f"out of date: {names}. Run python scripts/build_icons.py")


if __name__ == "__main__":
    main()
