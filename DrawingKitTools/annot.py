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
    """Until one is set, TechDraw's label font."""
    try:
        f = App.ParamGet(PREFS).GetString("Font", "")
        if f:
            return f
        return App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Labels").GetString(
            "LabelFont", "osifont") or "osifont"
    except Exception:
        return "osifont"


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
    for n in names:
        p, r = _edge_points(view, n)
        pts.extend(p)
        radius = radius or r
    t = dim.Type
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
    u = b - a
    u.normalize()
    return (cx + a.x * s, cy + a.y * s), (cx + b.x * s, cy + b.y * s), (u.x, u.y)


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
    if kind == "dim" and link is not None:
        p0, p1, (ux, uy) = dimension_ends(link)
        if int(round(a.x)) == 0:
            return p0, (-ux, -uy)
        return p1, (ux, uy)
    return (a.x, a.y), sdir


# ---------------------------------------------------------------------------
# Drawing (page coordinates, Y up) -> SVG
# ---------------------------------------------------------------------------
class Sketch:
    def __init__(self, k=1.0, font=None):
        self.items = []
        self.xs, self.ys = [], []
        self.k = k                       # text size / 3.5 mm
        self.font = font or "osifont"

    def _p(self, x, y):
        self.xs.append(x)
        self.ys.append(y)

    def line(self, a, b, width=STROKE, dash=False):
        self._p(*a)
        self._p(*b)
        self.items.append(("line", a, b, width, dash))

    def poly(self, pts, fill=False):
        for p in pts:
            self._p(*p)
        self.items.append(("poly", pts, fill))

    def rect(self, x0, y0, x1, y1, width=STROKE):
        self._p(x0, y0)
        self._p(x1, y1)
        self.items.append(("rect", x0, y0, x1, y1, width))

    def circle(self, c, r, width=STROKE):
        self._p(c[0] - r, c[1] - r)
        self._p(c[0] + r, c[1] + r)
        self.items.append(("circle", c, r, width))

    def raw(self, x0, ytop, w, h, fragment):
        """SVG fragment drawn in its own y-down box w x h, top-left at
        page point (x0, ytop)."""
        self._p(x0, ytop)
        self._p(x0 + w, ytop - h)
        self.items.append(("raw", x0, ytop, fragment))

    def text(self, x, y, txt, size=gdt.FONT, anchor="middle", editable=None, rot=0):
        """``size`` is the size at 3.5 mm lettering; it is scaled by k.
        ``rot``: 90 reads upwards (for vertical ordinate values)."""
        size = size * self.k
        w = len(txt) * size * 0.6
        x0 = x if anchor == "start" else x - w
        x1 = x + w
        if rot:
            # rotated about (x, y): along the text is now up the page
            self._p(x - size * 0.7, y + (x0 - x))
            self._p(x + size * 0.4, y + (x1 - x))
        else:
            self._p(x0, y - size * 0.4)
            self._p(x1, y + size * 0.7)
        self.items.append(("text", x, y, txt, size, anchor, editable, rot))

    def svg(self):
        m = 1.0
        x0, x1 = min(self.xs) - m, max(self.xs) + m
        y0, y1 = min(self.ys) - m, max(self.ys) + m
        W, Hh = x1 - x0, y1 - y0
        X = lambda x: x - x0
        Y = lambda y: y1 - y
        out = ['<svg %s width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">' % (gdt.NS, W, Hh, W, Hh),
               '<g style="fill:none;stroke:#000000;stroke-linecap:round;stroke-linejoin:round">']
        texts = []
        for it in self.items:
            k = it[0]
            if k == "line":
                _, a, b, w, dash = it
                out.append('<path style="stroke-width:%g%s" d="M %.3f,%.3f L %.3f,%.3f"/>'
                           % (w, ";stroke-dasharray:3,1.5" if dash else "",
                              X(a[0]), Y(a[1]), X(b[0]), Y(b[1])))
            elif k == "poly":
                _, pts, fill = it
                d = " L ".join("%.3f,%.3f" % (X(p[0]), Y(p[1])) for p in pts)
                out.append('<path style="stroke-width:%g%s" d="M %s Z"/>'
                           % (STROKE, ";fill:#000000" if fill else "", d))
            elif k == "rect":
                _, a0, b0, a1, b1, w = it
                out.append('<rect style="stroke-width:%g" x="%.3f" y="%.3f" width="%.3f" height="%.3f"/>'
                           % (w, X(min(a0, a1)), Y(max(b0, b1)), abs(a1 - a0), abs(b1 - b0)))
            elif k == "raw":
                _, rx, rtop, frag = it
                out.append('<g style="stroke-width:%g" transform="translate(%.3f,%.3f)">%s</g>'
                           % (STROKE, X(rx), Y(rtop), frag))
            elif k == "circle":
                _, c, r, w = it
                out.append('<circle style="stroke-width:%g" cx="%.3f" cy="%.3f" r="%.3f"/>'
                           % (w, X(c[0]), Y(c[1]), r))
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
                                   "zones", "uval", "proj")}]


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
    if "U" in zones:
        out.append(("circ", "U"))
        out.append(("text", seg.get("uval") or "0.1"))
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
    return out


def _item_w(kind, text):
    return 4.8 if kind == "circ" else len(text) * _CHAR_W + 1.0


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
    try:
        txt = dim.getText()            # e.g. '⌀10.00 mm'
    except Exception:
        txt = "⌀10"
    txt = txt.replace(" mm", "").replace("mm", "")
    if "w" in (dim.FormatSpec or "") and "." in txt:
        txt = txt.rstrip("0").rstrip(".")   # %w drops trailing zeros
    w = len(txt) * fs * 0.55
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
            else:
                sk.text(mx + w / 2.0, cy, txt)
            mx += w
        x += tol_w
        pairs = [(d, m) for d, m in zip(seg.get("datums") or [], seg.get("datum_mods") or ["", "", ""]) if d]
        for i, ((d, m), w) in enumerate(zip(pairs, datum_ws)):
            sk.line((x, y0), (x, y1))
            name = _field("datum%d" % (i + 1), r)
            sk.text(x + (w - (3.6 * k if m else 0.0)) / 2.0, cy, values.get(name, d), editable=name)
            if m:
                mx = x + w - 2.2 * k
                circled(sk, (mx, cy), 1.5 * k, gdt.MODIFIERS[m])
            x += w
    count = values.get("count", p.get("count", ""))
    if count:
        sk.text(x0, ytop + 2.5 * k, count, anchor="start", editable="count")
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


def _dia(sk, x, cy, d=2.6):
    r = d / 2.0
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
    sk.circle((bx, by), r)
    sk.line((bx - r, by), (bx + r, by))
    if size_text:
        sk.text(bx, by + 2.2 * s, size_text, size=2.5, editable="size")
    sk.text(bx, by - 2.4 * s, label, size=3.0, editable="target")
    d = math.hypot(ax - bx, ay - by) or 1.0
    start = (bx + (ax - bx) / d * r, by + (ay - by) / d * r)
    sk.line(start, anchor)


def note_lines(p, values):
    lines = list(p.get("lines") or [""])
    return [values.get("line%d" % (i + 1), ln) for i, ln in enumerate(lines)]


def draw_note(sk, anchor, box_centre, lines):
    """Left-aligned multi-line note centred on box_centre; with a leader
    (horizontal shoulder, then to the arrow tip) when anchor is given."""
    size = gdt.FONT * sk.k
    pitch = size * 1.6
    w = max(len(ln) for ln in lines) * size * 0.6 if lines else size
    h = pitch * (len(lines) - 1)
    bx, by = box_centre
    x0 = bx - w / 2.0
    ytop = by + h / 2.0
    for i, ln in enumerate(lines):
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
        elif kind == "tag":
            sk.text(box[0], box[1], p.get("label", ""), size=2.5)
            if math.hypot(off.x, off.y) > 8.0 * sk.k:
                sk.line((box[0], box[1] - 1.5 * sk.k), (ax, ay), 0.25)
        elif kind == "note":
            anchor = None if getattr(sym, ANCHOR_KIND, "free") == "free" else (ax, ay)
            draw_note(sk, anchor, box, note_lines(p, values))
        elif kind in ("ordinate", "surface", "weld"):
            from . import drafting
            fields = drafting.draw(kind, sk, sym, (ax, ay), sdir, box, p, values)
        elif kind in ("holetable", "revtable", "bom"):
            from . import tables
            tables.draw_table(sk, box, p, values)
        else:
            draw_target(sk, (ax, ay), box, values.get("target", p.get("label", "A1")),
                        values.get("size", p.get("size", "")))
            fields = ["target"] + (["size"] if values.get("size", p.get("size")) else [])
        svg, centre = sk.svg()
        # the SVG lists editable texts in drawing order; remember that order
        order = []
        import re
        for m in re.finditer(r'freecad:editable="([^"]+)"', svg):
            order.append(m.group(1))
        p["fields"] = order
        setattr(sym, PARAMS, json.dumps(p))
        sym.Symbol = svg
        if values:
            texts = [values.get(n, "") for n in order]
            if any(texts):
                sym.EditableTexts = texts
        sym.X, sym.Y = centre
        setattr(sym, CENTRE, App.Vector(centre[0], centre[1], 0))
    finally:
        _busy.discard(key)
    return sym


def _new(page, kind, label, params, anchor_kind, link, anchor, offset):
    doc = page.Document
    sym = doc.addObject("TechDraw::DrawViewSymbol", {
        "datum": "Datum", "frame": "ToleranceFrame", "target": "DatumTarget", "tag": "HoleTag",
        "holetable": "HoleTable", "revtable": "RevisionTable", "note": "Note",
        "ordinate": "Ordinate", "surface": "SurfaceFinish", "weld": "Weld",
        "bom": "PartsList"}[kind])
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
    return render(sym)


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


def add_datum(page, x, y, letter=None, box=None, dimension=None, dim_end=1):
    """Datum flag on the feature at page (x, y), or on a dimension's end.

    ``box``: page point for the letter box (default: straight out)."""
    letter = (letter or next_datum_letter(page.Document)).strip().upper()
    if dimension is not None:
        sym_tmp = None
        p0, p1, (ux, uy) = dimension_ends(dimension)
        (ax, ay), sdir = (p1, (ux, uy)) if dim_end else (p0, (-ux, -uy))
        params = {"letter": letter}
        default = (sdir[0] * 12.0, sdir[1] * 12.0)
        off = default if box is None else (box[0] - ax, box[1] - ay)
        return _new(page, "datum", "Datum %s" % letter, params, "dim", dimension,
                    (dim_end, 0), off)
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
                kind, view, local if kind == "view" else (x, y), off)


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
        return _new(page, "frame", label, p, "dimlabel", dimension, (0, 0), off)
    if anchor is None:
        return _new(page, "frame", label, p, "free", None, box, (0, 0))
    kind, view, local, _ = attach_point(page, anchor[0], anchor[1])
    if kind == "view":
        hit = nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local = hit[0]   # arrow tip exactly on the edge
    return _new(page, "frame", label, p, kind, view,
                local if kind == "view" else anchor, (box[0] - anchor[0], box[1] - anchor[1]))


def add_target(page, x, y, label, size="", box=None):
    kind, view, local, vbox = attach_point(page, x, y)
    side = gdt.datum_side(vbox, x, y) if vbox else "up"
    sdir = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}[side]
    off = (sdir[0] * 14 + 6, sdir[1] * 14 + 6) if box is None else (box[0] - x, box[1] - y)
    return _new(page, "target", "Datum target %s" % label, {"label": label, "size": size, "side": side},
                kind, view, local if kind == "view" else (x, y), off)


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
    return core.next_free_letter(used_datum_letters(doc))


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
        return _new(page, "note", label.strip(), p, "free", None, box, (0, 0))
    kind, view, local, _ = attach_point(page, anchor[0], anchor[1])
    if kind == "view":
        hit = nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local = hit[0]
    return _new(page, "note", label.strip(), p, kind, view,
                local if kind == "view" else anchor, (box[0] - anchor[0], box[1] - anchor[1]))


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
        return float(p.get("tsize", 3.5)), p.get("font", "osifont")
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
