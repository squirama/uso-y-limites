"""Detect foreground windows that cover the widget's monitor."""

import ctypes
import ctypes.wintypes
import os


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def foreground_is_fullscreen(monitor_point):
    if os.name != "nt":
        return False

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.wintypes.LONG), ("top", ctypes.wintypes.LONG),
                    ("right", ctypes.wintypes.LONG), ("bottom", ctypes.wintypes.LONG)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.wintypes.DWORD)]

    try:
        user32 = ctypes.windll.user32
        point = ctypes.wintypes.POINT(int(monitor_point[0]), int(monitor_point[1]))
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
        target_monitor = user32.MonitorFromPoint(point, 2)
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False

        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
        if not user32.GetClassNameW(foreground, class_name, len(class_name)):
            return False
        if class_name.value in SHELL_CLASSES:
            return False

        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(process_id))
        if process_id.value == os.getpid():
            return False

        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
        foreground_monitor = user32.MonitorFromWindow(foreground, 2)
        if not target_monitor or foreground_monitor != target_monitor:
            return False

        rect = RECT()
        user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(RECT)]
        if not user32.GetWindowRect(foreground, ctypes.byref(rect)):
            return False
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        if not user32.GetMonitorInfoW(ctypes.c_void_p(target_monitor), ctypes.byref(info)):
            return False
        monitor = info.rcMonitor
        return (rect.left <= monitor.left and rect.top <= monitor.top
                and rect.right >= monitor.right and rect.bottom >= monitor.bottom)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return False
