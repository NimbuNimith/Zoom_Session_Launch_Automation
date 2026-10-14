@echo off
REM make_build_env.bat
REM --------------------
REM Creates the CLEAN Python environment releases are built with:
REM   %LOCALAPPDATA%\CommandCenterBuild\env
REM containing only what the app needs (requirements.txt) plus PyInstaller. The shared
REM everyday Python carries packages from other projects (pyarrow, Pillow, cryptography,
REM streamlit, ...) that PyInstaller then sweeps into the app.
REM
REM It lives outside the project folder on purpose: PySide6 has very long file names, and
REM under the OneDrive path Windows refuses to unpack them ("No such file or directory").
REM
REM PySide6-Essentials (QtCore/QtGui/QtWidgets/QtNetwork/QtSvg) is installed instead of the
REM PySide6 meta-package, which also pulls in WebEngine, PDF and the Qt Quick add-ons
REM (~230 MB the app never uses).
REM
REM Versions are pinned to what the app was tested with. playwright in particular must stay on
REM the release whose browsers.json asks for Chromium 1208 — a newer one wants a different
REM Chromium, which would no longer match the browser the installer ships.
REM
REM Run once, and again after requirements.txt changes.

set ENV_DIR=%LOCALAPPDATA%\CommandCenterBuild\env
if not exist "%ENV_DIR%\Scripts\python.exe" (
    echo Creating %ENV_DIR% ...
    py -3.14 -m venv "%ENV_DIR%" || py -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
)
"%ENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
"%ENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check ^
    "PySide6-Essentials==6.11.1" "qasync==0.28.0" "playwright==1.58.0" "pandas==3.0.2" "openpyxl>=3.1" ^
    "requests>=2.31" "packaging>=23.0" "psutil>=5.9" pyinstaller
if errorlevel 1 exit /b 1
echo.
echo Build environment ready: %ENV_DIR%
