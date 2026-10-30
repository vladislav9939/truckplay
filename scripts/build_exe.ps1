# Сборка TruckPlay.exe + zip-релиза
#   powershell -ExecutionPolicy Bypass -File build\build_exe.ps1 -Version 2.1.0

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "== 1/3 плагин телеметрии ==" -ForegroundColor Cyan
if (Test-Path "plugin\truckplay_telemetry.dll") {
    Write-Host "   DLL уже есть, пропускаю (удалите файл, чтобы пересобрать)"
} else {
    & "plugin\build.bat"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось собрать плагин" }
}

Write-Host "== 2/3 зависимости ==" -ForegroundColor Cyan
python -m pip install --quiet --upgrade pywebview pyinstaller

Write-Host "== 3/3 exe ==" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --onefile --windowed --name TruckPlay `
    --add-data "src\app.html;." `
    --add-data "plugin\truckplay_telemetry.dll;plugin" `
    --collect-all webview src\truckplay.py

Copy-Item *.bat dist\ -Force
Copy-Item config.example.json dist\ -Force
Copy-Item README.md, LICENSE, CHANGELOG.md dist\ -Force
New-Item -ItemType Directory -Force -Path "dist\docs" | Out-Null
Copy-Item "docs\*" "dist\docs\" -Recurse -Force

Remove-Item -Recurse -Force build\__pycache__ -ErrorAction SilentlyContinue
Remove-Item TruckPlay.spec -ErrorAction SilentlyContinue

if ($Version) {
    $zip = "TruckPlay-$Version.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path "dist\*" -DestinationPath $zip
    Write-Host "Готово: $zip" -ForegroundColor Green
} else {
    Write-Host "Готово: dist\TruckPlay.exe" -ForegroundColor Green
}
