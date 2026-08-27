"""Re-detect the container panel in the CURRENT window and update the anchor.
Run this whenever the Minecraft window is moved/resized.

    python find_anchor.py
"""

import json
import sys

import numpy as np

import chamber_bot
import vision
import window


def main():
    with open(chamber_bot.DEFAULT_CONFIG, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    hwnd = window.find_window(cfg["window_title"])
    left, top, width, height = window.client_rect_on_screen(hwnd)
    print(f"client {width}x{height} at ({left},{top})")

    with vision.ScreenCapture() as capture:
        frame = capture.grab(left, top, width, height)

    grey = np.abs(frame.astype(np.int32) - 198).max(axis=2) <= 16
    n = int(grey.sum())
    print(f"grey px {n}")
    if n < 2000:
        print("not enough container grey - is the chamber GUI open and windowed?")
        return
    ys, xs = np.where(grey)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    print(f"grey bbox client x{x0}-{x1} y{y0}-{y1}  size {x1 - x0 + 1}x{y1 - y0 + 1}")

    cfg["anchor"] = {"x": x0, "y": y0, "w": x1 - x0 + 1, "h": y1 - y0 + 1}
    chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
    print(f"anchor updated: {cfg['anchor']}")


if __name__ == "__main__":
    main()
