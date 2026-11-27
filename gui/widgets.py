"""Small reusable widgets."""
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget, QPushButton,
                               QSizePolicy)


class Card(QFrame):
    """White rounded panel with an optional title."""

    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            self.lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("hint")
            s.setWordWrap(True)
            self.lay.addWidget(s)


class Page(QWidget):
    """Page with a title, a subtitle and a scrollable body."""

    def __init__(self, title, subtitle="", scroll=True, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 20, 26, 16)
        outer.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        outer.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("pageSub")
            s.setWordWrap(True)
            outer.addWidget(s)
        outer.addSpacing(8)
        self.body = QWidget()
        self.body.setObjectName("page")
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 6, 0)
        self.lay.setSpacing(12)
        if scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sa.setWidget(self.body)
            outer.addWidget(sa, 1)
        else:
            outer.addWidget(self.body, 1)
        self.footer = QHBoxLayout()
        outer.addLayout(self.footer)


def hint(text):
    l = QLabel(text)
    l.setObjectName("hint")
    l.setWordWrap(True)
    l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def banner(text, ok=False):
    l = QLabel(text)
    l.setObjectName("bannerOk" if ok else "banner")
    l.setWordWrap(True)
    return l


def hbox(*widgets, stretch_end=True, spacing=8):
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(spacing)
    for x in widgets:
        if x == "stretch":
            l.addStretch(1)
        elif isinstance(x, int):
            l.addSpacing(x)
        else:
            l.addWidget(x)
    if stretch_end:
        l.addStretch(1)
    return w


def button(text, kind=None, tip=None, slot=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class ModeCard(QPushButton):
    """Large checkable button with a title line and a description line."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setObjectName("modeCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        a = QLabel(title)
        a.setObjectName("cardTitle")
        a.setStyleSheet("background: transparent;")
        b = QLabel(text)
        b.setObjectName("cardSub")
        b.setWordWrap(True)
        for w in (a, b):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)


class StepChip(QLabel):
    """Phase indicator: waiting / active / done (colours from the theme's style sheet)."""

    def __init__(self, text):
        super().__init__(text)
        self.setObjectName("stepChip")
        self.base = text
        self.set_state("wait")

    def set_state(self, st):
        self.setProperty("state", st)
        self.setText(("✓ " if st == "done" else "") + self.base)
        self.style().unpolish(self)
        self.style().polish(self)


class ThemedCanvas(FigureCanvasQTAgg):
    """Matplotlib canvas whose figure follows the day / night theme on screen."""

    def draw(self):
        from . import style
        try:
            style.theme_figure(self.figure)
        except Exception:
            pass
        super().draw()


class FigurePanel(QWidget):
    """A figure with zoom/pan, and Copy / Save… / Full screen buttons (double-click the figure for full screen).
    controls: widgets shown on the left of the button row (they move into full screen with the figure).
    name: default file name for saving (a string or a function returning one).
    Saved and copied figures use the white paper look whatever the theme, at the size shown on screen."""

    def __init__(self, fig, name="figure", controls=(), parent=None):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

        class NavBar(NavigationToolbar2QT):
            toolitems = [t for t in NavigationToolbar2QT.toolitems if t[0] in ("Home", "Back", "Forward", "Pan",
                                                                              "Zoom")]

        self.fig, self.name, self.full_win = fig, name, None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.inner = QWidget()
        outer.addWidget(self.inner)
        lay = QVBoxLayout(self.inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.canvas = ThemedCanvas(fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav = NavBar(self.canvas, self.inner)
        self.nav.setToolTip("Zoom and pan; Home goes back to the full view")
        self.nav.setMinimumWidth(200)                 # keep the five tool buttons visible
        if controls:                              # the figure's own choices on a row of their own
            crow = QHBoxLayout()
            crow.setContentsMargins(0, 0, 0, 0)
            crow.setSpacing(8)
            for c in controls:
                crow.addWidget(c)
            crow.addStretch(1)
            lay.addLayout(crow)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        row.addWidget(self.msg)
        row.addWidget(self.nav)
        self.copy_btn = button("Copy", tip="Copy the figure to the clipboard (white background, 200 dpi)",
                               slot=self.copy)
        self.save_btn = button("Save…", tip="Save as PNG, PDF, SVG or TIFF (white background, 300 dpi)",
                               slot=self.save)
        self.full_btn = button("Full screen", tip="Show the figure on the whole screen (Esc to go back). "
                                                  "Double-clicking the figure does the same.", slot=self.toggle_full)
        for b in (self.copy_btn, self.save_btn, self.full_btn):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("button_press_event",
                                lambda e: self.toggle_full() if e.dblclick and not self._nav_mode() else None)
        from . import style
        style.on_theme_change(self._icons)
        self._icons()

    def _nav_mode(self):
        return bool(str(getattr(self.nav, "mode", "") or ""))

    def _icons(self):
        from . import style
        col = style.TEXT
        self.copy_btn.setIcon(style.icon("copy", col, 16))
        self.save_btn.setIcon(style.icon("save", col, 16))
        self.full_btn.setIcon(style.icon("shrink" if self.full_win else "expand", col, 16))
        for _, _, image, callback in getattr(self.nav, "toolitems", ()):
            act = getattr(self.nav, "_actions", {}).get(callback)
            if act is not None and image:
                try:
                    act.setIcon(self.nav._icon(image + ".png"))
                except Exception:
                    pass

    def _name(self):
        n = self.name() if callable(self.name) else self.name
        return "".join(c if c.isalnum() or c in "-_. " else "_" for c in str(n or "figure")).strip() or "figure"

    def _say(self, text):
        from PySide6.QtCore import QTimer
        self.msg.setText(text)
        QTimer.singleShot(6000, lambda: self.msg.setText("") if self.msg.text() == text else None)

    def _empty(self):
        if self.fig.axes:
            return False
        self._say("Nothing drawn yet")
        return True

    def _paper(self, fn):
        """Run fn() with the figure in the white paper look, then redraw it for the screen."""
        from . import style
        try:
            style.theme_figure(self.fig, night=False)
            return fn()
        finally:
            self.canvas.draw_idle()

    def save(self):
        if self._empty():
            return
        import os
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        start = QSettings().value("plots/save_dir", QSettings().value("plots/last_dir", os.path.expanduser("~")))
        p, flt = QFileDialog.getSaveFileName(self, "Save figure", os.path.join(start, self._name() + ".png"),
                                             "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif)")
        if not p:
            return
        if not os.path.splitext(p)[1]:
            p += "." + flt.split("*.")[-1].rstrip(")")
        QSettings().setValue("plots/save_dir", os.path.dirname(p))
        try:
            self._paper(lambda: self.fig.savefig(p, dpi=300, bbox_inches="tight", facecolor="white"))
        except Exception as e:
            QMessageBox.warning(self, "Save figure", f"Could not save:\n{e}")
            return
        self._say(f"Saved {os.path.basename(p)}")

    def copy(self):
        if self._empty():
            return
        import io
        from PySide6.QtGui import QGuiApplication, QImage
        buf = io.BytesIO()
        self._paper(lambda: self.fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white"))
        QGuiApplication.clipboard().setImage(QImage.fromData(buf.getvalue(), "PNG"))
        self._say("Copied to the clipboard")

    def toggle_full(self):
        if self.full_win is not None:
            self.full_win.close()
            return
        if self._empty():
            return
        self.full_win = full_screen(self.inner, self._name(), on_exit=self._restored,
                                    keys=(("Ctrl+S", self.save), ("Ctrl+Shift+C", self.copy),
                                          ("Ctrl+F", self.toggle_full)))
        self.full_btn.setText("Exit full screen")
        self._icons()

    def _restored(self):
        self.full_win = None
        self.full_btn.setText("Full screen")
        self._icons()
        self.canvas.draw_idle()


def full_screen(widget, title, on_exit=None, keys=()):
    """Show widget alone on the whole screen; Esc (or closing) puts it back where it was. keys: (shortcut, slot)
    pairs that work in the full-screen window. Returns the window."""
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QDialog, QSplitter
    parent = widget.parentWidget()
    if isinstance(parent, QSplitter):
        idx, sizes = parent.indexOf(widget), parent.sizes()

        def put_back():
            parent.insertWidget(idx, widget)
            parent.setSizes(sizes)
    else:
        lay = parent.layout()
        idx = lay.indexOf(widget)
        stretch = lay.stretch(idx) if hasattr(lay, "stretch") else 0

        def put_back():
            lay.insertWidget(idx, widget, stretch)
    w = QDialog(widget.window())
    w.setObjectName("page")
    w.setWindowTitle(title)
    l = QVBoxLayout(w)
    l.setContentsMargins(18, 14, 18, 14)
    l.addWidget(widget)
    widget.show()
    for key, slot in keys:
        QShortcut(QKeySequence(key), w, slot)

    def done(*_):
        put_back()
        widget.show()
        if on_exit:
            on_exit()
        w.deleteLater()
    w.finished.connect(done)
    w.showFullScreen()
    return w
