"""Quality check page: train a model that recognises problem files (drying, blur, …) from your own labels."""
import glob
import json
import os
import re

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, Signal, QSettings, QStandardPaths
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHeaderView, QLabel, QMessageBox, QTableWidget,
                               QTableWidgetItem, QAbstractItemView)

from ml import qc as QC
from .widgets import Page, Card, button, hint, banner, hbox
from . import style

LABELS = ["ok", "problem", "skip"]
DATE_SUMMARY = re.compile(r"^(\d{6}(-\d{6})?|other)_summary\.xlsx$")


def read_quality(path):
    """'quality' sheet of a result workbook (or its _quality.csv), with a _src column (the results folder)."""
    try:
        q = pd.read_excel(path, sheet_name="quality")
    except ValueError as e:                           # workbook without a quality sheet (made before QC existed)
        raise ValueError(f"{os.path.basename(path)} has no ‘quality’ sheet — analyse the files again with this "
                         "version of the app") from e
    except Exception:
        base = os.path.splitext(path)[0]
        if not os.path.exists(base + "_quality.csv"):
            raise
        q = pd.read_csv(base + "_quality.csv")
    q["file"] = q["file"].astype(str)
    for c in ("qc_flags", "other", "qc_status"):
        if c in q:
            q[c] = q[c].fillna("").astype(str)
    q["_src"] = os.path.dirname(os.path.abspath(path))
    return q


def default_model_path():
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~")
    return os.path.join(base, "models", "qc_model.joblib")


class ModelsPage(Page):
    model_saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Quality check", "Teach the app what a problem file looks like. Load results of earlier "
                         "runs, mark which files had problems (drying, bubbles, blur…), train, and the model is "
                         "applied to new runs together with the built-in checks.")
        self.q = pd.DataFrame()
        self.result = None
        try:
            self.labels = json.loads(QSettings().value("models/labels", "") or "{}")   # file -> label
        except ValueError:
            self.labels = {}
        try:
            import sklearn  # noqa: F401
            self.have_sklearn = True
        except ImportError:
            self.have_sklearn = False

        # --- how it works
        how = Card("How it works")
        how.lay.addWidget(hint(
            "1. Load the summary spreadsheets (mmddyy_summary.xlsx) of runs made with this version.\n"
            "2. Check the Label column. It starts from your file-name notes: a note such as _other-dried-edges_ or "
            "_other-bubble_ means ‘problem’, everything else ‘ok’. Change any label; ‘skip’ leaves a file out.\n"
            "3. Train. The model learns from the quality numbers of each file (sharpness, edge/centre brightness, "
            "dark tiles, …) and is tested by cross-validation: each file is predicted by a model that did not "
            "see it.\n"
            "4. Save. New runs then add the model's probability to the ‘quality’ sheet and flag likely problems."))
        how.lay.addWidget(hint("Aim for at least 10 problem and 10 ok files from several days. The built-in "
                               "rules work without any training."))
        if not self.have_sklearn:
            how.lay.addWidget(banner("scikit-learn is not installed, so models cannot be trained here "
                                     "(pip install scikit-learn). The built-in checks still run."))
        self.lay.addWidget(how)

        # --- training data
        data = Card("Training files")
        self.count = QLabel("No files loaded")
        self.count.setStyleSheet("font-weight: 700;")
        data.lay.addWidget(hbox(self.count, "stretch",
                                button("Add spreadsheets…", "primary", slot=self.add_files),
                                button("Add folder…", slot=self.add_folder,
                                       tip="Finds every mmddyy_summary.xlsx in the folder and its subfolders"),
                                button("Clear", slot=self.clear), stretch_end=False))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Label", "File", "Date", "Sample", "Note in name",
                                              "Built-in checks", "Model (cross-val.)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.table.setMinimumHeight(260)
        data.lay.addWidget(self.table, 1)
        data.lay.addWidget(hint("Labels you change are remembered (by file name)."))
        self.lay.addWidget(data, 1)

        # --- train
        tr = Card("Train and save")
        self.train_btn = button("Train model", "primary", slot=self.train)
        self.save_btn = button("Save model and use it for new runs…", slot=self.save)
        self.save_btn.setEnabled(False)
        self.train_btn.setEnabled(self.have_sklearn)
        tr.lay.addWidget(hbox(self.train_btn, self.save_btn))
        self.res = QLabel("Not trained yet.")
        self.res.setWordWrap(True)
        self.res.setTextFormat(Qt.RichText)
        self.res.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tr.lay.addWidget(self.res)
        tr.lay.addWidget(hint("Model files (.joblib) can contain program code: only use model files you made "
                              "yourself or got from someone you trust."))
        self.lay.addWidget(tr)

    # ------------------------------------------------------------------ loading
    def add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Result spreadsheets", "", "Summary spreadsheet (*.xlsx)")
        self.load(ps)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with results")
        if d:
            ps = [p for p in glob.glob(os.path.join(d, "**", "*_summary.xlsx"), recursive=True)
                  if DATE_SUMMARY.match(os.path.basename(p))]
            if not ps:
                QMessageBox.information(self, "Add folder", "No mmddyy_summary.xlsx found in that folder.")
            self.load(ps)

    def load(self, paths):
        errs, parts = [], [self.q] if len(self.q) else []
        for p in paths:
            try:
                parts.append(read_quality(p))
            except Exception as e:
                errs.append(f"{os.path.basename(p)}: {e}")
        if parts:
            q = pd.concat(parts, ignore_index=True, sort=False)
            # the same image analysed twice (re-run, or date + combined summary): keep the newest
            self.q = q.drop_duplicates(subset=["file"], keep="last").reset_index(drop=True)
            self.result = None
            self.save_btn.setEnabled(False)
            self.fill()
        if errs:
            QMessageBox.warning(self, "Some spreadsheets were not loaded", "\n".join(errs[:10]))

    def clear(self):
        self.q = pd.DataFrame()
        self.result = None
        self.save_btn.setEnabled(False)
        self.fill()

    def label_of(self, i):
        r = self.q.iloc[i]
        return self.labels.get(r["file"], r.get("label_from_note") or QC.auto_label(r.get("other")))

    def fill(self, cv=None):
        self.table.setRowCount(len(self.q))
        for i in range(len(self.q)):
            r = self.q.iloc[i]
            cb = QComboBox()
            cb.addItems(LABELS)
            cb.setCurrentText(self.label_of(i))
            cb.currentTextChanged.connect(lambda v, f=r["file"]: self._set_label(f, v))
            self.table.setCellWidget(i, 0, cb)
            pred = ""
            if cv is not None and r["file"] in cv:
                pred = "problem" if cv[r["file"]] else "ok"
            vals = [r["file"], str(r.get("date", "")), str(r.get("sample", "")), r.get("other", ""),
                    "⚠ " + r["qc_flags"] if r.get("qc_status") == "check" else "ok", pred]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(str(v))
                it.setToolTip(str(v))
                if c == 5 and r.get("qc_status") == "check":
                    it.setForeground(QBrush(QColor(style.WARN)))
                if c == 6 and pred and pred != self.label_of(i):
                    it.setForeground(QBrush(QColor(style.ERROR)))
                    it.setToolTip("The cross-validated model disagrees with the label: check this file (or its "
                                  "label).")
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, min(max(self.table.columnWidth(1), 200), 380))
        self.table.setColumnWidth(5, min(self.table.columnWidth(5), 320))
        self._count()

    def _set_label(self, f, v):
        self.labels[f] = v
        QSettings().setValue("models/labels", json.dumps(self.labels))
        self._count()

    def _count(self):
        if not len(self.q):
            self.count.setText("No files loaded")
            return
        labs = [self.label_of(i) for i in range(len(self.q))]
        self.count.setText(f"{len(labs)} file(s): {labs.count('ok')} ok, {labs.count('problem')} problem, "
                           f"{labs.count('skip')} skipped")

    # ------------------------------------------------------------------ training
    def train(self):
        if not len(self.q):
            QMessageBox.information(self, "Train", "Load some result spreadsheets first.")
            return
        labs = np.array([self.label_of(i) for i in range(len(self.q))])
        use = labs != "skip"
        X = self.q.loc[use].reindex(columns=QC.FEATURE_KEYS)
        y = (labs[use] == "problem").astype(int)
        try:
            self.result = QC.train(X, y)
        except Exception as e:
            QMessageBox.warning(self, "Train", str(e))
            return
        m = self.result
        (tn, fp), (fn, tp) = m["cv_confusion"]
        names = dict(QC.FEATURES)
        top = "".join(f"<li>{k} — {names[k]} ({v:.0%})</li>" for k, v in m["importances"][:5])
        acc = m["cv_balanced_accuracy"]
        verdict = ("good" if acc >= 0.85 else "fair — add more labelled files" if acc >= 0.7 else
                   "weak — the labelled problems may not be visible in these numbers, or there are too few")
        small = (" <b>Few examples:</b> treat this as a first try." if min(m["n_ok"], m["n_problem"]) < 10 else "")
        self.res.setText(
            f"Trained on <b>{m['n_ok']} ok</b> and <b>{m['n_problem']} problem</b> files.{small}<br>"
            f"Cross-validated ({m['cv_folds']}-fold) balanced accuracy: <b>{acc:.0%}</b> ({verdict}).<br>"
            f"Problem files found: {tp} of {tp + fn}; ok files wrongly flagged: {fp} of {tn + fp}.<br>"
            f"Most useful numbers:<ul>{top}</ul>"
            "Red entries in the last column are files where the model disagrees with your label.")
        files = list(self.q.loc[use, "file"])
        self.fill(cv=dict(zip(files, m["cv_pred"])))
        self.save_btn.setEnabled(True)

    def save(self):
        if not self.result:
            return
        start = QSettings().value("models/last_path", "") or default_model_path()
        os.makedirs(os.path.dirname(start), exist_ok=True)
        p, _ = QFileDialog.getSaveFileName(self, "Save quality-check model", start, "QC model (*.joblib)")
        if not p:
            return
        if not p.endswith(".joblib"):
            p += ".joblib"
        try:
            QC.save_model(p, self.result)
        except Exception as e:
            QMessageBox.warning(self, "Save model", f"Could not save the model:\n{e}")
            return
        QSettings().setValue("models/last_path", p)
        self.model_saved.emit(p)
        QMessageBox.information(self, "Model saved", f"Saved to\n{p}\n\nNew runs will apply it (Outputs step → "
                                "Quality check). Untick it there to run without it.")
