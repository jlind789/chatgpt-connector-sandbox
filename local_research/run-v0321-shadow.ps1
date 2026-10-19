param(
    [string]$SourceBundle = "",
    [int]$Workers = 0
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$Uv = Find-Uv
if (-not $Uv) {
    Write-Host "Installing uv with the official Astral installer..."
    $Installer = Join-Path $env:TEMP "uv-v0321-installer.ps1"
    Invoke-WebRequest -UseBasicParsing "https://astral.sh/uv/install.ps1" -OutFile $Installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
    Remove-Item $Installer -Force -ErrorAction SilentlyContinue
    $Uv = Find-Uv
    if (-not $Uv) { throw "Cannot find uv.exe after installation." }
}

$PythonVersion = "3.13.15"
& $Uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) { throw "Python installation failed" }

$Venv = Join-Path $Root ".venv-local-research"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    & $Uv venv $Venv --python $PythonVersion
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment setup failed" }
}

& $Uv pip install --python $Python -r (Join-Path $Root "runtime_v0317\requirements-tested.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }

$Runtime = Join-Path $Root ".runtime_v0317"
if (-not (Test-Path (Join-Path $Runtime "decision_engine"))) {
    New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
    Expand-Archive -Path (Join-Path $Root "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip") -DestinationPath $Runtime -Force
}

# Never call Test-Path with an empty string: PowerShell treats that as a parameter error.
# The previous launcher could fail here before ever offering the intended prompt.
function Test-BundlePath([string]$Candidate) {
    return (-not [string]::IsNullOrWhiteSpace($Candidate)) -and
        (Test-Path -LiteralPath $Candidate -PathType Leaf)
}

if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
    $Candidates = @(
        (Join-Path $Root "v0320-development-results-bundle.zip"),
        (Join-Path $Root "historical\local_runs\v0320-development\v0320-development-results-bundle.zip"),
        (Join-Path (Join-Path $env:USERPROFILE "Downloads") "v0320-development-results-bundle.zip")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-BundlePath $Candidate) {
            $SourceBundle = $Candidate
            break
        }
    }
}
while (-not (Test-BundlePath $SourceBundle)) {
    Write-Host ""
    Write-Host "The original v0.3.20 research ZIP has not yet been located."
    Write-Host "Look for v0320-development-results-bundle.zip in the previous extracted repo's"
    Write-Host "historical\local_runs\v0320-development folder."
    $SourceBundle = Read-Host "Paste the full ZIP file path (or type Q to stop)"
    if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
        throw "No source bundle was provided. Research was not started."
    }
    $SourceBundle = $SourceBundle.Trim().Trim('"').Trim("'")
    if ($SourceBundle -ieq "Q") { throw "Cancelled at the source-bundle prompt." }
    if (-not (Test-BundlePath $SourceBundle)) {
        Write-Host "That file does not exist at the given path. Please try again." -ForegroundColor Yellow
    }
}
$SourceBundle = (Resolve-Path -LiteralPath $SourceBundle).Path

$env:OMP_NUM_THREADS="1"
$env:OPENBLAS_NUM_THREADS="1"
$env:MKL_NUM_THREADS="1"
$env:NUMEXPR_NUM_THREADS="1"
$env:VECLIB_MAXIMUM_THREADS="1"

Write-Host ""
Write-Host "Certifying unchanged development-only local computations..."
& $Python (Join-Path $Root "scripts\certify_local_v0319.py")
if ($LASTEXITCODE -ne 0) { throw "Certification failed: shadow research did not start." }

Write-Host ""
Write-Host "Replaying 53 original unconfirmed development decisions through 1024 rollouts..."
$RunDir = Join-Path $Root "historical\local_runs\v0321-shadow-1024"
$Args = @(
    (Join-Path $Root "scripts\run_v0321_shadow_local.py"),
    "--source-bundle", $SourceBundle,
    "--run-dir", $RunDir,
    "--max-budget", "1024"
)
if ($Workers -gt 0) { $Args += @("--workers", "$Workers") }
& $Python @Args
if ($LASTEXITCODE -ne 0) { throw "Shadow replay failed. Preserve logs and rerun to resume." }

$Output = Join-Path $RunDir "v0321-shadow-diagnostic-results.zip"
$Files = @(
    (Join-Path $RunDir "manifest.json"),
    (Join-Path $RunDir "status.json"),
    (Join-Path $RunDir "COMPLETE.json"),
    (Join-Path $RunDir "summary.json"),
    (Join-Path $RunDir "decision_comparison.csv")
)
$Files += @(Get-ChildItem (Join-Path $RunDir "seat_results") -Filter "*.json" | ForEach-Object { $_.FullName })
$Files += @(Get-ChildItem (Join-Path $RunDir "logs") -Filter "*.log" | ForEach-Object { $_.FullName })
if (Test-Path $Output) { Remove-Item $Output -Force }
Compress-Archive -Path $Files -DestinationPath $Output -CompressionLevel Optimal
Write-Host ""
Write-Host "Complete. Upload this diagnostic ZIP into the Decision Engine development chat:"
Write-Host $Output
