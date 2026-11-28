# SPDX-License-Identifier: LGPL-2.1-or-later
"""Your own title blocks.

A title block design is a rectangle cut into cells. Each cell holds one
thing:

* ``field``  - a value typed per drawing or filled from your defaults
  (drawing number, material, drawn by...), with a small caption
* ``text``   - fixed text (CONFIDENTIAL, a property notice, a caption);
  it can run upwards
* ``logo``   - a picture (PNG, JPG or SVG), kept inside the design
* ``tolerance`` - general tolerances by size range, filled from your saved
  tolerance table
* ``projection`` - the first / third angle projection symbol
* ``empty``

Designs are JSON files in your own folder, or the company folder next to
the company defaults file, so a whole team draws on the same block. The
sheet's template is made from the design and goes in the bottom-right
corner of the frame.
"""

import base64
import copy
import json
import os
import re

import FreeCAD as App

KINDS = ["field", "text", "logo", "tolerance", "surface", "projection", "empty"]
KIND_NAMES = {"field": "Field (typed per drawing)", "text": "Fixed text", "logo": "Picture / logo",
              "tolerance": "Tolerances by size (table)",
              "surface": "Surface finish symbol (value + note)",
              "projection": "Projection symbol", "empty": "Empty"}
MAX_LINES = 12
ALIGNS = ["left", "centre", "right"]
CAPTION_SIZE = 1.8
MIN_CELL = 2.0
AUTOFILL = {"title": "title", "creator": "author", "date_of_issue": "date", "scale": "scale",
            "legal_owner_1": "organization"}
TOL_RANGE = "tolsize_r%d"           # editable names of the in-block tolerance rows
TOL_VALUE = "tolsize_t%d"
EPS = 1e-6


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------
def cell(x, y, w, h, kind="empty", **kw):
    c = {"x": float(x), "y": float(y), "w": float(w), "h": float(h), "kind": kind,
         "label": "", "field": "", "text": "", "size": 3.5, "bold": False, "align": "left",
         "vertical": False, "rows": 6, "lines": 1, "image": "", "image_type": "",
         "border": True}
    c.update(kw)
    return c


def new_design(name="My title block", width=180.0, height=40.0):
    return {"drawingkit": "title block", "version": 1, "name": name,
            "width": float(width), "height": float(height),
            "cells": [cell(0, 0, width, height)]}


def clean(d):
    """A design with every key present and sane numbers."""
    out = new_design(str(d.get("name") or "Title block"), float(d.get("width", 180)),
                     float(d.get("height", 40)))
    cells = []
    for c in d.get("cells") or []:
        try:
            n = cell(c["x"], c["y"], c["w"], c["h"])
        except (KeyError, TypeError, ValueError):
            continue
        for k in ("kind", "label", "field", "text", "align", "image", "image_type"):
            if k in c and c[k] is not None:
                n[k] = str(c[k])
        for k in ("size",):
            try:
                n[k] = max(0.8, float(c.get(k, n[k])))
            except (TypeError, ValueError):
                pass
        try:
            n["rows"] = max(1, min(20, int(c.get("rows", 6))))
        except (TypeError, ValueError):
            pass
        try:
            n["lines"] = max(1, min(MAX_LINES, int(c.get("lines", 1))))
        except (TypeError, ValueError):
            pass
        n["bold"] = bool(c.get("bold"))
        n["vertical"] = bool(c.get("vertical"))
        n["border"] = bool(c.get("border", True))
        if n["kind"] not in KINDS:
            n["kind"] = "empty"
        if n["align"] not in ALIGNS:
            n["align"] = "left"
        cells.append(n)
    out["cells"] = cells or out["cells"]
    return out


def slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name or "").strip("-")
    return s[:40] or "block"


def cell_at(d, x, y):
    """Index of the cell under (x, y) (block mm, y down), or None."""
    for i, c in enumerate(d["cells"]):
        if c["x"] - EPS <= x <= c["x"] + c["w"] + EPS and c["y"] - EPS <= y <= c["y"] + c["h"] + EPS:
            return i
    return None


def split(d, i, direction="h", parts=2):
    """Cut cell i into ``parts`` equal cells: 'h' side by side, 'v' one above
    the other. The first keeps its contents. Returns the new indices."""
    c = d["cells"][i]
    parts = max(2, int(parts))
    size = (c["w"] if direction == "h" else c["h"]) / parts
    if size < MIN_CELL:
        raise ValueError("Too small to split")
    new = [i]
    for k in range(1, parts):
        if direction == "h":
            n = cell(c["x"] + k * size, c["y"], size, c["h"])
        else:
            n = cell(c["x"], c["y"] + k * size, c["w"], size)
        d["cells"].append(n)
        new.append(len(d["cells"]) - 1)
    if direction == "h":
        c["w"] = size
    else:
        c["h"] = size
    return new


def neighbour(d, i, side):
    """Index of the cell sharing the whole of cell i's side ('right' or
    'below'), or None."""
    c = d["cells"][i]
    for j, o in enumerate(d["cells"]):
        if j == i:
            continue
        if side == "right" and abs(o["x"] - (c["x"] + c["w"])) < EPS and \
                abs(o["y"] - c["y"]) < EPS and abs(o["h"] - c["h"]) < EPS:
            return j
        if side == "below" and abs(o["y"] - (c["y"] + c["h"])) < EPS and \
                abs(o["x"] - c["x"]) < EPS and abs(o["w"] - c["w"]) < EPS:
            return j
        if side == "left" and abs(o["x"] + o["w"] - c["x"]) < EPS and \
                abs(o["y"] - c["y"]) < EPS and abs(o["h"] - c["h"]) < EPS:
            return j
        if side == "above" and abs(o["y"] + o["h"] - c["y"]) < EPS and \
                abs(o["x"] - c["x"]) < EPS and abs(o["w"] - c["w"]) < EPS:
            return j
    return None


def merge(d, i, side):
    """Join cell i with the cell on that side; i keeps its contents.
    Returns the index of the joined cell."""
    j = neighbour(d, i, side)
    if j is None:
        raise ValueError("No cell of the same %s on that side to merge with"
                         % ("height" if side in ("left", "right") else "width"))
    c, o = d["cells"][i], d["cells"][j]
    x0, y0 = min(c["x"], o["x"]), min(c["y"], o["y"])
    x1 = max(c["x"] + c["w"], o["x"] + o["w"])
    y1 = max(c["y"] + c["h"], o["y"] + o["h"])
    c.update({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
    del d["cells"][j]
    return d["cells"].index(c)


def _edge_group(d, i, side):
    """Cells on either side of cell i's edge, along the whole straight line it
    is part of: ([cells ending there], [cells starting there], position)."""
    c = d["cells"][i]
    vertical = side in ("left", "right")
    if vertical:
        pos = c["x"] + c["w"] if side == "right" else c["x"]
        lo, hi = c["y"], c["y"] + c["h"]
    else:
        pos = c["y"] + c["h"] if side == "bottom" else c["y"]
        lo, hi = c["x"], c["x"] + c["w"]
    a = "x" if vertical else "y"
    s = "w" if vertical else "h"
    b, t = ("y", "h") if vertical else ("x", "w")
    ends, starts = [], []
    grown = True
    while grown:
        grown = False
        for o in d["cells"]:
            if o in ends or o in starts:
                continue
            o_lo, o_hi = o[b], o[b] + o[t]
            if o_hi < lo - EPS or o_lo > hi + EPS:
                continue
            if abs(o[a] + o[s] - pos) < EPS:
                ends.append(o)
            elif abs(o[a] - pos) < EPS:
                starts.append(o)
            else:
                continue
            if o_lo < lo or o_hi > hi:
                lo, hi = min(lo, o_lo), max(hi, o_hi)
            grown = True
    return ends, starts, pos


def move_edge(d, i, side, new_pos):
    """Move one side ('left', 'right', 'top', 'bottom') of cell i, and with
    it every cell along the same line, to ``new_pos`` (mm in the block).
    The block's own outline doesn't move. Returns the position used."""
    vertical = side in ("left", "right")
    total = d["width"] if vertical else d["height"]
    ends, starts, pos = _edge_group(d, i, side)
    if not ends or not starts:
        return pos              # the outline of the block
    a, s = ("x", "w") if vertical else ("y", "h")
    lo = max(o[a] for o in ends) + MIN_CELL
    hi = min(o[a] + o[s] for o in starts) - MIN_CELL
    new_pos = max(lo, min(hi, float(new_pos), total))
    for o in ends:
        o[s] = new_pos - o[a]
    for o in starts:
        o[s] = o[a] + o[s] - new_pos
        o[a] = new_pos
    return new_pos


# rows and columns right across the block: "row" works along y (heights),
# "column" along x (widths)
_AXES = {"row": ("y", "h", "x", "w", "height"), "column": ("x", "w", "y", "h", "width")}


def insert_band(d, i, kind="row", where="after", size=None):
    """Insert a row (or column) right across the block, next to cell i:
    ``where`` 'after' (below / right of it) or 'before' (above / left).
    The block grows by ``size`` mm (default: cell i's height / width). The
    new strip is cut where the lines meeting it are; a cell the strip runs
    through gets longer instead. Returns the new cells' indices."""
    a, s, b, t, total = _AXES[kind]
    c = d["cells"][i]
    size = float(size if size else c[s])
    if size < MIN_CELL:
        raise ValueError("A %s must be at least %g mm" % (kind, MIN_CELL))
    pos = c[a] + c[s] if where == "after" else c[a]
    # the cells whose edge is on that line, on the side the new strip copies
    edge = [o for o in d["cells"]
            if abs((o[a] + o[s] if where == "after" else o[a]) - pos) < EPS]
    for o in d["cells"]:
        if o[a] >= pos - EPS:
            o[a] += size                       # beyond the line: moved along
        elif o[a] + o[s] > pos + EPS:
            o[s] += size                       # across the line: stretched
    new = []
    for o in edge:
        kw = {a: pos, s: size, b: o[b], t: o[t]}
        d["cells"].append(cell(kw["x"], kw["y"], kw["w"], kw["h"]))
        new.append(len(d["cells"]) - 1)
    d[total] += size
    return new


def band_of(d, i, kind="row"):
    """(start, end) of the row (or column) that cell i is in: the smallest
    strip across the block, holding cell i, that no cell only partly
    crosses (a cell running right through it doesn't count)."""
    a, s = _AXES[kind][:2]
    c = d["cells"][i]
    lo, hi = c[a], c[a] + c[s]
    grown = True
    while grown:
        grown = False
        for o in d["cells"]:
            o_lo, o_hi = o[a], o[a] + o[s]
            if o_hi <= lo + EPS or o_lo >= hi - EPS:
                continue                       # clear of the strip
            if o_lo <= lo + EPS and o_hi >= hi - EPS and (o_lo < lo - EPS or o_hi > hi + EPS):
                continue                       # runs right through it
            if o_lo < lo - EPS or o_hi > hi + EPS:
                lo, hi = min(lo, o_lo), max(hi, o_hi)
                grown = True
    return lo, hi


def delete_band(d, i, kind="row"):
    """Delete the row (or column) cell i is in, right across the block; the
    block shrinks by its size, and a cell running through it gets shorter.
    Returns the size removed."""
    a, s, _b, _t, total = _AXES[kind]
    lo, hi = band_of(d, i, kind)
    size = hi - lo
    if size >= d[total] - EPS:
        raise ValueError("That %s is the whole block: nothing would be left" % kind)
    through = [o for o in d["cells"] if o[a] + o[s] > hi + EPS and o[a] < lo - EPS]
    if any(o[s] - size < MIN_CELL - EPS for o in through):
        raise ValueError("A cell running through this %s would be too small" % kind)
    for o in through:
        o[s] -= size                           # runs through it: shorter
    keep = []
    for o in d["cells"]:
        if o[a] >= lo - EPS and o[a] + o[s] <= hi + EPS:
            continue                           # inside the strip: gone
        if o[a] >= hi - EPS:
            o[a] -= size
        keep.append(o)
    d["cells"] = keep
    d[total] -= size
    return size


def resize(d, width=None, height=None):
    """Stretch the whole block to a new size."""
    kx = float(width) / d["width"] if width else 1.0
    ky = float(height) / d["height"] if height else 1.0
    for c in d["cells"]:
        c["x"], c["w"] = c["x"] * kx, c["w"] * kx
        c["y"], c["h"] = c["y"] * ky, c["h"] * ky
    d["width"], d["height"] = d["width"] * kx, d["height"] * ky


def line_names(c):
    """Editable names of a field cell's lines: notes, notes_2, notes_3..."""
    if not c["field"]:
        return []
    return [c["field"]] + ["%s_%d" % (c["field"], k) for k in range(2, c["lines"] + 1)]


def surface_names(c):
    """(value, note) editable names of a surface finish cell."""
    f = c["field"] or "surface_finish"
    return f, f + "_note"


def field_names(d):
    """Editable field names the design makes, in order."""
    out = []
    for c in d["cells"]:
        if c["kind"] == "field" and c["field"]:
            out += line_names(c)
        elif c["kind"] == "surface":
            out += list(surface_names(c))
        elif c["kind"] == "tolerance":
            for r in range(c["rows"]):
                out += [TOL_RANGE % (r + 1), TOL_VALUE % (r + 1)]
    return out


def problems(d):
    """What would stop the design working, as sentences."""
    out = []
    names = field_names(d)
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        out.append("Two cells use the same field: %s" % ", ".join(dup))
    for c in d["cells"]:
        if c["kind"] == "field" and not c["field"]:
            out.append("A field cell has no field name")
            break
    if sum(1 for c in d["cells"] if c["kind"] == "tolerance") > 1:
        out.append("Only one tolerance table per title block")
    area = sum(c["w"] * c["h"] for c in d["cells"])
    if abs(area - d["width"] * d["height"]) > 0.5:
        out.append("The cells don't fill the block")
    return out


def has_tolerance(d):
    return any(c["kind"] == "tolerance" for c in d["cells"])


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------
IMAGE_TYPES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".svg": "svg+xml",
               ".bmp": "bmp", ".gif": "gif"}


def load_image(c, path):
    """Put a picture file into a logo cell."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_TYPES:
        raise ValueError("Pick a PNG, JPG or SVG picture")
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("That picture is over 4 MB; use a smaller one")
    c["image"] = base64.b64encode(data).decode("ascii")
    c["image_type"] = IMAGE_TYPES[ext]
    c["kind"] = "logo"


def image_size(c):
    """(w, h) pixels of a logo cell's picture, or None."""
    if not c.get("image"):
        return None
    try:
        raw = base64.b64decode(c["image"])
    except Exception:
        return None
    if c.get("image_type") == "png" and raw[:8] == b"\x89PNG\r\n\x1a\n":
        import struct
        return struct.unpack(">II", raw[16:24])
    try:
        from PySide import QtGui, QtCore
        img = QtGui.QImage()
        if img.loadFromData(QtCore.QByteArray(raw)):
            return img.width(), img.height()
    except Exception:
        pass
    if c.get("image_type") == "svg+xml":
        m = re.search(rb'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', raw)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


# ---------------------------------------------------------------------------
# drawing the block
# ---------------------------------------------------------------------------
def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _range_text(over, up_to):
    if over and up_to:
        try:
            if float(over) < 1e-9:
                return "<%s" % up_to
        except ValueError:
            pass
        return "%s-%s" % (over, up_to)
    if up_to:
        return "<%s" % up_to
    return ">%s" % over


def _tol_text(t):
    t = str(t).strip()
    return t if not t or t[0] in "±+-<>" else "±" + t


def tolerance_values(data, rows):
    """{field: text} for the in-block tolerance rows from a saved tolerance
    table ({'linear': [[over, up_to, tol]], 'angular', 'angle_all'}). The
    last row takes the angle tolerance."""
    out = {}
    data = data or {}
    lin = [r for r in data.get("linear") or [] if len(r) == 3 and r[2]]
    ang = ""
    if data.get("angle_all"):
        ang = data["angle_all"]
    elif data.get("angular"):
        ang = data["angular"][0][2]
    room = rows - (1 if ang else 0)
    if len(lin) > room and room > 0:
        # too many ranges: keep the first ones, last one open-ended
        lin = lin[:room]
        lin[-1] = [lin[-1][0], "", lin[-1][2]]
    for i in range(rows):
        out[TOL_RANGE % (i + 1)] = ""
        out[TOL_VALUE % (i + 1)] = ""
    for i, (a, b, t) in enumerate(lin[:rows]):
        out[TOL_RANGE % (i + 1)] = _range_text(a, b)
        out[TOL_VALUE % (i + 1)] = _tol_text(t)
    if ang and rows:
        out[TOL_RANGE % rows] = "∠"
        out[TOL_VALUE % rows] = _tol_text(ang)
    return out


def _caption_h(c):
    n = len(c["label"].splitlines()) if c["label"] else 0
    return CAPTION_SIZE * 1.25 * n + 0.6 if n else 0.0


def _surface(a, c, cx0, cy0, weight, preview):
    """Surface texture symbol (√) with its value over the V and a note after
    it, e.g. 63 √ MACHINE: both editable."""
    val_name, note_name = surface_names(c)
    defaults = (c["text"].splitlines() + ["", ""])[:2]
    s = c["size"]
    h = s * 1.5                               # height of the short leg
    top = cy0 + _caption_h(c)
    note_w = max(len(defaults[1]), 6) * s * 0.64
    sym_w = 1.25 * h
    total = sym_w + 1.0 + note_w
    if c["align"] == "left":
        x = cx0 + 1.5
    elif c["align"] == "right":
        x = cx0 + c["w"] - 1.5 - total
    else:
        x = cx0 + (c["w"] - total) / 2.0
    # the V: bottom point, short leg up-left, long leg up-right (60 degrees)
    by = top + (cy0 + c["h"] - top) / 2.0 + h * 0.55
    bx = x + 0.58 * h * 0.6
    lx, ly = bx - 0.35 * h, by - 0.6 * h
    rx, ry = bx + 0.69 * h, by - 1.2 * h
    a('<path style="fill:none;stroke:#000000;stroke-width:0.3" d="M %.3f,%.3f L %.3f,%.3f '
      'L %.3f,%.3f"/>' % (lx, ly, bx, by, rx, ry))
    names = ((val_name, defaults[0], lx + 0.1 * h, ly - 0.6, "middle", s * 0.8),
             (note_name, defaults[1], rx + 1.0, by, "start", s))
    for name, default, tx, ty, anchor, fs in names:
        a('<text x="%.3f" y="%.3f" freecad:editable="%s" style="font-size:%gpx;text-anchor:%s%s">'
          '<tspan>%s</tspan></text>' % (tx, ty, _esc(name), fs, anchor, weight, _esc(default)))
        if preview and not default:
            a('<text x="%.3f" y="%.3f" style="font-size:%gpx;text-anchor:%s;fill:#9aa5b1;'
              'font-style:italic">%s</text>' % (tx, ty, fs * 0.8, anchor, _esc(name)))


def _value_y(c, y0, size):
    """Baseline of a cell's main text: centred in the space under its caption."""
    top = y0 + c["y"] + _caption_h(c)
    bottom = y0 + c["y"] + c["h"]
    return (top + bottom) / 2.0 + size * 0.36


def _anchor_x(c, x0, pad=1.2):
    if c["align"] == "centre":
        return x0 + c["x"] + c["w"] / 2.0, "middle"
    if c["align"] == "right":
        return x0 + c["x"] + c["w"] - pad, "end"
    return x0 + c["x"] + pad, "start"


def _minus(a, b, cuts):
    """Parts of the interval a..b not covered by the intervals in ``cuts``."""
    parts = [(a, b)]
    for c0, c1 in cuts:
        nxt = []
        for p0, p1 in parts:
            if c1 <= p0 + EPS or c0 >= p1 - EPS:
                nxt.append((p0, p1))
                continue
            if c0 > p0 + EPS:
                nxt.append((p0, c0))
            if c1 < p1 - EPS:
                nxt.append((c1, p1))
        parts = nxt
    return [(p0, p1) for p0, p1 in parts if p1 - p0 > EPS]


def inner_lines(d):
    """The lines between cells as (x, y, length, vertical) in block mm (y
    down); the outer frame is not included. Each cell draws its left and
    top edge. Where a cell without a border meets another cell without a
    border the line is left out, so a group of them reads as one open area."""
    out = []
    cells = d["cells"]
    for c in cells:
        for vertical in (True, False):
            pos, start, span = ("x", "y", "h") if vertical else ("y", "x", "w")
            if c[pos] <= EPS:
                continue
            a, b = c[start], c[start] + c[span]
            if c.get("border", True):
                parts = [(a, b)]
            else:
                far = "w" if vertical else "h"
                cuts = [(n[start], n[start] + n[span]) for n in cells
                        if not n.get("border", True) and n is not c
                        and abs(n[pos] + n[far] - c[pos]) < EPS]
                parts = _minus(a, b, cuts)
            for p0, p1 in parts:
                if vertical:
                    out.append((c["x"], p0, p1 - p0, True))
                else:
                    out.append((p0, c["y"], p1 - p0, False))
    return out


def svg(d, x0, y0, size_letter="", first_angle=True, preview=False, font=None):
    """SVG elements of the title block with its top-left corner on (x0, y0)
    (mm). ``preview``: show field names in empty fields (for the designer)."""
    from . import fonts, templates
    ff = fonts.css_family(font)
    out = []
    a = out.append
    lines = []
    for x, y, length, vertical in inner_lines(d):
        if vertical:
            lines.append("M %.3f,%.3f V %.3f" % (x0 + x, y0 + y, y0 + y + length))
        else:
            lines.append("M %.3f,%.3f H %.3f" % (x0 + x, y0 + y, x0 + x + length))
    a('<g id="title_block" style="fill:none;stroke:#000000;stroke-linecap:round;'
      'stroke-linejoin:round;stroke-width:0.35">')
    a('<rect id="title_block_frame" x="%.3f" y="%.3f" width="%.3f" height="%.3f" '
      'style="stroke-width:0.7"/>' % (x0, y0, d["width"], d["height"]))
    if lines:
        a('<path d="%s"/>' % " ".join(sorted(set(lines))))
    a('</g>')
    a('<g id="title_block_text" style="font-family:%s;fill:#000000;stroke:none">' % ff)
    for c in d["cells"]:
        cx0, cy0 = x0 + c["x"], y0 + c["y"]
        weight = ";font-weight:bold" if c["bold"] else ""
        if c["label"] and c["kind"] in ("field", "logo", "projection", "empty", "surface"):
            for k, cap in enumerate(c["label"].splitlines()):
                a('<text x="%.3f" y="%.3f" style="font-size:%gpx%s">%s</text>'
                  % (cx0 + c["w"] / 2.0 if c["align"] == "centre" else cx0 + 0.9,
                     cy0 + CAPTION_SIZE + 0.5 + k * CAPTION_SIZE * 1.25, CAPTION_SIZE,
                     ";text-anchor:middle" if c["align"] == "centre" else "", _esc(cap)))
        if c["kind"] == "field" and c["field"]:
            x, anchor = _anchor_x(c, x0)
            names = line_names(c)
            defaults = c["text"].replace("{size}", size_letter).splitlines() or [""]
            pitch = c["size"] * 1.3
            y = _value_y(c, y0, c["size"]) - pitch * (len(names) - 1) / 2.0
            for k, name in enumerate(names):
                default = defaults[k] if k < len(defaults) else ""
                auto = ' freecad:autofill="%s"' % AUTOFILL[name] if name in AUTOFILL else ""
                a('<text x="%.3f" y="%.3f" freecad:editable="%s"%s style="font-size:%gpx;'
                  'text-anchor:%s%s"><tspan>%s</tspan></text>'
                  % (x, y + k * pitch, _esc(name), auto, c["size"], anchor, weight, _esc(default)))
                if preview and not default:
                    a('<text x="%.3f" y="%.3f" style="font-size:%gpx;text-anchor:%s;fill:#9aa5b1;'
                      'font-style:italic">%s</text>'
                      % (x, y + k * pitch, min(c["size"], 3.0), anchor, _esc(name)))
        elif c["kind"] == "surface":
            _surface(a, c, cx0, cy0, weight, preview)
        elif c["kind"] == "text" and c["text"]:
            tl = c["text"].replace("{size}", size_letter).splitlines() or [""]
            pitch = c["size"] * 1.25
            if c["vertical"]:
                mx, my = cx0 + c["w"] / 2.0, cy0 + c["h"] / 2.0
                bx = mx - pitch * (len(tl) - 1) / 2.0 + c["size"] * 0.36
                for k, ln in enumerate(tl):
                    xx = bx + k * pitch
                    a('<text x="%.3f" y="%.3f" transform="rotate(-90 %.3f %.3f)" '
                      'style="font-size:%gpx;text-anchor:middle%s">%s</text>'
                      % (xx, my, xx, my, c["size"], weight, _esc(ln)))
            else:
                x, anchor = _anchor_x(c, x0)
                y = cy0 + c["h"] / 2.0 - pitch * (len(tl) - 1) / 2.0 + c["size"] * 0.36
                for k, ln in enumerate(tl):
                    a('<text x="%.3f" y="%.3f" style="font-size:%gpx;text-anchor:%s%s">%s</text>'
                      % (x, y + k * pitch, c["size"], anchor, weight, _esc(ln)))
        elif c["kind"] == "tolerance":
            _tolerance(a, c, cx0, cy0, preview)
    a('</g>')
    for c in d["cells"]:
        cx0, cy0 = x0 + c["x"], y0 + c["y"]
        if c["kind"] == "logo":
            a(_logo(c, cx0, cy0, preview))
        elif c["kind"] == "projection":
            top = CAPTION_SIZE + 0.8 if c["label"] else 0.0
            h = min(c["h"] - top - 2.0, c["w"] / 2.4)
            if h > 1.0:
                a(templates.projection_symbol(cx0 + c["w"] / 2.0, cy0 + top + (c["h"] - top) / 2.0,
                                              first_angle, h=h))
    return "\n".join(out)


def _tolerance(a, c, cx0, cy0, preview):
    """Tolerance-by-size cell: a heading, then range / tolerance rows."""
    head = max(c["size"] * 1.6, 4.0) if c["label"] else 0.0
    rows = c["rows"]
    rh = (c["h"] - head) / rows
    lines = []
    if head:
        a('<text x="%.3f" y="%.3f" style="font-size:%gpx;text-anchor:middle;font-weight:bold">'
          '%s</text>' % (cx0 + c["w"] / 2.0, cy0 + head / 2.0 + c["size"] * 0.36, c["size"],
                         _esc(c["label"])))
        lines.append("M %.3f,%.3f H %.3f" % (cx0, cy0 + head, cx0 + c["w"]))
    for r in range(1, rows):
        y = cy0 + head + r * rh
        lines.append("M %.3f,%.3f H %.3f" % (cx0, y, cx0 + c["w"]))
    fs = min(c["size"], rh * 0.62)
    weight = ";font-weight:bold" if c["bold"] else ""
    for r in range(rows):
        y = cy0 + head + r * rh + rh / 2.0 + fs * 0.36
        a('<text x="%.3f" y="%.3f" freecad:editable="%s" style="font-size:%gpx;'
          'text-anchor:start%s"><tspan></tspan></text>'
          % (cx0 + 1.2, y, TOL_RANGE % (r + 1), fs, weight))
        a('<text x="%.3f" y="%.3f" freecad:editable="%s" style="font-size:%gpx;'
          'text-anchor:end%s"><tspan></tspan></text>'
          % (cx0 + c["w"] - 1.2, y, TOL_VALUE % (r + 1), fs, weight))
        if preview:
            a('<text x="%.3f" y="%.3f" style="font-size:%gpx;fill:#9aa5b1;font-style:italic">'
              'range %d</text>' % (cx0 + 1.2, y, fs * 0.8, r + 1))
    if lines:
        a('<path style="fill:none;stroke:#000000;stroke-width:0.25" d="%s"/>' % " ".join(lines))


def _logo(c, cx0, cy0, preview):
    top = CAPTION_SIZE + 0.8 if c["label"] else 0.0
    bw, bh = c["w"] - 2.0, c["h"] - top - 2.0
    if not c.get("image"):
        if not preview:
            return ""
        return ('<text x="%.3f" y="%.3f" style="font-size:2.5px;text-anchor:middle;fill:#9aa5b1;'
                'font-style:italic;font-family:sans-serif">picture</text>'
                % (cx0 + c["w"] / 2.0, cy0 + c["h"] / 2.0 + 1.0))
    px = image_size(c) or (bw, bh)
    k = min(bw / float(px[0] or 1), bh / float(px[1] or 1))
    w, h = px[0] * k, px[1] * k
    if c["align"] == "left":
        x = cx0 + 1.0
    elif c["align"] == "right":
        x = cx0 + c["w"] - 1.0 - w
    else:
        x = cx0 + (c["w"] - w) / 2.0
    y = cy0 + top + 1.0 + (bh - h) / 2.0
    href = "data:image/%s;base64,%s" % (c.get("image_type") or "png", c["image"])
    return ('<image x="%.3f" y="%.3f" width="%.3f" height="%.3f" preserveAspectRatio="xMidYMid meet" '
            'xlink:href="%s"/>' % (x, y, w, h, href))


def preview_svg(d, first_angle=True, margin=2.0):
    """A whole SVG of just the block, for the designer."""
    W, H = d["width"] + 2 * margin, d["height"] + 2 * margin
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace" '
            'width="%gmm" height="%gmm" viewBox="0 0 %g %g">%s</svg>'
            % (W, H, W, H, svg(d, margin, margin, "C", first_angle, preview=True)))


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------
def preset_iso():
    """DrawingKit's ISO block as a design to start from."""
    from . import templates
    d = new_design("ISO (copy)", templates.TB_W, templates.TB_H)
    d["cells"] = []
    for row, x0, x1, label, name, default, _auto, font, align in templates.CELLS:
        d["cells"].append(cell(x0, row * templates.ROW_H, x1 - x0, templates.ROW_H, "field",
                               label=label, field=name, text=default, size=font,
                               align="centre" if align == "middle" else "left"))
    r, x0, x1 = templates.PROJECTION_CELL
    d["cells"].append(cell(x0, r * templates.ROW_H, x1 - x0, templates.ROW_H, "projection"))
    return d


def preset_asme():
    """DrawingKit's ASME (Y14.1 style) block as a design to start from: the
    tolerance block on the left, company, title and numbers on the right."""
    from . import templates as T
    small = T.ANSI_SMALL
    d = new_design("ASME (copy)", T.ANSI_W, T.ANSI_H)
    tw, mx, mw = T.ANSI_TOL_W, T.ANSI_TOL_W, T.ANSI_MAIN_W
    yb = T.ANSI_H - 14.0                        # projection and size row
    # the tolerance lines use the same fields as DrawingKit's ASME block, so
    # the saved defaults (units, .X / .XX ..., angles, notes) fill them
    r = 3.3

    # the notes area is open: its cells have no lines between them
    def txt(x, y, w, text, bold=False):
        return cell(x, y, w, r, "text", text=text, size=small, bold=bold, border=False)

    def fld(x, y, w, name, text="", h=r, lines=1):
        return cell(x, y, w, h, "field", field=name, text=text, size=small, lines=lines,
                    border=False)

    half = tw / 2.0
    C = [
        cell(0, 0, tw, 5, "text", text="UNLESS OTHERWISE SPECIFIED:", size=small, bold=True,
             border=False),
        txt(0, 5, 32, "DIMENSIONS ARE IN"), fld(32, 5, tw - 32, "units", "INCHES"),
        txt(0, 5 + r, tw, "TOLERANCES:", bold=True),
        txt(0, 5 + 2 * r, 11, ".X"), fld(11, 5 + 2 * r, half - 11, "tol_1"),
        txt(half, 5 + 2 * r, 11, ".XX"), fld(half + 11, 5 + 2 * r, half - 11, "tol_2"),
        txt(0, 5 + 3 * r, 11, ".XXX"), fld(11, 5 + 3 * r, half - 11, "tol_3"),
        txt(half, 5 + 3 * r, 11, ".XXXX"), fld(half + 11, 5 + 3 * r, half - 11, "tol_4"),
        txt(0, 5 + 4 * r, 14, "ANGLES"), fld(14, 5 + 4 * r, tw - 14, "tol_angle"),
        fld(0, 5 + 5 * r, tw, "surface_note", h=2 * r, lines=2),
        fld(0, 5 + 7 * r, tw, "edge_note", h=yb - 6 - (5 + 7 * r)),
        cell(0, yb - 6, tw, 6, "field", label="INTERPRET PER", field="general_tolerances",
             text="ASME Y14.5-2018", size=small, align="right"),
        cell(0, yb, tw - 18, 14, "projection"),
        cell(tw - 18, yb, 18, 14, "field", label="SIZE", field="sheet_size", text="{size}",
             size=6.0, bold=True, align="centre"),
        cell(mx, 0, mw, 7, "field", field="legal_owner_1", size=4.5, bold=True, align="centre"),
        cell(mx, 7, mw, 5, "field", field="company_address", size=small, align="centre"),
        cell(mx, 12, mw, 7.5, "field", label="TITLE", field="title", size=4.5, bold=True,
             align="centre"),
        cell(mx, 19.5, mw, 4.5, "field", field="title_2", size=3.2, align="centre"),
    ]
    ys = [0.0]
    for h in T.ANSI_ROWS:
        ys.append(ys[-1] + h)
    for row, x0, x1, label, name, default, _auto, font, align in T.ANSI_CELLS:
        C.append(cell(mx + x0, ys[row], x1 - x0, T.ANSI_ROWS[row], "field", label=label,
                      field=name, text=default, size=font,
                      bold=name in ("drawing_number", "revision_index"),
                      align="centre" if align == "middle" else "left"))
    d["cells"] = C
    return d


def preset_size_table():
    """A block with the tolerances by size in it, a logo and the people
    who drew, checked and approved the drawing."""
    d = new_design("Size tolerances + logo", 200.0, 50.0)
    C = []
    C.append(cell(0, 0, 9, 50, "text", text="CONFIDENTIAL", size=5.0, bold=True, vertical=True,
                  align="centre"))
    C.append(cell(9, 0, 45, 50, "tolerance", label="TOLERANCE", size=3.2, bold=True, rows=6))
    C.append(cell(54, 0, 76, 18, "logo", align="centre"))
    C.append(cell(54, 18, 76, 21, "field", label="TITLE", field="title", size=4.0))
    C.append(cell(54, 39, 56, 11, "field", label="DRAWING NO.", field="drawing_number", size=4.5,
                  bold=True))
    C.append(cell(110, 39, 20, 11, "field", label="REV", field="revision_index", text="A",
                  size=4.5, bold=True, align="centre"))
    C.append(cell(130, 0, 70, 11, "field", label="MATERIAL", field="part_material", size=4.0,
                  bold=True, align="centre"))
    for k, (who, f, fd) in enumerate((("DRAWN BY", "creator", "date_of_issue"),
                                      ("CHECKED BY", "checked_by", "checked_date"),
                                      ("APPROVED", "approval_person", "approved_date"))):
        y = 11 + k * 9.33333
        C.append(cell(130, y, 24, 9.33333, "text", text=who, size=2.3, bold=True, align="centre"))
        C.append(cell(154, y, 25, 9.33333, "field", field=f, size=2.8, align="centre"))
        C.append(cell(179, y, 21, 9.33333, "field", field=fd, size=2.5, align="centre"))
    C.append(cell(130, 39, 22, 11, "field", label="SCALE", field="scale", text="1 : 1", size=4.0,
                  align="centre"))
    C.append(cell(152, 39, 48, 11, "field", label="SHEET", field="sheet_number", text="1 OF 1",
                  size=4.0, bold=True, align="centre"))
    d["cells"] = C
    return d


def preset_notes():
    """A wide block with room for the general notes: tolerances, edges,
    draft and radii, surface finish, specification, hardness, material,
    item number, drawn and approved."""
    d = new_design("Notes + inch tolerances (wide)", 280.0, 50.0)
    C = [
        cell(0, 0, 130, 7, "text", text="UNLESS OTHERWISE SPECIFIED", size=3.2, align="centre"),
        cell(0, 7, 42, 36, "field", field="tolerance_notes", lines=7, size=2.5, align="centre",
             text="TOLERANCES (INCH)\n.XX  ±.01\n.XXX  ±.005\nANGLES  ±.5°\nALL DIMENSIONS\n"
                  "ARE IN INCHES\n"),
        cell(0, 43, 42, 7, "text", text="DO NOT SCALE", size=3.0, align="centre"),
        cell(42, 7, 50, 10, "field", field="edge_note", lines=2, size=2.3, align="centre",
             text="REMOVE ALL BURRS\nAND SHARP EDGES"),
        cell(92, 7, 38, 10, "field", label="DRAFT ANGLE", field="draft_angle", size=2.8,
             align="centre"),
        cell(42, 17, 44, 9, "field", label="FILLET RADII", field="fillet_radii", size=2.8,
             align="centre"),
        cell(86, 17, 44, 9, "field", label="CORNER RADII", field="corner_radii", size=2.8,
             align="centre"),
        cell(42, 26, 88, 24, "surface", label="SURFACE FINISH, MICROINCH Ra\nMAX ALL MACHINED SURFACES",
             field="surface_finish", text="64\nMACHINE", size=3.5, align="centre"),
        cell(130, 0, 65, 26, "field", label="SPECIFICATIONS", field="specification", lines=2,
             size=4.0, align="centre"),
        cell(130, 26, 65, 13, "field", label="NOTES", field="general_notes", lines=2, size=2.5),
        cell(130, 39, 65, 11, "field", label="HARDNESS", field="hardness", size=3.0,
             align="centre"),
        cell(195, 0, 85, 11, "field", label="DESCRIPTION", field="title", size=3.5,
             align="centre"),
        cell(195, 11, 14, 13, "field", label="SCALE", field="scale", text="1:1", size=3.0,
             align="centre"),
        cell(209, 11, 71, 13, "field", label="MATERIAL", field="part_material", size=3.5,
             align="centre"),
        cell(195, 24, 14, 15, "field", label="SIZE", field="sheet_size", text="{size}", size=6.0,
             align="centre"),
        cell(209, 24, 16, 15, "field", label="REV", field="revision_index", text="A", size=5.0,
             align="centre"),
        cell(225, 24, 55, 15, "field", label="ITEM NO.", field="drawing_number", size=5.0,
             align="centre"),
        cell(195, 39, 20, 11, "field", label="DRAWN", field="creator", size=2.8),
        cell(215, 39, 22, 11, "field", label="DATE", field="date_of_issue", size=2.5,
             align="centre"),
        cell(237, 39, 21, 11, "field", label="APPROVED", field="approval_person", size=2.8),
        cell(258, 39, 22, 11, "field", label="DATE", field="approved_date", size=2.5,
             align="centre"),
    ]
    d["cells"] = C
    # drawn on a 280 mm grid, kept at the ISO 7200 width: 180 mm, so the
    # same block fits every sheet down to A4 portrait
    resize(d, WIDE, 50.0)
    for c in d["cells"]:
        c["size"] = round(max(1.8, c["size"] * 0.72), 2)
    d["name"] = "Notes + inch tolerances"
    return d


WIDE = 180.0          # ISO 7200: one title block that fits every sheet, A4 portrait too
SHRINK = 0.85         # a wider block is drawn up to this much smaller to fit a sheet


def scaled(d, k):
    """A copy of the design k times the size, text too (not under 1.5 mm)."""
    out = copy_of(d)
    resize(out, d["width"] * k, d["height"] * k)
    for c in out["cells"]:
        c["size"] = round(max(1.5, c["size"] * k), 2)
    return out


def warnings(d):
    """Things worth a second look that don't stop the design working."""
    out = []
    if d["width"] > WIDE + 1e-6:
        out.append("%.0f mm wide: title blocks are usually at most %.0f mm (ISO 7200), so one "
                   "block fits every sheet. On A4 landscape this one takes %.0f%% of the "
                   "drawing's width; on A4 portrait it %s."
                   % (d["width"], WIDE, 100.0 * d["width"] / 267.0,
                      "is drawn smaller to fit" if d["width"] * SHRINK <= 180.0 + 1e-6
                      else "doesn't fit (the ISO block is used)"))
    return out


def preset_blank():
    return new_design("Blank", 180.0, 40.0)


PRESETS = [("Blank 180 x 40", preset_blank), ("Copy of DrawingKit ISO", preset_iso),
           ("Copy of DrawingKit ASME", preset_asme),
           ("Tolerances by size + logo", preset_size_table),
           ("Notes + inch tolerances", preset_notes)]


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def user_dir():
    return os.path.join(App.getUserAppDataDir(), "DrawingKit", "TitleBlocks")


def company_dir():
    """'TitleBlocks' next to the company defaults file, if one is set."""
    from . import titleblock
    f = titleblock.company_file()
    return os.path.join(os.path.dirname(os.path.abspath(f)), "TitleBlocks") if f else ""


def designs():
    """{slug: (design, path, 'mine'|'company')}; yours win over the company's
    of the same name."""
    out = {}
    for where, folder in (("company", company_dir()), ("mine", user_dir())):
        if not folder or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as f:
                    d = clean(json.load(f))
            except (OSError, ValueError) as e:
                App.Console.PrintWarning("DrawingKit: can't read title block %s: %s\n" % (path, e))
                continue
            out[slug(d["name"])] = (d, path, where)
    return out


def find(key):
    """Design by slug (or name), or None."""
    ds = designs()
    hit = ds.get(key) or ds.get(slug(key))
    return hit[0] if hit else None


def save(d, where="mine"):
    folder = company_dir() if where == "company" else user_dir()
    if not folder:
        raise ValueError("Set a company defaults file first (Title block form): company "
                         "title blocks are kept next to it")
    os.makedirs(folder, exist_ok=True)
    d = clean(d)
    path = os.path.join(folder, slug(d["name"]) + ".json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return path


def delete(key):
    ds = designs()
    hit = ds.get(key)
    if hit:
        os.remove(hit[1])
        return hit[1]
    return None


def copy_of(d):
    return copy.deepcopy(d)
