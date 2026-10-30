@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Ставит плагин телеметрии в игру (TruckPlay читает данные фуры)
if exist "TruckPlay.exe" (
  start "" /wait "TruckPlay.exe" --install-plugin
) else (
  python src\truckplay.py --install-plugin
  pause
)
