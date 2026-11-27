# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small pop-up panels next to the mouse, in place of big dialogs.

Each panel shows only what the task needs, in one or two rows; extras sit
behind a small toggle. Enter accepts, Esc cancels, clicking elsewhere
cancels.
"""

import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

STYLE = """
QDialog#DrawingKitPopover { background: palette(window); border: 1px solid palette(mid);
                             border-radius: 6px; }
QLabel#title { font-weight: bold; }
QLabel { color: palette(text); }
QToolButton { padding: 2px 6px; border: 1px solid palette(mid); border-radius: 3px; }
QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { padding: 1px 3px; }
"""


class Popover(QtWidgets.QDialog):
    """Frameless panel at the mouse. Build rows with add_row(); exec_()."""

    def __init__(self, title=None, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setObjectName("DrawingKitPopover")
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.box = QtWidgets.QVBoxLayout(self)
        self.box.setContentsMargins(8, 6, 8, 6)
        self.box.setSpacing(4)
        if title:
            head = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(title)
            lab.setObjectName("title")
            head.addWidget(lab)
            head.addStretch(1)
            ok = QtWidgets.QToolButton()
            ok.setText("✓")
            ok.setToolTip("OK (Enter)")
            ok.clicked.connect(self.accept)
            cancel = QtWidgets.QToolButton()
            cancel.setText("✕")
            cancel.setToolTip("Cancel (Esc)")
            cancel.clicked.connect(self.reject)
            head.addWidget(ok)
            head.addWidget(cancel)
            self.box.addLayout(head)

    def add_row(self, *widgets, stretch=False):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        for w in widgets:
            if isinstance(w, str):
                lab = QtWidgets.QLabel(w)
                row.addWidget(lab)
            elif isinstance(w, QtWidgets.QLayout):
                row.addLayout(w)
            else:
                row.addWidget(w)
        if stretch:
            row.addStretch(1)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.box.addWidget(holder)
        return holder

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            # Enter means OK in a text or number field; on a drop-down it only
            # picks the item (so choosing '±' doesn't close the panel early)
            focus = self.focusWidget()
            if isinstance(focus, QtWidgets.QComboBox):
                return
            self.accept()
            return
        super().keyPressEvent(event)

    def run(self):
        """Show at the mouse (kept on screen). True if accepted."""
        self.adjustSize()
        pos = QtGui.QCursor.pos() + QtCore.QPoint(14, 14)
        screen = QtWidgets.QApplication.screenAt(pos) if hasattr(QtWidgets.QApplication, "screenAt") else None
        if screen is not None:
            g = screen.availableGeometry()
            if pos.x() + self.width() > g.right():
                pos.setX(g.right() - self.width())
            if pos.y() + self.height() > g.bottom():
                pos.setY(g.bottom() - self.height())
        self.move(pos)
        first = getattr(self, "focus_on", None) or self.findChild(QtWidgets.QLineEdit)
        if first is not None:
            QtCore.QTimer.singleShot(0, lambda: (first.setFocus(), first.selectAll()))
        # call Qt's own exec (subclasses alias exec_ to run(), so don't use self.exec_)
        base = getattr(QtWidgets.QDialog, "exec_", None) or QtWidgets.QDialog.exec
        return bool(base(self))


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------
def field(text="", width=60, placeholder="", tip=""):
    e = QtWidgets.QLineEdit(text)
    e.setFixedWidth(width)
    if placeholder:
        e.setPlaceholderText(placeholder)
    if tip:
        e.setToolTip(tip)
    return e


def toggle(text, checked=False, tip=""):
    b = QtWidgets.QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setChecked(checked)
    if tip:
        b.setToolTip(tip)
    return b


class Cycle(QtWidgets.QToolButton):
    """Button that cycles through values on click, e.g. none / Ⓜ / Ⓛ."""

    def __init__(self, values, labels, value="", tip=""):
        super().__init__()
        self.values, self.labels = list(values), list(labels)
        self.i = self.values.index(value) if value in self.values else 0
        self.setToolTip(tip)
        self.clicked.connect(self._next)
        self._show()

    def _next(self):
        self.i = (self.i + 1) % len(self.values)
        self._show()

    def _show(self):
        self.setText(self.labels[self.i])

    def value(self):
        return self.values[self.i]


def mmc_button(value="", tip=None, datum=False):
    """Click to cycle: none / MMC Ⓜ / LMC Ⓛ / RFS Ⓢ (ASME), and for a datum
    also ▷ translation (ASME: the datum simulator may move)."""
    values = ["", "MMC", "LMC", "RFS"] + (["TR"] if datum else [])
    labels = [" · ", "Ⓜ", "Ⓛ", "Ⓢ"] + (["▷"] if datum else [])
    tip = tip or ("Click to change: none / MMC Ⓜ / LMC Ⓛ / RFS Ⓢ"
                  + (" / translation ▷" if datum else ""))
    return Cycle(values, labels, value, tip)


def combo(items, current=None, width=None):
    """items: [(value, label)]"""
    c = QtWidgets.QComboBox()
    for v, lab in items:
        c.addItem(lab, v)
    if current is not None:
        for i in range(c.count()):
            if c.itemData(i) == current:
                c.setCurrentIndex(i)
    if width:
        c.setFixedWidth(width)
    return c


class TextStyleRow:
    """Size (mm, pick or type) and font widgets for one pop-up row."""

    def __init__(self, size=3.5, font=None, sizes=(2.5, 3.5, 5.0, 7.0, 10.0)):
        if not font:
            from . import fonts
            font = fonts.drawing_font()
        self.size = QtWidgets.QComboBox()
        self.size.setEditable(True)
        for s in sizes:
            self.size.addItem("%g" % s)
        self.size.setEditText("%g" % size)
        self.size.setMinimumWidth(self.size.fontMetrics().horizontalAdvance("10.00") + 34
                                  if hasattr(self.size.fontMetrics(), "horizontalAdvance") else 72)
        self.size.setToolTip("Text height in mm (pick or type)")
        v = QtGui.QDoubleValidator(0.5, 100.0, 2, self.size)
        v.setLocale(QtCore.QLocale.c())
        self.size.setValidator(v)
        self.font = QtWidgets.QFontComboBox()
        self.font.setCurrentFont(QtGui.QFont(font))
        # a font that isn't installed shows as a stand-in; keep the real name
        # unless the user picks another one
        self._font0 = font
        self._shown0 = self.font.currentFont().family()
        self.font.setFixedWidth(140)
        self.font.setToolTip("Font (DejaVu Sans: plain, clear print font; osifont: "
                             "hand-lettered ISO style)")

    def widgets(self):
        return ["Size", self.size, "mm", self.font]

    def value(self):
        try:
            size = float(self.size.currentText().replace(",", "."))
        except ValueError:
            size = None
        fam = self.font.currentFont().family()
        if fam == self._shown0:
            fam = self._font0
        return (size if size and size > 0.4 else None), fam


class TableEditor(QtWidgets.QDialog):
    """A table's title and cells in a grid, like a spreadsheet: the first
    row holds the headings. Rows and columns can be set by number or put in
    and taken out where the cursor is; cells copied from a spreadsheet can
    be pasted in (Ctrl+V), several at once."""

    def __init__(self, heading, title="", headers=None, rows=None, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle(heading)
        headers = list(headers or ["", "", ""])
        rows = [list(r) for r in (rows or [[""] * len(headers)])]
        lay = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Title"))
        self.title = QtWidgets.QLineEdit(title)
        self.title.setPlaceholderText("optional, e.g. SPLINE DATA")
        top.addWidget(self.title, 1)
        lay.addLayout(top)

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Rows"))
        self.n_rows = QtWidgets.QSpinBox()
        self.n_rows.setRange(1, 200)
        self.n_rows.setToolTip("Rows under the headings")
        bar.addWidget(self.n_rows)
        bar.addWidget(QtWidgets.QLabel("Columns"))
        self.n_cols = QtWidgets.QSpinBox()
        self.n_cols.setRange(1, 40)
        bar.addWidget(self.n_cols)
        bar.addSpacing(12)
        for text, tip, fn in (("+ Row", "Put a row in under the current one", self.add_row),
                              ("− Row", "Take out the current row", self.del_row),
                              ("+ Column", "Put a column in right of the current one", self.add_col),
                              ("− Column", "Take out the current column", self.del_col)):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.grid = QtWidgets.QTableWidget(0, 0)
        self.grid.installEventFilter(self)
        lay.addWidget(self.grid, 1)
        hint = QtWidgets.QLabel("The first row is the headings. Paste cells copied from a "
                                "spreadsheet with Ctrl+V; Delete clears the selected cells.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                         | QtWidgets.QDialogButtonBox.Cancel)
        for b in box.buttons():
            b.setAutoDefault(False)
            b.setDefault(False)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._fill(headers, rows)
        self.n_rows.valueChanged.connect(self._set_rows)
        self.n_cols.valueChanged.connect(self._set_cols)
        self.resize(min(1100, 140 + 110 * max(3, len(headers))),
                    min(700, 230 + 28 * (len(rows) + 1)))

    # -- grid -------------------------------------------------------------
    def _fill(self, headers, rows):
        n = max([len(headers)] + [len(r) for r in rows])
        self.grid.setRowCount(len(rows) + 1)
        self.grid.setColumnCount(n)
        for r, row in enumerate([headers] + rows):
            for c in range(n):
                self._set(r, c, row[c] if c < len(row) else "")
        self._labels()

    def _set(self, r, c, text):
        it = self.grid.item(r, c)
        if it is None:
            it = QtWidgets.QTableWidgetItem()
            self.grid.setItem(r, c, it)
        it.setText(str(text))

    def _labels(self):
        self.grid.setVerticalHeaderLabels(
            ["Headings"] + [str(i) for i in range(1, self.grid.rowCount())])
        letters = [chr(65 + i) if i < 26 else "A" + chr(65 + i - 26)
                   for i in range(self.grid.columnCount())]
        self.grid.setHorizontalHeaderLabels(letters)
        bold = QtGui.QFont(self.grid.font())
        bold.setBold(True)
        for c in range(self.grid.columnCount()):
            it = self.grid.item(0, c)
            if it is None:
                self._set(0, c, "")
                it = self.grid.item(0, c)
            it.setFont(bold)
        for w, n in ((self.n_rows, self.grid.rowCount() - 1), (self.n_cols, self.grid.columnCount())):
            w.blockSignals(True)
            w.setValue(max(1, n))
            w.blockSignals(False)
        self.grid.resizeColumnsToContents()
        for c in range(self.grid.columnCount()):
            self.grid.setColumnWidth(c, max(70, min(320, self.grid.columnWidth(c) + 12)))

    def _set_rows(self, n):
        self.grid.setRowCount(n + 1)
        self._labels()

    def _set_cols(self, n):
        self.grid.setColumnCount(n)
        self._labels()

    def _here(self):
        r, c = self.grid.currentRow(), self.grid.currentColumn()
        return (self.grid.rowCount() - 1 if r < 0 else r,
                self.grid.columnCount() - 1 if c < 0 else c)

    def add_row(self):
        r, _ = self._here()
        self.grid.insertRow(r + 1)
        self._labels()
        self.grid.setCurrentCell(r + 1, max(0, self.grid.currentColumn()))

    def del_row(self):
        r, _ = self._here()
        if r <= 0 or self.grid.rowCount() <= 2:
            return                  # the headings stay, and one row under them
        self.grid.removeRow(r)
        self._labels()

    def add_col(self):
        _, c = self._here()
        self.grid.insertColumn(c + 1)
        self._labels()
        self.grid.setCurrentCell(max(0, self.grid.currentRow()), c + 1)

    def del_col(self):
        _, c = self._here()
        if self.grid.columnCount() <= 1:
            return
        self.grid.removeColumn(c)
        self._labels()

    def paste(self, text):
        """Cells copied from a spreadsheet (tabs and new lines), from the
        current cell on; the grid grows to take them."""
        lines = text.replace("\r", "").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return
        r0, c0 = max(0, self.grid.currentRow()), max(0, self.grid.currentColumn())
        cells = [ln.split("\t") for ln in lines]
        if len(cells) == 1 and len(cells[0]) == 1:
            for it in self.grid.selectedItems() or [None]:
                if it is None:
                    self._set(r0, c0, cells[0][0])
                else:
                    it.setText(cells[0][0])
            return
        need_r = r0 + len(cells)
        need_c = c0 + max(len(row) for row in cells)
        if need_r > self.grid.rowCount():
            self.grid.setRowCount(need_r)
        if need_c > self.grid.columnCount():
            self.grid.setColumnCount(need_c)
        for i, row in enumerate(cells):
            for j, txt in enumerate(row):
                self._set(r0 + i, c0 + j, txt)
        self._labels()

    def eventFilter(self, obj, event):
        if obj is self.grid and event.type() == QtCore.QEvent.KeyPress \
                and self.grid.state() != QtWidgets.QAbstractItemView.EditingState:
            if event.matches(QtGui.QKeySequence.Paste):
                self.paste(QtWidgets.QApplication.clipboard().text())
                return True
            if event.matches(QtGui.QKeySequence.Copy):
                QtWidgets.QApplication.clipboard().setText(self.selection_text())
                return True
            if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
                for it in self.grid.selectedItems():
                    it.setText("")
                return True
        return super().eventFilter(obj, event)

    def selection_text(self):
        idx = self.grid.selectedIndexes()
        if not idx:
            return ""
        rs = sorted({i.row() for i in idx})
        cs = sorted({i.column() for i in idx})
        return "\n".join("\t".join(self._text(r, c) for c in cs) for r in rs)

    def _text(self, r, c):
        it = self.grid.item(r, c)
        return it.text().strip() if it is not None else ""

    def keyPressEvent(self, event):
        # Enter moves on in the grid; it doesn't close the editor
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            return
        super().keyPressEvent(event)

    # -- result ------------------------------------------------------------
    def values(self):
        """(title, headings, rows); rows left wholly empty are dropped."""
        n = self.grid.columnCount()
        table = [[self._text(r, c) for c in range(n)] for r in range(self.grid.rowCount())]
        headers, rows = table[0], [r for r in table[1:] if any(r)]
        return self.title.text().strip(), headers, rows

    def run(self):
        base = getattr(QtWidgets.QDialog, "exec_", None) or QtWidgets.QDialog.exec
        return bool(base(self))


def ask_text(title, label, text="", width=60):
    """One-field pop-up. Returns the text or None."""
    p = Popover(title)
    f = field(text, width)
    p.add_row(label, f, stretch=True)
    return f.text() if p.run() else None


def choose(title, options):
    """Pop-up with one button per option [(value, label)]; returns value or None."""
    p = Popover(title)
    picked = {}
    buttons = []
    for v, lab in options:
        b = QtWidgets.QToolButton()
        b.setText(lab)

        def make(val):
            return lambda: (picked.setdefault("v", val), p.accept())
        b.clicked.connect(make(v))
        buttons.append(b)
    p.add_row(*buttons)
    return picked.get("v") if p.run() else None
