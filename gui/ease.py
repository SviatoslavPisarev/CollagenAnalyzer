"""Ease-of-use helpers: results in dropped files / folders, recent analyses, remembered choices,
the run-finished notice and the check for a new version."""
import glob
import json
import os
import re
import time

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Signal, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QSpinBox,
                               QSystemTrayIcon)

SUMMARY_RE = re.compile(r"^((\d{6}|other)(-\d{6})?_summary|summary)\.xlsx$")
IMAGE_EXT = (".nd2", ".tif", ".tiff")
REPO = "SviatoslavPisarev/CollagenAnalyzer"


# ---------------------------------------------------------------- results in files / folders
def _under(p, d):
    try:
        return os.path.commonpath([os.path.abspath(p), os.path.abspath(d)]) == os.path.abspath(d)
    except ValueError:                                  # different drives (Windows)
        return False


def without_parts(books):
    """A combined summary.xlsx already holds the per-date workbooks of the folders below it."""
    combined = [p for p in books if os.path.basename(p) == "summary.xlsx"]
    return combined + [p for p in books if os.path.basename(p) != "summary.xlsx"
                       and not any(_under(p, os.path.dirname(c)) for c in combined)]


def find_results(paths):
    """(result spreadsheets, tile tables, image files) in the given files and folders (folders searched with
    their subfolders)."""
    books, tiles, images = [], [], []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            books += [q for q in glob.glob(os.path.join(p, "**", "*.xlsx"), recursive=True)
                      if SUMMARY_RE.match(os.path.basename(q))]
            tiles += glob.glob(os.path.join(p, "**", "*_tiles.csv"), recursive=True)
            if not books and not tiles:
                images += [q for e in IMAGE_EXT for q in glob.glob(os.path.join(p, "*" + e))]
        elif os.path.isfile(p):
            low, base = p.lower(), os.path.basename(p)
            if low.endswith(".xlsx") and not base.startswith(("~$", "._")):
                books.append(p)
            elif low.endswith("_tiles.csv"):
                tiles.append(p)
            elif low.endswith(IMAGE_EXT):
                images.append(p)
    uniq = lambda xs: [x for x in dict.fromkeys(xs) if not os.path.basename(x).startswith("._")]   # noqa: E731
    return without_parts(uniq(books)), uniq(tiles), uniq(images)


def tiles_near(books):
    """The tile tables written next to these result spreadsheets (by the same runs)."""
    out = []
    for b in books:
        d, base = os.path.dirname(b), os.path.basename(b)
        if base == "summary.xlsx":
            out += glob.glob(os.path.join(d, "**", "*_tiles.csv"), recursive=True)
        else:
            pre = base.split("_summary")[0]
            out += glob.glob(os.path.join(d, pre + "_tiles.csv")) or glob.glob(os.path.join(d, "*_tiles.csv"))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- recent analyses
MAX_RECENT = 8


class _RecentSignals(QObject):
    changed = Signal()


recent_changed = _RecentSignals()


def recent_list():
    try:
        lst = json.loads(QSettings().value("recent/analyses", "") or "[]")
    except ValueError:
        lst = []
    return [e for e in lst if isinstance(e, dict) and e.get("books")]


def _save_recent(lst):
    QSettings().setValue("recent/analyses", json.dumps(lst[:MAX_RECENT]))
    recent_changed.changed.emit()


def add_recent(books, tiles=None):
    """Remember these results (most recent first; one entry per results folder)."""
    books = [os.path.abspath(b) for b in books if b]
    if not books:
        return
    tiles = [os.path.abspath(t) for t in (tiles if tiles is not None else tiles_near(books))]
    dirs = [os.path.dirname(b) for b in books]
    try:
        folder = os.path.commonpath(dirs)
    except ValueError:
        folder = dirs[0]
    entry = dict(folder=folder, books=books, tiles=tiles, when=time.strftime("%Y-%m-%d %H:%M"))
    key = os.path.normcase(folder)
    _save_recent([entry] + [e for e in recent_list() if os.path.normcase(e.get("folder", "")) != key])


def forget_recent(folder):
    key = os.path.normcase(folder)
    _save_recent([e for e in recent_list() if os.path.normcase(e.get("folder", "")) != key])


def existing(entry):
    """The entry's files that are still there: (books, tiles)."""
    return [b for b in entry.get("books", []) if os.path.exists(b)], \
           [t for t in entry.get("tiles", []) if os.path.exists(t)]


def describe(entry):
    """(title, detail) for showing an entry: 'data folder › results folder', 'date · n spreadsheets'."""
    f = entry.get("folder", "")
    name, parent = os.path.basename(f) or f, os.path.basename(os.path.dirname(f))
    title = f"{parent} › {name}" if parent else name
    nb = len(entry.get("books", []))
    detail = entry.get("when", "") + (f" · {nb} spreadsheets" if nb > 1 else "")
    return title, detail


# ---------------------------------------------------------------- remembered choices
def _get(w):
    if isinstance(w, QComboBox):
        d = w.currentData()
        return ["data", str(d)] if d is not None else ["text", w.currentText()]
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        return w.value()
    if isinstance(w, QListWidget):
        return [str(w.item(i).data(Qt.UserRole)) for i in range(w.count())
                if w.item(i).checkState() == Qt.Checked]
    if isinstance(w, QLineEdit):
        return w.text()
    raise TypeError(type(w))


def _set(w, v):
    if isinstance(w, QComboBox):
        kind, val = v
        i = next((i for i in range(w.count()) if str(w.itemData(i)) == val), -1) if kind == "data" \
            else w.findText(val)
        if i >= 0:
            w.setCurrentIndex(i)
    elif isinstance(w, QCheckBox):
        w.setChecked(bool(v))
    elif isinstance(w, QSpinBox):
        w.setValue(int(v))
    elif isinstance(w, QDoubleSpinBox):
        w.setValue(float(v))
    elif isinstance(w, QListWidget):
        on = set(v)
        for i in range(w.count()):
            it = w.item(i)
            it.setCheckState(Qt.Checked if str(it.data(Qt.UserRole)) in on else Qt.Unchecked)
    elif isinstance(w, QLineEdit):
        w.setText(str(v))


def _signal(w):
    if isinstance(w, QComboBox):
        return w.currentIndexChanged
    if isinstance(w, QCheckBox):
        return w.toggled
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        return w.valueChanged
    if isinstance(w, QListWidget):
        return w.itemChanged
    return w.textChanged


def remember(prefix, widgets):
    """Restore the widgets' last values (QSettings prefix/key) and save every change from now on."""
    st = QSettings()
    for key, w in widgets.items():
        k = f"{prefix}/{key}"
        raw = st.value(k)
        if raw:
            try:
                _set(w, json.loads(raw))
            except (ValueError, TypeError, IndexError):
                pass
        _signal(w).connect(lambda *_, k=k, w=w: QSettings().setValue(k, json.dumps(_get(w))))


# ---------------------------------------------------------------- run finished
def notify(window, title, text):
    """Tell the user a long job ended: sound, dock bounce / taskbar flash, and a system notification when the
    app is not in front."""
    if QSettings().value("run/notify", "true") in (False, "false"):
        return
    app = QApplication.instance()
    app.beep()
    if window.isActiveWindow():
        return
    app.alert(window, 0)                                # until the user comes back
    if QSystemTrayIcon.isSystemTrayAvailable():
        icon = window.windowIcon()
        if icon.isNull():
            from . import style
            icon = style.icon("logo", style.ACCENT, 32)
        tray = QSystemTrayIcon(icon, window)
        tray.show()
        tray.showMessage(title, text, QSystemTrayIcon.Information, 10000)
        tray.messageClicked.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
        QTimer.singleShot(15000, tray.deleteLater)


# ---------------------------------------------------------------- new version
def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def is_newer(tag, current):
    return version_tuple(tag) > version_tuple(current)


class UpdateCheck(QObject):
    """Asks GitHub for the latest release (at most once a day); emits available(version, url) if it is newer."""
    available = Signal(str, str)
    URL = f"https://api.github.com/repos/{REPO}/releases/latest"

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.current = current
        self.nam = None

    @staticmethod
    def enabled():
        return QSettings().value("updates/check", "true") not in (False, "false")

    def start(self, force=False):
        st = QSettings()
        if not force and not self.enabled():
            return
        last = float(st.value("updates/last", 0) or 0)
        if not force and time.time() - last < 20 * 3600:
            self._report(st.value("updates/tag", ""), st.value("updates/url", ""))
            return
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
        self.nam = QNetworkAccessManager(self)
        req = QNetworkRequest(QUrl(self.URL))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", b"CollagenAnalyzer")
        req.setTransferTimeout(10000)
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply):
        from PySide6.QtNetwork import QNetworkReply
        try:
            if reply.error() != QNetworkReply.NoError:
                return
            data = json.loads(bytes(reply.readAll()).decode("utf-8", "replace"))
            tag, url = str(data.get("tag_name", "")), str(data.get("html_url", ""))
            st = QSettings()
            st.setValue("updates/last", time.time())
            st.setValue("updates/tag", tag)
            st.setValue("updates/url", url)
            self._report(tag, url)
        except (ValueError, AttributeError):
            pass
        finally:
            reply.deleteLater()

    def _report(self, tag, url):
        if tag and is_newer(tag, self.current) and QSettings().value("updates/skip", "") != tag:
            self.available.emit(tag, url or f"https://github.com/{REPO}/releases/latest")
