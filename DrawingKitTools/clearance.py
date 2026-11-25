# SPDX-License-Identifier: LGPL-2.1-or-later
"""Keep new symbols clear of what is already on the sheet.

``obstacles(page)`` lists page rectangles that a new frame, datum, surface
or weld symbol or note should not land on: dimension and callout texts,
view captions (SECTION A-A, DETAIL B), balloons, other DrawingKit symbols,
tables and the title block. ``clear_spot`` finds the nearest free spot for
a box, moving it away from its anchor first.
"""

import math
import re

import FreeCAD as App

from . import core


def _dim_font(dim):
    vo = getattr(dim, "ViewObject", None)
    if vo is not None and hasattr(vo, "Fontsize"):
        try:
            return float(getattr(vo.Fontsize, "Value", vo.Fontsize))
        except Exception:
            pass
    try:
        return App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions") \
            .GetFloat("FontSize", 3.5) or 3.5
    except Exception:
        return 3.5


def dimension_text(dim):
    from . import textlayer
    try:
        t = textlayer.printed_text(dim)
    except Exception:
        t = ""
    return t or "00"


def dimension_label_box(dim):
    """Page rectangle of a dimension's text."""
    refs = dim.References2D
    if not refs:
        return None
    view = refs[0][0]
    cx, cy = core.view_center(view)
    x, y = cx + float(dim.X), cy + float(dim.Y)
    fs = _dim_font(dim)
    from . import fonts
    w = fonts.dimension_text_width(dimension_text(dim), fs, fonts.dimension_font(dim)) + 1.0
    h = fs * 1.3
    vertical = dim.Type == "DistanceY"
    vo = getattr(dim, "ViewObject", None)
    if vo is not None and "Referencing" in str(getattr(vo, "StandardAndStyle", "")):
        vertical = False
    if vertical:
        w, h = h, w
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def caption_box(view):
    """Page rectangle of the caption TechDraw writes under a section or
    detail view ('SECTION A-A', 'DETAIL B (2 : 1)')."""
    text = ""
    if view.isDerivedFrom("TechDraw::DrawViewSection"):
        text = "SECTION %s-%s" % (view.SectionSymbol, view.SectionSymbol)
    elif view.isDerivedFrom("TechDraw::DrawViewDetail"):
        text = "DETAIL %s (%s)" % (view.Reference, core.scale_text(core.view_scale(view)))
    if not text:
        return None
    try:
        x0, y0, x1, y1 = core.view_box(view)
    except Exception:
        return None
    fs = 5.0
    w = len(text) * fs * 0.6
    cx = (x0 + x1) / 2
    return (cx - w / 2, y0 - fs * 2.2 - 2, cx + w / 2, y0 - 1)


def symbol_box(sym):
    """Page rectangle of a TechDraw symbol (DrawingKit annotations)."""
    svg = getattr(sym, "Symbol", "") or ""
    mw = re.search(r'width="([\d.]+)mm"', svg)
    mh = re.search(r'height="([\d.]+)mm"', svg)
    if not mw or not mh:
        return None
    w, h = float(mw.group(1)), float(mh.group(1))
    s = float(getattr(sym, "Scale", 1.0) or 1.0)
    x, y = float(sym.X), float(sym.Y)
    return (x - w * s / 2, y - h * s / 2, x + w * s / 2, y + h * s / 2)


def balloon_box(b, r=5.0):
    v = getattr(b, "SourceView", None)
    if v is None:
        return None
    cx, cy = core.view_center(v)
    s = core.view_scale(v)
    x, y = cx + float(b.X) * s, cy + float(b.Y) * s
    return (x - r, y - r, x + r, y + r)


def obstacles(page, exclude=(), views=True):
    """Rectangles a new symbol should keep clear of. ``views``: include the
    part views' outlines (symbols belong outside the views)."""
    out = []
    names = {o.Name for o in exclude}
    for v in page.Views:
        if v.Name in names:
            continue
        box = None
        try:
            if v.isDerivedFrom("TechDraw::DrawViewDimension"):
                box = dimension_label_box(v)
            elif v.isDerivedFrom("TechDraw::DrawViewBalloon"):
                box = balloon_box(v)
            elif v.isDerivedFrom("TechDraw::DrawViewSymbol"):
                box = symbol_box(v)
            elif v.isDerivedFrom("TechDraw::DrawViewPart") and not \
                    v.isDerivedFrom("TechDraw::DrawProjGroupItem"):
                c = caption_box(v)
                if c:
                    out.append(c)
        except Exception:
            box = None
        if box:
            out.append(box)
    if views:
        for v in core.part_views(page):
            try:
                out.append(core.view_box(v))
            except Exception:
                pass
    try:
        frame, title = core.drawing_area(page.Template.Template, *core.page_size(page))
        if title:
            out.append(title)
    except Exception:
        pass
    return out


def _hits(box, obstacles, gap=1.0):
    return any(box[0] < o[2] + gap and o[0] - gap < box[2] and
               box[1] < o[3] + gap and o[1] - gap < box[3] for o in obstacles)


def clear_spot(box, anchor, obstacles, area=None, reach=40.0, step=3.0):
    """Shift (dx, dy) that moves ``box`` off every obstacle, preferring small
    moves away from ``anchor``; (0, 0) if it is clear already, None if no
    spot within ``reach`` mm is free."""
    if not _hits(box, obstacles) and _inside(box, area):
        return (0.0, 0.0)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    ax, ay = anchor
    ux, uy = cx - ax, cy - ay
    n = math.hypot(ux, uy)
    away = (ux / n, uy / n) if n > 1e-9 else (0.0, 1.0)
    cands = []
    r = step
    while r <= reach + 1e-9:
        for k in range(16):
            a = 2 * math.pi * k / 16
            dx, dy = r * math.cos(a), r * math.sin(a)
            # moving away from the anchor costs less than moving towards it
            cost = r - 0.5 * (dx * away[0] + dy * away[1])
            cands.append((cost, dx, dy))
        r += step
    cands.sort()
    for _, dx, dy in cands:
        b = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
        if not _hits(b, obstacles) and _inside(b, area):
            return (dx, dy)
    return None


def _inside(box, area):
    if area is None:
        return True
    return box[0] >= area[0] and box[1] >= area[1] and box[2] <= area[2] and box[3] <= area[3]


def drawing_area(page):
    try:
        frame, _ = core.drawing_area(page.Template.Template, *core.page_size(page))
        return frame
    except Exception:
        return None
