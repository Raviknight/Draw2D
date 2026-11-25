# SPDX-License-Identifier: LGPL-2.1-or-later
"""Datum flags and geometric tolerance (feature control) frames.

TechDraw has no tool for these, so DrawingKit draws them as SVG and places them
as TechDraw symbols (DrawViewSymbol): you can drag them, and edit the
tolerance value and datum letters as the symbol's editable texts.
Characteristic symbols are drawn as lines, so they do not depend on fonts.
"""

from . import core

H = 7.0           # frame height in paper mm (2 x 3.5 mm lettering, ISO 1101)
FONT = 3.5
STROKE = 0.35
NS = ('xmlns="http://www.w3.org/2000/svg" '
      'xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace"')

# name -> label shown in the dialog
CHARACTERISTICS = [
    ("position", "Position"),
    ("flatness", "Flatness"),
    ("straightness", "Straightness"),
    ("circularity", "Circularity (roundness)"),
    ("cylindricity", "Cylindricity"),
    ("perpendicularity", "Perpendicularity"),
    ("parallelism", "Parallelism"),
    ("angularity", "Angularity"),
    ("profile_line", "Profile of a line"),
    ("profile_surface", "Profile of a surface"),
    ("concentricity", "Concentricity / coaxiality"),
    ("symmetry", "Symmetry"),
    ("circular_runout", "Circular run-out"),
    ("total_runout", "Total run-out"),
]
MODIFIERS = {"": "", "MMC": "M", "LMC": "L", "RFS": "S"}   # RFS: regardless of feature size (ASME)


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def symbol_paths(name, x0, y0, h=H):
    """SVG elements drawing a characteristic symbol in the h x h cell at (x0, y0)."""
    m = h * 0.2
    l, r, t, b = x0 + m, x0 + h - m, y0 + m, y0 + h - m
    cx, cy = x0 + h / 2.0, y0 + h / 2.0
    s = h - 2 * m
    P = '<path d="%s"/>'
    C = '<circle cx="%.3f" cy="%.3f" r="%.3f"/>'
    if name == "straightness":
        return P % "M %.3f,%.3f H %.3f" % (l, cy, r)
    if name == "flatness":
        k = s * 0.3
        return P % "M %.3f,%.3f L %.3f,%.3f L %.3f,%.3f L %.3f,%.3f Z" % (
            l, cy + s * 0.2, l + k, cy - s * 0.2, r, cy - s * 0.2, r - k, cy + s * 0.2)
    if name == "circularity":
        return C % (cx, cy, s / 2.0)
    if name == "cylindricity":
        rr = s * 0.32
        return (C % (cx, cy, rr)) + P % "M %.3f,%.3f L %.3f,%.3f M %.3f,%.3f L %.3f,%.3f" % (
            l, b, cx - rr * 0.2, t, cx + rr * 0.2, b, r, t)
    if name == "perpendicularity":
        return P % "M %.3f,%.3f H %.3f M %.3f,%.3f V %.3f" % (l, b, r, cx, b, t)
    if name == "parallelism":
        k = s * 0.35
        return P % "M %.3f,%.3f L %.3f,%.3f M %.3f,%.3f L %.3f,%.3f" % (
            l, b, l + k + s * 0.2, t, cx, b, cx + k + s * 0.2, t)
    if name == "angularity":
        return P % "M %.3f,%.3f L %.3f,%.3f M %.3f,%.3f H %.3f" % (l, b, r, t + s * 0.2, l, b, r)
    if name == "profile_line":
        return P % "M %.3f,%.3f A %.3f,%.3f 0 0 1 %.3f,%.3f" % (l, cy + s * 0.25, s / 2, s / 2,
                                                                r, cy + s * 0.25)
    if name == "profile_surface":
        return P % "M %.3f,%.3f A %.3f,%.3f 0 0 1 %.3f,%.3f Z" % (l, cy + s * 0.25, s / 2, s / 2,
                                                                  r, cy + s * 0.25)
    if name == "position":
        rr = s * 0.32
        return (C % (cx, cy, rr)) + P % "M %.3f,%.3f H %.3f M %.3f,%.3f V %.3f" % (
            l, cy, r, cx, t, b)
    if name == "concentricity":
        return (C % (cx, cy, s / 2.0)) + (C % (cx, cy, s / 4.0))
    if name == "symmetry":
        return P % "M %.3f,%.3f H %.3f M %.3f,%.3f H %.3f M %.3f,%.3f H %.3f" % (
            l + s * 0.2, cy - s * 0.3, r - s * 0.2, l, cy, r, l + s * 0.2, cy + s * 0.3, r - s * 0.2)
    if name in ("circular_runout", "total_runout"):
        arrow = ('<path d="M %.3f,%.3f L %.3f,%.3f"/>'
                 '<path style="fill:#000000" d="M %.3f,%.3f L %.3f,%.3f L %.3f,%.3f Z"/>')
        out = arrow % (l, b, r - s * 0.15, t + s * 0.15,
                       r, t, r - s * 0.35, t + s * 0.05, r - s * 0.05, t + s * 0.35)
        if name == "total_runout":
            out += P % "M %.3f,%.3f H %.3f" % (l, b, r)
        return out
    raise ValueError("Unknown characteristic %r" % name)


def _text(x, y, txt, editable=None, anchor="middle", size=FONT, font=None):
    ed = ' freecad:editable="%s"' % editable if editable else ""
    if not font:
        from . import fonts
        font = fonts.drawing_font()
    font = font.replace("'", "").replace('"', "").replace(";", "")
    return ('<text x="%.3f" y="%.3f"%s style="font-size:%gpx;font-family:\'%s\';'
            'text-anchor:%s;fill:#000000;stroke:none"><tspan>%s</tspan></text>'
            % (x, y, ed, size, _esc(font), anchor, _esc(txt)))


def _diameter_sign(x, cy, d=2.6):
    r = d / 2.0
    return ('<circle cx="%.3f" cy="%.3f" r="%.3f"/>'
            '<path d="M %.3f,%.3f L %.3f,%.3f"/>' % (x + r, cy, r, x - 0.3, cy + r + 0.4,
                                                     x + d + 0.3, cy - r - 0.4))


def frame_svg(characteristic, tolerance, diameter=False, modifier="", datums=()):
    """SVG text of a feature control frame like  | ⌖ | ⌀0.05 Ⓜ | A | B |."""
    datums = [d.strip().upper() for d in datums if d and d.strip()]
    tol_w = len(tolerance) * 2.2 + 3.0 + (3.4 if diameter else 0.0) + (4.2 if modifier else 0.0)
    W = H + tol_w + H * len(datums)
    cy = H / 2.0
    base = cy + FONT * 0.36
    parts = ['<svg %s width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (NS, W, H, W, H),
             '<g style="fill:none;stroke:#000000;stroke-width:%g;stroke-linecap:round;'
             'stroke-linejoin:round">' % STROKE,
             '<rect x="0" y="0" width="%.3f" height="%.3f"/>' % (W, H)]
    x = H
    parts.append('<path d="M %.3f,0 V %.3f"/>' % (x, H))
    parts.append(symbol_paths(characteristic, 0, 0))
    tx = x + 1.5
    if diameter:
        parts.append(_diameter_sign(tx, cy))
        tx += 3.4
    x += tol_w
    parts.append('<path d="M %.3f,0 V %.3f"/>' % (x, H))
    if modifier:
        parts.append('<circle cx="%.3f" cy="%.3f" r="1.8"/>' % (x - 2.4, cy))
    for i, _ in enumerate(datums[:-1]):
        parts.append('<path d="M %.3f,0 V %.3f"/>' % (x + H * (i + 1), H))
    parts.append('</g>')
    parts.append(_text(tx, base, tolerance, "tolerance", "start"))
    if modifier:
        parts.append(_text(x - 2.4, cy + 1.0, MODIFIERS.get(modifier, modifier), None,
                           "middle", 2.6))
    for i, d in enumerate(datums):
        parts.append(_text(x + H * i + H / 2.0, base, d, "datum%d" % (i + 1)))
    parts.append('</svg>')
    return "".join(parts), W, H


DATUM_STEM = 5.0
DATUM_TRI = 3.0


def datum_svg(letter, side="up"):
    """SVG text of a datum flag: letter in a box, stem, filled triangle whose
    base lies on the feature. ``side`` is where the box sits relative to the
    feature ('up', 'down', 'left', 'right'). Returns (svg, width, height,
    (dx, dy)) where (dx, dy) is the triangle base's middle relative to the
    symbol centre, in page mm with Y up."""
    stem, tri = DATUM_STEM, DATUM_TRI
    th = tri * 0.87
    length = H + stem + th          # along the flag
    vertical = side in ("up", "down")
    W, Ht = (H, length) if vertical else (length, H)
    # build for side == 'up' in a local frame, then map
    def m(x, y):
        """local (x across, y along from box top) -> svg coords"""
        if side == "up":
            return x, y
        if side == "down":
            return x, length - y
        if side == "left":
            return y, x
        return length - y, x   # right
    c = H / 2.0
    box = [m(0, 0), m(H, 0), m(H, H), m(0, H)]
    s0, s1 = m(c, H), m(c, H + stem)
    t0, t1, t2 = m(c - tri / 2, length), m(c + tri / 2, length), m(c, H + stem)
    bx = min(p[0] for p in box)
    by = min(p[1] for p in box)
    parts = ['<svg %s width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (NS, W, Ht, W, Ht),
             '<g style="fill:none;stroke:#000000;stroke-width:%g;stroke-linejoin:round">' % STROKE,
             '<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/>' % (bx, by, H, H),
             '<path d="M %.3f,%.3f L %.3f,%.3f"/>' % (s0 + s1),
             '<path style="fill:#000000" d="M %.3f,%.3f L %.3f,%.3f L %.3f,%.3f Z"/>' % (t0 + t1 + t2),
             '</g>',
             _text(bx + c, by + c + FONT * 0.36, letter.strip().upper(), "datum"),
             '</svg>']
    base = m(c, length)
    dx, dy = base[0] - W / 2.0, -(base[1] - Ht / 2.0)   # svg y down -> page y up
    return "".join(parts), W, Ht, (dx, dy)


def datum_side(view_box, x, y):
    """Which way a datum flag on (x, y) should stick out of the view box:
    away from the nearest edge."""
    x0, y0, x1, y1 = view_box
    d = {"left": abs(x - x0), "right": abs(x1 - x), "down": abs(y - y0), "up": abs(y1 - y)}
    return min(d, key=d.get)


SYMBOL_KIND = "DrawingKitSymbol"


def _add_symbol(page, name, label, svg, x, y, kind):
    doc = page.Document
    sym = doc.addObject("TechDraw::DrawViewSymbol", name)
    page.addView(sym)
    sym.Symbol = svg
    sym.ScaleType = "Custom"
    sym.Scale = 1.0
    sym.X, sym.Y = x, y
    sym.Label = label
    sym.addProperty("App::PropertyString", SYMBOL_KIND, "DrawingKit", "Kind of DrawingKit symbol")
    setattr(sym, SYMBOL_KIND, kind)
    return sym


def add_frame(page, x, y, characteristic, tolerance, diameter=False, modifier="", datums=()):
    """Feature control frame centred at page point (x, y)."""
    svg, w, h = frame_svg(characteristic, tolerance, diameter, modifier, datums)
    label = "GD&T %s %s%s %s" % (characteristic, "dia " if diameter else "", tolerance,
                                 " ".join(d for d in datums if d))
    return _add_symbol(page, "ToleranceFrame", label.strip(), svg, x, y, "frame")


def datum_placement(page, x, y):
    """Side for a datum clicked at (x, y): away from the view it touches."""
    best = None
    for v in core.part_views(page):
        try:
            x0, y0, x1, y1 = core.view_box(v)
        except Exception:
            continue
        m = 3.0
        if x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, (x0, y0, x1, y1))
    return datum_side(best[1], x, y) if best else "up"


def add_datum(page, x, y, letter=None, side=None):
    """Datum flag whose triangle sits on page point (x, y), sticking out of
    the view (or ``side``: 'up', 'down', 'left', 'right')."""
    letter = (letter or next_datum_letter(page.Document)).strip().upper()
    side = side or datum_placement(page, x, y)
    svg, w, h, (dx, dy) = datum_svg(letter, side)
    # symbols are placed by their centre; put the triangle on the clicked point
    return _add_symbol(page, "Datum", "Datum %s" % letter, svg, x - dx, y - dy, "datum")


def used_datum_letters(doc):
    out = []
    for o in doc.Objects:
        if getattr(o, SYMBOL_KIND, "") == "datum":
            # a symbol's EditableTexts is a plain list of the current values
            texts = list(getattr(o, "EditableTexts", []) or [])
            out.append((texts[0] if texts and texts[0] else o.Label.replace("Datum", "")).strip())
    return out


def next_datum_letter(doc):
    return core.next_free_letter(used_datum_letters(doc) + core.view_letters(doc))
