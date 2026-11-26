"""Main window: sidebar navigation + pages."""
import os

from PySide6.QtCore import Qt, QSize, QUrl, QSettings
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from .common import APP_NAME, APP_VERSION, resource, open_path
from .page_data import DataPage
from .page_params import ParamsPage
from .page_outputs import OutputsPage
from .page_run import RunPage
from .page_plots import PlotsPage
from .page_models import ModelsPage, read_quality
from .page_planner import PlannerPage
from .widgets import Page, button, hbox


class HelpPage(Page):
    def __init__(self, parent=None):
        super().__init__("Help — User manual", "The same manual is in the app folder as User_Manual.pdf.",
                         scroll=False)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet("QTextBrowser { background: white; border: 1px solid #dde3e8; border-radius: 10px;"
                                " padding: 14px 22px; }")
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


# sidebar: sections (None = no heading) with their pages, in page order
SECTIONS = [("ANALYSE", [("data", "1   Data"), ("params", "2   Parameters"), ("outputs", "3   Outputs"),
                         ("run", "4   Run")]),
            ("EXPLORE", [("plots", "◆   Results & Plots")]),
            ("PLAN", [("planner", "◈   Experiment Planner")]),
            ("MODELS", [("models", "◇   Quality check")]),
            (None, [("help", "?   Help")])]
NAV = [p for _, pages in SECTIONS for p in pages]


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
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Collagen\nReflectance Analyzer")
        t.setObjectName("appTitle")
        s = QLabel("Fiber density · pores · alignment")
        s.setObjectName("appSub")
        sl.addWidget(t)
        sl.addWidget(s)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.row_of_page, self.page_of_row = [], {}
        for title, pages in SECTIONS:
            if title:
                h = QListWidgetItem(title)
                h.setFlags(Qt.NoItemFlags)                 # heading: not selectable
                f = QFont(h.font()); f.setPointSizeF(max(f.pointSizeF() * 0.75, 8)); f.setBold(True)
                f.setLetterSpacing(QFont.PercentageSpacing, 112)
                h.setFont(f)
                h.setForeground(QColor("#7fa3ad"))
                h.setSizeHint(QSize(200, 30))
                self.nav.addItem(h)
            elif self.nav.count():
                sp = QListWidgetItem("")
                sp.setFlags(Qt.NoItemFlags)
                sp.setSizeHint(QSize(200, 12))
                self.nav.addItem(sp)
            for key, text in pages:
                it = QListWidgetItem(text)
                it.setSizeHint(QSize(200, 42))
                self.page_of_row[self.nav.count()] = len(self.row_of_page)
                self.row_of_page.append(self.nav.count())
                self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        foot = QLabel(f"Version {APP_VERSION}\nMatsui Lab · Hunter College")
        foot.setObjectName("navFoot")
        sl.addWidget(foot)
        root.addWidget(side)

        # pages
        self.stack = QStackedWidget()
        self.data = DataPage()
        self.params = ParamsPage(self.data)
        self.outputs = OutputsPage(self.data)
        self.run = RunPage(self.data, self.params, self.outputs)
        self.plots = PlotsPage()
        self.planner = PlannerPage()
        self.models = ModelsPage()
        self.help = HelpPage()
        for p in (self.data, self.params, self.outputs, self.run, self.plots, self.planner, self.models, self.help):
            self.stack.addWidget(p)
        self.models.model_saved.connect(self.outputs.set_qc_model)
        # back / next buttons on the setup pages
        for i, p in enumerate((self.data, self.params, self.outputs)):
            if i > 0:
                p.footer.addWidget(button("← Back", slot=lambda _=False, k=i - 1: self.go(k)))
            p.footer.addStretch(1)
            p.footer.addWidget(button(f"Next: {NAV[i + 1][1].split('   ')[1]} →", "primary",
                                      slot=lambda _=False, k=i + 1: self.go(k)))
        self.run.footer.addWidget(button("← Back", slot=lambda: self.go(2)))
        self.run.footer.addStretch(1)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.nav.currentRowChanged.connect(self._nav_changed)
        self.run.go_plots = self.show_results
        self.run.run_finished.connect(self._run_done)
        self.go(0)
        st = QSettings()
        geo = st.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def go(self, i):
        self.nav.setCurrentRow(self.row_of_page[i])

    def _nav_changed(self, row):
        if row in self.page_of_row:
            self.stack.setCurrentIndex(self.page_of_row[row])

    def _run_done(self, outputs):
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
