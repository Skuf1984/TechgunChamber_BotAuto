"""Re-detect the Reaction Chamber container panel in the CURRENT window and update
the anchor. Robust to window size and position: it finds the largest connected
region of container grey and validates it looks like the chamber container.

    python find_anchor.py
"""

import json
import sys

import numpy as np

import chamber_bot
import vision
import window

# Minecraft container background grey and how far a pixel may deviate
CONTAINER_GREY = 198
GREY_TOL = 18
MIN_GREY_PX = 2000


def _components(mask):
    """Connected components as a list of (area, x0, y0, x1, y1)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for sy in range(height):
        row_seen = seen[sy]
        for sx in range(width):
            if not mask[sy, sx] or row_seen[sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            minx = maxx = sx
            miny = maxy = sy
            area = 0
            while stack:
                y, x = stack.pop()
                area += 1
                if x < minx:
                    minx = x
                elif x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                elif y > maxy:
                    maxy = y
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            comps.append((area, minx, miny, maxx, maxy))
    comps.sort(key=lambda c: -c[0])
    return comps


def find_container(frame):
    """Find the chamber container in a captured client-area frame.

    Returns (x, y, w, h) in client coordinates, or None when not found."""
    grey = np.abs(frame.astype(np.int32) - CONTAINER_GREY).max(axis=2) <= GREY_TOL
    if int(grey.sum()) < MIN_GREY_PX:
        return None
    comps = _components(grey)
    if not comps:
        return None
    area, x0, y0, x1, y1 = comps[0]
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def main():
    with open(chamber_bot.DEFAULT_CONFIG, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    hwnd = window.find_window(cfg["window_title"])
    left, top, width, height = window.client_rect_on_screen(hwnd)
    print(f"client {width}x{height} at ({left},{top})")

    with vision.ScreenCapture() as capture:
        frame = capture.grab(left, top, width, height)

    import panel_match

    found = None
    method = "grey blob"
    template = panel_match.load_template()
    if template is not None:
        found = panel_match.find_panel(frame, template)
        method = "panel template"
    if found is None:
        found = find_container(frame)
        method = "grey blob"
    if found is None:
        print("not enough container grey - is the chamber GUI open and windowed?")
        return
    x0, y0, cw, ch = found
    print(f"found via {method}: client x{x0}-{x0 + cw - 1} y{y0}-{y0 + ch - 1}  size {cw}x{ch}")

    if cw <= ch:
        print(f"warning: detected region is taller than wide ({cw}x{ch}); check the GUI")

    cfg["anchor"] = {"x": x0, "y": y0, "w": cw, "h": ch}
    chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
    print(f"anchor updated: {cfg['anchor']}")


if __name__ == "__main__":
    main()
