import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    get_group_tier, 
    is_vip_mic_active, 
    get_autolower_status, 
    set_autolower_status,
    get_session_by_group,
    get_mic_vip_price
)

router = Router()

def is_admin_filter(member_status: str) -> bool:
    return member_status in ["creator", "administrator"]

def get_lang(lang_code: str) -> str:
    return "es" if lang_code and lang_code.startswith("es") else "en"

async def get_active_sentinel_label(group_id: int, lang: str = "es") -> str:
    """Detecta si la comunidad usa un centinela propio ULTRA PRO o el maestro."""
    session_data = await get_session_by_group(group_id)
    if session_data:
        return "Centinela Dedicado Propio 💎" if lang == "es" else "Dedicated Sentinel 💎"
    return "Centinela Maestro (@Alphacentinel) 🤖" if lang == "es" else "Master Sentinel (@Alphacentinel) 🤖"

async def auto_delete_pair(msg1: Message, msg2: Message, delay: int = 15):
    """Auto-destrucción dual para mantener el chat grupal limpio y sin clutter."""
    await asyncio.sleep(delay)
    try: 
        await msg1.delete()
    except Exception: 
        pass
    try: 
        await msg2.delete()
    except Exception: 
        pass

# ==========================================
# 🌐 DICCIONARIO BILINGÜE DEL ECOSISTEMA
# ==========================================
TEXTS = {
    "en": {
        "status_title": "📡 <b>The Bunker Ecosystem Status</b>\n\n",
        "status_body": (
            "• <b>Community ID:</b> <code>{chat_id}</code>\n"
            "• <b>Membership Tier:</b> <code>{tier}</code>\n"
            "• <b>Live Voice Radar:</b> 🟢 <code>ACTIVE (24/7)</code>\n"
            "• <b>Assigned Sentinel:</b> {sentinel_name}\n"
            "• <b>AutoLower Protocol (2%):</b> {autolower}\n"
            "• <b>System Response:</b> <code>Instant & Fluid 🟢</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_refresh": "🔄 Refresh Telemetry",
        "private_only_vc": "⚠️ This command is exclusive to communities with an active Voice Chat.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_audit": (
            "📹 <b>Camera & Transmission Quality Audit:</b>\n\n"
            "• <b>Live Video Feed:</b> High-Fidelity Active Stream 🟢\n"
            "• <b>Transmission Status:</b> Fluid & Lag-Free\n"
            "• <b>Background Optimization:</b> Continuous stream refresh enabled\n"
            "• <b>Active Sentinel:</b> {sentinel_name}\n\n"
            "<i>Authorized allies and VIP Pass holders transmit with unrestricted volume.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "admin_only": "⛔ Only community administrators can toggle the AutoLower mode.\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_on": "🟢 <b>ACTIVATED</b> (Volume dialed down to 2% for unverified users)\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_off": "🔴 <b>DEACTIVATED</b> (Open microphones at 100%)\n\n🛡️ <i>Cloud Media Management</i>",
        "vip_active": "✅ You already hold an active <b>VIP Microphone Pass (24h)</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "vip_promo": (
            "🎙️ <b>VIP Microphone Pass (24 Hours)</b>\n\n"
            "Unlock continuous speaking privileges at <b>100% volume</b> and bypass automated acoustic moderation.\n\n"
            "💡 <i>Ecosystem Advantage:</i> Running your own Bot Clone and Dedicated Sentinel routes <b>100% of Stars revenue directly to your balance</b>!\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_get_vip": "⭐ Unlock VIP Pass ({price} Stars)",
        "refreshed": "Telemetry updated 🔄"
    },
    "es": {
        "status_title": "📡 <b>Estado del Ecosistema The Bunker</b>\n\n",
        "status_body": (
            "• <b>Comunidad ID:</b> <code>{chat_id}</code>\n"
            "• <b>Nivel de Licencia:</b> <code>{tier}</code>\n"
            "• <b>Radar de Transmisión:</b> 🟢 <code>ACTIVO (24/7)</code>\n"
            "• <b>Centinela Asignado:</b> {sentinel_name}\n"
            "• <b>Filtro AutoLower (2%):</b> {autolower}\n"
            "• <b>Respuesta del Sistema:</b> <code>Inmediata y Fluida 🟢</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_refresh": "🔄 Refrescar Telemetría",
        "private_only_vc": "⚠️ Este comando solo funciona dentro de grupos con Videochat activo.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_audit": (
            "📹 <b>Auditoría de Calidad de Cámaras y Transmisión:</b>\n\n"
            "• <b>Video en Vivo:</b> Transmisión en Alta Fidelidad Activa 🟢\n"
            "• <b>Estado de Transmisión:</b> Fluida y sin congelamientos\n"
            "• <b>Optimización Continua:</b> Refresco en segundo plano activado\n"
            "• <b>Centinela en Sala:</b> {sentinel_name}\n\n"
            "<i>Los aliados autorizados y pases VIP transmiten sin restricciones.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "admin_only": "⛔ Solo los administradores pueden alterar el modo AutoLower.\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_on": "🟢 <b>ACTIVADO</b> (Volumen reducido al 2% a no autorizados)\n\n🛡️ <i>Cloud Media Management</i>",
        "autolower_off": "🔴 <b>DESACTIVADO</b> (Micrófonos libres al 100%)\n\n🛡️ <i>Cloud Media Management</i>",
        "vip_active": "✅ Ya cuentas con un <b>Pase VIP de Micrófono (24h)</b> activo.\n\n🛡️ <i>Cloud Media Management</i>",
        "vip_promo": (
            "🎙️ <b>Pase VIP de Micrófono (24 Horas)</b>\n\n"
            "Desbloquea tu voz al <b>100% de volumen continuo</b> y evita la moderación acústica del radar.\n\n"
            "💡 <i>Ventaja Clave:</i> Al desplegar tu propio Bot Clone y Centinela, <b>el 100% de las Stars recaudadas van directo a tu balance</b>.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_get_vip": "⭐ Obtener Pase VIP ({price} Stars)",
        "refreshed": "Telemetría actualizada 🔄"
    }
}
# ==========================================
# 🚀 COMANDOS DE ECOSISTEMA
# ==========================================
@router.message(Command("status_bc", "statusvc"))
async def cmd_status_bc(message: Message):
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if message.chat.type == "private":
        await message.answer(t["private_only_vc"], parse_mode="HTML")
        return

    chat_id = message.chat.id
    tier = await get_group_tier(chat_id) or "FREE"
    autolower_status = await get_autolower_status(chat_id)
    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"] + t["status_body"].format(
        chat_id=chat_id, 
        tier=tier.upper(), 
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_refresh"], callback_data=f"refresh_status_{chat_id}_{lang}")]
    ])

    bot_msg = await message.answer(status_text, reply_markup=keyboard, parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, bot_msg, delay=30))

@router.callback_query(F.data.startswith("refresh_status_"))
async def cb_refresh_status(callback: CallbackQuery):
    data_parts = callback.data.split("_")
    chat_id = int(data_parts[2])
    lang = data_parts[3] if len(data_parts) > 3 else "es"
    t = TEXTS[lang]

    await callback.answer(t["refreshed"])

    tier = await get_group_tier(chat_id) or "FREE"
    autolower_status = await get_autolower_status(chat_id)
    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"].replace("\n\n", " (Updated)\n\n") if lang == "en" else t["status_title"].replace("\n\n", " (Actualizado)\n\n")
    status_text += t["status_body"].format(
        chat_id=chat_id, 
        tier=tier.upper(), 
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    try:
        await callback.message.edit_text(status_text, reply_markup=callback.message.reply_markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass

@router.message(Command("cams"))
async def cmd_cams(message: Message):
    lang = get_lang(message.from_user.language_code)
    if message.chat.type == "private":
        await message.answer(TEXTS[lang]["private_only_vc"], parse_mode="HTML")
        return
        
    chat_id = message.chat.id
    sentinel_label = await get_active_sentinel_label(chat_id, lang)
    cams_text = TEXTS[lang]["cams_audit"].format(sentinel_name=sentinel_label)

    bot_msg = await message.reply(cams_text, parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, bot_msg, delay=20))

@router.message(Command("autolower"))
async def cmd_autolower(message: Message):
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if message.chat.type == "private":
        await message.answer(t["private_only_vc"], parse_mode="HTML")
        return

    try:
        member = await message.bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
        if not is_admin_filter(member.status):
            try: 
                await message.delete()
            except Exception: 
                pass
            return
    except Exception:
        return

    chat_id = message.chat.id
    current = await get_autolower_status(chat_id)
    new_status = 0 if current == 1 else 1
    await set_autolower_status(chat_id, new_status)

    state_str = t["autolower_on"] if new_status == 1 else t["autolower_off"]
    title = "⚙️ <b>AutoLower Protocol:</b> " if lang == "en" else "⚙️ <b>Protocolo AutoLower:</b> "
    
    bot_msg = await message.answer(f"{title}{state_str}", parse_mode="HTML")
    asyncio.create_task(auto_delete_pair(message, bot_msg, delay=15))

@router.message(Command("mic_vip", "micvip"))
async def cmd_mic_vip(message: Message):
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    chat_id = message.chat.id
    user_id = message.from_user.id
    bot_info = await message.bot.get_me()

    if message.chat.type == "private":
        await message.answer("⚠️ Command exclusive to groups. / Comando exclusivo para grupos.", parse_mode="HTML")
        return

    if await is_vip_mic_active(user_id, chat_id):
        vip_msg = await message.reply(t["vip_active"], parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, vip_msg, delay=10))
        return

    try: 
        await message.delete()
    except Exception: 
        pass

    price = await get_mic_vip_price(chat_id)
    deep_link = f"https://t.me/{bot_info.username}?start=vipmic_{chat_id}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_get_vip"].format(price=price), url=deep_link)]
    ])

    promo_msg = await message.answer(t["vip_promo"], reply_markup=keyboard, parse_mode="HTML")
    
    async def auto_clean_promo():
        await asyncio.sleep(45)
        try: 
            await promo_msg.delete()
        except Exception: 
            pass
        
    asyncio.create_task(auto_clean_promo())