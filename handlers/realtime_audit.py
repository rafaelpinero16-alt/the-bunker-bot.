"""
realtime_audit.py — The Bunker OS · v41 · Auditoría en TIEMPO REAL (complemento del Log Channel Hub)

El informe periódico de rendimiento (Log Report Worker, 24 h / 72 h / 7 d) deja de ser la única vía de reporte: todo lo
que antes solo llegaba en ese resumen se publica ahora al instante en el canal de registro de la comunidad.

1. 👮 Pulsos de actividad de administradores (vector `admin_activity`)
   Cada mensaje de un administrador o del dueño se cuenta en memoria (O(1), sin base de datos ni red). Al cerrarse su
   ventana (ADMIN_PULSE_WINDOW_S, 15 min por defecto) se publica UNA tarjeta por administrador: mensajes enviados,
   respuestas a miembros y miembros atendidos. Tiempo real sin inundar el canal con una tarjeta por mensaje.

2. 🏅 Destacados e inactivos (vector `admin_activity`)
   Cada ADMIN_HIGHLIGHTS_EVERY_H horas (6 por defecto): administrador más activo, podio y administradores sin
   actividad en ese periodo, para todas las comunidades con canal de registro.

3. ⌛ Expiración exacta de cada pase MicVIP (vector `monetization`)
   Vigía de 60 s sobre micvip_audit + vip_mic_passes: al vencer el pase REAL (las renovaciones no generan falsas
   expiraciones) se publica la tarjeta con compra, importe, momento en que el Centinela aplicó el 100 % y expiración.
   Junto con #MICVIP (compra) y #MICVIP_GRANTED (aplicación automática del volumen) forma la prueba legal completa.

Todo corre en logger_hub._AUDIT_BG_TASKS; los fallos de red de Telegram o de base de datos se registran en el log del
servidor y nunca interrumpen al bot.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from handlers.logger_hub import dispatch_audit_log, spawn_audit_task

logger = logging.getLogger("bunker.realtime_audit")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(os.getenv(name, default)).strip())))
    except ValueError:
        return default


ADMIN_PULSE_WINDOW_S = _env_int("ADMIN_PULSE_WINDOW_S", 900, 60, 6 * 3600)
ADMIN_PULSE_MAX_KEYS = 20000
ADMIN_HIGHLIGHTS_EVERY_H = _env_int("ADMIN_HIGHLIGHTS_EVERY_H", 6, 1, 72)
MICVIP_WATCH_INTERVAL_S = _env_int("MICVIP_WATCH_INTERVAL_S", 60, 15, 3600)
PULSE_TICK_S = 30
PULSE_TRACKED_TIERS = frozenset({"owner", "admin"})

BotGetter = Callable[[], Any]


# ==========================================
# 👮 PULSOS DE ACTIVIDAD DE ADMINISTRADORES
# ==========================================
@dataclass
class _Pulse:
    bot: Any
    chat_title: Optional[str]
    admin_id: int
    admin_name: str
    admin_username: Optional[str]
    first_ts: float
    last_ts: float
    messages: int = 0
    replies: int = 0
    members: Set[int] = field(default_factory=set)


_PULSES: Dict[Tuple[int, int], _Pulse] = {}


def track_admin_message(bot: Any, message: Any, tier: str) -> None:
    """
    Llamado por la matriz perimetral para cada mensaje de un administrador / dueño. Síncrono y O(1): nunca toca la red
    ni la base de datos; jamás lanza.
    """
    try:
        if tier not in PULSE_TRACKED_TIERS:
            return
        user = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
        if user is None or chat is None or getattr(user, "is_bot", False):
            return
        now = time.time()
        key = (int(chat.id), int(user.id))
        pulse = _PULSES.get(key)
        if pulse is None:
            if len(_PULSES) >= ADMIN_PULSE_MAX_KEYS:
                return
            pulse = _PULSES[key] = _Pulse(bot=bot, chat_title=getattr(chat, "title", None), admin_id=int(user.id),
                                          admin_name=str(getattr(user, "full_name", "") or user.id),
                                          admin_username=getattr(user, "username", None), first_ts=now, last_ts=now)
        pulse.messages += 1
        pulse.last_ts = now
        reply = getattr(message, "reply_to_message", None)
        target = getattr(reply, "from_user", None) if reply is not None else None
        if target is not None and not getattr(target, "is_bot", False) and int(target.id) != int(user.id):
            pulse.replies += 1
            if len(pulse.members) < 200:
                pulse.members.add(int(target.id))
    except Exception as ex:   # la auditoría nunca afecta al flujo del mensaje
        logger.debug(f"[RealtimeAudit] Pulso de admin no contado: {ex}")


def _due_pulses(now: float, force: bool = False) -> List[Tuple[int, _Pulse]]:
    due = [key for key, p in _PULSES.items() if force or now - p.first_ts >= ADMIN_PULSE_WINDOW_S]
    return [(key[0], _PULSES.pop(key)) for key in due]


async def _publish_pulse(chat_id: int, pulse: _Pulse) -> None:
    start = time.strftime("%H:%M", time.gmtime(pulse.first_ts))
    end = time.strftime("%H:%M", time.gmtime(pulse.last_ts))
    await dispatch_audit_log(
        pulse.bot, chat_id, "admin_activity",
        actor_id=pulse.admin_id, actor_name=pulse.admin_name, actor_username=pulse.admin_username,
        chat_title=pulse.chat_title, automatic=True, system="admin_monitor",
        count=pulse.messages, window_seconds=int(max(60, pulse.last_ts - pulse.first_ts) or ADMIN_PULSE_WINDOW_S),
        extra={("Messages sent", "Mensajes enviados"): pulse.messages,
               ("Replies to members", "Respuestas a miembros"): pulse.replies,
               ("Members helped", "Miembros atendidos"): len(pulse.members),
               ("Period (UTC)", "Periodo (UTC)"): f"{start}–{end}"},
    )


async def flush_pulses(force: bool = False) -> int:
    """Publica los pulsos cuya ventana terminó (todos si force=True, p. ej. al apagar). Devuelve cuántos publicó."""
    sent = 0
    for chat_id, pulse in _due_pulses(time.time(), force=force):
        with contextlib.suppress(Exception):
            await _publish_pulse(chat_id, pulse)
            sent += 1
    return sent


async def _pulse_loop() -> None:
    while True:
        await asyncio.sleep(PULSE_TICK_S)
        try:
            await flush_pulses()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.debug(f"[RealtimeAudit] Vaciado de pulsos fallido: {ex}")


# ==========================================
# 🏅 DESTACADOS E INACTIVOS
# ==========================================
def _fmt_admin(entry: Dict[str, Any]) -> str:
    name = str(entry.get("name") or f"Admin {entry.get('user_id')}")
    handle = f" @{entry['username']}" if entry.get("username") else ""
    return f"{name}{handle}"


async def publish_admin_highlights(chat_id: int, since_ts: int, bot: Any = None) -> bool:
    """Tarjeta de destacados / inactivos del periodo [since_ts, ahora] para UNA comunidad."""
    from database.database import get_admin_performance
    perf = await get_admin_performance(int(chat_id), int(since_ts))
    admins: List[Dict[str, Any]] = list(perf.get("admins") or [])
    inactive: List[Dict[str, Any]] = list(perf.get("inactive") or [])
    if not admins and not inactive:
        return False
    hours = max(1, round((time.time() - since_ts) / 3600))
    podium = [f"{i}. {_fmt_admin(a)} — {a.get('messages', 0)} msg · {a.get('replies', 0)} resp. · "
              f"{a.get('actions_total', 0)} mod" for i, a in enumerate(admins[:3], start=1)]
    top = admins[0] if admins else None
    return await dispatch_audit_log(
        bot, int(chat_id), "admin_highlights", automatic=True, system="admin_monitor",
        actor_id=top.get("user_id") if top else None, actor_name=_fmt_admin(top) if top else None,
        detail=f"last {hours} h", evidence=podium or None,
        extra={("Active admins", "Admins activos"): len(admins),
               ("Inactive admins", "Admins sin actividad"): len(inactive),
               ("No activity", "Sin actividad"): ", ".join(_fmt_admin(a) for a in inactive[:8]) or "—",
               ("Security blocks", "Bloqueos de seguridad"): (perf.get("security") or {}).get("total")},
    )


async def _highlights_loop() -> None:
    from database.database import groups_with_log_channel
    period = ADMIN_HIGHLIGHTS_EVERY_H * 3600
    while True:
        await asyncio.sleep(period)
        since = int(time.time()) - period
        try:
            groups = await groups_with_log_channel()
        except Exception as ex:
            logger.debug(f"[RealtimeAudit] Comunidades con canal de registro no disponibles: {ex}")
            continue
        for chat_id in groups:
            try:
                await publish_admin_highlights(chat_id, since)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.debug(f"[RealtimeAudit] Destacados de {chat_id} omitidos: {ex}")
            await asyncio.sleep(0.2)


# ==========================================
# ⌛ VIGÍA DE EXPIRACIONES MICVIP
# ==========================================
async def micvip_expiry_tick(bot: Any = None) -> int:
    """Una pasada del vigía: publica las expiraciones reales pendientes y las marca. Devuelve cuántas procesó."""
    from database.database import micvip_due_expiries, micvip_mark_expiry_logged
    due = await micvip_due_expiries(100)
    done: List[int] = []
    for row in due:
        try:
            await dispatch_audit_log(
                bot, row["group_id"], "micvip_expired",
                user_id=row["user_id"], user_name=row.get("full_name") or None, username=row.get("username") or None,
                automatic=True, system="micvip", action="none", amount=row.get("amount_stars") or None,
                purchased_at=row.get("purchased_at"), applied_at=row.get("sentinel_applied_at"),
                expires_at=row.get("expires_at"),
                detail=f"charge {str(row.get('charge_id') or '')[:24]}",
                extra={("Volume delivered", "Volumen entregado"):
                       "✅ 100 % (Sentinel)" if row.get("sentinel_applied_at") else "⚠️ not confirmed / sin confirmar"},
                dedupe_key=f"micvip_expired:{row['id']}", dedupe_ttl=3600,
            )
        except Exception as ex:
            logger.debug(f"[RealtimeAudit] Expiración MicVIP {row.get('id')} no publicada: {ex}")
        done.append(int(row["id"]))   # sin canal de registro también se marca: no se reintenta en bucle
    if done:
        await micvip_mark_expiry_logged(done)
    return len(done)


async def _micvip_loop() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await micvip_expiry_tick()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.debug(f"[RealtimeAudit] Vigía MicVIP: {ex}")
        await asyncio.sleep(MICVIP_WATCH_INTERVAL_S)


# ==========================================
# ▶️ CICLO DE VIDA
# ==========================================
_TASKS: List["asyncio.Task[Any]"] = []


def start() -> None:
    """Arranca los tres bucles (idempotente). main.py lo llama tras registrar el bot maestro en el Hub."""
    if any(not t.done() for t in _TASKS):
        return
    _TASKS.clear()
    _TASKS.extend([
        spawn_audit_task(_pulse_loop(), name="audit_admin_pulses"),
        spawn_audit_task(_highlights_loop(), name="audit_admin_highlights"),
        spawn_audit_task(_micvip_loop(), name="audit_micvip_watch"),
    ])
    logger.info(f"🧾 [RealtimeAudit] Activo: pulsos de admins cada {ADMIN_PULSE_WINDOW_S // 60} min, destacados cada "
                f"{ADMIN_HIGHLIGHTS_EVERY_H} h, vigía MicVIP cada {MICVIP_WATCH_INTERVAL_S} s.")


async def stop() -> None:
    """Al apagar: publica los pulsos pendientes y detiene los bucles."""
    with contextlib.suppress(Exception):
        await asyncio.wait_for(flush_pulses(force=True), timeout=5)
    for task in _TASKS:
        task.cancel()
    _TASKS.clear()
