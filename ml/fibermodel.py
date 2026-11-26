"""Trained fiber mask: a pixel classifier (random forest) that learns from strokes you paint.

Instead of a threshold on the ridge filter, every pixel gets a probability of being fiber from a
set of filter responses of the flattened image (smoothed intensity, Hessian eigenvalues = ridge /
blob shape, gradient magnitude, local contrast) at several scales. The scales are in µm, so a model
trained at one magnification can be applied to images with another pixel size (check it there).

Training data: on some images you paint a few strokes "fiber" and "background" (label 1 / 2, 0 =
not labelled); the classifier is trained on the labelled pixels only, as in ilastik.
"""
from datetime import datetime

import numpy as np
from scipy import ndimage as ndi

MODEL_KIND = "collagen-analyzer-fiber"
FEATURE_VERSION = 1
SCALES_UM = (0.1, 0.2, 0.4, 0.8, 1.6)
_CACHE = {}


def feature_names():
    out = ["flat"]
    for s in SCALES_UM:
        out += [f"gauss_{s:g}um", f"hess1_{s:g}um", f"hess2_{s:g}um", f"gradient_{s:g}um"]
    return out + ["local_sd_0.8um"]


def features(flat, px):
    """(H, W, F) float32 feature stack of a flattened image (core.flatten), px = pixel size in µm."""
    flat = np.asarray(flat, np.float32)
    out = [flat]
    for s_um in SCALES_UM:
        s = max(s_um / px, 0.5)
        g = ndi.gaussian_filter(flat, s)
        # Hessian of the smoothed image (scale-normalised): eigenvalues l1 <= l2; a bright ridge has l1 << 0
        hrr = ndi.gaussian_filter(flat, s, order=(2, 0)) * s * s
        hcc = ndi.gaussian_filter(flat, s, order=(0, 2)) * s * s
        hrc = ndi.gaussian_filter(flat, s, order=(1, 1)) * s * s
        tr, d = (hrr + hcc) / 2, np.sqrt(((hrr - hcc) / 2) ** 2 + hrc ** 2)
        out += [g, tr - d, tr + d, ndi.gaussian_gradient_magnitude(flat, s) * s]
    s = max(0.8 / px, 0.5)
    m = ndi.gaussian_filter(flat, s)
    out.append(np.sqrt(np.maximum(ndi.gaussian_filter(flat * flat, s) - m * m, 0)))
    return np.stack(out, axis=-1).astype(np.float32)


def train(samples, seed=0, max_px=200000, n_estimators=40, max_depth=16, sigma_bg_um=None):
    """samples: list of (flat image, px, labels) with labels 1 = fiber, 2 = background, 0 = unlabelled.
    Returns a model dict with the forest and its out-of-bag accuracy."""
    from sklearn.ensemble import RandomForestClassifier
    X, y = [], []
    for flat, px, lab in samples:
        m = lab > 0
        if not m.any():
            continue
        F = features(flat, px)
        X.append(F[m])
        y.append((lab[m] == 1).astype(np.uint8))
    if not X:
        raise ValueError("no painted pixels")
    X, y = np.concatenate(X), np.concatenate(y)
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    if n1 < 50 or n0 < 50:
        raise ValueError(f"paint more: {n1} fiber and {n0} background pixels (need at least 50 of each)")
    rng = np.random.default_rng(seed)
    if len(y) > max_px:                      # balanced subsample
        idx = np.concatenate([rng.choice(np.flatnonzero(y == c), min(max_px // 2, int((y == c).sum())),
                                         replace=False) for c in (0, 1)])
        X, y = X[idx], y[idx]
    rf = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, min_samples_leaf=3,
                                class_weight="balanced_subsample", oob_score=True, n_jobs=-1, random_state=seed)
    rf.fit(X, y)
    rf.set_params(n_jobs=1)                  # analysis workers run in parallel already
    imp = sorted(zip(feature_names(), map(float, rf.feature_importances_)), key=lambda t: -t[1])
    return dict(kind=MODEL_KIND, feature_version=FEATURE_VERSION, scales_um=list(SCALES_UM), forest=rf,
                sigma_bg_um=sigma_bg_um,
                created=datetime.now().strftime("%Y-%m-%d %H:%M"), n_fiber_px=n1, n_background_px=n0,
                n_images=len(samples), oob_accuracy=round(float(rf.oob_score_), 4), importances=imp)


def probability(flat, px, model, chunk=262144):
    """Fiber probability (0..1) of every pixel."""
    F = features(flat, px)
    H, W, k = F.shape
    F = F.reshape(-1, k)
    p = np.empty(len(F), np.float32)
    rf = model["forest"]
    for i in range(0, len(F), chunk):
        p[i:i + chunk] = rf.predict_proba(F[i:i + chunk])[:, 1]
    return p.reshape(H, W)


def save_model(path, m):
    import joblib
    try:
        import sklearn
        m = dict(m, sklearn_version=sklearn.__version__)
    except ImportError:
        pass
    joblib.dump(m, path, compress=3)
    return path


def load_model(path):
    """Load (and cache) a model saved by save_model. Only load model files you made yourself."""
    if path in _CACHE:
        return _CACHE[path]
    import joblib
    m = joblib.load(path)
    if not isinstance(m, dict) or m.get("kind") != MODEL_KIND:
        raise ValueError("not a Collagen Analyzer fiber model")
    if m.get("feature_version") != FEATURE_VERSION:
        raise ValueError("this fiber model was made by another version of the app; train it again")
    _CACHE.clear()
    _CACHE[path] = m
    return m


def describe(m):
    return (f"trained {m.get('created')} on {m.get('n_images')} image(s), {m.get('n_fiber_px')} fiber + "
            f"{m.get('n_background_px')} background pixels; out-of-bag accuracy {m.get('oob_accuracy'):.1%}")
