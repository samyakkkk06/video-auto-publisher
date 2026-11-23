# Run the publisher. Examples:
#   powershell -ExecutionPolicy Bypass -File run_local.ps1
#   powershell -ExecutionPolicy Bypass -File run_local.ps1 -Verify
#   powershell -ExecutionPolicy Bypass -File run_local.ps1 -DryRun
param(
    [switch]$Verify,
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

if ($Verify) {
    & $python -m scripts.daily_run --verify
} elseif ($DryRun) {
    & $python -m scripts.daily_run --dry-run
} elseif ($Force) {
    & $python -m scripts.daily_run --force
} else {
    & $python -m scripts.daily_run
}
