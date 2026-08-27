"""Interactively locate the power number: hover the mouse over the digit, tap SPACE.

    python locate_digit.py
"""

import os
import time

from PIL import Image

import chamber_bot
import mouse
import vision
import window


def main():
    cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
    hwnd = window.find_window(cfg["window_title"])
    left, top, width, height = window.client_rect_on_screen(hwnd)
    print(f"window client {width}x{height} at ({left},{top})")
    print("open the chamber GUI, hover the mouse pointer OVER THE DIGIT, then tap SPACE")

    keys = mouse.HotkeyEdge("SPACE", "ESC")
    if not keys.wait("SPACE", abort="ESC"):
        print("cancelled")
        return

    sx, sy = window.cursor_pos()
    time.sleep(0.15)
    px, py = sx - left, sy - top
    print(f"cursor screen=({sx},{sy})  client=({px},{py})")

    anchor = cfg["anchor"]
    rx, ry = px - anchor["x"], py - anchor["y"]
    print(f"panel-relative=({rx},{ry})  (panel is {anchor['w']}x{anchor['h']} at client {anchor['x']},{anchor['y']})")
    print(f"normalized=({rx / anchor['w']:.4f},{ry / anchor['h']:.4f})")

    with vision.ScreenCapture() as capture:
        panel = capture.grab(left + anchor["x"], top + anchor["y"], anchor["w"], anchor["h"])
    chamber_bot.save_image(os.path.join(chamber_bot.LOG_DIR, "panel.png"), panel)

    x0 = max(0, rx - 30)
    y0 = max(0, ry - 25)
    x1 = min(anchor["w"], rx + 30)
    y1 = min(anchor["h"], ry + 25)
    crop = panel[y0:y1, x0:x1]
    Image.fromarray(crop.astype("uint8")).resize(
        (crop.shape[1] * 8, crop.shape[0] * 8), Image.NEAREST
    ).save(os.path.join(chamber_bot.LOG_DIR, "digit_hover.png"))
    print(f"saved zoom around the point -> logs/digit_hover.png  (panel[{y0}:{y1},{x0}:{x1}])")
    print("now tell me the digit value you see there so I can verify OCR")


if __name__ == "__main__":
    main()
