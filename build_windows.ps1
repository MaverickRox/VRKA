param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

Write-Host "[1/4] Setting up Python environment..."
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe" -PathType Leaf)) {
    $SystemPython = (Get-Command python -ErrorAction Stop).Source
    & $SystemPython -m venv .venv
}
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

Write-Host "[2/4] Installing dependencies..."
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller

Write-Host "[3/4] Building VRKA.exe with PyInstaller..."
& $Python -m PyInstaller --noconfirm --clean VRKA-Windows.spec
if (-not (Test-Path -LiteralPath "dist\VRKA.exe" -PathType Leaf)) {
    throw "PyInstaller finished without producing dist\VRKA.exe."
}
Write-Host "Standalone binary generated at dist\VRKA.exe"

if ($SkipInstaller) {
    Write-Host "[4/4] Installer skipped as requested."
    exit 0
}

Write-Host "[4/4] Building Windows installer..."
$InnoCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$InnoCompiler = $InnoCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $InnoCompiler) {
    Write-Warning "Inno Setup 6 compiler (ISCC.exe) not found. Portable binary is ready in dist\VRKA.exe."
    exit 0
}

# Stage VRKA-portable directory for Inno Setup
New-Item -ItemType Directory -Path "VRKA-portable" -Force | Out-Null
Copy-Item -LiteralPath "dist\VRKA.exe" -Destination "VRKA-portable\VRKA.exe" -Force
Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination "VRKA-portable\THIRD_PARTY_NOTICES.md" -Force

& $InnoCompiler VRKA-4.0.iss
Write-Host "Setup package created in outputs\VRKA-4.0.0-build016-release"
