@echo off
REM build_release.bat
REM -------------------
REM Run from the project folder. Builds the folder install + installer + update assets.
REM
REM The app is a FOLDER build (dist\Zoom_Ai_latest\ = small exe + _internal\), installed by
REM the installer. Chromium is not inside it: it travels in the installer and the app
REM downloads it once from a GitHub Release asset if it's ever missing. See
REM Zoom_Ai_latest.spec, installer.iss and updater.py for the full story.
REM
REM Prerequisites (one-time, per build machine):
REM   tools\make_build_env.bat      (clean Python env with only what the app needs)
REM   Inno Setup installed: https://jrsoftware.org/isdl.php
REM   (installs ISCC.exe — add its folder to PATH, or edit ISCC_PATH below)
REM
REM Usage:  build_release.bat "What changed, shown in the update prompt"

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC_PATH% set ISCC_PATH="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
set ENV_DIR=%LOCALAPPDATA%\CommandCenterBuild\env
set PY="%ENV_DIR%\Scripts\python.exe"
set NOTES=%~1

if not exist %PY% (
    echo The clean build environment is missing - run tools\make_build_env.bat first.
    exit /b 1
)

echo === Step 1: Make sure Playwright's Chromium is installed on this machine ===
echo     (the installer copies it from here; it is not bundled into the app)
set PLAYWRIGHT_BROWSERS_PATH=0
%PY% -m playwright install chromium
if errorlevel 1 (
    echo playwright install chromium failed — stopping.
    exit /b 1
)
set PLAYWRIGHT_BROWSERS_PATH=

echo.
echo === Step 2: Browser assets (installer include + fallback download zip) ===
%PY% tools\prepare_release_assets.py
if errorlevel 1 (
    echo prepare_release_assets failed — stopping.
    exit /b 1
)

echo.
echo === Step 3: PyInstaller (folder build) ===
"%ENV_DIR%\Scripts\pyinstaller.exe" Zoom_Ai_latest.spec --noconfirm --clean
if errorlevel 1 (
    echo PyInstaller build failed — stopping before the installer step.
    exit /b 1
)

echo.
echo === Step 4: Update assets (runtime id, exe, pack, update.json) ===
%PY% tools\make_release_assets.py --notes "%NOTES%"
if errorlevel 1 (
    echo make_release_assets failed — stopping.
    exit /b 1
)

echo.
echo === Step 5: Inno Setup ===
%ISCC_PATH% installer.iss
if errorlevel 1 (
    echo Inno Setup compile failed.
    exit /b 1
)

echo.
echo Done. Installer is in Output\, update files are in dist\release\.
echo.
echo Release checklist (version.txt is NOT touched - it stays 1.0.0):
echo   1. If Step 2 printed a "gh release create chromium-..." command for a NEW
echo      Chromium revision, run it once (skip if that release already exists).
echo   2. Create the GitHub release v^<version^> with the two assets from dist\release\
echo      (Step 4 printed the exact gh command). Check both download URLs work.
echo   3. Copy dist\release\update.json to the repository root and push it LAST - from
echo      that moment running apps are offered the update.
echo   4. Share the installer from Output\ with learners (it includes the browser).
