"""
chat_feed.py
-------------
One shared Google Chat room that shows what every moderator's Command Center is doing, so nobody
has to open each person's log: sessions going LIVE / failing / losing their window, polls,
sign-in problems and warnings, and a short run summary.

HOW IT POSTS
  Each app posts DIRECTLY to a Google Chat incoming webhook (the same mechanism the AI Meeting
  Copilot uses). The webhook URL is a secret, so it is not in this code or in the installer: it is
  stored once in the Apps Script's Script Properties (sheet menu: Command Center > Set Chat webhook)
  and fetched at start-up like the Prism token (see GSheetsAPIClient.fetch_chat_webhook).
  CC_CHAT_WEBHOOK (environment variable) overrides it - for testing only.

WHAT KEEPS IT QUIET AND SAFE
  * lines arriving within a few seconds are sent as ONE message, at most one message per
    MIN_INTERVAL seconds (Chat allows about one per second per space) and MAX_LINES lines each;
  * the same line is not repeated within DEDUPE_SECONDS;
  * every moderator's updates for a day go into one Chat thread (`threadKey`);
  * sanitize() cuts a line at the first " | " (that is where screenshot paths and page text start in
    the Event Log), drops anything that looks like a secret (zak=, pwd=, token=, key=, password),
    and replaces every link and every long opaque string with <link> / <redacted>;
  * posting problems never reach the rest of the app and are reported at most once a minute.
The class has no Qt in it (a plain background thread), so it is testable on its own.
"""

import os
import re
import threading
import time
from datetime import datetime

import requests

COALESCE_SECONDS = 3.0        # lines arriving this close together become one message
MIN_INTERVAL = 3.0            # never more than one message per this many seconds (~20 a minute)
MAX_LINES = 15                # per message; the rest is summarised as "+N more"
DEDUPE_SECONDS = 60.0
MAX_LINE_LENGTH = 280
MAX_QUEUE = 200
POST_TIMEOUT = 8.0
RETRY_DELAYS = (1.0, 3.0, 8.0)
ERROR_REPORT_INTERVAL = 60.0

SETTINGS_KEY = "chatfeed/enabled"
WEBHOOK_PREFIX = "https://chat.googleapis.com/"
SELF_PREFIX = "Chat updates"      # the feed's own Event Log lines start with this and are never re-posted

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
_SECRET = re.compile(r"(zak=|pwd=|token=|key=|passw(?:or)?d\s*[:=]|bearer\s|authorization)", re.IGNORECASE)

_EVENT_STYLE = {
    "Session is live": ("🔴 LIVE", None),
    "Session failed": ("❌ FAILED", " - see"),
    "Session window closed": ("⚠️ WINDOW CLOSED", " - use"),
    "Poll is live": ("📊 POLL LIVE", None),
    "Poll failed": ("⚠️ POLL FAILED", None),
}

# Event Log lines that are noise for a shared room.
_SKIP_LOG = re.compile(r"(\[POLL\] attempt|Update check|Blocked duplicate|Released Zoom slot|Poll scan|"
                       r"Switched to|Desktop notifications|participant)", re.IGNORECASE)


def webhook_ok(url: str) -> bool:
    return isinstance(url, str) and url.startswith(WEBHOOK_PREFIX) and "/messages" in url


def sanitize(line: str, max_len: int = MAX_LINE_LENGTH) -> str:
    """One safe line (or "" when it must not be posted)."""
    if not line:
        return ""
    line = line.split(" | ", 1)[0]
    line = " ".join(line.split())
    # A secret check on the raw text first (a URL with zak=/pwd=/token= must drop the whole line),
    # then no link ever leaves the PC and no long opaque string (a token, an id) either: the room
    # needs "what happened", not addresses.
    if not line or _SECRET.search(line):
        return ""
    line = _URL.sub("<link>", line)
    line = _LONG_TOKEN.sub("<redacted>", line)
    return line if len(line) <= max_len else line[: max_len - 1] + "…"


def session_label(m) -> str:
    """'Test (zoom132 · 18:55)': the session name plus what tells two same-named sessions apart."""
    name = (getattr(m, "session_name", "") or "Session").strip()
    bits = []
    try:
        if m.is_zoom() and getattr(m, "host_email", ""):
            bits.append(m.host_email.split("@")[0])
        elif m.is_prism():
            bits.append("Prism")
    except Exception:
        pass
    start = str(getattr(m, "start_time", "") or "")
    hm = re.search(r"(\d{1,2}:\d{2})", start)
    if hm:
        bits.append(hm.group(1))
    return f"{name} ({' · '.join(bits)})" if bits else name


def event_lines(events) -> list:
    """[(title, message, session)] from notifications.detect_events_ex -> chat lines."""
    out = []
    for title, message, m in events:
        label, cut = _EVENT_STYLE.get(title, (title, None))
        what = session_label(m)
        tail = ""
        if title == "Poll failed":
            reason = (getattr(m, "poll_status", "") or "").replace("Failed:", "").strip()
            tail = f": {reason}" if reason else ""
        out.append(sanitize(f"{label} — {what}{tail}"))
    return [x for x in out if x]


def log_line(msg: str, level: str):
    """An Event Log entry -> a chat line, or None when it is not worth posting."""
    if level not in ("warn", "err") or not msg or msg.startswith(SELF_PREFIX) or _SKIP_LOG.search(msg):
        return None
    text = sanitize(msg)
    if not text:
        return None
    return ("❌ " if level == "err" else "⚠️ ") + text


class ChatFeed:
    def __init__(self, moderator_fn, settings=None, host=None, on_error=None,
                 poster=None, clock=time.monotonic):
        """moderator_fn() -> the moderator's name for the message prefix; settings: QSettings-like
        (value/setValue) for the on/off switch; on_error(text): where posting problems are reported;
        poster(url, params, payload) -> status code, replaceable in tests."""
        self._moderator_fn = moderator_fn
        self._settings = settings
        self._host = host or os.environ.get("COMPUTERNAME", "")
        self._on_error = on_error
        self._poster = poster or self._http_post
        self._clock = clock
        self._url = os.environ.get("CC_CHAT_WEBHOOK", "").strip() if webhook_ok(os.environ.get("CC_CHAT_WEBHOOK", "").strip()) else ""
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._queue = []                 # lines waiting to go out
        self._recent = {}                # line -> time last queued (de-duplication)
        self._first_pending = None
        self._last_sent = 0.0
        self._last_error = -1e9
        self._thread = None
        self._stop = False
        self.sent = []                   # messages actually delivered (for tests / diagnostics)
        v = str(settings.value(SETTINGS_KEY, "true")).lower() if settings is not None else "true"
        self._enabled = v not in ("false", "0")

    # ── configuration ────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self._url)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active(self) -> bool:
        return self.configured and self._enabled

    def set_webhook(self, url: str) -> bool:
        if os.environ.get("CC_CHAT_WEBHOOK") and self._url:
            return True                  # the test override wins
        url = (url or "").strip()
        if not webhook_ok(url):
            return False
        self._url = url
        return True

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        if self._settings is not None:
            self._settings.setValue(SETTINGS_KEY, "true" if on else "false")
        if not on:
            with self._lock:
                self._queue.clear()

    # ── posting ──────────────────────────────────────────────────────
    def post(self, *lines):
        """Queue one or more already-written lines (sanitised here again, defensively)."""
        if not self.active:
            return
        now = self._clock()
        with self._lock:
            for raw in lines:
                line = sanitize(raw)
                if not line:
                    continue
                last = self._recent.get(line)
                if last is not None and now - last < DEDUPE_SECONDS:
                    continue
                self._recent[line] = now
                if len(self._queue) < MAX_QUEUE:
                    self._queue.append(line)
            if len(self._recent) > 500:
                self._recent = {k: v for k, v in self._recent.items() if now - v < DEDUPE_SECONDS}
            if self._queue and self._first_pending is None:
                self._first_pending = now
        self._ensure_thread()
        self._wake.set()

    def on_events(self, events):
        """notifications.Notifier listener (session / poll transitions)."""
        self.post(*event_lines(events))

    def on_log(self, msg: str, level: str):
        line = log_line(msg, level)
        if line:
            self.post(line)

    # ── sender ───────────────────────────────────────────────────────
    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="chat-feed", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            self.flush_due()

    def flush_due(self, force: bool = False) -> bool:
        """Send one message if lines have waited long enough (or `force`). Returns True if sent."""
        now = self._clock()
        with self._lock:
            if not self._queue:
                return False
            if not force:
                if now - (self._first_pending or now) < COALESCE_SECONDS or now - self._last_sent < MIN_INTERVAL:
                    return False
            batch, self._queue = self._queue[:MAX_LINES], self._queue[MAX_LINES:]
            more = len(self._queue)
            self._queue = []                      # whatever is beyond the cap is summarised, not carried
            self._first_pending = None
        if more:
            batch.append(f"… +{more} more update(s)")
        self._last_sent = now
        self._send("\n".join(batch))
        return True

    def stop(self, flush_timeout: float = 3.0):
        """Send what is left (best effort, bounded) and stop the thread - used when the app closes."""
        end = time.monotonic() + flush_timeout
        while time.monotonic() < end and self.active:
            if not self.flush_due(force=True):
                break
        self._stop = True
        self._wake.set()

    def _prefix(self) -> str:
        who = (self._moderator_fn() or os.environ.get("USERNAME", "") or "Moderator").strip()
        host = f" ({self._host})" if self._host else ""
        return f"*{who}*{host}"

    def _thread_key(self) -> str:
        who = re.sub(r"[^A-Za-z0-9_-]+", "-", (self._moderator_fn() or "moderator")).strip("-") or "moderator"
        return f"cc-{who}-{datetime.now().strftime('%Y-%m-%d')}"

    def _send(self, body: str):
        text = f"{self._prefix()}\n{body}"
        params = {"threadKey": self._thread_key(), "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"}
        last_problem = ""
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                status = self._poster(self._url, params, {"text": text})
            except Exception as e:
                status = None
                last_problem = f"{type(e).__name__}"
            else:
                if status is not None and 200 <= status < 300:
                    self.sent.append(text)
                    return
                last_problem = f"HTTP {status}"
                if status is not None and status < 500 and status != 429:
                    break                         # a bad webhook / permissions problem: retrying can't fix it
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
        self._report(f"{SELF_PREFIX}: couldn't post to Google Chat ({last_problem}).")

    def _report(self, text: str):
        now = self._clock()
        if self._on_error and now - self._last_error >= ERROR_REPORT_INTERVAL:
            self._last_error = now
            try:
                self._on_error(text)
            except Exception:
                pass

    @staticmethod
    def _http_post(url, params, payload):
        r = requests.post(url, params=params, json=payload, timeout=POST_TIMEOUT)
        return r.status_code
