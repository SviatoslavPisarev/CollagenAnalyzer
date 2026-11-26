"""Image files kept in iCloud Drive (or OneDrive) that are not on this computer yet.

With "Optimize Mac Storage", macOS keeps only a placeholder of a file and downloads it when it is opened;
when the disk is nearly full it can also remove the local copy again at any time - even in the middle of
a run. This module makes the analysis wait for such files instead of failing:

  state(path)          'ok', 'cloud' (only a placeholder here), 'empty', 'bad' (not a valid image file yet)
                       or 'missing'; never downloads anything
  ensure_local(path)   downloads the file if needed and waits for it (raises FileUnavailable on timeout)
  wait_all(paths)      starts all downloads of a folder at once, then waits; returns the files that failed
  evict(paths)         frees the local copies of the files this run had to download (they stay in iCloud)

macOS: download with `brctl download` plus a plain read (both start the iCloud download); free space with
`brctl evict`. Windows (OneDrive): the read starts the download; nothing is freed. Elsewhere files are
always 'ok', 'empty', 'bad' or 'missing'.
"""
import os
import subprocess
import sys
import threading
import time

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
SF_DATALESS = 0x40000000                        # APFS: file content is not on this computer (sys/stat.h)
WIN_CLOUD = 0x00400000 | 0x00040000             # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN
BRCTL = "/usr/bin/brctl"
_TIFF = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
MAGIC = {".nd2": (b"\xda\xce\xbe\x0a", b"\x00\x00\x00\x0c"), ".tif": _TIFF, ".tiff": _TIFF}
EMPTY_GRACE_S = 120          # an empty / invalid file that is not in the cloud gets this long to finish syncing

DOWNLOADED = set()           # files this run had to download (only these are freed again)
_fetching = set()
_lock = threading.Lock()


class FileUnavailable(OSError):
    """A file could not be made available. permanent=False: still in iCloud (try again later);
    permanent=True: the file itself is empty or broken (copy it again)."""

    def __init__(self, path, reason, permanent=False):
        super().__init__(f"{os.path.basename(path)}: {reason}")
        self.path, self.reason, self.permanent = path, reason, permanent


def _stub(path):
    """Older macOS: an undownloaded file is a hidden '.name.icloud' file next to it."""
    d, n = os.path.split(path)
    return os.path.join(d, "." + n + ".icloud")


def state(path):
    """Where the file's content is, without downloading it."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "cloud" if os.path.exists(_stub(path)) else "missing"
    except OSError:
        return "bad"
    if IS_MAC and getattr(st, "st_flags", 0) & SF_DATALESS:
        return "cloud"
    if IS_WIN and getattr(st, "st_file_attributes", 0) & WIN_CLOUD:
        return "cloud"
    if st.st_size == 0:
        return "empty"
    magic = MAGIC.get(os.path.splitext(path)[1].lower())
    if magic:
        try:
            with open(path, "rb") as fh:
                head = fh.read(4)
        except OSError:
            return "bad"
        if not head.startswith(magic):
            return "bad"
    return "ok"


def is_cloud_only(path):
    return state(path) == "cloud"


def _read_one_byte(path):
    try:
        with open(path, "rb") as fh:              # opening a placeholder makes the system download it
            fh.read(1)
    except OSError:
        pass
    finally:
        with _lock:
            _fetching.discard(path)


def request_download(path):
    """Start downloading the file (returns at once)."""
    with _lock:
        if path in _fetching:
            return
        _fetching.add(path)
    if IS_MAC and os.path.exists(BRCTL):
        try:
            subprocess.run([BRCTL, "download", path], capture_output=True, timeout=30)
        except Exception:
            pass
    threading.Thread(target=_read_one_byte, args=(path,), daemon=True).start()


def _mb(path):
    try:
        return os.path.getsize(path) / 1e6
    except OSError:
        return 0.0


def ensure_local(path, timeout=900, log=None, poll=2.0):
    """Make sure the file's content is on this computer, downloading it if needed.
    Returns True if it had to be downloaded, False if it was already here.
    Raises FileUnavailable (or FileNotFoundError) if that is not possible."""
    log = log or (lambda *a: None)
    name = os.path.basename(path)
    s = state(path)
    if s == "ok":
        return False
    if s == "missing":
        raise FileNotFoundError(f"file not found: {path}")
    if s == "cloud":
        log(f"  {name}: in iCloud, not on this computer - downloading…")
    else:
        log(f"  {name}: {'empty (0 bytes)' if s == 'empty' else 'not a complete image file'} - "
            "waiting in case iCloud is still syncing it…")
    request_download(path)
    t0 = time.monotonic()
    was_cloud = s == "cloud"
    next_note = 60
    while True:
        time.sleep(poll)
        s = state(path)
        el = time.monotonic() - t0
        if s == "ok":
            DOWNLOADED.add(os.path.abspath(path))
            log(f"  {name}: downloaded ({_mb(path):.0f} MB, {el:.0f} s)")
            return True
        if s == "missing":
            raise FileNotFoundError(f"file disappeared: {path}")
        if s == "cloud":
            was_cloud = True
            request_download(path)                # in case the first request was dropped
        if not was_cloud and el > min(EMPTY_GRACE_S, timeout):
            what = "the file is empty (0 bytes)" if s == "empty" else "the file is not a valid image file"
            raise FileUnavailable(path, f"{what} - copy it again from the microscope computer", permanent=True)
        if el > timeout:
            raise FileUnavailable(path, f"still not downloaded from iCloud after {timeout / 60:g} min")
        if el > next_note:
            log(f"  {name}: still downloading ({el / 60:.0f} min)…")
            next_note += 60


def wait_all(paths, timeout=900, log=None):
    """Start the downloads of all files at once, then wait for each.
    Returns [(path, FileUnavailable)] for the files that could not be made available."""
    log = log or (lambda *a: None)
    todo = [p for p in paths if state(p) != "ok"]
    if not todo:
        return []
    n_cloud = sum(state(p) == "cloud" for p in todo)
    if n_cloud:
        log(f"  {n_cloud} file(s) are in iCloud only - downloading "
            f"({sum(_mb(p) for p in todo):.0f} MB)")
    for p in todo:
        if state(p) == "cloud":
            request_download(p)
    deadline = time.monotonic() + timeout
    failed = []
    for p in todo:
        try:
            ensure_local(p, timeout=max(30.0, deadline - time.monotonic()), log=log)
        except FileUnavailable as e:
            failed.append((p, e))
        except FileNotFoundError as e:
            failed.append((p, FileUnavailable(p, str(e), permanent=True)))
    return failed


def evict(paths, log=None):
    """Free the local copies of those of `paths` that this run downloaded (they stay in iCloud)."""
    log = log or (lambda *a: None)
    mine = [p for p in paths if os.path.abspath(p) in DOWNLOADED]
    if not mine or not (IS_MAC and os.path.exists(BRCTL)):
        return 0
    freed, n = 0.0, 0
    for p in mine:
        mb = _mb(p)
        try:
            r = subprocess.run([BRCTL, "evict", p], capture_output=True, timeout=60)
            ok = r.returncode == 0
        except Exception:
            ok = False
        if ok:
            freed += mb
            n += 1
            DOWNLOADED.discard(os.path.abspath(p))
    if n:
        log(f"  freed {freed:.0f} MB: removed the local copies of {n} downloaded file(s) (they stay in iCloud)")
    return n
