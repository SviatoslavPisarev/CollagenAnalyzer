"""Results & Plots: load result spreadsheets and build custom graphs from any files."""
import glob
import os
import re

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QSpinBox, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QSizePolicy)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.colors as mcolors

import collagen_core as core
import gelstats as GS
from .widgets import Page, button, hint, hbox
from . import style

METRICS = [
    ("area_frac_pct", "Fiber area fraction (%)"),
    ("length_density_um_per_um2", "Fiber length density (µm/µm²)"),
    ("pore_lt_mean_um", "Pore size — mean local thickness (µm)"),
    ("pore_lt_median_um", "Pore size — median local thickness (µm)"),
    ("pore_localmax_mean_um", "Local pore diameter at maxima (µm)"),
    ("fiber_lt_mean_um", "Fiber size — mean local thickness (µm)"),
    ("fiber_lt_median_um", "Fiber size — median local thickness (µm)"),
    ("fiber_len_mean_um", "Fiber segment length — mean (µm)"),
    ("fiber_len_median_um", "Fiber segment length — median (µm)"),
    ("fiber_segments_per_100um2", "Fiber segments per 100 µm²"),
    ("order_S", "Alignment — order parameter S"),
    ("coherency", "Coherency"),
    ("mean_angle_deg", "Mean fiber angle (°)"),
    ("tile_cv", "Patchiness — tile CV"),
    ("mean_reflectance", "Mean reflectance (a.u., QC)"),
    ("threshold", "Fiber threshold"),
    ("area_frac_pct_thr_minus10pct", "Fiber area fraction, threshold −10 % (QC)"),
    ("area_frac_pct_thr_plus10pct", "Fiber area fraction, threshold +10 % (QC)"),
    ("pore_lt_edge_excluded_pct", "Edge-excluded pore area (%)"),
    ("fiber_len_edge_excluded_pct", "Fiber segments touching the edge (%)"),
]
PARAM_X = [  # column, label, scale
    ("nc_conc_nM", "NC concentration (nM)", 1.0),
    ("collagen_mg_ml", "Collagen (mg/mL)", 1.0),
    ("amf_freq_hz", "AMF frequency (Hz)", 1.0),
    ("amf_time_s", "AMF exposure (min)", 1 / 60),
    ("amf_vpp_set", "AMF Vp-p set (V)", 1.0),
    ("amf_v_measured", "AMF measured voltage (V)", 1.0),
    ("amf_app_set", "AMF Ap-p set (A)", 1.0),
    ("amf_coil_turns", "Coil turns", 1.0),
    ("amf_b_peak_mT", "Magnetic field, peak (mT)", 1.0),
    ("amf_ampere_turns_pp", "Ap-p × turns", 1.0),
    ("nc_incubation_time_s", "NC incubation time (min)", 1 / 60),
]
COLOR_BY = [("condition", "Before / after AMF"), ("arm", "Group (NC + AMF / AMF only / sham)"),
            ("file", "Each file its own color"), ("sample", "Sample"),
            ("nc_conc_nM", "NC concentration"), ("nc_mixing", "NC mixing"), ("collagen_mg_ml", "Collagen"),
            ("amf_freq_hz", "AMF frequency"), ("amf_time_s", "AMF time"), ("amf_coil_turns", "Coil"),
            ("amf_b_peak_mT", "Magnetic field"), ("date", "Date")]
GROUP_BY = [("sample", "Sample"), ("date", "Date"), ("condition", "Condition"), ("arm", "Group (NC + AMF, sham, …)"),
            ("nc_conc_nM", "NC concentration"),
            ("nc_mixing", "NC mixing"), ("collagen_mg_ml", "Collagen"), ("amf_freq_hz", "AMF frequency")]
LABELS = [("cond", "Condition + AMF + file no."), ("sample_cond", "Sample + condition"), ("file", "Full file name"),
          ("no", "File number only"), ("nc", "Condition + NC conditions")]
PLOT_TYPES = [
    ("series", "Metric vs depth / time (one line per file)"),
    ("perfile", "Metric per file (bars, box plots or points)"),
    ("change", "Change after AMF / sham, per sample (%)"),
    ("stats", "Change by group with statistics (one dot per gel)"),
    ("param", "Metric vs experimental parameter (NC conc., AMF …)"),
    ("dist", "Distribution (pore size, fiber size, length, angle)"),
]
DISTS = [("lt", "Pore size (local thickness, area-weighted)"), ("fib", "Fiber size (local thickness)"),
         ("seg", "Fiber segment length"), ("ang", "Fiber angle (rose plot)")]
TAB = ["#3b6fb6", "#d9622b", "#2a9d5c", "#8e44ad", "#c0392b", "#7f8c8d", "#e0a800", "#17a2b8", "#6d4c41", "#ad1457"]


def _fmt(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:g}" if isinstance(v, (int, float, np.floating, np.integer)) else str(v)


class ResultsStore:
    """Rows of loaded result spreadsheets + histograms from the *_distributions.npz files."""

    def __init__(self):
        self.per = pd.DataFrame()
        self.sources = []          # loaded workbook paths
        self.hists = {}            # uid -> {lt, fib, seg, ang, S, frames: {(t,z): {...}}}
        self.info = {}             # uid -> first row (labels)
        self.user_fields = []      # parameters added in the app (column names)

    def load(self, path):
        path = os.path.abspath(path)
        if path in self.sources:
            return 0
        try:
            df = pd.read_excel(path, sheet_name="per slice")
        except Exception:
            base = os.path.splitext(path)[0]
            if os.path.exists(base + "_per_slice.csv"):
                df = pd.read_csv(base + "_per_slice.csv")
            else:
                raise
        root = os.path.dirname(path)
        df = df.copy()
        if "results_folder" in df.columns:          # combined summary of several folders
            df["_src"] = [os.path.normpath(os.path.join(root, str(r))) for r in df["results_folder"]]
        else:
            df["_src"] = root
        df["file"] = df["file"].astype(str)
        df["date"] = df["date"].astype(str).str.zfill(6).where(df["date"].astype(str) != "other", "other")
        df["sample"] = df["sample"].astype(str)
        if not self.per.empty:
            df = pd.concat([self.per, df], ignore_index=True, sort=False)
        df = df.drop_duplicates(subset=["file", "_src", "t_index", "z_index"], keep="last")
        # unique id per file: the name, plus the results folder if the same name comes from two places
        srcs = df.groupby("file")["_src"].nunique()
        df = df.copy()
        df["uid"] = [f if srcs[f] == 1 else f"{f}  [{os.path.basename(s)}]" for f, s in zip(df["file"], df["_src"])]
        df = GS.upgrade_labels(df)              # older results: _sham_ / _noNC_ files
        first = GS.assign_arms(df.groupby("uid", sort=False).first().reset_index())
        df["arm"] = df["uid"].map(dict(zip(first["uid"], first["arm"])))
        self.per = df.reset_index(drop=True)
        self.sources.append(path)
        for src in self.per["_src"].unique():
            self._load_hists(src)
        self.info = {u: g.iloc[0].to_dict() for u, g in self.per.groupby("uid", sort=False)}
        if "set_user_fields" in self.per.columns:
            for v in self.per["set_user_fields"].dropna().astype(str):
                for k in v.split(","):
                    k = k.strip()
                    if k and k in self.per.columns and k not in self.user_fields:
                        self.user_fields.append(k)
        return df["uid"].nunique()

    def _load_hists(self, src):
        for npz in glob.glob(os.path.join(src, "*_distributions.npz")):
            try:
                z = np.load(npz)
            except Exception:
                continue
            for key in z.files:
                parts = key.split("|")
                if len(parts) < 2:
                    continue
                name = parts[0]
                uids = self.per.loc[(self.per["file"] == name) & (self.per["_src"] == src), "uid"].unique()
                for u in uids:
                    h = self.hists.setdefault(u, {"frames": {}})
                    if len(parts) == 2:
                        h[parts[1]] = z[key]
                    elif len(parts) == 4:
                        h["frames"].setdefault((int(parts[1]), int(parts[2])), {})[parts[3]] = z[key]

    def clear(self):
        self.__init__()

    def files(self):
        return list(self.info)


class PlotsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Results & Plots", "Load result spreadsheets (mmddyy_summary.xlsx or summary.xlsx) and "
                         "combine any files in your own graphs. Results of a run are loaded automatically.",
                         scroll=False)
        self.store = ResultsStore()
        self.plotted = []                       # rows of the plotted data (for CSV export)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.lay.addWidget(split, 1)

        # ================================================================ controls
        ctrl = QWidget()
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(0, 0, 8, 0)
        cl.setSpacing(10)

        g = QGroupBox("1. Data")
        gl = QVBoxLayout(g)
        gl.addWidget(hbox(button("Load spreadsheet(s)…", "primary", slot=self.load_dialog),
                          button("Load results folder…", slot=self.load_folder_dialog),
                          button("Clear", "link", slot=self.clear)))
        self.src_label = hint("Nothing loaded.")
        gl.addWidget(self.src_label)
        cl.addWidget(g)

        g = QGroupBox("2. Files to plot")
        gl = QVBoxLayout(g)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Sample / file", "Condition", "NC", "Type"])
        self.tree.setMinimumHeight(220)
        hh = self.tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 58)
        self.tree.setColumnWidth(3, 70)
        self.tree.setObjectName("plain")
        self.tree.itemChanged.connect(self._tree_changed)
        gl.addWidget(self.tree)
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("Filter (e.g. AMF, 500Hz, 0.5nM, premixed)…")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self._filter_tree)
        gl.addWidget(hbox(self.tree_filter, button("All", slot=lambda: self._tick_all(True)),
                          button("None", slot=lambda: self._tick_all(False)), stretch_end=False))
        cl.addWidget(g)

        g = QGroupBox("3. What to plot")
        f = QFormLayout(g)
        self.ptype = QComboBox()
        for k, t in PLOT_TYPES:
            self.ptype.addItem(t, k)
        f.addRow("Graph", self.ptype)
        self.metric = QComboBox()
        f.addRow("Metric", self.metric)
        self.metric_row = f.rowCount() - 1
        self.dist = QComboBox()
        for k, t in DISTS:
            self.dist.addItem(t, k)
        f.addRow("Distribution", self.dist)
        self.xaxis = QComboBox()
        f.addRow("x-axis", self.xaxis)
        self.style_ = QComboBox()
        f.addRow("Style", self.style_)
        self.err = QComboBox()
        self.err.addItems(["SD over images", "SEM over images", "No error bars"])
        f.addRow("Error bars", self.err)
        self.smooth = QSpinBox()
        self.smooth.setRange(1, 51)
        self.smooth.setSingleStep(2)
        self.smooth.setToolTip("Moving average over this many neighbouring images (1 = none). Only for lines vs "
                               "depth / time.")
        f.addRow("Smoothing (images)", self.smooth)
        self.points = QCheckBox("Show every analysed image as a dot")
        f.addRow("", self.points)
        self.logx = QCheckBox("Logarithmic x-axis")
        f.addRow("", self.logx)
        self.sub_sham = QCheckBox("Subtract the change of the sham gels (same date)")
        self.sub_sham.setToolTip("Sham gels (‘sham’ in the file name) show how much a gel changes without the field "
                                 "(drying, handling, re-imaging). With this on, each bar is the AMF change minus the "
                                 "mean sham change of the same date (all dates if that day has no sham).")
        f.addRow("", self.sub_sham)
        self.form3 = f
        cl.addWidget(g)

        g = QGroupBox("4. Layout and labels")
        f = QFormLayout(g)
        self.layout_ = QComboBox()
        for k, t in [("overlay", "All files on one graph"), ("file", "Side by side: one panel per file"),
                     ("group", "One panel per group (below)")]:
            self.layout_.addItem(t, k)
        f.addRow("Panels", self.layout_)
        self.group_by = QComboBox()
        for k, t in GROUP_BY:
            self.group_by.addItem(t, k)
        f.addRow("Group by", self.group_by)
        self.ncols = QSpinBox()
        self.ncols.setRange(1, 8)
        self.ncols.setValue(3)
        f.addRow("Panels per row", self.ncols)
        self.share_y = QCheckBox("Same y-axis in all panels")
        self.share_y.setChecked(True)
        f.addRow("", self.share_y)
        self.color_by = QComboBox()
        for k, t in COLOR_BY:
            self.color_by.addItem(t, k)
        f.addRow("Colors", self.color_by)
        self.label_by = QComboBox()
        for k, t in LABELS:
            self.label_by.addItem(t, k)
        f.addRow("Legend labels", self.label_by)
        self.legend_pos = QComboBox()
        self.legend_pos.addItems(["Automatic", "Inside each graph", "Right of the graphs", "Below the graphs",
                                  "No legend"])
        f.addRow("Legend", self.legend_pos)
        self.title = QLineEdit()
        self.title.setPlaceholderText("automatic")
        f.addRow("Title", self.title)
        self.xlabel = QLineEdit()
        self.xlabel.setPlaceholderText("automatic")
        f.addRow("x-axis label", self.xlabel)
        self.ylabel = QLineEdit()
        self.ylabel.setPlaceholderText("automatic")
        f.addRow("y-axis label", self.ylabel)
        self.ymin = QLineEdit(); self.ymin.setPlaceholderText("auto")
        self.ymax = QLineEdit(); self.ymax.setPlaceholderText("auto")
        f.addRow("y range", hbox(self.ymin, QLabel("to"), self.ymax, stretch_end=False))
        self.font = QSpinBox()
        self.font.setRange(6, 24)
        self.font.setValue(10)
        f.addRow("Font size", self.font)
        self.grid = QCheckBox("Grid lines")
        self.grid.setChecked(True)
        f.addRow("", self.grid)
        cl.addWidget(g)

        g = QGroupBox("5. Save")
        f = QFormLayout(g)
        self.fig_w = QDoubleSpinBox(); self.fig_w.setRange(2, 40); self.fig_w.setValue(8); self.fig_w.setSuffix(" in")
        self.fig_h = QDoubleSpinBox(); self.fig_h.setRange(2, 40); self.fig_h.setValue(5); self.fig_h.setSuffix(" in")
        self.auto_size = QCheckBox("automatic")
        self.auto_size.setChecked(True)
        f.addRow("Size (w × h)", hbox(self.fig_w, QLabel("×"), self.fig_h, self.auto_size, stretch_end=False))
        self.dpi = QSpinBox(); self.dpi.setRange(50, 1200); self.dpi.setValue(300); self.dpi.setSuffix(" dpi")
        f.addRow("Resolution", self.dpi)
        f.addRow(hbox(button("Save figure…", "primary", slot=self.save_figure),
                      button("Export plotted data (CSV)…", slot=self.export_csv)))
        cl.addWidget(g)
        cl.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(ctrl)
        sa.setMinimumWidth(470)
        split.addWidget(sa)

        # ================================================================ canvas
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.fig = Figure(figsize=(8, 5), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, right)
        rl.addWidget(self.toolbar)
        rl.addWidget(self.canvas, 1)
        self.note = hint("")
        rl.addWidget(self.note)
        split.addWidget(right)
        split.setSizes([480, 880])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.redraw)
        for w in (self.ptype, self.metric, self.dist, self.xaxis, self.style_, self.err, self.layout_, self.group_by,
                  self.color_by, self.label_by, self.legend_pos):
            w.currentIndexChanged.connect(self._controls_changed)
        for w in (self.smooth, self.ncols, self.font, self.fig_w, self.fig_h):
            w.valueChanged.connect(self._schedule)
        for w in (self.points, self.logx, self.share_y, self.grid, self.auto_size, self.sub_sham):
            w.toggled.connect(self._schedule)
        for w in (self.title, self.xlabel, self.ylabel, self.ymin, self.ymax):
            w.textChanged.connect(self._schedule)
        self._fill_metric()
        self._controls_changed()

    # ------------------------------------------------------------------ loading
    def load_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Load result spreadsheets", start,
                                                "Result spreadsheets (*_summary.xlsx summary.xlsx *.xlsx)")
        if paths:
            QSettings().setValue("plots/last_dir", os.path.dirname(paths[0]))
            self.load_paths(paths)

    def load_folder_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "Folder with results (searched including subfolders)", start)
        if not d:
            return
        QSettings().setValue("plots/last_dir", d)
        found = [p for p in glob.glob(os.path.join(d, "**", "*.xlsx"), recursive=True)
                 if re.match(r"^((\d{6}|other)(-\d{6})?_summary|summary)\.xlsx$", os.path.basename(p))]
        combined = [p for p in found if os.path.basename(p) == "summary.xlsx"]
        if combined:            # a combined summary already holds its folders' data
            found = combined + [p for p in found if os.path.basename(p) != "summary.xlsx"
                                and not any(os.path.dirname(p).startswith(os.path.dirname(c)) for c in combined)]
        if not found:
            QMessageBox.information(self, "Load results", "No mmddyy_summary.xlsx or summary.xlsx found in this folder.")
            return
        self.load_paths(found)

    def load_paths(self, paths):
        errs = []
        for p in paths:
            try:
                self.store.load(p)
            except Exception as e:
                errs.append(f"{os.path.basename(p)}: {e}")
        if errs:
            QMessageBox.warning(self, "Load results", "Could not read:\n" + "\n".join(errs))
        self._after_load()

    def clear(self):
        self.store.clear()
        self._after_load()

    def _after_load(self):
        n = len(self.store.files())
        if self.store.sources:
            names = ", ".join(os.path.basename(p) for p in self.store.sources[-4:])
            more = "" if len(self.store.sources) <= 4 else f" (+{len(self.store.sources) - 4} more)"
            self.src_label.setText(f"{n} file(s) from {len(self.store.sources)} spreadsheet(s): {names}{more}")
        else:
            self.src_label.setText("Nothing loaded.")
        self._fill_tree()
        self._fill_metric()
        self._fill_choice_lists()
        self._schedule()

    # ------------------------------------------------------------------ file tree
    def _fill_tree(self):
        self.tree.blockSignals(True)
        old = self._ticked_set() if self.tree.topLevelItemCount() else None
        self.tree.clear()
        groups = {}
        for u, r in self.store.info.items():
            groups.setdefault(r["sample"], []).append(u)
        for smp in sorted(groups):
            top = QTreeWidgetItem([f"Sample {smp}"])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setCheckState(0, Qt.Checked)
            f = top.font(0); f.setBold(True); top.setFont(0, f)
            for u in sorted(groups[smp], key=lambda x: (self.store.info[x]["condition"] != "Before AMF", x)):
                r = self.store.info[u]
                it = QTreeWidgetItem([u, self._cond(r), _fmt(r.get("nc_conc_nM")) + (" nM" if pd.notna(r.get("nc_conc_nM")) else ""),
                                      str(r.get("series", ""))])
                it.setData(0, Qt.UserRole, u)
                it.setToolTip(0, u)
                if str(r.get("qc_status")) == "check":           # flagged by the quality check
                    it.setText(0, "⚠ " + u)
                    it.setForeground(0, QColor(style.WARN))
                    it.setToolTip(0, f"{u}\nQuality check: {r.get('qc_flags')}")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if (old is None or u in old) else Qt.Unchecked)
                it.setForeground(1, QColor(style.AFTER if r["condition"] == "After AMF" else
                                           style.SHAM if GS.is_sham(r["condition"]) else style.BEFORE))
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        self._filter_tree(self.tree_filter.text())

    def _tree_changed(self, *_):
        self._schedule()

    def _filter_tree(self, text):
        t = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            vis = 0
            for j in range(top.childCount()):
                c = top.child(j)
                hide = bool(t) and t not in " ".join(c.text(k).lower() for k in range(4))
                c.setHidden(hide)
                vis += not hide
            top.setHidden(vis == 0)

    def _tick_all(self, on):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                if not top.child(j).isHidden():
                    top.child(j).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._schedule()

    def _ticked_set(self):
        out = set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                if c.checkState(0) == Qt.Checked:
                    out.add(c.data(0, Qt.UserRole))
        return out

    def selected(self):
        """Ticked files in tree order."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                if c.checkState(0) == Qt.Checked and not c.isHidden():
                    out.append(c.data(0, Qt.UserRole))
        return out

    # ------------------------------------------------------------------ controls
    def _fill_metric(self):
        cur = self.metric.currentData()
        self.metric.blockSignals(True)
        self.metric.clear()
        cols = set(self.store.per.columns) if not self.store.per.empty else None
        for k, t in METRICS:
            if cols is None or k in cols:
                self.metric.addItem(t, k)
        if cols is not None:
            known = {k for k, _ in METRICS} | set(core.LABEL_COLS) | {"uid", "_src"} | set(self.store.user_fields)
            for c in sorted(cols - known):
                if not c.startswith(("meta_", "slice_", "set_", "file_", "results_folder", "folder")) \
                        and pd.api.types.is_numeric_dtype(self.store.per[c]):
                    self.metric.addItem(f"(other) {c}", c)
        i = self.metric.findData(cur)
        self.metric.setCurrentIndex(max(i, 0))
        self.metric.blockSignals(False)

    def _controls_changed(self, *_):
        t = self.ptype.currentData()
        self.xaxis.blockSignals(True)
        self.style_.blockSignals(True)
        cx, cs = self.xaxis.currentData(), self.style_.currentData()
        self.xaxis.clear()
        self.style_.clear()
        if t == "series":
            for k, lab in [("auto", "Depth for z-stacks, time for time-lapses"), ("z", "Depth z (µm)"),
                           ("time", "Time"), ("index", "Image number")]:
                self.xaxis.addItem(lab, k)
            for k, lab in [("line", "Lines with markers"), ("line_only", "Lines only"), ("markers", "Markers only")]:
                self.style_.addItem(lab, k)
        elif t == "perfile":
            for k, lab in [("bar", "Bars (mean ± error)"), ("box", "Box plots"), ("violin", "Violin plots"),
                           ("dots", "Mean ± error as points")]:
                self.style_.addItem(lab, k)
        elif t == "change":
            for k, lab in [("bar", "Bars")]:
                self.style_.addItem(lab, k)
        elif t == "stats":
            for k, lab in [("pct", "% change (after vs before)"), ("diff", "Absolute change (after − before)")]:
                self.style_.addItem(lab, k)
        elif t == "param":
            for k, lab, _ in self.param_x():
                self.xaxis.addItem(lab, k)
            for k, lab in [("points_lines", "Points joined by lines (per color)"), ("points", "Points only")]:
                self.style_.addItem(lab, k)
        elif t == "dist":
            for k, lab in [("density", "Distribution curve"), ("cumulative", "Cumulative (% below x)")]:
                self.style_.addItem(lab, k)
        for combo, cur in ((self.xaxis, cx), (self.style_, cs)):
            i = combo.findData(cur)
            combo.setCurrentIndex(max(i, 0))
        self.xaxis.blockSignals(False)
        self.style_.blockSignals(False)
        f = self.form3
        vis = dict(metric=t != "dist", dist=t == "dist", xaxis=t in ("series", "param"), style=True,
                   err=t in ("perfile", "param", "series"), smooth=t == "series", points=t in ("perfile", "series"),
                   logx=t == "param", sub_sham=t == "change")
        for w, on in [(self.metric, vis["metric"]), (self.dist, vis["dist"]), (self.xaxis, vis["xaxis"]),
                      (self.style_, vis["style"]), (self.err, vis["err"]), (self.smooth, vis["smooth"]),
                      (self.points, vis["points"]), (self.logx, vis["logx"]), (self.sub_sham, vis["sub_sham"])]:
            f.setRowVisible(w, on)
        self.points.setText("Show every analysed image as a dot" if t == "perfile" else
                            "Show the unsmoothed values too")
        self.err.setItemText(0, "SD over images" if t != "param" else "SD over images of each file")
        self.group_by.setEnabled(self.layout_.currentData() == "group")
        self._schedule()

    def _schedule(self, *_):
        self.group_by.setEnabled(self.layout_.currentData() == "group")
        self._timer.start(120)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _cond(r):
        if GS.is_sham(r.get("condition")):
            return "After sham"
        if r.get("condition") != "After AMF":
            return "Before AMF"
        parts = [f"{r['amf_freq_hz']:g} Hz" if pd.notna(r.get("amf_freq_hz")) else None,
                 r.get("amf_time") if isinstance(r.get("amf_time"), str) else None,
                 f"{r['amf_vpp_set']:g} Vpp" if pd.notna(r.get("amf_vpp_set")) else None,
                 f"{r['amf_v_measured']:g} V" if pd.notna(r.get("amf_v_measured")) else None,
                 f"{r['amf_app_set']:g} App" if pd.notna(r.get("amf_app_set")) else None,
                 f"{r['amf_coil_turns']:g} turns" if pd.notna(r.get("amf_coil_turns")) else None,
                 (f"{r['amf_b_mT']:g} mT {r.get('amf_b_type') if isinstance(r.get('amf_b_type'), str) else ''}".strip()
                  if pd.notna(r.get("amf_b_mT")) else None)]
        p = ", ".join(x for x in parts if x)
        return "After AMF" + (f", {p}" if p else "")

    def label(self, u):
        r = self.store.info[u]
        mode = self.label_by.currentData()
        no = str(r["file"]).split("_")[-1]
        if mode == "file":
            return u
        if mode == "no":
            return no
        if mode == "sample_cond":
            return f"{r['sample']} {self._cond(r)}"
        if mode == "nc":
            nc = core.fmt_conc(r.get("nc_conc_nM")) if pd.notna(r.get("nc_conc_nM")) else None
            mix = r.get("nc_mixing") if isinstance(r.get("nc_mixing"), str) else None
            inc = r.get("nc_incubation_time") if isinstance(r.get("nc_incubation_time"), str) else None
            ncs = ", ".join(x for x in (nc, mix + (f" {inc}" if inc and mix and "incub" in mix else "") if mix else None) if x)
            return f"{self._cond(r)}" + (f"; NC {ncs}" if ncs else "") + f" ({no})"
        return f"{self._cond(r)} ({no})"

    def colors(self, files):
        key = self.color_by.currentData()
        info = self.store.info
        if key == "condition":
            out, used, k = {}, set(), 0
            for u in files:
                cnd = info[u]["condition"]
                c = style.AFTER if cnd == "After AMF" else style.SHAM if GS.is_sham(cnd) else style.BEFORE
                tag = (info[u]["sample"], c)
                if tag in used:
                    c = core.EXTRA_COLORS[k % len(core.EXTRA_COLORS)]; k += 1
                used.add(tag)
                out[u] = c
            return out
        if key == "file":
            return {u: TAB[i % len(TAB)] for i, u in enumerate(files)}
        if key == "arm":
            cats = list(dict.fromkeys(str(info[u].get("arm") or "") for u in files))
            return {u: GS.ARM_COLORS.get(str(info[u].get("arm")), TAB[cats.index(str(info[u].get("arm") or "")) % len(TAB)])
                    for u in files}
        vals = [info[u].get(key) for u in files]
        num = [v for v in vals if isinstance(v, (int, float, np.number)) and pd.notna(v)]
        if key not in ("sample", "date", "nc_mixing") and len(num) == len(vals) and len(set(num)) > 1:
            lo, hi = min(num), max(num)
            cmap = matplotlib.colormaps["viridis"]
            return {u: mcolors.to_hex(cmap(0.1 + 0.8 * (v - lo) / (hi - lo))) for u, v in zip(files, vals)}
        cats = list(dict.fromkeys(str(v) for v in vals))
        return {u: TAB[cats.index(str(v)) % len(TAB)] for u, v in zip(files, vals)}

    def groups(self, files):
        mode = self.layout_.currentData()
        if mode == "overlay":
            return [("", files)]
        if mode == "file":
            return [(self.label(u), [u]) for u in files]
        key = self.group_by.currentData()
        g = {}
        for u in files:
            v = self.store.info[u].get(key)
            name = f"Sample {v}" if key == "sample" else (
                f"{dict(GROUP_BY).get(key, key.replace('_', ' '))}: {_fmt(v)}" if key not in ("condition",) else str(v))
            g.setdefault(name, []).append(u)
        return list(g.items())

    def _axes(self, n, polar=False):
        self.fig.clear()
        ncols = min(self.ncols.value(), n) if n > 1 else 1
        nrows = int(np.ceil(n / ncols))
        kw = {"projection": "polar"} if polar else {}
        axs = []
        first = None
        for i in range(n):
            share = first if (self.share_y.isChecked() and first is not None and not polar) else None
            a = self.fig.add_subplot(nrows, ncols, i + 1, sharey=share, **kw)
            first = first or a
            axs.append(a)
        if not self.auto_size.isChecked():
            self.fig.set_size_inches(self.fig_w.value(), self.fig_h.value())
        return axs

    def _err(self, vals):
        v = np.asarray(vals, float)
        v = v[np.isfinite(v)]
        if self.err.currentIndex() == 2 or v.size < 2:
            return 0.0
        sd = float(np.std(v, ddof=1))
        return sd / np.sqrt(v.size) if self.err.currentIndex() == 1 else sd

    def _finish(self, axs, xlabel, ylabel, title):
        fs = self.font.value()
        many = len(axs) > 1
        xl = self.xlabel.text() or xlabel
        yl = self.ylabel.text() or ylabel
        ncols = min(self.ncols.value(), len(axs)) if many else 1
        nrows = int(np.ceil(len(axs) / ncols))
        for i, a in enumerate(axs):
            if not getattr(a, "name", "") == "polar":
                a.grid(self.grid.isChecked(), alpha=0.3)
                last_row = i // ncols == nrows - 1 or i + ncols >= len(axs)
                if xl and (not many or last_row):
                    a.set_xlabel(xl)
                if yl and (not many or i % ncols == 0):
                    a.set_ylabel(yl)
                try:
                    lo = float(self.ymin.text()) if self.ymin.text().strip() else None
                    hi = float(self.ymax.text()) if self.ymax.text().strip() else None
                    if lo is not None or hi is not None:
                        a.set_ylim(lo, hi)
                except ValueError:
                    pass
            for item in [a.title, a.xaxis.label, a.yaxis.label] + a.get_xticklabels() + a.get_yticklabels():
                item.set_fontsize(fs if item in (a.xaxis.label, a.yaxis.label, a.title) else fs - 1)
        pos = self.legend_pos.currentIndex()
        if pos == 0:                      # automatic: inside a single graph, below several graphs
            pos = 1 if not many else 3
        handles, labels = [], []
        for a in axs:
            for h, l in zip(*a.get_legend_handles_labels()):
                if l not in labels and not l.startswith("_"):
                    handles.append(h); labels.append(l)
        if handles and pos != 4:
            if pos == 1:
                for a in axs:
                    if a.get_legend_handles_labels()[0]:
                        if getattr(a, "name", "") == "polar":
                            a.legend(fontsize=fs - 2, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.08))
                        else:
                            a.legend(fontsize=fs - 1, frameon=False)
            elif pos == 2:
                self.fig.legend(handles, labels, loc="outside right upper", fontsize=fs - 1, frameon=False)
            else:
                width_pt = self.fig.get_size_inches()[0] * 72
                longest = max(len(l) for l in labels)
                ncol = int(width_pt // (0.55 * (fs - 1) * longest + 36))
                self.fig.legend(handles, labels, loc="outside lower center", fontsize=fs - 1, frameon=False,
                                ncol=max(1, min(ncol, len(labels), 4)))
        t = self.title.text() or title
        if t:
            self.fig.suptitle(t, fontsize=fs + 2)

    # ------------------------------------------------------------------ drawing
    def redraw(self):
        self.plotted = []
        files = self.selected()
        if self.store.per.empty or not files:
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "Load results and tick files to plot" if self.store.per.empty else "Tick files to plot",
                    ha="center", va="center", color="#9aa3ab", fontsize=13, transform=ax.transAxes)
            self.canvas.draw_idle()
            self.note.setText("")
            return
        try:
            {"series": self.plot_series, "perfile": self.plot_perfile, "change": self.plot_change,
             "stats": self.plot_stats, "param": self.plot_param, "dist": self.plot_dist}[self.ptype.currentData()](files)
        except Exception as e:
            import traceback
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, f"Cannot draw this graph:\n{e}", ha="center", va="center", color=style.ERROR,
                    transform=ax.transAxes, wrap=True)
            traceback.print_exc()
        self.canvas.draw_idle()

    def _metric(self):
        return self.metric.currentData(), self.metric.currentText()

    def plot_series(self, files):
        col, mlabel = self._metric()
        per = self.store.per
        cols = self.colors(files)
        groups = self.groups(files)
        axs = self._axes(len(groups))
        xmode = self.xaxis.currentData()
        st = self.style_.currentData()
        fmt = {"line": "o-", "line_only": "-", "markers": "o"}[st]
        xlabels = set()
        w = self.smooth.value()
        for a, (gname, us) in zip(axs, groups):
            for u in us:
                df = per[per.uid == u]
                kind = str(df["series"].iloc[0])
                mode = xmode if xmode != "auto" else ("time" if kind == "time-lapse" else "z")
                if mode == "time" and kind == "time-lapse":
                    g = df.groupby("t_index")
                    y = g[col].mean().to_numpy()
                    t = g["time_s"].first().to_numpy()
                    if np.isfinite(t).all():
                        sc = 60.0 if np.nanmax(t) > 120 else 1.0
                        x = t / sc
                        xlabels.add(f"time ({'min' if sc == 60 else 's'})")
                    else:
                        x = np.array(list(g.groups.keys()), float)
                        xlabels.add("time point")
                elif mode == "index":
                    x, y = np.arange(len(df)), df[col].to_numpy()
                    xlabels.add("image number")
                else:
                    x, y = df["z_um"].to_numpy(float), df[col].to_numpy(float)
                    xlabels.add("depth z (µm, bottom → top)")
                ys = y
                if w > 1 and len(y) > w:
                    ys = pd.Series(y).rolling(w, center=True, min_periods=1).mean().to_numpy()
                    if self.points.isChecked():
                        a.plot(x, y, "o", ms=2.5, alpha=0.35, color=cols[u], label="_raw")
                if len(x) == 1:
                    a.errorbar(x, ys, yerr=self._err(df[col]), fmt="o", ms=7, color=cols[u], capsize=4,
                               label=self.label(u))
                else:
                    a.plot(x, ys, fmt, ms=3.5, lw=1.6, color=cols[u], label=self.label(u))
                for xi, yi in zip(x, ys):
                    self.plotted.append(dict(panel=gname, file=u, label=self.label(u), x=xi, y=yi))
            a.set_title(gname)
        self._finish(axs, " / ".join(sorted(xlabels)), mlabel,
                     f"{mlabel}" + (" (smoothed over %d images)" % w if w > 1 else ""))
        self.note.setText("Time-lapses with several z-slices per time point are averaged over z. "
                          "Single images appear as one point (± error).")

    def plot_perfile(self, files):
        col, mlabel = self._metric()
        per = self.store.per
        cols = self.colors(files)
        groups = self.groups(files)
        axs = self._axes(len(groups))
        st = self.style_.currentData()
        for a, (gname, us) in zip(axs, groups):
            xs = np.arange(len(us))
            data = [per.loc[per.uid == u, col].dropna().to_numpy(float) for u in us]
            means = [d.mean() if d.size else np.nan for d in data]
            errs = [self._err(d) for d in data]
            if st == "bar":
                a.bar(xs, means, yerr=errs, capsize=4, color=[cols[u] for u in us], alpha=0.85,
                      edgecolor="white")
            elif st == "box":
                bp = a.boxplot(data, positions=xs, widths=0.55, patch_artist=True, showfliers=False)
                for patch, u in zip(bp["boxes"], us):
                    patch.set_facecolor(mcolors.to_rgba(cols[u], 0.35)); patch.set_edgecolor(cols[u])
                for med in bp["medians"]:
                    med.set_color("#222")
            elif st == "violin":
                ok = [i for i, d in enumerate(data) if d.size > 1]
                if ok:
                    vp = a.violinplot([data[i] for i in ok], positions=xs[ok], widths=0.7, showmeans=True)
                    for body, i in zip(vp["bodies"], ok):
                        body.set_facecolor(cols[us[i]]); body.set_alpha(0.45)
            else:
                for x, m, e, u in zip(xs, means, errs, us):
                    a.errorbar([x], [m], yerr=[e], fmt="o", ms=8, color=cols[u], capsize=5)
            if self.points.isChecked() or st in ("box", "violin"):
                rng = np.random.default_rng(0)
                for x, d, u in zip(xs, data, us):
                    a.plot(x + rng.uniform(-0.12, 0.12, d.size), d, "o", ms=2.6, color="#333", alpha=0.45,
                           label="_pts")
            for u in us:
                a.bar([0], [0], width=0, color=cols[u], label=self.label(u))
            a.set_xticks(xs)
            a.set_xticklabels([str(self.store.info[u]["file"]).split("_")[-1] for u in us])
            a.set_title(gname)
            for u, d, m, e in zip(us, data, means, errs):
                self.plotted.append(dict(panel=gname, file=u, label=self.label(u), n_images=d.size, mean=m,
                                         error=e, error_type=self.err.currentText()))
        self._finish(axs, "file no.", mlabel, mlabel)
        self.note.setText("Bars: mean over the analysed images of each file; error bars as chosen. Images of one "
                          "stack are not independent replicates — use separate gels for statistics.")

    def _per_file(self, files, col):
        """Per-file table of the ticked files (mean of col over each file's images) for gelstats."""
        per = self.store.per[self.store.per["uid"].isin(files)]
        g = per.groupby("uid", sort=False)
        keep = [c for c in ("date", "sample", "_src", "condition", "arm", "nc_conc_nM", "qc_status", "amf_freq_hz",
                            "amf_time_s", "amf_b_peak_mT", "nc_mixing", "collagen_mg_ml") if c in per]
        pf = g[keep].first().reset_index()
        pf["value"] = g[col].mean().to_numpy()
        pf["file"] = pf["uid"]
        return pf

    def _changes(self, files, change="pct"):
        col, _ = self._metric()
        exp, notes = GS.pair_changes(self._per_file(files, col), col, change=change, exclude_qc=False,
                                     value_col="value")
        if exp.empty:
            raise ValueError("No sample has both a Before file and an After file (AMF or sham) among the ticked files.")
        return GS.control_correct(exp), notes

    def plot_change(self, files):
        col, mlabel = self._metric()
        info = self.store.info
        exp, _ = self._changes(files, "pct")
        corr = self.sub_sham.isChecked()
        if corr:
            ctrl = exp["arm"].isin(GS.CONTROL_ARMS)
            if not ctrl.any():
                raise ValueError("No sham gel (‘sham’ in the file name) among the ticked files — untick "
                                 "‘Subtract the change of the sham gels’ or tick sham files too.")
            exp = exp[~ctrl & exp["y_corrected"].notna()]
            if exp.empty:
                raise ValueError("No AMF sample with a matching sham control among the ticked files.")
        rows = [(r["sample"], r["file"], r["y_corrected"] if corr else r["y"], r["before"], r["after"],
                 r.get("control_mean"), r.get("control_match"))
                for _, r in exp.iterrows()]
        us_all = [u for _, u, *_ in rows]
        groups = self.groups(us_all) if self.layout_.currentData() == "group" else [("", us_all)]
        axs = self._axes(len(groups))
        cols = self.colors(us_all)
        lookup = {u: (smp, ch, b, v, cm, cmatch) for smp, u, ch, b, v, cm, cmatch in rows}
        for a, (gname, us) in zip(axs, groups):
            xs = np.arange(len(us))
            vals = [lookup[u][1] for u in us]
            a.bar(xs, vals, color=[cols[u] for u in us], alpha=0.85)
            a.axhline(0, color="#333", lw=0.8)
            a.set_xticks(xs)
            a.set_xticklabels([f"{lookup[u][0]}\n({str(info[u]['file']).split('_')[-1]})" for u in us], fontsize=8)
            for x, v in zip(xs, vals):
                if np.isfinite(v):
                    a.annotate(f"{v:+.0f} %", (x, v), ha="center", va="bottom" if v >= 0 else "top", fontsize=8,
                               xytext=(0, 2 if v >= 0 else -2), textcoords="offset points")
            for u in us:
                a.bar([0], [0], width=0, color=cols[u], label=self.label(u))
                smp, ch, b, v, cm, cmatch = lookup[u]
                row = dict(panel=gname, sample=smp, after_file=u, arm=info[u].get("arm"), before_mean=b, after_mean=v,
                           change_pct=ch)
                if corr:
                    row.update(change_pct_raw=ch + cm, sham_change_pct=cm, sham_match=cmatch)
                    row["change_pct_minus_sham"] = row.pop("change_pct")
                self.plotted.append(row)
            a.set_title(gname)
        what = "minus the sham change" if corr else "after vs before"
        self._finish(axs, "sample (after file)", f"change of {mlabel.split(' (')[0].lower()} (%)"
                     + (" − sham" if corr else ""), f"Change {what}: {mlabel}")
        self.note.setText("Change = (after − before) / before × 100, using the mean over the analysed images of each "
                          "file. With several before files in a sample their mean is used."
                          + (" The mean % change of the sham gels of the same date (all dates if none) is subtracted."
                             if corr else " Grey bars: sham gels (no field)."))

    def plot_stats(self, files):
        col, mlabel = self._metric()
        change = self.style_.currentData() or "pct"
        exp, notes = self._changes(files, change)
        unit = "%" if change == "pct" else ""
        info = self.store.info
        ctrl = exp["arm"].isin(GS.CONTROL_ARMS)
        key = self.group_by.currentData() if self.layout_.currentData() == "group" else None
        if key in (None, "arm", "condition"):
            panels = [("", exp)]
        else:                                   # sham gels have no AMF settings: shown in every panel
            vals = exp.loc[~ctrl, "file"].map(lambda u: info[u].get(key))
            panels = []
            for v in dict.fromkeys(vals):
                sub = exp[(~ctrl & (vals.reindex(exp.index) == v)) | ctrl]
                lab = f"Sample {v}" if key == "sample" else f"{dict(GROUP_BY).get(key, key.replace('_', ' '))}: {_fmt(v)}"
                panels.append((lab, sub))
        axs = self._axes(len(panels))
        texts = []
        rng = np.random.default_rng(0)
        for a, (gname, sub) in zip(axs, panels):
            gels = GS.per_gel(sub, "y")
            arms = [arm for arm in GS.ARMS if (gels["arm"] == arm).any()]
            if not arms:
                continue
            summ, comp = GS.arm_statistics(sub, "y")
            for x, arm in enumerate(arms):
                v = gels.loc[gels["arm"] == arm, "value"].to_numpy(float)
                st = summ[summ["arm"] == arm].iloc[0]
                c = GS.ARM_COLORS.get(arm, "#555")
                a.bar(x, st["mean"], width=0.6, color=c, alpha=0.35, edgecolor=c)
                if np.isfinite(st["ci95_low"]):
                    a.errorbar(x, st["mean"], yerr=[[st["mean"] - st["ci95_low"]], [st["ci95_high"] - st["mean"]]],
                               fmt="none", ecolor="#333", capsize=5, lw=1.2)
                a.scatter(x + rng.uniform(-0.12, 0.12, v.size), v, s=30, color=c, edgecolor="white", zorder=3)
                a.annotate(f"n = {int(st['n_gels'])}\n{GS.fmt_p(st['p_ttest_vs0'])}", (x, 1.0),
                           xycoords=("data", "axes fraction"), ha="center", va="top", fontsize=8, color="#333")
                gl = gels[gels["arm"] == arm]
                for (_, gr), val in zip(gl.iterrows(), v):
                    self.plotted.append(dict(panel=gname, row="gel", arm=arm, date=gr["date"], sample=gr["sample"],
                                             n_files=gr["n_files"], value=val))
                self.plotted.append(dict(panel=gname, row="summary", arm=arm,
                                         **{k: st[k] for k in st.index if k not in ("arm",)}))
            for _, cr in comp.iterrows():
                head = f"{(gname + ': ') if gname else ''}{cr['group_a']} vs {cr['group_b']}: Δ {cr['difference']:+.3g}{unit}"
                if np.isfinite(cr["p_welch"]):
                    texts.append(f"{head} [{cr['diff_ci95_low']:+.3g}, {cr['diff_ci95_high']:+.3g}], "
                                 f"Welch {GS.fmt_p(cr['p_welch'])} (Holm {GS.fmt_p(cr['p_welch_holm'])}), "
                                 f"Mann–Whitney {GS.fmt_p(cr['p_mannwhitney'])}, g = {cr['hedges_g']:.2f}")
                else:
                    texts.append(f"{head} (needs ≥ 2 gels per group for a test)")
                self.plotted.append(dict(panel=gname, row="comparison", **cr.to_dict()))
            a.axhline(0, color="#333", lw=0.8)
            a.set_xticks(range(len(arms)))
            a.set_xticklabels([arm.replace(" (", "\n(") for arm in arms], fontsize=8.5)
            lo, hi = a.get_ylim()
            a.set_ylim(lo, hi + 0.22 * (hi - lo))
            a.set_title(gname)
        ylab = (f"{mlabel.split(' (')[0]}: change (%)" if change == "pct"
                else f"Δ {mlabel} (after − before)")
        self._finish(axs, "", ylab, f"{mlabel}: change by group (dots = gels, bars = mean ± 95 % CI)")
        self.note.setText("Each dot is one gel (After files of a gel averaged). Above each bar: paired test of "
                          "the change against 0 (t-test). " + (" | ".join(texts) if texts else
                          "Tick sham gels too for the field-effect comparison (NC + AMF vs sham).")
                          + (" " + "; ".join(notes) if notes else ""))

    def plot_param(self, files):
        col, mlabel = self._metric()
        xcol = self.xaxis.currentData()
        xlab, sc = next((l, s) for k, l, s in self.param_x() if k == xcol)
        per = self.store.per
        info = self.store.info
        cols = self.colors(files)
        groups = self.groups(files)
        axs = self._axes(len(groups))
        missing = []
        for a, (gname, us) in zip(axs, groups):
            series = {}
            for u in us:
                try:
                    xv = float(info[u].get(xcol))
                    assert np.isfinite(xv)
                except (TypeError, ValueError, AssertionError):
                    missing.append(u)
                    continue
                d = per.loc[per.uid == u, col].dropna().to_numpy(float)
                series.setdefault(cols[u], []).append((float(xv) * sc, d.mean(), self._err(d), u))
            for c, pts in series.items():
                pts.sort()
                x, y, e, us_ = zip(*pts)
                lab = self._legend_for_color(us_)
                if self.style_.currentData() == "points_lines" and len(pts) > 1:
                    a.plot(x, y, "-", color=c, lw=1.2, alpha=0.8)
                a.errorbar(x, y, yerr=e, fmt="o", ms=6, color=c, capsize=4, label=lab)
                for xi, yi, ei, u in pts:
                    self.plotted.append(dict(panel=gname, file=u, x=xi, mean=yi, error=ei))
            if self.logx.isChecked():
                a.set_xscale("log")
            a.set_title(gname)
        self._finish(axs, xlab, mlabel, f"{mlabel} vs {xlab.split(' (')[0]}")
        self.note.setText(("Not shown (no value in the file name): " + ", ".join(missing[:4]) +
                           ("…" if len(missing) > 4 else "") + ". ") if missing else
                          "Each point = one file (mean over its images ± error). Colors as chosen under ‘Colors’.")

    def param_x(self):
        num = [k for k in self.store.user_fields if pd.api.types.is_numeric_dtype(self.store.per[k])]
        return PARAM_X + [(k, f"{k.replace('_', ' ')} (added)", 1.0) for k in num]

    def _fill_choice_lists(self):
        extra = [(k, f"{k.replace('_', ' ')} (added)") for k in self.store.user_fields]
        for combo, base in ((self.color_by, COLOR_BY), (self.group_by, GROUP_BY)):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for k, t in base + extra:
                combo.addItem(t, k)
            i = combo.findData(cur)
            combo.setCurrentIndex(max(i, 0))
            combo.blockSignals(False)
        self._controls_changed()

    def _legend_for_color(self, us):
        key = self.color_by.currentData()
        r = self.store.info[us[0]]
        if key == "condition":
            return r["condition"]
        if key == "file":
            return self.label(us[0])
        return f"{dict(COLOR_BY).get(key, key.replace('_', ' '))}: {_fmt(r.get(key))}"

    def plot_dist(self, files):
        which = self.dist.currentData()
        have = [u for u in files if which in self.store.hists.get(u, {})]
        if not have:
            raise ValueError("No distribution data for these files. Distributions need the *_distributions.npz "
                             "file next to the spreadsheet (written by version 1.0 of the app or newer). "
                             "Re-run the analysis to create it.")
        cols = self.colors(have)
        groups = self.groups(have)
        polar = which == "ang"
        axs = self._axes(len(groups), polar=polar)
        cum = self.style_.currentData() == "cumulative"
        xmax_all = None
        if which != "ang":          # same x-range in every panel
            xmax_all = {"lt": core.lt_xmax, "fib": core.fib_xmax, "seg": core.seg_xmax}[which](
                [np.asarray(self.store.hists[u][which], float) for u in have])
        xlab = {"lt": core.DIST_X, "fib": core.FIB_X, "seg": core.SEG_X, "ang": ""}[which]
        for a, (gname, us) in zip(axs, groups):
            for u in us:
                h = np.asarray(self.store.hists[u][which], float)
                lab = self.label(u)
                if which == "ang":
                    S = self.store.hists[u].get("S", [np.nan, np.nan])
                    core.rose(a, h, cols[u], label=f"{lab}: S = {S[0]:.2f}")
                    for c_, v in zip(np.linspace(-87.5, 87.5, len(h)), h / max(h.sum(), 1)):
                        self.plotted.append(dict(panel=gname, file=u, angle_deg=c_, fraction=v))
                    continue
                edges = {"lt": core.LT_EDGES, "fib": core.FIB_EDGES, "seg": core.SEG_EDGES}[which]
                tot = h.sum()
                if tot <= 0:
                    continue
                if cum or which == "fib":
                    x = edges[1:-1]
                    y = 100 * np.cumsum(h[:-1]) / tot
                    a.step(np.r_[0, x], np.r_[0, y], where="post", color=cols[u], lw=1.7, label=lab)
                elif which == "lt":
                    x, y = core.lt_curve(h)
                    a.plot(x, y, "o-", ms=2.5, color=cols[u], lw=1.6, label=lab)
                else:
                    fine = h[:-1]
                    idx = np.searchsorted(core.SEG_EDGES[:-1], core.SEG_PLOT_EDGES[:-1], side="left")
                    hh = np.add.reduceat(fine, idx)
                    x = (core.SEG_PLOT_EDGES[:-1] + core.SEG_PLOT_EDGES[1:]) / 2
                    y = 100 * hh / tot / np.diff(core.SEG_PLOT_EDGES)
                    a.plot(x, y, "o-", ms=2.5, color=cols[u], lw=1.6, label=lab)
                for xi, yi in zip(x, y):
                    self.plotted.append(dict(panel=gname, file=u, x=xi, y=yi))
            if polar:
                core.style_polar(a, gname, note=False)
            else:
                a.set_xlim(0, xmax_all)
                a.set_ylim(bottom=0)
                a.set_title(gname)
        ylab = ("% below x" if (cum or which == "fib") else
                {"lt": core.DIST_Y, "seg": core.SEG_Y}.get(which, ""))
        self._finish(axs, xlab, ylab, dict(DISTS)[which] + " — all analysed images of each file")
        self.note.setText({"lt": "Area-weighted: share of the pore area at each pore size.",
                           "fib": "Fiber size is shown cumulative (values come in pixel steps).",
                           "seg": "Each fiber segment counts once.",
                           "ang": "Radius = fraction of fiber pixels; S = order parameter (0 random, 1 aligned)."}[which])

    # ------------------------------------------------------------------ export
    def save_figure(self):
        if not self.plotted:
            QMessageBox.information(self, "Save figure", "Nothing plotted yet.")
            return
        start = QSettings().value("plots/save_dir", QSettings().value("plots/last_dir", os.path.expanduser("~")))
        p, flt = QFileDialog.getSaveFileName(self, "Save figure", os.path.join(start, "figure.png"),
                                             "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif)")
        if not p:
            return
        if not os.path.splitext(p)[1]:
            p += "." + flt.split("*.")[-1].rstrip(")")
        QSettings().setValue("plots/save_dir", os.path.dirname(p))
        old = self.fig.get_size_inches()
        try:
            if not self.auto_size.isChecked():
                self.fig.set_size_inches(self.fig_w.value(), self.fig_h.value())
            self.fig.savefig(p, dpi=self.dpi.value(), bbox_inches="tight", facecolor="white")
        finally:
            self.fig.set_size_inches(old)
            self.canvas.draw_idle()
        self.note.setText(f"Saved {p}")

    def export_csv(self):
        if not self.plotted:
            QMessageBox.information(self, "Export data", "Nothing plotted yet.")
            return
        start = QSettings().value("plots/save_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getSaveFileName(self, "Export plotted data", os.path.join(start, "plot_data.csv"), "CSV (*.csv)")
        if p:
            pd.DataFrame(self.plotted).to_csv(p, index=False)
            self.note.setText(f"Saved {p}")
