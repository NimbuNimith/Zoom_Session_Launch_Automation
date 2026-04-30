@echo off
title Zoom Command Center - Installer
echo ============================================
echo   Zoom Command Center - One-Time Setup
echo ============================================
echo.

echo [1/3] Checking Python...
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.9+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
python --version
echo Python found.
echo.

echo [2/3] Installing required packages...
python -m pip install pandas playwright openpyxl
IF ERRORLEVEL 1 (
    echo ERROR: Package install failed. Check your internet connection.
    pause
    exit /b 1
)
echo.

echo [3/3] Installing Playwright browser (Chromium)...
python -m playwright install chromium
IF ERRORLEVEL 1 (
    echo ERROR: Playwright browser install failed.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup Complete!
echo   Run the app with:  python Zoom_Ai.py
echo   Or double-click:   run_app.bat
echo ============================================
pause
