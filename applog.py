"""
applog.py
----------
Persists every Event Log line to disk, retained for 30 days — separate
from (and in addition to) the in-memory Event Log panel in the UI, which
only ever holds the last 500 lines and loses everything on restart.

WHERE LOGS LIVE
----------------
%LOCALAPPDATA%\\Command Center\\logs\\ on Windows (per-user, always
writable without admin rights, survives app updates/reinstalls since
it's outside the install directory). Falls back to ~/.command_center/logs
on other platforms for local dev.

RETENTION
----------
One file per day (command_center_YYYY-MM-DD.log), via
TimedRotatingFileHandler — Python's standard library handles both the
daily rotation and deleting anything older than 30 days; nothing hand-
rolled here that could drift or leak disk space.
"""

import os
import sys
import logging
import logging.handlers

APP_DIR_NAME = "Command Center"

if sys.platform == "win32":
    _base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    LOG_DIR = os.path.join(_base, APP_DIR_NAME, "logs")
else:
    LOG_DIR = os.path.expanduser(f"~/.{APP_DIR_NAME.lower().replace(' ', '_')}/logs")

_LEVEL_MAP = {
    "info": logging.INFO,
    "ok":   logging.INFO,
    "warn": logging.WARNING,
    "err":  logging.ERROR,
}

_logger = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("command_center")
    logger.setLevel(logging.INFO)

    if not logger.handlers:  # guard against duplicate handlers if this
                              # gets called more than once (e.g. in tests)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            handler = logging.handlers.TimedRotatingFileHandler(
                filename=os.path.join(LOG_DIR, "command_center.log"),
                when="midnight",
                backupCount=30,   # 30 days retained, older ones auto-deleted
                encoding="utf-8",
            )
            handler.suffix = "%Y-%m-%d"
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(handler)
        except Exception as e:
            # A broken log directory (permissions, disk full, etc.)
            # should never be the reason the app won't start.
            print(f"File logging disabled — could not set up {LOG_DIR}: {e}")

    _logger = logger
    return logger


def log_to_file(msg: str, level: str = "info"):
    """Same (msg, level) shape as MainWindow.log_event — call this
    alongside the UI update, not instead of it."""
    logger = _get_logger()
    logger.log(_LEVEL_MAP.get(level, logging.INFO), msg)
