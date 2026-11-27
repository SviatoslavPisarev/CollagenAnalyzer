"""Controls and statistics with the gel as the replicate.

Experimental groups ("arms"), read from the file names of a sample's after file(s):

  NC + AMF          After-AMF file (name contains AMF), nanocages present (or not stated)
  AMF only (no NC)  After-AMF file of a gel without nanocages (_noNC_ or _NC_0nM_)
  sham (NC, no field)  After file marked _sham_: imaged again after the same handling, no field
  sham, no NC       sham file of a gel without nanocages

Every After file is paired with the Before file(s) of its own sample (same mmddyy_##, same results
folder): change = after - before (or % of before), using the per-file means over all analysed images.

Control correction: the mean change of the matching sham gels is subtracted from each treated
change, so drift from drying, handling and re-imaging is not counted as remodeling. Matching control
arm: NC + AMF -> sham (NC, no field); AMF only -> sham, no NC (else sham with NCs). Shams of the same
date are used; if there are none, all shams in the data (marked in control_match).

Statistics treat each gel as one replicate (images of one stack are not independent): several After
files of one gel in the same arm are averaged first. Per arm: mean, SD, 95 % CI, paired test of the
change against 0 (one-sample t-test = paired t-test of before vs after, and Wilcoxon signed-rank).
Between arms (treated vs its sham, NC + AMF vs AMF only): Welch's t-test, Mann-Whitney U, Hedges' g
with an approximate 95 % CI; p-values are Holm-adjusted within each metric.
For the p-value of the field effect use the comparison NC + AMF vs sham, not the control-corrected
values against 0 (the correction does not carry the uncertainty of the control mean).
"""
import math
import re
import warnings

import numpy as np
import pandas as pd

# file-name tags (also used by collagen_core.py)
SHAM_RE = re.compile(r"(?:(?<=[_\-])|^)sham(?=$|[_\-.\d])", re.I)
NONC_RE = re.compile(r"(?:(?<=[_\-])|^)no[_\-]?NCs?(?=$|[_\-.])", re.I)
_OTHER_RE = re.compile(r"(?:(?<=_)|^)other(?:[-=:]([^_]+)|_([^_]+))", re.I)

TREATED, AMF_ONLY, SHAM, SHAM_NONC = "NC + AMF", "AMF only (no NC)", "sham (NC, no field)", "sham, no NC"
ARMS = [TREATED, AMF_ONLY, SHAM, SHAM_NONC]
CONTROL_ARMS = {SHAM, SHAM_NONC}
CONTROL_FOR = {TREATED: [SHAM], AMF_ONLY: [SHAM_NONC, SHAM]}
COMPARISONS = [(TREATED, SHAM, "field effect (NC + AMF vs sham)"),
               (AMF_ONLY, SHAM_NONC, "field without NCs (AMF only vs sham, no NC)"),
               (TREATED, AMF_ONLY, "NC effect under AMF (NC + AMF vs AMF only)"),
               (SHAM, SHAM_NONC, "NC effect without field (sham vs sham, no NC)")]
ARM_COLORS = {TREATED: "#d9622b", AMF_ONLY: "#8e44ad", SHAM: "#7f8c8d", SHAM_NONC: "#b0b7bd"}
# metrics summarised in the 'changes' and 'statistics' sheets of a run
KEY_METRICS = [("area_frac_pct", "fiber area fraction (%)"),
               ("length_density_um_per_um2", "fiber length density (µm/µm²)"),
               ("pore_lt_mean_um", "pore size (µm)"),
               ("fiber_lt_mean_um", "fiber size (µm)"),
               ("fiber_len_mean_um", "fiber segment length (µm)"),
               ("order_S", "alignment S"),
               ("coherency", "coherency"),
               ("tile_cv", "tile CV")]


def is_after(condition):
    return str(condition).startswith("After")


def is_sham(condition):
    return str(condition).lower().startswith("after sham")


def file_arm(condition, nc_nM):
    """Arm of one After file (None for a Before file). nc_nM = 0 means no nanocages; unknown counts as NCs."""
    if not is_after(condition):
        return None
    try:
        no_nc = float(nc_nM) == 0
    except (TypeError, ValueError):
        no_nc = False
    if is_sham(condition):
        return SHAM_NONC if no_nc else SHAM
    return AMF_ONLY if no_nc else TREATED


def upgrade_labels(pf):
    """Spreadsheets written before sham/noNC support: a file named ..._sham_... was stored as
    'Before AMF', and _noNC_ left the NC concentration empty. Fix both from the file names."""
    if "file" not in pf or "condition" not in pf:
        return pf
    pf = pf.copy()
    core = pf["file"].astype(str).map(lambda n: _OTHER_RE.sub("", n))
    sham = core.map(lambda n: bool(SHAM_RE.search(n)))
    pf.loc[sham & pf["condition"].astype(str).eq("Before AMF"), "condition"] = "After sham"
    if "nc_conc_nM" in pf:
        nonc = core.map(lambda n: bool(NONC_RE.search(n)))
        pf["nc_conc_nM"] = pd.to_numeric(pf["nc_conc_nM"], errors="coerce")
        pf.loc[nonc & pf["nc_conc_nM"].isna(), "nc_conc_nM"] = 0.0
    return pf


def _gel_key(df):
    src = df["_src"].astype(str) if "_src" in df else pd.Series("", index=df.index)
    return df["date"].astype(str) + "|" + df["sample"].astype(str) + "|" + src


def assign_arms(pf):
    """Add/refresh an 'arm' column: After files get their own arm, Before files the arm(s) of their
    sample's After files (joined with ' / '), samples without After files ''."""
    pf = pf.reset_index(drop=True)
    nc = pf["nc_conc_nM"] if "nc_conc_nM" in pf else pd.Series(np.nan, index=pf.index)
    arm = pd.Series([file_arm(c, n) for c, n in zip(pf["condition"], nc)], index=pf.index, dtype=object)
    key = _gel_key(pf)
    for k, idx in key.groupby(key).groups.items():
        arms = [a for a in arm[idx] if a]
        joined = " / ".join(dict.fromkeys(arms))
        for i in idx:
            if not arm[i]:
                arm[i] = joined
    pf["arm"] = arm.fillna("")
    return pf


def pair_changes(pf, metric, change="pct", exclude_qc=True, value_col=None):
    """One row per After file paired with its sample's Before file(s).
    pf: per-file rows (columns date, sample, file, condition, [nc_conc_nM, qc_status, _src], value column).
    value_col defaults to metric + '_mean' (the 'per file' sheet). change: 'pct' or 'diff'.
    Returns (DataFrame, notes). Columns: file, date, sample, _src, arm, before, after, y, before_files,
    plus every other column of the After file's row."""
    col = value_col or metric + "_mean"
    notes = []
    if col not in pf:
        return pd.DataFrame(), [f"metric {metric} not in the data"]
    pf = assign_arms(upgrade_labels(pf))
    if "_src" not in pf:
        pf["_src"] = ""
    flagged = pf.get("qc_status", pd.Series("", index=pf.index)).astype(str).eq("check")
    rows, n_qc, n_nobefore = [], 0, 0
    for _, g in pf.groupby(_gel_key(pf), sort=False):
        before = g[~g["condition"].map(is_after)]
        after = g[g["condition"].map(is_after)]
        if exclude_qc:
            n_qc += int(flagged[before.index].sum() + flagged[after.index].sum())
            before, after = before[~flagged[before.index]], after[~flagged[after.index]]
        if after.empty:
            continue
        b = pd.to_numeric(before[col], errors="coerce").dropna()
        if b.empty:
            n_nobefore += len(after)
            continue
        b0 = float(b.mean())
        for _, r in after.iterrows():
            a = pd.to_numeric(pd.Series([r[col]]), errors="coerce").iloc[0]
            if not np.isfinite(a):
                continue
            y = a - b0 if change == "diff" else (100 * (a - b0) / b0 if b0 else np.nan)
            row = r.to_dict()
            row.update(before=b0, after=float(a), y=float(y), before_files=", ".join(before["file"].astype(str)))
            rows.append(row)
    if n_qc and exclude_qc:
        notes.append(f"{n_qc} file(s) left out because the quality check marked them ‘check’")
    if n_nobefore:
        notes.append(f"{n_nobefore} After file(s) without a Before file of the same sample left out")
    return pd.DataFrame(rows), notes


def control_correct(exp, match="date"):
    """Add control_arm, control_n, control_mean, control_match and y_corrected (= y - control_mean).
    Control rows themselves get y_corrected = NaN. match: 'date' (same date, else all) or 'all'."""
    exp = exp.copy()
    for c in ("control_arm", "control_match"):
        exp[c] = ""
    exp["control_n"] = 0
    exp["control_mean"] = np.nan
    exp["y_corrected"] = np.nan
    if exp.empty:
        return exp
    gels = per_gel(exp, "y")
    for i, r in exp.iterrows():
        cands = CONTROL_FOR.get(r["arm"])
        if not cands:
            continue
        for carm in cands:
            pool = gels[gels["arm"] == carm]
            if pool.empty:
                continue
            same = pool[pool["date"].astype(str) == str(r["date"])]
            use, how = (same, "same date") if (match == "date" and len(same)) else (pool, "all dates")
            exp.at[i, "control_arm"] = carm
            exp.at[i, "control_match"] = how
            exp.at[i, "control_n"] = int(len(use))
            exp.at[i, "control_mean"] = float(use["value"].mean())
            exp.at[i, "y_corrected"] = float(r["y"] - use["value"].mean())
            break
        else:
            exp.at[i, "control_match"] = "no sham control in the data"
    return exp


def per_gel(exp, value="y", by=()):
    """Average the value per gel (date, sample, results folder) and arm (and the extra columns by)."""
    if exp.empty:
        return pd.DataFrame(columns=["date", "sample", "_src", "arm", *by, "value", "n_files"])
    e = exp.copy()
    if "_src" not in e:
        e["_src"] = ""
    keys = ["date", "sample", "_src", "arm", *by]
    for k in keys:
        e[k] = e[k].astype(str) if k in ("date", "sample", "_src", "arm") else e[k]
    g = e.groupby(keys, sort=False, dropna=False)[value]
    out = g.mean().rename("value").reset_index()
    out["n_files"] = g.count().to_numpy()
    return out[np.isfinite(pd.to_numeric(out["value"], errors="coerce"))].reset_index(drop=True)


def _tcrit(df):
    from scipy import stats
    return float(stats.t.ppf(0.975, df)) if df > 0 else np.nan


def summarize(v):
    """n, mean, SD, SEM, 95 % CI, paired tests against 0 (t-test, Wilcoxon), effect size dz = mean / SD."""
    with warnings.catch_warnings():            # near-identical values: scipy warns, the result is still given
        warnings.simplefilter("ignore", RuntimeWarning)
        return _summarize(v)


def _summarize(v):
    from scipy import stats
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    n = v.size
    out = dict(n_gels=n, mean=float(v.mean()) if n else np.nan, sd=np.nan, sem=np.nan, ci95_low=np.nan,
               ci95_high=np.nan, p_ttest_vs0=np.nan, p_wilcoxon_vs0=np.nan, dz=np.nan)
    if n >= 2:
        sd = float(v.std(ddof=1))
        sem = sd / math.sqrt(n)
        h = _tcrit(n - 1) * sem
        out.update(sd=sd, sem=sem, ci95_low=out["mean"] - h, ci95_high=out["mean"] + h,
                   dz=out["mean"] / sd if sd > 0 else np.nan)
        if sd > 0:
            out["p_ttest_vs0"] = float(stats.ttest_1samp(v, 0.0).pvalue)
        if np.any(v != 0):
            try:
                out["p_wilcoxon_vs0"] = float(stats.wilcoxon(v).pvalue)
            except ValueError:
                pass
    return out


def compare(a, b):
    """Group a vs group b (independent gels): difference of means with Welch 95 % CI, Welch's t-test,
    Mann-Whitney U, Hedges' g with an approximate 95 % CI."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _compare(a, b)


def _compare(a, b):
    from scipy import stats
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    b = np.asarray(b, float); b = b[np.isfinite(b)]
    na, nb = a.size, b.size
    out = dict(n_a=na, n_b=nb, mean_a=float(a.mean()) if na else np.nan, mean_b=float(b.mean()) if nb else np.nan,
               difference=np.nan, diff_ci95_low=np.nan, diff_ci95_high=np.nan, p_welch=np.nan,
               p_mannwhitney=np.nan, hedges_g=np.nan, g_ci95_low=np.nan, g_ci95_high=np.nan)
    if not (na and nb):
        return out
    out["difference"] = out["mean_a"] - out["mean_b"]
    if na >= 2 and nb >= 2:
        va, vb = a.var(ddof=1), b.var(ddof=1)
        se = math.sqrt(va / na + vb / nb)
        if se > 0:
            df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
            h = _tcrit(df) * se
            out.update(diff_ci95_low=out["difference"] - h, diff_ci95_high=out["difference"] + h,
                       p_welch=float(stats.ttest_ind(a, b, equal_var=False).pvalue))
        sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
        if sp > 0:
            d = out["difference"] / sp
            J = 1 - 3 / (4 * (na + nb) - 9)
            g = J * d
            se_g = math.sqrt((na + nb) / (na * nb) + g * g / (2 * (na + nb)))
            out.update(hedges_g=g, g_ci95_low=g - 1.96 * se_g, g_ci95_high=g + 1.96 * se_g)
    try:
        out["p_mannwhitney"] = float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
    except ValueError:
        pass
    return out


def holm(p):
    """Holm-Bonferroni adjusted p-values (NaN stays NaN)."""
    p = np.asarray(p, float)
    out = np.full(p.shape, np.nan)
    ok = np.where(np.isfinite(p))[0]
    if not ok.size:
        return out
    order = ok[np.argsort(p[ok])]
    m = ok.size
    run = 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (m - k) * p[i]))
        out[i] = run
    return out


def arm_statistics(exp, value="y", by=()):
    """Per-arm summaries and between-arm comparisons, gels as replicates.
    by: extra grouping columns (e.g. ['amf_freq_hz']); comparisons are made within each group, and a
    sham arm (which has no AMF settings) is compared with every group of its treated arm.
    Returns (summary DataFrame, comparisons DataFrame)."""
    gels = per_gel(exp, value, by)
    srows, crows = [], []
    if gels.empty:
        return pd.DataFrame(), pd.DataFrame()
    groups = [()] if not by else list(dict.fromkeys(tuple(r) for r in gels[list(by)].itertuples(index=False)))
    def sel(arm, grp):
        g = gels[gels["arm"] == arm]
        if by and arm not in CONTROL_ARMS:
            for c, v in zip(by, grp):
                g = g[(g[c] == v) | (g[c].isna() & pd.isna(v))]
        return g
    seen = set()
    for grp in groups:
        gl = dict(zip(by, grp))
        for arm in ARMS:
            g = sel(arm, grp)
            if g.empty or (arm in CONTROL_ARMS and arm in seen):
                continue
            if arm in CONTROL_ARMS:
                seen.add(arm)
            srows.append(dict(**({} if arm in CONTROL_ARMS else gl), arm=arm, **summarize(g["value"]),
                              gels=", ".join(g["sample"].astype(str))))
        for a_arm, b_arm, what in COMPARISONS:
            ga, gb = sel(a_arm, grp), sel(b_arm, grp)
            if ga.empty or gb.empty:
                continue
            crows.append(dict(**gl, comparison=what, group_a=a_arm, group_b=b_arm,
                              **compare(ga["value"], gb["value"])))
    summ, comp = pd.DataFrame(srows), pd.DataFrame(crows)
    if len(comp):
        for c in ("p_welch", "p_mannwhitney"):
            comp[c + "_holm"] = holm(comp[c].to_numpy(float))
    return summ, comp


def run_tables(pf, metrics=KEY_METRICS, change="pct", exclude_qc=True):
    """The 'changes', 'statistics' and 'arm comparisons' sheets of a run's workbook.
    changes: one row per After file with before / after / % change / control-corrected change of
    every key metric. statistics / comparisons: long format, one block per metric."""
    base_cols = ["file", "date", "sample", "arm", "before_files"]
    changes, stats_rows, comp_rows = None, [], []
    for m, label in metrics:
        exp, _ = pair_changes(pf, m, change=change, exclude_qc=exclude_qc)
        if exp.empty:
            continue
        exp = control_correct(exp)
        keep = exp[[c for c in base_cols if c in exp]].copy()
        unit = "change_pct" if change == "pct" else "change"
        keep[f"{m}_before"] = exp["before"]
        keep[f"{m}_after"] = exp["after"]
        keep[f"{m}_{unit}"] = exp["y"]
        keep[f"{m}_{unit}_minus_sham"] = exp["y_corrected"]
        if changes is None:
            changes = keep
            changes["control_arm"], changes["control_match"], changes["control_n"] = \
                exp["control_arm"], exp["control_match"], exp["control_n"]
        else:
            changes = changes.merge(keep.drop(columns=[c for c in base_cols if c != "file" and c in keep]),
                                    on="file", how="outer")
        s, c = arm_statistics(exp, "y")
        for df_, out in ((s, stats_rows), (c, comp_rows)):
            if len(df_):
                df_ = df_.copy()
                df_.insert(0, "change", "% of before" if change == "pct" else "after − before")
                df_.insert(0, "metric", label)
                out.append(df_)
    if changes is None:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    st = pd.concat(stats_rows, ignore_index=True) if stats_rows else pd.DataFrame()
    cp = pd.concat(comp_rows, ignore_index=True) if comp_rows else pd.DataFrame()
    return changes, st, cp


def fmt_p(p):
    if p is None or not np.isfinite(p):
        return "n/a"
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}" if p < 0.1 else f"p = {p:.2f}"


def stars(p):
    if p is None or not np.isfinite(p):
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
