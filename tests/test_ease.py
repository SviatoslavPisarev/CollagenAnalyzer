"""Ease-of-use helpers: results in dropped folders, recent analyses, remembered choices, versions, error help."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QSpinBox  # noqa: E402

from gui import ease  # noqa: E402
from gui.errors import explain, friendly  # noqa: E402


@pytest.fixture(scope="module")
def app():
    QCoreApplication.setOrganizationName("CollagenAnalyzerTests")
    QCoreApplication.setApplicationName("tests")
    a = QApplication.instance() or QApplication([])
    QSettings().clear()
    yield a
    QSettings().clear()


def touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return str(p)


def test_find_results(tmp_path):
    r = tmp_path / "run"
    a = touch(r / "112726_results" / "112726_summary.xlsx")
    t = touch(r / "112726_results" / "112726_tiles.csv")
    touch(r / "112726_results" / "~$112726_summary.xlsx")
    touch(r / "112726_results" / "notes.xlsx")
    books, tiles, images = ease.find_results([str(r)])
    assert books == [a] and tiles == [t] and images == []
    assert ease.tiles_near([a]) == [t]
    # a combined summary.xlsx stands for the workbooks below it
    c = touch(tmp_path / "summary.xlsx")
    assert ease.find_results([str(tmp_path)])[0] == [c]
    img = touch(tmp_path / "imgs" / "112726_1_x.nd2")
    assert ease.find_results([str(tmp_path / "imgs")])[2] == [img]


def test_recent(app, tmp_path):
    a = touch(tmp_path / "d1" / "r" / "112726_summary.xlsx")
    b = touch(tmp_path / "d2" / "r" / "112826_summary.xlsx")
    ease.add_recent([a])
    ease.add_recent([b])
    ease.add_recent([a])                           # again: moves to the top, no duplicate
    lst = ease.recent_list()
    assert [e["books"] for e in lst] == [[a], [b]]
    assert ease.describe(lst[0])[0] == "d1 › r"
    os.remove(b)
    assert ease.existing(lst[1]) == ([], [])
    ease.forget_recent(lst[1]["folder"])
    assert len(ease.recent_list()) == 1


def test_remember(app):
    cb, chk, sp = QComboBox(), QCheckBox(), QSpinBox()
    for k in ("a", "b", "c"):
        cb.addItem(k.upper(), k)
    ease.remember("t", dict(cb=cb, chk=chk, sp=sp))
    cb.setCurrentIndex(2)
    chk.setChecked(True)
    sp.setValue(7)
    cb2, chk2, sp2 = QComboBox(), QCheckBox(), QSpinBox()
    for k in ("a", "b", "c"):
        cb2.addItem(k.upper(), k)
    ease.remember("t", dict(cb=cb2, chk=chk2, sp=sp2))
    assert (cb2.currentData(), chk2.isChecked(), sp2.value()) == ("c", True, 7)


def test_versions():
    assert ease.is_newer("v1.2", "1.1") and ease.is_newer("v1.10", "1.9") and ease.is_newer("v2.0", "1.1")
    assert not ease.is_newer("v1.1", "1.1") and not ease.is_newer("", "1.1")


def test_error_help():
    log = ("Starting: CollagenAnalyzer --core /Users/x/Library/Mobile Documents/com~apple~CloudDocs/data\n"
           "x.tif: pixel size not found in the TIFF; give it with --pixel-size (µm)\n"
           "MemoryError: Unable to allocate 2.00 GiB\n")
    what = [w for w, _ in explain(log)]
    assert what == ["A TIFF file has no pixel size.", "The computer ran out of memory."]
    assert "What to do" in friendly("[Errno 13] Permission denied: 'a.xlsx'")
    assert friendly("something odd") == "something odd"
