"""Page 2: analysis parameters, presets, and a live preview of one image."""
import argparse
import json
import os
import traceback

import numpy as np
from PySide6.QtCore import Qt, Signal, QThread, QSettings, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QRadioButton,
                               QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget, QSizePolicy)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

import collagen_core as core
from ml import fibermodel as FM
from .common import DEFAULTS, NUMERIC, GROUPS
from .widgets import Page, Card, button, hint, hbox
from . import style


class Task(QThread):
    """Run a function in the background; emits ok(result) or failed(message)."""
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.ok.emit(self.fn(*self.args))
        except SystemExit as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")


def _spin(key):
    label, unit, lo, hi, step, dec, tip = NUMERIC[key]
    w = QSpinBox() if dec == 0 else QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    if dec:
        w.setDecimals(dec)
    if unit:
        w.setSuffix(f"  {unit}")
    w.setToolTip(tip)
    w.setMinimumWidth(110)
    w.setMaximumWidth(170)
    w.setValue(DEFAULTS[key])
    return w


class ParamsPage(Page):
    changed = Signal()

    def __init__(self, data_page, parent=None):
        super().__init__("2 · Parameters", "Defaults suit 60×/1.4 oil reflectance stacks of 2–4 mg/mL collagen. "
                         "Hover any field for an explanation. Use the preview on the right to check the fiber "
                         "mask before a long run.", scroll=False)
        self.data_page = data_page
        self.w = {}
        self._ready = False
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.lay.addWidget(split, 1)

        # ================================================================ left: settings
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(10)
        ll.addWidget(hbox(button("Reset to defaults", slot=self.reset),
                          button("Save preset…", slot=self.save_preset),
                          button("Load preset…", slot=self.load_preset)))

        # --- threshold
        g = QGroupBox("Fiber threshold (which pixels count as fiber)")
        gl = QVBoxLayout(g)
        self.thr_group = QButtonGroup(self)
        self.thr_per = QRadioButton("Automatic (Otsu), separately for each file")
        self.thr_shared = QRadioButton("Automatic (Otsu), one threshold for all files together")
        self.thr_fixed = QRadioButton("Fixed value")
        self.thr_value = QDoubleSpinBox()
        self.thr_value.setRange(0.001, 5)
        self.thr_value.setDecimals(4)
        self.thr_value.setSingleStep(0.005)
        self.thr_value.setValue(0.19)
        self.thr_model = QRadioButton("Trained fiber model:")
        self.fm_path = QLineEdit()
        self.fm_path.setReadOnly(True)
        self.fm_path.setPlaceholderText("paint and train one on the Fiber model page…")
        self.fm_cut = QDoubleSpinBox()
        self.fm_cut.setRange(0.05, 0.95)
        self.fm_cut.setDecimals(2)
        self.fm_cut.setSingleStep(0.05)
        self.fm_cut.setValue(0.5)
        self.fm_cut.setPrefix("p > ")
        self.fm_cut.setToolTip("A pixel is fiber when the model's fiber probability is above this value")
        for b in (self.thr_per, self.thr_shared, self.thr_fixed, self.thr_model):
            self.thr_group.addButton(b)
        gl.addWidget(self.thr_per)
        gl.addWidget(self.thr_shared)
        gl.addWidget(hbox(self.thr_fixed, self.thr_value))
        row = hbox(self.thr_model, self.fm_path, button("Choose…", slot=self.browse_fiber_model), self.fm_cut,
                   stretch_end=False)
        row.layout().setStretch(1, 1)
        gl.addWidget(row)
        gl.addWidget(hint("Per-file Otsu adapts to laser/gain differences but can partly hide real density changes. "
                          "For before-vs-after claims a fixed value (same for both files) is the stricter test. "
                          "The value is a ridge-filter score (no units), typically 0.1–0.3; higher = stricter."))
        self.thr_per.setChecked(True)
        for b in (self.thr_per, self.thr_shared, self.thr_fixed, self.thr_model):
            b.toggled.connect(self._sync)
        self.thr_value.valueChanged.connect(self._sync)
        self.fm_cut.valueChanged.connect(self._sync)
        ll.addWidget(g)

        # --- hotspot
        g = QGroupBox("Reflection hotspot (bright disc of the objective)")
        gl = QVBoxLayout(g)
        self.hs_auto = QRadioButton("Detect automatically")
        self.hs_none = QRadioButton("No hotspot (analyse the whole image)")
        self.hs_manual = QRadioButton("Manual circle (pixels):")
        hg = QButtonGroup(self)
        for b in (self.hs_auto, self.hs_none, self.hs_manual):
            hg.addButton(b)
            b.toggled.connect(self._sync)
        self.hs_x, self.hs_y, self.hs_r = QSpinBox(), QSpinBox(), QSpinBox()
        for s_, pre in ((self.hs_x, "x "), (self.hs_y, "y "), (self.hs_r, "r ")):
            s_.setRange(0, 20000)
            s_.setMaximumWidth(90)
            s_.setPrefix(pre)
            s_.valueChanged.connect(self._sync)
        self.hs_x.setValue(512); self.hs_y.setValue(512); self.hs_r.setValue(150)
        gl.addWidget(self.hs_auto)
        gl.addWidget(self.hs_none)
        gl.addWidget(hbox(self.hs_manual, self.hs_x, self.hs_y, self.hs_r))
        self.w["hotspot_sigma_um"] = _spin("hotspot_sigma_um")
        f = QFormLayout()
        f.addRow(NUMERIC["hotspot_sigma_um"][0], self.w["hotspot_sigma_um"])
        gl.addLayout(f)
        gl.addWidget(hint("Use the preview to check the yellow circle. The manual circle is in pixels of the image "
                          "(x to the right, y down), e.g. 499, 518, 170."))
        self.hs_auto.setChecked(True)
        ll.addWidget(g)

        # --- numeric groups
        for title, keys in GROUPS:
            g = QGroupBox(title)
            f = QFormLayout(g)
            f.setLabelAlignment(Qt.AlignLeft)
            for k in keys:
                if k not in self.w:
                    self.w[k] = _spin(k)
                lab = QLabel(NUMERIC[k][0])
                lab.setToolTip(NUMERIC[k][6])
                f.addRow(lab, self.w[k])
                self.w[k].valueChanged.connect(self._sync)
            if title == "Which images are analysed":
                self.stack_axis = QComboBox()
                self.stack_axis.addItems(["auto (from the file)", "z (depth)", "t (time)"])
                self.stack_axis.setToolTip("Only for 3-D files whose third axis is not labelled (some TIFFs).")
                f.addRow("Third axis of 3-D files", self.stack_axis)
                self.t_int_on = QCheckBox("Time between time points")
                self.t_int = QDoubleSpinBox()
                self.t_int.setRange(0.001, 1e6); self.t_int.setSuffix("  s"); self.t_int.setValue(10)
                self.t_int.setToolTip("Only needed if the file does not store the timing (some TIFFs).")
                f.addRow(self.t_int_on, self.t_int)
                self.stack_axis.currentIndexChanged.connect(self._sync)
                self.t_int_on.toggled.connect(self._sync)
                self.t_int.valueChanged.connect(self._sync)
            if title == "Fiber detection":
                self.ridge = QLineEdit(",".join(f"{v:g}" for v in DEFAULTS["ridge_sigmas_um"]) if not isinstance(
                    DEFAULTS["ridge_sigmas_um"], str) else DEFAULTS["ridge_sigmas_um"])
                self.ridge.setToolTip("Sato ridge-filter scales ≈ fiber half-widths, µm, separated by commas. "
                                      "Add 0.3,0.4 for thick bundles; remove 0.1 for noisy images.")
                f.insertRow(1, "Ridge filter scales (µm)", self.ridge)
                self.ridge.textChanged.connect(self._sync)
            ll.addWidget(g)

        # --- names and TIFF
        g = QGroupBox("File names and TIFF calibration")
        f = QFormLayout(g)
        self.after = QLineEdit(DEFAULTS["after_pattern"])
        self.after.setToolTip("Text (regular expression) in a file name that marks the After-AMF file. Default: AMF")
        f.addRow("After-AMF files contain", self.after)
        self.px_on, self.px = QCheckBox("Pixel size"), QDoubleSpinBox()
        self.px.setRange(0.0001, 100); self.px.setDecimals(4); self.px.setSuffix("  µm"); self.px.setValue(0.1026)
        self.dz_on, self.dz = QCheckBox("Z-step"), QDoubleSpinBox()
        self.dz.setRange(0.0001, 100); self.dz.setDecimals(4); self.dz.setSuffix("  µm"); self.dz.setValue(0.075)
        for cb, sp in ((self.px_on, self.px), (self.dz_on, self.dz)):
            sp.setToolTip("TIFF files only, when the file does not contain it. .nd2 files always use their own "
                          "calibration.")
            f.addRow(cb, sp)
            cb.toggled.connect(self._sync)
            sp.valueChanged.connect(self._sync)
        self.after.textChanged.connect(self._sync)
        ll.addWidget(g)
        ll.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(left)
        sa.setMinimumWidth(500)
        split.addWidget(sa)

        # ================================================================ right: preview
        right = Card("Preview one image", "Loads one image with the current settings: excluded areas, fiber mask "
                     "and its threshold. Move the slider to try other thresholds instantly; ‘Measure’ runs the "
                     "full analysis of this image.")
        self.pv_file = QComboBox()
        self.pv_file.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pv_file.setMinimumContentsLength(28)
        self.pv_slice = QSpinBox()
        self.pv_slice.setPrefix("image ")
        self.pv_slice.setToolTip("Which image of the file (z-slice or time point; counted from 0)")
        self.pv_load = button("Load", "primary", slot=self.preview_load)
        right.lay.addWidget(hbox(QLabel("File"), self.pv_file, self.pv_slice, self.pv_load, stretch_end=False))
        self.pv_thr = QSlider(Qt.Horizontal)
        self.pv_thr.setRange(10, 800)          # threshold × 1000
        self.pv_thr.setEnabled(False)
        self.pv_thr.valueChanged.connect(self._thr_moved)
        self.pv_thr_label = QLabel("threshold —")
        self.pv_thr_label.setMinimumWidth(170)
        self.pv_use = button("Use as fixed threshold", slot=self.use_threshold)
        self.pv_otsu = button("Back to Otsu value", "link", slot=self.reset_preview_threshold)
        self.pv_measure = button("Measure this image", slot=self.preview_measure)
        for b in (self.pv_use, self.pv_otsu, self.pv_measure):
            b.setEnabled(False)
        right.lay.addWidget(hbox(self.pv_thr_label, self.pv_thr, stretch_end=False))
        right.lay.addWidget(hbox(self.pv_use, self.pv_otsu, "stretch", self.pv_measure, stretch_end=False))
        self.fig = Figure(figsize=(7, 6.4), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.lay.addWidget(self.canvas, 1)
        self.pv_info = QLabel("Choose a file and click Load.")
        self.pv_info.setObjectName("hint")
        self.pv_info.setWordWrap(True)
        self.pv_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.lay.addWidget(self.pv_info)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([540, 760])
        self._cache = {}
        self._pv = None
        self._task = None
        self._thr_timer = QTimer(self)
        self._thr_timer.setSingleShot(True)
        self._thr_timer.timeout.connect(self._redraw_mask)
        self.data_page.changed.connect(self.update_files)
        self._ready = True
        self.load_settings()
        self._sync()
        self._blank()

    # ------------------------------------------------------------------ values
    def values(self):
        v = {k: w.value() for k, w in self.w.items()}
        v["ridge_sigmas_um"] = self.ridge.text().strip()
        v["threshold_mode"] = "shared" if self.thr_shared.isChecked() else "per-file"
        v["threshold"] = self.thr_value.value() if self.thr_fixed.isChecked() else None
        v["fiber_model"] = self.fm_path.text() if self.thr_model.isChecked() and self.fm_path.text() else None
        v["fiber_model_path"] = self.fm_path.text()
        v["fiber_cutoff"] = self.fm_cut.value()
        if self.hs_auto.isChecked():
            v["hotspot"] = "auto"
        elif self.hs_none.isChecked():
            v["hotspot"] = "none"
        else:
            v["hotspot"] = f"{self.hs_x.value()},{self.hs_y.value()},{self.hs_r.value()}"
        v["stack_axis"] = ["auto", "z", "t"][self.stack_axis.currentIndex()]
        v["t_interval"] = self.t_int.value() if self.t_int_on.isChecked() else None
        v["after_pattern"] = self.after.text().strip() or "AMF"
        v["pixel_size"] = self.px.value() if self.px_on.isChecked() else None
        v["z_step"] = self.dz.value() if self.dz_on.isChecked() else None
        return v

    def set_values(self, v):
        for k, w in self.w.items():
            if k in v and v[k] is not None:
                w.setValue(v[k])
        if "ridge_sigmas_um" in v:
            r = v["ridge_sigmas_um"]
            self.ridge.setText(r if isinstance(r, str) else ",".join(f"{x:g}" for x in r))
        self.fm_path.setText(v.get("fiber_model_path") or v.get("fiber_model") or "")
        self.fm_cut.setValue(float(v.get("fiber_cutoff") or 0.5))
        if v.get("fiber_model"):
            self.thr_model.setChecked(True)
        elif v.get("threshold") is not None:
            self.thr_fixed.setChecked(True)
            self.thr_value.setValue(float(v["threshold"]))
        elif v.get("threshold_mode") == "shared":
            self.thr_shared.setChecked(True)
        else:
            self.thr_per.setChecked(True)
        hs = v.get("hotspot", "auto")
        if hs == "auto":
            self.hs_auto.setChecked(True)
        elif hs == "none":
            self.hs_none.setChecked(True)
        else:
            try:
                x, y, r = (int(t) for t in str(hs).split(","))
                self.hs_x.setValue(x); self.hs_y.setValue(y); self.hs_r.setValue(r)
                self.hs_manual.setChecked(True)
            except ValueError:
                self.hs_auto.setChecked(True)
        self.stack_axis.setCurrentIndex(["auto", "z", "t"].index(v.get("stack_axis", "auto")))
        for key, cb, sp in (("t_interval", self.t_int_on, self.t_int), ("pixel_size", self.px_on, self.px),
                            ("z_step", self.dz_on, self.dz)):
            cb.setChecked(v.get(key) is not None)
            if v.get(key) is not None:
                sp.setValue(float(v[key]))
        self.after.setText(v.get("after_pattern", "AMF"))

    def defaults(self):
        d = {k: DEFAULTS[k] for k in self.w}
        d.update(ridge_sigmas_um=DEFAULTS["ridge_sigmas_um"], threshold_mode="per-file", threshold=None,
                 fiber_model=None, fiber_model_path=self.fm_path.text(), fiber_cutoff=0.5,
                 hotspot="auto", stack_axis="auto", t_interval=None, after_pattern="AMF", pixel_size=None, z_step=None)
        return d

    def reset(self):
        self.set_values(self.defaults())

    def validate(self):
        v = self.values()
        try:
            r = [float(x) for x in v["ridge_sigmas_um"].split(",")]
            assert r and min(r) > 0
        except (ValueError, AssertionError):
            return "Ridge filter scales must be positive numbers separated by commas, e.g. 0.1,0.15,0.2"
        import re
        try:
            re.compile(v["after_pattern"])
        except re.error as e:
            return f"‘After-AMF files contain’ is not a valid pattern: {e}"
        if self.thr_model.isChecked():
            if not os.path.isfile(self.fm_path.text()):
                return "Trained fiber model: choose a model file, or train one on the Fiber model page."
            try:
                FM.load_model(self.fm_path.text())
            except Exception as e:
                return f"The fiber model cannot be used: {e}"
        if self.data_page.needs_pixel_size() and v["pixel_size"] is None:
            return "Some TIFF files have no pixel size: tick ‘Pixel size’ under TIFF calibration and enter it."
        return None

    def engine_args(self):
        v = self.values()
        a = []
        for k in NUMERIC:
            val = v[k]
            if val != DEFAULTS[k] or k in ("zstep", "tstep", "workers"):
                a += [f"--{k.replace('_', '-')}", f"{val:g}" if isinstance(val, float) else str(val)]
        a += ["--ridge-sigmas-um", v["ridge_sigmas_um"].replace(" ", "")]
        if v["fiber_model"]:
            a += ["--fiber-model", v["fiber_model"], "--fiber-cutoff", f"{v['fiber_cutoff']:g}"]
        elif v["threshold"] is not None:
            a += ["--threshold", f"{v['threshold']:g}"]
        else:
            a += ["--threshold-mode", v["threshold_mode"]]
        if v["hotspot"] != "auto":
            a += ["--hotspot", v["hotspot"]]
        if v["stack_axis"] != "auto":
            a += ["--stack-axis", v["stack_axis"]]
        for k in ("t_interval", "pixel_size", "z_step"):
            if v[k] is not None:
                a += [f"--{k.replace('_', '-')}", f"{v[k]:g}"]
        if v["after_pattern"] != "AMF":
            a += ["--after-pattern", v["after_pattern"]]
        return a

    def namespace(self):
        """Settings as the engine's options object (for the preview)."""
        v = self.values()
        P = argparse.Namespace(**DEFAULTS)
        for k, val in v.items():
            setattr(P, k, val)
        P.ridge_sigmas_um = [float(x) for x in v["ridge_sigmas_um"].split(",")]
        del P.fiber_model_path
        return P

    def _sync(self, *_):
        if not self._ready:
            return
        self.thr_value.setEnabled(self.thr_fixed.isChecked())
        self.fm_path.setEnabled(self.thr_model.isChecked())
        self.fm_cut.setEnabled(self.thr_model.isChecked())
        for s_ in (self.hs_x, self.hs_y, self.hs_r):
            s_.setEnabled(self.hs_manual.isChecked())
        self.t_int.setEnabled(self.t_int_on.isChecked())
        self.px.setEnabled(self.px_on.isChecked())
        self.dz.setEnabled(self.dz_on.isChecked())
        self.data_page.set_after_pattern(self.after.text().strip() or "AMF")
        self.save_settings()
        self.changed.emit()

    # ------------------------------------------------------------------ persistence / presets
    def save_settings(self):
        try:
            QSettings().setValue("params/values", json.dumps(self.values()))
        except Exception:
            pass

    def load_settings(self):
        try:
            v = json.loads(QSettings().value("params/values", "") or "{}")
            if v:
                self.set_values({**self.defaults(), **v})
        except Exception:
            pass

    def save_preset(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save parameters as preset", "collagen_preset.json", "Preset (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(self.values(), fh, indent=2)

    def load_preset(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load preset", "", "Preset (*.json)")
        if p:
            try:
                with open(p, encoding="utf-8") as fh:
                    self.set_values({**self.defaults(), **json.load(fh)})
            except Exception as e:
                QMessageBox.warning(self, "Preset", f"Could not read the preset:\n{e}")

    # ------------------------------------------------------------------ preview
    def update_files(self):
        cur = self.pv_file.currentData()
        self.pv_file.blockSignals(True)
        self.pv_file.clear()
        for p in self.data_page.selected_files() or list(self.data_page.rows):
            self.pv_file.addItem(os.path.basename(p), p)
        i = self.pv_file.findData(cur)
        self.pv_file.setCurrentIndex(max(i, 0))
        self.pv_file.blockSignals(False)
        self.pv_load.setEnabled(self.pv_file.count() > 0)

    def _blank(self, msg="No image loaded"):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        ax.text(0.5, 0.5, msg, ha="center", va="center", color="#9aa3ab", fontsize=12, transform=ax.transAxes)
        self.canvas.draw_idle()

    def _busy(self, on, msg=""):
        for b in (self.pv_load, self.pv_measure, self.pv_use):
            b.setEnabled(not on and (b is self.pv_load or self._pv is not None))
        if msg:
            self.pv_info.setText(msg)

    def preview_load(self):
        path = self.pv_file.currentData()
        if not path:
            return
        err = self.validate()
        if err:
            QMessageBox.warning(self, "Parameters", err)
            return
        P = self.namespace()
        idx = self.pv_slice.value()
        self._busy(True, f"Loading {os.path.basename(path)} …")
        self._task = Task(self._load_job, path, idx, P)
        self._task.ok.connect(self._loaded)
        self._task.failed.connect(lambda m: (self._busy(False), self.pv_info.setText("Could not load: " + m)))
        self._task.start()

    def _load_job(self, path, idx, P):
        key = (path, P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
        if key not in self._cache:
            self._cache.clear()                     # keep one stack in memory
            s, px, dz, times = core.load_stack(path, P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
            self._cache[key] = (s, px, dz)
        s, px, dz = self._cache[key]
        G = core.geometry(px, P)
        frames = core.frames_to_analyse(s.shape, 1, 1)
        idx = min(idx, len(frames) - 1)
        t, z = frames[idx]
        if P.hotspot == "auto":
            hs = core.find_hotspot(s.reshape(-1, *s.shape[2:]), sigma=G["hotspot_sigma"])
        elif P.hotspot == "none":
            hs = None
        else:
            hs = tuple(int(v) for v in P.hotspot.split(","))
        valid = core.analysis_mask(s.shape[2:], hs, edge=G["edge"])
        img = s[t, z]
        flat = core.flatten(img, G["sigma_bg"])
        if P.fiber_model:                           # fiber probability; "Otsu" value = the chosen cutoff
            rdg = FM.probability(flat, px, FM.load_model(P.fiber_model))
            otsu = P.fiber_cutoff
        else:
            rdg = core.ridge(flat, G["ridge_sigmas"])
            otsu = core.otsu_threshold(rdg[valid])
        return dict(path=path, img=img, flat=flat, ridge=rdg, valid=valid, hs=hs, G=G, px=px, otsu=otsu,
                    t=t, z=z, n=len(frames), shape=s.shape, P=P)

    def _loaded(self, pv):
        self._pv = pv
        self.pv_thr.setRange(10, 990 if pv["P"].fiber_model else 800)
        self.pv_slice.setMaximum(pv["n"] - 1)
        P = pv["P"]
        thr = P.threshold if P.threshold is not None else pv["otsu"]
        pv["thr"] = thr
        self.pv_thr.blockSignals(True)
        self.pv_thr.setValue(int(round(thr * 1000)))
        self.pv_thr.blockSignals(False)
        self.pv_thr.setEnabled(True)
        self.pv_otsu.setEnabled(True)
        self._busy(False)
        self._draw_preview()

    def _thr_moved(self, v):
        if self._pv is None:
            return
        self._pv["thr"] = v / 1000
        self.pv_thr_label.setText(f"threshold {v / 1000:.3f}")
        self._thr_timer.start(60)

    def reset_preview_threshold(self):
        if self._pv:
            self.pv_thr.setValue(int(round(self._pv["otsu"] * 1000)))

    def browse_fiber_model(self):
        p, _ = QFileDialog.getOpenFileName(self, "Fiber model", os.path.dirname(self.fm_path.text()),
                                           "Fiber model (*.joblib)")
        if p:
            self.set_fiber_model(p)

    def set_fiber_model(self, path):
        """Use this fiber model (called by the Fiber model page after saving)."""
        self.fm_path.setText(path)
        self.thr_model.setChecked(True)
        self._sync()

    def use_threshold(self):
        if self._pv and self._pv["P"].fiber_model:
            self.fm_cut.setValue(round(self._pv["thr"], 2))
            return
        if self._pv:
            self.thr_fixed.setChecked(True)
            self.thr_value.setValue(round(self._pv["thr"], 4))

    def _mask(self):
        pv = self._pv
        return core.remove_small(pv["ridge"] > pv["thr"], pv["G"]["min_obj_px"])

    def _draw_preview(self, measured=None):
        pv = self._pv
        self.fig.clear()
        axs = self.fig.subplots(2, 2)
        px = pv["px"]
        ext = [0, pv["img"].shape[1] * px, pv["img"].shape[0] * px, 0]
        a = axs[0, 0]
        img = pv["img"]
        a.imshow(img, cmap="gray", vmin=np.percentile(img, 1), vmax=np.percentile(img, 99.7), extent=ext)
        if pv["hs"]:
            x, y, r = pv["hs"]
            import matplotlib.pyplot as plt
            a.add_patch(plt.Circle((x * px, y * px), r * px, fill=False, ec="yellow", ls="--", lw=1.2))
        a.set_title("Image (yellow = excluded reflection)", fontsize=9)
        self._mask_ax = axs[0, 1]
        self._mask_artist = None
        self._redraw_mask(draw=False)
        if measured is None:
            for a in (axs[1, 0], axs[1, 1]):
                a.axis("off")
            axs[1, 0].text(0.5, 0.5, "Click ‘Measure this image’ for the\norientation and pore-size maps",
                           ha="center", va="center", color="#9aa3ab", fontsize=10, transform=axs[1, 0].transAxes)
        else:
            out, aux = measured
            o = aux["o"]
            import matplotlib.colors as mc
            hsv = np.zeros(img.shape + (3,))
            hsv[..., 0] = (o["theta_map"] + np.pi / 2) / np.pi
            hsv[..., 1] = np.clip(o["coh_map"] * 1.5, 0, 1)
            fl = aux["flat"]
            lo, hi = np.percentile(fl, 1), np.percentile(fl, 99.5)
            hsv[..., 2] = np.clip((fl - lo) / (hi - lo), 0, 1)
            axs[1, 0].imshow(mc.hsv_to_rgb(hsv), extent=ext)
            axs[1, 0].set_title(f"Orientation (S = {out['order_S']:.2f}, mean {out['mean_angle_deg']:.0f}°)", fontsize=9)
            im = axs[1, 1].imshow(np.where(np.isnan(aux["lt"]), np.nan, aux["lt"] * px), cmap="viridis",
                                  extent=ext, interpolation="nearest")
            axs[1, 1].set_facecolor("black")
            axs[1, 1].set_title(f"Pore size map (mean {out['pore_lt_mean_um']:.2f} µm)", fontsize=9)
            self.fig.colorbar(im, ax=axs[1, 1], fraction=0.046, pad=0.02).set_label("µm", fontsize=8)
        for a in axs.flat:
            a.tick_params(labelsize=7)
        self.canvas.draw_idle()

    def _redraw_mask(self, draw=True):
        pv = self._pv
        if pv is None:
            return
        mask = self._mask()
        valid = pv["valid"]
        rgb = np.zeros(mask.shape + (3,))
        f = pv["flat"]
        g = np.clip((f - np.percentile(f, 1)) / (np.percentile(f, 99.5) - np.percentile(f, 1) + 1e-9), 0, 1) * 0.55
        rgb[...] = g[..., None]
        rgb[mask & valid] = (1.0, 0.25, 0.2)
        rgb[~valid] = rgb[~valid] * 0.35 + 0.2
        px = pv["px"]
        ext = [0, mask.shape[1] * px, mask.shape[0] * px, 0]
        a = self._mask_ax
        if self._mask_artist is None:
            self._mask_artist = a.imshow(rgb, extent=ext)
        else:
            self._mask_artist.set_data(rgb)
        af = 100 * (mask & valid).sum() / valid.sum()
        model = bool(pv["P"].fiber_model)
        what = "probability cutoff" if model else "threshold"
        a.set_title(f"Fiber mask (red), {what} {pv['thr']:.3f}: {af:.1f} % fiber", fontsize=9)
        self.pv_thr_label.setText(f"{what} {pv['thr']:.3f}")
        t, z = pv["t"], pv["z"]
        where = (f"t = {t}, " if pv["shape"][0] > 1 else "") + (f"z = {z}" if pv["shape"][1] > 1 else "single image")
        thr_txt = (f"Trained fiber model {os.path.basename(pv['P'].fiber_model)}: fiber = probability > cutoff "
                   f"(set {pv['otsu']:.2f})." if model else
                   f"Otsu threshold of this image: {pv['otsu']:.4f} (a run uses up to 12 images of the file, so it "
                   f"can differ slightly).")
        self.pv_info.setText(
            f"{os.path.basename(pv['path'])} — {where}; pixel {px:.4f} µm; "
            f"hotspot {'none' if not pv['hs'] else ', '.join(map(str, pv['hs'])) + ' px (x, y, r)'}.  "
            f"{thr_txt} Fiber area {af:.2f} %.")
        if draw:
            self.canvas.draw_idle()

    def preview_measure(self):
        pv = self._pv
        if pv is None:
            return
        self._busy(True, "Measuring this image (about 10–30 s) …")
        self._task = Task(lambda: core.analyse_slice(pv["img"], pv["valid"], pv["thr"], pv["G"], full=True))
        self._task.ok.connect(self._measured)
        self._task.failed.connect(lambda m: (self._busy(False), self.pv_info.setText("Failed: " + m)))
        self._task.start()

    def _measured(self, res):
        self._busy(False)
        out, aux = res
        self._draw_preview(measured=res)
        self.pv_info.setText(self.pv_info.text() + "\n" + (
            f"Measured: fiber {out['area_frac_pct']:.2f} %, length density {out['length_density_um_per_um2']:.3f} µm/µm², "
            f"pore size {out['pore_lt_mean_um']:.2f} µm (median {out['pore_lt_median_um']:.2f}), fiber size "
            f"{out['fiber_lt_mean_um']:.2f} µm, fiber length {out['fiber_len_mean_um']:.2f} µm, S = {out['order_S']:.2f}, "
            f"tile CV {out['tile_cv']:.2f}."))
