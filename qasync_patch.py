"""
qasync_patch.py
-----------------
Works around a known, still-open upstream qasync bug: _SimpleTimer
allocates one Qt timer per zero-delay callback (the path every
asyncio.create_task()/call_soon() goes through under qasync) and
asserts the newly-allocated Qt timer id was never used before. Qt
legitimately RECYCLES timer ids once an old one fires and is cleaned
up — under any burst of near-simultaneous zero-delay callbacks (which
this app triggers constantly: Auto-Pilot, the three heartbeat loops,
CSV loading, poll launches, ...), a new timer can grab an id an old,
not-yet-cleaned-up entry still holds, and the assertion trips —
crashing whatever create_task() call triggered it. The resulting task
is destroyed without ever running ("coroutine ... was never awaited"),
which is exactly why "Upload Sessions CSV" (or a Zoom/Prism launch, or
anything else routed through create_task) can silently do nothing.

Confirmed as a known, unresolved upstream issue — see
CabbageDevelopment/qasync issue #22 (a related KeyError-on-cleanup bug
in the same mechanism) and PR #123 (a full rewrite of this timer
mechanism proposed by the maintainers, unmerged/stalled since Aug 2025
on a failing test). PyPI has no newer qasync release with a fix as of
this writing (0.28.0 is latest), so this patches the two unsafe lines
locally instead of waiting on upstream:

  - add_callback: overwrite instead of asserting on an id collision — a
    collision only ever means a stale, already-fired entry is still
    sitting in the dict; there's nothing to protect by crashing here.
  - timerEvent's cleanup: use dict.pop(..., None) instead of a bare
    `del`, so a timer event for an id that's already been cleaned up
    (or was never fully registered) is a no-op instead of a KeyError.

IMPORTANT: timerEvent must still call self.killTimer(timerid)
unconditionally at the end, exactly like upstream does — startTimer(0)
creates a REPEATING Qt timer, so skipping killTimer() leaves it firing
forever after its one real callback already ran. An earlier version of
this file dropped that call while rewriting the method and it was a
real regression: over a long session with many create_task() calls
(the three heartbeat loops, poll launches, participant scans, ...)
these dead timers accumulate and flood the event loop, so *new*
create_task() work silently takes longer and longer to get scheduled —
not a crash, just growing timer noise on every loop iteration. Confirmed
2026-09-28 against a live session where Auto-Pilot poll launches and
participant scraping both stalled indefinitely with no errors.

Call patch() once, at startup, before qasync.QEventLoop(app) is
constructed — app.py does this immediately after `import qasync`.
"""

import qasync


def patch():
    timer_cls = qasync._SimpleTimer
    callbacks_attr = "_SimpleTimer__callbacks"  # name-mangled `self.__callbacks`

    def add_callback(self, handle, delay=0):
        timerid = self.startTimer(int(max(0, delay) * 1000))
        getattr(self, callbacks_attr)[timerid] = handle
        return handle

    def timerEvent(self, event):
        timerid = event.timerId()
        callbacks = getattr(self, callbacks_attr)
        if self._stopped:
            self.killTimer(timerid)
            callbacks.pop(timerid, None)
            return
        handle = callbacks.pop(timerid, None)
        if handle is not None and not handle._cancelled:
            handle._run()
        # Upstream always kills the Qt timer here regardless of whether a
        # callback was found (see qasync/__init__.py's own timerEvent) —
        # startTimer(0) creates a REPEATING timer, so skipping this (as an
        # earlier version of this patch did) leaves every zero-delay
        # callback's timer firing forever after its one real callback
        # already ran. Over a long session with many asyncio.create_task()
        # calls (heartbeat loops, poll launches, participant scans, ...)
        # these dead timers accumulate and flood the event loop, which is
        # what was actually behind new tasks silently taking forever to
        # get scheduled — not a crash, just endless unrelated timer noise
        # crowding out real work on every loop iteration.
        self.killTimer(timerid)

    timer_cls.add_callback = add_callback
    timer_cls.timerEvent = timerEvent
