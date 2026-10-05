"""
splash.py
----------
A startup splash screen — shown the instant the app has real work to do
(single-instance check passed), gone the moment MainWindow is up.

WHAT THIS DOES AND DOESN'T FIX
--------------------------------
This covers the QApplication + MainWindow construction phase — real but
usually brief. It does NOT cover:
  - PyInstaller onefile extraction (happens before any Python code runs
    at all — nothing in-process can show anything during that phase).
  - Playwright launching Chromium (happens AFTER MainWindow is shown,
    as a background task — see app.py). The main window's own Event Log
    already surfaces this ("Starting browser engine...", "Browser
    engine ready.") and the app is genuinely usable meanwhile (CSV
    upload, Add Session, etc. don't need the browser yet) — so handing
    off to the real window early is a feature, not a shortcut.
If onefile extraction turns out to be the dominant wait (very possible
now that Chromium is bundled — see Zoom_Ai_latest.spec's docstring),
this splash won't make that part faster; only switching to a --onedir
build addresses that.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QSplashScreen

from theme import BG, RED, INK, MUTED


def _build_splash_pixmap() -> QPixmap:
    """Painted directly, not loaded from a file — same visual language
    as theme.py (graphite canvas, red brand accent, the wordmark-dot
    motif from the header bar) without needing a separate image asset
    to bundle and keep in sync if the design changes."""
    w, h = 440, 260
    pix = QPixmap(w, h)
    pix.fill(QColor(BG))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.fillRect(0, 0, w, 5, QColor(RED))  # top accent bar, matches header styling

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(RED))
    painter.drawEllipse(44, 96, 16, 16)  # wordmark dot, same motif as the header

    painter.setPen(QColor(INK))
    painter.setFont(QFont("Segoe UI", 21, QFont.Bold))
    painter.drawText(74, 111, "Zoom + Prism")
    painter.drawText(44, 142, "Command Center")

    painter.setPen(QColor(MUTED))
    painter.setFont(QFont("Segoe UI", 11))
    painter.drawText(44, 175, "Starting up…")

    painter.end()
    return pix


def show_splash() -> QSplashScreen:
    """Call right after the single-instance check passes. Caller owns
    the returned QSplashScreen — call splash.finish(window) once
    MainWindow is constructed and about to be shown (Qt's own idiom for
    a clean handoff from splash to real window)."""
    splash = QSplashScreen(_build_splash_pixmap())
    splash.show()
    return splash
