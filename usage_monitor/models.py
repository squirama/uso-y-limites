"""Validate provider data without persisting account or credential fields."""

from dataclasses import dataclass
from datetime import datetime
import math
import time


STALE_AFTER_SECONDS = 1860  # Claude may settle to one query every 30 minutes.
MAX_WINDOWS = 24


class UsageError(Exception):
    """An error safe to display without raw provider responses."""


def number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError(f"Dato no válido: {name}.")
    if not math.isfinite(value) or value < minimum:
        raise UsageError(f"Dato fuera de rango: {name}.")
    if maximum is not None and value > maximum:
        raise UsageError(f"Dato fuera de rango: {name}.")
    return float(value)


def timestamp(value, name="fecha de reinicio"):
    if value is None:
        return None
    return number(value, name, maximum=253402214400)


@dataclass(frozen=True)
class WindowUsage:
    label: str
    used_percent: float
    resets_at: float | None
    reset_description: str = ""


@dataclass(frozen=True)
class UsageSnapshot:
    windows: tuple[WindowUsage, ...]
    observed_at: float
    source: str
    detail: str = ""

    def stale(self, now=None):
        now = time.time() if now is None else now
        return now - self.observed_at >= STALE_AFTER_SECONDS or any(
            w.resets_at is not None and now >= w.resets_at for w in self.windows
        )


def window_label(minutes, fallback):
    if minutes is None:
        return fallback
    minutes = number(minutes, "duración", minimum=1, maximum=5256000)
    if minutes % 1440 == 0:
        return f"{minutes / 1440:g} días"
    if minutes % 60 == 0:
        return f"{minutes / 60:g} horas"
    return f"{minutes:g} minutos"


def parse_codex(result, now=None):
    if not isinstance(result, dict):
        raise UsageError("Codex ha devuelto una respuesta no válida.")
    multi = result.get("rateLimitsByLimitId")
    if isinstance(multi, dict) and multi:
        buckets = list(multi.items())
    elif isinstance(result.get("rateLimits"), dict):
        buckets = [("codex", result["rateLimits"])]
    else:
        raise UsageError("Esta cuenta no ha entregado métricas de límites.")
    windows = []
    details = []
    for bucket_id, bucket in buckets:
        if not isinstance(bucket, dict):
            raise UsageError("Codex ha devuelto un grupo de límites no válido.")
        plan = bucket.get("planType")
        if plan in {"enterprise", "business", "team", "pro", "plus", "free", "edu"}:
            details.append(f"Plan informado: {plan.capitalize()}")
        bucket_name = bucket.get("limitName") or bucket_id
        if not isinstance(bucket_name, str):
            raise UsageError("Codex ha devuelto un nombre de límite no válido.")
        bucket_name = " ".join(bucket_name.split())[:64]
        for key, default in (("primary", "Ventana principal"), ("secondary", "Ventana secundaria")):
            raw = bucket.get(key)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise UsageError("Codex ha devuelto una ventana no válida.")
            # A missing percentage is unavailable, never zero or unlimited.
            if raw.get("usedPercent") is None:
                continue
            label = window_label(raw.get("windowDurationMins"), default)
            if len(buckets) > 1:
                label = f"{bucket_name} · {label}"
            windows.append(WindowUsage(
                label,
                number(raw["usedPercent"], "porcentaje", maximum=100),
                timestamp(raw.get("resetsAt")),
            ))
        credits = bucket.get("credits")
        if isinstance(credits, dict):
            if credits.get("unlimited") is True:
                details.append("Créditos: sin límite informado por el servicio")
            elif credits.get("balance") is not None:
                try:
                    balance = float(credits["balance"])
                except (ValueError, TypeError, OverflowError):
                    raise UsageError("Saldo de créditos no válido.") from None
                balance = number(balance, "créditos")
                details.append(f"Saldo informado: {balance:g} créditos")
    if len(windows) > MAX_WINDOWS:
        raise UsageError("Codex ha entregado más ventanas de las admitidas.")
    if not windows and not details:
        raise UsageError("Esta cuenta no ha entregado porcentajes ni saldo de créditos.")
    if not windows:
        details.append("Sin porcentajes disponibles para esta cuenta")
    return UsageSnapshot(tuple(windows), time.time() if now is None else now,
                         "Codex app-server", " · ".join(dict.fromkeys(details)))


def parse_claude(payload, observed_at=None):
    if not isinstance(payload, dict):
        raise UsageError("Claude ha entregado un formato no válido.")
    limits = payload.get("rate_limits")
    if limits is None:
        raise UsageError("Claude todavía no ha entregado límites; completa una respuesta en la sesión conectada.")
    if not isinstance(limits, dict):
        raise UsageError("Claude ha entregado límites no válidos.")
    windows = []
    for key, label in (("five_hour", "5 horas"), ("seven_day", "7 días")):
        raw = limits.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise UsageError("Claude ha entregado una ventana no válida.")
        if raw.get("used_percentage") is None:
            continue
        windows.append(WindowUsage(label,
            number(raw["used_percentage"], "porcentaje", maximum=100),
            timestamp(raw.get("resets_at"))))
    if not windows:
        raise UsageError("Claude todavía no ha entregado porcentajes de suscripción.")
    return UsageSnapshot(tuple(windows), time.time() if observed_at is None else observed_at,
                         "Claude Code · barra de estado")


def reset_text(resets_at, now=None):
    if resets_at is None:
        return "Reinicio no informado"
    now = time.time() if now is None else now
    remaining = resets_at - now
    if remaining <= 0:
        return "Reinicio previsto superado · falta nueva lectura"
    total_minutes = max(1, math.ceil(remaining / 60))
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    countdown = f"{days} d {hours} h" if days else f"{hours} h {minutes} min" if hours else f"{minutes} min"
    try:
        date = datetime.fromtimestamp(resets_at).strftime("%d/%m %H:%M")
    except (ValueError, OSError, OverflowError):
        return "Fecha de reinicio no representable"
    return f"Reinicia en {countdown} · {date}"
