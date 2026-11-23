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
    """Feature control frame: characteristic, tolerance, modifiers, datums."""

    last = {"char": "position", "tol": "0.05", "dia": True, "mod": "",
            "datums": ["A", "B", ""], "datum_mods": ["", "", ""], "count": ""}

    def __init__(self, parent=None, count=""):
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
        self.count = QtWidgets.QLineEdit(count or self.last.get("count", ""))
        self.count.setPlaceholderText("e.g. 6X  (filled in when you click a hole)")
        form.addRow("Characteristic", self.char)
        form.addRow("Tolerance", self.tol)
        form.addRow("", self.dia)
        form.addRow("Material condition", self.mod)
        self.datums, self.dmods = [], []
        for i, name in enumerate(("Primary datum", "Secondary datum", "Tertiary datum")):
            row = QtWidgets.QHBoxLayout()
            d = QtWidgets.QLineEdit(self.last["datums"][i])
            d.setMaxLength(3)
            m = QtWidgets.QComboBox()
            m.addItems(["", "MMC", "LMC"])
            m.setCurrentText(self.last["datum_mods"][i])
            row.addWidget(d)
            row.addWidget(m)
            form.addRow(name, row)
            self.datums.append(d)
            self.dmods.append(m)
        form.addRow("Count above frame", self.count)

        # optional second segment: composite (shared symbol) or stacked
        self.seg2 = QtWidgets.QGroupBox("Second segment")
        self.seg2.setCheckable(True)
        self.seg2.setChecked(False)
        f2 = QtWidgets.QFormLayout(self.seg2)
        self.mode2 = QtWidgets.QComboBox()
        self.mode2.addItem("Composite (one symbol for both rows)", True)
        self.mode2.addItem("Stacked (each row its own symbol)", False)
        self.char2 = QtWidgets.QComboBox()
        for key, label in gdt.CHARACTERISTICS:
            self.char2.addItem(label, key)
        self.tol2 = QtWidgets.QLineEdit("0.02")
        self.dia2 = QtWidgets.QCheckBox("Diameter zone (⌀)")
        self.dia2.setChecked(True)
        self.mod2 = QtWidgets.QComboBox()
        self.mod2.addItems(["", "MMC", "LMC"])
        self.datums2, self.dmods2 = [], []
        row2 = QtWidgets.QHBoxLayout()
        for txt in ("A", "", ""):
            d = QtWidgets.QLineEdit(txt)
            d.setMaxLength(3)
            m = QtWidgets.QComboBox()
            m.addItems(["", "MMC", "LMC"])
            row2.addWidget(d)
            row2.addWidget(m)
            self.datums2.append(d)
            self.dmods2.append(m)
        f2.addRow("Kind", self.mode2)
        f2.addRow("Characteristic (stacked)", self.char2)
        f2.addRow("Tolerance", self.tol2)
        f2.addRow("", self.dia2)
        f2.addRow("Material condition", self.mod2)
        f2.addRow("Datums", row2)
        self.mode2.currentIndexChanged.connect(
            lambda _i: self.char2.setEnabled(not self.mode2.currentData()))
        self.char2.setEnabled(False)
        form.addRow(self.seg2)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def choice(self):
        c = {"char": self.char.currentData(), "tol": self.tol.text().strip() or "0.05",
             "dia": self.dia.isChecked(), "mod": self.mod.currentText(),
             "datums": [d.text().strip().upper() for d in self.datums],
             "datum_mods": [m.currentText() for m in self.dmods],
             "count": self.count.text().strip()}
        FrameDialog.last = dict(c)
        if self.seg2.isChecked():
            composite = bool(self.mode2.currentData())
            first = {k: c[k] for k in ("char", "tol", "dia", "mod", "datums", "datum_mods")}
            second = {"char": c["char"] if composite else self.char2.currentData(),
                      "tol": self.tol2.text().strip() or "0.02", "dia": self.dia2.isChecked(),
                      "mod": self.mod2.currentText(),
                      "datums": [d.text().strip().upper() for d in self.datums2],
                      "datum_mods": [m.currentText() for m in self.dmods2]}
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
                           "drag the frame to move it."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        page = target_page()
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        dlg = FrameDialog(Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        c = dlg.choice()
        show_page(page)
        Gui.updateGui()

        def preview(anchor, pt):
            return [("line", [anchor, pt]), ("rect", pt[0], pt[1], 40, 7)]

        def done(anchor, box):
            p = dict(c)
            if not p.get("count"):
                p["count"] = _hole_count_at(page, anchor)
            box = box or (anchor[0] + 25, anchor[1] + 15)
            doc = page.Document
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
        letter, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Datum", "Datum letter:", text=letter)
        if not ok or not letter.strip():
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
        label, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Datum target", "Target (e.g. A1):", text="A1")
        if not ok or not label.strip():
            return
        size, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Datum target",
            "Target area size (blank for a point, e.g. ⌀3):", text="")
        if not ok:
            return
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
        desc, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Revision", "What changed:", text="Initial release")
        if not ok:
            return
        who, ok = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(), "DrawingKit - Revision", "Approved by:", text="")
        if not ok:
            return
        doc = page.Document
        run_in_transaction(doc, "DrawingKit revision",
                           lambda: tables.add_revision(page, desc.strip(), who.strip()))
        doc.recompute()
        say("Revision added")


class DimensionDialog(QtWidgets.QDialog):
    """Onshape-style dimension pop-up: text before/after, decimals,
    tolerance, basic, reference."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DrawingKit - Edit dimension")
        form = QtWidgets.QFormLayout(self)
        self.before = QtWidgets.QLineEdit(settings["before"])
        self.before.setPlaceholderText("e.g. 3X  (⌀ or R stay as they are)")
        self.after = QtWidgets.QLineEdit(settings["after"])
        self.after.setPlaceholderText("e.g.  THRU  or  H7")
        self.decimals = QtWidgets.QSpinBox()
        self.decimals.setRange(0, 6)
        self.decimals.setValue(settings["decimals"])
        self.tol = QtWidgets.QComboBox()
        self.tol.addItem("No tolerance", "none")
        self.tol.addItem("Symmetric  ±", "symmetric")
        self.tol.addItem("Deviation  +upper / −lower", "deviation")
        self.tol.setCurrentIndex(["none", "symmetric", "deviation"].index(settings["tol"]))
        self.upper = QtWidgets.QDoubleSpinBox()
        self.lower = QtWidgets.QDoubleSpinBox()
        for sb, v in ((self.upper, settings["upper"]), (self.lower, settings["lower"])):
            sb.setDecimals(4)
            sb.setRange(-1000, 1000)
            sb.setSingleStep(0.01)
            sb.setValue(v)
        self.basic = QtWidgets.QCheckBox("Basic (boxed, theoretically exact) - e.g. locating "
                                         "holes under a position tolerance")
        self.basic.setChecked(settings["basic"])
        self.reference = QtWidgets.QCheckBox("Reference (in parentheses)")
        self.reference.setChecked(settings["reference"])
        form.addRow("Text before", self.before)
        form.addRow("Text after", self.after)
        form.addRow("Decimals", self.decimals)
        form.addRow("Tolerance", self.tol)
        form.addRow("Upper (or ±)", self.upper)
        form.addRow("Lower", self.lower)
        form.addRow(self.basic)
        form.addRow(self.reference)

        def sync(*_):
            basic = self.basic.isChecked()
            t = self.tol.currentData()
            self.tol.setEnabled(not basic)
            self.upper.setEnabled(not basic and t != "none")
            self.lower.setEnabled(not basic and t == "deviation")
        self.basic.toggled.connect(sync)
        self.tol.currentIndexChanged.connect(sync)
        sync()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def choice(self):
        return {"before": self.before.text(), "after": self.after.text(),
                "decimals": self.decimals.value(), "tol": self.tol.currentData(),
                "upper": self.upper.value(), "lower": self.lower.value(),
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
        dlg = DimensionDialog(dimsmod.read_dimension(dims[0]), Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        c = dlg.choice()
        doc = App.ActiveDocument
        run_in_transaction(doc, "DrawingKit edit dimension",
                           lambda: [dimsmod.set_dimension(d, **c) for d in dims])
        doc.recompute()
        say("%d dimension(s) updated" % len(dims))


COMMANDS = [
    ("DrawingKit_NewSheet", NewSheet()),
    ("DrawingKit_StandardViews", StandardViews()),
    ("DrawingKit_ProjectedView", ProjectedView()),
    ("DrawingKit_Section", Section()),
    ("DrawingKit_AlignedSection", AlignedSection()),
    ("DrawingKit_DetailView", DetailView()),
    ("DrawingKit_AuxView", AuxiliaryView()),
    ("DrawingKit_CenterMarks", CenterMarks()),
    ("DrawingKit_HoleCallout", HoleCallout()),
    ("DrawingKit_EditDimension", EditDimension()),
    ("DrawingKit_Datum", DatumFlag()),
    ("DrawingKit_DatumTarget", DatumTarget()),
    ("DrawingKit_ToleranceFrame", ToleranceFrame()),
    ("DrawingKit_HoleTable", HoleTable()),
    ("DrawingKit_FillTitleBlock", FillTitleBlock()),
    ("DrawingKit_Revision", AddRevision()),
    ("DrawingKit_FitToPage", FitToPage()),
    ("DrawingKit_SpeedToggle", SpeedToggle()),
]

for _name, _cmd in COMMANDS:
    Gui.addCommand(_name, _cmd)

# toolbars in the DrawingKit workbench
COMMAND_GROUPS = [
    ("DrawingKit views", ["DrawingKit_NewSheet", "DrawingKit_StandardViews",
                          "DrawingKit_ProjectedView", "DrawingKit_Section",
                          "DrawingKit_AlignedSection", "DrawingKit_DetailView",
                          "DrawingKit_AuxView", "DrawingKit_FitToPage", "DrawingKit_SpeedToggle"]),
    ("DrawingKit annotation", ["DrawingKit_CenterMarks", "DrawingKit_HoleCallout",
                               "DrawingKit_EditDimension",
                               "DrawingKit_Datum", "DrawingKit_DatumTarget",
                               "DrawingKit_ToleranceFrame"]),
    ("DrawingKit tables", ["DrawingKit_HoleTable", "DrawingKit_FillTitleBlock",
                           "DrawingKit_Revision"]),
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
