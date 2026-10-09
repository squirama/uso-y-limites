"""Floating magnetic widget. Network work never runs in Tk's event loop."""

import ctypes
import ctypes.wintypes
import math
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from PIL import ImageTk

from . import render
from .claude_probe import ClaudeProbe
from .codex import CodexClient
from .models import UsageError
from .schedule import ClaudeSchedule
from .storage import DATA_DIR, load_settings, save_settings, load_snapshot, save_snapshot


MARGIN = 12          # Gap between the widget and the screen edge.
BREAK = 70           # Pull needed to tear the widget off its edge.
RESIST = 0.28        # Fraction of the pull that moves the widget while attached.
CORNER = 40          # Distance at which the widget locks into a corner.
FRAME_MS = 15
POLL_MS, WATCH_MS, HOVER_MS, CLOCK_MS, CODEX_MS = 200, 3000, 60, 1000, 30000
LABELS = {"claude": {"5 horas": "Sesión", "7 días": "Semana"}, "codex": {}}


def work_area(x, y, root):
    """Usable rectangle (no taskbar) of the monitor containing the point."""
    if os.name == "nt":
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        try:
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            monitor = user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def vertical(side):
    return side in ("left", "right")


def ease_in(t):
    return t * t * t


def smooth(t):
    return t * t * (3 - 2 * t)


class App:
    def __init__(self, root, data_dir=DATA_DIR, network=True, providers=None):
        self.root = root
        self.data_dir = Path(data_dir)
        self.network = network
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
        # One pending timer per repeating loop, so toggling a provider never runs it twice.
        self.loops = {"codex": None, "watch": None}
        try:
            self.settings = load_settings(self.data_dir)
        except UsageError:
            self.settings = {"providers": ["claude", "codex"], "dock": None}
        self.provider_override = providers is not None
        selected = providers if self.provider_override else self.settings.get("providers", ["claude", "codex"])
        self.providers = [key for key in ("claude", "codex") if key in selected]
        self.provider_vars = {}
        for provider in self.providers:
            try:
                self.snapshots[provider] = load_snapshot(provider, self.data_dir)
            except UsageError as exc:
                self.errors[provider] = str(exc)

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
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
        try:
            root.attributes("-transparentcolor", render.KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(root, background=render.KEY, highlightthickness=0, borderwidth=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.picture = self.canvas.create_image(0, 0, anchor="nw")
        self.menu = tk.Menu(root, tearoff=0)
        for key, label in (("claude", "Claude"), ("codex", "Codex")):
            variable = tk.BooleanVar(value=key in self.providers)
            self.provider_vars[key] = variable
            self.menu.add_checkbutton(label=label, variable=variable,
                                      command=lambda provider=key: self.toggle_provider(provider))
        self.menu.add_separator()
        self.menu.add_command(label="Actualizar", command=self.manual_refresh)
        self.menu.add_separator()
        self.menu.add_command(label="Cerrar", command=self.close)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.manual_refresh())
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.restore_dock()
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
        x, y, w, h = self.anchored(side, self.dock["along"], size, area)
        self.rect = (x, y, w, h)

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
            message = self.errors[key] or ("" if snapshot else "Sin datos todavía.")
            providers.append(render.Provider(key, percent, tuple(rows), age, message, self.loading[key]))
        return providers

    def draw(self, image=None, size=None):
        if image is None:
            providers = self.view()
            image = render.expanded(providers) if self.open else render.compact(providers, vertical(self.dock["side"]))
        if size and image.size != size:
            image = image.resize(size)
        self.image = ImageTk.PhotoImage(image, master=self.root)
        self.canvas.itemconfigure(self.picture, image=self.image)

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
            t = min(1.0, (time.perf_counter() - start) * 1000 / duration)
            self.set_rect(*frames_fn(t))
            if t < 1:
                self.animation = self.later(FRAME_MS, step)
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
        image = render.compact(self.view(), vertical(self.dock["side"]))
        # Keep the window size fixed while flying: resizing a layered window leaves square trails.
        sx, sy = sx + (sw - tw) / 2, sy + (sh - th) / 2

        def frame(t):
            e = ease_in(t)
            return sx + (tx - sx) * e, sy + (ty - sy) * e, tw, th, image
        self.animate(frame, duration, lambda: self.squash(strength))

    def squash(self, strength):
        """Ball-like impact: flatten against the wall, rebound, settle."""
        if strength <= 0.02:
            self.place()
            return
        side = self.dock["side"]
        x, y, w, h = self.anchored(side, self.dock["along"], self.compact_size())
        image = render.compact(self.view(), vertical(side))
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
            return (*stage, render.on_stage(image, stage[2:], box))
        self.animate(frame, 620, self.place)

    # Pointer ----------------------------------------------------------------

    def on_press(self, event):
        self.stop_animation()
        x, y = self.rect[:2]
        self.drag = {"dx": event.x_root - x, "dy": event.y_root - y, "start": (event.x_root, event.y_root),
                     "attached": True, "pull": 0, "moved": False}

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
            self.loop("codex", CODEX_MS, self.auto_codex)

    def schedule_claude(self, delay):
        self.cancel(self.claude_timer)
        self.claude_timer = None
        if "claude" in self.providers:
            self.claude_timer = self.later(delay * 1000, self.refresh_claude)

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
            self.loop("codex", CODEX_MS, self.auto_codex)
        if not self.provider_override:
            try:
                save_settings(self.settings, self.data_dir)
            except UsageError as exc:
                self.errors[provider] = str(exc)
        self.open = False
        self.dock["along"] = self.corner_lock(
            self.dock["side"], self.dock["along"], self.compact_size(), self.area())
        self.place()

    def tick(self):
        # Expanded text shows ages in seconds; the compact view only needs occasional repaint.
        if self.open:
            self.redraw()
        self.ticks = getattr(self, "ticks", 0) + 1
        if self.ticks % 5 == 0:
            self.root.attributes("-topmost", True)
            if not self.open:
                self.redraw()
        self.later(CLOCK_MS, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for handle in list(self.after_ids):
            self.root.after_cancel(handle)
        self.after_ids.clear()
        self.client.close()
        self.claude_probe.close()
        for worker in self.workers:
            worker.join(timeout=3)
        self.root.destroy()


def run(providers=None):
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, providers=providers)
    root.mainloop()
