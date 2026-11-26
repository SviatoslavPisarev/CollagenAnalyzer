"""Page 3: where to save, how detailed, which figures, figure format."""
import json
import os

from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from .common import FIGURES
from .widgets import Page, button, hint, hbox


class OutputsPage(Page):
    changed = Signal()

    def __init__(self, data_page, parent=None):
        super().__init__("3 · Outputs", "Choose where the results go and which figures to make. The spreadsheets "
                         "(all numbers, settings and metadata) are always written; the Results & Plots page "
                         "can draw any other graph from them later.")
        self.data_page = data_page

        # --- location
        g = QGroupBox("Where to save")
        gl = QVBoxLayout(g)
        self.loc_next = QRadioButton("Next to the data: a folder mmddyy_results inside each data folder")
        self.loc_custom = QRadioButton("In a folder I choose:")
        grp = QButtonGroup(self)
        grp.addButton(self.loc_next)
        grp.addButton(self.loc_custom)
        self.loc_path = QLineEdit()
        self.loc_path.setPlaceholderText("Choose a folder…")
        self.loc_browse = button("Browse…", slot=self.browse)
        gl.addWidget(self.loc_next)
        row = hbox(self.loc_custom, self.loc_path, self.loc_browse, stretch_end=False)
        row.layout().setStretch(1, 1)
        gl.addWidget(row)
        self.loc_note = hint("")
        gl.addWidget(self.loc_note)
        self.lay.addWidget(g)

        # --- detail
        g = QGroupBox("Level of detail")
        gl = QVBoxLayout(g)
        self.det_full = QRadioButton("Detailed (recommended): a folder per file with its own figures and spreadsheet")
        self.det_compact = QRadioButton("Compact: comparisons and one figure per file only (faster, fewer files)")
        grp2 = QButtonGroup(self)
        grp2.addButton(self.det_full)
        grp2.addButton(self.det_compact)
        gl.addWidget(self.det_full)
        gl.addWidget(self.det_compact)
        gl.addWidget(hint("‘One file’ mode on the Data page always gives the detailed single-file report."))
        self.lay.addWidget(g)

        # --- figures
        g = QGroupBox("Figures")
        gl = QVBoxLayout(g)
        gl.addWidget(hbox(button("All", slot=lambda: self.set_all(True)),
                          button("None", slot=lambda: self.set_all(False)),
                          button("Only comparisons", slot=self.only_comparisons)))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        self.fig_cb = {}
        groups = []
        for key, label, group in FIGURES:
            if group not in groups:
                groups.append(group)
        col_of = {grp_: i for i, grp_ in enumerate(groups)}
        rows = {grp_: 1 for grp_ in groups}
        for grp_ in groups:
            h = QLabel(grp_)
            h.setStyleSheet("font-weight: 700; color: #155e6c;")
            grid.addWidget(h, 0, col_of[grp_])
        for key, label, group in FIGURES:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.toggled.connect(self._sync)
            self.fig_cb[key] = cb
            grid.addWidget(cb, rows[group], col_of[group], Qt.AlignTop)
            rows[group] += 1
        grid.setRowStretch(max(rows.values()) + 1, 1)
        gl.addLayout(grid)
        self.share_y = QCheckBox("Per-date graphs: same y-axis in every panel (easier to compare samples)")
        self.share_y.toggled.connect(self._sync)
        gl.addWidget(self.share_y)
        gl.addWidget(hint("Per sample = the before-AMF and after-AMF files of one sample (same mmddyy_##_) on the same "
                          "graphs. Per date = one panel per sample, side by side. For other combinations (any files "
                          "on one graph, against NC concentration, etc.) use Results & Plots after the run."))
        self.lay.addWidget(g)

        # --- format
        g = QGroupBox("Figure format")
        gl = QHBoxLayout(g)
        self.fmt = QComboBox()
        self.fmt.addItems(["PNG (pictures, default)", "PDF (vector, for papers)", "SVG (vector, editable)",
                           "TIFF (for journals)"])
        self.dpi_default = QCheckBox("Default resolution")
        self.dpi_default.setChecked(True)
        self.dpi = QSpinBox()
        self.dpi.setRange(50, 1200)
        self.dpi.setValue(300)
        self.dpi.setSuffix(" dpi")
        for w in (self.fmt, self.dpi_default, self.dpi):
            (w.currentIndexChanged if isinstance(w, QComboBox) else
             w.toggled if isinstance(w, QCheckBox) else w.valueChanged).connect(self._sync)
        gl.addWidget(QLabel("Format"))
        gl.addWidget(self.fmt)
        gl.addSpacing(20)
        gl.addWidget(self.dpi_default)
        gl.addWidget(self.dpi)
        gl.addStretch(1)
        self.lay.addWidget(g)
        self.lay.addWidget(hint("Default resolution is 90–120 dpi (fast, small files). 300 dpi makes print-quality "
                                "PNG/TIFF; large runs with ‘every analysed image’ figures then take longer."))

        # --- quality check and per-tile table
        g = QGroupBox("Quality check and per-tile table")
        gl = QVBoxLayout(g)
        gl.addWidget(hint("Every run checks each file for blur, uneven (dried) edges, saturation, dark tiles and "
                          "threshold sensitivity. Results: the ‘quality’ sheet of the summary spreadsheet, the QC "
                          "column on the Data page and warnings in the log."))
        self.tiles = QCheckBox("Per-tile table (mmddyy_tiles.csv): the metrics of every tile of every analysed "
                               "image, for finding local patterns")
        self.tiles.setChecked(True)
        self.tiles.setToolTip("Tile size = ‘Tile size’ on the Parameters page (default 13 µm).")
        self.qc_on = QCheckBox("Also apply my trained quality-check model:")
        self.qc_path = QLineEdit()
        self.qc_path.setReadOnly(True)
        self.qc_path.setPlaceholderText("Train and save one on the Quality check page…")
        self.qc_browse = button("Choose…", slot=self.browse_model)
        for w in (self.tiles, self.qc_on):
            w.toggled.connect(self._sync)
        gl.addWidget(self.tiles)
        row = hbox(self.qc_on, self.qc_path, self.qc_browse, stretch_end=False)
        row.layout().setStretch(1, 1)
        gl.addWidget(row)
        self.lay.addWidget(g)

        # --- iCloud Drive
        g = QGroupBox("Files in iCloud Drive")
        gl = QVBoxLayout(g)
        gl.addWidget(hint("Files that are only in iCloud (not downloaded to this computer) are downloaded when "
                          "their folder is analysed. A folder whose files do not arrive in time is tried again "
                          "after the other folders."))
        self.free_space = QCheckBox("Free disk space: after each folder, remove the local copies of the files the "
                                    "run had to download (they stay in iCloud)")
        self.free_space.setChecked(True)
        self.free_space.toggled.connect(self._sync)
        gl.addWidget(self.free_space)
        self.lay.addWidget(g)
        self.lay.addStretch(1)

        self.loc_next.toggled.connect(self._sync)
        self.loc_path.textChanged.connect(self._sync)
        self.det_full.toggled.connect(self._sync)
        self.load_settings()
        self.data_page.changed.connect(self._sync)
        self._sync()

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "Folder for the results", self.loc_path.text() or os.path.expanduser("~"))
        if p:
            self.loc_path.setText(p)
            self.loc_custom.setChecked(True)

    def browse_model(self):
        p, _ = QFileDialog.getOpenFileName(self, "Quality-check model", os.path.dirname(self.qc_path.text()),
                                           "QC model (*.joblib)")
        if p:
            self.set_qc_model(p)

    def set_qc_model(self, path):
        """Use this model for new runs (called by the Quality check page after saving a model)."""
        self.qc_path.setText(path)
        self.qc_on.setChecked(True)
        self._sync()

    def set_all(self, on):
        for cb in self.fig_cb.values():
            cb.setChecked(on)

    def only_comparisons(self):
        for k, cb in self.fig_cb.items():
            cb.setChecked(k not in ("slices", "slice_comparison", "file"))

    def _sync(self, *_):
        custom = self.loc_custom.isChecked()
        self.loc_path.setEnabled(custom)
        self.loc_browse.setEnabled(custom)
        self.dpi.setEnabled(not self.dpi_default.isChecked())
        self.qc_path.setEnabled(self.qc_on.isChecked())
        detail = self.det_full.isChecked() or self.data_page.mode == "file"
        for k in ("slices", "slice_comparison"):
            self.fig_cb[k].setEnabled(detail)
        multi = self.data_page.is_multi()
        if custom:
            root = self.loc_path.text() or "<chosen folder>"
            note = (f"Results: {root}/<data folder name>/mmddyy_results; combined summary: {root}/summary.xlsx"
                    if multi else f"Results: {root}/mmddyy_results")
        else:
            note = ("Results: <each data folder>/mmddyy_results; combined summary.xlsx in the folder that contains "
                    "all data folders" if multi else "Results: <data folder>/mmddyy_results")
        if self.data_page.mode == "file":
            note += "/<file name>/"
        self.loc_note.setText(note + "   (mmddyy = date in the file names). Existing files with the same names "
                              "are overwritten.")
        self.save_settings()
        self.changed.emit()

    def validate(self):
        if self.loc_custom.isChecked():
            p = self.loc_path.text().strip()
            if not p:
                return "Choose the folder for the results (Outputs page), or save next to the data."
            try:
                os.makedirs(p, exist_ok=True)
            except OSError as e:
                return f"Cannot create the results folder: {e}"
        if self.qc_on.isChecked() and not os.path.isfile(self.qc_path.text()):
            return ("The quality-check model file was not found (Outputs page): choose it again, or untick "
                    "‘apply my trained quality-check model’.")
        return None

    def engine_args(self):
        a = []
        if self.loc_custom.isChecked() and self.loc_path.text().strip():
            a += ["--results-root", self.loc_path.text().strip()]
        if self.det_compact.isChecked() and self.data_page.mode != "file":
            a.append("--averages-only")
        keys = [k for k, cb in self.fig_cb.items() if cb.isChecked() and cb.isEnabled()]
        a += ["--figures", ",".join(keys) if keys else "none"]
        a += ["--fig-format", ["png", "pdf", "svg", "tif"][self.fmt.currentIndex()]]
        if not self.dpi_default.isChecked():
            a += ["--dpi", str(self.dpi.value())]
        if self.share_y.isChecked():
            a.append("--share-y")
        if not self.tiles.isChecked():
            a.append("--no-tiles")
        if self.qc_on.isChecked() and self.qc_path.text():
            a += ["--qc-model", self.qc_path.text()]
        if not self.free_space.isChecked():
            a.append("--keep-downloaded")
        return a

    def results_root(self):
        return self.loc_path.text().strip() if self.loc_custom.isChecked() else None

    # ------------------------------------------------------------------ persistence
    def state(self):
        return dict(custom=self.loc_custom.isChecked(), path=self.loc_path.text(), detail=self.det_full.isChecked(),
                    figs={k: cb.isChecked() for k, cb in self.fig_cb.items()}, share_y=self.share_y.isChecked(),
                    fmt=self.fmt.currentIndex(), dpi_default=self.dpi_default.isChecked(), dpi=self.dpi.value(),
                    tiles=self.tiles.isChecked(), qc_on=self.qc_on.isChecked(), qc_path=self.qc_path.text(),
                    free_space=self.free_space.isChecked())

    def save_settings(self):
        if getattr(self, "_loading", False):
            return
        QSettings().setValue("outputs/state", json.dumps(self.state()))

    def load_settings(self):
        self._loading = True
        try:
            st = json.loads(QSettings().value("outputs/state", "") or "{}")
        except Exception:
            st = {}
        (self.loc_custom if st.get("custom") else self.loc_next).setChecked(True)
        self.loc_path.setText(st.get("path", ""))
        (self.det_full if st.get("detail", True) else self.det_compact).setChecked(True)
        for k, v in st.get("figs", {}).items():
            if k in self.fig_cb:
                self.fig_cb[k].setChecked(v)
        self.share_y.setChecked(st.get("share_y", False))
        self.fmt.setCurrentIndex(st.get("fmt", 0))
        self.dpi_default.setChecked(st.get("dpi_default", True))
        self.dpi.setValue(st.get("dpi", 300))
        self.tiles.setChecked(st.get("tiles", True))
        self.free_space.setChecked(st.get("free_space", True))
        self.qc_path.setText(st.get("qc_path", ""))
        self.qc_on.setChecked(st.get("qc_on", False) and bool(st.get("qc_path")))
        self._loading = False
