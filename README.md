# 52pojie 每日签到 · GitHub Actions

吾爱破解（www.52pojie.cn）每日「签到红包任务」的全自动签到，跑在 GitHub Actions 上，零本地依赖。

## 为什么不是简单的 requests 脚本

站点前置**创宇盾 WAF**，纯 HTTP 客户端（requests / curl_cffi / 甚至 playwright 的 `ctx.request`）
拿到的都是 32528 字节的 `Please enable JavaScript` 挑战页，页面上一个 `do=apply` 链接都没有。
所以这里用 **patchright（playwright 的反检测分支）+ 真 Chromium 渲染**，让挑战页在浏览器里自己解开。

实测踩到的两个坑（代码里都有注释）：

1. **`ctx.add_init_script()` 会让 GBK 页面整页变「锟斤拷」**
   本站是 `charset=gbk`，一旦注入 init script 破坏 charset 检测，所有中文判断全部失效、
   连截图里的字都是乱码。所以不注入任何补丁，靠 patchright 自带的指纹处理。
2. **`page.inner_text()` / `page.content()` 拿到的是乱码**
   所有中文判断（签到/暂无/每日签到/本期您已申请过…）都放在页面内 JS 里做，
   Python 只接收布尔值、数字和 URL。

## 工作流

```
.github/workflows/sign.yml
  ├─ schedule: 20 0 * * *   (UTC 00:20 = 北京时间 08:20，GitHub 通常延迟 5–30 分钟)
  └─ workflow_dispatch        (手动触发，可勾 dry_run 只探测)
```

判定逻辑：

| 页面状态 | 结论 | 退出码 |
| --- | --- | --- |
| 有 `do=draw` 入口 | 直接领奖 | 0 |
| 有 `do=apply` 且任务名含「签到/打卡/每日/每天」 | 申请 → 若出现 draw 入口再领奖 | 0 |
| 无入口，且 new/doing 显示「暂无」 | 今天已签过，等下一期（相隔 24h） | 0 |
| 无入口，但 done 页有「每日签到」 | 今天已完成 | 0 |
| 命中「本期您已申请过此任务」 | 今天已申请/已领，视为成功 | 0 |
| 挑战页没解开 / 拿不到入口 | 未知 | 1 |
| Cookie 失效（无 logout 链接） | 需要重新取 Cookie | 2 |

## 部署

```bash
# 1) 仓库（已由 gh 创建，private）
gh repo view <OWNER>/52pojie-daily-sign --web

# 2) 配 Cookie secret（只做一次；Cookie 换了这个也要重做）
gh secret set PJ52_COOKIE --repo <OWNER>/52pojie-daily-sign --body "htVC_2132_saltkey=xxx; htVC_2132_auth=yyy; htVC_2132_sid=0"

# 3) 手动跑一次验证
gh workflow run sign.yml --repo <OWNER>/52pojie-daily-sign
gh run list --repo <OWNER>/52pojie-daily-sign --limit 3
gh run view --repo <OWNER>/52pojie-daily-sign --log
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

- **Actions 出网 IP 是 Azure 数据中心**，风控比家宽严。patchright 能过 JS 挑战，
  但如果站点改成「访问验证 + 验证码」，云端就会失败（日志会写「首页未通过创宇盾挑战」）。
  这种情况只能本地跑，或换自建 runner（家宽 IP）。
- **GitHub 定时任务不准时**，通常延迟 5–30 分钟；仓库 60 天没有任何提交，
  schedule 会被 GitHub 自动暂停（提交一次即恢复）。
- **站点公告明确写着**：「禁止使用非人工签到，如浏览器脚本自动签到、服务器挂机签到、签到网站签到等，
  对于严重重复签到会对账号做禁言处理。」用不用、怎么用，自己权衡。
