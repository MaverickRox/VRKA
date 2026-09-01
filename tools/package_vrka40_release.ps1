param(
    [string]$OutputDirectory = "outputs\VRKA-4.0.0-build016-release"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$stageRoot = Join-Path $projectRoot ".release_stage_4_0_0"

Write-Host "Creating release package at $outputRoot..."

if (Test-Path -LiteralPath $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

$names = [ordered]@{
    PortableZip = "VRKA-4.0.0-build016-portable-Windows-x64.zip"
    PortableExe = "VRKA-4.0.0-build016-portable-Windows-x64.exe"
    Installer   = "VRKA-4.0.0-build016-setup-Windows-x64.exe"
    SourceZip   = "VRKA-4.0.0-build016-complete-source.zip"
    Hashes      = "SHA256SUMS.txt"
}

# 1. Prepare portable package
$portableSource = Join-Path $projectRoot "VRKA-portable"
if (-not (Test-Path $portableSource)) {
    throw "Missing VRKA-portable directory: $portableSource"
}

$portableExeSrc = Join-Path $portableSource "VRKA.exe"
$portableExeDst = Join-Path $outputRoot $names.PortableExe
if (Test-Path $portableExeDst) { Remove-Item $portableExeDst -Force }
Copy-Item -LiteralPath $portableExeSrc -Destination $portableExeDst -Force

$portableZipPath = Join-Path $outputRoot $names.PortableZip
if (Test-Path $portableZipPath) { Remove-Item $portableZipPath -Force }
Compress-Archive -Path (Join-Path $portableSource "*") -DestinationPath $portableZipPath -CompressionLevel Optimal

# 2. Compile Inno Setup installer
$isccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$isccPath = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($isccPath) {
    Write-Host "Compiling Inno Setup installer with $isccPath..."
    $issFile = Join-Path $projectRoot "VRKA-4.0.iss"
    & $isccPath $issFile | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ISCC compilation failed with exit code $LASTEXITCODE" }
} else {
    Write-Warning "Inno Setup compiler (ISCC.exe) not found. Skipping installer exe creation."
}

# 3. Prepare complete source package (strict source-only filter, < 10 MB)
$sourceStage = Join-Path $stageRoot "VRKA-4.0.0-source"
New-Item -ItemType Directory -Path $sourceStage -Force | Out-Null

$ignoreDirs = @(
    '.git', '.venv', '.venv-macos', 'build', 'dist', 'installer_output',
    'verification', 'performance', '.codex_patches', '__pycache__', '.test_tmp',
    'dmg_staging', 'outputs', 'target', 'VRKA-portable', 'ffmpeg_bin', 'source_archive',
    '.release_stage_4_0_0', 'lab', '.pytest_cache'
)
$ignoreExtensions = @('.pyc', '.pyo', '.zip', '.exe', '.dll', '.pdb', '.lib', '.exp', '.har', '.dump', '.tar', '.gz')

Get-ChildItem -Path $projectRoot -Recurse -File | ForEach-Object {
    $file = $_
    $relPath = $file.FullName.Substring($projectRoot.Length).TrimStart('\', '/')
    $parts = $relPath.Split([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $skip = $false
    if ($parts.Length -gt 1) {
        foreach ($part in $parts[0..($parts.Length - 2)]) {
            if ($ignoreDirs -contains $part) {
                $skip = $true
                break
            }
        }
    }
    if (-not $skip -and ($ignoreExtensions -notcontains $file.Extension.ToLowerInvariant())) {
        $destFile = Join-Path $sourceStage $relPath
        $destDir = [System.IO.Path]::GetDirectoryName($destFile)
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item -LiteralPath $file.FullName -Destination $destFile -Force
    }
}

$sourceZipPath = Join-Path $outputRoot $names.SourceZip
if (Test-Path $sourceZipPath) { Remove-Item $sourceZipPath -Force }
Compress-Archive -Path (Join-Path $sourceStage "*") -DestinationPath $sourceZipPath -CompressionLevel Optimal

# 4. Generate SHA256 checksums
$hashTargets = @(
    $names.PortableZip,
    $names.PortableExe,
    $names.SourceZip
)
if (Test-Path (Join-Path $outputRoot $names.Installer)) {
    $hashTargets = @(
        $names.PortableZip,
        $names.PortableExe,
        $names.Installer,
        $names.SourceZip
    )
}
function Get-Sha256Hex($filePath) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($filePath)
    try {
        $bytes = $sha.ComputeHash($stream)
        return -join ($bytes | ForEach-Object { $_.ToString("x2") })
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

$hashLines = foreach ($name in $hashTargets) {
    $filePath = Join-Path $outputRoot $name
    $hash = Get-Sha256Hex $filePath
    "$hash *$name"
}
$hashPath = Join-Path $outputRoot $names.Hashes
$hashLines | Set-Content -LiteralPath $hashPath -Encoding ascii

# 5. Verify checksums
foreach ($line in Get-Content -LiteralPath $hashPath) {
    if ($line -match '^([0-9a-f]{64}) \*(.+)$') {
        $actual = Get-Sha256Hex (Join-Path $outputRoot $Matches[2])
        if ($actual -ne $Matches[1]) { throw "Checksum verification failed for $($Matches[2])" }
    }
}

Remove-Item -LiteralPath $stageRoot -Recurse -Force

Write-Host "======================================================"
Write-Host "RELEASE PACKAGE GENERATED SUCCESSFULLY"
Write-Host "======================================================"
Get-ChildItem -LiteralPath $outputRoot | Select-Object Name, Length, LastWriteTime
