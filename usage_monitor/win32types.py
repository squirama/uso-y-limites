"""Win32 structures shared by every module that calls the same user32 functions.

ctypes stores argtypes on the shared user32 object, so two modules defining their own
MONITORINFO class make each other's GetMonitorInfoW calls fail with ArgumentError.
"""

import ctypes
import ctypes.wintypes


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.wintypes.LONG), ("top", ctypes.wintypes.LONG),
                ("right", ctypes.wintypes.LONG), ("bottom", ctypes.wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.wintypes.DWORD)]


def monitor_info(monitor):
    """Return MONITORINFO for a monitor handle, or None if Windows cannot provide it."""
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
        return None
    return info
