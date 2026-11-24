# 52pojie 每日签到 · GitHub Actions

吾爱破解（www.52pojie.cn）每日「签到红包任务」的全自动签到。**双跑道**：

- **`sign-local`** —— 跑在本机 **self-hosted Windows runner** 上，出网是家宽 IP。52pojie 的创宇盾对家宽 IP 基本不拦，成功率最高。**这是主跑道。**
- **`sign-cloud`** —— GitHub 托管 ubuntu runner（Azure 段），仅在本机跑道失败时兜底。该段 IP 会被升级成「滑动拼图」，能不能过看运气。

**已部署**：<https://github.com/kukuaifei/52pojie-daily-sign>（public）
**最近一次验证**：run [45349262352](https://github.com/kukuaifei/52pojie-daily-sign/actions/runs/45349262352) ✓ ——
`sign-local=success | sign-cloud=skipped`，家宽 IP + 验证码 `9PZs` 一次过，apply 成功，+2CB 到账。
Telegram 通知也已打通（run [45350550471](https://github.com/kukuaifei/52pojie-daily-sign/actions/runs/45350550471)）。

## 三层防线：为什么不写 requests 脚本

站点前置**创宇盾 WAF**，要连过三关：

| 关卡 | 现象 | 本项目的解法 |
| --- | --- | --- |
| ① JS 挑战 | 纯 HTTP（requests / curl_cffi / 甚至 playwright 的 `ctx.request`）拿到的是 32528 字节 `Please enable JavaScript`，页面上没有一个 `do=apply` | **patchright + 真 Chromium 渲染**，让挑战在浏览器里自己解开 |
| ② 文字验证码 | 数据中心 IP（如 Actions 的 Azure 段）直接给 `waf_text_verify.html`：「请完成安全验证! IP:xxx」+ 215×80 图形验证码 | **ddddocr 识别 → 填入 → 提交**，错了自动换图重试（实测 1–2 次内通过，上限 10 次） |
| ③ GBK 乱码 | 本站 `charset=gbk`，Playwright 回传的中文在 Python 侧全是「锟斤拷」 | 所有中文判断放进**页面内 JS**，Python 只收布尔值/数字/URL |
| ④ 滑动拼图 | **2026-11-24 起**，数据中心 IP（如 Actions 的 Azure 段 `20.120.164.105`）从文字验证码**升级为滑块**：「向右滑动填充拼图」，ddddocr 完全无效 | 不硬刚：改用**家宽 IP 的 self-hosted runner**（实测本机直连首页 76KB 正常内容，连验证码都不给） |

> ④ 是这套东西现在必须自建 runner 的唯一原因。本地 IP 实测：`HTTP 200 / 76821 字节`，正文无 `waf_` / `安全验证` / `wzws` 任何痕迹。
> Azure 段则稳定吃滑块——那不是代码能解决的问题，是 IP 信誉问题。

实测踩到的三个坑（代码里都留了注释）：

1. **`ctx.add_init_script()` 会让 GBK 页面整页变「锟斤拷」**
   一旦注入 `navigator.webdriver` 补丁，charset 检测被破坏，中文判断全失效、截图里也是乱码。
   四组对照实验（plain / init_script / viewport+tz / all）只有含 init_script 的两组出错。
   所以**不注入任何补丁**，靠 patchright 自带指纹。
2. **`page.inner_text()` / `page.content()` 拿到的是乱码**
   于是有了 `PROBE_JS`：一次 `page.evaluate()` 采集 logout、challenge、verify、none、daily、
   `do=apply|draw` 链接行等全部信号，Python 侧只见 ASCII。
3. **验证码图片必须用 canvas 取原图**
   `<img>` 被 CSS 缩成 80×30，`element.screenshot()` 出来的图 ddddocr 几乎认不出；
   `GET_CAPTCHA_JS` 按 `naturalWidth/naturalHeight` 重绘到 canvas，得到无损 215×80 PNG。

## 本机 runner 部署（`sign-local` 跑道）

Windows 10/11 x64，已装 Python 3.11（`D:\Python311`）与 GitHub CLI。

**1. 注册 runner**

```powershell
$zip = "$env:TEMP\actions-runner.zip"
Invoke-WebRequest 'https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-win-x64-2.338.2.zip' -OutFile $zip
Expand-Archive $zip D:\actions-runner -Force
cd D:\actions-runner
# RUNNER_TOKEN 从 repo Settings → Actions → Runners → New self-hosted runner 拿
.\config.cmd --url https://github.com/kukuaifei/52pojie-daily-sign --token <RUNNER_TOKEN> `
             --name win-local --labels local --runasservice `
             --windowslogonaccount "NT AUTHORITY\NETWORK SERVICE" --unattended
```

2.338.2 **没有 `svc.cmd`**，装服务只能用 `--runasservice`；要换账户先 `.\config.cmd remove --token <REMOVE_TOKEN>`。
服务名形如 `actions.runner.kukuaifei-52pojie-daily-sign.win-local`，`Get-Service` 里应为 Running / Automatic。

**2. 给服务账户开权限**（它不是你，读不到你的 `%LOCALAPPDATA%`）

```powershell
robocopy "$env:LOCALAPPDATA\ms-playwright" D:\ms-playwright /E
icacls D:\ms-playwright /grant "NETWORK SERVICE:(OI)(CI)RX" /T
icacls D:\Python311      /grant "NETWORK SERVICE:(OI)(CI)RX" /T
```

**3. 执行策略**

```powershell
Set-ExecutionPolicy -Scope LocalMachine RemoteSigned -Force
```

workflow 的 `defaults.run.shell` 已写成 `powershell -ExecutionPolicy Bypass -File {0}`。
机器上**没有 pwsh**，`shell: pwsh` 会直接 `command not found`。

**4. Telegram 走代理**

`NETWORK SERVICE` 读不到你的用户级代理设置，直连 `api.telegram.org` 会被重置（`WinError 10054`）。workflow 里显式指定：

```yaml
HTTP_PROXY: http://127.0.0.1:7897
HTTPS_PROXY: http://127.0.0.1:7897
NO_PROXY: www.52pojie.cn,static.52pojie.cn,localhost,127.0.0.1
TG_INSECURE_SSL: "1"
```

`TG_INSECURE_SSL=1` 是因为本机代理做了 TLS 中间人（自签证书链）。它只影响通知请求；签到流量走 Chromium，不读这两个变量。

### 这台机器关机 / 睡眠会怎样

`sign-local` 会一直 pending，托管跑道不会自动接管（`needs: sign-local` 没跑完）。想强制走云跑道：手动触发时勾 `use_cloud`。
本机 `run_now.ps1` 是幂等的（今天已有 success 就退出、20 分钟内有 run 也不重复触发），多个触发源并存不会重复签到。

## 工作流

```
.github/workflows/sign.yml
  ├─ schedule: 40 1 * * *   (UTC 01:40 = 北京时间 09:40；见下方「schedule 在本账号失效」)
  └─ workflow_dispatch        (手动触发，可勾 dry_run 只探测不提交)
```

> **`schedule` 在本账号整体失效（2026-11-21 四轮实证）**
>
> | 测试 | 配置 | schedule run |
> | --- | --- | --- |
> | 私有仓库 | `*/15`，跨 08:15 UTC 窗口 +12min | **0** |
> | 转 public 后 | `*/10`，跨 08:40 / 08:50 窗口 | **0** |
> | 全新最小 workflow（public、无 secret、无 concurrency） | `*/5`，跨 08:55 / 09:00 / 09:05 | **0** |
>
> 期间 GitHub 状态页 All Systems Operational，`state=active`、`workflow_dispatch` 秒级可跑。
> 结论：**与可见性、YAML 写法都无关，是这个账号的 cron 调度不工作**。社区报告：
> [#201354](https://github.com/orgs/community/discussions/201354)、
> [#209391](https://github.com/orgs/community/discussions/209391)、
> [#209569](https://github.com/orgs/community/discussions/209569)、
> [#209896](https://github.com/orgs/community/discussions/209896)、
> [#201008](https://github.com/orgs/community/discussions/201008)。
>
> **签到本身在 GitHub Actions 上跑得好好的，缺的只是一个"准点按按钮"的东西。**
> 四个替代方案（都只发一个 `POST .../dispatches`，签到仍在 Actions 执行）见
> [`EXTERNAL_CRON.md`](EXTERNAL_CRON.md)：
>
> | 方案 | 依赖 | 机器要开机吗 |
> | --- | --- | --- |
> | A. cron-job.org | 注册一个免费账号，网页配 5 分钟 | 不要 |
> | B. Cloudflare Worker | CF 免费账号 + 两条 wrangler 命令 | 不要 |
> | C. Google Apps Script | 有 Google 账号即可 | 不要 |
> | D. 本机 `run_now.ps1` + 计划任务 | 已经配好了 | **要** |
>
> `run_now.ps1` 是幂等的：先看今天有没有 `conclusion=success` 的 run，有就直接退出；
> 有 20 分钟内刚启动的 run 也不重复触发。日志在 `logs/trigger_YYYYMMDD.log`。
>
> 多个方案并存没问题——`sign.py` 本身幂等，同一天被触发几次都只是「今日已完成」后退出。

判定逻辑：

| 页面状态 | 结论 | 退出码 |
| --- | --- | --- |
| 有 `do=draw` 入口 | 直接领奖 | 0 |
| 有 `do=apply` 且任务名含「签到/打卡/每日/每天」 | 申请 → 若出现 draw 入口再领奖 | 0 |
| 无入口，done 页主内容区含**当日日期** | 今天已完成 | 0 |
| 无入口，new 页显示「暂无新任务」 | 本期已申请/已完成，等下一期（相隔 24h） | 0 |
| 命中「本期您已申请过此任务」 | 今天已申请/已领，视为成功 | 0 |
| 挑战页/验证码始终没过 | 未知 | 1 |
| Cookie 失效（无 logout 链接） | 需要重新取 Cookie | 2 |

> **2026-11-21 踩过的坑（已修）**：`ok` / `already` / `fail` 关键词原来在**整页**文本里匹配，
> 而页脚免责声明里有「不得将上述内容用于商业或者**非法**用途」——于是每个成功页都命中失败词，
> 签到明明 `+2CB` 到账了，脚本却报「命中失败词：非法」、workflow 显示失败。
> 现在三个词表只在 `#ct` 主内容区匹配（`scope = mainTxt || body`），并且 `apply` 已明确成功后，
> 补领 `draw` 返回什么都不会覆盖这个成功结论。

> **关于「今天签了」的判定**：done 页只要历史上完成过就永远挂着「每日签到红包任务」字样，
> 光看任务名会把「没签」误判成「已签」。页脚又有 `GMT+8, 2026-11-20 16:08` 这种时间戳，
> 扫整页找日期也会假阳性。所以 `today` 只在 `#ct` 主内容区里匹配当日 `YYYY-MM-DD`
> ——实测 new/doing 页 `today=False`、done 页 `today=True`，判据可靠。

## 部署

```bash
# 1) 仓库（已由 gh 创建，private）
gh repo view kukuaifei/52pojie-daily-sign --web

# 2) 配 Cookie secret（只做一次；Cookie 换了这个也要重做）
gh secret set PJ52_COOKIE --repo kukuaifei/52pojie-daily-sign \
  --body "htVC_2132_saltkey=xxx; htVC_2132_auth=yyy; htVC_2132_sid=0"

# 3) 手动跑一次验证
gh workflow run sign.yml --repo kukuaifei/52pojie-daily-sign
gh run list --repo kukuaifei/52pojie-daily-sign --limit 3
gh run watch --repo kukuaifei/52pojie-daily-sign

# 4) 取调试产物（截图 + 每页 JSON 快照）
gh run download <run-id> --repo kukuaifei/52pojie-daily-sign -n debug -D _art/debug
```

## Cookie 自动续期（可选，靠账号密码）

默认只用 `PJ52_COOKIE`。想让它**过期后自己重新登录**，再配三个 secret：

```bash
# 账号密码（52pojie 的登录名 + 密码）
gh secret set PJ52_USER --repo kukuaifei/52pojie-daily-sign --body "你的用户名"
gh secret set PJ52_PASS --repo kukuaifei/52pojie-daily-sign --body "你的密码"

# 有 secrets 写权限的 PAT，用来把新 Cookie 回写到 secret
gh secret set GH_PAT --repo kukuaifei/52pojie-daily-sign --body "github_pat_xxx"
```

工作流就变成：

```
Cookie 有效  → 直接签到
Cookie 失效  → 用 PJ52_USER/PJ52_PASS 重新登录
             → 拿到新 htVC_2132_auth
             → 写 new_cookie.txt
             → workflow 步骤 gh secret set PJ52_COOKIE 回写
             → 下次运行直接用新的
```

**先在本机验证这条路能不能走通**（家宽 IP 的 Turnstile 比 Azure 容易过得多）：

```powershell
$env:PJ52_USER = "你的用户名"
$env:PJ52_PASS = "你的密码"
python test_login.py
```

会打开一个**有头浏览器**，你能看到它填表单、过 Turnstile、点登录。成功会打印：

```
=== COOKIE ===
htVC_2132_auth=xxxx; htVC_2132_saltkey=yyyy; htVC_2132_sid=zzzz
=== END ===
```

### 这条路的三个已知边界

| 边界 | 说明 |
| --- | --- |
| **Cloudflare Turnstile** | 登录表单里有 `cf-turnstile-response`，脚本最多等 30 秒自动放行。Azure 数据中心 IP 常被要求交互，家宽一般直接过 |
| **安全提问** | 账号一旦设了「安全提问」，脚本填空答案会登录失败（日志会说「自动登录失败」）。去 52pojie 账号设置里取消，或继续用手动 Cookie |
| **图形验证码** | 连续登录失败几次后 Discuz 会挂验证码，脚本用 ddddocr 兜底识别（第一次通常不需要） |

三条都过不去时不影响主流程：签到照常跑，Cookie 失效就返回退出码 2 并发一条 ❌ Telegram，你按提示手动更新一次即可。

PAT 权限：Fine-grained token → 目标仓库 `52pojie-daily-sign` → **Secrets: Read and write**。

## Cookie 怎么取（掉了再取一次）

只要两个字段就够，`htVC_2132_saltkey` + `htVC_2132_auth`（顺带 `htVC_2132_sid=0`）：

- **手动**：浏览器登录 → F12 → Network → 刷新 → 第一条文档请求 → Request Headers → Cookie，复制这两个。
- **本机自动**（Chrome 157 的 Cookie 已经是 v20 App-Bound 加密，读数据库拿不到明文）：
  用同目录 `../52pojie_sign/chrome_cookie_extract.py` 从运行中的 Chrome 内存里读：
  ```bat
  python chrome_cookie_extract.py <chrome主进程PID> <network-servicePID>
  ```
  PID 用 `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'"` 找，
  带 `--type=utility --utility-sub-type=network.mojom.NetworkService` 的那个。

## 本地调试

```bash
pip install -r requirements.txt
python -m patchright install chromium
set PJ52_COOKIE=htVC_2132_saltkey=xxx; htVC_2132_auth=yyy
set PJ52_DEBUG=1
set PJ52_DRY_RUN=1        # 只探测，不真的提交
python sign.py
```

`debug/` 下会有每步截图和 JSON 快照（CI 里作为 artifact 上传，保留 7 天）。

## Telegram 通知

签到结果（成功 / 未知 / 失败）都会推到 Telegram，消息长这样：

```
✅ 52pojie 签到成功
时间：2026-11-21 08:23:11
结果：今日已完成（done 页含 2026-11-21 记录）
查看运行日志            ← 链到本次 Actions run
```

### 一次配置

1. Telegram 里找 **@BotFather** → `/newbot` → 起个名 → 拿到 `123456789:AAH...`。
2. **给这个 bot 发一条 `/start`**（不发的话 bot 没法主动给你发消息，会报 `chat not found`）。
3. 查自己的 chat id：找 **@userinfobot** 发条消息，它会回 `Id: 123456789`。
4. 一条命令写完 secret 并触发测试：

```powershell
cd D:\deepseek-harness\软件破解\52pojie-actions
.\setup_telegram.ps1
```

脚本会验 token、写两个 secret、跑一次 `test_notify`，几十秒后手机会收到「🧪 通知测试」。

手动等价命令：

```bash
gh secret set TG_BOT_TOKEN --repo kukuaifei/52pojie-daily-sign --body "123456789:AAH..."
gh secret set TG_CHAT_ID  --repo kukuaifei/52pojie-daily-sign --body "123456789"
gh workflow run sign.yml --repo kukuaifei/52pojie-daily-sign -f test_notify=true
```

### 可选开关

| 变量 | 位置 | 作用 |
| --- | --- | --- |
| `TG_CHAT_ID` | secret | 支持多个，逗号分隔：`111,222` |
| `TG_NOTIFY_LEVEL` | **repo variable** | `all`（默认，失败也发）/ `success`（只在成功时发） |
| `TG_API_BASE` | 环境变量 | 换反代域名，默认 `https://api.telegram.org` |
| `TG_INSECURE_SSL` | 环境变量 | `=1` 跳过证书校验，仅供本机有 MITM 代理时调试，CI 不要开 |

设 repo variable：`gh variable set TG_NOTIFY_LEVEL --repo kukuaifei/52pojie-daily-sign --body success`

### 排错

| 日志 | 原因 |
| --- | --- |
| `HTTP Error 401 ... Unauthorized` | token 错了或已被 BotFather 撤销 |
| `HTTP Error 400 ... chat not found` | 没给 bot 发过 `/start`，或 chat_id 写错 |
| `HTTP Error 400 ... can't parse entities` | 消息里的尖括号没转义（本次已用 HTML 模式 + `html.escape`，正常不会出现） |
| `CERTIFICATE_VERIFY_FAILED` | 本机有 MITM 代理，加 `TG_INSECURE_SSL=1` 仅用于本地调试 |

通知失败**不会**影响签到本身的退出码：日志里只有 `!` 级别的告警，`EXIT_CODE` 仍是签到结果。

## 已知限制

- **验证码不是 100% 一次过**。ddddocr 在 215×80 的扭曲文字上单次约 60–80%，
  失败会自动 reload 换图重试，最多 10 轮；若 10 轮全错会以退出码 1 结束（不会误报成功）。
- **GitHub 定时任务不准时**，通常延迟 5–30 分钟；仓库 60 天没有任何提交，
  schedule 会被 GitHub 自动暂停（提交一次即恢复）。
- **状态判定依赖中文文案**。站点改文案（比如把「暂无」改成别的）会导致兜底分支失效，
  这时会返回退出码 1 而不是假装成功，看日志里的 `plain` 字段就能定位。
- **站点公告明确写着**：「禁止使用非人工签到，如浏览器脚本自动签到、服务器挂机签到、签到网站签到等，
  对于严重重复签到会对账号做禁言处理。」用不用、怎么用，自己权衡。
