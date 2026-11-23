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


def _kept(sec):
    """(kept half-space, plane origin, normal): TechDraw keeps the side
    opposite the section normal."""
    n = App.Vector(sec.SectionNormal)
    n.normalize()
    o = App.Vector(sec.SectionOrigin)
    return halfspace(o, -n), o, n


def cut_faces(shape, sec, keep):
    """Faces of ``shape`` lying on the section's cut plane."""
    o, n = App.Vector(sec.SectionOrigin), App.Vector(sec.SectionNormal)
    n.normalize()
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
        if abs((f.CenterOfMass - o).dot(n)) > 1e-4:
            continue
        out.append(f)
    return out


def _local_frame(sec, keep):
    """Model -> section-view local (unscaled, Y up) mapping."""
    full = core.source_shape(sec.Source)
    kept = full.common(keep)
    centre = kept.BoundBox.Center
    d, right, up = core.view_axes(sec)
    return lambda p: ((p - centre).dot(right), (p - centre).dot(up))


def _face_2d(face, to_local):
    """The face redrawn flat in the view's local XY plane."""
    import Part
    wires = []
    for w in face.Wires:
        pts = []
        for e in w.OrderedEdges:
            for p in e.discretize(Deflection=0.005):
                q = to_local(p)
                v = App.Vector(q[0], q[1], 0)
                if not pts or (pts[-1] - v).Length > 1e-7:
                    pts.append(v)
        if len(pts) >= 3:
            if (pts[0] - pts[-1]).Length > 1e-7:
                pts.append(pts[0])
            wires.append(Part.makePolygon(pts))
    if not wires:
        return None
    try:
        return Part.Face(Part.Shape(wires), "Part::FaceMakerBullseye")
    except Exception:
        try:
            return Part.Face(wires[0])
        except Exception:
            return None


def hatch_lines(face2d, angle, spacing):
    """Hatch segments ((x0, y0), (x1, y1)) clipped to a flat face."""
    import Part
    bb = face2d.BoundBox
    cx, cy = (bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2
    reach = math.hypot(bb.XLength, bb.YLength) / 2 + spacing
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)          # along the lines
    nx, ny = -uy, ux                            # across them
    # lines on a fixed grid (through the origin) so neighbours line up
    k0 = math.floor(((cx * nx + cy * ny) - reach) / spacing)
    k1 = math.ceil(((cx * nx + cy * ny) + reach) / spacing)
    segs = []
    for k in range(int(k0), int(k1) + 1):
        off = k * spacing
        px, py = nx * off, ny * off
        # the point on this line nearest the face's middle
        t = (cx - px) * ux + (cy - py) * uy
        mx, my = px + ux * t, py + uy * t
        p0 = App.Vector(mx - ux * reach, my - uy * reach, 0)
        p1 = App.Vector(mx + ux * reach, my + uy * reach, 0)
        try:
            cut = face2d.common(Part.LineSegment(p0, p1).toShape())
        except Exception:
            continue
        for e in cut.Edges:
            v = e.Vertexes
            if len(v) >= 2 and (v[0].Point - v[-1].Point).Length > 1e-6:
                segs.append(((v[0].Point.x, v[0].Point.y), (v[-1].Point.x, v[-1].Point.y)))
    return segs


def plan(sec):
    """{part name: {'angle', 'spacing', 'segments' (local), 'skip'}}"""
    keep, o, n = _kept(sec)
    to_local = _local_frame(sec, keep)
    s = core.view_scale(sec)
    from . import drafting
    out = {}
    i = 0
    styles = {}         # identical parts (one parts-list item) share a hatch
    for part in section_parts(sec):
        shape = core.shape_of(part)
        if shape is None:
            continue
        faces = cut_faces(shape, sec, keep)
        if not faces:
            continue
        if is_unsectioned(part):
            out[part.Name] = {"skip": True, "segments": []}
            continue
        try:
            sig = drafting._signature(part)
        except Exception:
            sig = part.Name
        if sig not in styles:
            styles[sig] = i
            i += 1
        k = styles[sig]
        angle = ANGLES[k % len(ANGLES)]
        flat = [f2 for f2 in (_face_2d(f, to_local) for f in faces) if f2 is not None]
        # small sections get closer hatching (about four lines across the
        # narrowest piece, never under 1 mm on paper)
        narrow = min((min(f2.BoundBox.XLength, f2.BoundBox.YLength) for f2 in flat),
                     default=SPACING) * s
        paper = max(1.0, min(SPACING, narrow / 4.0))
        spacing = paper * (1.0 + 0.5 * (k // len(ANGLES))) / s
        segs = []
        for f2 in flat:
            segs.extend(hatch_lines(f2, angle, spacing))
        out[part.Name] = {"angle": angle, "spacing": spacing * s, "segments": segs, "skip": False}
    return out


def draw(sk, sec, p):
    """Hatch lines (stored in section-local units) drawn on the page."""
    for item in p.get("parts", {}).values():
        for a, b in item.get("segments", []):
            sk.line(annot.view_point(sec, a), annot.view_point(sec, b), 0.18)
    if not sk.items:            # nothing to hatch: keep the SVG valid
        cx, cy = core.view_center(sec)
        sk.line((cx, cy), (cx, cy), 0.0)


def existing(sec):
    for o in sec.InList:
        if getattr(o, annot.KIND, "") == "hatch" and core.same(getattr(o, annot.LINK, None), sec):
            return o
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
    if sec.isDerivedFrom("TechDraw::DrawComplexSection"):
        return None, {}         # stepped/aligned cuts are not one plane
    parts = plan(sec)
    if len(parts) < 2:
        return None, parts
    sec.CutSurfaceDisplay = "Hide"
    p = {"parts": parts}
    sym = existing(sec)
    if sym is None:
        sym = annot._new(page, "hatch", "Hatch %s" % sec.Label, p, "view", sec, (0.0, 0.0), (0.0, 0.0))
    else:
        import json
        old = annot._params(sym)
        old["parts"] = parts
        setattr(sym, annot.PARAMS, json.dumps(old))
        annot.render(sym)
    return sym, parts
