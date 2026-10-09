from pathlib import Path
import tempfile
import unittest

from usage_monitor.models import UsageError, UsageSnapshot, WindowUsage
from usage_monitor.storage import load_snapshot, save_snapshot, load_settings, save_settings, write_json


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


if __name__ == "__main__":
    unittest.main()
