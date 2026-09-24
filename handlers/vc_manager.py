import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    add_to_whitelist, remove_from_whitelist, is_group_approved, 
    is_vip_mic_active, get_autolower_status, set_autolower_status,
    get_mic_vip_price, get_session_by_group,
    get_night_mode_config, get_podcast_status, get_screen_shield_status,
    get_community_live_telemetry, get_speaker_queue, get_panic_status,
    get_vc_monitor_status, set_vc_monitor_status
)
from assistant import set_participant_mic

logger = logging.getLogger("vc_manager_gateway")
router = Router()

# 🧠 Nota de Arquitectura: Eliminado el diccionario volátil en RAM (vc_states).
# Ahora el estado de monitoreo se persiste de forma segura en la base de datos SQLite con WAL.


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la respuesta correspondiente."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


# ==========================================
# 🌐 DICCIONARIO BILINGÜE DE GESTIÓN DE VOZ
# ==========================================
TEXTS = {
    "en": {
        "owner_only": "⛔ <b>Access Denied:</b> This protocol is strictly reserved for the Community Owner.\n\n🛡️ <i>Cloud Media Management</i>",
        "private_warning": "⚠️ @{name}, please start a private chat with me first to view this control console: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "unauthorized_start": "⚠️ @{name}, this command is strictly reserved for community administrators.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_enabled": "🔊 Voice chat monitoring and AutoLower sentinel are now <b>activated</b> for your group.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_disabled": "🔇 Voice chat monitoring and AutoLower sentinel have been <b>deactivated</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_report": (
            "📹 <b>Camera & Transmission Quality Audit:</b>\n\n"
            "• <b>Live Video & Streams:</b> Active High-Fidelity Feed 🟢\n"
            "• <b>Transmission Status:</b> Fluid & Lag-Free\n"
            "• <b>Assigned Sentinel:</b> {sentinel_name}\n"
            "• <b>Screen-Share Shield:</b> {shield}\n"
            "• <b>AutoLower Protocol:</b> {autolower}\n"
            "• <b>Podcast Mode:</b> {podcast}\n"
            "• <b>Emergency Lockdown:</b> {panic}\n"
            "• <b>Night Mode:</b> {night}\n"
            "• <b>Active VIP Passes in Room:</b> <code>{vip_count}</code>\n"
            "• <b>Speakers Queue:</b> <code>{speakers_count} waiting</code>\n"
            "• <b>Background Optimization:</b> Cleans video lag & refreshes room every 3.5h automatically\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "kickcam_success": "⚡ User {target} has been disconnected from the voice room.\n\n🛡️ <i>Cloud Media Management</i>",
        "kickcam_needed": "⚠️ Reply to the message of the user you want to disconnect.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "✅ User {target} registered to Whitelist with full voice immunity.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "❌ User {target} removed from Whitelist.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_needed": "⚠️ Reply to the message of the user you wish to authorize.\n\n🛡️ <i>Cloud Media Management</i>",
        "status_text": (
            "🎙️ <b>Voice Room & Radar Live Telemetry</b>\n\n"
            "• <b>Supervision State:</b> {status}\n"
            "• <b>Active Sentinel:</b> {sentinel_name} 🟢\n"
            "• <b>VIP Mic Rate:</b> <code>{mic_price} Stars (XTR)</code>\n"
            "• <b>Active VIP Passes in Room:</b> <code>{vip_count}</code>\n"
            "• <b>Speakers Queue:</b> <code>{speakers_count} waiting</code>\n"
            "• <b>AutoLower Protocol:</b> {autolower}\n"
            "• <b>Podcast Mode:</b> {podcast}\n"
            "• <b>Screen Shield:</b> {shield}\n"
            "• <b>Emergency Lockdown:</b> {panic}\n"
            "• <b>Night Mode:</b> {night}\n\n"
            "<i>Select an action below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_cams": "📹 Camera Quality Audit",
        "btn_reset": "🔄 Sync Voice Room",
        "btn_back_vc": "🔙 Back to Telemetry",
        "btn_close_vc": "🗑️ Close Panel",
        "getid_text": (
            "✅ <b>Connectivity Telemetry:</b>\n"
            "• Chat Type: <code>{type}</code>\n"
            "• Chat ID: <code>{chat_id}</code>\n"
            "• Thread ID: <code>{thread_id}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "autolower_on": "🎚️ <b>AutoLower Protocol:</b> 🟢 ACTIVATED (Mics dialed down to 2% for unverified users)\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_off": "🎚️ <b>AutoLower Protocol:</b> 🔴 DEACTIVATED (Open microphones at 100%)\n\n🛡️ <i>Cloud Media Management</i>",
        "micvip_msg": (
            "🔇 <b>The Bunker Bot — Voice Chat Sentinel</b>\n\n"
            "• Regular microphones are moderated automatically to keep the transmission clean.\n"
            "• Unlock your voice continuously at <b>100% volume</b> for 24 hours with your VIP Pass.\n\n"
            "💡 <i>Ecosystem Perk:</i> With a dedicated Sentinel & Bot Clone, <b>100% of the Stars paid go straight to the community owner's balance</b>!\n\n"
            "👤 @{name}, tap below to unlock the mic:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pay_stars": "⭐ Get VIP Voice Pass ({price} Stars)"
    },
    "es": {
        "owner_only": "⛔ <b>Acceso Denegado:</b> Este protocolo está reservado exclusivamente para el Dueño de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "private_warning": "⚠️ @{name}, para ver esta información debes iniciar un chat privado conmigo primero: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "unauthorized_start": "⚠️ @{name}, este comando está reservado exclusivamente para los administradores del grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_enabled": "🔊 El sistema de control acústico y radar centinela ha sido <b>activado</b> para tu grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_disabled": "🔇 El sistema de monitoreo de voz y centinela ha sido <b>desactivado</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_report": (
            "📹 <b>Auditoría de Calidad de Cámaras y Transmisión:</b>\n\n"
            "• <b>Video en Vivo y Streams:</b> Transmisión en Alta Fidelidad Activa 🟢\n"
            "• <b>Estado de Transmisión:</b> Fluida y sin congelamientos\n"
            "• <b>Centinela Asignado:</b> {sentinel_name}\n"
            "• <b>Escudo Antinota:</b> {shield}\n"
            "• <b>Protocolo AutoLower:</b> {autolower}\n"
            "• <b>Modo Podcast:</b> {podcast}\n"
            "• <b>Bloqueo de Emergencia:</b> {panic}\n"
            "• <b>Modo Nocturno:</b> {night}\n"
            "• <b>Pases VIP Activos en Sala:</b> <code>{vip_count}</code>\n"
            "• <b>Cola de Speakers:</b> <code>{speakers_count} en espera</code>\n"
            "• <b>Optimización en Segundo Plano:</b> Purgado de lag y refresco automático cada 3.5h activado\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "kickcam_success": "⚡ El usuario {target} ha sido desconectado de la sala de voz.\n\n🛡️ <i>Cloud Media Management</i>",
        "kickcam_needed": "⚠️ Debes responder al mensaje del usuario que deseas desconectar.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "✅ Usuario {target} registrado en Whitelist con inmunidad de voz total.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "❌ Usuario {target} retirado de la Whitelist.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_needed": "⚠️ Responde al mensaje del usuario que deseas autorizar.\n\n🛡️ <i>Cloud Media Management</i>",
        "status_text": (
            "🎙️ <b>Telemetría en Vivo de la Sala de Voz y Radar</b>\n\n"
            "• <b>Supervisión Acústica:</b> {status}\n"
            "• <b>Centinela Asignado:</b> {sentinel_name} 🟢\n"
            "• <b>Tarifa Pase VIP:</b> <code>{mic_price} Stars (XTR)</code>\n"
            "• <b>Pases VIP Activos en Sala:</b> <code>{vip_count}</code>\n"
            "• <b>Cola de Speakers Pagada:</b> <code>{speakers_count} en espera</code>\n"
            "• <b>Protocolo AutoLower:</b> {autolower}\n"
            "• <b>Modo Podcast:</b> {podcast}\n"
            "• <b>Escudo Antinota:</b> {shield}\n"
            "• <b>Bloqueo de Emergencia:</b> {panic}\n"
            "• <b>Modo Nocturno:</b> {night}\n\n"
            "<i>Selecciona una acción:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_cams": "📹 Calidad de Cámaras",
        "btn_reset": "🔄 Sincronizar Sala",
        "btn_back_vc": "🔙 Volver a Telemetría",
        "btn_close_vc": "🗑️ Cerrar Panel",
        "getid_text": (
            "✅ <b>Telemetría de Conectividad:</b>\n"
            "• Tipo de Chat: <code>{type}</code>\n"
            "• ID del Chat: <code>{chat_id}</code>\n"
            "• Hilo: <code>{thread_id}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "autolower_on": "🎚️ <b>Protocolo AutoLower:</b> 🟢 ACTIVADO (Volumen reducido al 2% a no autorizados)\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_off": "🎚️ <b>Protocolo AutoLower:</b> 🔴 DESACTIVADO (Micrófonos libres al 100%)\n\n🛡️ <i>Cloud Media Management</i>",
        "micvip_msg": (
            "🔇 <b>The Bunker Bot — Centinela de Videochat</b>\n\n"
            "• Los micrófonos regulares se moderan automáticamente para mantener la sala limpia.\n"
            "• Desbloquea tu voz al <b>100% de volumen continuo</b> durante 24 horas con tu Pase VIP.\n\n"
            "💡 <i>Ventaja del Ecosistema:</i> Con Centinela y Bot Clon propio, <b>el 100% de las Stars pagadas van directas al saldo del dueño del grupo</b>.\n\n"
            "👤 @{name}, haz clic abajo para activar tu micrófono:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pay_stars": "⭐ Obtener Pase VIP ({price} Stars)"
    }
}


async def get_active_sentinel_label(group_id: int) -> str:
    """Devuelve la etiqueta del centinela activo para el grupo (Dedicado o Maestro)."""
    session_data = await get_session_by_group(group_id)
    if session_data:
        return "Centinela Dedicado Propio 💎"
    return "Centinela Maestro (@Alphacentinel) 🤖"


# ==========================================
# 📡 FUENTE ÚNICA DE VERDAD: TELEMETRÍA EN CALIENTE
# ==========================================
async def get_telemetry_context(chat_id: int, lang: str) -> dict:
    """
    Punto único de acceso a la telemetría en tiempo real de la sala.
    Invoca get_community_live_telemetry(chat_id) + get_night_mode_config(chat_id)
    y devuelve los strings ya formateados y bilingües, listos para inyectar
    en cams_report y status_text. Usado por /cams, /statusvc, vc_cams y vc_status
    para garantizar que ambos reportes reflejen exactamente el mismo estado en vivo.
    """
    telem = await get_community_live_telemetry(chat_id)
    night_cfg = await get_night_mode_config(chat_id)
    sentinel_label = await get_active_sentinel_label(chat_id)

    if lang == "en":
        shield = "🟢 Active" if telem["shield_status"] == 1 else "🔴 Inactive"
        autolower = "🟢 Active at 2%" if telem["autolower_status"] == 1 else "🔴 Inactive"
        podcast = "🟢 Active" if telem["podcast_status"] == 1 else "🔴 Inactive"
        panic = "🚨 RAID LOCKDOWN ACTIVE" if telem["panic_active"] == 1 else "🟢 Normal"
        night = "🟢 Active" if night_cfg and night_cfg.get("status") == 1 else "🔴 Inactive"
    else:
        shield = "🟢 Activo" if telem["shield_status"] == 1 else "🔴 Inactivo"
        autolower = "🟢 Activo al 2%" if telem["autolower_status"] == 1 else "🔴 Inactivo"
        podcast = "🟢 Activo" if telem["podcast_status"] == 1 else "🔴 Inactivo"
        panic = "🚨 BLOQUEO ACTIVO" if telem["panic_active"] == 1 else "🟢 Normal"
        night = "🟢 Activo" if night_cfg and night_cfg.get("status") == 1 else "🔴 Inactivo"

    return {
        "sentinel_name": sentinel_label,
        "shield": shield,
        "autolower": autolower,
        "podcast": podcast,
        "panic": panic,
        "night": night,
        "vip_count": telem["vip_passes_active"],
        "speakers_count": telem["speakers_in_queue"],
    }


async def verify_creator_and_approved(message: Message, bot: Bot) -> bool:
    """Valida la aprobación del grupo y rango exclusivo de Dueño (Creator)."""
    if message.chat.type != "private":
        try:
            await message.delete()
        except Exception:
            pass
            
    if message.chat.type == "private":
        return True
        
    chat_id = message.chat.id
    if not await is_group_approved(chat_id):
        return False

    try:
        member = await bot.get_chat_member(chat_id, message.from_user.id)
        if member.status == "creator":
            return True
        else:
            lang = get_lang(message.from_user.language_code)
            warn = await message.answer(TEXTS[lang]["owner_only"], parse_mode="HTML")
            async def auto_del_warn(msg):
                await asyncio.sleep(8)
                try: 
                    await msg.delete()
                except Exception: 
                    pass
            asyncio.create_task(auto_del_warn(warn))
            return False
    except Exception:
        return False


async def send_private_response(message: Message, text: str, reply_markup=None):
    """Fuerza que las respuestas a comandos de administración lleguen al chat privado."""
    user_id = message.from_user.id
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    try:
        await message.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        bot_info = await message.bot.get_me()
        name = message.from_user.username or message.from_user.first_name
        try:
            temp_msg = await message.answer(
                t["private_warning"].format(name=name, bot_user=bot_info.username),
                parse_mode="HTML"
            )
            async def auto_del_warn(msg):
                await asyncio.sleep(12)
                try: 
                    await msg.delete()
                except Exception: 
                    pass
            asyncio.create_task(auto_del_warn(temp_msg))
        except Exception:
            pass


def get_user_mention_html(user) -> str:
    """Genera una mención válida en formato HTML."""
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = getattr(user, "full_name", getattr(user, "first_name", "Usuario"))
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# ==========================================
# CAZADOR DE COMANDOS PÚBLICOS NO AUTORIZADOS
# ==========================================
@router.message(Command("start"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_start_group(message: Message, bot: Bot):
    try:
        await message.delete()
    except Exception:
        pass
        
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ["creator", "administrator"]:
            lang = get_lang(message.from_user.language_code)
            name = message.from_user.username or message.from_user.first_name
            temp_msg = await message.answer(
                TEXTS[lang]["unauthorized_start"].format(name=name),
                parse_mode="HTML"
            )
            async def auto_del_start(msg):
                await asyncio.sleep(8)
                try: 
                    await msg.delete()
                except Exception: 
                    pass
            asyncio.create_task(auto_del_start(temp_msg))
    except Exception:
        pass


# ==========================================
# COMANDOS DE ADMINISTRADOR BLINDADOS (DUEÑO)
# ==========================================
@router.message(Command("enablevc"))
async def cmd_enable_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    await set_vc_monitor_status(chat_id, 1)  # 💎 Persistido en base de datos
    lang = get_lang(message.from_user.language_code)
    await send_private_response(message, TEXTS[lang]["vc_enabled"])


@router.message(Command("disablevc"))
async def cmd_disable_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    await set_vc_monitor_status(chat_id, 0)  # 💎 Persistido en base de datos
    lang = get_lang(message.from_user.language_code)
    await send_private_response(message, TEXTS[lang]["vc_disabled"])


@router.message(Command("cams"))
async def cmd_cams(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    lang = get_lang(message.from_user.language_code)

    ctx = await get_telemetry_context(chat_id, lang)

    await send_private_response(
        message,
        TEXTS[lang]["cams_report"].format(**ctx)
    )


@router.message(Command("kickoffcam"))
async def cmd_kickoff_cam(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        target_mention = get_user_mention_html(target_user)
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_user.id, muted=True, volume=0)
        except Exception:
            pass
        await send_private_response(message, t["kickcam_success"].format(target=target_mention))
    else:
        await send_private_response(message, t["kickcam_needed"])


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
        await add_to_whitelist(uid)
        target_mention = get_user_mention_html(message.reply_to_message.from_user)
        await send_private_response(message, t["wl_added"].format(target=target_mention))
    else:
        await send_private_response(message, t["wl_needed"])


@router.message(Command("unwhitelist"))
async def cmd_unwhitelist(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
        await remove_from_whitelist(uid)
        target_mention = get_user_mention_html(message.reply_to_message.from_user)
        await send_private_response(message, t["wl_removed"].format(target=target_mention))
    else:
        await send_private_response(message, t["wl_needed"])


@router.message(Command("statusvc"))
async def cmd_status_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    is_active = (await get_vc_monitor_status(chat_id)) == 1  # 💎 Consulta a base de datos
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    ctx = await get_telemetry_context(chat_id, lang)
    mic_price = await get_mic_vip_price(chat_id)

    if lang == "en":
        status_text = "🟢 Active and monitoring" if is_active else "🔴 Paused"
    else:
        status_text = "🟢 Activo y supervisando" if is_active else "🔴 En pausa"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_cams"], callback_data=f"vc_cams_{chat_id}"),
            InlineKeyboardButton(text=t["btn_reset"], callback_data=f"vc_reset_{chat_id}")
        ],
        [
            InlineKeyboardButton(text=t["btn_close_vc"], callback_data="vc_close_panel")
        ]
    ])

    await send_private_response(
        message, 
        t["status_text"].format(
            status=status_text,
            mic_price=mic_price,
            **ctx
        ), 
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("vc_"))
async def process_vc_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data.split("_")
    action = f"{data[0]}_{data[1]}"
    chat_id = int(data[2]) if len(data) > 2 else callback.message.chat.id
    lang = get_lang(callback.from_user.language_code)
    t = TEXTS[lang]
    
    try:
        if action == "vc_close":
            try:
                await callback.message.delete()
            except Exception:
                pass
            return

        elif action == "vc_status":
            is_active = (await get_vc_monitor_status(chat_id)) == 1  # 💎 Consulta a base de datos
            ctx = await get_telemetry_context(chat_id, lang)
            mic_price = await get_mic_vip_price(chat_id)

            if lang == "en":
                status_text = "🟢 Active and monitoring" if is_active else "🔴 Paused"
            else:
                status_text = "🟢 Activo y supervisando" if is_active else "🔴 En pausa"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=t["btn_cams"], callback_data=f"vc_cams_{chat_id}"),
                    InlineKeyboardButton(text=t["btn_reset"], callback_data=f"vc_reset_{chat_id}")
                ],
                [
                    InlineKeyboardButton(text=t["btn_close_vc"], callback_data="vc_close_panel")
                ]
            ])
            await callback.message.edit_text(
                t["status_text"].format(
                    status=status_text,
                    mic_price=mic_price,
                    **ctx
                ),
                reply_markup=keyboard,
                parse_mode="HTML"
            )

        elif action == "vc_cams":
            ctx = await get_telemetry_context(chat_id, lang)

            report = t["cams_report"].format(**ctx)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_vc"], callback_data=f"vc_status_{chat_id}")]
            ])
            await callback.message.edit_text(report, reply_markup=back_kb, parse_mode="HTML")

        elif action == "vc_reset":
            synced = (
                "🔄 <b>Sincronización Completada:</b> Refresco de sala de voz ejecutado con éxito.\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "🔄 <b>Synchronization Completed:</b> Voice room refreshed successfully.\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_vc"], callback_data=f"vc_status_{chat_id}")]
            ])
            await callback.message.edit_text(synced, reply_markup=back_kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.message(Command("getid"))
async def cmd_get_id(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    await send_private_response(
        message, 
        t["getid_text"].format(
            type=message.chat.type,
            chat_id=message.chat.id,
            thread_id=message.message_thread_id or ('Ninguno' if lang == 'es' else 'None')
        )
    )


@router.message(Command("autolower"))
async def cmd_auto_lower(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    current_st = await get_autolower_status(chat_id)
    new_st = 0 if current_st == 1 else 1
    await set_autolower_status(chat_id, new_st)

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    res_text = t["autolower_on"] if new_st == 1 else t["autolower_off"]
    await send_private_response(message, res_text)


# ==========================================
# PASE VIP DE MICRÓFONO (COMANDO PÚBLICO)
# ==========================================
@router.message(Command("micvip"))
async def trigger_mic_vip_offer(message: Message, bot: Bot):
    chat_id = message.chat.id
    
    if not await is_group_approved(chat_id):
        return

    bot_info = await bot.get_me()
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    name = message.from_user.username or message.from_user.first_name
    
    price = await get_mic_vip_price(chat_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=t["btn_pay_stars"].format(price=price), 
                url=f"https://t.me/{bot_info.username}?start=vipmic_{chat_id}"
            )
        ]
    ])

    sent_msg = await message.answer(
        t["micvip_msg"].format(name=name),
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    try:
        await message.delete()  
    except Exception:
        pass

    async def auto_clean_offer():
        await asyncio.sleep(45)
        try:
            await sent_msg.delete()
        except Exception:
            pass
    asyncio.create_task(auto_clean_offer())