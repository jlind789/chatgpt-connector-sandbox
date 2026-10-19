param(
    [int]$Workers = 0,
    [switch]$SkipCertification
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Resolve-Uv {
    $found = Find-Uv
    if ($found) { return $found }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "uv not found. Trying winget..."
        & winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
        $found = Find-Uv
        if ($found) { return $found }
        Write-Host "winget did not make uv available; trying the official Astral installer..."
    } else {
        Write-Host "winget is unavailable; trying the official Astral installer..."
    }

    $installer = Join-Path $env:TEMP "uv-install.ps1"
    Invoke-WebRequest -UseBasicParsing "https://astral.sh/uv/install.ps1" -OutFile $installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    Remove-Item $installer -Force -ErrorAction SilentlyContinue

    $found = Find-Uv
    if ($found) { return $found }

    throw "uv installation completed but uv.exe could not be located. Close this window, open a new PowerShell window, run 'uv --version', and then run RUN-RESEARCH.cmd again."
}

$Uv = Resolve-Uv
Write-Host "Using uv: $Uv"

$PythonVersion = "3.13.15"
& $Uv python install $PythonVersion

$Venv = Join-Path $Root ".venv-local-research"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    & $Uv venv $Venv --python $PythonVersion
}

& $Uv pip install --python $Python -r (Join-Path $Root "runtime_v0317\requirements-tested.txt")

$Runtime = Join-Path $Root ".runtime_v0317"
if (-not (Test-Path (Join-Path $Runtime "decision_engine"))) {
    Write-Host "Preparing frozen research runtime..."
    New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
    Expand-Archive -Path (Join-Path $Root "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip") -DestinationPath $Runtime -Force
}

$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
$env:VECLIB_MAXIMUM_THREADS = "1"

if (-not $SkipCertification) {
    Write-Host ""
    Write-Host "Running frozen local equivalence certification..."
    & $Python (Join-Path $Root "scripts\certify_local_v0319.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Local equivalence certification failed. v0.3.20 research was NOT started."
    }
}

$argsList = @((Join-Path $Root "scripts\local_research_v0320.py"))
if ($Workers -gt 0) {
    $argsList += @("--workers", $Workers)
}

Write-Host ""
Write-Host "Starting/resuming local v0.3.20 development research..."
& $Python @argsList
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Creating results bundle for review..."
& $Python (Join-Path $Root "scripts\bundle_local_v0320_results.py")
if ($LASTEXITCODE -ne 0) {
    throw "Research completed, but results bundling failed."
}

Write-Host ""
Write-Host "v0.3.20 development research completed and was bundled successfully."
exit 0
