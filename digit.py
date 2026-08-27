"""Digit reading for the laser power number via connected-component templates.

Minecraft's font is pixel-fixed, so capturing each digit once lets us match them
exactly - no OCR engine. Handles 1- and 2-digit values (power goes up to 10).

Templates live in digit_templates.json as {label: [[0/1 rows]]} for digits 0-9.
"""

import json
import os

import numpy as np

import paths

BASE_DIR = paths.DATA_DIR
TEMPLATES_PATH = os.path.join(BASE_DIR, "digit_templates.json")

# ROI tight around the power digit(s); excludes the scale (left), the frame (top)
# and the stray vertical block at panel x316+ (right). Digit '1' sits ~x293-300,y32-45.
# Expressed for the reference panel size; scaled to the actual panel so a GUI-scale
# change doesn't shift the crop.
DIGIT_ROI = (288, 27, 316, 51)
DIGIT_ROI_REF = (398, 324)
INK_MAX = 110     # digit ink is darker than this
MIN_INK = 18      # ignore stray specks smaller than this


def crop_digit(panel):
    h, w = panel.shape[:2]
    rw, rh = DIGIT_ROI_REF
    if (w, h) == (rw, rh):
        x0, y0, x1, y1 = DIGIT_ROI
        return panel[y0:y1, x0:x1]
    sx, sy = w / rw, h / rh
    x0, y0, x1, y1 = DIGIT_ROI
    return panel[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]


def _gray(crop):
    rgb = crop.astype(np.int32)
    return (rgb[:, :, 0] + rgb[:, :, 1] + rgb[:, :, 2]) // 3


def _components(mask):
    """Connected components of a binary mask as a list of (cells, x_center)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for sy in range(height):
        for sx in range(width):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            cells = [(sy, sx)]
            while stack:
                y, x = stack.pop()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
                        cells.append((ny, nx))
            xs = [c[1] for c in cells]
            comps.append((cells, sum(xs) / len(xs)))
    return comps


def _crop_cells(mask, cells):
    ys = [c[0] for c in cells]
    xs = [c[1] for c in cells]
    y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
    out = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=np.uint8)
    for y, x in cells:
        out[y - y0, x - x0] = 1
    return out


def digit_components(crop):
    """Return a list of per-digit glyph masks, left to right (stray specks dropped)."""
    ink = _gray(crop) < INK_MAX
    if not ink.any():
        return []
    comps = [c for c in _components(ink) if len(c[0]) >= MIN_INK]
    comps.sort(key=lambda c: c[1])
    return [_crop_cells(ink, c[0]) for c in comps]


def glyph_mask(crop):
    """Single glyph with the most ink (used when capturing one known digit)."""
    comps = digit_components(crop)
    if not comps:
        return None
    return max(comps, key=lambda m: int(m.sum()))


def canonical(mask, size=(22, 30)):
    import cv2

    return cv2.resize((mask * 255).astype(np.uint8), size, interpolation=cv2.INTER_AREA)


def similarity(a, b):
    am = a > 96
    bm = b > 96
    union = am | bm
    if not union.any():
        return 1.0
    return float((am & bm).sum()) / float(union.sum())


def load_templates(path=TEMPLATES_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {int(label): np.array(rows, dtype=np.uint8) for label, rows in raw.items()}


def save_templates(templates, path=TEMPLATES_PATH):
    raw = {str(label): mask.astype(int).tolist() for label, mask in templates.items()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle)


def read_number(panel, templates):
    """Return (value, confidence) or (None, 0.0). Handles 1- and 2-digit values."""
    comps = digit_components(crop_digit(panel))
    if not comps:
        return None, 0.0
    if len(comps) > 2:
        comps = comps[:2]

    digits = []
    scores = []
    for mask in comps:
        cur = canonical(mask)
        best_label, best_score = None, 0.0
        for label, tmpl in templates.items():
            score = similarity(cur, canonical(tmpl))
            if score > best_score:
                best_score = score
                best_label = label
        if best_label is None:
            return None, 0.0
        digits.append(best_label)
        scores.append(best_score)

    value = 0
    for d in digits:
        value = value * 10 + d
    return value, sum(scores) / len(scores)
