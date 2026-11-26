#!/usr/bin/env python3
"""
Collagen reflectance confocal analysis: fiber density, pore size, fiber angle.

Runs on every .nd2 (or .tif/.tiff) file in a folder: z-stacks, time-lapses (also
time-lapses of z-stacks) and single images. File names must start
with mmddyy_##_ (date_sample_), e.g. 102126_10_Collagen_..._1003.nd2.
Files whose name contains "AMF" are labelled "After AMF", all others "Before AMF"
(change with --after-pattern).
Also read from the name when present: _NC_0.5nM_ (theoretical local nanocage concentration),
_premixed_ or _inc45min_ (NCs mixed in before gelation, or incubated on the gel for that time),
_10Vpp_ (AMF Vp-p set on the instrument), _8.5V_ (measured AMF voltage),
_2App_ (AMF current Ap-p set on the power supply), _500turns_ (coil), _40mTpk_ / _28mTrms_ (measured
magnetic field, peak or rms) and _other-free-text_ (a note, saved in the "other" column).

Several folders can be given at once; each is analysed on its own (own thresholds,
figures, spreadsheets) and a combined summary.xlsx is added (see below).

Outputs (in <folder>/<mmddyy>_results; mmddyy = the date in the file names, first-last if
the folder holds several dates, "other" if no name has a date):
  sample_mmddyy_##.png        per sample: images + density, pore size, angle comparison
  mmddyy_fiber_density.png    one density graph per sample of that date (vs depth,
                              vs time, or one bar per file for single images)
  mmddyy_pore_size.png        the same for pore size
  mmddyy_pore_size_distribution.png  one pore-size distribution per sample of that date
  mmddyy_fiber_size_distribution.png one fiber-size (thickness) distribution per sample
  mmddyy_fiber_length_distribution.png one fiber-segment-length distribution per sample
  mmddyy_fiber_angle.png      one fiber-angle graph per sample of that date
  <nd2 file name>.png         per file: slice, projection, masks, three graphs
  mmddyy_summary.xlsx         spreadsheet: "per slice" (every analysed image of every file:
                              all metrics, all settings, file and per-image metadata),
                              "per file" (mean and SD per file), "settings", "quality" (one row
                              per file: QC status and flags, see ml/qc.py)
  mmddyy_tiles.csv            per-tile table: the metrics of every --tile-um tile of every analysed
                              image, with its position and a dark/blurred flag (not with --no-tiles)
  <file name>/                one folder per file (default; not with --averages-only):
    slices/NAME_z###.png      a figure of every analysed image
    NAME.png, NAME_slice_comparison.png, NAME_slice_distributions.png, NAME_summary.xlsx
  With --averages-only the per-file figures <file name>.png are written to mmddyy_results/
  and there are no per-file folders.

Several folders (python collagen_reflectance_analysis.py FOLDER1 FOLDER2 ...):
  every folder gets its own <mmddyy>_results/ as above, plus one combined workbook
  summary.xlsx in the folders' common parent folder (or --out): sheets "per slice" and
  "per file" (all rows of every mmddyy_summary, with folder and results_folder columns),
  "settings" (the settings of each folder/date), "folders" (overview) and "quality".

Single-file mode (--file NAME): only that file, written to mmddyy_results/NAME/
(with several folders: in every folder that contains it):
  slices/NAME_z###.png (or _t####[_z###].png)  one figure per analysed image: image, fiber
                              mask, orientation map, fiber segments, pore-size and fiber-size
                              maps, all distributions and the numbers of that image
  NAME.png                    the per-file figure
  NAME_slice_comparison.png   every metric vs depth / time (z-stacks, time-lapses)
  NAME_slice_distributions.png  the distributions of every image, colored by depth / time
  NAME_summary.xlsx

Pipeline:
  1. exclude image edge + reflection hotspot (auto-detected, or given)
  2. flatten: slice / gaussian(slice, sigma_bg)
  3. ridge filter (Sato) to enhance fibers
  4. threshold: Otsu per file (default), one pooled Otsu for all files
     (--threshold-mode shared), or a fixed value (--threshold)
  5. per slice: area fraction, length density (chain-code length of the pruned centerline),
     pore size (local thickness, edge-corrected; plus distance-map maxima),
     fiber size (local thickness of the fiber mask = apparent fiber thickness),
     fiber length (skeleton split into segments between ends / branch points),
     structure-tensor alignment, tile heterogeneity, mean reflectance, quality-check metrics
     (sharpness, edge/centre brightness, saturation, dark or blurred tiles)
  6. per file: quality check (rules, plus a trained model with --qc-model) -> qc_status, qc_flags
  All filter sizes are given in µm and converted per file with its pixel size.
  Slices are analysed in parallel (--workers).

File types (detected per file):
  z-stack      every --zstep-th slice; graphs vs depth
  time-lapse   every --tstep-th time point (and every --zstep-th slice if each time point
               is a z-stack, averaged per time point); graphs vs time
  single image the one image; per-file figure shows the distributions instead of
               depth graphs, and comparisons between files use one bar per file
  --stack-axis z|t overrides the detection for 3-D files whose third axis is unlabelled.

Install:  pip install nd2 tifffile scikit-image scipy numpy pandas matplotlib openpyxl
Usage:    python collagen_reflectance_analysis.py /path/to/folder
          python collagen_reflectance_analysis.py /path/to/folder1 /path/to/folder2
          python collagen_reflectance_analysis.py /path/to/folder --threshold 0.18
Angles:   degrees counter-clockwise from the image x-axis, range -90..90
          (same convention as OrientationJ).
"""
import argparse, os, sys, glob, math, re, time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from skimage import filters, morphology, feature
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.patches import Patch

try:
    from ml import qc as QC
except ImportError:                      # collagen_core.py copied on its own, without the ml/ folder
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ml import qc as QC
from ml import fibermodel as FM
import cloudfiles as CF

ANGLE_BINS = 36                      # 5-degree bins over -90..90 deg
BEFORE_COLOR, AFTER_COLOR = "#3b6fb6", "#d9622b"
EXTRA_COLORS = ["#2a9d5c", "#8e44ad", "#c0392b", "#7f8c8d"]
LABEL_COLS = ["date", "sample", "file", "collagen_mg_ml", "nc_conc_nM", "nc_mixing", "nc_incubation_time",
              "nc_incubation_time_s", "other", "condition", "amf_freq_hz", "amf_time", "amf_time_s",
              "amf_vpp_set", "amf_v_measured", "amf_app_set", "amf_coil_turns", "amf_b_mT", "amf_b_type",
              "amf_b_peak_mT", "amf_ampere_turns_pp",
              "series", "t_index", "time_s", "z_index", "z_um", "threshold", "qc_status", "qc_flags"]
SERIES_NAME = {"z": "z-stack", "t": "time-lapse", "single": "single image"}
LT_BIN_UM, LT_MAX_UM = 0.25, 60.0    # pore-size histogram: 0.25 µm bins up to 60 µm (+ overflow)
LT_EDGES = np.r_[np.arange(0, LT_MAX_UM + 1e-9, LT_BIN_UM), np.inf]
FIB_BIN_UM, FIB_MAX_UM = 0.005, 5.0  # fiber-size histogram: fine bins (values are pixel-quantised)
FIB_EDGES = np.r_[np.arange(0, FIB_MAX_UM + 1e-9, FIB_BIN_UM), np.inf]
SEG_BIN_UM, SEG_MAX_UM = 0.05, 40.0  # fiber-segment-length histogram
SEG_EDGES = np.r_[np.arange(0, SEG_MAX_UM + 1e-9, SEG_BIN_UM), np.inf]


# ----------------------------------------------------------------------------- progress bar
class Progress:
    """Progress bar printed as a new line at every update:
    phase name, bar, %, count, elapsed time, time left, current item."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.active = False

    def start(self, phase, total):
        self.phase, self.total, self.n = phase, max(int(total), 1), 0
        self.item, self.t0 = "", time.time()
        self.active = self.enabled
        self._draw()

    def step(self, k=1, item=None):
        self.n = min(self.n + k, self.total)
        if item is not None:
            self.item = item
        self._draw()

    def set_item(self, item):
        self.item = item            # shown with the next update; no new line by itself

    def finish(self):
        if self.active and self.n < self.total:     # print 100% only if not printed yet
            self.n = self.total
            self._draw()
        self.active = False

    @staticmethod
    def _t(sec):
        sec = int(sec)
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"

    def _line(self):
        frac = self.n / self.total
        width = 28
        bar = "█" * int(width * frac) + "░" * (width - int(width * frac))
        el = time.time() - self.t0
        left = f"~{self._t(el / self.n * (self.total - self.n))} left" if 0 < self.n < self.total else ""
        item = self.item if len(self.item) <= 45 else "…" + self.item[-44:]
        return f"{self.phase:<22} |{bar}| {100 * frac:5.1f}%  {self.n}/{self.total}  {self._t(el)} {left}  {item}"

    def _draw(self):
        if FIG["gui"]:
            emit("P", self.phase, self.n, self.total, round(time.time() - self.t0, 1), self.item)
        elif self.active:
            sys.stdout.write(self._line() + "\n"); sys.stdout.flush()

    def write(self, *args):
        sys.stdout.write(" ".join(str(a) for a in args) + "\n"); sys.stdout.flush()


PROGRESS = Progress()

# ---- output options (set from the command line in main)
FIGURE_KEYS = {
    "file": "per-file figure <file>.png",
    "slices": "a figure of every analysed image (slices/)",
    "slice_comparison": "<file>_slice_comparison.png and <file>_slice_distributions.png",
    "sample": "sample_mmddyy_##.png (before vs after, per sample)",
    "density": "mmddyy_fiber_density.png",
    "pore_size": "mmddyy_pore_size.png",
    "angle": "mmddyy_fiber_angle.png",
    "pore_dist": "mmddyy_pore_size_distribution.png",
    "fiber_dist": "mmddyy_fiber_size_distribution.png",
    "length_dist": "mmddyy_fiber_length_distribution.png",
}
FIG = dict(keys=set(FIGURE_KEYS), fmt="png", dpi=None, gui=False)
# values entered by hand in the app (--metadata): {"fields": [{key, label, unit, type}], "files": {name: {...}}}
METADATA = {"fields": [], "files": {}}
# files in iCloud Drive: how long to wait for a download (s); set from --cloud-wait-min
CLOUD = dict(timeout=900)
MANUAL_KEYS = {"condition": "condition", "amf_hz": "AMF frequency (Hz)", "amf_s": "AMF time (s)",
               "amf_vpp": "AMF Vp-p set (V)", "amf_v": "AMF measured voltage (V)",
               "amf_app": "AMF Ap-p set (A)", "coil_turns": "coil turns", "amf_b": "magnetic field (mT)",
               "amf_b_type": "field measured as", "collagen_mgml": "collagen (mg/mL)",
               "nc_nM": "NC concentration (nM)", "mixing": "NC mixing", "inc_s": "NC incubation time (s)",
               "other": "note"}


def user_fields():
    return [f["key"] for f in METADATA.get("fields", [])]


def manual(name):
    return METADATA.get("files", {}).get(name, {})


def fig_on(key):
    return key in FIG["keys"]


def savefig(fig, path, dpi):
    """Save a figure in the chosen format (--fig-format) and resolution (--dpi)."""
    path = os.path.splitext(path)[0] + "." + FIG["fmt"]
    fig.savefig(path, dpi=FIG["dpi"] or dpi, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    emit("OUT", "figure", path)
    return path


def emit(kind, *fields):
    """Machine-readable line for the GUI (only with --gui): @@KIND|field|field..."""
    if FIG["gui"]:
        sys.stdout.write("@@" + "|".join([kind] + [str(f).replace("|", "/").replace("\n", " ") for f in fields]) + "\n")
        sys.stdout.flush()


def log(*args):
    """print() that keeps the progress bar intact."""
    PROGRESS.write(*args)


# ----------------------------------------------------------------------------- I/O
def _to_tzyx(arr, axes, name):
    """Reorder an array with axis letters `axes` (e.g. 'TZCYX') to (T, Z, Y, X).
    Positions (P/M), channels (C) and RGB samples (S) keep index 0; other unknown axes
    (Q, I) are treated as Z."""
    axes = list(axes)
    for ax in list(axes):
        if ax in ("P", "M", "C", "S"):
            if arr.shape[axes.index(ax)] > 1:
                log(f"  note: {ax} axis present ({arr.shape[axes.index(ax)]}), using index 0")
            arr = np.take(arr, 0, axis=axes.index(ax)); axes.remove(ax)
    axes = ["Z" if a in ("Q", "I") else a for a in axes]
    if axes.count("Z") > 1:
        sys.exit(f"{name}: cannot interpret axes {''.join(axes)}")
    for ax in ("T", "Z"):
        if ax not in axes:
            arr = arr[None]; axes.insert(0, ax)
    if set(axes) != {"T", "Z", "Y", "X"} or len(axes) != 4:
        sys.exit(f"{name}: cannot interpret axes {''.join(axes)} (shape {arr.shape})")
    return np.transpose(arr, [axes.index(a) for a in "TZYX"])


def _nd2_times(f, nt):
    """Time (s) of each time point of an ND2 file, from frame metadata, events or loop period."""
    try:
        times = np.full(nt, np.nan)
        for seq, li in enumerate(f.loop_indices):
            if li.get("Z", 0) == 0 and li.get("P", 0) == 0 and "T" in li and np.isnan(times[li["T"]]):
                times[li["T"]] = f.frame_metadata(seq).channels[0].time.relativeTimeMs / 1000
        if np.isfinite(times).all():
            return times - times[0]
    except Exception:
        pass
    try:
        times = {}
        for e in f.events():
            t, z = e.get("T Index"), e.get("Z Index", 0)
            if t is not None and z == 0 and "Time [s]" in e:
                times.setdefault(int(t), float(e["Time [s]"]))
        if len(times) == nt:
            t = np.array([times[i] for i in range(nt)])
            return t - t[0]
    except Exception:
        pass
    try:
        for lp in f.experiment:
            if "time" in lp.type.lower():
                per = getattr(lp.parameters, "periodMs", None) or getattr(lp.parameters, "periodDiff", None)
                per = getattr(per, "avg", per)
                if per:
                    return np.arange(nt) * float(per) / 1000
    except Exception:
        pass
    return None


def load_stack(path, pixel_size=None, z_step=None, stack_axis="auto", t_interval=None):
    """Read a file as (T, Z, Y, X) float32.
    Returns (array, px_um, z_um, times_s or None)."""
    name = os.path.basename(path)
    CF.ensure_local(path, CLOUD["timeout"], log=log)     # iCloud placeholder -> download and wait
    times = None
    if path.lower().endswith(".nd2"):
        import nd2
        with nd2.ND2File(path) as f:
            arr = f.asarray()
            vs = f.voxel_size()
            px, dz = vs.x, vs.z
            axes = "".join(k[0] for k in f.sizes.keys())
            arr = _to_tzyx(arr, axes, name)
            if arr.shape[0] > 1:
                times = _nd2_times(f, arr.shape[0])
    else:
        import tifffile
        with tifffile.TiffFile(path) as tf:
            ser = tf.series[0]
            arr = ser.asarray()
            axes = ser.axes
            ij = tf.imagej_metadata or {}
            px = pixel_size
            if px is None:                      # ImageJ TIFFs store the pixel size as resolution
                try:
                    num, den = tf.pages[0].tags["XResolution"].value
                    if str(ij.get("unit", "")).lower() in ("micron", "um", "µm") and num:
                        px = den / num
                except Exception:
                    pass
            dz = z_step if z_step is not None else ij.get("spacing")
            fint = t_interval if t_interval is not None else ij.get("finterval")
        arr = _to_tzyx(arr, axes, name)
        if arr.shape[0] > 1 and fint:
            times = np.arange(arr.shape[0]) * float(fint)
        if px is None:
            sys.exit(f"{name}: pixel size not found in the TIFF; give it with --pixel-size (µm)")
    # a 3-D file whose third axis is really the other kind
    nt, nz = arr.shape[:2]
    if stack_axis == "t" and nt == 1 and nz > 1:
        arr = np.swapaxes(arr, 0, 1)
        times = np.arange(nz) * float(t_interval) if t_interval else None
    elif stack_axis == "z" and nz == 1 and nt > 1:
        arr = np.swapaxes(arr, 0, 1); times = None
    if not (px and px > 0):
        sys.exit(f"{name}: pixel size missing or invalid ({px})")
    return arr.astype(np.float32), float(px), float(dz or 0), times


def z_positions(smeta, nt, nz, dz, name):
    """Height (µm) of every z-slice above the lowest slice: slice index x z-step, counted from
    the lowest slice.  The stage coordinates recorded with each image (ND2) are used only to see
    in which direction the stack was acquired, so 'bottom -> top' stays true for stacks acquired
    from the top down (the coordinates themselves are only precise to about 0.025 µm).
    Returns (array [z], source text)."""
    up = np.arange(nz) * dz
    if nz < 2:
        return up, "single plane"
    try:
        zc = np.array([smeta[(0, z)]["slice_z_coord_um"] for z in range(nz)], float)
        assert np.isfinite(zc).all()
    except (KeyError, TypeError, ValueError, AssertionError):
        return up, "slice index x z-step (no stage coordinates in the file; first slice assumed lowest)"
    if zc[-1] < zc[0]:
        log(f"  note: {name}: z-stack was acquired from the top down (stage coordinates); "
            f"z is counted from the last slice so graphs still run bottom -> top")
        return up[::-1].copy(), "slice index x z-step, counted from the last slice (acquired top down)"
    return up, "slice index x z-step (first slice is the lowest, from the stage coordinates)"


def otsu_threshold(values, nbins=16384):
    """Otsu threshold (maximum between-class variance) of ridge values on a fine histogram.
    The usual 256 bins between the smallest and largest value make the threshold jump by
    1-2 % depending on the brightest pixel; 16384 bins resolve it to better than 0.1 %."""
    v = np.asarray(values, np.float32)
    lo, hi = float(v.min()), float(v.max())
    if not hi > lo:
        return lo
    h, edges = np.histogram(v, bins=nbins, range=(lo, hi))
    h = h.astype(float)
    c = (edges[:-1] + edges[1:]) / 2
    w0, w1 = np.cumsum(h), np.cumsum(h[::-1])[::-1]
    m0 = np.cumsum(h * c) / np.maximum(w0, 1)
    m1 = (np.cumsum((h * c)[::-1]) / np.maximum(np.cumsum(h[::-1]), 1))[::-1]
    var = w0[:-1] * w1[1:] * (m0[:-1] - m1[1:]) ** 2
    return float(c[np.argmax(var)])


def series_kind(shape):
    nt, nz = shape[:2]
    return "t" if nt > 1 else ("z" if nz > 1 else "single")


def frames_to_analyse(shape, zstep, tstep):
    """(t, z) indices of the images that are analysed."""
    nt, nz = shape[:2]
    zs = list(range(0, nz, zstep)) if nz > 1 else [0]
    ts = list(range(0, nt, tstep)) if nt > 1 else [0]
    return [(t, z) for t in ts for z in zs]


# ----------------------------------------------------------------------------- metadata
def _key(s):
    """Column-safe name: 'Laser Power; 4.channel [%]' -> 'laser_power_4_channel_pct'."""
    s = str(s).replace("%", "pct").replace("µm", "um").replace("µ", "u")
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()


def _is_scalar(v):
    return isinstance(v, (str, bool, int, float, np.integer, np.floating))


def read_nd2_metadata(path):
    """All acquisition metadata of an .nd2 file.
    Returns (file_meta, slice_meta): file_meta = {'meta_...': value} (same for every slice),
    slice_meta = {z_index: {'slice_...': value}} (recorded per slice: time, stage, laser, HV...)."""
    import nd2
    fm, sm = {}, {}
    CF.ensure_local(path, CLOUD["timeout"], log=log)
    with nd2.ND2File(path) as f:
        ti = f.text_info or {}
        fm["acq_datetime"] = ti.get("date")
        fm["optics"] = ti.get("optics")
        try:   # header lines of the description: camera name, NA, refractive index, modality...
            head = ti.get("description", "").split("Camera Settings")[0]
            for line in head.splitlines():
                m = re.match(r"^\s*([^:{}]+?):\s*(\S.*)$", line)
                if m and m.group(1).strip() != "Metadata":
                    fm["desc_" + _key(m.group(1))] = m.group(2).strip()
        except Exception as e:
            log("  note: description not parsed:", e)
        try:   # scanner/detector settings: {Key}: value, channel lines prefixed CHn / Stimn
            prefix = ""
            for line in ti.get("capturing", "").splitlines():
                m = re.match(r"^\s*(CH\d+|Stim\d+)\s", line)
                if m:
                    prefix = m.group(1) + "_"
                elif not line.startswith("\t"):
                    prefix = ""
                for k, v in re.findall(r"\{([^}]*)\}:\s*([^\t\r\n]*)", line):
                    fm["acq_" + _key(prefix + k)] = v.strip().rstrip(";")
        except Exception as e:
            log("  note: capturing settings not parsed:", e)
        try:   # microscope body settings (Ti stand, light path, PFS)
            desc = ti.get("description", "")
            if "Microscope Settings:" in desc:
                ms = desc.split("Microscope Settings:")[1].split("Z Stack Loop")[0]
                for line in ms.replace("Microscope: ", "\nMicroscope: ").splitlines():
                    if ":" in line:
                        k, v = line.rsplit(":", 1)
                        fm["scope_" + _key(k)] = v.strip()
        except Exception as e:
            log("  note: microscope settings not parsed:", e)
        try:   # structured metadata (first channel)
            c = f.metadata.channels[0]
            ch, mi, vo = c.channel, c.microscope, c.volume
            fm.update(channel_name=ch.name, excitation_nm=ch.excitationLambdaNm,
                      emission_nm=ch.emissionLambdaNm, n_channels=f.metadata.contents.channelCount,
                      objective=mi.objectiveName, objective_mag=mi.objectiveMagnification,
                      objective_na=mi.objectiveNumericalAperture, immersion_ri=mi.immersionRefractiveIndex,
                      zoom_total=mi.zoomMagnification, pinhole_um=mi.pinholeDiameterUm,
                      modality=";".join(mi.modalityFlags or []),
                      pixel_x_um=vo.axesCalibration[0], pixel_y_um=vo.axesCalibration[1],
                      z_step_um=vo.axesCalibration[2], size_x_px=vo.voxelCount[0],
                      size_y_px=vo.voxelCount[1], n_slices=vo.voxelCount[2],
                      bits_stored=vo.bitsPerComponentInMemory, bits_significant=vo.bitsPerComponentSignificant)
        except Exception as e:
            log("  note: structured metadata not read:", e)
        try:   # experiment loops (z-stack definition, time lapse, multipoint)
            for lp in f.experiment:
                t = _key(lp.type)
                fm[f"loop_{t}_count"] = lp.count
                for k, v in vars(lp.parameters).items():
                    if _is_scalar(v):
                        fm[f"loop_{t}_{_key(k)}"] = v
        except Exception as e:
            log("  note: experiment loops not read:", e)
        try:   # software version, scan area, confocal head, camera (display/LUT state skipped)
            cd = f.custom_data
            blocks = {"app": cd.get("AppInfo_V1_0", {}), "scanarea": cd.get("ScanAreaDescV1_0", {}),
                      "confocal": cd.get("ConfocalA1V1_0", {}),
                      "camera": cd.get("GrabberCameraSettingsV1_0", {}).get("GrabberCameraSettings", {})}
            for b, dct in blocks.items():
                for k, v in (dct or {}).items():
                    if _is_scalar(v) and v != "":
                        fm[f"{b}_{_key(k)}"] = v
        except Exception as e:
            log("  note: custom data not read:", e)
        try:   # per-image records, keyed by (time index, z index)
            try:
                seq_key = {seq: (li.get("T", 0), li.get("Z", 0)) for seq, li in enumerate(f.loop_indices)
                           if li.get("P", 0) == 0}
            except Exception:
                seq_key = {}
            for e in f.events():
                seq = e.get("Index")
                key = seq_key.get(seq) if seq is not None else None
                if key is None:
                    key = (int(e.get("T Index", 0)), int(e.get("Z Index", 0 if "T Index" in e else (seq or 0))))
                sm[key] = {"slice_" + _key(k): (v.item() if hasattr(v, "item") else v)
                           for k, v in e.items() if k not in ("Index", "Z Index", "T Index")}
        except Exception as e:
            log("  note: per-slice records not read:", e)
    fm = {"meta_" + k: (v.item() if hasattr(v, "item") else v) for k, v in fm.items()}
    return fm, sm


SETTING_KEYS = ("laser_power", "laser_wavelength", "pmt_hv", "pmt_offset", "pinhole", "zoom",
                "objective", "numerical_aperture", "immersion", "line_average", "scan_speed",
                "dichroic", "detector", "pixel_x_um", "pixel_y_um", "z_step_um", "bits_",
                "size_x_px", "size_y_px", "excitation", "emission", "head_type")


def check_settings(names, data, what):
    """Warn if imaging settings differ between files that are compared."""
    keys = sorted({k for n in names for k in data[n]["meta"]
                   if k.startswith("meta_") and any(s in k for s in SETTING_KEYS)})
    diff = [k for k in keys if len({str(data[n]["meta"].get(k)) for n in names}) > 1]
    if diff:
        log(f"  WARNING: imaging settings differ {what}: " +
              "; ".join(f"{k[5:]}=" + "/".join(str(data[n]['meta'].get(k)) for n in names) for k in diff))
    return diff


# ----------------------------------------------------------------------------- names
def parse_name(name, after_pattern):
    """Return (date 'mmddyy', sample 'mmddyy_##', condition)."""
    m = re.match(r"^(\d{6})_(\d+)_", name)
    if m:
        date, sample = m.group(1), f"{m.group(1)}_{m.group(2)}"
    else:
        log(f"  warning: '{name}' does not start with mmddyy_##_; grouped as 'other'")
        date, sample = "other", name
    cond = "After AMF" if re.search(after_pattern, name_core(name), re.I) else "Before AMF"
    return date, sample, cond


def parse_amf(name):
    """AMF frequency (Hz) and exposure time (s) from a file name, e.g. '..._AMF_500Hz_3min_...'.
    Accepted: 500Hz, 1.5kHz | 3min, 1.5min, 3min30s, 3m30s, 3-30min, 3_30min, 90s, 90sec.
    The NC incubation tag (inc45min) and the _other-..._ note are ignored.
    Returns (freq_hz or None, time_s or None)."""
    name = INC_RE.sub("_", name_core(name))
    freq = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kM]?)Hz", name, re.I)
    if m:
        freq = float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]
    t = None
    for pat, fn in [
            (r"(\d+)\s*min\s*(\d+)\s*s(?:ec)?(?![a-z])", lambda a, b: 60 * int(a) + int(b)),  # 3min30s
            (r"(\d+)m(\d+)s(?![a-z])", lambda a, b: 60 * int(a) + int(b)),                     # 3m30s
            (r"(\d+)[-_:](\d{2})\s*min", lambda a, b: 60 * int(a) + int(b)),                    # 3-30min
            (r"(\d+(?:\.\d+)?)\s*min", lambda a: round(60 * float(a))),                         # 3min, 1.5min
            (r"(?<![a-z\d])(\d+)\s*s(?:ec)?(?=$|[_\-\s.])", lambda a: int(a))]:                 # 90s
        m = re.search(pat, name, re.I)
        if m:
            t = fn(*m.groups()); break
    return freq, t


def fmt_freq(hz):
    return f"{hz:g} Hz" if hz is not None else None


def fmt_time(sec):
    return f"{sec // 60}:{sec % 60:02d} min" if sec is not None else None


_VNUM = r"(\d+(?:[.p]\d+)?)\s*(m?)"
VPP_RE = re.compile(rf"(?:(?<=[_\-])|^){_VNUM}V(?:pp|p-p|ptp|pk-pk|pkpk)(?=$|[_.]|-(?!p))", re.I)
VMEAS_RE = re.compile(rf"(?:(?<=[_\-])|^){_VNUM}V(?=$|[_.\-])", re.I)


def parse_amf_voltage(name):
    """AMF voltages from a file name:
      Vp-p set on the instrument: _10Vpp_, _10Vp-p_, _10Vptp_, _10Vpk-pk_, _2p5Vpp_, _500mVpp_
      measured voltage:           _8.5V_, _8p5V_, _850mV_
    (case-insensitive; the _other-..._ note is ignored). Returns (vpp_set, v_measured) in V or None."""
    core = name_core(name)
    def val(m):
        return float(f"{_num(m.group(1)) * (1e-3 if m.group(2) else 1):.10g}") if m else None
    vpp = val(VPP_RE.search(core))
    vm = val(VMEAS_RE.search(VPP_RE.sub("_", core)))
    return vpp, vm


def fmt_volt(v, unit="V"):
    return f"{v:g} {unit}" if v is not None else None


_PP = r"(?:pp|p-p|ptp|pk-pk|pkpk)"
APP_RE = re.compile(rf"(?:(?<=[_\-])|^){_VNUM}A{_PP}(?=$|[_.]|-(?!p))", re.I)
COIL_RE = re.compile(r"(?:(?<=[_\-])|^)(?:(\d+)\s*(?:turns?|trn)|coil[_\-]?(\d+)(?:\s*(?:turns?|trn))?)(?=$|[_.\-])",
                     re.I)
B_RE = re.compile(r"(?:(?<=[_\-])|^)(\d+(?:[.p]\d+)?)\s*([mµu]?)T(?:[_\-]?((?i:pk-pk|pk|peak|amp|rms|pp|p-p|ptp)))?"
                  r"(?=$|[_.]|-(?!p))")
B_TYPES = {"pk": "peak", "peak": "peak", "amp": "peak", "rms": "rms", "pp": "peak-to-peak", "p-p": "peak-to-peak",
           "pk-pk": "peak-to-peak", "ptp": "peak-to-peak"}


def parse_amf_coil(name):
    """Coil and field from a file name:
      Ap-p set on the power supply: _2App_, _2Ap-p_, _1p5App_, _500mApp_      -> amf_app (A)
      coil turns:                   _500turns_, _200turn_, _coil500_, _coil-200_ -> coil_turns
      measured magnetic field:      _40mTpk_ / _40mTpeak_ (peak), _28mTrms_ (rms), _80mTpp_ (peak-to-peak),
                                    _40mT_ (type not given), also µT/uT and T     -> amf_b (mT), amf_b_type
    (case-sensitive T, so it is not confused with other parts; the _other-..._ note is ignored)."""
    core = name_core(name)
    m = APP_RE.search(core)
    app = float(f"{_num(m.group(1)) * (1e-3 if m.group(2) else 1):.10g}") if m else None
    m = COIL_RE.search(core)
    turns = int(m.group(1) or m.group(2)) if m else None
    b = btype = None
    m = B_RE.search(core)
    if m:
        b = float(f"{_num(m.group(1)) * {'': 1e3, 'm': 1.0, 'µ': 1e-3, 'u': 1e-3}[m.group(2)]:.10g}")
        btype = B_TYPES.get((m.group(3) or "").lower(), "")
    return dict(amf_app=app, coil_turns=turns, amf_b=b, amf_b_type=btype or None)


def b_peak(b, btype):
    """Field amplitude (peak, mT) from a measured value: rms x sqrt(2), peak-to-peak / 2 (sinusoidal field)."""
    if b is None:
        return None
    f = {"peak": 1.0, "rms": math.sqrt(2), "peak-to-peak": 0.5}.get(btype or "")
    return round(b * f, 6) if f else None


def amf_text(d):
    """'500 Hz, 3:00 min' (whatever parts were found in the file name)."""
    volt = ", ".join(x for x in (fmt_volt(d.get("amf_vpp"), "Vp-p set"), fmt_volt(d.get("amf_v"), "V measured"),
                                 fmt_volt(d.get("amf_app"), "Ap-p set"),
                                 f"{d['coil_turns']} turns" if d.get("coil_turns") else None,
                                 f"{d['amf_b']:g} mT {d.get('amf_b_type') or ''}".strip() if d.get("amf_b") is not None
                                 else None) if x)
    return ", ".join(x for x in (fmt_freq(d.get("amf_hz")), fmt_time(d.get("amf_s")),
                                 f"{volt}" if volt else None) if x)


def condition_text(d):
    """'Before AMF' or 'After AMF, 500 Hz, 3:00 min'."""
    t = amf_text(d) if d["condition"] == "After AMF" else ""
    return f"{d['condition']}, {t}" if t else d["condition"]


def file_label(d):
    """Legend/title label, e.g. 'Before AMF (1003)' or 'After AMF, 500 Hz, 3:00 min (1004)'."""
    return f"{condition_text(d)} ({d['name'].split('_')[-1]})"


def sample_amf(names, data):
    """AMF settings of a sample's After-AMF files, e.g. '500 Hz, 3:00 min' (joined if several)."""
    t = [amf_text(data[n]) for n in names if data[n]["condition"] == "After AMF" and amf_text(data[n])]
    return "; ".join(dict.fromkeys(t))


# ---- nanocage (NC) concentration, mixing condition, free-text note
OTHER_RE = re.compile(r"(?:(?<=_)|^)other(?:[-=:]([^_]+)|_([^_]+))", re.I)
_TIME_UNITS = r"(?:h|hr|hrs|hour|hours|min|mins|m|s|sec|secs)"
_DURATION = rf"(?:\d+(?:[.p]\d+)?\s*{_TIME_UNITS})+"
INC_RE = re.compile(rf"(?:(?<=[_\-])|^)(?:incubated|incubation|incub|inc)(?:[_\-]?({_DURATION}))?(?=$|[_\-.])", re.I)
PREMIX_RE = re.compile(r"(?:(?<=[_\-])|^)pre[_\-]?mix(?:ed)?(?=$|[_\-.])", re.I)
NC_RE = re.compile(r"(?:(?<=[_\-])|^)NCs?[_\-]?(\d+(?:[.p]\d+)?(?:e[-+]?\d+)?)\s*([fpnuµm]?)M(?=$|[_\-.])", re.I)
_MOLAR = {"": 1e9, "m": 1e6, "u": 1e3, "µ": 1e3, "n": 1.0, "p": 1e-3, "f": 1e-6}


def name_core(name):
    """The file name without the _other-..._ note, so text in the note is not parsed."""
    return OTHER_RE.sub("", name)


def parse_other(name):
    """Free-text note after an 'other' marker: _other-dried-edges_ or _other_driededges_ or
    _other=..._ / _other:..._ (the note runs to the next underscore). Several notes are joined
    with '; '. Returns a string or None."""
    notes = [(a or b) for a, b in OTHER_RE.findall(name)]
    return "; ".join(notes) or None


def _num(txt):
    return float(txt.replace("p", ".").replace("P", "."))


def parse_duration(txt):
    """'45min', '1h30min', '1.5h', '90s', '2p5h' -> seconds (int)."""
    tot = 0.0
    for v, u in re.findall(rf"(\d+(?:[.p]\d+)?)\s*({_TIME_UNITS})", txt, re.I):
        u = u.lower()
        tot += _num(v) * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
    return int(round(tot))


def parse_nc(name):
    """Theoretical local nanocage concentration (nM) from a file name: _NC_0.5nM_, _NC0.5nM_,
    _NCs_500pM_, _NC_0p5nM_, _NC_2uM_ (fM, pM, nM, uM/µM, mM, M; case-insensitive).
    Returns a float (nM) or None."""
    m = NC_RE.search(name_core(name))
    if not m:
        return None
    return float(f"{_num(m.group(1)) * _MOLAR[m.group(2).lower()]:.10g}")


def parse_mixing(name):
    """How the NCs were combined with the collagen:
      'premixed'   _premixed_ / _premix_ / _pre-mixed_: NCs mixed into the collagen before gelation
      'incubated'  _inc45min_ / _inc_45min_ / _incubated_45min_ / _incub1h30min_: NCs added to the
                   polymerized gel for that time (min, h, s; 1p5h = 1.5 h)
    Returns (mixing or None, incubation time in s or None)."""
    core = name_core(name)
    pre, inc = PREMIX_RE.search(core), INC_RE.search(core)
    if pre and inc:
        log(f"  WARNING: '{name}' contains both 'premixed' and 'incubated'; recorded as 'premixed + incubated'")
        return "premixed + incubated", parse_duration(inc.group(1)) if inc.group(1) else None
    if pre:
        return "premixed", None
    if inc:
        return "incubated", parse_duration(inc.group(1)) if inc.group(1) else None
    return None, None


def fmt_conc(nm):
    """Concentration in nM as text: 0.5 -> '0.5 nM', 0.0005 -> '0.5 pM', 1500 -> '1.5 µM'
    (nM is used from 0.001 to 999.9)."""
    if nm is None:
        return None
    a = abs(nm)
    if a == 0:
        return "0 nM"
    unit, f = (("mM", 1e6) if a >= 1e6 else ("µM", 1e3) if a >= 1e3 else ("nM", 1.0) if a >= 1e-3
               else ("pM", 1e-3) if a >= 1e-6 else ("fM", 1e-6))
    return f"{nm / f:.4g} {unit}"


def fmt_duration(sec):
    """2700 -> '45 min', 5400 -> '1 h 30 min', 90 -> '1 min 30 s'."""
    if sec is None:
        return None
    h, rem = divmod(int(sec), 3600)
    m, s_ = divmod(rem, 60)
    return " ".join(x for x in (f"{h} h" if h else "", f"{m} min" if m else "", f"{s_} s" if s_ else "") if x) or "0 s"


def mixing_text(d):
    """'premixed', 'incubated 45 min' or ''."""
    mx = d.get("mixing")
    if not mx:
        return ""
    t = fmt_duration(d.get("inc_s"))
    return f"{mx} {t}" if (t and "incubated" in mx) else mx


def nc_text(d):
    """'NC 0.5 nM, premixed', 'NC incubated 45 min' or '' (whatever the file name gives)."""
    parts = [x for x in (fmt_conc(d.get("nc_nM")), mixing_text(d)) if x]
    return ("NC " + ", ".join(parts)) if parts else ""


def sample_nc(names, data):
    return " / ".join(dict.fromkeys(t for t in (nc_text(data[n]) for n in names) if t))


def parse_collagen(name):
    """Collagen concentration (mg/mL) from a file name, e.g. '..._Collagen_3mgml_...'.
    Accepted: Collagen_3mgml, Collagen_2.5mgml, Collagen_2p5mgml, Collagen_2-5mgml,
    Collagen_3mg_ml, Collagen_3mg-ml (case-insensitive). Returns a float or None."""
    m = re.search(r"collagen[_\-\s]*(\d+)(?:[.p\-](\d+))?\s*mg[_\-/]?ml", name_core(name), re.I)
    if not m:
        return None
    return float(m.group(1) + ("." + m.group(2) if m.group(2) else ""))


def collagen_text(d):
    """'Collagen 3 mg/mL' or '' if not in the file name."""
    c = d.get("collagen_mgml")
    return f"Collagen {c:g} mg/mL" if c is not None else ""


def sample_collagen(names, data):
    """Collagen concentration(s) of a sample, e.g. 'Collagen 3 mg/mL' (joined if they differ)."""
    return " / ".join(dict.fromkeys(t for t in (collagen_text(data[n]) for n in names) if t))


def order_files(names, data):
    """Before AMF first, then After AMF, then by name."""
    return sorted(names, key=lambda n: (data[n]["condition"] != "Before AMF", n))


def colors_for(names, data):
    """Blue = before, orange = after; extra files of the same condition get other colors."""
    col, used, k = {}, set(), 0
    for n in names:
        c = BEFORE_COLOR if data[n]["condition"] == "Before AMF" else AFTER_COLOR
        if c in used:
            c = EXTRA_COLORS[k % len(EXTRA_COLORS)]; k += 1
        used.add(c); col[n] = c
    return col


# ----------------------------------------------------------------------------- masks
def find_hotspot(stack, min_ratio=1.15, sigma=40, central=0.5, max_r_frac=0.25):
    """Detect a stationary bright reflection disc. Returns (x, y, r) in px or None.
    The objective's reflection lies on the optical axis, so the peak is searched only in the
    central part of the image (central fraction `central` of each side); this stops bright
    edges or illumination gradients from being taken for the hotspot. Radius is capped at
    max_r_frac of the image size. Use --hotspot x,y,r for an off-centre reflection."""
    g = ndi.gaussian_filter(stack.mean(0), sigma)
    H, W = g.shape
    y0, y1 = int(H * (1 - central) / 2), int(H * (1 + central) / 2)
    x0, x1 = int(W * (1 - central) / 2), int(W * (1 + central) / 2)
    sub = g[y0:y1, x0:x1]
    yy_, xx_ = np.unravel_index(sub.argmax(), sub.shape)
    y, x = yy_ + y0, xx_ + x0
    peak, base = g[y, x], np.median(g)
    if peak / base < min_ratio:
        return None
    yy, xx = np.indices(g.shape)
    rr = np.hypot(yy - y, xx - x).astype(int)
    prof = ndi.mean(g, rr, np.arange(rr.max() + 1))
    below = np.where(prof < base + 0.5 * (peak - base))[0]
    r_half = int(below[0]) if len(below) else 100
    return int(x), int(y), int(min(r_half * 1.6, max_r_frac * min(H, W)))


def analysis_mask(shape, hotspot, edge=16):
    m = np.zeros(shape, bool)
    m[edge:-edge, edge:-edge] = True
    if hotspot:
        x, y, r = hotspot
        yy, xx = np.indices(shape)
        m &= (yy - y) ** 2 + (xx - x) ** 2 > r ** 2
    return m


# ----------------------------------------------------------------------------- filters
def flatten(img, sigma_bg):
    return img / (ndi.gaussian_filter(img, sigma_bg) + 1e-6)


def ridge(flat, sigmas):
    return filters.sato(flat, sigmas=sigmas, black_ridges=False)


def remove_small(binary, n):
    """Remove objects of <= n pixels (works on old and new scikit-image)."""
    try:
        return morphology.remove_small_objects(binary, max_size=n)
    except TypeError:
        return morphology.remove_small_objects(binary, min_size=n + 1)


def radii_schedule(rmax, fine_step=0.5, fine_until=6.0, rel_step=0.05):
    """Radii (px), large to small: 0.5 px steps up to 6 px, then 5 % steps.
    Values between steps are refined to the exact distance value (see local_thickness)."""
    r = list(np.arange(fine_step, min(fine_until, rmax) + 1e-9, fine_step))
    x = fine_until
    while x < rmax:
        x *= 1 + rel_step
        r.append(min(x, rmax))
    r = np.minimum(np.unique(np.round(r, 4)), rmax)     # rounding must not exceed the largest radius
    return np.unique(r)[::-1]


def _uphill(dt):
    """For every pixel: flat index of the pixel with the largest dt in its 3x3 neighbourhood."""
    H, W = dt.shape
    p = np.pad(dt.astype(np.float32), 1, constant_values=-1)
    best = dt.astype(np.float32).copy()
    up = np.arange(H * W, dtype=np.int64).reshape(H, W)
    for dr, dc in _OFFS:
        nb = p[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        better = nb > best
        best = np.where(better, nb, best)
        rr, cc = np.nonzero(better)
        up[rr, cc] = (rr + dr) * W + (cc + dc)
    return up.ravel()


def local_thickness(pores, measure, known, dt=None, dk=None):
    """2D local thickness of the pore phase (Hildebrand & Rüegsegger definition), in px.
    LT(p) = largest diameter 2*d(c) of a circle centred at c (d = distance to the nearest fiber)
    that contains p.  Computed for the pixels in `measure`.
    The radii are scanned from large to small in steps (radii_schedule); a pixel first covered
    at a step gets the radius of the covering circle found by walking uphill on the distance map
    from the nearest circle centre (as long as the circle still contains the pixel), so the
    value is not rounded down to the step.
    Edge correction: a value is kept only if that circle lies entirely inside `known`
    (the analysed area); otherwise the pore may continue outside the image, its size is
    unknown, and the pixel is returned as NaN (minus-sampling).
    Returns (lt, dropped): lt = diameters (NaN where not measured), dropped = edge-excluded pixels."""
    if dt is None:
        dt = ndi.distance_transform_edt(pores)
    if dk is None:
        dk = ndi.distance_transform_edt(known)        # distance to the nearest unknown pixel
    H, W = pores.shape
    lt = np.full(pores.shape, np.nan)
    dropped = np.zeros(pores.shape, bool)
    if not dt.max() > 0:
        return lt, dropped
    dtf, dkf, up = dt.ravel(), dk.ravel(), _uphill(dt)
    prev = np.inf
    for r in radii_schedule(float(dt.max())):
        cand = measure & np.isnan(lt) & ~dropped
        if not cand.any():
            break
        centers = dt >= r
        if not centers.any():
            prev = r
            continue
        dist, (ir, ic) = ndi.distance_transform_edt(~centers, return_indices=True)
        # pixels within r of a centre are covered for certain; pixels up to 1.5 px further out
        # (the rim of a pore, missed on a pixel grid) are checked against the exact rule below
        new = cand & (dist < r + 1.5)
        if new.any():
            py, px_ = np.where(new)
            c = ir[py, px_].astype(np.int64) * W + ic[py, px_]     # nearest centre with radius >= r
            slack = dtf[c] - dist[py, px_]                          # > 0: that circle contains the pixel
            # walk uphill on dt to the largest circle that still contains the pixel
            for _ in range(int(np.ceil(prev - r)) + 3 if np.isfinite(prev) else 3):
                c2 = up[c]
                s2 = dtf[c2] - np.hypot(py - c2 // W, px_ - c2 % W)
                move = (dtf[c2] > dtf[c]) & (s2 > np.minimum(slack, 0))
                if not move.any():
                    break
                c, slack = np.where(move, c2, c), np.where(move, s2, slack)
            cov = slack > 0
            py, px_, c = py[cov], px_[cov], c[cov]
            # capped by the previous step: the pixel was not covered by any larger circle
            val = np.minimum(dtf[c], prev)
            ok = dkf[c] >= val
            lt[py, px_] = np.where(ok, 2 * val, np.nan)
            dropped[py, px_] = ~ok
        prev = r
    return lt, dropped


EDGE_WEIGHT_FLOOR = 0.05      # smallest inclusion probability used for the edge weights


def window_sizes(valid, dk=None):
    """Sorted distances (px) from every analysed pixel to the nearest excluded pixel.
    Used by edge_weight: the number of positions at which a circle of radius R fits inside the
    analysed area is the number of these distances >= R."""
    if dk is None:
        dk = ndi.distance_transform_edt(valid)
    return np.sort(dk[valid])


def edge_weight(diam_px, dk_sorted):
    """Edge-correction weight (Miles-Lantuejoul / Horvitz-Thompson) for a feature of size
    diam_px: 1 / (number of positions at which a circle of that diameter fits inside the
    analysed area).  For a plain rectangle H x W this is 1 / ((H - D)(W - D)); computed from
    the real analysed area it also accounts for the excluded reflection disc.
    Large features are more likely to touch an excluded area and be dropped (minus-sampling);
    this weight compensates.  The inclusion probability is floored at EDGE_WEIGHT_FLOOR so a
    single feature that only just fits cannot dominate the result."""
    n = dk_sorted.size
    cnt = n - np.searchsorted(dk_sorted, np.asarray(diam_px, float) / 2, side="left")
    return 1.0 / np.maximum(cnt, max(EDGE_WEIGHT_FLOOR * n, 1.0))


def weighted_median(x, w):
    if len(x) == 0:
        return np.nan
    o = np.argsort(x); x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[np.searchsorted(c, 0.5 * c[-1])])


_OFFS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def skeleton_length(sk):
    """Length (px) of a skeleton with the Vossepoel-Smeulders chain-code estimator:
    L = 0.980 n_orth + 1.406 n_diag - 0.091 n_corner, on a thinned 8-connected skeleton.
    Plain pixel counting underestimates diagonal fibers by up to 29 %; this estimator is
    within about ±3 % at every angle."""
    sk = morphology.thin(sk)
    p = np.pad(sk, 1)
    nb = np.stack([p[1 + dr:p.shape[0] - 1 + dr, 1 + dc:p.shape[1] - 1 + dc] for dr, dc in _OFFS])
    a, b, c, d = sk[:-1, :-1], sk[:-1, 1:], sk[1:, :-1], sk[1:, 1:]
    n_o = (sk[:, :-1] & sk[:, 1:]).sum() + (sk[:-1, :] & sk[1:, :]).sum()
    n_d = (a & d & ~b & ~c).sum() + (b & c & ~a & ~d).sum()
    straight = np.zeros(sk.shape, bool)
    for i, j in [(0, 7), (1, 6), (2, 5), (3, 4)]:
        straight |= nb[i] & nb[j]
    n_c = ((nb.sum(0) * sk == 2) & ~straight).sum()
    return 0.980 * n_o + 1.406 * n_d - 0.091 * n_c


def orientation(flat, weight_mask, sigma_st):
    """Structure-tensor fiber angles (rad, -pi/2..pi/2, CCW from +x) and coherency."""
    Arr, Arc, Acc = feature.structure_tensor(flat, sigma=sigma_st, order="rc")
    phi = 0.5 * np.arctan2(2 * Arc, Acc - Arr)                 # gradient angle, y down
    theta = (np.pi / 2 - phi + np.pi / 2) % np.pi - np.pi / 2   # fiber angle, y up
    l1, l2 = feature.structure_tensor_eigenvalues([Arr, Arc, Acc])
    coh = (l1 - l2) / (l1 + l2 + 1e-12)
    th, c = theta[weight_mask], coh[weight_mask]
    zsum = np.exp(2j * th).sum() if th.size else 0j        # kept for pooling over slices
    z = zsum / th.size if th.size else 0
    return dict(theta=th, S=float(abs(z)), mean_angle=float(np.degrees(0.5 * np.angle(z))) if th.size else np.nan,
                coherency=float(c.mean()) if c.size else np.nan, zsum=zsum, n=int(th.size),
                theta_map=theta, coh_map=coh)


def pooled_orientation(zsum, n):
    """Order parameter S and mean angle (deg) of all fiber pixels pooled (axial statistics)."""
    if n == 0:
        return np.nan, np.nan
    z = zsum / n
    return float(abs(z)), float(np.degrees(0.5 * np.angle(z)))


# ----------------------------------------------------------------------------- per slice
_K8 = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8)


def _fill_small_holes(mask, max_px):
    """Fill holes of <= max_px pixels inside fibers (works on old and new scikit-image)."""
    try:
        return morphology.remove_small_holes(mask, max_size=max_px)
    except TypeError:
        return morphology.remove_small_holes(mask, area_threshold=max_px + 1)


def _segments(sk, valid):
    """Split a thinned skeleton at branch points (pixels with >= 3 skeleton neighbours) and
    measure each piece with the Vossepoel-Smeulders chain code (as skeleton_length), adding the
    step from each end to its branch point.
    Returns (labels, lengths_px [index 0 unused], touches_excluded_area, n_branch_points_touched,
    intersects_analysed_area, anchor_in_analysed_area); the anchor of a piece is its first
    pixel in raster order (used to count pieces per area without edge bias)."""
    nbr = ndi.convolve(sk.astype(np.uint8), _K8, mode="constant") * sk
    junc = sk & (nbr >= 3)
    seg = sk & ~junc
    lab, n = ndi.label(seg, structure=np.ones((3, 3)))
    L = np.zeros(n + 1)
    if n == 0:
        return lab, L, np.zeros(1, bool), np.zeros(1, int), np.zeros(1, bool), np.zeros(1, bool)
    h = (lab[:, :-1] > 0) & (lab[:, :-1] == lab[:, 1:]); np.add.at(L, lab[:, :-1][h], 0.980)
    v = (lab[:-1, :] > 0) & (lab[:-1, :] == lab[1:, :]); np.add.at(L, lab[:-1, :][v], 0.980)
    a, b, c, d = lab[:-1, :-1], lab[:-1, 1:], lab[1:, :-1], lab[1:, 1:]
    d1 = (a > 0) & (a == d) & (b != a) & (c != a); np.add.at(L, a[d1], 1.406)
    d2 = (b > 0) & (b == c) & (a != b) & (d != b); np.add.at(L, b[d2], 1.406)
    p = np.pad(seg, 1)
    nb = np.stack([p[1 + dr:p.shape[0] - 1 + dr, 1 + dc:p.shape[1] - 1 + dc] for dr, dc in _OFFS])
    straight = np.zeros(seg.shape, bool)
    for i, j in [(0, 7), (1, 6), (2, 5), (3, 4)]:
        straight |= nb[i] & nb[j]
    np.add.at(L, lab[seg & (nb.sum(0) == 2) & ~straight], -0.091)
    near_j = seg & (ndi.convolve(junc.astype(np.uint8), _K8, mode="constant") > 0)
    np.add.at(L, lab[near_j], 1.0)
    npx = np.bincount(lab.ravel(), minlength=n + 1)
    L = np.maximum(L, (npx > 0).astype(float))          # single-pixel pieces: 1 px
    near_out = ndi.binary_dilation(~valid, structure=np.ones((3, 3)))
    bad = np.bincount(lab[seg & near_out], minlength=n + 1) > 0
    # how many distinct branch points (clusters of branch pixels) each piece touches
    jl, nj = ndi.label(junc, structure=np.ones((3, 3)))
    n_j = np.zeros(n + 1, int)
    if nj:
        big = nj + 1
        jmax = ndi.maximum_filter(jl, size=3)
        jmin = ndi.minimum_filter(np.where(jl > 0, jl, big), size=3)
        pairs = np.unique(np.r_[np.c_[lab[near_j], jmax[near_j]], np.c_[lab[near_j], jmin[near_j]]], axis=0)
        pairs = pairs[(pairs[:, 1] > 0) & (pairs[:, 1] < big)]
        np.add.at(n_j, pairs[:, 0], 1)
    inside = np.bincount(lab[seg & valid], minlength=n + 1) > 0
    flat = lab.ravel()
    nz = np.flatnonzero(flat)
    ids, first = np.unique(flat[nz], return_index=True)
    anchor = np.zeros(n + 1, bool)
    anchor[ids] = valid.ravel()[nz[first]]
    return lab, L, bad, n_j, inside, anchor


def fiber_segments(mask, valid, min_px, hole_px, prune_iter=4, return_labels=False):
    """Fiber segments: pieces of the fiber centerline between free ends and branch points.
    1. fill tiny holes inside fibers (they would make skeleton loops = false branch points)
    2. skeletonize + thin to a 1-px, 8-connected centerline
    3. prune spurs: pieces shorter than min_px hanging off a single branch point (they come
       from uneven fiber edges); repeat, since removing one can expose another
    4. split at the remaining branch points and measure each piece
    Returns a dict for the pieces of at least min_px:
      L        lengths (px)
      bad      touches the excluded area (may continue outside; not used for length statistics)
      inside   has at least one pixel in the analysed area
      anchor   its anchor pixel lies in the analysed area (unbiased counting rule)
      sk       the pruned centerline (whole image)
      lab, bad_lut   label image of the pieces and per-label 'bad' flag (only with return_labels)"""
    sk = morphology.thin(morphology.skeletonize(_fill_small_holes(mask, hole_px)))
    for _ in range(prune_iter):
        lab, L, _, n_j, _, _ = _segments(sk, valid)
        spur = (n_j == 1) & (L < min_px)
        spur[0] = False
        if not spur.any():
            break
        sk = morphology.thin(sk & ~spur[lab])
    lab, L, bad, _, inside, anchor = _segments(sk, valid)
    keep = L >= min_px
    keep[0] = False
    out = dict(L=L[keep], bad=bad[keep], inside=inside[keep], anchor=anchor[keep], sk=sk)
    if return_labels:          # label image of counted segments; bad_lut marks edge-touching ones
        out.update(lab=np.where(keep[lab], lab, 0), bad_lut=bad)
    return out


def geometry(px, P):
    """Convert the µm settings to pixels for a file with pixel size px (µm)."""
    return dict(px=px,
                sigma_bg=P.sigma_bg_um / px,
                ridge_sigmas=[v / px for v in P.ridge_sigmas_um],
                sigma_st=P.sigma_st_um / px,
                min_obj_px=max(int(round(P.min_obj_um2 / px ** 2)), 0),
                tile=max(int(round(P.tile_um / px)), 8),
                edge=max(int(round(P.edge_um / px)), 1),
                pore_min_dist=max(int(round(P.pore_min_dist_um / px)), 1),
                hotspot_sigma=P.hotspot_sigma_um / px,
                min_fiber_len=P.min_fiber_length_um / px,
                tiles=not getattr(P, "no_tiles", False),
                fiber_model=getattr(P, "fiber_model", None))


def analyse_slice(img, valid, thr, G, full=False):
    """All metrics of one optical section. G = geometry() of the file (sizes in px).
    Returns (metrics dict, aux dict); aux holds images only when full=True."""
    px = G["px"]
    flat = flatten(img, G["sigma_bg"])
    if G.get("fiber_model"):            # trained pixel classifier: thr = probability cutoff
        rdg = FM.probability(flat, px, FM.load_model(G["fiber_model"]))
    else:                               # ridge filter: thr = ridge threshold
        rdg = ridge(flat, G["ridge_sigmas"])
    mask = remove_small(rdg > thr, G["min_obj_px"])
    fib = mask & valid
    vf = valid.sum()
    out = {"area_frac_pct": 100 * fib.sum() / vf}
    # QC: how much the fiber density depends on the threshold (threshold 10 % lower / higher)
    for tag, f in (("thr_minus10pct", 0.9), ("thr_plus10pct", 1.1)):
        out[f"area_frac_pct_{tag}"] = 100 * (remove_small(rdg > thr * f, G["min_obj_px"]) & valid).sum() / vf
    del rdg
    # fiber centerline: skeleton of the whole mask (no artificial ends at the boundary), tiny
    # holes filled and spurs from uneven fiber edges pruned; split into segments
    seg = fiber_segments(mask, valid, G["min_fiber_len"], G["min_obj_px"], return_labels=full)
    # length density: the pruned centerline inside the analysed area, chain-code estimator
    out["length_density_um_per_um2"] = skeleton_length(seg["sk"] & valid) * px / (vf * px * px)
    # pores
    pores = ~mask
    dt = ndi.distance_transform_edt(pores)
    dk = ndi.distance_transform_edt(valid)
    window = window_sizes(valid, dk)
    measure = pores & valid
    lt, dropped = local_thickness(pores, measure, valid, dt, dk)
    ok = measure & ~np.isnan(lt)
    D = lt[ok]
    w = edge_weight(D, window)
    out["pore_lt_mean_um"] = float(np.sum(w * D) / np.sum(w) * px) if D.size else np.nan
    out["pore_lt_median_um"] = weighted_median(D, w) * px if D.size else np.nan
    out["pore_lt_edge_excluded_pct"] = 100 * dropped[measure].mean() if measure.any() else np.nan
    lt_hist = np.histogram(D * px, bins=LT_EDGES, weights=w)[0]
    # fiber size: local thickness of the fiber phase (diameter of the largest circle that fits
    # inside the fiber mask and covers the pixel) = apparent fiber thickness; same edge correction
    lt_f, dropped_f = local_thickness(mask, fib, valid, dk=dk)
    okf = fib & ~np.isnan(lt_f)
    Df = lt_f[okf]
    wf = edge_weight(Df, window)
    out["fiber_lt_mean_um"] = float(np.sum(wf * Df) / np.sum(wf) * px) if Df.size else np.nan
    out["fiber_lt_median_um"] = weighted_median(Df, wf) * px if Df.size else np.nan
    out["fiber_lt_edge_excluded_pct"] = 100 * dropped_f[fib].mean() if fib.any() else np.nan
    fib_hist = np.histogram(Df * px, bins=FIB_EDGES, weights=wf)[0]
    # fiber length: segments between free ends / branch points. Segments touching the excluded
    # area may continue outside it -> dropped; the rest re-weighted for their size (a straight
    # segment of length L has a mean width of 2L/pi over all directions)
    Ls, bad_s = seg["L"], seg["bad"]
    Lk = Ls[~bad_s]
    ws = edge_weight(2 * Lk / np.pi, window)
    out["fiber_len_mean_um"] = float(np.sum(ws * Lk) / np.sum(ws) * px) if Lk.size else np.nan
    out["fiber_len_median_um"] = weighted_median(Lk, ws) * px if Lk.size else np.nan
    out["fiber_len_n"] = int(Lk.size)
    # number per area: a segment is counted where its anchor pixel is (each segment has exactly
    # one), so segments cut by the border are neither lost nor counted twice
    out["fiber_segments_per_100um2"] = 100 * int(seg["anchor"].sum()) / (vf * px * px)
    n_in = int(seg["inside"].sum())
    out["fiber_len_edge_excluded_pct"] = 100 * (bad_s & seg["inside"]).sum() / n_in if n_in else np.nan
    seg_hist = np.histogram(Lk * px, bins=SEG_EDGES, weights=ws)[0]
    # secondary pore measure: diameters at distance-map maxima whose circle fits inside the
    # analysed area (edge-corrected the same way)
    pk = feature.peak_local_max(dt, min_distance=G["pore_min_dist"], labels=valid.astype(int))
    if len(pk):
        r = dt[tuple(pk.T)]
        r = r[r <= dk[tuple(pk.T)]]
    else:
        r = np.array([])
    dmax = 2 * r
    wm = edge_weight(dmax, window)
    out["pore_localmax_mean_um"] = float(np.sum(wm * dmax) / np.sum(wm) * px) if dmax.size else np.nan
    out["pore_localmax_median_um"] = weighted_median(dmax, wm) * px if dmax.size else np.nan
    out["pore_localmax_n"] = int(dmax.size)
    # alignment
    o = orientation(flat, fib, G["sigma_st"])
    out["order_S"], out["mean_angle_deg"], out["coherency"] = o["S"], o["mean_angle"], o["coherency"]
    out["n_fiber_px"] = o["n"]
    # heterogeneity
    t, fr = G["tile"], []
    for i in range(0, img.shape[0] - t + 1, t):
        for j in range(0, img.shape[1] - t + 1, t):
            v = valid[i:i + t, j:j + t]
            if v.mean() > 0.8:
                fr.append(fib[i:i + t, j:j + t][v].mean())
    fr = np.array(fr)
    out["tile_cv"] = float(fr.std() / fr.mean()) if len(fr) and fr.mean() > 0 else np.nan
    out["mean_reflectance"] = float(img[valid].mean())
    # quality check: focus, edge/centre brightness, saturation; per-tile table with dark/blurred tiles
    qcm, lap = QC.image_metrics(img, flat, valid)
    out.update(qcm)
    tiles = []
    if G.get("tiles", True):
        tiles, out["qc_bad_tiles_pct"] = QC.tile_table(img, valid, fib, mask, seg["sk"], lt, lt_f, o["theta_map"],
                                                       o["coh_map"], lap, G["tile"], px, skeleton_length)
    aux = dict(zsum=o["zsum"], n=o["n"], angle_hist=angle_hist(o["theta"]), lt_hist=lt_hist,
               fib_hist=fib_hist, seg_hist=seg_hist, tiles=tiles)
    if full:
        aux.update(mask=mask, flat=flat, o=o, lt=lt, lt_f=lt_f, seg_lab=seg["lab"], seg_bad_lut=seg["bad_lut"])
    return out, aux


def _slice_job(args):
    """Worker for parallel slice analysis. With a figure spec (single-file mode) it also draws
    the detailed figure of that slice, inside the worker, and returns only the numbers."""
    z, img, valid, thr, G = args[:5]
    spec = args[5] if len(args) > 5 else None
    out, aux = analyse_slice(img, valid, thr, G, full=spec is not None)
    if spec is not None:
        FIG.update(spec.get("fig", {}))          # workers do not inherit the settings on Mac/Windows
        slice_figure(img, valid, thr, G, out, aux, spec)
        aux = {k: aux[k] for k in ("zsum", "n", "angle_hist", "lt_hist", "fib_hist", "seg_hist", "tiles")}
    return z, out, aux


# ----------------------------------------------------------------------------- plot helpers
def angle_hist(theta):
    return np.histogram(theta, bins=ANGLE_BINS, range=(-np.pi / 2, np.pi / 2))[0].astype(float)


def rose(ax, hist, color, label=None, fill=False):
    """Angle histogram as a closed outline, mirrored (a line at 30 deg also points at 210)."""
    e = np.linspace(-np.pi / 2, np.pi / 2, len(hist) + 1)
    c = (e[:-1] + e[1:]) / 2
    h = hist / max(hist.sum(), 1)
    ang, val = np.r_[c, c + np.pi, c[0]], np.r_[h, h, h[0]]
    ax.plot(ang, val, c=color, lw=1.6, label=label)
    if fill:
        ax.fill(ang, val, color=color, alpha=0.25)


def style_polar(ax, title, note=True):
    ax.set_title(f"{title}\n(radius = fraction of fiber pixels)" if note else title, pad=14)
    ax.set_thetagrids(range(0, 360, 45), [f"{a}°" for a in range(0, 360, 45)])
    if ax.get_rmax() <= 0:
        ax.set_rmax(0.01)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3))   # few radial labels
    ax.set_rlabel_position(22.5)
    ax.tick_params(axis="y", labelsize=7, colors="0.4")


def _time_unit(names, data):
    tmax = max((np.nanmax(data[n]["times"]) if data[n].get("times") is not None else 0) for n in names)
    return (60.0, "min") if tmax > 120 else (1.0, "s")


def panel_kind(names, data):
    """'z', 't' or 'single' if all files of a panel are of that kind, else 'mixed'."""
    kinds = {data[n]["kind"] for n in names}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def series_xy(df, d, col, unit=(1.0, "s")):
    """x and y of one file for a 'vs depth' / 'vs time' plot (time points averaged over z)."""
    if d["kind"] == "z":
        return df.z_um.to_numpy(), df[col].to_numpy()
    g = df.groupby("t_index")
    y = g[col].mean().to_numpy()
    if d.get("times") is not None:
        x = g["time_s"].first().to_numpy() / unit[0]
    else:
        x = np.array(list(g.groups.keys()), float)
    return x, y


def compare_plot(ax, names, data, per, col, ylabel, what, title=None):
    """Compare files: lines vs depth (z-stacks), lines vs time (time-lapses), or one bar per
    file (single images, or files of different kinds). Returns the kind used."""
    kind = panel_kind(names, data)
    cols = colors_for(names, data)
    if kind in ("z", "t"):
        unit = _time_unit(names, data) if kind == "t" else None
        for nm in names:
            x, y = series_xy(per[per.file == nm], data[nm], col, unit)
            ax.plot(x, y, "o-", ms=3.5, lw=1.6, c=cols[nm], label=file_label(data[nm]))
        if kind == "z":
            ax.set_xlabel("depth z (µm, bottom → top)")
        else:
            has_t = all(data[n].get("times") is not None for n in names)
            ax.set_xlabel(f"time ({unit[1]})" if has_t else "time point")
        ax.set_title(title or f"{what} vs {'depth' if kind == 'z' else 'time'}")
    else:
        mu = [per[per.file == nm][col].mean() for nm in names]
        sd = [per[per.file == nm][col].std() if (per.file == nm).sum() > 1 else 0 for nm in names]
        xs = np.arange(len(names))
        ax.bar(xs, mu, yerr=sd, capsize=4, color=[cols[nm] for nm in names], alpha=0.85,
               label=None)
        for x, nm in zip(xs, names):
            ax.bar([x], [0], color=cols[nm], label=file_label(data[nm]))   # legend entries
        ax.set_xticks(xs)
        ax.set_xticklabels([data[nm]["name"].split("_")[-1] for nm in names])
        ax.set_xlabel("file (error bar = SD over analysed images)" if kind == "mixed" else "file")
        ax.set_title(title or f"{what}, per file")
    ax.set_ylabel(ylabel)
    return kind


def show_image(ax, img, px, title, cmap="gray", lo=1, hi=99.5):
    ext = [0, img.shape[1] * px, img.shape[0] * px, 0]
    ax.imshow(img, cmap=cmap, vmin=np.percentile(img, lo), vmax=np.percentile(img, hi), extent=ext)
    ax.set_title(title); ax.set_xlabel("x (µm)"); ax.set_ylabel("y (µm)")
    return ext


def draw_hotspot(ax, d):
    if d["hotspot"]:
        x, y, r = d["hotspot"]; px = d["px"]
        ax.add_patch(plt.Circle((x * px, y * px), r * px, fill=False, ec="yellow", ls="--", lw=1))


DENS_Y = "fiber area fraction (%)"
PORE_Y = "mean pore size (µm)\n(local thickness)"
DIST_X = "pore size, local thickness (µm)"
DIST_Y = "% of pore area per µm"


def _plot_edges():
    """Display bins: 0.5 µm wide up to 5 µm, then 10 % of the size (aligned to the 0.25 µm
    storage bins). Wider bins at large sizes remove the comb pattern caused by the discrete
    radius steps and the few large pores per image."""
    e, x = [0.0], 0.0
    while x < LT_MAX_UM:
        x = min(LT_MAX_UM, x + max(0.5, round(0.1 * x / LT_BIN_UM) * LT_BIN_UM))
        e.append(x)
    return np.array(e)


PLOT_EDGES = _plot_edges()


def lt_curve(hist):
    """x (µm bin centres) and y (% of pore area per µm) from a weighted LT histogram."""
    fine = hist[:-1]                                # drop overflow bin for plotting
    tot = hist.sum()
    idx = np.searchsorted(LT_EDGES[:-1], PLOT_EDGES[:-1], side="left")
    h = np.add.reduceat(fine, idx) if fine.size else fine
    w = np.diff(PLOT_EDGES)
    x = (PLOT_EDGES[:-1] + PLOT_EDGES[1:]) / 2
    y = 100 * h / tot / w if tot > 0 else np.zeros_like(x)
    return x, y


def lt_hist_mean(hist):
    x = np.r_[(LT_EDGES[:-2] + LT_EDGES[1:-1]) / 2, LT_MAX_UM]
    return float(np.sum(x * hist) / hist.sum()) if hist.sum() > 0 else np.nan


def lt_xmax(hists, q=0.995):
    """x-limit covering q of the pore area of every histogram."""
    xm = 2.0
    for h in hists:
        if h.sum() > 0:
            c = np.cumsum(h) / h.sum()
            xm = max(xm, LT_EDGES[min(np.searchsorted(c, q) + 1, len(LT_EDGES) - 2)])
    return xm


FIB_X = "fiber size, local thickness (µm)"
FIB_Y = "% of fiber area thinner than x"


def fib_stats(hist):
    """Weighted mean and median (µm) from a fiber-size histogram."""
    tot = hist.sum()
    if tot <= 0:
        return np.nan, np.nan
    x = np.r_[(FIB_EDGES[:-2] + FIB_EDGES[1:-1]) / 2, FIB_MAX_UM]
    c = np.cumsum(hist) / tot
    return float(np.sum(x * hist) / tot), float(x[np.searchsorted(c, 0.5)])


def fib_xmax(hists, q=0.995):
    xm = 0.5
    for h in hists:
        if h.sum() > 0:
            c = np.cumsum(h) / h.sum()
            xm = max(xm, FIB_EDGES[min(np.searchsorted(c, q) + 1, len(FIB_EDGES) - 2)] * 1.1)
    return xm


SEG_X = "fiber segment length (µm)"
SEG_Y = "% of fiber segments per µm"


def _seg_plot_edges():
    """Display bins: 0.25 µm up to 3 µm, then 10 % of the length."""
    e, x = [0.0], 0.0
    while x < SEG_MAX_UM:
        x = min(SEG_MAX_UM, x + max(0.25, round(0.1 * x / SEG_BIN_UM) * SEG_BIN_UM))
        e.append(x)
    return np.array(e)


SEG_PLOT_EDGES = _seg_plot_edges()


def seg_stats(hist):
    tot = hist.sum()
    if tot <= 0:
        return np.nan, np.nan
    x = np.r_[(SEG_EDGES[:-2] + SEG_EDGES[1:-1]) / 2, SEG_MAX_UM]
    c = np.cumsum(hist) / tot
    return float(np.sum(x * hist) / tot), float(x[np.searchsorted(c, 0.5)])


def seg_xmax(hists, q=0.99):
    xm = 2.0
    for h in hists:
        if h.sum() > 0:
            c = np.cumsum(h) / h.sum()
            xm = max(xm, SEG_EDGES[min(np.searchsorted(c, q) + 1, len(SEG_EDGES) - 2)])
    return xm


def seg_plot(ax, hist, color, label, title):
    """Fiber-segment-length distribution (each segment counts once), dashed line = mean."""
    tot = hist.sum()
    fine = hist[:-1]
    idx = np.searchsorted(SEG_EDGES[:-1], SEG_PLOT_EDGES[:-1], side="left")
    h = np.add.reduceat(fine, idx) if fine.size else fine
    w = np.diff(SEG_PLOT_EDGES)
    x = (SEG_PLOT_EDGES[:-1] + SEG_PLOT_EDGES[1:]) / 2
    y = 100 * h / tot / w if tot > 0 else np.zeros_like(x)
    m, med = seg_stats(hist)
    lab = f"{label}: mean {m:.2f}, median {med:.2f} µm" if np.isfinite(m) else label
    ax.plot(x, y, "o-", ms=2.5, c=color, lw=1.6, label=lab)
    if np.isfinite(m):
        ax.axvline(m, c=color, ls="--", lw=1)
    ax.set_xlabel(SEG_X); ax.set_ylabel(SEG_Y); ax.set_title(title)


def fib_plot(ax, hist, color, label, title):
    """Cumulative fiber-size distribution (area-weighted). Fiber widths are only a few pixels,
    so they take discrete values; a cumulative curve shows them without binning artifacts."""
    tot = hist.sum()
    x = FIB_EDGES[1:-1]
    y = 100 * np.cumsum(hist[:-1]) / tot if tot > 0 else np.zeros_like(x)
    m, med = fib_stats(hist)
    lab = f"{label}: mean {m:.2f}, median {med:.2f} µm" if np.isfinite(m) else label
    ax.step(np.r_[0, x], np.r_[0, y], where="post", c=color, lw=1.8, label=lab)
    if np.isfinite(m):
        ax.axvline(m, c=color, ls="--", lw=1)
    ax.axhline(50, c="0.6", ls=":", lw=1)
    ax.set_xlabel(FIB_X); ax.set_ylabel(FIB_Y); ax.set_title(title)
    ax.set_ylim(0, 105)


def dist_plot(ax, hist, color, label, title):
    x, y = lt_curve(hist)
    ax.plot(x, y, "o-", ms=2.5, c=color, lw=1.6, label=label)
    m = lt_hist_mean(hist)
    if np.isfinite(m):
        ax.axvline(m, c=color, ls="--", lw=1)
    ax.set_xlabel(DIST_X); ax.set_ylabel(DIST_Y); ax.set_title(title)


# ----------------------------------------------------------------------------- figures
def _image_panel(ax, img, px, title, hi=99.5):
    show_image(ax, img, px, title, hi=hi)


def file_figure(d, df, out):
    """Per file: two images, fiber mask, orientation map, then graphs vs depth / vs time
    (z-stacks, time-lapses) or the size distributions (single images), angle, summary."""
    px, aux, kind = d["px"], d["aux"], d["kind"]
    fig = plt.figure(figsize=(22 if kind != "single" else 27, 10.5), layout="constrained")
    top, bottom = fig.subfigures(2, 1, height_ratios=[1, 1])
    ta = top.subplots(1, 4)
    # (i), (ii) images
    _image_panel(ta[0], d["img_a"], px, d["img_a_title"], hi=99.7)
    _image_panel(ta[1], d["img_b"], px, d["img_b_title"])
    draw_hotspot(ta[1], d)
    ta[1].legend(handles=[plt.Line2D([], [], color="yellow", ls="--", label="excluded reflection")],
                 loc="lower right", fontsize=8, facecolor="0.2", labelcolor="w", framealpha=0.7)
    # (iii) fiber mask
    a = ta[2]
    rgb = np.zeros(d["img_a"].shape + (3,))
    rgb[~d["valid"]] = 0.35
    rgb[aux["mask"] & d["valid"]] = 1.0
    ext = [0, rgb.shape[1] * px, rgb.shape[0] * px, 0]
    a.imshow(rgb, extent=ext)
    a.set_title(f"Fiber mask, {d['frame_a_text']}, threshold {d['thr']:.3f}"); a.set_xlabel("x (µm)"); a.set_ylabel("y (µm)")
    a.legend(handles=[Patch(fc="white", ec="0.5", label="fiber"), Patch(fc="black", ec="0.5", label="pore"),
                      Patch(fc="0.35", ec="0.5", label="excluded")], loc="lower right", fontsize=8)
    # (iv) orientation map
    a = ta[3]; o = aux["o"]
    hsv = np.zeros(rgb.shape)
    hsv[..., 0] = (o["theta_map"] + np.pi / 2) / np.pi
    hsv[..., 1] = np.clip(o["coh_map"] * 1.5, 0, 1)
    fl = aux["flat"]; lo, hi = np.percentile(fl, 1), np.percentile(fl, 99.5)
    hsv[..., 2] = np.clip((fl - lo) / (hi - lo), 0, 1)
    a.imshow(matplotlib.colors.hsv_to_rgb(hsv), extent=ext)
    a.set_title(f"Orientation map, {d['frame_a_text']}"); a.set_xlabel("x (µm)"); a.set_ylabel("y (µm)")
    sm = plt.cm.ScalarMappable(cmap="hsv", norm=matplotlib.colors.Normalize(-90, 90))
    cb = fig.colorbar(sm, ax=a, fraction=0.046, pad=0.02, ticks=[-90, -45, 0, 45, 90])
    cb.set_label("fiber angle (°); color strength = coherency")
    # bottom row
    if kind == "single":
        ba = bottom.subplots(1, 5)
        dist_plot(ba[0], d["lt_hist"], AFTER_COLOR, "pore size", "Pore size distribution\n(dashed = mean)")
        ba[0].set_xlim(0, lt_xmax([d["lt_hist"]])); ba[0].set_ylim(bottom=0); ba[0].legend(frameon=False)
        fib_plot(ba[1], d["fib_hist"], BEFORE_COLOR, "fiber size", "Fiber size (thickness) distribution,\ncumulative (dashed = mean)")
        ba[1].set_xlim(0, fib_xmax([d["fib_hist"]]))
        ba[1].legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.15))
        seg_plot(ba[2], d["seg_hist"], "#2a9d5c", "fiber length", "Fiber length distribution\n(segments between branch points; dashed = mean)")
        ba[2].set_xlim(0, seg_xmax([d["seg_hist"]])); ba[2].set_ylim(bottom=0)
        ba[2].legend(frameon=False, fontsize=8, loc="upper right")
        k_ang, k_txt = 3, 4
    else:
        ba = bottom.subplots(1, 4)
        one = {d["name"]: d}
        for a, col, ylab, what in [(ba[0], "area_frac_pct", DENS_Y, "Fiber density"),
                                   (ba[1], "pore_lt_mean_um", PORE_Y, "Pore size")]:
            x, y = series_xy(df, d, col, _time_unit([d["name"]], one) if kind == "t" else None)
            a.plot(x, y, "o-", ms=3.5, lw=1.6, c=BEFORE_COLOR if col == "area_frac_pct" else AFTER_COLOR,
                   label=what.lower() + (" (mean over z per time point)" if kind == "t" and d["nz"] > 1 else ""))
            if kind == "z":
                a.set_xlabel("depth z (µm, bottom → top)")
            else:
                a.set_xlabel(f"time ({_time_unit([d['name']], one)[1]})" if d.get("times") is not None else "time point")
            a.set_ylabel(ylab); a.set_title(f"{what} vs {'depth' if kind == 'z' else 'time'}")
            a.set_ylim(bottom=0); a.legend(frameon=False)
        k_ang, k_txt = 2, 3
    # fiber angle
    pos = ba[k_ang].get_subplotspec(); ba[k_ang].remove()
    a = bottom.add_subplot(pos, projection="polar")
    rose(a, d["angle_hist"], "#444444", label=f"S = {d['S']:.2f}, mean {d['angle']:.0f}° (all fiber pixels pooled)", fill=True)
    style_polar(a, "Fiber angle distribution" + ("" if kind == "single" else " (all analysed images)"))
    a.legend(loc="lower center", bbox_to_anchor=(0.5, -0.2), frameon=False, fontsize=9)
    # summary text
    a = ba[k_txt]; a.axis("off")
    pm = lambda c: f"{df[c].mean():.2f}" + (f" ± {df[c].std():.2f}" if len(df) > 1 else "")
    if kind == "z":
        over = f"mean ± SD over {len(df)} analysed slices\npixel {px:.4f} µm, z-step {d['dz']} µm"
    elif kind == "t":
        tt = d.get("times")
        span = f", {tt[-1]:.0f} s total" if tt is not None else ""
        over = (f"mean ± SD over {df.t_index.nunique()} time points" + (f" × {df.z_index.nunique()} z" if d["nz"] > 1 else "")
                + f"{span}\npixel {px:.4f} µm")
    else:
        over = f"single image\npixel {px:.4f} µm"
    txt = (f"{condition_text(d)}\n{collagen_text(d) or 'collagen concentration: not in file name'}\n"
           + (f"{nc_text(d)}\n" if nc_text(d) else "") + (f"note: {d['other']}\n" if d.get("other") else "") +
           f"sample {d['sample']}   ({SERIES_NAME[kind]})\n\n"
           f"fiber area fraction: {pm('area_frac_pct')} %\n"
           f"   threshold -10 % / +10 %: {df.area_frac_pct_thr_minus10pct.mean():.2f} / "
           f"{df.area_frac_pct_thr_plus10pct.mean():.2f} %\n"
           f"length density: {df.length_density_um_per_um2.mean():.3f} µm/µm²\n"
           f"pore size (local thickness):\n"
           f"   mean {pm('pore_lt_mean_um')} µm, median {df.pore_lt_median_um.mean():.2f} µm\n"
           f"   edge-excluded pore area {df.pore_lt_edge_excluded_pct.mean():.0f} %\n"
           f"local pore diameter (maxima): {df.pore_localmax_mean_um.mean():.2f} µm\n"
           f"fiber size (local thickness): mean {df.fiber_lt_mean_um.mean():.2f}, "
           f"median {df.fiber_lt_median_um.mean():.2f} µm\n"
           f"fiber segment length: mean {df.fiber_len_mean_um.mean():.2f}, "
           f"median {df.fiber_len_median_um.mean():.2f} µm\n"
           f"   {df.fiber_segments_per_100um2.mean():.0f} segments per 100 µm²\n"
           f"order parameter S (pooled): {d['S']:.2f}\n"
           f"mean fiber angle (pooled): {d['angle']:.0f}°\n"
           f"tile CV (patchiness): {df.tile_cv.mean():.2f}\n\n"
           f"{over}\n"
           f"fiber threshold {d['thr']:.4f}")
    a.text(0, 1, txt, va="top", family="monospace", fontsize=10)
    fig.suptitle(" | ".join(x for x in (collagen_text(d), nc_text(d), condition_text(d), f"sample {d['sample']}",
                                        SERIES_NAME[kind]) if x)
                 + f"\n{d['name']}", fontsize=13)
    savefig(fig, os.path.join(out, d["name"] + ".png"), 100)


def slice_figure(img, valid, thr, G, out, aux, spec):
    """Single-file mode: everything about one analysed image.
    Row 1: image, fiber mask, orientation map, fiber segments.
    Row 2: pore-size map, fiber-size map, pore-size and fiber-size distributions.
    Row 3: fiber-length distribution, fiber angles, numbers."""
    px = G["px"]
    ext = [0, img.shape[1] * px, img.shape[0] * px, 0]
    fig = plt.figure(figsize=(24, 17), layout="constrained")
    gs = fig.add_gridspec(3, 4)
    def axim(a, title):
        a.set_title(title); a.set_xlabel("x (µm)"); a.set_ylabel("y (µm)")
    # image
    a = fig.add_subplot(gs[0, 0])
    show_image(a, img, px, "Image", hi=99.7)
    if spec.get("hotspot"):
        x, y, r = spec["hotspot"]
        a.add_patch(plt.Circle((x * px, y * px), r * px, fill=False, ec="yellow", ls="--", lw=1))
    # fiber mask
    a = fig.add_subplot(gs[0, 1])
    rgb = np.zeros(img.shape + (3,)); rgb[~valid] = 0.35; rgb[aux["mask"] & valid] = 1.0
    a.imshow(rgb, extent=ext); axim(a, f"Fiber mask (threshold {thr:.3f})")
    a.legend(handles=[Patch(fc="white", ec="0.5", label="fiber"), Patch(fc="black", ec="0.5", label="pore"),
                      Patch(fc="0.35", ec="0.5", label="excluded")], loc="lower right", fontsize=8)
    # orientation map
    a = fig.add_subplot(gs[0, 2]); o = aux["o"]
    hsv = np.zeros(img.shape + (3,))
    hsv[..., 0] = (o["theta_map"] + np.pi / 2) / np.pi
    hsv[..., 1] = np.clip(o["coh_map"] * 1.5, 0, 1)
    fl = aux["flat"]; lo, hi = np.percentile(fl, 1), np.percentile(fl, 99.5)
    hsv[..., 2] = np.clip((fl - lo) / (hi - lo), 0, 1)
    a.imshow(matplotlib.colors.hsv_to_rgb(hsv), extent=ext); axim(a, "Orientation map")
    sm = plt.cm.ScalarMappable(cmap="hsv", norm=matplotlib.colors.Normalize(-90, 90))
    cb = fig.colorbar(sm, ax=a, fraction=0.046, pad=0.02, ticks=[-90, -45, 0, 45, 90])
    cb.set_label("fiber angle (°); color strength = coherency")
    # fiber segments
    a = fig.add_subplot(gs[0, 3])
    lab, bad_lut = aux["seg_lab"], aux["seg_bad_lut"]
    seg_rgb = np.zeros(img.shape + (3,)); seg_rgb[~valid] = 0.2
    seg_rgb[aux["mask"] & valid] = 0.18
    tab = plt.get_cmap("tab20")(np.arange(20))[:, :3]
    on = lab > 0
    cols_ = tab[lab[on] % 20]
    cols_[bad_lut[lab[on]]] = 0.55            # segments touching the excluded area: grey
    seg_sk = ndi.binary_dilation(on, iterations=1)   # thicken for visibility
    lab_d = ndi.grey_dilation(lab, size=3)
    on_d = seg_sk & (lab_d > 0)
    cd = tab[lab_d[on_d] % 20]; cd[bad_lut[lab_d[on_d]]] = 0.55
    seg_rgb[on_d] = cd
    a.imshow(seg_rgb, extent=ext); axim(a, f"Fiber segments ({out['fiber_len_n']} counted; grey = touch edge, not counted)")
    # pore-size map
    a = fig.add_subplot(gs[1, 0])
    im = a.imshow(np.where(np.isnan(aux["lt"]), np.nan, aux["lt"] * px), cmap="viridis", extent=ext,
                  interpolation="nearest")
    a.set_facecolor("black"); axim(a, "Pore size map (local thickness)\nblack = fiber / excluded / edge-dropped")
    fig.colorbar(im, ax=a, fraction=0.046, pad=0.02).set_label("pore size (µm)")
    # fiber-size map
    a = fig.add_subplot(gs[1, 1])
    lf = np.where(aux["mask"] & valid, aux["lt_f"] * px, np.nan)
    im = a.imshow(lf, cmap="magma", extent=ext, interpolation="nearest",
                  vmin=0, vmax=max(np.nanpercentile(lf, 99), 0.5) if np.isfinite(lf).any() else 1)
    a.set_facecolor("#1f3b5a"); axim(a, "Fiber size map (local thickness)\nblue = not fiber")
    fig.colorbar(im, ax=a, fraction=0.046, pad=0.02).set_label("fiber size (µm)")
    # distributions
    a = fig.add_subplot(gs[1, 2])
    dist_plot(a, aux["lt_hist"], AFTER_COLOR, "pore size", "Pore size distribution (dashed = mean)")
    a.set_xlim(0, lt_xmax([aux["lt_hist"]])); a.set_ylim(bottom=0); a.legend(frameon=False)
    a = fig.add_subplot(gs[1, 3])
    fib_plot(a, aux["fib_hist"], BEFORE_COLOR, "fiber size", "Fiber size distribution, cumulative (dashed = mean)")
    a.set_xlim(0, fib_xmax([aux["fib_hist"]])); a.legend(frameon=False, fontsize=8, loc="lower right")
    a = fig.add_subplot(gs[2, 0])
    seg_plot(a, aux["seg_hist"], "#2a9d5c", "fiber length", "Fiber length distribution (dashed = mean)")
    a.set_xlim(0, seg_xmax([aux["seg_hist"]])); a.set_ylim(bottom=0); a.legend(frameon=False, fontsize=8)
    a = fig.add_subplot(gs[2, 1], projection="polar")
    rose(a, aux["angle_hist"], "#444444", label=f"S = {out['order_S']:.2f}, mean {out['mean_angle_deg']:.0f}°", fill=True)
    style_polar(a, "Fiber angle distribution")
    a.legend(loc="lower center", bbox_to_anchor=(0.5, -0.2), frameon=False, fontsize=9)
    a = fig.add_subplot(gs[2, 2:]); a.axis("off")
    rows_ = [("fiber area fraction", f"{out['area_frac_pct']:.2f} %"),
             ("   threshold -10 % / +10 %", f"{out['area_frac_pct_thr_minus10pct']:.2f} % / {out['area_frac_pct_thr_plus10pct']:.2f} %"),
             ("length density", f"{out['length_density_um_per_um2']:.3f} µm/µm²"),
             ("pore size, local thickness", f"mean {out['pore_lt_mean_um']:.2f}, median {out['pore_lt_median_um']:.2f} µm"),
             ("   edge-excluded pore area", f"{out['pore_lt_edge_excluded_pct']:.0f} %"),
             ("local pore diameter (maxima)", f"mean {out['pore_localmax_mean_um']:.2f} µm (n = {out['pore_localmax_n']})"),
             ("fiber size, local thickness", f"mean {out['fiber_lt_mean_um']:.2f}, median {out['fiber_lt_median_um']:.2f} µm"),
             ("fiber segment length", f"mean {out['fiber_len_mean_um']:.2f}, median {out['fiber_len_median_um']:.2f} µm"),
             ("   segments", f"{out['fiber_len_n']} counted, {out['fiber_segments_per_100um2']:.1f} per 100 µm², "
                            f"{out['fiber_len_edge_excluded_pct']:.0f} % touch the edge"),
             ("order parameter S", f"{out['order_S']:.3f}  (mean angle {out['mean_angle_deg']:.1f}°)"),
             ("coherency", f"{out['coherency']:.3f}"),
             ("tile CV (patchiness)", f"{out['tile_cv']:.3f}"),
             ("mean reflectance (QC)", f"{out['mean_reflectance']:.1f}"),
             ("fiber threshold", f"{thr:.4f}")]
    a.text(0, 1, "\n".join(f"{k:<30s} {v}" for k, v in rows_), va="top", family="monospace", fontsize=11)
    fig.suptitle(spec["title"], fontsize=14)
    savefig(fig, spec["path"], 90)


DETAIL_METRICS = [("area_frac_pct", "fiber area fraction (%)", "Fiber density"),
                  ("length_density_um_per_um2", "length density (µm/µm²)", "Fiber length per area"),
                  ("pore_lt_mean_um", "pore size, local thickness (µm)", "Pore size (mean; dashed = median)"),
                  ("fiber_lt_mean_um", "fiber size, local thickness (µm)", "Fiber size (mean; dashed = median)"),
                  ("fiber_len_mean_um", "fiber segment length (µm)", "Fiber segment length (mean; dashed = median)"),
                  ("fiber_segments_per_100um2", "segments per 100 µm²", "Number of fiber segments"),
                  ("order_S", "order parameter S / coherency", "Alignment (S solid, coherency dashed)"),
                  ("tile_cv", "tile CV", "Patchiness (tile CV)"),
                  ("mean_reflectance", "mean reflectance (a.u.)", "Mean reflectance (QC)")]
SECOND = {"pore_lt_mean_um": "pore_lt_median_um", "fiber_lt_mean_um": "fiber_lt_median_um",
          "fiber_len_mean_um": "fiber_len_median_um", "order_S": "coherency"}


def file_detail_figures(d, df, frame_hists, out):
    """Single-file mode: compare the analysed images of one file with each other.
    <file>_slice_comparison.png   every metric vs depth / time
    <file>_slice_distributions.png  size, length and angle distributions of every image,
                                    colored by depth / time"""
    kind = d["kind"]
    if kind == "z":
        xs = df.z_um.to_numpy(); xlab = "depth z (µm, bottom → top)"; cbl = "depth z (µm)"
    elif d.get("times") is not None:
        u = (60.0, "min") if np.nanmax(d["times"]) > 120 else (1.0, "s")
        xs = df.time_s.to_numpy() / u[0]; xlab = f"time ({u[1]})"; cbl = xlab
        if d["nz"] > 1:
            xs = xs + df.z_index.to_numpy() * 0     # one point per (t, z); same x within a time point
    else:
        xs = df.t_index.to_numpy().astype(float); xlab = "time point"; cbl = xlab
    # ---- metrics vs depth / time
    fig, axs = plt.subplots(3, 3, figsize=(18, 13), layout="constrained")
    for a, (c, yl, tl) in zip(axs.flat, DETAIL_METRICS):
        names_ = {"order_S": ("order parameter S", "coherency")}.get(c, ("mean", "median"))
        a.plot(xs, df[c], "o-", ms=4, lw=1.6, c=BEFORE_COLOR, label=names_[0] if c in SECOND else None)
        if c in SECOND:
            a.plot(xs, df[SECOND[c]], "s--", ms=3, lw=1.2, c=AFTER_COLOR, label=names_[1])
        a.set_xlabel(xlab); a.set_ylabel(yl); a.set_title(tl)
        if c != "mean_reflectance":
            a.set_ylim(bottom=0)
        a.grid(alpha=0.25)
        if c in SECOND:
            a.legend(frameon=False, fontsize=8)
    fig.suptitle(f"{d['name']}: all analysed images compared ({SERIES_NAME[kind]}, {len(df)} images)", fontsize=14)
    savefig(fig, os.path.join(out, f"{d['name']}_slice_comparison.png"), 100)
    # ---- distributions of every image, colored by depth / time
    cmap = plt.get_cmap("viridis")
    norm = matplotlib.colors.Normalize(np.nanmin(xs), np.nanmax(xs) if np.nanmax(xs) > np.nanmin(xs) else np.nanmin(xs) + 1)
    fig = plt.figure(figsize=(22, 6.5), layout="constrained")
    g = fig.add_gridspec(1, 4)
    a1, a2, a3 = fig.add_subplot(g[0]), fig.add_subplot(g[1]), fig.add_subplot(g[2])
    a4 = fig.add_subplot(g[3], projection="polar")
    for x, h in zip(xs, frame_hists):
        col = cmap(norm(x))
        xx, yy = lt_curve(h["lt_hist"]); a1.plot(xx, yy, c=col, lw=1.2)
        tot = h["fib_hist"].sum()
        if tot > 0:
            a2.step(np.r_[0, FIB_EDGES[1:-1]], np.r_[0, 100 * np.cumsum(h["fib_hist"][:-1]) / tot], where="post", c=col, lw=1.2)
        fine = h["seg_hist"][:-1]
        idx = np.searchsorted(SEG_EDGES[:-1], SEG_PLOT_EDGES[:-1], side="left")
        hh = np.add.reduceat(fine, idx); st = h["seg_hist"].sum()
        if st > 0:
            a3.plot((SEG_PLOT_EDGES[:-1] + SEG_PLOT_EDGES[1:]) / 2, 100 * hh / st / np.diff(SEG_PLOT_EDGES), c=col, lw=1.2)
        rose(a4, h["angle_hist"], col)
    a1.set_xlim(0, lt_xmax([h["lt_hist"] for h in frame_hists])); a1.set_ylim(bottom=0)
    a1.set_xlabel(DIST_X); a1.set_ylabel(DIST_Y); a1.set_title("Pore size distribution of each image")
    a2.set_xlim(0, fib_xmax([h["fib_hist"] for h in frame_hists])); a2.set_ylim(0, 105)
    a2.set_xlabel(FIB_X); a2.set_ylabel(FIB_Y); a2.set_title("Fiber size distribution of each image (cumulative)")
    a3.set_xlim(0, seg_xmax([h["seg_hist"] for h in frame_hists])); a3.set_ylim(bottom=0)
    a3.set_xlabel(SEG_X); a3.set_ylabel(SEG_Y); a3.set_title("Fiber length distribution of each image")
    style_polar(a4, "Fiber angle distribution of each image")
    for a in (a1, a2, a3):
        a.grid(alpha=0.25)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    fig.colorbar(sm, ax=[a1, a2, a3, a4], fraction=0.02, pad=0.01).set_label(f"line color = {cbl}")
    fig.suptitle(f"{d['name']}: distributions of every analysed image ({len(frame_hists)} images)", fontsize=14)
    savefig(fig, os.path.join(out, f"{d['name']}_slice_distributions.png"), 100)


def sample_figure(smp, names, data, per, P, out):
    """One sample: before/after images, then density, pore size and angle comparisons."""
    n = len(names); col = colors_for(names, data)
    fig = plt.figure(figsize=(max(22, 5 * n), 17), layout="constrained")
    top, bottom = fig.subfigures(2, 1, height_ratios=[1, 1.75])
    axm = top.subplots(1, n, squeeze=False)[0]
    for a, nm in zip(axm, names):
        d = data[nm]
        show_image(a, d["thumb"], d["px"], f"{file_label(d)}\n"
                   + "".join(f"{t}, " for t in (collagen_text(d), nc_text(d)) if t) + d["thumb_title"])
        a.title.set_color(col[nm]); draw_hotspot(a, d)
    g = bottom.subplots(2, 3)
    # row 1: density vs depth, pore size vs depth, fiber angle
    # row 2: pore size distribution, fiber size distribution, fiber length distribution
    axs = [g[0, 0], g[0, 1], g[1, 0], g[1, 1], g[0, 2], g[1, 2]]
    compare_plot(axs[0], names, data, per, "area_frac_pct", DENS_Y, "Fiber density")
    compare_plot(axs[1], names, data, per, "pore_lt_mean_um", PORE_Y, "Pore size")
    for nm in names:
        dist_plot(axs[2], data[nm]["lt_hist"], col[nm], file_label(data[nm]),
                  "Pore size distribution\n(all analysed images; dashed = mean)")
        fib_plot(axs[3], data[nm]["fib_hist"], col[nm], file_label(data[nm]),
                 "Fiber size (thickness) distribution, cumulative\n(all analysed images; dashed = mean)")
        seg_plot(axs[5], data[nm]["seg_hist"], col[nm], file_label(data[nm]),
                 "Fiber length distribution (segments between branch points)\n(all analysed images; dashed = mean)")
    for a in axs[:3]:
        a.set_ylim(bottom=0); a.legend(frameon=False, fontsize=8.5)
    for a in axs[:2]:                     # headroom for the legend
        a.set_ylim(0, a.get_ylim()[1] * 1.25)
    axs[3].legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.15))
    axs[5].set_ylim(bottom=0); axs[5].legend(frameon=False, fontsize=8, loc="upper right")
    axs[2].set_xlim(0, lt_xmax([data[nm]["lt_hist"] for nm in names]))
    axs[3].set_xlim(0, fib_xmax([data[nm]["fib_hist"] for nm in names]))
    axs[5].set_xlim(0, seg_xmax([data[nm]["seg_hist"] for nm in names]))
    pos = axs[4].get_subplotspec(); axs[4].remove()
    ap = bottom.add_subplot(pos, projection="polar")
    for nm in names:
        rose(ap, data[nm]["angle_hist"], col[nm],
             f"{file_label(data[nm])}: S = {data[nm]['S']:.2f}, {data[nm]['angle']:.0f}°")
    style_polar(ap, "Fiber angle distribution")
    ap.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), frameon=False, fontsize=9)
    amf = sample_amf(names, data)
    col_t = ", ".join(x for x in (sample_collagen(names, data), sample_nc(names, data)) if x)
    fig.suptitle(f"Sample {smp}" + (f", {col_t}" if col_t else "") + ": before vs after AMF"
                 + (f" ({amf})" if amf else "")
                 + "\nfiber thresholds: " + ", ".join(f"{file_label(data[n])} {data[n]['thr']:.3f}" for n in names),
                 fontsize=14)
    savefig(fig, os.path.join(out, f"sample_{smp}.png"), 100)


def panel_title(smp, names, data):
    amf = sample_amf(names, data)
    col_t = ", ".join(x for x in (sample_collagen(names, data), sample_nc(names, data)) if x)
    first = f"Sample {smp}" + (f", {col_t}" if col_t else "")
    return f"{first}\nAMF {amf}" if amf else first


def grid_shape(n, max_cols=3):
    """Rows x columns for n panels: at most max_cols per row."""
    ncols = min(n, max_cols)
    return int(np.ceil(n / ncols)), ncols


def date_figures(date, samples, data, per, out, share_y=False):
    """Three files per date; one panel per sample, laid out in rows of up to 3."""
    n = len(samples)
    nrows, ncols = grid_shape(n)
    items = list(samples.items())
    kinds = {panel_kind(nm_, data) for _, nm_ in items}
    vs = " vs depth" if kinds == {"z"} else (" vs time" if kinds == {"t"} else "")
    for col, ylab, title, fname in [
            ("area_frac_pct", DENS_Y, "Fiber density" + vs, "fiber_density"),
            ("pore_lt_mean_um", PORE_Y, "Pore size" + vs, "pore_size")]:
        if not fig_on("density" if fname == "fiber_density" else "pore_size"):
            continue
        fig, axs = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 4.3 * nrows), squeeze=False,
                                sharey=share_y, gridspec_kw=dict(hspace=0.45, wspace=0.28))
        # y-axis top: data maximum + 30% headroom for the legend (global if shared)
        tops = [per[per.file.isin(names)][col].max() * 1.3 for _, names in items]
        for k, a in enumerate(axs.flat):
            if k >= n:
                a.axis("off"); continue
            smp, names = items[k]
            compare_plot(a, names, data, per, col, ylab, title, title=panel_title(smp, names, data))
            a.set_ylim(0, max(tops) if share_y else tops[k])
            a.legend(frameon=False, fontsize=8.5, loc="upper right", ncol=1)
            a.grid(alpha=0.25)
        if share_y:
            for a in axs[:, 1:].flat:
                a.set_ylabel("")
        note = "same y-axis in all panels" if share_y else "each panel has its own y-axis"
        if kinds - {"z", "t"}:
            note += "; single images / mixed file types: one bar per file"
        fh = fig.get_figheight()                   # keep ~1 inch above the panels for the title
        fig.subplots_adjust(top=1 - 1.35 / fh)
        fig.suptitle(f"{date}: {title}, one graph per sample\n({note})", fontsize=14,
                     y=1 - 0.1 / fh, va="top")
        savefig(fig, os.path.join(out, f"{date}_{fname}.png"), 120)
    # fiber angle
    if fig_on('angle'):
        fig, axs = plt.subplots(nrows, ncols, figsize=(5.2 * ncols, 6.0 * nrows), squeeze=False,
                                subplot_kw={"projection": "polar"},
                                gridspec_kw=dict(hspace=0.6, wspace=0.35))
        fig.subplots_adjust(top=1 - 1.5 / fig.get_figheight())
        rmax = max(d["angle_hist"].max() / max(d["angle_hist"].sum(), 1)
                   for names in samples.values() for d in (data[x] for x in names)) * 1.05
        for k, a in enumerate(axs.flat):
            if k >= n:
                a.axis("off"); continue
            smp, names = items[k]
            c = colors_for(names, data)
            for nm in names:
                df = per[per.file == nm]
                rose(a, data[nm]["angle_hist"], c[nm],
                     f"{file_label(data[nm])}: S = {data[nm]['S']:.2f}")
            a.set_rmax(rmax)                       # same radial scale in every panel
            style_polar(a, panel_title(smp, names, data), note=False)
            a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False, fontsize=9)
        fig.suptitle(f"{date}: Fiber angle distribution, one graph per sample\n"
                     f"(radius = fraction of fiber pixels; same scale in all panels; S = order parameter)",
                     fontsize=14, y=1 - 0.1 / fig.get_figheight(), va="top")
        savefig(fig, os.path.join(out, f"{date}_fiber_angle.png"), 120)
    # pore size distribution
    if fig_on('pore_dist'):
        fig, axs = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 4.3 * nrows), squeeze=False,
                                sharex=True, gridspec_kw=dict(hspace=0.5, wspace=0.28))
        xmax = lt_xmax([data[nm]["lt_hist"] for _, names in items for nm in names])
        for k, a in enumerate(axs.flat):
            if k >= n:
                a.axis("off"); continue
            smp, names = items[k]
            c = colors_for(names, data)
            for nm in names:
                dist_plot(a, data[nm]["lt_hist"], c[nm], file_label(data[nm]), panel_title(smp, names, data))
            a.set_xlim(0, xmax); a.set_ylim(bottom=0)
            a.xaxis.set_tick_params(labelbottom=True)
            a.legend(frameon=False, fontsize=8.5, loc="upper right"); a.grid(alpha=0.25)
        fh = fig.get_figheight()
        fig.subplots_adjust(top=1 - 1.35 / fh)
        fig.suptitle(f"{date}: Pore size distribution (local thickness), one graph per sample\n"
                     f"(area-weighted, all analysed images; dashed line = mean; same x-axis in all panels)",
                     fontsize=14, y=1 - 0.1 / fh, va="top")
        savefig(fig, os.path.join(out, f"{date}_pore_size_distribution.png"), 120)
    # fiber size distribution
    if fig_on('fiber_dist'):
        fig, axs = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 5.2 * nrows), squeeze=False,
                                sharex=True, gridspec_kw=dict(hspace=0.85, wspace=0.28))
        xmax = fib_xmax([data[nm]["fib_hist"] for _, names in items for nm in names])
        for k, a in enumerate(axs.flat):
            if k >= n:
                a.axis("off"); continue
            smp, names = items[k]
            c = colors_for(names, data)
            for nm in names:
                fib_plot(a, data[nm]["fib_hist"], c[nm], file_label(data[nm]), panel_title(smp, names, data))
            a.set_xlim(0, xmax)
            a.xaxis.set_tick_params(labelbottom=True)
            a.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2)); a.grid(alpha=0.25)
        fh = fig.get_figheight()
        fig.subplots_adjust(top=1 - 1.35 / fh)
        fig.suptitle(f"{date}: Fiber size distribution (local thickness = apparent fiber thickness), one graph per sample\n"
                     f"(cumulative, area-weighted, all analysed images; dashed line = mean, dotted = 50 %; same x-axis)",
                     fontsize=14, y=1 - 0.1 / fh, va="top")
        savefig(fig, os.path.join(out, f"{date}_fiber_size_distribution.png"), 120)
    # fiber length distribution
    if fig_on('length_dist'):
        fig, axs = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 5.2 * nrows), squeeze=False,
                                sharex=True, gridspec_kw=dict(hspace=0.85, wspace=0.28))
        xmax = seg_xmax([data[nm]["seg_hist"] for _, names in items for nm in names])
        for k, a in enumerate(axs.flat):
            if k >= n:
                a.axis("off"); continue
            smp, names = items[k]
            c = colors_for(names, data)
            for nm in names:
                seg_plot(a, data[nm]["seg_hist"], c[nm], file_label(data[nm]), panel_title(smp, names, data))
            a.set_xlim(0, xmax); a.set_ylim(bottom=0)
            a.xaxis.set_tick_params(labelbottom=True)
            a.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2)); a.grid(alpha=0.25)
        fh = fig.get_figheight()
        fig.subplots_adjust(top=1 - 1.35 / fh)
        fig.suptitle(f"{date}: Fiber length distribution (segments between ends / branch points), one graph per sample\n"
                     f"(each segment counts once, all analysed images; dashed line = mean; same x-axis)",
                     fontsize=14, y=1 - 0.1 / fh, va="top")
        savefig(fig, os.path.join(out, f"{date}_fiber_length_distribution.png"), 120)


# ----------------------------------------------------------------------------- spreadsheets
def global_settings(P, threshold_source, run_time, single_file, detail):
    """(parameter, value, description) rows that apply to every file."""
    return [("run_time", run_time, "when this analysis was run"),
            ("mode", "per-slice detail" if detail else "averages only",
             "per-slice detail: a folder per file with a figure of every analysed image (default); "
             "--averages-only: comparisons and one figure per file"),
            ("fiber_model", P.fiber_model or "", FM.describe(FM.load_model(P.fiber_model)) if P.fiber_model
             else "none: fiber mask = ridge filter > threshold"),
            ("threshold_mode", "fiber model" if P.fiber_model else "fixed" if P.threshold is not None
             else P.threshold_mode,
             f"fiber threshold on the ridge image: {threshold_source}"),
            ("zstep", P.zstep, "every Nth z-slice analysed"),
            ("tstep", P.tstep, "every Nth time point analysed (time-lapses)"),
            ("stack_axis", P.stack_axis, "how the third axis of 3-D files is read: auto / z / t"),
            ("sigma_bg_um", P.sigma_bg_um, "flattening blur"),
            ("ridge_sigmas_um", ",".join(str(v) for v in P.ridge_sigmas_um), "Sato ridge filter scales"),
            ("sigma_st_um", P.sigma_st_um, "structure tensor window"),
            ("min_obj_um2", P.min_obj_um2, "mask objects this small or smaller removed"),
            ("tile_um", P.tile_um, "tile size for tile CV"),
            ("edge_um", P.edge_um, "excluded image border"),
            ("pore_min_dist_um", P.pore_min_dist_um, "minimum spacing of distance-map maxima"),
            ("min_fiber_length_um", P.min_fiber_length_um, "shortest fiber segment counted; shorter side branches pruned"),
            ("hotspot_sigma_um", P.hotspot_sigma_um, "blur for hotspot detection"),
            ("hotspot_mode", P.hotspot, "auto / none / x,y,r"),
            ("after_pattern", P.after_pattern, "text marking After AMF files"),
            ("pore_size_method", "local thickness, minus-sampling + Miles-Lantuejoul weights",
             "pores whose largest circle leaves the analysed area are excluded and the rest re-weighted by "
             "1 / (number of positions where that circle fits in the analysed area, hotspot included)"),
            ("length_method", "Vossepoel-Smeulders chain code on the pruned centerline",
             "length density: skeleton after filling tiny holes and pruning spurs shorter than "
             "min_fiber_length_um (same centerline as the fiber segments)"),
            ("fiber_length_method", "skeleton split at branch points after pruning spurs; chain-code length",
             "segments touching the excluded area dropped, rest re-weighted (Miles-Lantuejoul, mean width 2L/pi)"),
            ("segment_count_method", "anchor-pixel rule",
             "fiber_segments_per_100um2 counts a segment if its first pixel (raster order) is in the analysed area"),
            ("edge_weight_floor", EDGE_WEIGHT_FLOOR, "smallest inclusion probability used for edge weights"),
            ("threshold_sensitivity", "area_frac_pct_thr_minus10pct / _thr_plus10pct",
             "fiber area fraction with the threshold 10 % lower / higher (QC): a large spread means the "
             "density and pore size of that image depend strongly on the threshold"),
            ("fiber_size_note", "apparent thickness",
             "set by the optical resolution and the threshold, not the true fibril diameter; on the pixel grid "
             "it can read up to 1 pixel high; use for relative comparison"),
            ("slice_independence", "see description",
             "optical sections closer than the axial resolution (about 0.5 µm) show the same fibers; SD over "
             "slices is the variation with depth, not an error bar for comparing samples (replicates = gels / fields)"),
            ("fiber_size_method", "local thickness of the fiber mask, same edge correction as pores",
             "apparent fiber thickness; limited by optical resolution and pixel size"),
            ("orientation_pooling", "complex mean of exp(2i theta) over all fiber pixels",
             "file-level S and mean angle"),
            ("workers", P.workers, "parallel processes"),
            ("user_fields", ",".join(user_fields()) or None,
             "parameters added in the app: " + "; ".join(f"{f['key']} = {f.get('label', f['key'])}"
                                                         + (f" ({f['unit']})" if f.get("unit") else "")
                                                         for f in METADATA.get("fields", []))),
            ("figures", ",".join(k for k in FIGURE_KEYS if k in FIG["keys"]) or "none", "figures made"),
            ("figure_format", f"{FIG['fmt']}, dpi {FIG['dpi'] or 'default'}", "figure file format and resolution"),
            ("single_file", single_file or "", "--file: only this file was analysed"),
            ("tiles", "off" if P.no_tiles else f"{P.tile_um} µm tiles",
             "per-tile table mmddyy_tiles.csv (tiles at least 80 % inside the analysed area)")] \
        + QC.settings_rows(P.qc_model)


def file_settings(d, threshold_source):
    """(parameter:file, value, description) rows of one file."""
    n, G = d["name"], d["G"]
    return [(f"file:{n}", condition_text(d), f"sample {d['sample']}"),
            (f"collagen_mg_ml:{n}", d["collagen_mgml"], "from the file name (_Collagen_#mgml_)"),
            (f"nc_conc_nM:{n}", d["nc_nM"], "theoretical local nanocage concentration, from the file name (_NC_#nM_)"),
            (f"nc_mixing:{n}", mixing_text(d) or None,
             "from the file name: premixed (_premixed_) or incubated with NCs after polymerization (_inc45min_)"),
            (f"other:{n}", d["other"], "note from the file name (_other-..._)"),
            (f"set_by_hand:{n}", ", ".join(MANUAL_KEYS[k] for k in d.get("manual", [])) or None,
             "values entered in the app instead of being read from the file name"),
            (f"amf_vpp_set:{n}", d["amf_vpp"], "AMF instrument setting, V peak-to-peak, from the file name (_10Vpp_)"),
            (f"amf_v_measured:{n}", d["amf_v"], "measured AMF voltage, V, from the file name (_8.5V_)"),
            (f"amf_app_set:{n}", d.get("amf_app"), "AMF current set on the power supply, A peak-to-peak (_2App_)"),
            (f"amf_coil_turns:{n}", d.get("coil_turns"), "turns of the AMF coil (_500turns_)"),
            (f"amf_b_mT:{n}", d.get("amf_b"), f"measured magnetic field, mT, {d.get('amf_b_type') or 'type not given'} "
                                              "(_40mTpk_, _28mTrms_)"),
            (f"threshold:{n}", d["thr"], threshold_source),
            (f"filters_px:{n}", f"bg {G['sigma_bg']:.1f}, ridge {','.join(f'{v:.2f}' for v in G['ridge_sigmas'])}, "
             f"st {G['sigma_st']:.1f}, min_obj {G['min_obj_px']}, tile {G['tile']}, edge {G['edge']}, "
             f"pore_min_dist {G['pore_min_dist']}, min_fiber_len {G['min_fiber_len']:.1f}",
             "µm settings converted to pixels for this file"),
            (f"order_S_pooled:{n}", d["S"], "all fiber pixels of all analysed images"),
            (f"mean_angle_deg_pooled:{n}", d["angle"], "all fiber pixels of all analysed images"),
            (f"pixel_um:{n}", d["px"], "from file metadata"),
            (f"z_step_um:{n}", d["dz"], "from file metadata"),
            (f"z_positions:{n}", d.get("zsrc", ""), "how z_um (height above the lowest slice) was obtained"),
            (f"series:{n}", SERIES_NAME[d["kind"]], f"T={d['nt']}, Z={d['nz']}; {len(d['frames'])} image(s) analysed"),
            (f"time_span_s:{n}", None if d["times"] is None else round(float(d["times"][-1]), 3),
             "time of the last time point (time-lapses)"),
            (f"hotspot_xyr_px:{n}", "none" if d["hotspot"] is None else ",".join(map(str, d["hotspot"])),
             "excluded reflection disc")]


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(df):
    """Excel-safe copy: remove control characters, turn tuples/lists into text."""
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: _ILLEGAL.sub("", v) if isinstance(v, str)
                              else (",".join(map(str, v)) if isinstance(v, (tuple, list)) else v))
    return df


def write_spreadsheet(base, per_df, settings_rows, quality=None):
    """Write <base>.xlsx with four sheets:
      per slice  one row per analysed image of every file: all metrics, then every setting
                 (set_... for all files, file_... for that file), then the file metadata
                 (meta_...) and the per-image metadata (slice_...)
      per file   mean and SD of every metric per file
      settings   parameter / value / description
      quality    one row per file: QC status, flags, model probability and the QC features (ml/qc.py)
    Falls back to CSV files if openpyxl is not installed.
    Returns (path written, per-slice frame, per-file frame, settings frame, quality frame)."""
    q = quality if quality is not None else pd.DataFrame(columns=["file", "qc_status", "qc_flags"])
    st = pd.DataFrame(settings_rows, columns=["parameter", "value", "description"])
    glob_set = {p: v for p, v, _ in settings_rows if ":" not in p}
    file_set = {}
    for p, v, _ in settings_rows:
        if ":" in p:
            k, f = p.split(":", 1)
            file_set.setdefault(f, {})[k] = v
    df = per_df.copy()
    set_cols = {f"set_{k}": v for k, v in glob_set.items()}
    for k, v in set_cols.items():
        df[k] = v
    fkeys = sorted(k for k in {k for dct in file_set.values() for k in dct} - {"file"}
                   if f"file_{k}" not in df.columns)          # skip values already in a column
    for k in fkeys:
        df[f"file_{k}"] = df["file"].map(lambda f, k=k: file_set.get(f, {}).get(k))
    first = [c for c in df.columns if not c.startswith(("set_", "file_", "meta_", "slice_"))
             or c in ("file_order_S_pooled", "file_mean_angle_deg_pooled")]
    order = first + list(set_cols) + [f"file_{k}" for k in fkeys] \
        + [c for c in df.columns if c.startswith("meta_")] + [c for c in df.columns if c.startswith("slice_")]
    df = df[list(dict.fromkeys(order))]
    # per-file averages
    label = [c for c in LABEL_COLS if c not in ("t_index", "time_s", "z_index", "z_um")]
    metrics = [c for c in per_df.columns if c not in LABEL_COLS and not c.startswith(("meta_", "slice_"))
               and pd.api.types.is_numeric_dtype(per_df[c])]
    g = per_df.groupby("file", sort=False)
    pf = g[label].first().reset_index(drop=True)
    pf.insert(pf.columns.get_loc("series") + 1, "n_images", g.size().to_numpy())
    mean, sd = g[metrics].mean().reset_index(drop=True), g[metrics].std().reset_index(drop=True)
    for c in metrics:
        pf[f"{c}_mean"] = mean[c]
        if c not in ("file_order_S_pooled", "file_mean_angle_deg_pooled", "threshold"):
            pf[f"{c}_sd"] = sd[c]
    try:
        import openpyxl  # noqa: F401
        path = base + ".xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            for sheet, frame, freeze in (("per slice", df, (1, 3)), ("per file", pf, (1, 3)), ("settings", st, (1, 1)),
                                         ("quality", q, (1, 1))):
                _clean(frame).to_excel(xw, sheet_name=sheet, index=False, freeze_panes=freeze)
                ws = xw.sheets[sheet]
                for k, col in enumerate(frame.columns, start=1):
                    w = min(max(len(str(col)), 10) + 2, 45)
                    ws.column_dimensions[openpyxl.utils.get_column_letter(k)].width = w
        return path, df, pf, st, q
    except ImportError:
        log("  WARNING: openpyxl not installed (pip install openpyxl) - writing CSV files instead")
        df.to_csv(base + "_per_slice.csv", index=False)
        pf.to_csv(base + "_per_file.csv", index=False)
        st.to_csv(base + "_settings.csv", index=False)
        q.to_csv(base + "_quality.csv", index=False)
        return base + "_per_slice.csv", df, pf, st, q


def write_combined(base, parts):
    """Combine the date summaries of several folders into one workbook <base>.xlsx.
    parts: list of (folder, results folder, date, per-slice, per-file, settings, quality) frames.
    Sheets: 'per slice' and 'per file' (all rows of all folders, with folder and
    results_folder columns first), 'settings' (each folder's settings, one block per
    folder/date) and 'folders' (one line per folder/date: files, images, where the results are).
    Folder paths are written relative to the summary's location."""
    root = os.path.dirname(os.path.abspath(base))
    parts = [(os.path.relpath(fo, root), os.path.relpath(res, root), date, *fr) for fo, res, date, *fr in parts]
    def tagged(i):
        out = []
        for folder, res, date, *frames in parts:
            f = frames[i].copy()
            f.insert(0, "results_folder", res)
            f.insert(0, "folder", folder)
            out.append(f)
        return pd.concat(out, ignore_index=True, sort=False)
    per_slice, per_file, quality = tagged(0), tagged(1), tagged(3)
    st = []
    for folder, res, date, _, _, s, _ in parts:
        s = s.copy()
        s.insert(0, "date", date)
        s.insert(0, "folder", folder)
        st.append(s)
    st = pd.concat(st, ignore_index=True, sort=False)
    ov = pd.DataFrame([dict(folder=folder, date=date, results_folder=res,
                            n_files=int(pf_["file"].nunique()), n_images=len(ps),
                            samples=", ".join(dict.fromkeys(pf_["sample"].astype(str))))
                       for folder, res, date, ps, pf_, _, _ in parts])
    sheets = (("per slice", per_slice, (1, 4)), ("per file", per_file, (1, 4)),
              ("settings", st, (1, 2)), ("folders", ov, (1, 1)), ("quality", quality, (1, 3)))
    try:
        import openpyxl  # noqa: F401
        path = base + ".xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            for sheet, frame, freeze in sheets:
                _clean(frame).to_excel(xw, sheet_name=sheet, index=False, freeze_panes=freeze)
                ws = xw.sheets[sheet]
                for k, col in enumerate(frame.columns, start=1):
                    w = min(max(len(str(col)), 10) + 2, 45)
                    ws.column_dimensions[openpyxl.utils.get_column_letter(k)].width = w
        return path
    except ImportError:
        for sheet, frame, _ in sheets:
            frame.to_csv(f"{base}_{sheet.replace(' ', '_')}.csv", index=False)
        return base + "_per_slice.csv"


def results_tag(files):
    """Name of a folder's results folder: <mmddyy>_results from the dates at the start of the
    file names (mmddyy_##_...). Several dates -> first-last (chronological); no date -> 'other'."""
    dates = sorted({m.group(1) for m in (re.match(r"^(\d{6})_\d+_", os.path.basename(f)) for f in files) if m},
                   key=lambda d: (d[4:], d[:4]))
    if not dates:
        return "other_results"
    return f"{dates[0]}_results" if len(dates) == 1 else f"{dates[0]}-{dates[-1]}_results"


# ----------------------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="*",
                    help="one or more folders with .nd2/.tif files; each is analysed separately into "
                         "<folder>/<mmddyy>_results, and with several folders a combined summary.xlsx "
                         "is written to their common parent folder")
    ap.add_argument("--out", default=None,
                    help="one folder: results folder (default <folder>/<mmddyy>_results); "
                         "several folders: path of the combined summary (default <common parent>/summary.xlsx)")
    ap.add_argument("--averages-only", action="store_true",
                    help="only the comparisons between files and one figure per file (no per-file folders "
                         "with a figure of every analysed image); faster")
    ap.add_argument("--file", default=None,
                    help="analyse only this file of the folder (name with or without extension) and write a "
                         "detailed report to <mmddyy>_results/<file name>/: a figure of every analysed image, the "
                         "per-file figure and graphs comparing the images")
    ap.add_argument("--zstep", type=int, default=1, help="analyse every Nth z-slice (default 1 = every slice)")
    ap.add_argument("--tstep", type=int, default=1, help="time-lapses: analyse every Nth time point (default 1)")
    ap.add_argument("--stack-axis", choices=["auto", "z", "t"], default="auto",
                    help="for 3-D files: treat the third axis as depth (z) or time (t); default: from the file")
    ap.add_argument("--t-interval", type=float, default=None,
                    help="time between time points, s (only if the file does not contain it, e.g. some TIFFs)")
    ap.add_argument("--threshold", type=float, default=None,
                    help="fixed ridge threshold for every file (overrides --threshold-mode)")
    ap.add_argument("--fiber-model", default=None,
                    help="trained fiber model (.joblib, made on the app's Fiber model page): the fiber mask is "
                         "'fiber probability > --fiber-cutoff' instead of a ridge threshold")
    ap.add_argument("--fiber-cutoff", type=float, default=0.5,
                    help="probability above which a pixel is fiber, with --fiber-model (default 0.5)")
    ap.add_argument("--threshold-mode", choices=["per-file", "shared"], default="per-file",
                    help="per-file: Otsu threshold of each file (default); shared: one Otsu threshold "
                         "pooled over all files")
    ap.add_argument("--hotspot", default="auto", help="'auto', 'none', or 'x,y,r' in pixels")
    ap.add_argument("--after-pattern", default="AMF", help="text in a file name that marks 'After AMF'")
    ap.add_argument("--sigma-bg-um", type=float, default=2.5, help="flattening blur, µm (default 2.5)")
    ap.add_argument("--ridge-sigmas-um", default="0.1,0.15,0.2",
                    help="Sato ridge-filter scales (fiber half-widths), µm (default 0.1,0.15,0.2)")
    ap.add_argument("--sigma-st-um", type=float, default=0.4, help="structure tensor window, µm (default 0.4)")
    ap.add_argument("--min-obj-um2", type=float, default=0.32,
                    help="remove mask objects this small or smaller, µm² (default 0.32)")
    ap.add_argument("--tile-um", type=float, default=13.0, help="tile size for tile CV, µm (default 13)")
    ap.add_argument("--edge-um", type=float, default=1.6, help="excluded image border, µm (default 1.6)")
    ap.add_argument("--min-fiber-length-um", type=float, default=0.5,
                    help="shortest fiber segment counted; shorter side branches are pruned, µm (default 0.5)")
    ap.add_argument("--pore-min-dist-um", type=float, default=0.5,
                    help="minimum spacing of distance-map maxima, µm (default 0.5)")
    ap.add_argument("--hotspot-sigma-um", type=float, default=4.0,
                    help="blur used to detect the reflection hotspot, µm (default 4)")
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)),
                    help="slices analysed in parallel (default: CPU cores - 1, max 8)")
    ap.add_argument("--share-y", action="store_true",
                    help="same y-axis in every panel of the mmddyy_ graphs (default: own axis each)")
    ap.add_argument("--no-progress", action="store_true", help="hide the progress bar")
    ap.add_argument("--pixel-size", type=float, default=None, help="um/px, TIFF only")
    ap.add_argument("--z-step", type=float, default=None, help="um, TIFF only")
    ap.add_argument("--files-from", default=None,
                    help="text file with one image file path per line: analyse exactly these files "
                         "(grouped by their folders; used by the app)")
    ap.add_argument("--results-root", default=None,
                    help="save all results in this folder instead of next to the data: "
                         "<root>/<mmddyy>_results (one data folder) or <root>/<data folder name>/<mmddyy>_results")
    ap.add_argument("--figures", default="all",
                    help="figures to make: 'all', 'none' or a comma list of " + ", ".join(FIGURE_KEYS))
    ap.add_argument("--fig-format", choices=["png", "pdf", "svg", "tif"], default="png", help="figure file format")
    ap.add_argument("--dpi", type=int, default=None, help="figure resolution (default: 90-120 depending on figure)")
    ap.add_argument("--metadata", default=None,
                    help="JSON file with values entered by hand (they replace the values read from the file names) "
                         "and added parameters; written by the app")
    ap.add_argument("--no-tiles", action="store_true",
                    help="do not write the per-tile table (mmddyy_tiles.csv: metrics of every --tile-um tile)")
    ap.add_argument("--qc-model", default=None,
                    help="trained quality-check model (.joblib, made on the app's Models page): adds the "
                         "probability that a file has a problem to the 'quality' sheet")
    ap.add_argument("--cloud-wait-min", type=float, default=15,
                    help="files in iCloud Drive that are not on this computer are downloaded first; wait up to "
                         "this long for a folder's downloads before moving it to the end of the queue (default 15)")
    ap.add_argument("--cloud-retries", type=int, default=3,
                    help="how many times a folder whose files are not available is tried before it is skipped "
                         "(default 3)")
    ap.add_argument("--keep-downloaded", action="store_true",
                    help="keep the local copies of files that had to be downloaded from iCloud (default: after "
                         "each folder they are removed again to free disk space; the files stay in iCloud)")
    ap.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    ap = build_parser()
    P = ap.parse_args(argv)
    FIG["gui"] = P.gui
    if P.metadata:
        import json
        try:
            with open(P.metadata, encoding="utf-8") as fh:
                METADATA.update(json.load(fh))
        except (OSError, ValueError) as e:
            ap.error(f"--metadata: {e}")
        pos = LABEL_COLS.index("other") + 1           # added parameters become label columns after the note
        for k in reversed(user_fields()):
            if k not in LABEL_COLS:
                LABEL_COLS.insert(pos, k)
    FIG["fmt"], FIG["dpi"] = P.fig_format, P.dpi
    figs = P.figures.strip().lower()
    if figs == "all":
        FIG["keys"] = set(FIGURE_KEYS)
    elif figs in ("none", ""):
        FIG["keys"] = set()
    else:
        FIG["keys"] = {k.strip() for k in figs.split(",") if k.strip()}
        bad = FIG["keys"] - set(FIGURE_KEYS)
        if bad:
            ap.error(f"--figures: unknown {', '.join(sorted(bad))}; choose from {', '.join(FIGURE_KEYS)}")
    if P.fiber_model:
        if not os.path.isfile(P.fiber_model):
            ap.error(f"--fiber-model: file not found: {P.fiber_model}")
        try:
            FM.load_model(P.fiber_model)
        except Exception as e:
            ap.error(f"--fiber-model: {e}")
        if not 0 < P.fiber_cutoff < 1:
            ap.error("--fiber-cutoff must be between 0 and 1")
    if P.qc_model and not os.path.isfile(P.qc_model):
        ap.error(f"--qc-model: file not found: {P.qc_model}")
    if P.dpi is not None and not 30 <= P.dpi <= 1200:
        ap.error("--dpi must be between 30 and 1200")
    try:
        P.ridge_sigmas_um = [float(v) for v in P.ridge_sigmas_um.split(",")]
    except ValueError:
        ap.error("--ridge-sigmas-um must be numbers separated by commas, e.g. 0.1,0.15,0.2")
    # ---- validate input
    if P.zstep < 1:
        ap.error("--zstep must be >= 1")
    if P.tstep < 1:
        ap.error("--tstep must be >= 1")
    if P.t_interval is not None and P.t_interval <= 0:
        ap.error("--t-interval must be > 0")
    if P.workers < 1:
        ap.error("--workers must be >= 1")
    if P.cloud_wait_min <= 0:
        ap.error("--cloud-wait-min must be > 0")
    if P.cloud_retries < 1:
        ap.error("--cloud-retries must be >= 1")
    CLOUD["timeout"] = P.cloud_wait_min * 60
    for nm in ("sigma_bg_um", "sigma_st_um", "tile_um", "edge_um", "pore_min_dist_um", "hotspot_sigma_um",
               "min_fiber_length_um"):
        if getattr(P, nm) <= 0:
            ap.error(f"--{nm.replace('_', '-')} must be > 0")
    if P.min_obj_um2 < 0:
        ap.error("--min-obj-um2 must be >= 0")
    if not P.ridge_sigmas_um or min(P.ridge_sigmas_um) <= 0:
        ap.error("--ridge-sigmas-um must all be > 0")
    if P.threshold is not None and P.threshold <= 0:
        ap.error("--threshold must be > 0")
    for nm in ("pixel_size", "z_step"):
        if getattr(P, nm) is not None and getattr(P, nm) <= 0:
            ap.error(f"--{nm.replace('_', '-')} must be > 0")
    try:
        re.compile(P.after_pattern)
    except re.error as e:
        ap.error(f"--after-pattern is not a valid pattern: {e}")
    if P.hotspot not in ("auto", "none"):
        try:
            hs_given = tuple(int(v) for v in P.hotspot.split(","))
            assert len(hs_given) == 3 and hs_given[2] > 0
        except (ValueError, AssertionError):
            ap.error("--hotspot must be 'auto', 'none' or x,y,r in pixels, e.g. 499,518,170")
    else:
        hs_given = None
    chosen = None
    if P.files_from:
        try:
            with open(P.files_from, encoding="utf-8") as fh:
                chosen = [os.path.abspath(l.strip()) for l in fh if l.strip()]
        except OSError as e:
            ap.error(f"--files-from: {e}")
        missing = [f for f in chosen if not os.path.isfile(f)]
        if missing:
            ap.error("file(s) not found: " + "; ".join(missing[:5]))
        P.folder = list(P.folder) + [os.path.dirname(f) for f in chosen]
    if not P.folder:
        ap.error("give at least one folder (or --files-from)")
    folders = list(dict.fromkeys(os.path.abspath(os.path.expanduser(f)) for f in P.folder))
    for fo in folders:
        if not os.path.isdir(fo):
            ap.error(f"folder not found: {fo}")
    jobs = []                                        # (folder, its image files)
    for fo in folders:
        files = folder_files(fo)
        if chosen is not None:
            files = [f for f in files if os.path.abspath(f) in set(chosen)]
        if P.file:
            files = match_file(files, P.file)
        if files:
            jobs.append((fo, files))
        elif P.file and len(folders) > 1:
            log(f"--file: '{P.file}' not in {fo}; folder skipped")
        elif P.file:
            allf = folder_files(fo)
            sys.exit(f"--file: '{P.file}' not found in {fo}. Files there:\n  "
                     + "\n  ".join(os.path.basename(f) for f in allf))
        else:
            log(f"WARNING: no .nd2/.tif files in {fo}; folder skipped")
    if not jobs:
        sys.exit("no .nd2/.tif files found" + (f" matching --file '{P.file}'" if P.file else ""))
    multi = len(folders) > 1
    t_all = time.time()
    parts_of = {}                                    # folder number -> its date summaries
    queue = list(enumerate(jobs, 1))
    tries, skipped = {}, []
    while queue:
        k, (fo, files) = queue.pop(0)
        tries[k] = tries.get(k, 0) + 1
        if multi:
            log(f"=== folder {k}/{len(jobs)}: {fo}" + (f" (try {tries[k]} of {P.cloud_retries})" if tries[k] > 1 else ""))
        elif tries[k] > 1:
            log(f"=== try {tries[k]} of {P.cloud_retries}")
        if P.results_root:
            sub = os.path.basename(fo.rstrip("/\\")) or f"folder{k}"
            names = [os.path.basename(f2.rstrip("/\\")) for f2, _ in jobs]
            if names.count(sub) > 1:
                sub = f"{sub}_{k}"
            out = os.path.join(P.results_root, results_tag(files)) if not multi \
                else os.path.join(P.results_root, sub, results_tag(files))
        else:
            out = os.path.join(fo, results_tag(files)) if (multi or not P.out) else P.out
        # files in iCloud only: download the whole folder first (all at once), then analyse it
        problems = CF.wait_all(files, CLOUD["timeout"], log=log)
        if not problems:
            try:
                parts_of[k] = [(fo, res, date) + tuple(frames)
                               for res, date, frames in analyse_folder(P, fo, files, out, hs_given)]
            except OSError as e:                     # e.g. a file removed from this computer during the run
                bad = e if isinstance(e, CF.FileUnavailable) else None
                if bad is None:
                    gone = [f for f in files if CF.state(f) != "ok"]
                    bad = CF.FileUnavailable(gone[0] if gone else getattr(e, "filename", None) or fo,
                                             str(e), permanent=not gone)
                problems = [(bad.path, bad)]
        if not P.keep_downloaded:
            CF.evict(files, log=log)
        if not problems:
            continue
        for path, e in problems:
            log(f"  WARNING: {e}")
        permanent = any(e.permanent for _, e in problems)
        if not permanent and tries[k] < P.cloud_retries:
            log("  WARNING: folder moved to the end of the queue; it is tried again after the other folders")
            queue.append((k, (fo, files)))
        else:
            log("  ERROR: folder skipped" + ("" if permanent else f" after {tries[k]} tries"))
            skipped.append((fo, problems))
    parts = [p for k in sorted(parts_of) for p in parts_of[k]]
    if skipped:
        log(f"WARNING: {len(skipped)} folder(s) were not analysed:")
        for fo, problems in skipped:
            log(f"  WARNING: {fo}: " + "; ".join(str(e) for _, e in problems))
        log("  Run them again once their files are on this computer (or copied again, if a file is broken).")
    if not parts_of:
        sys.exit("no folder could be analysed (see the warnings above)")
    if multi:
        if P.out:
            base = P.out if not os.path.isdir(P.out) else os.path.join(P.out, "summary")
            base = os.path.splitext(base)[0]
        elif P.results_root:
            base = os.path.join(P.results_root, "summary")
        else:
            base = os.path.join(os.path.commonpath([fo for fo, _ in jobs]), "summary")
        os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
        written = write_combined(base, parts)
        log(f"combined summary of {len(parts_of)} folder(s) -> {written}")
        emit("OUT", "summary", written)
        log(f"Total run time for all folders: {Progress._t(time.time() - t_all)} (min:s)")
    emit("DONE", round(time.time() - t_all, 1))


def folder_files(folder):
    return sorted(sum([glob.glob(os.path.join(folder, e)) for e in ("*.nd2", "*.tif", "*.tiff")], []))


def match_file(files, want):
    want = os.path.basename(want)
    return [f for f in files if os.path.basename(f) == want or os.path.splitext(os.path.basename(f))[0] == want][:1]


def analyse_folder(P, folder, files, out, hs_given):
    """Analyse the image files of one folder into <out>. Returns a list of
    (results folder, date, (per slice, per file, settings)) for the date summaries."""
    single_file = None
    detail = not P.averages_only
    if P.file:
        single_file = os.path.splitext(os.path.basename(files[0]))[0]
        out = os.path.join(out, single_file)
        os.makedirs(os.path.join(out, "slices"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    if P.fiber_model:
        threshold_source = (f"trained fiber model {os.path.basename(P.fiber_model)}: fiber = probability > "
                            f"{P.fiber_cutoff:g} (the ±10 % QC columns move this cutoff)")
        fm_bg = FM.load_model(P.fiber_model).get("sigma_bg_um")
        if fm_bg is not None and abs(fm_bg - P.sigma_bg_um) > 1e-6:
            log(f"WARNING: the fiber model was trained with background flattening {fm_bg:g} µm, this run uses "
                f"{P.sigma_bg_um:g} µm — the model may not fit")
    elif P.threshold is not None:
        threshold_source = "fixed value (--threshold), same for every file"
    elif P.threshold_mode == "shared":
        threshold_source = "Otsu pooled over all files, same for every file"
    else:
        threshold_source = "Otsu computed separately for each file"

    # ---- pass 1: names, hotspot, valid area, threshold sample (one stack in memory at a time)
    t_start = time.time()
    t_phase = {}
    PROGRESS.enabled = not P.no_progress
    log(f"{len(files)} file(s) found in {folder}")
    data, pool = {}, []
    PROGRESS.start("1/3 reading files", len(files))
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        PROGRESS.set_item(name)
        date, smp, cond = parse_name(name, P.after_pattern)
        man = manual(name)
        cond = man.get("condition", cond)
        log(f"reading {name}")
        s, px, dz, times = load_stack(f, P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
        G = geometry(px, P)
        kind = series_kind(s.shape)
        frames = frames_to_analyse(s.shape, P.zstep, P.tstep)
        if P.hotspot == "auto":
            hs = find_hotspot(s.reshape(-1, *s.shape[2:]), sigma=G["hotspot_sigma"])
        elif P.hotspot == "none":
            hs = None
        else:
            hs = hs_given
        valid = analysis_mask(s.shape[2:], hs, edge=G["edge"])
        if valid.sum() < 0.2 * valid.size:
            log(f"  WARNING: only {100 * valid.mean():.0f}% of the image is analysed (edge/hotspot exclusion)")
        tinfo = (f", {s.shape[0]} time points" + (f" over {times[-1]:.0f} s" if times is not None else " (no timing in file)")
                 if kind == "t" else "")
        log(f"  date={date} sample={smp} {cond} {parse_amf(name) if cond == 'After AMF' else ''}; "
            f"{SERIES_NAME[kind]} (T,Z,Y,X = {s.shape}){tinfo}, px={px:.4f} um, dz={dz} um, hotspot={hs}; "
            f"{len(frames)} image(s) to analyse")
        hz, sec = parse_amf(name) if cond == "After AMF" else (None, None)
        hz, sec = man.get("amf_hz", hz), man.get("amf_s", sec)
        if cond == "After AMF" and (hz is None or sec is None):
            log(f"  warning: AMF {'frequency' if hz is None else 'time'} not found in the file name")
        vpp, vm = parse_amf_voltage(name) if cond == "After AMF" else (None, None)
        vpp, vm = man.get("amf_vpp", vpp), man.get("amf_v", vm)
        if cond == "After AMF":
            log(f"  AMF voltage: Vp-p set {fmt_volt(vpp) or 'not in file name (expected e.g. _10Vpp_)'}; "
                f"measured {fmt_volt(vm) or 'not in file name (expected e.g. _8.5V_)'}")
        elif any(parse_amf_voltage(name)):
            log("  note: AMF voltage in the name of a Before-AMF file; not recorded")
        coil = parse_amf_coil(name) if cond == "After AMF" else dict.fromkeys(("amf_app", "coil_turns", "amf_b",
                                                                              "amf_b_type"))
        coil = {k: man.get(k, v) for k, v in coil.items()}
        if coil["coil_turns"] is not None:
            coil["coil_turns"] = int(round(float(coil["coil_turns"])))
        if cond == "After AMF" and any(v is not None for v in coil.values()):
            log(f"  AMF coil/field: Ap-p set {fmt_volt(coil['amf_app'], 'A') or '-'}; coil "
                f"{coil['coil_turns'] or '-'} turns; field {fmt_volt(coil['amf_b'], 'mT') or '-'} "
                f"{coil['amf_b_type'] or ''}")
            if coil["amf_b"] is not None and not coil["amf_b_type"]:
                log("  warning: field given without peak/rms (write e.g. _40mTpk_ or _28mTrms_); "
                    "amf_b_peak_mT left empty")
        cmg = parse_collagen(name)
        nc_nm = parse_nc(name)
        mix, inc_s = parse_mixing(name)
        other = parse_other(name)
        cmg, nc_nm = man.get("collagen_mgml", cmg), man.get("nc_nM", nc_nm)
        mix, inc_s, other = man.get("mixing", mix), man.get("inc_s", inc_s), man.get("other", other)
        set_by_hand = [k for k in MANUAL_KEYS if k in man]
        custom = dict(man.get("custom", {}))
        if set_by_hand:
            log("  set in the app (instead of the file name): " + ", ".join(f"{MANUAL_KEYS[k]} = {man[k]}" for k in set_by_hand))
        if custom:
            log("  added parameters: " + ", ".join(f"{k} = {v}" for k, v in custom.items()))
        log(f"  nanocages: {fmt_conc(nc_nm) or 'concentration not in file name (expected _NC_#nM_)'}; "
            f"mixing: {mixing_text(dict(mixing=mix, inc_s=inc_s)) or 'not in file name (expected _premixed_ or _inc#min_)'}"
            + (f"; note: {other}" if other else ""))
        if mix and "incubated" in mix and inc_s is None:
            log("  warning: incubation time not found (expected e.g. _inc45min_ or _incubated_1h_)")
        log(f"  collagen: {f'{cmg:g} mg/mL' if cmg is not None else 'not found in the file name (expected _Collagen_#mgml_)'}")
        fmeta, smeta = read_nd2_metadata(f) if f.lower().endswith(".nd2") else ({}, {})
        if name in data:
            sys.exit(f"two files share the name '{name}' (e.g. .nd2 and .tif); keep only one")
        data[name] = dict(name=name, path=f, date=date, sample=smp, condition=cond, manual=set_by_hand, custom=custom,
                          amf_hz=hz, amf_s=sec, amf_vpp=vpp, amf_v=vm, **coil, collagen_mgml=cmg,
                          nc_nM=nc_nm, mixing=mix, inc_s=inc_s, other=other,
                          px=px, dz=dz, hotspot=hs, valid=valid, nt=s.shape[0], nz=s.shape[1],
                          kind=kind, times=times, frames=frames,
                          shape=s.shape, G=G, meta=fmeta, slice_meta=smeta,
                          zpos=None, zsrc="")
        data[name]["zpos"], data[name]["zsrc"] = z_positions(smeta, s.shape[0], s.shape[1], dz, name)
        if P.threshold is None and not P.fiber_model:
            # ridge values of up to 12 analysed images spread through the file. Per-file threshold:
            # all analysed pixels of those images (no random sampling, so it is exactly
            # reproducible). Shared threshold: a fixed-seed sample of each file is pooled.
            fpool, rng = [], np.random.default_rng(0)
            pick = sorted(set(np.linspace(0, len(frames) - 1, min(len(frames), 12)).round().astype(int)))
            for k in pick:
                t, z = frames[k]
                r = ridge(flatten(s[t, z], G["sigma_bg"]), G["ridge_sigmas"])[valid].astype(np.float32)
                fpool.append(r if P.threshold_mode == "per-file" else rng.choice(r, min(100000, r.size), replace=False))
            fpool = np.concatenate(fpool)
            if P.threshold_mode == "per-file":
                data[name]["thr"] = otsu_threshold(fpool)
                log(f"  Otsu threshold for this file = {data[name]['thr']:.5f}")
            else:
                pool.append(fpool)
            del fpool
        del s
        PROGRESS.step()
    PROGRESS.finish()
    t_phase["reading files"] = time.time() - t_start
    if P.fiber_model:
        for d in data.values():
            d["thr"] = P.fiber_cutoff
    elif P.threshold is not None:
        for d in data.values():
            d["thr"] = P.threshold
    elif P.threshold_mode == "shared":
        t = otsu_threshold(np.concatenate(pool))
        log(f"pooled Otsu threshold = {t:.5f}  (pass --threshold to reuse)")
        for d in data.values():
            d["thr"] = t
    del pool
    # imaging settings must match across every file that is compared
    check_settings(list(data), data, "between files in this folder")

    # ---- pass 2: analyse every file
    rows, tile_rows = [], []
    # work units: each analysed slice + median slice + per-file figure
    PROGRESS.start("2/3 analysing slices",
                   sum(len(d["frames"]) + 2 for d in data.values()))
    t0 = time.time()
    log(f"analysing slices with {P.workers} parallel worker(s)")
    import multiprocessing
    ctx = multiprocessing.get_context(os.environ.get("COLLAGEN_MP_START") or None)   # e.g. "spawn" for testing
    pool_ex = ProcessPoolExecutor(max_workers=P.workers, mp_context=ctx) if P.workers > 1 else None
    try:
        for name, d in data.items():
            PROGRESS.set_item(name)
            s, px2, _, _ = load_stack(d["path"], P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
            if s.shape != d["shape"] or abs(px2 - d["px"]) > 1e-9:
                sys.exit(f"{name}: file changed while the script was running; run again")
            frames = d["frames"]
            z_um = lambda t, z, zp=d["zpos"]: float(zp[z])
            log(f"analysing {name}: {len(frames)} image(s) ({SERIES_NAME[d['kind']]})")
            # detailed mode: every file gets its own folder results/<file>/ with slices/
            file_out = out if (single_file or not detail) else os.path.join(out, name)
            d["file_out"] = file_out
            os.makedirs(file_out, exist_ok=True)
            slice_figs = detail and fig_on("slices")
            if slice_figs:
                os.makedirs(os.path.join(file_out, "slices"), exist_ok=True)
            if slice_figs:
                tt = d["times"]
                def spec_for(t, z):
                    parts, tags = [], []
                    if d["kind"] == "t":
                        parts.append(f"time point {t}" + (f" (t = {tt[t]:.1f} s)" if tt is not None else ""))
                        tags.append(f"t{t:04d}")
                    if d["nz"] > 1:
                        parts.append(f"z = {z} ({z_um(t, z):.3f} µm)"); tags.append(f"z{z:03d}")
                    tag = "_".join(tags) or "image"
                    return dict(path=os.path.join(file_out, "slices", f"{name}_{tag}.png"), hotspot=d["hotspot"],
                                title=" | ".join(x for x in (collagen_text(d), nc_text(d), condition_text(d), f"sample {d['sample']}") if x)
                                      + f"\n{name} — " + (", ".join(parts) or "single image"),
                                fig=dict(fmt=FIG["fmt"], dpi=FIG["dpi"]))
                jobs = [((t, z), s[t, z], d["valid"], d["thr"], d["G"], spec_for(t, z)) for t, z in frames]
            else:
                jobs = [((t, z), s[t, z], d["valid"], d["thr"], d["G"]) for t, z in frames]
            results = pool_ex.map(_slice_job, jobs) if pool_ex else map(_slice_job, jobs)
            ah, lh, zsum, nfib, frows = np.zeros(ANGLE_BINS), np.zeros(len(LT_EDGES) - 1), 0j, 0, []
            frame_hists = []
            fh = np.zeros(len(FIB_EDGES) - 1)
            sh = np.zeros(len(SEG_EDGES) - 1)
            for (t, z), res, aux in results:
                res.update(date=d["date"], sample=d["sample"], file=name, condition=d["condition"],
                           collagen_mg_ml=d["collagen_mgml"], nc_conc_nM=d["nc_nM"], nc_mixing=d["mixing"],
                           nc_incubation_time=fmt_duration(d["inc_s"]), nc_incubation_time_s=d["inc_s"],
                           other=d["other"], **{k: d["custom"].get(k) for k in user_fields()},
                           amf_freq_hz=d["amf_hz"], amf_time=fmt_time(d["amf_s"]), amf_time_s=d["amf_s"],
                           amf_vpp_set=d["amf_vpp"], amf_v_measured=d["amf_v"], amf_app_set=d["amf_app"],
                           amf_coil_turns=d["coil_turns"], amf_b_mT=d["amf_b"], amf_b_type=d["amf_b_type"],
                           amf_b_peak_mT=b_peak(d["amf_b"], d["amf_b_type"]),
                           amf_ampere_turns_pp=(round(d["amf_app"] * d["coil_turns"], 6)
                                                if d["amf_app"] is not None and d["coil_turns"] else None),
                           series=SERIES_NAME[d["kind"]], t_index=t,
                           time_s=round(float(d["times"][t]), 3) if d["times"] is not None else np.nan,
                           z_index=z, z_um=round(z_um(t, z), 4), threshold=d["thr"])
                frows.append(res)
                for tr in aux.get("tiles", ()):
                    tile_rows.append(dict(file=name, t_index=t, z_index=z, **tr))
                ah += aux["angle_hist"]; lh += aux["lt_hist"]; zsum += aux["zsum"]; nfib += aux["n"]
                fh += aux["fib_hist"]; sh += aux["seg_hist"]
                frame_hists.append({k: aux[k] for k in ("lt_hist", "fib_hist", "seg_hist", "angle_hist")})
                frame_hists[-1]["tz"] = (t, z)
                PROGRESS.step(item=f"{name}  " + (f"t={t} " if d["kind"] == "t" else "") + f"z={z}")
            S, ang = pooled_orientation(zsum, nfib)
            for res in frows:                       # file-level values, then metadata columns
                res.update(file_order_S_pooled=S, file_mean_angle_deg_pooled=ang)
                res.update(d["meta"]); res.update(d["slice_meta"].get((res["t_index"], res["z_index"]), {}))
            # images for the figures
            zm = d["nz"] // 2
            tunit = (60.0, "min") if d["times"] is not None and d["times"][-1] > 120 else (1.0, "s")
            ttxt = lambda t: (f"t = {d['times'][t] / tunit[0]:.1f} {tunit[1]}" if d["times"] is not None
                              else f"time point {t}")
            if d["kind"] == "z":
                ta, img_b = 0, s[0].max(0)
                d.update(frame_a_text=f"z = {zm} (middle slice)", img_a_title=f"Optical section, z = {zm} (middle slice)",
                         img_b_title=f"Max projection of all {d['nz']} z-slices",
                         thumb_title="max projection")
                thumb = img_b
            elif d["kind"] == "t":
                ta, img_b = 0, s[-1, zm]
                zt = f", z = {zm}" if d["nz"] > 1 else ""
                d.update(frame_a_text=f"first time point{zt}", img_a_title=f"First time point ({ttxt(0)}{zt})",
                         img_b_title=f"Last time point ({ttxt(d['nt'] - 1)}{zt})",
                         thumb_title=f"first time point ({ttxt(0)})")
                thumb = s[0, zm]
            else:
                ta, img_b = 0, s[0, 0]
                d.update(frame_a_text="single image", img_a_title="Image",
                         img_b_title="Image with excluded areas", thumb_title="image")
                thumb = img_b
            if fig_on("file"):
                _, d["aux"] = analyse_slice(s[ta, zm], d["valid"], d["thr"], d["G"], full=True)
            PROGRESS.step(item=f"{name}  figure images")
            d.update(img_a=s[ta, zm].copy(), img_b=img_b.copy(), thumb=thumb.copy(), angle_hist=ah, lt_hist=lh,
                     fib_hist=fh, seg_hist=sh, S=S, angle=ang, frame_hists=frame_hists)
            rows += frows
            if fig_on("file"):
                file_figure(d, pd.DataFrame(frows), file_out)
            if detail and len(frows) > 1 and fig_on("slice_comparison"):
                file_detail_figures(d, pd.DataFrame(frows), frame_hists, file_out)
            PROGRESS.step(item=f"{name} done")
            del s
            for k in ("aux", "img_a", "img_b"):         # keep only what the sample figures need
                d.pop(k, None)
    finally:
        if pool_ex:
            pool_ex.shutdown()
    PROGRESS.finish()
    t_phase["analysing slices"] = time.time() - t0

    per = pd.DataFrame(rows)
    # ---- quality check per file (rules + optional trained model); file-level result on every row
    quality = QC.assess(per, P.qc_model, log=lambda *a: log(*a))
    qmap = quality.set_index("file")
    per["qc_status"] = per["file"].map(qmap["qc_status"])
    per["qc_flags"] = per["file"].map(qmap["qc_flags"])
    n_check = int((quality["qc_status"] == "check").sum())
    log(f"quality check: {len(quality) - n_check} file(s) ok, {n_check} to check")
    for _, q in quality[quality["qc_status"] == "check"].iterrows():
        log(f"  WARNING (QC) {q['file']}: {q['qc_flags']}")
    for _, q in quality[(quality["qc_status"] == "ok") & (quality["label_from_note"] == "problem")].iterrows():
        log(f"  note (QC) {q['file']}: the file name mentions a problem ({q['other']}) but no QC rule flagged it")
    metric_cols = [c for c in per.columns if c not in LABEL_COLS and not c.startswith(("meta_", "slice_"))]
    meta_cols = [c for c in per.columns if c.startswith("meta_")]
    slice_cols = [c for c in per.columns if c.startswith("slice_")]
    per = per[LABEL_COLS + metric_cols + meta_cols + slice_cols]
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    # per-tile table: tile metrics + the labels of their image
    tiles = pd.DataFrame(tile_rows)
    if len(tiles):
        lab = [c for c in LABEL_COLS if c not in ("file", "t_index", "z_index", "threshold", "qc_flags")]
        tiles = per[["file", "t_index", "z_index"] + lab].merge(tiles, on=["file", "t_index", "z_index"])
        tiles = tiles[["file", "t_index", "z_index"] + lab + [c for c in QC.TILE_COLS if c in tiles]]
    del tile_rows

    # ---- per date: summary CSV, settings CSV, sample figures, side-by-side figures
    dates = sorted(set(d["date"] for d in data.values()))
    n_samples = len(set(d["sample"] for d in data.values()))
    t0 = time.time()
    sheets = []
    PROGRESS.start("3/3 figures, spreadsheets", n_samples + len(dates))
    for date in dates:
        dfiles = [n for n, d in data.items() if d["date"] == date]
        samples = {}
        for smp in sorted(set(data[n]["sample"] for n in dfiles)):
            samples[smp] = order_files([n for n in dfiles if data[n]["sample"] == smp], data)
            check_settings(samples[smp], data, f"within sample {smp}")
            if len({data[n]["collagen_mgml"] for n in samples[smp]}) > 1:
                log(f"  WARNING: collagen concentration differs within sample {smp}: "
                    + ", ".join(f"{file_label(data[n])} {collagen_text(data[n]) or '?'}" for n in samples[smp]))
            for key, what, fmt in (("nc_nM", "nanocage concentration", lambda d: fmt_conc(d["nc_nM"])),
                                   ("mixing", "NC mixing condition", mixing_text)):
                if len({(data[n][key], data[n]["inc_s"] if key == "mixing" else 0) for n in samples[smp]}) > 1:
                    log(f"  WARNING: {what} differs within sample {smp}: "
                        + ", ".join(f"{file_label(data[n])} {fmt(data[n]) or '?'}" for n in samples[smp]))
            log(f"sample {smp}: {', '.join(file_label(data[n]) for n in samples[smp])}")
            PROGRESS.set_item(f"sample_{smp}.png")
            if not single_file and fig_on("sample"):
                sample_figure(smp, samples[smp], data, per, P, out)
            PROGRESS.step()
        PROGRESS.set_item(f"{date} figures and spreadsheets")
        prefix = single_file or date
        if not single_file:
            date_figures(date, samples, data, per, out, P.share_y)
        g_rows = global_settings(P, threshold_source, run_time, single_file, detail)
        f_rows = [r for n in dfiles for r in file_settings(data[n], threshold_source)]
        qd = quality[quality["file"].isin(dfiles)]
        sheets.append((os.path.join(out, f"{prefix}_summary"), per[per.date == date], g_rows + f_rows, date, qd))
        if len(tiles):
            tpath = os.path.join(out, f"{prefix}_tiles.csv")
            tiles[tiles["file"].isin(dfiles)].to_csv(tpath, index=False)
            log(f"  saved {os.path.relpath(tpath, out)} ({int(tiles['file'].isin(dfiles).sum())} tiles)")
            emit("OUT", "tiles", tpath)
        save_distributions(os.path.join(out, f"{prefix}_distributions.npz"), dfiles, data)
        if detail and not single_file:          # each file's own workbook in its folder
            for n in dfiles:
                sheets.append((os.path.join(data[n]["file_out"], f"{n}_summary"), per[per.file == n],
                               g_rows + file_settings(data[n], threshold_source), None,
                               quality[quality["file"] == n]))
        PROGRESS.step()
    PROGRESS.finish()
    t_phase["figures and spreadsheets"] = time.time() - t0
    total = time.time() - t_start
    n_slices = len(per)
    timing = (f"total {Progress._t(total)} (min:s) for {len(data)} file(s), {n_slices} analysed images; "
              + ", ".join(f"{k} {Progress._t(v)}" for k, v in t_phase.items())
              + (f"; {total / n_slices:.1f} s per image" if n_slices else ""))
    summaries = []
    for path, df_, rows_, date, qd in sheets:
        written, *frames = write_spreadsheet(path, df_, rows_ + [("run_duration_s", round(total, 1), timing)], qd)
        log(f"  saved {os.path.relpath(written, out)}")
        emit("OUT", "workbook" if date is not None else "file_workbook", written)
        if date is not None:
            summaries.append((out, date, frames))
    log("done ->", out)
    log(f"Run time: {timing}")
    emit("OUT", "results", out)
    return summaries


def save_distributions(path, names, data):
    """Histograms behind the distribution graphs, for re-plotting (e.g. in the app):
    per file pooled over all analysed images ('<file>|lt' ...) and per image ('<file>|t|z|lt' ...),
    plus the bin edges. lt = pore size (area-weighted), fib = fiber size, seg = fiber length,
    ang = fiber angle (5° bins, -90..90)."""
    arr = dict(lt_edges=LT_EDGES, fib_edges=FIB_EDGES, seg_edges=SEG_EDGES,
               ang_edges=np.linspace(-90, 90, ANGLE_BINS + 1))
    for n in names:
        d = data[n]
        for k, key in (("lt", "lt_hist"), ("fib", "fib_hist"), ("seg", "seg_hist"), ("ang", "angle_hist")):
            arr[f"{n}|{k}"] = np.asarray(d[key], np.float64)
            for h in d.get("frame_hists", []):
                t, z = h["tz"]
                arr[f"{n}|{t}|{z}|{k}"] = np.asarray(h[key], np.float32)
        arr[f"{n}|S"] = np.array([d["S"], d["angle"]])
    try:
        np.savez_compressed(path, **arr)
        emit("OUT", "distributions", path)
    except Exception as e:
        log("  note: distributions not saved:", e)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
