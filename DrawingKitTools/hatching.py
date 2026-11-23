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
    """Straight and stepped (offset) sections; aligned ones swing pieces
    round, which this doesn't follow yet."""
    if sec.isDerivedFrom("TechDraw::DrawComplexSection"):
        return is_stepped(sec)
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
        skip = ov.get("off", is_unsectioned(part)) if "off" in ov else is_unsectioned(part)
        if skip:
            # where its uncut drawing goes: TechDraw centres a view on the
            # part's box, so the box centre in section-local units
            out[part.Name] = {"skip": True, "segments": [], "dashed": [], "label": part.Label,
                              "uncut_at": list(_to_local_cache[0](core.shape_of(part).BoundBox.Center))}
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
        pattern = ov.get("pattern") or material_pattern(material)
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


def _part_regions_for(sec):
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
