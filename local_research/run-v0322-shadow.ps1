param(
    [string]$SourceBundle = "",
    [int]$Workers = 0
)

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
    Write-Host "Installing uv from the official Astral installer..."
    $installer=Join-Path $env:TEMP "uv-v0322-installer.ps1"
    Invoke-WebRequest -UseBasicParsing "https://astral.sh/uv/install.ps1" -OutFile $installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    $Uv=Find-Uv
    if (-not $Uv) { throw "uv.exe could not be located" }
}
$PythonVersion="3.13.15"
& $Uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) { throw "Python setup failed" }
$Venv=Join-Path $Root ".venv-local-research"
$Python=Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    & $Uv venv $Venv --python $PythonVersion
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment setup failed" }
}
& $Uv pip install --python $Python -r (Join-Path $Root "runtime_v0317\requirements-tested.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency setup failed" }
$Runtime=Join-Path $Root ".runtime_v0317"
if (-not (Test-Path (Join-Path $Runtime "decision_engine"))) {
    New-Item -Path $Runtime -ItemType Directory -Force | Out-Null
    Expand-Archive -Path (Join-Path $Root "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip") -DestinationPath $Runtime -Force
}

function Is-ValidSource([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $z=[System.IO.Compression.ZipFile]::OpenRead($Candidate)
        try {
            $names=@{}
            foreach ($entry in $z.Entries) { $names[$entry.FullName.Replace('\','/')]=$true }
            foreach ($required in @("manifest.json","BUNDLE-CHECKSUMS.json",
                                    "COMPLETE.json","results/summary.json",
                                    "results/gate_report.json")) {
                if (-not $names.ContainsKey($required)) { return $false }
            }
            foreach ($season in @("2022-23","2023-24")) {
                foreach ($seat in 1..6) {
                    $name=('seat_results/{0}_seat{1}.json' -f $season,$seat)
                    if (-not $names.ContainsKey($name)) { return $false }
                }
            }
            return $true
        } finally {
            $z.Dispose()
        }
    } catch { return $false }
}
if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
    foreach ($candidate in @(
        (Join-Path $Root "v0321-full-development-results-bundle.zip"),
        (Join-Path $Root "historical\local_runs\v0321-full-development\v0321-full-development-results-bundle.zip"),
        (Join-Path (Join-Path $env:USERPROFILE "Downloads") "v0321-full-development-results-bundle.zip")
    )) {
        if (Is-ValidSource $candidate) { $SourceBundle=$candidate; break }
    }
}
while (-not (Is-ValidSource $SourceBundle)) {
    Write-Host ""
    Write-Host "Select your original v0.3.21 FULL DEVELOPMENT RESULTS bundle (not the repo ZIP):"
    Write-Host "v0321-full-development-results-bundle.zip"
    $SourceBundle=Read-Host "Paste its full Windows file path (or Q to cancel)"
    if ([string]::IsNullOrWhiteSpace($SourceBundle)) { throw "No valid source bundle supplied" }
    $SourceBundle=$SourceBundle.Trim().Trim('"').Trim("'")
    if ($SourceBundle -ieq "Q") { throw "Cancelled" }
    if (-not (Is-ValidSource $SourceBundle)) {
        Write-Host "That ZIP is not the expected research results bundle." -ForegroundColor Yellow
    }
}
$SourceBundle=(Resolve-Path -LiteralPath $SourceBundle).Path
Write-Host "Located completed v0.3.21 results ZIP: $SourceBundle"

$env:PYTHONHASHSEED="0"
$env:OMP_NUM_THREADS="1"
$env:OPENBLAS_NUM_THREADS="1"
$env:MKL_NUM_THREADS="1"
$env:NUMEXPR_NUM_THREADS="1"
$env:VECLIB_MAXIMUM_THREADS="1"

Write-Host "Running syntax and synthetic safety checks..."
foreach ($name in @("diagnose_v0322_shadow_2048.py",
                   "aggregate_v0322_shadow.py",
                   "run_v0322_shadow_local.py",
                   "test_v0322_shadow_contract.py")) {
    & $Python -m py_compile (Join-Path $Root "scripts\$name")
    if ($LASTEXITCODE -ne 0) { throw "Syntax error: $name" }
}
& $Python (Join-Path $Root "scripts\test_v0322_shadow_contract.py")
if ($LASTEXITCODE -ne 0) { throw "Local contract tests failed" }

Write-Host "Running development-only equivalence certification..."
& $Python (Join-Path $Root "scripts\certify_local_v0319.py")
if ($LASTEXITCODE -ne 0) { throw "Frozen certification failed; no shadow research started" }

Write-Host "Replaying 35 original unconfirmed v0.3.21 decisions through 2048 rollouts..."
$Args=@((Join-Path $Root "scripts\run_v0322_shadow_local.py"),"--source-bundle",$SourceBundle)
if ($Workers -gt 0) { $Args+=@("--workers","$Workers") }
& $Python @Args
if ($LASTEXITCODE -ne 0) {
    throw "Research stopped. Completed decision checkpoints are preserved; rerun to resume."
}

Write-Host ""
Write-Host "2048 development-only shadow study complete. Upload:"
Write-Host (Join-Path $Root "historical\local_runs\v0322-shadow-2048\v0322-shadow-2048-results-bundle.zip")
