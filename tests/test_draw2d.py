# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless tests for Draw2D. Run with: freecadcmd tests/run_tests.py"""

import unittest

import FreeCAD as App

from Draw2DTools import core


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
        self.assertEqual(title, (230, 10, 410, 46))

    def test_ansi_drawing_area_has_fallback_title_block(self):
        path = core.available_templates()[("ANSI B", "Landscape")]
        frame, title = core.drawing_area(path, 431.8, 279.4)
        self.assertGreater(frame[0], 0)
        self.assertLess(frame[2], 431.8)
        self.assertIsNotNone(title)


class DocTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Draw2DTest")
        self.box = make_box(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class TestPages(DocTest):
    def test_create_every_available_page(self):
        for (size, orient) in core.available_templates():
            page = core.create_page(self.doc, size, orient)
            self.doc.recompute()
            w, h = core.page_size(page)
            if orient == "Landscape":
                self.assertGreater(w, h, (size, orient))
            else:
                self.assertGreater(h, w, (size, orient))

    def test_missing_template_raises(self):
        with self.assertRaises(ValueError):
            core.create_page(self.doc, "A0", "Sideways")


class TestViews(DocTest):
    def test_400x300_part_fits_a3_at_1_2(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=False)
        self.doc.recompute()
        self.assertEqual(page.Scale, 0.5)
        self.assertEqual(sorted(v.Type for v in group.Views), ["Front", "Right", "Top"])
        for v in group.Views:
            self.assertIn("Up-to-date", v.State)
        self.assertInsideFrame(page, group)

    def test_isometric_added_and_smaller_scale(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True)
        self.doc.recompute()
        self.assertIn("FrontTopRight", [v.Type for v in group.Views])
        self.assertLess(page.Scale, 0.5)
        self.assertInsideFrame(page, group)

    def test_small_part_on_a4_is_enlarged(self):
        small = make_box(self.doc, 20, 10, 5)
        page = core.create_page(self.doc, "A4", "Portrait")
        core.add_standard_views(page, [small])
        self.assertGreaterEqual(page.Scale, 2.0)

    def test_layout_matches_techdraw_placement(self):
        # Our predicted spacing must equal what TechDraw does after recompute.
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True)
        self.doc.recompute()
        s = page.Scale
        layout = core.group_layout(group)
        right = group.getItemByLabel("Right")
        top = group.getItemByLabel("Top")
        cols, rows = layout["cols"], layout["rows"]
        expect_dx = s * (cols[0] + cols[1]) / 2 + layout["gap_x"]
        expect_dy = s * (rows[0] + rows[1]) / 2 + layout["gap_y"]
        self.assertAlmostEqual(abs(float(right.X)), expect_dx, places=3)
        self.assertAlmostEqual(abs(float(top.Y)), expect_dy, places=3)

    def test_first_angle_detected(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box])
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
        self.group = core.add_standard_views(self.page, [self.box])
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


class TestSpeed(DocTest):
    def test_toggle(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        core.add_standard_views(page, [self.box])
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
