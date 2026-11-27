# Collagen Reflectance Analyzer

Desktop app for Mac and Windows that measures the structure of collagen hydrogels in confocal **reflectance** images
(`.nd2` from the Nikon A1, or `.tif`): fiber density, pore size, fiber thickness and length, alignment and patchiness.
It compares files before and after AMF treatment, across samples and across experiments, and makes figures and
spreadsheets ready for papers.

Matsui Lab · Hunter College (CUNY) · version 1.0

![Results & Plots page](docs/img/plot_perfile.png)

## Download and install (no Python needed)

1. Open **[Actions → Build app](../../actions/workflows/build.yml)**, click the latest successful (green) run and download
   from **Artifacts**. If a **[release](../../releases)** exists, download the zips from there instead.

   | Your computer | Download |
   |---|---|
   | Mac with M1/M2/M3/M4 chip (Apple menu → About This Mac → *Chip: Apple…*) | `CollagenAnalyzer-mac-apple-silicon` |
   | Older Mac with Intel processor | `CollagenAnalyzer-mac-intel` |
   | Windows 10 / 11 (64-bit) | `CollagenAnalyzer-windows` |

2. **Mac:** unzip until you see **Collagen Analyzer.app** (there may be a zip inside the zip), then drag it to **Applications**.
   The app is not signed by Apple, so macOS blocks it the first time. Run this once in Terminal, then open the app normally:

   ```
   xattr -dr com.apple.quarantine "/Applications/Collagen Analyzer.app"
   ```

   You can also wait for the warning, click **Done**, then go to System Settings → Privacy & Security → **Open Anyway**.

3. **Windows:** unzip the folder anywhere and double-click **CollagenAnalyzer.exe**. Keep the `_internal` folder next to it.
   If “Windows protected your PC” appears, click **More info → Run anyway**.

The first start takes about 20–40 seconds; after that it opens faster.

## Using it

Work through the pages on the left of the window:

1. **Data:** choose one file, one folder or several folders, and tick the files to analyse. The table shows what was read
   from each file name. Double-click any value to correct or fill it in, and use **Defaults & added parameters…** to set
   defaults and add your own parameters (e.g. gel thickness). These are remembered for future runs.
2. **Parameters:** check the fiber mask in the live preview. Choose the threshold method (Otsu per file, Otsu shared, or a
   fixed value) and the other settings. Presets can be saved and loaded.
3. **Outputs:** choose where to save, the level of detail, which figures to make, and the format (PNG, PDF, SVG or TIFF).
4. **Run:** shows progress, a log, and the results when it finishes.
5. **Results & Plots:** combine any files in your own graphs:
   - a metric vs depth or time
   - per-file bars or box plots
   - % change after AMF
   - a metric vs NC concentration or AMF settings
   - distributions (pore size, fiber size, fiber length, angle)

   You can save the figure and export the plotted data as CSV.

### Quality check (new)

Every run now checks each file for blur, uneven (dried) edges, saturation, dark tiles and threshold sensitivity. The
results go to a **quality** sheet in the summary spreadsheet, a **QC** column on the Data page and warnings in the log.
On the **Quality check** page you can train a model on your own labelled files (a note like `_other-dried-edges_` in the
file name marks a problem) and apply it to new runs. Each run also writes **mmddyy_tiles.csv**, the metrics of every
13 µm tile, for finding local patterns. See section 14 of the manual.

### Patterns (new)

The **Patterns** page clusters the per-tile table into structure types (Gaussian mixture or k-means on standardised tile
metrics), names them ("aligned, sparse", …), and shows how their share changes after AMF per sample, where they sit in the
image (tile maps, before and after side by side) and how the tiles spread (PCA / t-SNE). See section 16.

### Fiber model (new)

On the **Fiber model** page you paint a few fiber / background strokes on some images, train a pixel classifier
(random forest on multi-scale filter responses) and check its mask against the ridge + Otsu mask. Saved, it replaces the
ridge threshold for new runs (Parameters page → Fiber threshold → Trained fiber model). See section 17.

### Experiment Planner (new)

The **Experiment Planner** page pairs each After-AMF file with its sample's Before-AMF file, models how the AMF and
nanocage settings change a metric you choose (e.g. alignment S), and suggests the next conditions to test (Gaussian
process + expected improvement), with ready-made file names. It also makes a space-filling starting design. See section 15.

### Sham controls and statistics (new)

Name the second image of a **sham** gel (same handling and waiting time, no field) with `sham`, and gels without nanocages
with `noNC`. Every file then gets a group: *NC + AMF*, *AMF only (no NC)*, *sham (NC, no field)* or *sham, no NC*. Each run's
spreadsheet gets three new sheets: **changes** (every After file vs its Before file, with the % change minus the matching
sham change of the same date), **statistics** (per group, with the gel as the replicate: mean, 95 % CI, paired tests) and
**arm comparisons** (NC + AMF vs sham = the field effect; Welch, Mann–Whitney, Hedges' g, Holm-adjusted). On **Results &
Plots**, *Change by group with statistics* shows one dot per gel with these numbers, and the % change plot can subtract
the sham change. The **Experiment Planner** subtracts the sham change by default, so it models the AMF effect and not the
drift from drying or re-imaging. See section 18 of the manual.

### Registration and local change maps (new)

The After image of each sample is aligned onto its Before image (masked phase correlation on the projections; the
hotspot and edge do not count) and the tiles at the same place in the gel are compared: **registration** sheet (shift,
similarity, status), **mmddyy_tile_changes.csv** and **change_map_mmddyy_##.png** (before, after moved onto before,
overlay, maps of the change in alignment, fiber density and pore size). See section 19.

### Nanocage physics (new)

Section 5 of the Experiment Planner (and `python nc_physics.py --help`) estimates the Brownian and Néel relaxation times,
the phase lag at the AMF frequency and the field's alignment/torque for a given nanocage size. See section 20.

### Files in iCloud Drive

Data in iCloud Drive with "Optimize Mac Storage" may be only a placeholder on the Mac, and macOS can remove the local copy
again during a run. The analysis now downloads a folder's files before analysing it (all at once) and re-downloads a file
that disappears mid-run. A folder whose files do not arrive within 15 min (`--cloud-wait-min`) goes to the end of the
queue and is tried again after the others (up to 3 times, `--cloud-retries`); an empty or broken file skips only its
folder, and the log lists the skipped folders at the end. After each folder the local copies of the files the run had to
download are removed again (Outputs page → *Free disk space*, or `--keep-downloaded` to keep them). The Data page shows
such files as "in iCloud" without downloading them.

The full **user manual** is in the app (**Help** page) and in [`docs/User_Manual.pdf`](docs/User_Manual.pdf).

### File names

The app reads the experimental conditions from the file names, for example:

```
112026_3_Collagen_3mgml_NC_0.5nM_inc45min_other-dried-edges_z_AMF_500Hz_3min_10Vpp_8.5V_2App_500turns_40mTpk_1013.nd2
```

| Part | Meaning |
|---|---|
| `mmddyy_##_` (start, required) | date and sample number; files with the same start are compared before vs after |
| `AMF` | marks the After-AMF file |
| `sham` | marks the second image of a sham control gel (no field) |
| `500Hz`, `3min`, `10Vpp`, `8.5V` | AMF frequency, time, Vp-p set on the instrument, measured voltage |
| `2App`, `500turns` | AMF current Ap-p set on the power supply (not the measured current), coil turns (e.g. 200 or 500) |
| `40mTpk` / `28mTrms` | measured magnetic field, peak or rms (also `mTpp`); converted to a peak value in `amf_b_peak_mT` |
| `Collagen_3mgml` | collagen concentration |
| `NC_0.5nM` | theoretical local nanocage concentration |
| `noNC` | gel without nanocages (same as `NC_0nM`) |
| `premixed` / `inc45min` | NCs mixed in before gelation / incubated on the gel for that time |
| `other-…` | free note (use hyphens inside it) |
| last part | file number shown in legends |

The manual (section 3) lists every accepted form.

## Run from the source code (optional, needs Python 3.9+)

Download this repository (**Code → Download ZIP**), unzip it, then:

- **Mac:** open **Terminal**, type `bash ` (with a space), drag `Start_Mac.command` from the unzipped folder into the
  Terminal window, and press Return. This works even though macOS blocks double-clicking downloaded scripts, and GitHub's
  ZIP download does not keep the file's "executable" setting.
- **Windows:** double-click `Start_Windows.bat`.

The first start installs the Python packages (2–5 minutes, internet needed).

The analysis engine also runs without the window: `python3 collagen_core.py --help`.

## Building the app

The standalone apps are built automatically on GitHub by [`.github/workflows/build.yml`](.github/workflows/build.yml):
go to **Actions → Build app → Run workflow**. A build takes about 10–20 minutes.

Pushing a tag such as `v1.1`, or creating a release with that tag, builds the app again and attaches the zips to the release.
Downloads under Actions → Artifacts are deleted after 90 days, so share the zips from a release or a lab drive.

To build on your own computer: `pip install -r requirements.txt pyinstaller`, then
`pyinstaller CollagenAnalyzer.spec --noconfirm`. The result is in `dist/`.

## Tests

`pip install pytest`, then `python -m pytest tests` (about 15 s). They check the file-name parsing, the statistics against
SciPy, the registration and the image metrics on synthetic fiber networks with known order parameter and angle. GitHub runs
them on every push (**Actions → Tests**).

## What's in this repository

| Path | Contents |
|---|---|
| `CollagenAnalyzer.py` | start file of the app |
| `collagen_core.py` | analysis engine (image processing, metrics, figures, spreadsheets) |
| `cloudfiles.py` | waits for iCloud Drive downloads and frees the local copies again |
| `gelstats.py` | groups (NC + AMF, AMF only, sham), sham correction and statistics with the gel as the replicate |
| `changemap.py` | before/after registration and tile-by-tile change maps |
| `nc_physics.py` | relaxation times, phase lag and torque of the nanocages in the AMF |
| `ml/` | quality check, Experiment Planner, Patterns and the trained fiber model |
| `tests/` | automatic tests, including synthetic fiber networks with known alignment (`tests/synth.py`) |
| `gui/` | the app's windows and pages |
| `docs/` | user manual (HTML + PDF) and its screenshots |
| `assets/` | icon and start screen |
| `requirements.txt` | Python packages |
| `Start_Mac.command`, `Start_Windows.bat` | launchers for running from the source code |
| `CollagenAnalyzer.spec`, `.github/workflows/build.yml` | standalone-app build |
| `.github/workflows/tests.yml` | runs the tests on every push and pull request |
| `CITATION.cff` | how to cite the app (GitHub: “Cite this repository”) |
| `LICENSE` | MIT License of the app's code |
| `THIRD_PARTY_NOTICES.md`, `licenses/` | licenses of the packages inside the standalone app (PySide6/Qt: LGPL-3.0) |
| `.gitignore` | keeps Python cache files (`__pycache__`) and `.DS_Store` out of the repository |

## License

The app's code is released under the [MIT License](LICENSE). The standalone app also contains third-party
packages under their own licenses — notably PySide6 / Qt under the LGPL-3.0, used unmodified; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Both files are also inside the downloaded app.
