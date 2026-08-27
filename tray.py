"""System tray icon (pystray) with Show / Start-Stop / Exit menu."""

import threading

from PIL import Image, ImageDraw

import pystray

ACCENT = (99, 102, 241)


def make_icon_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=15, fill=ACCENT + (255,))
    bolt = [(36, 7), (17, 36), (29, 36), (25, 57), (47, 25), (34, 25), (42, 7)]
    d.polygon(bolt, fill=(255, 255, 255, 255))
    return img


def save_ico(path):
    make_icon_image(64).save(path, format="ICO")


class TrayManager:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread = None

    def is_active(self):
        return self.icon is not None

    def start(self):
        if self.icon is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show, default=True),
            pystray.MenuItem("Start / Stop", self._toggle),
            pystray.MenuItem("Exit", self._exit),
        )
        self.icon = pystray.Icon("chamberbot", make_icon_image(), "Reaction Chamber Bot", menu)
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.icon = None

    def _show(self, _icon=None, _item=None):
        self.app.after(0, self.app.restore_from_tray)

    def _toggle(self, _icon=None, _item=None):
        self.app.after(0, self.app.toggle_run)

    def _exit(self, _icon=None, _item=None):
        self.stop()
        self.app.after(0, self.app.quit_from_tray)
