"""
raid_shield.py — The Bunker OS · Escudo Anti-Raid / Anti-Reportes Masivos (v9)

Por qué existe
--------------
Las campañas de reportes masivos contra un bot siguen siempre el mismo patrón: decenas de cuentas nuevas
entran a la vez, provocan que el bot publique muchos mensajes (desafíos, avisos de sanción, bienvenidas)
y después reportan esos mensajes como spam. Telegram limita o banea al bot por el volumen de reportes,
aunque el bot solo estuviera defendiendo la comunidad.

El escudo corta las tres patas del ataque:

1. Detección de picos coordinados (JoinSpikeDetector): ventana deslizante por comunidad con tres señales
   independientes — volumen total de ingresos, ingresos de cuentas nuevas y ráfagas de cuentas nuevas con
   nombres casi idénticos (granjas de cuentas). Cualquiera activa el escudo.
2. Aislamiento preventivo de la instancia (bot maestro y clones comparten esta caché en el mismo proceso):
   mientras el escudo está activo el bot deja de publicar en esa comunidad todo lo que no es esencial
   (avisos de aduana y sanción, difusiones de planes, anuncios de gamificación y de videochat). Los
   desafíos de ingreso directo se sustituyen por UN único mensaje-puerta con enlace al bot: el
   verificado lo inicia el propio usuario (sin DMs no solicitados) y la superficie reportable pasa de
   N mensajes a 1.
3. Validación estricta de cuentas nuevas con el Captcha Pro: cuentas recientes y bots reciben siempre el
   reto por código (no el botón simple), con tiempo acotado y expulsión — no silencio — al fallar. Con el
   escudo activo la aduana se aplica aunque el administrador la tenga desactivada.

Umbrales configurables (v37)
----------------------------
Cada comunidad fija en raid_config (database.py) su umbral mínimo y máximo, la ventana, el umbral de solicitudes
de unión y la duración del escudo. handlers/groups.py los pasa en cada register_join(..., config=cfg); sin config
se usan los valores de entorno de v9 (comportamiento idéntico). Las solicitudes de unión usan su propio detector.

Estado
------
El escudo se persiste en SQLite (raid_shield_state) para sobrevivir a reinicios y para que la Mini App y el
informe del canal de registro lo muestren. Esta caché en memoria evita consultar la base en cada mensaje:
`is_isolated(chat_id)` es una búsqueda O(1) sin E/S.

Heurística de "cuenta nueva"
----------------------------
La Bot API no expone la fecha de creación de una cuenta. Los ID de usuario de Telegram son crecientes, de
modo que un ID por encima de RAID_NEW_ACCOUNT_ID_FLOOR corresponde a una cuenta reciente. El umbral es
configurable por entorno y se usa solo para endurecer la verificación, nunca para expulsar sin reto.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import unicodedata
from collections import Counter, deque
from typing import Any, Deque, Dict, Mapping, Optional, Tuple

logger = logging.getLogger("bunker.raid_shield")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


RAID_WINDOW_SECONDS = _env_int("RAID_WINDOW_SECONDS", 60, 10, 900)
RAID_JOIN_THRESHOLD = _env_int("RAID_JOIN_THRESHOLD", 12, 3, 1000)          # ingresos totales en la ventana
RAID_NEW_ACCOUNT_THRESHOLD = _env_int("RAID_NEW_ACCOUNT_THRESHOLD", 6, 2, 1000)  # ingresos de cuentas nuevas
RAID_SIMILAR_NAME_THRESHOLD = _env_int("RAID_SIMILAR_NAME_THRESHOLD", 5, 3, 1000)  # nombres casi idénticos
RAID_SHIELD_SECONDS = _env_int("RAID_SHIELD_SECONDS", 900, 120, 86400)       # 15 min (se prolonga si sigue)
RAID_CRITICAL_MULTIPLIER = _env_int("RAID_CRITICAL_MULTIPLIER", 3, 2, 20)     # nivel 2 = umbral × 3
RAID_NEW_ACCOUNT_ID_FLOOR = _env_int("RAID_NEW_ACCOUNT_ID_FLOOR", 7_500_000_000, 1, 10**13)
RAID_CAPTCHA_SECONDS = _env_int("RAID_CAPTCHA_SECONDS", 60, 15, 300)          # reto acotado para cuentas nuevas
RAID_GATE_SECONDS = _env_int("RAID_GATE_SECONDS", 300, 60, 1800)             # plazo para verificarse por la puerta
NOTICE_BUDGET_PER_MINUTE = _env_int("NOTICE_BUDGET_PER_MINUTE", 8, 1, 60)    # avisos no esenciales por comunidad

LEVEL_NONE, LEVEL_ELEVATED, LEVEL_CRITICAL = 0, 1, 2
_NAME_CLEAN_RE = re.compile(r"[^a-z]")


def is_new_account(user_id: Any) -> bool:
    """Cuenta reciente según el ID creciente de Telegram (ver docstring del módulo)."""
    try:
        return int(user_id) >= RAID_NEW_ACCOUNT_ID_FLOOR
    except (TypeError, ValueError):
        return False


def name_fingerprint(full_name: Any) -> str:
    """'Ánna 🔥 Smith23' → 'annas': prefijo alfabético normalizado (las granjas varían sufijos y emojis)."""
    text = unicodedata.normalize("NFKD", str(full_name or "")).encode("ascii", "ignore").decode().lower()
    letters = _NAME_CLEAN_RE.sub("", text)
    return letters[:5] if len(letters) >= 4 else ""


def resolve_limits(config: Optional[Mapping[str, Any]] = None, kind: str = "join") -> Dict[str, Any]:
    """
    Umbrales efectivos. config = fila de raid_config (get_raid_config). kind = 'join' | 'request'.
      • join      : umbral mínimo de ingresos (o de solicitudes si kind='request').
      • critical  : umbral máximo → nivel 2. Para solicitudes se escala con la misma proporción.
      • new / name: señales de cuentas nuevas y nombres clonados, proporcionales al umbral mínimo
                    (misma relación que los valores por defecto 6/12 y 5/12), para que el dueño gobierne
                    el escudo con un único número.
      • shield    : duración del escudo en segundos (exactamente la configurada; sin config, ×2 en crítico).
    """
    if not config:
        return {"window": RAID_WINDOW_SECONDS, "join": RAID_JOIN_THRESHOLD, "new": RAID_NEW_ACCOUNT_THRESHOLD,
                "name": RAID_SIMILAR_NAME_THRESHOLD, "critical": RAID_JOIN_THRESHOLD * RAID_CRITICAL_MULTIPLIER,
                "shield": RAID_SHIELD_SECONDS, "critical_doubles": True}
    base_join = max(3, int(config.get("join_threshold") or RAID_JOIN_THRESHOLD))
    base_critical = max(base_join + 1, int(config.get("critical_threshold") or base_join * RAID_CRITICAL_MULTIPLIER))
    join = max(3, int(config.get("request_threshold") or base_join)) if kind == "request" else base_join
    critical = base_critical if kind != "request" else max(join + 1, math.ceil(join * base_critical / base_join))
    return {
        "window": max(10, int(config.get("window_seconds") or RAID_WINDOW_SECONDS)),
        "join": join,
        "new": max(3, math.ceil(join * RAID_NEW_ACCOUNT_THRESHOLD / RAID_JOIN_THRESHOLD)),
        "name": max(3, math.ceil(join * RAID_SIMILAR_NAME_THRESHOLD / RAID_JOIN_THRESHOLD)),
        "critical": critical,
        "shield": max(60, int(config.get("shield_minutes") or RAID_SHIELD_SECONDS // 60) * 60),
        "critical_doubles": False,
    }


class JoinSpikeDetector:
    """Ventana deslizante por comunidad. Función pura del reloj inyectado: se prueba sin Telegram."""

    def __init__(self, window: int = RAID_WINDOW_SECONDS, join_threshold: int = RAID_JOIN_THRESHOLD,
                 new_threshold: int = RAID_NEW_ACCOUNT_THRESHOLD, name_threshold: int = RAID_SIMILAR_NAME_THRESHOLD,
                 critical_multiplier: int = RAID_CRITICAL_MULTIPLIER):
        self.window = window
        self.join_threshold = join_threshold
        self.new_threshold = new_threshold
        self.name_threshold = name_threshold
        self.critical_multiplier = critical_multiplier
        self._events: Dict[int, Deque[Tuple[float, int, bool, str]]] = {}
        self._windows: Dict[int, int] = {}      # v37 · ventana configurada por comunidad

    def _default_limits(self) -> Dict[str, Any]:
        return {"window": self.window, "join": self.join_threshold, "new": self.new_threshold,
                "name": self.name_threshold, "critical": self.join_threshold * self.critical_multiplier}

    def _prune(self, chat_id: int, now: float, window: Optional[int] = None) -> Deque[Tuple[float, int, bool, str]]:
        window = int(window or self._windows.get(chat_id, self.window))
        events = self._events.setdefault(chat_id, deque())
        while events and now - events[0][0] > window:
            events.popleft()
        return events

    def record(self, chat_id: int, user_id: int, is_new: bool, full_name: str = "", now: Optional[float] = None,
               limits: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        now = time.monotonic() if now is None else now
        lim = dict(limits) if limits else self._default_limits()
        cid = int(chat_id)
        self._windows[cid] = int(lim["window"])
        events = self._prune(cid, now, lim["window"])
        if not any(e[1] == int(user_id) for e in events):    # reingresos del mismo usuario no inflan el pico
            events.append((now, int(user_id), bool(is_new), name_fingerprint(full_name)))
        joins = len(events)
        new_joins = sum(1 for e in events if e[2])
        names = Counter(e[3] for e in events if e[3] and e[2])
        similar = max(names.values()) if names else 0

        reasons = []
        if joins >= lim["join"]:
            reasons.append("join_spike")
        if new_joins >= lim["new"]:
            reasons.append("new_accounts")
        if similar >= lim["name"]:
            reasons.append("similar_names")
        level = LEVEL_NONE
        if reasons:
            critical_new = math.ceil(lim["critical"] * lim["new"] / max(1, lim["join"]))
            critical = joins >= lim["critical"] or new_joins >= critical_new
            level = LEVEL_CRITICAL if critical else LEVEL_ELEVATED
        if len(self._events) > 5000:          # higiene de memoria: comunidades sin actividad reciente
            for c in [c for c, ev in self._events.items() if not ev or now - ev[-1][0] > self._windows.get(c, self.window)]:
                self._events.pop(c, None)
                self._windows.pop(c, None)
        return {"level": level, "reasons": reasons, "joins": joins, "new_joins": new_joins,
                "similar_names": similar, "window": int(lim["window"])}

    def window_members(self, chat_id: int, now: Optional[float] = None) -> list:
        """[(user_id, is_new)] de los ingresos aún dentro de la ventana (para la cuarentena retroactiva)."""
        now = time.monotonic() if now is None else now
        return [(e[1], e[2]) for e in self._prune(int(chat_id), now)]

    def reset(self, chat_id: Optional[int] = None) -> None:
        if chat_id is None:
            self._events.clear()
            self._windows.clear()
        else:
            self._events.pop(int(chat_id), None)
            self._windows.pop(int(chat_id), None)


detector = JoinSpikeDetector()
# Las solicitudes de unión (aprobación manual) tienen su propio umbral: ventana independiente.
request_detector = JoinSpikeDetector()

# ==========================================
# 🧊 CACHÉ DEL ESCUDO (aislamiento de la instancia)
# ==========================================
_SHIELD_UNTIL: Dict[int, float] = {}     # chat_id → epoch (s) de fin del escudo
_SHIELD_LEVEL: Dict[int, int] = {}
_GATE_MESSAGES: Dict[int, Tuple[int, float]] = {}   # chat_id → (message_id, expira)
_NOTICE_BUCKET: Dict[int, Deque[float]] = {}
_LOADED = False


def _db():
    import database.database as db  # importación perezosa: sin ciclos ni dependencia en pruebas puras
    return db


async def load_active_shields() -> int:
    """Rehidrata la caché desde SQLite (arranque). Devuelve cuántos escudos siguen vigentes."""
    global _LOADED
    try:
        active = await _db().get_active_raid_shields()
    except Exception as ex:
        logger.warning("⚠️ [RaidShield] No se pudo rehidratar el estado: %s", ex)
        return 0
    _SHIELD_UNTIL.update({int(k): float(v) for k, v in active.items()})
    _LOADED = True
    return len(active)


def is_isolated(chat_id: Any) -> bool:
    """¿La comunidad está bajo escudo? O(1), sin E/S: lo consultan todos los workers antes de publicar."""
    try:
        until = _SHIELD_UNTIL.get(int(chat_id))
    except (TypeError, ValueError):
        return False
    if until is None:
        return False
    if until <= time.time():
        _SHIELD_UNTIL.pop(int(chat_id), None)
        _SHIELD_LEVEL.pop(int(chat_id), None)
        _GATE_MESSAGES.pop(int(chat_id), None)
        return False
    return True


def shield_level(chat_id: Any) -> int:
    return _SHIELD_LEVEL.get(int(chat_id), LEVEL_ELEVATED) if is_isolated(chat_id) else LEVEL_NONE


def shield_remaining(chat_id: Any) -> int:
    until = _SHIELD_UNTIL.get(int(chat_id), 0)
    return max(0, int(until - time.time()))


async def register_join(chat_id: int, user_id: int, full_name: str = "", is_bot: bool = False, *,
                        kind: str = "join", config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Registra un ingreso directo (kind='join') o una solicitud de unión (kind='request').
    config = fila de raid_config: si el dueño desactivó el Anti-Raid no se cuenta ni se aísla nada.
    `triggered` es True solo en la activación nueva (para alertar UNA vez). Firma retrocompatible.
    """
    new = is_bot or is_new_account(user_id)
    if config is not None and not int(config.get("enabled", 1) or 0):
        return {"level": LEVEL_NONE, "reasons": [], "joins": 0, "new_joins": 0, "similar_names": 0,
                "triggered": False, "isolated": False, "is_new": new, "disabled": True}
    limits = resolve_limits(config, kind)
    det = request_detector if kind == "request" else detector
    verdict = det.record(int(chat_id), int(user_id), new, full_name, limits=limits)
    verdict.update({"triggered": False, "isolated": is_isolated(chat_id), "is_new": new, "kind": kind})
    if verdict["level"] == LEVEL_NONE:
        return verdict
    critical = verdict["level"] == LEVEL_CRITICAL
    duration = int(limits["shield"]) * (2 if critical and limits.get("critical_doubles") else 1)
    reason = "+".join(verdict["reasons"]) + ("" if kind == "join" else ":requests")
    try:
        state = await _db().activate_raid_shield(int(chat_id), duration, verdict["level"], reason, verdict["joins"])
        until = float(state.get("active_until") or (time.time() + duration))
        level = int(state.get("level") or verdict["level"])
        verdict["triggered"] = bool(state.get("new_activation"))
    except Exception as ex:                    # la defensa no depende de que la base responda
        logger.error("❌ [RaidShield] No se pudo persistir el escudo de %s: %s", chat_id, ex)
        until = max(_SHIELD_UNTIL.get(int(chat_id), 0.0), time.time() + duration)
        level = verdict["level"]
        verdict["triggered"] = not verdict["isolated"]
    _SHIELD_UNTIL[int(chat_id)] = until
    _SHIELD_LEVEL[int(chat_id)] = max(level, _SHIELD_LEVEL.get(int(chat_id), 0))
    verdict["isolated"] = True
    verdict["active_until"] = int(until)
    if verdict["triggered"]:
        logger.warning("🚨 [RaidShield] Escudo activado en %s (nivel %s, %s): %s %s / %s nuevos en %ss (umbral %s).",
                       chat_id, level, reason, verdict["joins"], "solicitudes" if kind == "request" else "ingresos",
                       verdict["new_joins"], limits["window"], limits["join"])
    return verdict


async def lift_shield(chat_id: int) -> None:
    """Levanta el escudo: caché, mensaje-puerta, ventanas de AMBOS detectores y fila en SQLite."""
    _SHIELD_UNTIL.pop(int(chat_id), None)
    _SHIELD_LEVEL.pop(int(chat_id), None)
    _GATE_MESSAGES.pop(int(chat_id), None)
    detector.reset(int(chat_id))
    request_detector.reset(int(chat_id))
    try:
        await _db().clear_raid_shield(int(chat_id))
    except Exception as ex:
        logger.warning("⚠️ [RaidShield] No se pudo limpiar el escudo de %s: %s", chat_id, ex)


def captcha_overrides(chat_id: int, user_id: int, is_bot: bool = False) -> Dict[str, Any]:
    """
    Endurecimiento del Captcha Pro. Devuelve {} si no aplica. Con escudo activo, o para cuentas nuevas y
    bots: reto por código (mode=1), tiempo acotado y expulsión al fallar (un silencio deja al atacante dentro).
    """
    isolated = is_isolated(chat_id)
    strict = isolated or is_bot or is_new_account(user_id)
    if not strict:
        return {}
    return {"mode": 1, "max_time": RAID_CAPTCHA_SECONDS, "action": "kick", "isolated": isolated,
            "reason": "raid_shield" if isolated else ("bot" if is_bot else "new_account")}


def notice_allowed(chat_id: int, essential: bool = False) -> bool:
    """
    Presupuesto de avisos públicos por comunidad (token bucket por minuto). Con el escudo activo solo pasan
    los esenciales: menos mensajes del bot = menos material para un reporte masivo.
    """
    if is_isolated(chat_id):
        return essential
    if essential:
        return True
    now = time.monotonic()
    bucket = _NOTICE_BUCKET.setdefault(int(chat_id), deque())
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= NOTICE_BUDGET_PER_MINUTE:
        return False
    bucket.append(now)
    if len(_NOTICE_BUCKET) > 5000:
        for cid in [c for c, b in _NOTICE_BUCKET.items() if not b or now - b[-1] > 60]:
            _NOTICE_BUCKET.pop(cid, None)
    return True


def gate_message(chat_id: int) -> Optional[int]:
    entry = _GATE_MESSAGES.get(int(chat_id))
    if not entry or entry[1] <= time.time() or not is_isolated(chat_id):
        return None
    return entry[0]


def remember_gate_message(chat_id: int, message_id: int) -> None:
    _GATE_MESSAGES[int(chat_id)] = (int(message_id), float(_SHIELD_UNTIL.get(int(chat_id), time.time() + RAID_GATE_SECONDS)))


_GATE_LOCKS: Dict[int, asyncio.Lock] = {}


def gate_lock(chat_id: int) -> asyncio.Lock:
    """Un solo mensaje-puerta por comunidad aunque lleguen 50 ingresos en el mismo segundo."""
    return _GATE_LOCKS.setdefault(int(chat_id), asyncio.Lock())


def status_payload(chat_id: int) -> Dict[str, Any]:
    active = is_isolated(chat_id)
    return {"active": active, "level": shield_level(chat_id), "remaining_seconds": shield_remaining(chat_id) if active else 0}