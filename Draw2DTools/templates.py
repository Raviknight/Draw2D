# SPDX-License-Identifier: LGPL-2.1-or-later
"""Draw2D's own drawing templates (frame + title block), made as SVG.

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


def page_size(size, orientation):
    if size not in SIZES or orientation not in ORIENTATIONS:
        raise ValueError("Unknown sheet %s %s" % (size, orientation))
    w, h = round(SIZES[size][0], 2), round(SIZES[size][1], 2)
    return (h, w) if orientation == "Portrait" else (w, h)


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
    while x > start + 1e-6:
        cuts.add(round(x, 3))
        x -= step
    x = centre + step
    while x < end - 1e-6:
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


def make_svg(size, orientation, first_angle=True):
    """SVG text of a Draw2D template."""
    W, H = page_size(size, orientation)
    left, other = 20.0, 10.0
    fx0, fy0, fx1, fy1 = left, other, W - other, H - other       # drawing frame
    gx0, gy0, gx1, gy1 = left - 5, other - 5, W - other + 5, H - other + 5  # grid border
    out = []
    a = out.append
    a('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    a('<svg xmlns="http://www.w3.org/2000/svg" '
      'xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace" '
      'version="1.1" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (W, H, W, H))
    a('<!-- Draw2D template: %s %s, %s angle projection -->'
      % (size, orientation, "first" if first_angle else "third"))
    a('<g id="draw2d_frame" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round">')
    a('<rect id="grid_reference_border" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.35"/>' % (gx0, gy0, gx1 - gx0, gy1 - gy0))
    a('<rect id="drawing_space_frame" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.7"/>' % (fx0, fy0, fx1 - fx0, fy1 - fy0))
    cx, cy = (fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0
    # centring marks
    a('<path style="stroke-width:0.7" d="M %g,%g V %g M %g,%g V %g M %g,%g H %g M %g,%g H %g"/>'
      % (cx, gy0 - 5 + 5, fy0 + 5, cx, gy1, fy1 - 5,
         gx0, cy, fx0 + 5, gx1, cy, fx1 - 5))
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
    a('<g id="draw2d_zones" style="font-size:3.5px;font-family:osifont;text-anchor:middle;'
      'fill:#000000;stroke:none">')
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

    # title block
    tx0, ty0 = fx1 - TB_W, fy1 - TB_H
    a('<g id="title_block" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round;stroke-width:0.35">')
    a('<rect id="title_block_frame" x="%g" y="%g" width="%g" height="%g" '
      'style="stroke-width:0.7"/>' % (tx0, ty0, TB_W, TB_H))
    lines = []
    for r in range(1, 4):
        lines.append("M %g,%g H %g" % (tx0, ty0 + r * ROW_H, fx1))
    for row, x0, x1, *_ in CELLS + [PROJECTION_CELL + (None,) * 6]:
        if x0 > 0:
            y = ty0 + row * ROW_H
            lines.append("M %g,%g V %g" % (tx0 + x0, y, y + ROW_H))
    a('<path d="%s"/>' % " ".join(sorted(set(lines))))
    a('</g>')
    a('<g id="title_block_labels" style="font-size:%gpx;font-family:osifont;'
      'fill:#000000;stroke:none">' % LABEL_FONT)
    for row, x0, x1, label, *_ in CELLS:
        a('<text x="%g" y="%g">%s</text>' % (tx0 + x0 + 1.0, ty0 + row * ROW_H + 2.8, _esc(label)))
    a('</g>')
    a('<g id="title_block_data_fields" style="font-family:osifont;fill:#000000;stroke:none">')
    for row, x0, x1, label, name, default, autofill, font, align in CELLS:
        y = ty0 + row * ROW_H + (7.6 if font < TITLE_FONT else 8.0)
        x = tx0 + (x0 + 1.5 if align == "start" else (x0 + x1) / 2.0)
        auto = ' freecad:autofill="%s"' % autofill if autofill else ""
        a('<text x="%g" y="%g" freecad:editable="%s"%s style="font-size:%gpx;text-anchor:%s">'
          '<tspan>%s</tspan></text>' % (x, y, name, auto, font, align, _esc(default)))
    a('</g>')
    prow, px0, px1 = PROJECTION_CELL
    a(projection_symbol(tx0 + (px0 + px1) / 2.0, ty0 + prow * ROW_H + ROW_H / 2.0,
                        first_angle, h=ROW_H - 2.6))
    a('</svg>')
    return "\n".join(out) + "\n"


def template_dir():
    return os.path.join(App.getUserAppDataDir(), "Draw2D", "Templates")


def template_path(size, orientation, first_angle=True, out_dir=None):
    """Write (if changed) and return the path of a Draw2D template."""
    out_dir = out_dir or template_dir()
    os.makedirs(out_dir, exist_ok=True)
    name = "Draw2D_%s_%s_%s.svg" % (size.replace(" ", ""), orientation,
                                    "1st" if first_angle else "3rd")
    path = os.path.join(out_dir, name)
    text = make_svg(size, orientation, first_angle)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
