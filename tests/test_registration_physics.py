"""Before/after registration, tile pairing and the nanocage physics helper."""
import math

import numpy as np
import pandas as pd
import pytest

import changemap as CM
import collagen_core as core
import nc_physics as NP
import synth


@pytest.fixture(scope="module")
def pair():
    img, t = synth.network(seed=3, shape=(320, 320), kappa=0.5)
    moved, _ = synth.network(seed=9, shape=(320, 320), centers=t["centers"] + np.array([14, -9]), angles=t["angles"])
    return img, moved, t


def test_recovers_shift(pair):
    img, moved, _ = pair
    v = core.analysis_mask(img.shape, (160, 160, 40), edge=16)       # with an excluded hotspot
    r = CM.register(img, moved, v, v, 0.1)
    assert r["status"] == "registered"
    assert (r["shift_y_px"], r["shift_x_px"]) == pytest.approx((9, -14), abs=1)
    assert r["shift_x_um"] == pytest.approx(-1.4, abs=0.1)


def test_unrelated_field_is_flagged(pair):
    img, _, _ = pair
    other, _ = synth.network(seed=77, shape=(320, 320))
    v = np.ones(img.shape, bool)
    assert CM.register(img, other, v, v, 0.1)["status"] == "different field?"


def test_remodelled_same_field_is_recognised(pair):
    img, _, t = pair
    turned, _ = synth.network(seed=9, shape=(320, 320), centers=t["centers"] + np.array([14, -9]),
                              angles=t["angles"] * 0.6)
    v = np.ones(img.shape, bool)
    r = CM.register(img, turned, v, v, 0.1)
    assert r["status"] in CM.SAME_FIELD
    assert r["z"] > CM.Z_MIN


def test_tile_pairing_follows_the_shift():
    tb = pd.DataFrame([dict(tile_row=r, tile_col=c, x_um=0, y_um=0, order_S=r * 10 + c) for r in range(4) for c in range(4)])
    ta = tb.copy()
    ta["order_S"] = [((r - 1) * 10 + (c + 2)) for r, c in zip(ta["tile_row"], ta["tile_col"])]   # moved 1 down, 2 left
    # content at before tile (r, c) is at after tile (r + 1, c - 2): shift moving after onto before = (-1, +2) tiles
    tc = CM.tile_changes(tb, ta, -1 * 100, 2 * 100, 100, metrics=["order_S"])
    assert len(tc) == 6 and (tc["order_S_change"] == 0).all() and (tc["overlap"] == 1).all()


def test_brownian_relaxation_time():
    r = NP.relaxation(dh_nm=30, core_nm=20, eta_mPa_s=0.89, T_K=298.15, freq_hz=500, field_mT=40)
    tb = 3 * 0.89e-3 * (math.pi / 6 * (30e-9) ** 3) / (1.380649e-23 * 298.15)
    assert r["tau_brown_s"] == pytest.approx(tb)
    assert r["f_peak_brown_hz"] == pytest.approx(1 / (2 * math.pi * tb))
    assert r["phase_lag_brown_deg"] < 5


def test_hollow_cage_has_less_magnetic_volume():
    assert NP.magnetic_volume(20, 4) < NP.magnetic_volume(20)
    assert NP.magnetic_volume(20, 0) == NP.magnetic_volume(20)
