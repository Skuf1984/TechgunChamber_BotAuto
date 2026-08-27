"""Windows toast notifications (best-effort). Falls back silently if unavailable -
the GUI always shows its own banner regardless."""

import os
import sys


def _icon_path():
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidate = os.path.join(base, "app_icon.ico")
    return candidate if os.path.exists(candidate) else None


def toast(title, message):
    try:
        from winotify import Notification  # noqa: PLC0415

        note = Notification(app_id="Reaction Chamber Bot", title=title, msg=message, duration="short")
        icon = _icon_path()
        if icon:
            note.icon = icon
        note.show()
    except Exception:  # noqa: BLE001 - notifications must never break the app
        pass


def beep(kind="info"):
    try:
        import winsound  # noqa: PLC0415

        if kind == "error":
            winsound.Beep(600, 300)
        elif kind == "success":
            winsound.Beep(1000, 180)
        else:
            winsound.Beep(800, 150)
    except Exception:  # noqa: BLE001
        pass
