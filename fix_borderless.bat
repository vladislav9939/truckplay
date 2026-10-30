@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Переводит ETS2 в оконный borderless-режим (делается один раз)
if exist "TruckPlay.exe" (
  start "" /wait "TruckPlay.exe" --borderless
) else (
  python src\truckplay.py --borderless
  pause
)
