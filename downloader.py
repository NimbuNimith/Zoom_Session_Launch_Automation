"""
downloader.py
--------------
One streaming file download with progress, shared by the app updater
(updater.py) and the one-time browser download (browser_setup.py). No Qt in
here: callers run it on a background thread and pass a progress callback.

The file is written to `part_path` and only renamed to `final_path` once it
has been checked (size, optional magic bytes, optional SHA-256), so a dropped
connection can never leave something that looks like a finished download.
`part_path` is removed on any failure or cancel.
"""

import hashlib
import os
import shutil
import time
from collections import deque

import requests

CHUNK_SIZE = 256 * 1024          # 8 KB chunks would mean ~55,000 loop turns for a 435 MB file
CONNECT_TIMEOUT = 15             # seconds
READ_TIMEOUT = 30                # a stalled connection fails after this long, not never
PROGRESS_INTERVAL = 0.1          # at most ~10 progress callbacks a second
SPEED_WINDOW = 3.0               # seconds of history the speed/ETA is averaged over
SPACE_MARGIN = 1.1               # need this much free disk space relative to the download size


class DownloadCancelled(Exception):
    pass


def mb(n: float) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def friendly_error(e: Exception) -> str:
    if isinstance(e, requests.exceptions.RequestException):
        return ("The connection was interrupted or the download isn't available. "
                "Check your internet connection and try again.\n"
                f"({type(e).__name__})")
    return str(e)


def download_file(url, part_path, final_path, cancel, on_progress,
                  magic=None, sha256=None, space_factor=SPACE_MARGIN,
                  magic_error="The downloaded file is not the expected kind of file."):
    """Download `url` -> `final_path`.

    cancel       threading.Event, checked every chunk
    on_progress  called as on_progress(bytes_done, total_bytes_or_0, bytes_per_sec)
    magic        required first bytes of the file (b"MZ" for an exe, b"PK" for a zip)
    sha256       expected hex digest of the whole file
    space_factor free disk space needed, as a multiple of the download size
    magic_error  message used when `magic` doesn't match
    Raises DownloadCancelled, IOError (checks failed) or requests exceptions."""
    try:
        with requests.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)

            if total:
                free = shutil.disk_usage(os.path.dirname(final_path) or ".").free
                if free < total * space_factor:
                    raise IOError(
                        f"Not enough free disk space. This needs about "
                        f"{mb(total * space_factor)} and only {mb(free)} is free.")

            received = 0
            hasher = hashlib.sha256() if sha256 else None
            samples = deque([(time.monotonic(), 0)])
            last_emit = 0.0
            speed = 0.0
            with open(part_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    f.write(chunk)
                    if hasher:
                        hasher.update(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    samples.append((now, received))
                    while len(samples) > 2 and now - samples[0][0] > SPEED_WINDOW:
                        samples.popleft()
                    if now - last_emit >= PROGRESS_INTERVAL:
                        last_emit = now
                        span = now - samples[0][0]
                        speed = (received - samples[0][1]) / span if span > 0 else 0.0
                        on_progress(received, total, speed)

        if cancel.is_set():
            raise DownloadCancelled()
        if total and received != total:
            raise IOError(f"The download was incomplete ({mb(received)} of {mb(total)}). "
                          f"Check your connection and try again.")
        if magic:
            with open(part_path, "rb") as f:
                if f.read(len(magic)) != magic:
                    raise IOError(magic_error)
        if hasher and hasher.hexdigest().lower() != sha256.lower():
            raise IOError("The downloaded file failed its integrity check (wrong checksum).")
        on_progress(received, total, speed)
        os.replace(part_path, final_path)
    except BaseException:
        try:
            os.remove(part_path)
        except OSError:
            pass
        raise
