"""
groups.py — The Bunker OS (Aiogram 3.x)

Núcleo de seguridad perimetral para grupos y supergrupos.
Motor Híbrido: Detección MTProto + Padrón Local Bot API + Protección Total.
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
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, ChatMemberUpdated, ChatJoinRequest, LabeledPrice, PreCheckoutQuery
)
import database.database as _db_module
from database.database import (
    get_antispam_filter, get_antispam_delete,
    get_antiflood_config, is_whitelisted, get_captcha_config,
    get_lock_status, get_warns_config, add_warning, ban_user,
    approve_group, register_user_group, get_blacklist,
    get_session_by_group,
    get_panic_status, activate_panic, deactivate_panic,
    get_screen_shield_status, set_screen_shield_status,
    set_podcast_duck_volume, set_noise_shield_status,
    get_speaker_price, set_speaker_price, add_to_speaker_queue,
    get_speaker_queue, pop_next_speaker, remove_from_speaker_queue, clear_speaker_queue,
    flag_userbot, is_userbot_flagged,
    get_ghost_purge_config, update_ghost_purge_scan_time
)
import assistant as _assistant_module
from assistant import active_sentinels, set_participant_mic, execute_ghost_purge
from middlewares.anti_spam import check_global_cas_spam

logger = logging.getLogger("groups_handler")
router = Router()

_db_reset_warnings = getattr(_db_module, "reset_warnings", None)


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
# 🧠 CACHÉ DE ESTADO DE MIEMBROS
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
# 🎚️ MATRIZ DE RANGOS — AUTOLOWER SELECTIVO & INMUNIDADES
# ==========================================
IMMUNE_TIERS = frozenset({"architect", "service", "sentinel", "owner", "admin", "whitelisted", "unknown"})


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

    if status is None:
        return "unknown"

    return "standard"


def _tier_is_privileged(tier: str) -> bool:
    return tier in IMMUNE_TIERS


async def _autolower_can_mute(bot: Bot, group_id: int, user_id: int, username: str = "") -> bool:
    tier = await get_privilege_tier(bot, group_id, user_id, username)
    return not _tier_is_privileged(tier)


FLOOD_CACHE = {}
CAPTCHA_SESSIONS = {}
RECENTLY_VERIFIED = {}


async def auto_delete_msg(message: Message, delay: int = 15):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


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
    group_name = chat.title or ("Canal Oficial" if is_channel else "Comunidad")
    user = event.from_user
    promoter_id = user.id if user else 8269470905

    await approve_group(group_id, tier="free")
    await register_user_group(promoter_id, group_id, group_name, chat_type=chat_type_str)

    bot_info = await bot.get_me()

    if is_channel:
        if user:
            ch_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📡 Consola del Canal / Studio Panel", url=f"https://t.me/{bot_info.username}?start=cset_{group_id}")]
            ])
            channel_welcome_text = (
                f"📡 <b>¡ESTUDIO DE CANAL CONECTADO! / CHANNEL CONNECTED!</b>\n\n"
                f"Has promovido a <b>@{bot_info.username}</b> como administrador en <b>{group_name}</b>.\n\n"
                f"• 🎙️ <b>Moderación de Lives:</b> Desmuteo inteligente tras 'Levantar Mano' (*Raise Hand*).\n"
                f"• 💎 <b>Membresías VIP:</b> Enlaces efímeros de 1 solo uso y expulsión automática de morosos.\n"
                f"• ⭐ <b>Propinas Stars:</b> Monetización directa en tus transmisiones.\n\n"
                f"Pulsa el botón inferior para abrir la consola de gestión de este canal en privado.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            try:
                await bot.send_message(chat_id=user.id, text=channel_welcome_text, reply_markup=ch_kb, parse_mode="HTML")
            except Exception as ex:
                logger.warning(f"Aviso al enviar bienvenida privada de canal al usuario {user.id}: {ex}")
    else:
        group_welcome_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Iniciar Bot / Start Bot", url=f"https://t.me/{bot_info.username}?start=true"),
                InlineKeyboardButton(text="⚙️ Configurar / Settings", url=f"https://t.me/{bot_info.username}?start=gset_{group_id}")
            ]
        ])

        group_welcome_text = (
            f"🛡️ <b>¡SISTEMA DE SEGURIDAD DESPLEGADO! / SECURITY BOT DEPLOYED!</b>\n\n"
            f"Hola a todos. He sido activado como administrador para blindar el perímetro de <b>{group_name}</b> con aduana alfanumérica, anti-spam y protección de transmisiones.\n\n"
            f"🇺🇸 <i>Greetings! I have been activated as an administrator to protect <b>{group_name}</b> with automated captcha customs, anti-spam shields, and stream monitoring.</i>\n\n"
            f"👑 <b>Panel de Control / Management:</b>\n"
            f"El Propietario del grupo puede pulsar los botones inferiores para configurar la matriz en privado.\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )

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


def generate_simple_keyboard(group_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Verificar Identidad / Verify Identity", callback_data=f"cap_simple_{group_id}_{user_id}")]
    ])


async def captcha_timeout_task(bot: Bot, group_id: int, user_id: int, timeout: int):
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    session_key = (group_id, user_id)
    if session_key in CAPTCHA_SESSIONS:
        session_data = CAPTCHA_SESSIONS.pop(session_key, {})
        cfg = await get_captcha_config(group_id)
        
        msg_id = session_data.get("msg_id")
        chat_msg_id = session_data.get("group_msg_id")
        
        if msg_id:
            try: 
                await bot.delete_message(chat_id=user_id, message_id=msg_id)
            except Exception: 
                pass

        if chat_msg_id:
            try:
                await bot.delete_message(chat_id=group_id, message_id=chat_msg_id)
            except Exception:
                pass

        support_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Soporte Técnico / Support", url="https://t.me/m/RGx4ohGTMTk5")]
        ])
        
        custom_msg = cfg["text"] if cfg["text"] else "⏱️ El tiempo límite para verificar tu identidad ha expirado."
        text = (
            f"❌ <b>Acceso Denegado / Access Denied</b>\n\n"
            f"{custom_msg}\n\n"
            f"🇺🇸 <i>Verification time expired. Automated perimeter protocol applied.</i>\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )

        try:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=support_kb, parse_mode="HTML")
        except Exception:
            try:
                fail_msg = await bot.send_message(
                    chat_id=group_id, 
                    text=f"❌ <a href='tg://user?id={user_id}'>Usuario / User</a> no completó la verificación a tiempo.", 
                    reply_markup=support_kb, 
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(fail_msg, 20))
            except Exception: 
                pass

        try:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
        except Exception:
            pass

        try:
            if cfg["action"] == "mute":
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            else:
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
        except Exception as e:
            logger.error(f"Error aplicando sanción por timeout a {user_id}: {e}")


async def process_user_captcha(bot: Bot, group_id: int, user_id: int, full_name: str, username: str, is_join_req: bool = False):
    cfg = await get_captcha_config(group_id)
    if cfg["status"] != 1:
        return

    if is_super_admin(user_id) or await is_sentinel_account(group_id, user_id, username) or await is_whitelisted(user_id):
        return

    try:
        member_check = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        if member_check.status in ["creator", "administrator"]:
            return
    except Exception:
        pass

    mention = f"<a href='tg://user?id={user_id}'>{full_name}</a>"

    if not is_join_req:
        try:
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        except Exception as e:
            logger.warning(f"Aviso restricción preventiva a {user_id} en {group_id}: {e}")

    custom_intro = cfg["text"] if cfg["text"] else f"¡Hola, {mention}! Para proteger la comunidad, requerimos una breve verificación."

    if cfg["mode"] == 1:
        correct_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        CAPTCHA_SESSIONS[(group_id, user_id)] = {"code": correct_code, "is_join_req": is_join_req}
        kb = generate_captcha_keyboard(group_id, user_id, correct_code)
        text = (
            f"🛑 <b>ADUANA DE SEGURIDAD / SECURITY CHECKPOINT</b>\n\n"
            f"{custom_intro}\n\n"
            f"Selecciona el botón que contiene exactamente este código:\n"
            f"👉 <code>{correct_code}</code>\n\n"
            f"⏱️ <b>Tiempo límite:</b> {cfg['time']}s\n\n"
            f"🇺🇸 <i>Tap the button matching the code above to clear entry!</i>\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )
    else:
        CAPTCHA_SESSIONS[(group_id, user_id)] = {"code": "simple", "is_join_req": is_join_req}
        kb = generate_simple_keyboard(group_id, user_id)
        text = (
            f"🛑 <b>ADUANA DE SEGURIDAD / SECURITY CHECKPOINT</b>\n\n"
            f"{custom_intro}\n\n"
            f"Pulsa el botón inferior para confirmar tu identidad:\n\n"
            f"🇺🇸 <i>Hit the button below to verify your account and join the chat!</i>\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )

    dm_sent = False
    try:
        sent_msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
        CAPTCHA_SESSIONS[(group_id, user_id)]["msg_id"] = sent_msg.message_id
        dm_sent = True
    except Exception:
        pass

    if not dm_sent and not is_join_req:
        try:
            group_msg = await bot.send_message(
                chat_id=group_id,
                text=(
                    f"🛑 <b>ADUANA DE SEGURIDAD / CHECKPOINT</b>\n\n"
                    f"{mention}, completa tu verificación en este botón para desbloquear tu acceso al chat:\n"
                    f"⏱️ <i>Tiempo restante: {cfg['time']}s</i>"
                ),
                reply_markup=kb,
                parse_mode="HTML"
            )
            CAPTCHA_SESSIONS[(group_id, user_id)]["group_msg_id"] = group_msg.message_id
        except Exception:
            pass

    task = asyncio.create_task(captcha_timeout_task(bot, group_id, user_id, cfg["time"]))
    CAPTCHA_SESSIONS[(group_id, user_id)]["task"] = task


# ==========================================
# 📡 SOLICITUDES DE UNIÓN (JOIN REQUESTS)
# ==========================================
@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot):
    group_id = event.chat.id
    user_id = event.from_user.id

    if await check_global_cas_spam(user_id):
        try:
            await bot.decline_chat_join_request(chat_id=group_id, user_id=user_id)
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

    if cfg["status"] != 1:
        try: 
            await bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
        except Exception: 
            pass
        return

    await process_user_captcha(bot, group_id, user_id, event.from_user.full_name, event.from_user.username or "", is_join_req=True)


@router.message(F.chat.type.in_({"group", "supergroup"}), F.new_chat_members)
async def handle_new_members(message: Message, bot: Bot):
    group_id = message.chat.id
    cfg = await get_captcha_config(group_id)

    if cfg.get("service_del") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass

    bot_me = await bot.get_me()
    now = time.time()

    for new_user in message.new_chat_members:
        if new_user.id == bot_me.id:
            continue

        if not new_user.is_bot:
            if await check_global_cas_spam(new_user.id):
                try:
                    await bot.ban_chat_member(chat_id=group_id, user_id=new_user.id)
                    logger.warning(f"🚨 [Anti-Spam Global CAS] Miembro spammer global {new_user.id} expulsado en {group_id}.")
                except Exception as e:
                    logger.error(f"Error expulsando spammer global {new_user.id}: {e}")
                continue

            await registry_track(group_id, new_user.id, force=True)

            if await enforce_userbot_flag(bot, group_id, new_user.id, new_user.username or "", source="ingreso"):
                continue

        if cfg["status"] != 1:
            continue

        if (group_id, new_user.id) in CAPTCHA_SESSIONS or (now - RECENTLY_VERIFIED.get((group_id, new_user.id), 0) < 120):
            continue

        await process_user_captcha(bot, group_id, new_user.id, new_user.full_name, new_user.username or "", is_join_req=False)


@router.chat_member(F.chat.type.in_({"group", "supergroup"}))
async def track_member_updates(event: ChatMemberUpdated, bot: Bot):
    user = event.new_chat_member.user
    if user.is_bot:
        return

    group_id = event.chat.id
    _MEMBER_STATUS_CACHE.pop((group_id, user.id), None)

    new_status = event.new_chat_member.status
    if new_status in ("left", "kicked"):
        await registry_forget(group_id, user.id)
        return
    if new_status == "restricted" and getattr(event.new_chat_member, "is_member", True) is False:
        await registry_forget(group_id, user.id)
        return

    await registry_track(group_id, user.id, force=True)
    await enforce_userbot_flag(bot, group_id, user.id, user.username or "", source="cambio de membresía")


@router.callback_query(F.data.startswith("cap_ver_") | F.data.startswith("cap_simple_"))
async def process_captcha(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
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

    clicker_id = callback.from_user.id
    if clicker_id != target_user_id:
        await callback.answer("⚠️ Este desafío no pertenece a tu perfil / Not your checkpoint.", show_alert=True)
        return
        
    session_key = (group_id, target_user_id)
    session_data = CAPTCHA_SESSIONS.pop(session_key, {})
    if "task" in session_data:
        session_data["task"].cancel()

    cfg = await get_captcha_config(group_id)

    try:
        await callback.message.delete()
    except Exception:
        pass

    group_msg_id = session_data.get("group_msg_id")
    if group_msg_id:
        try:
            await bot.delete_message(chat_id=group_id, message_id=group_msg_id)
        except Exception:
            pass

    try:
        chat_info = await bot.get_chat(group_id)
        group_title = chat_info.title or "la comunidad"
    except Exception:
        group_title = "la comunidad"

    if is_correct == 1:
        await callback.answer("✅ ¡Identidad verificada con éxito!", show_alert=False)
        RECENTLY_VERIFIED[(group_id, target_user_id)] = time.time()

        try:
            await bot.approve_chat_join_request(chat_id=group_id, user_id=target_user_id)
        except Exception:
            pass

        try:
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=target_user_id,
                permissions=ChatPermissions(
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
            )
        except Exception as e:
            logger.warning(f"Aviso otorgando permisos en {group_id}: {e}")
            
        user_full_name = callback.from_user.full_name
        user_mention = f"<a href='tg://user?id={target_user_id}'>{user_full_name}</a>"

        try:
            welcome_msg = await bot.send_message(
                chat_id=group_id,
                text=(
                    f"🎉 <b>¡Acceso Concedido! / Access Granted!</b>\n\n"
                    f"Estimado {user_mention}, has completado la aduana de seguridad con éxito.\n"
                    f"🔓 Tu acceso ha sido liberado para participar en <b>{group_title}</b>.\n\n"
                    f"🇺🇸 <i>Security checkpoint cleared. Welcome aboard!</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                parse_mode="HTML"
            )
            asyncio.create_task(auto_delete_msg(welcome_msg, 20))
        except Exception:
            pass

    else:
        await callback.answer("❌ Código incorrecto / Wrong code.", show_alert=True)
        try:
            support_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Soporte Técnico / Support", url="https://t.me/m/RGx4ohGTMTk5")]
            ])
            
            custom_fail = cfg["text"] if cfg["text"] else "❌ Código incorrecto ingresado en la aduana de seguridad."
            fail_text = (
                f"❌ <b>Verificación Fallida / Checkpoint Failed</b>\n\n"
                f"{custom_fail}\n\n"
                f"🇺🇸 <i>Incorrect code selected. Access protocol denied.</i>\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )

            try: 
                await bot.send_message(chat_id=target_user_id, text=fail_text, reply_markup=support_kb, parse_mode="HTML")
            except Exception: 
                pass

            try: 
                await bot.decline_chat_join_request(chat_id=group_id, user_id=target_user_id)
            except Exception: 
                pass

            if cfg["action"] == "mute":
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=target_user_id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            else:
                await bot.ban_chat_member(chat_id=group_id, user_id=target_user_id)
                await bot.unban_chat_member(chat_id=group_id, user_id=target_user_id)
        except Exception as ex:
            logger.error(f"Error aplicando sanción por fallo de captcha: {ex}")


# ==========================================
# 🧹 PURGA DE MENSAJES DE SERVICIO
# ==========================================
@router.message(F.chat.type.in_({"group", "supergroup"}), F.left_chat_member)
async def purge_left_member(message: Message):
    left_user = message.left_chat_member
    if left_user and not left_user.is_bot:
        await registry_forget(message.chat.id, left_user.id)

    cfg = await get_captcha_config(message.chat.id)
    if cfg.get("service_del") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass


@router.message(F.chat.type.in_({"group", "supergroup"}), F.video_chat_started | F.video_chat_ended | F.video_chat_participants_invited | F.pinned_message)
async def purge_general_service_messages(message: Message):
    cfg = await get_captcha_config(message.chat.id)
    if cfg.get("service_del") == 1:
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
    """Motor de respaldo completo por Bot API sobre el padrón local de SQLite."""
    added_by_sentinel = await _bootstrap_registry_from_sentinel(group_id)
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
                f"🧹 <b>Ghost Purge (Padrón Bot API) en curso...</b>\n\n"
                f"• Verificados: <b>{processed}/{len(candidates)}</b>\n"
                f"• Fantasmas encontrados: <b>{stats['ghost'] + stats['invalid']}</b>\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
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
    """
    Función Unificada de Entrada:
    1. Si hay un Centinela MTProto conectado, ejecuta el escaneo profundo.
    2. Si no hay Centinela o falla, conmuta al escáner completo de Bot API.
    """
    try:
        mtproto_res = await execute_ghost_purge(chat_id=group_id, action=action)
        if mtproto_res.get("status") == "success" and not dry_run:
            return {
                "engine": "MTProto Sentinel 💎",
                "found": mtproto_res.get("found", 0),
                "purged": mtproto_res.get("purged", 0),
                "action": action
            }
    except Exception as ex:
        logger.warning(f"Aviso MTProto Purge en {group_id}, pasando a Bot API: {ex}")

    bot_api_res = await run_bot_api_ghost_purge(bot, group_id, action=action, dry_run=dry_run, status_msg=status_msg)
    bot_api_res["engine"] = "Bot API Registry (Padrón Local) 🤖"
    return bot_api_res


@router.message(Command("purgeghosts"), F.chat.type.in_({"group", "supergroup"}))
async def purge_ghosts_command(message: Message, bot: Bot):
    group_id = message.chat.id
    username = message.from_user.username if message.from_user else ""
    user_id = message.from_user.id if message.from_user else 0

    is_authorized = await _message_author_is_admin(bot, message)
    if not is_authorized and user_id:
        is_authorized = await is_sentinel_account(group_id, user_id, username or "")

    if not is_authorized:
        warn = await message.answer("⛔ El comando /purgeghosts es exclusivo para administradores.", parse_mode="HTML")
        _spawn(auto_delete_msg(warn, 8))
        return

    args = (message.text or "").split()[1:]
    dry_run = bool(args) and args[0].strip().lower() in ("scan", "dry", "simular", "preview")
    action = "kick" if bool(args) and args[0].strip().lower() in ("kick", "expulsar") else "ban"

    if group_id in _GHOST_PURGE_RUNNING:
        busy = await message.answer("⏳ Ya hay un Ghost Purge en curso en esta comunidad. Espera a que termine.", parse_mode="HTML")
        _spawn(auto_delete_msg(busy, 8))
        return

    _GHOST_PURGE_RUNNING.add(group_id)
    try:
        status_msg = await message.answer(
            f"🧹 <b>Ghost Purge{' (simulación)' if dry_run else ''}:</b> Evaluando entorno y activando motor híbrido...\n\n"
            "🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )

        res = await execute_unified_ghost_purge(bot, group_id, action=action, dry_run=dry_run, status_msg=status_msg)
        engine_name = res.get("engine", "Híbrido")
        found = res.get("found", 0)
        purged = res.get("purged", 0)

        lines = [
            f"🧹 <b>Ghost Purge {'(Simulación) ' if dry_run else ''}Completado</b>",
            f"• <b>Motor Utilizado:</b> <code>{engine_name}</code>",
            f"• 👻 <b>Fantasmas Detectados:</b> <b>{found}</b>",
            f"• 💀 <b>Fantasmas Depurados:</b> <b>{purged}</b>",
            f"• ⚖️ <b>Acción Aplicada:</b> <code>{action.upper()}</code>",
            "",
            "🛡️ <i>Cloud Media Management</i>"
        ]

        await _edit_status(status_msg, "\n".join(lines))
        _spawn(auto_delete_msg(status_msg, 60))

    except Exception as e:
        logger.error(f"Error ejecutando Ghost Purge en {group_id}: {e}")
        try:
            await message.answer("⚠️ El Ghost Purge se interrumpió por un error inesperado.", parse_mode="HTML")
        except Exception:
            pass
    finally:
        _GHOST_PURGE_RUNNING.discard(group_id)
        # ==========================================
# 🛡️ EL BOTÓN DE PÁNICO (PROTOCOLO RAID LOCKDOWN)
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

    try:
        await bot.set_chat_permissions(chat_id=group_id, permissions=_lockdown_permissions())
    except Exception as e:
        logger.warning(f"Aviso: no se pudieron restringir los permisos de {group_id} en /panic: {e}")

    try:
        admins = await bot.get_chat_administrators(group_id)
        owner = next((a for a in admins if a.status == "creator"), None)
        if owner:
            alert_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🟢 Desactivar Raid Lockdown", callback_data=f"panic_off_{group_id}")]
            ])
            await bot.send_message(
                chat_id=owner.user.id,
                text=(
                    f"🚨 <b>PROTOCOLO RAID LOCKDOWN ACTIVADO</b>\n\n"
                    f"Comunidad: <b>{chat.title or 'Sin título'}</b> (<code>{group_id}</code>)\n"
                    f"Activado por: <code>{activated_by}</code>\n\n"
                    f"Se elevaron todas las cerraduras, el Captcha entró en modo estricto, el Anti-Spam "
                    f"subió su sensibilidad y el chat general quedó restringido sólo a administradores.\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                reply_markup=alert_kb,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.warning(f"Aviso: no se pudo notificar al dueño de {group_id} sobre /panic: {e}")

    return True


@router.message(Command("panic"), F.chat.type.in_({"group", "supergroup"}))
async def panic_command(message: Message, bot: Bot):
    group_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or ""

    if not (await _is_group_admin(bot, group_id, user_id) or await is_sentinel_account(group_id, user_id, username)):
        warn = await message.answer(
            "⛔ El Botón de Pánico es exclusivo para administradores de la comunidad.\n"
            "🇺🇸 <i>The Panic Button is restricted to community administrators.</i>",
            parse_mode="HTML"
        )
        asyncio.create_task(auto_delete_msg(warn, 8))
        return

    if await get_panic_status(group_id) == 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Desactivar Raid Lockdown", callback_data=f"panic_off_{group_id}")]
        ])
        await message.answer(
            "🛡️ El Protocolo Raid Lockdown ya está <b>activo</b> en esta comunidad.\n\n"
            "🛡️ <i>Cloud Media Management</i>",
            reply_markup=kb, parse_mode="HTML"
        )
        return

    engaged = await _engage_panic(bot, message.chat, user_id)
    if engaged:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Desactivar Raid Lockdown", callback_data=f"panic_off_{group_id}")]
        ])
        await message.answer(
            "🚨 <b>PROTOCOLO RAID LOCKDOWN ACTIVADO</b>\n\n"
            "Cerraduras al máximo, Captcha estricto, Anti-Spam elevado y chat general restringido sólo a administradores.\n\n"
            "🇺🇸 <i>Raid Lockdown engaged. Perimeter secured.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>",
            reply_markup=kb, parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("panic_off_"))
async def panic_deactivate_callback(callback: CallbackQuery, bot: Bot):
    group_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    if not (await _is_group_admin(bot, group_id, user_id) or await is_sentinel_account(group_id, user_id, callback.from_user.username or "")):
        await callback.answer("⛔ Sólo administradores pueden desactivar el Protocolo.", show_alert=True)
        return

    result = await deactivate_panic(group_id)
    perms_json = result.get("chat_permissions_json") if isinstance(result, dict) else None
    if perms_json:
        try:
            perms_dict = json.loads(perms_json)
            await bot.set_chat_permissions(chat_id=group_id, permissions=ChatPermissions(**perms_dict))
        except Exception as e:
            logger.warning(f"Aviso restaurando permisos de {group_id} tras desactivar /panic: {e}")

    try:
        await callback.message.edit_text(
            "✅ <b>Protocolo Raid Lockdown desactivado.</b>\n\n"
            "El perímetro de la comunidad fue restaurado a su estado previo a la alerta.\n\n"
            "🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Perímetro restaurado ✅")


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
    if not await _is_group_admin(bot, group_id, message.from_user.id):
        return
    args = message.text.split()[1:]
    status = _parse_on_off(args)
    await set_screen_shield_status(group_id, status)
    await message.answer(
        f"🎥 Escudo Antinota (Screen-Sharing Shield): <b>{'ACTIVADO' if status else 'DESACTIVADO'}</b>\n\n"
        f"🛡️ <i>Cloud Media Management</i>", parse_mode="HTML"
    )


@router.message(Command("duckvolume"), F.chat.type.in_({"group", "supergroup"}))
async def duck_volume_command(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _is_group_admin(bot, group_id, message.from_user.id):
        return
    args = message.text.split()[1:]
    if not args or not args[0].strip().isdigit():
        await message.answer("Uso: /duckvolume <porcentaje 1-100> — Ej: /duckvolume 5")
        return
    pct = max(1, min(100, int(args[0].strip())))
    await set_podcast_duck_volume(group_id, pct * 100)
    await message.answer(f"🎚️ Volumen de atenuación del Modo Podcast ajustado a <b>{pct}%</b>.", parse_mode="HTML")


@router.message(Command("noiseshield"), F.chat.type.in_({"group", "supergroup"}))
async def noiseshield_toggle(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _is_group_admin(bot, group_id, message.from_user.id):
        return
    args = message.text.split()[1:]
    status = _parse_on_off(args)
    await set_noise_shield_status(group_id, status)
    await message.answer(
        f"🔇 Escudo Antirruido: <b>{'ACTIVADO' if status else 'DESACTIVADO'}</b>\n\n"
        f"🛡️ <i>Cloud Media Management</i>", parse_mode="HTML"
    )


# ==========================================
# 💰 COLA DE SPEAKERS PAGADA (/speakers)
# ==========================================
@router.message(Command("speakers"), F.chat.type.in_({"group", "supergroup"}))
async def speakers_command(message: Message, bot: Bot):
    group_id = message.chat.id
    user_id = message.from_user.id
    args = message.text.split(maxsplit=2)[1:]
    sub = args[0].lower() if args else ""
    is_admin = await _is_group_admin(bot, group_id, user_id)

    if sub == "next":
        if not is_admin:
            return
        row = await pop_next_speaker(group_id)
        if not row:
            await message.answer("📭 La cola de oradores está vacía.")
            return
        _, spk_user_id, full_name, spk_username, stars_paid = row
        mention = f"<a href='tg://user?id={spk_user_id}'>{full_name}</a>"
        tag = f" (⭐ {stars_paid} XTR)" if stars_paid else ""
        await message.answer(
            f"🎙️ <b>Siguiente orador en la ronda de preguntas:</b> {mention}{tag}\n\n"
            f"🇺🇸 <i>Next up in the queue: {mention}{tag}</i>\n\n🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )
        return

    if sub == "clear":
        if not is_admin:
            return
        await clear_speaker_queue(group_id)
        await message.answer("🧹 Cola de oradores vaciada.")
        return

    if sub == "price":
        if not is_admin:
            return
        if len(args) > 1 and args[1].strip().isdigit():
            new_price = int(args[1].strip())
            await set_speaker_price(group_id, new_price)
            await message.answer(f"⭐ Tarifa de turno prioritario actualizada a <b>{new_price} Stars (XTR)</b>.", parse_mode="HTML")
        else:
            await message.answer("Uso: /speakers price <cantidad_de_stars>")
        return

    queue = await get_speaker_queue(group_id)
    price = await get_speaker_price(group_id)

    if queue:
        lines = []
        for idx, row in enumerate(queue[:10], start=1):
            _, spk_user_id, full_name, spk_username, stars_paid, _ts = row
            tag = f" ⭐{stars_paid}" if stars_paid else ""
            lines.append(f"{idx}. {full_name}{tag}")
        queue_text = "\n".join(lines)
    else:
        queue_text = "— La cola está vacía —"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎟️ Asegurar turno prioritario ({price} ⭐)", callback_data=f"speak_buy_{group_id}_{price}")]
    ])
    await message.answer(
        f"🎙️ <b>Cola de Preguntas (AMA) — {message.chat.title}</b>\n\n{queue_text}\n\n"
        f"💰 Paga <b>{price} Telegram Stars</b> y asegura tu prioridad en la próxima ronda de preguntas.\n\n"
        f"🇺🇸 <i>Pay {price} Telegram Stars to lock in priority in the next round.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("speak_buy_"))
async def speak_buy_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    group_id = int(parts[2])
    price = int(parts[3])
    await callback.answer()

    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="🎙️ Turno Prioritario — Cola de Preguntas",
            description="Asegura tu prioridad en la próxima ronda de preguntas (AMA) de la comunidad.",
            payload=f"speak_{group_id}_{callback.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label="Turno prioritario", amount=price)],
        )
    except Exception as e:
        logger.warning(f"Error enviando invoice de /speakers a {callback.from_user.id}: {e}")
        try:
            await bot.send_message(
                chat_id=callback.from_user.id,
                text="⚠️ No pude generarte la factura. Abre un chat privado conmigo primero e inténtalo de nuevo."
            )
        except Exception:
            try:
                await callback.message.answer("⚠️ Abre un privado con el bot primero para poder pagar con Stars.")
            except Exception:
                pass


@router.pre_checkout_query(F.invoice_payload.startswith("speak_"))
async def speakers_pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.successful_payment, F.successful_payment.invoice_payload.startswith("speak_"))
async def speakers_successful_payment(message: Message, bot: Bot):
    payload = message.successful_payment.invoice_payload
    try:
        _, group_id_str, user_id_str = payload.split("_")
        group_id, buyer_id = int(group_id_str), int(user_id_str)
    except Exception:
        return

    stars_paid = message.successful_payment.total_amount
    full_name = message.from_user.full_name
    username = message.from_user.username or ""

    await add_to_speaker_queue(group_id, buyer_id, full_name, username, stars_paid)

    await message.answer(
        f"✅ <b>¡Turno asegurado!</b>\n\nPagaste <b>{stars_paid} Stars</b> por prioridad en la cola de preguntas.\n\n"
        f"🛡️ <i>Cloud Media Management</i>", parse_mode="HTML"
    )
    try:
        await bot.send_message(
            chat_id=group_id,
            text=(
                f"🎙️ <a href='tg://user?id={buyer_id}'>{full_name}</a> aseguró un turno prioritario en la "
                f"cola de preguntas (⭐ {stars_paid} XTR).\n\n🛡️ <i>Cloud Media Management</i>"
            ),
            parse_mode="HTML"
        )
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
        elif USERBOT_ACTION == "mute":
            await bot.restrict_chat_member(
                chat_id=group_id, user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
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
# ⚖️ ESCALA CENTRALIZADA DE ADVERTENCIAS
# ==========================================
_REASON_TEXT = {
    "filter": (
        "tu mensaje ha sido retirado por infringir las directivas comunitarias.",
        "your message was removed for violating community guidelines. Strike logged."
    ),
    "flood": (
        "por favor modera la velocidad de tus mensajes en el chat.",
        "please slow down your message frequency in the chat. Strike logged."
    ),
    "manual": (
        "se ha registrado un strike por orden de la administración.",
        "a strike was logged by the administration."
    ),
    "command": (
        "el uso de comandos está restringido en esta comunidad.",
        "commands are restricted in this community. Strike logged."
    ),
}

_SANCTION_TEXT = {
    "mute": (
        "🔇", "Sanción Automática / Auto-Sanction",
        "ha sido silenciado por reincidencia en faltas comunitarias.",
        "User has been muted due to reaching the strike threshold."
    ),
    "kick": (
        "👢", "Sanción Automática / Auto-Sanction",
        "ha sido expulsado temporalmente por infringir las reglas.",
        "User has been kicked due to reaching the strike threshold."
    ),
    "ban": (
        "🚫", "Sanción Definitiva / Final Sanction",
        "ha sido bloqueado permanentemente del grupo por faltas graves.",
        "User has been permanently banned from the community."
    ),
}

WARN_ACTIONS = ("mute", "kick", "ban")


async def _send_temp(bot: Bot, chat_id: int, text: str, ttl: int, reply_to: Optional[Message] = None) -> None:
    try:
        if reply_to is not None:
            sent = await reply_to.answer(text, parse_mode="HTML")
        else:
            sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        _spawn(auto_delete_msg(sent, ttl))
    except Exception as e:
        logger.debug(f"No se pudo enviar aviso temporal en {chat_id}: {e}")


async def _reset_warnings_safe(user_id: int) -> None:
    if not RESET_WARNS_AFTER_SANCTION or _db_reset_warnings is None:
        return
    try:
        result = _db_reset_warnings(user_id)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.debug(f"No se pudieron reiniciar los warns de {user_id}: {e}")


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

    try:
        strikes = int(await add_warning(target_id))
        warns_cfg = await get_warns_config(chat_id)
        limit = max(1, int(warns_cfg["limit"]))
        action = str(warns_cfg["action"]).strip().lower()
    except Exception as e:
        logger.error(f"Error registrando strike de {target_id} en {chat_id}: {e}")
        return {"status": "error", "tier": tier, "strikes": 0, "limit": 0, "action": None}

    if action not in WARN_ACTIONS:
        action = "mute"

    if strikes < limit:
        await _acoustic_attenuate(bot, chat_id, target_id, target_username, strikes, limit)

        if not silent:
            es_txt, en_txt = _REASON_TEXT.get(reason, _REASON_TEXT["filter"])
            await _send_temp(
                bot, chat_id,
                f"⚠️ <b>Aviso de Seguridad / Security Notice ({strikes}/{limit})</b>\n\n"
                f"{target_mention}, {es_txt}\n"
                f"🇺🇸 <i>Notice: {target_mention}, {en_txt}</i>\n\n"
                f"🛡️ <i>Cloud Media Management</i>",
                ttl=20, reply_to=reply_to
            )
        return {"status": "warned", "tier": tier, "strikes": strikes, "limit": limit, "action": action}

    await _acoustic_attenuate(bot, chat_id, target_id, target_username, strikes, limit, force_mute=True)

    try:
        if action == "mute":
            await bot.restrict_chat_member(
                chat_id=chat_id, user_id=target_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        elif action == "kick":
            await bot.ban_chat_member(chat_id=chat_id, user_id=target_id, until_date=int(time.time() + 35))
            await bot.unban_chat_member(chat_id=chat_id, user_id=target_id, only_if_banned=True)
        elif action == "ban":
            await ban_user(target_id)
            await bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
    except Exception as e:
        logger.error(f"Error aplicando sanción '{action}' a {target_id} en {chat_id}: {e}")
        return {"status": "error", "tier": tier, "strikes": strikes, "limit": limit, "action": action}

    await _reset_warnings_safe(target_id)

    if action in ("kick", "ban"):
        await registry_forget(chat_id, target_id)

    if not silent:
        icon, title, es_txt, en_txt = _SANCTION_TEXT[action]
        await _send_temp(
            bot, chat_id,
            f"{icon} <b>{title} ({strikes}/{limit})</b>\n\n"
            f"{target_mention} {es_txt}\n"
            f"🇺🇸 <i>{en_txt}</i>\n\n"
            f"🛡️ <i>Cloud Media Management</i>",
            ttl=25, reply_to=reply_to
        )

    return {"status": "sanctioned", "tier": tier, "strikes": strikes, "limit": limit, "action": action}


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
        return

    if text_content.startswith("/") and await get_lock_status(group_id, "lock_commands") == 1:
        try:
            await message.delete()
        except Exception:
            pass

        outcome = await enforce_warn_ladder(
            bot, group_id, user_id, username, message.from_user.mention_html(),
            reply_to=message, reason="command", silent=True
        )
        return

    is_threat_detected = False
    lower_text = text_content.lower()

    if await get_lock_status(group_id, "lock_media") == 1 and (
        message.photo or message.video or message.document or 
        message.audio or message.voice or message.video_note or message.animation
    ):
        is_threat_detected = True
    elif await get_lock_status(group_id, "lock_stickers") == 1 and message.sticker:
        is_threat_detected = True
    elif await get_lock_status(group_id, "lock_links") == 1 and (
        "http://" in lower_text or "https://" in lower_text or 
        "www." in lower_text or "t.me/" in lower_text or "telegram.me/" in lower_text
    ):
        is_threat_detected = True

    if not is_threat_detected:
        blacklist = await get_blacklist()
        for b_word in blacklist:
            if b_word and b_word in lower_text:
                is_threat_detected = True
                break

    if not is_threat_detected:
        if await get_antispam_filter(group_id, "tg_links") == 1 and ("t.me/" in lower_text or "telegram.me/" in lower_text):
            is_threat_detected = True
        elif await get_antispam_filter(group_id, "web_links") == 1 and ("http://" in lower_text or "https://" in lower_text or "www." in lower_text):
            if "t.me/" not in lower_text and "telegram.me/" not in lower_text:
                is_threat_detected = True
        elif await get_antispam_filter(group_id, "quotes") == 1 and (message.quote or message.reply_to_message):
            quoted_text = (message.quote.text if message.quote else (message.reply_to_message.text or "")) if (message.quote or message.reply_to_message) else ""
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
        except Exception: 
            pass

        await enforce_warn_ladder(
            bot, group_id, user_id, username, message.from_user.mention_html(),
            reply_to=message, reason="filter"
        )
        return

    if is_edit:
        return

    af_cfg = await get_antiflood_config(group_id)
    max_msgs, time_window, af_action = af_cfg["msgs"], af_cfg["time"], af_cfg["action"]

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
                await _send_temp(
                    bot, group_id,
                    f"👢 <b>Aviso de Expulsión / Kick Notice:</b>\n"
                    f"{user_mention} ha sido expulsado temporalmente por saturación de mensajes (Flood).\n"
                    f"🇺🇸 <i>User kicked due to message flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    ttl=20, reply_to=message
                )
            elif af_action == "mute":
                await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                await _send_temp(
                    bot, group_id,
                    f"🔇 <b>Aviso de Silencio / Mute Notice:</b>\n"
                    f"{user_mention} ha sido silenciado por saturación masiva de mensajes (Flood).\n"
                    f"🇺🇸 <i>User muted due to message flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    ttl=20, reply_to=message
                )
            elif af_action == "ban":
                await ban_user(user_id)
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                await registry_forget(group_id, user_id)
                await _send_temp(
                    bot, group_id,
                    f"🚫 <b>Bloqueo Definitivo / Ban Notice:</b>\n"
                    f"{user_mention} ha sido baneado permanentemente por flood reiterado.\n"
                    f"🇺🇸 <i>User permanently banned due to continuous flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    ttl=25, reply_to=message
                )
        except Exception as e:
            logger.error(f"Error aplicando sanción anti-flood ({group_id}/{user_id}): {e}")


async def add_speaker_to_queue(group_id: int, user_id: int, full_name: str = "Speaker", username: str = "", stars_paid: int = 0):
    return await add_to_speaker_queue(group_id, user_id, full_name, username, stars_paid)