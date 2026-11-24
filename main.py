#!/usr/bin/env python3
"""The Isle of Wandering Gold -- a hand-drawn treasure map generator.

Pure standard library: no Pillow, no network, no fonts on disk.  The map is
drawn with a tiny software rasterizer (thick polylines, scanline polygon
fill, disks, bezier curves) and all lettering uses a single-stroke engraved
capitals font defined in this file, so the program runs in any python3.13
sandbox.  Output: one 1024x1024 indexed PNG at /output/entry.png (or
./output/entry.png when /output is not writable), under the 1 MiB gate.

The island: a fictional isle with nine named places that belong to one
story -- a captain's wreck, a grumbling volcano, a whispering jungle, a
mermaid lagoon, a dead man's cove, and the captain's cache marked with a
big red X at the end of a dotted route from the Old Landing.
"""

import math
import os
import random
import struct
import sys
import zlib

W = H = 1024
SEED = 1749
rng = random.Random(SEED)

# ---------------------------------------------------------------- palette
PARCHMENT = (232, 211, 162)
PARCH_LIGHT = (243, 228, 188)
PARCH_DARK = (213, 184, 128)
PARCH_DEEP = (190, 156, 98)
INK = (74, 52, 32)
INK_DARK = (50, 34, 20)
INK_SOFT = (110, 84, 52)
RED = (176, 56, 36)
RED_DARK = (138, 38, 24)
SAND = (238, 221, 172)
LAND = (216, 196, 138)
GREEN = (138, 158, 90)
GREEN_DARK = (94, 124, 64)
GREEN_DEEP = (74, 102, 52)
SEA_WASH = (158, 184, 168)
LAGOON = (104, 186, 172)
LAGOON_DEEP = (74, 158, 150)
RIVER = (96, 140, 148)
STONE = (122, 110, 98)
LAVA = (206, 84, 40)
GOLD = (198, 148, 52)

PALETTE = [
    PARCHMENT, PARCH_LIGHT, PARCH_DARK, PARCH_DEEP, INK, INK_DARK, INK_SOFT,
    RED, RED_DARK, SAND, LAND, GREEN, GREEN_DARK, GREEN_DEEP, SEA_WASH,
    LAGOON, LAGOON_DEEP, RIVER, STONE, LAVA, GOLD,
    (224, 200, 146), (204, 172, 112), (240, 220, 170), (168, 138, 84),
    (128, 148, 84), (88, 112, 58), (180, 120, 60), (146, 90, 50),
    (60, 90, 78), (130, 168, 158), (210, 110, 70), (160, 60, 44),
    (236, 214, 166), (200, 168, 110), (90, 66, 40), (150, 120, 70),
    (100, 130, 110), (220, 190, 130), (70, 60, 44), (170, 150, 100),
]


# ------------------------------------------------------------------- canvas
_BLEND_TABLES = {}


def _blend_table(value, alpha):
    """A 256-byte translate table for blending one channel toward `value`."""
    key = (value, round(alpha, 3))
    tab = _BLEND_TABLES.get(key)
    if tab is None:
        a = alpha
        tab = bytes(min(255, int(v * (1 - a) + value * a + 0.5))
                    for v in range(256))
        _BLEND_TABLES[key] = tab
    return tab


class Canvas:
    """An RGB raster with alpha-blended drawing primitives.

    Three planar bytearrays; row spans are filled with slice assignment
    (opaque) or bytes.translate through a cached blend table (translucent),
    which keeps a full 1024x1024 render to a few seconds in pure Python.
    """

    def __init__(self, w, h, base):
        self.w = w
        self.h = h
        n = w * h
        r, g, b = base
        self.r = bytearray([r]) * n
        self.g = bytearray([g]) * n
        self.b = bytearray([b]) * n

    def _span(self, y, x0, x1, color, alpha=1.0):
        if y < 0 or y >= self.h:
            return
        x0 = max(0, x0)
        x1 = min(self.w - 1, x1)
        if x1 < x0:
            return
        n = x1 - x0 + 1
        base = y * self.w + x0
        if alpha >= 1.0:
            cr, cg, cb = color
            self.r[base:base + n] = bytes([cr]) * n
            self.g[base:base + n] = bytes([cg]) * n
            self.b[base:base + n] = bytes([cb]) * n
        elif alpha > 0.0:
            for plane, cv in ((self.r, color[0]), (self.g, color[1]),
                              (self.b, color[2])):
                tab = _blend_table(cv, alpha)
                plane[base:base + n] = plane[base:base + n].translate(tab)

    def set(self, x, y, color, alpha=1.0):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            if alpha >= 1.0:
                self.r[i], self.g[i], self.b[i] = color
            elif alpha > 0.0:
                self.r[i] = _blend_table(color[0], alpha)[self.r[i]]
                self.g[i] = _blend_table(color[1], alpha)[self.g[i]]
                self.b[i] = _blend_table(color[2], alpha)[self.b[i]]

    def disk(self, cx, cy, r, color, alpha=1.0):
        r = int(r)
        if r <= 0:
            self.set(int(cx), int(cy), color, alpha)
            return
        x0 = max(0, int(cx) - r)
        x1 = min(self.w - 1, int(cx) + r)
        y0 = max(0, int(cy) - r)
        y1 = min(self.h - 1, int(cy) + r)
        r2 = r * r
        for y in range(y0, y1 + 1):
            dy = y - cy
            dx = int(math.sqrt(max(0, r2 - dy * dy)))
            self._span(y, max(x0, int(cx) - dx), min(x1, int(cx) + dx),
                       color, alpha)

    def line(self, x0, y0, x1, y1, width, color, alpha=1.0):
        """A thick line drawn as stamped disks (round caps, hand feel)."""
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist / max(1.0, width * 0.45)))
        rad = width / 2.0
        for i in range(steps + 1):
            t = i / steps
            self.disk(x0 + dx * t, y0 + dy * t, rad, color, alpha)

    def polyline(self, pts, width, color, alpha=1.0, closed=False):
        seq = list(pts)
        if closed and len(seq) > 2:
            seq = seq + [seq[0]]
        for a, b in zip(seq, seq[1:]):
            self.line(a[0], a[1], b[0], b[1], width, color, alpha)

    def wobbly_line(self, x0, y0, x1, y1, width, color, alpha=1.0, wob=1.2):
        """A slightly unsteady hand-drawn line."""
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 14))
        pts = []
        for i in range(n + 1):
            t = i / n
            pts.append((x0 + (x1 - x0) * t + rng.uniform(-wob, wob),
                        y0 + (y1 - y0) * t + rng.uniform(-wob, wob)))
        self.polyline(pts, width, color, alpha)

    def fill_polygon(self, pts, color, alpha=1.0):
        """Even-odd scanline fill."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if (y1p <= y < y2p) or (y2p <= y < y1p):
                    t = (y - y1p) / (y2p - y1p)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self._span(y, int(math.ceil(xs[k])),
                           int(math.floor(xs[k + 1])), color, alpha)

    def ellipse(self, cx, cy, rx, ry, color, alpha=1.0):
        rx, ry = int(rx), int(ry)
        for y in range(max(0, int(cy) - ry), min(self.h, int(cy) + ry + 1)):
            dy = (y - cy) / max(1, ry)
            dx = int(rx * math.sqrt(max(0.0, 1 - dy * dy)))
            self._span(y, int(cx) - dx, int(cx) + dx, color, alpha)

    def ring(self, cx, cy, r, width, color, alpha=1.0):
        steps = max(24, int(2 * math.pi * r / 3))
        pts = [(cx + r * math.cos(2 * math.pi * i / steps),
                cy + r * math.sin(2 * math.pi * i / steps))
               for i in range(steps)]
        self.polyline(pts, width, color, alpha, closed=True)

    def bezier(self, p0, p1, p2, p3, width, color, alpha=1.0, steps=28):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            x = (u ** 3 * p0[0] + 3 * u * u * t * p1[0]
                 + 3 * u * t * t * p2[0] + t ** 3 * p3[0])
            y = (u ** 3 * p0[1] + 3 * u * u * t * p1[1]
                 + 3 * u * t * t * p2[1] + t ** 3 * p3[1])
            pts.append((x, y))
        self.polyline(pts, width, color, alpha)

    def quad(self, p0, p1, p2, width, color, alpha=1.0, steps=20):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
        self.polyline(pts, width, color, alpha)

    def arc(self, cx, cy, r, a0, a1, width, color, alpha=1.0):
        steps = max(8, int(abs(a1 - a0) * r / 4))
        pts = [(cx + r * math.cos(a0 + (a1 - a0) * i / steps),
                cy + r * math.sin(a0 + (a1 - a0) * i / steps))
               for i in range(steps + 1)]
        self.polyline(pts, width, color, alpha)


# ------------------------------------------------------------- PNG encoding
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def save_indexed_png(canvas, path):
    """Quantize the RGB canvas to the fixed palette and write an indexed PNG.

    Colours are bucketed to 6 bits per channel before the nearest-palette
    lookup, so the cache stays small; rows are filtered with filter 0 or
    filter 1 (Sub), whichever compresses smaller.
    """
    w, h = canvas.w, canvas.h
    cache = {}

    def nearest(r, g, b):
        key = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2)
        hit = cache.get(key)
        if hit is not None:
            return hit
        rr, gg, bb = r & ~3, g & ~3, b & ~3
        best, bd = 0, 1 << 30
        for i, (pr, pg, pb) in enumerate(PALETTE):
            d = (rr - pr) * (rr - pr) + (gg - pg) * (gg - pg) + (bb - pb) * (bb - pb)
            if d < bd:
                bd, best = d, i
        cache[key] = best
        return best

    rows = []
    for y in range(h):
        base = y * w
        rr = canvas.r[base:base + w]
        gg = canvas.g[base:base + w]
        bb = canvas.b[base:base + w]
        row = bytearray(w)
        for x, (rv, gv, bv) in enumerate(zip(rr, gg, bb)):
            row[x] = nearest(rv, gv, bv)
        rows.append(bytes(row))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    plte = b"".join(bytes(c) for c in PALETTE)
    best = None
    for kind in (0, 1):
        raw = bytearray()
        if kind == 0:
            for row in rows:
                raw.append(0)
                raw += row
        else:
            for row in rows:
                raw.append(1)
                raw += bytes((c - p) & 0xFF
                             for c, p in zip(row, b"\x00" + row[:-1]))
        comp = zlib.compress(bytes(raw), 9)
        if best is None or len(comp) < len(best):
            best = comp
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
           + chunk(b"PLTE", plte)
           + chunk(b"IDAT", best)
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)

# ------------------------------------------------------- engraved capitals font
# One-stroke engraved capitals on a 10 wide x 14 tall grid (y down).
# Every glyph is a list of strokes; every stroke is a polyline of points.
# Drawn thick, it reads like the hand lettering on an old chart.
FONT = {
    "A": [[(0, 14), (5, 0), (10, 14)], [(2.6, 9), (7.4, 9)]],
    "B": [[(1, 0), (1, 14)], [(1, 0), (6.5, 0), (9, 2), (9, 5), (6.5, 7), (1, 7)],
          [(1, 7), (7.5, 7), (9.5, 9.5), (9.5, 12), (7, 14), (1, 14)]],
    "C": [[(9, 2.5), (7, 0), (3, 0), (1, 2), (0, 5), (0, 9), (1, 12), (3, 14), (7, 14), (9, 11.5)]],
    "D": [[(1, 0), (1, 14)], [(1, 0), (6.5, 0), (9, 2.5), (10, 7), (9, 11.5), (6.5, 14), (1, 14)]],
    "E": [[(9, 0), (1, 0), (1, 14), (9, 14)], [(1, 7), (6, 7)]],
    "F": [[(1, 0), (9, 0)], [(1, 0), (1, 14)], [(1, 7), (6.5, 7)]],
    "G": [[(9, 2.5), (7, 0), (3, 0), (1, 2), (0, 5), (0, 9), (1, 12), (3, 14), (7, 14), (9, 12), (9, 8), (5, 8)]],
    "H": [[(1, 0), (1, 14)], [(9, 0), (9, 14)], [(1, 7), (9, 7)]],
    "I": [[(5, 0), (5, 14)], [(2, 0), (8, 0)], [(2, 14), (8, 14)]],
    "J": [[(8, 0), (8, 11), (6, 14), (2.5, 14), (1, 12)], [(3, 0), (8, 0)]],
    "K": [[(1, 0), (1, 14)], [(8.5, 0), (1, 7.5), (9.5, 14)]],
    "L": [[(1, 0), (1, 14), (9, 14)]],
    "M": [[(1, 14), (1, 0), (5, 7.5), (9, 0), (9, 14)]],
    "N": [[(1, 14), (1, 0), (9, 14), (9, 0)]],
    "O": [[(3, 0), (7, 0), (9, 2), (10, 5), (10, 9), (9, 12), (7, 14), (3, 14), (1, 12), (0, 9), (0, 5), (1, 2), (3, 0)]],
    "P": [[(1, 14), (1, 0), (7, 0), (9, 2), (9, 5), (7, 7), (1, 7)]],
    "Q": [[(3, 0), (7, 0), (9, 2), (10, 5), (10, 9), (9, 12), (7, 14), (3, 14), (1, 12), (0, 9), (0, 5), (1, 2), (3, 0)],
          [(6, 10), (10, 14)]],
    "R": [[(1, 14), (1, 0), (7, 0), (9, 2), (9, 5), (7, 7), (1, 7)], [(6, 7), (10, 14)]],
    "S": [[(9, 2), (7, 0), (3, 0), (1, 2), (0, 5), (2, 7), (5, 7), (8, 7.5), (9.5, 10), (9.5, 12), (7, 14), (3, 14), (1, 12.5)]],
    "T": [[(0, 0), (10, 0)], [(5, 0), (5, 14)]],
    "U": [[(1, 0), (1, 11), (3, 14), (7, 14), (9, 11), (9, 0)]],
    "V": [[(0, 0), (5, 14), (10, 0)]],
    "W": [[(0, 0), (2.5, 14), (5, 7), (7.5, 14), (10, 0)]],
    "X": [[(0, 0), (10, 14)], [(10, 0), (0, 14)]],
    "Y": [[(0, 0), (5, 7), (10, 0)], [(5, 7), (5, 14)]],
    "Z": [[(0, 0), (10, 0), (0, 14), (10, 14)]],
    "0": [[(3, 0), (7, 0), (9, 2), (10, 5), (10, 9), (9, 12), (7, 14), (3, 14), (1, 12), (0, 9), (0, 5), (1, 2), (3, 0)]],
    "1": [[(3.5, 2), (5.5, 0), (5.5, 14)], [(2, 14), (9, 14)]],
    "2": [[(1, 2.5), (3, 0), (7, 0), (9, 2), (9, 5), (1, 14), (9, 14)]],
    "3": [[(1, 1.5), (3, 0), (7, 0), (9, 2), (9, 5), (7, 7), (4.5, 7)],
          [(4.5, 7), (8, 7), (9.5, 9), (9.5, 12), (7, 14), (3, 14), (1, 12.5)]],
    "4": [[(7, 0), (7, 14)], [(7, 0), (0.5, 10), (9.5, 10)]],
    "5": [[(9, 0), (1, 0), (1, 7), (7, 7), (9, 9), (9, 12), (7, 14), (3, 14), (1, 12)]],
    "6": [[(8.5, 1), (6, 0), (3, 1), (1, 4), (0, 8), (1, 12), (4, 14), (7, 14), (9, 12), (9, 9), (7, 7), (4, 7), (2, 9)]],
    "7": [[(0, 0), (10, 0), (4, 14)]],
    "8": [[(4, 0), (7, 0), (9, 2), (9, 5), (7, 7), (3, 7), (1, 5), (1, 2), (3, 0)],
          [(3, 7), (1, 9), (1, 12), (3, 14), (7, 14), (9, 12), (9, 9), (7, 7)]],
    "9": [[(1, 13), (3, 14), (6, 13), (9, 10), (10, 7), (9, 3), (6, 0), (3, 0), (1, 2), (1, 5), (3, 7), (6, 7), (8, 5)]],
    " ": [],
    "'": [[(5, 0), (4, 4)]],
    ".": [[(5, 12.5), (5, 13.5)]],
    ",": [[(5, 10), (4, 14)]],
    "-": [[(1, 7), (9, 7)]],
    "!": [[(5, 0), (5, 10)], [(5, 12.5), (5, 13.5)]],
    ":": [[(5, 3.5), (5, 4.5)], [(5, 10), (5, 11)]],
    ";": [[(5, 3.5), (5, 4.5)], [(5, 10), (4, 14)]],
    "(": [[(7, 0), (4, 3), (3, 7), (4, 11), (7, 14)]],
    ")": [[(3, 0), (6, 3), (7, 7), (6, 11), (3, 14)]],
    "/": [[(8, 0), (2, 14)]],
    "?": [[(2, 2), (3.5, 0), (7, 0), (9, 2), (9, 4.5), (7, 7), (5, 7), (5, 9.5)], [(5, 12.5), (5, 13.5)]],
    "&": [[(8, 1.5), (6, 0), (3, 0), (1, 2), (1.5, 5), (4, 7), (8, 11), (9, 13), (7, 14), (3.5, 14), (1, 12)], [(8.5, 6.5), (1.5, 13)]],
}

# Narrow letters get less advance; wide letters more.
ADVANCE = {c: 8.0 for c in FONT}
for _c in "IJ1.,':;!":
    ADVANCE[_c] = 5.5
for _c in "MW":
    ADVANCE[_c] = 10.5
for _c in "OQCG":
    ADVANCE[_c] = 9.0
ADVANCE[" "] = 5.0


def text_width(text, size):
    """Width in px of text at cap-height `size`."""
    scale = size / 14.0
    w = 0.0
    for ch in text:
        w += ADVANCE.get(ch, 8.0) * scale
    return w + max(0, len(text) - 1) * size * 0.06


def draw_text(cv, text, cx, top, size, color, halo=None, align="center",
              tracking=0.06, jitter=0.0):
    """Draw one line of engraved capitals. `top` is the cap top; the text is
    centred on cx (or left/right aligned). A halo pass under the ink keeps
    labels readable over busy ground."""
    text = text.upper()
    scale = size / 14.0
    width = text_width(text, size)
    if align == "center":
        x = cx - width / 2.0
    elif align == "right":
        x = cx - width
    else:
        x = cx
    stroke = max(1.8, size / 9.0)

    def render(col, extra_w, dy=0.0):
        xx = x
        for ch in text:
            glyph = FONT.get(ch)
            if glyph:
                for line in glyph:
                    pts = []
                    for (gx, gy) in line:
                        jx = rng.uniform(-jitter, jitter) if jitter else 0.0
                        jy = rng.uniform(-jitter, jitter) if jitter else 0.0
                        pts.append((xx + gx * scale + jx,
                                    top + gy * scale + dy + jy))
                    cv.polyline(pts, stroke + extra_w, col)
            xx += ADVANCE.get(ch, 8.0) * scale + size * tracking

    if halo is not None:
        render(halo, stroke * 1.7)
    render(color, 0.0)
    return width


def draw_text_block(cv, lines, cx, top, size, color, halo=None, leading=1.35):
    """Several centred lines; returns the bottom y."""
    y = top
    for line in lines:
        draw_text(cv, line, cx, y, size, color, halo=halo)
        y += size * leading
    return y

# ------------------------------------------------------------- island shape
def island_outline(cx, cy, base_r, n=260):
    """A wobbly island outline with a lagoon bite on the east, a cove bite
    on the south-east and a bay on the north-west."""
    pts = []
    for i in range(n):
        th = 2 * math.pi * i / n
        r = base_r * (1 + 0.14 * math.sin(3 * th + 1.2)
                      + 0.09 * math.sin(5 * th + 0.5)
                      + 0.06 * math.sin(2 * th + 2.0)
                      + 0.04 * math.sin(8 * th + 4.0))
        # lagoon bite, east (th ~ 0)
        r *= 1 - 0.30 * math.exp(-((th) ** 2) / 0.05)
        r *= 1 - 0.30 * math.exp(-((th - 2 * math.pi) ** 2) / 0.05)
        # dead man's cove, south-east (th ~ 0.85)
        r *= 1 - 0.22 * math.exp(-((th - 0.85) ** 2) / 0.04)
        # old landing bay, south-west (th ~ 2.6)
        r *= 1 - 0.16 * math.exp(-((th - 2.6) ** 2) / 0.05)
        # bay, north-west (th ~ 3.9): gentler, wider
        r *= 1 - 0.10 * math.exp(-((th - 3.9) ** 2) / 0.08)
        pts.append((cx + r * math.cos(th), cy + r * math.sin(th) * 0.94))
    # smooth the outline so the coast reads hand-drawn, not spiky
    sm = []
    for i in range(n):
        ax = sum(pts[(i + k) % n][0] for k in (-2, -1, 0, 1, 2)) / 5
        ay = sum(pts[(i + k) % n][1] for k in (-2, -1, 0, 1, 2)) / 5
        sm.append((ax, ay))
    return sm


def draw_parchment(cv):
    # soft blotches of age
    for _ in range(26):
        x = rng.uniform(40, W - 40)
        y = rng.uniform(40, H - 40)
        rx = rng.uniform(40, 170)
        ry = rng.uniform(30, 120)
        col = rng.choice([PARCH_DARK, PARCH_DEEP, PARCH_LIGHT, (206, 176, 118)])
        cv.ellipse(x, y, rx, ry, col, alpha=rng.uniform(0.10, 0.22))
    # soft darkening in the corners, like age and handling (no hard ring:
    # a circle across the page would cut through the compass and labels)
    for (cx, cy) in ((0, 0), (W, 0), (0, H), (W, H)):
        cv.ellipse(cx, cy, 260, 200, PARCH_DEEP, alpha=0.10)
        cv.ellipse(cx, cy, 150, 110, PARCH_DEEP, alpha=0.08)
    # speckles and foxing
    for _ in range(2400):
        x = rng.uniform(0, W)
        y = rng.uniform(0, H)
        col = rng.choice([INK_SOFT, PARCH_DEEP, PARCH_DARK])
        cv.disk(x, y, rng.uniform(0.5, 1.4), col, alpha=rng.uniform(0.08, 0.28))
    for _ in range(26):  # a few darker fox spots
        cv.disk(rng.uniform(0, W), rng.uniform(0, H),
                rng.uniform(2, 6), INK_SOFT, alpha=rng.uniform(0.10, 0.22))


def draw_sea(cv, island):
    # pale sea washes, kept off the island
    for _ in range(9):
        x = rng.uniform(60, W - 60)
        y = rng.uniform(60, H - 60)
        if 240 < x < 800 and 250 < y < 820:
            continue
        cv.ellipse(x, y, rng.uniform(60, 150), rng.uniform(40, 100),
                   SEA_WASH, alpha=rng.uniform(0.12, 0.25))
    # wave squiggles, hand-drawn
    for _ in range(46):
        x = rng.uniform(30, W - 30)
        y = rng.uniform(30, H - 30)
        if 230 < x < 810 and 240 < y < 830:
            continue
        s = rng.uniform(8, 20)
        cv.quad((x, y), (x + s * 0.5, y - 4), (x + s, y), 2.0,
                INK_SOFT, alpha=rng.uniform(0.35, 0.6))
        cv.quad((x + 3, y + 6), (x + s * 0.5 + 3, y + 2), (x + s + 3, y + 6),
                1.6, INK_SOFT, alpha=rng.uniform(0.25, 0.45))
    # faint rhumb lines from the compass rose
    for k in range(16):
        a = math.pi * k / 8
        cv.line(848 + 150 * math.cos(a), 168 + 150 * math.sin(a),
                848 + 430 * math.cos(a), 168 + 430 * math.sin(a),
                1.0, INK_SOFT, alpha=0.10)


def draw_tree(cv, x, y, s, color=GREEN_DARK):
    """A little round-topped tree, or a palm when asked."""
    cv.line(x, y, x, y - 10 * s, 2.5 * s, INK, alpha=0.9)
    cv.disk(x, y - 13 * s, 7.5 * s, color)
    cv.disk(x - 4 * s, y - 10 * s, 4 * s, GREEN)
    cv.disk(x + 4 * s, y - 15 * s, 3.5 * s, GREEN_DEEP)


def draw_palm(cv, x, y, s):
    cv.quad((x, y), (x + 3 * s, y - 12 * s), (x + 7 * s, y - 22 * s),
            2.5 * s, INK)
    for a in (-2.6, -1.9, -1.2, -0.5, 0.2):
        ex = x + 7 * s + 12 * s * math.cos(a)
        ey = y - 22 * s + 12 * s * math.sin(a) * 0.6
        cv.quad((x + 7 * s, y - 22 * s), (x + 7 * s + 7 * s * math.cos(a),
                y - 22 * s + 7 * s * math.sin(a) * 0.6), (ex, ey),
                2.0 * s, GREEN_DEEP)
    cv.disk(x + 8 * s, y - 20 * s, 2.2 * s, GOLD)
    cv.disk(x + 5 * s, y - 19 * s, 2.0 * s, GOLD)


def draw_pine(cv, x, y, s):
    cv.line(x, y, x, y - 4 * s, 2 * s, INK)
    for k, w in enumerate((11, 8, 5)):
        yy = y - (5 + k * 6) * s
        cv.polyline([(x - w * s, yy + 3 * s), (x, yy - 3 * s),
                     (x + w * s, yy + 3 * s)], 2.2 * s, GREEN_DARK)
    cv.disk(x, y - 22 * s, 2 * s, GREEN_DEEP)


def draw_volcano(cv, x, y, s):
    # the mountain
    cv.fill_polygon([(x - 52 * s, y), (x - 14 * s, y - 62 * s),
                     (x + 8 * s, y - 62 * s), (x + 50 * s, y)],
                    STONE, alpha=0.95)
    cv.fill_polygon([(x - 52 * s, y), (x - 14 * s, y - 62 * s),
                     (x - 6 * s, y - 20 * s), (x - 30 * s, y)],
                    (104, 94, 84), alpha=0.9)
    # crater + lava
    cv.ellipse(x - 3 * s, y - 62 * s, 11 * s, 4.5 * s, INK_DARK)
    cv.disk(x - 3 * s, y - 60 * s, 5.5 * s, LAVA)
    cv.disk(x - 3 * s, y - 60 * s, 2.5 * s, (232, 120, 60))
    cv.quad((x - 3 * s, y - 58 * s), (x - 8 * s, y - 40 * s), (x - 2 * s, y - 28 * s),
            4.5 * s, LAVA)
    cv.quad((x + 2 * s, y - 58 * s), (x + 10 * s, y - 44 * s), (x + 6 * s, y - 30 * s),
            4 * s, LAVA)
    cv.disk(x - 2 * s, y - 28 * s, 3 * s, LAVA)
    cv.disk(x + 6 * s, y - 30 * s, 2.5 * s, LAVA)
    # snow-ish ash cap lines
    cv.polyline([(x - 14 * s, y - 62 * s), (x - 9 * s, y - 52 * s),
                 (x - 4 * s, y - 58 * s), (x + 2 * s, y - 50 * s),
                 (x + 8 * s, y - 62 * s)], 2 * s, PARCH_LIGHT, alpha=0.8)
    # smoke curls
    cv.bezier((x - 3 * s, y - 66 * s), (x - 22 * s, y - 84 * s),
              (x + 8 * s, y - 96 * s), (x - 6 * s, y - 112 * s),
              4.5 * s, INK_SOFT, alpha=0.6)
    cv.bezier((x - 3 * s, y - 66 * s), (x + 14 * s, y - 82 * s),
              (x - 10 * s, y - 92 * s), (x + 4 * s, y - 104 * s),
              3.5 * s, INK_SOFT, alpha=0.5)
    cv.disk(x - 6 * s, y - 112 * s, 4 * s, INK_SOFT, alpha=0.4)
    cv.disk(x + 4 * s, y - 104 * s, 3.5 * s, INK_SOFT, alpha=0.35)


def draw_wreck(cv, x, y, s, tilt=-0.5):
    """The wreck of the Salty Gull, beached and listing."""
    c, sn = math.cos(tilt), math.sin(tilt)

    def rot(px, py):
        return (x + px * c - py * sn, y + px * sn + py * c)

    hull = [rot(px, py) for px, py in
            [(-34, 0), (34, 0), (26, 14), (-26, 14)]]
    cv.fill_polygon(hull, (96, 66, 40))
    cv.polyline(hull, 2.5 * s, INK_DARK, closed=True)
    mast = [rot(0, 0), rot(4, -34)]
    cv.polyline(mast, 3 * s, INK_DARK)
    # tattered sail
    sail = [rot(4, -32), rot(20, -6), rot(4, -8)]
    cv.fill_polygon(sail, PARCH_LIGHT, alpha=0.9)
    cv.polyline(sail, 2 * s, INK, closed=True)
    cv.line(*rot(-34, 0), *rot(-40, -8), 2 * s, INK_DARK)
    # cargo spilling out
    cv.disk(*rot(-40, 8), 3.5 * s, GOLD)
    cv.disk(*rot(38, 8), 3 * s, GOLD)
    cv.disk(*rot(30, 12), 2.5 * s, RED)


def draw_skull_rock(cv, x, y, s):
    cv.ellipse(x, y, 26 * s, 16 * s, STONE)
    cv.ellipse(x - 8 * s, y - 10 * s, 12 * s, 10 * s, STONE)
    cv.ellipse(x + 10 * s, y - 8 * s, 9 * s, 8 * s, STONE)
    # skull face on the rock, big enough to read at a glance
    cv.ellipse(x - 8 * s, y - 12 * s, 9 * s, 7.5 * s, PARCH_LIGHT)
    cv.disk(x - 11.5 * s, y - 13.5 * s, 2.6 * s, INK_DARK)
    cv.disk(x - 4.5 * s, y - 13.5 * s, 2.6 * s, INK_DARK)
    cv.line(x - 8 * s, y - 10 * s, x - 8 * s, y - 6.5 * s, 2 * s, INK_DARK)
    cv.line(x - 11 * s, y - 6 * s, x - 5 * s, y - 6 * s, 1.6 * s, INK_DARK)
    # waves at its base
    cv.arc(x, y + 8 * s, 30 * s, 0.15 * math.pi, 0.85 * math.pi, 2 * s, INK_SOFT, alpha=0.6)
    cv.arc(x, y + 14 * s, 36 * s, 0.15 * math.pi, 0.85 * math.pi, 1.6 * s, INK_SOFT, alpha=0.45)

# ------------------------------------------------------- compass and monsters
def draw_compass(cv, cx, cy, r):
    cv.ring(cx, cy, r, 2.5, INK)
    cv.ring(cx, cy, r * 0.78, 1.6, INK_SOFT, alpha=0.8)
    cv.disk(cx, cy, 4, INK)
    for k in range(8):
        a = math.pi * k / 4 - math.pi / 2
        long = (k % 2 == 0)
        ln = r * (0.92 if long else 0.62)
        tip = (cx + ln * math.cos(a), cy + ln * math.sin(a))
        base_w = 9 if long else 6
        bx, by = math.cos(a + math.pi / 2), math.sin(a + math.pi / 2)
        tri = [(cx + base_w * bx, cy + base_w * by),
               (cx - base_w * bx, cy - base_w * by), tip]
        if k == 0:  # north point, red
            cv.fill_polygon(tri, RED)
            cv.polyline(tri, 1.6, INK_DARK, closed=True)
        else:
            cv.fill_polygon(tri, INK if long else INK_SOFT)
            cv.polyline(tri, 1.4, INK_DARK, closed=True)
    # fleur on the north point
    cv.disk(cx, cy - r - 2, 3, INK)
    cv.quad((cx, cy - r - 12), (cx - 4, cy - r - 2), (cx, cy - r + 2),
            2, INK)
    cv.quad((cx, cy - r - 12), (cx + 4, cy - r - 2), (cx, cy - r + 2),
            2, INK)


def draw_serpent(cv, x, y, s):
    # a friendly sea serpent, smooth humps and a curious head
    cv.bezier((x - 78 * s, y + 8 * s), (x - 55 * s, y - 22 * s),
              (x - 30 * s, y - 20 * s), (x - 8 * s, y + 2 * s),
              8 * s, (96, 128, 96))
    cv.bezier((x - 8 * s, y + 2 * s), (x + 12 * s, y - 20 * s),
              (x + 32 * s, y - 18 * s), (x + 48 * s, y + 0 * s),
              8 * s, (96, 128, 96))
    cv.bezier((x - 78 * s, y + 8 * s), (x - 55 * s, y - 22 * s),
              (x - 30 * s, y - 20 * s), (x - 8 * s, y + 2 * s),
              8 * s, GREEN_DARK, alpha=0.45)
    for (hx, hy) in [(x - 52 * s, y - 14 * s), (x - 28 * s, y - 12 * s),
                     (x + 6 * s, y - 10 * s), (x + 34 * s, y - 8 * s)]:
        cv.disk(hx, hy, 2.5 * s, GREEN_DEEP, alpha=0.8)
    # head
    hx, hy = x + 66 * s, y - 16 * s
    cv.ellipse(hx, hy, 14 * s, 9 * s, GREEN_DARK)
    cv.ellipse(hx + 2 * s, hy - 2 * s, 9 * s, 5 * s, (96, 128, 96))
    cv.disk(hx + 7 * s, hy - 4 * s, 2.5 * s, PARCH_LIGHT)
    cv.disk(hx + 7.5 * s, hy - 4 * s, 1.2 * s, INK_DARK)
    cv.quad((hx + 11 * s, hy + 2 * s), (hx + 18 * s, hy + 4 * s),
            (hx + 11 * s, hy + 6 * s), 2 * s, INK_DARK)
    # little horns
    cv.line(hx - 4 * s, hy - 7 * s, hx - 8 * s, hy - 14 * s, 2 * s, INK_DARK)
    cv.line(hx + 2 * s, hy - 8 * s, hx + 2 * s, hy - 15 * s, 2 * s, INK_DARK)


def draw_kraken(cv, x, y, s):
    # a kraken, mostly tentacles and bad intentions
    cv.ellipse(x, y, 30 * s, 19 * s, (110, 90, 110))
    cv.ellipse(x, y - 3 * s, 20 * s, 12 * s, (128, 104, 128))
    cv.disk(x - 9 * s, y - 5 * s, 4 * s, PARCH_LIGHT)
    cv.disk(x - 9 * s, y - 5 * s, 2 * s, INK_DARK)
    cv.disk(x + 9 * s, y - 5 * s, 4 * s, PARCH_LIGHT)
    cv.disk(x + 9 * s, y - 5 * s, 2 * s, INK_DARK)
    for dx, bend in ((-24, -30), (0, -38), (24, -30), (-14, 26), (16, 24)):
        cv.bezier((x + dx * s, y + 8 * s),
                  (x + (dx + bend) * s, y + 26 * s),
                  (x + (dx - bend * 0.4) * s, y + 44 * s),
                  (x + (dx + bend * 0.7) * s, y + 58 * s),
                  7 * s, (110, 90, 110))
        for t in (0.35, 0.6, 0.85):
            sx = x + dx * s + bend * s * (0.6 * t)
            sy = y + (8 + 50 * t) * s
            cv.disk(sx, sy, 1.8 * s, PARCH_LIGHT, alpha=0.8)


def draw_route(cv, waypoints):
    """The dotted treasure route, with a little bridge where it crosses
    the river."""
    pts = []
    for a, b in zip(waypoints, waypoints[1:]):
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 9))
        for i in range(n):
            t = i / n
            pts.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
    pts.append(waypoints[-1])
    for (px, py) in pts:
        cv.disk(px, py, 2.6, RED_DARK)
    # start and end markers
    cv.disk(*waypoints[0], 5, RED_DARK)
    cv.disk(*waypoints[0], 2.5, PARCH_LIGHT)


def draw_x_marks(cv, x, y, s):
    # the big red X, slightly unsteady like a hand drew it in a hurry
    cv.wobbly_line(x - 24 * s, y - 24 * s, x + 24 * s, y + 24 * s,
                   8 * s, RED, wob=1.5)
    cv.wobbly_line(x + 24 * s, y - 24 * s, x - 24 * s, y + 24 * s,
                   8 * s, RED, wob=1.5)
    cv.wobbly_line(x - 24 * s, y - 24 * s, x + 24 * s, y + 24 * s,
                   2.5 * s, RED_DARK, wob=1.0)
    cv.wobbly_line(x + 24 * s, y - 24 * s, x - 24 * s, y + 24 * s,
                   2.5 * s, RED_DARK, wob=1.0)
    # a little mound of gold under the X
    cv.ellipse(x, y + 30 * s, 16 * s, 6 * s, (170, 132, 66))
    for dx, dy in ((-8, 28), (0, 26), (8, 29), (-3, 31), (5, 32)):
        cv.disk(x + dx * s, y + dy * s, 2.2 * s, GOLD)

# ------------------------------------------------------------------ the map
def draw_map(cv):
    draw_parchment(cv)
    island = island_outline(500, 545, 295)
    draw_sea(cv, island)

    # --- the island itself: sand rim, land, green heart
    cv.polyline(island, 12, SAND, alpha=0.85, closed=True)
    cv.fill_polygon(island, LAND)
    cv.polyline(island, 3, INK, alpha=0.9, closed=True)
    cv.polyline(island, 1.2, INK_SOFT, alpha=0.5, closed=True)
    # jungle and forest ground
    for (x, y, rx, ry) in [(350, 530, 95, 70), (330, 470, 70, 50),
                           (540, 315, 85, 45), (620, 480, 70, 55)]:
        cv.ellipse(x, y, rx, ry, GREEN, alpha=0.45)
        cv.ellipse(x, y, rx * 0.6, ry * 0.6, GREEN_DARK, alpha=0.35)
    # the lagoon bite, east
    cv.ellipse(745, 548, 58, 30, LAGOON)
    cv.ellipse(745, 548, 38, 18, LAGOON_DEEP, alpha=0.8)
    cv.ellipse(748, 542, 14, 6, PARCH_LIGHT, alpha=0.5)
    # dead man's cove water, south-east
    cv.ellipse(695, 745, 30, 17, LAGOON, alpha=0.9)
    # the river, volcano to lagoon
    river_pts = [(385, 435), (420, 470), (410, 510), (460, 535),
                 (530, 550), (620, 558), (715, 550)]
    cv.polyline(river_pts, 7.5, (70, 118, 128))
    cv.polyline(river_pts, 3, (140, 178, 168), alpha=0.85)

    # --- landmarks
    draw_volcano(cv, 380, 420, 1.0)
    # parrot woods, north
    for (x, y, s) in [(505, 310, 1.0), (535, 325, 1.2), (565, 308, 0.9),
                      (520, 340, 0.8), (585, 330, 1.0), (550, 295, 0.85)]:
        draw_pine(cv, x, y, s)
    # whispering jungle, west
    for (x, y, s) in [(310, 500, 1.0), (350, 530, 1.2), (390, 495, 0.9),
                      (320, 560, 1.1), (370, 570, 1.0), (410, 545, 0.85),
                      (335, 515, 0.9), (395, 585, 1.0)]:
        draw_tree(cv, x, y, s)
    # a few lone trees and palms
    draw_tree(cv, 600, 470, 0.9)
    draw_tree(cv, 640, 440, 0.8)
    draw_palm(cv, 255, 690, 1.0)
    draw_palm(cv, 345, 720, 0.9)
    draw_palm(cv, 720, 640, 0.85)
    # hills, centre
    for (x, y, s) in [(455, 445, 1.0), (500, 425, 0.8)]:
        cv.arc(x, y, 26 * s, math.pi, 2 * math.pi, 2.5, INK_SOFT, alpha=0.7)
        cv.arc(x, y + 4, 16 * s, math.pi, 2 * math.pi, 2, INK_SOFT, alpha=0.5)
    # the wreck, beached south
    draw_wreck(cv, 480, 782, 1.1)
    # skull rock, off the north-west shore
    draw_skull_rock(cv, 165, 270, 1.0)

    # --- the dotted route and the X
    route = [(320, 690), (360, 635), (415, 605), (470, 575),
             (530, 550), (575, 560), (612, 570)]
    draw_route(cv, route)
    # riddle bridge where the route crosses the river
    cv.line(520, 540, 542, 560, 3, INK_DARK)
    cv.line(526, 536, 548, 556, 3, INK_DARK)
    for t in (0.25, 0.5, 0.75):
        x = 520 + 22 * t
        cv.line(x, 540 + 2 * t, x + 4, 544 + 2 * t, 1.6, INK_DARK)
    draw_x_marks(cv, 620, 575, 1.0)

    # --- sea life and the compass
    draw_compass(cv, 848, 168, 52)
    draw_text(cv, "N", 848, 92, 20, INK_DARK)
    draw_serpent(cv, 170, 855, 1.0)
    draw_kraken(cv, 865, 855, 1.0)
    # birds over the sea
    for (x, y, s) in [(700, 205, 1.0), (745, 185, 0.8), (250, 175, 0.9)]:
        cv.arc(x - 7 * s, y, 7 * s, math.pi * 1.15, math.pi * 1.85, 2, INK, alpha=0.8)
        cv.arc(x + 7 * s, y, 7 * s, math.pi * 1.15, math.pi * 1.85, 2, INK, alpha=0.8)

    # --- title cartouche
    draw_text(cv, "THE ISLE OF", 512, 34, 30, INK_DARK, halo=PARCH_LIGHT)
    draw_text(cv, "WANDERING GOLD", 512, 68, 42, INK_DARK, halo=PARCH_LIGHT)
    cv.line(300, 126, 470, 126, 2, INK_SOFT, alpha=0.8)
    cv.line(554, 126, 724, 126, 2, INK_SOFT, alpha=0.8)
    cv.disk(512, 126, 3, INK_SOFT)
    draw_text(cv, "SURVEY'D BY CAPT. R. GULL - ANNO 1749", 512, 136, 19,
              INK, halo=PARCH_LIGHT)

    # --- place labels (nine, all with a halo so they read over the ground)
    halo = PARCH_LIGHT
    draw_text(cv, "MOUNT GRUMBLE", 380, 272, 25, INK_DARK, halo=halo)
    draw_text(cv, "PARROT WOODS", 545, 360, 25, INK_DARK, halo=halo)
    draw_text(cv, "DO NOT FEED THE PARROTS", 545, 396, 15, INK_SOFT, halo=halo)
    draw_text(cv, "WHISPERING", 350, 438, 25, INK_DARK, halo=halo)
    draw_text(cv, "JUNGLE", 350, 470, 25, INK_DARK, halo=halo)
    draw_text(cv, "MERMAID'S", 745, 428, 22, INK_DARK, halo=halo)
    draw_text(cv, "LAGOON", 745, 456, 22, INK_DARK, halo=halo)
    draw_text(cv, "CAPTAIN'S CACHE", 600, 500, 26, RED_DARK, halo=halo)
    draw_text(cv, "DIG HERE!", 722, 596, 16, RED_DARK, halo=halo, align="left")
    draw_text(cv, "DEAD MAN'S", 655, 682, 23, INK_DARK, halo=halo)
    draw_text(cv, "COVE", 655, 710, 23, INK_DARK, halo=halo)
    draw_text(cv, "WRECK OF THE", 455, 695, 23, INK_DARK, halo=halo)
    draw_text(cv, "SALTY GULL", 455, 723, 23, INK_DARK, halo=halo)
    draw_text(cv, "OLD LANDING", 280, 762, 23, INK_DARK, halo=halo)
    draw_text(cv, "SKULL ROCK", 165, 212, 24, INK_DARK, halo=halo)
    draw_text(cv, "HERE BE MONSTERS", 170, 912, 17, INK_SOFT, halo=halo)
    draw_text(cv, "BEWARE THE KRAKEN", 860, 922, 17, INK_SOFT, halo=halo)
    # leader lines from labels to their places
    cv.line(455, 749, 472, 770, 1.5, INK_SOFT, alpha=0.7)
    cv.line(668, 736, 688, 742, 1.5, INK_SOFT, alpha=0.7)
    cv.line(165, 238, 165, 254, 1.5, INK_SOFT, alpha=0.7)
    cv.line(745, 482, 745, 516, 1.5, INK_SOFT, alpha=0.7)

    # --- scale bar and frame
    cv.line(460, 962, 580, 962, 3, INK_DARK)
    cv.line(460, 954, 460, 970, 3, INK_DARK)
    cv.line(520, 956, 520, 968, 2, INK_DARK)
    cv.line(580, 954, 580, 970, 3, INK_DARK)
    draw_text(cv, "0", 448, 948, 14, INK, align="right")
    draw_text(cv, "5 LEAGUES", 590, 948, 14, INK, align="left")
    cv.polyline([(14, 14), (W - 14, 14), (W - 14, H - 14), (14, H - 14)],
                2.5, INK, alpha=0.85, closed=True)
    cv.polyline([(24, 24), (W - 24, 24), (W - 24, H - 24), (24, H - 24)],
                1.2, INK_SOFT, alpha=0.6, closed=True)


def main():
    cv = Canvas(W, H, PARCHMENT)
    draw_map(cv)
    for outdir in ("/output", "output"):
        try:
            os.makedirs(outdir, exist_ok=True)
            path = os.path.join(outdir, "entry.png")
            size = save_indexed_png(cv, path)
            print(f"wrote {path}: {W}x{H}, {size} bytes")
            if size > 1048576:
                print("WARNING: over the 1 MiB gate", file=sys.stderr)
                return 1
            return 0
        except OSError as exc:
            print(f"cannot write to {outdir}: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
