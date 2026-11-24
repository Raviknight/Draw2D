# SPDX-License-Identifier: LGPL-2.1-or-later
"""Alternate positions: a part shown swung or slid to another position in
phantom lines (ISO 128 long dash, double dot; ASME Y14.2 phantom line).

The part's outline, as the view sees it, is worked out once (hidden lines
removed for that part alone) and then turned about a pivot and / or slid
in the plane of the view - which is exactly what swinging the part about
an axis square to the view, or sliding it across, looks like on paper.
It is drawn as cosmetic lines on the view, so it scales and moves with
the view; the settings are kept on the view so it can be redrawn when the
model changes.
"""

import json
import math

import FreeCAD as App

from . import core

STYLE = 5          # TechDraw line style: long dash, double dot (phantom)
WEIGHT = 0.25      # thin line
PROP = "DrawingKitPhantoms"
DEFLECTION = 0.2   # paper mm: how closely curves are followed


def _part_shape(obj):
    """The part's shape where it sits in the assembly."""
    shp = core.shape_of(obj)
    return shp.copy() if shp is not None else None


def outline(view, obj):
    """Visible edges of one part as the view sees it: polylines in the
    view's unscaled coordinates (Y up), the part alone (nothing in front
    of it hides any of it)."""
    import TechDraw
    shape = _part_shape(obj)
    if shape is None or shape.isNull():
        raise ValueError("%s has no shape" % obj.Label)
    d, right, up = core.view_axes(view)
    centre = core.shape_centre(core.source_shape(view.Source), d)
    m = App.Matrix(right.x, right.y, right.z, 0,
                   up.x, up.y, up.z, 0,
                   d.x, d.y, d.z, 0,
                   0, 0, 0, 1)
    shape.translate(-centre)
    shape.transformShape(m)
    res = TechDraw.projectEx(shape, App.Vector(0, 0, 1))
    edges = []
    for i in (0, 1, 3):             # visible sharp, smooth and outline edges
        try:
            edges.extend(res[i].Edges)
        except Exception:
            pass
    defl = DEFLECTION / max(core.view_scale(view), 1e-9)
    polys = []
    for e in edges:
        try:
            if e.Curve.TypeId in ("Part::GeomLine", "Part::GeomLineSegment"):
                pts = [e.Vertexes[0].Point, e.Vertexes[-1].Point]
            else:
                pts = e.discretize(Deflection=defl)
        except Exception:
            try:
                pts = e.discretize(20)
            except Exception:
                continue
        if len(pts) >= 2:
            polys.append([(p.x, p.y) for p in pts])
    return polys


def moved(polys, pivot=(0.0, 0.0), angle=0.0, move=(0.0, 0.0)):
    """Polylines turned ``angle`` degrees (counter-clockwise) about
    ``pivot`` and then slid by ``move`` (view coordinates)."""
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    px, py = pivot
    mx, my = move
    out = []
    for poly in polys:
        q = []
        for x, y in poly:
            dx, dy = x - px, y - py
            q.append((px + dx * c - dy * s + mx, py + dx * s + dy * c + my))
        out.append(q)
    return out


def entries(view):
    try:
        return json.loads(getattr(view, PROP, "") or "[]")
    except ValueError:
        return []


def _store(view, items):
    if PROP not in view.PropertiesList:
        view.addProperty("App::PropertyString", PROP, "DrawingKit",
                         "Alternate positions DrawingKit draws in phantom lines")
        view.setEditorMode(PROP, 2)
    setattr(view, PROP, json.dumps(items))


def _clear(view, item):
    for tag in item.get("tags", []):
        try:
            view.removeCosmeticEdge(tag)
        except Exception:
            pass
    item["tags"] = []


def _draw(view, item):
    obj = view.Document.getObject(item["part"])
    if obj is None:
        return []
    polys = moved(outline(view, obj), tuple(item.get("pivot", (0, 0))),
                  float(item.get("angle", 0.0)), tuple(item.get("move", (0, 0))))
    tags = []
    for poly in polys:
        for a, b in zip(poly, poly[1:]):
            if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9:
                continue
            tag = view.makeCosmeticLine(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0),
                                        STYLE, WEIGHT)
            if tag:
                tags.append(tag)
    return tags


def add(view, obj, angle=0.0, pivot=(0.0, 0.0), move=(0.0, 0.0)):
    """Show ``obj`` (a part of the view's source) turned ``angle`` degrees
    about ``pivot`` and slid by ``move`` (view coordinates) in phantom
    lines on ``view``. Returns the number of lines drawn."""
    if abs(angle) < 1e-9 and abs(move[0]) < 1e-9 and abs(move[1]) < 1e-9:
        raise ValueError("Turn or slide the part to a new position first")
    item = {"part": obj.Name, "angle": float(angle), "pivot": [float(pivot[0]), float(pivot[1])],
            "move": [float(move[0]), float(move[1])], "tags": []}
    item["tags"] = _draw(view, item)
    items = entries(view)
    items.append(item)
    _store(view, items)
    view.touch()
    return len(item["tags"])


def refresh(view):
    """Draw a view's alternate positions again (after the model changed)."""
    items = entries(view)
    if not items:
        return 0
    n = 0
    for item in items:
        _clear(view, item)
        try:
            item["tags"] = _draw(view, item)
        except Exception as e:
            App.Console.PrintWarning("DrawingKit alternate position: %s\n" % e)
        n += len(item["tags"])
    _store(view, items)
    view.touch()
    return n


def refresh_page(page):
    return sum(refresh(v) for v in core.part_views(page) if getattr(v, PROP, ""))


def remove(view, index=None):
    """Remove one (``index``) or all of a view's alternate positions."""
    items = entries(view)
    keep = []
    for i, item in enumerate(items):
        if index is None or i == index:
            _clear(view, item)
        else:
            keep.append(item)
    _store(view, keep)
    view.touch()
    return len(items) - len(keep)
