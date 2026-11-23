# SPDX-License-Identifier: LGPL-2.1-or-later
"""DrawingKit toolbar commands (GUI only)."""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:  # older FreeCAD PySide shim keeps widgets in QtGui
    QtWidgets = QtGui

from . import core
from . import ui

ICONS = os.path.join(os.path.dirname(__file__), "Resources", "icons")


def icon(name):
    return os.path.join(ICONS, name)


def say(text):
    App.Console.PrintMessage("DrawingKit: %s\n" % text)
    try:
        Gui.getMainWindow().statusBar().showMessage("DrawingKit: " + text, 8000)
    except Exception:
        pass


def warn(text):
    App.Console.PrintWarning("DrawingKit: %s\n" % text)
    QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "DrawingKit", text)


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------
def pages(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]


def parent_page(obj):
    if obj.isDerivedFrom("TechDraw::DrawPage"):
        return obj
    if hasattr(obj, "findParentPage"):
        try:
            page = obj.findParentPage()
            if page:
                return page
        except Exception:
            pass
    for parent in obj.InListRecursive:
        if parent.isDerivedFrom("TechDraw::DrawPage"):
            return parent
    return None


def target_page():
    """Page from the selection, else the page shown in the active tab, else the only page."""
    doc = App.ActiveDocument
    if not doc:
        return None
    for obj in Gui.Selection.getSelection():
        page = parent_page(obj)
        if page:
            return page
    view = Gui.activeView()
    if view is not None and hasattr(view, "getPage"):
        try:
            page = view.getPage()
            if page:
                return page
        except Exception:
            pass
    all_pages = pages(doc)
    if len(all_pages) == 1:
        return all_pages[0]
    return None


def selected_parts():
    return [o for o in Gui.Selection.getSelection()
            if not o.isDerivedFrom("TechDraw::DrawView") and core.shape_of(o) is not None]


def selected_part_view():
    for obj in Gui.Selection.getSelection():
        if obj.isDerivedFrom("TechDraw::DrawViewPart") and \
                not obj.isDerivedFrom("TechDraw::DrawViewSection") and \
                not obj.isDerivedFrom("TechDraw::DrawViewDetail"):
            return obj
        if obj.isDerivedFrom("TechDraw::DrawProjGroup"):
            return obj.Anchor
    return None


def show_page(page):
    """Open (or bring forward) the page's tab, staying in this workbench.

    Not ViewObject.doubleClicked(): in FreeCAD 1.1 that also switches to the
    TechDraw workbench (ViewProviderPage::doubleClicked -> assureWorkbench).
    """
    view = Gui.activeView()
    if view is not None and hasattr(view, "getPage"):
        try:
            if core.same(view.getPage(), page):
                return
        except Exception:
            pass
    try:
        page.ViewObject.show()   # creates the tab if needed
        Gui.updateGui()
    except Exception:
        pass
    view = Gui.activeView()
    if view is not None and hasattr(view, "getPage"):
        try:
            if core.same(view.getPage(), page):
                return
        except Exception:
            pass
    # The tab may exist already but sit behind the 3D view: bring it forward.
    try:
        mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        for win in mdi.subWindowList():
            if win.windowTitle().replace("[*]", "").strip() == page.Label:
                mdi.setActiveSubWindow(win)
                break
    except Exception:
        pass


class RedrawWatcher:
    """For a few seconds after a DrawingKit action, redraw views that came out
    at the wrong size (see core.stale_views)."""

    def __init__(self, page, seconds=20.0, interval_ms=400, retry_s=2.0):
        self.page = page
        self.ticks_left = int(seconds * 1000 / interval_ms)
        self.retry_ticks = max(1, int(retry_s * 1000 / interval_ms))
        self.last_redraw = {}
        self.tick_no = 0
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(interval_ms)
        _WATCHERS.add(self)

    def stop(self):
        self.timer.stop()
        _WATCHERS.discard(self)

    def tick(self):
        self.tick_no += 1
        self.ticks_left -= 1
        try:
            page = self.page
            if self.ticks_left <= 0 or page is None or not page.isValid() \
                    or not page.KeepUpdated:
                self.stop()
                return
            views = core.part_views(page)
            stale = core.stale_views(views)
            if not stale and self.tick_no > 2:
                self.stop()
                return
            for v in stale:
                last = self.last_redraw.get(v.Name, -10 ** 6)
                if self.tick_no - last >= self.retry_ticks:
                    self.last_redraw[v.Name] = self.tick_no
                    v.recompute()
        except Exception as e:
            App.Console.PrintWarning("DrawingKit redraw check: %s\n" % e)
            self.stop()


_WATCHERS = set()


def watch_redraw(page):
    for w in list(_WATCHERS):
        if core.same(w.page, page):
            w.stop()
    RedrawWatcher(page)


def run_in_transaction(doc, name, func):
    doc.openTransaction(name)
    try:
        result = func()
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return result


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
def all_sheets():
    """Every paper size DrawingKit can make, in both orientations."""
    from . import templates as d2t
    return {(s, o): None for s in d2t.SIZES for o in d2t.ORIENTATIONS}


class SheetDialog(ui.Popover):
    """Sheet size + orientation in one row; views options only with a part."""

    last_size = "A3"
    last_orient = "Landscape"

    def __init__(self, templates, parent=None):
        super().__init__("New sheet", parent)
        self.templates = templates
        sizes = [s for s in core.ISO_SIZES + core.ANSI_SIZES
                 if any((s, o) in templates for o in core.ORIENTATIONS)]
        self.size = ui.combo([(s, s) for s in sizes], self.last_size, 80)
        self.land = ui.toggle("▭ Landscape", self.last_orient == "Landscape")
        self.port = ui.toggle("▯ Portrait", self.last_orient == "Portrait")
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.land)
        group.addButton(self.port)
        self.add_row(self.size, self.land, self.port)
        has_part = bool(selected_parts())
        self.views = ui.toggle("+ Views", has_part, "Add front, top and right views of the selected part")
        self.iso = ui.toggle("+ Isometric", False, "Add an isometric view too")
        self.views.toggled.connect(self.iso.setEnabled)
        self.iso.setEnabled(has_part)
        # (no setVisible here: on a widget without a parent it opens its own
        # window, which closes the pop-up; the row is simply left out)
        if has_part:
            self.add_row(self.views, self.iso, stretch=True)

    def exec_(self):
        return self.run()

    def choice(self):
        orient = "Portrait" if self.port.isChecked() else "Landscape"
        SheetDialog.last_size = self.size.currentData()
        SheetDialog.last_orient = orient
        return (self.size.currentData(), orient, self.views.isChecked(), self.iso.isChecked())


def ask_isometric():
    return ui.choose("Standard views", [(False, "Front · Top · Right"),
                                        (True, "+ Isometric")])


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
class NewSheet:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_NewSheet.svg"),
                "MenuText": "New drawing sheet",
                "ToolTip": "Create a TechDraw page: pick ISO A4-A0 or ANSI A-E and "
                           "the orientation. With a part selected, standard views "
                           "can be added straight away."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        parts = selected_parts()
        dlg = SheetDialog(all_sheets(), Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        size, orient, add_views, iso = dlg.choice()

        def work():
            page = core.create_page(doc, size, orient)
            if add_views and parts:
                core.add_standard_views(page, parts, iso)
            return page

        page = run_in_transaction(doc, "DrawingKit new sheet", work)
        doc.recompute()
        show_page(page)
        watch_redraw(page)
        msg = "%s %s sheet created" % (size, orient.lower())
        if add_views and parts:
            msg += ", views at %s" % core.scale_text(page.Scale)
        say(msg)


class StandardViews:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_StandardViews.svg"),
                "MenuText": "Add standard views, fitted",
                "ToolTip": "Select a 3D part (and the page, if there are several). "
                           "Adds front, top and right views (+ optional isometric) "
                           "at a standard scale that fits the sheet."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        parts = selected_parts()
        if not parts:
            warn("Select the 3D part to draw first (in the tree or the 3D view).")
            return
        page = target_page()
        if page is None:
            if pages(doc):
                warn("There are several pages. Select the page too (Ctrl+click it in the tree).")
                return
            dlg = SheetDialog(all_sheets(), Gui.getMainWindow())
            dlg.views.setChecked(True)
            dlg.views.setEnabled(False)
            if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
                return
            size, orient, _, iso = dlg.choice()
            page = run_in_transaction(
                doc, "DrawingKit new sheet",
                lambda: core.create_page(doc, size, orient))
        else:
            iso = ask_isometric()
            if iso is None:
                return
        run_in_transaction(doc, "DrawingKit standard views",
                           lambda: core.add_standard_views(page, parts, iso))
        doc.recompute()
        show_page(page)
        watch_redraw(page)
        say("Standard views added at %s" % core.scale_text(page.Scale))


class FitToPage:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_FitToPage.svg"),
                "MenuText": "Fit to page",
                "ToolTip": "Re-pick the standard scale of the page so the views "
                           "and sections fit the drawing area, and re-centre them."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        doc = App.ActiveDocument
        page = target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        try:
            scale = run_in_transaction(doc, "DrawingKit fit to page",
                                       lambda: core.fit_page(page))
        except ValueError as e:
            warn(str(e))
            return
        doc.recompute()
        watch_redraw(page)
        say("Page scale is now %s" % core.scale_text(scale))


class Section:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Section.svg"),
                "MenuText": "Section by cut line",
                "ToolTip": "Select a view. Click on it where the cut goes: one point "
                           "for a straight cut, or several points for a stepped "
                           "(zig-zag) cut. Right-click or Enter, then click where the "
                           "section view goes; the arrows point that way. Lettered "
                           "A-A, B-B... automatically."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        base, page = _base_view_and_page()
        if base is None:
            return
        Gui.Selection.clearSelection()
        from . import interact

        def preview_line(points, cursor=None):
            pts = list(points) + ([cursor] if cursor else [])
            snapped = core.snap_cut_points(pts)
            if len(snapped) == 1:
                x, y = snapped[0]
                r = 4.0
                return [("line", [(x - r, y), (x + r, y)]), ("line", [(x, y - r), (x, y + r)])]
            return [("line", snapped)]

        def on_move_cut(pt, points):
            return preview_line(points, pt)

        def on_cut_done(points):
            if not points:
                return
            cut = core.snap_cut_points(points)

            def on_move_place(pt, _p):
                shapes = preview_line(cut)
                try:
                    direction = core.cut_orientation(cut, pt)
                    sec_dir, sec_x = core._section_vectors(base, direction)
                    w, h = core.projected_extent(core.source_shape(base.Source), sec_dir, sec_x)
                    s = core.view_scale(base)
                    bx, by = core.view_center(base)
                    if direction in ("Right", "Left"):
                        x, y = pt[0], by
                    else:
                        x, y = bx, pt[1]
                    shapes.append(("rect", x, y, w * s, h * s))
                except Exception:
                    pass
                return shapes

            def on_place_done(places):
                if not places:
                    return
                doc = page.Document
                sec = run_in_transaction(
                    doc, "DrawingKit section",
                    lambda: core.add_cut_section(page, base, cut, places[-1]))
                doc.recompute()
                extra = ""
                try:
                    from . import hatching
                    sym, parts = hatching.hatch_by_part(page, sec)
                    if sym is not None:
                        doc.recompute()
                        extra = " (hatched part by part)"
                except Exception as e:
                    App.Console.PrintWarning("DrawingKit hatching: %s\n" % e)
                watch_redraw(page)
                say("%s added%s" % (sec.Label, extra))

            interact.PagePicker(page, "DrawingKit: click where the section view goes "
                                      "(Esc to cancel)",
                                on_move=on_move_place, on_click=lambda pt, pts: True,
                                on_done=on_place_done, finish_on_right_click=False)

        interact.PagePicker(page, "DrawingKit: click the cut line on the view (one point = "
                                  "straight cut, more = stepped). Right-click or Enter "
                                  "when done, Esc to cancel.",
                            on_move=on_move_cut, on_click=lambda pt, pts: False,
                            on_done=lambda pts: QtCore.QTimer.singleShot(
                                0, lambda: on_cut_done(pts)))


class SpeedToggle:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Speed.svg"),
                "MenuText": "Fast editing on/off",
                "ToolTip": "On: the page stops updating itself and views use Coarse "
                           "View, so edits don't freeze FreeCAD. Off: full-quality "
                           "views and one redraw."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        doc = App.ActiveDocument
        page = target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        fast = not core.is_fast(page)
        run_in_transaction(doc, "DrawingKit fast editing",
                           lambda: core.set_fast(page, fast))
        if not fast:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                doc.recompute()
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            watch_redraw(page)
        say("Fast editing %s for %s" % ("ON" if fast else "OFF (redrawn)", page.Label))


class HatchByPart:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_HatchParts.svg"),
                "MenuText": "Hatch by part",
                "ToolTip": "Assembly sections: each part's cut faces get their own hatch "
                           "angle (and spacing after six parts); shafts, pins, bolts, "
                           "screws, keys, nuts and washers are left unhatched. Select a "
                           "section (or none for every section on the page); run again "
                           "after changing the model."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import hatching
        page = target_page()
        if page is None:
            warn("Open the page (or select a section on it) first.")
            return
        secs = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewSection")]
        secs = secs or [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawViewSection")]
        if not secs:
            warn("There is no section view on this page.")
            return
        doc = page.Document

        def work():
            done = []
            for s in secs:
                sym, parts = hatching.hatch_by_part(page, s)
                if sym is not None:
                    done.append((s, parts))
            return done
        done = run_in_transaction(doc, "DrawingKit hatch by part", work)
        doc.recompute()
        if not done:
            say("Nothing to do: hatching by part needs a straight section through two or more parts")
        else:
            say("; ".join("%s: %d part(s) hatched, %d left uncut" % (
                s.Label, sum(1 for p in parts.values() if not p["skip"]),
                sum(1 for p in parts.values() if p["skip"])) for s, parts in done))


class HiddenLines:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Hidden.svg"),
                "MenuText": "Hidden lines on/off",
                "ToolTip": "Show hidden edges as dashed lines (e.g. counterbores seen from "
                           "the side) in the selected views, or in every view on the page "
                           "if none is selected. Click again to hide them."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        picked = [o for o in Gui.Selection.getSelection()
                  if o.isDerivedFrom("TechDraw::DrawViewPart")]
        views = picked or core.part_views(page)
        on = not core.has_hidden_lines(views)
        doc = page.Document
        changed = run_in_transaction(doc, "DrawingKit hidden lines",
                                     lambda: core.set_hidden_lines(views, on))
        core.redraw(changed)
        doc.recompute()
        say("Hidden lines %s in %d view(s)" % ("shown" if on else "hidden", len(changed)))


def _base_view_and_page():
    base = selected_part_view()
    if base is None:
        warn("Select a view on the page first (click one of its lines).")
        return None, None
    page = parent_page(base)
    if page is None:
        warn("That view is not on a page.")
        return None, None
    show_page(page)
    Gui.updateGui()
    return base, page


class ProjectedView:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_ProjectedView.svg"),
                "MenuText": "Projected view by pointing",
                "ToolTip": "Select a view, then move the mouse to where the new view "
                           "should go and click: right, left, above, below or a corner "
                           "(isometric-style). Click again for more views; Esc to stop."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        base, page = _base_view_and_page()
        if base is None:
            return
        Gui.Selection.clearSelection()
        self._pick(page, base)

    def _pick(self, page, base):
        from . import interact
        doc = page.Document

        def on_move(pt, _points):
            try:
                x, y, w, h = core.projection_preview(page, base, pt)
            except Exception:
                return []
            return [("line", [core.view_center(base), (x, y)]), ("rect", x, y, w, h)]

        def on_done(points):
            if not points:
                return
            try:
                view = run_in_transaction(
                    doc, "DrawingKit projected view",
                    lambda: core.add_projected_view(page, base, points[-1]))
            except ValueError as e:
                warn(str(e))
            else:
                doc.recompute()
                watch_redraw(page)
                say("%s added" % view.Label)
            # keep going from the same base view until Esc
            QtCore.QTimer.singleShot(0, lambda: self._pick(page, base))

        interact.PagePicker(page, "DrawingKit: click where the projected view goes "
                                  "(Esc to stop)",
                            on_move=on_move, on_click=lambda pt, pts: True,
                            on_done=on_done, finish_on_right_click=False)


class CenterMarks:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_CenterMarks.svg"),
                "MenuText": "Centre marks and centre lines",
                "ToolTip": "Adds centre marks to holes and round features seen end-on, "
                           "and centre lines along their axes seen from the side. "
                           "Select views, or the page for all views. Run again to "
                           "refresh them after changes."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        doc = App.ActiveDocument
        views = []
        for obj in Gui.Selection.getSelection():
            if obj.isDerivedFrom("TechDraw::DrawProjGroup"):
                views.extend(obj.Views)
            elif obj.isDerivedFrom("TechDraw::DrawViewPart") and \
                    not obj.isDerivedFrom("TechDraw::DrawViewDetail"):
                views.append(obj)
        if not views:
            page = target_page()
            if page is None:
                warn("Select views or the page first.")
                return
            views = [v for v in core.part_views(page)
                     if not v.isDerivedFrom("TechDraw::DrawViewDetail")]
        total = run_in_transaction(doc, "DrawingKit centre marks",
                                   lambda: sum(core.add_center_marks(v) for v in views))
        say("%d centre line(s) on %d view(s)" % (total, len(views)))


class DetailView:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_DetailView.svg"),
                "MenuText": "Detail view by clicking",
                "ToolTip": "Click the centre of the area to enlarge on a view, move out "
                           "and click to set the circle, then click where the detail "
                           "goes. Letter and a standard scale are picked for you."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import interact
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        show_page(page)
        Gui.updateGui()
        Gui.Selection.clearSelection()
        state = {}

        def dist(a, b):
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

        def on_move(pt, points):
            if not points:
                return [("circle", pt[0], pt[1], 1.0)]
            if len(points) == 1:
                return [("circle", points[0][0], points[0][1], max(dist(points[0], pt), 0.5))]
            c, r = points[0], dist(points[0], points[1])
            scale = core.detail_scale(core.view_scale(state["base"]),
                                      r / core.view_scale(state["base"]))
            zoom = scale / core.view_scale(state["base"])
            return [("circle", c[0], c[1], r), ("line", [c, pt]),
                    ("circle", pt[0], pt[1], r * zoom)]

        def on_click(pt, points):
            if len(points) == 1:
                base = interact._view_under(page, pt[0], pt[1], margin=0.0)
                if base is None or base.isDerivedFrom("TechDraw::DrawViewDetail"):
                    points.pop()
                    say("Click inside a view for the detail's centre")
                    return False
                state["base"] = base
                return False
            if len(points) == 2:
                if dist(points[0], points[1]) < 1.0:
                    points.pop()
                return False
            return True

        def on_done(points):
            if len(points) < 3:
                return
            doc = page.Document
            det = run_in_transaction(
                doc, "DrawingKit detail view",
                lambda: core.add_detail(page, state["base"], points[0],
                                        dist(points[0], points[1]), points[2]))
            doc.recompute()
            say("%s added at %s" % (det.Label, core.scale_text(det.Scale)))

        interact.PagePicker(page, "DrawingKit: click the centre of the detail, then its edge, "
                                  "then where the detail goes (Esc to cancel)",
                            on_move=on_move, on_click=on_click, on_done=on_done,
                            finish_on_right_click=False)


class HoleCallout:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_HoleCallout.svg"),
                "MenuText": "Hole callouts",
                "ToolTip": "Labels holes like '3X ⌀6.4 THRU', '⌀5 ↧ 10' or '4X ⌀6.6 THRU ⌴ ⌀11 ↧ 6.4'. "
                           "Select hole circles on a view for just those, or a view "
                           "(or the page) for every kind of hole seen end-on."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import holes
        doc = App.ActiveDocument
        jobs = []   # (page, view, holes or None)
        for sel in Gui.Selection.getSelectionEx():
            obj = sel.Object
            if not obj.isDerivedFrom("TechDraw::DrawViewPart"):
                continue
            edges = [n for n in sel.SubElementNames if n.startswith("Edge")]
            page = parent_page(obj)
            if edges:
                picked = holes.holes_for_edges(obj, edges)
                if picked:
                    jobs.append((page, obj, picked))
            else:
                jobs.append((page, obj, None))
        if not jobs:
            page = target_page()
            if page is None:
                warn("Select hole circles, a view, or the page first.")
                return
            jobs = [(page, v, None) for v in core.part_views(page)
                    if not v.isDerivedFrom("TechDraw::DrawViewSection")
                    and not v.isDerivedFrom("TechDraw::DrawViewDetail")]

        def work():
            made = []
            for page, view, hs in jobs:
                made.extend(holes.add_callouts(page, view, hs))
            return made

        made = run_in_transaction(doc, "DrawingKit hole callouts", work)
        doc.recompute()
        say("%d hole callout(s) added" % len(made) if made
            else "No holes seen end-on in the selected views")


GDT_SYMBOLS = {
    "position": "⌖", "flatness": "⏥", "straightness": "⏤",
    "circularity": "○", "cylindricity": "⌭", "perpendicularity": "⟂",
    "parallelism": "∥", "angularity": "∠", "profile_line": "⌒",
    "profile_surface": "⌓", "concentricity": "◎", "symmetry": "⌯",
    "circular_runout": "↗", "total_runout": "⌰",
}


ZONE_TIPS = [
    ("U", "Ⓤ Unequally disposed profile zone; type the offset in the next box"),
    ("CZ", "CZ Common zone: one zone for several features"),
    ("P", "Ⓟ Projected tolerance zone (e.g. threaded holes); height in the next box"),
    ("F", "Ⓕ Free state (non-rigid parts)"),
    ("T", "Ⓣ Tangent plane"),
    ("DP", "△ Dynamic profile (ASME): controls form/size of the profile, not its location"),
    ("ST", "ST Statistical tolerance (ASME)"),
]


class _SegmentRow:
    """One frame row: symbol | ⌀ tol Ⓜ | A Ⓜ | B Ⓜ | C Ⓜ"""

    def __init__(self, seg, show_char=True):
        from . import gdt
        self.char = ui.combo([(k, "%s  %s" % (GDT_SYMBOLS.get(k, ""), lab))
                              for k, lab in gdt.CHARACTERISTICS], seg.get("char", "position"), 150)
        if not show_char:
            # never setVisible(True) here: the combo has no parent yet, so it
            # would open as its own window and close the pop-up at once
            self.char.setVisible(False)
        self.dia = ui.toggle("⌀", seg.get("dia", True), "Diameter (cylindrical) zone")
        self.tol = ui.field(seg.get("tol", "0.05"), 52, tip="Tolerance")
        self.mod = ui.mmc_button(seg.get("mod", ""))
        self.datums, self.dmods = [], []
        for i in range(3):
            d = ui.field((seg.get("datums") or ["", "", ""])[i], 26, "–",
                         ("Primary", "Secondary", "Tertiary")[i] + " datum")
            d.setMaxLength(3)
            self.datums.append(d)
            self.dmods.append(ui.mmc_button((seg.get("datum_mods") or ["", "", ""])[i], datum=True))

    def widgets(self):
        w = [self.char, self.dia, self.tol, self.mod]
        for d, m in zip(self.datums, self.dmods):
            w += [d, m]
        return w

    def value(self, char=None):
        return {"char": char or self.char.currentData(), "tol": self.tol.text().strip() or "0.05",
                "dia": self.dia.isChecked(), "mod": self.mod.value(),
                "datums": [d.text().strip().upper() for d in self.datums],
                "datum_mods": [m.value() for m in self.dmods]}


class FrameDialog(ui.Popover):
    """Feature control frame as one compact row, like the frame itself.
    '+ Segment' adds a composite or stacked second row."""

    last = {"char": "position", "tol": "0.05", "dia": True, "mod": "",
            "datums": ["A", "B", ""], "datum_mods": ["", "", ""], "count": ""}

    def __init__(self, parent=None, count=""):
        super().__init__("Tolerance frame", parent)
        self.row1 = _SegmentRow(self.last)
        self.add_row(*self.row1.widgets())
        self.count = ui.field(count or self.last.get("count", ""), 40, "6X",
                              "Count above the frame (filled in when you click one of several holes)")
        self.more = ui.toggle("+ Segment", False, "Second row: composite or stacked frame")
        self.opts = ui.toggle("Modifiers…", False,
                              "Zone modifiers (Ⓤ CZ Ⓟ Ⓕ Ⓣ △ ST), all around, between, CF")
        self.add_row("Count", self.count, self.more, self.opts, stretch=True)
        # zone modifiers and leader symbols, hidden until asked for
        last = self.last
        zones = last.get("zones") or []
        self.z = {}
        tips = dict(ZONE_TIPS)
        for code, label in (("U", "Ⓤ"), ("CZ", "CZ"), ("P", "Ⓟ"), ("F", "Ⓕ"), ("T", "Ⓣ"),
                            ("DP", "△"), ("ST", "ST")):
            self.z[code] = ui.toggle(label, code in zones, tips[code])
        self.uval = ui.field(last.get("uval", ""), 34, "0.1", "Ⓤ offset towards the outside of the profile")
        self.proj = ui.field(last.get("proj", ""), 34, "15", "Ⓟ projected zone height")
        self.zrow = self.add_row(self.z["U"], self.uval, self.z["CZ"], self.z["P"], self.proj,
                                 self.z["F"], self.z["T"], self.z["DP"], self.z["ST"], stretch=True)
        self.around = ui.toggle("○ All around", last.get("all_around", False),
                                "All around: the tolerance applies to the whole outline in this view")
        b = last.get("between") or ["", ""]
        self.b1 = ui.field(b[0], 26, "J", "Between: from point")
        self.b2 = ui.field(b[1], 26, "K", "Between: to point")
        self.cf = ui.toggle("CF", last.get("cf", False),
                            "Continuous feature (ASME): interrupted surfaces treated as one")
        self.lrow = self.add_row(self.around, "Between", self.b1, "↔", self.b2, self.cf, stretch=True)
        for r in (self.zrow, self.lrow):
            r.setVisible(bool(zones or last.get("all_around") or any(b) or last.get("cf")))
        self.opts.setChecked(self.zrow.isVisible())
        self.opts.toggled.connect(lambda on: (self.zrow.setVisible(on), self.lrow.setVisible(on),
                                              self.adjustSize()))
        self.kind = ui.combo([(True, "Composite"), (False, "Stacked")], True, 100)
        self.row2 = _SegmentRow({"tol": "0.02", "dia": True, "datums": ["A", "", ""]}, show_char=False)
        self.seg2 = self.add_row(self.kind, *self.row2.widgets())
        self.seg2.setVisible(False)
        self.kind.currentIndexChanged.connect(
            lambda _i: self.row2.char.setVisible(not self.kind.currentData()))
        self.more.toggled.connect(lambda on: (self.seg2.setVisible(on), self.adjustSize()))

    def exec_(self):
        return self.run()

    def choice(self):
        first = self.row1.value()
        first["zones"] = [code for code, b in self.z.items() if b.isChecked()]
        if "U" in first["zones"]:
            first["uval"] = self.uval.text().strip() or "0.1"
        if "P" in first["zones"] and self.proj.text().strip():
            first["proj"] = self.proj.text().strip()
        c = dict(first)
        c["count"] = self.count.text().strip()
        c["all_around"] = self.around.isChecked()
        c["cf"] = self.cf.isChecked()
        between = [self.b1.text().strip().upper(), self.b2.text().strip().upper()]
        if all(between):
            c["between"] = between
        FrameDialog.last = dict(c)
        if self.more.isChecked():
            composite = bool(self.kind.currentData())
            second = self.row2.value(first["char"] if composite else None)
            c["rows"] = [first, second]
            c["composite"] = composite
        return c


def _hole_count_at(page, pt):
    """'6X' if the page point is on/inside one of several identical holes."""
    from . import holes
    for v in core.part_views(page):
        try:
            x0, y0, x1, y1 = core.view_box(v)
        except Exception:
            continue
        if not (x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1):
            continue
        try:
            shape = core.source_shape(v.Source)
            hs = holes.holes_seen_end_on(v, shape)
        except Exception:
            continue
        s = core.view_scale(v)
        for h in hs:
            hx, hy = core.model_to_page(v, h.centre, shape)
            if ((pt[0] - hx) ** 2 + (pt[1] - hy) ** 2) ** 0.5 <= h.radius * s + 2.0:
                group = [g for g in holes.group_holes(hs) if h in g][0]
                return ("%dX" % len(group)) if len(group) > 1 else ""
    return ""


def _two_click(page, hint1, hint2, preview, done):
    """Click the feature, then where the box goes. Enter/right-click after
    the first click keeps the default box position."""
    from . import interact

    def on_click(pt, points):
        return len(points) >= 2

    def on_move(pt, points):
        if not points:
            return [("line", [(pt[0] - 2, pt[1] - 2), (pt[0] + 2, pt[1] + 2)]),
                    ("line", [(pt[0] - 2, pt[1] + 2), (pt[0] + 2, pt[1] - 2)])]
        return preview(points[0], pt)

    def on_done(points):
        if not points:
            return
        done(points[0], points[1] if len(points) > 1 else None)

    interact.PagePicker(page, hint1 + "  then " + hint2 + "  (Enter: default place, Esc: cancel)",
                        on_move=on_move, on_click=on_click, on_done=on_done,
                        finish_on_right_click=True)


class ToleranceFrame:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_ToleranceFrame.svg"),
                "MenuText": "Geometric tolerance frame",
                "ToolTip": "Pick the characteristic, tolerance, modifiers and datums, click "
                           "the feature (a hole gets its count, e.g. 6X), then click where "
                           "the frame goes. The leader stays attached when the view moves; "
                           "drag the frame to move it. Select a size dimension first to put "
                           "the frame under its value (e.g. position of a hole's axis). "
                           "Modifiers… adds Ⓤ CZ Ⓟ Ⓕ Ⓣ, all around and between."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        dims = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewDimension")]
        page = parent_page(dims[0]) if dims else target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        dlg = FrameDialog(Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        c = dlg.choice()
        if dims:
            doc = page.Document
            sym = run_in_transaction(doc, "DrawingKit tolerance frame",
                                     lambda: annot.add_frame(page, None, c, dimension=dims[0]))
            doc.recompute()
            say("%s placed under the dimension; it moves with it" % sym.Label)
            return
        show_page(page)
        Gui.updateGui()

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("rect", pt[0], pt[1], 40, 7)]

        def done(anchor, box):
            p = dict(c)
            if not p.get("count"):
                p["count"] = _hole_count_at(page, anchor)
            doc = page.Document     # no box (Enter): add_frame picks a clear spot
            sym = run_in_transaction(doc, "DrawingKit tolerance frame",
                                     lambda: annot.add_frame(page, box, p, anchor=anchor))
            doc.recompute()
            say("%s placed; drag it to move, its leader stays on the feature" % sym.Label)

        _two_click(page, "Click the toleranced feature,", "click where the frame goes.",
                   preview, done)


class DatumFlag:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Datum.svg"),
                "MenuText": "Datum flag",
                "ToolTip": "Click the datum edge or surface line, then where the letter box "
                           "goes (Enter for straight out). To make a diameter or a slot's "
                           "centre plane the datum (axis/centre-plane datum), select its "
                           "dimension first. Follows the view; lettered A, B, C..."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        dims = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewDimension")]
        page = parent_page(dims[0]) if dims else target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        letter = annot.next_datum_letter(page.Document)
        letter = ui.ask_text("Datum", "Letter", letter, 34)
        if not letter or not letter.strip():
            return
        doc = page.Document
        if dims:
            sym = run_in_transaction(doc, "DrawingKit datum",
                                     lambda: annot.add_datum(page, 0, 0, letter, dimension=dims[0]))
            doc.recompute()
            say("%s placed on the dimension; drag the box to move it" % sym.Label)
            return
        show_page(page)
        Gui.updateGui()

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("rect", pt[0], pt[1], 7, 7)]

        def done(anchor, box):
            sym = run_in_transaction(doc, "DrawingKit datum",
                                     lambda: annot.add_datum(page, anchor[0], anchor[1], letter, box=box))
            doc.recompute()
            say("%s placed; drag the box to move it, the triangle stays on the edge" % sym.Label)

        _two_click(page, "Click the datum edge,", "click where the letter box goes.", preview, done)


class DatumTarget:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_DatumTarget.svg"),
                "MenuText": "Datum target",
                "ToolTip": "Datum target point: click the point on the part, then where the "
                           "target symbol goes. Label like A1; optional size for a target area."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        pop = ui.Popover("Datum target")
        lab_f = ui.field("A1", 40, tip="Target, e.g. A1")
        size_f = ui.field("", 60, "point", "Target area size, e.g. ⌀3 (blank for a point)")
        pop.add_row(lab_f, "Area", size_f, stretch=True)
        if not pop.run() or not lab_f.text().strip():
            return
        label, size = lab_f.text(), size_f.text()
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("circle", pt[0], pt[1], 5)]

        def done(anchor, box):
            sym = run_in_transaction(doc, "DrawingKit datum target",
                                     lambda: annot.add_target(page, anchor[0], anchor[1],
                                                              label.strip().upper(), size.strip(), box))
            doc.recompute()
            say("%s placed" % sym.Label)

        _two_click(page, "Click the target point,", "click where the symbol goes.", preview, done)


class AuxiliaryView:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_AuxView.svg"),
                "MenuText": "Auxiliary view",
                "ToolTip": "Select a slanted straight edge in a view (the face seen edge-on), "
                           "then click on the side where the view goes. It looks square-on "
                           "at that face and stays lined up with its parent."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import interact
        picked = None
        for sel in Gui.Selection.getSelectionEx():
            if sel.Object.isDerivedFrom("TechDraw::DrawViewPart"):
                edges = [n for n in sel.SubElementNames if n.startswith("Edge")]
                if edges:
                    picked = (sel.Object, edges[0])
                    break
        if picked is None:
            warn("Select the slanted edge in a view first (click it on the page).")
            return
        base, edge = picked
        page = parent_page(base)
        try:
            core._straight_edge(base, edge)
        except ValueError as e:
            warn(str(e))
            return
        show_page(page)
        Gui.updateGui()
        Gui.Selection.clearSelection()

        def on_move(pt, _p):
            try:
                vd, vx, rot, pos, _ = core.auxiliary_setup(page, base, edge, pt)
                w, h = core.projected_extent(core.source_shape(base.Source), vd, vx)
            except Exception:
                return []
            s = core.view_scale(base)
            return [("line", [core.view_center(base), pos]),
                    ("circle", pos[0], pos[1], max(w, h) * s / 2.0)]

        def on_done(points):
            if not points:
                return
            doc = page.Document
            view = run_in_transaction(doc, "DrawingKit auxiliary view",
                                      lambda: core.add_auxiliary_view(page, base, edge, points[-1]))
            doc.recompute()
            watch_redraw(page)
            say("%s added" % view.Label)

        interact.PagePicker(page, "DrawingKit: click on the side where the auxiliary view goes "
                                  "(Esc to cancel)",
                            on_move=on_move, on_click=lambda pt, pts: True,
                            on_done=on_done, finish_on_right_click=False)


class AlignedSection:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_AlignedSection.svg"),
                "MenuText": "Aligned section",
                "ToolTip": "Select a view, click the cut: out through one feature, the "
                           "centre, then out through another (any angle), right-click or "
                           "Enter, then click where the section goes. The angled part is "
                           "swung flat, as for holes on a bolt circle."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        base, page = _base_view_and_page()
        if base is None:
            return
        Gui.Selection.clearSelection()
        from . import interact

        def on_move_cut(pt, points):
            return [("line", list(points) + [pt])] if points else []

        def on_cut_done(points):
            if len(points) < 3:
                warn("Click at least three points: out, centre, out.")
                return
            cut = list(points)

            def on_move_place(pt, _p):
                return [("line", cut), ("rect", pt[0], pt[1], 30, 30)]

            def on_place_done(places):
                if not places:
                    return
                doc = page.Document
                sec = run_in_transaction(doc, "DrawingKit aligned section",
                                         lambda: core.add_aligned_section(page, base, cut, places[-1]))
                doc.recompute()
                watch_redraw(page)
                say("%s added" % sec.Label)

            interact.PagePicker(page, "DrawingKit: click where the section view goes (Esc to cancel)",
                                on_move=on_move_place, on_click=lambda pt, pts: True,
                                on_done=on_place_done, finish_on_right_click=False)

        interact.PagePicker(page, "DrawingKit: click out, centre, out for the aligned cut; "
                                  "right-click or Enter when done (Esc to cancel)",
                            on_move=on_move_cut, on_click=lambda pt, pts: False,
                            on_done=lambda pts: QtCore.QTimer.singleShot(0, lambda: on_cut_done(pts)))


class FillTitleBlock:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_TitleBlock.svg"),
                "MenuText": "Fill title block",
                "ToolTip": "Fill empty title-block fields from the part (name, material, "
                           "drawing number) and your FreeCAD author name, and number the "
                           "sheets 1 / N. Fields you typed in are kept."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        doc = App.ActiveDocument
        changed = run_in_transaction(doc, "DrawingKit title block",
                                     lambda: [core.fill_title_block(p) for p in pages(doc)])
        doc.recompute()
        n = sum(len(c) for c in changed)
        say("Title blocks: %d field(s) filled, %d sheet(s) numbered" % (n, len(changed)))


class HoleTable:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_HoleTable.svg"),
                "MenuText": "Hole table",
                "ToolTip": "Select a view: tags every hole seen end-on (A1, A2... per size) "
                           "and adds a table of their X / Y positions from the part's "
                           "lower-left corner and sizes. Click where the table goes "
                           "(Enter: above the title block)."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import tables, interact
        base, page = _base_view_and_page()
        if base is None:
            return
        Gui.Selection.clearSelection()
        rows = tables.hole_rows(base)
        if not rows:
            warn("No holes seen end-on in %s." % base.Label)
            return
        w, h = 94.0, (len(rows) + 2) * tables.ROW_H

        def on_done(points):
            doc = page.Document
            place = points[-1] if points else None
            table, tags = run_in_transaction(doc, "DrawingKit hole table",
                                             lambda: tables.add_hole_table(page, base, place))
            if place is None:
                core.make_room_for_tables(page)   # default spot: move views off it
            doc.recompute()
            say("Hole table with %d hole(s) added" % len(tags))

        interact.PagePicker(page, "DrawingKit: click where the hole table goes "
                                  "(Enter: above the title block, Esc: cancel)",
                            on_move=lambda pt, _p: [("rect", pt[0], pt[1], w, h)],
                            on_click=lambda pt, pts: True, on_done=on_done,
                            finish_on_right_click=True)


class AddRevision:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Revision.svg"),
                "MenuText": "Add revision",
                "ToolTip": "Adds the next revision (A, B, C...) to the page's revision table "
                           "(made at the top right of the frame if needed) and puts it in "
                           "the title block."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import tables
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        pop = ui.Popover("New revision")
        desc_f = ui.field("Initial release", 200, tip="What changed")
        who_f = ui.field("", 50, "by", "Approved by")
        pop.add_row(desc_f, who_f)
        if not pop.run():
            return
        desc, who = desc_f.text(), who_f.text()
        doc = page.Document
        run_in_transaction(doc, "DrawingKit revision",
                           lambda: tables.add_revision(page, desc.strip(), who.strip()))
        doc.recompute()
        say("Revision added")


def _num(text):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return 0.0


class DimensionDialog(ui.Popover):
    """Onshape-style dimension pop-up:
         [before] 12.00 [after]
         [tol ▾] [+upper] [-lower]
         [.00 ▾] [x] basic  (x) reference
    """

    def __init__(self, settings, parent=None, value=None):
        super().__init__("Dimension", parent)
        self.before = ui.field(settings["before"], 50, "3X", "Text before, e.g. 3X")
        self.after = ui.field(settings["after"], 60, "H7", "Text after, e.g. THRU or H7")
        self.value = QtWidgets.QLabel("" if value is None else "%.*f" % (settings["decimals"], value))
        self.value.setMinimumWidth(48)
        self.value.setAlignment(QtCore.Qt.AlignCenter)
        self.add_row(self.before, self.value, self.after)
        self.tol = ui.combo([("none", "No tol."), ("symmetric", "±"),
                             ("deviation", "+ / −")], settings["tol"], 80)
        def fmt(v):
            return ("%g" % abs(v)) if abs(v) > 1e-12 else ""
        self.upper = ui.field(fmt(settings["upper"]), 56, "0.05", "Upper (or ±) tolerance")
        self.lower = ui.field(fmt(settings["lower"]), 56, "0.05", "Lower tolerance (minus)")
        validator = QtGui.QDoubleValidator(0.0, 1000.0, 6)
        validator.setNotation(QtGui.QDoubleValidator.StandardNotation)
        validator.setLocale(QtCore.QLocale.c())
        self.upper.setValidator(validator)
        self.lower.setValidator(validator)
        self.add_row(self.tol, self.upper, self.lower, stretch=True)
        self.decimals = ui.combo([(0, "0"), (1, ".0"), (2, ".00"), (3, ".000"), (4, ".0000")],
                                 settings["decimals"], 70)
        self.basic = ui.toggle("[x] Basic", settings["basic"],
                               "Basic (theoretically exact, boxed) - no tolerance")
        self.reference = ui.toggle("(x) Ref", settings["reference"], "Reference (in parentheses)")
        self.envelope = ui.toggle("Ⓔ", "Ⓔ" in settings["after"],
                                  "Envelope requirement (ISO 14405 / Rule #1) after the size")

        def envelope(on):
            t = self.after.text().replace("Ⓔ", "").rstrip()
            self.after.setText((t + " Ⓔ") if on else t)
        self.envelope.toggled.connect(envelope)
        self.add_row(self.decimals, self.basic, self.reference, self.envelope, stretch=True)

        def sync(*_):
            basic = self.basic.isChecked()
            t = self.tol.currentData()
            self.tol.setEnabled(not basic)
            self.upper.setVisible(not basic and t != "none")
            self.lower.setVisible(not basic and t == "deviation")
            if value is not None:
                self.value.setText("%.*f" % (self.decimals.currentData(), value))
        self.basic.toggled.connect(sync)
        self.tol.currentIndexChanged.connect(sync)
        self.decimals.currentIndexChanged.connect(sync)
        sync()

    def exec_(self):
        return self.run()

    def choice(self):
        return {"before": self.before.text(), "after": self.after.text(),
                "decimals": int(self.decimals.currentData()), "tol": self.tol.currentData(),
                "upper": _num(self.upper.text()), "lower": _num(self.lower.text()),
                "basic": self.basic.isChecked(), "reference": self.reference.isChecked()}


class EditDimension:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_EditDimension.svg"),
                "MenuText": "Edit dimension",
                "ToolTip": "Select one or more dimensions: set text before/after (3X), "
                           "decimals, tolerance (± or +/−), basic (boxed) or "
                           "reference (in parentheses)."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import dims as dimsmod
        dims = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewDimension")]
        if not dims:
            warn("Select one or more dimensions on the page first.")
            return
        try:
            raw = dims[0].getRawValue()
        except Exception:
            raw = None
        dlg = DimensionDialog(dimsmod.read_dimension(dims[0]), Gui.getMainWindow(), raw)
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        c = dlg.choice()
        doc = App.ActiveDocument
        run_in_transaction(doc, "DrawingKit edit dimension",
                           lambda: [dimsmod.set_dimension(d, **c) for d in dims])
        doc.recompute()
        say("%d dimension(s) updated" % len(dims))


class SmartDimension:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Dimension.svg"),
                "MenuText": "Dimension",
                "ToolTip": "Dimension like in the Sketcher: hover lights up a line, circle, "
                           "point or circle centre; click one or two of them, move the mouse "
                           "(above/below: horizontal, left/right: vertical, diagonal: true "
                           "distance) and click to place. Two circles give centre-to-centre. "
                           "Stays on until Esc."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot, interact, smartdim
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        show_page(page)
        Gui.updateGui()
        Gui.Selection.clearSelection()
        st = {"view": None, "picks": [], "ents": {}, "hover": None}

        def ents_of(view):
            if view.Name not in st["ents"]:
                st["ents"][view.Name] = smartdim.entities(view)
            return st["ents"][view.Name]

        def highlight(view, e, color):
            s = core.view_scale(view)
            if e.kind == "line":
                return ("hl_line", [annot.view_point(view, e.pts[0]),
                                    annot.view_point(view, e.pts[1])], color)
            if e.kind in ("circle", "arc"):
                return ("hl_circle", annot.view_point(view, e.point), e.radius * s, color)
            return ("hl_point", annot.view_point(view, e.point), 1.6, color)

        def same(a, b):
            return a.kind == b.kind and a.index == b.index

        def on_move(pt, _points):
            return _on_move(pt, _points)

        def _on_move(pt, _points):
            view = st["view"] or interact._view_under(page, pt[0], pt[1], margin=3.0)
            st["hover"] = None
            if view is None:
                return []
            local = annot.page_to_local(view, *pt)
            try:
                hov = smartdim.pick(view, local, ents_of(view))
            except Exception:
                hov = None
            st["hover"] = (view, hov) if hov is not None else None
            shapes = [highlight(st["view"], p, "#1f77b4") for p in st["picks"]]
            if hov is not None and not any(same(hov, p) for p in st["picks"]):
                shapes.append(highlight(view, hov, "#ff7f0e"))
            if st["picks"]:
                txt = smartdim.preview_value(st["picks"], local)
                if txt:
                    shapes.append(("text", pt[0] + 2.0, pt[1] + 2.0, txt))
            return shapes

        def on_click(pt, points):
            points.clear()
            hov = st["hover"]
            if hov is not None and len(st["picks"]) < 2 and \
                    (st["view"] is None or core.same(hov[0], st["view"])) and \
                    not any(same(hov[1], p) for p in st["picks"]):
                st["view"] = hov[0]
                st["picks"].append(hov[1])
                return False
            if st["picks"]:
                view = st["view"]
                local = annot.page_to_local(view, *pt)
                t, _, why = smartdim.plan(st["picks"], local)
                if t is None:
                    say(why)
                    return False
                doc = page.Document
                try:
                    dim = run_in_transaction(
                        doc, "DrawingKit dimension",
                        lambda: smartdim.add_dimension(page, view, st["picks"], local, pt))
                    doc.recompute()
                    if dim.Type in ("Diameter", "Radius"):
                        from . import holes
                        holes.set_horizontal_text(dim)
                    say("%s dimension added - pick the next, Esc to finish" % dim.Type)
                except Exception as e:
                    warn(str(e))
                st["view"], st["picks"] = None, []
                st["ents"].clear()
            return False

        interact.PagePicker(page, "DrawingKit dimension: click a line, circle or point (one or "
                                  "two), move to choose, click to place. Esc to finish.",
                            on_move=on_move, on_click=on_click, finish_on_right_click=False)


def _pick_entities(page, hint, done, max_picks=None, label=None):
    """Hover-highlight and click lines, circles and points in one view (like
    the Dimension tool). Enter or right-click finishes; reaching
    ``max_picks`` finishes at once. done(view, picks)."""
    from . import annot, interact, smartdim
    show_page(page)
    Gui.updateGui()
    Gui.Selection.clearSelection()
    st = {"view": None, "picks": [], "ents": {}, "hover": None}

    def ents_of(view):
        if view.Name not in st["ents"]:
            st["ents"][view.Name] = smartdim.entities(view)
        return st["ents"][view.Name]

    def hl(view, e, color):
        s = core.view_scale(view)
        if e.kind == "line":
            return ("hl_line", [annot.view_point(view, e.pts[0]), annot.view_point(view, e.pts[1])], color)
        if e.kind in ("circle", "arc"):
            return ("hl_circle", annot.view_point(view, e.point), e.radius * s, color)
        return ("hl_point", annot.view_point(view, e.point), 1.6, color)

    def same(a, b):
        return a.kind == b.kind and a.index == b.index

    def on_move(pt, _points):
        view = st["view"] or interact._view_under(page, pt[0], pt[1], margin=3.0)
        st["hover"] = None
        shapes = [hl(st["view"], p, "#1f77b4") for p in st["picks"]]
        if view is None:
            return shapes
        try:
            hov = smartdim.pick(view, annot.page_to_local(view, *pt), ents_of(view))
        except Exception:
            hov = None
        if hov is not None:
            st["hover"] = (view, hov)
            if not any(same(hov, p) for p in st["picks"]):
                shapes.append(hl(view, hov, "#ff7f0e"))
        if label and st["picks"]:
            shapes.append(("text", pt[0] + 2.0, pt[1] + 2.0, label(st["picks"])))
        return shapes

    def on_click(pt, points):
        points.clear()
        hov = st["hover"]
        if hov is None or (st["view"] is not None and not core.same(hov[0], st["view"])):
            return False
        if any(same(hov[1], p) for p in st["picks"]):
            return False
        st["view"] = hov[0]
        st["picks"].append(hov[1])
        if max_picks and len(st["picks"]) >= max_picks:
            QtCore.QTimer.singleShot(0, finish)
            return False
        return False

    def finish():
        picker.cancel()
        if st["picks"]:
            try:
                done(st["view"], list(st["picks"]))
            except Exception as e:
                warn(str(e))

    def on_done(_points):
        if st["picks"]:
            try:
                done(st["view"], list(st["picks"]))
            except Exception as e:
                warn(str(e))

    picker = interact.PagePicker(page, hint, on_move=on_move, on_click=on_click,
                                 on_done=on_done, finish_on_right_click=True)
    return picker


class OrdinateDims:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Ordinate.svg"),
                "MenuText": "Ordinate / baseline dimensions",
                "ToolTip": "Dimension many features from one zero edge or point. Pick "
                           "Ordinate (0, 20, 45 … on extension lines), Baseline (stacked) "
                           "or Chain (end to end), and horizontal or vertical. Click the "
                           "zero edge/point first, then each feature; Enter to finish."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    last = {"mode": "ordinate", "axis": "x"}

    def Activated(self):
        from . import drafting
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        pop = ui.Popover("Ordinate / baseline")
        mode = ui.combo([("ordinate", "Ordinate"), ("baseline", "Baseline"), ("chain", "Chain")],
                        self.last["mode"], 100)
        horiz = ui.toggle("↔ Horizontal", self.last["axis"] == "x")
        vert = ui.toggle("↕ Vertical", self.last["axis"] == "y")
        grp = QtWidgets.QButtonGroup(pop)
        grp.setExclusive(True)
        grp.addButton(horiz)
        grp.addButton(vert)
        pop.add_row(mode, horiz, vert)
        if not pop.run():
            return
        m, axis = mode.currentData(), ("x" if horiz.isChecked() else "y")
        OrdinateDims.last = {"mode": m, "axis": axis}
        doc = page.Document

        def done(view, picks):
            if len(picks) < 2:
                warn("Pick the zero edge or point, then at least one feature.")
                return
            if m == "ordinate":
                r = run_in_transaction(doc, "DrawingKit ordinate dimensions", lambda: drafting.add_ordinate(
                    page, view, picks[0], picks[1:], axis))
                n = len(picks) - 1
            else:
                r = run_in_transaction(doc, "DrawingKit %s dimensions" % m, lambda: drafting.add_baseline(
                    page, view, picks[0], picks[1:], axis, chain=(m == "chain")))
                n = len(r)
            doc.recompute()
            say("%d %s dimension(s) added" % (n, m))

        _pick_entities(page, "%s %s: click the zero edge or point, then each feature. "
                             "Enter to finish, Esc to cancel." % (m.capitalize(),
                                                                 "horizontal" if axis == "x" else "vertical"),
                       done, label=lambda picks: "0" if len(picks) == 1 else "%d" % (len(picks) - 1))


class Centerline:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Centerline.svg"),
                "MenuText": "Centerline",
                "ToolTip": "Click two edges (e.g. the sides of a hole or shaft seen from the "
                           "side) or two points: a centerline is drawn midway, extending a "
                           "little past them. It follows the view."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import drafting
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        doc = page.Document

        def done(view, picks):
            if len(picks) < 2:
                return
            run_in_transaction(doc, "DrawingKit centerline",
                               lambda: drafting.add_centerline(view, picks[0], picks[1]))
            doc.recompute()
            say("Centerline added")

        _pick_entities(page, "Centerline: click two edges or two points (Esc to cancel).", done,
                       max_picks=2)


class ChamferCallout:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Chamfer.svg"),
                "MenuText": "Chamfer callout",
                "ToolTip": "Click the slanted chamfer edge, then where the text goes: "
                           "'1 x 45°' (or '2 x 30°'), on a leader that stays on the edge."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot, drafting, interact, smartdim
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("rect", pt[0], pt[1], 22, 5)]

        def done(anchor, box):
            view = interact._view_under(page, anchor[0], anchor[1], margin=3.0)
            if view is None:
                warn("Click on the chamfer edge in a view.")
                return
            local = annot.page_to_local(view, *anchor)
            lines = [e for e in smartdim.entities(view) if e.kind == "line"]
            ent = smartdim.pick(view, local, lines)
            if ent is None:
                warn("No edge there - click right on the chamfer.")
                return
            try:
                text = drafting.chamfer_text(ent)
            except ValueError as e:
                warn(str(e))
                return
            box = box or (anchor[0] + 18, anchor[1] + 12)
            sym = run_in_transaction(doc, "DrawingKit chamfer", lambda: annot.add_note(
                page, box, [text], anchor=anchor))
            doc.recompute()
            say("Chamfer %s placed (a note: select it and press Note to edit)" % text)

        _two_click(page, "Click the chamfer edge,", "click where the text goes.", preview, done)


class SurfaceDialog(ui.Popover):
    last = {"process": "remove", "ra": "Ra 3.2", "lay": "", "method": "", "all_around": False}

    def __init__(self):
        from . import drafting
        super().__init__("Surface texture")
        l = self.last
        self.proc = ui.combo(drafting.SURF_PROCESS, l["process"], 190)
        self.ra = QtWidgets.QComboBox()
        self.ra.setEditable(True)
        for v in ("Ra 0.4", "Ra 0.8", "Ra 1.6", "Ra 3.2", "Ra 6.3", "Ra 12.5", "Rz 6.3", "Rz 25"):
            self.ra.addItem(v)
        self.ra.setEditText(l["ra"])
        self.ra.setFixedWidth(84)
        self.ra.setToolTip("Roughness requirement (pick or type)")
        self.add_row(self.proc, self.ra)
        self.lay = ui.combo([(x, x or "no lay") for x in drafting.LAYS], l["lay"], 70)
        self.lay.setToolTip("Lay (direction of the machining marks)")
        self.method = ui.field(l["method"], 90, "ground", "Manufacturing method (above the line)")
        self.around = ui.toggle("○ All around", l["all_around"], "All surfaces around the outline")
        self.add_row("Lay", self.lay, self.method, self.around, stretch=True)
        self.focus_on = self.ra.lineEdit()

    def choice(self):
        c = {"process": self.proc.currentData(), "ra": self.ra.currentText().strip(),
             "lay": self.lay.currentData(), "method": self.method.text().strip(),
             "all_around": self.around.isChecked()}
        SurfaceDialog.last = dict(c)
        return c


class SurfaceFinish:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_SurfaceFinish.svg"),
                "MenuText": "Surface texture symbol",
                "ToolTip": "ISO 1302 surface symbol: pick the process (any / machined / no "
                           "material removal), Ra value, lay and method, then click the "
                           "surface's edge and where the symbol goes (Enter: on the surface "
                           "if it's on top, else on a leader). Follows the view."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import drafting
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        dlg = SurfaceDialog()
        if not dlg.run():
            return
        c = dlg.choice()
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("line", [pt, (pt[0] - 3, pt[1] + 5), (pt[0], pt[1]),
                                                      (pt[0] + 6, pt[1] + 10)])]

        def done(anchor, box):
            sym = run_in_transaction(doc, "DrawingKit surface symbol",
                                     lambda: drafting.add_surface(page, anchor[0], anchor[1], c, box))
            doc.recompute()
            say("%s placed; drag it to move" % sym.Label)

        _two_click(page, "Click the surface's edge,", "click where the symbol goes.", preview, done)


class WeldDialog(ui.Popover):
    last = {"type": "fillet", "side": "arrow", "size": "a5", "length": "", "all_around": False,
            "field": False, "process": "", "iso_line": True}

    def __init__(self):
        from . import drafting
        super().__init__("Weld")
        l = self.last
        self.kind = ui.combo(drafting.WELDS, l["type"], 130)
        self.side = ui.combo([("arrow", "Arrow side"), ("other", "Other side"), ("both", "Both sides")],
                             l["side"], 100)
        self.size = ui.field(l["size"], 40, "a5", "Size (fillet: a = throat, z = leg)")
        self.length = ui.field(l["length"], 46, "50", "Weld length, or n x l (e)")
        self.add_row(self.kind, self.side, self.size, self.length)
        self.around = ui.toggle("○ All around", l["all_around"])
        self.field_ = ui.toggle("⚑ Field weld", l["field"], "Welded on site")
        self.iso = ui.toggle("ISO dashed line", l["iso_line"], "ISO 2553 system A identification line")
        self.process = ui.field(l["process"], 46, "111", "Process number in the tail (ISO 4063)")
        self.add_row(self.around, self.field_, self.iso, "Tail", self.process, stretch=True)
        self.focus_on = self.size

    def choice(self):
        c = {"type": self.kind.currentData(), "side": self.side.currentData(),
             "size": self.size.text().strip(), "length": self.length.text().strip(),
             "all_around": self.around.isChecked(), "field": self.field_.isChecked(),
             "iso_line": self.iso.isChecked(), "process": self.process.text().strip()}
        WeldDialog.last = dict(c)
        return c


class WeldSymbol:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Weld.svg"),
                "MenuText": "Weld symbol",
                "ToolTip": "ISO 2553 weld symbol: type, side, size and length, all-around, "
                           "field weld and process. Click the joint, then where the reference "
                           "line goes. Follows the view."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import drafting
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        dlg = WeldDialog()
        if not dlg.run():
            return
        c = dlg.choice()
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def preview(anchor, pt):
            return [("line", [anchor, pt, (pt[0] + 22, pt[1])])]

        def done(anchor, box):
            sym = run_in_transaction(doc, "DrawingKit weld symbol",
                                     lambda: drafting.add_weld(page, anchor[0], anchor[1], c, box))
            doc.recompute()
            say("%s placed; drag it to move" % sym.Label)

        _two_click(page, "Click the joint,", "click where the reference line starts.", preview, done)


class PartsList:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_PartsList.svg"),
                "MenuText": "Parts list",
                "ToolTip": "Select a view of an assembly: a parts list (item, qty, part, "
                           "material) goes above the title block, identical parts counted "
                           "together. Balloons with the item numbers are added too."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import drafting
        view = selected_part_view()
        if view is None:
            page = target_page()
            views = core.part_views(page) if page is not None else []
            view = views[0] if views else None
        if view is None:
            warn("Select a view of the assembly first.")
            return
        page = parent_page(view)
        pop = ui.Popover("Parts list")
        balloons = ui.toggle("+ Balloons", True, "Number every part in this view")
        pop.add_row("For %s" % view.Label, balloons, stretch=True)
        if not pop.run():
            return
        doc = page.Document

        def work():
            table, items = drafting.add_parts_list(page, view)
            core.make_room_for_tables(page)     # views off the new table
            made = drafting.auto_balloons(page, view) if balloons.isChecked() else []
            return items, made

        items, made = run_in_transaction(doc, "DrawingKit parts list", work)
        doc.recompute()
        say("Parts list with %d item(s)%s" % (len(items), (", %d balloon(s)" % len(made)) if made else ""))


class Balloon:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Balloon.svg"),
                "MenuText": "Balloon",
                "ToolTip": "Click a part in a view, then where the balloon goes: it gets the "
                           "part's item number from the parts list (or the next number)."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import drafting, interact
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("circle", pt[0], pt[1], 4)]

        def done(anchor, box):
            view = interact._view_under(page, anchor[0], anchor[1], margin=1.0)
            if view is None:
                warn("Click on a part in a view.")
                return
            it, _obj = drafting.part_at(view, anchor[0], anchor[1])
            if it is not None:
                text = it["item"]
            else:
                nums = [int(b.Text) for b in page.Views
                        if b.isDerivedFrom("TechDraw::DrawViewBalloon") and str(b.Text).isdigit()]
                text = max(nums or [0]) + 1
            box = box or (anchor[0] + 15, anchor[1] + 15)
            run_in_transaction(doc, "DrawingKit balloon",
                               lambda: drafting.add_balloon(page, view, anchor, box, text))
            doc.recompute()
            say("Balloon %s placed" % text)

        _two_click(page, "Click the part,", "click where the balloon goes.", preview, done)


class ExportSheets:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Export.svg"),
                "MenuText": "Export PDF / DXF",
                "ToolTip": "Save the current sheet, or all sheets, as PDF, DXF or SVG "
                           "(one file per sheet), named after the drawing number."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    last = {"fmt": "pdf", "all": False}

    def Activated(self):
        from . import export
        page = target_page()
        if page is None:
            warn("Open the sheet to export first.")
            return
        sheets = sorted(pages(page.Document), key=lambda p: p.Label)
        pop = ui.Popover("Export")
        fmt = ui.combo(export.FORMATS, self.last["fmt"], 70)
        every = ui.toggle("All %d sheets" % len(sheets), self.last["all"] and len(sheets) > 1)
        every.setEnabled(len(sheets) > 1)
        pop.add_row(fmt, every, stretch=True)
        if not pop.run():
            return
        f = fmt.currentData()
        ExportSheets.last = {"fmt": f, "all": every.isChecked()}
        chosen = sheets if every.isChecked() else [page]
        folder, base = export.default_base(page)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            Gui.getMainWindow(), "Export as %s" % f.upper(),
            os.path.join(folder, "%s.%s" % (base, f)), "%s (*.%s)" % (f.upper(), f))
        if not path:
            return
        try:
            done = export.export_pages(chosen, path, f)
        except Exception as e:
            warn("Export failed: %s" % e)
            return
        say("Exported %d file(s): %s" % (len(done), ", ".join(os.path.basename(d) for d in done)))


def _one_click(page, hint, preview, done):
    """Click once to place (Esc cancels)."""
    from . import interact

    def on_click(pt, points):
        return len(points) >= 1

    def on_done(points):
        if points:
            done(points[0])

    interact.PagePicker(page, hint + "  (Esc: cancel)", on_move=lambda pt, pts: preview(pt),
                        on_click=on_click, on_done=on_done, finish_on_right_click=False)


class NoteDialog(ui.Popover):
    """Text (several lines), size, font and leader on/off in one small pop-up."""

    def __init__(self, text="", size=3.5, font="osifont", leader=False, editing=False):
        super().__init__("Edit note" if editing else "Note")
        self.text = QtWidgets.QPlainTextEdit(text)
        # tall enough for the note's lines (3 to 8 rows), then it scrolls
        rows = min(8, max(3, len(text.splitlines()) + 1))
        lh = self.text.fontMetrics().lineSpacing()
        self.text.setFixedSize(280, rows * lh + 12)
        self.text.setTabChangesFocus(True)
        self.text.setPlaceholderText("Note text (Enter for a new line)")
        self.add_row(self.text)
        self.style = ui.TextStyleRow(size, font)
        row = self.style.widgets()
        self.leader = None
        if not editing:
            self.leader = ui.toggle("↗ Leader", leader, "Leader with arrow to a feature")
            row = row + [self.leader]
        self.add_row(*row, stretch=True)
        self.focus_on = self.text

    def choice(self):
        size, font = self.style.value()
        return {"text": self.text.toPlainText().rstrip("\n"), "size": size, "font": font,
                "leader": bool(self.leader and self.leader.isChecked())}


class Note:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Note.svg"),
                "MenuText": "Note",
                "ToolTip": "Add a note: type the text (several lines are fine), pick its size "
                           "and font, then click where it goes. With Leader on, click the "
                           "feature first, then the place for the text. Select a note and "
                           "run this again to edit it."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        notes = [o for o in Gui.Selection.getSelection() if annot.is_note(o)]
        if notes:
            sym = notes[0]
            size, font = annot.text_style(sym)
            dlg = NoteDialog(annot.note_text(sym), size, font, editing=True)
            if not dlg.run():
                return
            c = dlg.choice()
            doc = sym.Document

            def edit():
                annot.set_note_text(sym, c["text"])
                annot.set_text_style(sym, c["size"], c["font"])
            run_in_transaction(doc, "DrawingKit edit note", edit)
            doc.recompute()
            say("Note updated")
            return
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        dlg = NoteDialog(size=annot.default_size(), font=annot.default_font())
        if not dlg.run():
            return
        c = dlg.choice()
        if not c["text"].strip():
            return
        lines = c["text"].splitlines()
        h = (c["size"] or 3.5) * (1.6 * (len(lines) - 1) + 1.0)
        w = max(len(ln) for ln in lines) * (c["size"] or 3.5) * 0.6
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def place(box, anchor=None):
            sym = run_in_transaction(doc, "DrawingKit note", lambda: annot.add_note(
                page, box, lines, anchor=anchor, size=c["size"], font=c["font"]))
            doc.recompute()
            say("%s placed; drag it to move, select it and run Note again to edit" % sym.Label)

        if c["leader"]:
            def preview(anchor, pt):
                return [("line", [anchor, pt]), ("rect", pt[0], pt[1], w, h)]

            def done(anchor, box):
                place(box or (anchor[0] + w / 2 + 10, anchor[1] + 10), anchor)
            _two_click(page, "Click the feature,", "click where the text goes.", preview, done)
        else:
            _one_click(page, "Click where the note goes.",
                       lambda pt: [("rect", pt[0], pt[1], w, h)], lambda pt: place(pt))


class TextStyle:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_TextStyle.svg"),
                "MenuText": "Text size and font",
                "ToolTip": "Change the text height and font of the selected dimensions, notes, "
                           "datums, tolerance frames and tables (symbols grow with their "
                           "text). With nothing selected it sets the size and font for new "
                           "DrawingKit notes and symbols."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from . import annot
        objs = [o for o in Gui.Selection.getSelection() if annot.text_style(o) is not None]
        if Gui.Selection.getSelection() and not objs:
            warn("The selection has no text. Select dimensions, notes or DrawingKit symbols "
                 "(or nothing, to set the defaults).")
            return
        if objs:
            size, font = annot.text_style(objs[0])
            title = "Text of %d item%s" % (len(objs), "" if len(objs) == 1 else "s")
        else:
            size, font = annot.default_size(), annot.default_font()
            title = "Default text for new notes and symbols"
        pop = ui.Popover(title)
        row = ui.TextStyleRow(size, font)
        pop.add_row(*row.widgets(), stretch=True)
        if not pop.run():
            return
        size, font = row.value()
        if not objs:
            annot.set_defaults(size, font)
            say("New notes and symbols: %g mm %s" % (size or annot.default_size(), font))
            return
        doc = objs[0].Document
        run_in_transaction(doc, "DrawingKit text size and font",
                           lambda: [annot.set_text_style(o, size, font) for o in objs])
        doc.recompute()
        say("Text updated on %d item(s)" % len(objs))


COMMANDS = [
    ("DrawingKit_NewSheet", NewSheet()),
    ("DrawingKit_StandardViews", StandardViews()),
    ("DrawingKit_ProjectedView", ProjectedView()),
    ("DrawingKit_Section", Section()),
    ("DrawingKit_AlignedSection", AlignedSection()),
    ("DrawingKit_DetailView", DetailView()),
    ("DrawingKit_AuxView", AuxiliaryView()),
    ("DrawingKit_Dimension", SmartDimension()),
    ("DrawingKit_CenterMarks", CenterMarks()),
    ("DrawingKit_HoleCallout", HoleCallout()),
    ("DrawingKit_EditDimension", EditDimension()),
    ("DrawingKit_Datum", DatumFlag()),
    ("DrawingKit_DatumTarget", DatumTarget()),
    ("DrawingKit_ToleranceFrame", ToleranceFrame()),
    ("DrawingKit_Note", Note()),
    ("DrawingKit_TextStyle", TextStyle()),
    ("DrawingKit_Ordinate", OrdinateDims()),
    ("DrawingKit_Centerline", Centerline()),
    ("DrawingKit_Chamfer", ChamferCallout()),
    ("DrawingKit_SurfaceFinish", SurfaceFinish()),
    ("DrawingKit_Weld", WeldSymbol()),
    ("DrawingKit_PartsList", PartsList()),
    ("DrawingKit_Balloon", Balloon()),
    ("DrawingKit_Export", ExportSheets()),
    ("DrawingKit_HoleTable", HoleTable()),
    ("DrawingKit_FillTitleBlock", FillTitleBlock()),
    ("DrawingKit_Revision", AddRevision()),
    ("DrawingKit_FitToPage", FitToPage()),
    ("DrawingKit_SpeedToggle", SpeedToggle()),
    ("DrawingKit_HiddenLines", HiddenLines()),
    ("DrawingKit_HatchByPart", HatchByPart()),
]

for _name, _cmd in COMMANDS:
    Gui.addCommand(_name, _cmd)

# toolbars in the DrawingKit workbench
COMMAND_GROUPS = [
    ("DrawingKit views", ["DrawingKit_NewSheet", "DrawingKit_StandardViews",
                          "DrawingKit_ProjectedView", "DrawingKit_Section",
                          "DrawingKit_AlignedSection", "DrawingKit_HatchByPart", "DrawingKit_DetailView",
                          "DrawingKit_AuxView", "DrawingKit_FitToPage", "DrawingKit_HiddenLines",
                          "DrawingKit_SpeedToggle"]),
    ("DrawingKit dimensions", ["DrawingKit_Dimension", "DrawingKit_Ordinate", "DrawingKit_EditDimension",
                               "DrawingKit_CenterMarks", "DrawingKit_Centerline",
                               "DrawingKit_HoleCallout", "DrawingKit_Chamfer"]),
    ("DrawingKit annotation", ["DrawingKit_SurfaceFinish", "DrawingKit_Weld",
                               "DrawingKit_Datum", "DrawingKit_DatumTarget",
                               "DrawingKit_ToleranceFrame", "DrawingKit_Note",
                               "DrawingKit_TextStyle"]),
    ("DrawingKit tables", ["DrawingKit_PartsList", "DrawingKit_Balloon",
                           "DrawingKit_HoleTable", "DrawingKit_FillTitleBlock",
                           "DrawingKit_Revision", "DrawingKit_Export"]),
]


class AlignmentObserver:
    """Keeps dragged projected views on their alignment axis (see core)."""

    def __init__(self):
        self.pending = set()

    def slotChangedObject(self, obj, prop):
        if prop not in ("X", "Y"):
            return
        try:
            if not obj.isDerivedFrom("TechDraw::DrawView"):
                return
        except Exception:
            return
        key = (obj.Document.Name, obj.Name)
        if key in self.pending:
            return
        self.pending.add(key)
        # Fix it after TechDraw has finished handling the drag.
        QtCore.QTimer.singleShot(0, lambda: self._fix(key))

    def _fix(self, key):
        self.pending.discard(key)
        try:
            from . import annot
            doc = App.getDocument(key[0])
            obj = doc.getObject(key[1]) if doc else None
            if obj is None:
                return
            if annot.is_annotation(obj):
                annot.dragged(obj)          # box moved; anchor stays on the feature
                return
            core.enforce_alignment(obj)
            # annotations hanging on this view (or its group, or a dimension) follow
            moved = [obj]
            if obj.isDerivedFrom("TechDraw::DrawProjGroupItem"):
                group = core.parent_group(obj)
                if group is not None:
                    moved.append(group)
            for m in moved:
                annot.refresh(annot.attached_to(m))
        except Exception as e:  # never break FreeCAD over a layout nicety
            App.Console.PrintWarning("DrawingKit alignment: %s\n" % e)


# This module is imported once per FreeCAD session, so one observer.
ALIGNMENT_OBSERVER = AlignmentObserver()
App.addDocumentObserver(ALIGNMENT_OBSERVER)

try:
    from . import interact as _interact
    _interact.start_watching()
except Exception as _e:  # drag-anywhere is a convenience; never block loading
    App.Console.PrintWarning("DrawingKit: drag-anywhere not available: %s\n" % _e)

COMMAND_NAMES = [n for n, _ in COMMANDS]
