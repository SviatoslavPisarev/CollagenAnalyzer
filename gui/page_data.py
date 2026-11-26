"""Page 1: choose a file, a folder or several folders, and which files to analyse."""
import os
import glob

from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, QSettings
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from .common import IMAGE_EXT, parse_file_name, read_header, kind_text
from .widgets import Page, Card, ModeCard, button, hint, banner, hbox
from . import style

COLUMNS = ["", "File", "Folder", "Date", "Sample", "Condition", "AMF", "Collagen", "NC conc.", "NC mixing",
           "Note", "Type", "Pixel (µm)"]


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
        super().__init__("1 · Data", "Choose what to analyse. File names are read for date, sample, AMF settings, "
                         "collagen and nanocage conditions — check the table before running.", scroll=False)
        self.setAcceptDrops(True)
        self.mode = "folder"
        self.folders = []
        self.single = None
        self.rows = {}               # path -> row data
        self.headers = {}
        self.after_pattern = "AMF"
        self.pool = QThreadPool.globalInstance()

        # ---- mode cards
        cards = QWidget()
        cl = QHBoxLayout(cards)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(12)
        self.mode_buttons = {}
        grp = QButtonGroup(self)
        for key, title, text in [
                ("file", "One file", "Detailed report of a single .nd2 / .tif: a figure of every image, "
                                     "slice comparisons and a spreadsheet."),
                ("folder", "One folder", "All image files of a folder (or the ones you tick): per-file results "
                                         "plus before/after and side-by-side comparisons."),
                ("folders", "Several folders", "Each folder analysed on its own, plus one combined "
                                               "summary.xlsx of all folders.")]:
            b = ModeCard(title, text)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            grp.addButton(b)
            cl.addWidget(b)
            self.mode_buttons[key] = b
        self.lay.addWidget(cards)

        # ---- source card
        src = Card()
        self.src_label = QLabel()
        self.src_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("Nothing selected yet — click Browse, or drag files / folders onto this window")
        self.browse_btn = button("Browse…", "primary", slot=self.browse)
        self.add_btn = button("Add folder…", "primary", slot=self.add_folder)
        self.remove_btn = button("Remove selected folder", slot=self.remove_folder)
        self.clear_btn = button("Clear", slot=self.clear)
        self.folder_list = QListWidget()
        self.folder_list.setObjectName("plain")
        self.folder_list.setMaximumHeight(96)
        src.lay.addWidget(self.src_label)
        self.single_row = hbox(self.path_edit, self.browse_btn, stretch_end=False)
        self.single_row.layout().setStretch(0, 1)
        src.lay.addWidget(self.single_row)
        src.lay.addWidget(self.folder_list)
        self.multi_row = hbox(self.add_btn, self.remove_btn, self.clear_btn)
        src.lay.addWidget(self.multi_row)
        self.lay.addWidget(src)

        # ---- files card
        files = Card()
        top = QHBoxLayout()
        self.count_label = QLabel("No files")
        self.count_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name, sample, condition…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.filter_edit.setMaximumWidth(280)
        top.addWidget(self.count_label)
        top.addStretch(1)
        top.addWidget(self.filter_edit)
        top.addWidget(button("Tick all", slot=lambda: self.tick_all(True)))
        top.addWidget(button("Untick all", slot=lambda: self.tick_all(False)))
        files.lay.addLayout(top)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
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
        files.lay.addWidget(hint("Tick the files to analyse. Hover a row for the full name. Files of one sample "
                                 "(same mmddyy_##_) are compared before vs after AMF."))
        self.lay.addWidget(files, 1)

        s = QSettings()
        self.set_mode(s.value("data/mode", "folder"))

    # ------------------------------------------------------------------ mode / sources
    def set_mode(self, mode):
        self.mode = mode
        for k, b in self.mode_buttons.items():
            b.setChecked(k == mode)
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

    def refresh(self):
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
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(paths))
        multi_folder = len(self.folders) > 1
        for i, p in enumerate(paths):
            d = parse_file_name(p, self.after_pattern)
            d["tick"] = old_tick.get(p, True)
            self.rows[p] = d
            vals = ["", d["name"], os.path.basename(os.path.dirname(p)), d["date"], d["sample"].split("_")[-1]
                    if d["named"] else "?", d["condition"], d["amf"], d["collagen"], d["nc"], d["mix"],
                    d["other"] or "", "…", ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                if c == 0:
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if d["tick"] else Qt.Unchecked)
                if c == 5:
                    it.setForeground(QBrush(QColor(style.AFTER if d["condition"] == "After AMF" else style.BEFORE)))
                if c == 3 and not d["named"]:
                    it.setForeground(QBrush(QColor(style.ERROR)))
                self.table.setItem(i, c, it)
            h = self.headers.get(p)
            if h is not None:
                self._fill_header(i, h)
            else:
                job = _HeaderJob(p)
                job.sig.done.connect(self._header_ready)
                self.pool.start(job)
        self.table.setColumnHidden(2, not multi_folder)
        self.table.resizeColumnsToContents()
        for c, (lo, hi) in {0: (30, 34), 1: (220, 420), 2: (80, 200), 6: (60, 260), 10: (50, 200)}.items():
            self.table.setColumnWidth(c, min(max(self.table.columnWidth(c), lo), hi))
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.apply_filter(self.filter_edit.text())
        self._update_count()

    def _header_ready(self, path, h):
        self.headers[path] = h
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.data(Qt.UserRole) == path:
                self._fill_header(i, h)
        self._update_count()

    def _fill_header(self, i, h):
        self.table.blockSignals(True)
        self.table.item(i, 11).setText(kind_text(h) if "error" not in h else "cannot read")
        px = h.get("px")
        self.table.item(i, 12).setText(f"{px:.4f}" if px else ("—" if "error" not in h else ""))
        if "error" in h:
            self.table.item(i, 11).setToolTip(h["error"])
            self.table.item(i, 11).setForeground(QBrush(QColor(style.ERROR)))
        self.table.blockSignals(False)

    def _item_changed(self, it):
        if it.column() == 0:
            p = it.data(Qt.UserRole)
            if p in self.rows:
                self.rows[p]["tick"] = it.checkState() == Qt.Checked
            self._update_count()

    def tick_all(self, on):
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                it = self.table.item(i, 0)
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                self.rows[it.data(Qt.UserRole)]["tick"] = on
        self.table.blockSignals(False)
        self._update_count()

    def apply_filter(self, text):
        t = text.lower().strip()
        for i in range(self.table.rowCount()):
            row = " ".join(self.table.item(i, c).text().lower() for c in range(1, len(COLUMNS)))
            self.table.setRowHidden(i, bool(t) and t not in row)

    def _update_count(self):
        sel = self.selected_files()
        n = len(self.rows)
        samples = {self.rows[p]["sample"] for p in sel}
        self.count_label.setText(f"{len(sel)} of {n} file(s) ticked · {len(samples)} sample(s)" if n else "No files")
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
            msgs.append(f"{len(tif_nopx)} TIFF file(s) have no pixel size — set it on the Parameters page "
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
            if not h or "error" in h:
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
            return "No files are ticked on the Data page."
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
