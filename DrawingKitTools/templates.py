# SPDX-License-Identifier: LGPL-2.1-or-later
"""DrawingKit's own drawing templates (frame + title block), made as SVG.

ISO 5457 frame (20 mm binding margin on the left, 10 mm elsewhere, centring
marks, zone grid) and a 180 x 36 mm title block in the bottom-right corner
with every field in its own cell. The field names match FreeCAD's own
templates, so TechDraw's autofill and editing work as usual.
"""

import os

import FreeCAD as App

INCH = 25.4

# Landscape sizes in mm (width, height)
SIZES = {
    "A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
    "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
    "ANSI A": (11 * INCH, 8.5 * INCH), "ANSI B": (17 * INCH, 11 * INCH),
    "ANSI C": (22 * INCH, 17 * INCH), "ANSI D": (34 * INCH, 22 * INCH),
    "ANSI E": (44 * INCH, 34 * INCH),
}
ORIENTATIONS = ["Landscape", "Portrait"]

TB_W = 180.0
ROW_H = 9.0
TB_H = 4 * ROW_H
LABEL_FONT = 2.2
VALUE_FONT = 3.5
TITLE_FONT = 5.0

# (row, x0, x1, label, editable name, default value, autofill, font size, align)
# rows count from the top of the title block.
CELLS = [
    (0, 0, 115, "Owner / company", "legal_owner_1", "", "organization", VALUE_FONT, "start"),
    (0, 115, 140, "Scale", "scale", "1 : 1", "scale", VALUE_FONT, "middle"),
    (1, 0, 115, "Title", "title", "", "title", TITLE_FONT, "start"),
    (1, 115, 180, "Created by", "creator", "", "author", VALUE_FONT, "start"),
    (2, 0, 70, "Material", "part_material", "", None, VALUE_FONT, "start"),
    (2, 70, 115, "General tolerances", "general_tolerances", "ISO 2768-m", None, VALUE_FONT, "start"),
    (2, 115, 180, "Approved by", "approval_person", "", None, VALUE_FONT, "start"),
    (3, 0, 70, "Drawing number", "drawing_number", "", None, VALUE_FONT, "start"),
    (3, 70, 115, "Date", "date_of_issue", "", "date", VALUE_FONT, "start"),
    (3, 115, 140, "Revision", "revision_index", "A", None, VALUE_FONT, "middle"),
    # no autofill: FreeCAD 1.1's "sheet" autofill writes "0 / 1"
    (3, 140, 180, "Sheet", "sheet_number", "1 / 1", None, VALUE_FONT, "middle"),
]
PROJECTION_CELL = (0, 140, 180)
# optional top row: units, general tolerances by decimal places, angles, finish
TOL_CELLS = [
    (0, 0, 30, "Dimensions in", "units", "", None, VALUE_FONT, "start"),
    (0, 30, 55, "Tol. X.X", "tol_1", "", None, VALUE_FONT, "start"),
    (0, 55, 80, "Tol. X.XX", "tol_2", "", None, VALUE_FONT, "start"),
    (0, 80, 110, "Tol. X.XXX", "tol_3", "", None, VALUE_FONT, "start"),
    (0, 110, 135, "Angles", "tol_angle", "", None, VALUE_FONT, "start"),
    (0, 135, 180, "Finish", "finish", "", None, VALUE_FONT, "start"),
]


# the row when general tolerances are given by a size table on the sheet
TOL_CELLS_TABLE = [
    (0, 0, 30, "Dimensions in", "units", "", None, VALUE_FONT, "start"),
    (0, 30, 180, "Finish", "finish", "", None, VALUE_FONT, "start"),
]


def cells(tol_row=False, tol_mode="places"):
    """Title block cells, rows from the top (the tolerance row first)."""
    if not tol_row:
        return list(CELLS)
    row = TOL_CELLS_TABLE if tol_mode == "table" else TOL_CELLS
    return list(row) + [(c[0] + 1,) + tuple(c[1:]) for c in CELLS]


def parse_name(path):
    """(size, orientation, first_angle) of a DrawingKit template file, or None."""
    import re
    m = re.match(r"DrawingKit_(.+?)_(Landscape|Portrait)_(1st|3rd)"
                 r"(_tol|_ansi|_toltab|_ansitab|_c-[A-Za-z0-9-]+)?\.svg$",
                 os.path.basename(path or ""))
    if not m:
        return None
    size = m.group(1)
    for s in SIZES:
        if s.replace(" ", "") == size:
            size = s
    return size, m.group(2), m.group(3) == "1st"


def parse_style(path):
    """'iso', 'ansi' or 'custom:<design>' of a DrawingKit template file."""
    import re
    m = re.search(r"_(1st|3rd)(_tol|_ansi|_toltab|_ansitab|_c-[A-Za-z0-9-]+)?\.svg$",
                  os.path.basename(path or ""))
    if not m or not m.group(2):
        return "iso"
    if m.group(2).startswith("_c-"):
        return "custom:" + m.group(2)[3:]
    return "ansi" if m.group(2).startswith("_ansi") else "iso"


def custom_design(style):
    """The design of a 'custom:<name>' style, or None."""
    if not style or not str(style).startswith("custom:"):
        return None
    from . import blocks
    return blocks.find(style[7:])


def fits(style, size, orientation):
    """Does that title block fit inside the sheet's frame?"""
    W, H = page_size(size, orientation)
    if style == "ansi":
        return W - 30.0 >= ANSI_MIN_FRAME_W
    d = custom_design(style)
    if d is not None:
        return d["width"] <= W - 30.0 + 1e-6 and d["height"] <= H - 20.0 - 20.0
    return not str(style).startswith("custom:")


def page_size(size, orientation):
    if size not in SIZES or orientation not in ORIENTATIONS:
        raise ValueError("Unknown sheet %s %s" % (size, orientation))
    w, h = round(SIZES[size][0], 2), round(SIZES[size][1], 2)
    return (h, w) if orientation == "Portrait" else (w, h)


def _ff():
    """font-family for template text: the drawing font."""
    from . import fonts
    return fonts.css_family()


def _wf():
    from . import fonts
    return fonts.width_factor()


def _esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _zone_labels(n, letters):
    if letters:
        pool = [c for c in "ABCDEFGHJKLMNPQRSTUVWXYZ"]  # ISO: no I, O
        return [pool[i % len(pool)] for i in range(n)]
    return [str(i + 1) for i in range(n)]


def _zones(start, end, centre, step=50.0):
    """Zone boundaries: every `step` mm out from the centring mark."""
    cuts = {round(centre, 3)}
    x = centre - step
    while x > start + 10.0:          # no sliver of a zone at the corner
        cuts.add(round(x, 3))
        x -= step
    x = centre + step
    while x < end - 10.0:
        cuts.add(round(x, 3))
        x += step
    return [start] + sorted(cuts) + [end]


def projection_symbol(cx, cy, first_angle, h=7.0):
    """ISO 5456-2 projection symbol centred on (cx, cy), about h mm tall."""
    d1, d2 = h * 0.5, h            # small and large cone diameters
    length = h * 1.0               # cone length in the side view
    gap = h * 0.45
    total = length + gap + d2
    x = cx - total / 2.0
    parts = []

    def cone(x0):
        # side view: narrow end towards the circles in third angle,
        # away from them in first angle
        if first_angle:
            xs, xl = x0, x0 + length      # small end left, large end right
        else:
            xs, xl = x0 + length, x0      # large end left, small end right
        return ('<path d="M %.3f,%.3f L %.3f,%.3f L %.3f,%.3f L %.3f,%.3f Z"/>'
                % (xs, cy - d1 / 2, xl, cy - d2 / 2, xl, cy + d2 / 2, xs, cy + d1 / 2))

    def circles(ccx):
        return ('<circle cx="%.3f" cy="%.3f" r="%.3f"/><circle cx="%.3f" cy="%.3f" r="%.3f"/>'
                % (ccx, cy, d2 / 2, ccx, cy, d1 / 2))

    if first_angle:
        parts.append(cone(x))
        ccx = x + length + gap + d2 / 2
        parts.append(circles(ccx))
    else:
        ccx = x + d2 / 2
        parts.append(circles(ccx))
        parts.append(cone(x + d2 + gap))
    # centre lines
    parts.append('<path style="stroke-width:0.18;stroke-dasharray:1.5,0.5,0.2,0.5" '
                 'd="M %.3f,%.3f H %.3f M %.3f,%.3f V %.3f"/>'
                 % (x - 0.8, cy, x + total + 0.8, ccx, cy - d2 / 2 - 0.8, cy + d2 / 2 + 0.8))
    return '<g id="projection_symbol" style="fill:none;stroke:#000000;stroke-width:0.35">%s</g>' \
        % "".join(parts)


# ---------------------------------------------------------------------------
# ANSI (ASME Y14.1 style) title block: a tolerance block on the left, the
# company / title / numbers block on the right
# ---------------------------------------------------------------------------
ANSI_TOL_W = 66.0
ANSI_MAIN_W = 134.0
ANSI_W = ANSI_TOL_W + ANSI_MAIN_W
ANSI_ROWS = [12.0, 12.0, 9.0, 9.0, 9.0]          # company, title, numbers, material, people
ANSI_H = sum(ANSI_ROWS)
ANSI_MIN_FRAME_W = ANSI_W + 10.0                 # narrower sheets keep the ISO block

# main block cells: (row, x0, x1, label, field, default, autofill, font, align)
ANSI_CELLS = [
    (2, 0, 70, "DRAWING NO.", "drawing_number", "", None, 4.0, "start"),
    (2, 70, 90, "REV", "revision_index", "A", None, 4.0, "middle"),
    (2, 90, 134, "SHEET", "sheet_number", "1 OF 1", None, 3.5, "middle"),
    (3, 0, 70, "MATERIAL", "part_material", "", None, 3.0, "start"),
    (3, 70, 134, "FINISH", "finish", "", None, 3.0, "start"),
    (4, 0, 27, "DRAWN", "creator", "", "author", 2.5, "start"),
    (4, 27, 54, "CHECKED", "checked_by", "", None, 2.5, "start"),
    (4, 54, 81, "APPROVED", "approval_person", "", None, 2.5, "start"),
    (4, 81, 108, "DATE", "date_of_issue", "", "date", 2.5, "start"),
    (4, 108, 134, "SCALE", "scale", "1 : 1", "scale", 2.5, "middle"),
]
# tolerance block lines: (static text, field or None, default); fields are
# shared with the ISO tolerance row so one set of defaults serves both
# each line: [(x offset, static text, field or None, default), ...]
ANSI_TOL_LINES = [
    [(0, "UNLESS OTHERWISE SPECIFIED:", None, "")],
    [(0, "DIMENSIONS ARE IN", "units", "INCHES")],
    [(0, "TOLERANCES:", None, "")],
    [(2, ".X", "tol_1", ""), (32, ".XX", "tol_2", "")],
    [(2, ".XXX", "tol_3", ""), (32, ".XXXX", "tol_4", "")],
    [(2, "ANGLES", "tol_angle", "")],
    [(0, "INTERPRET PER", "general_tolerances", "ASME Y14.5-2018")],
    [(0, "", "surface_note", "")],
    [(0, "", "edge_note", "")],
]
# ... and when general tolerances are given by a size table on the sheet
ANSI_TOL_LINES_TABLE = [
    [(0, "UNLESS OTHERWISE SPECIFIED:", None, "")],
    [(0, "DIMENSIONS ARE IN", "units", "INCHES")],
    [(0, "TOLERANCES: SEE TABLE", None, "")],
    [(0, "INTERPRET PER", "general_tolerances", "ASME Y14.5-2018")],
    [(0, "", "surface_note", "")],
    [(0, "", "edge_note", "")],
]
ANSI_FIELDS = (["legal_owner_1", "company_address", "title", "title_2", "sheet_size"]
               + [c[4] for c in ANSI_CELLS]
               + [f for line in ANSI_TOL_LINES for _, _, f, _ in line if f])


def sheet_letter(size):
    """'C' for ANSI C, 'A3' for A3."""
    return size.replace("ANSI ", "")


def _ansi_block(a, fx1, fy1, size, first_angle, tol_mode="places"):
    """Write the ANSI title block, its bottom-right corner on (fx1, fy1)."""
    x0, y0 = fx1 - ANSI_W, fy1 - ANSI_H
    mx = x0 + ANSI_TOL_W                     # main block's left edge
    ys = [y0]
    for h in ANSI_ROWS:
        ys.append(ys[-1] + h)
    ed = '<text x="%g" y="%g" freecad:editable="%s"%s style="font-size:%gpx;text-anchor:%s%s">' \
         '<tspan>%s</tspan></text>'

    def edit(x, y, name, default, font, anchor="start", auto=None, bold=False):
        a(ed % (x, y, name, ' freecad:autofill="%s"' % auto if auto else "", font, anchor,
                ";font-weight:bold" if bold else "", _esc(default)))

    a('<g id="title_block" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round;stroke-width:0.35">')
    a('<rect id="title_block_frame" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.7"/>' % (x0, y0, ANSI_W, ANSI_H))
    lines = ["M %g,%g V %g" % (mx, y0, fy1)]
    for y in ys[1:-1]:
        lines.append("M %g,%g H %g" % (mx, y, fx1))
    for row, c0, *_ in ANSI_CELLS:
        if c0 > 0:
            lines.append("M %g,%g V %g" % (mx + c0, ys[row], ys[row + 1]))
    lines.append("M %g,%g V %g" % (mx + 70, ys[3], ys[4]))
    # tolerance block: notes above, projection symbol and sheet size below
    yb = fy1 - 14.0
    lines.append("M %g,%g H %g" % (x0, yb, mx))
    lines.append("M %g,%g V %g" % (mx - 18.0, yb, fy1))
    a('<path d="%s"/>' % " ".join(lines))
    a('</g>')
    a('<g id="title_block_labels" style="font-size:1.8px;font-family:%s;'
      'fill:#000000;stroke:none">' % _ff())
    a('<text x="%g" y="%g">TITLE</text>' % (mx + 1.0, ys[1] + 2.4))
    for row, c0, c1, label, *_ in ANSI_CELLS:
        a('<text x="%g" y="%g">%s</text>' % (mx + c0 + 1.0, ys[row] + 2.4, _esc(label)))
    a('<text x="%g" y="%g">SIZE</text>' % (mx - 17.0, yb + 2.4))
    a('<text x="%g" y="%g" style="text-anchor:middle">%s ANGLE PROJECTION</text>'
      % (x0 + (ANSI_TOL_W - 18.0) / 2.0, fy1 - 1.2, "FIRST" if first_angle else "THIRD"))
    a('</g>')
    a('<g id="title_block_data_fields" style="font-family:%s;fill:#000000;stroke:none">' % _ff())
    cx = mx + ANSI_MAIN_W / 2.0
    edit(cx, ys[0] + 6.2, "legal_owner_1", "", 4.5, "middle", "organization", bold=True)
    edit(cx, ys[0] + 10.3, "company_address", "", 2.2, "middle")
    edit(cx, ys[1] + 6.4, "title", "", 4.5, "middle", "title", bold=True)
    edit(cx, ys[1] + 10.6, "title_2", "", 3.2, "middle")
    for row, c0, c1, label, name, default, auto, font, align in ANSI_CELLS:
        x = mx + (c0 + 1.5 if align == "start" else (c0 + c1) / 2.0)
        edit(x, ys[row] + 7.4, name, default, font, align, auto,
             bold=name in ("drawing_number", "revision_index"))
    edit(mx - 9.0, fy1 - 3.0, "sheet_size", sheet_letter(size), 6.0, "middle", bold=True)
    # tolerance lines
    ty = y0 + 3.8
    for line in (ANSI_TOL_LINES_TABLE if tol_mode == "table" else ANSI_TOL_LINES):
        for dx, static, field, default in line:
            tx = x0 + 1.5 + dx
            if static:
                weight = ";font-weight:bold" if field is None else ""
                a('<text x="%g" y="%g" style="font-size:2.3px%s">%s</text>'
                  % (tx, ty, weight, _esc(static)))
            if field:
                off = {"": 0.0, ".X": 8.0, ".XX": 8.0, ".XXX": 8.0, ".XXXX": 8.0,
                       "ANGLES": 10.0 * _wf() / 0.52}.get(
                    static, len(static) * 2.3 * _wf() * 0.95 + 1.0)
                edit(tx + off, ty, field, default, 2.3)
        ty += 3.6
    a('</g>')
    a(projection_symbol(x0 + (ANSI_TOL_W - 18.0) / 2.0, yb + 5.6, first_angle, h=6.0))


def make_svg(size, orientation, first_angle=True, tol_row=False, style="iso", tol_mode="places"):
    """SVG text of a DrawingKit template (``tol_row``: with the row of
    units, tolerances and finish on top of the title block; ``style``
    'ansi': ASME Y14.1 style title block with a tolerance block;
    ``tol_mode`` 'table': general tolerances are in a size table on the
    sheet, so no X.X / X.XX / X.XXX cells)."""
    W, H = page_size(size, orientation)
    design = custom_design(style)
    if style != "iso" and not fits(style, size, orientation):
        style, design = "iso", None         # no room (or no such design): the ISO block
    rows = 5 if tol_row else 4
    tb_h = rows * ROW_H
    all_cells = cells(tol_row, tol_mode)
    proj = (PROJECTION_CELL[0] + (1 if tol_row else 0),) + PROJECTION_CELL[1:]
    left, other = 20.0, 10.0
    fx0, fy0, fx1, fy1 = left, other, W - other, H - other       # drawing frame
    gx0, gy0, gx1, gy1 = left - 5, other - 5, W - other + 5, H - other + 5  # grid border
    out = []
    a = out.append
    a('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    a('<svg xmlns="http://www.w3.org/2000/svg" '
      'xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace" '
      'version="1.1" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (W, H, W, H))
    a('<!-- DrawingKit template: %s %s, %s angle projection -->'
      % (size, orientation, "first" if first_angle else "third"))
    a('<g id="drawingkit_frame" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round">')
    a('<rect id="grid_reference_border" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.35"/>' % (gx0, gy0, gx1 - gx0, gy1 - gy0))
    a('<rect id="drawing_space_frame" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.7"/>' % (fx0, fy0, fx1 - fx0, fy1 - fy0))
    cx, cy = (fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0
    # centring marks (stopping at the frame where the title block reaches them)
    tb_w = design["width"] if design else (ANSI_W if style == "ansi" else TB_W)
    tb_hh = design["height"] if design else (ANSI_H if style == "ansi" else tb_h)
    bottom_in = fy1 if fx1 - tb_w <= cx + 1e-6 else fy1 - 5
    right_in = fx1 if fy1 - tb_hh <= cy + 1e-6 else fx1 - 5
    a('<path style="stroke-width:0.7" d="M %g,%g V %g M %g,%g V %g M %g,%g H %g M %g,%g H %g"/>'
      % (cx, gy0 - 5 + 5, fy0 + 5, cx, gy1, bottom_in,
         gx0, cy, fx0 + 5, gx1, cy, right_in))
    # zone ticks and labels
    xs = _zones(fx0, fx1, cx)
    ys = _zones(fy0, fy1, cy)
    ticks = []
    for x in xs[1:-1]:
        ticks.append("M %g,%g V %g M %g,%g V %g" % (x, gy0, fy0, x, fy1, gy1))
    for y in ys[1:-1]:
        ticks.append("M %g,%g H %g M %g,%g H %g" % (gx0, y, fx0, fx1, y, gx1))
    a('<path style="stroke-width:0.35" d="%s"/>' % " ".join(ticks))
    a('</g>')
    a('<g id="drawingkit_zones" style="font-size:3.5px;font-family:%s;text-anchor:middle;'
      'fill:#000000;stroke:none">' % _ff())
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        lab = _zone_labels(len(xs) - 1, False)[i]
        xm = (x0 + x1) / 2.0
        a('<text x="%g" y="%g">%s</text><text x="%g" y="%g">%s</text>'
          % (xm, gy0 + 3.8, lab, xm, gy1 - 1.2, lab))
    for i, (y0, y1) in enumerate(zip(ys, ys[1:])):
        lab = _zone_labels(len(ys) - 1, True)[i]
        ym = (y0 + y1) / 2.0 + 1.2
        a('<text x="%g" y="%g">%s</text><text x="%g" y="%g">%s</text>'
          % ((gx0 + fx0) / 2.0, ym, lab, (fx1 + gx1) / 2.0, ym, lab))
    a('</g>')

    if style == "ansi":
        _ansi_block(a, fx1, fy1, size, first_angle, tol_mode)
        a('</svg>')
        return "\n".join(out) + "\n"
    if design is not None:
        from . import blocks
        a('<!-- title block: %s -->' % blocks._esc(design["name"]).replace("--", "- -"))
        a(blocks.svg(design, fx1 - design["width"], fy1 - design["height"], sheet_letter(size),
                     first_angle))
        a('</svg>')
        return "\n".join(out) + "\n"

    # title block
    tx0, ty0 = fx1 - TB_W, fy1 - tb_h
    a('<g id="title_block" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round;stroke-width:0.35">')
    a('<rect id="title_block_frame" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.7"/>' % (tx0, ty0, TB_W, tb_h))
    lines = []
    for r in range(1, rows):
        lines.append("M %g,%g H %g" % (tx0, ty0 + r * ROW_H, fx1))
    for row, x0, x1, *_ in all_cells + [proj + (None,) * 6]:
        if x0 > 0:
            y = ty0 + row * ROW_H
            lines.append("M %g,%g V %g" % (tx0 + x0, y, y + ROW_H))
    a('<path d="%s"/>' % " ".join(sorted(set(lines))))
    a('</g>')
    a('<g id="title_block_labels" style="font-size:%gpx;font-family:%s;'
      'fill:#000000;stroke:none">' % (LABEL_FONT, _ff()))
    for row, x0, x1, label, *_ in all_cells:
        a('<text x="%g" y="%g">%s</text>' % (tx0 + x0 + 1.0, ty0 + row * ROW_H + 2.8, _esc(label)))
    a('</g>')
    a('<g id="title_block_data_fields" style="font-family:%s;fill:#000000;stroke:none">' % _ff())
    for row, x0, x1, label, name, default, autofill, font, align in all_cells:
        y = ty0 + row * ROW_H + (7.6 if font < TITLE_FONT else 8.0)
        x = tx0 + (x0 + 1.5 if align == "start" else (x0 + x1) / 2.0)
        auto = ' freecad:autofill="%s"' % autofill if autofill else ""
        a('<text x="%g" y="%g" freecad:editable="%s"%s style="font-size:%gpx;text-anchor:%s">'
          '<tspan>%s</tspan></text>' % (x, y, name, auto, font, align, _esc(default)))
    a('</g>')
    prow, px0, px1 = proj
    a(projection_symbol(tx0 + (px0 + px1) / 2.0, ty0 + prow * ROW_H + ROW_H / 2.0,
                        first_angle, h=ROW_H - 2.6))
    a('</svg>')
    return "\n".join(out) + "\n"


def template_dir():
    return os.path.join(App.getUserAppDataDir(), "DrawingKit", "Templates")


def fits_ansi(size, orientation):
    return page_size(size, orientation)[0] - 30.0 >= ANSI_MIN_FRAME_W


def template_path(size, orientation, first_angle=True, out_dir=None, tol_row=False, style="iso",
                  tol_mode="places"):
    """Write (if changed) and return the path of a DrawingKit template."""
    out_dir = out_dir or template_dir()
    os.makedirs(out_dir, exist_ok=True)
    if style != "iso" and not fits(style, size, orientation):
        style = "iso"
    if str(style).startswith("custom:"):
        from . import blocks
        suffix = "_c-" + blocks.slug(style[7:])
        tol_row = False
    else:
        suffix = "_ansi" if style == "ansi" else ("_tol" if tol_row else "")
        if suffix and tol_mode == "table":
            suffix += "tab"
    name = "DrawingKit_%s_%s_%s%s.svg" % (size.replace(" ", ""), orientation,
                                      "1st" if first_angle else "3rd", suffix)
    path = os.path.join(out_dir, name)
    text = make_svg(size, orientation, first_angle, tol_row and style != "ansi", style, tol_mode)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
