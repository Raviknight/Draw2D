# SPDX-License-Identifier: LGPL-2.1-or-later
"""DrawingKit core logic.

Everything in here works without the FreeCAD GUI, so it can be tested with
freecadcmd. The GUI commands in ``commands.py`` are thin wrappers around it.
"""

import os
import re
import xml.etree.ElementTree as ET

import FreeCAD as App

# ---------------------------------------------------------------------------
# Standard scales (ISO 5455). Stored as (numerator, denominator).
# ---------------------------------------------------------------------------
STANDARD_SCALES = [
    (50, 1), (20, 1), (10, 1), (5, 1), (2, 1), (1, 1),
    (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100),
    (1, 200), (1, 500), (1, 1000),
]


def scale_value(ratio):
    return ratio[0] / ratio[1]


def scale_text(value):
    """1.0 -> '1:1', 0.2 -> '1:5', 2.0 -> '2:1'."""
    if value >= 1:
        return "%g:1" % round(value, 4)
    return "1:%g" % round(1.0 / value, 4)


def pick_scale(fits):
    """Return the largest standard scale for which ``fits(scale)`` is true.

    Falls back to the smallest standard scale if nothing fits.
    """
    for ratio in STANDARD_SCALES:  # largest first
        s = scale_value(ratio)
        if fits(s):
            return s
    return scale_value(STANDARD_SCALES[-1])


def pick_scale_for_box(content_w, content_h, avail_w, avail_h, gap_w=0.0, gap_h=0.0):
    """Largest standard scale so that content (model mm) plus fixed paper gaps fits."""
    return pick_scale(lambda s: content_w * s + gap_w <= avail_w + 1e-9
                      and content_h * s + gap_h <= avail_h + 1e-9)


# ---------------------------------------------------------------------------
# Section letters
# ---------------------------------------------------------------------------
# I, O and Q are skipped: they are easily confused with 1 and 0 (ASME Y14.3).
LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOQ"]


def letter_sequence():
    """A, B, ... Z, AA, BB, ... ZZ, AAA, ..."""
    n = 1
    while True:
        for c in LETTERS:
            yield c * n
        n += 1


def next_free_letter(used):
    used = {u.strip().upper() for u in used if u}
    for letter in letter_sequence():
        if letter not in used:
            return letter


def used_letters(doc):
    """Letters already used by section and detail views in the document."""
    used = []
    for obj in doc.Objects:
        if obj.isDerivedFrom("TechDraw::DrawViewSection"):
            used.append(obj.SectionSymbol)
        elif obj.isDerivedFrom("TechDraw::DrawViewDetail"):
            used.append(obj.Reference)
    return used


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
ISO_SIZES = ["A4", "A3", "A2", "A1", "A0"]
ANSI_SIZES = ["ANSI A", "ANSI B", "ANSI C", "ANSI D", "ANSI E"]
ORIENTATIONS = ["Landscape", "Portrait"]


def template_dir():
    return os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")


def _iso_candidates(size, orient):
    # Prefer a template with a title block, then fall back to plainer ones.
    for kind in ("ISO5457_minimal", "ISO5457_advanced", "TD", "blank"):
        yield os.path.join("ISO", "%s_%s_%s.svg" % (size, orient, kind))


def _ansi_candidates(size, orient):
    letter = size.split()[-1]
    yield os.path.join("ASME", "ANSI%s_%s.svg" % (letter, orient))
    yield os.path.join("ASME", "ANSI%s_%s_blank.svg" % (letter, orient))


def available_templates(base=None):
    """Return {(size, orientation): template path} for templates FreeCAD ships."""
    base = base or template_dir()
    found = {}
    for size in ISO_SIZES + ANSI_SIZES:
        for orient in ORIENTATIONS:
            gen = _iso_candidates if size in ISO_SIZES else _ansi_candidates
            for rel in gen(size, orient):
                path = os.path.join(base, rel)
                if os.path.isfile(path):
                    found[(size, orient)] = path
                    break
    return found


# ---------------------------------------------------------------------------
# Drawing area of a template
# ---------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _num(text, default=0.0):
    m = _NUM.search(text or "")
    return float(m.group()) if m else default


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def drawing_area(template_path, page_w, page_h):
    """Return (frame, title_block) in page coordinates (mm, Y up from bottom).

    frame is (x0, y0, x1, y1). title_block is the same or None.
    Uses the ISO 5457 ids FreeCAD's templates carry ('drawing_space_frame',
    'title_block_frame'); otherwise takes the largest rectangle as the frame
    and assumes a 180 x 60 mm title block in the bottom-right corner.
    """
    frame = None
    title = None
    rects = []
    texts = []
    try:
        root = ET.parse(template_path).getroot()
        vb = root.get("viewBox")
        if vb:
            parts = [float(v) for v in vb.replace(",", " ").split()]
            sx = page_w / parts[2] if parts[2] else 1.0
            sy = page_h / parts[3] if parts[3] else 1.0
        else:
            sx = sy = 1.0
        for el in root.iter():
            if _local(el.tag) == "text" and el.get("x") and el.get("y"):
                texts.append((_num(el.get("x")) * sx, page_h - _num(el.get("y")) * sy))
            if _local(el.tag) != "rect":
                continue
            x, y = _num(el.get("x")) * sx, _num(el.get("y")) * sy
            w, h = _num(el.get("width")) * sx, _num(el.get("height")) * sy
            if w <= 0 or h <= 0 or el.get("transform"):
                continue
            # SVG y grows downwards; TechDraw page y grows upwards.
            r = (x, page_h - (y + h), x + w, page_h - y)
            rid = el.get("id", "")
            if rid == "drawing_space_frame":
                frame = r
            elif rid == "title_block_frame":
                title = r
            if w < page_w * 0.999 or h < page_h * 0.999:
                rects.append((w * h, r))
    except (ET.ParseError, OSError, ValueError, IndexError):
        pass

    if frame is None:
        if rects:
            frame = max(rects)[1]
        else:
            frame = (10.0, 10.0, page_w - 10.0, page_h - 10.0)
    if title is None:
        tw = min(180.0, frame[2] - frame[0])
        th = min(60.0, (frame[3] - frame[1]) / 3.0)
        title = (frame[2] - tw, frame[1], frame[2], frame[1] + th)
    else:
        # ISO 7200 templates put a row (material, tolerances, scale) just
        # above the title block frame. Keep views clear of it too.
        tx0, ty0, tx1, ty1 = title
        top = ty1
        for x, y in texts:
            if tx0 - 0.1 <= x <= tx1 and ty1 < y <= ty1 + 20.0:
                top = max(top, y + 7.0)
        title = (tx0, ty0, tx1, top)
    return frame, title


def free_regions(frame, title, pad=5.0):
    """Rectangles of the frame not covered by the title block, padded."""
    fx0, fy0, fx1, fy1 = frame
    regions = [(fx0, fy0, fx1, fy1)]
    if title:
        tx0, ty0, tx1, ty1 = title
        regions = [
            (fx0, ty1, fx1, fy1),  # full width, above the title block
            (fx0, fy0, tx0, fy1),  # full height, left of the title block
        ]
    out = []
    for x0, y0, x1, y1 in regions:
        x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1, y1))
    return out


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def create_page(doc, size, orientation, templates=None, drawingkit_template=True):
    """New page of the given paper size.

    Uses DrawingKit's own frame and title block (any size, both orientations,
    projection symbol to match the page). With ``drawingkit_template=False``, or
    if that fails, FreeCAD's bundled template for the size is used.
    """
    page = doc.addObject("TechDraw::DrawPage", "Page")
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    path = None
    if drawingkit_template:
        try:
            from . import templates as d2t
            path = d2t.template_path(size, orientation, page_first_angle(page))
        except Exception as e:
            App.Console.PrintWarning("DrawingKit template failed, using FreeCAD's: %s\n" % e)
    if path is None:
        templates = templates or available_templates()
        if (size, orientation) not in templates:
            doc.removeObject(tpl.Name)
            doc.removeObject(page.Name)
            raise ValueError("No template for %s %s" % (size, orientation))
        path = templates[(size, orientation)]
    tpl.Template = path
    page.Template = tpl
    page.Label = "Sheet %s" % size
    page.Scale = 1.0
    return page


def page_size(page):
    tpl = page.Template
    return float(tpl.Width), float(tpl.Height)


def page_regions(page):
    w, h = page_size(page)
    path = getattr(page.Template, "Template", "") or ""
    frame, title = drawing_area(path, w, h)
    return free_regions(frame, title)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def source_shape(objs):
    import Part
    shapes = [o.Shape for o in objs if hasattr(o, "Shape") and not o.Shape.isNull()]
    if not shapes:
        raise ValueError("Nothing with a 3D shape selected")
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def projected_extent(shape, direction, xdirection):
    """(width, height) of the shape seen along ``direction`` (model mm)."""
    d = App.Vector(direction)
    d.normalize()
    x = App.Vector(xdirection)
    x = x - d * x.dot(d)  # make sure X is perpendicular to the view direction
    if x.Length < 1e-9:
        x = App.Vector(1, 0, 0) if abs(d.x) < 0.9 else App.Vector(0, 1, 0)
        x = x - d * x.dot(d)
    x.normalize()
    y = d.cross(x)
    m = App.Matrix(x.x, x.y, x.z, 0,
                   y.x, y.y, y.z, 0,
                   d.x, d.y, d.z, 0,
                   0, 0, 0, 1)
    s = shape.copy()
    s.transformShape(m)
    try:
        bb = s.optimalBoundingBox()
    except Exception:
        bb = s.BoundBox
    return bb.XLength, bb.YLength


# ---------------------------------------------------------------------------
# Projection group layout
# ---------------------------------------------------------------------------
# Grid cell of each projection in third-angle; first angle mirrors it.
_GRID = {
    "Front": (0, 0), "Top": (0, 1), "Bottom": (0, -1), "Right": (1, 0),
    "Left": (-1, 0), "Rear": (2, 0),
    "FrontTopRight": (1, 1), "FrontTopLeft": (-1, 1),
    "FrontBottomRight": (1, -1), "FrontBottomLeft": (-1, -1),
}


def is_first_angle(group):
    """True for first-angle projection (FreeCAD spells it 'First angle')."""
    ptype = group.ProjectionType
    if ptype == "Default":
        page = group.findParentPage()
        ptype = page.ProjectionType if page else "First angle"
    return ptype.lower().startswith("first")


# Directions TechDraw gives the standard projections when Front looks along -Y
# (checked against FreeCAD 1.1: see tests).
STANDARD_DIRECTIONS = {
    "Front": (App.Vector(0, -1, 0), App.Vector(1, 0, 0)),
    "Top": (App.Vector(0, 0, 1), App.Vector(1, 0, 0)),
    "Right": (App.Vector(1, 0, 0), App.Vector(0, 1, 0)),
    "FrontTopRight": (App.Vector(1, -1, 1), App.Vector(1, 1, 0)),
}


def group_layout(group, shape=None, specs=None):
    """Describe a projection group's size in a scale-independent way.

    ``specs`` is a list of (name, type, direction, xdirection); by default it
    comes from the group's existing views. Returns a dict with col/row model
    sizes, gaps and each view's grid cell, for ``group_box``,
    ``anchor_offset`` and ``item_offsets``.
    """
    shape = shape or source_shape(group.Source)
    first = is_first_angle(group)
    if specs is None:
        specs = [(v.Name, v.Type, v.Direction, v.XDirection) for v in group.Views]
    cols, rows, cells = {}, {}, {}
    for name, vtype, direction, xdirection in specs:
        cell = _GRID.get(vtype)
        if cell is None:
            continue
        cx, cy = cell
        if first:
            cx, cy = -cx, -cy
        cells[name] = (cx, cy)
        w, h = projected_extent(shape, direction, xdirection)
        cols[cx] = max(cols.get(cx, 0.0), w)
        rows[cy] = max(rows.get(cy, 0.0), h)
    if not cols:
        cols, rows = {0: 0.0}, {0: 0.0}
    return {
        "cols": [cols[k] for k in sorted(cols)],
        "rows": [rows[k] for k in sorted(rows)],
        "col_keys": sorted(cols),
        "row_keys": sorted(rows),
        "cells": cells,
        "gap_x": float(group.spacingX),
        "gap_y": float(group.spacingY),
    }


def _centers(sizes, keys, gap, s):
    """Centre of each column/row (paper mm), measured from the box centre."""
    total = s * sum(sizes) + gap * (len(sizes) - 1)
    pos = -total / 2.0
    out = {}
    for size, key in zip(sizes, keys):
        out[key] = pos + s * size / 2.0
        pos += s * size + gap
    return out


def item_offsets(layout, s):
    """{item name: (dx, dy)} of each projection relative to the Front view."""
    xs = _centers(layout["cols"], layout["col_keys"], layout["gap_x"], s)
    ys = _centers(layout["rows"], layout["row_keys"], layout["gap_y"], s)
    x0, y0 = xs.get(0, 0.0), ys.get(0, 0.0)
    return {name: (xs[cx] - x0, ys[cy] - y0)
            for name, (cx, cy) in layout["cells"].items()}


def group_box(layout, s):
    w = s * sum(layout["cols"]) + layout["gap_x"] * (len(layout["cols"]) - 1)
    h = s * sum(layout["rows"]) + layout["gap_y"] * (len(layout["rows"]) - 1)
    return w, h


def anchor_offset(layout, s):
    """Offset of the Front view centre from the group box centre (paper mm)."""
    xs = _centers(layout["cols"], layout["col_keys"], layout["gap_x"], s)
    ys = _centers(layout["rows"], layout["row_keys"], layout["gap_y"], s)
    return xs.get(0, 0.0), ys.get(0, 0.0)


# ---------------------------------------------------------------------------
# Whole-page layout: one projection group plus extra views in a side column
# ---------------------------------------------------------------------------
GAP = 15.0  # paper mm between the group and the extra views


def extra_view_size(view, shape=None):
    shape = shape or source_shape(view.Source)
    return projected_extent(shape, view.Direction, view.XDirection)


def _arrangements(gw, gh, extras, s):
    """Yield (total_w, total_h, placer) for the group + extras side or below."""
    if not extras:
        yield gw, gh, "none"
        return
    ew = max(w for w, _ in extras) * s
    eh = sum(h for _, h in extras) * s + GAP * (len(extras) - 1)
    yield gw + GAP + ew, max(gh, eh), "right"
    rw = sum(w for w, _ in extras) * s + GAP * (len(extras) - 1)
    rh = max(h for _, h in extras) * s
    yield max(gw, rw), gh + GAP + rh, "below"


def plan_page(page, group, extras_views=(), layout=None):
    """Pick the scale and positions for the group and the extra views.

    Returns (scale, {view_name: (x, y)}) where the group entry is the
    position of its anchor (Front view).
    """
    layout = layout or group_layout(group)
    extras = [(v, extra_view_size(v)) for v in extras_views]
    sizes = [sz for _, sz in extras]
    regions = page_regions(page)

    best = None
    for ratio in STANDARD_SCALES:
        s = scale_value(ratio)
        gw, gh = group_box(layout, s)
        for region in regions:
            rw, rh = region[2] - region[0], region[3] - region[1]
            for tw, th, mode in _arrangements(gw, gh, sizes, s):
                if tw <= rw + 1e-9 and th <= rh + 1e-9:
                    best = (s, region, tw, th, mode)
                    break
            if best:
                break
        if best:
            break
    if not best:
        s = scale_value(STANDARD_SCALES[-1])
        gw, gh = group_box(layout, s)
        tw, th, mode = next(iter(_arrangements(gw, gh, sizes, s)))
        best = (s, max(regions, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])), tw, th, mode)

    s, region, tw, th, mode = best
    cx = (region[0] + region[2]) / 2.0
    cy = (region[1] + region[3]) / 2.0
    left, top = cx - tw / 2.0, cy + th / 2.0
    gw, gh = group_box(layout, s)
    ox, oy = anchor_offset(layout, s)
    positions = {group.Name: (left + gw / 2.0 + ox, top - gh / 2.0 + oy)}

    if mode == "right":
        x = left + gw + GAP
        y = top
        ew = max(w for w, _ in sizes) * s
        for view, (w, h) in extras:
            positions[view.Name] = (x + ew / 2.0, y - h * s / 2.0)
            y -= h * s + GAP
    elif mode == "below":
        x = left
        y = top - gh - GAP
        for view, (w, h) in extras:
            positions[view.Name] = (x + w * s / 2.0, y - h * s / 2.0)
            x += w * s + GAP
    return s, positions


def page_content(page):
    """(main projection group or None, extra part views to lay out)."""
    groups, extras = [], []
    for v in page.Views:
        if v.isDerivedFrom("TechDraw::DrawProjGroup"):
            groups.append(v)
        elif v.isDerivedFrom("TechDraw::DrawViewDetail"):
            continue  # detail views keep their own scale and spot
        elif v.isDerivedFrom("TechDraw::DrawViewPart") and not \
                v.isDerivedFrom("TechDraw::DrawProjGroupItem"):
            extras.append(v)
    group = max(groups, key=lambda g: len(g.Views)) if groups else None
    return group, extras


def fit_page(page):
    """Re-pick the page scale so the main group and extra views fit. Returns scale."""
    group, extras = page_content(page)
    if group is None:
        raise ValueError("This page has no projection group to fit")
    scale, positions = plan_page(page, group, extras)
    apply_plan(page, group, extras, scale, positions)
    redraw([group] + list(group.Views) + list(extras))
    return scale


def redraw(views):
    """Recompute views one by one.

    In the GUI a touched view is not always redrawn by doc.recompute() after
    only its scale changed (checked on FreeCAD 1.1.4); recomputing each view
    directly is what makes it draw again.
    """
    for v in views:
        v.recompute()


def stale_views(views, tolerance=0.25):
    """Views whose drawing is not at their current scale (or not drawn yet).

    TechDraw draws in background threads and has no 'busy' flag in Python;
    if a redraw is requested while an older one is still running, the older
    result can land last. Standard scales are at least 2x apart, so a drawing
    more than 25 % off its expected size is stale.
    """
    import Part
    stale = []
    shapes = {}
    for v in views:
        if not v.isDerivedFrom("TechDraw::DrawViewPart") or \
                v.isDerivedFrom("TechDraw::DrawViewSection") or \
                v.isDerivedFrom("TechDraw::DrawViewDetail") or \
                v.isDerivedFrom("TechDraw::DrawProjGroup"):
            continue
        try:
            key = tuple(o.Name for o in v.Source)
            if key not in shapes:
                shapes[key] = source_shape(v.Source)
            w, h = projected_extent(shapes[key], v.Direction, v.XDirection)
            expected = max(w, h) * float(v.getScale())
            edges = v.getVisibleEdges()
            if not edges:
                stale.append(v)
                continue
            bb = Part.makeCompound(edges).BoundBox
            drawn = max(bb.XLength, bb.YLength)
        except Exception:
            continue
        if expected > 1e-6 and abs(drawn / expected - 1.0) > tolerance:
            stale.append(v)
    return stale


def apply_plan(page, group, extras, scale, positions):
    """Set the scale and positions, then make every view redraw.

    In the GUI TechDraw draws each view as soon as it exists, so views made
    before the scale was known keep their old drawing unless touched.
    """
    page.Scale = scale
    set_title_block_scale(page, scale)
    for view in [group] + list(extras):
        if view.ScaleType != "Page":
            view.ScaleType = "Page"
        view.Scale = scale
        if view.Name in positions:
            view.X, view.Y = positions[view.Name]
    if is_aligned(group):
        layout = group_layout(group)
        offsets = item_offsets(layout, scale)
        for item in group.Views:
            if item.Name in offsets:
                item.X, item.Y = offsets[item.Name]
    for view in list(group.Views) + [group] + list(extras):
        view.touch()


def set_title_block_scale(page, scale):
    """Write the scale into the title block's 'scale' field, if it has one."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return False
    texts = dict(tpl.EditableTexts)
    keys = [k for k in texts if k.lower() == "scale"]
    if not keys:
        return False
    text = scale_text(scale).replace(":", " : ")
    for k in keys:
        texts[k] = text
    tpl.EditableTexts = texts
    return True


# ---------------------------------------------------------------------------
# Projected views that slide along their alignment axis
# ---------------------------------------------------------------------------
# TechDraw's AutoDistribute locks every view of a group in place. DrawingKit turns
# it off and keeps each view aligned with the Front view on one axis only:
# side views (Left/Right/Rear) move left-right, Top/Bottom move up-down,
# isometric/corner views move freely, and moving the Front view moves all.
ALIGN_PROP = "DrawingKitAligned"
_SLIDE_X = {"Right", "Left", "Rear"}       # keep Y = 0, X free
_SLIDE_Y = {"Top", "Bottom"}               # keep X = 0, Y free


def make_aligned(group, on=True):
    if ALIGN_PROP not in group.PropertiesList:
        group.addProperty("App::PropertyBool", ALIGN_PROP, "DrawingKit",
                          "Projected views stay aligned with the Front view on one "
                          "axis but can be dragged along the other")
    setattr(group, ALIGN_PROP, on)
    group.AutoDistribute = not on


def is_aligned(group):
    return bool(getattr(group, ALIGN_PROP, False)) and not group.AutoDistribute


def parent_group(item):
    for parent in item.InList:
        if parent.isDerivedFrom("TechDraw::DrawProjGroup"):
            return parent
    return None


def enforce_alignment(item):
    """Snap a moved view back onto its alignment axis and bring the views
    projected from it along. True if anything changed."""
    changed = False
    if PARENT_PROP in getattr(item, "PropertiesList", []):
        changed = follow_parent(item) or changed
    if item.isDerivedFrom("TechDraw::DrawProjGroupItem"):
        changed = _enforce_group_item(item) or changed
    if item.isDerivedFrom("TechDraw::DrawView"):
        changed = realign_children(item) or changed
        group = parent_group(item) if item.isDerivedFrom("TechDraw::DrawProjGroupItem") else None
        if group is not None:
            changed = realign_children(group) or changed
    return changed


def _enforce_group_item(item):
    if not item.isDerivedFrom("TechDraw::DrawProjGroupItem"):
        return False
    group = parent_group(item)
    if group is None or not is_aligned(group):
        return False
    x, y = float(item.X), float(item.Y)
    if item.Type == "Front":
        if abs(x) < 1e-9 and abs(y) < 1e-9:
            return False
        # Moving the Front view moves the whole group with it.
        group.X = float(group.X) + x
        group.Y = float(group.Y) + y
        item.X, item.Y = 0.0, 0.0
        return True
    if item.Type in _SLIDE_X and abs(y) > 1e-9:
        item.Y = 0.0
        return True
    if item.Type in _SLIDE_Y and abs(x) > 1e-9:
        item.X = 0.0
        return True
    return False


# ---------------------------------------------------------------------------
# Standard views
# ---------------------------------------------------------------------------
def best_front(shape):
    """(direction, x direction) for the most descriptive front view.

    The drafting rule: the front view shows the part's biggest face, with
    its longest side across the page. Of the model's front, top and right
    directions, the one with the largest projected outline wins (ties keep
    the model's own front). A flange lying flat gets its round face as the
    front view instead of a thin strip.
    """
    front = STANDARD_DIRECTIONS["Front"]
    candidates = [front, STANDARD_DIRECTIONS["Top"], STANDARD_DIRECTIONS["Right"]]
    best, best_area = None, -1.0
    for d, x in candidates:
        w, h = projected_extent(shape, d, x)
        if w * h > best_area * 1.02:   # clearly bigger: earlier ones win ties
            best, best_area = (d, x), w * h
    d, x = best
    w, h = projected_extent(shape, d, x)
    if h > w * 1.02:
        # turn the view so its longest side runs across the page
        dd = App.Vector(d)
        dd.normalize()
        x = dd.cross(App.Vector(x))
    return App.Vector(d), App.Vector(x)


def add_standard_views(page, objs, isometric=False, front=None):
    """Front/Top/Right (+ iso) projection group, scaled to fit. Returns group.

    ``front`` is (direction, x direction) for the front view; by default the
    most descriptive one is picked (see best_front)."""
    doc = page.Document
    shape = source_shape(objs)  # fails early if nothing usable
    d, x = front or best_front(shape)
    group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
    page.addView(group)
    group.Source = list(objs)
    group.ScaleType = "Page"
    make_aligned(group, True)
    types = ["Front", "Top", "Right"] + (["FrontTopRight"] if isometric else [])

    # Work out the scale BEFORE any view exists. In the GUI each view starts
    # drawing in a background thread as soon as it is added; a view first
    # drawn at 1:1 kept that drawing even after the scale changed.
    _, extras = page_content(page)
    specs = []
    for t in types:
        dirs = (d, x) if t == "Front" else projected_directions(d, x, _GRID[t])
        specs.append((t, t) + tuple(dirs))
    layout = group_layout(group, shape=shape, specs=specs)
    scale, positions = plan_page(page, group, extras, layout)
    page.Scale = scale
    group.Scale = scale

    group.addProjection("Front")
    group.Anchor.Direction = d
    group.Anchor.XDirection = x
    group.Anchor.RotationVector = x
    for t in types[1:]:
        group.addProjection(t)
    apply_plan(page, group, extras, scale, positions)
    return group


# ---------------------------------------------------------------------------
# Section views
# ---------------------------------------------------------------------------
def view_box(view):
    """Approximate (x0, y0, x1, y1) of a placed view on the page."""
    w, h = extra_view_size(view)
    s = float(view.getScale()) if hasattr(view, "getScale") else float(view.Scale)
    x, y = float(view.X), float(view.Y)
    if view.isDerivedFrom("TechDraw::DrawProjGroupItem"):
        group = view.InList[0] if view.InList else None
        for parent in view.InList:
            if parent.isDerivedFrom("TechDraw::DrawProjGroup"):
                group = parent
        if group is not None:
            x += float(group.X)
            y += float(group.Y)
    return (x - w * s / 2.0, y - h * s / 2.0, x + w * s / 2.0, y + h * s / 2.0)


def _section_vectors(base, direction):
    """TechDraw's rules (DrawViewSection::getCSFromBase) for a named direction."""
    d, right, up = view_axes(base)
    if direction == "Right":
        return -right, d
    if direction == "Left":
        return right, -d
    if direction == "Up":
        return -up, right
    return up, right  # Down


def _new_section(page, base, letter, type_id="TechDraw::DrawViewSection"):
    doc = page.Document
    letter = letter or next_free_letter(used_letters(doc))
    sec = doc.addObject(type_id, "SectionView")
    page.addView(sec)
    sec.BaseView = base
    sec.Source = base.Source
    sec.SectionSymbol = letter
    sec.Label = "Section %s-%s" % (letter, letter)
    sec.Caption = "SECTION %s-%s" % (letter, letter)
    sec.ScaleType = "Page"
    sec.Scale = page.Scale
    return sec


def add_section(page, base, letter=None, direction="Right", origin=None, place=None):
    """Straight section of ``base``, auto-lettered.

    ``origin`` is a 3D point on the cut plane (default: the part's middle).
    ``place`` is the page position (x, y) for the section view; by default
    it goes beside the base view on the side the arrows point to.
    """
    shape = source_shape(base.Source)
    sec = _new_section(page, base, letter)
    sec.SectionOrigin = origin if origin is not None else shape.BoundBox.Center
    sec.SectionDirection = direction
    sec_dir, sec_x = _section_vectors(base, direction)
    sec.Direction = sec_dir
    sec.SectionNormal = sec_dir
    sec.XDirection = sec_x
    _place_section(page, base, sec, direction, shape, place)
    sec.touch()
    return sec


def _place_section(page, base, sec, direction, shape, place):
    bx, by = view_center(base)
    if place is not None:
        # keep it lined up with the base view, like a projected view
        if direction in ("Right", "Left"):
            sec.X, sec.Y = float(place[0]), by
        else:
            sec.X, sec.Y = bx, float(place[1])
        return
    x0, y0, x1, y1 = view_box(base)
    w, h = extra_view_size(sec, shape)
    s = float(page.Scale)
    if direction == "Right":
        sec.X, sec.Y = x1 + GAP + w * s / 2.0, by
    elif direction == "Left":
        sec.X, sec.Y = x0 - GAP - w * s / 2.0, by
    elif direction == "Up":
        sec.X, sec.Y = bx, y1 + GAP + h * s / 2.0
    else:
        sec.X, sec.Y = bx, y0 - GAP - h * s / 2.0


def snap_cut_points(points):
    """Snap each segment of a clicked cut line to horizontal or vertical.

    ``points`` are page (x, y). Returns new points; each segment keeps its
    larger component, so the line turns at right angles (stepped section).
    """
    if not points:
        return []
    out = [tuple(map(float, points[0]))]
    for x, y in points[1:]:
        px, py = out[-1]
        if abs(x - px) >= abs(y - py):
            nxt = (float(x), py)
        else:
            nxt = (px, float(y))
        if abs(nxt[0] - px) > 1e-6 or abs(nxt[1] - py) > 1e-6:
            out.append(nxt)
    return out


def cut_orientation(points, place):
    """Which way the section looks: 'Right', 'Left', 'Up' or 'Down'.

    The first cut segment decides whether the cut is vertical or
    horizontal (a single click: where the view is placed decides). The
    section view sits on the side the arrows point to.
    """
    px, py = place
    if len(points) >= 2:
        (x0, y0), (x1, y1) = points[0], points[1]
        vertical = abs(y1 - y0) > abs(x1 - x0)
    else:
        x0, y0 = points[0]
        vertical = abs(px - x0) >= abs(py - y0)
    if vertical:
        mid = sum(p[0] for p in points) / len(points)
        return "Right" if px >= mid else "Left"
    mid = sum(p[1] for p in points) / len(points)
    return "Up" if py >= mid else "Down"


def check_cut_crosses(points):
    """A stepped cut must cross the part: its main segments (parallel to the
    first one) all run the same way. TechDraw cannot cut a U-shaped line."""
    if len(points) < 3:
        return
    (x0, y0), (x1, y1) = points[0], points[1]
    vertical = abs(y1 - y0) > abs(x1 - x0)
    sign = None
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        d = (by - ay) if vertical else (bx - ax)
        if abs(d) < 1e-9:
            continue
        s = d > 0
        if sign is None:
            sign = s
        elif s != sign:
            raise ValueError(
                "A stepped cut has to go across the part from one side to the "
                "other: keep going %s, and use sideways steps to reach each "
                "feature. It can't turn back." % (
                    ("down" if not sign else "up") if vertical
                    else ("left" if not sign else "right")))


def _past_outline(point, direction, shape, margin):
    """Move ``point`` along ``direction`` to just outside the part's box."""
    u = App.Vector(direction)
    u.normalize()
    bb = shape.BoundBox
    corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
               for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    reach = max((c - point).dot(u) for c in corners)
    return point + u * (max(reach, 0.0) + margin)


def add_cut_section(page, base, points, place, letter=None):
    """Section along a cut line clicked on ``base`` (page coordinates).

    One point, or a straight line: an ordinary section through that point.
    A line with steps: a TechDraw complex (offset) section following it.
    """
    pts = snap_cut_points(points)
    if not pts:
        raise ValueError("Click at least one point for the cut")
    check_cut_crosses(pts)
    direction = cut_orientation(pts, place)
    shape = source_shape(base.Source)
    model = [page_to_model(base, x, y) for x, y in pts]
    if len(pts) <= 2:
        return add_section(page, base, letter, direction, origin=model[0], place=place)

    # Stepped line: run the end segments just past the part's outline (as a
    # drawn cutting line does). TechDraw extends the profile itself for the
    # cut; overlong ends make its extension go wrong and the cut fails.
    margin = 5.0 / view_scale(base)
    first = _past_outline(model[0], model[0] - model[1], shape, margin)
    last = _past_outline(model[-1], model[-1] - model[-2], shape, margin)
    wire_pts = [first] + model[1:-1] + [last]
    import Part
    doc = page.Document
    sec = _new_section(page, base, letter, "TechDraw::DrawComplexSection")
    tool = doc.addObject("Part::Feature", "CutLine_%s" % sec.SectionSymbol)
    tool.Label = "Cut line %s-%s" % (sec.SectionSymbol, sec.SectionSymbol)
    tool.Shape = Part.makePolygon(wire_pts)
    if App.GuiUp and tool.ViewObject is not None:
        tool.ViewObject.Visibility = False
    sec.CuttingToolWireObject = tool
    sec.ProjectionStrategy = "Offset"
    sec.SectionOrigin = model[0]
    sec.SectionDirection = "Aligned"
    sec_dir, sec_x = _section_vectors(base, direction)
    sec.Direction = sec_dir
    sec.SectionNormal = sec_dir
    sec.XDirection = sec_x
    _place_section(page, base, sec, direction, shape, place)
    sec.touch()
    return sec


# ---------------------------------------------------------------------------
# View geometry: page <-> model coordinates
# ---------------------------------------------------------------------------
def view_axes(view):
    """(direction, right, up) unit vectors of a part view."""
    d = App.Vector(view.Direction)
    d.normalize()
    x = App.Vector(view.XDirection)
    x = x - d * x.dot(d)
    x.normalize()
    return d, x, d.cross(x)


def view_center(view):
    """Page position (x, y) of the view's centre (group items included)."""
    x, y = float(view.X), float(view.Y)
    if view.isDerivedFrom("TechDraw::DrawProjGroupItem"):
        group = parent_group(view)
        if group is not None:
            x += float(group.X)
            y += float(group.Y)
    return x, y


def view_scale(view):
    return float(view.getScale()) if hasattr(view, "getScale") else float(view.Scale)


def model_to_local(view, point, shape=None):
    """3D point -> unscaled view coordinates (Y up), as cosmetic lines want.

    TechDraw centres a view on the bounding-box centre of its source shape.
    """
    shape = shape or source_shape(view.Source)
    d, right, up = view_axes(view)
    v = App.Vector(point) - shape.BoundBox.Center
    return v.dot(right), v.dot(up)


def model_to_page(view, point, shape=None):
    lx, ly = model_to_local(view, point, shape)
    cx, cy = view_center(view)
    s = view_scale(view)
    return cx + lx * s, cy + ly * s


def page_to_model(view, px, py, shape=None):
    """Page point -> 3D point on the plane through the part's middle."""
    shape = shape or source_shape(view.Source)
    d, right, up = view_axes(view)
    cx, cy = view_center(view)
    s = view_scale(view)
    return shape.BoundBox.Center + right * ((px - cx) / s) + up * ((py - cy) / s)


# ---------------------------------------------------------------------------
# Projected views by pointing
# ---------------------------------------------------------------------------
_TYPE_FOR_SIDE = {
    (1, 0): "Right", (-1, 0): "Left", (0, 1): "Top", (0, -1): "Bottom",
    (1, 1): "FrontTopRight", (-1, 1): "FrontTopLeft",
    (1, -1): "FrontBottomRight", (-1, -1): "FrontBottomLeft",
}


def placement_side(base_xy, point):
    """Which of 8 directions the point is from the base view: (sx, sy)."""
    dx, dy = point[0] - base_xy[0], point[1] - base_xy[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return (1, 0)
    t = 2.414  # tan(67.5 deg): 45-degree sectors centred on the axes
    if abs(dx) > t * abs(dy):
        return (1 if dx > 0 else -1, 0)
    if abs(dy) > t * abs(dx):
        return (0, 1 if dy > 0 else -1)
    return (1 if dx > 0 else -1, 1 if dy > 0 else -1)


def snapped_position(base_xy, point, side):
    """Keep orthogonal projections lined up with their base view."""
    if side[1] == 0:
        return float(point[0]), float(base_xy[1])
    if side[0] == 0:
        return float(base_xy[0]), float(point[1])
    return float(point[0]), float(point[1])


def projected_directions(direction, xdirection, side):
    """Direction and X direction of the view showing ``side`` of the base.

    ``side`` is (sx, sy) in the base view: (1, 0) = its right-hand side.
    Matches TechDraw's projection group (third-angle names).
    """
    d = App.Vector(direction)
    d.normalize()
    x = App.Vector(xdirection)
    x = x - d * x.dot(d)
    x.normalize()
    up = d.cross(x)
    sx, sy = side
    if sy == 0:
        return (x * sx, -d * sx)
    if sx == 0:
        return (up * sy, x)
    nd = (d + x * sx + up * sy)
    nd.normalize()
    nx = x - d * sx
    nx = nx - nd * nx.dot(nd)
    nx.normalize()
    return nd, nx


def shown_side(placed_side, first_angle):
    """In first angle a view placed on the right shows the LEFT side."""
    if first_angle:
        return (-placed_side[0], -placed_side[1])
    return placed_side


def page_first_angle(page):
    return str(page.ProjectionType).lower().startswith("first")


def projection_preview(page, base, point):
    """(x, y, w, h) on the page of the view that a click at ``point`` makes."""
    base_xy = view_center(base)
    side = placement_side(base_xy, point)
    group = parent_group(base) if base.isDerivedFrom("TechDraw::DrawProjGroupItem") else None
    first = is_first_angle(group) if group is not None else page_first_angle(page)
    d, x = projected_directions(base.Direction, base.XDirection, shown_side(side, first))
    w, h = projected_extent(source_shape(base.Source), d, x)
    px, py = snapped_position(base_xy, point, side)
    s = view_scale(base)
    return px, py, w * s, h * s


PARENT_PROP = "DrawingKitParent"
AXIS_PROP = "DrawingKitAxis"


def same(a, b):
    """True if a and b are the same document object.

    Two Python handles to one FreeCAD object do not always compare equal
    with ==, so compare document and object names.
    """
    if a is None or b is None:
        return False
    try:
        return a.Document.Name == b.Document.Name and a.Name == b.Name
    except Exception:
        return False


def add_projected_view(page, base, point):
    """Project a new view from ``base`` towards the clicked page ``point``.

    From the Front view of a projection group it becomes part of the group;
    from any other view it is a separate view that stays lined up with it.
    """
    doc = page.Document
    base_xy = view_center(base)
    side = placement_side(base_xy, point)
    px, py = snapped_position(base_xy, point, side)
    group = parent_group(base) if base.isDerivedFrom("TechDraw::DrawProjGroupItem") else None

    if group is not None and same(group.Anchor, base):
        vtype = _TYPE_FOR_SIDE[shown_side(side, is_first_angle(group))]
        if any(v.Type == vtype for v in group.Views):
            raise ValueError("This group already has a %s view" % vtype)
        if not is_aligned(group):
            make_aligned(group, True)
        item = group.addProjection(vtype)
        item.X, item.Y = px - float(group.X), py - float(group.Y)
        item.touch()
        return item

    first = is_first_angle(group) if group is not None else page_first_angle(page)
    d, x = projected_directions(base.Direction, base.XDirection, shown_side(side, first))
    view = doc.addObject("TechDraw::DrawViewPart", "View")
    page.addView(view)
    view.Source = base.Source
    view.Direction = d
    view.XDirection = x
    view.ScaleType = "Page"
    view.Scale = page.Scale
    view.X, view.Y = px, py
    view.Label = "View from %s" % base.Label
    view.addProperty("App::PropertyLink", PARENT_PROP, "DrawingKit",
                     "View this one was projected from; it stays lined up with it")
    view.addProperty("App::PropertyString", AXIS_PROP, "DrawingKit",
                     "H: stays level with the parent view, V: stays above/below it")
    setattr(view, PARENT_PROP, base)
    setattr(view, AXIS_PROP, "H" if side[1] == 0 else ("V" if side[0] == 0 else ""))
    view.touch()
    return view


def linked_children(view):
    """Views projected from ``view`` (DrawingKit links)."""
    return [o for o in view.InList if same(getattr(o, PARENT_PROP, None), view)]


def follow_parent(view):
    """Snap a linked view back in line with its parent. True if it moved."""
    parent = getattr(view, PARENT_PROP, None)
    axis = getattr(view, AXIS_PROP, "")
    if parent is None or axis not in ("H", "V"):
        return False
    px, py = view_center(parent)
    if axis == "H" and abs(float(view.Y) - py) > 1e-6:
        view.Y = py
        return True
    if axis == "V" and abs(float(view.X) - px) > 1e-6:
        view.X = px
        return True
    return False


def realign_children(obj):
    """After ``obj`` moved, bring the views projected from it back in line."""
    views = [obj]
    if obj.isDerivedFrom("TechDraw::DrawProjGroup"):
        views = list(obj.Views)
    moved = False
    for v in views:
        for child in linked_children(v):
            moved = follow_parent(child) or moved
    return moved


# ---------------------------------------------------------------------------
# Detail views by clicking
# ---------------------------------------------------------------------------
DETAIL_TARGET = 60.0   # aim for a detail about this big on paper (mm)


def detail_scale(base_scale, radius_model, target=DETAIL_TARGET):
    """Standard scale for a detail: at least 2x the base view, sized so the
    detail circle comes out about ``target`` mm across."""
    want = target / (2.0 * radius_model) if radius_model > 0 else base_scale * 2
    values = sorted(scale_value(r) for r in STANDARD_SCALES)
    floor = base_scale * 2.0 - 1e-9
    fitting = [v for v in values if floor <= v <= want + 1e-9]
    if fitting:
        return fitting[-1]
    bigger = [v for v in values if v >= floor]
    return bigger[0] if bigger else values[-1]


def add_detail(page, base, centre, radius, place, letter=None, scale=None):
    """Detail view of the circle (page ``centre``, page ``radius`` in mm) on
    ``base``, placed at page point ``place``. Auto-lettered and scaled."""
    doc = page.Document
    s = view_scale(base)
    cx, cy = view_center(base)
    r_model = float(radius) / s
    if r_model <= 0:
        raise ValueError("Drag out a circle for the detail")
    letter = letter or next_free_letter(used_letters(doc))
    scale = scale or detail_scale(s, r_model)
    det = doc.addObject("TechDraw::DrawViewDetail", "Detail")
    page.addView(det)
    det.BaseView = base
    det.Source = base.Source
    det.Direction = base.Direction
    det.XDirection = base.XDirection
    # anchor: unscaled base-view coordinates, Y up, from the view centre
    det.AnchorPoint = App.Vector((centre[0] - cx) / s, (centre[1] - cy) / s, 0)
    det.Radius = r_model
    det.Reference = letter
    det.ScaleType = "Custom"
    det.Scale = scale
    det.X, det.Y = float(place[0]), float(place[1])
    det.Label = "Detail %s" % letter
    det.Caption = "DETAIL %s (%s)" % (letter, scale_text(scale).replace(":", " : "))
    det.touch()
    return det


# ---------------------------------------------------------------------------
# Centre marks and centre lines
# ---------------------------------------------------------------------------
MARKS_PROP = "DrawingKitCenterMarks"
CENTER_LINE_STYLE = 4      # dash-dot (centre line)
CENTER_LINE_WEIGHT = 0.25  # thin line
OVERSHOOT = 3.0            # paper mm the lines stick out past the circle/edge


def _round_faces(shape):
    """Cylindrical faces that are a hole or a boss (at least half round)."""
    import math
    out = []
    for face in shape.Faces:
        surf = face.Surface
        if surf.TypeId != "Part::GeomCylinder":
            continue
        u0, u1 = face.ParameterRange[0], face.ParameterRange[1]
        if u1 - u0 < math.pi - 1e-3:
            continue  # fillet or partial round: no centre mark
        out.append(face)
    return out


def center_features(view, shape=None, with_radius=False):
    """Centre marks and centre lines to draw on ``view``.

    Returns (marks, lines): marks are (lx, ly, half_length) crosses (plus the
    feature radius as a 4th item with ``with_radius``) for
    round features seen end-on; lines are ((lx0, ly0), (lx1, ly1)) along
    the axes of round features seen from the side. All in unscaled view
    coordinates, Y up; overshoot already added.
    """
    shape = shape or source_shape(view.Source)
    d, right, up = view_axes(view)
    s = view_scale(view)
    over = OVERSHOOT / s
    marks, lines, radii = {}, {}, {}
    for face in _round_faces(shape):
        axis = App.Vector(face.Surface.Axis)
        axis.normalize()
        centre = face.Surface.Center
        radius = face.Surface.Radius
        if abs(abs(axis.dot(d)) - 1.0) < 1e-6:
            lx, ly = model_to_local(view, centre, shape)
            key = (round(lx, 4), round(ly, 4))
            marks[key] = max(marks.get(key, 0.0), radius + over)
            radii[key] = min(radii.get(key, radius), radius)  # innermost (the hole)
        elif abs(axis.dot(d)) < 1e-6:
            # extent of the face along its axis
            ts = [(App.Vector(v.Point) - centre).dot(axis) for v in face.Vertexes]
            if not ts:
                bb = face.BoundBox
                ts = [(App.Vector(bb.XMin, bb.YMin, bb.ZMin) - centre).dot(axis),
                      (App.Vector(bb.XMax, bb.YMax, bb.ZMax) - centre).dot(axis)]
            p0 = model_to_local(view, centre + axis * min(ts), shape)
            p1 = model_to_local(view, centre + axis * max(ts), shape)
            # the axis line is the same for coaxial faces: merge them
            ax = model_to_local(view, centre + axis, shape)
            dirx, diry = ax[0] - model_to_local(view, centre, shape)[0], \
                ax[1] - model_to_local(view, centre, shape)[1]
            n = (dirx ** 2 + diry ** 2) ** 0.5
            dirx, diry = dirx / n, diry / n
            if dirx < -1e-9 or (abs(dirx) < 1e-9 and diry < 0):
                dirx, diry = -dirx, -diry
            # offset of the line from the view origin, measured across it
            off = -p0[0] * diry + p0[1] * dirx
            key = (round(dirx, 4), round(diry, 4), round(off, 4))
            t0 = p0[0] * dirx + p0[1] * diry
            t1 = p1[0] * dirx + p1[1] * diry
            lo, hi = min(t0, t1), max(t0, t1)
            if key in lines:
                lo, hi = min(lo, lines[key][0]), max(hi, lines[key][1])
            lines[key] = (lo, hi)
    if with_radius:
        mark_list = [(k[0], k[1], half, radii[k]) for k, half in marks.items()]
    else:
        mark_list = [(k[0], k[1], half) for k, half in marks.items()]
    line_list = []
    for (dx, dy, off), (lo, hi) in lines.items():
        lo, hi = lo - over, hi + over
        bx, by = -dy * off, dx * off  # foot point of the line
        line_list.append(((bx + dx * lo, by + dy * lo), (bx + dx * hi, by + dy * hi)))
    return mark_list, line_list


def _visible_circle_centres(view):
    """Centres (unscaled, Y up) of circles TechDraw drew as visible."""
    out = []
    s = view_scale(view)
    for e in view.getVisibleEdges():
        c = getattr(e, "Curve", None)
        if c is not None and c.TypeId in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            # visible edges are scaled and Y-down
            out.append((c.Center.x / s, -c.Center.y / s))
    return out


def clear_center_marks(view):
    tags = list(getattr(view, MARKS_PROP, []) or [])
    for tag in tags:
        try:
            view.removeCosmeticEdge(tag)
        except Exception:
            pass
    if MARKS_PROP in view.PropertiesList:
        setattr(view, MARKS_PROP, [])
    return len(tags)


def plan_center_marks(marks, over):
    """Turn marks (lx, ly, half, radius) into line segments and circles.

    Holes of the same size lying on a circle (a bolt circle) get the pitch
    circle plus a short radial centre line each, instead of separate crosses,
    as on a typical flange drawing. The bolt circle's centre gets a cross if
    nothing is marked there already.
    """
    from . import holes as _holes
    segs, circles = [], []
    by_radius = {}
    for i, m in enumerate(marks):
        by_radius.setdefault(round(m[3], 4), []).append(i)
    on_bolt_circle = set()
    for idxs in by_radius.values():
        pts = [(marks[i][0], marks[i][1]) for i in idxs]

        def centre_marked(x, y):
            return any(abs(m[0] - x) < 1e-3 and abs(m[1] - y) < 1e-3 for m in marks)

        for (cx, cy), R, members in _holes.bolt_circles(pts, centre_marked=centre_marked):
            circles.append((cx, cy, R))
            for j in members:
                i = idxs[j]
                on_bolt_circle.add(i)
                lx, ly, half, r = marks[i]
                ux, uy = (lx - cx) / R, (ly - cy) / R
                a, b = R - r - over, R + r + over
                segs.append(((cx + ux * a, cy + uy * a), (cx + ux * b, cy + uy * b)))
            if not any(abs(m[0] - cx) < 1e-3 and abs(m[1] - cy) < 1e-3 for m in marks):
                half = R * 0.15 + over
                segs.append(((cx - half, cy), (cx + half, cy)))
                segs.append(((cx, cy - half), (cx, cy + half)))
    for i, (lx, ly, half, r) in enumerate(marks):
        if i in on_bolt_circle:
            continue
        segs.append(((lx - half, ly), (lx + half, ly)))
        segs.append(((lx, ly - half), (lx, ly + half)))
    return segs, circles


def add_center_marks(view):
    """(Re)draw DrawingKit centre marks, bolt circles and centre lines on a view.
    Returns the number of cosmetic lines/circles made."""
    if not view.isDerivedFrom("TechDraw::DrawViewPart"):
        return 0
    clear_center_marks(view)
    marks, lines = center_features(view, with_radius=True)
    visible = _visible_circle_centres(view)
    if visible:
        # skip holes hidden behind material in this view
        marks = [m for m in marks
                 if any(abs(m[0] - cx) < 1e-3 and abs(m[1] - cy) < 1e-3 for cx, cy in visible)]
    segs, circles = plan_center_marks(marks, OVERSHOOT / view_scale(view))
    segs.extend(lines)
    tags = []
    for (x0, y0), (x1, y1) in segs:
        tag = view.makeCosmeticLine(App.Vector(x0, y0, 0), App.Vector(x1, y1, 0),
                                    CENTER_LINE_STYLE, CENTER_LINE_WEIGHT)
        if tag:
            tags.append(tag)
    for cx, cy, R in circles:
        tag = view.makeCosmeticCircle(App.Vector(cx, cy, 0), R,
                                      CENTER_LINE_STYLE, CENTER_LINE_WEIGHT)
        if tag:
            tags.append(tag)
    if MARKS_PROP not in view.PropertiesList:
        view.addProperty("App::PropertyStringList", MARKS_PROP, "DrawingKit",
                         "Cosmetic lines DrawingKit made as centre marks (replaced on refresh)")
    setattr(view, MARKS_PROP, tags)
    return len(tags)


def refresh_center_marks(views):
    """Redraw marks on views that had them (after the scale changed)."""
    n = 0
    for v in views:
        if getattr(v, MARKS_PROP, None):
            n += add_center_marks(v)
    return n


# ---------------------------------------------------------------------------
# Speed toggle
# ---------------------------------------------------------------------------
def part_views(page):
    out = []
    for v in page.getAllViews() if hasattr(page, "getAllViews") else page.Views:
        if v.isDerivedFrom("TechDraw::DrawViewPart") and "CoarseView" in v.PropertiesList:
            out.append(v)
    return out


def is_fast(page):
    return not page.KeepUpdated


def set_fast(page, fast):
    """Fast mode: stop auto-updates and use coarse views. Off: full redraw."""
    views = part_views(page)
    if fast:
        page.KeepUpdated = False
        for v in views:
            v.CoarseView = True
    else:
        for v in views:
            v.CoarseView = False
        page.KeepUpdated = True
        groups = [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawProjGroup")]
        redraw(groups + views)
        page.touch()
    return fast
