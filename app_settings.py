"""
app_settings.py
-----------------
Small persisted key-value store for user preferences that should survive
between app launches — window size/position/maximized state, last CSV
folder, and anywhere else this pattern is useful later.

Uses Qt's own QSettings rather than hand-rolling a JSON file — it's
already a dependency (PySide6), handles the read/write/escaping for us,
and (used in QSettings.IniFormat mode, as below) writes a plain
human-readable .ini file rather than the Windows registry. The registry
is Qt's default on Windows, but an .ini file next to applog.py's log
file is easier to find, inspect, and delete by hand if something ever
needs resetting — same reasoning as why applog.py writes a real file
instead of using Windows Event Log.
"""

import sys
import os

from PySide6.QtCore import QSettings

APP_DIR_NAME = "Command Center"

if sys.platform == "win32":
    _base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    _SETTINGS_DIR = os.path.join(_base, APP_DIR_NAME)
else:
    _SETTINGS_DIR = os.path.expanduser(f"~/.{APP_DIR_NAME.lower().replace(' ', '_')}")

_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "settings.ini")

_settings = None


def get_settings() -> QSettings:
    """Returns a shared QSettings instance backed by a plain .ini file
    at %LOCALAPPDATA%\\Command Center\\settings.ini (or the platform
    equivalent). Safe to call repeatedly — QSettings itself handles
    read/write caching."""
    global _settings
    if _settings is None:
        try:
            os.makedirs(_SETTINGS_DIR, exist_ok=True)
        except Exception as e:
            print(f"Could not create settings directory {_SETTINGS_DIR}: {e}")
        _settings = QSettings(_SETTINGS_PATH, QSettings.IniFormat)
    return _settings
