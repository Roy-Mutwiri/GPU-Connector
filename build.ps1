param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Build step failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Checked { python -m venv .venv }
}
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not $SkipInstall) {
    Checked { & $Python -m pip install -r requirements.txt }
    Checked { & $Python -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128 }
}
Checked { & $Python -m pytest -q }
Checked { & $Python -m ruff check gpu_link tests scripts launcher.py }
Checked { & $Python -m compileall -q gpu_link }
Checked { & $Python -m PyInstaller --clean --noconfirm GPU-Link.spec }
Write-Host 'Built dist\GPU-Link\GPU-Link.exe. Copy the ENTIRE GPU-Link directory to each PC.'
