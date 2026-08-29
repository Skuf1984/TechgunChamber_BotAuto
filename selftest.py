"""Deterministic checks for the laser power regulator against the luck/fail model.

    python selftest.py
"""

import json
import os
import sys
import tempfile
import time

import numpy as np

import chamber_bot
import vision

FAILURES = []


def check(name, got, expected, tolerance=0.02):
    ok = abs(got - expected) <= tolerance
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got:.3f} expected {expected:.3f}")
    if not ok:
        FAILURES.append(name)


def test_power_bar_colors():
    print("power bar: green fill reads 'ok', orange fill reads 'bad'")
    empty = (55, 55, 55)

    def bar(fill_fraction, color):
        img = np.full((62, 17, 3), empty, dtype=np.uint8)
        rows = int(round(62 * fill_fraction))
        if rows:
            img[62 - rows:, :] = color
        return img

    fills = {"ok": (128, 255, 128), "bad": (255, 127, 0)}
    fraction, name = vision.bar_fill_multi(bar(0.33, (128, 255, 128)), fills, empty, "up")
    check("green fill fraction", fraction, 0.33, 0.03)
    check("green named ok", float(name == "ok"), 1.0, 0.0)

    fraction, name = vision.bar_fill_multi(bar(0.56, (255, 127, 0)), fills, empty, "up")
    check("orange fill fraction", fraction, 0.56, 0.03)
    check("orange named bad", float(name == "bad"), 1.0, 0.0)

    fraction, name = vision.bar_fill_multi(bar(0.0, (128, 255, 128)), fills, empty, "up")
    check("empty fraction", fraction, 0.0, 0.0)
    check("empty unknown", float(name == "unknown"), 1.0, 0.0)


def test_marker():
    print("red target marker position on the scale")
    height, width = 62, 14
    img = np.full((height, width, 3), (55, 55, 55), dtype=np.uint8)
    check("no marker -> None", float(vision.marker_position(img, (255, 0, 0)) is None), 1.0, 0.0)

    target = 5 / 9
    centre = int(round((1 - target) * (height - 1)))
    img[max(0, centre - 1):centre + 2, :] = (255, 0, 0)
    got = vision.marker_position(img, (255, 0, 0), tolerance=70.0)
    check("marker at 5/9", got, target, 0.03)

    img[:] = (55, 55, 55)
    img[:2, :] = (255, 0, 0)
    got = vision.marker_position(img, (255, 0, 0), tolerance=70.0)
    check("marker at ceiling reads ~1.0", got, 1.0, 0.03)


def build_panel(cfg):
    panel = np.full((324, 398, 3), (198, 198, 198), dtype=np.uint8)

    scale = vision.crop(panel, cfg["rois"]["power_scale"])
    scale[:, :] = cfg["colors"]["power_empty"]
    rows = scale.shape[0]
    level_rows = int(round(rows * (3 / 9)))
    scale[rows - level_rows:, :] = cfg["colors"]["power_fill_bad"]

    # marker at power 5/9: place it on the scale (power_scale) but spanning the
    # marker search strip's x-range, so marker_fraction (scale-relative) reads 5/9
    sx, sy, sw, sh = cfg["rois"]["power_scale"]
    H, W = panel.shape[:2]
    sy0, sy1 = int(sy * H), int((sy + sh) * H)
    marker_row = sy1 - int(round((5 / 9) * (sy1 - sy0)))
    mx, my, mw, mh = cfg["rois"]["power_marker"]
    mx0, mx1 = int(mx * W), int((mx + mw) * W)
    panel[max(0, marker_row - 1):marker_row + 2, mx0:mx1] = cfg["colors"]["marker"]

    luck = vision.crop(panel, cfg["rois"]["luck_bar"])
    luck[:, :] = cfg["colors"]["luck_empty"]
    luck[:, : int(luck.shape[1] * 0.4)] = cfg["colors"]["luck_fill"]

    fail = vision.crop(panel, cfg["rois"]["fail_bar"])
    fail[:, :] = cfg["colors"]["fail_empty"]
    return panel


def test_read_state():
    print("read_state on a synthetic panel (power 3/9 orange, marker 5/9, luck 40%)")
    with open(chamber_bot.DEFAULT_CONFIG, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    state = chamber_bot.read_state(build_panel(cfg), cfg)
    print(f"  state: {state.line()}")
    check("gui open", float(state.gui_open), 1.0, 0.0)
    check("power fill 3/9", state.power_fill, 3 / 9, 0.03)
    check("power color bad (orange)", float(state.power_color == "bad"), 1.0, 0.0)
    check("marker 5/9", state.marker, 5 / 9, 0.03)
    check("offset +2/9", state.offset, 2 / 9, 0.03)
    check("luck 40%", state.luck_fill, 0.4, 0.02)
    check("fail 0%", state.fail_fill, 0.0, 0.0)
    return cfg


class MockController:
    def __init__(self):
        self.enabled = True
        self.actions = []

    def blocked_reason(self):
        return None

    def press(self, name, times=1):
        self.actions.append((name, times))
        return True

    def press_now(self, name, times=1, gap=0.0):
        self.actions.append((name, times))
        return True


def make_watchdog(cfg, target_crafts=0, stop_on_fail=True):
    watchdog = chamber_bot.Watchdog.__new__(chamber_bot.Watchdog)
    watchdog.cfg = cfg
    watchdog.alerter = chamber_bot.Alerter({"alerts": {"beep": False, "cooldown_seconds": 0.0}})
    watchdog.controller = MockController()
    watchdog.regulator = chamber_bot.PowerRegulator(cfg, watchdog.controller, watchdog.alerter)
    watchdog.state_log = None
    watchdog.digit_templates = {}
    watchdog.target_crafts = target_crafts
    watchdog.stop_on_fail = stop_on_fail
    watchdog.successes = 0
    watchdog.failures = 0
    watchdog._run_successes = 0
    watchdog.stop_reason = None
    watchdog._idle_since = None
    watchdog._grey_polls = 0
    watchdog.events = []
    watchdog._emit_cb = lambda event, data: watchdog.events.append((event, data))
    watchdog._saw_colored = False
    watchdog._peak_luck = 0.0
    watchdog._peak_fail = 0.0
    watchdog._reset_pending = False
    return watchdog


def active_state(cfg, power, marker, luck=0.4, fail=0.0, color="bad"):
    state = chamber_bot.ChamberState(timestamp=time.time(), gui_open=True)
    state.power_fill = power
    state.power_color = color
    state.marker = marker
    state.luck_fill = luck
    state.fail_fill = fail
    return state


def test_regulator_math(cfg):
    print("regulator step math")
    watchdog = make_watchdog(cfg)
    reg = watchdog.regulator
    step_frac = 1.0 / reg.nominal_steps
    check("nominal step = 1/steps", reg.step_fraction, step_frac, 0.001)
    check("clicks for 2 steps", float(reg.clicks_for(2 * step_frac)), 2.0, 0.0)
    check("clicks for half step rounds to 1", float(reg.clicks_for(0.5 * step_frac)), 1.0, 0.0)
    check("clicks capped", float(reg.clicks_for(1.0)), float(cfg["power"]["max_clicks_per_correction"]), 0.0)


def test_follow_marker(cfg):
    print("craft running, marker jumped above current power -> click plus")
    watchdog = make_watchdog(cfg)

    status = watchdog.evaluate(active_state(cfg, 3 / 9, 5 / 9))
    print(f"  status: {status}")
    check("status ADJUST plus x2", float(status == "ADJUST power_plus x2"), 1.0, 0.0)
    check("clicked plus", float(watchdog.controller.actions[-1] == ("power_plus", 2)), 1.0, 0.0)

    watchdog.controller.actions.clear()
    status = watchdog.evaluate(active_state(cfg, 5 / 9, 1 / 9))
    print(f"  status: {status}")
    check("marker below -> minus", float(status == "ADJUST power_minus x4"), 1.0, 0.0)

    watchdog.controller.actions.clear()
    status = watchdog.evaluate(active_state(cfg, 4 / 9, 4 / 9, color="ok"))
    check("aligned -> no clicks", float(status == "ALIGNED"), 1.0, 0.0)
    check("no action issued", float(len(watchdog.controller.actions)), 0.0, 0.0)


def test_mismatch_alert(cfg):
    print("orange power scale during a craft raises the mismatch alert")
    watchdog = make_watchdog(cfg)
    watchdog.evaluate(active_state(cfg, 0.3, 0.6, luck=0.4, color="bad"))
    check("mismatch alert fired", float("power_mismatch" in watchdog.alerter._last), 1.0, 0.0)


def test_craft_end_reset(cfg):
    print("craft runs (bars coloured) then bars go grey -> power resets to level 3")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0

    status = watchdog.evaluate(active_state(cfg, 5 / 9, 5 / 9, luck=0.4, fail=0.1, color="ok"))
    check("mid-craft tracked as active", float(status == "ALIGNED"), 1.0, 0.0)
    check("peaks recorded", float(watchdog._peak_luck), 0.4, 0.001)

    watchdog.read_power_fill = lambda: 0.0
    watchdog.regulator.step_fraction = 1 / 9

    status = watchdog.evaluate(active_state(cfg, 5 / 9, 5 / 9, luck=0.0, fail=0.0, color="ok"))
    check("first grey poll -> finishing", float(status == "FINISHING"), 1.0, 0.0)

    status = watchdog.evaluate(active_state(cfg, 5 / 9, 5 / 9, luck=0.0, fail=0.0, color="ok"))
    print(f"  status when bars stay grey: {status}")
    check("grey bars trigger reset", float(status == "RESET_DONE"), 1.0, 0.0)
    down = watchdog.regulator.nominal_steps + 2
    check("floor drive minus x%d" % down, float(watchdog.controller.actions[0] == ("power_minus", down)), 1.0, 0.0)
    check("stepped up x3", float(watchdog.controller.actions[-1] == ("power_plus", 3)), 1.0, 0.0)
    check("known level 3", float(watchdog.regulator.known_level), 3.0, 0.0)
    check("reset flag cleared", float(watchdog._reset_pending), 0.0, 0.0)
    check("saw_colored cleared", float(watchdog._saw_colored), 0.0, 0.0)

    watchdog.controller.actions.clear()
    status = watchdog.evaluate(active_state(cfg, 5 / 9, 5 / 9, luck=0.0, fail=0.0, color="ok"))
    check("still grey -> idle, no second reset", float(status == "IDLE"), 1.0, 0.0)
    check("no clicks while idle", float(len(watchdog.controller.actions)), 0.0, 0.0)

    status = watchdog.evaluate(active_state(cfg, 3 / 9, 5 / 9, luck=0.3, fail=0.0, color="bad"))
    check("new craft re-arms tracking", float(status.startswith("ADJUST")), 1.0, 0.0)


def test_fail_classification(cfg):
    print("fail bar leading when bars grey -> classified as failure")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0
    watchdog.evaluate(active_state(cfg, 3 / 9, 5 / 9, luck=0.1, fail=0.6, color="bad"))
    watchdog.read_power_fill = lambda: 0.0
    watchdog.evaluate(active_state(cfg, 3 / 9, 5 / 9, luck=0.0, fail=0.0, color="ok"))  # grey poll 1 -> FINISHING
    watchdog.evaluate(active_state(cfg, 3 / 9, 5 / 9, luck=0.0, fail=0.0, color="ok"))  # grey poll 2 -> complete
    check("fail alert fired", float("craft_fail" in watchdog.alerter._last), 1.0, 0.0)


def test_digit_reset_down(cfg):
    print("digit reset: reads 9, clicks minus x6, confirms 3")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0
    values = iter([(9, 1.0), (3, 1.0)])
    ok = watchdog.regulator.reset_to_default(lambda: next(values), lambda: 0.0)
    check("reset ok", float(ok), 1.0, 0.0)
    check("clicked minus x6", float(watchdog.controller.actions[0] == ("power_minus", 6)), 1.0, 0.0)
    check("known level 3", float(watchdog.regulator.known_level), 3.0, 0.0)


def test_digit_reset_from_ten(cfg):
    print("digit reset from 10 (two-digit): clicks minus x7, confirms 3")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0
    values = iter([(10, 1.0), (3, 1.0)])
    ok = watchdog.regulator.reset_to_default(lambda: next(values), lambda: 0.0)
    check("reset ok", float(ok), 1.0, 0.0)
    check("clicked minus x7", float(watchdog.controller.actions[0] == ("power_minus", 7)), 1.0, 0.0)


def test_digit_reset_up(cfg):
    print("digit reset upward: reads 1, clicks plus x2, confirms 3")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0
    values = iter([(1, 1.0), (3, 1.0)])
    ok = watchdog.regulator.reset_to_default(lambda: next(values), lambda: 0.0)
    check("reset ok", float(ok), 1.0, 0.0)
    check("clicked plus x2", float(watchdog.controller.actions[0] == ("power_plus", 2)), 1.0, 0.0)


def test_digit_unreadable_fallback(cfg):
    print("unreadable digit -> falls back to blind counted homing")
    watchdog = make_watchdog(cfg)
    watchdog.regulator.settle = 0.0
    ok = watchdog.regulator.reset_to_default(lambda: (None, 0.0), lambda: 0.0)
    check("fallback ok", float(ok), 1.0, 0.0)
    down = watchdog.regulator.nominal_steps + 2
    check("blind minus x%d" % down, float(watchdog.controller.actions[0] == ("power_minus", down)), 1.0, 0.0)


def _run_craft(watchdog, cfg, luck_peak, fail_peak):
    watchdog.evaluate(active_state(cfg, 4 / 9, 4 / 9, luck=luck_peak, fail=fail_peak, color="ok"))
    watchdog.read_power_fill = lambda: 0.0
    watchdog.read_power_value = lambda: (3, 1.0)
    watchdog.evaluate(active_state(cfg, 3 / 9, 4 / 9, luck=0.0, fail=0.0, color="ok"))  # grey poll 1 -> FINISHING
    watchdog.evaluate(active_state(cfg, 3 / 9, 4 / 9, luck=0.0, fail=0.0, color="ok"))  # grey poll 2 -> complete + reset


def test_target_stop(cfg):
    print("target crafts: 2 successes -> stop_reason target_reached")
    watchdog = make_watchdog(cfg, target_crafts=2)
    _run_craft(watchdog, cfg, 0.7, 0.2)  # success 1
    check("success 1 counted", float(watchdog.successes), 1.0, 0.0)
    check("no stop yet", float(watchdog.stop_reason is None), 1.0, 0.0)
    _run_craft(watchdog, cfg, 0.8, 0.1)  # success 2
    check("success 2 counted", float(watchdog.successes), 2.0, 0.0)
    check("stop at target", float(watchdog.stop_reason == "target_reached"), 1.0, 0.0)


def test_fail_stop(cfg):
    print("stop_on_fail: a failed craft halts the run")
    watchdog = make_watchdog(cfg, target_crafts=5, stop_on_fail=True)
    _run_craft(watchdog, cfg, 0.2, 0.8)  # failure
    check("failure counted", float(watchdog.failures), 1.0, 0.0)
    check("success not counted", float(watchdog.successes), 0.0, 0.0)
    check("stopped on fail", float(watchdog.stop_reason == "craft_failed"), 1.0, 0.0)


def test_fail_continue(cfg):
    print("stop_on_fail=False: failure logged, run continues")
    watchdog = make_watchdog(cfg, target_crafts=5, stop_on_fail=False)
    _run_craft(watchdog, cfg, 0.2, 0.8)  # failure
    check("failure counted", float(watchdog.failures), 1.0, 0.0)
    check("still running", float(watchdog.stop_reason is None), 1.0, 0.0)


def test_state_log():
    print("csv state log")
    path = os.path.join(tempfile.gettempdir(), "chamber_selftest.csv")
    if os.path.exists(path):
        os.remove(path)

    state = chamber_bot.ChamberState(timestamp=0.0, gui_open=True, power_fill=0.33, power_color="bad", marker=0.56)
    state.luck_fill = 0.4
    trace = chamber_bot.StateLog(path)
    trace.write(state, "ALIGNED")

    with open(path, "r", encoding="utf-8") as handle:
        rows = [line.strip().split(",") for line in handle if line.strip()]
    check("csv rows (header + 1)", float(len(rows)), 2.0, 0.0)
    check("csv header intact", float(rows[0] == chamber_bot.StateLog.HEADER), 1.0, 0.0)
    check("csv power value", float(rows[1][2]), 0.33, 0.0001)
    check("csv marker value", float(rows[1][4]), 0.56, 0.0001)
    os.remove(path)


def main():
    print("Reaction Chamber power regulator selftest\n")
    test_power_bar_colors()
    test_marker()
    cfg = test_read_state()
    test_regulator_math(cfg)
    test_follow_marker(cfg)
    test_mismatch_alert(cfg)
    test_craft_end_reset(cfg)
    test_fail_classification(cfg)
    test_digit_reset_down(cfg)
    test_digit_reset_from_ten(cfg)
    test_digit_reset_up(cfg)
    test_digit_unreadable_fallback(cfg)
    test_target_stop(cfg)
    test_fail_stop(cfg)
    test_fail_continue(cfg)
    test_state_log()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
