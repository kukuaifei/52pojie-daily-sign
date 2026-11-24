# -*- coding: utf-8 -*-
"""诊断：为什么拖动 .slider 时拼图块不跟随？

要回答三个问题：
  1. 鼠标中心点落在哪个元素上（elementFromPoint）
  2. .slider 的 computed style 有没有 pointer-events/z-index 坑
  3. mousemove 过程中 block canvas 的像素 bbox 是否变化（块到底动不动）
"""
import base64
import json
import sys
import time
from pathlib import Path

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URL = "https://www.52pojie.cn/"

PROBE_JS = r"""
() => {
  const s = document.querySelector('.slider');
  if (!s) return { err: 'no .slider' };
  const r = s.getBoundingClientRect();
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
  const hit = document.elementFromPoint(cx, cy);
  const cs = getComputedStyle(s);
  const stack = [];
  let el = hit;
  while (el && stack.length < 6) { stack.push(el.tagName + '.' + (el.className || '')); el = el.parentElement; }
  return {
    sliderBox: { x: r.x, y: r.y, w: r.width, h: r.height },
    center: { x: cx, y: cy },
    hitTag: hit ? hit.tagName : null,
    hitCls: hit ? String(hit.className || '') : null,
    hitStack: stack,
    style: {
      pointerEvents: cs.pointerEvents, zIndex: cs.zIndex, position: cs.position,
      userSelect: cs.userSelect, cursor: cs.cursor,
    },
    sameAsSlider: hit === s,
  };
}
"""

BLOCKBOX_JS = r"""
() => {
  const cs = [...document.querySelectorAll('#captcha canvas')];
  if (cs.length < 2) return null;
  const c = cs[1];
  try {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let minX = 1e9, maxX = -1, minY = 1e9, maxY = -1;
    for (let y = 0; y < c.height; y++) {
      for (let x = 0; x < c.width; x++) {
        if (d[(y * c.width + x) * 4 + 3] > 200) {
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    return maxX < 0 ? null : { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  } catch (e) { return { err: String(e) }; }
}
"""


def main():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN",
                                  timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        print(f"landed: {page.url}")
        if "waf_slider_verify" not in page.url:
            print("not a slider page, abort")
            browser.close()
            return 0

        info = page.evaluate(PROBE_JS)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        (OUT / "diag_probe.json").write_text(json.dumps(info, ensure_ascii=False, indent=1),
                                             encoding="utf-8")

        box = info["sliderBox"]
        sx, sy = info["center"]["x"], info["center"]["y"]
        print(f"before: block={page.evaluate(BLOCKBOX_JS)}")

        page.mouse.move(sx, sy)
        time.sleep(0.2)
        page.mouse.down()
        time.sleep(0.3)
        print(f"after down: block={page.evaluate(BLOCKBOX_JS)}")
        print(f"  slider class={page.evaluate('() => document.querySelector(\".slider\").className')}")
        page.screenshot(path=str(OUT / "diag_down.png"))

        for step in (20, 40, 60, 80, 100, 120, 150, 180):
            page.mouse.move(sx + step, sy + (1 if step % 40 else -1), steps=1)
            time.sleep(0.05)
            bb = page.evaluate(BLOCKBOX_JS)
            print(f"  move +{step}: block={bb}")

        page.screenshot(path=str(OUT / "diag_dragging.png"))
        page.mouse.up()
        time.sleep(2)
        print(f"after up: url={page.url}")
        print(f"  block={page.evaluate(BLOCKBOX_JS)}")
        page.screenshot(path=str(OUT / "diag_after.png"))

        browser.close()
    print("DIAG_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
