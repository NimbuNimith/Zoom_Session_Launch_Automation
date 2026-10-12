@echo off
REM build_release.bat
REM -------------------
REM Four steps. Run from the project folder.
REM
REM Chromium is NOT bundled into the exe any more (that was 64% of every
REM auto-update download). It travels in the INSTALLER instead, and the app
REM downloads it once from a GitHub Release asset if it's ever missing. See
REM Zoom_Ai_latest.spec and installer.iss for the full story.
REM
REM Prerequisites (one-time, per build machine):
REM   pip install -r requirements.txt pyinstaller
REM   Inno Setup installed: https://jrsoftware.org/isdl.php
REM   (installs ISCC.exe — add its folder to PATH, or edit ISCC_PATH below)

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

echo === Step 1: Make sure Playwright's Chromium is installed on this machine ===
echo     (the installer copies it from here; it is no longer bundled into the exe)
set PLAYWRIGHT_BROWSERS_PATH=0
playwright install chromium
if errorlevel 1 (
    echo playwright install chromium failed — stopping.
    exit /b 1
)

echo.
echo === Step 2: Browser assets (installer include + fallback download zip) ===
python tools\prepare_release_assets.py
if errorlevel 1 (
    echo prepare_release_assets failed — stopping.
    exit /b 1
)

echo.
echo === Step 3: PyInstaller ===
pyinstaller Zoom_Ai_latest.spec --noconfirm --clean
if errorlevel 1 (
    echo PyInstaller build failed — stopping before the installer step.
    exit /b 1
)

echo.
echo === Step 4: Inno Setup ===
%ISCC_PATH% installer.iss
if errorlevel 1 (
    echo Inno Setup compile failed.
    exit /b 1
)

echo.
echo Done. Installer is in the Output\ folder.
echo dist\Zoom_Ai_latest.exe should be ~150-160 MB (no browser inside).
echo.
echo Release checklist:
echo   1. If Step 2 printed a "gh release create chromium-..." command for a NEW
echo      Chromium revision, run it once (skip if that release already exists).
echo   2. Upload dist\Zoom_Ai_latest.exe to the GitHub Release (for auto-update).
echo   3. Share the installer from Output\ with learners (it includes the browser).
echo   4. Set version.txt to match updater.py's CURRENT_VERSION — LAST.
