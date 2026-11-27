"""Page 4: run the analysis in a separate process, show progress and log, list the results."""
import os
import signal
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSettings
from PySide6.QtGui import QTextCharFormat, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget, QTextEdit)

from .common import engine_command, open_path, quote
from .widgets import Page, Card, StepChip, button, hint, banner, hbox
from . import style
from . import ease
from .errors import explain, as_html


class EngineProcess(QThread):
    """Runs the analysis engine as a child process and relays its output."""
    line = Signal(str)
    progress = Signal(str, int, int, float, str)
    output = Signal(str, str)
    finished_code = Signal(int)

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.proc = None
        self.stopped = False

    def run(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", MPLBACKEND="Agg")
        kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                  bufsize=1, text=True, encoding="utf-8", errors="replace")
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(engine_command(self.args), **kw)
        except Exception as e:
            self.line.emit(f"ERROR: could not start the analysis: {e}")
            self.finished_code.emit(-1)
            return
        for raw in self.proc.stdout:
            ln = raw.rstrip("\n")
            if ln.startswith("@@P|"):
                parts = ln.split("|")
                try:
                    self.progress.emit(parts[1], int(parts[2]), int(parts[3]), float(parts[4]), "|".join(parts[5:]))
                except (IndexError, ValueError):
                    pass
            elif ln.startswith("@@OUT|"):
                _, kind, path = ln.split("|", 2)
                self.output.emit(kind, path)
            elif ln.startswith("@@DONE"):
                pass
            else:
                self.line.emit(ln)
        code = self.proc.wait()
        self.finished_code.emit(-2 if self.stopped else code)

    def stop(self):
        self.stopped = True
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(p.pid, signal.SIGTERM)
                QTimer.singleShot(3000, lambda: self._kill_hard(p))
        except Exception:
            p.kill()

    @staticmethod
    def _kill_hard(p):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass


PHASES = [("1/3", "Reading files"), ("2/3", "Analysing images"), ("3/3", "Figures & spreadsheets")]


class RunPage(Page):
    run_finished = Signal(list)          # list of (kind, path)
    run_ended = Signal(int, str)         # exit code (0 ok, -2 stopped), one-line summary

    def __init__(self, data_page, params_page, outputs_page, parent=None):
        super().__init__("Run", "Check the summary and start. The analysis runs in the background; you can "
                         "keep using the app (e.g. look at earlier results) while it works.")
        self.data, self.params, self.outputs = data_page, params_page, outputs_page
        self.proc = None
        self.outputs_found = []

        # --- summary
        card = Card("Ready to run")
        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        self.sum_files = QLabel("0")
        self.sum_images = QLabel("0")
        self.sum_mode = QLabel("—")
        for i, (w, cap) in enumerate([(self.sum_files, "files"), (self.sum_images, "images to analyse"),
                                      (self.sum_mode, "mode")]):
            w.setObjectName("bigNumber")
            grid.addWidget(w, 0, i)
            c = QLabel(cap)
            c.setObjectName("hint")
            grid.addWidget(c, 1, i)
        grid.setColumnStretch(3, 1)
        card.lay.addLayout(grid)
        self.sum_text = hint("")
        card.lay.addWidget(self.sum_text)
        self.problems = banner("")
        self.problems.hide()
        card.lay.addWidget(self.problems)
        self.start_btn = button("Start analysis", "big", slot=self.start)
        self.stop_btn = button("Stop", "danger", slot=self.stop)
        self.stop_btn.setEnabled(False)
        self.copy_btn = button("Copy command line", "link", slot=self.copy_command,
                               tip="The same analysis as a Terminal command (for your notes or scripts)")
        card.lay.addWidget(hbox(self.start_btn, self.stop_btn, "stretch", self.copy_btn, stretch_end=False))
        self.lay.addWidget(card)

        # --- progress
        prog = Card("Progress")
        self.folder_label = QLabel("")
        self.folder_label.setStyleSheet("font-weight: 600;")
        prog.lay.addWidget(self.folder_label)
        chips = QHBoxLayout()
        self.chips = [StepChip(t) for _, t in PHASES]
        for c in self.chips:
            chips.addWidget(c)
        chips.addStretch(1)
        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("hint")
        chips.addWidget(self.elapsed_label)
        prog.lay.addLayout(chips)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        prog.lay.addWidget(self.bar)
        self.item_label = QLabel("Not started")
        self.item_label.setObjectName("hint")
        prog.lay.addWidget(self.item_label)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.setMinimumHeight(220)
        self.details = QCheckBox("Show details in the log (per-file metadata, thresholds)")
        self.details.setChecked(True)
        self.notify_cb = QCheckBox("Sound and notification when it finishes")
        self.notify_cb.setChecked(True)
        self.notify_cb.setToolTip("So you can work in other programs during a long run")
        self.notify_cb.toggled.connect(lambda on: QSettings().setValue("run/notify", "true" if on else "false"))
        prog.lay.addWidget(self.log, 1)
        prog.lay.addWidget(hbox(self.details, self.notify_cb, button("Save log…", "link", slot=self.save_log),
                                button("Clear log", "link", slot=self.log.clear)))
        ease.remember("run", dict(details=self.details))
        self.notify_cb.setChecked(QSettings().value("run/notify", "true") not in (False, "false"))
        self.lay.addWidget(prog, 1)

        # --- results
        res = Card("Results")
        self.res_banner = banner("", ok=True)
        self.res_banner.setTextFormat(Qt.RichText)
        self.res_banner.hide()
        res.lay.addWidget(self.res_banner)
        self.res_list = QListWidget()
        self.res_list.setObjectName("plain")
        self.res_list.setMaximumHeight(170)
        self.res_list.itemDoubleClicked.connect(lambda it: open_path(it.data(Qt.UserRole)))
        res.lay.addWidget(self.res_list)
        self.open_folder_btn = button("Open results folder", "primary", slot=self.open_results)
        self.open_sheet_btn = button("Open summary spreadsheet", slot=self.open_sheet)
        self.to_plots_btn = button("Make custom plots from these results →", slot=self._to_plots)
        for b in (self.open_folder_btn, self.open_sheet_btn, self.to_plots_btn):
            b.setEnabled(False)
        res.lay.addWidget(hbox(self.open_folder_btn, self.open_sheet_btn, self.to_plots_btn))
        res.lay.addWidget(hint("Double-click a line to open it."))
        self.lay.addWidget(res)
        self.go_plots = None              # set by the main window

        self._t0 = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        for pg in (data_page, params_page, outputs_page):
            pg.changed.connect(self.refresh_summary)
        self.refresh_summary()

    # ------------------------------------------------------------------ summary / validation
    def problems_list(self):
        out = []
        for pg in (self.data, self.params, self.outputs):
            e = pg.validate()
            if e:
                out.append(e)
        return out

    def refresh_summary(self):
        v = self.params.values()
        sel = self.data.selected_files()
        self.sum_files.setText(str(len(sel)))
        n, known = self.data.n_images(v["zstep"], v["tstep"])
        self.sum_images.setText(f"{n}" + ("" if known else "+"))
        self.sum_mode.setText({"file": "One file", "folder": "One folder", "folders": "Several folders"}[self.data.mode])
        thr = (f"fixed threshold {v['threshold']:g}" if v["threshold"] is not None else
               "Otsu threshold per file" if v["threshold_mode"] == "per-file" else "one Otsu threshold for all files")
        root = self.outputs.results_root()
        def nth(n, what):
            return f"every {what}" if n == 1 else f"every {n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')} {what}"
        self.sum_text.setText(
            f"{self.data.describe()}; {nth(v['zstep'], 'z-slice')}"
            + (f", {nth(v['tstep'], 'time point')}" if v["tstep"] > 1 else "") + f"; {thr}; hotspot {v['hotspot']}; "
            f"{v['workers']} parallel worker{'s' if v['workers'] > 1 else ''}. Results "
            + (f"in {root}" if root else "next to the data") + "."
            + (f" {self.data.n_manual()} value(s) typed by hand in the Files step." if self.data.n_manual() else ""))
        probs = self.problems_list()
        self.problems.setText("\n".join("• " + p for p in probs))
        self.problems.setVisible(bool(probs))
        running = self.proc is not None
        self.start_btn.setEnabled(not probs and not running)

    def build_args(self):
        self._list_file = os.path.join(tempfile.gettempdir(), f"collagen_files_{os.getpid()}.txt")
        self._meta_file = os.path.join(tempfile.gettempdir(), f"collagen_metadata_{os.getpid()}.json")
        args = self.data.engine_args(self._list_file)
        meta = self.data.metadata()
        if meta["files"] or meta["fields"]:
            import json
            with open(self._meta_file, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=1)
            args += ["--metadata", self._meta_file]
        return args + self.params.engine_args() + self.outputs.engine_args() + ["--gui"]

    def copy_command(self):
        probs = self.problems_list()
        if probs and not self.data.selected_files():
            QMessageBox.information(self, "Command line", probs[0])
            return
        args = [a for a in self.build_args() if a != "--gui"]
        if "--metadata" in args:
            i = args.index("--metadata")
            args = args[:i] + args[i + 2:]
            args.append("   # (values typed in the Files step are not part of this command)")
        if "--files-from" in args:
            i = args.index("--files-from")
            files = self.data.selected_files()
            args = args[:i] + args[i + 2:]
            if len({os.path.dirname(f) for f in files}) == 1 and len(files) == len(self.data.rows):
                args = [os.path.dirname(files[0])] + args
            else:
                args = list(dict.fromkeys(os.path.dirname(f) for f in files)) + args
                args.append("   # (the app analyses only the ticked files; this command runs whole folders)")
        cmd = "python3 collagen_reflectance_analysis.py " + " ".join(quote(a) for a in args)
        QGuiApplication.clipboard().setText(cmd)
        self.item_label.setText("Command copied to the clipboard: " + cmd[:200])

    # ------------------------------------------------------------------ run
    def start(self):
        probs = self.problems_list()
        if probs:
            QMessageBox.warning(self, "Cannot start", "\n".join(probs))
            return
        args = self.build_args()
        self.log.clear()
        self.res_list.clear()
        self.res_banner.hide()
        self.outputs_found = []
        for c in self.chips:
            c.set_state("wait")
        self.bar.setValue(0)
        self.folder_label.setText("")
        self._append("Starting: " + " ".join(quote(a) for a in args if a != "--gui"), "#7fd3e3")
        self.proc = EngineProcess(args)
        self.proc.line.connect(self._on_line)
        self.proc.progress.connect(self._on_progress)
        self.proc.output.connect(self._on_output)
        self.proc.finished_code.connect(self._on_finished)
        self.proc.start()
        self._t0 = time.time()
        self._timer.start(1000)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        for b in (self.open_folder_btn, self.open_sheet_btn, self.to_plots_btn):
            b.setEnabled(False)

    def stop(self):
        if self.proc and QMessageBox.question(self, "Stop", "Stop the running analysis? Results written so far "
                                              "are kept, but the spreadsheets of unfinished folders are not "
                                              "written.") == QMessageBox.Yes:
            self.proc.stop()
            self._append("Stopping…", "#ffcc80")

    def is_running(self):
        return self.proc is not None

    def _tick(self):
        if self._t0:
            el = int(time.time() - self._t0)
            self.elapsed_label.setText(f"elapsed {el // 60}:{el % 60:02d}")

    def _append(self, text, color=None):
        cur = self.log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))
        cur.insertText(text + "\n", fmt)
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()

    def _on_line(self, ln):
        low = ln.lower()
        if ln.startswith("  ") and not self.details.isChecked() and "warning" not in low:
            return
        if "error" in low or "traceback" in low:
            col = "#ff8a80"
        elif "warning" in low:
            col = "#ffcc80"
        elif ln.startswith("==="):
            col = "#7fd3e3"
            self.folder_label.setText(ln.strip("= ").replace("folder", "Folder", 1))
        elif ln.startswith("done") or ln.startswith("Run time") or ln.startswith("combined"):
            col = "#a5d6a7"
        else:
            col = None
        self._append(ln, col)

    def _on_progress(self, phase, n, total, elapsed, item):
        idx = next((i for i, (k, _) in enumerate(PHASES) if phase.startswith(k)), None)
        if idx is None:
            return
        for i, c in enumerate(self.chips):
            c.set_state("done" if i < idx or (i == idx and n >= total) else "active" if i == idx else "wait")
        self.bar.setValue(int(1000 * n / max(total, 1)))
        left = ""
        if 0 < n < total and elapsed > 2:
            r = elapsed / n * (total - n)
            left = f" · about {int(r // 60)}:{int(r % 60):02d} left in this step"
        self.item_label.setText(f"{PHASES[idx][1]}: {n} of {total}{left}" + (f"  —  {item}" if item else ""))

    def _on_output(self, kind, path):
        self.outputs_found.append((kind, path))
        label = {"results": "Results folder", "workbook": "Summary spreadsheet", "summary": "Combined summary",
                 "tiles": "Per-tile table", "distributions": None, "figure": None, "file_workbook": None}.get(kind)
        if label:
            it = QListWidgetItem(f"{label}:  {path}")
            it.setData(Qt.UserRole, path)
            it.setToolTip(path)
            if kind == "summary":
                f = it.font(); f.setBold(True); it.setFont(f)
            self.res_list.addItem(it)

    def _on_finished(self, code):
        self._timer.stop()
        self._tick()
        self.proc = None
        self.stop_btn.setEnabled(False)
        nfig = sum(1 for k, _ in self.outputs_found if k == "figure")
        if code == 0:
            for c in self.chips:
                c.set_state("done")
            self.bar.setValue(1000)
            self.res_banner.setObjectName("bannerOk")
            took = self.elapsed_label.text().replace('elapsed ', '')
            summary = f"Finished in {took} (min:s) — {nfig} figure(s) and the spreadsheets were saved."
            notes = explain(self.log.toPlainText())
            self.res_banner.setText(summary + (("<br><b>Worth checking:</b>" + as_html(notes)) if notes else ""))
            self._append("Finished.", "#a5d6a7")
        elif code == -2:
            self.res_banner.setObjectName("banner")
            summary = "Stopped. Files written before stopping are kept."
            self.res_banner.setText(summary)
        else:
            self.res_banner.setObjectName("banner")
            self._append(f"Stopped with error code {code}.", "#ff8a80")
            notes = explain(self.log.toPlainText())
            summary = "The analysis stopped with an error."
            self.res_banner.setText(
                f"<b>{summary}</b>" + (as_html(notes) if notes else
                                       " See the last lines of the log. Common causes: a file that cannot be read, a "
                                       "TIFF without pixel size, or too little memory (lower ‘Parallel workers’ in "
                                       "the Settings step)."))
        self.res_banner.style().unpolish(self.res_banner)
        self.res_banner.style().polish(self.res_banner)
        self.res_banner.show()
        have = bool(self.outputs_found)
        self.open_folder_btn.setEnabled(any(k == "results" for k, _ in self.outputs_found))
        self.open_sheet_btn.setEnabled(any(k in ("workbook", "summary") for k, _ in self.outputs_found))
        self.to_plots_btn.setEnabled(any(k in ("workbook", "summary") for k, _ in self.outputs_found))
        self.refresh_summary()
        if code == 0:
            self.run_finished.emit(list(self.outputs_found))
        self.run_ended.emit(code, summary)
        try:
            os.remove(self._list_file)
        except OSError:
            pass

    # ------------------------------------------------------------------ results
    def _first(self, kinds):
        for k in kinds:
            for kk, p in self.outputs_found:
                if kk == k:
                    return p
        return None

    def open_results(self):
        """One folder: its results folder. Several: the folder holding the combined summary."""
        summ = self._first(["summary"])
        p = os.path.dirname(summ) if summ else self._first(["results"])
        if p:
            open_path(p)

    def open_sheet(self):
        p = self._first(["summary", "workbook"])
        if p:
            open_path(p)

    def _to_plots(self):
        if self.go_plots:
            self.go_plots([p for k, p in self.outputs_found if k in ("workbook",)])

    def save_log(self):
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getSaveFileName(self, "Save log", "analysis_log.txt", "Text (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(self.log.toPlainText())
