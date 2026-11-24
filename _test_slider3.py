# -*- coding: utf-8 -*-
"""滑块求解器 v3：修正了 CSS 缩放系数（块位移 = 滑块位移 × 13/14）。

diag2 实测（run 45353859395）：
  slider_left 200px -> block_left 185.714px，比例恒为 0.928571 = 13/14
  canvas0 显示 322x182，block/captcha 内部 320x180，缩放 322/320

要让块（内部 x=2）落到缺口 gap_x 处：
  block_left 需要 = (gap_x - 2) * 322/320
  slider 需要    = block_left * 14/13 = (gap_x - 2) * 1.08365
"""
import base64
import json
import random
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URL = "https://www.52pojie.cn/"
SCALE = (322.0 / 320.0) * (14.0 / 13.0)   # 1.0836538

CANVAS_JS = r"""
() => {
  const cs = [...document.querySelectorAll('#captcha canvas')];
  if (cs.length < 2) return { err: 'no canvas', n: cs.length };
  const s = document.querySelector('.slider');
  const srect = s ? s.getBoundingClientRect() : null;
  const toB64 = (c) => {
    const cv = document.createElement('canvas');
    cv.width = c.width; cv.height = c.height;
    cv.getContext('2d').drawImage(c, 0, 0);
    return cv.toDataURL('image/png').split(',')[1];
  };
  const data1 = cs[1].getContext('2d').getImageData(0, 0, cs[1].width, cs[1].height).data;
  let x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
  for (let i = 3, p = 0; i < data1.length; i += 4, p++) {
    if (data1[i] > 200) {
      const px = p % cs[1].width, py = (p / cs[1].width) | 0;
      if (px < x0) x0 = px; if (px > x1) x1 = px;
      if (py < y0) y0 = py; if (py > y1) y1 = py;
    }
  }
  return {
    bg: toB64(cs[0]), blk: toB64(cs[1]),
    bw: cs[1].width, bh: cs[1].height,
    cw: cs[0].width, ch: cs[0].height,
    block: x1 >= 0 ? { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 } : null,
    slider: srect ? { x: srect.x, y: srect.y, w: srect.width, h: srect.height } : null,
    url: location.href,
  };
}
"""


def b64_to_img(s):
    return cv2.imdecode(np.frombuffer(base64.b64decode(s), np.uint8), cv2.IMREAD_UNCHANGED)


def find_gap(bg, blk, box):
    """返回缺口 canvas 内 x（ccoeff 为主，edge_tpl 为辅投票）"""
    bg3 = bg[:, :, :3]
    gray = cv2.cvtColor(bg3, cv2.COLOR_BGR2GRAY)
    alpha = blk[:, :, 3] if blk.shape[2] == 4 else np.full(blk.shape[:2], 255, np.uint8)
    x0, y0, w, h = box["x"], box["y"], box["w"], box["h"]
    patch = blk[y0:y0 + h, x0:x0 + w, :3]
    mask = (alpha[y0:y0 + h, x0:x0 + w] > 200).astype(np.uint8) * 255
    cands = []
    try:
        r = cv2.matchTemplate(bg3, patch, cv2.TM_CCOEFF_NORMED, mask=mask)
        _, mv, _, ml = cv2.minMaxLoc(r)
        cands.append(("ccoeff", int(ml[0]), round(float(mv), 4)))
    except Exception as e:
        cands.append(("ccoeff", -1, str(e)))
    try:
        pg = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        r = cv2.matchTemplate(cv2.Canny(gray, 50, 150), cv2.Canny(pg, 50, 150), cv2.TM_CCORR_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        cands.append(("edge", int(ml[0]), round(float(mv), 4)))
    except Exception as e:
        cands.append(("edge", -1, str(e)))
    vals = [c[1] for c in cands if c[1] >= 0]
    if len(vals) == 2 and abs(vals[0] - vals[1]) <= 15:
        gap = int(round((vals[0] + vals[1]) / 2))
        detail = f"agree {vals}"
    elif len(vals) >= 1:
        cands_sorted = sorted([c for c in cands if c[1] >= 0], key=lambda t: -t[2])
        gap = cands_sorted[0][1]
        detail = f"pick {cands_sorted[0][0]} from {[(c[0], c[1]) for c in cands]}"
    else:
        return None, "no candidate"
    return gap, detail


def human_track(distance):
    """加速-匀速-减速，带微小抖动；返回每步的累计位移列表"""
    track, cur, v = [], 0.0, 0
    mid = distance * 0.7
    t, v = 0.0, 0.0
    while cur < distance:
        if cur < mid:
            a = random.uniform(1.6, 3.2)
        else:
            a = -random.uniform(2.2, 4.0)
        v = max(0.6, v + a)
        v = min(v, 22.0)
        step = max(1.0, v + random.uniform(-0.6, 0.6))
        if cur + step > distance:
            step = distance - cur
        cur += step
        track.append(round(cur, 2))
        t += 1
        if t > 120:
            break
    if not track or track[-1] < distance:
        track.append(distance)
    return track


def solve_one(page, tag):
    st = page.evaluate(CANVAS_JS)
    if st.get("err"):
        return None, st["err"]
    bg = b64_to_img(st["bg"])
    blk = b64_to_img(st["blk"])
    if bg is None or blk is None or not st.get("block"):
        return None, "decode/block fail"
    gap, detail = find_gap(bg, blk, st["block"])
    if gap is None:
        return None, detail
    bx = st["block"]["x"]
    off = int(round((gap - bx) * SCALE))
    (OUT / f"{tag}_bg.png").write_bytes(base64.b64decode(st["bg"]))
    (OUT / f"{tag}_before.png").write_bytes(page.screenshot())
    return {"gap": gap, "bx": bx, "offset": off, "detail": detail, "slider": st["slider"]}, detail


def drag(page, sl, offset):
    sx = sl["x"] + sl["w"] / 2
    sy = sl["y"] + sl["h"] / 2
    page.mouse.move(sx, sy)
    time.sleep(random.uniform(0.15, 0.35))
    page.mouse.down()
    time.sleep(random.uniform(0.12, 0.25))
    prev = 0.0
    for pos in human_track(offset):
        page.mouse.move(sx + pos, sy + random.uniform(-1.2, 1.2))
        time.sleep(random.uniform(0.012, 0.035))
        prev = pos
    # 末端微调：过冲 2px 再回正，模拟人手
    page.mouse.move(sx + offset + random.uniform(1, 3), sy)
    time.sleep(0.08)
    page.mouse.move(sx + offset, sy)
    time.sleep(0.12)
    page.mouse.up()


def main():
    from patchright.sync_api import sync_playwright

    rounds = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN",
                                 timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        print(f"landed: {page.url}")

        if "waf_slider_verify" not in page.url:
            print(f"NOT_SLIDER: {page.url}")
            page.screenshot(path=str(OUT / "not_slider.png"))
            browser.close()
            return 0

        ok = False
        for i in range(1, 5):
            page.wait_for_selector(".slider", timeout=20000)
            time.sleep(2.0)          # 等 slidecaptcha 绑事件
            info, detail = solve_one(page, f"r{i}")
            if not info:
                print(f"round{i}: SOLVE_FAIL {detail}")
                page.reload(wait_until="domcontentloaded")
                time.sleep(5)
                continue
            print(f"round{i}: gap={info['gap']} block_x={info['bx']} offset={info['offset']} ({detail})")
            drag(page, info["slider"], info["offset"])
            time.sleep(3.0)
            after = page.url
            print(f"  after: {after}")
            rounds.append({"round": i, **info, "url": after})
            if "waf_slider_verify" not in after:
                print(f"  PASSED -> {after}")
                ok = True
                break
            page.screenshot(path=str(OUT / f"r{i}_after.png"))
            page.reload(wait_until="domcontentloaded")
            time.sleep(5)

        if not ok:
            page.screenshot(path=str(OUT / "final.png"))
        (OUT / "slider3.json").write_text(json.dumps({"ok": ok, "rounds": rounds}, ensure_ascii=False,
                                                     indent=1), encoding="utf-8")
        browser.close()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
