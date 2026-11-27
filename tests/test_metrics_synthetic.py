"""Method validation on synthetic fiber networks with known alignment and density."""
import numpy as np
import pytest

import collagen_core as core
import synth


def _analyse(img, px=0.1):
    P = core.build_parser().parse_args(["."])
    P.ridge_sigmas_um = [float(v) for v in P.ridge_sigmas_um.split(",")]
    G = core.geometry(px, P)
    valid = core.analysis_mask(img.shape, None, edge=G["edge"])
    thr = core.otsu_threshold(core.ridge(core.flatten(img, G["sigma_bg"]), G["ridge_sigmas"])[valid])
    m, _ = core.analyse_slice(img, valid, thr, G)
    return m


@pytest.mark.parametrize("kappa,angle", [(1.0, 30.0), (3.0, -45.0), (8.0, 60.0)])
def test_alignment_and_angle(kappa, angle):
    img, truth = synth.network(kappa=kappa, mean_angle_deg=angle, seed=11, shape=(384, 384))
    m = _analyse(img)
    assert abs(m["order_S"] - truth["S"]) < 0.12
    d = (m["mean_angle_deg"] - truth["mean_angle_deg"] + 90) % 180 - 90      # axial difference
    assert abs(d) < 6


def test_random_network_is_not_aligned():
    img, _ = synth.network(kappa=0.0, seed=5, shape=(384, 384))
    assert _analyse(img)["order_S"] < 0.15


def test_alignment_increases_with_kappa():
    S = [_analyse(synth.network(kappa=k, seed=3, shape=(320, 320))[0])["order_S"] for k in (0, 1, 3, 10)]
    assert all(a < b for a, b in zip(S, S[1:]))


def test_density_increases_with_fiber_number():
    a = [_analyse(synth.network(n_fibers=n, seed=2, shape=(320, 320))[0]) for n in (80, 200, 400)]
    af = [m["area_frac_pct"] for m in a]
    ld = [m["length_density_um_per_um2"] for m in a]
    pores = [m["pore_lt_mean_um"] for m in a]
    assert af[0] < af[1] < af[2]
    assert ld[0] < ld[1] < ld[2]
    assert pores[0] > pores[1] > pores[2]


def test_expected_S_formula():
    rng = np.random.default_rng(0)
    th = synth.sample_angles(rng, 200000, 2.0, 10.0)
    assert abs(abs(np.exp(2j * th).mean()) - synth.expected_S(2.0)) < 0.01
