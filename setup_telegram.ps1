<#
配置 52pojie 签到任务的 Telegram 通知（本机一次性操作）

用法：
  .\setup_telegram.ps1
  .\setup_telegram.ps1 -Token "123456:AA..." -ChatId "123456789"

Token 从 @BotFather 拿（/newbot）；ChatId 用 @userinfobot 查，或给 bot 发条 /start 后看 getUpdates。
#>
param(
    [string]$Token,
    [string]$ChatId,
    [string]$Repo = "kukuaifei/52pojie-daily-sign"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

if (-not $Token)  { $Token  = (Read-Host "粘贴 bot token（形如 123456789:AAH...）").Trim() }
if (-not $ChatId) { $ChatId = (Read-Host "粘贴 chat id（形如 123456789，可多个用逗号分隔）").Trim() }

if ($Token -notmatch '^\d+:[\w-]{30,}$') {
    Write-Host "token 格式看着不对，应该形如 123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" -ForegroundColor Red
    exit 1
}

Write-Host "`n[1/3] 验证 token ..." -ForegroundColor Cyan
$probe = @"
import json, os, urllib.request, ssl
t = os.environ['TG_T']
ctx = ssl._create_unverified_context() if os.environ.get('TG_K') == '1' else None
kw = {'context': ctx} if ctx else {}
try:
    with urllib.request.urlopen('https://api.telegram.org/bot%s/getMe' % t, timeout=20, **kw) as r:
        print(r.read().decode())
except Exception as e:
    try: print(e.read().decode())
    except Exception: print('ERR: %s' % str(e)[:200])
"@
$tmp = Join-Path $env:TEMP "tg_probe.py"
Set-Content -Path $tmp -Value $probe -Encoding UTF8
$env:TG_T = $Token
$env:TG_K = "1"    # 本机若有 MITM 代理，跳过证书校验（只用于这一步探测）
$out = & python $tmp 2>&1 | Out-String
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
Write-Host $out.Trim()
if ($out -notmatch '"ok":true') {
    Write-Host "`n验证失败：token 不对，或本机访问不了 api.telegram.org（需要代理）。" -ForegroundColor Red
    Write-Host "如果只是证书问题，脚本已自动跳过校验；如果超时，请先开代理再重试。" -ForegroundColor Yellow
    exit 1
}
$botName = ""
if ($out -match '"username":"([^"]+)"') { $botName = $Matches[1] }
Write-Host "OK，bot = @$botName" -ForegroundColor Green

Write-Host "`n[2/3] 写入仓库 secret（$Repo）..." -ForegroundColor Cyan
$Token | gh secret set TG_BOT_TOKEN --repo $Repo
if ($LASTEXITCODE -ne 0) { Write-Host "gh secret set TG_BOT_TOKEN 失败" -ForegroundColor Red; exit 1 }
$ChatId | gh secret set TG_CHAT_ID --repo $Repo
if ($LASTEXITCODE -ne 0) { Write-Host "gh secret set TG_CHAT_ID 失败" -ForegroundColor Red; exit 1 }

Write-Host "`n[3/3] 触发一次纯通知测试（不访问 52pojie，几十秒）..." -ForegroundColor Cyan
gh workflow run sign.yml --repo $Repo -f test_notify=true
Start-Sleep -Seconds 6
gh run list --repo $Repo --limit 1

Write-Host "`n完成。等这条 run 跑完，Telegram 里应该收到「🧪 通知测试」。" -ForegroundColor Green
Write-Host "看结果： gh run view --repo $Repo --log | Select-String Telegram" -ForegroundColor Gray
Write-Host "`n提示：想让 bot 能给你发消息，务必先在 Telegram 里给它发过一条 /start。" -ForegroundColor Yellow
