# SPDX-License-Identifier: LGPL-2.1-or-later
"""A title block design read from an SVG template.

The template's straight lines in its title block become the cells of a
design (blocks.py), its editable texts become field cells and its fixed
texts become captions or text cells. The design can then be changed in the
title block designer like any other.

Coordinates here are millimetres on the sheet, y down (as in the SVG).
"""

import base64
import math
import os
import re
import xml.etree.ElementTree as ET

from . import blocks
from .realtext import compose, parse_transform, _local

SNAP = 0.6          # lines closer than this are one line (mm)
SLIVER = 1.5        # rows or columns thinner than this are merged into a neighbour
COVER = 0.6         # a line between two cells has to run along this share of the edge
SKIP = ("defs", "symbol", "metadata", "title", "desc", "clipPath", "mask", "pattern",
        "marker", "style", "script", "namedview")
_UNITS = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0, "pc": 25.4 / 6.0,
          "px": 25.4 / 96.0, "": 1.0}       # no unit: mm, as FreeCAD reads templates
_TOK = re.compile(r"[MmLlHhVvZzCcSsQqTtAa]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}


class ImportError_(ValueError):
    """The file can't be made into a title block (the message says why)."""


# ---------------------------------------------------------------------------
# reading the SVG
# ---------------------------------------------------------------------------
def _floats(s):
    return [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", s or "")]


def _length_mm(v):
    m = re.fullmatch(r"\s*([-+\d.eE]+)\s*([a-z]*)\s*", v or "")
    if not m or m.group(2) not in _UNITS:
        return None
    try:
        return float(m.group(1)) * _UNITS[m.group(2)]
    except ValueError:
        return None


def page_transform(root):
    """(matrix from user units to sheet mm, sheet width, sheet height)."""
    vb = _floats(root.get("viewBox"))
    w, h = _length_mm(root.get("width")), _length_mm(root.get("height"))
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        # FreeCAD's templates give the size in mm; without units the viewBox is taken as mm
        sx = w / vb[2] if w else 1.0
        sy = h / vb[3] if h else 1.0
        return (sx, 0.0, 0.0, sy, -vb[0] * sx, -vb[1] * sy), vb[2] * sx, vb[3] * sy
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), w or 420.0, h or 297.0


def _path_lines(d):
    """Straight pieces of an SVG path (curves are skipped) in its own units."""
    toks = _TOK.findall(d or "")
    out = []
    i, cmd = 0, None
    x = y = sx = sy = 0.0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if abs(x - sx) + abs(y - sy) > 1e-9:
                    out.append((x, y, sx, sy))
                x, y = sx, sy
            continue
        if cmd is None or cmd in "Zz":
            break
        n = _ARGS[cmd.upper()]
        if i + n > len(toks) or any(v.isalpha() for v in toks[i:i + n]):
            break
        a = [float(v) for v in toks[i:i + n]]
        i += n
        rel = cmd.islower()
        big = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if big == "M":
            x, y = ox + a[0], oy + a[1]
            sx, sy = x, y
            cmd = "l" if rel else "L"            # more pairs after M are lines
        elif big == "L":
            nx, ny = ox + a[0], oy + a[1]
            out.append((x, y, nx, ny))
            x, y = nx, ny
        elif big == "H":
            nx = x + a[0] if rel else a[0]
            out.append((x, y, nx, y))
            x = nx
        elif big == "V":
            ny = y + a[0] if rel else a[0]
            out.append((x, y, x, ny))
            y = ny
        else:
            x, y = ox + a[-2], oy + a[-1]
    return out


def _style(el, inherited):
    st = dict(inherited)
    for k in ("stroke", "fill", "display", "visibility", "font-size", "font-weight",
              "text-anchor"):
        if el.get(k) is not None:
            st[k] = el.get(k).strip()
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def read_svg(svg):
    """{'lines': [(x1, y1, x2, y2)], 'texts': [dict], 'images': [dict],
    'width', 'height'} of an SVG (sheet mm, y down)."""
    try:
        root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
    except ET.ParseError as e:
        raise ImportError_("This isn't a readable SVG file (%s)" % e)
    m0, page_w, page_h = page_transform(root)
    lines, texts, images, outlines = [], [], [], []

    def walk(el, m, st):
        tag = _local(el.tag)
        if tag in SKIP:
            return
        st = _style(el, st)
        if st.get("display") == "none" or st.get("visibility") == "hidden":
            return
        try:
            m = compose(m, parse_transform(el.get("transform")))
        except (ValueError, IndexError):
            pass
        if tag == "text":
            texts.append(_text(el, m, st))
            return
        if tag == "image":
            img = _image(el, m)
            if img:
                images.append(img)
            return
        drawn = st.get("stroke", "none") not in ("none", "") or tag == "line"
        if tag in ("path", "circle", "ellipse", "polygon") and (
                not drawn and st.get("fill", "black") not in ("none", "")
                or tag in ("circle", "ellipse")):
            # filled shapes (texts turned into outlines, symbols) and circles
            p = _floats(el.get("d") or "%s %s" % (el.get("cx"), el.get("cy")))
            if len(p) >= 2:
                outlines.append(_apply(m, p[0], p[1]))
        pieces = []
        if tag == "path" and drawn:
            pieces = _path_lines(el.get("d"))
        elif tag == "line":
            p = [float(el.get(k) or 0) for k in ("x1", "y1", "x2", "y2")]
            pieces = [tuple(p)]
        elif tag == "rect" and drawn:
            x, y = float(_num(el.get("x"))), float(_num(el.get("y")))
            w, h = float(_num(el.get("width"))), float(_num(el.get("height")))
            if w > 0 and h > 0:
                pieces = [(x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h),
                          (x, y + h, x, y)]
        elif tag in ("polyline", "polygon") and drawn:
            p = _floats(el.get("points"))
            pts = list(zip(p[0::2], p[1::2]))
            if tag == "polygon" and len(pts) > 2:
                pts.append(pts[0])
            pieces = [pts[k] + pts[k + 1] for k in range(len(pts) - 1)]
        for x1, y1, x2, y2 in pieces:
            lines.append(_apply(m, x1, y1) + _apply(m, x2, y2))
        for ch in el:
            walk(ch, m, st)

    walk(root, m0, {})
    texts = [t for t in texts if t["text"] or t["field"]]
    return {"lines": lines, "texts": texts, "images": images, "outlines": outlines,
            "width": page_w, "height": page_h}


def _num(v):
    f = _floats(v)
    return f[0] if f else 0.0


def _text(el, m, st):
    x, y = el.get("x"), el.get("y")
    first = None
    for ch in el.iter():
        if _local(ch.tag) == "tspan":
            first = ch
            break
    if first is not None:
        st = _style(first, st)
        if x is None or y is None:
            x, y = x or first.get("x"), y or first.get("y")
    px, py = _apply(m, _num(x), _num(y))
    scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0
    size = None
    fs = st.get("font-size", "")
    v = re.fullmatch(r"\s*([-+\d.eE]+)\s*(px)?\s*", fs)
    if v:
        size = float(v.group(1))
    elif _length_mm(fs) is not None:
        size = _length_mm(fs) / (25.4 / 96.0)      # pt and so on: in user units as px
    size = (size or 3.5) * scale
    name = ""
    for k, val in el.attrib.items():
        if k.endswith("editable"):
            name = val
    lines = []
    for ch in el:
        if _local(ch.tag) == "tspan":
            t = " ".join("".join(ch.itertext()).split())
            if t:
                lines.append(t)
    if not lines:
        t = " ".join("".join(el.itertext()).split())
        lines = [t] if t else []
    weight = st.get("font-weight", "")
    return {"x": px, "y": py, "size": size, "field": name, "text": "\n".join(lines),
            "anchor": st.get("text-anchor", "start"),
            "bold": weight == "bold" or (weight.isdigit() and int(weight) >= 600),
            "vertical": abs(m[1]) > abs(m[0])}


def _image(el, m):
    href = ""
    for k, v in el.attrib.items():
        if k.endswith("href"):
            href = v
    mt = re.match(r"data:image/([a-z+]+);base64,(.*)", href.strip(), re.S)
    if not mt:
        return None
    x, y = _num(el.get("x")), _num(el.get("y"))
    w, h = _num(el.get("width")), _num(el.get("height"))
    cx, cy = _apply(m, x + w / 2.0, y + h / 2.0)
    kind = {"jpg": "jpeg"}.get(mt.group(1), mt.group(1))
    return {"x": cx, "y": cy, "type": kind, "data": "".join(mt.group(2).split())}


# ---------------------------------------------------------------------------
# finding the title block
# ---------------------------------------------------------------------------
def _axis_lines(lines):
    """(vertical [(x, y0, y1)], horizontal [(y, x0, x1)]) of the straight lines."""
    vs, hs = [], []
    for x1, y1, x2, y2 in lines:
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if max(dx, dy) < 0.3:
            continue
        if dx <= 0.02 * dy + 0.05:
            vs.append(((x1 + x2) / 2.0, min(y1, y2), max(y1, y2)))
        elif dy <= 0.02 * dx + 0.05:
            hs.append(((y1 + y2) / 2.0, min(x1, x2), max(x1, x2)))
    return vs, hs


def _core(t):
    """A point well inside a text: (x, y) half a letter above the baseline."""
    w = max(len(t["text"] or t["field"]), 1) * t["size"] * 0.55
    step = min(w / 2.0, t["size"])
    x = t["x"] + {"middle": 0.0, "end": -step}.get(t["anchor"], step)
    return x, t["y"] - t["size"] * 0.35


def _region(path, page_w, page_h):
    """Where the title block is (x0, y0, x1, y1, y down), as DrawingKit
    measures it for the views."""
    from . import core
    _frame, title = core.drawing_area(path, page_w, page_h)
    tx0, ty0, tx1, ty1 = title
    return tx0, page_h - ty1, tx1, page_h - ty0


def find_box(vs, hs, pts):
    """The lines round the points: the nearest line beyond the outermost
    point on each side that runs past the points."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    def edges(lines, lo, hi):
        """Positions of lines beside lo..hi at least 5 mm long (not a tick
        mark or an icon)."""
        return [pos for pos, a, b in lines
                if b - a >= 5.0 and min(b, hi) - max(a, lo) > -0.5]

    left = [x for x in edges(vs, y0, y1) if x < x0]
    right = [x for x in edges(vs, y0, y1) if x > x1]
    top = [y for y in edges(hs, x0, x1) if y < y0]
    bottom = [y for y in edges(hs, x0, x1) if y > y1]
    if not (left and right and top and bottom):
        return None
    return max(left), max(top), min(right), min(bottom)


# ---------------------------------------------------------------------------
# lines into cells
# ---------------------------------------------------------------------------
def _clusters(values, fixed):
    """Positions of lines with near ones joined: [(position, members)].
    ``fixed`` positions (the block's edges) stay where they are."""
    pts = sorted(values + [(f, 1e9) for f in fixed])
    groups = []
    for v, wgt in pts:
        if groups and v - groups[-1][-1][0] <= SNAP:
            groups[-1].append((v, wgt))
        else:
            groups.append([(v, wgt)])
    out = []
    for g in groups:
        big = [v for v, w in g if w >= 1e9]
        pos = big[0] if big else sum(v * w for v, w in g) / sum(w for v, w in g)
        out.append([pos, sum(w for v, w in g), any(w >= 1e9 for v, w in g)])
    # thin strips: drop the weaker of two lines that close together (not an edge)
    changed = True
    while changed and len(out) > 2:
        changed = False
        for k in range(len(out) - 1):
            if out[k + 1][0] - out[k][0] < SLIVER:
                a, b = out[k], out[k + 1]
                drop = k + 1 if (a[2] or (not b[2] and a[1] >= b[1])) else k
                if out[drop][2]:
                    continue
                del out[drop]
                changed = True
                break
    return [p for p, _w, _f in out]


def _nearest(pos, value):
    return min(pos, key=lambda p: abs(p - value))


def _covered(intervals, a, b):
    """How much of a..b the intervals cover."""
    total, cur = 0.0, a
    for s, e in sorted(intervals):
        s, e = max(s, cur), min(e, b)
        if e > s:
            total += e - s
            cur = e
    return total


def _prune(vs, hs, box):
    """Only the lines that end on another line (or on the block's edge) at
    both ends: cell walls do, the strokes of a symbol or a drawing inside a
    cell mostly don't."""
    bx0, by0, bx1, by1 = box
    reach = 2 * SNAP

    def v_end_ok(x, y, hs_):
        if abs(y - by0) <= reach or abs(y - by1) <= reach:
            return True
        return any(abs(hy - y) <= reach and a - reach <= x <= b + reach for hy, a, b in hs_)

    def h_end_ok(y, x, vs_):
        if abs(x - bx0) <= reach or abs(x - bx1) <= reach:
            return True
        return any(abs(vx - x) <= reach and a - reach <= y <= b + reach for vx, a, b in vs_)

    while True:
        nv = [s for s in vs if v_end_ok(s[0], s[1], hs) and v_end_ok(s[0], s[2], hs)]
        nh = [s for s in hs if h_end_ok(s[0], s[1], vs) and h_end_ok(s[0], s[2], vs)]
        if len(nv) == len(vs) and len(nh) == len(hs):
            break
        vs, hs = nv, nh
    return _connected(vs, hs, box)


def _connected(vs, hs, box):
    """Only the lines joined (through other lines) to the block's edge: a
    closed little shape inside a cell (an icon) is not part of the grid."""
    bx0, by0, bx1, by1 = box
    reach = 2 * SNAP
    segs = [("v",) + s for s in vs] + [("h",) + s for s in hs]

    def meets(p, q):
        if p[0] == q[0]:
            return abs(p[1] - q[1]) <= SNAP and min(p[3], q[3]) - max(p[2], q[2]) > -reach
        v, h = (p, q) if p[0] == "v" else (q, p)
        return h[2] - reach <= v[1] <= h[3] + reach and v[2] - reach <= h[1] <= v[3] + reach

    def on_edge(s):
        if s[0] == "v":
            return (abs(s[1] - bx0) <= reach or abs(s[1] - bx1) <= reach
                    or s[2] <= by0 + reach or s[3] >= by1 - reach)
        return (abs(s[1] - by0) <= reach or abs(s[1] - by1) <= reach
                or s[2] <= bx0 + reach or s[3] >= bx1 - reach)

    keep = [on_edge(s) for s in segs]
    todo = [k for k, ok in enumerate(keep) if ok]
    while todo:
        k = todo.pop()
        for j, s in enumerate(segs):
            if not keep[j] and meets(segs[k], s):
                keep[j] = True
                todo.append(j)
    return ([s[1:] for s, ok in zip(segs, keep) if ok and s[0] == "v"],
            [s[1:] for s, ok in zip(segs, keep) if ok and s[0] == "h"])


def grid_cells(vs, hs, box):
    """([(x, y, w, h, open_sides)], dropped): the rectangles that the lines
    inside ``box`` cut it into, and how many lines were not cell walls.
    open_sides: sides with no line (where one area had to be cut into
    rectangles)."""
    bx0, by0, bx1, by1 = box
    inside_v = [(x, max(a, by0), min(b, by1)) for x, a, b in vs
                if bx0 - SNAP <= x <= bx1 + SNAP and min(b, by1) - max(a, by0) > SNAP]
    inside_h = [(y, max(a, bx0), min(b, bx1)) for y, a, b in hs
                if by0 - SNAP <= y <= by1 + SNAP and min(b, bx1) - max(a, bx0) > SNAP]
    count = len(inside_v) + len(inside_h)
    inside_v, inside_h = _prune(inside_v, inside_h, box)
    dropped = count - len(inside_v) - len(inside_h)
    xs = _clusters([(x, b - a) for x, a, b in inside_v if bx0 + SNAP < x < bx1 - SNAP],
                   [bx0, bx1])
    ys = _clusters([(y, b - a) for y, a, b in inside_h if by0 + SNAP < y < by1 - SNAP],
                   [by0, by1])
    walls_v = {}
    for x, a, b in inside_v:
        walls_v.setdefault(_nearest(xs, x), []).append((a, b))
    walls_h = {}
    for y, a, b in inside_h:
        walls_h.setdefault(_nearest(ys, y), []).append((a, b))
    nx, ny = len(xs) - 1, len(ys) - 1

    def wall_right(i, j):          # between column i and i + 1, in row j
        a, b = ys[j], ys[j + 1]
        return _covered(walls_v.get(xs[i + 1], []), a - SNAP, b + SNAP) >= COVER * (b - a)

    def wall_below(i, j):          # between row j and j + 1, in column i
        a, b = xs[i], xs[i + 1]
        return _covered(walls_h.get(ys[j + 1], []), a - SNAP, b + SNAP) >= COVER * (b - a)

    parent = {(i, j): (i, j) for i in range(nx) for j in range(ny)}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for i in range(nx):
        for j in range(ny):
            if i + 1 < nx and not wall_right(i, j):
                parent[find((i, j))] = find((i + 1, j))
            if j + 1 < ny and not wall_below(i, j):
                parent[find((i, j))] = find((i, j + 1))
    comp = {p: find(p) for p in parent}
    used = set()
    rects = []
    for j in range(ny):
        for i in range(nx):
            if (i, j) in used:
                continue
            root = comp[(i, j)]
            i1 = i
            while i1 + 1 < nx and comp[(i1 + 1, j)] == root and (i1 + 1, j) not in used:
                i1 += 1
            j1 = j
            while j1 + 1 < ny and all(comp[(k, j1 + 1)] == root and (k, j1 + 1) not in used
                                      for k in range(i, i1 + 1)):
                j1 += 1
            for k in range(i, i1 + 1):
                for m in range(j, j1 + 1):
                    used.add((k, m))
            rects.append([i, j, i1, j1, root])
    out = []
    for i, j, i1, j1, root in rects:
        open_sides = ""
        # a side is open where the whole side faces the same area (no line there)
        if j1 + 1 < ny and all(comp[(k, j1 + 1)] == root for k in range(i, i1 + 1)):
            open_sides += "b"
        if i1 + 1 < nx and all(comp[(i1 + 1, m)] == root for m in range(j, j1 + 1)):
            open_sides += "r"
        out.append((xs[i] - bx0, ys[j] - by0, xs[i1 + 1] - xs[i], ys[j1 + 1] - ys[j],
                    open_sides))
    return out, dropped


# ---------------------------------------------------------------------------
# texts into the cells
# ---------------------------------------------------------------------------
def _field_name(name, taken):
    n = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "field"
    base, k = n, 2
    while n in taken:
        n = "%s_%d" % (base, k)
        k += 1
    taken.add(n)
    return n


def _align(t, c, bx0):
    if t["anchor"] == "middle":
        return "centre"
    if t["anchor"] == "end":
        return "right"
    mid = bx0 + c["x"] + c["w"] / 2.0
    return "centre" if abs(t["x"] - (bx0 + c["x"])) > c["w"] * 0.3 and t["x"] < mid else "left"


def _width(t):
    return max(len(t["text"] or t["field"]), 1) * t["size"] * 0.55


def _left(t):
    return t["x"] - {"middle": 0.5, "end": 1.0}.get(t["anchor"], 0.0) * _width(t)


def _split_for_fields(c, fields, by0):
    """A cell holding several texts (fields, captions beside them) cut into
    one piece per text (no lines between the pieces). Returns [(cell,
    [text])]."""
    if len(fields) < 2:
        return [(c, fields)]
    rows = []
    for t in sorted(fields, key=lambda t: t["y"]):
        if rows and t["y"] - rows[-1][-1]["y"] < 0.6 * t["size"]:
            rows[-1].append(t)
        else:
            rows.append([t])
    out = []
    if len(rows) > 1:
        cuts = [c["y"]]
        for a, b in zip(rows, rows[1:]):
            lo = max(t["y"] for t in a) - by0
            hi = min(t["y"] - t["size"] for t in b) - by0
            cuts.append(max(cuts[-1] + 1.0, min((lo + hi) / 2.0 + 0.3, c["y"] + c["h"] - 1.0)))
        cuts.append(c["y"] + c["h"])
        for k, row in enumerate(rows):
            piece = dict(c, y=cuts[k], h=cuts[k + 1] - cuts[k])
            if k + 1 < len(rows):
                piece["border"] = blocks.sides(piece["border"].replace("b", ""))
            out.extend(_split_for_fields(piece, row, by0) if len(row) > 1 else [(piece, row)])
        return out
    row = sorted(fields, key=lambda t: t["x"])
    return _split_columns(c, row)


def _split_columns(c, row):
    out = []
    x0 = c["x"]
    for k, t in enumerate(row):
        if k + 1 < len(row):
            nxt = row[k + 1]
            # just before the next text starts; halfway if they overlap
            cut = _left(nxt) - 0.8
            if cut < _core(t)[0] + 0.5:
                cut = (_core(t)[0] + _core(nxt)[0]) / 2.0
            x1 = max(x0 + 1.0, cut - row[0]["_bx0"])
            x1 = min(x1, c["x"] + c["w"] - 1.0)
        else:
            x1 = c["x"] + c["w"]
        piece = dict(c, x=x0, w=x1 - x0)
        if k + 1 < len(row):
            piece["border"] = blocks.sides(piece["border"].replace("r", ""))
        out.append((piece, [t]))
        x0 = x1
    return out


def design_from_svg(svg, path="", name=""):
    """(design, notes) made from an SVG template's title block. ``notes``
    are sentences about what could not be brought over."""
    d, notes, _box = import_svg(svg, path, name)
    return d, notes


def import_svg(svg, path="", name=""):
    """(design, notes, box): box is where the block was on the sheet (mm, y down)."""
    data = read_svg(svg)
    vs, hs = _axis_lines(data["lines"])
    if path:
        rx0, ry0, rx1, ry1 = _region(path, data["width"], data["height"])
    else:
        rx0, ry0, rx1, ry1 = 0.0, data["height"] * 0.55, data["width"], data["height"]
    title_texts = []
    for t in data["texts"]:
        if not t["field"] and len(t["text"].strip()) <= 2:
            continue                          # zone letters, sheet size
        px, py = _core(t)
        if rx0 - 1.0 <= px <= rx1 + 1.0 and ry0 - 1.0 <= py <= ry1 + 1.0:
            title_texts.append(t)
    if not title_texts:
        raise ImportError_("No title block texts found in the lower right of this sheet")
    box = find_box(vs, hs, [_core(t) for t in title_texts])
    if box is None:
        raise ImportError_("No lines found round the title block texts")
    bx0, by0, bx1, by1 = box
    notes = []
    rects, dropped = grid_cells(vs, hs, box)
    cells = []
    for x, y, w, h, open_sides in rects:
        c = blocks.cell(x, y, w, h)
        c["border"] = blocks.sides("".join(s for s in blocks.SIDES if s not in open_sides))
        cells.append(c)

    def cell_of(px, py):
        for c in cells:
            if (c["x"] - 0.2 <= px - bx0 <= c["x"] + c["w"] + 0.2
                    and c["y"] - 0.2 <= py - by0 <= c["y"] + c["h"] + 0.2):
                return c
        return None

    held = {id(c): [] for c in cells}
    outside = 0
    for t in data["texts"]:
        px, py = _core(t)
        if not (bx0 <= px <= bx1 and by0 <= py <= by1):
            if t in title_texts:
                outside += 1
            continue
        t["_bx0"] = bx0
        c = cell_of(px, py)
        if c is not None:
            held[id(c)].append(t)
    if outside:
        notes.append("%d text(s) outside the lines found were left out" % outside)
    taken = set()
    final = []
    for c in cells:
        ts = held[id(c)]
        fields = [t for t in ts if t["field"]]
        fixed = sorted((t for t in ts if not t["field"]), key=lambda t: (round(t["y"], 1), t["x"]))
        if not fields:
            if fixed:
                c.update(kind="text", text="\n".join(t["text"] for t in fixed),
                         size=round(max(t["size"] for t in fixed), 2),
                         bold=fixed[0]["bold"], vertical=fixed[0]["vertical"],
                         align=_align(fixed[0], c, bx0))
            final.append(c)
            continue
        # a caption on the same line as a field, before it ("Drawn:  J. Smith"),
        # gets a cell of its own beside the field; other captions go over it
        beside = [f for f in fixed if any(
            abs(f["y"] - t["y"]) < 0.6 * max(f["size"], t["size"])
            and _left(f) + _width(f) <= _left(t) + 1.0 for t in fields)]
        fixed = [f for f in fixed if f not in beside]
        for piece, fs in _split_for_fields(c, fields + beside, by0):
            t = fs[0]
            if not t["field"]:
                piece.update(kind="text", text=t["text"], size=round(t["size"], 2),
                             bold=t["bold"], align=_align(t, piece, bx0))
                final.append(piece)
                continue
            caps = [f for f in fixed
                    if piece["y"] - 0.2 <= _core(f)[1] - by0 <= piece["y"] + piece["h"] + 0.2
                    and piece["x"] - 0.2 <= _core(f)[0] - bx0 <= piece["x"] + piece["w"] + 0.2]
            piece.update(kind="field", field=_field_name(t["field"], taken),
                         text=t["text"], size=round(t["size"], 2), bold=t["bold"],
                         align=_align(t, piece, bx0),
                         label="\n".join(f["text"] for f in caps))
            final.append(piece)
    outlines = sum(1 for x, y in data["outlines"] if bx0 <= x <= bx1 and by0 <= y <= by1)
    if outlines or dropped:
        notes.append("Shapes drawn inside cells (symbols, and texts turned into outlines) "
                     "can't be brought over; add a Projection or Picture cell, or type the "
                     "captions in")
    for img in data["images"]:
        c = cell_of(img["x"], img["y"]) if bx0 <= img["x"] <= bx1 and by0 <= img["y"] <= by1 else None
        if c is not None and any(c is f for f in final) and c["kind"] == "empty":
            c.update(kind="logo", image=img["data"], image_type=img["type"])
    stem = name or (os.path.splitext(os.path.basename(path))[0] if path else "Imported")
    d = blocks.new_design(stem, bx1 - bx0, by1 - by0)
    d["cells"] = final
    d = blocks.clean(d)
    d["width"], d["height"] = round(bx1 - bx0, 3), round(by1 - by0, 3)
    for c in d["cells"]:
        # round the edges, not the sizes, so cells that meet still meet exactly
        for a, s in (("x", "w"), ("y", "h")):
            end = round(c[a] + c[s], 3)
            c[a] = round(c[a], 3)
            c[s] = end - c[a]
    if not any(c["kind"] == "field" for c in d["cells"]):
        notes.append("The template has no editable fields; every text came in as fixed text")
    return d, notes, box


def design_from_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ImportError_("Can't read %s (%s)" % (os.path.basename(path), e))
    return design_from_svg(svg, path)
