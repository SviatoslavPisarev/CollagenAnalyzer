"""Strongest correlations in the tile tables (used by the Patterns page).

Four levels, each with its own replicate so that tiles of one image are not counted as
independent samples:

  settings   AMF/NC settings vs the change after AMF (after − before, one value per sample/gel):
             what drives remodeling. Changes of the tile-type shares are included once tile
             types have been found.
  files      metric vs metric, one value per file (mean of its tiles).
  tiles      metric vs metric inside each image (tile by tile); summarised as the median ρ over
             images, tested with a sign test across images.
  depth      metric vs depth z inside each file (image means of a z-stack); median ρ over files,
             sign test across files.

Spearman's ρ (rank correlation: any monotonic relation, robust to outliers) is used throughout.
p-values are corrected for the number of pairs tested (Benjamini–Hochberg q). Ranking is by |ρ|;
with few samples a large |ρ| can still be chance, so q and n are shown next to it.
"""
import numpy as np
import pandas as pd

from ml import patterns as PT
from ml import planner as PL

LEVELS = [("settings", "Settings vs change after AMF (per sample)"),
          ("files", "Metric vs metric (per file)"),
          ("tiles", "Metric vs metric (inside images)"),
          ("depth", "Metric vs depth z (per z-stack)")]
# pairs that move together because of how they are measured, not because of the biology
LINKED = {frozenset(p) for p in [("area_frac_pct", "length_density_um_per_um2"),
                                  ("area_frac_pct", "pore_lt_mean_um"),
                                  ("length_density_um_per_um2", "pore_lt_mean_um"),
                                  ("area_frac_pct", "fiber_lt_mean_um"),
                                  ("order_S", "coherency"),
                                  ("mean_reflectance", "area_frac_pct")]}


def label(col):
    """Readable name of a metric, setting or type-share column."""
    if col in PT.FEAT:
        return PT.FEAT[col][1]
    if col.startswith("d_") and col[2:] in PT.FEAT:
        return "Δ " + PT.FEAT[col[2:]][1]
    if col.startswith("cluster_") and col.endswith("_change_pp"):
        return f"Δ share of type {col.split('_')[1]} (pp)"
    if col in PL.VAR:
        return PL.VAR[col][2] + (f" ({PL.VAR[col][3]})" if PL.VAR[col][3] else "")
    if col == "z_um":
        return "depth z (µm)"
    return col


def strength(r):
    a = abs(r)
    return "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else "weak"


def bh(p):
    """Benjamini–Hochberg q-values (NaN stays NaN)."""
    p = np.asarray(p, float)
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    if not ok.any():
        return q
    pv = p[ok]
    o = np.argsort(pv)
    m = len(pv)
    adj = np.minimum.accumulate((pv[o] * m / np.arange(1, m + 1))[::-1])[::-1]
    out = np.empty(m)
    out[o] = np.minimum(adj, 1)
    q[ok] = out
    return q


def _spearman(x, y):
    from scipy.stats import spearmanr
    r, p = spearmanr(x, y)
    return float(r), float(p)


def _sign_p(k, n):
    from scipy.stats import binomtest
    return float(binomtest(int(k), int(n), 0.5).pvalue) if n else np.nan


def _finish(rows, hide_linked):
    t = pd.DataFrame(rows, columns=["x", "y", "rho", "n", "p", "linked", "unit_of_n", "extra"])
    if t.empty:
        return t
    if hide_linked:
        t = t[~t["linked"]]
    t = t[np.isfinite(t["rho"])].copy()
    t["q"] = bh(t["p"])
    t["strength"] = [strength(r) for r in t["rho"]]
    t["x_label"] = [label(c) for c in t["x"]]
    t["y_label"] = [label(c) for c in t["y"]]
    t = t.reindex(t["rho"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return t


# ---------------------------------------------------------------- tables
def settings_of(df):
    """The AMF/NC settings as shown values (columns named by the planner's keys)."""
    out = pd.DataFrame(index=df.index)
    for key, col, *_ in PL.VARIABLES:
        if col in df:
            out[key] = PL._value(key, df[col])
    return out


def file_table(tiles, features):
    """One row per file: the mean of each metric over its tiles, with the file's labels and settings."""
    g = tiles.groupby("file", sort=False)
    info = g[[c for c in ("date", "sample", "condition", "arm", "_tiles_csv") if c in tiles]].first()
    means = g[features].agg(lambda s: pd.to_numeric(s, errors="coerce").mean())
    sets = settings_of(g.first())
    out = info.join(means).join(sets)
    out["n_tiles"] = g.size()
    return out.reset_index()


def sample_changes(ft, features, change=None):
    """One row per sample with a Before- and an After-AMF file: after − before mean of each metric (d_<metric>),
    the sample's settings (from its After file), and the type-share changes if `change` (patterns.paired_change)
    is given."""
    src = ft["_tiles_csv"] if "_tiles_csv" in ft else pd.Series("", index=ft.index)
    key = ft["date"].astype(str) + "|" + ft["sample"].astype(str) + "|" + src.astype(str)
    setcols = [k for k in PL.VAR if k in ft]
    rows = []
    for _, g in ft.groupby(key, sort=False):
        b = g[g["condition"].astype(str).eq("Before AMF")]
        a = g[g["condition"].astype(str).str.startswith("After")]
        if b.empty or a.empty:
            continue
        r = dict(sample=g["sample"].iloc[0], date=g["date"].iloc[0],
                 arm=str(a["arm"].iloc[0]) if "arm" in a else "")
        for f in features:
            r["d_" + f] = float(a[f].mean() - b[f].mean())
        for k in setcols:
            v = a[k].dropna()
            r[k] = float(v.iloc[0]) if len(v) else np.nan
        rows.append(r)
    ch = pd.DataFrame(rows)
    if change is not None and len(change) and len(ch):
        cc = [c for c in change.columns if c.endswith("_change_pp")]
        ch = ch.merge(change[["sample", "date"] + cc].astype({"sample": str, "date": str}),
                      on=["sample", "date"], how="left")
    return ch


# ---------------------------------------------------------------- levels
def by_settings(tiles, features, change=None, min_n=4, hide_linked=True):
    ft = file_table(tiles, features)
    ch = sample_changes(ft, features, change)
    if ch.empty:
        return _finish([], hide_linked), ch
    xs = [k for k in PL.VAR if k in ch and ch[k].nunique(dropna=True) >= 2]
    ys = ["d_" + f for f in features] + [c for c in ch.columns if c.endswith("_change_pp")]
    rows = []
    for x in xs:
        for y in ys:
            d = ch[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
            if len(d) < min_n or d[x].nunique() < 2 or d[y].nunique() < 2:
                continue
            r, p = _spearman(d[x], d[y])
            rows.append((x, y, r, len(d), p, False, "samples", f"{d[x].nunique()} levels"))
    return _finish(rows, hide_linked), ch


def between_files(tiles, features, min_n=5, hide_linked=True):
    ft = file_table(tiles, features)
    rows = []
    for i, x in enumerate(features):
        for y in features[i + 1:]:
            d = ft[[x, y]].dropna()
            if len(d) < min_n or d[x].nunique() < 3 or d[y].nunique() < 3:
                continue
            r, p = _spearman(d[x], d[y])
            rows.append((x, y, r, len(d), p, frozenset((x, y)) in LINKED, "files", ""))
    return _finish(rows, hide_linked), ft


def inside_images(tiles, features, min_tiles=15, hide_linked=True):
    """Per image Spearman matrix of the tile metrics; per pair: median ρ, images, share with the median's sign."""
    keys = [c for c in ("file", "t_index", "z_index") if c in tiles]
    per = {}                                   # (x, y) -> list of ρ over images
    for _, g in tiles.groupby(keys, sort=False):
        X = g[features].apply(pd.to_numeric, errors="coerce").dropna()
        if len(X) < min_tiles:
            continue
        R = X.rank().to_numpy(float)
        sd = R.std(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            C = np.corrcoef(R, rowvar=False) if len(features) > 1 else np.ones((1, 1))
        for i, x in enumerate(features):
            for j in range(i + 1, len(features)):
                if sd[i] > 0 and sd[j] > 0 and np.isfinite(C[i, j]):
                    per.setdefault((x, features[j]), []).append(C[i, j])
    rows = []
    for (x, y), v in per.items():
        v = np.asarray(v)
        med = float(np.median(v))
        k = int((np.sign(v) == np.sign(med)).sum()) if med else 0
        n = len(v)
        rows.append((x, y, med, n, _sign_p(k, n) if med else 1.0, frozenset((x, y)) in LINKED, "images",
                     f"same sign in {k} of {n} images"))
    return _finish(rows, hide_linked), per


def with_depth(tiles, features, min_z=4, hide_linked=True):
    """Per file (z-stack): ρ of each metric's image mean with z; median over files and sign test."""
    if "z_um" not in tiles:
        return _finish([], hide_linked), {}
    t = tiles.copy()
    t["z_um"] = pd.to_numeric(t["z_um"], errors="coerce")
    img = t.groupby(["file", "z_um"])[features].agg(lambda s: pd.to_numeric(s, errors="coerce").mean())
    img = img.reset_index()
    per = {}                                   # metric -> list of (file, ρ, condition)
    cond = t.groupby("file")["condition"].first() if "condition" in t else pd.Series(dtype=str)
    for fname, g in img.groupby("file", sort=False):
        if g["z_um"].nunique() < min_z:
            continue
        for f in features:
            d = g[["z_um", f]].dropna()
            if len(d) >= min_z and d[f].nunique() > 1:
                per.setdefault(f, []).append((fname, _spearman(d["z_um"], d[f])[0], str(cond.get(fname, ""))))
    rows = []
    for f, lst in per.items():
        v = np.array([r for _, r, _ in lst if np.isfinite(r)])
        if not len(v):
            continue
        med = float(np.median(v))
        k = int((np.sign(v) == np.sign(med)).sum()) if med else 0
        rows.append(("z_um", f, med, len(v), _sign_p(k, len(v)) if med else 1.0, False, "z-stacks",
                     f"same sign in {k} of {len(v)} stacks"))
    return _finish(rows, hide_linked), (img, per)


def run(level, tiles, features, change=None, hide_linked=True):
    """Ranked table for one level, plus the data behind it (for plotting)."""
    if level == "settings":
        return by_settings(tiles, features, change, hide_linked=hide_linked)
    if level == "files":
        return between_files(tiles, features, hide_linked=hide_linked)
    if level == "tiles":
        return inside_images(tiles, features, hide_linked=hide_linked)
    if level == "depth":
        return with_depth(tiles, features, hide_linked=hide_linked)
    raise ValueError(level)


def headline(t, top=3, q_max=0.1):
    """Plain-language list of the strongest correlations (list of strings)."""
    if t is None or t.empty:
        return []
    out = []
    for _, r in t.head(top).iterrows():
        sure = ("unlikely to be chance" if r["q"] < 0.05 else "possibly real" if r["q"] < q_max
                else "could be chance")
        direction = "goes up" if r["rho"] > 0 else "goes down"
        out.append(f"{r['y_label']} {direction} with {r['x_label']}: ρ = {r['rho']:+.2f} ({r['strength']}, "
                   f"n = {int(r['n'])} {r['unit_of_n']}, q = {r['q']:.2g} — {sure})")
    return out
