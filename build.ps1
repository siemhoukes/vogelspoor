# Bouw Vogelspoor.exe (een enkel bestand, deelbaar met pa)
# Gebruik:  .\build.ps1   (vanuit de vogelspoor-map)
$ErrorActionPreference = "Stop"

$exiftool = Get-ChildItem -Directory "vendor\exiftool*" | Select-Object -First 1
if (-not $exiftool) { throw "ExifTool niet gevonden in vendor\ - zie README.md" }

& ".venv\Scripts\pyinstaller.exe" --noconfirm --onefile --windowed `
    --name Vogelspoor `
    --add-data "ui;ui" `
    --add-data "$($exiftool.FullName);exiftool" `
    app.py

Write-Host ""
Write-Host "Klaar: dist\Vogelspoor.exe"
