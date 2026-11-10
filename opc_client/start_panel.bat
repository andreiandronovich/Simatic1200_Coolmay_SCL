@echo off
title Drive panel
cd /d "%~dp0"
".venv\Scripts\python.exe" panel.py
echo.
echo Panel stopped. Press any key to close.
pause >nul
