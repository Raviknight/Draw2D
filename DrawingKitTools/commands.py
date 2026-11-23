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
            if hasattr(o, "Shape") and not o.isDerivedFrom("TechDraw::DrawView")
            and not o.Shape.isNull()]


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


class SheetDialog(QtWidgets.QDialog):
    """Pick a sheet size and orientation."""

    last_size = "A3"
    last_orient = "Landscape"

    def __init__(self, templates, parent=None):
        super().__init__(parent)
        self.templates = templates
        self.setWindowTitle("DrawingKit - New drawing sheet")
        form = QtWidgets.QFormLayout(self)
        self.size = QtWidgets.QComboBox()
        sizes = [s for s in core.ISO_SIZES + core.ANSI_SIZES
                 if any((s, o) in templates for o in core.ORIENTATIONS)]
        self.size.addItems(sizes)
        self.orient = QtWidgets.QComboBox()
        form.addRow("Sheet size", self.size)
        form.addRow("Orientation", self.orient)
        self.views = QtWidgets.QCheckBox("Add standard views of the selected part")
        self.iso = QtWidgets.QCheckBox("Include an isometric view")
        self.views.setChecked(bool(selected_parts()))
        self.views.setEnabled(bool(selected_parts()))
        self.iso.setEnabled(self.views.isChecked())
        self.views.toggled.connect(self.iso.setEnabled)
        form.addRow(self.views)
        form.addRow(self.iso)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.size.currentTextChanged.connect(self._fill_orientations)
        if self.last_size in sizes:
            self.size.setCurrentText(self.last_size)
        self._fill_orientations(self.size.currentText())

    def _fill_orientations(self, size):
        current = self.orient.currentText() or self.last_orient
        self.orient.clear()
        options = [o for o in core.ORIENTATIONS if (size, o) in self.templates]
        self.orient.addItems(options)
        if current in options:
            self.orient.setCurrentText(current)

    def choice(self):
        SheetDialog.last_size = self.size.currentText()
        SheetDialog.last_orient = self.orient.currentText()
        return (self.size.currentText(), self.orient.currentText(),
                self.views.isChecked(), self.iso.isChecked())


def ask_isometric():
    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setWindowTitle("DrawingKit - Standard views")
    box.setText("Add front, top and right views.\nInclude an isometric view as well?")
    yes = box.addButton("With isometric", QtWidgets.QMessageBox.YesRole)
    box.addButton("Without", QtWidgets.QMessageBox.NoRole)
    cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
    box.exec_() if hasattr(box, "exec_") else box.exec()
    clicked = box.clickedButton()
    if clicked == cancel:
        return None
    return clicked == yes


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
                watch_redraw(page)
                say("%s added" % sec.Label)

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
                "ToolTip": "Labels holes like '3X ⌀ 6.40 THRU' or '⌀ 5 ↧ 10'. "
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


class FrameDialog(QtWidgets.QDialog):
    """Feature control frame: characteristic, tolerance, modifier, datums."""

    last = {"char": "position", "tol": "0.05", "dia": True, "mod": "", "datums": ["A", "B", ""]}

    def __init__(self, parent=None):
        from . import gdt
        super().__init__(parent)
        self.setWindowTitle("DrawingKit - Geometric tolerance")
        form = QtWidgets.QFormLayout(self)
        self.char = QtWidgets.QComboBox()
        for key, label in gdt.CHARACTERISTICS:
            self.char.addItem(label, key)
        self.char.setCurrentIndex(max(0, [k for k, _ in gdt.CHARACTERISTICS].index(self.last["char"])))
        self.dia = QtWidgets.QCheckBox("Diameter zone (⌀)")
        self.dia.setChecked(self.last["dia"])
        self.tol = QtWidgets.QLineEdit(self.last["tol"])
        self.mod = QtWidgets.QComboBox()
        self.mod.addItems(["", "MMC", "LMC"])
        self.mod.setCurrentText(self.last["mod"])
        self.datums = [QtWidgets.QLineEdit(d) for d in self.last["datums"]]
        for d in self.datums:
            d.setMaxLength(3)
        form.addRow("Characteristic", self.char)
        form.addRow("Tolerance", self.tol)
        form.addRow("", self.dia)
        form.addRow("Material condition", self.mod)
        row = QtWidgets.QHBoxLayout()
        for d in self.datums:
            row.addWidget(d)
        form.addRow("Datums (primary, secondary, tertiary)", row)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def choice(self):
        c = {"char": self.char.currentData(), "tol": self.tol.text().strip() or "0.05",
             "dia": self.dia.isChecked(), "mod": self.mod.currentText(),
             "datums": [d.text().strip().upper() for d in self.datums]}
        FrameDialog.last = dict(c)
        return c


def _place_symbol(page, hint, make, preview_size):
    from . import interact
    w, h = preview_size

    def on_done(points):
        if not points:
            return
        doc = page.Document
        sym = run_in_transaction(doc, "DrawingKit symbol", lambda: make(points[-1]))
        doc.recompute()
        say("%s placed; double-click it to edit the texts" % sym.Label)

    interact.PagePicker(page, hint,
                        on_move=lambda pt, _p: [("rect", pt[0], pt[1], w, h)],
                        on_click=lambda pt, pts: True, on_done=on_done,
                        finish_on_right_click=False)


class ToleranceFrame:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_ToleranceFrame.svg"),
                "MenuText": "Geometric tolerance frame",
                "ToolTip": "Pick the characteristic, tolerance and datums, then click "
                           "where the frame goes. Drag it like any view; edit the value "
                           "and datum letters in its Editable Texts."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import gdt
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        dlg = FrameDialog(Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        c = dlg.choice()
        _, w, h = gdt.frame_svg(c["char"], c["tol"], c["dia"], c["mod"], c["datums"])
        show_page(page)
        Gui.updateGui()
        _place_symbol(page, "DrawingKit: click where the tolerance frame goes (Esc to cancel)",
                      lambda pt: gdt.add_frame(page, pt[0], pt[1], c["char"], c["tol"],
                                               c["dia"], c["mod"], c["datums"]),
                      (w, h))


class DatumFlag:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Datum.svg"),
                "MenuText": "Datum flag",
                "ToolTip": "Click on the edge or surface line that is the datum; the "
                           "flag's triangle sits there. Lettered A, B, C... automatically."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import gdt
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        letter = gdt.next_datum_letter(page.Document)
        letter, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Datum", "Datum letter:", text=letter)
        if not ok or not letter.strip():
            return
        show_page(page)
        Gui.updateGui()
        from . import interact

        def on_move(pt, _p):
            side = gdt.datum_placement(page, pt[0], pt[1])
            _, w, h, (dx, dy) = gdt.datum_svg(letter, side)
            return [("rect", pt[0] - dx, pt[1] - dy, w, h)]

        def on_done(points):
            if not points:
                return
            doc = page.Document
            sym = run_in_transaction(doc, "DrawingKit datum",
                                     lambda: gdt.add_datum(page, points[-1][0], points[-1][1],
                                                           letter))
            doc.recompute()
            say("%s placed" % sym.Label)

        interact.PagePicker(page, "DrawingKit: click the datum edge (Esc to cancel)",
                            on_move=on_move, on_click=lambda pt, pts: True,
                            on_done=on_done, finish_on_right_click=False)


COMMANDS = [
    ("DrawingKit_NewSheet", NewSheet()),
    ("DrawingKit_StandardViews", StandardViews()),
    ("DrawingKit_ProjectedView", ProjectedView()),
    ("DrawingKit_Section", Section()),
    ("DrawingKit_DetailView", DetailView()),
    ("DrawingKit_CenterMarks", CenterMarks()),
    ("DrawingKit_HoleCallout", HoleCallout()),
    ("DrawingKit_Datum", DatumFlag()),
    ("DrawingKit_ToleranceFrame", ToleranceFrame()),
    ("DrawingKit_FitToPage", FitToPage()),
    ("DrawingKit_SpeedToggle", SpeedToggle()),
]

for _name, _cmd in COMMANDS:
    Gui.addCommand(_name, _cmd)


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
            doc = App.getDocument(key[0])
            obj = doc.getObject(key[1]) if doc else None
            if obj is not None:
                core.enforce_alignment(obj)
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
