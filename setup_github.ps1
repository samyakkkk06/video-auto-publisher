# One time GitHub setup. Run:  powershell -ExecutionPolicy Bypass -File setup_github.ps1
# Creates the public repo, pushes the code and uploads every secret from .env.
param(
    [string]$RepoName = "video-auto-publisher",
    [switch]$SkipSecrets
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

# ---------------------------------------------------------------- gh cli
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Say "GitHub CLI (gh) is not installed." "Yellow"
    $answer = Read-Host "Install it now with winget? (Y/N)"
    if ($answer -eq "Y" -or $answer -eq "y") {
        winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements
        $env:Path += ";$env:ProgramFiles\GitHub CLI"
        Say "Installed. Restart this script if gh is still not found." "Green"
    }
    Say ""
    Say "Manual alternative:" "Yellow"
    Say "  1. Download gh from https://cli.github.com and install it"
    Say "  2. Run: gh auth login"
    Say "  3. Run this script again"
    exit 1
}

# ---------------------------------------------------------------- login
$auth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Say "Opening the browser for GitHub sign in..." "Yellow"
    gh auth login --hostname github.com --git-protocol https --web
    if ($LASTEXITCODE -ne 0) { Say "GitHub login failed." "Red"; exit 1 }
}

$account = gh api user --jq .login
Say "Signed in as $account" "Green"

# ---------------------------------------------------------------- repo
$repoExists = $false
try { gh repo view "$account/$RepoName" *>$null; $repoExists = $true } catch { }

if (-not $repoExists) {
    Say "Creating public repository $account/$RepoName..." "Yellow"
    gh repo create $RepoName --public --source=. --push --description "Automatic video publisher for Instagram and YouTube"
    if ($LASTEXITCODE -ne 0) { Say "Could not create the repository." "Red"; exit 1 }
} else {
    Say "Repository $account/$RepoName already exists, pushing the code..." "Yellow"
    $remote = (git remote) 2>$null
    if ($remote -notcontains "origin") {
        git remote add origin "https://github.com/$account/$RepoName.git"
    }
    git push -u origin main
}

Say "Repository ready: https://github.com/$account/$RepoName" "Green"

# ---------------------------------------------------------------- secrets
if (-not $SkipSecrets) {
    Say ""
    Say "Uploading secrets from .env..." "Yellow"
    $envPath = Join-Path $root ".env"
    if (-not (Test-Path $envPath)) {
        Say ".env not found, skipping secrets." "Red"
    } else {
        $names = @(
            "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
            "CUSTOM_AI_BASE_URL", "CUSTOM_AI_API_KEY", "CUSTOM_AI_MODEL",
            "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN",
            "INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN",
            "GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
        )
        $map = @{}
        foreach ($line in Get-Content $envPath) {
            $trimmed = $line.Trim()
            if ($trimmed -and -not $trimmed.StartsWith("#") -and $trimmed.Contains("=")) {
                $parts = $trimmed.Split("=", 2)
                $map[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
        # owner and repo are known now
        $map["GITHUB_OWNER"] = $account
        $map["GITHUB_REPO"] = $RepoName

        foreach ($name in $names) {
            if ($map.ContainsKey($name) -and $map[$name]) {
                $map[$name] | gh secret set $name --repo "$account/$RepoName" 2>&1 | Out-Null
                Say "  set $name" "Green"
            }
        }
    }
}

# ---------------------------------------------------------------- workflow
gh workflow enable "Auto Publish" --repo "$account/$RepoName" 2>&1 | Out-Null
Say ""
Say "Workflow enabled. It will run every 10 minutes on GitHub's servers." "Green"
Say ""
Say "Next:"
Say "  1. Put your clips in      $root\clips"
Say "  2. Put your Word file in  $root\descriptions"
Say "  3. Push the clips         powershell -ExecutionPolicy Bypass -File $root\upload_clips.ps1"
Say "  4. Watch the runs         https://github.com/$account/$RepoName/actions"
Say ""
