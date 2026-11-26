"""Quality check (QC) of collagen reflectance images.

Three levels, all written to the result spreadsheets:

  per image  qc_sharpness (focus), qc_edge_center_ratio (brightness of the outer band vs the centre;
             drying usually changes the edges first), qc_saturated_pct, qc_valid_pct (analysed area),
             qc_bad_tiles_pct (tiles flagged dark or blurred)
  per tile   the structure metrics of every tile (for the per-tile table) plus a tile flag
             ("dark" / "blurred", relative to the other tiles of the same image)
  per file   features summarising the images of a file, rule-based flags and, when a trained model is
             given, the model's probability that the file has a problem ("quality" sheet)

The rules use fixed limits (RULES below). They are first guesses meant to catch obvious problems;
check them against your own data and adjust. The trained model learns from files you marked as
problems (e.g. "_other-dried-edges_" in the file name) and needs scikit-learn.
"""
import math
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import ndimage as ndi

EDGE_BAND, CENTRE_BAND = 0.7, 0.5     # outer band: r > 0.7, centre: r < 0.5 (r = 1 at the image border)
TILE_DARK, TILE_BLUR = 0.4, 0.4       # tile flagged if below this fraction of the image's median tile
MODEL_KIND = "collagen-analyzer-qc"
MODEL_VERSION = 1

# --------------------------------------------------------------------------------------------- per image
_RCACHE = {}


def _radius(shape):
    """Normalised 'square' radius: 0 at the centre, 1 at the image border."""
    r = _RCACHE.get(shape)
    if r is None:
        H, W = shape
        yy, xx = np.indices(shape, dtype=np.float32)
        r = np.maximum(np.abs(yy - (H - 1) / 2) / (H / 2), np.abs(xx - (W - 1) / 2) / (W / 2))
        _RCACHE.clear()
        _RCACHE[shape] = r
    return r


def image_metrics(img, flat, valid):
    """QC numbers of one image. Returns (metrics dict, Laplacian of the flattened image)."""
    lap = ndi.laplace(flat)
    inner = ndi.binary_erosion(valid, iterations=1)
    out = dict(qc_sharpness=float(lap[inner].var()) if inner.any() else np.nan,
               qc_valid_pct=float(100 * valid.mean()))
    r = _radius(img.shape)
    edge, centre = valid & (r > EDGE_BAND), valid & (r < CENTRE_BAND)
    ce = img[centre].mean() if centre.any() else np.nan
    out["qc_edge_center_ratio"] = float(img[edge].mean() / ce) if edge.any() and ce and ce > 0 else np.nan
    vals = img[valid]
    m = vals.max() if vals.size else 0
    out["qc_saturated_pct"] = float(100 * np.mean(vals >= m)) if m > 0 else np.nan
    return out, lap


# --------------------------------------------------------------------------------------------- per tile
TILE_COLS = ["tile_row", "tile_col", "x_um", "y_um", "tile_valid_pct", "area_frac_pct", "length_density_um_per_um2",
             "pore_lt_mean_um", "fiber_lt_mean_um", "order_S", "mean_angle_deg", "coherency", "mean_reflectance",
             "reflectance_cv", "sharpness", "tile_flag"]


def tile_table(img, valid, fib, mask, sk, lt, lt_f, theta_map, coh_map, lap, t, px, length_fn):
    """Metrics of every t x t tile that is at least 80 % inside the analysed area.
    Returns (list of row dicts, % of those tiles flagged dark or blurred)."""
    rows = []
    H, W = img.shape
    for i in range(0, H - t + 1, t):
        for j in range(0, W - t + 1, t):
            sl = (slice(i, i + t), slice(j, j + t))
            v = valid[sl]
            if v.mean() <= 0.8:
                continue
            nv = int(v.sum())
            f = fib[sl] & v
            im = img[sl][v]
            th, co = theta_map[sl][f], coh_map[sl][f]
            if th.size:
                z = np.exp(2j * th).mean()
                S, ang, coh = float(abs(z)), float(np.degrees(0.5 * np.angle(z))), float(co.mean())
            else:
                S = ang = coh = np.nan
            lp = lt[sl][v & ~mask[sl]]
            lf = lt_f[sl][f]
            mref = float(im.mean())
            rows.append(dict(
                tile_row=i // t, tile_col=j // t, x_um=round((j + t / 2) * px, 3), y_um=round((i + t / 2) * px, 3),
                tile_valid_pct=round(100 * nv / v.size, 1),
                area_frac_pct=100 * f.sum() / nv,
                length_density_um_per_um2=length_fn(sk[sl] & v) * px / (nv * px * px),
                pore_lt_mean_um=float(np.nanmean(lp)) * px if np.isfinite(lp).any() else np.nan,
                fiber_lt_mean_um=float(np.nanmean(lf)) * px if np.isfinite(lf).any() else np.nan,
                order_S=S, mean_angle_deg=ang, coherency=coh, mean_reflectance=mref,
                reflectance_cv=float(im.std() / mref) if mref > 0 else np.nan,
                sharpness=float(lap[sl][v].var())))
    if not rows:
        return rows, np.nan
    ref = np.median([r["mean_reflectance"] for r in rows])
    shp = np.median([r["sharpness"] for r in rows])
    bad = 0
    for r in rows:
        fl = []
        if ref > 0 and r["mean_reflectance"] < TILE_DARK * ref:
            fl.append("dark")
        if shp > 0 and r["sharpness"] < TILE_BLUR * shp:
            fl.append("blurred")
        r["tile_flag"] = ", ".join(fl)
        bad += bool(fl)
    return rows, 100 * bad / len(rows)


# --------------------------------------------------------------------------------------------- per file
# feature, description. "rel" features are divided by the median of all files of the same run/folder,
# so they do not depend on laser power or detector gain.
FEATURES = [
    ("sharpness_rel", "median image sharpness / median of all files in the folder"),
    ("reflectance_rel", "median mean reflectance / median of all files in the folder"),
    ("edge_center_ratio", "median brightness of the outer band / centre"),
    ("edge_center_dev", "|log(edge_center_ratio)|: 0 = even, larger = uneven edges"),
    ("saturated_pct", "largest % of pixels at the maximum value in one image"),
    ("thr_sensitivity", "median (area at -10 % threshold - area at +10 %) / area"),
    ("area_frac_pct", "median fiber area fraction (%)"),
    ("tile_cv", "median tile CV (patchiness)"),
    ("bad_tiles_pct", "mean % of tiles flagged dark or blurred"),
    ("valid_pct", "median % of the image analysed (edge + hotspot excluded)"),
    ("depth_reflectance_cv", "CV of the mean reflectance between the analysed images"),
    ("fiber_extent_frac", "z-stacks with >= 5 images: fraction of images with >= half the largest fiber area"),
]
FEATURE_KEYS = [k for k, _ in FEATURES]

# key, test on the feature row, flag text
RULES = [
    ("sharpness_rel", lambda v: v < 0.5, "blurred / out of focus (sharpness < 0.5 x folder median)"),
    ("edge_center_dev", lambda v: v > math.log(1.5), "uneven edges, possible drying (edge/centre brightness "
                                                      "outside 0.67-1.5)"),
    ("saturated_pct", lambda v: v > 0.5, "saturated (> 0.5 % of pixels at the maximum)"),
    ("thr_sensitivity", lambda v: v > 0.6, "fiber mask depends strongly on the threshold (> 60 % change "
                                           "for a ±10 % threshold)"),
    ("area_frac_pct", lambda v: v < 1.0, "very few fibers (< 1 % area)"),
    ("bad_tiles_pct", lambda v: v > 20, "> 20 % of tiles dark or blurred"),
    ("valid_pct", lambda v: v < 50, "less than half of the image analysed"),
    ("reflectance_rel", lambda v: v < 0.4, "dim (reflectance < 0.4 x folder median)"),
    ("fiber_extent_frac", lambda v: v < 0.3, "fibers only in a few images of the stack"),
]

PROBLEM_WORDS = ("dried", "dry", "drying", "evaporat", "bubble", "crack", "detach", "blur", "defocus",
                 "out-of-focus", "bad", "artifact", "artefact", "torn", "tear", "fold", "damage", "broken")


def file_features(per):
    """One row of FEATURES per file from the per-slice rows of one folder (index = file name)."""
    def med(col):
        return per.groupby("file", sort=False)[col].median() if col in per else None
    files = list(dict.fromkeys(per["file"]))
    f = pd.DataFrame(index=pd.Index(files, name="file"))
    sh, rf = med("qc_sharpness"), med("mean_reflectance")
    f["sharpness_rel"] = sh / sh.median() if sh is not None and sh.median() > 0 else np.nan
    f["reflectance_rel"] = rf / rf.median() if rf is not None and rf.median() > 0 else np.nan
    f["edge_center_ratio"] = med("qc_edge_center_ratio")
    f["edge_center_dev"] = np.abs(np.log(f["edge_center_ratio"].astype(float)))
    f["saturated_pct"] = per.groupby("file", sort=False)["qc_saturated_pct"].max()
    a = per["area_frac_pct"].replace(0, np.nan)
    sens = (per["area_frac_pct_thr_minus10pct"] - per["area_frac_pct_thr_plus10pct"]) / a
    f["thr_sensitivity"] = sens.groupby(per["file"], sort=False).median()
    f["area_frac_pct"] = med("area_frac_pct")
    f["tile_cv"] = med("tile_cv")
    f["bad_tiles_pct"] = per.groupby("file", sort=False)["qc_bad_tiles_pct"].mean() \
        if "qc_bad_tiles_pct" in per else np.nan
    f["valid_pct"] = med("qc_valid_pct")
    g = per.groupby("file", sort=False)["mean_reflectance"]
    f["depth_reflectance_cv"] = g.std() / g.mean()
    ext = {}
    for name, d in per.groupby("file", sort=False):
        af = d["area_frac_pct"].to_numpy(float)
        ext[name] = float(np.mean(af >= 0.5 * np.nanmax(af))) if len(af) >= 5 and np.nanmax(af) > 0 else np.nan
    f["fiber_extent_frac"] = pd.Series(ext)
    return f[FEATURE_KEYS]


def rule_flags(row):
    out = []
    for key, test, text in RULES:
        v = row.get(key)
        if v is not None and np.isfinite(v) and test(v):
            out.append(text)
    return out


def auto_label(note):
    """'problem' if the file-name note mentions a known problem word, else 'ok'."""
    n = str(note or "").lower()
    return "problem" if n and n != "nan" and any(w in n for w in PROBLEM_WORDS) else "ok"


def assess(per, model_path=None, log=print):
    """QC of the files of one folder. Returns the 'quality' table (one row per file)."""
    feats = file_features(per)
    first = per.groupby("file", sort=False).first()
    q = pd.DataFrame(index=feats.index)
    for c in ("date", "sample", "condition", "other"):
        q[c] = first[c] if c in first else None
    flags = {n: rule_flags(feats.loc[n].to_dict()) for n in feats.index}
    q["qc_ml_p_problem"] = np.nan
    if model_path:
        try:
            m = load_model(model_path)
            p = predict(m, feats)
            q["qc_ml_p_problem"] = p
            for n, pv in zip(feats.index, p):
                if np.isfinite(pv) and pv >= 0.5:
                    flags[n].append(f"QC model: likely problem (p = {pv:.2f})")
            log(f"QC model {model_path}: trained on {m.get('n_ok')} ok + {m.get('n_problem')} problem files")
        except Exception as e:
            log(f"  WARNING: QC model not used ({e})")
    q["qc_status"] = ["check" if flags[n] else "ok" for n in feats.index]
    q["qc_flags"] = ["; ".join(flags[n]) for n in feats.index]
    q["label_from_note"] = [auto_label(v) for v in q["other"]]
    q = q.join(feats)
    q.index.name = "file"
    q = q.reset_index()
    order = ["file", "date", "sample", "condition", "other", "qc_status", "qc_flags", "qc_ml_p_problem",
             "label_from_note"] + FEATURE_KEYS
    return q[order]


def settings_rows(model_path):
    rows = [("qc_rules", "; ".join(t for _, _, t in RULES), "rule-based QC flags (any rule -> qc_status 'check')"),
            ("qc_tile_flags", f"dark < {TILE_DARK} x, blurred < {TILE_BLUR} x the image's median tile",
             "tile flags behind qc_bad_tiles_pct")]
    if model_path:
        try:
            m = load_model(model_path)
            desc = (f"trained {m.get('created')} on {m.get('n_ok')} ok + {m.get('n_problem')} problem files; "
                    f"cross-validated balanced accuracy {m.get('cv_balanced_accuracy')}")
        except Exception as e:
            desc = f"could not be read: {e}"
        rows.append(("qc_model", model_path, desc))
    return rows


# --------------------------------------------------------------------------------------------- model
def _require_sklearn():
    try:
        import sklearn  # noqa: F401
    except ImportError as e:
        raise RuntimeError("scikit-learn is not installed (pip install scikit-learn)") from e


def train(X, y, seed=0):
    """Random forest on the FEATURES of labelled files. y: 1 = problem, 0 = ok.
    Returns a dict with the fitted pipeline, cross-validation results and feature importances."""
    _require_sklearn()
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    X = X[FEATURE_KEYS].astype(float)
    y = np.asarray(y, int)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 < 2 or n0 < 2:
        raise ValueError(f"need at least 2 'ok' and 2 'problem' files (have {n0} ok, {n1} problem)")
    def make():
        return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                             RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced",
                                                    random_state=seed))
    k = min(5, n0, n1)
    pred = cross_val_predict(make(), X, y, cv=StratifiedKFold(k, shuffle=True, random_state=seed))
    model = make().fit(X, y)
    imp = model[-1].feature_importances_
    return dict(pipeline=model, features=list(FEATURE_KEYS), kind=MODEL_KIND, version=MODEL_VERSION,
                created=datetime.now().strftime("%Y-%m-%d %H:%M"), n_ok=n0, n_problem=n1, cv_folds=k,
                cv_balanced_accuracy=round(float(balanced_accuracy_score(y, pred)), 3),
                cv_confusion=confusion_matrix(y, pred, labels=[0, 1]).tolist(),
                cv_pred=pred.tolist(),
                importances=sorted(zip(FEATURE_KEYS, map(float, imp)), key=lambda t: -t[1]))


def save_model(path, m):
    import joblib
    m = {k: v for k, v in m.items() if k != "cv_pred"}
    try:
        import sklearn
        m["sklearn_version"] = sklearn.__version__
    except ImportError:
        pass
    joblib.dump(m, path)
    return path


def load_model(path):
    """Load a model saved by save_model. Only load model files you made yourself: the format can run code."""
    _require_sklearn()
    import joblib
    m = joblib.load(path)
    if not isinstance(m, dict) or m.get("kind") != MODEL_KIND:
        raise ValueError("not a Collagen Analyzer QC model")
    return m


def predict(m, feats):
    X = feats.reindex(columns=m["features"]).astype(float)
    return m["pipeline"].predict_proba(X)[:, 1]
