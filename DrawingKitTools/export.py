# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export sheets to PDF, SVG or DXF in one go (like Onshape's Export).

PDF and SVG need the GUI (TechDraw draws them from the page on screen);
DXF works headless. Several sheets give one file per sheet:
'Bracket - Sheet 1.pdf', 'Bracket - Sheet 2.pdf', ...
"""

import os
import re

import FreeCAD as App

FORMATS = [("pdf", "PDF"), ("dxf", "DXF"), ("svg", "SVG")]


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Drawing"


def default_base(page):
    """Folder and file name to suggest: next to the FreeCAD file, named after
    the drawing number / part, else the document."""
    doc = page.Document
    folder = os.path.dirname(doc.FileName) if doc.FileName else os.path.expanduser("~")
    name = ""
    try:
        texts = dict(page.Template.EditableTexts)
        name = texts.get("drawing_number") or texts.get("title") or ""
    except Exception:
        pass
    return folder, _safe(name or doc.Label)


def sheet_paths(path, pages):
    """File names for each sheet: the chosen name for one sheet; with
    several, ' - Sheet N' added before the extension."""
    if len(pages) == 1:
        return [path]
    root, ext = os.path.splitext(path)
    out = []
    for i, p in enumerate(pages):
        m = re.match(r"Sheet (\d+)", p.Label)
        n = m.group(1) if m else str(i + 1)
        out.append("%s - Sheet %s%s" % (root, n, ext))
    return out


def export_pages(pages, path, fmt):
    """Write each page; returns the files written."""
    fmt = fmt.lower()
    paths = sheet_paths(path, pages)
    done = []
    for page, out in zip(pages, paths):
        if fmt == "dxf":
            import TechDraw
            TechDraw.writeDXFPage(page, out)
        else:
            import FreeCADGui as Gui
            import TechDrawGui
            page.ViewObject.show()        # the page must be open to be drawn
            Gui.updateGui()
            if fmt == "pdf":
                TechDrawGui.exportPageAsPdf(page, out)
            elif fmt == "svg":
                TechDrawGui.exportPageAsSvg(page, out)
            else:
                raise ValueError("Unknown format %s" % fmt)
        if os.path.exists(out):
            done.append(out)
        else:
            App.Console.PrintWarning("DrawingKit: %s was not written\n" % out)
    return done
