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
    PhoneNumberInvalid, PasswordHashInvalid, PeerIdInvalid,
    AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan
)
from pyrogram.raw.types import PeerUser, InputPeerUser, InputGroupCall, DataJSON
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
    is_vip_mic_active, is_group_approved, 
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

FATAL_SESSION_ERRORS = (Unauthorized, AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan)

logger = logging.getLogger("assistant_radar")

SCREEN_SHIELD_ALERT_TEXT = (
    "🛡️ <b>Escudo Antinota activado</b>\n\n"
    "El participante {user_name} fue detectado con presentación o video no autorizado y fue removido del videochat.\n"
    "La sala queda protegida y la presencia no autorizada queda bloqueada.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

VC_SCHED_MESSAGES = {
    "start": (
        "📡 <b>Videochat programada abierta</b>\n\n"
        "La sala de voz queda activa por horario del Bunker y la supervisión perimetral está en línea.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "end": (
        "📡 <b>Videochat programada cerrada</b>\n\n"
        "La sala de voz queda temporalmente cerrada según el horario programado del Bunker.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
}

GHOST_PURGE_ALERT_TEXT = (
    "🧹 <b>Purgado de cuentas fantasma</b>\n\n"
    "Se revisaron {found} cuentas eliminadas o fantasma y se purgaron {purged} con acción: {action}.\n\n"
    "La limpieza se ejecutó con supervisión del Bunker y la comunidad queda reforzada.\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

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

if not DEFAULT_API_ID or not DEFAULT_API_HASH:
    logger.warning("⚠ [Configuración] TELEGRAM_API_ID / TELEGRAM_API_HASH no configurados en el entorno.")
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
    logger.warning("⚠️ [MASTER_SESSION no configurado] Operando con Centinelas dedicados por comunidad.")

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


def _register_forbidden_strike(chat_id: int, action_label: str) -> int:
    current = _forbidden_strikes.get(chat_id, 0) + 1
    _forbidden_strikes[chat_id] = current
    logger.warning(f"⚠️ [Strike prohibido] Grupo {chat_id}: {action_label} (intento {current}/{FORBIDDEN_STRIKE_LIMIT})")

    if current >= FORBIDDEN_STRIKE_LIMIT:
        _autolower_cooldowns[chat_id] = time.monotonic() + FORBIDDEN_COOLDOWN_SECONDS
        logger.warning(
            f"⏳ [Cooldown activado] Grupo {chat_id} bloqueado por {FORBIDDEN_COOLDOWN_SECONDS}s por exceso de acciones prohibidas."
        )
    return current


def _register_noise_strike(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now_ts = time.monotonic()
    history = _noise_unmute_history.setdefault(key, [])
    history = [ts for ts in history if now_ts - ts <= NOISE_SPIKE_WINDOW_SECONDS]
    history.append(now_ts)
    _noise_unmute_history[key] = history

    if len(history) >= NOISE_SPIKE_STRIKE_LIMIT:
        history.clear()
        _noise_unmute_history[key] = history
        logger.warning(f"⚠️ [Spike de ruido] Usuario {user_id} superó el umbral en grupo {chat_id}.")
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
    except Exception as e:
        logger.debug(f"Aviso consultando modo nocturno para {chat_id}: {e}")
        return False, "error"


def _is_time_in_window(current_hm: str, start_hm: str, end_hm: str) -> bool:
    if start_hm <= end_hm:
        return start_hm <= current_hm < end_hm
    else:
        return current_hm >= start_hm or current_hm < end_hm


def _extract_urls_to_markup(
    text: str, 
    custom_btn_text: str = None, 
    custom_btn_url: str = None
) -> tuple[str, InlineKeyboardMarkup | None]:
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


async def _dispatch_radar_notice(
    chat_id: int,
    text: str,
    media_id: int | str | None = None,
    media_type: str | None = None,
    auto_delete_after: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    if not _global_bot:
        return None

    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        if media_id and media_type:
            type_key = media_type.lower()
            if type_key in {"photo", "video", "animation", "document", "voice", "audio"}:
                payload[type_key] = media_id

        sent = await _global_bot.send_message(**payload)

        if auto_delete_after and auto_delete_after > 0:
            await asyncio.sleep(auto_delete_after)
            try:
                await _global_bot.delete_message(chat_id, sent.message_id)
            except Exception:
                pass

        return sent
    except Exception as exc:
        logger.debug(f"Aviso enviando radar notice a {chat_id}: {exc}")
        return None


async def _dispatch_member_vc_notice(chat_id: int, user_name: str, lang: str = "es"):
    if not _global_bot:
        return None
    notice = (
        f"🎙️ <b>Participante en la sala</b>\n\n"
        f"{user_name} fue detectado en la llamada de voz y su estado fue revisado por el radar del Bunker."
    )
    try:
        return await _global_bot.send_message(chat_id=chat_id, text=notice, parse_mode="HTML")
    except Exception as exc:
        logger.debug(f"Aviso enviando VC notice a {chat_id}: {exc}")
        return None


async def _dispatch_pinned_vc_welcome(chat_id: int, lang: str = "es"):
    if not _global_bot:
        return None
    text = (
        "📡 <b>Videochat activa</b>\n\n"
        "La sala de voz está operativa y la supervisión perimetral del Bunker está en línea.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
    try:
        return await _global_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.debug(f"Aviso enviando welcome VC a {chat_id}: {exc}")
        return None


async def _dispatch_sentinel_payload(chat_id: int, origin: str = "manual"):
    if not _global_bot:
        return None

    try:
        cfg = await get_sentinel_payload_config(chat_id)
    except Exception as exc:
        logger.debug(f"Aviso consultando payload del centinela para {chat_id}: {exc}")
        return None

    if not cfg or cfg.get("enabled") != 1:
        return None

    payload_text = cfg.get("text")
    if not payload_text:
        return None

    media_id = cfg.get("media_id")
    media_type = cfg.get("media_type")
    auto_delete_after = cfg.get("auto_delete_after")
    try:
        auto_delete_after = int(auto_delete_after) if auto_delete_after is not None else None
    except (TypeError, ValueError):
        auto_delete_after = None

    cleaned_text, reply_markup = _extract_urls_to_markup(payload_text)
    return await _dispatch_radar_notice(
        chat_id=chat_id,
        text=cleaned_text,
        media_id=media_id,
        media_type=media_type,
        auto_delete_after=auto_delete_after if auto_delete_after and auto_delete_after > 0 else None,
        reply_markup=reply_markup,
    )


_LEETSPEAK_PATTERNS = [
    (re.compile(r'\bc[\W_]*p\b', re.IGNORECASE), "Material Ilícito Evasivo (CP)"),
    (re.compile(r'\bp[\W_]*[e3][\W_]*d[\W_]*[o0]\b', re.IGNORECASE), "Violación Perimetral Infantil"),
    (re.compile(r'\bk[\W_]*9\b', re.IGNORECASE), "Zoofilia Evasiva"),
    (re.compile(r'(free\s+stars|stars\s+hack|claim\s+airdrop|leaked\s+pass)', re.IGNORECASE), "Patrón de Estafa/Phishing"),
]


async def semantic_scan_content(text: str, custom_prompt: str = "") -> dict:
    if not text:
        return {"flagged": False, "reason": ""}
    for pattern, reason in _LEETSPEAK_PATTERNS:
        if pattern.search(text):
            return {"flagged": True, "reason": reason}

    if not mistral_client or len(text.strip()) < 8:
        return {"flagged": False, "reason": ""}

    system_prompt = (
        "Eres el Escudo Semántico de The Bunker OS. Analiza el siguiente texto de un chat de Telegram. "
        "Determina si contiene pornografía infantil, zoofilia, estafas financieras fraudulentas extremas, "
        "amenazas de muerte directas o leetspeak malicioso. "
        "Responde estrictamente en formato JSON con dos campos: 'flagged' (true/false) y 'reason' (breve explicación)."
    )

    try:
        response = await asyncio.to_thread(
            mistral_client.chat.complete,
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        return {"flagged": bool(data.get("flagged")), "reason": data.get("reason", "Infracción semántica")}
    except Exception as e:
        logger.debug(f"Aviso en análisis semántico: {e}")
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
    if not message.chat or message.chat.type == ChatType.PRIVATE:
        return
    chat_id = message.chat.id

    if client == assistant_app and chat_id in active_sentinels:
        dedicated = active_sentinels[chat_id]
        if dedicated.get("client") != assistant_app:
            return

    if message.from_user and message.from_user.is_self:
        return

    from_user = message.from_user
    user_id = from_user.id if from_user else 0
    text_content = (message.text or message.caption or "").strip()

    if user_id and not (from_user and from_user.is_bot):
        _spawn(record_hourly_chat_activity(chat_id))
        _spawn(add_user_reputation_xp(
            group_id=chat_id,
            user_id=user_id,
            full_name=from_user.first_name or "",
            username=from_user.username or ""
        ))

    tier = (await get_group_tier(chat_id) or "free").lower()
    is_ultra = tier in ("ultra_pro", "ultra") or (user_id and is_super_admin(user_id))

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
        me_username = (client.me.username or "").lower() if client.me else ""
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


# ==========================================
# 🛡️ BUCLE RESILIENTE DE MONITOREO DE GRUPOS
# ==========================================
async def _refresh_admin_cache(client: Client, chat_id: int, bot_client_id: int):
    try:
        admins = set()
        try:
            admin_members = await client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS)
            for member in admin_members:
                if getattr(member, "user", None):
                    admins.add(member.user.id)
        except Exception:
            pass

        if bot_client_id:
            admins.add(bot_client_id)

        admin_caches[chat_id] = {"admins": admins, "ts": asyncio.get_event_loop().time()}
        return admins
    except Exception as e:
        logger.debug(f"Aviso refrescando caché de administradores para {chat_id}: {e}")
        return admin_caches.get(chat_id, {}).get("admins", set())


async def _verify_active_membership(client: Client, chat_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, client.me.id if getattr(client, "me", None) else 0)
        return bool(member and getattr(member, "status", None) not in (None, "left", "kicked"))
    except Exception:
        try:
            chat = await client.get_chat(chat_id)
            return getattr(chat, "type", None) in (ChatType.GROUP, ChatType.SUPERGROUP)
        except Exception:
            return False


async def monitor_single_group(chat_id: int, peer, client: Client, bot_client_id: int, user_id: int = 0):
    alerted_users = set()
    current_call = None
    last_channel_check = 0
    is_joined_audio = False
    permission_warned = False
    call_start_time = 0

    while True:
        if not client.is_connected:
            try:
                await client.start()
                logger.info(f"🔄 [Centinela Reconectado] Sesión restablecida para el grupo {chat_id}.")
            except FATAL_SESSION_ERRORS as auth_err:
                logger.error(f"🔒 [Sesión Inválida] Centinela del grupo {chat_id} desautorizado: {auth_err}")
                if user_id:
                    try:
                        await revoke_owner_session(user_id, chat_id, reason=str(auth_err))
                    except Exception:
                        pass
                active_sentinels.pop(chat_id, None)
                return
            except Exception as reconnect_err:
                logger.warning(f"⚠️ [Centinela Desconectado] Grupo {chat_id} sin conexión, reintentando en 5s: {reconnect_err}")
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

            if not current_call or (current_time - last_channel_check > 45):
                try:
                    full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                    raw_call = full_chat_res.full_chat.call
                except PeerIdInvalid:
                    # 🔄 REINTENTO INTELIGENTE DE RESOLUCIÓN DE PEER
                    try:
                        peer = await client.resolve_peer(chat_id)
                        full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                        raw_call = full_chat_res.full_chat.call
                    except Exception:
                        raw_call = None
                except Exception:
                    try:
                        full_chat_res = await client.invoke(GetFullChat(chat_id=peer.chat_id))
                        raw_call = full_chat_res.full_chat.call
                    except Exception:
                        raw_call = None

                if raw_call:
                    new_call_id = raw_call.id
                    if not current_call or current_call.id != new_call_id:
                        call_start_time = asyncio.get_event_loop().time()
                        asyncio.create_task(_dispatch_pinned_vc_welcome(chat_id, lang="es"))
                    current_call = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
                    permission_warned = False
                else:
                    current_call = None
                    is_joined_audio = False
                    call_start_time = 0

                last_channel_check = current_time

            if current_call:
                night_active, night_action = await _is_night_active(chat_id)
                autolower_enabled = await get_autolower_status(chat_id)
                if autolower_enabled != 1 and not night_active:
                    await asyncio.sleep(10)
                    continue

                cooldown_until = _autolower_cooldowns.get(chat_id, 0)
                if current_time < cooldown_until:
                    await asyncio.sleep(15)
                    continue

                if not is_joined_audio:
                    try:
                        my_peer = await client.resolve_peer(bot_client_id)
                        await client.invoke(
                            JoinGroupCall(call=current_call, join_as=my_peer, muted=True, video_stopped=True, params=DataJSON(data="{}"))
                        )
                        is_joined_audio = True
                        _forbidden_strikes[chat_id] = 0
                    except Exception as join_err:
                        err_text = str(join_err).upper()
                        if "ALREADY_PARTICIPATED" in err_text or "DUPLICATE" in err_text:
                            is_joined_audio = True
                        elif "GROUPCALL_FORBIDDEN" in err_text:
                            _register_forbidden_strike(chat_id, "unirse al videochat (JoinGroupCall)")
                            await asyncio.sleep(25)
                            continue
                        else:
                            await asyncio.sleep(5)
                            continue

                res = await client.invoke(
                    GetGroupParticipants(call=current_call, ids=[], sources=[], offset="", limit=100)
                )
                
                participants = res.participants
                users_map = {u.id: u for u in res.users} if getattr(res, 'users', None) else {}
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
                                    try:
                                        await _dispatch_radar_notice(
                                            chat_id=chat_id,
                                            text=SCREEN_SHIELD_ALERT_TEXT.format(user_name=user_name),
                                            auto_delete_after=30
                                        )
                                    except Exception:
                                        pass
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
                            if user_obj and getattr(user_obj, "access_hash", None):
                                p_peer = InputPeerUser(user_id=u_id, access_hash=user_obj.access_hash)
                            else:
                                p_peer = await client.resolve_peer(u_id)

                            await client.invoke(
                                EditGroupCallParticipant(call=current_call, participant=p_peer, muted=desired_muted, volume=desired_volume)
                            )
                            _forbidden_strikes[chat_id] = 0
                        except Exception as e:
                            err_msg = str(e).upper()
                            if "GROUPCALL_FORBIDDEN" in err_msg:
                                if not permission_warned:
                                    permission_warned = True
                                _register_forbidden_strike(chat_id, "silenciar un participante")
                                await asyncio.sleep(25)
                                break
                            elif "GROUPCALL_INVALID" in err_msg or "CALL_ALREADY_ENDED" in err_msg:
                                current_call = None
                                is_joined_audio = False
                                break
                            continue

                        if u_id not in alerted_users and not night_active:
                            alerted_users.add(u_id)
                            raw_u = user_obj.username if (user_obj and getattr(user_obj, "username", None)) else ""
                            first_name = html.escape(user_obj.first_name) if (user_obj and getattr(user_obj, "first_name", None)) else f"Usuario {u_id}"
                            
                            if raw_u:
                                user_mention = f"@{raw_u}"
                            else:
                                user_mention = f'<a href="tg://user?id={u_id}">{first_name}</a>'

                            if _global_bot:
                                try:
                                    if noise_spike:
                                        await _dispatch_radar_notice(
                                            chat_id=chat_id,
                                            text=SCREEN_SHIELD_ALERT_TEXT.format(user_name=user_mention),
                                            auto_delete_after=30
                                        )
                                    else:
                                        _spawn(_dispatch_member_vc_notice(chat_id=chat_id, user_name=user_mention, lang="es"))
                                except Exception:
                                    pass

                if is_joined_audio and bot_client_id not in active_users:
                    is_joined_audio = False

                alerted_users.intersection_update(active_users)

        except FloodWait as fw:
            await asyncio.sleep(fw.value + 2)
        except PeerIdInvalid:
            await asyncio.sleep(60)
        except Exception as e:
            err_str = str(e).upper()
            if "GROUPCALL_INVALID" in err_str or "CALL_ALREADY_ENDED" in err_str:
                current_call = None
                is_joined_audio = False
            await asyncio.sleep(5)

        await asyncio.sleep(3)


async def vc_scheduler_loop():
    logger.info("🗓️ [Programador VC] Sistema de programación semanal iniciado con soporte de zona horaria.")
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
                        full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                        raw_call = full_chat_res.full_chat.call
                        if raw_call:
                            call_obj = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
                            await client.invoke(DiscardGroupCall(call=call_obj))
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
            async for dialog in session_client.get_dialogs(limit=250):
                if dialog.chat.id == group_id:
                    break
        except Exception as dialog_err:
            logger.debug(f"Aviso al poblar la libreta de contactos para {group_id}: {dialog_err}")

        is_member = await _verify_active_membership(session_client, group_id)
        if not is_member:
            logger.warning(f"⚠️️ [Aviso] Telegram no pudo confirmar la membresía inmediatamente en {group_id}. Intentando conectar...")

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
    except PeerIdInvalid:
        logger.error(f"⚠ [Peer ID Inválido] El Centinela no encontró el grupo {group_id} en sus chats activos.")
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
                except PeerIdInvalid:
                    pass
                except Exception:
                    pass

    await asyncio.gather(*[
        _load_one(row[0], row[1], row[2], row[3], row[4]) for row in sessions
    ])


async def radar_master_loop():
    while True:
        try:
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
                            except PeerIdInvalid:
                                continue
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Aviso en radar master loop: {e}")
        await asyncio.sleep(45)


async def cancel_phone_auth(uid: int):
    """Cancela una sesión pendiente de autenticación por teléfono si aún existe."""
    try:
        session_data = pending_auth_sessions.pop(uid, None)
        if not session_data:
            return False
        logger.info(f"🧹 [Auth Pendiente] Cancelando verificación telefónica de UID {uid} por expiración.")
        return True
    except Exception as e:
        logger.debug(f"Aviso cancelando auth pendiente {uid}: {e}")
        return False


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
    global _default_my_id
    if assistant_app is None:
        logger.warning("⚠ [Centinela Maestro Inactivo] Sin MASTER_SESSION; operando con Centinelas propios por comunidad.")
    else:
        try:
            if not assistant_app.is_connected:
                assistant_app.add_handler(
                    MessageHandler(sentinel_incoming_message_dispatcher, filters.group | filters.channel)
                )
                await assistant_app.start()
            me = await assistant_app.get_me()
            _default_my_id = me.id
            logger.info(f"🤖 [Centinela Maestro Activo] Online como: @{me.username or me.first_name}")
        except FATAL_SESSION_ERRORS as auth_err:
            logger.error(f"🔒 [MASTER_SESSION Inválida] {auth_err}. Opera con Centinelas propios por comunidad.")
        except Exception as e:
            err_msg = str(e)
            if "AUTH_KEY_DUPLICATED" in err_msg or "406" in err_msg:
                logger.warning("⚠️ [MASTER_SESSION Clave Duplicada] Telegram detectó uso simultáneo. El maestro continuará en reposo sin afectar a los centinelas dedicados.")
            else:
                logger.warning(f"⚠ [Aviso Centinela Maestro]: {e}")

    await load_all_sentinels()
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
        peer = await client.resolve_peer(chat_id)
        try:
            full_chat_res = await client.invoke(GetFullChannel(channel=peer))
        except Exception:
            full_chat_res = await client.invoke(GetFullChat(chat_id=peer.chat_id))
            
        raw_call = full_chat_res.full_chat.call
        if not raw_call:
            return False

        call = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
        participant_peer = await client.resolve_peer(user_id)
        await client.invoke(
            EditGroupCallParticipant(call=call, participant=participant_peer, muted=muted, volume=volume)
        )
        return True
    except Exception as e:
        logger.warning(f"Aviso en set_participant_mic para grupo {chat_id}: {e}")
        return False


async def _cut_video_and_remove(client: Client, current_call, chat_id: int, user_id: int, participant_peer):
    if not client or not current_call or participant_peer is None:
        return False
    try:
        await client.invoke(
            EditGroupCallParticipant(
                call=current_call,
                participant=participant_peer,
                muted=True,
                volume=0
            )
        )
        return True
    except Exception as e:
        logger.debug(f"Aviso cortando presentación de {user_id} en {chat_id}: {e}")
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
    logger.info(f"🎙️️ [Modo Podcast] Ducking activado al {duck_level}% y persistido en el grupo {group_id}")


async def disengage_podcast_ducking(group_id: int):
    await set_podcast_mode(group_id, 0)
    logger.info(f"🎙️ [Modo Podcast] Ducking desactivado y persistido en el grupo {group_id}")

async def execute_ghost_purge(chat_id: int, action: str = "ban") -> dict:
    """Purga de cuentas fantasma o eliminadas mediante MTProto o fallback de Bot API."""
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