"""Shared definitions: parameters, file-name parsing for the file table, command-line building."""
import os
import re
import sys

import collagen_core as core
import cloudfiles as CF

APP_NAME = "Collagen Reflectance Analyzer"
APP_VERSION = "1.1"
IMAGE_EXT = (".nd2", ".tif", ".tiff")

DEFAULTS = vars(core.build_parser().parse_args(["."]))

# Numeric parameters shown in the Settings step.
# key = engine option (argparse dest), label, unit, min, max, step, decimals, help text
NUMERIC = {
    "zstep": ("Analyse every Nth z-slice", "", 1, 500, 1, 0,
              "1 = every slice (slowest, default). Interval in µm = file z-step × N; e.g. 4 × 0.075 µm = 0.3 µm, "
              "which still samples below the ~0.6 µm axial resolution at ¼ of the time."),
    "tstep": ("Analyse every Nth time point", "", 1, 500, 1, 0,
              "Time-lapses only. 1 = every time point."),
    "sigma_bg_um": ("Background flattening blur", "µm", 0.2, 50, 0.1, 2,
                    "Each image is divided by a blurred copy of itself to remove uneven illumination and depth "
                    "attenuation. Should be clearly larger than a fiber; larger keeps more large-scale "
                    "brightness differences."),
    "sigma_st_um": ("Orientation window", "µm", 0.05, 10, 0.05, 2,
                    "Structure-tensor smoothing for the fiber angle. Larger = smoother angle map, less noise, "
                    "but short bent fibers are averaged."),
    "min_obj_um2": ("Remove specks up to", "µm²", 0, 50, 0.02, 3,
                    "Mask objects this small or smaller are removed as noise (0.32 µm² ≈ 30 px at 0.1 µm/px)."),
    "edge_um": ("Excluded image border", "µm", 0.1, 50, 0.1, 2,
                "Border strip ignored in every image (filter edge effects)."),
    "tile_um": ("Tile size for patchiness (tile CV)", "µm", 1, 200, 1, 1,
                "The image is cut into tiles of this size; tile CV = SD / mean of the fiber fraction of the tiles."),
    "min_fiber_length_um": ("Shortest fiber segment counted", "µm", 0.05, 20, 0.05, 2,
                            "Side branches shorter than this that hang off one branch point are pruned before the "
                            "centerline is split into segments; also affects length density."),
    "pore_min_dist_um": ("Spacing of pore centres (secondary pore measure)", "µm", 0.05, 20, 0.05, 2,
                         "Minimum distance between distance-map maxima for 'local pore diameter (maxima)'. "
                         "Does not affect the main pore size (local thickness)."),
    "hotspot_sigma_um": ("Hotspot detection blur", "µm", 0.5, 50, 0.5, 1,
                         "Blur used to find the bright reflection disc of the objective."),
    "workers": ("Parallel workers", "", 1, 64, 1, 0,
                "Images analysed at the same time. Default: CPU cores − 1 (max 8). Lower it if the computer "
                "runs out of memory."),
}

GROUPS = [
    ("Which images are analysed", ["zstep", "tstep"]),
    ("Fiber detection", ["sigma_bg_um", "min_obj_um2", "sigma_st_um"]),
    ("Measurements", ["edge_um", "tile_um", "min_fiber_length_um", "pore_min_dist_um"]),
    ("Performance", ["workers"]),
]

FIGURES = [  # key, label, group
    ("file", "Per-file figure (images, masks, graphs, numbers)", "Per file"),
    ("slices", "A figure of every analysed image (slices/ folder)", "Per file"),
    ("slice_comparison", "Slice comparison and per-image distributions", "Per file"),
    ("sample", "Sample comparison (before vs after, sample_mmddyy_##)", "Per sample"),
    ("density", "Fiber density, one panel per sample", "Per date (side by side)"),
    ("pore_size", "Pore size, one panel per sample", "Per date (side by side)"),
    ("angle", "Fiber angle (rose plots), one panel per sample", "Per date (side by side)"),
    ("pore_dist", "Pore size distribution, one panel per sample", "Per date (side by side)"),
    ("fiber_dist", "Fiber size distribution, one panel per sample", "Per date (side by side)"),
    ("length_dist", "Fiber length distribution, one panel per sample", "Per date (side by side)"),
    ("change_map", "Registered before/after and local change maps (change_map_mmddyy_##)", "Per sample"),
]


def parse_file_name(path, after_pattern="AMF"):
    """What the engine will read from a file name (for the file table)."""
    name = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^(\d{6})_(\d+)_", name)
    date, sample = (m.group(1), f"{m.group(1)}_{m.group(2)}") if m else ("other", name)
    sham = core.parse_sham(name)
    try:
        after = not sham and bool(re.search(after_pattern, core.name_core(name), re.I))
    except re.error:
        after = False
    d = dict(name=name, date=date, sample=sample, named=bool(m),
             condition="After sham" if sham else "After AMF" if after else "Before AMF")
    hz, sec = core.parse_amf(name) if after else (None, None)
    vpp, vm = core.parse_amf_voltage(name) if after else (None, None)
    d.update(core.parse_amf_coil(name) if after else dict(amf_app=None, coil_turns=None, amf_b=None, amf_b_type=None))
    d.update(amf_hz=hz, amf_s=sec, amf_vpp=vpp, amf_v=vm, collagen_mgml=core.parse_collagen(name),
             nc_nM=core.parse_nc(name), other=core.parse_other(name))
    d["mixing"], d["inc_s"] = core.parse_mixing(name)
    d["amf"] = core.amf_text(d) if after else ""
    d["nc"] = core.fmt_conc(d["nc_nM"]) or ""
    d["mix"] = core.mixing_text(d)
    d["collagen"] = f"{d['collagen_mgml']:g} mg/mL" if d["collagen_mgml"] is not None else ""
    d["file_no"] = name.split("_")[-1]
    return d


def read_header(path):
    """Shape, type and pixel size of an image file without loading the pixels.
    A file that is only in iCloud is not opened (that would download it): {'cloud': True}."""
    if CF.is_cloud_only(path):
        return dict(cloud=True)
    try:
        if path.lower().endswith(".nd2"):
            import nd2
            with nd2.ND2File(path) as f:
                sz = dict(f.sizes)
                vs = f.voxel_size()
                nt, nz = sz.get("T", 1), sz.get("Z", 1)
                return dict(nt=nt, nz=nz, ny=sz.get("Y"), nx=sz.get("X"), px=vs.x, dz=vs.z if nz > 1 else None)
        import tifffile
        with tifffile.TiffFile(path) as tf:
            ser = tf.series[0]
            shp = dict(zip(ser.axes, ser.shape))
            ij = tf.imagej_metadata or {}
            px = None
            try:
                num, den = tf.pages[0].tags["XResolution"].value
                if str(ij.get("unit", "")).lower() in ("micron", "um", "µm") and num:
                    px = den / num
            except Exception:
                pass
            nt = shp.get("T", 1)
            nz = shp.get("Z", shp.get("Q", shp.get("I", 1)))
            return dict(nt=nt, nz=nz, ny=shp.get("Y"), nx=shp.get("X"), px=px, dz=ij.get("spacing"))
    except Exception as e:
        return dict(error=str(e))


def kind_text(h):
    if not h or "error" in h:
        return "?"
    nt, nz = h.get("nt", 1), h.get("nz", 1)
    if nt > 1:
        return f"time-lapse, {nt} t" + (f" × {nz} z" if nz > 1 else "")
    if nz > 1:
        return f"z-stack, {nz} slices"
    return "single image"


def engine_command(args):
    """Command that runs the analysis engine in a separate process (also inside a packaged app)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--core"] + args
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CollagenAnalyzer.py")
    return [sys.executable, "-u", main, "--core"] + args


def resource(*parts):
    """Path of a bundled file (works from source and in the packaged app)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def open_path(path):
    """Open a file or folder with the system's default app."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def quote(a):
    return f'"{a}"' if (" " in a or not a) else a


# ---------------------------------------------------------------------------- editable sample information
# key, column title, unit, type, choices, engine key, factor (table value × factor = engine value)
STD_FIELDS = [
    ("condition", "Condition", "", "choice", ["Before AMF", "After AMF", "After sham"], "condition", None),
    ("amf_hz", "AMF freq.", "Hz", "num", None, "amf_hz", 1.0),
    ("amf_min", "AMF time", "min", "num", None, "amf_s", 60.0),
    ("amf_vpp", "Vp-p set", "V", "num", None, "amf_vpp", 1.0),
    ("amf_v", "V meas.", "V", "num", None, "amf_v", 1.0),
    ("amf_app", "Ap-p set", "A", "num", None, "amf_app", 1.0),
    ("coil_turns", "Coil", "turns", "num", None, "coil_turns", 1.0),
    ("amf_b", "B field", "mT", "num", None, "amf_b", 1.0),
    ("amf_b_type", "B as", "", "choice", ["", "peak", "rms", "peak-to-peak"], "amf_b_type", None),
    ("collagen_mgml", "Collagen", "mg/mL", "num", None, "collagen_mgml", 1.0),
    ("nc_nM", "NC conc.", "nM", "num", None, "nc_nM", 1.0),
    ("mixing", "NC mixing", "", "choice", ["", "premixed", "incubated", "premixed + incubated"], "mixing", None),
    ("inc_min", "Incubation", "min", "num", None, "inc_s", 60.0),
    ("other", "Note", "", "text", None, "other", None),
]
FIELD_TIPS = {
    "condition": "Before AMF / After AMF (from ‘AMF’ in the name) / After sham (from ‘sham’: same handling, no field)",
    "amf_hz": "AMF frequency (name: 500Hz, 1kHz)", "amf_min": "AMF exposure time in minutes (name: 3min, 90s)",
    "amf_vpp": "Vp-p set on the instrument (name: 10Vpp)", "amf_v": "Measured AMF voltage (name: 8.5V)",
    "amf_app": "Current Ap-p set on the power supply, not the measured current (name: 2App)",
    "coil_turns": "Number of turns of the AMF coil (name: 500turns or coil500)",
    "amf_b": "Measured magnetic field in mT (name: 40mTpk, 28mTrms; also µT/T)",
    "amf_b_type": "How the field was measured: peak, rms or peak-to-peak (name: …mTpk / …mTrms / …mTpp)",
    "collagen_mgml": "Collagen concentration (name: Collagen_3mgml)",
    "nc_nM": "Theoretical local nanocage concentration in nM (name: NC_0.5nM; noNC or NC_0nM = gel without NCs)",
    "mixing": "premixed / incubated (name: premixed, inc45min)", "inc_min": "NC incubation time in minutes (name: inc45min)",
    "other": "Free note (name: other-text)",
}


def parsed_value(d, key):
    """Value of a standard field as read from the file name (table units)."""
    if key == "amf_min":
        return d["amf_s"] / 60 if d.get("amf_s") is not None else None
    if key == "inc_min":
        return d["inc_s"] / 60 if d.get("inc_s") is not None else None
    return d.get(key)


def slug(text):
    t = str(text).replace("µ", "u").replace("%", "pct").replace("°", "deg")
    return re.sub(r"[^0-9a-zA-Z]+", "_", t).strip("_").lower()


RESERVED = set(core.LABEL_COLS) | {"uid", "folder", "results_folder", "n_images", "area_frac_pct", "order_S",
                                   "threshold", "coherency", "tile_cv", "mean_reflectance"}


def field_key(label, unit, taken=()):
    """Column name for an added parameter, e.g. ('Gel thickness', 'µm') -> gel_thickness_um."""
    k = slug(label) + (f"_{slug(unit)}" if unit and slug(unit) else "")
    k = k or "param"
    if k[0].isdigit():
        k = "p_" + k
    if k in RESERVED or k.startswith(("meta_", "slice_", "set_", "file_")):
        k += "_user"
    base, i = k, 2
    while k in taken:
        k = f"{base}_{i}"
        i += 1
    return k


def fmt_value(v):
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)
