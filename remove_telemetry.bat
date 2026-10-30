@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Убирает плагин телеметрии из игры
if exist "TruckPlay.exe" (
  start "" /wait "TruckPlay.exe" --uninstall-plugin
) else (
  python src\truckplay.py --uninstall-plugin
  pause
)
