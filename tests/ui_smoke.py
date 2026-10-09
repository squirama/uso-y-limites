from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from usage_monitor.models import UsageSnapshot, WindowUsage
from usage_monitor.ui import App


class UiSmokeTests(unittest.TestCase):
    def make_app(self, folder, providers):
        root = tk.Tk()
        return root, App(root, data_dir=Path(folder), network=False, providers=providers)

    def test_provider_selection_view_size_persistence_and_docking(self):
        for selected in (["claude"], ["codex"], ["claude", "codex"]):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as folder:
                root, app = self.make_app(folder, selected)
                try:
                    app.snapshots["claude"] = UsageSnapshot((WindowUsage("5 horas", 66, None),), 1, "test")
                    app.snapshots["codex"] = UsageSnapshot((WindowUsage("5 horas", 33, None),), 1, "test")
                    self.assertEqual([item.key for item in app.view()], selected)
                    size = app.compact_size("right")
                    self.assertEqual(size, (56, 56) if len(selected) == 1 else (56, 124))
                    self.assertEqual(app.settings["providers"], ["claude", "codex"])

                    if len(selected) == 1:
                        only = selected[0]
                        app.provider_vars[only].set(False)
                        app.toggle_provider(only)
                        self.assertTrue(app.provider_vars[only].get())
                        self.assertEqual(app.providers, selected)
                    else:
                        app.provider_vars["claude"].set(False)
                        app.toggle_provider("claude")
                        self.assertEqual(app.providers, ["codex"])
                        app.provider_vars["codex"].set(False)
                        app.toggle_provider("codex")
                        self.assertTrue(app.provider_vars["codex"].get())
                        app.provider_vars["claude"].set(True)
                        app.toggle_provider("claude")
                        self.assertEqual(app.providers, selected)

                    side = app.dock["side"]
                    app.open = False
                    app.place()
                    app.save_dock()
                    self.assertEqual(app.settings["providers"], ["claude", "codex"])
                    left, top, right, bottom = app.area()
                    if side == "right":
                        self.assertEqual(app.rect[0] + app.rect[2], right - app.px(12))
                    elif side == "left":
                        self.assertEqual(app.rect[0], left + app.px(12))
                    elif side == "top":
                        self.assertEqual(app.rect[1], top + app.px(12))
                    else:
                        self.assertEqual(app.rect[1] + app.rect[3], bottom - app.px(12))
                finally:
                    app.close()

    def test_inactive_provider_is_not_loaded_or_queried(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "claude.json").write_text("invalid", encoding="utf-8")
            with patch("usage_monitor.ui.load_snapshot", wraps=__import__(
                    "usage_monitor.ui", fromlist=["load_snapshot"]).load_snapshot) as load:
                root, app = self.make_app(folder, ["codex"])
            try:
                self.assertEqual([call.args[0] for call in load.call_args_list], ["codex"])
                counts = {"codex": 0, "claude": 0}
                app.network = True
                app.client.fetch = lambda: counts.__setitem__("codex", counts["codex"] + 1)
                app.claude_probe.fetch = lambda: counts.__setitem__("claude", counts["claude"] + 1)
                app.refresh_provider("claude")
                app.refresh_provider("codex")
                app.workers[0].join(timeout=2)
                self.assertEqual(counts, {"codex": 1, "claude": 0})
                app.poll()
                self.assertEqual(app.providers, ["codex"])
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
