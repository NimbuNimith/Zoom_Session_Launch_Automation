"""
zoom_start.py
---------------
Small helpers for the "start link from the Zoom API" path (see Engine._zoom_api_start_url).

Why this path exists: Zoom's own sign-in page started refusing the automated browser's email check
(HTTP 403 on /signin/credential_type), so the sign-in can't be relied on. Zoom's official
Server-to-Server OAuth API gives a host START link for a scheduled meeting that opens the meeting as
host with no sign-in at all - the same kind of link Prism sessions already use.

The Zoom credentials never reach this app. They live in the Apps Script's Script Properties and the
script itself talks to Zoom (APPS_SCRIPT_TEMPLATE.js, action=zoom_start_url). The app only sends the
meeting number plus an "app key", and gets one start link back. The app key ships inside the app, so
it keeps casual requests out; the script additionally only answers for sessions listed in the sheet for
today, inside their time window, with request limits. See the long comment in the script.
"""

import os
import re
from urllib.parse import urlparse


def app_key() -> str:
    """The key the Apps Script expects with every start-link request: CC_ZOOM_API_KEY if set (local
    testing), else ZOOM_API_KEY from prism_config.py (git-ignored, bundled into the build). Empty
    means this build has no key, so the start-link path is simply not used."""
    key = os.environ.get("CC_ZOOM_API_KEY", "").strip()
    if key:
        return key
    try:
        import prism_config
        return str(getattr(prism_config, "ZOOM_API_KEY", "") or "").strip()
    except Exception:
        return ""


def valid_start_url(url) -> bool:
    """A host start link must be https, on a zoom.us address, and carry the host token (zak=...).
    Anything else is not used (the sign-in path takes over)."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "zoom.us" or host.endswith(".zoom.us")):
        return False
    return bool(re.search(r"(^|&)zak=[^&\s]+", parts.query))
