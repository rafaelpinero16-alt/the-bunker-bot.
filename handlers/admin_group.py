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
    register_user_group,
    get_podcast_status, set_podcast_status,
    get_screen_shield_status, set_screen_shield_status,
    add_user_strike, get_user_strikes, reset_user_strikes, get_warns_config,
    get_night_mode_config, activate_universal_night_mode, deactivate_universal_night_mode
)
from assistant import (
    set_participant_mic,
    engage_podcast_ducking, disengage_podcast_ducking,
    engage_screen_shield, disengage_screen_shield
)

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


# ==========================================================
# 🌐 DICCIONARIO BILINGÜE (MENSAJES DE SERVICIO Y ALERTAS)
# ==========================================================
TEXTS = {
    "en": {
        "owner_only": "⛔ <b>Access Denied:</b> This command is restricted exclusively to the community Owner.\n\n🛡️ <i>Cloud Media Management</i>",
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
        "podcast_panel": (
            "🎙️ <b>Radar Console: Podcast Mode (Ducking)</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change dynamic audio ducking:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_on_btn": "✅ Enable Podcast",
        "podcast_off_btn": "❌ Disable Podcast",
        "status_active_pod": "🟢 ACTIVE (Dynamic Ducking enabled)",
        "status_inactive_pod": "🔴 DEACTIVATED",
        "shield_panel": (
            "🎥 <b>Radar Console: Screen Shield (Antinota)</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change screen-sharing restrictions:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_on_btn": "✅ Enable Shield",
        "shield_off_btn": "❌ Disable Shield",
        "status_active_shield": "🟢 ACTIVE (Screen sharing blocked)",
        "status_inactive_shield": "🔴 DEACTIVATED",
        "night_on_msg": "🌙 <b>Universal Night Mode:</b> 🟢 ACTIVATED. Perimeter restrictions applied.",
        "night_off_msg": "☀️ <b>Universal Night Mode:</b> 🔴 DEACTIVATED. Standard permissions restored.",
        "vip_revoked": "✅ VIP Pass revoked for {target_tag}. Mic volume reset to 2%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Target required. Reply to a user, mention them or provide their ID.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_ban": "⚠️ Target required. Reply to a message or use: <code>/ban [@user or ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_success": "🚫 <b>Sanction Executed:</b> {target_tag} has been permanently banned from the community for violating perimeter rules.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_kick": "⚠️ Target required. Reply to a message or use: <code>/kick [@user or ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_success": "👢 <b>Perimeter Warning:</b> {target_tag} has been kicked from the community due to rule infractions.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_mute": "⚠️ Target required. Reply to a message or use: <code>/mute [@user or ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>Notice:</b> {target_tag} has been muted in chat and live videochat for non-compliant conduct.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_unmute": "⚠️ Target required. Reply to a message or use: <code>/unmute [@user or ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 <b>Restoration:</b> Chat and live voice privileges restored for {target_tag}.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_error": "❌ Execution failed: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_warn": "⚠️ Target required. Reply to a message or use: <code>/warn [@user or ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Warning Issued:</b> {target_tag} has received a formal strike ({current}/{limit}).\n• <b>Reason:</b> {reason}\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_punished": "⚖️ <b>Threshold Reached:</b> {target_tag} reached {limit}/{limit} strikes.\n• <b>Automated Action:</b> {action_name} executed.\n\n🛡️ <i>Cloud Media Management</i>",
        "warns_reset_done": "✅ All strikes have been cleared for {target_tag}.\n\n🛡️ <i>Cloud Media Management</i>"
    },
    "es": {
        "owner_only": "⛔ <b>Acceso denegado:</b> Este protocolo está reservado única y exclusivamente para el Dueño de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
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
        "podcast_panel": (
            "🎙️ <b>Panel de Control: Modo Podcast (Ducking)</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para alterar la atenuación dinámica:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_on_btn": "✅ Activar Podcast",
        "podcast_off_btn": "❌ Desactivar Podcast",
        "status_active_pod": "🟢 ACTIVO (Atenuación dinámica en vivo)",
        "status_inactive_pod": "🔴 DESACTIVADO",
        "shield_panel": (
            "🎥 <b>Panel de Control: Escudo Antinota</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para el corte de transmisiones de pantalla:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_on_btn": "✅ Activar Escudo",
        "shield_off_btn": "❌ Desactivar Escudo",
        "status_active_shield": "🟢 ACTIVO (Corte de pantalla a no autorizados)",
        "status_inactive_shield": "🔴 DESACTIVADO",
        "night_on_msg": "🌙 <b>Modo Nocturno Universal:</b> 🟢 ACTIVADO. Restricciones perimetrales aplicadas.",
        "night_off_msg": "☀️ <b>Modo Nocturno Universal:</b> 🔴 DESACTIVADO. Permisos previos restaurados.",
        "vip_revoked": "✅ Pase VIP revocado para {target_tag}. Micrófono restablecido al 2%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Objetivo requerido. Responde a un usuario, menciónalo con @ o pasa su ID.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_ban": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/ban [@usuario o ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_success": "🚫 <b>Sanción Ejecutada:</b> {target_tag} ha sido expulsado y bloqueado permanentemente del grupo por infringir las reglas de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "ban_error": "❌ No se pudo ejecutar el baneo: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_kick": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/kick [@usuario o ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_success": "👢 <b>Aviso de Seguridad:</b> {target_tag} ha sido expulsado del grupo por infringir las normas de convivencia.\n\n🛡️ <i>Cloud Media Management</i>",
        "kick_error": "❌ No se pudo expulsar al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_mute": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/mute [@usuario o ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>Aviso de Moderación:</b> {target_tag} ha sido silenciado en el chat y sala de voz por conducta no permitida.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_error": "❌ No se pudo silenciar al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_unmute": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/unmute [@usuario o ID]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 <b>Restauración:</b> Privilegios de voz y chat restaurados al 100% para {target_tag}.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_error": "❌ No se pudo desmutear al usuario: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_warn": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/warn [@usuario o ID] [motivo]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Advertencia Registrada:</b> {target_tag} ha acumulado una falta formal ({current}/{limit}).\n• <b>Motivo:</b> {reason}\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_punished": "⚖️ <b>Límite de Faltas Alcanzado:</b> {target_tag} sumó {limit}/{limit} faltas.\n• <b>Castigo Automático:</b> Se aplicó {action_name} de inmediato.\n\n🛡️ <i>Cloud Media Management</i>",
        "warns_reset_done": "✅ Todas las advertencias han sido restablecidas a cero para {target_tag}.\n\n🛡️ <i>Cloud Media Management</i>"
    }
}


# ==========================================================
# 🎯 RESOLUCIÓN TÁCTICA DE USUARIO Y ETIQUETA (@ O ENLACE)
# ==========================================================
async def resolve_target(message: Message, command: CommandObject, bot: Bot):
    """
    Identifica al objetivo por respuesta directa, mención @ o ID numérica,
    generando la etiqueta de mención directa para el aviso público.
    """
    user_obj = None
    target_id = None

    if message.reply_to_message and message.reply_to_message.from_user:
        user_obj = message.reply_to_message.from_user
        target_id = user_obj.id
    elif command and command.args:
        arg = command.args.split()[0].strip()
        if arg.isdigit():
            target_id = int(arg)
            try:
                member = await bot.get_chat_member(chat_id=message.chat.id, user_id=target_id)
                user_obj = member.user
            except Exception:
                user_obj = None
        elif arg.startswith("@"):
            try:
                chat_info = await bot.get_chat(arg)
                target_id = chat_info.id
                user_obj = chat_info
            except Exception:
                return None, arg

    if user_obj:
        if getattr(user_obj, "username", None):
            tag = f"@{user_obj.username}"
        else:
            name = getattr(user_obj, "full_name", getattr(user_obj, "first_name", "Usuario"))
            tag = f'<a href="tg://user?id={user_obj.id}">{name}</a>'
        return target_id, tag
    elif target_id:
        return target_id, f'<a href="tg://user?id={target_id}">ID: {target_id}</a>'

    return None, None


# ==========================================================
# 🔄 COMANDO DE RECARGA E INDEXACIÓN (/reload)
# ==========================================================
@router.message(Command("reload"))
async def cmd_reload_group(message: Message, bot: Bot):
    """Indexa la comunidad en la base de datos para que el dueño la vea en privado."""
    if message.chat.type == "private": 
        return
    
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    await register_user_group(
        user_id=message.from_user.id, 
        group_id=message.chat.id, 
        group_name=message.chat.title or "Comunidad",
        chat_type=message.chat.type
    )
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    msg = await message.reply(t["reload_success"], parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 10))


# ==========================================================
# ⚙️ COMANDO DE ENLACE A CONFIGURACIÓN (/settings)
# ==========================================================
@router.message(Command("settings"))
async def cmd_settings_group(message: Message, bot: Bot):
    """Entrega un acceso directo por DM al panel de configuración del búnker."""
    if message.chat.type == "private": 
        return
    
    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    await register_user_group(
        user_id=message.from_user.id, 
        group_id=message.chat.id, 
        group_name=message.chat.title or "Comunidad",
        chat_type=message.chat.type
    )
    bot_info = await bot.get_me()
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_pv"], url=f"https://t.me/{bot_info.username}?start=gset_{message.chat.id}")]
    ])
    
    msg = await message.answer(t["settings_title"].format(title=message.chat.title), reply_markup=kb, parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================================
# 🎛️ CONTROLES RÁPIDOS DE AUDIO Y TRANSMISIÓN (BLINDADOS)
# ==========================================================
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
            InlineKeyboardButton(text=t["autolower_on_btn"], callback_data=f"gautolower_on_{lang}"),
            InlineKeyboardButton(text=t["autolower_off_btn"], callback_data=f"gautolower_off_{lang}")
        ]
    ])
    status_text = t["status_active"] if current_status == 1 else t["status_inactive"]
    msg = await message.answer(t["autolower_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    if message.chat.type != "private":
        asyncio.create_task(auto_delete_pair(message, msg, 25))


@router.callback_query(F.data.startswith("gautolower_"))
async def process_autolower_callback(callback: CallbackQuery, bot: Bot):
    group_id = callback.message.chat.id
    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer("⛔ Acceso denegado.", show_alert=True)
        return

    await callback.answer()
    data_parts = callback.data.split("_")
    action = data_parts[1]
    lang = data_parts[2] if len(data_parts) > 2 else get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    new_status = 1 if action == "on" else 0
    await set_autolower_status(group_id, new_status)
    
    status_text = t["status_active"] if new_status == 1 else t["status_inactive"]
    try:
        await callback.message.edit_text(t["autolower_panel"].format(status=status_text), reply_markup=callback.message.reply_markup, parse_mode="HTML")
    except Exception:
        pass


@router.message(Command("podcast"))
async def cmd_podcast_config(message: Message, bot: Bot):
    """Muestra el panel interactivo del Modo Podcast (Ducking) al creador del grupo."""
    if message.chat.type != "private":
        if not await is_user_creator(bot, message.chat.id, message.from_user.id):
            try: 
                await message.delete()
            except Exception: 
                pass
            return

    chat_id = message.chat.id
    current_status = await get_podcast_status(chat_id)
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["podcast_on_btn"], callback_data=f"gpodcast_on_{lang}"),
            InlineKeyboardButton(text=t["podcast_off_btn"], callback_data=f"gpodcast_off_{lang}")
        ]
    ])
    status_text = t["status_active_pod"] if current_status == 1 else t["status_inactive_pod"]
    msg = await message.answer(t["podcast_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    if message.chat.type != "private":
        asyncio.create_task(auto_delete_pair(message, msg, 25))


@router.callback_query(F.data.startswith("gpodcast_"))
async def process_podcast_callback(callback: CallbackQuery, bot: Bot):
    group_id = callback.message.chat.id
    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer("⛔ Acceso denegado.", show_alert=True)
        return

    await callback.answer()
    data_parts = callback.data.split("_")
    action = data_parts[1]
    lang = data_parts[2] if len(data_parts) > 2 else get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    new_status = 1 if action == "on" else 0
    await set_podcast_status(group_id, new_status)
    
    if new_status == 1:
        await engage_podcast_ducking(group_id)
    else:
        await disengage_podcast_ducking(group_id)

    status_text = t["status_active_pod"] if new_status == 1 else t["status_inactive_pod"]
    try:
        await callback.message.edit_text(t["podcast_panel"].format(status=status_text), reply_markup=callback.message.reply_markup, parse_mode="HTML")
    except Exception:
        pass


@router.message(Command("shield"))
async def cmd_shield_config(message: Message, bot: Bot):
    """Muestra el panel interactivo del Escudo Antinota al creador del grupo."""
    if message.chat.type != "private":
        if not await is_user_creator(bot, message.chat.id, message.from_user.id):
            try: 
                await message.delete()
            except Exception: 
                pass
            return

    chat_id = message.chat.id
    current_status = await get_screen_shield_status(chat_id)
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["shield_on_btn"], callback_data=f"gshield_on_{lang}"),
            InlineKeyboardButton(text=t["shield_off_btn"], callback_data=f"gshield_off_{lang}")
        ]
    ])
    status_text = t["status_active_shield"] if current_status == 1 else t["status_inactive_shield"]
    msg = await message.answer(t["shield_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    if message.chat.type != "private":
        asyncio.create_task(auto_delete_pair(message, msg, 25))


@router.callback_query(F.data.startswith("gshield_"))
async def process_shield_callback(callback: CallbackQuery, bot: Bot):
    group_id = callback.message.chat.id
    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer("⛔ Acceso denegado.", show_alert=True)
        return

    await callback.answer()
    data_parts = callback.data.split("_")
    action = data_parts[1]
    lang = data_parts[2] if len(data_parts) > 2 else get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    new_status = 1 if action == "on" else 0
    await set_screen_shield_status(group_id, new_status)

    if new_status == 1:
        await engage_screen_shield(group_id)
    else:
        await disengage_screen_shield(group_id)
    
    status_text = t["status_active_shield"] if new_status == 1 else t["status_inactive_shield"]
    try:
        await callback.message.edit_text(t["shield_panel"].format(status=status_text), reply_markup=callback.message.reply_markup, parse_mode="HTML")
    except Exception:
        pass


# ==========================================================
# 🌙 BLINDAJE RÁPIDO: MODO NOCTURNO UNIVERSAL (/night)
# ==========================================================
@router.message(Command("night"))
async def cmd_toggle_night(message: Message, bot: Bot):
    """Activa o desactiva en un solo paso el Modo Nocturno Universal con snapshot."""
    if message.chat.type == "private":
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    chat_id = message.chat.id
    cfg = await get_night_mode_config(chat_id)
    if cfg["status"] == 0:
        await activate_universal_night_mode(chat_id)
        msg = await message.reply(t["night_on_msg"], parse_mode="HTML")
    else:
        await deactivate_universal_night_mode(chat_id)
        msg = await message.reply(t["night_off_msg"], parse_mode="HTML")

    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================================
# ⚠️ MATRIZ DE ADVERTENCIAS CENTRALIZADA (/warn, /resetwarns)
# ==========================================================
@router.message(Command("warn"))
async def cmd_warn_user(message: Message, command: CommandObject, bot: Bot):
    """Aplica una advertencia formal al usuario y ejecuta castigo automático si alcanza el límite."""
    if message.chat.type == "private":
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_warn"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    reason = "Violación de normas perimetrales" if lang == "es" else "Perimeter rules violation"
    if command and command.args:
        args_parts = command.args.split(maxsplit=1)
        if len(args_parts) > 1 and not args_parts[0].isdigit() and not args_parts[0].startswith("@"):
            reason = command.args.strip()
        elif len(args_parts) > 1:
            reason = args_parts[1].strip()

    cfg = await get_warns_config(message.chat.id)
    limit = cfg["limit"]
    action = cfg["action"]
    current_strikes = await add_user_strike(message.chat.id, target_id, reason)

    if current_strikes >= limit:
        await reset_user_strikes(message.chat.id, target_id)
        try:
            if action == "ban":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
            elif action == "kick":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
            elif action == "mute":
                await bot.restrict_chat_member(
                    chat_id=message.chat.id, 
                    user_id=target_id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            try:
                await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
            except Exception:
                pass
            msg = await message.reply(t["warn_punished"].format(target_tag=target_tag, limit=limit, action_name=action.upper()), parse_mode="HTML")
        except Exception as ex:
            msg = await message.reply(f"❌ Error: {ex}", parse_mode="HTML")
    else:
        msg = await message.reply(t["warn_issued"].format(target_tag=target_tag, current=current_strikes, limit=limit, reason=reason), parse_mode="HTML")

    asyncio.create_task(auto_delete_pair(message, msg, 15))


@router.message(Command("resetwarns"))
async def cmd_reset_warns(message: Message, command: CommandObject, bot: Bot):
    """Limpia a cero el historial de faltas de un miembro."""
    if message.chat.type == "private":
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_admin(bot, message.chat.id, message.from_user.id):
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_warn"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    await reset_user_strikes(message.chat.id, target_id)
    msg = await message.reply(t["warns_reset_done"].format(target_tag=target_tag), parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================================
# 🛑 REVOCACIÓN DE PASE VIP (EXCLUSIVO DUEÑO)
# ==========================================================
@router.message(Command("delvip"))
async def cmd_remove_vip(message: Message, command: CommandObject, bot: Bot):
    """Revoca la inmunidad acústica de un miembro y resetea su volumen al 2%."""
    if message.chat.type == "private": 
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_vip"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return
        
    await revoke_vip_mic(target_id, message.chat.id)
    
    try:
        await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=200)
    except Exception as e:
        logger.warning(f"Aviso Centinela al revocar VIP en grupo {message.chat.id}: {e}")

    msg = await message.answer(t["vip_revoked"].format(target_tag=target_tag), parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, msg, 12))


# ==========================================================
# 🚫 BANEO DIRECTO EN GRUPO (EXCLUSIVO DUEÑO)
# ==========================================================
@router.message(Command("ban"))
async def cmd_ban_user(message: Message, command: CommandObject, bot: Bot):
    """Expulsa y bloquea permanentemente a un infractor tagueándolo en el grupo."""
    if message.chat.type == "private": 
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_ban"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["ban_success"].format(target_tag=target_tag), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["ban_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================================
# 👢 EXPULSIÓN TEMPORAL EN GRUPO (KICK - EXCLUSIVO DUEÑO)
# ==========================================================
@router.message(Command("kick"))
async def cmd_kick_user(message: Message, command: CommandObject, bot: Bot):
    """Expulsa a un usuario tagueándolo y notificando la advertencia formal."""
    if message.chat.type == "private": 
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_kick"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=int(time.time() + 35))
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["kick_success"].format(target_tag=target_tag), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["kick_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================================
# 🔇 SILENCIO DE MIEMBRO EN GRUPO (MUTE - EXCLUSIVO DUEÑO)
# ==========================================================
@router.message(Command("mute"))
async def cmd_mute_user(message: Message, command: CommandObject, bot: Bot):
    """Restringe chat y videollamada al usuario tagueado con aviso de moderación."""
    if message.chat.type == "private": 
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_mute"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass
        msg = await message.reply(t["mute_success"].format(target_tag=target_tag), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["mute_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 15))


# ==========================================================
# 🔊 RESTAURACIÓN DE FACULTADES (UNMUTE DUAL - EXCLUSIVO DUEÑO)
# ==========================================================
@router.message(Command("unmute"))
async def cmd_unmute_user(message: Message, command: CommandObject, bot: Bot):
    """Restaura chat y micrófono al 100% tagueando al usuario beneficiario."""
    if message.chat.type == "private": 
        return
        
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id):
        msg = await message.reply(t["owner_only"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 8))
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id:
        msg = await message.reply(t["target_needed_unmute"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, msg, 10))
        return

    try:
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
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=False, volume=10000)
        except Exception as mic_err:
            logger.warning(f"Aviso Centinela al desmutear en videochat: {mic_err}")

        msg = await message.reply(t["unmute_success"].format(target_tag=target_tag), parse_mode="HTML")
    except Exception as e:
        msg = await message.reply(t["unmute_error"].format(error=e), parse_mode="HTML")
        
    asyncio.create_task(auto_delete_pair(message, msg, 15))