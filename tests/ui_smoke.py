from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from usage_monitor import glass, render
from usage_monitor.models import UsageSnapshot, WindowUsage
from usage_monitor.fullscreen import window_covers_monitor
from usage_monitor.storage import load_settings, save_snapshot
from usage_monitor.ui import App
import usage_monitor.ui as ui_module


class Event:
    def __init__(self, x, y):
        self.x_root, self.y_root = x, y


class UiSmokeTests(unittest.TestCase):
    def finish_glass_capture(self, app):
        if app.glass_timer is not None:
            app.cancel(app.glass_timer)
            app.glass_timer = None
        app._refresh_glass()
        if app.glass_worker:
            app.glass_worker.join(timeout=2)
        if app.glass_timer is not None:
            app.cancel(app.glass_timer)
            app.glass_timer = None
        app._refresh_glass()
        if app.glass_timer is not None:
            app.cancel(app.glass_timer)
            app.glass_timer = None

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


    def test_status_line_reading_also_alerts(self):
        notices = []
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"], notifier=lambda *args: notices.append(args))
            try:
                now = time.time()
                save_snapshot("claude", UsageSnapshot((WindowUsage("5 horas", 91, now + 3600),),
                                                      now, "Claude Code · barra de estado"), Path(folder))
                app.watch_claude()
                self.assertEqual(len(notices), 1)
                self.assertEqual(notices[0][0], "Claude al 90 %")
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
            exclusions = []
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered,
                      capture_fn=lambda rect: Image.new("RGB", rect[2:], (70, 90, 120)),
                      capture_exclusion_fn=lambda hwnd, excluded: exclusions.append((hwnd, excluded)))
            try:
                self.finish_glass_capture(app)
                self.assertEqual(app.render_mode, "layered")
                self.assertTrue(app.glass_active)
                self.assertEqual(exclusions[-1][1], True)
                self.assertEqual(app.canvas.winfo_manager(), "")
                self.assertEqual(layered.images[-1].mode, "RGBA")
                app.open = True
                app.place()
                self.assertEqual(layered.images[-1].mode, "RGBA")
                self.assertGreater(layered.images[-1].height, 56)
            finally:
                app.close()
            self.assertTrue(layered.closed)
            self.assertEqual(exclusions[-1][1], False)


    def test_glass_single_service_is_round_and_expanded_veil_floor_on_light_background(self):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                pass

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "settings.json").write_text('{"render_mode":"auto"}', encoding="utf-8")
            root = tk.Tk()
            layered = FakeLayeredWindow(root)
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered,
                      capture_fn=lambda rect: Image.new("RGB", rect[2:], (250, 250, 250)),
                      capture_exclusion_fn=lambda hwnd, excluded: None)
            try:
                self.finish_glass_capture(app)
                image = layered.images[-1]
                w, h = image.size
                self.assertEqual(w, h)
                corner = round(w * 0.13)
                # Outside the circle but inside a 22 px rounded square: must be transparent.
                self.assertLess(image.getpixel((corner, corner))[3], 40)
                self.assertGreater(image.getpixel((w // 2, 2))[3], 200)
                app.open = True
                app.place()
                # Expanding changes the rectangle, so the glass needs a fresh capture.
                self.finish_glass_capture(app)
                self.assertEqual(app.glass_surface_key[4], 0.62)
            finally:
                app.close()


    def make_glass_app(self, folder, capture_fn):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                pass

        Path(folder, "settings.json").write_text('{"render_mode":"auto"}', encoding="utf-8")
        root = tk.Tk()
        layered = FakeLayeredWindow(root)
        app = App(root, data_dir=Path(folder), network=False, providers=["claude", "codex"],
                  layered_factory=lambda _root: layered, capture_fn=capture_fn,
                  capture_exclusion_fn=lambda hwnd, excluded: None)
        return root, app, layered

    def run_animation(self, root, app, seconds=3):
        deadline = time.time() + seconds
        while app.animation and time.time() < deadline:
            root.update()
            time.sleep(0.005)

    def test_glass_follows_drag_flight_and_bounce(self):
        def capture(rect):
            # A gradient, so crops at different positions really differ.
            return Image.linear_gradient("L").resize(rect[2:]).convert("RGB")

        with tempfile.TemporaryDirectory() as folder:
            root, app, layered = self.make_glass_app(folder, capture)
            calls = []
            original = ui_module.glass.compose
            try:
                with patch.object(ui_module.glass, "compose",
                                  side_effect=lambda *a, **k: calls.append(a[1]) or original(*a, **k)):
                    self.finish_glass_capture(app)
                    x, y = app.rect[:2]
                    app.on_press(Event(x + 5, y + 5))
                    app.glass_wide_worker.join(timeout=2)
                    left, top, right, bottom = app.area()
                    before = len(calls)
                    app.on_motion(Event(x - 15, y + 5))
                    self.assertGreater(len(calls), before)
                    # A jump outside the wide capture repeats the last glass frame, never opaque.
                    app.on_motion(Event((left + right) // 2, bottom - 40))
                    self.assertIs(layered.images[-1], app.glass_last_frame)
                    app.on_release(Event((left + right) // 2, bottom - 40))
                    self.run_animation(root, app)
                    self.assertIsNone(app.animation)
                    self.assertGreater(len(calls) - before, 10)
                    self.assertEqual(app.dock["side"], "bottom")
                    expected = app.anchored("bottom", app.dock["along"], app.compact_size())
                    self.assertEqual(app.rect, tuple(round(v) for v in expected))
                    # The resting glass starts from the wide capture, without an opaque flash.
                    self.assertIsNotNone(app.glass_background)
                    self.assertIsNone(app.motion_content)
                    self.assertTrue(all(image.mode == "RGBA" for image in layered.images))
            finally:
                app.close()

    def test_slow_bounce_frames_switch_to_precomposed_glass(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app, _layered = self.make_glass_app(
                folder, lambda rect: Image.new("RGB", rect[2:], (90, 110, 140)))
            calls = []
            original = ui_module.glass.compose

            def slow(*args, **kwargs):
                calls.append(args[1])
                time.sleep(0.02)
                return original(*args, **kwargs)

            try:
                self.finish_glass_capture(app)
                app._request_wide_capture(app.rect)
                app.glass_wide_worker.join(timeout=2)
                with patch.object(ui_module.glass, "compose", side_effect=slow):
                    app.squash(1)
                    self.run_animation(root, app)
                self.assertIsNone(app.animation)
                # One base composition plus three measured frames, then the precomposed glass.
                self.assertLessEqual(len(calls), 5)
                self.assertGreaterEqual(len(app.glass_frame_ms), 3)
            finally:
                app.close()


    def test_animation_timer_subtracts_frame_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"])
            delays = []
            original_later = app.later

            def spy(delay, callback):
                # Only the animation's own frames; other loops (hover, poll) also use later().
                if getattr(callback, "__name__", "") == "step":
                    delays.append(delay)
                return original_later(delay, callback)

            def slow_frame(t):
                time.sleep(0.006)
                return (*app.rect, None)

            try:
                app.later = spy
                app.animate(slow_frame, 120)
                self.run_animation(root, app)
                # The first call schedules the start (0); later frames must wait less than 15 ms.
                frame_delays = delays[1:]
                self.assertTrue(frame_delays)
                self.assertTrue(all(d < ui_module.FRAME_MS for d in frame_delays))
            finally:
                app.later = original_later
                app.close()

    def test_glass_toggle_persists_and_updates_capture_exclusion(self):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                pass

        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            layered = FakeLayeredWindow(root)
            exclusions = []
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered,
                      capture_fn=lambda rect: Image.new("RGB", rect[2:], (80, 80, 80)),
                      capture_exclusion_fn=lambda hwnd, enabled: exclusions.append(enabled))
            try:
                self.assertTrue(app.settings["glass"])
                self.assertTrue(exclusions[-1])
                app.glass_var.set(False)
                app.toggle_glass()
                self.assertFalse(app.settings["glass"])
                self.assertFalse(app.glass_active)
                self.assertFalse(exclusions[-1])
                self.assertFalse(load_settings(Path(folder))["glass"])

                app.glass_var.set(True)
                app.toggle_glass()
                self.assertTrue(app.glass_active)
                self.assertTrue(exclusions[-1])
            finally:
                app.close()

    def test_glass_capture_failure_falls_back_and_removes_exclusion(self):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                pass

        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            layered = FakeLayeredWindow(root)
            exclusions = []

            def fail_capture(_rect):
                raise OSError("captura simulada")

            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered, capture_fn=fail_capture,
                      capture_exclusion_fn=lambda hwnd, enabled: exclusions.append(enabled))
            try:
                self.finish_glass_capture(app)
                self.assertFalse(app.glass_active)
                self.assertTrue(app.glass_failed)
                self.assertEqual(exclusions, [True, False])
                self.assertTrue(app.glass_var.get())
                self.assertEqual(layered.images[-1].mode, "RGBA")
            finally:
                app.close()

    def test_glass_refresh_skips_same_background_pauses_and_slows_when_away(self):
        class FakeLayeredWindow:
            def __init__(self, _root):
                self.images = []

            def render(self, image, _x, _y):
                self.images.append(image)

            def close(self, reset_style=False):
                pass

        background = {"color": (60, 70, 80)}
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            layered = FakeLayeredWindow(root)
            app = App(root, data_dir=Path(folder), network=False, providers=["claude"],
                      layered_factory=lambda _root: layered,
                      capture_fn=lambda rect: Image.new("RGB", rect[2:], background["color"]),
                      capture_exclusion_fn=lambda _hwnd, _enabled: None)
            try:
                app.layered = layered
                app.render_mode = "layered"
                app.glass_active = True
                with patch("usage_monitor.ui.glass.compose", wraps=glass.compose) as compose:
                    current = Image.new("RGB", app.rect[2:], background["color"])
                    app.glass_background = current
                    app.glass_signature = current.resize((16, 16), Image.Resampling.BOX).tobytes()
                    app._compose_glass(render.content(app.view(), vertical=False))
                    compose.reset_mock()
                    with patch.object(app, "_start_glass_capture"), patch.object(
                            app, "_schedule_glass") as schedule:
                        app.glass_events.put((app.glass_generation, app.rect, current, False))
                        app._refresh_glass()
                        self.assertEqual(compose.call_count, 0)
                        schedule.assert_called_once_with(100)

                        changed = Image.new("RGB", app.rect[2:], (90, 100, 110))
                        schedule.reset_mock()
                        app.glass_events.put((app.glass_generation, app.rect, changed, False))
                        app._refresh_glass()
                        self.assertEqual(compose.call_count, 1)

                        app.away = True
                        schedule.reset_mock()
                        app.glass_events.put((app.glass_generation, app.rect, changed, False))
                        app._refresh_glass()
                        schedule.assert_called_once_with(1000)

                        app.fullscreen_hidden = True
                        schedule.reset_mock()
                        app._refresh_glass()
                        self.assertEqual(compose.call_count, 1)
                        schedule.assert_not_called()
            finally:
                app.close()

    def test_glass_capture_runs_on_worker_thread(self):
        threads = []
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"])
            try:
                app.capture_fn = lambda rect: threads.append(threading.get_ident()) or Image.new(
                    "RGB", rect[2:], (20, 30, 40))
                app._start_glass_capture()
                app.glass_worker.join(timeout=2)
                self.assertEqual(len(threads), 1)
                self.assertNotEqual(threads[0], threading.get_ident())
                event = app.glass_events.get_nowait()
                self.assertEqual(event[:2], (app.glass_generation, app.rect))
                self.assertFalse(event[3])
            finally:
                app.close()

    def test_glass_opacity_moves_between_compact_rest_and_expanded(self):
        with tempfile.TemporaryDirectory() as folder:
            root, app = self.make_app(folder, ["claude"])
            try:
                app.glass_pointer_out_since = 0
                app._update_glass_target(False, 2.9)
                self.assertEqual(app.glass_opacity, 0.26)

                app._update_glass_target(False, 3.1)
                self.assertEqual(app.glass_transition[2], 0.08)

                app.open = True
                app._update_glass_target(True, 3.2)
                self.assertEqual(app.glass_transition[2], 0.58)
            finally:
                app.close()

    def test_dpi_scale_change_preserves_widget_center(self):
        with tempfile.TemporaryDirectory() as folder, patch(
                "usage_monitor.ui.monitor_scale", return_value=1.0):
            root, app = self.make_app(folder, ["claude"])
            try:
                app.scale = 1.25
                render.SCALE = 1.25
                x, y, width, height = app.rect
                center = (x + width / 2, y + height / 2)
                self.assertTrue(app._update_monitor_scale(*center))
                self.assertEqual(app.scale, 1.0)
                self.assertEqual(render.SCALE, 1.0)
                self.assertAlmostEqual(app.rect[0] + app.rect[2] / 2, center[0], delta=1)
                self.assertAlmostEqual(app.rect[1] + app.rect[3] / 2, center[1], delta=1)
            finally:
                app.close()

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
                      layered_factory=lambda _root: layered,
                      capture_fn=lambda rect: Image.new("RGB", rect[2:], (50, 60, 70)),
                      capture_exclusion_fn=lambda _hwnd, _enabled: None)
            try:
                self.assertEqual(app.render_mode, "colorkey")
                self.assertIsNone(app.layered)
                self.assertEqual(app.canvas.winfo_manager(), "pack")
                self.assertTrue(layered.reset_style)
            finally:
                app.close()

    def test_saved_dock_with_half_pixels_starts(self):
        # Odd widget sizes centred on a saved point used to produce "+446.5" geometries.
        for side, cx, cy in (("right", 1782, 524), ("right", 1782, 525), ("bottom", 901, 1100)):
            with self.subTest(side=side, cy=cy), tempfile.TemporaryDirectory() as folder:
                Path(folder, "settings.json").write_text(
                    f'{{"render_mode":"colorkey","dock":["{side}",{cx},{cy}]}}', encoding="utf-8")
                root = tk.Tk()
                app = App(root, data_dir=Path(folder), network=False)
                try:
                    self.assertTrue(all(isinstance(v, int) for v in app.rect))
                    self.assertEqual(app.dock["side"], side)
                finally:
                    app.close()

if __name__ == "__main__":
    unittest.main()
