"""Fetch the vector map styles the panel ships with.

The panel renders vector tiles through MapLibre rather than the old raster
tiles: sharp labels at any zoom, smooth zooming, rotation and tilt, and a real
dark basemap instead of a CSS-inverted light one.

Two keyless providers are used, both serving OpenStreetMap data:

* **OpenFreeMap** (openfreemap.org) -- OpenMapTiles schema, no key, no quota.
* **VersaTiles** (versatiles.org) -- Shortbread schema, no key; its dark styles
  are purpose-built rather than inverted.

Style documents are stored locally so the panel does not fetch a style
descriptor on every start; tiles, fonts and sprites still come from the
provider at runtime, which is what needs the internet.

Run: .venv\\Scripts\\python.exe tools/make_map_styles.py
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "iosloc" / "static" / "map-styles"

#: filename -> (url, human label). Order is the order shown in settings.
STYLES: dict[str, tuple[str, str]] = {
    "liberty": ("https://tiles.openfreemap.org/styles/liberty", "Подробная"),
    "positron": ("https://tiles.openfreemap.org/styles/positron", "Светлая минимал"),
    "dark": ("https://tiles.versatiles.org/assets/styles/colorful-dark/style.json", "Тёмная"),
    "graybeard": ("https://tiles.versatiles.org/assets/styles/graybeard/style.json", "Тёмная минимал"),
}

USER_AGENT = "ios-loc/1.0 (local developer tool)"
TIMEOUT = 60


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def absolutise(style: dict, base_url: str) -> dict:
    """Make relative sprite/glyph URLs absolute against the style's own origin.

    A style served from the provider may point at `./sprite`; once the document
    is stored locally that would resolve against our own server and 404.
    """
    origin = base_url.rsplit("/", 1)[0]
    for key in ("sprite", "glyphs"):
        value = style.get(key)
        if isinstance(value, str) and value.startswith("."):
            style[key] = f"{origin}/{value.lstrip('./')}"
    return style


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []

    for name, (url, label) in STYLES.items():
        print(f"fetching {name} ...", end=" ", flush=True)
        style = absolutise(fetch(url), url)
        path = OUTPUT_DIR / f"{name}.json"
        path.write_text(json.dumps(style, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index.append({"key": name, "label": label, "layers": len(style.get("layers", []))})
        print(f"{len(style.get('layers', []))} layers, {path.stat().st_size // 1024} KB")

    (OUTPUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nstyles written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
