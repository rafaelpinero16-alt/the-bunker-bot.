"""
moderation.py — The Bunker OS (Aiogram 3.x)

Módulo de moderación táctica, sanciones remotas y control perimetral.
Integrado con la matriz de advertencias granular (Free / PRO / ULTRA PRO) y Centinela MTProto.

v25:
  • Seguridad: un operador de moderación debe ser creador, Arquitecto, administrador anónimo o un administrador con el
    derecho REAL de restringir miembros (can_restrict_members). Antes bastaba con figurar como "administrator", y los
    compradores de un pase MicVIP (ascendidos sin derechos solo para mostrar su título) podían ejecutar /ban, /mute,
    /kick, /warn o editar las listas globales.
  • /unban funciona de verdad con usuarios baneados por la escalera de advertencias: retira la marca global
    users.is_banned si la puso esta comunidad (antes el middleware anti-spam los volvía a expulsar al primer mensaje),
    reinicia sus strikes y cierra las apelaciones de pago abiertas para que nadie pague por algo ya resuelto.
  • Los baneos permanentes manuales registran el origen de la marca global (global_ban_origins).

v39 · Log Channel Hub (fase 2):
  • /ban, /kick y /mute publican en el canal de registro la acción HUMANA: administrador que la ordenó, administrador
    que pulsó el botón de ejecución, motivo escrito y duración. El motivo de «/ban @user spam» viaja de la orden a la
    consola remota (los botones no caben en 64 bytes) y se consume al ejecutarse.
  • También /unban, /unmute, /unwarn y el baneo por /blacklist. Las tarjetas genéricas de chat_member se suprimen para
    no duplicar (mark_audit_handled).
  • /warn pasa actor + manual=True a la escalera (sin métrica de sistema ni oferta de apelación pagada).
  • Tipado estricto: message.bot / message.from_user / callback.message (InaccessibleMessage) protegidos.

v40 · Fase 3 (Leaderboard y Falsos Positivos):
  • Cada /ban, /kick y /mute ejecutado (y el baneo por /blacklist) queda en el libro f3_sanctions; las sanciones
    manuales suman puntos al administrador que pulsó el botón (f3_admin_stats → leaderboard de la Mini App).
  • Su tarjeta del canal de registro lleva el botón «↩️ Reportar falso positivo» (callback fp_<id>, ≤ 64 bytes).
    Solo el DUEÑO de la comunidad puede pulsarlo: se revierte la sanción en Telegram (desbaneo / fin del silencio,
    marca global retirada si la puso esta comunidad, strikes y apelaciones abiertas cerradas), se restan al
    administrador los puntos sumados + la penalización, se marca la tarjeta y se publica #FALSO_POSITIVO.
  • revert_sanction() es el mismo servicio que usa la Mini App (POST /api/chat/{id}/sanctions/{sid}/false-positive).
The Bunker Command OS © 2026 — Cloud Media Management
"""
import os
import asyncio
import contextlib
import time
import html
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from database.database import (
    check_command_limit, get_group_tier,
    add_to_whitelist, remove_from_whitelist,
    ban_user, add_to_blacklist, get_blacklist,
    is_whitelisted, add_user_strike, get_user_strikes,
    reset_user_strikes, get_warns_config
)
import database.database as _db_module   # v25 · origen de la marca global y apelaciones (resolución diferida)
from assistant import set_participant_mic
# 🧾 v39 · Log Channel Hub: auditoría de la moderación manual en el canal de registro de la comunidad.
from handlers.logger_hub import dispatch_audit_log, mark_audit_handled
# 🏆 v40 · Fase 3: libro de sanciones, AdminStats y reversión de falsos positivos.
from database import crud_fase3 as crud_f3
from database.models_fase3 import FALSE_POSITIVE_PENALTY, AdminStatsRow, SanctionRow
from handlers import telegram_ops

logger = logging.getLogger("moderation_handler")
router = Router()

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

# Cuentas de servicio de Telegram: 777000 (canal vinculado), 1087968824 (admin anónimo),
# 136817688 (publicación como canal). Jamás pueden ser objetivo de una sanción.
SERVICE_ACCOUNT_IDS = {777000, 1087968824, 136817688}

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def is_super_admin(user_id: int) -> bool:
    try:
        return int(user_id) in SUPER_ADMIN_IDS
    except (TypeError, ValueError):
        return False


def _msg_lang(message: Message) -> str:
    """Idioma del autor tolerando mensajes sin from_user."""
    user = getattr(message, "from_user", None)
    return get_lang(getattr(user, "language_code", None) or "es")


def _is_anonymous_admin(message: Message) -> bool:
    return bool(message.sender_chat and message.sender_chat.id == message.chat.id)


# ==========================================
# 🧾 v39 · LOG CHANNEL HUB (AUDITORÍA DE LA ACCIÓN HUMANA)
# ==========================================
_MOD_REASON_TTL = 1800.0          # la consola remota caduca de hecho a los pocos minutos; 30 min es holgado
_MOD_REASON_MAX = 2000
# (chat_id, target_id, acción) → (motivo, id de quien ordenó, nombre de quien ordenó, marca de tiempo)
_PENDING_MOD_ORDERS: Dict[Tuple[int, int, str], Tuple[Optional[str], Optional[int], Optional[str], float]] = {}


def _audit(bot: Optional[Bot], chat_id: int, event_type: str, **fields: Any) -> None:
    """Publica el evento en el canal de registro sin bloquear el handler (vía _spawn). Nunca lanza."""
    _spawn(dispatch_audit_log(bot, chat_id, event_type, **fields))


def _audit_claim(chat_id: int, user_id: int, *kinds: str) -> None:
    """La acción ya tiene su tarjeta humana: el observador de chat_member no publica además la genérica."""
    for kind in kinds:
        mark_audit_handled(f"{int(chat_id)}:{kind}:{int(user_id)}", 30.0)


def _extract_reason(message: Message, command: Optional[CommandObject]) -> Optional[str]:
    """
    Motivo escrito por el operador: en respuesta, todos los argumentos («/ban spam»); con objetivo explícito, lo que
    sigue al ID / @alias («/ban @user spam»). Texto plano (el Hub lo escapa), una línea, máx. 200 caracteres.
    """
    args = (command.args or "").strip() if command is not None else ""
    if not args:
        return None
    if message.reply_to_message is not None and message.reply_to_message.from_user is not None:
        reason = args
    else:
        parts = args.split(maxsplit=1)
        reason = parts[1] if len(parts) > 1 else ""
    reason = re.sub(r"\s+", " ", reason).strip()[:200]
    return reason or None


def _operator_of(message: Message) -> Any:
    """Quién ordenó el comando: la persona o, si es un administrador anónimo, el propio grupo (sender_chat)."""
    if _is_anonymous_admin(message) and message.sender_chat is not None:
        return message.sender_chat
    return message.from_user


def _remember_order(message: Message, target_id: int, action: str, reason: Optional[str]) -> None:
    """Guarda motivo y autor de /ban, /kick o /mute hasta que alguien pulse el botón de ejecución."""
    now = time.time()
    if len(_PENDING_MOD_ORDERS) >= _MOD_REASON_MAX:
        for key in [k for k, v in _PENDING_MOD_ORDERS.items() if now - v[3] > _MOD_REASON_TTL] or \
                   sorted(_PENDING_MOD_ORDERS, key=lambda k: _PENDING_MOD_ORDERS[k][3])[:_MOD_REASON_MAX // 4]:
            _PENDING_MOD_ORDERS.pop(key, None)
    operator = _operator_of(message)
    op_id = getattr(operator, "id", None)
    op_name = getattr(operator, "full_name", None) or getattr(operator, "title", None)
    _PENDING_MOD_ORDERS[(int(message.chat.id), int(target_id), action)] = (
        reason, int(op_id) if op_id is not None else None, str(op_name) if op_name else None, now)


def _take_order(chat_id: int, target_id: int, action: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Consume la orden pendiente (motivo, id y nombre de quien la dio). Sin orden vigente → (None, None, None)."""
    entry = _PENDING_MOD_ORDERS.pop((int(chat_id), int(target_id), action), None)
    if entry is None or time.time() - entry[3] > _MOD_REASON_TTL:
        return None, None, None
    return entry[0], entry[1], entry[2]


def _order_fields(executor_id: int, ordered_by_id: Optional[int], ordered_by_name: Optional[str]) -> Dict[str, Any]:
    """Si otro administrador ordenó la sanción (p. ej. consola de un admin anónimo), queda constancia en la tarjeta."""
    if ordered_by_id is None or ordered_by_id == executor_id:
        return {}
    label = f"{ordered_by_name or 'ID'} · {ordered_by_id}"
    return {"extra": {("Ordered by", "Ordenado por"): label}}


def _fp_button(sanction_id: int) -> Tuple[str, str, str]:
    return ("↩️ Report false positive", "↩️ Reportar falso positivo", f"fp_{int(sanction_id)}")


async def _ledger(group_id: int, target_id: int, action: str, *, source: str = "manual", admin: Any = None,
                  user_name: Optional[str] = None, reason: Optional[str] = None, minutes: Optional[int] = None,
                  until_ts: Optional[int] = None, global_set: bool = False) -> Dict[str, Any]:
    """
    🏆 v40 · Anota la sanción ya aplicada en f3_sanctions (y suma puntos al admin si es manual). Devuelve los campos
    extra de la tarjeta: el botón «Reportar falso positivo». Un fallo de base de datos nunca deshace la sanción.
    """
    try:
        sanction_id = await crud_f3.record_sanction(
            int(group_id), int(target_id), action, source,
            admin_id=getattr(admin, "id", None) if source == "manual" else None,
            admin_name=(getattr(admin, "full_name", None) or getattr(admin, "title", None)) if admin is not None else None,
            user_name=user_name, reason=reason, duration_minutes=minutes or None, until_ts=until_ts,
            global_ban_set=global_set)
    except Exception as ex:
        logger.error(f"❌ [Fase3] Sanción {action} de {target_id} en {group_id} aplicada pero no registrada: {ex}")
        return {}
    return {"buttons": [_fp_button(sanction_id)]}


async def _edit_cb(callback: CallbackQuery, text: str) -> None:
    """Edita la consola remota; un mensaje inaccesible (InaccessibleMessage) no se puede editar."""
    msg = callback.message
    if not isinstance(msg, Message):
        return
    try:
        await msg.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass


_FULL_CHAT_PERMISSIONS = dict(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True
)


async def _chat_default_permissions(bot: Bot, chat_id: int) -> ChatPermissions:
    """
    Permisos por defecto del grupo (coherente con la aduana de groups.py): levantar una
    sanción devuelve al usuario al nivel general del grupo, no a permisos totales que
    saltarían las restricciones generales o un Raid Lockdown activo.
    """
    try:
        chat = await bot.get_chat(chat_id)
        if chat.permissions is not None:
            return chat.permissions
    except Exception:
        pass
    return ChatPermissions(**_FULL_CHAT_PERMISSIONS)


async def is_operator_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Creador, Arquitecto, administrador anónimo o administrador con el derecho REAL de restringir miembros.
    v25: un "administrador" sin can_restrict_members (p. ej. el título temporal de un pase MicVIP) no modera.
    """
    if is_super_admin(user_id) or user_id == 1087968824:  # Group Anonymous Bot
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception:
        return False
    status = str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", "")) or "")
    if status == "creator":
        return True
    return status == "administrator" and bool(getattr(member, "can_restrict_members", False))


async def _record_ban_origin(user_id: int, chat_id: int, source: str) -> None:
    """v25 · Anota qué comunidad activó la marca global users.is_banned (nunca sobrescribe un origen anterior)."""
    try:
        await _db_module.record_global_ban_origin(user_id, chat_id, source)
    except Exception as ex:
        logger.debug(f"Aviso registrando el origen del baneo global de {user_id}: {ex}")


async def _lift_after_unban(chat_id: int, user_id: int) -> dict:
    """
    v25 · Tras un /unban manual: retira la marca global SOLO si la puso esta comunidad, reinicia los strikes y cierra las
    apelaciones de pago abiertas. Nunca lanza. → {"global_lifted": bool, "appeals_closed": int}
    """
    out = {"global_lifted": False, "appeals_closed": 0}
    try:
        out["global_lifted"] = bool(await _db_module.lift_global_ban_if_origin(user_id, chat_id))
    except Exception as ex:
        logger.debug(f"Aviso retirando la marca global de {user_id}: {ex}")
    with contextlib.suppress(Exception):
        await reset_user_strikes(chat_id, user_id)
    try:
        out["appeals_closed"] = int(await _db_module.appeal_cancel_open(chat_id, user_id) or 0)
    except Exception as ex:
        logger.debug(f"Aviso cerrando apelaciones abiertas de {user_id}: {ex}")
    return out


async def is_target_protected(bot: Bot, chat_id: int, target_id: int) -> bool:
    """Verifica si el objetivo posee inmunidad absoluta contra sanciones."""
    if is_super_admin(target_id) or target_id in SERVICE_ACCOUNT_IDS:
        return True
    try:
        if await is_whitelisted(target_id):
            return True
    except Exception:
        pass
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=target_id)
        if member.status in ["creator", "administrator"]:
            return True
    except Exception:
        pass
    return False


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la respuesta correspondiente."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


def format_duration(minutes: int, lang: str) -> str:
    """Formatea la duración temporal de sanciones en lenguaje natural."""
    if minutes == 0:
        return "Permanent" if lang == "en" else "Permanente"
    elif minutes >= 1440:
        days = minutes // 1440
        return f"{days} days" if lang == "en" else f"{days} días"
    elif minutes >= 60:
        hours = minutes // 60
        return f"{hours} hours" if lang == "en" else f"{hours} horas"
    else:
        return f"{minutes} minutes" if lang == "en" else f"{minutes} minutos"


async def auto_delete_pair(cmd_msg: Message, bot_msg: Message, delay: int = 12):
    """Auto-destrucción dual estricta para mantener el chat limpio."""
    await asyncio.sleep(delay)
    try:
        await cmd_msg.delete()
    except Exception:
        pass
    try:
        await bot_msg.delete()
    except Exception:
        pass


async def auto_delete_msg(msg: Message, delay: int = 12):
    """Auto-destrucción simple de mensajes de advertencia temporal."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


# ==========================================
# 🌐 DICCIONARIO BILINGÜE DE MODERACIÓN TÁCTICA
# ==========================================
TEXTS = {
    "en": {
        "admin_only": "⛔ Access denied. Only community administrators can execute this protocol.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed": "⚠️ Target required. Reply to a message or use: <code>/{cmd} [ID or @username]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "target_protected": "🛡️ <b>Action Denied:</b> This user holds protected status (Architect, Admin, or Whitelist).\n\n🛡️ <i>Cloud Media Management</i>",
        "self_sanction": "⚠️ You cannot execute moderation protocols on yourself.\n\n🛡️ <i>Cloud Media Management</i>",
        "bot_sanction": "⚠️ System bots cannot be targeted with sanctions.\n\n🛡️ <i>Cloud Media Management</i>",
        "limit_reached": "⚠️ <b>Daily Command Limit Hit (Free Tier).</b>\n\nUpgrade to PRO or ULTRA to unlock unlimited moderation command lines.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_upgrade": "⭐ Upgrade to PRO / ULTRA",
        "start_pm_warning": "⚠️ Drop a quick /start in my DMs first to use Remote Moderation: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_title": "🚫 <b>Community:</b> {group_name}\n<b>Target:</b> {name}\n\nSelect Ban Duration:\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_title": "🔇 <b>Community:</b> {group_name}\n<b>Target:</b> {name}\n\nSelect Mute Duration:\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_title": "👢 <b>Community:</b> {group_name}\n<b>Target:</b> {name}\n\nConfirm Temporary Kick:\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_1m": "⏱️ 1 Min",
        "btn_10m": "⏱️ 10 Min",
        "btn_1h": "⏰ 1 Hour",
        "btn_24h": "⏰ 24 Hours",
        "btn_30d": "📅 30 Days",
        "btn_perm": "♾️ Permanent",
        "btn_confirm_kick": "⚡ Execute Kick",
        "btn_add_wl": "➕ Add User to Whitelist",
        "btn_add_bl": "➕ Add Word to Blacklist",
        "help_add_list": "To log a user or term, reply to their message with /{cmd} or type /{cmd} [ID or keyword].",
        "ban_success": "🚫 <b>{name}</b> has been permanently banned from the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_timed_success": "🚫 <b>{name}</b> has been banned for <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "unban_success": "🔓 <b>{name}</b> has been unbanned and unblocked from the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "unban_global_kept": "ℹ️ This user also carries a global ban set by another community or the Architect: they may be removed again when they post.",
        "kick_success": "👢 <b>{name}</b> has been kicked from the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>{name}</b> has been silenced for <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_perm": "🔇 <b>{name}</b> has been permanently silenced.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 <b>{name}</b>'s voice and chat privileges have been restored.\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Warning Issued</b>\n\nTarget: <b>{name}</b>\nStrikes: <b>{current}/{limit}</b>\nReason: <i>{reason}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_max_hit": "🚨 <b>Strike Limit Reached</b>\n\n<b>{name}</b> hit <b>{limit}/{limit}</b> warnings. Executing automated action: <code>{action}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_cleared": "🟢 Warnings reset for <b>{name}</b>. Full voice and chat restored.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "⚪ <b>{name}</b> registered in Whitelist. Tactical immunity active 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "⚪ <b>{name}</b> removed from Whitelist. Tactical immunity revoked 🔴\n\n🛡️️ <i>Cloud Media Management</i>",
        "wl_status": "⚪ <b>Active Whitelist:</b> Verified users exempt from security filters.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_added": "⚫ Keyword <code>{word}</code> registered in Blacklist. Auto-purge protocol active 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_status": "⚫ <b>Active Blacklist:</b> {total} prohibited terms logged in database.\n\n🛡️ <i>Cloud Media Management</i>",
        "error": "❌ Execution error: {error}\n\n🛡️ <i>Cloud Media Management</i>"
    },
    "es": {
        "admin_only": "⛔ Acceso denegado. Solo los administradores pueden ejecutar este protocolo.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/{cmd} [ID o @usuario]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "target_protected": "🛡️ <b>Acción Denegada:</b> Este usuario posee rango protegido (Arquitecto, Admin o Whitelist).\n\n🛡️ <i>Cloud Media Management</i>",
        "self_sanction": "⚠️ No puedes aplicarte una sanción a ti mismo.\n\n🛡️ <i>Cloud Media Management</i>",
        "bot_sanction": "⚠️ No puedes sancionar a los bots del sistema.\n\n🛡️ <i>Cloud Media Management</i>",
        "limit_reached": "⚠️ <b>Límite Diario Alcanzado (Plan Básico).</b>\n\nMejora a PRO o ULTRA para desbloquear moderación ilimitada.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_upgrade": "⭐ Mejorar a PRO / ULTRA",
        "start_pm_warning": "⚠️ Inicia un chat privado conmigo primero para usar la Moderación Remota: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_title": "🚫 <b>Comunidad:</b> {group_name}\n<b>Objetivo:</b> {name}\n\nSelecciona la duración del Baneo:\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_title": "🔇 <b>Comunidad:</b> {group_name}\n<b>Objetivo:</b> {name}\n\nSelecciona la duración del Silencio:\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_title": "👢 <b>Comunidad:</b> {group_name}\n<b>Objetivo:</b> {name}\n\nConfirmar Expulsión Temporal:\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_1m": "⏱️ 1 Min",
        "btn_10m": "⏱️ 10 Min",
        "btn_1h": "⏰ 1 Hora",
        "btn_24h": "⏰ 24 Horas",
        "btn_30d": "📅 30 Días",
        "btn_perm": "♾️ Permanente",
        "btn_confirm_kick": "⚡ Ejecutar Expulsión",
        "btn_add_wl": "➕ Añadir Usuario a Whitelist",
        "btn_add_bl": "➕ Añadir Término a Blacklist",
        "help_add_list": "Para registrar una identidad o término, responde a un mensaje con /{cmd} o escribe /{cmd} [ID o palabra].",
        "ban_success": "🚫 <b>{name}</b> ha sido desterrado permanentemente de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_timed_success": "🚫 <b>{name}</b> ha sido baneado por <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "unban_success": "🔓 <b>{name}</b> ha sido desbaneado y desbloqueado de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "unban_global_kept": "ℹ️ Este usuario además tiene un baneo global puesto por otra comunidad o por el Arquitecto: podría ser expulsado de nuevo al escribir.",
        "kick_success": "👢 <b>{name}</b> ha sido expulsado temporalmente del grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>{name}</b> ha sido silenciado por <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_perm": "🔇 <b>{name}</b> ha sido silenciado permanentemente.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 Privilegios de voz y chat de <b>{name}</b> restaurados con éxito.\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Advertencia Aplicada</b>\n\nObjetivo: <b>{name}</b>\nStrikes: <b>{current}/{limit}</b>\nMotivo: <i>{reason}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_max_hit": "🚨 <b>Límite de Advertencias Superado</b>\n\n<b>{name}</b> alcanzó <b>{limit}/{limit}</b> strikes. Ejecutando acción automática: <code>{action}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_cleared": "🟢 Historial de advertencias restablecido para <b>{name}</b>. Voz y chat restaurados.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "⚪ <b>{name}</b> registrado en Whitelist. Inmunidad táctica concedida 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "⚪ <b>{name}</b> retirado de la Whitelist. Inmunidad táctica revocada 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_status": "⚪ <b>Whitelist Activa:</b> Usuarios exentos de filtros anti-spam y moderación.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_added": "⚫ Término <code>{word}</code> registrado en Blacklist. Purga automática activa 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_status": "⚫ <b>Blacklist Activa:</b> {total} términos configurados.\n\n🛡️ <i>Cloud Media Management</i>",
        "error": "❌ Error de ejecución: {error}\n\n🛡️ <i>Cloud Media Management</i>"
    }
}


async def extract_target_user(message: Message, command: CommandObject):
    """Extrae la entidad de usuario objetivo mediante respuesta, mención @ o ID numérico."""
    if message.reply_to_message and message.reply_to_message.from_user:
        replied_user = message.reply_to_message.from_user
        # Respuesta a una publicación hecha como canal / admin anónimo: el from_user es una
        # cuenta de servicio de Telegram, no la persona real (sancionarla no tiene efecto).
        if replied_user.id in SERVICE_ACCOUNT_IDS:
            return None
        return replied_user
    bot = message.bot
    if bot is None:   # v39 · Pylance: Message.bot es Optional (siempre presente en un update real)
        return None
    if command and command.args:
        arg = command.args.split()[0].strip()
        if arg.isdigit():
            user_id = int(arg)
            try:
                chat_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=user_id)
                return chat_member.user
            except Exception:
                try:
                    user_chat = await bot.get_chat(user_id)
                    return user_chat
                except Exception:
                    # Objeto ligero para permitir sanción/desbaneo de IDs externos
                    class _SimpleUser:
                        def __init__(self, uid):
                            self.id = uid
                            self.full_name = f"ID: {uid}"
                            self.first_name = f"ID: {uid}"
                            self.username = None
                            self.is_bot = False
                    return _SimpleUser(user_id)
        elif arg.startswith("@"):
            try:
                user_chat = await bot.get_chat(arg)
                return user_chat
            except Exception:
                return None
    return None


async def enforce_limits(group_id: int, user_id: int, command_name: str, lang: str, bot: Bot):
    """Verifica la cuota diaria de comandos (Arquitectos/PRO/ULTRA: ilimitado, Free: 3/día)."""
    if is_super_admin(user_id):
        return None, None

    if not await check_command_limit(group_id, command_name):
        t = TEXTS[lang]
        bot_info = await bot.get_me()
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_upgrade"], url=f"https://t.me/{bot_info.username}?start=sub_pro_{group_id}")]
        ])
        return t["limit_reached"], markup
    return None, None


async def send_remote_menu(message: Message, lang: str, text: str, kb: InlineKeyboardMarkup | None):
    """Despacha la consola remota de sanción al chat privado del administrador ejecutor."""
    if _is_anonymous_admin(message) or not message.from_user:
        # Un administrador anónimo no tiene DM: la consola se muestra en el grupo de forma
        # temporal (cada botón vuelve a verificar que quien pulsa sea administrador).
        try:
            sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
            _spawn(auto_delete_pair(message, sent, delay=60))
        except Exception as ex:
            logger.debug(f"Aviso mostrando consola remota en grupo: {ex}")
        return
    bot = message.bot
    if bot is None:
        return
    try:
        await bot.send_message(chat_id=message.from_user.id, text=text, reply_markup=kb, parse_mode="HTML")
        tier = await get_group_tier(message.chat.id)
        if tier in ["pro", "ultra_pro"] or is_super_admin(message.from_user.id):
            try:
                await message.delete()
            except Exception:
                pass
    except Exception:
        bot_info = await bot.get_me()
        warn = await message.reply(TEXTS[lang]["start_pm_warning"].format(bot_user=bot_info.username), parse_mode="HTML")
        _spawn(auto_delete_pair(message, warn, delay=10))


async def validate_moderation_target(message: Message, command: CommandObject, bot: Bot, cmd_name: str) -> Tuple[Any, Any, Any]:
    """Función unificada para validar permisos, extraer el objetivo y aplicar filtros de protección."""
    if message.chat.type == "private":
        return None, None, None

    lang = _msg_lang(message)
    t = TEXTS[lang]

    # Reconocimiento de administradores anónimos en supergrupos
    is_anon = _is_anonymous_admin(message)
    if not is_anon and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return None, None, None

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd=cmd_name), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))
        return None, None, None

    bot_info = await bot.get_me()
    if target.id == bot_info.id:
        msg = await message.reply(t["bot_sanction"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=8))
        return None, None, None

    operator_id = message.from_user.id if message.from_user else 0   # v39 · Pylance (admin anónimo)
    if not is_anon and target.id == operator_id:
        msg = await message.reply(t["self_sanction"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=8))
        return None, None, None

    # En caso de desbaneo (/unban), se omite el bloqueo de objetivo protegido
    if cmd_name != "unban" and await is_target_protected(bot, message.chat.id, target.id):
        msg = await message.reply(t["target_protected"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=8))
        return None, None, None

    limit_text, markup = await enforce_limits(message.chat.id, operator_id, cmd_name, lang, bot)
    if limit_text:
        try:
            await bot.send_message(chat_id=operator_id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return None, None, None

    return lang, t, target


# ==========================================
# 🚫 PROTOCOLOS DE BANEO, EXPULSIÓN Y MUTE
# ==========================================
@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
    """Apertura del selector remoto de duración de baneo."""
    lang, t, target = await validate_moderation_target(message, command, bot, "ban")
    if not target:
        return

    raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
    target_name = html.escape(raw_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_1m"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_1"),
            InlineKeyboardButton(text=t["btn_10m"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_10")
        ],
        [
            InlineKeyboardButton(text=t["btn_1h"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_60"),
            InlineKeyboardButton(text=t["btn_24h"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_1440")
        ],
        [
            InlineKeyboardButton(text=t["btn_30d"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_43200"),
            InlineKeyboardButton(text=t["btn_perm"], callback_data=f"exec_ban_{message.chat.id}_{target.id}_0")
        ]
    ])

    chat_title = message.chat.title or f"Chat {message.chat.id}"
    text = t["ban_title"].format(group_name=html.escape(chat_title), name=target_name)
    # 🧾 v39 · Motivo y autor de la orden: los recoge el botón de ejecución para la tarjeta de auditoría.
    _remember_order(message, target.id, "ban", _extract_reason(message, command))
    await send_remote_menu(message, lang, text, kb)


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot):
    """Levanta el baneo de un usuario y restaura su acceso a la comunidad."""
    lang, t, target = await validate_moderation_target(message, command, bot, "unban")
    if not target:
        return

    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target.id, only_if_banned=True)
        lifted = await _lift_after_unban(message.chat.id, target.id)
        raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        target_name = html.escape(raw_name)
        text = t["unban_success"].format(name=target_name)
        still_global = False
        if not lifted["global_lifted"]:
            with contextlib.suppress(Exception):
                still_global = bool(await _db_module.is_user_globally_banned(target.id))
        if still_global:
            text = text.replace("\n\n🛡️", "\n" + t["unban_global_kept"] + "\n\n🛡️", 1)
        # 🧾 v39 · Auditoría: desbaneo manual (y si se retiró la marca global de esta comunidad).
        _audit(bot, message.chat.id, "unban", user=target, actor=_operator_of(message), action="unban",
               reason=_extract_reason(message, command), chat_title=message.chat.title,
               extra={("Global ban lifted", "Marca global retirada"): "✅" if lifted["global_lifted"] else
                      ("⚠️ kept / se mantiene" if still_global else "—"),
                      ("Appeals closed", "Apelaciones cerradas"): lifted["appeals_closed"] or None})
        await send_remote_menu(message, lang, text, None)
    except Exception as e:
        msg = await message.reply(t["error"].format(error=e), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject, bot: Bot):
    """Apertura de la confirmación remota de expulsión inmediata."""
    lang, t, target = await validate_moderation_target(message, command, bot, "kick")
    if not target:
        return

    raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
    target_name = html.escape(raw_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_confirm_kick"], callback_data=f"exec_kick_{message.chat.id}_{target.id}_0")]
    ])

    chat_title = message.chat.title or f"Chat {message.chat.id}"
    text = t["kick_title"].format(group_name=html.escape(chat_title), name=target_name)
    # 🧾 v39 · Motivo y autor de la orden: los recoge el botón de ejecución para la tarjeta de auditoría.
    _remember_order(message, target.id, "kick", _extract_reason(message, command))
    await send_remote_menu(message, lang, text, kb)


@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject, bot: Bot):
    """Apertura del selector remoto de duración de restricción de chat y voz."""
    lang, t, target = await validate_moderation_target(message, command, bot, "mute")
    if not target:
        return

    raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
    target_name = html.escape(raw_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_1m"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_1"),
            InlineKeyboardButton(text=t["btn_10m"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_10")
        ],
        [
            InlineKeyboardButton(text=t["btn_1h"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_60"),
            InlineKeyboardButton(text=t["btn_24h"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_1440")
        ],
        [
            InlineKeyboardButton(text=t["btn_30d"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_43200"),
            InlineKeyboardButton(text=t["btn_perm"], callback_data=f"exec_mute_{message.chat.id}_{target.id}_0")
        ]
    ])

    chat_title = message.chat.title or f"Chat {message.chat.id}"
    text = t["mute_title"].format(group_name=html.escape(chat_title), name=target_name)
    # 🧾 v39 · Motivo y autor de la orden: los recoge el botón de ejecución para la tarjeta de auditoría.
    _remember_order(message, target.id, "mute", _extract_reason(message, command))
    await send_remote_menu(message, lang, text, kb)


# ==========================================
# ⚠️ SISTEMA DE ADVERTENCIAS INTEGRADO (FASE 3)
# ==========================================
@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject, bot: Bot):
    """
    Aplica una advertencia conectada a la matriz granular por niveles (Free / PRO / ULTRA PRO)
    delegando en enforce_warn_ladder para garantizar textos, multimedia y atenuación acústica exacta.
    """
    lang, t, target = await validate_moderation_target(message, command, bot, "warn")
    if not target:
        return

    # Extracción fiel del motivo según provenga de respuesta directa o argumentos
    reason = "Violación de normas perimetrales" if lang == "es" else "Perimeter rules violation"
    if message.reply_to_message:
        if command and command.args:
            reason = command.args.strip()
    elif command and command.args:
        args_parts = command.args.split(maxsplit=1)
        if len(args_parts) > 1:
            reason = args_parts[1].strip()

    raw_name = getattr(target, "full_name", None) or getattr(target, "first_name", None) or f"ID {target.id}"
    clean_username = getattr(target, "username", "") or ""
    target_mention = f'<a href="tg://user?id={target.id}">{html.escape(raw_name)}</a>'
    # El motivo es texto libre del operador y se inserta en mensajes HTML.
    reason = html.escape(reason[:200])

    try:
        from handlers.groups import enforce_warn_ladder
        outcome = await enforce_warn_ladder(
            bot=bot,
            chat_id=message.chat.id,
            target_id=target.id,
            target_username=clean_username,
            target_mention=target_mention,
            reply_to=message,
            reason=reason,
            # v39 · advertencia MANUAL: autor en la auditoría; sin métrica de sistema ni oferta de apelación pagada.
            actor=_operator_of(message),
            manual=True
        )
        if outcome.get("status") in ("warned", "sanctioned", "immune"):
            try:
                await message.delete()
            except Exception:
                pass
            return
    except Exception as e:
        logger.debug(f"Aviso ejecutando ladder de advertencias desde moderation: {e}")

    # Fallback robusto en caso de excepción
    cfg = await get_warns_config(message.chat.id)
    limit = cfg.get("limit", 3)
    action = cfg.get("action", "mute")
    current_strikes = await add_user_strike(message.chat.id, target.id, reason=reason)
    # 🧾 v39 · Ruta de respaldo (sin escalera disponible): la auditoría se registra aquí.
    audit_fields: Dict[str, Any] = {"user": target, "actor": _operator_of(message), "reason": html.unescape(reason),
                                    "strikes": current_strikes, "limit": limit, "chat_title": message.chat.title}

    if current_strikes >= limit:
        await reset_user_strikes(message.chat.id, target.id)
        try:
            if action == "ban":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
            elif action == "kick":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=message.chat.id, user_id=target.id, only_if_banned=True)
            elif action == "mute":
                await bot.restrict_chat_member(
                    chat_id=message.chat.id,
                    user_id=target.id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            try:
                await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=True, volume=0)
            except Exception:
                pass
            _audit_claim(message.chat.id, target.id, "removal", "restrict")
            _audit(bot, message.chat.id, "sanction", action=action, **audit_fields)   # 🧾 v39
            msg = await message.reply(t["warn_max_hit"].format(name=target_mention, limit=limit, action=action.upper()), parse_mode="HTML")
        except Exception as ex:
            msg = await message.reply(f"❌ Error: {html.escape(str(ex))}", parse_mode="HTML")
    else:
        try:
            vol = int(10000 * max(0, limit - current_strikes) / max(1, limit))
            await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=False, volume=vol)
        except Exception:
            pass
        _audit(bot, message.chat.id, "warn", action="warn", **audit_fields)   # 🧾 v39
        msg = await message.reply(t["warn_issued"].format(name=target_mention, current=current_strikes, limit=limit, reason=reason), parse_mode="HTML")

    _spawn(auto_delete_pair(message, msg, delay=12))


@router.message(Command("unwarn", "resetwarns"))
async def cmd_unwarn(message: Message, command: CommandObject, bot: Bot):
    """Restablece a cero las advertencias acumuladas por un usuario y restaura facultades de texto y voz."""
    if message.chat.type == "private":
        return
    lang = _msg_lang(message)
    t = TEXTS[lang]

    if not _is_anonymous_admin(message) and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd="unwarn"), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))
        return

    await reset_user_strikes(message.chat.id, target.id)
    # 🧾 v39 · Auditoría: historial a cero (la restauración de permisos que sigue no genera otra tarjeta).
    _audit_claim(message.chat.id, target.id, "unrestrict")
    _audit(bot, message.chat.id, "warns_reset", user=target, actor=_operator_of(message),
           reason=_extract_reason(message, command), chat_title=message.chat.title)

    # 1. Restaurar volumen acústico en la llamada
    try:
        await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=False, volume=10000)
    except Exception:
        pass

    # 2. Restaurar los permisos por defecto del grupo en el chat general
    permissions = await _chat_default_permissions(bot, message.chat.id)
    try:
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=target.id, permissions=permissions)
    except Exception as e:
        logger.debug(f"Aviso restaurando permisos de chat en unwarn ({target.id}): {e}")

    raw_name = getattr(target, "full_name", None) or getattr(target, "first_name", None) or f"ID {target.id}"
    text = t["warn_cleared"].format(name=html.escape(raw_name))
    msg = await message.reply(text, parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, delay=12))


# ==========================================
# ⚡ EJECUCIÓN DIRECTA DESDE BOTONES REMOTOS
# ==========================================
@router.callback_query(F.data.startswith("exec_"))
async def cb_mod_execution(callback: CallbackQuery, bot: Bot):
    """Ejecuta la sanción seleccionada sincronizándola en tiempo real con el Centinela de voz."""
    await callback.answer()
    parts = (callback.data or "").split("_")
    if len(parts) < 5:
        return

    action = parts[1]
    try:
        group_id = int(parts[2])
        target_id = int(parts[3])
        minutes = int(parts[4])
    except ValueError:
        return
    if action not in ("ban", "kick", "mute") or minutes < 0 or minutes > 525600:
        return

    lang = get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, group_id, callback.from_user.id):
        await _edit_cb(callback, t["admin_only"])
        return

    if await is_target_protected(bot, group_id, target_id):
        await _edit_cb(callback, t["target_protected"])
        return

    target_user: Any = target_id
    try:
        target_member = await bot.get_chat_member(chat_id=group_id, user_id=target_id)
        target_user = target_member.user
        raw_name = target_member.user.full_name or f"ID: {target_id}"
    except Exception:
        raw_name = f"ID: {target_id}"

    target_name = html.escape(raw_name)
    # 🧾 v39 · Datos de la tarjeta: quién ejecuta (botón), quién ordenó y con qué motivo (comando /ban /kick /mute).
    reason, ordered_by_id, ordered_by_name = _take_order(group_id, target_id, action)
    audit_fields: Dict[str, Any] = {
        "user": target_user, "user_id": target_id, "actor": callback.from_user, "reason": reason, "action": action,
        **_order_fields(callback.from_user.id, ordered_by_id, ordered_by_name),
    }

    try:
        # Sincronización acústica en vivo con el Centinela
        try:
            await set_participant_mic(chat_id=group_id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass

        if action == "ban":
            _audit_claim(group_id, target_id, "removal")
            if minutes == 0:
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id)
                already_global = False
                with contextlib.suppress(Exception):
                    already_global = bool(await _db_module.is_user_globally_banned(target_id))
                await ban_user(target_id)
                if not already_global:
                    await _record_ban_origin(target_id, group_id, "manual_ban")
                ledger = await _ledger(group_id, target_id, "ban", admin=callback.from_user, user_name=raw_name,
                                       reason=reason, global_set=not already_global)   # 🏆 v40
                _audit(bot, group_id, "ban", duration_minutes=0,
                       detail="Global ban flag set" if not already_global else None, **audit_fields, **ledger)   # 🧾 v39
                await _edit_cb(callback, t["ban_success"].format(name=target_name))
            else:
                until_timestamp = int(time.time() + (minutes * 60))
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until_timestamp)
                ledger = await _ledger(group_id, target_id, "ban", admin=callback.from_user, user_name=raw_name,
                                       reason=reason, minutes=minutes, until_ts=until_timestamp)   # 🏆 v40
                _audit(bot, group_id, "ban", duration_minutes=minutes, expires_at=until_timestamp,
                       **audit_fields, **ledger)   # 🧾 v39
                dur = format_duration(minutes, lang)
                await _edit_cb(callback, t["ban_timed_success"].format(name=target_name, duration=dur))

            # Sincronización del padrón local
            try:
                from handlers.groups import registry_forget
                await registry_forget(group_id, target_id)
            except Exception:
                pass

        elif action == "kick":
            _audit_claim(group_id, target_id, "removal")
            await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
            await bot.unban_chat_member(chat_id=group_id, user_id=target_id, only_if_banned=True)
            ledger = await _ledger(group_id, target_id, "kick", admin=callback.from_user, user_name=raw_name,
                                   reason=reason)   # 🏆 v40
            _audit(bot, group_id, "kick", **audit_fields, **ledger)   # 🧾 v39
            await _edit_cb(callback, t["kick_success"].format(name=target_name))

            try:
                from handlers.groups import registry_forget
                await registry_forget(group_id, target_id)
            except Exception:
                pass

        elif action == "mute":
            perms = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False
            )
            _audit_claim(group_id, target_id, "restrict")
            if minutes == 0:
                await bot.restrict_chat_member(chat_id=group_id, user_id=target_id, permissions=perms)
                ledger = await _ledger(group_id, target_id, "mute", admin=callback.from_user, user_name=raw_name,
                                       reason=reason)   # 🏆 v40
                _audit(bot, group_id, "mute", duration_minutes=0, **audit_fields, **ledger)   # 🧾 v39
                await _edit_cb(callback, t["mute_perm"].format(name=target_name))
            else:
                until_timestamp = int(time.time() + (minutes * 60))
                await bot.restrict_chat_member(chat_id=group_id, user_id=target_id, permissions=perms, until_date=until_timestamp)
                ledger = await _ledger(group_id, target_id, "mute", admin=callback.from_user, user_name=raw_name,
                                       reason=reason, minutes=minutes, until_ts=until_timestamp)   # 🏆 v40
                _audit(bot, group_id, "mute", duration_minutes=minutes, expires_at=until_timestamp,
                       **audit_fields, **ledger)   # 🧾 v39
                dur = format_duration(minutes, lang)
                await _edit_cb(callback, t["mute_success"].format(name=target_name, duration=dur))

    except Exception as e:
        await _edit_cb(callback, t["error"].format(error=html.escape(str(e))))


# ==========================================
# 🔊 RESTAURACIÓN DE FACULTADES (UNMUTE DUAL)
# ==========================================
@router.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject, bot: Bot):
    """Restaura todos los permisos de envío en el chat y el micrófono al 100% en el videochat."""
    if message.chat.type == "private":
        return
    lang = _msg_lang(message)
    t = TEXTS[lang]

    if not _is_anonymous_admin(message) and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd="unmute"), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))
        return

    operator_id = message.from_user.id if message.from_user else 0   # v39 · Pylance (admin anónimo)
    limit_text, markup = await enforce_limits(message.chat.id, operator_id, "unmute", lang, bot)
    if limit_text:
        try:
            await bot.send_message(chat_id=operator_id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return

    permissions = await _chat_default_permissions(bot, message.chat.id)

    try:
        _audit_claim(message.chat.id, target.id, "unrestrict")
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=target.id, permissions=permissions)
        # 🧾 v39 · Auditoría: silencio retirado por un administrador.
        _audit(bot, message.chat.id, "unmute", user=target, actor=_operator_of(message), action="unmute",
               reason=_extract_reason(message, command), chat_title=message.chat.title)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=False, volume=10000)
        except Exception:
            pass

        raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        target_name = html.escape(raw_name)
        text = t["unmute_success"].format(name=target_name)
        await send_remote_menu(message, lang, text, None)
    except Exception as e:
        msg = await message.reply(t["error"].format(error=html.escape(str(e))), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))


# ==========================================
# ⚪ GESTIÓN DE LISTA BLANCA (WHITELIST)
# ==========================================
@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject, bot: Bot):
    """Concede inmunidad absoluta ante filtros antispam, cerraduras y captcha."""
    if message.chat.type == "private":
        return
    lang = _msg_lang(message)
    t = TEXTS[lang]

    if not _is_anonymous_admin(message) and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return

    target = await extract_target_user(message, command)

    operator_id = message.from_user.id if message.from_user else 0   # v39 · Pylance (admin anónimo)
    limit_text, markup = await enforce_limits(message.chat.id, operator_id, "whitelist", lang, bot)
    if limit_text:
        try:
            await bot.send_message(chat_id=operator_id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return

    if target and target.id in SERVICE_ACCOUNT_IDS:
        target = None
    if target:
        await add_to_whitelist(target.id)
        raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        text = t["wl_added"].format(name=html.escape(raw_name))
        await send_remote_menu(message, lang, text, None)
    elif command and command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
        await add_to_whitelist(target_id)
        text = t["wl_added"].format(name=f"ID: {target_id}")
        await send_remote_menu(message, lang, text, None)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t["btn_add_wl"], callback_data="help_wl")]])
        await send_remote_menu(message, lang, t["wl_status"], kb)


@router.message(Command("unwhitelist"))
async def cmd_unwhitelist(message: Message, command: CommandObject, bot: Bot):
    """Revoca la inmunidad táctica de un usuario previamente autorizado."""
    if message.chat.type == "private":
        return
    lang = _msg_lang(message)
    t = TEXTS[lang]

    if not _is_anonymous_admin(message) and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return

    target = await extract_target_user(message, command)

    if target:
        await remove_from_whitelist(target.id)
        raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        text = t["wl_removed"].format(name=html.escape(raw_name))
        await send_remote_menu(message, lang, text, None)
    elif command and command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
        await remove_from_whitelist(target_id)
        text = t["wl_removed"].format(name=f"ID: {target_id}")
        await send_remote_menu(message, lang, text, None)
    else:
        msg = await message.reply(t["target_needed"].format(cmd="unwhitelist"), parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, delay=10))


# ==========================================
# ⚫ GESTIÓN DE LISTA NEGRA (BLACKLIST)
# ==========================================
@router.message(Command("blacklist"))
async def cmd_blacklist(message: Message, command: CommandObject, bot: Bot):
    """Registra términos prohibidos con purga automática y advertencias tácticas."""
    if message.chat.type == "private":
        return
    lang = _msg_lang(message)
    t = TEXTS[lang]

    if not _is_anonymous_admin(message) and (not message.from_user or not await is_operator_admin(bot, message.chat.id, message.from_user.id)):
        try:
            await message.delete()
        except Exception:
            pass
        return

    operator_id = message.from_user.id if message.from_user else 0   # v39 · Pylance (admin anónimo)
    limit_text, markup = await enforce_limits(message.chat.id, operator_id, "blacklist", lang, bot)
    if limit_text:
        try:
            await bot.send_message(chat_id=operator_id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return

    if command and command.args:
        word = command.args.strip().lower()[:64]
        await add_to_blacklist(word)
        text = t["bl_added"].format(word=html.escape(word))
        await send_remote_menu(message, lang, text, None)
        return

    if message.reply_to_message:
        replied_text = message.reply_to_message.text or message.reply_to_message.caption
        if replied_text:
            word = replied_text.strip().lower()
            if len(word) > 50:
                msg = await message.reply(
                    "⚠️ El texto es demasiado largo para ser una palabra prohibida. Usa <code>/blacklist [palabra]</code>.\n\n🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                _spawn(auto_delete_pair(message, msg, delay=10))
                return
            await add_to_blacklist(word)
            text = t["bl_added"].format(word=html.escape(word))
            await send_remote_menu(message, lang, text, None)
            return
        else:
            target = message.reply_to_message.from_user
            if target and target.id not in SERVICE_ACCOUNT_IDS:
                if await is_target_protected(bot, message.chat.id, target.id):
                    msg = await message.reply(t["target_protected"], parse_mode="HTML")
                    _spawn(auto_delete_pair(message, msg, delay=8))
                    return

                already_global = False
                with contextlib.suppress(Exception):
                    already_global = bool(await _db_module.is_user_globally_banned(target.id))
                await ban_user(target.id)
                if not already_global:
                    await _record_ban_origin(target.id, message.chat.id, "blacklist_ban")
                _audit_claim(message.chat.id, target.id, "removal")
                try:
                    await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
                except Exception as ban_err:
                    msg = await message.reply(t["error"].format(error=html.escape(str(ban_err))), parse_mode="HTML")
                    _spawn(auto_delete_pair(message, msg, delay=10))
                    return
                # 🧾 v39 · Auditoría: baneo permanente desde /blacklist (respuesta a un mensaje sin texto).
                ledger = await _ledger(message.chat.id, target.id, "ban", source="blacklist", admin=_operator_of(message),
                                       user_name=getattr(target, "full_name", None), reason="Blacklist",
                                       global_set=not already_global)   # 🏆 v40
                _audit(bot, message.chat.id, "ban", user=target, actor=_operator_of(message), action="ban",
                       reason="Blacklist", duration_minutes=0, chat_title=message.chat.title,
                       detail="Global ban flag set" if not already_global else None, **ledger)
                try:
                    await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=True, volume=0)
                except Exception:
                    pass
                try:
                    from handlers.groups import registry_forget
                    await registry_forget(message.chat.id, target.id)
                except Exception:
                    pass
                raw_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
                text = t["ban_success"].format(name=html.escape(raw_name))
                await send_remote_menu(message, lang, text, None)
                return

    bl_items = await get_blacklist()
    total = len(bl_items) if bl_items else 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t["btn_add_bl"], callback_data="help_bl")]])
    await send_remote_menu(message, lang, t["bl_status"].format(total=total), kb)


@router.callback_query(F.data.startswith("help_"))
async def cb_help_lists(callback: CallbackQuery):
    """Muestra la guía táctica para registrar usuarios o términos en las listas."""
    lang = get_lang(callback.from_user.language_code)
    cmd_type = "whitelist" if callback.data == "help_wl" else "blacklist"
    await callback.answer(TEXTS[lang]["help_add_list"].format(cmd=cmd_type), show_alert=True)


# ==========================================
# ↩️ v40 · FALSOS POSITIVOS (botón del canal de registro y Mini App)
# ==========================================
RevertStatus = Literal["ok", "missing", "already", "forbidden", "telegram_failed"]


@dataclass(frozen=True)
class RevertOutcome:
    status: RevertStatus
    sanction: Optional[SanctionRow] = None
    stats: Optional[AdminStatsRow] = None
    code: Optional[str] = None
    detail: Optional[str] = None


async def _is_group_owner(group_id: int, user_id: int, bot: Optional[Bot]) -> bool:
    """Solo el creador REAL de la comunidad (en vivo, no la caché de administradores)."""
    return await telegram_ops.member_status(group_id, user_id, preferred=bot) == "creator"


async def revert_sanction(sanction_id: int, owner_id: int, *, bot: Optional[Bot] = None, owner: Any = None,
                          owner_verified: bool = False, origin: str = "log_channel") -> RevertOutcome:
    """
    Revierte una sanción del libro f3 como FALSO POSITIVO. Atómico frente a dobles pulsaciones (claim en BD antes de
    tocar Telegram); si Telegram no permite revertir, el claim y la penalización se deshacen. Nunca lanza.
    `owner_verified=True` cuando el llamador ya comprobó al dueño (la API de la Mini App usa assert_chat_ownership).
    """
    try:
        sanction = await crud_f3.get_sanction(int(sanction_id))
    except Exception as ex:
        return RevertOutcome("missing", detail=str(ex)[:200])
    if sanction is None:
        return RevertOutcome("missing")
    group_id, user_id = sanction["group_id"], sanction["user_id"]
    if not owner_verified and not await _is_group_owner(group_id, owner_id, bot):
        return RevertOutcome("forbidden", sanction=sanction)
    status, claimed, stats = await crud_f3.claim_false_positive(int(sanction_id), int(owner_id),
                                                                f"false_positive:{origin}")
    if status != "ok" or claimed is None:
        return RevertOutcome("already" if status == "already" else "missing", sanction=claimed or sanction)

    action = sanction["action"]
    result: telegram_ops.OpResult
    if action == "ban":
        mark_audit_handled(f"{group_id}:unban:{user_id}", 60.0)
        result = await telegram_ops.unban_member(group_id, user_id, preferred=bot)
        if result.ok:
            with contextlib.suppress(Exception):
                if sanction["global_ban_set"]:
                    await _db_module.lift_global_ban_if_origin(user_id, group_id)
            with contextlib.suppress(Exception):
                await reset_user_strikes(group_id, user_id)
            with contextlib.suppress(Exception):
                await _db_module.appeal_cancel_open(group_id, user_id)
    elif action == "mute":
        _audit_claim(group_id, user_id, "unrestrict")
        result = await telegram_ops.unmute_member(group_id, user_id, preferred=bot)
        if result.ok:
            with contextlib.suppress(Exception):
                await set_participant_mic(chat_id=group_id, user_id=user_id, muted=False, volume=10000)
    else:   # kick: no hay estado que deshacer en Telegram (el usuario ya puede volver); se corrige el historial
        result = telegram_ops.OpResult(ok=True)

    if not result.ok:
        with contextlib.suppress(Exception):
            await crud_f3.release_false_positive(int(sanction_id))
        logger.warning(f"[Fase3] Falso positivo #{sanction_id} no revertido en Telegram: {result.code} {result.detail}")
        return RevertOutcome("telegram_failed", sanction=sanction, code=result.code, detail=result.detail)

    extra: Dict[Any, Any] = {("Sanction", "Sanción"): f"#{sanction_id} · {action}",
                             ("Origin", "Origen"): "Mini App" if origin == "miniapp" else "Log channel"}
    if stats is not None:
        penalty = int(sanction["points_awarded"]) + FALSE_POSITIVE_PENALTY
        extra[("Admin penalized", "Admin penalizado")] = (f"{stats['admin_name'] or stats['admin_id']} · "
                                                          f"-{penalty} pts → {stats['points']} pts")
    elif sanction["source"] == "automod":
        extra[("Auto-Mod evidence", "Evidencia Auto-Mod")] = "cleared / limpiada"
    _audit(bot, group_id, "false_positive", user_id=user_id, user_name=sanction["user_name"],
           actor=owner if owner is not None else owner_id, action="unban" if action == "ban" else
           ("unmute" if action == "mute" else "none"), reason=sanction["reason"], extra=extra)
    return RevertOutcome("ok", sanction=claimed, stats=stats)


_FP_TEXTS: Dict[str, Dict[str, str]] = {
    "en": {"ok": "✅ Reverted as a false positive.", "missing": "⚠️ This sanction no longer exists.",
           "already": "ℹ️ It was already reverted.", "forbidden": "⛔ Only the community owner can report false positives.",
           "telegram_failed": "❌ Telegram refused the revert ({code}). Check the bot's admin rights.",
           "stamp": "↩️ <b>False positive</b> — reverted by {name} · {date} UTC"},
    "es": {"ok": "✅ Revertido como falso positivo.", "missing": "⚠️ Esta sanción ya no existe.",
           "already": "ℹ️ Ya estaba revertida.", "forbidden": "⛔ Solo el Dueño de la comunidad puede reportar falsos positivos.",
           "telegram_failed": "❌ Telegram rechazó la reversión ({code}). Revisa los permisos del bot.",
           "stamp": "↩️ <b>Falso positivo</b> — revertido por {name} · {date} UTC"},
}


@router.callback_query(F.data.startswith("fp_"))
async def cb_false_positive(callback: CallbackQuery, bot: Bot) -> None:
    """Botón «Reportar falso positivo» de las tarjetas del canal de registro (restringido al Dueño)."""
    raw = (callback.data or "")[3:]
    if not raw.isdigit():
        await callback.answer()
        return
    sanction_id = int(raw)
    sanction = await crud_f3.get_sanction(sanction_id)
    lang = "en"
    if sanction is not None:
        with contextlib.suppress(Exception):
            prefs = await _db_module.get_service_prefs(sanction["group_id"])
            lang = "es" if str(prefs.get("lang")) == "es" else "en"
    texts = _FP_TEXTS[lang]
    outcome = await revert_sanction(sanction_id, callback.from_user.id, bot=bot, owner=callback.from_user)
    message_text = texts[outcome.status].format(code=outcome.code or "?")
    with contextlib.suppress(TelegramAPIError):
        await callback.answer(message_text, show_alert=outcome.status != "ok")
    if outcome.status not in ("ok", "already"):
        return
    card = callback.message
    if not isinstance(card, Message):   # InaccessibleMessage (muy antiguo): no se puede editar
        return
    name = html.escape(callback.from_user.full_name or str(callback.from_user.id))
    stamp = texts["stamp"].format(name=name, date=time.strftime("%Y-%m-%d %H:%M", time.gmtime()))
    try:
        body = card.html_text if outcome.status == "ok" else ""
        if body and len(body) + len(stamp) + 2 <= 4096:
            await card.edit_text(f"{body}\n\n{stamp}", parse_mode="HTML", reply_markup=None,
                                 disable_web_page_preview=True)
        else:
            await card.edit_reply_markup(reply_markup=None)
    except TelegramAPIError as ex:
        logger.debug(f"[Fase3] No se pudo marcar la tarjeta del falso positivo #{sanction_id}: {ex}")
