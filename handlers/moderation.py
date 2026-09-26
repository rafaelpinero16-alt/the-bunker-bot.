import os
import asyncio
import time
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    check_command_limit, get_group_tier, 
    add_to_whitelist, remove_from_whitelist,
    ban_user, add_to_blacklist, get_blacklist,
    is_whitelisted, add_user_strike, get_user_strikes,
    reset_user_strikes, get_warns_config
)
from assistant import set_participant_mic

router = Router()

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


async def is_operator_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el ejecutor cuenta con facultades de creador, administrador o Arquitecto."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


async def is_target_protected(bot: Bot, chat_id: int, target_id: int) -> bool:
    """Verifica si el objetivo posee inmunidad absoluta contra sanciones."""
    if is_super_admin(target_id):
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
        "kick_success": "👢 <b>{name}</b> has been kicked from the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>{name}</b> has been silenced for <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_perm": "🔇 <b>{name}</b> has been permanently silenced.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 <b>{name}</b>'s voice and chat privileges have been restored.\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Warning Issued</b>\n\nTarget: <b>{name}</b>\nStrikes: <b>{current}/{limit}</b>\nReason: <i>{reason}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_max_hit": "🚨 <b>Strike Limit Reached</b>\n\n<b>{name}</b> hit <b>{limit}/{limit}</b> warnings. Executing automated action: <code>{action}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_cleared": "🟢 Warnings reset for <b>{name}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "⚪ <b>{name}</b> registered in Whitelist. Tactical immunity active 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "⚪ <b>{name}</b> removed from Whitelist. Tactical immunity revoked 🔴\n\n🛡️ <i>Cloud Media Management</i>",
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
        "kick_success": "👢 <b>{name}</b> ha sido expulsado temporalmente del grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_success": "🔇 <b>{name}</b> ha sido silenciado por <b>{duration}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "mute_perm": "🔇 <b>{name}</b> ha sido silenciado permanentemente.\n\n🛡️ <i>Cloud Media Management</i>",
        "unmute_success": "🔊 Privilegios de voz y chat de <b>{name}</b> restaurados con éxito.\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Advertencia Aplicada</b>\n\nObjetivo: <b>{name}</b>\nStrikes: <b>{current}/{limit}</b>\nMotivo: <i>{reason}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_max_hit": "🚨 <b>Límite de Advertencias Superado</b>\n\n<b>{name}</b> alcanzó <b>{limit}/{limit}</b> strikes. Ejecutando acción automática: <code>{action}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_cleared": "🟢 Historial de advertencias restablecido para <b>{name}</b>.\n\n🛡️ <i>Cloud Media Management</i>",
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
        return message.reply_to_message.from_user
    if command.args:
        arg = command.args.split()[0].strip()
        if arg.isdigit():
            try:
                chat_member = await message.bot.get_chat_member(chat_id=message.chat.id, user_id=int(arg))
                return chat_member.user
            except Exception:
                return None
        elif arg.startswith("@"):
            try:
                user_chat = await message.bot.get_chat(arg)
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
            [InlineKeyboardButton(text=t["btn_upgrade"], url=f"https://t.me/{bot_info.username}?start=sub_pro")]
        ])
        return t["limit_reached"], markup
    return None, None


async def send_remote_menu(message: Message, lang: str, text: str, kb: InlineKeyboardMarkup | None):
    """Despacha la consola remota de sanción al chat privado del administrador ejecutor."""
    try:
        await message.bot.send_message(chat_id=message.from_user.id, text=text, reply_markup=kb, parse_mode="HTML")
        tier = await get_group_tier(message.chat.id)
        if tier in ["pro", "ultra_pro"] or is_super_admin(message.from_user.id):
            try: 
                await message.delete()
            except Exception: 
                pass
    except Exception:
        bot_info = await message.bot.get_me()
        warn = await message.reply(TEXTS[lang]["start_pm_warning"].format(bot_user=bot_info.username), parse_mode="HTML")
        await asyncio.sleep(10)
        try:
            await warn.delete()
            await message.delete()
        except Exception:
            pass


async def validate_moderation_target(message: Message, command: CommandObject, bot: Bot, cmd_name: str):
    """Función unificada para validar permisos, extraer el objetivo y aplicar filtros de protección."""
    if message.chat.type == "private":
        return None, None, None

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass
        return None, None, None

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd=cmd_name), parse_mode="HTML")
        await asyncio.sleep(10)
        try:
            await msg.delete()
            await message.delete()
        except Exception:
            pass
        return None, None, None

    bot_info = await bot.get_me()
    if target.id == bot_info.id:
        msg = await message.reply(t["bot_sanction"], parse_mode="HTML")
        await asyncio.sleep(8)
        try:
            await msg.delete()
            await message.delete()
        except Exception:
            pass
        return None, None, None

    if target.id == message.from_user.id:
        msg = await message.reply(t["self_sanction"], parse_mode="HTML")
        await asyncio.sleep(8)
        try:
            await msg.delete()
            await message.delete()
        except Exception:
            pass
        return None, None, None

    if await is_target_protected(bot, message.chat.id, target.id):
        msg = await message.reply(t["target_protected"], parse_mode="HTML")
        await asyncio.sleep(8)
        try:
            await msg.delete()
            await message.delete()
        except Exception:
            pass
        return None, None, None

    limit_text, markup = await enforce_limits(message.chat.id, message.from_user.id, cmd_name, lang, bot)
    if limit_text:
        await bot.send_message(chat_id=message.from_user.id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        return None, None, None

    return lang, t, target


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
    """Apertura del selector remoto de duración de baneo."""
    lang, t, target = await validate_moderation_target(message, command, bot, "ban")
    if not target:
        return

    target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
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
    text = t["ban_title"].format(group_name=chat_title, name=target_name)
    await send_remote_menu(message, lang, text, kb)


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject, bot: Bot):
    """Apertura de la confirmación remota de expulsión inmediata."""
    lang, t, target = await validate_moderation_target(message, command, bot, "kick")
    if not target:
        return

    target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_confirm_kick"], callback_data=f"exec_kick_{message.chat.id}_{target.id}_0")]
    ])
    
    chat_title = message.chat.title or f"Chat {message.chat.id}"
    text = t["kick_title"].format(group_name=chat_title, name=target_name)
    await send_remote_menu(message, lang, text, kb)


@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject, bot: Bot):
    """Apertura del selector remoto de duración de restricción de chat y voz."""
    lang, t, target = await validate_moderation_target(message, command, bot, "mute")
    if not target:
        return

    target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
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
    text = t["mute_title"].format(group_name=chat_title, name=target_name)
    await send_remote_menu(message, lang, text, kb)


# ==========================================
# ⚠️ SISTEMA TÁCTICO DE ADVERTENCIAS (WARNS)
# ==========================================
@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject, bot: Bot):
    """Aplica una advertencia (strike) al usuario con penalización automática al alcanzar el límite."""
    lang, t, target = await validate_moderation_target(message, command, bot, "warn")
    if not target:
        return

    reason = "Infracción de reglas"
    if command.args:
        args_parts = command.args.split(maxsplit=1)
        if len(args_parts) > 1:
            reason = args_parts[1].strip()

    cfg = await get_warns_config(message.chat.id)
    limit = cfg.get("limit", 3)
    action = cfg.get("action", "mute")

    current_strikes = await add_user_strike(message.chat.id, target.id, reason=reason)
    target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))

    if current_strikes >= limit:
        await reset_user_strikes(message.chat.id, target.id)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=True, volume=0)
        except Exception:
            pass

        if action == "ban":
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
            await ban_user(target.id)
        elif action == "kick":
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id, until_date=int(time.time() + 35))
            await bot.unban_chat_member(chat_id=message.chat.id, user_id=target.id)
        else:
            perms = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_other_messages=False
            )
            await bot.restrict_chat_member(chat_id=message.chat.id, user_id=target.id, permissions=perms, until_date=int(time.time() + 86400))

        text = t["warn_max_hit"].format(name=target_name, limit=limit, action=action.upper())
        await message.reply(text, parse_mode="HTML")
    else:
        text = t["warn_issued"].format(name=target_name, current=current_strikes, limit=limit, reason=reason)
        await message.reply(text, parse_mode="HTML")


@router.message(Command("unwarn", "resetwarns"))
async def cmd_unwarn(message: Message, command: CommandObject, bot: Bot):
    """Restablece a cero las advertencias acumuladas por un usuario."""
    if message.chat.type == "private":
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass
        return

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd="unwarn"), parse_mode="HTML")
        await asyncio.sleep(8)
        try:
            await msg.delete()
            await message.delete()
        except Exception:
            pass
        return

    await reset_user_strikes(message.chat.id, target.id)
    target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
    text = t["warn_cleared"].format(name=target_name)
    await message.reply(text, parse_mode="HTML")


# ==========================================
# ⚡ EJECUCIÓN DIRECTA DESDE BOTONES REMOTOS
# ==========================================
@router.callback_query(F.data.startswith("exec_"))
async def cb_mod_execution(callback: CallbackQuery):
    """Ejecuta la sanción seleccionada sincronizándola en tiempo real con el Centinela de voz."""
    await callback.answer()
    parts = callback.data.split("_")
    if len(parts) < 5:
        return

    action = parts[1]
    group_id = int(parts[2])
    target_id = int(parts[3])
    minutes = int(parts[4])

    lang = get_lang(callback.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(callback.bot, group_id, callback.from_user.id):
        await callback.message.edit_text(t["admin_only"], parse_mode="HTML")
        return

    if await is_target_protected(callback.bot, group_id, target_id):
        await callback.message.edit_text(t["target_protected"], parse_mode="HTML")
        return

    try:
        target_member = await callback.bot.get_chat_member(chat_id=group_id, user_id=target_id)
        target_name = target_member.user.full_name
    except Exception:
        target_name = f"ID: {target_id}"

    try:
        # Sincronización en vivo con el Centinela acústico
        try:
            await set_participant_mic(chat_id=group_id, user_id=target_id, muted=True, volume=0)
        except Exception:
            pass

        if action == "ban":
            if minutes == 0:
                await callback.bot.ban_chat_member(chat_id=group_id, user_id=target_id)
                await ban_user(target_id)
                await callback.message.edit_text(t["ban_success"].format(name=target_name), parse_mode="HTML")
            else:
                until_timestamp = int(time.time() + (minutes * 60))
                await callback.bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until_timestamp)
                dur = format_duration(minutes, lang)
                await callback.message.edit_text(t["ban_timed_success"].format(name=target_name, duration=dur), parse_mode="HTML")
        
        elif action == "kick":
            await callback.bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
            await callback.bot.unban_chat_member(chat_id=group_id, user_id=target_id)
            await callback.message.edit_text(t["kick_success"].format(name=target_name), parse_mode="HTML")
        
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
            if minutes == 0:
                await callback.bot.restrict_chat_member(chat_id=group_id, user_id=target_id, permissions=perms)
                await callback.message.edit_text(t["mute_perm"].format(name=target_name), parse_mode="HTML")
            else:
                until_timestamp = int(time.time() + (minutes * 60))
                await callback.bot.restrict_chat_member(chat_id=group_id, user_id=target_id, permissions=perms, until_date=until_timestamp)
                dur = format_duration(minutes, lang)
                await callback.message.edit_text(t["mute_success"].format(name=target_name, duration=dur), parse_mode="HTML")

    except Exception as e:
        try:
            await callback.message.edit_text(t["error"].format(error=e), parse_mode="HTML")
        except TelegramBadRequest: 
            pass


# ==========================================
# 🔊 RESTAURACIÓN DE FACULTADES (UNMUTE DUAL)
# ==========================================
@router.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject, bot: Bot):
    """Restaura todos los permisos de envío en el chat y el micrófono en el videochat."""
    if message.chat.type == "private": 
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    target = await extract_target_user(message, command)
    if not target:
        msg = await message.reply(t["target_needed"].format(cmd="unmute"), parse_mode="HTML")
        await asyncio.sleep(10)
        try:
            await msg.delete()
            await message.delete()
        except Exception: 
            pass
        return

    limit_text, markup = await enforce_limits(message.chat.id, message.from_user.id, "unmute", lang, bot)
    if limit_text:
        await bot.send_message(chat_id=message.from_user.id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        return

    permissions = ChatPermissions(
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
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=target.id, permissions=permissions)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=False, volume=10000)
        except Exception:
            pass

        target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        text = t["unmute_success"].format(name=target_name)
        await send_remote_menu(message, lang, text, None)
    except Exception as e:
        msg = await message.reply(t["error"].format(error=e), parse_mode="HTML")
        await asyncio.sleep(10)
        try:
            await msg.delete()
            await message.delete()
        except Exception: 
            pass


# ==========================================
# ⚪ GESTIÓN DE LISTA BLANCA (WHITELIST)
# ==========================================
@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject, bot: Bot):
    """Concede inmunidad absoluta ante filtros antispam, cerraduras y captcha."""
    if message.chat.type == "private": 
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    target = await extract_target_user(message, command)
    
    limit_text, markup = await enforce_limits(message.chat.id, message.from_user.id, "whitelist", lang, bot)
    if limit_text:
        await bot.send_message(chat_id=message.from_user.id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        return

    if target:
        await add_to_whitelist(target.id)
        target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        text = t["wl_added"].format(name=target_name)
        await send_remote_menu(message, lang, text, None)
    elif command.args and command.args.strip().isdigit():
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
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    target = await extract_target_user(message, command)
    
    if target:
        await remove_from_whitelist(target.id)
        target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
        text = t["wl_removed"].format(name=target_name)
        await send_remote_menu(message, lang, text, None)
    elif command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
        await remove_from_whitelist(target_id)
        text = t["wl_removed"].format(name=f"ID: {target_id}")
        await send_remote_menu(message, lang, text, None)
    else:
        msg = await message.reply(t["target_needed"].format(cmd="unwhitelist"), parse_mode="HTML")
        await asyncio.sleep(10)
        try:
            await msg.delete()
            await message.delete()
        except Exception: 
            pass


# ==========================================
# ⚫ GESTIÓN DE LISTA NEGRA (BLACKLIST)
# ==========================================
@router.message(Command("blacklist"))
async def cmd_blacklist(message: Message, command: CommandObject, bot: Bot):
    """Registra términos prohibidos con purga automática y advertencias tácticas."""
    if message.chat.type == "private": 
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    limit_text, markup = await enforce_limits(message.chat.id, message.from_user.id, "blacklist", lang, bot)
    if limit_text:
        await bot.send_message(chat_id=message.from_user.id, text=limit_text, reply_markup=markup, parse_mode="HTML")
        return

    if command.args:
        word = command.args.strip().lower()
        await add_to_blacklist(word)
        text = t["bl_added"].format(word=word)
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
                await asyncio.sleep(10)
                try:
                    await msg.delete()
                    await message.delete()
                except Exception: 
                    pass
                return
            await add_to_blacklist(word)
            text = t["bl_added"].format(word=word)
            await send_remote_menu(message, lang, text, None)
            return
        else:
            target = message.reply_to_message.from_user
            if target:
                if await is_target_protected(bot, message.chat.id, target.id):
                    msg = await message.reply(t["target_protected"], parse_mode="HTML")
                    await asyncio.sleep(8)
                    try:
                        await msg.delete()
                        await message.delete()
                    except Exception:
                        pass
                    return

                await ban_user(target.id)
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
                try:
                    await set_participant_mic(chat_id=message.chat.id, user_id=target.id, muted=True, volume=0)
                except Exception:
                    pass
                target_name = getattr(target, "full_name", getattr(target, "first_name", f"ID {target.id}"))
                text = t["ban_success"].format(name=target_name)
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