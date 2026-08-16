# push.ps1 - run this from E:\agent tread\agent-trade only
# Usage: cd "E:\agent tread\agent-trade"  then  .\push.ps1

$ErrorActionPreference = "Stop"

function Check-Step {
    param([string]$Description)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Description (exit code $LASTEXITCODE)" -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
}

Write-Host "=== Checking folder ===" -ForegroundColor Cyan
$here = Get-Location
Write-Host "Current location: $here"

if ($here.Path -notlike "*agent-trade") {
    Write-Host "ERROR: not inside the agent-trade folder" -ForegroundColor Red
    Write-Host "Run: cd `"E:\agent tread\agent-trade`"  then try again" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "`n=== git init ===" -ForegroundColor Cyan
if (Test-Path ".git") {
    Write-Host "Already a git repo, skipping init"
} else {
    git init
    Check-Step "git init"
}

Write-Host "`n=== Git identity (local to this repo only) ===" -ForegroundColor Cyan
$curEmail = git config user.email
if (-not $curEmail) {
    git config user.email "naxyz23@users.noreply.github.com"
    Write-Host "Set user.email"
} else {
    Write-Host "user.email already set: $curEmail"
}
$curName = git config user.name
if (-not $curName) {
    git config user.name "Naxyz23"
    Write-Host "Set user.name"
} else {
    Write-Host "user.name already set: $curName"
}

Write-Host "`n=== git add ===" -ForegroundColor Cyan
git add -A
Check-Step "git add"
git status --short

Write-Host "`n=== git commit ===" -ForegroundColor Cyan
$changes = git status --porcelain
if ($changes) {
    git commit -m "Trading signal agent v0.5"
    Check-Step "git commit"
} else {
    Write-Host "Nothing new to commit (maybe already committed earlier)"
}

Write-Host "`n=== Set branch name ===" -ForegroundColor Cyan
git branch -M main
Check-Step "git branch -M main"

Write-Host "`n=== Set remote ===" -ForegroundColor Cyan
$remotes = git remote
if ($remotes -contains "origin") {
    Write-Host "origin already set, using existing one"
} else {
    git remote add origin https://github.com/Naxyz23/agent-trade.git
    Check-Step "git remote add"
}

Write-Host "`n=== Sync with GitHub (pull any changes made on the website) ===" -ForegroundColor Cyan
git fetch origin main
Check-Step "git fetch"
git pull --rebase --autostash origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED: git pull --rebase (exit code $LASTEXITCODE)" -ForegroundColor Red
    Write-Host "This usually means the SAME lines were edited both locally and on GitHub." -ForegroundColor Red
    Write-Host "Do not guess - stop here and tell Claude what happened." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "`n=== Push to GitHub ===" -ForegroundColor Cyan
Write-Host "A browser or credential prompt may pop up - log in if asked."
git push -u origin main
Check-Step "git push"

Write-Host "`n=== Done - push succeeded ===" -ForegroundColor Green
Write-Host "Check the result at https://github.com/Naxyz23/agent-trade"
Read-Host "Press Enter to close this window"
