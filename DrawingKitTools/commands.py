# SPDX-License-Identifier: LGPL-2.1-or-later
"""DrawingKit toolbar commands (GUI only)."""

import math
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
    try:
        from . import lineweights           # new views take the sheet's line weights
        if lineweights.weights():
            for v in core.part_views(page):
                lineweights.style(v)
    except Exception:
        pass
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
    last_block = None

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
        # a company can have several title blocks: pick one per sheet
        from . import blocks, titleblock
        own = sorted(((k, d["name"]) for k, (d, _p, _w) in blocks.designs().items()),
                     key=lambda kv: kv[1].lower())
        self.block = None
        if own:
            items = [(None, "Title block: default"), ("iso", "ISO"), ("ansi", "ANSI")]
            items += [("custom:" + k, name) for k, name in own]
            self.block = ui.combo(items, SheetDialog.last_block, 200)
            self.block.setToolTip("Title block for this sheet (default: as set in the Title "
                                  "block form for new sheets, now %s)"
                                  % titleblock.style().replace("custom:", ""))
            self.add_row(self.block, stretch=True)
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
        SheetDialog.last_block = self.block.currentData() if self.block is not None else None
        return (self.size.currentData(), orient, self.views.isChecked(), self.iso.isChecked())

    def title_style(self):
        return self.block.currentData() if self.block is not None else None


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
        block = dlg.title_style()

        def work():
            page = core.create_page(doc, size, orient, style=block)
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
            block = dlg.title_style()
            page = run_in_transaction(
                doc, "DrawingKit new sheet",
                lambda: core.create_page(doc, size, orient, style=block))
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
            def work():
                core.fit_page(page)
                core.make_room_for_tables(page)   # room for dimensions and callouts
                return core.view_scale(core.page_content(page)[0])
            scale = run_in_transaction(doc, "DrawingKit fit to page", work)
        except ValueError as e:
            warn(str(e))
            return
        doc.recompute()
        watch_redraw(page)
        say("Page scale is now %s" % core.scale_text(scale))


def selected_detail():
    """The selected view with a scale of its own (a detail, a section or a
    view outside the projection group), if any."""
    for obj in Gui.Selection.getSelection():
        if core.has_own_scale(obj):
            return obj
    return None


def _rescale(name, page_work, detail_work):
    """Run a scale change on the selected detail view, else on the page."""
    doc = App.ActiveDocument
    det = selected_detail()
    page = parent_page(det) if det is not None else target_page()
    if page is None:
        warn("Select the page (or a view on it) first.")
        return
    try:
        if det is not None:
            scale = run_in_transaction(doc, name, lambda: detail_work(det))
        else:
            scale = run_in_transaction(doc, name, lambda: page_work(page))
    except ValueError as e:
        warn(str(e))
        return
    doc.recompute()
    watch_redraw(page)
    return det, page, scale


class ScaleStep:
    def __init__(self, step):
        self.step = step

    def GetResources(self):
        up = self.step > 0
        return {"Pixmap": icon("DrawingKit_ScaleUp.svg" if up else "DrawingKit_ScaleDown.svg"),
                "MenuText": "Scale up" if up else "Scale down",
                "ToolTip": ("One standard scale %s (e.g. %s) for the whole page, laid "
                            "out again like Fit to page; detail views keep their zoom. "
                            "With a detail, section or auxiliary view selected, only "
                            "that view changes."
                            % (("bigger", "1:2 -> 1:1 -> 2:1") if up else
                               ("smaller", "2:1 -> 1:1 -> 1:2")))}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        done = _rescale("DrawingKit scale %s" % ("up" if self.step > 0 else "down"),
                        lambda p: core.step_page_scale(p, self.step),
                        lambda d: core.step_view_scale(d, self.step))
        if done:
            det, page, scale = done
            say("%s is now at %s" % (det.Label if det else "The page", core.scale_text(scale)))


class SetScaleDialog(ui.Popover):
    def __init__(self, current, detail=False, parent=None):
        super().__init__("Scale of the view" if detail else "Page scale", parent)
        self.value = ui.field(current, 70, "1:3", "Any scale: 1:3, 3:4, 2.5:1, 0.8 or 150%")
        self.add_row("Scale", self.value, stretch=True)
        self.fill = None
        if not detail:
            self.fill = ui.toggle("Fill sheet (NTS)", False,
                                  "Zoom the views to fill the drawing area at whatever "
                                  "scale that takes; the title block says NTS")
            self.fill.toggled.connect(lambda on: self.value.setEnabled(not on))
            self.add_row(self.fill, stretch=True)


class SetScale:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_SetScale.svg"),
                "MenuText": "Set scale...",
                "ToolTip": "Type any scale, standard or not (1:3, 3:4, 0.8, 150%), "
                           "or fill the sheet (title block says NTS). Applies to the "
                           "page, or to the selected detail, section or auxiliary view."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        det = selected_detail()
        page = parent_page(det) if det is not None else target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        if det is not None:
            current = core.scale_text(core.view_scale(det))
        else:
            group, _ = core.page_content(page)
            current = core.scale_text(core.view_scale(group) if group else float(page.Scale))
        dlg = SetScaleDialog(current, det is not None, Gui.getMainWindow())
        if not dlg.run():
            return
        fill = dlg.fill is not None and dlg.fill.isChecked()
        try:
            value, label = (None, "NTS") if fill else core.parse_scale(dlg.value.text())
        except ValueError as e:
            warn(str(e))
            return
        if fill:
            done = _rescale("DrawingKit fill sheet", core.fill_page, None)
        else:
            done = _rescale("DrawingKit set scale",
                            lambda p: core.set_page_scale(p, value, label),
                            lambda d: core.set_view_scale(d, value))
        if done:
            det, page, scale = done
            say("%s is now at %s%s" % (det.Label if det else "The page",
                                       label if not fill else core.scale_text(scale),
                                       " (NTS)" if fill else ""))


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


def hatch_when_drawn(page, sec, tries=60, every_ms=250):
    """Hatch an aligned section by part once TechDraw has drawn it (it
    draws in the background; the hatch is matched to the drawing)."""
    from . import hatching

    def attempt(left):
        try:
            if core.section_stale_factor(sec) != 1.0:   # not drawn, or at an old scale
                if left > 0:
                    QtCore.QTimer.singleShot(every_ms, lambda: attempt(left - 1))
                return
            sym, parts = hatching.hatch_by_part(page, sec)
            # now TechDraw has drawn it, line the section up with its base
            core.realign_sections(page, [sec])
            from . import annot
            annot.refresh_page(page)
            page.Document.recompute()
            if sym is not None:
                say("%s hatched part by part" % sec.Label)
        except Exception as e:
            App.Console.PrintWarning("DrawingKit hatching: %s\n" % e)
    QtCore.QTimer.singleShot(every_ms, lambda: attempt(tries))


class DrawingStandard:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Standard.svg"),
                "MenuText": "Drawing standard (ISO / ASME)",
                "ToolTip": "One standard for DrawingKit: section and detail captions, the "
                           "projection of new sheets, dimension style, which GD&T symbols the "
                           "frame offers and the weld line. Auto follows TechDraw's dimension "
                           "standard preference."}

    def IsActive(self):
        return True

    def Activated(self):
        from . import standards
        pop = ui.Popover("Drawing standard")
        choices = [(v, lab if v != "auto" else "Auto (TechDraw: %s)" % standards.techdraw_standard())
                   for v, lab in standards.CHOICES]
        combo = ui.combo(choices, standards.setting(), 170)
        page = None
        try:
            page = target_page()
        except Exception:
            page = None
        apply_now = ui.toggle("Update this sheet", page is not None,
                              "Bring this sheet's captions, weld lines and dimension style in line")
        apply_now.setEnabled(page is not None)
        pop.add_row(combo, apply_now, stretch=True)
        if not pop.run():
            return
        standards.set_setting(combo.currentData())
        msg = "Drawing standard: %s" % standards.current()
        if page is not None and apply_now.isChecked():
            doc = page.Document
            n = run_in_transaction(doc, "DrawingKit drawing standard",
                                   lambda: standards.apply_to_page(page))
            doc.recompute()
            msg += " (%d item(s) on %s updated)" % (n, page.Label)
        say(msg)


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
        pop = ui.Popover("Hatch by part")
        by_mat = ui.toggle("Patterns by material", hatching.material_patterns_on(),
                           "Off (standard): one general hatch for all, as ASME Y14.2 and ISO 128 "
                           "do. On: steel double lines, brass solid/dashed, aluminium cross, "
                           "plastics dashed.")
        pop.add_row(by_mat, stretch=True)
        if not pop.run():
            return
        hatching.set_material_patterns(by_mat.isChecked())
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


class EditHatch:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_EditHatch.svg"),
                "MenuText": "Edit a part's hatch",
                "ToolTip": "Click a part in a section hatched by part: change its hatch "
                           "angle, spacing or pattern, or switch its hatching off (or on, "
                           "e.g. for a pin). Kept when the hatch is redone."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot, hatching
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        syms = [v for v in page.Views if getattr(v, annot.KIND, "") == "hatch"]
        if not syms:
            warn("No section on this page is hatched by part yet (use Hatch by part first).")
            return
        show_page(page)
        Gui.updateGui()
        doc = page.Document

        def done(pt):
            for sym in syms:
                part = hatching.part_at(sym, pt[0], pt[1])
                if part is not None:
                    break
            else:
                warn("Click inside a part's cut face in the section.")
                return
            cur = annot._params(sym).get("parts", {}).get(part.Name, {})
            pop = ui.Popover("Hatch: %s" % part.Label)
            on = ui.toggle("Hatched", not cur.get("skip", False), "Off: draw this part uncut")
            angle = ui.field("%g" % cur.get("angle", 45), 40, "45", "Angle (degrees)")
            spacing = ui.field("%g" % cur.get("spacing", 2.5), 40, "2.5", "Spacing on paper (mm)")
            pattern = ui.combo(hatching.PATTERNS, cur.get("pattern", "single"), 110)
            reset = ui.toggle("Reset", False, "Back to the automatic hatch for this part")
            pop.add_row(on, angle, "°", spacing, "mm", stretch=True)
            pop.add_row(pattern, reset, stretch=True)
            if not pop.run():
                return
            if reset.isChecked():
                choice = {"angle": None, "spacing": None, "pattern": None, "off": None}
            else:
                choice = {"angle": _num(angle.text()) or cur.get("angle", 45),
                          "spacing": max(0.5, _num(spacing.text()) or cur.get("spacing", 2.5)),
                          "pattern": pattern.currentData(), "off": not on.isChecked()}

            def work():
                hatching.set_override(sym, part.Name, **choice)
                return hatching.hatch_by_part(page, getattr(sym, annot.LINK))
            run_in_transaction(doc, "DrawingKit edit hatch", work)
            doc.recompute()
            say("Hatch of %s updated" % part.Label)

        _one_click(page, "Click a part in the hatched section.", lambda pt: [], done)


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
            try:
                interact.place_detail_letter(det)     # its letter clear of the lines
            except Exception as e:
                App.Console.PrintWarning("DrawingKit: detail letter: %s\n" % e)
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


MOD_LABELS = {"": " · ", "MMC": "Ⓜ", "LMC": "Ⓛ", "RFS": "Ⓢ", "TR": "▷"}


def _mod_button(values, value, tip):
    return ui.Cycle(values, [MOD_LABELS[v] for v in values], value if value in values else "", tip)


class _SegmentRow:
    """One frame row: symbol | ⌀ tol Ⓜ | A Ⓜ | B Ⓜ | C Ⓜ  (the modifiers
    offered follow the drawing standard, ISO or ASME)"""

    def __init__(self, seg, show_char=True):
        from . import gdt, standards
        items = []
        for k, lab in gdt.CHARACTERISTICS:
            note = standards.characteristic_note(k)
            items.append((k, "%s  %s%s" % (GDT_SYMBOLS.get(k, ""), lab, ("  (%s)" % note) if note else "")))
        self.char = ui.combo(items, seg.get("char", "position"), 150)
        if not show_char:
            # never setVisible(True) here: the combo has no parent yet, so it
            # would open as its own window and close the pop-up at once
            self.char.setVisible(False)
        self.dia = ui.toggle("⌀", seg.get("dia", True), "Diameter (cylindrical) zone")
        self.tol = ui.field(seg.get("tol", "0.05"), 52, tip="Tolerance")
        tm = standards.tolerance_modifiers()
        self.mod = _mod_button(tm, seg.get("mod", ""), "Click to change: " + " / ".join(
            MOD_LABELS[v].strip() or "none" for v in tm))
        self.datums, self.dmods = [], []
        for i in range(3):
            d = ui.field((seg.get("datums") or ["", "", ""])[i], 26, "–",
                         ("Primary", "Secondary", "Tertiary")[i] + " datum")
            d.setMaxLength(3)
            self.datums.append(d)
            dm = standards.datum_modifiers()
            self.dmods.append(_mod_button(dm, (seg.get("datum_mods") or ["", "", ""])[i],
                                          "Click to change: " + " / ".join(
                                              MOD_LABELS[v].strip() or "none" for v in dm)))

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
        from . import standards
        self.std = standards.current()
        super().__init__("Tolerance frame (%s)" % self.std, parent)
        self.row1 = _SegmentRow(self.last)
        self.add_row(*self.row1.widgets())
        self.count = ui.field(count or self.last.get("count", ""), 40, "6X",
                              "Count above the frame (filled in when you click one of several holes)")
        self.more = ui.toggle("+ Segment", False, "Second row: composite or stacked frame")
        self.opts = ui.toggle("Modifiers…", False,
                              "Zone modifiers, all around, between" +
                              (", CF" if standards.has_cf() else ""))
        self.add_row("Count", self.count, self.more, self.opts, stretch=True)
        # zone modifiers and leader symbols, hidden until asked for
        last = self.last
        zones = last.get("zones") or []
        self.z = {}
        tips = dict(ZONE_TIPS)
        labels = {"U": "UZ" if self.std == "ISO" else "Ⓤ", "CZ": "CZ", "P": "Ⓟ", "F": "Ⓕ",
                  "T": "Ⓣ", "DP": "△", "ST": "ST"}
        offered = standards.frame_zones()
        for code in offered:
            tip = tips[code]
            if code == "U" and self.std == "ISO":
                tip = "UZ: unequal zone (ISO 1101), offset in the next box (+ outwards)"
            self.z[code] = ui.toggle(labels[code], code in zones, tip)
        self.uval = ui.field(last.get("uval", ""), 34, "0.1", "Offset of the profile zone (+ outwards)")
        self.proj = ui.field(last.get("proj", ""), 34, "15", "Ⓟ projected zone height")
        row = []
        for code in offered:
            row.append(self.z[code])
            if code == "U":
                row.append(self.uval)
            if code == "P":
                row.append(self.proj)
        self.zrow = self.add_row(*row, stretch=True)
        self.around = ui.toggle("○ All around", last.get("all_around", False),
                                "All around: the tolerance applies to the whole outline in this view")
        b = last.get("between") or ["", ""]
        self.b1 = ui.field(b[0], 26, "J", "Between: from point")
        self.b2 = ui.field(b[1], 26, "K", "Between: to point")
        lrow = [self.around, "Between", self.b1, "↔", self.b2]
        self.cf = None
        if standards.has_cf():
            self.cf = ui.toggle("CF", last.get("cf", False),
                                "Continuous feature (ASME): interrupted surfaces treated as one")
            lrow.append(self.cf)
        self.lrow = self.add_row(*lrow, stretch=True)
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
        first["std"] = self.std
        first["zones"] = [code for code, b in self.z.items() if b.isChecked()]
        if "U" in first["zones"]:
            first["uval"] = self.uval.text().strip() or "0.1"
        if "P" in first["zones"] and self.proj.text().strip():
            first["proj"] = self.proj.text().strip()
        c = dict(first)
        c["count"] = self.count.text().strip()
        c["all_around"] = self.around.isChecked()
        c["cf"] = bool(self.cf is not None and self.cf.isChecked())
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
                           "dimension first; to hang it from the feature's tolerance frame "
                           "(ASME), select the frame first or click on the frame's edge; to "
                           "attach an existing datum, select it and the frame. Follows the "
                           "view; lettered A, B, C..."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot
        dims = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewDimension")]
        frames = [o for o in Gui.Selection.getSelection()
                  if getattr(o, annot.KIND, "") == "frame"]
        page = parent_page(frames[0]) if frames else (parent_page(dims[0]) if dims else target_page())
        if page is None:
            warn("Open the page (or select a view on it) first.")
            return
        datums = [o for o in Gui.Selection.getSelection() if getattr(o, annot.KIND, "") == "datum"]
        if frames and datums:
            # an existing datum and a frame: hang the datum from the frame
            doc = page.Document
            run_in_transaction(doc, "DrawingKit datum on frame",
                               lambda: [annot.hang_from_frame(d, frames[0]) for d in datums])
            doc.recompute()
            say("%s now hang%s from %s and move%s with it" % (
                ", ".join(d.Label for d in datums), "" if len(datums) > 1 else "s",
                frames[0].Label, "" if len(datums) > 1 else "s"))
            return
        letter = annot.next_datum_letter(page.Document)
        letter = ui.ask_text("Datum", "Letter", letter, 34)
        if not letter or not letter.strip():
            return
        doc = page.Document
        if frames:
            sym = run_in_transaction(doc, "DrawingKit datum",
                                     lambda: annot.add_datum(page, 0, 0, letter, frame=frames[0]))
            doc.recompute()
            say("%s hangs from %s; it moves with the frame" % (sym.Label, frames[0].Label))
            return
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
                hatch_when_drawn(page, sec)

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


def redraw_template(page):
    """Make the open sheet show its template (and the texts in it) now."""
    try:
        tpl = page.Template
        tpl.touch()
        tpl.recompute()
        page.touch()
        page.recompute()
        vo = getattr(tpl, "ViewObject", None)
        if vo is not None and hasattr(vo, "update"):
            vo.update()
        Gui.updateGui()
    except Exception:
        pass


class ToleranceDialog(QtWidgets.QDialog):
    """General tolerances by size range: an ISO 2768 class or your own
    rows (over, up to, ±) for lengths and for angles."""

    def __init__(self, data, units="mm", parent=None):
        from . import tables
        super().__init__(parent or Gui.getMainWindow())
        self.tables = tables
        self.setWindowTitle("General tolerances by size")
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Start from:"))
        self.preset = QtWidgets.QComboBox()
        self.preset.addItem("(keep the rows below)", "")
        for cls, text in tables.TOL_PRESETS:
            self.preset.addItem(text, cls)
        self.preset.activated.connect(self.load_preset)
        top.addWidget(self.preset)
        top.addStretch(1)
        lay.addLayout(top)
        self.name = QtWidgets.QLineEdit((data or {}).get("preset", ""))
        self.name.setPlaceholderText("e.g. ISO 2768-m, or your company standard")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Shown as:"))
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.lin = self._grid(["Over (%s)" % units, "Up to (%s)" % units, "±"],
                              (data or {}).get("linear", []))
        self.ang = self._grid(["Side over (%s)" % units, "Up to (%s)" % units, "±"],
                              (data or {}).get("angular", []))
        d = data or {}
        self.ang_mode = QtWidgets.QComboBox()
        for val, text in (("size", "By size (rows below)"), ("all", "One tolerance for all angles"),
                          ("none", "None in this table")):
            self.ang_mode.addItem(text, val)
        mode = "all" if d.get("angle_all") else ("size" if d.get("angular") or data is None else "none")
        self.ang_all = QtWidgets.QLineEdit(d.get("angle_all", "") or "1°")
        self.ang_all.setPlaceholderText("e.g. 1° or 0°30'")
        self.ang_all.setMaximumWidth(90)
        for title, grid in (("Lengths", self.lin), ("Angles", self.ang)):
            lay.addWidget(QtWidgets.QLabel("<b>%s</b>" % title))
            if grid is self.ang:
                arow = QtWidgets.QHBoxLayout()
                arow.addWidget(self.ang_mode)
                arow.addWidget(QtWidgets.QLabel("±"))
                arow.addWidget(self.ang_all)
                arow.addStretch(1)
                lay.addLayout(arow)
            lay.addWidget(grid)
            btns = QtWidgets.QHBoxLayout()
            if grid is self.ang:
                self.ang_btns = btns
            add = QtWidgets.QPushButton("Add row")
            add.clicked.connect(lambda _=False, g=grid: g.insertRow(g.rowCount()))
            rem = QtWidgets.QPushButton("Remove row")
            rem.clicked.connect(lambda _=False, g=grid: g.removeRow(max(0, g.currentRow()))
                                if g.rowCount() else None)
            btns.addWidget(add)
            btns.addWidget(rem)
            btns.addStretch(1)
            lay.addLayout(btns)
        def ang_sync(*_):
            m = self.ang_mode.currentData()
            self.ang.setVisible(m == "size")
            for i in range(self.ang_btns.count()):
                w = self.ang_btns.itemAt(i).widget()
                if w is not None:
                    w.setVisible(m == "size")
            self.ang_all.setVisible(m == "all")
        self.ang_mode.currentIndexChanged.connect(ang_sync)
        self.ang_mode.setCurrentIndex(self.ang_mode.findData(mode))
        ang_sync()
        note = QtWidgets.QLabel("Leave the rows empty for no table on the sheet. Save it for every "
                                "drawing with \"Save * as my defaults\" or \"to company file\".")
        note.setWordWrap(True)
        lay.addWidget(note)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(460, 560)

    def _grid(self, headers, rows):
        g = QtWidgets.QTableWidget(len(rows), 3)
        g.setHorizontalHeaderLabels(headers)
        g.horizontalHeader().setStretchLastSection(True)
        g.verticalHeader().setVisible(False)
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                g.setItem(i, j, QtWidgets.QTableWidgetItem(str(v)))
        return g

    def _fill(self, grid, rows):
        grid.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                grid.setItem(i, j, QtWidgets.QTableWidgetItem(str(v)))

    def load_preset(self, _i):
        cls = self.preset.currentData()
        if not cls:
            return
        d = self.tables.iso2768(cls)
        self._fill(self.lin, d["linear"])
        self._fill(self.ang, d["angular"])
        self.name.setText(d["preset"])
        self.ang_mode.setCurrentIndex(self.ang_mode.findData("size"))

    def _rows(self, grid):
        out = []
        for i in range(grid.rowCount()):
            cells = [(grid.item(i, j).text().strip() if grid.item(i, j) else "") for j in range(3)]
            if cells[2]:
                out.append(cells)
        return out

    def data(self):
        mode = self.ang_mode.currentData()
        lin = self._rows(self.lin)
        ang = self._rows(self.ang) if mode == "size" else []
        ang_all = self.ang_all.text().strip() if mode == "all" else ""
        if not lin and not ang and not ang_all:
            return None
        return {"preset": self.name.text().strip(), "linear": lin, "angular": ang,
                "angle_all": ang_all}


def layout_key(page):
    """'iso', 'iso_tol', 'ansi' or 'custom:<design>' of a page's title block."""
    from . import titleblock as tb
    st = tb.page_style(page)
    if st.startswith("custom:"):
        return st
    return "ansi" if tb.is_ansi(page) else ("iso_tol" if tb.has_tolerance_row(page) else "iso")


class TitleBlockDialog(QtWidgets.QDialog):
    """Every field of a page's title block in one form, with saving the
    fields that are the same on every drawing as your defaults or to a
    company file."""

    def __init__(self, page, parent=None):
        from . import titleblock as tb
        super().__init__(parent or Gui.getMainWindow())
        self.tb, self.page = tb, page
        self.setWindowTitle("Title block - %s" % page.Label)
        lay = QtWidgets.QVBoxLayout(self)
        grid = QtWidgets.QGridLayout()
        self.edits = {}
        # in-block tolerance rows are edited with "Tolerances by size..."
        rows = [r for r in tb.fields(page) if not r[0].startswith("tolsize_")]
        half = (len(rows) + 1) // 2
        for i, (key, label, value) in enumerate(rows):
            col, row = (0, i) if i < half else (2, i - half)
            lab = QtWidgets.QLabel(label + (" *" if key in tb.SHARED else ""))
            if key in tb.SHARED:
                lab.setToolTip("Same on every drawing: saved with your defaults")
            e = QtWidgets.QLineEdit(value)
            e.setMinimumWidth(240)
            if key in ("scale", "sheet_number"):
                e.setToolTip("Set by DrawingKit (scale, sheet numbers)")
            grid.addWidget(lab, row, col)
            grid.addWidget(e, row, col + 1)
            self.edits[key] = e
        lay.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Layout of this sheet:"))
        from . import blocks
        own = sorted(((k, d["name"]) for k, (d, _p, _w) in blocks.designs().items()),
                     key=lambda kv: kv[1].lower())
        self.layout_combo = QtWidgets.QComboBox()
        for val, text in (("iso", "ISO"), ("iso_tol", "ISO + tolerance row"),
                          ("ansi", "ANSI (tolerance block, checked by, size)")):
            self.layout_combo.addItem(text, val)
        for k, name in own:
            self.layout_combo.addItem(name, "custom:" + k)
        cur = layout_key(page)
        if self.layout_combo.findData(cur) < 0:
            self.layout_combo.addItem(cur.replace("custom:", "") + " (not found)", cur)
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(cur))
        self.layout_combo.setToolTip("Swaps the title block at once, keeping what you typed")
        self.switch_to = None

        def switch(_i):
            # apply now: the form closes and opens again on the new layout
            self.switch_to = self.layout_combo.currentData()
            self.done(2)
        self.layout_combo.currentIndexChanged.connect(switch)
        row.addWidget(self.layout_combo)
        design = QtWidgets.QPushButton("Design title blocks...")
        design.setToolTip("Draw your own title block: cells for fields, fixed text, your logo, "
                          "the tolerances by size, the projection symbol")
        design.clicked.connect(self.open_designer)
        row.addWidget(design)
        row.addSpacing(16)
        row.addWidget(QtWidgets.QLabel("New sheets:"))
        self.new_combo = QtWidgets.QComboBox()
        for val, text in (("auto", "Auto (ANSI for ASME)"), ("ISO", "ISO"), ("ANSI", "ANSI")):
            self.new_combo.addItem(text, val)
        for k, name in own:
            self.new_combo.addItem(name, "custom:" + k)
        try:
            pref = App.ParamGet(tb.PREFS).GetString(tb.STYLE_PREF, "auto") or "auto"
        except Exception:
            pref = "auto"
        self.new_combo.setCurrentIndex(max(0, self.new_combo.findData(pref)))
        row.addWidget(self.new_combo)
        row.addStretch(1)
        lay.addLayout(row)
        # general tolerances by size range (a table on the sheet)
        from . import tables
        self.tol_table = tb.block_tolerance_data(page) or tables.tolerance_data(page)
        self.tol_changed = False
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(QtWidgets.QLabel("General tolerances by size *:"))
        self.tol_label = QtWidgets.QLabel()
        trow.addWidget(self.tol_label, 1)
        tol_btn = QtWidgets.QPushButton("Tolerances by size...")
        tol_btn.setToolTip("A table of tolerances by size range (e.g. ISO 2768-m: 0.5-3 ±0.1, "
                           "3-6 ±0.1, 6-30 ±0.2...) above the title block - the usual way on "
                           "metric drawings, instead of X.X / X.XX / X.XXX")
        tol_btn.clicked.connect(self.edit_tolerances)
        trow.addWidget(tol_btn)
        lay.addLayout(trow)
        self._show_tol()
        note = QtWidgets.QLabel("* fields are the same on every drawing. New sheets start from "
                                "the company file, then your own defaults on top.")
        note.setWordWrap(True)
        lay.addWidget(note)
        comp = QtWidgets.QHBoxLayout()
        self.comp_label = QtWidgets.QLabel()
        self._show_company()
        choose = QtWidgets.QPushButton("Use company file...")
        choose.setToolTip("Point DrawingKit at a defaults file, e.g. on a shared drive")
        choose.clicked.connect(self.use_company)
        comp.addWidget(self.comp_label, 1)
        comp.addWidget(choose)
        lay.addLayout(comp)
        btns = QtWidgets.QHBoxLayout()
        mine = QtWidgets.QPushButton("Save * as my defaults")
        mine.clicked.connect(self.save_mine)
        company = QtWidgets.QPushButton("Save * to company file...")
        company.clicked.connect(self.save_company)
        load = QtWidgets.QPushButton("Fill * from defaults")
        load.setToolTip("Replace the * fields with the saved defaults")
        load.clicked.connect(self.fill_defaults)
        btns.addWidget(mine)
        btns.addWidget(company)
        btns.addWidget(load)
        btns.addStretch(1)
        ok = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        ok.accepted.connect(self.accept)
        ok.rejected.connect(self.reject)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def open_designer(self):
        from . import blockdesigner
        cur = layout_key(self.page)
        start = cur[7:] if cur.startswith("custom:") else None
        use = blockdesigner.run(self.page, start, self)
        if use:
            self.switch_to = use
            self.done(2)

    def _show_company(self):
        path = self.tb.company_file()
        self.comp_label.setText("Company file: %s" % (path or "none"))
        self.comp_label.setToolTip(path or "")

    def values(self):
        return {k: e.text() for k, e in self.edits.items()}

    def units(self):
        e = self.edits.get("units")
        return self.tb.units_label({"units": e.text() if e else "mm"})

    def _show_tol(self):
        t = self.tol_table
        if not t:
            self.tol_label.setText("none" if self.tb.block_tolerance_rows(self.page)
                                   else "none (no table on the sheet)")
        else:
            n = len(t.get("linear", [])) + len(t.get("angular", []))
            extra = (", angles ±%s" % t["angle_all"].lstrip("±")) if t.get("angle_all") else ""
            self.tol_label.setText("%s table, %d row(s)%s" % (t.get("preset") or "Own", n, extra))

    def edit_tolerances(self):
        dlg = ToleranceDialog(self.tol_table, self.units(), self)
        if dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec():
            self.tol_table = dlg.data()
            self.tol_changed = True
            self._show_tol()
            g = self.edits.get("general_tolerances")
            if g is not None and self.tol_table:
                name = self.tol_table.get("preset") or ""
                g.setText(name if name.upper().startswith("ISO") else "SEE TABLE")

    def defaults_values(self):
        v = self.values()
        v[self.tb.TOL_TABLE] = self.tol_table
        return v

    def save_mine(self):
        path = self.tb.save_user_defaults(self.defaults_values())
        say("Title block defaults saved to %s" % path)

    def save_company(self):
        start = self.tb.company_file() or os.path.join(os.path.expanduser("~"), "drawingkit_company.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Company title block file", start,
                                                        "DrawingKit defaults (*.json)")
        if path:
            self.tb.save_company_defaults(self.defaults_values(), path)
            self._show_company()
            say("Company title block defaults saved to %s" % path)

    def use_company(self):
        start = self.tb.company_file() or os.path.expanduser("~")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Company title block file", start,
                                                        "DrawingKit defaults (*.json)")
        if path:
            self.tb.set_company_file(path)
            self._show_company()

    def fill_defaults(self):
        vals = self.tb.load_defaults()
        for k in self.tb.SHARED:
            if k in self.edits and k in vals:
                self.edits[k].setText(vals[k])
        if self.tb.TOL_TABLE in vals:
            self.tol_table = vals[self.tb.TOL_TABLE]
            self.tol_changed = True
            self._show_tol()


class DesignTitleBlock:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_DesignTitleBlock.svg"),
                "MenuText": "Design title block...",
                "ToolTip": "Draw your own title block: split and merge cells, and put a field, "
                           "fixed text, your logo, the tolerances by size or the projection "
                           "symbol in each. Save it for yourself or for the company; it then "
                           "shows up as a layout in the Title block form and for new sheets."}

    def IsActive(self):
        return True

    def Activated(self):
        from . import blockdesigner, titleblock as tb
        page = target_page() if App.ActiveDocument is not None and \
            pages(App.ActiveDocument) else None
        cur = layout_key(page) if page is not None else ""
        if page is not None and not tb.template_style(page):
            page = None                     # not DrawingKit's template: can't swap it
        use = blockdesigner.run(page, cur[7:] if cur.startswith("custom:") else None)
        if not use or page is None:
            return
        doc = page.Document

        def work():
            if not tb.set_template_variant(page, style_=use):
                raise ValueError("That title block doesn't fit inside this sheet's frame.")
            core.make_room_for_tables(page)
        try:
            run_in_transaction(doc, "DrawingKit title block", work)
        except ValueError as e:
            warn(str(e))
        doc.recompute()
        redraw_template(page)


class EditTitleBlock:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_EditTitleBlock.svg"),
                "MenuText": "Title block...",
                "ToolTip": "Edit every title block field in one form. Save company name, "
                           "tolerances (X.X / X.XX / X.XXX, angles), finish and the like as "
                           "your defaults or to a company file the whole team uses."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import titleblock as tb
        doc = App.ActiveDocument
        page = target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        if not tb.fields(page):
            warn("This page's template has no fields to edit.")
            return
        total = 0
        for _ in range(10):          # the form reopens after each layout switch
            dlg = TitleBlockDialog(page)
            res = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
            if not res:
                break
            vals = dlg.values()
            want = dlg.switch_to or dlg.layout_combo.currentData()
            tb.set_style_pref(dlg.new_combo.currentData())
            cur = layout_key(page)
            tol = (dlg.tol_table, dlg.units()) if dlg.tol_changed else None

            def work():
                changed = tb.set_fields(page, vals)
                if tol is not None:
                    if tb.block_tolerance_rows(page):
                        tb.fill_block_tolerances(page, tol[0])   # rows in the title block
                    else:
                        from . import tables
                        tables.set_tolerance_table(page, tol[0], tol[1])
                        if tol[0]:
                            core.make_room_for_tables(page)   # views off the new table
                if want != cur:
                    style_ = want if want.startswith("custom:") else \
                        ("ansi" if want == "ansi" else "iso")
                    ok_ = tb.set_template_variant(page, tol_row=(want == "iso_tol"), style_=style_)
                    if not ok_:
                        raise ValueError("That layout needs DrawingKit's own template, and room "
                                         "for the block inside the sheet's frame (the ANSI "
                                         "block needs 210 mm).")
                    tb.set_fields(page, {k: v for k, v in vals.items() if k != "sheet_number"})
                    if want.startswith("custom:") or cur.startswith("custom:"):
                        core.make_room_for_tables(page)
                if tb.match_tolerance_mode(page):    # size table or X.X values, not both
                    tb.set_fields(page, {k: v for k, v in vals.items() if k != "sheet_number"})
                return changed
            try:
                total += len(run_in_transaction(doc, "DrawingKit title block", work))
            except ValueError as e:
                warn(str(e))
            doc.recompute()
            redraw_template(page)
            if res != 2:             # OK pressed: done
                break
        say("Title block: %d field(s) changed" % total)


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
        eco_f = ui.field("", 60, "ECO", "Change order number (optional)")
        desc_f = ui.field("Initial release", 200, tip="What changed")
        who_f = ui.field("", 50, "by", "Approved by")
        pop.add_row(eco_f, desc_f, who_f)
        pop.focus_on = desc_f
        if not pop.run():
            return
        desc, who, eco = desc_f.text(), who_f.text(), eco_f.text()
        doc = page.Document
        run_in_transaction(doc, "DrawingKit revision",
                           lambda: tables.add_revision(page, desc.strip(), who.strip(),
                                                       eco=eco.strip()))
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
                             ("deviation", "+ / −"), ("limits", "Limits")], settings["tol"], 80)
        self.tol.setToolTip("Limits: the largest and smallest size, stacked (e.g. 0.053 over "
                            "0.051), worked out from the measured size and the + / − values")
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
            self.lower.setVisible(not basic and t in ("deviation", "limits"))
            self.after.setEnabled(t != "limits")
            self.reference.setEnabled(t != "limits")
            if value is not None:
                dp = self.decimals.currentData()
                if t == "limits" and not basic:
                    hi = value + _num(self.upper.text())
                    lo = value - _num(self.lower.text())
                    self.value.setText("%.*f / %.*f" % (dp, hi, dp, lo))
                else:
                    self.value.setText("%.*f" % (dp, value))
        self.upper.textChanged.connect(sync)
        self.lower.textChanged.connect(sync)
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
                           "decimals, tolerance (±, +/− or limits), basic (boxed) or "
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


class ShadedView:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Shaded.svg"),
                "MenuText": "Shaded isometric view",
                "ToolTip": "A shaded picture of the part or assembly from the isometric "
                           "direction (each part in its colour, edges drawn), marked reference "
                           "only, not to scale. Select parts, a view, or nothing (the sheet's "
                           "parts), then click where it goes (Enter: a free spot). With a "
                           "shaded view selected, paints it again after the model changed."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import shaded, interact
        doc = App.ActiveDocument
        sel = Gui.Selection.getSelection()
        old = [o for o in sel if shaded.is_shaded(o)]
        if old:
            try:
                run_in_transaction(doc, "DrawingKit update shaded view",
                                   lambda: [shaded.update(o) for o in old])
            except ValueError as e:
                warn(str(e))
                return
            doc.recompute()
            say("%d shaded view(s) painted again" % len(old))
            return
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        objs = selected_parts()
        if not objs:
            for o in sel:
                if o.isDerivedFrom("TechDraw::DrawViewPart"):
                    objs = list(o.Source)
                    break
                if o.isDerivedFrom("TechDraw::DrawProjGroup"):
                    objs = list(o.Source)
                    break
        if not objs:
            group, extras = core.page_content(page)
            src = group if group is not None else (extras[0] if extras else None)
            objs = list(src.Source) if src is not None else []
        if not objs:
            warn("Select the part(s) to show, or a view of them.")
            return
        show_page(page)
        Gui.Selection.clearSelection()
        frame, _ = core.drawing_area(page.Template.Template, *core.page_size(page))
        width = round(min(110.0, max(60.0, (frame[2] - frame[0]) * 0.25)) / 5.0) * 5.0

        def on_done(points):
            place = points[-1] if points else None
            try:
                img = run_in_transaction(doc, "DrawingKit shaded view",
                                         lambda: shaded.add(page, objs, width, place))
            except ValueError as e:
                warn(str(e))
                return
            doc.recompute()
            say("Shaded isometric view added (%s)" % img.Label)

        interact.PagePicker(page, "DrawingKit: click where the shaded isometric view goes "
                                  "(Enter: a free spot, Esc: cancel)",
                            on_move=lambda pt, _p: [("rect", pt[0], pt[1], width, width * 0.75)],
                            on_click=lambda pt, pts: True, on_done=on_done,
                            finish_on_right_click=True)


class AlternatePosition:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Phantom.svg"),
                "MenuText": "Alternate position (phantom lines)",
                "ToolTip": "Show a part swung or slid to another position in phantom lines "
                           "(long dash, double dot). Click the part in a view, then the pivot "
                           "(snaps to hole centres and corners) and swing it with the mouse; "
                           "or press Enter instead of a pivot to slide it. Select a view that "
                           "has some and press it again to remove them."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot, interact, phantom, drafting, smartdim
        doc = App.ActiveDocument
        sel = [o for o in Gui.Selection.getSelection()
               if o.isDerivedFrom("TechDraw::DrawViewPart") and phantom.entries(o)]
        if sel:
            n = sum(len(phantom.entries(v)) for v in sel)
            if ui.choose("%d alternate position(s) on the selected view(s)" % n,
                         [(True, "Remove them"), (False, "Keep")]):
                run_in_transaction(doc, "DrawingKit remove alternate positions",
                                   lambda: [phantom.remove(v) for v in sel])
                doc.recompute()
                say("Alternate positions removed")
            return
        page = target_page()
        if page is None:
            warn("Open the page first.")
            return
        show_page(page)
        Gui.Selection.clearSelection()
        st = {}

        def ghost(polys):
            return [("line", [annot.view_point(st["view"], q) for q in poly]) for poly in polys]

        # 1. the part
        def pick_part(pt, points):
            points.clear()
            view = interact._view_under(page, pt[0], pt[1], margin=1.0)
            if view is None:
                say("Click on a part in a view")
                return False
            try:
                _it, obj = drafting.part_at(view, pt[0], pt[1])
            except Exception:
                obj = None
            if obj is None and len(view.Source) == 1:
                obj = view.Source[0]
            if obj is None:
                say("No part there - click on the part's outline or face")
                return False
            try:
                polys = phantom.outline(view, obj)
            except Exception as e:
                warn(str(e))
                return False
            st.update(view=view, obj=obj, polys=polys,
                      grab=annot.page_to_local(view, *pt))
            return True

        def hover_part(pt, _points):
            view = interact._view_under(page, pt[0], pt[1], margin=1.0)
            if view is None:
                return []
            try:
                _it, obj = drafting.part_at(view, pt[0], pt[1])
            except Exception:
                obj = None
            if obj is None:
                return []
            try:
                st["view"] = view
                return [("hl_line", line[i:i + 2], "#ff7f0e")
                        for line in [[annot.view_point(view, q) for q in poly]
                                     for poly in _cached_outline(st, view, obj)]
                        for i in range(len(line) - 1)]
            except Exception:
                return []

        # 2. the pivot (Enter: slide instead)
        def snap(pt):
            view = st["view"]
            local = annot.page_to_local(view, *pt)
            try:
                e = smartdim.pick(view, local, smartdim.entities(view))
            except Exception:
                e = None
            if e is not None and e.kind in ("circle", "arc", "vertex", "centre"):
                return e.point
            return local

        def after_part(points):
            if "obj" not in st:
                return
            interact.PagePicker(page, "DrawingKit: click the pivot to swing %s about (snaps to "
                                      "hole centres and corners), or press Enter to slide it"
                                % st["obj"].Label,
                                on_move=lambda pt, _p: ghost(st["polys"]) +
                                [("hl_point", annot.view_point(st["view"], snap(pt)), 1.6, "#ff7f0e")],
                                on_click=lambda pt, pts: True,
                                on_done=after_pivot, finish_on_right_click=True)

        # 3. the new position
        def placement(pt):
            view = st["view"]
            local = annot.page_to_local(view, *pt)
            if st.get("pivot") is None:
                return 0.0, (0.0, 0.0), (local[0] - st["grab"][0], local[1] - st["grab"][1])
            px, py = st["pivot"]
            a0 = math.atan2(st["grab"][1] - py, st["grab"][0] - px)
            a1 = math.atan2(local[1] - py, local[0] - px)
            ang = math.degrees(a1 - a0)
            ang = (ang + 180.0) % 360.0 - 180.0
            return round(ang / 5.0) * 5.0, (px, py), (0.0, 0.0)

        def after_pivot(points):
            st["pivot"] = snap(points[-1]) if points else None

            def show(pt, _p):
                ang, piv, mv = placement(pt)
                shapes = ghost(phantom.moved(st["polys"], piv, ang, mv))
                label = ("%g°" % ang) if st["pivot"] is not None else \
                    "%.1f, %.1f" % (mv[0], mv[1])
                shapes.append(("text", pt[0] + 3.0, pt[1] + 3.0, label))
                return shapes

            def place(pt, pts):
                ang, piv, mv = placement(pt)
                try:
                    n = run_in_transaction(doc, "DrawingKit alternate position",
                                           lambda: phantom.add(st["view"], st["obj"], ang, piv, mv))
                except ValueError as e:
                    say(str(e))
                    return False
                doc.recompute()
                say("%s shown in phantom lines (%d lines)" % (st["obj"].Label, n))
                return True

            interact.PagePicker(page, "DrawingKit: move the mouse to %s %s, click to place "
                                      "(Esc: cancel)" % ("swing" if st["pivot"] is not None
                                                         else "slide", st["obj"].Label),
                                on_move=show, on_click=place, finish_on_right_click=False)

        interact.PagePicker(page, "DrawingKit: click the part to show in another position",
                            on_move=hover_part, on_click=pick_part, on_done=after_part,
                            finish_on_right_click=False)


def _cached_outline(st, view, obj):
    from . import phantom
    key = (view.Name, obj.Name)
    cache = st.setdefault("outlines", {})
    if key not in cache:
        cache[key] = phantom.outline(view, obj)
    return cache[key]


FLAG_PREF = "FlagText"


def _dimension_at(page, pt, reach=2.5):
    """The dimension whose text is under (or within ``reach`` mm of) a page
    point, or None."""
    from . import annot
    best = None
    for d in page.Views:
        if not d.isDerivedFrom("TechDraw::DrawViewDimension"):
            continue
        try:
            b = annot.dim_text_box(d)
        except Exception:
            b = None
        if b is None:
            continue
        dx = max(b[0] - pt[0], 0.0, pt[0] - b[2])
        dy = max(b[1] - pt[1], 0.0, pt[1] - b[3])
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= reach and (best is None or dist < best[0]):
            best = (dist, d)
    return best[1] if best else None


class SpecialCharacteristic:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Flag.svg"),
                "MenuText": "Special characteristic flag",
                "ToolTip": "A hexagon flag (SC, CC, KC... you choose the text) after a "
                           "dimension's value, to mark a special or critical characteristic. "
                           "Select dimensions first, or click a dimension's value; click "
                           "anywhere else for a flag on its own (e.g. in the notes). Esc to "
                           "finish."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import annot, interact
        last = App.ParamGet(annot.PREFS).GetString(FLAG_PREF, "SC") or "SC"
        text = ui.ask_text("Special characteristic", "Flag text", last, 60)
        if text is None or not text.strip():
            return
        text = text.strip()
        App.ParamGet(annot.PREFS).SetString(FLAG_PREF, text)
        doc = App.ActiveDocument
        dims = [o for o in Gui.Selection.getSelection()
                if o.isDerivedFrom("TechDraw::DrawViewDimension")]
        if dims:
            page = parent_page(dims[0])

            def work():
                made = 0
                for d in dims:
                    if any(_flag_text(f) == text for f in annot.flags_on(d)):
                        continue
                    annot.add_flag(parent_page(d), text, dimension=d)
                    made += 1
                return made
            made = run_in_transaction(doc, "DrawingKit flag", work)
            doc.recompute()
            say("%d %s flag(s) added" % (made, text))
            return
        page = target_page()
        if page is None:
            warn("Open the page (or select dimensions) first.")
            return
        show_page(page)
        Gui.Selection.clearSelection()
        w, h = annot.flag_size(text, annot.default_size() / 3.5)

        def on_move(pt, _pts):
            d = _dimension_at(page, pt)
            if d is not None:
                spot = annot.flag_beside(d, text, annot.default_size() / 3.5)
                if spot is not None:
                    return [("rect", spot[0], spot[1], w, h)]
            return [("rect", pt[0], pt[1], w, h)]

        def on_click(pt, points):
            points.clear()
            d = _dimension_at(page, pt)
            if d is not None and any(_flag_text(f) == text for f in annot.flags_on(d)):
                say("%s already has a %s flag" % (d.Label, text))
                return False
            run_in_transaction(doc, "DrawingKit flag",
                               lambda: annot.add_flag(page, text, dimension=d) if d is not None
                               else annot.add_flag(page, text, at=pt))
            doc.recompute()
            say("%s flag added%s - click the next, Esc to finish"
                % (text, (" to " + d.Label) if d is not None else ""))
            return False

        interact.PagePicker(page, "DrawingKit: click a dimension's value for a %s flag, or "
                                  "anywhere for a flag on its own. Esc to finish." % text,
                            on_move=on_move, on_click=on_click, finish_on_right_click=True)


def _flag_text(sym):
    from . import annot
    return annot._values(sym).get("flag", annot._params(sym).get("text", ""))


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
                pv = st["view"]
                plocal = annot.page_to_local(pv, *pt)
                ready = hov is None or any(same(hov, p) for p in st["picks"]) or \
                    len(st["picks"]) >= 2
                txt = smartdim.preview_value(st["picks"], plocal)
                if ready:
                    # the dimension as it will be placed, following the mouse
                    try:
                        for kind, pts in smartdim.preview_shapes(st["picks"], plocal,
                                                                 core.view_scale(pv)):
                            shapes.append(("ghost_fill" if kind == "fill" else "ghost",
                                           [annot.view_point(pv, q) for q in pts]))
                    except Exception:
                        pass
                    if txt:
                        shapes.append(("ctext", pt[0], pt[1] + 1.8, txt))
                elif txt:
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
        from . import standards
        self.iso = ui.toggle("ISO dashed line", standards.weld_dashed_line(),
                             "ISO 2553 system A identification line (AWS A2.4, used with ASME, has none)")
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
                "ToolTip": "Save the current sheet, or all sheets, as PDF (one file, a page "
                           "per sheet, with the characteristics list inside), DXF or SVG "
                           "(a file per sheet), named after the drawing number."}

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

    def __init__(self, text="", size=3.5, font=None, leader=False, editing=False):
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
                           "DrawingKit notes, symbols and dimensions, and the title block font "
                           "of new sheets (DejaVu Sans until you pick one), and the size of "
                           "view captions such as SECTION A-A (or select a view for just "
                           "that). Captions share one size on every sheet."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from . import annot, fonts
        sel = Gui.Selection.getSelection()
        objs = [o for o in sel if annot.text_style(o) is not None]
        views = [o for o in sel if o.isDerivedFrom("TechDraw::DrawViewPart")
                 or o.isDerivedFrom("TechDraw::DrawProjGroup")]
        if sel and not objs and not views:
            warn("The selection has no text. Select dimensions, notes or DrawingKit symbols, "
                 "a view for the size of its caption (or nothing, to set the defaults).")
            return
        cap_row = None
        if objs:
            size, font = annot.text_style(objs[0])
            title = "Text of %d item%s" % (len(objs), "" if len(objs) == 1 else "s")
        elif views:
            title = "View captions (SECTION A-A, DETAIL B...), on every sheet"
        else:
            size, font = annot.default_size(), annot.default_font()
            title = "Default text for new notes, symbols, dimensions and title blocks"
        pop = ui.Popover(title)
        row = None
        if not (views and not objs):
            row = ui.TextStyleRow(size, font)
            pop.add_row(*row.widgets(), stretch=True)
        if not objs:
            cap_row = ui.TextStyleRow(*fonts.caption_style())
            pop.add_row(*(["Captions"] if row else []) + cap_row.widgets(), stretch=True)
        if not pop.run():
            return
        if cap_row is not None:
            csize, cfont = cap_row.value()
            if (csize, cfont) != fonts.caption_style():
                fonts.set_caption_style(csize, cfont)
                say("View captions: %g mm %s (TechDraw's label preference, every sheet)"
                    % fonts.caption_style())
        if row is None:
            return
        size, font = row.value()
        if not objs:
            annot.set_defaults(size, font)
            say("New notes, symbols, dimensions and title blocks: %g mm %s"
                % (size or annot.default_size(), font))
            return
        doc = objs[0].Document
        run_in_transaction(doc, "DrawingKit text size and font",
                           lambda: [annot.set_text_style(o, size, font) for o in objs])
        doc.recompute()
        say("Text updated on %d item(s)" % len(objs))


class Table:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_Table.svg"),
                "MenuText": "Table",
                "ToolTip": "A table of your own (data for a spline, a gear, a gauge...): type "
                           "or paste the rows, the first one being the headings, cells split "
                           "by tabs (from a spreadsheet), '|' or ';'. Placed in a free spot; "
                           "drag it where you want it. To change it, select the table and "
                           "run this again, or double-click it on the sheet."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import tables
        own = [o for o in Gui.Selection.getSelection() if tables.is_own_table(o)]
        if own:
            edit_own_table(own[0])
            return
        page = target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        pop = ui.Popover("New table on %s" % page.Label)
        title = ui.field("", 260, "Title (optional), e.g. SPLINE DATA")
        body = QtWidgets.QPlainTextEdit()
        body.setPlaceholderText("TEETH | PITCH | PRESSURE ANGLE\n16 | 32/64 | 30°")
        body.setMinimumSize(320, 140)
        pop.add_row(title, stretch=True)
        pop.add_row(body, stretch=True)
        if not pop.run():
            return
        rows = tables.parse_rows(body.toPlainText())
        if not rows:
            warn("Type the headings and at least one row.")
            return
        doc = page.Document
        t = run_in_transaction(doc, "DrawingKit table",
                               lambda: tables.add_table(page, rows[0], rows[1:],
                                                        title.text().strip()))
        doc.recompute()
        say("%s: %d column(s), %d row(s); drag it into place; to change it, select it "
            "and run Table again" % (t.Label, len(rows[0]), len(rows) - 1))


def edit_own_table(table):
    """Edit a table of your own: its title and all its cells as text."""
    from . import tables
    title_now, _h, _r = tables.table_contents(table)
    pop = ui.Popover("Edit %s" % table.Label)
    title = ui.field(title_now, 260, "Title (optional), e.g. SPLINE DATA")
    body = QtWidgets.QPlainTextEdit()
    body.setPlainText(tables.contents_text(table))
    body.setMinimumSize(420, 160)
    pop.add_row(title, stretch=True)
    pop.add_row(body, stretch=True)
    if not pop.run():
        return False
    rows = tables.parse_rows(body.toPlainText())
    if not rows:
        warn("Type the headings and at least one row.")
        return False
    doc = table.Document
    run_in_transaction(doc, "DrawingKit edit table",
                       lambda: tables.edit_table(table, rows[0], rows[1:], title.text().strip()))
    doc.recompute()
    say("%s updated: %d column(s), %d row(s)" % (table.Label, len(rows[0]), len(rows) - 1))
    return True


class LineWeights:
    def GetResources(self):
        return {"Pixmap": icon("DrawingKit_LineWeights.svg"),
                "MenuText": "Line weights",
                "ToolTip": "Thick and thin lines for the whole sheet (ISO 128 / ASME Y14.2): "
                           "thick for visible outlines; thin for hidden and centre lines, "
                           "dimensions, leaders and DrawingKit's symbols. New views and "
                           "dimensions keep to it."}

    def IsActive(self):
        return App.ActiveDocument is not None and bool(pages(App.ActiveDocument))

    def Activated(self):
        from . import lineweights
        page = target_page()
        if page is None:
            warn("Select the page (or a view on it) first.")
            return
        now = lineweights.weights() or lineweights.PRESETS[0][1:]
        pop = ui.Popover("Line weights for %s" % page.Label)
        items = [(i, name) for i, (name, _a, _b) in enumerate(lineweights.PRESETS)]
        preset = ui.combo(items)
        thick = ui.field("%g" % now[0], 46, "0.5", "Visible outlines (mm)")
        thin = ui.field("%g" % now[1], 46, "0.25", "Hidden and centre lines, dimensions, "
                                                   "leaders, symbols (mm)")

        def pick(_i=None):
            _name, a, b = lineweights.PRESETS[preset.currentData()]
            thick.setText("%g" % a)
            thin.setText("%g" % b)
        preset.currentIndexChanged.connect(pick)
        pop.add_row(preset, stretch=True)
        pop.add_row("Thick", thick, "Thin", thin, "mm")
        if not pop.run():
            return
        try:
            a, b = float(thick.text().replace(",", ".")), float(thin.text().replace(",", "."))
        except ValueError:
            warn("Type the line widths in mm, e.g. 0.5 and 0.25.")
            return
        if not (0.05 <= b <= a <= 3.0):
            warn("The thin line must be thinner than the thick one (0.05 to 3 mm).")
            return
        doc = page.Document
        n = run_in_transaction(doc, "DrawingKit line weights",
                               lambda: lineweights.apply_page(page, a, b))
        doc.recompute()
        say("Line weights %g / %g mm on %d item(s); new views and dimensions follow"
            % (a, b, n))


COMMANDS = [
    ("DrawingKit_NewSheet", NewSheet()),
    ("DrawingKit_LineWeights", LineWeights()),
    ("DrawingKit_Table", Table()),
    ("DrawingKit_Standard", DrawingStandard()),
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
    ("DrawingKit_Flag", SpecialCharacteristic()),
    ("DrawingKit_Phantom", AlternatePosition()),
    ("DrawingKit_Shaded", ShadedView()),
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
    ("DrawingKit_EditTitleBlock", EditTitleBlock()),
    ("DrawingKit_DesignTitleBlock", DesignTitleBlock()),
    ("DrawingKit_Revision", AddRevision()),
    ("DrawingKit_FitToPage", FitToPage()),
    ("DrawingKit_ScaleUp", ScaleStep(+1)),
    ("DrawingKit_ScaleDown", ScaleStep(-1)),
    ("DrawingKit_SetScale", SetScale()),
    ("DrawingKit_SpeedToggle", SpeedToggle()),
    ("DrawingKit_HiddenLines", HiddenLines()),
    ("DrawingKit_HatchByPart", HatchByPart()),
    ("DrawingKit_EditHatch", EditHatch()),
]

for _name, _cmd in COMMANDS:
    Gui.addCommand(_name, _cmd)

# toolbars in the DrawingKit workbench
COMMAND_GROUPS = [
    ("DrawingKit views", ["DrawingKit_NewSheet", "DrawingKit_Standard", "DrawingKit_StandardViews",
                          "DrawingKit_ProjectedView", "DrawingKit_Section",
                          "DrawingKit_AlignedSection", "DrawingKit_HatchByPart", "DrawingKit_EditHatch",
                          "DrawingKit_DetailView",
                          "DrawingKit_AuxView", "DrawingKit_FitToPage", "DrawingKit_ScaleUp",
                          "DrawingKit_ScaleDown", "DrawingKit_SetScale", "DrawingKit_HiddenLines",
                          "DrawingKit_Phantom", "DrawingKit_Shaded",
                          "DrawingKit_SpeedToggle"]),
    ("DrawingKit dimensions", ["DrawingKit_Dimension", "DrawingKit_Ordinate", "DrawingKit_EditDimension",
                               "DrawingKit_CenterMarks", "DrawingKit_Centerline",
                               "DrawingKit_HoleCallout", "DrawingKit_Chamfer"]),
    ("DrawingKit annotation", ["DrawingKit_SurfaceFinish", "DrawingKit_Weld",
                               "DrawingKit_Datum", "DrawingKit_DatumTarget",
                               "DrawingKit_ToleranceFrame", "DrawingKit_Flag", "DrawingKit_Note",
                               "DrawingKit_TextStyle", "DrawingKit_LineWeights"]),
    ("DrawingKit tables", ["DrawingKit_PartsList", "DrawingKit_Balloon",
                           "DrawingKit_HoleTable", "DrawingKit_Table", "DrawingKit_FillTitleBlock",
                           "DrawingKit_EditTitleBlock", "DrawingKit_DesignTitleBlock",
                           "DrawingKit_Revision", "DrawingKit_Export"]),
]


class AlignmentObserver:
    """Keeps dragged projected views on their alignment axis (see core)."""

    def __init__(self):
        self.pending = set()
        self.settling = {}

    def slotDeletedObject(self, obj):
        """A symbol deleted: its leader goes too (after the deletion is done)."""
        try:
            name = getattr(obj, "DrawingKitLeader", "") if "DrawingKitLeader" in obj.PropertiesList else ""
        except Exception:
            return
        if not name:
            return
        docname, owner = obj.Document.Name, obj.Name

        def drop():
            try:
                from . import annot
                doc = App.getDocument(docname)
                lead = doc.getObject(name) if doc else None
                if lead is not None and getattr(lead, annot.LEADER_OF, "") == owner \
                        and doc.getObject(owner) is None:
                    annot._remove(lead)
            except Exception:
                pass
        QtCore.QTimer.singleShot(0, drop)

    def slotDeletedDocument(self, doc):
        try:
            from . import dimrefs
            dimrefs.forget_document(doc.Name)
            self.settling.pop(doc.Name, None)
        except Exception:
            pass

    def slotCreatedDocument(self, doc):
        """A document made or opened. Python observers get no 'restored'
        call, and an opened file is read straight after this one: look at
        it once the event loop runs again."""
        self.tidy_opened(doc)

    def tidy_opened(self, doc):
        """An opened drawing: dimensions FreeCAD moved to other edges put
        back (see dimrefs), leaders and hatches under the views, symbols
        drawn by an older DrawingKit drawn again (their leaders split off,
        tight boxes), loose datums on a frame's edge attached to it."""
        name = doc.Name
        tries = [0]

        def ready(d):
            # TechDraw works out the views' lines in the background after opening
            for o in d.Objects:
                if o.isDerivedFrom("TechDraw::DrawViewPart"):
                    try:
                        o.getVertexByIndex(0)
                    except Exception:
                        return False
            return True

        def tidy():
            try:
                from . import annot, dimrefs
                d = App.getDocument(name)
            except Exception:
                return
            try:
                if d is None:
                    return
                dims = any(o.isDerivedFrom("TechDraw::DrawViewDimension") for o in d.Objects)
                notes = any(annot.is_annotation(o) or annot.LEADER_OF in o.PropertiesList
                            for o in d.Objects)
                cuts = any(o.isDerivedFrom("TechDraw::DrawViewSection")
                           or o.isDerivedFrom("TechDraw::DrawViewDetail") for o in d.Objects)
                if not dims and not notes and not cuts:
                    return
                is_ready = ready(d)
                if not is_ready and tries[0] < 60:
                    tries[0] += 1
                    QtCore.QTimer.singleShot(500, tidy)
                    return
            except Exception:
                return
            try:
                dimrefs.update_views(d)     # first look: notes where everything is
            except Exception:
                pass
            if dims and is_ready:
                try:
                    fixed = dimrefs.repair(d)
                    if fixed:
                        App.Console.PrintMessage(
                            "DrawingKit: %d dimension(s) had moved to other edges when the "
                            "file opened and were put back: %s\n" % (len(fixed), ", ".join(fixed)))
                except Exception as e:
                    App.Console.PrintWarning("DrawingKit dimensions: %s\n" % e)
            if notes:
                try:
                    annot.tidy_leaders(d)
                except Exception as e:
                    App.Console.PrintWarning("DrawingKit leaders: %s\n" % e)
        QtCore.QTimer.singleShot(500, tidy)

    def slotRecomputedDocument(self, doc):
        """The model or the drawing changed: once TechDraw has redrawn the
        views, put back any dimension that lost its edge (see dimrefs)."""
        from . import dimrefs
        if dimrefs.BUSY[0]:
            return
        try:
            name = doc.Name
            if not any(o.isDerivedFrom("TechDraw::DrawViewDimension")
                       or o.isDerivedFrom("TechDraw::DrawViewSection")
                       or o.isDerivedFrom("TechDraw::DrawViewDetail") for o in doc.Objects):
                return
        except Exception:
            return
        self.settle_then_check(name)

    def settle_then_check(self, name):
        from . import dimrefs
        if name in self.settling:
            self.settling[name] = [None, 0, 0]          # start waiting again
            return
        self.settling[name] = [None, 0, 0]              # last snapshot, same count, polls

        def poll():
            st = self.settling.get(name)
            if st is None:
                return
            try:
                d = App.getDocument(name)
            except Exception:
                d = None
            if d is None:
                self.settling.pop(name, None)
                return
            try:
                snap = dimrefs.snapshot(d)
            except Exception:
                snap = None
            st[1] = st[1] + 1 if snap == st[0] else 0
            st[0] = snap
            st[2] += 1
            # TechDraw redraws the views and then the dimensions in the
            # background for some seconds after a recompute
            if st[1] < 6 and st[2] < 120:
                QtCore.QTimer.singleShot(500, poll)
                return
            self.settling.pop(name, None)
            try:
                if dimrefs.update_views(d):
                    # details moved or marks redrawn: wait for that redraw too
                    self.settle_then_check(name)
                    return
            except Exception as e:
                App.Console.PrintWarning("DrawingKit views: %s\n" % e)
            try:
                fixed = dimrefs.repair(d)
                if fixed:
                    App.Console.PrintMessage(
                        "DrawingKit: %d dimension(s) lost their edges when the model changed "
                        "and were put back: %s\n" % (len(fixed), ", ".join(fixed)))
            except Exception as e:
                App.Console.PrintWarning("DrawingKit dimensions: %s\n" % e)
        QtCore.QTimer.singleShot(500, poll)

    def slotChangedObject(self, obj, prop):
        if prop == "References2D":
            try:
                from . import dimrefs
                if not dimrefs.BUSY[0] and obj.isDerivedFrom("TechDraw::DrawViewDimension"):
                    dimrefs.forget(obj)
            except Exception:
                pass
            return
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
            if obj.isDerivedFrom("TechDraw::DrawViewDimension"):
                from . import standards
                standards.fit_text_style(obj)   # ASME text outside: no line through it
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
