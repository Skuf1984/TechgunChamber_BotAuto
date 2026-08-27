"""Analyse the images saved by 'Verify calibration' (logs/verify_zone_N.png and
verify_preview.png) so we can check zone placement without looking at them.

    python analyze_verify.py
"""

import os

import numpy as np
from PIL import Image

import chamber_bot

ZONE_NAMES = {
    1: "power scale", 2: "marker strip", 3: "luck bar", 4: "fail bar",
    5: "power DIGIT", 6: "+ button", 7: "- button",
}


def ink_stats(rgb):
    gray = rgb.astype(np.int32).mean(axis=2)
    dark = (gray < 110).mean()
    return dark


def main():
    log_dir = chamber_bot.LOG_DIR
    preview = os.path.join(log_dir, "verify_preview.png")
    if not os.path.exists(preview):
        print("no verify_preview.png - run 'Verify calibration' in the GUI first")
        return

    for i in range(1, 8):
        path = os.path.join(log_dir, f"verify_zone_{i}.png")
        if not os.path.exists(path):
            print(f"zone {i} ({ZONE_NAMES[i]}): MISSING")
            continue
        rgb = np.asarray(Image.open(path).convert("RGB"))
        h, w = rgb.shape[:2]
        dark = ink_stats(rgb)
        mean = rgb.reshape(-1, 3).mean(axis=0)
        note = ""
        if i == 5:
            note = "OK: has glyph" if dark > 0.05 else "WARN: little/no dark ink (digit may be missed)"
        elif i in (6, 7):
            note = f"avg colour ~({int(mean[0])},{int(mean[1])},{int(mean[2])})"
        print(f"zone {i} ({ZONE_NAMES[i]}): {w}x{h} dark={dark:.2%} {note}")

    prev = np.asarray(Image.open(preview).convert("RGB"))
    print(f"\npreview: {prev.shape[1]}x{prev.shape[0]}")


if __name__ == "__main__":
    main()
