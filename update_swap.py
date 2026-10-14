"""
update_swap.py
---------------
The last step of a self-update: a small batch script that waits for the
running app to close, swaps the downloaded files into place, starts the new
version, and puts the old version back if the new one doesn't come up. Kept
free of Qt so it can be exercised on its own (see tests in the scratchpad /
tools).

Two kinds of update (see updater.py):
  * exe only  - `Zoom_Ai_latest.exe` is replaced.
  * full pack - the exe, `_internal\\` (Python, Qt, driver) and `runtime.id` are
                replaced. The new files were unpacked into a staging folder
                inside the app folder first, so every move below is a rename
                on the same drive (instant, and either happens or doesn't).

Earlier failures this script is built around (a 2.2.3 -> 2.3.1 update failed with
"Failed to load Python DLL _MEIxxxx\\python314.dll" and left the old exe in place):

 1. It waited a fixed 2 seconds. Shutting down (closing the Playwright browser)
    can take several seconds more, so the exe was still locked, the move failed,
    and the script went on to start the OLD exe anyway. Now it retries until the
    exe can be moved (up to ~90 s) and starts nothing if it never can.
 2. It inherited this process's PyInstaller environment variables (_MEIPASS2,
    _PYI_*), which point at the old process's folder. They are cleared both in
    the script and for the process that runs it.

NEW: ROLLBACK. The old exe / `_internal` / runtime.id are kept as `.old` until
the new app reports a healthy start by creating `startup_ok` in the app folder
(app.py does that once its window is up). If that doesn't happen (the new
process died, or 120 s pass), the script stops the new process, puts the old
files back, starts the old version and leaves `update_failed.txt` (the version
that failed) for the app to tell the user about.
"""

import os
import subprocess

_RETRIES = 90            # one attempt per second while the old app is still shutting down
_WATCH_SECONDS = 120     # how long the new version has to report a healthy start
MARKER_OK = "startup_ok"
MARKER_FAILED = "update_failed.txt"
STAGE_DIR = "_update_stage"


def write_swap_script(current_exe: str, new_exe_path: str = None, stage_dir: str = None,
                      new_version: str = "") -> str:
    """Returns the path of the written .bat. Give `new_exe_path` for an exe-only
    update, or `stage_dir` (containing Zoom_Ai_latest.exe, runtime.id, _internal\\)
    for a full pack."""
    assert bool(new_exe_path) != bool(stage_dir), "exactly one of new_exe_path / stage_dir"
    app_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    bat_path = current_exe + "_updater.bat"
    internal = os.path.join(app_dir, "_internal")
    internal_old = internal + ".old"
    rid = os.path.join(app_dir, "runtime.id")
    rid_old = rid + ".old"
    exe_old = current_exe + ".old"
    ok = os.path.join(app_dir, MARKER_OK)
    failed = os.path.join(app_dir, MARKER_FAILED)
    full = stage_dir is not None

    L = []
    add = L.append
    add("@echo off")
    add('set "_MEIPASS2="')
    add('set "_PYI_APPLICATION_HOME_DIR="')
    add('set "_PYI_ARCHIVE_FILE="')
    add('set "_PYI_PARENT_PROCESS_LEVEL="')
    add("set PYINSTALLER_RESET_ENVIRONMENT=1")
    add(f'del "{ok}" > nul 2>&1')
    add("set /a tries=0")
    add(":retry")
    add("ping -n 2 127.0.0.1 > nul")                       # ~1 s sleep; `timeout` fails without a console
    # 1. wait until the old app has really exited. Windows lets a RUNNING exe be renamed (and
    #    even opened for append), so neither proves that; a process of that name does.
    add(f'tasklist /fi "imagename eq {exe_name}" 2> nul | findstr /i /c:"{exe_name}" > nul')
    add("if not errorlevel 1 goto again")
    add(f'move /y "{current_exe}" "{exe_old}" > nul 2>&1')
    add("if errorlevel 1 goto again")
    if full:
        add(f'if exist "{rid}" copy /y "{rid}" "{rid_old}" > nul 2>&1')
        add(f'if exist "{internal_old}" rmdir /s /q "{internal_old}" > nul 2>&1')
        add(f'move "{internal}" "{internal_old}" > nul 2>&1')
        add("if errorlevel 1 goto undo_exe")                # something still has a library open
        add(f'move "{os.path.join(stage_dir, "_internal")}" "{internal}" > nul 2>&1')
        add("if errorlevel 1 goto swap_failed")
        add(f'move /y "{os.path.join(stage_dir, "runtime.id")}" "{rid}" > nul 2>&1')
        add("if errorlevel 1 goto swap_failed")
        add(f'move /y "{os.path.join(stage_dir, exe_name)}" "{current_exe}" > nul 2>&1')
    else:
        add(f'move /y "{new_exe_path}" "{current_exe}" > nul 2>&1')
    add("if errorlevel 1 goto swap_failed")
    add("goto launch")

    add(":undo_exe")                                        # old exe back, try again shortly
    add(f'move /y "{exe_old}" "{current_exe}" > nul 2>&1')
    add(":again")
    add("set /a tries+=1")
    add(f"if %tries% lss {_RETRIES} goto retry")
    add('del "%~f0"')                                       # never managed to swap: leave everything as it was
    add("exit /b 1")

    add(":launch")
    add(f'start "" "{current_exe}"')
    add("set /a waited=0")
    add(":watch")
    add("ping -n 2 127.0.0.1 > nul")
    add(f'if exist "{ok}" goto healthy')
    add("set /a waited+=1")
    # after a grace period, a new process that is no longer running will never report in
    add("if %waited% geq 15 (")
    add(f'  tasklist /fi "imagename eq {exe_name}" 2>nul | findstr /i /c:"{exe_name}" > nul')
    add("  if errorlevel 1 goto rollback")
    add(")")
    add(f"if %waited% lss {_WATCH_SECONDS} goto watch")

    add(":rollback")
    add(f'taskkill /f /im "{exe_name}" > nul 2>&1')
    add("ping -n 4 127.0.0.1 > nul")
    add(f'echo {new_version} > "{failed}"')
    add(":swap_failed")
    if full:
        add(f'if exist "{internal_old}" (')
        add(f'  if exist "{internal}" rmdir /s /q "{internal}" > nul 2>&1')
        add(f'  move "{internal_old}" "{internal}" > nul 2>&1')
        add(")")
        add(f'if exist "{rid_old}" move /y "{rid_old}" "{rid}" > nul 2>&1')
    add(f'if exist "{exe_old}" move /y "{exe_old}" "{current_exe}" > nul 2>&1')
    add(f'if not exist "{failed}" echo {new_version} > "{failed}"')
    add(f'start "" "{current_exe}"')
    add('del "%~f0"')
    add("exit /b 1")

    add(":healthy")
    add(f'del "{exe_old}" > nul 2>&1')
    if full:
        add(f'rmdir /s /q "{internal_old}" > nul 2>&1')
        add(f'del "{rid_old}" > nul 2>&1')
        add(f'rmdir /s /q "{stage_dir}" > nul 2>&1')
    add('del "%~f0"')

    with open(bat_path, "w") as f:
        f.write("\n".join(L) + "\n")
    return bat_path


def launch_swap_script(bat_path: str):
    env = {k: v for k, v in os.environ.items()
           if k != "_MEIPASS2" and not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        ["cmd", "/c", bat_path], env=env, close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                      | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
