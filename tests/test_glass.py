import unittest

import numpy as np
from PIL import Image

from usage_monitor.glass import Tint, _refraction, _refraction_indices, compose, effective_opacity


class GlassTests(unittest.TestCase):
    def test_compose_returns_rounded_rgba_surface(self):
        background = Image.new("RGB", (80, 60), (75, 120, 180))
        result = compose(background, (80, 60), Tint(), 18)
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.size, (80, 60))
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((40, 30))[3], 255)

    def test_refraction_preserves_center_and_shifts_inward_near_edge(self):
        values = np.tile(np.arange(80, dtype=np.uint8), (60, 1))
        source = Image.fromarray(np.repeat(values[:, :, None], 3, axis=2), "RGB")
        result = _refraction(source, 18)
        self.assertEqual(result.getpixel((40, 30)), source.getpixel((40, 30)))
        self.assertGreater(result.getpixel((2, 30))[0], source.getpixel((2, 30))[0])

    def test_bright_background_raises_tint_floor(self):
        tint = Tint(opacity=0.26, bright_minimum=0.35)
        self.assertAlmostEqual(effective_opacity(Image.new("RGB", (16, 16), "white"), tint), 0.35)
        self.assertAlmostEqual(effective_opacity(Image.new("RGB", (16, 16), "black"), tint), 0.26)

    def test_invalid_tint_and_size_are_rejected(self):
        with self.assertRaises(ValueError):
            compose(Image.new("RGB", (4, 4)), (0, 4), Tint(), 1)
        with self.assertRaises(ValueError):
            compose(Image.new("RGB", (4, 4)), (4, 4), Tint(color=(0, 0, 256)), 1)


    def test_single_service_circle_has_transparent_corners(self):
        result = compose(Image.new("RGB", (70, 70), (200, 200, 200)), (70, 70), Tint(), 35, 1.25)
        self.assertEqual(result.getpixel((9, 9))[3], 0)
        self.assertEqual(result.getpixel((35, 35))[3], 255)

    def test_refraction_geometry_is_cached_per_shape(self):
        image = Image.new("RGB", (90, 50), (10, 20, 30))
        _refraction(image, 20, 1.25)
        hits = _refraction_indices.cache_info().hits
        _refraction(image, 20, 1.25)
        self.assertEqual(_refraction_indices.cache_info().hits, hits + 1)

    def test_refraction_band_follows_display_scale(self):
        values = np.tile(np.arange(120, dtype=np.uint8), (60, 1))
        source = Image.fromarray(np.repeat(values[:, :, None], 3, axis=2), "RGB")
        # At scale 1 the band is 14 px: column 16 is untouched; at scale 1.5 it is 21 px.
        self.assertEqual(_refraction(source, 20, 1.0).getpixel((16, 30)), source.getpixel((16, 30)))
        self.assertNotEqual(_refraction(source, 20, 1.5).getpixel((16, 30)), source.getpixel((16, 30)))

if __name__ == "__main__":
    unittest.main()
