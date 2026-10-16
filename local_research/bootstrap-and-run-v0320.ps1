param(
    [int]$Workers = 0,
    [switch]$SkipCertification
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $candidate) { return $candidate }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "uv not found. Installing with winget..."
        & winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
        if (Test-Path $candidate) { return $candidate }
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }

    throw "uv is required but could not be installed automatically. Install uv from https://docs.astral.sh/uv/ and run this script again."
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
exit $LASTEXITCODE
