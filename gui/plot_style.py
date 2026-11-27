"""Look of the graphs on the Results & plots page: the Style panel, presets and draggable labels.

Style settings are one plain dict (see DEFAULTS); the panel edits it, remembers it between sessions and
can save / load named presets. The plotting code reads the dict: fonts and sizes through rc() and
finish(); bars, points, error bars and fit lines in each graph.

DragLabels lets the user move the figure title, panel titles, axis labels, legends and the numbers on
the graph with the mouse; the moves are kept when the graph is redrawn (until the graph type changes or
'Reset label positions' is clicked).
"""
import json
import math

import numpy as np
from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

DEFAULTS = dict(
    font="DejaVu Sans", size_title=12, size_panel=10, size_label=10, size_tick=9, size_legend=9, size_annot=8,
    title_bold=False, title_align="center", show_title=True, show_panel_titles=True,
    legend_pos="auto", legend_cols=0, legend_frame=False, xrot=0, value_labels=True,
    bar_width=0.8, bar_alpha=0.85, bar_edge=False, capsize=4.0, err_lw=1.2,
    points=True, point_size=5.0, point_alpha=0.75, jitter=0.12, point_color="match",
    line_width=1.6, marker_size=4.0,
    fit="none", fit_band=False, fit_eq=True,
    yzero=True, logy=False, clean_axes=False, grid=True, tick_dir="out", ymin="", ymax="", xmin="", xmax="",
    palette="default", c_before="#3b6fb6", c_after="#d9622b", c_sham="#7f8c8d", c_amfonly="#8e44ad",
)
PALETTES = {
    "default": ["#3b6fb6", "#d9622b", "#2a9d5c", "#8e44ad", "#c0392b", "#7f8c8d", "#e0a800", "#17a2b8", "#6d4c41",
                "#ad1457"],
    "okabe": ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#F0E442", "#000000"],
    "tab10": ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22",
              "#17becf"],
    "grey": ["#222222", "#777777", "#aaaaaa", "#4d4d4d", "#cccccc", "#999999"],
    "viridis": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
}
PALETTE_NAMES = [("default", "App colours"), ("okabe", "Colour-blind safe (Okabe–Ito)"), ("tab10", "Matplotlib tab10"),
                 ("grey", "Greyscale (print)"), ("viridis", "Viridis")]
NUMERIC_CMAP = {"default": "viridis", "okabe": "cividis", "tab10": "viridis", "grey": "Greys", "viridis": "viridis"}
COND_BY_PALETTE = {  # Before, After AMF, sham, AMF only for the non-default palettes
    "okabe": ("#0072B2", "#E69F00", "#999999", "#CC79A7"),
    "tab10": ("#1f77b4", "#ff7f0e", "#7f7f7f", "#9467bd"),
    "grey": ("#bbbbbb", "#333333", "#888888", "#555555"),
    "viridis": ("#3b528b", "#fde725", "#21918c", "#440154"),
}
PRESETS = {
    "App default": {},
    "Journal figure (Arial, 7–8 pt, clean axes)": dict(
        font="Arial", size_title=9, size_panel=8, size_label=8, size_tick=7, size_legend=7, size_annot=6.5,
        title_bold=True, clean_axes=True, grid=False, legend_frame=False, capsize=2.5, err_lw=0.8, line_width=1.0,
        marker_size=3.0, point_size=2.2, bar_edge=False, palette="okabe"),
    "Slides (large text)": dict(size_title=20, size_panel=16, size_label=16, size_tick=14, size_legend=14,
                                size_annot=13, title_bold=True, line_width=2.4, marker_size=7, point_size=5,
                                capsize=6, err_lw=2.0),
    "Greyscale print": dict(palette="grey", clean_axes=True, grid=False, bar_edge=True),
}
FIG_SIZES = [("auto", "Automatic (fills the window)", None), ("j1", "Journal, 1 column (3.5 × 2.6 in)", (3.5, 2.6)),
             ("j15", "Journal, 1.5 columns (5.5 × 3.6 in)", (5.5, 3.6)), ("j2", "Journal, 2 columns (7.2 × 4.2 in)", (7.2, 4.2)),
             ("slide", "Slide, 16:9 (10 × 5.6 in)", (10.0, 5.6)), ("custom", "Custom size", None)]


def font_names():
    """Font families matplotlib can use: common ones first, then everything installed."""
    from matplotlib import font_manager
    try:
        have = sorted({f.name for f in font_manager.fontManager.ttflist})
    except Exception:
        have = []
    first = [n for n in ("Arial", "Helvetica", "Inter", "DejaVu Sans", "Liberation Sans", "Calibri", "Segoe UI",
                         "Times New Roman", "Georgia", "DejaVu Serif", "Courier New") if n in have]
    return first + [n for n in have if n not in first]


def register_app_font():
    """Make the bundled Inter font available to matplotlib too."""
    import os
    import sys
    from matplotlib import font_manager
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for f in ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
        p = os.path.join(base, "assets", "fonts", f)
        if os.path.exists(p):
            try:
                font_manager.fontManager.addfont(p)
            except Exception:
                pass


def rc(S):
    """rcParams for drawing with style S (fonts and sizes are fixed when the artists are made)."""
    return {"font.family": [S["font"], "DejaVu Sans"], "font.size": S["size_tick"],
            "axes.titlesize": S["size_panel"], "axes.labelsize": S["size_label"],
            "xtick.labelsize": S["size_tick"], "ytick.labelsize": S["size_tick"], "legend.fontsize": S["size_legend"],
            "figure.titlesize": S["size_title"], "figure.titleweight": "bold" if S["title_bold"] else "normal",
            "figure.labelsize": S["size_label"],
            "axes.spines.top": not S["clean_axes"], "axes.spines.right": not S["clean_axes"],
            "xtick.direction": S["tick_dir"], "ytick.direction": S["tick_dir"],
            "errorbar.capsize": S["capsize"], "lines.linewidth": S["line_width"], "lines.markersize": S["marker_size"],
            "axes.grid": False, "legend.frameon": S["legend_frame"]}


def cond_colors(S):
    """Colours of Before, After AMF, sham and AMF-only for style S."""
    return S["c_before"], S["c_after"], S["c_sham"], S["c_amfonly"]


def palette_group_colors(palette):
    """Before, After AMF, sham, AMF-only colours that go with a palette."""
    return COND_BY_PALETTE.get(palette, (DEFAULTS["c_before"], DEFAULTS["c_after"], DEFAULTS["c_sham"],
                                         DEFAULTS["c_amfonly"]))


def fit_line(x, y, kind):
    """Fit y on x. kind: linear / logx / mean. Returns dict(xs, ys, lo, hi, text) or None."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if kind == "logx":
        ok &= x > 0
    x, y = x[ok], y[ok]
    if x.size < 2 or kind == "none":
        return None
    if kind == "mean":
        m = float(y.mean())
        xs = np.array([x.min(), x.max()])
        return dict(xs=xs, ys=np.full(2, m), lo=None, hi=None, text=f"mean {m:.3g}")
    u = np.log10(x) if kind == "logx" else x
    if np.ptp(u) == 0:
        return None
    b, a = np.polyfit(u, y, 1)
    yhat = a + b * u
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    n = x.size
    us = np.linspace(u.min(), u.max(), 100)
    ys = a + b * us
    lo = hi = None
    if n > 2:
        from scipy import stats
        s = math.sqrt(ss_res / (n - 2))
        se = s * np.sqrt(1 / n + (us - u.mean()) ** 2 / np.sum((u - u.mean()) ** 2))
        t = stats.t.ppf(0.975, n - 2)
        lo, hi = ys - t * se, ys + t * se
    xs = 10 ** us if kind == "logx" else us
    xv = "log₁₀x" if kind == "logx" else "x"
    return dict(xs=xs, ys=ys, lo=lo, hi=hi, text=f"y = {a:.3g} {'+' if b >= 0 else '−'} {abs(b):.3g}·{xv}, R² = {r2:.2f}")


# ---------------------------------------------------------------------------------------------- the panel
class _Color(QPushButton):
    changed = Signal()

    def __init__(self, color):
        super().__init__()
        self.setFixedSize(34, 24)
        self.set(color)
        self.clicked.connect(self._pick)

    def set(self, c):
        self.color = c
        self.setStyleSheet(f"background: {c}; border: 1px solid #8a959f; border-radius: 5px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Choose a colour")
        if c.isValid():
            self.set(c.name())
            self.changed.emit()


class StylePanel(QWidget):
    """Tabs with every style setting of the graphs; emits changed() on any edit."""
    changed = Signal()
    reset_positions = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.w = {}
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setToolTip("Ready-made looks and your own saved styles")
        self.preset.setMinimumContentsLength(14)
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        top.addWidget(self.preset, 1)
        b = QPushButton("Save as…")
        b.setToolTip("Save the current style under a name (kept on this computer)")
        b.clicked.connect(self.save_preset)
        top.addWidget(b)
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self.delete_preset)
        top.addWidget(self.del_btn)
        lay.addLayout(top)
        self.preset.activated.connect(self._preset_chosen)

        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- text
        t, f = self._tab(tabs, "Text")
        self._combo("font", font_names(), f, "Font", editable=True,
                    tip="Font of all text in the graph (Arial or Helvetica are usual for journals)")
        for key, lab in (("size_title", "Title"), ("size_panel", "Panel titles"), ("size_label", "Axis labels"),
                         ("size_tick", "Tick labels"), ("size_legend", "Legend"), ("size_annot", "Numbers on graph")):
            self._num(key, f, lab, 4, 40, 0.5, 1, " pt")
        self._check("title_bold", f, "Bold title")
        self._choice("title_align", [("left", "Left"), ("center", "Centre"), ("right", "Right")], f, "Title position")

        # --- labels & legend
        t, f = self._tab(tabs, "Labels")
        self._check("show_title", f, "Show the figure title")
        self._check("show_panel_titles", f, "Show panel titles")
        self._choice("legend_pos", [("auto", "Automatic"), ("inside", "Inside each graph"), ("right", "Right of the graphs"),
                                    ("below", "Below the graphs"), ("above", "Above the graphs"), ("none", "No legend")],
                     f, "Legend")
        self._num("legend_cols", f, "Legend columns", 0, 8, 1, 0, "", special="auto")
        self._check("legend_frame", f, "Frame around the legend")
        self._choice("xrot", [(0, "0°"), (30, "30°"), (45, "45°"), (90, "90°")], f, "x tick labels")
        self._check("value_labels", f, "Numbers on the graph (values, n, p)")
        hint = QLabel("Drag the title, axis labels, legend or numbers in the graph with the mouse to move them.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        rb = QPushButton("Reset label positions")
        rb.clicked.connect(self.reset_positions.emit)
        f.addRow(rb)

        # --- bars & points
        t, f = self._tab(tabs, "Bars && points")
        self._num("bar_width", f, "Bar width", 0.1, 1.0, 0.05, 2, "", tip="1 = bars touch")
        self._num("bar_alpha", f, "Bar opacity", 0.1, 1.0, 0.05, 2, "")
        self._check("bar_edge", f, "Dark outline on bars and boxes")
        self._num("capsize", f, "Error-bar caps", 0, 12, 0.5, 1, " pt")
        self._num("err_lw", f, "Error-bar line", 0.3, 4, 0.1, 1, " pt")
        self._check("points", f, "Show individual data points",
                    tip="Images (per-file and depth graphs), gels (group graph) or images at each setting")
        self._num("point_size", f, "Point size", 0.5, 12, 0.5, 1, " pt")
        self._num("point_alpha", f, "Point opacity", 0.05, 1.0, 0.05, 2, "")
        self._num("jitter", f, "Point spread", 0.0, 0.45, 0.02, 2, "", tip="Random sideways spread so points do not overlap")
        self._choice("point_color", [("match", "Same colour as the bar"), ("dark", "Dark grey"), ("white", "White with outline")],
                     f, "Point colour")
        self._num("line_width", f, "Line width", 0.3, 6, 0.1, 1, " pt")
        self._num("marker_size", f, "Marker size", 0, 16, 0.5, 1, " pt")

        # --- fit lines
        t, f = self._tab(tabs, "Fit")
        self._choice("fit", [("none", "None"), ("linear", "Straight line (linear fit)"),
                             ("logx", "Straight line on log x (y = a + b·log x)"), ("mean", "Mean (horizontal line)")],
                     f, "Trend line")
        self._check("fit_band", f, "95 % confidence band of the line")
        self._check("fit_eq", f, "Equation and R² in the legend")
        note = QLabel("Fit lines are drawn on ‘Metric vs experimental parameter’ (one line per colour) and "
                      "‘Metric vs depth / time’ (one line per file).")
        note.setObjectName("hint")
        note.setWordWrap(True)
        f.addRow(note)

        # --- axes & colours
        t, f = self._tab(tabs, "Axes && colours")
        self._check("yzero", f, "y axis starts at 0 (bars, positive values)")
        self._check("logy", f, "Logarithmic y axis")
        self._check("clean_axes", f, "Clean axes (no top / right lines)")
        self._check("grid", f, "Grid lines")
        self._choice("tick_dir", [("out", "Outside"), ("in", "Inside")], f, "Tick marks")
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        for key in ("ymin", "ymax"):
            e = QLineEdit()
            e.setPlaceholderText("auto")
            e.textChanged.connect(self._edited)
            self.w[key] = e
            rl.addWidget(e)
            if key == "ymin":
                rl.addWidget(QLabel("to"))
        f.addRow("y range", row)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        for key in ("xmin", "xmax"):
            e = QLineEdit()
            e.setPlaceholderText("auto")
            e.textChanged.connect(self._edited)
            self.w[key] = e
            rl.addWidget(e)
            if key == "xmin":
                rl.addWidget(QLabel("to"))
        f.addRow("x range", row)
        self._choice("palette", PALETTE_NAMES, f, "Palette")
        from PySide6.QtWidgets import QGridLayout
        row = QWidget()
        rl = QGridLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setHorizontalSpacing(8)
        for i, (key, lab) in enumerate((("c_before", "Before"), ("c_after", "After AMF"), ("c_sham", "Sham"),
                                        ("c_amfonly", "AMF only"))):
            c = _Color(DEFAULTS[key])
            c.setToolTip(f"Colour of {lab} (click to change)")
            c.changed.connect(self._edited)
            self.w[key] = c
            rl.addWidget(c, i // 2, (i % 2) * 2)
            rl.addWidget(QLabel(lab), i // 2, (i % 2) * 2 + 1)
        rl.setColumnStretch(4, 1)
        f.addRow("Group colours", row)

        self._fill_presets()
        try:
            saved = json.loads(QSettings().value("plots/style", "") or "{}")
        except ValueError:
            saved = {}
        S = {**DEFAULTS, **saved}
        self._palette = S["palette"]
        self.set_values(S)

    # ------------------------------------------------------------------ widgets
    def _tab(self, tabs, name):
        t = QWidget()
        f = QFormLayout(t)
        f.setContentsMargins(10, 10, 10, 10)
        f.setSpacing(7)
        tabs.addTab(t, name)
        return t, f

    def _num(self, key, form, label, lo, hi, step, dec, suffix, tip=None, special=None):
        w = QDoubleSpinBox() if dec else QSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        if dec:
            w.setDecimals(dec)
        if suffix:
            w.setSuffix(suffix)
        if special:
            w.setSpecialValueText(special)
        if tip:
            w.setToolTip(tip)
        w.valueChanged.connect(self._edited)
        self.w[key] = w
        form.addRow(label, w)

    def _check(self, key, form, label, tip=None):
        w = QCheckBox(label)
        if tip:
            w.setToolTip(tip)
        w.toggled.connect(self._edited)
        self.w[key] = w
        form.addRow("", w)

    def _choice(self, key, items, form, label):
        w = QComboBox()
        for k, t in items:
            w.addItem(t, k)
        w.currentIndexChanged.connect(self._edited)
        self.w[key] = w
        form.addRow(label, w)

    def _combo(self, key, items, form, label, editable=False, tip=None):
        w = QComboBox()
        w.setEditable(editable)
        w.addItems(items)
        w.setMaxVisibleItems(20)
        if tip:
            w.setToolTip(tip)
        w.currentTextChanged.connect(self._edited)
        self.w[key] = w
        form.addRow(label, w)

    # ------------------------------------------------------------------ values
    def values(self):
        S = dict(DEFAULTS)
        for k, w in self.w.items():
            if isinstance(w, QCheckBox):
                S[k] = w.isChecked()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                S[k] = w.value()
            elif isinstance(w, QComboBox):
                S[k] = w.currentText().strip() if w.isEditable() else w.currentData()
            elif isinstance(w, QLineEdit):
                S[k] = w.text().strip()
            elif isinstance(w, _Color):
                S[k] = w.color
        S["font"] = S["font"] or DEFAULTS["font"]
        return S

    def set_values(self, S):
        self._loading = True
        try:
            for k, w in self.w.items():
                v = S.get(k, DEFAULTS[k])
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(v)
                elif isinstance(w, QComboBox):
                    if w.isEditable():
                        w.setCurrentText(str(v))
                    else:
                        i = w.findData(v)
                        w.setCurrentIndex(max(i, 0))
                elif isinstance(w, QLineEdit):
                    w.setText(str(v))
                elif isinstance(w, _Color):
                    w.set(v)
        finally:
            self._loading = False
        self._edited()

    def _edited(self, *_):
        if self._loading:
            return
        pal = self.w["palette"].currentData()
        if pal != getattr(self, "_palette", pal):        # a new palette brings its group colours along
            self._loading = True
            for key, c in zip(("c_before", "c_after", "c_sham", "c_amfonly"), palette_group_colors(pal)):
                self.w[key].set(c)
            self._loading = False
        self._palette = pal
        QSettings().setValue("plots/style", json.dumps(self.values()))
        self.changed.emit()

    # ------------------------------------------------------------------ presets
    def _user_presets(self):
        try:
            return json.loads(QSettings().value("plots/style_presets", "") or "{}")
        except ValueError:
            return {}

    def _fill_presets(self, current=None):
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("Choose a style…", None)
        for name in PRESETS:
            self.preset.addItem(name, ("builtin", name))
        for name in sorted(self._user_presets()):
            self.preset.addItem(f"★ {name}", ("user", name))
        i = self.preset.findData(current) if current else 0
        self.preset.setCurrentIndex(max(i, 0))
        self.preset.blockSignals(False)
        self.del_btn.setEnabled(bool(current and current[0] == "user"))

    def _preset_chosen(self, _i):
        d = self.preset.currentData()
        self.del_btn.setEnabled(bool(d and d[0] == "user"))
        if not d:
            return
        kind, name = d
        S = {**DEFAULTS, **(PRESETS[name] if kind == "builtin" else self._user_presets().get(name, {}))}
        if kind == "builtin" and "c_before" not in PRESETS[name]:
            S.update(zip(("c_before", "c_after", "c_sham", "c_amfonly"), palette_group_colors(S["palette"])))
        self._palette = S["palette"]
        if S["font"] not in font_names():
            S["font"] = DEFAULTS["font"] if not font_names() else ("Arial" if "Arial" in font_names() else DEFAULTS["font"])
        self.set_values(S)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Save style", "Name for this style:")
        name = (name or "").strip()
        if not ok or not name:
            return
        p = self._user_presets()
        p[name] = self.values()
        QSettings().setValue("plots/style_presets", json.dumps(p))
        self._fill_presets(("user", name))

    def delete_preset(self):
        d = self.preset.currentData()
        if not d or d[0] != "user":
            return
        if QMessageBox.question(self, "Delete style", f"Delete the style ‘{d[1]}’?") != QMessageBox.Yes:
            return
        p = self._user_presets()
        p.pop(d[1], None)
        QSettings().setValue("plots/style_presets", json.dumps(p))
        self._fill_presets()


# ---------------------------------------------------------------------------------------------- dragging labels
class DragLabels:
    """Move titles, axis labels, legends and texts of a figure with the mouse; moves survive redraws."""

    def __init__(self, canvas, busy=lambda: False):
        self.canvas = canvas
        self.busy = busy            # e.g. the toolbar is in zoom / pan mode
        self.moves = {}             # key -> (kind, value)
        self.items = {}             # key -> artist (current figure)
        self.drag = None
        canvas.mpl_connect("button_press_event", self._press)
        canvas.mpl_connect("motion_notify_event", self._motion)
        canvas.mpl_connect("button_release_event", self._release)

    def reset(self):
        self.moves = {}

    # ---- naming the movable artists of a freshly drawn figure, and re-applying earlier moves
    def tag(self, fig, axs):
        self.items = {}
        if fig._suptitle is not None:
            self.items["suptitle"] = fig._suptitle
        if getattr(fig, "_supylabel", None) is not None:
            self.items["supylabel"] = fig._supylabel
        for k, leg in enumerate(fig.legends):
            self.items[f"figlegend{k}"] = leg
        for i, a in enumerate(axs):
            if a.get_title():
                self.items[f"title{i}"] = a.title
            if a.get_xlabel():
                self.items[f"xlabel{i}"] = a.xaxis.label
            if a.get_ylabel():
                self.items[f"ylabel{i}"] = a.yaxis.label
            if a.get_legend() is not None:
                self.items[f"legend{i}"] = a.get_legend()
            for j, t in enumerate(a.texts):
                self.items[f"text{i}_{j}"] = t
        for key, (kind, val) in self.moves.items():
            art = self.items.get(key)
            if art is not None:
                try:
                    self._set(key, art, kind, val)
                except Exception:
                    pass

    def _set(self, key, art, kind, val):
        from matplotlib.legend import Legend
        from matplotlib.text import Annotation
        if kind == "label":
            ax = art.axes
            (ax.xaxis if key.startswith("xlabel") else ax.yaxis).set_label_coords(*val)
        elif kind == "legend" and isinstance(art, Legend):
            art.set_loc(tuple(val))
        elif kind == "ann" and isinstance(art, Annotation):
            art.anncoords = "offset points"
            art.xyann = tuple(val)
        elif kind == "pos":
            art.set_position(tuple(val))
            if key.startswith("title"):
                art.axes._autotitlepos = False
            if hasattr(art, "_autopos"):
                art._autopos = False

    # ---- mouse
    def _hit(self, event):
        from matplotlib.legend import Legend
        for key, art in sorted(self.items.items(), key=lambda kv: not isinstance(kv[1], Legend)):
            try:
                if art.get_visible() and art.contains(event)[0]:
                    return key, art
            except Exception:
                continue
        return None

    def _press(self, event):
        if event.button != 1 or self.busy():
            return
        h = self._hit(event)
        if h is None:
            return
        key, art = h
        r = self.canvas.get_renderer()
        bb = art.get_window_extent(r)
        self.drag = dict(key=key, art=art, x0=event.x, y0=event.y, bb=bb)

    def _motion(self, event):
        if self.drag is None or event.x is None:
            if self.drag is None and not self.busy():
                h = self._hit(event) if event.x is not None else None
                self.canvas.setCursor(Qt.OpenHandCursor if h else Qt.ArrowCursor)
            return
        self.canvas.setCursor(Qt.ClosedHandCursor)
        d = self.drag
        self._move_to(d, event.x - d["x0"], event.y - d["y0"])
        self.canvas.draw_idle()

    def _release(self, event):
        if self.drag is None:
            return
        d, self.drag = self.drag, None
        if event.x is not None:
            self._move_to(d, event.x - d["x0"], event.y - d["y0"], record=True)
            self.canvas.draw_idle()
        self.canvas.setCursor(Qt.OpenHandCursor)

    def _move_to(self, d, dx, dy, record=False):
        """Place the artist so that its box is (dx, dy) pixels from where the drag started."""
        from matplotlib.legend import Legend
        from matplotlib.text import Annotation
        key, art, bb = d["key"], d["art"], d["bb"]
        fig = art.get_figure()
        if isinstance(art, Legend):
            ref = art.get_bbox_to_anchor()
            x = (bb.x0 + dx - ref.x0) / ref.width
            y = (bb.y0 + dy - ref.y0) / ref.height
            val, kind = (x, y), "legend"
        elif isinstance(art, Annotation):
            r = self.canvas.get_renderer()
            if "start" not in d:
                d["start"] = np.asarray(art.get_transform().transform(art.get_position()), float)
                d["anchor"] = np.asarray(art._get_position_xy(r), float)
            p = d["start"] + (dx, dy) - d["anchor"]
            val, kind = tuple(p * 72.0 / fig.dpi), "ann"
        elif key.startswith(("xlabel", "ylabel")):
            ax = art.axes
            if "start" not in d:
                d["start"] = np.asarray(art.get_transform().transform(art.get_position()), float)
            p = ax.transAxes.inverted().transform(d["start"] + (dx, dy))
            val, kind = tuple(p), "label"
        else:
            if "start" not in d:
                d["start"] = np.asarray(art.get_transform().transform(art.get_position()), float)
            p = art.get_transform().inverted().transform(d["start"] + (dx, dy))
            val, kind = tuple(p), "pos"
        self._set(key, art, kind, val)
        if record:
            self.moves[key] = (kind, [float(v) for v in val])
