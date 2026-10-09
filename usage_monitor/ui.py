"""Floating magnetic widget. Network work never runs in Tk's event loop."""

import ctypes
import ctypes.wintypes
from datetime import datetime
import math
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

from . import capture, render
try:
    from . import glass
except ImportError:  # numpy missing: the widget still works, without the glass look.
    glass = None
from .claude_probe import ClaudeProbe
from .codex import CodexClient
from .fullscreen import foreground_is_fullscreen
from .idle import idle_seconds
from .layered import LayeredWindow
from .models import UsageError
from .notify import send_notification
from .schedule import ClaudeSchedule
from .win32types import monitor_info
from .storage import (DATA_DIR, load_settings, save_settings, load_snapshot, save_snapshot,
                     load_alerts, save_alerts)


MARGIN = 12          # Gap between the widget and the screen edge.
BREAK = 70           # Pull needed to tear the widget off its edge.
RESIST = 0.28        # Fraction of the pull that moves the widget while attached.
CORNER = 40          # Distance at which the widget locks into a corner.
FRAME_MS = 15
GLASS_REFRESH_MS, GLASS_AWAY_MS = 100, 1000
POLL_MS, WATCH_MS, HOVER_MS, CLOCK_MS, CODEX_MS = 200, 3000, 60, 1000, 30000
IDLE_THRESHOLD_SECONDS, RESUME_THRESHOLD_SECONDS = 300, 5
AWAY_CLAUDE_SECONDS, AWAY_CODEX_MS = 1800, 300000
GLASS_SETTLE_SECONDS, GLASS_TRANSITION_MS = 3, 300
GLASS_OPACITY = {"compact": 0.26, "rest": 0.08, "expanded": 0.58}
GLASS_BRIGHT_MINIMUM = {"compact": 0.35, "rest": 0.18, "expanded": 0.62}
GLASS_WIDE_MARGIN = 160     # Extra area captured around the widget while it moves.
GLASS_WIDE_MAX_AGE = 2.0    # Seconds a wide capture may seed the resting glass.
GLASS_SLOW_FRAME_MS = 12    # Above this, the bounce deforms a precomposed glass instead.
LABELS = {"claude": {"5 horas": "Sesión", "7 días": "Semana"}, "codex": {}}


def work_area(x, y, root):
    """Usable rectangle (no taskbar) of the monitor containing the point."""
    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
            info = monitor_info(user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2))
            if info is not None:
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except (AttributeError, OSError, ctypes.ArgumentError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def monitor_scale(x, y, root):
    """Return the effective display scale for the monitor containing a screen point."""
    if os.name == "nt":
        try:
            user32, shcore = ctypes.windll.user32, ctypes.windll.shcore
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
            monitor = user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2)
            if monitor:
                dpi_x, dpi_y = ctypes.wintypes.UINT(), ctypes.wintypes.UINT()
                shcore.GetDpiForMonitor.restype = ctypes.c_long
                shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                    ctypes.POINTER(ctypes.wintypes.UINT),
                                                    ctypes.POINTER(ctypes.wintypes.UINT)]
                if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                    return max(1.0, dpi_x.value / 96)
        except (AttributeError, OSError):
            pass
    return max(1.0, root.winfo_fpixels("1i") / 96)


def set_per_monitor_dpi_awareness():
    """Request per-monitor v2 coordinates before creating Tk windows."""
    if os.name != "nt":
        return
    try:
        setter = ctypes.windll.user32.SetProcessDpiAwarenessContext
        setter.restype = ctypes.wintypes.BOOL
        setter.argtypes = [ctypes.c_void_p]
        if setter(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def vertical(side):
    return side in ("left", "right")


def ease_in(t):
    return t * t * t


def smooth(t):
    return t * t * (3 - 2 * t)


class App:
    def __init__(self, root, data_dir=DATA_DIR, network=True, providers=None, idle_getter=idle_seconds,
                 notifier=send_notification, fullscreen_getter=foreground_is_fullscreen,
                 layered_factory=LayeredWindow, capture_fn=capture.capture_screen,
                 capture_exclusion_fn=capture.set_capture_exclusion):
        self.root = root
        self.data_dir = Path(data_dir)
        self.network = network
        self.idle_getter = idle_getter
        self.away = False
        self.notifier = notifier
        self.fullscreen_getter = fullscreen_getter
        self.fullscreen_hidden = False
        self.events = queue.Queue(maxsize=64)
        self.snapshots = {"claude": None, "codex": None}
        self.errors = {"claude": "", "codex": ""}
        self.loading = {"claude": False, "codex": False}
        self.closed = False
        self.after_ids = set()
        self.workers = []
        self.generations = {"claude": 0, "codex": 0}
        self.client = CodexClient()
        self.claude_probe = ClaudeProbe()
        self.claude_schedule = ClaudeSchedule(time.time())
        self.claude_timer = None
        try:
            self.alert_history = load_alerts(self.data_dir)
            self.alert_history_valid = True
        except UsageError:
            self.alert_history = {}
            self.alert_history_valid = False
        # One pending timer per repeating loop, so toggling a provider never runs it twice.
        self.loops = {"codex": None, "watch": None}
        try:
            self.settings = load_settings(self.data_dir)
        except UsageError:
            self.settings = {"providers": ["claude", "codex"], "dock": None,
                             "alerts": True, "render_mode": "auto"}
        self.render_mode_preference = self.settings.get("render_mode", "auto")
        self.render_mode = "colorkey"
        self.layered = None
        self.capture_fn = capture_fn
        self.capture_exclusion_fn = capture_exclusion_fn
        self.glass_preference = self.settings.get("glass", True)
        self.glass_active = False
        self.glass_failed = False
        self.glass_background = None
        self.glass_signature = None
        self.glass_surface = None
        self.glass_surface_key = None
        self.glass_timer = None
        self.glass_events = queue.Queue(maxsize=1)
        self.glass_worker = None
        self.glass_generation = 0
        self.glass_pointer_out_since = time.monotonic()
        self.glass_opacity = GLASS_OPACITY["compact"]
        self.glass_transition = None
        # Capturing costs ~6 ms of waiting for Windows whatever the size, so animations crop
        # frames from one wide capture taken in a worker thread.
        self.glass_wide = None
        self.glass_wide_worker = None
        self.glass_last_frame = None
        self.glass_frame_ms = []
        self.motion_content = None
        self.alerts_enabled = self.settings.get("alerts", True)
        self.provider_override = providers is not None
        selected = providers if self.provider_override else self.settings.get("providers", ["claude", "codex"])
        self.providers = [key for key in ("claude", "codex") if key in selected]
        self.provider_vars = {}
        for provider in self.providers:
            try:
                self.snapshots[provider] = load_snapshot(provider, self.data_dir)
            except UsageError as exc:
                self.errors[provider] = str(exc)

        saved_dock = self.settings.get("dock")
        point = (saved_dock[1], saved_dock[2]) if saved_dock else (0, 0)
        self.scale = monitor_scale(*point, root)
        render.SCALE = self.scale
        self.open = False
        self.drag = None
        self.animation = None
        self.hover_since = None
        self.leave_since = None
        self.rect = (0, 0, 1, 1)
        self.image = None

        root.title("Uso y límites")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(background=render.KEY)
        self.canvas = tk.Canvas(root, background=render.KEY, highlightthickness=0, borderwidth=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.picture = self.canvas.create_image(0, 0, anchor="nw")
        self.input_widget = None
        self._bind_pointer(self.canvas)
        self.menu = tk.Menu(root, tearoff=0)
        for key, label in (("claude", "Claude"), ("codex", "Codex")):
            variable = tk.BooleanVar(value=key in self.providers)
            self.provider_vars[key] = variable
            self.menu.add_checkbutton(label=label, variable=variable,
                                      command=lambda provider=key: self.toggle_provider(provider))
        self.menu.add_separator()
        self.alerts_var = tk.BooleanVar(value=self.alerts_enabled)
        self.menu.add_checkbutton(label="Avisos", variable=self.alerts_var, command=self.toggle_alerts)
        self.menu.add_separator()
        self.glass_var = tk.BooleanVar(value=self.glass_preference)
        self.menu.add_checkbutton(label="Cristal", variable=self.glass_var, command=self.toggle_glass)
        self.menu.add_separator()
        self.menu.add_command(label="Actualizar", command=self.manual_refresh)
        self.menu.add_separator()
        self.menu.add_command(label="Cerrar", command=self.close)
        self.restore_dock()
        x, y, width, height = self.rect
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.update_idletasks()
        self._enable_layered(layered_factory)
        self.place()
        if network:
            for provider in self.providers:
                self.refresh_provider(provider)
            if "codex" in self.providers:
                self.loop("codex", CODEX_MS, self.auto_codex)
            if "claude" in self.providers:
                self.schedule_claude(self.claude_schedule.delay)
        self.later(POLL_MS, self.poll)
        if "claude" in self.providers:
            self.loop("watch", WATCH_MS, self.watch_claude)
        self.later(HOVER_MS, self.hover)
        self.later(CLOCK_MS, self.tick)

    # Scheduling -------------------------------------------------------------

    def later(self, delay, callback):
        if self.closed:
            return None
        handle = None

        def call():
            self.after_ids.discard(handle)
            if not self.closed:
                callback()
        handle = self.root.after(int(delay), call)
        self.after_ids.add(handle)
        return handle

    def cancel(self, handle):
        if handle is not None:
            self.root.after_cancel(handle)
            self.after_ids.discard(handle)

    # Geometry ---------------------------------------------------------------

    def px(self, value):
        return round(value * self.scale)

    def _update_monitor_scale(self, x, y):
        scale = monitor_scale(x, y, self.root)
        if math.isclose(scale, self.scale, rel_tol=0, abs_tol=0.001):
            return False
        center_x, center_y = self.rect[0] + self.rect[2] / 2, self.rect[1] + self.rect[3] / 2
        self.scale = scale
        render.SCALE = scale
        width, height = self.size()
        self.set_rect(center_x - width / 2, center_y - height / 2, width, height)
        return True

    def compact_size(self, side=None):
        return render.scaled(render.compact_size(len(self.providers), vertical(side or self.dock["side"])))

    def size(self):
        return render.scaled(render.expanded_size(self.view())) if self.open else self.compact_size()

    def area(self):
        x, y, w, h = self.rect
        return work_area(x + w / 2, y + h / 2, self.root)

    def anchored(self, side, along, size, area=None):
        left, top, right, bottom = area or self.area()
        margin, (w, h) = self.px(MARGIN), size
        if vertical(side):
            x = left + margin if side == "left" else right - w - margin
            return x, min(max(top + margin, along), bottom - h - margin), w, h
        y = top + margin if side == "top" else bottom - h - margin
        return min(max(left + margin, along), right - w - margin), y, w, h

    def corner_lock(self, side, along, size, area):
        left, top, right, bottom = area
        margin, lock = self.px(MARGIN), self.px(CORNER)
        low = (top if vertical(side) else left) + margin
        high = (bottom - size[1] if vertical(side) else right - size[0]) - margin
        if along < low + lock:
            return low
        if along > high - lock:
            return high
        return along

    def nearest_side(self, x, y, w, h):
        area = work_area(x + w / 2, y + h / 2, self.root)
        left, top, right, bottom = area
        cx, cy = x + w / 2, y + h / 2
        distances = {"left": cx - left, "right": right - cx, "top": cy - top, "bottom": bottom - cy}
        return min(distances, key=distances.get), area

    def set_rect(self, x, y, w, h, image=None):
        x, y, w, h = round(x), round(y), max(1, round(w)), max(1, round(h))
        if (x, y, w, h) != self.rect or not self.image:
            if (x, y) != self.rect[:2] or (w, h) != self.rect[2:]:
                self.glass_background = None
                self.glass_signature = None
                self.glass_surface = None
                self.glass_surface_key = None
            if (w, h) == self.rect[2:]:
                self.root.geometry(f"+{x}+{y}")
            else:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.rect = (x, y, w, h)
        self.draw(image, (w, h))

    def place(self):
        x, y, w, h = self.anchored(self.dock["side"], self.dock["along"], self.size())
        self.set_rect(x, y, w, h)

    def restore_dock(self):
        saved = self.settings.get("dock")
        area = work_area(*(saved[1:] if saved else (self.root.winfo_screenwidth(), 0)), self.root)
        side = saved[0] if saved else "right"
        size = self.compact_size(side)
        if saved:
            along = saved[2] - size[1] / 2 if vertical(side) else saved[1] - size[0] / 2
        else:
            along = area[1]
        self.dock = {"side": side, "along": self.corner_lock(side, along, size, area)}
        # Centring on a saved point can leave half pixels, which Tk geometry rejects.
        self.rect = tuple(round(v) for v in self.anchored(side, self.dock["along"], size, area))

    def save_dock(self):
        x, y, w, h = self.rect
        self.settings["dock"] = [self.dock["side"], round(x + w / 2), round(y + h / 2)]
        try:
            save_settings(self.settings, self.data_dir)
        except UsageError:
            pass

    # Drawing ----------------------------------------------------------------

    def view(self):
        now = time.time()
        providers = []
        for key in self.providers:
            snapshot = self.snapshots[key]
            rows, percent, age = [], None, ""
            if snapshot:
                for window in snapshot.windows[:2]:
                    rows.append(render.Row(LABELS[key].get(window.label, window.label), window.used_percent,
                        render.reset_label(window.resets_at, now),
                        render.countdown(window.resets_at, now) if window.resets_at else ""))
                percent = snapshot.windows[0].used_percent if snapshot.windows else None
                age = render.age_label(snapshot.observed_at, now)
            if self.away:
                age = f"En pausa: sin actividad · {age}" if age else "En pausa: sin actividad"
            message = self.errors[key] or ("" if snapshot else "Sin datos todavía.")
            providers.append(render.Provider(key, percent, tuple(rows), age, message, self.loading[key]))
        return providers

    def draw(self, image=None, size=None):
        moving_glass = False
        if image is None and self.glass_active and self.layered and (self.animation or self.drag) \
                and not self.open:
            image = self._moving_glass(self.rect)
            moving_glass = image is not None
        if image is None and self.glass_active and self.glass_background is None \
                and not self.animation and not self.drag:
            self._seed_glass_from_wide()
        if image is None:
            providers = self.view()
            if (self.glass_active and self.glass_background is not None
                    and not self.animation and not self.drag):
                image = render.content(providers, vertical(self.dock["side"]), self.open)
            else:
                image = (render.expanded(providers, self.render_mode) if self.open else
                         render.compact(providers, vertical(self.dock["side"]), self.render_mode))
        if size and image.size != size:
            image = image.resize(size)
        if self.layered:
            if self.glass_active and not self.animation and not self.drag and not moving_glass:
                if self.glass_background is None:
                    self._schedule_glass(0)
                    image = (render.expanded(self.view(), self.render_mode) if self.open else
                             render.compact(self.view(), vertical(self.dock["side"]), self.render_mode))
                else:
                    try:
                        image = self._compose_glass(image)
                    except Exception:
                        self._fail_glass()
                        image = (render.expanded(self.view(), self.render_mode) if self.open else
                                 render.compact(self.view(), vertical(self.dock["side"]), self.render_mode))
            try:
                self.layered.render(image, self.rect[0], self.rect[1])
            except Exception:
                self._activate_colorkey()
                self.image = None
                self.draw()
                return
            self.image = image
            return
        self.image = ImageTk.PhotoImage(image, master=self.root)
        self.canvas.itemconfigure(self.picture, image=self.image)

    def _bind_pointer(self, widget):
        sequences = ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>",
                     "<Double-Button-1>", "<Button-3>")
        if self.input_widget:
            for sequence in sequences:
                self.input_widget.unbind(sequence)
        widget.bind("<ButtonPress-1>", self.on_press)
        widget.bind("<B1-Motion>", self.on_motion)
        widget.bind("<ButtonRelease-1>", self.on_release)
        widget.bind("<Double-Button-1>", lambda _e: self.manual_refresh())
        widget.bind("<Button-3>", lambda event: self.menu.tk_popup(event.x_root, event.y_root))
        self.input_widget = widget

    def _enable_layered(self, layered_factory):
        if self.render_mode_preference == "colorkey":
            self._activate_colorkey()
            return
        try:
            self.layered = layered_factory(self.root)
            self.render_mode = "layered"
            self.canvas.pack_forget()
            self._bind_pointer(self.root)
            if self.glass_preference:
                self._enable_glass()
        except Exception:
            self._activate_colorkey()

    def _activate_colorkey(self):
        self._disable_glass()
        if self.layered:
            try:
                self.layered.close(reset_style=True)
            except Exception:
                pass
            self.layered = None
        self.render_mode = "colorkey"
        try:
            self.root.attributes("-transparentcolor", render.KEY)
        except tk.TclError:
            pass
        if not self.canvas.winfo_manager():
            self.canvas.pack(fill="both", expand=True)
        self._bind_pointer(self.canvas)

    def _enable_glass(self):
        if not self.layered or self.glass_failed or glass is None:
            return False
        try:
            hwnd = int(self.root.wm_frame(), 16)
            self.glass_generation += 1
            while True:
                try:
                    self.glass_events.get_nowait()
                except queue.Empty:
                    break
            self.glass_active = True
            self.capture_exclusion_fn(hwnd, True)
            self.glass_background = None
            self.glass_signature = None
            self.glass_surface = None
            self.glass_surface_key = None
            self._schedule_glass(0)
            return True
        except Exception:
            self._fail_glass()
            return False

    def _disable_glass(self):
        if self.glass_timer is not None:
            self.cancel(self.glass_timer)
            self.glass_timer = None
        was_active = self.glass_active
        self.glass_active = False
        self.glass_generation += 1
        self.glass_background = None
        self.glass_signature = None
        self.glass_surface = None
        self.glass_surface_key = None
        if was_active:
            try:
                hwnd = int(self.root.wm_frame(), 16)
                self.capture_exclusion_fn(hwnd, False)
            except Exception:
                pass

    def _fail_glass(self):
        self._disable_glass()
        self.glass_failed = True
        self.image = None

    def _schedule_glass(self, delay=None):
        if not self.glass_active or self.fullscreen_hidden or self.closed:
            return
        if self.glass_timer is not None:
            self.cancel(self.glass_timer)
        if delay is None:
            delay = GLASS_AWAY_MS if self.away else GLASS_REFRESH_MS
        self.glass_timer = self.later(delay, self._refresh_glass)

    def _refresh_glass(self):
        self.glass_timer = None
        if not self.glass_active or self.fullscreen_hidden or self.closed:
            return
        try:
            generation, rect, image, failed = self.glass_events.get_nowait()
        except queue.Empty:
            generation = rect = image = failed = None
        if generation == self.glass_generation and rect == self.rect:
            if failed:
                self._fail_glass()
                if self.layered:
                    self.draw()
                return
            signature = image.resize((16, 16), Image.Resampling.BOX).tobytes()
            if signature != self.glass_signature:
                self.glass_background = image.convert("RGB")
                self.glass_signature = signature
                self.glass_surface = None
                self.glass_surface_key = None
                if not self.animation and not self.drag:
                    self.draw()
            else:
                self._current_glass_opacity()
                rendered_opacity = self.glass_surface_key[2] if self.glass_surface_key else None
                if (self.glass_surface is None or rendered_opacity != round(self.glass_opacity, 3)) \
                        and not self.animation and not self.drag:
                    self.glass_surface = None
                    self.draw()
        if self.glass_worker is None or not self.glass_worker.is_alive():
            self._start_glass_capture()
        self._schedule_glass(GLASS_AWAY_MS if self.away else GLASS_REFRESH_MS)

    def _start_glass_capture(self):
        rect, generation = self.rect, self.glass_generation

        def capture_background():
            try:
                image = self.capture_fn(rect)
                if not isinstance(image, Image.Image) or image.size != rect[2:]:
                    raise ValueError("La captura del fondo no es válida.")
                result = (generation, rect, image.convert("RGB"), False)
            except Exception:
                result = (generation, rect, None, True)
            if self.closed:
                return
            try:
                self.glass_events.put_nowait(result)
            except queue.Full:
                try:
                    self.glass_events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.glass_events.put_nowait(result)
                except queue.Full:
                    pass

        self.glass_worker = threading.Thread(target=capture_background, daemon=True)
        self.glass_worker.start()

    def _current_glass_opacity(self, now=None):
        now = time.monotonic() if now is None else now
        if self.glass_transition is None:
            return self.glass_opacity
        started, initial, target = self.glass_transition
        progress = min(1, max(0, (now - started) * 1000 / GLASS_TRANSITION_MS))
        eased = smooth(progress)
        value = initial + (target - initial) * eased
        if progress >= 1:
            self.glass_transition = None
        self.glass_opacity = value
        return value

    def _update_glass_target(self, pointer_inside, now=None):
        now = time.monotonic() if now is None else now
        if pointer_inside:
            self.glass_pointer_out_since = None
        elif self.glass_pointer_out_since is None:
            self.glass_pointer_out_since = now
        if self.open:
            target = GLASS_OPACITY["expanded"]
        elif pointer_inside or now - self.glass_pointer_out_since < GLASS_SETTLE_SECONDS:
            target = GLASS_OPACITY["compact"]
        else:
            target = GLASS_OPACITY["rest"]
        current = self._current_glass_opacity(now)
        active_target = self.glass_transition[2] if self.glass_transition else current
        if target != active_target:
            self.glass_transition = (now, current, target)
            self.glass_surface = None

    def _compose_glass(self, content_image):
        if self.glass_background is None:
            raise ValueError("El fondo del cristal todavía no está disponible.")
        opacity = self._current_glass_opacity()
        # Minimum veil over light backgrounds, per state: rest, compact, expanded.
        if self.open:
            bright_minimum = GLASS_BRIGHT_MINIMUM["expanded"]
        elif opacity >= 0.2:
            bright_minimum = GLASS_BRIGHT_MINIMUM["compact"]
        else:
            bright_minimum = GLASS_BRIGHT_MINIMUM["rest"]
        logical = (render.expanded_size(self.view()) if self.open else
                   render.compact_size(len(self.providers), vertical(self.dock["side"])))
        radius = self.px(render.shape_radius(logical))
        key = (self.glass_signature, self.rect[2:], round(opacity, 3), radius, bright_minimum, self.scale)
        if self.glass_surface is None or key != self.glass_surface_key:
            tint = glass.Tint(opacity=opacity, bright_minimum=bright_minimum)
            self.glass_surface = glass.compose(
                self.glass_background, self.rect[2:], tint, radius, self.scale)
            self.glass_surface_key = key
        result = self.glass_surface.copy()
        result.alpha_composite(content_image.convert("RGBA"))
        return result

    def _request_wide_capture(self, rect):
        """Capture a wide area around rect in a worker thread, unless one is in flight."""
        if not self.glass_active or self.closed:
            return
        if self.glass_wide_worker is not None and self.glass_wide_worker.is_alive():
            return
        margin = self.px(GLASS_WIDE_MARGIN)
        x, y, w, h = rect
        area = (round(x - margin), round(y - margin), round(w + 2 * margin), round(h + 2 * margin))
        generation = self.glass_generation

        def work():
            try:
                image = self.capture_fn(area)
                valid = isinstance(image, Image.Image) and image.size == area[2:]
            except Exception:
                valid = False
            if valid and generation == self.glass_generation and not self.closed:
                self.glass_wide = (area, image.convert("RGB"), time.monotonic())

        self.glass_wide_worker = threading.Thread(target=work, daemon=True)
        self.glass_wide_worker.start()

    def _wide_background(self, rect, max_age=None):
        """Crop the background behind rect from the wide capture, if it covers it."""
        wide = self.glass_wide
        if wide is None:
            return None
        (wx, wy, ww, wh), image, taken = wide
        if max_age is not None and time.monotonic() - taken > max_age:
            return None
        x, y, w, h = (round(v) for v in rect)
        if wx <= x and wy <= y and x + w <= wx + ww and y + h <= wy + wh:
            return image.crop((x - wx, y - wy, x - wx + w, y - wy + h))
        return None

    def _seed_glass_from_wide(self):
        # After an animation or on expanding, start from the recent wide capture instead of
        # showing the opaque look until the next regular capture arrives.
        background = self._wide_background(self.rect, GLASS_WIDE_MAX_AGE)
        if background is not None:
            self.glass_background = background
            self.glass_signature = background.resize((16, 16), Image.Resampling.BOX).tobytes()
            self.glass_surface = None
            self.glass_surface_key = None

    def _compact_radius(self, side=None):
        logical = render.compact_size(len(self.providers), vertical(side or self.dock["side"]))
        return self.px(render.shape_radius(logical))

    def _motion_glass(self, rect, content, radius):
        """Glass for a moving or deforming widget, or None to fall back to the opaque look."""
        if not self.glass_active:
            return None
        self._request_wide_capture(rect)
        background = self._wide_background(rect)
        size = (max(1, round(rect[2])), max(1, round(rect[3])))
        if background is None:
            # No wide capture yet: repeat the last glass frame rather than flashing opaque.
            last = self.glass_last_frame
            return last if last is not None and last.size == size else None
        opacity = self._current_glass_opacity()
        floor = GLASS_BRIGHT_MINIMUM["compact"] if opacity >= 0.2 else GLASS_BRIGHT_MINIMUM["rest"]
        try:
            surface = glass.compose(background, size, glass.Tint(opacity=opacity, bright_minimum=floor),
                                    radius, self.scale)
        except Exception:
            return None
        surface.alpha_composite(content if content.size == size else content.resize(size))
        self.glass_last_frame = surface
        return surface

    def _moving_glass(self, rect):
        if self.motion_content is None:
            self.motion_content = render.content(self.view(), vertical(self.dock["side"]), False)
        return self._motion_glass(rect, self.motion_content, self._compact_radius())

    def toggle_glass(self):
        enabled = bool(self.glass_var.get())
        self.glass_preference = enabled
        self.settings["glass"] = enabled
        try:
            save_settings(self.settings, self.data_dir)
        except UsageError:
            self.glass_preference = not enabled
            self.glass_var.set(self.glass_preference)
            self.settings["glass"] = self.glass_preference
            return
        if enabled:
            self.glass_failed = False
            if self._enable_glass():
                self.draw()
        else:
            self._disable_glass()
            self.draw()

    def redraw(self):
        if self.animation or self.drag:
            return
        target = self.size()
        if target != self.rect[2:]:
            self.place()
        else:
            self.draw()

    # Animation --------------------------------------------------------------

    def animate(self, frames_fn, duration, done=None):
        """frames_fn(t) -> (x, y, w, h, image) for t in [0, 1]."""
        self.stop_animation()
        start = time.perf_counter()

        def step():
            began = time.perf_counter()
            t = min(1.0, (began - start) * 1000 / duration)
            self.set_rect(*frames_fn(t))
            if t < 1:
                # Tk timers on Windows tick every ~15.6 ms: subtract the frame's own work, or a
                # few milliseconds of glass push every frame to two ticks (about 32 FPS).
                spent = (time.perf_counter() - began) * 1000
                self.animation = self.later(max(1, round(FRAME_MS - spent)), step)
            else:
                self.animation = None
                if done:
                    done()
        self.animation = self.later(0, step)

    def stop_animation(self):
        self.cancel(self.animation)
        self.animation = None

    def fly_to_wall(self, strength, duration):
        """Accelerate from the current rectangle into the docked one, then bounce."""
        sx, sy, sw, sh = self.rect
        tx, ty, tw, th = self.anchored(self.dock["side"], self.dock["along"], self.compact_size())
        image = render.compact(self.view(), vertical(self.dock["side"]), self.render_mode)
        # Keep the window size fixed while flying: resizing a layered window leaves square trails.
        sx, sy = sx + (sw - tw) / 2, sy + (sh - th) / 2
        content, radius = self._animation_content()

        def frame(t):
            e = ease_in(t)
            fx, fy = sx + (tx - sx) * e, sy + (ty - sy) * e
            frame_image = image
            if content is not None:
                frame_image = self._motion_glass((fx, fy, tw, th), content, radius) or image
            return fx, fy, tw, th, frame_image
        self.animate(frame, duration, lambda: self.squash(strength))

    def squash(self, strength):
        """Ball-like impact: flatten against the wall, rebound, settle."""
        if strength <= 0.02:
            self._end_motion()
            return
        side = self.dock["side"]
        x, y, w, h = self.anchored(side, self.dock["along"], self.compact_size())
        image = render.compact(self.view(), vertical(side), self.render_mode)
        content, radius = self._animation_content()
        base_glass = self._motion_glass((x, y, w, h), content, radius) if content is not None else None
        self.glass_frame_ms = []
        s = strength
        # (time, perpendicular scale, parallel scale, offset from the wall)
        keys = [(0, 1 - .32 * s, 1 + .16 * s, 0), (.35, 1 + .1 * s, 1 - .06 * s, 10 * s),
                (.6, 1 - .12 * s, 1 + .05 * s, 0), (.8, 1 + .03 * s, 1 - .02 * s, 3 * s), (1, 1, 1, 0)]
        perp, par = (w, h) if vertical(side) else (h, w)
        # The window stays still on a padded stage; only the picture inside deforms.
        pad = self.px(16)
        stage = (x - pad, y - pad, w + pad * 2, h + pad * 2)

        def frame(t):
            for (t0, n0, p0, d0), (t1, n1, p1, d1) in zip(keys, keys[1:]):
                if t <= t1:
                    k = smooth((t - t0) / (t1 - t0))
                    n, p, d = n0 + (n1 - n0) * k, p0 + (p1 - p0) * k, d0 + (d1 - d0) * k
                    break
            new_perp, new_par, offset = perp * n, par * p, self.px(d)
            if vertical(side):
                nx = pad + offset if side == "left" else pad + w - new_perp - offset
                box = (nx, pad + (h - new_par) / 2, new_perp, new_par)
            else:
                ny = pad + offset if side == "top" else pad + h - new_perp - offset
                box = (pad + (w - new_par) / 2, ny, new_par, new_perp)
            picture = image
            if content is not None:
                recent = self.glass_frame_ms[-3:]
                if len(recent) == 3 and sum(recent) / 3 > GLASS_SLOW_FRAME_MS and base_glass is not None:
                    picture = base_glass
                else:
                    started = time.perf_counter()
                    screen_box = (stage[0] + box[0], stage[1] + box[1], box[2], box[3])
                    frame_radius = radius * min(box[2] / w, box[3] / h)
                    picture = self._motion_glass(screen_box, content, frame_radius) or base_glass or image
                    self.glass_frame_ms.append((time.perf_counter() - started) * 1000)
            return (*stage, render.on_stage(picture, stage[2:], box, self.render_mode))
        self.animate(frame, 620, self._end_motion)

    def _animation_content(self):
        """Content and radius for glass animation frames, or (None, None) without glass."""
        if not (self.glass_active and self.layered):
            return None, None
        self.motion_content = render.content(self.view(), vertical(self.dock["side"]), False)
        return self.motion_content, self._compact_radius()

    def _end_motion(self):
        self.motion_content = None
        self.glass_last_frame = None
        self.place()

    # Pointer ----------------------------------------------------------------

    def on_press(self, event):
        self.stop_animation()
        x, y = self.rect[:2]
        self.drag = {"dx": event.x_root - x, "dy": event.y_root - y, "start": (event.x_root, event.y_root),
                     "attached": True, "pull": 0, "moved": False}
        self.motion_content = None
        # Until the first wide capture arrives, a fast drag repeats the glass already on screen.
        self.glass_last_frame = self.image if self.glass_active and not self.open else None
        self._request_wide_capture(self.rect)

    def on_motion(self, event):
        drag = self.drag
        if not drag:
            return
        if not drag["moved"]:
            if math.dist(drag["start"], (event.x_root, event.y_root)) < self.px(4):
                return
            drag["moved"] = True
            if self.open:
                self.open = False
                self.place()
                w, h = self.rect[2:]
                drag["dx"], drag["dy"] = w / 2, h / 2
        side = self.dock["side"]
        size = self.compact_size()
        x, y = event.x_root - drag["dx"], event.y_root - drag["dy"]
        if drag["attached"]:
            along = y if vertical(side) else x
            bx, by, _, _ = self.anchored(side, along, size)
            pull = {"left": x - bx, "right": bx - x, "top": y - by, "bottom": by - y}[side]
            drag["pull"] = max(0, pull)
            if drag["pull"] < self.px(BREAK):
                shift = drag["pull"] * RESIST
                dx = shift if side == "left" else -shift if side == "right" else 0
                dy = shift if side == "top" else -shift if side == "bottom" else 0
                self.dock["along"] = along
                self.set_rect(bx + dx, by + dy, *size)
                return
            drag["attached"] = False
        self.set_rect(x, y, *size)

    def on_release(self, _event):
        drag, self.drag = self.drag, None
        if not drag or not drag["moved"]:
            return
        center_x, center_y = self.rect[0] + self.rect[2] / 2, self.rect[1] + self.rect[3] / 2
        self._update_monitor_scale(center_x, center_y)
        x, y, w, h = self.rect
        if drag["attached"]:
            self.dock["along"] = self.corner_lock(self.dock["side"], self.dock["along"], (w, h), self.area())
            strength = min(1, drag["pull"] / self.px(BREAK)) * 0.6 if drag["pull"] > self.px(12) else 0
            self.fly_to_wall(strength, 160)
        else:
            side, area = self.nearest_side(x, y, w, h)
            size = self.compact_size(side)
            cx, cy = x + w / 2, y + h / 2
            along = cy - size[1] / 2 if vertical(side) else cx - size[0] / 2
            self.dock = {"side": side, "along": self.corner_lock(side, along, size, area)}
            self.fly_to_wall(1, 220)
        self.save_dock()

    def hover(self):
        inside = False
        if not self.drag and not self.animation:
            px, py = self.root.winfo_pointerxy()
            x, y, w, h = self.rect
            inside = x <= px < x + w and y <= py < y + h
            now = time.monotonic()
            if inside and not self.open:
                self.leave_since = None
                self.hover_since = self.hover_since or now
                if now - self.hover_since >= 0.12:
                    self.open, self.hover_since = True, None
                    self.place()
            elif not inside and self.open:
                self.hover_since = None
                self.leave_since = self.leave_since or now
                if now - self.leave_since >= 0.25:
                    self.open, self.leave_since = False, None
                    self.place()
            else:
                self.hover_since = None if not inside else self.hover_since
                self.leave_since = None
        self._update_glass_target(inside)
        self.later(HOVER_MS, self.hover)

    # Data -------------------------------------------------------------------

    def manual_refresh(self):
        for provider in self.providers:
            self.refresh_provider(provider)
        if "claude" in self.providers:
            self.claude_schedule.reset(time.time())
            self.schedule_claude(self.claude_schedule.delay)
        if not self.open and not self.drag:
            self.squash(0.35)

    def run(self, provider, fetch):
        if provider not in self.providers or self.loading[provider] or self.closed or not self.network:
            return
        generation = self.generations[provider]
        self.loading[provider] = True
        self.redraw()

        def work():
            try:
                snapshot, error = fetch(), ""
            except UsageError as exc:
                snapshot, error = None, str(exc)
            except Exception:
                snapshot, error = None, "Error interno. Reinicia la app."
            if not self.closed:
                try:
                    self.events.put((provider, snapshot, error, "probe", generation), timeout=1)
                except queue.Full:
                    pass
        worker = threading.Thread(target=work, daemon=True)
        self.workers = [w for w in self.workers if w.is_alive()] + [worker]
        worker.start()

    def refresh_provider(self, provider):
        if provider == "codex":
            self.run(provider, self.client.fetch)
        elif provider == "claude":
            self.run(provider, self.claude_probe.fetch)

    def refresh_codex(self):
        self.refresh_provider("codex")

    def refresh_claude(self):
        self.refresh_provider("claude")

    def loop(self, name, delay, callback):
        self.cancel(self.loops[name])
        self.loops[name] = self.later(delay, callback)

    def auto_codex(self):
        self.loops["codex"] = None
        if "codex" in self.providers:
            self.refresh_codex()
            self.loop("codex", AWAY_CODEX_MS if self.away else CODEX_MS, self.auto_codex)

    def schedule_claude(self, delay):
        self.cancel(self.claude_timer)
        self.claude_timer = None
        if "claude" in self.providers:
            if self.away:
                delay = max(delay, AWAY_CLAUDE_SECONDS)
            self.claude_timer = self.later(delay * 1000, self.refresh_claude)

    def update_idle_state(self):
        try:
            idle = max(0, float(self.idle_getter()))
        except (TypeError, ValueError, OSError):
            idle = 0
        if not self.away and idle >= IDLE_THRESHOLD_SECONDS:
            self.away = True
            if "claude" in self.providers:
                self.schedule_claude(max(self.claude_schedule.delay, AWAY_CLAUDE_SECONDS))
            if "codex" in self.providers:
                self.loop("codex", AWAY_CODEX_MS, self.auto_codex)
            self.redraw()
        elif self.away and idle < RESUME_THRESHOLD_SECONDS:
            self.away = False
            if "claude" in self.providers:
                self.claude_schedule.reset(time.time())
            for provider in self.providers:
                self.refresh_provider(provider)
            if "claude" in self.providers:
                self.schedule_claude(self.claude_schedule.delay)
            if "codex" in self.providers:
                self.loop("codex", CODEX_MS, self.auto_codex)
            self.redraw()

    def observe_claude(self, snapshot, scheduled):
        # Readings from the status line can speed polling up, never slow it down.
        if self.claude_schedule.observe(snapshot, time.time(), scheduled) and not self.loading["claude"]:
            self.schedule_claude(self.claude_schedule.delay)

    def poll(self):
        changed = False
        while True:
            try:
                provider, snapshot, error, *origin = self.events.get_nowait()
            except queue.Empty:
                break
            probe = bool(origin and origin[0] == "probe")
            generation = origin[1] if probe and len(origin) > 1 else self.generations[provider]
            if provider not in self.providers or generation != self.generations[provider]:
                continue
            if provider == "claude" and snapshot:
                self.observe_claude(snapshot, scheduled=probe)
            if snapshot:
                previous = self.snapshots[provider]
                if not previous or snapshot.observed_at >= previous.observed_at:
                    try:
                        save_snapshot(provider, snapshot, self.data_dir)
                    except UsageError as exc:
                        error = str(exc)
                    self.snapshots[provider] = snapshot
                    self.check_alerts(provider, snapshot)
            self.errors[provider] = error
            if probe:
                self.loading[provider] = False
                if provider == "claude":
                    # A failing query (no session, no network) must not retry every 30 seconds.
                    delay = self.claude_schedule.delay if snapshot else max(self.claude_schedule.delay, 300)
                    self.schedule_claude(delay)
            changed = True
        if changed:
            self.redraw()
        self.later(POLL_MS, self.poll)

    def watch_claude(self):
        self.loops["watch"] = None
        if "claude" not in self.providers:
            return
        # Claude Code's status line writes this file from another process.
        try:
            snapshot = load_snapshot("claude", self.data_dir)
        except UsageError:
            snapshot = None
        previous = self.snapshots["claude"]
        if snapshot and (previous is None or snapshot.observed_at > previous.observed_at):
            self.observe_claude(snapshot, scheduled=False)
            self.snapshots["claude"] = snapshot
            self.errors["claude"] = ""
            # Status line readings must alert too, not only the widget's own queries.
            self.check_alerts("claude", snapshot)
            self.redraw()
        self.loop("watch", WATCH_MS, self.watch_claude)

    def toggle_provider(self, provider):
        selected = [key for key in ("claude", "codex") if self.provider_vars[key].get()]
        if not selected:
            self.provider_vars[provider].set(True)
            return
        previous = set(self.providers)
        self.providers = selected
        if not self.provider_override:
            self.settings["providers"] = selected
        for key in ("claude", "codex"):
            if key in previous and key not in selected:
                self.generations[key] += 1
                self.loading[key] = False
        if "claude" not in selected:
            self.cancel(self.claude_timer)
            self.claude_timer = None
            self.cancel(self.loops["watch"])
            self.loops["watch"] = None
        elif "claude" not in previous:
            self.generations["claude"] += 1
            self.claude_schedule.reset(time.time())
            self.refresh_claude()
            self.schedule_claude(self.claude_schedule.delay)
            self.loop("watch", WATCH_MS, self.watch_claude)
        if "codex" not in selected:
            self.cancel(self.loops["codex"])
            self.loops["codex"] = None
        elif "codex" not in previous:
            self.generations["codex"] += 1
            self.refresh_codex()
            self.loop("codex", AWAY_CODEX_MS if self.away else CODEX_MS, self.auto_codex)
        if not self.provider_override:
            try:
                save_settings(self.settings, self.data_dir)
            except UsageError as exc:
                self.errors[provider] = str(exc)
        self.open = False
        self.dock["along"] = self.corner_lock(
            self.dock["side"], self.dock["along"], self.compact_size(), self.area())
        self.place()

    def toggle_alerts(self):
        enabled = self.alerts_var.get()
        previous = self.alerts_enabled
        self.alerts_enabled = enabled
        self.settings["alerts"] = enabled
        try:
            save_settings(self.settings, self.data_dir)
        except UsageError:
            self.alerts_enabled = previous
            self.alerts_var.set(previous)

    def check_alerts(self, provider, snapshot):
        if not self.alerts_enabled or not self.alert_history_valid:
            return
        for window in snapshot.windows:
            label = window.label.rsplit("·", 1)[-1].strip()
            if label != "5 horas" or window.used_percent < 90 or window.resets_at is None:
                continue
            key = (provider, window.resets_at)
            if key in self.alert_history:
                continue
            try:
                reset_time = datetime.fromtimestamp(window.resets_at).strftime("%H:%M")
                self.notifier(
                    f"{provider.capitalize()} al 90 %",
                    f"Se restablece a las {reset_time}.",
                )
            except Exception:
                pass
            self.alert_history[key] = time.time()
            try:
                save_alerts(self.alert_history, self.data_dir)
            except UsageError:
                pass

    def tick(self):
        # Expanded text shows ages in seconds; the compact view only needs occasional repaint.
        self.update_idle_state()
        self.update_fullscreen_state()
        if self.open:
            self.redraw()
        self.ticks = getattr(self, "ticks", 0) + 1
        if self.ticks % 5 == 0:
            self.root.attributes("-topmost", True)
            if not self.open:
                self.redraw()
        self.later(CLOCK_MS, self.tick)

    def update_fullscreen_state(self):
        x, y, width, height = self.rect
        try:
            fullscreen = bool(self.fullscreen_getter((x + width // 2, y + height // 2)))
        except Exception:
            fullscreen = False
        if fullscreen and not self.drag and not self.animation and not self.fullscreen_hidden:
            self.root.withdraw()
            self.fullscreen_hidden = True
            if self.glass_timer is not None:
                self.cancel(self.glass_timer)
                self.glass_timer = None
        elif not fullscreen and self.fullscreen_hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.fullscreen_hidden = False
            self.place()
            self._schedule_glass(0)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for handle in list(self.after_ids):
            self.root.after_cancel(handle)
        self.after_ids.clear()
        self.glass_timer = None
        self._disable_glass()
        self.client.close()
        self.claude_probe.close()
        if self.layered:
            self.layered.close()
        for worker in self.workers:
            worker.join(timeout=3)
        if self.glass_worker:
            self.glass_worker.join(timeout=3)
        self.root.destroy()


def run(providers=None):
    set_per_monitor_dpi_awareness()
    root = tk.Tk()
    App(root, providers=providers)
    root.mainloop()
