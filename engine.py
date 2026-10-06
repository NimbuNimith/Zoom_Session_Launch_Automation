"""
engine.py
----------
The async Playwright engine — launch/end/monitor/Prism-push. Ported from
Zoom_Ai_V54.py's business logic verbatim where it's Playwright/Prism
mechanics (selectors, timing, retry counts, the zoommtg:// protocol
blocker, URL conversion) — none of that is Tkinter-specific and none of
it changes here.

What DID change, and why:
  - Module-level globals (session_store, running_sessions,
    active_accounts, browser_instance, ...) are now instance state on an
    Engine object. Same data, no globals — makes this testable and
    removes the implicit "there is exactly one app" assumption.
  - `app.root.after(0, lambda: ...)` is gone. That existed purely to
    marshal calls from the asyncio thread back onto Tkinter's UI thread.
    With qasync, the asyncio loop and the Qt event loop are the SAME
    loop on the SAME thread — so engine code just calls
    `self.model.set_fields(...)` / `self.log(...)` directly. This is the
    concrete asyncio-native benefit PySide6 (via qasync) gives you here.
  - `session_store` (a dict) is replaced by `self.model`, a
    SessionTableModel — the Qt model IS the store, so a status update
    and a UI update are the same call, not two things kept in sync by
    hand.
"""

import asyncio
import os
import random
import re

from playwright.async_api import async_playwright

from models import SessionModel, SessionTableModel
from prism_status_push import push_prism_live_status_async

MAX_SESSIONS_PER_ACCOUNT = 2

DEFAULT_POLL_NAME = "End Session Poll"   # fallback / default poll to pre-select
POLL_LAUNCH_MAX_ATTEMPTS = 2             # 1 initial try + 1 retry
AUTO_POLL_END_MINUTES = 15               # auto-launch poll(s) N minutes before End Time

# The host/co-host row-by-row scrape in get_zoom_participant_info is
# O(participant count) — the dominant cost of _participant_scan_loop
# once classes get large, and it runs for every LIVE session on every
# tick. Host rarely changes mid-session and co-host changes are rare
# and not time-critical for a monitoring dashboard, so once a host has
# been found at least once, _scan_one_participant only repeats the
# full row-by-row scrape every Nth tick — every other tick just
# refreshes the cheap participant COUNT (a single inner_text() call).
PARTICIPANT_FULL_SCAN_EVERY = 5          # every 5th tick (~60s at the current 12s interval)

_PARTICIPANTS_BTN_SEL = (
    "button:has-text('Participants'), "
    "[aria-label*='Participants'], "
    "button[aria-label*='participant']"
)
_HOST_RE   = re.compile(r"^(.*?)\s*\(\s*Host\b",    re.IGNORECASE)
_COHOST_RE = re.compile(r"^(.*?)\s*\(\s*Co-?host\b", re.IGNORECASE)

# ── Poll automation selectors ───────────────────────────────────────────
# Ported from a colleague's prism_auto_launcher.py — pure Playwright
# mechanics (drives Participants → More → Polls → <poll> → Launch on the
# Zoom web client), no app-framework dependency, so ported near-verbatim
# per this project's established porting convention (see engine.py's
# module docstring). Deliberately broad selectors with has-text()
# substring matches rather than exact-text regexes — that's what
# actually failed in earlier iterations, since the real DOM shape of
# these rows doesn't reliably match an exact-text assumption across Zoom
# web-client builds.
_MORE_BTN_SEL = (
    "button:has-text('More'), "
    "[aria-label='More'], [aria-label*='More button']"
)
_POLLS_MENU_ITEM_SEL = (
    "[role='menuitem']:has-text('Polls'), "
    "li:has-text('Polls'), "
    "div[role='menuitem']:has-text('Polls'), "
    "button:has-text('Polls'), "
    "a:has-text('Polls'), "
    "[class*='menu-item']:has-text('Polls'), "
    # Confirmed live 2026-09-28: the 'More' menu now sometimes renders as
    # a grid of tiles instead of a classic dropdown list — each tile is a
    # plain <div role="button" class="more-button__item-box"> (no
    # menuitem role, no button tag), which none of the selectors above
    # match, so 'Polls' item never appeared... was a real, accurate
    # error, not a false negative. This is the current known DOM for that
    # tile; kept alongside every older alternative above (not replacing
    # them) since Zoom has switched between dropdown-list and grid-tile
    # 'More' menus across different sessions in this same testing round.
    "[role='button']:has-text('Polls'), "
    ".more-button__item-box:has-text('Polls')"
)
_POLLS_PANEL_TITLE_SEL = "text=Polls/Quizzes"

LAUNCH_STATUSES = {
    "🔑 Logging in...", "🌍 Starting...",
    "🚀 Queued...", "Launching...",
    "🟣 Opening Prism...", "🟣 Detecting Layout...",
    "🟣 Setting Devices...", "🟣 Joining...",
}


def clean_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r'[}]+#\w+$', '', url)
    url = re.sub(r'#\w+[}]*$', '', url)
    return url.rstrip('}').strip()


def detect_url_type(url: str) -> str:
    if re.search(r"/w/\d+", url):  return "w"
    if re.search(r"/wc/\d+", url): return "wc"
    if re.search(r"/s/\d+", url):  return "s"
    return "j"


def build_launch_url(mid: str, raw_url: str, mode: str = "start") -> str:
    """See Zoom_Ai_V54.py's version for the full rationale (zoommtg://
    handoff avoidance) — logic unchanged."""
    url_type = detect_url_type(raw_url)
    host_match = re.search(r'https?://([^/]+)/', raw_url)
    host = host_match.group(1) if host_match else "upgrad.zoom.us"
    tk  = re.search(r'tk=([^&\s}]+)',  raw_url)
    zak = re.search(r'zak=([^&\s}]+)', raw_url)
    pwd = re.search(r'pwd=([^&\s}]+)', raw_url)

    if url_type == "w":
        params = []
        if tk:  params.append(f"tk={tk.group(1)}")
        if pwd: params.append(f"pwd={pwd.group(1)}")
        params.append("prefer=1")
        return f"https://{host}/wc/{mid}/join?" + "&".join(params)

    base = f"https://{host}/wc/{mid}/{mode}"
    params = []
    if zak: params.append(f"zak={zak.group(1)}")
    if pwd: params.append(f"pwd={pwd.group(1)}")
    return base + ("?" + "&".join(params) if params else "")


async def _block_zoom_protocol(route):
    # Registered with the route pattern "zoommtg://**" (see call sites),
    # so this only ever fires for that one protocol — every normal
    # http(s) request on the page bypasses Python/CDP entirely and is
    # handled natively by Chromium. An earlier version registered this
    # against "**/*" (every request on the page), which meant every
    # single network call — JS bundles, images, XHRs, Google's
    # reCAPTCHA verification calls during Zoom sign-in — had to round-
    # trip through this async Python callback before being allowed to
    # continue. Confirmed live 2026-09-28: launching 5 Zoom sessions
    # concurrently, 4 failed sign-in with Zoom's own page showing
    # "Could not connect to the reCAPTCHA service" — not a real
    # connectivity problem, but reCAPTCHA's tight client-side timeout
    # tripping under the added latency from routing hundreds of
    # concurrent requests across 5 pages through one Python event loop.
    try:    await route.abort()
    except Exception: pass


class Engine:
    def __init__(self, model: SessionTableModel, log_fn):
        self.model = model
        self.log = log_fn                      # callable(msg: str, level: str)
        self.running_locks: set[str] = set()
        self.active_accounts: dict[str, set[str]] = {}
        self.guest_pages: list = []
        self._participant_scan_ticks: dict[str, int] = {}   # url -> tick count, see PARTICIPANT_FULL_SCAN_EVERY
        self.participant_scraping_enabled = True            # user-facing perf toggle, see set_participant_scraping()
        self._page_locks: dict[str, asyncio.Lock] = {}       # url -> lock, see _page_lock_for()
        self.browser_ready = False
        self._playwright = None
        self._browser = None
        self._tasks: list[asyncio.Task] = []
        self._browser_not_ready_warned = False

    # ── Startup ──────────────────────────────────────────────────────
    async def start(self):
        """Without the try/except below, a Chromium launch failure here
        (most commonly: Playwright's browser was never actually installed
        by the installer's `--playwright-install` step) propagates out of
        this coroutine with nowhere to go — it's scheduled via a bare
        `loop.create_task(window.engine.start())` in app.py that nothing
        ever awaits, so asyncio silently discards it as an "exception was
        never retrieved" warning. In a console=False build (see the
        .spec) that warning has no console to print to anyway, so the
        failure was completely invisible: browser_ready just stays False
        forever, and every later launch_zoom()/launch_prism() call bails
        out on its own silent `if not self.browser_ready: return` —
        which is exactly the symptom of Auto-Pilot retrying forever with
        no further log lines. Catching it here and routing it through
        self.log() (same function the rest of the engine uses) puts the
        real error in front of the user immediately, in both the Event
        Log panel and the persistent file log."""
        try:
            resolved = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            note = resolved if resolved else "<unset — expected; Chromium is bundled, frozen-mode default applies>"
            self.log(f"Starting browser engine — PLAYWRIGHT_BROWSERS_PATH = {note}", "info")
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=["--deny-permission-prompts", "--window-size=1200,800"],
            )
        except Exception as e:
            self.log(
                f"❌ Failed to start the browser engine: {e}. Sessions cannot "
                f"launch until this is fixed — check that Chromium was "
                f"installed correctly (see the app's install/setup docs).",
                "err",
            )
            return
        self.browser_ready = True
        self.log("✅ Browser engine ready.", "ok")
        self._tasks.append(asyncio.create_task(self._fast_live_loop()))
        self._tasks.append(asyncio.create_task(self._status_text_loop()))
        self._tasks.append(asyncio.create_task(self._participant_scan_loop()))

    async def shutdown(self):
        """Bounded, step-by-step teardown. This is called inside a 5s
        asyncio.wait_for(...) from MainWindow._shutdown_and_quit — but
        that outer bound only protects the caller, not the steps inside
        here. Without per-step timeouts, a single hung await (e.g.
        browser.close() on an unresponsive CDP connection) eats the
        entire outer budget by itself: the outer wait_for cancels this
        coroutine while it's still stuck on that first await, and
        playwright.stop() — which kills the separate Node driver
        subprocess — never runs at all. That's the same "still running
        in Task Manager" symptom as the original bug, just one process
        down. Giving each step its own short timeout guarantees every
        step at least gets attempted, in order, inside the outer budget.
        """
        # 1. Cancel the 3 heartbeat loops and actually wait for them to
        #    finish unwinding — not just fire-and-forget .cancel() — so
        #    none of them can touch a page mid-close below.
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True), timeout=0.5)
            except asyncio.TimeoutError:
                self.log("Heartbeat loops didn't cancel within 0.5s — continuing anyway.", "warn")

        # 2. Close the browser — the step most likely to hang (a crashed
        #    renderer or stuck CDP pipe), so it gets the largest slice.
        if self._browser:
            try:
                await asyncio.wait_for(self._browser.close(), timeout=2.5)
            except asyncio.TimeoutError:
                self.log("browser.close() timed out after 2.5s — abandoning it.", "warn")
            except Exception as e:
                self.log(f"browser.close() error during shutdown: {e}", "warn")

        # 3. Stop the Playwright driver process. Runs regardless of what
        #    happened in step 2 — this is the whole point of giving it
        #    its own timeout instead of sharing one unbounded await.
        if self._playwright:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=1.0)
            except asyncio.TimeoutError:
                self.log("playwright.stop() timed out after 1s — abandoning it.", "warn")
            except Exception as e:
                self.log(f"playwright.stop() error during shutdown: {e}", "warn")

        # 4. Belt-and-suspenders: browser.close() in step 2 is a graceful
        #    request over the CDP connection, and if it's still mid-flight
        #    when its 2.5s budget runs out, step 2 just abandons it — the
        #    Chromium process(es) it spawned can be left running as
        #    orphans. Under load (many concurrent contexts, a busy
        #    machine) this is exactly what was observed: chrome.exe
        #    processes surviving well past the app closing, accumulating
        #    across repeated launches until a full OS restart cleared
        #    them and fixed the slowness. This only ever terminates
        #    processes that are actual descendants of THIS process (never
        #    a system-wide kill by name), so it can never touch the
        #    user's own browser windows.
        survivor_count = await asyncio.to_thread(self._kill_orphaned_chromium)
        if survivor_count:
            self.log(f"Cleaned up {survivor_count} leftover Chromium process(es) after shutdown.", "warn")

    def _kill_orphaned_chromium(self) -> int:
        """Runs off the Qt/asyncio thread via asyncio.to_thread — psutil's
        process enumeration/termination is blocking. Returns the count
        killed; the caller logs it back on the main thread, since
        self.log() touches a Qt widget and isn't safe to call from a
        worker thread."""
        try:
            import psutil
            me = psutil.Process(os.getpid())
            survivors = [p for p in me.children(recursive=True)
                         if p.is_running() and "chrome" in (p.name() or "").lower()]
            for p in survivors:
                try: p.terminate()
                except Exception: pass
            if survivors:
                _, alive = psutil.wait_procs(survivors, timeout=2)
                for p in alive:
                    try: p.kill()
                    except Exception: pass
            return len(survivors)
        except Exception:
            return 0

    # ── Performance toggle ──────────────────────────────────────────────
    def set_participant_scraping(self, enabled: bool):
        """User-facing perf toggle: turns the host/co-host/participant-
        count display scrape in _scan_one_participant on or off.

        Deliberately does NOT touch the Prism status-push retry check in
        that same function — that's the only retry path for a push that
        failed on its first attempt (see _push_prism_status's "retry via
        participant-scan loop" comment), and it costs nothing extra (a
        plain state check, not a DOM scrape), so it keeps running
        regardless of this setting."""
        self.participant_scraping_enabled = enabled
        self.log(f"Live participant scraping {'enabled' if enabled else 'disabled'}.", "info")

    # ── Cleanup / locks ──────────────────────────────────────────────
    def _page_lock_for(self, url: str) -> asyncio.Lock:
        """One lock per session page, shared by everything that clicks
        around a live meeting's DOM (participant scraping, poll launch).
        Without this, the participant-scan loop (every 12s, for every
        LIVE session) and a poll launch triggered on that same session
        can run concurrently on the same Playwright page — e.g. the scan
        opening/closing the Participants panel while a poll launch has
        the 'More' menu open closes that menu out from under it, so
        Playwright's wait for the 'Polls' item times out even though the
        menu was genuinely on screen a moment earlier (confirmed live:
        the menu renders and the button is reachable in isolation, so a
        real DOM/selector break wouldn't explain an intermittent timeout
        the way two tasks clicking the same page at once would)."""
        lock = self._page_locks.get(url)
        if lock is None:
            lock = self._page_locks[url] = asyncio.Lock()
        return lock

    async def cleanup_session(self, url: str):
        m = self.model.get(url)
        if not m: return
        lock_key = m.lock_key()
        self.running_locks.discard(lock_key)
        self._participant_scan_ticks.pop(url, None)
        self._page_locks.pop(url, None)
        if m.is_zoom():
            email = m.host_email
            if len(email) > 3 and email in self.active_accounts:
                self.active_accounts[email].discard(lock_key)
                self.log(f"Released Zoom slot for {email}", "info")

    async def close_pages(self, pages: list):
        for page in pages:
            try:
                if not page.is_closed(): await page.close()
            except Exception: pass
        self.active_accounts.clear()
        self.log(f"Closed {len(pages)} page(s).", "warn")

    def _warn_browser_not_ready(self):
        """Logged once, not on every retry — Auto-Pilot re-attempts a
        pending session roughly every 10s, and repeating this on every
        tick would just bury the one useful line (and the real error
        logged by start(), if that's why we're here) under noise."""
        if self._browser_not_ready_warned:
            return
        self._browser_not_ready_warned = True
        self.log(
            "⚠️ Launch requested but the browser engine isn't ready yet — "
            "check the Event Log above for a startup error.", "warn")

    # ── Launch: Zoom ─────────────────────────────────────────────────
    def launch_zoom(self, url: str):
        if not self.browser_ready:
            self._warn_browser_not_ready()
            return
        m = self.model.get(url)
        if not m or not m.is_zoom(): return
        lock_key = m.lock_key()
        if lock_key in self.running_locks:
            self.log(f"Blocked duplicate Zoom launch: {lock_key}", "warn")
            return
        email = m.host_email
        if len(email) > 3:
            current = len(self.active_accounts.get(email, set()))
            if current >= MAX_SESSIONS_PER_ACCOUNT:
                msg = f"⚠️ Account Full ({current}/{MAX_SESSIONS_PER_ACCOUNT})"
                self.model.set_fields(url, status=msg, action="Retry Later")
                self.log(f"Account full for {email} — cannot launch {m.session_name}", "warn")
                return
        if m.status != "Pending": return
        self.running_locks.add(lock_key)
        if len(email) > 3:
            self.active_accounts.setdefault(email, set()).add(lock_key)
        self.model.set_fields(url, status="🚀 Queued...", action="...")
        self.model.set_fields(url, status="Launching...")
        self.log(f"Zoom launch queued: {m.session_name} [{m.moderator_name}]", "info")
        asyncio.create_task(self._launch_zoom_task(url, m, mode="HOST"))

    def join_zoom_guest(self, url: str):
        m = self.model.get(url)
        if not m: return
        self.model.set_fields(url, status="👤 Joining...", action="...")
        asyncio.create_task(self._launch_zoom_task(url, m, mode="GUEST"))

    async def _launch_zoom_task(self, url: str, m: SessionModel, mode: str = "HOST"):
        delay = random.uniform(2, 6)
        await asyncio.sleep(delay)
        bot_name = m.moderator_name
        email    = m.host_email
        password = m.host_password
        context = None  # tracked outside the try so a failure anywhere
                         # below (even before m.page is assigned) can
                         # still close it — see the except block
        try:
            context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                permissions=["microphone", "camera"],
                ignore_https_errors=True,
                no_viewport=True,
            )
            page = await context.new_page()
            if mode == "HOST":
                m.page = page
            else:
                self.guest_pages.append(page)

            await page.route("zoommtg://**", _block_zoom_protocol)

            if mode == "HOST" and len(email) > 3:
                self.model.set_fields(url, status="🔑 Logging in...", action="...")
                self.log(f"Logging in as {email}...", "info")
                # Zoom's sign-in redirect can land on a different host
                # entirely (app.zoom.us, with tracking query params) —
                # confirmed live: two real accounts submitted email +
                # password with no error, but the OLD check here
                # (`wait_for_url("**/profile")`) timed out anyway because
                # Zoom's post-login landing page no longer matches that
                # pattern, got logged as "Login failed", and a retry then
                # did a fresh page.goto() to a blank sign-in form — which
                # is what actually looked like "bounces back to the email
                # page". Checking whether the sign-in FORM ITSELF is
                # still present is host/redirect-agnostic and directly
                # answers "what page are we actually on" instead of
                # guessing from a URL substring.
                SIGNIN_FORM_SEL = "input[name='email'], #email"
                PASSWORD_FIELD_SEL = "input[name='password'], #password"
                login_ok = True
                try:
                    await page.goto("https://zoom.us/signin", timeout=100000, wait_until="domcontentloaded")
                    # Zoom's sign-in form is rendered by client-side JS
                    # AFTER domcontentloaded fires — checking .count()
                    # instantly can find zero email inputs simply
                    # because the form hasn't mounted yet, not because
                    # we're actually logged in. Confirmed live TWICE now:
                    # first with a zero-wait check, then again with only
                    # a 5s allowance — under the load conditions seen
                    # today, page loads have taken 90-100+ seconds (see
                    # the white-screen timeouts logged earlier), so 5s
                    # was nowhere near enough margin. Matching the same
                    # 100s timeout used everywhere else in this function
                    # costs nothing in the normal case — a genuine
                    # already-logged-in redirect resolves almost
                    # instantly — and only matters for the slow case,
                    # which is exactly the one that was failing.
                    try:
                        await page.wait_for_selector(SIGNIN_FORM_SEL, state="attached", timeout=100000)
                        email_form_present = True
                    except Exception:
                        email_form_present = await page.locator(SIGNIN_FORM_SEL).count() > 0

                    if email_form_present:
                        already_logged_in = False
                    elif "/signin" not in page.url.lower():
                        already_logged_in = True
                    else:
                        # Email form is gone, but we're still somewhere
                        # under zoom.us/signin — confirmed live: Zoom can
                        # redirect through OTHER sign-in sub-steps
                        # (.../signin#/login/bind-passkey — a passkey
                        # enrollment prompt) that don't show the email
                        # field at all. That's not "logged in", and it's
                        # not the plain email form either — the
                        # automation has no logic for it, so surface it
                        # explicitly rather than misreading it as either.
                        raise RuntimeError(f"stuck on an unhandled sign-in step at {page.url}")

                    if already_logged_in:
                        self.log(f"Already logged in: {email} (at {page.url})", "ok")
                    else:
                        await page.click(SIGNIN_FORM_SEL, timeout=100000)
                        await asyncio.sleep(0.5)
                        await page.fill(SIGNIN_FORM_SEL, email)
                        for _ in range(3):
                            try:    await page.click("button:has-text('Next')", timeout=100000)
                            except Exception: pass
                            try:
                                await page.wait_for_selector(
                                    PASSWORD_FIELD_SEL, state="visible", timeout=100000)
                                break
                            except Exception:
                                await asyncio.sleep(2)
                        await page.wait_for_selector(
                            PASSWORD_FIELD_SEL, state="visible", timeout=100000)
                        await page.fill(PASSWORD_FIELD_SEL, password)
                        await page.click("button:has-text('Sign In'), button:has-text('Sign in')", timeout=100000)

                        # Success = the PASSWORD form is GONE *and* we've
                        # actually left the /signin URL space. Waiting on
                        # the email selector here (SIGNIN_FORM_SEL) was a
                        # real bug: the email step is a page ago by this
                        # point — Zoom's flow is email page -> password
                        # page, and the email input is already absent the
                        # moment the password page appears, well before
                        # Sign In is even clicked. That made the wait
                        # resolve instantly, so this check was firing
                        # while Zoom's own Sign In button was still mid-
                        # submission (its loading spinner visibly still
                        # spinning in the captured screenshot), reporting
                        # a false failure on a login that just hadn't
                        # finished processing yet. Waiting on the
                        # password field — what's actually still on
                        # screen right after clicking Sign In — is the
                        # correct signal for "this specific submission is
                        # done," whether that ends in success or a real
                        # failure/passkey step.
                        try:
                            await page.wait_for_selector(PASSWORD_FIELD_SEL, state="detached", timeout=100000)
                            form_gone = True
                        except Exception:
                            # A detached-wait can itself time out if Zoom
                            # merely hides the form instead of removing
                            # it from the DOM — fall back to a direct
                            # presence check before concluding failure.
                            form_gone = await page.locator(PASSWORD_FIELD_SEL).count() == 0
                        if not form_gone:
                            raise RuntimeError(
                                f"still on the password entry page after submitting — stuck at {page.url}")
                        if "/signin" in page.url.lower():
                            raise RuntimeError(
                                f"password form is gone but still stuck somewhere in Zoom's "
                                f"sign-in flow (e.g. a passkey/verification step) at {page.url}")
                        self.log(f"Login success: {email} (landed at {page.url})", "ok")
                except Exception as e:
                    # Diagnostic capture — a generic "Login failed" line
                    # alone doesn't say WHERE it got stuck (still on
                    # sign-in? bounced back after password? a CAPTCHA/
                    # verification step the selectors don't know about?).
                    # Grab the current URL, a screenshot, and any visible
                    # page text so a failure can be diagnosed from the
                    # log folder without anyone having to be watching
                    # live when it happens.
                    current_url = "<unknown>"
                    try:
                        current_url = page.url
                    except Exception:
                        pass
                    snippet = ""
                    try:
                        body_text = await page.inner_text("body")
                        snippet = " ".join(body_text.split())[:300]
                    except Exception:
                        pass
                    shot_path = ""
                    try:
                        from applog import LOG_DIR
                        shot_dir = os.path.join(os.path.dirname(LOG_DIR), "login_failures")
                        os.makedirs(shot_dir, exist_ok=True)
                        ts = _now_hhmmss().replace(":", "-")
                        safe_email = email.replace("@", "_at_").replace(".", "_")
                        shot_path = os.path.join(shot_dir, f"{safe_email}_{ts}.png")
                        await page.screenshot(path=shot_path, full_page=True)
                    except Exception:
                        pass
                    detail = f"Login failed for {email}: {e} | stuck at: {current_url}"
                    if shot_path: detail += f" | screenshot: {shot_path}"
                    if snippet: detail += f" | page text: {snippet}"
                    self.log(detail, "warn")
                    login_ok = False

                if not login_ok:
                    # Previously this fell through to page.goto(target)
                    # regardless — an unauthenticated attempt to open a
                    # HOST meeting URL just redirects straight back to a
                    # sign-in page, which is exactly what looked like
                    # "bounces back to the email page" no matter how
                    # accurate the success/failure detection above is.
                    # A confirmed login failure must stop the launch
                    # here, not continue toward a guaranteed second
                    # bounce. Raising (rather than returning) routes
                    # through the existing outer except block below,
                    # which is the only place that closes `context`,
                    # clears m.page, and calls cleanup_session() — a
                    # bare early return would skip all of that and leak
                    # the BrowserContext, same class of bug CODE_REVIEW
                    # already fixed once for the other failure paths.
                    raise RuntimeError(f"Login to {email} did not succeed — aborting this launch")

            if mode == "HOST":
                self.model.set_fields(url, status="🌍 Starting...", action="...")
            mid = m.zoom_meeting_id() or ""
            target = (f"https://upgrad.zoom.us/wc/{mid}/start"
                      if (mode == "HOST" and email) else f"https://upgrad.zoom.us/wc/{mid}/join")
            await page.goto(target, timeout=90000, wait_until="domcontentloaded")

            try:
                rescue = page.locator("text=Join from your browser")
                if await rescue.count() > 0: await rescue.click(force=True)
            except Exception: pass

            try:
                name_input = page.locator("input#input-for-name, input#input-name").first
                if await name_input.is_visible(timeout=8000):
                    await name_input.fill(bot_name)
                    await page.click("button.preview-join-button")
            except Exception: pass

            if mode == "HOST":
                self.model.set_fields(url, status="⏳ Connecting...", action="...")

        except Exception as e:
            self.log(f"Zoom launch failed: {m.session_name} — {e}", "err")
            # Without this, a context created above but never reaching
            # a clean success path is never closed by anything — it's
            # not tracked in guest_pages/m.page in every failure path,
            # and a retry overwrites m.page with a brand-new page,
            # losing the only reference to this one. Each failed launch
            # would otherwise leak one BrowserContext for the rest of
            # the app's run (only reclaimed when the whole browser
            # closes at shutdown).
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if mode == "HOST":
                m.page = None
            await self.cleanup_session(url)
            if mode == "HOST":
                self.model.set_fields(url, status="❌ Failed", action="▶ Launch")

    # ── Launch: Prism ────────────────────────────────────────────────
    def launch_prism(self, url: str):
        if not self.browser_ready:
            self._warn_browser_not_ready()
            return
        m = self.model.get(url)
        if not m or not m.is_prism(): return
        lock_key = m.lock_key()
        if lock_key in self.running_locks:
            self.log(f"Blocked duplicate Prism launch: {lock_key}", "warn")
            return
        if m.status != "Pending": return
        self.running_locks.add(lock_key)
        self.model.set_fields(url, status="🟣 Launching Prism...", action="...")
        self.model.set_fields(url, status="Launching...")
        self.log(f"Prism launch queued: {m.session_name} [{m.moderator_name}]", "info")
        asyncio.create_task(self._launch_prism_task(url, m))

    async def _launch_prism_task(self, url: str, m: SessionModel):
        delay = random.uniform(1, 4)
        await asyncio.sleep(delay)
        bot_name = m.moderator_name or "Monitor"
        context = None  # see the matching comment in _launch_zoom_task
        try:
            context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                permissions=["microphone", "camera"],
                ignore_https_errors=True,
                no_viewport=True,
            )
            page = await context.new_page()
            m.page = page
            await page.route("zoommtg://**", _block_zoom_protocol)

            cleaned_url = clean_url(url)
            mid = m.zoom_meeting_id() or ""
            launch_url = build_launch_url(mid, cleaned_url, mode="start")

            self.model.set_fields(url, status="🟣 Opening Prism...", action="...")
            await page.goto(launch_url, timeout=90000, wait_until="domcontentloaded")

            try: await page.keyboard.press("Escape")
            except Exception: pass
            await asyncio.sleep(3)

            # Zoom's ZAK/host-start flow has, as of 2026-09-25, been observed
            # to skip the 'Join from browser' splash and device-preview
            # screen entirely and drop straight into the live meeting
            # (confirmed via live DOM inspection against a real session —
            # End/Leave button present within ~5s of page.goto). Zoom
            # changes this often enough that we can't assume it's
            # permanent, so this is a fast-path CHECK layered in front of
            # the original wait-for-splash flow below, not a replacement
            # for it — if a launch doesn't land directly in the meeting
            # (e.g. Zoom reverts, or a different session type still shows
            # the splash), the exact old flow still runs untouched.
            already_in_meeting = False
            for _attempt in range(8):
                try:
                    if await _check_live_buttons(page):
                        already_in_meeting = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)

            if already_in_meeting:
                self.log(f"Prism: landed directly in the live meeting (no join splash) — {m.session_name}", "info")
            else:
                self.log(f"Prism: waiting for 'Join from browser' — {m.session_name}", "info")
                jfb_sel = (
                    "button:has-text('Join from browser'), "
                    "a:has-text('Join from browser'), "
                    "button:has-text('Join from Browser'), "
                    "span.zoom-button__label:has-text('Join from browser'), "
                    "a:has-text('join from your browser')"
                )
                jfb_clicked = False
                for attempt in range(5):
                    try: await page.keyboard.press("Escape")
                    except Exception: pass
                    try:
                        loc = page.locator(jfb_sel)
                        if await loc.count() > 0 and await loc.first.is_visible(timeout=2000):
                            await loc.first.click(force=True)
                            jfb_clicked = True
                            self.log(f"Prism: 'Join from browser' clicked — {m.session_name}", "ok")
                            await asyncio.sleep(1)
                            try: await page.keyboard.press("Escape")
                            except Exception: pass
                            await asyncio.sleep(1)
                            break
                    except Exception: pass
                    await asyncio.sleep(2)
                if not jfb_clicked:
                    self.log(f"Prism: 'Join from browser' not found — {m.session_name}", "warn")

                self.model.set_fields(url, status="🟣 Detecting Layout...", action="...")
                await asyncio.sleep(4)

                target_context = page
                mic_found = False
                for attempt in range(6):
                    try:
                        if await page.locator("#preview-audio-control-button").is_visible(timeout=500):
                            target_context = page
                            mic_found = True
                            break
                    except Exception: pass
                    for frame in page.frames:
                        try:
                            if await frame.locator("#preview-audio-control-button").is_visible(timeout=500):
                                target_context = frame
                                mic_found = True
                                break
                        except Exception: pass
                    if mic_found: break
                    await asyncio.sleep(2)

                try:
                    name_loc = target_context.locator(
                        "input#input-for-name, input#input-name, "
                        "input[placeholder*='name'], input[placeholder*='Name']"
                    ).first
                    if await name_loc.is_visible(timeout=5000):
                        await name_loc.fill(bot_name)
                except Exception: pass

                self.model.set_fields(url, status="🟣 Setting Devices...", action="...")
                mic_loc = target_context.locator("#preview-audio-control-button").first
                vid_loc = target_context.locator("#preview-video-control-button").first
                try:
                    if await mic_loc.get_attribute("aria-label", timeout=5000) == "Mute":
                        await mic_loc.click(force=True)
                except Exception: pass
                try:
                    if await vid_loc.get_attribute("aria-label", timeout=5000) == "Stop Video":
                        await vid_loc.click(force=True)
                except Exception: pass

                self.model.set_fields(url, status="🟣 Joining...", action="...")
                join_clicked = False
                for attempt in range(6):
                    try: await target_context.keyboard.press("Escape")
                    except Exception:
                        try: await page.keyboard.press("Escape")
                        except Exception: pass
                    for btn_sel in ["button.preview-join-button", "button:has-text('Join')",
                                    "button:has-text('Join Meeting')", "button[aria-label='Join']"]:
                        try:
                            b = target_context.locator(btn_sel)
                            if await b.count() > 0 and await b.first.is_visible(timeout=2000):
                                await b.first.click(force=True)
                                join_clicked = True
                                break
                        except Exception: pass
                    if join_clicked: break
                    await asyncio.sleep(2)

                if join_clicked:
                    await asyncio.sleep(1)
                    try: await page.keyboard.press("Escape")
                    except Exception: pass
                else:
                    self.log(f"Prism: Join button not found — {m.session_name}", "warn")

            self.model.set_fields(url, status="⏳ Prism Connecting...", action="...")
            self.log(f"Prism: joining session — {m.session_name} [{bot_name}]", "info")

        except Exception as e:
            self.log(f"Prism launch failed: {m.session_name} — {e}", "err")
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            m.page = None
            await self.cleanup_session(url)
            self.model.set_fields(url, status="❌ Prism Failed", action="▶ Launch")

    # ── End meeting ──────────────────────────────────────────────────
    def end_session(self, url: str):
        m = self.model.get(url)
        if not m: return
        self.model.set_fields(url, status="🛑 Ending...", action="...")
        self.log(f"Ending session: {m.session_name}", "warn")
        asyncio.create_task(self._end_meeting_task(url))

    async def _end_meeting_task(self, url: str):
        m = self.model.get(url)
        if not m or not m.page: return
        try:
            page = m.page
            try:    await page.click("button:has-text('End')", timeout=5000)
            except Exception: pass
            await asyncio.sleep(0.5)
            await page.click("button:has-text('End Meeting for All')", timeout=5000)
            await page.close()
            await self.cleanup_session(url)
            self.model.set_fields(url, status="⚫ Ended", action="Closed")
            self.log(f"Session ended: {m.session_name}", "warn")
        except Exception:
            try:
                if m.page: await m.page.close()
            except Exception: pass
            await self.cleanup_session(url)
            self.model.set_fields(url, status="❌ Force Closed", action="Closed")

    # ── Retry ────────────────────────────────────────────────────────
    def force_retry(self, url: str):
        m = self.model.get(url)
        if not m: return
        self.log(f"Retry requested: {m.session_name}", "warn")
        m.status = "Pending"
        m.prism_status_pushed = False  # retry resets ALL state, not just launch locks
        self.running_locks.discard(m.lock_key())
        self.model.set_fields(url, status="Pending", action="▶ Launch", retry="")
        if m.is_prism(): self.launch_prism(url)
        else:            self.launch_zoom(url)

    # ── Clear all ────────────────────────────────────────────────────
    def clear_all(self):
        pages = [m.page for m in self.model.all_models()
                 if m.page and not m.page.is_closed()] + \
                [p for p in self.guest_pages if not p.is_closed()]
        self.guest_pages.clear()
        asyncio.create_task(self.close_pages(pages))
        self.model.clear_all()
        self.running_locks.clear()
        self.active_accounts.clear()
        self.log("All sessions cleared.", "warn")

    # ── Participant refresh (toolbar button) ────────────────────────
    def refresh_all_participants(self):
        if not self.browser_ready:
            self.log("Engine not ready — cannot refresh participants.", "warn")
            return
        live = [m for m in self.model.all_models() if "LIVE" in m.status and m.page]
        if not live:
            self.log("No live sessions to refresh.", "warn")
            return
        self.log(f"Refreshing participants for {len(live)} live session(s)...", "info")
        for m in live:
            asyncio.create_task(self._refresh_one_session(m.join_url))

    async def _refresh_one_session(self, url: str):
        m = self.model.get(url)
        if not m: return
        page = m.page
        if not page or page.is_closed(): return
        try:
            async with self._page_lock_for(url):
                host_name, cohosts, count = await get_zoom_participant_info(page)
            self.model.set_fields(url, host_name=host_name, cohost_names=cohosts,
                                   participant_count=count)
        except Exception as e:
            self.log(f"Refresh failed [{m.session_name}]: {e}", "err")

    # ── Poll launch ──────────────────────────────────────────────────
    def _poll_preflight(self, url: str) -> tuple[bool, str]:
        """Synchronous guard shared by both the single-click (Poll
        Action cell) and bulk (toolbar 'Launch Poll (Selected)') paths.
        Runs entirely before any await, so two overlapping calls for the
        same session — a double-click racing a bulk launch, or two bulk
        launches overlapping — can never both pass the poll_launching
        check and launch twice. Reserves the lock on success."""
        m = self.model.get(url)
        if not m:
            return False, "session not found"
        if "LIVE" not in m.status:
            return False, "session is not LIVE"
        if not m.page or m.page.is_closed():
            return False, "session page not available"
        if m.poll_launching:
            return False, "poll launch already in progress"
        m.poll_launching = True
        return True, ""

    def poll_name_for(self, url: str) -> str:
        """The poll this row should launch: its dropdown selection, or the
        default ('End Session Poll') when nothing has been selected."""
        m = self.model.get(url)
        return (m.selected_poll if m else "") or DEFAULT_POLL_NAME

    def launch_poll(self, url: str, poll_name: str = None):
        """Single-session poll launch — the Poll Action cell click."""
        poll_name = poll_name or DEFAULT_POLL_NAME
        m = self.model.get(url)
        ok, reason = self._poll_preflight(url)
        if not ok:
            if m:
                self.log(f"Poll launch skipped for '{m.session_name}' — {reason}.", "warn")
            return
        self.model.set_fields(url, poll_status="Launching...", poll_action="...")
        self.log(f"Launching poll '{poll_name}' for '{m.session_name}'...", "info")
        asyncio.create_task(self._launch_poll_task(url, poll_name, ""))

    async def _launch_poll_task(self, url: str, poll_name: str, prefix: str) -> bool:
        """Participants → More → Polls → <poll_name> → Launch, reusing
        the session's existing Playwright page — no new browser tabs or
        contexts. Retries once on failure before giving up (
        POLL_LAUNCH_MAX_ATTEMPTS). Returns True/False so
        launch_poll_for_many can report an accurate success count."""
        m = self.model.get(url)
        if not m:
            return False
        last_err = None
        for attempt in range(1, POLL_LAUNCH_MAX_ATTEMPTS + 1):
            try:
                async with self._page_lock_for(url):
                    await _do_launch_poll_once(m.page, poll_name, is_prism=m.is_prism())
                m.poll_launching = False
                self.model.set_fields(url, poll_status=f"{prefix}Poll Live", poll_action="Launch Poll")
                self.log(f"{prefix}Poll '{poll_name}' launched successfully for '{m.session_name}'.", "ok")
                return True
            except Exception as e:
                last_err = e
                self.log(f"{prefix}[POLL] attempt {attempt} failed for '{m.session_name}': {e}", "warn")
                if attempt < POLL_LAUNCH_MAX_ATTEMPTS:
                    await asyncio.sleep(2)

        m.poll_launching = False
        self.model.set_fields(url, poll_status=f"{prefix}Failed", poll_action="Launch Poll")
        self.log(f"{prefix}Poll launch failed for '{m.session_name}': {last_err}", "err")
        return False

    async def launch_poll_for_many(self, urls: list):
        """Bulk poll launch — toolbar 'Launch Poll (Selected)'. Reserves
        every eligible session's poll_launching lock synchronously up
        front (see _poll_preflight), then runs them concurrently — each
        session has its own Playwright page/tab, so these don't
        serialize against each other, same reasoning as the participant
        scan loop."""
        if not urls:
            return
        eligible = []
        for url in urls:
            ok, reason = self._poll_preflight(url)
            if ok:
                eligible.append(url)
            else:
                m = self.model.get(url)
                if m:
                    self.log(f"Launch Poll (Selected): skipping '{m.session_name}' — {reason}.", "warn")
        if not eligible:
            self.log("Launch Poll (Selected): no eligible LIVE sessions to launch a poll for.", "warn")
            return

        total = len(eligible)
        names = {url: self.poll_name_for(url) for url in eligible}
        distinct = set(names.values())
        label = f"'{next(iter(distinct))}'" if len(distinct) == 1 else "the selected poll(s)"
        for i, url in enumerate(eligible, start=1):
            self.model.set_fields(url, poll_status=f"[{i}/{total}] Launching...", poll_action="...")
        self.log(f"Launching {label} for {total} selected LIVE session(s)...", "info")

        results = await asyncio.gather(
            *[self._launch_poll_task(url, names[url], f"[{i}/{total}] ")
              for i, url in enumerate(eligible, start=1)],
            return_exceptions=True,
        )
        succeeded = sum(1 for r in results if r is True)
        if succeeded == total:
            self.log(f"{label} launched successfully for all {total} selected session(s).", "ok")
        else:
            self.log(f"{label} launched for {succeeded}/{total} selected session(s) — "
                      f"check individual rows for failures.", "warn")

    # ── Prism status push ────────────────────────────────────────────
    async def _push_prism_status(self, url: str, m: SessionModel):
        """See prism_status_push.py / PRISM_STATUS_PUSH_CHANGELOG.md for the
        full config-error-vs-transient-error rationale — unchanged here."""
        VALID_TYPES = ("session", "workshop-session", "session-group-session")

        def fail_permanent(reason: str):
            m.prism_status_pushed = True
            self.model.set_fields(url, prism_sync=f"❌ {reason}")
            self.log(f"Prism status push skipped ({reason}): {m.session_name}", "err")

        if m.prism_type not in VALID_TYPES:
            fail_permanent(f"invalid Prism Type {m.prism_type!r}"); return
        if not m.prism_session_id:
            fail_permanent("missing Session ID"); return
        if m.prism_type == "session-group-session" and not m.prism_session_group_id:
            fail_permanent("missing Session Group ID"); return

        m.prism_status_pushed = True
        try:
            ids = m.prism_push_ids()
            await push_prism_live_status_async(m.prism_type, ids)
            self.model.set_fields(url, prism_sync="✅ Synced")
            self.log(f"Prism status pushed to 'started': {m.session_name} [{m.prism_type}]", "ok")
        except Exception as e:
            m.prism_status_pushed = False  # transient — retry via participant-scan loop
            self.model.set_fields(url, prism_sync=f"❌ {e}")
            self.log(f"Prism status push failed (will retry in ~20s): {m.session_name} — {e}", "err")

    # ── Heartbeat loops ──────────────────────────────────────────────
    async def _fast_live_loop(self):
        """Every 3s. Only End/Leave button checks — cheapest possible DOM
        query — to flip LIVE as fast as possible."""
        while True:
            await asyncio.sleep(3)
            if not self.browser_ready: continue
            for m in self.model.all_models():
                page = m.page
                if not page or page.is_closed(): continue
                if m.status in LAUNCH_STATUSES: continue
                if "LIVE" in m.status: continue
                try:
                    if await _check_live_buttons(page):
                        poll_action = "..." if m.poll_launching else "Launch Poll"
                        self.model.set_fields(m.join_url, status="🔴 LIVE", action="⏹ End Session",
                                               launched_at=_now_hhmmss(), poll_action=poll_action)
                        if m.is_prism() and not m.prism_status_pushed:
                            asyncio.create_task(self._push_prism_status(m.join_url, m))
                        asyncio.create_task(self._scan_available_polls(m.join_url))
                except Exception:
                    pass

    async def _scan_available_polls(self, url: str):
        """One-shot, fire-and-forget at the LIVE transition: discover the
        Poll-type entries in this meeting so the row's dropdown has choices
        before anyone opens it. Best-effort — never raises into the caller.
        A failed scan leaves the dropdown as it was (Default only on a first
        scan); manual and Auto-Pilot launches keep working either way.

        Waits briefly first (the End/Leave button that flips LIVE appears
        before the whole toolbar is necessarily interactive) and tries a
        second time if the first attempt raises. An EMPTY result is not
        retried — a meeting with no polls is a legitimate answer."""
        m = self.model.get(url)
        if not m: return
        names = None
        for attempt in (1, 2):
            await asyncio.sleep(3 if attempt == 1 else 15)
            m = self.model.get(url)
            if not m or not m.page or m.page.is_closed():
                return
            try:
                async with self._page_lock_for(url):
                    await _open_polls_panel(m.page)
                    polls = await _list_available_polls(m.page)
                # The "Default (End Session Poll)" dropdown entry already
                # covers the default poll, so don't list it a second time.
                names = [title for title, _ in polls
                         if title.casefold() != DEFAULT_POLL_NAME.casefold()]
                break
            except Exception as e:
                if attempt == 2:
                    self.log(f"Poll scan failed for '{m.session_name}' — dropdown unchanged: {e}", "warn")
        if names is None:
            return
        # A selection that's no longer in the meeting's poll list must not
        # linger invisibly (Auto-Pilot would still try to launch it) — drop
        # back to Default and say so.
        if m.selected_poll and m.selected_poll not in names:
            self.log(f"Selected poll '{m.selected_poll}' is no longer in '{m.session_name}' — "
                     f"reverting to {DEFAULT_POLL_NAME}.", "warn")
            self.model.set_fields(url, selected_poll="")
        self.model.set_fields(url, available_polls=names)
        self.log(f"Found {len(names)} poll(s) for '{m.session_name}'.", "info")

    async def _status_text_loop(self):
        """Every 6s. Body-text poll for Waiting/Ended/stuck states, kept
        off the fast loop so a slow inner_text() never delays LIVE."""
        while True:
            await asyncio.sleep(6)
            if not self.browser_ready: continue
            for m in self.model.all_models():
                page = m.page
                if not page or page.is_closed(): continue
                if m.status in LAUNCH_STATUSES: continue
                if "LIVE" in m.status: continue
                try:
                    text = await page.inner_text("body")
                    t_low = text.lower()
                    status = m.status
                    if "waiting for the host" in t_low: status = "⏳ Waiting for Host"
                    elif "host has another meeting" in t_low: status = "⚠️ Host Collision"
                    elif "meeting has ended" in t_low: status = "⚫ Ended"
                    elif "this meeting has been ended" in t_low: status = "⚫ Ended"
                    elif "your meeting is protected" in t_low: status = "⏳ Waiting (Passcode)"
                    elif "join from your browser" in t_low:
                        status = "⚠️ Stuck (Popup)"
                        try:
                            await page.keyboard.press("Escape")
                            await page.click("text=Join from your browser", force=True)
                        except Exception: pass
                    elif m.is_prism():
                        if   "session has ended" in t_low: status = "⚫ Prism Ended"
                        elif "invalid meeting id" in t_low: status = "⚠️ Invalid Meeting"
                        elif "meeting is not started" in t_low: status = "⏳ Not Started Yet"
                        elif "waiting" in t_low: status = "⏳ Waiting"

                    if status != m.status:
                        if "Ended" in status:
                            self.running_locks.discard(m.lock_key())
                            self.log(f"Session ended: {m.session_name} [{m.session_type}]", "warn")
                            self.model.set_fields(m.join_url, status=status, action="...",
                                                   poll_action="", poll_status=m.poll_status or "")
                        else:
                            self.model.set_fields(m.join_url, status=status, action="...")
                except Exception:
                    pass

    async def _participant_scan_loop(self):
        """Every 12s, scan ALL currently-LIVE sessions concurrently —
        not a round-robin subset. The earlier 3-sessions-per-tick/20s
        version meant any session outside a given tick's batch could go
        a minute or more between refreshes once there were more than a
        few LIVE sessions at once; each session has its own Playwright
        page/tab, so scanning everyone every tick concurrently is both
        faster to refresh per-session AND simpler than batching. Also
        the retry cadence for any Prism push that failed or never fired.
        """
        while True:
            await asyncio.sleep(12)
            if not self.browser_ready: continue

            live = [m for m in self.model.all_models()
                    if "LIVE" in m.status and m.page and not m.page.is_closed()]
            if not live: continue

            await asyncio.gather(
                *[self._scan_one_participant(m) for m in live], return_exceptions=True
            )

    async def _scan_one_participant(self, m: SessionModel):
        page = m.page
        if not page or page.is_closed(): return
        try:
            # The Prism status-push retry must run regardless of the
            # participant-scraping toggle below — it's the only retry
            # path for a push that failed on its first attempt, and it's
            # a plain state check + a separate network call, not a DOM
            # scrape, so it costs nothing to keep running.
            if m.is_prism() and not m.prism_status_pushed:
                asyncio.create_task(self._push_prism_status(m.join_url, m))

            if not self.participant_scraping_enabled:
                return  # user opted out of the host/co-host/participant-count display refresh

            # Zoom rows never use host/co-host names (see below — the
            # values were always discarded), so they never need the
            # expensive per-row scrape. Prism rows display host/co-host,
            # but only need the full row-by-row scrape until a host is
            # first found, then periodically after that (see
            # PARTICIPANT_FULL_SCAN_EVERY) — not on every single tick.
            if m.is_zoom():
                need_full_scan = False
            else:
                tick = self._participant_scan_ticks.get(m.join_url, 0) + 1
                self._participant_scan_ticks[m.join_url] = tick
                need_full_scan = (not m.host_name) or (tick % PARTICIPANT_FULL_SCAN_EVERY == 0)

            async with self._page_lock_for(m.join_url):
                if m.is_prism():
                    host_name, cohosts, count = await get_zoom_participant_info(page, scan_names=need_full_scan)
                    kw = {}
                    if count: kw["participant_count"] = count
                    if need_full_scan and (host_name or cohosts):
                        kw["host_name"] = host_name
                        kw["cohost_names"] = cohosts
                    if kw:
                        self.model.set_fields(m.join_url, **kw)
                elif m.is_zoom():
                    # host_name/cohosts intentionally discarded — the Zoom
                    # grid never displays them, only participant_count.
                    _, _, count = await get_zoom_participant_info(page, scan_names=False)
                    if count and count != m.participant_count:
                        self.model.set_fields(m.join_url, participant_count=count)
        except Exception as e:
            self.log(f"Participant scan error [{m.session_name}]: {e}", "err")


def _now_hhmmss():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


async def _check_live_buttons(page) -> bool:
    async def _frame_has_live(ctx):
        try:
            if await ctx.locator("button:has-text('End')").count() > 0: return True
            if await ctx.locator("button:has-text('Leave')").count() > 0: return True
        except Exception:
            pass
        return False
    contexts = [page] + list(page.frames)
    results = await asyncio.gather(*[_frame_has_live(c) for c in contexts], return_exceptions=True)
    return any(r is True for r in results)


async def _find_meeting_context(page):
    try:
        if await page.locator(_PARTICIPANTS_BTN_SEL).count() > 0:
            return page
    except Exception:
        pass
    for frame in page.frames:
        try:
            if await frame.locator(_PARTICIPANTS_BTN_SEL).count() > 0:
                return frame
        except Exception:
            continue
    return page


async def _ensure_participants_panel_open(ctx) -> bool:
    try:
        panel_sel = "text=/Participants\\s*\\(\\d+\\)/"
        if await ctx.locator(panel_sel).count() > 0:
            if await ctx.locator(panel_sel).first.is_visible(timeout=1500):
                return True
        btn = ctx.locator(_PARTICIPANTS_BTN_SEL)
        if await btn.count() > 0 and await btn.first.is_visible(timeout=2000):
            await btn.first.click(force=True)
            await asyncio.sleep(1)
            return True
    except Exception:
        pass
    return False


async def get_zoom_participant_info(page, scan_names: bool = True):
    """scan_names=False skips the row-by-row host/co-host scrape below
    (O(participant count) Playwright round-trips) and only refreshes
    the cheap header count (a single inner_text() call) — see
    PARTICIPANT_FULL_SCAN_EVERY for why the caller throttles this."""
    host_name, cohost_names, participant_count = "", [], 0
    if page is None or page.is_closed():
        return (host_name, cohost_names, participant_count)
    try:
        ctx = await _find_meeting_context(page)
        if not await _ensure_participants_panel_open(ctx):
            return (host_name, cohost_names, participant_count)
        try:
            header_text = await ctx.locator("text=/Participants\\s*\\(\\d+\\)/").first.inner_text(timeout=2000)
            m = re.search(r"\((\d+)\)", header_text)
            if m: participant_count = int(m.group(1))
        except Exception: pass
        if scan_names:
            try:
                rows = ctx.locator("[class*='participants-item'], [class*='participant-item'], [role='listitem']")
                for i in range(await rows.count()):
                    try:
                        text = (await rows.nth(i).inner_text(timeout=1000)).strip().replace("\n", " ")
                    except Exception:
                        continue
                    if not text: continue
                    hm = _HOST_RE.match(text)
                    if hm:
                        name = hm.group(1).strip()
                        if name: host_name = name
                        continue
                    cm = _COHOST_RE.match(text)
                    if cm:
                        name = cm.group(1).strip()
                        if name and name not in cohost_names:
                            cohost_names.append(name)
            except Exception: pass
    except Exception: pass
    return (host_name, cohost_names, participant_count)


# ── Poll automation mechanics ───────────────────────────────────────────
# Ported near-verbatim from a colleague's prism_auto_launcher.py — see
# the selector docstring near _MORE_BTN_SEL for why these selectors are
# deliberately broad. All functions below reuse the *existing* Playwright
# page for a session; no new browser tabs or contexts are ever opened for
# polls, and nothing here touches session launch / participant
# monitoring / end-session logic.

async def _open_polls_panel(page):
    """Drive Participants → More → Polls to reveal the Polls/Quizzes
    panel. Idempotent — if the panel is already open, returns
    immediately without re-clicking anything.

    Raises RuntimeError with a *specific* reason for whichever sub-step
    failed (More button missing, Polls item missing, panel never
    opened, etc.) so failures are actionable in the Event Log instead of
    one generic message."""
    if await page.locator(_POLLS_PANEL_TITLE_SEL).count() > 0:
        if await page.locator(_POLLS_PANEL_TITLE_SEL).first.is_visible(timeout=1000):
            return

    # Step 1: Participants (also ensures the meeting toolbar / side
    # panel are in a known state).
    await _ensure_participants_panel_open(page)

    polls_item = page.locator(_POLLS_MENU_ITEM_SEL)

    # If the "More" dropdown is already open (e.g. left over from a
    # previous failed attempt), the Polls row will already be visible.
    # More is a *toggle* button — clicking it again in that case would
    # close the menu instead of opening it, breaking automatic retry.
    # So: only click More if the menu isn't already showing Polls.
    menu_already_open = False
    try:
        if await polls_item.count() > 0 and await polls_item.first.is_visible(timeout=800):
            menu_already_open = True
    except Exception:
        pass

    if not menu_already_open:
        # Step 2: More (three-dot menu)
        more_btn = page.locator(_MORE_BTN_SEL)
        if await more_btn.count() == 0 or not await more_btn.first.is_visible(timeout=2000):
            raise RuntimeError("'More' button not found or not visible")
        await more_btn.first.click(force=True)
        await asyncio.sleep(1)

        try:
            await polls_item.first.wait_for(state="visible", timeout=3000)
        except Exception:
            raise RuntimeError("'Polls' item never appeared in the More menu after clicking More")

    # Step 3: Polls
    if await polls_item.count() == 0:
        raise RuntimeError("'Polls' menu item not found")
    await polls_item.first.click(force=True)
    await asyncio.sleep(1)

    try:
        await page.locator(_POLLS_PANEL_TITLE_SEL).first.wait_for(state="visible", timeout=5000)
    except Exception:
        raise RuntimeError("Polls/Quizzes panel did not open after clicking 'Polls'")


async def _is_quiz_entry(title_loc) -> bool:
    """Best-effort check: does this Polls/Quizzes list entry carry a
    'Quiz' type label near its title? Zoom lists Polls and Quizzes
    together in the same panel, and a Quiz can carry the *exact same
    title* as a Poll (confirmed live: 'END SESSION POLL' the Poll vs.
    'End Session Poll' the Quiz, in the same meeting) — Auto-Pilot must
    only ever launch a Poll, never a Quiz that happens to share its
    name.

    Verified live against the actual DOM: the type label is NOT its
    own isolated element — it's a bare text node sharing a container
    with a divider and the question count, e.g.
        <div class="poll-list-item__detail">Poll<div class="common-ui-component__vertical-divider"></div>5 questions</div>
    So there is no element anywhere whose own text is exactly "Poll"/
    "Quiz" for an exact-match check to find (confirmed: that approach
    matched nothing at all, silently). This checks the specific
    container's text with a prefix match instead. Falls back to the
    generic ancestor-scoped 'Poll'/'Quiz' prefix on nearby text if
    that specific class isn't present (Zoom markup can change), and
    defaults to NOT excluding the entry if neither is found — the
    exact-title + row-scoped-button checks elsewhere still guard
    against a wrong click even if this can't classify the type."""
    try:
        row4 = title_loc.locator("xpath=ancestor::*[4]")
        detail = row4.locator(".poll-list-item__detail")
        if await detail.count() > 0:
            detail_text = (await detail.first.inner_text()).strip()
            if re.match(r"^\s*Quiz\b", detail_text, re.IGNORECASE):
                return True
            if re.match(r"^\s*Poll\b", detail_text, re.IGNORECASE):
                return False
    except Exception:
        pass

    for levels in range(2, 6):
        try:
            row = title_loc.locator(f"xpath=ancestor::*[{levels}]")
            if await row.get_by_text(re.compile(r"^\s*Quiz\b", re.IGNORECASE)).count() > 0:
                return True
            if await row.get_by_text(re.compile(r"^\s*Poll\b", re.IGNORECASE)).count() > 0:
                return False
        except Exception:
            continue
    return False


async def _get_question_count(title_loc) -> int | None:
    """Reads the question count off the same '.poll-list-item__detail'
    container _is_quiz_entry uses (e.g. 'Poll<divider>5 questions' ->
    5). Returns None if it can't be determined — callers must treat
    that as 'unknown', never as 0."""
    try:
        row4 = title_loc.locator("xpath=ancestor::*[4]")
        detail = row4.locator(".poll-list-item__detail")
        if await detail.count() > 0:
            detail_text = (await detail.first.inner_text()).strip()
            m = re.search(r"(\d+)\s*questions?", detail_text, re.IGNORECASE)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


async def _list_available_polls(page) -> list[tuple[str, int | None]]:
    """Every Poll-type (never Quiz-type) entry in the already-open
    Polls/Quizzes panel, as [(title, question_count_or_None), ...] in panel
    order, de-duplicated case-insensitively (same matching rule
    _find_poll_row launches by). The caller opens the panel first.

    Confirmed live 2026-10-06 against a real Zoom web-client panel: each
    entry's title is <div class="poll-list-item__topic-name"> and sits
    exactly 4 levels below its <div class="poll-list-item"> row — the same
    "ancestor::*[4]" depth _is_quiz_entry/_get_question_count already rely
    on, so those two are reused untouched rather than re-implemented here.
    Best-effort: any failure returns what was found so far, never raises."""
    found: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    try:
        titles = page.locator(".poll-list-item__topic-name")
        n = await titles.count()
    except Exception:
        return found
    for i in range(n):
        t = titles.nth(i)
        try:
            title = (await t.inner_text(timeout=1500)).strip()
            if not title or await _is_quiz_entry(t):
                continue
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((title, await _get_question_count(t)))
        except Exception:
            continue
    return found


async def _find_poll_row(page, poll_name: str, is_prism: bool = False):
    """Return a locator for the poll's title text — an EXACT (case-
    insensitive, whitespace-trimmed) match, not a substring. The old
    substring match (`exact=False`, `.first`) would match any title
    merely *containing* poll_name, unscoped to the Polls/Quizzes panel,
    and silently take whichever came first in DOM order.

    Also filters out Quiz-type entries (see _is_quiz_entry) — 'End
    Session Poll' must never resolve to a same-named Quiz.

    If more than one Poll-type entry still matches after that, tries
    one further, explicit tie-break: Prism sessions' real poll always
    carries '6 questions', Zoom sessions' real poll always carries '5
    questions' (confirmed against real duplicate-poll setups) — so
    is_prism picks the candidate with that question count instead of
    refusing outright. Only applies when the count resolves to exactly
    ONE candidate; if zero or more than one entry carries the expected
    count, this still raises RuntimeError rather than guess further —
    ambiguity should surface as an actionable failure in the Event Log,
    not a silent guess (a silent guess picking the wrong entry is
    exactly what caused a past mis-launch)."""
    pattern = re.compile(rf"^\s*{re.escape(poll_name)}\s*$", re.IGNORECASE)
    try:
        candidates = page.get_by_text(pattern)
        n = await candidates.count()
    except Exception:
        return None

    polls = []
    for i in range(n):
        cand = candidates.nth(i)
        try:
            if await _is_quiz_entry(cand):
                continue
        except Exception:
            pass
        polls.append(cand)

    if not polls:
        return None
    if len(polls) > 1:
        target_count = 6 if is_prism else 5
        by_count = []
        for cand in polls:
            count = await _get_question_count(cand)
            if count == target_count:
                by_count.append(cand)
        if len(by_count) == 1:
            return by_count[0]
        raise RuntimeError(
            f"Poll name '{poll_name}' matches {len(polls)} Poll-type entries in this "
            f"meeting — ambiguous, refusing to guess which one to launch "
            f"(expected exactly one with {target_count} questions for "
            f"{'a Prism' if is_prism else 'a Zoom'} session, found {len(by_count)})."
        )
    return polls[0]


async def _click_launch_on_row(page, title_loc) -> bool:
    """Hover the poll's title (which cascades :hover to its row) then
    click THAT row's own Launch button — and only that row's button.

    Walks ancestor levels looking for the smallest ancestor that
    contains EXACTLY one Launch button (requires count() == 1, not
    just count() > 0). If a level is reached where more than one
    Launch button is already in scope before any level produced
    exactly one, the row boundary can't be reliably isolated from its
    neighbors — raises rather than guessing.

    This replaces the old two-part logic (loosest ancestor level with
    ANY match, then a page-wide "any visible Launch button" fallback)
    that caused a real incident: the scoped search escaped past its
    own row into a container spanning multiple rows, and the fallback
    then clicked an unrelated poll's Launch button ('Academic writing
    session' launched instead of 'End Session Poll')."""
    await title_loc.scroll_into_view_if_needed(timeout=2000)
    await title_loc.hover(force=True)
    await asyncio.sleep(0.5)

    for levels in range(2, 9):
        try:
            row = title_loc.locator(f"xpath=ancestor::*[{levels}]")
            btn = row.locator("button:has-text('Launch')")
            count = await btn.count()
        except Exception:
            continue

        if count == 1:
            if await btn.first.is_visible(timeout=700):
                await btn.first.click(force=True)
                return True
            continue

        if count > 1:
            raise RuntimeError(
                f"Could not isolate a single Launch button for this poll — "
                f"{count} Launch buttons are already in scope at ancestor level "
                f"{levels}; row boundary unclear, refusing to guess."
            )

    raise RuntimeError("Launch button not found within this poll's own row")


async def _do_launch_poll_once(page, poll_name: str, is_prism: bool = False):
    """Single attempt: Participants → More → Polls → <poll_name> →
    Launch. Raises on failure so the caller (Engine._launch_poll_task)
    can retry / mark as Failed."""
    await _open_polls_panel(page)   # raises a specific RuntimeError on failure

    row = await _find_poll_row(page, poll_name, is_prism=is_prism)
    if row is None:
        raise RuntimeError(f"Poll '{poll_name}' was not found in the list")

    await _click_launch_on_row(page, row)  # raises on failure/ambiguity

    # Best-effort confirmation wait. Zoom doesn't expose a single
    # reliable DOM marker for "poll is now live" across versions, so we
    # give the UI a moment to settle rather than asserting on a specific
    # element.
    await asyncio.sleep(2)
