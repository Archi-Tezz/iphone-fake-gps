"""Generate the ios-loc app icon.

The icon is drawn rather than shipped as an opaque binary, so it can be
re-rendered at any size and changed by editing numbers here.

Design: an iOS-style squircle with a blue-to-indigo diagonal gradient, a glass
highlight across the top, a navigation arrow in the middle and a thin orbit ring
behind it -- the arrow reads as "location", the ring as "being tracked".

Everything is drawn at SUPERSAMPLE times the target size and downscaled with
Lanczos, which is what keeps the curves clean with no antialiasing work.

Run: .venv\\Scripts\\python.exe tools/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
SUPERSAMPLE = 4
S = SIZE * SUPERSAMPLE

BRAND_DIR = Path(__file__).resolve().parent.parent / "iosloc" / "static" / "brand"
ICO_PATH = BRAND_DIR / "ios-loc.ico"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

# Matches --accent and the panel gradient.
TOP_LEFT = (10, 132, 255)
BOTTOM_RIGHT = (94, 92, 230)


def squircle_points(size: int, inset: float = 0.0, exponent: float = 5.0) -> list[tuple[float, float]]:
    """Outline of an iOS-style rounded square: the superellipse |x|^n + |y|^n = 1.

    Sampled parametrically rather than by angle, which spreads the samples
    evenly along the outline instead of bunching them at the corners.
    """
    radius = size / 2 - inset
    centre = size / 2
    steps = 2048
    points = []
    for i in range(steps):
        theta = 2 * math.pi * i / steps
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        points.append((
            centre + radius * math.copysign(abs(cos_t) ** (2 / exponent), cos_t),
            centre + radius * math.copysign(abs(sin_t) ** (2 / exponent), sin_t),
        ))
    return points


def squircle_mask(size: int, inset: float = 0.0) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(squircle_points(size, inset), fill=255)
    return mask


def diagonal_gradient(size: int, start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    """A 135-degree linear gradient.

    Built small and scaled up: a linear ramp stays linear under bilinear
    resampling, and filling 16M pixels in a Python loop would take minutes.
    """
    source = 512
    small = Image.new("RGB", (source, source))
    pixels = small.load()
    for y in range(source):
        for x in range(source):
            t = (x + y) / (2 * (source - 1))
            pixels[x, y] = (
                round(start[0] + (end[0] - start[0]) * t),
                round(start[1] + (end[1] - start[1]) * t),
                round(start[2] + (end[2] - start[2]) * t),
            )
    return small.resize((size, size), Image.BILINEAR)


def navigation_arrow(size: int) -> list[tuple[float, float]]:
    """The Apple-Maps-style arrow: a tall triangle notched from below."""
    centre = size / 2
    height = size * 0.46
    half_width = size * 0.25
    notch = size * 0.115
    return [
        (centre, centre - height * 0.64),              # tip
        (centre + half_width, centre + height * 0.40),  # right wing
        (centre, centre + height * 0.40 - notch),       # notch
        (centre - half_width, centre + height * 0.40),  # left wing
    ]


def build() -> Image.Image:
    mask = squircle_mask(S)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Body: gradient clipped to the squircle.
    body = diagonal_gradient(S, TOP_LEFT, BOTTOM_RIGHT).convert("RGBA")
    body.putalpha(mask)
    base.alpha_composite(body)

    # Glass highlight across the top, clipped so it cannot bleed past the edge.
    highlight = Image.new("L", (S, S), 0)
    ImageDraw.Draw(highlight).ellipse([-S * 0.25, -S * 0.78, S * 1.25, S * 0.44], fill=62)
    highlight = highlight.filter(ImageFilter.GaussianBlur(S * 0.03))
    highlight = Image.composite(highlight, Image.new("L", (S, S), 0), mask)
    white = Image.new("RGBA", (S, S), (255, 255, 255, 255))
    white.putalpha(highlight)
    base.alpha_composite(white)

    # Orbit ring behind the arrow, plus a tighter arc for depth.
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring)
    inset = S * 0.205
    ring_draw.ellipse([inset, inset, S - inset, S - inset],
                      outline=(255, 255, 255, 80), width=int(S * 0.015))
    inset2 = S * 0.285
    ring_draw.arc([inset2, inset2, S - inset2, S - inset2], start=205, end=335,
                  fill=(255, 255, 255, 48), width=int(S * 0.010))
    base.alpha_composite(ring)

    # Arrow: a soft drop shadow first, so it lifts off the gradient.
    arrow_points = navigation_arrow(S)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x, y + S * 0.016) for x, y in arrow_points],
                                   fill=(0, 18, 60, 115))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.02)))

    arrow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(arrow).polygon(arrow_points, fill=(255, 255, 255, 255))
    base.alpha_composite(arrow)

    # Hairline inner edge: keeps the silhouette crisp on a light background.
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).line(
        squircle_points(S, inset=S * 0.004) + [squircle_points(S, inset=S * 0.004)[0]],
        fill=(255, 255, 255, 64), width=int(S * 0.005), joint="curve",
    )
    base.alpha_composite(edge)

    return base.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    icon = build()

    icon.save(BRAND_DIR / "icon-1024.png")
    for size in (512, 256, 180, 128, 64, 32):
        icon.resize((size, size), Image.LANCZOS).save(BRAND_DIR / f"icon-{size}.png")

    # One .ico carrying every size Explorer, the taskbar and the title bar ask for.
    icon.save(ICO_PATH, format="ICO", sizes=[(s, s) for s in ICO_SIZES])

    print(f"PNG set: {BRAND_DIR}")
    print(f"Windows icon: {ICO_PATH}")


if __name__ == "__main__":
    main()
