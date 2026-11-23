# Draw2D

A FreeCAD 1.1 workbench that makes TechDraw drawings quicker, inspired by how Onshape drawings feel. It sits on top of TechDraw: every page and view it creates is an ordinary TechDraw object, so you can keep editing them with the normal TechDraw tools.

The **Draw2D** workbench has the Draw2D buttons plus TechDraw's own view, dimension, centre-line, annotation and export tools, so you can make a whole drawing without switching workbench. The Draw2D buttons are also added at the end of TechDraw's *TechDraw Pages* toolbar, for when you are in TechDraw. (Double-clicking a page in the tree still switches to TechDraw; that is TechDraw's own preference, *switch workbench on click*.)

Status: early version (0.2.0), not yet in the Addon Manager.

## Commands

| Button | What it does |
|---|---|
| **New drawing sheet** | Pick a paper size (ISO A4–A0, ANSI A–E) and orientation, and get a page with Draw2D's frame and title block. If a part is selected you can add its standard views in the same step. |
| **Add standard views, fitted** | With a 3D part selected: adds front, top and right views (and an optional isometric) as a projection group. The scale is the largest standard scale (1:1, 1:2, 1:5, 1:10 … 2:1, 5:1 …) that fits the drawing area beside the title block. The views are centred. |
| **Projected view by pointing** | Select a view, then move the mouse: a preview shows the view you'd get to the right, left, above, below or at a corner (isometric-style). Click to place it; click again for more; Esc to stop. From the front view it joins the projection group; from any other view it becomes a separate view that stays lined up with its parent. |
| **Section by cut line** | Select a view and click on it where the cut goes: one point for a straight cut, or several for a stepped (zig-zag) cut through several features. Segments snap to horizontal/vertical. Right-click or Enter, then click where the section view goes; the arrows point that way. Lettered A-A, B-B … automatically (I, O and Q are skipped). A stepped cut must cross the part from one side to the other. |
| **Centre marks and centre lines** | Adds centre marks to holes and round features seen end-on, and centre lines along their axes seen from the side. Works on the selected views, or on every view of the page. Run it again to refresh after changes; fillets are left alone. |
| **Fit to page** | Picks the scale again so the projection group and the other views fit, then re-centres them. Use it after adding views or changing the part. |
| **Fast editing on/off** | On: the page stops updating itself and views switch to Coarse View, so small edits don't freeze FreeCAD. Off: views go back to full quality and the page redraws once. |

**Moving views.** Drag a view by any of its lines or faces (TechDraw on its own only lets you grab the small name label). Projected views stay lined up, as in Onshape: side views slide left-right, top/bottom views slide up-down, the isometric moves freely, and dragging the front view moves them all. Views made with *Projected view by pointing* follow their parent the same way. A quick click still selects edges and faces as usual; hold Alt to drag the TechDraw way. To stop the locking on a group, set its **Draw2DAligned** property to false.

**Frame and title block.** New sheets use Draw2D's own template: an ISO 5457 frame with zone letters and numbers, and a 180 × 36 mm title block where every field has its own cell (owner, title, material, tolerances, drawing number, date, scale, revision, sheet, created/approved by). The projection symbol matches the page's first- or third-angle setting, and the scale is filled in for you. Every ISO A4–A0 and ANSI A–E size works in both orientations. Templates are written to your FreeCAD user folder under `Draw2D/Templates`; double-click a field on the page to edit it, as with any TechDraw template.

Which page a command acts on: a selected page (or a view on it), else the page in the active tab, else the only page in the document.

## Install by hand (until it is in the Addon Manager)

1. Download this repository as a ZIP (green **Code** button → **Download ZIP**) and unzip it, or `git clone` it.
2. Open FreeCAD's user Mod folder. On Windows it is `%APPDATA%\FreeCAD\v1-1\Mod`. On any system, type `App.getUserAppDataDir()` in FreeCAD's Python console (**View → Panels → Python console**); the Mod folder goes inside the folder it prints. Create the `Mod` folder if it doesn't exist.
3. Put the unzipped folder there as `Mod\Draw2D`, so that `Mod\Draw2D\InitGui.py` exists.
4. Restart FreeCAD and pick **Draw2D** from the workbench list.

## Limits of this version

- Fitting uses each view's bounding box, so a rotated or round part may get one scale step smaller than it strictly needs.
- With a template from elsewhere that has no ISO frame ids, Fit to page assumes a 180 × 60 mm title block in the bottom-right corner.
- Detail views keep their own scale and position when you use Fit to page.
- Centre marks are cosmetic lines: after Fit to page changes the scale, run the button again so the overhang matches the new scale.
- Stepped sections use TechDraw's complex section ("Offset" style), so only right-angle steps are supported.

## Tests

The logic that doesn't need the GUI is tested headless:

```
"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_tests.py
```

## Licence

LGPL-2.1-or-later, the same as FreeCAD. See [LICENSE](LICENSE).
