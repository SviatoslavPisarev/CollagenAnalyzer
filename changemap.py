"""Before/after registration and local change maps.

The Before and After images of one gel are usually re-imaged at (nearly) the same place, but the
stage, the gel or the cover glass moves a little in between. register() finds the x/y shift between
two projections (masked phase correlation, so the excluded edge and reflection hotspot do not count)
and reports how similar the two images are once aligned (Pearson r of the overlapping area). Because
real remodeling lowers r, r is also compared with the r of the same two images at 12 large random
shifts (unrelated positions): z = (r - mean) / SD of those.
  r >= 0.5            'registered': same field
  z >= 5 and r >= 0.1 'same field, strongly changed': maps shown with a warning (shift less exact)
  otherwise           'different field?': no tile map
For z-stacks the max projection is used (insensitive to a small z offset); for time-lapses the first
time point, for single images the image.

tile_changes() pairs the per-tile metrics (mmddyy_tiles.csv, averaged over the images of each file,
dark/blurred tiles left out) of the Before tile with the After tile at the same position in the gel.
Tiles sit on a fixed grid, so after a shift the nearest After tile is used and its overlap with the
Before tile (0.25-1) is recorded.

Shift convention: shift (dy, dx) in px is what moves the After image onto the Before image; a
structure at position p in the Before image is at p - shift in the After image.
"""
import numpy as np
import pandas as pd
from scipy import ndimage as ndi

MAP_METRICS = [("order_S", "alignment S", "PuOr_r"),
               ("area_frac_pct", "fiber area fraction (%)", "RdBu_r"),
               ("pore_lt_mean_um", "pore size (µm)", "BrBG")]
TILE_METRICS = ["area_frac_pct", "length_density_um_per_um2", "pore_lt_mean_um", "fiber_lt_mean_um", "order_S",
                "coherency", "mean_reflectance"]
R_GOOD, R_MIN, Z_MIN, N_NULL = 0.5, 0.1, 5.0, 12
SAME_FIELD = ("registered", "same field, strongly changed")


def _prep(img, valid, sigma_px):
    """Flatten uneven illumination, scale to zero mean / unit SD inside the analysed area."""
    img = np.asarray(img, np.float32)
    bg = ndi.gaussian_filter(img, sigma_px)
    f = img / (bg + 1e-6 * max(float(img.max()), 1.0))
    v = f[valid]
    if v.size == 0:
        return np.zeros_like(f)
    f = (f - v.mean()) / (v.std() + 1e-9)
    f[~valid] = 0
    return f


def _overlap_corr(a, b, va, vb, shift):
    """Pearson r of a and b shifted by shift, over the pixels valid in both."""
    bs = ndi.shift(b, shift, order=1, cval=0)
    vbs = ndi.shift(vb.astype(np.float32), shift, order=0, cval=0) > 0.5
    m = va & vbs
    if m.sum() < 100:
        return np.nan, 0.0
    x, y = a[m], bs[m]
    if x.std() == 0 or y.std() == 0:
        return np.nan, float(m.mean())
    return float(np.corrcoef(x, y)[0, 1]), float(m.mean())


def register(before, after, valid_b, valid_a, px_um, sigma_um=2.5, max_shift_frac=0.4):
    """Shift between a Before and an After projection. Returns a dict:
    shift_y_px, shift_x_px, shift_x_um, shift_y_um (x right, y down in the image), r_registered,
    r_unshifted, r_null_mean, r_null_sd, z (see the module notes), overlap_pct, status ('registered',
    'same field, strongly changed', 'different field?', 'size differs')."""
    from skimage.registration import phase_cross_correlation
    out = dict(shift_y_px=np.nan, shift_x_px=np.nan, shift_x_um=np.nan, shift_y_um=np.nan, r_registered=np.nan,
               r_unshifted=np.nan, r_null_mean=np.nan, r_null_sd=np.nan, z=np.nan, overlap_pct=np.nan, status="")
    if before.shape != after.shape:
        out["status"] = "size differs"
        return out
    sig = max(sigma_um / px_um, 2.0)
    a, b = _prep(before, valid_b, sig), _prep(after, valid_a, sig)
    if valid_b.all() and valid_a.all():
        shift = phase_cross_correlation(a, b, upsample_factor=10, normalization=None)[0]
    else:
        shift = phase_cross_correlation(a, b, reference_mask=valid_b, moving_mask=valid_a)
        shift = shift[0] if isinstance(shift, tuple) else shift
    shift = np.asarray(shift, float)
    H, W = before.shape
    if abs(shift[0]) > max_shift_frac * H or abs(shift[1]) > max_shift_frac * W:
        shift = np.zeros(2)                 # implausible: treat as unshifted, judged by r below
    r0, _ = _overlap_corr(a, b, valid_b, valid_a, (0, 0))
    r1, ov = _overlap_corr(a, b, valid_b, valid_a, shift)
    if np.isfinite(r0) and (not np.isfinite(r1) or r0 >= r1):
        shift, r1 = np.zeros(2), r0
        _, ov = _overlap_corr(a, b, valid_b, valid_a, shift)
    rng = np.random.default_rng(0)
    null = []
    for _ in range(N_NULL):
        ang, mag = rng.uniform(0, 2 * np.pi), rng.uniform(0.2, 0.3) * min(H, W)
        null.append(_overlap_corr(a, b, valid_b, valid_a, (mag * np.sin(ang), mag * np.cos(ang)))[0])
    null = np.asarray([v for v in null if np.isfinite(v)])
    nm, ns = (float(null.mean()), float(max(null.std(), 0.01))) if null.size >= 3 else (0.0, 0.05)
    z = (r1 - nm) / ns if np.isfinite(r1) else np.nan
    if not np.isfinite(r1):
        status = "different field?"
    elif r1 >= R_GOOD:
        status = "registered"
    elif z >= Z_MIN and r1 >= R_MIN:
        status = "same field, strongly changed"
    else:
        status = "different field?"
    out.update(shift_y_px=float(shift[0]), shift_x_px=float(shift[1]), shift_x_um=float(shift[1] * px_um),
               shift_y_um=float(shift[0] * px_um), r_registered=r1, r_unshifted=r0, r_null_mean=nm, r_null_sd=ns,
               z=z, overlap_pct=100 * ov, status=status)
    return out


def file_tiles(tiles, name, metrics=TILE_METRICS):
    """Per-tile metrics of one file averaged over its analysed images (flagged tiles left out)."""
    t = tiles[tiles["file"] == name]
    if "tile_flag" in t:
        t = t[t["tile_flag"].fillna("").astype(str).eq("")]
    cols = [c for c in metrics if c in t]
    if t.empty:
        return pd.DataFrame(columns=["tile_row", "tile_col", "x_um", "y_um", *cols])
    return t.groupby(["tile_row", "tile_col"], as_index=False)[["x_um", "y_um", *cols]].mean()


def tile_changes(tb, ta, shift_y_px, shift_x_px, tile_px, metrics=TILE_METRICS):
    """Pair Before tiles with the After tile at the same gel position. tb, ta: file_tiles() frames.
    Returns one row per pair: tile_row, tile_col, x_um, y_um (Before grid), after_tile_row/col, overlap,
    <metric>_before, <metric>_after, <metric>_change (after - before)."""
    if tb.empty or ta.empty or not np.isfinite(shift_y_px):
        return pd.DataFrame()
    fy, fx = shift_y_px / tile_px, shift_x_px / tile_px
    idx = {(int(r), int(c)): i for i, (r, c) in enumerate(zip(ta["tile_row"], ta["tile_col"]))}
    rows = []
    cols = [m for m in metrics if m in tb and m in ta]
    for _, r in tb.iterrows():
        yr, xc = r["tile_row"] - fy, r["tile_col"] - fx
        r2, c2 = int(round(yr)), int(round(xc))
        ov = (1 - abs(yr - r2)) * (1 - abs(xc - c2))
        j = idx.get((r2, c2))
        if j is None:
            continue
        a = ta.iloc[j]
        row = dict(tile_row=int(r["tile_row"]), tile_col=int(r["tile_col"]), x_um=r["x_um"], y_um=r["y_um"],
                   after_tile_row=r2, after_tile_col=c2, overlap=round(ov, 3))
        for m in cols:
            row[f"{m}_before"], row[f"{m}_after"] = r[m], a[m]
            row[f"{m}_change"] = a[m] - r[m]
        rows.append(row)
    return pd.DataFrame(rows)


def change_grid(tc, metric, tb):
    """2-D array (rows x cols of the Before tile grid) of <metric>_change, NaN where no pair."""
    if tb.empty:
        return np.full((1, 1), np.nan)
    nr, nc = int(tb["tile_row"].max()) + 1, int(tb["tile_col"].max()) + 1
    g = np.full((nr, nc), np.nan)
    if len(tc) and f"{metric}_change" in tc:
        g[tc["tile_row"].astype(int), tc["tile_col"].astype(int)] = tc[f"{metric}_change"].to_numpy(float)
    return g


def registered_after(after, shift):
    """The After image moved onto the Before image (for display)."""
    return ndi.shift(np.asarray(after, np.float32), shift, order=1, cval=np.nan)


def overlay_rgb(before, after_reg, valid):
    """Green = Before, magenta = After (registered); white where both are bright."""
    def norm(x):
        x = np.asarray(x, float)
        ok = np.isfinite(x) & valid
        if not ok.any():
            return np.zeros_like(x)
        lo, hi = np.percentile(x[ok], [1, 99.5])
        return np.clip((np.nan_to_num(x, nan=lo) - lo) / (hi - lo + 1e-9), 0, 1)
    b, a = norm(before), norm(after_reg)
    return np.dstack([a, b, a])
