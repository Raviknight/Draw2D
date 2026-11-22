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
    """Open (or bring forward) the page's tab."""
    try:
        page.ViewObject.doubleClicked()
        Gui.updateGui()
    except Exception:
        pass
    view = Gui.activeView()
    if view is not None and hasattr(view, "getPage"):
        try:
            if view.getPage() == page:
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
        templates = core.available_templates()
        if not templates:
            warn("No TechDraw templates found in %s" % core.template_dir())
            return
        parts = selected_parts()
        dlg = SheetDialog(templates, Gui.getMainWindow())
        if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
            return
        size, orient, add_views, iso = dlg.choice()

        def work():
            page = core.create_page(doc, size, orient, templates)
            if add_views and parts:
                core.add_standard_views(page, parts, iso)
            return page

        page = run_in_transaction(doc, "Draw2D new sheet", work)
        doc.recompute()
        show_page(page)
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
            templates = core.available_templates()
            dlg = SheetDialog(templates, Gui.getMainWindow())
            dlg.views.setChecked(True)
            dlg.views.setEnabled(False)
            if not (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()):
                return
            size, orient, _, iso = dlg.choice()
            page = run_in_transaction(
                doc, "Draw2D new sheet",
                lambda: core.create_page(doc, size, orient, templates))
        else:
            iso = ask_isometric()
            if iso is None:
                return
        run_in_transaction(doc, "Draw2D standard views",
                           lambda: core.add_standard_views(page, parts, iso))
        doc.recompute()
        show_page(page)
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
        say("Page scale is now %s" % core.scale_text(scale))


class Section:
    def GetResources(self):
        return {"Pixmap": icon("Draw2D_Section.svg"),
                "MenuText": "Section, auto-lettered",
                "ToolTip": "Select a view on the page. Adds a section through the "
                           "middle of the part with the next free letter (A-A, B-B...)."
                           " Edit it afterwards by double-clicking the section."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        doc = App.ActiveDocument
        base = selected_part_view()
        if base is None:
            warn("Select the view to cut (e.g. the front view) on the page first.")
            return
        page = parent_page(base)
        directions = ["Right", "Left", "Up", "Down"]
        direction, ok = QtWidgets.QInputDialog.getItem(
            Gui.getMainWindow(), "Draw2D - Section",
            "Look direction (arrows point this way):", directions, 0, False)
        if not ok:
            return
        sec = run_in_transaction(doc, "Draw2D section",
                                 lambda: core.add_section(page, base, direction=direction))
        doc.recompute()
        say("%s added" % sec.Label)


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
        say("Fast editing %s for %s" % ("ON" if fast else "OFF (redrawn)", page.Label))


COMMANDS = [
    ("Draw2D_NewSheet", NewSheet()),
    ("Draw2D_StandardViews", StandardViews()),
    ("Draw2D_FitToPage", FitToPage()),
    ("Draw2D_Section", Section()),
    ("Draw2D_SpeedToggle", SpeedToggle()),
]

for _name, _cmd in COMMANDS:
    Gui.addCommand(_name, _cmd)

COMMAND_NAMES = [n for n, _ in COMMANDS]
