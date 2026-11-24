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
    return "ansi" if standards.is_asme() else "iso"


def set_style_pref(value):
    if value not in ("auto", "ISO", "ANSI"):
        raise ValueError("Title block style must be auto, ISO or ANSI")
    App.ParamGet(PREFS).SetString(STYLE_PREF, value)


def is_ansi(page_or_texts):
    texts = page_or_texts
    if not isinstance(texts, dict):
        tpl = page_or_texts.Template
        texts = dict(tpl.EditableTexts) if tpl is not None and \
            "EditableTexts" in tpl.PropertiesList else {}
    return "checked_by" in texts


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
            "angular": rows(t.get("angular"))}


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
    vals = load_defaults() if values is None else values
    texts = dict(tpl.EditableTexts)
    changed = {}
    for k in SHARED:
        if k not in texts or k not in vals:
            continue
        cur = (texts[k] or "").strip()
        if only_blank and cur not in _template_defaults(k):
            continue
        if texts[k] != vals[k]:
            texts[k] = vals[k]
            changed[k] = vals[k]
    if changed:
        tpl.EditableTexts = texts
    return changed


def fields(page):
    """[(key, label, value)] of the page's title block, in a sensible order."""
    tpl = page.Template
    if tpl is None or "EditableTexts" not in tpl.PropertiesList:
        return []
    texts = dict(tpl.EditableTexts)
    keys = [k for k in ORDER if k in texts] + sorted(k for k in texts if k not in ORDER)
    return [(k, LABELS.get(k, k.replace("_", " ").capitalize()), texts[k]) for k in keys]


def set_fields(page, values):
    tpl = page.Template
    texts = dict(tpl.EditableTexts)
    changed = {k: v for k, v in values.items() if k in texts and texts[k] != v}
    if changed:
        texts.update(changed)
        tpl.EditableTexts = texts
    return changed


def has_tolerance_row(page):
    tpl = page.Template
    return tpl is not None and "EditableTexts" in tpl.PropertiesList and \
        "tol_1" in dict(tpl.EditableTexts)


def page_style(page):
    return "ansi" if is_ansi(page) else "iso"


def set_template_variant(page, tol_row=None, style_=None):
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
    if style_ == "ansi" and not templates.fits_ansi(size, orient):
        return False
    if tol_row is None:
        tol_row = has_tolerance_row(page)
    keep = dict(tpl.EditableTexts)
    tpl.Template = templates.template_path(size, orient, first, tol_row=tol_row, style=style_)
    texts = dict(tpl.EditableTexts)
    for k, v in keep.items():
        if k in texts and k != "sheet_number":
            texts[k] = v
    tpl.EditableTexts = texts
    apply_defaults(page)
    try:
        core.set_title_block_scale(page, core.view_scale(core.page_content(page)[0]))
    except Exception:
        pass
    core.renumber_sheets(page.Document, force=True)
    return True


def set_tolerance_row(page, on):
    """ISO block with or without the tolerance row (see set_template_variant)."""
    return set_template_variant(page, tol_row=on, style_="iso")
