"""Template-based localization of the Reaction Chamber panel.

Why this exists: the old re-anchor found the largest grey blob and trusted its
bounding box. The chamber GUI is NOT all grey (dark slot rows, the right-hand
section), so the blob box could be a different rectangle than the panel the
zones were tuned on - and every normalized zone then landed in the wrong place.

Here the panel is re-found by matching a saved snapshot ("template") of the
panel itself. Only state-independent pixels are scored: the container
background grey and the dark static structure (tracks, slot frames, bevels).
Bar fills, the marker and the digit change colour while a craft runs, but they
stay 'not background' / 'not dark', so the match holds through every state.

The GUI scale can change when the game window is resized across a scale
boundary; Minecraft scales the whole GUI uniformly, so the template is
resampled at a few candidate scales and the best-scoring one wins. Normalized
ROIs stay valid at any scale once the panel box is right.
"""

import json
import os
from datetime import datetime

import numpy as np

import paths

BASE_DIR = paths.DATA_DIR
TEMPLATE_IMG = os.path.join(BASE_DIR, "panel_template.png")
TEMPLATE_JSON = os.path.join(BASE_DIR, "panel_template.json")

BG_GRAY = 198
BG_TOL = 18
DARK_MAX = 90
SEARCH_MARGIN = 80
MIN_SCORE = 0.80
EARLY_SCORE = 0.88
SCALE_MIN = 0.4
SCALE_MAX = 3.0

_cache = {"mtime": None, "template": None}


def _bg_mask(frame):
    return (np.abs(frame.astype(np.int32) - BG_GRAY).max(axis=2) <= BG_TOL)


def _dark_mask(frame):
    return frame.astype(np.int32).max(axis=2) < DARK_MAX


def _resample_mask(mask, width, height):
    """Nearest-neighbour resample of a bool mask to width x height."""
    src_h, src_w = mask.shape
    xs = np.minimum(src_w - 1, (np.arange(width) * src_w / width).astype(np.int64))
    ys = np.minimum(src_h - 1, (np.arange(height) * src_h / height).astype(np.int64))
    return mask[np.ix_(ys, xs)]


def _ncc_offset(long_prof, short_prof):
    """Best alignment offset of short_prof inside long_prof (normalized xcorr).

    Returns (offset, score) with score in [-1, 1]; (0, -1.0) when degenerate."""
    n_short = short_prof.size
    if long_prof.size < n_short or n_short < 4:
        return 0, -1.0
    a = long_prof - long_prof.mean()
    b = short_prof - short_prof.mean()
    ss_b = float((b * b).sum())
    if ss_b <= 1e-9:
        return 0, -1.0
    dots = np.correlate(a, b, mode="valid")
    sq = np.cumsum(np.concatenate(([0.0], a * a)))
    win_sq = sq[n_short:] - sq[:-n_short]
    valid = win_sq > 1e-9
    scores = np.full(dots.shape, -1.0)
    scores[valid] = dots[valid] / np.sqrt(win_sq[valid] * ss_b)
    idx = int(scores.argmax())
    return idx, float(scores[idx])


class PanelTemplate:
    def __init__(self, image, meta):
        self.image = np.ascontiguousarray(image)
        self.h, self.w = self.image.shape[:2]
        self.meta = meta or {}
        self.rois = self.meta.get("rois") or {}
        self.points = self.meta.get("points") or {}
        self.bg = _bg_mask(self.image)
        self.dark = _dark_mask(self.image)
        self.col_prof = self.bg.mean(axis=0)
        self.row_prof = self.bg.mean(axis=1)


def save_template(panel_img, rois=None, points=None):
    from PIL import Image

    panel_img = np.ascontiguousarray(panel_img.astype("uint8"))
    Image.fromarray(panel_img).save(TEMPLATE_IMG)
    meta = {
        "w": int(panel_img.shape[1]),
        "h": int(panel_img.shape[0]),
        "rois": rois or {},
        "points": points or {},
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(TEMPLATE_JSON, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    _cache["mtime"] = None
    _cache["template"] = None
    return meta


def load_template():
    """Load (and cache) the saved panel template. None when it doesn't exist."""
    if not (os.path.exists(TEMPLATE_IMG) and os.path.exists(TEMPLATE_JSON)):
        return None
    try:
        mtime = os.path.getmtime(TEMPLATE_IMG)
    except OSError:
        return None
    if _cache["template"] is not None and _cache["mtime"] == mtime:
        return _cache["template"]
    try:
        from PIL import Image

        image = np.asarray(Image.open(TEMPLATE_IMG).convert("RGB"))
        with open(TEMPLATE_JSON, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        template = PanelTemplate(image, meta)
    except Exception:  # noqa: BLE001 - a broken template must never crash the bot
        return None
    _cache["mtime"] = mtime
    _cache["template"] = template
    return template


def _largest_component(mask):
    """Bounding box (x, y, w, h) of the largest True region, or None."""
    try:
        import cv2

        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        if count <= 1:
            return None
        areas = stats[1:, cv2.CC_STAT_AREA]
        best = 1 + int(areas.argmax())
        x, y = int(stats[best, cv2.CC_STAT_LEFT]), int(stats[best, cv2.CC_STAT_TOP])
        w, h = int(stats[best, cv2.CC_STAT_WIDTH]), int(stats[best, cv2.CC_STAT_HEIGHT])
        return (x, y, w, h)
    except ImportError:
        import find_anchor

        mask_any = mask
        if int(mask_any.sum()) < 2000:
            return None
        comps = find_anchor._components(mask_any)
        if not comps:
            return None
        area, x0, y0, x1, y1 = comps[0]
        return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


# Minecraft GUI scales are integers, so the panel size only ever changes by a
# ratio of small whole numbers. Snapping the blob-based estimate onto these
# ratios keeps the resampled template from carrying a 1-2% scale drift.
SNAP_SCALES = (1 / 3, 0.5, 2 / 3, 0.75, 1.0, 1.25, 4 / 3, 1.5, 2.0, 2.5, 3.0)


def _candidate_scales(blob, tmpl):
    _, _, bw, bh = blob
    raw = [1.0, bw / tmpl.w, bh / tmpl.h, (bw / tmpl.w + bh / tmpl.h) / 2.0]
    scales = []
    for s in raw:
        s = max(SCALE_MIN, min(SCALE_MAX, float(s)))
        snapped = min(SNAP_SCALES, key=lambda q: abs(q - s))
        for cand in (s, snapped):
            if all(abs(cand - prev) > 0.004 for prev in scales):
                scales.append(cand)
    scales.sort(key=lambda s: abs(s - 1.0))
    return scales


def _score_offset(live_bg, live_dark, tmpl_bg, tmpl_dark, x, y):
    """Agreement of the scaled template masks with the frame at (x, y)."""
    th, tw = tmpl_bg.shape
    fh, fw = live_bg.shape
    if x < 0 or y < 0 or x + tw > fw or y + th > fh:
        return 0.0
    bg_win = live_bg[y:y + th, x:x + tw]
    dk_win = live_dark[y:y + th, x:x + tw]
    step = slice(None, None, 2)
    bg_agree = float((tmpl_bg[step, step] == bg_win[step, step]).mean())
    dk_agree = float((tmpl_dark[step, step] == dk_win[step, step]).mean())
    return (bg_agree + dk_agree) / 2.0


def find_panel(frame, tmpl):
    """Locate the template panel inside a captured frame.

    Returns (x, y, w, h) in frame coordinates, or None when the panel can't be
    found with enough confidence."""
    frame = np.asarray(frame)
    fh, fw = frame.shape[:2]
    if fh < tmpl.h // 2 or fw < tmpl.w // 2:
        return None

    live_bg = _bg_mask(frame)
    blob = _largest_component(live_bg)
    if blob is None:
        return None
    bx, by, bw, bh = blob
    if bw * bh < 1500:
        return None

    live_dark = _dark_mask(frame)
    best = None

    for scale in _candidate_scales(blob, tmpl):
        tw = int(round(tmpl.w * scale))
        th = int(round(tmpl.h * scale))
        if tw < 24 or th < 24 or tw > fw or th > fh:
            continue
        if scale == 1.0:
            t_col, t_row = tmpl.col_prof, tmpl.row_prof
            t_bg, t_dark = tmpl.bg, tmpl.dark
        else:
            t_bg = _resample_mask(tmpl.bg, tw, th)
            t_dark = _resample_mask(tmpl.dark, tw, th)
            t_col = t_bg.mean(axis=0)
            t_row = t_bg.mean(axis=1)

        sx0 = max(0, bx - SEARCH_MARGIN)
        sx1 = min(fw, bx + bw + SEARCH_MARGIN)
        sy0 = max(0, by - SEARCH_MARGIN)
        sy1 = min(fh, by + bh + SEARCH_MARGIN)
        if sx1 - sx0 < tw or sy1 - sy0 < th:
            sx0, sy0 = 0, 0
            sx1, sy1 = fw, fh
            if sx1 < tw or sy1 < th:
                continue

        region = live_bg[sy0:sy1, sx0:sx1]
        dx_rel, col_score = _ncc_offset(region.mean(axis=0), t_col)
        dy_rel, row_score = _ncc_offset(region.mean(axis=1), t_row)
        if col_score < 0.30 or row_score < 0.30:
            continue
        x0 = sx0 + dx_rel
        y0 = sy0 + dy_rel

        refined = None
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                score = _score_offset(live_bg, live_dark, t_bg, t_dark, x0 + dx, y0 + dy)
                if refined is None or score > refined[0]:
                    refined = (score, x0 + dx, y0 + dy)
        score, rx, ry = refined
        if best is None or score > best[0]:
            best = (score, rx, ry, tw, th)
        if scale == 1.0 and score >= EARLY_SCORE:
            break

    if best is None or best[0] < MIN_SCORE:
        return None
    score, rx, ry, tw, th = best
    return (rx, ry, tw, th)
