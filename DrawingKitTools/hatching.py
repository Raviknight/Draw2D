# SPDX-License-Identifier: LGPL-2.1-or-later
"""Hatching by part in assembly sections (ISO 128-50).

TechDraw gives a section one hatch for everything it cuts. Here each part's
cut faces get their own hatch: a different angle (45°, 135°, 30°, 120°...)
and, after six parts, a different spacing, so neighbouring parts read
apart. Shafts, pins, bolts, screws, keys, rivets, nuts and washers are not
hatched (they are conventionally not sectioned lengthwise); give any part a
DrawingKitNoSection = True property to treat it that way too.

How it works: TechDraw keeps the half of the model behind the cut plane
(opposite the section normal) and centres the drawing on that half's box.
The same cut is made here part by part in 3D, the faces lying on the cut
plane are projected into the section view, and hatch lines clipped to them
are drawn as a DrawingKit symbol tied to the section view, so it moves with
it. The section's own hatch is switched off. Run it again (Hatch by part)
after changing the model or the cut.
"""

import math
import re

import FreeCAD as App

from . import annot
from . import core

ANGLES = [45, 135, 30, 120, 60, 150]
SPACING = 2.5            # mm on paper between hatch lines
NO_SECTION = re.compile(r"(shaft|pin|bolt|screw|key|rivet|nut|washer|dowel|stud)",
                        re.IGNORECASE)
PROP = "DrawingKitNoSection"


def halfspace(origin, normal, L=5000.0):
    """A big solid on the +normal side of the plane through origin."""
    import Part
    n = App.Vector(normal)
    n.normalize()
    rot = App.Rotation(App.Vector(0, 0, 1), n)
    b = Part.makeBox(2 * L, 2 * L, L)
    b.Placement = App.Placement(App.Vector(origin) - rot.multVec(App.Vector(L, L, 0)), rot)
    return b


def is_unsectioned(obj):
    """Parts drawn uncut by convention (or marked by the user)."""
    if PROP in getattr(obj, "PropertiesList", []):
        return bool(getattr(obj, PROP))
    src = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
    return bool(NO_SECTION.search(src.Label) or NO_SECTION.search(obj.Label))


def part_axis(shape):
    """Long axis of a shaft/pin/bolt-like part: the axis of its biggest
    cylindrical face, else the long side of its box if it is clearly
    longer than wide. None if it has no clear axis."""
    best = None
    for f in shape.Faces:
        if f.Surface.TypeId == "Part::GeomCylinder":
            if best is None or f.Area > best[0]:
                best = (f.Area, App.Vector(f.Surface.Axis))
    if best is not None:
        ax = best[1]
        ax.normalize()
        return ax
    bb = shape.BoundBox
    dims = sorted([(bb.XLength, App.Vector(1, 0, 0)), (bb.YLength, App.Vector(0, 1, 0)),
                   (bb.ZLength, App.Vector(0, 0, 1))], key=lambda t: -t[0])
    if dims[0][0] >= 2.0 * dims[1][0]:
        return dims[0][1]
    return None


def cut_normals(sec):
    """Normals of the planes the section cuts on (one per run for an
    aligned section)."""
    if is_aligned(sec):
        d, _, _ = core.view_axes(sec.BaseView)
        pts = [App.Vector(v.Point) for v in sec.CuttingToolWireObject.Shape.Vertexes]
        out = []
        for a, b in zip(pts, pts[1:]):
            m = (b - a).cross(d)
            if m.Length > 1e-9:
                m.normalize()
                out.append(m)
        return out
    n = App.Vector(sec.SectionNormal)
    n.normalize()
    return [n]


def cut_lengthwise(part, sec):
    """True if the cut runs along the part's axis (the axis lies in a cut
    plane): ASME Y14.3 and ISO 128 then show shafts, pins, bolts... whole,
    not sectioned. Cut across, they are hatched like any other part."""
    shape = core.shape_of(part)
    ax = part_axis(shape) if shape is not None else None
    if ax is None:
        return False
    return any(abs(ax.dot(n)) < 0.5 for n in cut_normals(sec))


def section_parts(sec):
    from . import drafting
    return drafting._leaf_parts(list(sec.Source))


def complex_kept(sec, L=5000.0):
    """The region a stepped (offset) section keeps: everything on the far
    side (opposite the normal) of the stepped cutting surface, which is the
    cutting line swept along the base view's direction."""
    import Part
    n = App.Vector(sec.SectionNormal)
    n.normalize()
    base = sec.BaseView
    d, _, _ = core.view_axes(base)
    pts = [App.Vector(v.Point) for v in sec.CuttingToolWireObject.Shape.Vertexes]
    if len(pts) < 2:
        raise ValueError("No cutting line")
    # run the ends well past the part, then close the outline behind the cut
    first = pts[0] + (pts[0] - pts[1]).normalize() * L
    last = pts[-1] + (pts[-1] - pts[-2]).normalize() * L
    poly = [first] + pts + [last, last - n * L, first - n * L, first]
    # flatten onto one plane across the view direction
    o = pts[0]
    flat = [p - d * (p - o).dot(d) for p in poly]
    face = Part.Face(Part.makePolygon(flat))
    return face.extrude(d * 2 * L).translated(-d * L)


def is_stepped(sec):
    return sec.isDerivedFrom("TechDraw::DrawComplexSection") and         str(getattr(sec, "ProjectionStrategy", "")) == "Offset"


def supported(sec):
    """Straight, stepped (offset) and aligned sections."""
    if sec.isDerivedFrom("TechDraw::DrawComplexSection"):
        return is_stepped(sec) or is_aligned(sec)
    return sec.isDerivedFrom("TechDraw::DrawViewSection")


def cut_planes(sec):
    """[(point, normal)] of the planes the section cuts on: one for a
    straight section, one per step for a stepped one."""
    n = App.Vector(sec.SectionNormal)
    n.normalize()
    if not is_stepped(sec):
        return [(App.Vector(sec.SectionOrigin), n)]
    pts = [App.Vector(v.Point) for v in sec.CuttingToolWireObject.Shape.Vertexes]
    out = []
    for a, b in zip(pts, pts[1:]):
        u = b - a
        if u.Length > 1e-9 and abs(u.normalize().dot(n)) < 1e-6:
            out.append((a, n))       # a step across the view (not a jog)
    return out or [(App.Vector(sec.SectionOrigin), n)]


def _kept(sec):
    """(kept region, plane origin, normal): TechDraw keeps the side
    opposite the section normal (behind the stepped surface for a stepped
    section)."""
    n = App.Vector(sec.SectionNormal)
    n.normalize()
    o = App.Vector(sec.SectionOrigin)
    if is_stepped(sec):
        return complex_kept(sec), o, n
    return halfspace(o, -n), o, n


def cut_faces(shape, sec, keep):
    """Faces of ``shape`` lying on the section's cut plane."""
    planes = cut_planes(sec)
    n = planes[0][1]
    try:
        part = shape.common(keep)
    except Exception:
        return []
    out = []
    for f in part.Faces:
        surf = f.Surface
        if "Plane" not in surf.TypeId:
            continue
        ax = App.Vector(surf.Axis)
        if abs(abs(ax.dot(n)) - 1.0) > 1e-6:
            continue
        c = f.CenterOfMass
        if not any(abs((c - o).dot(n)) < 1e-4 for o, _ in planes):
            continue
        out.append(f)
    return out


def kept_centre(sec, keep=None):
    """Model point TechDraw puts at the middle of a straight or stepped
    section: the centre of the box of what it keeps."""
    import FreeCAD
    keep = keep if keep is not None else _kept(sec)[0]
    bb = FreeCAD.BoundBox()
    shapes = [core.shape_of(p) for p in section_parts(sec)] or [core.source_shape(sec.Source)]
    for shp in shapes:
        if shp is None:
            continue
        try:
            k = shp.common(keep)
        except Exception:
            continue
        if not k.isNull() and k.Volume > 1e-9:
            bb.add(k.BoundBox)
    if not bb.isValid():
        bb = core.source_shape(sec.Source).BoundBox
    return bb.Center


def align_offset(sec):
    """(along base right, along base up) in model units: how far the
    section's centre must sit from its base view's centre so the part lines
    up across both views. None for an aligned section TechDraw hasn't drawn
    yet."""
    base = sec.BaseView
    d, rb, ub = core.view_axes(base)
    full_c = core.shape_centre(core.source_shape(base.Source), d)
    if is_aligned(sec):
        key = (sec.Document.Name, sec.Name)
        _aligned_anchor.pop(key, None)    # the model may have changed
        if core.section_stale_factor(sec) != 1.0:
            return None                   # not (re)drawn at this scale yet
        aligned_part_regions(sec)
        if key not in _aligned_anchor:
            return None
        p, (lx, ly) = _aligned_anchor[key]
        return ((p - full_c).dot(rb) - lx, (p - full_c).dot(ub) - ly)
    c = kept_centre(sec)
    return ((c - full_c).dot(rb), (c - full_c).dot(ub))


def _local_frame(sec, keep):
    """Model -> section-view local (unscaled, Y up) mapping: TechDraw
    centres the section on the box of what it keeps. Worked out part by
    part: one boolean on the whole assembly fails when parts overlap (a
    screw in its hole) and returns nothing."""
    import FreeCAD
    bb = FreeCAD.BoundBox()
    shapes = [core.shape_of(p) for p in section_parts(sec)] or [core.source_shape(sec.Source)]
    for shp in shapes:
        if shp is None:
            continue
        try:
            k = shp.common(keep)
        except Exception:
            continue
        if not k.isNull() and k.Volume > 1e-9:
            bb.add(k.BoundBox)
    if not bb.isValid():
        bb = core.source_shape(sec.Source).BoundBox
    centre = bb.Center
    d, right, up = core.view_axes(sec)
    return lambda p: ((p - centre).dot(right), (p - centre).dot(up))


class Region:
    """A flat face in the view's local XY plane: closed loops (outline and
    holes) as point lists. Even-odd rule, so holes need no special care."""

    def __init__(self, loops):
        self.loops = loops
        xs = [p[0] for lp in loops for p in lp]
        ys = [p[1] for lp in loops for p in lp]
        self.box = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x, y):
        inside = False
        for lp in self.loops:
            n = len(lp)
            for i in range(n):
                (ax, ay), (bx, by) = lp[i], lp[(i + 1) % n]
                if (ay > y) != (by > y):
                    if x < ax + (y - ay) * (bx - ax) / (by - ay):
                        inside = not inside
        return inside


def _face_2d(face, to_local):
    """The face redrawn flat in the view's local XY plane, as a Region."""
    loops = []
    for w in face.Wires:
        # walk the wire as one path: edge by edge, an edge's own direction
        # can run against the wire's and twist the outline into a bow-tie
        try:
            raw = w.discretize(Deflection=0.002)
        except Exception:
            raw = []
            for e in w.OrderedEdges:
                seg = e.discretize(Deflection=0.002)
                if raw and seg and (seg[-1] - raw[-1]).Length < (seg[0] - raw[-1]).Length:
                    seg = list(reversed(seg))
                raw.extend(seg)
        pts = []
        for p in raw:
            q = to_local(p)
            if not pts or math.hypot(pts[-1][0] - q[0], pts[-1][1] - q[1]) > 1e-9:
                pts.append(q)
        if len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
            pts.pop()
        if len(pts) >= 3:
            loops.append(pts)
    return Region(loops) if loops else None


def hatch_lines(region, angle, spacing, phase=0.0):
    """Hatch segments ((x0, y0), (x1, y1)) clipped to a Region: each line
    on a fixed grid is cut where it crosses the loops, and the crossings
    are paired up (in, out, in, out...), so lines run edge to edge."""
    x0, y0, x1, y1 = region.box
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)          # along the lines
    nx, ny = -uy, ux                            # across them
    offs = [x * nx + y * ny for x in (x0, x1) for y in (y0, y1)]
    # lines on a fixed grid (through the origin) so neighbouring parts and
    # pieces of one part line up
    k0 = math.ceil((min(offs) - phase) / spacing)
    k1 = math.floor((max(offs) - phase) / spacing)
    segs = []
    for k in range(int(k0), int(k1) + 1):
        c = k * spacing + phase
        ts = []
        for lp in region.loops:
            n = len(lp)
            for i in range(n):
                (ax, ay), (bx, by) = lp[i], lp[(i + 1) % n]
                da = ax * nx + ay * ny - c
                db = bx * nx + by * ny - c
                if (da > 0) == (db > 0) or da == db:
                    continue        # this edge doesn't cross the line
                f = da / (da - db)
                px, py = ax + (bx - ax) * f, ay + (by - ay) * f
                ts.append(px * ux + py * uy)
        ts.sort()
        for t0, t1 in zip(ts[0::2], ts[1::2]):
            if t1 - t0 > 1e-9:
                segs.append(((nx * c + ux * t0, ny * c + uy * t0),
                             (nx * c + ux * t1, ny * c + uy * t1)))
    return segs


# Patterns: how the lines of one part's hatch are laid out. Defaults come
# from the part's material, after the material symbols of ANSI Y14.2 /
# ISO 128-50's note that materials may be told apart by hatching.
PATTERNS = [("single", "Single lines"), ("double", "Double lines"),
            ("alternate", "Solid / dashed"), ("cross", "Cross-hatch"),
            ("dashed", "Dashed")]
MATERIAL_PATTERNS = [
    (re.compile(r"steel|stainless|inox|c45|s235|s355|42crmo|1\.\d{4}", re.I), "double"),
    (re.compile(r"brass|bronze|copper|cuzn|cusn", re.I), "alternate"),
    (re.compile(r"alumin|almg|alsi|magnes|titan", re.I), "cross"),
    (re.compile(r"plastic|polymer|nylon|\bpa\d|\bpom\b|\babs\b|ptfe|peek|rubber|elastom|pvc|pe-?hd", re.I),
     "dashed"),
]


PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"


def material_patterns_on():
    """Hatch patterns by material are optional: ASME Y14.2 and ISO 128 both
    use one general-purpose hatch, the material going in the parts list or
    title block. Off unless switched on."""
    try:
        return App.ParamGet(PREFS).GetBool("MaterialHatch", False)
    except Exception:
        return False


def set_material_patterns(on):
    App.ParamGet(PREFS).SetBool("MaterialHatch", bool(on))


def material_pattern(material):
    for rx, pat in MATERIAL_PATTERNS:
        if material and rx.search(material):
            return pat
    return "single"


def pattern_lines(region, angle, spacing, pattern):
    """(solid segments, dashed segments) for one region."""
    if pattern == "double":
        a = hatch_lines(region, angle, spacing * 1.6)
        b = hatch_lines(region, angle, spacing * 1.6, phase=spacing * 0.35)
        return a + b, []
    if pattern == "alternate":
        return (hatch_lines(region, angle, spacing * 2),
                hatch_lines(region, angle, spacing * 2, phase=spacing))
    if pattern == "cross":
        return (hatch_lines(region, angle, spacing * 1.4) +
                hatch_lines(region, angle + 90, spacing * 1.4)), []
    if pattern == "dashed":
        return [], hatch_lines(region, angle, spacing)
    return hatch_lines(region, angle, spacing), []


_to_local_cache = [None]       # the last section's model -> local mapping


def part_regions(sec):
    """[(part, [Region...])] for every part the section cuts."""
    keep, o, n = _kept(sec)
    to_local = _local_frame(sec, keep)
    _to_local_cache[0] = to_local
    out = []
    for part in section_parts(sec):
        shape = core.shape_of(part)
        if shape is None:
            continue
        faces = cut_faces(shape, sec, keep)
        if not faces:
            continue
        regions = [r for r in (_face_2d(f, to_local) for f in faces) if r is not None]
        if regions:
            out.append((part, regions))
    return out


def plan(sec, overrides=None):
    """{part name: {'angle', 'spacing' (paper mm), 'pattern', 'segments',
    'dashed' (local units), 'skip', 'label'}}. ``overrides``: {part name:
    {'angle', 'spacing', 'pattern', 'off'}} chosen by the user."""
    from . import drafting
    overrides = overrides or {}
    s = core.view_scale(sec)
    out = {}
    i = 0
    styles = {}         # identical parts (one parts-list item) share a hatch
    for part, regions in _part_regions_for(sec):
        ov = overrides.get(part.Name, {})
        if "off" in ov:
            skip = bool(ov["off"])
        else:
            # shafts, pins, bolts... are drawn whole only when cut lengthwise
            skip = is_unsectioned(part) and cut_lengthwise(part, sec)
        if skip:
            # where its uncut drawing goes: TechDraw centres a view on the
            # part's box, so the box centre in section-local units
            out[part.Name] = {"skip": True, "segments": [], "dashed": [], "label": part.Label,
                              "uncut_at": (list(_to_local_cache[0](core.shape_of(part).BoundBox.Center))
                                           if _to_local_cache[0] else None)}
            if is_aligned(sec) and part.Name in _aligned_uncut:
                at, vdir, xdir = _aligned_uncut[part.Name]
                out[part.Name].update({"uncut_at": at, "uncut_dir": vdir, "uncut_x": xdir})
            continue
        try:
            sig = drafting._signature(part)
        except Exception:
            sig = part.Name
        if sig not in styles:
            styles[sig] = i
            i += 1
        k = styles[sig]
        src = part.getLinkedObject(True) if hasattr(part, "getLinkedObject") else part
        material = core.part_material(src) or ""
        pattern = ov.get("pattern") or (material_pattern(material) if material_patterns_on()
                                        else "single")
        angle = float(ov.get("angle", ANGLES[k % len(ANGLES)]))
        # small sections get closer hatching (about four lines across the
        # narrowest piece, never under 1 mm on paper)
        narrow = min((min(r.box[2] - r.box[0], r.box[3] - r.box[1]) for r in regions),
                     default=SPACING) * s
        paper = max(1.0, min(SPACING, narrow / 4.0)) * (1.0 + 0.5 * (k // len(ANGLES)))
        paper = float(ov.get("spacing", paper))
        solid, dashed = [], []
        for r in regions:
            a, b = pattern_lines(r, angle, paper / s, pattern)
            solid.extend(a)
            dashed.extend(b)
        out[part.Name] = {"angle": angle, "spacing": paper, "pattern": pattern,
                          "material": material, "segments": solid, "dashed": dashed,
                          "skip": False, "label": part.Label}
    return out


def is_aligned(sec):
    return sec.isDerivedFrom("TechDraw::DrawComplexSection") and \
        str(getattr(sec, "ProjectionStrategy", "")) == "Aligned"


def drawn_vertices(sec):
    """Corner points TechDraw drew for a view (local units), [] if it hasn't
    drawn it yet."""
    out = []
    seen = set()
    # complex sections give no vertices of their own: take the edges' ends
    for i in range(20000):
        try:
            e = sec.getEdgeByIndex(i)
        except Exception:
            break
        for v in e.Vertexes:
            key = (round(v.Point.x, 6), round(v.Point.y, 6))
            if key not in seen:
                seen.add(key)
                out.append((v.Point.x, v.Point.y))
    return out


def _piece_faces(shape, a, u, d, length, big=5000.0):
    """Cross-section of ``shape`` on the plane through ``a`` spanned by the
    cutting-line run ``u`` and the view direction ``d``, trimmed to the run
    (0..length along u). Returns Part faces."""
    import Part
    m = u.cross(d)
    if m.Length < 1e-9:
        return []
    m.normalize()
    try:
        wires = shape.slice(m, m.dot(a))
    except Exception:
        return []
    if not wires:
        return []
    try:
        face = Part.Face(Part.Shape(wires), "Part::FaceMakerBullseye")
    except Exception:
        return []
    strip = Part.Face(Part.makePolygon([a - d * big, a + u * length - d * big,
                                        a + u * length + d * big, a + d * big, a - d * big]))
    try:
        return [f for f in face.common(strip).Faces if f.Area > 1e-9]
    except Exception:
        return []


def _orient(fit, u, v):
    swap, su, sv, tx, ty = fit
    a, b = su * u, sv * v
    return (b + tx, a + ty) if swap else (a + tx, b + ty)


def _register(corners, drawn, tol=0.02, avoid=()):
    """Best (swap, su, sv, tx, ty) putting piece corners (u along the cut,
    v along the view direction) onto drawn corners: any of the eight
    turns/flips and a shift voted for by every corner pair. Near-equal
    fits (a piece and its mirror image) are told apart by keeping clear of
    the boxes in ``avoid`` (pieces already placed). None if under half the
    corners (and fewer than 3) agree."""
    corners = list({(round(u, 6), round(v, 6)) for u, v in corners})
    if not corners or not drawn:
        return None
    grid = lambda v: round(v / tol)
    cands = []
    for swap in (False, True):
        for su in (1, -1):
            for sv in (1, -1):
                votes = {}
                for (u, v) in corners:
                    px, py = _orient((swap, su, sv, 0.0, 0.0), u, v)
                    for (x, y) in drawn:
                        key = (grid(x - px), grid(y - py))
                        votes[key] = votes.get(key, 0) + 1
                for key, n in votes.items():
                    cands.append((n, (swap, su, sv, key[0] * tol, key[1] * tol)))
    need = max(3, 0.5 * len(corners))
    top = max(n for n, _ in cands)
    good = [(n, f) for n, f in cands if n >= need and n >= 0.75 * top]
    if not good:
        return None

    def overlap(fit):
        pts = [_orient(fit, u, v) for u, v in corners]
        bx = (min(p[0] for p in pts), min(p[1] for p in pts),
              max(p[0] for p in pts), max(p[1] for p in pts))
        tot = 0.0
        for a in avoid:
            w = min(bx[2], a[2]) - max(bx[0], a[0])
            h = min(bx[3], a[3]) - max(bx[1], a[1])
            if w > 1e-6 and h > 1e-6:
                tot += w * h
        return tot
    n, fit = min(good, key=lambda nf: (overlap(nf[1]), -nf[0]))
    # refine the shift: average over the corners that matched
    swap, su, sv, tx, ty = fit
    dx, dy, k = 0.0, 0.0, 0
    for (u, v) in corners:
        px, py = _orient(fit, u, v)
        near = min(drawn, key=lambda q: (q[0] - px) ** 2 + (q[1] - py) ** 2)
        if (near[0] - px) ** 2 + (near[1] - py) ** 2 < (3 * tol) ** 2:
            dx += near[0] - px
            dy += near[1] - py
            k += 1
    if k:
        tx, ty = tx + dx / k, ty + dy / k
    return swap, su, sv, tx, ty


_aligned_uncut = {}     # part name -> (local centre, view direction, x direction)
_aligned_anchor = {}    # (doc, section) -> (model point, its local position)


def _uncut_frame(part, fit, a, u, d):
    """Where and how to draw a part whole on an aligned section: the run's
    piece is turned by ``fit`` (u along the run, v along d), so an ordinary
    view looking square at the run's plane, turned the same way, lands on
    TechDraw's drawing. (local centre, Direction, XDirection)."""
    swap, su, sv, tx, ty = fit
    ux, vy = u * su, d * sv                 # model directions of +local u / v
    xm, ym = (vy, ux) if swap else (ux, vy)  # model directions of local x, y
    view_dir = xm.cross(ym)
    c = core.shape_of(part).BoundBox.Center
    local = _orient(fit, (c - a).dot(u), (c - a).dot(d))
    return [local[0], local[1]], [view_dir.x, view_dir.y, view_dir.z], [xm.x, xm.y, xm.z]


def aligned_part_regions(sec):
    """[(part, [Region...])] for an aligned section: each run of the cutting
    line gives a piece of every part it cuts; TechDraw swings the pieces
    flat and lays them side by side, so each run's pieces are matched to
    what TechDraw drew (flip and shift from the drawn corners). None if the
    section hasn't been drawn yet."""
    drawn = drawn_vertices(sec)
    if not drawn:
        return None
    d, _, _ = core.view_axes(sec.BaseView)
    pts = [App.Vector(v.Point) for v in sec.CuttingToolWireObject.Shape.Vertexes]
    parts = [(p, core.shape_of(p)) for p in section_parts(sec)]
    found = {}
    runs = []
    for a, b in zip(pts, pts[1:]):
        run = b - a
        length = run.Length
        if length < 1e-9:
            continue
        u = run * (1.0 / length)
        pieces = []
        for part, shape in parts:
            if shape is None:
                continue
            for f in _piece_faces(shape, a, u, d, length):
                pieces.append((part, f))
        if not pieces:
            continue
        to_uv = lambda p, a=a, u=u: ((p - a).dot(u), (p - a).dot(d))
        corners = []
        for _, f in pieces:
            for vx in f.Vertexes:
                c = to_uv(vx.Point)
                if all(abs(c[0] - q[0]) > 1e-6 or abs(c[1] - q[1]) > 1e-6 for q in corners):
                    corners.append(c)
        runs.append((pieces, to_uv, corners, a, u))
    placed = []
    _aligned_uncut.clear()
    for pieces, to_uv, corners, a, u in sorted(runs, key=lambda r: -len(r[2])):
        fit = _register(corners, drawn, avoid=placed)
        if fit is None:
            continue
        if (a - pts[-2]).Length < 1e-9:
            # the run seen true starts at the bend: that point lines the
            # section up with its base view
            _aligned_anchor[(sec.Document.Name, sec.Name)] = (App.Vector(a), _orient(fit, 0.0, 0.0))
        ps = [_orient(fit, u, v) for u, v in corners]
        placed.append((min(p[0] for p in ps), min(p[1] for p in ps),
                       max(p[0] for p in ps), max(p[1] for p in ps)))
        place = lambda p, f=to_uv, fit=fit: _orient(fit, *f(p))
        for part, f in pieces:
            r = _face_2d(f, place)
            if r is not None:
                found.setdefault(part.Name, (part, []))[1].append(r)
            if part.Name not in _aligned_uncut:
                _aligned_uncut[part.Name] = _uncut_frame(part, fit, a, u, d)
    _to_local_cache[0] = None      # no single mapping: each run has its own
    return list(found.values())


def _part_regions_for(sec):
    if is_aligned(sec):
        return aligned_part_regions(sec) or []
    return part_regions(sec)


UNCUT = "DrawingKitUncutOf"     # property on the uncut views: their section


def uncut_views(sec):
    """{part name: view} of the uncut overlays made for a section."""
    out = {}
    for o in sec.InList:
        if UNCUT in getattr(o, "PropertiesList", []) and core.same(getattr(o, UNCUT), sec):
            src = list(o.Source)
            if src:
                out[src[0].Name] = o
    return out


def is_uncut_view(v):
    return UNCUT in getattr(v, "PropertiesList", [])


def sync_uncut(page, sec, parts):
    """Make, place or remove the uncut views: one per part left unhatched
    (shafts, pins, bolts... are drawn whole, not cut, ISO 128-50)."""
    doc = page.Document
    have = uncut_views(sec)
    want = {n: it for n, it in parts.items() if it.get("skip") and it.get("uncut_at")}
    for name, v in have.items():
        if name not in want:
            try:
                page.removeView(v)
            except Exception:
                pass
            doc.removeObject(v.Name)
    for name, it in want.items():
        v = have.get(name)
        part = doc.getObject(name)
        if part is None:
            continue
        if v is None:
            v = doc.addObject("TechDraw::DrawViewPart", "Uncut")
            page.addView(v)
            v.addProperty("App::PropertyLink", UNCUT, "DrawingKit", "Section this uncut part sits on")
            setattr(v, UNCUT, sec)
            v.Source = [part]
            v.Label = "Uncut %s (%s)" % (part.Label, sec.Label)
        if it.get("uncut_dir"):          # aligned section: square to its run
            v.Direction = App.Vector(*it["uncut_dir"])
            v.XDirection = App.Vector(*it["uncut_x"])
        else:
            v.Direction = sec.Direction
            v.XDirection = sec.XDirection
        v.ScaleType = "Custom"
        v.Scale = core.view_scale(sec)
        place_uncut(sec, v, it["uncut_at"])
    return uncut_views(sec)


def place_uncut(sec, v, local):
    x, y = annot.view_point(sec, local)
    if abs(float(v.X) - x) > 1e-9 or abs(float(v.Y) - y) > 1e-9:
        v.X, v.Y = x, y


def draw(sk, sec, p):
    """Hatch lines (stored in section-local units) drawn on the page."""
    # the uncut parts ride along with the section
    try:
        have = uncut_views(sec)
        for name, it in p.get("parts", {}).items():
            if name in have and it.get("uncut_at"):
                place_uncut(sec, have[name], it["uncut_at"])
    except Exception:
        pass
    for item in p.get("parts", {}).values():
        for a, b in item.get("segments", []):
            sk.line(annot.view_point(sec, a), annot.view_point(sec, b), 0.18)
        for a, b in item.get("dashed", []):
            sk.line(annot.view_point(sec, a), annot.view_point(sec, b), 0.18, dash=True)
    if not sk.items:            # nothing to hatch: keep the SVG valid
        cx, cy = core.view_center(sec)
        sk.line((cx, cy), (cx, cy), 0.0)


def existing(sec):
    for o in sec.InList:
        if getattr(o, annot.KIND, "") == "hatch" and core.same(getattr(o, annot.LINK, None), sec):
            return o
    return None


def set_override(sym, part_name, **choice):
    """Remember a user's choice for one part's hatch (angle, spacing in
    paper mm, pattern, off) on the hatch symbol; kept on every redo.
    Passing None for a key forgets that choice."""
    import json
    p = annot._params(sym)
    ov = p.setdefault("overrides", {}).setdefault(part_name, {})
    for k, v in choice.items():
        if v is None:
            ov.pop(k, None)
        else:
            ov[k] = v
    if not ov:
        p["overrides"].pop(part_name, None)
    setattr(sym, annot.PARAMS, json.dumps(p))
    return p["overrides"]


def part_at(sym, x, y):
    """The part whose hatched (or skipped) cut face is under page (x, y)."""
    sec = getattr(sym, annot.LINK)
    lx, ly = annot.page_to_local(sec, x, y)
    for part, regions in _part_regions_for(sec):
        if any(r.contains(lx, ly) for r in regions):
            return part
    return None


def refresh_page(page):
    """Redo every part hatch on the page (after a scale change or a model
    edit: face shapes and the spacing on paper may have changed)."""
    n = 0
    for v in list(page.Views):
        if getattr(v, annot.KIND, "") == "hatch":
            sec = getattr(v, annot.LINK, None)
            if sec is not None:
                try:
                    hatch_by_part(page, sec)
                    n += 1
                except Exception as e:
                    App.Console.PrintWarning("DrawingKit hatch %s: %s\n" % (v.Label, e))
    return n


def hatch_by_part(page, sec):
    """Hatch the section part by part (making or updating its hatch
    symbol). Returns (symbol, plan) or (None, {}) if the section cuts fewer
    than two parts (TechDraw's own hatch is fine then)."""
    if not supported(sec):
        return None, {}         # aligned sections swing pieces round
    sym = existing(sec)
    overrides = annot._params(sym).get("overrides", {}) if sym is not None else {}
    parts = plan(sec, overrides)
    if len(parts) < 2:
        return None, parts
    sec.CutSurfaceDisplay = "Hide"
    try:
        sync_uncut(page, sec, parts)
    except Exception as e:
        App.Console.PrintWarning("DrawingKit uncut parts: %s\n" % e)
    p = {"parts": parts, "overrides": overrides}
    if sym is None:
        sym = annot._new(page, "hatch", "Hatch %s" % sec.Label, p, "view", sec, (0.0, 0.0), (0.0, 0.0))
    else:
        import json
        old = annot._params(sym)
        old["parts"] = parts
        setattr(sym, annot.PARAMS, json.dumps(old))
        annot.render(sym)
    return sym, parts
