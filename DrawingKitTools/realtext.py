# SPDX-License-Identifier: LGPL-2.1-or-later
"""Keep the words of a PDF real text.

TechDraw draws the title block, notes, tables and symbols through Qt's SVG
renderer, and Qt's SVG renderer writes every letter into a PDF as an
outline: the sheet looks right, but no program can read the words. Text
Qt draws itself (QPainter.drawText) stays real text, with its font
embedded.

So just before TechDraw writes a PDF, DrawingKit swaps each SVG drawing on
the page for two things: the same SVG with its text taken out (lines,
boxes, logo), and the text drawn by Qt at the same place, size, angle and
alignment. After the export everything is put back. Text DrawingKit can't
place exactly (text on a path, letter spacing...) stays in the SVG, drawn
as before.
"""

import math
import re
import xml.etree.ElementTree as ET

import FreeCAD as App

SVG_ITEM = 13                     # QGraphicsSvgItem.Type
BASE_PX = 100                     # fonts are built at this size, then scaled

_TEXT_RE = re.compile(r"<text\b[^>]*?(?:/>|>.*?</text\s*>)", re.S)
_INHERITED = ("font-family", "font-size", "font-weight", "font-style", "fill",
              "fill-opacity", "text-anchor", "visibility", "letter-spacing",
              "word-spacing", "opacity")
_UNSUPPORTED_ATTRS = ("dx", "dy", "rotate", "textLength", "lengthAdjust")


def _local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _style(el, inherited):
    st = dict(inherited)
    for k in _INHERITED + ("display",):
        if el.get(k) is not None:
            st[k] = el.get(k)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def parse_transform(s):
    """An SVG transform list as (a, b, c, d, e, f): x' = a x + c y + e,
    y' = b x + d y + f."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", s or ""):
        a = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if name == "translate":
            t = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0.0)
        elif name == "scale":
            t = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif name == "rotate":
            r = math.radians(a[0])
            c, s_ = math.cos(r), math.sin(r)
            cx, cy = (a[1], a[2]) if len(a) == 3 else (0.0, 0.0)
            t = (c, s_, -s_, c, cx - c * cx + s_ * cy, cy - s_ * cx - c * cy)
        elif name == "matrix" and len(a) == 6:
            t = tuple(a)
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        else:
            raise ValueError("transform %s" % name)
        m = compose(m, t)
    return m


def compose(m, t):
    """m applied after t (SVG order: 'm t' acts on a point as m(t(p)))."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = t
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def _length(v):
    """A font size in user units ('3.5', '3.5px'); None for other units."""
    m = re.fullmatch(r"\s*([-\d.]+)\s*(px)?\s*", v or "")
    return float(m.group(1)) if m else None


def _family(v):
    fam = (v or "").split(",")[0].strip().strip("'\"")
    return fam


def _clean(t, keep_spaces=False):
    t = t.replace("\n", " ").replace("\t", " ")
    return t if keep_spaces else re.sub(r" +", " ", t)


class Run:
    """One piece of text, drawn with one font from one point."""

    def __init__(self, text, x, y, st, matrix, chunk):
        self.text, self.x, self.y, self.st, self.matrix, self.chunk = text, x, y, st, matrix, chunk

    def __repr__(self):
        return "Run(%r, %.2f, %.2f)" % (self.text, self.x or 0, self.y or 0)


def text_runs(text_el, st, matrix, values=None):
    """[Run] for one <text> element, or None if it uses something DrawingKit
    doesn't draw itself (it then stays in the SVG)."""
    st = _style(text_el, st)
    if st.get("display") == "none" or st.get("visibility") in ("hidden", "collapse"):
        return []
    if any(text_el.get(a) is not None for a in _UNSUPPORTED_ATTRS):
        return None
    m = compose(matrix, parse_transform(text_el.get("transform")))
    name = None
    for k, v in text_el.attrib.items():
        if k.endswith("editable"):
            name = v
    try:
        x0 = float(text_el.get("x", 0) or 0)
        y0 = float(text_el.get("y", 0) or 0)
    except ValueError:
        return None                       # lists of positions
    runs = []
    chunk = 0
    if name is not None and values is not None and name in values:
        # an editable field: TechDraw writes its value into the text
        sub = text_el
        for ch in text_el:
            if _local(ch.tag) == "tspan":
                sub = ch
                break
        s2 = _style(sub, st) if sub is not text_el else st
        x = float(sub.get("x", x0) or x0) if sub is not text_el else x0
        y = float(sub.get("y", y0) or y0) if sub is not text_el else y0
        val = str(values[name])
        if val.strip():
            runs.append(Run(_clean(val).strip(), x, y, s2, m, 0))
            runs[-1].element = id(text_el)
        return runs
    if text_el.text and text_el.text.strip():
        runs.append(Run(_clean(text_el.text), x0, y0, st, m, chunk))
    for ch in text_el:
        if _local(ch.tag) != "tspan":
            return None
        if len(ch):
            return None                   # nested tspans
        if any(ch.get(a) is not None for a in _UNSUPPORTED_ATTRS) or ch.get("transform"):
            return None
        s2 = _style(ch, st)
        if s2.get("display") == "none":
            continue
        try:
            x = float(ch.get("x")) if ch.get("x") is not None else None
            y = float(ch.get("y")) if ch.get("y") is not None else None
        except ValueError:
            return None
        if not runs:
            # the element's first words start at its own position
            x = x0 if x is None else x
            y = y0 if y is None else y
        if x is not None:
            chunk += 1
        if ch.text and ch.text.strip():
            runs.append(Run(_clean(ch.text), x, y, s2, m, chunk))
        if ch.tail and ch.tail.strip():
            runs.append(Run(_clean(ch.tail), None, None, st, m, chunk))
    for r in runs:
        r.element = id(text_el)
    # leading and trailing blanks of a line don't show
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    for r in runs:
        if _length(r.st.get("font-size", "16")) is None:
            return None
        if r.st.get("letter-spacing") not in (None, "normal", "0", "0px") or \
                r.st.get("word-spacing") not in (None, "normal", "0", "0px"):
            return None
    return [r for r in runs if r.text]


def split_svg(svg, values=None):
    """(the SVG without the text DrawingKit draws itself, [Run]). Text it
    can't draw stays in the SVG."""
    try:
        root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
    except ET.ParseError:
        return svg, []
    keep = []                               # per <text>, in document order: runs or None
    runs = []

    def walk(el, st, matrix, hidden):
        tag = _local(el.tag)
        if tag in ("defs", "clipPath", "mask", "pattern", "symbol", "marker", "title", "desc",
                   "metadata"):
            # text inside these isn't drawn where it stands
            for t in el.iter():
                if _local(t.tag) == "text" and t is not el:
                    keep.append(None)
            if tag == "text":
                keep.append(None)
            return
        st = _style(el, st)
        if tag == "text":
            r = None if hidden or el.get("clip-path") or el.get("mask") or el.get("filter") \
                else text_runs(el, st, matrix, values)
            keep.append(r)
            if r:
                runs.extend(r)
            return
        try:
            matrix = compose(matrix, parse_transform(el.get("transform")))
        except ValueError:
            hidden = True                   # something odd: leave its text alone
        if st.get("display") == "none" or el.get("clip-path") or el.get("mask") \
                or el.get("filter") or tag in ("switch", "a", "use"):
            hidden = True
        for ch in el:
            walk(ch, st, matrix, hidden)
    walk(root, {}, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), False)
    # take out the text elements DrawingKit draws, byte for byte otherwise
    found = list(_TEXT_RE.finditer(svg))
    if len(found) != len(keep):
        return svg, []                      # can't match them up safely
    out, i = [], 0
    for m, r in zip(found, keep):
        out.append(svg[i:m.start()])
        if r is None:
            out.append(m.group(0))
        i = m.end()
    out.append(svg[i:])
    return "".join(out), runs


# ---------------------------------------------------------------------------
# Qt side
# ---------------------------------------------------------------------------
def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    try:
        from PySide import QtSvg
    except ImportError:
        QtSvg = None
    return QtCore, QtGui, QtWidgets, QtSvg


def _color(st, QtGui):
    fill = (st.get("fill") or "#000000").strip()
    if fill in ("none", "transparent"):
        return None
    if fill.startswith("url("):
        fill = "#000000"
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", fill)
    c = QtGui.QColor(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else QtGui.QColor(fill)
    if not c.isValid():
        c = QtGui.QColor("#000000")
    try:
        op = float(st.get("fill-opacity", 1)) * float(st.get("opacity", 1))
        c.setAlphaF(max(0.0, min(1.0, op)))
    except ValueError:
        pass
    return c


def _font(st, QtGui):
    f = QtGui.QFont(_family(st.get("font-family")) or "DejaVu Sans")
    f.setPixelSize(BASE_PX)
    f.setHintingPreference(QtGui.QFont.PreferNoHinting)
    f.setKerning(True)
    w = (st.get("font-weight") or "").strip()
    if w in ("bold", "bolder") or (w.isdigit() and int(w) >= 600):
        f.setBold(True)
    if (st.get("font-style") or "") in ("italic", "oblique"):
        f.setItalic(True)
    return f


def layout(runs, QtGui):
    """[(run, font, x, y, scale)] with each chunk shifted for its anchor."""
    placed = []
    chunks = {}
    cx = cy = 0.0
    for r in runs:
        f = _font(r.st, QtGui)
        size = _length(r.st.get("font-size", "16")) or 16.0
        k = size / BASE_PX
        x = r.x if r.x is not None else cx
        y = r.y if r.y is not None else cy
        w = QtGui.QFontMetricsF(f).horizontalAdvance(r.text) * k
        placed.append([r, f, x, y, k, w])
        chunks.setdefault((getattr(r, "element", 0), r.chunk), []).append(len(placed) - 1)
        cx, cy = x + w, y
    for idx in chunks.values():
        first = placed[idx[0]][0]
        anchor = (first.st.get("text-anchor") or "start").strip()
        total = sum(placed[i][5] for i in idx)
        shift = {"middle": total / 2.0, "end": total}.get(anchor, 0.0)
        for i in idx:
            placed[i][2] -= shift
    return [(p[0], p[1], p[2], p[3], p[4]) for p in placed]


_items = []                            # keep Python items alive while shown
report = []                            # why an SVG drawing was left as it is


_Drawing = None


def _drawing_class():
    """A scene item that draws an SVG without its text, then the text with
    QPainter.drawText (real text in a PDF). Built on QGraphicsItem itself:
    PySide's QGraphicsSvgItem (QtSvgWidgets) mustn't be loaded, because
    once it is, PySide crashes reading TechDraw's own SVG items."""
    global _Drawing
    if _Drawing is not None:
        return _Drawing
    QtCore, QtGui, QtWidgets, _s = _qt()

    class Drawing(QtWidgets.QGraphicsItem):
        def __init__(self, renderer, rect, runs):
            super().__init__()
            self._renderer = renderer
            self._rect = rect
            self._view = _view_matrix(renderer, rect)
            self._placed = layout(runs, QtGui)
            self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)

        def boundingRect(self):
            return self._rect

        def paint(self, painter, option, widget=None):
            painter.save()
            try:
                self._renderer.render(painter, self._rect)
                base = painter.worldTransform()
                for r, f, x, y, k in self._placed:
                    c = _color(r.st, QtGui)
                    if c is None:
                        continue
                    a, b, cc, d, e, ff = compose(self._view, r.matrix)
                    t = QtGui.QTransform().translate(x, y).scale(k, k) * \
                        QtGui.QTransform(a, b, cc, d, e, ff)
                    painter.setWorldTransform(t * base)
                    painter.setPen(c)
                    painter.setFont(f)
                    painter.drawText(QtCore.QPointF(0.0, 0.0), r.text)
            finally:
                painter.restore()

    _Drawing = Drawing
    return Drawing


def svg_widgets_loaded():
    import sys
    return any(m in sys.modules for m in ("PySide6.QtSvgWidgets", "PySide.QtSvgWidgets",
                                          "PySide2.QtSvg"))


def _view_matrix(renderer, rect):
    vb = renderer.viewBoxF()
    if vb.width() <= 0 or vb.height() <= 0:
        return (1.0, 0.0, 0.0, 1.0, rect.x(), rect.y())
    sx = rect.width() / vb.width()
    sy = rect.height() / vb.height()
    return (sx, 0.0, 0.0, sy, rect.x() - vb.x() * sx, rect.y() - vb.y() * sy)


REZ = 10.0                            # TechDraw scene units per mm


def core_page_size(page):
    from . import core
    return core.page_size(page)


def _source(page, holder):
    """(SVG text, editable values or None, is the template) that TechDraw
    draws in the SVG item ``holder`` holds."""
    p = holder
    while p is not None:
        if p.data(0) == "QGIV":
            obj = page.Document.getObject(str(p.data(1)))
            if obj is not None and obj.isDerivedFrom("TechDraw::DrawViewSymbol"):
                return getattr(obj, "Symbol", "") or "", None, False
            return None, None, False
        p = _parent(p)
    tpl = page.Template
    if tpl is None or not tpl.isDerivedFrom("TechDraw::DrawSVGTemplate"):
        return None, None, False
    try:
        with open(tpl.Template, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return None, None, False
    return svg, dict(getattr(tpl, "EditableTexts", {}) or {}), True


def prepare(page):
    """Swap the page's SVG text for real text. Returns a function that puts
    everything back, and how many pieces of text were swapped."""
    import sys
    if svg_widgets_loaded() or "PySide6" not in sys.modules:
        # PySide would crash reading TechDraw's SVG items (Qt 5 has them
        # in QtSvg itself): leave the page as it is
        report.append("skipped: Qt SVG items can't be read safely here")
        return (lambda: None), 0
    QtCore, QtGui, QtWidgets, QtSvg = _qt()
    import TechDrawGui
    scene = TechDrawGui.getSceneForPage(page)
    if scene is None or QtSvg is None:
        return (lambda: None), 0
    Drawing = _drawing_class()
    done, added, count = [], [], 0
    del report[:]
    all_items, holders = _holders(page, scene)
    W, H = core_page_size(page)
    for grp in holders:
        if grp is None or not grp.isVisible():
            continue
        try:
            svg, values, is_template = _source(page, grp)
            if not svg or "<text" not in svg:
                report.append("no SVG text found for an item")
                continue
            stripped, runs = split_svg(svg, values)
            if not runs:
                report.append("no text DrawingKit can draw: %s" % svg[:80])
                continue
            renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(stripped.encode("utf-8")))
            if not renderer.isValid():
                continue
            ds = renderer.defaultSize()
            if is_template:
                # the template fills the page: page mm x 10 (TechDraw's
                # scene units), its bottom-left corner on the origin
                k = REZ
                target = QtCore.QRectF(0.0, -H * k, W * k, H * k)
                around = grp.childrenBoundingRect()
                if abs(around.width() - target.width()) > 0.02 * target.width():
                    report.append("template not where expected: %s" % around)
                    continue
            else:
                target = grp.childrenBoundingRect()
                if target.width() <= 0 or abs(target.width() / target.height()
                                              - ds.width() / float(ds.height())) > 0.02:
                    report.append("symbol not where expected: %s" % target)
                    continue
            top = grp
            while True:
                up = _parent(top)
                if up is None:
                    break
                top = up
            z = top.zValue() + 0.001
            box = Drawing(renderer, QtCore.QRectF(0, 0, ds.width(), ds.height()), runs)
            added += [box, renderer]          # kept alive until put back
            fit = QtGui.QTransform().translate(target.x(), target.y()).scale(
                target.width() / ds.width(), target.height() / ds.height())
            box.setTransform(fit * grp.sceneTransform())
            box.setZValue(z)
            scene.addItem(box)
            # see-through rather than hidden: TechDraw's printing trips
            # over a hidden template
            grp.setOpacity(0.0)
            done.append(_key(grp))
            count += len(runs)
        except Exception as e:
            import traceback
            report.append("error: %s" % traceback.format_exc()[-600:])
            App.Console.PrintWarning("DrawingKit real text: %s\n" % e)
    holders = grp = top = all_items = None
    _items.extend(added)

    # TechDraw may rebuild its items while it writes the PDF: the groups
    # hidden here are found again afterwards by what they draw, never
    # through the handles taken now
    hidden = set(done)
    done.clear()
    gc_collect()

    def restore():
        sc = TechDrawGui.getSceneForPage(page)
        for x in added:
            try:
                if hasattr(x, "scene") and x.scene() is not None:
                    x.scene().removeItem(x)
            except Exception:
                pass
        if sc is not None:
            every, again = _holders(page, sc)
            for grp in again:
                if grp.opacity() == 0.0 and _key(grp) in hidden:
                    grp.setOpacity(1.0)
            every = again = grp = None
        # the removed items stay alive in _items: deleting them while
        # TechDraw may still hold on to them isn't safe
        added.clear()
        gc_collect()
    return restore, count


DISPLAY_AREA = 65543                  # TechDraw's QGDisplayArea (UserType + 7)
TEMPLATE = 65566                      # TechDraw's QGISVGTemplate (UserType + 30)
ONLY = None                           # testing: "template" or "symbols"
_leaked = []                          # handles PySide wrongly thinks it owns


def _holders(page, scene):
    """(every item, [the groups that hold TechDraw's SVG drawings]): the
    template's group, and each symbol's display area.

    PySide is fragile here, so: every item is taken from scene.items()
    first (those handles don't own anything), and parents are only looked
    up while that list is held, so PySide hands back the same handles. A
    handle PySide claims to own would delete TechDraw's item when Python
    lets go of it: such a group is kept forever and left alone."""
    try:
        import shiboken6 as shiboken
    except ImportError:
        return [], []
    every = scene.items()
    out = []
    has_svg = any(it.type() == SVG_ITEM for it in every)
    W, H = core_page_size(page)
    for it in every:
        t = it.type()
        if ONLY and ((t == DISPLAY_AREA) == (ONLY == "template")):
            continue
        if t == TEMPLATE and has_svg and it.data(0) != "QGIV":
            # the template's group: never reached through its SVG item,
            # whose PySide handle is unreliable
            if it.parentItem() is not None:
                continue
            r = it.childrenBoundingRect()
            if abs(r.width() - W * REZ) < 0.02 * W * REZ and \
                    abs(r.height() - H * REZ) < 0.02 * H * REZ:
                out.append(it)
        elif t == DISPLAY_AREA:
            v = _parent(it)
            if v is None or v.data(0) != "QGIV":
                continue
            obj = page.Document.getObject(str(v.data(1)))
            if obj is not None and obj.isDerivedFrom("TechDraw::DrawViewSymbol"):
                out.append(it)
    return every, out


def _parent(item):
    """item.parentItem(), safely. PySide hands the parent over as if Python
    owned it, and would delete TechDraw's item when the handle goes;
    adding it to the scene it's already in changes nothing in Qt but gives
    the ownership back to C++."""
    p = item.parentItem()
    if p is None:
        return None
    try:
        import shiboken6 as shiboken
        if shiboken.ownedByPython(p):
            sc = p.scene()
            if sc is not None:
                sc.addItem(p)
            if shiboken.ownedByPython(p):
                _leaked.append(p)             # never let Python delete it
                return None
    except ImportError:
        _leaked.append(p)
        return None
    return p


def _key(holder):
    """What an SVG holder draws: a view's name, or '' for the template."""
    p = holder
    while p is not None:
        if p.data(0) == "QGIV":
            return str(p.data(1))
        p = _parent(p)
    return ""


def gc_collect():
    import gc
    gc.collect()
