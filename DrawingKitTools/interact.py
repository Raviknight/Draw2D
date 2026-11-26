# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mouse interaction on a TechDraw page (GUI only).

- PagePicker: click points on the page with a live preview, for commands
  like "projected view by pointing" and "section by cut line".
- Drag-anywhere: drag a view by any of its lines or faces, not just by its
  label as TechDraw requires. A plain click still selects as before.

Page coordinates are mm with Y up. TechDraw's scene uses the same numbers
times its "Rez" factor, with Y down.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from . import core


def rez():
    p = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Rez")
    return p.GetFloat("Resolution", 10.0)


def to_page(scene_pt):
    k = rez()
    return scene_pt.x() / k, -scene_pt.y() / k


def to_scene(x, y):
    k = rez()
    return QtCore.QPointF(x * k, -y * k)


def page_scene(page):
    import TechDrawGui
    try:
        return TechDrawGui.getSceneForPage(page)
    except Exception:
        return None


def page_graphics_view(page):
    scene = page_scene(page)
    if scene is None:
        return None
    views = scene.views()
    return views[0] if views else None


def _pen(color, dashed=True, width=0.35):
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(width * rez())
    if dashed:
        pen.setStyle(QtCore.Qt.DashLine)
    pen.setCosmetic(False)
    return pen


def status(text):
    try:
        Gui.getMainWindow().statusBar().showMessage(text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Picking points with a preview
# ---------------------------------------------------------------------------
class PagePicker(QtCore.QObject):
    """Collect clicks on a page.

    on_move(pt, points) -> list of shapes to preview, each either
        ("rect", x, y, w, h) centred rect, or ("line", [(x, y), ...]).
    on_click(pt, points) -> True to finish (calls on_done), False to keep going.
    on_done(points) is called when finished (Enter / right-click / on_click
    returning True); on_cancel() on Esc.
    """

    active = None

    def __init__(self, page, hint, on_move=None, on_click=None, on_done=None,
                 on_cancel=None, finish_on_right_click=True):
        super().__init__()
        self.page = page
        self.hint = hint
        self.on_move = on_move
        self.on_click = on_click
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.finish_on_right_click = finish_on_right_click
        self.points = []
        self.items = []
        self.scene = page_scene(page)
        self.gview = page_graphics_view(page)
        if self.scene is None or self.gview is None:
            raise RuntimeError("Open the page first")
        if PagePicker.active is not None:
            PagePicker.active.cancel()
        PagePicker.active = self
        self.gview.viewport().installEventFilter(self)
        self.gview.installEventFilter(self)
        self.gview.viewport().setMouseTracking(True)
        self.gview.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.gview.setFocus()
        status(hint)

    # -- preview ----------------------------------------------------------
    def _clear_preview(self):
        for it in self.items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.items = []

    def _draw(self, shapes):
        self._clear_preview()
        for shape in shapes or []:
            if shape[0] == "rect":
                _, x, y, w, h = shape
                tl = to_scene(x - w / 2.0, y + h / 2.0)
                item = QtWidgets.QGraphicsRectItem(tl.x(), tl.y(), w * rez(), h * rez())
                item.setPen(_pen("#1f77b4"))
                item.setBrush(QtGui.QBrush(QtGui.QColor(31, 119, 180, 30)))
            elif shape[0] == "circle":
                _, x, y, r = shape
                tl = to_scene(x - r, y + r)
                item = QtWidgets.QGraphicsEllipseItem(tl.x(), tl.y(), 2 * r * rez(), 2 * r * rez())
                item.setPen(_pen("#d62728", dashed=True, width=0.5))
            elif shape[0] in ("hl_line", "hl_circle", "hl_point"):
                # highlight of a pickable thing: solid; orange = hover, blue = picked
                color = shape[-1]
                if shape[0] == "hl_line":
                    a, b = shape[1]
                    path = QtGui.QPainterPath(to_scene(*a))
                    path.lineTo(to_scene(*b))
                    item = QtWidgets.QGraphicsPathItem(path)
                else:
                    x, y = shape[1]
                    r = shape[2]
                    tl = to_scene(x - r, y + r)
                    item = QtWidgets.QGraphicsEllipseItem(tl.x(), tl.y(), 2 * r * rez(), 2 * r * rez())
                    if shape[0] == "hl_point":
                        item.setBrush(QtGui.QBrush(QtGui.QColor(color)))
                item.setPen(_pen(color, dashed=False, width=0.7))
            elif shape[0] in ("ghost", "ghost_fill"):
                # the dimension being placed, drawn as it will look
                pts = shape[1]
                if len(pts) < 2:
                    continue
                path = QtGui.QPainterPath(to_scene(*pts[0]))
                for p in pts[1:]:
                    path.lineTo(to_scene(*p))
                item = QtWidgets.QGraphicsPathItem(path)
                item.setPen(_pen("#1f77b4", dashed=False, width=0.35))
                if shape[0] == "ghost_fill":
                    item.setBrush(QtGui.QBrush(QtGui.QColor("#1f77b4")))
            elif shape[0] in ("text", "ctext"):
                _, x, y, txt = shape
                item = QtWidgets.QGraphicsSimpleTextItem(txt)
                f = item.font()
                f.setPointSizeF(3.5 * rez())
                item.setFont(f)
                item.setBrush(QtGui.QBrush(QtGui.QColor("#1f77b4")))
                pos = to_scene(x, y)
                if shape[0] == "ctext":
                    # centred on the point, just above it (horizontal, as placed)
                    br = item.boundingRect()
                    pos = QtCore.QPointF(pos.x() - br.width() / 2.0, pos.y() - br.height())
                item.setPos(pos)
            else:
                pts = shape[1]
                if len(pts) < 2:
                    continue
                path = QtGui.QPainterPath(to_scene(*pts[0]))
                for p in pts[1:]:
                    path.lineTo(to_scene(*p))
                item = QtWidgets.QGraphicsPathItem(path)
                item.setPen(_pen("#d62728", dashed=True, width=0.5))
            item.setZValue(1e6)
            self.scene.addItem(item)
            self.items.append(item)

    # -- events -------------------------------------------------------------
    def _is_viewport(self, obj):
        """True if the event came from the page's viewport. Compare the Qt
        objects, not the Python wrappers (PySide can hand out a new wrapper)."""
        try:
            return obj == self.gview.viewport()
        except Exception:
            return False

    def _pt(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()
        return to_page(self.gview.mapToScene(pos.toPoint() if hasattr(pos, "toPoint") else pos))

    def eventFilter(self, obj, event):
        t = event.type()
        try:
            if t == QtCore.QEvent.MouseMove and self._is_viewport(obj):
                if self.on_move:
                    self._draw(self.on_move(self._pt(event), self.points))
                return False
            if t == QtCore.QEvent.MouseButtonPress and self._is_viewport(obj):
                if event.button() == QtCore.Qt.LeftButton:
                    pt = self._pt(event)
                    self.points.append(pt)
                    done = self.on_click(pt, self.points) if self.on_click else False
                    if done:
                        self.finish()
                    elif self.on_move:
                        self._draw(self.on_move(pt, self.points))
                    return True
                if event.button() == QtCore.Qt.RightButton:
                    # act after the release, so the context-menu event that
                    # follows is still swallowed by this filter
                    self._right = "finish" if (self.finish_on_right_click and self.points) \
                        else "cancel"
                    return True
            if t == QtCore.QEvent.MouseButtonRelease and self._is_viewport(obj) \
                    and event.button() == QtCore.Qt.RightButton:
                action = getattr(self, "_right", None)
                self._right = None
                if action:
                    QtCore.QTimer.singleShot(80, getattr(self, action))
                return True
            if t in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick) \
                    and self._is_viewport(obj):
                return True
            if t == QtCore.QEvent.ContextMenu:
                return True  # right-click ends picking; no TechDraw menu
            if t == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Escape:
                    self.cancel()
                    return True
                if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    self.finish()
                    return True
        except Exception as e:
            App.Console.PrintError("DrawingKit: %s\n" % e)
            self.cancel()
            return True
        return False

    def _stop(self):
        self._clear_preview()
        try:
            self.gview.viewport().removeEventFilter(self)
            self.gview.removeEventFilter(self)
            self.gview.viewport().unsetCursor()
        except Exception:
            pass
        if PagePicker.active is self:
            PagePicker.active = None
        status("")

    def finish(self):
        self._stop()
        if self.on_done:
            try:
                self.on_done(list(self.points))
            except Exception as e:
                App.Console.PrintError("DrawingKit: %s\n" % e)
                QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "DrawingKit", str(e))

    def cancel(self):
        self._stop()
        if self.on_cancel:
            self.on_cancel()


# ---------------------------------------------------------------------------
# Drag a view from anywhere on it
# ---------------------------------------------------------------------------
DRAG_THRESHOLD_PX = 6


def _view_under(page, x, y, margin=3.0):
    """Smallest part view whose box (plus a margin for line width) contains
    page point (x, y)."""
    best = None
    for v in core.part_views(page):
        try:
            x0, y0, x1, y1 = core.view_box(v)
        except Exception:
            continue
        if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, v)
    return best[1] if best else None


class DragAnywhere(QtCore.QObject):
    """Event filter on a page's viewport that moves views dragged by their
    lines or faces. Hold Alt to get TechDraw's normal behaviour."""

    def __init__(self, page, gview):
        super().__init__(gview)
        self.page = page
        self.gview = gview
        self.press = None      # (screen pos, page pt, view, start positions)
        self.dragging = False

    def _scene_item_is_geometry(self, pos):
        item = self.gview.itemAt(pos)
        return isinstance(item, QtWidgets.QGraphicsPathItem)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QtCore.QEvent.Paint:
            # fit symbol frames before the page is painted, so TechDraw's wide
            # hover frame with the name under it never shows, not even once
            boxes = getattr(self, "boxes", None)
            if boxes is not None:
                try:
                    boxes.flush()
                except Exception:
                    pass
            return False
        if PagePicker.active is not None:
            return False
        try:
            if t == QtCore.QEvent.MouseButtonPress:
                self.press, self.dragging = None, False
                if event.button() != QtCore.Qt.LeftButton or \
                        event.modifiers() & (QtCore.Qt.AltModifier | QtCore.Qt.ControlModifier
                                             | QtCore.Qt.ShiftModifier):
                    return False
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                if not self._scene_item_is_geometry(pos):
                    return False  # labels, frames, dimensions: TechDraw handles them
                x, y = to_page(self.gview.mapToScene(pos))
                view = _view_under(self.page, x, y)
                if view is None:
                    return False
                target = self._move_target(view)
                self.press = (pos, (x, y), view, target, (float(target.X), float(target.Y)))
                return False
            if t == QtCore.QEvent.MouseMove and self.press is not None:
                if not (event.buttons() & QtCore.Qt.LeftButton):
                    self.press = None
                    return False
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                start_pos, start_pt, view, target, start_xy = self.press
                if not self.dragging:
                    if (pos - start_pos).manhattanLength() < DRAG_THRESHOLD_PX:
                        return False
                    self.dragging = True
                    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.ClosedHandCursor)
                x, y = to_page(self.gview.mapToScene(pos))
                nx, ny = start_xy[0] + x - start_pt[0], start_xy[1] + y - start_pt[1]
                target.X, target.Y = nx, ny
                core.enforce_alignment(target)
                return True
            if t == QtCore.QEvent.MouseButtonRelease and self.press is not None:
                was_dragging = self.dragging
                self.press, self.dragging = None, False
                if was_dragging:
                    QtWidgets.QApplication.restoreOverrideCursor()
                    # let TechDraw finish its click, then drop the face/edge it selected
                    QtCore.QTimer.singleShot(0, Gui.Selection.clearSelection)
                return False
        except Exception as e:
            App.Console.PrintWarning("DrawingKit drag: %s\n" % e)
            self.press, self.dragging = None, False
        return False

    @staticmethod
    def _move_target(view):
        """Front view of an aligned group: move the whole group."""
        if view.isDerivedFrom("TechDraw::DrawProjGroupItem"):
            group = core.parent_group(view)
            if group is not None and (core.same(group.Anchor, view) or not core.is_aligned(group)):
                return group
        return view


_installed = {}


def install_drag(page):
    """Make views on this page draggable from anywhere (once per page tab)."""
    gview = page_graphics_view(page)
    if gview is None:
        return False
    key = id(gview.viewport())
    if key in _installed:
        return True
    f = DragAnywhere(page, gview)
    gview.viewport().installEventFilter(f)
    _installed[key] = f
    try:
        f.boxes = ClickBoxes(page, gview.scene())
    except Exception as e:
        App.Console.PrintWarning("DrawingKit click boxes: %s\n" % e)
    gview.viewport().destroyed.connect(lambda *_: _installed.pop(key, None))
    return True


# ---------------------------------------------------------------------------
# Tight click boxes for DrawingKit's symbols
# ---------------------------------------------------------------------------
# TechDraw gives every view a hover/selection frame 2 mm outside its drawing,
# with the object's name under it, and that whole area catches the mouse: a
# datum named "Datum D" caught clicks 25 mm wide. For DrawingKit's symbols
# the frame is kept to the drawing itself (its SVG's 0.5 mm margin) and the
# name is left out.
CLICK_INSET = 0.0          # mm in from the SVG's edge


def trim_click_box(item):
    """Fit one symbol's frame to its drawing. True if anything changed."""
    kids = item.childItems()
    rects = [k for k in kids if isinstance(k, QtWidgets.QGraphicsRectItem)]
    groups = [k for k in kids if isinstance(k, QtWidgets.QGraphicsItemGroup)]
    texts = [k for k in kids if isinstance(k, QtWidgets.QGraphicsTextItem)]
    if len(rects) != 1 or not groups:
        return False
    disp = groups[0]
    area = disp.mapRectToItem(item, disp.boundingRect())
    if area.isEmpty():
        return False
    d = CLICK_INSET * rez()
    if area.width() > 2 * d + 1 and area.height() > 2 * d + 1:
        area = area.adjusted(d, d, -d, -d)
    changed = False
    border = rects[0]
    want = border.mapRectFromItem(item, area)
    r = border.rect()
    if max(abs(r.left() - want.left()), abs(r.top() - want.top()),
           abs(r.right() - want.right()), abs(r.bottom() - want.bottom())) > 0.5:
        border.setRect(want)
        changed = True
    c = area.center()
    for t in texts:
        if t.toPlainText():
            t.setPlainText("")
            changed = True
        f = t.font()
        if f.pixelSize() != 1:
            f.setPixelSize(1)
            t.setFont(f)
            t.document().setDocumentMargin(0)
            changed = True
        br = t.boundingRect()
        pos = QtCore.QPointF(c.x() - br.width() / 2.0, c.y() - br.height() / 2.0)
        if (t.pos() - pos).manhattanLength() > 0.5:
            t.setPos(pos)
            changed = True
    return changed


class ClickBoxes:
    """Keeps the page's DrawingKit symbols' frames tight: TechDraw draws
    them again on every redraw, hover and selection, so they are fitted
    again after each change to the scene."""

    # TechDraw deletes and makes its items behind Python's back: none is kept
    # between passes (a kept one may be gone), they are looked up each time.

    # The page's viewport filter calls flush() just before each paint, for
    # the areas that changed since the last one (a frame drawn wide on hover
    # is fitted before it is ever seen); the timer is a full pass after a
    # burst of changes, in case a paint came by another way.

    def __init__(self, page, scene):
        self.page, self.scene = page, scene
        self.pending = None                   # changed scene area not yet fitted
        self.busy = False
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(30)            # one pass for a burst of changes
        self.timer.timeout.connect(self.trim_all)
        scene.changed.connect(self.on_changed)

    def on_changed(self, rects=None):
        if self.busy:
            return
        try:
            for r in rects or []:
                self.pending = QtCore.QRectF(r) if self.pending is None else self.pending.united(r)
            if not rects:
                self.pending = self.scene.sceneRect()
        except Exception:
            self.pending = self.scene.sceneRect()
        if not self.timer.isActive():
            self.timer.start()

    def _names(self):
        from . import annot
        try:
            return {o.Name for o in self.page.Views
                    if annot.KIND in o.PropertiesList or annot.LEADER_OF in o.PropertiesList}
        except Exception:
            return set()

    def _trim(self, items):
        names = self._names()
        if not names:
            return
        self.busy = True
        try:
            for it in items:
                try:
                    if it.data(0) == "QGIV" and it.data(1) in names and it.parentItem() is None:
                        trim_click_box(it)
                except Exception:
                    continue
        finally:
            self.busy = False

    def flush(self):
        """Fit the frames in the area changed since the last paint."""
        area, self.pending = self.pending, None
        if area is None or area.isEmpty():
            return
        self._trim(self.scene.items(area.adjusted(-1, -1, 1, 1)))

    def trim_all(self):
        self.pending = None
        self._trim(self.scene.items())


def install_drag_everywhere():
    """Hook every open page, and pages opened later."""
    for doc in App.listDocuments().values():
        for obj in doc.Objects:
            if obj.isDerivedFrom("TechDraw::DrawPage"):
                try:
                    install_drag(obj)
                except Exception:
                    pass


def _on_subwindow(_win):
    QtCore.QTimer.singleShot(200, install_drag_everywhere)


def start_watching():
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    if mdi is not None:
        mdi.subWindowActivated.connect(_on_subwindow)
    install_drag_everywhere()


# ---------------------------------------------------------------------------
# Detail view letters on their base view
# ---------------------------------------------------------------------------
ADJUSTS = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]


def _letter_box(scene, letter, near, reach):
    """Page box of the detail letter TechDraw drew nearest ``near``."""
    best = None
    for it in scene.items():
        try:
            if it.type() == 13 or not isinstance(it, QtWidgets.QGraphicsTextItem):
                continue
            if it.toPlainText() != letter:
                continue
            # the capitals themselves, not the text item's padded box
            fm = QtGui.QFontMetricsF(it.font())
            m = it.document().documentMargin()
            cap = fm.capHeight() if hasattr(fm, "capHeight") else fm.ascent() * 0.72
            adv = fm.horizontalAdvance(letter) if hasattr(fm, "horizontalAdvance") \
                else fm.width(letter)
            r = it.mapRectToScene(QtCore.QRectF(m, m + fm.ascent() - cap, adv, cap))
        except Exception:
            continue
        x0, y1 = to_page(r.topLeft())
        x1, y0 = to_page(r.bottomRight())
        d = abs((x0 + x1) / 2 - near[0]) + abs((y0 + y1) / 2 - near[1])
        if d < reach and (best is None or d < best[0]):
            best = (d, (x0, y0, x1, y1))
    return best[1] if best else None


def place_detail_letter(det):
    """Turn the detail's mark on its base view (TechDraw's Highlight adjust)
    until the letter is clear of the view's lines; it stays put if it is
    clear already or no turn helps. Returns the angle used, or None."""
    from . import sheetcheck
    base = getattr(det, "BaseView", None)
    vo = getattr(base, "ViewObject", None)
    if base is None or vo is None or not hasattr(vo, "HighlightAdjust"):
        return None
    try:
        page = det.findParentPage()
    except Exception:
        return None
    scene = page_scene(page)
    if scene is None:
        return None
    cx, cy = core.view_center(base)
    s = core.view_scale(base)
    a = det.AnchorPoint
    centre = (cx + a.x * s, cy + a.y * s)
    reach = float(det.Radius) * s * 4.0 + 15.0
    segs = sheetcheck._view_segments(base)

    def crossings():
        Gui.updateGui()
        box = _letter_box(scene, det.Reference, centre, reach)
        if box is None:
            return None
        inner = sheetcheck._shrink(box, 0.2)
        return sum(1 for sg in segs if sheetcheck._crosses(sg, inner))

    start = float(vo.HighlightAdjust)
    best = (crossings(), 0.0, start)
    if best[0] is None or best[0] == 0:
        return start
    for adj in sorted(ADJUSTS, key=lambda x: min(abs(x - start), 360 - abs(x - start))):
        vo.HighlightAdjust = adj
        n = crossings()
        if n == 0:
            return adj
        turn = min(abs(adj - start), 360 - abs(adj - start))
        if n is not None and (n, turn) < best[:2]:
            best = (n, turn, adj)
    # nowhere clear (a crowded spot): the turn that crosses fewest lines
    vo.HighlightAdjust = best[2]
    Gui.updateGui()
    return best[2]
