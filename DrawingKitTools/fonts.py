# SPDX-License-Identifier: LGPL-2.1-or-later
"""The drawing font.

DrawingKit writes its title blocks, notes and dimensions in a plain sans
font, DejaVu Sans by default (the family Onshape prints in), instead of the
hand-lettered osifont. FreeCAD carries DejaVu Sans in its own copy of
matplotlib, so it's loaded from there when the system doesn't have it.
"""

import glob
import os

import FreeCAD as App

PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"
DEFAULT = "DejaVu Sans"
FALLBACK = "osifont"
_registered = []


def _font_files():
    """DejaVu Sans .ttf files shipped with FreeCAD's matplotlib."""
    roots = []
    try:
        import matplotlib
        roots.append(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"))
    except Exception:
        pass
    home = App.getHomePath()
    roots += glob.glob(os.path.join(home, "bin", "Lib", "site-packages", "matplotlib", "mpl-data",
                                    "fonts", "ttf"))
    roots += glob.glob(os.path.join(home, "lib", "python3*", "site-packages", "matplotlib",
                                    "mpl-data", "fonts", "ttf"))
    out = []
    for r in roots:
        for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf",
                     "DejaVuSans-BoldOblique.ttf"):
            p = os.path.join(r, name)
            if os.path.isfile(p) and p not in out:
                out.append(p)
        if out:
            break
    return out


def families():
    """Font families Qt knows (empty without a GUI)."""
    try:
        from PySide import QtGui
        try:
            return list(QtGui.QFontDatabase.families())
        except TypeError:
            return list(QtGui.QFontDatabase().families())
    except Exception:
        return []


def ensure_registered():
    """Make DejaVu Sans available to Qt (and so to TechDraw). Returns True if
    it is."""
    if _registered:
        return True
    fams = families()
    if DEFAULT in fams:
        _registered.append(DEFAULT)
        return True
    try:
        from PySide import QtGui
    except Exception:
        return False
    ok = False
    for path in _font_files():
        try:
            if QtGui.QFontDatabase.addApplicationFont(path) >= 0:
                ok = True
        except Exception:
            pass
    if ok:
        _registered.append(DEFAULT)
    return ok


def drawing_font():
    """The font for title blocks, notes and dimensions: the one you chose
    (Text style), else DejaVu Sans."""
    try:
        f = App.ParamGet(PREFS).GetString("Font", "")
        if f:
            return f
    except Exception:
        pass
    return DEFAULT


def css_family(font=None):
    """font-family value for SVG text. One name only: Qt's SVG renderer
    doesn't take a list (it falls back to its default font by itself)."""
    font = (font or drawing_font()).replace("'", "").replace('"', "").replace(";", "")
    return "'%s'" % font


def width_factor(font=None):
    """Average character width / text height, for sizing boxes round text:
    osifont is narrow, DejaVu Sans and Arial wider."""
    f = (font or drawing_font()).lower()
    if "osifont" in f or "narrow" in f or "condensed" in f:
        return 0.52
    return 0.64


NARROW = set(".,:;'|!il ")


def dimension_text_width(text, size, font=None):
    """Width (mm) of a dimension's text as TechDraw draws it. TechDraw sizes
    dimension text by its capitals (ISO 3098: 'size' is the cap height), so
    the font is about 1.37 times bigger than 'size'; TechDraw also spaces a
    ± out (' ± ')."""
    text = (text or "").replace(" ±", "±").replace("± ", "±").replace("±", " ± ")
    em = size / 0.73
    wide = width_factor(font)
    return sum((wide / 2.0 if c in NARROW else wide) for c in text) * em


def dimension_font(dim):
    vo = getattr(dim, "ViewObject", None)
    try:
        return vo.Font if vo is not None and hasattr(vo, "Font") else drawing_font()
    except Exception:
        return drawing_font()


TD_LABELS = "User parameter:BaseApp/Preferences/Mod/TechDraw/Labels"


def caption_style():
    """(size mm, font) TechDraw draws view captions with ('SECTION A-A',
    'DETAIL B SCALE 2 : 1', view labels): its Labels preference, one for
    every sheet."""
    p = App.ParamGet(TD_LABELS)
    size = p.GetFloat("LabelSize", 8.0) or 8.0
    return size, p.GetString("LabelFont", "osifont") or "osifont"


def set_caption_style(size=None, font=None):
    """Set TechDraw's caption size and font (Preferences > TechDraw >
    Annotation: Label size / Label font) and redraw the captions on the
    open documents' sheets. Returns how many views were redrawn."""
    p = App.ParamGet(TD_LABELS)
    if size:
        p.SetFloat("LabelSize", float(size))
    if font:
        p.SetString("LabelFont", font)
    n = 0
    for doc in App.listDocuments().values():
        for v in doc.Objects:
            try:
                if not v.isDerivedFrom("TechDraw::DrawView") or "Caption" not in v.PropertiesList:
                    continue
                if not v.Caption and not getattr(getattr(v, "ViewObject", None), "KeepLabel", False):
                    continue
                c = v.Caption                 # a change to it draws the caption again
                v.Caption = c + " "
                v.Caption = c
                n += 1
            except Exception:
                continue
    return n


def style_dimension(obj):
    """New DrawingKit dimensions and balloons: the drawing font (and the
    sheet's thin line, if Line weights was used)."""
    vo = getattr(obj, "ViewObject", None)
    if vo is not None and hasattr(vo, "Font"):
        try:
            vo.Font = drawing_font()
        except Exception:
            pass
    try:
        from . import lineweights
        lineweights.style(obj)
    except Exception:
        pass
