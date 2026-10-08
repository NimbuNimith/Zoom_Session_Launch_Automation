@echo off
REM build_release.bat
REM -------------------
REM Three steps now, not two. Run from the project folder.
REM
REM Chromium is bundled INTO the exe now (see Zoom_Ai_latest.spec's
REM docstring for why the earlier "install it externally via the
REM installer" approach was reverted). That means THIS build machine's
REM own Playwright installation needs Chromium present, in hermetic
REM mode, before PyInstaller runs — Step 1 below does that every time;
REM it's fast and a no-op if already installed, so it's safe to leave in
REM permanently rather than remembering to run it manually once.
REM
REM Prerequisites (one-time, per build machine):
REM   pip install -r requirements.txt pyinstaller
REM   Inno Setup installed: https://jrsoftware.org/isdl.php
REM   (installs ISCC.exe — add its folder to PATH, or edit ISCC_PATH below)

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

echo === Step 1: Ensure Chromium is installed hermetically for bundling ===
set PLAYWRIGHT_BROWSERS_PATH=0
playwright install chromium
if errorlevel 1 (
    echo playwright install chromium failed — stopping before the PyInstaller step.
    exit /b 1
)

echo.
echo === Step 2: PyInstaller ===
pyinstaller Zoom_Ai_latest.spec --noconfirm --clean
if errorlevel 1 (
    echo PyInstaller build failed — stopping before the installer step.
    exit /b 1
)

echo.
echo === Step 3: Inno Setup ===
%ISCC_PATH% installer.iss
if errorlevel 1 (
    echo Inno Setup compile failed.
    exit /b 1
)

echo.
echo Done. Installer is in the Output\ folder.
echo dist\Zoom_Ai_latest.exe should now be ~250-300MB larger than before
echo (Chromium is bundled in) — if it isn't, Step 1 likely didn't find/
echo install Chromium and the .spec's build-time guard should have
echo caught that already, but double check the output above.
echo Next: upload BOTH dist\Zoom_Ai_latest.exe (for auto-update)
echo AND the installer .exe (for new installs) to the GitHub Release, and
echo update version.txt to match updater.py's CURRENT_VERSION.
