# PyInstaller recipe: builds a standalone app (no Python needed on the computer).
#   pip install -r requirements.txt pyinstaller
#   pyinstaller CollagenAnalyzer.spec --noconfirm
# Result: dist/Collagen Analyzer.app (Mac) or dist/CollagenAnalyzer/CollagenAnalyzer.exe (Windows).
# Usually built automatically on GitHub (.github/workflows/build.yml).
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# app files + licenses: the app's MIT license, third-party notices and the LGPL/GPL texts for PySide6/Qt
datas = [("docs", "docs"), ("assets", "assets"), ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", "."),
         ("licenses", "licenses")]
# package metadata (*.dist-info, with each package's own license files where it ships them)
for pkg in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "numpy", "scipy", "scikit-image",
            "scikit-learn", "pandas", "matplotlib", "openpyxl", "nd2", "tifffile", "joblib"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass
binaries = []
hiddenimports = collect_submodules("skimage.filters") + collect_submodules("skimage.morphology") + \
    collect_submodules("skimage.feature") + collect_submodules("skimage.registration") + [
                                             "gelstats", "changemap", "nc_physics", "PySide6.QtSvg", "PySide6.QtNetwork", "gui.plot_style",
                                             "gui.ease", "gui.errors", "ml.correlate",
                                             "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_agg",
                                             "openpyxl", "collagen_core", "cloudfiles", "ml", "ml.qc", "joblib",
                                             "sklearn.ensemble", "sklearn.impute", "sklearn.pipeline",
                                             "sklearn.model_selection", "sklearn.metrics",
                                             "sklearn.gaussian_process", "scipy.stats", "ml.planner",
                                             "ml.patterns", "sklearn.mixture", "sklearn.cluster",
                                             "sklearn.manifold", "sklearn.decomposition",
                                             "ml.fibermodel"]
for pkg in ("nd2", "ome_types", "resource_backed_dask_array", "tifffile", "imagecodecs"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(["CollagenAnalyzer.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "pytest",
                       "cryptography", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
                       "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
                       "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest"], noarchive=False)
pyz = PYZ(a.pure)
icon = "assets/icon.icns" if sys.platform == "darwin" else "assets/icon.ico"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CollagenAnalyzer", console=False,
          disable_windowed_traceback=False, icon=icon, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="CollagenAnalyzer", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Collagen Analyzer.app", icon="assets/icon.icns",
                 bundle_identifier="edu.cuny.hunter.matsuilab.collagenanalyzer",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "1.2",
                             "CFBundleDisplayName": "Collagen Analyzer",
                             "NSRequiresAquaSystemAppearance": False})
