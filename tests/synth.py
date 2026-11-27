"""Synthetic collagen-like fiber networks with known structure, for tests and method validation.

A network is a set of straight fiber segments (anti-aliased lines with a Gaussian cross-section)
whose angles follow a von Mises distribution on the axial circle: kappa = 0 gives random
directions, larger kappa gives more aligned fibers. For angles theta with doubled-angle von Mises
concentration kappa, the expected order parameter is S = I1(kappa) / I0(kappa).

    img, truth = network(seed=0, kappa=2, mean_angle_deg=30)
    truth -> dict(S=..., mean_angle_deg=..., n_fibers=..., px_um=...)

Angles: degrees counter-clockwise from the image x-axis (y up), the app's convention.
"""
import numpy as np
from scipy import ndimage as ndi
from scipy.special import i0, i1


def expected_S(kappa):
    """Order parameter of axial angles drawn as theta = phi / 2, phi ~ von Mises(2 mu, kappa)."""
    return float(i1(kappa) / i0(kappa)) if kappa > 0 else 0.0


def sample_angles(rng, n, kappa, mean_angle_deg):
    """n fiber angles (rad, -pi/2..pi/2) around mean_angle_deg with concentration kappa."""
    mu2 = 2 * np.radians(mean_angle_deg)
    phi = rng.vonmises(mu2, kappa, n) if kappa > 0 else rng.uniform(-np.pi, np.pi, n)
    return (phi / 2 + np.pi / 2) % np.pi - np.pi / 2


def draw_segments(shape, x0, y0, theta, length_px, width_px):
    """Sum of Gaussian-profile line segments. theta CCW from +x with y up (rows go down)."""
    H, W = shape
    img = np.zeros(shape, np.float32)
    step = 0.5
    for xc, yc, th, L in zip(x0, y0, theta, np.broadcast_to(length_px, x0.shape)):
        n = max(int(L / step), 2)
        s = np.linspace(-L / 2, L / 2, n)
        xs = xc + s * np.cos(th)
        ys = yc - s * np.sin(th)                     # y up in angle convention -> rows decrease
        r, c = np.round(ys).astype(int), np.round(xs).astype(int)
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        np.add.at(img, (r[ok], c[ok]), 1.0)
    img = ndi.gaussian_filter(img, width_px / 2.355)   # width = FWHM of the fiber cross-section
    return img / (img.max() + 1e-12)


def network(shape=(512, 512), px_um=0.1, n_fibers=260, length_um=12.0, width_um=0.35, kappa=0.0,
            mean_angle_deg=0.0, noise=0.03, background=0.05, seed=0, centers=None, angles=None):
    """One synthetic reflectance image (float32, 0..~1) and its ground truth.
    centers/angles may be given to reuse the same fibers (e.g. a shifted copy of a gel)."""
    rng = np.random.default_rng(seed)
    H, W = shape
    if centers is None:
        pad = length_um / px_um / 2
        centers = np.c_[rng.uniform(-pad, W + pad, n_fibers), rng.uniform(-pad, H + pad, n_fibers)]
    if angles is None:
        angles = sample_angles(rng, len(centers), kappa, mean_angle_deg)
    fib = draw_segments(shape, centers[:, 0], centers[:, 1], angles, length_um / px_um, width_um / px_um)
    img = background + 0.9 * np.clip(fib * 1.6, 0, 1)
    img = img + rng.normal(0, noise, shape)
    img = np.clip(img, 0, None).astype(np.float32)
    z = np.exp(2j * angles).mean()
    truth = dict(S=float(abs(z)), S_expected=expected_S(kappa), mean_angle_deg=float(np.degrees(np.angle(z) / 2)),
                 n_fibers=len(centers), px_um=px_um, centers=centers, angles=angles)
    return img, truth


def stack(nz=3, seed=0, **kw):
    """A small z-stack: the same network with a little extra noise per slice. Returns (Z, Y, X), truth."""
    img, truth = network(seed=seed, **kw)
    rng = np.random.default_rng(seed + 1000)
    out = np.stack([np.clip(img + rng.normal(0, 0.01, img.shape), 0, None) for _ in range(nz)]).astype(np.float32)
    return out, truth


def save_tif(path, arr, px_um=0.1, dz_um=0.5):
    """ImageJ TIFF (Z, Y, X) or (Y, X), uint16, with pixel size and z-step, readable by the app."""
    import tifffile
    a = np.asarray(arr, np.float32)
    a = (np.clip(a / max(float(a.max()), 1e-9), 0, 1) * 4000).astype(np.uint16)
    axes = "ZYX" if a.ndim == 3 else "YX"
    tifffile.imwrite(path, a, imagej=True, resolution=(1 / px_um, 1 / px_um),
                     metadata=dict(spacing=dz_um, unit="um", axes=axes))
    return path
