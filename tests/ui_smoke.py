from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from usage_monitor.models import UsageSnapshot, WindowUsage
from usage_monitor.fullscreen import window_covers_monitor
from usage_monitor.ui import App


class Event:
    def __init__(self, x, y):
        self.x_root, self.y_root = x, y


class UiSmokeTests(unittest.TestCase):
    def make_app(self, folder, providers, idle_getter=None, notifier=None, fullscreen_getter=None):
        Path(folder, "settings.json").write_text('{"render_mode":"colorkey"}', encoding="utf-8")
        root = tk.Tk()
        kwargs = {"idle_getter": idle_getter} if idle_getter else {}
        if notifier is not None:
            kwargs["notifier"] = notifier
        if fullscreen_getter is not None:
            kwargs["fullscreen_getter"] = fullscreen_getter
        return root, App(root, data_dir=Path(folder), network=False, providers=providers, **kwargs)

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


    def test_drag_resistance_tear_off_and_magnet(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude", "codex"])
            try:
                for key in ("claude", "codex"):
                    app.snapshots[key] = UsageSnapshot((WindowUsage("5 horas", 66, time.time() + 3600),
                        WindowUsage("7 días", 54, time.time() + 86400 * 3)), time.time(), "test")
                root.update()
                self.assertEqual(app.dock["side"], "right")
                w, h = app.rect[2:]
                self.assertLess(w, h)
                # A short pull stays attached; a long one tears off and docks at the nearest edge.
                x, y = app.rect[:2]
                app.on_press(Event(x + 5, y + 5))
                app.on_motion(Event(x - 20, y + 5))
                self.assertTrue(app.drag["attached"])
                left, top, right, bottom = app.area()
                app.on_motion(Event((left + right) // 2, bottom - 20))
                self.assertFalse(app.drag["attached"])
                app.on_release(Event((left + right) // 2, bottom - 20))
                self.assertEqual(app.dock["side"], "bottom")
                deadline = time.time() + 2
                while app.animation and time.time() < deadline:
                    root.update()
                w, h = app.rect[2:]
                self.assertGreater(w, h)
                app.open = True
                app.place()
                self.assertGreater(app.rect[3], h * 3)
                self.assertEqual(app.settings["dock"][0], "bottom")
            finally:
                app.close()
            self.assertFalse(app.after_ids)

    def test_toggling_a_provider_keeps_a_single_polling_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude", "codex"])
            try:
                app.network = True
                app.client.fetch = lambda: None
                app.claude_probe.fetch = lambda: None
                app.loop("codex", 30000, app.auto_codex)
                app.loop("watch", 3000, app.watch_claude)
                before = len(app.after_ids)
                for key in ("codex", "claude"):
                    app.provider_vars[key].set(False)
                    app.toggle_provider(key)
                    app.provider_vars[key].set(True)
                    app.toggle_provider(key)
                for worker in app.workers:
                    worker.join(timeout=2)
                # Each loop keeps exactly one pending timer after being switched off and on.
                self.assertIn(app.loops["codex"], app.after_ids)
                self.assertIn(app.loops["watch"], app.after_ids)
                self.assertLessEqual(len(app.after_ids), before + 1)
            finally:
                app.close()

    def test_idle_and_resume_adjust_polling_without_duplicate_loops(self):
        idle = {"seconds": 0}
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude", "codex"], lambda: idle["seconds"])
            try:
                app.network = True
                refreshed = []
                app.refresh_provider = refreshed.append
                app.loop("codex", 30000, app.auto_codex)
                app.schedule_claude(30)

                idle["seconds"] = 300
                with patch.object(root, "after", wraps=root.after) as after:
                    app.update_idle_state()
                    self.assertTrue(app.away)
                    self.assertEqual([call.args[0] for call in after.call_args_list], [1800000, 300000])
                    self.assertIn("En pausa: sin actividad", app.view()[0].age)

                refreshed.clear()
                idle["seconds"] = 4.9
                with patch.object(root, "after", wraps=root.after) as after:
                    app.update_idle_state()
                    self.assertFalse(app.away)
                    self.assertEqual(refreshed, ["claude", "codex"])
                    self.assertEqual([call.args[0] for call in after.call_args_list], [30000, 30000])
            finally:
                app.close()

    def test_alerts_are_once_per_five_hour_window_and_can_be_disabled(self):
        notices = []
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"], notifier=lambda *args: notices.append(args))
            try:
                now = time.time()
                snapshot = UsageSnapshot((WindowUsage("5 horas", 90, now + 3600),
                                          WindowUsage("7 días", 99, now + 86400)), now, "test")
                app.check_alerts("claude", snapshot)
                app.check_alerts("claude", snapshot)
                self.assertEqual(len(notices), 1)
                self.assertEqual(notices[0][0], "Claude al 90 %")
                self.assertIn("Se restablece a las", notices[0][1])

                next_window = UsageSnapshot((WindowUsage("5 horas", 95, now + 7200),), now, "test")
                app.check_alerts("claude", next_window)
                self.assertEqual(len(notices), 2)

                app.alerts_var.set(False)
                app.toggle_alerts()
                disabled_window = UsageSnapshot((WindowUsage("5 horas", 99, now + 10800),), now, "test")
                app.check_alerts("claude", disabled_window)
                self.assertEqual(len(notices), 2)
                self.assertFalse(app.settings["alerts"])
            finally:
                app.close()

    def test_fullscreen_hides_and_restores_widget(self):
        state = {"fullscreen": True}
        points = []
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(
                folder, ["claude"],
                fullscreen_getter=lambda point: points.append(point) or state["fullscreen"],
            )
            try:
                with patch.object(root, "withdraw") as withdraw, patch.object(
                        root, "deiconify") as deiconify, patch.object(
                        root, "attributes") as attributes, patch.object(app, "place") as place:
                    app.update_fullscreen_state()
                    self.assertTrue(app.fullscreen_hidden)
                    withdraw.assert_called_once_with()
                    self.assertEqual(points[-1], (app.rect[0] + app.rect[2] // 2,
                                                   app.rect[1] + app.rect[3] // 2))

                    state["fullscreen"] = False
                    app.update_fullscreen_state()
                    self.assertFalse(app.fullscreen_hidden)
                    deiconify.assert_called_once_with()
                    attributes.assert_called_once_with("-topmost", True)
                    place.assert_called_once_with()
            finally:
                app.close()

    def test_fullscreen_does_not_hide_during_drag_or_animation(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"], fullscreen_getter=lambda _point: True)
            try:
                with patch.object(root, "withdraw") as withdraw:
                    app.drag = {"attached": True}
                    app.update_fullscreen_state()
                    app.drag = None
                    app.animation = object()
                    app.update_fullscreen_state()
                    self.assertFalse(app.fullscreen_hidden)
                    withdraw.assert_not_called()
                    app.animation = None
                    app.update_fullscreen_state()
                    self.assertTrue(app.fullscreen_hidden)
                    withdraw.assert_called_once_with()
            finally:
                app.close()

    def test_fullscreen_on_another_monitor_does_not_hide_widget(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(
                folder, ["claude"], fullscreen_getter=lambda point: point[0] < 1920,
            )
            try:
                app.rect = (2400, 100, 100, 100)
                with patch.object(root, "withdraw") as withdraw:
                    app.update_fullscreen_state()
                    self.assertFalse(app.fullscreen_hidden)
                    withdraw.assert_not_called()
            finally:
                app.close()

    def test_maximized_window_is_not_treated_as_fullscreen(self):
        monitor = (0, 0, 1920, 1080)
        maximized_rect = (-8, -8, 1928, 1088)
        self.assertFalse(window_covers_monitor(maximized_rect, monitor, is_zoomed=True))
        self.assertTrue(window_covers_monitor((0, 0, 1920, 1080), monitor))

    def test_layered_mode_renders_compact_and_expanded(self):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []
                self.closed = False

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                self.closed = True

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "settings.json").write_text('{"render_mode":"auto"}', encoding="utf-8")
            root = tk.Tk()
            layered = FakeLayeredWindow(root)
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered)
            try:
                self.assertEqual(app.render_mode, "layered")
                self.assertEqual(app.canvas.winfo_manager(), "")
                self.assertEqual(layered.images[-1].mode, "RGBA")
                app.open = True
                app.place()
                self.assertEqual(layered.images[-1].mode, "RGBA")
                self.assertGreater(layered.images[-1].height, 56)
            finally:
                app.close()
            self.assertTrue(layered.closed)

    def test_layered_initialization_failure_falls_back_to_colorkey(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "settings.json").write_text('{"render_mode":"auto"}', encoding="utf-8")
            root = tk.Tk()

            def fail(_root):
                raise OSError("native layered setup unavailable")

            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=fail)
            try:
                self.assertEqual(app.render_mode, "colorkey")
                self.assertEqual(app.canvas.winfo_manager(), "pack")
                self.assertIsNone(app.layered)
            finally:
                app.close()

    def test_layered_render_failure_releases_renderer_and_falls_back(self):
        class FailingLayeredWindow:
            def __init__(self, _root):
                self.reset_style = None

            def render(self, _image, _x, _y):
                raise OSError("UpdateLayeredWindow failed")

            def close(self, reset_style=False):
                self.reset_style = reset_style

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "settings.json").write_text('{"render_mode":"auto"}', encoding="utf-8")
            root = tk.Tk()
            layered = FailingLayeredWindow(root)
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered)
            try:
                self.assertEqual(app.render_mode, "colorkey")
                self.assertIsNone(app.layered)
                self.assertEqual(app.canvas.winfo_manager(), "pack")
                self.assertTrue(layered.reset_style)
            finally:
                app.close()

if __name__ == "__main__":
    unittest.main()
