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
        size = holes.callout_text([g[0]]).replace("%.2f", _fmt(g[0].radius * 2))
        for i, (x, y, h) in enumerate(pts):
            out.append(("%s%d" % (letter, i + 1), x, y, size, h))
    return out


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
         "widths": [14.0, 20.0, 20.0, 40.0]}
    W, Ht = table_size(p)
    p["widths"][-1] = max(40.0, W - 54.0)
    W, Ht = table_size(p)
    if place is None:
        frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
        place = (title[2] - W / 2.0, title[3] + 3.0 + Ht / 2.0)
    table = annot._new(page, "holetable", "Hole table %s" % view.Label, p,
                       "free", None, place, (0, 0))
    return table, tags


# ---------------------------------------------------------------------------
# Revision table
# ---------------------------------------------------------------------------
REV_HEADERS = ["REV", "DESCRIPTION", "DATE", "APPROVED"]
REV_WIDTHS = [12.0, 70.0, 24.0, 24.0]


def revision_table(page):
    for v in page.Views:
        if getattr(v, annot.KIND, "") == "revtable":
            return v
    return None


def next_revision(rows):
    used = [r[0] for r in rows]
    return core.next_free_letter(used)


def add_revision(page, description, approved="", date=None):
    """Add a row (next letter) to the page's revision table, making the
    table if needed, and put the letter in the title block. Returns the
    table."""
    date = date or datetime.date.today().isoformat()
    table = revision_table(page)
    if table is None:
        p = {"title": "", "headers": list(REV_HEADERS), "rows": [], "widths": list(REV_WIDTHS),
             "editable": True}
    else:
        p = annot._params(table)
        # keep edits the user made in the table's texts
        vals = annot._values(table)
        for i, row in enumerate(p["rows"]):
            for j in range(len(row)):
                row[j] = vals.get("r%dc%d" % (i, j), row[j])
    rev = next_revision(p["rows"])
    p["rows"].append([rev, description, date, approved])
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
