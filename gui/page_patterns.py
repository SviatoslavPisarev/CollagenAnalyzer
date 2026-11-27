"""Patterns: cluster the per-tile table into structure types and compare their shares before vs after AMF."""
import glob
import os

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QScrollArea, QSpinBox, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from ml import correlate as CR
from ml import patterns as PT
from .widgets import Page, button, hint, banner, hbox
from .widgets import FigurePanel
from . import style

COLORS = ["#3b6fb6", "#d9622b", "#2a9d5c", "#8e44ad", "#c0392b", "#e0a800", "#17a2b8", "#6d4c41", "#ad1457",
          "#7f8c8d"]


class PatternsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Patterns", "Groups the tiles of all images into structure types by their metrics alone "
                         "(unsupervised), then shows whether AMF changes how common each type is and where the "
                         "types sit in the image.", scroll=False)
        self.tiles = pd.DataFrame()
        self.sources = []
        self.res = None            # results of the last clustering
        self.corr = None           # results of the last correlation search
        try:
            import sklearn  # noqa: F401
            self.have_sklearn = True
        except ImportError:
            self.have_sklearn = False

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.lay.addWidget(split, 1)
        ctrl = QWidget()
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(0, 0, 8, 0)
        cl.setSpacing(10)

        g = QGroupBox("1. Tiles")
        gl = QVBoxLayout(g)
        gl.addWidget(hbox(button("Load tile tables…", "primary", slot=self.load_dialog),
                          button("Load results folder…", slot=self.load_folder_dialog),
                          button("Clear", "link", slot=self.clear)))
        self.src_label = hint("Nothing loaded. The tile table (mmddyy_tiles.csv) of each run is added automatically.")
        gl.addWidget(self.src_label)
        self.excl_tiles = QCheckBox("Leave out tiles flagged dark or blurred")
        self.excl_qc = QCheckBox("Leave out files the quality check marked ‘check’")
        for cb in (self.excl_tiles, self.excl_qc):
            cb.setChecked(True)
            gl.addWidget(cb)
        cl.addWidget(g)

        g = QGroupBox("2. What describes a tile")
        gl = QVBoxLayout(g)
        self.feat_list = QListWidget()
        self.feat_list.setObjectName("plain")
        for f, name, words, _ in PT.FEATURES:
            it = QListWidgetItem(f"{name}   ({words[0]} ↔ {words[1]})")
            it.setData(Qt.UserRole, f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f in PT.DEFAULT_FEATURES else Qt.Unchecked)
            self.feat_list.addItem(it)
        self.feat_list.setMaximumHeight(190)
        gl.addWidget(self.feat_list)
        self.log_sizes = QCheckBox("Log scale for pore size, fiber thickness and reflectance")
        self.log_sizes.setChecked(True)
        gl.addWidget(self.log_sizes)
        gl.addWidget(hint("Reflectance depends on laser power and gain: include it only if those were the same for "
                          "every file. The fiber angle is left out on purpose (it depends on how the gel lies)."))
        cl.addWidget(g)

        g = QGroupBox("3. Group the tiles")
        f = QFormLayout(g)
        self.method = QComboBox()
        self.method.addItem("Gaussian mixture (recommended)", "gmm")
        self.method.addItem("k-means", "kmeans")
        self.k = QSpinBox()
        self.k.setRange(2, 10)
        self.k.setValue(3)
        f.addRow("Method", self.method)
        f.addRow("Number of types (k)", hbox(self.k, button("Suggest k", "link", slot=self.suggest_k,
                                                           tip="Compares k = 2…8 by BIC (lower = better balance of fit "
                                                               "and simplicity)")))
        self.run_btn = button("Find tile types", "primary", slot=self.run)
        self.run_btn.setEnabled(self.have_sklearn)
        f.addRow(self.run_btn)
        cl.addWidget(g)
        if not self.have_sklearn:
            cl.addWidget(banner("scikit-learn is not installed (pip install scikit-learn): clustering is not available."))

        g = QGroupBox("4. Strongest correlations")
        f = QFormLayout(g)
        self.corr_level = QComboBox()
        for k, lab in CR.LEVELS:
            self.corr_level.addItem(lab, k)
        self.corr_level.setMinimumContentsLength(24)
        self.corr_level.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.corr_hide = QCheckBox("Hide pairs linked by how they are measured")
        self.corr_hide.setChecked(True)
        self.corr_hide.setToolTip("e.g. fiber density ↔ pore size, alignment S ↔ coherency: these move together "
                                  "by construction, so they would always top the list.")
        self.corr_level.currentIndexChanged.connect(lambda *_: self.find_correlations(show=True))
        self.corr_hide.toggled.connect(lambda *_: self.find_correlations(show=True))
        f.addRow("Look at", self.corr_level)
        f.addRow(self.corr_hide)
        f.addRow(button("Find strongest correlations", "primary", slot=lambda: self.find_correlations(show=True),
                        tip="Ranks every pair by Spearman's ρ (uses the metrics ticked in section 2). "
                            "No scikit-learn needed."))
        f.addRow(hint("Runs by itself when tiles are loaded. With tile types found, the change of each type's "
                      "share is tested against the AMF settings too."))
        cl.addWidget(g)

        g = QGroupBox("5. Save")
        gl = QVBoxLayout(g)
        gl.addWidget(hbox(button("Export results (Excel)…", slot=self.export)))
        gl.addWidget(hint("Sheets: tile types (profiles), share per file, change per sample, every tile with its "
                          "type, and the ranked correlations. Each graph has its own Copy / Save… / Full screen "
                          "buttons (or double-click it)."))
        cl.addWidget(g)
        self.status = hint("")
        cl.addWidget(self.status)
        cl.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(ctrl)
        sa.setMinimumWidth(470)
        split.addWidget(sa)

        # ---------------------------------------------------------------- results
        self.tabs = QTabWidget()
        # types
        w = QWidget(); l = QVBoxLayout(w)
        self.prof_fig = Figure(figsize=(8, 3.6), layout="constrained")
        l.addWidget(FigurePanel(self.prof_fig, name="tile_types"), 2)
        self.prof_table = _table()
        l.addWidget(self.prof_table, 1)
        l.addWidget(hint("Colours: how far each type's average is from the average of all tiles, in standard "
                         "deviations (z). The names are made from the strongest differences."))
        self.tabs.addTab(w, "Tile types")
        # before / after
        w = QWidget(); l = QVBoxLayout(w)
        self.ba_fig = Figure(figsize=(8, 4.2), layout="constrained")
        l.addWidget(FigurePanel(self.ba_fig, name="tile_types_before_after"), 2)
        self.ba_label = QLabel("")
        self.ba_label.setWordWrap(True)
        self.ba_label.setTextFormat(Qt.RichText)
        self.ba_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.ba_label)
        self.ba_table = _table()
        l.addWidget(self.ba_table, 1)
        self.tabs.addTab(w, "Before vs after")
        # tile map
        w = QWidget(); l = QVBoxLayout(w)
        self.map_file, self.map_img = QComboBox(), QComboBox()
        self.map_file.setMinimumContentsLength(30)
        self.map_file.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.map_file.currentIndexChanged.connect(self._fill_images)
        self.map_img.currentIndexChanged.connect(self.draw_map)
        self.map_pair = QCheckBox("show the sample's before and after file side by side")
        self.map_pair.setChecked(True)
        self.map_pair.toggled.connect(self.draw_map)
        self.map_fig = Figure(figsize=(8, 4.5), layout="constrained")
        l.addWidget(FigurePanel(self.map_fig, name=lambda: "tile_map_" + str(self.map_file.currentData() or ""),
                                controls=[QLabel("File"), self.map_file, QLabel("Image"), self.map_img,
                                          self.map_pair]), 1)
        l.addWidget(hint("Each square is one tile at its place in the image, coloured by its type; white = tile not "
                         "used (image edge, hotspot, flagged or missing values)."))
        self.tabs.addTab(w, "Tile map")
        # embedding
        w = QWidget(); l = QVBoxLayout(w)
        self.emb_method, self.emb_color = QComboBox(), QComboBox()
        self.emb_method.addItem("PCA (fast)", "pca")
        self.emb_method.addItem("t-SNE (slower, shows groups better)", "tsne")
        for k, lab in (("cluster", "tile type"), ("condition", "before / after AMF"), ("sample", "sample"),
                       ("z_um", "depth (z)")):
            self.emb_color.addItem(lab, k)
        self.emb_method.currentIndexChanged.connect(self._embed_changed)
        self.emb_color.currentIndexChanged.connect(self.draw_embedding)
        self.emb_fig = Figure(figsize=(7, 5), layout="constrained")
        l.addWidget(FigurePanel(self.emb_fig, name="tile_space", controls=[QLabel("View"), self.emb_method,
                                                                           QLabel("Colour by"), self.emb_color]), 1)
        l.addWidget(hint("Each dot is a tile (up to 4000 shown). Tiles close together have similar structure."))
        self.tabs.addTab(w, "Tile space")
        # k
        w = QWidget(); l = QVBoxLayout(w)
        self.k_fig = Figure(figsize=(6, 4), layout="constrained")
        l.addWidget(FigurePanel(self.k_fig, name="choosing_k"), 1)
        l.addWidget(hint("BIC for each number of types: the lowest value balances fit against complexity. Prefer a "
                         "small k you can interpret if the curve is flat."))
        self.tabs.addTab(w, "Choosing k")
        # correlations
        w = QWidget(); l = QVBoxLayout(w)
        self.corr_label = QLabel("Load tile tables: the strongest correlations are listed here.")
        self.corr_label.setWordWrap(True)
        self.corr_label.setTextFormat(Qt.RichText)
        self.corr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.corr_label)
        csplit = QSplitter(Qt.Vertical)
        csplit.setChildrenCollapsible(False)
        self.corr_table = _table()
        self.corr_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.corr_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.corr_table.itemSelectionChanged.connect(self.draw_correlation)
        csplit.addWidget(self.corr_table)
        self.corr_fig = Figure(figsize=(8, 4), layout="constrained")
        self.corr_panel = FigurePanel(self.corr_fig, name=self._corr_name)
        csplit.addWidget(self.corr_panel)
        csplit.setSizes([230, 420])
        l.addWidget(csplit, 1)
        l.addWidget(hint("Ranked by the size of Spearman's ρ (−1…+1; any steadily rising or falling relation). "
                         "n = independent units (samples, files, images or z-stacks — never single tiles). "
                         "q = p corrected for testing many pairs: below 0.05 unlikely to be chance. A correlation "
                         "is a lead to test, not proof of cause. Click a row to plot it."))
        self.CORR_TAB = self.tabs.addTab(w, "Correlations")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 820])

    # ------------------------------------------------------------------ data
    def load_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        ps, _ = QFileDialog.getOpenFileNames(self, "Tile tables", start, "Tile tables (*_tiles.csv)")
        if ps:
            self.load_paths(ps)

    def load_folder_dialog(self):
        start = QSettings().value("plots/last_dir", os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "Folder with results (searched including subfolders)", start)
        if d:
            ps = glob.glob(os.path.join(d, "**", "*_tiles.csv"), recursive=True)
            if not ps:
                QMessageBox.information(self, "Patterns", "No mmddyy_tiles.csv found. Tile tables are written by runs "
                                        "of this version (Outputs step → per-tile table).")
            self.load_paths(ps)

    def load_paths(self, paths):
        paths = [os.path.abspath(p) for p in paths]
        new = [p for p in paths if p not in self.sources]
        if not new:
            return
        try:
            t = PT.load_tiles(new)
        except Exception as e:
            QMessageBox.warning(self, "Patterns", f"Could not read the tile table(s):\n{e}")
            return
        self.sources += new
        self.tiles = PT.load_tiles(self.sources) if len(self.tiles) else t
        self.res = None
        self._summary()
        self.find_correlations()

    def clear(self):
        self.tiles, self.sources, self.res, self.corr = pd.DataFrame(), [], None, None
        self._summary()
        self.corr_label.setText("Load tile tables: the strongest correlations are listed here.")
        _fill(self.corr_table, pd.DataFrame(), [])
        self.corr_fig.clear()
        self.corr_fig.canvas.draw_idle()

    def _summary(self):
        if not len(self.tiles):
            self.src_label.setText("Nothing loaded. The tile table (mmddyy_tiles.csv) of each run is added "
                                   "automatically.")
            return
        t = self.tiles
        self.src_label.setText(f"{len(t)} tiles from {t['file'].nunique()} file(s), {t['sample'].nunique()} "
                               f"sample(s), {len(self.sources)} table(s).")

    def features(self):
        out = []
        for i in range(self.feat_list.count()):
            it = self.feat_list.item(i)
            if it.checkState() == Qt.Checked and it.data(Qt.UserRole) in self.tiles:
                out.append(it.data(Qt.UserRole))
        return out

    def _prepare(self):
        if not len(self.tiles):
            raise ValueError("Load tile tables first (mmddyy_tiles.csv).")
        feats = self.features()
        if len(feats) < 2:
            raise ValueError("Tick at least two metrics (section 2).")
        sel, notes = PT.select(self.tiles, self.excl_tiles.isChecked(), self.excl_qc.isChecked())
        Z, idx, mu, sd = PT.matrix(sel, feats, self.log_sizes.isChecked())
        if len(Z) < 20:
            raise ValueError(f"Only {len(Z)} usable tiles — too few to find types.")
        dropped = len(sel) - len(Z)
        if dropped:
            notes.append(f"{dropped} tile(s) with a missing value left out")
        return feats, sel, Z, idx, notes

    # ------------------------------------------------------------------ actions
    def suggest_k(self):
        try:
            feats, sel, Z, idx, notes = self._prepare()
        except ValueError as e:
            QMessageBox.information(self, "Patterns", str(e))
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            bic = PT.choose_k(Z)
        finally:
            QGuiApplication.restoreOverrideCursor()
        best = min(bic, key=bic.get)
        self.k.setValue(best)
        fig = self.k_fig
        fig.clear()
        ax = fig.add_subplot(111)
        ax.plot(list(bic), list(bic.values()), "o-", color=style.ACCENT)
        ax.axvline(best, color=style.AFTER, ls="--")
        ax.set_xlabel("number of tile types k")
        ax.set_ylabel("BIC (lower = better)")
        fig.canvas.draw_idle()
        self.status.setText(f"BIC is lowest at k = {best} (set). See ‘Choosing k’.")
        self.tabs.setCurrentIndex(4)

    def run(self):
        try:
            feats, sel, Z, idx, notes = self._prepare()
        except ValueError as e:
            QMessageBox.information(self, "Patterns", str(e))
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            lab, prob = PT.cluster(Z, self.k.value(), self.method.currentData())
            lab = PT.relabel(lab, Z, feats)
            used = sel.loc[idx].copy()
            used["cluster"] = lab + 1
            used["cluster_probability"] = prob
            prof = PT.profiles(Z, lab, used, feats)
            sh = PT.shares(used)
            ch = PT.paired_change(sh)
        except Exception as e:
            QMessageBox.warning(self, "Patterns", f"Clustering failed:\n{e}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.res = dict(feats=feats, Z=Z, used=used, prof=prof, shares=sh, change=ch, emb=None)
        self.status.setText(f"{len(used)} tiles grouped into {self.k.value()} types."
                            + ("\n" + "; ".join(notes) if notes else ""))
        self.draw_profiles()
        self.draw_before_after()
        self._fill_files()
        self._embed_changed()
        self.find_correlations()
        self.tabs.setCurrentIndex(0)

    def find_correlations(self, show=False):
        """Rank the correlations at the chosen level; show=True also switches to the Correlations tab."""
        if not len(self.tiles):
            if show:
                QMessageBox.information(self, "Patterns", "Load tile tables first (mmddyy_tiles.csv).")
            return
        level = self.corr_level.currentData()
        feats = self.features()
        need = 1 if level in ("settings", "depth") else 2
        if len(feats) < need:
            self.corr_label.setText("Tick at least two metrics in section 2.")
            return
        sel, _ = PT.select(self.tiles, self.excl_tiles.isChecked(), self.excl_qc.isChecked())
        change = self.res["change"] if self.res else None
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            table, data = CR.run(level, sel, feats, change, hide_linked=self.corr_hide.isChecked())
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Patterns", f"Correlations failed:\n{e}")
            return
        QGuiApplication.restoreOverrideCursor()
        self.corr = dict(level=level, table=table, data=data, sel=sel, feats=feats)
        lines = CR.headline(table)
        if lines:
            txt = "<b>Strongest correlations</b> — " + self.corr_level.currentText() + ":<ol style='margin:2px'>" \
                  + "".join(f"<li>{x}</li>" for x in lines) + "</ol>"
            if (table["q"] >= 0.1).all():
                txt += ("<i>None is clearly beyond chance yet (all q ≥ 0.1): treat these as leads and add samples."
                        "</i>")
        else:
            txt = {"settings": "No sample has a Before- and an After-AMF file with settings that differ between "
                               "samples (need at least 4 samples with two or more values of a setting).",
                   "depth": "No file has images at 4 or more depths (z-stacks).",
                   }.get(level, "Not enough files or images to correlate (need at least 5).")
        self.corr_label.setText(txt)
        t = table
        rows = pd.DataFrame({
            "#": np.arange(1, len(t) + 1),
            "relation": [f"{y}  ↔  {x}" for x, y in zip(t.get("x_label", []), t.get("y_label", []))],
            "rho": [f"{v:+.2f}" for v in t.get("rho", [])],
            "strength": t.get("strength", []),
            "n": [f"{int(v)} {u}" for v, u in zip(t.get("n", []), t.get("unit_of_n", []))],
            "q": t.get("q", []),
            "note": [("linked by how it is measured; " if lk else "") + str(e)
                     for lk, e in zip(t.get("linked", []), t.get("extra", []))]})
        _fill(self.corr_table, rows, ["#", "Relation", "ρ", "Strength", "n", "q", "Note"])
        if len(t):
            self.corr_table.selectRow(0)
        else:
            self.corr_fig.clear()
            self.corr_fig.canvas.draw_idle()
        if show:
            self.tabs.setCurrentIndex(self.CORR_TAB)

    def _corr_name(self):
        r = self._corr_row()
        return f"correlation_{r['y']}_vs_{r['x']}" if r is not None else "correlation"

    def _corr_row(self):
        if not self.corr or not len(self.corr["table"]):
            return None
        rows = self.corr_table.selectionModel().selectedRows() if self.corr_table.selectionModel() else []
        i = rows[0].row() if rows else 0
        return self.corr["table"].iloc[min(i, len(self.corr["table"]) - 1)]

    def draw_correlation(self):
        fig = self.corr_fig
        fig.clear()
        r = self._corr_row()
        if r is None:
            fig.canvas.draw_idle()
            return
        c, x, y = self.corr, r["x"], r["y"]
        title = f"ρ = {r['rho']:+.2f} ({r['strength']}), n = {int(r['n'])} {r['unit_of_n']}, q = {r['q']:.2g}"

        def cond_color(v):
            return style.AFTER if str(v).startswith("After") else style.BEFORE

        if c["level"] in ("settings", "files"):
            d = c["data"][[x, y] + [k for k in ("sample", "condition", "arm") if k in c["data"]]].copy()
            d[[x, y]] = d[[x, y]].apply(pd.to_numeric, errors="coerce")
            d = d.dropna(subset=[x, y])
            ax = fig.add_subplot(111)
            if c["level"] == "files" and "condition" in d:
                for cond, g in d.groupby(d["condition"].astype(str).str.startswith("After")):
                    ax.scatter(g[x], g[y], s=36, color=style.AFTER if cond else style.BEFORE,
                               label="after AMF" if cond else "before AMF", edgecolors="white", linewidths=0.5,
                               zorder=3)
                ax.legend(fontsize=8, frameon=False)
            else:
                ax.scatter(d[x], d[y], s=44, color=style.ACCENT, edgecolors="white", linewidths=0.5, zorder=3)
                for _, row in d.iterrows():
                    ax.annotate(str(row.get("sample", "")).split("_")[-1], (row[x], row[y]), fontsize=7,
                                xytext=(4, 3), textcoords="offset points", color="#555")
                ax.axhline(0, color="#999", lw=0.8, ls=":")
            if len(d) >= 3 and d[x].nunique() > 1:
                k, b0 = np.polyfit(d[x], d[y], 1)
                xs = np.linspace(d[x].min(), d[x].max(), 50)
                ax.plot(xs, k * xs + b0, "--", color="#888", lw=1.2, zorder=2)
            ax.set_xlabel(r["x_label"])
            ax.set_ylabel(r["y_label"])
            ax.set_title(title, fontsize=10)
        elif c["level"] == "tiles":
            ax = fig.add_subplot(1, 2, 1)
            t = c["sel"][[x, y, "condition"]].copy()
            t[[x, y]] = t[[x, y]].apply(pd.to_numeric, errors="coerce")
            t = t.dropna(subset=[x, y])
            if len(t) > 4000:
                t = t.sample(4000, random_state=0)
            ax.scatter(t[x], t[y], s=4, alpha=0.35, c=[cond_color(v) for v in t["condition"]], lw=0)
            ax.set_xlabel(r["x_label"])
            ax.set_ylabel(r["y_label"])
            ax.set_title("tiles (up to 4000; blue before, orange after)", fontsize=9)
            for axis, col in (("x", x), ("y", y)):
                if PT.FEAT.get(col, (0, 0, 0, False))[3]:
                    getattr(ax, f"set_{axis}scale")("log")
            ax2 = fig.add_subplot(1, 2, 2)
            v = np.asarray(c["data"].get((x, y), c["data"].get((y, x), [])))
            ax2.hist(v, bins=np.linspace(-1, 1, 21), color=style.ACCENT, alpha=0.85)
            ax2.axvline(np.median(v) if len(v) else 0, color=style.AFTER, lw=2)
            ax2.axvline(0, color="#999", lw=0.8, ls=":")
            ax2.set_xlabel("ρ inside each image")
            ax2.set_ylabel("images")
            ax2.set_title(title, fontsize=9)
        else:                                            # depth
            img, per = c["data"]
            ax = fig.add_subplot(111)
            seen = set()
            for fname, _, cond in per.get(y, []):
                g = img[img["file"] == fname].sort_values("z_um")
                after = str(cond).startswith("After")
                lab = ("after AMF" if after else "before AMF") if after not in seen else None
                seen.add(after)
                ax.plot(g["z_um"], g[y], "-o", ms=3, lw=1, alpha=0.7, color=style.AFTER if after else style.BEFORE,
                        label=lab)
            ax.set_xlabel(r["x_label"])
            ax.set_ylabel(r["y_label"])
            ax.set_title(title + " (each line = one z-stack)", fontsize=10)
            if seen:
                ax.legend(fontsize=8, frameon=False)
        fig.canvas.draw_idle()

    def export(self):
        if not self.res and not (self.corr and len(self.corr["table"])):
            QMessageBox.information(self, "Export", "Find tile types or correlations first.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export patterns", "tile_patterns.xlsx", "Excel (*.xlsx)")
        if not p:
            return
        r = self.res
        try:
            with pd.ExcelWriter(p, engine="openpyxl") as xw:
                if r:
                    r["prof"].to_excel(xw, sheet_name="tile types", index=False)
                    r["shares"].drop(columns=["_tiles_csv"], errors="ignore").to_excel(
                        xw, sheet_name="share per file", index=False)
                    r["change"].to_excel(xw, sheet_name="change per sample", index=False)
                    r["used"].drop(columns=["_tiles_csv"], errors="ignore").to_excel(xw, sheet_name="tiles",
                                                                                     index=False)
                if self.corr and len(self.corr["table"]):
                    t = self.corr["table"]
                    t[["y_label", "x_label", "rho", "strength", "n", "unit_of_n", "p", "q", "linked", "extra", "x",
                       "y"]].rename(columns={"y_label": "metric", "x_label": "with", "unit_of_n": "n counts",
                                             "extra": "note"}).to_excel(xw, sheet_name="correlations", index=False)
                pd.DataFrame([("features", ", ".join(r["feats"] if r else self.corr["feats"])),
                              ("method", self.method.currentText() if r else ""),
                              ("k", self.k.value() if r else ""),
                              ("log scale for sizes", self.log_sizes.isChecked()),
                              ("correlations", self.corr_level.currentText() if self.corr else ""),
                              ("tile tables", "; ".join(self.sources))],
                             columns=["setting", "value"]).to_excel(xw, sheet_name="settings", index=False)
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Could not save:\n{e}")

    # ------------------------------------------------------------------ drawing
    def _cmap(self, k):
        return ListedColormap(COLORS[:k])

    def draw_profiles(self):
        r = self.res
        prof, feats = r["prof"], r["feats"]
        fig = self.prof_fig
        fig.clear()
        ax = fig.add_subplot(111)
        M = prof[[f"z_{f}" for f in feats]].to_numpy()
        lim = max(1.0, float(np.nanmax(np.abs(M))))
        im = ax.imshow(M, cmap="RdBu_r", vmin=-lim, vmax=lim, aspect="auto")
        ax.set_xticks(range(len(feats)), [PT.FEAT[f][1] for f in feats], rotation=25, ha="right", fontsize=8)
        ax.set_yticks(range(len(prof)), [f"{c}: {n}" for c, n in zip(prof["cluster"], prof["name"])], fontsize=8)
        for t in ax.get_yticklabels():
            t.set_color(COLORS[(int(t.get_text().split(":")[0]) - 1) % len(COLORS)])
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                ax.text(j, i, f"{M[i, j]:+.1f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(M[i, j]) > 0.6 * lim else "black")
        fig.colorbar(im, ax=ax, label="z (SD from the mean)", shrink=0.9)
        fig.canvas.draw_idle()
        cols = ["cluster", "name", "n_tiles", "share_pct"] + feats
        heads = ["Type", "Name", "Tiles", "Share (%)"] + [PT.FEAT[f][1] for f in feats]
        _fill(self.prof_table, prof[cols], heads)

    def draw_before_after(self):
        r = self.res
        sh, ch, k = r["shares"], r["change"], int(r["used"]["cluster"].max())
        fig = self.ba_fig
        fig.clear()
        ax = fig.add_subplot(111)
        sh = sh.sort_values(["date", "sample", "condition"], ascending=[True, True, False]).reset_index(drop=True)
        x, labels, pos, last = [], [], 0, None
        for _, row in sh.iterrows():
            key = (row["date"], row["sample"])
            if last is not None and key != last:
                pos += 0.6
            x.append(pos)
            labels.append(f"{str(row['sample']).split('_')[-1]} {'after' if str(row['condition']).startswith('After') else 'before'}")
            pos += 1
            last = key
        bottom = np.zeros(len(sh))
        for c in range(1, k + 1):
            v = sh[f"cluster_{c}_pct"].to_numpy()
            ax.bar(x, v, bottom=bottom, color=COLORS[(c - 1) % len(COLORS)], width=0.85, label=f"type {c}")
            bottom += v
        ax.set_xticks(x, labels, rotation=60, ha="right", fontsize=7)
        ax.set_ylabel("share of tiles (%)")
        ax.set_ylim(0, 100)
        ax.legend(fontsize=8, ncol=min(k, 5), loc="upper center", bbox_to_anchor=(0.5, 1.12), frameon=False)
        fig.canvas.draw_idle()
        if len(ch):
            cols = [c for c in ch.columns if c.endswith("_change_pp")]
            parts = []
            for c in cols:
                v = ch[c].to_numpy(float)
                n = int(np.isfinite(v).sum())
                m, s = np.nanmean(v), (np.nanstd(v, ddof=1) if n > 1 else np.nan)
                same = int((np.sign(v) == np.sign(m)).sum()) if m else 0
                parts.append(f"type {c.split('_')[1]}: {m:+.1f} pp" + (f" (SD {s:.1f}; same direction in {same} of "
                                                                           f"{n} samples)" if n > 1 else ""))
            self.ba_label.setText("<b>Change after AMF</b> (after − before share, percentage points, mean over "
                                  f"{len(ch)} sample(s)):<br>" + "<br>".join(parts)
                                  + "<br><i>One value per sample — tiles of one image are not independent, so "
                                    "use samples (gels) as replicates for statistics.</i>")
            _fill(self.ba_table, ch, ["Sample", "Date", "Before files", "After files"]
                  + [f"Type {c.split('_')[1]} change (pp)" for c in cols])
        else:
            self.ba_label.setText("No sample has both a Before-AMF and an After-AMF file among the tiles used.")
            _fill(self.ba_table, pd.DataFrame(), [])

    def _fill_files(self):
        u = self.res["used"]
        self.map_file.blockSignals(True)
        self.map_file.clear()
        for f in dict.fromkeys(u.sort_values(["date", "sample", "condition"], ascending=[True, True, False])["file"]):
            self.map_file.addItem(f, f)
        self.map_file.blockSignals(False)
        self._fill_images()

    def _fill_images(self, *_):
        if not self.res:
            return
        f = self.map_file.currentData()
        t = self.tiles[self.tiles["file"] == f]
        self.map_img.blockSignals(True)
        self.map_img.clear()
        for (ti, zi), g in t.groupby(["t_index", "z_index"]):
            zu = g["z_um"].iloc[0] if "z_um" in g else np.nan
            self.map_img.addItem(f"t {ti}, z {zi}" + (f" ({zu:.2f} µm)" if np.isfinite(zu) else ""), (int(ti), int(zi)))
        self.map_img.blockSignals(False)
        self.draw_map()

    def _partner(self, f):
        u = self.tiles
        r = u[u["file"] == f].iloc[0]
        same = u[(u["sample"] == r["sample"]) & (u["date"] == r["date"]) & (u["file"] != f)]
        other = same[same["condition"].astype(str).str.startswith("After") != str(r["condition"]).startswith("After")]
        return other["file"].iloc[0] if len(other) else None

    def draw_map(self, *_):
        fig = self.map_fig
        fig.clear()
        if not self.res or self.map_file.currentData() is None or self.map_img.currentData() is None:
            fig.canvas.draw_idle()
            return
        f = self.map_file.currentData()
        ti, zi = self.map_img.currentData()
        files = [f]
        if self.map_pair.isChecked():
            p = self._partner(f)
            if p:
                files = sorted([f, p], key=lambda x: str(self.tiles.loc[self.tiles["file"] == x, "condition"].iloc[0])
                               .startswith("After"))
        k = int(self.res["used"]["cluster"].max())
        cm = self._cmap(k)
        for i, fn in enumerate(files):
            ax = fig.add_subplot(1, len(files), i + 1)
            allt = self.tiles[self.tiles["file"] == fn]
            imgs = allt[["t_index", "z_index"]].drop_duplicates()
            # same image index in the partner file if it exists, else its first image
            if not ((imgs["t_index"] == ti) & (imgs["z_index"] == zi)).any():
                ti2, zi2 = int(imgs.iloc[0]["t_index"]), int(imgs.iloc[0]["z_index"])
            else:
                ti2, zi2 = ti, zi
            a = allt[(allt["t_index"] == ti2) & (allt["z_index"] == zi2)]
            u = self.res["used"]
            u = u[(u["file"] == fn) & (u["t_index"] == ti2) & (u["z_index"] == zi2)]
            nr, nc = int(allt["tile_row"].max()) + 1, int(allt["tile_col"].max()) + 1
            grid = np.full((nr, nc), np.nan)
            grid[u["tile_row"].astype(int), u["tile_col"].astype(int)] = u["cluster"] - 1
            step = float(np.median(np.diff(np.unique(allt["x_um"])))) if allt["x_um"].nunique() > 1 else 1.0
            ax.imshow(grid, cmap=cm, vmin=-0.5, vmax=k - 0.5, extent=(0, nc * step, nr * step, 0),
                      interpolation="nearest")
            ax.set_xlabel("x (µm)")
            ax.set_ylabel("y (µm)")
            cond = str(a["condition"].iloc[0]) if len(a) else ""
            ax.set_title(f"{cond}\n…{fn[-40:]}  (t {ti2}, z {zi2})", fontsize=8,
                         color=style.AFTER if cond.startswith("After") else style.BEFORE)
        fig.legend(handles=[Patch(color=COLORS[c % len(COLORS)], label=f"type {c + 1}") for c in range(k)],
                   loc="outside lower center", ncol=min(k, 6), fontsize=8, frameon=False)
        fig.canvas.draw_idle()

    def _embed_changed(self, *_):
        if not self.res:
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.res["emb"] = PT.embed(self.res["Z"], self.emb_method.currentData())
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.draw_embedding()

    def draw_embedding(self, *_):
        fig = self.emb_fig
        fig.clear()
        if not self.res or self.res.get("emb") is None:
            fig.canvas.draw_idle()
            return
        idx, Y, note = self.res["emb"]
        u = self.res["used"].iloc[idx]
        by = self.emb_color.currentData()
        ax = fig.add_subplot(111)
        if by == "cluster":
            k = int(self.res["used"]["cluster"].max())
            for c in range(1, k + 1):
                m = (u["cluster"] == c).to_numpy()
                ax.scatter(Y[m, 0], Y[m, 1], s=6, color=COLORS[(c - 1) % len(COLORS)], label=f"type {c}", alpha=0.7)
            ax.legend(fontsize=8, markerscale=2)
        elif by == "z_um" and "z_um" in u:
            sc = ax.scatter(Y[:, 0], Y[:, 1], s=6, c=pd.to_numeric(u["z_um"], errors="coerce"), cmap="viridis",
                            alpha=0.7)
            fig.colorbar(sc, ax=ax, label="z (µm)")
        else:
            vals = u[by].astype(str)
            for i, v in enumerate(dict.fromkeys(vals)):
                m = (vals == v).to_numpy()
                col = (style.AFTER if v.startswith("After") else style.BEFORE) if by == "condition" else \
                    COLORS[i % len(COLORS)]
                ax.scatter(Y[m, 0], Y[m, 1], s=6, color=col, label=v, alpha=0.6)
            ax.legend(fontsize=7, markerscale=2)
        ax.set_xlabel(note[0])
        ax.set_ylabel(note[1])
        fig.canvas.draw_idle()


def _table():
    t = QTableWidget(0, 0)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return t


def _fill(t, df, heads):
    t.clear()
    t.setRowCount(len(df))
    t.setColumnCount(len(df.columns))
    if heads:
        t.setHorizontalHeaderLabels(heads)
    for i, r in enumerate(df.itertuples(index=False)):
        for j, v in enumerate(r):
            if isinstance(v, (float, np.floating)):
                txt = "" if not np.isfinite(v) else f"{v:.3g}"
            else:
                txt = str(v)
            t.setItem(i, j, QTableWidgetItem(txt))
    t.resizeColumnsToContents()
