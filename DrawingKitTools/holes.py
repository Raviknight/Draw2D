# SPDX-License-Identifier: LGPL-2.1-or-later
"""Holes: find them in the 3D part, group the same ones, and label them
with a TechDraw diameter dimension reading e.g. '3X Ø6.40 THRU'.

No GUI needed (testable with freecadcmd).
"""

import math

import FreeCAD as App

from . import core

TOL = 1e-4
DEPTH_SYMBOL = "↧"   # ↧  (ISO 129 / ASME depth symbol)
DIAMETER = "⌀"       # ⌀


COUNTERBORE = "⌴"   # ⌴  ASME Y14.5 / ISO 129-1
COUNTERSINK = "⌵"   # ⌵
SPOTFACE = "SF"
SPOTFACE_RATIO = 0.2       # a counterbore shallower than this x its diameter is a spotface


class Hole:
    def __init__(self, centre, axis, radius, t0, t1, through):
        self.centre = centre      # point on the axis (3D)
        self.axis = axis          # unit vector
        self.radius = radius
        self.t0, self.t1 = t0, t1  # extent along the axis from centre
        self.through = through
        self.cbore = None         # (diameter, depth) of a counterbore / spotface
        self.spotface = False
        self.csink = None         # (diameter at the surface, included angle)

    def key(self):
        """What makes two holes 'the same' for counting (3X ...)."""
        return (round(self.radius * 2, 3), True if self.through else round(self.depth, 3),
                tuple(round(x, 3) for x in self.cbore) if self.cbore else None, self.spotface,
                tuple(round(x, 2) for x in self.csink) if self.csink else None)

    @property
    def depth(self):
        return self.t1 - self.t0

    def __repr__(self):
        return "Hole(r=%.3f, %s)" % (self.radius, "THRU" if self.through else "depth %.3f" % self.depth)


def _is_hole_face(face):
    """True for a concave (inside) cylindrical face covering >= 180 deg."""
    surf = face.Surface
    if surf.TypeId != "Part::GeomCylinder":
        return False
    u0, u1, v0, v1 = face.ParameterRange
    if u1 - u0 < math.pi - 1e-3:
        return False
    u, v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    p = face.valueAt(u, v)
    n = face.normalAt(u, v)
    axis = App.Vector(surf.Axis)
    axis.normalize()
    c = surf.Center
    radial = (p - c) - axis * (p - c).dot(axis)
    return n.dot(radial) < 0  # normal points towards the axis: material outside


def find_holes(shape):
    """All round holes in the shape (split cylinder halves merged)."""
    groups = {}
    for face in shape.Faces:
        if not _is_hole_face(face):
            continue
        surf = face.Surface
        axis = App.Vector(surf.Axis)
        axis.normalize()
        if axis.x < -TOL or (abs(axis.x) < TOL and (axis.y < -TOL or (abs(axis.y) < TOL and axis.z < 0))):
            axis = -axis
        c = App.Vector(surf.Center)
        # canonical point on the axis: closest to the origin
        c = c - axis * c.dot(axis)
        ts = [(App.Vector(v.Point) - c).dot(axis) for v in face.Vertexes] or [0.0]
        key = (round(c.x, 3), round(c.y, 3), round(c.z, 3),
               round(axis.x, 4), round(axis.y, 4), round(axis.z, 4), round(surf.Radius, 4))
        lo, hi = min(ts), max(ts)
        if key in groups:
            g = groups[key]
            g[3], g[4] = min(g[3], lo), max(g[4], hi)
        else:
            groups[key] = [c, axis, surf.Radius, lo, hi]
    holes = []
    for c, axis, r, lo, hi in groups.values():
        eps = max(0.01, r * 0.05)
        # probe just past each end near the wall (not on the axis, which may
        # still be inside a narrower hole below a counterbore)
        side = axis.cross(App.Vector(1, 0, 0))
        if side.Length < 1e-6:
            side = axis.cross(App.Vector(0, 1, 0))
        side.normalize()
        w = side * (r * 0.9)
        open0 = not shape.isInside(c + w + axis * (lo - eps), 1e-6, True)
        open1 = not shape.isInside(c + w + axis * (hi + eps), 1e-6, True)
        holes.append(Hole(c, axis, r, lo, hi, open0 and open1))
    return _merge_coaxial(holes, _countersinks(shape))


def _same_line(c1, a1, c2, a2, tol=1e-3):
    if abs(abs(a1.dot(a2)) - 1.0) > 1e-6:
        return False
    d = c2 - c1
    return (d - a1 * d.dot(a1)).Length < tol


def _countersinks(shape):
    """Concave cones (countersinks): (point on axis, axis, t of wide end,
    wide radius, t of narrow end, included angle in degrees)."""
    out = []
    for face in shape.Faces:
        surf = face.Surface
        if surf.TypeId != "Part::GeomCone":
            continue
        axis = App.Vector(surf.Axis)
        axis.normalize()
        u0, u1, v0, v1 = face.ParameterRange
        if u1 - u0 < math.pi - 1e-3:
            continue
        u, v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        p, n = face.valueAt(u, v), face.normalAt(u, v)
        apex = App.Vector(surf.Apex)
        radial = (p - apex) - axis * (p - apex).dot(axis)
        if n.dot(radial) >= 0:
            continue          # convex: a chamfer on a pin, not a countersink
        circles = [(e.Curve.Radius, App.Vector(e.Curve.Center)) for e in face.Edges
                   if "Circle" in getattr(getattr(e, "Curve", None), "TypeId", "")]
        if not circles:
            continue
        wide = max(circles, key=lambda t: t[0])
        narrow = min(circles, key=lambda t: t[0])
        out.append((apex, axis, wide[1], wide[0], narrow[1], narrow[0],
                    2.0 * math.degrees(abs(surf.SemiAngle))))
    return out


def _merge_coaxial(holes, cones):
    """A hole with a wider, blind, coaxial bore at one end is a counterbore
    (or a spotface if shallow); a cone at one end is a countersink. The
    wider bore is folded into the hole instead of being a hole of its own."""
    used = set()
    for h in sorted(holes, key=lambda x: x.radius):
        if id(h) in used:
            continue
        for big in holes:
            if big is h or id(big) in used or big.radius <= h.radius + TOL or big.through:
                continue
            if not _same_line(h.centre, h.axis, big.centre, big.axis):
                continue
            # aligned extents (both canonical along the same axis direction)
            off = (big.centre - h.centre).dot(h.axis)
            b0, b1 = big.t0 + off, big.t1 + off
            if abs(b0 - h.t1) < 1e-3 or abs(b1 - h.t0) < 1e-3 or \
                    (h.t0 - 1e-3 <= b0 and b1 <= h.t1 + 1e-3 and (abs(b0 - h.t0) < 1e-3 or abs(b1 - h.t1) < 1e-3)):
                depth = b1 - b0
                h.cbore = (big.radius * 2, depth)
                h.spotface = depth < SPOTFACE_RATIO * big.radius * 2
                used.add(id(big))
                break
        for apex, axis, wide_c, wide_r, narrow_c, narrow_r, ang in cones:
            if wide_r <= h.radius + TOL or not _same_line(h.centre, h.axis, apex, axis):
                continue
            if abs(narrow_r - h.radius) > 1e-3:
                continue
            h.csink = (wide_r * 2, ang)
            break
    return [h for h in holes if id(h) not in used]


def holes_seen_end_on(view, shape=None):
    """Holes whose axis points along the view direction (drawn as circles)."""
    shape = shape or core.source_shape(view.Source)
    d, _, _ = core.view_axes(view)
    return [h for h in find_holes(shape) if abs(abs(h.axis.dot(d)) - 1.0) < 1e-6]


def group_holes(holes):
    """Same diameter and same depth (or both through) -> one group."""
    groups = {}
    for h in holes:
        groups.setdefault(h.key(), []).append(h)
    return list(groups.values())


def callout_text(group, decimals=2):
    """FormatSpec for TechDraw, ASME / ISO 129-1 style:
    '3X ⌀%.2f THRU', '⌀%.2f ↧ 10.00',
    '4X ⌀%.2f THRU ⌴ ⌀11.00 ↧ 6.40'  (counterbore),
    '⌀%.2f THRU SF ⌀16.00 ↧ 1.00'     (spotface),
    '⌀%.2f THRU ⌵ ⌀12.40 X 90°'      (countersink)."""
    h = group[0]
    spec = "%s%%.%df" % (DIAMETER, decimals)
    if len(group) > 1:
        spec = "%dX %s" % (len(group), spec)
    if h.through:
        spec += " THRU"
    else:
        spec += " %s %.*f" % (DEPTH_SYMBOL, decimals, h.depth)
    if h.cbore:
        dia, depth = h.cbore
        spec += " %s %s%.*f %s %.*f" % (SPOTFACE if h.spotface else COUNTERBORE, DIAMETER,
                                        decimals, dia, DEPTH_SYMBOL, decimals, depth)
    if h.csink:
        dia, ang = h.csink
        spec += " %s %s%.*f X %g°" % (COUNTERSINK, DIAMETER, decimals, dia, round(ang, 1))
    return spec


def circle_edge_name(view, hole, shape=None):
    """'EdgeN' of the circle TechDraw drew for this hole, or None."""
    shape = shape or core.source_shape(view.Source)
    lx, ly = core.model_to_local(view, hole.centre, shape)
    i = 0
    while i < 10000:
        try:
            e = view.getEdgeByIndex(i)   # unscaled, Y up
        except Exception:
            return None
        c = getattr(e, "Curve", None)
        if c is not None and "Circle" in c.TypeId and \
                abs(c.Radius - hole.radius) < 1e-4 and \
                abs(c.Center.x - lx) < 1e-3 and abs(c.Center.y - ly) < 1e-3:
            return "Edge%d" % i
        i += 1
    return None


PREFERRED_ANGLES = [45, 135, 315, 225, 90, 0, 180, 270]


def _angle_gap(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _label_offset(view, hole, ang, shape, clearance):
    """Label position for a callout, relative to the view centre (page mm):
    out from the hole along ``ang`` until clear of the view's outline."""
    s = core.view_scale(view)
    lx, ly = core.model_to_local(view, hole.centre, shape)
    w, h = core.projected_extent(shape, view.Direction, view.XDirection)
    hw, hh = w * s / 2.0, h * s / 2.0
    ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    px, py = lx * s, ly * s
    ts = []
    if abs(ux) > 1e-9:
        ts.append(((hw if ux > 0 else -hw) - px) / ux)
    if abs(uy) > 1e-9:
        ts.append(((hh if uy > 0 else -hh) - py) / uy)
    pos = [t for t in ts if t >= 0]
    t = max(min(pos) if pos else 0.0, hole.radius * s)
    return px + ux * (t + clearance), py + uy * (t + clearance)


def _inside(regions, x, y):
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in regions)


LABEL_FONT = 5.0     # TechDraw's default dimension text height here (mm)


def label_width(group):
    txt = callout_text(group).replace("%.2f", "%.2f" % (group[0].radius * 2))
    return len(txt) * LABEL_FONT * 0.58


def _label_box(x, y, w):
    """Page rectangle of a horizontal callout label centred at (x, y)."""
    return (x - w / 2.0 - 1.0, y - 1.5, x + w / 2.0 + 1.0, y + LABEL_FONT + 1.0)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _place_callout(page, view, group, shape, used, clearance=10.0, min_gap=35.0,
                   text_len=25.0, boxes=None):
    """Pick (hole, angle, (x, y) relative to view): preferred directions in
    order, clear of other callouts' labels (``boxes``, page rectangles),
    label on the drawing area (not off the frame, not on the title block)."""
    cx, cy = core.view_center(view)
    regions = core.page_regions(page)
    boxes = boxes if boxes is not None else []
    text_len = max(text_len, label_width(group))
    located = []
    for h in group:
        lx, ly = core.model_to_local(view, h.centre, shape)
        ang = math.degrees(math.atan2(ly, lx)) % 360 if math.hypot(lx, ly) > 1e-6 else None
        located.append((h, ang))
    fallback = None
    for pref in PREFERRED_ANGLES:
        if any(_angle_gap(pref, u) < min_gap for u in used):
            continue
        # the hole lying closest to this direction (any hole if centred)
        hole, _ = min(located, key=lambda o: _angle_gap(o[1], pref) if o[1] is not None else 0)
        for extra in (0.0, 8.0, 16.0, 24.0):
            rel = _label_offset(view, hole, pref, shape, clearance + extra)
            box = _label_box(cx + rel[0], cy + rel[1], text_len)
            if any(_overlap(box, b) for b in boxes):
                continue        # would sit on another label: try further out
            if _inside(regions, box[0], box[1]) and _inside(regions, box[2], box[3]):
                boxes.append(box)
                return hole, float(pref), rel
            break
        if fallback is None:
            fallback = (hole, float(pref), _label_offset(view, hole, pref, shape, clearance))
    if fallback:
        return fallback
    hole = located[0][0]
    return hole, 45.0, _label_offset(view, hole, 45.0, shape, clearance)


def add_callout(page, view, group, shape=None, used_angles=None, clearance=10.0, boxes=None):
    """Diameter dimension on one hole of the group, labelled for all of it.

    The label sits just outside the view's outline, in a direction no other
    callout on this view uses, and on the drawing area."""
    shape = shape or core.source_shape(view.Source)
    used_angles = used_angles if used_angles is not None else []
    hole, ang, rel = _place_callout(page, view, group, shape, used_angles, clearance, boxes=boxes)
    edge = circle_edge_name(view, hole, shape)
    if edge is None:
        raise ValueError("Could not find that hole's circle in %s" % view.Label)
    used_angles.append(ang)
    doc = page.Document
    dim = doc.addObject("TechDraw::DrawViewDimension", "HoleCallout")
    dim.Type = "Diameter"
    dim.MeasureType = "Projected"
    dim.References2D = [(view, edge)]
    dim.FormatSpec = callout_text(group)
    page.addView(dim)
    dim.X, dim.Y = rel   # relative to the view centre, page mm
    set_horizontal_text(dim)
    dim.Label = "Hole %s" % dim.FormatSpec.replace("%.2f", "%.2f" % (hole.radius * 2))
    return dim


def set_horizontal_text(dim):
    """Callout text reads horizontally on a shoulder ('Referencing' style)
    instead of running along the leader. GUI only (it is a display
    property); keeps the ISO/ASME family the user has chosen."""
    vo = getattr(dim, "ViewObject", None)
    if vo is None or "StandardAndStyle" not in vo.PropertiesList:
        return False
    current = str(vo.StandardAndStyle)
    vo.StandardAndStyle = "ASME Referencing" if current.startswith("ASME") else "ISO Referencing"
    return True


def add_callouts(page, view, holes=None):
    """One callout per group of identical holes seen end-on in the view."""
    shape = core.source_shape(view.Source)
    holes = holes if holes is not None else holes_seen_end_on(view, shape)
    out = []
    used = []
    # labels must not land on other views (they would be hidden under them)
    boxes = []
    for v in core.part_views(page):
        if not core.same(v, view):
            try:
                boxes.append(core.view_box(v))
            except Exception:
                pass
    # bigger holes first: they usually sit in the middle and need the space
    for g in sorted(group_holes(holes), key=lambda g: -g[0].radius):
        try:
            out.append(add_callout(page, view, g, shape, used, boxes=boxes))
        except ValueError:
            continue  # hidden in this view (no circle drawn)
    return out


def holes_for_edges(view, edge_names, shape=None):
    """Holes (seen end-on) matching selected circle edges 'EdgeN'."""
    shape = shape or core.source_shape(view.Source)
    picked = []
    for name in edge_names:
        try:
            e = view.getEdgeByIndex(int(name.replace("Edge", "")))
        except Exception:
            continue
        c = getattr(e, "Curve", None)
        if c is None or "Circle" not in c.TypeId:
            continue
        for h in holes_seen_end_on(view, shape):
            lx, ly = core.model_to_local(view, h.centre, shape)
            if abs(c.Radius - h.radius) < 1e-4 and abs(c.Center.x - lx) < 1e-3 \
                    and abs(c.Center.y - ly) < 1e-3:
                picked.append(h)
    if not picked:
        return []
    # expand to every hole identical to a picked one (for the 3X count)
    all_holes = holes_seen_end_on(view, shape)
    keys = {h.key() for h in picked}
    return [h for h in all_holes if h.key() in keys]


# ---------------------------------------------------------------------------
# Bolt circles
# ---------------------------------------------------------------------------
def bolt_circles(points, min_count=3, tol=1e-3, centre_marked=None):
    """Find sets of >= 3 points (same-size holes) lying on a common circle
    centred on their centroid. ``points`` is [(x, y)]. Returns
    [((cx, cy), R, [indices])]. ``centre_marked(cx, cy)`` tells whether a
    round feature (bore, boss) sits at the pattern centre: then four holes
    on a square are a bolt circle too, otherwise a rectangular pattern."""
    out = []
    if len(points) < min_count:
        return out
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in points]
    R = sum(rs) / len(rs)
    if R <= tol or not all(abs(r - R) < max(tol, R * 1e-4) for r in rs):
        return out
    # evenly spaced round the circle (a rectangle's corners are not)
    angles = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in points)
    n = len(angles)
    gaps = [(angles[(i + 1) % n] - angles[i]) % (2 * math.pi) for i in range(n)]
    if any(abs(g - 2 * math.pi / n) > 1e-3 for g in gaps):
        return out
    # four holes on an axis-aligned square read as a rectangular pattern,
    # unless they surround a round feature (a flange)
    if n == 4 and all(abs(abs(math.cos(a)) - abs(math.sin(a))) < 1e-3 for a in angles) \
            and not (centre_marked and centre_marked(cx, cy)):
        return out
    out.append(((cx, cy), R, list(range(n))))
    return out
