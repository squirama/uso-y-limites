"""Windows per-pixel alpha rendering for the floating widget."""

import ctypes
import ctypes.wintypes
import os


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 2
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
BI_RGB = 0
DIB_RGB_COLORS = 0


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.wintypes.DWORD), ("biWidth", ctypes.wintypes.LONG),
                ("biHeight", ctypes.wintypes.LONG), ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD), ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.wintypes.LONG),
                ("biYPelsPerMeter", ctypes.wintypes.LONG), ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD)]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte),
                ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


class LayeredWindow:
    def __init__(self, root):
        if os.name != "nt":
            raise OSError("La transparencia por píxel solo está disponible en Windows.")
        self.root = root
        self.hwnd = int(root.wm_frame(), 16)
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.hdc_screen = None
        self.hdc_memory = None
        self.bitmap = None
        self.old_bitmap = None
        self.bits = None
        self.bitmap_size = None
        self.style_enabled = False
        self.closed = False
        self._configure_api()
        try:
            self._enable_layered_style()
            self.hdc_screen = self.user32.GetDC(None)
            if not self.hdc_screen:
                raise ctypes.WinError()
            self.hdc_memory = self.gdi32.CreateCompatibleDC(self.hdc_screen)
            if not self.hdc_memory:
                raise ctypes.WinError()
        except Exception:
            self.close(reset_style=True)
            raise

    def _configure_api(self):
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.GetDC.argtypes = [ctypes.wintypes.HWND]
        self.user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
        self.set_window_style = getattr(self.user32, "SetWindowLongPtrW", None)
        self.get_window_style = getattr(self.user32, "GetWindowLongPtrW", None)
        if self.set_window_style is None:
            self.set_window_style = self.user32.SetWindowLongW
            self.get_window_style = self.user32.GetWindowLongW
        self.set_window_style.restype = ctypes.c_ssize_t
        self.set_window_style.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.get_window_style.restype = ctypes.c_ssize_t
        self.get_window_style.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        self.user32.UpdateLayeredWindow.restype = ctypes.wintypes.BOOL
        self.user32.UpdateLayeredWindow.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(POINT),
            ctypes.POINTER(SIZE), ctypes.c_void_p, ctypes.POINTER(POINT),
            ctypes.wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), ctypes.wintypes.DWORD,
        ]
        self.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.wintypes.DWORD,
        ]
        self.gdi32.SelectObject.restype = ctypes.c_void_p
        self.gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    def _enable_layered_style(self):
        style = self.get_window_style(self.hwnd, GWL_EXSTYLE)
        self.set_window_style(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        self.style_enabled = True
        if not self.get_window_style(self.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
            raise ctypes.WinError()

    def _create_bitmap(self, width, height):
        if self.old_bitmap:
            self.gdi32.SelectObject(self.hdc_memory, self.old_bitmap)
            self.gdi32.DeleteObject(self.bitmap)
            self.old_bitmap = self.bitmap = self.bits = None
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB,
            width * height * 4, 0, 0, 0, 0,
        )
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(
            self.hdc_screen, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits.value:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            raise ctypes.WinError()
        old_bitmap = self.gdi32.SelectObject(self.hdc_memory, bitmap)
        if not old_bitmap or old_bitmap == ctypes.c_void_p(-1).value:
            self.gdi32.DeleteObject(bitmap)
            raise ctypes.WinError()
        self.bitmap, self.bits, self.old_bitmap = bitmap, bits, old_bitmap
        self.bitmap_size = (width, height)

    def render(self, image, x, y):
        if self.closed:
            raise RuntimeError("La ventana por capas ya está cerrada.")
        rgba = image.convert("RGBA")
        width, height = rgba.size
        if self.bitmap_size != (width, height):
            self._create_bitmap(width, height)
        pixels = rgba.tobytes("raw", "BGRa")
        ctypes.memmove(self.bits, pixels, len(pixels))
        destination = POINT(round(x), round(y))
        size = SIZE(width, height)
        source = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        if not self.user32.UpdateLayeredWindow(
                self.hwnd, self.hdc_screen, ctypes.byref(destination), ctypes.byref(size),
                self.hdc_memory, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA):
            raise ctypes.WinError()

    def close(self, reset_style=False):
        if self.closed:
            return
        self.closed = True
        if self.hdc_memory and self.old_bitmap:
            try:
                self.gdi32.SelectObject(self.hdc_memory, self.old_bitmap)
            except Exception:
                pass
        if self.bitmap:
            try:
                self.gdi32.DeleteObject(self.bitmap)
            except Exception:
                pass
        if self.hdc_memory:
            try:
                self.gdi32.DeleteDC(self.hdc_memory)
            except Exception:
                pass
        if self.hdc_screen:
            try:
                self.user32.ReleaseDC(None, self.hdc_screen)
            except Exception:
                pass
        if reset_style and self.style_enabled:
            try:
                style = self.get_window_style(self.hwnd, GWL_EXSTYLE)
                self.set_window_style(self.hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
            except Exception:
                pass
        self.bitmap = self.bits = self.old_bitmap = None
        self.hdc_memory = self.hdc_screen = None
