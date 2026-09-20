import asyncio
import time
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command, CommandObject
from database.database import (
    set_autolower_status, 
    get_autolower_status, 
    revoke_vip_mic, 
    register_user_group
)
from assistant import set_participant_mic

logger = logging.getLogger("admin_group_handler")
router = Router()


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la respuesta correspondiente."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


async def is_user_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el miembro cuenta con facultades de creador o administrador."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


async def is_user_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el miembro posee el rango máximo de Creador (Owner) del grupo."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status == "creator"
    except Exception:
        return False


async def auto_delete_pair(cmd_msg: Message, bot_msg: Message, delay: int = 15):
    """Auto-destrucción dual para mantener el chat limpio y sin contaminación visual."""
    await asyncio.sleep(delay)
    try:
        await cmd_msg.delete()
    except Exception:
        pass
    try:
        await bot_msg.delete()
    except Exception:
        pass


# ==========================================
# 🌐 DICCIONARIO BILINGÜE PARA ADMINISTRACIÓN DE GRUPOS
# ==========================================
TEXTS = {
    "en": {
        "reload_success": (
            "🔄 <b>Database synchronized.</b>\n"
            "This community is now indexed and ready in your Private Command Center.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "settings_title": (
            "🛡️ <b>Control Center:</b> {title}\n\n"
            "Configure alphanumeric customs, content locks, purge limits, and voice sentinels.\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "btn_open_pv": "⚙️ Open in DMs",
        "autolower_panel": (
            "🎛️ <b>Radar Console: AutoLower Acoustic Shield</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change microphone attenuation in real-time:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "autolower_on_btn": "✅ Enable AutoLower (2%)",
        "autolower_off_btn": "❌ Disable AutoLower (Free)",
        "status_active": "🟢 ACTIVE (Mics dialed down to 2% for unverified users)",
        "status_inactive": "🔴 DEACTIVATED (Open Mics at 100%)",
        "success_updated": "Console updated",
        "vip_revoked": "✅ VIP Pass revoked for user ID <code>{target_id}</code>. Mic volume reset to 2%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Target required. Reply to a user or pass their numeric ID to revoke VIP pass.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_ban": "⚠️ Target required. Reply to a user or pass their numeric ID to ban them.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_success": "🚫 User <code>{target_id}</code> permanently removed from the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_kick": "⚠️ Target required. Reply to a user or pass their numeric ID to kick them.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_success": "👢 User <code>{target_id}</code> kicked from the chat.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_mute": "⚠️ Target required. Reply to a user or pass their numeric ID to silence them.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 User <code>{target_id}</code> silenced in text and live videochat.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_unmute": "⚠️ Target required. Reply to a user or pass their numeric ID to restore privileges.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 Voice and chat permissions restored to 100% for user <code>{target_id}</code>.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>"
    },
    "es": {
        "reload_success": (
            "🔄 <b>Base de datos sincronizada.</b>\n"
            "El grupo ahora está visible e indexado en tu panel de Configuración Privada.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "settings_title": (
            "🛡️ <b>Panel de Configuración:</b> {title}\n\n"
            "Configura aduana alfanumérica, cerraduras, purgas y centinelas de voz.\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "btn_open_pv": "⚙️ Abrir en Privado",
        "autolower_panel": (
            "🎛️ <b>Panel de Control: Radar AutoLower</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para alterar la atenuación de micrófonos en vivo:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "autolower_on_btn": "✅ Activar AutoLower (2%)",
        "autolower_off_btn": "❌ Desactivar AutoLower (Libre)",
        "status_active": "🟢 ACTIVO (Reduciendo a 2% a no autorizados)",
        "status_inactive": "🔴 DESACTIVADO (Micrófonos Libres al 100%)",
        "success_updated": "Consola actualizada con éxito",
        "vip_revoked": "✅ Pase VIP revocado con éxito para el usuario ID <code>{target_id}</code>. Micrófono restablecido al 2%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Responde a un usuario o proporciona su ID numérica para revocar el pase VIP.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_ban": "⚠️ Responde a un usuario o proporciona su ID numérica para banearlo.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_success": "🚫 Usuario <code>{target_id}</code> erradicado permanentemente de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_error": "❌ No se pudo ejecutar el baneo: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_kick": "⚠️ Responde a un usuario o proporciona su ID numérica para expulsarlo.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_success": "👢 Usuario <code>{target_id}</code> expulsado temporalmente.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_error": "❌ No se pudo expulsar al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_mute": "⚠️ Responde a un usuario o proporciona su ID numérica para silenciarlo.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 Usuario <code>{target_id}</code> silenciado en chat y llamada de voz.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_error": "❌ No se pudo silenciar al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_unmute": "⚠️ Responde a un usuario o proporciona su ID numérica para desmutearlo.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 Permisos de voz y chat restaurados al 100% para el usuario <code>{target_id}</code>.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_error": "❌ No se pudo desmutear al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>"
    }
}


# ==========================================
# 🔄 COMANDO DE RECARGA E INDEXACIÓN (/reload)
# ==========================================
@router.message(Command("reload"))
async def cmd_reload_group(message: Message, bot: Bot):
    """Indexa la comunidad en la base de datos para que el administrador la vea en privado."""
    if message.chat.type == "private": 
        return
    
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    await register_user_group(message.from_user.id, message.chat.id, message.chat.title)
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    msg = await message.reply(t["reload_success"], parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 10))


# ==========================================
# ⚙️ COMANDO DE ENLACE A CONFIGURACIÓN (/settings)
# ==========================================
@router.message(Command("settings"))
async def cmd_settings_group(message: Message, bot: Bot):
    """Entrega un acceso directo por DM al panel de configuración perimetral de la comunidad."""
    if message.chat.type == "private": 
        return
    
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    await register_user_group(message.from_user.id, message.chat.id, message.chat.title)
    bot_info = await bot.get_me()
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_pv"], url=f"https://t.me/{bot_info.username}?start=gset_{message.chat.id}")]
    ])
    
    msg = await message.answer(t["settings_title"].format(title=message.chat.title), reply_markup=kb, parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================
# 🎛️ CONTROL RÁPIDO DE AUTOLOWER EN GRUPO
# ==========================================
@router.message(Command("autolower"))
async def cmd_autolower_config(message: Message, command: CommandObject, bot: Bot):
    """Muestra el panel interactivo de atenuación acústica al creador del grupo."""
    if message.chat.type != "private":
        if not await is_user_creator(bot, message.chat.id, message.from_user.id):
            try: 
                await message.delete()
            except Exception: 
                pass
            return

    chat_id = message.chat.id
    current_status = await get_autolower_status(chat_id)
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["autolower_on_btn"], callback_data=f"autolower_on_{lang}"),
            InlineKeyboardButton(text=t["autolower_off_btn"], callback_data=f"autolower_off_{lang}")
        ]
    ])
    status_text = t["status_active"] if current_status == 1 else t["status_inactive"]
    msg = await message.answer(t["autolower_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    if message.chat.type != "private":
        asyncio.create_task(auto_delete_pair(message, msg, 25))


@router.callback_query(F.data.startswith("autolower_"))
async def process_autolower_callback(callback: CallbackQuery):
    """Procesa los interruptores inline para activar o suspender el AutoLower en caliente."""
    await callback.answer()
    data_parts = callback.data.split("_")
    action = data_parts[1]
    lang = data_parts[2] if len(data_parts) > 2 else get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    group_id = callback.message.chat.id
    new_status = 1 if action == "on" else 0
    await set_autolower_status(group_id, new_status)
    
    status_text = t["status_active"] if new_status == 1 else t["status_inactive"]
    try:
        await callback.message.edit_text(t["autolower_panel"].format(status=status_text), reply_markup=callback.message.reply_markup, parse_mode="HTML")
    except Exception:
        pass
    # ==========================================
# 🛑 REVOCACIÓN DE PASE VIP DE MICRÓFONO
# ==========================================
@router.message(Command("delvip"))
async def cmd_remove_vip(message: Message, command: CommandObject, bot: Bot):
    """Revoca inmediatamente la inmunidad acústica de un miembro y baja su volumen al 2%."""
    if message.chat.type == "private": 
        return
        
    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    target_id = None
    if command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        msg = await message.reply(t["target_needed_vip"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return
        
    # Revocar de la tabla de pases VIP activos
    await revoke_vip_mic(target_id, message.chat.id)
    
    # 🎙️ Restablecer atenuación forzada al 2% en sala de voz mediante el Centinela
    try:
        await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=200)
    except Exception as e:
        logger.warning(f"Aviso Centinela al revocar VIP en grupo {message.chat.id}: {e}")

    msg = await message.answer(t["vip_revoked"].format(target_id=target_id), parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================
# 🚫 BANEO DIRECTO EN GRUPO CON CENTINELA
# ==========================================
@router.message(Command("ban"))
async def cmd_ban_user(message: Message, command: CommandObject, bot: Bot):
    """Expulsa y bloquea permanentemente a un usuario, silenciándolo en la videollamada."""
    if message.chat.type == "private": 
        return
        
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    else:
        msg = await message.reply(t["target_needed_ban"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
        # 🎙️ Silenciar y expulsar inmediatamente de la llamada activa
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["ban_success"].format(target_id=target_id), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["ban_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================
# 👢 EXPULSIÓN TEMPORAL EN GRUPO (KICK)
# ==========================================
@router.message(Command("kick"))
async def cmd_kick_user(message: Message, command: CommandObject, bot: Bot):
    """Expulsa a un miembro sin bloquearlo permanentemente del grupo."""
    if message.chat.type == "private": 
        return
        
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    else:
        msg = await message.reply(t["target_needed_kick"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        # Baneo de 35 segundos y desbaneo inmediato para expulsar sin ban permanente
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=int(time.time() + 35))
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        # 🎙️ Desconectar de la videollamada
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["kick_success"].format(target_id=target_id), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["kick_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================
# 🔇 SILENCIO DE MIEMBRO EN GRUPO (MUTE)
# ==========================================
@router.message(Command("mute"))
async def cmd_mute_user(message: Message, command: CommandObject, bot: Bot):
    """Restringe el envío de mensajes en el chat y silencia el micrófono en vivo."""
    if message.chat.type == "private": 
        return
        
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    else:
        msg = await message.reply(t["target_needed_mute"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        # 🎙️ Silenciar simultáneamente en la videollamada activa
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["mute_success"].format(target_id=target_id), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["mute_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================
# 🔊 RESTAURACIÓN DE FACULTADES EN GRUPO (UNMUTE DUAL)
# ==========================================
@router.message(Command("unmute"))
async def cmd_unmute_user(message: Message, command: CommandObject, bot: Bot):
    """Restaura completamente los privilegios de chat y el micrófono al 100% en la llamada."""
    if message.chat.type == "private": 
        return
        
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    else:
        # Corregido: apunte exacto a target_needed_unmute
        msg = await message.reply(t["target_needed_unmute"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        # 1. Restaurar permisos de texto y multimedia en el chat
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
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
        # 2. 🎙️ Restaurar voz al 100% en la llamada mediante el Centinela
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=False, volume=10000)
        except Exception as mic_err:
            logger.warning(f"Aviso Centinela al desmutear en videochat: {mic_err}")

        msg = await message.reply(t["unmute_success"].format(target_id=target_id), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["unmute_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 12))