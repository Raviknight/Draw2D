# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless tests for DrawingKit. Run with: freecadcmd tests/run_tests.py"""

import unittest

import FreeCAD as App

from DrawingKitTools import core

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
        self.assertIn("3X ⌀%.2f THRU", texts)
        self.assertIn("⌀%.2f THRU", texts)
        self.assertTrue(any(t.startswith("⌀%.2f ↧ 4.0") for t in texts), texts)

    def test_callouts_on_top_view(self):
        dims = self.holes.add_callouts(self.page, self.top)
        self.doc.recompute()
        self.assertEqual(len(dims), 3)          # bolt holes, bore, blind hole
        texts = [d.FormatSpec for d in dims]
        self.assertIn("3X ⌀%.2f THRU", texts)
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
