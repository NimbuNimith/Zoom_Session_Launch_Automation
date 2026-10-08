"""
single_instance.py
--------------------
Enforces exactly one running copy of the app at a time, using Qt's own
cross-platform local IPC (QLocalServer/QLocalSocket) — no extra
dependencies, and it doubles as a message channel to the first instance
rather than just a lock.

Without this, every icon click (or every accidental double-click) spawns
a completely separate process — its own QApplication, its own Engine,
its own Playwright browser window. Combined with the shutdown-ordering
issues this project has already been through, that's exactly how
multiple background processes pile up: nothing ever stopped a second,
third, fourth launch from happening in the first place.

HOW IT WORKS
-------------
- First launch: try to connect to a well-known local server name. No one
  answers -> this is the first instance. Start listening ourselves, so
  any LATER launch can find us and we can react to it.
- Later launch: connects successfully -> another instance is already
  running. Send it a short "activate" message (so it can raise its
  window to the front) and exit immediately — BEFORE ever creating a
  MainWindow or Engine, so a duplicate launch never spins up a second
  Playwright browser at all.
"""

from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SERVER_NAME = "ZoomPrismCommandCenter_SingleInstance_v1"
_ACTIVATE_MSG = b"activate"


def try_acquire_or_notify_existing(connect_timeout_ms: int = 250):
    """Call this right after creating the QApplication, before building
    MainWindow. Requires a QCoreApplication to already exist (the
    waitFor*() calls below spin a local event loop internally, which
    needs one) — but does NOT require that app's main event loop
    (app.exec() / qasync's run_forever()) to be running yet.

    Returns a live QLocalServer if this is the first instance — the
    caller owns it and must keep a reference alive for the app's
    lifetime (see wire_activation_to_window). Returns None if another
    instance is already running; an "activate" message has already been
    sent to it, and the caller should exit immediately without building
    a MainWindow/Engine.
    """
    socket = QLocalSocket()
    socket.connectToServer(_SERVER_NAME)
    if socket.waitForConnected(connect_timeout_ms):
        socket.write(_ACTIVATE_MSG)
        socket.waitForBytesWritten(connect_timeout_ms)
        socket.disconnectFromServer()
        return None

    server = QLocalServer()
    # A prior crash or force-kill (os._exit, TerminateProcess, a killed
    # -9, etc.) can occasionally leave a stale server name registered
    # even though nothing is actually listening anymore, depending on
    # platform. removeServer() clears that before we listen, so a truly
    # dead previous instance can never falsely block a new one from
    # starting — the common "the process died weirdly and now the app
    # won't launch at all" failure mode this would otherwise create.
    QLocalServer.removeServer(_SERVER_NAME)
    if not server.listen(_SERVER_NAME):
        # Extremely unlikely right after removeServer(), but if it still
        # fails, don't block the app from starting over an IPC glitch —
        # just proceed without single-instance enforcement this run
        # rather than refuse to launch at all.
        print(f"Could not start single-instance server (continuing without "
              f"single-instance enforcement this run): {server.errorString()}")
    return server


def wire_activation_to_window(server: QLocalServer, window) -> None:
    """Whenever a second launch attempt signals us, bring the real
    window to the front instead of leaving it buried behind others."""
    def _raise_window():
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_new_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(_raise_window)
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(_on_new_connection)
