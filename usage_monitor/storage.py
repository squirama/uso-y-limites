"""Only application preferences and validated usage snapshots live here."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import time

from .models import UsageError, UsageSnapshot, WindowUsage, number, timestamp


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".runtime"
MAX_FILE_BYTES = 32768
ALERT_RETENTION_SECONDS = 7 * 86400


def read_json(path):
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise UsageError("El archivo local supera el tamaño permitido.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise UsageError("No se pudo leer el archivo de datos local.") from None
    if not isinstance(value, dict):
        raise UsageError("El archivo local tiene un formato no válido.")
    return value


def write_json(path, payload):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".usage-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
        temporary.replace(path)
    except (OSError, ValueError):
        raise UsageError("No se pudieron guardar los datos locales.") from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def load_settings(data_dir=DATA_DIR):
    raw = read_json(data_dir / "settings.json") or {}
    allowed = ("claude", "codex")
    selected = raw.get("providers", list(allowed))
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(item, str) or item not in allowed for item in selected)
            or len(selected) != len(set(selected))):
        selected = list(allowed)
    selected = [item for item in allowed if item in selected]
    dock = raw.get("dock")
    valid = (isinstance(dock, list) and len(dock) == 3 and dock[0] in {"left", "right", "top", "bottom"} and
             all(isinstance(v, int) and not isinstance(v, bool) and abs(v) < 100000 for v in dock[1:]))
    alerts = raw.get("alerts", True)
    glass = raw.get("glass", True)
    render_mode = raw.get("render_mode", "auto")
    if not isinstance(render_mode, str) or render_mode not in {"auto", "colorkey"}:
        render_mode = "auto"
    return {"providers": selected, "dock": dock if valid else None,
            "alerts": alerts if isinstance(alerts, bool) else True,
            "glass": glass if isinstance(glass, bool) else True,
            "render_mode": render_mode}


def save_settings(settings, data_dir=DATA_DIR):
    providers = [item for item in ("claude", "codex") if item in settings.get("providers", ())]
    if not providers:
        raise UsageError("Debe quedar al menos un servicio seleccionado.")
    alerts = settings.get("alerts", True)
    if not isinstance(alerts, bool):
        raise UsageError("La preferencia de avisos no es válida.")
    glass = settings.get("glass", True)
    if not isinstance(glass, bool):
        raise UsageError("La preferencia de cristal no es válida.")
    render_mode = settings.get("render_mode", "auto")
    if not isinstance(render_mode, str) or render_mode not in {"auto", "colorkey"}:
        raise UsageError("El modo de renderizado no es válido.")
    write_json(data_dir / "settings.json", {
        "providers": providers, "dock": settings.get("dock"), "alerts": alerts, "glass": glass,
        "render_mode": render_mode,
    })


def load_alerts(data_dir=DATA_DIR, now=None):
    raw = read_json(data_dir / "alerts.json") or {}
    entries = raw.get("entries", [])
    if not isinstance(entries, list) or len(entries) > 1000:
        raise UsageError("El registro local de avisos no es válido.")
    now = time.time() if now is None else now
    alerts = {}
    try:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("provider") not in {"claude", "codex"}:
                raise ValueError
            resets_at = number(entry.get("resets_at"), "fecha de reinicio")
            notified_at = number(entry.get("notified_at"), "fecha del aviso")
            if notified_at > now + 300:
                raise ValueError
            if now - notified_at <= ALERT_RETENTION_SECONDS:
                alerts[(entry["provider"], resets_at)] = notified_at
    except (KeyError, TypeError, ValueError, UsageError):
        raise UsageError("El registro local de avisos no es válido.") from None
    return alerts


def save_alerts(alerts, data_dir=DATA_DIR, now=None):
    now = time.time() if now is None else now
    entries = [
        {"provider": provider, "resets_at": resets_at, "notified_at": notified_at}
        for (provider, resets_at), notified_at in alerts.items()
        if provider in {"claude", "codex"}
        and now - notified_at <= ALERT_RETENTION_SECONDS
    ]
    entries.sort(key=lambda item: item["notified_at"])
    write_json(data_dir / "alerts.json", {"entries": entries[-100:]})


def save_snapshot(provider, snapshot, data_dir=DATA_DIR):
    if provider not in {"codex", "claude"}:
        raise UsageError("Proveedor no válido.")
    write_json(data_dir / f"{provider}.json", asdict(snapshot))


def load_snapshot(provider, data_dir=DATA_DIR):
    if provider not in {"codex", "claude"}:
        raise UsageError("Proveedor no válido.")
    raw = read_json(data_dir / f"{provider}.json")
    if raw is None:
        return None
    try:
        windows = raw["windows"]
        if not isinstance(windows, list) or len(windows) > 24:
            raise ValueError
        parsed = []
        for window in windows:
            label = window["label"]
            description = window.get("reset_description", "")
            if not isinstance(label, str) or len(label) > 128:
                raise ValueError
            if not isinstance(description, str) or len(description) > 160:
                raise ValueError
            parsed.append(WindowUsage(label, number(window["used_percent"], "porcentaje", maximum=100),
                                      timestamp(window.get("resets_at")), description))
        source = raw["source"]
        detail = raw.get("detail", "")
        if not isinstance(source, str) or len(source) > 80 or not isinstance(detail, str) or len(detail) > 1024:
            raise ValueError
        observed = timestamp(raw["observed_at"], "fecha de lectura")
        if observed is None:
            raise ValueError
        return UsageSnapshot(tuple(parsed), observed, source, detail)
    except (KeyError, TypeError, ValueError):
        raise UsageError("Los datos locales de consumo tienen un formato no válido.") from None
