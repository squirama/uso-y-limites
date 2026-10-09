"""Minimal headless Claude Code call that reads plan limits; authentication stays in Claude Code."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from .models import UsageError, parse_claude


TIMEOUT = 90
MAX_LINE = 1024 * 1024
# Smallest useful request: Haiku, no tools, no MCP, no settings, a one-word system prompt.
ARGS = ["-p", "ok", "--model", "haiku", "--output-format", "stream-json", "--verbose",
        "--max-turns", "1", "--tools", "", "--system-prompt", "Responde solo: ok",
        "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
        "--setting-sources", ""]


def find_claude():
    found = shutil.which("claude.exe" if os.name == "nt" else "claude") or shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if local.exists():
        return str(local)
    raise UsageError("No se encuentra Claude Code. Instálalo e inicia sesión con tu cuenta.")


def limits_from_event(info):
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    if not isinstance(windows, dict):
        raise UsageError("Claude Code no ha entregado límites del plan.")
    limits = {}
    for key in ("five_hour", "seven_day"):
        raw = windows.get(key)
        if isinstance(raw, dict) and isinstance(raw.get("utilization"), (int, float)):
            limits[key] = {"used_percentage": round(min(100.0, max(0.0, raw["utilization"] * 100)), 1),
                           "resets_at": raw.get("resetsAt")}
    return {"rate_limits": limits}


class ClaudeProbe:
    def __init__(self, command=None, timeout=TIMEOUT):
        self.command = command
        self.timeout = timeout
        self.process = None
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def close(self):
        self.closed.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.kill()

    def fetch(self):
        if self.closed.is_set():
            raise UsageError("La consulta se ha cancelado.")
        command = self.command or [find_claude(), *ARGS]
        # A neutral folder keeps project CLAUDE.md files and hooks out of the request.
        with tempfile.TemporaryDirectory(prefix="uso-limites-") as folder:
            try:
                with self.lock:
                    process = subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.process = process
            except OSError:
                raise UsageError("No se ha podido iniciar Claude Code.") from None
            timer = threading.Timer(self.timeout, process.kill)
            timer.start()
            payload = None
            failed_auth = False
            try:
                for line in iter(lambda: process.stdout.readline(MAX_LINE), b""):
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "rate_limit_event":
                        payload = limits_from_event(message.get("rate_limit_info"))
                    elif message.get("type") == "result" and message.get("is_error"):
                        failed_auth = "login" in str(message.get("result", "")).lower()
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()
                with self.lock:
                    if self.process is process:
                        self.process = None
        if self.closed.is_set():
            raise UsageError("La consulta se ha cancelado.")
        if payload is None:
            if failed_auth:
                raise UsageError("Claude Code necesita una sesión válida. Ejecuta claude e inicia sesión.")
            raise UsageError("Claude Code no ha devuelto límites. Se reintentará más tarde.")
        snapshot = parse_claude(payload)
        return snapshot.__class__(snapshot.windows, snapshot.observed_at, "Claude Code · consulta automática")
