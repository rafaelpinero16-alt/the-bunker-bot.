import os
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from database.database import (
    get_group_tier, 
    get_autolower_status, 
    get_session_by_group,
    is_group_approved,
    get_due_channel_plan_broadcasts,
    mark_channel_plan_broadcasted,
    get_expiring_channel_subscriptions,
    get_expired_channel_subscriptions,
    mark_subscription_warned,
    update_subscription_status,
    get_channel_settings,
    get_channel_plans,
    get_channel_live_telemetry
)

logger = logging.getLogger("ecosystem_handler")
router = Router()

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la respuesta correspondiente."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


async def is_operator_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Valida si el ejecutor cuenta con rango administrativo o inmunidad de Arquitecto."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


async def get_active_sentinel_label(group_id: int, lang: str = "es") -> str:
    """Detecta si la comunidad usa un centinela propio ULTRA PRO o el maestro."""
    try:
        session_data = await get_session_by_group(group_id)
        if session_data:
            return "Centinela Dedicado Propio 💎" if lang == "es" else "Dedicated Sentinel 💎"
    except Exception:
        pass
    return "Centinela Maestro (@Alphacentinel) 🤖" if lang == "es" else "Master Sentinel (@Alphacentinel) 🤖"


async def auto_delete_pair(msg1: Message, msg2: Message, delay: int = 30):
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


# ==========================================================
# 🌐 DICCIONARIO BILINGÜE DEL ECOSISTEMA Y NAVEGACIÓN
# ==========================================================
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
        "admin_only": "⛔ <b>Access Denied:</b> Only community administrators can view radar telemetry.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_refresh": "🔄 Refresh Telemetry",
        "btn_back_panel": "🔙 Back to Ecosystem",
        "btn_close_panel": "🗑️ Close Radar",
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
        "admin_only": "⛔ <b>Acceso Denegado:</b> Solo los administradores pueden consultar la telemetría del radar.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_refresh": "🔄 Refrescar Telemetría",
        "btn_back_panel": "🔙 Volver al Ecosistema",
        "btn_close_panel": "🗑️ Cerrar Radar",
        "refreshed": "Telemetría actualizada 🔄"
    }
}


def build_radar_markup(chat_id: int, lang: str, in_private: bool = False) -> InlineKeyboardMarkup:
    """Construye el teclado del radar garantizando botones de retorno y actualización."""
    t = TEXTS.get(lang, TEXTS["es"])
    rows = [
        [InlineKeyboardButton(text=t["btn_refresh"], callback_data=f"refresh_status_{chat_id}_{lang}_{1 if in_private else 0}")]
    ]
    if in_private:
        rows.append([
            InlineKeyboardButton(text=t["btn_back_panel"], callback_data=f"menu_eco_{chat_id}_{lang}")
        ])
    else:
        rows.append([
            InlineKeyboardButton(text=t["btn_close_panel"], callback_data="close_eco_panel")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================================
# 📡 COMANDO PÚBLICO/ADMIN: /radar y /ecosystem
# ==========================================================
@router.message(Command("radar", "ecosystem"))
async def cmd_radar_telemetry(message: Message, bot: Bot):
    """Permite auditar el estado del radar y centinela directamente vía comando."""
    lang = get_lang(message.from_user.language_code)
    t = TEXTS.get(lang, TEXTS["es"])

    if message.chat.type != "private":
        if not await is_operator_admin(bot, message.chat.id, message.from_user.id):
            try:
                await message.delete()
            except Exception:
                pass
            return

    chat_id = message.chat.id
    try:
        tier = (await get_group_tier(chat_id) or "FREE").upper()
    except Exception:
        tier = "FREE"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"] + t["status_body"].format(
        chat_id=chat_id,
        tier=tier,
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    in_private = (message.chat.type == "private")
    keyboard = build_radar_markup(chat_id, lang, in_private=in_private)
    sent = await message.answer(status_text, reply_markup=keyboard, parse_mode="HTML")

    if not in_private:
        asyncio.create_task(auto_delete_pair(message, sent, delay=35))


# ==========================================================
# 🚀 CALLBACKS DE TELEMETRÍA Y RADAR DEL ECOSISTEMA
# ==========================================================
@router.callback_query(F.data.startswith("refresh_status_"))
async def cb_refresh_status(callback: CallbackQuery):
    """Refresca la telemetría conservando la navegación intacta."""
    data_parts = callback.data.split("_")
    if len(data_parts) < 4:
        await callback.answer()
        return

    chat_id = int(data_parts[2])
    lang = data_parts[3] if data_parts[3] in ["es", "en"] else "es"
    in_private = (int(data_parts[4]) == 1) if len(data_parts) > 4 else (callback.message.chat.type == "private")
    t = TEXTS.get(lang, TEXTS["es"])

    await callback.answer(t["refreshed"])

    try:
        tier = (await get_group_tier(chat_id) or "FREE").upper()
    except Exception:
        tier = "FREE"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE" if autolower_status == 1 else "🔴 INACTIVE"

    updated_tag = " (Updated)\n\n" if lang == "en" else " (Actualizado)\n\n"
    status_text = t["status_title"].replace("\n\n", updated_tag)
    status_text += t["status_body"].format(
        chat_id=chat_id, 
        tier=tier, 
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = build_radar_markup(chat_id, lang, in_private=in_private)
    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("radar_eco_"))
async def cb_open_radar_private(callback: CallbackQuery):
    """Apertura remota del Radar Ecosistema desde el panel de ajustes privado."""
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer()
        return

    chat_id = int(parts[2])
    lang = parts[3] if len(parts) > 3 and parts[3] in ["es", "en"] else get_lang(callback.from_user.language_code)
    t = TEXTS.get(lang, TEXTS["es"])
    await callback.answer()

    try:
        tier = (await get_group_tier(chat_id) or "FREE").upper()
    except Exception:
        tier = "FREE"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"] + t["status_body"].format(
        chat_id=chat_id, 
        tier=tier, 
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = build_radar_markup(chat_id, lang, in_private=True)
    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "close_eco_panel")
async def cb_close_panel(callback: CallbackQuery):
    """Cierra el panel temporal en grupo."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


# ==========================================================
# 👁️ WATCHDOG VIP EN SEGUNDO PLANO (FASE 3: AUTO-KICK & RENOVACIÓN)
# ==========================================================
async def start_subscription_watchdog_worker(bot: Bot):
    """
    Supervisa continuamente los vencimientos de membresías VIP en canales:
    1. Alerta de renovación a miembros con 48h de anticipación.
    2. Aplica Auto-Kick y revocación tras vencer los días de gracia configurados.
    """
    logging.info("👁️ [Subscription Watchdog]: Auditor de membresías VIP y auto-kick iniciado.")
    while True:
        try:
            # 1. Alertas de renovación (próximos a vencer)
            expiring = await get_expiring_channel_subscriptions(hours_ahead=48)
            if expiring:
                bot_info = await bot.get_me()
                bot_username = bot_info.username or "thebunkerapp_bot"
                for ch_id, u_id, exp_at, stars_paid, grace_days in expiring:
                    try:
                        warn_text = (
                            "⚠️ <b>Aviso de Renovación VIP — The Bunker Command OS</b>\n\n"
                            f"Tu acceso al canal VIP expira el: <code>{exp_at}</code>.\n"
                            f"Dispones de <b>{grace_days} días</b> de gracia antes del Auto-Kick automático.\n\n"
                            "Renueva tu membresía con Telegram Stars para mantener tu acceso sin interrupciones."
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⭐ Renovar Acceso VIP", url=f"https://t.me/{bot_username}?start=sub_pro")]
                        ])
                        await bot.send_message(chat_id=u_id, text=warn_text, reply_markup=kb, parse_mode="HTML")
                        await mark_subscription_warned(ch_id, u_id)
                        logging.info(f"📢 [Watchdog] Alerta enviada a usuario {u_id} (Canal {ch_id}).")
                    except Exception as warn_err:
                        logging.warning(f"⚠️ [Watchdog] Fallo al alertar usuario {u_id}: {warn_err}")
                        await mark_subscription_warned(ch_id, u_id)

            # 2. Expulsión automática (Auto-Kick) al agotarse la gracia
            expired = await get_expired_channel_subscriptions()
            if expired:
                for ch_id, u_id, exp_at, grace_days, auto_kick in expired:
                    if auto_kick:
                        try:
                            # Expulsar y desbanear de inmediato para permitir reincorporación tras pago
                            await bot.ban_chat_member(chat_id=ch_id, user_id=u_id)
                            await bot.unban_chat_member(chat_id=ch_id, user_id=u_id)
                            await update_subscription_status(ch_id, u_id, "kicked")
                            logging.info(f"🚫 [Watchdog Auto-Kick]: Usuario {u_id} removido del canal {ch_id} por membresía expirada.")
                            
                            try:
                                kick_msg = (
                                    "🔒 <b>Acceso VIP Finalizado</b>\n\n"
                                    "Tu período de suscripción y los días de gracia han concluido. "
                                    "Has sido removido del canal VIP. Puedes reactivar tu pase adquiriendo un plan en cualquier momento."
                                )
                                await bot.send_message(chat_id=u_id, text=kick_msg, parse_mode="HTML")
                            except Exception:
                                pass
                        except Exception as kick_err:
                            logging.error(f"❌ [Watchdog Auto-Kick Error] Fallo al remover usuario {u_id} en canal {ch_id}: {kick_err}")
                    else:
                        await update_subscription_status(ch_id, u_id, "expired")

        except Exception as ex:
            logging.error(f"❌ [Subscription Watchdog Error]: {ex}")
        
        await asyncio.sleep(60)


# ==========================================
# 📡 BACKGROUND WORKER: DIFUSIÓN RECURRENTE DE PLANES
# ==========================================
async def start_channel_broadcast_worker(bot: Bot):
    """
    Worker perimetral en segundo plano: evalúa continuamente los planes de membresía 
    con difusión recurrente activa y publica los anuncios de pago con Telegram Stars.
    También inicializa concurrentemente el Watchdog de suscripciones VIP.
    """
    # Despliegue concurrente del Watchdog VIP de membresías
    asyncio.create_task(start_subscription_watchdog_worker(bot))
    logging.info("📡 [Broadcast Worker]: Bucle de difusión recurrente de planes iniciado.")
    
    while True:
        try:
            due_plans = await get_due_channel_plan_broadcasts()
            if due_plans:
                bot_info = await bot.get_me()
                bot_username = bot_info.username or "thebunkerapp_bot"

                for plan in due_plans:
                    plan_id = plan["plan_id"]
                    chat_id = plan["broadcast_chat_id"]
                    plan_name = plan["plan_name"]
                    duration_days = plan["duration_days"]
                    stars_price = plan["stars_price"]
                    promo_text = plan["promo_text"] or ""
                    media_id = plan["media_id"]
                    media_type = plan["media_type"]
                    target_link = plan["target_link"]
                    channel_id = plan["channel_id"]

                    pay_link = f"https://t.me/{bot_username}?start=chanplan_{plan_id}_{channel_id}"

                    caption = promo_text.strip() if promo_text else f"💎 <b>{plan_name}</b>\n\n⏳ {duration_days} días — ⭐ {stars_price} XTR"
                    if target_link:
                        caption += f"\n\n🔗 <b>Destino VIP:</b> <code>{target_link}</code>"

                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"⭐ Adquirir por {stars_price} Stars", url=pay_link)]
                    ])

                    try:
                        if media_id and media_type == "photo":
                            await bot.send_photo(chat_id=chat_id, photo=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "video":
                            await bot.send_video(chat_id=chat_id, video=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "animation":
                            await bot.send_animation(chat_id=chat_id, animation=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        else:
                            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup, parse_mode="HTML")
                        
                        await mark_channel_plan_broadcasted(plan_id)
                        logging.info(f"✅ [Broadcast Worker] Plan {plan_id} difundido exitosamente en chat {chat_id}.")
                    except Exception as send_err:
                        logging.error(f"❌ [Broadcast Worker] Error publicando plan {plan_id} en chat {chat_id}: {send_err}")

        except Exception as ex:
            logging.error(f"❌ [Broadcast Worker Error]: {ex}")
        
        await asyncio.sleep(60)