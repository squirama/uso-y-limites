"""GDI screen capture and Windows display-affinity helpers."""

import ctypes
import ctypes.wintypes
import os

from PIL import Image


SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


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


def _windows_apis():
    if os.name != "nt":
        raise OSError("La captura de pantalla solo está disponible en Windows.")
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.wintypes.HWND]
    user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.BitBlt.restype = ctypes.wintypes.BOOL
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_int, ctypes.wintypes.DWORD]
    return user32, gdi32


def capture_screen(rect):
    """Capture (x, y, width, height) into an RGB image and release every GDI handle."""
    x, y, width, height = (int(value) for value in rect)
    if width <= 0 or height <= 0:
        raise ValueError("El área de captura no es válida.")
    user32, gdi32 = _windows_apis()
    screen_dc = memory_dc = bitmap = old_bitmap = None
    bitmap_selected = False
    try:
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise OSError("No se pudo acceder a la pantalla.")
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise OSError("No se pudo preparar la captura de pantalla.")
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB,
            width * height * 4, 0, 0, 0, 0,
        )
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            screen_dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits.value:
            raise OSError("No se pudo crear la imagen de captura.")
        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        if not old_bitmap or old_bitmap == ctypes.c_void_p(-1).value:
            raise OSError("No se pudo seleccionar la imagen de captura.")
        bitmap_selected = True
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY):
            raise OSError("No se pudo capturar el área de pantalla.")
        pixels = ctypes.string_at(bits, width * height * 4)
        return Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX")
    finally:
        if memory_dc and bitmap_selected:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


def get_capture_exclusion(hwnd):
    """Return the window display-affinity value currently set by Windows."""
    if os.name != "nt":
        raise OSError("La exclusión de capturas solo está disponible en Windows.")
    user32 = ctypes.windll.user32
    user32.GetWindowDisplayAffinity.restype = ctypes.wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [ctypes.wintypes.HWND,
                                               ctypes.POINTER(ctypes.wintypes.DWORD)]
    affinity = ctypes.wintypes.DWORD()
    if not user32.GetWindowDisplayAffinity(int(hwnd), ctypes.byref(affinity)):
        raise OSError("No se pudo consultar la exclusión de capturas.")
    return affinity.value


def set_capture_exclusion(hwnd, excluded):
    """Enable or disable exclusion of a widget window from screen capture."""
    if os.name != "nt":
        raise OSError("La exclusión de capturas solo está disponible en Windows.")
    user32 = ctypes.windll.user32
    user32.SetWindowDisplayAffinity.restype = ctypes.wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
    affinity = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
    if not user32.SetWindowDisplayAffinity(int(hwnd), affinity):
        raise OSError("No se pudo cambiar la exclusión de capturas.")
    actual = get_capture_exclusion(hwnd)
    if actual != affinity:
        raise OSError("Windows no aplicó la exclusión de capturas solicitada.")
    return actual
