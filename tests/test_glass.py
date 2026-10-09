import unittest

import numpy as np
from PIL import Image

from usage_monitor.glass import Tint, _refraction, compose, effective_opacity


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


if __name__ == "__main__":
    unittest.main()
