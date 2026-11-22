# Draw2D

A FreeCAD 1.1 workbench that makes TechDraw drawings quicker. It sits on top of TechDraw and adds one toolbar, **Draw2D**. Every page and view it creates is an ordinary TechDraw object, so you can keep editing them with the normal TechDraw tools.

Status: first version (0.1.0), not yet in the Addon Manager.

## Commands

| Button | What it does |
|---|---|
| **New drawing sheet** | Pick a paper size (ISO A4–A0, ANSI A–E) and orientation, and get a page built from FreeCAD's bundled template. If a part is selected you can add its standard views in the same step. |
| **Add standard views, fitted** | With a 3D part selected: adds front, top and right views (and an optional isometric) as a projection group. The scale is the largest standard scale (1:1, 1:2, 1:5, 1:10 … 2:1, 5:1 …) that fits the drawing area beside the title block. The views are centred. |
| **Fit to page** | Picks the scale again for an existing page so the projection group and any section views fit, then re-centres them. Use it after adding sections or changing the part. |
| **Section, auto-lettered** | With a view selected: adds a section through the middle of the part, lettered with the next free letter (A-A, B-B …; I, O and Q are skipped). The section is placed next to the view. Double-click it to move the cut with TechDraw's own section dialog. |
| **Fast editing on/off** | On: the page stops updating itself and views switch to Coarse View, so small edits don't freeze FreeCAD. Off: views go back to full quality and the page redraws once. |

Which page a command acts on: a selected page (or a view on it), else the page in the active tab, else the only page in the document.

## Install by hand (until it is in the Addon Manager)

1. Download this repository as a ZIP (green **Code** button → **Download ZIP**) and unzip it, or `git clone` it.
2. Open FreeCAD's user Mod folder. On Windows it is `%APPDATA%\FreeCAD\v1-1\Mod`. On any system, type `App.getUserAppDataDir()` in FreeCAD's Python console (**View → Panels → Python console**); the Mod folder goes inside the folder it prints. Create the `Mod` folder if it doesn't exist.
3. Put the unzipped folder there as `Mod\Draw2D`, so that `Mod\Draw2D\InitGui.py` exists.
4. Restart FreeCAD and pick **Draw2D** from the workbench list.

## Limits of this version

- Fitting uses each view's bounding box, so a rotated or round part may get one scale step smaller than it strictly needs.
- Templates with no ISO frame ids (the ANSI ones) are given a 180 × 60 mm title block in the bottom-right corner.
- Detail views keep their own scale and position when you use Fit to page.
- Sections cut through the middle of the part; move the cut afterwards in TechDraw's section dialog.

## Tests

The logic that doesn't need the GUI is tested headless:

```
"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_tests.py
```

## Licence

LGPL-2.1-or-later, the same as FreeCAD. See [LICENSE](LICENSE).
