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

import os
import re
import sys
import time
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
() => {
  const CH = ['请完成安全验证', '验证码访问', 'Please enable JavaScript', 'waf_zw_verify', '访问验证'];
  const SIGN = /签到|打卡|每日|每天/;
  const OK = ['签到成功', '已签到', '已经签到', '任务完成', '恭喜', '获得', '领取成功', '奖励', '已完成', '申请成功', '您已经签到'];
  const ALREADY = ['本期您已申请过此任务', '已申请过此任务', '下期再来', '您已完成此任务', '已领取', '已经领取'];
  const FAIL = ['失败', '错误', '没有找到', '不存在', '无权', '禁止', '请先登录', '您需要先登录', '非法'];

  const body = document.body ? document.body.innerText : '';
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
    none: body.includes('暂无'),
    daily: body.includes('每日签到'),
    ok: OK.filter(w => body.includes(w)),
    already: ALREADY.filter(w => body.includes(w)),
    fail: FAIL.filter(w => body.includes(w)),
    rows: rows,
    len: body.length,
    plain: body.replace(/[^\x20-\x7e]/g, ' ').replace(/\s+/g, ' ').slice(0, 260)
  };
}
"""


def log(msg, level="*"):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print("[%s] %s %s" % (ts, level, msg), flush=True)


def shot(page, name):
    if not DEBUG:
        return
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SHOT_DIR, name + ".png"), full_page=False)
        st = page.evaluate(PROBE_JS)
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


def probe(page):
    try:
        return page.evaluate(PROBE_JS)
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


def open_page(page, url, tag=""):
    """打开页面并等待 JS 挑战自解，返回 probe 结果（没解完返回 None）"""
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log("导航失败（%s）：%s" % (tag or url, str(e)[:120]), "!")
            time.sleep(3)
            continue
        for _ in range(20):
            time.sleep(1.5)
            st = probe(page)
            if st and not st.get("challenge") and st.get("len", 0) > 300:
                return st
        log("第 %d 次仍在挑战页（%s），重试" % (attempt + 1, tag or url), "!")
        shot(page, "challenge_%s_%d" % (tag or "page", attempt + 1))
    return None


def main():
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
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1366, "height": 900},
                                  timezone_id="Asia/Shanghai")
        # 注意：绝对不要 add_init_script 注入 webdriver 补丁——patchright 实测会因此
        # 破坏 GBK 页面的 charset 检测，整页中文变成“锟斤拷”，所有中文判定失效。
        ctx.add_cookies(cookies_for_playwright(ck))
        page = ctx.new_page()
        page.set_default_timeout(45000)
        try:
            return run(page, dry, force_id)
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass


def run(page, dry, force_id):
    # 1) 首页预热（过挑战、拿挑战下发的 cookie）
    st = open_page(page, HOME, "home")
    if st is None:
        return 1, "首页未通过创宇盾挑战（数据中心 IP 常被要求验证码，见 README「Actions 出网 IP」）"
    if not st.get("logout"):
        return 2, "Cookie 无效或已过期：未检测到登录态，请重新取 htVC_2132_saltkey / htVC_2132_auth"
    log("登录态 OK（%d 字符）" % st.get("len", 0))

    # 2) 任务页
    time.sleep(1.0)
    st = open_page(page, TASK_URL, "task")
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
            log("item=%-5s len=%s none=%s daily=%s" % (
                item, pages[item].get("len"), pages[item].get("none"), pages[item].get("daily")))
        if pages.get("done", {}).get("daily") or pages.get("new", {}).get("daily"):
            return 0, "今日的任务已完成/已申请（每日签到红包任务），等下一期（相隔 24 小时）"
        if pages.get("new", {}).get("none") or pages.get("doing", {}).get("none"):
            return 0, "本期任务已完成或未开放（new/doing 均为空），按已签处理"
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
    sys.exit(rc)
