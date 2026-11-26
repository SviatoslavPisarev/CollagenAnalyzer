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

from ml import patterns as PT
from .widgets import Page, button, hint, banner, hbox
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

        g = QGroupBox("4. Save")
        gl = QVBoxLayout(g)
        gl.addWidget(hbox(button("Export results (Excel)…", slot=self.export)))
        gl.addWidget(hint("Sheets: tile types (profiles), share per file, change per sample, and every tile with its "
                          "type."))
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
        l.addWidget(FigureCanvasQTAgg(self.prof_fig), 2)
        self.prof_table = _table()
        l.addWidget(self.prof_table, 1)
        l.addWidget(hint("Colours: how far each type's average is from the average of all tiles, in standard "
                         "deviations (z). The names are made from the strongest differences."))
        self.tabs.addTab(w, "Tile types")
        # before / after
        w = QWidget(); l = QVBoxLayout(w)
        self.ba_fig = Figure(figsize=(8, 4.2), layout="constrained")
        l.addWidget(FigureCanvasQTAgg(self.ba_fig), 2)
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
        l.addWidget(hbox(QLabel("File"), self.map_file, QLabel("Image"), self.map_img, self.map_pair))
        self.map_fig = Figure(figsize=(8, 4.5), layout="constrained")
        l.addWidget(FigureCanvasQTAgg(self.map_fig), 1)
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
        l.addWidget(hbox(QLabel("View"), self.emb_method, QLabel("Colour by"), self.emb_color))
        self.emb_fig = Figure(figsize=(7, 5), layout="constrained")
        l.addWidget(FigureCanvasQTAgg(self.emb_fig), 1)
        l.addWidget(hint("Each dot is a tile (up to 4000 shown). Tiles close together have similar structure."))
        self.tabs.addTab(w, "Tile space")
        # k
        w = QWidget(); l = QVBoxLayout(w)
        self.k_fig = Figure(figsize=(6, 4), layout="constrained")
        l.addWidget(FigureCanvasQTAgg(self.k_fig), 1)
        l.addWidget(hint("BIC for each number of types: the lowest value balances fit against complexity. Prefer a "
                         "small k you can interpret if the curve is flat."))
        self.tabs.addTab(w, "Choosing k")
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
                                        "of this version (Outputs page → per-tile table).")
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

    def clear(self):
        self.tiles, self.sources, self.res = pd.DataFrame(), [], None
        self._summary()

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
        self.tabs.setCurrentIndex(0)

    def export(self):
        if not self.res:
            QMessageBox.information(self, "Export", "Find tile types first.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export patterns", "tile_patterns.xlsx", "Excel (*.xlsx)")
        if not p:
            return
        r = self.res
        try:
            with pd.ExcelWriter(p, engine="openpyxl") as xw:
                r["prof"].to_excel(xw, sheet_name="tile types", index=False)
                r["shares"].drop(columns=["_tiles_csv"], errors="ignore").to_excel(xw, sheet_name="share per file",
                                                                                   index=False)
                r["change"].to_excel(xw, sheet_name="change per sample", index=False)
                r["used"].drop(columns=["_tiles_csv"], errors="ignore").to_excel(xw, sheet_name="tiles", index=False)
                pd.DataFrame([("features", ", ".join(r["feats"])), ("method", self.method.currentText()),
                              ("k", self.k.value()), ("log scale for sizes", self.log_sizes.isChecked()),
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
