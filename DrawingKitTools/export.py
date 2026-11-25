# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export sheets to PDF, SVG or DXF in one go (like Onshape's Export).

PDF and SVG need the GUI (TechDraw draws them from the page on screen);
DXF works headless. Several sheets as PDF give one file with a page per
sheet; DXF and SVG give one file per sheet: 'Bracket - Sheet 1.dxf',
'Bracket - Sheet 2.dxf', ...
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


PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"


def wants_characteristics():
    """Write the characteristics list beside each PDF ('Characteristics'
    preference, on unless turned off)."""
    try:
        return App.ParamGet(PREFS).GetBool("Characteristics", True)
    except Exception:
        return True


def wants_one_pdf():
    """Several sheets as PDF: one file with a page per sheet ('OnePdf'
    preference, on unless turned off) rather than a file per sheet."""
    try:
        return App.ParamGet(PREFS).GetBool("OnePdf", True)
    except Exception:
        return True


def wants_real_text():
    """Draw the title block, notes and tables' words as real PDF text
    ('RealText' preference, on unless turned off)."""
    try:
        return App.ParamGet(PREFS).GetBool("RealText", True)
    except Exception:
        return True


def _warn(msg):
    App.Console.PrintWarning("DrawingKit: %s\n" % msg)


def add_text_layer(page, out):
    from . import textlayer
    try:
        # with real text there's usually nothing left for the hidden layer
        textlayer.add_text_layer(page, out)
    except Exception as e:
        _warn("text layer for %s failed: %s" % (out, e))


def properties(pages):
    """The PDF's document properties from the (first) sheet's title block:
    (standard info, DrawingKit's own keys)."""
    from . import __version__, textlayer
    t = textlayer._texts(pages[0])
    number = (t.get("drawing_number") or "").strip()
    rev = (t.get("revision_index") or "").strip()
    title = " ".join((t.get(k) or "").strip() for k in ("title", "title_2")).strip()
    subject = "Drawing %s" % number if number else ""
    if subject and rev:
        subject += ", revision %s" % rev
    info = {"Title": title or number or pages[0].Document.Label,
            "Author": (t.get("creator") or "").strip(),
            "Subject": subject,
            "Keywords": ", ".join(x for x in (number, rev and "Rev " + rev,
                                               (t.get("part_material") or "").strip()) if x),
            "Creator": "FreeCAD TechDraw with DrawingKit %s" % __version__}
    custom = {"DrawingNumber": number, "Revision": rev, "Sheets": str(len(pages))}
    return info, custom


def finish_pdf(pages, parts, out, characteristics=None):
    """Put the sheets' PDFs (each already with its text layer) together
    into ``out``: one page per sheet, the document properties filled in and
    (unless turned off) the characteristics list both inside the PDF and
    beside it. Returns the extra files written."""
    from . import pdfdoc, textlayer
    want = wants_characteristics() if characteristics is None else characteristics
    data = None
    if want:
        try:
            data = textlayer.combine_characteristics(
                [textlayer.characteristics_data(p, out) for p in pages])
        except Exception as e:
            _warn("characteristics for %s failed: %s" % (out, e))
    info, custom = properties(pages)
    att = []
    if data is not None:
        inner = dict(data, pdf_sha256=None, embedded=True)
        att.append((os.path.basename(textlayer.characteristics_path(out)),
                    textlayer.json_text(inner).encode("utf-8"), "application/json",
                    "DrawingKit characteristics list"))
    pdfdoc.write(out, parts, info=info, custom=custom, attachments=att)
    extra = []
    if data is not None:
        data["pdf_sha256"] = textlayer.pdf_sha256(out)
        extra.append(textlayer.save_json(data, textlayer.characteristics_path(out)))
    return extra


def _export_pdfs(pages, path, characteristics):
    """PDF: each sheet drawn by TechDraw, then put together by DrawingKit."""
    import tempfile
    import FreeCADGui as Gui
    import TechDrawGui
    one = len(pages) > 1 and wants_one_pdf()
    targets = [path] if one else sheet_paths(path, pages)
    groups = [(pages, path)] if one else [([p], t) for p, t in zip(pages, targets)]
    done = []
    tmpdir = tempfile.mkdtemp(prefix="drawingkit-")
    try:
        for group, out in groups:
            parts = []
            for i, page in enumerate(group):
                page.ViewObject.show()        # the page must be open to be drawn
                Gui.updateGui()
                part = os.path.join(tmpdir, "sheet%d.pdf" % (len(parts) + 1))
                restore = None
                if wants_real_text():
                    try:
                        from . import realtext
                        restore, _n = realtext.prepare(page)
                    except Exception as e:
                        _warn("real text for %s failed: %s" % (page.Label, e))
                try:
                    TechDrawGui.exportPageAsPdf(page, part)
                finally:
                    if restore is not None:
                        restore()
                if not os.path.exists(part):
                    _warn("%s was not drawn" % page.Label)
                    continue
                add_text_layer(page, part)
                parts.append(part)
            if not parts:
                continue
            try:
                extra = finish_pdf(group, parts, out, characteristics)
            except Exception as e:
                # an unexpected PDF: hand out TechDraw's own sheet files
                _warn("couldn't put %s together (%s); writing each sheet as it is" % (out, e))
                extra = []
                import shutil
                outs = [out] if len(parts) == 1 else sheet_paths(out, group)
                for part, o in zip(parts, outs):
                    shutil.copyfile(part, o)
                    done.append(o)
                continue
            done.append(out)
            done += extra
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return done


def export_pages(pages, path, fmt, characteristics=None):
    """Write the pages; returns the files written. PDF: one file for all
    the sheets (unless the 'OnePdf' preference is off), with a real text
    layer, the document properties and, unless turned off, the
    characteristics list inside it and beside it."""
    fmt = fmt.lower()
    try:
        from . import sheetcheck
        sheetcheck.report(pages)        # texts on part lines, tables off the frame
    except Exception as e:
        _warn("sheet check failed: %s" % e)
    if fmt == "pdf":
        return _export_pdfs(pages, path, characteristics)
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
            if fmt == "svg":
                TechDrawGui.exportPageAsSvg(page, out)
            else:
                raise ValueError("Unknown format %s" % fmt)
        if os.path.exists(out):
            done.append(out)
        else:
            App.Console.PrintWarning("DrawingKit: %s was not written\n" % out)
    return done
