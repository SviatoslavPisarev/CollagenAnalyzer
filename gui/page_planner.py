"""Experiment Planner: model how the AMF/NC settings change a metric and suggest the next experiments."""
import glob
import os
import re

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHeaderView, QLabel, QMessageBox, QScrollArea, QSpinBox, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from ml import planner as PL
import nc_physics as NP
from .page_plots import METRICS
from .widgets import Page, button, hint, banner, hbox
from .widgets import FigurePanel
from . import style

GOALS = [("order_S", "Alignment — order parameter S"), ("file_order_S_pooled", "Alignment — S of all fiber pixels pooled"),
         ("coherency", "Coherency")] + [m for m in METRICS if m[0] not in ("order_S", "coherency", "mean_angle_deg",
                                                                            "threshold", "mean_reflectance")
                                        and "thr_" not in m[0] and "excluded" not in m[0]]
# ranges offered when there is no data yet (shown units)
FALLBACK = {"amf_freq_hz": (50, 1800), "amf_time_min": (1, 10), "amf_vpp_set": (1, 20), "amf_v_measured": (1, 10),
            "nc_conc_nM": (0.1, 5), "premixed": (0, 1), "inc_min": (15, 120), "collagen_mg_ml": (2, 4),
            "amf_app_set": (0.5, 5), "amf_coil_turns": (200, 500), "amf_b_peak_mT": (5, 50),
            "amf_ampere_turns": (100, 2500)}
SHORT = {"amf_freq_hz": ("AMF frequency (Hz)", 0), "amf_time_min": ("AMF time (min)", 1),
         "amf_vpp_set": ("Vp-p set (V)", 1), "amf_v_measured": ("Measured V (V)", 2),
         "nc_conc_nM": ("NC conc. (nM)", 2), "premixed": ("Premixed (1) / inc. (0)", 0),
         "inc_min": ("Incubation (min)", 0), "collagen_mg_ml": ("Collagen (mg/mL)", 1),
         "amf_app_set": ("Ap-p set (A)", 2), "amf_coil_turns": ("Coil (turns)", 0),
         "amf_b_peak_mT": ("B field, peak (mT)", 1), "amf_ampere_turns": ("Ap-p × turns", 0)}
SUMMARY_RE = re.compile(r"^((\d{6}|other)(-\d{6})?_summary|summary)\.xlsx$")


def read_per_file(path):
    pf = pd.read_excel(path, sheet_name="per file")
    root = os.path.dirname(os.path.abspath(path))
    if "results_folder" in pf:
        pf["_src"] = [os.path.normpath(os.path.join(root, str(r))) for r in pf["results_folder"]]
    else:
        pf["_src"] = root
    pf["file"] = pf["file"].astype(str)
    pf["sample"] = pf["sample"].astype(str)
    pf["date"] = pf["date"].astype(str)
    return pf


class PlannerPage(Page):
    def __init__(self, parent=None):
        super().__init__("Experiment planner", "Learns how the AMF and nanocage settings change a structure metric "
                         "(after vs before AMF, per sample) and suggests which conditions to test next.",
                         scroll=False)
        self.pf = pd.DataFrame()
        self.sources = []
        self.exp = pd.DataFrame()
        self.model = None
        self.plan = pd.DataFrame()
        self.best = None
        try:
            import sklearn  # noqa: F401
            self.have_sklearn = True
        except ImportError:
            self.have_sklearn = False

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
        self.src_label = hint("Nothing loaded. Results of a run are added automatically.")
        gl.addWidget(self.src_label)
        self.excl_qc = QCheckBox("Leave out files the quality check marked ‘check’")
        self.excl_qc.setChecked(True)
        self.excl_qc.toggled.connect(self.rebuild)
        gl.addWidget(self.excl_qc)
        self.sub_sham = QCheckBox("Subtract the change of sham gels (same date) from each experiment")
        self.sub_sham.setChecked(True)
        self.sub_sham.setToolTip("Sham gels (‘sham’ in the file name: same handling and wait, no field) show how "
                                 "much a gel changes without AMF (drying, re-imaging). With this on, the model "
                                 "learns the AMF effect beyond that drift. Sham gels are never experiments.")
        self.sub_sham.toggled.connect(self.rebuild)
        gl.addWidget(self.sub_sham)
        cl.addWidget(g)

        g = QGroupBox("2. Goal")
        f = QFormLayout(g)
        self.metric_cb = QComboBox()
        for k, lab in GOALS:
            self.metric_cb.addItem(lab, k)
        self.change = QComboBox()
        self.change.addItem("after − before", "diff")
        self.change.addItem("% change after AMF", "pct")
        self.direction = QComboBox()
        self.direction.addItem("as large as possible", "increase")
        self.direction.addItem("as small as possible", "decrease")
        for w in (self.metric_cb, self.change):
            w.currentIndexChanged.connect(self.rebuild)
        f.addRow("Metric", self.metric_cb)
        f.addRow("Response", self.change)
        f.addRow("Make it", self.direction)
        cl.addWidget(g)

        g = QGroupBox("3. Settings to optimise")
        gl = QVBoxLayout(g)
        self.vt = QTableWidget(len(PL.VARIABLES), 7)
        self.vt.setHorizontalHeaderLabels(["Use", "Setting", "In data", "From", "To", "Step", "Log"])
        self.vt.verticalHeader().setVisible(False)
        self.vt.setSelectionMode(QAbstractItemView.SingleSelection)
        self.vt.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.vrow = {}
        for i, (key, _, label, unit, *_r) in enumerate(PL.VARIABLES):
            self.vrow[key] = i
            use, lg = QCheckBox(), QCheckBox()
            self.vt.setCellWidget(i, 0, _centered(use))
            it = QTableWidgetItem(SHORT[key][0])
            it.setToolTip(label + (f" ({unit})" if unit else ""))
            it.setFlags(Qt.ItemIsEnabled)
            self.vt.setItem(i, 1, it)
            it = QTableWidgetItem("—")
            it.setFlags(Qt.ItemIsEnabled)
            self.vt.setItem(i, 2, it)
            lo, hi = FALLBACK[key]
            for c, v in ((3, lo), (4, hi), (5, PL.VAR[key][6])):
                it = QTableWidgetItem(_fmt(v))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setToolTip("Double-click to change")
                self.vt.setItem(i, c, it)
            self.vt.setRowHeight(i, 34)
            self.vt.setCellWidget(i, 6, _centered(lg))
            lg.setChecked(PL.VAR[key][5])
            if key == "premixed":
                lg.setEnabled(False)
                lg.setChecked(False)
        for c, wd in enumerate((38, 140, 108, 64, 64, 54, 40)):
            self.vt.setColumnWidth(c, wd)
        self.vt.horizontalHeader().setStretchLastSection(True)
        self.vt.setMinimumHeight(330)
        gl.addWidget(self.vt)
        gl.addWidget(hint("Tick the settings that vary between your experiments. ‘From’–‘To’ is the range the "
                          "planner may suggest from — widen it to the instrument limits to explore (e.g. the coil's "
                          "highest frequency). Suggestions are rounded to ‘Step’, or limited to a list of values "
                          "such as ‘200, 500’ (the two coils). ‘Log’ treats equal ratios as equal "
                          "steps (good for frequency and concentration)."))
        cl.addWidget(g)

        g = QGroupBox("4. Plan")
        gl = QVBoxLayout(g)
        self.n_sugg = QSpinBox()
        self.n_sugg.setRange(1, 24)
        self.n_sugg.setValue(4)
        gl.addWidget(hbox(QLabel("Experiments to suggest"), self.n_sugg))
        self.fit_btn = button("Fit model and suggest", "primary", slot=self.fit_and_suggest)
        self.fit_btn.setEnabled(self.have_sklearn)
        gl.addWidget(hbox(self.fit_btn, button("Starting design (no data needed)", slot=self.starting_design,
                                                tip="Space-filling first experiments (Latin hypercube) across the "
                                                    "ranges — for when there are fewer than about 6 experiments")))
        gl.addWidget(hbox(button("Export plan (CSV)…", slot=self.export),
                          button("Save all graphs…", slot=self.save_all,
                                 tip="Response map, effects and model check as PNG and PDF in one folder"),
                          button("Copy file names", "link", slot=self.copy_names)))
        if not self.have_sklearn:
            gl.addWidget(banner("scikit-learn is not installed (pip install scikit-learn): only the starting design "
                                "is available."))
        self.status = hint("")
        gl.addWidget(self.status)
        cl.addWidget(g)

        g = QGroupBox("5. Nanocage physics (estimate)")
        f = QFormLayout(g)
        self.phys = {}
        for key, lab, lo, hi, val, dec, suf, tip in [
                ("dh", "Hydrodynamic diameter", 1, 2000, 30, 1, " nm", "From DLS: core + coating + bound water"),
                ("core", "Magnetic core / cage diameter", 1, 500, 20, 1, " nm", "Outer diameter of the iron oxide"),
                ("shell", "Cage shell thickness (0 = solid)", 0, 250, 0, 1, " nm",
                 "For a hollow nanocage: thickness of the iron oxide shell (smaller magnetic volume)"),
                ("eta", "Local viscosity", 0.1, 1e5, 0.89, 2, " mPa·s", "Water at 25 °C = 0.89; 37 °C = 0.69"),
                ("T", "Temperature", 270, 330, 298.15, 1, " K", ""),
                ("K", "Anisotropy constant K", 1e2, 1e6, 1.1e4, 0, " J/m³", "Bulk magnetite ≈ 1.1×10⁴ J/m³; "
                 "nanoparticles often higher (surface anisotropy)"),
                ("freq", "AMF frequency", 1, 1e6, 500, 0, " Hz", ""),
                ("field", "Field amplitude (peak)", 0.01, 1000, 40, 2, " mT", "")]:
            w = QDoubleSpinBox()
            w.setRange(lo, hi); w.setDecimals(dec); w.setValue(val); w.setSuffix(suf)
            w.setToolTip(tip)
            w.valueChanged.connect(self.update_physics)
            self.phys[key] = w
            f.addRow(lab, w)
        self.phys_out = hint("")
        f.addRow(self.phys_out)
        f.addRow(hint("Brownian = the particle turns in the liquid (this moves what it is bound to); Néel = the "
                      "moment flips inside the particle. Below the loss peak a free particle turns nearly in phase "
                      "with the field. Textbook estimate (Rosensweig 2002); see nc_physics.py."))
        cl.addWidget(g)
        self.update_physics()
        cl.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(ctrl)
        sa.setMinimumWidth(590)
        split.addWidget(sa)

        # ================================================================ results
        self.tabs = QTabWidget()
        # plan
        w = QWidget()
        l = QVBoxLayout(w)
        self.best_label = QLabel("Load results and click ‘Fit model and suggest’, or make a starting design.")
        self.best_label.setWordWrap(True)
        self.best_label.setTextFormat(Qt.RichText)
        self.best_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.best_label)
        self.plan_table = QTableWidget(0, 0)
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setAlternatingRowColors(True)
        self.plan_table.horizontalHeader().setStretchLastSection(True)
        l.addWidget(self.plan_table, 1)
        l.addWidget(hint("Predicted = the model's expected response ± 1 SD. The file names follow the app's naming "
                         "scheme; fill in date, sample and file number, and for a measured voltage use the value you "
                         "actually measure. Settings that are not optimised keep their most common value in your data."))
        self.tabs.addTab(w, "Plan")
        # map
        w = QWidget()
        l = QVBoxLayout(w)
        self.map_x, self.map_y, self.map_show = QComboBox(), QComboBox(), QComboBox()
        for k, lab in (("both", "prediction and uncertainty"), ("mean", "prediction only"),
                       ("sd", "uncertainty only")):
            self.map_show.addItem(lab, k)
        for c in (self.map_x, self.map_y, self.map_show):
            c.currentIndexChanged.connect(self.draw_map)
        self.map_fig = Figure(figsize=(8, 4.5), layout="constrained")
        self.map_panel = FigurePanel(self.map_fig, name=lambda: "response_map_" + "_vs_".join(
            str(c.currentData()) for c in (self.map_y, self.map_x)),
            controls=[QLabel("x"), self.map_x, QLabel("y"), self.map_y, QLabel("Show"), self.map_show])
        l.addWidget(self.map_panel, 1)
        l.addWidget(hint("Predicted response and its uncertainty (SD). Other settings are held at the best "
                         "predicted values. Dots = experiments done, stars = suggestions. Double-click the map or "
                         "use Full screen to see it large; Save… writes a white-background PNG, PDF, SVG or TIFF."))
        self.tabs.addTab(w, "Response map")
        # effects
        w = QWidget()
        l = QVBoxLayout(w)
        self.eff_fig = Figure(figsize=(8, 5), layout="constrained")
        self.eff_panel = FigurePanel(self.eff_fig, name="planner_effects")
        l.addWidget(self.eff_panel, 1)
        l.addWidget(hint("Each panel: predicted response (± 2 SD band) when one setting changes and the others stay "
                         "at the best predicted values; dots are all experiments (their other settings may differ). Relevance = how quickly the response changes with that "
                         "setting (1 / fitted length scale)."))
        self.tabs.addTab(w, "Effects")
        # model check
        w = QWidget()
        l = QVBoxLayout(w)
        self.chk_label = hint("")
        l.addWidget(self.chk_label)
        self.chk_fig = Figure(figsize=(6, 5), layout="constrained")
        self.chk_panel = FigurePanel(self.chk_fig, name="planner_model_check")
        l.addWidget(self.chk_panel, 1)
        self.tabs.addTab(w, "Model check")
        # data
        self.data_table = QTableWidget(0, 0)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabs.addTab(self.data_table, "Experiments")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([560, 760])

    # ------------------------------------------------------------------ data
    def load_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Load result spreadsheets", start, "Result spreadsheets (*.xlsx)")
        if paths:
            self.load_paths(paths)

    def load_folder_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "Folder with results (searched including subfolders)", start)
        if not d:
            return
        found = [p for p in glob.glob(os.path.join(d, "**", "*.xlsx"), recursive=True)
                 if SUMMARY_RE.match(os.path.basename(p))]
        if not found:
            QMessageBox.information(self, "Load results", "No mmddyy_summary.xlsx or summary.xlsx found.")
            return
        self.load_paths(found)

    def load_paths(self, paths):
        parts, errs = ([self.pf] if len(self.pf) else []), []
        for p in paths:
            p = os.path.abspath(p)
            try:
                parts.append(read_per_file(p))
                if p not in self.sources:
                    self.sources.append(p)
            except Exception as e:
                errs.append(f"{os.path.basename(p)}: {e}")
        if parts:
            pf = pd.concat(parts, ignore_index=True, sort=False)
            self.pf = pf.drop_duplicates(subset=["file"], keep="last").reset_index(drop=True)
        if errs:
            QMessageBox.warning(self, "Some spreadsheets were not loaded", "\n".join(errs[:10]))
        self.rebuild(set_ranges=True)

    def clear(self):
        self.pf, self.sources, self.model, self.plan, self.best = pd.DataFrame(), [], None, pd.DataFrame(), None
        self.rebuild(set_ranges=True)

    def rebuild(self, *_, set_ranges=False):
        """Pair the files into experiments for the chosen metric; update the 'In data' column."""
        notes = []
        if len(self.pf):
            self.exp, notes = PL.build_experiments(self.pf, self.metric_cb.currentData(), self.change.currentData(),
                                                   self.excl_qc.isChecked(),
                                                   "subtract" if self.sub_sham.isChecked() else "none")
        else:
            self.exp = pd.DataFrame()
        desc = PL.describe_variables(self.exp) if len(self.exp) else {}
        for key, i in self.vrow.items():
            d = desc.get(key)
            if d and d["n"]:
                txt = (f"{_fmt(d['lo'])}–{_fmt(d['hi'])} ({d['distinct']})" if d["distinct"] > 1
                       else f"{_fmt(d['lo'])} (fixed)")
            else:
                txt = "—"
            self.vt.item(i, 2).setText(txt)
            if set_ranges and d and d["n"]:
                use = d["distinct"] > 1
                self._use(key).setChecked(use)
                if use:
                    self._set(key, 3, d["lo"])
                    self._set(key, 4, d["hi"])
                if key == "nc_conc_nM" and d["lo"] <= 0:
                    self._cell(key, 6, check=True).setChecked(False)
        n_s = self.exp["sample"].nunique() if len(self.exp) else 0
        self.src_label.setText(
            (f"{len(self.pf)} file(s) from {len(self.sources)} spreadsheet(s) → {len(self.exp)} experiment(s) "
             f"(After-AMF files with a Before-AMF file) in {n_s} sample(s)." if len(self.pf) else
             "Nothing loaded. Results of a run are added automatically.") + ("\n" + "; ".join(notes) if notes else ""))
        self._fill_data()

    def update_physics(self, *_):
        v = {k: w.value() for k, w in self.phys.items()}
        try:
            r = NP.relaxation(v["dh"], v["core"], v["shell"] or None, v["eta"], v["T"], v["K"], 1e-9,
                              NP.MAGNETITE["Ms"], v["freq"], v["field"])
            self.phys_out.setText("\n".join(NP.describe(r)))
        except (ValueError, OverflowError, ZeroDivisionError) as e:
            self.phys_out.setText(f"Cannot compute: {e}")

    def _use(self, key):
        return self.vt.cellWidget(self.vrow[key], 0).findChild(QCheckBox)

    def _cell(self, key, col, check=False):
        w = self.vt.cellWidget(self.vrow[key], col)
        return w.findChild(QCheckBox) if check else w

    def _set(self, key, col, v):
        self.vt.item(self.vrow[key], col).setText(_fmt(v))

    def _num(self, key, col):
        txt = self.vt.item(self.vrow[key], col).text().strip().replace(",", ".")
        try:
            return float(txt or 0)
        except ValueError:
            raise ValueError(f"{SHORT[key][0]}: ‘{txt}’ is not a number (section 3)")

    def _step(self, key):
        """Step, or a list of allowed values ("200, 500")."""
        txt = self.vt.item(self.vrow[key], 5).text().strip()
        if ";" in txt or txt.count(",") >= 1 and not _is_num(txt.replace(",", ".")):
            try:
                return 0.0, sorted(float(v) for v in txt.replace(";", ",").split(",") if v.strip())
            except ValueError:
                raise ValueError(f"{SHORT[key][0]}: ‘{txt}’ — give a step or allowed values like ‘200, 500’")
        return self._num(key, 5), None

    def space(self):
        vars_ = []
        for key in self.vrow:
            if self._use(key).isChecked():
                step, levels = self._step(key)
                lo, hi = self._num(key, 3), self._num(key, 4)
                if levels:
                    lo, hi = min(levels + [lo]), max(levels + [hi])
                vars_.append(dict(key=key, lo=lo, hi=hi, step=step, levels=levels,
                                  log=self._cell(key, 6, check=True).isChecked()))
        if not vars_:
            raise ValueError("Tick at least one setting to optimise (section 3).")
        return PL.Space(vars_)

    def _unit(self):
        lab = self.metric_cb.currentText().split(" — ")[-1]
        return f"% change of {lab}" if self.change.currentData() == "pct" else f"Δ {lab}"

    # ------------------------------------------------------------------ actions
    def fit_and_suggest(self):
        try:
            sp = self.space()
        except ValueError as e:
            QMessageBox.warning(self, "Planner", str(e))
            return
        if not len(self.exp):
            QMessageBox.information(self, "Planner", "No experiments yet: load result spreadsheets with before and "
                                    "after AMF files, or make a starting design.")
            return
        d = self.exp.dropna(subset=sp.keys + ["y"])
        dropped = len(self.exp) - len(d)
        if len(d) < 3:
            QMessageBox.information(self, "Planner", f"Only {len(d)} experiment(s) have all the ticked settings — "
                                    "need at least 3 (better 6+). Make a starting design first.")
            return
        X = d[sp.keys].to_numpy(float)
        out = [(k, X[:, j]) for j, k in enumerate(sp.keys) if (X[:, j] < sp.vars[j]["lo"]).any()
               or (X[:, j] > sp.vars[j]["hi"]).any()]
        if out:
            QMessageBox.warning(self, "Planner", "Some experiments lie outside the From–To range of: "
                                + ", ".join(PL.VAR[k][2] for k, _ in out) + ". Widen the range.")
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.model = PL.Model(sp, X, d["y"].to_numpy(float))
            self.model_exp = d
            self.plan, self.best = self.model.suggest(self.n_sugg.value(), self.direction.currentData())
        except Exception as e:
            QMessageBox.warning(self, "Planner", f"The model could not be fitted:\n{e}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        b = self.best
        unit = self._unit()
        bs = ", ".join(f"{PL.VAR[k][2]} {_fmt(b[k])} {PL.VAR[k][3]}".strip() for k in sp.keys)
        few = (" <b>Few experiments:</b> the map is rough; the suggestions mostly explore." if len(d) < 8 else "")
        self.best_label.setText(
            f"<b>Best predicted:</b> {bs} → {unit} = {b['mean']:.3g} ± {b['sd']:.2g}.<br>"
            f"Model of {len(d)} experiments ({sp.keys.__len__()} setting(s)); measurement noise ≈ "
            f"{self.model.noise_sd():.2g}.{few}"
            + (f" {dropped} experiment(s) without all the ticked settings left out." if dropped else ""))
        self.status.setText("Suggestions balance ‘probably good’ against ‘not yet explored’ (expected improvement). "
                            "Run them, analyse, and fit again.")
        self._fill_plan(sp, predicted=True)
        self._fill_map_choices(sp)
        self.draw_effects()
        self.draw_check()
        self.tabs.setCurrentIndex(0)

    def starting_design(self):
        try:
            sp = self.space()
        except ValueError as e:
            QMessageBox.warning(self, "Planner", str(e))
            return
        n = max(self.n_sugg.value(), 2 * len(sp.vars) + 2)
        self.plan = PL.starting_design(sp, n)
        self.best, self.model = None, None
        self.best_label.setText(f"<b>Starting design:</b> {n} experiments spread evenly over the ranges (Latin "
                                f"hypercube; at least 2 × settings + 2). Run them, then fit the model.")
        self._fill_plan(sp, predicted=False)
        self.map_fig.clear(); self.map_fig.canvas.draw_idle()
        self.tabs.setCurrentIndex(0)

    def _fixed_values(self, sp):
        others = [k for k in PL.VAR if k not in sp.keys]
        fx = PL.typical_values(self.exp, others) if len(self.exp) else {}
        return {k: v for k, v in fx.items() if np.isfinite(v)}

    def _fill_plan(self, sp, predicted):
        fixed = self._fixed_values(sp)
        cols = [f"{PL.VAR[k][2]} ({PL.VAR[k][3]})" if PL.VAR[k][3] else PL.VAR[k][2] for k in sp.keys]
        cols = ["#"] + cols + (["Predicted"] if predicted else []) + ["File name"]
        self.plan["file_name"] = [PL.file_name_template(r, fixed) for r in self.plan.to_dict("records")]
        t = self.plan_table
        t.clear()
        t.setColumnCount(len(cols))
        t.setRowCount(len(self.plan))
        t.setHorizontalHeaderLabels(cols)
        for i, r in enumerate(self.plan.to_dict("records")):
            vals = [str(i + 1)] + [_fmt(r[k]) for k in sp.keys]
            if predicted:
                vals.append(f"{r['predicted']:.3g} ± {r['predicted_sd']:.2g}")
            vals.append(r["file_name"])
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                t.setItem(i, c, it)
        t.resizeColumnsToContents()

    def export(self):
        if not len(self.plan):
            QMessageBox.information(self, "Export", "Make a plan first.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export plan", "experiment_plan.csv", "CSV (*.csv)")
        if p:
            out = self.plan.copy()
            out.insert(0, "goal", f"{self._unit()}, {self.direction.currentText()}")
            out.to_csv(p, index=False)

    def save_all(self):
        figs = [(f, n) for f, n in ((self.map_fig, "response_map"), (self.eff_fig, "effects"),
                                    (self.chk_fig, "model_check")) if f.axes]
        if not figs:
            QMessageBox.information(self, "Save graphs", "Fit the model first.")
            return
        start = QSettings().value("plots/save_dir", QSettings().value("plots/last_dir", os.path.expanduser("~")))
        d = QFileDialog.getExistingDirectory(self, "Folder for the planner graphs", start)
        if not d:
            return
        QSettings().setValue("plots/save_dir", d)
        saved = []
        try:
            for fig, n in figs:
                style.theme_figure(fig, night=False)
                for ext in ("png", "pdf"):
                    p = os.path.join(d, f"planner_{n}.{ext}")
                    fig.savefig(p, dpi=300, bbox_inches="tight", facecolor="white")
                    saved.append(os.path.basename(p))
        except Exception as e:
            QMessageBox.warning(self, "Save graphs", f"Could not save:\n{e}")
        finally:
            for fig, _ in figs:
                fig.canvas.draw_idle()
        if saved:
            self.status.setText(f"Saved {len(saved)} file(s) to {d}: " + ", ".join(saved))

    def copy_names(self):
        if len(self.plan):
            QGuiApplication.clipboard().setText("\n".join(self.plan["file_name"]))
            self.status.setText("File names copied to the clipboard.")

    # ------------------------------------------------------------------ plots
    def _fill_map_choices(self, sp):
        for c in (self.map_x, self.map_y):
            c.blockSignals(True)
            c.clear()
            for k in sp.keys:
                c.addItem(PL.VAR[k][2], k)
            c.blockSignals(False)
        self.map_x.setCurrentIndex(0)
        self.map_y.setCurrentIndex(1 if len(sp.keys) > 1 else 0)
        self.draw_map()

    def _base(self):
        sp = self.model.space
        return np.array([self.best[k] for k in sp.keys], float)

    def draw_map(self, *_):
        fig = self.map_fig
        fig.clear()
        if self.model is None:
            fig.canvas.draw_idle()
            return
        sp, m = self.model.space, self.model
        kx, ky = self.map_x.currentData(), self.map_y.currentData()
        if kx is None:
            return
        jx, jy = sp.keys.index(kx), sp.keys.index(ky)
        base = self._base()
        unit = self._unit()
        X0 = m.X
        if jx == jy:                                   # one setting: a line with its band
            ax = fig.add_subplot(111)
            self._line(ax, jx, base, unit)
            fig.canvas.draw_idle()
            return
        g = np.linspace(0, 1, 70)
        UU = np.tile(sp.to_unit(base[None, :]), (70 * 70, 1))
        gx, gy = np.meshgrid(g, g)
        UU[:, jx], UU[:, jy] = gx.ravel(), gy.ravel()
        mu, sd = m.predict(U=UU)
        XX = sp.from_unit(UU)
        xs, ys = XX[:, jx].reshape(70, 70), XX[:, jy].reshape(70, 70)
        show = self.map_show.currentData() or "both"
        panels = [p for p in ((mu, unit, "viridis"), (sd, "uncertainty (SD)", "magma"))
                  if show == "both" or (show == "mean") == (p[2] == "viridis")]
        for k, (z, title, cmap) in enumerate(panels):
            ax = fig.add_subplot(1, len(panels), k + 1)
            cs = ax.contourf(xs, ys, z.reshape(70, 70), 18, cmap=cmap)
            fig.colorbar(cs, ax=ax, shrink=0.85, label=title)
            ax.scatter(X0[:, jx], X0[:, jy], c="white", edgecolors="black", s=28, zorder=3, label="done")
            if len(self.plan):
                ax.scatter(self.plan[kx], self.plan[ky], marker="*", s=160, c=style.AFTER, edgecolors="black",
                           zorder=4, label="suggested")
            for axis, j in (("x", jx), ("y", jy)):
                if sp.vars[j]["log"]:
                    getattr(ax, f"set_{axis}scale")("log")
                    _plain(getattr(ax, f"{axis}axis"))
            ax.set_xlabel(_axis_label(kx))
            ax.set_ylabel(_axis_label(ky))
            ax.set_title(title, fontsize=10)
            if k == 0:
                ax.legend(fontsize=8, loc="best")
        fig.canvas.draw_idle()

    def _line(self, ax, j, base, unit):
        sp, m = self.model.space, self.model
        g = np.linspace(0, 1, 120)
        UU = np.tile(sp.to_unit(base[None, :]), (len(g), 1))
        UU[:, j] = g
        mu, sd = m.predict(U=UU)
        x = sp.from_unit(UU)[:, j]
        ax.fill_between(x, mu - 2 * sd, mu + 2 * sd, color=style.ACCENT, alpha=0.18, lw=0)
        ax.plot(x, mu, color=style.ACCENT, lw=2)
        ax.scatter(m.X[:, j], m.y, s=16, c="black", zorder=3)
        if sp.vars[j]["log"]:
            ax.set_xscale("log")
            _plain(ax.xaxis)
        ax.set_xlabel(_axis_label(sp.keys[j]))
        ax.set_ylabel(unit, fontsize=8)

    def draw_effects(self):
        fig = self.eff_fig
        fig.clear()
        if self.model is None:
            fig.canvas.draw_idle()
            return
        sp = self.model.space
        n = len(sp.keys)
        cols = min(3, n + 1)
        rows = int(np.ceil((n + 1) / cols))
        base = self._base()
        for j in range(n):
            self._line(fig.add_subplot(rows, cols, j + 1), j, base, self._unit())
        ax = fig.add_subplot(rows, cols, n + 1)
        rel = self.model.relevance()
        ax.barh([PL.VAR[k][2] for k in sp.keys], rel, color=style.ACCENT)
        ax.set_xlabel("relevance (share)")
        ax.invert_yaxis()
        ax.tick_params(axis="y", labelsize=8)
        fig.canvas.draw_idle()

    def draw_check(self):
        fig = self.chk_fig
        fig.clear()
        m = self.model
        lm, ls = m.loo()
        y = m.y
        ok = np.isfinite(lm)
        r2 = 1 - np.sum((lm[ok] - y[ok]) ** 2) / np.sum((y[ok] - y[ok].mean()) ** 2) if ok.sum() > 2 else np.nan
        rmse = float(np.sqrt(np.mean((lm[ok] - y[ok]) ** 2))) if ok.any() else np.nan
        ax = fig.add_subplot(111)
        ax.errorbar(y[ok], lm[ok], yerr=ls[ok], fmt="o", color=style.ACCENT, ecolor="#9cbfc7", ms=5)
        lo, hi = np.nanmin(np.r_[y, lm]), np.nanmax(np.r_[y, lm])
        ax.plot([lo, hi], [lo, hi], "--", color="#888")
        ax.set_xlabel(f"measured {self._unit()}")
        ax.set_ylabel("predicted without that experiment")
        verdict = ("the model predicts new experiments well" if r2 >= 0.6 else
                   "the model captures part of the trend" if r2 >= 0.2 else
                   "the model cannot yet predict new experiments — mostly noise or too few points; trust the "
                   "uncertainty, not the map")
        self.chk_label.setText(f"Leave-one-out check: each experiment predicted by a model fitted without it. "
                               f"R² = {r2:.2f}, RMSE = {rmse:.3g} → {verdict}.")
        fig.canvas.draw_idle()

    def _fill_data(self):
        t = self.data_table
        t.clear()
        if not len(self.exp):
            t.setRowCount(0)
            t.setColumnCount(0)
            return
        keys = [k for k in PL.VAR if self.exp[k].notna().any()]
        cols = ["file", "sample", "arm", "before", "after", "y"] + keys
        heads = ["After-AMF file", "Sample", "Group", "Before", "After",
                 self._unit() + (" − sham" if self.sub_sham.isChecked() else "")] + [_axis_label(k) for k in keys]
        t.setColumnCount(len(cols))
        t.setRowCount(len(self.exp))
        t.setHorizontalHeaderLabels(heads)
        for i, r in enumerate(self.exp[cols].to_dict("records")):
            for c, k in enumerate(cols):
                v = r[k]
                t.setItem(i, c, QTableWidgetItem(v if isinstance(v, str) else _fmt(v)))
        t.resizeColumnsToContents()
        t.setColumnWidth(0, min(t.columnWidth(0), 320))


def _is_num(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def _plain(axis):
    """Plain numbers (100, 1000, 0.5) instead of 10^n on a log axis."""
    from matplotlib.ticker import FuncFormatter, NullFormatter
    axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    lo, hi = sorted(abs(v) for v in axis.get_view_interval())
    if lo > 0 and hi / lo < 30:                # short range: label the 2× and 5× ticks too
        axis.set_minor_formatter(FuncFormatter(lambda v, _: f"{v:g}" if f"{v:e}"[0] in "25" else ""))
    else:
        axis.set_minor_formatter(NullFormatter())


def _centered(w):
    box = QWidget()
    l = QVBoxLayout(box)
    l.setContentsMargins(0, 0, 0, 0)
    l.addWidget(w, 0, Qt.AlignCenter)
    return box


def _fmt(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not np.isfinite(v):
        return ""
    return f"{v:.4g}"


def _axis_label(k):
    _, _, label, unit, *_ = PL.VAR[k]
    return f"{label} ({unit})" if unit else label
