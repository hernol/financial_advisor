"""Render the PNG app icons from the same geometry as web/icon.svg.

Bubblewrap refuses to build without a PNG of at least 512px, and the manifest
keeps the SVG for browsers that prefer it. There is no SVG rasteriser on the
server and pulling one in for three files is not worth the dependency, so the
shapes are drawn here with Pillow.

The constants below mirror web/icon.svg. Change one and change the other.

    python3 scripts/make_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parent.parent / "web"

# --- geometry, in the SVG's 512 unit viewBox --------------------------------
BACKGROUND = "#020617"
FOREGROUND = "#22C55E"
CORNER_RADIUS = 112
POLYLINE = [(96, 344), (192, 240), (272, 304), (416, 160)]
STROKE = 36
DOT_CENTRE = (416, 160)
DOT_RADIUS = 30

# A maskable icon may be cropped to the circle covering the middle 80%, and this
# artwork reaches 228 units from the centre — outside it. Shrinking to 0.85
# brings the far corner to 194 units, comfortably inside, and the background
# goes edge to edge because the launcher supplies the silhouette.
SAFE_SCALE = 0.85

# Pillow draws shapes without antialiasing, so everything is rendered large and
# resampled down.
SUPERSAMPLE = 4


def render(size: int, *, maskable: bool) -> Image.Image:
    canvas = size * SUPERSAMPLE
    unit = canvas / 512
    shrink = SAFE_SCALE if maskable else 1.0

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if maskable:
        draw.rectangle([0, 0, canvas, canvas], fill=BACKGROUND)
    else:
        draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=CORNER_RADIUS * unit, fill=BACKGROUND)

    def place(x: float, y: float) -> tuple[float, float]:
        """Scale about the centre, then into pixels."""
        return (((x - 256) * shrink + 256) * unit, ((y - 256) * shrink + 256) * unit)

    points = [place(x, y) for x, y in POLYLINE]
    width = STROKE * shrink * unit
    draw.line(points, fill=FOREGROUND, width=round(width), joint="curve")

    # joint="curve" rounds the corners but not the two ends, which the SVG draws
    # with stroke-linecap="round".
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=FOREGROUND)

    dot_x, dot_y = place(*DOT_CENTRE)
    dot = DOT_RADIUS * shrink * unit
    draw.ellipse([dot_x - dot, dot_y - dot, dot_x + dot, dot_y + dot], fill=FOREGROUND)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    for name, size, maskable in [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-512-maskable.png", 512, True),
    ]:
        path = WEB / name
        render(size, maskable=maskable).save(path, "PNG", optimize=True)
        print(f"{path.relative_to(WEB.parent)}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
