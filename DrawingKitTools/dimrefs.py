# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dimensions kept on their edges when a drawing is opened again.

A view's edges and vertices are numbered afresh each time FreeCAD works the
view out, and a PartDesign body can come back with them numbered differently
after the file is reopened.  TechDraw keeps a copy of each referenced edge
(the dimension's SavedGeometry, with the page's y axis flipped) and re-points
most dimensions itself, but it misses some: an edge that now comes as a
B-spline, or is split in two, or an index past the end.  Those dimensions then
measure the wrong edges (often 0.000).

repair(doc) looks at every dimension whose saved copy no longer matches the
edge it points at and points it at the edge that does match.
"""

import FreeCAD as App

TOL = 1e-3          # mm, on the view's own (unscaled-page) coordinates


def _pt(p, flip):
    return (p.x, -p.y if flip else p.y)


def describe(shape, flip=False):
    """A comparable description of a vertex or edge.  flip: the shape is a
    saved copy, whose y runs the other way."""
    if shape.ShapeType == "Vertex":
        return ("V", _pt(shape.Point, flip))
    ends = sorted(_pt(v.Point, flip) for v in shape.Vertexes)
    curve = shape.Curve
    if "Circle" in curve.TypeId:
        return ("C", ends, _pt(curve.Center, flip), curve.Radius)
    if len(ends) == 2:
        (ax, ay), (bx, by) = ends
        if abs(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 - shape.Length) < TOL:
            return ("L", ends, shape.Length)        # straight, whatever the curve type
    return ("E", ends, shape.Length)


def _close(a, b):
    return abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL


def same(a, b):
    if a is None or b is None or a[0] != b[0]:
        return False
    if a[0] == "V":
        return _close(a[1], b[1])
    if len(a[1]) != len(b[1]) or not all(_close(p, q) for p, q in zip(a[1], b[1])):
        return False
    if a[0] == "C":
        return _close(a[2], b[2]) and abs(a[3] - b[3]) < TOL
    return abs(a[2] - b[2]) < TOL


def _shape(view, sub):
    try:
        if sub.startswith("Vertex"):
            return view.getVertexByIndex(int(sub[6:]))
        if sub.startswith("Edge"):
            return view.getEdgeByIndex(int(sub[4:]))
    except Exception:
        pass
    return None


def _all(view, kind):
    get = view.getVertexByIndex if kind == "Vertex" else view.getEdgeByIndex
    i = 0
    while i < 100000:
        try:
            s = get(i)
        except Exception:
            return
        yield i, s
        i += 1


def _overlap(want, got):
    """How far a straight edge runs along the wanted straight edge (0 if it
    is off its line)."""
    (ax, ay), (bx, by) = want[1]
    L = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if L < TOL:
        return 0.0
    ux, uy = (bx - ax) / L, (by - ay) / L
    ts = []
    for cx, cy in got[1]:
        if abs((cx - ax) * uy - (cy - ay) * ux) > TOL:
            return 0.0
        ts.append((cx - ax) * ux + (cy - ay) * uy)
    return max(0.0, min(max(ts), L) - max(min(ts), 0.0))


def find(view, want):
    """The sub-element of view matching description want: the same vertex or
    edge, else the same circle, else the straight edge lying longest along
    the wanted one.  None if nothing does."""
    if want[0] == "V":
        best = None
        for i, s in _all(view, "Vertex"):
            p = _pt(s.Point, False)
            d = ((p[0] - want[1][0]) ** 2 + (p[1] - want[1][1]) ** 2) ** 0.5
            if d < TOL and (best is None or d < best[0]):
                best = (d, i)
        return "Vertex%d" % best[1] if best else None
    near = []
    for i, s in _all(view, "Edge"):
        try:
            got = describe(s)
        except Exception:
            continue
        if same(want, got):
            return "Edge%d" % i
        if want[0] == "C" and got[0] == "C" and _close(want[2], got[2]) \
                and abs(want[3] - got[3]) < TOL:
            near.append((-1e9, i))
        elif want[0] == "L" and got[0] == "L":
            run = _overlap(want, got)
            if run > TOL:
                near.append((-run, i))
    return "Edge%d" % min(near)[1] if near else None


def check(dim):
    """New References2D for dim if some of its references no longer point at
    their saved edges and all of those can be found again; else None."""
    try:
        if not dim.isDerivedFrom("TechDraw::DrawViewDimension"):
            return None
        if getattr(dim, "References3D", None):
            return None             # TechDraw keeps these from the model itself
        refs = [(v, list(subs) if isinstance(subs, (list, tuple)) else [subs])
                for v, subs in dim.References2D]
        saved = list(dim.SavedGeometry)
    except Exception:
        return None
    flat = [(v, s) for v, subs in refs for s in subs]
    if not flat or len(saved) != len(flat):
        return None
    moved, k = False, 0
    out = []
    for v, subs in refs:
        new = []
        for s in subs:
            g = saved[k]
            k += 1
            try:
                want = describe(g, flip=True)
            except Exception:
                return None
            cur = _shape(v, s)
            try:
                if cur is not None and same(want, describe(cur)):
                    new.append(s)
                    continue
            except Exception:
                pass
            found = find(v, want)
            if found is None:
                return None         # leave it as it is rather than half fix it
            moved = moved or found != s
            new.append(found)
        out.append((v, new))
    return out if moved else None


def repair(doc):
    """Re-point the dimensions FreeCAD left on the wrong edges.  Returns the
    labels of the dimensions changed."""
    fixed = []
    for d in list(doc.Objects):
        new = check(d)
        if new is None:
            continue
        try:
            d.References2D = new
            fixed.append(d.Label)
        except Exception as e:
            App.Console.PrintWarning("DrawingKit: could not re-attach %s: %s\n" % (d.Label, e))
    if fixed:
        doc.recompute()
    return fixed
