"""How magnetic nanoparticles follow an alternating field: relaxation times and phase lag.

Two ways a particle's moment can follow the field:
  Brownian: the whole particle rotates in the liquid.  tau_B = 3 eta V_h / (k_B T)
            V_h = hydrodynamic volume (core + coating + bound water); eta = local viscosity
  Néel:     the moment flips inside a fixed particle.  tau_N = tau_0 exp(K V_m / (k_B T))
            V_m = magnetic volume, K = effective anisotropy constant, tau_0 ~ 1e-9 s
The faster one wins: tau_eff = tau_B tau_N / (tau_B + tau_N).

Only Brownian rotation moves the particle (and anything it is bound to); if Néel relaxation is much
faster than Brownian, the field mostly flips the moment instead of turning the particle.
At frequency f the particle lags the field by phi = atan(2 pi f tau); the loss (heating) peaks
at f = 1 / (2 pi tau). Well below that frequency (2 pi f tau_B << 1) a free particle turns almost in
phase with the field; well above it the particle cannot follow.

xi = m B / (k_B T) (Langevin parameter, m = M_s V_m) says how strongly the field aligns the moment
against thermal motion (xi >> 1: saturated). The torque on a particle whose moment cannot flip
(Néel slow) is up to m B.

These are textbook estimates (Rosensweig 2002, J. Magn. Magn. Mater. 252, 370). K and M_s of
nanoparticles differ from bulk; a hollow nanocage has a smaller magnetic volume than a sphere of the
same outer size (give the shell thickness). A particle bound to a collagen fiber cannot rotate freely;
in a pore much larger than the particle the local viscosity is close to that of water.

Command line:  python nc_physics.py --dh 30 --core 20 --shell 4 --freq 500 --field 40
"""
import argparse
import math

KB = 1.380649e-23        # J/K
MU0 = 4e-7 * math.pi
MAGNETITE = dict(K=1.1e4, Ms=4.8e5)   # bulk magnetite: anisotropy J/m^3, saturation magnetisation A/m


def sphere_volume(d_nm):
    return math.pi / 6 * (d_nm * 1e-9) ** 3


def magnetic_volume(core_nm, shell_nm=None):
    """Magnetic volume (m^3): a full sphere of diameter core_nm, or a hollow shell of that outer diameter."""
    v = sphere_volume(core_nm)
    if shell_nm and 0 < shell_nm < core_nm / 2:
        v -= sphere_volume(core_nm - 2 * shell_nm)
    return v


def relaxation(dh_nm, core_nm, shell_nm=None, eta_mPa_s=0.89, T_K=298.15, K=MAGNETITE["K"], tau0=1e-9,
               Ms=MAGNETITE["Ms"], freq_hz=None, field_mT=None):
    """All the numbers for one particle. Returns a dict (SI units, times in s, frequencies in Hz)."""
    kT = KB * T_K
    vh = sphere_volume(dh_nm)
    vm = magnetic_volume(core_nm, shell_nm)
    tb = 3 * eta_mPa_s * 1e-3 * vh / kT
    e = K * vm / kT
    tn = tau0 * math.exp(min(e, 700))
    teff = tb * tn / (tb + tn)
    out = dict(tau_brown_s=tb, tau_neel_s=tn, tau_eff_s=teff, f_peak_brown_hz=1 / (2 * math.pi * tb),
               f_peak_eff_hz=1 / (2 * math.pi * teff), KV_over_kT=e, moment_Am2=Ms * vm,
               dominant="Brownian (particle rotates)" if tb < tn else "Néel (moment flips inside the particle)")
    if freq_hz:
        w = 2 * math.pi * freq_hz
        out.update(omega_tau_brown=w * tb, phase_lag_brown_deg=math.degrees(math.atan(w * tb)),
                   omega_tau_eff=w * teff, phase_lag_eff_deg=math.degrees(math.atan(w * teff)))
    if field_mT:
        B = field_mT * 1e-3
        out.update(langevin_xi=out["moment_Am2"] * B / kT, max_torque_Nm=out["moment_Am2"] * B,
                   max_torque_over_kT=out["moment_Am2"] * B / kT)
    return out


def describe(r):
    """Short readable summary (list of lines)."""
    lines = [f"Brownian relaxation τB = {r['tau_brown_s']:.3g} s  → loss peak at {r['f_peak_brown_hz']:.3g} Hz",
             f"Néel relaxation τN = {r['tau_neel_s']:.3g} s  (KV/kT = {r['KV_over_kT']:.1f})",
             f"Faster process: {r['dominant']}; effective τ = {r['tau_eff_s']:.3g} s "
             f"(peak {r['f_peak_eff_hz']:.3g} Hz)"]
    if "omega_tau_brown" in r:
        lines.append(f"At this frequency: ωτB = {r['omega_tau_brown']:.3g} → particle lags the field by "
                     f"{r['phase_lag_brown_deg']:.1f}° "
                     + ("(turns almost in phase)" if r["omega_tau_brown"] < 0.3 else
                        "(near the loss peak)" if r["omega_tau_brown"] < 3 else "(cannot follow the field)"))
    if "langevin_xi" in r:
        lines.append(f"Field: ξ = mB/kT = {r['langevin_xi']:.3g} "
                     + ("(moments saturated)" if r["langevin_xi"] > 5 else "(partly aligned)" if r["langevin_xi"] > 0.5
                        else "(weakly aligned)")
                     + f"; max torque {r['max_torque_Nm']:.3g} N·m = {r['max_torque_over_kT']:.3g} kT")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Relaxation times and phase lag of magnetic nanoparticles in an AMF")
    ap.add_argument("--dh", type=float, required=True, help="hydrodynamic diameter, nm (DLS)")
    ap.add_argument("--core", type=float, required=True, help="magnetic core / cage outer diameter, nm")
    ap.add_argument("--shell", type=float, default=None, help="shell thickness of a hollow cage, nm")
    ap.add_argument("--eta", type=float, default=0.89, help="viscosity, mPa·s (water at 25 °C = 0.89)")
    ap.add_argument("--T", type=float, default=298.15, help="temperature, K")
    ap.add_argument("--K", type=float, default=MAGNETITE["K"], help="anisotropy constant, J/m³")
    ap.add_argument("--Ms", type=float, default=MAGNETITE["Ms"], help="saturation magnetisation, A/m")
    ap.add_argument("--freq", type=float, default=None, help="AMF frequency, Hz")
    ap.add_argument("--field", type=float, default=None, help="field amplitude (peak), mT")
    a = ap.parse_args(argv)
    r = relaxation(a.dh, a.core, a.shell, a.eta, a.T, a.K, 1e-9, a.Ms, a.freq, a.field)
    print("\n".join(describe(r)))


if __name__ == "__main__":
    main()
