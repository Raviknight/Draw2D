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


def moved_to(view, want):
    """The edge that is most likely want after its feature changed size: a
    straight edge parallel to it covering most of the same stretch (and
    covered by it), the nearest one; or a circle round the same centre, the
    nearest in size.  None if there is none."""
    if want[0] == "C":
        best = None
        for i, s in _all(view, "Edge"):
            try:
                got = describe(s)
            except Exception:
                continue
            if got[0] == "C" and _close(want[2], got[2]):
                d = abs(got[3] - want[3])
                if best is None or d < best[0]:
                    best = (d, i)
        return "Edge%d" % best[1] if best else None
    if want[0] != "L":
        return None
    (ax, ay), (bx, by) = want[1]
    L = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if L < TOL:
        return None
    ux, uy = (bx - ax) / L, (by - ay) / L
    best = None
    for i, s in _all(view, "Edge"):
        try:
            got = describe(s)
        except Exception:
            continue
        if got[0] != "L":
            continue
        (cx, cy), (dx, dy) = got[1]
        M = ((dx - cx) ** 2 + (dy - cy) ** 2) ** 0.5
        if M < TOL or abs(((dx - cx) * uy - (dy - cy) * ux) / M) > 1e-3:
            continue                                    # not parallel
        t0, t1 = sorted(((cx - ax) * ux + (cy - ay) * uy, (dx - ax) * ux + (dy - ay) * uy))
        run = min(t1, L) - max(t0, 0.0)
        if run < 0.8 * L or run < 0.8 * M:
            continue                                    # a different stretch
        off = abs((cx - ax) * uy - (cy - ay) * ux)
        if off > L:
            continue
        if best is None or off < best[0]:
            best = (off, i)
    return "Edge%d" % best[1] if best else None


def _shifted(want, dx, dy):
    if not dx and not dy:
        return want
    mv = lambda p: (p[0] + dx, p[1] + dy)              # noqa: E731
    if want[0] == "V":
        return ("V", mv(want[1]))
    if want[0] == "C":
        return ("C", [mv(p) for p in want[1]], mv(want[2]), want[3])
    return (want[0], [mv(p) for p in want[1]], want[2])


# What each dimension pointed at the last time it was known to be right, in
# this session: {(document, dimension): {"want": [description per reference],
# "anchor": {view: where the model origin was drawn}}}.  Without an entry
# TechDraw's own saved copy is used.
_KNOWN = {}
BUSY = [False]          # True while DrawingKit itself changes references


def anchor(view):
    """Where the model's origin lands in the view's own coordinates. TechDraw
    centres a view on the part, so when the part changes size everything in
    the view can move; this says by how much."""
    try:
        from . import core
        d, right, up = core.view_axes(view)
        v = App.Vector(0, 0, 0) - core.shape_centre(core.source_shape(view.Source), d)
        return v.dot(right), v.dot(up)
    except Exception:
        return None


def _key(dim):
    return (dim.Document.Name, dim.Name)


def forget(dim):
    """The user pointed dim somewhere else: its old edges no longer count."""
    try:
        _KNOWN.pop(_key(dim), None)
    except Exception:
        pass


def forget_document(name):
    """A document closed: a file opened again later starts afresh."""
    for k in [k for k in _KNOWN if k[0] == name]:
        _KNOWN.pop(k, None)
    for k in [k for k in _VIEWS if k[0] == name]:
        _VIEWS.pop(k, None)
    try:
        from . import cutfollow
        cutfollow.forget_document(name)
    except Exception:
        pass


def _refs(dim):
    try:
        if not dim.isDerivedFrom("TechDraw::DrawViewDimension"):
            return None
        if getattr(dim, "References3D", None):
            return None             # TechDraw keeps these from the model itself
        return [(v, list(subs) if isinstance(subs, (list, tuple)) else [subs])
                for v, subs in dim.References2D]
    except Exception:
        return None


def _wanted(dim, flat):
    """[(description, (dx, dy))] for each reference: what it should point at
    and how far the view has moved since that was known."""
    known = _KNOWN.get(_key(dim))
    if known and len(known["want"]) == len(flat):
        out = []
        for (v, _), w in zip(flat, known["want"]):
            was, now = known["anchor"].get(v.Name), anchor(v)
            shift = (now[0] - was[0], now[1] - was[1]) if was and now else (0.0, 0.0)
            out.append((w, shift))
        return out
    try:
        saved = list(dim.SavedGeometry)
        if len(saved) != len(flat):
            return None
        return [(describe(g, flip=True), (0.0, 0.0)) for g in saved]
    except Exception:
        return None


def check(dim):
    """New References2D for dim if some of its references no longer point at
    what they pointed at when last known right (TechDraw's saved copy, or
    what DrawingKit saw earlier in this session); else None."""
    refs = _refs(dim)
    if not refs:
        return None
    flat = [(v, s) for v, subs in refs for s in subs]
    wanted = _wanted(dim, flat)
    if wanted is None:
        return None
    moved, k = False, 0
    out = []
    for v, subs in refs:
        new = []
        for s in subs:
            want, (dx, dy) = wanted[k]
            k += 1
            cur = _shape(v, s)
            try:
                here = describe(cur) if cur is not None else None
            except Exception:
                here = None
            found = None
            for w in ((_shifted(want, dx, dy), want) if (dx or dy) else (want,)):
                if same(w, here):
                    found = s
                    break
                found = find(v, w)
                if found:
                    break
            if found is None:
                found = moved_to(v, _shifted(want, dx, dy))
            if found is None:
                found = s           # nothing like it: leave it where TechDraw put it
            moved = moved or found != s
            new.append(found)
        out.append((v, new))
    return out if moved else None


def remember(dim):
    """Note what dim points at now, as right."""
    refs = _refs(dim)
    if not refs:
        return
    want, anchors = [], {}
    for v, subs in refs:
        a = anchor(v)
        if a is not None:
            anchors[v.Name] = a
        for s in subs:
            cur = _shape(v, s)
            if cur is None:
                return              # points past the view's last edge: not right
            try:
                want.append(describe(cur))
            except Exception:
                return
    _KNOWN[_key(dim)] = {"want": want, "anchor": anchors}


def repair(doc):
    """Re-point the dimensions FreeCAD left on the wrong edges, then note
    every dimension as right.  Returns the labels of the dimensions changed."""
    fixed = []
    dims = [d for d in doc.Objects if d.isDerivedFrom("TechDraw::DrawViewDimension")]
    BUSY[0] = True
    try:
        for d in dims:
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
    finally:
        BUSY[0] = False
    for d in dims:
        remember(d)
    return fixed


# Per view, as last seen: {(document, view): (anchor, model signature)}
_VIEWS = {}


def _model_sig(view):
    try:
        from . import core
        s = core.source_shape(view.Source)
        b = s.BoundBox
        return (round(s.Volume, 6), round(b.XMin, 6), round(b.YMin, 6), round(b.ZMin, 6),
                round(b.XMax, 6), round(b.YMax, 6), round(b.ZMax, 6))
    except Exception:
        return None


def update_views(doc):
    """After the model changed: detail views keep looking at the same spot
    on the part (their anchor is held in the base view's coordinates, which
    move when TechDraw centres the changed part), section cuts stay on their
    feature (see cutfollow), and DrawingKit's centre marks are drawn again
    on the changed part.  Returns True if anything
    changed (the views then need drawing again before dimensions are
    checked)."""
    from . import core
    parts = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawViewPart")]
    seen = {}
    for v in parts:
        seen[v.Name] = (anchor(v), _model_sig(v))
    changed = False
    for v in parts:
        old = _VIEWS.get((doc.Name, v.Name))
        if old is None:
            continue
        (a0, s0), (a1, s1) = old, seen[v.Name]
        if v.isDerivedFrom("TechDraw::DrawViewDetail"):
            base = getattr(v, "BaseView", None)
            b_old = _VIEWS.get((doc.Name, base.Name)) if base is not None else None
            b_new = seen.get(base.Name) if base is not None else None
            if b_old and b_new and b_old[0] and b_new[0]:
                dx, dy = b_new[0][0] - b_old[0][0], b_new[0][1] - b_old[0][1]
                if abs(dx) > TOL or abs(dy) > TOL:
                    p = v.AnchorPoint
                    v.AnchorPoint = App.Vector(p.x + dx, p.y + dy, p.z)
                    changed = True
        if s0 != s1 and getattr(v, core.MARKS_PROP, None):
            try:
                core.add_center_marks(v)
                v.touch()           # else the old marks stay among its edges
                changed = True
            except Exception as e:
                App.Console.PrintWarning("DrawingKit centre marks: %s\n" % e)
    try:
        from . import cutfollow
        if cutfollow.update(doc):
            changed = True
    except Exception as e:
        App.Console.PrintWarning("DrawingKit sections: %s\n" % e)
    for k in [k for k in _VIEWS if k[0] == doc.Name]:
        _VIEWS.pop(k, None)
    for name, val in seen.items():
        _VIEWS[(doc.Name, name)] = val
    if changed:
        doc.recompute()
    return changed


def snapshot(doc):
    """Something that changes while TechDraw is still redrawing the views
    and their dimensions after an edit."""
    out = []
    for o in doc.Objects:
        try:
            if o.isDerivedFrom("TechDraw::DrawViewDimension"):
                out.append(round(o.getRawValue(), 6))
            elif o.isDerivedFrom("TechDraw::DrawViewPart"):
                e = o.getEdgeByIndex(0)
                out.append((round(e.Length, 6), round(e.Vertexes[0].Point.x, 6)))
        except Exception:
            out.append(None)
    return tuple(out)
