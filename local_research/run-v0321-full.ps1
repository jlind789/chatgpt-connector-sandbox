param([int]$Workers = 0)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Uv {
    $cmd=Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )) {
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    return $null
}

$Uv=Find-Uv
if (-not $Uv) {
    Write-Host "Installing official Astral uv launcher..."
    $installer=Join-Path $env:TEMP "uv-v0321-full-install.ps1"
    Invoke-WebRequest -UseBasicParsing "https://astral.sh/uv/install.ps1" -OutFile $installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    $Uv=Find-Uv
    if (-not $Uv) { throw "Cannot find installed uv.exe" }
}

$PythonVersion="3.13.15"
& $Uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) { throw "Python setup failed" }

$Venv=Join-Path $Root ".venv-local-research"
$Python=Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) {
    & $Uv venv $Venv --python $PythonVersion
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment setup failed" }
}
& $Uv pip install --python $Python -r (Join-Path $Root "runtime_v0317\requirements-tested.txt")
if ($LASTEXITCODE -ne 0) { throw "Frozen dependencies could not be installed" }

$Runtime=Join-Path $Root ".runtime_v0317"
if (-not (Test-Path (Join-Path $Runtime "decision_engine"))) {
    New-Item -Path $Runtime -ItemType Directory -Force | Out-Null
    Expand-Archive -Path (Join-Path $Root "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip") -DestinationPath $Runtime -Force
}

$env:OMP_NUM_THREADS="1"
$env:OPENBLAS_NUM_THREADS="1"
$env:MKL_NUM_THREADS="1"
$env:NUMEXPR_NUM_THREADS="1"
$env:VECLIB_MAXIMUM_THREADS="1"

Write-Host ""
Write-Host "Running syntax checks and small synthetic contract tests..."
$Files=@(
    "scripts\run_v0321_commit_seat.py",
    "scripts\run_v0321_evaluate_seat.py",
    "scripts\aggregate_v0321_full.py",
    "scripts\run_v0321_full_local.py",
    "scripts\bundle_v0321_full.py"
)
foreach ($File in $Files) {
    & $Python -m py_compile (Join-Path $Root $File)
    if ($LASTEXITCODE -ne 0) { throw "Syntax check failed: $File" }
}
& $Python (Join-Path $Root "scripts\test_v0321_full_contract.py")
if ($LASTEXITCODE -ne 0) { throw "Full-development contract tests failed" }

Write-Host ""
Write-Host "Running frozen development-only equivalence certification..."
& $Python (Join-Path $Root "scripts\certify_local_v0319.py")
if ($LASTEXITCODE -ne 0) { throw "Equivalence certification failed; no full research started" }

Write-Host ""
Write-Host "Starting/resuming v0.3.21 12-seat full DEVELOPMENT rerun..."
$Args=@((Join-Path $Root "scripts\run_v0321_full_local.py"))
if ($Workers -gt 0) { $Args += @("--workers","$Workers") }
& $Python @Args
if ($LASTEXITCODE -ne 0) {
    throw "Run stopped. Preserve its logs/checkpoints; rerun this launcher to resume."
}

Write-Host ""
Write-Host "Full development research complete. Upload:"
Write-Host (Join-Path $Root "historical\local_runs\v0321-full-development\v0321-full-development-results-bundle.zip")
