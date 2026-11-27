# SPDX-License-Identifier: LGPL-2.1-or-later
"""Title block defaults: company name, tolerances, finish and the like.

Fields that are the same on every drawing (owner / company, general
tolerances, units, the X.X / X.XX / X.XXX and angle tolerances, finish,
who approves) come from, in order, each one overriding the last:

1. DrawingKit's built-in values (inch tolerances for ASME, ISO 2768-m for ISO)
2. a company file: a JSON file anyone can point DrawingKit at, e.g. on a
   shared drive, so the whole company starts from the same values
3. your own saved defaults (in FreeCAD's user data folder)

New sheets get them, and Fill title block puts them in fields that are
still empty. Fields that belong to one drawing (title, drawing number,
material, date, revision, sheet, scale) are never saved as defaults.
"""

import json
import os

import FreeCAD as App

from . import standards

PREFS = standards.PREFS
COMPANY_PREF = "CompanyTitleBlockFile"
TOL_ROW_PREF = "ToleranceRow"        # auto / on / off
STYLE_PREF = "TitleBlockStyle"       # auto / ISO / ANSI

# Fields worth saving as defaults: the same on every drawing
SHARED = ["legal_owner_1", "company_address", "creator", "checked_by", "approval_person",
          "general_tolerances", "units", "tol_1", "tol_2", "tol_3", "tol_4", "tol_angle",
          "finish", "surface_note", "edge_note"]
TOLERANCE_FIELDS = ["units", "tol_1", "tol_2", "tol_3", "tol_4", "tol_angle", "finish"]

LABELS = {
    "legal_owner_1": "Owner / company", "company_address": "Company address",
    "scale": "Scale", "title": "Title", "title_2": "Title, 2nd line",
    "creator": "Drawn / created by", "checked_by": "Checked by", "part_material": "Material",
    "general_tolerances": "General tolerances / interpret per",
    "approval_person": "Approved by",
    "drawing_number": "Drawing number", "date_of_issue": "Date",
    "revision_index": "Revision", "sheet_number": "Sheet", "sheet_size": "Sheet size",
    "units": "Dimensions in", "tol_1": "Tolerance X.X", "tol_2": "Tolerance X.XX",
    "tol_3": "Tolerance X.XXX", "tol_4": "Tolerance X.XXXX", "tol_angle": "Angles",
    "finish": "Finish", "surface_note": "Surface texture note", "edge_note": "Edges note",
}
ORDER = ["title", "title_2", "drawing_number", "part_material", "revision_index",
         "date_of_issue", "legal_owner_1", "company_address", "creator", "checked_by",
         "approval_person", "general_tolerances", "units", "tol_1", "tol_2", "tol_3",
         "tol_4", "tol_angle", "finish", "surface_note", "edge_note",
         "scale", "sheet_number", "sheet_size"]


def builtin_defaults(asme=None):
    asme = standards.is_asme() if asme is None else asme
    if asme:
        return {"units": "INCHES", "tol_1": "±0.02", "tol_2": "±0.01", "tol_3": "±0.005",
                "tol_4": "±0.0005", "tol_angle": "±0.5°", "finish": "125 µin Ra",
                "general_tolerances": "ASME Y14.5-2018",
                "surface_note": "SURFACE TEXTURE PER ASME Y14.36",
                "edge_note": "BREAK SHARP EDGES 0.005-0.015"}
    return {"units": "MM", "general_tolerances": "ISO 2768-m"}


def style():
    """'ansi' (ASME Y14.1 style block with a tolerance block) or 'iso', from
    the 'TitleBlockStyle' preference; auto follows the drawing standard."""
    try:
        pref = App.ParamGet(PREFS).GetString(STYLE_PREF, "auto") or "auto"
    except Exception:
        pref = "auto"
    if pref.upper() in ("ISO", "ANSI"):
        return pref.lower()
    if pref.startswith("custom:"):
        from . import templates
        if templates.custom_design(pref) is not None:
            return pref
    return "ansi" if standards.is_asme() else "iso"


def set_style_pref(value):
    if value not in ("auto", "ISO", "ANSI") and not str(value).startswith("custom:"):
        raise ValueError("Title block style must be auto, ISO, ANSI or custom:<design>")
    App.ParamGet(PREFS).SetString(STYLE_PREF, value)


def template_style(page):
    """'iso', 'ansi' or 'custom:<design>' from the page's template file
    ('' for a template that isn't DrawingKit's)."""
    from . import templates
    tpl = page.Template
    path = getattr(tpl, "Template", "") or "" if tpl is not None else ""
    if templates.parse_name(path) is None:
        return ""
    return templates.parse_style(path)


def is_custom(page):
    return template_style(page).startswith("custom:")


def is_ansi(page_or_texts):
    texts = page_or_texts
    if not isinstance(texts, dict):
        if is_custom(page_or_texts):
            return False
        tpl = page_or_texts.Template
        texts = dict(tpl.EditableTexts) if tpl is not None and \
            "EditableTexts" in tpl.PropertiesList else {}
    return "checked_by" in texts


def sheet_of(page, texts=None):
    """Sheet numbers as '1 OF 3' (ANSI, or a design that writes it so)
    rather than '1 / 3'?"""
    if texts is None:
        texts = _texts(page)
    if not template_style(page):
        # someone else's template: as its sheet field is written now, else
        # as the drawing standard writes it
        cur = (texts.get("sheet_number") or "").upper()
        if " OF " in cur:
            return True
        if "/" in cur:
            return False
        return standards.is_asme()
    if is_custom(page):
        cur = (texts.get("sheet_number") or "").upper()
        if cur.strip():
            return " OF " in cur
        from . import templates
        d = templates.custom_design(template_style(page))
        for c in (d or {}).get("cells", []):
            if c.get("field") == "sheet_number":
                return "OF" in (c.get("text") or "").upper()
        return False
    return is_ansi(texts)


def sheet_text(i, n, ansi=False):
    return ("%d OF %d" if ansi else "%d / %d") % (i, n)


def user_file():
    return os.path.join(App.getUserAppDataDir(), "DrawingKit", "title_block_defaults.json")


def company_file():
    try:
        return App.ParamGet(PREFS).GetString(COMPANY_PREF, "") or ""
    except Exception:
        return ""


def set_company_file(path):
    App.ParamGet(PREFS).SetString(COMPANY_PREF, path or "")


def read_file(path):
    """{field: value} from a defaults file ({} if missing or unreadable)."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        App.Console.PrintWarning("DrawingKit: can't read %s: %s\n" % (path, e))
        return {}
    fields = data.get("fields", data) if isinstance(data, dict) else {}
    out = {k: str(v) for k, v in fields.items() if k in SHARED}
    if isinstance(data, dict) and TOL_TABLE in data:
        out[TOL_TABLE] = _clean_table(data[TOL_TABLE])
    return out


TOL_TABLE = "tolerance_table"     # general tolerances by size range (or None)


def _clean_table(t):
    """{'preset', 'linear': [[over, up_to, tol]], 'angular': [...]} or None."""
    if not isinstance(t, dict):
        return None

    def rows(rs):
        out = []
        for r in rs or []:
            if isinstance(r, (list, tuple)) and len(r) == 3:
                out.append([str(x).strip() for x in r])
        return out
    return {"preset": str(t.get("preset", "")), "linear": rows(t.get("linear")),
            "angular": rows(t.get("angular")), "angle_all": str(t.get("angle_all", "") or "")}


def write_file(path, values):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = {"drawingkit": "title block defaults",
            "fields": {k: values.get(k, "") for k in SHARED if k in values}}
    if TOL_TABLE in values:
        data[TOL_TABLE] = _clean_table(values[TOL_TABLE])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def load_defaults(asme=None):
    """Built-in, then company file, then your own saved values."""
    vals = builtin_defaults(asme)
    for path in (company_file(), user_file()):
        vals.update({k: v for k, v in read_file(path).items()})
    return vals


def save_user_defaults(values):
    return write_file(user_file(), values)


def save_company_defaults(values, path):
    write_file(path, values)
    set_company_file(path)
    return path


def wants_tolerance_row():
    """Should new sheets have the tolerance row (units, X.X / X.XX / X.XXX,
    angles, finish)? 'ToleranceRow' preference: on, off or auto (ASME, or
    saved defaults that fill any of those fields)."""
    try:
        pref = App.ParamGet(PREFS).GetString(TOL_ROW_PREF, "auto") or "auto"
    except Exception:
        pref = "auto"
    if pref in ("on", "off"):
        return pref == "on"
    if standards.is_asme():
        return True
    saved = {}
    for path in (company_file(), user_file()):
        saved.update(read_file(path))
    return any(saved.get(k) for k in TOLERANCE_FIELDS if k != "units")


def _template_defaults(key):
    """Values a field has in any DrawingKit template before anyone types."""
    from . import templates
    out = {""}
    for cell in templates.CELLS + templates.TOL_CELLS + templates.ANSI_CELLS:
        if cell[4] == key:
            out.add(cell[5])
    for line in templates.ANSI_TOL_LINES:
        for _, _, f, default in line:
            if f == key:
                out.add(default)
    return out


def units_label(vals):
    u = (vals.get("units") or "").strip().lower()
    return "in" if u.startswith("inch") or u == "in" else (u or "mm")


def apply_tolerance_table(page, values=None):
    """New sheets: put in the saved general tolerance table, if any."""
    from . import tables
    vals = load_defaults() if values is None else values
    data = vals.get(TOL_TABLE)
    if block_tolerance_rows(page):
        # the title block has its own rows: no table on the sheet
        if data and block_tolerance_data(page) is None:
            fill_block_tolerances(page, data)
        return None
    if data and tables.tolerance_table(page) is None:
        return tables.set_tolerance_table(page, data, units_label(vals))
    return None


def apply_defaults(page, values=None, only_blank=True):
    """Put the saved defaults in the page's title block: in fields still
    empty (or at the template's own value) unless ``only_blank`` is False.
    Returns the fields set."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return {}
    from . import fieldmap
    vals = load_defaults() if values is None else values
    texts = fieldmap.read(tpl)          # by role, whatever the template calls them
    changed = {}
    for k in SHARED:
        if k not in texts or k not in vals:
            continue
        cur = (texts[k] or "").strip()
        if only_blank and cur not in _template_defaults(k) | {fieldmap.template_default(tpl, k)}:
            continue
        if texts[k] != vals[k]:
            changed[k] = vals[k]
    if changed:
        fieldmap.write(tpl, changed)
    return changed


def fields(page):
    """[(key, label, value)] of the page's title block, in a sensible order."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return []
    texts = dict(tpl.EditableTexts)
    labels = dict(LABELS)
    order = list(ORDER)
    d = None
    if is_custom(page):
        from . import templates
        d = templates.custom_design(template_style(page))
    if d is not None:
        # a design of your own: its captions name the fields, in its order
        from . import blocks
        order = []
        for c in d["cells"]:
            cap = (c["label"].splitlines() or [""])[0].strip().capitalize()
            if c["kind"] == "field" and c["field"]:
                names = blocks.line_names(c)
                base = cap or LABELS.get(c["field"], c["field"].replace("_", " ").capitalize())
                for k, n in enumerate(names):
                    if len(names) > 1:
                        labels[n] = "%s (line %d)" % (base, k + 1)
                    elif n not in LABELS or cap:
                        labels[n] = base
                    order.append(n)
            elif c["kind"] == "surface":
                v, note = blocks.surface_names(c)
                labels[v], labels[note] = "Surface finish value", "Surface finish note"
                order += [v, note]
    # someone else's template: its fields go by the role DrawingKit found
    # for them (AuthorName shows as 'Drawn / created by', in that place)
    from . import fieldmap
    role = fieldmap.role_of(page) if d is None else {}
    for k, r in role.items():
        if k != r and k in texts:
            labels[k] = labels.get(r, k)
            if r in order:
                order[order.index(r)] = k
    keys = [k for k in order if k in texts] + sorted(k for k in texts if k not in order)
    out = [(k, labels.get(k, k.replace("_", " ").capitalize()), texts[k]) for k in keys]
    seen = [lab for _k, lab, _v in out]
    # two cells with the same caption (DATE, DATE): say which is which
    return [(k, lab if seen.count(lab) == 1 or k in LABELS and LABELS[k] == lab
             else "%s (%s)" % (lab, k.replace("_", " ")), v) for k, lab, v in out]


def set_fields(page, values):
    tpl = page.Template
    texts = dict(tpl.EditableTexts)
    changed = {k: v for k, v in values.items() if k in texts and texts[k] != v}
    if changed:
        texts.update(changed)
        tpl.EditableTexts = texts
    return changed


def _texts(page):
    """The title block's texts, fields with a role under the role's name."""
    from . import fieldmap
    return fieldmap.read(page)


def has_tolerance_row(page):
    """ISO title block with the row on top (units, tolerances, finish)."""
    t = _texts(page)
    return "units" in t and not is_ansi(t)


def tolerance_mode(page):
    """'table' when the title block points to a size table instead of
    having X.X / X.XX / X.XXX values, else 'places'."""
    t = _texts(page)
    return "table" if "units" in t and "tol_1" not in t else "places"


def page_style(page):
    st = template_style(page)
    if st.startswith("custom:"):
        return st
    return "ansi" if is_ansi(page) else "iso"


# ---------------------------------------------------------------------------
# tolerances by size inside a title block of your own
# ---------------------------------------------------------------------------
def block_tolerance_rows(page):
    """Number of tolerance-by-size rows in the page's title block (0: none)."""
    from . import blocks
    t = _texts(page)
    n = 0
    while (blocks.TOL_RANGE % (n + 1)) in t:
        n += 1
    return n


def fill_block_tolerances(page, data):
    """Write a tolerance table ({'linear', 'angular', 'angle_all'}) into the
    title block's own tolerance rows. True if the block has them."""
    from . import blocks
    n = block_tolerance_rows(page)
    if not n:
        return False
    set_fields(page, blocks.tolerance_values(data, n))
    return True


def block_tolerance_data(page):
    """The page's in-block tolerance rows as a tolerance table, or None."""
    from . import blocks
    t = _texts(page)
    n = block_tolerance_rows(page)
    lin, ang = [], ""
    for i in range(1, n + 1):
        r, v = (t.get(blocks.TOL_RANGE % i) or "").strip(), (t.get(blocks.TOL_VALUE % i) or "").strip()
        if not v:
            continue
        if r in ("∠", "ANGLES", "ANGULAR"):
            ang = v
            continue
        if r.startswith("<"):
            lin.append(["", r[1:], v])
        elif r.startswith(">"):
            lin.append([r[1:], "", v])
        elif "-" in r:
            a, b = r.split("-", 1)
            lin.append([a.strip(), b.strip(), v])
        else:
            lin.append(["", r, v])
    if not lin and not ang:
        return None
    return {"preset": "", "linear": lin, "angular": [], "angle_all": ang}


def match_tolerance_mode(page):
    """One way of giving general tolerances per sheet: with a size table
    on the sheet the title block drops its X.X / X.XX / X.XXX values (and
    gets them back when the table goes). Returns True if it switched."""
    from . import tables
    if is_custom(page):
        return False                 # your own block is as you drew it
    want = "table" if tables.tolerance_table(page) is not None else "places"
    t = _texts(page)
    if "units" not in t or tolerance_mode(page) == want:
        return False
    return set_template_variant(page, tol_mode=want)


def set_template_variant(page, tol_row=None, style_=None, tol_mode=None):
    """Swap a DrawingKit template for another variant (ISO block with or
    without the tolerance row, or the ANSI block), keeping what was typed
    in and filling new fields from the defaults. False if the page's
    template isn't a DrawingKit one (or the sheet is too narrow for ANSI)."""
    from . import core, templates
    tpl = page.Template
    path = getattr(tpl, "Template", "") or ""
    info = templates.parse_name(path)
    if info is None:
        return False
    size, orient, first = info
    style_ = style_ or page_style(page)
    if style_ != "iso" and not templates.fits(style_, size, orient):
        return False
    if tol_row is None:
        tol_row = has_tolerance_row(page)
    from . import tables
    if tol_mode is None:
        tol_mode = "table" if tables.tolerance_table(page) is not None else "places"
    keep = dict(tpl.EditableTexts)
    # the tolerances by size, wherever they are now (block rows or a table)
    tol_data = block_tolerance_data(page) or tables.tolerance_data(page)
    tpl.Template = templates.template_path(size, orient, first, tol_row=tol_row, style=style_,
                                           tol_mode=tol_mode)
    texts = dict(tpl.EditableTexts)
    for k, v in keep.items():
        if k in texts and k != "sheet_number":
            texts[k] = v
    tpl.EditableTexts = texts
    if block_tolerance_rows(page):
        if tol_data:
            fill_block_tolerances(page, tol_data)
            if tables.tolerance_table(page) is not None:   # now in the block: table goes
                t = tables.tolerance_table(page)
                page.removeView(t)
                page.Document.removeObject(t.Name)
    elif tol_data and tables.tolerance_table(page) is None and not str(style_).startswith("custom:"):
        tables.set_tolerance_table(page, tol_data, units_label(texts))
    try:
        page.Document.recompute()
        core.move_tables_clear(page)      # off a taller title block
    except Exception:
        pass
    apply_defaults(page)
    try:
        core.set_title_block_scale(page, core.view_scale(core.page_content(page)[0]))
    except Exception:
        pass
    core.renumber_sheets(page.Document, force=True)
    return True


def refresh_design(key):
    """After a design is saved again: redraw the title block of every sheet
    using it in the open drawings (FreeCAD keeps its own copy of the
    template in each drawing). Returns the sheets updated."""
    style_ = "custom:" + key
    done = []
    for doc in App.listDocuments().values():
        for page in [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]:
            try:
                if template_style(page) == style_ and set_template_variant(page, style_=style_):
                    done.append(page)
            except Exception as e:
                App.Console.PrintWarning("DrawingKit: %s: %s\n" % (page.Label, e))
        if any(p.Document is doc for p in done):
            doc.recompute()
    return done


def set_tolerance_row(page, on):
    """ISO block with or without the tolerance row (see set_template_variant)."""
    return set_template_variant(page, tol_row=on, style_="iso")
