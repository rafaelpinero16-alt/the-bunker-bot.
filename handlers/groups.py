import time
import string
import random
import asyncio
import os
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, ChatMemberUpdated, ChatJoinRequest
)
from aiogram.filters import ChatMemberUpdatedFilter, ADMINISTRATOR, MEMBER
from database.database import (
    get_antispam_filter, get_antispam_delete,
    get_antiflood_config, is_whitelisted, get_captcha_config,
    get_lock_status, get_warns_config, add_warning, ban_user,
    approve_group, register_user_group, get_blacklist,
    get_session_by_group
)
from assistant import active_sentinels, set_participant_mic

logger = logging.getLogger("groups_handler")
router = Router()

# Memoria temporal en RAM optimizada
FLOOD_CACHE = {}
CAPTCHA_SESSIONS = {}
RECENTLY_VERIFIED = {}


async def auto_delete_msg(message: Message, delay: int = 15):
    """Elimina automáticamente mensajes temporales de alerta para evitar saturación visual."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


async def is_sentinel_account(group_id: int, user_id: int, username: str) -> bool:
    """Valida si el usuario corresponde al Centinela Maestro o a un Centinela Dedicado."""
    clean_username = (username or "").lower()
    if clean_username == "alphacentinel":
        return True

    sentinel_info = active_sentinels.get(group_id)
    if sentinel_info and sentinel_info.get("user_id") == user_id:
        return True

    session_row = await get_session_by_group(group_id)
    if session_row and session_row[0] == user_id:
        return True

    return False


# ==========================================
# 📡 OBSERVADOR UNIVERSAL DE MEMBRESÍA (MY_CHAT_MEMBER)
# ==========================================
@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER >> ADMINISTRATOR))
async def bot_added_as_admin(event: ChatMemberUpdated, bot: Bot):
    """Detecta automáticamente cuando el bot es promovido a administrador en un grupo."""
    chat = event.chat
    if chat.type not in {"group", "supergroup"}:
        return
    
    group_id = chat.id
    group_name = chat.title
    user = event.from_user

    await approve_group(group_id, tier="free")
    if user:
        await register_user_group(user.id, group_id, group_name)

    admin_group_id_raw = os.getenv("ADMIN_GROUP_ID")
    if admin_group_id_raw:
        try:
            admin_group_id = int(admin_group_id_raw)
            bot_info = await bot.get_me()
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⚙️ Configurar Matriz", url=f"https://t.me/{bot_info.username}?start=gset_{group_id}"),
                    InlineKeyboardButton(text="⚡ Elevar a PRO/ULTRA", url=f"https://t.me/{bot_info.username}?start=sub_pro_{group_id}")
                ]
            ])
            
            invite_link = f"https://t.me/{chat.username}" if chat.username else f"ID: <code>{group_id}</code>"
            
            await bot.send_message(
                chat_id=admin_group_id,
                text=(
                    f"📡 <b>NUEVA COMUNIDAD ENLAZADA AL BÚNKER</b>\n\n"
                    f"• <b>Comunidad:</b> {group_name}\n"
                    f"• <b>Chat ID:</b> <code>{group_id}</code>\n"
                    f"• <b>Enlace / Ref:</b> {invite_link}\n"
                    f"• <b>Autor de Alta:</b> {user.full_name if user else 'N/A'} (<code>{user.id if user else 'N/A'}</code>)\n\n"
                    f"✅ El bot ha sido desplegado con éxito y está listo para auditar el perímetro.\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Error notificando nuevo grupo a ADMIN_GROUP_ID: {e}")


# ==========================================
# 🤖 ADUANA DE SEGURIDAD (CAPTCHA PRO BILINGÜE)
# ==========================================
def generate_captcha_keyboard(group_id: int, user_id: int, correct_code: str) -> InlineKeyboardMarkup:
    """Genera un teclado de selección múltiple con opciones aleatorias y un único código correcto."""
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
    """Genera el botón estándar de un solo toque para el modo básico."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Verificar Identidad / Verify Identity", callback_data=f"cap_simple_{group_id}_{user_id}")]
    ])


async def captcha_timeout_task(bot: Bot, group_id: int, user_id: int, timeout: int):
    """Tarea en segundo plano para controlar la expiración de la aduana."""
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    session_key = (group_id, user_id)
    if session_key in CAPTCHA_SESSIONS:
        session_data = CAPTCHA_SESSIONS.pop(session_key, {})
        cfg = await get_captcha_config(group_id)
        
        msg_id = session_data.get("msg_id")
        if msg_id:
            try: 
                await bot.delete_message(chat_id=user_id, message_id=msg_id)
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
                    text=f"❌ <a href='tg://user?id={user_id}'>Usuario</a> no completó la verificación a tiempo.", 
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
    """Procesa y despacha el captcha perimetral a nuevos miembros."""
    cfg = await get_captcha_config(group_id)
    if cfg["status"] != 1:
        return

    # Inmunidad total para Centinelas y cuentas de Whitelist
    if await is_sentinel_account(group_id, user_id, username):
        return

    try:
        if await is_whitelisted(user_id):
            return
        member_check = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        if member_check.status in ["creator", "administrator"]:
            return
    except Exception:
        if await is_whitelisted(user_id):
            return

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

    bot_user = await bot.get_me()
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
            f"🇺🇸 <i>Tap the button matching the code above to clear entry! Don't let the clock run out.</i>\n\n"
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

    try:
        sent_msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
        CAPTCHA_SESSIONS[(group_id, user_id)]["msg_id"] = sent_msg.message_id
    except Exception:
        fallback_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔓 Verificarme en Privado / Verify", url=f"https://t.me/{bot_user.username}?start=cap_{group_id}")]
        ])
        try:
            fallback_msg = await bot.send_message(
                chat_id=group_id,
                text=(
                    f"🛡️ {mention}, por favor inicia un chat privado conmigo para verificar tu identidad y desbloquear tu acceso.\n\n"
                    f"🇺🇸 <i>Please click the button below to clear the security checkpoint!</i>"
                ),
                reply_markup=fallback_kb,
                parse_mode="HTML"
            )
            CAPTCHA_SESSIONS[(group_id, user_id)]["msg_id"] = fallback_msg.message_id
            asyncio.create_task(auto_delete_msg(fallback_msg, cfg["time"] + 10))
        except Exception:
            pass

    task = asyncio.create_task(captcha_timeout_task(bot, group_id, user_id, cfg["time"]))
    CAPTCHA_SESSIONS[(group_id, user_id)]["task"] = task


# ==========================================
# 📡 SOLICITUDES DE UNIÓN (JOIN REQUESTS)
# ==========================================
@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot):
    """Intercepta solicitudes de ingreso cuando el grupo opera con enlace de aprobación."""
    group_id = event.chat.id
    user_id = event.from_user.id
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
    """Detecta nuevos miembros que ingresan por enlace directo."""
    group_id = message.chat.id
    cfg = await get_captcha_config(group_id)

    if cfg.get("service_del") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass

    if cfg["status"] != 1:
        return

    bot_me = await bot.get_me()
    now = time.time()

    for new_user in message.new_chat_members:
        if new_user.id == bot_me.id:
            continue

        if (group_id, new_user.id) in CAPTCHA_SESSIONS or (now - RECENTLY_VERIFIED.get((group_id, new_user.id), 0) < 120):
            continue

        await process_user_captcha(bot, group_id, new_user.id, new_user.full_name, new_user.username or "", is_join_req=False)


@router.callback_query(F.data.startswith("cap_ver_") | F.data.startswith("cap_simple_"))
async def process_captcha(callback: CallbackQuery, bot: Bot):
    """Evalúa la respuesta de la aduana y desbloquea permisos al usuario."""
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
        await callback.answer("⚠️ Este desafío no pertenece a tu perfil.", show_alert=True)
        return
        
    session_key = (group_id, target_user_id)
    session_data = CAPTCHA_SESSIONS.pop(session_key, {})
    if "task" in session_data:
        session_data["task"].cancel()

    cfg = await get_captcha_config(group_id)

    try:
        chat_info = await bot.get_chat(group_id)
        group_title = chat_info.title or "la comunidad"
    except Exception:
        chat_info = None
        group_title = "la comunidad"

    group_invite = None
    if chat_info:
        if chat_info.username:
            group_invite = f"https://t.me/{chat_info.username}"
        elif chat_info.invite_link:
            group_invite = chat_info.invite_link

    if not group_invite:
        try:
            group_invite = await bot.export_chat_invite_link(group_id)
        except Exception:
            try:
                new_link = await bot.create_chat_invite_link(group_id, name="Acceso Captcha")
                group_invite = new_link.invite_link
            except Exception:
                bot_me = await bot.get_me()
                group_invite = f"https://t.me/{bot_me.username}"

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
            
        try: 
            await callback.message.delete()
        except Exception: 
            pass
            
        user_full_name = callback.from_user.full_name
        user_mention = f"<a href='tg://user?id={target_user_id}'>{user_full_name}</a>"

        success_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👉 Entrar a {group_title} / Enter Chat", url=group_invite)]
        ])
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"🎉 <b>¡Verificación completada con éxito!</b>\n\n"
                    f"Bienvenido a <b>{group_title}</b>. Tu acceso ha sido aprobado y liberado.\n\n"
                    f"🇺🇸 <i>Checkpoint cleared! Welcome to the crew. Tap below to jump straight in!</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                reply_markup=success_kb,
                parse_mode="HTML"
            )
        except Exception:
            pass

        try:
            welcome_msg = await bot.send_message(
                chat_id=group_id,
                text=(
                    f"🎉 <b>¡Acceso concedido! Bienvenido a {group_title}</b>\n\n"
                    f"Estimado {user_mention}, has completado la aduana de seguridad con éxito.\n"
                    f"🔓 Tu acceso ha sido liberado para participar en la comunidad.\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                parse_mode="HTML"
            )
            asyncio.create_task(auto_delete_msg(welcome_msg, 25))
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
                await callback.message.edit_text(fail_text, reply_markup=support_kb, parse_mode="HTML")
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
    """Purga de mensajes de servicio cuando un usuario se retira."""
    cfg = await get_captcha_config(message.chat.id)
    if cfg.get("service_del") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass


@router.message(F.chat.type.in_({"group", "supergroup"}), F.video_chat_started | F.video_chat_ended | F.video_chat_participants_invited | F.pinned_message)
async def purge_general_service_messages(message: Message):
    """Purga de avisos de inicio/cierre de videochat, fijado e invitaciones."""
    cfg = await get_captcha_config(message.chat.id)
    if cfg.get("service_del") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass


# ==========================================
# 🛡️ MATRIZ DE SEGURIDAD (LOCKS, ANTISPAM & ANTIFLOOD)
# ==========================================
@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_security_matrix(message: Message, bot: Bot):
    """Núcleo de inspección: Cerraduras, Blacklist, Anti-Spam, Advertencias y Anti-Flood."""
    if not message.from_user or message.from_user.is_bot:
        return

    group_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or ""
    text_content = message.text or message.caption or ""

    # Inmunidad para Centinelas
    if await is_sentinel_account(group_id, user_id, username):
        return

    is_creator = False
    is_admin = False
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        is_creator = (member.status == "creator")
        is_admin = (member.status in ["creator", "administrator"])
    except Exception:
        pass

    # El Creador (Dueño) y usuarios en Whitelist tienen inmunidad total
    if is_creator or await is_whitelisted(user_id):
        return

    # 🛑 Cerradura de Comandos (Lock Commands): Exclusivo para el Dueño
    if text_content.startswith("/") and await get_lock_status(group_id, "lock_commands") == 1:
        try: 
            await message.delete()
        except Exception: 
            pass

        user_mention = message.from_user.mention_html()
        temp_warn = await message.answer(
            f"⛔ {user_mention}, la ejecución de comandos en este grupo está reservada <b>exclusivamente para el Dueño de la comunidad</b>.\n\n"
            f"🇺🇸 <i>Command execution is locked to the Community Owner.</i>\n\n"
            f"🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )
        asyncio.create_task(auto_delete_msg(temp_warn, 8))
        return

    # Si es administrador autorizado no aplican los filtros de usuarios comunes
    if is_admin:
        return

    is_threat_detected = False
    lower_text = text_content.lower()

    # 1. 🔒 Verificación de Cerraduras (Locks)
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

    # 2. ⚫ Verificación de Lista Negra (Blacklist)
    if not is_threat_detected:
        blacklist = await get_blacklist()
        for b_word in blacklist:
            if b_word and b_word in lower_text:
                is_threat_detected = True
                break

    # 3. ✉️ Verificación Anti-Spam Granular
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

    # ⚠️ Procesamiento de Infracción (Borrado + Warns + Mensaje de Servicio Tagueado)
    if is_threat_detected:
        try: 
            await message.delete()
        except Exception: 
            pass

        warnings = await add_warning(user_id)
        warns_cfg = await get_warns_config(group_id)
        limit = warns_cfg["limit"]
        action = warns_cfg["action"]
        user_mention = message.from_user.mention_html()

        if warnings >= limit:
            try:
                # Silenciar al infractor en la sala de audio/videochat en vivo
                try:
                    await set_participant_mic(chat_id=group_id, user_id=user_id, muted=True, volume=0)
                except Exception:
                    pass

                if action == "mute":
                    await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                    msg_sancion = await message.answer(
                        f"🔇 <b>Sanción Automática: Límite de Advertencias ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido silenciado en el chat y salas de audio por reincidencia en faltas comunitarias.\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                elif action == "kick":
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=int(time.time() + 35))
                    await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                    msg_sancion = await message.answer(
                        f"👢 <b>Sanción Automática: Límite de Advertencias ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido expulsado temporalmente por infringir las reglas de la comunidad.\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                elif action == "ban":
                    await ban_user(user_id)
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                    msg_sancion = await message.answer(
                        f"🚫 <b>Sanción Definitiva: Límite de Advertencias ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido expulsado y bloqueado permanentemente del grupo por faltas graves reiteradas.\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                asyncio.create_task(auto_delete_msg(msg_sancion, 25))
            except Exception as e:
                logger.error(f"Error aplicando sanción por warns acumulados: {e}")
        else:
            try:
                warn_msg = await message.answer(
                    f"⚠️ <b>Aviso de Seguridad ({warnings}/{limit})</b>\n\n"
                    f"{user_mention}, tu mensaje ha sido retirado automáticamente por infringir las directivas del grupo (enlace, término o contenido no permitido).\n\n"
                    f"🇺🇸 <i>Notice: {user_mention}, your message was removed for violating community guidelines. Strike logged.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(warn_msg, 20))
            except Exception:
                pass
        return

    # 4. 🗣️ Verificación Anti-Flood con Detección Dinámica
    af_cfg = await get_antiflood_config(group_id)
    max_msgs, time_window, af_action = af_cfg["msgs"], af_cfg["time"], af_cfg["action"]

    now = time.time()
    cache_key = (group_id, user_id)
    
    if cache_key not in FLOOD_CACHE: 
        FLOOD_CACHE[cache_key] = []
    
    FLOOD_CACHE[cache_key] = [t for t in FLOOD_CACHE[cache_key] if now - t < time_window]
    FLOOD_CACHE[cache_key].append(now)

    # Limpieza preventiva para optimizar la memoria RAM
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
            
            # Silenciar en videollamada al instante
            try:
                await set_participant_mic(chat_id=group_id, user_id=user_id, muted=True, volume=0)
            except Exception:
                pass

            if af_action == "warn": 
                f_msg = await message.answer(
                    f"⚠️ <b>Alerta Anti-Flood:</b> {user_mention}, por favor modera la velocidad de tus mensajes en el chat.\n\n🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 12))
            elif af_action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                f_msg = await message.answer(
                    f"👢 <b>Aviso de Expulsión:</b> {user_mention} ha sido expulsado temporalmente por saturación de mensajes (Flood).\n\n🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 20))
            elif af_action == "mute":
                await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                f_msg = await message.answer(
                    f"🔇 <b>Aviso de Silencio:</b> {user_mention} ha sido silenciado por saturación masiva de mensajes (Flood).\n\n🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 20))
            elif af_action == "ban":
                await ban_user(user_id)
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                f_msg = await message.answer(
                    f"🚫 <b>Bloqueo Definitivo:</b> {user_mention} ha sido baneado permanentemente por flood reiterado.\n\n🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 25))
        except Exception: 
            pass