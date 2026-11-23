# SPDX-License-Identifier: LGPL-2.1-or-later
"""Draw2D toolbar commands (GUI only)."""

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
    App.Console.PrintMessage("Draw2D: %s\n" % text)
    try:
        Gui.getMainWindow().statusBar().showMessage("Draw2D: " + text, 8000)
    except Exception:
        pass


def warn(text):
    App.Console.PrintWarning("Draw2D: %s\n" % text)
    QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Draw2D", text)


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
    """For a few seconds after a Draw2D action, redraw views that came out
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
            App.Console.PrintWarning("Draw2D redraw check: %s\n" % e)
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
    """Every paper size Draw2D can make, in both orientations."""
    from . import templates as d2t
    return {(s, o): None for s in d2t.SIZES for o in d2t.ORIENTATIONS}


class SheetDialog(QtWidgets.QDialog):
    """Pick a sheet size and orientation."""

    last_size = "A3"
    last_orient = "Landscape"

    def __init__(self, templates, parent=None):
        super().__init__(parent)
        self.templates = templates
        self.setWindowTitle("Draw2D - New drawing sheet")
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
    box.setWindowTitle("Draw2D - Standard views")
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
        return {"Pixmap": icon("Draw2D_NewSheet.svg"),
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

        page = run_in_transaction(doc, "Draw2D new sheet", work)
        doc.recompute()
        show_page(page)
        watch_redraw(page)
        msg = "%s %s sheet created" % (size, orient.lower())
        if add_views and parts:
            msg += ", views at %s" % core.scale_text(page.Scale)
        say(msg)


class StandardViews:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_StandardViews.svg"),
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
                doc, "Draw2D new sheet",
                lambda: core.create_page(doc, size, orient))
        else:
            iso = ask_isometric()
            if iso is None:
                return
        run_in_transaction(doc, "Draw2D standard views",
                           lambda: core.add_standard_views(page, parts, iso))
        doc.recompute()
        show_page(page)
        watch_redraw(page)
        say("Standard views added at %s" % core.scale_text(page.Scale))


class FitToPage:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_FitToPage.svg"),
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
            scale = run_in_transaction(doc, "Draw2D fit to page",
                                       lambda: core.fit_page(page))
        except ValueError as e:
            warn(str(e))
            return
        doc.recompute()
        watch_redraw(page)
        say("Page scale is now %s" % core.scale_text(scale))


class Section:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_Section.svg"),
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
                    doc, "Draw2D section",
                    lambda: core.add_cut_section(page, base, cut, places[-1]))
                doc.recompute()
                watch_redraw(page)
                say("%s added" % sec.Label)

            interact.PagePicker(page, "Draw2D: click where the section view goes "
                                      "(Esc to cancel)",
                                on_move=on_move_place, on_click=lambda pt, pts: True,
                                on_done=on_place_done, finish_on_right_click=False)

        interact.PagePicker(page, "Draw2D: click the cut line on the view (one point = "
                                  "straight cut, more = stepped). Right-click or Enter "
                                  "when done, Esc to cancel.",
                            on_move=on_move_cut, on_click=lambda pt, pts: False,
                            on_done=lambda pts: QtCore.QTimer.singleShot(
                                0, lambda: on_cut_done(pts)))


class SpeedToggle:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_Speed.svg"),
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
        run_in_transaction(doc, "Draw2D fast editing",
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
        return {"Pixmap": icon("Draw2D_ProjectedView.svg"),
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
                    doc, "Draw2D projected view",
                    lambda: core.add_projected_view(page, base, points[-1]))
            except ValueError as e:
                warn(str(e))
            else:
                doc.recompute()
                watch_redraw(page)
                say("%s added" % view.Label)
            # keep going from the same base view until Esc
            QtCore.QTimer.singleShot(0, lambda: self._pick(page, base))

        interact.PagePicker(page, "Draw2D: click where the projected view goes "
                                  "(Esc to stop)",
                            on_move=on_move, on_click=lambda pt, pts: True,
                            on_done=on_done, finish_on_right_click=False)


class CenterMarks:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_CenterMarks.svg"),
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
        total = run_in_transaction(doc, "Draw2D centre marks",
                                   lambda: sum(core.add_center_marks(v) for v in views))
        say("%d centre line(s) on %d view(s)" % (total, len(views)))


COMMANDS = [
    ("Draw2D_NewSheet", NewSheet()),
    ("Draw2D_StandardViews", StandardViews()),
    ("Draw2D_ProjectedView", ProjectedView()),
    ("Draw2D_Section", Section()),
    ("Draw2D_CenterMarks", CenterMarks()),
    ("Draw2D_FitToPage", FitToPage()),
    ("Draw2D_SpeedToggle", SpeedToggle()),
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
            App.Console.PrintWarning("Draw2D alignment: %s\n" % e)


# This module is imported once per FreeCAD session, so one observer.
ALIGNMENT_OBSERVER = AlignmentObserver()
App.addDocumentObserver(ALIGNMENT_OBSERVER)

try:
    from . import interact as _interact
    _interact.start_watching()
except Exception as _e:  # drag-anywhere is a convenience; never block loading
    App.Console.PrintWarning("Draw2D: drag-anywhere not available: %s\n" % _e)

COMMAND_NAMES = [n for n, _ in COMMANDS]
