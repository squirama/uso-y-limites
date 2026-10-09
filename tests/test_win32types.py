import ctypes
import os
import tkinter as tk
import unittest

from usage_monitor import fullscreen, ui, win32types


@unittest.skipUnless(os.name == "nt", "Requiere Windows")
class SharedWin32TypesTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.user32 = ctypes.windll.user32
        self.saved_argtypes = self.user32.GetMonitorInfoW.argtypes

    def tearDown(self):
        self.user32.GetMonitorInfoW.argtypes = self.saved_argtypes
        self.root.destroy()

    def test_work_area_after_fullscreen_check_uses_the_same_structure(self):
        # Regression: fullscreen.py and ui.work_area had separate MONITORINFO classes; once the
        # fullscreen check ran, work_area raised ctypes.ArgumentError on every call.
        self.user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = self.user32.MonitorFromPoint(ctypes.wintypes.POINT(100, 100), 2)
        info = win32types.monitor_info(monitor)
        self.assertIsNotNone(info)
        fullscreen.foreground_is_fullscreen((100, 100))
        area = ui.work_area(100, 100, self.root)
        self.assertEqual(area, (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom))
        self.assertIs(fullscreen.RECT, win32types.RECT)

    def test_work_area_never_raises_on_foreign_argtypes(self):
        class OTHER(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.DWORD)]

        self.user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(OTHER)]
        # monitor_info resets the argtypes to the shared structure, so this still works.
        self.assertEqual(len(ui.work_area(100, 100, self.root)), 4)


if __name__ == "__main__":
    unittest.main()
