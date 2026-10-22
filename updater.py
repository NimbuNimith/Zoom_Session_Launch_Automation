"""
updater.py
-----------
Self-update, in two sizes (since 2.6.0, when the app became a folder install):

  * EXE ONLY  (~12 MB)  - our own code lives in Zoom_Ai_latest.exe; most releases change
                          only that, so only that is downloaded and swapped.
  * FULL PACK (~100 MB) - when the runtime changed (Python, Qt, the Playwright driver,
                          a library): the exe + `_internal\\` + `runtime.id` as one zip.

Which one is decided by `update.json` (repository root, raw GitHub):

    {"version": "2.6.1", "runtime_id": "...", "notes": "...",
     "exe":  {"url": "...", "sha256": "...", "size": 12000000},
     "pack": {"url": "...", "sha256": "...", "size": 100000000}}

compared with the `runtime.id` file next to the installed exe. Everything is SHA-256
checked before anything is replaced. The replace itself is update_swap.py's batch
script, which also puts the old version back if the new one never reports a healthy
start (see on_startup_complete() below).

`version.txt` is NOT used any more and is kept at 1.0.0 in the repository on purpose:
the old single-exe versions (<= 2.5.x) still read it, and must never be offered a
folder-install release (an exe alone would not start). They move to this version by
running the installer once.

Release workflow: tools/make_release_assets.py builds the assets and update.json; bump
CURRENT_VERSION here and in installer.iss first; publish the assets, then update.json.
"""

import json
import os
import shutil
import sys
import threading
import zipfile

import requests
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from downloader import download_file, friendly_error, mb, DownloadCancelled
from update_swap import (write_swap_script, launch_swap_script,
                         MARKER_OK, MARKER_FAILED, STAGE_DIR)

CURRENT_VERSION = "2.7.2"
# CC_UPDATE_URL points a test build at a local update.json (never set on a learner's machine).
UPDATE_URL = os.environ.get("CC_UPDATE_URL") or "https://raw.githubusercontent.com/NimbuNimith/Zoom_Session_Launch_Automation/main/update.json"

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _url_ok(url: str) -> bool:
    """https only. The one exception: plain http to the loopback address, and only while the
    CC_UPDATE_URL test override is set, so a test build can be updated from a local server.
    The host is parsed, not prefix-matched ("http://127.0.0.1.evil.com" must not pass)."""
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        host = (p.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if p.scheme == "https" and host:
        return True
    return bool(os.environ.get("CC_UPDATE_URL")) and p.scheme == "http" and p.username is None and host in _LOOPBACK_HOSTS

PACK_SPACE_FACTOR = 3.0   # the zip, plus the unpacked copy, plus room for the old folder


# ── pure helpers (no Qt, no network - unit-tested) ───────────────────────

def app_dir():
    """The install folder, or None when running from source."""
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None


def installed_runtime_id() -> str:
    d = app_dir()
    if not d:
        return ""
    try:
        with open(os.path.join(d, "runtime.id"), encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return ""


def parse_manifest(text: str) -> dict:
    """Validates update.json; raises ValueError on anything unusable."""
    try:
        m = json.loads(text)
        out = {"version": str(m["version"]).strip(), "runtime_id": str(m.get("runtime_id", "")).strip(),
               "notes": str(m.get("notes", "")).strip()}
        for kind in ("exe", "pack"):
            a = m[kind]
            out[kind] = {"url": str(a["url"]), "sha256": str(a["sha256"]).lower(), "size": int(a.get("size", 0))}
            if not _url_ok(out[kind]["url"]) or len(out[kind]["sha256"]) != 64:
                raise ValueError(f"bad {kind} entry")
        if not out["version"]:
            raise ValueError("no version")
        return out
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"unreadable update.json ({e})")


def is_newer(latest: str, current: str) -> bool:
    try:
        from packaging import version as pkg_version
        return pkg_version.parse(latest) > pkg_version.parse(current)
    except Exception:
        return False


def choose_level(manifest: dict, installed_rid: str) -> str:
    """'exe' when the runtime is unchanged, otherwise 'pack' (also when we can't tell)."""
    if installed_rid and installed_rid == manifest["runtime_id"]:
        return "exe"
    return "pack"


def extract_pack(zip_path: str, stage_dir: str, exe_name: str, on_status=None):
    """Unpack the pack into a fresh staging folder (never into the live folder) and make
    sure it is complete. Rejects entries that would land outside the folder."""
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir, ignore_errors=True)
    os.makedirs(stage_dir)
    root = os.path.realpath(stage_dir)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for n in names:
            target = os.path.realpath(os.path.join(stage_dir, n))
            if target != root and not target.startswith(root + os.sep):
                raise IOError("The update package contains an unsafe path.")
        if on_status:
            on_status("Unpacking the update...")
        z.extractall(stage_dir)
    for required in (exe_name, "runtime.id", os.path.join("_internal", "base_library.zip")):
        if not os.path.exists(os.path.join(stage_dir, required)):
            raise IOError(f"The update package is incomplete (missing {required}).")


# ── start-up housekeeping ────────────────────────────────────────────────

def on_startup_complete(window):
    """Called once the main window is up (app.py). Reports a healthy start to the swap
    script (which then drops the old version), cleans update leftovers, tells the user if
    an update was rolled back, and logs a one-off 'Updated to vX'."""
    d = app_dir()
    if d:
        try:
            with open(os.path.join(d, MARKER_OK), "w") as f:
                f.write(CURRENT_VERSION)
        except OSError:
            pass
        _cleanup_leftovers(d)
        failed = os.path.join(d, MARKER_FAILED)
        if os.path.isfile(failed):
            try:
                with open(failed, encoding="utf-8", errors="replace") as f:
                    bad = f.read().strip()
                os.remove(failed)
            except OSError:
                bad = ""
            _remember_failed_version(bad)
            QTimer.singleShot(1500, lambda: _tell_rolled_back(window, bad))
    try:
        from app_settings import get_settings
        s = get_settings()
        last = s.value("app/last_version", "")
        s.setValue("app/last_version", CURRENT_VERSION)
        if last and last != CURRENT_VERSION and is_newer(CURRENT_VERSION, str(last)):
            window.log_event(f"Updated to v{CURRENT_VERSION} (from v{last}).", "ok")
    except Exception:
        pass


def _cleanup_leftovers(d: str):
    """Things a finished (or abandoned) update can leave next to the exe. The .old copies
    are only removed here - after a healthy start - never earlier."""
    exe = sys.executable
    for path in (os.path.join(d, STAGE_DIR), os.path.join(d, "_internal.old"), os.path.join(d, "_internal.new")):
        shutil.rmtree(path, ignore_errors=True)
    for path in (exe + ".new", exe + ".new.part", exe + ".old", os.path.join(d, "runtime.id.old"),
                 os.path.join(d, "_update_pack.zip"), os.path.join(d, "_update_pack.zip.part")):
        try:
            os.remove(path)
        except OSError:
            pass


def _remember_failed_version(version: str):
    try:
        from app_settings import get_settings
        get_settings().setValue("update/failed_version", version)
    except Exception:
        pass


def _tell_rolled_back(window, bad_version: str):
    from main_window import notify
    notify(window, "Update Not Applied",
           f"The update to v{bad_version or '?'} didn't start correctly, so the previous version "
           f"(v{CURRENT_VERSION}) was put back. Nothing was lost.\n\n"
           "It won't be offered automatically again; use the Check for updates button to retry.")


# ── the download + swap ──────────────────────────────────────────────────

class _DownloadSignals(QObject):
    """Lives on the UI thread; the worker thread only ever .emit()s on it, which
    Qt delivers to the UI thread's event loop (queued connection)."""
    progress = Signal(object, object, float)   # bytes done, total bytes (0 = unknown), bytes/sec
    status = Signal(str)
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()


def _download_worker(level: str, asset: dict, exe_path: str, cancel: threading.Event, sig: _DownloadSignals):
    """Runs on a background thread. Never touches Qt widgets - it only emits."""
    d = os.path.dirname(exe_path)
    try:
        if level == "exe":
            final = exe_path + ".new"
            download_file(asset["url"], final + ".part", final, cancel, sig.progress.emit, magic=b"MZ",
                          sha256=asset["sha256"], magic_error="The downloaded file is not a Windows program.")
        else:
            zip_path = os.path.join(d, "_update_pack.zip")
            download_file(asset["url"], zip_path + ".part", zip_path, cancel, sig.progress.emit, magic=b"PK",
                          sha256=asset["sha256"], space_factor=PACK_SPACE_FACTOR,
                          magic_error="The downloaded file is not a valid update package.")
            if cancel.is_set():
                raise DownloadCancelled()
            sig.status.emit("Unpacking the update...")
            try:
                extract_pack(zip_path, os.path.join(d, STAGE_DIR), os.path.basename(exe_path))
            finally:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
    except DownloadCancelled:
        sig.cancelled.emit()
    except Exception as e:
        sig.failed.emit(friendly_error(e))
    else:
        sig.finished.emit()


class _UpdateDownload(QObject):
    """Owns one update: the progress dialog, the download thread, and the
    hand-off to the replace-and-restart script once everything is ready."""

    def __init__(self, parent, manifest: dict, level: str, current_exe: str):
        super().__init__(parent)
        from main_window import UpdateProgressDialog  # local import: main_window imports this module
        self._parent = parent
        self._exe = current_exe
        self._manifest = manifest
        self._level = level
        self._cancel = threading.Event()
        self._sig = _DownloadSignals(self)
        self._dlg = UpdateProgressDialog(parent, manifest["version"])

        self._sig.progress.connect(self._dlg.set_progress)
        self._sig.status.connect(self._dlg.set_status)
        self._sig.finished.connect(self._on_finished)
        self._sig.failed.connect(self._on_failed)
        self._sig.cancelled.connect(self._on_cancelled)
        self._dlg.cancel_requested.connect(self._cancel.set)

    def start(self):
        self._dlg.open()
        # daemon: closing the app mid-download must never wait on this thread
        threading.Thread(
            target=_download_worker, daemon=True, name="update-download",
            args=(self._level, self._manifest[self._level], self._exe, self._cancel, self._sig),
        ).start()

    def _discard_downloaded(self):
        d = os.path.dirname(self._exe)
        shutil.rmtree(os.path.join(d, STAGE_DIR), ignore_errors=True)
        try:
            os.remove(self._exe + ".new")
        except OSError:
            pass

    @Slot()
    def _on_finished(self):
        self._dlg.finish()
        self._release()
        if self._cancel.is_set():
            # Cancel was clicked in the last instant, after the download was
            # already complete: honour it rather than restarting anyway.
            self._discard_downloaded()
            return
        # The swap script (why it retries, clears PyInstaller's env vars and rolls
        # back) lives in update_swap.py.
        version = self._manifest["version"]
        if self._level == "exe":
            bat_path = write_swap_script(self._exe, new_exe_path=self._exe + ".new", new_version=version)
        else:
            bat_path = write_swap_script(self._exe, stage_dir=os.path.join(os.path.dirname(self._exe), STAGE_DIR),
                                         new_version=version)

        # NOT sys.exit(0) - that skips MainWindow.closeEvent(), which is where the
        # "sessions are LIVE, are you sure?" confirmation lives, along with the only
        # code that calls engine.shutdown() to close the Playwright browser cleanly.
        # close() returns False when the user declines that prompt; the script is
        # only started once the close is accepted, so declining leaves nothing running
        # (the downloaded files stay and the update is offered again next start).
        if self._parent.close():
            launch_swap_script(bat_path)
        else:
            try:
                os.remove(bat_path)
            except OSError:
                pass

    @Slot(str)
    def _on_failed(self, message: str):
        from main_window import notify
        self._dlg.finish()
        self._release()
        self._discard_downloaded()
        notify(self._parent, "Update Failed", f"Could not download the update:\n{message}")

    @Slot()
    def _on_cancelled(self):
        self._dlg.finish()
        self._release()
        self._discard_downloaded()

    def _release(self):
        global _active
        _active = None
        self.deleteLater()


_active = None   # the one in-flight _UpdateDownload, if any (also keeps it from being garbage-collected)


def _do_update(parent, manifest: dict):
    global _active
    from main_window import notify  # local import avoids a circular import at module load

    current_exe = sys.executable if getattr(sys, "frozen", False) else None
    if not current_exe:
        notify(parent, "Update Available",
               f"Version {manifest['version']} is available.\n\n"
               "This is running from source, not the installed app, so it can't update itself.")
        return
    if _active is not None:
        return   # an update is already downloading

    level = choose_level(manifest, installed_runtime_id())
    _active = _UpdateDownload(parent, manifest, level, current_exe)
    _active.start()


# ── the check ────────────────────────────────────────────────────────────

class _VersionCheck(QObject):
    """Fetches update.json on a background thread and hands the result back to the UI
    thread. manual=True (the Check for updates button) always answers - 'up to date' or
    'couldn't check' - while the automatic check at start-up stays silent on any failure."""
    result = Signal(object, str)   # manifest dict (or None), error text

    def __init__(self, parent, manual: bool):
        super().__init__(parent)
        self._parent = parent
        self._manual = manual
        self.result.connect(self._on_result)

    def start(self):
        threading.Thread(target=self._fetch, daemon=True, name="update-check").start()

    def _fetch(self):
        from applog import log_to_file
        try:
            resp = requests.get(UPDATE_URL, timeout=8, headers={"Cache-Control": "no-cache"})
            resp.raise_for_status()
            manifest = parse_manifest(resp.text)
            log_to_file(f"Update check: latest is v{manifest['version']}, running v{CURRENT_VERSION}.", "info")
            self.result.emit(manifest, "")
        except Exception as e:
            log_to_file(f"Update check failed: {type(e).__name__}: {str(e)[:200]}", "warn")
            self.result.emit(None, f"{type(e).__name__}")

    @Slot(object, str)
    def _on_result(self, manifest, error: str):
        global _check_running
        _check_running = False
        from main_window import notify, confirm
        if manifest is None:
            if self._manual:
                notify(self._parent, "Check for Updates",
                       "Couldn't check for updates right now. Check your internet connection and try again.")
            return
        latest = manifest["version"]
        if not is_newer(latest, CURRENT_VERSION):
            if self._manual:
                notify(self._parent, "Check for Updates", f"You're up to date (v{CURRENT_VERSION}).")
            return
        if not self._manual:
            try:
                from app_settings import get_settings
                if str(get_settings().value("update/failed_version", "")) == latest:
                    return   # this version was rolled back on this computer; only a manual check retries it
            except Exception:
                pass

        level = choose_level(manifest, installed_runtime_id())
        size = manifest[level]["size"]
        text = f"A new version is available: v{latest}\nYou have: v{CURRENT_VERSION}\n"
        if size:
            text += f"\nDownload: about {mb(size)}"
            if level == "pack":
                text += " (includes updated components)"
        if manifest["notes"]:
            text += f"\n\nWhat's new:\n{manifest['notes']}"
        text += "\n\nDownload and restart now?"

        def _prompt():
            if confirm(self._parent, "Update Available", text, danger=False):
                _do_update(self._parent, manifest)
        QTimer.singleShot(1000 if not self._manual else 0, _prompt)


_check = None
_check_running = False


def check_for_update(parent, manual: bool = False):
    """Call once, shortly after the main window is shown (see app.py), and from the
    Check for updates button with manual=True. The automatic call is a silent no-op on
    any failure - a broken update check should never be the reason the app won't open."""
    global _check, _check_running
    if _check_running:
        return
    _check_running = True
    try:
        from applog import log_to_file
        log_to_file(f"Update check started ({'manual' if manual else 'automatic'}) -> {UPDATE_URL}", "info")
    except Exception:
        pass
    _check = _VersionCheck(parent, manual)
    _check.start()
