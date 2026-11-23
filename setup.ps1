# One time setup. Run:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host ""
Write-Host "=== Video Auto Publisher setup ===" -ForegroundColor Cyan
Write-Host ""

# 1. folders
foreach ($dir in @("clips", "descriptions", "output", "state")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
        Write-Host "created folder: $dir"
    }
}

# 2. python env
if (-not (Test-Path ".venv")) {
    Write-Host "creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
}
$python = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "installing python packages..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt --quiet
Write-Host "packages installed" -ForegroundColor Green

# 3. ffmpeg
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Host "ffmpeg found: $($ffmpeg.Source)" -ForegroundColor Green
} else {
    Write-Host "ffmpeg NOT found. Install it from https://ffmpeg.org/download.html and add it to PATH." -ForegroundColor Yellow
}

# 4. env file
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "created .env from .env.example - fill in your keys" -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, left untouched" -ForegroundColor Green
}

# 5. state file (no BOM, JSON must parse cleanly)
if (-not (Test-Path "state\published.json")) {
    [System.IO.File]::WriteAllText((Join-Path $root "state\published.json"), "{}", (New-Object System.Text.UTF8Encoding $false))
}

Write-Host ""
Write-Host "=== Setup finished ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Put your clips in   : $root\clips"
Write-Host "  2. Put your Word file in: $root\descriptions"
Write-Host "  3. Fill in your keys   : $root\.env"
Write-Host "  4. Check the setup     : powershell -ExecutionPolicy Bypass -File run_local.ps1 -Verify"
Write-Host ""
