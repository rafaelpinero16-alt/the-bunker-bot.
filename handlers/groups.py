"""
groups.py — The Bunker OS (Aiogram 3.x)

Núcleo de seguridad perimetral para grupos y supergrupos.
Motor Híbrido: Detección MTProto + Padrón Local Bot API + Protección Total por Niveles (Free / PRO / ULTRA PRO).
Fase 4: Gamificación Tokenizada (XP / Niveles / /rank / /top) + Analítica Heatmap 24x7.
Fase 5: Ascensos de nivel emitidos en caliente al radar WebSocket de la Mini App.
Fase 6: Aduana sin DMs fríos — el captcha privado solo se envía en ChatJoinRequest (ventana legal
        de 5 min vía user_chat_id); los ingresos directos se verifican únicamente en el grupo.
The Bunker Command OS © 2026 — Cloud Media Management
"""
import time
import string
import random
import asyncio
import os
import json
import sqlite3
import inspect
import logging
import contextlib
import html
import types
from typing import Any, Optional

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, ChatMemberUpdated, ChatJoinRequest, LabeledPrice, PreCheckoutQuery,
    WebAppInfo
)
import database.database as _db_module
from database.database import (
    get_antispam_filter, get_antispam_delete,
    get_antiflood_config, is_whitelisted, is_vip_mic_active, get_captcha_config,
    get_lock_status, get_warns_config, add_warning, ban_user,
    approve_group, register_user_group, get_blacklist,
    get_session_by_group, get_group_tier,
    add_user_strike, get_user_strikes, reset_user_strikes,
    get_panic_status, activate_panic, deactivate_panic,
    get_screen_shield_status, set_screen_shield_status,
    set_podcast_duck_volume, set_noise_shield_status,
    get_speaker_price, set_speaker_price, add_to_speaker_queue,
    get_speaker_queue, pop_next_speaker, remove_from_speaker_queue, clear_speaker_queue,
    flag_userbot, is_userbot_flagged,
    get_ghost_purge_config, update_ghost_purge_scan_time,
    get_service_msgs_mode,
    # 🎮 Fase 4: Gamificación y Analítica Horaria
    record_hourly_chat_activity,
    add_user_reputation_xp,
    get_user_reputation,
    get_top_reputation,
    get_chat_heatmap_matrix,
    # 🎮 Anuncio recurrente de gamificación
    get_gamification_announcement,
    set_gamification_announcement_field,
    schedule_gamification_announcement_now,
    # 🛡️ v9: métricas de bloqueos y Escudo Anti-Raid
    record_security_event,
    # 🚨🌐 v37: Anti-Raid configurable, lista negra de raid e idioma/TTL de servicio
    get_raid_config, raid_blacklist_add, raid_blacklist_resolve,
    get_service_prefs, set_service_lang, set_user_lang,
    DEFAULT_SERVICE_LANG, DEFAULT_SERVICE_TTL,
)
import raid_shield
import assistant as _assistant_module
import vip_roles     # 👑 v23 · Roles y etiquetas VIP automáticos (exenciones parciales, flood y prioridad acústica)
import link_quotas   # 🔗 v23 · Cuota diaria de enlaces / promociones por administrador
from handlers import media_locks   # 🔒 v15 · Matriz granular de bloqueo multimedia (30 vectores)
import importlib
import importlib.util
from assistant import active_sentinels, set_participant_mic, execute_ghost_purge


def _ban_appeals_module():
    """
    v26 · Módulo de apelaciones: ubicación oficial handlers/ban_appeals.py; se tolera ban_appeals.py en la raíz.
    find_spec decide la ruta ANTES de importar: un ImportError real dentro del módulo nunca queda enmascarado.
    """
    name = "handlers.ban_appeals" if importlib.util.find_spec("handlers.ban_appeals") is not None else "ban_appeals"
    return importlib.import_module(name)


ban_appeals = _ban_appeals_module()   # ⚖️ v24 · Apelación de baneos por Telegram Stars

try:
    from radar_bus import publish_radar_event  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional runtime dependency
    def publish_radar_event(*args, **kwargs):
        return None

try:
    from middlewares.anti_spam import check_global_cas_spam
except ImportError:
    async def check_global_cas_spam(user_id: int) -> bool:
        return False

logger = logging.getLogger("groups_handler")
router = Router()

_db_reset_warnings = getattr(_db_module, "reset_warnings", None)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://the-bunker-bot-bunkerminiapp.vercel.app/").strip()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


# ==========================================
# ⚙️ CONFIGURACIÓN GLOBAL
# ==========================================
FULL_VOLUME = 10000
SERVICE_ACCOUNT_IDS = {777000, 1087968824, 136817688}

USERBOT_ACTION = os.getenv("USERBOT_ACTION", "ban").strip().lower()
if USERBOT_ACTION not in ("ban", "mute", "delete"):
    USERBOT_ACTION = "ban"

WARN_PROGRESSIVE_ATTENUATION = _env_flag("WARN_PROGRESSIVE_ATTENUATION", True)
RESET_WARNS_AFTER_SANCTION = _env_flag("RESET_WARNS_AFTER_SANCTION", True)
MEMBER_REGISTRY_DB = os.getenv("MEMBER_REGISTRY_DB", "database/bot_data.db")

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


# ==========================================
# 🛡️ v9 · MÉTRICA DE BLOQUEOS (no bloqueante)
# ==========================================
async def _safe_record_security(group_id: int, user_id: int, source: str, action: str, is_bot: bool = False) -> None:
    try:
        await record_security_event(group_id, user_id, source, action, is_bot=is_bot)
    except Exception as e:
        logger.debug(f"Aviso registrando evento de seguridad ({group_id}/{user_id}): {e}")
    publish_radar_event(group_id, "security_block", {"u": user_id, "s": source, "a": action, "b": 1 if is_bot else 0})


def track_security(group_id: int, user_id: int, source: str, action: str, is_bot: bool = False) -> None:
    """Registra un bloqueo del sistema (Captcha, Anti-Flood, Anti-Spam, Guardián IA, Escudo) sin esperar."""
    _spawn(_safe_record_security(group_id, user_id, source, action, is_bot))


# ==========================================
# 🧠 CACHÉ DE ESTADO DE MIEMBROS CON PURGA
# ==========================================
_MEMBER_STATUS_CACHE: dict = {}
_MEMBER_STATUS_TTL = 60


async def _get_member_status(bot: Bot, chat_id: int, user_id: int, fresh: bool = False) -> Optional[str]:
    key = (chat_id, user_id)
    now = time.time()

    if not fresh:
        cached = _MEMBER_STATUS_CACHE.get(key)
        if cached and now - cached[1] < _MEMBER_STATUS_TTL:
            return cached[0]

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception:
        return None

    status = member.status
    _MEMBER_STATUS_CACHE[key] = (status, now)

    if len(_MEMBER_STATUS_CACHE) > 3000:
        expired = [k for k, (_, ts) in _MEMBER_STATUS_CACHE.items() if now - ts > _MEMBER_STATUS_TTL]
        for k in expired:
            _MEMBER_STATUS_CACHE.pop(k, None)

    return status


async def _is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    status = await _get_member_status(bot, chat_id, user_id, fresh=True)
    return status in ("creator", "administrator")


async def _message_author_is_admin(bot: Bot, message: Message) -> bool:
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    if not message.from_user:
        return False
    return await _is_group_admin(bot, message.chat.id, message.from_user.id)


# ==========================================
# 👑 v12 · REGLA DE ORO — SOLO CREADOR LEGÍTIMO O ARQUITECTO CONFIGURAN EL BOT
# ==========================================
# Los administradores comunes (con todos o algunos permisos) NO pueden usar comandos de configuración ni
# herramientas ejecutivas del bot. _is_group_admin se conserva solo para decisiones de PROTECCIÓN (inmunidad
# ante filtros, quién puede añadir bots durante un raid), nunca para conceder acceso a configuración.
# v37 · Se conservan los nombres (otros módulos los importan); el texto sale en el idioma de servicio del grupo.
OWNER_ONLY_ALERT = (
    "⛔ <b>Access denied:</b> this tool is restricted to the community <b>Owner</b>.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)
OWNER_ONLY_ALERT_SHORT = "⛔ Access denied: Owner only."
ANONYMOUS_ADMIN_ALERT = (
    "⛔ <b>Access denied:</b> anonymous admin messages cannot be verified as the Owner. Disable anonymity and retry.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

async def _is_community_owner(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Fuente única: handlers.user_private.is_legitimate_owner (Arquitecto → sí · creador en vivo → sí ·
    administrador común → NO · caída de la Bot API → solo dueño registrado sin rango contradictorio).
    Se importa en diferido porque user_private importa este módulo. Si no estuviera disponible, se aplica la
    regla mínima equivalente (Arquitecto o creador confirmado en vivo), nunca una más laxa.
    """
    if not user_id:
        return False
    if is_super_admin(user_id):
        return True
    try:
        from handlers.user_private import is_legitimate_owner  # import diferido (evita el ciclo de importación)
    except Exception:
        is_legitimate_owner = None
    if is_legitimate_owner is not None:
        try:
            return await is_legitimate_owner(bot, user_id, chat_id)
        except Exception as e:
            logger.warning(f"[Aduana] is_legitimate_owner falló ({chat_id}/{user_id}): {e}; se aplica la regla mínima.")
    status = await _get_member_status(bot, chat_id, user_id, fresh=True)
    return str(getattr(status, "value", status) or "") == "creator"


async def _message_author_is_owner(bot: Bot, message: Message) -> bool:
    """
    ¿El autor del mensaje es el dueño legítimo? Los mensajes 'en nombre del grupo' (administrador anónimo) o
    de un canal vinculado NO identifican a una persona: cualquier administrador podría enviarlos, así que se
    rechazan para configuración.
    """
    if message.sender_chat is not None:
        return False
    if not message.from_user:
        return False
    return await _is_community_owner(bot, message.chat.id, message.from_user.id)


async def _deny_owner_only_message(message: Message) -> None:
    """
    v37 · Anti-Rogue: quien no es el Dueño no recibe respuesta pública (solo se retira el comando). La única
    excepción es el administrador ANÓNIMO, que puede ser el propio Dueño: se le explica cómo identificarse.
    """
    logger.warning(
        f"⛔ [Aduana] Comando '{(message.text or '')[:40]}' bloqueado en {message.chat.id}: "
        f"autor={getattr(message.from_user, 'id', None)} sender_chat={getattr(message.sender_chat, 'id', None)} no es el Dueño."
    )
    try:
        await message.delete()
    except Exception:
        pass
    if message.sender_chat is None or message.sender_chat.id != message.chat.id:
        return
    try:
        lang = await group_lang(message.chat.id)
        warn = await message.bot.send_message(chat_id=message.chat.id, text=gtx(lang, "anon_admin"), parse_mode="HTML")
        _spawn(auto_delete_msg(warn, await service_ttl(message.chat.id, 10)))
    except Exception:
        pass

def _sync_miniapp(group_id: int, changed, source: str = "group_command") -> None:
    """
    🎙️ v14 · Sincronización bot → Mini App: tras un comando de grupo que cambia ajustes (escudo, antirruido,
    atenuación, tarifa de oradores, Protocolo de Pánico) se emite 'settings_updated' a /ws/radar/{group_id} con
    el estado real guardado. Reutiliza el emisor de la consola privada (misma forma que la API de main.py).
    Import diferido: user_private importa este módulo.
    """
    try:
        from handlers.user_private import emit_settings_updated_bg
    except Exception as ex:
        logger.debug(f"[Radar] Emisor de ajustes no disponible: {ex}")
        return
    emit_settings_updated_bg(group_id, source, changed)


async def _require_owner_message(bot: Bot, message: Message) -> bool:
    if await _message_author_is_owner(bot, message):
        return True
    await _deny_owner_only_message(message)
    return False


# ==========================================
# 🎚️ MATRIZ DE RANGOS — AUTOLOWER SELECTIVO & INMUNIDADES
# ==========================================
SECURITY_MATRIX_IMMUNE_TIERS = frozenset({"architect", "service", "sentinel", "owner", "admin", "whitelisted"})
AUTOLOWER_IMMUNE_TIERS = frozenset({"architect", "service", "sentinel", "owner", "admin", "whitelisted", "vip_mic"})


async def get_privilege_tier(bot: Bot, group_id: int, user_id: int, username: str = "") -> str:
    if is_super_admin(user_id):
        return "architect"

    if user_id in SERVICE_ACCOUNT_IDS:
        return "service"

    if await is_sentinel_account(group_id, user_id, username):
        return "sentinel"

    status = await _get_member_status(bot, group_id, user_id)
    if status == "creator":
        return "owner"
    if status == "administrator":
        return "admin"

    try:
        if await is_whitelisted(user_id):
            return "whitelisted"
    except Exception as e:
        logger.warning(f"No se pudo consultar la whitelist para {user_id}: {e}")

    try:
        if await is_vip_mic_active(user_id, group_id):
            return "vip_mic"
    except Exception as e:
        logger.debug(f"Aviso consultando pase MicVIP para {user_id}: {e}")

    if status is None:
        return "unknown"

    return "standard"


def _tier_is_privileged(tier: str) -> bool:
    return tier in SECURITY_MATRIX_IMMUNE_TIERS


async def _autolower_can_mute(bot: Bot, group_id: int, user_id: int, username: str = "") -> bool:
    tier = await get_privilege_tier(bot, group_id, user_id, username)
    if tier in AUTOLOWER_IMMUNE_TIERS:
        return False
    # 👑 v23 · Rol VIP con prioridad acústica (configurable): tampoco se atenúa.
    perks = await vip_roles.get_active_perks(group_id, user_id)
    return not (perks and perks.get("autolower_immune"))


FLOOD_CACHE = {}
CAPTCHA_SESSIONS = {}
RECENTLY_VERIFIED = {}

# Telegram autoriza escribir por privado a quien envía una solicitud de unión usando
# ChatJoinRequest.user_chat_id durante 5 minutos y SOLO hasta que la solicitud se aprueba o
# rechaza. Se deja un margen para que el aviso de expiración aún llegue dentro de la ventana.
JOIN_REQUEST_DM_WINDOW_SECONDS = 300
JOIN_REQUEST_DM_SAFETY_MARGIN = 20
JOIN_REQUEST_MAX_CAPTCHA_SECONDS = JOIN_REQUEST_DM_WINDOW_SECONDS - JOIN_REQUEST_DM_SAFETY_MARGIN
CAPTCHA_GROUP_NOTICE_TTL = 20
SUPPORT_URL = "https://t.me/m/RGx4ohGTMTk5"


async def auto_delete_msg(message: Optional[Message], delay: int = 15):
    if not message:
        return
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


# ==========================================
# 🌐 v37 · IDIOMA (INGLÉS POR DEFECTO) Y TTL DE LOS MENSAJES DE SERVICIO DEL GRUPO
# ==========================================
# Cada comunidad fija su idioma de servicio (/lang o panel «Language & TTL») y cuántos segundos viven los avisos.
# Sin preferencia: inglés y 20 s. Los textos personalizados del dueño (captcha, advertencias) se respetan tal cual.
SVC_SIGNATURE = "\n\n🛡️ <i>Cloud Media Management</i>"
SVC_PREFS_TTL = 60.0
_SVC_PREFS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}

GROUP_SVC_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "community": "the community",
        "user": "User",
        "captcha_title": "🛑 <b>SECURITY CHECKPOINT</b>",
        "captcha_intro": "Hi {mention}! To protect the community we need a quick verification.",
        "captcha_code": "Tap the button that shows exactly this code:\n👉 <code>{code}</code>",
        "captcha_simple": "Tap the button below to confirm your identity.",
        "captcha_time": "⏱️ <b>Time limit:</b> {seconds}s",
        "captcha_expired_default": "⏱️ The verification time limit has expired.",
        "captcha_expired_dm": "❌ <b>Access denied</b>\n\n{text}",
        "captcha_expired_group": "❌ {mention} did not complete the verification in time.",
        "captcha_ok": "🎉 <b>Access granted!</b>\n\n{mention}, you passed the security checkpoint. You can now take part in <b>{title}</b>.",
        "captcha_fail_default": "❌ Wrong code selected at the security checkpoint.",
        "captcha_fail_dm": "❌ <b>Checkpoint failed</b>\n\n{text}",
        "captcha_fail_group": "❌ <b>Checkpoint failed</b>\n\n{mention}: {text}",
        "captcha_not_yours": "⚠️ This checkpoint is not yours.",
        "captcha_verified_toast": "✅ Identity verified!",
        "captcha_wrong_toast": "❌ Wrong code.",
        "btn_verify": "✅ Verify identity",
        "btn_support": "💬 Support",
        "gate_text": "🚨 <b>Anti-Raid Shield active</b>\n\nAn abnormal join spike was detected. New members stay read-only until they pass the Captcha Pro (deadline: {minutes} min).",
        "btn_gate": "🛡️ Verify me",
        "gate_none": "ℹ️ You have no pending verification in that community.",
        "gate_dm": "🛑 <b>SECURITY CHECKPOINT</b>\n\nCommunity: <b>{title}</b>\nTap the button that shows exactly this code:\n👉 <code>{code}</code>",
        "raid_log": "🚨 <b>Anti-Raid Shield engaged — {title}</b>\n\n• <b>Level:</b> {level}\n• <b>Signals:</b> {signals}\n• <b>Joins in {window}s:</b> {joins} ({new} new accounts)\n• <b>Duration:</b> {minutes} min (extended if the attack continues)\n\nMeasures: mandatory code Captcha Pro, public notices muted and broadcasts paused.",
        "level_critical": "CRITICAL 🔴",
        "level_elevated": "ELEVATED 🟠",
        "sig_join_spike": "join spike",
        "sig_new_accounts": "new accounts",
        "sig_similar_names": "cloned names",
        "owner_only": "⛔ <b>Access denied:</b> this tool is restricted to the community <b>Owner</b>.",
        "owner_only_short": "⛔ Access denied: Owner only.",
        "anon_admin": "⛔ <b>Access denied:</b> anonymous admin messages cannot be verified as the Owner. Disable anonymity and retry.",
        "warn": "⚠️ <b>Security notice ({strikes}/{limit})</b>\n\n{mention}, {reason}",
        "warn_pro": "⚠️ <b>PRO ⭐ security notice ({strikes}/{limit})</b>\n\n{body}",
        "warn_ultra": "⚠️ <b>ULTRA PRO 💎 perimeter notice ({strikes}/{limit})</b>\n\n{body}",
        "warn_body": "{mention}, you received a formal warning ({strikes}/{limit}).\n• <b>Reason:</b> {reason}",
        "sanction_mute": "🔇 <b>Auto-sanction{badge} ({strikes}/{limit})</b>\n\n{mention} was muted in chat and voice after reaching the warning limit.\n• <b>Action:</b> <code>MUTE</code>",
        "sanction_kick": "👢 <b>Auto-sanction{badge} ({strikes}/{limit})</b>\n\n{mention} was kicked after reaching the warning limit.\n• <b>Action:</b> <code>KICK</code>",
        "sanction_ban": "🚫 <b>Permanent ban{badge} ({strikes}/{limit})</b>\n\n{mention} was permanently banned for repeated violations.\n• <b>Action:</b> <code>BAN</code>",
        "flood_kick": "👢 <b>Kick notice:</b> {mention} was temporarily removed for message flooding.",
        "flood_mute": "🔇 <b>Mute notice:</b> {mention} was muted for message flooding.",
        "flood_ban": "🚫 <b>Ban notice:</b> {mention} was permanently banned for repeated flooding.",
        "levelup": "🎉 <b>Level up!</b>\n\nCongratulations {mention}! You reached <b>level {level}</b> with <code>{xp} XP</code>.",
        "welcome_group": "🛡️ <b>The Bunker OS — Perimeter core online</b>\n\nThe security system is now deployed in <b>{title}</b>.\n\n👑 <b>Owner-only control panel:</b> tap below to configure anti-spam, captcha, anti-raid and locks in the bot's private chat.",
        "welcome_channel": "📡 <b>CHANNEL STUDIO CONNECTED!</b>\n\nYou promoted <b>@{bot}</b> as administrator in <b>{title}</b>.\n\n• 🎙️ <b>Live moderation:</b> smart unmute after “Raise Hand”.\n• 💎 <b>VIP memberships:</b> single-use links and automatic removal of expired members.\n• ⭐ <b>Stars tips:</b> direct monetization of your streams.\n\nTap below to open this channel's console in private or in the Mini App.",
        "btn_settings": "⚙️ Configure privately",
        "btn_channel_console": "📡 Channel console",
        "btn_command_center": "🌐 Open Command Center",
        "raid_status": "🚨 <b>Anti-Raid</b>\n\n• Status: {status}\n• Trigger: <b>{min}</b> joins / {window}s (critical: <b>{max}</b>)\n• Penalty: <b>{penalty}</b>\n\nOpen the private panel to change thresholds, penalties and review the raid blacklist.",
        "btn_open_raid": "🚨 Open Anti-Raid panel",
        "on": "🟢 Enabled",
        "off": "🔴 Disabled",
        "lang_pick": "🌐 <b>Language / Idioma</b>\n\nAdmins with settings rights change the group's service language. Members change their personal language for private chats.",
        "lang_set_group": "✅ Service language for this group: English.",
        "lang_set_user": "✅ Your personal language is now English (private chat with the bot).",
        "ghost_free_limit": "⏳ <b>Ghost Purge limit (Free plan):</b> only 1 free scan per day.\nUpgrade to <b>PRO ⭐</b> or <b>ULTRA PRO 💎</b> for unlimited automated scans and purges.",
        "ghost_busy": "⏳ A Ghost Purge is already running in this community. Wait for it to finish.",
        "ghost_start": "🧹 <b>Ghost Purge{sim}:</b> evaluating the environment and starting the hybrid engine...",
        "ghost_sim": " (simulation)",
        "ghost_done": "🧹 <b>Ghost Purge {sim}completed</b>\n• <b>Engine:</b> <code>{engine}</code>\n• 👻 <b>Ghosts detected:</b> <b>{found}</b>\n• 💀 <b>Ghosts purged:</b> <b>{purged}</b>\n• ⚖️ <b>Action:</b> <code>{action}</code>",
        "ghost_sim_done": "(Simulation) ",
        "ghost_error": "⚠️ The Ghost Purge stopped because of an unexpected error.",
        "panic_btn_off": "🟢 Disable Raid Lockdown",
        "panic_dm": "🚨 <b>RAID LOCKDOWN PROTOCOL ENGAGED</b>\n\nCommunity: <b>{title}</b> (<code>{chat_id}</code>)\nActivated by: <code>{by}</code>\n\nAll locks were raised, the Captcha switched to strict mode, Anti-Spam sensitivity went up and the general chat is restricted to administrators.",
        "panic_already": "🛡️ The Raid Lockdown protocol is already <b>active</b> in this community.",
        "panic_on": "🚨 <b>RAID LOCKDOWN PROTOCOL ENGAGED</b>\n\nLocks at maximum, strict Captcha, raised Anti-Spam and general chat restricted to administrators.",
        "panic_off_done": "✅ <b>Raid Lockdown disabled.</b>\n\nThe community perimeter was restored to its pre-alert state.",
        "panic_off_toast": "Perimeter restored ✅",
        "untitled": "Untitled",
        "activated": "ENABLED",
        "deactivated": "DISABLED",
        "screenshield": "🎥 Screen-Share Shield: <b>{state}</b>",
        "noiseshield": "🔇 Noise Shield: <b>{state}</b>",
        "duck_usage": "Usage: /duckvolume <percent 1-100> — e.g. /duckvolume 5",
        "duck_ok": "🎚️ Podcast Mode ducking volume set to <b>{pct}%</b>.",
        "spk_empty": "📭 The speakers queue is empty.",
        "spk_next": "🎙️ <b>Next speaker in the Q&amp;A round:</b> {mention}{tag}",
        "spk_cleared": "🧹 Speakers queue cleared.",
        "spk_price_ok": "⭐ Priority turn price updated to <b>{price} Stars (XTR)</b>.",
        "spk_price_usage": "Usage: /speakers price <stars_amount>",
        "spk_queue_empty_line": "— The queue is empty —",
        "spk_btn": "🎟️ Book a priority turn ({price} ⭐)",
        "spk_card": "🎙️ <b>Q&amp;A queue — {title}</b>\n\n{queue}\n\n💰 Pay <b>{price} Telegram Stars</b> to lock in priority in the next round.",
        "spk_inv_title": "🎙️ Priority turn — Q&A queue",
        "spk_inv_desc": "Lock in your priority in the community's next Q&A round.",
        "spk_inv_label": "Priority turn",
        "spk_open_dm": "⚠️ Open a private chat with the bot first to pay with Stars.",
        "btn_open_dm": "💬 Open private chat",
        "spk_paid_dm": "✅ <b>Turn booked!</b>\n\nYou paid <b>{stars} Stars</b> for priority in the Q&amp;A queue.",
        "spk_paid_group": "🎙️ {mention} booked a priority turn in the Q&amp;A queue (⭐ {stars} XTR).",
        "rank_card": "🎖️ <b>Rank &amp; reputation — {title}</b>\n\n👤 <b>Member:</b> {mention}\n⭐ <b>Leaderboard position:</b> <code>#{pos}</code>\n🏆 <b>Level:</b> <b>{level}</b>\n⚡ <b>Total experience:</b> <code>{xp} XP</code>\n\n<b>Progress to next level:</b>\n<code>[{bar}]</code> {pct}%\n<i>({current} / {diff} XP to level {next})</i>",
        "top_empty": "📭 <b>Not enough activity recorded on the reputation board yet.</b>",
        "top_line": "{medal} <b>{name}</b> — <code>Level {level}</code> ({xp} XP)",
        "top_card": "🏆 <b>Community Hall of Fame (Top 10)</b>\n📍 <b>Community:</b> <code>{title}</code>\n\n{lines}\n\n<i>Chat legitimately to earn XP and climb the ranks!</i>",
        "btn_top": "🌐 View full analytics",
        "heatmap": "📊 <b>Traffic &amp; peak hours audit (Heatmap)</b>\n📍 <b>Community:</b> <code>{title}</code>\n\n• 📈 <b>Indexed messages:</b> <code>{total}</code>\n• ⚡ <b>Busiest day:</b> <b>{day}</b>\n• ⏰ <b>Busiest time slot:</b> <code>{h1:02d}:00 - {h2:02d}:00</code>\n• 🔥 <b>Peak traffic record:</b> <code>{peak} messages</code>\n\n💡 <i>Tip: schedule video chats and announcements during peak days and hours to maximise the audience.</i>",
        "heatmap_default_day": "Monday",
        "btn_heatmap": "📊 View 24x7 heatmap",
        "ghost_progress": "🧹 <b>Ghost Purge (Bot API registry) in progress...</b>\n\n• Checked: <b>{done}/{total}</b>\n• Ghosts found: <b>{found}</b>",
        "engine_mtproto": "MTProto Sentinel 💎",
        "engine_botapi": "Bot API registry (local roster) 🤖",
        "heatmap_unknown_day": "Day",
        "official_channel": "Official Channel",
        "community_title": "Community",
    },
    "es": {
        "community": "la comunidad",
        "user": "Usuario",
        "captcha_title": "🛑 <b>ADUANA DE SEGURIDAD</b>",
        "captcha_intro": "¡Hola, {mention}! Para proteger la comunidad necesitamos una breve verificación.",
        "captcha_code": "Selecciona el botón que contiene exactamente este código:\n👉 <code>{code}</code>",
        "captcha_simple": "Pulsa el botón inferior para confirmar tu identidad.",
        "captcha_time": "⏱️ <b>Tiempo límite:</b> {seconds}s",
        "captcha_expired_default": "⏱️ El tiempo límite para verificar tu identidad ha expirado.",
        "captcha_expired_dm": "❌ <b>Acceso denegado</b>\n\n{text}",
        "captcha_expired_group": "❌ {mention} no completó la verificación a tiempo.",
        "captcha_ok": "🎉 <b>¡Acceso concedido!</b>\n\n{mention}, completaste la aduana de seguridad. Ya puedes participar en <b>{title}</b>.",
        "captcha_fail_default": "❌ Código incorrecto ingresado en la aduana de seguridad.",
        "captcha_fail_dm": "❌ <b>Verificación fallida</b>\n\n{text}",
        "captcha_fail_group": "❌ <b>Verificación fallida</b>\n\n{mention}: {text}",
        "captcha_not_yours": "⚠️ Este desafío no pertenece a tu perfil.",
        "captcha_verified_toast": "✅ ¡Identidad verificada con éxito!",
        "captcha_wrong_toast": "❌ Código incorrecto.",
        "btn_verify": "✅ Verificar identidad",
        "btn_support": "💬 Soporte",
        "gate_text": "🚨 <b>Escudo Anti-Raid activo</b>\n\nSe detectó un pico anómalo de ingresos. Los nuevos miembros permanecen en modo lectura hasta verificarse con el Captcha Pro (plazo: {minutes} min).",
        "btn_gate": "🛡️ Verificarme",
        "gate_none": "ℹ️ No tienes ninguna verificación pendiente en esa comunidad.",
        "gate_dm": "🛑 <b>ADUANA DE SEGURIDAD</b>\n\nComunidad: <b>{title}</b>\nSelecciona el botón que contiene exactamente este código:\n👉 <code>{code}</code>",
        "raid_log": "🚨 <b>Escudo Anti-Raid activado — {title}</b>\n\n• <b>Nivel:</b> {level}\n• <b>Señales:</b> {signals}\n• <b>Ingresos en {window}s:</b> {joins} ({new} cuentas nuevas)\n• <b>Duración:</b> {minutes} min (se prolonga si el ataque continúa)\n\nMedidas: Captcha Pro obligatorio por código, avisos públicos silenciados y difusiones pausadas.",
        "level_critical": "CRÍTICO 🔴",
        "level_elevated": "ELEVADO 🟠",
        "sig_join_spike": "pico de ingresos",
        "sig_new_accounts": "cuentas nuevas",
        "sig_similar_names": "nombres clonados",
        "owner_only": "⛔ <b>Acceso denegado:</b> esta herramienta está reservada al <b>Dueño</b> de la comunidad.",
        "owner_only_short": "⛔ Acceso denegado: exclusivo del Dueño.",
        "anon_admin": "⛔ <b>Acceso denegado:</b> los mensajes de administrador anónimo no permiten verificar al Dueño. Desactiva el anonimato y repite el comando.",
        "warn": "⚠️ <b>Aviso de seguridad ({strikes}/{limit})</b>\n\n{mention}, {reason}",
        "warn_pro": "⚠️ <b>Aviso de seguridad PRO ⭐ ({strikes}/{limit})</b>\n\n{body}",
        "warn_ultra": "⚠️ <b>Aviso perimetral ULTRA PRO 💎 ({strikes}/{limit})</b>\n\n{body}",
        "warn_body": "{mention}, has recibido una falta formal ({strikes}/{limit}).\n• <b>Motivo:</b> {reason}",
        "sanction_mute": "🔇 <b>Sanción automática{badge} ({strikes}/{limit})</b>\n\n{mention} fue silenciado en el chat y el videochat por acumulación de faltas.\n• <b>Medida:</b> <code>MUTE</code>",
        "sanction_kick": "👢 <b>Sanción automática{badge} ({strikes}/{limit})</b>\n\n{mention} fue expulsado temporalmente por infringir las reglas.\n• <b>Medida:</b> <code>KICK</code>",
        "sanction_ban": "🚫 <b>Bloqueo definitivo{badge} ({strikes}/{limit})</b>\n\n{mention} fue bloqueado permanentemente por faltas reiteradas.\n• <b>Medida:</b> <code>BAN</code>",
        "flood_kick": "👢 <b>Aviso de expulsión:</b> {mention} fue expulsado temporalmente por saturación de mensajes.",
        "flood_mute": "🔇 <b>Aviso de silencio:</b> {mention} fue silenciado por saturación de mensajes.",
        "flood_ban": "🚫 <b>Bloqueo definitivo:</b> {mention} fue baneado por flood reiterado.",
        "levelup": "🎉 <b>¡Ascenso de rango!</b>\n\n¡Felicitaciones {mention}! Alcanzaste el <b>nivel {level}</b> con <code>{xp} XP</code>.",
        "welcome_group": "🛡️ <b>The Bunker OS — Núcleo perimetral activado</b>\n\nEl sistema de seguridad fue desplegado en <b>{title}</b>.\n\n👑 <b>Panel exclusivo del Dueño:</b> pulsa abajo para configurar anti-spam, captcha, anti-raid y cerraduras en el chat privado del bot.",
        "welcome_channel": "📡 <b>¡ESTUDIO DE CANAL CONECTADO!</b>\n\nPromoviste a <b>@{bot}</b> como administrador en <b>{title}</b>.\n\n• 🎙️ <b>Moderación de directos:</b> desmuteo inteligente tras «Levantar la mano».\n• 💎 <b>Membresías VIP:</b> enlaces de un solo uso y expulsión automática de morosos.\n• ⭐ <b>Propinas Stars:</b> monetización directa de tus transmisiones.\n\nPulsa abajo para abrir la consola de este canal en privado o en la Mini App.",
        "btn_settings": "⚙️ Configurar en privado",
        "btn_channel_console": "📡 Consola del canal",
        "btn_command_center": "🌐 Abrir Command Center",
        "raid_status": "🚨 <b>Anti-Raid</b>\n\n• Estado: {status}\n• Disparo: <b>{min}</b> ingresos / {window}s (crítico: <b>{max}</b>)\n• Penalización: <b>{penalty}</b>\n\nAbre el panel privado para cambiar umbrales, penalizaciones y revisar la lista negra de raid.",
        "btn_open_raid": "🚨 Abrir panel Anti-Raid",
        "on": "🟢 Activado",
        "off": "🔴 Desactivado",
        "lang_pick": "🌐 <b>Language / Idioma</b>\n\nLos administradores con permisos de configuración cambian el idioma de servicio del grupo. Los miembros cambian su idioma personal en el chat privado.",
        "lang_set_group": "✅ Idioma de servicio del grupo: Español.",
        "lang_set_user": "✅ Tu idioma personal ahora es Español (chat privado con el bot).",
        "ghost_free_limit": "⏳ <b>Límite de Ghost Purge (Plan Free):</b> solo se permite 1 escaneo diario gratuito.\nMejora a <b>PRO ⭐</b> o <b>ULTRA PRO 💎</b> para escaneos y purgas automatizadas ilimitadas.",
        "ghost_busy": "⏳ Ya hay un Ghost Purge en curso en esta comunidad. Espera a que termine.",
        "ghost_start": "🧹 <b>Ghost Purge{sim}:</b> evaluando el entorno y activando el motor híbrido...",
        "ghost_sim": " (simulación)",
        "ghost_done": "🧹 <b>Ghost Purge {sim}completado</b>\n• <b>Motor utilizado:</b> <code>{engine}</code>\n• 👻 <b>Fantasmas detectados:</b> <b>{found}</b>\n• 💀 <b>Fantasmas depurados:</b> <b>{purged}</b>\n• ⚖️ <b>Acción aplicada:</b> <code>{action}</code>",
        "ghost_sim_done": "(Simulación) ",
        "ghost_error": "⚠️ El Ghost Purge se interrumpió por un error inesperado.",
        "panic_btn_off": "🟢 Desactivar Raid Lockdown",
        "panic_dm": "🚨 <b>PROTOCOLO RAID LOCKDOWN ACTIVADO</b>\n\nComunidad: <b>{title}</b> (<code>{chat_id}</code>)\nActivado por: <code>{by}</code>\n\nSe elevaron todas las cerraduras, el Captcha entró en modo estricto, el Anti-Spam subió su sensibilidad y el chat general quedó restringido a administradores.",
        "panic_already": "🛡️ El Protocolo Raid Lockdown ya está <b>activo</b> en esta comunidad.",
        "panic_on": "🚨 <b>PROTOCOLO RAID LOCKDOWN ACTIVADO</b>\n\nCerraduras al máximo, Captcha estricto, Anti-Spam elevado y chat general restringido a administradores.",
        "panic_off_done": "✅ <b>Protocolo Raid Lockdown desactivado.</b>\n\nEl perímetro de la comunidad fue restaurado a su estado previo a la alerta.",
        "panic_off_toast": "Perímetro restaurado ✅",
        "untitled": "Sin título",
        "activated": "ACTIVADO",
        "deactivated": "DESACTIVADO",
        "screenshield": "🎥 Escudo Antinota: <b>{state}</b>",
        "noiseshield": "🔇 Escudo Antirruido: <b>{state}</b>",
        "duck_usage": "Uso: /duckvolume <porcentaje 1-100> — Ej: /duckvolume 5",
        "duck_ok": "🎚️ Volumen de atenuación del Modo Podcast ajustado a <b>{pct}%</b>.",
        "spk_empty": "📭 La cola de oradores está vacía.",
        "spk_next": "🎙️ <b>Siguiente orador en la ronda de preguntas:</b> {mention}{tag}",
        "spk_cleared": "🧹 Cola de oradores vaciada.",
        "spk_price_ok": "⭐ Tarifa de turno prioritario actualizada a <b>{price} Stars (XTR)</b>.",
        "spk_price_usage": "Uso: /speakers price <cantidad_de_stars>",
        "spk_queue_empty_line": "— La cola está vacía —",
        "spk_btn": "🎟️ Asegurar turno prioritario ({price} ⭐)",
        "spk_card": "🎙️ <b>Cola de preguntas (AMA) — {title}</b>\n\n{queue}\n\n💰 Paga <b>{price} Telegram Stars</b> y asegura tu prioridad en la próxima ronda de preguntas.",
        "spk_inv_title": "🎙️ Turno prioritario — Cola de preguntas",
        "spk_inv_desc": "Asegura tu prioridad en la próxima ronda de preguntas (AMA) de la comunidad.",
        "spk_inv_label": "Turno prioritario",
        "spk_open_dm": "⚠️ Abre un privado con el bot primero para poder pagar con Stars.",
        "btn_open_dm": "💬 Abrir privado",
        "spk_paid_dm": "✅ <b>¡Turno asegurado!</b>\n\nPagaste <b>{stars} Stars</b> por prioridad en la cola de preguntas.",
        "spk_paid_group": "🎙️ {mention} aseguró un turno prioritario en la cola de preguntas (⭐ {stars} XTR).",
        "rank_card": "🎖️ <b>Registro de rango y reputación — {title}</b>\n\n👤 <b>Miembro:</b> {mention}\n⭐ <b>Posición en el cuadro:</b> <code>#{pos}</code>\n🏆 <b>Nivel:</b> <b>{level}</b>\n⚡ <b>Experiencia total:</b> <code>{xp} XP</code>\n\n<b>Progreso al siguiente nivel:</b>\n<code>[{bar}]</code> {pct}%\n<i>({current} / {diff} XP para el nivel {next})</i>",
        "top_empty": "📭 <b>Aún no hay suficiente actividad registrada en el cuadro de reputación.</b>",
        "top_line": "{medal} <b>{name}</b> — <code>Nivel {level}</code> ({xp} XP)",
        "top_card": "🏆 <b>Cuadro de honor comunitario (Top 10)</b>\n📍 <b>Comunidad:</b> <code>{title}</code>\n\n{lines}\n\n<i>¡Chatea legítimamente para acumular XP y subir de rango!</i>",
        "btn_top": "🌐 Ver analítica completa",
        "heatmap": "📊 <b>Auditoría de tráfico y horas pico (Heatmap)</b>\n📍 <b>Comunidad:</b> <code>{title}</code>\n\n• 📈 <b>Mensajes indexados:</b> <code>{total}</code>\n• ⚡ <b>Día de mayor actividad:</b> <b>{day}</b>\n• ⏰ <b>Franja horaria más concurrida:</b> <code>{h1:02d}:00 - {h2:02d}:00</code>\n• 🔥 <b>Récord de tráfico en pico:</b> <code>{peak} mensajes</code>\n\n💡 <i>Recomendación: programa videollamadas y anuncios en los días y horas pico para maximizar la audiencia.</i>",
        "heatmap_default_day": "Lunes",
        "btn_heatmap": "📊 Ver mapa de calor 24x7",
        "ghost_progress": "🧹 <b>Ghost Purge (padrón Bot API) en curso...</b>\n\n• Verificados: <b>{done}/{total}</b>\n• Fantasmas encontrados: <b>{found}</b>",
        "engine_mtproto": "Centinela MTProto 💎",
        "engine_botapi": "Padrón Bot API (registro local) 🤖",
        "heatmap_unknown_day": "Día",
        "official_channel": "Canal Oficial",
        "community_title": "Comunidad",
    },
}


def gtx(lang: str, key: str, signed: bool = True, **fmt: Any) -> str:
    """Texto de servicio localizado (inglés de respaldo) con firma perimetral opcional."""
    bundle = GROUP_SVC_TEXTS.get(lang) or GROUP_SVC_TEXTS["en"]
    template = bundle.get(key) or GROUP_SVC_TEXTS["en"].get(key, key)
    text = template.format(**fmt) if fmt else template
    return text + SVC_SIGNATURE if signed else text


def reason_text(lang: str, reason: str) -> str:
    """Motivo de la escalera de advertencias en el idioma del grupo (_REASON_TEXT guarda pares ES/EN)."""
    pair = _REASON_TEXT.get(reason)
    if not pair:
        return reason
    return pair[0] if lang == "es" else pair[1]


async def service_prefs(chat_id: int) -> dict[str, Any]:
    """{"lang", "ttl"} de la comunidad con caché de 60 s (invalidate_service_prefs la vacía al cambiarla)."""
    now = time.monotonic()
    cached = _SVC_PREFS_CACHE.get(chat_id)
    if cached and now - cached[0] < SVC_PREFS_TTL:
        return cached[1]
    try:
        prefs = await get_service_prefs(chat_id)
    except Exception as e:
        logger.debug(f"Preferencias de servicio no disponibles para {chat_id}: {e}")
        prefs = {"lang": DEFAULT_SERVICE_LANG, "ttl": DEFAULT_SERVICE_TTL}
    if len(_SVC_PREFS_CACHE) > 5000:
        _SVC_PREFS_CACHE.clear()
    _SVC_PREFS_CACHE[chat_id] = (now, prefs)
    return prefs


async def group_lang(chat_id: int) -> str:
    lang = str((await service_prefs(chat_id)).get("lang") or DEFAULT_SERVICE_LANG)
    return lang if lang in GROUP_SVC_TEXTS else DEFAULT_SERVICE_LANG


def group_lang_cached(chat_id: int) -> str:
    """Versión síncrona para módulos que no pueden await (vc_manager._msg_lang). Refresca en segundo plano."""
    cached = _SVC_PREFS_CACHE.get(chat_id)
    if cached is None or time.monotonic() - cached[0] >= SVC_PREFS_TTL:
        with contextlib.suppress(RuntimeError):
            _spawn(service_prefs(chat_id))
    lang = str(cached[1].get("lang")) if cached else DEFAULT_SERVICE_LANG
    return lang if lang in GROUP_SVC_TEXTS else DEFAULT_SERVICE_LANG


async def service_ttl(chat_id: int, fallback: int = DEFAULT_SERVICE_TTL) -> int:
    try:
        return int((await service_prefs(chat_id)).get("ttl") or fallback)
    except Exception:
        return fallback


def invalidate_service_prefs(chat_id: int) -> None:
    _SVC_PREFS_CACHE.pop(int(chat_id), None)


async def is_sentinel_account(group_id: int, user_id: int, username: str) -> bool:
    clean_username = (username or "").lower()
    if clean_username == "alphacentinel":
        return True

    sentinel_info = active_sentinels.get(group_id)
    if sentinel_info and sentinel_info.get("user_id") == user_id:
        return True

    try:
        session_row = await get_session_by_group(group_id)
    except Exception as e:
        logger.warning(f"No se pudo consultar la sesión de Centinela de {group_id}: {e}")
        return False

    if session_row and session_row[0] == user_id:
        return True

    return False


ARCHITECT_FALLBACK_ID = 8269470905


async def _resolve_chat_creator_id(bot: Bot, chat_id: int) -> Optional[int]:
    """ID del creador real del chat según Telegram (None si no se puede determinar)."""
    try:
        admins = await asyncio.wait_for(bot.get_chat_administrators(chat_id), timeout=6)
    except Exception as e:
        logger.debug(f"No se pudo listar administradores de {chat_id}: {e}")
        return None
    for adm in admins:
        status = str(getattr(adm.status, "value", adm.status) or "")
        if status == "creator" and adm.user and not adm.user.is_bot:
            return adm.user.id
    return None


# ==========================================
# 📡 OBSERVADOR UNIVERSAL DE MEMBRESÍA
# ==========================================
@router.my_chat_member()
async def bot_added_as_admin(event: ChatMemberUpdated, bot: Bot):
    if event.new_chat_member.status not in ["administrator", "creator"]:
        return
    if event.old_chat_member.status in ["administrator", "creator"]:
        return

    chat = event.chat
    if chat.type not in {"group", "supergroup", "channel"}:
        return

    is_channel = (chat.type == "channel")
    chat_type_str = "channel" if is_channel else "supergroup"
    group_id = chat.id
    group_name = chat.title or ("Official Channel" if is_channel else "Community")   # v38 · inglés por defecto
    user = event.from_user

    # 👑 Regla de Oro: el registro de dueños SOLO guarda al CREADOR real del chat. Antes se registraba a quien
    # añadió/promovió al bot aunque fuera un administrador común, lo que le abría las consolas.
    creator_id = await _resolve_chat_creator_id(bot, group_id)
    promoter_is_creator = bool(user) and creator_id is not None and user.id == creator_id
    owner_to_register = creator_id if creator_id is not None else ARCHITECT_FALLBACK_ID
    if creator_id is None:
        logger.warning(f"⚠️ [Aduana] No se pudo determinar el creador de {group_id}; el chat queda bajo custodia del Arquitecto.")
    elif user and not promoter_is_creator:
        logger.info(f"👑 [Aduana] {user.id} añadió el bot a {group_id} pero no es el creador; se registra al creador {creator_id}.")

    await approve_group(group_id, tier="free")
    await register_user_group(owner_to_register, group_id, group_name, chat_type=chat_type_str)
    # 🛡️ v37 · El Anti-Rogue deja de tratar el chat como ajeno al instante (sin esperar a su caché de 5 min).
    try:
        from middlewares.anti_spam import forget_chat_approval
        forget_chat_approval(group_id)
    except Exception:
        pass

    bot_info = await bot.get_me()
    lang = await group_lang(group_id)

    if is_channel:
        # La consola solo se ofrece por privado al creador (un administrador común sería rechazado igualmente).
        if user and promoter_is_creator:
            ch_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=gtx(lang, "btn_command_center", signed=False),
                                      web_app=WebAppInfo(url=f"{WEBAPP_URL}?channel_id={group_id}"))],
                [InlineKeyboardButton(text=gtx(lang, "btn_channel_console", signed=False),
                                      url=f"https://t.me/{bot_info.username}?start=gset_{group_id}")]
            ])
            channel_welcome_text = gtx(lang, "welcome_channel", bot=bot_info.username, title=html.escape(group_name))
            try:
                await bot.send_message(chat_id=user.id, text=channel_welcome_text, reply_markup=ch_kb, parse_mode="HTML")
            except Exception as ex:
                logger.warning(f"Aviso al enviar bienvenida privada de canal al usuario {user.id}: {ex}")
    else:
        # En grupos se utiliza URL directa para evitar BUTTON_TYPE_INVALID
        group_welcome_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=gtx(lang, "btn_settings", signed=False),
                                  url=f"https://t.me/{bot_info.username}?start=gset_{group_id}")],
            [InlineKeyboardButton(text="🌐 Command Center", url=f"{WEBAPP_URL}?chat_id={group_id}")]
        ])
        group_welcome_text = gtx(lang, "welcome_group", title=html.escape(group_name))

        try:
            await bot.send_message(
                chat_id=group_id,
                text=group_welcome_text,
                reply_markup=group_welcome_kb,
                parse_mode="HTML"
            )
        except Exception as ex:
            logger.warning(f"Aviso al enviar bienvenida al grupo {group_id}: {ex}")

# ==========================================
# 🤖 ADUANA DE SEGURIDAD (CAPTCHA PRO BILINGÜE)
# ==========================================
def generate_captcha_keyboard(group_id: int, user_id: int, correct_code: str) -> InlineKeyboardMarkup:
    options = [correct_code]
    while len(options) < 4:
        fake_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        if fake_code not in options:
            options.append(fake_code)
    
    random.shuffle(options)
    
    kb = []
    row = []
    for code in options:
        cb_data = f"cap_ver_{group_id}_{user_id}_{1 if code == correct_code else 0}"
        row.append(InlineKeyboardButton(text=code, callback_data=cb_data))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
        
    return InlineKeyboardMarkup(inline_keyboard=kb)


def generate_simple_keyboard(group_id: int, user_id: int, lang: str = DEFAULT_SERVICE_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "btn_verify", signed=False), callback_data=f"cap_simple_{group_id}_{user_id}")]
    ])


def _support_keyboard(lang: str = DEFAULT_SERVICE_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "btn_support", signed=False), url=SUPPORT_URL)]
    ])


def _join_request_dm_open(session_data: dict) -> bool:
    """True si la ventana legal de DM de la solicitud de unión sigue abierta."""
    if not session_data.get("is_join_req") or not session_data.get("dm_chat_id"):
        return False
    return time.monotonic() < float(session_data.get("dm_deadline", 0.0))


async def _send_join_request_dm(bot: Bot, session_data: dict, text: str,
                                reply_markup: Optional[InlineKeyboardMarkup] = None) -> bool:
    """
    Único punto autorizado para escribir por privado durante la aduana: exige solicitud de
    unión pendiente y ventana de 5 minutos abierta. Debe llamarse ANTES de aprobar o rechazar.
    """
    if not _join_request_dm_open(session_data):
        return False
    try:
        await bot.send_message(
            chat_id=session_data["dm_chat_id"], text=text,
            reply_markup=reply_markup, parse_mode="HTML"
        )
        return True
    except Exception as e:
        logger.debug(f"Aviso DM de solicitud de unión ({session_data.get('dm_chat_id')}): {e}")
        return False


async def _post_group_notice(bot: Bot, group_id: int, text: str,
                             reply_markup: Optional[InlineKeyboardMarkup] = None,
                             ttl: int = CAPTCHA_GROUP_NOTICE_TTL, essential: bool = False) -> None:
    # Anti-reportes: presupuesto de avisos por minuto y silencio total de lo no esencial bajo escudo.
    if not raid_shield.notice_allowed(group_id, essential=essential):
        return
    ttl = await service_ttl(group_id, ttl)   # v37 · el administrador fija el TTL de los avisos
    try:
        notice = await bot.send_message(chat_id=group_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
        _spawn(auto_delete_msg(notice, ttl))
    except Exception as e:
        logger.debug(f"Aviso publicando nota de aduana en {group_id}: {e}")


async def _apply_captcha_sanction(bot: Bot, group_id: int, user_id: int, cfg: dict, is_join_req: bool,
                                  session_data: Optional[dict] = None) -> None:
    """Rechaza la solicitud (ingreso por solicitud) o sanciona al miembro (ingreso directo)."""
    session_data = session_data or {}
    source = session_data.get("source") or "captcha"
    is_bot = bool(session_data.get("is_bot"))
    # Captcha Pro estricto (cuenta nueva, bot o escudo activo): expulsión, nunca silencio.
    action = session_data.get("action_override") or cfg.get("action")
    if is_join_req:
        # El usuario aún no es miembro: basta con rechazar; restringir o banear fallaría o
        # le impediría volver a solicitar acceso legítimamente.
        try:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
            track_security(group_id, user_id, source, "decline", is_bot)
        except Exception as e:
            logger.debug(f"Aviso rechazando solicitud de {user_id} en {group_id}: {e}")
        return
    try:
        if action == "mute":
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            track_security(group_id, user_id, source, "mute", is_bot)
        else:
            await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
            await registry_forget(group_id, user_id)
            track_security(group_id, user_id, source, "kick", is_bot)
    except Exception as e:
        logger.error(f"Error aplicando sanción de aduana a {user_id} en {group_id}: {e}")


async def captcha_timeout_task(bot: Bot, group_id: int, user_id: int, timeout: int):
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    session_key = (group_id, user_id)
    if session_key not in CAPTCHA_SESSIONS:
        return
    session_data = CAPTCHA_SESSIONS.pop(session_key, {})
    cfg = await get_captcha_config(group_id)
    is_join_req = bool(session_data.get("is_join_req"))

    msg_id = session_data.get("msg_id")
    if msg_id and session_data.get("dm_chat_id"):
        try:
            await bot.delete_message(chat_id=session_data["dm_chat_id"], message_id=msg_id)
        except Exception:
            pass

    chat_msg_id = session_data.get("group_msg_id")
    if chat_msg_id:
        try:
            await bot.delete_message(chat_id=group_id, message_id=chat_msg_id)
        except Exception:
            pass

    lang = session_data.get("lang") or await group_lang(group_id)
    custom_msg = cfg["text"] if cfg["text"] else gtx(lang, "captcha_expired_default", signed=False)

    if is_join_req:
        # Aviso privado dentro de la ventana legal y ANTES de rechazar (el rechazo la cierra).
        await _send_join_request_dm(bot, session_data, gtx(lang, "captcha_expired_dm", text=custom_msg),
                                    reply_markup=_support_keyboard(lang))
    else:
        # Ingreso directo: jamás se escribe por privado. Aviso efímero en el grupo.
        await _post_group_notice(
            bot, group_id,
            gtx(lang, "captcha_expired_group", mention=f"<a href='tg://user?id={user_id}'>{gtx(lang, 'user', signed=False)}</a>"),
            reply_markup=_support_keyboard(lang)
        )

    await _apply_captcha_sanction(bot, group_id, user_id, cfg, is_join_req, session_data)


# ==========================================
# 🚨 v9 · PUERTA ANTI-RAID (un único mensaje en lugar de N desafíos)
# ==========================================
_BOT_USERNAME_CACHE: dict = {}


async def _bot_username(bot: Bot) -> str:
    cached = _BOT_USERNAME_CACHE.get(bot.id)
    if cached:
        return cached
    me = await bot.get_me()
    _BOT_USERNAME_CACHE[bot.id] = me.username or ""
    return _BOT_USERNAME_CACHE[bot.id]


async def _ensure_raid_gate(bot: Bot, group_id: int) -> None:
    """
    Bajo escudo, los ingresos directos no reciben un desafío público cada uno: quedan restringidos en
    silencio y se publica UNA sola puerta con enlace al bot. El usuario inicia el chat privado (no es un
    DM no solicitado) y allí resuelve el Captcha Pro por código.
    """
    async with raid_shield.gate_lock(group_id):
        if raid_shield.gate_message(group_id):
            return
        username = await _bot_username(bot)
        if not username:
            return
        minutes = max(1, raid_shield.RAID_GATE_SECONDS // 60)
        lang = await group_lang(group_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=gtx(lang, "btn_gate", signed=False), url=f"https://t.me/{username}?start=raidv_{group_id}")
        ]])
        try:
            gate = await bot.send_message(
                chat_id=group_id,
                text=gtx(lang, "gate_text", minutes=minutes),
                reply_markup=kb,
                parse_mode="HTML",
            )
            raid_shield.remember_gate_message(group_id, gate.message_id)
            _spawn(auto_delete_msg(gate, max(60, raid_shield.shield_remaining(group_id))))
        except Exception as e:
            logger.warning(f"Aduana: no se pudo publicar la puerta anti-raid en {group_id}: {e}")


async def _send_raid_shield_alert(bot: Bot, group_id: int, verdict: dict) -> None:
    """Alerta UNA vez por activación al canal de registro (nunca por privado) y al radar de la Mini App."""
    publish_radar_event(group_id, "raid_shield", {
        "active": True, "level": verdict.get("level"), "joins": verdict.get("joins"),
        "new": verdict.get("new_joins"), "reasons": verdict.get("reasons"),
        "until": verdict.get("active_until"),
    })
    try:
        log_id = await asyncio.to_thread(_db_module._get_setting, group_id, "log_channel_id", None)
    except Exception:
        log_id = None
    if not log_id:
        return
    try:
        title = (await bot.get_chat(group_id)).title or str(group_id)
    except Exception:
        title = str(group_id)
    lang = await group_lang(group_id)
    rs = verdict.get("reasons") or []
    level = int(verdict.get("level") or 1)
    minutes = max(1, raid_shield.shield_remaining(group_id) // 60)
    signals = ", ".join(gtx(lang, f"sig_{r}", signed=False) if f"sig_{r}" in GROUP_SVC_TEXTS["en"] else str(r) for r in rs) or "—"
    text = gtx(
        lang, "raid_log", title=html.escape(title),
        level=gtx(lang, "level_critical" if level >= 2 else "level_elevated", signed=False),
        signals=html.escape(signals),
        window=int(verdict.get("window") or raid_shield.RAID_WINDOW_SECONDS),   # v37 · ventana configurada
        joins=int(verdict.get("joins") or 0), new=int(verdict.get("new_joins") or 0), minutes=minutes,
    )
    try:
        await bot.send_message(chat_id=int(log_id) if str(log_id).lstrip("-").isdigit() else log_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.debug(f"Aviso enviando alerta anti-raid al canal de registro de {group_id}: {e}")


# ==========================================
# 🚨 v37 · ANTI-RAID CONFIGURABLE (Free / PRO / ULTRA PRO)
# ==========================================
# Los umbrales (mínimo, máximo/crítico, ventana, solicitudes de unión, duración del escudo) los fija el dueño o un
# administrador con «Añadir administradores» en raid_config. raid_shield.register_join los aplica en su propio
# detector (sin contadores paralelos) y raid_shield.lift_shield levanta el escudo de verdad (caché + SQLite).
RAID_CFG_TTL = 30.0
_RAID_CFG_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_RAID_CFG_FALLBACK: dict[str, Any] = {
    "enabled": 1, "join_threshold": 8, "critical_threshold": 20, "window_seconds": 60, "request_threshold": 10,
    "new_account_only": 1, "penalty": "captcha", "penalty_minutes": 60, "shield_minutes": 15,
}


async def raid_cfg(group_id: int) -> dict[str, Any]:
    now = time.monotonic()
    cached = _RAID_CFG_CACHE.get(group_id)
    if cached and now - cached[0] < RAID_CFG_TTL:
        return cached[1]
    try:
        cfg = await get_raid_config(group_id)
    except Exception as e:
        logger.warning(f"[Anti-Raid] Configuración no disponible para {group_id}: {e}")
        return cached[1] if cached else dict(_RAID_CFG_FALLBACK)
    if len(_RAID_CFG_CACHE) > 5000:
        _RAID_CFG_CACHE.clear()
    _RAID_CFG_CACHE[group_id] = (now, cfg)
    return cfg


def invalidate_raid_cfg(group_id: int) -> None:
    """Llamar tras cambiar la configuración desde el bot o la Mini App."""
    _RAID_CFG_CACHE.pop(int(group_id), None)


def _raid_isolated(group_id: int) -> bool:
    return bool(raid_shield.is_isolated(group_id))


async def raid_force_clear(group_id: int) -> None:
    """Levanta el escudo de verdad: caché en memoria + mensaje-puerta + ventanas + raid_shield_state."""
    await raid_shield.lift_shield(group_id)


async def _register_join_and_alert(bot: Bot, group_id: int, user_id: int, full_name: str, is_bot: bool,
                                   kind: str = "join") -> dict:
    """
    kind = 'join' (ingreso directo) | 'request' (solicitud de unión / aprobación manual).
    Anti-Raid desactivado por el dueño → no se cuenta ni se aísla nada. Una alerta por activación.
    """
    cfg = await raid_cfg(group_id)
    if not cfg.get("enabled", 1):
        return {"isolated": False, "triggered": False, "is_new": False, "disabled": True}
    try:
        verdict = await raid_shield.register_join(group_id, user_id, full_name, is_bot=is_bot, kind=kind, config=cfg)
    except Exception as e:
        logger.error(f"[RaidShield] Error evaluando el ingreso de {user_id} en {group_id}: {e}")
        return {"isolated": raid_shield.is_isolated(group_id), "triggered": False, "is_new": False}
    if verdict.get("triggered"):
        _spawn(_send_raid_shield_alert(bot, group_id, verdict))
        _spawn(_quarantine_recent_joiners(bot, group_id, exclude_user_id=user_id))
    return verdict


async def _raid_should_penalize(group_id: int, verdict: dict) -> bool:
    cfg = await raid_cfg(group_id)
    if not cfg.get("enabled", 1) or not verdict.get("isolated"):
        return False
    return not cfg.get("new_account_only", 1) or bool(verdict.get("is_new"))


async def _apply_raid_penalty(bot: Bot, group_id: int, user, *, is_join_req: bool) -> bool:
    """
    Aplica la penalización configurada a una cuenta atrapada en el raid y la anota en la lista negra de raid.
    Devuelve True si la penalización sustituye al captcha (mute / kick / ban). Con 'captcha' se registra el
    caso y devuelve False para que siga el Captcha Pro estricto habitual.
    """
    cfg = await raid_cfg(group_id)
    penalty = str(cfg.get("penalty") or "captcha")
    minutes = int(cfg.get("penalty_minutes") or 60)
    until = int(time.time()) + max(60, minutes * 60)
    common: dict[str, Any] = {"full_name": getattr(user, "full_name", None), "username": getattr(user, "username", None),
                              "source": "request" if is_join_req else "join"}
    if penalty == "captcha":
        with contextlib.suppress(Exception):
            await raid_blacklist_add(group_id, user.id, "captcha", **common)
        return False
    try:
        if is_join_req:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user.id)
            if penalty == "ban":
                await bot.ban_chat_member(chat_id=group_id, user_id=user.id, until_date=until)
            action = "decline"
        elif penalty == "mute":
            await bot.restrict_chat_member(chat_id=group_id, user_id=user.id,
                                           permissions=ChatPermissions(can_send_messages=False), until_date=until)
            action = "mute"
        elif penalty == "kick":
            await bot.ban_chat_member(chat_id=group_id, user_id=user.id, until_date=int(time.time() + 45))
            await bot.unban_chat_member(chat_id=group_id, user_id=user.id, only_if_banned=True)
            await registry_forget(group_id, user.id)
            action = "kick"
        else:
            await bot.ban_chat_member(chat_id=group_id, user_id=user.id, until_date=until)
            await registry_forget(group_id, user.id)
            action = "ban"
    except TelegramAPIError as e:
        logger.warning(f"[Anti-Raid] No se pudo aplicar '{penalty}' a {user.id} en {group_id}: {e}; se usa el captcha.")
        return False
    with contextlib.suppress(Exception):
        await raid_blacklist_add(group_id, user.id, penalty,
                                 penalty_until=until if penalty in ("mute", "ban") else None, **common)
    track_security(group_id, user.id, "raid_shield", action, bool(getattr(user, "is_bot", False)))
    return True

async def _quarantine_recent_joiners(bot: Bot, group_id: int, exclude_user_id: int) -> None:
    """
    Cuarentena retroactiva: las cuentas nuevas que entraron en la MISMA ráfaga antes de cruzar el umbral
    (p. ej. con la aduana apagada) pasan también por la puerta anti-raid. Las ya verificadas, las que tienen
    un desafío abierto y los administradores quedan fuera.
    """
    now = time.time()
    for uid, is_new in raid_shield.detector.window_members(group_id):
        if not is_new or uid == exclude_user_id or (group_id, uid) in CAPTCHA_SESSIONS:
            continue
        if now - RECENTLY_VERIFIED.get((group_id, uid), 0) < 300:
            continue
        try:
            await process_user_captcha(bot, group_id, uid, "", "", is_join_req=False, force=True)
        except Exception as e:
            logger.debug(f"[RaidShield] Cuarentena retroactiva de {uid} en {group_id}: {e}")
        await asyncio.sleep(0.2)


async def process_user_captcha(bot: Bot, group_id: int, user_id: int, full_name: str, username: str,
                               is_join_req: bool = False, user_chat_id: Optional[int] = None,
                               is_bot: bool = False, force: bool = False):
    """
    Aduana de seguridad con dos rutas estrictamente separadas:

    • Solicitud de unión (is_join_req=True): Telegram autoriza el diálogo privado con
      `user_chat_id` durante 5 minutos → el desafío se envía por privado. Si no se puede
      entregar, la solicitud queda pendiente para revisión manual (nunca se aprueba sola).
    • Ingreso directo (new_chat_members): CERO mensajes privados. Se restringe al miembro y el
      desafío se publica solo en el grupo, con auto-eliminación programada.
    """
    cfg = dict(await get_captcha_config(group_id))
    # Con el Escudo Anti-Raid activo (force) la aduana se aplica aunque el administrador la tenga apagada.
    if cfg["status"] != 1 and not force:
        return

    if is_super_admin(user_id) or await is_sentinel_account(group_id, user_id, username) or await is_whitelisted(user_id):
        if is_join_req:
            try:
                await bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
            except Exception:
                pass
        return

    if not is_join_req:
        try:
            member_check = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
            if member_check.status in ["creator", "administrator"]:
                return
        except Exception:
            pass

    session_key = (group_id, user_id)
    previous = CAPTCHA_SESSIONS.pop(session_key, None)
    if previous and previous.get("task"):
        previous["task"].cancel()

    lang = await group_lang(group_id)
    clean_name = html.escape(full_name or gtx(lang, "user", signed=False))
    mention = f"<a href='tg://user?id={user_id}'>{clean_name}</a>"

    if not is_join_req:
        try:
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        except Exception as e:
            logger.warning(f"Aviso restricción preventiva a {user_id} en {group_id}: {e}")

    # En solicitudes de unión el tiempo se acota para que el desenlace llegue dentro de la ventana.
    time_limit = int(cfg["time"] or 60)
    if is_join_req:
        time_limit = max(15, min(time_limit, JOIN_REQUEST_MAX_CAPTCHA_SECONDS))

    # 🛡️ v9 · Captcha Pro estricto para cuentas nuevas, bots y comunidades bajo escudo.
    overrides = raid_shield.captcha_overrides(group_id, user_id, is_bot=is_bot)
    if overrides:
        cfg["mode"] = overrides["mode"]
        time_limit = max(15, min(time_limit, overrides["max_time"]))

    custom_intro = cfg["text"] if cfg["text"] else gtx(lang, "captcha_intro", signed=False, mention=mention)

    session_data: dict = {"is_join_req": is_join_req, "is_bot": bool(is_bot), "lang": lang}
    if overrides:
        session_data["action_override"] = overrides["action"]
        session_data["source"] = "raid_shield" if overrides.get("isolated") else "captcha"
    if is_join_req:
        session_data["dm_chat_id"] = int(user_chat_id or user_id)
        session_data["dm_deadline"] = time.monotonic() + JOIN_REQUEST_DM_WINDOW_SECONDS - JOIN_REQUEST_DM_SAFETY_MARGIN

    if cfg["mode"] == 1:
        correct_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        session_data["code"] = correct_code
        kb = generate_captcha_keyboard(group_id, user_id, correct_code)
        challenge_line = gtx(lang, "captcha_code", signed=False, code=correct_code)
    else:
        session_data["code"] = "simple"
        kb = generate_simple_keyboard(group_id, user_id, lang)
        challenge_line = gtx(lang, "captcha_simple", signed=False)

    CAPTCHA_SESSIONS[session_key] = session_data
    title = gtx(lang, "captcha_title", signed=False)
    timer = gtx(lang, "captcha_time", signed=False, seconds=time_limit)

    if not is_join_req and overrides.get("isolated") and not is_bot:
        # Ruta 3 — Ingreso directo bajo escudo: sin desafío público individual. El miembro ya quedó
        # restringido; se verifica por la puerta anti-raid (chat privado iniciado por él mismo).
        session_data["raid_gate"] = True
        session_data["full_name"] = full_name or ""
        await _ensure_raid_gate(bot, group_id)
        session_data["task"] = _spawn(captcha_timeout_task(bot, group_id, user_id, raid_shield.RAID_GATE_SECONDS))
        return

    if is_join_req:
        # Ruta 1 — Solicitud de unión: diálogo privado autorizado por Telegram.
        text = "\n\n".join([title, custom_intro, challenge_line, timer]) + SVC_SIGNATURE
        try:
            sent_msg = await bot.send_message(chat_id=session_data["dm_chat_id"], text=text, reply_markup=kb, parse_mode="HTML")
            session_data["msg_id"] = sent_msg.message_id
        except Exception as e:
            # Sin entrega no hay verificación posible: se deja la solicitud pendiente para revisión
            # humana en lugar de aprobarla o rechazarla a ciegas.
            CAPTCHA_SESSIONS.pop(session_key, None)
            logger.warning(f"Aduana: no se pudo entregar el desafío de la solicitud de {user_id} en {group_id} ({e}); queda pendiente para revisión manual.")
            return
    else:
        # Ruta 2 — Ingreso directo: desafío exclusivamente en el grupo, sin DMs.
        try:
            group_msg = await bot.send_message(
                chat_id=group_id,
                text="\n\n".join([title, custom_intro, f"{mention} — {challenge_line}", timer]) + SVC_SIGNATURE,
                reply_markup=kb,
                parse_mode="HTML"
            )
            session_data["group_msg_id"] = group_msg.message_id
            # Auto-eliminación programada de respaldo (el desenlace normal también lo borra).
            _spawn(auto_delete_msg(group_msg, time_limit + 5))
        except Exception as e:
            logger.warning(f"Aduana: no se pudo publicar el desafío de {user_id} en {group_id}: {e}")

    session_data["task"] = _spawn(captcha_timeout_task(bot, group_id, user_id, time_limit))


# ==========================================
# 📡 SOLICITUDES DE UNIÓN (JOIN REQUESTS)
# ==========================================
@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot):
    group_id = event.chat.id
    user_id = event.from_user.id
    is_bot = bool(event.from_user.is_bot)

    # 🚨 v9/v37 · Detector de picos coordinados: las solicitudes tienen su propio umbral configurable.
    verdict = await _register_join_and_alert(bot, group_id, user_id, event.from_user.full_name or "", is_bot,
                                             kind="request")
    # 🚨 v37 · Penalización configurable de raid (antes de CAS / captcha).
    if not is_bot and await _raid_should_penalize(group_id, verdict):
        if await _apply_raid_penalty(bot, group_id, event.from_user, is_join_req=True):
            return

    if await check_global_cas_spam(user_id):
        try:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
            track_security(group_id, user_id, "antispam", "decline", is_bot)
            logger.warning(f"🚨 [Anti-Spam Global CAS] Solicitud de spammer global {user_id} rechazada en {group_id}.")
        except Exception:
            pass
        return

    if await enforce_userbot_flag(bot, group_id, user_id, event.from_user.username or "", source="solicitud de ingreso"):
        try:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
        except Exception:
            pass
        return

    cfg = await get_captcha_config(group_id)
    # Bajo escudo, las cuentas nuevas pasan por la aduana aunque esté desactivada.
    forced = bool(verdict.get("isolated")) and bool(verdict.get("is_new"))

    if cfg["status"] != 1 and not forced:
        try: 
            await bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
        except Exception: 
            pass
        return

    await process_user_captcha(
        bot, group_id, user_id, event.from_user.full_name, event.from_user.username or "",
        is_join_req=True,
        user_chat_id=getattr(event, "user_chat_id", None) or user_id,
        is_bot=is_bot, force=forced
    )


@router.message(F.chat.type.in_({"group", "supergroup"}), F.new_chat_members)
async def handle_new_members(message: Message, bot: Bot):
    group_id = message.chat.id
    cfg = await get_captcha_config(group_id)
    srv_mode = await get_service_msgs_mode(group_id)

    if cfg.get("service_del") == 1 or srv_mode == 1:
        try: 
            await message.delete()
        except Exception: 
            pass

    bot_me = await bot.get_me()
    now = time.time()

    if len(RECENTLY_VERIFIED) > 2000:
        stale_keys = [k for k, ts in RECENTLY_VERIFIED.items() if now - ts > 300]
        for k in stale_keys:
            RECENTLY_VERIFIED.pop(k, None)

    adder = message.from_user
    for new_user in message.new_chat_members or []:
        if new_user.id == bot_me.id:
            continue

        # 🚨 v9 · Detector de picos coordinados (cuentas nuevas, bots y nombres clonados).
        verdict = await _register_join_and_alert(bot, group_id, new_user.id, new_user.full_name or "", bool(new_user.is_bot))

        if new_user.is_bot and verdict.get("isolated"):
            # Bajo escudo, un bot añadido por alguien que no es administrador se retira al instante.
            adder_is_admin = bool(adder) and (adder.id == new_user.id or is_super_admin(adder.id)
                                              or await _is_group_admin(bot, group_id, adder.id))
            if not adder_is_admin:
                try:
                    await bot.ban_chat_member(chat_id=group_id, user_id=new_user.id)
                    track_security(group_id, new_user.id, "raid_shield", "ban", True)
                    logger.warning(f"🚨 [RaidShield] Bot {new_user.id} añadido durante un raid en {group_id}: retirado.")
                except Exception as e:
                    logger.warning(f"[RaidShield] No se pudo retirar el bot {new_user.id} de {group_id}: {e}")
                continue

        # 🚨 v37 · Penalización configurable de raid (silencio / expulsión / baneo temporal o captcha estricto).
        if not new_user.is_bot and await _raid_should_penalize(group_id, verdict):
            if await _apply_raid_penalty(bot, group_id, new_user, is_join_req=False):
                continue

        # 🛸 v15 · Vector 29 (bot_added): bot invitado por un miembro sin privilegios → el bot se retira al
        # instante y quien lo invitó recibe la sanción configurada en la matriz multimedia.
        if new_user.is_bot and await media_locks.handle_bot_added(bot, message, adder, new_user):
            continue

        if not new_user.is_bot:
            if await check_global_cas_spam(new_user.id):
                try:
                    await bot.ban_chat_member(chat_id=group_id, user_id=new_user.id)
                    track_security(group_id, new_user.id, "antispam", "ban", False)
                    logger.warning(f"🚨 [Anti-Spam Global CAS] Miembro spammer global {new_user.id} expulsado en {group_id}.")
                except Exception as e:
                    logger.error(f"Error expulsando spammer global {new_user.id}: {e}")
                continue

            await registry_track(group_id, new_user.id, force=True)

            if await enforce_userbot_flag(bot, group_id, new_user.id, new_user.username or "", source="ingreso"):
                continue

        forced = bool(verdict.get("isolated")) and bool(verdict.get("is_new"))
        if cfg["status"] != 1 and not forced:
            continue

        if (group_id, new_user.id) in CAPTCHA_SESSIONS or (now - RECENTLY_VERIFIED.get((group_id, new_user.id), 0) < 120):
            continue

        await process_user_captcha(bot, group_id, new_user.id, new_user.full_name, new_user.username or "",
                                   is_join_req=False, is_bot=bool(new_user.is_bot), force=forced)


@router.chat_member(F.chat.type.in_({"group", "supergroup"}))
async def track_member_updates(event: ChatMemberUpdated, bot: Bot):
    user = event.new_chat_member.user
    if user.is_bot:
        return

    group_id = event.chat.id
    _MEMBER_STATUS_CACHE.pop((group_id, user.id), None)
    # 👑 Cambio de rango (ascenso, degradación, transferencia de propiedad): la caché de dueño se invalida al
    # instante para que un administrador degradado o un ex-creador pierdan el acceso sin esperar a que caduque.
    try:
        from handlers.user_private import invalidate_owner_status
        invalidate_owner_status(group_id, user.id)
    except Exception:
        pass
    # 🛡️ v37 · Anti-Rogue: un ascenso o degradación se refleja al instante en quién puede invocar al bot.
    try:
        from middlewares.anti_spam import forget_sender_rights
        forget_sender_rights(group_id, user.id)
    except Exception:
        pass

    new_status = event.new_chat_member.status
    if new_status in ("left", "kicked"):
        await registry_forget(group_id, user.id)
        return
    # ⚖️ v24 · Reingreso por un enlace de apelación: se revoca y se borra el mensaje privado que lo contenía.
    invite = getattr(event, "invite_link", None)
    if invite is not None and str(getattr(invite, "name", "") or "").startswith("appeal-"):
        _spawn(ban_appeals.on_member_joined(group_id, user.id, invite))
    if new_status == "restricted" and getattr(event.new_chat_member, "is_member", True) is False:
        await registry_forget(group_id, user.id)
        return

    await registry_track(group_id, user.id, force=True)
    await enforce_userbot_flag(bot, group_id, user.id, user.username or "", source="cambio de membresía")


@router.callback_query(F.data.startswith("cap_ver_") | F.data.startswith("cap_simple_"))
async def process_captcha(callback: CallbackQuery, bot: Bot):
    data = (callback.data or "").split("_")
    if len(data) < 4:
        await callback.answer()
        return

    action_type = data[1]
    if action_type == "ver":
        if len(data) < 5: 
            await callback.answer()
            return
        group_id = int(data[2])
        target_user_id = int(data[3])
        is_correct = int(data[4])
    else:
        group_id = int(data[2])
        target_user_id = int(data[3])
        is_correct = 1

    lang = await group_lang(group_id)
    clicker_id = callback.from_user.id
    if clicker_id != target_user_id:
        await callback.answer(gtx(lang, "captcha_not_yours", signed=False), show_alert=True)
        return

    session_key = (group_id, target_user_id)
    session_data = CAPTCHA_SESSIONS.pop(session_key, {})
    if "task" in session_data:
        session_data["task"].cancel()

    # Ruta de ingreso: por la sesión o, si se perdió (reinicio), por dónde se pulsó el botón:
    # un desafío pulsado en privado solo pudo originarse en una solicitud de unión.
    message_chat_type = getattr(getattr(callback.message, "chat", None), "type", None)
    if session_data:
        is_join_req = bool(session_data.get("is_join_req"))
    else:
        is_join_req = message_chat_type == "private"
        if is_join_req and callback.message is not None:
            session_data = {
                "is_join_req": True,
                "dm_chat_id": callback.message.chat.id,
                # Ventana desconocida tras un reinicio: se asume cerrada (sin DMs adicionales).
                "dm_deadline": 0.0,
            }

    cfg = await get_captcha_config(group_id)

    try:
        if isinstance(callback.message, Message):   # InaccessibleMessage no tiene delete()
            await callback.message.delete()
    except Exception:
        pass

    group_msg_id = session_data.get("group_msg_id")
    if group_msg_id:
        try:
            await bot.delete_message(chat_id=group_id, message_id=group_msg_id)
        except Exception:
            pass

    chat_info = None
    try:
        chat_info = await bot.get_chat(group_id)
        group_title = chat_info.title or gtx(lang, "community", signed=False)
    except Exception:
        group_title = gtx(lang, "community", signed=False)

    if is_correct == 1:
        await callback.answer(gtx(lang, "captcha_verified_toast", signed=False), show_alert=False)
        RECENTLY_VERIFIED[(group_id, target_user_id)] = time.time()
        track_security(group_id, target_user_id, "captcha", "verified", False)
        # 🗂️ v37 · Si estaba en la lista negra de raid (captcha estricto), el caso se cierra como liberado.
        _spawn(raid_blacklist_resolve(group_id, target_user_id, "released", None))

        if is_join_req:
            try:
                await bot.approve_chat_join_request(chat_id=group_id, user_id=target_user_id)
            except Exception as e:
                logger.warning(f"Aviso aprobando solicitud de {target_user_id} en {group_id}: {e}")
        else:
            # Se restauran los permisos por defecto del grupo (no se conceden más de los que el
            # grupo permite a cualquier miembro).
            default_permissions = getattr(chat_info, "permissions", None) or ChatPermissions(
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
            try:
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=target_user_id,
                    permissions=default_permissions
                )
            except Exception as e:
                logger.warning(f"Aviso otorgando permisos en {group_id}: {e}")

        user_full_name = html.escape(callback.from_user.full_name or gtx(lang, "user", signed=False))
        user_mention = f"<a href='tg://user?id={target_user_id}'>{user_full_name}</a>"

        await _post_group_notice(bot, group_id, gtx(lang, "captcha_ok", mention=user_mention,
                                                    title=html.escape(group_title)), ttl=20)

    else:
        await callback.answer(gtx(lang, "captcha_wrong_toast", signed=False), show_alert=True)
        custom_fail = cfg["text"] if cfg["text"] else gtx(lang, "captcha_fail_default", signed=False)

        if is_join_req:
            # Aviso privado dentro de la ventana legal, ANTES de rechazar la solicitud.
            await _send_join_request_dm(bot, session_data, gtx(lang, "captcha_fail_dm", text=custom_fail),
                                        reply_markup=_support_keyboard(lang))
        else:
            # Ingreso directo: el aviso se publica solo en el grupo y se autoelimina.
            await _post_group_notice(
                bot, group_id,
                gtx(lang, "captcha_fail_group",
                    mention=f"<a href='tg://user?id={target_user_id}'>{gtx(lang, 'user', signed=False)}</a>",
                    text=custom_fail),
                reply_markup=_support_keyboard(lang)
            )

        await _apply_captcha_sanction(bot, group_id, target_user_id, cfg, is_join_req, session_data)


@router.message(CommandStart(deep_link=True), F.chat.type == "private", F.text.regexp(r"^/start\s+raidv_-?\d+$"))
async def raid_gate_start(message: Message, command: CommandObject, bot: Bot):
    """
    Puerta anti-raid: el miembro restringido abre el bot desde el enlace del grupo (chat iniciado por él) y
    recibe aquí su Captcha Pro por código. Sin sesión pendiente no se revela nada.
    """
    try:
        group_id = int(str(command.args or "").split("_", 1)[1])
    except (IndexError, ValueError):
        return
    if message.from_user is None:
        return
    user_id = message.from_user.id
    lang = await group_lang(group_id)
    session_data = CAPTCHA_SESSIONS.get((group_id, user_id))
    if not session_data or not session_data.get("raid_gate"):
        await message.answer(gtx(lang, "gate_none"), parse_mode="HTML")
        return
    previous_msg = session_data.get("msg_id")
    if previous_msg:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=previous_msg)
        except Exception:
            pass
    correct_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    session_data["code"] = correct_code
    session_data["dm_chat_id"] = message.chat.id
    try:
        title = (await bot.get_chat(group_id)).title or gtx(lang, "community", signed=False)
    except Exception:
        title = gtx(lang, "community", signed=False)
    sent = await message.answer(
        gtx(lang, "gate_dm", title=html.escape(title), code=correct_code),
        reply_markup=generate_captcha_keyboard(group_id, user_id, correct_code),
        parse_mode="HTML"
    )
    session_data["msg_id"] = sent.message_id


# ==========================================
# 🧹 PURGA Y LIMPIEZA DE MENSAJES DE SERVICIO
# ==========================================
@router.message(F.chat.type.in_({"group", "supergroup"}), F.left_chat_member)
async def purge_left_member(message: Message):
    left_user = message.left_chat_member
    if left_user and not left_user.is_bot:
        await registry_forget(message.chat.id, left_user.id)

    cfg = await get_captcha_config(message.chat.id)
    srv_mode = await get_service_msgs_mode(message.chat.id)
    if cfg.get("service_del") == 1 or srv_mode == 1:
        try: 
            await message.delete()
        except Exception: 
            pass


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.video_chat_started | F.video_chat_ended | F.video_chat_participants_invited | F.video_chat_scheduled | F.pinned_message
)
async def purge_general_service_messages(message: Message):
    if (message.video_chat_started or message.video_chat_ended or 
        message.video_chat_participants_invited or getattr(message, "video_chat_scheduled", None)):
        try:
            await message.delete()
            return
        except Exception:
            pass

    cfg = await get_captcha_config(message.chat.id)
    srv_mode = await get_service_msgs_mode(message.chat.id)
    if cfg.get("service_del") == 1 or srv_mode == 1:
        try: 
            await message.delete()
        except Exception: 
            pass


# ==========================================
# 🧹 MOTOR HÍBRIDO DE GHOST PURGE (MTPROTO + BOT API)
# ==========================================
GHOST_DISPLAY_NAMES = {"deleted account", "cuenta eliminada"}
GHOST_SCAN_CONCURRENCY = 6
GHOST_SCAN_CHUNK = 60
GHOST_SCAN_MAX_MEMBERS = 30000
_INVALID_USER_HINTS = ("user not found", "participant_id_invalid", "user_id_invalid", "invalid user_id")
_NO_RIGHTS_HINTS = ("not enough rights", "need to be administrator", "have no rights", "can't remove chat owner", "not enough permissions")

_GHOST_PURGE_RUNNING: set = set()


async def _call_with_retry(func, *args, attempts: int = 3, **kwargs):
    for attempt in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == attempts:
                raise
            await asyncio.sleep(min(float(e.retry_after), 60.0) + 0.5)


def _is_ghost_user(user) -> bool:
    if getattr(user, "is_deleted", False):
        return True
    first_name = (getattr(user, "first_name", "") or "").strip().lower()
    return first_name in GHOST_DISPLAY_NAMES


async def _inspect_member(bot: Bot, group_id: int, user_id: int) -> str:
    if is_super_admin(user_id):
        return "protected"

    try:
        member = await _call_with_retry(bot.get_chat_member, chat_id=group_id, user_id=user_id)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(hint in err for hint in _INVALID_USER_HINTS):
            return "invalid"
        return "unverified"
    except Exception:
        return "unverified"
    if member is None:   # _call_with_retry con attempts < 1 no consulta nada
        return "unverified"

    status = member.status
    if status in ("creator", "administrator"):
        return "protected"
    if status in ("left", "kicked"):
        return "gone"
    if status == "restricted" and getattr(member, "is_member", True) is False:
        return "gone"
    if not _is_ghost_user(member.user):
        return "active"

    try:
        if await is_whitelisted(user_id):
            return "protected"
    except Exception:
        pass

    return "ghost"


async def _expel_ghost(bot: Bot, group_id: int, user_id: int) -> str:
    try:
        await _call_with_retry(
            bot.ban_chat_member,
            chat_id=group_id, user_id=user_id,
            until_date=int(time.time() + 45), revoke_messages=False
        )
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(hint in err for hint in _INVALID_USER_HINTS):
            return "stale"
        if any(hint in err for hint in _NO_RIGHTS_HINTS):
            return "no_rights"
        logger.warning(f"Ghost Purge: no se pudo expulsar a {user_id} en {group_id}: {e}")
        return "failed"
    except TelegramAPIError as e:
        logger.warning(f"Ghost Purge: error de API expulsando a {user_id} en {group_id}: {e}")
        return "failed"

    for attempt in range(3):
        try:
            await _call_with_retry(bot.unban_chat_member, chat_id=group_id, user_id=user_id, only_if_banned=True)
            break
        except TelegramAPIError as e:
            if attempt == 2:
                logger.warning(f"Ghost Purge: unban de {user_id} en {group_id} falló ({e}); el ban temporal expirará solo.")
            else:
                await asyncio.sleep(1.5)

    return "purged"


async def _edit_status(status_msg: Message, text: str) -> None:
    try:
        await status_msg.edit_text(text, parse_mode="HTML")
    except TelegramAPIError:
        pass


async def run_bot_api_ghost_purge(bot: Bot, group_id: int, action: str = "ban", dry_run: bool = False, status_msg: Optional[Message] = None) -> dict:
    await _bootstrap_registry_from_sentinel(group_id)
    registered = await registry_members(group_id)
    candidates = registered[:GHOST_SCAN_MAX_MEMBERS]

    try:
        total_members = await bot.get_chat_member_count(group_id)
    except Exception:
        total_members = None

    stats = {
        "ghost": 0, "invalid": 0, "protected": 0, "gone": 0, "active": 0, "unverified": 0,
        "purged": 0, "stale": 0, "failed": 0
    }
    processed = 0
    abort = asyncio.Event()
    semaphore = asyncio.Semaphore(GHOST_SCAN_CONCURRENCY)

    async def process(uid: int):
        nonlocal processed
        if abort.is_set():
            return
        async with semaphore:
            if abort.is_set():
                return
            try:
                verdict = await _inspect_member(bot, group_id, uid)
                stats[verdict] += 1
                processed += 1

                if verdict == "gone":
                    await registry_forget(group_id, uid)
                    return

                if verdict in ("ghost", "invalid") and not dry_run:
                    outcome = await _expel_ghost(bot, group_id, uid)
                    if outcome == "no_rights":
                        abort.set()
                        return
                    stats[outcome] += 1
                    if outcome in ("purged", "stale"):
                        await registry_forget(group_id, uid)
            except Exception as e:
                stats["unverified"] += 1
                logger.warning(f"Ghost Purge: error procesando a {uid} en {group_id}: {e}")

    last_edit = time.time()
    for start in range(0, len(candidates), GHOST_SCAN_CHUNK):
        if abort.is_set():
            break
        chunk = candidates[start:start + GHOST_SCAN_CHUNK]
        await asyncio.gather(*(process(uid) for uid in chunk))

        if status_msg and (time.time() - last_edit > 8):
            last_edit = time.time()
            await _edit_status(
                status_msg,
                gtx(await group_lang(group_id), "ghost_progress", done=processed, total=len(candidates),
                    found=stats["ghost"] + stats["invalid"])
            )
        await asyncio.sleep(0.2)

    await update_ghost_purge_scan_time(group_id)
    return {
        "status": "success",
        "processed": processed,
        "found": stats["ghost"] + stats["invalid"],
        "purged": stats["purged"],
        "total_members": total_members,
        "aborted": abort.is_set(),
        "stats": stats
    }


async def execute_unified_ghost_purge(bot: Bot, group_id: int, action: str = "ban", dry_run: bool = False, status_msg: Optional[Message] = None) -> dict:
    lang = await group_lang(group_id)
    try:
        mtproto_res = await execute_ghost_purge(chat_id=group_id, action=action, lang=lang)
        if mtproto_res.get("status") == "success" and not dry_run:
            return {
                "engine": gtx(lang, "engine_mtproto", signed=False),
                "found": mtproto_res.get("found", 0),
                "purged": mtproto_res.get("purged", 0),
                "action": action
            }
    except Exception as ex:
        logger.warning(f"Aviso MTProto Purge en {group_id}, pasando a Bot API: {ex}")

    bot_api_res = await run_bot_api_ghost_purge(bot, group_id, action=action, dry_run=dry_run, status_msg=status_msg)
    bot_api_res["engine"] = gtx(lang, "engine_botapi", signed=False)
    return bot_api_res


@router.message(Command("purgeghosts"), F.chat.type.in_({"group", "supergroup"}))
async def purge_ghosts_command(message: Message, bot: Bot):
    group_id = message.chat.id
    username = message.from_user.username if message.from_user else ""
    user_id = message.from_user.id if message.from_user else 0

    # 👑 Regla de Oro: exclusivo del Dueño legítimo o del Arquitecto (ni administradores comunes ni anónimos).
    if not await _require_owner_message(bot, message):
        return

    lang = await group_lang(group_id)
    tier = (await get_group_tier(group_id) or "free").lower()
    if tier == "free":
        try:
            from database.database import check_can_free_purge
            if not await check_can_free_purge(group_id):
                warn = await message.answer(gtx(lang, "ghost_free_limit"), parse_mode="HTML")
                _spawn(auto_delete_msg(warn, await service_ttl(group_id, 15)))
                return
        except Exception:
            pass

    args = (message.text or "").split()[1:]
    dry_run = bool(args) and args[0].strip().lower() in ("scan", "dry", "simular", "preview")
    action = "kick" if bool(args) and args[0].strip().lower() in ("kick", "expulsar") else "ban"

    if group_id in _GHOST_PURGE_RUNNING:
        busy = await message.answer(gtx(lang, "ghost_busy"), parse_mode="HTML")
        _spawn(auto_delete_msg(busy, await service_ttl(group_id, 8)))
        return

    _GHOST_PURGE_RUNNING.add(group_id)
    try:
        status_msg = await message.answer(
            gtx(lang, "ghost_start", sim=gtx(lang, "ghost_sim", signed=False) if dry_run else ""),
            parse_mode="HTML"
        )

        res = await execute_unified_ghost_purge(bot, group_id, action=action, dry_run=dry_run, status_msg=status_msg)
        lines = [gtx(lang, "ghost_done", sim=gtx(lang, "ghost_sim_done", signed=False) if dry_run else "",
                     engine=res.get("engine", "Hybrid"), found=res.get("found", 0), purged=res.get("purged", 0),
                     action=action.upper())]

        await _edit_status(status_msg, "\n".join(lines))
        _spawn(auto_delete_msg(status_msg, 60))

    except Exception as e:
        logger.error(f"Error ejecutando Ghost Purge en {group_id}: {e}")
        try:
            await message.answer(gtx(lang, "ghost_error"), parse_mode="HTML")
        except Exception:
            pass
    finally:
        _GHOST_PURGE_RUNNING.discard(group_id)


# ==========================================
# 🛡️️ EL BOTÓN DE PÁNICO (PROTOCOLO RAID LOCKDOWN)
# ==========================================
_FULL_PERMISSION_FIELDS = [
    "can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos",
    "can_send_videos", "can_send_video_notes", "can_send_voice_notes", "can_send_polls",
    "can_send_other_messages", "can_add_web_page_previews", "can_change_info",
    "can_invite_users", "can_pin_messages", "can_manage_topics"
]


def _permissions_to_dict(perms: ChatPermissions) -> dict:
    return {field: getattr(perms, field, None) for field in _FULL_PERMISSION_FIELDS}


def _lockdown_permissions() -> ChatPermissions:
    return ChatPermissions(**{field: False for field in _FULL_PERMISSION_FIELDS})


async def _engage_panic(bot: Bot, chat, activated_by: int):
    group_id = chat.id

    try:
        full_chat = await bot.get_chat(group_id)
        current_perms = full_chat.permissions or ChatPermissions()
    except Exception:
        current_perms = ChatPermissions()

    snapshot_json = json.dumps(_permissions_to_dict(current_perms))
    was_activated = await activate_panic(group_id, activated_by, snapshot_json)
    if not was_activated:
        return False
    # El protocolo eleva captcha, cerraduras y anti-spam: la Mini App lo refleja al instante.
    _sync_miniapp(group_id, ["panic", "captcha_enabled", "linklock_enabled", "antiflood_enabled"], source="panic")

    try:
        await bot.set_chat_permissions(chat_id=group_id, permissions=_lockdown_permissions())
    except Exception as e:
        logger.warning(f"Aviso: no se pudieron restringir los permisos de {group_id} en /panic: {e}")

    try:
        lang = await group_lang(group_id)
        admins = await bot.get_chat_administrators(group_id)
        owner = next((a for a in admins if a.status == "creator"), None)
        if owner:
            alert_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=gtx(lang, "panic_btn_off", signed=False), callback_data=f"panic_off_{group_id}")]
            ])
            await bot.send_message(
                chat_id=owner.user.id,
                text=gtx(lang, "panic_dm", title=html.escape(chat.title or gtx(lang, "untitled", signed=False)),
                         chat_id=group_id, by=activated_by),
                reply_markup=alert_kb,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.warning(f"Aviso: no se pudo notificar al dueño de {group_id} sobre /panic: {e}")

    return True


@router.message(Command("panic"), F.chat.type.in_({"group", "supergroup"}))
async def panic_command(message: Message, bot: Bot):
    group_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    # 👑 Regla de Oro: el Botón de Pánico es exclusivo del Dueño legítimo o del Arquitecto.
    if not await _require_owner_message(bot, message):
        return

    lang = await group_lang(group_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "panic_btn_off", signed=False), callback_data=f"panic_off_{group_id}")]
    ])
    if await get_panic_status(group_id) == 1:
        await message.answer(gtx(lang, "panic_already"), reply_markup=kb, parse_mode="HTML")
        return

    engaged = await _engage_panic(bot, message.chat, user_id)
    if engaged:
        await message.answer(gtx(lang, "panic_on"), reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("panic_off_"))
async def panic_deactivate_callback(callback: CallbackQuery, bot: Bot):
    try:
        group_id = int((callback.data or "").split("_")[-1])
    except ValueError:
        await callback.answer()
        return
    user_id = callback.from_user.id

    # 👑 Regla de Oro: solo el Dueño legítimo o el Arquitecto levantan el Protocolo.
    lang = await group_lang(group_id)
    if not await _is_community_owner(bot, group_id, user_id):
        logger.warning(f"⛔ [Aduana] panic_off bloqueado: user={user_id} no es Dueño de {group_id}.")
        await callback.answer(gtx(lang, "owner_only_short", signed=False), show_alert=True)
        return

    result = await deactivate_panic(group_id)
    if result:
        _sync_miniapp(group_id, ["panic", "captcha_enabled", "linklock_enabled", "antiflood_enabled"], source="panic")
    perms_json = result.get("chat_permissions_json") if isinstance(result, dict) else None
    if perms_json:
        try:
            perms_dict = json.loads(perms_json)
            await bot.set_chat_permissions(chat_id=group_id, permissions=ChatPermissions(**perms_dict))
        except Exception as e:
            logger.warning(f"Aviso restaurando permisos de {group_id} tras desactivar /panic: {e}")

    if isinstance(callback.message, Message):   # un mensaje inaccesible no se puede editar
        try:
            await callback.message.edit_text(gtx(lang, "panic_off_done"), parse_mode="HTML")
        except Exception:
            pass
    await callback.answer(gtx(lang, "panic_off_toast", signed=False))


async def execute_raid_lockdown(bot: Bot, group_id: int) -> bool:
    if await get_panic_status(group_id) == 1:
        return False

    try:
        chat = await bot.get_chat(group_id)
    except Exception as e:
        logger.warning(f"Aviso: no se pudo resolver el chat {group_id} en execute_raid_lockdown: {e}")
        return False

    return await _engage_panic(bot, chat, activated_by=0)


async def lift_raid_lockdown(bot: Bot, group_id: int) -> bool:
    result = await deactivate_panic(group_id)
    if not result:
        return False
    _sync_miniapp(group_id, ["panic", "captcha_enabled", "linklock_enabled", "antiflood_enabled"], source="panic")

    perms_json = result.get("chat_permissions_json") if isinstance(result, dict) else None
    if perms_json:
        try:
            perms_dict = json.loads(perms_json)
            await bot.set_chat_permissions(chat_id=group_id, permissions=ChatPermissions(**perms_dict))
        except Exception as e:
            logger.warning(f"Aviso restaurando permisos de {group_id} en lift_raid_lockdown: {e}")

    return True


# ==========================================
# 🎥🎙️ INTERRUPTORES: ESCUDO, ATENUACIÓN & ANTIRRUIDO
# ==========================================
def _parse_on_off(args: list, default_on: bool = True) -> int:
    if not args:
        return 1 if default_on else 0
    val = args[0].strip().lower()
    if val in ("off", "0", "desactivar", "no"):
        return 0
    return 1


@router.message(Command("screenshield"), F.chat.type.in_({"group", "supergroup"}))
async def screenshield_toggle(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _require_owner_message(bot, message):
        return
    args = (message.text or "").split()[1:]
    status = _parse_on_off(args)
    await set_screen_shield_status(group_id, status)
    _sync_miniapp(group_id, ["shield_enabled"])
    lang = await group_lang(group_id)
    reply = await message.answer(
        gtx(lang, "screenshield", state=gtx(lang, "activated" if status else "deactivated", signed=False)), parse_mode="HTML"
    )
    _spawn(auto_delete_msg(reply, await service_ttl(group_id, 20)))


@router.message(Command("duckvolume"), F.chat.type.in_({"group", "supergroup"}))
async def duck_volume_command(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _require_owner_message(bot, message):
        return
    args = (message.text or "").split()[1:]
    lang = await group_lang(group_id)
    if not args or not args[0].strip().isdigit():
        reply = await message.answer(html.escape(gtx(lang, "duck_usage", signed=False)), parse_mode="HTML")
        _spawn(auto_delete_msg(reply, await service_ttl(group_id, 20)))
        return
    pct = max(1, min(100, int(args[0].strip())))
    await set_podcast_duck_volume(group_id, pct * 100)
    _sync_miniapp(group_id, ["podcast_duck_volume"])
    reply = await message.answer(gtx(lang, "duck_ok", pct=pct), parse_mode="HTML")
    _spawn(auto_delete_msg(reply, await service_ttl(group_id, 20)))


@router.message(Command("noiseshield"), F.chat.type.in_({"group", "supergroup"}))
async def noiseshield_toggle(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _require_owner_message(bot, message):
        return
    args = (message.text or "").split()[1:]
    status = _parse_on_off(args)
    await set_noise_shield_status(group_id, status)
    _sync_miniapp(group_id, ["noise_shield"])
    lang = await group_lang(group_id)
    reply = await message.answer(
        gtx(lang, "noiseshield", state=gtx(lang, "activated" if status else "deactivated", signed=False)), parse_mode="HTML"
    )
    _spawn(auto_delete_msg(reply, await service_ttl(group_id, 20)))


# ==========================================
# 💰 COLA DE SPEAKERS PAGADA (/speakers)
# ==========================================
@router.message(Command("speakers"), F.chat.type.in_({"group", "supergroup"}))
async def speakers_command(message: Message, bot: Bot):
    group_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    args = (message.text or "").split(maxsplit=2)[1:]
    sub = args[0].lower() if args else ""
    lang = await group_lang(group_id)
    # 👑 Las subórdenes de gestión (next / clear / price) son exclusivas del Dueño o del Arquitecto;
    # consultar la cola y comprar turno sigue abierto a cualquier miembro.
    is_admin = False
    if sub in ("next", "clear", "price"):
        is_admin = await _message_author_is_owner(bot, message)
        if not is_admin:
            await _deny_owner_only_message(message)
            return

    if sub == "next":
        if not is_admin:
            return
        row = await pop_next_speaker(group_id)
        if not row:
            await message.answer(gtx(lang, "spk_empty"), parse_mode="HTML")
            return
        _, spk_user_id, full_name, spk_username, stars_paid = row
        mention = f"<a href='tg://user?id={spk_user_id}'>{html.escape(full_name)}</a>"
        tag = f" (⭐ {stars_paid} XTR)" if stars_paid else ""
        await message.answer(gtx(lang, "spk_next", mention=mention, tag=tag), parse_mode="HTML")
        return

    if sub == "clear":
        if not is_admin:
            return
        await clear_speaker_queue(group_id)
        await message.answer(gtx(lang, "spk_cleared"), parse_mode="HTML")
        return

    if sub == "price":
        if not is_admin:
            return
        if len(args) > 1 and args[1].strip().isdigit():
            new_price = int(args[1].strip())
            await set_speaker_price(group_id, new_price)
            _sync_miniapp(group_id, ["speaker_price"])
            await message.answer(gtx(lang, "spk_price_ok", price=new_price), parse_mode="HTML")
        else:
            await message.answer(html.escape(gtx(lang, "spk_price_usage", signed=False)), parse_mode="HTML")
        return

    queue = await get_speaker_queue(group_id)
    price = await get_speaker_price(group_id) or 25

    if queue:
        lines = []
        for idx, row in enumerate(queue[:10], start=1):
            _, spk_user_id, full_name, spk_username, stars_paid, _ts = row
            tag = f" ⭐{stars_paid}" if stars_paid else ""
            lines.append(f"{idx}. {html.escape(full_name)}{tag}")
        queue_text = "\n".join(lines)
    else:
        queue_text = gtx(lang, "spk_queue_empty_line", signed=False)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "spk_btn", signed=False, price=price), callback_data=f"speak_buy_{group_id}_{price}")]
    ])
    card = await message.answer(
        gtx(lang, "spk_card", title=html.escape(message.chat.title or ""), queue=queue_text, price=price),
        reply_markup=kb, parse_mode="HTML"
    )
    _spawn(auto_delete_msg(card, await service_ttl(group_id, 60)))


@router.callback_query(F.data.startswith("speak_buy_"))
async def speak_buy_callback(callback: CallbackQuery, bot: Bot):
    parts = (callback.data or "").split("_")
    try:
        group_id = int(parts[2])
        price = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer()
        return
    await callback.answer()
    lang = await group_lang(group_id)

    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=gtx(lang, "spk_inv_title", signed=False)[:32],
            description=gtx(lang, "spk_inv_desc", signed=False),
            payload=f"speak_{group_id}_{callback.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label=gtx(lang, "spk_inv_label", signed=False), amount=price)],
        )
    except Exception as e:
        # Si la factura no se entregó, el usuario no ha iniciado el bot: un segundo DM fallaría
        # igual. Se le invita a abrir el privado él mismo desde un aviso efímero en el grupo.
        logger.warning(f"Error enviando invoice de /speakers a {callback.from_user.id}: {e}")
        try:
            bot_info = await bot.me()
            open_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=gtx(lang, "btn_open_dm", signed=False), url=f"https://t.me/{bot_info.username}?start=speakers")]
            ])
            if callback.message is not None:
                notice = await callback.message.answer(gtx(lang, "spk_open_dm"), reply_markup=open_kb, parse_mode="HTML")
                _spawn(auto_delete_msg(notice, await service_ttl(group_id, 30)))
        except Exception:
            pass


@router.pre_checkout_query(F.invoice_payload.startswith("speak_"))
async def speakers_pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.successful_payment, F.successful_payment.invoice_payload.startswith("speak_"))
async def speakers_successful_payment(message: Message, bot: Bot):
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return
    payload = payment.invoice_payload
    try:
        _, group_id_str, user_id_str = payload.split("_")
        group_id, buyer_id = int(group_id_str), int(user_id_str)
    except Exception:
        return

    stars_paid = payment.total_amount
    full_name = message.from_user.full_name or gtx(await group_lang(group_id), "user", signed=False)
    username = message.from_user.username or ""

    await add_to_speaker_queue(group_id, buyer_id, full_name, username, stars_paid)

    lang = await group_lang(group_id)
    await message.answer(gtx(lang, "spk_paid_dm", stars=stars_paid), parse_mode="HTML")
    try:
        clean_name = html.escape(full_name)
        notice = await bot.send_message(
            chat_id=group_id,
            text=gtx(lang, "spk_paid_group", mention=f"<a href='tg://user?id={buyer_id}'>{clean_name}</a>", stars=stars_paid),
            parse_mode="HTML"
        )
        _spawn(auto_delete_msg(notice, await service_ttl(group_id, 45)))
    except Exception:
        pass


# ==========================================
# 🗂️ PADRÓN LOCAL DE MIEMBROS
# ==========================================
_REGISTRY_TOUCH: dict = {}
_REGISTRY_TOUCH_TTL = 600


def _registry_connect() -> sqlite3.Connection:
    directory = os.path.dirname(MEMBER_REGISTRY_DB)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(MEMBER_REGISTRY_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS group_members ("
        "group_id INTEGER NOT NULL, "
        "user_id INTEGER NOT NULL, "
        "first_seen INTEGER NOT NULL, "
        "last_seen INTEGER NOT NULL, "
        "PRIMARY KEY (group_id, user_id))"
    )
    return conn


def _registry_upsert_sync(group_id: int, user_ids: list) -> None:
    now = int(time.time())
    with contextlib.closing(_registry_connect()) as conn:
        with conn:
            conn.executemany(
                "INSERT INTO group_members (group_id, user_id, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET last_seen = excluded.last_seen",
                [(group_id, uid, now, now) for uid in user_ids]
            )


def _registry_forget_sync(group_id: int, user_id: int) -> None:
    with contextlib.closing(_registry_connect()) as conn:
        with conn:
            conn.execute(
                "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            )


def _registry_list_sync(group_id: int) -> list:
    with contextlib.closing(_registry_connect()) as conn:
        rows = conn.execute(
            "SELECT user_id FROM group_members WHERE group_id = ? ORDER BY user_id",
            (group_id,)
        ).fetchall()
    return [row[0] for row in rows]


async def registry_track(group_id: int, user_id: int, force: bool = False) -> None:
    if user_id <= 0 or user_id in SERVICE_ACCOUNT_IDS:
        return

    key = (group_id, user_id)
    now = time.time()
    if not force and now - _REGISTRY_TOUCH.get(key, 0) < _REGISTRY_TOUCH_TTL:
        return

    _REGISTRY_TOUCH[key] = now
    if len(_REGISTRY_TOUCH) > 20000:
        stale = [k for k, ts in _REGISTRY_TOUCH.items() if now - ts > _REGISTRY_TOUCH_TTL]
        for k in stale:
            _REGISTRY_TOUCH.pop(k, None)

    try:
        await asyncio.to_thread(_registry_upsert_sync, group_id, [user_id])
    except Exception as e:
        logger.warning(f"No se pudo registrar a {user_id} en el padrón de {group_id}: {e}")


async def registry_forget(group_id: int, user_id: int) -> None:
    _REGISTRY_TOUCH.pop((group_id, user_id), None)
    try:
        await asyncio.to_thread(_registry_forget_sync, group_id, user_id)
    except Exception as e:
        logger.warning(f"No se pudo retirar a {user_id} del padrón de {group_id}: {e}")


async def registry_members(group_id: int) -> list:
    try:
        return await asyncio.to_thread(_registry_list_sync, group_id)
    except Exception as e:
        logger.error(f"No se pudo leer el padrón de {group_id}: {e}")
        return []


async def _bootstrap_registry_from_sentinel(group_id: int) -> int:
    try:
        def _get_existing():
            with _db_module.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT user_id FROM user_groups WHERE group_id = ? UNION SELECT user_id FROM users", (group_id,))
                return [r[0] for r in cursor.fetchall() if r[0] not in SERVICE_ACCOUNT_IDS]
        ids = await asyncio.to_thread(_get_existing)
        if ids:
            before = len(await registry_members(group_id))
            await asyncio.to_thread(_registry_upsert_sync, group_id, ids)
            after = len(await registry_members(group_id))
            return max(0, after - before)
    except Exception as e:
        logger.warning(f"Bootstrap del padrón falló: {e}")
    return 0


# ==========================================
# 🕵️‍♂️ USERBOT HUNTER
# ==========================================
_USERBOT_ENFORCED: dict = {}
_USERBOT_ENFORCE_COOLDOWN = 300


async def _safe_is_userbot_flagged(user_id: int, group_id: int) -> bool:
    try:
        return bool(await is_userbot_flagged(user_id, group_id))
    except Exception as e:
        logger.error(f"[UserbotHunter] Error consultando flagged_userbots ({group_id}/{user_id}): {e}")
        return False


async def enforce_userbot_flag(
    bot: Bot,
    group_id: int,
    user_id: int,
    username: str = "",
    message: Optional[Message] = None,
    source: str = "mensaje"
) -> bool:
    if is_super_admin(user_id) or user_id in SERVICE_ACCOUNT_IDS:
        return False

    if not await _safe_is_userbot_flagged(user_id, group_id):
        return False

    tier = await get_privilege_tier(bot, group_id, user_id, username)
    if _tier_is_privileged(tier):
        return False

    if message is not None:
        try:
            await message.delete()
        except Exception:
            pass

    key = (group_id, user_id)
    now = time.time()
    if now - _USERBOT_ENFORCED.get(key, 0) < _USERBOT_ENFORCE_COOLDOWN:
        return True

    _USERBOT_ENFORCED[key] = now
    if len(_USERBOT_ENFORCED) > 5000:
        stale = [k for k, ts in _USERBOT_ENFORCED.items() if now - ts > _USERBOT_ENFORCE_COOLDOWN]
        for k in stale:
            _USERBOT_ENFORCED.pop(k, None)

    try:
        await set_participant_mic(chat_id=group_id, user_id=user_id, muted=True, volume=0)
    except Exception:
        pass

    try:
        if USERBOT_ACTION == "ban":
            await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            track_security(group_id, user_id, "antispam", "ban", False)
        elif USERBOT_ACTION == "mute":
            await bot.restrict_chat_member(
                chat_id=group_id, user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            track_security(group_id, user_id, "antispam", "mute", False)
        else:
            track_security(group_id, user_id, "antispam", "delete", False)
        logger.warning(
            f"[UserbotHunter] Cuenta fichada {user_id} neutralizada en {group_id} "
            f"(acción: {USERBOT_ACTION}, origen: {source})."
        )
    except TelegramAPIError as e:
        logger.error(f"[UserbotHunter] No se pudo aplicar '{USERBOT_ACTION}' a {user_id} en {group_id}: {e}")

    return True


# ==========================================
# 🔊 ATENUACIÓN ACÚSTICA (RADAR AUTOLOWER)
# ==========================================
async def _acoustic_attenuate(
    bot: Bot,
    group_id: int,
    user_id: int,
    username: str,
    strikes: int,
    limit: int,
    force_mute: bool = False
) -> None:
    if not await _autolower_can_mute(bot, group_id, user_id, username):
        return

    if force_mute or strikes >= limit:
        muted, volume = True, 0
    elif WARN_PROGRESSIVE_ATTENUATION:
        remaining = max(0, limit - strikes)
        muted, volume = False, int(FULL_VOLUME * remaining / max(1, limit))
    else:
        return

    try:
        await set_participant_mic(chat_id=group_id, user_id=user_id, muted=muted, volume=volume)
    except Exception as e:
        logger.debug(f"AutoLower: sin efecto sobre {user_id} en {group_id}: {e}")


# ==========================================
# ⚖️ ESCALA CENTRALIZADA DE ADVERTENCIAS POR NIVELES
# ==========================================
_REASON_TEXT = {
    "filter": (
        "tu mensaje ha sido retirado por infringir las directivas perimetrales.",
        "your message was removed for violating perimeter directives. Strike logged."
    ),
    "flood": (
        "por favor modera la velocidad de tus mensajes en el chat.",
        "please slow down your message frequency in the chat. Strike logged."
    ),
    "manual": (
        "se ha registrado una advertencia formal por orden de la administración.",
        "a formal strike was logged by community administration."
    ),
    "command": (
        "el uso de comandos públicos está restringido en esta comunidad.",
        "public commands are restricted in this community. Strike logged."
    ),
}

_SANCTION_TEXT = {
    "mute": (
        "🔇", "Auto-Sanction",
        "ha sido silenciado en el chat y videochat por acumulación de faltas.",
        "User has been muted due to reaching the strike threshold."
    ),
    "kick": (
        "👢", "Auto-Sanction",
        "ha sido expulsado temporalmente por infringir las reglas.",
        "User has been kicked due to reaching the strike threshold."
    ),
    "ban": (
        "🚫", "Permanent Ban",
        "ha sido bloqueado permanentemente de la comunidad por faltas reiteradas.",
        "User has been permanently banned from the community."
    ),
}

WARN_ACTIONS = ("mute", "kick", "ban")
# v15 · Motivos legibles de los 30 vectores de la matriz multimedia para la escalera de advertencias.
_REASON_TEXT.update(media_locks.REASON_TEXTS)


def _sync_get_warn_template(chat_id: int) -> dict:
    try:
        with _db_module.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(group_settings)")
            existing = {col[1] for col in cursor.fetchall()}
            target = [c for c in ["warn_custom_text", "warn_custom_media_id", "warn_custom_media_type"] if c in existing]
            if target:
                cursor.execute(f"SELECT {', '.join(target)} FROM group_settings WHERE group_id = ?", (chat_id,))
                row = cursor.fetchone()
                if row:
                    return {c: row[i] for i, c in enumerate(target)}
    except Exception:
        pass
    return {}


async def _get_extended_warn_config(chat_id: int) -> dict:
    return await asyncio.to_thread(_sync_get_warn_template, chat_id)


async def _send_temp(
    bot: Bot, 
    chat_id: int, 
    text: str, 
    ttl: int = 20, 
    reply_to: Optional[Message] = None,
    media_id: Optional[str] = None,
    media_type: Optional[str] = None
) -> None:
    # Anti-reportes: los avisos temporales no son esenciales (presupuesto por minuto; silencio bajo escudo).
    if not raid_shield.notice_allowed(chat_id):
        return
    ttl = await service_ttl(chat_id, ttl)   # v37 · TTL configurado por el administrador
    try:
        sent = None
        if media_id and media_type:
            try:
                if media_type == "photo":
                    sent = await bot.send_photo(chat_id=chat_id, photo=media_id, caption=text, parse_mode="HTML")
                elif media_type == "video":
                    sent = await bot.send_video(chat_id=chat_id, video=media_id, caption=text, parse_mode="HTML")
                elif media_type == "animation":
                    sent = await bot.send_animation(chat_id=chat_id, animation=media_id, caption=text, parse_mode="HTML")
            except Exception as media_err:
                logger.debug(f"Fallo enviando multimedia temporal de advertencia ({media_err}); usando texto.")

        if not sent:
            try:
                if reply_to is not None:
                    sent = await reply_to.answer(text, parse_mode="HTML")
            except TelegramBadRequest as br:
                if "reply" in str(br).lower() or "not found" in str(br).lower():
                    sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                else:
                    raise
            except Exception:
                sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            
            if not sent:
                sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                
        _spawn(auto_delete_msg(sent, ttl))
    except Exception as e:
        logger.debug(f"No se pudo enviar aviso temporal en {chat_id}: {e}")


async def _reset_warnings_safe(chat_id: int, user_id: int) -> None:
    try:
        await reset_user_strikes(chat_id, user_id)
    except Exception:
        pass

    if RESET_WARNS_AFTER_SANCTION and _db_reset_warnings is not None:
        try:
            res = _db_reset_warnings(user_id)
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            logger.debug(f"No se pudieron reiniciar los warns globales de {user_id}: {e}")


async def enforce_warn_ladder(
    bot: Bot,
    chat_id: int,
    target_id: int,
    target_username: str,
    target_mention: str,
    reply_to: Optional[Message] = None,
    reason: str = "filter",
    silent: bool = False
) -> dict:
    tier = await get_privilege_tier(bot, chat_id, target_id, target_username)
    if _tier_is_privileged(tier):
        return {"status": "immune", "tier": tier, "strikes": 0, "limit": 0, "action": None}

    group_tier = (await get_group_tier(chat_id) or "free").lower()
    warns_cfg = await get_warns_config(chat_id)
    limit = max(1, int(warns_cfg.get("limit", 3)))
    action = str(warns_cfg.get("action", "mute")).strip().lower()
    if action not in WARN_ACTIONS:
        action = "mute"

    try:
        strikes = await add_user_strike(chat_id, target_id, reason)
    except Exception as e:
        logger.error(f"Error registrando strike perimetral de {target_id} en {chat_id}: {e}")
        strikes = int(await add_warning(target_id))

    ext_cfg = await _get_extended_warn_config(chat_id)
    custom_text = ext_cfg.get("warn_custom_text")
    media_id = ext_cfg.get("warn_custom_media_id")
    media_type = ext_cfg.get("warn_custom_media_type")

    if reason in _REASON_TEXT:
        es_txt, en_txt = _REASON_TEXT[reason]
    else:
        es_txt = en_txt = reason
    lang = await group_lang(chat_id)
    reason_loc = es_txt if lang == "es" else en_txt

    # CASO A: EL USUARIO AÚN NO ALCANZA EL LÍMITE PUNITIVO
    if strikes < limit:
        await _acoustic_attenuate(bot, chat_id, target_id, target_username, strikes, limit)

        if not silent:
            def _fill(template: str) -> str:
                return (template.replace("{mention}", target_mention).replace("{user}", target_mention)
                        .replace("{user_name}", target_mention).replace("{name}", target_mention)
                        .replace("{strikes}", str(strikes)).replace("{limit}", str(limit)).replace("{reason}", reason_loc))

            if group_tier in ("ultra_pro", "ultra", "pro"):
                body = _fill(custom_text) if custom_text else gtx(lang, "warn_body", signed=False, mention=target_mention,
                                                                   strikes=strikes, limit=limit, reason=reason_loc)
                ultra = group_tier in ("ultra_pro", "ultra")
                notice = gtx(lang, "warn_ultra" if ultra else "warn_pro", strikes=strikes, limit=limit, body=body)
                await _send_temp(bot, chat_id, notice, ttl=20, reply_to=reply_to,
                                 media_id=media_id if ultra else None, media_type=media_type if ultra else None)
            else:
                notice = gtx(lang, "warn", strikes=strikes, limit=limit, mention=target_mention, reason=reason_loc)
                await _send_temp(bot, chat_id, notice, ttl=20, reply_to=reply_to)

        return {"status": "warned", "tier": tier, "strikes": strikes, "limit": limit, "action": action}

    # CASO B: SE ALCANZÓ EL LÍMITE DE ADVERTENCIAS (APLICACIÓN DE CASTIGO)
    await _acoustic_attenuate(bot, chat_id, target_id, target_username, strikes, limit, force_mute=True)

    # ⚖️ v24 · ¿La marca global users.is_banned ya existía ANTES de este baneo (Arquitecto, otra causa)? Se lee antes
    # de ban_user(): una apelación pagada solo retira la marca que puso ESTE baneo, nunca una anterior.
    prior_global_ban = False
    if action == "ban":
        try:
            prior_global_ban = await _db_module.is_user_globally_banned(target_id)
        except Exception:
            prior_global_ban = True   # ante la duda, no se ofrece apelación (se conserva la seguridad)

    try:
        if action == "mute":
            await bot.restrict_chat_member(
                chat_id=chat_id, user_id=target_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            try:
                await set_participant_mic(chat_id=chat_id, user_id=target_id, muted=True, volume=0)
            except Exception:
                pass
        elif action == "kick":
            await bot.ban_chat_member(chat_id=chat_id, user_id=target_id, until_date=int(time.time() + 35))
            await bot.unban_chat_member(chat_id=chat_id, user_id=target_id, only_if_banned=True)
        elif action == "ban":
            await ban_user(target_id)
            await bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
    except Exception as e:
        logger.error(f"Error aplicando sanción '{action}' a {target_id} en {chat_id}: {e}")
        return {"status": "error", "tier": tier, "strikes": strikes, "limit": limit, "action": action}

    # ⚖️ v25 · Origen de la marca global: solo si la activó ESTE baneo (/unban y apelaciones la retiran con seguridad).
    if action == "ban" and not prior_global_ban:
        try:
            await _db_module.record_global_ban_origin(target_id, chat_id, "warn_ladder")
        except Exception as ex:
            logger.debug(f"Aviso registrando el origen del baneo global de {target_id}: {ex}")

    await _reset_warnings_safe(chat_id, target_id)

    if reason != "manual":     # las advertencias manuales son decisiones del administrador, no del sistema
        track_security(chat_id, target_id, "antiflood" if reason == "flood" else "antispam", action, False)

    if action in ("kick", "ban"):
        await registry_forget(chat_id, target_id)

    # ⚖️ v24 · Baneo AUTOMÁTICO por warns_limit (enlaces, lista negra, anti-spam, matriz…): DM privado con el motivo y
    # la oferta de apelación por Stars. Las advertencias manuales de un administrador no son apelables por pago.
    if action == "ban" and reason != "manual":
        _spawn(ban_appeals.on_ladder_ban(
            bot, group_id=chat_id,
            user=types.SimpleNamespace(id=target_id, username=target_username or None, full_name=None, language_code=None),
            reason_key=str(reason), reason_es=es_txt, reason_en=en_txt, strikes=strikes, limit=limit,
            prior_global_ban=prior_global_ban,
        ))

    if not silent:
        badge = " 💎" if group_tier in ("ultra_pro", "ultra") else (" ⭐" if group_tier == "pro" else "")
        sanction_notice = gtx(lang, f"sanction_{action}", badge=badge, strikes=strikes, limit=limit, mention=target_mention)
        use_media = media_id if group_tier in ("ultra_pro", "ultra") else None
        use_type = media_type if group_tier in ("ultra_pro", "ultra") else None
        await _send_temp(bot, chat_id, sanction_notice, ttl=25, reply_to=reply_to, media_id=use_media, media_type=use_type)

    return {"status": "sanctioned", "tier": tier, "strikes": strikes, "limit": limit, "action": action}


# ==========================================
# 🎮 FASE 4: MOTOR DE GAMIFICACIÓN EN TIEMPO REAL
# ==========================================
async def _process_message_reputation(bot: Bot, message: Message):
    """Otorga XP por mensaje legítimo respetando multiplicadores y notifica ascensos."""
    if not message.from_user or message.from_user.is_bot:
        return

    group_id = message.chat.id
    user_id = message.from_user.id
    full_name = message.from_user.full_name or gtx(await group_lang(group_id), "user", signed=False)
    username = message.from_user.username or ""

    rep_result = await add_user_reputation_xp(
        group_id=group_id,
        user_id=user_id,
        full_name=full_name,
        username=username,
        base_xp=10,
        cooldown_seconds=45
    )

    if rep_result.get("leveled_up"):
        new_lvl = rep_result.get("level", 1)
        # ⚡ Radar en vivo: ascenso de nivel (payload compacto, sin I/O)
        publish_radar_event(group_id, "level_up", {
            "u": user_id,
            "n": full_name[:48],
            "l": new_lvl,
            "x": rep_result.get("xp", 0),
        })
        clean_name = html.escape(full_name)
        mention = f"<a href='tg://user?id={user_id}'>{clean_name}</a>"
        lang = await group_lang(group_id)
        levelup_text = gtx(lang, "levelup", mention=mention, level=new_lvl, xp=rep_result.get("xp", 0))
        try:
            lvl_msg = await message.answer(levelup_text, parse_mode="HTML")
            _spawn(auto_delete_msg(lvl_msg, await service_ttl(group_id, 15)))
        except Exception:
            pass


# ==========================================
# 🏆 COMANDOS DE GAMIFICACIÓN & ANALÍTICA (/rank, /top, /heatmap)
# ==========================================
@router.message(Command("rank", "xp", "nivel"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_user_rank(message: Message, command: CommandObject, bot: Bot):
    """Muestra el nivel, XP acumulado, barra de progreso y posición en el ranking."""
    group_id = message.chat.id
    target_user = message.from_user
    
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
    elif command and command.args:
        arg = command.args.split()[0].strip()
        if arg.isdigit():
            try:
                member = await bot.get_chat_member(group_id, int(arg))
                target_user = member.user
            except Exception:
                pass
        elif arg.startswith("@"):
            try:
                chat_info = await bot.get_chat(arg)
                target_user = chat_info
            except Exception:
                pass

    if not target_user:
        return

    rep = await get_user_reputation(group_id, target_user.id)
    xp = rep.get("xp", 0)
    level = rep.get("level", 1)
    rank_pos = rep.get("rank", 1)

    # Cálculo visual de la barra de progreso
    prev_xp = ((level - 1) ** 2) * 100
    next_xp = (level ** 2) * 100
    diff = max(1, next_xp - prev_xp)
    current_in_level = max(0, xp - prev_xp)
    pct = min(100, int((current_in_level / diff) * 100))
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    lang = await group_lang(group_id)
    clean_name = html.escape(getattr(target_user, "full_name", getattr(target_user, "first_name", gtx(lang, "user", signed=False))))
    mention = f"<a href='tg://user?id={target_user.id}'>{clean_name}</a>"

    rank_card = gtx(lang, "rank_card", title=html.escape(message.chat.title or gtx(lang, "community", signed=False)),
                    mention=mention, pos=rank_pos, level=level, xp=xp, bar=bar, pct=pct,
                    current=current_in_level, diff=diff, next=level + 1)

    try:
        sent = await message.answer(rank_card, parse_mode="HTML")
        _spawn(auto_delete_msg(sent, await service_ttl(group_id, 25)))
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Aviso en /rank: {e}")


@router.message(Command("top", "leaderboard", "ranking"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_top_ranking(message: Message, bot: Bot):
    """Muestra el Top 10 de usuarios con mayor nivel y XP en la comunidad."""
    group_id = message.chat.id
    top_list = await get_top_reputation(group_id, limit=10)
    lang = await group_lang(group_id)

    if not top_list:
        msg = await message.answer(gtx(lang, "top_empty"), parse_mode="HTML")
        _spawn(auto_delete_msg(msg, await service_ttl(group_id, 15)))
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = []
    for idx, item in enumerate(top_list):
        medal = medals[idx] if idx < len(medals) else f"#{idx+1}"
        name = html.escape(item["name"])
        lines.append(gtx(lang, "top_line", signed=False, medal=medal, name=name, level=item["level"], xp=item["xp"]))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "btn_top", signed=False), url=f"{WEBAPP_URL}?chat_id={group_id}")]
    ])

    top_card = gtx(lang, "top_card", title=html.escape(message.chat.title or ""), lines="\n".join(lines))

    try:
        sent = await message.answer(top_card, reply_markup=kb, parse_mode="HTML")
        _spawn(auto_delete_msg(sent, await service_ttl(group_id, 35)))
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Aviso en /top: {e}")


@router.message(Command("heatmap", "horas", "densidad"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_chat_heatmap(message: Message, bot: Bot):
    """Muestra un resumen táctico de las horas pico de mensajes de la comunidad."""
    group_id = message.chat.id
    # 👑 Analítica de la comunidad: exclusiva del Dueño legítimo o del Arquitecto.
    if not await _require_owner_message(bot, message):
        return

    data = await get_chat_heatmap_matrix(group_id)
    matrix = data.get("matrix", {})
    days = data.get("days", [])

    lang = await group_lang(group_id)
    try:
        local_days = list(_db_module.weekday_names(lang))
    except Exception:
        local_days = list(days)
    # Identificar la hora pico global y el día de mayor actividad
    peak_day = gtx(lang, "heatmap_default_day", signed=False)
    peak_hour = 12
    max_count = 0
    total_msgs = 0

    for day_num, hours in matrix.items():
        for hour, count in hours.items():
            total_msgs += count
            if count > max_count:
                max_count = count
                peak_day = (local_days[day_num - 1] if 1 <= day_num <= len(local_days)
                            else gtx(lang, "heatmap_unknown_day", signed=False))
                peak_hour = hour

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gtx(lang, "btn_heatmap", signed=False), url=f"{WEBAPP_URL}?chat_id={group_id}")]
    ])

    report = gtx(lang, "heatmap", title=html.escape(message.chat.title or ""), total=total_msgs, day=peak_day,
                 h1=peak_hour, h2=(peak_hour + 1) % 24, peak=max_count)

    try:
        sent = await message.answer(report, reply_markup=kb, parse_mode="HTML")
        _spawn(auto_delete_msg(sent, await service_ttl(group_id, 40)))
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Aviso en /heatmap: {e}")


# ==========================================
# 🎮 ANUNCIO RECURRENTE DE GAMIFICACIÓN (ACCESO DESDE EL GRUPO)
# ==========================================
_GAMI_GROUP_TEXTS = {
    "es": {
        "status": (
            "🎮 <b>Anuncio Recurrente de Gamificación</b>\n\n"
            "• <b>Licencia:</b> {tier}\n"
            "• <b>Estado:</b> {status}\n"
            "• <b>Frecuencia:</b> cada {interval}\n"
            "• <b>Próximo envío:</b> {next}\n\n"
            "Configura texto bilingüe, multimedia y botón interactivo desde la consola privada.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locked": (
            "🎮 <b>Anuncio Recurrente de Gamificación</b>\n\n"
            "🔒 Disponible desde el plan <b>PRO ⭐</b> (ULTRA PRO 💎 añade multimedia y frecuencia desde 30 min).\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_open": "⚙️ Configurar en privado",
        "on": "🟢 Activo",
        "off": "🔴 Inactivo",
        "none": "—",
        "now_ok": "🚀 La tarjeta se publicará en el próximo ciclo (≈1 min).\n\n🛡️ <i>Cloud Media Management</i>",
        "now_inactive": "⚠️ El anuncio está inactivo. Actívalo desde la consola privada.\n\n🛡️ <i>Cloud Media Management</i>",
        "off_ok": "🔴 Anuncio de gamificación desactivado.\n\n🛡️ <i>Cloud Media Management</i>",
        "admin_only": "⛔ Acceso Denegado: solo el <b>Dueño</b> de la comunidad puede gestionar el anuncio de gamificación.\n\n🛡️ <i>Cloud Media Management</i>",
    },
    "en": {
        "status": (
            "🎮 <b>Recurring Gamification Announcement</b>\n\n"
            "• <b>License:</b> {tier}\n"
            "• <b>Status:</b> {status}\n"
            "• <b>Frequency:</b> every {interval}\n"
            "• <b>Next send:</b> {next}\n\n"
            "Configure bilingual text, media and the interactive button from the private console.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locked": (
            "🎮 <b>Recurring Gamification Announcement</b>\n\n"
            "🔒 Available from the <b>PRO ⭐</b> plan (ULTRA PRO 💎 adds media and frequency from 30 min).\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_open": "⚙️ Configure privately",
        "on": "🟢 Active",
        "off": "🔴 Inactive",
        "none": "—",
        "now_ok": "🚀 The card will be published in the next cycle (≈1 min).\n\n🛡️ <i>Cloud Media Management</i>",
        "now_inactive": "⚠️ The announcement is inactive. Enable it from the private console.\n\n🛡️ <i>Cloud Media Management</i>",
        "off_ok": "🔴 Gamification announcement disabled.\n\n🛡️ <i>Cloud Media Management</i>",
        "admin_only": "⛔ Access Denied: only the community <b>Owner</b> can manage the gamification announcement.\n\n🛡️ <i>Cloud Media Management</i>",
    },
}


def _gami_group_interval_label(minutes: int, lang: str) -> str:
    minutes = int(minutes or 0)
    if minutes and minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} día" + ("s" if days > 1 else "") if lang == "es" else f"{days} day" + ("s" if days > 1 else "")
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


@router.message(Command("gamification", "gamificacion", "anuncio"), F.chat.type.in_({"group", "supergroup"}))
async def gamification_announcement_command(message: Message, bot: Bot):
    """
    /gamification            → estado del anuncio + acceso directo a la consola privada.
    /gamification now|ahora  → adelanta la próxima publicación al siguiente ciclo del worker.
    /gamification off        → desactiva el anuncio de inmediato.
    Uso exclusivo del Dueño legítimo (creador) o del Arquitecto; la edición completa (texto ES/EN, multimedia, botón, frecuencia)
    se realiza en la consola privada, que vuelve a verificar la propiedad de la comunidad.
    """
    group_id = message.chat.id
    lang = await group_lang(group_id)
    gt = _GAMI_GROUP_TEXTS[lang]

    try:
        await message.delete()
    except Exception:
        pass

    # 👑 Regla de Oro: exclusivo del Dueño legítimo o del Arquitecto (administradores comunes y anónimos fuera).
    if not await _message_author_is_owner(bot, message):
        logger.warning(f"⛔ [Aduana] /gamification bloqueado en {group_id}: el autor no es el Dueño.")
        if message.sender_chat is not None and message.sender_chat.id == group_id:
            warn = await message.answer(gtx(lang, "anon_admin"), parse_mode="HTML")
            _spawn(auto_delete_msg(warn, await service_ttl(group_id, 8)))
        return   # v37 · Anti-Rogue: sin aviso público para quien no es el Dueño

    args = (message.text or "").split()[1:]
    sub = args[0].lower() if args else ""

    if sub in ("now", "ahora"):
        scheduled = await schedule_gamification_announcement_now(group_id)
        reply = await message.answer(gt["now_ok"] if scheduled else gt["now_inactive"], parse_mode="HTML")
        _spawn(auto_delete_msg(reply, await service_ttl(group_id, 15)))
        return

    if sub in ("off", "apagar", "desactivar"):
        await set_gamification_announcement_field(group_id, "status", 0)
        logger.info(f"🎮 [Gamificación] Anuncio desactivado desde el grupo {group_id}.")
        reply = await message.answer(gt["off_ok"], parse_mode="HTML")
        _spawn(auto_delete_msg(reply, await service_ttl(group_id, 15)))
        return

    tier = str(await get_group_tier(group_id) or "free").lower()
    try:
        bot_username = (await bot.get_me()).username or ""
    except Exception:
        bot_username = ""
    open_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=gt["btn_open"], url=f"https://t.me/{bot_username}?start=gami_{group_id}")
    ]]) if bot_username else None

    if tier not in ("pro", "ultra_pro", "ultra"):
        text = gt["locked"]
    else:
        cfg = await get_gamification_announcement(group_id)
        text = gt["status"].format(
            tier="ULTRA PRO 💎" if "ultra" in tier else "PRO ⭐",
            status=gt["on"] if cfg.get("status") == 1 else gt["off"],
            interval=_gami_group_interval_label(cfg.get("interval_minutes") or 360, lang),
            next=f"{cfg['next_send_at']} UTC" if (cfg.get("status") == 1 and cfg.get("next_send_at")) else gt["none"],
        )

    reply = await message.answer(text, reply_markup=open_kb, parse_mode="HTML")
    _spawn(auto_delete_msg(reply, await service_ttl(group_id, 30)))


# ==========================================
# 🚨🌐 v37 · /antiraid · /lang · PERMISO DE CONFIGURACIÓN
# ==========================================
async def has_config_rights(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    v37 · Permiso de configuración: Dueño legítimo / Arquitecto, o administrador con «Añadir administradores»
    (can_promote_members). Se usa SOLO para Anti-Raid, idioma y TTL de servicio (todos los rangos).
    """
    if not user_id:
        return False
    if await _is_community_owner(bot, chat_id, user_id):
        return True
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=user_id), timeout=5)
    except Exception:
        return False
    status = str(getattr(member.status, "value", member.status) or "")
    return status == "administrator" and bool(getattr(member, "can_promote_members", False))


_RAID_PENALTY_NAMES = {"captcha": ("Captcha estricto", "Strict captcha"), "mute": ("Silencio", "Mute"),
                       "kick": ("Expulsión", "Kick"), "ban": ("Baneo", "Ban")}


@router.message(Command("antiraid", "raid"), F.chat.type.in_({"group", "supergroup"}))
async def antiraid_command(message: Message, bot: Bot):
    """Estado del Anti-Raid + acceso al panel privado. Solo Dueño o admin con «Añadir administradores»."""
    group_id = message.chat.id
    with contextlib.suppress(Exception):
        await message.delete()
    user = message.from_user
    if message.sender_chat is not None or not user or not await has_config_rights(bot, group_id, user.id):
        return   # Anti-Rogue: silencio total
    lang = await group_lang(group_id)
    cfg = await raid_cfg(group_id)
    username = await _bot_username(bot)
    penalty = _RAID_PENALTY_NAMES.get(cfg["penalty"], ("—", "—"))[0 if lang == "es" else 1]
    text = gtx(lang, "raid_status", status=gtx(lang, "on" if cfg["enabled"] else "off", signed=False),
               min=cfg["join_threshold"], max=cfg["critical_threshold"], window=cfg["window_seconds"], penalty=penalty)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=gtx(lang, "btn_open_raid", signed=False), url=f"https://t.me/{username}?start=raidcfg_{group_id}")]])
    sent = await bot.send_message(chat_id=group_id, text=text, reply_markup=kb, parse_mode="HTML")
    _spawn(auto_delete_msg(sent, await service_ttl(group_id, 30)))


@router.message(Command("lang", "language", "idioma"), F.chat.type.in_({"group", "supergroup"}))
async def group_lang_command(message: Message, bot: Bot):
    """Selector de idioma: admins con permisos → idioma del grupo; miembros → idioma personal (privado)."""
    group_id = message.chat.id
    with contextlib.suppress(Exception):
        await message.delete()
    lang = await group_lang(group_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=("✅ " if lang == "en" else "") + "🇺🇸 English", callback_data=f"glang_{group_id}_en"),
        InlineKeyboardButton(text=("✅ " if lang == "es" else "") + "🇪🇸 Español", callback_data=f"glang_{group_id}_es"),
    ]])
    sent = await bot.send_message(chat_id=group_id, text=gtx(lang, "lang_pick"), reply_markup=kb, parse_mode="HTML")
    _spawn(auto_delete_msg(sent, await service_ttl(group_id, 30)))


@router.callback_query(F.data.startswith("glang_"))
async def group_lang_callback(callback: CallbackQuery, bot: Bot):
    parts = (callback.data or "").split("_")
    try:
        group_id, code = int(parts[1]), parts[2]
    except (IndexError, ValueError):
        await callback.answer()
        return
    if code not in ("en", "es"):
        await callback.answer()
        return
    user = callback.from_user
    if await has_config_rights(bot, group_id, user.id):
        await set_service_lang(group_id, code)
        invalidate_service_prefs(group_id)
        _sync_miniapp(group_id, ["service_lang"], source="group_command")
        await callback.answer(gtx(code, "lang_set_group", signed=False), show_alert=True)
        if isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.delete()
        return
    await set_user_lang(user.id, code, user.username, user.full_name)
    with contextlib.suppress(Exception):
        from handlers.user_private import forget_user_lang_cache
        forget_user_lang_cache(user.id)
    await callback.answer(gtx(code, "lang_set_user", signed=False), show_alert=True)


# ==========================================
# 🛡️ MATRIZ PERIMETRAL DE MENSAJES & SEGURIDAD
# ==========================================
@router.message(F.chat.type.in_({"group", "supergroup"}))
@router.edited_message(F.chat.type.in_({"group", "supergroup"}))
async def group_security_matrix(message: Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return

    group_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or ""
    text_content = message.text or message.caption or ""
    is_edit = message.edit_date is not None

    await registry_track(group_id, user_id)

    if await enforce_userbot_flag(bot, group_id, user_id, username, message=message, source="mensaje"):
        return

    tier = await get_privilege_tier(bot, group_id, user_id, username)
    if _tier_is_privileged(tier):
        # 🔗 v23 · Los administradores comunes siguen sujetos a su cuota diaria de enlaces (si el dueño la activó).
        if tier == "admin" and await link_quotas.enforce_admin_link_quota(bot, message, tier, is_edit=is_edit):
            return
        # Miembros inmunes también registran densidad horaria
        _spawn(record_hourly_chat_activity(group_id))
        return

    # 👑 v23 · Privilegios VIP vigentes (caché 60 s): exenciones parciales de la matriz y margen anti-flood.
    vip_perks = await vip_roles.get_active_perks(group_id, user_id)

    # 1. Cerradura de Comandos
    if text_content.startswith("/") and await get_lock_status(group_id, "lock_commands") == 1:
        try:
            await message.delete()
        except Exception:
            pass

        await enforce_warn_ladder(
            bot, group_id, user_id, username, message.from_user.mention_html(),
            reply_to=message, reason="command", silent=True
        )
        return

    is_threat_detected = False
    lower_text = text_content.lower()

    # Extracción profunda de enlaces dentro de entidades para evitar evasiones con hipervínculos
    has_link_entity = False
    entities = (message.entities or []) + (message.caption_entities or [])
    for ent in entities:
        if ent.type == "url":
            has_link_entity = True
            break
        elif ent.type == "text_link" and ent.url:
            has_link_entity = True
            lower_text += " " + ent.url.lower()

    # 2. 🔒 v15 · Matriz granular de bloqueo multimedia (30 vectores, acción propia por vector).
    #    Sustituye a las 4 cerraduras binarias: lock_media / lock_stickers / lock_links siguen gobernando sus
    #    vectores (intención del dueño ↔ matriz) y, durante el Pánico o el modo nocturno, actúan como piso
    #    mínimo Warn + borrado. Se aplica IGUAL a los mensajes nuevos y a los EDITADOS: una edición que
    #    inyecta enlaces, spoilers, botones, formato abusivo o palabras de la lista negra se sanciona con la
    #    regla del vector 17 (edited_msg) además de la regla propia de cada vector.
    if await media_locks.inspect_and_enforce(
        bot, message, is_edit=is_edit, blacklist=(await get_blacklist()) if is_edit else None,
        exempt=vip_perks["exempt"] if vip_perks else None,
    ):
        return

    # 3. Lista Negra (Blacklist Alfanumérica)
    if not is_threat_detected:
        blacklist = await get_blacklist()
        for b_word in blacklist:
            if b_word and b_word in lower_text:
                is_threat_detected = True
                break

    # 4. Filtros Perimetrales Anti-Spam
    if not is_threat_detected:
        if await get_antispam_filter(group_id, "tg_links") == 1 and ("t.me/" in lower_text or "telegram.me/" in lower_text):
            is_threat_detected = True
        elif await get_antispam_filter(group_id, "web_links") == 1 and (has_link_entity or "http://" in lower_text or "https://" in lower_text or "www." in lower_text):
            if "t.me/" not in lower_text and "telegram.me/" not in lower_text:
                is_threat_detected = True
        elif await get_antispam_filter(group_id, "quotes") == 1 and (message.quote or message.reply_to_message):
            if message.quote is not None:
                quoted_text = message.quote.text or ""
            elif message.reply_to_message is not None:
                quoted_text = message.reply_to_message.text or ""
            else:
                quoted_text = ""
            if "http://" in quoted_text.lower() or "https://" in quoted_text.lower() or "t.me/" in quoted_text.lower():
                is_threat_detected = True
        elif await get_antispam_filter(group_id, "forwards") == 1 and message.forward_origin:
            origin_type = type(message.forward_origin).__name__
            if "Channel" in origin_type and await get_antispam_filter(group_id, "fwd_channels") == 1: 
                is_threat_detected = True
            elif "User" in origin_type and await get_antispam_filter(group_id, "fwd_users") == 1: 
                is_threat_detected = True
            elif "Chat" in origin_type and await get_antispam_filter(group_id, "fwd_groups") == 1: 
                is_threat_detected = True
            elif "Bot" in origin_type and await get_antispam_filter(group_id, "fwd_bots") == 1: 
                is_threat_detected = True

    if is_threat_detected:
        try: 
            await message.delete()
            track_security(group_id, user_id, "antispam", "delete", False)
        except Exception: 
            pass

        await enforce_warn_ladder(
            bot, group_id, user_id, username, message.from_user.mention_html(),
            reply_to=message, reason="filter"
        )
        return

    # Las ediciones YA pasaron por la matriz multimedia, la lista negra y el anti-spam. Aquí se detienen a
    # propósito: una edición no es un mensaje nuevo, así que no cuenta para el anti-flood (evita falsos
    # positivos al corregir un texto) ni suma XP (evita farmear reputación editando).
    if is_edit:
        return

    # 5. Escudo Anti-Flood
    af_cfg = await get_antiflood_config(group_id)
    max_msgs, time_window, af_action = af_cfg["msgs"], af_cfg["time"], af_cfg["action"]
    if vip_perks and int(vip_perks.get("flood_bonus") or 1) > 1:
        max_msgs = int(max_msgs) * int(vip_perks["flood_bonus"])   # 👑 v23 · interacción privilegiada

    if af_action != "off":
        now = time.time()
        cache_key = (group_id, user_id)
        
        if cache_key not in FLOOD_CACHE: 
            FLOOD_CACHE[cache_key] = []
        
        FLOOD_CACHE[cache_key] = [t for t in FLOOD_CACHE[cache_key] if now - t < time_window]
        FLOOD_CACHE[cache_key].append(now)

        if len(FLOOD_CACHE) > 500:
            keys_to_delete = [k for k, v in FLOOD_CACHE.items() if not v or (now - v[-1] > 60)]
            for k in keys_to_delete:
                del FLOOD_CACHE[k]

        if len(FLOOD_CACHE[cache_key]) > max_msgs:
            FLOOD_CACHE[cache_key] = [] 
            try:
                if af_cfg["delete"] == 1: 
                    await message.delete()
                user_mention = message.from_user.mention_html()

                if af_action == "warn":
                    await enforce_warn_ladder(
                        bot, group_id, user_id, username, user_mention,
                        reply_to=message, reason="flood"
                    )
                    return

                if af_action in ("kick", "mute", "ban"):
                    await _acoustic_attenuate(bot, group_id, user_id, username, strikes=1, limit=1, force_mute=True)

                if af_action == "kick":
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=int(time.time() + 35))
                    await bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
                    await registry_forget(group_id, user_id)
                    track_security(group_id, user_id, "antiflood", "kick", False)
                    await _send_temp(bot, group_id, gtx(await group_lang(group_id), "flood_kick", mention=user_mention),
                                     ttl=20, reply_to=message)
                elif af_action == "mute":
                    await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                    track_security(group_id, user_id, "antiflood", "mute", False)
                    await _send_temp(bot, group_id, gtx(await group_lang(group_id), "flood_mute", mention=user_mention),
                                     ttl=20, reply_to=message)
                elif af_action == "ban":
                    await ban_user(user_id)
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                    await registry_forget(group_id, user_id)
                    track_security(group_id, user_id, "antiflood", "ban", False)
                    await _send_temp(bot, group_id, gtx(await group_lang(group_id), "flood_ban", mention=user_mention),
                                     ttl=25, reply_to=message)
            except Exception as e:
                logger.error(f"Error aplicando sanción anti-flood ({group_id}/{user_id}): {e}")
            return

    # 6. MENSAJE LEGÍTIMO VERIFICADO: PROCESAMIENTO DE FASE 4 (XP & HEATMAPS)
    _spawn(record_hourly_chat_activity(group_id))
    _spawn(_process_message_reputation(bot, message))


async def add_speaker_to_queue(group_id: int, user_id: int, full_name: str = "Speaker", username: str = "", stars_paid: int = 0):
    return await add_to_speaker_queue(group_id, user_id, full_name, username, stars_paid)