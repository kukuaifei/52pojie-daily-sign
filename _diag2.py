# -*- coding: utf-8 -*-
"""决定性诊断：拖动时 canvas.block 的 CSS left 到底移动多少（1:1 还是缩放）"""
import json
import sys
import time
from pathlib import Path

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URL = "https://www.52pojie.cn/"

STATE_JS = r"""
() => {
  const cs = [...document.querySelectorAll('#captcha canvas')];
  const s = document.querySelector('.slider');
  const m = document.querySelector('.sliderMask');
  const g = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { x: Math.round(r.x * 100) / 100, y: Math.round(r.y * 100) / 100,
             w: Math.round(r.width * 100) / 100, h: Math.round(r.height * 100) / 100,
             styleLeft: el.style.left || '', styleTop: el.style.top || '',
             transform: getComputedStyle(el).transform }; };
  return {
    canvas0: g(cs[0]), block: g(cs[1]), slider: g(s), mask: g(m),
    captchaBox: g(document.querySelector('#captcha')),
  };
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
            print("not slider page")
            browser.close()
            return 0

        st0 = page.evaluate(STATE_JS)
        print("initial:", json.dumps(st0, ensure_ascii=False))
        sx = st0["slider"]["x"] + st0["slider"]["w"] / 2
        sy = st0["slider"]["y"] + st0["slider"]["h"] / 2

        page.mouse.move(sx, sy)
        time.sleep(0.2)
        page.mouse.down()
        time.sleep(0.4)
        print("after down:", json.dumps(page.evaluate(STATE_JS), ensure_ascii=False))

        rows = []
        for step in (10, 30, 50, 80, 110, 140, 170, 200):
            page.mouse.move(sx + step, sy, steps=2)
            time.sleep(0.15)
            st = page.evaluate(STATE_JS)
            rows.append({
                "step": step,
                "block_x": st["block"]["x"] if st["block"] else None,
                "block_left": st["block"]["styleLeft"] if st["block"] else None,
                "block_tf": st["block"]["transform"] if st["block"] else None,
                "slider_x": st["slider"]["x"] if st["slider"] else None,
                "slider_left": st["slider"]["styleLeft"] if st["slider"] else None,
                "mask_w": st["mask"]["w"] if st["mask"] else None,
            })
            print(f"  step+{step}: {json.dumps(rows[-1], ensure_ascii=False)}")

        page.screenshot(path=str(OUT / "diag2_dragging.png"))
        page.mouse.up()
        time.sleep(2)
        print(f"after up: {page.url}")
        (OUT / "diag2.json").write_text(json.dumps({"initial": st0, "rows": rows}, ensure_ascii=False,
                                                   indent=1), encoding="utf-8")
        browser.close()
    print("DIAG2_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
