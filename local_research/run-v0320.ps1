param(
    [int]$Workers = 0,
    [switch]$SkipCertification
)

$ErrorActionPreference = "Stop"
$Bootstrap = Join-Path $PSScriptRoot "bootstrap-and-run-v0320.ps1"

$params = @{}
if ($Workers -gt 0) { $params["Workers"] = $Workers }
if ($SkipCertification) { $params["SkipCertification"] = $true }

& $Bootstrap @params
exit $LASTEXITCODE
