"""Win32 window lookup and client-rect geometry for the Minecraft client."""

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL


class WindowNotFound(RuntimeError):
    pass


def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def list_windows():
    found = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd)
            if title:
                found.append((hwnd, title))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def find_window(title_substring):
    needle = title_substring.lower()
    matches = [(h, t) for h, t in list_windows() if needle in t.lower()]
    if not matches:
        raise WindowNotFound(
            f"no visible window matching {title_substring!r}; "
            "run 'python chamber_bot.py --list-windows' to see candidates"
        )
    matches.sort(key=lambda item: len(item[1]))
    return matches[0][0]


def client_rect_on_screen(hwnd):
    """Return (left, top, width, height) of the client area in screen coords."""
    if not user32.IsWindow(hwnd):
        raise WindowNotFound("window handle is no longer valid")
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def is_foreground(hwnd):
    return user32.GetForegroundWindow() == hwnd


def is_minimized(hwnd):
    return bool(user32.IsIconic(hwnd))


def cursor_pos():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def focus(hwnd):
    """Best-effort bring the window to the foreground. Attaches our input thread to
    the current foreground thread so Windows' foreground lock doesn't block us."""
    if is_minimized(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(my_thread, fg_thread, False)
    return user32.GetForegroundWindow() == hwnd
