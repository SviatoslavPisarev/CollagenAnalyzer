"""Shared definitions: parameters, file-name parsing for the file table, command-line building."""
import os
import re
import sys

import collagen_core as core

APP_NAME = "Collagen Reflectance Analyzer"
APP_VERSION = "1.0"
IMAGE_EXT = (".nd2", ".tif", ".tiff")

DEFAULTS = vars(core.build_parser().parse_args(["."]))

# Numeric parameters shown on the Parameters page.
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
]


def parse_file_name(path, after_pattern="AMF"):
    """What the engine will read from a file name (for the file table)."""
    name = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^(\d{6})_(\d+)_", name)
    date, sample = (m.group(1), f"{m.group(1)}_{m.group(2)}") if m else ("other", name)
    try:
        after = bool(re.search(after_pattern, core.name_core(name), re.I))
    except re.error:
        after = False
    d = dict(name=name, date=date, sample=sample, named=bool(m),
             condition="After AMF" if after else "Before AMF")
    hz, sec = core.parse_amf(name) if after else (None, None)
    vpp, vm = core.parse_amf_voltage(name) if after else (None, None)
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
    """Shape, type and pixel size of an image file without loading the pixels."""
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
