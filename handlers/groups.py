import time
import string
import random
import asyncio
import os
import json
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, ChatMemberUpdated, ChatJoinRequest, LabeledPrice, PreCheckoutQuery
)
from database.database import (
    get_antispam_filter, get_antispam_delete,
    get_antiflood_config, is_whitelisted, get_captcha_config,
    get_lock_status, get_warns_config, add_warning, ban_user,
    approve_group, register_user_group, get_blacklist,
    get_session_by_group,
    get_panic_status, activate_panic, deactivate_panic,
    get_screen_shield_status, set_screen_shield_status,
    get_podcast_config, set_podcast_mode, set_podcast_duck_volume, set_noise_shield_status,
    get_speaker_price, set_speaker_price, add_to_speaker_queue,
    get_speaker_queue, pop_next_speaker, remove_from_speaker_queue, clear_speaker_queue
)
from assistant import active_sentinels, set_participant_mic

logger = logging.getLogger("groups_handler")
router = Router()

# Inmunidad total para los Arquitectos Supremos
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


async def _is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Valida si el usuario es Dueño/Administrador de la comunidad (o Arquitecto Supremo)."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False

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
@router.my_chat_member()
async def bot_added_as_admin(event: ChatMemberUpdated, bot: Bot):
    """Detecta automáticamente cuando el bot maestro o cualquier clon es promovido a administrador."""
    if event.new_chat_member.status not in ["administrator", "creator"]:
        return
    if event.old_chat_member.status in ["administrator", "creator"]:
        return

    chat = event.chat
    if chat.type not in {"group", "supergroup"}:
        return
    
    group_id = chat.id
    group_name = chat.title or "Comunidad"
    user = event.from_user

    await approve_group(group_id, tier="free")
    if user:
        await register_user_group(user.id, group_id, group_name)

    bot_info = await bot.get_me()

    # Mensaje de bienvenida público enviado directamente al grupo con enlaces al bot activo
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

    # Notificación de auditoría a la consola de administración
    admin_group_id_raw = os.getenv("ADMIN_GROUP_ID")
    if admin_group_id_raw:
        try:
            admin_group_id = int(admin_group_id_raw)
            kb_admin = InlineKeyboardMarkup(inline_keyboard=[
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
                    f"• <b>Instancia Operativa:</b> @{bot_info.username}\n"
                    f"• <b>Comunidad:</b> {group_name}\n"
                    f"• <b>Chat ID:</b> <code>{group_id}</code>\n"
                    f"• <b>Enlace / Ref:</b> {invite_link}\n"
                    f"• <b>Autor de Alta:</b> {user.full_name if user else 'N/A'} (<code>{user.id if user else 'N/A'}</code>)\n\n"
                    f"✅ Instancia desplegada con éxito. Perímetro activo.\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ),
                reply_markup=kb_admin,
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
    """Procesa y despacha el captcha perimetral a nuevos miembros con tolerancia a fallo de DM."""
    cfg = await get_captcha_config(group_id)
    if cfg["status"] != 1:
        return

    # Inmunidad total para Arquitectos, Centinelas y Whitelist
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

    # Si el usuario no tiene abierto el privado del bot, se despliega la aduana directamente en el grupo
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
    """Evalúa la respuesta de la aduana y desbloquea permisos al usuario en tiempo real."""
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

    # Limpieza del mensaje de aduana
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
        chat_info = None
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
    """Silencio perimetral: nadie salvo administradores puede enviar nada en el chat general."""
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

    # 1. Cerraduras al máximo + captcha estricto + antispam elevado: ya aplicado en activate_panic().
    # 2. Silencio perimetral: sólo administradores pueden hablar en el chat general.
    try:
        await bot.set_chat_permissions(chat_id=group_id, permissions=_lockdown_permissions())
    except Exception as e:
        logger.warning(f"Aviso: no se pudieron restringir los permisos globales de {group_id} en /panic: {e}")

    # 3. Reporte de alerta a la consola privada del dueño del grupo.
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
                    f"🇺🇸 <i>Raid Lockdown engaged: locks maxed, strict captcha, tighter anti-spam and the "
                    f"general chat is now admin-only.</i>\n\n"
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
            "Cerraduras al máximo, Captcha estricto, Anti-Spam elevado y chat general restringido "
            "sólo a administradores.\n\n"
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
    perms_json = result.get("chat_permissions_json")
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


# ==========================================
# 🌉 PUENTE PÚBLICO PARA EL PANEL PRIVADO (user_private.py)
#
# El Botón de Pánico ULTRA PRO del Command Center privado no tiene acceso a un
# Message/ChatJoinRequest de grupo, sólo a `bot` y `group_id`. Estas dos
# funciones son el contrato de importación que consume user_private.py y
# reutilizan EXACTAMENTE la misma lógica que ya corre para /panic y el botón
# "panic_off_" en el grupo (_engage_panic / deactivate_panic), sin duplicar
# reglas de negocio ni tocar la firma de las funciones de database.database.
# ==========================================
async def execute_raid_lockdown(bot: Bot, group_id: int) -> bool:
    """
    Activa el Raid Lockdown desde fuera del grupo (panel privado ULTRA PRO).
    Devuelve True si el bloqueo se aplicó, False si ya estaba activo o si el
    chat no pudo resolverse (p. ej. el bot fue removido del grupo).
    """
    if await get_panic_status(group_id) == 1:
        return False

    try:
        chat = await bot.get_chat(group_id)
    except Exception as e:
        logger.warning(f"Aviso: no se pudo resolver el chat {group_id} en execute_raid_lockdown (panel privado): {e}")
        return False

    # activated_by=0 identifica activaciones disparadas desde el panel privado
    # (sin un Message de grupo del que tomar el user_id del solicitante).
    return await _engage_panic(bot, chat, activated_by=0)


async def lift_raid_lockdown(bot: Bot, group_id: int) -> bool:
    """
    Levanta el Raid Lockdown desde el panel privado ULTRA PRO, restaurando los
    permisos que existían antes del bloqueo (snapshot guardado por
    activate_panic()). Devuelve True si se desactivó y se intentó restaurar
    permisos, False si el protocolo no estaba activo.
    """
    result = await deactivate_panic(group_id)
    if not result:
        return False

    perms_json = result.get("chat_permissions_json") if isinstance(result, dict) else None
    if perms_json:
        try:
            perms_dict = json.loads(perms_json)
            await bot.set_chat_permissions(chat_id=group_id, permissions=ChatPermissions(**perms_dict))
        except Exception as e:
            logger.warning(f"Aviso restaurando permisos de {group_id} en lift_raid_lockdown (panel privado): {e}")

    return True


# ==========================================
# 🎥🎙️ INTERRUPTORES RÁPIDOS: ESCUDO ANTINOTA, MODO PODCAST & ESCUDO ANTIRRUIDO
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


@router.message(Command("podcast"), F.chat.type.in_({"group", "supergroup"}))
async def podcast_toggle(message: Message, bot: Bot):
    group_id = message.chat.id
    if not await _is_group_admin(bot, group_id, message.from_user.id):
        return
    args = message.text.split()[1:]
    status = _parse_on_off(args, default_on=True)
    await set_podcast_mode(group_id, status)
    await message.answer(
        f"🎙️ Modo Podcast (Audio Ducking Dinámico): <b>{'ACTIVADO' if status else 'DESACTIVADO'}</b>\n"
        f"Cuando el orador principal hable, el resto de participantes no autorizados se atenuará "
        f"automáticamente en segundo plano.\n\n🛡️ <i>Cloud Media Management</i>", parse_mode="HTML"
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
# 💰 COLA DE PREGUNTAS PAGADA (/speakers — TELEGRAM STARS XTR)
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
            await message.answer("📭 La cola de preguntas está vacía por ahora.")
            return
        _, spk_user_id, full_name, spk_username, stars_paid = row
        mention = f"<a href='tg://user?id={spk_user_id}'>{full_name}</a>"
        tag = f" (⭐ {stars_paid} XTR)" if stars_paid else ""
        await message.answer(
            f"🎙️ <b>Siguiente turno en la ronda de preguntas:</b> {mention}{tag}\n\n"
            f"🇺🇸 <i>Next up in the AMA queue: {mention}{tag}</i>\n\n🛡️ <i>Cloud Media Management</i>",
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
        f"🇺🇸 <i>Pay {price} Telegram Stars to lock in priority in the next AMA round.</i>\n\n"
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

    # Inmunidad para Arquitectos Supremos y Centinelas
    if is_super_admin(user_id) or await is_sentinel_account(group_id, user_id, username):
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

    # Cerradura de Comandos (Lock Commands): Exclusivo para el Dueño
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

    # 1. Verificación de Cerraduras (Locks)
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

    # 2. Verificación de Lista Negra (Blacklist)
    if not is_threat_detected:
        blacklist = await get_blacklist()
        for b_word in blacklist:
            if b_word and b_word in lower_text:
                is_threat_detected = True
                break

    # 3. Verificación Anti-Spam Granular
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

    # Procesamiento de Infracción (Borrado + Warns + Sanción Bilingüe)
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
                try:
                    await set_participant_mic(chat_id=group_id, user_id=user_id, muted=True, volume=0)
                except Exception:
                    pass

                if action == "mute":
                    await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                    msg_sancion = await message.answer(
                        f"🔇 <b>Sanción Automática / Auto-Sanction ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido silenciado por reincidencia en faltas comunitarias.\n"
                        f"🇺🇸 <i>User has been muted due to reaching the strike threshold.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                elif action == "kick":
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=int(time.time() + 35))
                    await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                    msg_sancion = await message.answer(
                        f"👢 <b>Sanción Automática / Auto-Sanction ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido expulsado temporalmente por infringir las reglas.\n"
                        f"🇺🇸 <i>User has been kicked due to reaching the strike threshold.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                elif action == "ban":
                    await ban_user(user_id)
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                    msg_sancion = await message.answer(
                        f"🚫 <b>Sanción Definitiva / Final Sanction ({warnings}/{limit})</b>\n\n"
                        f"{user_mention} ha sido bloqueado permanentemente del grupo por faltas graves.\n"
                        f"🇺🇸 <i>User has been permanently banned from the community.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>",
                        parse_mode="HTML"
                    )
                asyncio.create_task(auto_delete_msg(msg_sancion, 25))
            except Exception as e:
                logger.error(f"Error aplicando sanción por warns acumulados: {e}")
        else:
            try:
                warn_msg = await message.answer(
                    f"⚠️ <b>Aviso de Seguridad / Security Notice ({warnings}/{limit})</b>\n\n"
                    f"{user_mention}, tu mensaje ha sido retirado por infringir las directivas comunitarias.\n"
                    f"🇺🇸 <i>Notice: {user_mention}, your message was removed for violating community guidelines. Strike logged.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(warn_msg, 20))
            except Exception:
                pass
        return

    # 4. Verificación Anti-Flood con Detección Dinámica Bilingüe
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
            
            try:
                await set_participant_mic(chat_id=group_id, user_id=user_id, muted=True, volume=0)
            except Exception:
                pass

            if af_action == "warn": 
                f_msg = await message.answer(
                    f"⚠️ <b>Alerta Anti-Flood / Anti-Flood Alert:</b>\n"
                    f"{user_mention}, por favor modera la velocidad de tus mensajes en el chat.\n"
                    f"🇺🇸 <i>Please slow down message frequency in the chat.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 12))
            elif af_action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                f_msg = await message.answer(
                    f"👢 <b>Aviso de Expulsión / Kick Notice:</b>\n"
                    f"{user_mention} ha sido expulsado temporalmente por saturación de mensajes (Flood).\n"
                    f"🇺🇸 <i>User kicked due to message flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 20))
            elif af_action == "mute":
                await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
                f_msg = await message.answer(
                    f"🔇 <b>Aviso de Silencio / Mute Notice:</b>\n"
                    f"{user_mention} ha sido silenciado por saturación masiva de mensajes (Flood).\n"
                    f"🇺🇸 <i>User muted due to message flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 20))
            elif af_action == "ban":
                await ban_user(user_id)
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                f_msg = await message.answer(
                    f"🚫 <b>Bloqueo Definitivo / Ban Notice:</b>\n"
                    f"{user_mention} ha sido baneado permanentemente por flood reiterado.\n"
                    f"🇺🇸 <i>User permanently banned due to continuous flood.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    parse_mode="HTML"
                )
                asyncio.create_task(auto_delete_msg(f_msg, 25))
        except Exception: 
            pass
async def add_speaker_to_queue(group_id: int, user_id: int, full_name: str = "Speaker", username: str = "", stars_paid: int = 0):
    """Wrapper de compatibilidad para user_private.py"""
    return await add_to_speaker_queue(group_id, user_id, full_name, username, stars_paid)