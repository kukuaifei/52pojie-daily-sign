#Requires -Version 5
# Configure 52pojie credentials + optional PAT so the workflow can re-login and write the new cookie back.
# All output is ASCII to avoid console encoding mess. Secrets are piped via stdin (never in argv / history).

param(
    [string]$Repo = "kukuaifei/52pojie-daily-sign",
    [string]$User = "",
    [string]$Pass = "",
    [string]$Pat  = ""
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

function Write-Step($n, $t) { Write-Host ""; Write-Host "[$n] $t" -ForegroundColor Cyan }

Write-Host "=== 52pojie auto-relogin setup ===" -ForegroundColor Green
Write-Host "repo: $Repo"

Write-Step 1 "Checking gh auth"
gh auth status 2>&1 | Select-Object -First 3 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { Write-Host "gh is not logged in. Run: gh auth login" -ForegroundColor Red; exit 1 }

Write-Step 2 "52pojie username / password"
if (-not $User) { $User = (Read-Host "  username").Trim() }
if (-not $Pass) {
    $sec = Read-Host "  password" -AsSecureString
    $Pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
if (-not $User -or -not $Pass) { Write-Host "  username/password empty, aborted." -ForegroundColor Red; exit 1 }

$User | gh secret set PJ52_USER --repo $Repo
if ($LASTEXITCODE -eq 0) { Write-Host "  OK  PJ52_USER" -ForegroundColor Green } else { exit 1 }
$Pass | gh secret set PJ52_PASS --repo $Repo
if ($LASTEXITCODE -eq 0) { Write-Host "  OK  PJ52_PASS" -ForegroundColor Green } else { exit 1 }

Write-Step 3 "PAT for writing the refreshed cookie back (optional)"
Write-Host "  Needs a fine-grained token with 'Secrets: Read and write' on $Repo."
Write-Host "  Leave empty to skip: cookie then refreshes in-place for that run only,"
Write-Host "  and you must update PJ52_COOKIE by hand once it expires."
if (-not $Pat) { $Pat = (Read-Host "  GH_PAT (Enter to skip)").Trim() }
if ($Pat) {
    $Pat | gh secret set GH_PAT --repo $Repo
    if ($LASTEXITCODE -eq 0) { Write-Host "  OK  GH_PAT (auto write-back enabled)" -ForegroundColor Green }
} else {
    Write-Host "  skipped GH_PAT" -ForegroundColor Yellow
}

Write-Step 4 "Secret list"
gh secret list --repo $Repo

Write-Host ""
Write-Host "DONE. Next: verify auto-login works from YOUR network first (Turnstile is" -ForegroundColor Green
Write-Host "much easier on a home IP than on Azure runners):" -ForegroundColor Green
Write-Host ""
Write-Host '  $env:PJ52_USER = "<your username>"'
Write-Host '  $env:PJ52_PASS = "<your password>"'
Write-Host '  python test_login.py'
Write-Host ""
Write-Host "That opens a visible browser. If it prints a COOKIE block, this path works"
Write-Host "and the scheduled run will self-heal. If it fails, read the three limits"
Write-Host "listed in README (Turnstile / security question / image captcha)."
Write-Host ""
Write-Host "Schedule is now 09:20 Asia/Shanghai (UTC 01:20). GitHub cron can lag a few"
Write-Host "minutes under load - that is normal, the sign-in window is all day."
