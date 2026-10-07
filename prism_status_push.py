"""
prism_status_push.py
---------------------
Pushes a Prism-hosted session's status from "yet-to-start" to "started"
once Zoom Command Center detects the meeting has actually gone LIVE in the
browser. Without this, Prism never learns the session happened, so it stays
"yet-to-start" and the recording never gets attached.

Prism has THREE session entity types, each with its own mutation and its
own required ID(s):

    Prism type              Mutation                              ID(s) needed
    -----------------------  ------------------------------------  ---------------------------------
    session                  updateStatusForSession                 session_id
    workshop-session          updateStatusForWorkshopSession          session_id (the workshopSession id)
    session-group-session     updateStatusForSessionGroupSession      session_group_id + session_group_session_id

IMPORTANT: the type for a given row comes from the `virtualMeetingFor` field
returned by the liveSessions query (see prism_data_pull.py) — it is NOT the
`LS Type` / `Session Type` field from the old export, which is just a
display category like "Live" or "lecture".

Credentials: this module reads the token via prism_token.get_cached_token()
— loaded from the Google Sheet's Config tab at startup, falling back to
PRISM_BEARER_TOKEN in prism_config.py (env var first, else a value pasted
into that file) if the Sheet can't be reached. See prism_config.py for why
it's git-ignored — short version: this repo is public, don't commit a real
token to it.
"""

import os
import asyncio
import requests

from prism_token import get_cached_token

PRISM_GRAPHQL_URL = "https://lxp-api.upgrad.com/graphql"


def _headers(role: str = "delivery-manager"):
    token = get_cached_token()
    if not token:
        raise RuntimeError(
            "No Prism bearer token found. It's normally loaded from the Google "
            "Sheet's Config tab at startup — check the Event Log for why that "
            "failed — or set PRISM_BEARER_TOKEN as an env var."
        )
    return {
        "accept": "*/*",
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "apollographql-client-name": "prism-frontend",
        "apollographql-client-version": "1.0",
        "role": role,
    }


def _post(payload, role: str = "delivery-manager", timeout=30):
    resp = requests.post(PRISM_GRAPHQL_URL, headers=_headers(role), json=payload, timeout=timeout)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # See the matching comment in prism_data_pull.py — the response
        # BODY (not just the status line) is where Prism actually says
        # why the request was rejected. Without this, every failure just
        # looks like "400 Client Error: Bad Request for url: ..." with
        # no way to tell an expired token from a bad role header from a
        # malformed mutation.
        raise RuntimeError(
            f"Prism API HTTP {resp.status_code}: {resp.text[:500]}"
        ) from e
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Prism API error: {data['errors']}")
    return data


# ---------------------------------------------------------------------------
# Type 1: plain "session"
# ---------------------------------------------------------------------------
# NOTE: the working example for this one (Live_Session_HEad.py) used
# role: "live-session-head" — NOT "delivery-manager" like the other two
# scripts. Keeping that distinction here; sending the wrong role header
# could get this specific mutation rejected even with a valid token.
_SESSION_MUTATION = """
mutation updateStatusForSession($data: UpdateStatusForSessionInput!, $where: SessionWhereUniqueInput!) {
  updateStatusForSession(data: $data, where: $where) {
    id
    virtualMeeting { id code startUrl status }
  }
}
"""


def push_session_status(session_id, status="started"):
    payload = {
        "operationName": "updateStatusForSession",
        "variables": {"where": {"id": session_id}, "data": {"status": status}},
        "query": _SESSION_MUTATION,
    }
    return _post(payload, role="live-session-head")


# ---------------------------------------------------------------------------
# Type 2: "workshop-session"
# ---------------------------------------------------------------------------
_WORKSHOP_SESSION_MUTATION = """
mutation updateStatusForWorkshopSession($data: updateStatusForWorkshopSessionInput!, $where: WorkshopSessionWhereUniqueInput!) {
  updateStatusForWorkshopSession(data: $data, where: $where) {
    id
    virtualMeeting { id code startUrl status }
  }
}
"""


def push_workshop_session_status(workshop_session_id, status="started"):
    payload = {
        "operationName": "updateStatusForWorkshopSession",
        "variables": {"where": {"id": workshop_session_id}, "data": {"status": status}},
        "query": _WORKSHOP_SESSION_MUTATION,
    }
    return _post(payload, role="delivery-manager")


# ---------------------------------------------------------------------------
# Type 3: "session-group-session"
# ---------------------------------------------------------------------------
_SESSION_GROUP_SESSION_MUTATION = """
mutation updateStatusForSessionGroupSession($data: UpdateStatusForSessionGroupSessionInput!, $where: SessionGroupSessionWhereUniqueInput!) {
  updateStatusForSessionGroupSession(data: $data, where: $where) {
    id
    virtualMeetingProvider { virtualMeetingProvider vendorSource }
    virtualMeeting { id code startUrl status joinUrl encryptedPassword }
  }
}
"""


def push_session_group_session_status(session_group_id, session_group_session_id, status="started"):
    payload = {
        "operationName": "updateStatusForSessionGroupSession",
        "variables": {
            "where": {
                "sessionGroup": session_group_id,
                "sessionGroupSession": session_group_session_id,
            },
            "data": {"status": status},
        },
        "query": _SESSION_GROUP_SESSION_MUTATION,
    }
    return _post(payload, role="delivery-manager")


# ---------------------------------------------------------------------------
# Dispatcher — call this one from Zoom_Ai.py
# ---------------------------------------------------------------------------
def push_prism_live_status(prism_type, ids, status="started"):
    """
    prism_type: 'session' | 'workshop-session' | 'session-group-session'
    ids: dict of whatever IDs that type needs, e.g.
         {"session_id": "..."}  -- for 'session' / 'workshop-session'
         {"session_group_id": "...", "session_group_session_id": "..."}  -- for 'session-group-session'
    """
    if prism_type == "session":
        return push_session_status(ids["session_id"], status=status)
    elif prism_type == "workshop-session":
        return push_workshop_session_status(ids["session_id"], status=status)
    elif prism_type == "session-group-session":
        return push_session_group_session_status(
            ids["session_group_id"], ids["session_group_session_id"], status=status
        )
    else:
        raise ValueError(f"Unknown Prism session type: {prism_type!r}")


async def push_prism_live_status_async(prism_type, ids, status="started"):
    """Async-safe wrapper — Zoom_Ai.py's engine runs on an asyncio event
    loop (Playwright), and requests.post() is blocking. Running it in a
    thread keeps it from stalling the heartbeat/monitoring loops."""
    return await asyncio.to_thread(push_prism_live_status, prism_type, ids, status)
