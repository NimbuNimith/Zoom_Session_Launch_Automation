"""
startup_window.py
------------------
The window shown while the app starts: logo, the current step, and a real
progress bar. It replaces the PyInstaller bootloader splash (which listed
unpacked file names and was always-on-top) and the painted Qt splash.

It is an ordinary top-level window: it has a taskbar button, can be
minimized, and sits behind other windows like any other app — starting the
app never takes over the screen. There is deliberately no close button
(closing it would leave the app half-started); the taskbar/Alt+F4 route still
works because closeEvent is only refused for the bar's own title-bar button.

The bar never moves backwards and never sits still: each step has a
"start" and an "end" percentage; the displayed value jumps to the step's
start and then creeps toward (but never reaches) its end until the next
step begins, so a slow step still looks alive.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QLabel, QProgressBar, QVBoxLayout, QHBoxLayout

import theme
from paths import resource_path


class StartupWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Command Center — Starting")
        # Title bar + minimize + system menu, no close/maximize, NOT always-on-top.
        self.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint
                            | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint)
        icon_path = resource_path("assets/icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setFixedSize(460, 230)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.BG}; color: {theme.INK}; }}"
            f"QProgressBar {{ background: {theme.SURFACE_RAISED}; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {theme.RED}; border-radius: 4px; }}"
        )

        self._value = 0.0
        self._start = 0.0
        self._end = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        if os.path.exists(icon_path):
            logo.setPixmap(QIcon(icon_path).pixmap(40, 40))
        head.addWidget(logo)
        title = QLabel("Zoom + Prism\nCommand Center")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        head.addWidget(title, 1)
        root.addLayout(head)
        root.addSpacing(26)

        self._step = QLabel("Starting up…")
        self._step.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        root.addWidget(self._step)
        root.addSpacing(8)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        root.addWidget(self._bar)
        root.addStretch(1)

        hint = QLabel("You can minimize this window — the app keeps starting.")
        hint.setStyleSheet(f"font-size: 11px; color: {theme.MUTED};")
        root.addWidget(hint)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── progress ─────────────────────────────────────────────────────
    def step(self, text: str, start: float, end: float):
        """Begin a step: show `text`, jump the bar to `start`%, and let it creep
        toward `end`% until the next step() call."""
        self._step.setText(text)
        self._start = max(self._start, float(start))
        self._end = max(self._start, float(end))

    def value(self) -> float:
        return self._value

    def _tick(self):
        if self._value < self._start:
            self._value += max(1.0, (self._start - self._value) * 0.35)
            self._value = min(self._value, self._start)
        elif self._value < self._end:
            self._value += (self._end - self._value) * 0.02
        self._bar.setValue(int(self._value * 10))

    def finish_instantly(self):
        self._start = self._end = self._value = 100.0
        self._bar.setValue(1000)

    def closeEvent(self, event):
        # Only the app itself closes this window (via _close_for_real).
        if getattr(self, "_closing_for_real", False):
            event.accept()
        else:
            event.ignore()

    def close_for_real(self):
        self._closing_for_real = True
        self._timer.stop()
        self.close()
