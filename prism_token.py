"""
prism_token.py
----------------
Where the Prism API bearer token comes from at runtime.

The token is kept in the Google Sheet (Config tab, cell B1) and fetched
through the Apps Script integration this app already uses for the session
roster, instead of living in the app's own code — so it can be rotated by
editing one cell, with no rebuild or redistribution. refresh_token() runs
once at startup (see app.py) and caches the result in memory;
prism_data_pull / prism_status_push read it through get_cached_token().

Why a plain cached value rather than an async lookup at the point of use:
both of those modules build their request headers in synchronous functions
that run inside asyncio.to_thread(...), where nothing can be awaited. The
token therefore has to be resolved on the event loop beforehand.

If the Sheet can't be reached (or the script hasn't been updated with the
`token` action yet) the built-in PRISM_BEARER_TOKEN from prism_config.py —
if one is set — is used instead, and the reason is logged. That keeps a
network blip or a half-finished rollout from taking Prism offline.

Not encryption: the token is still plaintext in the Sheet and in this
process's memory, and the Apps Script URL carries no secret of its own.
What this buys is that the token no longer has to ship inside the app.
"""

import asyncio

from gsheets_api_client import create_client_from_settings, GSheetsError
from prism_config import PRISM_BEARER_TOKEN as _BUILT_IN_TOKEN

_cached_token = None          # set once the Sheet has answered; None = use the built-in one

# Transient failures (timeouts, network blips, a slow Apps Script cold start)
# are retried; answers from the script itself (missing Config tab, empty
# cell, script not updated) won't change by waiting, so they aren't.
_RETRY_DELAYS = (0, 5, 10)    # seconds to wait before each attempt
_TRANSIENT_CODES = {"TIMEOUT", "NETWORK_ERROR"}


def get_cached_token() -> str:
    """Synchronous and cheap — safe to call from the threads that build
    Prism request headers. Prefers the Sheet's token, else the built-in one
    (possibly empty, which the callers already turn into a clear error)."""
    return _cached_token or _BUILT_IN_TOKEN


def token_source() -> str:
    if _cached_token:
        return "Google Sheet"
    return "built-in fallback" if _BUILT_IN_TOKEN else "none"


async def refresh_token(log_fn=None) -> bool:
    """Fetch the token from the Sheet and cache it. Returns True on success.
    Never raises — problems are logged via log_fn(msg, level) and the
    built-in token (if any) stays in effect."""
    global _cached_token

    def log(msg: str, level: str = "info"):
        if log_fn:
            log_fn(msg, level)

    last_reason = "unknown error"
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            client = create_client_from_settings()
            token = await asyncio.to_thread(client.fetch_token)
        except GSheetsError as e:
            last_reason = f"{e.code}: {e.message}"
            if e.code not in _TRANSIENT_CODES:
                break
        except Exception as e:
            last_reason = f"{type(e).__name__}: {e}"
        else:
            _cached_token = token
            log("Prism token loaded from the Google Sheet.", "ok")
            return True

    if _BUILT_IN_TOKEN:
        log(f"Prism token: couldn't load it from the Google Sheet ({last_reason}) — "
            f"using the built-in token instead.", "warn")
    else:
        log(f"Prism token: couldn't load it from the Google Sheet ({last_reason}) and "
            f"there's no built-in token — Prism features won't work until this is fixed "
            f"(check the Config tab and that the Apps Script has the 'token' action).", "err")
    return False
