# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless tests for DrawingKit. Run with: freecadcmd tests/run_tests.py"""

import math
import os
import unittest

import FreeCAD as App

from DrawingKitTools import core
from DrawingKitTools import titleblock as _titleblock

# Keep the tests off the user's own title block defaults, company file and
# title block preferences (they would change every sheet the tests make).
import tempfile as _tempfile
_NO_DEFAULTS = os.path.join(_tempfile.mkdtemp(), "no_defaults.json")
_titleblock.user_file = lambda: _NO_DEFAULTS
_titleblock.company_file = lambda: ""
_titleblock.style = lambda: "iso"
_titleblock.wants_tolerance_row = lambda: False

# the model's own front view (most tests are written against it)
FRONT = core.STANDARD_DIRECTIONS["Front"]


def make_box(doc, length=400, width=300, height=50):
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = length, width, height
    doc.recompute()
    return box


class TestScales(unittest.TestCase):
    def test_scale_text(self):
        self.assertEqual(core.scale_text(1.0), "1:1")
        self.assertEqual(core.scale_text(0.5), "1:2")
        self.assertEqual(core.scale_text(0.1), "1:10")
        self.assertEqual(core.scale_text(2.0), "2:1")

    def test_pick_largest_fitting(self):
        # 700 x 350 model mm into 380 x 231 paper mm with 15 mm gaps -> 1:2
        self.assertEqual(core.pick_scale_for_box(700, 350, 380, 231, 15, 15), 0.5)
        # tiny part gets enlarged
        self.assertEqual(core.pick_scale_for_box(10, 10, 380, 231), 20.0)
        # exact fit counts as fitting
        self.assertEqual(core.pick_scale_for_box(200, 100, 200, 100), 1.0)

    def test_nothing_fits_gives_smallest(self):
        self.assertEqual(core.pick_scale_for_box(1e9, 1e9, 10, 10), 0.001)

    def test_scales_are_standard_and_sorted(self):
        values = [core.scale_value(r) for r in core.STANDARD_SCALES]
        self.assertEqual(values, sorted(values, reverse=True))
        for v in (1.0, 0.5, 0.2, 0.1, 2.0, 5.0):
            self.assertIn(v, values)


class TestLetters(unittest.TestCase):
    def test_first_letters(self):
        self.assertEqual(core.next_free_letter([]), "A")
        self.assertEqual(core.next_free_letter(["A"]), "B")
        self.assertEqual(core.next_free_letter(["A", "C"]), "B")
        self.assertEqual(core.next_free_letter(["a", " b "]), "C")

    def test_skips_i_o_q(self):
        self.assertEqual(core.next_free_letter(list("ABCDEFGH")), "J")
        self.assertNotIn("I", core.LETTERS)
        self.assertNotIn("O", core.LETTERS)
        self.assertNotIn("Q", core.LETTERS)

    def test_doubles_after_z(self):
        self.assertEqual(core.next_free_letter(core.LETTERS), "AA")
        self.assertEqual(core.next_free_letter(core.LETTERS + ["AA"]), "BB")


class TestTemplates(unittest.TestCase):
    def test_bundled_sizes_found(self):
        t = core.available_templates()
        for size in core.ISO_SIZES:
            self.assertIn((size, "Landscape"), t)
        self.assertIn(("A4", "Portrait"), t)
        for size in ("ANSI A", "ANSI B", "ANSI C", "ANSI D"):
            self.assertIn((size, "Landscape"), t)
            self.assertIn((size, "Portrait"), t)

    def test_prefers_title_block(self):
        t = core.available_templates()
        self.assertTrue(t[("A3", "Landscape")].endswith("A3_Landscape_ISO5457_minimal.svg"))

    def test_iso_drawing_area(self):
        path = core.available_templates()[("A3", "Landscape")]
        frame, title = core.drawing_area(path, 420, 297)
        self.assertEqual(frame, (20, 10, 410, 287))
        # title block frame is 230..410 x 10..46; the material/scale row
        # above it (up to y=58) must count as title block too
        self.assertEqual(title[:3], (230, 10, 410))
        self.assertGreaterEqual(title[3], 58)

    def test_drawingkit_template_every_size_both_ways(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from DrawingKitTools import templates as d2t
        out = tempfile.mkdtemp()
        for size in d2t.SIZES:
            for orient in d2t.ORIENTATIONS:
                for first in (True, False):
                    path = d2t.template_path(size, orient, first, out)
                    ET.parse(path)  # valid XML
                    w, h = d2t.page_size(size, orient)
                    frame, title = core.drawing_area(path, w, h)
                    self.assertEqual(frame, (20, 10, w - 10, h - 10), (size, orient))
                    self.assertAlmostEqual(title[0], w - 10 - 180, places=6)
                    self.assertAlmostEqual(title[3], 10 + 36, places=6)
                    # A4 portrait: the title block is as wide as the frame
                    self.assertGreaterEqual(len(core.free_regions(frame, title)), 1)

    def test_drawingkit_template_fields(self):
        from DrawingKitTools import templates as d2t
        text = d2t.make_svg("A3", "Landscape", True)
        for name in ("title", "scale", "drawing_number", "part_material", "creator",
                     "date_of_issue", "sheet_number", "revision_index"):
            self.assertIn('freecad:editable="%s"' % name, text)
        self.assertIn('id="projection_symbol"', text)
        self.assertNotEqual(d2t.make_svg("A3", "Landscape", False), text)
        with self.assertRaises(ValueError):
            d2t.page_size("A3", "Sideways")


class DocTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("DrawingKitTest")
        self.box = make_box(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class TestPages(DocTest):
    def test_create_every_page_size(self):
        from DrawingKitTools import templates as d2t
        for size in d2t.SIZES:
            for orient in d2t.ORIENTATIONS:
                page = core.create_page(self.doc, size, orient)
                self.doc.recompute()
                w, h = core.page_size(page)
                self.assertEqual((w, h), d2t.page_size(size, orient))
                self.assertIn("DrawingKit_", page.Template.Template)
                self.assertIn("scale", page.Template.EditableTexts)

    def test_projection_symbol_follows_page(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        want = "_1st.svg" if core.page_first_angle(page) else "_3rd.svg"
        self.assertTrue(page.Template.Template.endswith(want))

    def test_freecad_template_still_available(self):
        page = core.create_page(self.doc, "A3", "Landscape", drawingkit_template=False)
        self.assertTrue(page.Template.Template.endswith("A3_Landscape_ISO5457_minimal.svg"))

    def test_missing_template_raises(self):
        with self.assertRaises(ValueError):
            core.create_page(self.doc, "A0", "Sideways")


class TestViews(DocTest):
    def test_400x300_part_fits_a3_at_1_2(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=False, front=FRONT)
        self.doc.recompute()
        self.assertEqual(page.Scale, 0.5)
        self.assertEqual(sorted(v.Type for v in group.Views), ["Front", "Right", "Top"])
        for v in group.Views:
            self.assertIn("Up-to-date", v.State)
        self.assertInsideFrame(page, group)

    def test_isometric_added_and_smaller_scale(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True, front=FRONT)
        self.doc.recompute()
        self.assertIn("FrontTopRight", [v.Type for v in group.Views])
        self.assertLess(page.Scale, 0.5)
        self.assertInsideFrame(page, group)

    def test_small_part_on_a4_is_enlarged(self):
        small = make_box(self.doc, 20, 10, 5)
        page = core.create_page(self.doc, "A4", "Portrait")
        core.add_standard_views(page, [small], front=FRONT)
        self.assertGreaterEqual(page.Scale, 2.0)

    def test_layout_matches_techdraw_placement(self):
        # With TechDraw's own AutoDistribute on, it must place every view
        # exactly where DrawingKit's layout puts them when it is off.
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True, front=FRONT)
        ours = {v.Name: (float(v.X), float(v.Y)) for v in group.Views}
        core.make_aligned(group, False)
        self.doc.recompute()
        for v in group.Views:
            self.assertAlmostEqual(float(v.X), ours[v.Name][0], places=3, msg=v.Type)
            self.assertAlmostEqual(float(v.Y), ours[v.Name][1], places=3, msg=v.Type)

    def test_every_view_drawn_at_page_scale(self):
        # The bug Ravi saw: views kept their first (1:1) drawing.
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        import Part
        s = page.Scale
        expect = {"Front": (400, 50), "Top": (400, 300), "Right": (300, 50)}
        for v in group.Views:
            bb = Part.makeCompound(v.getVisibleEdges()).BoundBox
            w, h = expect[v.Type]
            self.assertAlmostEqual(bb.XLength, w * s, places=2, msg=v.Type)
            self.assertAlmostEqual(bb.YLength, h * s, places=2, msg=v.Type)

    def test_standard_directions_match_techdraw(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True, front=FRONT)
        self.doc.recompute()
        for v in group.Views:
            d, x = core.STANDARD_DIRECTIONS[v.Type]
            d, x = App.Vector(d).normalize(), App.Vector(x).normalize()
            self.assertTrue(App.Vector(v.Direction).normalize().isEqual(d, 1e-9), v.Type)
            self.assertTrue(App.Vector(v.XDirection).normalize().isEqual(x, 1e-9), v.Type)

    def test_stale_drawings_detected(self):
        import Part
        part = self.doc.addObject("Part::Feature", "Plain")
        part.Shape = Part.makeBox(100, 80, 60)
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [part], front=FRONT)
        self.doc.recompute()
        views = core.part_views(page)
        self.assertEqual(core.stale_views(views), [])
        # Headless FreeCAD redraws synchronously, so make the drawings stale
        # by growing the part 3x without recomputing the views.
        part.Shape = Part.makeBox(300, 240, 180)
        self.assertEqual(len(core.stale_views(views)), 3)
        core.redraw([group] + list(group.Views))
        self.assertEqual(core.stale_views(views), [])

    def test_title_block_shows_scale(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        core.add_standard_views(page, [self.box], front=FRONT)
        self.assertEqual(page.Template.EditableTexts["scale"], "1 : 2")

    def test_views_slide_on_one_axis_only(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True, front=FRONT)
        self.doc.recompute()
        self.assertTrue(core.is_aligned(group))
        self.assertFalse(group.AutoDistribute)
        right, top = group.getItemByLabel("Right"), group.getItemByLabel("Top")
        iso = group.getItemByLabel("FrontTopRight")
        front = group.Anchor
        # side view dragged diagonally: keeps the new X, snaps Y back
        right.X, right.Y = float(right.X) - 20, 33.0
        self.assertTrue(core.enforce_alignment(right))
        self.assertEqual(float(right.Y), 0.0)
        # top view dragged diagonally: keeps the new Y, snaps X back
        top.X, top.Y = 12.0, float(top.Y) - 10
        self.assertTrue(core.enforce_alignment(top))
        self.assertEqual(float(top.X), 0.0)
        # isometric is free
        iso.X, iso.Y = 1.0, 2.0
        self.assertFalse(core.enforce_alignment(iso))
        self.assertEqual((float(iso.X), float(iso.Y)), (1.0, 2.0))
        # front view drags the whole group
        gx, gy = float(group.X), float(group.Y)
        front.X, front.Y = 5.0, 7.0
        self.assertTrue(core.enforce_alignment(front))
        self.assertEqual((float(group.X), float(group.Y)), (gx + 5.0, gy + 7.0))
        self.assertEqual((float(front.X), float(front.Y)), (0.0, 0.0))

    def test_alignment_off_leaves_views_alone(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], front=FRONT)
        core.make_aligned(group, False)
        right = group.getItemByLabel("Right")
        right.Y = 40.0
        self.assertFalse(core.enforce_alignment(right))

    def test_first_angle_detected(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        right = group.getItemByLabel("Right")
        # First angle puts the right view on the left of the front view.
        self.assertEqual(core.is_first_angle(group), float(right.X) < 0)

    def assertInsideFrame(self, page, group):
        frame, _ = core.drawing_area(page.Template.Template, *core.page_size(page))
        for item in group.Views:
            x0, y0, x1, y1 = core.view_box(item)
            self.assertGreaterEqual(x0, frame[0] - 0.01, item.Type)
            self.assertGreaterEqual(y0, frame[1] - 0.01, item.Type)
            self.assertLessEqual(x1, frame[2] + 0.01, item.Type)
            self.assertLessEqual(y1, frame[3] + 0.01, item.Type)


class TestSectionsAndFit(DocTest):
    def setUp(self):
        super().setUp()
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.box], front=FRONT)
        self.doc.recompute()
        self.front = self.group.getItemByLabel("Front")

    def test_sections_get_next_letters(self):
        a = core.add_section(self.page, self.front)
        b = core.add_section(self.page, self.front, direction="Down")
        self.doc.recompute()
        self.assertEqual((a.SectionSymbol, b.SectionSymbol), ("A", "B"))
        self.assertEqual(a.Label, "Section A-A")
        self.assertIn("Up-to-date", a.State)
        self.assertIn("Up-to-date", b.State)
        self.assertGreater(len(a.getVisibleEdges()), 0)

    def test_section_matches_techdraw_direction_rules(self):
        sec = core.add_section(self.page, self.front, direction="Right")
        # TechDraw "Right": normal = -base X direction, X dir = base direction
        self.assertTrue(sec.SectionNormal.isEqual(App.Vector(-1, 0, 0), 1e-9))
        self.assertTrue(sec.XDirection.isEqual(App.Vector(0, -1, 0), 1e-9))

    def test_letters_skip_detail_reference(self):
        detail = self.doc.addObject("TechDraw::DrawViewDetail", "Detail")
        self.page.addView(detail)
        detail.Reference = "A"
        sec = core.add_section(self.page, self.front)
        self.assertEqual(sec.SectionSymbol, "B")

    def test_fit_page_after_section_keeps_everything_inside(self):
        core.add_section(self.page, self.front)
        self.page.Scale = 5.0  # deliberately wrong
        scale = core.fit_page(self.page)
        self.doc.recompute()
        self.assertLess(scale, 0.5 + 1e-9)
        frame, _ = core.drawing_area(self.page.Template.Template, *core.page_size(self.page))
        for v in core.part_views(self.page):
            x0, y0, x1, y1 = core.view_box(v)
            self.assertGreaterEqual(x0, frame[0] - 0.01, v.Label)
            self.assertLessEqual(x1, frame[2] + 0.01, v.Label)
            self.assertGreaterEqual(y0, frame[1] - 0.01, v.Label)
            self.assertLessEqual(y1, frame[3] + 0.01, v.Label)

    def test_fit_page_without_group_raises(self):
        empty = core.create_page(self.doc, "A4", "Landscape")
        with self.assertRaises(ValueError):
            core.fit_page(empty)

    def _title_scale(self):
        texts = dict(self.page.Template.EditableTexts)
        keys = [k for k in texts if k.lower() == "scale"]
        return texts[keys[0]] if keys else None

    def test_scale_down_then_up_steps_one_standard_scale(self):
        fitted = core.fit_page(self.page)
        down = core.step_page_scale(self.page, -1)
        self.assertAlmostEqual(down, core.next_standard_scale(fitted, -1))
        self.assertAlmostEqual(core.view_scale(self.group), down)
        up = core.step_page_scale(self.page, +1)
        self.assertAlmostEqual(up, fitted)

    def test_scale_up_past_fit_refuses(self):
        core.fit_page(self.page)
        with self.assertRaises(ValueError):
            core.step_page_scale(self.page, +1)

    def test_detail_keeps_its_zoom_over_the_page(self):
        core.fit_page(self.page)
        s = core.view_scale(self.front)
        cx, cy = core.view_center(self.front)
        det = core.add_detail(self.page, self.front, (cx, cy), 10.0, (60, 60))
        ratio = core.view_scale(det) / s
        core.step_page_scale(self.page, -1)
        # same zoom over its view, on the nearest standard scale
        want = core.nearest_standard_scale(ratio * core.view_scale(self.front))
        self.assertAlmostEqual(core.view_scale(det), want)
        self.assertGreater(core.view_scale(det), core.view_scale(self.front))
        from DrawingKitTools import standards
        self.assertEqual(det.Caption, standards.detail_caption(
            det.Reference, core.scale_text(core.view_scale(det))))

    def test_detail_step_stays_larger_than_its_view(self):
        core.fit_page(self.page)
        s = core.view_scale(self.front)
        cx, cy = core.view_center(self.front)
        det = core.add_detail(self.page, self.front, (cx, cy), 10.0, (60, 60), scale=2 * s)
        with self.assertRaises(ValueError):
            core.step_view_scale(det, -1)
        up = core.step_view_scale(det, +1)
        self.assertGreater(up, 2 * s)
        with self.assertRaises(ValueError):
            core.step_view_scale(self.front, +1)

    def test_any_scale_goes_in_title_block_as_typed(self):
        value, label = core.parse_scale("1:7")
        scale = core.set_page_scale(self.page, value, label)
        self.assertAlmostEqual(scale, 1 / 7.0)
        self.assertAlmostEqual(core.view_scale(self.front), 1 / 7.0)
        if self._title_scale() is not None:
            self.assertEqual(self._title_scale(), "1 : 7")
        with self.assertRaises(ValueError):
            core.set_page_scale(self.page, 50.0, "50 : 1")

    def test_fill_sheet_is_non_standard_and_nts(self):
        fitted = core.fit_page(self.page)
        scale = core.fill_page(self.page)
        self.assertGreaterEqual(scale, fitted - 1e-12)
        group, extras = core.page_content(self.page)
        self.assertTrue(core.fits_at(self.page, group, extras, scale))
        self.assertFalse(core.fits_at(self.page, group, extras, scale * 1.1))
        if self._title_scale() is not None:
            self.assertEqual(self._title_scale(), "NTS")

    def test_details_moved_clear_after_fill(self):
        core.fit_page(self.page)
        s = core.view_scale(self.front)
        cx, cy = core.view_center(self.front)
        det = core.add_detail(self.page, self.front, (cx, cy), 10.0, (60, 60))
        core.fill_page(self.page)
        box = core._detail_footprint(det, float(det.X), float(det.Y))
        frame, title = core.drawing_area(self.page.Template.Template, *core.page_size(self.page))
        self.assertTrue(frame[0] <= box[0] and box[2] <= frame[2] and
                        frame[1] <= box[1] and box[3] <= frame[3])
        for v in core.part_views(self.page):
            if v.isDerivedFrom("TechDraw::DrawViewDetail"):
                continue
            self.assertFalse(core._overlaps(box, core.view_box(v), 0.0), v.Label)
        if title:
            self.assertFalse(core._overlaps(box, title, 0.0))
        self.assertGreater(core.view_scale(det), core.view_scale(self.front))


class TestParseScale(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(core.parse_scale("1:3"), (1 / 3.0, "1 : 3"))
        self.assertEqual(core.parse_scale(" 3 : 4 "), (0.75, "3 : 4"))
        self.assertEqual(core.parse_scale("1/2.5")[0], 0.4)
        self.assertEqual(core.parse_scale("0,8")[0], 0.8)
        self.assertEqual(core.parse_scale("150%")[0], 1.5)

    def test_bad(self):
        for t in ("", "abc", "1:0", "-2", "1:1e9"):
            with self.assertRaises(ValueError, msg=t):
                core.parse_scale(t)

    def test_steps(self):
        self.assertEqual(core.next_standard_scale(1.0, +1), 2.0)
        self.assertEqual(core.next_standard_scale(1 / 3.0, +1), 0.5)
        self.assertEqual(core.next_standard_scale(1 / 3.0, -1), 0.2)
        self.assertIsNone(core.next_standard_scale(50.0, +1))
        self.assertIsNone(core.next_standard_scale(0.001, -1))


def block_with_hole(doc):
    """100 x 60 x 40 block, off-centre vertical hole r=8 at (30, 20)."""
    import Part
    shp = Part.makeBox(100, 60, 40).cut(
        Part.makeCylinder(8, 60, App.Vector(30, 20, -10)))
    obj = doc.addObject("Part::Feature", "Block")
    obj.Shape = shp
    doc.recompute()
    return obj


class TestProjectedViews(DocTest):
    def setUp(self):
        super().setUp()
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.box], front=FRONT)
        self.doc.recompute()
        self.front = self.group.Anchor

    def test_directions_match_techdraw_for_all_eight(self):
        names = {v: k for k, v in core._TYPE_FOR_SIDE.items()}
        for t in names:
            if not any(v.Type == t for v in self.group.Views):
                self.group.addProjection(t)
        self.doc.recompute()
        for item in self.group.Views:
            if item.Type == "Front":
                continue
            d, x = core.projected_directions(self.front.Direction, self.front.XDirection,
                                             names[item.Type])
            self.assertTrue(App.Vector(item.Direction).normalize().isEqual(d, 1e-9), item.Type)
            self.assertTrue(App.Vector(item.XDirection).normalize().isEqual(x, 1e-9), item.Type)

    def test_placement_side_sectors(self):
        self.assertEqual(core.placement_side((0, 0), (10, 1)), (1, 0))
        self.assertEqual(core.placement_side((0, 0), (-1, -10)), (0, -1))
        self.assertEqual(core.placement_side((0, 0), (10, 9)), (1, 1))
        self.assertEqual(core.placement_side((0, 0), (-10, 8)), (-1, 1))

    def test_from_front_joins_group_first_angle(self):
        # First angle: a view placed LEFT of Front shows the right side.
        fx, fy = core.view_center(self.front)
        item = core.add_projected_view(self.page, self.front, (fx + 150, fy + 7))
        self.assertEqual(item.Type, "Left")      # placed right -> Left view
        self.assertIn(item.Name, [v.Name for v in self.group.Views])
        self.assertEqual(core.view_center(item), (fx + 150, fy))  # snapped level
        with self.assertRaises(ValueError):
            core.add_projected_view(self.page, self.front, (fx + 140, fy))

    def test_from_other_view_is_linked_and_follows(self):
        top = self.group.getItemByLabel("Top")
        tx, ty = core.view_center(top)
        view = core.add_projected_view(self.page, top, (tx + 120, ty - 3))
        self.doc.recompute()
        self.assertEqual(view.TypeId, "TechDraw::DrawViewPart")
        self.assertTrue(core.same(getattr(view, core.PARENT_PROP), top))
        self.assertEqual([c.Name for c in core.linked_children(top)], [view.Name])
        self.assertEqual(getattr(view, core.AXIS_PROP), "H")
        self.assertEqual(float(view.Y), ty)
        self.assertIn("Up-to-date", view.State)
        # drag the projected view off its line: it snaps back level
        view.Y = ty + 30
        self.assertTrue(core.enforce_alignment(view))
        self.assertEqual(float(view.Y), ty)
        # move the whole group: the linked view follows on its axis
        self.group.Y = float(self.group.Y) + 20
        self.assertTrue(core.enforce_alignment(self.group))
        self.assertAlmostEqual(float(view.Y), core.view_center(top)[1], places=6)

    def test_page_model_round_trip(self):
        top = self.group.getItemByLabel("Top")
        p = App.Vector(123.0, 45.0, 25.0)   # z is the depth for the top view
        px, py = core.model_to_page(top, p)
        back = core.page_to_model(top, px, py)
        self.assertAlmostEqual(back.x, p.x, places=6)
        self.assertAlmostEqual(back.y, p.y, places=6)


class TestCenterMarks(DocTest):
    def setUp(self):
        super().setUp()
        self.block = block_with_hole(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.block], front=FRONT)
        self.doc.recompute()

    def test_hole_gets_cross_in_top_and_axis_in_front(self):
        top = self.group.getItemByLabel("Top")
        front = self.group.Anchor
        marks, lines = core.center_features(top)
        self.assertEqual(len(marks), 1)
        lx, ly, half = marks[0]
        self.assertAlmostEqual(lx, 30 - 50, places=6)   # hole x minus block middle
        self.assertAlmostEqual(ly, 20 - 30, places=6)
        self.assertGreater(half, 8)
        self.assertEqual(lines, [])
        fm, fl = core.center_features(front)
        self.assertEqual(fm, [])
        self.assertEqual(len(fl), 1)
        (x0, y0), (x1, y1) = fl[0]
        self.assertAlmostEqual(x0, x1, places=6)   # vertical axis line
        self.assertAlmostEqual(x0, 30 - 50, places=6)
        self.assertGreater(abs(y1 - y0), 40)        # through the whole block + overshoot

    def test_cosmetic_lines_added_and_replaced(self):
        top = self.group.getItemByLabel("Top")
        self.assertEqual(core.add_center_marks(top), 2)
        first = list(getattr(top, core.MARKS_PROP))
        self.assertEqual(core.add_center_marks(top), 2)    # rerun replaces, no duplicates
        second = list(getattr(top, core.MARKS_PROP))
        self.assertEqual(len(set(first) & set(second)), 0)
        for tag in second:
            self.assertIsNotNone(top.getCosmeticEdge(tag))
        right = self.group.getItemByLabel("Right")
        self.assertEqual(core.add_center_marks(right), 1)   # hole seen from the side

    def test_fillets_get_no_marks(self):
        import Part
        box = Part.makeBox(50, 40, 20)
        edge = [e for e in box.Edges if abs(e.BoundBox.ZLength - 20) < 1e-6][0]
        filleted = box.makeFillet(5, [edge])
        obj = self.doc.addObject("Part::Feature", "Fillet")
        obj.Shape = filleted
        page = core.create_page(self.doc, "A4", "Landscape")
        g = core.add_standard_views(page, [obj], front=FRONT)
        self.doc.recompute()
        self.assertEqual(core.center_features(g.getItemByLabel("Top")), ([], []))


class TestCutSections(DocTest):
    def setUp(self):
        super().setUp()
        self.block = block_with_hole(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.block], front=FRONT)
        self.doc.recompute()
        self.top = self.group.getItemByLabel("Top")

    def test_snap_points(self):
        self.assertEqual(core.snap_cut_points([(0, 0), (1, 10), (9, 12), (10, 30)]),
                         [(0, 0), (0, 10), (9, 10), (9, 30)])

    def test_one_click_cut_through_the_hole(self):
        hx, hy = core.model_to_page(self.top, App.Vector(30, 20, 40))
        tx, ty = core.view_center(self.top)
        sec = core.add_cut_section(self.page, self.top, [(hx, hy + 3)], (tx + 120, ty + 9))
        self.doc.recompute()
        self.assertEqual(sec.SectionDirection, "Right")
        self.assertAlmostEqual(sec.SectionOrigin.x, 30, places=6)   # through the hole
        self.assertEqual((float(sec.X), float(sec.Y)), (tx + 120, ty))  # level with base
        self.assertIn("Up-to-date", sec.State)
        self.assertGreater(len(sec.getVisibleEdges()), 0)

    def test_horizontal_cut_placed_below(self):
        tx, ty = core.view_center(self.top)
        sec = core.add_cut_section(self.page, self.top, [(tx - 30, ty), (tx + 30, ty + 2)],
                                   (tx + 5, ty - 90))
        self.assertEqual(sec.SectionDirection, "Down")
        self.assertEqual(float(sec.X), tx)

    def test_stepped_cut_makes_complex_section(self):
        hx, hy = core.model_to_page(self.top, App.Vector(30, 20, 40))
        tx, ty = core.view_center(self.top)
        # vertical through the hole, step right, vertical again near the end
        pts = [(hx, hy + 30), (hx, hy), (tx + 30, hy), (tx + 30, hy - 30)]
        sec = core.add_cut_section(self.page, self.top, pts, (tx + 150, ty))
        self.doc.recompute()
        self.assertEqual(sec.TypeId, "TechDraw::DrawComplexSection")
        self.assertEqual(sec.ProjectionStrategy, "Offset")
        wire = sec.CuttingToolWireObject.Shape
        self.assertEqual(wire.ShapeType, "Wire")
        self.assertIn("Up-to-date", sec.State)
        self.assertGreater(len(sec.getVisibleEdges()), 0)
        # ends stop just past the part (60 mm deep block, 5 mm paper margin)
        ys = [v.Point.y for v in wire.Vertexes]
        s = core.view_scale(self.top)
        self.assertAlmostEqual(max(ys), 60 + 5.0 / s, places=6)
        self.assertAlmostEqual(min(ys), 0 - 5.0 / s, places=6)

    def test_u_shaped_cut_is_refused_with_a_reason(self):
        tx, ty = core.view_center(self.top)
        pts = [(tx - 20, ty + 20), (tx - 20, ty), (tx + 20, ty), (tx + 20, ty + 20)]
        with self.assertRaises(ValueError) as cm:
            core.add_cut_section(self.page, self.top, pts, (tx + 150, ty))
        self.assertIn("across the part", str(cm.exception))


def coupon(doc):
    """Disc r25 x 6 thick: centre bore r8 THRU, 3 x r3.2 THRU on a 17 mm
    pitch circle, one blind hole r2 x 4 deep at (-17, 0)."""
    import math
    import Part
    shp = Part.makeCylinder(25, 6).cut(Part.makeCylinder(8, 20, App.Vector(0, 0, -5)))
    for k in range(3):
        a = math.radians(90 + 120 * k)
        shp = shp.cut(Part.makeCylinder(3.2, 20, App.Vector(17 * math.cos(a), 17 * math.sin(a), -5)))
    shp = shp.cut(Part.makeCylinder(2, 4.01, App.Vector(0, -20, 2)))  # blind from the top
    obj = doc.addObject("Part::Feature", "Coupon")
    obj.Shape = shp
    doc.recompute()
    return obj


class TestHoles(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import holes
        self.holes = holes
        self.part = coupon(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.part], front=FRONT)
        self.doc.recompute()
        self.top = self.group.getItemByLabel("Top")

    def test_finds_through_and_blind_holes(self):
        hs = self.holes.find_holes(self.part.Shape)
        self.assertEqual(len(hs), 5)
        bolt = [h for h in hs if abs(h.radius - 3.2) < 1e-6]
        self.assertEqual(len(bolt), 3)
        self.assertTrue(all(h.through for h in bolt))
        blind = [h for h in hs if abs(h.radius - 2) < 1e-6][0]
        self.assertFalse(blind.through)
        self.assertAlmostEqual(blind.depth, 4.0, places=2)

    def test_callout_texts(self):
        groups = self.holes.group_holes(self.holes.find_holes(self.part.Shape))
        texts = sorted(self.holes.callout_text(g) for g in groups)
        self.assertIn("3X ⌀%.2w THRU", texts)
        self.assertIn("⌀%.2w THRU", texts)
        self.assertTrue(any(t.startswith("⌀%.2w ↧ 4") for t in texts), texts)

    def test_callouts_on_top_view(self):
        dims = self.holes.add_callouts(self.page, self.top)
        self.doc.recompute()
        self.assertEqual(len(dims), 3)          # bolt holes, bore, blind hole
        texts = [d.FormatSpec for d in dims]
        self.assertIn("3X ⌀%.2w THRU", texts)
        for d in dims:
            self.assertEqual(d.Type, "Diameter")
            self.assertIn("Up-to-date", d.State)
        bolt = [d for d in dims if d.FormatSpec.startswith("3X")][0]
        self.assertAlmostEqual(bolt.getRawValue(), 6.4, places=6)

    def test_callout_labels_stay_on_the_drawing_area(self):
        dims = self.holes.add_callouts(self.page, self.top)
        cx, cy = core.view_center(self.top)
        regions = core.page_regions(self.page)
        spots = []
        for d in dims:
            x, y = cx + float(d.X), cy + float(d.Y)
            self.assertTrue(any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in regions),
                            d.FormatSpec)
            spots.append((x, y))
        # no two labels on top of each other
        for i in range(len(spots)):
            for j in range(i + 1, len(spots)):
                self.assertGreater(abs(spots[i][0] - spots[j][0]) + abs(spots[i][1] - spots[j][1]), 10)

    def test_selected_edge_expands_to_its_group(self):
        h = [h for h in self.holes.holes_seen_end_on(self.top) if abs(h.radius - 3.2) < 1e-6][0]
        edge = self.holes.circle_edge_name(self.top, h)
        self.assertIsNotNone(edge)
        self.assertEqual(len(self.holes.holes_for_edges(self.top, [edge])), 3)

    def test_bolt_circle_detection(self):
        import math
        bc = self.holes.bolt_circles
        tri = [(10 * math.cos(math.radians(90 + 120 * k)), 10 * math.sin(math.radians(90 + 120 * k)))
               for k in range(3)]
        found = bc(tri)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][1], 10, places=6)
        self.assertEqual(bc([(-20, -10), (20, -10), (20, 10), (-20, 10)]), [])   # rectangle
        self.assertEqual(bc([(-10, -10), (10, -10), (10, 10), (-10, 10)]), [])   # square grid
        self.assertEqual(len(bc([(10, 0), (0, 10), (-10, 0), (0, -10)])), 1)     # on axes

    def test_centre_marks_draw_pitch_circle(self):
        marks, _ = core.center_features(self.top, with_radius=True)
        segs, circles = core.plan_center_marks(marks, 1.0)
        self.assertEqual(len(circles), 1)
        self.assertAlmostEqual(circles[0][2], 17, places=3)
        # 3 radial lines + centre cross (2) + blind hole cross (2)
        self.assertEqual(len(segs), 3 + 2 + 2)
        self.assertGreater(core.add_center_marks(self.top), len(segs))  # lines + circle


class TestGdt(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import gdt
        self.gdt = gdt
        self.page = core.create_page(self.doc, "A3", "Landscape")

    def test_every_characteristic_draws(self):
        import xml.etree.ElementTree as ET
        for key, _ in self.gdt.CHARACTERISTICS:
            svg, w, h = self.gdt.frame_svg(key, "0.05", key == "position", "MMC", ["A", "B"])
            ET.fromstring(svg)
            self.assertEqual(h, 7.0)
            self.assertAlmostEqual(w, 7 + (4 * 2.2 + 3 + (3.4 if key == "position" else 0) + 4.2) + 14)

    def test_frame_and_datum_symbols(self):
        f = self.gdt.add_frame(self.page, 200, 150, "position", "0.05", True, "", ["A", "B"])
        a = self.gdt.add_datum(self.page, 100, 100)
        b = self.gdt.add_datum(self.page, 120, 100)
        self.doc.recompute()
        self.assertIn("Up-to-date", f.State)
        self.assertEqual((a.Label, b.Label), ("Datum A", "Datum B"))
        self.assertEqual(self.gdt.next_datum_letter(self.doc), "C")
        self.assertIn("0.05", list(f.EditableTexts))
        # no view near (100, 100): flag stands up, triangle base on the point
        self.assertAlmostEqual(float(a.Y) - a_half_height(a), 100.0, places=6)

    def test_datum_points_away_from_the_view(self):
        g = self.gdt
        box = (100, 100, 200, 150)
        self.assertEqual(g.datum_side(box, 150, 100.5), "down")   # bottom edge
        self.assertEqual(g.datum_side(box, 150, 149.5), "up")
        self.assertEqual(g.datum_side(box, 100.5, 120), "left")
        self.assertEqual(g.datum_side(box, 199.5, 120), "right")
        for side in ("up", "down", "left", "right"):
            svg, w, h, (dx, dy) = g.datum_svg("A", side)
            # the triangle base is on the symbol's outer edge on the feature side
            if side == "up":
                self.assertAlmostEqual(dy, -h / 2.0)
            if side == "down":
                self.assertAlmostEqual(dy, h / 2.0)
            if side == "left":
                self.assertAlmostEqual(dx, w / 2.0)
            if side == "right":
                self.assertAlmostEqual(dx, -w / 2.0)


def spin_event_loop(seconds):
    """Give TechDraw's worker threads time to finish (headless FreeCAD has
    no running Qt event loop, so just wait)."""
    import time
    time.sleep(seconds)


def a_half_height(sym):
    import re
    m = re.search(r'height="([\d.]+)mm"', sym.Symbol)
    return float(m.group(1)) / 2.0


class TestDetail(DocTest):
    def test_scale_choice(self):
        self.assertEqual(core.detail_scale(1.0, 5), 5.0)     # 10 mm circle -> 5:1 (50 mm)
        self.assertEqual(core.detail_scale(1.0, 50), 2.0)    # big area: at least 2x
        self.assertEqual(core.detail_scale(0.5, 10), 2.0)    # 20 mm circle -> 2:1 (40 mm)

    def test_detail_of_a_hole(self):
        part = coupon(self.doc)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [part], front=FRONT)
        self.doc.recompute()
        top = g.getItemByLabel("Top")
        hx, hy = core.model_to_page(top, App.Vector(0, 17, 6))   # top bolt hole
        r = 6 * core.view_scale(top)
        det = core.add_detail(page, top, (hx, hy), r, (300, 200))
        self.doc.recompute()
        spin_event_loop(3.0)   # detail cut runs in a thread, finished via Qt signals
        self.assertEqual(det.Reference, "A")
        self.assertAlmostEqual(det.Radius, 6, places=6)
        self.assertAlmostEqual(det.AnchorPoint.y, 17, places=6)
        self.assertIn("Up-to-date", det.State)
        circles = [e for e in det.getVisibleEdges() if "Circle" in e.Curve.TypeId]
        hole = [c for c in circles if abs(c.Curve.Radius - 3.2 * det.Scale) < 1e-3]
        self.assertEqual(len(hole), 1)
        self.assertLess(hole[0].Curve.Center.Length, 1e-3)    # hole in the middle
        # the next section/detail letter skips A
        self.assertEqual(core.next_free_letter(core.used_letters(self.doc)), "B")


class TestBestFront(DocTest):
    def test_flat_flange_shows_its_round_face(self):
        import Part
        flange = self.doc.addObject("Part::Feature", "Flange")
        flange.Shape = Part.makeCylinder(40, 10)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [flange])
        self.doc.recompute()
        self.assertTrue(App.Vector(g.Anchor.Direction).normalize().isEqual(App.Vector(0, 0, 1), 1e-9))
        import Part as P
        front = P.makeCompound(g.Anchor.getVisibleEdges()).BoundBox
        self.assertAlmostEqual(front.XLength, front.YLength, places=3)   # a circle
        # the side view stands upright next to it (thin and tall)
        right = g.getItemByLabel("Right")
        rb = P.makeCompound(right.getVisibleEdges()).BoundBox
        self.assertLess(rb.XLength, rb.YLength)
        self.assertAlmostEqual(float(right.Y), 0.0)            # level with the front view

    def test_plate_front_is_its_big_face_longest_side_across(self):
        d, x = core.best_front(self.box.Shape)                   # 400 x 300 x 50
        w, h = core.projected_extent(self.box.Shape, d, x)
        self.assertEqual((round(w), round(h)), (400, 300))

    def test_tall_part_is_turned_upright(self):
        import Part
        post = Part.makeBox(20, 30, 200)       # tallest along Z
        d, x = core.best_front(post)
        w, h = core.projected_extent(post, d, x)
        self.assertGreaterEqual(w, h)          # longest side across the page
        self.assertEqual(round(w), 200)

    def test_group_directions_match_techdraw_for_any_front(self):
        import Part
        flange = self.doc.addObject("Part::Feature", "Flange")
        flange.Shape = Part.makeCylinder(40, 10)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [flange], isometric=True)
        self.doc.recompute()
        d, x = g.Anchor.Direction, g.Anchor.XDirection
        for v in g.Views:
            if v.Type == "Front":
                continue
            ed, ex = core.projected_directions(d, x, core._GRID[v.Type])
            self.assertTrue(App.Vector(v.Direction).normalize().isEqual(ed, 1e-9), v.Type)
            self.assertTrue(App.Vector(v.XDirection).normalize().isEqual(ex, 1e-9), v.Type)


def flange(doc, bolt_r=2.0, n=6):
    """Disc r40 x 10 with a boss r20 up to z=25, bore r6, n holes on r30."""
    import math
    import Part
    shp = Part.makeCylinder(40, 10).fuse(Part.makeCylinder(20, 25)).cut(
        Part.makeCylinder(6, 40, App.Vector(0, 0, -5)))
    for k in range(n):
        a = math.radians(360.0 / n * k)
        shp = shp.cut(Part.makeCylinder(bolt_r, 40, App.Vector(30 * math.cos(a), 30 * math.sin(a), -5)))
    obj = doc.addObject("Part::Feature", "Flange")
    obj.Shape = shp.removeSplitter()
    doc.recompute()
    return obj


class TestAnnotations(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import annot
        self.annot = annot
        self.part = flange(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.part])
        self.doc.recompute()
        self.front = self.group.Anchor
        self.right = self.group.getItemByLabel("Right")

    def test_datum_sticks_to_the_view(self):
        import xml.etree.ElementTree as ET
        x0, y0, x1, y1 = core.view_box(self.right)
        d = self.annot.add_datum(self.page, (x0 + x1) / 2, y0)
        ET.fromstring(d.Symbol)
        self.assertEqual(d.DrawingKitAnchorKind, "view")
        self.assertEqual(list(d.EditableTexts), ["A"])
        before = (float(d.X), float(d.Y))
        self.group.X = float(self.group.X) + 12
        self.annot.refresh(self.annot.attached_to(self.group))
        self.assertAlmostEqual(float(d.X) - before[0], 12.0, places=6)
        self.assertAlmostEqual(float(d.Y) - before[1], 0.0, places=6)

    def test_dragging_moves_the_box_not_the_anchor(self):
        x0, y0, x1, y1 = core.view_box(self.right)
        d = self.annot.add_datum(self.page, (x0 + x1) / 2, y0)
        anchor_before = self.annot.anchor_page(d)[0]
        d.X = float(d.X) + 15
        self.assertTrue(self.annot.dragged(d))
        self.assertAlmostEqual(d.DrawingKitOffset.x, 15.0, places=6)
        self.assertEqual(self.annot.anchor_page(d)[0], anchor_before)

    def test_frame_with_count_and_datum_modifiers(self):
        fr = self.annot.add_frame(
            self.page, (300, 230),
            {"char": "position", "tol": "0.05", "dia": True, "mod": "MMC",
             "datums": ["A", "B", ""], "datum_mods": ["", "MMC", ""], "count": "6X"},
            anchor=core.model_to_page(self.front, App.Vector(30, 0, 10)))
        self.assertEqual(list(fr.EditableTexts), ["0.05", "A", "B", "6X"])
        self.assertEqual(fr.DrawingKitAnchorKind, "view")
        self.assertIn("<circle", fr.Symbol)   # the Ⓜ circles

    def test_datum_on_a_diameter_dimension(self):
        from DrawingKitTools import holes
        h = [x for x in holes.holes_seen_end_on(self.front) if abs(x.radius - 6) < 1e-6][0]
        dim = self.doc.addObject("TechDraw::DrawViewDimension", "Dim")
        dim.Type = "Diameter"
        dim.MeasureType = "Projected"
        dim.References2D = [(self.front, holes.circle_edge_name(self.front, h))]
        self.page.addView(dim)
        dim.X, dim.Y = 40, 30
        self.doc.recompute()
        (a0, a1, u) = self.annot.dimension_ends(dim)
        s = core.view_scale(self.front)
        self.assertAlmostEqual(math_dist(a0, a1), 12 * s, places=6)   # Ø12 bore
        d = self.annot.add_datum(self.page, 0, 0, "B", dimension=dim)
        self.assertEqual(d.DrawingKitAnchorKind, "dim")
        self.assertEqual(self.annot.next_datum_letter(self.doc), "A")

    def _two_rows(self, composite):
        row1 = {"char": "position", "tol": "0.25", "dia": True, "mod": "MMC",
                "datums": ["A", "B", "C"], "datum_mods": ["", "", ""]}
        row2 = {"char": "position" if composite else "perpendicularity", "tol": "0.05",
                "dia": True, "mod": "MMC", "datums": ["A", "", ""], "datum_mods": ["", "", ""]}
        return {"char": "position", "rows": [row1, row2], "composite": composite, "count": "6X"}

    def test_composite_frame_shares_one_symbol(self):
        import re
        fr = self.annot.add_frame(self.page, (300, 230), self._two_rows(True))
        texts = list(fr.EditableTexts)
        self.assertEqual(texts, ["0.25", "A", "B", "C", "0.05", "A", "6X"])
        # one characteristic symbol drawn (a single position circle+cross group)
        self.assertEqual(fr.Symbol.count("transform=\"translate"), 1)
        h = float(re.search(r'height="([\d.]+)mm"', fr.Symbol).group(1))
        self.assertGreater(h, 2 * 7.0)        # two rows tall

    def test_stacked_frame_has_a_symbol_per_row(self):
        fr = self.annot.add_frame(self.page, (300, 230), self._two_rows(False))
        self.assertEqual(fr.Symbol.count("transform=\"translate"), 2)

    def test_datum_target(self):
        x0, y0, x1, y1 = core.view_box(self.right)
        t = self.annot.add_target(self.page, (x0 + x1) / 2, y1, "A1", "⌀3")
        self.assertEqual(sorted(t.EditableTexts), ["A1", "⌀3"])

    def test_hole_callout_count_for_frames(self):
        from DrawingKitTools import holes
        hs = holes.holes_seen_end_on(self.front)
        groups = holes.group_holes(hs)
        self.assertIn(6, [len(g) for g in groups])

    @staticmethod
    def _svg_size(sym):
        import re
        w = float(re.search(r'width="([\d.]+)mm"', sym.Symbol).group(1))
        h = float(re.search(r'height="([\d.]+)mm"', sym.Symbol).group(1))
        return w, h

    def test_zone_modifiers_widen_the_frame(self):
        import xml.etree.ElementTree as ET
        base = {"char": "position", "tol": "0.1", "dia": True, "mod": "MMC",
                "datums": ["A", "B", ""], "tsize": 3.5}
        plain = self.annot.add_frame(self.page, (300, 230), base)
        full = self.annot.add_frame(self.page, (300, 200), dict(
            base, zones=["CZ", "P", "F", "T"], proj="15"))
        ET.fromstring(full.Symbol)
        self.assertGreater(self._svg_size(full)[0], self._svg_size(plain)[0] + 4 * 4.2)
        for t in (">CZ<", ">15<"):
            self.assertIn(t, full.Symbol)
        # Ⓜ Ⓟ Ⓕ Ⓣ: four circles more than the plain frame's Ⓜ
        self.assertEqual(full.Symbol.count("<circle") - plain.Symbol.count("<circle"), 3)
        prof = self.annot.add_frame(self.page, (300, 170), {
            "char": "profile_surface", "tol": "0.2", "zones": ["U"], "uval": "0.05",
            "datums": ["A", "", ""], "tsize": 3.5})
        self.assertEqual(prof.Symbol.count("<circle"), 1)   # Ⓤ
        self.assertIn(">0.05<", prof.Symbol)

    def test_circled_letters_are_centred(self):
        sk = self.annot.Sketch()
        for letter in "MLPFTUES":
            sk.items, sk.xs, sk.ys = [], [], []
            self.annot.circled(sk, (10.0, 20.0), 1.8, letter)
            lines = [it for it in sk.items if it[0] == "line"]
            self.assertTrue(lines, letter)
            xs = [p[0] for it in lines for p in it[1:3]]
            ys = [p[1] for it in lines for p in it[1:3]]
            self.assertAlmostEqual((min(xs) + max(xs)) / 2, 10.0, delta=0.15, msg=letter)
            self.assertAlmostEqual((min(ys) + max(ys)) / 2, 20.0, delta=0.15, msg=letter)

    def test_all_around_and_between(self):
        x0, y0, x1, y1 = core.view_box(self.front)
        p = {"char": "profile_surface", "tol": "0.1", "datums": ["A", "", ""],
             "all_around": True, "between": ["J", "K"], "tsize": 3.5}
        fr = self.annot.add_frame(self.page, (x1 + 30, y1 + 10), p, anchor=(x1, (y0 + y1) / 2))
        self.assertIn("<circle", fr.Symbol)        # the all-around circle
        texts = list(fr.EditableTexts)
        self.assertIn("J", texts)
        self.assertIn("K", texts)

    def test_frame_under_a_dimension_moves_with_it(self):
        from DrawingKitTools import holes
        h = [x for x in holes.holes_seen_end_on(self.front) if abs(x.radius - 6) < 1e-6][0]
        dim = self.doc.addObject("TechDraw::DrawViewDimension", "Dim")
        dim.Type = "Diameter"
        dim.MeasureType = "Projected"
        dim.References2D = [(self.front, holes.circle_edge_name(self.front, h))]
        self.page.addView(dim)
        dim.X, dim.Y = 40, 30
        self.doc.recompute()
        fr = self.annot.add_frame(self.page, None, {"char": "position", "tol": "0.1", "dia": True,
                                                   "datums": ["A", "", ""]}, dimension=dim)
        self.assertEqual(fr.DrawingKitAnchorKind, "dimlabel")
        self.assertNotIn("fill:#000000\" d", fr.Symbol)
        cx, cy = core.view_center(self.front)
        self.assertLess(float(fr.Y), cy + 30)       # below the value
        bx = float(fr.X)
        dim.X = 55
        self.annot.refresh([fr])
        self.assertAlmostEqual(float(fr.X) - bx, 15.0, places=6)

    def test_plain_note_with_several_lines(self):
        import xml.etree.ElementTree as ET
        n = self.annot.add_note(self.page, (60, 60), ["ALL FILLETS R1", "BREAK SHARP EDGES"])
        ET.fromstring(n.Symbol)
        self.assertEqual(list(n.EditableTexts), ["ALL FILLETS R1", "BREAK SHARP EDGES"])
        self.assertEqual(n.DrawingKitAnchorKind, "free")
        self.assertNotIn("<path", n.Symbol)              # no leader
        self.assertAlmostEqual(float(n.X), 60.0, delta=6)
        self.assertTrue(self.annot.is_note(n))

    def test_note_with_leader_follows_the_view(self):
        x0, y0, x1, y1 = core.view_box(self.right)
        n = self.annot.add_note(self.page, (x1 + 30, y1 + 10), ["CHAMFER 1X45°"],
                                anchor=((x0 + x1) / 2, y1))
        self.assertEqual(n.DrawingKitAnchorKind, "view")
        self.assertIn("fill:#000000", n.Symbol)        # the arrow head
        before = float(n.X)
        self.group.X = float(self.group.X) + 10
        self.annot.refresh(self.annot.attached_to(self.group))
        self.assertAlmostEqual(float(n.X) - before, 10.0, places=6)

    def test_edit_note_text(self):
        n = self.annot.add_note(self.page, (60, 60), ["ONE"])
        self.annot.set_note_text(n, "FIRST LINE\nSECOND LINE\nTHIRD")
        self.assertEqual(list(n.EditableTexts), ["FIRST LINE", "SECOND LINE", "THIRD"])
        self.assertEqual(self.annot.note_text(n), "FIRST LINE\nSECOND LINE\nTHIRD")

    def test_note_size_and_font(self):
        n = self.annot.add_note(self.page, (60, 60), ["NOTE"], size=7, font="DejaVu Sans")
        self.assertEqual(self.annot.text_style(n), (7.0, "DejaVu Sans"))
        self.assertIn("font-size:7px", n.Symbol)
        self.assertIn("DejaVu Sans", n.Symbol)

    def test_frame_grows_with_its_text(self):
        fr = self.annot.add_frame(self.page, (300, 230),
                                  {"char": "flatness", "tol": "0.05", "datums": ["", "", ""]})
        self.annot.set_text_style(fr, size=3.5)
        w1, h1 = self._svg_size(fr)
        self.assertTrue(self.annot.set_text_style(fr, size=7.0, font="Arial"))
        w2, h2 = self._svg_size(fr)
        self.assertAlmostEqual((w2 - 2) / (w1 - 2), 2.0, delta=0.05)   # 1 mm margins
        self.assertAlmostEqual((h2 - 2) / (h1 - 2), 2.0, delta=0.05)
        self.assertIn("Arial", fr.Symbol)
        self.assertEqual(list(fr.EditableTexts), ["0.05"])

    def test_datum_and_target_scale(self):
        x0, y0, x1, y1 = core.view_box(self.right)
        d = self.annot.add_datum(self.page, (x0 + x1) / 2, y0)
        self.annot.set_text_style(d, size=5.0)
        self.assertIn("font-size:5px", d.Symbol)
        self.assertEqual(list(d.EditableTexts), ["A"])
        t = self.annot.add_target(self.page, (x0 + x1) / 2, y1, "A1", "⌀3")
        self.annot.set_text_style(t, size=2.5)
        self.assertEqual(sorted(t.EditableTexts), ["A1", "⌀3"])

    def test_defaults_for_new_symbols(self):
        g = App.ParamGet(self.annot.PREFS)
        old = (g.GetFloat("TextSize", 0.0), g.GetString("Font", ""))
        try:
            self.annot.set_defaults(6.0, "DejaVu Sans")
            n = self.annot.add_note(self.page, (60, 60), ["X"])
            self.assertEqual(self.annot.text_style(n), (6.0, "DejaVu Sans"))
        finally:
            g.RemFloat("TextSize")
            g.RemString("Font")
            if old[0]:
                g.SetFloat("TextSize", old[0])
            if old[1]:
                g.SetString("Font", old[1])

    def test_techdraw_annotation_style(self):
        a = self.doc.addObject("TechDraw::DrawViewAnnotation", "Anno")
        self.page.addView(a)
        a.Text = ["HELLO"]
        self.assertTrue(self.annot.set_text_style(a, 6.0, "DejaVu Sans"))
        self.assertEqual(self.annot.text_style(a), (6.0, "DejaVu Sans"))


def math_dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


class TestAuxiliaryView(DocTest):
    def test_looks_square_on_at_the_slanted_face(self):
        import Part
        blk = Part.makeBox(60, 40, 30)
        cutter = Part.makeBox(40, 60, 40)
        cutter.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), 45)
        cutter.translate(App.Vector(40, -10, 30))
        obj = self.doc.addObject("Part::Feature", "Wedge")
        obj.Shape = blk.cut(cutter)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [obj], front=FRONT)
        self.doc.recompute()
        front = g.Anchor
        slanted = None
        i = 0
        while i < 100:
            try:
                e = front.getEdgeByIndex(i)
            except Exception:
                break
            if len(e.Vertexes) == 2:
                p0, p1 = e.Vertexes[0].Point, e.Vertexes[1].Point
                if abs(abs(p1.x - p0.x) - abs(p1.y - p0.y)) < 1e-6 and abs(p1.x - p0.x) > 1:
                    slanted = "Edge%d" % i
            i += 1
        self.assertIsNotNone(slanted)
        cx, cy = core.view_center(front)
        aux = core.add_auxiliary_view(page, front, slanted, (cx + 80, cy + 80))
        self.doc.recompute()
        # the chamfer face's outward normal is (1, 0, 1)/sqrt2: we look straight at it
        n = App.Vector(1, 0, 1).normalize()
        self.assertTrue(App.Vector(aux.Direction).normalize().isEqual(n, 1e-6))
        self.assertLessEqual(abs(float(aux.Rotation)), 90.0)
        self.assertIn("Up-to-date", aux.State)
        aux.X = float(aux.X) + 10           # dragged off its line...
        self.assertTrue(core.follow_parent(aux))   # ...comes back onto it
        ang = math_angle(aux, front)
        self.assertAlmostEqual(ang, 45.0, places=3)


def math_angle(view, parent):
    import math
    (x, y), (px, py) = core.view_center(view), core.view_center(parent)
    return math.degrees(math.atan2(y - py, x - px))


class TestAlignedSection(DocTest):
    def test_makes_an_aligned_complex_section(self):
        import math
        part = flange(self.doc)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [part])
        self.doc.recompute()
        f = g.Anchor
        P = lambda x, y: core.model_to_page(f, App.Vector(x, y, 10))
        a60 = math.radians(60)
        pts = [P(30 * math.cos(a60), 30 * math.sin(a60)), P(0, 0), P(0.5, -30)]
        fx, fy = core.view_center(f)
        sec = core.add_aligned_section(page, f, pts, (fx + 120, fy))
        self.assertEqual(sec.TypeId, "TechDraw::DrawComplexSection")
        self.assertEqual(sec.ProjectionStrategy, "Aligned")
        wire = sec.CuttingToolWireObject.Shape
        self.assertEqual(len(wire.Edges), 2)
        with self.assertRaises(ValueError):
            core.add_aligned_section(page, f, pts[:2], (fx + 120, fy))


class TestTitleAndTables(DocTest):
    def test_title_block_from_part_and_sheet_numbers(self):
        part = flange(self.doc)
        p1 = core.create_page(self.doc, "A3", "Landscape")
        core.add_standard_views(p1, [part])
        p2 = core.create_page(self.doc, "A4", "Landscape")
        t1, t2 = dict(p1.Template.EditableTexts), dict(p2.Template.EditableTexts)
        self.assertEqual(t1["title"], "Flange")
        self.assertEqual(t1["drawing_number"], "FLANGE")
        self.assertEqual((t1["sheet_number"], t2["sheet_number"]), ("1 / 2", "2 / 2"))
        # a field the user typed in is kept
        texts = dict(p1.Template.EditableTexts)
        texts["title"] = "My flange"
        p1.Template.EditableTexts = texts
        core.fill_title_block(p1, part)
        self.assertEqual(dict(p1.Template.EditableTexts)["title"], "My flange")

    def test_hole_table(self):
        from DrawingKitTools import tables
        part = coupon(self.doc)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [part], front=FRONT)
        self.doc.recompute()
        top = g.getItemByLabel("Top")
        rows = tables.hole_rows(top)
        self.assertEqual([r[0] for r in rows], ["A1", "B1", "B2", "B3", "C1"])
        bore = rows[0]
        self.assertAlmostEqual(bore[1], 25.0, places=3)   # disc r25: centre is 25 from lower-left
        self.assertAlmostEqual(bore[2], 25.0, places=3)
        self.assertEqual(bore[3], "⌀16 THRU")
        table, tags = tables.add_hole_table(page, top)
        self.assertEqual(len(tags), 5)
        self.assertIn("HOLE TABLE", table.Symbol)

    def test_revisions(self):
        from DrawingKitTools import tables
        page = core.create_page(self.doc, "A3", "Landscape")
        t = tables.add_revision(page, "Initial release", "RS", "2026-11-22")
        t = tables.add_revision(page, "Holes enlarged", "RS", "2026-12-01")
        p = __import__("json").loads(t.DrawingKitParams)
        self.assertEqual([r[0] for r in p["rows"]], ["A", "B"])
        self.assertEqual(dict(page.Template.EditableTexts)["revision_index"], "B")
        self.assertEqual(len([v for v in page.Views if getattr(v, "DrawingKitAnnot", "") == "revtable"]), 1)

    def test_revision_eco_column_and_old_tables(self):
        import json
        from DrawingKitTools import tables, annot
        page = core.create_page(self.doc, "A3", "Landscape")
        t = tables.add_revision(page, "Initial release", "TH", "2026-03-02", eco="26-0110")
        p = json.loads(t.DrawingKitParams)
        self.assertEqual(p["headers"], ["REV", "ECO", "DESCRIPTION", "DATE", "APPROVED"])
        self.assertEqual(p["rows"][0], ["A", "26-0110", "Initial release", "2026-03-02", "TH"])
        self.assertIn(">26-0110<", t.Symbol)
        # a table made before the ECO column gets one
        p = {"title": "", "headers": ["REV", "DESCRIPTION", "DATE", "APPROVED"],
             "rows": [["A", "First", "2026-01-01", "RS"]], "widths": [12.0, 70.0, 24.0, 24.0],
             "editable": True, "tsize": 3.5}
        setattr(t, annot.PARAMS, json.dumps(p))
        t.EditableTexts = []
        annot.render(t)
        t = tables.add_revision(page, "Second", "RS", "2026-02-01", eco="E-2")
        p = json.loads(t.DrawingKitParams)
        self.assertEqual(p["headers"][1], "ECO")
        self.assertEqual(p["rows"], [["A", "", "First", "2026-01-01", "RS"],
                                     ["B", "E-2", "Second", "2026-02-01", "RS"]])


class TestEditDimension(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import dims
        self.dims = dims
        page = core.create_page(self.doc, "A4", "Landscape")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        self.dim = self.doc.addObject("TechDraw::DrawViewDimension", "Dim")
        self.dim.Type = "DistanceX"
        self.dim.References2D = [(g.Anchor, "Edge0")]
        page.addView(self.dim)
        self.doc.recompute()

    def test_round_trip_and_text(self):
        d = self.dim
        self.dims.set_dimension(d, before="3X ", after=" H7", decimals=3, tol="deviation",
                                upper=0.02, lower=0.01)
        s = self.dims.read_dimension(d)
        self.assertEqual((s["before"], s["after"], s["decimals"], s["tol"]),
                         ("3X ", " H7", 3, "deviation"))
        self.assertAlmostEqual(s["upper"], 0.02)
        self.assertAlmostEqual(s["lower"], 0.01)
        self.assertFalse(d.EqualTolerance)

    def test_basic_drops_the_tolerance(self):
        d = self.dim
        self.dims.set_dimension(d, tol="symmetric", upper=0.1, basic=True)
        self.assertTrue(d.TheoreticalExact)
        self.assertEqual(self.dims.read_dimension(d)["tol"], "none")

    def test_reference_and_symmetric(self):
        d = self.dim
        self.dims.set_dimension(d, decimals=1, tol="symmetric", upper=0.05, reference=True)
        s = self.dims.read_dimension(d)
        self.assertTrue(s["reference"])
        self.assertEqual(s["tol"], "symmetric")
        self.assertEqual(d.FormatSpec, "(%.1f)")

    def test_limits(self):
        d = self.dim
        nominal = float(d.getRawValue())
        self.dims.set_dimension(d, decimals=3, tol="limits", upper=0.002, lower=0.001)
        self.assertTrue(self.dims.is_limits(d))
        self.assertEqual(d.FormatSpecOverTolerance, "%.3f" % (nominal + 0.002))
        self.assertEqual(d.FormatSpecUnderTolerance, "%.3f" % (nominal - 0.001))
        s = self.dims.read_dimension(d)
        self.assertEqual((s["tol"], s["decimals"]), ("limits", 3))
        self.assertAlmostEqual(s["upper"], 0.002)
        self.assertAlmostEqual(s["lower"], 0.001)
        # the model changes size: the limits follow
        d.FormatSpecOverTolerance = "0.000"
        page = d.findParentPage() if hasattr(d, "findParentPage") else d.InList[0]
        self.assertEqual(self.dims.refresh_limits(page), 1)
        self.assertEqual(d.FormatSpecOverTolerance, "%.3f" % (nominal + 0.002))
        # back to a plain dimension
        self.dims.set_dimension(d, decimals=2)
        self.assertFalse(self.dims.is_limits(d))
        self.assertFalse(d.Arbitrary)

    def test_special_characteristic_flag(self):
        from DrawingKitTools import annot, clearance
        d = self.dim
        page = d.InList[0] if not hasattr(d, "findParentPage") else d.findParentPage()
        f = annot.add_flag(page, "SC", dimension=d)
        self.assertEqual(f.DrawingKitAnnot, "flag")
        self.assertIn(">SC<", f.Symbol)
        self.assertEqual(annot.flags_on(d), [f])
        text = clearance.dimension_label_box(d)
        body = annot.body_box(f)
        self.assertGreater(body[0], text[2])                 # just after the value
        self.assertLess(body[0] - text[2], 5.0)
        # the dimension moves: the flag follows
        x = float(f.X)
        d.X = float(d.X) + 10.0
        annot.refresh(annot.attached_to(d))
        self.assertAlmostEqual(float(f.X) - x, 10.0, places=3)
        # a tolerance makes the text longer: the flag moves out past it
        x = float(f.X)
        self.dims.set_dimension(d, decimals=2, tol="symmetric", upper=0.02)
        annot.refresh(annot.attached_to(d))
        self.assertGreater(float(f.X), x + 1.0)
        # a flag on its own, e.g. in the notes
        g = annot.add_flag(page, "CC", at=(100.0, 50.0))
        self.assertIn(">CC<", g.Symbol)
        b = annot.body_box(g)
        self.assertAlmostEqual((b[0] + b[2]) / 2.0, 100.0, places=3)

    def test_limits_on_a_diameter_keep_the_symbol(self):
        d = self.dim
        d.Type = "Diameter"
        self.dims.set_dimension(d, decimals=2, tol="limits", upper=0.1, lower=0.1, nominal=10.0)
        self.assertEqual(d.FormatSpec, "⌀ ")
        self.assertEqual((d.FormatSpecOverTolerance, d.FormatSpecUnderTolerance), ("10.10", "9.90"))
        self.dims.set_dimension(d, before="⌀", decimals=2, tol="limits", upper=0.1, lower=0.1,
                                nominal=10.0)
        self.assertEqual(d.FormatSpec, "⌀ ")


def plate_two_holes(doc):
    """80 x 50 x 10 plate, holes r5 at (20, 25) and (60, 25)."""
    import Part
    shp = Part.makeBox(80, 50, 10).cut(Part.makeCylinder(5, 30, App.Vector(20, 25, -5))) \
        .cut(Part.makeCylinder(5, 30, App.Vector(60, 25, -5)))
    obj = doc.addObject("Part::Feature", "Plate")
    obj.Shape = shp
    doc.recompute()
    return obj


class TestSmartDimension(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import smartdim
        self.sd = smartdim
        self.part = plate_two_holes(self.doc)
        self.page = core.create_page(self.doc, "A4", "Landscape")
        self.g = core.add_standard_views(self.page, [self.part])
        self.doc.recompute()
        self.v = self.g.Anchor            # looks at the 80 x 50 face (best front)
        self.ents = self.sd.entities(self.v)

    def find(self, kind, near):
        return self.sd.pick(self.v, near, [e for e in self.ents if e.kind == kind], tol=1.0)

    def test_two_circles_centre_to_centre(self):
        a = self.find("circle", (-20 + 5, 0))     # hole edges (view centre is the plate middle)
        b = self.find("circle", (20 + 5, 0))
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        t, ents, _ = self.sd.plan([a, b], (0, 10))
        self.assertEqual(t, "DistanceX")
        self.assertEqual(self.sd.preview_value([a, b], (0, 10)), "40.00")
        d = self.sd.add_dimension(self.page, self.v, [a, b], (0, 10))
        self.doc.recompute()
        self.assertAlmostEqual(d.getRawValue(), 40.0, places=6)
        self.assertIn("Up-to-date", d.State)

    def test_circle_gives_diameter(self):
        a = self.find("circle", (-15, 0))
        d = self.sd.add_dimension(self.page, self.v, [a], (-30, 20))
        self.doc.recompute()
        self.assertEqual(d.Type, "Diameter")
        self.assertAlmostEqual(d.getRawValue(), 10.0, places=6)

    def test_line_length_and_mouse_choice(self):
        top = self.find("line", (0, 25))
        self.assertEqual(self.sd.plan([top], (0, 40))[0], "DistanceX")
        d = self.sd.add_dimension(self.page, self.v, [top], (0, 40))
        self.doc.recompute()
        self.assertAlmostEqual(d.getRawValue(), 80.0, places=6)

    def test_points_horizontal_vertical_true(self):
        p = self.sd.Ent("vertex", 0, [(0.0, 0.0)])
        q = self.sd.Ent("vertex", 1, [(30.0, 40.0)])
        self.assertEqual(self.sd.plan([p, q], (15, 60))[0], "DistanceX")   # above
        self.assertEqual(self.sd.plan([p, q], (50, 20))[0], "DistanceY")   # right
        self.assertEqual(self.sd.plan([p, q], (50, 60))[0], "Distance")    # diagonal
        self.assertEqual(self.sd.preview_value([p, q], (50, 60)), "50.00")

    def test_circle_to_line_and_parallel_lines_and_angle(self):
        hole = self.find("circle", (-15, 0))
        top = self.find("line", (0, 25))
        bottom = self.find("line", (0, -25))
        left = self.find("line", (-40, 0))
        d = self.sd.add_dimension(self.page, self.v, [hole, top], (-30, 10))
        self.doc.recompute()
        self.assertAlmostEqual(d.getRawValue(), 25.0, places=6)
        d2 = self.sd.add_dimension(self.page, self.v, [top, bottom], (45, 0))
        self.doc.recompute()
        self.assertAlmostEqual(d2.getRawValue(), 50.0, places=6)
        self.assertEqual(self.sd.plan([top, left], (0, 0))[0], "Angle")
        self.assertEqual(self.sd.preview_value([top, left], (0, 0)), "90.0°")

    def test_hovering_a_circle_edge_picks_the_circle(self):
        # the circle's seam point sits on its edge; it must not be picked
        e = self.sd.pick(self.v, (-15.0, 0.0), self.ents)
        self.assertEqual(e.kind, "circle")

    def test_points_beat_edges_when_picking(self):
        # right at a hole's centre: the centre is picked, not the circle edge
        e = self.sd.pick(self.v, (-20, 0), self.ents, tol=6.0)
        self.assertTrue(e.is_point())


class TestSpeed(DocTest):
    def test_toggle(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        self.assertFalse(core.is_fast(page))
        core.set_fast(page, True)
        self.assertTrue(core.is_fast(page))
        self.assertTrue(all(v.CoarseView for v in core.part_views(page)))
        core.set_fast(page, False)
        self.doc.recompute()
        self.assertFalse(core.is_fast(page))
        self.assertFalse(any(v.CoarseView for v in core.part_views(page)))
        self.assertTrue(all("Up-to-date" in v.State for v in core.part_views(page)))


class TestDrafting(DocTest):
    """Ordinate/baseline/chain, centerlines, chamfers, surface, weld, parts list."""

    def setUp(self):
        super().setUp()
        from DrawingKitTools import drafting, smartdim, annot
        self.dr, self.sd, self.annot = drafting, smartdim, annot
        self.part = plate_two_holes(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.g = core.add_standard_views(self.page, [self.part])
        self.doc.recompute()
        self.v = self.g.Anchor            # 80 x 50 face, centre at the plate middle
        self.ents = self.sd.entities(self.v)

    def find(self, kind, near):
        return self.sd.pick(self.v, near, [e for e in self.ents if e.kind == kind], tol=1.0)

    def test_baseline_dimensions_stack(self):
        left = self.find("line", (-40, 0))
        holes = [self.find("circle", (-15, 0)), self.find("circle", (25, 0))]
        right = self.find("line", (40, 0))
        dims = self.dr.add_baseline(self.page, self.v, left, holes + [right], "x")
        self.doc.recompute()
        self.assertEqual([round(d.getRawValue(), 3) for d in dims], [20.0, 60.0, 80.0])
        self.assertEqual(len({round(float(d.Y), 3) for d in dims}), 3)   # three rows

    def test_chain_dimensions_one_row(self):
        left = self.find("line", (-40, 0))
        holes = [self.find("circle", (25, 0)), self.find("circle", (-15, 0))]
        dims = self.dr.add_baseline(self.page, self.v, left, holes, "x", chain=True)
        self.doc.recompute()
        self.assertEqual([round(d.getRawValue(), 3) for d in dims], [20.0, 40.0])
        self.assertEqual(len({round(float(d.Y), 3) for d in dims}), 1)

    def test_vertical_baseline(self):
        bottom = self.find("line", (0, -25))
        hole = self.find("circle", (-15, 0))
        dims = self.dr.add_baseline(self.page, self.v, bottom, [hole], "y")
        self.doc.recompute()
        self.assertEqual(dims[0].Type, "DistanceY")
        self.assertAlmostEqual(dims[0].getRawValue(), 25.0, places=6)

    def test_ordinate_follows_view(self):
        import xml.etree.ElementTree as ET
        left = self.find("line", (-40, 0))
        holes = [self.find("circle", (-15, 0)), self.find("circle", (25, 0)), self.find("line", (40, 0))]
        o = self.dr.add_ordinate(self.page, self.v, left, holes, "x")
        ET.fromstring(o.Symbol)
        for t in (">0<", ">20<", ">60<", ">80<"):
            self.assertIn(t, o.Symbol)
        self.assertIn("rotate(-90", o.Symbol)
        x = float(o.X)
        self.g.X = float(self.g.X) + 10
        self.annot.refresh(self.annot.attached_to(self.g))
        self.assertAlmostEqual(float(o.X) - x, 10.0, places=6)

    def test_dimension_keeps_its_gap_when_the_scale_changes(self):
        left = self.find("line", (-40, 0))
        hole = self.find("circle", (-15, 0))
        dim = self.dr.add_baseline(self.page, self.v, left, [hole], "x")[0]
        top = core.view_box(self.v)[3]
        cy = core.view_center(self.v)[1]
        gap = cy + float(dim.Y) - top          # text above the view's top edge
        mid = float(dim.X)                     # over the view: scales with it
        s0 = core.view_scale(self.v)
        core.step_page_scale(self.page, -1)
        k = core.view_scale(self.v) / s0
        top = core.view_box(self.v)[3]
        cy = core.view_center(self.v)[1]
        self.assertAlmostEqual(cy + float(dim.Y) - top, gap, places=6)
        self.assertAlmostEqual(float(dim.X), mid * k, places=6)

    def test_remap(self):
        self.assertAlmostEqual(core._remap(30.0, 20.0, 20.0, 0.5), 20.0)   # 10 past the edge
        self.assertAlmostEqual(core._remap(-30.0, 20.0, 20.0, 0.5), -20.0)
        self.assertAlmostEqual(core._remap(8.0, 20.0, 20.0, 0.5), 4.0)

    def test_callouts_move_outside_the_dimension_rows(self):
        from DrawingKitTools import clearance
        circ = self.find("circle", (-15, 0))
        dim = self.doc.addObject("TechDraw::DrawViewDimension", "Dimension")
        dim.Type = "Diameter"
        dim.References2D = [(self.v, ["Edge%d" % circ.index])]
        self.page.addView(dim)
        top = core.view_box(self.v)[3]
        cy = core.view_center(self.v)[1]
        dim.X, dim.Y = -15.0 * core.view_scale(self.v), top - cy + 9.0   # where a row goes
        left = self.find("line", (-40, 0))
        holes = [circ, self.find("circle", (25, 0)), self.find("line", (40, 0))]
        o = self.dr.add_ordinate(self.page, self.v, left, holes, "x")
        row = clearance.symbol_box(o)[3]
        self.assertGreater(clearance.dimension_label_box(dim)[1], row - 1e-6)

    def test_ordinate_spreads_close_values(self):
        left = self.find("line", (-40, 0))
        o = self.dr.add_ordinate(self.page, self.v, left, [self.find("line", (-40, 0))], "y")
        self.assertIn(">0<", o.Symbol)

    def test_centerline_between_two_lines(self):
        top = self.find("line", (0, 25))
        bottom = self.find("line", (0, -25))
        before = len(self.v.CenterLines)
        self.dr.add_centerline(self.v, top, bottom)
        self.assertEqual(len(self.v.CenterLines), before + 1)

    def test_chamfer_text(self):
        E = self.sd.Ent
        self.assertEqual(self.dr.chamfer_text(E("line", 0, [(0, 0), (1, 1)])), "1 x 45°")
        self.assertEqual(self.dr.chamfer_text(E("line", 0, [(0, 0), (1.5, 1.5)]), "c"), "C1.5")
        self.assertEqual(self.dr.chamfer_text(E("line", 0, [(0, 0), (2, 1.1547)])), "2 x 30°")
        with self.assertRaises(ValueError):
            self.dr.chamfer_text(E("line", 0, [(0, 0), (5, 0)]))

    def test_surface_symbol_on_top_edge(self):
        import xml.etree.ElementTree as ET
        x0, y0, x1, y1 = core.view_box(self.v)
        s = self.dr.add_surface(self.page, (x0 + x1) / 2 + 10, y1,
                                {"ra": "Ra 1.6", "process": "remove", "lay": "="})
        ET.fromstring(s.Symbol)
        self.assertEqual(s.DrawingKitAnchorKind, "view")
        self.assertEqual(list(s.EditableTexts), ["Ra 1.6"])
        self.assertEqual(tuple(s.DrawingKitOffset)[:2], (0.0, 0.0))   # sits on the top face
        # on the right edge it stands off on a leader with an arrow
        s2 = self.dr.add_surface(self.page, x1, (y0 + y1) / 2,
                                 {"ra": "Ra 3.2", "process": "noremove", "method": "ground"})
        self.assertNotEqual(tuple(s2.DrawingKitOffset)[:2], (0.0, 0.0))
        self.assertEqual(sorted(s2.EditableTexts), ["Ra 3.2", "ground"])

    def test_weld_symbol(self):
        import xml.etree.ElementTree as ET
        x0, y0, x1, y1 = core.view_box(self.v)
        w = self.dr.add_weld(self.page, x1, y1, {"type": "fillet", "size": "a5", "length": "50",
                                                  "side": "arrow", "all_around": True, "field": True})
        ET.fromstring(w.Symbol)
        self.assertIn("stroke-dasharray", w.Symbol)   # ISO identification line
        self.assertEqual(sorted(w.EditableTexts), ["50", "a5"])
        for kind, _ in self.dr.WELDS:
            for side in ("arrow", "other", "both"):
                w2 = self.dr.add_weld(self.page, x1, y0, {"type": kind, "size": "5", "side": side})
                ET.fromstring(w2.Symbol)


class TestPartsList(DocTest):
    def setUp(self):
        super().setUp()
        import Part
        from DrawingKitTools import drafting
        self.dr = drafting
        asm = self.doc.addObject("App::Part", "Assembly")
        base = self.doc.addObject("Part::Feature", "Base")
        base.Shape = Part.makeBox(100, 60, 10)
        p1 = self.doc.addObject("Part::Feature", "Pin001")
        p1.Shape = Part.makeCylinder(4, 20, App.Vector(20, 30, 10))
        p2 = self.doc.addObject("Part::Feature", "Pin002")
        p2.Shape = Part.makeCylinder(4, 20, App.Vector(80, 30, 10))
        for o in (base, p1, p2):
            asm.addObject(o)
        self.doc.recompute()
        self.asm = asm
        self.page = core.create_page(self.doc, "A3", "Landscape")
        view = self.doc.addObject("TechDraw::DrawViewPart", "View")
        self.page.addView(view)
        view.Source = [asm]
        view.Direction = App.Vector(0, -1, 0)
        view.XDirection = App.Vector(1, 0, 0)
        view.Scale = 1.0
        view.X, view.Y = 150, 150
        self.doc.recompute()
        self.view = view

    def test_identical_parts_counted(self):
        items = self.dr.bom_items([self.asm])
        self.assertEqual([(i["name"], i["qty"]) for i in items], [("Base", 1), ("Pin", 2)])

    def test_parts_list_table(self):
        table, items = self.dr.add_parts_list(self.page, self.view)
        self.assertIn("PARTS LIST", table.Symbol)
        self.assertIn(">Pin<", table.Symbol)
        self.assertEqual(table.DrawingKitAnnot, "bom")

    def test_copy_numbers_dropped_but_part_numbers_kept(self):
        b = self.dr._base_name
        self.assertEqual([b("Pin001"), b("Bolt_2"), b("Bolt 12")], ["Pin", "Bolt", "Bolt"])
        self.assertEqual([b("PRT-1001"), b("SHCS 10-32 X 3-8"), b("M6"), b("Rail")],
                         ["PRT-1001", "SHCS 10-32 X 3-8", "M6", "Rail"])

    def test_parts_list_part_number_and_description(self):
        import json
        base = [o for o in self.dr._leaf_parts([self.asm]) if o.Label.startswith("Base")][0]
        base.addProperty("App::PropertyString", "PartNumber", "Part")
        base.PartNumber = "PRT-1001"
        base.Label2 = "GUIDE BODY"
        table, items = self.dr.add_parts_list(self.page, self.view, asme=True)
        p = json.loads(table.DrawingKitParams)
        self.assertEqual(p["headers"], ["ITEM", "PART NO.", "DESCRIPTION", "MATERIAL", "QTY"])
        row = [r for r in p["rows"] if r[1] == "PRT-1001"][0]
        self.assertEqual((row[2], row[4]), ("GUIDE BODY", "1"))
        pin = [r for r in p["rows"] if r[1] == "Pin"][0]
        self.assertEqual(pin[4], "2")
        table2, _ = self.dr.add_parts_list(self.page, self.view, asme=False)
        self.assertEqual(json.loads(table2.DrawingKitParams)["headers"][:2], ["ITEM", "QTY"])

    def test_part_under_a_point_and_balloons(self):
        from DrawingKitTools import core as c
        items = self.dr.bom_items([self.asm])
        # the pin seen from the front, above the base
        px, py = c.model_to_page(self.view, App.Vector(20, 30, 25))
        it, obj = self.dr.part_at(self.view, px, py, items)
        self.assertEqual(it["name"], "Pin")
        px, py = c.model_to_page(self.view, App.Vector(50, 30, 5))
        it, obj = self.dr.part_at(self.view, px, py, items)
        self.assertEqual(it["name"], "Base")
        balloons = self.dr.auto_balloons(self.page, self.view)
        self.assertEqual(sorted(b.Text for b in balloons), ["1", "2"])
        # stored in view units (page offset / scale); bubbles outside the view
        s = c.view_scale(self.view)
        cx, cy = c.view_center(self.view)
        x0, y0, x1, y1 = c.view_box(self.view)
        for b in balloons:
            bx, by = cx + float(b.X) * s, cy + float(b.Y) * s
            self.assertFalse(x0 < bx < x1 and y0 < by < y1, b.Text)

    def test_balloon_places_do_not_overlap(self):
        taken = []
        for _ in range(5):
            taken.append(self.dr.balloon_place((10, 50), (0, 0, 100, 100), taken))
        for i, a in enumerate(taken):
            self.assertLess(a[0], 0)                      # left of the box
            for b in taken[i + 1:]:
                self.assertGreaterEqual(math_dist(a, b), 10.0 - 1e-9)


def hole_block(doc):
    """60 x 40 x 20 block (top at z=20) with, seen from the top:
    a counterbored through hole (x=10), a countersunk through hole (x=30),
    a spotfaced through hole (x=50) and a plain through hole (x=50, y=30)."""
    import Part
    s = Part.makeBox(60, 40, 20)
    z = App.Vector(0, 0, 1)
    for x, y in ((10, 10), (30, 10), (50, 10), (50, 30)):
        s = s.cut(Part.makeCylinder(3.3, 30, App.Vector(x, y, -5), z))
    s = s.cut(Part.makeCylinder(5.5, 6.4, App.Vector(10, 10, 20 - 6.4), z))       # counterbore
    s = s.cut(Part.makeCone(3.3, 6.2, 2.9, App.Vector(30, 10, 20 - 2.9), z))      # 90° countersink
    s = s.cut(Part.makeCylinder(8.0, 1.0, App.Vector(50, 10, 19.0), z))          # spotface
    obj = doc.addObject("Part::Feature", "HoleBlock")
    obj.Shape = s
    doc.recompute()
    return obj


class TestAsmeHoles(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import holes
        self.h = holes
        self.obj = hole_block(self.doc)
        self.found = sorted(holes.find_holes(self.obj.Shape), key=lambda x: (x.centre.x, x.centre.y))

    def at(self, x, y):
        for h in self.found:
            p = h.centre
            d = App.Vector(x, y, 0) - App.Vector(p.x, p.y, 0)
            if d.Length < 1e-3:
                return h

    def test_wider_bores_are_merged_into_their_hole(self):
        self.assertEqual(len(self.found), 4)            # not 6: bores folded in
        self.assertTrue(all(abs(h.radius - 3.3) < 1e-6 for h in self.found))
        self.assertTrue(all(h.through for h in self.found))

    def test_counterbore(self):
        h = self.at(10, 10)
        self.assertAlmostEqual(h.cbore[0], 11.0, places=3)
        self.assertAlmostEqual(h.cbore[1], 6.4, places=3)
        self.assertFalse(h.spotface)
        self.assertEqual(self.h.callout_text([h]), "⌀%.2w THRU ⌴ ⌀11 ↧ 6.4")

    def test_countersink(self):
        h = self.at(30, 10)
        self.assertAlmostEqual(h.csink[0], 12.4, places=3)
        self.assertAlmostEqual(h.csink[1], 90.0, delta=0.1)
        self.assertEqual(self.h.callout_text([h]), "⌀%.2w THRU ⌵ ⌀12.4 X 90°")

    def test_spotface(self):
        h = self.at(50, 10)
        self.assertTrue(h.spotface)
        self.assertEqual(self.h.callout_text([h]), "⌀%.2w THRU SF ⌀16 ↧ 1")

    def test_plain_and_counted_separately(self):
        groups = self.h.group_holes(self.found)
        self.assertEqual(len(groups), 4)                 # same pilot, different heads
        plain = self.at(50, 30)
        self.assertIsNone(plain.cbore)
        self.assertIsNone(plain.csink)
        self.assertEqual(self.h.callout_text([plain, plain]), "2X ⌀%.2w THRU")


class TestAsmeFrame(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import annot
        self.annot = annot
        self.page = core.create_page(self.doc, "A3", "Landscape")

    def test_asme_symbols_in_the_frame(self):
        import xml.etree.ElementTree as ET
        plain = self.annot.add_frame(self.page, (200, 200), {
            "char": "profile_surface", "tol": "0.2", "datums": ["A", "B", ""], "tsize": 3.5})
        fr = self.annot.add_frame(self.page, (200, 150), {
            "char": "profile_surface", "tol": "0.2", "mod": "RFS", "zones": ["DP", "ST"],
            "datums": ["A", "B", ""], "datum_mods": ["TR", "RFS", ""], "cf": True, "tsize": 3.5})
        ET.fromstring(fr.Symbol)
        self.assertIn(">ST<", fr.Symbol)
        self.assertIn(">CF<", fr.Symbol)
        # two Ⓢ circles (tolerance and datum B)
        self.assertEqual(fr.Symbol.count("<circle") - plain.Symbol.count("<circle"), 2)
        w_plain = float(__import__("re").search(r'width="([\d.]+)mm"', plain.Symbol).group(1))
        w_full = float(__import__("re").search(r'width="([\d.]+)mm"', fr.Symbol).group(1))
        self.assertGreater(w_full, w_plain + 10)
        self.assertEqual(list(fr.EditableTexts), ["0.2", "A", "B"])

    def test_cycle_buttons_offer_asme_modifiers(self):
        from DrawingKitTools import gdt
        self.assertEqual(gdt.MODIFIERS["RFS"], "S")


class TestTablesAndLayout(DocTest):
    def test_empty_rects_avoid_blockers(self):
        frame = (0, 0, 400, 280)
        title = (220, 0, 400, 50)
        table = (290, 50, 400, 90)
        rects = core.empty_rects(frame, [title, table], pad=0.0)
        for r in rects:
            for b in (title, table):
                self.assertFalse(r[0] < b[2] and b[0] < r[2] and r[1] < b[3] and b[1] < r[3])
        self.assertEqual(rects[0], (0, 90, 400, 280))       # biggest: above both

    def test_fit_keeps_views_off_the_parts_list(self):
        import Part
        from DrawingKitTools import drafting
        asm = self.doc.addObject("App::Part", "Assembly")
        b = self.doc.addObject("Part::Feature", "Base")
        b.Shape = Part.makeBox(100, 60, 10)
        p1 = self.doc.addObject("Part::Feature", "Pin001")
        p1.Shape = Part.makeCylinder(4, 20, App.Vector(20, 30, 10))
        p2 = self.doc.addObject("Part::Feature", "Pin002")
        p2.Shape = Part.makeCylinder(4, 20, App.Vector(80, 30, 10))
        for o in (b, p1, p2):
            asm.addObject(o)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [asm])
        self.doc.recompute()
        drafting.add_parts_list(page, g.Anchor)
        core.make_room_for_tables(page)
        self.doc.recompute()
        tb = core.table_boxes(page, pad=0.0)
        for v in core.part_views(page):
            vb = core.view_box(v)
            for t in tb:
                self.assertFalse(vb[0] < t[2] and t[0] < vb[2] and vb[1] < t[3] and t[1] < vb[3], v.Label)


class TestExport(DocTest):
    def test_sheet_names(self):
        from DrawingKitTools import export
        p1 = core.create_page(self.doc, "A4", "Landscape")
        p2 = core.create_page(self.doc, "A4", "Landscape")
        self.assertEqual(export.sheet_paths("C:/x/Part.pdf", [p1]), ["C:/x/Part.pdf"])
        self.assertEqual(export.sheet_paths("C:/x/Part.pdf", [p1, p2]),
                         ["C:/x/Part - Sheet 1.pdf", "C:/x/Part - Sheet 2.pdf"])

    def test_dxf_export_headless(self):
        import tempfile
        from DrawingKitTools import export
        page = core.create_page(self.doc, "A3", "Landscape")
        core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        d = tempfile.mkdtemp(prefix="dk_export_")
        done = export.export_pages([page], os.path.join(d, "Box.dxf"), "dxf")
        self.assertEqual(len(done), 1)
        self.assertGreater(os.path.getsize(done[0]), 1000)
        with open(done[0], encoding="utf-8", errors="ignore") as f:
            self.assertIn("ENTITIES", f.read())


def threaded_plate(doc):
    """PartDesign body: 60 x 40 x 20 plate, two M6 holes through and one
    M8 blind (drill 15, thread 12) from the top face."""
    import Part
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Base")
    pts = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 40, 0), App.Vector(0, 40, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 20
    doc.recompute()

    def hole(name, centres, size, through, depth=0, tdepth=0):
        hs = body.newObject("Sketcher::SketchObject", name + "Sketch")
        hs.Placement = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        for c in centres:
            hs.addGeometry(Part.Circle(App.Vector(c[0], c[1], 0), App.Vector(0, 0, 1), 3))
        h = body.newObject("PartDesign::Hole", name)
        h.Profile = hs
        h.ThreadType = "ISOMetricProfile"
        h.Threaded = True
        doc.recompute()
        h.ThreadSize = size
        h.ThreadClass = "6H"
        if through:
            h.DepthType = "ThroughAll"
        else:
            h.DepthType = "Dimension"
            h.Depth = depth
            h.ThreadDepthType = "Dimension"
            h.ThreadDepth = tdepth
        doc.recompute()
        return h

    hole("M6Holes", [(10, 10), (50, 10)], "M6x1.0", True)
    hole("M8Hole", [(30, 28)], "M8x1.25", False, 15, 12)
    doc.recompute()
    return body


class TestThreads(DocTest):
    def test_thread_callouts(self):
        from DrawingKitTools import holes
        body = threaded_plate(self.doc)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [body])
        self.doc.recompute()
        view = [v for v in core.part_views(page) if holes.holes_seen_end_on(v)][0]
        hs = holes.holes_seen_end_on(view)
        texts = sorted(holes.callout_text(gr) for gr in holes.group_holes(hs))
        self.assertEqual(texts, ["2X M6 - 6H THRU", "⌀%.2w ↧ 15 M8 - 6H ↧ 12"])
        made = holes.add_callouts(page, view)
        self.doc.recompute()
        thru = [d for d in made if "THRU" in d.FormatSpec][0]
        self.assertTrue(thru.Arbitrary)

    def test_make_room_keeps_annotations_on_the_sheet_and_off_tables(self):
        from DrawingKitTools import holes, tables
        body = threaded_plate(self.doc)
        page = core.create_page(self.doc, "A4", "Landscape")
        core.add_standard_views(page, [body])
        self.doc.recompute()
        view = [v for v in core.part_views(page) if holes.holes_seen_end_on(v)][0]
        made = holes.add_callouts(page, view)
        cy = core.view_center(view)[1]
        top = core.view_box(view)[3]
        for d in made:                       # push the callouts off the top of the sheet
            d.Y = top - cy + 200.0
        tables.add_hole_table(page, view)
        self.doc.recompute()
        self.assertTrue(core.annotation_clashes(page))
        core.make_room_for_tables(page)
        for d in made:
            d.Y = min(float(d.Y), top - cy + 20.0)   # back near the view, as placed
        core.make_room_for_tables(page)
        self.assertFalse(core.annotation_clashes(page))

    def test_designations(self):
        from DrawingKitTools import holes

        class F:
            pass
        f = F()
        f.ThreadType, f.ThreadSize, f.ThreadClass = "ISOMetricFineProfile", "M8x1.0", "6H"
        self.assertEqual(holes.thread_designation(f), "M8x1 - 6H")
        f.ThreadType, f.ThreadSize, f.ThreadClass = "UNC", "1/4", "2B"
        self.assertEqual(holes.thread_designation(f), "1/4 UNC - 2B")


class TestTitleBlockDefaults(DocTest):
    def setUp(self):
        super().setUp()
        import tempfile
        from DrawingKitTools import titleblock
        self.tb = titleblock
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, "mine.json")
        self.company = os.path.join(self.tmp, "company.json")
        self._saved = (titleblock.user_file, titleblock.company_file, titleblock.wants_tolerance_row)
        titleblock.user_file = lambda: self.user
        titleblock.company_file = lambda: self.company

    def tearDown(self):
        self.tb.user_file, self.tb.company_file, self.tb.wants_tolerance_row = self._saved
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_template_with_tolerance_row(self):
        from DrawingKitTools import templates
        path = templates.template_path("A3", "Landscape", True, self.tmp, tol_row=True)
        self.assertEqual(templates.parse_name(path), ("A3", "Landscape", True))
        self.assertEqual(templates.parse_name(templates.template_path("ANSI B", "Portrait", False, self.tmp)),
                         ("ANSI B", "Portrait", False))
        _, title = core.drawing_area(path, 420, 297)
        self.assertAlmostEqual(title[3] - title[1], 45.0, places=3)   # five rows
        page = core.create_page(self.doc, "A3", "Landscape")
        page.Template.Template = path
        texts = dict(page.Template.EditableTexts)
        for k in ("units", "tol_1", "tol_2", "tol_3", "tol_angle", "finish", "title"):
            self.assertIn(k, texts)

    def test_company_then_mine_on_top(self):
        self.tb.write_file(self.company, {"legal_owner_1": "ACME", "finish": "Ra 1.6",
                                          "title": "not shared"})
        self.tb.save_user_defaults({"finish": "Ra 0.8"})
        vals = self.tb.load_defaults(asme=True)
        self.assertEqual(vals["legal_owner_1"], "ACME")
        self.assertEqual(vals["finish"], "Ra 0.8")
        self.assertEqual(vals["tol_3"], "±0.005")
        self.assertNotIn("title", vals)

    def test_new_sheet_gets_defaults_and_keeps_typed_fields(self):
        self.tb.wants_tolerance_row = lambda: True
        self.tb.write_file(self.company, {"legal_owner_1": "ACME", "tol_1": "±.03"})
        page = core.create_page(self.doc, "A3", "Landscape")
        texts = dict(page.Template.EditableTexts)
        self.assertEqual(texts["legal_owner_1"], "ACME")
        self.assertEqual(texts["tol_1"], "±.03")
        texts["legal_owner_1"] = "Typed Ltd"
        page.Template.EditableTexts = texts
        self.tb.apply_defaults(page)
        self.assertEqual(dict(page.Template.EditableTexts)["legal_owner_1"], "Typed Ltd")

    def test_tolerance_row_on_and_off_keeps_values(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        self.tb.set_fields(page, {"title": "Bracket", "legal_owner_1": "ACME"})
        self.assertTrue(self.tb.set_tolerance_row(page, True))
        self.assertTrue(self.tb.has_tolerance_row(page))
        texts = dict(page.Template.EditableTexts)
        self.assertEqual((texts["title"], texts["legal_owner_1"]), ("Bracket", "ACME"))
        self.tb.set_fields(page, {"tol_1": "±.02"})
        self.assertTrue(self.tb.set_tolerance_row(page, False))
        self.assertFalse(self.tb.has_tolerance_row(page))
        self.assertEqual(dict(page.Template.EditableTexts)["title"], "Bracket")

    def test_ansi_title_block(self):
        from DrawingKitTools import templates
        path = templates.template_path("ANSI C", "Landscape", False, self.tmp, style="ansi")
        self.assertTrue(path.endswith("_ansi.svg"))
        self.assertEqual(templates.parse_name(path), ("ANSI C", "Landscape", False))
        _, title = core.drawing_area(path, *templates.page_size("ANSI C", "Landscape"))
        self.assertAlmostEqual(title[2] - title[0], templates.ANSI_W, places=3)
        self.assertAlmostEqual(title[3] - title[1], templates.ANSI_H, places=3)
        # too narrow for it: the ISO block
        self.assertFalse(templates.template_path("ANSI A", "Portrait", False, self.tmp,
                                                 style="ansi").endswith("_ansi.svg"))
        page = core.create_page(self.doc, "ANSI C", "Landscape")
        self.tb.set_fields(page, {"title": "Saw guide", "legal_owner_1": "ACME"})
        self.assertTrue(self.tb.set_template_variant(page, style_="ansi"))
        texts = dict(page.Template.EditableTexts)
        for k in ("checked_by", "company_address", "title_2", "tol_4", "surface_note",
                  "edge_note", "sheet_size", "finish"):
            self.assertIn(k, texts)
        self.assertEqual(texts["title"], "Saw guide")
        self.assertEqual(texts["legal_owner_1"], "ACME")
        self.assertEqual(texts["sheet_size"], "C")
        self.assertEqual(texts["sheet_number"], "1 OF 1")
        self.assertTrue(self.tb.is_ansi(page))
        core.create_page(self.doc, "ANSI C", "Landscape")
        core.renumber_sheets(self.doc)
        self.assertEqual(dict(page.Template.EditableTexts)["sheet_number"], "1 OF 2")

    def test_ansi_gets_inch_defaults(self):
        page = core.create_page(self.doc, "ANSI B", "Landscape")
        self.tb.set_template_variant(page, style_="ansi")
        self.tb.apply_defaults(page, self.tb.load_defaults(asme=True))
        texts = dict(page.Template.EditableTexts)
        self.assertEqual(texts["tol_4"], "±0.0005")
        self.assertEqual(texts["general_tolerances"], "ASME Y14.5-2018")
        self.assertIn("Y14.36", texts["surface_note"])

    def test_tolerance_table_by_size(self):
        import json
        from DrawingKitTools import tables
        m = tables.iso2768("m")
        self.assertEqual(m["linear"][0], ["0.5", "3", "0.1"])
        self.assertEqual(m["linear"][-1], ["2000", "4000", "2"])
        self.assertEqual(len(tables.iso2768("f")["linear"]), 7)     # no 2000-4000 in f
        self.assertEqual(tables.iso2768("v")["linear"][0], ["3", "6", "0.5"])
        page = core.create_page(self.doc, "A3", "Landscape")
        t = tables.set_tolerance_table(page, m)
        p = json.loads(t.DrawingKitParams)
        self.assertIn("ISO 2768-m", p["title"])
        self.assertEqual(p["rows"][0], ["0.5 - 3", "±0.1"])
        self.assertIn(["ANGLES (SHORTER SIDE)", ""], p["rows"])
        self.assertIn(["UP TO 10", "±1°"], p["rows"])
        # own ranges replace it, in the same table
        own = {"preset": "", "linear": [["0", "2", "0.01"], ["2", "6", "0.02"]], "angular": []}
        t2 = tables.set_tolerance_table(page, own)
        self.assertEqual(t2.Name, t.Name)
        self.assertEqual(json.loads(t2.DrawingKitParams)["rows"],
                         [["0 - 2", "±0.01"], ["2 - 6", "±0.02"]])
        self.assertEqual(tables.tolerance_data(page)["linear"], own["linear"])
        # off the drawing area's views: it is one of the tables views keep clear of
        self.assertEqual(len(core.table_boxes(page)), 1)
        self.assertIsNone(tables.set_tolerance_table(page, None))
        self.assertIsNone(tables.tolerance_table(page))

    def test_one_tolerance_method_per_sheet(self):
        from DrawingKitTools import tables
        page = core.create_page(self.doc, "A3", "Landscape")
        self.tb.set_tolerance_row(page, True)
        self.tb.set_fields(page, {"finish": "Ra 1.6", "title": "Body"})
        self.assertEqual(self.tb.tolerance_mode(page), "places")
        tables.set_tolerance_table(page, tables.iso2768("f"))
        texts = dict(page.Template.EditableTexts)
        self.assertNotIn("tol_1", texts)                 # no X.X cells next to the table
        self.assertEqual((texts["finish"], texts["title"]), ("Ra 1.6", "Body"))
        self.assertTrue(page.Template.Template.endswith("_toltab.svg"))
        tables.set_tolerance_table(page, None)
        self.assertIn("tol_1", dict(page.Template.EditableTexts))
        # ANSI: "TOLERANCES: SEE TABLE" instead of the .X lines
        page2 = core.create_page(self.doc, "ANSI C", "Landscape")
        self.tb.set_template_variant(page2, style_="ansi")
        tables.set_tolerance_table(page2, tables.iso2768("m"))
        self.assertTrue(page2.Template.Template.endswith("_ansitab.svg"))
        self.assertNotIn("tol_3", dict(page2.Template.EditableTexts))
        self.assertTrue(self.tb.is_ansi(page2))
        with open(page2.Template.Template, encoding="utf-8") as f:
            self.assertIn("TOLERANCES: SEE TABLE", f.read())

    def test_one_tolerance_for_all_angles(self):
        import json
        from DrawingKitTools import tables
        page = core.create_page(self.doc, "A3", "Landscape")
        d = {"preset": "", "linear": [["0", "6", "0.02"]], "angular": [], "angle_all": "1°"}
        t = tables.set_tolerance_table(page, d)
        rows = json.loads(t.DrawingKitParams)["rows"]
        self.assertEqual(rows, [["0 - 6", "±0.02"], ["ANGLES", "±1°"]])
        self.assertEqual(tables.tolerance_data(page)["angle_all"], "1°")
        # only an angle tolerance is still a table
        t = tables.set_tolerance_table(page, {"preset": "", "linear": [], "angular": [],
                                              "angle_all": "0°30'"})
        self.assertEqual(json.loads(t.DrawingKitParams)["rows"], [["ANGLES", "±0°30'"]])
        # kept with the defaults
        self.tb.save_user_defaults({self.tb.TOL_TABLE: d})
        self.assertEqual(self.tb.load_defaults(asme=False)[self.tb.TOL_TABLE]["angle_all"], "1°")

    def test_saved_tolerance_table_goes_on_new_sheets(self):
        from DrawingKitTools import tables
        own = {"preset": "ACME STD-12", "linear": [["0", "2", "0.01"], ["2", "6", "0.02"]],
               "angular": []}
        self.tb.save_user_defaults({"units": "MM", self.tb.TOL_TABLE: own})
        self.assertEqual(self.tb.load_defaults(asme=False)[self.tb.TOL_TABLE]["preset"], "ACME STD-12")
        page = core.create_page(self.doc, "A3", "Landscape")
        self.assertEqual(tables.tolerance_data(page)["linear"], own["linear"])

    def test_fields_in_order(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        keys = [k for k, _, _ in self.tb.fields(page)]
        self.assertEqual(keys[0], "title")
        self.assertIn("general_tolerances", keys)


class TestDimensionPreview(unittest.TestCase):
    def setUp(self):
        from DrawingKitTools import smartdim
        self.sd = smartdim
        self.E = smartdim.Ent

    def lines(self, shapes):
        return [pts for kind, pts in shapes if kind == "line"]

    def tips(self, shapes):
        return [pts[0] for kind, pts in shapes if kind == "fill"]

    def test_horizontal_length_follows_the_mouse(self):
        line = self.E("line", 1, [(0.0, 0.0), (10.0, 0.0)])
        sh = self.sd.preview_shapes([line], (4.0, 6.0), 1.0)
        tips = sorted(self.tips(sh))
        self.assertEqual(len(tips), 2)
        for (x, y), want in zip(tips, [(0.0, 6.0), (10.0, 6.0)]):
            self.assertAlmostEqual(x, want[0])
            self.assertAlmostEqual(y, want[1])
        # two extension lines, from just off the part past the dimension line
        ext = [l for l in self.lines(sh) if abs(l[0][0] - l[1][0]) < 1e-9]
        self.assertEqual(len(ext), 2)
        self.assertAlmostEqual(min(p[1] for p in ext[0]), 1.5)
        self.assertAlmostEqual(max(p[1] for p in ext[0]), 8.0)

    def test_mouse_past_the_end_stretches_the_line(self):
        a = self.E("vertex", 1, [(0.0, 0.0)])
        b = self.E("vertex", 2, [(10.0, 10.0)])
        sh = self.sd.preview_shapes([a, b], (25.0, 5.0), 1.0)   # right: vertical
        dim = [l for l in self.lines(sh) if abs(l[0][0] - 25.0) < 1e-9 and abs(l[1][0] - 25.0) < 1e-9]
        self.assertEqual(len(dim), 1)
        ys = sorted(p[1] for p in dim[0])
        self.assertAlmostEqual(ys[0], 0.0)
        self.assertAlmostEqual(ys[1], 10.0)

    def test_diameter_and_angle(self):
        c = self.E("circle", 1, [(0.0, 0.0)], radius=5.0)
        sh = self.sd.preview_shapes([c], (20.0, 0.0), 2.0)
        self.assertEqual(len(self.tips(sh)), 2)
        self.assertTrue(any(abs(l[-1][0] - 20.0) < 1e-9 for l in self.lines(sh)))   # leader
        l1 = self.E("line", 1, [(0.0, 0.0), (10.0, 0.0)])
        l2 = self.E("line", 2, [(0.0, 0.0), (0.0, 10.0)])
        sh = self.sd.preview_shapes([l1, l2], (5.0, 5.0), 1.0)
        arc = max(self.lines(sh), key=len)
        for p in arc:
            self.assertAlmostEqual(math.hypot(*p), math.hypot(5.0, 5.0), places=6)


class TestAlternatePosition(DocTest):
    def setUp(self):
        super().setUp()
        import Part
        from DrawingKitTools import phantom
        self.ph = phantom
        self.base = self.doc.addObject("Part::Feature", "Base")
        self.base.Shape = Part.makeBox(100, 40, 10)
        self.arm = self.doc.addObject("Part::Feature", "Arm")
        self.arm.Shape = Part.makeBox(40, 8, 6, App.Vector(60, 16, 10))
        self.asm = self.doc.addObject("App::Part", "Assembly")
        self.asm.addObjects([self.base, self.arm])
        self.doc.recompute()
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.g = core.add_standard_views(self.page, [self.asm],
                                         front=core.STANDARD_DIRECTIONS["Top"])
        self.doc.recompute()
        self.v = self.g.Anchor

    def test_turning(self):
        (p,), = self.ph.moved([[(10.0, 0.0)]], (0.0, 0.0), 90.0)
        self.assertAlmostEqual(p[0], 0.0)
        self.assertAlmostEqual(p[1], 10.0)
        (p,), = self.ph.moved([[(10.0, 0.0)]], (5.0, 0.0), 180.0, (1.0, 2.0))
        self.assertAlmostEqual(p[0], 1.0)
        self.assertAlmostEqual(p[1], 2.0)

    def test_outline_is_the_part_as_the_view_sees_it(self):
        polys = self.ph.outline(self.v, self.arm)
        xs = [x for poly in polys for x, _ in poly]
        ys = [y for poly in polys for _, y in poly]
        bb = self.arm.Shape.BoundBox
        lo = core.model_to_local(self.v, App.Vector(bb.XMin, bb.YMin, bb.ZMax))
        hi = core.model_to_local(self.v, App.Vector(bb.XMax, bb.YMax, bb.ZMax))
        self.assertAlmostEqual(min(xs), min(lo[0], hi[0]), places=4)
        self.assertAlmostEqual(max(xs), max(lo[0], hi[0]), places=4)
        self.assertAlmostEqual(min(ys), min(lo[1], hi[1]), places=4)
        self.assertAlmostEqual(max(ys), max(lo[1], hi[1]), places=4)

    def test_add_refresh_remove(self):
        pivot = core.model_to_local(self.v, App.Vector(64, 20, 13))
        n = self.ph.add(self.v, self.arm, 30.0, pivot)
        self.assertGreaterEqual(n, 4)
        items = self.ph.entries(self.v)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["part"], "Arm")
        for tag in items[0]["tags"]:
            self.assertIsNotNone(self.v.getCosmeticEdge(tag))
        # the model changes: drawn again, same number of lines
        self.arm.Shape = self.arm.Shape.copy()
        self.assertEqual(self.ph.refresh(self.v), n)
        old = self.ph.entries(self.v)[0]["tags"]
        self.assertEqual(self.ph.remove(self.v), 1)
        self.assertEqual(self.ph.entries(self.v), [])
        for tag in old:
            with self.assertRaises(Exception):     # TechDraw: "edge ... not found"
                self.v.getCosmeticEdge(tag)
        with self.assertRaises(ValueError):
            self.ph.add(self.v, self.arm, 0.0, pivot)


class TestShadedView(DocTest):
    def test_frame_matches_techdraw_iso(self):
        from DrawingKitTools import shaded
        d, x = core.STANDARD_DIRECTIONS["FrontTopRight"]
        dd, right, up = shaded.view_frame(d, x)
        self.assertAlmostEqual(right.dot(App.Vector(1, 1, 0).normalize()), 1.0, places=9)
        self.assertGreater(up.z, 0.5)                       # up is up the model
        self.assertAlmostEqual(right.cross(up).dot(dd), 1.0, places=9)

    def test_triangles_lit_and_sorted(self):
        import Part
        from DrawingKitTools import shaded
        a = self.doc.addObject("Part::Feature", "A")
        a.Shape = Part.makeBox(20, 20, 20)
        b = self.doc.addObject("Part::Feature", "B")
        b.Shape = Part.makeBox(10, 10, 10, App.Vector(30, 0, 0))
        self.doc.recompute()
        d, x = core.STANDARD_DIRECTIONS["FrontTopRight"]
        dd, right, up = shaded.view_frame(d, x)
        tris = shaded.triangles([a, b], dd, right, up)
        self.assertGreater(len(tris), 12)
        depths = [t[0] for t in tris]
        self.assertEqual(depths, sorted(depths))           # far first
        # three faces of a cube face the iso viewer: three different shades
        shades = {round(t[2][0], 3) for t in tris if t[0] > 0}
        self.assertGreaterEqual(len(shades), 3)
        polys = shaded.edges([a, b], dd, right, up, 50.0)
        self.assertGreaterEqual(len(polys), 9 + 9)         # 9 visible edges per box


class TestBalloonLeaders(unittest.TestCase):
    def test_crossing_leaders_are_swapped(self):
        from DrawingKitTools import drafting
        plan = [["a", (0, 10), (10, 0)], ["b", (0, 0), (10, 10)]]   # an X
        drafting.uncross(plan)
        self.assertFalse(drafting._segments_cross(plan[0][1], plan[0][2], plan[1][1], plan[1][2]))


class TestAssemblyFromFile(DocTest):
    """An App::Part assembly saved and opened again (Forge's finding #1)."""

    def test_saved_assembly_draws_and_lists(self):
        import Part
        import tempfile
        from DrawingKitTools import drafting
        doc = App.newDocument("AsmSave")
        asm = doc.addObject("App::Part", "Assembly")
        asm.Placement.Base = App.Vector(100, 0, 0)          # moved assembly
        b = doc.addObject("Part::Feature", "BasePlate")
        b.Shape = Part.makeBox(80, 50, 10)
        asm.addObject(b)
        for i, x in enumerate((20, 60)):
            p = doc.addObject("Part::Feature", "Pin%03d" % (i + 1))
            p.Shape = Part.makeCylinder(4, 20, App.Vector(x, 25, 10))
            asm.addObject(p)
        doc.recompute()
        path = os.path.join(tempfile.mkdtemp(prefix="dk_asm_"), "asm.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        doc = App.openDocument(path)
        try:
            asm = doc.getObject("Assembly")
            self.assertEqual([(i["name"], i["qty"]) for i in drafting.bom_items([asm])],
                             [("BasePlate", 1), ("Pin", 2)])
            page = core.create_page(doc, "A3", "Landscape")
            g = core.add_standard_views(page, [asm])
            doc.recompute()
            self.assertEqual(core.page_part(page).Name, "Assembly")
            # a pin is found where the moved assembly shows it
            items = drafting.bom_items([asm])
            v = g.Anchor
            d, right, up = core.view_axes(v)
            pin_top = App.Vector(120, 25, 30)
            hits = [drafting.part_at(v, *core.model_to_page(v, pin_top - d * 0.0))[0]]
            self.assertTrue(any(h is not None for h in hits))
        finally:
            App.closeDocument(doc.Name)


class TestSectionFromTopView(DocTest):
    """Forge's finding #2: a section cut on the Top view must stay in line
    with the Top view after Fit to page (not jump to the Front row)."""

    def test_stays_lined_up_with_its_base_after_fit(self):
        import Part
        s = Part.makeBox(100, 60, 12).fuse(Part.makeBox(100, 12, 60))
        for x in (20, 80):
            s = s.cut(Part.makeCylinder(3.3, 30, App.Vector(x, 40, -5)))
        part = self.doc.addObject("Part::Feature", "Bracket")
        part.Shape = s
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [part], front=FRONT)
        self.doc.recompute()
        top = g.getItemByLabel("Top")
        hx, hy = core.model_to_page(top, App.Vector(20, 40, 12))
        bx = max(core.view_box(v)[2] for v in g.Views)
        sec = core.add_cut_section(page, top, [(hx, hy)], (bx + 45, hy))
        self.assertEqual(core.section_side(sec), "Right")
        core.fit_page(page)
        self.doc.recompute()
        self.assertAlmostEqual(float(sec.Y), core.view_center(top)[1], places=6)
        self.assertGreater(core.view_box(sec)[0], core.view_box(top)[2])
        for v in core.part_views(page):
            if core.same(v, sec):
                continue
            a, b = core.view_box(sec), core.view_box(v)
            self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], v.Label)

    def test_detail_box_is_its_circle(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        front = g.Anchor
        cx, cy = core.view_center(front)
        det = core.add_detail(page, front, (cx, cy), 8.0, (cx + 120, cy))
        self.doc.recompute()
        spin_event_loop(3.0)   # detail cut runs in a thread, finished via Qt signals
        x0, y0, x1, y1 = core.view_box(det)
        r = float(det.Radius) * core.view_scale(det)
        self.assertAlmostEqual(x1 - x0, 2 * r, places=6)


class TestTurnedPart(DocTest):
    """Forge's finding #3: end faces and shoulders of a turned part."""

    def test_end_faces_can_be_dimensioned(self):
        import Part
        from DrawingKitTools import smartdim
        x = App.Vector(1, 0, 0)
        s = Part.makeCylinder(12.5, 70, App.Vector(-60, 0, 0), x).fuse(
            Part.makeCylinder(10, 50, App.Vector(10, 0, 0), x))
        s = s.removeSplitter()
        shaft = self.doc.addObject("Part::Feature", "Shaft")
        shaft.Shape = s
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [shaft])
        self.doc.recompute()
        v = [w for w in g.Views if abs(App.Vector(w.Direction).dot(x)) < 1e-6][0]   # side-on
        ents = smartdim.entities(v)
        lines = [e for e in ents if e.kind == "line"]
        span = lambda e: (e.pts[0], e.pts[1])
        # the two end faces seen edge-on: lines across the axis at the extremes
        d, right, up = core.view_axes(v)
        horiz = abs(right.dot(x)) > 0.5
        k = 0 if horiz else 1
        across = [e for e in lines if abs(e.pts[0][k] - e.pts[1][k]) < 1e-6]
        self.assertGreaterEqual(len(across), 3)          # two ends + the shoulder
        ends = sorted(across, key=lambda e: e.pts[0][k])
        a, b = ends[0], ends[-1]
        t, refs, _ = smartdim.plan([a, b], (0, 40) if horiz else (40, 0))
        dim = smartdim.add_dimension(page, v, [a, b], (0, 40) if horiz else (40, 0))
        self.doc.recompute()
        self.assertAlmostEqual(dim.getRawValue(), 120.0, places=4)


class TestForgeLayoutFixes(DocTest):
    def test_revision_table_inside_frame(self):
        from DrawingKitTools import tables
        page = core.create_page(self.doc, "A3", "Landscape")
        tables.add_revision(page, "Initial release", "QA", "2026-11-23")
        frame, _ = core.drawing_area(page.Template.Template, *core.page_size(page))
        (x0, y0, x1, y1), = core.table_boxes(page, pad=0.0)
        self.assertLessEqual(x1, frame[2] + 0.01)
        self.assertLessEqual(y1, frame[3] + 0.01)
        import re
        t = tables.revision_table(page)
        w = float(re.search(r'width="([\d.]+)mm"', t.Symbol).group(1))
        self.assertAlmostEqual(w - 2.0, x1 - x0, delta=0.5)        # drawn as big as planned

    def test_datum_on_a_line_to_line_distance(self):
        from DrawingKitTools import annot, smartdim
        part = plate_two_holes(self.doc)
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [part])
        self.doc.recompute()
        v = g.Anchor
        ents = smartdim.entities(v)
        top = smartdim.pick(v, (0, 25), [e for e in ents if e.kind == "line"], tol=1.0)
        bottom = smartdim.pick(v, (0, -25), [e for e in ents if e.kind == "line"], tol=1.0)
        dim = smartdim.add_dimension(page, v, [top, bottom], (55, 0))
        self.doc.recompute()
        self.assertEqual(dim.Type, "Distance")
        p0, p1, u = annot.dimension_ends(dim)
        s = core.view_scale(v)
        self.assertAlmostEqual(math_dist(p0, p1), 50 * s, places=4)   # square across the plate
        cx, cy = core.view_center(v)
        self.assertAlmostEqual(p0[0], cx + 55 * s, places=4)          # through the label


class TestKeywayCentreLines(DocTest):
    def test_no_axis_lines_for_keyway_ends_seen_side_on(self):
        import Part
        x, z = App.Vector(1, 0, 0), App.Vector(0, 0, 1)
        shaft = Part.makeCylinder(10, 100, App.Vector(0, 0, 0), x)
        slot = Part.makeBox(20, 6, 10, App.Vector(40, -3, 7)) \
            .fuse(Part.makeCylinder(3, 10, App.Vector(40, 0, 7), z)) \
            .fuse(Part.makeCylinder(3, 10, App.Vector(60, 0, 7), z))
        obj = self.doc.addObject("Part::Feature", "KeyedShaft")
        obj.Shape = shaft.cut(slot)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [obj])
        self.doc.recompute()
        for v in core.part_views(page):
            d = App.Vector(v.Direction)
            marks, lines = core.center_features(v)
            if abs(d.dot(x)) < 1e-6 and abs(d.dot(z)) < 0.99:   # side-on: only the shaft's axis
                self.assertEqual(len(lines), 1, v.Label)


class TestHiddenLines(DocTest):
    def test_toggle_hidden_lines(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        views = core.part_views(page)
        self.assertFalse(core.has_hidden_lines(views))
        changed = core.set_hidden_lines(views, True)
        self.assertEqual(len(changed), len(views))
        self.assertTrue(all(v.HardHidden for v in views))
        core.set_hidden_lines(views, False)
        self.assertFalse(core.has_hidden_lines(views))


class TestKeepClear(DocTest):
    """Forge's finding #6: new symbols keep off texts and each other."""

    def setUp(self):
        super().setUp()
        from DrawingKitTools import annot, clearance, smartdim
        self.annot, self.cl, self.sd = annot, clearance, smartdim
        self.part = plate_two_holes(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.g = core.add_standard_views(self.page, [self.part])
        self.doc.recompute()
        self.v = self.g.Anchor

    @staticmethod
    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def test_two_datums_at_one_spot_do_not_pile_up(self):
        x0, y0, x1, y1 = core.view_box(self.v)
        a = self.annot.add_datum(self.page, (x0 + x1) / 2, y1)
        b = self.annot.add_datum(self.page, (x0 + x1) / 2 + 1, y1, "B")
        self.assertFalse(self.overlap(self.annot.body_box(a), self.annot.body_box(b)))

    def test_frame_keeps_off_dimension_text(self):
        ents = self.sd.entities(self.v)
        top = self.sd.pick(self.v, (0, 25), [e for e in ents if e.kind == "line"], tol=1.0)
        dim = self.sd.add_dimension(self.page, self.v, [top], (0, 40))   # text above the view
        self.doc.recompute()
        label = self.cl.dimension_label_box(dim)
        # ask for the frame right on top of the dimension text, by default placement
        cx, cy = core.view_center(self.v)
        s = core.view_scale(self.v)
        anchor = (cx, cy + 25 * s)
        fr = self.annot.add_frame(self.page, None, {"char": "flatness", "tol": "0.05",
                                                   "datums": ["", "", ""]}, anchor=anchor)
        fr2 = self.annot.add_frame(self.page, ((label[0] + label[2]) / 2, (label[1] + label[3]) / 2),
                                   {"char": "flatness", "tol": "0.05", "datums": ["", "", ""]},
                                   anchor=anchor)
        for f in (fr, fr2):
            self.assertFalse(self.overlap(self.annot.body_box(f), label), f.Name)
        self.assertFalse(self.overlap(self.annot.body_box(fr), core.view_box(self.v)))

    def test_clear_spot_moves_away_from_anchor(self):
        shift = self.cl.clear_spot((0, 0, 10, 5), (5, -20), [(0, 0, 10, 5)])
        self.assertIsNotNone(shift)
        self.assertGreater(shift[1], 0)            # pushed further from the anchor below


class TestHatchByPart(DocTest):
    def make(self):
        import Part
        z = App.Vector(0, 0, 1)
        asm = self.doc.addObject("App::Part", "Assembly")
        plate = self.doc.addObject("Part::Feature", "BasePlate")
        plate.Shape = Part.makeBox(80, 50, 10).cut(Part.makeCylinder(4, 20, App.Vector(20, 25, -5), z))
        collar = self.doc.addObject("Part::Feature", "Collar001")
        collar.Shape = Part.makeCylinder(8, 6, App.Vector(20, 25, 10), z).cut(
            Part.makeCylinder(4, 10, App.Vector(20, 25, 8), z))
        pin = self.doc.addObject("Part::Feature", "Pin001")
        pin.Shape = Part.makeCylinder(4, 30, App.Vector(20, 25, -2), z)
        for o in (plate, collar, pin):
            asm.addObject(o)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [asm], front=FRONT)
        self.doc.recompute()
        return page, g, plate, collar, pin

    def test_each_part_its_own_hatch_and_pins_left_uncut(self):
        from DrawingKitTools import hatching
        page, g, plate, collar, pin = self.make()
        front = g.Anchor
        sec = core.add_section(page, front, direction="Right", origin=App.Vector(20, 25, 5))
        self.doc.recompute()
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertIsNotNone(sym)
        self.assertEqual(sec.CutSurfaceDisplay, "Hide")
        self.assertTrue(parts["Pin001"]["skip"])
        self.assertFalse(parts["BasePlate"]["skip"])
        self.assertNotEqual(parts["BasePlate"]["angle"], parts["Collar001"]["angle"])
        self.assertGreater(len(parts["BasePlate"]["segments"]), 5)
        self.assertGreater(len(parts["Collar001"]["segments"]), 0)
        # every hatch line lies inside its part's cut face (checked in 3D)
        import Part
        keep, o, n = hatching._kept(sec)
        to_local = hatching._local_frame(sec, keep)
        for name in ("BasePlate", "Collar001"):
            faces = hatching.cut_faces(core.shape_of(self.doc.getObject(name)), sec, keep)
            f2 = [hatching._face_2d(f, to_local) for f in faces]
            for (a, b) in parts[name]["segments"]:
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                self.assertTrue(any(f.contains(mx, my) for f in f2), name)
        # wall to wall: across the 10 mm plate a 45 deg line is 14.14 mm long
        import math as _m
        plate_angle = parts["BasePlate"]["angle"]
        longest = max(_m.hypot(b[0] - a[0], b[1] - a[1]) for a, b in parts["BasePlate"]["segments"])
        self.assertAlmostEqual(longest, 10 / _m.sin(_m.radians(plate_angle)), delta=0.02)
        # follows the section when it moves
        x = float(sym.X)
        sec.X = float(sec.X) + 10
        from DrawingKitTools import annot
        annot.refresh(annot.attached_to(sec))
        self.assertAlmostEqual(float(sym.X) - x, 10.0, places=6)

    def test_single_part_keeps_techdraw_hatch(self):
        from DrawingKitTools import hatching
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        sec = core.add_section(page, g.Anchor)
        self.doc.recompute()
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertIsNone(sym)
        self.assertNotEqual(sec.CutSurfaceDisplay, "Hide")


class TestHatchSameParts(DocTest):
    def test_identical_parts_share_a_hatch(self):
        import Part
        from DrawingKitTools import hatching
        z = App.Vector(0, 0, 1)
        asm = self.doc.addObject("App::Part", "Assembly")
        plate = self.doc.addObject("Part::Feature", "BasePlate")
        plate.Shape = Part.makeBox(80, 50, 10)
        asm.addObject(plate)
        for i, x in enumerate((20, 60)):
            c = self.doc.addObject("Part::Feature", "Collar%03d" % (i + 1))
            c.Shape = Part.makeCylinder(8, 6, App.Vector(x, 25, 10), z)
            asm.addObject(c)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [asm], front=FRONT)
        self.doc.recompute()
        sec = core.add_section(page, g.getItemByLabel("Top"), direction="Up", origin=App.Vector(40, 25, 5))
        self.doc.recompute()
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertEqual(parts["Collar001"]["angle"], parts["Collar002"]["angle"])
        self.assertNotEqual(parts["Collar001"]["angle"], parts["BasePlate"]["angle"])


class TestHatchEdgeToEdge(unittest.TestCase):
    """The hatch lines must run right across the face, wall to wall."""

    def test_rectangle_and_hole(self):
        from DrawingKitTools import hatching
        outer = [(0, 0), (16, 0), (16, 10), (0, 10)]
        hole = [(6, 3), (10, 3), (10, 7), (6, 7)]
        r = hatching.Region([outer, hole])
        segs = hatching.hatch_lines(r, 45, 1.0)
        self.assertGreater(len(segs), 15)
        on_edge = lambda p: (abs(p[0]) < 1e-9 or abs(p[0] - 16) < 1e-9 or abs(p[1]) < 1e-9 or
                             abs(p[1] - 10) < 1e-9 or (6 - 1e-9 <= p[0] <= 10 + 1e-9 and
                             3 - 1e-9 <= p[1] <= 7 + 1e-9))
        for a, b in segs:
            self.assertTrue(on_edge(a) and on_edge(b), (a, b))
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            self.assertTrue(r.contains(mx, my))
            self.assertFalse(6 < mx < 10 and 3 < my < 7)       # nothing in the hole
        plain = hatching.hatch_lines(hatching.Region([outer]), 45, 1.0)
        longest = max(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in plain)
        self.assertAlmostEqual(longest, 10 * 2 ** 0.5, delta=0.01)   # wall to wall


class TestHatchPatterns(DocTest):
    def test_material_defaults(self):
        from DrawingKitTools import hatching as h
        self.assertEqual(h.material_pattern("Steel-Generic"), "double")
        self.assertEqual(h.material_pattern("C45 steel"), "double")
        self.assertEqual(h.material_pattern("Brass"), "alternate")
        self.assertEqual(h.material_pattern("Aluminum-6061-T6"), "cross")
        self.assertEqual(h.material_pattern("PA6 nylon"), "dashed")
        self.assertEqual(h.material_pattern(""), "single")
        self.assertEqual(h.material_pattern("Compass"), "single")

    def test_patterns(self):
        from DrawingKitTools import hatching as h
        r = h.Region([[(0, 0), (20, 0), (20, 10), (0, 10)]])
        single = h.pattern_lines(r, 45, 1.0, "single")
        double = h.pattern_lines(r, 45, 1.0, "double")
        alt = h.pattern_lines(r, 45, 1.0, "alternate")
        cross = h.pattern_lines(r, 45, 1.0, "cross")
        dashed = h.pattern_lines(r, 45, 1.0, "dashed")
        self.assertFalse(single[1])
        self.assertTrue(alt[0] and alt[1])                   # solid and dashed lines
        self.assertTrue(dashed[1] and not dashed[0])
        dirs = {round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180) for a, b in cross[0]}
        self.assertEqual(dirs, {45, 135})
        self.assertGreater(len(double[0]), len(single[0]) * 1.1)

    def test_user_overrides_survive_a_redo(self):
        import json
        from DrawingKitTools import annot, hatching
        maker = TestHatchByPart("test_each_part_its_own_hatch_and_pins_left_uncut")
        maker.doc = self.doc
        page, g, plate, collar, pin = maker.make()
        sec = core.add_section(page, g.Anchor, direction="Right", origin=App.Vector(20, 25, 5))
        self.doc.recompute()
        sym, parts = hatching.hatch_by_part(page, sec)
        hatching.set_override(sym, "BasePlate", angle=60, spacing=3.0, pattern="cross")
        hatching.set_override(sym, "Pin001", off=False)          # hatch the pin after all
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertEqual(parts["BasePlate"]["angle"], 60)
        self.assertEqual(parts["BasePlate"]["pattern"], "cross")
        self.assertAlmostEqual(parts["BasePlate"]["spacing"], 3.0)
        self.assertFalse(parts["Pin001"]["skip"])
        hatching.set_override(sym, "Collar001", off=True)
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertTrue(parts["Collar001"]["skip"])
        self.assertIn("overrides", json.loads(sym.DrawingKitParams))


class TestUncutParts(DocTest):
    def test_pin_drawn_whole_and_follows(self):
        from DrawingKitTools import annot, hatching
        maker = TestHatchByPart("test_each_part_its_own_hatch_and_pins_left_uncut")
        maker.doc = self.doc
        page, g, plate, collar, pin = maker.make()
        sec = core.add_section(page, g.Anchor, direction="Right", origin=App.Vector(20, 25, 5))
        self.doc.recompute()
        sym, parts = hatching.hatch_by_part(page, sec)
        views = hatching.uncut_views(sec)
        self.assertEqual(list(views), ["Pin001"])
        v = views["Pin001"]
        self.assertEqual(v.Direction, sec.Direction)
        self.assertAlmostEqual(float(v.Scale), core.view_scale(sec))
        self.assertNotIn(v, core.part_views(page))              # not laid out on its own
        # sits where the pin is in the section: the pin's box centre
        keep, o, n = hatching._kept(sec)
        lx, ly = hatching._local_frame(sec, keep)(pin.Shape.BoundBox.Center)
        self.assertAlmostEqual(float(v.X), annot.view_point(sec, (lx, ly))[0], places=6)
        x = float(v.X)
        sec.X = float(sec.X) + 10
        annot.refresh(annot.attached_to(sec))
        self.assertAlmostEqual(float(v.X) - x, 10.0, places=6)
        # hatching the pin after all removes its uncut view
        hatching.set_override(sym, "Pin001", off=False)
        hatching.hatch_by_part(page, sec)
        self.assertEqual(hatching.uncut_views(sec), {})


class TestHatchStepped(DocTest):
    def test_stepped_section_hatched_by_part(self):
        import Part
        from DrawingKitTools import hatching
        asm = self.doc.addObject("App::Part", "Assembly")
        plate = self.doc.addObject("Part::Feature", "BasePlate")
        plate.Shape = Part.makeBox(100, 60, 10)
        asm.addObject(plate)
        for i, (x, y) in enumerate(((20, 15), (70, 45))):
            b = self.doc.addObject("Part::Feature", "Block%03d" % (i + 1))
            b.Shape = Part.makeBox(16, 16, 12, App.Vector(x - 8, y - 8, 10))
            asm.addObject(b)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [asm], front=FRONT)
        self.doc.recompute()
        top = g.getItemByLabel("Top")
        pts = [core.model_to_page(top, App.Vector(x, y, 5))
               for x, y in ((-5, 15), (45, 15), (45, 45), (105, 45))]
        bx = core.view_box(top)
        sec = core.add_cut_section(page, top, pts, (bx[0] + 50, bx[1] - 40))
        self.doc.recompute()
        self.assertTrue(hatching.is_stepped(sec))
        self.assertEqual(len(hatching.cut_planes(sec)), 2)
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertIsNotNone(sym)
        self.assertEqual(set(parts), {"BasePlate", "Block001", "Block002"})
        self.assertNotEqual(parts["Block001"]["angle"], parts["BasePlate"]["angle"])
        self.assertEqual(parts["Block001"]["angle"], parts["Block002"]["angle"])   # same part
        # the plate is cut on both steps: hatched across the whole width
        xs = [p[0] for a, b in parts["BasePlate"]["segments"] for p in (a, b)]
        self.assertAlmostEqual(min(xs), -50.0, delta=0.5)
        self.assertAlmostEqual(max(xs), 50.0, delta=0.5)


class TestHatchAligned(DocTest):
    """Aligned sections are only drawn in the GUI (checked there off-screen);
    here: the piece matching, and that an undrawn section is left alone."""

    def test_register_finds_turn_flip_and_shift(self):
        from DrawingKitTools import hatching
        corners = [(17.1, -11.5), (17.1, -1.5), (37.1, -1.5), (37.1, 13.5), (51.1, 13.5),
                   (51.1, -11.5), (25.1, -13.5), (29.1, 0.5)]
        truth = (True, -1, 1, -11.5, 57.1)
        drawn = [hatching._orient(truth, u, v) for u, v in corners]
        drawn += [(5.0, 5.0), (-30.0, 12.0), (2.0, -40.0)]          # other drawn corners
        fit = hatching._register(corners, drawn)
        self.assertIsNotNone(fit)
        for u, v in corners:
            a, b = hatching._orient(fit, u, v), hatching._orient(truth, u, v)
            self.assertAlmostEqual(a[0], b[0], places=6)
            self.assertAlmostEqual(a[1], b[1], places=6)

    def test_mirror_twin_goes_to_the_free_side(self):
        from DrawingKitTools import hatching
        corners = [(3, -11.5), (3, 13.5), (40, -11.5), (40, -1.5), (20, -1.5), (20, 13.5)]
        up = (True, 1, 1, 0.0, 0.0)
        down = (True, -1, 1, 0.0, 0.0)
        drawn = [hatching._orient(up, u, v) for u, v in corners] +                 [hatching._orient(down, u, v) for u, v in corners]
        taken = [(-11.5, 3, 13.5, 40)]                                 # the up side is used
        fit = hatching._register(corners, drawn, avoid=taken)
        self.assertEqual(fit[1], -1)

    def test_undrawn_aligned_section_is_skipped(self):
        import Part
        from DrawingKitTools import hatching
        fl = flange(self.doc)
        asm = self.doc.addObject("App::Part", "Assembly")
        sleeve = self.doc.addObject("Part::Feature", "Sleeve")
        sleeve.Shape = Part.makeCylinder(6, 25).cut(Part.makeCylinder(3, 25))
        for o in (fl, sleeve):
            asm.addObject(o)
        self.doc.recompute()
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [asm])
        self.doc.recompute()
        f = g.Anchor
        P = lambda x, y: core.model_to_page(f, App.Vector(x, y, 10))
        a60 = math.radians(60)
        pts = [P(45 * math.cos(a60), 45 * math.sin(a60)), P(0, 0), P(0.5, -45)]
        fx, fy = core.view_center(f)
        sec = core.add_aligned_section(page, f, pts, (fx + 120, fy))
        self.doc.recompute()
        self.assertTrue(hatching.supported(sec))
        # pieces of both parts on both runs of the cutting line
        d, _, _ = core.view_axes(f)
        wp = [App.Vector(v.Point) for v in sec.CuttingToolWireObject.Shape.Vertexes]
        for a, b in zip(wp, wp[1:]):
            u = (b - a).normalize()
            self.assertTrue(hatching._piece_faces(fl.Shape, a, u, d, (b - a).Length))
        if not hatching.drawn_vertices(sec):
            sym, parts = hatching.hatch_by_part(page, sec)
            self.assertIsNone(sym)


class TestCutDirection(DocTest):
    """ASME Y14.3 / ISO 128: shafts, pins, bolts are drawn whole only when
    cut lengthwise; cut across they are hatched."""

    def test_pin_cut_across_is_hatched(self):
        from DrawingKitTools import hatching
        maker = TestHatchByPart("test_each_part_its_own_hatch_and_pins_left_uncut")
        maker.doc = self.doc
        page, g, plate, collar, pin = maker.make()
        # horizontal cut through the plate (pin axis is vertical): across the pin
        sec = core.add_section(page, g.Anchor, direction="Down", origin=App.Vector(20, 25, 5))
        self.doc.recompute()
        self.assertFalse(hatching.cut_lengthwise(pin, sec))
        sym, parts = hatching.hatch_by_part(page, sec)
        self.assertFalse(parts["Pin001"]["skip"])
        self.assertTrue(parts["Pin001"]["segments"])
        self.assertEqual(hatching.uncut_views(sec), {})
        # vertical cut along it: drawn whole
        sec2 = core.add_section(page, g.Anchor, direction="Right", origin=App.Vector(20, 25, 5))
        self.doc.recompute()
        self.assertTrue(hatching.cut_lengthwise(pin, sec2))
        sym2, parts2 = hatching.hatch_by_part(page, sec2)
        self.assertTrue(parts2["Pin001"]["skip"])

    def test_plain_hatch_unless_material_patterns_on(self):
        from DrawingKitTools import hatching
        g = App.ParamGet(hatching.PREFS)
        old = g.GetBool("MaterialHatch", False)
        try:
            hatching.set_material_patterns(False)
            self.assertFalse(hatching.material_patterns_on())
            r = hatching.Region([[(0, 0), (20, 0), (20, 10), (0, 10)]])
            self.assertEqual(hatching.material_pattern("Steel-Generic"), "double")
            hatching.set_material_patterns(True)
            self.assertTrue(hatching.material_patterns_on())
        finally:
            g.SetBool("MaterialHatch", old)
            if not old:
                g.RemBool("MaterialHatch")


class TestDrawingStandard(DocTest):
    def setUp(self):
        super().setUp()
        from DrawingKitTools import standards
        self.st = standards
        self.g = App.ParamGet(standards.PREFS)
        self.old = self.g.GetString("Standard", "")

    def tearDown(self):
        if self.old:
            self.g.SetString("Standard", self.old)
        else:
            self.g.RemString("Standard")
        super().tearDown()

    def test_auto_follows_techdraw(self):
        self.st.set_setting("auto")
        self.assertEqual(self.st.current(), self.st.techdraw_standard())
        self.assertFalse(self.st.explicit())

    def test_iso_conventions(self):
        self.st.set_setting("ISO")
        self.assertEqual(self.st.section_caption("A"), "A-A")
        self.assertEqual(self.st.detail_caption("B", "2:1"), "B (2 : 1)")
        self.assertTrue(self.st.weld_dashed_line())
        self.assertIn("CZ", self.st.frame_zones())
        self.assertNotIn("DP", self.st.frame_zones())
        self.assertNotIn("RFS", self.st.tolerance_modifiers())
        self.assertNotIn("TR", self.st.datum_modifiers())
        self.assertFalse(self.st.has_cf())
        page = core.create_page(self.doc, "A3", "Landscape")
        self.assertEqual(page.ProjectionType, "First angle")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        sec = core.add_section(page, g.Anchor)
        self.assertEqual(sec.Caption, "A-A")

    def test_asme_conventions(self):
        self.st.set_setting("ASME")
        self.assertEqual(self.st.section_caption("A"), "SECTION A-A")
        self.assertEqual(self.st.detail_caption("B", "2:1"), "DETAIL B SCALE 2 : 1")
        self.assertFalse(self.st.weld_dashed_line())
        self.assertIn("DP", self.st.frame_zones())
        self.assertNotIn("CZ", self.st.frame_zones())
        self.assertIn("RFS", self.st.tolerance_modifiers())
        self.assertTrue(self.st.characteristic_note("symmetry"))
        page = core.create_page(self.doc, "A3", "Landscape")
        self.assertEqual(page.ProjectionType, "Third angle")
        self.assertIn("_3rd", page.Template.Template)

    def test_iso_unequal_zone_is_written_uz(self):
        from DrawingKitTools import annot
        page = core.create_page(self.doc, "A3", "Landscape")
        fr = annot.add_frame(page, (200, 200), {"char": "profile_surface", "tol": "0.2",
                                                "zones": ["U"], "uval": "0.05", "std": "ISO",
                                                "datums": ["A", "", ""]})
        self.assertIn(">UZ+0.05<", fr.Symbol)
        self.assertEqual(fr.Symbol.count("<circle"), 0)

    def test_apply_to_sheet_updates_captions_and_welds(self):
        from DrawingKitTools import drafting
        self.st.set_setting("ASME")
        page = core.create_page(self.doc, "A3", "Landscape")
        g = core.add_standard_views(page, [self.box], front=FRONT)
        self.doc.recompute()
        sec = core.add_section(page, g.Anchor)
        x0, y0, x1, y1 = core.view_box(g.Anchor)
        w = drafting.add_weld(page, x1, y1, {"type": "fillet", "size": "5", "side": "arrow",
                                             "iso_line": False})
        self.assertEqual(sec.Caption, "SECTION A-A")
        self.st.set_setting("ISO")
        n = self.st.apply_to_page(page)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(sec.Caption, "A-A")
        self.assertIn("stroke-dasharray", w.Symbol)


class TestAlignedUncutFrame(DocTest):
    def test_uncut_view_maps_like_the_piece(self):
        import Part
        from DrawingKitTools import hatching as h
        bolt = self.doc.addObject("Part::Feature", "Bolt")
        bolt.Shape = Part.makeCylinder(2, 14, App.Vector(15, 26, -2))
        self.doc.recompute()
        a = App.Vector(28.57, 49.49, 11.5)
        u = (App.Vector(0, 0, 11.5) - a).normalize()
        d = App.Vector(0, 0, 1)
        for fit in [(True, -1, 1, -11.5, 57.1), (False, 1, -1, 3.0, -4.0), (True, 1, 1, 0.0, 0.0)]:
            at, vdir, xdir = h._uncut_frame(bolt, fit, a, u, d)
            V, X = App.Vector(*vdir), App.Vector(*xdir)
            Y = V.cross(X)                          # TechDraw: local y = Direction x XDirection
            c = bolt.Shape.BoundBox.Center
            for p in (App.Vector(15, 26, -2), App.Vector(17, 26, 12), c):
                via_view = (at[0] + (p - c).dot(X), at[1] + (p - c).dot(Y))
                via_piece = h._orient(fit, (p - a).dot(u), (p - a).dot(d))
                self.assertAlmostEqual(via_view[0], via_piece[0], places=6)
                self.assertAlmostEqual(via_view[1], via_piece[1], places=6)
