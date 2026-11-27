"""Pairing, control correction and statistics (gels as replicates)."""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

import gelstats as GS
from ml import planner as PL


def _pf():
    """Per-file table: 3 treated gels, 3 sham gels (same date), 1 AMF-only gel, 1 treated gel on another date."""
    rows = []
    def gel(date, n, before, after, cond, nc=0.5, freq=500.0):
        rows.append(dict(date=date, sample=f"{date}_{n}", file=f"{date}_{n}_b", condition="Before AMF",
                         nc_conc_nM=nc, order_S_mean=before, qc_status="ok", amf_freq_hz=np.nan))
        rows.append(dict(date=date, sample=f"{date}_{n}", file=f"{date}_{n}_a", condition=cond, nc_conc_nM=nc,
                         order_S_mean=after, qc_status="ok", amf_freq_hz=freq if cond == "After AMF" else np.nan))
    gel("112026", 1, 0.30, 0.45, "After AMF")
    gel("112026", 2, 0.32, 0.46, "After AMF")
    gel("112026", 3, 0.28, 0.40, "After AMF")
    gel("112026", 4, 0.30, 0.31, "After sham")
    gel("112026", 5, 0.31, 0.33, "After sham")
    gel("112026", 6, 0.29, 0.29, "After sham")
    gel("112026", 7, 0.30, 0.33, "After AMF", nc=0.0)
    gel("112126", 1, 0.30, 0.40, "After AMF")
    return pd.DataFrame(rows)


def test_assign_arms():
    pf = GS.assign_arms(_pf())
    a = dict(zip(pf["file"], pf["arm"]))
    assert a["112026_1_a"] == a["112026_1_b"] == GS.TREATED
    assert a["112026_4_b"] == GS.SHAM
    assert a["112026_7_a"] == GS.AMF_ONLY


def test_pair_and_correct():
    exp, notes = GS.pair_changes(_pf(), "order_S", change="diff")
    assert len(exp) == 8 and not notes
    exp = GS.control_correct(exp)
    sham_mean = np.mean([0.01, 0.02, 0.0])
    r = exp.set_index("file")
    assert r.loc["112026_1_a", "y"] == pytest.approx(0.15)
    assert r.loc["112026_1_a", "y_corrected"] == pytest.approx(0.15 - sham_mean)
    assert r.loc["112026_1_a", "control_match"] == "same date"
    assert r.loc["112126_1_a", "control_match"] == "all dates"        # no sham that day
    assert r.loc["112026_7_a", "control_arm"] == GS.SHAM              # no 'sham, no NC' gel: falls back
    assert np.isnan(r.loc["112026_4_a", "y_corrected"])               # controls are not corrected


def test_pct_change():
    exp, _ = GS.pair_changes(_pf(), "order_S", change="pct")
    assert exp.set_index("file").loc["112026_1_a", "y"] == pytest.approx(50.0)


def test_qc_exclusion():
    pf = _pf()
    pf.loc[pf["file"] == "112026_2_b", "qc_status"] = "check"
    exp, notes = GS.pair_changes(pf, "order_S", change="diff")
    assert "112026_2_a" not in set(exp["file"]) and notes


def test_statistics_match_scipy():
    exp, _ = GS.pair_changes(_pf(), "order_S", change="diff")
    summ, comp = GS.arm_statistics(exp, "y")
    t = summ.set_index("arm").loc[GS.TREATED]
    v = np.array([0.15, 0.14, 0.12, 0.10])
    assert t["n_gels"] == 4
    assert t["mean"] == pytest.approx(v.mean())
    assert t["p_ttest_vs0"] == pytest.approx(stats.ttest_1samp(v, 0).pvalue)
    h = stats.t.ppf(0.975, 3) * v.std(ddof=1) / 2
    assert t["ci95_low"] == pytest.approx(v.mean() - h)
    c = comp.set_index("comparison").loc["field effect (NC + AMF vs sham)"]
    s = np.array([0.01, 0.02, 0.0])
    assert c["p_welch"] == pytest.approx(stats.ttest_ind(v, s, equal_var=False).pvalue)
    assert c["difference"] == pytest.approx(v.mean() - s.mean())
    assert c["hedges_g"] > 3


def test_gel_is_the_replicate():
    pf = _pf()
    extra = pf[pf["file"] == "112026_1_a"].copy()
    extra["file"] = "112026_1_a2"
    exp, _ = GS.pair_changes(pd.concat([pf, extra]), "order_S", change="diff")
    summ, _ = GS.arm_statistics(exp, "y")
    assert summ.set_index("arm").loc[GS.TREATED, "n_gels"] == 4     # two files of one gel = one replicate


def test_holm():
    p = GS.holm([0.01, 0.04, np.nan, 0.03])
    assert p[0] == pytest.approx(0.03) and p[3] == pytest.approx(0.06) and p[1] == pytest.approx(0.06)
    assert np.isnan(p[2])


def test_upgrade_old_labels():
    old = pd.DataFrame(dict(file=["112026_4_x_sham_1", "112026_5_noNC_1"], condition=["Before AMF", "Before AMF"],
                            nc_conc_nM=[0.5, np.nan]))
    new = GS.upgrade_labels(old)
    assert list(new["condition"]) == ["After sham", "Before AMF"]
    assert new["nc_conc_nM"].iloc[1] == 0.0


def test_run_tables():
    pf = _pf()
    ch, st, cp = GS.run_tables(pf, metrics=[("order_S", "alignment S")])
    assert len(ch) == 8 and "order_S_change_pct_minus_sham" in ch
    assert set(st["arm"]) == {GS.TREATED, GS.SHAM, GS.AMF_ONLY}
    assert "field effect (NC + AMF vs sham)" in set(cp["comparison"])


def test_planner_excludes_and_subtracts_shams():
    exp, notes = PL.build_experiments(_pf(), "order_S", "diff", True, "subtract")
    assert not exp["arm"].isin(GS.CONTROL_ARMS).any()
    assert len(exp) == 5
    assert exp.set_index("file").loc["112026_1_a", "y"] == pytest.approx(0.15 - 0.01)
    raw, _ = PL.build_experiments(_pf(), "order_S", "diff", True, "none")
    assert raw.set_index("file").loc["112026_1_a", "y"] == pytest.approx(0.15)
