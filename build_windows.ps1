param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

Write-Host "[1/6] Checking required bundled media tools..."
foreach ($RequiredFile in @("ffmpeg_bin\ffmpeg.exe", "ffmpeg_bin\ffprobe.exe")) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Missing $RequiredFile. Restore it from the supplied project archive before building."
    }
}

Write-Host "[2/6] Creating the isolated Python environment..."
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe" -PathType Leaf)) {
    $SystemPython = (Get-Command python -ErrorAction Stop).Source
    & $SystemPython -m venv .venv
}
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

Write-Host "[3/6] Installing the pinned build tools..."
& $Python -m pip install -r requirements.txt

Write-Host "[4/6] Running the regression checks..."
& $Python -m py_compile vrka_downloader.py test_vrka.py test_vrka_20.py test_release_rework.py tools\generate_brand_assets.py
& $Python -m unittest -v

$Deno = Get-Command deno -ErrorAction SilentlyContinue
if ($Deno) {
    New-Item -ItemType Directory -Path "deno_bin" -Force | Out-Null
    Copy-Item -LiteralPath $Deno.Source -Destination "deno_bin\deno.exe" -Force
    Write-Host "Bundled Deno from $($Deno.Source)"
} else {
    Write-Warning "Deno was not found. The app will build, but some future YouTube challenges may need Deno."
}

Write-Host "[5/6] Building VRKA.exe..."
& $Python -m PyInstaller --noconfirm --clean VRKA-Windows.spec
if (-not (Test-Path -LiteralPath "dist\VRKA.exe" -PathType Leaf)) {
    throw "PyInstaller finished without producing dist\VRKA.exe."
}

if ($SkipInstaller) {
    Write-Host "[6/6] Installer skipped. Portable app: dist\VRKA.exe"
    exit 0
}

Write-Host "[6/6] Building the Windows installer..."
$InnoCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$InnoCompiler = $InnoCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $InnoCompiler) {
    throw "Inno Setup 6 is not installed. Install it, then run this script again. The tested VRKA.exe is already in dist\."
}
& $InnoCompiler VRKA.iss

Write-Host "Done. Installer: installer_output\VRKA-3.5.0-setup-Windows-x64.exe"
