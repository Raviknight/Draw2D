# SPDX-License-Identifier: LGPL-2.1-or-later
"""Which title block field is which, on any template.

DrawingKit fills title blocks by role: the title, the drawing number, the
revision, the scale, the sheet number, who drew it... Its own templates name
their fields after those roles ('title', 'drawing_number', 'revision_index').
Other templates name them anything: 'Title', 'PARTNAME', 'DWG_NO', 'AuthorName',
'FC-SC', 'Номер'. This module works out, for each template, which of its
fields plays which role:

1. a field named after the role is that role;
2. a field whose name is a known spelling of the role ('AuthorName',
   'author_name', 'DRAWN_BY' are all 'creator');
3. a field whose printed caption beside it in the title block names the role
   ('DWG NO.', 'DRAWN BY', 'REV');
4. what you set yourself in "Fields DrawingKit fills", saved for that
   template (and in the company file, if you use one).

The rest of DrawingKit reads and writes the title block through read() and
write(), which rename the template's own fields to the roles and back, so it
never needs to know a template's names. Fields with no role are left as
they are.
"""

import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET

import FreeCAD as App

# role: (label, known spellings, caption words). Spellings and captions are
# compared without case, spaces, '_', '-', '.' or ':'.
ROLES = [
    ("title", "Title",
     ["title", "drawingtitle", "drawingtitle1", "dwgtitle", "parttitle", "partname",
      "fctitle", "titel", "titre", "titolo", "titulo", "título", "benennung", "designation",
      "название", "наименование"],
     ["title", "drawingtitle", "parttitle", "partname", "designation", "benennung",
      "titre", "titel", "titolo", "titulo", "título", "наименование", "название"]),
    ("title_2", "Title, 2nd line",
     ["title2", "subtitle", "drawingtitle2", "supplementarytitle1", "supplementarytitle",
      "soustitre", "sottotitolo", "subtitulo", "subtítulo", "untertitel"],
     ["subtitle", "supplementarytitle", "soustitre", "sottotitolo", "subtitulo"]),
    ("drawing_number", "Drawing number",
     ["drawingnumber", "drawingno", "dwgno", "dwgnumber", "documentnumber", "documentno",
      "docno", "docnumber", "partnumber", "partno", "partcode", "partcodename", "dn", "fcdn",
      "fcnumber", "zeichnungsnummer", "zeichnungsnr", "sachnummer", "numplano", "numeroplano",
      "numerodisegno", "номер", "обозначение", "itemno"],
     ["dwgno", "drawingno", "drawingnumber", "dwgnumber", "documentno", "documentnumber",
      "docno", "partno", "partnumber", "itemno", "zeichnungsnummer", "zeichnungsnr",
      "sachnummer", "n°plano", "nºplano", "обозначение"]),
    ("revision_index", "Revision",
     ["revisionindex", "revision", "rev", "revno", "revisionno", "fcrev", "version", "ver",
      "numerodiversione", "index", "änderung", "aenderung", "изм"],
     ["rev", "revision", "revno", "version", "numerodiversione", "index", "änderung", "изм"]),
    ("scale", "Scale",
     ["scale", "fcsc", "fcscale", "massstab", "maßstab", "échelle", "echelle", "scala",
      "escala", "масштаб"],
     ["scale", "massstab", "maßstab", "échelle", "echelle", "scala", "escala", "масштаб"]),
    ("sheet_number", "Sheet",
     ["sheetnumber", "sheet", "sheetno", "sheetof", "ofsheet", "fcsheet", "fcsh", "blatt",
      "feuille", "foglio", "hoja", "лист"],
     ["sheet", "sheetno", "blatt", "feuille", "foglio", "pagina", "hoja", "лист"]),
    ("date_of_issue", "Date",
     ["dateofissue", "date", "creationdate", "drawndate", "date1", "issuedate", "issuedate",
      "datum", "fcdate", "datedecreation", "datadicreazione", "fecha", "дата"],
     ["date", "dateofissue", "issued", "datum", "data", "fecha", "дата"]),
    ("creator", "Drawn / created by",
     ["creator", "author", "authorname", "drawnby", "drawn", "drawnname", "createdby",
      "draftsman", "drafter", "designer", "designedby", "designedbyname", "fcauthor",
      "nomauteur", "nomeprogettista", "gezeichnet", "erstellt", "разраб", "разработал"],
     ["drawn", "drawnby", "createdby", "author", "drafter", "draftsman", "designer",
      "designedby", "dessinateur", "nomeprogettista", "progettista", "dibujó", "dibujo",
      "gezeichnet", "erstellt", "разраб", "разработал"]),
    ("checked_by", "Checked by",
     ["checkedby", "checkedname", "checker", "checked", "checkername", "supervisorname",
      "nomsuperviseur", "supervisore", "geprüft", "geprueft", "пров", "проверил"],
     ["checked", "checkedby", "checker", "supervisor", "supervisore", "validépar",
      "revisó", "reviso", "geprüft", "пров", "проверил"]),
    ("approval_person", "Approved by",
     ["approvalperson", "approvedby", "approvername", "approved", "approved1", "approver",
      "approvedname", "genehmiger", "freigabe", "freigegeben", "утв", "утвердил"],
     ["approved", "approvedby", "approval", "approver", "genehmigtvon", "freigabe",
      "freigegeben", "aprobó", "aprobo", "approvato", "утв", "утвердил"]),
    ("legal_owner_1", "Owner / company",
     ["legalowner1", "legalowner", "owner1", "companyname", "company", "organization",
      "organisation", "owner", "firm", "firma", "societe", "société", "ditta", "empresa",
      "организация", "организация1"],
     ["company", "companyname", "owner", "organization", "organisation", "firm", "firma",
      "société", "societe", "ditta", "empresa", "eigentümer"]),
    ("company_address", "Company address",
     ["companyaddress", "companyaddr", "address", "adresse", "indirizzo", "direccion"],
     ["address", "companyaddress", "adresse", "indirizzo"]),
    ("part_material", "Material",
     ["partmaterial", "material", "materialmark", "materials", "werkstoff", "matériau",
      "materiale", "материал"],
     ["material", "partmaterial", "werkstoff", "matériau", "materiale", "материал"]),
    ("general_tolerances", "General tolerances / interpret per",
     ["generaltolerances", "generaltolerance", "tolerances", "tolerance",
      "allgemeintoleranzen", "tolerancias", "tolleranze"],
     ["generaltolerances", "generaltolerance", "tolerances", "allgemeintoleranzen",
      "interpretper", "interpretdimper"]),
    ("sheet_size", "Sheet size",
     ["sheetsize", "size", "papersize", "format", "formato", "формат"],
     ["size", "sheetsize", "format", "formato", "формат"]),
]
# vaguer names, taken only when nothing clearer gave the role a field
WEAK = {"drawing_number": ["number", "no", "num", "numero"],
        "date_of_issue": ["date1", "fecha1"]}
ROLE_NAMES = [r[0] for r in ROLES]
LABELS = {r[0]: r[1] for r in ROLES}
# what DrawingKit fills or reads: the window asks about a template that
# leaves any of these unknown
CORE = ["title", "drawing_number", "revision_index", "scale", "sheet_number",
        "date_of_issue", "creator"]


def norm(s):
    return re.sub(r"[\s_\-.:/#()\[\]]+", "", (s or "").lower())


_SPELLINGS = {r[0]: {norm(x) for x in r[2]} | {norm(r[0])} for r in ROLES}
_CAPTIONS = {r[0]: {norm(x) for x in r[3]} for r in ROLES}


# ---------------------------------------------------------------------------
# The template's fields and what is printed beside them
# ---------------------------------------------------------------------------
def _template(page_or_tpl):
    if page_or_tpl is None:
        return None
    if "EditableTexts" in getattr(page_or_tpl, "PropertiesList", []):
        return page_or_tpl
    tpl = getattr(page_or_tpl, "Template", None)
    if tpl is not None and "EditableTexts" in getattr(tpl, "PropertiesList", []):
        return tpl
    return None


def _svg(tpl):
    for prop in ("PageResult", "Template"):
        path = getattr(tpl, prop, "") or ""
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                continue
    return ""


def svg_fields(svg):
    """([(name, x, y)] of the editable fields, [(text, x, y)] of the fixed
    texts): SVG units, y down, transforms applied."""
    from .realtext import compose, parse_transform, _local
    try:
        root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
    except ET.ParseError:
        return [], []
    fields, fixed = [], []

    def pos(el):
        x, y = el.get("x"), el.get("y")
        if x is None or y is None:
            for ch in el:
                if _local(ch.tag) == "tspan" and ch.get("x") is not None:
                    x, y = x or ch.get("x"), y or ch.get("y")
                    break
        try:
            return float(str(x or 0).split()[0]), float(str(y or 0).split()[0])
        except ValueError:
            return None

    def walk(el, m):
        tag = _local(el.tag)
        if tag in ("defs", "symbol", "metadata", "title", "desc", "clipPath", "mask"):
            return
        try:
            m = compose(m, parse_transform(el.get("transform")))
        except ValueError:
            pass
        if tag == "text":
            p = pos(el)
            if p is None:
                return
            a, b, c, d, e, f = m
            x, y = a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f
            name = None
            for k, v in el.attrib.items():
                if k.endswith("editable"):
                    name = v
            if name:
                fields.append((name, x, y))
            else:
                txt = " ".join("".join(el.itertext()).split())
                if txt:
                    fixed.append((txt, x, y))
            return
        for ch in el:
            walk(ch, m)
    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    return fields, fixed


def svg_defaults(svg):
    """{field name: the text the template file puts in it} (placeholders
    such as 'PART TITLE' that count as empty)."""
    from .realtext import _local
    try:
        root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        if _local(el.tag) != "text":
            continue
        for k, v in el.attrib.items():
            if k.endswith("editable"):
                out[v] = " ".join("".join(el.itertext()).split())
    return out


_DEFAULTS = {}


def template_default(page_or_tpl, role):
    """What the template file itself has in a role's field ('' if none)."""
    tpl = _template(page_or_tpl)
    name = field_for(tpl, role) if tpl is not None else None
    if not name:
        return ""
    path = getattr(tpl, "Template", "") or ""
    if path not in _DEFAULTS:
        _DEFAULTS[path] = svg_defaults(_svg(tpl))
    return _DEFAULTS[path].get(name, "")


def caption_of(field_xy, fixed, reach=40.0):
    """The fixed text most likely to be a field's caption: just above it or
    to its left, close by."""
    fx, fy = field_xy
    best = None
    for txt, x, y in fixed:
        dy = fy - y                      # > 0: the text is above the field
        dx = fx - x                      # > 0: the text starts left of it
        if -1.0 <= dy <= 12.0 and -15.0 <= dx <= reach:
            d = math.hypot(dx * 0.5, dy * 1.5)     # rather the one above it
        elif -1.5 <= dy <= 1.5 and 0.0 < dx <= reach * 2:
            d = dx * 0.6                 # on the same line, to its left
        else:
            continue
        if best is None or d < best[0]:
            best = (d, txt)
    return best[1] if best else None


def _caption_role(text):
    t = norm(text)
    if not t:
        return None
    for role in ROLE_NAMES:
        if t in _CAPTIONS[role]:
            return role
    # 'DRAWN BY / DATE', 'DWG. NO. (PART)': a caption starting with the words
    for role in ROLE_NAMES:
        for c in sorted(_CAPTIONS[role], key=len, reverse=True):
            if len(c) >= 3 and t.startswith(c):
                return role
    return None


def _name_says_other(name, role):
    """The field's own name has a word of another role in it."""
    n = norm(name)
    for other in ROLE_NAMES:
        if other == role:
            continue
        for s in _SPELLINGS[other]:
            if len(s) >= 4 and s in n:
                return True
    return False


def guess(names, svg=""):
    """{role: field name} for a template with these editable names."""
    names = list(names)
    out, used = {}, set()
    # 1. named after the role
    for role in ROLE_NAMES:
        if role in names:
            out[role] = role
            used.add(role)
    # 2. a known spelling
    for role in ROLE_NAMES:
        if role in out:
            continue
        for n in names:
            if n not in used and n not in ROLE_NAMES and norm(n) in _SPELLINGS[role]:
                out[role] = n
                used.add(n)
                break
    # 3. the caption printed beside it; a caption beside several fields is a
    # row or column heading (a 'CHECKED' row with name and date): not used
    if svg:
        fields, fixed = svg_fields(svg)
        caps = [(n, caption_of((x, y), fixed)) for n, x, y in fields
                if n not in used and n not in ROLE_NAMES and n in names]
        count = {}
        for _n, c in caps:
            if c:
                count[c] = count.get(c, 0) + 1
        for n, c in caps:
            if not c or count[c] > 1 or n in used:
                continue
            role = _caption_role(c)
            if role and _name_says_other(n, role):
                continue                 # 'FECHA2' beside 'Dibujó' is a date, not a name
            if role and role not in out:
                out[role] = n
                used.add(n)
    # 4. vaguer names
    for role, spellings in WEAK.items():
        if role in out:
            continue
        for n in names:
            if n not in used and norm(n) in spellings:
                out[role] = n
                used.add(n)
                break
    return out


# ---------------------------------------------------------------------------
# Saved mappings: per template (its set of field names), yours and the company's
# ---------------------------------------------------------------------------
def signature(names):
    return hashlib.sha1("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:16]


USER_FILE = None        # tests point this elsewhere


def user_file():
    return USER_FILE or os.path.join(App.getUserAppDataDir(), "DrawingKit",
                                     "title_block_fields.json")


def _read(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    maps = data.get("field_maps", {}) if isinstance(data, dict) else {}
    return {k: v for k, v in maps.items() if isinstance(v, dict) and isinstance(v.get("map"), dict)}


def saved_maps():
    from . import titleblock
    maps = _read(titleblock.company_file())
    maps.update(_read(user_file()))          # yours on top
    return maps


def _write(path, sig, name, mapping):
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("field_maps", {})[sig] = {"template": name, "map": dict(mapping)}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def save(page, mapping, company=False):
    """Keep ``mapping`` ({role: field name, or '' for none}) for the page's
    template; with ``company`` also in the company file."""
    tpl = _template(page)
    names = list(dict(tpl.EditableTexts))
    sig = signature(names)
    name = os.path.basename(getattr(tpl, "Template", "") or "") or "template"
    _write(user_file(), sig, name, mapping)
    if company:
        from . import titleblock
        if titleblock.company_file():
            _write(titleblock.company_file(), sig, name, mapping)
    _CACHE.pop(sig, None)


_CACHE = {}


def mapping(page_or_tpl):
    """{role: field name} for the page's template."""
    tpl = _template(page_or_tpl)
    if tpl is None:
        return {}
    names = list(dict(tpl.EditableTexts))
    sig = signature(names)
    if sig in _CACHE:
        return dict(_CACHE[sig])
    saved = saved_maps().get(sig)
    if saved is not None:
        m = {r: n for r, n in saved["map"].items() if r in LABELS and n and n in names}
    else:
        m = guess(names, _svg(tpl))
    _CACHE[sig] = m
    return dict(m)


def is_saved(page):
    tpl = _template(page)
    return tpl is not None and signature(list(dict(tpl.EditableTexts))) in saved_maps()


def needs_review(page):
    """A template DrawingKit hasn't been told about, that leaves some of the
    fields it fills unknown while having fields it can't place."""
    tpl = _template(page)
    if tpl is None or is_saved(page):
        return False
    names = list(dict(tpl.EditableTexts))
    if all(r in names for r in CORE if r != "title_2"):
        return False                     # DrawingKit's own kind of template
    m = mapping(tpl)
    unknown = [n for n in names if n not in m.values()]
    return bool(unknown) and any(r not in m for r in CORE)


# ---------------------------------------------------------------------------
# Reading and writing by role
# ---------------------------------------------------------------------------
def role_of(page_or_tpl):
    """{field name: role} for the fields with a role."""
    return {n: r for r, n in mapping(page_or_tpl).items()}


def read(page_or_tpl):
    """The title block's texts with each field that has a role under the
    role's name ({} without a title block)."""
    tpl = _template(page_or_tpl)
    if tpl is None:
        return {}
    raw = dict(tpl.EditableTexts)
    rename = role_of(tpl)
    return {rename.get(k, k): v for k, v in raw.items()}


def write(page_or_tpl, texts):
    """Write texts keyed as read() gives them (roles, or the template's own
    names) back into the template. Returns {field name: value} changed."""
    tpl = _template(page_or_tpl)
    if tpl is None:
        return {}
    raw = dict(tpl.EditableTexts)
    m = mapping(tpl)
    changed = {}
    for k, v in texts.items():
        name = m.get(k, k)
        if name in raw and raw[name] != v:
            raw[name] = v
            changed[name] = v
    if changed:
        tpl.EditableTexts = raw
    return changed


def field_for(page_or_tpl, role):
    """The template's field for a role, or None."""
    tpl = _template(page_or_tpl)
    if tpl is None:
        return None
    name = mapping(tpl).get(role)
    return name if name in dict(tpl.EditableTexts) else None
