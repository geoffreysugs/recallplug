"""Tiny wrapper around Windows APIs (no extra packages needed).

On non-Windows systems the functions return harmless defaults so the rest
of the code (and the unit tests) can still be imported.
"""
from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def enable_dpi_awareness() -> None:
    """Avoid blurry / mis-positioned windows on 125% / 150% display scaling."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


SPI_GETWORKAREA = 0x0030


def get_work_area() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the primary monitor's work area, i.e. the
    screen without the taskbar, in physical pixels once DPI awareness is on.
    None if it cannot be read (not Windows, or the call fails)."""
    if not IS_WINDOWS:
        return None
    try:
        from ctypes import wintypes
        rect = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0,
                                                          ctypes.byref(rect), 0):
            return None
        area = (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None
    if area[2] - area[0] <= 0 or area[3] - area[1] <= 0:
        return None
    return area


def get_foreground_title() -> str:
    """Title text of the window the user is currently working in."""
    if not IS_WINDOWS:
        return ""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input."""
    if not IS_WINDOWS:
        return 0.0
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = ctypes.c_uint32
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return millis / 1000.0
