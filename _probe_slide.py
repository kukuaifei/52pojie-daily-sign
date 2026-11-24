# -*- coding: utf-8 -*-
"""探测创宇盾验证页的真实 DOM（在 GitHub 云 runner 上跑，结果作为 artifact 下载）

为什么要这个：2026-11-24 起 Azure 出口 IP 从「文字验证码」升级成「滑动拼图」，
ddddocr 的文字识别路线直接失效。要写滑块求解器，先得知道它的 DOM 长什么样。
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URLS = [
    "https://www.52pojie.cn/",
    "https://www.52pojie.cn/home.php?mod=task",
]

DUMP_JS = r"""
() => {
  const info = {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    bodyLen: document.body ? document.body.innerHTML.length : 0,
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
  };
  const imgs = [...document.querySelectorAll('img')].map(i => {
    const r = i.getBoundingClientRect();
    return {
      src: (i.getAttribute('src') || '').slice(0, 240),
      id: i.id, cls: String(i.className || '').slice(0, 120),
      nw: i.naturalWidth, nh: i.naturalHeight,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      vis: r.width > 0 && r.height > 0,
    };
  });
  const canv = [...document.querySelectorAll('canvas')].map(c => {
    const r = c.getBoundingClientRect();
    return { id: c.id, cls: String(c.className || '').slice(0, 120), w: c.width, h: c.height,
             x: Math.round(r.x), y: Math.round(r.y), rw: Math.round(r.width), rh: Math.round(r.height) };
  });
  const nodes = [];
  document.querySelectorAll('*').forEach(el => {
    const id = el.id || '';
    const cls = typeof el.className === 'string' ? el.className : '';
    if (/waf|slide|verify|captcha|drag|check|puzzle|jigsaw/i.test(id + ' ' + cls)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        nodes.push({ tag: el.tagName, id: id, cls: cls.slice(0, 120),
                     x: Math.round(r.x), y: Math.round(r.y),
                     w: Math.round(r.width), h: Math.round(r.height) });
      }
    }
  });
  // HTML 转 base64，绕开 GBK 在 Python 侧变「锟斤拷」的问题
  const html = btoa(unescape(encodeURIComponent(document.documentElement.outerHTML)));
  return { info, imgs, canv, nodes, html };
}
"""


def main():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = ctx.new_page()

        for idx, url in enumerate(URLS, 1):
            tag = "home" if idx == 1 else "task"
            print(f"=== [{idx}] {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  goto error: {type(e).__name__}: {e}")
            time.sleep(4)
            try:
                data = page.evaluate(DUMP_JS)
            except Exception as e:
                print(f"  evaluate error: {type(e).__name__}: {e}")
                continue

            html_b64 = data.pop("html", "")
            try:
                raw = base64.b64decode(html_b64)
                (OUT / f"probe_{tag}.html").write_bytes(raw)
                print(f"  html saved: {len(raw)} bytes")
            except Exception as e:
                print(f"  html decode error: {e}")

            (OUT / f"probe_{tag}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            page.screenshot(path=str(OUT / f"probe_{tag}.png"), full_page=False)

            print(f"  url={data['info']['url']}")
            print(f"  title={data['info']['title']!r}")
            print(f"  bodyLen={data['info']['bodyLen']}")
            for im in data["imgs"]:
                if im["vis"]:
                    print(f"  IMG id={im['id']!r} cls={im['cls']!r} nat={im['nw']}x{im['nh']} "
                          f"box=({im['x']},{im['y']},{im['w']}x{im['h']}) src={im['src'][:90]}")
            for cv in data["canv"]:
                if cv["rw"] > 0:
                    print(f"  CANVAS id={cv['id']!r} cls={cv['cls']!r} {cv['w']}x{cv['h']} "
                          f"box=({cv['x']},{cv['y']},{cv['rw']}x{cv['rh']})")
            for nd in data["nodes"]:
                print(f"  NODE {nd['tag']} id={nd['id']!r} cls={nd['cls']!r} "
                      f"box=({nd['x']},{nd['y']},{nd['w']}x{nd['h']})")

        browser.close()
    print("PROBE_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
