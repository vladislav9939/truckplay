@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Ждёт запуска ETS2 и сам поднимает экран
if exist "TruckPlay.exe" (
  start "" "TruckPlay.exe" --watch
) else (
  title TruckPlay (watch)
  python src\truckplay.py --watch
)
