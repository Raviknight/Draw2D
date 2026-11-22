# SPDX-License-Identifier: LGPL-2.1-or-later
"""Draw2D core logic.

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
def create_page(doc, size, orientation, templates=None):
    templates = templates or available_templates()
    key = (size, orientation)
    if key not in templates:
        raise ValueError("No FreeCAD template for %s %s" % (size, orientation))
    page = doc.addObject("TechDraw::DrawPage", "Page")
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tpl.Template = templates[key]
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
# TechDraw's AutoDistribute locks every view of a group in place. Draw2D turns
# it off and keeps each view aligned with the Front view on one axis only:
# side views (Left/Right/Rear) move left-right, Top/Bottom move up-down,
# isometric/corner views move freely, and moving the Front view moves all.
ALIGN_PROP = "Draw2DAligned"
_SLIDE_X = {"Right", "Left", "Rear"}       # keep Y = 0, X free
_SLIDE_Y = {"Top", "Bottom"}               # keep X = 0, Y free


def make_aligned(group, on=True):
    if ALIGN_PROP not in group.PropertiesList:
        group.addProperty("App::PropertyBool", ALIGN_PROP, "Draw2D",
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
    """Snap a dragged projection back onto its alignment axis. True if changed."""
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
def add_standard_views(page, objs, isometric=False):
    """Front/Top/Right (+ iso) projection group, scaled to fit. Returns group."""
    doc = page.Document
    source_shape(objs)  # fail early if nothing usable
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
    specs = [(t, t) + STANDARD_DIRECTIONS[t] for t in types]
    layout = group_layout(group, specs=specs)
    scale, positions = plan_page(page, group, extras, layout)
    page.Scale = scale
    group.Scale = scale

    group.addProjection("Front")
    group.Anchor.Direction = STANDARD_DIRECTIONS["Front"][0]
    group.Anchor.XDirection = STANDARD_DIRECTIONS["Front"][1]
    group.Anchor.RotationVector = STANDARD_DIRECTIONS["Front"][1]
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


def add_section(page, base, letter=None, direction="Right"):
    """Section through the middle of ``base``'s part, auto-lettered."""
    doc = page.Document
    letter = letter or next_free_letter(used_letters(doc))
    shape = source_shape(base.Source)

    d = App.Vector(base.Direction)
    d.normalize()
    right = App.Vector(base.XDirection)
    right.normalize()
    up = d.cross(right)
    if direction == "Right":
        sec_dir, sec_x = -right, d
    elif direction == "Left":
        sec_dir, sec_x = right, -d
    elif direction == "Up":
        sec_dir, sec_x = -up, right
    else:  # Down
        sec_dir, sec_x = up, right

    sec = doc.addObject("TechDraw::DrawViewSection", "SectionView")
    page.addView(sec)
    sec.BaseView = base
    sec.Source = base.Source
    sec.SectionSymbol = letter
    sec.Label = "Section %s-%s" % (letter, letter)
    sec.Caption = "SECTION %s-%s" % (letter, letter)
    sec.SectionOrigin = shape.BoundBox.Center
    sec.SectionDirection = direction
    sec.Direction = sec_dir
    sec.SectionNormal = sec_dir
    sec.XDirection = sec_x
    sec.ScaleType = "Page"
    sec.Scale = page.Scale
    sec.touch()

    # Put it beside the base view (right for left/right cuts, below for up/down).
    x0, y0, x1, y1 = view_box(base)
    w, h = extra_view_size(sec, shape)
    s = float(page.Scale)
    if direction in ("Right", "Left"):
        sec.X = x1 + GAP + w * s / 2.0
        sec.Y = (y0 + y1) / 2.0
    else:
        sec.X = (x0 + x1) / 2.0
        sec.Y = y0 - GAP - h * s / 2.0
    return sec


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
