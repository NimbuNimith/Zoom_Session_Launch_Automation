"""
browser_setup.py
-----------------
Where the app's Chromium comes from. It is NOT inside the exe any more (it
was 64% of the 435 MB every auto-update re-downloaded); it lives in one
fixed folder per Chromium revision:

    %LOCALAPPDATA%\\Command Center\\chromium\\chromium-<rev>\\chrome-win64\\chrome.exe

and gets there one of three ways:
  1. the installer copies it there (the normal case for learners),
  2. an earlier version of the app already left a copy there (2.2.3+), or
  3. install_browser() below downloads it once from a GitHub Release asset.

Running it from a fixed path (instead of the per-launch %TEMP%\\_MEIxxxxxx
folder a onefile exe unpacks into) also keeps Windows Firewall to a single
"allow this app?" prompt. Qt-free so it can be tested on its own.

Overrides (for testing, or hosting the file somewhere else):
  CC_BROWSER_URL     download the browser zip from this URL instead
  CC_BROWSER_SHA256  expected SHA-256 of that zip (required with the URL override)
"""

import json
import os
import shutil
import sys
import zipfile

from downloader import download_file, DownloadCancelled
from paths import resource_path, stable_chromium_dir

REPO = "NimbuNimith/Zoom_Session_Launch_Automation"

# SHA-256 of chromium-<rev>-win64.zip as published on the `chromium-<rev>` GitHub
# Release. Pinned here so a tampered or truncated download is refused.
# tools/prepare_release_assets.py prints the value to paste in.
CHROMIUM_ZIP_SHA256 = {
    "1208": "113348a867b401e7220c7d927085e972bcdf9e52fd37c114589c9a50d7f34ef0",
}

# download + extracted copy, relative to the zip size (zip ~170 MB -> ~395 MB extracted)
SPACE_FACTOR = 3.6


def required_revision() -> str:
    """The Chromium revision this build of Playwright was made for, read from
    the driver package's browsers.json (bundled in the exe, minus the browser)."""
    candidates = [resource_path(os.path.join("playwright", "driver", "package", "browsers.json"))]
    try:
        import playwright
        candidates.append(os.path.join(os.path.dirname(playwright.__file__),
                                       "driver", "package", "browsers.json"))
    except Exception:
        pass
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                for b in json.load(f)["browsers"]:
                    if b["name"] == "chromium":
                        return str(b["revision"])
        except Exception:
            continue
    raise RuntimeError("Couldn't work out which browser version this app needs "
                       "(Playwright's browsers.json is missing).")


def browser_exe_path(rev: str) -> str:
    return os.path.join(stable_chromium_dir(), f"chromium-{rev}", "chrome-win64", "chrome.exe")


def browser_installed(rev: str) -> bool:
    return os.path.isfile(browser_exe_path(rev))


def download_url(rev: str) -> str:
    return (os.environ.get("CC_BROWSER_URL")
            or f"https://github.com/{REPO}/releases/download/chromium-{rev}/chromium-{rev}-win64.zip")


def expected_sha256(rev: str):
    if os.environ.get("CC_BROWSER_URL"):
        return os.environ.get("CC_BROWSER_SHA256") or None
    return CHROMIUM_ZIP_SHA256.get(rev) or None


def remove_other_revisions(rev: str):
    """Delete browser folders of other Chromium revisions (and stale download
    leftovers) so old copies don't pile up. Blocking file work."""
    _remove_other_revisions(stable_chromium_dir(), keep=f"chromium-{rev}")


def _remove_other_revisions(root: str, keep: str):
    for name in os.listdir(root):
        if name != keep and (name.startswith("chromium-") or name.endswith(".zip.part")):
            path = os.path.join(root, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass


def _extract(zip_path: str, dest_dir: str, cancel, on_status):
    """Extract into dest_dir, refusing any entry that would land outside it."""
    dest_real = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        for i, m in enumerate(members):
            if cancel.is_set():
                raise DownloadCancelled()
            target = os.path.realpath(os.path.join(dest_dir, m.filename))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise IOError(f"Unsafe path in the browser download: {m.filename}")
            z.extract(m, dest_dir)
            if i % 200 == 0:
                on_status(f"Extracting... {i * 100 // max(1, len(members))}%")


def install_browser(rev: str, cancel, on_progress, on_status=lambda s: None) -> str:
    """Download and unpack Chromium `rev`. Blocking — run on a worker thread.
    Returns the path to chrome.exe. Raises DownloadCancelled / IOError / requests
    exceptions; leaves no partial files behind."""
    sha = expected_sha256(rev)
    if not sha:
        raise IOError("This build has no checksum for the browser download, so it won't "
                      "download one. Reinstall using the latest installer instead.")
    root = stable_chromium_dir()
    final_dir = os.path.join(root, f"chromium-{rev}")
    tmp_dir = final_dir + ".tmp"
    zip_final = os.path.join(root, f"chromium-{rev}.zip")
    zip_part = zip_final + ".part"

    shutil.rmtree(tmp_dir, ignore_errors=True)       # leftovers from an interrupted run
    try:
        download_file(download_url(rev), zip_part, zip_final, cancel, on_progress,
                      magic=b"PK", sha256=sha, space_factor=SPACE_FACTOR,
                      magic_error="The downloaded browser file is not a zip file.")
        on_status("Extracting... 0%")
        _extract(zip_final, tmp_dir, cancel, on_status)
        if not os.path.isfile(os.path.join(tmp_dir, "chrome-win64", "chrome.exe")):
            raise IOError("The browser download didn't contain chrome.exe.")
        shutil.rmtree(final_dir, ignore_errors=True)  # exe was missing, so whatever is here is partial
        os.replace(tmp_dir, final_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        for p in (zip_final, zip_part):
            try:
                os.remove(p)
            except OSError:
                pass

    _remove_other_revisions(root, keep=f"chromium-{rev}")
    return browser_exe_path(rev)
