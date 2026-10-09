import unittest

from usage_monitor import render


class RenderTests(unittest.TestCase):
    def provider(self, key):
        return render.Provider(key, 25, (), "hace 1 s")

    def test_compact_sizes(self):
        self.assertEqual(render.compact_size(2, False), (124, 56))
        self.assertEqual(render.compact_size(2, True), (56, 124))
        self.assertEqual(render.compact_size(1, False), (56, 56))
        self.assertEqual(render.compact_size(1, True), (56, 56))

    def test_compact_single_is_round_and_transparent_at_corners(self):
        image = render.compact([self.provider("claude")], False)
        self.assertEqual(image.size, (56, 56))
        key = tuple(int(render.KEY[i:i + 2], 16) for i in (1, 3, 5))
        for point in ((0, 0), (55, 0), (0, 55), (55, 55)):
            self.assertEqual(image.getpixel(point), key)

    def test_expanded_sizes_fit_one_and_two_providers(self):
        one = render.expanded_size([self.provider("claude")])
        two = render.expanded_size([self.provider("claude"), self.provider("codex")])
        self.assertEqual(one[0], two[0])
        self.assertGreater(two[1], one[1])
        self.assertEqual(render.expanded([self.provider("codex")]).size, one)


if __name__ == "__main__":
    unittest.main()
