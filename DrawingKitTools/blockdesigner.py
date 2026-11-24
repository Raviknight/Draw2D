# SPDX-License-Identifier: LGPL-2.1-or-later
"""The title block designer: draw your own title block.

Click a cell to pick it, drag a line between cells to move it, split and
merge cells, and say what each cell holds (a field, fixed text, a logo,
the tolerances by size, the projection symbol). Saved designs show up as
layouts in the Title block form and for new sheets.
"""

import re

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui
try:
    from PySide import QtSvg
except ImportError:            # pragma: no cover
    from PySide6 import QtSvg

from . import blocks

EDGE_PX = 5
SNAP = 0.5
EXTRA_FIELDS = [("checked_date", "Checked date"), ("approved_date", "Approved date"),
                ("drawn_date", "Drawn date"), ("part_number", "Part number"),
                ("project", "Project"), ("weight", "Weight"), ("eco", "ECO / ECN"),
                ("next_assy", "Next assembly"), ("heat_treat", "Heat treatment"),
                ("inspection_code", "Inspection code"), ("prefix", "Prefix"),
                ("suffix", "Suffix"), ("cage_code", "CAGE code")]


def known_fields():
    from . import titleblock as tb
    out = [(k, tb.LABELS.get(k, k)) for k in tb.ORDER]
    out += [f for f in EXTRA_FIELDS if f[0] not in dict(out)]
    return out


class BlockCanvas(QtWidgets.QWidget):
    """Draws the design and lets you pick cells and drag the lines."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setMinimumSize(560, 200)
        self.setMouseTracking(True)
        self.renderer = None
        self.drag = None              # (cell index, side)
        self.k, self.ox, self.oy = 1.0, 0.0, 0.0
        self.margin = 2.0

    @property
    def d(self):
        return self.owner.design

    def refresh(self):
        svg = blocks.preview_svg(self.d, margin=self.margin)
        self.renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
        self.update()

    def _layout(self):
        W = self.d["width"] + 2 * self.margin
        H = self.d["height"] + 2 * self.margin
        k = min((self.width() - 8) / W, (self.height() - 8) / H)
        self.k = k
        self.ox = (self.width() - W * k) / 2.0 + self.margin * k
        self.oy = (self.height() - H * k) / 2.0 + self.margin * k
        return QtCore.QRectF(self.ox - self.margin * k, self.oy - self.margin * k, W * k, H * k)

    def to_mm(self, pos):
        return (pos.x() - self.ox) / self.k, (pos.y() - self.oy) / self.k

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#ffffff"))
        r = self._layout()
        if self.renderer is not None:
            self.renderer.render(p, r)
        i = self.owner.current
        if i is not None and i < len(self.d["cells"]):
            c = self.d["cells"][i]
            pen = QtGui.QPen(QtGui.QColor("#1e88e5"))
            pen.setWidthF(2.5)
            p.setPen(pen)
            p.setBrush(QtGui.QColor(30, 136, 229, 28))
            p.drawRect(QtCore.QRectF(self.ox + c["x"] * self.k, self.oy + c["y"] * self.k,
                                     c["w"] * self.k, c["h"] * self.k))
        p.end()

    def _edge_at(self, pos):
        """(cell index, side) of an inner line under the mouse, or None."""
        x, y = self.to_mm(pos)
        tol = EDGE_PX / self.k
        order = list(range(len(self.d["cells"])))
        if self.owner.current is not None:
            order.insert(0, self.owner.current)
        for i in order:
            c = self.d["cells"][i]
            inside_y = c["y"] - tol <= y <= c["y"] + c["h"] + tol
            inside_x = c["x"] - tol <= x <= c["x"] + c["w"] + tol
            for side, pos_, inside, outline in (
                    ("right", c["x"] + c["w"], inside_y, self.d["width"]),
                    ("left", c["x"], inside_y, 0.0),
                    ("bottom", c["y"] + c["h"], inside_x, self.d["height"]),
                    ("top", c["y"], inside_x, 0.0)):
                along = x if side in ("left", "right") else y
                if inside and abs(along - pos_) <= tol and abs(pos_ - outline) > 1e-6:
                    return i, side
        return None

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        pos = e.position() if hasattr(e, "position") else e.pos()
        edge = self._edge_at(pos)
        if edge is not None:
            self.owner.remember()
            self.drag = edge
            return
        x, y = self.to_mm(pos)
        i = blocks.cell_at(self.d, x, y)
        if i is not None:
            self.owner.select(i)

    def mouseMoveEvent(self, e):
        pos = e.position() if hasattr(e, "position") else e.pos()
        if self.drag is not None:
            i, side = self.drag
            x, y = self.to_mm(pos)
            v = x if side in ("left", "right") else y
            v = round(v / SNAP) * SNAP
            blocks.move_edge(self.d, i, side, v)
            self.refresh()
            self.owner.show_cell()
            return
        edge = self._edge_at(pos)
        if edge is None:
            self.unsetCursor()
        elif edge[1] in ("left", "right"):
            self.setCursor(QtCore.Qt.SplitHCursor)
        else:
            self.setCursor(QtCore.Qt.SplitVCursor)

    def mouseReleaseEvent(self, _e):
        if self.drag is not None:
            self.drag = None
            self.owner.changed()


class TitleBlockDesigner(QtWidgets.QDialog):
    """Make and change your own title blocks."""

    def __init__(self, parent=None, page=None, start=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("Title block designer")
        self.page = page
        self.use_on_page = None
        self.design = blocks.preset_size_table()
        self.current = None
        self.undo = []
        self._loading = False
        lay = QtWidgets.QVBoxLayout(self)

        # design: open / new, name, size
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Open:"))
        self.open_combo = QtWidgets.QComboBox()
        self.open_combo.setMinimumWidth(220)
        top.addWidget(self.open_combo)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Name:"))
        self.name = QtWidgets.QLineEdit()
        self.name.setMinimumWidth(180)
        self.name.editingFinished.connect(self._name_changed)
        top.addWidget(self.name)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Size:"))
        self.bw = self._spin(20, 600, " mm wide")
        self.bh = self._spin(5, 300, " mm high")
        self.bw.editingFinished.connect(self._block_size)
        self.bh.editingFinished.connect(self._block_size)
        top.addWidget(self.bw)
        top.addWidget(self.bh)
        top.addStretch(1)
        lay.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.canvas = BlockCanvas(self)
        left.addWidget(self.canvas, 1)
        hint = QtWidgets.QLabel("Click a cell to pick it. Drag a line between cells to move it.")
        hint.setStyleSheet("color:#666")
        left.addWidget(hint)
        ops = QtWidgets.QHBoxLayout()
        for text, tip, fn in (
                ("Split side by side", "Cut the cell into a left and a right half",
                 lambda: self._op(lambda: blocks.split(self.design, self.current, "h"))),
                ("Split top / bottom", "Cut the cell into a top and a bottom half",
                 lambda: self._op(lambda: blocks.split(self.design, self.current, "v"))),
                ("Merge right", "Join with the cell on the right (same height)",
                 lambda: self._op(lambda: blocks.merge(self.design, self.current, "right"), True)),
                ("Merge down", "Join with the cell below (same width)",
                 lambda: self._op(lambda: blocks.merge(self.design, self.current, "below"), True)),
                ("Undo", "Undo the last change", self._undo)):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            ops.addWidget(b)
        ops.addStretch(1)
        left.addLayout(ops)
        mid.addLayout(left, 1)

        # the picked cell
        box = QtWidgets.QGroupBox("Cell")
        form = QtWidgets.QFormLayout(box)
        self.kind = QtWidgets.QComboBox()
        for k in blocks.KINDS:
            self.kind.addItem(blocks.KIND_NAMES[k], k)
        self.kind.currentIndexChanged.connect(self._edited)
        form.addRow("Holds", self.kind)
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("small caption in the corner, e.g. DRAWN BY")
        self.label.editingFinished.connect(self._edited)
        form.addRow("Caption", self.label)
        self.field = QtWidgets.QComboBox()
        self.field.setEditable(True)
        for key, lab in known_fields():
            self.field.addItem("%s  -  %s" % (key, lab), key)
        self.field.setToolTip("Which value goes here. Pick one, or type a name of your own "
                              "(letters, digits, _): it shows up in the Title block form.")
        self.field.lineEdit().editingFinished.connect(self._edited)
        self.field.activated.connect(lambda _i: self._edited())
        form.addRow("Field", self.field)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setFixedHeight(52)
        self.text.setToolTip("Fixed text, or the starting value of a field. {size} is the "
                             "sheet size letter.")
        self.text.textChanged.connect(self._text_changed)
        form.addRow("Text", self.text)
        self.size = QtWidgets.QDoubleSpinBox()
        self.size.setRange(0.8, 30.0)
        self.size.setSingleStep(0.25)
        self.size.setSuffix(" mm")
        self.size.valueChanged.connect(self._edited)
        form.addRow("Text height", self.size)
        row = QtWidgets.QHBoxLayout()
        self.bold = QtWidgets.QCheckBox("Bold")
        self.vertical = QtWidgets.QCheckBox("Runs upwards")
        self.bold.toggled.connect(self._edited)
        self.vertical.toggled.connect(self._edited)
        row.addWidget(self.bold)
        row.addWidget(self.vertical)
        form.addRow("", row)
        self.align = QtWidgets.QComboBox()
        for a_ in blocks.ALIGNS:
            self.align.addItem(a_.capitalize(), a_)
        self.align.currentIndexChanged.connect(self._edited)
        form.addRow("Align", self.align)
        self.rows = QtWidgets.QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setToolTip("Rows of size ranges; the last one takes the angle tolerance")
        self.rows.valueChanged.connect(self._edited)
        form.addRow("Rows", self.rows)
        pic = QtWidgets.QHBoxLayout()
        self.pic_btn = QtWidgets.QPushButton("Choose picture...")
        self.pic_btn.clicked.connect(self._pick_picture)
        self.pic_clear = QtWidgets.QPushButton("Remove")
        self.pic_clear.clicked.connect(self._clear_picture)
        pic.addWidget(self.pic_btn)
        pic.addWidget(self.pic_clear)
        form.addRow("Picture", pic)
        sz = QtWidgets.QHBoxLayout()
        self.cw = self._spin(blocks.MIN_CELL, 600, " mm")
        self.ch = self._spin(blocks.MIN_CELL, 300, " mm")
        self.cw.editingFinished.connect(lambda: self._cell_size("w"))
        self.ch.editingFinished.connect(lambda: self._cell_size("h"))
        sz.addWidget(QtWidgets.QLabel("W"))
        sz.addWidget(self.cw)
        sz.addWidget(QtWidgets.QLabel("H"))
        sz.addWidget(self.ch)
        form.addRow("Size", sz)
        self.cell_box = box
        box.setMinimumWidth(330)
        mid.addWidget(box)
        lay.addLayout(mid, 1)

        self.problem = QtWidgets.QLabel()
        self.problem.setStyleSheet("color:#c0392b")
        self.problem.setWordWrap(True)
        lay.addWidget(self.problem)
        btns = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("Save to my title blocks")
        save.clicked.connect(lambda: self._save("mine"))
        comp = QtWidgets.QPushButton("Save to company title blocks")
        comp.setToolTip("Next to the company defaults file, so everyone using that file gets it")
        comp.clicked.connect(lambda: self._save("company"))
        dele = QtWidgets.QPushButton("Delete...")
        dele.clicked.connect(self._delete)
        btns.addWidget(save)
        btns.addWidget(comp)
        btns.addWidget(dele)
        btns.addStretch(1)
        if page is not None:
            use = QtWidgets.QPushButton("Save and use on this sheet")
            use.clicked.connect(self._use)
            btns.addWidget(use)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        lay.addLayout(btns)

        self._fill_open_combo(start)
        self.open_combo.activated.connect(self._open)
        if start and blocks.find(start):
            self.load(blocks.copy_of(blocks.find(start)))
        else:
            self.load(self.design)
        self.resize(1150, 560)

    # -- helpers -------------------------------------------------------------
    def _spin(self, lo, hi, suffix):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setSingleStep(0.5)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        return s

    def _fill_open_combo(self, select=None):
        c = self.open_combo
        c.blockSignals(True)
        c.clear()
        c.addItem("- pick a design -", None)
        for key, (d, _path, where) in sorted(blocks.designs().items()):
            c.addItem("%s  (%s)" % (d["name"], "company" if where == "company" else "mine"),
                      "design:" + key)
        for text, _fn in blocks.PRESETS:
            c.addItem("New: " + text, "preset:" + text)
        idx = c.findData("design:" + select) if select else 0
        c.setCurrentIndex(max(0, idx))
        c.blockSignals(False)

    def _open(self, _i):
        data = self.open_combo.currentData()
        if not data:
            return
        what, _, key = data.partition(":")
        if what == "design":
            d = blocks.find(key)
            if d:
                self.load(blocks.copy_of(d))
        else:
            self.load(dict(blocks.PRESETS)[key]())

    def load(self, d):
        self.design = blocks.clean(d)
        self.undo = []
        self.current = 0 if self.design["cells"] else None
        self.name.setText(self.design["name"])
        self._show_size()
        self.changed(remember=False)
        self.show_cell()

    def _show_size(self):
        for s, v in ((self.bw, self.design["width"]), (self.bh, self.design["height"])):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)

    def remember(self):
        self.undo.append(blocks.copy_of(self.design))
        del self.undo[:-50]

    def _undo(self):
        if self.undo:
            self.design = self.undo.pop()
            if self.current is not None and self.current >= len(self.design["cells"]):
                self.current = len(self.design["cells"]) - 1
            self._show_size()
            self.changed(remember=False)
            self.show_cell()

    def _op(self, fn, keep_current=False):
        if self.current is None:
            return
        self.remember()
        try:
            res = fn()
        except ValueError as e:
            self.undo.pop()
            self.problem.setText(str(e))
            return
        if isinstance(res, int):
            self.current = res
        self.changed(remember=False)
        self.show_cell()

    def changed(self, remember=False):
        self.canvas.refresh()
        probs = blocks.problems(self.design)
        self.problem.setText("  ".join(probs))

    def select(self, i):
        self.current = i
        self.show_cell()
        self.canvas.update()

    # -- the cell panel ----------------------------------------------------
    def show_cell(self):
        self._loading = True
        c = self.design["cells"][self.current] if self.current is not None else None
        self.cell_box.setEnabled(c is not None)
        if c is not None:
            self.kind.setCurrentIndex(self.kind.findData(c["kind"]))
            self.label.setText(c["label"])
            i = self.field.findData(c["field"])
            if i >= 0:
                self.field.setCurrentIndex(i)
            else:
                self.field.setEditText(c["field"])
            if self.text.toPlainText() != c["text"]:
                self.text.setPlainText(c["text"])
            self.size.setValue(c["size"])
            self.bold.setChecked(c["bold"])
            self.vertical.setChecked(c["vertical"])
            self.align.setCurrentIndex(self.align.findData(c["align"]))
            self.rows.setValue(c["rows"])
            self.cw.setValue(c["w"])
            self.ch.setValue(c["h"])
            k = c["kind"]
            self.field.setEnabled(k == "field")
            self.text.setEnabled(k in ("field", "text"))
            self.size.setEnabled(k in ("field", "text", "tolerance"))
            self.bold.setEnabled(k in ("field", "text", "tolerance"))
            self.vertical.setEnabled(k == "text")
            self.align.setEnabled(k in ("field", "text", "logo"))
            self.rows.setEnabled(k == "tolerance")
            self.pic_btn.setEnabled(k == "logo")
            self.pic_clear.setEnabled(k == "logo" and bool(c["image"]))
            self.label.setEnabled(k != "text")
        self._loading = False

    def _field_key(self):
        t = self.field.currentText()
        i = self.field.findText(t)
        if i >= 0 and self.field.itemData(i):
            return self.field.itemData(i)
        key = t.split("  -  ")[0].strip()
        return re.sub(r"[^A-Za-z0-9_]", "_", key).strip("_")

    def _edited(self, *_a):
        if self._loading or self.current is None:
            return
        c = self.design["cells"][self.current]
        new = dict(c)
        new["kind"] = self.kind.currentData()
        new["label"] = self.label.text()
        new["field"] = self._field_key() if new["kind"] == "field" else c["field"]
        new["size"] = self.size.value()
        new["bold"] = self.bold.isChecked()
        new["vertical"] = self.vertical.isChecked()
        new["align"] = self.align.currentData()
        new["rows"] = self.rows.value()
        if new["kind"] == "field" and not new["field"]:
            new["field"] = "field_%d" % (self.current + 1)
        if new["kind"] == "tolerance" and c["kind"] != "tolerance" and not new["label"]:
            new["label"] = "TOLERANCE"
        if new != c:
            self.remember()
            c.update(new)
            self.changed()
            self.show_cell()

    def _text_changed(self):
        if self._loading or self.current is None:
            return
        c = self.design["cells"][self.current]
        t = self.text.toPlainText()
        if c["kind"] == "field":
            t = t.splitlines()[0] if t else ""      # a field is one line
        if t != c["text"]:
            c["text"] = t
            self.changed()

    def _cell_size(self, which):
        if self._loading or self.current is None:
            return
        c = self.design["cells"][self.current]
        val = (self.cw if which == "w" else self.ch).value()
        if abs(val - c[which]) < 1e-6:
            return
        self.remember()
        if which == "w":
            if abs(c["x"] + c["w"] - self.design["width"]) > 1e-6:
                blocks.move_edge(self.design, self.current, "right", c["x"] + val)
            else:
                blocks.move_edge(self.design, self.current, "left", c["x"] + c["w"] - val)
        else:
            if abs(c["y"] + c["h"] - self.design["height"]) > 1e-6:
                blocks.move_edge(self.design, self.current, "bottom", c["y"] + val)
            else:
                blocks.move_edge(self.design, self.current, "top", c["y"] + c["h"] - val)
        self.changed()
        self.show_cell()

    def _block_size(self):
        w, h = self.bw.value(), self.bh.value()
        if abs(w - self.design["width"]) < 1e-6 and abs(h - self.design["height"]) < 1e-6:
            return
        self.remember()
        blocks.resize(self.design, w, h)
        self.changed()
        self.show_cell()

    def _name_changed(self):
        self.design["name"] = self.name.text().strip() or "Title block"

    def _pick_picture(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Logo or picture", "", "Pictures (*.png *.jpg *.jpeg *.svg *.bmp *.gif)")
        if not path or self.current is None:
            return
        self.remember()
        try:
            blocks.load_image(self.design["cells"][self.current], path)
        except (OSError, ValueError) as e:
            self.undo.pop()
            self.problem.setText(str(e))
            return
        self.changed()
        self.show_cell()

    def _clear_picture(self):
        if self.current is None:
            return
        self.remember()
        c = self.design["cells"][self.current]
        c["image"], c["image_type"] = "", ""
        self.changed()
        self.show_cell()

    # -- files ---------------------------------------------------------------
    def _save(self, where):
        self._name_changed()
        probs = blocks.problems(self.design)
        if probs:
            QtWidgets.QMessageBox.warning(self, "Title block", "Fix this first:\n" + "\n".join(probs))
            return None
        key = blocks.slug(self.design["name"])
        hit = blocks.designs().get(key)
        if hit and hit[2] == where:
            ok = QtWidgets.QMessageBox.question(
                self, "Title block", "Replace the saved '%s'? Sheets using it in the drawings "
                "you have open change too." % hit[0]["name"])
            if ok != QtWidgets.QMessageBox.Yes:
                return None
        try:
            path = blocks.save(self.design, where)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Title block", str(e))
            return None
        self._fill_open_combo(key)
        from . import titleblock
        n = len(titleblock.refresh_design(key)) if hit else 0
        self.problem.setText("Saved to %s%s" % (path, "; %d open sheet(s) updated" % n if n else ""))
        App.Console.PrintMessage("DrawingKit: title block saved to %s\n" % path)
        return key

    def _delete(self):
        key = blocks.slug(self.design["name"])
        hit = blocks.designs().get(key)
        if not hit:
            self.problem.setText("'%s' isn't saved." % self.design["name"])
            return
        ok = QtWidgets.QMessageBox.question(
            self, "Title block", "Delete the saved title block '%s' (%s)?\n%s"
            % (hit[0]["name"], hit[2], hit[1]))
        if ok == QtWidgets.QMessageBox.Yes:
            blocks.delete(key)
            self._fill_open_combo()
            self.problem.setText("Deleted %s" % hit[1])

    def _use(self):
        """Save (over the saved design of this name, wherever it is) and put
        it on the sheet."""
        self._name_changed()
        probs = blocks.problems(self.design)
        if probs:
            QtWidgets.QMessageBox.warning(self, "Title block", "Fix this first:\n" + "\n".join(probs))
            return
        key = blocks.slug(self.design["name"])
        hit = blocks.designs().get(key)
        try:
            blocks.save(self.design, hit[2] if hit else "mine")
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Title block", str(e))
            return
        if hit:
            from . import titleblock
            titleblock.refresh_design(key)     # other sheets already using it
        self.use_on_page = "custom:" + key
        self.accept()


def run(page=None, start=None, parent=None):
    """Open the designer. Returns 'custom:<design>' to use on the page, or None."""
    dlg = TitleBlockDesigner(parent, page, start)
    res = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    return dlg.use_on_page if res else None
