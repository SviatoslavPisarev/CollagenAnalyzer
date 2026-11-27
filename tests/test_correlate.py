"""Correlation search of the Patterns page (ml/correlate.py) on synthetic tile tables with known relations."""
import numpy as np
import pandas as pd

from ml import correlate as CR

FEATS = ["area_frac_pct", "length_density_um_per_um2", "pore_lt_mean_um", "fiber_lt_mean_um", "order_S", "coherency"]


def tiles(seed=1):
    """8 gels (Before/After), 5 depths, 6×6 tiles. After AMF, S rises with frequency; S also rises with depth;
    fiber thickness is pure noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for s, freq in enumerate([100, 200, 400, 800, 100, 200, 400, 800]):
        for cond in ("Before AMF", "After AMF"):
            after = cond.startswith("After")
            for z in range(5):
                for tr in range(6):
                    for tc in range(6):
                        dens = 30 + rng.normal(0, 5)
                        S = 0.2 + (0.0003 * freq if after else 0) + rng.normal(0, 0.05) + 0.02 * z
                        rows.append(dict(file=f"f{s}_{cond[0]}", sample=f"s{s}", date="010126", condition=cond,
                                         arm="NC + AMF", amf_freq_hz=freq if after else np.nan, nc_conc_nM=1.0,
                                         t_index=0, z_index=z, z_um=float(z), tile_row=tr, tile_col=tc,
                                         area_frac_pct=dens, length_density_um_per_um2=dens / 10 + rng.normal(0, .3),
                                         pore_lt_mean_um=50 / dens + rng.normal(0, .1),
                                         fiber_lt_mean_um=0.5 + rng.normal(0, .05), order_S=S,
                                         coherency=S + rng.normal(0, .05)))
    return pd.DataFrame(rows)


def test_settings_finds_frequency_effect():
    t, ch = CR.run("settings", tiles(), FEATS)
    assert len(ch) == 8
    top = t.iloc[0]
    assert top["x"] == "amf_freq_hz" and top["y"] in ("d_order_S", "d_coherency")
    assert top["rho"] > 0.9 and top["q"] < 0.01 and top["unit_of_n"] == "samples"
    noise = t[t["y"] == "d_fiber_lt_mean_um"].iloc[0]
    assert abs(noise["rho"]) < abs(top["rho"])


def test_linked_pairs_hidden_or_flagged():
    shown, _ = CR.run("files", tiles(), FEATS, hide_linked=False)
    hidden, _ = CR.run("files", tiles(), FEATS, hide_linked=True)
    assert len(shown) == 15 and len(hidden) == 15 - 5
    assert not hidden["linked"].any() and shown["linked"].sum() == 5


def test_inside_images_uses_images_as_units():
    t, per = CR.run("tiles", tiles(), FEATS, hide_linked=False)
    r = t[(t["x"] == "area_frac_pct") & (t["y"] == "pore_lt_mean_um")].iloc[0]
    assert r["rho"] < -0.8 and r["n"] == 80 and r["unit_of_n"] == "images"
    assert len(per[("area_frac_pct", "pore_lt_mean_um")]) == 80


def test_depth_trend():
    t, _ = CR.run("depth", tiles(), FEATS)
    top = t.iloc[0]
    assert top["y"] in ("order_S", "coherency") and top["rho"] > 0.8 and top["n"] == 16
    assert CR.headline(t)[0].startswith(("alignment S goes up", "coherency goes up"))


def test_bh():
    q = CR.bh([0.01, 0.04, np.nan, 0.03])
    assert np.isnan(q[2]) and np.allclose(q[[0, 1, 3]], [0.03, 0.04, 0.04])
