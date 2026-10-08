"""
update_swap.py
---------------
The last step of a self-update: a small batch script that waits for the
running exe to close, swaps the downloaded `.new` file into its place and
starts it. Kept free of Qt so it can be exercised on its own.

Two things went wrong with the earlier version of this script (a 2.2.3 ->
2.3.1 update failed with "Failed to load Python DLL _MEIxxxx\\python314.dll"
and left the old exe in place):

 1. It waited a fixed 2 seconds before moving the new exe over the old one.
    Shutting down (closing the Playwright browser) can take several seconds
    more, so the exe was still locked, the move failed, and the script went
    on to start the OLD exe anyway. Now it retries the move until it works
    (up to ~90 s) and starts nothing if it never does.
 2. The script inherited this process's PyInstaller environment variables
    (_MEIPASS2, _PYI_*), which point at this run's temp extraction folder.
    That folder is deleted when this app exits, so an exe started with those
    variables looks for its Python DLL in a folder that no longer exists.
    The variables are cleared both in the script and for the process that
    runs it, so the new exe unpacks into a fresh folder.
"""

import os
import subprocess

_RETRIES = 90   # one attempt per second


def write_swap_script(current_exe: str, new_exe_path: str) -> str:
    bat_path = current_exe + "_updater.bat"
    bat = ("@echo off\n"
           'set "_MEIPASS2="\n'
           'set "_PYI_APPLICATION_HOME_DIR="\n'
           'set "_PYI_ARCHIVE_FILE="\n'
           'set "_PYI_PARENT_PROCESS_LEVEL="\n'
           "set PYINSTALLER_RESET_ENVIRONMENT=1\n"
           "set /a tries=0\n"
           ":retry\n"
           "ping -n 2 127.0.0.1 > nul\n"          # ~1 s sleep; `timeout` fails without a console
           f'move /y "{new_exe_path}" "{current_exe}" > nul 2>&1\n'
           "if not errorlevel 1 goto launch\n"
           "set /a tries+=1\n"
           f"if %tries% lss {_RETRIES} goto retry\n"
           'del "%~f0"\n'
           "exit /b 1\n"
           ":launch\n"
           f'start "" "{current_exe}"\n'
           'del "%~f0"\n')
    with open(bat_path, "w") as f:
        f.write(bat)
    return bat_path


def launch_swap_script(bat_path: str):
    env = {k: v for k, v in os.environ.items()
           if k != "_MEIPASS2" and not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        ["cmd", "/c", bat_path], env=env, close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                      | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
