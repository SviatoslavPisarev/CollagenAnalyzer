"""Trend lines of the Results & plots page (needs PySide6, skipped without it)."""
import numpy as np
import pytest

pytest.importorskip("PySide6")
from gui.plot_style import fit_line, rc, DEFAULTS  # noqa: E402


def test_linear_fit_recovers_line():
    x = np.array([1, 2, 3, 4, 5.0])
    r = fit_line(x, 2 + 0.5 * x, "linear")
    assert r["ys"][0] == pytest.approx(2.5) and r["ys"][-1] == pytest.approx(4.5)
    assert "R² = 1.00" in r["text"]


def test_log_fit_and_band():
    rng = np.random.default_rng(0)
    x = np.array([10, 30, 100, 300, 1000.0])
    y = 1 + 0.3 * np.log10(x) + rng.normal(0, 0.01, 5)
    r = fit_line(x, y, "logx")
    assert r["xs"].min() == pytest.approx(10) and r["xs"].max() == pytest.approx(1000)
    assert np.all(r["lo"] <= r["ys"]) and np.all(r["hi"] >= r["ys"])
    assert fit_line([1, 1], [2, 3], "linear") is None          # no spread in x
    assert fit_line([1, 2, 3], [2, 2, 5], "mean")["ys"][0] == pytest.approx(3)


def test_rc_from_style():
    p = rc(dict(DEFAULTS, font="Arial", size_tick=7, clean_axes=True))
    assert p["font.family"][0] == "Arial" and p["xtick.labelsize"] == 7 and p["axes.spines.top"] is False
