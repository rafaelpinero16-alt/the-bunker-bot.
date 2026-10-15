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
import os
import re
import time
import unicodedata
from collections import Counter, deque
from typing import Any, Deque, Dict, Optional, Tuple

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

    def _prune(self, chat_id: int, now: float) -> Deque[Tuple[float, int, bool, str]]:
        events = self._events.setdefault(chat_id, deque())
        while events and now - events[0][0] > self.window:
            events.popleft()
        return events

    def record(self, chat_id: int, user_id: int, is_new: bool, full_name: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        now = time.monotonic() if now is None else now
        events = self._prune(int(chat_id), now)
        if not any(e[1] == int(user_id) for e in events):    # reingresos del mismo usuario no inflan el pico
            events.append((now, int(user_id), bool(is_new), name_fingerprint(full_name)))
        joins = len(events)
        new_joins = sum(1 for e in events if e[2])
        # Solo las cuentas NUEVAS cuentan para "nombres clonados": cinco "María…" legítimas entrando a la vez
        # en una comunidad grande no son un ataque; cinco cuentas recién creadas con el mismo nombre, sí.
        names = Counter(e[3] for e in events if e[3] and e[2])
        similar = max(names.values()) if names else 0

        reasons = []
        if joins >= self.join_threshold:
            reasons.append("join_spike")
        if new_joins >= self.new_threshold:
            reasons.append("new_accounts")
        if similar >= self.name_threshold:
            reasons.append("similar_names")
        level = LEVEL_NONE
        if reasons:
            critical = (joins >= self.join_threshold * self.critical_multiplier
                        or new_joins >= self.new_threshold * self.critical_multiplier)
            level = LEVEL_CRITICAL if critical else LEVEL_ELEVATED
        if len(self._events) > 5000:          # higiene de memoria: comunidades sin actividad reciente
            for cid in [c for c, ev in self._events.items() if not ev or now - ev[-1][0] > self.window]:
                self._events.pop(cid, None)
        return {"level": level, "reasons": reasons, "joins": joins, "new_joins": new_joins, "similar_names": similar}

    def window_members(self, chat_id: int, now: Optional[float] = None) -> list:
        """[(user_id, is_new)] de los ingresos aún dentro de la ventana (para la cuarentena retroactiva)."""
        now = time.monotonic() if now is None else now
        return [(e[1], e[2]) for e in self._prune(int(chat_id), now)]

    def reset(self, chat_id: Optional[int] = None) -> None:
        if chat_id is None:
            self._events.clear()
        else:
            self._events.pop(int(chat_id), None)


detector = JoinSpikeDetector()

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


async def register_join(chat_id: int, user_id: int, full_name: str = "", is_bot: bool = False) -> Dict[str, Any]:
    """
    Registra un ingreso (directo o solicitud). Si la ventana supera algún umbral activa o prolonga el
    escudo en SQLite y en la caché. `triggered` es True solo en la activación nueva (para alertar UNA vez).
    """
    new = is_bot or is_new_account(user_id)
    verdict = detector.record(int(chat_id), int(user_id), new, full_name)
    verdict.update({"triggered": False, "isolated": is_isolated(chat_id), "is_new": new})
    if verdict["level"] == LEVEL_NONE:
        return verdict
    duration = RAID_SHIELD_SECONDS * (2 if verdict["level"] == LEVEL_CRITICAL else 1)
    reason = "+".join(verdict["reasons"])
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
        logger.warning("🚨 [RaidShield] Escudo activado en %s (nivel %s, %s): %s ingresos / %s nuevos en %ss.",
                       chat_id, level, reason, verdict["joins"], verdict["new_joins"], RAID_WINDOW_SECONDS)
    return verdict


async def lift_shield(chat_id: int) -> None:
    _SHIELD_UNTIL.pop(int(chat_id), None)
    _SHIELD_LEVEL.pop(int(chat_id), None)
    _GATE_MESSAGES.pop(int(chat_id), None)
    detector.reset(int(chat_id))
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