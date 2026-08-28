"""SendInput-based mouse control and non-blocking hotkey polling."""

import ctypes
import random
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK = {}
for _i in range(26):
    VK[chr(ord("A") + _i)] = 0x41 + _i
for _i in range(10):
    VK[str(_i)] = 0x30 + _i
    VK[f"NUM{_i}"] = 0x60 + _i
for _i in range(1, 25):
    VK[f"F{_i}"] = 0x70 + (_i - 1)
VK.update({
    "SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "BACKSPACE": 0x08,
    "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "CAPSLOCK": 0x14,
    "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "MINUS": 0xBD, "EQUALS": 0xBB, "COMMA": 0xBC, "PERIOD": 0xBE,
})
VK_TO_NAME = {code: name for name, code in VK.items()}
MODIFIERS = {"SHIFT", "CTRL", "ALT"}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _send(*inputs):
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())


def _absolute(x, y):
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    return int((x - vx) * 65535 / vw), int((y - vy) * 65535 / vh)


def move_to(x, y):
    nx, ny = _absolute(x, y)
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=nx, dy=ny, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None)))


def click(x, y, button="left", jitter=1, press_delay=0.045):
    """Move with a pixel of jitter, then press and release."""
    tx = x + random.randint(-jitter, jitter) if jitter else x
    ty = y + random.randint(-jitter, jitter) if jitter else y
    move_to(tx, ty)
    time.sleep(0.02 + random.random() * 0.02)

    down, up = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    if button == "right":
        down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)

    _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=down, time=0, dwExtraInfo=None)))
    time.sleep(press_delay + random.random() * 0.02)
    _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=up, time=0, dwExtraInfo=None)))


def key_down(name):
    code = VK.get(name.upper())
    if code is None:
        raise KeyError(f"unknown key {name!r}")
    state = user32.GetAsyncKeyState(code)
    # 0x8000 = currently held; 0x0001 = pressed since the previous query. Without
    # the second bit a quick tap that lands between two polls (~0.4s apart in the
    # watchdog loop) is lost entirely - that's why F8/F9/F10 felt unresponsive.
    return bool(state & 0x8001)


def scan_pressed(skip_modifiers=True):
    """Return the name of a key currently held down, or None. Used to capture a
    fresh binding: the user presses a key and we detect which one it is."""
    fallback = None
    for name, code in VK.items():
        if user32.GetAsyncKeyState(code) & 0x8000:
            if skip_modifiers and name in MODIFIERS:
                fallback = fallback or name
                continue
            return name
    return fallback


class HotkeyEdge:
    """Rising-edge detector so a held key fires exactly once."""

    def __init__(self, *names):
        self._state = {name.upper(): False for name in names}

    def pressed(self, name):
        key = name.upper()
        now = key_down(key)
        was = self._state.get(key, False)
        self._state[key] = now
        return now and not was

    def wait(self, name, poll=0.02, abort=None):
        """Block until `name` fires. Returns False if `abort` fired first."""
        while True:
            if abort and self.pressed(abort):
                return False
            if self.pressed(name):
                return True
            time.sleep(poll)
