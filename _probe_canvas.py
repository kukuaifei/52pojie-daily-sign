# -*- coding: utf-8 -*-
"""探测创宇盾滑块页 `waf_slider_verify.html` 的 canvas 像素结构（云 runner 上跑）

页面结构（已确认）：
  .slidercaptcha.card
    #captcha                     320x222  (524,362)
      canvas                      320x180  背景
      canvas.block                320x180  缺口层
      .sliderContainer           320x40   (524,544)
        .sliderbg
        .slider                  40x40    ← 拖动柄
        .sliderText
"""
import base64
import json
import sys
import time
from pathlib import Path

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URLS = ["https://www.52pojie.cn/", "https://www.52pojie.cn/home.php?mod=task"]

CANVAS_JS = r"""
() => {
  const cs = [...document.querySelectorAll('#captcha canvas')];
  const out = [];
  for (let i = 0; i < cs.length; i++) {
    const c = cs[i];
    const rec = { idx: i, w: c.width, h: c.height, cls: String(c.className || '') };
    try {
      const ctx = c.getContext('2d');
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let minX = 1e9, maxX = -1, minY = 1e9, maxY = -1, cnt = 0, opaque = 0;
      for (let y = 0; y < c.height; y++) {
        for (let x = 0; x < c.width; x++) {
          const a = d[(y * c.width + x) * 4 + 3];
          if (a > 0) {
            opaque++;
            if (a > 200) {
              cnt++;
              if (x < minX) minX = x;
              if (x > maxX) maxX = x;
              if (y < minY) minY = y;
              if (y > maxY) maxY = y;
            }
          }
        }
      }
      rec.opaquePixels = opaque;
      rec.total = c.width * c.height;
      rec.solidBox = cnt ? { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1, count: cnt } : null;
      rec.dataUrl = c.toDataURL('image/png');
    } catch (e) {
      rec.err = String(e);
    }
    out.push(rec);
  }
  return out;
}
"""


def main():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN",
                                  timezone_id="Asia/Shanghai")
        page = ctx.new_page()

        for url in URLS:
            print(f"=== goto {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  goto error: {e}")
            time.sleep(4)
            print(f"  landed: {page.url}")

            try:
                recs = page.evaluate(CANVAS_JS)
            except Exception as e:
                print(f"  evaluate error: {type(e).__name__}: {e}")
                continue

            if not recs:
                print("  no #captcha canvas on this page")
                continue

            for rec in recs:
                durl = rec.pop("dataUrl", "")
                print(f"  canvas[{rec['idx']}] {rec['w']}x{rec['h']} cls={rec['cls']!r} "
                      f"opaque={rec.get('opaquePixels')}/{rec.get('total')} "
                      f"solidBox={rec.get('solidBox')} err={rec.get('err')}")
                if durl.startswith("data:image/png;base64,"):
                    raw = base64.b64decode(durl.split(",", 1)[1])
                    fp = OUT / f"canvas_{rec['idx']}.png"
                    fp.write_bytes(raw)
                    print(f"    saved {fp} ({len(raw)} bytes)")

            (OUT / "canvas_meta.json").write_text(
                json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
            page.screenshot(path=str(OUT / "slider_page.png"), full_page=False)
            # 只要任务页那一份
            break

        browser.close()
    print("PROBE2_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
