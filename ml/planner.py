"""Experiment planner: Gaussian-process model of the AMF response + Bayesian optimisation.

Each experiment is one After-AMF file paired with the Before-AMF file(s) of the same sample (same
mmddyy_##_ and results folder); its response is the change of one metric (after - before, or %).
The inputs are the experiment's settings (AMF frequency, time, voltage, NC concentration, ...).

A Gaussian process (Matérn 5/2 kernel, one length scale per input, plus a noise term) is fitted
to the inputs scaled to 0..1 (log scale where chosen). Expected improvement then proposes the
next experiments; a batch is built with the 'kriging believer' rule (each chosen point is
added as if its predicted value had been measured, so the next one goes somewhere else).
Needs scikit-learn and scipy.
"""
import math

import numpy as np
import pandas as pd

# key, column in the per-file sheet, label, unit, factor (column x factor = value shown), log by default,
# rounding step (in shown units)
VARIABLES = [
    ("amf_freq_hz", "amf_freq_hz", "AMF frequency", "Hz", 1.0, True, 10),
    ("amf_time_min", "amf_time_s", "AMF exposure time", "min", 1 / 60, False, 0.5),
    ("amf_vpp_set", "amf_vpp_set", "AMF Vp-p set", "V", 1.0, False, 0.5),
    ("amf_v_measured", "amf_v_measured", "AMF measured voltage", "V", 1.0, False, 0.1),
    ("amf_app_set", "amf_app_set", "AMF Ap-p set (power supply)", "A", 1.0, False, 0.1),
    ("amf_coil_turns", "amf_coil_turns", "Coil turns", "turns", 1.0, False, "200, 500"),
    ("amf_b_peak_mT", "amf_b_peak_mT", "Magnetic field (peak)", "mT", 1.0, False, 0.5),
    ("amf_ampere_turns", "amf_ampere_turns_pp", "Ap-p × coil turns", "A·turns", 1.0, False, 10),
    ("nc_conc_nM", "nc_conc_nM", "NC concentration", "nM", 1.0, True, 0.05),
    ("premixed", "nc_mixing", "NCs premixed (1) or incubated (0)", "", None, False, 1),
    ("inc_min", "nc_incubation_time_s", "NC incubation time", "min", 1 / 60, False, 5),
    ("collagen_mg_ml", "collagen_mg_ml", "Collagen", "mg/mL", 1.0, False, 0.5),
]
VAR = {v[0]: v for v in VARIABLES}


def _value(key, s):
    """Column values -> shown values for variable key (Series)."""
    _, col, _, _, f, _, _ = VAR[key]
    if key == "premixed":
        m = s.astype(str).str.lower()
        return pd.Series(np.where(m.str.contains("premix"), 1.0, np.where(m.str.contains("incub"), 0.0, np.nan)),
                         index=s.index)
    return pd.to_numeric(s, errors="coerce") * f


def build_experiments(pf, metric, change="diff", exclude_qc=True):
    """Pair After-AMF files with their sample's Before-AMF files.
    pf: per-file rows (one or several workbooks); metric: column name without _mean.
    Returns (experiments DataFrame, list of notes)."""
    col = metric + "_mean"
    notes = []
    if col not in pf:
        return pd.DataFrame(), [f"metric {metric} not in the spreadsheets"]
    pf = pf.copy()
    src = pf["_src"] if "_src" in pf else pd.Series("", index=pf.index)
    pf["_key"] = pf["date"].astype(str) + "|" + pf["sample"].astype(str) + "|" + src.astype(str)
    flagged = pf.get("qc_status", pd.Series("", index=pf.index)).astype(str).eq("check")
    rows, n_qc, n_nobefore = [], 0, 0
    for _, g in pf.groupby("_key", sort=False):
        before = g[g["condition"].astype(str).eq("Before AMF")]
        after = g[g["condition"].astype(str).str.startswith("After")]
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
        for i, r in after.iterrows():
            a = pd.to_numeric(pd.Series([r[col]]), errors="coerce").iloc[0]
            if not np.isfinite(a):
                continue
            y = a - b0 if change == "diff" else (100 * (a - b0) / b0 if b0 else np.nan)
            row = dict(file=r["file"], sample=r["sample"], date=r["date"], before=b0, after=float(a), y=y)
            for key in VAR:
                c = VAR[key][1]
                row[key] = float(_value(key, pd.Series([r.get(c)])).iloc[0]) if c in r else np.nan
            rows.append(row)
    if n_qc:
        notes.append(f"{n_qc} file(s) left out because the quality check marked them ‘check’")
    if n_nobefore:
        notes.append(f"{n_nobefore} After-AMF file(s) without a Before-AMF file of the same sample left out")
    return pd.DataFrame(rows), notes


def describe_variables(exp):
    """Per variable: n known values, n distinct, min, max (shown units)."""
    out = {}
    for key in VAR:
        v = exp[key].dropna() if key in exp else pd.Series(dtype=float)
        out[key] = dict(n=len(v), distinct=int(v.nunique()), lo=float(v.min()) if len(v) else np.nan,
                        hi=float(v.max()) if len(v) else np.nan)
    return out


class Space:
    """The chosen inputs: key, lo, hi, log, step (shown units)."""

    def __init__(self, vars_):
        self.vars = [dict(v) for v in vars_]
        for v in self.vars:
            if v["log"] and v["lo"] <= 0:
                raise ValueError(f"{VAR[v['key']][2]}: a log scale needs a lower limit above 0")
            if not v["hi"] > v["lo"]:
                raise ValueError(f"{VAR[v['key']][2]}: the upper limit must be above the lower limit")
        self.keys = [v["key"] for v in self.vars]

    def _t(self, j, x):
        return np.log10(x) if self.vars[j]["log"] else x

    def to_unit(self, X):
        X = np.asarray(X, float).reshape(-1, len(self.vars))
        U = np.empty_like(X)
        for j, v in enumerate(self.vars):
            a, b = self._t(j, v["lo"]), self._t(j, v["hi"])
            U[:, j] = (self._t(j, np.clip(X[:, j], v["lo"] if v["log"] else -np.inf, None)) - a) / (b - a)
        return U

    def from_unit(self, U):
        U = np.asarray(U, float).reshape(-1, len(self.vars))
        X = np.empty_like(U)
        for j, v in enumerate(self.vars):
            a, b = self._t(j, v["lo"]), self._t(j, v["hi"])
            t = a + U[:, j] * (b - a)
            X[:, j] = 10 ** t if v["log"] else t
        return X

    def round(self, X):
        """Round to the step, or snap to the allowed levels (e.g. coil 200 or 500 turns)."""
        X = np.array(X, float)
        for j, v in enumerate(self.vars):
            lev = v.get("levels")
            if lev:
                lev = np.asarray(sorted(lev), float)
                X[:, j] = lev[np.abs(X[:, j][:, None] - lev[None, :]).argmin(1)]
                continue
            if v["step"] and v["step"] > 0:
                X[:, j] = np.round(X[:, j] / v["step"]) * v["step"]
            X[:, j] = np.clip(X[:, j], v["lo"], v["hi"])
        return X


class Model:
    """Gaussian process of the response on the chosen inputs."""

    def __init__(self, space, X, y, seed=0):
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        self.space, self.seed = space, seed
        self.X = np.asarray(X, float)
        self.y = np.asarray(y, float)
        self.U = space.to_unit(self.X)
        d = self.U.shape[1]
        kern = ConstantKernel(1.0, (1e-3, 1e3)) * Matern(length_scale=np.full(d, 0.5), length_scale_bounds=(0.05, 20),
                                                         nu=2.5) + WhiteKernel(0.1, (1e-3, 10))
        self.gp = GaussianProcessRegressor(kern, normalize_y=True, n_restarts_optimizer=8, random_state=seed)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.gp.fit(self.U, self.y)

    def _fixed(self, U, y):
        from sklearn.gaussian_process import GaussianProcessRegressor
        return GaussianProcessRegressor(self.gp.kernel_, normalize_y=True, optimizer=None).fit(U, y)

    def predict(self, X=None, U=None):
        U = self.space.to_unit(X) if U is None else U
        m, s = self.gp.predict(U, return_std=True)
        return m, s

    def length_scales(self):
        k = self.gp.kernel_.k1.k2
        return np.atleast_1d(k.length_scale).astype(float)

    def relevance(self):
        """Relative importance of each input: 1/length scale, normalised to sum 1."""
        r = 1 / self.length_scales()
        return r / r.sum()

    def noise_sd(self):
        """Fitted measurement noise, in response units."""
        return float(math.sqrt(self.gp.kernel_.k2.noise_level) * self.gp._y_train_std)

    def loo(self):
        """Leave-one-out predictions (kernel fixed): (mean, sd)."""
        n = len(self.y)
        m, s = np.full(n, np.nan), np.full(n, np.nan)
        for i in range(n):
            keep = np.arange(n) != i
            if keep.sum() < 2:
                continue
            g = self._fixed(self.U[keep], self.y[keep])
            a, b = g.predict(self.U[i:i + 1], return_std=True)
            m[i], s[i] = a[0], b[0]
        return m, s

    def suggest(self, n=4, direction="increase", xi=0.01, n_cand=4096, min_dist=0.1):
        """Next n experiments by expected improvement (kriging believer batch).
        Returns (DataFrame of suggestions, dict for the best predicted setting)."""
        from scipy.stats import norm, qmc
        sign = 1.0 if direction == "increase" else -1.0
        d = len(self.space.vars)
        U0 = qmc.Sobol(d, scramble=True, seed=self.seed).random(n_cand)
        Xc = self.space.round(self.space.from_unit(U0))
        Xc = np.unique(Xc, axis=0)
        Uc = self.space.to_unit(Xc)
        mu0, sd0 = self.predict(U=Uc)
        ib = int(np.argmax(sign * mu0))
        best = dict(zip(self.space.keys, Xc[ib]), mean=float(mu0[ib]), sd=float(sd0[ib]))
        U, y = self.U.copy(), self.y.copy()
        gp = self.gp
        chosen, out = np.zeros(len(Xc), bool), []
        scale = float(np.std(self.y)) or 1.0
        for k in range(min(n, len(Xc))):
            mu, sd = gp.predict(Uc, return_std=True)
            f_best = float(np.max(sign * gp.predict(U)))
            imp = sign * mu - f_best - xi * scale
            z = np.where(sd > 0, imp / np.maximum(sd, 1e-12), 0)
            ei = np.where(sd > 0, imp * norm.cdf(z) + sd * norm.pdf(z), 0)
            ei[chosen] = -np.inf
            # do not repeat a setting that was already measured (rounded) unless nothing else is left
            seen = (np.abs(Uc[:, None, :] - self.U[None, :, :]).max(-1) < 1e-9).any(1)
            if (~seen & ~chosen).any():
                ei[seen] = -np.inf
            # spread the batch: keep suggestions at least min_dist apart (0..1 scaled inputs), when possible
            if chosen.any():
                near = (np.sqrt(((Uc[:, None, :] - Uc[chosen][None, :, :]) ** 2).sum(-1)) < min_dist).any(1)
                if (np.isfinite(ei) & ~near).any():
                    ei[near] = -np.inf
            i = int(np.argmax(ei))
            chosen[i] = True
            m_i, s_i = self.predict(U=Uc[i:i + 1])
            out.append(dict(zip(self.space.keys, Xc[i]), predicted=float(m_i[0]), predicted_sd=float(s_i[0]),
                            expected_improvement=float(ei[i])))
            U = np.vstack([U, Uc[i:i + 1]])
            y = np.append(y, mu[i])
            gp = self._fixed(U, y)
        return pd.DataFrame(out), best


def starting_design(space, n, seed=0):
    """Space-filling first experiments (Latin hypercube), rounded to the steps."""
    from scipy.stats import qmc
    U = qmc.LatinHypercube(len(space.vars), seed=seed).random(n)
    X = space.round(space.from_unit(U))
    return pd.DataFrame(X, columns=space.keys)


def _g(v):
    return f"{v:g}"


def file_name_template(row, fixed):
    """Suggested file name in the app's naming scheme for one planned experiment.
    row: values of the varied inputs; fixed: values of the others (from the data)."""
    v = {**fixed, **{k: row[k] for k in row if k in VAR}}
    parts = ["mmddyy", "##"]
    if np.isfinite(v.get("collagen_mg_ml", np.nan)):
        parts.append(f"Collagen_{_g(v['collagen_mg_ml'])}mgml")
    if np.isfinite(v.get("nc_conc_nM", np.nan)):
        parts.append(f"NC_{_g(round(v['nc_conc_nM'], 4))}nM")
    pm = v.get("premixed", np.nan)
    if np.isfinite(pm) and pm >= 0.5:
        parts.append("premixed")
    elif np.isfinite(v.get("inc_min", np.nan)):
        parts.append(f"inc{_g(round(v['inc_min'], 2))}min")
    parts += ["z", "AMF"]
    if np.isfinite(v.get("amf_freq_hz", np.nan)):
        parts.append(f"{_g(round(v['amf_freq_hz'], 2))}Hz")
    if np.isfinite(v.get("amf_time_min", np.nan)):
        parts.append(f"{_g(round(v['amf_time_min'], 3))}min")
    if np.isfinite(v.get("amf_vpp_set", np.nan)):
        parts.append(f"{_g(round(v['amf_vpp_set'], 3))}Vpp")
    if np.isfinite(v.get("amf_v_measured", np.nan)):
        parts.append(f"{_g(round(v['amf_v_measured'], 3))}V")
    if np.isfinite(v.get("amf_app_set", np.nan)):
        parts.append(f"{_g(round(v['amf_app_set'], 3))}App")
    if np.isfinite(v.get("amf_coil_turns", np.nan)):
        parts.append(f"{int(round(v['amf_coil_turns']))}turns")
    if np.isfinite(v.get("amf_b_peak_mT", np.nan)):
        parts.append(f"{_g(round(v['amf_b_peak_mT'], 1))}mTpk")
    parts.append("####")
    return "_".join(parts)


def typical_values(exp, keys):
    """Most common value of each input in the data (for the inputs that are not varied)."""
    out = {}
    for k in keys:
        s = exp[k].dropna() if k in exp else pd.Series(dtype=float)
        out[k] = float(s.mode().iloc[0]) if len(s) else np.nan
    return out
