"""Results & Plots: load result spreadsheets and build custom graphs from any files."""
import glob
import os
import re

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QHeaderView, QSizePolicy)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.colors as mcolors

import collagen_core as core
import gelstats as GS
from .plot_style import (StylePanel, DragLabels, rc, cond_colors, fit_line, PALETTES, NUMERIC_CMAP, FIG_SIZES,
                         register_app_font)
from .widgets import Page, button, hint, hbox
from .widgets import ThemedCanvas
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
TILES = [("series", "Depth / time", "lines"), ("perfile", "Per file", "bars"), ("change", "Change per gel", "delta"),
         ("stats", "By group + stats", "groups"), ("param", "Vs setting", "scatter"), ("dist", "Distribution", "dist")]
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
        super().__init__("Results & plots", "Load result spreadsheets (mmddyy_summary.xlsx or summary.xlsx) and "
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
        tiles = QWidget()
        tg = QGridLayout(tiles)
        tg.setContentsMargins(0, 0, 0, 4)
        tg.setSpacing(8)
        self.tiles, grp = {}, QButtonGroup(self)
        for i, (k, t, ic) in enumerate(TILES):
            b = QPushButton(" " + t)
            b.setObjectName("tile")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(dict(PLOT_TYPES)[k])
            b.clicked.connect(lambda _=False, key=k: self.ptype.setCurrentIndex(self.ptype.findData(key)))
            grp.addButton(b)
            tg.addWidget(b, i // 2, i % 2)
            self.tiles[k] = (b, ic)
        f.addRow(tiles)
        self.ptype = QComboBox()
        for k, t in PLOT_TYPES:
            self.ptype.addItem(t, k)
        f.addRow("Graph", self.ptype)
        self.metric_cb = QComboBox()
        f.addRow("Metric", self.metric_cb)
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
        self.err.addItems(["SD over images", "SEM over images", "No error bars", "95 % CI of the mean"])
        f.addRow("Error bars", self.err)
        self.smooth = QSpinBox()
        self.smooth.setRange(1, 51)
        self.smooth.setSingleStep(2)
        self.smooth.setToolTip("Moving average over this many neighbouring images (1 = none). Only for lines vs "
                               "depth / time.")
        f.addRow("Smoothing (images)", self.smooth)
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
        self.title = QLineEdit()
        self.title.setPlaceholderText("automatic")
        f.addRow("Title", self.title)
        self.xlabel = QLineEdit()
        self.xlabel.setPlaceholderText("automatic")
        f.addRow("x-axis label", self.xlabel)
        self.ylabel = QLineEdit()
        self.ylabel.setPlaceholderText("automatic")
        f.addRow("y-axis label", self.ylabel)
        cl.addWidget(g)

        g = QGroupBox("5. Style")
        gl = QVBoxLayout(g)
        register_app_font()
        self.style_panel = StylePanel()
        gl.addWidget(self.style_panel)
        cl.addWidget(g)

        g = QGroupBox("6. Save")
        f = QFormLayout(g)
        self.size_preset = QComboBox()
        for k, t, _ in FIG_SIZES:
            self.size_preset.addItem(t, k)
        self.size_preset.setToolTip("Figure size on screen and when saved; text sizes stay as set under Style")
        f.addRow("Figure size", self.size_preset)
        self.fig_w = QDoubleSpinBox(); self.fig_w.setRange(2, 40); self.fig_w.setValue(8); self.fig_w.setSuffix(" in")
        self.fig_h = QDoubleSpinBox(); self.fig_h.setRange(2, 40); self.fig_h.setValue(5); self.fig_h.setSuffix(" in")
        self.auto_size = QCheckBox("automatic")
        self.auto_size.setChecked(True)
        self.auto_size.hide()
        self.size_row = hbox(self.fig_w, QLabel("×"), self.fig_h, stretch_end=False)
        f.addRow("Width × height", self.size_row)
        self.size_preset.currentIndexChanged.connect(self._size_preset)
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
        self.canvas = ThemedCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, right)
        rl.addWidget(self.toolbar)
        rl.addWidget(self.canvas, 1)
        self._right_lay = rl
        self.cards = QWidget()
        self.cards_lay = QHBoxLayout(self.cards)
        self.cards_lay.setContentsMargins(0, 6, 0, 0)
        self.cards_lay.setSpacing(10)
        self.cards.hide()
        rl.addWidget(self.cards)
        self.note = hint("")
        rl.addWidget(self.note)
        split.addWidget(right)
        split.setSizes([480, 880])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.redraw)
        for w in (self.ptype, self.metric_cb, self.dist, self.xaxis, self.style_, self.err, self.layout_, self.group_by,
                  self.color_by, self.label_by):
            w.currentIndexChanged.connect(self._controls_changed)
        for w in (self.smooth, self.ncols, self.fig_w, self.fig_h):
            w.valueChanged.connect(self._schedule)
        for w in (self.fig_w, self.fig_h):
            w.valueChanged.connect(self._fit_canvas)
        for w in (self.logx, self.share_y, self.auto_size, self.sub_sham):
            w.toggled.connect(self._schedule)
        for w in (self.title, self.xlabel, self.ylabel):
            w.textChanged.connect(self._schedule)
        self.drag = DragLabels(self.canvas, busy=lambda: bool(str(getattr(self.toolbar, "mode", "") or "")))
        self.style_panel.changed.connect(self._schedule)
        self.style_panel.reset_positions.connect(lambda: (self.drag.reset(), self._schedule()))
        self._last_ptype = None
        self._size_preset()
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
        known = set(getattr(self, "_known", set()))         # files shown before: keep their tick; new ones ticked
        self._known = set(self.store.info)
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
                it.setCheckState(0, Qt.Checked if (old is None or u in old or u not in known) else Qt.Unchecked)
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
        cur = self.metric_cb.currentData()
        self.metric_cb.blockSignals(True)
        self.metric_cb.clear()
        cols = set(self.store.per.columns) if not self.store.per.empty else None
        for k, t in METRICS:
            if cols is None or k in cols:
                self.metric_cb.addItem(t, k)
        if cols is not None:
            known = {k for k, _ in METRICS} | set(core.LABEL_COLS) | {"uid", "_src"} | set(self.store.user_fields)
            for c in sorted(cols - known):
                if not c.startswith(("meta_", "slice_", "set_", "file_", "results_folder", "folder")) \
                        and pd.api.types.is_numeric_dtype(self.store.per[c]):
                    self.metric_cb.addItem(f"(other) {c}", c)
        i = self.metric_cb.findData(cur)
        self.metric_cb.setCurrentIndex(max(i, 0))
        self.metric_cb.blockSignals(False)

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
        for k, (b, ic) in self.tiles.items():
            b.setChecked(k == t)
            b.setIcon(style.icon(ic, style.ACCENT_TEXT if k == t else style.TEXT2, 18))
        f = self.form3
        f.setRowVisible(self.ptype, False)
        vis = dict(metric=t != "dist", dist=t == "dist", xaxis=t in ("series", "param"), style=True,
                   err=t in ("perfile", "param", "series"), smooth=t == "series",
                   logx=t == "param", sub_sham=t == "change")
        for w, on in [(self.metric_cb, vis["metric"]), (self.dist, vis["dist"]), (self.xaxis, vis["xaxis"]),
                      (self.style_, vis["style"]), (self.err, vis["err"]), (self.smooth, vis["smooth"]),
                      (self.logx, vis["logx"]), (self.sub_sham, vis["sub_sham"])]:
            f.setRowVisible(w, on)
        if t != self._last_ptype:              # a new kind of graph: earlier label moves no longer fit
            self.drag.reset()
            self._last_ptype = t
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
        S = self.S
        cb, ca, cs, cn = cond_colors(S)
        tab = PALETTES.get(S["palette"], TAB)
        if key == "condition":
            out, used, k = {}, set(), 0
            for u in files:
                cnd = info[u]["condition"]
                c = (cs if GS.is_sham(cnd) else cn if str(info[u].get("arm", "")).startswith("AMF only") else ca) \
                    if str(cnd).startswith("After") else cb
                tag = (info[u]["sample"], c)
                if tag in used:
                    c = core.EXTRA_COLORS[k % len(core.EXTRA_COLORS)]; k += 1
                used.add(tag)
                out[u] = c
            return out
        if key == "file":
            return {u: tab[i % len(tab)] for i, u in enumerate(files)}
        if key == "arm":
            cats = list(dict.fromkeys(str(info[u].get("arm") or "") for u in files))
            return {u: self.arm_color(info[u].get("arm")) or tab[cats.index(str(info[u].get("arm") or "")) % len(tab)]
                    for u in files}
        vals = [info[u].get(key) for u in files]
        num = [v for v in vals if isinstance(v, (int, float, np.number)) and pd.notna(v)]
        if key not in ("sample", "date", "nc_mixing") and len(num) == len(vals) and len(set(num)) > 1:
            lo, hi = min(num), max(num)
            cmap = matplotlib.colormaps[NUMERIC_CMAP.get(S["palette"], "viridis")]
            return {u: mcolors.to_hex(cmap(0.1 + 0.8 * (v - lo) / (hi - lo))) for u, v in zip(files, vals)}
        cats = list(dict.fromkeys(str(v) for v in vals))
        return {u: tab[cats.index(str(v)) % len(tab)] for u, v in zip(files, vals)}

    def arm_color(self, arm):
        """Graph colour of a group (NC + AMF, AMF only, sham …) from the style's condition colours."""
        cb, ca, cs, cn = cond_colors(self.S)
        a = str(arm or "")
        return ca if a.startswith("NC + AMF") else cn if a.startswith("AMF only") else cs if a.startswith("sham") \
            else None

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
        self._axs = axs
        if not self.auto_size.isChecked():
            self.fig.set_size_inches(self.fig_w.value(), self.fig_h.value())
        return axs

    def _err(self, vals):
        v = np.asarray(vals, float)
        v = v[np.isfinite(v)]
        k = self.err.currentIndex()
        if k == 2 or v.size < 2:
            return 0.0
        sd = float(np.std(v, ddof=1))
        if k == 1:
            return sd / np.sqrt(v.size)
        if k == 3:
            from scipy import stats
            return float(stats.t.ppf(0.975, v.size - 1) * sd / np.sqrt(v.size))
        return sd

    def _size_preset(self, *_):
        k = self.size_preset.currentData()
        size = dict((a, c) for a, _, c in FIG_SIZES).get(k)
        self.auto_size.setChecked(k == "auto")
        self.size_row.setEnabled(k == "custom")
        if size:
            for w, v in zip((self.fig_w, self.fig_h), size):
                w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        self._fit_canvas()
        self._schedule()

    def _fit_canvas(self, *_):
        """Fixed figure size: show it at that size (text sizes in pt then look as they will on paper)."""
        if self.auto_size.isChecked():
            self.canvas.setMinimumSize(120, 120)
            self.canvas.setMaximumSize(16777215, 16777215)
            self._right_lay.setAlignment(self.canvas, Qt.Alignment())
            return
        self._right_lay.setAlignment(self.canvas, Qt.AlignHCenter | Qt.AlignTop)
        ratio = getattr(self.canvas, "device_pixel_ratio", 1) or 1
        w = int(self.fig_w.value() * self.fig.dpi / ratio)
        h = int(self.fig_h.value() * self.fig.dpi / ratio)
        self.canvas.setFixedSize(max(w, 120), max(h, 120))

    # ---- style helpers for the graphs
    @property
    def S(self):
        return self.style_panel.values()

    def _bar_kw(self, S, color):
        kw = dict(width=S["bar_width"], color=color, alpha=S["bar_alpha"])
        kw["edgecolor"] = "#222222" if S["bar_edge"] else "none"
        kw["linewidth"] = 0.8 if S["bar_edge"] else 0
        kw["error_kw"] = dict(elinewidth=S["err_lw"], capsize=S["capsize"], capthick=S["err_lw"], ecolor="#222222")
        return kw

    def _points(self, a, x, v, color, S, rng, zorder=3, label="_pts"):
        """Individual data points around x (spread sideways), styled from S."""
        v = np.asarray(v, float)
        if v.size == 0:
            return
        xs = x + rng.uniform(-S["jitter"], S["jitter"], v.size) if S["jitter"] else np.full(v.size, x, float)
        ms = S["point_size"] ** 2
        if S["point_color"] == "white":
            a.scatter(xs, v, s=ms, facecolor="white", edgecolor=color, linewidth=0.9, alpha=max(S["point_alpha"], 0.6),
                      zorder=zorder, label=label)
        else:
            c = color if S["point_color"] == "match" else "#333333"
            a.scatter(xs, v, s=ms, color=c, alpha=S["point_alpha"], edgecolor="white" if c == color else "none",
                      linewidth=0.4, zorder=zorder, label=label)

    def _fit(self, a, x, y, color, S, label_prefix=""):
        r = fit_line(x, y, S["fit"])
        if r is None:
            return None
        lab = (f"{label_prefix}{r['text']}" if S["fit_eq"] else "_fit")
        a.plot(r["xs"], r["ys"], "--", color=color, lw=max(S["line_width"] * 0.9, 0.6), label=lab, zorder=2)
        if S["fit_band"] and r["lo"] is not None:
            a.fill_between(r["xs"], r["lo"], r["hi"], color=color, alpha=0.12, lw=0, zorder=1)
        return r

    def _finish(self, axs, xlabel, ylabel, title, bars=False):
        """Labels, axes, legend and title of a finished graph, styled from the Style panel."""
        S = self.S
        many = len(axs) > 1
        xl = self.xlabel.text() or xlabel
        yl = self.ylabel.text() or ylabel
        ncols = min(self.ncols.value(), len(axs)) if many else 1
        nrows = int(np.ceil(len(axs) / ncols))
        # long axis labels on a small figure: wrap them so they fit next to the axes
        import textwrap
        wi, hi_ = self.fig.get_size_inches()
        fit_y = int(0.8 * hi_ * 72 / (0.55 * S["size_label"]) / (1 if (nrows == 1 or not many) else 1))
        fit_x = int(0.8 * wi * 72 / ncols / (0.55 * S["size_label"]))
        if yl and "\n" not in yl and len(yl) > max(fit_y, 12):
            yl = textwrap.fill(yl, max(fit_y, 12))
        if xl and "\n" not in xl and len(xl) > max(fit_x, 12):
            xl = textwrap.fill(xl, max(fit_x, 12))

        def num(txt):
            try:
                return float(txt) if str(txt).strip() else None
            except ValueError:
                return None
        ylo, yhi, xlo, xhi = (num(S[k]) for k in ("ymin", "ymax", "xmin", "xmax"))
        for i, a in enumerate(axs):
            polar = getattr(a, "name", "") == "polar"
            if not S["show_panel_titles"]:
                a.set_title("")
            if polar:
                continue
            if S["grid"]:
                a.grid(True, alpha=0.3)
            else:
                a.grid(False)
            a.set_axisbelow(True)
            last_row = i // ncols == nrows - 1 or i + ncols >= len(axs)
            if xl and (not many or last_row):
                a.set_xlabel(xl)
            if yl and (not many or (i % ncols == 0 and nrows == 1)):
                a.set_ylabel(yl)
            a.tick_params(direction=S["tick_dir"], labelsize=S["size_tick"])
            if S["xrot"]:
                for lab in a.get_xticklabels():
                    lab.set_rotation(S["xrot"])
                    lab.set_ha("right" if S["xrot"] < 90 else "center")
                    lab.set_rotation_mode("anchor")
            if S["logy"]:
                lo_ = min((ln.get_ydata().min() for ln in a.lines if len(ln.get_ydata())), default=1)
                if lo_ > 0 or not a.lines:
                    a.set_yscale("log")
            elif bars and S["yzero"]:
                lo, hi = a.get_ylim()
                if lo >= 0 or all(p.get_height() >= 0 for p in a.patches if hasattr(p, "get_height")):
                    a.set_ylim(bottom=0)
            if ylo is not None or yhi is not None:
                a.set_ylim(ylo, yhi)
            if xlo is not None or xhi is not None:
                a.set_xlim(xlo, xhi)
            for t in a.texts:
                if t.get_fontsize() and t not in (a.title,):
                    t.set_fontsize(S["size_annot"])
                t.set_visible(S["value_labels"] or getattr(t, "_keep", False))
        if yl and many and nrows > 1:     # several rows: one shared y label (row labels would overlap)
            self.fig.supylabel(yl, fontsize=S["size_label"])
        pos = S["legend_pos"]
        if pos == "auto":                 # inside a single graph, below several graphs
            pos = "inside" if not many else "below"
        handles, labels = [], []
        for a in axs:
            for h, l in zip(*a.get_legend_handles_labels()):
                if l not in labels and not l.startswith("_"):
                    handles.append(h); labels.append(l)
        fsl = S["size_legend"]
        if handles and pos != "none":
            frame = dict(frameon=S["legend_frame"], fontsize=fsl)
            if pos == "inside":
                for a in axs:
                    if a.get_legend_handles_labels()[0]:
                        if getattr(a, "name", "") == "polar":
                            a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), **frame)
                        else:
                            a.legend(ncols=max(S["legend_cols"], 1), **frame)
            elif pos == "right":
                self.fig.legend(handles, labels, loc="outside right upper", ncols=max(S["legend_cols"], 1), **frame)
            else:
                ncol = S["legend_cols"]
                if not ncol:
                    width_pt = self.fig.get_size_inches()[0] * 72
                    longest = max(len(l) for l in labels)
                    ncol = max(1, min(int(width_pt // (0.55 * fsl * longest + 36)), len(labels), 4))
                self.fig.legend(handles, labels, loc="outside lower center" if pos == "below" else "outside upper center",
                                ncols=ncol, **frame)
        t = self.title.text() or title
        if t and S["show_title"]:
            x, ha = {"left": (0.01, "left"), "center": (0.5, "center"), "right": (0.99, "right")}[S["title_align"]]
            self.fig.suptitle(t, fontsize=S["size_title"], fontweight="bold" if S["title_bold"] else "normal",
                              x=x, ha=ha)
        fam = [S["font"], "DejaVu Sans"]
        from matplotlib.text import Text
        for txt in self.fig.findobj(Text):
            txt.set_fontfamily(fam)

    # ------------------------------------------------------------------ drawing
    def on_theme(self):
        self._controls_changed()
        tb = self.toolbar                       # matplotlib picks dark or light icons from the background
        for _, _, image, callback in getattr(tb, "toolitems", ()):
            act = getattr(tb, "_actions", {}).get(callback)
            if act is not None and image:
                try:
                    act.setIcon(tb._icon(image + ".png"))
                except Exception:
                    pass

    def _set_cards(self, cards):
        """Summary cards under the graph: (label, value, line1, line2, warn) tuples; [] hides the row."""
        while self.cards_lay.count():
            w = self.cards_lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for label, value, l1, l2, warn in cards:
            fr = QFrame()
            fr.setObjectName("statCardWarn" if warn else "statCard")
            v = QVBoxLayout(fr)
            v.setContentsMargins(14, 10, 14, 12)
            v.setSpacing(2)
            a = QLabel(label)
            a.setObjectName("statWarn" if warn else "statLabel")
            a.setWordWrap(True)
            v.addWidget(a)
            if value:
                b = QLabel(value)
                b.setObjectName("statValue")
                v.addWidget(b)
            for txt in (l1, l2):
                if txt:
                    c = QLabel(txt)
                    c.setObjectName("statWarn" if warn else "statSub")
                    c.setWordWrap(True)
                    v.addWidget(c)
            v.addStretch(1)
            self.cards_lay.addWidget(fr, 1)
        self.cards.setVisible(bool(cards))

    def redraw(self):
        self.plotted = []
        if self.ptype.currentData() != "stats":
            self._set_cards([])
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
            import matplotlib
            self._axs = []
            with matplotlib.rc_context(rc(self.S)):
                {"series": self.plot_series, "perfile": self.plot_perfile, "change": self.plot_change,
                 "stats": self.plot_stats, "param": self.plot_param, "dist": self.plot_dist}[self.ptype.currentData()](files)
            self.drag.tag(self.fig, self._axs)
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
        return self.metric_cb.currentData(), self.metric_cb.currentText()

    def plot_series(self, files):
        col, mlabel = self._metric()
        no_of = lambda u: str(self.store.info[u]["file"]).split("_")[-1]
        per = self.store.per
        cols = self.colors(files)
        groups = self.groups(files)
        axs = self._axes(len(groups))
        xmode = self.xaxis.currentData()
        st = self.style_.currentData()
        S = self.S
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
                    if S["points"]:
                        a.plot(x, y, "o", ms=S["point_size"], alpha=S["point_alpha"], color=cols[u], label="_raw",
                               mec="none")
                if len(x) == 1:
                    a.errorbar(x, ys, yerr=self._err(df[col]), fmt="o", ms=S["marker_size"] * 1.6, color=cols[u],
                               capsize=S["capsize"], elinewidth=S["err_lw"], capthick=S["err_lw"], label=self.label(u))
                else:
                    a.plot(x, ys, fmt, ms=S["marker_size"], lw=S["line_width"], color=cols[u], label=self.label(u))
                    if S["fit"] != "none":
                        r = self._fit(a, x, y, cols[u], S, f"{no_of(u)}: ")
                        if r:
                            self.plotted.append(dict(panel=gname, file=u, fit=r["text"]))
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
        S = self.S
        for a, (gname, us) in zip(axs, groups):
            xs = np.arange(len(us))
            data = [per.loc[per.uid == u, col].dropna().to_numpy(float) for u in us]
            means = [d.mean() if d.size else np.nan for d in data]
            errs = [self._err(d) for d in data]
            edge = "#222222" if S["bar_edge"] else None
            if st == "bar":
                a.bar(xs, means, yerr=errs, **self._bar_kw(S, [cols[u] for u in us]))
            elif st == "box":
                bp = a.boxplot(data, positions=xs, widths=S["bar_width"] * 0.7, patch_artist=True, showfliers=False)
                for patch, u in zip(bp["boxes"], us):
                    patch.set_facecolor(mcolors.to_rgba(cols[u], S["bar_alpha"] * 0.45))
                    patch.set_edgecolor(edge or cols[u])
                for med in bp["medians"]:
                    med.set_color("#222")
            elif st == "violin":
                ok = [i for i, d in enumerate(data) if d.size > 1]
                if ok:
                    vp = a.violinplot([data[i] for i in ok], positions=xs[ok], widths=S["bar_width"] * 0.9,
                                      showmeans=True)
                    for body, i in zip(vp["bodies"], ok):
                        body.set_facecolor(cols[us[i]]); body.set_alpha(S["bar_alpha"] * 0.55)
                        if edge:
                            body.set_edgecolor(edge)
            else:
                for x, m, e, u in zip(xs, means, errs, us):
                    a.errorbar([x], [m], yerr=[e], fmt="o", ms=S["marker_size"] * 2, color=cols[u],
                               capsize=S["capsize"], elinewidth=S["err_lw"], capthick=S["err_lw"])
            if S["points"]:
                rng = np.random.default_rng(0)
                for x, d, u in zip(xs, data, us):
                    self._points(a, x, d, cols[u], S, rng)
            for u in us:
                a.bar([0], [0], width=0, color=cols[u], label=self.label(u))
            a.set_xticks(xs)
            a.set_xticklabels([str(self.store.info[u]["file"]).split("_")[-1] for u in us])
            a.set_title(gname)
            for u, d, m, e in zip(us, data, means, errs):
                self.plotted.append(dict(panel=gname, file=u, label=self.label(u), n_images=d.size, mean=m,
                                         error=e, error_type=self.err.currentText()))
        self._finish(axs, "file no.", mlabel, mlabel, bars=st == "bar")
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
        S = self.S
        for a, (gname, us) in zip(axs, groups):
            xs = np.arange(len(us))
            vals = [lookup[u][1] for u in us]
            kw = self._bar_kw(S, [cols[u] for u in us])
            kw.pop("error_kw")
            a.bar(xs, vals, **kw)
            a.axhline(0, color="#333", lw=0.8)
            a.set_xticks(xs)
            a.set_xticklabels([f"{lookup[u][0]}\n({str(info[u]['file']).split('_')[-1]})" for u in us])
            for x, v in zip(xs, vals):
                if np.isfinite(v):
                    a.annotate(f"{v:+.0f} %", (x, v), ha="center", va="bottom" if v >= 0 else "top",
                               fontsize=S["size_annot"], xytext=(0, 2 if v >= 0 else -2), textcoords="offset points")
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
            S = self.S
            for x, arm in enumerate(arms):
                v = gels.loc[gels["arm"] == arm, "value"].to_numpy(float)
                st = summ[summ["arm"] == arm].iloc[0]
                c = self.arm_color(arm) or "#555555"
                kw = self._bar_kw(S, c)
                kw.pop("error_kw")
                kw["alpha"] = S["bar_alpha"] * 0.45
                if not S["bar_edge"]:
                    kw.update(edgecolor=c, linewidth=1.0)
                a.bar(x, st["mean"], **kw)
                if np.isfinite(st["ci95_low"]):
                    a.errorbar(x, st["mean"], yerr=[[st["mean"] - st["ci95_low"]], [st["ci95_high"] - st["mean"]]],
                               fmt="none", ecolor="#333", capsize=S["capsize"], elinewidth=S["err_lw"],
                               capthick=S["err_lw"])
                if S["points"]:
                    self._points(a, x, v, c, S, rng)
                a.annotate(f"n = {int(st['n_gels'])}\n{GS.fmt_p(st['p_ttest_vs0'])}", (x, 1.0),
                           xycoords=("data", "axes fraction"), ha="center", va="top", fontsize=S["size_annot"],
                           color="#333")
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
            a.set_xticklabels([arm.replace(" (", "\n(") for arm in arms])
            lo, hi = a.get_ylim()
            a.set_ylim(lo, hi + 0.22 * (hi - lo))
            a.set_title(gname)
        ylab = (f"{mlabel.split(' (')[0]}: change (%)" if change == "pct"
                else f"Δ {mlabel} (after − before)")
        self._finish(axs, "", ylab, f"{mlabel.split(' (')[0]}: change by group", bars=True)
        cards = []
        for r in [x for x in self.plotted if x.get("row") == "comparison"][:3]:
            what = str(r["comparison"])
            head, _, tail = what.partition(" (")
            label = f"{head[:1].upper() + head[1:]} · {tail.rstrip(')')}" if tail else what
            if np.isfinite(r.get("p_welch", np.nan)):
                cards.append((label, f"{r['difference']:+.3g}{unit}",
                              f"95 % CI {r['diff_ci95_low']:.3g} to {r['diff_ci95_high']:.3g} · Welch "
                              + GS.fmt_p(r["p_welch"]),
                              f"Holm {GS.fmt_p(r['p_welch_holm'])} · g {r['hedges_g']:.2g} · "
                              f"{int(r['n_a'])} vs {int(r['n_b'])} gels", False))
            else:
                cards.append((label, f"{r['difference']:+.3g}{unit}", "Needs ≥ 2 gels in each group for a test",
                              f"{int(r['n_a'])} vs {int(r['n_b'])} gels", False))
        have = set(exp["arm"])
        if GS.TREATED in have and GS.SHAM not in have:
            cards.append(("Before you report this", "", "No sham gel is ticked: the field effect (NC + AMF vs "
                          "sham) cannot be tested.", "", True))
        elif any(r[1] and "Needs" in (r[2] or "") for r in cards):
            cards.append(("Before you report this", "", "Some groups have fewer than 2 gels. Add gels to test "
                          "those comparisons.", "", True))
        self._set_cards(cards)
        self.note.setText("Dots = gels (After files of a gel averaged), bars = mean ± 95 % CI. Above each bar: paired test of "
                          "the change against 0 (t-test). " + (" | ".join(texts[3:]) if len(texts) > 3 else
                                                               "Comparisons in the cards; all numbers in the CSV export."
                                                               if texts else
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
        S = self.S
        rng = np.random.default_rng(0)
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
                series.setdefault(cols[u], []).append((float(xv) * sc, d.mean(), self._err(d), u, d))
            for c, pts in series.items():
                pts.sort(key=lambda p: p[:2])
                x, y, e, us_, ds = zip(*pts)
                lab = self._legend_for_color(us_)
                if self.style_.currentData() == "points_lines" and len(pts) > 1:
                    a.plot(x, y, "-", color=c, lw=S["line_width"] * 0.8, alpha=0.8)
                if S["points"]:                 # every analysed image at its setting
                    for xi, d in zip(x, ds):
                        spread = S["jitter"] * (abs(xi) * 0.15 if self.logx.isChecked() else
                                                (max(x) - min(x) or 1) * 0.15)
                        self._points(a, xi, d, c, dict(S, jitter=spread), rng, zorder=2)
                a.errorbar(x, y, yerr=e, fmt="o", ms=S["marker_size"] * 1.5, color=c, capsize=S["capsize"],
                           elinewidth=S["err_lw"], capthick=S["err_lw"], label=lab, zorder=3)
                if S["fit"] != "none":
                    r = self._fit(a, x, y, c, S, "")
                    if r:
                        self.plotted.append(dict(panel=gname, group=lab, fit=r["text"]))
                for xi, yi, ei, u, _ in pts:
                    self.plotted.append(dict(panel=gname, file=u, x=xi, mean=yi, error=ei))
            if self.logx.isChecked():
                a.set_xscale("log")
            a.set_title(gname)
        self._finish(axs, xlab, mlabel, f"{mlabel} vs {xlab.split(' (')[0]}")
        self.note.setText(("Not shown (no value in the file name): " + ", ".join(missing[:4]) +
                           ("…" if len(missing) > 4 else "") + ". ") if missing else
                          "Each point = one file (mean over its images ± error). Colors as chosen under ‘Colors’.")
        if S["fit"] != "none":
            self.note.setText(self.note.text() + " Fit lines: fitted to the file means, one per colour (equation in "
                              "the legend and the CSV export).")

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
        if key == "arm":
            return str(r.get("arm") or "")
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
                    a.step(np.r_[0, x], np.r_[0, y], where="post", color=cols[u], lw=self.S["line_width"], label=lab)
                elif which == "lt":
                    x, y = core.lt_curve(h)
                    a.plot(x, y, "o-", ms=self.S["marker_size"] * 0.65, color=cols[u], lw=self.S["line_width"], label=lab)
                else:
                    fine = h[:-1]
                    idx = np.searchsorted(core.SEG_EDGES[:-1], core.SEG_PLOT_EDGES[:-1], side="left")
                    hh = np.add.reduceat(fine, idx)
                    x = (core.SEG_PLOT_EDGES[:-1] + core.SEG_PLOT_EDGES[1:]) / 2
                    y = 100 * hh / tot / np.diff(core.SEG_PLOT_EDGES)
                    a.plot(x, y, "o-", ms=self.S["marker_size"] * 0.65, color=cols[u], lw=self.S["line_width"], label=lab)
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
            style.theme_figure(self.fig, night=False)     # saved figures: white paper, dark text
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
