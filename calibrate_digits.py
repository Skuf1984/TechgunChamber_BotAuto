"""Build digit templates by stepping the laser power and capturing each glyph.

Start from a KNOWN value (the user sets it, default assumption: 1). The routine
clicks - once to try to reach 0, then climbs + capturing 2..9. The value 10 is
later read as '1'+'0' from these same templates.

CLI:
    python calibrate_digits.py            assumes current power is 1
    python calibrate_digits.py --start 3  if you set the power to 3 instead
"""

import argparse
import time

import chamber_bot
import digit
import mouse
import vision
import window


def grab(cfg, hwnd):
    rect = vision.anchor_rect(window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
    with vision.ScreenCapture() as capture:
        panel = capture.grab(*rect)
    return digit.glyph_mask(digit.crop_digit(panel))


def focus_game(cfg, hwnd):
    for _ in range(3):
        window.focus(hwnd)
        time.sleep(0.5)
        if window.is_foreground(hwnd):
            return True
    return window.is_foreground(hwnd)


def run_calibration(cfg, hwnd, start_value, progress_cb=None, should_stop=None):
    """Capture digit templates starting from a known power value.

    Verifies that a '+' click actually changes the digit first, then sweeps up to
    9 and back down to 0 capturing every glyph. Returns (templates, final_value).
    progress_cb(message) reports steps; should_stop() is polled for cancellation."""

    def say(msg):
        if progress_cb is not None:
            progress_cb(msg)

    def cancelled():
        return should_stop is not None and should_stop()

    if not focus_game(cfg, hwnd):
        say("WARNING: could not focus Minecraft; clicks may miss")

    controller = chamber_bot.Controller(cfg, hwnd, enabled=True)
    settle = float(cfg["power"]["settle_seconds"]) + 0.4

    def click_button(name):
        x, y = controller._screen_point(name)
        mouse.click(x, y)

    def grab_diag():
        """Grab the digit twice (second wins, guards a stale frame) and return
        (mask, ink_pixels). mask is None when nothing readable is found."""
        rect = vision.anchor_rect(window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
        mask = None
        with vision.ScreenCapture() as capture:
            for _ in range(2):
                panel = capture.grab(*rect)
                comps = digit.digit_components(digit.crop_digit(panel))
                mask = max(comps, key=lambda m: int(m.sum())) if comps else None
                time.sleep(0.12)
        return (mask, int(mask.sum())) if mask is not None else (None, 0)

    def sim(a, b):
        if a is None or b is None:
            return 0.0
        return digit.similarity(digit.canonical(a), digit.canonical(b))

    # 1) the digit must be visible right now
    start_mask, ink0 = grab_diag()
    if start_mask is None:
        raise RuntimeError("cannot see the digit - is the chamber GUI open and the digit region correct?")
    say(f"start value {int(start_value)} visible (ink={ink0})")

    # 2) verify a '+' click changes what we read (catches dead buttons / wrong ROI)
    click_button("power_plus")
    time.sleep(settle)
    after_mask, ink1 = grab_diag()
    if after_mask is None:
        raise RuntimeError("digit unreadable after a '+' click - the digit region is likely off (re-fit anchor)")
    s = sim(after_mask, start_mask)
    say(f"after '+': ink={ink1}, change_vs_start={1.0 - s:.2f}")
    if s >= 0.95:
        raise RuntimeError(
            "the '+' click did not change the digit I read. Either the +/- buttons "
            "are not hitting, or the digit region is wrong. Run 'Calibrate +/- "
            "buttons' and re-fit the anchor, then retry."
        )
    # restore the starting value
    click_button("power_minus")
    time.sleep(settle)

    # 3) sweep up from start to 9, then back down to 0, capturing everything
    templates = {int(start_value): start_mask}
    current = int(start_value)
    prev_mask = grab_diag()[0]
    prev = prev_mask if prev_mask is not None else start_mask

    while current < 9 and not cancelled():
        click_button("power_plus")
        time.sleep(settle)
        m, ink = grab_diag()
        if m is None:
            say(f"lost the digit at {current} - stopping upward sweep")
            break
        if sim(m, prev) >= 0.95:
            say(f"digit stopped changing at {current} (max reached)")
            break
        current += 1
        templates[current] = m
        prev = m
        say(f"captured {current} (ink={ink})")

    prev_mask = grab_diag()[0]
    if prev_mask is not None:
        prev = prev_mask
    while current > 0 and not cancelled():
        click_button("power_minus")
        time.sleep(settle)
        m, ink = grab_diag()
        if m is None:
            say(f"lost the digit at {current} - stopping downward sweep")
            break
        if sim(m, prev) >= 0.95:
            say(f"digit stopped changing at {current} (floor reached)")
            break
        current -= 1
        templates[current] = m
        prev = m
        say(f"captured {current} (ink={ink})")

    digit.save_templates(templates)
    say(f"saved templates for values: {sorted(templates)}")
    return templates, current


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="the power value you set right now")
    args = parser.parse_args()

    cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
    hwnd = window.find_window(cfg["window_title"])
    templates, final_value = run_calibration(cfg, hwnd, args.start, progress_cb=lambda m: print(m, flush=True))
    print(f"\nfinal power is now {final_value} - set it back to your default if needed", flush=True)
    _ = templates


if __name__ == "__main__":
    main()
