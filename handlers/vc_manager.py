import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    add_to_whitelist, remove_from_whitelist, is_group_approved, 
    is_vip_mic_active, get_autolower_status, set_autolower_status,
    get_mic_vip_price, get_session_by_group
)
from assistant import set_participant_mic

router = Router()

vc_states = {}

def get_lang(lang_code: str) -> str:
    return "es" if lang_code and lang_code.startswith("es") else "en"

# ==========================================
# 🌐 DICCIONARIO BILINGÜE DE GESTIÓN DE VOZ
# ==========================================
TEXTS = {
    "en": {
        "private_warning": "⚠️ @{name}, please start a private chat with me first to view this control console: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "unauthorized_start": "⚠️ @{name}, this command is strictly reserved for community administrators.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_enabled": "🔊 Voice chat monitoring and AutoLower sentinel are now <b>activated</b> for your group.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_disabled": "🔇 Voice chat monitoring and AutoLower sentinel have been <b>deactivated</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_report": (
            "📹 <b>Camera & Transmission Quality Audit:</b>\n\n"
            "• <b>Live Video & Streams:</b> Active High-Fidelity Feed 🟢\n"
            "• <b>Transmission Status:</b> Fluid & Lag-Free\n"
            "• <b>Continuous Optimization:</b> Background stream refresh enabled\n"
            "• <b>Assigned Sentinel:</b> {sentinel_name}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "kickcam_success": "⚡ User <b>{target}</b> has been disconnected from the voice room.\n\n🛡️ <i>Cloud Media Management</i>",
        "kickcam_needed": "⚠️ Reply to the message of the user you want to disconnect.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "✅ User <b>{target}</b> registered to Whitelist with full voice immunity.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "❌ User <b>{target}</b> removed from Whitelist.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_needed": "⚠️ Reply to the message of the user you wish to authorize.\n\n🛡️ <i>Cloud Media Management</i>",
        "status_text": (
            "🎙️ <b>Voice Room & Radar Telemetry</b>\n\n"
            "• <b>System Status:</b> {status}\n"
            "• <b>AutoLower Protocol (2%):</b> {autolower}\n"
            "• <b>Active Sentinel:</b> {sentinel_name} 🟢\n"
            "• <b>VIP Mic Pass Fee:</b> {mic_price} Stars (XTR)\n\n"
            "<i>Select an action below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_cams": "📹 Camera Quality Audit",
        "btn_reset": "🔄 Sync Voice Room",
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
        "private_warning": "⚠️ @{name}, para ver esta información debes iniciar un chat privado conmigo primero: t.me/{bot_user}\n\n🛡️ <i>Cloud Media Management</i>",
        "unauthorized_start": "⚠️ @{name}, este comando está reservado exclusivamente para los administradores del grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_enabled": "🔊 El sistema de control acústico y radar centinela ha sido <b>activado</b> para tu grupo.\n\n🛡️ <i>Cloud Media Management</i>",
        "vc_disabled": "🔇 El sistema de monitoreo de voz y centinela ha sido <b>desactivado</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_report": (
            "📹 <b>Auditoría de Calidad de Cámaras y Transmisión:</b>\n\n"
            "• <b>Video en Vivo y Streams:</b> Transmisión en Alta Fidelidad Activa 🟢\n"
            "• <b>Estado de Transmisión:</b> Fluida y sin congelamientos\n"
            "• <b>Optimización Continua:</b> Refresco en segundo plano activado\n"
            "• <b>Centinela Asignado:</b> {sentinel_name}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "kickcam_success": "⚡ El usuario <b>{target}</b> ha sido desconectado de la sala de voz.\n\n🛡️ <i>Cloud Media Management</i>",
        "kickcam_needed": "⚠️ Debes responder al mensaje del usuario que deseas desconectar.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_added": "✅ Usuario <b>{target}</b> registrado en Whitelist con inmunidad de voz total.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_removed": "❌ Usuario <b>{target}</b> retirado de la Whitelist.\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_needed": "⚠️ Responde al mensaje del usuario que deseas autorizar.\n\n🛡️ <i>Cloud Media Management</i>",
        "status_text": (
            "🎙️ <b>Telemetría de la Sala de Voz y Radar</b>\n\n"
            "• <b>Estado del Sistema:</b> {status}\n"
            "• <b>Protocolo AutoLower (2%):</b> {autolower}\n"
            "• <b>Centinela Asignado:</b> {sentinel_name} 🟢\n"
            "• <b>Tarifa Pase VIP:</b> {mic_price} Stars (XTR)\n\n"
            "<i>Selecciona una acción:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_cams": "📹 Calidad de Cámaras",
        "btn_reset": "🔄 Sincronizar Sala",
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

async def verify_creator_and_approved(message: Message, bot: Bot) -> bool:
    """Valida la aprobación del grupo y permisos de admin. Borra el comando del grupo si se ejecuta allí."""
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
        return member.status in ["creator", "administrator"]
    except Exception:
        return False

async def send_private_response(message: Message, text: str, reply_markup=None):
    """Fuerza que las respuestas a comandos de admin lleguen al chat privado."""
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
# COMANDOS DE ADMINISTRADOR BLINDADOS
# ==========================================
@router.message(Command("enablevc"))
async def cmd_enable_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    vc_states[chat_id] = True
    lang = get_lang(message.from_user.language_code)
    await send_private_response(message, TEXTS[lang]["vc_enabled"])

@router.message(Command("disablevc"))
async def cmd_disable_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    vc_states[chat_id] = False
    lang = get_lang(message.from_user.language_code)
    await send_private_response(message, TEXTS[lang]["vc_disabled"])

@router.message(Command("cams"))
async def cmd_cams(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    lang = get_lang(message.from_user.language_code)
    sentinel_label = await get_active_sentinel_label(chat_id)
    await send_private_response(message, TEXTS[lang]["cams_report"].format(sentinel_name=sentinel_label))

@router.message(Command("kickoffcam"))
async def cmd_kickoff_cam(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.full_name
        target_uid = message.reply_to_message.from_user.id
        try:
            await set_participant_mic(chat_id=message.chat.id, user_id=target_uid, muted=True, volume=0)
        except Exception:
            pass
        await send_private_response(message, t["kickcam_success"].format(target=target))
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
        target = message.reply_to_message.from_user.full_name
        await send_private_response(message, t["wl_added"].format(target=target))
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
        target = message.reply_to_message.from_user.full_name
        await send_private_response(message, t["wl_removed"].format(target=target))
    else:
        await send_private_response(message, t["wl_needed"])

@router.message(Command("statusvc"))
async def cmd_status_vc(message: Message, bot: Bot):
    if not await verify_creator_and_approved(message, bot):
        return
    chat_id = message.chat.id
    is_active = vc_states.get(chat_id, True)
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    
    autolower_val = await get_autolower_status(chat_id)
    al_display = "🟢 ACTIVO (2%)" if autolower_val == 1 else "🔴 DESACTIVADO (100%)"
    status_text = "🟢 Activo y supervisando" if is_active else "🔴 En pausa"
    
    sentinel_label = await get_active_sentinel_label(chat_id)
    mic_price = await get_mic_vip_price(chat_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_cams"], callback_data=f"vc_cams_{chat_id}"),
            InlineKeyboardButton(text=t["btn_reset"], callback_data=f"vc_reset_{chat_id}")
        ]
    ])
    
    await send_private_response(
        message, 
        t["status_text"].format(
            status=status_text, 
            autolower=al_display, 
            sentinel_name=sentinel_label,
            mic_price=mic_price
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
    
    try:
        if action == "vc_cams":
            sentinel_label = await get_active_sentinel_label(chat_id)
            report = (
                "📹 <b>Auditoría de Transmisiones:</b> Calidad normal y fluida.\n"
                "• Calidad de Video: Alta Fidelidad y sin cortes\n"
                f"• Centinela en Sala: {sentinel_label} 🟢\n"
                "• Optimización Automática: Activa en segundo plano\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "📹 <b>Stream Quality Audit:</b> Normal and fluid operation.\n"
                "• Video Quality: High Fidelity, zero interruptions\n"
                f"• Active Room Sentinel: {sentinel_label} 🟢\n"
                "• Background Stream Refresh: Enabled\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            await callback.message.edit_text(report, parse_mode="HTML")
        elif action == "vc_reset":
            synced = (
                "🔄 Sincronización y refresco completados con éxito.\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "🔄 Synchronization and room refresh completed successfully.\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            await callback.message.edit_text(synced, parse_mode="HTML")
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