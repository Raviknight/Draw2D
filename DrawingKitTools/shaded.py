# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shaded isometric view: a picture of the part or assembly, each part in
its own colour with its sharp edges drawn over it, placed on the sheet as
a TechDraw image view (reference only, not to scale).

It is painted without OpenGL: the parts are tessellated, seen from the
view direction and painted back to front with simple lighting; the edges
come from TechDraw's own hidden-line removal of the whole assembly, so
they match the line views. That works the same in any FreeCAD, shown on
screen or not. The settings are kept on the image so it can be painted
again after the model changes.
"""

import json
import math
import os

import FreeCAD as App

from . import core

PROP = "DrawingKitShaded"
CAPTION = "ISOMETRIC VIEW (SHADED)"
NOTE = "REFERENCE ONLY - NOT TO SCALE"
DEFAULT_COLOUR = (0.72, 0.76, 0.82)
LIGHT = (-0.35, 0.45, 0.82)          # from the viewer, a little up and left


def view_frame(direction, xdirection=None):
    """(d, right, up): d towards the viewer, right and up across the
    picture (right x up = d)."""
    d = App.Vector(direction)
    d.normalize()
    if xdirection is not None:
        r = App.Vector(xdirection)
        r = r - d * r.dot(d)
    else:
        r = App.Vector(0, 0, 1).cross(d)
    if r.Length < 1e-9:
        r = App.Vector(1, 0, 0) - d * d.x
    r.normalize()
    up = d.cross(r)
    return d, r, up


def iso_direction(page=None):
    """(direction, xdirection) of the isometric view: the sheet's own
    projection group's, if there is one, so it matches the drawing."""
    if page is not None:
        try:
            group, _ = core.page_content(page)
        except Exception:
            group = None
        if group is not None:
            for item in group.Views:
                if getattr(item, "Type", "") == "FrontTopRight":
                    return App.Vector(item.Direction), App.Vector(item.XDirection)
            try:
                a = group.Anchor
                return core.projected_directions(App.Vector(a.Direction), App.Vector(a.XDirection),
                                                 core._GRID["FrontTopRight"])
            except Exception:
                pass
    return core.STANDARD_DIRECTIONS["FrontTopRight"]


def part_colour(obj):
    """The part's colour as shown in the 3D view (r, g, b in 0..1)."""
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return DEFAULT_COLOUR
    try:
        app = vo.ShapeAppearance
        c = app[0].DiffuseColor if app else None
        if c:
            return tuple(c[:3])
    except Exception:
        pass
    try:
        return tuple(vo.ShapeColor[:3])
    except Exception:
        return DEFAULT_COLOUR


def _parts(objs):
    from . import drafting
    parts = drafting._leaf_parts(list(objs))
    return parts or [o for o in objs if core.shape_of(o) is not None]


def triangles(objs, d, right, up, tolerance=None):
    """[(depth, ((x, y, z), (x, y, z), (x, y, z)), (r, g, b))] lit and
    projected: x, y across the picture, z towards the viewer (model mm)."""
    import Part
    out = []
    parts = _parts(objs)
    if not parts:
        raise ValueError("Nothing with a 3D shape to show")
    size = core.source_shape(parts).BoundBox.DiagonalLength or 1.0
    tol = tolerance or size / 400.0
    L = App.Vector(*LIGHT)
    L.normalize()
    # the light is set relative to the picture: turn it into model space
    light = right * L.x + up * L.y + d * L.z
    for obj in parts:
        shape = core.shape_of(obj)
        if shape is None or shape.isNull():
            continue
        r, g, b = part_colour(obj)
        for face in shape.Faces:
            try:
                pts, tris = face.tessellate(tol)
            except Exception:
                continue
            for t in tris:
                p0, p1, p2 = pts[t[0]], pts[t[1]], pts[t[2]]
                n = (p1 - p0).cross(p2 - p0)
                if n.Length < 1e-12:
                    continue
                n.normalize()
                facing = n.dot(d)
                if facing < 0:
                    # lit as seen: the side towards the viewer (faces turned
                    # away are farther and get painted over)
                    n, facing = -n, -facing
                k = 0.32 + 0.68 * max(0.0, n.dot(light))
                k = min(1.0, k + 0.08 * facing)
                depth = (p0.dot(d) + p1.dot(d) + p2.dot(d)) / 3.0
                tri = tuple((p.dot(right), p.dot(up), p.dot(d)) for p in (p0, p1, p2))
                out.append((depth, tri, (r * k, g * k, b * k)))
    out.sort(key=lambda t: t[0])                   # far first
    return out


def _zbuffer(tris, x0, y0, w, h, W, H, QtGui, QtCore, ss=2):
    """Paint the triangles with a depth buffer (each pixel shows the
    nearest surface) at ``ss`` times the size, then smooth it down to
    W x H. Returns a QImage, see-through where there is no part."""
    import numpy as np
    W2, H2 = W * ss, H * ss
    k = W2 / w
    zb = np.full((H2, W2), -np.inf)
    col = np.zeros((H2, W2, 3), dtype=np.float32)
    eps = -1e-7
    for _depth, tri, (r, g, b) in tris:
        xs = [(q[0] - x0) * k for q in tri]
        ys = [(y0 + h - q[1]) * k for q in tri]
        zs = [q[2] for q in tri]
        xa, xb = max(int(math.floor(min(xs))), 0), min(int(math.ceil(max(xs))), W2 - 1)
        ya, yb = max(int(math.floor(min(ys))), 0), min(int(math.ceil(max(ys))), H2 - 1)
        if xa > xb or ya > yb:
            continue
        x1, x2, x3 = xs
        y1, y2, y3 = ys
        den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(xa, xb + 1) + 0.5, np.arange(ya, yb + 1) + 0.5)
        a = ((y2 - y3) * (gx - x3) + (x3 - x2) * (gy - y3)) / den
        bb = ((y3 - y1) * (gx - x3) + (x1 - x3) * (gy - y3)) / den
        c = 1.0 - a - bb
        inside = (a >= eps) & (bb >= eps) & (c >= eps)
        if not inside.any():
            continue
        z = a * zs[0] + bb * zs[1] + c * zs[2]
        zsub = zb[ya:yb + 1, xa:xb + 1]
        upd = inside & (z > zsub)
        zsub[upd] = z[upd]
        csub = col[ya:yb + 1, xa:xb + 1]
        csub[upd] = (min(r, 1.0), min(g, 1.0), min(b, 1.0))
    rgba = np.zeros((H2, W2, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(col * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(zb), 255, 0).astype(np.uint8)
    buf = rgba.tobytes()
    big = QtGui.QImage(buf, W2, H2, W2 * 4, QtGui.QImage.Format_RGBA8888).copy()
    small = big.scaled(W, H, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    return small.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied)


def edges(objs, d, right, up, size):
    """Visible sharp and outline edges of all the parts together, as
    polylines across the picture (model mm)."""
    import Part
    import TechDraw
    shapes = [s for s in (core.shape_of(o) for o in _parts(objs)) if s is not None]
    comp = Part.makeCompound([s.copy() for s in shapes])
    m = App.Matrix(right.x, right.y, right.z, 0, up.x, up.y, up.z, 0, d.x, d.y, d.z, 0, 0, 0, 0, 1)
    comp.transformShape(m)
    res = TechDraw.projectEx(comp, App.Vector(0, 0, 1))
    polys = []
    defl = size / 600.0
    for i in (0, 3):                    # visible sharp edges and outlines
        try:
            es = res[i].Edges
        except Exception:
            continue
        for e in es:
            try:
                if e.Curve.TypeId in ("Part::GeomLine", "Part::GeomLineSegment"):
                    pts = [e.Vertexes[0].Point, e.Vertexes[-1].Point]
                else:
                    pts = e.discretize(Deflection=defl)
            except Exception:
                continue
            if len(pts) >= 2:
                polys.append([(p.x, p.y) for p in pts])
    return polys


def render(objs, path, direction, xdirection=None, width_px=1600, with_edges=True):
    """Paint the shaded picture to ``path`` (PNG). Returns its size in
    pixels (W, H)."""
    try:
        from PySide import QtCore, QtGui
    except ImportError:
        raise ValueError("The shaded view needs FreeCAD's GUI libraries")
    d, right, up = view_frame(direction, xdirection)
    tris = triangles(objs, d, right, up)
    if not tris:
        raise ValueError("Nothing to show from this direction")
    xs = [p[0] for _, t, _ in tris for p in t]
    ys = [p[1] for _, t, _ in tris for p in t]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    margin = 0.03 * max(w, h)
    x0, y0, w, h = x0 - margin, y0 - margin, w + 2 * margin, h + 2 * margin
    k = width_px / w
    W, H = int(round(width_px)), max(1, int(round(h * k)))
    if QtGui.QGuiApplication.instance() is None:
        raise ValueError("The shaded view needs FreeCAD's GUI")
    img = _zbuffer(tris, x0, y0, w, h, W, H, QtGui, QtCore)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def pt(x, y):
        return QtCore.QPointF((x - x0) * k, (y0 + h - y) * k)

    if with_edges:
        pen = QtGui.QPen(QtGui.QColor(25, 25, 25), max(1.2, width_px / 900.0))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        for poly in edges(objs, d, right, up, max(w, h)):
            path_ = QtGui.QPainterPath(pt(*poly[0]))
            for q in poly[1:]:
                path_.lineTo(pt(*q))
            p.drawPath(path_)
    p.end()
    if not img.save(path, "PNG"):
        raise ValueError("Could not write %s" % path)
    return W, H


def image_dir():
    return os.path.join(App.getUserAppDataDir(), "DrawingKit", "Images")


def _image_path(doc, name):
    os.makedirs(image_dir(), exist_ok=True)
    return os.path.join(image_dir(), "%s_%s.png" % (doc.Name, name))


def settings(img):
    try:
        return json.loads(getattr(img, PROP, "") or "{}")
    except ValueError:
        return {}


def _paint(img, s):
    doc = img.Document
    objs = [doc.getObject(n) for n in s.get("sources", [])]
    objs = [o for o in objs if o is not None]
    if not objs:
        raise ValueError("The parts this view shows are gone")
    path = _image_path(doc, img.Name)
    W, H = render(objs, path, App.Vector(*s["direction"]),
                  App.Vector(*s["xdirection"]) if s.get("xdirection") else None,
                  int(s.get("pixels", 1600)), s.get("edges", True))
    img.ImageFile = path
    # TechDraw shows an image at 0.1 mm a pixel; its scale sets the size
    # (Width / Height only crop it, so they get the whole picture)
    native_w, native_h = W / PX_PER_MM, H / PX_PER_MM
    img.Width, img.Height = native_w, native_h
    img.ScaleType = "Custom"
    img.Scale = float(s.get("width", 90.0)) / native_w
    _place_caption(img, s)
    return img


CAPTION_SIZE = 3.0


def _place_caption(img, s):
    """The caption (a DrawingKit note: two centred lines) just under the
    picture, attached to it so it moves with it."""
    from . import annot
    page = None
    try:
        page = img.findParentPage()
    except Exception:
        pass
    if page is None:
        for o in img.InList:
            if o.isDerivedFrom("TechDraw::DrawPage"):
                page = o
    if page is None:
        return None
    k = float(img.Scale)
    h = float(img.Height) * k
    gap = 2.0 + CAPTION_SIZE * 1.3          # under the picture, first line's height
    local = (0.0, -(h / 2.0 + gap) / k)
    doc = img.Document
    note = doc.getObject(s.get("caption", "")) if s.get("caption") else None
    if note is None or not note.isValid():
        p = {"lines": [CAPTION, NOTE], "tsize": CAPTION_SIZE, "noleader": True, "align": "centre"}
        note = annot._new(page, "note", "Shaded view caption", p, "view", img, local, (0.0, 0.0))
        s["caption"] = note.Name
        setattr(img, PROP, json.dumps(s))
    else:
        setattr(note, annot.ANCHOR, App.Vector(local[0], local[1], 0))
        annot.render(note)
    return note


PX_PER_MM = 10.0      # TechDraw draws image pixels at this many to the mm


def size_on_sheet(img):
    """(width, height) mm the picture takes on the sheet."""
    k = float(img.Scale)
    return float(img.Width) * k, float(img.Height) * k


def add(page, objs, width=90.0, place=None, direction=None, xdirection=None, pixels=1600):
    """Shaded isometric picture of ``objs`` on ``page``, ``width`` mm wide,
    centred on page point ``place`` (else the first free spot). Returns
    the image view."""
    doc = page.Document
    if direction is None:
        direction, xdirection = iso_direction(page)
    img = doc.addObject("TechDraw::DrawViewImage", "ShadedView")
    page.addView(img)
    img.addProperty("App::PropertyString", PROP, "DrawingKit",
                    "What DrawingKit's shaded view shows (to paint it again)")
    img.setEditorMode(PROP, 2)
    s = {"sources": [o.Name for o in objs], "direction": list(direction),
         "xdirection": list(xdirection) if xdirection is not None else None,
         "width": float(width), "pixels": int(pixels), "edges": True}
    setattr(img, PROP, json.dumps(s))
    img.Label = "Shaded isometric view"
    img.Caption = ""                 # DrawingKit's own caption note goes under it
    _paint(img, s)
    w, h = size_on_sheet(img)
    extra = 4.0 + CAPTION_SIZE * 3.6    # the caption's two lines under the picture
    if place is None:
        from . import tables
        cx, cy = tables.free_table_spot(page, w, h + extra)
        place = (cx, cy + extra / 2.0)
    img.X, img.Y = float(place[0]), float(place[1])
    s = settings(img)
    _place_caption(img, s)          # follow the picture to its spot
    return img


def is_shaded(obj):
    return obj is not None and obj.isDerivedFrom("TechDraw::DrawViewImage") and \
        bool(getattr(obj, PROP, ""))


def update(img, width=None):
    """Paint a shaded view again (after the model changed), optionally at a
    new width on the sheet."""
    s = settings(img)
    if width:
        s["width"] = float(width)
        setattr(img, PROP, json.dumps(s))
    _paint(img, s)
    img.touch()
    return img


def update_page(page):
    return [update(v) for v in page.Views if is_shaded(v)]
