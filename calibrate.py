"""Interactive calibration for the Reaction Chamber watchdog.

Point the mouse, tap SPACE. ESC skips a step and keeps the current value.
Order: GUI panel anchor -> ROI rectangles -> button points -> reference colours.

    python calibrate.py                 full pass
    python calibrate.py --only anchor   just re-anchor the panel
    python calibrate.py --only colors   just re-sample colours

Open the Reaction Chamber GUI in windowed mode before starting.
"""

import argparse
import json
import sys
import time

import numpy as np

import chamber_bot
import mouse
import vision
import window

ROI_HELP = {
    "power_scale": "vertical laser POWER bar (fills bottom-up, green/orange)",
    "power_marker": "same vertical strip where the RED target marker slides",
    "luck_bar": "horizontal GREEN luck bar (fills left->right)",
    "fail_bar": "horizontal ORANGE fail bar just below the luck bar",
}

POINT_HELP = {
    "power_plus": "the '+' button (left of the two, raises power)",
    "power_minus": "the '-' button (right of the two, lowers power)",
}

COLOR_HELP = {
    "power_fill_ok": "the GREEN fill of the power bar (matched)",
    "power_fill_bad": "the ORANGE fill of the power bar (mismatched)",
    "power_empty": "the dark empty part of the power bar track",
    "marker": "the red target marker",
    "luck_fill": "the green fill of the luck bar",
    "luck_empty": "the grey empty part of the luck bar track",
    "fail_fill": "the orange fill of the fail bar",
    "fail_empty": "the grey empty part of the fail bar track",
    "container_gray": "the light grey container background",
    "track_gray": "the grey bar/slot background",
}


def prompt(keys, message):
    print(f"  {message}\n    SPACE = capture, ESC = skip", flush=True)
    return keys.wait("SPACE", abort="ESC")


def sample_color(capture, x, y, size=3):
    half = size // 2
    patch = capture.grab(x - half, y - half, size, size)
    median = np.median(patch.reshape(-1, 3), axis=0)
    return [int(round(float(v))) for v in median]


def calibrate_anchor(cfg, hwnd, keys):
    print("\n[1/4] GUI panel anchor")
    left, top, width, height = window.client_rect_on_screen(hwnd)
    print(f"  client area: {width}x{height} at ({left},{top})")

    if not prompt(keys, "hover the TOP-LEFT corner of the chamber panel"):
        print("  skipped")
        return
    x0, y0 = window.cursor_pos()
    time.sleep(0.2)

    if not prompt(keys, "hover the BOTTOM-RIGHT corner of the chamber panel"):
        print("  skipped")
        return
    x1, y1 = window.cursor_pos()
    time.sleep(0.2)

    ax, ay = min(x0, x1) - left, min(y0, y1) - top
    aw, ah = abs(x1 - x0), abs(y1 - y0)
    if aw < 20 or ah < 20:
        print(f"  rejected: panel {aw}x{ah} is too small")
        return
    cfg["anchor"] = {"x": int(ax), "y": int(ay), "w": int(aw), "h": int(ah)}
    print(f"  anchor = {cfg['anchor']}")


def _anchor_screen_rect(cfg, hwnd):
    anchor = cfg["anchor"]
    if not anchor.get("w") or not anchor.get("h"):
        raise SystemExit("anchor is not set yet - run calibration without --only, or --only anchor first")
    return vision.anchor_rect(
        window.client_rect_on_screen(hwnd), (anchor["x"], anchor["y"], anchor["w"], anchor["h"])
    )


def _to_normalized(rect, x, y):
    left, top, width, height = rect
    return (x - left) / width, (y - top) / height


def calibrate_rois(cfg, hwnd, keys, names=None):
    print("\n[2/4] ROI rectangles")
    rect = _anchor_screen_rect(cfg, hwnd)
    for name in names or list(cfg["rois"].keys()):
        if name not in cfg["rois"]:
            raise SystemExit(f"unknown roi {name!r}; known: {', '.join(cfg['rois'])}")
        print(f"\n  {name}: {ROI_HELP.get(name, '')}")
        if not prompt(keys, "hover the TOP-LEFT corner"):
            print("    kept existing")
            continue
        x0, y0 = window.cursor_pos()
        time.sleep(0.2)
        if not prompt(keys, "hover the BOTTOM-RIGHT corner"):
            print("    kept existing")
            continue
        x1, y1 = window.cursor_pos()
        time.sleep(0.2)

        nx0, ny0 = _to_normalized(rect, min(x0, x1), min(y0, y1))
        nx1, ny1 = _to_normalized(rect, max(x0, x1), max(y0, y1))
        roi = [round(nx0, 4), round(ny0, 4), round(max(0.002, nx1 - nx0), 4), round(max(0.002, ny1 - ny0), 4)]
        cfg["rois"][name] = roi
        print(f"    {name} = {roi}")


def calibrate_points(cfg, hwnd, keys, names=None):
    print("\n[3/4] button points")
    rect = _anchor_screen_rect(cfg, hwnd)
    for name in names or list(cfg["points"].keys()):
        if name not in cfg["points"]:
            raise SystemExit(f"unknown point {name!r}; known: {', '.join(cfg['points'])}")
        print(f"\n  {name}: {POINT_HELP.get(name, '')}")
        if not prompt(keys, "hover the centre of the button"):
            print("    kept existing")
            continue
        x, y = window.cursor_pos()
        time.sleep(0.2)
        nx, ny = _to_normalized(rect, x, y)
        cfg["points"][name] = [round(nx, 4), round(ny, 4)]
        print(f"    {name} = {cfg['points'][name]}")


def calibrate_colors(cfg, hwnd, keys, names=None):
    print("\n[4/4] reference colours")
    rect = _anchor_screen_rect(cfg, hwnd)
    wanted = {name: COLOR_HELP[name] for name in names} if names else COLOR_HELP
    with vision.ScreenCapture() as capture:
        for name, help_text in wanted.items():
            print(f"\n  {name}: {help_text}")
            if not prompt(keys, "hover a representative pixel"):
                print("    kept existing")
                continue
            x, y = window.cursor_pos()
            time.sleep(0.2)
            cfg["colors"][name] = sample_color(capture, x, y)
            print(f"    {name} = {cfg['colors'][name]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate the Reaction Chamber watchdog")
    parser.add_argument("--config", default=chamber_bot.DEFAULT_CONFIG)
    parser.add_argument(
        "--only",
        choices=["anchor", "rois", "points", "colors"],
        help="run a single calibration stage",
    )
    parser.add_argument("--roi", action="append", metavar="NAME", help="recalibrate one ROI (repeatable)")
    parser.add_argument("--point", action="append", metavar="NAME", help="recalibrate one button point")
    parser.add_argument("--color", action="append", metavar="NAME", help="resample one reference colour")
    args = parser.parse_args(argv)

    with open(args.config, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    hwnd = window.find_window(cfg["window_title"])
    print(f"target window: 0x{hwnd:08X}  {window.window_title(hwnd)}")
    if window.is_minimized(hwnd):
        raise SystemExit("the game window is minimized - restore it first")
    print("open the Reaction Chamber GUI now, then follow the prompts")

    keys = mouse.HotkeyEdge("SPACE", "ESC")

    if args.roi or args.point or args.color:
        if args.roi:
            calibrate_rois(cfg, hwnd, keys, args.roi)
        if args.point:
            calibrate_points(cfg, hwnd, keys, args.point)
        if args.color:
            unknown = [name for name in args.color if name not in COLOR_HELP]
            if unknown:
                raise SystemExit(f"unknown colour(s) {', '.join(unknown)}; known: {', '.join(COLOR_HELP)}")
            calibrate_colors(cfg, hwnd, keys, args.color)
    else:
        stages = {
            "anchor": calibrate_anchor,
            "rois": calibrate_rois,
            "points": calibrate_points,
            "colors": calibrate_colors,
        }
        order = [args.only] if args.only else ["anchor", "rois", "points", "colors"]
        for stage in order:
            stages[stage](cfg, hwnd, keys)

    chamber_bot.save_config(cfg, args.config)
    print(f"\nsaved {args.config}")
    print("verify with:  python chamber_bot.py --dump logs\\probe.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
