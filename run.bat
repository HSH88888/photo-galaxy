@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "PhotoGalaxy.exe" (
    start "" "PhotoGalaxy.exe"
) else (
    start "" pythonw app.py
)
