#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机验证「账号密码自动登录」是否可行（会真的登录一次，成功就打印新 Cookie）

用法（PowerShell）：
    $env:PJ52_USER = "你的用户名"
    $env:PJ52_PASS = "你的密码"
    python test_login.py

建议在本机（家宽 IP）跑：Turnstile 在家宽上更容易自动通过。
有头模式能看到浏览器在干什么；加 --headless 可无头跑。
成功后把打印出来的 cookie 贴进 GitHub secret：
    gh secret set PJ52_COOKIE --repo kukuaifei/52pojie-daily-sign --body "<cookie>"
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sign as S

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

headless = "--headless" in sys.argv

if not os.environ.get("PJ52_USER", "").strip() or not os.environ.get("PJ52_PASS", "").strip():
    print("请先设置 PJ52_USER / PJ52_PASS 环境变量")
    sys.exit(2)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=headless, args=[
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage", "--no-sandbox"])
    ctx = browser.new_context(locale="zh-CN", viewport={"width": 1366, "height": 900},
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(45000)
    try:
        ok = S.relogin(page)
        print("relogin:", ok)
        print("url:", page.url)
        ck = S.export_cookie(ctx)
        if ck:
            print("\n=== COOKIE ===")
            print(ck)
            print("=== END ===")
        else:
            print("没导出到 htVC_2132_auth / saltkey")
        if not ok:
            S.shot(page, "test_login_fail")
            print("\n失败截图（若 PJ52_DEBUG=1）：debug/test_login_fail.png")
            print("常见原因：账号设了安全提问 / Turnstile 没过 / 密码错 / 需要图形验证码")
    finally:
        if not headless:
            input("\n按回车关闭浏览器...")
        ctx.close()
        browser.close()
