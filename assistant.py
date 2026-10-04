"""
assistant.py — The Bunker OS (Aiogram 3.x / Pyrogram)

Núcleo de supervisión de voz 24/7, Radar Acústico MTProto, Guardián Mistral AI,
Gestión de Sesiones Propias y Bucles Autónomos de Automatización (Modo Nocturno & VC Scheduler).
The Bunker Command OS © 2026 — Cloud Media Management
"""
import asyncio
import logging
import random
import os
import time
import json
import html
import re
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.enums import ChatMembersFilter, ChatType, ChatAction
from pyrogram.errors import (
    FloodWait, RPCError, Unauthorized,
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    PhoneNumberInvalid, PhoneNumberBanned, PhoneNumberFlood,
    PasswordHashInvalid, PeerIdInvalid,
    AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan
)
from pyrogram.raw.types import (
    PeerUser, InputPeerUser, InputGroupCall, DataJSON,
    InputChannel, InputPeerChannel, InputPeerChat
)
from pyrogram.raw.functions.channels import GetFullChannel
from pyrogram.raw.functions.messages import GetFullChat
from pyrogram.raw.functions.phone import (
    EditGroupCallParticipant,
    GetGroupParticipants,
    JoinGroupCall,
    CreateGroupCall,
    DiscardGroupCall
)
from database.database import (
    is_vip_mic_active, is_group_approved, approve_group,
    get_autolower_status, is_whitelisted,
    get_all_active_sessions, get_session_by_group,
    get_all_active_vc_schedules, update_vc_call_status,
    get_radar_config, revoke_owner_session, save_owner_session,
    get_screen_shield_status, set_screen_shield_status,
    get_podcast_config, set_podcast_mode, set_podcast_duck_volume,
    get_night_mode_config, is_night_mode_time,
    get_lock_status, get_db_connection,
    activate_universal_night_mode,
    deactivate_universal_night_mode,
    flag_userbot, is_userbot_flagged,
    get_ai_sentinel_config,
    get_ghost_purge_config,
    update_ghost_purge_scan_time,
    get_mic_vip_price,
    get_group_tier,
    get_sentinel_service_messages_config,
    save_ai_chat_context,
    get_ai_chat_context,
    clear_ai_chat_context,
    record_hourly_chat_activity,
    add_user_reputation_xp
)

# 🤖 Integración de Mistral AI para el Guardián de Voz y Copiloto
try:
    from importlib import import_module
    Mistral = import_module("mistralai").Mistral
except ImportError:
    Mistral = None

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
mistral_client = Mistral(api_key=MISTRAL_API_KEY) if (Mistral and MISTRAL_API_KEY) else None

try:
    from database.database import get_sentinel_payload_config
except ImportError:
    async def get_sentinel_payload_config(chat_id: int) -> dict:
        return {"enabled": 0, "text": None, "media_id": None, "media_type": None, "auto_delete_after": None}

FATAL_SESSION_ERRORS = (Unauthorized, AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan)

logger = logging.getLogger("assistant_radar")

# ==========================================
# 📢 CONSTANTES Y PLANTILLAS MULTILINGÜES
# ==========================================
SCREEN_SHIELD_ALERT_TEXT = (
    "🎥 <b>The Bunker Bot: Escudo Antinota Activado</b>\n\n"
    "Se detectó una transmisión de pantalla no autorizada por parte de <b>{user_name}</b>. "
    "La señal fue cortada y la cuenta fue retirada de la sala de inmediato para proteger a la comunidad.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

NOISE_SHIELD_ALERT_TEXT = (
    "🔇 <b>The Bunker Bot: Escudo Antirruido Activado</b>\n\n"
    "<b>{user_name}</b> fue silenciado automáticamente tras detectar picos de ruido anómalos y repetidos.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

GHOST_PURGE_ALERT_TEXT = (
    "💀 <b>The Bunker Bot: Ghost Purge Completado</b>\n\n"
    "• Cuentas Fantasma / Eliminadas detectadas: <b>{found}</b>\n"
    "• Cuentas purgadas exitosamente: <b>{purged}</b>\n"
    "• Acción ejecutada: <code>{action}</code>\n\n"
    "🛡️ <i>Perímetro depurado y optimizado — Cloud Media Management</i>"
)

VC_START_TEXTS = {
    "es": (
        "EL VIDEO CHAT DE ⚜️🔐The Búnker Chat🔐⚜️ HA INICIADO CON ÉXITO AHORA, TODOS ESTÁN BIENVENIDOS A PARTICIPAR 🔥🐽💨🚀\n\n"
        "🔇 <b>SE HA ESTABLECIDO POR DEFECTO UN VOLUMEN MÁXIMO DEL 2% PARA TODOS LOS MIEMBROS EN GENERAL QUE INGRESAN AL VIDEO CHAT.</b>\n\n"
        "⚜️ ¿QUIERES CONVERTIRTE EN MIEMBRO VIP Y DESBLOQUEAR EL 100% DEL VOLUMEN DE TU 🎙️MICRÓFONO🎙️ AL PARTICIPAR EN NUESTRO VIDEO CHAT?\n\n"
        "Usa los siguientes botones para activar tu /micvip usando tus TELEGRAM STARS ↓ ↓ ↓\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "en": (
        "THE VOICE CHAT FOR ⚜️🔐The Búnker Chat🔐⚜️ HAS STARTED! EVERYONE IS WELCOME TO JOIN 🔥🐽💨🚀\n\n"
        "🔇 <b>A DEFAULT MAXIMUM VOLUME OF 2% HAS BEEN SET FOR ALL GENERAL MEMBERS JOINING THE VOICE CHAT.</b>\n\n"
        "⚜ WANT TO BECOME A VIP MEMBER AND UNLOCK 100% VOLUME ON YOUR 🎙️MIC🎙️ WHILE PARTICIPATING IN OUR VOICE CHAT?\n\n"
        "Use the buttons below to activate your /micvip with TELEGRAM STARS ↓ ↓ ↓\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
}

VC_MEMBER_JOIN_TEXTS = {
    "es": (
        "UN NUEVO MIEMBRO SE HA UNIDO AL VC DE THE BÚNKER CHAT.\n\n"
        "🔇 {user_name}, el volumen de tu micrófono se ha establecido por defecto a un máximo del 2%.\n\n"
        "¿QUIERES CONVERTIRTE EN MIEMBRO VIP Y ACTIVAR EL VOLUMEN DE TU MICRÓFONO AL 100% DE CAPACIDAD?\n\n"
        "Usa los siguientes botones para obtener tu ⚜️MIC🎙️VIP⚜️\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "en": (
        "A NEW MEMBER HAS JOINED THE BÚNKER CHAT VC.\n\n"
        "🔇 {user_name}, your microphone volume has been set to a maximum of 2% by default.\n\n"
        "DO YOU WANT TO BECOME A VIP MEMBER AND UNLOCK YOUR MICROPHONE VOLUME AT 100% CAPACITY?\n\n"
        "Use the buttons below to get your ⚜️MIC🎙️VIP⚜️\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
}

OPTIMIZATION_TEXT = (
    "🔄 <b>Protocolo de Optimización Audiovisual — The Bunker</b>\n\n"
    "Estamos realizando una optimización de rutina en segundo plano para refrescar cámaras, purgar la transmisión y garantizar máxima fluidez sin retrasos.\n\n"
    "⚡ <i>La sala se reiniciará en 3 segundos y se abrirá limpia de inmediato. Los pases VIP se mantendrán activos al reconectarse.</i>\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

VC_SCHED_MESSAGES = {
    "start": (
        "📡 <b>Apertura Programada — The Bunker</b>\n\n"
        "El videochat de la comunidad ha sido abierto automáticamente según el cronograma ULTRA PRO.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "end": (
        "📡 <b>Cierre Programado — The Bunker</b>\n\n"
        "El ciclo programado de videochat ha concluido. La sala ha sido cerrada de forma ordenada.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
}

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS Y SERVICIO
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

SERVICE_ACCOUNT_IDS = {777000, 1087968824, 136817688}

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


DEFAULT_API_ID = int(os.getenv("TELEGRAM_API_ID", os.getenv("API_ID", "0")))
DEFAULT_API_HASH = os.getenv("TELEGRAM_API_HASH", os.getenv("API_HASH", ""))

MASTER_SESSION = os.getenv("MASTER_SESSION", "").strip()

if MASTER_SESSION:
    assistant_app = Client(
        "assistant_session",
        session_string=MASTER_SESSION,
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )
else:
    assistant_app = None

_global_bot = None
_default_my_id = None


def get_assistant_bot_id() -> int:
    global _default_my_id
    if _default_my_id:
        return _default_my_id
    if assistant_app and getattr(assistant_app, "me", None):
        _default_my_id = assistant_app.me.id
        return _default_my_id
    return 0


active_sentinels = {}
admin_caches = {}  
ADMIN_CACHE_TTL = 300
pending_auth_sessions = {}
_forbidden_strikes = {}
_autolower_cooldowns = {}
FORBIDDEN_STRIKE_LIMIT = 3
FORBIDDEN_COOLDOWN_SECONDS = 900  
_screen_shield_flagged = {}
_noise_unmute_history = {}
NOISE_SPIKE_WINDOW_SECONDS = 12
NOISE_SPIKE_STRIKE_LIMIT = 3
_last_mute_state = {}
_sentinel_payload_last_sent = {}
SENTINEL_PAYLOAD_MIN_GAP_SECONDS = 60
_sentinel_launch_locks = {}
_sentinel_launch_semaphore = asyncio.Semaphore(4)
_pinned_vc_messages = {}
_last_vc_notice = {}
_vc_notice_locks = {}

_call_rights_cache: dict[int, tuple[bool, float]] = {}
_last_join_attempt: dict[int, float] = {}
CALL_RIGHTS_TTL_SECONDS = 300
JOIN_RETRY_SECONDS = 60


async def _has_manage_calls_right(client: Client, chat_id: int) -> bool:
    now = time.time()
    cached = _call_rights_cache.get(chat_id)
    if cached and (now - cached[1]) < CALL_RIGHTS_TTL_SECONDS:
        return cached[0]
    try:
        user_target = client.me.id if getattr(client, "me", None) else "me"
        me = await client.get_chat_member(chat_id, user_target)
        status_val = str(getattr(me.status, "value", me.status)).lower()
        if status_val == "owner":
            allowed = True
        elif status_val == "administrator":
            priv = getattr(me, "privileges", None)
            allowed = bool(priv and getattr(priv, "can_manage_video_chats", False))
        else:
            allowed = False
    except Exception as e:
        logger.debug(f"No se pudo verificar permisos de videochat en {chat_id}: {e}")
        return cached[0] if cached else False
    _call_rights_cache[chat_id] = (allowed, now)
    return allowed


def _invalidate_call_rights(chat_id: int) -> None:
    _call_rights_cache.pop(chat_id, None)


async def _fetch_all_participants(client: Client, call, max_pages: int = 20):
    participants: list = []
    users_map: dict = {}
    offset = ""
    for _ in range(max_pages):
        res = await client.invoke(
            GetGroupParticipants(call=call, ids=[], sources=[], offset=offset, limit=100)
        )
        participants.extend(getattr(res, "participants", []) or [])
        for u in getattr(res, "users", []) or []:
            users_map[u.id] = u
        offset = getattr(res, "next_offset", "") or ""
        if not offset:
            break
    return participants, users_map


def _get_launch_lock(group_id: int) -> asyncio.Lock:
    lock = _sentinel_launch_locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _sentinel_launch_locks[group_id] = lock
    return lock


def _get_group_now(tz_name: str = None) -> datetime:
    tz_str = tz_name or os.getenv("BOT_TIMEZONE", "America/Bogota")
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_str))
    except Exception:
        return datetime.now()


async def _ensure_connected(client: Client, retries: int = 2, delay: float = 1.0) -> bool:
    for attempt in range(retries):
        if client.is_connected:
            return True
        try:
            await asyncio.wait_for(client.connect(), timeout=8.0)
            return True
        except Exception as e:
            logger.debug(f"Reintento de conexión ({attempt + 1}/{retries}) falló: {e}")
            await asyncio.sleep(delay)
    return client.is_connected


def _register_forbidden_strike(chat_id: int, action_label: str) -> int:
    current = _forbidden_strikes.get(chat_id, 0) + 1
    _forbidden_strikes[chat_id] = current
    logger.warning(f"⚠️ [Strike prohibido] Grupo {chat_id}: {action_label} (intento {current}/{FORBIDDEN_STRIKE_LIMIT})")

    if current >= FORBIDDEN_STRIKE_LIMIT:
        _autolower_cooldowns[chat_id] = asyncio.get_event_loop().time() + FORBIDDEN_COOLDOWN_SECONDS
        logger.warning(f"⏳ [Cooldown activado] Grupo {chat_id} bloqueado por {FORBIDDEN_COOLDOWN_SECONDS}s.")
    return current


def _register_noise_strike(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now_ts = asyncio.get_event_loop().time()
    history = _noise_unmute_history.setdefault(key, [])
    history = [ts for ts in history if now_ts - ts <= NOISE_SPIKE_WINDOW_SECONDS]
    history.append(now_ts)
    _noise_unmute_history[key] = history

    if len(history) >= NOISE_SPIKE_STRIKE_LIMIT:
        history.clear()
        _noise_unmute_history[key] = history
        return True
    return False


async def _is_night_active(chat_id: int) -> tuple[bool, str]:
    try:
        cfg = await get_night_mode_config(chat_id)
        if not cfg or cfg.get("status") != 1:
            return False, "disabled"
        start_str = cfg.get("start", "22:00")
        end_str = cfg.get("end", "06:00")
        in_night = is_night_mode_time(start_str, end_str)
        return in_night, "night" if in_night else "day"
    except Exception:
        return False, "error"


def _is_time_in_window(current_hm: str, start_hm: str, end_hm: str) -> bool:
    if start_hm <= end_hm:
        return start_hm <= current_hm < end_hm
    else:
        return current_hm >= start_hm or current_hm < end_hm


def _extract_urls_to_markup(text: str, custom_btn_text: str = None, custom_btn_url: str = None):
    buttons = []
    if custom_btn_url:
        label = custom_btn_text if custom_btn_text else "🌐 Ver Enlace Oficial"
        buttons.append([InlineKeyboardButton(text=label, url=custom_btn_url.strip())])

    url_pattern = re.compile(r'(?<!href=["\'])(https?://[^\s<>"\']+)')
    found_urls = url_pattern.findall(text)

    cleaned_text = text
    for i, u in enumerate(found_urls):
        cleaned_text = cleaned_text.replace(u, "").strip()
        if not custom_btn_url or u != custom_btn_url:
            btn_title = f"🔗 Enlace {i + 1}" if len(found_urls) > 1 else (custom_btn_text if custom_btn_text else "🌐 Acceder")
            buttons.append([InlineKeyboardButton(text=btn_title, url=u)])

    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text).strip()
    markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    return cleaned_text, markup


def build_vc_moderation_keyboard(chat_id: int, bot_username: str, lang: str = "es", price: int = 50, custom_btn_text: str = None, custom_btn_url: str = None):
    btn_label = custom_btn_text if custom_btn_text else ("⭐ ACTIVAR MICVIP AHORA" if lang == "es" else "⭐ ACTIVATE MICVIP NOW")
    pay_url = custom_btn_url.strip() if custom_btn_url else f"https://t.me/{bot_username}?start=vipmic_{chat_id}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_label, url=pay_url)]
    ])


async def _resolve_reset_text(chat_id: int):
    svc_cfg = await get_sentinel_service_messages_config(chat_id)
    custom_text = svc_cfg.get("reset_text")
    text = custom_text if custom_text else OPTIMIZATION_TEXT
    media_id = svc_cfg.get("reset_media_id")
    media_type = svc_cfg.get("reset_media_type")
    btn_text = svc_cfg.get("reset_btn")
    btn_url = svc_cfg.get("reset_btn_url")
    autodel = svc_cfg.get("reset_autodel", 20) or 20
    return text, media_id, media_type, btn_text, btn_url, autodel


async def _dispatch_radar_notice(chat_id: int, text: str, media_id: str = None, media_type: str = None, auto_delete_after: int = None, reply_markup: InlineKeyboardMarkup = None):
    if not _global_bot:
        return None
    try:
        sent = None
        safe_caption = text[:1020] + "..." if len(text) > 1024 else text

        if media_id and media_type == "photo":
            sent = await _global_bot.send_photo(chat_id=chat_id, photo=media_id, caption=safe_caption, reply_markup=reply_markup, parse_mode="HTML")
        elif media_id and media_type == "video":
            sent = await _global_bot.send_video(chat_id=chat_id, video=media_id, caption=safe_caption, reply_markup=reply_markup, parse_mode="HTML")
        elif media_id and media_type == "animation":
            sent = await _global_bot.send_animation(chat_id=chat_id, animation=media_id, caption=safe_caption, reply_markup=reply_markup, parse_mode="HTML")
        else:
            sent = await _global_bot.send_message(chat_id=chat_id, text=text[:4000], reply_markup=reply_markup, parse_mode="HTML")

        if sent and auto_delete_after and auto_delete_after > 0:
            async def _auto_delete_notice(msg, delay: int):
                await asyncio.sleep(delay)
                try:
                    await msg.delete()
                except Exception:
                    pass
            _spawn(_auto_delete_notice(sent, auto_delete_after))
        return sent
    except Exception as e:
        logger.warning(f"Aviso despachando radar notice en {chat_id}: {e}")
        return None


async def _dispatch_member_vc_notice(chat_id: int, user_name: str, lang: str = "es"):
    if not _global_bot:
        return
    svc_cfg = await get_sentinel_service_messages_config(chat_id)
    if svc_cfg.get("vc_enabled", 1) == 0:
        return

    now = time.time()
    if now - _vc_notice_locks.get(chat_id, 0) < 3:
        return
    _vc_notice_locks[chat_id] = now

    try:
        last_id = _last_vc_notice.get(chat_id)
        if last_id:
            try:
                await _global_bot.delete_message(chat_id=chat_id, message_id=last_id)
            except Exception:
                pass

        bot_info = await _global_bot.get_me()
        bot_username = bot_info.username or "thebunkerapp_bot"
        price = await get_mic_vip_price(chat_id) or 50

        custom_text = svc_cfg.get("vc_text")
        custom_btn = svc_cfg.get("vc_btn") or svc_cfg.get("micvip_btn")
        custom_url = svc_cfg.get("vc_btn_url") or svc_cfg.get("micvip_btn_url")
        autodel_time = int(svc_cfg.get("vc_autodel", 30) or 30)

        if custom_text:
            text = custom_text.replace("{user_name}", user_name).replace("{mention}", user_name)
        else:
            text = (
                f"⚜️ <b>The Bunker O.S.</b>\n\n"
                f"🔇 {user_name}, <i>el búnker ha establecido por defecto tu volumen al 2%.</i>\n\n"
                f"¿Quieres desbloquear el 100% de tu micrófono? Presiona el botón inferior.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )

        markup = build_vc_moderation_keyboard(chat_id, bot_username, lang, price, custom_btn, custom_url)
        cleaned_text, extracted_markup = _extract_urls_to_markup(text, custom_btn, custom_url)
        final_markup = markup if markup else extracted_markup

        sent = await _dispatch_radar_notice(
            chat_id=chat_id, text=cleaned_text, media_id=svc_cfg.get("vc_media_id"),
            media_type=svc_cfg.get("vc_media_type"), auto_delete_after=autodel_time if autodel_time > 0 else None,
            reply_markup=final_markup
        )
        if sent:
            _last_vc_notice[chat_id] = sent.message_id
    except Exception as e:
        logger.warning(f"Aviso notificación de entrada a VC en {chat_id}: {e}")


async def _dispatch_pinned_vc_welcome(chat_id: int, lang: str = "es"):
    if not _global_bot:
        return
    try:
        svc_cfg = await get_sentinel_service_messages_config(chat_id)
        if svc_cfg.get("vc_welcome_enabled", 1) == 0:
            return

        bot_info = await _global_bot.get_me()
        bot_username = bot_info.username or "thebunkerapp_bot"
        tier = (await get_group_tier(chat_id) or "free").lower()

        custom_text = svc_cfg.get("vc_welcome_text")
        custom_btn = svc_cfg.get("vc_welcome_btn")
        custom_url = svc_cfg.get("vc_welcome_btn_url")
        autodel = svc_cfg.get("vc_welcome_autodel", 0) or 0

        markup = build_vc_moderation_keyboard(chat_id, bot_username, lang, custom_btn_text=custom_btn, custom_btn_url=custom_url)
        text = custom_text if (tier in ("pro", "ultra_pro") and custom_text) else VC_START_TEXTS.get(lang, VC_START_TEXTS["es"])

        cleaned_text, extracted_markup = _extract_urls_to_markup(text, custom_btn, custom_url)
        final_markup = markup if markup else extracted_markup

        sent = await _dispatch_radar_notice(
            chat_id=chat_id, text=cleaned_text,
            media_id=svc_cfg.get("vc_welcome_media_id") if tier == "ultra_pro" else None,
            media_type=svc_cfg.get("vc_welcome_media_type") if tier == "ultra_pro" else None,
            auto_delete_after=autodel if autodel > 0 else None, reply_markup=final_markup
        )
        if sent:
            try:
                await _global_bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, both_sides=True)
            except Exception:
                pass
            _pinned_vc_messages[chat_id] = sent.message_id
    except Exception as e:
        logger.warning(f"Aviso bienvenida de VC en {chat_id}: {e}")


async def _dispatch_sentinel_payload(chat_id: int, origin: str = "optimizacion"):
    if not _global_bot:
        return None
    try:
        payload_cfg = await get_sentinel_payload_config(chat_id)
    except Exception:
        return None

    if not payload_cfg or payload_cfg.get("enabled") != 1:
        return None
    text = payload_cfg.get("text")
    if not text:
        return None

    now = asyncio.get_event_loop().time()
    if now - _sentinel_payload_last_sent.get(chat_id, 0) < SENTINEL_PAYLOAD_MIN_GAP_SECONDS:
        return None

    cleaned_text, markup = _extract_urls_to_markup(text, payload_cfg.get("button_text"), payload_cfg.get("button_url"))
    sent = await _dispatch_radar_notice(
        chat_id=chat_id, text=cleaned_text, media_id=payload_cfg.get("media_id"),
        media_type=payload_cfg.get("media_type"), auto_delete_after=payload_cfg.get("auto_delete_after"),
        reply_markup=markup
    )
    if sent:
        _sentinel_payload_last_sent[chat_id] = now
    return sent


async def _get_raw_group_call(client: Client, chat_id: int, peer=None):
    try:
        if peer is None:
            peer = await client.resolve_peer(chat_id)

        raw_call = None
        if isinstance(peer, (InputPeerChannel, InputChannel)):
            full_chat_res = await client.invoke(
                GetFullChannel(channel=InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash))
            )
            raw_call = getattr(full_chat_res.full_chat, "call", None)
        elif isinstance(peer, InputPeerChat):
            full_chat_res = await client.invoke(GetFullChat(chat_id=peer.chat_id))
            raw_call = getattr(full_chat_res.full_chat, "call", None)
        else:
            resolved = await client.resolve_peer(chat_id)
            if isinstance(resolved, (InputPeerChannel, InputChannel)):
                full_chat_res = await client.invoke(
                    GetFullChannel(channel=InputChannel(channel_id=resolved.channel_id, access_hash=resolved.access_hash))
                )
                raw_call = getattr(full_chat_res.full_chat, "call", None)
            elif isinstance(resolved, InputPeerChat):
                full_chat_res = await client.invoke(GetFullChat(chat_id=resolved.chat_id))
                raw_call = getattr(full_chat_res.full_chat, "call", None)

        if raw_call:
            return InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
        return None
    except Exception as e:
        logger.debug(f"Aviso obteniendo llamada en {chat_id}: {e}")
        return None


async def semantic_scan_content(text: str, custom_prompt: str = "") -> dict:
    if not text or not mistral_client or len(text.strip()) < 8:
        return {"flagged": False, "reason": ""}
    try:
        response = await asyncio.to_thread(
            mistral_client.chat.complete,
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": "Determina si el texto contiene amenazas extremas o material ilicito. Responde estrictamente JSON con 'flagged' y 'reason'."},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        return {"flagged": bool(data.get("flagged")), "reason": data.get("reason", "Infracción semántica")}
    except Exception:
        return {"flagged": False, "reason": ""}


async def generate_sentinel_ai_response(
    chat_id: int, 
    user_id: int, 
    user_name: str, 
    message_text: str,
    personality_tone: str = "guardian",
    custom_prompt: str = ""
) -> str:
    tones = {
        "guardian": (
            "Eres el Centinela Guardián de The Bunker OS (Cloud Media Management © 2026). "
            "Tu tono es militar, táctico, vigilante, formal y conciso. Defiendes el orden, "
            "la disciplina y la seguridad perimetral de la comunidad. No usas rodeos ni saludos innecesarios."
        ),
        "copilot": (
            "Eres el Copiloto Inteligente de The Bunker OS. Tu tono es profesional, ágil, servicial "
            "y ejecutivo. Ayudas a los miembros y anfitriones con información clara, precisa y productiva."
        ),
        "pr": (
            "Eres el Relaciones Públicas y Anfitrión de The Bunker OS. Tu tono es cordial, entusiasta, "
            "diplomático y enfocado en fomentar la participación respetuosa en el canal y la sala de voz."
        )
    }

    tone_prompt = tones.get(personality_tone, tones["guardian"])
    system_instruction = (
        f"{tone_prompt}\n"
        f"Directivas personalizadas del Creador: {custom_prompt if custom_prompt else 'Ninguna adicional'}\n"
        "Reglas obligatorias:\n"
        "- Responde en el idioma del usuario (generalmente español).\n"
        "- Máximo 2 a 3 oraciones (máximo 80 palabras).\n"
        "- Estrictamente adaptado a un chat comunitario en vivo."
    )

    if not mistral_client:
        fallbacks = [
            f"Perímetro seguro, {user_name}. Supervisión acústica y defensiva activa 24/7. 🛡️",
            f"Recibido, {user_name}. El radar acústico mantiene la sala optimizada. Informa al Creador si requieres privilegios especiales.",
            f"Transmisión estable y monitoreada, {user_name}. Los protocolos del Búnker están operando al 100%."
        ]
        return random.choice(fallbacks)

    try:
        context_history = await get_ai_chat_context(chat_id, limit=6)
        messages = [{"role": "system", "content": system_instruction}]
        for item in context_history:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": f"{user_name}: {message_text}"})

        response = await asyncio.to_thread(
            mistral_client.chat.complete,
            model="mistral-small-latest",
            messages=messages,
            max_tokens=220,
            temperature=0.7
        )
        reply_text = response.choices[0].message.content.strip()

        await save_ai_chat_context(chat_id, user_id, "user", message_text)
        await save_ai_chat_context(chat_id, 0, "assistant", reply_text)

        return reply_text
    except Exception as ex:
        logger.error(f"❌ [Error Generando Respuesta IA Centinela]: {ex}")
        return f"Perímetro asegurado, {user_name}. Directiva de supervisión en línea. 🛡️"


async def sentinel_incoming_message_dispatcher(client: Client, message):
    if not message.chat or message.chat.type == ChatType.PRIVATE or (message.from_user and message.from_user.is_self):
        return
    chat_id = message.chat.id
    from_user = message.from_user
    user_id = from_user.id if from_user else 0

    if user_id and not (from_user and from_user.is_bot):
        _spawn(record_hourly_chat_activity(chat_id))
        _spawn(add_user_reputation_xp(group_id=chat_id, user_id=user_id, full_name=from_user.first_name or "", username=from_user.username or ""))

    tier = (await get_group_tier(chat_id) or "free").lower()
    is_ultra = tier in ("ultra_pro", "ultra") or (user_id and is_super_admin(user_id))

    text_content = (message.text or message.caption or "").strip()
    if not is_ultra or not text_content:
        return

    ai_cfg = await get_ai_sentinel_config(chat_id)

    if ai_cfg.get("guardian_status") == 1 and user_id and not is_super_admin(user_id) and user_id not in SERVICE_ACCOUNT_IDS:
        if not await is_whitelisted(user_id):
            threat = await semantic_scan_content(text_content, custom_prompt=ai_cfg.get("custom_prompt", ""))
            if threat.get("flagged"):
                try:
                    await message.delete()
                except Exception:
                    if _global_bot:
                        try:
                            await _global_bot.delete_message(chat_id, message.id)
                        except Exception:
                            pass
                user_tag = f"@{from_user.username}" if from_user and from_user.username else (from_user.first_name if from_user else f"ID {user_id}")
                alert_text = (
                    f"🛡️ <b>The Bunker Bot: Intervención Semántica del Guardián</b>\n\n"
                    f"Mensaje de <b>{html.escape(user_tag)}</b> purgado preventivamente.\n"
                    f"• <b>Detección:</b> <code>{html.escape(threat.get('reason'))}</code>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                )
                _spawn(_dispatch_radar_notice(chat_id, alert_text, auto_delete_after=20))
                return

    if ai_cfg.get("copilot_status") == 1:
        me_username = (client.me.username or "").lower() if getattr(client, "me", None) else ""
        text_lower = text_content.lower()

        is_replied_to_me = bool(
            message.reply_to_message 
            and message.reply_to_message.from_user 
            and message.reply_to_message.from_user.is_self
        )
        is_mentioned = (f"@{me_username}" in text_lower) if me_username else False
        if not is_mentioned and "@alphacentinel" in text_lower:
            is_mentioned = True

        response_mode = ai_cfg.get("response_mode", "mention_only")
        response_chance = ai_cfg.get("response_chance", 15)

        should_reply = False
        if is_replied_to_me or is_mentioned:
            should_reply = True
        elif response_mode == "always":
            should_reply = True
        elif response_mode == "chance" and random.randint(1, 100) <= response_chance:
            should_reply = True

        if should_reply:
            try:
                await client.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:
                pass

            clean_prompt = text_content
            if me_username:
                clean_prompt = re.sub(rf"@{me_username}", "", clean_prompt, flags=re.IGNORECASE).strip()

            user_display = from_user.first_name if from_user else "Miembro"
            ai_reply = await generate_sentinel_ai_response(
                chat_id=chat_id,
                user_id=user_id,
                user_name=user_display,
                message_text=clean_prompt,
                personality_tone=ai_cfg.get("personality_tone", "guardian"),
                custom_prompt=ai_cfg.get("custom_prompt", "")
            )

            if ai_reply:
                try:
                    await message.reply_text(ai_reply, quote=True)
                except Exception:
                    try:
                        await client.send_message(chat_id, ai_reply)
                    except Exception as send_err:
                        logger.warning(f"Aviso enviando réplica IA en {chat_id}: {send_err}")


async def _refresh_admin_cache(client: Client, chat_id: int, bot_client_id: int):
    try:
        new_admins = set()
        async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
            status_val = str(getattr(member.status, "value", member.status)).lower()
            if status_val in ["creator", "owner", "administrator"] and member.user and not member.user.is_bot:
                new_admins.add(member.user.id)
        if bot_client_id:
            new_admins.add(bot_client_id)
        admin_caches[chat_id] = {'admins': new_admins, 'ts': asyncio.get_event_loop().time()}
    except Exception as e:
        logger.debug(f"Aviso actualizando admin cache en chat {chat_id}: {e}")


async def _verify_active_membership(client: Client, chat_id: int) -> bool:
    try:
        user_target = client.me.id if getattr(client, "me", None) else "me"
        member = await client.get_chat_member(chat_id, user_target)
        status_val = str(getattr(member.status, "value", member.status)).lower()
        return status_val not in ("left", "kicked", "banned")
    except Exception:
        return True


async def monitor_single_group(chat_id: int, peer, client: Client, bot_client_id: int, user_id: int = 0):
    alerted_users = set()
    current_call = None
    last_channel_check = 0
    is_joined_audio = False
    call_start_time = 0

    while True:
        if not client.is_connected:
            try:
                await client.start()
            except FATAL_SESSION_ERRORS as auth_err:
                logger.error(f"🔒 [Sesión Inválida] Centinela del grupo {chat_id} desautorizado: {auth_err}")
                if user_id:
                    try:
                        await revoke_owner_session(user_id, chat_id, reason=str(auth_err))
                    except Exception:
                        pass
                active_sentinels.pop(chat_id, None)
                return
            except Exception:
                await asyncio.sleep(5)
                continue

        try:
            current_time = asyncio.get_event_loop().time()
            cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': 0})
            
            if current_time - cache_info['ts'] > ADMIN_CACHE_TTL:
                await _refresh_admin_cache(client, chat_id, bot_client_id)
                cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': current_time})

                if not await _verify_active_membership(client, chat_id):
                    logger.warning(f"🚪 [Membresía Perdida] El Centinela ya no pertenece al grupo {chat_id}. Deteniendo monitor.")
                    active_sentinels.pop(chat_id, None)
                    return

            if not current_call or (current_time - last_channel_check > 15):
                raw_call_obj = await _get_raw_group_call(client, chat_id, peer)
                if raw_call_obj:
                    if not current_call or current_call.id != raw_call_obj.id:
                        call_start_time = asyncio.get_event_loop().time()
                        _spawn(_dispatch_pinned_vc_welcome(chat_id, lang="es"))
                    current_call = raw_call_obj
                else:
                    current_call = None
                    is_joined_audio = False
                    call_start_time = 0
                last_channel_check = current_time

            # Protocolo de reinicio preventivo audiovisual cada 3.5 horas de transmisión continua
            if current_call and call_start_time > 0:
                if (asyncio.get_event_loop().time() - call_start_time) >= 12600:
                    logger.info(f"🔄 [Optimización Audiovisual] Reinicio preventivo en grupo {chat_id} (Transmisión > 3.5h).")
                    if _global_bot:
                        try:
                            svc_cfg = await get_sentinel_service_messages_config(chat_id)
                            if svc_cfg.get("reset_enabled", 1) == 1:
                                text, media_id, media_type, btn_text, btn_url, autodel = await _resolve_reset_text(chat_id)
                                reset_markup = None
                                if btn_text or btn_url:
                                    bot_info = await _global_bot.get_me()
                                    reset_markup = build_vc_moderation_keyboard(
                                        chat_id=chat_id,
                                        bot_username=bot_info.username or "thebunkerapp_bot",
                                        lang="es",
                                        custom_btn_text=btn_text,
                                        custom_btn_url=btn_url
                                    )
                                cleaned_text, extracted_markup = _extract_urls_to_markup(text, btn_text, btn_url)
                                final_reset_markup = reset_markup if reset_markup else extracted_markup

                                await _dispatch_radar_notice(
                                    chat_id=chat_id,
                                    text=cleaned_text,
                                    media_id=media_id,
                                    media_type=media_type,
                                    auto_delete_after=autodel,
                                    reply_markup=final_reset_markup
                                )
                        except Exception as reset_notice_err:
                            logger.warning(f"Aviso despachando aviso de reset en {chat_id}: {reset_notice_err}")

                        try:
                            await _dispatch_sentinel_payload(chat_id, origin="optimizacion_3.5h")
                        except Exception as payload_err:
                            logger.debug(f"Aviso despachando payload Ultra Pro en {chat_id}: {payload_err}")

                    try:
                        await client.invoke(DiscardGroupCall(call=current_call))
                    except Exception as disc_err:
                        logger.warning(f"Aviso al cerrar llamada previa: {disc_err}")

                    await asyncio.sleep(3.0)

                    try:
                        await client.invoke(CreateGroupCall(peer=peer, random_id=random.randint(100000, 999999)))
                        _spawn(_dispatch_pinned_vc_welcome(chat_id, lang="es"))
                    except Exception as create_err:
                        logger.warning(f"Aviso al reiniciar llamada: {create_err}")

                    current_call = None
                    is_joined_audio = False
                    call_start_time = 0
                    continue

            if current_call:
                night_active, _ = await _is_night_active(chat_id)
                autolower_enabled = await get_autolower_status(chat_id)
                if autolower_enabled != 1 and not night_active:
                    await asyncio.sleep(8)
                    continue

                if current_time < _autolower_cooldowns.get(chat_id, 0):
                    await asyncio.sleep(15)
                    continue

                if not await _has_manage_calls_right(client, chat_id):
                    await asyncio.sleep(8)
                    continue

                if not is_joined_audio and (current_time - _last_join_attempt.get(chat_id, 0)) >= JOIN_RETRY_SECONDS:
                    _last_join_attempt[chat_id] = current_time
                    try:
                        my_peer = await client.resolve_peer("me")
                        await client.invoke(
                            JoinGroupCall(
                                call=current_call, join_as=my_peer,
                                muted=True, video_stopped=True, params=DataJSON(data="{}")
                            )
                        )
                        is_joined_audio = True
                        _forbidden_strikes[chat_id] = 0
                    except Exception:
                        is_joined_audio = False

                participants, users_map = await _fetch_all_participants(client, current_call)
                active_users = set()

                podcast_cfg = await get_podcast_config(chat_id)
                host_is_speaking = False
                if podcast_cfg["status"] == 1:
                    for p_scan in participants:
                        if getattr(p_scan, "left", False):
                            continue
                        peer_scan = getattr(p_scan, "peer", None)
                        if not isinstance(peer_scan, PeerUser):
                            continue
                        scan_id = peer_scan.user_id
                        if (scan_id == bot_client_id or scan_id in cache_info['admins']) and not getattr(p_scan, "muted", True):
                            host_is_speaking = True
                            break

                screen_shield_on = await get_screen_shield_status(chat_id)

                for p in participants:
                    if getattr(p, "left", False):
                        continue
                    peer_user = getattr(p, "peer", None)
                    if not isinstance(peer_user, PeerUser):
                        continue
                    u_id = peer_user.user_id
                    active_users.add(u_id)

                    if await is_userbot_flagged(u_id, chat_id):
                        try:
                            await client.invoke(
                                EditGroupCallParticipant(
                                    call=current_call, 
                                    participant=await client.resolve_peer(u_id), 
                                    muted=True, 
                                    volume=0
                                )
                            )
                        except Exception:
                            pass
                        continue

                    is_admin_or_owner = (
                        is_super_admin(u_id)
                        or u_id in SERVICE_ACCOUNT_IDS
                        or u_id == bot_client_id 
                        or u_id in cache_info['admins']
                    )
                    is_vip = await is_vip_mic_active(u_id, chat_id) or await is_whitelisted(u_id)
                    is_authorized = is_admin_or_owner or is_vip

                    if not is_authorized and screen_shield_on and getattr(p, "presentation", None):
                        flag_key = (chat_id, u_id)
                        if not _screen_shield_flagged.get(flag_key):
                            _screen_shield_flagged[flag_key] = True
                            user_obj = users_map.get(u_id)
                            try:
                                if user_obj and getattr(user_obj, "access_hash", None):
                                    p_peer = InputPeerUser(user_id=u_id, access_hash=user_obj.access_hash)
                                else:
                                    p_peer = await client.resolve_peer(u_id)
                                removed = await _cut_video_and_remove(client, current_call, chat_id, u_id, p_peer)
                                if removed:
                                    user_name = f"@{user_obj.username}" if (user_obj and getattr(user_obj, "username", None)) else f"ID {u_id}"
                                    _spawn(_dispatch_radar_notice(
                                        chat_id=chat_id,
                                        text=SCREEN_SHIELD_ALERT_TEXT.format(user_name=user_name),
                                        auto_delete_after=30
                                    ))
                            except Exception as e:
                                logger.debug(f"Aviso en Escudo Antinota para {u_id} en {chat_id}: {e}")
                        continue
                    else:
                        _screen_shield_flagged.pop((chat_id, u_id), None)

                    if is_vip and not is_admin_or_owner:
                        vol = p.volume if getattr(p, "volume", None) is not None else 10000
                        target_vip_vol = podcast_cfg["duck_volume"] if (podcast_cfg["status"] == 1 and host_is_speaking) else 10000
                        if abs(vol - target_vip_vol) > 50:
                            try:
                                p_peer = await client.resolve_peer(u_id)
                                await client.invoke(
                                    EditGroupCallParticipant(call=current_call, participant=p_peer, muted=False, volume=target_vip_vol)
                                )
                            except Exception:
                                pass
                        continue

                    if is_admin_or_owner:
                        continue

                    vol = p.volume if getattr(p, "volume", None) is not None else 10000
                    is_muted = getattr(p, "muted", True)

                    noise_spike = False
                    was_muted_before = _last_mute_state.get((chat_id, u_id), True)
                    if not is_muted and was_muted_before and podcast_cfg["noise_shield"] == 1:
                        noise_spike = _register_noise_strike(chat_id, u_id)
                    _last_mute_state[(chat_id, u_id)] = is_muted

                    if night_active:
                        desired_muted, desired_volume = True, 0
                    elif noise_spike:
                        desired_muted, desired_volume = True, 0
                    else:
                        desired_muted, desired_volume = True, 200

                    action_needed = (
                        noise_spike
                        or night_active
                        or (desired_muted and ((not is_muted) or vol > 200))
                    )

                    if action_needed:
                        user_obj = users_map.get(u_id)
                        try:
                            p_peer = InputPeerUser(user_id=u_id, access_hash=user_obj.access_hash) if (user_obj and getattr(user_obj, "access_hash", None)) else await client.resolve_peer(u_id)
                            await client.invoke(EditGroupCallParticipant(call=current_call, participant=p_peer, muted=desired_muted, volume=desired_volume))
                            _forbidden_strikes[chat_id] = 0
                        except Exception as e:
                            err_msg = str(e).upper()
                            if "GROUPCALL_FORBIDDEN" in err_msg:
                                _invalidate_call_rights(chat_id)
                                _register_forbidden_strike(chat_id, "silenciar")
                                await asyncio.sleep(25)
                                break
                            elif "GROUPCALL_INVALID" in err_msg or "CALL_ALREADY_ENDED" in err_msg:
                                current_call = None
                                is_joined_audio = False
                                break
                            continue

                        if u_id not in alerted_users and not night_active:
                            alerted_users.add(u_id)
                            first_name = html.escape(user_obj.first_name) if (user_obj and getattr(user_obj, "first_name", None)) else f"Usuario {u_id}"
                            user_mention = f'<a href="tg://user?id={u_id}">{first_name}</a>'
                            if _global_bot:
                                try:
                                    if noise_spike:
                                        _spawn(_dispatch_radar_notice(
                                            chat_id=chat_id,
                                            text=NOISE_SHIELD_ALERT_TEXT.format(user_name=user_mention),
                                            auto_delete_after=30
                                        ))
                                    else:
                                        _spawn(_dispatch_member_vc_notice(chat_id=chat_id, user_name=user_mention, lang="es"))
                                except Exception:
                                    pass

                alerted_users.intersection_update(active_users)

        except FloodWait as fw:
            await asyncio.sleep(fw.value + 2)
        except PeerIdInvalid:
            await asyncio.sleep(60)
        except Exception:
            await asyncio.sleep(3)
        await asyncio.sleep(3)


async def vc_scheduler_loop():
    logger.info("🗓 [Programador VC] Sistema de programación semanal iniciado con soporte de zona horaria.")
    while True:
        try:
            now = _get_group_now()
            current_day_str = str(now.isoweekday())
            current_time_str = now.strftime("%H:%M")

            schedules = await get_all_active_vc_schedules()
            for row in schedules:
                group_id, days_allowed, start_time, end_time, status, call_active = row[0], row[1], row[2], row[3], row[4], row[5]
                
                if not days_allowed:
                    continue
                allowed_days_list = [d.strip() for d in days_allowed.split(",")]
                if current_day_str not in allowed_days_list:
                    continue

                sentinel_data = active_sentinels.get(group_id)
                client = sentinel_data["client"] if sentinel_data else assistant_app
                if not client or not client.is_connected:
                    continue

                try:
                    peer = await client.resolve_peer(group_id)
                except Exception:
                    continue

                is_in_window = _is_time_in_window(current_time_str, start_time, end_time)

                if is_in_window and call_active == 0:
                    try:
                        await client.invoke(CreateGroupCall(peer=peer, random_id=random.randint(100000, 999999)))
                        await update_vc_call_status(group_id, 1)
                        if _global_bot:
                            svc_cfg = await get_sentinel_service_messages_config(group_id)
                            if svc_cfg.get("sched_enabled", 1) == 1:
                                sched_text = svc_cfg.get("sched_start_text") or VC_SCHED_MESSAGES["start"]
                                sched_media_id = svc_cfg.get("sched_start_media_id")
                                sched_media_type = svc_cfg.get("sched_start_media_type")
                                sched_autodel = svc_cfg.get("sched_start_autodel", 0) or 0
                                cleaned_sched_text, sched_markup = _extract_urls_to_markup(sched_text)

                                await _dispatch_radar_notice(
                                    chat_id=group_id,
                                    text=cleaned_sched_text,
                                    media_id=sched_media_id,
                                    media_type=sched_media_type,
                                    auto_delete_after=sched_autodel if sched_autodel > 0 else None,
                                    reply_markup=sched_markup
                                )
                            _spawn(_dispatch_pinned_vc_welcome(group_id, lang="es"))
                            try:
                                await _dispatch_sentinel_payload(group_id, origin="apertura_programada")
                            except Exception as payload_err:
                                logger.debug(f"Aviso despachando payload Ultra Pro en apertura {group_id}: {payload_err}")
                        logger.info(f"📡 [Apertura VC Programada] Sala abierta en comunidad {group_id}.")
                    except Exception as e:
                        err_up = str(e).upper()
                        if "ALREADY_STARTED" in err_up:
                            await update_vc_call_status(group_id, 1)
                        else:
                            logger.error(f"Error abriendo videochat en grupo {group_id}: {e}")

                elif not is_in_window and call_active == 1:
                    try:
                        raw_call_obj = await _get_raw_group_call(client, group_id, peer)
                        if raw_call_obj:
                            await client.invoke(DiscardGroupCall(call=raw_call_obj))
                        await update_vc_call_status(group_id, 0)
                        if _global_bot:
                            await _global_bot.send_message(chat_id=group_id, text=VC_SCHED_MESSAGES["end"], parse_mode="HTML")
                        logger.info(f"📡 [Cierre VC Programado] Sala concluida ordenadamente en comunidad {group_id}.")
                    except Exception as e:
                        err_up = str(e).upper()
                        if "CALL_ALREADY_ENDED" in err_up or "GROUPCALL_INVALID" in err_up:
                            await update_vc_call_status(group_id, 0)
                        else:
                            logger.error(f"Error cerrando videochat en grupo {group_id}: {e}")

        except Exception as e:
            logger.error(f"Error en bucle de programación VC: {e}")

        await asyncio.sleep(40)


async def _get_all_night_groups() -> list[int]:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT group_id FROM group_settings WHERE night_mode_status = 1")
            return [row[0] for row in cursor.fetchall()]
    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        return list(active_sentinels.keys())


async def night_mode_autonomous_loop():
    logger.info("🌙 [Modo Nocturno Autónomo] Bucle de automatización perimetral iniciado.")
    while True:
        try:
            night_configured_groups = await _get_all_night_groups()
            all_target_groups = set(night_configured_groups).union(active_sentinels.keys())

            for group_id in all_target_groups:
                try:
                    cfg = await get_night_mode_config(group_id)
                    if not cfg or cfg.get("status") != 1:
                        continue
                    start_str = cfg.get("start", "22:00")
                    end_str = cfg.get("end", "06:00")
                    in_night_time = is_night_mode_time(start_str, end_str)
                    current_media_lock = await get_lock_status(group_id, "lock_media")

                    if in_night_time and current_media_lock == 0:
                        await activate_universal_night_mode(group_id)
                        if _global_bot:
                            try:
                                await _global_bot.send_message(
                                    chat_id=group_id,
                                    text="🌙 <b>Modo Nocturno Autónomo:</b> 🟢 Activado automáticamente según el horario programado.\n\n🛡️ <i>Cloud Media Management</i>",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass
                        logger.info(f"🌙 [Modo Nocturno Activado] Perímetro asegurado automáticamente en comunidad {group_id}.")

                    elif not in_night_time and current_media_lock == 1:
                        night_cfg = await get_night_mode_config(group_id)
                        if night_cfg.get("status") == 1:
                            await deactivate_universal_night_mode(group_id)
                            if _global_bot:
                                try:
                                    await _global_bot.send_message(
                                        chat_id=group_id,
                                        text="☀️ <b>Modo Nocturno Autónomo:</b> 🔴 Desactivado. Se restablecen los permisos perimetrales diurnos.\n\n🛡️ <i>Cloud Media Management</i>",
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
                            logger.info(f"☀️ [Modo Nocturno Desactivado] Permisos diurnos restaurados en comunidad {group_id}.")
                except Exception as inner_err:
                    logger.debug(f"Aviso evaluando modo nocturno autónomo en grupo {group_id}: {inner_err}")
        except Exception as e:
            logger.error(f"Error en bucle autónomo de modo nocturno: {e}")
        await asyncio.sleep(60)


async def radar_master_loop():
    """Bucle del maestro con corte automático definitivo ante clave duplicada para evitar colisiones."""
    global assistant_app
    while True:
        try:
            if assistant_app and not assistant_app.is_connected:
                try:
                    await assistant_app.start()
                    logger.info("🤖 [Centinela Maestro Reconectado con Éxito]")
                except FATAL_SESSION_ERRORS as auth_err:
                    logger.error(
                        f"🔒 [MASTER_SESSION Inválida/Duplicada]: {auth_err}. "
                        "Deteniendo Centinela Maestro para evitar saturación de red."
                    )
                    try:
                        await assistant_app.stop()
                    except Exception:
                        pass
                    assistant_app = None
                    break
                except Exception as e:
                    err_str = str(e).upper()
                    if "AUTH_KEY_DUPLICATED" in err_str or "UNAUTHORIZED" in err_str or "406" in err_str:
                        logger.error(f"🔒 [MASTER_SESSION Inválida]: {e}. Deteniendo Centinela Maestro.")
                        assistant_app = None
                        break

            if assistant_app and assistant_app.is_connected:
                async for dialog in assistant_app.get_dialogs(limit=100):
                    chat = dialog.chat
                    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                        chat_id = chat.id
                        lock = _get_launch_lock(chat_id)
                        async with lock:
                            if chat_id in active_sentinels:
                                continue
                            try:
                                peer = await assistant_app.resolve_peer(chat_id)
                                bot_id = get_assistant_bot_id()
                                task = asyncio.create_task(monitor_single_group(chat_id, peer, assistant_app, bot_id))
                                active_sentinels[chat_id] = {
                                    "client": assistant_app,
                                    "task": task,
                                    "user_id": 0
                                }
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Aviso en radar master loop: {e}")
        await asyncio.sleep(25)


async def launch_sentinel_instance(user_id: int, group_id: int, session_string: str, api_id: int = None, api_hash: str = None):
    client_api_id = api_id if api_id else DEFAULT_API_ID
    client_api_hash = api_hash if api_hash else DEFAULT_API_HASH
    
    session_client = Client(
        f"sentinel_{user_id}_{group_id}",
        session_string=session_string,
        api_id=client_api_id,
        api_hash=client_api_hash,
        in_memory=True
    )
    
    try:
        session_client.add_handler(
            MessageHandler(sentinel_incoming_message_dispatcher, filters.group | filters.channel)
        )

        await session_client.start()
        me = await session_client.get_me()

        try:
            chat_obj = await session_client.get_chat(group_id)
            peer = await session_client.resolve_peer(chat_obj.id)
        except Exception:
            peer = await session_client.resolve_peer(group_id)

        task = asyncio.create_task(monitor_single_group(group_id, peer, session_client, me.id, user_id))
        active_sentinels[group_id] = {
            "client": session_client,
            "task": task,
            "user_id": user_id
        }
        logger.info(f"💎 [Centinela Propio Conectado] Comunidad {group_id} protegida por @{me.username or me.id}")
        return True
    except FATAL_SESSION_ERRORS as auth_err:
        logger.warning(f"⚠️ [Sesión Inválida] La sesión de {user_id} para {group_id} fue revocada: {auth_err}")
        try:
            await revoke_owner_session(user_id, group_id, reason=str(auth_err))
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"⚠️ [Error al iniciar Centinela Propio] Grupo {group_id}: {e}")
        return False


async def register_or_update_sentinel(user_id: int, group_id: int, session_string: str, api_id: int = None, api_hash: str = None):
    lock = _get_launch_lock(group_id)
    async with lock:
        await _disconnect_sentinel_unlocked(group_id)
        async with _sentinel_launch_semaphore:
            return await launch_sentinel_instance(user_id, group_id, session_string, api_id, api_hash)


async def disconnect_sentinel(group_id: int):
    lock = _get_launch_lock(group_id)
    async with lock:
        await _disconnect_sentinel_unlocked(group_id)


async def _disconnect_sentinel_unlocked(group_id: int):
    if group_id in active_sentinels:
        sentinel_info = active_sentinels.pop(group_id)
        try:
            sentinel_info["task"].cancel()
        except Exception:
            pass
        try:
            client = sentinel_info["client"]
            if client and client != assistant_app and client.is_connected:
                await client.stop()
        except Exception:
            pass
        logger.info(f"🛑 [Centinela Desconectado] Grupo {group_id} liberado.")

    admin_caches.pop(group_id, None)
    _forbidden_strikes.pop(group_id, None)
    _autolower_cooldowns.pop(group_id, None)

    for key in [k for k in _screen_shield_flagged if k[0] == group_id]:
        _screen_shield_flagged.pop(key, None)
    for key in [k for k in _noise_unmute_history if k[0] == group_id]:
        _noise_unmute_history.pop(key, None)
    for key in [k for k in _last_mute_state if k[0] == group_id]:
        _last_mute_state.pop(key, None)


async def load_all_sentinels():
    sessions = await get_all_active_sessions()

    async def _load_one(u_id, g_id, s_str, a_id, a_hash):
        lock = _get_launch_lock(g_id)
        async with lock:
            if g_id in active_sentinels:
                return
            async with _sentinel_launch_semaphore:
                try:
                    await launch_sentinel_instance(u_id, g_id, s_str, a_id, a_hash)
                except Exception:
                    pass

    await asyncio.gather(*[
        _load_one(row[0], row[1], row[2], row[3], row[4]) for row in sessions
    ])


async def cancel_phone_auth(uid: int):
    """Limpia una sesión de autenticación telefónica pendiente y desconecta su cliente temporal con timeout."""
    try:
        session_data = pending_auth_sessions.pop(uid, None)
        if session_data:
            client = session_data.get("client")
            if client and client.is_connected:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=3.0)
                except Exception:
                    pass
            logger.info(f"🧹 [Auth Pendiente] Cancelada y desconectada sesión de UID {uid}.")
            return True
        return False
    except Exception as e:
        logger.debug(f"Aviso cancelando auth pendiente {uid}: {e}")
        return False


# ==========================================
# 📱 AUTENTICACIÓN TELEFÓNICA BLINDADA CON TIMEOUTS
# ==========================================
async def start_phone_auth(user_id: int, group_id: int, phone_number: str) -> dict:
    """Inicia el proceso de autenticación telefónica con timeout estricto para evitar cuelgues."""
    await cancel_phone_auth(user_id)
    clean_phone = phone_number.replace(" ", "").replace("-", "").strip()
    if not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    if not DEFAULT_API_ID or not DEFAULT_API_HASH:
        logger.error("❌ TELEGRAM_API_ID / TELEGRAM_API_HASH no configurados en el entorno.")
        return {"status": "error", "message": "Faltan TELEGRAM_API_ID o TELEGRAM_API_HASH en variables de entorno."}

    logger.info(f"📱 [Auth Teléfono] Solicitando código para UID {user_id} ({clean_phone})...")

    client = Client(
        f"auth_temp_{user_id}_{group_id}_{int(time.time())}",
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )

    try:
        connected = await _ensure_connected(client)
        if not connected:
            logger.error(f"❌ [Auth Teléfono] No se pudo conectar a Telegram para {clean_phone}.")
            return {"status": "error", "message": "connection_lost"}

        sent_code = await asyncio.wait_for(client.send_code(clean_phone), timeout=25.0)
        logger.info(f"📩 [Auth Teléfono] Código enviado exitosamente a {clean_phone} (hash: {sent_code.phone_code_hash})")

        pending_auth_sessions[user_id] = {
            "client": client,
            "phone": clean_phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "group_id": group_id,
            "ts": time.time()
        }
        return {"status": "ok", "phone": clean_phone}

    except asyncio.TimeoutError:
        logger.error(f"⏱️ [Auth Teléfono] Tiempo de espera agotado al conectar con Telegram para {clean_phone}.")
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": "Tiempo de espera agotado al conectar con Telegram. Intenta de nuevo."}
    except PhoneNumberInvalid:
        logger.warning(f"⚠️ [Auth Teléfono] Número inválido: {clean_phone}")
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": "invalid_phone"}
    except PhoneNumberBanned:
        logger.warning(f"⚠️ [Auth Teléfono] Número suspendido: {clean_phone}")
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": "Este número de teléfono está suspendido en Telegram."}
    except PhoneNumberFlood:
        logger.warning(f"⚠️ [Auth Teléfono] Límite de intentos superado para: {clean_phone}")
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": "Demasiados intentos para este número. Espera unas horas antes de reintentar."}
    except FloodWait as fw:
        logger.warning(f"⏳ [Auth Teléfono] FloodWait {fw.value}s para {clean_phone}")
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        logger.error(f"❌ [Auth Teléfono] Error inesperado en start_phone_auth: {e}", exc_info=True)
        if client.is_connected:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        return {"status": "error", "message": str(e)}


async def verify_phone_code(user_id: int, code: str) -> dict:
    """Verifica el código numérico enviado por Telegram con timeout."""
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]
    clean_code = code.strip().replace(" ", "").replace("-", "")

    logger.info(f"🔑 [Auth Código] Validando código para UID {user_id}...")

    try:
        connected = await _ensure_connected(client)
        if not connected:
            return {"status": "error", "message": "connection_lost"}

        await asyncio.wait_for(
            client.sign_in(
                phone_number=auth_data["phone"],
                phone_code_hash=auth_data["phone_code_hash"],
                phone_code=clean_code
            ),
            timeout=20.0
        )
        session_str = await client.export_session_string()
        group_id = auth_data["group_id"]
        
        await cancel_phone_auth(user_id)
        logger.info(f"✅ [Auth Código] Sesión exportada con éxito para UID {user_id}.")
        return {"status": "success", "session_string": session_str, "group_id": group_id}

    except SessionPasswordNeeded:
        logger.info(f"🔐 [Auth Código] Verificación 2FA requerida para UID {user_id}.")
        return {"status": "2fa_required"}
    except (PhoneCodeInvalid, PhoneCodeExpired):
        logger.warning(f"⚠️ [Auth Código] Código inválido o expirado para UID {user_id}.")
        return {"status": "error", "message": "invalid_code"}
    except FloodWait as fw:
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        logger.error(f"❌ [Auth Código] Error en verify_phone_code: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def verify_2fa_password(user_id: int, password: str) -> dict:
    """Valida la contraseña de Verificación en Dos Pasos (2FA) con timeout."""
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]

    logger.info(f"🔐 [Auth 2FA] Validando contraseña para UID {user_id}...")

    try:
        connected = await _ensure_connected(client)
        if not connected:
            return {"status": "error", "message": "connection_lost"}

        await asyncio.wait_for(client.check_password(password=password.strip()), timeout=20.0)
        session_str = await client.export_session_string()
        group_id = auth_data["group_id"]

        await cancel_phone_auth(user_id)
        logger.info(f"✅ [Auth 2FA] Contraseña 2FA verificada para UID {user_id}.")
        return {"status": "success", "session_string": session_str, "group_id": group_id}

    except PasswordHashInvalid:
        logger.warning(f"⚠️ [Auth 2FA] Contraseña incorrecta para UID {user_id}.")
        return {"status": "error", "message": "invalid_password"}
    except FloodWait as fw:
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        logger.error(f"❌ [Auth 2FA] Error en verify_2fa_password: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def pending_auth_cleanup_loop():
    TTL_SECONDS = 600
    while True:
        try:
            now = time.time()
            expired = [uid for uid, data in pending_auth_sessions.items() if now - data.get("ts", now) > TTL_SECONDS]
            for uid in expired:
                await cancel_phone_auth(uid)
        except Exception as e:
            logger.debug(f"Aviso en limpieza de sesiones pendientes: {e}")
        await asyncio.sleep(120)


async def init_assistant_master():
    global _default_my_id, assistant_app
    if assistant_app is None:
        logger.info("ℹ️ [Centinela Maestro Inactivo] Sin MASTER_SESSION; operando con Centinelas dedicados por comunidad.")
    else:
        try:
            if not assistant_app.is_connected:
                assistant_app.add_handler(
                    MessageHandler(sentinel_incoming_message_dispatcher, filters.group | filters.channel)
                )
                await assistant_app.start()
            me = await assistant_app.get_me()
            _default_my_id = me.id
            logger.info(f"🤖 [Centinela Maestro Activo] Online como ID {_default_my_id} (@{me.username or me.first_name})")
        except FATAL_SESSION_ERRORS as auth_err:
            logger.error(f"🔒 [MASTER_SESSION Inválida/Duplicada] {auth_err}. Desactivando Centinela Maestro para evitar bucles.")
            try:
                await assistant_app.stop()
            except Exception:
                pass
            assistant_app = None
        except Exception as e:
            logger.warning(f"⚠️ [Centinela Maestro]: Error de conexión inicial ({e})")

    await load_all_sentinels()
    if assistant_app:
        _spawn(radar_master_loop())
    _spawn(vc_scheduler_loop())
    _spawn(pending_auth_cleanup_loop())
    _spawn(night_mode_autonomous_loop())


def start_voice_radar(bot):
    global _global_bot
    _global_bot = bot
    _spawn(init_assistant_master())


async def close_all_sentinels():
    for group_id in list(active_sentinels.keys()):
        await disconnect_sentinel(group_id)
    if assistant_app and assistant_app.is_connected:
        try:
            await assistant_app.stop()
        except Exception:
            pass


async def set_participant_mic(chat_id: int, user_id: int, muted: bool, volume: int = 10000) -> bool:
    sentinel_data = active_sentinels.get(chat_id)
    client = sentinel_data["client"] if sentinel_data else assistant_app

    if not client or not client.is_connected:
        return False

    try:
        raw_call = await _get_raw_group_call(client, chat_id)
        if not raw_call:
            return False

        participant_peer = await client.resolve_peer(user_id)
        await client.invoke(
            EditGroupCallParticipant(call=raw_call, participant=participant_peer, muted=muted, volume=volume)
        )
        return True
    except Exception as e:
        logger.warning(f"Aviso en set_participant_mic para grupo {chat_id}: {e}")
        return False


async def _cut_video_and_remove(client: Client, current_call, chat_id: int, u_id: int, p_peer) -> bool:
    if is_super_admin(u_id) or u_id in SERVICE_ACCOUNT_IDS:
        return False

    try:
        await client.invoke(
            EditGroupCallParticipant(
                call=current_call, participant=p_peer,
                video_stopped=True, presentation_paused=True, muted=True, volume=200
            )
        )
    except Exception as e:
        logger.debug(f"Aviso al intentar cortar video en {chat_id} para {u_id}: {e}")

    if _global_bot:
        try:
            await _global_bot.ban_chat_member(chat_id=chat_id, user_id=u_id, until_date=int(time.time() + 35))
            await _global_bot.unban_chat_member(chat_id=chat_id, user_id=u_id)
            return True
        except Exception as e:
            logger.warning(f"Aviso: no se pudo expulsar a {u_id} de {chat_id} tras Escudo Antinota: {e}")
    return False


async def engage_screen_shield(group_id: int):
    await set_screen_shield_status(group_id, 1)
    logger.info(f"🎥 [Escudo Antinota] Activado y persistido para el grupo {group_id}")


async def disengage_screen_shield(group_id: int):
    await set_screen_shield_status(group_id, 0)
    logger.info(f"🎥 [Escudo Antinota] Desactivado y persistido para el grupo {group_id}")


async def engage_podcast_ducking(group_id: int, duck_level: int = 20):
    await set_podcast_mode(group_id, 1)
    await set_podcast_duck_volume(group_id, duck_level * 100)
    logger.info(f"🎙️ [Modo Podcast] Ducking activado al {duck_level}% y persistido en el grupo {group_id}")


async def disengage_podcast_ducking(group_id: int):
    await set_podcast_mode(group_id, 0)
    logger.info(f"🎙️ [Modo Podcast] Ducking desactivado y persistido en el grupo {group_id}")


async def execute_ghost_purge(chat_id: int, action: str = "ban") -> dict:
    sentinel_data = active_sentinels.get(chat_id)
    client: Client = sentinel_data["client"] if sentinel_data else assistant_app

    found = 0
    purged = 0

    if client and client.is_connected:
        try:
            async for member in client.get_chat_members(chat_id):
                user = member.user
                if user and getattr(user, "is_deleted", False):
                    found += 1
                    try:
                        if action == "ban":
                            await client.ban_chat_member(chat_id, user.id)
                        else:
                            await client.ban_chat_member(chat_id, user.id)
                            await client.unban_chat_member(chat_id, user.id)
                        purged += 1
                    except Exception as p_err:
                        logger.warning(f"Aviso purgando usuario {user.id} en {chat_id}: {p_err}")

            await update_ghost_purge_scan_time(chat_id)
            if _global_bot and purged > 0:
                alert_text = GHOST_PURGE_ALERT_TEXT.format(
                    found=found,
                    purged=purged,
                    action="Baneo Permanente 🔴" if action == "ban" else "Expulsión Suave 🟡"
                )
                _spawn(_dispatch_radar_notice(chat_id=chat_id, text=alert_text, auto_delete_after=60))

            return {"status": "success", "found": found, "purged": purged, "action": action}
        except Exception as e:
            logger.warning(f"Aviso en Ghost Purge MTProto para {chat_id}, activando fallback: {e}")

    if _global_bot:
        try:
            await update_ghost_purge_scan_time(chat_id)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM chat_user_activity WHERE group_id = ?", (chat_id,))
                tracked = cursor.fetchall()

            for (uid,) in tracked:
                try:
                    chat_member = await _global_bot.get_chat_member(chat_id, uid)
                    user = chat_member.user
                    if getattr(user, "is_deleted", False) or (user.first_name and "Deleted Account" in user.first_name):
                        found += 1
                        if chat_member.status not in ("creator", "administrator"):
                            await _global_bot.ban_chat_member(chat_id, uid)
                            if action != "ban":
                                await _global_bot.unban_chat_member(chat_id, uid)
                            purged += 1
                            await asyncio.sleep(0.1)
                except Exception:
                    continue

            return {"status": "success", "found": found, "purged": purged, "action": action, "fallback": True}
        except Exception as fb_err:
            logger.error(f"❌ Falló fallback de Ghost Purge en {chat_id}: {fb_err}")

    return {"status": "error", "message": "No se pudo conectar con el chat para la purga.", "purged": 0}