<#
Configure Telegram notification for the 52pojie daily-sign workflow.

Usage:
  .\setup_telegram.ps1
  .\setup_telegram.ps1 -Token "123456789:AAH..." -ChatId "123456789"
  .\setup_telegram.ps1 -Token "..." -ChatId "..." -Repo "owner/repo"

Get token:  Telegram -> @BotFather -> /newbot
Get chat id: send /start to your bot first, then Telegram -> @userinfobot
Note: local api.telegram.org may be blocked, so verification happens on GitHub
Actions instead of on this machine.
#>
param(
    [string]$Token,
    [string]$ChatId,
    [string]$Repo = "kukuaifei/52pojie-daily-sign",
    [switch]$NoTest
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Token)  { $Token  = (Read-Host "Paste bot token (looks like 123456789:AAH...)").Trim() }
if (-not $ChatId) { $ChatId = (Read-Host "Paste chat id (digits, can be 111,222)").Trim() }

# trim quotes / trailing junk from copy-paste
$Token  = $Token.Trim().Trim('"').Trim("'")
$ChatId = $ChatId.Trim().Trim('"').Trim("'")

if ($Token -notmatch ':' -or $Token.Length -lt 20) {
    Write-Host "ERROR: token format looks wrong (need 'digits:letters', length >= 20)." -ForegroundColor Red
    Write-Host "       got length $($Token.Length); expected e.g. 123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" -ForegroundColor Yellow
    exit 1
}
if ($ChatId -notmatch '^-?\d+(\s*[,;]\s*-?\d+)*$') {
    Write-Host "ERROR: chat id must be digits only (negative for groups), e.g. 123456789 or -1001234567890" -ForegroundColor Red
    Write-Host "       got: $ChatId" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[1/3] Writing secrets to $Repo ..." -ForegroundColor Cyan
$Token  | gh secret set TG_BOT_TOKEN --repo $Repo
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: gh secret set TG_BOT_TOKEN" -ForegroundColor Red; exit 1 }
$ChatId | gh secret set TG_CHAT_ID --repo $Repo
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: gh secret set TG_CHAT_ID" -ForegroundColor Red; exit 1 }
gh secret list --repo $Repo
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: could not list secrets (token may lack permission)" -ForegroundColor Yellow }

if ($NoTest) {
    Write-Host ""
    Write-Host "Secrets written. Skipped test (-NoTest)." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "[2/3] Triggering a notify-only test run (does not touch 52pojie) ..." -ForegroundColor Cyan
gh workflow run sign.yml --repo $Repo -f test_notify=true
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: gh workflow run" -ForegroundColor Red; exit 1 }

Start-Sleep -Seconds 8
$runId = (gh run list --repo $Repo --limit 1 --json databaseId --jq '.[0].databaseId' | Out-String).Trim()
Write-Host "run id: $runId"
Write-Host "https://github.com/$Repo/actions/runs/$runId" -ForegroundColor Gray

Write-Host ""
Write-Host "[3/3] Waiting for the run to finish ..." -ForegroundColor Cyan
for ($i = 0; $i -lt 24; $i++) {
    $j = gh run view $runId --repo $Repo --json status,conclusion 2>$null | ConvertFrom-Json
    if ($j.status -eq 'completed') { break }
    Start-Sleep -Seconds 15
}
$final = gh run view $runId --repo $Repo --json status,conclusion 2>$null | ConvertFrom-Json
Write-Host ("status={0} conclusion={1}" -f $final.status, $final.conclusion)

Write-Host ""
Write-Host "--- telegram related log lines ---" -ForegroundColor Cyan
gh run view $runId --repo $Repo --log 2>&1 | Select-String "Telegram|TG_|通知" | ForEach-Object { $_.Line }

Write-Host ""
if ($final.conclusion -eq 'success') {
    Write-Host "DONE. Check your phone: you should have received a test message." -ForegroundColor Green
    Write-Host "If you did NOT receive it, check the log above for 401 / chat not found." -ForegroundColor Yellow
    Write-Host "Reminder: you must send /start to your own bot once, otherwise chat not found." -ForegroundColor Yellow
} else {
    Write-Host "Run failed. Full log:  gh run view $runId --repo $Repo --log" -ForegroundColor Red
}
