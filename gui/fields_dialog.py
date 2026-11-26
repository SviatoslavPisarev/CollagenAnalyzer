"""Dialog: default values for the sample information, and parameters added by the user."""
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .common import STD_FIELDS, FIELD_TIPS, fmt_value
from .widgets import button, hint, hbox


class FieldsDialog(QDialog):
    """cfg = {"defaults": {std_key: value}, "custom": [{id, label, unit, type, default}]}"""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sample information: defaults and added parameters")
        self.resize(840, 820)
        lay = QVBoxLayout(self)
        t = QLabel("Defaults and added parameters")
        t.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(t)
        lay.addWidget(hint(
            "A default is used for every file whose name does not contain the value and where you have not typed one "
            "in the table. Add your own parameters (e.g. gel thickness, NC type, replicate, temperature) — they get "
            "a column in the file table, in all spreadsheets, and can be used in Results & Plots. Everything here is "
            "remembered for future runs."))

        lay.addWidget(QLabel("<b>Standard information</b> (read from the file names)"))
        self.std = QTableWidget(len(STD_FIELDS), 3)
        self.std.setHorizontalHeaderLabels(["Information", "Unit", "Default when not in the file name"])
        self.std.verticalHeader().setVisible(False)
        self.std.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.std.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.std.setObjectName("plain")
        defaults = cfg.get("defaults", {})
        for i, (key, title, unit, typ, choices, *_) in enumerate(STD_FIELDS):
            a = QTableWidgetItem(title)
            a.setFlags(Qt.ItemIsEnabled)
            a.setToolTip(FIELD_TIPS.get(key, ""))
            a.setData(Qt.UserRole, key)
            b = QTableWidgetItem(unit)
            b.setFlags(Qt.ItemIsEnabled)
            self.std.setItem(i, 0, a)
            self.std.setItem(i, 1, b)
            if typ == "choice":
                cb = QComboBox()
                cb.addItems(["(none)"] + [c for c in choices if c])
                v = defaults.get(key)
                cb.setCurrentText(v if v else "(none)")
                self.std.setCellWidget(i, 2, cb)
            else:
                self.std.setItem(i, 2, QTableWidgetItem(fmt_value(defaults.get(key))))
        self.std.resizeRowsToContents()
        h = self.std.horizontalHeader().height() + sum(self.std.rowHeight(r) for r in range(self.std.rowCount())) + 6
        self.std.setMinimumHeight(h)
        self.std.setMaximumHeight(h)
        lay.addWidget(self.std)

        lay.addWidget(QLabel("<b>Added parameters</b>"))
        self.cus = QTableWidget(0, 4)
        self.cus.setHorizontalHeaderLabels(["Name", "Unit", "Type", "Default for all files"])
        self.cus.verticalHeader().setVisible(False)
        self.cus.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cus.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.cus.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cus.setObjectName("plain")
        for f in cfg.get("custom", []):
            self._add_row(f)
        lay.addWidget(self.cus, 1)
        lay.addWidget(hbox(button("Add parameter", "primary", slot=lambda: self._add_row(None, edit=True)),
                           button("Remove selected", "danger", slot=self._remove)))
        lay.addWidget(hint("Type ‘Number’ for values you may want to plot against (e.g. thickness 100); ‘Text’ for "
                           "labels (e.g. NC type DHCA). Removing a parameter also removes the values typed for it."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result_cfg = None

    def _add_row(self, f, edit=False):
        f = f or {"id": uuid.uuid4().hex[:8], "label": "", "unit": "", "type": "num", "default": None}
        r = self.cus.rowCount()
        self.cus.insertRow(r)
        name = QTableWidgetItem(f["label"])
        name.setData(Qt.UserRole, f["id"])
        self.cus.setItem(r, 0, name)
        self.cus.setItem(r, 1, QTableWidgetItem(f.get("unit", "")))
        cb = QComboBox()
        cb.addItems(["Number", "Text"])
        cb.setCurrentIndex(0 if f.get("type", "num") == "num" else 1)
        self.cus.setCellWidget(r, 2, cb)
        self.cus.setItem(r, 3, QTableWidgetItem(fmt_value(f.get("default"))))
        if edit:
            self.cus.setCurrentCell(r, 0)
            self.cus.editItem(name)

    def _remove(self):
        for r in sorted({i.row() for i in self.cus.selectedIndexes()}, reverse=True):
            self.cus.removeRow(r)

    @staticmethod
    def _num(text, what):
        t = text.strip().replace(",", ".")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            raise ValueError(f"{what}: ‘{text}’ is not a number")

    def _accept(self):
        try:
            defaults = {}
            for i, (key, title, unit, typ, *_rest) in enumerate(STD_FIELDS):
                if typ == "choice":
                    v = self.std.cellWidget(i, 2).currentText()
                    if v != "(none)":
                        defaults[key] = v
                else:
                    txt = self.std.item(i, 2).text() if self.std.item(i, 2) else ""
                    v = self._num(txt, title) if typ == "num" else (txt.strip() or None)
                    if v is not None:
                        defaults[key] = v
            custom, names = [], set()
            for r in range(self.cus.rowCount()):
                label = (self.cus.item(r, 0).text() if self.cus.item(r, 0) else "").strip()
                if not label:
                    continue
                if label.lower() in names:
                    raise ValueError(f"The parameter name ‘{label}’ is used twice.")
                names.add(label.lower())
                typ = "num" if self.cus.cellWidget(r, 2).currentIndex() == 0 else "text"
                txt = self.cus.item(r, 3).text() if self.cus.item(r, 3) else ""
                default = self._num(txt, label) if typ == "num" else (txt.strip() or None)
                custom.append(dict(id=self.cus.item(r, 0).data(Qt.UserRole) or uuid.uuid4().hex[:8], label=label,
                                   unit=(self.cus.item(r, 1).text() if self.cus.item(r, 1) else "").strip(),
                                   type=typ, default=default))
        except ValueError as e:
            QMessageBox.warning(self, "Check the values", str(e))
            return
        self.result_cfg = {"defaults": defaults, "custom": custom}
        self.accept()
