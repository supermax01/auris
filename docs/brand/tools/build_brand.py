#!/usr/bin/env python3
"""Build the Auris brand assets from their construction grid.

Standard library only, fully offline and deterministic. Every SVG and PNG in
docs/brand is generated from the geometry below; nothing is hand-edited.

    python3 docs/brand/tools/build_brand.py           # write the assets
    python3 docs/brand/tools/build_brand.py --check   # verify they are current

The mark: concentric arches (spoken sound) resting on lines (the written
note). One rule set builds all three sizes:

* stroke ``s``, gap ``g``, opening radius ``a``, ``n`` arches and ``n`` lines;
* the outer radius is ``R = a + n*s + (n-1)*g``; the mark is ``2R`` square;
* each arch's straight legs are ``a`` long, so the opening is a half circle on
  a block of the same height, and the arches stand on the first line.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1]

TERRACOTTA = "#C7522B"  # primary mark colour on light surfaces
TERRACOTTA_LIGHT = "#E58A62"  # mark colour on dark surfaces
PAPER = "#F4EFE8"  # light tile
WHITE = "#FFFFFF"
BLACK = "#000000"


@dataclass(frozen=True)
class Grid:
    name: str
    stroke: float
    gap: float
    opening: float
    count: int

    @property
    def radius(self) -> float:
        return self.opening + self.count * self.stroke + (self.count - 1) * self.gap

    @property
    def size(self) -> float:
        return 2 * self.radius


# Full mark for 64 px and up; optical versions snap to whole pixels at
# 24/48 px (medium) and 16/32 px (small).
FULL = Grid("full", stroke=20, gap=26, opening=70, count=4)
MEDIUM = Grid("medium", stroke=2, gap=2, opening=2, count=3)
SMALL = Grid("small", stroke=2, gap=2, opening=2, count=2)


def shapes(grid: Grid, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0):
    """Arch regions and line rectangles in output coordinates."""
    s, g, a, r = grid.stroke, grid.gap, grid.opening, grid.radius
    cx = cy = r
    base = cy + a  # top of the first line
    out = []
    for i in range(grid.count):
        inner = a + i * (s + g)
        # Legs run through the first line so the union has no seam.
        out.append(("arch", cx, cy, inner, inner + s, base + s))
    for k in range(grid.count):
        top = base + k * (s + g)
        out.append(("rect", 0.0, top, grid.size, top + s))

    def place(shape):
        kind, *v = shape
        if kind == "arch":
            cx_, cy_, ri, ro, yb = v
            return (kind, dx + cx_ * scale, dy + cy_ * scale, ri * scale, ro * scale, dy + yb * scale)
        x0, y0, x1, y1 = v
        return (kind, dx + x0 * scale, dy + y0 * scale, dx + x1 * scale, dy + y1 * scale)

    return [place(shape) for shape in out]


# --- SVG -------------------------------------------------------------------


def _n(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def mark_path(grid: Grid, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> str:
    """One filled path; every subpath runs clockwise (nonzero union)."""
    parts = []
    for kind, *v in shapes(grid, scale, dx, dy):
        if kind == "arch":
            cx, cy, ri, ro, yb = map(_n, v)
            l_o, r_o = _n(v[0] - v[3]), _n(v[0] + v[3])
            l_i, r_i = _n(v[0] - v[2]), _n(v[0] + v[2])
            parts.append(
                f"M{l_o} {yb}V{cy}A{ro} {ro} 0 0 1 {r_o} {cy}V{yb}"
                f"H{r_i}V{cy}A{ri} {ri} 0 0 0 {l_i} {cy}V{yb}Z"
            )
        else:
            x0, y0, x1, y1 = map(_n, v)
            parts.append(f"M{x0} {y0}H{x1}V{y1}H{x0}Z")
    return "".join(parts)


def svg(view: float, body: str, title: str = "Auris") -> str:
    v = _n(view)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {v} {v}" '
        f'width="{v}" height="{v}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def mark_svg(grid: Grid, colour: str) -> str:
    return svg(grid.size, f'<path fill="{colour}" d="{mark_path(grid)}"/>')


TILE = 1024
TILE_RADIUS = 228  # rounded tile, about 22% of the side
ICON_SCALE = 1.25  # the 456-unit mark becomes 570 px on a 1024 px tile
ICON_OFFSET = (TILE - FULL.size * ICON_SCALE) / 2


def rounded_rect_path(size: float, radius: float) -> str:
    s, r = _n(size), _n(radius)
    e = _n(size - radius)
    return (
        f"M{r} 0H{e}A{r} {r} 0 0 1 {s} {r}V{e}A{r} {r} 0 0 1 {e} {s}"
        f"H{r}A{r} {r} 0 0 1 0 {e}V{r}A{r} {r} 0 0 1 {r} 0Z"
    )


def icon_svg(tile: str, mark: str, *, rounded: bool) -> str:
    background = (
        f'<path fill="{tile}" d="{rounded_rect_path(TILE, TILE_RADIUS)}"/>'
        if rounded
        else f'<rect fill="{tile}" width="{TILE}" height="{TILE}"/>'
    )
    body = background + (
        f'<path fill="{mark}" d="{mark_path(FULL, ICON_SCALE, ICON_OFFSET, ICON_OFFSET)}"/>'
    )
    return svg(TILE, body)


# --- PNG (analytic coverage rasteriser) -------------------------------------

SUBROWS = 16


def _intervals(shape, y: float):
    kind, *v = shape
    if kind == "rect":
        x0, y0, x1, y1 = v
        return [(x0, x1)] if y0 <= y < y1 else []
    if kind == "tile":
        size, radius = v
        if not 0 <= y < size:
            return []
        d = min(y, size - y)
        if d >= radius:
            return [(0.0, size)]
        inset = radius - math.sqrt(max(0.0, radius * radius - (radius - d) ** 2))
        return [(inset, size - inset)]
    cx, cy, ri, ro, yb = v
    if y >= yb:
        return []
    if y >= cy:
        return [(cx - ro, cx - ri), (cx + ri, cx + ro)]
    dy = cy - y
    if dy >= ro:
        return []
    xo = math.sqrt(ro * ro - dy * dy)
    if dy >= ri:
        return [(cx - xo, cx + xo)]
    xi = math.sqrt(ri * ri - dy * dy)
    return [(cx - xo, cx - xi), (cx + xi, cx + xo)]


def coverage(shape_list, width: int, height: int) -> list[array]:
    """Per-pixel area coverage (0..1) of the union of the shapes."""
    rows = []
    weight = 1.0 / SUBROWS
    for py in range(height):
        diff = array("d", bytes(8 * (width + 2)))
        part = array("d", bytes(8 * (width + 1)))
        for sub in range(SUBROWS):
            y = py + (sub + 0.5) / SUBROWS
            spans = sorted(
                (max(0.0, a), min(float(width), b))
                for shape in shape_list
                for a, b in _intervals(shape, y)
                if b > a
            )
            merged: list[list[float]] = []
            for a, b in spans:
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            for a, b in merged:
                ia, ib = int(math.floor(a)), int(math.floor(b))
                if ia == ib:
                    part[ia] += (b - a) * weight
                    continue
                part[ia] += (ia + 1 - a) * weight
                if ib < width:
                    part[ib] += (b - ib) * weight
                diff[ia + 1] += weight
                diff[ib] -= weight
        row = array("d", bytes(8 * width))
        running = 0.0
        for x in range(width):
            running += diff[x]
            row[x] = min(1.0, running + part[x])
        rows.append(row)
    return rows


def _rgb(colour: str) -> tuple[float, float, float]:
    return tuple(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))


def render(layers, width: int, height: int) -> bytes:
    """Composite (colour, shapes) layers bottom to top into RGBA PNG bytes."""
    premul = [array("d", bytes(8 * width * height)) for _ in range(4)]
    for colour, shape_list in layers:
        cr, cg, cb = _rgb(colour)
        for py, row in enumerate(coverage(shape_list, width, height)):
            base = py * width
            for px, alpha in enumerate(row):
                if alpha <= 0:
                    continue
                i = base + px
                keep = 1 - alpha
                premul[0][i] = cr * alpha + premul[0][i] * keep
                premul[1][i] = cg * alpha + premul[1][i] * keep
                premul[2][i] = cb * alpha + premul[2][i] * keep
                premul[3][i] = alpha + premul[3][i] * keep
    raw = bytearray()
    for py in range(height):
        raw.append(0)
        for px in range(width):
            i = py * width + px
            a = premul[3][i]
            if a <= 0:
                raw += b"\0\0\0\0"
                continue
            raw += bytes(
                (
                    round(min(1.0, premul[0][i] / a) * 255),
                    round(min(1.0, premul[1][i] / a) * 255),
                    round(min(1.0, premul[2][i] / a) * 255),
                    round(a * 255),
                )
            )

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def mark_png(grid: Grid, colour: str, size: int) -> bytes:
    scale = size / grid.size
    return render([(colour, shapes(grid, scale))], size, size)


def icon_png(tile: str, mark: str, size: int, *, rounded: bool) -> bytes:
    k = size / TILE
    background = [("tile", size, TILE_RADIUS * k)] if rounded else [("rect", 0, 0, size, size)]
    marks = shapes(FULL, ICON_SCALE * k, ICON_OFFSET * k, ICON_OFFSET * k)
    return render([(tile, background), (mark, marks)], size, size)


# --- Contrast ----------------------------------------------------------------


def luminance(colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


CONTRAST_PAIRS = (
    (TERRACOTTA, WHITE),
    (TERRACOTTA, PAPER),
    (WHITE, TERRACOTTA),
    (TERRACOTTA_LIGHT, "#17120F"),
)


# --- Asset list ----------------------------------------------------------------


def assets() -> dict[str, object]:
    """Relative path -> str (SVG) or zero-argument callable returning PNG bytes."""
    out: dict[str, object] = {}
    for grid, suffix in ((FULL, ""), (MEDIUM, "-medium"), (SMALL, "-small")):
        out[f"auris-mark{suffix}.svg"] = mark_svg(grid, TERRACOTTA)
        out[f"auris-mark{suffix}-dark.svg"] = mark_svg(grid, TERRACOTTA_LIGHT)
    out["auris-mark-mono-black.svg"] = mark_svg(FULL, BLACK)
    out["auris-mark-mono-white.svg"] = mark_svg(FULL, WHITE)
    out["auris-app-icon.svg"] = icon_svg(TERRACOTTA, WHITE, rounded=True)
    out["auris-app-icon-light.svg"] = icon_svg(PAPER, TERRACOTTA, rounded=True)
    out["auris-app-icon-fullbleed.svg"] = icon_svg(TERRACOTTA, WHITE, rounded=False)

    out["png/auris-mark-1024.png"] = lambda: mark_png(FULL, TERRACOTTA, 1024)
    out["png/auris-mark-dark-1024.png"] = lambda: mark_png(FULL, TERRACOTTA_LIGHT, 1024)
    out["png/auris-app-icon-1024.png"] = lambda: icon_png(TERRACOTTA, WHITE, 1024, rounded=True)
    out["png/auris-app-icon-light-1024.png"] = lambda: icon_png(
        PAPER, TERRACOTTA, 1024, rounded=True
    )
    out["png/auris-app-icon-fullbleed-1024.png"] = lambda: icon_png(
        TERRACOTTA, WHITE, 1024, rounded=False
    )
    out["png/favicon-16.png"] = lambda: mark_png(SMALL, TERRACOTTA, 16)
    out["png/favicon-32.png"] = lambda: mark_png(SMALL, TERRACOTTA, 32)
    out["png/favicon-48.png"] = lambda: mark_png(MEDIUM, TERRACOTTA, 48)
    # Apple touch icon: opaque square; the platform rounds the corners.
    out["png/favicon-180.png"] = lambda: icon_png(TERRACOTTA, WHITE, 180, rounded=False)
    out["png/favicon-512.png"] = lambda: icon_png(TERRACOTTA, WHITE, 512, rounded=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="verify files are current")
    args = parser.parse_args()
    stale = []
    for relative, content in assets().items():
        data = content() if callable(content) else content.encode()
        path = BRAND / relative
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for a, b in CONTRAST_PAIRS:
        ratio = contrast(a, b)
        print(f"contrast {a} on {b}: {ratio:.2f}:1")
        if ratio < 3:
            stale.append(f"contrast {a} on {b} below 3:1")
    if stale:
        print("out of date or failing:", *stale, sep="\n  ")
        return 1
    print("brand assets " + ("current" if args.check else "written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
