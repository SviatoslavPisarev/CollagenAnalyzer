# Third-party software

Collagen Reflectance Analyzer's own code is under the MIT License (see `LICENSE`). The standalone app
(the downloadable Mac and Windows zips) also contains the third-party packages below, each under its
own license. When the app is run from the source code, these packages are installed separately by the
launchers and are not part of this repository.

## PySide6, Shiboken6 and Qt

The app's window is built with **PySide6** (Qt for Python) and **Shiboken6**, which include the
**Qt 6** libraries.

- Copyright (C) The Qt Company Ltd. and other contributors.
- License: GNU Lesser General Public License version 3 (LGPL-3.0), which the app uses (PySide6 is also
  available under GPL-2.0 / GPL-3.0 and a commercial license). The LGPL-3.0 adds permissions to the GNU
  General Public License version 3. Full texts: [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt) and
  [`licenses/GPL-3.0.txt`](licenses/GPL-3.0.txt).
- PySide6 and Qt are used **unmodified**, as published on PyPI (`pip install PySide6`, version given in
  `requirements.txt`; the build installs the newest matching release).
- Source code of PySide6, Shiboken6 and Qt: <https://code.qt.io/cgit/pyside/pyside-setup.git/>,
  <https://code.qt.io/cgit/qt/>, and the source distributions at <https://download.qt.io/>.
- Replacing the library: in the standalone app, PySide6 and the Qt libraries are separate files inside
  the app (Windows: the `_internal` folder next to `CollagenAnalyzer.exe`; Mac: inside
  `Collagen Analyzer.app/Contents/`). You may replace them with another compatible (including modified)
  version. The complete source code of this app is in this repository, so you can also run it from source
  with any PySide6 version (`Start_Mac.command` / `Start_Windows.bat`) or rebuild the app yourself with
  `pyinstaller CollagenAnalyzer.spec` (see `README.md`).

## Inter typeface

The app's text uses **Inter** (version 4.1, the Inter Project Authors, <https://rsms.me/inter/>), bundled in
`assets/fonts/` and licensed under the SIL Open Font License 1.1 — full text: [`assets/fonts/OFL-Inter.txt`](assets/fonts/OFL-Inter.txt).
The font files are unmodified apart from conversion from WOFF2 to TTF.

## Other packages in the standalone app

| Package | License |
|---|---|
| NumPy | BSD-3-Clause (with some parts under 0BSD, MIT, Zlib, CC0-1.0) |
| SciPy | BSD-3-Clause |
| scikit-image | BSD-3-Clause |
| scikit-learn | BSD-3-Clause |
| pandas | BSD-3-Clause |
| Matplotlib | Matplotlib License (PSF-based, BSD-compatible) |
| openpyxl | MIT |
| nd2 | BSD-3-Clause |
| tifffile | BSD-3-Clause |
| joblib | BSD-3-Clause |
| Python | Python Software Foundation License |

These packages and their own dependencies (included automatically by the build) keep their copyright
notices and license texts in their installed package metadata (`*.dist-info`), which the build copies into
the app where the package provides them. Each package's full license is also on its PyPI page.
