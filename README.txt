COLLAGEN REFLECTANCE ANALYZER  (version 1.0)
============================================
Desktop app (Mac and Windows) for confocal reflectance images of collagen gels:
fiber density, pore size, fiber thickness and length, alignment, patchiness;
before/after AMF comparisons and custom graphs.

START
  Mac:      double-click Start_Mac.command     (first time: right-click -> Open -> Open)
  Windows:  double-click Start_Windows.bat
  Needs Python 3.9+ from python.org. The first start installs the packages
  (2-5 min, internet needed); later starts take seconds.

  No Python? Build the standalone app (Collagen Analyzer.app / CollagenAnalyzer.exe)
  for free on GitHub - see User manual, section 1A.

MANUAL
  docs/User_Manual.pdf   (also inside the app: Help page)

FILES
  CollagenAnalyzer.py        the app (start file)
  collagen_core.py           the analysis engine (also usable from Terminal:
                             python3 collagen_core.py --help)
  gui/                       the windows and pages of the app
  docs/                      manual (HTML + PDF) and its pictures
  assets/                    icon and start screen
  requirements.txt           Python packages
  Start_Mac.command, Start_Windows.bat   launchers
  CollagenAnalyzer.spec, .github/workflows/build.yml   standalone-app build (GitHub)
