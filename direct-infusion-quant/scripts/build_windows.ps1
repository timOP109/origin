[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot "..\.venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "packaging\DirectInfusionQuant.spec"
$BuildPath = Join-Path $ProjectRoot "build\pyinstaller"
$DistPath = Join-Path $ProjectRoot "dist"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python 3.11 environment was not found at $Python"
}

& $Python -m PyInstaller --version
if (-not $SkipTests) {
    & $Python -m ruff check $ProjectRoot
    & $Python -m pytest $ProjectRoot
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $BuildPath `
    --distpath $DistPath `
    $Spec

$Executable = Join-Path $DistPath "DirectInfusionQuant\DirectInfusionQuant.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Expected one-folder executable was not produced: $Executable"
}

Write-Host "One-folder build created: $Executable"
Write-Host "One-file mode is intentionally not enabled."
