# SPDX-License-Identifier: LGPL-2.1-or-later
"""Section cuts that stay on their feature when the part changes.

TechDraw keeps a section's cut at a fixed point in space (SectionOrigin).
Lengthen the part and a cut through a socket near its end no longer meets
the socket.  DrawingKit notes what each cut goes through the first time it
sees the section, and after a model change puts the cut back on it:

- a hole or other round feature whose axis lies in the cut plane (a cut
  through a cross hole): the cut stays on that axis;
- two like round ends evenly either side (a cut through the middle of a
  slot or keyway): the cut stays midway between them;
- otherwise the nearest flat face square to the cut, and the distance from
  it (1.27 mm back from the tip face): the cut keeps that distance.

A section whose origin is driven by an expression is left to it.  Complex
(stepped or aligned) sections are left alone.
"""

import math

import FreeCAD as App

TOL = 1e-3

# {(document, section): what the cut goes through}, this session
_ANCHORS = {}


def forget_document(name):
    for k in [k for k in _ANCHORS if k[0] == name]:
        _ANCHORS.pop(k, None)


def _shape(sec):
    from . import core
    return core.source_shape(sec.Source)


def _normal(sec):
    n = App.Vector(sec.SectionNormal)
    if n.Length < 1e-12:
        return None
    n.normalize()
    return n


def _planes(shape, n):
    """Flat faces square to n, coplanar ones together, in order along n:
    [(position along n, area, centre off the n axis)]."""
    groups = []
    for f in shape.Faces:
        try:
            if "Plane" not in f.Surface.TypeId:
                continue
            fn = f.normalAt(0, 0)
        except Exception:
            continue
        if abs(abs(fn.dot(n)) - 1.0) > 1e-6:
            continue
        p = f.Vertexes[0].Point.dot(n) if f.Vertexes else f.CenterOfMass.dot(n)
        c = f.CenterOfMass
        for g in groups:
            if abs(g[0] - p) < 1e-6:
                a = g[1] + f.Area
                g[2] = (g[2] * g[1] + c * f.Area) / a if a > 0 else g[2]
                g[1] = a
                break
        else:
            groups.append([p, f.Area, App.Vector(c)])
    groups.sort(key=lambda g: g[0])
    return [(p, a, c - n * c.dot(n)) for p, a, c in groups]


def _axes(shape):
    """Round faces: [(point on the axis, axis direction, radius)]."""
    out = []
    for f in shape.Faces:
        s = f.Surface
        if "Cylinder" not in s.TypeId and "Cone" not in s.TypeId:
            continue
        try:
            d = App.Vector(s.Axis)
            d.normalize()
            out.append((App.Vector(s.Center), d, float(getattr(s, "Radius", 0.0))))
        except Exception:
            continue
    return out


def _off_axis(point, axis_pt, axis_dir):
    v = point - axis_pt
    return (v - axis_dir * v.dot(axis_dir)).Length


def anchor_of(sec):
    """What sec's cut goes through, or None."""
    try:
        shape, n, o = _shape(sec), _normal(sec), App.Vector(sec.SectionOrigin)
    except Exception:
        return None
    if n is None:
        return None
    # a hole across the cut, its axis in the cut plane: the smallest one
    best = None
    for pt, d, r in _axes(shape):
        if abs(d.dot(n)) > 1e-6:
            continue                        # axis not in a plane square to n
        if abs((pt - o).dot(n)) > TOL:
            continue                        # not in this cut plane
        if best is None or r < best[2]:
            best = (pt, d, r)
    if best is not None:
        pt, d, r = best
        foot = pt + d * (o - pt).dot(d)     # nearest axis point to the origin
        return {"kind": "axis", "pt": foot, "dir": d, "r": r}
    # midway between two like round ends (a cut through the middle of a
    # slot or keyway): the closest such pair
    across = [(pt, d, r) for pt, d, r in _axes(shape) if abs(d.dot(n)) <= 1e-6]
    pair = None
    for i in range(len(across)):
        for j in range(i + 1, len(across)):
            (p1, d1, r1), (p2, d2, r2) = across[i], across[j]
            if abs(r1 - r2) > TOL or abs(abs(d1.dot(d2)) - 1.0) > 1e-6:
                continue
            s1, s2 = (p1 - o).dot(n), (p2 - o).dot(n)
            if abs(s1 + s2) > TOL or abs(s1) < TOL:
                continue                    # not on either side, evenly
            if _off_axis(p1 + n * (-s1), p2 + n * (-s2), d1) > max(1.0, 2 * r1):
                continue                    # not facing each other
            if pair is None or abs(s1) < pair[0]:
                pair = (abs(s1), p1, p2, d1, r1)
    if pair is not None:
        _, p1, p2, d, r = pair
        return {"kind": "mid", "pts": (p1, p2), "dir": d, "r": r}
    planes = _planes(shape, n)
    if not planes:
        return None
    on = o.dot(n)
    i = min(range(len(planes)), key=lambda k: abs(planes[k][0] - on))
    p, a, c = planes[i]
    return {"kind": "face", "rank": i, "count": len(planes), "p": p, "area": a,
            "centre": c, "offset": on - p}


def _find_plane(planes, a):
    """The flat face that is most likely the one noted in a."""
    if not planes:
        return None

    def fits(g):
        p, area, c = g
        ratio = area / a["area"] if a["area"] > 1e-12 else 1.0
        return 0.25 < ratio < 4.0 and (c - a["centre"]).Length < max(1.0, math.sqrt(a["area"]))

    # counted from the end it is nearer, as the part's features usually keep
    # their order even when they change size
    i, n0 = a["rank"], a["count"]
    from_hi = i >= n0 / 2.0
    k = len(planes) - (n0 - i) if from_hi else i
    if 0 <= k < len(planes) and fits(planes[k]):
        return planes[k]
    ok = [g for g in planes if fits(g)]
    if not ok:
        return None
    return min(ok, key=lambda g: abs(g[0] - a["p"]))


def follow(sec, a):
    """Where sec's origin should be now, by anchor a; None if it can't be
    found again."""
    shape, n, o = _shape(sec), _normal(sec), App.Vector(sec.SectionOrigin)
    axes = _axes(shape)

    def nearest_axis(was, d0, r0, skip=None):
        best = None
        for pt, d, r in axes:
            if abs(abs(d.dot(d0)) - 1.0) > 1e-6:
                continue
            if skip is not None and _off_axis(skip, pt, d) < TOL:
                continue
            if r0 > TOL and not 0.5 <= r / r0 <= 2.0:
                continue                    # a different size of hole
            score = _off_axis(was, pt, d) + 10.0 * abs(r - r0)
            if best is None or score < best[0]:
                best = (score, pt)
        return best[1] if best else None

    if a["kind"] == "axis":
        pt = nearest_axis(a["pt"], a["dir"], a["r"])
        if pt is None:
            return None
        target = (pt - o).dot(n)
    elif a["kind"] == "mid":
        p1 = nearest_axis(a["pts"][0], a["dir"], a["r"])
        p2 = nearest_axis(a["pts"][1], a["dir"], a["r"], skip=p1)
        if p1 is None or p2 is None:
            return None
        target = ((p1 + p2) * 0.5 - o).dot(n)
    else:
        g = _find_plane(_planes(shape, n), a)
        if g is None:
            return None
        target = g[0] + a["offset"] - o.dot(n)
    return o + n * target


def _driven(sec):
    try:
        return any(path.startswith(".SectionOrigin") or path.startswith("SectionOrigin")
                   for path, _ in sec.ExpressionEngine)
    except Exception:
        return False


def sections(doc):
    return [o for o in doc.Objects
            if o.isDerivedFrom("TechDraw::DrawViewSection")
            and not o.isDerivedFrom("TechDraw::DrawComplexSection")]


def update(doc):
    """Put each section's cut back on its feature after a model change.
    Returns the sections moved."""
    moved = []
    for sec in sections(doc):
        key = (doc.Name, sec.Name)
        if _driven(sec):
            _ANCHORS.pop(key, None)
            continue
        a = _ANCHORS.get(key)
        if a is not None:
            try:
                new = follow(sec, a)
            except Exception:
                new = None
            if new is not None and (new - App.Vector(sec.SectionOrigin)).Length > TOL:
                sec.SectionOrigin = new
                moved.append(sec)
        try:
            fresh = anchor_of(sec)
        except Exception:
            fresh = None
        if fresh is not None:
            _ANCHORS[key] = fresh
    return moved
