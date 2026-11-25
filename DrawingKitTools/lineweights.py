# SPDX-License-Identifier: LGPL-2.1-or-later
"""Line weights for a whole sheet, in two groups as ISO 128 and ASME Y14.2
set them out: thick for visible outlines; thin for hidden lines, centre
lines, dimensions, leaders and DrawingKit's symbols, frames and tables.

The choice is kept as a preference ('ThickLine', 'ThinLine' in mm), so new
views and dimensions follow it.
"""

import FreeCAD as App

PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"

# (name, thick, thin) in mm
PRESETS = [
    ("ISO 128, A4-A2 (0.5 / 0.25)", 0.5, 0.25),
    ("ISO 128, small (0.35 / 0.18)", 0.35, 0.18),
    ("ISO 128, A1-A0 (0.7 / 0.35)", 0.7, 0.35),
    ("ASME Y14.2 (0.6 / 0.3)", 0.6, 0.3),
]
SYMBOL_STROKE = 0.35            # what DrawingKit's symbols are drawn with by default
CENTRE_DEFAULT = 0.25


def weights():
    """(thick, thin) mm as set with the Line weights tool, or None."""
    try:
        p = App.ParamGet(PREFS)
        thick, thin = p.GetFloat("ThickLine", 0.0), p.GetFloat("ThinLine", 0.0)
    except Exception:
        return None
    return (thick, thin) if thick > 0 and thin > 0 else None


def set_weights(thick, thin):
    p = App.ParamGet(PREFS)
    if thick and thin:
        p.SetFloat("ThickLine", float(thick))
        p.SetFloat("ThinLine", float(thin))
    else:
        p.RemFloat("ThickLine")
        p.RemFloat("ThinLine")


def symbol_factor():
    """How much DrawingKit's own symbol strokes are scaled (thin line over
    the default 0.35 mm)."""
    w = weights()
    return w[1] / SYMBOL_STROKE if w else 1.0


def centre_line():
    w = weights()
    return w[1] if w else CENTRE_DEFAULT


def _set(vo, prop, value):
    if vo is not None and hasattr(vo, prop):
        try:
            old = getattr(vo, prop)
            if abs(float(getattr(old, "Value", old)) - value) > 1e-9:
                setattr(vo, prop, value)      # unchanged: no redraw
            return True
        except Exception:
            pass
    return False


def style(obj, w=None):
    """Give one view, dimension or balloon the saved (or given) weights."""
    w = w or weights()
    if not w:
        return False
    thick, thin = w
    vo = getattr(obj, "ViewObject", None)
    if obj.isDerivedFrom("TechDraw::DrawViewPart"):
        done = _set(vo, "LineWidth", thick)
        _set(vo, "HiddenWidth", thin)
        _set(vo, "IsoWidth", thin)
        return done
    if obj.isDerivedFrom("TechDraw::DrawViewDimension") or \
            obj.isDerivedFrom("TechDraw::DrawViewBalloon"):
        return _set(vo, "LineWidth", thin)
    return False


def apply_page(page, thick, thin):
    """Set the weights (and keep them for new views): every view, dimension
    and balloon on the sheet, DrawingKit's symbols and centre marks.
    Returns how many objects changed."""
    from . import annot, core
    set_weights(thick, thin)
    n = 0
    every = page.getAllViews() if hasattr(page, "getAllViews") else page.Views
    for v in every:
        if style(v, (thick, thin)):
            n += 1
        elif annot.is_annotation(v):
            try:
                annot.render(v)
                n += 1
            except Exception:
                pass
    core.refresh_center_marks(core.part_views(page))
    return n
