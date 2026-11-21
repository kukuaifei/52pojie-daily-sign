# 外部定时触发（GitHub 原生 cron 失效时的替代方案）

## 为什么需要这个

本仓库的 workflow `state=active`，`workflow_dispatch` 秒级可跑，但 **`event=schedule` 的 run 一条都没有**。

实证（2026-11-21，全部在 `kukuaifei` 账号下）：

| 测试 | 配置 | 结果 |
| --- | --- | --- |
| 私有仓库 `*/15` | sign.yml，跨 08:15 UTC 窗口 +12 分钟 | `schedule` 计数 **0** |
| 转 public 后 `*/10` | 跨 08:40 / 08:50 窗口 | **0** |
| 全新最小 workflow `ping.yml`（public，无 secret，无 concurrency） | `*/5`，跨 08:55 / 09:00 / 09:05 | **0** |

GitHub 状态页当时全绿（All Systems Operational），社区 2026 下半年同类报告：
[#201354](https://github.com/orgs/community/discussions/201354) ·
[#209391](https://github.com/orgs/community/discussions/209391) ·
[#209569](https://github.com/orgs/community/discussions/209569) ·
[#209896](https://github.com/orgs/community/discussions/209896) ·
[#201008](https://github.com/orgs/community/discussions/201008)

结论：**这个 GitHub 账号的 schedule 调度整体不工作**，与仓库可见性、YAML 写法都无关。
签到逻辑本身在 GitHub Actions 里跑得很好，缺的只是一个"准点按下按钮"的东西。

下面四个方案，**签到仍然 100% 在 GitHub Actions 上执行**，外部只负责发一个 HTTP POST。

---

## 先做一个专用 PAT

不要复用你平时 `gh` 用的那个 token（权限太大）。新建一个最小权限的：

1. <https://github.com/settings/personal-access-tokens/new>
2. Token name：`52pojie-cron-dispatch`
3. Expiration：`No expiration`（或 1 年，到期再换）
4. Repository access → **Only select repositories** → 勾 `52pojie-daily-sign`
5. Permissions → Repository permissions → **Actions: Read and write**（其他全部留 No access）
6. Generate，复制 `github_pat_...`

验证一下能用（204 = 成功）：

```bash
curl -i -X POST \
  -H "Authorization: Bearer github_pat_你的token" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/kukuaifei/52pojie-daily-sign/actions/workflows/sign.yml/dispatches \
  -d '{"ref":"main"}'
```

---

## 方案 A：cron-job.org（最快，纯网页，5 分钟）

1. 注册 <https://cron-job.org/>（免费，邮箱即可）
2. **Create cronjob**，填：

| 字段 | 值 |
| --- | --- |
| Title | `52pojie daily sign` |
| URL | `https://api.github.com/repos/kukuaifei/52pojie-daily-sign/actions/workflows/sign.yml/dispatches` |
| Enable job | ✅ |
| Schedule | Every day at **09:40**，Timezone **Asia/Shanghai** |
| Request method | **POST** |

3. **Advanced** 里加 Headers：

```
Authorization: Bearer github_pat_你的token
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

4. **Request body**：`{"ref":"main"}`
5. Save → 点 **TEST RUN**，看到 `204` 就成了

免费额度：每 15 分钟 1 次以上都够用，一天 1 次毫无压力。

---

## 方案 B：Cloudflare Worker（免费，最稳，cron 精准）

```bash
npm create cloudflare@latest 52pojie-cron -- --type=hello-world
cd 52pojie-cron
```

`src/index.js`：

```js
export default {
  async scheduled(controller, env, ctx) {
    const res = await fetch(
      `https://api.github.com/repos/${env.REPO}/actions/workflows/sign.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.GH_PAT}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'cf-worker-52pojie-cron',
        },
        body: JSON.stringify({ ref: 'main' }),
      }
    );
    console.log('dispatch status', res.status);
  },
  // 也可以手动用浏览器打一下 /trigger 验证
  async fetch(request, env) {
    await this.scheduled(null, env, null);
    return new Response('dispatched\n');
  },
};
```

`wrangler.toml` 追加：

```toml
[vars]
REPO = "kukuaifei/52pojie-daily-sign"

[triggers]
crons = ["40 1 * * *"]   # UTC 01:40 = 北京 09:40
```

部署 + 存 token：

```bash
npx wrangler secret put GH_PAT     # 粘贴 PAT
npx wrangler deploy
```

Cloudflare 的 Cron Trigger 在免费计划里是准点的（通常 ±1 分钟内）。

---

## 方案 C：Google Apps Script（有 Google 账号就行，免费）

1. <https://script.google.com/> → 新建项目
2. 粘贴：

```js
function dispatchSign() {
  const url = 'https://api.github.com/repos/kukuaifei/52pojie-daily-sign/actions/workflows/sign.yml/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + PropertiesService.getScriptProperties().getProperty('GH_PAT'),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });
  Logger.log(res.getResponseCode());   // 204 = 成功
}
```

3. 项目设置 → Script properties → 加 `GH_PAT` = 你的 token
4. 左侧「触发器」→ 添加触发器 → `dispatchSign` / 时间驱动 / 每天 / **上午 9 点到 10 点**
5. 先手动 Run 一次，授权 + 看日志是不是 204

---

## 方案 D：本机 Windows 计划任务（零外部依赖，但机器要开机）

已配置好，任务名 `52pojie-sign-trigger`：

```
StartBoundary: 09:40
Interval: 每小时     Duration: 6 小时      →  09:40 / 10:40 / … / 15:40
```

脚本 `run_now.ps1` 是幂等的：今天已有 `conclusion=success` 的 run 就直接退出，
有 20 分钟内刚启动的 run 也不重复触发。日志 `logs/trigger_YYYYMMDD.log`。

```powershell
.\run_now.ps1            # 今天成功过 → "already OK today"
.\run_now.ps1 -Force     # 强制跑一次

schtasks /Run /TN "52pojie-sign-trigger"     # 手动触发计划任务
schtasks /Delete /TN "52pojie-sign-trigger" /F   # 不想要了就删掉
```

---

## 怎么选

| 场景 | 选 |
| --- | --- |
| 想要最省事，机器不常开 | **A（cron-job.org）** |
| 想要稳，且不介意点几次部署 | **B（Cloudflare Worker）** |
| 已经有 Google 账号，不想再注册 | **C（Apps Script）** |
| 就这一台机器天天开着 | **D（已配好）** |

多个方案可以并存——`sign.py` 本身幂等，同一天被触发多次也只是「今日已完成」直接退出，
不会重复签到。想稳妥就 A + D 双开。

## 保留的 GitHub 原生 cron

`sign.yml` 里仍然留着：

```yaml
- cron: "40 1 * * *"
```

一旦 GitHub 把调度修好，这条通道会自动接回来，不需要改任何东西。它和上面任何方案都不冲突。
