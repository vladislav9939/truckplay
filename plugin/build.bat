@echo off
rem ---------------------------------------------------------------------------
rem  Сборка плагина телеметрии TruckPlay (x64).
rem  Нужно: Visual Studio Build Tools с C++ и заголовки SCS Telemetry SDK
rem  в ..\third_party\scssdk\include  (скачать: modding.scssoft.com -> SDK/Telemetry)
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if "%~1"=="" (set SDK=%~dp0..\third_party\scssdk\include) else (set SDK=%~1)
if not exist "%SDK%\scssdk_telemetry.h" (
  echo [!] Не найдены заголовки SCS SDK: "%SDK%"
  echo     Скачайте SDK и положите include в third_party\scssdk\include
  exit /b 1
)

set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
if not exist "%VSWHERE%" (
  echo [!] Не найден vswhere.exe - установите Visual Studio Build Tools
  exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSPATH=%%i
if not defined VSPATH (
  echo [!] Не найдены C++ Build Tools
  exit /b 1
)

call "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" >nul
cl /nologo /LD /O2 /EHsc /I"%SDK%" truckplay_telemetry.cpp ^
   /link /DEF:truckplay_telemetry.def /OUT:truckplay_telemetry.dll /MACHINE:X64
if errorlevel 1 exit /b 1
del /q *.obj *.exp *.lib 2>nul
echo.
echo Готово: %~dp0truckplay_telemetry.dll
endlocal
