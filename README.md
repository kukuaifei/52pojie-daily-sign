# 52pojie 每日签到 · GitHub Actions

吾爱破解（www.52pojie.cn）每日「签到红包任务」的全自动签到，跑在 GitHub Actions 上，零本地依赖。

**已部署**：<https://github.com/kukuaifei/52pojie-daily-sign>（private）
**最近一次验证**：run [44699655475](https://github.com/kukuaifei/52pojie-daily-sign/actions/runs/44699655475) ✓ 1m33s ——
验证码第 1 次识别 `6rdp` 错误 → 第 2 次 `yezH` 通过 → 判定「今日已完成」。

## 三层防线：为什么不写 requests 脚本

站点前置**创宇盾 WAF**，要连过三关：

| 关卡 | 现象 | 本项目的解法 |
| --- | --- | --- |
| ① JS 挑战 | 纯 HTTP（requests / curl_cffi / 甚至 playwright 的 `ctx.request`）拿到的是 32528 字节 `Please enable JavaScript`，页面上没有一个 `do=apply` | **patchright + 真 Chromium 渲染**，让挑战在浏览器里自己解开 |
| ② 文字验证码 | 数据中心 IP（如 Actions 的 Azure 段）直接给 `waf_text_verify.html`：「请完成安全验证! IP:xxx」+ 215×80 图形验证码 | **ddddocr 识别 → 填入 → 提交**，错了自动换图重试（实测 1–2 次内通过，上限 10 次） |
| ③ GBK 乱码 | 本站 `charset=gbk`，Playwright 回传的中文在 Python 侧全是「锟斤拷」 | 所有中文判断放进**页面内 JS**，Python 只收布尔值/数字/URL |

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

## 工作流

```
.github/workflows/sign.yml
  ├─ schedule: 20 0 * * *   (UTC 00:20 = 北京时间 08:20，GitHub 通常延迟 5–30 分钟)
  └─ workflow_dispatch        (手动触发，可勾 dry_run 只探测不提交)
```

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

## 已知限制

- **验证码不是 100% 一次过**。ddddocr 在 215×80 的扭曲文字上单次约 60–80%，
  失败会自动 reload 换图重试，最多 10 轮；若 10 轮全错会以退出码 1 结束（不会误报成功）。
- **GitHub 定时任务不准时**，通常延迟 5–30 分钟；仓库 60 天没有任何提交，
  schedule 会被 GitHub 自动暂停（提交一次即恢复）。
- **状态判定依赖中文文案**。站点改文案（比如把「暂无」改成别的）会导致兜底分支失效，
  这时会返回退出码 1 而不是假装成功，看日志里的 `plain` 字段就能定位。
- **站点公告明确写着**：「禁止使用非人工签到，如浏览器脚本自动签到、服务器挂机签到、签到网站签到等，
  对于严重重复签到会对账号做禁言处理。」用不用、怎么用，自己权衡。
