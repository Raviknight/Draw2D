# SPDX-License-Identifier: LGPL-2.1-or-later
"""More Onshape-style drafting tools:

* ordinate, baseline and chain dimensions from one zero point
* centerlines between two edges, chamfer callouts
* surface texture (ISO 1302 / ISO 21920) and weld (ISO 2553) symbols
* balloons and a parts list (bill of materials) for assemblies

Ordinate dimensions, surface and weld symbols are DrawingKit annotations
(see annot.py): they stay attached to their view and redraw when it moves.
Baseline/chain dimensions, centerlines and balloons are TechDraw's own
objects, so they behave like any other TechDraw item.
"""

import math
import re

import FreeCAD as App

from . import annot
from . import core
from . import smartdim

GAP = 1.5          # gap between a feature and its extension line (mm on paper)
ROW = 8.0          # first dimension row from the view's edge
STEP = 7.0         # spacing of stacked rows


# ---------------------------------------------------------------------------
# Picks -> points
# ---------------------------------------------------------------------------
def ent_point(ent, near=None):
    """A point for a picked thing: the point itself, a circle's centre, or
    the end of a line nearer ``near`` (local)."""
    if ent.kind in ("vertex", "centre", "circle", "arc"):
        return ent.point
    a, b = ent.pts
    if near is None:
        return a
    return a if math.hypot(a[0] - near[0], a[1] - near[1]) <= \
        math.hypot(b[0] - near[0], b[1] - near[1]) else b


def _vertex_name(view, ent, near=None):
    if ent.kind == "vertex":
        return "Vertex%d" % ent.index
    return smartdim.vertex_at(view, ent_point(ent, near))


# ---------------------------------------------------------------------------
# Baseline and chain dimensions (TechDraw dimensions)
# ---------------------------------------------------------------------------
def add_baseline(page, view, origin, features, axis="x", chain=False):
    """Horizontal (axis 'x') or vertical ('y') dimensions from ``origin`` to
    each feature (baseline, stacked rows) or between neighbours (chain, one
    row). Picks are smartdim.Ent. Returns the dimensions."""
    i = 0 if axis == "x" else 1
    o = ent_point(origin)
    pts = sorted(((ent_point(f, o), f) for f in features), key=lambda t: abs(t[0][i] - o[i]))
    pts = [(p, f) for p, f in pts if abs(p[i] - o[i]) > 1e-6]
    if not pts:
        raise ValueError("Pick features away from the zero point")
    doc = page.Document
    x0, y0, x1, y1 = core.view_box(view)
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    side = 1 if pts[-1][0][i] >= o[i] else -1
    names = [_vertex_name(view, origin)] + [_vertex_name(view, f, o) for _, f in pts]
    coords = [o] + [p for p, _ in pts]
    dims = []
    for n in range(1, len(coords)):
        a = 0 if not chain else n - 1
        dim = doc.addObject("TechDraw::DrawViewDimension", "Dimension")
        dim.Type = "DistanceX" if axis == "x" else "DistanceY"
        dim.MeasureType = "Projected"
        dim.References2D = [(view, [names[a], names[n]])]
        page.addView(dim)
        row = 0 if chain else n - 1
        mid = (coords[a][i] + coords[n][i]) / 2.0 * s
        if axis == "x":
            dim.X, dim.Y = mid, (y1 - cy) + ROW + row * STEP
        else:
            dim.X, dim.Y = (x1 - cx) + ROW + row * STEP, mid
        dims.append(dim)
    return dims


# ---------------------------------------------------------------------------
# Ordinate dimensions (one annotation per set)
# ---------------------------------------------------------------------------
def add_ordinate(page, view, origin, features, axis="x"):
    """Ordinate dimensions: '0' at the zero point and each feature's distance
    from it, on extension lines that end in one row beside the view."""
    if not features:
        raise ValueError("Pick at least one feature")
    # lines: use the end nearer the row (top for X ordinates, right for Y)
    far = (0.0, 1e9) if axis == "x" else (1e9, 0.0)
    o = ent_point(origin, far)
    pts = [list(ent_point(f, far)) for f in features]
    radii = [f.radius if f.kind in ("circle", "arc") else 0.0 for f in features]
    x0, y0, x1, y1 = core.view_box(view)
    ox, oy = annot.view_point(view, o)
    # row position, kept as the annotation's offset (drag it to move the row)
    off = (0.0, (y1 - oy) + ROW) if axis == "x" else ((x1 - ox) + ROW, 0.0)
    p = {"axis": axis, "pts": pts, "radii": radii, "tsize": annot.default_size()}
    return annot._new(page, "ordinate", "Ordinate %s" % axis.upper(), p, "view", view, o, off)


def _fmt(v):
    s = "%.2f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


def draw_ordinate(sk, view, origin_page, box, p):
    """Extension lines from each point to the row, values at their ends;
    texts that would collide are spread out with a short jog."""
    axis = p.get("axis", "x")
    i = 0 if axis == "x" else 1
    s = core.view_scale(view)
    pts = p.get("pts") or []
    size = 3.5 * sk.k
    ox, oy = origin_page
    radii = p.get("radii") or [0.0] * len(pts)
    items = [(0.0, (ox, oy), 0.0)]
    for q, r in zip(pts, radii):
        qp = annot.view_point(view, q)
        items.append(((qp[i] - origin_page[i]) / s, qp, r * s))
    items.sort(key=lambda t: t[1][i])
    # spread value positions so neighbours are at least 1.4 text heights apart
    want = [t[1][i] for t in items]
    pos = list(want)
    for n in range(1, len(pos)):
        pos[n] = max(pos[n], pos[n - 1] + size * 1.4)
    shift = (sum(want) - sum(pos)) / len(pos) if pos else 0.0
    pos = [v + shift for v in pos]
    for n in range(1, len(pos)):
        pos[n] = max(pos[n], pos[n - 1] + size * 1.4)
    for (val, (px, py), rad), at in zip(items, pos):
        txt = _fmt(abs(val))
        if axis == "x":
            row = box[1]
            jog = row - 2.5 * sk.k
            start = (px, py + rad + GAP)     # just off a hole's edge, not through it
            if abs(at - px) > 1e-6:
                sk.line(start, (px, jog - 2.0 * sk.k), 0.25)
                sk.line((px, jog - 2.0 * sk.k), (at, jog), 0.25)
                sk.line((at, jog), (at, row), 0.25)
            else:
                sk.line(start, (px, row), 0.25)
            sk.text(at, row + 0.8 * sk.k, txt, anchor="start", rot=90)
        else:
            col = box[0]
            jog = col - 2.5 * sk.k
            start = (px + rad + GAP, py)
            if abs(at - py) > 1e-6:
                sk.line(start, (jog - 2.0 * sk.k, py), 0.25)
                sk.line((jog - 2.0 * sk.k, py), (jog, at), 0.25)
                sk.line((jog, at), (col, at), 0.25)
            else:
                sk.line(start, (col, py), 0.25)
            sk.text(col + 0.8 * sk.k, at, txt, anchor="start")
    # zero point mark: small circle
    sk.circle((ox, oy), 0.8 * sk.k, 0.25)
    return [t[0] for t in items]


# ---------------------------------------------------------------------------
# Centerlines and chamfers
# ---------------------------------------------------------------------------
def add_centerline(view, a, b):
    """Centerline between two lines (mode 1) or two points (mode 2), TechDraw's
    own cosmetic centerline. Returns its tag."""
    if a.kind == "line" and b.kind == "line":
        names, mode = ["Edge%d" % a.index, "Edge%d" % b.index], 1
    else:
        names, mode = [_vertex_name(view, a), _vertex_name(view, b)], 2
    tag = view.makeCenterLine(names, mode)
    view.recompute()
    return tag


def chamfer_text(ent, style="iso"):
    """'1 x 45°' (or 'C1' for ISO 13715 short form) from a slanted line."""
    (ax, ay), (bx, by) = ent.pts
    dx, dy = abs(bx - ax), abs(by - ay)
    if dx < 1e-6 or dy < 1e-6:
        raise ValueError("Pick the slanted chamfer edge")
    ang = math.degrees(math.atan2(dy, dx))
    leg = min(dx, dy)
    if abs(ang - 45.0) < 0.5:
        return ("C%s" % _fmt(leg)) if style == "c" else "%s x 45°" % _fmt(leg)
    # leg along the longer side and its angle
    return "%s x %s°" % (_fmt(max(dx, dy)), _fmt(min(ang, 90 - ang)))


# ---------------------------------------------------------------------------
# Surface texture symbol (ISO 1302 / ISO 21920-1)
# ---------------------------------------------------------------------------
SURF_PROCESS = [("any", "√ Any process"), ("remove", "▽ Material removal required"),
                ("noremove", "⊙ Material removal not allowed")]
LAYS = ["", "=", "⊥", "X", "M", "C", "R", "P"]


def add_surface(page, x, y, params, box=None):
    """Surface symbol on the edge at page point (x, y)."""
    kind, view, local, vbox = annot.attach_point(page, x, y)
    sdir = (0.0, 1.0)
    if kind == "view":
        hit = annot.nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local, (nx, ny) = hit
            x, y = annot.view_point(view, local)
            cx, cy = core.view_center(view)
            if nx * (x - cx) + ny * (y - cy) < 0:
                nx, ny = -nx, -ny
            sdir = (nx, ny)
    p = dict(params)
    p["dir"] = [sdir[0], sdir[1]]
    if box is None:
        # sitting right on a top surface; otherwise on a leader out from it
        on_top = sdir[1] > 0.9
        off = (0.0, 0.0) if on_top else (sdir[0] * 12.0 + 4.0, sdir[1] * 12.0 + 6.0)
        avoid = "on-surface" if on_top else "default"
    else:
        off = (box[0] - x, box[1] - y)
        avoid = "explicit"
    return annot._new(page, "surface", "Surface %s" % p.get("ra", ""), p, kind, view,
                      local if kind == "view" else (x, y), off, avoid=avoid)


def draw_surface(sk, anchor, box, p, values):
    """Upright √ symbol with its point at ``box``; a leader with an arrow to
    the surface when the symbol isn't sitting on it."""
    k = sk.k
    h1 = 5.0 * k                       # ISO 1302: H1 = 1.4 h, H2 = 2 H1 (h = 3.5)
    h2 = 2.0 * h1
    t = math.tan(math.radians(30))
    bx, by = box
    left = (bx - h1 * t, by + h1)
    right = (bx + h2 * t, by + h2)
    sk.line(left, (bx, by))
    sk.line((bx, by), right)
    proc = p.get("process", "any")
    if proc == "remove":
        sk.line(left, (bx + h1 * t, by + h1))
    elif proc == "noremove":
        r = h1 * t * 0.75
        sk.circle((bx, by + h1 * 0.55), r)
    ra = values.get("ra", p.get("ra", ""))
    method = values.get("method", p.get("method", ""))
    w = max(len(ra), len(method), 3) * 2.2 * k + 2.0 * k
    sk.body = (left[0], by, right[0] + w, right[1] + (4.5 * k if method else 1.0 * k))
    if ra or method or p.get("all_around"):
        sk.line(right, (right[0] + w, right[1]))
    if p.get("all_around"):
        sk.circle(right, 0.9 * k)
    if method:
        sk.text(right[0] + 1.0 * k, right[1] + 2.4 * k, method, anchor="start", editable="method")
    if ra:
        sk.text(right[0] + 1.0 * k, right[1] - 2.4 * k, ra, anchor="start", editable="ra")
    lay = p.get("lay", "")
    if lay:
        sk.text(bx + h2 * t * 0.75 + 1.5 * k, by + 1.2 * k, lay, anchor="start", size=3.0)
    ax, ay = anchor
    if math.hypot(bx - ax, by - ay) > 0.5:
        sk.line((bx, by), anchor)
        annot.draw_arrow(sk, anchor, (bx, by))


# ---------------------------------------------------------------------------
# Weld symbol (ISO 2553, system A: with the dashed identification line)
# ---------------------------------------------------------------------------
WELDS = [("fillet", "◺ Fillet"), ("v", "V  V-butt"), ("square", "‖ Square butt"),
         ("bevel", "⊾ Bevel butt"), ("plug", "⊓ Plug / slot"), ("spot", "○ Spot"),
         ("edge", "∥ Edge / flare")]


def add_weld(page, x, y, params, box=None):
    kind, view, local, vbox = annot.attach_point(page, x, y)
    if kind == "view":
        hit = annot.nearest_edge(view, local, max_dist=4.0 / core.view_scale(view))
        if hit is not None:
            local = hit[0]
            x, y = annot.view_point(view, local)
    off = (15.0, 12.0) if box is None else (box[0] - x, box[1] - y)
    p = dict(params)
    return annot._new(page, "weld", "Weld %s %s" % (p.get("type", ""), p.get("size", "")), p,
                      kind, view, local if kind == "view" else (x, y), off,
                      avoid="default" if box is None else "explicit")


def _weld_glyph(sk, kind, cx, base, up, k):
    """Weld symbol standing on the reference line at x = cx; ``up`` = +1 for
    the other side (above), -1 for the arrow side (below)."""
    h = 3.0 * k * up
    if kind == "fillet":
        sk.poly([(cx - 1.5 * k, base), (cx - 1.5 * k, base + h), (cx + 1.5 * k, base)])
    elif kind == "v":
        sk.line((cx - 1.8 * k, base + h), (cx, base))
        sk.line((cx, base), (cx + 1.8 * k, base + h))
    elif kind == "square":
        sk.line((cx - 0.8 * k, base), (cx - 0.8 * k, base + h))
        sk.line((cx + 0.8 * k, base), (cx + 0.8 * k, base + h))
    elif kind == "bevel":
        sk.line((cx - 1.2 * k, base), (cx - 1.2 * k, base + h))
        sk.line((cx - 1.2 * k, base), (cx + 1.6 * k, base + h))
    elif kind == "plug":
        sk.poly([(cx - 2 * k, base), (cx - 2 * k, base + h), (cx + 2 * k, base + h), (cx + 2 * k, base)])
    elif kind == "spot":
        sk.circle((cx, base), 1.6 * k)
    else:   # edge
        sk.line((cx - 0.8 * k, base), (cx - 0.8 * k, base + h))
        sk.line((cx + 0.8 * k, base), (cx + 0.8 * k, base + h))
        sk.line((cx - 0.8 * k, base + h), (cx + 0.8 * k, base + h))


def draw_weld(sk, anchor, box, p, values):
    """Arrow line from the joint to the reference line; symbol, size and
    length on the arrow side (below) or other side (above); ISO dashed
    identification line; all-around circle and field-weld flag."""
    k = sk.k
    ax, ay = anchor
    bx, by = box
    L = 22.0 * k
    right = ax <= bx                   # reference line runs away from the joint
    x_end = bx + L if right else bx - L
    sk.body = (min(bx, x_end), by - 5.0 * k, max(bx, x_end), by + 6.0 * k)
    sk.line((bx, by), (x_end, by))
    sk.line((bx, by), anchor)
    annot.draw_arrow(sk, anchor, (bx, by))
    side = p.get("side", "arrow")
    cx = bx + (L * 0.45 if right else -L * 0.45)
    if p.get("iso_line", True) and side != "both":
        # dashed line on the side the symbol is NOT on (ISO 2553 system A)
        dy = 1.6 * k if side == "arrow" else -1.6 * k
        sk.line((bx + (2 * k if right else -2 * k), by + dy), (x_end, by + dy), 0.25, dash=True)
    ups = {"arrow": [-1], "other": [1], "both": [-1, 1]}[side]
    kind = p.get("type", "fillet")
    size = values.get("size", p.get("size", ""))
    length = values.get("length", p.get("length", ""))
    for u in ups:
        _weld_glyph(sk, kind, cx, by, u, k)
        ty = by + u * 2.0 * k
        if size:
            sk.text(cx - 3.0 * k, ty, size, anchor="end" if False else "middle",
                    editable="size" if u == ups[0] else None)
        if length:
            sk.text(cx + 3.6 * k, ty, length, anchor="start",
                    editable="length" if u == ups[0] else None)
    if p.get("all_around"):
        sk.circle((bx, by), 1.3 * k)
    if p.get("field"):
        sk.line((bx, by), (bx, by + 5.0 * k))
        sk.poly([(bx, by + 5.0 * k), (bx + (3 if right else -3) * k, by + 4.0 * k), (bx, by + 3.0 * k)],
                fill=True)
    process = values.get("process", p.get("process", ""))
    if process:
        tx = x_end + (1.0 if right else -1.0) * k
        sk.line((x_end, by), (tx + (2 if right else -2) * k, by + 2 * k))
        sk.line((x_end, by), (tx + (2 if right else -2) * k, by - 2 * k))
        sk.text(tx + (3 if right else -3) * k, by, process, anchor="start" if right else "middle",
                editable="process")


# ---------------------------------------------------------------------------
# Parts list and balloons
# ---------------------------------------------------------------------------
def _leaf_parts(objs, out=None):
    """Solid parts under the given objects (assemblies, App::Part, links)."""
    out = [] if out is None else out
    for o in objs:
        group = getattr(o, "Group", None)
        if o.isDerivedFrom("App::Part") or (group and not hasattr(o, "Tip")
                                             and o.TypeId not in ("PartDesign::Body",)):
            # every child counts, shown or not (a file opened without the GUI
            # reports them all hidden) - except shapes another one is built
            # from, like the inputs of a boolean cut
            _leaf_parts([g for g in (group or []) if not _consumed(g)], out)
            continue
        shp = core.shape_of(o)
        if shp is None or not shp.Solids:
            continue
        out.append(o)
    return out


def _consumed(o):
    for parent in getattr(o, "InList", []):
        if parent.isDerivedFrom("Part::Feature") and not parent.isDerivedFrom("PartDesign::Body"):
            return True
    return False


def _base_name(label):
    return re.sub(r"[\s_-]*\d+$", "", label) or label


def _signature(o):
    s = core.shape_of(o)
    bb = s.BoundBox
    dims = sorted([round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)])
    src = o.getLinkedObject(True) if hasattr(o, "getLinkedObject") else o
    return (src.Name if src is not o else _base_name(o.Label), round(s.Volume, 3), tuple(dims))


def bom_items(objs):
    """[{'item', 'name', 'qty', 'material', 'objects'}] - identical parts
    (same source or same name and shape) counted together."""
    groups = {}
    order = []
    for o in _leaf_parts(objs):
        key = _signature(o)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(o)
    out = []
    for n, key in enumerate(order):
        objs_ = groups[key]
        first = objs_[0]
        src = first.getLinkedObject(True) if hasattr(first, "getLinkedObject") else first
        out.append({"item": n + 1, "name": _base_name(src.Label), "qty": len(objs_),
                    "material": core.part_material(src) or "", "objects": objs_})
    return out


def add_parts_list(page, view, place=None):
    """Parts list table above the title block (or above a hole table there)."""
    from . import tables
    items = bom_items(view.Source)
    if not items:
        raise ValueError("No solid parts found in this view")
    p = {"title": "PARTS LIST", "headers": ["ITEM", "QTY", "PART", "MATERIAL"],
         "widths": [12.0, 12.0, 50.0, 36.0],
         "rows": [[str(i["item"]), str(i["qty"]), i["name"], i["material"]] for i in items],
         "editable": True, "view": view.Name, "tsize": 3.5}
    W, Ht = tables.table_size(p)
    if place is None:
        frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
        top = (title or frame)[3]
        for v in page.Views:
            if getattr(v, annot.KIND, "") == "holetable":
                hw, hh = tables.table_size(annot._params(v))
                top = max(top, float(v.Y) + hh / 2.0)
        place = ((title or frame)[2] - W / 2.0, top + 3.0 + Ht / 2.0)
    return annot._new(page, "bom", "Parts list", p, "free", None, place, (0, 0)), items


def part_at(view, px, py, items=None):
    """The part under a page point (nearest the viewer), or None."""
    import Part
    shape = core.source_shape(view.Source)
    d, right, up = core.view_axes(view)
    p = core.page_to_model(view, px, py, shape)
    span = shape.BoundBox.DiagonalLength + 10.0
    ray = Part.LineSegment(p + d * span, p - d * span).toShape()
    tol = 1.5 / core.view_scale(view)
    best = None
    for it in (items or bom_items(view.Source)):
        for o in it["objects"]:
            try:
                dist, pairs, _ = core.shape_of(o).distToShape(ray)
            except Exception:
                continue
            if dist > tol or not pairs:
                continue
            depth = max(App.Vector(a).dot(d) for a, _b in pairs)
            if best is None or depth > best[0]:
                best = (depth, it, o)
    return (best[1], best[2]) if best else (None, None)


def add_balloon(page, view, origin, place, text):
    """TechDraw balloon: arrow at page point ``origin``, bubble at ``place``."""
    doc = page.Document
    b = doc.addObject("TechDraw::DrawViewBalloon", "Balloon")
    b.SourceView = view
    page.addView(b)
    # balloon positions are in the view's own (unscaled) units from its centre
    cx, cy = core.view_center(view)
    s = core.view_scale(view)
    b.OriginX, b.OriginY = (origin[0] - cx) / s, (origin[1] - cy) / s
    b.X, b.Y = (place[0] - cx) / s, (place[1] - cy) / s
    b.Text = str(text)
    try:
        b.BubbleShape = "Circular"
        b.EndType = "Dot"          # ISO 128: a leader ending inside a part ends in a dot
    except Exception:
        pass
    return b


def visible_point(view, obj, item, items):
    """A page point on ``obj`` that is visible in the view (for auto balloons)."""
    shape = core.source_shape(view.Source)
    cands = []
    oshape = core.shape_of(obj)
    bb = oshape.BoundBox
    cands.append(bb.Center)
    for f in oshape.Faces[:40]:
        try:
            cands.append(f.CenterOfMass)
        except Exception:
            pass
    for v in oshape.Vertexes[:40]:
        cands.append(v.Point)
        cands.append(bb.Center + (v.Point - bb.Center) * 0.8)   # just inside a corner
    # visible spots on the part; prefer one near the view's edge (short
    # leader) but a little inside the outline, so the dot lands on the part
    x0, y0, x1, y1 = core.view_box(view)
    good = []
    for c in cands[:60]:
        px, py = core.model_to_page(view, c, shape)
        edge = min(px - x0, x1 - px, py - y0, y1 - py)
        if edge < 0:
            continue
        it, o = part_at(view, px, py, items)
        if o is obj:
            good.append((edge if edge >= 2.0 else 1e6, (px, py)))
            # nudge points on the outline inwards towards the part's middle
    if not good:
        return None
    best = min(good)[1]
    if min(good)[0] >= 1e6:      # only outline points: move 30% towards the centre
        mx, my = core.model_to_page(view, bb.Center, shape)
        best = (best[0] + (mx - best[0]) * 0.3, best[1] + (my - best[1]) * 0.3)
    return best


def _hits_box(c, r, b):
    return b[0] - r < c[0] < b[2] + r and b[1] - r < c[1] < b[3] + r


def balloon_place(pt, box, taken, gap=12.0, spacing=10.0, avoid=(), area=None):
    """Bubble position just outside the view box, on the side nearest
    ``pt`` that has room: clear of other bubbles (``taken``), other views'
    boxes (``avoid``) and inside the drawing ``area`` if given."""
    x0, y0, x1, y1 = box
    d = {"left": pt[0] - x0, "right": x1 - pt[0], "bottom": pt[1] - y0, "top": y1 - pt[1]}
    r = spacing / 2.0

    def ok(c):
        if any(math.hypot(c[0] - t[0], c[1] - t[1]) < spacing for t in taken):
            return False
        if any(_hits_box(c, r, b) for b in avoid):
            return False
        if area is not None and not (area[0] + r < c[0] < area[2] - r and area[1] + r < c[1] < area[3] - r):
            return False
        return True

    first = None
    for side in sorted(d, key=d.get):
        if side in ("left", "right"):
            base, along = (x0 - gap if side == "left" else x1 + gap, pt[1]), (0.0, 1.0)
        else:
            base, along = (pt[0], y0 - gap if side == "bottom" else y1 + gap), (1.0, 0.0)
        first = first or base
        for n in range(0, 12):
            for sgn in ((1,) if n == 0 else (1, -1)):
                c = (base[0] + along[0] * spacing * n * sgn, base[1] + along[1] * spacing * n * sgn)
                if ok(c):
                    return c
    return first


def auto_balloons(page, view):
    """One balloon per parts-list item, just outside the view on the side
    nearest the part, kept clear of each other."""
    items = bom_items(view.Source)
    box = core.view_box(view)
    avoid = []
    for v in core.part_views(page):
        if not core.same(v, view):
            try:
                avoid.append(core.view_box(v))
            except Exception:
                pass
    for v in page.Views:          # tables and title block
        if getattr(v, annot.KIND, "") in ("bom", "holetable", "revtable"):
            from . import tables
            w, h = tables.table_size(annot._params(v))
            avoid.append((float(v.X) - w / 2, float(v.Y) - h / 2, float(v.X) + w / 2, float(v.Y) + h / 2))
    area = None
    try:
        frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
        area = frame
        if title:
            avoid.append(title)
    except Exception:
        pass
    taken = []
    plan = []
    for it in items:
        pt = None
        for o in it["objects"]:
            pt = visible_point(view, o, it, items)
            if pt:
                break
        if pt is None:
            continue
        place = balloon_place(pt, box, taken, avoid=avoid, area=area)
        taken.append(place)
        plan.append([it, pt, place])
    uncross(plan)
    return [add_balloon(page, view, pt, place, it["item"]) for it, pt, place in plan]


def _segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


def uncross(plan, rounds=10):
    """Swap bubble positions of balloons whose leaders cross (in place).
    ``plan``: [[item, point, place], ...]."""
    for _ in range(rounds):
        swapped = False
        for i in range(len(plan)):
            for j in range(i + 1, len(plan)):
                a, b = plan[i], plan[j]
                if _segments_cross(a[1], a[2], b[1], b[2]):
                    a[2], b[2] = b[2], a[2]
                    swapped = True
        if not swapped:
            break
    return plan


# ---------------------------------------------------------------------------
# Hook for annot.render
# ---------------------------------------------------------------------------
def draw(kind, sk, sym, anchor, sdir, box, p, values):
    """Draw a drafting annotation; returns its editable field names."""
    if kind == "ordinate":
        view = getattr(sym, annot.LINK)
        draw_ordinate(sk, view, anchor, box, p)
        return []
    if kind == "surface":
        draw_surface(sk, anchor, box, p, values)
        return ["method", "ra"]
    if kind == "weld":
        draw_weld(sk, anchor, box, p, values)
        return ["size", "length", "process"]
    raise ValueError("Unknown drafting kind %s" % kind)
