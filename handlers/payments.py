import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, LabeledPrice, PreCheckoutQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    approve_group, get_group_tier, grant_vip_mic, get_mic_vip_price,
    get_vip_badge_title
)
from assistant import set_participant_mic
from handlers.user_private import is_clone_bot, get_master_bot_username

logger = logging.getLogger("payments_gateway")
router = Router()

# ==========================================
# 💰 TARIFAS Y CONFIGURACIÓN DE FACTURACIÓN
# ==========================================
PRICE_PRO_STARS = 300          # 300 Stars Telegram (~$3.00 USD)
PRICE_ULTRAPRO_STARS = 600     # 600 Stars Telegram (~$6.00 USD)
DEFAULT_PRICE_VIP_MIC = 50     # Tarifa base en Stars para pase 24h

# Pasarelas de Pago Oficiales - Cloud Media Management
PAYPAL_LINK = "https://paypal.me/Felipecosmic"
BINANCE_PAY_LINK = "https://app.binance.com/uni-qr/request-to-pay?billOrderId=452404659499556864&billType=request_a_payment"
TON_MINI_APP_LINK = "https://t.me/thebunkerapp_bot?start=miniapp_ton"
ASSISTANT_INVITE_URL = "https://t.me/Alphacentinel?startgroup=true"


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la pasarela adecuada."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


async def is_user_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el usuario ostenta el rango máximo de Dueño / Creador del grupo."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status == "creator"
    except Exception:
        return False


async def auto_delete_pair(msg1: Message, msg2: Message, delay: int = 15):
    """Auto-destrucción dual para mantener el chat grupal libre de clutter visual."""
    await asyncio.sleep(delay)
    try: 
        await msg1.delete()
    except Exception: 
        pass
    try: 
        await msg2.delete()
    except Exception: 
        pass


def _clone_subscription_redirect(lang: str, plan: str, chat_id: int):
    """
    Construye el aviso + botón que redirige el cobro de una suscripción PRO/ULTRA PRO
    hacia el Bot Maestro cuando la orden se originó en un Bot Clon.
    """
    master_username = get_master_bot_username()
    if not master_username:
        return None, None

    text = (
        "⭐ <b>Suscripción Oficial — Cloud Media Management</b>\n\n"
        "Los planes PRO y ULTRA PRO son un servicio directo de la plataforma y se facturan siempre desde el <b>Bot Maestro</b>, para no descontar Stars del balance de tu Bot Clon.\n\n"
        "Pulsa el botón para completar el pago de forma segura:\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        "⭐ <b>Official Subscription — Cloud Media Management</b>\n\n"
        "PRO and ULTRA PRO plans are a direct platform service and are always billed through the <b>Master Bot</b>, so they never draw Stars from your Bot Clone's balance.\n\n"
        "Tap the button to complete payment securely:\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⭐ Pagar en el Bot Maestro" if lang == "es" else "⭐ Pay via Master Bot",
            url=f"https://t.me/{master_username}?start=sub_{plan}_{chat_id}"
        )]
    ])
    return text, markup


# ==========================================
# 🌐 DICCIONARIO BILINGÜE DE FACTURACIÓN Y PASARELAS
# ==========================================
TEXTS = {
    "en": {
        "owner_only": "⛔ <b>Access Denied:</b> Subscription and billing protocols are exclusive to the Community Owner.\n\n🛡️ <i>Cloud Media Management</i>",
        "active": (
            "✨ <b>Command Center: Subscriptions & Licensing</b>\n\n"
            "• <b>Community ID:</b> <code>{chat_id}</code>\n"
            "• <b>Operational Tier:</b> <code>{tier}</code>\n\n"
            "✅ <i>This ecosystem is operating under an active premium license. All elite modules, anti-spam barriers, and voice controls are fully unlocked.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "free": (
            "🤖 <b>Command Center: Subscriptions & Licensing</b>\n\n"
            "• <b>Community ID:</b> <code>{chat_id}</code>\n"
            "• <b>Operational Tier:</b> <code>BASIC (Free Tier)</code>\n\n"
            "Upgrade your network to remove daily rate limits, activate automated cleansers, or deploy autonomous clone architectures:\n\n"
            "<i>Select your payment gateway below to activate instantly:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pro_stars": "⭐ Upgrade to PRO (300 XTR)",
        "btn_ultra_stars": "💎 Upgrade to ULTRA PRO (600 XTR)",
        "btn_paypal": "💳 PayPal ($3 / $6 USD)",
        "btn_binance": "🟡 Binance Pay (Instant)",
        "btn_ton": "💎 TON Wallet (Mini App)",
        "btn_back": "🔙 Back to Main Menu",
        "btn_back_group": "🔙 Back to Group Panel",
        "btn_pay_stars": "⭐ Pay with Stars",
        
        # 🛡️ Límite API Telegram: Máximo 32 caracteres estrictos para títulos de factura
        "inv_pro_t": "PRO Subscription (300 XTR)",
        "inv_pro_d": "Unlimited bot commands, automated purge center, custom captcha pro, and master sentinel shielding.",
        "inv_ultra_t": "ULTRA PRO License (600 XTR)",
        "inv_ultra_d": "All PRO features + Bot Clone architecture + Dedicated Voice Sentinel + Weekly VC Scheduler (100% Stars yours).",
        "inv_vip_t": "VIP Mic Pass (24h)",
        "inv_vip_d": "Unrestricted 100% voice transmission privileges for 24 hours in community voice chats.",
        
        "pmt_ok_pro": (
            "🎉 <b>Payment Confirmed! PRO Plan Active</b>\n\n"
            "• Community: <code>{chat_id}</code> upgraded to <b>PRO ⭐</b>\n"
            "• All daily command caps and service message purge quotas have been removed.\n\n"
            "💡 <b>Deployment Step:</b> Add our Master Sentinel to your voice chats to manage microphones:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_ok_ultra": (
            "💎 <b>Payment Confirmed! ULTRA PRO License Active</b>\n\n"
            "• Community: <code>{chat_id}</code> upgraded to <b>ULTRA PRO 💎</b>\n"
            "• Autonomous Bot Clone deployment, isolated Voice Sentinel node, and Weekly VC Cron unlocked.\n"
            "• <b>Direct Monetization:</b> 100% of all Telegram Stars collected via /micvip enter your bot clone balance!\n\n"
            "💡 <b>Deployment Step:</b> Link your @BotFather token and secondary session string to activate your private node:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_vip_ok": (
            "🎙️ <b>VIP Microphone Pass Activated!</b>\n\n"
            "Voice unlocked. Mic volume dialed straight to <b>100%</b> by the Voice Sentinel.\n"
            "You have exactly <b>24 hours</b> of continuous, unrestricted live transmission.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_add_master": "🤖 Add Master Sentinel (@Alphacentinel)",
        "btn_setup_clone": "🧬 Setup Clone & Dedicated Sentinel",
        "err_inv": "⚠️ An error occurred while generating the invoice. Please try again.",
        "err_link": "⚠️ Invalid VIP activation link or expired parameters.",
        "private_only": "⚠️ Please open a private chat with me to access the billing terminal: t.me/{bot_username}"
    },
    "es": {
        "owner_only": "⛔ <b>Acceso Denegado:</b> Las opciones de suscripción y facturación son exclusivas para el Dueño de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "active": (
            "✨ <b>Centro de Mando: Suscripciones y Licencias</b>\n\n"
            "• <b>Comunidad ID:</b> <code>{chat_id}</code>\n"
            "• <b>Nivel Operativo:</b> <code>{tier}</code>\n\n"
            "✅ <i>Este ecosistema opera bajo una licencia premium activa. Todas las barreras de antispam, aduana y control de voz están desbloqueadas.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "free": (
            "🤖 <b>Centro de Mando: Suscripciones y Licencias</b>\n\n"
            "• <b>Comunidad ID:</b> <code>{chat_id}</code>\n"
            "• <b>Nivel Operativo:</b> <code>BÁSICO (Plan Gratuito)</code>\n\n"
            "Eleva tu comunidad para eliminar los topes de comandos diarios, activar purgas automatizadas y desplegar clones autónomos:\n\n"
            "<i>Selecciona tu pasarela preferida para activar al instante:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pro_stars": "⭐ Mejorar a PRO (300 XTR)",
        "btn_ultra_stars": "💎 Mejorar a ULTRA PRO (600 XTR)",
        "btn_paypal": "💳 PayPal ($3 / $6 USD)",
        "btn_binance": "🟡 Binance Pay (Instantáneo)",
        "btn_ton": "💎 TON Wallet (Mini App)",
        "btn_back": "🔙 Volver al Menú Principal",
        "btn_back_group": "🔙 Volver al Panel del Grupo",
        "btn_pay_stars": "⭐ Pagar con Stars",
        
        # 🛡️ Límite API Telegram: Máximo 32 caracteres estrictos para títulos de factura
        "inv_pro_t": "Suscripción PRO (300 XTR)",
        "inv_pro_d": "Comandos ilimitados, purga de mensajes automatizada, captcha pro y centinela maestro.",
        "inv_ultra_t": "Licencia ULTRA PRO (600 XTR)",
        "inv_ultra_d": "Todo PRO + Arquitectura Bot Clone + Centinela Dedicado Propio + Programador VC Semanal (100% Stars para ti).",
        "inv_vip_t": "Pase VIP Micrófono (24h)",
        "inv_vip_d": "Privilegios de voz continua al 100% de volumen por 24 horas en salas de voz y videochats.",
        
        "pmt_ok_pro": (
            "🎉 <b>¡Pago Confirmado! Plan PRO Activado</b>\n\n"
            "• Comunidad <code>{chat_id}</code> elevada al estándar <b>PRO ⭐</b> con éxito.\n"
            "• Los topes diarios de 3 comandos y restricciones de purga han sido levantados.\n\n"
            "💡 <b>Paso Siguiente:</b> Añade al Centinela Maestro a tu grupo para moderar llamadas de voz:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_ok_ultra": (
            "💎 <b>¡Pago Confirmado! Nivel ULTRA PRO Activado</b>\n\n"
            "• Comunidad <code>{chat_id}</code> elevada a <b>ULTRA PRO 💎</b>.\n"
            "• Clonación autónoma con @BotFather, Centinela aislado antiban y cronograma semanal de videochats desbloqueados.\n"
            "• <b>Monetización Directa:</b> El 100% de las Stars cobradas por /micvip van directamente a tu propio bot clon.\n\n"
            "💡 <b>Paso Siguiente:</b> Conecta tu token de bot y tu sesión de Pyrogram en el panel privado:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_vip_ok": (
            "🎙️ <b>¡Pase VIP de Micrófono Activado!</b>\n\n"
            "Voz liberada. Volumen configurado al <b>100%</b> por el Centinela de Voz.\n"
            "Cuentas con exactamente <b>24 horas</b> de transmisión continua sin atenuación.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_add_master": "🤖 Añadir Centinela Maestro (@Alphacentinel)",
        "btn_setup_clone": "🧬 Configurar Clon & Centinela Propio",
        "err_inv": "⚠️ Error al generar la factura. Intenta nuevamente.",
        "err_link": "⚠️ Enlace de facturación no válido, sin grupo asociado o expirado.",
        "private_only": "⚠️ Inicia un chat privado conmigo para gestionar suscripciones: t.me/{bot_username}"
    }
}


# ==========================================
# 🚀 COMANDOS DE ACCESO A PLANES EN GRUPOS (EXCLUSIVO DUEÑO)
# ==========================================
@router.message(Command("pro", "ultra"))
async def cmd_pro_ultra(message: Message, command: CommandObject, bot: Bot):
    """Permite al dueño auditar su plan o solicitar pasarelas al privado."""
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if message.chat.type == "private":
        await message.answer(
            f"⚠️ Por favor ejecuta /{command.command} dentro de tu grupo para vincular la facturación a esa comunidad.",
            parse_mode="HTML"
        )
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_user_creator(bot, chat_id, user_id):
        try:
            warn = await message.reply(t["owner_only"], parse_mode="HTML")
            asyncio.create_task(auto_delete_pair(message, warn, delay=8))
        except Exception:
            pass
        return

    try: 
        await message.delete()
    except Exception: 
        pass

    current_tier = await get_group_tier(chat_id)

    if current_tier in ["pro", "ultra_pro"]:
        tier_display = "PRO ⭐" if current_tier == "pro" else "ULTRA PRO 💎"
        private_text = t["active"].format(chat_id=chat_id, tier=tier_display)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
        ])
    else:
        private_text = t["free"].format(chat_id=chat_id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_pro_stars"], callback_data=f"inv_pro_{chat_id}_{lang}"),
                InlineKeyboardButton(text=t["btn_ultra_stars"], callback_data=f"inv_ultra_{chat_id}_{lang}")
            ],
            [
                InlineKeyboardButton(text=t["btn_paypal"], url=PAYPAL_LINK),
                InlineKeyboardButton(text=t["btn_binance"], url=BINANCE_PAY_LINK)
            ],
            [
                InlineKeyboardButton(text=t["btn_ton"], url=TON_MINI_APP_LINK)
            ],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
        ])

    try:
        await bot.send_message(chat_id=user_id, text=private_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        bot_info = await bot.get_me()
        temp_msg = await message.answer(t["private_only"].format(bot_username=bot_info.username), parse_mode="HTML")
        asyncio.create_task(auto_delete_pair(message, temp_msg, delay=12))


# ==========================================
# 🔗 ENRUTAMIENTO DE ENLACES PROFUNDOS (/start sub_)
# ==========================================
@router.message(Command("start"), F.text.contains("sub_"))
async def cmd_start_subscription(message: Message, command: CommandObject, bot: Bot):
    """Recibe parámetros profundos de suscripción para despachar facturas en Stars al instante."""
    if message.chat.type != "private":
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    args = command.args or ""

    if is_clone_bot(bot) and (args.startswith("sub_pro") or args.startswith("sub_ultra")):
        redirect_plan = "pro" if args.startswith("sub_pro") else "ultra"
        redirect_chat_id = 0
        redirect_parts = args.split("_")
        if len(redirect_parts) > 2:
            try:
                redirect_chat_id = int(redirect_parts[2])
            except ValueError:
                redirect_chat_id = 0
        redirect_text, redirect_markup = _clone_subscription_redirect(lang, redirect_plan, redirect_chat_id)
        if redirect_text:
            try:
                await message.answer(redirect_text, reply_markup=redirect_markup, parse_mode="HTML")
            except Exception:
                pass
            return
        logger.warning("⚠️ [Blindaje Suscripción] MASTER_BOT_USERNAME no resuelto aún; se factura por excepción desde el Clon.")

    try:
        chat_id_target = 0
        if args.startswith("sub_pro"):
            parts = args.split("_")
            if len(parts) > 2:
                chat_id_target = int(parts[2])
            price = PRICE_PRO_STARS
            title = t["inv_pro_t"]
            desc = t["inv_pro_d"]
            payload = f"sub_pro_{chat_id_target}"
        elif args.startswith("sub_ultra"):
            parts = args.split("_")
            if len(parts) > 2:
                chat_id_target = int(parts[2])
            price = PRICE_ULTRAPRO_STARS
            title = t["inv_ultra_t"]
            desc = t["inv_ultra_d"]
            payload = f"sub_ultra_{chat_id_target}"
        else:
            return

        # 🔒 Blindaje estricto: rechaza cobros si el ID de grupo no es un supergrupo válido negativo
        if chat_id_target >= 0:
            await message.answer(t["err_link"], parse_mode="HTML")
            return

        prices = [LabeledPrice(label=title, amount=price)]
        back_btn = InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id_target}_{lang}")
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({price} XTR)", pay=True)],
            [back_btn]
        ])
        
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=title,
            description=desc,
            payload=payload,
            provider_token="",  # Telegram Stars
            currency="XTR",
            prices=prices,
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Error generando factura de suscripción en start: {e}")
        await message.answer(t["err_inv"], parse_mode="HTML")


# ==========================================
# 🎙️ FACTURACIÓN DINÁMICA DE PASE VIP DE MICRÓFONO
# ==========================================
@router.message(Command("start"), F.text.contains("vipmic_"))
async def cmd_start_vipmic(message: Message, command: CommandObject, bot: Bot):
    """Despacha la factura en Stars para desbloquear el micrófono por 24h consultando la tarifa del grupo."""
    if message.chat.type != "private":
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    args = command.args or ""

    if args.startswith("vipmic_"):
        try:
            chat_id = int(args.split("_")[1])
            
            # 🔒 Seguro antibug: El pase de micrófono es solo para supergrupos reales
            if chat_id >= 0:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            title = t["inv_vip_t"]
            desc = t["inv_vip_d"]
            payload = f"vip_mic_{chat_id}"
            
            dynamic_price = await get_mic_vip_price(chat_id)
            final_price = dynamic_price if dynamic_price and dynamic_price > 0 else DEFAULT_PRICE_VIP_MIC
            
            prices = [LabeledPrice(label=title, amount=final_price)]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({final_price} XTR)", pay=True)],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])
            
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=title,
                description=desc,
                payload=payload,
                provider_token="",  # Telegram Stars
                currency="XTR",
                prices=prices,
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"Error generando factura de Micrófono VIP: {e}")
            await message.answer(t["err_link"], parse_mode="HTML")


# ==========================================
# ⚡ DESPACHO DE FACTURAS DESDE BOTONES INLINE (inv_)
# ==========================================
@router.callback_query(F.data.startswith("inv_"))
async def process_invoice_callback(callback: CallbackQuery, bot: Bot):
    """Genera y muestra la factura de Telegram Stars cuando el usuario pulsa en el panel inline."""
    await callback.answer()
    lang = get_lang(callback.from_user.language_code)
    t = TEXTS[lang]
    parts = callback.data.split("_")

    if len(parts) >= 3:
        plan = parts[1] 
        chat_id = int(parts[2])

        if chat_id >= 0:
            await callback.message.answer(t["err_link"], parse_mode="HTML")
            return

        if is_clone_bot(bot):
            redirect_text, redirect_markup = _clone_subscription_redirect(lang, plan, chat_id)
            if redirect_text:
                try:
                    await callback.message.answer(redirect_text, reply_markup=redirect_markup, parse_mode="HTML")
                except Exception:
                    pass
                return
            logger.warning("⚠️ [Blindaje Suscripción] MASTER_BOT_USERNAME no resuelto aún; se factura por excepción desde el Clon.")

        if plan == "pro":
            price = PRICE_PRO_STARS
            title = t["inv_pro_t"]
            desc = t["inv_pro_d"]
            payload = f"sub_pro_{chat_id}"
        else:
            price = PRICE_ULTRAPRO_STARS
            title = t["inv_ultra_t"]
            desc = t["inv_ultra_d"]
            payload = f"sub_ultra_{chat_id}"

        prices = [LabeledPrice(label=title, amount=price)]
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({price} XTR)", pay=True)],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
        ])
        
        try:
            await bot.send_invoice(
                chat_id=callback.message.chat.id,
                title=title,
                description=desc,
                payload=payload,
                provider_token="", 
                currency="XTR",
                prices=prices,
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"Error despachando factura mediante callback: {e}")
            await callback.message.answer(t["err_inv"], parse_mode="HTML")


# ==========================================
# 🛡️ VALIDACIÓN DE PRE-CHECKOUT (STARS GATEWAY)
# ==========================================
@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Responde positivamente a la pasarela de Telegram Stars para validar la orden."""
    await pre_checkout_query.answer(ok=True)


# ==========================================
# 💎 PROCESADOR DE PAGO EXITOSO Y ACTIVACIÓN INMEDIATA
# ==========================================
@router.message(F.successful_payment)
async def process_successful_payment(message: Message, bot: Bot):
    """Recibe la confirmación criptográfica de Telegram y eleva la comunidad en tiempo real."""
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    payload = message.successful_payment.invoice_payload

    # CASO 1: SUSCRIPCIONES PRO / ULTRA PRO
    if payload.startswith("sub_"):
        try:
            parts = payload.split("_")
            plan_type = parts[1]
            chat_id = int(parts[2])
            
            tier_db = "pro" if plan_type == "pro" else "ultra_pro"
            
            await approve_group(group_id=chat_id, tier=tier_db, duration_days=30)
            
            if plan_type == "pro":
                confirm_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t["btn_add_master"], url=ASSISTANT_INVITE_URL)],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
                ])
                await message.answer(
                    t["pmt_ok_pro"].format(chat_id=chat_id),
                    parse_mode="HTML",
                    reply_markup=confirm_markup
                )
            else:
                confirm_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t["btn_setup_clone"], callback_data=f"gset_clone_{chat_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
                ])
                await message.answer(
                    t["pmt_ok_ultra"].format(chat_id=chat_id),
                    parse_mode="HTML",
                    reply_markup=confirm_markup
                )
        except Exception as e:
            logger.error(f"Error procesando la entrega de suscripción adquirida: {e}")
            await message.answer(t["err_inv"], parse_mode="HTML")

    # CASO 2: PASES VIP DE MICRÓFONO (24 HORAS)
    elif payload.startswith("vip_mic_"):
        try:
            chat_id = int(payload.split("_")[2])
            user_id = message.from_user.id
            
            await grant_vip_mic(user_id=user_id, group_id=chat_id)
            
            try:
                await set_participant_mic(
                    chat_id=chat_id, 
                    user_id=user_id, 
                    muted=False, 
                    volume=10000
                )
            except Exception as radar_err:
                logger.warning(f"Aviso Centinela al restaurar volumen de pase VIP: {radar_err}")
            
            try:
                badge_title = await get_vip_badge_title(chat_id)

                # 💡 Corregido: se requiere can_manage_chat=True para que Telegram permita asignar un custom_title
                await bot.promote_chat_member(
                    chat_id=chat_id, user_id=user_id,
                    can_manage_chat=True, can_change_info=False, can_delete_messages=False,
                    can_invite_users=False, can_restrict_members=False, can_pin_messages=False,
                    can_promote_members=False, can_manage_video_chats=False
                )
                await bot.set_chat_administrator_custom_title(
                    chat_id=chat_id, user_id=user_id, custom_title=badge_title
                )
            except TelegramBadRequest as admin_err:
                logger.warning(f"Aviso al asignar título VIP (verificar permisos de promoción del bot): {admin_err}")
            except Exception as admin_err:
                logger.warning(f"Error general al asignar título VIP de micrófono: {admin_err}")
            
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])
            await message.answer(t["pmt_vip_ok"], parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logger.error(f"Error procesando la entrega del pase VIP de micrófono: {e}")
            await message.answer(t["err_inv"], parse_mode="HTML")