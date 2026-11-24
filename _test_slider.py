# -*- coding: utf-8 -*-
"""滑块求解器验证（云 runner 上跑）

算法（本地已用 artifact 三路交叉验证）：
  canvas[0] = 背景（全不透明）
  canvas[1].block = 拼图块，alpha>200 的 bbox 就是块本身（56x56）
  用 TM_CCORR_NORMED + 块 alpha 作 mask 在背景里匹配 → 最高分位置即缺口
  offset = 缺口x - 块x0     （实测 96 - 2 = 94，匹配分 0.9748）
"""
import base64
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

OUT = Path("debug")
OUT.mkdir(exist_ok=True)
URL = "https://www.52pojie.cn/"

CANVAS_JS = r"""
() => {
  const cs = [...document.querySelectorAll('#captcha canvas')];
  if (cs.length < 2) return null;
  const out = [];
  for (const c of cs) {
    let bbox = null;
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
      bbox = maxX < 0 ? null : { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
    } catch (e) {}
    out.push({ cls: String(c.className || ''), w: c.width, h: c.height, bbox: bbox,
               data: c.toDataURL('image/png').split(',')[1] });
  }
  const s = document.querySelector('.slider');
  const r = s ? s.getBoundingClientRect() : null;
  return { canvases: out, slider: r ? { x: r.x, y: r.y, w: r.width, h: r.height } : null };
}
"""


def compute_offset(bg_b64, blk_b64, blk_bbox):
    bg = cv2.imdecode(np.frombuffer(base64.b64decode(bg_b64), np.uint8), cv2.IMREAD_UNCHANGED)
    blk = cv2.imdecode(np.frombuffer(base64.b64decode(blk_b64), np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or blk is None:
        return None, 0.0, "decode failed"
    b = blk_bbox or {}
    bx0 = int(b.get("x", 0))
    by0 = int(b.get("y", 0))
    bw = int(b.get("w", 56))
    bh = int(b.get("h", 56))
    alpha = blk[:, :, 3] if blk.shape[2] == 4 else np.full(blk.shape[:2], 255, np.uint8)
    patch = blk[by0:by0 + bh, bx0:bx0 + bw, :3]
    mask = (alpha[by0:by0 + bh, bx0:bx0 + bw] > 200).astype(np.uint8) * 255
    if patch.size == 0:
        return None, 0.0, "empty patch"
    res = cv2.matchTemplate(bg[:, :, :3], patch, cv2.TM_CCORR_NORMED, mask=mask)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    return maxloc[0] - bx0, float(maxv), f"gap_x={maxloc[0]} block_x={bx0}"


def human_track(distance):
    """加速-减速轨迹，末端带一点回弹余量，模仿人手"""
    track = []
    current = 0.0
    mid = distance * 4 / 5
    t = 0.2
    v = 0.0
    while current < distance:
        a = 2.0 if current < mid else -3.0
        v0 = v
        v = v0 + a * t
        if v < 0.5:
            v = 0.5
        move = v0 * t + 0.5 * a * t * t
        if move <= 0:
            move = 0.5
        current += move
        track.append(move)
    # 归一化到 distance
    total = sum(track)
    if total <= 0:
        return [distance]
    track = [d * distance / total for d in track]
    out, acc = [], 0.0
    for d in track:
        acc += d
        out.append(int(round(acc)))
    out[-1] = int(round(distance))
    return out


def drag(page, box, offset):
    sx = box["x"] + box["w"] / 2
    sy = box["y"] + box["h"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    time.sleep(random.uniform(0.08, 0.16))
    # 起手一点点，制造"人刚开始拉"的微小位移
    page.mouse.move(sx + random.uniform(0.5, 2.0), sy + random.uniform(-1, 1), steps=1)
    for target in human_track(offset):
        page.mouse.move(sx + target, sy + random.uniform(-1.2, 1.2), steps=1)
        time.sleep(random.uniform(0.008, 0.028))
    # 末端停顿再松手
    page.mouse.move(sx + offset, sy, steps=2)
    time.sleep(random.uniform(0.15, 0.35))
    page.mouse.up()


def main():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN",
                                  timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        print(f"landed: {page.url}")

        ok = False
        for rnd in range(1, 4):
            if "waf_slider_verify" not in page.url:
                ok = True
                print(f"round {rnd}: not on slider page -> {page.url}")
                break
            try:
                state = page.evaluate(CANVAS_JS)
            except Exception as e:
                print(f"round {rnd}: evaluate error {e}")
                time.sleep(3)
                continue
            if not state or len(state.get("canvases") or []) < 2:
                print(f"round {rnd}: no canvas yet")
                time.sleep(3)
                continue

            bg_c, blk_c = state["canvases"][0], state["canvases"][1]
            offset, score, note = compute_offset(bg_c["data"], blk_c["data"], blk_c.get("bbox"))
            print(f"round {rnd}: offset={offset} score={score:.4f} ({note}) slider={state.get('slider')}")
            if offset is None or offset < 5:
                print(f"round {rnd}: bad offset, reload")
                page.reload(wait_until="domcontentloaded")
                time.sleep(4)
                continue

            (OUT / f"test_bg_{rnd}.png").write_bytes(base64.b64decode(bg_c["data"]))
            (OUT / f"test_blk_{rnd}.png").write_bytes(base64.b64decode(blk_c["data"]))
            page.screenshot(path=str(OUT / f"test_before_{rnd}.png"))

            drag(page, state["slider"], offset)
            time.sleep(3.5)
            print(f"  after drag: {page.url}")
            page.screenshot(path=str(OUT / f"test_after_{rnd}.png"))
            if "waf_slider_verify" not in page.url:
                ok = True
                print(f"round {rnd}: PASSED -> {page.url}")
                break
            # 没过就刷新换一张
            page.reload(wait_until="domcontentloaded")
            time.sleep(4)

        print("RESULT:", "PASS" if ok else "FAIL")
        (OUT / "result.json").write_text(
            json.dumps({"ok": ok, "url": page.url}, ensure_ascii=False), encoding="utf-8")
        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
