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
        # title block frame is 230..410 x 10..46; the material/scale row
        # above it (up to y=58) must count as title block too
        self.assertEqual(title[:3], (230, 10, 410))
        self.assertGreaterEqual(title[3], 58)

    def test_draw2d_template_every_size_both_ways(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from Draw2DTools import templates as d2t
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

    def test_draw2d_template_fields(self):
        from Draw2DTools import templates as d2t
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
        self.doc = App.newDocument("Draw2DTest")
        self.box = make_box(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class TestPages(DocTest):
    def test_create_every_page_size(self):
        from Draw2DTools import templates as d2t
        for size in d2t.SIZES:
            for orient in d2t.ORIENTATIONS:
                page = core.create_page(self.doc, size, orient)
                self.doc.recompute()
                w, h = core.page_size(page)
                self.assertEqual((w, h), d2t.page_size(size, orient))
                self.assertIn("Draw2D_", page.Template.Template)
                self.assertIn("scale", page.Template.EditableTexts)

    def test_projection_symbol_follows_page(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        want = "_1st.svg" if core.page_first_angle(page) else "_3rd.svg"
        self.assertTrue(page.Template.Template.endswith(want))

    def test_freecad_template_still_available(self):
        page = core.create_page(self.doc, "A3", "Landscape", draw2d_template=False)
        self.assertTrue(page.Template.Template.endswith("A3_Landscape_ISO5457_minimal.svg"))

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
        # With TechDraw's own AutoDistribute on, it must place every view
        # exactly where Draw2D's layout puts them when it is off.
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True)
        ours = {v.Name: (float(v.X), float(v.Y)) for v in group.Views}
        core.make_aligned(group, False)
        self.doc.recompute()
        for v in group.Views:
            self.assertAlmostEqual(float(v.X), ours[v.Name][0], places=3, msg=v.Type)
            self.assertAlmostEqual(float(v.Y), ours[v.Name][1], places=3, msg=v.Type)

    def test_every_view_drawn_at_page_scale(self):
        # The bug Ravi saw: views kept their first (1:1) drawing.
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box])
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
        group = core.add_standard_views(page, [self.box], isometric=True)
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
        group = core.add_standard_views(page, [part])
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
        core.add_standard_views(page, [self.box])
        self.assertEqual(page.Template.EditableTexts["scale"], "1 : 2")

    def test_views_slide_on_one_axis_only(self):
        page = core.create_page(self.doc, "A3", "Landscape")
        group = core.add_standard_views(page, [self.box], isometric=True)
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
        group = core.add_standard_views(page, [self.box])
        core.make_aligned(group, False)
        right = group.getItemByLabel("Right")
        right.Y = 40.0
        self.assertFalse(core.enforce_alignment(right))

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
        self.group = core.add_standard_views(self.page, [self.box])
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
        self.group = core.add_standard_views(self.page, [self.block])
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
        g = core.add_standard_views(page, [obj])
        self.doc.recompute()
        self.assertEqual(core.center_features(g.getItemByLabel("Top")), ([], []))


class TestCutSections(DocTest):
    def setUp(self):
        super().setUp()
        self.block = block_with_hole(self.doc)
        self.page = core.create_page(self.doc, "A3", "Landscape")
        self.group = core.add_standard_views(self.page, [self.block])
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
