"""Main window: sidebar navigation + pages."""
import os

from PySide6.QtCore import Qt, QSize, QUrl, QSettings
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from .common import APP_NAME, APP_VERSION, resource, open_path
from .page_data import DataPage
from .page_params import ParamsPage
from .page_outputs import OutputsPage
from .page_run import RunPage
from .page_plots import PlotsPage
from .page_models import ModelsPage, read_quality
from .page_planner import PlannerPage
from .page_patterns import PatternsPage
from .page_fibermodel import FiberModelPage
from .widgets import Page, button, hbox
from . import style


class HelpPage(Page):
    def __init__(self, parent=None):
        super().__init__("Help — User manual", "The same manual is in the app folder as User_Manual.pdf.",
                         scroll=False)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setObjectName("manual")
        docs = resource("docs")
        self.view.setSearchPaths([docs])
        path = os.path.join(docs, "manual.html")
        if os.path.exists(path):
            self.view.setSource(QUrl.fromLocalFile(path))
        else:
            self.view.setHtml("<p>The manual was not found next to the app.</p>")
        self.lay.addWidget(self.view, 1)
        pdf = os.path.join(docs, "User_Manual.pdf")
        b = button("Open the PDF manual", slot=lambda: open_path(pdf))
        b.setEnabled(os.path.exists(pdf))
        self.footer.addWidget(b)
        self.footer.addWidget(button("Back to top", "link", slot=lambda: self.view.verticalScrollBar().setValue(0)))
        self.footer.addStretch(1)


# sidebar: sections (None = no heading) with their entries; "setup" is the New-analysis flow (4 steps)
SECTIONS = [("ANALYSE", [("setup", "New analysis", "folder"), ("plots", "Results & plots", "chart"),
                         ("patterns", "Patterns", "grid")]),
            ("PLAN", [("planner", "Experiment planner", "target")]),
            ("TOOLS", [("models", "Quality check", "shield"), ("fibermodel", "Fiber model", "brush")]),
            (None, [("help", "Help & manual", "help")])]
STEPS = [("data", "Files"), ("params", "Settings"), ("outputs", "Outputs"), ("run", "Run")]
# page index (as used by go()): the 4 setup steps first, then the other pages
PAGE_KEYS = [k for k, _ in STEPS] + ["plots", "patterns", "planner", "models", "fibermodel", "help"]


class StepBar(QWidget):
    """Files → Settings → Outputs → Run → Results: where you are in a new analysis (clickable)."""

    def __init__(self, on_step, on_results, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.btns = []
        for i, (_, text) in enumerate(STEPS + [("results", "Results")]):
            if i:
                sep = QLabel("—")
                sep.setObjectName("stepSep")
                lay.addWidget(sep)
            b = QPushButton(f"  {text}")
            b.setObjectName("step")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect((lambda _=False, k=i: on_step(k)) if i < len(STEPS) else (lambda _=False: on_results()))
            lay.addWidget(b)
            self.btns.append(b)
        self.current = 0
        self.set_step(0)

    def set_step(self, i):
        self.current = i
        self.refresh()

    def refresh(self):
        for k, b in enumerate(self.btns):
            st = "active" if k == self.current else "done" if k < self.current else "wait"
            b.setProperty("state", st)
            b.setIcon(_number_icon("✓" if st == "done" else str(k + 1) if k < len(STEPS) else "→", st))
            b.setIconSize(QSize(20, 20))
            b.style().unpolish(b)
            b.style().polish(b)


def _number_icon(text, state):
    """Round badge with a number (or ✓) for the step bar."""
    from PySide6.QtGui import QPainter, QPixmap, QPen
    pm = QPixmap(40, 40)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if state == "active":
        bg, fg = QColor(style.ON_ACCENT), QColor(style.ACCENT)
    elif state == "done":
        bg, fg = QColor(style.OK_BG), QColor(style.OK)
    else:
        bg, fg = QColor(style.SUNKEN), QColor(style.TEXT2)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawEllipse(0, 0, 40, 40)
    f = QFont(QApplication.font())
    f.setPixelSize(20)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QPen(fg))
    p.drawText(pm.rect(), Qt.AlignCenter, text)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


class SetupFlow(QWidget):
    """The New-analysis flow: step bar on top, the four setup pages below."""

    def __init__(self, pages, on_results, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 16, 26, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        k = QLabel("New analysis")
        k.setObjectName("pageKicker")
        top.addWidget(k)
        top.addStretch(1)
        self.bar = StepBar(self.set_step, on_results)
        top.addWidget(self.bar)
        lay.addLayout(top)
        self.stack = QStackedWidget()
        for pg in pages:
            pg.layout().setContentsMargins(0, 6, 0, 16)
            self.stack.addWidget(pg)
        lay.addWidget(self.stack, 1)

    def set_step(self, i):
        self.stack.setCurrentIndex(i)
        self.bar.set_step(i)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon = resource("assets", "icon.png")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.resize(1360, 900)
        self.setMinimumSize(1080, 700)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(228)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 14)
        sl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 0, 12, 16)
        head.setSpacing(10)
        self.logo = QLabel()
        self.logo.setObjectName("logoMark")
        self.logo.setFixedSize(34, 34)
        self.logo.setAlignment(Qt.AlignCenter)
        head.addWidget(self.logo)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        t = QLabel("Collagen Analyzer")
        t.setObjectName("appTitle")
        s = QLabel("Matsui Lab")
        s.setObjectName("appSub")
        tl.addWidget(t)
        tl.addWidget(s)
        head.addLayout(tl, 1)
        sl.addLayout(head)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.row_of_key, self.key_of_row, self.icon_of_row, self.headings = {}, {}, {}, []
        for title, entries in SECTIONS:
            if title:
                h = QListWidgetItem(title)
                h.setFlags(Qt.NoItemFlags)                 # heading: not selectable
                f = QFont(h.font()); f.setPointSizeF(max(f.pointSizeF() * 0.78, 8)); f.setBold(True)
                f.setLetterSpacing(QFont.PercentageSpacing, 110)
                h.setFont(f)
                h.setSizeHint(QSize(200, 34))
                self.nav.addItem(h)
                self.headings.append(h)
            else:
                sp = QListWidgetItem("")
                sp.setFlags(Qt.NoItemFlags)
                sp.setSizeHint(QSize(200, 14))
                self.nav.addItem(sp)
            for key, text, ic in entries:
                it = QListWidgetItem(text)
                it.setSizeHint(QSize(200, 38))
                self.row_of_key[key] = self.nav.count()
                self.key_of_row[self.nav.count()] = key
                self.icon_of_row[self.nav.count()] = ic
                self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("themeToggle")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("Switch between the day and the night look (remembered)")
        self.theme_btn.clicked.connect(self.toggle_theme)
        tb = QHBoxLayout()
        tb.setContentsMargins(14, 8, 14, 6)
        tb.addWidget(self.theme_btn)
        sl.addLayout(tb)
        foot = QLabel(f"Version {APP_VERSION} · Hunter College")
        foot.setObjectName("navFoot")
        foot.setContentsMargins(20, 2, 12, 0)
        sl.addWidget(foot)
        root.addWidget(side)

        # pages
        self.stack = QStackedWidget()
        self.data = DataPage()
        self.params = ParamsPage(self.data)
        self.outputs = OutputsPage(self.data)
        self.run = RunPage(self.data, self.params, self.outputs)
        self.plots = PlotsPage()
        self.patterns = PatternsPage()
        self.planner = PlannerPage()
        self.models = ModelsPage()
        self.fibermodel = FiberModelPage(self.data, self.params)
        self.help = HelpPage()
        self.setup = SetupFlow((self.data, self.params, self.outputs, self.run), lambda: self.go(4))
        self.page_widget = {"setup": self.setup, "plots": self.plots, "patterns": self.patterns,
                            "planner": self.planner, "models": self.models, "fibermodel": self.fibermodel,
                            "help": self.help}
        for w in self.page_widget.values():
            self.stack.addWidget(w)
        self.data.ready.run_now.connect(lambda: self.go(3))
        self.models.model_saved.connect(self.outputs.set_qc_model)
        self.fibermodel.model_saved.connect(self.params.set_fiber_model)
        # back / next buttons on the setup pages
        for i, p in enumerate((self.data, self.params, self.outputs)):
            if i > 0:
                p.footer.addWidget(button("← Back", slot=lambda _=False, k=i - 1: self.go(k)))
            p.footer.addStretch(1)
            p.footer.addWidget(button(f"Continue to {STEPS[i + 1][1].lower()} →", "primary",
                                      slot=lambda _=False, k=i + 1: self.go(k)))
        self.run.footer.addWidget(button("← Back", slot=lambda: self.go(2)))
        self.run.footer.addStretch(1)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.nav.currentRowChanged.connect(self._nav_changed)
        self.run.go_plots = self.show_results
        self.run.run_finished.connect(self._run_done)
        style.on_theme_change(self._theme_changed)
        self._theme_changed()
        self.go(0)
        st = QSettings()
        geo = st.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    # ------------------------------------------------------------------ navigation
    def go(self, i):
        """Show page i of PAGE_KEYS (0-3 = the setup steps)."""
        key = PAGE_KEYS[i]
        if i < len(STEPS):
            self.setup.set_step(i)
            key = "setup"
        self.nav.setCurrentRow(self.row_of_key[key])

    def _nav_changed(self, row):
        key = self.key_of_row.get(row)
        if key:
            self.stack.setCurrentWidget(self.page_widget[key])
        self._recolor_nav()

    # ------------------------------------------------------------------ theme
    def toggle_theme(self):
        style.set_mode("night" if style.MODE == "day" else "day")

    def _theme_changed(self):
        night = style.MODE == "night"
        self.theme_btn.setText("  Day mode" if night else "  Night mode")
        self.theme_btn.setIcon(style.icon("sun" if night else "moon", style.TEXT2, 16))
        self.logo.setPixmap(style.icon("logo", style.ON_ACCENT, 22).pixmap(22, 22))
        for h in self.headings:
            h.setForeground(QColor(style.NAV_HEAD))
        self._recolor_nav()
        self.setup.bar.refresh()
        from .widgets import ThemedCanvas
        for c in self.findChildren(ThemedCanvas):
            c.draw_idle()
        for p in (self.data, self.params, self.outputs, self.run, self.plots, self.patterns, self.planner,
                  self.models, self.fibermodel, self.help):
            fn = getattr(p, "on_theme", None)
            if fn:
                try:
                    fn()
                except Exception:
                    pass

    def _recolor_nav(self):
        cur = self.nav.currentRow()
        for row, ic in self.icon_of_row.items():
            col = style.NAV_SEL_TEXT if row == cur else style.NAV_TEXT
            self.nav.item(row).setIcon(style.icon(ic, col, 18))

    def _run_done(self, outputs):
        tiles = [p for k, p in outputs if k == "tiles"]
        if tiles:
            try:
                self.patterns.load_paths(tiles)
            except Exception:
                pass
        books = [p for k, p in outputs if k == "workbook"]
        if books:
            self.plots.load_paths(books)
            try:
                self.planner.load_paths(books)
            except Exception:
                pass
            qc = {}
            for b in books:
                try:
                    q = read_quality(b)
                except Exception:
                    continue
                for _, r in q.iterrows():
                    qc[str(r["file"])] = (str(r.get("qc_status") or ""), str(r.get("qc_flags") or ""))
            if qc:
                self.data.set_qc(qc)

    def show_results(self, books):
        if books:
            self.plots.load_paths(books)
        self.go(4)

    def closeEvent(self, e):
        if self.run.is_running():
            if QMessageBox.question(self, "Analysis running", "An analysis is still running. Stop it and quit?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.run.proc.stop()
            self.run.proc.wait(5000)
        QSettings().setValue("window/geometry", self.saveGeometry())
        e.accept()
