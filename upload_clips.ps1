# Upload your clips to the GitHub Release and update the manifest.
# Run this whenever you add new clips:  powershell -ExecutionPolicy Bypass -File upload_clips.ps1
param(
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$args = @("-m", "scripts.upload_clips")
if ($NoPush) { $args += "--no-push" }

& $python @args
