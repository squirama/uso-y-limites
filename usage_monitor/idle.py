"""Read the time since the last user input on Windows."""

import ctypes
import ctypes.wintypes
import os


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]


def idle_seconds():
    """Return idle duration, or zero when the platform cannot report it."""
    if os.name != "nt":
        return 0.0

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        tick_count = kernel32.GetTickCount64() & 0xFFFFFFFF
        elapsed_ms = (tick_count - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000
    except (AttributeError, OSError):
        return 0.0
