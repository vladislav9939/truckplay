@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "TruckPlay.exe" (
  start "" "TruckPlay.exe"
) else (
  title TruckPlay
  python src\truckplay.py
  if errorlevel 1 pause
)
