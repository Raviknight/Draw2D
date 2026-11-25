# SPDX-License-Identifier: LGPL-2.1-or-later
"""Look over a sheet for what a checker would send back: dimension texts
and annotation texts sitting on the part's lines, and tables that run off
the drawing frame. Export lists them in the report view; nothing is moved.
"""

import FreeCAD as App

from . import core

TABLES = ("holetable", "revtable", "bom", "toltable")


def _view_segments(view, n=24):
    """The view's drawn edges as page-space line segments (curves split)."""
    from . import annot
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    out = []
    i = 0
    while i < 20000:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            break
        i += 1
        try:
            if len(e.Vertexes) >= 2 and "Line" in e.Curve.TypeId:
                pts = [e.Vertexes[0].Point, e.Vertexes[-1].Point]
            else:
                pts = e.discretize(n)
        except Exception:
            continue
        pp = [(cx + p.x * s, cy + p.y * s) for p in pts]
        out += list(zip(pp, pp[1:]))
    return out


def _crosses(seg, box):
    """True if the segment passes through the box (Liang-Barsky clip)."""
    (x0, y0), (x1, y1) = seg
    bx0, by0, bx1, by1 = box
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - bx0), (dx, bx1 - x0), (-dy, y0 - by0), (dy, by1 - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def _shrink(box, d):
    return (box[0] + d, box[1] + d, box[2] - d, box[3] - d)


def _dim_box(dim):
    """Page box of a dimension's whole text (value and tolerance)."""
    from . import clearance, textlayer
    box = clearance.dimension_label_box(dim)
    if box is None:
        return None
    try:
        text = textlayer.dimension_text(dim)
    except Exception:
        return box
    short = clearance.dimension_text(dim)
    if len(text) > len(short):
        # the label box counts the value only: widen it for the tolerance
        fs = clearance._dim_font(dim)
        extra = (len(text) - len(short)) * fs * 0.58 / 2.0
        if box[2] - box[0] >= box[3] - box[1]:
            box = (box[0] - extra, box[1], box[2] + extra, box[3])
        else:
            box = (box[0], box[1] - extra, box[2], box[3] + extra)
    return box


def problems(page):
    """[(object, what is wrong)] for one sheet."""
    from . import annot, clearance, textlayer
    every = page.getAllViews() if hasattr(page, "getAllViews") else page.Views
    views = [v for v in every if v.isDerivedFrom("TechDraw::DrawViewPart")]   # groups' too
    segs = {}
    for v in views:
        try:
            segs[v.Name] = (v, _view_segments(v))
        except Exception:
            pass
    found = []

    def on_lines(obj, box, what):
        if box is None:
            return
        inner = _shrink(box, 0.3)
        if inner[0] >= inner[2] or inner[1] >= inner[3]:
            return
        for v, ss in segs.values():
            try:
                vb = core.view_box(v)
            except Exception:
                vb = None
            if vb and (vb[2] < box[0] or vb[0] > box[2] or vb[3] < box[1] or vb[1] > box[3]):
                continue
            if any(_crosses(s, inner) for s in ss):
                found.append((obj, "%s sits on the lines of %s" % (what, v.Label)))
                return

    for d in page.Views:
        if d.isDerivedFrom("TechDraw::DrawViewDimension") and d.References2D:
            try:
                text = textlayer.dimension_text(d)
            except Exception:
                text = d.Label
            on_lines(d, _dim_box(d), "dimension text '%s'" % text)
    try:
        frame = clearance.drawing_area(page)
    except Exception:
        frame = None
    for sym in page.Views:
        kind = getattr(sym, annot.KIND, "")
        if not kind:
            continue
        if kind in TABLES:
            try:                             # the table itself, not its SVG's margins
                from . import tables
                w, h = tables.table_size(annot._params(sym))
                a = getattr(sym, annot.ANCHOR)
                box = (a.x - w / 2, a.y - h / 2, a.x + w / 2, a.y + h / 2)
            except Exception:
                box = clearance.symbol_box(sym)
            if frame and box and (box[0] < frame[0] - 0.3 or box[1] < frame[1] - 0.3
                                  or box[2] > frame[2] + 0.3 or box[3] > frame[3] + 0.3):
                found.append((sym, "%s runs past the drawing frame" % sym.Label))
        elif kind in ("note", "frame", "datum", "flag", "surface"):
            body = annot._bodies.get((sym.Document.Name, sym.Name))
            if body:                         # the text part only, not its leader
                on_lines(sym, body, "%s text" % sym.Label)
    return found


def report(pages):
    """Warn in the report view about each problem; returns how many."""
    n = 0
    for page in pages:
        try:
            items = problems(page)
        except Exception as e:
            App.Console.PrintWarning("DrawingKit: couldn't check %s: %s\n" % (page.Label, e))
            continue
        for obj, what in items:
            App.Console.PrintWarning("DrawingKit check, %s: %s\n" % (page.Label, what))
            n += 1
    return n
