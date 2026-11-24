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


def dimension_font(dim):
    vo = getattr(dim, "ViewObject", None)
    try:
        return vo.Font if vo is not None and hasattr(vo, "Font") else drawing_font()
    except Exception:
        return drawing_font()


def style_dimension(obj):
    """New DrawingKit dimensions and balloons: the drawing font."""
    vo = getattr(obj, "ViewObject", None)
    if vo is not None and hasattr(vo, "Font"):
        try:
            vo.Font = drawing_font()
        except Exception:
            pass
