# SPDX-License-Identifier: LGPL-2.1-or-later
"""Hole table and revision table, drawn as DrawingKit annotations.

Hole table: every hole seen end-on in a view gets a tag (A1, A2... one
letter per hole size) that follows the view, and a table lists each tag's
X / Y from the part's lower-left corner and its size.

Revision table: REV | DESCRIPTION | DATE | APPROVED rows at the top right of
the drawing frame; adding a revision also updates the title block.
"""

import datetime

import FreeCAD as App

from . import annot, core, holes

ROW_H_ = 6.0
ROW_H = ROW_H_
FONT = 2.5


def draw_table(sk, centre, p, values):
    """Grid table centred on page point ``centre``. Params: headers, rows,
    widths, title, editable (cell names 'rIcJ' for editable rows)."""
    k = sk.k
    ROW_H = ROW_H_ * k
    widths = [w * k for w in p["widths"]]
    headers = p["headers"]
    rows = p.get("rows", [])
    title = p.get("title", "")
    W = sum(widths)
    n_rows = len(rows) + 1 + (1 if title else 0)
    Ht = n_rows * ROW_H
    x0 = centre[0] - W / 2.0
    ytop = centre[1] + Ht / 2.0
    sk.rect(x0, ytop - Ht, x0 + W, ytop, 0.5)
    y = ytop
    if title:
        sk.text(x0 + W / 2.0, y - ROW_H / 2.0, title, size=FONT)
        y -= ROW_H
        sk.line((x0, y), (x0 + W, y))
    cells = [headers] + rows
    for r, row in enumerate(cells):
        x = x0
        for c, (txt, w) in enumerate(zip(row, widths)):
            name = None
            if r > 0 and p.get("editable"):
                name = "r%dc%d" % (r - 1, c)
                txt = values.get(name, txt)
            if txt:
                sk.text(x + w / 2.0, y - ROW_H / 2.0, str(txt), size=FONT, editable=name)
            x += w
        y -= ROW_H
        if r < len(cells) - 1:
            sk.line((x0, y), (x0 + W, y), 0.5 if r == 0 else 0.25)
    x = x0
    top_of_grid = ytop - (ROW_H if title else 0)
    for w in widths[:-1]:
        x += w
        sk.line((x, top_of_grid), (x, ytop - Ht))
    return W, Ht


def table_size(p):
    from .annot import text_scale
    k = text_scale(p)
    rows = len(p.get("rows", [])) + 1 + (1 if p.get("title") else 0)
    return sum(p["widths"]) * k, rows * ROW_H_ * k


# ---------------------------------------------------------------------------
# Hole table
# ---------------------------------------------------------------------------
def _fmt(v):
    return "%.2f" % v


def hole_rows(view, shape=None):
    """[(tag, x, y, size, hole)] for the holes seen end-on in the view,
    X / Y from the lower-left corner of the part's outline (model mm)."""
    shape = shape or core.source_shape(view.Source)
    hs = holes.holes_seen_end_on(view, shape)
    if not hs:
        return []
    bb = shape.BoundBox
    corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
               for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    loc = [core.model_to_local(view, c, shape) for c in corners]
    ox, oy = min(p[0] for p in loc), min(p[1] for p in loc)
    groups = sorted(holes.group_holes(hs), key=lambda g: -g[0].radius)
    out = []
    for gi, g in enumerate(groups):
        letter = core.LETTERS[gi % len(core.LETTERS)]
        pts = []
        for h in g:
            lx, ly = core.model_to_local(view, h.centre, shape)
            pts.append((lx - ox, ly - oy, h))
        pts.sort(key=lambda p: (-round(p[1], 3), round(p[0], 3)))   # top to bottom, left to right
        size = holes.fill_value(holes.callout_text([g[0]]), g[0].radius * 2)
        for i, (x, y, h) in enumerate(pts):
            out.append(("%s%d" % (letter, i + 1), x, y, size, h))
    return out


def _annotated_boxes(page):
    """Each part view's box grown to take in its dimensions (texts and the
    extension lines out to them) and the callouts and symbols on it."""
    from . import clearance
    boxes = {}
    for v in core.part_views(page):
        try:
            boxes[v.Name] = list(core.view_box(v))
        except Exception:
            pass
    for o in page.Views:
        b, owner = None, None
        try:
            if o.isDerivedFrom("TechDraw::DrawViewDimension") and o.References2D:
                owner, b = o.References2D[0][0], clearance.dimension_label_box(o)
            elif o.isDerivedFrom("TechDraw::DrawViewSymbol"):
                owner = getattr(o, annot.LINK, None)
                if getattr(o, annot.KIND, "") in ("bom", "holetable", "revtable", "toltable"):
                    continue
                b = clearance.symbol_box(o)
        except Exception:
            continue
        if b is None or owner is None or owner.Name not in boxes:
            continue
        a = boxes[owner.Name]
        boxes[owner.Name] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    return [tuple(b) for b in boxes.values()]


def free_table_spot(page, W, Ht, gap=3.0, exclude=None, strict=False):
    """Centre for a W x Ht table: above the title block if that is free,
    otherwise the first free corner of the drawing area (views, their
    dimensions and callouts, and other tables are kept clear). ``exclude``:
    a table being moved (not an obstacle to itself). With ``strict``, None
    when no spot is free (else the first candidate)."""
    from . import clearance
    frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
    ex = [exclude] if exclude is not None else []
    mine = core.table_boxes(page)
    if exclude is not None:
        try:
            w, h = table_size(annot._params(exclude))
            x, y = float(exclude.X), float(exclude.Y)
            own = (x - w / 2 - 3.0, y - h / 2 - 3.0, x + w / 2 + 3.0, y + h / 2 + 3.0)
            mine = [b for b in mine if any(abs(p - q) > 1e-6 for p, q in zip(b, own))]
        except Exception:
            pass
    obs = _annotated_boxes(page) + clearance.obstacles(page, exclude=ex, views=False) + mine
    x0, y0, x1, y1 = frame
    cands = []
    if title:
        cands.append((title[2] - W / 2.0, title[3] + gap + Ht / 2.0))
        cands.append((title[0] - gap - W / 2.0, y0 + gap + Ht / 2.0))   # left of the title block
    cands += [(x1 - gap - W / 2.0, y1 - gap - Ht / 2.0),
              (x0 + gap + W / 2.0, y1 - gap - Ht / 2.0),
              (x0 + gap + W / 2.0, y0 + gap + Ht / 2.0)]
    for cx, cy in cands:
        box = (cx - W / 2, cy - Ht / 2, cx + W / 2, cy + Ht / 2)
        if box[0] < x0 - 1e-6 or box[2] > x1 + 1e-6 or box[1] < y0 - 1e-6 or box[3] > y1 + 1e-6:
            continue
        if not clearance._hits(box, obs, gap=1.0):
            return (cx, cy)
    return None if strict else cands[0]


def add_hole_table(page, view, place=None):
    """Tags on every hole of ``view`` plus the table. Returns (table, tags)."""
    shape = core.source_shape(view.Source)
    rows = hole_rows(view, shape)
    if not rows:
        raise ValueError("No holes seen end-on in %s" % view.Label)
    s = core.view_scale(view)
    tags = []
    for tag, x, y, size, h in rows:
        lx, ly = core.model_to_local(view, h.centre, shape)
        off = h.radius * s + 2.5
        tags.append(annot._new(page, "tag", "Hole tag %s" % tag, {"label": tag},
                               "view", view, (lx, ly), (off, off)))
    p = {"title": "HOLE TABLE  (X, Y from the lower-left corner of %s)" % view.Label,
         "headers": ["TAG", "X", "Y", "SIZE"],
         "rows": [[t, _fmt(x), _fmt(y), size] for t, x, y, size, _ in rows],
         "widths": [14.0, 20.0, 20.0, 40.0],
         "tsize": 3.5}   # fixed: its size is worked out before it is drawn
    W, Ht = table_size(p)
    p["widths"][-1] = max(40.0, W - 54.0)
    W, Ht = table_size(p)
    if place is None:
        place = free_table_spot(page, W, Ht)
    table = annot._new(page, "holetable", "Hole table %s" % view.Label, p,
                       "free", None, place, (0, 0))
    return table, tags


# ---------------------------------------------------------------------------
# Revision table
# ---------------------------------------------------------------------------
REV_HEADERS = ["REV", "ECO", "DESCRIPTION", "DATE", "APPROVED"]
REV_WIDTHS = [12.0, 20.0, 70.0, 24.0, 24.0]


def _add_eco_column(p):
    """Tables made before the ECO column: put it in after REV."""
    if "ECO" in p["headers"]:
        return p
    p["headers"].insert(1, "ECO")
    p["widths"].insert(1, 20.0)
    for row in p["rows"]:
        row.insert(1, "")
    return p


def revision_table(page):
    for v in page.Views:
        if getattr(v, annot.KIND, "") == "revtable":
            return v
    return None


def next_revision(rows):
    used = [r[0] for r in rows]
    return core.next_free_letter(used)


def add_revision(page, description, approved="", date=None, eco=""):
    """Add a row (next letter) to the page's revision table, making the
    table if needed, and put the letter in the title block. ``eco``: the
    change order number. Returns the table."""
    date = date or datetime.date.today().isoformat()
    table = revision_table(page)
    if table is None:
        p = {"title": "", "headers": list(REV_HEADERS), "rows": [], "widths": list(REV_WIDTHS),
             "editable": True, "tsize": 3.5}
    else:
        p = annot._params(table)
        # keep edits the user made in the table's texts
        vals = annot._values(table)
        for i, row in enumerate(p["rows"]):
            for j in range(len(row)):
                row[j] = vals.get("r%dc%d" % (i, j), row[j])
        _add_eco_column(p)
    rev = next_revision(p["rows"])
    p["rows"].append([rev, eco, description, date, approved])
    W, Ht = table_size(p)
    frame, _ = core.drawing_area(page.Template.Template, *core.page_size(page))
    place = (frame[2] - W / 2.0, frame[3] - Ht / 2.0)   # hangs from the top-right corner
    if table is None:
        table = annot._new(page, "revtable", "Revision table", p, "free", None, place, (0, 0))
    else:
        import json
        setattr(table, annot.PARAMS, json.dumps(p))
        setattr(table, annot.ANCHOR, App.Vector(place[0], place[1], 0))
        table.EditableTexts = []
        annot.render(table)
    tpl = page.Template
    if tpl is not None and "EditableTexts" in tpl.PropertiesList:
        texts = dict(tpl.EditableTexts)
        if "revision_index" in texts:
            texts["revision_index"] = rev
            tpl.EditableTexts = texts
    return table


# ---------------------------------------------------------------------------
# General tolerances by size range (ISO 2768-1 or your own)
# ---------------------------------------------------------------------------
# linear: (over, up to) mm; ISO 2768-1 starts at 0.5 mm
ISO2768_RANGES = [("0.5", "3"), ("3", "6"), ("6", "30"), ("30", "120"), ("120", "400"),
                  ("400", "1000"), ("1000", "2000"), ("2000", "4000")]
ISO2768_LINEAR = {
    "f": ["0.05", "0.05", "0.1", "0.15", "0.2", "0.3", "0.5", ""],
    "m": ["0.1", "0.1", "0.2", "0.3", "0.5", "0.8", "1.2", "2"],
    "c": ["0.2", "0.3", "0.5", "0.8", "1.2", "2", "3", "4"],
    "v": ["", "0.5", "1", "1.5", "2.5", "4", "6", "8"],
}
# angular, by the length of the shorter side of the angle (mm)
ISO2768_ANGLE_RANGES = [("", "10"), ("10", "50"), ("50", "120"), ("120", "400"), ("400", "")]
ISO2768_ANGULAR = {
    "f": ["1°", "0°30'", "0°20'", "0°10'", "0°5'"],
    "m": ["1°", "0°30'", "0°20'", "0°10'", "0°5'"],
    "c": ["1°30'", "1°", "0°30'", "0°15'", "0°10'"],
    "v": ["3°", "2°", "1°", "0°30'", "0°20'"],
}
TOL_PRESETS = [("f", "ISO 2768-f (fine)"), ("m", "ISO 2768-m (medium)"),
               ("c", "ISO 2768-c (coarse)"), ("v", "ISO 2768-v (very coarse)")]


def iso2768(cls):
    """{'preset', 'linear': [[over, up_to, tol]], 'angular': [...]} of an
    ISO 2768-1 class (rows with no value in that class left out)."""
    lin = [[a, b, t] for (a, b), t in zip(ISO2768_RANGES, ISO2768_LINEAR[cls]) if t]
    ang = [[a, b, t] for (a, b), t in zip(ISO2768_ANGLE_RANGES, ISO2768_ANGULAR[cls]) if t]
    return {"preset": "ISO 2768-%s" % cls, "linear": lin, "angular": ang}


def _range_text(over, up_to, units=""):
    u = (" " + units) if units else ""
    if over and up_to:
        return "%s - %s%s" % (over, up_to, u)
    if up_to:
        return "UP TO %s%s" % (up_to, u)
    return "OVER %s%s" % (over, u)


def _tol_text(t):
    t = str(t).strip()
    return t if not t or t[0] in "±+-" else "±" + t


def tolerance_params(data, units="mm"):
    """Table settings for a general tolerance table from
    {'preset', 'linear', 'angular'}."""
    rows = [[_range_text(a, b), _tol_text(t)] for a, b, t in data.get("linear", []) if t]
    ang = [[_range_text(a, b), _tol_text(t)] for a, b, t in data.get("angular", []) if t]
    if ang:
        rows.append(["ANGLES (SHORTER SIDE)", ""])
        rows += ang
    title = "GENERAL TOLERANCES"
    if data.get("preset"):
        title += "  " + data["preset"]
    return {"title": title, "headers": ["SIZE (%s)" % units.upper(), "TOLERANCE"],
            "rows": rows, "widths": [34.0, 22.0], "tsize": 3.5,
            "data": {"preset": data.get("preset", ""), "linear": data.get("linear", []),
                     "angular": data.get("angular", []), "units": units}}


def tolerance_table(page):
    for v in page.Views:
        if getattr(v, annot.KIND, "") == "toltable":
            return v
    return None


def tolerance_data(page):
    """What the page's general tolerance table holds, or None."""
    t = tolerance_table(page)
    if t is None:
        return None
    return annot._params(t).get("data")


def set_tolerance_table(page, data, units="mm"):
    """Make, update or (with no rows) remove the page's general tolerance
    table. It goes above the title block, or the first free spot. Returns
    the table or None."""
    import json
    table = tolerance_table(page)
    has_rows = data and (any(r[2] for r in data.get("linear", [])) or
                         any(r[2] for r in data.get("angular", [])))
    if not has_rows:
        if table is not None:
            page.removeView(table)
            page.Document.removeObject(table.Name)
        return None
    p = tolerance_params(data, units)
    W, Ht = table_size(p)
    if table is None:
        place = free_table_spot(page, W, Ht)
        return annot._new(page, "toltable", "General tolerances", p, "free", None, place, (0, 0))
    # keep its spot, grown or shrunk from the top-right corner
    old_w, old_h = table_size(annot._params(table))
    x1 = float(table.X) + old_w / 2.0
    y0 = float(table.Y) - old_h / 2.0
    setattr(table, annot.PARAMS, json.dumps(p))
    setattr(table, annot.ANCHOR, App.Vector(x1 - W / 2.0, y0 + Ht / 2.0, 0))
    table.EditableTexts = []
    annot.render(table)
    return table
