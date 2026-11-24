# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketcher-style dimensioning on a drawing.

Click one or two things in a view (a line, a circle or arc, a point, or a
circle's centre), then move the mouse to choose the kind of dimension and
click to place it:

  circle            -> diameter          arc            -> radius
  line              -> length (horizontal / vertical / true by mouse)
  two points        -> distance (horizontal / vertical / true by mouse)
  two circles       -> centre-to-centre distance
  point/circle+line -> perpendicular distance
  two parallel lines-> distance between them
  two lines         -> angle

Circle centres are referenced through a point at the centre (TechDraw's own
centre vertex if it has one, otherwise a cosmetic vertex), which is why
TechDraw's tool cannot do circle-to-circle on its own.
"""

import math

import FreeCAD as App

from . import core

PICK_MM = 2.5   # pick distance on paper


class Ent:
    """A picked thing in a view, in unscaled view coordinates (Y up)."""

    def __init__(self, kind, index, pts, radius=0.0):
        self.kind = kind          # 'vertex', 'line', 'circle', 'arc', 'centre'
        self.index = index        # vertex/edge index in the view
        self.pts = pts            # vertex: [p]; line: [a, b]; circle/arc/centre: [centre]
        self.radius = radius

    @property
    def point(self):
        return self.pts[0]

    def is_point(self):
        return self.kind in ("vertex", "centre")

    def __repr__(self):
        return "Ent(%s, %s)" % (self.kind, self.index)


def _edges(view):
    out = []
    i = 0
    while i < 10000:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            break
        out.append((i, e))
        i += 1
    return out


def _vertices(view):
    out = []
    i = 0
    while i < 10000:
        try:
            v = view.getVertexByIndex(i)
        except Exception:
            break
        out.append((i, (v.Point.x, v.Point.y)))
        i += 1
    return out


def _is_straight(edge, tol=1e-6):
    """True if the edge runs straight from its first to its last vertex."""
    try:
        a, b = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
        chord = a.distanceToPoint(b)
        if chord < 1e-9:
            return False
        if abs(edge.Length - chord) > tol * max(1.0, chord):
            return False
        mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2.0)
        u = (b - a) * (1.0 / chord)
        d = mid - a
        return (d - u * d.dot(u)).Length < 1e-6 * max(1.0, chord)
    except Exception:
        return False


def entities(view):
    """Everything pickable in a view."""
    ents = []
    seams = []      # a full circle's start/end point is not a real corner
    for i, e in _edges(view):
        c = getattr(e, "Curve", None)
        if c is None:
            continue
        t = c.TypeId
        if "Circle" in t:
            full = len(e.Vertexes) < 2 or e.Vertexes[0].Point.distanceToPoint(e.Vertexes[-1].Point) < 1e-6
            kind = "circle" if full else "arc"
            ents.append(Ent(kind, i, [(c.Center.x, c.Center.y)], c.Radius))
            ents.append(Ent("centre", i, [(c.Center.x, c.Center.y)], c.Radius))
            if full:
                seams.extend((v.Point.x, v.Point.y) for v in e.Vertexes)
        elif len(e.Vertexes) >= 2 and (t in ("Part::GeomLine", "Part::GeomLineSegment")
                                       or _is_straight(e)):
            # TechDraw returns some straight edges as B-splines, e.g. a turned
            # part's end faces and shoulders (circles seen edge-on)
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            ents.append(Ent("line", i, [(a.x, a.y), (b.x, b.y)]))
    centres = [e.point for e in ents if e.kind == "centre"]
    for i, p in _vertices(view):
        if any(abs(p[0] - s[0]) < 1e-6 and abs(p[1] - s[1]) < 1e-6 for s in seams):
            continue
        if any(abs(p[0] - c[0]) < 1e-6 and abs(p[1] - c[1]) < 1e-6 for c in centres):
            continue   # the circle's 'centre' entity covers it
        ents.append(Ent("vertex", i, [p]))
    return ents


def _dist_to(ent, p):
    x, y = p
    if ent.kind in ("vertex", "centre"):
        return math.hypot(x - ent.point[0], y - ent.point[1])
    if ent.kind in ("circle", "arc"):
        return abs(math.hypot(x - ent.point[0], y - ent.point[1]) - ent.radius)
    (ax, ay), (bx, by) = ent.pts
    ex, ey = bx - ax, by - ay
    L2 = ex * ex + ey * ey
    t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / L2)) if L2 > 0 else 0.0
    return math.hypot(x - (ax + ex * t), y - (ay + ey * t))


def pick(view, local, ents=None, tol=None):
    """The thing under a local point: points beat edges (like the Sketcher)."""
    ents = ents if ents is not None else entities(view)
    tol = tol if tol is not None else PICK_MM / core.view_scale(view)
    best = None
    for e in ents:
        d = _dist_to(e, local)
        if d > tol:
            continue
        rank = 0 if e.is_point() else 1
        if best is None or (rank, d) < (best[0], best[1]):
            best = (rank, d, e)
    return best[2] if best else None


# ---------------------------------------------------------------------------
# What dimension two picks and a mouse position make
# ---------------------------------------------------------------------------
def _parallel(l1, l2, tol=1e-6):
    (a, b), (c, d) = l1.pts, l2.pts
    u = (b[0] - a[0], b[1] - a[1])
    v = (d[0] - c[0], d[1] - c[1])
    cross = u[0] * v[1] - u[1] * v[0]
    return abs(cross) <= tol * math.hypot(*u) * math.hypot(*v)


def _axis_choice(p, q, mouse):
    """Horizontal, vertical or true distance between p and q, chosen by
    where the mouse is (like the Sketcher's smart dimension)."""
    x0, x1 = sorted((p[0], q[0]))
    y0, y1 = sorted((p[1], q[1]))
    mx, my = mouse
    if abs(p[1] - q[1]) < 1e-9:
        return "DistanceX"
    if abs(p[0] - q[0]) < 1e-9:
        return "DistanceY"
    inside_x = x0 <= mx <= x1
    inside_y = y0 <= my <= y1
    if inside_x and not inside_y:
        return "DistanceX"     # mouse above/below: horizontal dimension
    if inside_y and not inside_x:
        return "DistanceY"     # mouse left/right: vertical dimension
    return "Distance"


def plan(picks, mouse):
    """(Type, [Ent...] to reference, label) for picks and a local mouse point,
    or (None, [], reason)."""
    if not picks:
        return None, [], "Click a line, circle or point"
    if len(picks) == 1:
        e = picks[0]
        if e.kind == "circle":
            return "Diameter", [e], "⌀ %.3g" % (2 * e.radius)
        if e.kind == "arc":
            return "Radius", [e], "R %.3g" % e.radius
        if e.kind == "line":
            t = _axis_choice(e.pts[0], e.pts[1], mouse)
            return t, [e], "length"
        return None, [], "Click a second point"
    a, b = picks[0], picks[1]
    pa = a.is_point() or a.kind in ("circle", "arc")
    pb = b.is_point() or b.kind in ("circle", "arc")
    if a.kind == "line" and b.kind == "line":
        if _parallel(a, b):
            return "Distance", [a, b], "distance between lines"
        return "Angle", [a, b], "angle"
    if pa and pb:
        ca = a if a.is_point() else Ent("centre", a.index, a.pts, a.radius)
        cb = b if b.is_point() else Ent("centre", b.index, b.pts, b.radius)
        return _axis_choice(ca.point, cb.point, mouse), [ca, cb], "distance"
    # point/circle + line: perpendicular distance from the point (centre)
    pt, ln = (a, b) if pa else (b, a)
    c = pt if pt.is_point() else Ent("centre", pt.index, pt.pts, pt.radius)
    return "Distance", [c, ln], "distance to line"


def _point_line(p, line):
    (ax, ay), (bx, by) = line.pts
    ex, ey = bx - ax, by - ay
    L = math.hypot(ex, ey) or 1.0
    return abs((p[0] - ax) * ey - (p[1] - ay) * ex) / L


def preview_value(picks, mouse):
    """Text of the dimension that would be placed now ('' if none)."""
    t, ents, _ = plan(picks, mouse)
    if t is None:
        return ""
    if t == "Diameter":
        return "⌀%.2f" % (2 * ents[0].radius)
    if t == "Radius":
        return "R%.2f" % ents[0].radius
    if t == "Angle":
        (a, b), (c, d) = ents[0].pts, ents[1].pts
        u = math.atan2(b[1] - a[1], b[0] - a[0])
        v = math.atan2(d[1] - c[1], d[0] - c[0])
        ang = abs(math.degrees(u - v)) % 180.0
        return "%.1f°" % min(ang, 180.0 - ang)
    if len(ents) == 1:                     # line length
        p, q = ents[0].pts
    elif ents[1].kind == "line" and ents[0].kind == "line":
        return "%.2f" % _point_line(ents[0].pts[0], ents[1])
    elif ents[1].kind == "line":
        return "%.2f" % _point_line(ents[0].point, ents[1])
    else:
        p, q = ents[0].point, ents[1].point
    if t == "DistanceX":
        return "%.2f" % abs(q[0] - p[0])
    if t == "DistanceY":
        return "%.2f" % abs(q[1] - p[1])
    return "%.2f" % math.hypot(q[0] - p[0], q[1] - p[1])


# ---------------------------------------------------------------------------
# Live preview: the dimension drawn as it would be placed
# ---------------------------------------------------------------------------
ARROW = 3.0        # arrowhead length on paper (mm)
EXT_GAP = 1.5      # gap between the feature and an extension line
EXT_OVER = 2.0     # extension line past the dimension line


def _unit(v):
    L = math.hypot(v[0], v[1])
    return (v[0] / L, v[1] / L) if L > 1e-12 else (1.0, 0.0)


def _add(p, v, k=1.0):
    return (p[0] + v[0] * k, p[1] + v[1] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _arrow(tip, toward, size):
    """Filled arrowhead with its tip at ``tip`` pointing along ``toward``."""
    u = _unit(toward)
    n = (-u[1], u[0])
    base = _add(tip, u, -size)
    return ("fill", [tip, _add(base, n, size * 0.18), _add(base, n, -size * 0.18), tip])


def _foot(p, line):
    (ax, ay), (bx, by) = line.pts
    u = _unit((bx - ax, by - ay))
    t = _dot(_sub(p, (ax, ay)), u)
    return (ax + u[0] * t, ay + u[1] * t)


def _linear(p, q, d, mouse, k):
    """Extension lines, dimension line (stretched to the mouse) and arrows
    for a distance from p to q measured along unit vector d."""
    n = (-d[1], d[0])
    a = _add(p, n, _dot(_sub(mouse, p), n))      # ends of the dimension line,
    b = _add(q, n, _dot(_sub(mouse, q), n))      # on the line through the mouse
    out = []
    for f, e in ((p, a), (q, b)):
        v = _sub(e, f)
        L = math.hypot(*v)
        if L > EXT_GAP * k:
            u = _unit(v)
            out.append(("line", [_add(f, u, EXT_GAP * k), _add(e, u, EXT_OVER * k)]))
    ta, tb = _dot(_sub(a, mouse), d), _dot(_sub(b, mouse), d)
    lo = min(0.0, ta, tb)
    hi = max(0.0, ta, tb)
    out.append(("line", [_add(mouse, d, lo), _add(mouse, d, hi)]))
    if math.hypot(*_sub(b, a)) > 1e-9:
        out.append(_arrow(a, _sub(a, b), ARROW * k))
        out.append(_arrow(b, _sub(b, a), ARROW * k))
    return out


def preview_shapes(picks, mouse, scale=1.0):
    """The dimension the picks and mouse describe, as ('line', [pts]) and
    ('fill', [pts]) in view-local coordinates (model mm), so it can be
    drawn under the mouse while placing. [] if there is nothing to show."""
    t, ents, _ = plan(picks, mouse)
    if t is None:
        return []
    k = 1.0 / (scale or 1.0)          # paper mm -> local units
    if t in ("Diameter", "Radius"):
        e = ents[0]
        c, r = e.point, e.radius
        u = _unit(_sub(mouse, c))
        far = _add(c, u, r)
        out = []
        outside = math.hypot(*_sub(mouse, c)) > r
        if t == "Diameter" and not outside:
            near = _add(c, u, -r)
            out.append(("line", [near, far]))
            out.append(_arrow(near, _sub(near, far), ARROW * k))
        elif t == "Radius":
            out.append(("line", [c, far]))
        # from inside the arrow points out at the circle; on a leader from
        # outside it points in
        out.append(_arrow(far, (-u[0], -u[1]) if outside else u, ARROW * k))
        if outside:
            out.append(("line", [far, mouse]))           # leader out to the text
            side = 1.0 if mouse[0] >= c[0] else -1.0     # then a horizontal shoulder
            out.append(("line", [mouse, (mouse[0] + side * 8.0 * k, mouse[1])]))
        return out
    if t == "Angle":
        a, b = ents
        (p1, p2), (p3, p4) = a.pts, b.pts
        d1, d2 = _sub(p2, p1), _sub(p4, p3)
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-12:
            return []
        s_ = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / den
        v = _add(p1, d1, s_)                             # where the lines meet
        R = max(math.hypot(*_sub(mouse, v)), 4.0 * k)
        # each line's direction away from the meeting point, on the mouse's side
        dirs = []
        for d in (d1, d2):
            u = _unit(d)
            dirs.append(u if _dot(_sub(mouse, v), u) >= 0 else (-u[0], -u[1]))
        a0 = math.atan2(dirs[0][1], dirs[0][0])
        a1 = math.atan2(dirs[1][1], dirs[1][0])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        pts = [_add(v, (math.cos(a0 + sweep * i / 24.0), math.sin(a0 + sweep * i / 24.0)), R)
               for i in range(25)]
        out = [("line", pts)]
        for end, nxt in ((pts[0], pts[1]), (pts[-1], pts[-2])):
            out.append(_arrow(end, _sub(end, nxt), ARROW * k))
        for d in dirs:                                   # extend the lines to the arc
            out.append(("line", [_add(v, d, R * 0.3), _add(v, d, R + EXT_OVER * k)]))
        return out
    # straight distances
    if len(ents) == 1:
        p, q = ents[0].pts
    elif ents[0].kind == "line" and ents[1].kind == "line":
        p = ents[0].pts[0]
        q = _foot(p, ents[1])
    elif ents[1].kind == "line":
        p = ents[0].point
        q = _foot(p, ents[1])
    else:
        p, q = ents[0].point, ents[1].point
    if t == "DistanceX":
        d = (1.0, 0.0)
    elif t == "DistanceY":
        d = (0.0, 1.0)
    else:
        d = _unit(_sub(q, p))
    return _linear(p, q, d, mouse, k)


# ---------------------------------------------------------------------------
# Making the TechDraw dimension
# ---------------------------------------------------------------------------
def vertex_at(view, point, tol=1e-4):
    """Name of a vertex at a local point, making a cosmetic one if needed."""
    for i, p in _vertices(view):
        if abs(p[0] - point[0]) < tol and abs(p[1] - point[1]) < tol:
            return "Vertex%d" % i
    view.makeCosmeticVertex(App.Vector(point[0], point[1], 0))
    for i, p in _vertices(view):
        if abs(p[0] - point[0]) < tol and abs(p[1] - point[1]) < tol:
            return "Vertex%d" % i
    raise ValueError("Could not add a point at the circle's centre")


def ref_name(view, ent):
    if ent.kind == "vertex":
        return "Vertex%d" % ent.index
    if ent.kind == "centre":
        return vertex_at(view, ent.point)
    return "Edge%d" % ent.index


def diameter_across(view, ents, tol=0.01):
    """Radius of the cylinder whose two outline lines (seen from the side)
    these two line entities are, else None: that distance is a diameter."""
    if len(ents) != 2 or any(e.kind != "line" for e in ents):
        return None
    try:
        shape = core.source_shape(view.Source)
        d, right, up = core.view_axes(view)
    except Exception:
        return None
    for face in core._round_faces(shape):
        axis = App.Vector(face.Surface.Axis)
        axis.normalize()
        if abs(axis.dot(d)) > 1e-6:
            continue                          # not seen from the side
        r = face.Surface.Radius
        c = core.model_to_local(view, face.Surface.Center, shape)
        a = core.model_to_local(view, App.Vector(face.Surface.Center) + axis, shape)
        u = _unit(_sub(a, c))
        n = (-u[1], u[0])
        sides = []
        for e in ents:
            p, q = e.pts
            if abs(abs(_dot(_unit(_sub(q, p)), u)) - 1.0) > 1e-6:
                break                          # not along the axis
            sides.append(_dot(_sub(p, c), n))
        if len(sides) == 2 and abs(abs(sides[0]) - r) < tol and abs(abs(sides[1]) - r) < tol \
                and sides[0] * sides[1] < 0:
            return r
    return None


def add_dimension(page, view, picks, mouse_local, label_page=None):
    """Create the dimension the picks + mouse describe. ``label_page`` is
    where the text goes (page point); default: the mouse position."""
    t, ents, why = plan(picks, mouse_local)
    if t is None:
        raise ValueError(why)
    doc = page.Document
    names = [ref_name(view, e) for e in ents]
    dim = doc.addObject("TechDraw::DrawViewDimension", "Dimension")
    dim.Type = t
    dim.MeasureType = "Projected"
    dim.References2D = [(view, names)]
    page.addView(dim)
    from . import fonts
    fonts.style_dimension(dim)
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    if label_page is None:
        label_page = (cx + mouse_local[0] * s, cy + mouse_local[1] * s)
    dim.X, dim.Y = label_page[0] - cx, label_page[1] - cy
    from . import standards
    standards.style_dimension(dim)
    if t in ("Distance", "DistanceX", "DistanceY") and diameter_across(view, ents) is not None:
        spec = dim.FormatSpec or "%.2w"
        if "⌀" not in spec and "Ø" not in spec:
            dim.FormatSpec = "⌀" + spec          # across a cylinder: a diameter
    if t in ("Diameter", "Radius"):
        # text outside the circle: horizontal, on a leader with a shoulder
        # (TechDraw's own style would tilt it along the leader)
        e = ents[0]
        if math.hypot(mouse_local[0] - e.point[0], mouse_local[1] - e.point[1]) > e.radius:
            from . import holes
            holes.set_horizontal_text(dim)
    return dim
