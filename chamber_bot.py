"""Techguns Reaction Chamber watchdog with laser power regulation.

Mechanic this bot serves:
  * the vertical scale on the right is the laser power level (green fill)
  * the red marker beside that scale is the power the chamber demands
  * the marker jumps up/down by random steps while a craft runs
  * matched  -> the scale stays green and the craft succeeds
  * mismatched -> the scale changes colour and the craft fails
  * the '+' and '-' buttons under the laser slot move the power one step

So the loop is: read marker, read current power, close the gap with +/- clicks,
and once the craft finishes put the power back to the default start level.

    python chamber_bot.py --once             single read
    python chamber_bot.py                    monitoring only, no clicks
    python chamber_bot.py --control          monitoring + power regulation
    python chamber_bot.py --dump logs\\probe.png
    python chamber_bot.py --home             calibrate the step size, then set default

Hotkeys while running: F9 pause/resume, F8 toggle control, F10 quit.
"""

import argparse
import csv
import json
import logging
import os
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler

import numpy as np

import digit
import mouse
import paths
import vision
import window

BASE_DIR = paths.DATA_DIR
DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

log = logging.getLogger("chamber")


@dataclass
class ChamberState:
    timestamp: float
    gui_open: bool
    power_fill: float = 0.0
    power_color: str = "unknown"
    marker: float = -1.0
    luck_fill: float = 0.0
    fail_fill: float = 0.0

    @property
    def has_marker(self):
        return self.marker >= 0.0

    @property
    def offset(self):
        """Marker minus current power. Positive means the power must go up."""
        return self.marker - self.power_fill if self.has_marker else 0.0

    def line(self):
        if not self.gui_open:
            return "gui=closed"
        marker = f"{self.marker:5.1%}" if self.has_marker else "  n/a"
        return (
            f"power={self.power_fill:5.1%}({self.power_color}) mark={marker} "
            f"off={self.offset:+.3f} luck={self.luck_fill:5.1%} fail={self.fail_fill:5.1%}"
        )


def safe_console():
    """Window titles carry zero-width and emoji junk; never die on the console codec."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging(verbose):
    safe_console()
    os.makedirs(LOG_DIR, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(stream)

    rotating = RotatingFileHandler(
        os.path.join(LOG_DIR, "chamber.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(rotating)


def setup_logging_gui():
    """File-only logging for the windowed GUI app (there is no console stdout)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    rotating = RotatingFileHandler(
        os.path.join(LOG_DIR, "chamber.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(rotating)


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    anchor = cfg.get("anchor", {})
    if not anchor.get("w") or not anchor.get("h"):
        raise SystemExit(
            "config anchor is empty - run 'python calibrate.py' first "
            "(it records where the Reaction Chamber panel sits inside the game window)"
        )
    return cfg


def save_config(cfg, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def anchor_tuple(cfg):
    anchor = cfg["anchor"]
    return (anchor["x"], anchor["y"], anchor["w"], anchor["h"])


def gui_is_open(frame, cfg):
    """The chamber GUI is a wall of container/track grey. When it closes the panel
    shows the game world instead, so grey fraction is a robust open/closed signal."""
    colors = cfg["colors"]
    minimum = float(cfg["thresholds"]["gui_gray_min_fraction"])
    grey = 0.0
    for ref in (colors["container_gray"], colors["track_gray"]):
        ref_arr = np.asarray(ref, dtype=np.int32)
        grey += (np.abs(frame.astype(np.int32) - ref_arr).max(axis=-1) <= 16).mean()
    return float(grey) >= minimum


def marker_fraction(frame, cfg):
    """Locate the red marker inside the (deliberately larger) power_marker ROI, then
    express its position as a fraction of the power_scale ROI. Measuring against the
    scale keeps the marker comparable to the fill even though the search zone is
    bigger than the scale itself."""
    colors = cfg["colors"]
    rois = cfg["rois"]
    tol = float(cfg["power"]["marker_tolerance"])
    h, w = frame.shape[:2]

    mx, my, mw, mh = rois["power_marker"]
    mx0, my0 = int(mx * w), int(my * h)
    mx1, my1 = int((mx + mw) * w), int((my + mh) * h)
    crop = frame[my0:my1, mx0:mx1]
    if crop.size == 0:
        return None
    dist = np.sqrt(vision._dist2(crop, colors["marker"]).astype(np.float32))
    weights = (dist < tol).sum(axis=1).astype(np.float64)
    if weights.sum() <= 0:
        return None
    idx = np.arange(weights.size, dtype=np.float64)
    center_row = float((idx * weights).sum() / weights.sum())
    marker_row = my0 + center_row

    sx, sy, sw, sh = rois["power_scale"]
    sy0, sy1 = int(sy * h), int((sy + sh) * h)
    span = float(sy1 - sy0)
    if span <= 0:
        return None
    frac = (sy1 - marker_row) / span
    return float(min(1.0, max(0.0, frac)))


def read_state(frame, cfg):
    colors = cfg["colors"]
    power_cfg = cfg["power"]
    rois = cfg["rois"]

    state = ChamberState(timestamp=time.time(), gui_open=gui_is_open(frame, cfg))
    if not state.gui_open:
        return state

    # max_gap bridges the dark tick lines drawn across the scale every few rows -
    # without it the contiguous run always stops at the first tick (~9%).
    state.power_fill, state.power_color = vision.bar_fill_multi(
        vision.crop(frame, rois["power_scale"]),
        {"ok": colors["power_fill_ok"], "bad": colors["power_fill_bad"]},
        colors["power_empty"],
        "up",
        max_gap=3,
    )

    marker = marker_fraction(frame, cfg)
    state.marker = -1.0 if marker is None else float(marker)

    state.luck_fill = vision.bar_edge(
        vision.crop(frame, rois["luck_bar"]), colors["luck_fill"], colors["luck_empty"], "right"
    )
    state.fail_fill = vision.bar_edge(
        vision.crop(frame, rois["fail_bar"]), colors["fail_fill"], colors["fail_empty"], "right"
    )
    return state


class Alerter:
    def __init__(self, cfg):
        self._cooldown = float(cfg["alerts"]["cooldown_seconds"])
        self._beep = bool(cfg["alerts"]["beep"])
        self._last = {}

    def fire(self, key, message, level=logging.WARNING):
        now = time.time()
        if now - self._last.get(key, 0.0) < self._cooldown:
            return False
        self._last[key] = now
        log.log(level, "ALERT %s: %s", key, message)
        if self._beep:
            try:
                import winsound

                winsound.Beep(880 if level < logging.ERROR else 1320, 220)
            except Exception:
                pass
        return True

    def clear(self, key):
        self._last.pop(key, None)


class StateLog:
    """Append-only CSV trace of every poll - the thing you grep after a failed craft.
    Rotates at max_bytes (keeps .1/.2/.3 backups), same policy as chamber.log."""

    HEADER = [
        "time", "status", "power", "power_color", "marker", "offset",
        "luck", "fail",
    ]
    MAX_BYTES = 2_000_000
    BACKUPS = 3

    def __init__(self, path):
        self.path = path
        self._needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
        self._rotate_if_needed()

    def _rotate_if_needed(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) < self.MAX_BYTES:
                return
            for i in range(self.BACKUPS, 0, -1):
                src = self.path if i == 1 else f"{self.path}.{i}"
                dst = f"{self.path}.{i + 1}" if i < self.BACKUPS else f"{self.path}.{self.BACKUPS}"
                if os.path.exists(src):
                    os.replace(src, dst)
            self._needs_header = True
        except OSError:
            pass

    def write(self, state, status):
        self._rotate_if_needed()
        with open(self.path, "a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if self._needs_header:
                writer.writerow(self.HEADER)
                self._needs_header = False
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                status,
                f"{state.power_fill:.4f}",
                state.power_color,
                f"{state.marker:.4f}" if state.has_marker else "",
                f"{state.offset:+.4f}",
                f"{state.luck_fill:.4f}",
                f"{state.fail_fill:.4f}",
            ])


class Controller:
    """Clicks the GUI buttons. Rate limited, foreground guarded, disabled by default."""

    def __init__(self, cfg, hwnd, enabled):
        self.cfg = cfg
        self.hwnd = hwnd
        self.enabled = enabled
        self._last_action = 0.0
        self.interrupted = None  # callable; the watchdog wires its stop/quit check in
        control = cfg["control"]
        self._interval = float(control["action_interval"])
        self._require_foreground = bool(control["require_foreground"])

    def blocked_reason(self):
        if not self.enabled:
            return "control disabled (--control or F8 to enable)"
        if self._require_foreground and not window.is_foreground(self.hwnd):
            return "game window is not focused"
        return None

    def _screen_point(self, name):
        point = self.cfg["points"].get(name)
        if not point:
            raise KeyError(f"no calibrated point named {name!r}")
        rect = vision.anchor_rect(window.client_rect_on_screen(self.hwnd), anchor_tuple(self.cfg))
        return vision.point_in(rect, point)

    def press_now(self, name, times=1, gap=0.07):
        """Ignores the rate limiter - used by the homing routine. Checks the
        interrupt hook between clicks so Stop/F10 lands mid-burst instead of
        waiting for a 12-click homing run to finish."""
        if self.blocked_reason():
            return False
        x, y = self._screen_point(name)
        for i in range(max(1, int(times))):
            if i and self.interrupted is not None and self.interrupted():
                log.info("click burst interrupted after %d click(s)", i)
                return False
            mouse.click(x, y)
            time.sleep(gap)
        self._last_action = time.time()
        return True

    def press(self, name, times=1):
        if self.blocked_reason() or time.time() - self._last_action < self._interval:
            return False
        return self.press_now(name, times)


class PowerRegulator:
    """Closes the gap between the current laser power and the red target marker.

    Everything is measured in bar-fill fractions, so the regulator never needs to
    know the absolute level to track the marker. The absolute level only matters
    for the post-craft reset, and that is solved by homing to the bottom of the
    scale and stepping up a counted number of times - a stepper-motor approach
    that cannot drift.
    """

    def __init__(self, cfg, controller, alerter):
        power = cfg["power"]
        self.controller = controller
        self.alerter = alerter
        self.tolerance = float(power["match_tolerance"])
        self.settle = float(power["settle_seconds"])
        self.max_clicks = int(power["max_clicks_per_correction"])
        self.default_level = int(power["default_level"])
        self.nominal_steps = max(1, int(power["steps"]))
        self.step_fraction = 1.0 / self.nominal_steps
        self.known_level = None

    def clicks_for(self, offset):
        magnitude = abs(offset) / max(1e-6, self.step_fraction)
        return max(1, min(self.max_clicks, int(round(magnitude))))

    def aligned(self, state):
        return state.has_marker and abs(state.offset) <= self.tolerance

    def follow_marker(self, state):
        """One correction burst per poll. The next poll verifies the result."""
        if not state.has_marker:
            return None
        offset = state.offset
        if abs(offset) <= self.tolerance:
            return None

        button = "power_plus" if offset > 0 else "power_minus"
        clicks = self.clicks_for(offset)
        if not self.controller.press(button, clicks):
            return None
        if self.known_level is not None:
            self.known_level += clicks if offset > 0 else -clicks
        return f"{button} x{clicks}"

    def settled_fill(self, read_fill):
        """Read the fill, then wait and read again so the GUI has time to redraw.

        A single read right after a click often returns the pre-click value - that
        stale read is what made the old floor detection stop one click too early."""
        time.sleep(self.settle)
        read_fill()
        time.sleep(0.15)
        return read_fill()

    def reset_to_default(self, read_value, read_fill):
        """Reset the laser power to the default level after a craft.

        Reads the actual number shown beside the scale and clicks exactly the gap,
        then re-reads to confirm. If the number is unreadable, falls back to the
        blind counted homing."""
        target = self.default_level

        for attempt in range(4):
            value, conf = read_value()
            if value is None:
                log.info("power digit unreadable - falling back to counted homing")
                return self._blind_home(read_fill)
            if value == target:
                self.known_level = value
                log.info("power reset to level %d (conf %.2f)", value, conf)
                return True
            if attempt == 3:
                break
            delta = target - value
            button = "power_plus" if delta > 0 else "power_minus"
            log.info("power %d -> %d: %s x%d (conf %.2f)", value, target, button, abs(delta), conf)
            if not self.controller.press_now(button, abs(delta), gap=self.settle):
                log.warning("reset clicks aborted: %s", self.controller.blocked_reason())
                return False
            time.sleep(self.settle)

        log.warning("digit reset could not confirm level %d - falling back to counted homing", target)
        return self._blind_home(read_fill)

    def _blind_home(self, read_fill):
        """Counted homing: drive to the floor with minus presses, identify the floor
        level with one settled fill read, then step up to the default."""
        target = self.default_level
        down = self.nominal_steps + 2

        if not self.controller.press_now("power_minus", down, gap=self.settle):
            log.warning("reset aborted on floor drive: %s", self.controller.blocked_reason())
            return False

        floor_fill = self.settled_fill(read_fill)
        floor_level = int(round(floor_fill / self.step_fraction)) if self.step_fraction > 0 else 0
        if floor_level < 0:
            floor_level = 0

        presses = target - floor_level
        if presses > 0:
            if not self.controller.press_now("power_plus", presses, gap=self.settle):
                log.warning("reset aborted on rise: %s", self.controller.blocked_reason())
                return False
        elif presses < 0:
            log.warning("floor_level %d is above default %d - check power.steps/floor", floor_level, target)

        self.known_level = target
        log.info(
            "power homed to level %d: minus x%d, floor_fill=%.3f floor_level=%d, plus x%d",
            target, down, floor_fill, floor_level, max(0, presses),
        )
        return True

    def home(self, read_fill):
        """Kept for the --home calibration command: runs a blind homing and reports."""
        ok = self._blind_home(read_fill)
        return 0.0 if ok else None


class Watchdog:
    def __init__(self, cfg, control_enabled, csv_enabled=True, hotkeys=None,
                 target_crafts=0, stop_on_fail=True, emit=None):
        self.cfg = cfg
        self.capture = vision.ScreenCapture()
        self.hwnd = window.find_window(cfg["window_title"])
        self.controller = Controller(cfg, self.hwnd, control_enabled)
        self.controller.interrupted = self._interrupted
        self.alerter = Alerter(cfg)
        self.regulator = PowerRegulator(cfg, self.controller, self.alerter)
        hk = hotkeys or {"toggle_control": "F8", "pause": "F9", "quit": "F10"}
        self.hotkeys = {role: key.upper() for role, key in hk.items()}
        self.keys = mouse.HotkeyEdge(*set(self.hotkeys.values()))
        self.digit_templates = digit.load_templates()
        if not self.digit_templates:
            log.warning("no digit templates (run calibrate_digits.py) - reset will use counted homing")
        self.state_log = StateLog(os.path.join(LOG_DIR, "chamber.csv")) if csv_enabled else None
        self.target_crafts = int(target_crafts)
        self.stop_on_fail = bool(stop_on_fail)
        self.successes = 0
        self.failures = 0
        # successes made by THIS run only (Start -> Stop). successes/failures hold
        # the lifetime total; the target must be counted against the run counter,
        # otherwise an old lifetime total eats the target.
        self._run_successes = 0
        self.stop_reason = None
        self._emit_cb = emit
        self.paused = False
        self._stop_requested = False
        self._saw_colored = False
        self._grey_polls = 0
        self._peak_luck = 0.0
        self._peak_fail = 0.0
        self._reset_pending = False
        self._blank_warned = False
        self._idle_since = None
        self._craft_started_at = None
        self._stalled_fired = False

    def _emit(self, event, data=None):
        if self._emit_cb is not None:
            try:
                self._emit_cb(event, data or {})
            except Exception:  # noqa: BLE001 - GUI must never crash the bot
                log.exception("emit callback failed for %s", event)

    def request_stop(self, reason="user_stop"):
        self.stop_reason = self.stop_reason or reason
        self._stop_requested = True

    def _interrupted(self):
        """Stop requested or quit hotkey tapped. Checked between the clicks of a
        long burst (power reset / homing) so Stop and F10 don't have to wait for
        the whole run to finish."""
        if self._stop_requested:
            return True
        quit_key = self.hotkeys.get("quit", "F10")
        if self.keys.pressed(quit_key):
            log.info("%s - shutting down", quit_key)
            self.stop_reason = self.stop_reason or "user_quit"
            return True
        return False

    def close(self):
        self.capture.close()

    def grab(self):
        rect = vision.anchor_rect(window.client_rect_on_screen(self.hwnd), anchor_tuple(self.cfg))
        frame = self.capture.grab(*rect)
        if vision.is_blank(frame):
            if not self._blank_warned:
                log.error("capture is blank - switch Minecraft to windowed mode")
                self._blank_warned = True
        else:
            self._blank_warned = False
        return frame

    def read(self):
        return read_state(self.grab(), self.cfg)

    def read_power_fill(self):
        return self.read().power_fill

    def read_power_value(self):
        """Read the actual power number shown beside the scale. (value, confidence).
        The digit ROI is re-read from config.json each call so ROI-editor changes
        take effect without a restart."""
        if not self.digit_templates:
            return None, 0.0
        return digit.read_number(self.grab(), self.digit_templates, digit.load_digit_roi_norm())

    def try_reanchor(self):
        """Re-detect the chamber container and update the anchor. Lets the bot cope
        with the window being moved/resized without manual recalibration.

        The saved panel template (panel_template.png) is matched first - it lands
        the exact panel box the zones were tuned on, at any GUI scale. The grey
        blob detector stays as a fallback when there is no template."""
        import find_anchor  # local import to avoid a circular import at module load
        import panel_match

        now = time.time()
        if now - getattr(self, "_last_reanchor", 0.0) < 3.0:
            return False
        self._last_reanchor = now
        try:
            left, top, width, height = window.client_rect_on_screen(self.hwnd)
            with vision.ScreenCapture() as capture:
                frame = capture.grab(left, top, width, height)
            found = None
            template = panel_match.load_template()
            if template is not None:
                found = panel_match.find_panel(frame, template)
            if found is None:
                found = find_anchor.find_container(frame)
        except Exception:  # noqa: BLE001
            return False
        if found is None:
            return False
        x, y, w, h = found
        self.cfg["anchor"] = {"x": x, "y": y, "w": w, "h": h}
        try:
            save_config(self.cfg, DEFAULT_CONFIG)
        except Exception:  # noqa: BLE001
            pass
        log.info("re-anchored container to x%d y%d %dx%d", x, y, w, h)
        self._emit("reanchored", {"x": x, "y": y, "w": w, "h": h})
        return True

    def craft_active(self, state):
        epsilon = float(self.cfg["thresholds"]["craft_active_epsilon"])
        return state.luck_fill > epsilon or state.fail_fill > epsilon

    def evaluate(self, state):
        if not state.gui_open:
            # the window may have moved/resized - try to find the container again
            self.try_reanchor()
            return "WAIT"

        active = self.craft_active(state)

        if active:
            self._saw_colored = True
            self._grey_polls = 0
            self._idle_since = None
            if self._craft_started_at is None:
                self._craft_started_at = state.timestamp
            stall_limit = float(self.cfg["thresholds"].get("stall_seconds", 60.0))
            if not self._stalled_fired and state.timestamp - self._craft_started_at > stall_limit:
                self._stalled_fired = True
                self.alerter.fire(
                    "craft_stalled",
                    f"craft has been running for {state.timestamp - self._craft_started_at:.0f}s "
                    f"without completing (stall limit {stall_limit:.0f}s)",
                    logging.ERROR,
                )
                self._emit("craft_stalled", {
                    "stalled_seconds": state.timestamp - self._craft_started_at,
                })
            if state.luck_fill > self._peak_luck:
                self._peak_luck = state.luck_fill
            if state.fail_fill > self._peak_fail:
                self._peak_fail = state.fail_fill
        elif self._saw_colored:
            # The bars were coloured and just turned grey: the craft ended. The
            # bars can jump +/-40% between polls, so a fixed high-water threshold
            # is unreliable - the colour draining away is the dependable signal.
            # Require two consecutive grey polls so a one-poll blink can't fire a
            # false completion.
            self._grey_polls += 1
            if self._grey_polls < 2:
                return "FINISHING"
            self._saw_colored = False
            self._grey_polls = 0
            success = self._peak_luck >= self._peak_fail
            if success:
                self.successes += 1
                self._run_successes += 1
                self.alerter.fire(
                    "craft_success", f"bars went grey, luck peaked at {self._peak_luck:.0%} - craft succeeded", logging.INFO
                )
                self._emit("craft_success", {"successes": self.successes, "peak_luck": self._peak_luck})
                if self.target_crafts and self._run_successes >= self.target_crafts:
                    self.stop_reason = "target_reached"
                    log.info("target of %d successful crafts reached this run - stopping", self.target_crafts)
            else:
                self.failures += 1
                self.alerter.fire(
                    "craft_fail", f"bars went grey, fail peaked at {self._peak_fail:.0%} - craft failed", logging.ERROR
                )
                self._emit("craft_fail", {"failures": self.failures, "peak_fail": self._peak_fail})
                if self.stop_on_fail:
                    self.stop_reason = "craft_failed"
            log.info("craft completed - queueing power reset to level %d", self.regulator.default_level)
            self._peak_luck = 0.0
            self._peak_fail = 0.0
            self._idle_since = None
            self._craft_started_at = None
            self._stalled_fired = False
            if bool(self.cfg["power"]["reset_after_craft"]):
                self._reset_pending = True

        if self._reset_pending:
            reason = self.controller.blocked_reason()
            if reason:
                self.alerter.fire("blocked_reset", f"power reset pending but {reason}")
                return "RESET_PENDING"
            if self.regulator.reset_to_default(self.read_power_value, self.read_power_fill):
                self._reset_pending = False
                return "RESET_DONE"
            return "RESET_FAILED"

        if active:
            if not state.has_marker:
                self.alerter.fire("no_marker", "red target marker not found in the marker ROI")
                return "NO_MARKER"
            self.alerter.clear("no_marker")

            if state.power_color == "bad":
                self.alerter.fire("power_mismatch", "power scale orange - level does not match the marker")
            else:
                self.alerter.clear("power_mismatch")

            if self.regulator.aligned(state):
                return "ALIGNED"

            reason = self.controller.blocked_reason()
            if reason:
                self.alerter.fire("blocked", f"power needs {state.offset:+.3f} but {reason}")
                return "NEEDS_ADJUST"

            action = self.regulator.follow_marker(state)
            return f"ADJUST {action}" if action else "ALIGNED"

        # Idle with the GUI open: while a run is in progress this usually means the
        # input slot is empty and needs a refill (manual feeding). Fires at most once
        # per alert cooldown, never every poll.
        run_in_progress = not self.stop_reason and (self.target_crafts == 0 or self._run_successes < self.target_crafts)
        if run_in_progress:
            if self._idle_since is None:
                self._idle_since = state.timestamp
            elif state.timestamp - self._idle_since > 10:
                if self.alerter.fire("waiting_input", "idle - add ingredients to start the next craft"):
                    self._emit("waiting_input", {"idle_seconds": state.timestamp - self._idle_since})
        else:
            self._idle_since = None

        return "IDLE"

    def handle_hotkeys(self):
        quit_key = self.hotkeys.get("quit", "F10")
        pause_key = self.hotkeys.get("pause", "F9")
        control_key = self.hotkeys.get("toggle_control", "F8")
        if self.keys.pressed(quit_key):
            log.info("%s - shutting down", quit_key)
            self.stop_reason = self.stop_reason or "user_quit"
            return False
        if self.keys.pressed(pause_key):
            self.paused = not self.paused
            log.info("%s - %s", pause_key, "paused" if self.paused else "resumed")
            self._emit("paused", {"paused": self.paused})
        if self.keys.pressed(control_key):
            self.controller.enabled = not self.controller.enabled
            log.info("%s - control %s", control_key, "ENABLED" if self.controller.enabled else "disabled")
            self._emit("control_toggled", {"enabled": self.controller.enabled})
        return True

    def run(self):
        interval = float(self.cfg["poll_interval"])
        log.info(
            "watching hwnd=0x%X control=%s interval=%.2fs default_level=%d target=%s",
            self.hwnd,
            "on" if self.controller.enabled else "off",
            interval,
            self.regulator.default_level,
            self.target_crafts or "unlimited",
        )
        log.info(
            "%s pause | %s toggle control | %s quit",
            self.hotkeys.get("pause", "F9"),
            self.hotkeys.get("toggle_control", "F8"),
            self.hotkeys.get("quit", "F10"),
        )

        misses = 0
        while not self._stop_requested:
            if not self.handle_hotkeys():
                break
            if self.paused:
                time.sleep(0.1)
                continue

            try:
                frame = self.grab()
            except (window.WindowNotFound, vision.CaptureError, OSError) as exc:
                misses += 1
                log.error("capture failed (%d): %s", misses, exc)
                if misses >= 5:
                    try:
                        self.hwnd = window.find_window(self.cfg["window_title"])
                        self.controller.hwnd = self.hwnd
                        misses = 0
                        log.info("re-acquired window hwnd=0x%X", self.hwnd)
                    except window.WindowNotFound:
                        log.error("Minecraft window is gone - waiting")
                time.sleep(1.0)
                continue

            misses = 0
            state = read_state(frame, self.cfg)
            status = self.evaluate(state)
            log.info("%-22s %s", status, state.line())
            self._emit("state", {
                "status": status,
                "gui_open": state.gui_open,
                "power_fill": state.power_fill,
                "power_color": state.power_color,
                "marker": state.marker,
                "luck_fill": state.luck_fill,
                "fail_fill": state.fail_fill,
                "successes": self.successes,
                "failures": self.failures,
                "paused": self.paused,
                "control_enabled": self.controller.enabled,
            })
            if self.state_log and state.gui_open:
                self.state_log.write(state, status)

            if self.stop_reason:
                log.info("stopping: %s", self.stop_reason)
                self._emit("stopped", {
                    "reason": self.stop_reason,
                    "successes": self.successes,
                    "failures": self.failures,
                    "run_successes": self._run_successes,
                })
                break
            time.sleep(interval)


def save_bmp(path, rgb):
    height, width = rgb.shape[:2]
    row_stride = (width * 3 + 3) & ~3
    padding = row_stride - width * 3
    bgr = rgb[::-1, :, ::-1]

    rows = bytearray()
    pad = b"\x00" * padding
    for row in bgr:
        rows += row.tobytes()
        rows += pad

    pixel_offset = 54
    header = struct.pack("<2sIHHI", b"BM", pixel_offset + len(rows), 0, 0, pixel_offset)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(rows), 0, 0, 0, 0)
    with open(path, "wb") as handle:
        handle.write(header + info + bytes(rows))


def save_image(path, rgb):
    """PNG when Pillow is available, BMP otherwise. Returns the path written."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg"):
        try:
            from PIL import Image
        except ImportError:
            path = os.path.splitext(path)[0] + ".bmp"
            log.warning("Pillow is not installed - writing %s instead", path)
        else:
            Image.fromarray(rgb).save(path)
            return path
    save_bmp(path, rgb)
    return path


def draw_outline(frame, roi, color):
    height, width = frame.shape[:2]
    x0 = max(0, min(width - 1, int(round(roi[0] * width))))
    y0 = max(0, min(height - 1, int(round(roi[1] * height))))
    x1 = max(x0 + 1, min(width - 1, int(round((roi[0] + roi[2]) * width))))
    y1 = max(y0 + 1, min(height - 1, int(round((roi[1] + roi[3]) * height))))
    frame[y0, x0:x1] = color
    frame[y1, x0:x1] = color
    frame[y0:y1, x0] = color
    frame[y0:y1, x1] = color


def dump_probe(cfg, path):
    hwnd = window.find_window(cfg["window_title"])
    rect = vision.anchor_rect(window.client_rect_on_screen(hwnd), anchor_tuple(cfg))
    with vision.ScreenCapture() as capture:
        frame = capture.grab(*rect)
    marked = frame.copy()
    palette = [
        (255, 0, 0), (0, 255, 0), (0, 128, 255), (255, 255, 0),
        (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 255, 128), (255, 255, 255),
    ]
    for index, (name, roi) in enumerate(cfg["rois"].items()):
        draw_outline(marked, roi, palette[index % len(palette)])
        log.info("roi %-16s %s", name, [round(v, 4) for v in roi])
    for name, point in cfg["points"].items():
        px = int(round(point[0] * frame.shape[1]))
        py = int(round(point[1] * frame.shape[0]))
        marked[max(0, py - 2):py + 3, max(0, px - 2):px + 3] = (255, 255, 255)
        log.info("point %-16s %s", name, [round(v, 4) for v in point])
    written = save_image(path, marked)
    log.info("wrote %s (%dx%d)", written, frame.shape[1], frame.shape[0])

    state = read_state(frame, cfg)
    log.info("state now: %s", state.line())
    if vision.is_blank(frame):
        log.error("capture is blank - use windowed mode, not exclusive fullscreen")


def dump_client(title, path):
    hwnd = window.find_window(title)
    left, top, width, height = window.client_rect_on_screen(hwnd)
    with vision.ScreenCapture() as capture:
        frame = capture.grab(left, top, width, height)
    written = save_image(path, frame)
    log.info("wrote %s (client %dx%d at %d,%d)", written, width, height, left, top)
    if vision.is_blank(frame):
        log.error("capture is blank - switch Minecraft to windowed mode")
    else:
        log.info("capture looks live (stddev %.1f)", float(frame.astype(np.float32).std()))


def run_homing(cfg, config_path):
    watchdog = Watchdog(cfg, control_enabled=True, csv_enabled=False)
    try:
        state = watchdog.read()
        if not state.gui_open:
            raise SystemExit("the Reaction Chamber GUI is not open")
        if watchdog.craft_active(state):
            raise SystemExit("a craft is running - homing would wreck it; wait until it finishes")
        for _ in range(3):
            window.focus(watchdog.hwnd)
            time.sleep(0.4)
            if window.is_foreground(watchdog.hwnd):
                break
        reason = watchdog.controller.blocked_reason()
        if reason:
            raise SystemExit(f"cannot click: {reason}")

        if not watchdog.regulator.reset_to_default(watchdog.read_power_value, watchdog.read_power_fill):
            raise SystemExit("reset failed")
        value, conf = watchdog.read_power_value()
        log.info("reset done - digit reads %s (conf %.2f), regulator level %s",
                 value, conf, watchdog.regulator.known_level)
    finally:
        watchdog.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Techguns Reaction Chamber watchdog")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--control", action="store_true", help="allow the bot to click the +/- buttons")
    parser.add_argument("--once", action="store_true", help="read the state once and exit")
    parser.add_argument("--home", action="store_true", help="home the power scale, learn the step, set the default level")
    parser.add_argument("--dump", metavar="FILE.png", help="save a capture with ROI outlines")
    parser.add_argument(
        "--client-dump", metavar="FILE.png", help="save the whole game client area (no calibration needed)"
    )
    parser.add_argument("--list-windows", action="store_true", help="print visible window titles")
    parser.add_argument("--no-csv", action="store_true", help="do not append logs/chamber.csv")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.list_windows:
        for hwnd, title in sorted(window.list_windows(), key=lambda item: item[1].lower()):
            print(f"0x{hwnd:08X}  {title}")
        return 0

    if args.client_dump:
        with open(args.config, "r", encoding="utf-8") as handle:
            title = json.load(handle)["window_title"]
        dump_client(title, args.client_dump)
        return 0

    cfg = load_config(args.config)

    if args.dump:
        dump_probe(cfg, args.dump)
        return 0

    if args.once:
        hwnd = window.find_window(cfg["window_title"])
        rect = vision.anchor_rect(window.client_rect_on_screen(hwnd), anchor_tuple(cfg))
        with vision.ScreenCapture() as capture:
            frame = capture.grab(*rect)
        log.info("%s", read_state(frame, cfg).line())
        return 0

    if args.home:
        run_homing(cfg, args.config)
        return 0

    watchdog = Watchdog(cfg, args.control or bool(cfg["control"]["enabled"]), csv_enabled=not args.no_csv)
    try:
        watchdog.run()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        watchdog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
