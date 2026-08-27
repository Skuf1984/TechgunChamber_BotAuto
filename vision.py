"""Screen capture (pure GDI, no third-party deps) and pixel readers for the
Reaction Chamber GUI: vertical/horizontal bar levels and slot occupancy."""

import ctypes
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


class CaptureError(RuntimeError):
    pass


class ScreenCapture:
    """Reusable GDI screen grabber. Returns RGB uint8 arrays shaped (h, w, 3)."""

    def __init__(self):
        self._screen_dc = user32.GetDC(None)
        if not self._screen_dc:
            raise CaptureError("GetDC(None) failed")
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        self._bitmap = None
        self._size = (0, 0)
        self._buffer = None

    def _ensure_bitmap(self, width, height):
        if self._size == (width, height) and self._bitmap:
            return
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
        self._bitmap = gdi32.CreateCompatibleBitmap(self._screen_dc, width, height)
        if not self._bitmap:
            raise CaptureError("CreateCompatibleBitmap failed")
        self._size = (width, height)
        self._buffer = ctypes.create_string_buffer(width * height * 4)

    def grab(self, left, top, width, height):
        if width <= 0 or height <= 0:
            raise CaptureError(f"invalid capture size {width}x{height}")
        self._ensure_bitmap(width, height)
        gdi32.SelectObject(self._mem_dc, self._bitmap)
        ok = gdi32.BitBlt(
            self._mem_dc, 0, 0, width, height,
            self._screen_dc, left, top, SRCCOPY | CAPTUREBLT,
        )
        if not ok:
            raise CaptureError("BitBlt failed (fullscreen exclusive mode is not capturable)")

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        copied = gdi32.GetDIBits(
            self._mem_dc, self._bitmap, 0, height,
            self._buffer, ctypes.byref(info), DIB_RGB_COLORS,
        )
        if copied != height:
            raise CaptureError(f"GetDIBits copied {copied}/{height} scanlines")

        raw = np.frombuffer(self._buffer, dtype=np.uint8, count=width * height * 4)
        bgra = raw.reshape((height, width, 4))
        return bgra[:, :, 2::-1].copy()  # BGRA -> RGB

    def close(self):
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
            self._bitmap = None
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        if self._screen_dc:
            user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def anchor_rect(client_rect, anchor):
    """Absolute screen rect of the GUI panel from client rect + calibrated anchor."""
    left, top, _cw, _ch = client_rect
    return left + int(anchor[0]), top + int(anchor[1]), int(anchor[2]), int(anchor[3])


def crop(frame, roi):
    """roi = [x, y, w, h] normalized to the frame (0..1)."""
    height, width = frame.shape[:2]
    x0 = max(0, min(width - 1, int(round(roi[0] * width))))
    y0 = max(0, min(height - 1, int(round(roi[1] * height))))
    x1 = max(x0 + 1, min(width, int(round((roi[0] + roi[2]) * width))))
    y1 = max(y0 + 1, min(height, int(round((roi[1] + roi[3]) * height))))
    return frame[y0:y1, x0:x1]


def point_in(rect, point):
    """Absolute screen point from a rect (left, top, w, h) + normalized point."""
    left, top, width, height = rect
    return left + int(round(point[0] * width)), top + int(round(point[1] * height))


def mean_rgb(img):
    return tuple(float(v) for v in img.reshape(-1, 3).mean(axis=0))


def _dist2(img, color):
    ref = np.asarray(color, dtype=np.int32)
    diff = img.astype(np.int32) - ref
    return (diff * diff).sum(axis=-1)


def bar_level(img, filled_color, empty_color, orientation="up"):
    """Fraction 0..1 of the bar that reads as 'filled'.

    orientation: 'up' fills bottom->top, 'down' top->bottom,
                 'right' left->right, 'left' right->left.
    """
    filled = _dist2(img, filled_color)
    empty = _dist2(img, empty_color)
    mask = filled < empty

    axis = 1 if orientation in ("up", "down") else 0
    line_filled = mask.mean(axis=axis) > 0.5
    total = line_filled.size
    if total == 0:
        return 0.0
    return float(line_filled.sum()) / float(total)


def bar_edge(img, filled_color, empty_color, orientation="up", edge_slack=2):
    """Fraction 0..1 measured to the outermost contiguous fill edge.

    More stable than bar_level for bars drawn with tick marks or gradients.
    `edge_slack` tolerates the 1-2px GUI frame that a hand-drawn ROI usually
    swallows at the filling end - without it a one pixel offset reads as zero.
    """
    filled = _dist2(img, filled_color)
    empty = _dist2(img, empty_color)
    mask = filled < empty

    if orientation in ("up", "down"):
        lines = mask.mean(axis=1) > 0.5
        if orientation == "up":
            lines = lines[::-1]
    else:
        lines = mask.mean(axis=0) > 0.5
        if orientation == "left":
            lines = lines[::-1]

    total = lines.size
    if total == 0:
        return 0.0

    start = 0
    limit = min(int(edge_slack), total)
    while start < limit and not lines[start]:
        start += 1

    run = 0
    for value in lines[start:]:
        if not value:
            break
        run += 1
    return float(run) / float(total)


def bar_fill_multi(img, fill_colors, empty_color, orientation="up", edge_slack=2):
    """Read a bar whose fill colour signals state (green = matched, red = mismatch).

    Returns (fraction, name_of_matching_colour). The colour that produces the
    longest contiguous run wins - a red fill scores zero against a green
    reference because red sits closer to the grey background than to green.
    """
    best_fraction = 0.0
    best_name = "unknown"
    for name, color in fill_colors.items():
        value = bar_edge(img, color, empty_color, orientation, edge_slack)
        if value > best_fraction:
            best_fraction = value
            best_name = name
    return best_fraction, best_name


def marker_position(img, marker_color, tolerance=70.0, orientation="up"):
    """Locate a coloured target marker on a scale.

    Returns the marker centre as a 0..1 fraction measured from the filling
    origin (bottom for 'up'), or None when no marker pixels are present.
    """
    dist = np.sqrt(_dist2(img, marker_color).astype(np.float32))
    mask = dist < tolerance

    axis = 1 if orientation in ("up", "down") else 0
    weights = mask.sum(axis=axis).astype(np.float64)
    total = weights.sum()
    if total <= 0:
        return None

    index = np.arange(weights.size, dtype=np.float64)
    centre = float((index * weights).sum() / total)
    span = max(1.0, float(weights.size - 1))
    if orientation in ("up", "left"):
        return 1.0 - centre / span
    return centre / span


def occupancy(img, empty_color, tolerance=42.0):
    """Fraction of pixels that differ from the empty-slot colour."""
    dist = np.sqrt(_dist2(img, empty_color).astype(np.float32))
    return float((dist > tolerance).mean())


def split_grid(img, cols, rows):
    height, width = img.shape[:2]
    cells = []
    for r in range(rows):
        for c in range(cols):
            y0 = height * r // rows
            y1 = height * (r + 1) // rows
            x0 = width * c // cols
            x1 = width * (c + 1) // cols
            cells.append(img[y0:y1, x0:x1])
    return cells


def is_blank(frame, threshold=4.0):
    """True when the capture came back black/uniform (hardware overlay problem)."""
    return float(frame.astype(np.float32).std()) < threshold
