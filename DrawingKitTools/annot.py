# SPDX-License-Identifier: LGPL-2.1-or-later
"""Attached annotations: datum flags, geometric tolerance frames and datum
targets that hang on a feature of a view (or on a dimension) and follow it.

Each annotation is a TechDraw symbol (DrawViewSymbol) whose SVG is drawn by
DrawingKit: the part touching the feature (datum triangle, leader arrow,
target cross) sits on the *anchor*; the box sits at the anchor plus an
*offset* you can drag. When the view moves or is rescaled, or you drag the
box, the symbol is redrawn so the leader still reaches the feature.

Anchor kinds:
  ("view", view, (lx, ly))   point in the view, unscaled view coords, Y up
  ("dim", dimension, end)    end 0/1 of a dimension line (axis/centre-plane datum)
  ("free", None, (x, y))     page point, nothing attached
"""

import json
import math

import FreeCAD as App

from . import core
from . import gdt

KIND = "DrawingKitAnnot"        # 'datum', 'frame', 'target'
LINK = "DrawingKitAttach"       # view or dimension
ANCHOR = "DrawingKitAnchor"     # Vector: local point (view) or (end, 0, 0) (dim) or page point
ANCHOR_KIND = "DrawingKitAnchorKind"
OFFSET = "DrawingKitOffset"     # Vector: box centre minus anchor, page mm
PARAMS = "DrawingKitParams"     # JSON
CENTRE = "DrawingKitCentre"     # Vector: where we last put the symbol centre

H = gdt.H
STROKE = gdt.STROKE
ARROW = 3.0
PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"
SIZES = [2.5, 3.5, 5.0, 7.0, 10.0]


def default_size():
    """Text height (mm) used for new DrawingKit annotations and notes.
    Until one is set, TechDraw's own dimension text size, so notes match."""
    try:
        v = App.ParamGet(PREFS).GetFloat("TextSize", 0.0)
        if v > 0.5:
            return v
        v = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions").GetFloat("FontSize", 3.5)
        return v if v > 0.5 else 3.5
    except Exception:
        return 3.5


def default_font():
    """The drawing font: the one chosen in Text style, else DejaVu Sans."""
    from . import fonts
    return fonts.drawing_font()


def set_defaults(size=None, font=None):
    g = App.ParamGet(PREFS)
    if size:
        g.SetFloat("TextSize", float(size))
    if font:
        g.SetString("Font", font)


def text_scale(p):
    """Everything in a symbol scales with its text: 3.5 mm text -> 1.0."""
    try:
        return max(0.2, float(p.get("tsize", 3.5)) / 3.5)
    except (TypeError, ValueError):
        return 1.0

_busy = set()
_bodies = {}      # (doc, name) -> page box of the symbol's main part, from the last render


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------
def view_point(view, local):
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    return cx + local[0] * s, cy + local[1] * s


def page_to_local(view, x, y):
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    return (x - cx) / s, (y - cy) / s


def _edge_points(view, name):
    """End points (unscaled, Y up) of a referenced edge/vertex 'EdgeN'/'VertexN'."""
    idx = int("".join(c for c in name if c.isdigit()))
    if name.startswith("Edge"):
        e = view.getEdgeByIndex(idx)
        c = getattr(e, "Curve", None)
        if c is not None and "Circle" in c.TypeId:
            return [App.Vector(c.Center)], c.Radius
        return [App.Vector(v.Point) for v in e.Vertexes], None
    v = view.getVertexByIndex(idx)
    return [App.Vector(v.Point)], None


def dimension_ends(dim):
    """((x0, y0), (x1, y1)) page points of a dimension line, and the
    unit vector along it from end 0 to end 1. Worked out from the
    dimension's references and label position (TechDraw only computes its
    arrow points while drawing on screen)."""
    refs = dim.References2D
    if not refs:
        raise ValueError("Dimension has no references")
    view = refs[0][0]
    names = refs[0][1] if isinstance(refs[0][1], (list, tuple)) else [refs[0][1]]
    s = core.view_scale(view)
    cx, cy = core.view_center(view)
    lab = App.Vector(float(dim.X) / s, float(dim.Y) / s, 0)   # label, unscaled local
    pts, radius = [], None
    items = []          # per reference: ('pt', p) or ('line', a, b)
    for n in names:
        p, r = _edge_points(view, n)
        pts.extend(p)
        radius = radius or r
        if n.startswith("Edge") and r is None and len(p) >= 2:
            items.append(("line", p[0], p[-1]))
        else:
            items.append(("pt", p[0]))
    t = dim.Type
    if t == "Distance" and len(items) == 2 and any(i[0] == "line" for i in items):
        # line to line / point to line: measured square to the line
        def foot(q, a, b):
            u = b - a
            u.normalize()
            return a + u * (q - a).dot(u)
        if items[0][0] == "line" and items[1][0] == "line":
            a = foot(lab, items[0][1], items[0][2])
            b = foot(a, items[1][1], items[1][2])
        else:
            pt = items[0][1] if items[0][0] == "pt" else items[1][1]
            ln = items[1] if items[1][0] == "line" else items[0]
            a, b = pt, foot(pt, ln[1], ln[2])
        u = b - a
        if u.Length < 1e-9:
            raise ValueError("Cannot place a datum on this dimension")
        u.normalize()
        a, b = _referencing_line(dim, a, b, lab, s, pts)
        return (cx + a.x * s, cy + a.y * s), (cx + b.x * s, cy + b.y * s), (u.x, u.y)
    if t in ("Diameter", "Radius") and radius:
        c = pts[0]
        u = lab - c
        if u.Length < 1e-9:
            u = App.Vector(1, 0, 0)
        u.normalize()
        a, b = c - u * radius, c + u * radius
        if t == "Radius":
            a = c
    else:
        p0, p1 = pts[0], pts[-1]
        if t == "DistanceX":
            a, b = App.Vector(min(p0.x, p1.x), lab.y, 0), App.Vector(max(p0.x, p1.x), lab.y, 0)
        elif t == "DistanceY":
            a, b = App.Vector(lab.x, min(p0.y, p1.y), 0), App.Vector(lab.x, max(p0.y, p1.y), 0)
        else:
            d = p1 - p0
            if d.Length < 1e-9:
                raise ValueError("Cannot place a datum on this dimension")
            d.normalize()
            n = App.Vector(-d.y, d.x, 0)
            off = (lab - p0).dot(n)
            a, b = p0 + n * off, p1 + n * off
        a, b = _referencing_line(dim, a, b, lab, s, pts)
    u = b - a
    u.normalize()
    return (cx + a.x * s, cy + a.y * s), (cx + b.x * s, cy + b.y * s), (u.x, u.y)


def _referencing_line(dim, a, b, lab, s, pts):
    """Where TechDraw's 'Referencing' styles really draw a straight
    dimension line (unscaled local a, b), given the one through the label.
    The text stands upright on a short reference line: a horizontal
    dimension line runs just under the text; any other line meets the end
    of the reference line nearer the measured feature."""
    vo = getattr(dim, "ViewObject", None)
    if vo is None or "Referencing" not in str(getattr(vo, "StandardAndStyle", "")):
        return a, b
    from . import clearance, fonts, textlayer
    fs = clearance._dim_font(dim)
    u = b - a
    if u.Length < 1e-9:
        return a, b
    u.normalize()
    if abs(u.y) < 1e-6:
        # under the text (TechDraw's text is about 1.37 x its cap height):
        # half its line height and a small gap
        dy = fs * 0.86 / s
        return App.Vector(a.x, lab.y - dy, 0), App.Vector(b.x, lab.y - dy, 0)
    try:
        width = fonts.dimension_text_width(textlayer.dimension_text(dim), fs,
                                           fonts.dimension_font(dim))
    except Exception:
        return a, b
    half = (width / 2.0 + fs * 0.33) / s
    if abs(u.x) < 1e-6:
        mid = sum(p.x for p in pts) / len(pts)
        x = lab.x + half if abs(lab.x + half - mid) < abs(lab.x - half - mid) else lab.x - half
        return App.Vector(x, a.y, 0), App.Vector(x, b.y, 0)
    return a, b


def anchor_page(sym):
    """Page point of the anchor and the outward direction at it."""
    kind = getattr(sym, ANCHOR_KIND, "free")
    link = getattr(sym, LINK, None)
    a = getattr(sym, ANCHOR, App.Vector())
    params = _params(sym)
    if params.get("dir"):
        sdir = tuple(params["dir"])
    else:
        side = params.get("side", "up")
        sdir = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}.get(side, (0, 1))
    if kind == "view" and link is not None:
        return view_point(link, (a.x, a.y)), sdir
    if kind == "dimlabel" and link is not None:
        # under a dimension's value (ISO: frame attached to a size dimension)
        view = link.References2D[0][0]
        cx, cy = core.view_center(view)
        return (cx + float(link.X), cy + float(link.Y)), (0, -1)
    if kind == "frame" and link is not None:
        # on the frame's bottom edge (or top, with the box above), mid-way
        b = body_box(link)
        off = getattr(sym, OFFSET, App.Vector())
        # a.x: along the edge from its middle, kept on the frame if it shrinks
        half = max(0.0, (b[2] - b[0]) / 2.0 - 1.5)
        x = (b[0] + b[2]) / 2.0 + max(-half, min(half, a.x))
        if off.y >= 0:
            return (x, b[3]), (0, 1)
        return (x, b[1]), (0, -1)
    if kind == "dim" and link is not None:
        p0, p1, (ux, uy) = dimension_ends(link)
        if int(round(a.x)) == 0:
            return p0, (-ux, -uy)
        return p1, (ux, uy)
    return (a.x, a.y), sdir


# ---------------------------------------------------------------------------
# Drawing (page coordinates, Y up) -> SVG
# ---------------------------------------------------------------------------
SVG_MARK = "<!--DrawingKit 2-->"     # drawn with the tight 0.5 mm margin


class Sketch:
    def __init__(self, k=1.0, font=None):
        self.items = []
        self.extents = []                # each item's points (for its bounds)
        self.k = k                       # text size / 3.5 mm
        self.font = font or default_font()
        self.body = None                 # the part that must not clash (no leader)
        self.glyphs = []                 # symbols drawn as lines: (char, x, y, size)

    def glyph(self, char, x, y, size):
        """Note a symbol drawn with lines (⌖, ⌀, Ⓜ...) at page point (x, y),
        its middle, so the PDF's text layer can carry the character."""
        if char:
            self.glyphs.append((char, x, y, size))

    @property
    def xs(self):
        return [p[0] for pts in self.extents for p in pts]

    @property
    def ys(self):
        return [p[1] for pts in self.extents for p in pts]

    def _add(self, item, pts):
        self.items.append(item)
        self.extents.append([tuple(p) for p in pts])

    def line(self, a, b, width=STROKE, dash=False):
        self._add(("line", a, b, width, dash), [a, b])

    def poly(self, pts, fill=False):
        self._add(("poly", pts, fill), pts)

    def rect(self, x0, y0, x1, y1, width=STROKE):
        self._add(("rect", x0, y0, x1, y1, width), [(x0, y0), (x1, y1)])

    def circle(self, c, r, width=STROKE):
        self._add(("circle", c, r, width), [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)])

    def raw(self, x0, ytop, w, h, fragment):
        """SVG fragment drawn in its own y-down box w x h, top-left at
        page point (x0, ytop)."""
        self._add(("raw", x0, ytop, fragment), [(x0, ytop), (x0 + w, ytop - h)])

    def split_leader(self, margin=0.8):
        """Take the leader (every line, arrow or circle reaching outside the
        body) out into a Sketch of its own, so the body's box on the sheet
        is just the body: a symbol's box catches the mouse, and a leader
        across the part would keep the part and its dimensions from being
        clicked. None if there is no body or nothing outside it."""
        if self.body is None or not self.items:
            return None
        x0, y0, x1, y1 = self.body
        x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
        keep, lead = [], []
        for it, pts in zip(self.items, self.extents):
            inside = all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts)
            (keep if inside or it[0] in ("text", "raw") else lead).append((it, pts))
        if not lead or not keep:
            return None
        other = Sketch(self.k, self.font)
        for it, pts in lead:
            other._add(it, pts)
        self.items = [it for it, _ in keep]
        self.extents = [pts for _, pts in keep]
        return other

    def text(self, x, y, txt, size=gdt.FONT, anchor="middle", editable=None, rot=0):
        """``size`` is the size at 3.5 mm lettering; it is scaled by k.
        ``rot``: 90 reads upwards (for vertical ordinate values)."""
        size = size * self.k
        w = len(txt) * size * 0.6
        x0 = x if anchor == "start" else x - w
        x1 = x + w
        if rot:
            # rotated about (x, y): along the text is now up the page
            pts = [(x - size * 0.7, y + (x0 - x)), (x + size * 0.4, y + (x1 - x))]
        else:
            pts = [(x0, y - size * 0.4), (x1, y + size * 0.7)]
        self._add(("text", x, y, txt, size, anchor, editable, rot), pts)

    def svg(self):
        m = 0.5          # just past the line widths: the box catches the mouse
        x0, x1 = min(self.xs) - m, max(self.xs) + m
        y0, y1 = min(self.ys) - m, max(self.ys) + m
        W, Hh = x1 - x0, y1 - y0
        X = lambda x: x - x0
        Y = lambda y: y1 - y
        out = ['<svg %s width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">' % (gdt.NS, W, Hh, W, Hh),
               SVG_MARK,
               '<g style="fill:none;stroke:#000000;stroke-linecap:round;stroke-linejoin:round">']
        from . import lineweights
        f = lineweights.symbol_factor()      # the sheet's thin line (Line weights tool)
        texts = []
        for it in self.items:
            k = it[0]
            if k == "line":
                _, a, b, w, dash = it
                out.append('<path style="stroke-width:%g%s" d="M %.3f,%.3f L %.3f,%.3f"/>'
                           % (w * f, ";stroke-dasharray:3,1.5" if dash else "",
                              X(a[0]), Y(a[1]), X(b[0]), Y(b[1])))
            elif k == "poly":
                _, pts, fill = it
                d = " L ".join("%.3f,%.3f" % (X(p[0]), Y(p[1])) for p in pts)
                out.append('<path style="stroke-width:%g%s" d="M %s Z"/>'
                           % (STROKE * f, ";fill:#000000" if fill else "", d))
            elif k == "rect":
                _, a0, b0, a1, b1, w = it
                out.append('<rect style="stroke-width:%g" x="%.3f" y="%.3f" width="%.3f" height="%.3f"/>'
                           % (w * f, X(min(a0, a1)), Y(max(b0, b1)), abs(a1 - a0), abs(b1 - b0)))
            elif k == "raw":
                _, rx, rtop, frag = it
                out.append('<g style="stroke-width:%g" transform="translate(%.3f,%.3f)">%s</g>'
                           % (STROKE * f, X(rx), Y(rtop), frag))
            elif k == "circle":
                _, c, r, w = it
                out.append('<circle style="stroke-width:%g" cx="%.3f" cy="%.3f" r="%.3f"/>'
                           % (w * f, X(c[0]), Y(c[1]), r))
            else:
                _, x, y, txt, size, anchor, ed, rot = it
                t = gdt._text(X(x), Y(y) + size * 0.36, txt, ed, anchor, size, self.font)
                if rot:
                    # turn about (x, y); the baseline shift keeps it centred
                    t = t.replace('<text ', '<text transform="rotate(%g %.3f %.3f)" '
                                  % (-rot, X(x), Y(y)), 1)
                texts.append(t)
        out.append('</g>')
        out.extend(texts)
        out.append('</svg>')
        return "".join(out), ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _box_edge_towards(cx, cy, w, h, px, py):
    """Point on the box (centre cx, cy, size w x h) edge facing (px, py)."""
    dx, dy = px - cx, py - cy
    if abs(dx) * h >= abs(dy) * w:
        return (cx + math.copysign(w / 2.0, dx), cy)
    return (cx, cy + math.copysign(h / 2.0, dy))


def draw_datum(sk, anchor, sdir, box_centre, letter):
    k = sk.k
    Hk = H * k
    ax, ay = anchor
    sx, sy = sdir
    tri = gdt.DATUM_TRI * k
    th = tri * 0.87
    nx, ny = -sy, sx
    base0 = (ax + nx * tri / 2, ay + ny * tri / 2)
    base1 = (ax - nx * tri / 2, ay - ny * tri / 2)
    apex = (ax + sx * th, ay + sy * th)
    sk.poly([base0, base1, apex], fill=True)
    bx, by = box_centre
    sk.rect(bx - Hk / 2, by - Hk / 2, bx + Hk / 2, by + Hk / 2)
    sk.body = (bx - Hk / 2, by - Hk / 2, bx + Hk / 2, by + Hk / 2)
    sk.text(bx, by, letter, editable="datum")
    end = _box_edge_towards(bx, by, Hk, Hk, apex[0], apex[1])
    knee = (apex[0] + sx * 2.0 * k, apex[1] + sy * 2.0 * k)
    # straight out of the triangle first, then to the box
    if math.hypot(end[0] - knee[0], end[1] - knee[1]) > 0.5:
        sk.line(apex, knee)
        sk.line(knee, end)
    else:
        sk.line(apex, end)


def draw_arrow(sk, tip, frm):
    dx, dy = tip[0] - frm[0], tip[1] - frm[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px, py = -uy, ux
    a = ARROW * sk.k
    back = (tip[0] - ux * a, tip[1] - uy * a)
    sk.poly([tip, (back[0] + px * a * 0.3, back[1] + py * a * 0.3),
             (back[0] - px * a * 0.3, back[1] - py * a * 0.3)], fill=True)


def frame_segments(p):
    """The frame's segments (rows): list of dicts with char, tol, dia, mod,
    datums, datum_mods. A single-row frame keeps its settings at the top
    level of the params."""
    rows = p.get("rows")
    if rows:
        return rows
    return [{k: p.get(k) for k in ("char", "tol", "dia", "mod", "datums", "datum_mods",
                                   "zones", "uval", "proj", "std")}]


def _field(name, row):
    return name if row == 0 else "%s_%d" % (name, row + 1)


# Zone modifiers that follow the tolerance value, in drawing order
# (ISO 1101 / ASME Y14.5): unequal profile Ⓤ + value, common zone CZ,
# material condition Ⓜ/Ⓛ, projected Ⓟ + height, free state Ⓕ,
# tangent plane Ⓣ, envelope Ⓔ.
ZONES = [("U", "Ⓤ Unequally disposed profile (with offset value)"),
         ("CZ", "CZ Common zone"),
         ("P", "Ⓟ Projected tolerance zone (with height)"),
         ("F", "Ⓕ Free state"),
         ("T", "Ⓣ Tangent plane")]
# (Ⓔ envelope belongs after a size dimension, not in a frame: Edit dimension)
_CHAR_W = 2.2       # width of one tolerance-text character at 3.5 mm


def tolerance_items(seg):
    """[(kind, text)] drawn after the tolerance value: kind 'circ' (circled
    letter) or 'text'."""
    zones = seg.get("zones") or []
    out = []
    if "DP" in zones:
        out.append(("tri", "DP"))      # ASME dynamic profile: open triangle after the value
    if "U" in zones:
        val = seg.get("uval") or "0.1"
        if seg.get("std") == "ISO":
            # ISO 1101: UZ with a signed offset, e.g. UZ+0.1
            out.append(("text", "UZ" + (val if val[:1] in "+-" else "+" + val)))
        else:
            out.append(("circ", "U"))
            out.append(("text", val))
    if "CZ" in zones:
        out.append(("text", "CZ"))
    if seg.get("mod"):
        out.append(("circ", gdt.MODIFIERS[seg["mod"]]))
    if "P" in zones:
        out.append(("circ", "P"))
        if seg.get("proj"):
            out.append(("text", seg["proj"]))
    for z in ("F", "T"):
        if z in zones:
            out.append(("circ", z))
    if "ST" in zones:
        out.append(("hex", "ST"))      # ASME statistical tolerance
    return out


def _item_w(kind, text):
    return {"circ": 4.8, "hex": 6.6, "tri": 4.0}.get(kind, len(text) * _CHAR_W + 1.0)


def hex_box(sk, c, text, w=5.8, h=3.2):
    """Elongated hexagon round a short text: ASME statistical tolerance ⟨ST⟩
    and continuous feature ⟨CF⟩ symbols."""
    k = sk.k
    w, h = w * k, h * k
    x, y = c
    e = h / 2.0
    sk.poly([(x - w / 2, y), (x - w / 2 + e, y + h / 2), (x + w / 2 - e, y + h / 2),
             (x + w / 2, y), (x + w / 2 - e, y - h / 2), (x - w / 2 + e, y - h / 2)])
    sk.text(x, y, text, size=2.4)


def open_triangle(sk, c, size, direction="up"):
    """△ (dynamic profile) or ▷ (datum translation), outline only."""
    x, y = c
    s = size
    if direction == "up":
        pts = [(x - s / 2, y - s * 0.43), (x + s / 2, y - s * 0.43), (x, y + s * 0.43)]
    else:
        pts = [(x - s * 0.43, y - s / 2), (x - s * 0.43, y + s / 2), (x + s * 0.43, y)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        sk.line(a, b)


def segment_cells(seg, k=1.0):
    """(tolerance cell width, [datum cell widths]) of one segment."""
    tol = seg.get("tol") or "0.05"
    tol_w = (len(tol) * _CHAR_W + 3.0 + (3.4 if seg.get("dia") else 0.0)
             + sum(_item_w(*it) for it in tolerance_items(seg))) * k
    datum_ws = []
    for d, m in zip(seg.get("datums") or [], seg.get("datum_mods") or ["", "", ""]):
        if d:
            datum_ws.append((H + (3.6 if m else 0.0) + max(0, len(d) - 1) * 2.2) * k)
    return tol_w, datum_ws


def frame_cells(p):
    """Widths of the first segment's cells (kept for callers/tests)."""
    return segment_cells(frame_segments(p)[0])


def dim_text_side(dim, label_x):
    """For a frame under a Ø/R dimension: ('left'|'right', half text width).
    The leader meets the shoulder at the end nearer the feature; the frame
    lines up with that end and runs away from the leader."""
    if dim.Type not in ("Diameter", "Radius"):
        return None
    try:
        _, p1, _ = dimension_ends(dim)
    except Exception:
        return None
    fs = 3.5
    vo = getattr(dim, "ViewObject", None)
    if vo is not None and hasattr(vo, "Fontsize"):
        fs = float(getattr(vo.Fontsize, "Value", vo.Fontsize))
    from . import textlayer
    try:
        txt = textlayer.printed_text(dim) or "⌀10"   # e.g. '⌀10'
    except Exception:
        txt = "⌀10"
    from . import fonts
    w = fonts.dimension_text_width(txt, fs, fonts.dimension_font(dim))
    # the frame's edge just past the shoulder end where the leader leaves,
    # so the leader runs clear of it
    return ("left" if p1[0] < label_x else "right"), w / 2.0 - 1.5


def draw_frame(sk, anchor, box_centre, p, values, align=None):
    """Feature control frame centred at box_centre; leader+arrow to anchor.

    Several segments stack downwards. Composite: one characteristic box
    spans all rows (ASME Y14.5 composite tolerancing). Stacked: each row
    has its own characteristic."""
    k = sk.k
    H = gdt.H * k
    segs = frame_segments(p)
    composite = bool(p.get("composite")) and len(segs) > 1
    widths = []
    for seg in segs:
        tol_w, datum_ws = segment_cells(seg, k)
        widths.append(H + tol_w + sum(datum_ws))
    W = max(widths)
    n = len(segs)
    bx, by = box_centre
    x0 = bx - W / 2.0
    if align is not None:
        side, half = align
        x0 = bx - half if side == "left" else bx + half - W
        bx = x0 + W / 2.0
    ytop = by + n * H / 2.0
    if composite:
        sk.rect(x0, ytop - n * H, x0 + H, ytop)
        _symbol(sk, segs[0].get("char", "position"), x0, ytop - (n - 1) * H / 2.0)
    for r, seg in enumerate(segs):
        y1 = ytop - r * H
        y0 = y1 - H
        cy = (y0 + y1) / 2.0
        tol_w, datum_ws = segment_cells(seg, k)
        row_w = H + tol_w + sum(datum_ws)
        sk.rect(x0 + (H if composite else 0.0), y0, x0 + row_w, y1)
        x = x0 + H
        if not composite:
            sk.line((x, y0), (x, y1))
            _symbol(sk, seg.get("char", "position"), x0, y1)
        tx = x + 1.5 * k
        if seg.get("dia"):
            _dia(sk, tx, cy, 2.6 * k)
            tx += 3.4 * k
        name = _field("tolerance", r)
        tol = values.get(name, seg.get("tol", ""))
        sk.text(tx, cy, tol, anchor="start", editable=name)
        mx = tx + (len(seg.get("tol") or "0.05") * _CHAR_W + 0.5) * k
        for kind_, txt in tolerance_items(seg):
            w = _item_w(kind_, txt) * k
            if kind_ == "circ":
                circled(sk, (mx + w / 2.0, cy), 1.8 * k, txt)
            elif kind_ == "hex":
                hex_box(sk, (mx + w / 2.0, cy), txt)
            elif kind_ == "tri":
                open_triangle(sk, (mx + w / 2.0, cy), 3.0 * k, "up")
            else:
                sk.text(mx + w / 2.0, cy, txt)
            mx += w
        x += tol_w
        pairs = [(d, m) for d, m in zip(seg.get("datums") or [], seg.get("datum_mods") or ["", "", ""]) if d]
        for i, ((d, m), w) in enumerate(zip(pairs, datum_ws)):
            sk.line((x, y0), (x, y1))
            name = _field("datum%d" % (i + 1), r)
            sk.text(x + (w - (3.6 * k if m else 0.0)) / 2.0, cy, values.get(name, d), editable=name)
            if m == "TR":
                # ASME translation modifier: the datum simulator may move
                open_triangle(sk, (x + w - 2.2 * k, cy), 2.6 * k, "right")
            elif m:
                mx = x + w - 2.2 * k
                circled(sk, (mx, cy), 1.5 * k, gdt.MODIFIERS[m])
            x += w
    count = values.get("count", p.get("count", ""))
    sk.body = (x0, ytop - n * H, x0 + W, ytop + (5.0 * k if (count or p.get("cf")) else 0.0))
    if count:
        sk.text(x0, ytop + 2.5 * k, count, anchor="start", editable="count")
    if p.get("cf"):
        # ASME continuous feature: ⟨CF⟩ next to the frame, above its right end
        hex_box(sk, (x0 + W - 3.2 * k, ytop + 2.8 * k), "CF")
    between = p.get("between") or []
    if len(between) == 2 and all(between):
        draw_between(sk, bx, ytop - n * H - 3.0 * k, between[0], between[1])
    if anchor is not None:
        # leave from the left or right end of the first row, as usual
        first_cy = ytop - H / 2.0
        left = anchor[0] < bx
        end = (x0 if left else x0 + widths[0], first_cy)
        if p.get("all_around"):
            # ISO 1101 / ASME: small circle at the leader's bend
            knee = (end[0] + (-4.0 if left else 4.0) * k, first_cy)
            sk.line(end, knee)
            sk.circle(knee, 1.0 * k)
            sk.line(knee, anchor)
            draw_arrow(sk, anchor, knee)
        else:
            sk.line(end, anchor)
            draw_arrow(sk, anchor, end)
    return W


def _arc(cx, cy, rx, ry, a0, a1, n=8):
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


# Modifier letters as strokes in a unit box (x, y in -0.5..0.5, y up), so
# they sit exactly in the middle of their circle whatever the font.
GLYPHS = {
    "M": [[(-0.5, -0.5), (-0.5, 0.5), (0, -0.1), (0.5, 0.5), (0.5, -0.5)]],
    "L": [[(-0.4, 0.5), (-0.4, -0.5), (0.45, -0.5)]],
    "F": [[(0.45, 0.5), (-0.4, 0.5), (-0.4, -0.5)], [(-0.4, 0.02), (0.3, 0.02)]],
    "T": [[(-0.5, 0.5), (0.5, 0.5)], [(0, 0.5), (0, -0.5)]],
    "E": [[(0.45, 0.5), (-0.4, 0.5), (-0.4, -0.5), (0.45, -0.5)], [(-0.4, 0.0), (0.3, 0.0)]],
    "U": [[(-0.45, 0.5), (-0.45, -0.05)] + _arc(0, -0.05, 0.45, 0.45, 180, 360)
          + [(0.45, 0.5)]],
    "P": [[(-0.4, -0.5), (-0.4, 0.5), (0.1, 0.5)] + _arc(0.1, 0.25, 0.3, 0.25, 90, -90)
          + [(-0.4, 0.0)]],
    "S": [_arc(0, 0.25, 0.4, 0.25, 20, 270) + _arc(0, -0.25, 0.4, 0.25, 90, -200)],
}


def circled(sk, c, r, letter):
    """Circle with a modifier letter (Ⓜ Ⓛ Ⓟ Ⓕ Ⓣ Ⓤ Ⓔ Ⓢ) drawn centred."""
    sk.circle(c, r)
    strokes = GLYPHS.get(letter)
    if strokes is None:
        sk.text(c[0], c[1], letter, size=2.6 * r / (1.8 * sk.k))
        return
    sk.glyph(gdt.CIRCLED.get(letter, letter), c[0], c[1], r * 2.0)
    gw, gh = r * 0.8, r * 1.05
    for s in strokes:
        pts = [(c[0] + x * gw, c[1] + y * gh) for x, y in s]
        for a, b in zip(pts, pts[1:]):
            sk.line(a, b, 0.25)


def draw_between(sk, cx, cy, a, b):
    """'A ↔ B' (between symbol): the tolerance applies from point A to B."""
    k = sk.k
    half = 4.5 * k
    sk.text(cx - half - 2.2 * k, cy, a, editable="between1")
    sk.text(cx + half + 2.2 * k, cy, b, editable="between2")
    l, r = (cx - half, cy), (cx + half, cy)
    sk.line(l, r)
    draw_arrow(sk, l, r)
    draw_arrow(sk, r, l)


def _symbol(sk, name, x0, ytop):
    """Characteristic symbol in the frame's first H x H cell."""
    h = H * sk.k
    sk.raw(x0, ytop, h, h, gdt.symbol_paths(name, 0, 0, h))
    sk.glyph(gdt.UNICODE.get(name, ""), x0 + h / 2.0, ytop - h / 2.0, h * 0.6)


def _dia(sk, x, cy, d=2.6):
    r = d / 2.0
    from . import standards
    sk.glyph(standards.diameter_sign(), x + r, cy, d * 1.2)
    sk.circle((x + r, cy), r)
    sk.line((x - 0.3, cy - r - 0.4), (x + d + 0.3, cy + r + 0.4))


def draw_target(sk, anchor, box_centre, label, size_text):
    """Datum target: X at the point, leader to a divided circle."""
    ax, ay = anchor
    s = sk.k
    c = 1.4 * s
    sk.line((ax - c, ay - c), (ax + c, ay + c), 0.5)
    sk.line((ax - c, ay + c), (ax + c, ay - c), 0.5)
    r = 5.0 * s
    bx, by = box_centre
    sk.body = (bx - r, by - r, bx + r, by + r)
    sk.circle((bx, by), r)
    sk.line((bx - r, by), (bx + r, by))
    if size_text:
        sk.text(bx, by + 2.2 * s, size_text, size=2.5, editable="size")
    sk.text(bx, by - 2.4 * s, label, size=3.0, editable="target")
    d = math.hypot(ax - bx, ay - by) or 1.0
    start = (bx + (ax - bx) / d * r, by + (ay - by) / d * r)
    sk.line(start, anchor)


def draw_flag(sk, box_centre, text):
    """Special characteristic flag: the text in an elongated hexagon
    (pointed ends), as used for SC / CC / KC marks next to a dimension."""
    size = gdt.FONT * sk.k
    h = size * 1.9
    w = max(len(text), 1) * size * 0.62 + h          # room for the pointed ends
    bx, by = box_centre
    x0, x1 = bx - w / 2.0, bx + w / 2.0
    pts = [(x0, by), (x0 + h / 2.0, by + h / 2.0), (x1 - h / 2.0, by + h / 2.0),
           (x1, by), (x1 - h / 2.0, by - h / 2.0), (x0 + h / 2.0, by - h / 2.0)]
    sk.poly(pts)
    sk.text(bx, by, text, editable="flag")
    sk.body = (x0, by - h / 2.0, x1, by + h / 2.0)
    return w, h


def flag_size(text, k=1.0):
    size = gdt.FONT * k
    h = size * 1.9
    return max(len(text), 1) * size * 0.62 + h, h


def _text_upright(dim):
    """True if the dimension's text reads upwards (vertical ISO dimension)."""
    if dim.Type != "DistanceY":
        return False
    vo = getattr(dim, "ViewObject", None)
    return not (vo is not None and "Referencing" in str(getattr(vo, "StandardAndStyle", "")))


def dim_text_box(dim):
    """Page box of a dimension's whole text: value plus any tolerance or
    stacked limits written after it."""
    from . import clearance
    b = clearance.dimension_label_box(dim)
    if b is None:
        return None
    x0, y0, x1, y1 = b
    try:
        over, under = dim.FormatSpecOverTolerance or "", dim.FormatSpecUnderTolerance or ""
        has_tol = abs(float(getattr(dim.OverTolerance, "Value", dim.OverTolerance))) > 1e-12 or \
            abs(float(getattr(dim.UnderTolerance, "Value", dim.UnderTolerance))) > 1e-12
    except Exception:
        has_tol, over, under = False, "", ""
    if has_tol and not dim.TheoreticalExact:
        fs = clearance._dim_font(dim)
        from . import fonts
        tw = max(len(over), len(under), 4) * fs * (fonts.width_factor(fonts.dimension_font(dim))
                                                   + 0.06) * 0.75 + 1.0
        if _text_upright(dim):               # value reads upwards: tolerance above it
            y1 += tw
        else:
            x1 += tw
    return x0, y0, x1, y1


def flag_beside(dim, text, k=1.0, gap=1.5):
    """Centre for a flag just after a dimension's text (above it when the
    text reads upwards)."""
    b = dim_text_box(dim)
    if b is None:
        return None
    w, h = flag_size(text, k)
    x0, y0, x1, y1 = b
    if _text_upright(dim):
        # above the value, kept on the value's side of the dimension line
        try:
            p0, _p1, _u = dimension_ends(dim)
            line_x = p0[0]
        except Exception:
            line_x = (x0 + x1) / 2.0
        cx = (x0 + x1) / 2.0
        if line_x >= cx:
            cx = min(cx, line_x - gap - w / 2.0)
        else:
            cx = max(cx, line_x + gap + w / 2.0)
        return (cx, y1 + gap + h / 2.0)
    return (x1 + gap + w / 2.0, (y0 + y1) / 2.0)


def note_lines(p, values):
    lines = list(p.get("lines") or [""])
    return [values.get("line%d" % (i + 1), ln) for i, ln in enumerate(lines)]


def draw_note(sk, anchor, box_centre, lines, align="left"):
    """Multi-line note (left-aligned, or ``align`` 'centre') centred on
    box_centre; with a leader (horizontal shoulder, then to the arrow tip)
    when anchor is given."""
    size = gdt.FONT * sk.k
    pitch = size * 1.6
    from . import fonts
    w = max(len(ln) for ln in lines) * size * fonts.width_factor(sk.font) if lines else size
    h = pitch * (len(lines) - 1)
    bx, by = box_centre
    x0 = bx - w / 2.0
    ytop = by + h / 2.0
    sk.body = (x0, ytop - h - size * 0.4, x0 + w, ytop + size * 0.8)
    for i, ln in enumerate(lines):
        if align == "centre":
            sk.text(bx, ytop - i * pitch, ln, anchor="middle", editable="line%d" % (i + 1))
        else:
            sk.text(x0, ytop - i * pitch, ln, anchor="start", editable="line%d" % (i + 1))
    if anchor is not None:
        left = anchor[0] < bx
        gap = size * 0.5
        end = (x0 - gap if left else x0 + w + gap, ytop)
        shoulder = (end[0] - size if left else end[0] + size, ytop)
        sk.line(end, shoulder)
        sk.line(shoulder, anchor)
        draw_arrow(sk, anchor, shoulder)


# ---------------------------------------------------------------------------
# Creating and refreshing
# ---------------------------------------------------------------------------
def _params(sym):
    try:
        return json.loads(getattr(sym, PARAMS, "") or "{}")
    except ValueError:
        return {}


def _values(sym):
    """Current editable texts by name (keeps the user's edits on redraw)."""
    p = _params(sym)
    names = p.get("fields", [])
    vals = list(getattr(sym, "EditableTexts", []) or [])
    return {n: v for n, v in zip(names, vals)}


def render(sym):
    """(Re)draw an annotation from its anchor, offset and params."""
    key = (sym.Document.Name, sym.Name)
    _busy.add(key)
    try:
        p = _params(sym)
        values = _values(sym)
        (ax, ay), sdir = anchor_page(sym)
        off = getattr(sym, OFFSET, App.Vector())
        box = (ax + off.x, ay + off.y)
        sk = Sketch(text_scale(p), p.get("font"))
        kind = getattr(sym, KIND)
        fields = []
        if kind == "datum":
            letter = values.get("datum", p.get("letter", "A"))
            draw_datum(sk, (ax, ay), sdir, box, letter)
            fields = ["datum"]
        elif kind == "frame":
            akind = getattr(sym, ANCHOR_KIND, "free")
            anchor = None if akind in ("free", "dimlabel") else (ax, ay)
            align = dim_text_side(getattr(sym, LINK), ax) if akind == "dimlabel" else None
            draw_frame(sk, anchor, box, p, values, align)
        elif kind == "flag":
            text = values.get("flag", p.get("text", "SC"))
            link = getattr(sym, LINK, None)
            if getattr(sym, ANCHOR_KIND, "free") == "dimlabel" and link is not None \
                    and p.get("beside", True):
                spot = flag_beside(link, text, sk.k)
                if spot is not None:
                    box = spot
            draw_flag(sk, box, text)
            fields = ["flag"]
        elif kind == "tag":
            sk.text(box[0], box[1], p.get("label", ""), size=2.5)
            if math.hypot(off.x, off.y) > 8.0 * sk.k:
                sk.line((box[0], box[1] - 1.5 * sk.k), (ax, ay), 0.25)
        elif kind == "note":
            anchor = None if getattr(sym, ANCHOR_KIND, "free") == "free" or p.get("noleader") \
                else (ax, ay)
            draw_note(sk, anchor, box, note_lines(p, values), p.get("align", "left"))
        elif kind == "hatch":
            from . import hatching
            hatching.draw(sk, getattr(sym, LINK), p)
        elif kind in ("ordinate", "surface", "weld"):
            from . import drafting
            fields = drafting.draw(kind, sk, sym, (ax, ay), sdir, box, p, values)
        elif kind in ("holetable", "revtable", "bom", "toltable", "table"):
            from . import tables
            tables.draw_table(sk, box, p, values)
        else:
            draw_target(sk, (ax, ay), box, values.get("target", p.get("label", "A1")),
                        values.get("size", p.get("size", "")))
            fields = ["target"] + (["size"] if values.get("size", p.get("size")) else [])
        lead = sk.split_leader()
        svg, centre = sk.svg()
        _bodies[key] = sk.body
        _set_leader(sym, lead)
        if kind == "hatch":
            under_everything(sym)       # Edit hatch picks by point; the view stays clickable
        # the SVG lists editable texts in drawing order; remember that order
        order = []
        import re
        for m in re.finditer(r'freecad:editable="([^"]+)"', svg):
            order.append(m.group(1))
        p["fields"] = order
        if sk.glyphs:
            p["glyphs"] = [[c, round(x, 3), round(y, 3), round(s, 3)] for c, x, y, s in sk.glyphs]
        else:
            p.pop("glyphs", None)
        setattr(sym, PARAMS, json.dumps(p))
        sym.Symbol = svg
        if values:
            texts = [values.get(n, "") for n in order]
            if any(texts):
                sym.EditableTexts = texts
        sym.X, sym.Y = centre
        setattr(sym, CENTRE, App.Vector(centre[0], centre[1], 0))
        if kind == "frame":
            # datums hanging from this frame follow it
            for a in list(sym.InList):
                if is_annotation(a) and getattr(a, ANCHOR_KIND, "") == "frame" \
                        and core.same(getattr(a, LINK, None), sym):
                    render(a)
    finally:
        _busy.discard(key)
    return sym


LEADER = "DrawingKitLeader"          # on a symbol: the name of its leader object
LEADER_OF = "DrawingKitLeaderOf"     # on a leader: the symbol it belongs to
UNDER = -1                           # stack order below views and dimensions (0 and up)


def under_everything(obj):
    """Put a drawing under the views and dimensions (still above the
    template), so it never catches a click meant for them."""
    vo = getattr(obj, "ViewObject", None)
    if vo is not None and hasattr(vo, "StackOrder"):
        try:
            if vo.StackOrder != UNDER:
                vo.StackOrder = UNDER
        except Exception:
            pass


def leader_of(sym):
    name = getattr(sym, LEADER, "") if LEADER in sym.PropertiesList else ""
    obj = sym.Document.getObject(name) if name else None
    if obj is not None and getattr(obj, LEADER_OF, "") == sym.Name:
        return obj
    return None


def _remove(obj):
    try:
        for page in [o for o in obj.InList if o.isDerivedFrom("TechDraw::DrawPage")]:
            page.removeView(obj)
    except Exception:
        pass
    try:
        obj.Document.removeObject(obj.Name)
    except Exception:
        pass


def _set_leader(sym, lead):
    """Draw (or remove) the symbol's leader as its own locked drawing under
    everything else."""
    obj = leader_of(sym)
    if LEADER not in sym.PropertiesList:
        sym.addProperty("App::PropertyString", LEADER, "DrawingKit",
                        "The drawing of this symbol's leader")
        sym.setEditorMode(LEADER, 2)
    if lead is None:
        if obj is not None:
            _remove(obj)
        if getattr(sym, LEADER, ""):
            setattr(sym, LEADER, "")
        return
    if obj is None:
        page = None
        try:
            page = sym.findParentPage()
        except Exception:
            pass
        if page is None:
            pages = [o for o in sym.InList if o.isDerivedFrom("TechDraw::DrawPage")]
            page = pages[0] if pages else None
        if page is None:
            return
        doc = sym.Document
        obj = doc.addObject("TechDraw::DrawViewSymbol", "Leader")
        page.addView(obj)
        obj.ScaleType = "Custom"
        obj.Scale = 1.0
        obj.addProperty("App::PropertyString", LEADER_OF, "DrawingKit", "Symbol this leader belongs to")
        obj.setEditorMode(LEADER_OF, 2)
        setattr(obj, LEADER_OF, sym.Name)
        if "LockPosition" in obj.PropertiesList:
            obj.LockPosition = True          # moved with its symbol, never on its own
        setattr(sym, LEADER, obj.Name)
    label = "%s (leader)" % sym.Label
    if obj.Label != label:
        obj.Label = label
    svg, centre = lead.svg()
    obj.Symbol = svg
    obj.X, obj.Y = centre
    under_everything(obj)


def tidy_leaders(doc):
    """Leaders whose symbol is gone are removed; symbols drawn before
    leaders were split off are drawn again (once); every leader and hatch
    goes under the views. For documents opened in the GUI."""
    for o in list(doc.Objects):
        try:
            if LEADER_OF in o.PropertiesList:
                owner = doc.getObject(getattr(o, LEADER_OF, ""))
                if owner is None or leader_of(owner) is not o:
                    _remove(o)
                else:
                    under_everything(o)
            elif is_annotation(o):
                if getattr(o, KIND, "") == "hatch":
                    under_everything(o)
                if LEADER not in o.PropertiesList or SVG_MARK not in (o.Symbol or ""):
                    render(o)              # drawn by an older DrawingKit
        except Exception as e:
            App.Console.PrintWarning("DrawingKit leaders: %s: %s\n" % (o.Name, e))
    adopt_datums(doc)


def _new(page, kind, label, params, anchor_kind, link, anchor, offset, avoid=None):
    doc = page.Document
    sym = doc.addObject("TechDraw::DrawViewSymbol", {
        "datum": "Datum", "frame": "ToleranceFrame", "target": "DatumTarget", "tag": "HoleTag",
        "holetable": "HoleTable", "revtable": "RevisionTable", "note": "Note",
        "ordinate": "Ordinate", "surface": "SurfaceFinish", "weld": "Weld",
        "bom": "PartsList", "hatch": "PartHatch", "toltable": "GeneralTolerances", "table": "Table",
        "flag": "SpecialCharacteristic"}[kind])
    page.addView(sym)
    sym.ScaleType = "Custom"
    sym.Scale = 1.0
    sym.Label = label
    sym.addProperty("App::PropertyString", KIND, "DrawingKit", "Kind of DrawingKit annotation")
    sym.addProperty("App::PropertyString", ANCHOR_KIND, "DrawingKit", "view, dim or free")
    sym.addProperty("App::PropertyLink", LINK, "DrawingKit", "View or dimension it is attached to")
    sym.addProperty("App::PropertyVector", ANCHOR, "DrawingKit", "Anchor point")
    sym.addProperty("App::PropertyVector", OFFSET, "DrawingKit", "Box position from the anchor (mm)")
    sym.addProperty("App::PropertyString", PARAMS, "DrawingKit", "Settings")
    sym.addProperty("App::PropertyVector", CENTRE, "DrawingKit", "Last placed centre")
    for prop in (ANCHOR_KIND, ANCHOR, CENTRE, PARAMS):
        sym.setEditorMode(prop, 2)   # hidden
    setattr(sym, KIND, kind)
    setattr(sym, ANCHOR_KIND, anchor_kind)
    if link is not None:
        setattr(sym, LINK, link)
    setattr(sym, ANCHOR, App.Vector(anchor[0], anchor[1], 0))
    setattr(sym, OFFSET, App.Vector(offset[0], offset[1], 0))
    params = dict(params)
    params.setdefault("tsize", default_size())
    params.setdefault("font", default_font())
    setattr(sym, PARAMS, json.dumps(params))
    render(sym)
    if avoid:
        keep_clear(sym, page, explicit=(avoid == "explicit"),
                   views=(avoid != "on-surface"))
    return sym


def make_room_under(sym, dim, page, step=3.0, reach=45.0):
    """A frame hangs under its dimension's text, so it can't move on its
    own: if it lands on a view or other text, move the dimension's text
    further out from its view (the frame follows) until both are clear."""
    from . import clearance
    box = body_box(sym)
    label = clearance.dimension_label_box(dim)
    if not box or not label:
        return None
    # the text and its frame move together: one box round both
    both = (min(box[0], label[0]), min(box[1], label[1]),
            max(box[2], label[2]), max(box[3], label[3]))
    obs = clearance.obstacles(page, exclude=[sym, dim], views=True)
    view = dim.References2D[0][0]
    shift = clearance.clear_spot(both, core.view_center(view), obs,
                                 clearance.drawing_area(page), reach=reach, step=step)
    if not shift or (abs(shift[0]) < 1e-9 and abs(shift[1]) < 1e-9):
        return None
    dim.X, dim.Y = float(dim.X) + shift[0], float(dim.Y) + shift[1]
    render(sym)
    return shift


def body_box(sym):
    """Page box of the symbol's main part (frame, letter box, text...)."""
    b = _bodies.get((sym.Document.Name, sym.Name))
    if b:
        return b
    from . import clearance
    return clearance.symbol_box(sym)


def keep_clear(sym, page, explicit=False, views=True):
    """Move a new symbol (its offset from the anchor) off dimension texts,
    captions, balloons, other symbols, tables and, unless ``views`` is
    False, the views themselves. A spot the user clicked only gets a small
    nudge off text. Returns the shift (dx, dy) or None if it stayed put."""
    from . import clearance
    box = body_box(sym)
    if not box:
        return None
    obs = clearance.obstacles(page, exclude=[sym], views=views and not explicit)
    if explicit and views:
        # a spot the user clicked may be inside a view's box (they meant it);
        # the nudge off text must not carry it into a view it was clear of
        for v in core.part_views(page):
            try:
                vb = core.view_box(v)
            except Exception:
                continue
            if not clearance._hits(box, [vb], gap=0.0):
                obs.append(vb)
    (ax, ay), _ = anchor_page(sym)
    area = clearance.drawing_area(page)
    shift = clearance.clear_spot(box, (ax, ay), obs, area, reach=12.0 if explicit else 40.0)
    if not shift or (abs(shift[0]) < 1e-9 and abs(shift[1]) < 1e-9):
        return None
    off = getattr(sym, OFFSET)
    setattr(sym, OFFSET, App.Vector(off.x + shift[0], off.y + shift[1], 0))
    render(sym)
    return shift


def _on_frame_edge(b, x, y, margin=1.0):
    """'bottom' or 'top' if page point (x, y) is on that edge of the frame
    box b (x0, y0, x1, y1), else None."""
    if not b or not (b[0] - margin <= x <= b[2] + margin):
        return None
    if abs(y - b[1]) <= margin:
        return "bottom"
    if abs(y - b[3]) <= margin:
        return "top"
    return None


def frame_at(page, x, y, margin=1.0):
    """The tolerance frame on the page whose top or bottom edge is at page
    point (x, y), or None."""
    for v in page.Views:
        try:
            if getattr(v, KIND, "") != "frame":
                continue
            if (v.Document.Name, v.Name) not in _bodies:
                render(v)                  # opened from a file: learn its real outline
            if _on_frame_edge(body_box(v), x, y, margin):
                return v
        except Exception:
            continue
    return None


def hang_from_frame(datum, frame):
    """Attach a datum feature symbol to a tolerance frame (ASME Y14.5): it
    keeps its place but from now on moves with the frame."""
    (ax, ay), _ = anchor_page(datum)
    off = getattr(datum, OFFSET, App.Vector())
    bx, by = ax + off.x, ay + off.y
    b = body_box(frame)
    below = by < (b[1] + b[3]) / 2.0
    mid = (b[0] + b[2]) / 2.0
    half = max(0.0, (b[2] - b[0]) / 2.0 - 1.5)
    dx = max(-half, min(half, (ax if _on_frame_edge(b, ax, ay) else bx) - mid))
    ny = b[1] if below else b[3]
    setattr(datum, ANCHOR_KIND, "frame")
    setattr(datum, LINK, frame)
    setattr(datum, ANCHOR, App.Vector(dx, 0, 0))
    oy = by - ny
    if below and oy > -1.0 or not below and oy < 1.0:   # the box must be past the edge
        k = float(_params(datum).get("tsize", default_size())) / 3.5
        reach = (gdt.DATUM_TRI * 0.87 + gdt.DATUM_STEM) * k + H * k / 2.0
        oy = -reach if below else reach
    setattr(datum, OFFSET, App.Vector(bx - (mid + dx), oy, 0))
    p = _params(datum)
    if p.pop("dir", None) is not None or p.pop("side", None) is not None:
        setattr(datum, PARAMS, json.dumps(p))
    render(datum)
    return datum


def adopt_datums(doc):
    """Datums placed loose on the page with their triangle on a frame's edge
    (made before datums could hang from frames) are attached to that frame.
    Returns the datums attached."""
    done = []
    for d in list(doc.Objects):
        try:
            if getattr(d, KIND, "") != "datum" or getattr(d, ANCHOR_KIND, "") != "free":
                continue
            page = d.findParentPage() if hasattr(d, "findParentPage") else None
            if page is None:
                continue
            (ax, ay), _ = anchor_page(d)
            fr = frame_at(page, ax, ay)
            if fr is not None:
                hang_from_frame(d, fr)
                done.append(d)
        except Exception as e:
            App.Console.PrintWarning("DrawingKit datum: %s: %s\n" % (d.Name, e))
    return done


def attach_point(page, x, y, margin=3.0):
    """('view', view, local) for the view under a page point, else free."""
    best = None
    for v in core.part_views(page):
        try:
            x0, y0, x1, y1 = core.view_box(v)
        except Exception:
            continue
        if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, v, (x0, y0, x1, y1))
    if best is None:
        return "free", None, (x, y), None
    return "view", best[1], page_to_local(best[1], x, y), best[2]


def nearest_edge(view, local, max_dist=None):
    """The view edge nearest a local point (unscaled, Y up): returns
    (point on the edge, unit normal) or None. Straight edges and circles."""
    best = None
    px, py = local
    i = 0
    while i < 5000:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            break
        i += 1
        c = getattr(e, "Curve", None)
        if c is None:
            continue
        if "Circle" in c.TypeId:
            cx, cy, r = c.Center.x, c.Center.y, c.Radius
            dx, dy = px - cx, py - cy
            d = math.hypot(dx, dy) or 1e-9
            dist = abs(d - r)
            foot = (cx + dx / d * r, cy + dy / d * r)
            normal = (dx / d, dy / d)
        elif len(e.Vertexes) >= 2:
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            ex, ey = b.x - a.x, b.y - a.y
            L2 = ex * ex + ey * ey
            if L2 < 1e-12:
                continue
            t = max(0.0, min(1.0, ((px - a.x) * ex + (py - a.y) * ey) / L2))
            foot = (a.x + ex * t, a.y + ey * t)
            dist = math.hypot(px - foot[0], py - foot[1])
            L = math.sqrt(L2)
            normal = (-ey / L, ex / L)
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, foot, normal)
    if best is None or (max_dist is not None and best[0] > max_dist):
        return None
    return best[1], best[2]


def add_datum(page, x, y, letter=None, box=None, dimension=None, dim_end=1, frame=None):
    """Datum flag on the feature at page (x, y), on a dimension's end, or
    (``frame``) hanging from a feature control frame (ASME Y14.5: the
    datum feature symbol attached to the frame of that feature).

    ``box``: page point for the letter box (default: straight out)."""
    letter = (letter or next_datum_letter(page.Document)).strip().upper()
    if frame is None and dimension is None:
        frame = frame_at(page, x, y)      # clicked on a frame's edge: hang from it
    if frame is not None:
        k = float(_params(frame).get("tsize", default_size())) / 3.5
        b = body_box(frame)
        on = _on_frame_edge(b, x, y)
        below = (box[1] < (b[1] + b[3]) / 2.0) if box is not None else (on != "top")
        dx = x - (b[0] + b[2]) / 2.0 if on else 0.0
        ax = (b[0] + b[2]) / 2.0 + dx
        ay = b[1] if below else b[3]
        reach = (gdt.DATUM_TRI * 0.87 + gdt.DATUM_STEM) * k + H * k / 2.0
        off = (0.0, -reach if below else reach) if box is None else (box[0] - ax, box[1] - ay)
        return _new(page, "datum", "Datum %s" % letter, {"letter": letter}, "frame", frame,
                    (dx, 0), off)
    if dimension is not None:
        sym_tmp = None
        p0, p1, (ux, uy) = dimension_ends(dimension)
        (ax, ay), sdir = (p1, (ux, uy)) if dim_end else (p0, (-ux, -uy))
        params = {"letter": letter}
        default = (sdir[0] * 12.0, sdir[1] * 12.0)
        off = default if box is None else (box[0] - ax, box[1] - ay)
        return _new(page, "datum", "Datum %s" % letter, params, "dim", dimension,
                    (dim_end, 0), off, avoid="default" if box is None else "explicit")
    kind, view, local, vbox = attach_point(page, x, y)
    params = {"letter": letter}
    sdir = None
    if kind == "view":
        # snap onto the edge clicked; the flag points away from it, towards
        # the box if one was placed (else away from the view's middle)
        s = core.view_scale(view)
        hit = nearest_edge(view, local, max_dist=4.0 / s)
        if hit is not None:
            local, (nx, ny) = hit
            x, y = view_point(view, local)
            cx, cy = core.view_center(view)
            toward = (box[0] - x, box[1] - y) if box is not None else (x - cx, y - cy)
            if nx * toward[0] + ny * toward[1] < 0:
                nx, ny = -nx, -ny
            sdir = (nx, ny)
    if sdir is None:
        side = gdt.datum_side(vbox, x, y) if vbox else "up"
        sdir = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}[side]
    params["dir"] = [sdir[0], sdir[1]]
    reach = gdt.DATUM_TRI + gdt.DATUM_STEM + H / 2
    off = (sdir[0] * reach, sdir[1] * reach) if box is None else (box[0] - x, box[1] - y)
    return _new(page, "datum", "Datum %s" % letter, params,
                kind, view, local if kind == "view" else (x, y), off,
                avoid="default" if box is None else "explicit")


def add_frame(page, box, params, anchor=None, dimension=None):
    """Tolerance frame at page point ``box``; leader to page point ``anchor``
    (attached to the view under it) if given. With ``dimension`` the frame
    sits under that dimension's value and moves with it (``box`` may be
    None for the usual place just below)."""
    p = dict(params)
    p.setdefault("datum_mods", ["", "", ""])
    label = "GD&T %s %s" % (p.get("char", ""), p.get("tol", ""))
    if dimension is not None:
        if dimension.Type in ("Diameter", "Radius"):
            # value on a horizontal shoulder, so the frame can sit under it
            # without the dimension line running through it
            from . import holes
            holes.set_horizontal_text(dimension)
        view = dimension.References2D[0][0]
        cx, cy = core.view_center(view)
        lx, ly = cx + float(dimension.X), cy + float(dimension.Y)
        k = float(p.get("tsize", default_size())) / 3.5
        rows = len(frame_segments(p))
        off = (0.0, -(4.0 + rows * H / 2.0) * k) if box is None else (box[0] - lx, box[1] - ly)
        sym = _new(page, "frame", label, p, "dimlabel", dimension, (0, 0), off)
        if box is None:
            make_room_under(sym, dimension, page)
        return sym
    if anchor is None:
        return _new(page, "frame", label, p, "free", None, box, (0, 0), avoid="explicit")
    avoid = "explicit"
    if box is None:
        box, avoid = (anchor[0] + 25.0, anchor[1] + 15.0), "default"
    kind, view, local, _ = attach_point(page, anchor[0], anchor[1])
    if kind == "view":
        hit = nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local = hit[0]   # arrow tip exactly on the edge
    return _new(page, "frame", label, p, kind, view,
                local if kind == "view" else anchor, (box[0] - anchor[0], box[1] - anchor[1]),
                avoid=avoid)


def add_flag(page, text="SC", dimension=None, at=None):
    """Special characteristic flag (text in a hexagon). With ``dimension``
    it sits just after that dimension's text and follows it; otherwise at
    page point ``at`` (e.g. in the notes)."""
    text = (text or "SC").strip()
    p = {"text": text}
    if dimension is not None:
        if dimension.Type in ("Diameter", "Radius"):
            # value on a level shoulder, so the flag lines up after it
            from . import holes
            holes.set_horizontal_text(dimension)
        return _new(page, "flag", "Flag %s on %s" % (text, dimension.Label), p,
                    "dimlabel", dimension, (0, 0), (0, 0))
    if at is None:
        raise ValueError("Give a dimension or a spot for the flag")
    return _new(page, "flag", "Flag %s" % text, p, "free", None, at, (0, 0), avoid="explicit")


def flags_on(dim):
    return [o for o in dim.InList if getattr(o, KIND, "") == "flag"
            and core.same(getattr(o, LINK, None), dim)]


def add_target(page, x, y, label, size="", box=None):
    kind, view, local, vbox = attach_point(page, x, y)
    side = gdt.datum_side(vbox, x, y) if vbox else "up"
    sdir = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}[side]
    off = (sdir[0] * 14 + 6, sdir[1] * 14 + 6) if box is None else (box[0] - x, box[1] - y)
    return _new(page, "target", "Datum target %s" % label, {"label": label, "size": size, "side": side},
                kind, view, local if kind == "view" else (x, y), off,
                avoid="default" if box is None else "explicit")


def is_annotation(obj):
    return KIND in getattr(obj, "PropertiesList", [])


def annotations_on(page):
    return [v for v in page.Views if is_annotation(v)]


def attached_to(obj):
    """Annotations attached to a view, a group's views, or a dimension."""
    targets = [obj]
    if obj.isDerivedFrom("TechDraw::DrawProjGroup"):
        targets = list(obj.Views)
    out = []
    for t in targets:
        for o in t.InList:
            if is_annotation(o) and core.same(getattr(o, LINK, None), t):
                out.append(o)
            # annotations on a dimension of this view
            if o.isDerivedFrom("TechDraw::DrawViewDimension"):
                for a in o.InList:
                    if is_annotation(a) and core.same(getattr(a, LINK, None), o):
                        out.append(a)
    return out


def dragged(sym):
    """The user moved the symbol: keep the anchor, move the box with it."""
    key = (sym.Document.Name, sym.Name)
    if key in _busy:
        return False
    c = getattr(sym, CENTRE, None)
    if c is None:
        return False
    dx, dy = float(sym.X) - c.x, float(sym.Y) - c.y
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return False
    p = _params(sym)
    if getattr(sym, KIND, "") == "flag" and p.get("beside", True) and \
            getattr(sym, ANCHOR_KIND, "free") == "dimlabel":
        # dragged off its place after the value: it stays where it was put
        (ax, ay), _ = anchor_page(sym)
        p["beside"] = False
        setattr(sym, PARAMS, json.dumps(p))
        setattr(sym, OFFSET, App.Vector(float(sym.X) - ax, float(sym.Y) - ay, 0))
        render(sym)
        return True
    off = getattr(sym, OFFSET)
    setattr(sym, OFFSET, App.Vector(off.x + dx, off.y + dy, 0))
    render(sym)
    return True


def refresh(objs):
    n = 0
    for a in objs:
        try:
            render(a)
            n += 1
        except Exception as e:
            App.Console.PrintWarning("DrawingKit annotation %s: %s\n" % (a.Label, e))
    return n


def refresh_page(page):
    try:
        from . import dims
        dims.refresh_limits(page)     # limit dimensions follow the model
    except Exception:
        pass
    try:
        from . import phantom
        phantom.refresh_page(page)    # alternate positions follow the model
    except Exception:
        pass
    return refresh(annotations_on(page))


def used_datum_letters(doc):
    out = []
    for o in doc.Objects:
        if getattr(o, KIND, "") == "datum":
            vals = _values(o)
            out.append(vals.get("datum") or _params(o).get("letter", ""))
        elif getattr(o, gdt.SYMBOL_KIND, "") == "datum":
            out.append(o.Label.replace("Datum", "").strip())
    return out


def next_datum_letter(doc):
    """Next datum letter, skipping those taken by datums and by section and
    detail views."""
    return core.next_free_letter(used_datum_letters(doc) + core.view_letters(doc))


def add_note(page, box, lines, anchor=None, size=None, font=None):
    """Note at page point ``box`` (its centre). With ``anchor`` a leader runs
    to that page point, attached to the view under it."""
    lines = [ln for ln in (lines if isinstance(lines, list) else str(lines).splitlines())] or [""]
    p = {"lines": lines}
    if size:
        p["tsize"] = float(size)
    if font:
        p["font"] = font
    label = "Note " + (lines[0][:20] if lines and lines[0] else "")
    if anchor is None:
        sym = _new(page, "note", label.strip(), p, "free", None, box, (0, 0), avoid="explicit")
        keep_in_frame(page, sym)
        return sym
    kind, view, local, _ = attach_point(page, anchor[0], anchor[1])
    if kind == "view":
        hit = nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local = hit[0]
    return _new(page, "note", label.strip(), p, kind, view,
                local if kind == "view" else anchor, (box[0] - anchor[0], box[1] - anchor[1]),
                avoid="explicit")


def keep_in_frame(page, sym, margin=3.0):
    """Slide a free-standing symbol (a note) so all of it is inside the
    drawing frame and off the title block. Returns True if it moved."""
    from . import clearance
    try:
        b = clearance.symbol_box(sym)
        frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
    except Exception:
        return False
    if b is None or getattr(sym, ANCHOR_KIND, "free") != "free":
        return False
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    fx0, fy0, fx1, fy1 = frame[0] + margin, frame[1] + margin, frame[2] - margin, frame[3] - margin
    dx = (fx0 - x0) if x0 < fx0 else ((fx1 - x1) if x1 > fx1 else 0.0)
    dy = (fy0 - y0) if y0 < fy0 else ((fy1 - y1) if y1 > fy1 else 0.0)
    nx0, ny0 = x0 + dx, y0 + dy
    if title and nx0 < title[2] and title[0] < nx0 + w and ny0 < title[3] + margin and title[1] < ny0 + h:
        # on the title block: beside it if there's room on the left, else above it
        if title[0] - margin - w >= fx0:
            dx += (title[0] - margin) - (nx0 + w)
        else:
            dy += (title[3] + margin) - ny0
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return False
    c = getattr(sym, ANCHOR)
    setattr(sym, ANCHOR, App.Vector(c.x + dx, c.y + dy, 0))
    sym.X, sym.Y = float(sym.X) + dx, float(sym.Y) + dy
    render(sym)
    return True


def is_note(obj):
    return getattr(obj, KIND, "") == "note"


def note_text(sym):
    return "\n".join(note_lines(_params(sym), _values(sym)))


def set_note_text(sym, text):
    lines = str(text).splitlines() or [""]
    p = _params(sym)
    p["lines"] = lines
    p["fields"] = []      # forget the old texts; render writes the new ones
    setattr(sym, PARAMS, json.dumps(p))
    sym.Label = ("Note " + lines[0][:20]).strip()
    return render(sym)


def text_style(obj):
    """(size mm, font) of a dimension, TechDraw annotation or DrawingKit symbol;
    None if the object has no text."""
    if is_annotation(obj):
        p = _params(obj)
        return float(p.get("tsize", 3.5)), p.get("font") or default_font()
    if obj.isDerivedFrom("TechDraw::DrawViewDimension") or obj.isDerivedFrom("TechDraw::DrawViewBalloon"):
        vo = getattr(obj, "ViewObject", None)
        if vo is not None and hasattr(vo, "Fontsize"):
            return float(getattr(vo.Fontsize, "Value", vo.Fontsize)), vo.Font
        return None
    if obj.isDerivedFrom("TechDraw::DrawViewAnnotation"):
        return float(obj.TextSize), obj.Font
    return None


def set_text_style(obj, size=None, font=None):
    """Change text height (mm) and/or font. Returns True if obj has text."""
    if is_annotation(obj):
        p = _params(obj)
        if size:
            p["tsize"] = float(size)
        if font:
            p["font"] = font
        setattr(obj, PARAMS, json.dumps(p))
        render(obj)
        if getattr(obj, KIND, "") in ("holetable", "revtable", "bom", "toltable", "table"):
            from . import tables
            tables.keep_in_frame(obj)          # grown with the text: still on the sheet
        return True
    if obj.isDerivedFrom("TechDraw::DrawViewDimension") or obj.isDerivedFrom("TechDraw::DrawViewBalloon"):
        vo = getattr(obj, "ViewObject", None)
        if vo is None or not hasattr(vo, "Fontsize"):
            return False
        if size:
            vo.Fontsize = float(size)
        if font:
            vo.Font = font
        obj.touch()
        return True
    if obj.isDerivedFrom("TechDraw::DrawViewAnnotation"):
        if size:
            obj.TextSize = float(size)
        if font:
            obj.Font = font
        return True
    return False
