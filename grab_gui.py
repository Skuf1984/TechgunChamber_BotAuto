"""Capture the bot GUI window to screenshots/bot_gui.png for the README."""

import os
import sys
import time

import chamber_bot
import vision
import window

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        hwnd = window.find_window(chamber_bot and "Reaction Chamber")
    except window.WindowNotFound:
        print("GUI window not found - is the bot running?")
        sys.exit(1)
    window.focus(hwnd)
    time.sleep(0.8)
    left, top, w, h = window.client_rect_on_screen(hwnd)
    print(f"gui window at ({left},{top}) {w}x{h}")
    with vision.ScreenCapture() as cap:
        img = cap.grab(left, top, w, h)
    chamber_bot.save_image(os.path.join(OUT, "bot_gui.png"), img)
    print("saved screenshots/bot_gui.png")


if __name__ == "__main__":
    main()
