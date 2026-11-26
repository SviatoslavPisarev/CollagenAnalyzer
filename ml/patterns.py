"""Patterns: unsupervised clustering of the per-tile table (mmddyy_tiles.csv).

Each tile (default 13 x 13 µm of one analysed image) is described by its structure metrics. The
metrics are standardised (z-scores; size-like metrics log-transformed first), a Gaussian mixture
groups the tiles into k structure types without being told which file is before or after AMF, and
the share of each type is then compared between conditions. A shift in the shares after AMF can
show local remodeling that a whole-image average hides.

Tiles of one image are not independent samples: compare conditions with one value per file or per
sample (as done here), not with tile counts.
"""
import numpy as np
import pandas as pd

# column, short name, words used for naming clusters (low, high), log-transform
FEATURES = [
    ("area_frac_pct", "fiber density", ("sparse", "dense"), False),
    ("length_density_um_per_um2", "fiber length density", ("few fibers", "many fibers"), False),
    ("pore_lt_mean_um", "pore size", ("small pores", "large pores"), True),
    ("fiber_lt_mean_um", "fiber thickness", ("thin fibers", "thick fibers"), True),
    ("order_S", "alignment S", ("isotropic", "aligned"), False),
    ("coherency", "coherency", ("low coherency", "high coherency"), False),
    ("mean_reflectance", "reflectance", ("dim", "bright"), True),
    ("reflectance_cv", "reflectance texture (CV)", ("smooth", "textured"), False),
]
FEAT = {f[0]: f for f in FEATURES}
DEFAULT_FEATURES = ["area_frac_pct", "length_density_um_per_um2", "pore_lt_mean_um", "fiber_lt_mean_um", "order_S",
                    "coherency"]
KEY = ["file", "t_index", "z_index", "tile_row", "tile_col"]


def load_tiles(paths):
    """Concatenate tile tables; the same tile loaded twice is kept once (the last)."""
    parts = []
    for p in paths:
        t = pd.read_csv(p, low_memory=False)
        t["_tiles_csv"] = p
        parts.append(t)
    if not parts:
        return pd.DataFrame()
    t = pd.concat(parts, ignore_index=True, sort=False)
    t["file"] = t["file"].astype(str)
    t["sample"] = t["sample"].astype(str)
    t["date"] = t["date"].astype(str)
    return t.drop_duplicates(subset=[c for c in KEY if c in t], keep="last").reset_index(drop=True)


def select(tiles, exclude_flagged_tiles=True, exclude_qc_files=True):
    keep = pd.Series(True, index=tiles.index)
    notes = []
    if exclude_flagged_tiles and "tile_flag" in tiles:
        bad = tiles["tile_flag"].fillna("").astype(str).str.len() > 0
        keep &= ~bad
        if bad.any():
            notes.append(f"{int(bad.sum())} dark/blurred tile(s) left out")
    if exclude_qc_files and "qc_status" in tiles:
        bad = tiles["qc_status"].astype(str).eq("check")
        if (bad & keep).any():
            notes.append(f"{tiles.loc[bad, 'file'].nunique()} file(s) marked ‘check’ by the quality check left out")
        keep &= ~bad
    return tiles[keep], notes


def matrix(tiles, features, log_sizes=True):
    """Standardised feature matrix (rows with a missing value dropped). Returns (Z, index, mean, sd)."""
    X = tiles[features].apply(pd.to_numeric, errors="coerce")
    for f in features:
        if log_sizes and FEAT[f][3]:
            X[f] = np.log(X[f].where(X[f] > 0))
    X = X.replace([np.inf, -np.inf], np.nan).dropna()
    mu, sd = X.mean(), X.std(ddof=0).replace(0, 1)
    return ((X - mu) / sd).to_numpy(float), X.index, mu, sd


def choose_k(Z, ks=range(2, 9), max_fit=20000, seed=0):
    """BIC of a Gaussian mixture for each k (lower = better). Returns {k: bic}."""
    from sklearn.mixture import GaussianMixture
    rng = np.random.default_rng(seed)
    S = Z[rng.choice(len(Z), min(len(Z), max_fit), replace=False)]
    out = {}
    for k in ks:
        if k >= len(S):
            break
        g = GaussianMixture(k, covariance_type="full", n_init=3, random_state=seed).fit(S)
        out[k] = float(g.bic(S))
    return out


def cluster(Z, k, method="gmm", max_fit=20000, seed=0):
    """Cluster labels 0..k-1 and, for the mixture, each tile's probability of its cluster."""
    rng = np.random.default_rng(seed)
    S = Z[rng.choice(len(Z), min(len(Z), max_fit), replace=False)]
    if method == "kmeans":
        from sklearn.cluster import KMeans
        m = KMeans(k, n_init=10, random_state=seed).fit(S)
        lab = m.predict(Z)
        return lab, np.ones(len(Z))
    from sklearn.mixture import GaussianMixture
    m = GaussianMixture(k, covariance_type="full", n_init=3, random_state=seed).fit(S)
    p = m.predict_proba(Z)
    return p.argmax(1), p.max(1)


def relabel(lab, Z, features):
    """Number the clusters by increasing alignment (or first feature) so the numbering is stable."""
    j = features.index("order_S") if "order_S" in features else 0
    k = lab.max() + 1
    order = np.argsort([Z[lab == c, j].mean() if (lab == c).any() else np.inf for c in range(k)])
    new = np.empty(k, int)
    new[order] = np.arange(k)
    return new[lab]


def profiles(Z, lab, raw, features):
    """Per cluster: n, share, mean z-score and mean raw value of each feature, and a short name."""
    rows = []
    for c in range(lab.max() + 1):
        m = lab == c
        z = Z[m].mean(0)
        words = [FEAT[f][2][int(v > 0)] for v, f in sorted(zip(z, features), key=lambda t: -abs(t[0]))
                 if abs(v) >= 0.5][:3]
        r = dict(cluster=c + 1, name=", ".join(words) or "average", n_tiles=int(m.sum()),
                 share_pct=100 * m.mean())
        for f, v in zip(features, z):
            r[f"z_{f}"] = float(v)
        for f in features:
            r[f] = float(raw.loc[m, f].mean())
        rows.append(r)
    return pd.DataFrame(rows)


def shares(tiles, lab_col="cluster"):
    """Share (%) of each cluster per file, with the file's labels. One row per file."""
    k = int(tiles[lab_col].max())
    g = tiles.groupby("file", sort=False)
    sh = pd.crosstab(tiles["file"], tiles[lab_col], normalize="index") * 100
    sh = sh.reindex(columns=range(1, k + 1), fill_value=0)
    sh.columns = [f"cluster_{c}_pct" for c in sh.columns]
    info_cols = [c for c in ("date", "sample", "condition", "nc_conc_nM", "amf_freq_hz", "amf_time_s", "_tiles_csv")
                 if c in tiles]
    info = g[info_cols].first()
    info["n_tiles"] = g.size()
    return info.join(sh).reset_index()


def paired_change(sh):
    """Per sample (and source): mean share after AMF - mean share before (percentage points)."""
    cols = [c for c in sh.columns if c.startswith("cluster_")]
    src = sh["_tiles_csv"] if "_tiles_csv" in sh else ""
    key = sh["date"].astype(str) + "|" + sh["sample"].astype(str) + "|" + pd.Series(src, index=sh.index).astype(str)
    rows = []
    for _, g in sh.groupby(key, sort=False):
        b = g[g["condition"].astype(str).eq("Before AMF")]
        a = g[g["condition"].astype(str).str.startswith("After")]
        if b.empty or a.empty:
            continue
        r = dict(sample=g["sample"].iloc[0], date=g["date"].iloc[0], n_before=len(b), n_after=len(a))
        for c in cols:
            r[c.replace("_pct", "_change_pp")] = float(a[c].mean() - b[c].mean())
        rows.append(r)
    return pd.DataFrame(rows)


def embed(Z, method="pca", max_points=4000, seed=0):
    """2-D view of the tiles. Returns (indices into Z that were embedded, coordinates, (x label, y label))."""
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(Z), min(len(Z), max_points), replace=False))
    if method == "tsne":
        from sklearn.manifold import TSNE
        Y = TSNE(2, perplexity=min(30, max(5, len(idx) // 50)), init="pca", random_state=seed).fit_transform(Z[idx])
        return idx, Y, ("t-SNE 1", "t-SNE 2 (distances only meaningful locally)")
    from sklearn.decomposition import PCA
    p = PCA(2, random_state=seed).fit(Z)
    ev = p.explained_variance_ratio_
    return idx, p.transform(Z[idx]), (f"PC1 ({ev[0]:.0%} of variance)", f"PC2 ({ev[1]:.0%})")
