"""
network_watch.py
------------------
A slow or flaky internet connection is, next to low memory, what makes meeting pages, polls and sign-ins
stall (seen 2026-10-31: on one PC the Google Chat posts failed with SSL/connection errors from 09:14 to
10:35 and four poll launches then sat for 22 minutes with nothing in the Event Log).

Every CHECK_EVERY seconds, while a session window is open, the engine times a tiny request to Zoom and to
Google (the same two places the app itself needs). A check is "bad" when a request is slow or fails; the
connection counts as poor once POOR_AT of the last WINDOW checks were bad, and as back to normal after
RECOVER_AFTER good checks in a row. The Event Log (and so the shared Chat room) gets ONE warning when
it turns poor, a reminder every REMIND_EVERY seconds while it stays poor, and one line when it recovers.

This module only measures and decides; it changes nothing about how sessions or polls are launched.
"""

import time
from collections import deque

import requests

CHECK_EVERY = 30            # seconds between checks while a session window is open
SLOW_SECONDS = 3.0          # a request slower than this is "slow" (a healthy one answers in 0.4 - 1 s)
TIMEOUT_SECONDS = 8.0       # a request with no answer by now has failed
WINDOW = 4                  # how many of the latest checks are looked at
POOR_AT = 3                 # this many bad ones in the window = poor connection (about 1.5 minutes)
RECOVER_AFTER = 2           # this many good checks in a row = back to normal
REMIND_EVERY = 600          # while poor, say so again this often

PROBES = (("Zoom", "https://zoom.us/"), ("Google", "https://script.google.com/"))


def probe_once(probes=PROBES, head=requests.head) -> dict:
    """{name: seconds to the first answer, or None when there was none}. Blocking (run it in a thread).
    HEAD, no redirects followed, nothing read: a few hundred bytes per check."""
    out = {}
    for name, url in probes:
        started = time.perf_counter()
        try:
            head(url, timeout=TIMEOUT_SECONDS, allow_redirects=False)
            out[name] = time.perf_counter() - started
        except Exception:
            out[name] = None
    return out


def _is_bad(sample: dict) -> bool:
    return any(t is None or t > SLOW_SECONDS for t in sample.values())


def _describe(sample: dict) -> str:
    """'Zoom answered in 4.2 s, Google did not answer'."""
    parts = []
    for name, t in sample.items():
        if t is None:
            parts.append(f"{name} did not answer")
        elif t > SLOW_SECONDS:
            parts.append(f"{name} answered in {t:.1f} s")
    return ", ".join(parts) or "slow answers"


class NetworkWatch:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._recent = deque(maxlen=WINDOW)
        self._good_run = 0
        self.poor = False
        self._last_warned = -1e9

    def add(self, sample: dict) -> list:
        """Feed one check; returns the messages to log: [("poor", text)] or [("ok", text)] or []."""
        bad = _is_bad(sample)
        self._recent.append(bad)
        self._good_run = 0 if bad else self._good_run + 1
        now = self._clock()
        if not self.poor:
            if sum(self._recent) >= POOR_AT:
                self.poor = True
                self._last_warned = now
                return [("poor", self._poor_text(sample))]
            return []
        if self._good_run >= RECOVER_AFTER:
            self.poor = False
            self._recent.clear()
            self._last_warned = -1e9
            worst = max((t for t in sample.values() if t is not None), default=None)
            return [("ok", "Internet is back to normal" + (f" (answers in {worst:.1f} s)." if worst is not None else "."))]
        if bad and now - self._last_warned >= REMIND_EVERY:
            self._last_warned = now
            return [("poor", self._poor_text(sample, still=True))]
        return []

    @staticmethod
    def _poor_text(sample: dict, still: bool = False) -> str:
        lead = "This PC's internet is still slow or unstable" if still else "This PC's internet looks slow or unstable"
        return (f"{lead} ({_describe(sample)}). Meeting pages, polls and sign-ins can stall or time out - "
                f"check the Wi-Fi or VPN, or switch to another network.")
