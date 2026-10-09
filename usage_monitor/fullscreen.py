"""Detect foreground windows that cover the widget's monitor."""

import ctypes
import ctypes.wintypes
import os

from .win32types import RECT, monitor_info


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def window_covers_monitor(window_rect, monitor_rect, is_zoomed=False):
    if is_zoomed:
        return False
    left, top, right, bottom = window_rect
    monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_rect
    return (left <= monitor_left and top <= monitor_top
            and right >= monitor_right and bottom >= monitor_bottom)


def foreground_is_fullscreen(monitor_point):
    if os.name != "nt":
        return False

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
        user32.IsZoomed.argtypes = [ctypes.wintypes.HWND]
        user32.IsZoomed.restype = ctypes.wintypes.BOOL
        if user32.IsZoomed(foreground):
            return False

        rect = RECT()
        user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(RECT)]
        if not user32.GetWindowRect(foreground, ctypes.byref(rect)):
            return False
        info = monitor_info(target_monitor)
        if info is None:
            return False
        monitor = info.rcMonitor
        return window_covers_monitor(
            (rect.left, rect.top, rect.right, rect.bottom),
            (monitor.left, monitor.top, monitor.right, monitor.bottom),
        )
    except (AttributeError, OSError, TypeError, ValueError, OverflowError, ctypes.ArgumentError):
        return False
