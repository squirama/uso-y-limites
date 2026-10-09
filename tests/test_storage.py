from pathlib import Path
import tempfile
import unittest

from usage_monitor.models import UsageError, UsageSnapshot, WindowUsage
from usage_monitor.storage import (load_snapshot, save_snapshot, load_settings, save_settings, write_json,
                                   load_alerts, save_alerts)


class StorageTests(unittest.TestCase):
    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            snapshot = UsageSnapshot((WindowUsage("5 horas", 66, None),), 1791463800, "test")
            save_snapshot("claude", snapshot, directory)
            self.assertEqual(load_snapshot("claude", directory), snapshot)
            self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_corrupt_cache_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "claude.json").write_text("broken", encoding="utf-8")
            with self.assertRaises(UsageError):
                load_snapshot("claude", Path(folder))

    def test_provider_settings_are_validated_and_ordered(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            cases = [
                ({"providers": ["codex", "claude"]}, ["claude", "codex"]),
                ({"providers": ["codex", "codex"]}, ["claude", "codex"]),
                ({"providers": []}, ["claude", "codex"]),
                ({"providers": ["unknown"]}, ["claude", "codex"]),
                ({}, ["claude", "codex"]),
            ]
            for payload, expected in cases:
                with self.subTest(payload=payload):
                    write_json(directory / "settings.json", payload)
                    self.assertEqual(load_settings(directory)["providers"], expected)

    def test_save_requires_at_least_one_provider(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(UsageError):
            save_settings({"providers": [], "dock": None}, Path(folder))
        with tempfile.TemporaryDirectory() as folder:
            save_settings({"providers": ["codex"], "dock": None}, Path(folder))
            self.assertEqual(load_settings(Path(folder))["providers"], ["codex"])

    def test_alert_preference_defaults_on_and_persists_off(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertTrue(load_settings(directory)["alerts"])
            save_settings({"providers": ["codex"], "dock": None, "alerts": False}, directory)
            self.assertFalse(load_settings(directory)["alerts"])

    def test_render_mode_defaults_auto_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertEqual(load_settings(directory)["render_mode"], "auto")
            write_json(directory / "settings.json", {"render_mode": "unknown"})
            self.assertEqual(load_settings(directory)["render_mode"], "auto")
            write_json(directory / "settings.json", {"render_mode": []})
            self.assertEqual(load_settings(directory)["render_mode"], "auto")
            save_settings({"providers": ["claude"], "dock": None, "render_mode": "colorkey"}, directory)
            self.assertEqual(load_settings(directory)["render_mode"], "colorkey")

    def test_glass_preference_defaults_on_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertTrue(load_settings(directory)["glass"])
            write_json(directory / "settings.json", {"glass": "yes"})
            self.assertTrue(load_settings(directory)["glass"])
            save_settings({"providers": ["codex"], "dock": None, "glass": False}, directory)
            self.assertFalse(load_settings(directory)["glass"])

    def test_save_rejects_invalid_glass_preference(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(UsageError):
            save_settings({"providers": ["codex"], "dock": None, "glass": 1}, Path(folder))

    def test_alert_history_roundtrip_and_seven_day_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            history = {
                ("claude", 2000): 1000,
                ("codex", 3000): 100,
            }
            save_alerts(history, directory, now=1000 + 7 * 86400)
            self.assertEqual(load_alerts(directory, now=1000 + 7 * 86400), {("claude", 2000): 1000})


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
