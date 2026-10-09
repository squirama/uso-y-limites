"""Claude Code status line hook: stores plan limits and prints a short summary."""

import json
import time
import sys

from .models import UsageError, parse_claude
from .storage import DATA_DIR, load_snapshot, save_snapshot, write_json


MAX_INPUT_BYTES = 262144


def summary(snapshot):
    names = {"5 horas": "5h", "7 días": "7d"}
    return " · ".join(f"{names.get(w.label, w.label)}: {w.used_percent:.0f}%" for w in snapshot.windows)


def record(payload, data_dir=DATA_DIR):
    snapshot = parse_claude(payload)
    try:
        previous = load_snapshot("claude", data_dir)
    except UsageError:
        previous = None
    # A newer reading (for example from the automatic query) must not be replaced by an older one.
    if previous is None or snapshot.observed_at >= previous.observed_at:
        save_snapshot("claude", snapshot, data_dir)
    return snapshot


def diagnose(data_dir, payload, error):
    # Records only field names and the error, never session content, to explain an empty card.
    info = {"called_at": time.time(), "error": str(error) if error else "",
            "keys": sorted(payload)[:40] if isinstance(payload, dict) else [],
            "rate_limits_available": payload.get("rate_limits_available") if isinstance(payload, dict) else None}
    try:
        write_json(data_dir / "statusline-diagnostico.json", info)
    except UsageError:
        pass


def main(stream=None, data_dir=DATA_DIR):
    stream = stream or sys.stdin.buffer
    payload = None
    try:
        raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("entrada demasiado grande")
        payload = json.loads(raw)
        snapshot = record(payload, data_dir)
    except (ValueError, UnicodeError, UsageError, OSError) as exc:
        diagnose(data_dir, payload, exc)
        # The status line must never break Claude Code; missing limits simply show nothing.
        return 0
    diagnose(data_dir, payload, None)
    # Piped stdout on Windows defaults to the ANSI code page; Claude Code reads UTF-8.
    sys.stdout.buffer.write(summary(snapshot).encode("utf-8"))
    sys.stdout.flush()
    return 0
