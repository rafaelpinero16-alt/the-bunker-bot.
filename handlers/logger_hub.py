"""
logger_hub.py — The Bunker OS · Log Channel Hub (Centro de Auditoría Perimetral)

Publica cada evento crítico de una comunidad (grupo o canal) en SU canal de registro: el mismo
`group_settings.log_channel_id` que el dueño configura y valida desde la consola del bot o la Mini App.

Uso (desde cualquier handler, SIEMPRE en segundo plano para no bloquear Aiogram):

    _spawn(dispatch_audit_log(bot, chat_id, "warn", user=target, actor=admin, reason="Spam", strikes=2, limit=3))

Garantías de diseño
-------------------
• Nunca lanza: cualquier error se registra en el log del servidor y el evento se descarta.
• Sin canal configurado (o categoría silenciada por el dueño) → se aborta en silencio, sin tocar Telegram.
• Cola por canal de registro con ritmo de ~1 mensaje / 3 s (límite de Telegram para canales): un raid de cientos
  de ingresos no provoca FloodWait. Si la cola se desborda, los eventos sobrantes se resumen en una tarjeta.
• `TelegramRetryAfter` se respeta; los errores de red se reintentan con espera creciente.
• Si el bot no puede escribir (expulsado, sin permisos, canal borrado) se prueba con el bot maestro y, si tampoco,
  se abre un disyuntor de 10 min para ese canal: no se martillea a Telegram ni se llenan los logs del servidor.
• Deduplicación: el mismo hecho visto por dos vías (p. ej. la actualización chat_member y el mensaje de servicio)
  se publica una sola vez (`dedupe_key` + `defer` para la vía de respaldo).
• Tarjetas en el idioma de servicio de la comunidad (inglés por defecto), con hashtags buscables (#BAN #id123…).

Campos admitidos en **kwargs (todos opcionales; los que no aplican no se muestran)
------------------------------------------------------------------------------
user / user_id / user_name / username      Usuario afectado (objeto aiogram User o datos sueltos).
actor / actor_id / actor_name              Quién ejecutó la acción (administrador, dueño, comprador…).
automatic=True, system="Anti-Spam"         Acción autónoma del sistema (sin actor humano).
reason, action, strikes, limit             Motivo, medida aplicada (mute/kick/ban/…), advertencias.
duration_minutes / duration_seconds        Duración de la medida.
amount, currency, plan_name, duration_days, expires_at   Monetización (Stars).
via, link_name, inviter                    Forma de ingreso (link / request / added / direct / folder / appeal / return).
level, signals, count, window_seconds      Escudo Anti-Raid.
detail, excerpt, extra={etiqueta: valor}   Texto libre, fragmento del mensaje eliminado y campos adicionales.
chat_title                                 Evita una llamada a get_chat si el llamador ya conoce el título.
dedupe_key, dedupe_ttl, defer              Control de duplicados (ver arriba).

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, Optional, Tuple, Union

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)

if TYPE_CHECKING:  # solo para el verificador de tipos: el Hub no necesita aiogram.Bot en tiempo de ejecución
    from aiogram import Bot

logger = logging.getLogger("bunker.audit_hub")


# ==========================================
# 🔌 DEPENDENCIAS DE DATOS (tolerantes a despliegues parciales)
# ==========================================
_get_audit_log_target: Optional[Callable[..., Any]]
_get_service_prefs: Optional[Callable[..., Any]]
try:
    from database.database import get_audit_log_target as _db_get_audit_log_target
    _get_audit_log_target = _db_get_audit_log_target
except ImportError:  # pragma: no cover - base de datos anterior a v39: se lee la columna directamente
    _get_audit_log_target = None
try:
    from database.database import get_service_prefs as _db_get_service_prefs
    _get_service_prefs = _db_get_service_prefs
except ImportError:  # pragma: no cover - base de datos anterior a v37
    _get_service_prefs = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


AUDIT_TARGET_TTL = max(10.0, _env_float("AUDIT_TARGET_TTL", 60.0))           # caché de canal + categorías
AUDIT_LANG_TTL = 60.0                                                        # caché del idioma de servicio
AUDIT_TITLE_TTL = 600.0                                                      # caché del título de la comunidad
AUDIT_MIN_INTERVAL = max(1.0, _env_float("AUDIT_MIN_INTERVAL", 3.1))         # segundos entre envíos por canal
AUDIT_QUEUE_MAX = max(20, int(_env_float("AUDIT_QUEUE_MAX", 300)))           # eventos en espera por canal
AUDIT_CIRCUIT_SECONDS = max(60.0, _env_float("AUDIT_CIRCUIT_SECONDS", 600))  # pausa tras perder permisos
AUDIT_WORKER_IDLE = 90.0                                                     # el worker termina tras este reposo
AUDIT_SEND_TIMEOUT = 15.0
AUDIT_DEDUPE_TTL = 20.0
AUDIT_MAX_DEFER = 30.0
AUDIT_EXCERPT_MAX = 350
_CACHE_MAX = 5000

SIGNATURE = "🛡️ <i>The Bunker OS · Audit Hub</i>"
DIVIDER = "━━━━━━━━━━━━━━━━"


# ==========================================
# 🗂️ CATÁLOGO DE EVENTOS
# ==========================================
# event_type → (categoría, emoji, hashtag, título EN, título ES)
EVENTS: Dict[str, Tuple[str, str, str, str, str]] = {
    # 👥 Membresía
    "member_joined": ("membership", "📥", "JOIN", "New member", "Nuevo miembro"),
    "member_left": ("membership", "📤", "LEAVE", "Member left", "Salida de miembro"),
    "join_request": ("membership", "📨", "REQUEST", "Join request received", "Solicitud de ingreso recibida"),
    "bot_joined": ("membership", "🤖", "BOT", "Bot added to the community", "Bot añadido a la comunidad"),
    "bunker_connected": ("owner", "🛡️", "SETUP", "The Bunker connected as administrator", "The Bunker conectado como administrador"),
    "log_channel_linked": ("owner", "🧾", "LOGCHANNEL", "Log channel linked", "Canal de registro vinculado"),
    # 🛂 Aduana (Captcha)
    "captcha_passed": ("captcha", "✅", "CAPTCHA_OK", "Captcha passed", "Captcha superado"),
    "captcha_failed": ("captcha", "❌", "CAPTCHA_FAIL", "Captcha failed (wrong code)", "Captcha fallido (código erróneo)"),
    "captcha_expired": ("captcha", "⏱️", "CAPTCHA_TIMEOUT", "Captcha expired (timeout)", "Captcha expirado (sin respuesta)"),
    # 🛡️ Seguridad autónoma
    "spam_deleted": ("security", "🧹", "ANTISPAM", "Message removed by Anti-Spam", "Mensaje eliminado por Anti-Spam"),
    "flood_sanction": ("security", "🌊", "ANTIFLOOD", "Anti-Flood sanction", "Sanción Anti-Flood"),
    "userbot_detected": ("security", "🕵️", "USERBOT", "Userbot neutralized", "Userbot neutralizado"),
    "cas_blocked": ("security", "🌐", "CAS", "Global spammer blocked (CAS)", "Spammer global bloqueado (CAS)"),
    "raid_shield_on": ("security", "🚨", "RAID", "Anti-Raid shield engaged", "Escudo Anti-Raid activado"),
    "raid_penalty": ("security", "🚧", "RAID_PENALTY", "Raid account penalized", "Cuenta de raid penalizada"),
    # 👮 Moderación
    "warn": ("moderation", "⚠️", "WARN", "Warning issued", "Advertencia registrada"),
    "sanction": ("moderation", "⚖️", "SANCTION", "Warning limit reached — sanction applied", "Límite de advertencias — sanción aplicada"),
    "warns_reset": ("moderation", "♻️", "UNWARN", "Warnings reset", "Advertencias restablecidas"),
    "vip_revoked": ("moderation", "🎙️", "DELVIP", "VIP pass revoked", "Pase VIP revocado"),
    "mute": ("moderation", "🔇", "MUTE", "Member muted", "Miembro silenciado"),
    "unmute": ("moderation", "🔊", "UNMUTE", "Member unmuted", "Silencio retirado"),
    "kick": ("moderation", "👢", "KICK", "Member kicked", "Miembro expulsado"),
    "ban": ("moderation", "⛔", "BAN", "Member banned", "Miembro baneado"),
    "unban": ("moderation", "✅", "UNBAN", "Member unbanned", "Baneo retirado"),
    "member_removed": ("moderation", "🚪", "KICK", "Member removed", "Miembro retirado"),
    "member_banned": ("moderation", "⛔", "BAN", "Member banned", "Miembro baneado"),
    "member_unbanned": ("moderation", "✅", "UNBAN", "Member unbanned", "Baneo retirado"),
    "member_restricted": ("moderation", "🔇", "MUTE", "Member restricted", "Miembro restringido"),
    "member_unrestricted": ("moderation", "🔊", "UNMUTE", "Restrictions lifted", "Restricciones retiradas"),
    # 👑 Comandos del Dueño
    "panic_on": ("owner", "🚨", "PANIC", "Raid Lockdown ENGAGED", "Raid Lockdown ACTIVADO"),
    "panic_off": ("owner", "🟢", "PANIC", "Raid Lockdown lifted", "Raid Lockdown desactivado"),
    "night_on": ("owner", "🌙", "NIGHT", "Night Mode enabled", "Modo Nocturno activado"),
    "night_off": ("owner", "☀️", "NIGHT", "Night Mode disabled", "Modo Nocturno desactivado"),
    "screenshield_on": ("owner", "🎥", "SCREENSHIELD", "Screen-Share Shield enabled", "Escudo Antinota activado"),
    "screenshield_off": ("owner", "🎥", "SCREENSHIELD", "Screen-Share Shield disabled", "Escudo Antinota desactivado"),
    "noiseshield_on": ("owner", "🔇", "NOISESHIELD", "Noise Shield enabled", "Escudo Antirruido activado"),
    "noiseshield_off": ("owner", "🔇", "NOISESHIELD", "Noise Shield disabled", "Escudo Antirruido desactivado"),
    "duckvolume": ("owner", "🎚️", "DUCKVOLUME", "Podcast ducking volume changed", "Volumen de atenuación cambiado"),
    "autolower_on": ("owner", "🎛️", "AUTOLOWER", "AutoLower enabled", "AutoLower activado"),
    "autolower_off": ("owner", "🎛️", "AUTOLOWER", "AutoLower disabled", "AutoLower desactivado"),
    "podcast_on": ("owner", "🎙️", "PODCAST", "Podcast Mode enabled", "Modo Podcast activado"),
    "podcast_off": ("owner", "🎙️", "PODCAST", "Podcast Mode disabled", "Modo Podcast desactivado"),
    "settings_exported": ("owner", "📦", "BACKUP", "Configuration backup exported", "Respaldo de configuración exportado"),
    "settings_imported": ("owner", "🔐", "RESTORE", "Configuration restored from backup", "Configuración restaurada desde respaldo"),
    "settings_import_failed": ("security", "🛑", "RESTORE_FAIL", "Backup restore rejected", "Restauración de respaldo rechazada"),
    "service_lang": ("owner", "🌐", "LANG", "Service language changed", "Idioma de servicio cambiado"),
    "speaker_price": ("owner", "⭐", "SPEAKERS", "Priority-turn price changed", "Precio del turno prioritario cambiado"),
    "ghost_purge": ("owner", "💀", "GHOSTPURGE", "Ghost Purge executed", "Ghost Purge ejecutado"),
    # ⭐ Monetización
    "speaker_purchase": ("monetization", "🎟️", "SPEAKERS", "Priority turn purchased", "Turno prioritario comprado"),
    "channel_subscription": ("monetization", "💎", "MEMBERSHIP", "Paid membership activated", "Membresía de pago activada"),
    "tip_received": ("monetization", "⭐", "TIP", "Tip received", "Propina recibida"),
    "micvip_purchase": ("monetization", "🎙️", "MICVIP", "MicVIP pass purchased", "Pase MicVIP comprado"),
    "license_purchase": ("monetization", "👑", "LICENSE", "License purchased", "Licencia adquirida"),
    "appeal_paid": ("monetization", "⚖️", "APPEAL", "Ban appeal paid", "Apelación de baneo pagada"),
    "membership_grace": ("monetization", "⏳", "MEMBERSHIP_GRACE", "Membership expired — grace period", "Membresía vencida — período de gracia"),
    "membership_expired": ("monetization", "⌛", "MEMBERSHIP_EXPIRED", "Membership expired", "Membresía expirada"),
    "membership_kicked": ("monetization", "🚪", "MEMBERSHIP_KICKED", "Removed for non-payment", "Expulsado por falta de pago"),
    "payment_refunded": ("monetization", "↩️", "REFUND", "Payment refunded", "Pago reembolsado"),
    # 📋 Planes de membresía de canal
    "plan_created": ("plans", "➕", "PLAN", "Membership plan created", "Plan de membresía creado"),
    "plan_toggled": ("plans", "⏯️", "PLAN", "Membership plan status changed", "Estado del plan cambiado"),
    "plan_deleted": ("plans", "🗑️", "PLAN", "Membership plan deleted", "Plan de membresía eliminado"),
    "plan_scheduled": ("plans", "⏰", "PLAN", "Plan broadcast schedule changed", "Programación de difusión del plan cambiada"),
    "plan_broadcast": ("plans", "📢", "PLAN", "Plan broadcast published", "Difusión del plan publicada"),
}
_GENERIC_EVENT = ("other", "📌", "EVENT", "Audit event", "Evento de auditoría")
# Categorías de bajo riesgo: se publican sin sonido para no saturar a los administradores.
SILENT_CATEGORIES = frozenset({"membership", "captcha", "plans"})

_LABELS: Dict[str, Tuple[str, str]] = {
    "community": ("Community", "Comunidad"),
    "user": ("User", "Usuario"),
    "actor": ("Executed by", "Ejecutado por"),
    "automatic": ("Automatic", "Automático"),
    "reason": ("Reason", "Motivo"),
    "action": ("Measure", "Medida"),
    "strikes": ("Warnings", "Advertencias"),
    "duration": ("Duration", "Duración"),
    "amount": ("Amount", "Importe"),
    "plan": ("Plan", "Plan"),
    "validity": ("Validity", "Vigencia"),
    "expires": ("Expired on", "Venció el"),
    "until": ("Until", "Hasta"),
    "via": ("Joined via", "Ingreso por"),
    "level": ("Level", "Nivel"),
    "signals": ("Signals", "Señales"),
    "count": ("Joins in window", "Ingresos en la ventana"),
    "detail": ("Detail", "Detalle"),
    "date": ("Date", "Fecha"),
    "message": ("Removed content", "Contenido eliminado"),
    "skipped": ("audit events were skipped because of the volume (the log channel queue was full).",
                "eventos de auditoría se omitieron por volumen (la cola del canal de registro estaba llena)."),
    "skipped_title": ("Audit volume protection", "Protección de volumen de auditoría"),
}
_ACTIONS: Dict[str, Tuple[str, str]] = {
    "mute": ("🔇 Mute", "🔇 Silencio"), "unmute": ("🔊 Unmute", "🔊 Fin del silencio"),
    "kick": ("👢 Kick", "👢 Expulsión"), "ban": ("⛔ Ban", "⛔ Baneo"), "unban": ("✅ Unban", "✅ Desbaneo"),
    "decline": ("🚫 Request declined", "🚫 Solicitud rechazada"), "approve": ("✅ Request approved", "✅ Solicitud aprobada"),
    "delete": ("🗑️ Message deleted", "🗑️ Mensaje eliminado"), "warn": ("⚠️ Warning", "⚠️ Advertencia"),
    "restrict": ("🔒 Read-only", "🔒 Solo lectura"), "captcha": ("🧩 Strict captcha", "🧩 Captcha estricto"),
    "none": ("— None", "— Ninguna"), "release": ("🔓 Released", "🔓 Liberado"),
}
_VIA: Dict[str, Tuple[str, str]] = {
    "link": ("🔗 Invite link", "🔗 Enlace de invitación"),
    "request": ("📨 Join request (approved)", "📨 Solicitud de ingreso (aprobada)"),
    "added": ("➕ Added by a member", "➕ Añadido por un miembro"),
    "direct": ("🚪 Direct (public link / username)", "🚪 Directo (enlace público / usuario)"),
    "folder": ("📁 Chat-folder link", "📁 Enlace de carpeta"),
    "appeal": ("⚖️ Paid ban appeal", "⚖️ Apelación de baneo pagada"),
    "return": ("🔙 Post-payment return link", "🔙 Enlace de retorno post-pago"),
    "unknown": ("❔ Unknown", "❔ Desconocido"),
}
_SYSTEMS: Dict[str, Tuple[str, str]] = {
    "antispam": ("Anti-Spam", "Anti-Spam"), "antiflood": ("Anti-Flood", "Anti-Flood"),
    "captcha": ("Security checkpoint", "Aduana de seguridad"), "raid_shield": ("Anti-Raid shield", "Escudo Anti-Raid"),
    "userbot": ("Userbot hunter", "Cazador de userbots"), "cas": ("CAS global blocklist", "Lista global CAS"),
    "warn_ladder": ("Warning ladder", "Escalera de advertencias"), "panic": ("Raid Lockdown", "Raid Lockdown"),
    "payments": ("Telegram Stars", "Telegram Stars"), "memberships": ("Membership worker", "Gestor de membresías"),
}


# ==========================================
# 🧠 ESTADO EN MEMORIA
# ==========================================
@dataclass
class _Item:
    bot: Any
    text: str
    silent: bool
    chat_id: int
    parse_mode: Optional[str] = "HTML"   # None = texto plano (último recurso ante un error de parseo)


@dataclass
class _Lane:
    target: Union[int, str]
    queue: Deque[_Item] = field(default_factory=deque)
    worker: Optional[asyncio.Task] = None
    last_sent: float = 0.0
    dropped: int = 0
    circuit_until: float = 0.0
    warned: bool = False
    last_bot: Any = None                 # remitente / comunidad del último evento (para el aviso de volumen)
    last_chat_id: Optional[int] = None


_LANES: Dict[str, _Lane] = {}
_TARGET_CACHE: Dict[int, Tuple[float, Optional[Union[int, str]], frozenset]] = {}
_LANG_CACHE: Dict[int, Tuple[float, str]] = {}
_TITLE_CACHE: Dict[int, Tuple[float, str]] = {}
_DEDUPE: Dict[str, float] = {}
_BG_TASKS: set = set()
_STATS: Dict[str, int] = {"queued": 0, "sent": 0, "dropped": 0, "failed": 0, "deduped": 0, "no_target": 0}
_MASTER_BOT: Any = None
_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _spawn(coro) -> asyncio.Task:
    """Tarea en segundo plano con referencia fuerte (evita que el GC la cancele)."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def register_master_bot(bot: Any) -> None:
    """main.py puede registrar el bot maestro: se usa como respaldo si el bot del evento no puede publicar."""
    global _MASTER_BOT
    _MASTER_BOT = bot


def _master_bot() -> Any:
    if _MASTER_BOT is not None:
        return _MASTER_BOT
    for module_name in ("__main__", "main"):
        module = sys.modules.get(module_name)
        candidate = getattr(module, "master_bot_instance", None) if module is not None else None
        if candidate is not None:
            return candidate
    return None


def _reset_lane_circuit(target: Any) -> None:
    lane = _LANES.get(str(target))
    if lane is not None:
        lane.circuit_until = 0.0
        lane.warned = False


def forget_audit_target(chat_id: int, new_target: Any = None) -> None:
    """
    Invalida la caché del canal de registro de la comunidad (llamar al vincularlo, cambiarlo, verificarlo o
    desactivarlo desde la consola o la Mini App): el siguiente evento lee el valor nuevo sin esperar el TTL de 60 s.
    También cierra el disyuntor del canal anterior y del nuevo: si el operador acaba de dar permisos al bot, la
    auditoría se reanuda al instante en lugar de esperar la pausa de 10 min. Nunca lanza.
    """
    try:
        cached = _TARGET_CACHE.pop(int(chat_id), None)
    except (TypeError, ValueError):
        return
    if cached is not None and cached[1] is not None:
        _reset_lane_circuit(cached[1])
    parsed = _parse_target(new_target) if new_target not in (None, "") else None
    if parsed is not None:
        _reset_lane_circuit(parsed)


def mark_audit_handled(dedupe_key: Any, ttl: float = AUDIT_DEDUPE_TTL) -> None:
    """Marca un hecho como ya registrado (p. ej. una sanción) para que otra vía no lo duplique."""
    _dedupe_claim(str(dedupe_key), ttl, claim_only=True)


def get_audit_stats() -> Dict[str, Any]:
    """Diagnóstico: contadores globales y estado de cada canal de registro activo."""
    return {**_STATS, "lanes": {key: {"pending": len(lane.queue), "dropped": lane.dropped,
                                      "circuit_open": lane.circuit_until > time.monotonic()}
                                for key, lane in _LANES.items()}}


def _prune(cache: Dict[Any, Any]) -> None:
    if len(cache) > _CACHE_MAX:
        for key in list(cache)[: len(cache) // 2]:
            cache.pop(key, None)


def _dedupe_claim(key: str, ttl: float, claim_only: bool = False) -> bool:
    """True si el hecho NO estaba registrado (y queda reclamado). Sin awaits: atómico dentro del event loop."""
    now = time.monotonic()
    expires = _DEDUPE.get(key)
    if expires is not None and expires > now and not claim_only:
        return False
    _DEDUPE[key] = now + max(1.0, float(ttl))
    if len(_DEDUPE) > _CACHE_MAX:
        for stale in [k for k, exp in _DEDUPE.items() if exp <= now]:
            _DEDUPE.pop(stale, None)
        _prune(_DEDUPE)
    return True


# ==========================================
# 🔎 RESOLUCIÓN DE CANAL, IDIOMA Y TÍTULO
# ==========================================
def _parse_target(raw: Any) -> Optional[Union[int, str]]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    if text.startswith("@") and len(text) > 1:
        return text
    return None


async def _resolve_target(chat_id: int) -> Tuple[Optional[Union[int, str]], frozenset]:
    now = time.monotonic()
    cached = _TARGET_CACHE.get(chat_id)
    if cached and now - cached[0] < AUDIT_TARGET_TTL:
        return cached[1], cached[2]
    target: Optional[Union[int, str]] = None
    disabled: frozenset = frozenset()
    try:
        if _get_audit_log_target is not None:
            info = await _get_audit_log_target(chat_id) or {}
            target = _parse_target(info.get("log_channel_id"))
            disabled = frozenset(info.get("disabled") or ())
        else:   # pragma: no cover - base de datos anterior a v39
            import database.database as _db
            raw = await asyncio.to_thread(_db._get_setting, chat_id, "log_channel_id", None)
            target = _parse_target(raw)
    except Exception as ex:
        logger.debug(f"[Audit] Canal de registro no disponible para {chat_id}: {ex}")
        if cached:
            return cached[1], cached[2]
    _TARGET_CACHE[chat_id] = (now, target, disabled)
    _prune(_TARGET_CACHE)
    return target, disabled


async def _resolve_lang(chat_id: int) -> str:
    now = time.monotonic()
    cached = _LANG_CACHE.get(chat_id)
    if cached and now - cached[0] < AUDIT_LANG_TTL:
        return cached[1]
    lang = "en"
    if _get_service_prefs is not None:
        try:
            prefs = await _get_service_prefs(chat_id) or {}
            lang = "es" if str(prefs.get("lang") or "").lower().startswith("es") else "en"
        except Exception:
            lang = cached[1] if cached else "en"
    _LANG_CACHE[chat_id] = (now, lang)
    _prune(_LANG_CACHE)
    return lang


async def _resolve_title(bot: Any, chat_id: int, given: Optional[str]) -> str:
    if given:
        _TITLE_CACHE[chat_id] = (time.monotonic(), str(given))
        return str(given)
    now = time.monotonic()
    cached = _TITLE_CACHE.get(chat_id)
    if cached and now - cached[0] < AUDIT_TITLE_TTL:
        return cached[1]
    title = ""
    for candidate in (bot, _master_bot()):
        if candidate is None:
            continue
        try:
            chat = await asyncio.wait_for(candidate.get_chat(chat_id), timeout=5)
            title = str(getattr(chat, "title", None) or getattr(chat, "full_name", None) or "")
            break
        except Exception:
            continue
    if title:
        _TITLE_CACHE[chat_id] = (now, title)
        _prune(_TITLE_CACHE)
    return title or str(chat_id)


# ==========================================
# 🎨 CONSTRUCCIÓN DE TARJETAS (función pura, sin I/O)
# ==========================================
def _t(lang: str, pair: Tuple[str, str]) -> str:
    return pair[1] if lang == "es" else pair[0]


def _esc(value: Any, limit: int = 200) -> str:
    text = str(value if value is not None else "").replace("\r", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text, quote=False)


def _person(obj: Any = None, pid: Any = None, name: Any = None, username: Any = None) -> Optional[Dict[str, Any]]:
    """Normaliza un usuario (objeto aiogram o datos sueltos) → {id, name, username, is_bot}."""
    if obj is not None and not isinstance(obj, (int, str)):
        pid = pid or getattr(obj, "id", None)
        full = getattr(obj, "full_name", None) or " ".join(
            p for p in (getattr(obj, "first_name", None), getattr(obj, "last_name", None)) if p) or getattr(obj, "title", None)
        name = name or full
        username = username or getattr(obj, "username", None)
        is_bot = bool(getattr(obj, "is_bot", False))
    else:
        if isinstance(obj, (int, str)) and str(obj).lstrip("-").isdigit():
            pid = pid or int(obj)
        is_bot = False
    try:
        pid_int = int(pid) if pid not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        pid_int = None
    if pid_int is None and not name and not username:
        return None
    return {"id": pid_int, "name": str(name or "").strip(), "username": str(username or "").lstrip("@").strip(), "is_bot": is_bot}


def _person_line(person: Dict[str, Any], lang: str) -> str:
    pid = person.get("id")
    name = person.get("name") or (f"@{person['username']}" if person.get("username") else (str(pid) if pid else "?"))
    shown = _esc(name, 64)
    mention = f'<a href="tg://user?id={pid}">{shown}</a>' if pid and pid > 0 else f"<b>{shown}</b>"
    parts = [("🤖 " if person.get("is_bot") else "") + mention]
    if pid:
        parts.append(f"<code>{pid}</code>")
    if person.get("username") and person.get("name"):
        parts.append(f"@{_esc(person['username'], 40)}")
    return " · ".join(parts)


def _fmt_duration(minutes: Optional[float], lang: str) -> str:
    if minutes is None:
        return ""
    total = max(0, int(round(float(minutes))))
    if total == 0:
        return "∞ (permanente)" if lang == "es" else "∞ (permanent)"
    if total % 1440 == 0:
        days = total // 1440
        return f"{days} d"
    if total % 60 == 0:
        return f"{total // 60} h"
    return f"{total} min"


def _fmt_stamp(value: Any) -> str:
    """Epoch (int/float), datetime o 'YYYY-MM-DD HH:MM:SS' de SQLite → texto UTC legible."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        elif isinstance(value, (int, float)) or str(value).strip().isdigit():
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            return _esc(str(value).replace("T", " ").replace("Z", "")[:19] + " UTC", 40)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return _esc(value, 40)


def _stamp_is_future(value: Any, now: Optional[datetime] = None) -> bool:
    """¿La fecha aún no llegó? (sanción temporal vigente → «Hasta»; membresía vencida → «Venció el»)."""
    try:
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        elif isinstance(value, (int, float)) or str(value).strip().isdigit():
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "")[:19]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return dt > (now or datetime.now(timezone.utc))


def _hashtag(word: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(word or "")).strip("_").upper()
    return f"#{clean}" if clean else ""


def build_audit_card(chat_id: int, event_type: str, lang: str = "en", chat_title: str = "",
                     now: Optional[datetime] = None, **kwargs: Any) -> str:
    """Tarjeta HTML (≤ 4096 caracteres) para un evento. Pura: se prueba sin Telegram."""
    lang = "es" if lang == "es" else "en"
    category, emoji, tag, title_en, title_es = EVENTS.get(event_type, _GENERIC_EVENT)
    title = title_es if lang == "es" else title_en
    action = str(kwargs.get("action") or "").strip().lower()
    if event_type == "sanction" and action in ("mute", "kick", "ban"):
        tag = action.upper()
    if event_type not in EVENTS:
        title = f"{title} · {event_type}"

    user = _person(kwargs.get("user"), kwargs.get("user_id"), kwargs.get("user_name"), kwargs.get("username"))
    actor = _person(kwargs.get("actor"), kwargs.get("actor_id"), kwargs.get("actor_name"), kwargs.get("actor_username"))

    tags = [_hashtag(tag)]
    if user and user.get("id"):
        tags.append(f"#id{user['id']}")
    lines = [f"{emoji} <b>{_esc(title, 120)}</b>", " ".join(t for t in tags if t), DIVIDER]

    lines.append(f"🏘️ <b>{_t(lang, _LABELS['community'])}:</b> <b>{_esc(chat_title or chat_id, 80)}</b> · <code>{int(chat_id)}</code>")
    if user:
        lines.append(f"👤 <b>{_t(lang, _LABELS['user'])}:</b> {_person_line(user, lang)}")
    if actor and (not user or actor.get("id") != user.get("id")):
        lines.append(f"👮 <b>{_t(lang, _LABELS['actor'])}:</b> {_person_line(actor, lang)}")
    elif kwargs.get("automatic") or kwargs.get("system"):
        system_key = str(kwargs.get("system") or "")
        system = _t(lang, _SYSTEMS[system_key]) if system_key in _SYSTEMS else _esc(system_key, 60)
        lines.append(f"🤖 <b>{_t(lang, _LABELS['actor'])}:</b> {_t(lang, _LABELS['automatic'])}"
                     + (f" · {system}" if system else ""))

    if kwargs.get("via"):
        via_key = str(kwargs.get("via"))
        via = _t(lang, _VIA[via_key]) if via_key in _VIA else _esc(via_key, 60)
        extras = []
        if kwargs.get("link_name"):
            extras.append(f"«{_esc(kwargs.get('link_name'), 40)}»")
        inviter = _person(kwargs.get("inviter"), kwargs.get("inviter_id"), kwargs.get("inviter_name"))
        if inviter:
            extras.append(_person_line(inviter, lang))
        lines.append(f"🔗 <b>{_t(lang, _LABELS['via'])}:</b> {via}" + (f" · {' · '.join(extras)}" if extras else ""))
    if kwargs.get("reason"):
        lines.append(f"📝 <b>{_t(lang, _LABELS['reason'])}:</b> {_esc(kwargs.get('reason'), 300)}")
    if action:
        shown = _t(lang, _ACTIONS[action]) if action in _ACTIONS else _esc(action.upper(), 40)
        lines.append(f"⚖️ <b>{_t(lang, _LABELS['action'])}:</b> {shown}")
    if kwargs.get("strikes") is not None and kwargs.get("limit") is not None:
        try:
            strikes, limit = int(kwargs["strikes"]), max(1, int(kwargs["limit"]))
            bar = "🟥" * min(strikes, limit) + "⬜" * max(0, limit - strikes)
            lines.append(f"🔢 <b>{_t(lang, _LABELS['strikes'])}:</b> <b>{strikes}/{limit}</b> {bar if limit <= 10 else ''}".rstrip())
        except (TypeError, ValueError):
            pass
    minutes: Optional[float] = None
    if kwargs.get("duration_minutes") is not None:
        minutes = float(kwargs["duration_minutes"])
    elif kwargs.get("duration_seconds") is not None:
        minutes = float(kwargs["duration_seconds"]) / 60.0
    if minutes is not None:
        lines.append(f"⏳ <b>{_t(lang, _LABELS['duration'])}:</b> {_fmt_duration(minutes, lang)}")
    if kwargs.get("level") is not None:
        lines.append(f"🌡️ <b>{_t(lang, _LABELS['level'])}:</b> {_esc(kwargs.get('level'), 40)}")
    if kwargs.get("signals"):
        lines.append(f"📡 <b>{_t(lang, _LABELS['signals'])}:</b> {_esc(kwargs.get('signals'), 200)}")
    if kwargs.get("count") is not None:
        window = kwargs.get("window_seconds")
        suffix = f" / {int(window)} s" if window else ""
        lines.append(f"📈 <b>{_t(lang, _LABELS['count'])}:</b> {_esc(kwargs.get('count'), 20)}{suffix}")
    if kwargs.get("plan_name"):
        lines.append(f"🏷️ <b>{_t(lang, _LABELS['plan'])}:</b> {_esc(kwargs.get('plan_name'), 80)}")
    if kwargs.get("amount") is not None:
        try:
            amount = int(kwargs["amount"])
            currency = str(kwargs.get("currency") or "XTR").upper()
            lines.append(f"⭐ <b>{_t(lang, _LABELS['amount'])}:</b> <b>{amount}</b> Stars ({_esc(currency, 8)})")
        except (TypeError, ValueError):
            pass
    if kwargs.get("duration_days") is not None:
        lines.append(f"🗓️ <b>{_t(lang, _LABELS['validity'])}:</b> {_esc(kwargs.get('duration_days'), 10)} "
                     + ("días" if lang == "es" else "days"))
    if kwargs.get("expires_at"):
        label = _LABELS["until"] if _stamp_is_future(kwargs.get("expires_at"), now) else _LABELS["expires"]
        lines.append(f"📅 <b>{_t(lang, label)}:</b> {_fmt_stamp(kwargs.get('expires_at'))}")
    if kwargs.get("detail"):
        lines.append(f"ℹ️ <b>{_t(lang, _LABELS['detail'])}:</b> {_esc(kwargs.get('detail'), 400)}")
    extra = kwargs.get("extra")
    if isinstance(extra, dict):
        for label, value in list(extra.items())[:8]:
            if value in (None, ""):
                continue
            shown_label = _t(lang, label) if isinstance(label, tuple) and len(label) == 2 else str(label)
            lines.append(f"• <b>{_esc(shown_label, 40)}:</b> {_esc(value, 200)}")
    if kwargs.get("excerpt"):
        lines.append(f"🗒️ <b>{_t(lang, _LABELS['message'])}:</b>")
        lines.append(f"<blockquote expandable>{_esc(kwargs.get('excerpt'), AUDIT_EXCERPT_MAX)}</blockquote>")

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"🕒 <b>{_t(lang, _LABELS['date'])}:</b> <code>{stamp}</code>")
    lines.append(DIVIDER)
    lines.append(SIGNATURE)
    text = "\n".join(lines)
    if len(text) > 4096:   # salvaguarda: el fragmento citado es lo primero que se recorta
        text = re.sub(r"<blockquote expandable>.*?</blockquote>", "<blockquote expandable>…</blockquote>", text, flags=re.S)
        text = text[:4090]
    return text


def event_category(event_type: str) -> str:
    return EVENTS.get(event_type, _GENERIC_EVENT)[0]


# ==========================================
# 📤 ENVÍO CON RITMO, REINTENTOS Y DISYUNTOR
# ==========================================
_RIGHTS_MARKERS = ("chat not found", "not enough rights", "have no rights", "need administrator", "not a member",
                   "bot was kicked", "bot is not a member", "chat_admin_required", "chat_write_forbidden",
                   "peer_id_invalid", "channel_private")


def _is_rights_error(exc: BaseException) -> bool:
    if isinstance(exc, (TelegramForbiddenError, TelegramNotFound)):
        return True
    return isinstance(exc, TelegramBadRequest) and any(m in str(exc).lower() for m in _RIGHTS_MARKERS)


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def _send_once(bot: Any, target: Union[int, str], item: _Item) -> None:
    await asyncio.wait_for(
        bot.send_message(chat_id=target, text=item.text, parse_mode=item.parse_mode,
                         disable_web_page_preview=True, disable_notification=item.silent),
        timeout=AUDIT_SEND_TIMEOUT,
    )


async def _deliver(lane: _Lane, item: _Item) -> bool:
    """Entrega un evento. True si Telegram lo aceptó."""
    bots = []
    for candidate in (item.bot, _master_bot()):
        if candidate is not None and all(candidate is not b for b in bots):
            bots.append(candidate)
    if not bots:
        return False
    rights_failures = 0
    for bot in bots:
        attempt = 0
        target = lane.target
        while attempt < 4:
            attempt += 1
            try:
                await _send_once(bot, target, item)
                return True
            except TelegramRetryAfter as ex:
                await asyncio.sleep(min(60.0, float(getattr(ex, "retry_after", 5) or 5)) + 0.5)
            except TelegramMigrateToChat as ex:
                new_id = getattr(ex, "migrate_to_chat_id", None)
                if not new_id or target == new_id:
                    break
                target = int(new_id)
                lane.target = target
            except (TelegramNetworkError, asyncio.TimeoutError):
                if attempt >= 3:
                    break
                await asyncio.sleep(2.0 * attempt)
            except TelegramBadRequest as ex:
                if _is_rights_error(ex):
                    rights_failures += 1
                    break
                if "parse" in str(ex).lower() and item.parse_mode:   # último recurso: texto plano sin parse_mode
                    item = _Item(bot=item.bot, text=_plain(item.text)[:4096], silent=item.silent, chat_id=item.chat_id,
                                 parse_mode=None)
                    continue
                logger.debug(f"[Audit] Telegram rechazó una tarjeta para {lane.target}: {ex}")
                return False
            except TelegramAPIError as ex:
                if _is_rights_error(ex):
                    rights_failures += 1
                    break
                logger.debug(f"[Audit] Error de Telegram publicando en {lane.target}: {ex}")
                return False
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.debug(f"[Audit] Error inesperado publicando en {lane.target}: {ex}")
                return False
    if rights_failures and rights_failures == len(bots):
        lane.circuit_until = time.monotonic() + AUDIT_CIRCUIT_SECONDS
        if not lane.warned:
            lane.warned = True
            logger.warning(f"⚠️ [Audit] El bot no puede publicar en el canal de registro {lane.target} "
                           f"(sin permisos o canal inexistente). Pausa de {int(AUDIT_CIRCUIT_SECONDS)} s.")
    return False


async def _lane_worker(key: str, lane: _Lane) -> None:
    try:
        idle_since = time.monotonic()
        while True:
            pending_summary = bool(lane.dropped) and lane.last_chat_id is not None
            if not lane.queue and not pending_summary:
                if time.monotonic() - idle_since > AUDIT_WORKER_IDLE:
                    return
                await asyncio.sleep(1.0)
                continue
            if lane.circuit_until > time.monotonic():
                # Sin permisos: lo pendiente se descarta (no cuenta como «volumen»; ya se avisó en el log del bot).
                _STATS["dropped"] += len(lane.queue)
                lane.queue.clear()
                lane.dropped = 0
                continue
            wait = lane.last_sent + AUDIT_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if lane.dropped and (lane.queue or lane.last_chat_id is not None):
                head = lane.queue[0] if lane.queue else _Item(bot=lane.last_bot, text="", silent=True,
                                                              chat_id=int(lane.last_chat_id or 0))
                lang = await _resolve_lang(head.chat_id)
                summary = (f"⚠️ <b>{_t(lang, _LABELS['skipped_title'])}</b>\n{DIVIDER}\n"
                           f"<b>{lane.dropped}</b> {_t(lang, _LABELS['skipped'])}\n{DIVIDER}\n{SIGNATURE}")
                lane.dropped = 0
                lane.queue.appendleft(_Item(bot=head.bot, text=summary, silent=True, chat_id=head.chat_id))
            item = lane.queue.popleft()
            ok = await _deliver(lane, item)
            lane.last_sent = time.monotonic()
            idle_since = lane.last_sent
            if ok:
                _STATS["sent"] += 1
                lane.warned = False
            else:
                _STATS["failed"] += 1
    except asyncio.CancelledError:
        raise
    except Exception as ex:   # el worker nunca debe morir en silencio con eventos pendientes
        logger.error(f"❌ [Audit] Worker del canal {key} detenido: {ex}", exc_info=True)
    finally:
        if _LANES.get(key) is lane:
            lane.worker = None
            if lane.queue:   # quedó trabajo (p. ej. tras una excepción): se relanza
                with contextlib.suppress(RuntimeError):
                    lane.worker = _spawn(_lane_worker(key, lane))
            elif not lane.dropped and lane.circuit_until <= time.monotonic():
                _LANES.pop(key, None)   # con el disyuntor abierto el carril se conserva: recuerda la pausa


def _enqueue(target: Union[int, str], item: _Item) -> bool:
    key = str(target)
    lane = _LANES.get(key)
    if lane is None:
        lane = _LANES[key] = _Lane(target=target)
    if lane.circuit_until > time.monotonic():
        _STATS["dropped"] += 1   # disyuntor abierto: se descarta sin coste (sin llamadas a la API)
        return False
    lane.last_bot, lane.last_chat_id = item.bot, item.chat_id
    if len(lane.queue) >= AUDIT_QUEUE_MAX:
        lane.dropped += 1
        _STATS["dropped"] += 1
        if lane.worker is None or lane.worker.done():
            lane.worker = _spawn(_lane_worker(key, lane))
        return False
    lane.queue.append(item)
    _STATS["queued"] += 1
    if lane.worker is None or lane.worker.done():
        lane.worker = _spawn(_lane_worker(key, lane))
    return True


# ==========================================
# 🚀 API PÚBLICA
# ==========================================
async def dispatch_audit_log(bot: Optional["Bot"], chat_id: int, event_type: str, **kwargs: Any) -> bool:
    """
    Registra `event_type` de la comunidad `chat_id` en su canal de registro. Devuelve True si quedó en cola.
    Diseñada para llamarse con `_spawn(...)`: nunca lanza y nunca bloquea al handler que la origina.
    """
    global _LOOP
    try:
        with contextlib.suppress(RuntimeError):
            _LOOP = asyncio.get_running_loop()
        chat_id = int(chat_id)
        defer = float(kwargs.pop("defer", 0) or 0)
        dedupe_key = kwargs.pop("dedupe_key", None)
        dedupe_ttl = float(kwargs.pop("dedupe_ttl", AUDIT_DEDUPE_TTL) or AUDIT_DEDUPE_TTL)
        if defer > 0:
            await asyncio.sleep(min(AUDIT_MAX_DEFER, defer))
        if dedupe_key is not None and not _dedupe_claim(f"{chat_id}:{dedupe_key}", dedupe_ttl):
            _STATS["deduped"] += 1
            return False

        target, disabled = await _resolve_target(chat_id)
        if target is None:
            _STATS["no_target"] += 1
            return False
        if isinstance(target, int) and target == chat_id:
            return False   # nunca se audita el propio canal de registro (evita bucles)
        category = event_category(event_type)
        if category in disabled:
            return False

        sender = bot if bot is not None else _master_bot()
        if sender is None:
            return False
        lang = await _resolve_lang(chat_id)
        title = await _resolve_title(sender, chat_id, kwargs.pop("chat_title", None))
        text = build_audit_card(chat_id, event_type, lang=lang, chat_title=title, **kwargs)
        return _enqueue(target, _Item(bot=sender, text=text, silent=category in SILENT_CATEGORIES, chat_id=chat_id))
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.debug(f"[Audit] Evento '{event_type}' de {chat_id} descartado: {ex}")
        return False


def _on_db_event(event_type: str, chat_id: int, **fields: Any) -> None:
    """Observador de la capa de datos (p. ej. expiraciones de membresía): programa el registro sin bloquear."""
    fields.setdefault("automatic", True)
    fields.setdefault("system", "memberships")
    try:
        asyncio.get_running_loop()
        _spawn(dispatch_audit_log(None, chat_id, event_type, **fields))
    except RuntimeError:   # llamado desde un hilo sin loop: se reenvía al loop principal conocido
        loop = _LOOP
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: _spawn(dispatch_audit_log(None, chat_id, event_type, **fields)))


try:
    from database.database import register_audit_listener as _register_audit_listener
    _register_audit_listener(_on_db_event)
except ImportError:  # pragma: no cover - base de datos anterior a v39
    pass