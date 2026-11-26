#!/usr/bin/env python3
"""Collagen Reflectance Analyzer — desktop app.

Start:     python CollagenAnalyzer.py            (opens the window)
Engine:    python CollagenAnalyzer.py --core ...  (runs the analysis without a window; used by the app)
The analysis itself is collagen_core.py (same as collagen_reflectance_analysis.py, which can also be run
on its own from a terminal).
"""
import multiprocessing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _ensure_streams():
    """Windowed apps (pythonw, packaged app) may start without stdout/stderr."""
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", buffering=1, closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def run_engine(args):
    _ensure_streams()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
    import matplotlib
    matplotlib.use("Agg")
    import collagen_core
    collagen_core.main(args)


def run_gui(paths):
    _ensure_streams()
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtGui import QPixmap

    QCoreApplication.setOrganizationName("MatsuiLab")
    QCoreApplication.setOrganizationDomain("hunter.cuny.edu")
    QCoreApplication.setApplicationName("CollagenAnalyzer")
    app = QApplication(sys.argv[:1])
    icon = os.path.join(getattr(sys, "_MEIPASS", HERE), "assets", "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    splash = None
    sp = os.path.join(getattr(sys, "_MEIPASS", HERE), "assets", "splash.png")
    if os.path.exists(sp):
        splash = QSplashScreen(QPixmap(sp))
        splash.show()
        app.processEvents()
    from gui.style import apply_theme
    apply_theme(app)
    from gui.main_window import MainWindow          # heavy imports (numpy, scikit-image) happen here
    w = MainWindow()
    if paths:
        w.data.load_paths(paths)
    w.show()
    if splash:
        splash.finish(w)
    return app.exec()


def main():
    multiprocessing.freeze_support()                # needed for parallel workers in the packaged app
    argv = sys.argv[1:]
    if "--core" in argv:
        run_engine(argv[argv.index("--core") + 1:])
        return
    # macOS adds -psn_... when started from Finder; ignore anything that is not a path
    sys.exit(run_gui([a for a in argv if os.path.exists(a)]))


if __name__ == "__main__":
    main()
