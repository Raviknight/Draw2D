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
