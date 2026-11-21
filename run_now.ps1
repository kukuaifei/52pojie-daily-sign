#Requires -Version 5
# Idempotent "make sure today's sign ran" trigger.
# Look at today's workflow runs; if none succeeded, fire workflow_dispatch.
# Designed for Windows Task Scheduler. Safe to run many times a day.
# All output ASCII (GitHub logs / cmd consoles mangle Chinese).

param(
    [string]$Repo = "kukuaifei/52pojie-daily-sign",
    [switch]$Force
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $here "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("trigger_" + (Get-Date -Format "yyyyMMdd") + ".log")

function Say($msg) {
    $line = "[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Say "repo=$Repo force=$Force"

# 1) gh must be available and logged in
$gh = (Get-Command gh -ErrorAction SilentlyContinue)
if (-not $gh) { Say "ERROR gh not found in PATH"; exit 3 }
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { Say "ERROR gh not logged in (run: gh auth login)"; exit 3 }

if (-not $Force) {
    # 2) any successful run from today (local date) already? then nothing to do
    try {
        $runs = (gh api "repos/$Repo/actions/runs?per_page=15" | ConvertFrom-Json).workflow_runs
    } catch {
        Say "WARN could not list runs: $($_.Exception.Message)"
        $runs = @()
    }
    $today = (Get-Date).ToString("yyyy-MM-dd")
    $done = $runs | Where-Object {
        $_.conclusion -eq "success" -and
        ([datetime]$_.created_at).ToLocalTime().ToString("yyyy-MM-dd") -eq $today
    } | Select-Object -First 1

    if ($done) {
        Say ("already OK today: run " + $done.id + " at " + ([datetime]$done.created_at).ToLocalTime().ToString("HH:mm"))
        exit 0
    }

    # a run started in the last 20 minutes may still be going - do not pile up
    $recent = $runs | Where-Object {
        $_.status -ne "completed" -and
        ((Get-Date) - [datetime]$_.created_at.ToUniversalTime()).TotalMinutes -lt 20
    } | Select-Object -First 1
    if ($recent) {
        Say ("a run is already in progress: " + $recent.id)
        exit 0
    }
    Say "no successful run today -> dispatching"
} else {
    Say "forced dispatch"
}

# 3) dispatch and confirm it appeared
gh workflow run sign.yml --repo $Repo *> $null
if ($LASTEXITCODE -ne 0) { Say "ERROR dispatch failed (exit $LASTEXITCODE)"; exit 4 }
Say "dispatch accepted"

Start-Sleep -Seconds 8
try {
    $newest = (gh api "repos/$Repo/actions/runs?per_page=1" | ConvertFrom-Json).workflow_runs[0]
    Say ("run " + $newest.id + " status=" + $newest.status + " event=" + $newest.event)
} catch {
    Say "WARN could not confirm run"
}
exit 0
