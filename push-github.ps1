$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

$repo = "https://github.com/Aze0920/DouYinSparkFlow.git"
Write-Host "============================================"
Write-Host " DouYinSparkFlow push to GitHub"
Write-Host " $repo"
Write-Host "============================================"
Write-Host ""

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "[ERROR] Git is not installed or not in PATH."
    Write-Host "Install: https://git-scm.com/download/win"
    Read-Host "Press Enter to exit"
    exit 1
}

$versionFile = Join-Path $PSScriptRoot "VERSION"
if (-not (Test-Path $versionFile)) {
    Set-Content -Path $versionFile -Value "1.0.0" -Encoding ascii -NoNewline
}

$old = (Get-Content -Path $versionFile -Raw).Trim()
$parts = $old.Split(".")
if ($parts.Count -lt 3) {
    $parts = @("1", "0", "0")
}
$parts[2] = [string]([int]$parts[2] + 1)
$new = "{0}.{1}.{2}" -f $parts[0], $parts[1], $parts[2]
Set-Content -Path $versionFile -Value $new -Encoding ascii -NoNewline
Write-Host "Version: $old -> $new"

if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
    git init
    git checkout -B main
}

git config user.name "Aze0920"
git config user.email "Aze0920@users.noreply.github.com"

$remotes = @(git remote 2>$null)
if ($remotes -contains "origin") {
    git remote set-url origin $repo
} else {
    git remote add origin $repo
}

git add -A
$pending = git status --porcelain
if ($pending) {
    git commit -m "release v$new"
} else {
    Write-Host "No file changes, still pushing current version."
}

git branch -M main
Write-Host ""
Write-Host "Pushing to GitHub..."
git push -u origin main --force
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] git push failed."
    Write-Host "Login GitHub first, for example: gh auth login"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "[OK] Pushed v$new"
Write-Host "Next: open server UI and click Update"
Read-Host "Press Enter to exit"
