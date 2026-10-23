r"""DEMO of option 1 (one Zoom sign-in per account, shared by that account's other sessions) against a SIMULATED Zoom.

Nothing here talks to the real Zoom: every request to zoom.us is answered by a fake that
  * shows an email page -> password page -> "Sign In" (same selectors the engine uses),
  * remembers a signed-in cookie, and redirects /signin to /profile when it is present,
  * SIMULATES the reCAPTCHA problem: a sign-in submitted while another sign-in was submitted
    in the last SIMULATED_WINDOW seconds is refused with "Could not connect to the reCAPTCHA service".
The rule is a crude model of 'several sign-ins at once'; the number of sign-ins each version needs is the real result.

MOD=old turns the sharing off (engine.SHARE_ACCOUNT_SIGNIN = False: every session signs in by itself), MOD=new leaves it on.
4 sessions on 2 accounts (2 each). Browser windows open on screen. From the project folder (PowerShell):
    $env:MOD="old"; py tools\demo_shared_login.py
    $env:MOD="new"; py tools\demo_shared_login.py"""
import os, sys, asyncio, time, json, importlib.util
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["QT_API"] = "pyside6"
SP = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(SP)
sys.path.insert(0, PROJ); os.chdir(PROJ)
from PySide6.QtWidgets import QApplication
app = QApplication([])
import qasync, qasync_patch; qasync_patch.patch()
from models import SessionModel, SessionTableModel

MOD = os.environ.get("MOD", "new")
import engine as E
E.SHARE_ACCOUNT_SIGNIN = (MOD != "old")      # "old" = every session signs in by itself, as before

SIMULATED_WINDOW = 12.0
ACCOUNTS = {"zoom128@upgrad.com": "pw128", "zoom129@upgrad.com": "pw129"}

SIGNIN_HTML = """<html><body style="font-family:sans-serif"><h2>Fake Zoom sign in (demo)</h2>
<input name="email" placeholder="email"><button onclick="document.getElementById('pw').style.display='block'">Next</button>
<div id="pw" style="display:none"><input name="password" type="password" placeholder="password"><button onclick="go()">Sign In</button></div>
<div id="err" style="color:#c00"></div>
<script>
async function go(){ const email=document.querySelector("input[name='email']").value;
  const r = await fetch('https://zoom.us/api/fake-login',{method:'POST',body:JSON.stringify({email})}); const j = await r.json();
  if(j.ok){ document.cookie='zsess='+encodeURIComponent(email)+'; domain=.zoom.us; path=/'; location.href='https://zoom.us/profile'; }
  else { document.getElementById('err').innerText = 'Could not connect to the reCAPTCHA service. Please check your internet connection and reload to get a reCAPTCHA challenge.'; } }
</script></body></html>"""

log = []
def elog(msg, level="info"): log.append((time.time(), level, msg))
signins = []     # (t, email, ok)
last_submit = [0.0]

def signed_in_email(cookie_header):
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "zsess":
            from urllib.parse import unquote
            v = unquote(v)
            if v in ACCOUNTS: return v
    return None

async def install_fake_zoom(context):
    async def handler(route):
        req = route.request; url = req.url
        if url.startswith("https://zoom.us/signin"):
            hdrs = await req.all_headers()
            if signed_in_email(hdrs.get("cookie")):
                return await route.fulfill(status=200, content_type="text/html", body="<script>location.replace('https://zoom.us/profile')</script>")
            return await route.fulfill(status=200, content_type="text/html", body=SIGNIN_HTML)
        if url.startswith("https://zoom.us/profile"):
            return await route.fulfill(status=200, content_type="text/html", body="<h2>Fake Zoom profile</h2>")
        if url.startswith("https://zoom.us/api/fake-login"):
            email = json.loads(req.post_data or "{}").get("email")
            now = time.time(); ok = (now - last_submit[0]) > SIMULATED_WINDOW and email in ACCOUNTS
            last_submit[0] = now
            signins.append((now, email, ok))
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": ok}))
        if "/wc/" in url and ("/start" in url or "/join" in url):
            return await route.fulfill(status=200, content_type="text/html", body="<h2>Fake meeting</h2>")
        await route.abort()
    await context.route("**/*", handler)

def mk_session(i, email):
    return SessionModel(session_type="ZOOM", join_url=f"https://upgrad.zoom.us/j/9000000000{i}", session_name=f"Demo session {i} ({email.split('@')[0]})",
                        moderator_name="Demo", host_email=email, host_password=ACCOUNTS[email])

async def main():
    model = SessionTableModel(); eng = E.Engine(model, elog)
    await eng.start(); assert eng.browser_ready
    orig_new_context = eng._browser.new_context
    contexts = []
    async def new_context(*a, **k):
        ctx = await orig_new_context(*a, **k); await install_fake_zoom(ctx); contexts.append(ctx); return ctx
    eng._browser.new_context = new_context
    out = {"mod": MOD}
    sessions = [mk_session(1, "zoom128@upgrad.com"), mk_session(2, "zoom128@upgrad.com"), mk_session(3, "zoom129@upgrad.com"), mk_session(4, "zoom129@upgrad.com")]
    for s in sessions: model.add_session(s)
    t0 = time.time()
    for s in sessions: eng.launch_zoom(s.join_url)
    deadline = time.time() + 240
    def done(s): return s.status.startswith(("⏳ Connecting", "🌍", "❌")) or "LIVE" in s.status
    while time.time() < deadline and not all(done(s) for s in sessions):
        await asyncio.sleep(1)
    await asyncio.sleep(3)
    out["seconds"] = round(time.time() - t0, 1)
    out["statuses"] = {s.session_name: s.status for s in sessions}
    out["sign_in_submissions"] = len(signins)
    out["refused_by_simulated_recaptcha"] = sum(1 for _, _, ok in signins if not ok)
    out["reused_sign_ins"] = sum(1 for _, l, m in log if "Reusing the sign-in" in m)
    out["log_highlights"] = [m for _, l, m in log if any(k in m for k in ("Logging in", "Login success", "Already logged in", "Reusing", "reCAPTCHA", "Login failed"))]

    if MOD == "new":
        # scenario 2: the saved cookies have expired -> a normal login must still work and refresh them
        signins.clear(); log.clear(); last_submit[0] = 0.0
        eng._account_auth["zoom128@upgrad.com"].state = {"cookies": [{"name": "zsess", "value": "expired", "domain": ".zoom.us", "path": "/", "expires": -1,
                                                                      "httpOnly": False, "secure": False, "sameSite": "Lax"}], "origins": []}
        await eng.cleanup_session(sessions[0].join_url)      # free one of the account's 2 slots first
        extra = mk_session(5, "zoom128@upgrad.com")
        model.add_session(extra); eng.launch_zoom(extra.join_url)
        for _ in range(120):
            await asyncio.sleep(1)
            if done(extra): break
        await asyncio.sleep(2)
        out["expired_cookie_scenario"] = {"status": extra.status, "sign_in_submissions": len(signins),
                                          "state_refreshed": bool(eng._account_auth["zoom128@upgrad.com"].state)
                                          and "expired" not in json.dumps(eng._account_auth["zoom128@upgrad.com"].state)}
    print(json.dumps(out, indent=1, ensure_ascii=False), flush=True)
    await eng.shutdown()

loop = qasync.QEventLoop(app); asyncio.set_event_loop(loop)
with loop:
    loop.run_until_complete(main())
os._exit(0)
