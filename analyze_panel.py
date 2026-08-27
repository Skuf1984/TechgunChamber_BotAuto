"""Scan the captured panel for the coloured GUI elements and print their boxes,
so the config ROIs can be pointed at the real pixels instead of guesses.

    python analyze_panel.py            uses logs/panel.png
"""

import os
import sys

import numpy as np
from PIL import Image

import chamber_bot


def cluster(mask, min_area=12):
    """Return a list of (y0, x0, y1, x1, area) for connected mask regions."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    boxes = []
    for sy in range(height):
        for sx in range(width):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            cells = []
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(cells) >= min_area:
                ys = [c[0] for c in cells]
                xs = [c[1] for c in cells]
                boxes.append((min(ys), min(xs), max(ys), max(xs), len(cells)))
    boxes.sort(key=lambda b: -b[4])
    return boxes


def report(name, mask, limit=6, min_area=12):
    print(f"\n[{name}]")
    boxes = cluster(mask, min_area)
    if not boxes:
        print("  nothing found")
        return
    for y0, x0, y1, x1, area in boxes[:limit]:
        print(f"  x {x0:>3}-{x1:<3} y {y0:>3}-{y1:<3}  size {x1 - x0 + 1}x{y1 - y0 + 1}  area {area}")


def main(argv):
    path = argv[1] if len(argv) > 1 else os.path.join(chamber_bot.LOG_DIR, "panel.png")
    frame = np.asarray(Image.open(path).convert("RGB")).astype(np.int32)
    height, width = frame.shape[:2]
    print(f"panel {width}x{height}")
    r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]

    report("saturated red (marker / red fill)", (r > 150) & (g < 90) & (b < 90))
    report("saturated green (power fill ok)", (g > 140) & (r < 130) & (b < 130))
    report("orange (progress)", (r > 180) & (g > 90) & (g < 190) & (b < 90))
    report("dark slots / backgrounds", (r < 60) & (g < 60) & (b < 60), min_area=100)
    report("light grey (container bg)", (np.abs(frame - 198).max(axis=-1) <= 8), min_area=400)


if __name__ == "__main__":
    main(sys.argv)
