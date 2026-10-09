import ctypes
import os
import unittest
from unittest.mock import Mock, patch

from usage_monitor import capture


class CaptureTests(unittest.TestCase):
    def test_capture_releases_gdi_handles_after_success(self):
        user32, gdi32 = Mock(), Mock()
        user32.GetDC.return_value = 101
        gdi32.CreateCompatibleDC.return_value = 202
        gdi32.SelectObject.return_value = 303
        gdi32.BitBlt.return_value = 1
        pixels = (ctypes.c_ubyte * 16)(10, 20, 30, 0, 40, 50, 60, 0,
                                        70, 80, 90, 0, 100, 110, 120, 0)

        def create_dib(_dc, _info, _colors, bits, _section, _offset):
            ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(
                pixels, ctypes.c_void_p)
            return 404

        gdi32.CreateDIBSection.side_effect = create_dib
        with patch("usage_monitor.capture._windows_apis", return_value=(user32, gdi32)):
            image = capture.capture_screen((5, 7, 2, 2))

        self.assertEqual(image.size, (2, 2))
        self.assertEqual(image.getpixel((0, 0)), (30, 20, 10))
        gdi32.SelectObject.assert_any_call(202, 303)
        gdi32.DeleteObject.assert_called_once_with(404)
        gdi32.DeleteDC.assert_called_once_with(202)
        user32.ReleaseDC.assert_called_once_with(None, 101)

    def test_capture_releases_gdi_handles_when_copy_fails(self):
        user32, gdi32 = Mock(), Mock()
        user32.GetDC.return_value = 101
        gdi32.CreateCompatibleDC.return_value = 202
        gdi32.SelectObject.return_value = 303
        gdi32.BitBlt.return_value = 0
        pixels = (ctypes.c_ubyte * 16)()

        def create_dib(_dc, _info, _colors, bits, _section, _offset):
            ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(
                pixels, ctypes.c_void_p)
            return 404

        gdi32.CreateDIBSection.side_effect = create_dib
        with patch("usage_monitor.capture._windows_apis", return_value=(user32, gdi32)):
            with self.assertRaisesRegex(OSError, "No se pudo capturar"):
                capture.capture_screen((5, 7, 2, 2))

        gdi32.SelectObject.assert_any_call(202, 303)
        gdi32.DeleteObject.assert_called_once_with(404)
        gdi32.DeleteDC.assert_called_once_with(202)
        user32.ReleaseDC.assert_called_once_with(None, 101)

    def test_capture_rejects_non_positive_dimensions(self):
        with patch("usage_monitor.capture._windows_apis") as windows:
            with self.assertRaises(ValueError):
                capture.capture_screen((0, 0, 0, 1))
        windows.assert_not_called()


    @unittest.skipUnless(os.name == "nt", "Requiere Windows")
    def test_real_capture_then_layered_resize_share_gdi_structures(self):
        # Regression: capture.py and layered.py once had separate BITMAPINFO classes, and
        # resizing the layered window after a capture raised ctypes.ArgumentError.
        import tkinter as tk
        from PIL import Image
        from usage_monitor.layered import LayeredWindow

        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry("40x40+0+0")
        root.update()
        layered = LayeredWindow(root)
        try:
            layered.render(Image.new("RGBA", (40, 40), (0, 0, 0, 0)), 0, 0)
            self.assertEqual(capture.capture_screen((0, 0, 8, 8)).size, (8, 8))
            layered.render(Image.new("RGBA", (60, 90), (0, 0, 0, 0)), 0, 0)
            self.assertEqual(layered.bitmap_size, (60, 90))
        finally:
            layered.close(reset_style=True)
            root.destroy()

if __name__ == "__main__":
    unittest.main()
