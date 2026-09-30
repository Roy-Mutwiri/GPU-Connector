$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path 'dist\GPU-Link\GPU-Link.exe')) { throw 'Run build.ps1 first.' }
& $Python scripts/prepare_setup.py
if ($LASTEXITCODE -ne 0) { throw 'Preparing installer failed' }
& $Python -m pytest -q tests/test_installer.py
if ($LASTEXITCODE -ne 0) { throw 'Installer tests failed' }
& $Python -m PyInstaller --clean --noconfirm --onefile --windowed --name GPU-Link-Setup --distpath release --workpath build/setup --add-data 'release/core.zip;.' --add-data 'release/setup-manifest.json;.' --exclude-module torch --exclude-module PySide6 scripts/windows_setup.py
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
Write-Host 'Built release\GPU-Link-Setup.exe. Internet is needed only during setup.'
