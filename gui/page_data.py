"""Page 1: choose a file, a folder or several folders, and which files to analyse."""
import os
import glob
import json

from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, QSettings, QRectF
from PySide6.QtGui import QColor, QBrush, QFont, QDoubleValidator, QPainter
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QAbstractItemView, QStyledItemDelegate, QComboBox, QMenu,
                               QMessageBox, QStyle)

import gelstats as GS

from .common import (IMAGE_EXT, parse_file_name, read_header, kind_text, STD_FIELDS, FIELD_TIPS, parsed_value,
                     field_key, fmt_value)
from .fields_dialog import FieldsDialog
from .widgets import Page, Card, ModeCard, button, hint, banner, hbox
from . import style
from . import ease

LEFT = ["", "File", "Folder", "Date", "Sample", "QC"]
RIGHT = ["Type", "Pixel (µm)"]
MODES = [("file", "One file", "Detailed report of a single .nd2 / .tif: a figure of every image, slice comparisons "
                              "and a spreadsheet."),
         ("folder", "One folder", "All image files of a folder (or the ones you tick): per-file results plus "
                                  "before/after and side-by-side comparisons."),
         ("folders", "Several folders", "Each folder analysed on its own, plus one combined summary.xlsx of all "
                                        "folders.")]
FILTERS = [("all", "All"), ("before", "Before"), ("after", "After AMF"), ("sham", "Sham"), ("check", "⚠ Check")]
PILL = {"before": ("BEFORE_BG", "BEFORE_FG"), "after": ("AFTER_BG", "AFTER_FG"), "sham": ("SHAM_BG", "SHAM_FG"),
        "amfonly": ("AMFONLY_BG", "AMFONLY_FG")}


class FieldDelegate(QStyledItemDelegate):
    """Editors for the sample-information columns: lists for choices, numbers for numeric fields."""

    def __init__(self, page):
        super().__init__(page.table)
        self.page = page

    def createEditor(self, parent, option, index):
        f = self.page.col_field.get(index.column())
        if f is None:
            return None
        typ, choices = f["type"], f.get("choices")
        if typ == "choice":
            cb = QComboBox(parent)
            cb.addItems([c if c else "(none)" for c in choices])
            return cb
        ed = QLineEdit(parent)
        if typ == "num":
            ed.setPlaceholderText("number, or empty")
        return ed

    def setEditorData(self, editor, index):
        txt = index.data(Qt.EditRole) or ""
        if isinstance(editor, QComboBox):
            editor.setCurrentText(txt if txt else "(none)")
        else:
            editor.setText(txt)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            t = editor.currentText()
            model.setData(index, "" if t == "(none)" else t, Qt.EditRole)
        else:
            model.setData(index, editor.text(), Qt.EditRole)

    def paint(self, painter, option, index):
        """Gels in alternating bands (files of one gel together); the condition as a coloured tag."""
        page = self.page
        path = index.sibling(index.row(), 0).data(Qt.UserRole)
        selected = bool(option.state & QStyle.State_Selected)
        if not selected and index.data(Qt.BackgroundRole) is None and page.band.get(path):
            painter.fillRect(option.rect, QColor(style.GEL_BAND))
        f = page.col_field.get(index.column())
        if f is None or f["key"] != "condition" or not index.data(Qt.DisplayRole):
            super().paint(painter, option, index)
            return
        if selected:
            painter.fillRect(option.rect, QColor(style.ACCENT_SOFT))
        text = index.data(Qt.DisplayRole)
        kind = style.pill_kind(text, page.arm.get(path, ""))
        bg, fg = (QColor(getattr(style, k)) for k in PILL[kind])
        label = text
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        fnt = QFont(option.font)
        fnt.setWeight(QFont.DemiBold)
        fnt.setPointSizeF(max(fnt.pointSizeF() * 0.92, 8))
        painter.setFont(fnt)
        w = painter.fontMetrics().horizontalAdvance(label) + 16
        h = min(option.rect.height() - 8, painter.fontMetrics().height() + 6)
        r = QRectF(option.rect.x() + 6, option.rect.center().y() - h / 2 + 0.5, min(w, option.rect.width() - 10), h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, 6, 6)
        painter.setPen(fg)
        painter.drawText(r, Qt.AlignCenter, label)
        painter.restore()


class ReadyPanel(QFrame):
    """Side panel of the Files step: what will be analysed and what to check first."""
    run_now = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFixedWidth(290)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(12)
        t = QLabel("Ready to run")
        t.setObjectName("cardTitle")
        lay.addWidget(t)
        nums = QHBoxLayout()
        nums.setSpacing(10)
        self.big = {}
        for key, lab in (("files", "files ticked"), ("gels", "gels")):
            box = QFrame()
            box.setObjectName("sunken")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            bl.setSpacing(0)
            v = QLabel("0")
            v.setObjectName("bigNumber")
            l = QLabel(lab)
            l.setObjectName("hint")
            bl.addWidget(v)
            bl.addWidget(l)
            nums.addWidget(box)
            self.big[key] = v
        lay.addLayout(nums)
        self.arm_box = QVBoxLayout()
        self.arm_box.setSpacing(6)
        lay.addLayout(self.arm_box)
        line = QFrame()
        line.setFixedHeight(1)
        line.setObjectName("sunken")
        lay.addWidget(line)
        self.check_box = QVBoxLayout()
        self.check_box.setSpacing(7)
        lay.addLayout(self.check_box)
        b = button("Skip to Run →", tip="Use the current settings and outputs and go straight to the Run step")
        b.clicked.connect(self.run_now.emit)
        lay.addWidget(b)

    @staticmethod
    def _clear(box):
        while box.count():
            it = box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                ReadyPanel._clear(it.layout())

    def _line(self, box, ok, text):
        row = QHBoxLayout()
        row.setSpacing(8)
        m = QLabel("✓" if ok else "!")
        m.setObjectName("checkOk" if ok else "checkWarn")
        m.setFixedWidth(10)
        t = QLabel(text)
        t.setWordWrap(True)
        t.setObjectName("" if ok else "checkTextWarn")
        t.setStyleSheet("font-size: 12px;")
        row.addWidget(m, 0, Qt.AlignTop)
        row.addWidget(t, 1)
        box.addLayout(row)

    def update_from(self, page, sel, gels):
        sel = set(sel)
        tg = {k: [(p, c, n) for p, c, n in v if p in sel] for k, v in gels.items()}
        tg = {k: v for k, v in tg.items() if v}
        self.big["files"].setText(str(len(sel)))
        self.big["gels"].setText(str(len(tg)))
        self._clear(self.arm_box)
        arm_n = {}
        for k, files in tg.items():
            a = page.arm.get(files[0][0], "")
            for one in (a.split(" / ") if a else ["no After file"]):
                arm_n[one] = arm_n.get(one, 0) + 1
        for a in GS.ARMS + ["no After file"]:
            if a not in arm_n:
                continue
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel()
            dot.setObjectName("dot")
            dot.setStyleSheet(f"background: {style.arm_color(a) if a in GS.ARMS else style.FAINT};")
            row.addWidget(dot)
            row.addWidget(QLabel(a[:1].upper() + a[1:]), 1)
            n = QLabel(str(arm_n[a]))
            n.setStyleSheet("font-weight: 700;")
            row.addWidget(n)
            self.arm_box.addLayout(row)
        self._clear(self.check_box)
        if not tg:
            self._line(self.check_box, False, "Tick the files to analyse.")
            return
        no_before = [k[1] for k, v in tg.items() if not any(not str(c).startswith("After") for _, c, _ in v)]
        no_after = [k[1] for k, v in tg.items() if not any(str(c).startswith("After") for _, c, _ in v)]
        self._line(self.check_box, not no_before, "Every gel has a Before file" if not no_before else
                   f"{len(no_before)} gel(s) without a Before file: " + ", ".join(no_before[:3]))
        if no_after:
            self._line(self.check_box, False, f"{len(no_after)} gel(s) without an After file (not compared)")
        amf_days = {k[0] for k, v in tg.items() if any(c == "After AMF" for _, c, _ in v)}
        sham_days = {k[0] for k, v in tg.items() if any(GS.is_sham(c) for _, c, _ in v)}
        if amf_days:
            miss = sorted(amf_days - sham_days)
            self._line(self.check_box, not miss,
                       "Sham controls on every day" if not miss else
                       ("No sham gel — changes can’t be corrected for drift" if not sham_days else
                        "No sham gel on " + ", ".join(miss[:3])))
        px = {round(h["px"], 5) for p, h in page.headers.items() if p in sel and h.get("px")}
        if len(px) > 1:
            self._line(self.check_box, False, f"Pixel sizes differ ({len(px)} values) — compare with care")
        elif px:
            self._line(self.check_box, True, "Same pixel size in every file")
        flagged = [p for p in sel if "check" in page._kind_of(p)]
        if flagged:
            self._line(self.check_box, False, f"{len(flagged)} file(s) marked ‘check’ by the last quality check")
        bad = [p for p in sel if not page.rows[p]["named"]]
        if bad:
            self._line(self.check_box, False, f"{len(bad)} name(s) don’t start with mmddyy_##_")
        nm = page.n_manual()
        if nm:
            self._line(self.check_box, True, f"{nm} value(s) typed by hand will be used")


class RecentCard(Card):
    """Recent analyses, shown on the Files step while no images are chosen: one click reopens the results."""
    open_entry = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("Recent analyses", "Open earlier results in Results & plots, Patterns and the Experiment "
                         "planner — or choose images above to start a new analysis.", parent)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        self.lay.addLayout(self.rows)
        ease.recent_changed.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.setParent(None)
        lst = ease.recent_list()[:5]
        for e in lst:
            title, detail = ease.describe(e)
            name = QLabel(title)
            name.setStyleSheet("font-weight: 600;")
            name.setToolTip(e.get("folder", ""))
            info = hint(detail)
            info.setWordWrap(False)
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(0)
            cl.addWidget(name)
            cl.addWidget(info)
            row = hbox(col, "stretch", button("Open results", slot=lambda _=False, e=e: self.open_entry.emit(e)),
                       button("Forget", "link", tip="Remove from this list (the files stay)",
                              slot=lambda _=False, f=e.get("folder", ""): ease.forget_recent(f)),
                       stretch_end=False)
            self.rows.addWidget(row)
        self.has_entries = bool(lst)


class _HeaderSignals(QObject):
    done = Signal(str, dict)


class _HeaderJob(QRunnable):
    def __init__(self, path):
        super().__init__()
        self.path, self.sig = path, _HeaderSignals()

    def run(self):
        self.sig.done.emit(self.path, read_header(self.path))


class DataPage(Page):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Choose your images", "Choose what to analyse. File names are read for date, sample, AMF settings, "
                         "collagen and nanocage conditions — check the table before running.", scroll=False)
        self.setAcceptDrops(True)
        self.mode = "folder"
        self.folders = []
        self.single = None
        self.rows = {}               # path -> row data
        self.headers = {}
        self.after_pattern = "AMF"
        self.pool = QThreadPool.globalInstance()
        st = QSettings()
        try:
            self.overrides = json.loads(st.value("data/overrides", "") or "{}")   # path -> {field: value}
        except ValueError:
            self.overrides = {}
        try:
            self.fields_cfg = json.loads(st.value("data/fields", "") or "{}")
        except ValueError:
            self.fields_cfg = {}
        self.fields_cfg.setdefault("defaults", {})
        self.fields_cfg.setdefault("custom", [])
        try:
            self.qc = json.loads(st.value("data/qc", "") or "{}")              # file name -> [status, flags]
        except ValueError:
            self.qc = {}
        self.col_field = {}

        self.band, self.arm = {}, {}      # path -> gel band (0/1) and group, for the table
        self.filter_kind = "all"

        # ---- source card: what to analyse (segmented control) + where
        src = Card()
        self.src_label = QLabel()
        self.src_label.setObjectName("cardTitle")
        seg = QFrame()
        seg.setObjectName("segGroup")
        sgl = QHBoxLayout(seg)
        sgl.setContentsMargins(3, 3, 3, 3)
        sgl.setSpacing(2)
        self.mode_buttons = {}
        grp = QButtonGroup(self)
        for key, title, text in MODES:
            b = QPushButton(title)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(text)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            grp.addButton(b)
            sgl.addWidget(b)
            self.mode_buttons[key] = b
        self.mode_hint = hint("")
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("Nothing selected yet — click Choose…, or drag files / folders onto this window")
        self.browse_btn = button("Browse…", "primary", slot=self.browse)
        self.add_btn = button("Add folder…", "primary", slot=self.add_folder)
        self.remove_btn = button("Remove selected folder", slot=self.remove_folder)
        self.clear_btn = button("Clear", slot=self.clear)
        self.folder_list = QListWidget()
        self.folder_list.setObjectName("plain")
        self.folder_list.setMaximumHeight(96)
        src.lay.addWidget(hbox(self.src_label, "stretch", seg, stretch_end=False))
        src.lay.addWidget(self.mode_hint)
        self.single_row = hbox(self.path_edit, self.browse_btn, stretch_end=False)
        self.single_row.layout().setStretch(0, 1)
        src.lay.addWidget(self.single_row)
        src.lay.addWidget(self.folder_list)
        self.multi_row = hbox(self.add_btn, self.remove_btn, self.clear_btn)
        src.lay.addWidget(self.multi_row)
        self.lay.addWidget(src)
        self.recent = RecentCard()
        self.lay.addWidget(self.recent)
        ease.recent_changed.changed.connect(self._show_recent)

        # ---- files card
        files = Card()
        top = QHBoxLayout()
        self.count_label = QLabel("No files")
        self.count_label.setObjectName("cardTitle")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name, sample, condition…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.filter_edit.setMaximumWidth(280)
        top.addWidget(self.count_label)
        top.addSpacing(8)
        self.chips = {}
        cgrp = QButtonGroup(self)
        for key, text in FILTERS:
            b = QPushButton(text)
            b.setObjectName("chipWarn" if key == "check" else "chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_filter_kind(k))
            cgrp.addButton(b)
            top.addWidget(b)
            self.chips[key] = b
        self.chips["all"].setChecked(True)
        top.addStretch(1)
        top.addWidget(self.filter_edit)
        files.lay.addLayout(top)
        acts = QHBoxLayout()
        acts.addWidget(button("Tick all shown", slot=lambda: self.tick_all(True)))
        acts.addWidget(button("Untick all shown", slot=lambda: self.tick_all(False)))
        acts.addWidget(button("Untick QC ‘check’", slot=self.untick_flagged,
                              tip="Untick the files the quality check of the last run marked ‘check’ "
                                  "(see the QC column)"))
        acts.addStretch(1)
        acts.addWidget(button("Defaults && added parameters…", slot=self.edit_fields,
                              tip="Default values used when a file name lacks them, and your own parameters "
                                  "(e.g. gel thickness, NC type) — remembered for future runs"))
        files.lay.addLayout(acts)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setItemDelegate(FieldDelegate(self))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(1, 330)
        self.table.itemChanged.connect(self._item_changed)
        files.lay.addWidget(self.table, 1)
        self.warn = banner("")
        self.warn.hide()
        files.lay.addWidget(self.warn)
        self.legend = QLabel()
        self.legend.setObjectName("hint")
        self.legend.setWordWrap(True)
        files.lay.addWidget(self.legend)
        self._set_legend()

        # ---- side panel: ready to run?
        self.ready = ReadyPanel()
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(files, 1)
        row.addWidget(self.ready, 0, Qt.AlignTop)
        self.lay.addLayout(row, 1)

        s = QSettings()
        self.set_mode(s.value("data/mode", "folder"))

    # ------------------------------------------------------------------ mode / sources
    def _set_legend(self):
        self.legend.setText(
            "Files of one gel (same mmddyy_##_) are shown together and compared before vs after. Double-click a "
            "value to change it (e.g. an NC concentration missing from the name): "
            f"<span style='background:{style.EDITED_BG}'>&nbsp;highlighted&nbsp;</span> = typed by hand, "
            f"<i style='color:{style.MUTED}'>grey italic</i> = default. Right-click: copy to selected / ticked "
            "files, back to the file-name value. Your changes are remembered.")

    def on_theme(self):
        self._set_legend()
        was = self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None:
                self._fill_fields(i, it.data(Qt.UserRole))
                self._fill_qc(i, it.data(Qt.UserRole))
        self.table.blockSignals(was)
        self.table.viewport().update()
        self._update_count()

    def set_mode(self, mode):
        self.mode = mode
        for k, b in self.mode_buttons.items():
            b.setChecked(k == mode)
        self.mode_hint.setText(dict((k, t) for k, _, t in MODES)[mode])
        multi = mode == "folders"
        self.src_label.setText({"file": "Image file", "folder": "Data folder", "folders": "Data folders"}[mode])
        self.single_row.setVisible(not multi)
        self.folder_list.setVisible(multi)
        self.multi_row.setVisible(multi)
        self.browse_btn.setText("Choose file…" if mode == "file" else "Choose folder…")
        QSettings().setValue("data/mode", mode)
        if mode == "file" and self.single is None:
            self.folders = []
        if mode != "file":
            self.single = None
        if mode == "folder" and len(self.folders) > 1:
            self.folders = self.folders[:1]
        self.refresh()

    def browse(self):
        start = QSettings().value("data/last_dir", os.path.expanduser("~"))
        if self.mode == "file":
            p, _ = QFileDialog.getOpenFileName(self, "Choose an image file", start,
                                               "Images (*.nd2 *.tif *.tiff);;All files (*)")
            if p:
                self.single = p
                self.folders = [os.path.dirname(p)]
                QSettings().setValue("data/last_dir", os.path.dirname(p))
        else:
            p = QFileDialog.getExistingDirectory(self, "Choose the data folder", start)
            if p:
                self.folders = [p]
                QSettings().setValue("data/last_dir", p)
        self.refresh()

    def add_folder(self):
        start = QSettings().value("data/last_dir", os.path.expanduser("~"))
        p = QFileDialog.getExistingDirectory(self, "Add a data folder", start)
        if p and p not in self.folders:
            self.folders.append(p)
            QSettings().setValue("data/last_dir", os.path.dirname(p))
        self.refresh()

    def remove_folder(self):
        for it in self.folder_list.selectedItems():
            if it.text() in self.folders:
                self.folders.remove(it.text())
        self.refresh()

    def clear(self):
        self.folders, self.single = [], None
        self.refresh()

    def load_paths(self, paths):
        """Files / folders dropped on the window or given at start-up."""
        paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(IMAGE_EXT)]
        if len(files) == 1 and not dirs:
            self.single, self.folders = files[0], [os.path.dirname(files[0])]
            self.set_mode("file")
        elif len(dirs) == 1 and not files:
            self.folders = dirs
            self.set_mode("folder")
        elif dirs or files:
            self.folders = list(dict.fromkeys(dirs + [os.path.dirname(f) for f in files]))
            self.set_mode("folders" if len(self.folders) > 1 else "folder")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.load_paths([u.toLocalFile() for u in e.mimeData().urls()])

    # ------------------------------------------------------------------ table
    def set_after_pattern(self, pat):
        if pat != self.after_pattern:
            self.after_pattern = pat
            self.refresh()

    def _show_recent(self):
        self.recent.setVisible(self.recent.has_entries and not self.folders and not self.single)

    def refresh(self):
        self._show_recent()
        self.folder_list.clear()
        self.folder_list.addItems(self.folders)
        if self.mode == "file":
            self.path_edit.setText(self.single or "")
            paths = [self.single] if self.single else []
        else:
            self.path_edit.setText(self.folders[0] if self.folders else "")
            paths = []
            for fo in self.folders:
                paths += sorted(sum([glob.glob(os.path.join(fo, "*" + e)) for e in IMAGE_EXT], []),
                                key=lambda p: os.path.basename(p).lower())
            paths = list(dict.fromkeys(p for p in paths if not os.path.basename(p).startswith("._")))
        old_tick = {p: r.get("tick", True) for p, r in self.rows.items()}
        self.rows = {}
        fields = self.fields()
        cols = LEFT + [f["title"] for f in fields] + RIGHT
        self.col_field = {len(LEFT) + i: f for i, f in enumerate(fields)}
        self.col_type, self.col_px, self.col_qc = len(cols) - 2, len(cols) - 1, LEFT.index("QC")
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        for c, f in self.col_field.items():
            self.table.horizontalHeaderItem(c).setToolTip(f["tip"])
        self.table.setRowCount(len(paths))
        multi_folder = len(self.folders) > 1
        for i, p in enumerate(paths):
            d = parse_file_name(p, self.after_pattern)
            d["tick"] = old_tick.get(p, True)
            self.rows[p] = d
            vals = ["", d["name"], os.path.basename(os.path.dirname(p)), d["date"],
                    d["sample"].split("_")[-1] if d["named"] else "?"]
            for c in range(len(cols)):
                it = QTableWidgetItem(vals[c] if c < len(vals) else "")
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                if c in self.col_field:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                else:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 0:
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if d["tick"] else Qt.Unchecked)
                if c == 3 and not d["named"]:
                    it.setForeground(QBrush(QColor(style.ERROR)))
                self.table.setItem(i, c, it)
            self._fill_fields(i, p)
            self._fill_qc(i, p)
            self.table.item(i, self.col_type).setText("…")
            h = self.headers.get(p)
            if h is not None:
                self._fill_header(i, h)
            else:
                job = _HeaderJob(p)
                job.sig.done.connect(self._header_ready)
                self.pool.start(job)
        self.table.setColumnHidden(2, not multi_folder)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 32)
        self.table.setColumnWidth(1, min(max(self.table.columnWidth(1), 220), 420))
        for c, f in self.col_field.items():
            self.table.setColumnWidth(c, min(max(self.table.columnWidth(c), 110 if f["key"] == "condition" else 70),
                                             200))
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.apply_filter(self.filter_edit.text())
        self._update_count()

    # ------------------------------------------------------------------ sample information (editable)
    def fields(self):
        """Columns of sample information: standard ones, then the parameters added by the user."""
        out = [dict(key=k, title=t + (f" ({u})" if u else ""), unit=u, type=typ, choices=ch, engine=ek, factor=fac,
                    tip=FIELD_TIPS.get(k, ""), custom=False, label=t)
               for k, t, u, typ, ch, ek, fac in STD_FIELDS]
        taken = set()
        for f in self.fields_cfg.get("custom", []):
            key = field_key(f["label"], f.get("unit", ""), taken)
            taken.add(key)
            out.append(dict(key="custom:" + f["id"], title=f["label"] + (f" ({f['unit']})" if f.get("unit") else ""),
                            unit=f.get("unit", ""), type=f.get("type", "num"), choices=None, engine=key, factor=None,
                            tip=f"Added parameter → spreadsheet column ‘{key}’", custom=True, label=f["label"],
                            default=f.get("default")))
        return out

    def value(self, path, f):
        """(value, source) of one field of one file; source = edited / name / default / ''."""
        ov = self.overrides.get(path, {})
        if f["key"] in ov:
            return ov[f["key"]], "edited"
        if not f["custom"]:
            v = parsed_value(self.rows[path], f["key"]) if path in self.rows else None
            if v is not None and v != "":
                return v, "name"
            dv = self.fields_cfg["defaults"].get(f["key"])
            return (dv, "default") if dv not in (None, "") else (None, "")
        return (f.get("default"), "default") if f.get("default") not in (None, "") else (None, "")

    def _fill_fields(self, i, path):
        was = self.table.blockSignals(True)
        for c, f in self.col_field.items():
            it = self.table.item(i, c)
            v, src = self.value(path, f)
            it.setText(fmt_value(v))
            font = it.font()
            font.setItalic(src == "default")
            font.setBold(src == "edited")
            it.setFont(font)
            if src == "edited":
                it.setBackground(QBrush(QColor(style.EDITED_BG)))
            else:
                it.setData(Qt.BackgroundRole, None)
            it.setForeground(QBrush(QColor(style.MUTED if src == "default" else style.TEXT)))
            name_v = parsed_value(self.rows[path], f["key"]) if not f["custom"] else None
            tips = {"edited": f"Typed by hand (file name: {fmt_value(name_v) or 'nothing'}). Right-click → "
                              "‘Back to file-name value’ to undo.",
                    "default": "Default value (Defaults & added parameters…)", "name": "Read from the file name",
                    "": "Not in the file name — double-click to enter"}
            it.setToolTip(f"{f['tip']}\n{tips[src]}")
        self.table.blockSignals(was)

    def _row_of(self, path):
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.data(Qt.UserRole) == path:
                return i
        return None

    def _parse_input(self, f, text):
        t = (text or "").strip()
        if f["type"] == "num":
            if not t:
                return None
            try:
                return float(t.replace(",", "."))
            except ValueError:
                raise ValueError(f"‘{text}’ is not a number ({f['title']}).")
        return t or None

    def set_value(self, path, f, value):
        ov = self.overrides.setdefault(path, {})
        ov.pop(f["key"], None)
        base, _ = self.value(path, f)                 # value without a hand-typed entry (file name / default)
        if value != base:
            ov[f["key"]] = value
        if not ov:
            self.overrides.pop(path, None)
        self._save_overrides()

    def reset_value(self, path, f):
        ov = self.overrides.get(path, {})
        ov.pop(f["key"], None)
        if not ov:
            self.overrides.pop(path, None)
        self._save_overrides()

    def _save_overrides(self):
        QSettings().setValue("data/overrides", json.dumps(self.overrides))

    def _context_menu(self, pos):
        it = self.table.itemAt(pos)
        if it is None:
            return
        c = it.column()
        f = self.col_field.get(c)
        rows = sorted({x.row() for x in self.table.selectedItems()} | {it.row()})
        paths = [self.table.item(r, 0).data(Qt.UserRole) for r in rows]
        m = QMenu(self)
        if f:
            v, src = self.value(it.data(Qt.UserRole), f)
            shown = fmt_value(v) or "(empty)"
            m.addAction(f"Edit {f['label']}…", lambda: self.table.editItem(it))
            if len(paths) > 1:
                m.addAction(f"Set {f['label']} = {shown} for the {len(paths)} selected files",
                            lambda: self._apply(paths, f, v))
            ticked = self.selected_files()
            m.addAction(f"Set {f['label']} = {shown} for all {len(ticked)} ticked files",
                        lambda: self._apply(ticked, f, v))
            m.addAction(f"Make {shown} the default {f['label']} for future files",
                        lambda: self._make_default(f, v))
            m.addSeparator()
            m.addAction(f"Back to file-name value ({f['label']}" + (", selected files)" if len(paths) > 1 else ")"),
                        lambda: self._reset_field(paths, f))
        m.addAction("Undo all hand-typed values of " + ("the selected files" if len(paths) > 1 else "this file"),
                    lambda: self._reset_files(paths))
        m.addSeparator()
        m.addAction("Defaults & added parameters…", self.edit_fields)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _reset_field(self, paths, f):
        for p in paths:
            self.reset_value(p, f)
        self._refill(paths)

    def _apply(self, paths, f, v):
        for p in paths:
            self.set_value(p, f, v)
        self._refill(paths)

    def _make_default(self, f, v):
        if f["custom"]:
            for cf in self.fields_cfg["custom"]:
                if "custom:" + cf["id"] == f["key"]:
                    cf["default"] = v
        elif v in (None, ""):
            self.fields_cfg["defaults"].pop(f["key"], None)
        else:
            self.fields_cfg["defaults"][f["key"]] = v
        QSettings().setValue("data/fields", json.dumps(self.fields_cfg))
        self.refresh()

    def _reset_files(self, paths):
        for p in paths:
            self.overrides.pop(p, None)
        self._save_overrides()
        self._refill(paths)

    def _refill(self, paths):
        for p in paths:
            i = self._row_of(p)
            if i is not None:
                self._fill_fields(i, p)
        self._update_count()

    def edit_fields(self):
        dlg = FieldsDialog(self.fields_cfg, self)
        if dlg.exec() and dlg.result_cfg is not None:
            ids = {"custom:" + f["id"] for f in dlg.result_cfg["custom"]}
            for p in list(self.overrides):                 # drop values of removed parameters
                for k in [k for k in self.overrides[p] if k.startswith("custom:") and k not in ids]:
                    del self.overrides[p][k]
                if not self.overrides[p]:
                    del self.overrides[p]
            self.fields_cfg = dlg.result_cfg
            QSettings().setValue("data/fields", json.dumps(self.fields_cfg))
            self._save_overrides()
            self.refresh()

    def metadata(self):
        """Values for the analysis engine (--metadata): hand-typed and default values, added parameters."""
        fields = self.fields()
        files = {}
        for p in self.selected_files():
            name = self.rows[p]["name"]
            entry, custom = {}, {}
            for f in fields:
                v, src = self.value(p, f)
                if f["custom"]:
                    if v not in (None, ""):
                        custom[f["engine"]] = v
                elif src in ("edited", "default"):
                    if f["factor"] and v is not None:
                        v = v * f["factor"]
                        if f["engine"] in ("amf_s", "inc_s"):
                            v = int(round(v))
                    entry[f["engine"]] = v if v != "" else None
            if custom:
                entry["custom"] = custom
            if entry:
                files[name] = entry
        return {"fields": [dict(key=f["engine"], label=f["label"], unit=f["unit"], type=f["type"])
                           for f in fields if f["custom"]], "files": files}

    def n_manual(self):
        sel = set(self.selected_files())
        return sum(len([k for k in v if not k.startswith("custom:")]) for p, v in self.overrides.items() if p in sel)

    def _header_ready(self, path, h):
        self.headers[path] = h
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.data(Qt.UserRole) == path:
                self._fill_header(i, h)
        self._update_count()

    def _fill_header(self, i, h):
        was = self.table.blockSignals(True)
        if h.get("cloud"):
            self.table.item(i, self.col_type).setText("in iCloud")
            self.table.item(i, self.col_type).setToolTip("Not downloaded to this computer yet. The analysis "
                                                         "downloads it when its folder is analysed.")
            self.table.item(i, self.col_px).setText("")
            self.table.blockSignals(was)
            return
        self.table.item(i, self.col_type).setText(kind_text(h) if "error" not in h else "cannot read")
        px = h.get("px")
        self.table.item(i, self.col_px).setText(f"{px:.4f}" if px else ("—" if "error" not in h else ""))
        if "error" in h:
            self.table.item(i, self.col_type).setToolTip(h["error"])
            self.table.item(i, self.col_type).setForeground(QBrush(QColor(style.ERROR)))
        self.table.blockSignals(was)

    # ------------------------------------------------------------------ quality check of the last run
    def set_qc(self, results):
        """results: file name (no extension) -> (status, flags), from the 'quality' sheet of a run."""
        self.qc.update({k: list(v) for k, v in results.items()})
        if len(self.qc) > 5000:                       # keep the stored list small
            self.qc = dict(list(self.qc.items())[-5000:])
        QSettings().setValue("data/qc", json.dumps(self.qc))
        was = self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None:
                self._fill_qc(i, it.data(Qt.UserRole))
        self.table.blockSignals(was)

    def _fill_qc(self, i, path):
        it = self.table.item(i, self.col_qc)
        if it is None:
            return
        status, flags = self.qc.get(os.path.splitext(os.path.basename(path))[0], ("", ""))
        it.setText({"ok": "ok", "check": "⚠ check"}.get(status, ""))
        it.setToolTip(flags or ("No problem found by the quality check of the last run." if status == "ok" else
                                "Not analysed yet (the QC column fills after a run)."))
        it.setForeground(QBrush(QColor(style.WARN if status == "check" else style.OK)))

    def untick_flagged(self):
        was = self.table.blockSignals(True)
        n = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            p = it.data(Qt.UserRole)
            if self.qc.get(os.path.splitext(os.path.basename(p))[0], ("",))[0] == "check" and self.rows[p].get("tick"):
                it.setCheckState(Qt.Unchecked)
                self.rows[p]["tick"] = False
                n += 1
        self.table.blockSignals(was)
        self._update_count()
        if not n:
            QMessageBox.information(self, "Quality check", "No ticked file is marked ‘check’. The QC column fills "
                                    "after a run.")

    def _item_changed(self, it):
        p = it.data(Qt.UserRole)
        if it.column() == 0:
            if p in self.rows:
                self.rows[p]["tick"] = it.checkState() == Qt.Checked
            self._update_count()
            return
        f = self.col_field.get(it.column())
        if f is None or p not in self.rows:
            return
        try:
            v = self._parse_input(f, it.text())
        except ValueError as e:
            QMessageBox.warning(self, "Not a number", str(e))
            self._refill([p])
            return
        old, _ = self.value(p, f)
        if v != old:
            self.set_value(p, f, v)
        self._refill([p])

    def tick_all(self, on):
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                it = self.table.item(i, 0)
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                self.rows[it.data(Qt.UserRole)]["tick"] = on
        self.table.blockSignals(False)
        self._update_count()

    def set_filter_kind(self, kind):
        self.filter_kind = kind
        self.apply_filter(self.filter_edit.text())

    def _kind_of(self, path):
        """before / after / sham / check flags of one file (for the filter chips)."""
        f = next((f for f in self.col_field.values() if f["key"] == "condition"), None)
        cond = self.value(path, f)[0] if f else self.rows[path]["condition"]
        k = {"before"} if not str(cond).startswith("After") else {"sham"} if GS.is_sham(cond) else {"after"}
        if self.qc.get(os.path.splitext(os.path.basename(path))[0], ("",))[0] == "check":
            k.add("check")
        return k

    def apply_filter(self, text=None):
        t = (self.filter_edit.text() if text is None else text).lower().strip()
        for i in range(self.table.rowCount()):
            p = self.table.item(i, 0).data(Qt.UserRole)
            row = " ".join(self.table.item(i, c).text().lower() for c in range(1, self.table.columnCount()))
            hide = bool(t) and t not in row
            if self.filter_kind != "all" and p in self.rows:
                hide = hide or self.filter_kind not in self._kind_of(p)
            self.table.setRowHidden(i, hide)

    def _groups(self):
        """Gel bands and groups (NC + AMF, sham, …) of every listed file, from the (possibly edited) values."""
        fc = next((f for f in self.col_field.values() if f["key"] == "condition"), None)
        fn = next((f for f in self.col_field.values() if f["key"] == "nc_nM"), None)
        gels = {}
        for p, r in self.rows.items():
            cond = self.value(p, fc)[0] if fc else r["condition"]
            nc = self.value(p, fn)[0] if fn else None
            gels.setdefault((r["date"], r["sample"]), []).append((p, cond, nc))
        self.band, self.arm = {}, {}
        for k, key in enumerate(sorted(gels)):
            files = gels[key]
            arms = [GS.file_arm(c, n) for _, c, n in files]
            joined = " / ".join(dict.fromkeys(a for a in arms if a))
            for (p, _, _), a in zip(files, arms):
                self.band[p] = k % 2
                self.arm[p] = a or joined
        return gels

    def _update_count(self):
        sel = self.selected_files()
        n = len(self.rows)
        samples = {self.rows[p]["sample"] for p in sel}
        self.count_label.setText(f"Files  ·  {len(sel)} of {n} ticked" if n else "Files")
        gels = self._groups()
        counts = {k: 0 for k, _ in FILTERS}
        for p in self.rows:
            counts["all"] += 1
            for k in self._kind_of(p):
                counts[k] += 1
        for k, text in FILTERS:
            self.chips[k].setText(f"{text} {counts[k]}")
            self.chips[k].setVisible(k in ("all", "before", "after") or counts[k] > 0)
        self.ready.update_from(self, sel, gels)
        self.table.viewport().update()
        msgs = []
        bad = [self.rows[p]["name"] for p in sel if not self.rows[p]["named"]]
        if bad:
            msgs.append(f"{len(bad)} file name(s) do not start with mmddyy_##_ and are grouped as 'other': "
                        + ", ".join(bad[:3]) + ("…" if len(bad) > 3 else ""))
        unread = [p for p in sel if "error" in self.headers.get(p, {})]
        if unread:
            msgs.append(f"{len(unread)} file(s) could not be opened: " + ", ".join(os.path.basename(p) for p in unread[:3]))
        tif_nopx = [p for p in sel if p.lower().endswith((".tif", ".tiff")) and p in self.headers
                    and "error" not in self.headers[p] and not self.headers[p].get("px")]
        if tif_nopx:
            msgs.append(f"{len(tif_nopx)} TIFF file(s) have no pixel size — set it in the Settings step "
                        "(TIFF calibration).")
        names = [os.path.basename(p) for p in sel]
        stems = [os.path.splitext(n_)[0] for n_ in names]
        if len(set(stems)) < len(stems) and len(self.folders) <= 1:
            msgs.append("Two ticked files have the same name with different extensions (.nd2 and .tif); untick one.")
        self.warn.setText("\n".join(msgs))
        self.warn.setVisible(bool(msgs))
        self.changed.emit()

    # ------------------------------------------------------------------ for other pages
    def selected_files(self):
        return [p for p, r in self.rows.items() if r.get("tick")]

    def needs_pixel_size(self):
        return any(p.lower().endswith((".tif", ".tiff")) and p in self.headers and "error" not in self.headers[p]
                   and not self.headers[p].get("px") for p in self.selected_files())

    def n_images(self, zstep, tstep):
        tot, known = 0, True
        for p in self.selected_files():
            h = self.headers.get(p)
            if not h or "error" in h or h.get("cloud"):
                known = False
                continue
            nt, nz = h.get("nt", 1), h.get("nz", 1)
            tot += len(range(0, nt, tstep)) * len(range(0, nz, zstep))
        return tot, known

    def describe(self):
        sel = self.selected_files()
        nf = len({os.path.dirname(p) for p in sel})
        return f"{len(sel)} file(s)" + (f" in {nf} folders" if nf > 1 else "")

    def validate(self):
        if not self.selected_files():
            return "No files are ticked in the Files step."
        return None

    def engine_args(self, list_path):
        """Input part of the engine command line."""
        sel = self.selected_files()
        if self.mode == "file":
            return [os.path.dirname(sel[0]), "--file", os.path.basename(sel[0])]
        with open(list_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sel) + "\n")
        return ["--files-from", list_path]

    def is_multi(self):
        return len({os.path.dirname(p) for p in self.selected_files()}) > 1
