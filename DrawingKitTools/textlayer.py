# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real text in exported PDFs, and the list of characteristics beside them.

TechDraw's PDF export draws every letter as an outline, so nothing can
read, search or copy the text of a sheet. DrawingKit knows every text it
put there - dimension values and tolerances, notes, title block fields,
table cells - and where it sits, so after TechDraw has written the PDF:

* ``add_text_layer`` appends an invisible text layer at those spots (PDF
  text render mode 3, as scanners do for OCR'd pages, but exact). The page
  looks the same; readers, search and copy-paste get the words. It is an
  incremental update: TechDraw's drawing is left byte for byte as it was.
* ``characteristics`` lists what an inspector checks - each dimension with
  its nominal, tolerance and type, each note, the title block's general
  tolerances - with its zone on the sheet; ``write_characteristics`` saves
  it as JSON beside the PDF, for an inspection plan or ballooning tool.

No PDF library needed: plain Python on the PDF TechDraw (Qt) writes.
"""

import json
import math
import os
import re
import xml.etree.ElementTree as ET

import FreeCAD as App

from . import core

PT = 72.0 / 25.4                     # PDF points per mm
FONT_KEY = "FDK0"                    # resource name of the layer's font


# ---------------------------------------------------------------------------
# What text is on the sheet, and where (page mm, Y up)
# ---------------------------------------------------------------------------
def _style(el, inherited):
    st = dict(inherited)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    for k in ("font-size", "text-anchor", "font-weight"):
        if el.get(k):
            st[k] = el.get(k)
    return st


def _size(st, default=3.5):
    m = re.match(r"\s*([\d.]+)", st.get("font-size", "") or "")
    return float(m.group(1)) if m else default


def _rotation(el):
    m = re.search(r"rotate\(\s*([-\d.]+)", el.get("transform") or "")
    return -float(m.group(1)) if m else 0.0       # SVG turns clockwise, y down


def _local(tag):
    return tag.split("}")[-1]


def svg_texts(svg_text, values=None, names=None):
    """[(text, x, y, size, anchor, angle, editable name)] of an SVG's text
    elements (SVG units, y down). Editable texts take their value from
    ``values`` ({name: text}); ``names`` lists editable names in document
    order for symbols that keep their values as a plain list."""
    try:
        root = ET.fromstring(svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text)
    except ET.ParseError:
        return []
    out = []
    values = values or {}

    def walk(el, st):
        st = _style(el, st)
        if _local(el.tag) == "text":
            name = None
            for k, v in el.attrib.items():
                if k.endswith("editable"):
                    name = v
            txt = values.get(name) if name is not None and name in values else \
                "".join(el.itertext())
            try:
                x, y = float(el.get("x", 0)), float(el.get("y", 0))
            except ValueError:
                return
            if txt and txt.strip():
                out.append((txt.strip(), x, y, _size(st), st.get("text-anchor", "start"),
                            _rotation(el), name))
            return
        for ch in el:
            walk(ch, st)
    walk(root, {})
    return out


def _viewbox(svg_text):
    m = re.search(r'viewBox="\s*([-\d.]+)[\s,]+([-\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)', svg_text)
    mw = re.search(r'width="([\d.]+)mm"', svg_text)
    mh = re.search(r'height="([\d.]+)mm"', svg_text)
    if not mw or not mh:
        return None
    w, h = float(mw.group(1)), float(mh.group(1))
    if m:
        return [float(g) for g in m.groups()], w, h
    return [0.0, 0.0, w, h], w, h


def _template_texts(page):
    tpl = page.Template
    path = getattr(tpl, "Template", "") or ""
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return []
    W, H = core.page_size(page)
    vb = _viewbox(svg)
    kx = ky = 1.0
    if vb:
        (_x, _y, vw, vh), w, h = vb
        kx, ky = W / vw if vw else 1.0, H / vh if vh else 1.0
    vals = dict(getattr(tpl, "EditableTexts", {}) or {})
    out = []
    for txt, x, y, size, anchor, ang, name in svg_texts(svg, vals):
        out.append({"text": txt, "x": x * kx, "y": H - y * ky, "size": size * ky,
                    "anchor": anchor, "angle": ang, "source": "title block",
                    "field": name})
    return out


def _symbol_texts(sym):
    svg = getattr(sym, "Symbol", "") or ""
    vb = _viewbox(svg)
    if not vb:
        return []
    (vx, vy, vw, vh), w, h = vb
    s = float(getattr(sym, "Scale", 1.0) or 1.0)
    from . import annot
    p = annot._params(sym)
    names = p.get("fields", [])
    vals = dict(zip(names, list(getattr(sym, "EditableTexts", []) or [])))
    x0 = float(sym.X) - w * s / 2.0
    ytop = float(sym.Y) + h * s / 2.0
    kx, ky = w * s / vw, h * s / vh
    out = []
    for txt, x, y, size, anchor, ang, name in svg_texts(svg, vals):
        out.append({"text": txt, "x": x0 + (x - vx) * kx, "y": ytop - (y - vy) * ky,
                    "size": size * ky, "anchor": anchor, "angle": ang,
                    "source": getattr(sym, annot.KIND, "") or "symbol", "field": name,
                    "object": sym.Name})
    return out


def _drop_zeros(txt):
    """'8.00' -> '8', '12.50' -> '12.5' in every number of a text."""
    return re.sub(r"(\d+)\.(\d*?)0+(?!\d)", lambda m: m.group(1) + ("." + m.group(2) if m.group(2) else ""),
                  txt)


def _fmt(spec, value):
    """A value through a TechDraw format spec ('%.2f', '%.3w', '%+.2w')."""
    m = re.search(r"%([+]?)\.?(\d*)([fwgeFWG])", spec or "")
    if not m:
        return spec or ""
    places = int(m.group(2)) if m.group(2) else 2
    s = ("%" + m.group(1) + ".%df") % places % value
    if m.group(3) in "wW":
        s = _drop_zeros(s)
    return spec[:m.start()] + s + spec[m.end():]


def dimension_record(dim):
    """Everything a dimension says, exactly as printed: {text, value_text
    (the number as printed: '8', '.500'), value, places, upper, lower
    (printed tolerance strings or None), limits ([hi, lo] or None), type
    (bilateral | unilateral | limit | basic | reference | general),
    count}."""
    from . import dims
    spec = dim.FormatSpec or ""
    try:
        value = float(dim.getRawValue())
    except Exception:
        value = None
    if dims.is_limits(dim):
        hi, lo = dim.FormatSpecOverTolerance, dim.FormatSpecUnderTolerance
        pre = spec.strip()
        return {"text": ("%s %s / %s" % (pre, hi, lo)).strip(), "value_text": None,
                "value": value, "places": None, "upper": None, "lower": None,
                "limits": [hi, lo], "type": "limit", "count": _count(spec)}
    if dim.Arbitrary:
        printed = spec
    else:
        try:
            printed = dim.getText()
        except Exception:
            printed = ""
        printed = (printed or "").replace(" mm", "").replace("mm", "").strip()
        if not printed and value is not None:
            printed = _fmt(spec, value)
        if "w" in spec:
            printed = _drop_zeros(printed)    # %w: as drawn, without trailing zeros
    # the number as printed (after any 4X count)
    body = re.sub(r"^\s*\d+\s*[Xx×]\s*", "", printed)
    num = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", body)
    value_text = num.group(0) if num else None
    places = len(value_text.split(".")[1]) if value_text and "." in value_text else 0
    reference = printed.strip().startswith("(") and printed.strip().endswith(")")
    over = under = 0.0
    try:
        over = float(getattr(dim.OverTolerance, "Value", dim.OverTolerance))
        under = float(getattr(dim.UnderTolerance, "Value", dim.UnderTolerance))
    except Exception:
        pass
    upper = lower = None
    has_tol = abs(over) > 1e-12 or abs(under) > 1e-12
    if getattr(dim, "TheoreticalExact", False):
        ttype = "basic"
    elif reference:
        ttype = "reference"
    elif has_tol:
        if dim.ArbitraryTolerances:
            upper, lower = dim.FormatSpecOverTolerance, dim.FormatSpecUnderTolerance
        else:
            upper = _fmt(dim.FormatSpecOverTolerance or "%+.2w", over)
            lower = _fmt(dim.FormatSpecUnderTolerance or "%+.2w", under)
        if abs(over + under) < 1e-12:
            ttype = "bilateral"
            upper = upper.lstrip("+±")
            lower = "-" + upper
        elif abs(over) < 1e-12 or abs(under) < 1e-12:
            ttype = "unilateral"
        else:
            ttype = "bilateral"            # unequal, either side of the nominal
    else:
        ttype = "general"
    text = printed
    if ttype == "bilateral" and abs(over + under) < 1e-12:
        text += " ±%s" % upper
    elif upper is not None:
        text += " %s/%s" % (upper, lower)
    return {"text": text.strip(), "value_text": value_text, "value": value, "places": places,
            "upper": upper, "lower": lower, "limits": None, "type": ttype,
            "count": _count(spec)}


def _count(spec):
    m = re.match(r"\s*(\d+)\s*[Xx×]", spec or "")
    return int(m.group(1)) if m else 1


def dimension_text(dim):
    """What a dimension shows: value with its Ø / R and any count, and the
    tolerance after it ('4X ⌀8 THRU', '⌀40 ±0.1', '25 +0.1/-0.05')."""
    return dimension_record(dim)["text"]


def _dim_angle(dim):
    from . import annot
    if dim.Type in ("Diameter", "Radius"):
        vo = getattr(dim, "ViewObject", None)
        if vo is not None and "Referencing" in str(getattr(vo, "StandardAndStyle", "")):
            return 0.0
    if annot._text_upright(dim):
        return 90.0
    try:
        _p0, _p1, u = annot.dimension_ends(dim)
        a = math.degrees(math.atan2(u[1], u[0]))
    except Exception:
        return 0.0
    while a > 90.0 + 1e-6:
        a -= 180.0
    while a <= -90.0 + 1e-6:
        a += 180.0
    return a


def _dim_size(dim):
    vo = getattr(dim, "ViewObject", None)
    try:
        return float(getattr(vo.Fontsize, "Value", vo.Fontsize))
    except Exception:
        return 3.5


def page_texts(page):
    """Every text DrawingKit and TechDraw put on the sheet, as
    {text, x, y (page mm, Y up; the text's anchor point), size (mm),
    anchor ('start'|'middle'|'end'), angle (degrees, anticlockwise),
    source, ...}."""
    out = _template_texts(page)
    for v in page.Views:
        try:
            if v.isDerivedFrom("TechDraw::DrawViewDimension"):
                refs = v.References2D
                if not refs:
                    continue
                view = refs[0][0]
                cx, cy = core.view_center(view)
                txt = dimension_text(v)
                if txt:
                    out.append({"text": txt, "x": cx + float(v.X), "y": cy + float(v.Y),
                                "size": _dim_size(v), "anchor": "middle", "angle": _dim_angle(v),
                                "source": "dimension", "object": v.Name, "view": view.Label})
            elif v.isDerivedFrom("TechDraw::DrawViewBalloon"):
                sv = getattr(v, "SourceView", None)
                if sv is None or not str(v.Text):
                    continue
                cx, cy = core.view_center(sv)
                s = core.view_scale(sv)
                out.append({"text": str(v.Text), "x": cx + float(v.X) * s, "y": cy + float(v.Y) * s,
                            "size": _dim_size(v), "anchor": "middle", "angle": 0.0,
                            "source": "balloon", "object": v.Name})
            elif v.isDerivedFrom("TechDraw::DrawViewSymbol"):
                out += _symbol_texts(v)
            elif v.isDerivedFrom("TechDraw::DrawViewAnnotation"):
                size = float(v.TextSize)
                for i, ln in enumerate(list(v.Text)):
                    if ln.strip():
                        out.append({"text": ln, "x": float(v.X), "y": float(v.Y) - i * size * 1.6,
                                    "size": size, "anchor": "start", "angle": 0.0,
                                    "source": "annotation", "object": v.Name})
        except Exception as e:
            App.Console.PrintWarning("DrawingKit text layer: %s: %s\n" % (v.Label, e))
    return out


# ---------------------------------------------------------------------------
# Zones (the frame's grid references: 1-8 across, A-F down)
# ---------------------------------------------------------------------------
def zone(page, x, y):
    """'C4' for a page point on a DrawingKit frame, '' elsewhere."""
    from . import templates
    try:
        W, H = core.page_size(page)
    except Exception:
        return ""
    fx0, fy0, fx1, fy1 = 20.0, 10.0, W - 10.0, H - 10.0
    cx, cy = (fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0
    xs = templates._zones(fx0, fx1, cx)
    ys = templates._zones(fy0, fy1, cy)
    yd = H - y                                    # zones count from the top
    col = next((i for i, (a, b) in enumerate(zip(xs, xs[1:])) if a <= x <= b), None)
    row = next((i for i, (a, b) in enumerate(zip(ys, ys[1:])) if a <= yd <= b), None)
    if col is None or row is None:
        return ""
    letters = templates._zone_labels(len(ys) - 1, True)
    return "%s%d" % (letters[row], col + 1)


# ---------------------------------------------------------------------------
# Characteristics
# ---------------------------------------------------------------------------
SCHEMA = 1
# title block fields that are requirements on the part (ballooned too)
REQUIREMENTS = [("part_material", "material"), ("hardness", "hardness"),
                ("heat_treat", "heat treatment"), ("specification", "specification"),
                ("specification_2", "specification"), ("finish", "finish"),
                ("surface_finish", "finish"), ("fillet_radii", "fillet radii"),
                ("corner_radii", "corner radii"), ("draft_angle", "draft angle"),
                ("edge_note", "edges"), ("surface_note", "surface texture")]
GENERAL_FIELDS = ("general_tolerances", "units", "tol_1", "tol_2", "tol_3", "tol_4", "tol_angle")


def _dim_kind(dim):
    t = dim.Type
    return {"Diameter": "diameter", "Radius": "radius", "Angle": "angle", "Angle3Pt": "angle",
            "DistanceX": "length", "DistanceY": "length", "Distance": "length"}.get(t, t.lower())


def _texts(page):
    try:
        return dict(page.Template.EditableTexts)
    except Exception:
        return {}


def drawing_unit(page):
    """'in' or 'mm': what the title block says the dimensions are in."""
    t = _texts(page)
    units = (t.get("units") or "").strip().upper()
    if units in ("IN", "IN.", "INCH", "INCHES", '"'):
        return "in"
    if units in ("MM", "MILLIMETRES", "MILLIMETERS", "MILLIMETRE", "MILLIMETER"):
        return "mm"
    words = " ".join(v for k, v in t.items() if k.startswith("tolerance_notes")).upper()
    if re.search(r"\bMM\b|MILLIMET", words):
        return "mm"
    return "in" if "INCH" in words else "mm"


def values_unit():
    """'in' or 'mm': the unit TechDraw writes dimension values in (FreeCAD's
    unit system)."""
    try:
        schema = App.ParamGet("User parameter:BaseApp/Preferences/Units").GetInt("UserSchema", 0)
    except Exception:
        schema = 0
    return "in" if schema in (2, 3, 5) else "mm"


def _num(text):
    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", text or "")
    return m.group(0) if m else None


def general_tolerances(page):
    """The title block's general tolerances, resolved: {unit,
    linear_by_places {'2': '0.01', ...}, angular, standard_class, bands
    [[over, up_to, tol]], angular_bands}. Empty keys are left out."""
    t = _texts(page)
    out = {"unit": drawing_unit(page)}
    places = {}
    for i in range(1, 5):
        v = _num((t.get("tol_%d" % i) or "").replace("±", ""))
        if v:
            places[str(i)] = v.lstrip("+")
    ang = _num((t.get("tol_angle") or "").replace("±", ""))
    for k in sorted(k for k in t if k.startswith("tolerance_notes")):
        line = (t.get(k) or "").upper()
        m = re.match(r"^\s*\.?(X+)\s*[±+]?\s*([.\d]+)", line.replace(" ", " "))
        if m:
            places[str(len(m.group(1)))] = m.group(2)
        a = re.search(r"ANGL\w*\s*[±+]?\s*([.\d]+)", line)
        if a:
            ang = a.group(1)
    if places:
        out["linear_by_places"] = places
    if ang:
        out["angular"] = ang
    cls = re.search(r"ISO\s*2768\s*-?\s*([fmcv]{1,2})", t.get("general_tolerances") or "", re.I)
    if cls:
        out["standard_class"] = "ISO 2768-%s" % cls.group(1).lower()
    elif (t.get("general_tolerances") or "").strip():
        out["interpret_per"] = t["general_tolerances"].strip()
    # tolerances by size: rows in the block, or the table on the sheet
    data = None
    try:
        from . import titleblock, tables
        data = titleblock.block_tolerance_data(page) or tables.tolerance_data(page)
    except Exception:
        pass
    if data:
        bands = [[a, b, c.lstrip("±")] for a, b, c in data.get("linear", []) if c]
        if bands:
            out["bands"] = bands
        angb = [[a, b, c.lstrip("±")] for a, b, c in data.get("angular", []) if c]
        if angb:
            out["angular_bands"] = angb
        if data.get("angle_all"):
            out["angular"] = data["angle_all"].lstrip("±")
        if data.get("preset") and "standard_class" not in out:
            out["standard_class"] = data["preset"]
    return out


def _in_band(value, over, up_to):
    try:
        lo = float(over) if over not in ("", None) else None
        hi = float(up_to) if up_to not in ("", None) else None
    except ValueError:
        return False
    return (lo is None or value > lo - 1e-9) and (hi is None or value <= hi + 1e-9)


# ISO 2768-1 table 2, external radii and chamfer heights: (over, up to, tol)
ISO2768_BROKEN_EDGES = {
    "f": [("0.5", "3", "0.2"), ("3", "6", "0.5"), ("6", "", "1")],
    "m": [("0.5", "3", "0.2"), ("3", "6", "0.5"), ("6", "", "1")],
    "c": [("0.5", "3", "0.4"), ("3", "6", "1"), ("6", "", "2")],
    "v": [("0.5", "3", "0.4"), ("3", "6", "1"), ("6", "", "2")],
}


def resolve_angle(item, general):
    """General tolerance of an item's angle (a chamfer's 45°): ISO 2768-1
    by the length of the shorter side, else the title block's angle rule."""
    m = re.match(r"ISO 2768-([fmcv])", general.get("standard_class") or "")
    side = item.get("nominal_value")
    angb = general.get("angular_bands")
    if not angb and m:
        from . import tables
        angb = [[a, b, t] for a, b, t in tables.iso2768(m.group(1))["angular"]]
    if angb and side is not None:
        for over, up_to, tol in angb:
            if _in_band(side, over or 0.0, up_to):
                return "±" + str(tol).lstrip("±"), "angle by shorter side%s" % (
                    " (%s)" % general["standard_class"] if m else "")
    if general.get("angular"):
        return "±" + general["angular"].lstrip("±").rstrip("°") + "°", "angular"
    return None, "no general angle tolerance in the title block"


def resolve_general(item, general):
    """The general tolerance that applies to one item, as printed text
    ('±0.2') and where it came from, or (None, why not)."""
    if item.get("tolerance_type") != "general":
        return None, None
    kind = item.get("kind")
    if kind == "angle":
        if general.get("angular"):
            return "±" + general["angular"].lstrip("±"), "angular"
        return None, "no general angle tolerance in the title block"
    value = item.get("nominal_value")
    if value is None:
        return None, "no value"
    bands = general.get("bands")
    cls = general.get("standard_class") or ""
    m = re.match(r"ISO 2768-([fmcv])", cls)
    if kind == "chamfer" and m:
        # ISO 2768-1 table 2: chamfer heights (and external radii) have their own
        for over, up_to, tol in ISO2768_BROKEN_EDGES[m.group(1)]:
            if _in_band(value, over, up_to):
                return "±" + tol, "chamfer height (%s)" % cls
        return None, "chamfer of %g is under 0.5 mm: no ISO 2768 tolerance" % value
    if not bands:
        m = re.match(r"ISO 2768-([fmcv])", cls)
        if m:
            from . import tables
            bands = [[a, b, t] for a, b, t in tables.iso2768(m.group(1))["linear"]]
    if bands:
        for over, up_to, tol in bands:
            if _in_band(value, over, up_to):
                return "±" + str(tol).lstrip("±"), "by size" + (" (%s)" % cls if cls else "")
        return None, "size %g is outside the tolerance table" % value
    by_places = general.get("linear_by_places") or {}
    p = item.get("places")
    if p is not None and str(p) in by_places:
        return "±" + by_places[str(p)].lstrip("±"), "by places (%d)" % p
    if by_places:
        return None, ("printed with %s decimal place(s), but the title block only gives "
                      "tolerances for %s" % (p, ", ".join("%s place(s)" % k for k in
                                                          sorted(by_places))))
    return None, "no general tolerance in the title block"


def title_block(page):
    """Title block facts that aren't tolerances: {field: text}."""
    t = _texts(page)
    skip = set(GENERAL_FIELDS) | {"scale", "sheet_number"}
    return {k: v for k, v in t.items() if (v or "").strip() and k not in skip
            and not k.startswith("tolsize_") and not k.startswith("tolerance_notes")}


def _field_spots(page):
    """{field: (x, y)} page mm of the title block's editable texts."""
    return {t["field"]: (t["x"], t["y"]) for t in _template_texts(page) if t.get("field")}


def _item(kind, text, x, y, source, **kw):
    it = {"kind": kind, "subtype": None, "text": text, "nominal": None, "nominal_value": None,
          "places": None, "upper_tol": None, "lower_tol": None, "limits": None,
          "tolerance_type": None, "tolerance_source": None, "count": 1,
          "x": round(x, 2), "y": round(y, 2), "view": "", "source": source}
    it.update(kw)
    return it


def characteristics(page):
    """What an inspector checks on this sheet, numbered top-left to
    bottom-right. Dimensions and hole callouts carry their value and
    tolerance as printed (decimal strings, with how many places), the
    tolerance type (bilateral, unilateral, limit, basic, reference, or
    general: the title block's) and a count (4X). Each note line, chamfer,
    GD&T frame, surface symbol and title-block requirement (material,
    hardness, finish, specification...) is an item too. A dimension shown
    again in another view is flagged ``duplicate_of``."""
    from . import annot
    items = []
    unit = values_unit()
    for v in page.Views:
        if not v.isDerivedFrom("TechDraw::DrawViewDimension") or not v.References2D:
            continue
        try:
            view = v.References2D[0][0]
            cx, cy = core.view_center(view)
            r = dimension_record(v)
            kind = _dim_kind(v)
            if kind == "length" and ("⌀" in r["text"] or "Ø" in r["text"]):
                kind = "diameter"              # across a cylinder, seen from the side
            items.append(_item(
                kind, r["text"], cx + float(v.X), cy + float(v.Y), v.Name,
                nominal=r["value_text"], places=r["places"],
                nominal_value=None if r["value"] is None else round(r["value"], 6),
                upper_tol=r["upper"], lower_tol=r["lower"], limits=r["limits"],
                tolerance_type=r["type"],
                tolerance_source="general" if r["type"] == "general" else "stated",
                count=r["count"], view=view.Label, unit=unit))
        except Exception as e:
            App.Console.PrintWarning("DrawingKit characteristics: %s: %s\n" % (v.Label, e))
    for v in page.Views:
        if not annot.is_note(v):
            continue
        for t in _symbol_texts(v):
            body = t["text"].strip()
            if not body or body.upper().rstrip(":") in ("NOTES", "NOTE", "GENERAL NOTES"):
                continue
            # each line its own id, so a balloon can follow it ('Note003#2')
            line = re.match(r"line(\d+)$", t.get("field") or "")
            src = "%s#%s" % (v.Name, line.group(1)) if line else v.Name
            ch = re.match(r"^\s*(\d*\.?\d+)\s*[xX×]\s*(\d*\.?\d+)\s*°\s*$", body)
            if ch:
                items.append(_item("chamfer", body, t["x"], t["y"], src, nominal=ch.group(1),
                                   places=len(ch.group(1).split(".")[1]) if "." in ch.group(1) else 0,
                                   nominal_value=float(ch.group(1)), angle=ch.group(2),
                                   tolerance_type="general", tolerance_source="general",
                                   unit=unit))
            else:
                items.append(_item("note", body, t["x"], t["y"], src, subtype="general"))
    for v in page.Views:
        kind = getattr(v, annot.KIND, "")
        if kind not in ("frame", "surface", "flag"):
            continue
        texts = [t["text"] for t in _symbol_texts(v)]
        b = None
        try:
            from . import clearance
            b = clearance.symbol_box(v)
        except Exception:
            pass
        x, y = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) if b else (float(v.X), float(v.Y))
        if kind == "frame":
            p = annot._params(v)
            for k, seg in enumerate(annot.frame_segments(p)):
                mods = []
                if seg.get("mod"):
                    mods.append(str(seg["mod"]))
                mods += [z for z in (seg.get("zones") or [])]
                items.append(_item(
                    "geometric tolerance", " ".join(texts), x, y, v.Name if k == 0 else
                    "%s#%d" % (v.Name, k + 1), symbol=seg.get("char"),
                    tolerance_value=str(seg.get("tol") or ""), diameter_zone=bool(seg.get("dia")),
                    modifiers=mods, datum_refs=[d for d in (seg.get("datums") or []) if d],
                    datum_modifiers=[m for m, d in zip(seg.get("datum_mods") or [],
                                                       seg.get("datums") or []) if d],
                    tolerance_type="geometric", tolerance_source="stated", unit=unit))
        elif texts:
            items.append(_item({"surface": "surface finish", "flag": "special characteristic"}[kind],
                               " ".join(texts), x, y, v.Name))
    # title block requirements: material, hardness, finish, specification...
    t = _texts(page)
    spots = _field_spots(page)
    seen = set()
    for field, sub in REQUIREMENTS:
        # a field over several lines (edge_note, edge_note_2...) is one requirement
        parts = [(t.get(field) or "").strip()] + [(t.get("%s_%d" % (field, k)) or "").strip()
                                                    for k in range(2, 13)]
        val = " ".join(p for p in parts if p)
        if not val:
            continue
        if field == "surface_finish" and (t.get("surface_finish_note") or "").strip():
            val = "%s Ra %s" % (val, t["surface_finish_note"].strip())
        if (sub, val) in seen:
            continue
        seen.add((sub, val))
        x, y = spots.get(field, (0.0, 0.0))
        items.append(_item("note", val, x, y, "title block:" + field, subtype=sub))
    # numbered in reading order: by 10 mm band from the top, then left to right
    W, H = core.page_size(page)
    items.sort(key=lambda it: (it["source"].startswith("title block"),
                               round((H - it["y"]) / 10.0), it["x"]))
    for i, it in enumerate(items):
        it["no"] = i + 1
        it["zone"] = zone(page, it["x"], it["y"])
    # the same size dimensioned in two views: one characteristic, the
    # later copy pointing at the first
    first = {}
    for it in items:
        if it["kind"] in ("diameter", "radius", "length", "angle") and it["nominal"]:
            key = (it["kind"], it["nominal"], it["upper_tol"], it["lower_tol"], it["count"],
                   re.sub(r"\s+", "", it["text"]))
            if key in first and first[key]["view"] != it["view"]:
                it["duplicate_of"] = first[key]["source"]
                it["duplicate_of_no"] = first[key]["no"]
            else:
                first.setdefault(key, it)
    return items


def title_block_tolerances(page):
    """Kept for callers: the resolved general tolerances."""
    return general_tolerances(page)


def _order(page):
    """(this page's place, how many pages) in the document."""
    pages = [o for o in page.Document.Objects if o.isDerivedFrom("TechDraw::DrawPage")]
    try:
        return pages.index(page) + 1, len(pages)
    except ValueError:
        return 1, max(1, len(pages))


def sheet_number(page):
    m = re.match(r"\s*(\d+)", _texts(page).get("sheet_number", "") or "")
    return int(m.group(1)) if m else _order(page)[0]


def sheet_info(page):
    t = _texts(page)
    W, H = core.page_size(page)
    i, n = _order(page)
    return {"sheet": page.Label, "page": sheet_number(page),
            # a title block without a sheet field: its place in the document
            "sheet_number": t.get("sheet_number", "") or "%d / %d" % (i, n),
            "drawing_number": t.get("drawing_number", ""),
            "revision": t.get("revision_index", ""), "title": t.get("title", ""),
            "scale": t.get("scale", ""), "size_mm": [round(W, 2), round(H, 2)]}


def write_characteristics(page, pdf_path):
    """Save the sheet's characteristics as JSON beside the PDF
    ('Bracket.pdf' -> 'Bracket.characteristics.json'). Returns the path."""
    import hashlib
    from . import __version__
    path = os.path.splitext(pdf_path)[0] + ".characteristics.json"
    try:
        with open(pdf_path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        sha = None
    items = characteristics(page)
    general = general_tolerances(page)
    warnings = []
    unresolved = []
    for it in items:
        it["page"] = sheet_number(page)
        if it.get("tolerance_type") == "general":
            tol, rule = resolve_general(it, general)
            it["general_tol"] = tol
            it["general_tol_rule"] = rule
            if it["kind"] == "chamfer":
                it["general_tol_angle"], it["general_tol_angle_rule"] = resolve_angle(it, general)
            if tol is None:
                unresolved.append("#%d %s (%s)" % (it["no"], it["text"], rule))
    if unresolved:
        warnings.append("No general tolerance covers: " + "; ".join(unresolved))
    du, vu = drawing_unit(page), values_unit()
    if du != vu:
        warnings.append("The title block says the dimensions are in %s, but the values are "
                        "written in %s (FreeCAD's unit system)." % (
                            {"in": "inches", "mm": "millimetres"}[du],
                            {"in": "inches", "mm": "millimetres"}[vu]))
    data = {"schema": SCHEMA, "drawingkit": __version__, "pdf": os.path.basename(pdf_path),
            "pdf_sha256": sha, "coordinates": "page mm from the bottom-left corner",
            **sheet_info(page),
            "units": {"drawing": du, "values": vu},
            "general_tolerances": general,
            "title_block": title_block(page),
            "warnings": warnings,
            "characteristics": items}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return path


# ---------------------------------------------------------------------------
# The invisible text layer: an incremental update of TechDraw's PDF
# ---------------------------------------------------------------------------
_CHARS = {"⌀": "Ø", "∅": "Ø", "−": "-", "–": "-", "—": "-", "×": "x", "∠": "<",
          "″": '"', "′": "'", "√": "V"}


def _pdf_string(text):
    t = "".join(_CHARS.get(c, c) for c in text)
    b = t.encode("cp1252", errors="replace")
    return "(" + b.decode("latin-1").replace("\\", "\\\\").replace("(", "\\(") \
        .replace(")", "\\)") + ")"


# Helvetica's average width per em for the characters of drawing text, to
# stretch the invisible words over the drawn ones (selection boxes line up)
_HELV = 0.56


def _content(texts, debug=False):
    ops = ["q", "BT", "%d Tr" % (0 if debug else 3)]
    if debug:
        ops.append("1 0 0 rg")
    from . import fonts
    for t in texts:
        size = max(0.5, t["size"]) * PT
        drawn_w = len(t["text"]) * t["size"] * fonts.width_factor() * PT
        helv_w = len(t["text"]) * size * _HELV
        scale = 100.0 * drawn_w / helv_w if helv_w > 0 else 100.0
        a = math.radians(t.get("angle", 0.0))
        ca, sa = math.cos(a), math.sin(a)
        x, y = t["x"] * PT, t["y"] * PT
        shift = {"middle": drawn_w / 2.0, "end": drawn_w}.get(t.get("anchor"), 0.0)
        if t.get("source") == "dimension" or t.get("source") == "balloon":
            # dimension labels: the point is the text's middle, vertically too
            x -= ca * shift - sa * (-size * 0.35)
            y -= sa * shift + ca * (-size * 0.35)
        else:
            x -= ca * shift
            y -= sa * shift
        ops.append("/%s %.2f Tf %.1f Tz %.4f %.4f %.4f %.4f %.2f %.2f Tm %s Tj"
                   % (FONT_KEY, size, scale, ca, sa, -sa, ca, x, y, _pdf_string(t["text"])))
    ops += ["ET", "Q"]
    return ("\n".join(ops) + "\n").encode("latin-1")


def _stream(raw, num):
    """Decoded data of stream object ``num`` (b'' if none)."""
    import zlib
    m = re.search(rb"(?<![0-9])%d 0 obj\s*<<(.*?)>>\s*stream\r?\n" % num, raw, re.S)
    if not m:
        return b""
    start = m.end()
    end = raw.find(b"endstream", start)
    data = raw[start:end].rstrip(b"\r\n")
    if b"FlateDecode" in m.group(1):
        try:
            data = zlib.decompress(data)
        except Exception:
            try:
                data = zlib.decompressobj().decompress(data)
            except Exception:
                return b""
    return data


def _cmap(data):
    """{code: text} from a ToUnicode CMap."""
    out = {}

    def uni(h):
        b = bytes.fromhex(h.decode())
        try:
            return b.decode("utf-16-be")
        except Exception:
            return ""
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            out[int(a, 16)] = uni(b)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        # <lo> <hi> <first>   or   <lo> <hi> [<u1> <u2> ...]
        for a, b, c, arr in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:<([0-9A-Fa-f]+)>|\[([^\]]*)\])", block):
            lo, hi = int(a, 16), int(b, 16)
            if arr:
                for k, h in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", arr)):
                    if lo + k <= hi:
                        out[lo + k] = uni(h)
                continue
            first = uni(c)
            if not first:
                continue
            for k, code in enumerate(range(lo, hi + 1)):
                out[code] = first[:-1] + chr(ord(first[-1]) + k)
    return out


def existing_texts(raw):
    """Texts the PDF already holds as real text (TechDraw writes a few, such
    as dimension values with ⌀), one string per BT ... ET block."""
    pages = set(re.findall(rb"(?<![0-9])(\d+) 0 obj\s*<<\s*/Type\s*/Page\b(?!s)", raw))
    if len(pages) != 1:
        return []
    page = _last_obj_text(raw, int(pages.pop())) or b""
    res_ref = re.search(rb"/Resources\s+(\d+)\s+0\s+R", page)
    res = _last_obj_text(raw, int(res_ref.group(1))) if res_ref else page
    fonts_ = {}
    fd = re.search(rb"/Font\s*<<(.*?)>>", res or b"", re.S)
    fr = re.search(rb"/Font\s+(\d+)\s+0\s+R", res or b"")
    if fr and not fd:
        fd_text = _last_obj_text(raw, int(fr.group(1))) or b""
        fd = re.search(rb"<<(.*?)>>", fd_text, re.S)
    if fd:
        for name, num in re.findall(rb"/(\S+?)\s+(\d+)\s+0\s+R", fd.group(1)):
            fobj = _last_obj_text(raw, int(num)) or b""
            tu = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", fobj)
            two = b"Identity-H" in fobj or b"Type0" in fobj
            fonts_[name] = (_cmap(_stream(raw, int(tu.group(1)))) if tu else {}, two)
    contents = re.search(rb"/Contents\s+(\d+\s+0\s+R|\[[^\]]*\])", page)
    if not contents:
        return []
    data = b"".join(_stream(raw, int(n)) for n in re.findall(rb"(\d+)\s+0\s+R", contents.group(1)))
    out = []
    for block in re.findall(rb"BT(.*?)ET", data, re.S):
        cmap, two = {}, False
        txt = []
        for tok in re.finditer(rb"/(\S+)\s+[-\d.]+\s+Tf|<([0-9A-Fa-f\s]*)>|\(((?:\\.|[^\\)])*)\)", block):
            if tok.group(1) is not None:
                cmap, two = fonts_.get(tok.group(1), ({}, False))
            elif tok.group(2) is not None:
                h = re.sub(rb"\s", b"", tok.group(2))
                step = 4 if two else 2
                for i in range(0, len(h) - step + 1, step):
                    code = int(h[i:i + step], 16)
                    txt.append(cmap.get(code, ""))
            else:
                s = tok.group(3).decode("latin-1")
                txt.append("".join(cmap.get(ord(c), c) for c in s) if cmap else s)
        t = "".join(txt).strip()
        if t:
            out.append(t)
    return out


def _norm(t):
    t = "".join(_CHARS.get(c, c) for c in t)
    return re.sub(r"\s+", "", t).upper()


def _obj_text(raw, num):
    m = re.search(rb"(?<![0-9])%d 0 obj(.*?)endobj" % num, raw, re.S)
    return m.group(1) if m else None


def _last_obj_text(raw, num):
    found = list(re.finditer(rb"(?<![0-9])%d 0 obj(.*?)endobj" % num, raw, re.S))
    return found[-1].group(1) if found else None


def add_text_layer(page, pdf_path, debug=False):
    """Append the invisible text layer to a one-page PDF TechDraw wrote.
    Returns the number of texts added (0 if the PDF isn't one it can
    update). ``debug``: draw the layer in red instead (to check it lines
    up)."""
    texts = page_texts(page)
    if not texts:
        return 0
    with open(pdf_path, "rb") as f:
        raw = f.read()
    if b"/Encrypt" in raw:
        return 0
    # what TechDraw already wrote as real text isn't written twice
    try:
        real = [_norm(t) for t in existing_texts(raw)]
    except Exception:
        real = []
    if real:
        texts = [t for t in texts
                 if len(_norm(t["text"])) < 2 or not any(_norm(t["text"]) in r for r in real)]
        if not texts:
            return 0
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", raw)
    root = re.search(rb"/Root\s+(\d+)\s+0\s+R", raw)
    size = re.findall(rb"/Size\s+(\d+)", raw)
    pages = re.findall(rb"(?<![0-9])(\d+) 0 obj\s*<<\s*/Type\s*/Page\b(?!s)", raw)
    if not m or not root or not size or len(pages) != 1:
        return 0
    prev = int(m.group(1))
    n_size = int(size[-1])
    page_no = int(pages[0])
    page_obj = _last_obj_text(raw, page_no)
    if page_obj is None:
        return 0
    page_dict = page_obj.strip()
    # the page's own scale: MediaBox points against the sheet's millimetres
    mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", page_dict)
    if mb:
        W, H = core.page_size(page)
        kx = (float(mb.group(3)) - float(mb.group(1))) / (W * PT)
        ky = (float(mb.group(4)) - float(mb.group(2))) / (H * PT)
        if abs(kx - 1.0) > 0.02 or abs(ky - 1.0) > 0.02:
            return 0                     # not mm-for-mm: leave it alone
    contents = re.search(rb"/Contents\s+(\d+\s+0\s+R|\[[^\]]*\])", page_dict)
    resources = re.search(rb"/Resources\s+(\d+)\s+0\s+R", page_dict)
    if not contents or not resources:
        return 0
    res_no = int(resources.group(1))
    res = _last_obj_text(raw, res_no)
    if res is None:
        return 0
    font_no, stream_no, save_no, restore_no = n_size, n_size + 1, n_size + 2, n_size + 3
    new = {}
    # TechDraw's drawing leaves its own scaling in force at the end of its
    # content; bracket it with q ... Q so the layer starts from the page's
    # plain coordinates (points from the bottom-left)
    new[save_no] = b"<< /Length 2 >>\nstream\nq\n\nendstream"
    new[restore_no] = b"<< /Length 2 >>\nstream\nQ\n\nendstream"
    # the font: Helvetica, built into every PDF reader (no embedding)
    new[font_no] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica " \
                   b"/Encoding /WinAnsiEncoding >>"
    body = _content(texts, debug)
    new[stream_no] = b"<< /Length %d >>\nstream\n" % len(body) + body + b"endstream"
    # resources: the same, plus our font
    res = res.strip()
    entry = b"/%s %d 0 R" % (FONT_KEY.encode(), font_no)
    fm = re.search(rb"/Font\s*<<", res)
    fr = re.search(rb"/Font\s+(\d+)\s+0\s+R", res)
    if fm:
        res = res[:fm.end()] + b" " + entry + b" " + res[fm.end():]
    elif fr:
        fdict = _last_obj_text(raw, int(fr.group(1)))
        if fdict is None:
            return 0
        fdict = fdict.strip()
        fdict = fdict[:2] + b" " + entry + b" " + fdict[2:] if fdict.startswith(b"<<") else None
        if fdict is None:
            return 0
        new[int(fr.group(1))] = fdict
    elif res.startswith(b"<<"):
        res = b"<< /Font << " + entry + b" >> " + res[2:]
    else:
        return 0
    new[res_no] = res
    # the page: its drawing first, then the layer on top
    old = contents.group(1)
    inner = old[1:-1].strip() if old.startswith(b"[") else old
    arr = b"[%d 0 R %s %d 0 R %d 0 R]" % (save_no, inner, restore_no, stream_no)
    new[page_no] = page_dict[:contents.start()] + b"/Contents " + arr + page_dict[contents.end():]
    # write the update
    out = bytearray(raw)
    if not out.endswith(b"\n"):
        out += b"\n"
    offsets = {}
    for num in sorted(new):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + new[num] + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n"
    nums = sorted(offsets)
    # one subsection per run of consecutive numbers
    runs = []
    for num in nums:
        if runs and num == runs[-1][-1] + 1:
            runs[-1].append(num)
        else:
            runs.append([num])
    for run in runs:
        out += b"%d %d\n" % (run[0], len(run))
        for num in run:
            out += b"%010d 00000 n \n" % offsets[num]
    trailer = re.findall(rb"trailer\s*<<(.*?)>>\s*startxref", raw, re.S)
    info = re.search(rb"/Info\s+\d+\s+0\s+R", trailer[-1]) if trailer else None
    ids = re.search(rb"/ID\s*\[[^\]]*\]", trailer[-1]) if trailer else None
    out += b"trailer\n<< /Size %d /Root %s 0 R /Prev %d" % (restore_no + 1, root.group(1), prev)
    if info:
        out += b" " + info.group(0)
    if ids:
        out += b" " + ids.group(0)
    out += b" >>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    tmp = pdf_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(out))
    os.replace(tmp, pdf_path)
    return len(texts)
