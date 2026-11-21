#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""52pojie.cn 每日签到（GitHub Actions 版，真浏览器内核过创宇盾 JS 挑战）

设计要点（踩坑换来的）：
  1. 站点前置创宇盾 WAF，纯 HTTP 客户端（requests / curl_cffi / ctx.request）拿到的都是
     "Please enable JavaScript" 挑战页 → 必须用真浏览器渲染。
  2. Discuz 页面是 GBK，playwright/patchright 回传的中文会在 Python 侧变成乱码
     （page.inner_text() 与 page.content() 都中招）→ 所有中文判断都放在页面内 JS 里做，
     Python 只接收 布尔值 / 数字 / URL。
  3. 任务页可能没有 apply/draw 入口（今日已申请、周期未到），此时按"已签"处理并退出 0。

环境变量：
  PJ52_COOKIE   必填，"htVC_2132_saltkey=xxx; htVC_2132_auth=yyy; htVC_2132_sid=0"
  PJ52_DRY_RUN  =1 只探测不提交
  PJ52_TASK_ID  手动指定任务 id
  PJ52_DEBUG    =1 存截图与页面快照

退出码：0 已签/成功，1 未知或被 WAF 拦，2 Cookie 失效/网络异常
"""

import base64
import html
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
TASK_URL = "https://www.52pojie.cn/home.php?mod=task"
HOME = "https://www.52pojie.cn/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEBUG = os.environ.get("PJ52_DEBUG", "") == "1"
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug")

# 页面内一次性采集所有需要的信息，中文判断全在 JS 里完成
PROBE_JS = r"""
(today) => {
  const CH = ['请完成安全验证', '验证码访问', 'Please enable JavaScript', 'waf_zw_verify', '访问验证'];
  const SIGN = /签到|打卡|每日|每天/;
  const OK = ['签到成功', '已签到', '已经签到', '任务完成', '恭喜', '获得', '领取成功', '奖励', '已完成', '申请成功', '您已经签到'];
  const ALREADY = ['本期您已申请过此任务', '已申请过此任务', '下期再来', '您已完成此任务', '已领取', '已经领取'];
  const FAIL = ['失败', '错误', '没有找到', '不存在', '无权', '禁止', '请先登录', '您需要先登录', '非法操作'];

  const body = document.body ? document.body.innerText : '';
  // 页脚有 "GMT+8, 2026-11-20 16:08" 这类时间戳，会在任何页面命中当天日期，
  // 所以"今天签过"必须在主内容区（#ct）里找，不能扫整页
  const main = document.querySelector('#ct') || document.querySelector('#wp') || null;
  const mainTxt = main ? (main.innerText || '') : '';
  // ok / already / fail 也只在主内容区匹配：#ft 页脚免责声明里有"非法用途"，
  // 拿整页匹配会让每个成功页都命中失败词（2026-11-21 真实踩过）
  const scope = mainTxt || body;
  const anchors = [...document.querySelectorAll('a[href]')];
  const rows = anchors.filter(a => /do=(apply|draw)/.test(a.href)).map(a => {
    const box = a.closest('tr') || a.parentElement || a;
    const ctx = box.innerText || '';
    return {
      href: a.href,
      action: /do=draw/.test(a.href) ? 'draw' : 'apply',
      sign: SIGN.test(ctx),
      txt: ctx.replace(/[^\x20-\x7e]/g, '').slice(0, 48)
    };
  });
  return {
    logout: anchors.some(a => /action=logout/.test(a.href)),
    challenge: CH.some(w => body.includes(w)),
    verify: location.href.indexOf('waf_text_verify') >= 0 || body.includes('验证码访问'),
    none: body.includes('暂无'),
    daily: body.includes('每日签到'),
    // done 页只要历史完成过就永远含"每日签到"文字，必须配合当日日期（且只在主内容区）才能证明"今天签了"
    today: !!(today && mainTxt && mainTxt.includes(today)),
    ok: OK.filter(w => scope.includes(w)),
    already: ALREADY.filter(w => scope.includes(w)),
    fail: FAIL.filter(w => scope.includes(w)),
    rows: rows,
    len: body.length,
    plain: body.replace(/[^\x20-\x7e]/g, ' ').replace(/\s+/g, ' ').slice(0, 260)
  };
}
"""

# 创宇盾文字验证码页：把 <img> 原图绘到 canvas 拿到无损 PNG（element.screenshot 会被 CSS 缩到 80x30）
GET_CAPTCHA_JS = r"""
() => {
  const i = document.querySelector('#Image1') || document.querySelector('img[src*=captcha]');
  if (!i) return null;
  const c = document.createElement('canvas');
  c.width = i.naturalWidth || i.width;
  c.height = i.naturalHeight || i.height;
  c.getContext('2d').drawImage(i, 0, 0);
  return {b64: c.toDataURL('image/png').split(',')[1], w: c.width, h: c.height};
}
"""

# Discuz 登录页图形验证码（只有连续登录失败后才会出现）
GET_SECCODE_JS = r"""
() => {
  const cands = [...document.querySelectorAll('img')].filter(i =>
    /seccode/i.test(i.src || '') || /^seccode/i.test(i.id || ''));
  const vis = cands.filter(i => i.offsetWidth > 0 && i.offsetHeight > 0);
  if (!vis.length) return null;
  const i = vis[0];
  const c = document.createElement('canvas');
  c.width = i.naturalWidth || i.width;
  c.height = i.naturalHeight || i.height;
  c.getContext('2d').drawImage(i, 0, 0);
  return {b64: c.toDataURL('image/png').split(',')[1], w: c.width, h: c.height, id: i.id || ''};
}
"""

# Cloudflare Turnstile 是否已经拿到 token（空串 = 还没过）
TURNSTILE_JS = r"""
() => {
  const e = document.querySelector('input[name="cf-turnstile-response"]');
  if (!e) return -1;
  return (e.value || '').length;
}
"""

LOGIN_URL = "https://www.52pojie.cn/member.php?mod=logging&action=login"
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "new_cookie.txt")

_OCR = None


def log(msg, level="*"):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print("[%s] %s %s" % (ts, level, msg), flush=True)


def run_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    rid = os.environ.get("GITHUB_RUN_ID", "").strip()
    if repo and rid:
        return "https://github.com/%s/actions/runs/%s" % (repo, rid)
    return ""


def build_message(rc, msg):
    """把结果拼成一条 Telegram 消息（HTML 模式，避免 MarkdownV2 的转义地狱）"""
    icon, title = {0: ("✅", "签到成功"), 1: ("⚠️", "结果未知")}.get(rc, ("❌", "失败"))
    lines = [
        "<b>%s 52pojie %s</b>" % (icon, title),
        "时间：%s" % datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "结果：%s" % html.escape(msg or ""),
    ]
    tid = os.environ.get("PJ52_TASK_ID", "").strip()
    if tid:
        lines.append("任务编号：<code>%s</code>" % html.escape(tid))
    url = run_url()
    if url:
        lines.append('<a href="%s">查看运行日志</a>' % url)
    return "\n".join(lines)


def notify_telegram(text):
    """推送 Telegram；任何异常都只记日志，不影响签到本身的退出码

    TG_BOT_TOKEN / TG_CHAT_ID 必填，TG_CHAT_ID 支持逗号或空格分隔多个会话。
    TG_API_BASE   可选，换反代域名（默认 https://api.telegram.org）
    TG_INSECURE_SSL=1 可选，跳过证书校验（本机有 MITM 代理时才需要，CI 不要开）
    """
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chats = [c for c in re.split(r"[,;\s]+", os.environ.get("TG_CHAT_ID", "").strip()) if c]
    if not token or not chats:
        log("未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过 Telegram 通知")
        return False
    base = (os.environ.get("TG_API_BASE", "").strip() or "https://api.telegram.org").rstrip("/")
    api = "%s/bot%s/sendMessage" % (base, token)
    ctx = None
    if os.environ.get("TG_INSECURE_SSL", "") == "1":
        import ssl
        ctx = ssl._create_unverified_context()
        log("TG_INSECURE_SSL=1：已跳过 Telegram API 证书校验（仅限本机调试）", "!")
    ok = 0
    for cid in chats:
        for attempt in range(3):
            try:
                body = urllib.parse.urlencode({
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                }).encode("utf-8")
                req = urllib.request.Request(api, data=body, method="POST")
                with urllib.request.urlopen(req, timeout=20, **({"context": ctx} if ctx else {})) as resp:
                    if resp.status == 200:
                        ok += 1
                        break
                    log("Telegram 返回 %s" % resp.status, "!")
            except Exception as e:
                detail = str(e)[:160]
                try:  # 把 Telegram 的 description 挖出来，401/400 时最有用
                    raw = e.read().decode("utf-8", "replace")[:200]
                    detail = detail + " | " + raw
                except Exception:
                    pass
                log("Telegram 推送失败（%s 第 %d 次）：%s" % (cid, attempt + 1, detail), "!")
                time.sleep(2 * (attempt + 1))
    if ok:
        log("Telegram 通知已发送（%d 个会话）" % ok)
    return ok > 0


def shot(page, name):
    if not DEBUG:
        return
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SHOT_DIR, name + ".png"), full_page=False)
        st = page.evaluate(PROBE_JS, today_str())
        with open(os.path.join(SHOT_DIR, name + ".json"), "w", encoding="utf-8") as f:
            import json
            json.dump(st, f, ensure_ascii=True, indent=1)
    except Exception as e:
        log("调试快照失败：%s" % e, "!")


def parse_cookie(raw):
    out = {}
    for part in re.split(r"[;\n]", raw or ""):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().strip("\"'"), v.strip().strip("\"'")
        if k:
            out[k] = v
    return out


def cookies_for_playwright(d):
    return [{
        "name": k, "value": v, "domain": "www.52pojie.cn", "path": "/",
        "secure": True, "httpOnly": k.startswith("htVC_"), "sameSite": "Lax",
    } for k, v in d.items()]


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def probe(page):
    try:
        return page.evaluate(PROBE_JS, today_str())
    except Exception:
        return None


def verdict(st):
    """返回 (True/False/None, 说明)"""
    if not st:
        return None, "页面读取失败"
    if st.get("challenge"):
        return None, "创宇盾挑战页未通过"
    if st.get("already"):
        return True, "今日已申请/已领取（%s）" % st["already"][0]
    if st.get("fail") and not st.get("ok"):
        return False, "命中失败词：" + "/".join(st["fail"][:3])
    if st.get("ok"):
        return True, "命中成功词：" + "/".join(st["ok"][:3])
    return None, "无明确关键词"


def solve_captcha(page, max_rounds=10):
    """过创宇盾「验证码访问」文字验证：ddddocr 识别 → 填 → 提交，失败自动换一张重试"""
    global _OCR
    if _OCR is None:
        try:
            import ddddocr
        except ImportError:
            log("未安装 ddddocr，无法自动过文字验证码", "!")
            return False
        _OCR = ddddocr.DdddOcr(show_ad=False)
        log("ddddocr 已加载")
    for r in range(max_rounds):
        img = page.evaluate(GET_CAPTCHA_JS)
        if not img:
            log("验证页上找不到验证码图片", "!")
            return False
        code = _OCR.classification(base64.b64decode(img["b64"]))
        code = re.sub(r"[^0-9A-Za-z]", "", code or "")
        log("验证码第 %d 次识别：%r（%dx%d）" % (r + 1, code, img["w"], img["h"]))
        if not code:
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(2)
            continue
        try:
            el = page.query_selector("input[name=captcha], input.code-input")
            el.fill("")
            el.type(code, delay=60)
            time.sleep(0.3)
            page.click("input.code-btn")
        except Exception as e:
            log("提交验证码失败：%s" % str(e)[:100], "!")
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(2)
            continue
        time.sleep(2.5)
        st = probe(page)
        if st and not st.get("verify") and not st.get("challenge"):
            log("验证码通过 → %s" % page.url)
            return True
        if st and st.get("verify"):
            log("验证码错误，换一张重试", "!")
            time.sleep(1.0)
    return False


def open_page(page, url, tag="", referer=None, tries=4):
    """打开页面，依次处理：JS 挑战（自动解）→ 文字验证码（ddddocr）→ 正常内容"""
    import random
    for attempt in range(tries):
        kw = {"wait_until": "domcontentloaded", "timeout": 60000}
        if referer:
            kw["referer"] = referer
        try:
            page.goto(url, **kw)
        except Exception as e:
            log("导航失败（%s）：%s" % (tag or url, str(e)[:120]), "!")
            time.sleep(3)
            continue
        for k in range(10):
            time.sleep(2)
            st = probe(page)
            if st and st.get("verify"):
                if solve_captcha(page):
                    st = probe(page)
                    if st and not st.get("challenge") and not st.get("verify") and st.get("len", 0) > 300:
                        return st
                continue
            if st and not st.get("challenge") and st.get("len", 0) > 300:
                return st
            if k == 4:  # 等到一半还没过 → 主动重发一次请求
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
        log("第 %d 次仍未通过（%s），重试" % (attempt + 1, tag or url), "!")
        shot(page, "challenge_%s_%d" % (tag or "page", attempt + 1))
        time.sleep(3 + random.uniform(2, 6) * (attempt + 1))
    return None


def export_cookie(ctx):
    """从浏览器 context 里导出 Discuz 认证所需的 htVC_2132_* """
    want = ("htVC_2132_saltkey", "htVC_2132_auth", "htVC_2132_sid")
    got = {}
    try:
        for c in ctx.cookies():
            if c.get("name") in want and c.get("value"):
                got[c["name"]] = c["value"]
    except Exception:
        return ""
    if "htVC_2132_auth" not in got or "htVC_2132_saltkey" not in got:
        return ""
    return "; ".join("%s=%s" % (k, got[k]) for k in want if k in got)


def save_cookie(cookie):
    """写盘给 workflow 回写 secret 用；同时输出到日志便于人工兜底"""
    if not cookie:
        return
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(cookie)
    log("新 Cookie 已写入 new_cookie.txt（长度 %d）" % len(cookie))


def relogin(page):
    """Cookie 失效时用账号密码重新登录：填表 → 等 Turnstile → 提交 → 验证登录态"""
    user = os.environ.get("PJ52_USER", "").strip()
    pwd = os.environ.get("PJ52_PASS", "").strip()
    if not user or not pwd:
        log("未配置 PJ52_USER / PJ52_PASS，无法自动重新登录", "!")
        return False
    log("Cookie 失效，尝试用账号 %s 自动登录" % user[:3] + "***")
    st = open_page(page, LOGIN_URL, "login")
    if st is None:
        log("打不开登录页（WAF）", "!")
        return False
    try:
        page.fill("input[name=username]", user)
        page.fill("input[name=password]", pwd)
    except Exception as e:
        log("填写账号密码失败：%s" % str(e)[:100], "!")
        return False

    # 安全提问留空（questionid=0）；若账号设了安全提问，登录会失败——这是已知边界
    try:
        page.select_option("select[name=questionid]", "0")
    except Exception:
        pass

    # Cloudflare Turnstile：invisible/managed 模式往往几秒内自动给 token
    waited = 0
    while waited < 30:
        try:
            n = page.evaluate(TURNSTILE_JS)
        except Exception:
            n = -1
        if n > 0:
            log("Turnstile 已通过（%d 字符）" % n)
            break
        time.sleep(2)
        waited += 2
    else:
        log("Turnstile 30 秒内没拿到 token，仍然尝试提交", "!")

    # Discuz 连续失败后才会挂图形验证码，出现就用 ddddocr 过
    try:
        sec = page.evaluate(GET_SECCODE_JS)
    except Exception:
        sec = None
    if sec:
        global _OCR
        if _OCR is None:
            try:
                import ddddocr
                _OCR = ddddocr.DdddOcr(show_ad=False)
            except ImportError:
                _OCR = False
        if _OCR:
            code = re.sub(r"[^0-9A-Za-z]", "", _OCR.classification(base64.b64decode(sec["b64"])) or "")
            log("登录验证码识别：%r（%dx%d）" % (code, sec["w"], sec["h"]))
            if code:
                try:
                    page.fill("input[name=seccodeverify]", code)
                except Exception:
                    pass

    try:
        page.click("button[name=loginsubmit]")
    except Exception:
        try:
            page.click("input[name=loginsubmit]")
        except Exception as e:
            log("点击登录按钮失败：%s" % str(e)[:100], "!")
            return False

    for k in range(8):
        time.sleep(2)
        st = probe(page)
        if st and st.get("logout"):
            log("自动登录成功 → %s" % page.url)
            return True
        if st and st.get("verify"):
            solve_captcha(page)
    log("自动登录失败（可能需要安全提问 / 验证码 / Turnstile 未过）", "!")
    shot(page, "login_fail")
    return False


def main():
    # 只测 Telegram 通道：不碰站点，发一条测试消息就退出
    if os.environ.get("PJ52_TEST_NOTIFY", "") == "1":
        ok = notify_telegram(
            "<b>🧪 52pojie 签到 · 通知测试</b>\n"
            "Telegram 通道正常，以后每次签到都会推到这里。")
        return (0 if ok else 1), ("Telegram 测试消息已发送" if ok else
                                  "Telegram 测试消息发送失败，检查 TG_BOT_TOKEN / TG_CHAT_ID（见 README）")

    raw = os.environ.get("PJ52_COOKIE", "").strip()
    dry = os.environ.get("PJ52_DRY_RUN", "") in ("1", "true", "yes")
    force_id = os.environ.get("PJ52_TASK_ID", "").strip()

    if not re.search(r"htVC_\d+_auth\s*=", raw):
        log("PJ52_COOKIE 未配置或格式不对（需要 htVC_2132_auth）", "!")
        return 2
    ck = parse_cookie(raw)
    log("Cookie 字段：%s" % ", ".join(sorted(ck)))

    try:
        from patchright.sync_api import sync_playwright  # 反检测版，优先
        engine = "patchright"
    except ImportError:
        from playwright.sync_api import sync_playwright
        engine = "playwright"
    log("浏览器引擎：%s" % engine)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage", "--no-sandbox"])
        ctx = browser.new_context(locale="zh-CN",
                                  viewport={"width": 1366, "height": 900},
                                  timezone_id="Asia/Shanghai")
        # 注意：绝对不要 add_init_script 注入 webdriver 补丁——patchright 实测会因此
        # 破坏 GBK 页面的 charset 检测，整页中文变成“锟斤拷”，所有中文判定失效。
        ctx.add_cookies(cookies_for_playwright(ck))
        page = ctx.new_page()
        page.set_default_timeout(45000)
        try:
            return run(page, dry, force_id, ctx)
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass


def run(page, dry, force_id, ctx=None):
    # 1) 首页预热（过挑战、拿挑战下发的 cookie）
    st = open_page(page, HOME, "home")
    if st is None:
        return 1, "首页未通过创宇盾挑战（数据中心 IP 常被要求验证码，见 README「Actions 出网 IP」）"
    if not st.get("logout"):
        # Cookie 失效 → 有账号密码就自动重登，成功后把新 Cookie 交给 workflow 回写 secret
        if relogin(page):
            st = probe(page) or {}
            new_ck = export_cookie(ctx) if ctx is not None else ""
            if new_ck:
                save_cookie(new_ck)
                if not os.environ.get("GH_PAT", "").strip():
                    log("未配置 GH_PAT，new_cookie.txt 不会自动回写到 secret（见 README）", "!")
        if not st.get("logout"):
            return 2, ("Cookie 无效或已过期，自动登录也没成功。请重新取 htVC_2132_saltkey / "
                       "htVC_2132_auth，并检查 PJ52_USER / PJ52_PASS 是否正确")
    log("登录态 OK（%d 字符）" % st.get("len", 0))

    # 2) 任务页（带 Referer 从首页进，裸 goto 更容易被判定为脚本）
    time.sleep(1.0)
    st = open_page(page, TASK_URL, "task", referer=HOME)
    if st is None:
        return 1, "任务页被创宇盾挑战拦截，本轮放弃"
    shot(page, "task")

    rows = st.get("rows") or []
    draws = [r for r in rows if r["action"] == "draw"]
    applys = [r for r in rows if r["action"] == "apply"]
    log("任务入口：apply=%d draw=%d | %s" % (len(applys), len(draws), st.get("plain", "")[:80]))

    target = None
    if force_id:
        target = ("draw" if draws else "apply", force_id, "手动指定")
    elif draws:
        target = ("draw", re.search(r"id=(\d+)", draws[0]["href"]).group(1), draws[0]["txt"] or "待领取")
    else:
        pick = [r for r in applys if r["sign"]] or applys
        if pick:
            target = ("apply", re.search(r"id=(\d+)", pick[0]["href"]).group(1), pick[0]["txt"] or "任务")

    if not target:
        log("任务页无 apply/draw 入口，核实 new / doing / done")
        pages = {}
        for item in ("new", "doing", "done"):
            s2 = open_page(page, TASK_URL + "&item=" + item, item)
            pages[item] = s2 or {}
            log("item=%-5s len=%s none=%s daily=%s today=%s" % (
                item, pages[item].get("len"), pages[item].get("none"),
                pages[item].get("daily"), pages[item].get("today")))
        # done 页历史完成记录会一直挂着"每日签到红包任务"字样，只有同时出现当日日期才算今天签过
        if pages.get("done", {}).get("today"):
            return 0, "今日已完成（done 页含 %s 记录）" % today_str()
        if pages.get("new", {}).get("none"):
            return 0, "本期任务已申请或已完成（new 页无新任务），等下一期（相隔 24 小时）"
        return 1, "未找到签到任务入口（任务已申请过或本期未开放）"

    action, tid, name = target
    url = "%s&do=%s&id=%s" % (TASK_URL, action, tid)
    log("执行 %s：#%s %s" % (action, tid, url))
    if dry:
        return 0, "[dry-run] 将请求 %s" % url

    st = open_page(page, url, action)
    if st is None:
        return 1, "请求 %s 时被 WAF 拦下" % url
    shot(page, action)
    v, why = verdict(st)
    log("结果：%s（%s）| %s" % ("成功" if v else ("失败" if v is False else "未知"), why,
                              st.get("plain", "")[:100]))

    # apply 之后通常立即出现 draw 入口，补一次领奖
    if action == "apply" and v is not False:
        dr = [r for r in (st.get("rows") or []) if r["action"] == "draw"]
        if dr:
            m = re.search(r"id=(\d+)", dr[0]["href"])
            if m:
                time.sleep(1.5)
                durl = "%s&do=draw&id=%s" % (TASK_URL, m.group(1))
                st2 = open_page(page, durl, "draw2")
                v2, why2 = verdict(st2 or {})
                # apply 已经明确成功（页面已经出现 +CB / 恭喜），补领这一步只是拿奖励，
                # 它返回"已领取/非法操作"不代表签到失败，不能覆盖已经成功的结果
                if v is True:
                    return 0, "任务 #%s 申请成功（%s）；补领：%s" % (m.group(1), why, why2)
                return (0 if v2 else 1), "任务 #%s 申请+领奖：%s（%s）" % (
                    m.group(1), "成功" if v2 else "未知", why2)

    return (0 if v else 1), "任务 #%s %s：%s（%s）" % (tid, action, "成功" if v else "未知", why)


if __name__ == "__main__":
    try:
        rc, msg = main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        rc, msg = 2, "未捕获异常：%s" % str(e)[:200]
    log(msg, "!" if rc else "+")
    print("EXIT_CODE=%d" % rc, flush=True)
    # 通知：默认任何结果都发（失败更需要知道）；TG_NOTIFY_LEVEL=success 时只在成功时发
    try:
        lvl = os.environ.get("TG_NOTIFY_LEVEL", "all").strip().lower()
        if os.environ.get("PJ52_TEST_NOTIFY", "") == "1":
            pass  # 测试模式已在 main() 里发过，不再重复
        elif lvl == "success" and rc != 0:
            log("TG_NOTIFY_LEVEL=success 且本次未成功，跳过通知")
        else:
            notify_telegram(build_message(rc, msg))
    except Exception as e:
        log("通知阶段异常（不影响结果）：%s" % str(e)[:150], "!")
    sys.exit(rc)
