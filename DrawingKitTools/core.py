# SPDX-License-Identifier: LGPL-2.1-or-later
"""DrawingKit core logic.

Everything in here works without the FreeCAD GUI, so it can be tested with
freecadcmd. The GUI commands in ``commands.py`` are thin wrappers around it.
"""

import math
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


def view_letters(doc):
    """Letters already used by section and detail views in the document."""
    used = []
    for obj in doc.Objects:
        if obj.isDerivedFrom("TechDraw::DrawViewSection"):
            used.append(obj.SectionSymbol)
        elif obj.isDerivedFrom("TechDraw::DrawViewDetail"):
            used.append(obj.Reference)
    return used


def used_letters(doc):
    """Letters a new section or detail view must not take: other views'
    and the datums' (a section A-A beside datum A reads as the datum)."""
    used = view_letters(doc)
    try:
        from . import annot
        used += annot.used_datum_letters(doc)
    except Exception:
        pass
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
def create_page(doc, size, orientation, templates=None, drawingkit_template=True, style=None):
    """New page of the given paper size.

    Uses DrawingKit's own frame and title block (any size, both orientations,
    projection symbol to match the page). With ``drawingkit_template=False``, or
    if that fails, FreeCAD's bundled template for the size is used.
    """
    page = doc.addObject("TechDraw::DrawPage", "Page")
    try:
        from . import standards
        if standards.explicit():      # ISO first-angle, ASME third-angle
            page.ProjectionType = "First angle" if standards.default_first_angle() else "Third angle"
    except Exception:
        pass
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    path = None
    if drawingkit_template:
        try:
            from . import templates as d2t
            from . import titleblock
            path = d2t.template_path(size, orientation, page_first_angle(page),
                                     tol_row=titleblock.wants_tolerance_row(),
                                     style=style or titleblock.style())
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
    n = len([o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")])
    page.Label = "Sheet %d (%s)" % (n, size)
    page.Scale = 1.0
    try:
        from . import titleblock
        titleblock.apply_defaults(page)   # company name, tolerances, finish...
        titleblock.apply_tolerance_table(page)   # general tolerances by size, if saved
    except Exception as e:
        App.Console.PrintWarning("DrawingKit title block defaults: %s\n" % e)
    try:
        renumber_sheets(doc)
    except Exception:
        pass
    return page


def page_size(page):
    tpl = page.Template
    return float(tpl.Width), float(tpl.Height)


def page_regions(page):
    """Free rectangles of the drawing area for views: not on the title
    block, nor on DrawingKit tables (parts list, hole and revision tables)."""
    w, h = page_size(page)
    path = getattr(page.Template, "Template", "") or ""
    frame, title = drawing_area(path, w, h)
    blockers = table_boxes(page)
    if not blockers:
        return free_regions(frame, title)
    return empty_rects(frame, ([title] if title else []) + blockers)


def annotation_margins(page, extra=2.0):
    """(left, bottom, right, top) paper mm by which the views' dimensions,
    callouts and symbols stick out past the views themselves."""
    from . import tables
    try:
        views = [view_box(v) for v in part_views(page)]
        ann = tables._annotated_boxes(page)
    except Exception:
        return None
    if not views or not ann:
        return None
    vx0, vy0 = min(b[0] for b in views), min(b[1] for b in views)
    vx1, vy1 = max(b[2] for b in views), max(b[3] for b in views)
    ax0, ay0 = min(b[0] for b in ann), min(b[1] for b in ann)
    ax1, ay1 = max(b[2] for b in ann), max(b[3] for b in ann)
    m = (vx0 - ax0, vy0 - ay0, ax1 - vx1, ay1 - vy1)
    return tuple(max(0.0, x) + (extra if x > 1e-6 else 0.0) for x in m)


def view_crowding(page, gap=2.0):
    """How far (paper mm, across and up/down) the dimensions and callouts of
    the projection group's views reach into a neighbouring view: (dx, dy),
    (0, 0) when they're clear."""
    from . import tables
    group, _ = page_content(page)
    if group is None:
        return 0.0, 0.0
    try:
        ann = tables._annotated_boxes(page, named=True)
    except Exception:
        return 0.0, 0.0
    items = [v for v in group.Views if v.Name in ann]
    need_x = need_y = 0.0
    for a in items:
        A = ann[a.Name]
        for b in items:
            if b is a:
                continue
            try:
                B = view_box(b)
            except Exception:
                continue
            ox = min(A[2], B[2]) - max(A[0], B[0])
            oy = min(A[3], B[3]) - max(A[1], B[1])
            if ox <= -gap or oy <= -gap:
                continue                       # clear
            ca, cb = view_box(a), B
            side_by_side = min(ca[3], cb[3]) - max(ca[1], cb[1]) > 0
            if side_by_side:
                need_x = max(need_x, ox + gap)
            else:
                need_y = max(need_y, oy + gap)
    return need_x, need_y


def widen_view_gaps(page, gap=2.0):
    """Open up the projection group's spacing where a view's dimensions
    reach into the next view. Returns True if the spacing changed."""
    group, _ = page_content(page)
    if group is None:
        return False
    dx, dy = view_crowding(page, gap)
    if dx < 1e-6 and dy < 1e-6:
        return False
    if dx > 1e-6:
        group.spacingX = float(group.spacingX) + dx
    if dy > 1e-6:
        group.spacingY = float(group.spacingY) + dy
    return True


def scaled_margins(page, old, new):
    """annotation_margins as they would be at scale ``new`` (dimensions sit
    further out as the views grow), or None."""
    m = annotation_margins(page)
    if not m or old <= 0:
        return m
    k = new / old
    return tuple(x * k for x in m)


def annotation_clashes(page, tol=0.5):
    """True if a view, or the dimensions and callouts round it, sits on a
    DrawingKit table, on another view of the group, or runs off the
    drawing area."""
    from . import tables
    boxes = table_boxes(page, pad=0.0)
    frame, _t = drawing_area(page.Template.Template, *page_size(page))
    dx, dy = view_crowding(page)
    if dx > 1e-6 or dy > 1e-6:
        return True
    for vb in tables._annotated_boxes(page):
        if any(vb[0] < b[2] and b[0] < vb[2] and vb[1] < b[3] and b[1] < vb[3] for b in boxes):
            return True
        if (vb[0] < frame[0] - tol or vb[1] < frame[1] - tol or
                vb[2] > frame[2] + tol or vb[3] > frame[3] + tol):
            return True
    return False


def move_tables_clear(page):
    """Move each DrawingKit table that sits on a view's dimensions or
    callouts, or on the title block (after a switch to a bigger one), to
    the first free spot. Returns True if one moved."""
    from . import annot, tables
    moved = False
    ann = tables._annotated_boxes(page)
    try:
        _frame, title = drawing_area(page.Template.Template, *page_size(page))
        if title:
            ann.append(title)
    except Exception:
        pass
    for v in page.Views:
        if getattr(v, "DrawingKitAnnot", "") not in ("bom", "holetable", "revtable", "toltable", "table"):
            continue
        try:
            w, h = tables.table_size(annot._params(v))
        except Exception:
            continue
        x, y = float(v.X), float(v.Y)
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in ann):
            continue
        spot = tables.free_table_spot(page, w, h, exclude=v, strict=True)
        if spot is None:
            continue
        setattr(v, annot.ANCHOR, App.Vector(spot[0], spot[1], 0))
        v.X, v.Y = spot
        annot.render(v)
        moved = True
    return moved


def _texts_scale(page):
    """What the title block's scale field says now (None if it has none)."""
    try:
        return dict(page.Template.EditableTexts).get("scale") or None
    except Exception:
        return None


def make_room_for_tables(page):
    """If a view, or the dimensions and callouts around it, sits on a
    DrawingKit table or runs off the drawing area: move the table to a free
    spot, and fit the page again keeping room round the views for their
    dimensions and callouts (going down a standard scale only when that
    room can't be found at this one). Returns True if anything changed."""
    group, _ = page_content(page)
    if group is None:
        return False
    try:
        if not annotation_clashes(page):
            return False
    except Exception:
        return False
    scale0 = view_scale(group)
    if not any(abs(scale_value(r) - scale0) < 1e-9 for r in STANDARD_SCALES):
        # a scale the user typed (1.5:1, NTS...): keep it, only make room at it
        label = _texts_scale(page)
        for _ in range(3):
            move_tables_clear(page)
            if not annotation_clashes(page):
                return True
            widen_view_gaps(page)
            fit_page(page, exact=scale0, title_text=label, pad=annotation_margins(page))
            if not annotation_clashes(page):
                return True
        move_tables_clear(page)
        return True
    smaller = [scale_value(r) for r in STANDARD_SCALES if scale_value(r) < scale0 - 1e-12]
    for max_s in [scale0] + smaller[:2]:
        for _ in range(3):
            move_tables_clear(page)
            if not annotation_clashes(page):
                return True
            widen_view_gaps(page)        # dimensions between views get room
            fit_page(page, max_scale=max_s, pad=annotation_margins(page))
            if not annotation_clashes(page):
                return True
    # nothing clears it: best effort at the start scale
    fit_page(page, max_scale=scale0, pad=annotation_margins(page))
    move_tables_clear(page)
    return True


def table_boxes(page, pad=3.0):
    """Page rectangles of the DrawingKit tables on a page."""
    out = []
    try:
        from . import annot, tables
    except Exception:
        return out
    for v in page.Views:
        if getattr(v, "DrawingKitAnnot", "") in ("bom", "holetable", "revtable", "toltable", "table"):
            try:
                w, h = tables.table_size(annot._params(v))
            except Exception:
                continue
            x, y = float(v.X), float(v.Y)
            out.append((x - w / 2 - pad, y - h / 2 - pad, x + w / 2 + pad, y + h / 2 + pad))
        elif v.isDerivedFrom("TechDraw::DrawViewImage") and getattr(v, "DrawingKitShaded", ""):
            # the shaded picture (and its caption under it): views keep off it
            try:
                from . import shaded
                w, h = shaded.size_on_sheet(v)
            except Exception:
                continue
            x, y = float(v.X), float(v.Y)
            out.append((x - w / 2 - pad, y - h / 2 - 15.0 - pad, x + w / 2 + pad, y + h / 2 + pad))
    return out


def empty_rects(frame, blockers, pad=5.0):
    """Largest rectangles inside ``frame`` that touch none of ``blockers``,
    biggest first (each padded inwards like free_regions)."""
    fx0, fy0, fx1, fy1 = frame
    xs = sorted({fx0, fx1} | {min(max(b[i], fx0), fx1) for b in blockers for i in (0, 2)})
    ys = sorted({fy0, fy1} | {min(max(b[i], fy0), fy1) for b in blockers for i in (1, 3)})

    def clear(r):
        return all(not (r[0] < b[2] - 1e-9 and b[0] < r[2] - 1e-9 and
                        r[1] < b[3] - 1e-9 and b[1] < r[3] - 1e-9) for b in blockers)

    cands = []
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    r = (x0, y0, x1, y1)
                    if clear(r):
                        cands.append(r)
    maximal = [r for r in cands if not any(
        o != r and o[0] <= r[0] and o[1] <= r[1] and o[2] >= r[2] and o[3] >= r[3] for o in cands)]
    maximal.sort(key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))
    out = []
    for x0, y0, x1, y1 in maximal:
        x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1, y1))
    return out


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def shape_of(o):
    """3D shape of an object, including containers that have no Shape
    property of their own (App::Part assemblies, links) - via Part.getShape,
    which also applies their placements."""
    import Part
    s = getattr(o, "Shape", None)
    if s is not None and not s.isNull():
        # a part inside a moved App::Part: put it where the assembly shows it
        try:
            gp = o.getGlobalPlacement()
            if not gp.isSame(o.Placement, 1e-9):
                s = s.copy()
                s.Placement = gp
        except Exception:
            pass
        return s
    try:
        s = Part.getShape(o)
    except Exception:
        s = None
    if s is not None and not s.isNull() and s.Solids:
        return s
    # Part.getShape leaves out children it takes as hidden (in the GUI, a
    # file saved without it has every child hidden): build it ourselves
    group = getattr(o, "Group", None)
    if group:
        parts = []
        for c in group:
            if any(p.isDerivedFrom("Part::Feature") for p in getattr(c, "InList", [])):
                continue        # input of a boolean etc.
            cs = shape_of(c)
            if cs is not None and cs.Solids:
                parts.append(cs)
        if parts:
            return parts[0] if len(parts) == 1 else Part.makeCompound(parts)
    return s if s is not None and not s.isNull() else None


def source_shape(objs):
    import Part
    shapes = [s for s in (shape_of(o) for o in objs) if s is not None]
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


def drawn_extent(view):
    """(x0, y0, x1, y1) of a view's drawn edges, unscaled local, or None
    if nothing is drawn yet."""
    bb = None
    i = 0
    while i < 20000:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            break
        i += 1
        if e is None:
            continue
        b = e.BoundBox
        if bb is None:
            bb = [b.XMin, b.YMin, b.XMax, b.YMax]
        else:
            bb = [min(bb[0], b.XMin), min(bb[1], b.YMin), max(bb[2], b.XMax), max(bb[3], b.YMax)]
    if bb is None or bb[2] - bb[0] < 1e-9:
        return None
    return tuple(bb)


def section_stale_factor(sec, ext=None):
    """Factor that brings a section's drawn (local) geometry to its current
    scale: 1.0 when up to date, about old/new scale while TechDraw has not
    redrawn it yet, None if nothing is drawn. Measured across the cut,
    along the axis the section shares with its base view (never unfolded)."""
    ext = ext if ext is not None else drawn_extent(sec)
    if ext is None:
        return None
    try:
        w, h = extra_view_size(sec)
    except Exception:
        return 1.0
    side = section_side(sec)
    drawn, want = (ext[2] - ext[0], w) if side in ("Up", "Down") else (ext[3] - ext[1], h)
    if drawn < 1e-9 or want < 1e-9:
        return 1.0
    r = want / drawn
    return 1.0 if abs(r - 1.0) < 0.25 else r


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


def _shrink(regions, pad):
    """Regions less a (left, bottom, right, top) margin in paper mm."""
    if not pad:
        return regions
    l, b, r, t = pad
    out = [(x0 + l, y0 + b, x1 - r, y1 - t) for x0, y0, x1, y1 in regions]
    return [g for g in out if g[2] > g[0] and g[3] > g[1]] or regions


def plan_page(page, group, extras_views=(), layout=None, max_scale=None, scales=None, pad=None):
    """Pick the scale and positions for the group and the extra views.

    Returns (scale, {view_name: (x, y)}) where the group entry is the
    position of its anchor (Front view). ``max_scale`` skips larger scales.
    ``scales`` (largest first) replaces the standard ones; if none of them
    fits, the last is used. ``pad`` (left, bottom, right, top, paper mm) is
    room kept round the views for their dimensions and callouts.
    """
    layout = layout or group_layout(group)
    extras = [(v, extra_view_size(v)) for v in extras_views]
    sizes = [sz for _, sz in extras]
    regions = _shrink(page_regions(page), pad)
    tried = list(scales) if scales else [scale_value(r) for r in STANDARD_SCALES]

    best = None
    for s in tried:
        if max_scale is not None and s > max_scale + 1e-12:
            continue
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
        s = tried[-1]
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
        elif "DrawingKitUncutOf" in v.PropertiesList:
            continue  # uncut parts ride on their section
        elif v.isDerivedFrom("TechDraw::DrawViewPart") and not \
                v.isDerivedFrom("TechDraw::DrawProjGroupItem"):
            extras.append(v)
    group = max(groups, key=lambda g: len(g.Views)) if groups else None
    return group, extras


def fits_at(page, group, extras_views, s, layout=None, pad=None):
    """True if the group and extra views fit a drawing region at scale s
    (with ``pad`` (left, bottom, right, top) paper mm kept round them for
    their dimensions and callouts)."""
    layout = layout or group_layout(group)
    sizes = [extra_view_size(v) for v in extras_views]
    gw, gh = group_box(layout, s)
    for region in _shrink(page_regions(page), pad):
        rw, rh = region[2] - region[0], region[3] - region[1]
        for tw, th, _ in _arrangements(gw, gh, sizes, s):
            if tw <= rw + 1e-9 and th <= rh + 1e-9:
                return True
    return False


def _layout(page, group, extras, scale, positions, title_text=None):
    apply_plan(page, group, extras, scale, positions)
    if title_text:
        set_title_block_scale(page, scale, title_text)
    for v in part_views(page):
        follow_parent(v)     # projected/aux views back on their line
    realign_sections(page, extras)   # sections first: they must stay in line
    clear_all_linked(page)   # then slide projected/aux views off the others


def content_box(page, margins=None):
    """Page box round the laid-out views (not detail views, which find
    their own spot), grown by ``margins`` (left, bottom, right, top) for
    their dimensions and callouts."""
    boxes = []
    for v in part_views(page):
        if v.isDerivedFrom("TechDraw::DrawViewDetail") or "DrawingKitUncutOf" in v.PropertiesList:
            continue
        try:
            boxes.append(view_box(v))
        except Exception:
            pass
    if not boxes:
        return None
    l, b, r, t = margins or (0.0, 0.0, 0.0, 0.0)
    return (min(x[0] for x in boxes) - l, min(x[1] for x in boxes) - b,
            max(x[2] for x in boxes) + r, max(x[3] for x in boxes) + t)


def _best_region(box, regions, pad=5.0):
    """The region (as page_regions gives them, already inset ``pad``) that
    a box of this size fits, with the most room to spare; None if none."""
    if box is None:
        return None
    w, h = box[2] - box[0], box[3] - box[1]
    best = None
    for r in regions:
        # the views may use the regions' padding, as _inside_regions allows
        rw, rh = r[2] - r[0] + 2 * pad, r[3] - r[1] + 2 * pad
        room = min(rw - w, rh - h)
        if room >= -1e-6 and (best is None or room > best[0]):
            best = (room, r)
    return best[1] if best else None


def centre_content(page, group, extras, regions, margins=None):
    """Move the group and the extra views together so what was laid out
    (with its dimensions and callouts) sits in the middle of the region it
    fits best. Returns (dx, dy)."""
    box = content_box(page, margins)
    reg = _best_region(box, regions)
    if reg is None:
        return (0.0, 0.0)
    dx = (reg[0] + reg[2]) / 2.0 - (box[0] + box[2]) / 2.0
    dy = (reg[1] + reg[3]) / 2.0 - (box[1] + box[3]) / 2.0
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return (0.0, 0.0)
    for v in [group] + list(extras):
        v.X = float(v.X) + dx
        v.Y = float(v.Y) + dy
    return (dx, dy)


def fit_page(page, max_scale=None, exact=None, title_text=None, draw=True, pad=None):
    """Re-pick the page scale so the main group and extra views fit
    (no larger than ``max_scale``), or lay them out at the ``exact`` scale
    given. ``title_text`` goes in the title block's scale field instead of
    the ratio (e.g. 'NTS'). ``draw=False`` only lays out (a trial: TechDraw
    is not asked to draw, so no drawing at a passing scale can land after
    the final one). ``pad``: see plan_page. Returns scale."""
    group, extras = page_content(page)
    if group is None:
        raise ValueError("This page has no projection group to fit")
    regions = page_regions(page)
    limit = max_scale
    margins = pad or annotation_margins(page)   # measured before anything moves
    for _ in range(len(STANDARD_SCALES)):
        scale, positions = plan_page(page, group, extras, max_scale=max_scale,
                                     scales=[exact] if exact else None, pad=pad)
        _layout(page, group, extras, scale, positions, title_text)
        # sections kept in line with their views need room in that row or
        # column; if one ends up off the drawing area, try the next scale down
        if exact or all(_inside_regions(view_box(v), regions) for v in part_views(page)
                        if not v.isDerivedFrom("TechDraw::DrawViewDetail")):
            break
        smaller = [scale_value(r) for r in STANDARD_SCALES if scale_value(r) < scale - 1e-12]
        if not smaller:
            break
        max_scale = max(smaller)
    if not exact:
        # the plan sets extra views beside the group, but sections and
        # auxiliary views end up in line with their views: measure what was
        # really laid out, try bigger scales while it fits, and centre it
        for _ in range(3):
            up = next_standard_scale(scale, +1)
            if up is None or (limit is not None and up > limit + 1e-12):
                break
            _, pos = plan_page(page, group, extras, scales=[up], pad=pad)
            _layout(page, group, extras, up, pos, title_text)
            if _best_region(content_box(page, margins), regions) is not None:
                scale = up
                continue
            _, pos = plan_page(page, group, extras, scales=[scale], pad=pad)
            _layout(page, group, extras, scale, pos, title_text)
            break
        centre_content(page, group, extras, regions, margins)
    if not draw:
        return scale
    redraw([group] + list(group.Views) + list(extras))
    from . import annot
    annot.refresh_page(page)   # datums and frames follow their features
    try:
        from . import hatching
        hatching.refresh_page(page)   # hatch spacing is set on paper
        if App.GuiUp:
            # aligned sections are drawn in the background: line them up
            # and hatch them again once TechDraw has drawn them
            from . import commands
            for sec in extras:
                if sec.isDerivedFrom("TechDraw::DrawViewSection") and hatching.is_aligned(sec):
                    commands.hatch_when_drawn(page, sec)
    except Exception as e:
        App.Console.PrintWarning("DrawingKit hatching: %s\n" % e)
    return scale


# ---------------------------------------------------------------------------
# Scale up / down one standard step
# ---------------------------------------------------------------------------
def next_standard_scale(value, step):
    """The standard scale one step larger (step > 0) or smaller (step < 0)
    than ``value``, or None past the end of the list."""
    values = sorted(scale_value(r) for r in STANDARD_SCALES)
    if step > 0:
        bigger = [v for v in values if v > value * (1 + 1e-6)]
        return bigger[0] if bigger else None
    smaller = [v for v in values if v < value * (1 - 1e-6)]
    return smaller[-1] if smaller else None


def nearest_standard_scale(value):
    import math
    return min((scale_value(r) for r in STANDARD_SCALES),
               key=lambda v: abs(math.log(v / value)))


def set_detail_scale(det, scale):
    """Set a detail view's scale; its caption follows if it still shows the
    old scale as DrawingKit wrote it."""
    from . import standards
    old = view_scale(det)
    try:
        if det.Caption == standards.detail_caption(det.Reference, scale_text(old)):
            det.Caption = standards.detail_caption(det.Reference, scale_text(scale))
    except Exception:
        pass
    page = None
    try:
        page = det.findParentPage()
    except Exception:
        pass
    anchors = dimension_anchors(page, [det]) if page is not None else {}
    det.ScaleType = "Custom"
    det.Scale = scale
    det.touch()
    if page is not None:
        follow_scale(page, anchors)


def step_page_scale(page, step):
    """Put the page one standard scale up (step > 0) or down (step < 0) and
    lay it out as Fit to page does. Detail views keep their zoom over their
    base view. Returns the new scale; ValueError if there is no such scale
    or the views would not fit the sheet at it."""
    group, extras = page_content(page)
    if group is None:
        raise ValueError("This page has no projection group to scale")
    old = view_scale(group)
    target = next_standard_scale(old, step)
    if target is None:
        raise ValueError("The page is already at %s, the %s standard scale"
                         % (scale_text(old), "largest" if step > 0 else "smallest"))
    if step > 0:
        s, _ = plan_page(page, group, extras, max_scale=target,
                         pad=scaled_margins(page, old, target))
        if s < target - 1e-12:
            raise ValueError("The views do not fit the sheet at %s. Use a bigger "
                             "sheet, or Fit to page for the largest scale that fits."
                             % scale_text(target))
    details = []
    for d in page.Views:
        if d.isDerivedFrom("TechDraw::DrawViewDetail") and getattr(d, "BaseView", None):
            details.append((d, view_scale(d) / view_scale(d.BaseView)))
    scale = fit_page(page, max_scale=target)
    if step > 0 and scale <= old + 1e-12:
        raise ValueError("The sections do not fit the sheet at %s" % scale_text(target))
    for d, ratio in details:
        set_detail_scale(d, nearest_standard_scale(ratio * view_scale(d.BaseView)))
    if details:
        place_details(page)
        from . import annot
        annot.refresh_page(page)
    return scale


def has_own_scale(view):
    """Views that can be drawn at a scale of their own: details, sections
    and views outside the projection group (auxiliary, separate views).
    A projection group's views share the group's scale."""
    return (view.isDerivedFrom("TechDraw::DrawViewPart")
            and not view.isDerivedFrom("TechDraw::DrawProjGroupItem"))


def set_section_scale(view, scale):
    """A section or separate view at its own scale; a section's caption
    gains (or loses) the scale when it differs from the page's."""
    from . import standards
    page = None
    try:
        page = view.findParentPage()
    except Exception:
        pass
    if view.isDerivedFrom("TechDraw::DrawViewSection"):
        try:
            letter = view.SectionSymbol
            group = page_content(page)[0] if page is not None else None
            page_scale = view_scale(group) if group is not None else float(page.Scale)
            known = {standards.section_caption(letter, std) for std in ("ASME", "ISO")}
            known |= {standards.section_caption(letter, std, scale_text(view_scale(view)))
                      for std in ("ASME", "ISO")}
            if view.Caption in known or not view.Caption:
                own = abs(scale - page_scale) > 1e-9
                view.Caption = standards.section_caption(
                    letter, None, scale_text(scale) if own else None)
        except Exception:
            pass
    anchors = dimension_anchors(page, [view]) if page is not None else {}
    view.ScaleType = "Custom"
    view.Scale = scale
    view.touch()
    if page is not None:
        follow_scale(page, anchors)


def _set_own_scale(view, scale):
    if view.isDerivedFrom("TechDraw::DrawViewDetail"):
        set_detail_scale(view, scale)
    else:
        set_section_scale(view, scale)


def step_view_scale(view, step):
    """One standard scale up or down for a view that has its own scale (a
    detail, a section, a separate view). Returns the new scale; ValueError
    if it can't."""
    if not has_own_scale(view):
        raise ValueError("%s follows the page scale: with no detail or section view "
                         "selected, Scale up / down changes the whole page" % view.Label)
    old = view_scale(view)
    target = next_standard_scale(old, step)
    if target is None:
        raise ValueError("%s is already at %s, the %s standard scale"
                         % (view.Label, scale_text(old), "largest" if step > 0 else "smallest"))
    base = getattr(view, "BaseView", None)
    if view.isDerivedFrom("TechDraw::DrawViewDetail") and base is not None \
            and target <= view_scale(base) + 1e-12:
        raise ValueError("%s is at %s; a detail must be drawn larger than its "
                         "view (%s)" % (view.Label, scale_text(old), scale_text(view_scale(base))))
    _set_own_scale(view, target)
    _refresh_after_detail(view)
    return target


def _refresh_after_detail(view):
    page = None
    try:
        page = view.findParentPage()
    except Exception:
        pass
    if page is not None:
        place_details(page)
        from . import annot
        annot.refresh_page(page)


DETAIL_CAPTION = 9.0   # paper mm under a detail circle for its caption


def _detail_footprint(det, x, y):
    r = float(getattr(det.Radius, "Value", det.Radius)) * view_scale(det)
    return (x - r, y - r - DETAIL_CAPTION, x + r, y + r)


def _overlaps(a, b, gap):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def place_details(page, gap=3.0, step=2.0):
    """Move each detail view that runs off the drawing area or onto another
    view to the nearest free spot. Returns False if one has nowhere to go
    (it is left where it is)."""
    dets = [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawViewDetail")]
    if not dets:
        return True
    frame, title = drawing_area(getattr(page.Template, "Template", "") or "", *page_size(page))
    fixed = [view_box(v) for v in part_views(page)
             if not v.isDerivedFrom("TechDraw::DrawViewDetail")]
    fixed += table_boxes(page, pad=0.0) + ([title] if title else [])
    placed, ok = [], True

    def free(box):
        return (box[0] >= frame[0] + gap - 1e-6 and box[1] >= frame[1] + gap - 1e-6 and
                box[2] <= frame[2] - gap + 1e-6 and box[3] <= frame[3] - gap + 1e-6 and
                not any(_overlaps(box, b, gap) for b in fixed + placed))

    for det in dets:
        x, y = float(det.X), float(det.Y)
        box = _detail_footprint(det, x, y)
        if free(box):
            placed.append(box)
            continue
        best = None
        nx = int((frame[2] - frame[0]) / step) + 1
        ny = int((frame[3] - frame[1]) / step) + 1
        for i in range(nx):
            cx = frame[0] + i * step
            for j in range(ny):
                cy = frame[1] + j * step
                d = (cx - x) ** 2 + (cy - y) ** 2
                if best is not None and d >= best[0]:
                    continue
                if free(_detail_footprint(det, cx, cy)):
                    best = (d, cx, cy)
        if best is None:
            ok = False
            placed.append(box)
            continue
        det.X, det.Y = best[1], best[2]
        placed.append(_detail_footprint(det, best[1], best[2]))
    return ok


def parse_scale(text):
    """'1:3', '1/3', '3:4', '2.5:1', '0.75', '75%' -> (value, label).
    The label is how the title block shows it ('1 : 3'). ValueError if
    it isn't a scale."""
    t = (text or "").strip().replace(" ", "").replace(",", ".")
    if not t:
        raise ValueError("Type a scale such as 1:3, 3:4, 0.8 or 150%")
    label = None
    try:
        if t.endswith("%"):
            value = float(t[:-1]) / 100.0
        else:
            sep = ":" if ":" in t else ("/" if "/" in t else None)
            if sep:
                a, b = t.split(sep, 1)
                value = float(a) / float(b)
                label = "%g : %g" % (float(a), float(b))
            else:
                value = float(t)
    except (ValueError, ZeroDivisionError):
        raise ValueError("'%s' is not a scale: type one such as 1:3, 3:4, 0.8 or 150%%" % text)
    if not (1e-4 <= value <= 1e3):
        raise ValueError("'%s' is out of range" % text)
    return value, label or scale_text(value).replace(":", " : ")


def set_page_scale(page, value, label=None, draw=True):
    """Lay the page out at exactly ``value`` (any scale, not only standard
    ones); the title block shows ``label``. Detail views keep their zoom
    over their base view. ValueError if the views would not fit. Returns
    the scale, or with ``draw=False`` (a trial, not drawn) whether the
    detail views found room."""
    group, extras = page_content(page)
    if group is None:
        raise ValueError("This page has no projection group to scale")
    if not fits_at(page, group, extras, value):
        raise ValueError("The views do not fit the sheet at %s. Use a smaller "
                         "scale or a bigger sheet." % (label or scale_text(value)))
    pad = scaled_margins(page, view_scale(group), value)
    if pad and not fits_at(page, group, extras, value, pad=pad):
        ok = [s for s in (scale_value(r) for r in STANDARD_SCALES)
              if s < value and fits_at(page, group, extras, s,
                                       pad=scaled_margins(page, view_scale(group), s))]
        raise ValueError("At %s the views fit, but not their dimensions and callouts. "
                         "Largest standard scale that fits with them: %s."
                         % (label or scale_text(value), scale_text(max(ok)) if ok else "none"))
    details = [(d, view_scale(d) / view_scale(d.BaseView)) for d in page.Views
               if d.isDerivedFrom("TechDraw::DrawViewDetail") and getattr(d, "BaseView", None)]
    scale = fit_page(page, exact=value, title_text=label, draw=draw)
    for d, ratio in details:
        set_detail_scale(d, ratio * scale)
    roomy = place_details(page) if details else True
    if not draw:
        return roomy
    if details:
        from . import annot
        annot.refresh_page(page)
    return scale


def fill_scale(page, digits=2):
    """Largest scale (rounded down to ``digits`` significant figures) at
    which the page's views fit the drawing area."""
    import math
    group, extras = page_content(page)
    if group is None:
        raise ValueError("This page has no projection group to scale")
    layout = group_layout(group)
    lo, hi = 1e-4, 1e3
    if not fits_at(page, group, extras, lo, layout):
        raise ValueError("The views do not fit this sheet at any scale")
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if fits_at(page, group, extras, mid, layout):
            lo = mid
        else:
            hi = mid
    step = 10 ** (math.floor(math.log10(lo)) - (digits - 1))
    return math.floor(lo / step + 1e-9) * step


def fill_page(page):
    """Zoom the views to fill the drawing area at whatever scale that takes,
    leaving room for the detail views; the title block says NTS (not to
    scale). Returns the scale."""
    import math
    s = fill_scale(page)
    # try smaller scales until the details have room; trials are only laid
    # out, never drawn (a drawing at a trial scale could land last)
    for _ in range(25):
        if set_page_scale(page, s, "NTS", draw=False):
            break
        s *= 0.92
        step = 10 ** (math.floor(math.log10(s)) - 1)
        s = math.floor(s / step + 1e-9) * step
    return set_page_scale(page, s, "NTS")


def set_view_scale(view, value):
    """Any scale for a detail view (it must stay larger than its view), a
    section or a separate view."""
    if not has_own_scale(view):
        raise ValueError("%s follows the page scale: select a detail or section "
                         "view, or nothing to scale the page" % view.Label)
    base = getattr(view, "BaseView", None)
    if view.isDerivedFrom("TechDraw::DrawViewDetail") and base is not None \
            and value <= view_scale(base) + 1e-12:
        raise ValueError("A detail must be drawn larger than its view (%s)"
                         % scale_text(view_scale(base)))
    _set_own_scale(view, value)
    _refresh_after_detail(view)
    return value


def _inside_regions(box, regions, tol=0.01):
    """True if the box lies within one of the free regions (allowing for
    regions' padding: they are inset 5 mm from the frame)."""
    pad = 5.0
    return any(box[0] >= r[0] - pad - tol and box[1] >= r[1] - pad - tol and
               box[2] <= r[2] + pad + tol and box[3] <= r[3] + pad + tol for r in regions)


def section_side(sec):
    """'Right', 'Left', 'Up' or 'Down': where a section sits from its base
    view (from its viewing direction, so stepped sections work too)."""
    base = getattr(sec, "BaseView", None)
    if base is None:
        return None
    d, right, up = view_axes(base)
    sd = App.Vector(sec.Direction)
    sd.normalize()
    for side in ("Right", "Left", "Up", "Down"):
        if (sd - _section_vectors(base, side)[0]).Length < 1e-6:
            return side
    return None


def realign_sections(page, extras):
    """Put each section back in line with the view it was cut from (a fit
    lays extra views out in a column, which would turn a section of the Top
    view into what looks like a rotated side view), on the side its arrows
    point to, and clear of the other views."""
    views = [v for v in part_views(page)]
    for sec in extras:
        if not sec.isDerivedFrom("TechDraw::DrawViewSection"):
            continue
        side = section_side(sec)
        if side is None:
            continue
        hold_snap()
        base = sec.BaseView
        bx, by = view_center(base)
        s = view_scale(sec)
        ox, oy = section_align_offset(sec)   # line the part up, not the boxes
        bx, by = bx + ox * s, by + oy * s
        x0, y0, x1, y1 = view_box(base)
        sb = view_box(sec)
        # the box around the view's centre (not always symmetric)
        l, d_ = float(sec.X) - sb[0], float(sec.Y) - sb[1]
        r, u = sb[2] - float(sec.X), sb[3] - float(sec.Y)
        if side in ("Right", "Left"):
            x = max(float(sec.X), x1 + GAP + l) if side == "Right" else                 min(float(sec.X), x0 - GAP - r)
            sec.X, sec.Y = x, by
        else:
            y = max(float(sec.Y), y1 + GAP + d_) if side == "Up" else                 min(float(sec.Y), y0 - GAP - u)
            sec.X, sec.Y = bx, y
        # step further out while it lands on another view
        for _ in range(20):
            box = (float(sec.X) - l, float(sec.Y) - d_, float(sec.X) + r, float(sec.Y) + u)
            hit = None
            for v in views:
                if same(v, sec) or getattr(v, AXIS_PROP, ""):
                    continue     # linked views make way for sections afterwards
                try:
                    vb = view_box(v)
                except Exception:
                    continue
                if box[0] < vb[2] and vb[0] < box[2] and box[1] < vb[3] and vb[1] < box[3]:
                    hit = vb
                    break
            if hit is None:
                break
            if side == "Right":
                sec.X = hit[2] + GAP + l
            elif side == "Left":
                sec.X = hit[0] - GAP - r
            elif side == "Up":
                sec.Y = hit[3] + GAP + d_
            else:
                sec.Y = hit[1] - GAP - u


def set_hidden_lines(views, on=True):
    """Show (dashed) or hide the hidden edges of part views, e.g. a
    counterbore seen from the side. Sections and details are left alone."""
    changed = []
    for v in views:
        if not v.isDerivedFrom("TechDraw::DrawViewPart") or \
                v.isDerivedFrom("TechDraw::DrawViewSection") or \
                v.isDerivedFrom("TechDraw::DrawViewDetail"):
            continue
        if "HardHidden" in v.PropertiesList and bool(v.HardHidden) != bool(on):
            v.HardHidden = bool(on)
            changed.append(v)
    return changed


def has_hidden_lines(views):
    return any(getattr(v, "HardHidden", False) for v in views)


def redraw(views):
    """Recompute views one by one.

    In the GUI a touched view is not always redrawn by doc.recompute() after
    only its scale changed (checked on FreeCAD 1.1.4); recomputing each view
    directly is what makes it draw again.
    """
    for v in views:
        v.recompute()


def stale_views(views, tolerance=0.05):
    """Views whose drawing is not at their current scale (or not drawn yet).

    TechDraw draws in background threads and has no 'busy' flag in Python;
    if a redraw is requested while an older one is still running, the older
    result can land last. The expected size is the shape's exact outline, so
    a drawing more than 5 % off it is stale (non-standard scales, e.g. from
    Fill sheet, can be close together).
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


def dimension_anchors(page, views=None):
    """{view name: (scale, left, right, below, above)}: each view's scale and
    paper distances from its centre to its edges, taken before a scale
    change so its dimensions can follow (see follow_scale)."""
    info = {}
    for v in (views if views is not None else part_views(page)):
        try:
            cx, cy = view_center(v)
            b = view_box(v)
            info[v.Name] = (view_scale(v), max(cx - b[0], 1e-3), max(b[2] - cx, 1e-3),
                            max(cy - b[1], 1e-3), max(b[3] - cy, 1e-3))
        except Exception:
            pass
    return info


def _remap(p, lo, hi, k):
    """Offset p from a view's centre after the view is scaled by k: past an
    edge it keeps its paper distance from that edge, over the view it
    scales with it."""
    if p > hi:
        return hi * k + (p - hi)
    if p < -lo:
        return -lo * k + (p + lo)
    return p * k


def follow_scale(page, info):
    """Move the TechDraw dimensions of views whose scale changed (their
    text positions are paper mm from the view's centre, so they would stay
    put while the view shrinks or grows)."""
    for d in page.Views:
        try:
            if not d.isDerivedFrom("TechDraw::DrawViewDimension") or not d.References2D:
                continue
            v = d.References2D[0][0]
            if v is None or v.Name not in info:
                continue
            s0, l, r, b, t = info[v.Name]
            k = view_scale(v) / s0
            if abs(k - 1.0) < 1e-9:
                continue
            d.X = _remap(float(d.X), l, r, k)
            d.Y = _remap(float(d.Y), b, t, k)
        except Exception:
            continue


def apply_plan(page, group, extras, scale, positions):
    """Set the scale and positions, then make every view redraw.

    In the GUI TechDraw draws each view as soon as it exists, so views made
    before the scale was known keep their old drawing unless touched.
    """
    anchors = dimension_anchors(page, [v for v in part_views(page)
                                       if not v.isDerivedFrom("TechDraw::DrawViewDetail")])
    _apply_plan(page, group, extras, scale, positions)
    follow_scale(page, anchors)


def _apply_plan(page, group, extras, scale, positions):
    old = float(page.Scale) or 1.0
    page.Scale = scale
    set_title_block_scale(page, scale)
    for view in [group] + list(extras):
        if view is not group and view.ScaleType == "Custom":
            # a section or separate view given its own scale keeps its zoom
            # over the page, as a detail does
            set_section_scale(view, float(view.Scale) / old * scale)
        else:
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


def set_title_block_scale(page, scale, text=None):
    """Write the scale (or ``text``, e.g. 'NTS') into the title block's
    'scale' field, if it has one."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return False
    texts = dict(tpl.EditableTexts)
    keys = [k for k in texts if k.lower() == "scale"]
    if not keys:
        return False
    text = text or scale_text(scale).replace(":", " : ")
    for k in keys:
        texts[k] = text
    tpl.EditableTexts = texts
    return True


# ---------------------------------------------------------------------------
# Title block from the part, sheet numbers
# ---------------------------------------------------------------------------
def page_part(page):
    """The 3D object drawn on the page (first projection group's source)."""
    for v in page.Views:
        if v.isDerivedFrom("TechDraw::DrawViewPart") or v.isDerivedFrom("TechDraw::DrawProjGroup"):
            src = list(getattr(v, "Source", []) or [])
            if src:
                return src[0]
    return None


def part_material(obj):
    mat = getattr(obj, "ShapeMaterial", None)
    name = getattr(mat, "Name", "") if mat is not None else ""
    if name and name.lower() != "default":
        return name
    m = getattr(obj, "Material", None)   # older/other objects
    if isinstance(m, dict):
        return m.get("Name", "")
    return ""


def author_name():
    return App.ParamGet("User parameter:BaseApp/Preferences/Document").GetString("prefAuthor", "")


def _blank(value, defaults=()):
    v = (value or "").strip()
    return not v or v in defaults


def fill_title_block(page, part=None):
    """Fill empty (or still default) title-block fields from the part.
    Fields the user has typed in are left alone. Returns the fields set."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return {}
    part = part or page_part(page)
    texts = dict(tpl.EditableTexts)
    doc = page.Document
    changed = {}

    def put(key, value, defaults=()):
        if key in texts and value and _blank(texts[key], defaults):
            texts[key] = value
            changed[key] = value

    if part is not None:
        put("title", part.Label, (doc.Label, doc.Name, "ISO 5457 template"))
        put("part_material", part_material(part))
        put("drawing_number", part.Label.upper().replace(" ", "-"), ("DN",))
    put("creator", author_name(), ("A. Nemesis",))
    if changed:
        tpl.EditableTexts = texts
    try:
        from . import titleblock
        changed.update(titleblock.apply_defaults(page))   # saved defaults in empty fields
        titleblock.match_tolerance_mode(page)   # size table or X.X values, not both
    except Exception as e:
        App.Console.PrintWarning("DrawingKit title block defaults: %s\n" % e)
    renumber_sheets(doc)
    return changed


def renumber_sheets(doc, force=False):
    """'1 / N', '2 / N'... ('1 OF N' in an ANSI title block) in the order
    the pages were made."""
    from . import titleblock
    pages_ = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]
    n = len(pages_)
    for i, p in enumerate(pages_):
        tpl = p.Template
        if tpl is None or "EditableTexts" not in tpl.PropertiesList:
            continue
        texts = dict(tpl.EditableTexts)
        if "sheet_number" in texts:
            want = titleblock.sheet_text(i + 1, n, titleblock.sheet_of(p, texts))
            if texts["sheet_number"] != want:
                texts["sheet_number"] = want
                tpl.EditableTexts = texts
    return n


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
    fill_title_block(page, objs[0])
    return group


# ---------------------------------------------------------------------------
# Section views
# ---------------------------------------------------------------------------
def view_box(view):
    """Approximate (x0, y0, x1, y1) of a placed view on the page."""
    s = float(view.getScale()) if hasattr(view, "getScale") else float(view.Scale)
    x, y = float(view.X), float(view.Y)
    if view.isDerivedFrom("TechDraw::DrawViewDetail"):
        # only the circle is drawn, not the whole part
        r = float(getattr(view.Radius, "Value", view.Radius)) * s
        return (x - r, y - r, x + r, y + r)
    if view.isDerivedFrom("TechDraw::DrawViewSection"):
        # what TechDraw drew: an aligned section is unfolded and a section
        # is centred on what it keeps, so the source's size is wrong. A
        # drawing still at an old scale is scaled to the current one.
        ext = drawn_extent(view)
        f = section_stale_factor(view, ext) if ext is not None else None
        if f is not None:
            k = s * f
            return (x + ext[0] * k, y + ext[1] * k, x + ext[2] * k, y + ext[3] * k)
    w, h = extra_view_size(view)
    rot = math.radians(float(getattr(getattr(view, "Rotation", 0.0), "Value",
                                     getattr(view, "Rotation", 0.0)) or 0.0))
    if abs(math.sin(rot)) > 1e-9:
        w, h = (abs(w * math.cos(rot)) + abs(h * math.sin(rot)),
                abs(w * math.sin(rot)) + abs(h * math.cos(rot)))
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
    from . import standards
    sec.Caption = standards.section_caption(letter)
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


_SNAP = {"n": 0, "old": True}
SNAP_PREFS = "User parameter:BaseApp/Preferences/Mod/TechDraw/General"


def hold_snap(ms=8000):
    """Keep TechDraw's 'snap views' off for a while. Its snapping moves a
    section's drawing on screen (not the document) to its own idea of in
    line, which is off for aligned and stepped sections; DrawingKit lines
    them up on the part itself."""
    if not App.GuiUp:
        return
    try:
        from PySide import QtCore
    except Exception:
        return
    g = App.ParamGet(SNAP_PREFS)
    if _SNAP["n"] == 0:
        _SNAP["old"] = g.GetBool("SnapViews", True)
        g.SetBool("SnapViews", False)
    _SNAP["n"] += 1

    def release():
        _SNAP["n"] = max(0, _SNAP["n"] - 1)
        if _SNAP["n"] == 0:
            g.SetBool("SnapViews", _SNAP["old"])
    QtCore.QTimer.singleShot(ms, release)


def section_align_offset(sec):
    """See hatching.align_offset: (dx, dy) model units, or (0, 0)."""
    try:
        from . import hatching
        off = hatching.align_offset(sec)
    except Exception as e:
        App.Console.PrintWarning("DrawingKit section alignment: %s\n" % e)
        off = None
    return off or (0.0, 0.0)


def _place_section(page, base, sec, direction, shape, place):
    hold_snap()
    bx, by = view_center(base)
    ox, oy = section_align_offset(sec)
    s0 = view_scale(base)
    bx, by = bx + ox * s0, by + oy * s0
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


def shape_centre(shape, direction=None):
    """The point TechDraw centres a view of ``shape`` on: the exact
    bounding-box centre. Shape.BoundBox is not exact once the part has been
    shown in 3D (it is then taken from the display mesh, off by up to a few
    hundredths of a mm on round parts), which put DrawingKit's points beside
    TechDraw's edges."""
    direction = App.Vector(direction) if direction is not None else App.Vector(0, 0, 1)
    try:
        key = (shape.hashCode(), round(direction.x, 6), round(direction.y, 6), round(direction.z, 6))
    except Exception:
        key = None
    if key is not None and key in _CENTRES:
        return App.Vector(_CENTRES[key])
    try:
        import TechDraw
        c = App.Vector(TechDraw.findCentroid(shape, direction))
    except Exception:
        try:
            c = App.Vector(shape.optimalBoundingBox(False, False).Center)
        except Exception:
            c = App.Vector(shape.BoundBox.Center)
    if key is not None:
        if len(_CENTRES) > 200:
            _CENTRES.clear()
        _CENTRES[key] = App.Vector(c)
    return c


_CENTRES = {}


_OFFSETS = {}


def _drawn_circles(view):
    """[(radius, cx, cy)] of the circles and arcs TechDraw drew (unscaled, Y up)."""
    out = []
    i = 0
    while i < 20000:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            break
        c = getattr(e, "Curve", None)
        if c is not None and c.TypeId in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            out.append((c.Radius, c.Center.x, c.Center.y))
        i += 1
    return out


def _outline_offset(view, shape, centre, d, right, up, limit=0.5):
    """The drift measured from the whole outline: centre of what TechDraw
    drew minus the centre of the part's exact extent in the view. Plain
    views only (a section or detail draws less than the whole part); a
    difference over ``limit`` mm isn't drift, so (0, 0)."""
    if view.isDerivedFrom("TechDraw::DrawViewSection") or \
            view.isDerivedFrom("TechDraw::DrawViewDetail"):
        return (0.0, 0.0)
    ext = drawn_extent(view)
    if ext is None:
        return None
    try:
        m = App.Matrix(right.x, right.y, right.z, 0, up.x, up.y, up.z, 0,
                       d.x, d.y, d.z, 0, 0, 0, 0, 1)
        moved = shape.copy()
        moved.translate(-centre)
        moved = moved.transformGeometry(m)
        bb = moved.optimalBoundingBox(False, False)
    except Exception:
        return (0.0, 0.0)
    ox = (ext[0] + ext[2]) / 2.0 - (bb.XMin + bb.XMax) / 2.0
    oy = (ext[1] + ext[3]) / 2.0 - (bb.YMin + bb.YMax) / 2.0
    if math.hypot(ox, oy) > limit or math.hypot(ox, oy) < 1e-6:
        return (0.0, 0.0)
    return (ox, oy)


def view_offset(view, shape=None):
    """(dx, dy) to add to DrawingKit's view coordinates so they land on what
    TechDraw drew. TechDraw's centring and the model's bounding box can
    disagree by a few hundredths of a mm on round parts; measured once per
    view from the round features both see end-on. (0, 0) when there is
    nothing to measure or the view isn't drawn yet."""
    shape = shape or source_shape(view.Source)
    d, right, up = view_axes(view)
    try:
        key = (view.Document.Name, view.Name, shape.hashCode(),
               round(d.x, 6), round(d.y, 6), round(d.z, 6), round(right.x, 6), round(right.y, 6))
    except Exception:
        return 0.0, 0.0
    if key in _OFFSETS:
        return _OFFSETS[key]
    drawn = _drawn_circles(view)
    centre = shape_centre(shape, d)
    if not drawn:
        off = _outline_offset(view, shape, centre, d, right, up)
        if off is None:
            return 0.0, 0.0                  # not drawn yet: measure next time
        _OFFSETS[key] = off
        return off
    diffs = []
    for face in _round_faces(shape):
        axis = App.Vector(face.Surface.Axis)
        if abs(abs(axis.normalize().dot(d)) - 1.0) > 1e-6:
            continue
        v = App.Vector(face.Surface.Center) - centre
        mx, my, r = v.dot(right), v.dot(up), face.Surface.Radius
        near = [(cx - mx, cy - my) for rr, cx, cy in drawn
                if abs(rr - r) < 1e-3 and math.hypot(cx - mx, cy - my) < 1.0]
        if near:
            diffs.append(min(near, key=lambda q: math.hypot(*q)))
    off = (0.0, 0.0)
    if diffs:
        xs = sorted(q[0] for q in diffs)
        ys = sorted(q[1] for q in diffs)
        mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
        agree = [q for q in diffs if abs(q[0] - mx) < 0.01 and abs(q[1] - my) < 0.01]
        if len(agree) * 2 > len(diffs) and math.hypot(mx, my) > 1e-6:
            off = (mx, my)
    if len(_OFFSETS) > 500:
        _OFFSETS.clear()
    _OFFSETS[key] = off
    return off


def model_to_local(view, point, shape=None):
    """3D point -> unscaled view coordinates (Y up), as cosmetic lines want.

    TechDraw centres a view on the bounding-box centre of its source shape
    (corrected by view_offset where TechDraw's drawing says otherwise).
    """
    shape = shape or source_shape(view.Source)
    d, right, up = view_axes(view)
    v = App.Vector(point) - shape_centre(shape, d)
    ox, oy = view_offset(view, shape)
    return v.dot(right) + ox, v.dot(up) + oy


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
    ox, oy = view_offset(view, shape)
    return shape_centre(shape, d) + right * ((px - cx) / s - ox) + up * ((py - cy) / s - oy)


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
    if parent is None or not axis:
        return False
    px, py = view_center(parent)
    if axis.startswith("N:"):
        # auxiliary view: stays on the line through the parent along the
        # normal of the edge it was projected from
        ang = math.radians(float(axis[2:]))
        nx, ny = math.cos(ang), math.sin(ang)
        t = (float(view.X) - px) * nx + (float(view.Y) - py) * ny
        tx, ty = px + nx * t, py + ny * t
        if abs(float(view.X) - tx) > 1e-6 or abs(float(view.Y) - ty) > 1e-6:
            view.X, view.Y = tx, ty
            return True
        return False
    if axis == "H" and abs(float(view.Y) - py) > 1e-6:
        view.Y = py
        return True
    if axis == "V" and abs(float(view.X) - px) > 1e-6:
        view.X = px
        return True
    return False


def _linked_dir(view):
    """Unit page direction a linked view slides along away from its parent."""
    parent = getattr(view, PARENT_PROP, None)
    axis = getattr(view, AXIS_PROP, "")
    if parent is None or not axis:
        return None
    px, py = view_center(parent)
    if axis.startswith("N:"):
        a = math.radians(float(axis[2:]))
        return math.cos(a), math.sin(a)
    if axis == "H":
        return (1.0 if float(view.X) >= px else -1.0), 0.0
    if axis == "V":
        return 0.0, (1.0 if float(view.Y) >= py else -1.0)
    return None


def clear_linked(page, view, step=2.0, reach=400.0):
    """Slide a linked view (projected or auxiliary) along its line away
    from its parent until it is clear of every other view. True if moved."""
    follow_parent(view)
    d = _linked_dir(view)
    if d is None:
        return False
    others = [v for v in part_views(page) if not same(v, view)]
    x0, y0 = float(view.X), float(view.Y)
    b = view_box(view)
    l, dn, r, u = x0 - b[0], y0 - b[1], b[2] - x0, b[3] - y0

    def hits(x, y):
        box = (x - l - GAP / 2, y - dn - GAP / 2, x + r + GAP / 2, y + u + GAP / 2)
        for v in others:
            try:
                vb = view_box(v)
            except Exception:
                continue
            if box[0] < vb[2] and vb[0] < box[2] and box[1] < vb[3] and vb[1] < box[3]:
                return True
        return False

    t = 0.0
    while t <= reach and hits(x0 + d[0] * t, y0 + d[1] * t):
        t += step
    if t == 0.0 or t > reach:
        return False
    view.X, view.Y = x0 + d[0] * t, y0 + d[1] * t
    return True


def clear_all_linked(page):
    moved = False
    for v in part_views(page):
        if getattr(v, AXIS_PROP, ""):
            moved = clear_linked(page, v) or moved
    return moved


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
# Auxiliary views
# ---------------------------------------------------------------------------
def _straight_edge(view, edge_name):
    """(p0, p1) unscaled view coords (Y up) of a straight edge 'EdgeN'."""
    e = view.getEdgeByIndex(int(edge_name.replace("Edge", "")))
    c = getattr(e, "Curve", None)
    if c is None or c.TypeId not in ("Part::GeomLine", "Part::GeomLineSegment") \
            or len(e.Vertexes) < 2:
        raise ValueError("Pick a straight edge for the auxiliary view")
    return App.Vector(e.Vertexes[0].Point), App.Vector(e.Vertexes[-1].Point)


def auxiliary_setup(page, base, edge_name, point):
    """Directions, rotation and position for an auxiliary view of the face
    seen edge-on as ``edge_name`` in ``base``, placed towards page ``point``.

    Returns (direction, xdirection, rotation_deg, (x, y), normal_angle_deg)."""
    p0, p1 = _straight_edge(base, edge_name)
    ex, ey = p1.x - p0.x, p1.y - p0.y
    n_ = math.hypot(ex, ey)
    ex, ey = ex / n_, ey / n_
    nx, ny = -ey, ex
    cx, cy = view_center(base)
    if (point[0] - cx) * nx + (point[1] - cy) * ny < 0:
        nx, ny = -nx, -ny
    # turn the view so its 'up' runs along the edge, by no more than 90 deg
    rotation = math.degrees(math.atan2(ey, ex)) - 90.0
    if rotation <= -90.0:
        rotation += 180.0
        ex, ey = -ex, -ey
    elif rotation > 90.0:
        rotation -= 180.0
        ex, ey = -ex, -ey
    d, right, up = view_axes(base)
    e3 = right * ex + up * ey
    n3 = right * nx + up * ny
    # An auxiliary view shows the slanted face itself: look at the part from
    # the side it is placed on (whatever the page's projection angle).
    vd = n3
    vx = e3.cross(vd)                    # up = edge direction -> X = up x D
    vx.normalize()
    t = (point[0] - cx) * nx + (point[1] - cy) * ny
    pos = (cx + nx * t, cy + ny * t)
    return vd, vx, rotation, pos, math.degrees(math.atan2(ny, nx))


def add_auxiliary_view(page, base, edge_name, point):
    """Auxiliary view square-on to the face that ``edge_name`` shows edge-on."""
    vd, vx, rot, pos, nang = auxiliary_setup(page, base, edge_name, point)
    doc = page.Document
    view = doc.addObject("TechDraw::DrawViewPart", "AuxView")
    page.addView(view)
    view.Source = base.Source
    view.Direction = vd
    view.XDirection = vx
    view.Rotation = rot
    view.ScaleType = "Page"
    view.Scale = page.Scale
    view.X, view.Y = pos
    view.Label = "Auxiliary view from %s" % base.Label
    view.addProperty("App::PropertyLink", PARENT_PROP, "DrawingKit",
                     "View this one was projected from; it stays lined up with it")
    view.addProperty("App::PropertyString", AXIS_PROP, "DrawingKit",
                     "N:<angle> = slides along that direction from the parent")
    setattr(view, PARENT_PROP, base)
    setattr(view, AXIS_PROP, "N:%.6f" % nang)
    clear_linked(page, view)
    view.touch()
    return view


# ---------------------------------------------------------------------------
# Aligned sections (cut line bends through a centre, e.g. bolt-circle holes)
# ---------------------------------------------------------------------------
def add_aligned_section(page, base, points, place, letter=None):
    """TechDraw 'Aligned' complex section along clicked page points
    (outer point, centre, outer point...). The last segment sets the view
    direction; the others are swung into its plane."""
    if len(points) < 3:
        raise ValueError("Click at least three points: out, centre, out")
    pts = [tuple(map(float, p)) for p in points]
    # the last segment is snapped to horizontal/vertical: it is the one seen true
    (ax, ay), (bx, by) = pts[-2], pts[-1]
    if abs(bx - ax) >= abs(by - ay):
        pts[-1] = (bx, ay)
    else:
        pts[-1] = (ax, by)
    direction = cut_orientation(pts[-2:], place)
    shape = source_shape(base.Source)
    model = [page_to_model(base, x, y) for x, y in pts]
    margin = 5.0 / view_scale(base)
    first = _past_outline(model[0], model[0] - model[1], shape, margin)
    last = _past_outline(model[-1], model[-1] - model[-2], shape, margin)
    import Part
    doc = page.Document
    sec = _new_section(page, base, letter, "TechDraw::DrawComplexSection")
    tool = doc.addObject("Part::Feature", "CutLine_%s" % sec.SectionSymbol)
    tool.Label = "Cut line %s-%s" % (sec.SectionSymbol, sec.SectionSymbol)
    tool.Shape = Part.makePolygon([first] + model[1:-1] + [last])
    if App.GuiUp and tool.ViewObject is not None:
        tool.ViewObject.Visibility = False
    sec.CuttingToolWireObject = tool
    sec.ProjectionStrategy = "Aligned"
    sec.SectionOrigin = model[-2]
    sec.SectionDirection = "Aligned"
    sec_dir, sec_x = _section_vectors(base, direction)
    sec.Direction = sec_dir
    sec.SectionNormal = sec_dir
    sec.XDirection = sec_x
    _place_section(page, base, sec, direction, shape, place)
    sec.touch()
    return sec


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
    from . import standards
    det.Caption = standards.detail_caption(letter, scale_text(scale))
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
    cover = {}          # (line key, radius) -> angle covered round that axis
    endon = {}          # (centre key, radius) -> angle of the faces seen end-on
    for face in _round_faces(shape):
        axis = App.Vector(face.Surface.Axis)
        axis.normalize()
        centre = face.Surface.Center
        radius = face.Surface.Radius
        if abs(abs(axis.dot(d)) - 1.0) < 1e-6:
            lx, ly = model_to_local(view, centre, shape)
            key = (round(lx, 4), round(ly, 4))
            try:
                u0, u1 = face.ParameterRange[:2]
            except Exception:
                u0, u1 = 0.0, 2 * math.pi
            ek = (key, round(radius, 4))
            endon[ek] = endon.get(ek, 0.0) + (u1 - u0)
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
            try:
                u0, u1 = face.ParameterRange[:2]
            except Exception:
                u0, u1 = 0.0, 2 * math.pi
            ck = (key, round(radius, 4))
            cover[ck] = cover.get(ck, 0.0) + (u1 - u0)
    # an axis line only where something goes all the way round it (a hole,
    # shaft or boss), not for part-round ends such as a keyway's seen side-on
    full = {ck[0] for ck, ang in cover.items() if ang >= 2 * math.pi - 1e-3}
    lines = {k: v for k, v in lines.items() if k in full}
    # a centre mark for a hole or boss (all, or nearly all, the way round: a
    # keyway may break into it) and a slot's half-round end; not for the
    # lobes of a hexalobe or other profile (part round, more than half)
    for (key, radius), ang in endon.items():
        if ang >= 2 * math.pi * 5.0 / 6.0 - 1e-3 or abs(ang - math.pi) < 0.02:
            marks[key] = max(marks.get(key, 0.0), radius + over)
            radii[key] = min(radii.get(key, radius), radius)  # innermost (the hole)
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
    from . import lineweights
    weight = lineweights.centre_line()     # the sheet's thin line
    tags = []
    for (x0, y0), (x1, y1) in segs:
        tag = view.makeCosmeticLine(App.Vector(x0, y0, 0), App.Vector(x1, y1, 0),
                                    CENTER_LINE_STYLE, weight)
        if tag:
            tags.append(tag)
    for cx, cy, R in circles:
        tag = view.makeCosmeticCircle(App.Vector(cx, cy, 0), R,
                                      CENTER_LINE_STYLE, weight)
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
        if v.isDerivedFrom("TechDraw::DrawViewPart") and "CoarseView" in v.PropertiesList                 and "DrawingKitUncutOf" not in v.PropertiesList:   # overlays on a section
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
