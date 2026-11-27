"""Plain-language explanations of common problems: what went wrong and what to do about it.

explain(text) scans a log or an error message; friendly(title, error) builds the text of a message box."""
import re

# (pattern, what went wrong, what to do) — checked in order; the first match per line counts
RULES = [
    (r"pixel size not found in the TIFF|pixel size missing or invalid",
     "A TIFF file has no pixel size.",
     "Enter the pixel size in the Settings step (Pixel size, µm), or export the TIFF again with its "
     "calibration (Fiji: Image → Properties)."),
    (r"cannot interpret axes",
     "An image has dimensions the app does not understand (e.g. several channels or positions).",
     "Export a single-channel image or z-stack (one position per file) and analyse that."),
    (r"no \.nd2/\.tif files found|no \.nd2/\.tif files in",
     "No .nd2 or .tif images were found.",
     "Choose the folder that contains the images themselves (not a parent folder or the results folder)."),
    (r"two files share the name",
     "The same image is there twice (e.g. as .nd2 and as .tif).",
     "Keep only one of them in the folder, or untick one in the Files step."),
    (r"file changed while the script was running",
     "A file was still being copied or synced while it was analysed.",
     "Wait until copying has finished, then run again."),
    (r"MemoryError|Unable to allocate|out of memory|std::bad_alloc|Killed|error code -9\b|error code 137\b",
     "The computer ran out of memory.",
     "In the Settings step lower ‘Parallel workers’ (e.g. to 1–2), close other programs, or analyse fewer "
     "z-steps (z-step > 1)."),
    (r"not downloaded from iCloud|FileUnavailable",
     "Some files are only in iCloud, not on this computer.",
     "In Finder or Explorer right-click the folder → Keep Downloaded / Always keep on this device, then run "
     "again."),
    (r"Permission denied|Operation not permitted|PermissionError|Access is denied",
     "The app is not allowed to read or write a file or folder.",
     "Close the file if it is open in Excel. On a Mac allow access in System Settings → Privacy & Security → "
     "Files and Folders (or Full Disk Access); otherwise choose a results folder you can write to "
     "(Outputs step)."),
    (r"File is not a zip file|BadZipFile|Excel file format cannot be determined",
     "A spreadsheet is damaged, still being saved, or not an Excel file.",
     "Close it in Excel, wait for syncing to finish, and load it again (or run the analysis again)."),
    (r"Worksheet named 'per file' not found|'per file'",
     "This spreadsheet is not a results summary of this app.",
     "Load an mmddyy_summary.xlsx or summary.xlsx written by an analysis run (or drop its results folder)."),
    (r"No module named",
     "A part of the app is missing.",
     "Download the app again from the Releases page (or, when running from Python, pip install -r "
     "requirements.txt)."),
    (r"copy it again from the microscope computer|Unexpected end of file|file is truncated|"
     r"\.nd2.*(corrupt|invalid)",
     "An image file is damaged or incomplete.",
     "Copy the file again from the microscope computer; untick it in the Files step to run without it."),
    (r"imaging settings differ",
     "Imaging settings (laser, gain, pixel size, …) differ between files of one comparison.",
     "Reflectance-based numbers are only comparable at the same settings; check the files listed in the log."),
    (r"differs within sample",
     "Files of one sample have different labels (e.g. collagen or NC concentration).",
     "Check the file names of that sample in the Files step (double-click a cell to correct a value)."),
    (r"only \d+% of the image is analysed",
     "Large parts of an image were excluded (edge or bright hotspot).",
     "Look at that file's figure; if the exclusion is wrong, adjust the hotspot setting in the Settings step."),
    (r"WARNING \(QC\)",
     "The quality check flagged some files.",
     "See the QC column in the Files step; untick them with ‘Untick QC check’ and run again if they are bad."),
    (r"no folder could be analysed",
     "None of the folders could be analysed.",
     "See the warnings above it in the log for the reason of each folder."),
]
_RX = [(re.compile(p, re.I), a, b) for p, a, b in RULES]


def explain(text, limit=4):
    """[(what went wrong, what to do)] for the known problems in text, in order of first appearance."""
    found = []
    for line in str(text).splitlines():
        if line.startswith("Starting:"):            # the command line itself (paths, options)
            continue
        for rx, what, fix in _RX:
            if rx.search(line):
                if (what, fix) not in found:
                    found.append((what, fix))
                break
        if len(found) >= limit:
            break
    return found


def as_html(items):
    return "".join(f"<br>• <b>{w}</b> {f}" for w, f in items)


def friendly(error, intro=""):
    """Message-box text: the explanation (if known) first, the technical message last."""
    msg = str(error)
    items = explain(msg)
    out = intro + ("\n\n" if intro else "")
    if items:
        out += "\n".join(f"{w}\nWhat to do: {f}" for w, f in items) + "\n\nDetails: "
    return out + msg
