"""
ecosystem.py — The Bunker OS (Aiogram 3.x)

Módulo de telemetría del ecosistema, workers perimetrales en segundo plano,
auditor de membresías de canales y anunciador programado de videollamadas.
Fase 4: Analítica de Retención Post-Captcha + Sincronización WebSocket (Live Radar).
The Bunker Command OS © 2026 — Cloud Media Management
"""
import os
import sys
import html
import sqlite3
import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, WebAppInfo
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
import database.database as _db_module
from database.database import (
    get_group_tier,
    get_autolower_status,
    get_session_by_group,
    is_group_approved,
    get_all_active_vc_schedules,
    get_due_channel_plan_broadcasts,
    mark_channel_plan_broadcasted,
    disable_channel_plan_broadcast,
    get_expiring_channel_subscriptions,
    get_expired_channel_subscriptions,
    mark_subscription_warned,
    update_subscription_status,
    get_channel_settings,
    get_channel_plans,
    get_channel_live_telemetry,
    get_db_connection,
    get_sentinel_service_messages_config
)

logger = logging.getLogger("ecosystem_handler")
router = Router()

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://thebunkerapp2.netlify.app/")

_BG_TASKS: set = set()


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"❌ [Ecosistema] Tarea {task.get_name()} terminó con error: {exc!r}", exc_info=exc)


def _spawn(coro, name: str = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    task.add_done_callback(_log_task_exception)
    return task


def is_super_admin(user_id: int) -> bool:
    try:
        return int(user_id) in SUPER_ADMIN_IDS
    except (TypeError, ValueError):
        return False


async def _message_author_is_admin(bot: Bot, message: Message) -> bool:
    """Admin real o administrador anónimo del propio grupo (sender_chat == chat)."""
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    if not message.from_user:
        return False
    return await is_operator_admin(bot, message.chat.id, message.from_user.id)


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la respuesta correspondiente."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


def _get_now_time() -> datetime:
    """Retorna la fecha y hora actual en la zona comunitaria configurada."""
    tz_str = os.getenv("BOT_TIMEZONE", "America/Bogota")
    try:
        return datetime.now(ZoneInfo(tz_str))
    except Exception:
        return datetime.now()


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
    """Auto-destrucción dual para mantener el chat grupal limpio y sin contaminación."""
    await asyncio.sleep(delay)
    try:
        await msg1.delete()
    except Exception:
        pass
    try:
        await msg2.delete()
    except Exception:
        pass


def _notify_radar_ws(chat_id: int, event_type: str, data: dict = None):
    """Difusión en tiempo real hacia WebSockets resolviendo dependencias de forma diferida."""
    main_mod = sys.modules.get("main")
    if main_mod:
        fn = getattr(main_mod, "emit_radar_event", None)
        if callable(fn):
            _spawn(fn(chat_id, event_type, data or {}))


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
        "btn_miniapp": "🌐 Mini App Command Center",
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
        "btn_miniapp": "🌐 Abrir Command Center",
        "btn_back_panel": "🔙 Volver al Ecosistema",
        "btn_close_panel": "🗑️ Cerrar Radar",
        "refreshed": "Telemetría actualizada 🔄"
    }
}


def build_radar_markup(chat_id: int, lang: str, in_private: bool = False) -> InlineKeyboardMarkup:
    """Construye el teclado del radar garantizando compatibilidad con grupos (sin WebApp directo)."""
    t = TEXTS.get(lang, TEXTS["es"])
    miniapp_btn = (
        InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))
        if in_private else
        InlineKeyboardButton(text=t["btn_miniapp"], url=f"{WEBAPP_URL}?chat_id={chat_id}")
    )
    rows = [
        [InlineKeyboardButton(text=t["btn_refresh"], callback_data=f"refresh_status_{chat_id}_{lang}_{1 if in_private else 0}")],
        [miniapp_btn]
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
# 📊 FASE 4: CÁLCULO DE RETENCIÓN POST-CAPTCHA
# ==========================================================
def _sync_calculate_retention(group_id: int) -> dict:
    """Calcula la tasa de retención de cohortes a 7 y 30 días desde el padrón local."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # El padrón lo crea groups.py de forma diferida: si aún no existe, se crea vacío.
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS group_members ("
            "group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
            "first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, "
            "PRIMARY KEY (group_id, user_id))"
        )
        conn.commit()

        # 1. Total de miembros registrados en el padrón local
        cursor.execute("SELECT COUNT(*) FROM group_members WHERE group_id = ?", (group_id,))
        total_tracked = cursor.fetchone()[0] or 0

        # 2. Miembros registrados hace más de 7 días
        cursor.execute("""
            SELECT COUNT(*) FROM group_members
            WHERE group_id = ? AND first_seen <= strftime('%s', 'now', '-7 days')
        """, (group_id,))
        cohort_7d = cursor.fetchone()[0] or 0

        # 3. Miembros activos en los últimos 7 días dentro de esa cohorte
        cursor.execute("""
            SELECT COUNT(*) FROM group_members
            WHERE group_id = ?
              AND first_seen <= strftime('%s', 'now', '-7 days')
              AND last_seen >= strftime('%s', 'now', '-7 days')
        """, (group_id,))
        retained_7d = cursor.fetchone()[0] or 0

        # 4. Miembros registrados hace más de 30 días
        cursor.execute("""
            SELECT COUNT(*) FROM group_members
            WHERE group_id = ? AND first_seen <= strftime('%s', 'now', '-30 days')
        """, (group_id,))
        cohort_30d = cursor.fetchone()[0] or 0

        # 5. Miembros activos en los últimos 30 días dentro de esa cohorte
        cursor.execute("""
            SELECT COUNT(*) FROM group_members
            WHERE group_id = ?
              AND first_seen <= strftime('%s', 'now', '-30 days')
              AND last_seen >= strftime('%s', 'now', '-30 days')
        """, (group_id,))
        retained_30d = cursor.fetchone()[0] or 0

        rate_7d = round((retained_7d / max(1, cohort_7d)) * 100, 1) if cohort_7d > 0 else 100.0
        rate_30d = round((retained_30d / max(1, cohort_30d)) * 100, 1) if cohort_30d > 0 else 100.0

        return {
            "total_tracked": total_tracked,
            "cohort_7d": cohort_7d,
            "retained_7d": retained_7d,
            "rate_7d": rate_7d,
            "cohort_30d": cohort_30d,
            "retained_30d": retained_30d,
            "rate_30d": rate_30d
        }


async def calculate_retention_metrics(group_id: int) -> dict:
    return await asyncio.to_thread(_sync_calculate_retention, group_id)


@router.message(Command("retention", "retencion"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_community_retention(message: Message, bot: Bot):
    """Muestra la tasa de retención post-captcha a los 7 y 30 días."""
    group_id = message.chat.id
    if not await _message_author_is_admin(bot, message):
        return

    try:
        data = await calculate_retention_metrics(group_id)
    except sqlite3.Error as db_err:
        logger.error(f"❌ [Retención] No se pudo calcular en {group_id}: {db_err}")
        return

    bar_7d = "█" * int(data["rate_7d"] // 10) + "░" * (10 - int(data["rate_7d"] // 10))
    bar_30d = "█" * int(data["rate_30d"] // 10) + "░" * (10 - int(data["rate_30d"] // 10))

    report = (
        f"📈 <b>Auditoría de Retención Post-Captcha — {html.escape(message.chat.title or 'Comunidad')}</b>\n\n"
        f"• 👥 <b>Miembros en Seguimiento:</b> <code>{data['total_tracked']}</code>\n\n"
        f"<b>Retención a 7 Días:</b>\n"
        f"<code>[{bar_7d}]</code> <b>{data['rate_7d']}%</b>\n"
        f"<i>({data['retained_7d']} activos de {data['cohort_7d']} miembros en cohorte)</i>\n\n"
        f"<b>Retención a 30 Días:</b>\n"
        f"<code>[{bar_30d}]</code> <b>{data['rate_30d']}%</b>\n"
        f"<i>({data['retained_30d']} activos de {data['cohort_30d']} miembros en cohorte)</i>\n\n"
        f"💡 <i>Una retención a 7 días superior al 60% indica una comunidad con alta afinidad orgánica y bajo abandono tras la aduana de seguridad.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Command Center Analítico", url=f"{WEBAPP_URL}?chat_id={group_id}")]
    ])

    sent = await message.answer(report, reply_markup=kb, parse_mode="HTML")
    _spawn(auto_delete_pair(message, sent, delay=45))


# ==========================================================
# 📡 COMANDO PÚBLICO/ADMIN: /radar y /ecosystem
# ==========================================================
@router.message(Command("radar", "ecosystem"))
async def cmd_radar_telemetry(message: Message, bot: Bot):
    """Permite auditar el estado del radar y centinela directamente vía comando."""
    lang = get_lang(message.from_user.language_code if message.from_user else "es")
    t = TEXTS[lang]
    in_private = (message.chat.type == "private")

    if not in_private:
        if not await _message_author_is_admin(bot, message):
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

    if tier == "PRO":
        tier_label = "PRO ⭐"
    elif tier in ("ULTRA_PRO", "ULTRAPRO"):
        tier_label = "ULTRA PRO 💎"
    else:
        tier_label = "BÁSICO (Free)" if lang == "es" else "BASIC (Free)"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO (2%)" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE (2%)" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"] + t["status_body"].format(
        chat_id=chat_id,
        tier=tier_label,
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = build_radar_markup(chat_id, lang, in_private=in_private)
    sent = await message.answer(status_text, reply_markup=keyboard, parse_mode="HTML")

    _notify_radar_ws(chat_id, "telemetry_checked", {"tier": tier_label, "autolower": al_status})

    if not in_private:
        _spawn(auto_delete_pair(message, sent, delay=35))


# ==========================================================
# 🚀 CALLBACKS DE TELEMETRÍA Y RADAR DEL ECOSISTEMA
# ==========================================================
@router.callback_query(F.data.startswith("refresh_status_"))
async def cb_refresh_status(callback: CallbackQuery):
    """Refresca la telemetría en vivo conservando la navegación intacta."""
    data_parts = (callback.data or "").split("_")
    if len(data_parts) < 4:
        await callback.answer()
        return

    try:
        chat_id = int(data_parts[2])
    except ValueError:
        await callback.answer()
        return
    lang = data_parts[3] if data_parts[3] in ["es", "en"] else "es"
    in_private = (data_parts[4] == "1") if len(data_parts) > 4 else (callback.message.chat.type == "private")
    t = TEXTS.get(lang, TEXTS["es"])

    # La telemetría de una comunidad solo la consultan sus administradores.
    if chat_id < 0 and not await is_operator_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t["admin_only"].split("\n")[0].replace("<b>", "").replace("</b>", ""), show_alert=True)
        return

    await callback.answer(t["refreshed"])

    try:
        tier = (await get_group_tier(chat_id) or "FREE").upper()
    except Exception:
        tier = "FREE"

    if tier == "PRO":
        tier_label = "PRO ⭐"
    elif tier in ("ULTRA_PRO", "ULTRAPRO"):
        tier_label = "ULTRA PRO 💎"
    else:
        tier_label = "BÁSICO (Free)" if lang == "es" else "BASIC (Free)"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO (2%)" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE (2%)" if autolower_status == 1 else "🔴 INACTIVE"

    updated_tag = " (Updated)\n\n" if lang == "en" else " (Actualizado)\n\n"
    status_text = t["status_title"].replace("\n\n", updated_tag)
    status_text += t["status_body"].format(
        chat_id=chat_id,
        tier=tier_label,
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = build_radar_markup(chat_id, lang, in_private=in_private)
    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    _notify_radar_ws(chat_id, "telemetry_refreshed", {"tier": tier_label, "autolower": al_status})


@router.callback_query(F.data.startswith("radar_eco_"))
async def cb_open_radar_private(callback: CallbackQuery):
    """Apertura remota del Radar Ecosistema desde el panel de ajustes privado."""
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer()
        return

    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    lang = parts[3] if len(parts) > 3 and parts[3] in ["es", "en"] else get_lang(callback.from_user.language_code)
    t = TEXTS.get(lang, TEXTS["es"])
    if not await is_operator_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t["admin_only"].split("\n")[0].replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    await callback.answer()

    try:
        tier = (await get_group_tier(chat_id) or "FREE").upper()
    except Exception:
        tier = "FREE"

    if tier == "PRO":
        tier_label = "PRO ⭐"
    elif tier in ("ULTRA_PRO", "ULTRAPRO"):
        tier_label = "ULTRA PRO 💎"
    else:
        tier_label = "BÁSICO (Free)" if lang == "es" else "BASIC (Free)"

    try:
        autolower_status = await get_autolower_status(chat_id)
    except Exception:
        autolower_status = 0

    sentinel_label = await get_active_sentinel_label(chat_id, lang)

    al_status = "🟢 ACTIVO (2%)" if autolower_status == 1 else "🔴 INACTIVO"
    if lang == "en":
        al_status = "🟢 ACTIVE (2%)" if autolower_status == 1 else "🔴 INACTIVE"

    status_text = t["status_title"] + t["status_body"].format(
        chat_id=chat_id,
        tier=tier_label,
        autolower=al_status,
        sentinel_name=sentinel_label
    )

    keyboard = build_radar_markup(chat_id, lang, in_private=True)
    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    _notify_radar_ws(chat_id, "telemetry_opened_dm", {"tier": tier_label})


@router.callback_query(F.data == "close_eco_panel")
async def cb_close_panel(callback: CallbackQuery):
    """Cierra el panel temporal en grupo."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


# ==========================================================
# 📢 FASE 4: ANUNCIADOR DE VIDEOLLAMADAS (PRO / ULTRA PRO)
# ==========================================================
def _sync_get_announcement_data(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vc_announcements (
                group_id INTEGER PRIMARY KEY,
                announcement_text TEXT,
                media_id TEXT,
                media_type TEXT,
                minutes_before INTEGER DEFAULT 15,
                last_announced_date TEXT,
                status INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            SELECT announcement_text, media_id, media_type, minutes_before, last_announced_date, status
            FROM vc_announcements WHERE group_id = ?
        """, (group_id,))
        row = cursor.fetchone()
        if row:
            return {
                "text": row[0],
                "media_id": row[1],
                "media_type": row[2],
                "minutes_before": row[3] or 15,
                "last_announced_date": row[4],
                "status": row[5]
            }
        return {"text": None, "media_id": None, "media_type": None, "minutes_before": 15, "last_announced_date": None, "status": 1}


def _sync_mark_vc_announced(group_id: int, date_str: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vc_announcements (group_id, last_announced_date, status)
            VALUES (?, ?, 1)
            ON CONFLICT(group_id) DO UPDATE SET last_announced_date = excluded.last_announced_date
        """, (group_id, date_str))
        conn.commit()


async def start_meeting_announcement_worker(bot: Bot):
    """Worker perimetral en segundo plano para preanuncios de videochats."""
    logger.info("📢 [Meeting Announcer Worker]: Sistema de anuncios previos de VC iniciado.")
    while True:
        try:
            now = _get_now_time()
            today_weekday = str(now.isoweekday())
            today_date_str = now.strftime("%Y-%m-%d")
            current_total_minutes = now.hour * 60 + now.minute

            schedules = await get_all_active_vc_schedules()
            for row in schedules:
                group_id, days_allowed, start_time, end_time, status, call_active = row[0], row[1], row[2], row[3], row[4], row[5]

                if not days_allowed or today_weekday not in [d.strip() for d in days_allowed.split(",")]:
                    continue

                tier = (await get_group_tier(group_id) or "free").lower()
                if tier not in ("pro", "ultra_pro", "ultra"):
                    continue

                sentinel_cfg = await get_sentinel_service_messages_config(group_id)
                if sentinel_cfg.get("sched_enabled", 1) == 0:
                    continue

                ann_cfg = await asyncio.to_thread(_sync_get_announcement_data, group_id)
                if ann_cfg.get("status") == 0:
                    continue

                if ann_cfg.get("last_announced_date") == today_date_str:
                    continue

                try:
                    start_h, start_m = (int(x) for x in str(start_time).strip().split(":")[:2])
                    start_total_minutes = start_h * 60 + start_m
                except Exception:
                    continue

                try:
                    mins_before = max(1, min(240, int(ann_cfg.get("minutes_before", 15) or 15)))
                except (TypeError, ValueError):
                    mins_before = 15
                diff_minutes = start_total_minutes - current_total_minutes

                if 0 <= diff_minutes <= mins_before and call_active == 0:
                    custom_text = sentinel_cfg.get("sched_start_text") or ann_cfg.get("text")
                    media_id = sentinel_cfg.get("sched_start_media_id") or ann_cfg.get("media_id")
                    media_type = sentinel_cfg.get("sched_start_media_type") or ann_cfg.get("media_type")
                    try:
                        autodel_secs = int(sentinel_cfg.get("sched_start_autodel", 0) or 0)
                    except (TypeError, ValueError):
                        autodel_secs = 0

                    try:
                        chat_info = await bot.get_chat(group_id)
                        chat_title = chat_info.title or "la comunidad"
                        chat_user = getattr(chat_info, "username", None)
                    except Exception:
                        chat_title = "la comunidad"
                        chat_user = None

                    default_body = (
                        f"📡 <b>Próxima Reunión / Live en Vivo — {html.escape(chat_title)}</b>\n\n"
                        f"⏰ La sala de videochat dará inicio en aproximadamente <b>{diff_minutes} minutos</b>.\n\n"
                        "• 🎙️ <i>Prepara tu micrófono y verifica tu conexión.</i>\n"
                        "• 🔇 <i>Por directiva perimetral, los micrófonos estarán al 2% para participantes no verificados.</i>\n"
                        "• 💎 <i>Los miembros con pase MicVIP conservarán voz prioritaria al 100%.</i>\n\n"
                        "🛡️ <i>Cloud Media Management</i>"
                    )

                    body = custom_text if custom_text else default_body

                    join_btn = (
                        InlineKeyboardButton(text="🎙️ Abrir Sala / Videochat", url=f"https://t.me/{chat_user}")
                        if chat_user else
                        InlineKeyboardButton(text="🌐 Command Center", url=f"{WEBAPP_URL}?chat_id={group_id}")
                    )
                    markup = InlineKeyboardMarkup(inline_keyboard=[[join_btn]])

                    sent_msg = None
                    try:
                        if media_id and media_type == "photo":
                            sent_msg = await bot.send_photo(chat_id=group_id, photo=media_id, caption=body, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "video":
                            sent_msg = await bot.send_video(chat_id=group_id, video=media_id, caption=body, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "animation":
                            sent_msg = await bot.send_animation(chat_id=group_id, animation=media_id, caption=body, reply_markup=markup, parse_mode="HTML")
                        else:
                            sent_msg = await bot.send_message(chat_id=group_id, text=body, reply_markup=markup, parse_mode="HTML")

                        await asyncio.to_thread(_sync_mark_vc_announced, group_id, today_date_str)
                        logger.info(f"📢 [Meeting Announcer] Aviso de VC despachado en {group_id} ({diff_minutes}m antes).")
                        _notify_radar_ws(group_id, "meeting_announced", {"diff_minutes": diff_minutes})

                        if sent_msg and autodel_secs > 0:
                            async def _del_ann(msg_to_del, delay):
                                await asyncio.sleep(delay)
                                try:
                                    await msg_to_del.delete()
                                except Exception:
                                    pass
                            _spawn(_del_ann(sent_msg, autodel_secs))

                    except (TelegramForbiddenError, TelegramBadRequest) as p_err:
                        logger.warning(f"Aviso al despachar anuncio de reunión en {group_id}: {p_err}")
                    except TelegramRetryAfter as retry_err:
                        await asyncio.sleep(retry_err.retry_after + 1)

        except Exception as ex:
            logger.error(f"❌ [Meeting Announcer Error]: {ex}")

        await asyncio.sleep(45)


# ==========================================================
# 👁️ WATCHDOG VIP EN SEGUNDO PLANO (AUTO-KICK & RENOVACIÓN)
# ==========================================================
async def start_subscription_watchdog_worker(bot: Bot):
    """Supervisa vencimientos de membresías VIP en canales con alertas y Auto-Kick."""
    logger.info("👁️️ [Subscription Watchdog]: Auditor de membresías VIP y auto-kick iniciado.")
    while True:
        try:
            # 1. Alertas de renovación preventivas
            expiring = await get_expiring_channel_subscriptions(hours_ahead=48)
            if expiring:
                bot_info = await bot.get_me()
                bot_username = bot_info.username or "thebunkerapp_bot"
                for ch_id, u_id, exp_at, stars_paid, grace_days in expiring:
                    try:
                        plans = await get_channel_plans(ch_id, only_active=True)
                        if plans:
                            first_plan = plans[0]
                            plan_id = first_plan[0]
                            renew_url = f"https://t.me/{bot_username}?start=chanplan_{plan_id}_{ch_id}"
                        else:
                            renew_url = f"https://t.me/{bot_username}?start=cset_{ch_id}"

                        warn_text = (
                            "⚠️ <b>Aviso de Renovación VIP — The Bunker Command OS</b>\n\n"
                            f"Tu acceso al canal VIP expira el: <code>{exp_at}</code>.\n"
                            f"Dispones de <b>{grace_days} días</b> de gracia antes del retiro automático de acceso.\n\n"
                            "Renueva tu membresía con Telegram Stars para mantener tu acceso sin interrupciones.\n\n"
                            "🛡️ <i>Cloud Media Management</i>"
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⭐ Renovar Acceso VIP", url=renew_url)]
                        ])
                        await bot.send_message(chat_id=u_id, text=warn_text, reply_markup=kb, parse_mode="HTML")
                        await mark_subscription_warned(ch_id, u_id)
                        logger.info(f"📢 [Watchdog] Alerta enviada a usuario {u_id} (Canal {ch_id}).")
                    except (TelegramForbiddenError, TelegramBadRequest):
                        await mark_subscription_warned(ch_id, u_id)
                    except TelegramRetryAfter as rate_err:
                        await asyncio.sleep(rate_err.retry_after + 1)
                    except Exception as warn_err:
                        logger.warning(f"⚠️ [Watchdog] Fallo al alertar usuario {u_id}: {warn_err}")
                        await mark_subscription_warned(ch_id, u_id)

            # 2. Expulsión automática (Auto-Kick)
            expired = await get_expired_channel_subscriptions()
            if expired:
                for ch_id, u_id, exp_at, grace_days, auto_kick in expired:
                    if auto_kick:
                        try:
                            await bot.ban_chat_member(chat_id=ch_id, user_id=u_id)
                            await bot.unban_chat_member(chat_id=ch_id, user_id=u_id, only_if_banned=True)
                            await update_subscription_status(ch_id, u_id, "kicked")
                            logger.info(f"🚫 [Watchdog Auto-Kick]: Usuario {u_id} removido del canal {ch_id} por membresía expirada.")

                            try:
                                kick_msg = (
                                    "🔒 <b>Acceso VIP Finalizado</b>\n\n"
                                    "Tu período de suscripción y los días de gracia han concluido. "
                                    "Has sido removido del canal VIP. Puedes reactivar tu pase adquiriendo un plan en cualquier momento.\n\n"
                                    "🛡️ <i>Cloud Media Management</i>"
                                )
                                await bot.send_message(chat_id=u_id, text=kick_msg, parse_mode="HTML")
                            except Exception:
                                pass

                            _notify_radar_ws(ch_id, "member_kicked_expired", {"user_id": u_id})

                        except TelegramRetryAfter as rate_err:
                            await asyncio.sleep(rate_err.retry_after + 1)
                        except (TelegramBadRequest, TelegramForbiddenError) as kick_err:
                            # Error permanente (bot sin permisos, usuario ya fuera, canal borrado):
                            # se marca vencida para no reintentar cada minuto indefinidamente.
                            logger.warning(f"⚠️ [Watchdog Auto-Kick] No se pudo remover a {u_id} de {ch_id} ({kick_err}); marcada como expirada.")
                            await update_subscription_status(ch_id, u_id, "expired")
                        except Exception as kick_err:
                            logger.error(f"❌ [Watchdog Auto-Kick Error] Fallo al remover usuario {u_id} en canal {ch_id}: {kick_err}")
                    else:
                        await update_subscription_status(ch_id, u_id, "expired")

        except Exception as ex:
            logger.error(f"❌ [Subscription Watchdog Error]: {ex}")

        await asyncio.sleep(60)


# ==========================================================
# 🎙️ EXPIRACIÓN DE INSIGNIAS VIP (REVERSIÓN DE PROMOCIÓN TEMPORAL)
# ==========================================================
_VIP_BADGE_DDL = (
    "CREATE TABLE IF NOT EXISTS vip_badge_promotions ("
    "group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, user_id))"
)


def _sync_get_expired_vip_badges() -> list:
    with get_db_connection() as conn:
        conn.execute(_VIP_BADGE_DDL)
        conn.commit()
        return conn.execute("""
            SELECT b.group_id, b.user_id FROM vip_badge_promotions b
            LEFT JOIN vip_mic_passes v ON v.group_id = b.group_id AND v.user_id = b.user_id
            WHERE v.expires_at IS NULL OR v.expires_at <= datetime('now')
        """).fetchall()


def _sync_forget_vip_badge(group_id: int, user_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM vip_badge_promotions WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        conn.commit()


_ELEVATED_RIGHTS = (
    "can_delete_messages", "can_restrict_members", "can_promote_members", "can_change_info",
    "can_invite_users", "can_pin_messages", "can_manage_video_chats", "can_post_messages",
    "can_edit_messages", "can_manage_topics"
)


async def start_vip_badge_expiry_worker(bot: Bot):
    """
    payments.py promueve al comprador de un pase MicVIP como administrador sin permisos para
    mostrar su título personalizado, pero nada revertía esa promoción: al vencer las 24 h el
    usuario seguía siendo "admin" (y por tanto inmune a todos los filtros del grupo).
    Este worker la revierte, salvo que el dueño le haya concedido después permisos reales.
    """
    logger.info("🎙️ [VIP Badge Watchdog]: Reversión automática de insignias MicVIP iniciada.")
    while True:
        try:
            expired = await asyncio.to_thread(_sync_get_expired_vip_badges)
            for group_id, user_id in expired:
                try:
                    member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
                    if member.status == "administrator" and not any(getattr(member, r, False) for r in _ELEVATED_RIGHTS):
                        await bot.promote_chat_member(
                            chat_id=group_id, user_id=user_id,
                            can_manage_chat=False, can_change_info=False, can_delete_messages=False,
                            can_invite_users=False, can_restrict_members=False, can_pin_messages=False,
                            can_promote_members=False, can_manage_video_chats=False
                        )
                        logger.info(f"🎙️ [VIP Badge] Insignia temporal retirada a {user_id} en {group_id}.")
                    await asyncio.to_thread(_sync_forget_vip_badge, group_id, user_id)
                except TelegramRetryAfter as rate_err:
                    await asyncio.sleep(rate_err.retry_after + 1)
                except (TelegramBadRequest, TelegramForbiddenError) as perm_err:
                    logger.warning(f"⚠️ [VIP Badge] No se pudo revertir la insignia de {user_id} en {group_id}: {perm_err}")
                    await asyncio.to_thread(_sync_forget_vip_badge, group_id, user_id)
                except Exception as badge_err:
                    logger.debug(f"Aviso revirtiendo insignia VIP ({group_id}/{user_id}): {badge_err}")
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [VIP Badge Watchdog Error]: {ex}")
        await asyncio.sleep(300)


# ==========================================
# 📡 BACKGROUND WORKER: DIFUSIÓN RECURRENTE DE PLANES
# ==========================================
async def start_channel_broadcast_worker(bot: Bot):
    """Worker perimetral en segundo plano para difusión recurrente de planes en canales."""
    child_workers = [
        _spawn(start_subscription_watchdog_worker(bot), name="subscription_watchdog"),
        _spawn(start_meeting_announcement_worker(bot), name="meeting_announcer"),
        _spawn(start_vip_badge_expiry_worker(bot), name="vip_badge_watchdog"),
    ]
    logger.info("📡 [Broadcast Worker]: Bucle de difusión recurrente de planes iniciado.")
    try:
        await _channel_broadcast_loop(bot)
    finally:
        # main.py cancela este worker al apagar: los sub-workers se detienen con él.
        for child in child_workers:
            child.cancel()


async def _channel_broadcast_loop(bot: Bot):
    while True:
        try:
            due_plans = await get_due_channel_plan_broadcasts()
            if due_plans:
                bot_info = await bot.get_me()
                bot_username = bot_info.username or "thebunkerapp_bot"

                for plan in due_plans:
                    plan_id = plan["plan_id"]
                    raw_chat_id = plan["broadcast_chat_id"]
                    plan_name = plan["plan_name"]
                    duration_days = plan["duration_days"]
                    stars_price = plan["stars_price"]
                    promo_text = plan["promo_text"] or ""
                    media_id = plan["media_id"]
                    media_type = plan["media_type"]
                    target_link = plan["target_link"]
                    channel_id = plan["channel_id"]

                    target_chat = raw_chat_id
                    if isinstance(raw_chat_id, str) and (raw_chat_id.startswith("-") or raw_chat_id.isdigit()):
                        try:
                            target_chat = int(raw_chat_id)
                        except ValueError:
                            pass

                    pay_link = f"https://t.me/{bot_username}?start=chanplan_{plan_id}_{channel_id}"

                    caption = promo_text.strip() if promo_text and promo_text.strip() else f"💎 <b>{html.escape(str(plan_name))}</b>\n\n⏳ {duration_days} días — ⭐ {stars_price} XTR\n\n🛡️ <i>Cloud Media Management</i>"

                    kb_rows = [
                        [InlineKeyboardButton(text=f"⭐ Adquirir por {stars_price} Stars", url=pay_link)]
                    ]

                    if target_link:
                        target_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
                        kb_rows.append([InlineKeyboardButton(text="🔗 Ver Recurso / Canal VIP", url=target_url)])

                    markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)

                    try:
                        if media_id and media_type == "photo":
                            await bot.send_photo(chat_id=target_chat, photo=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "video":
                            await bot.send_video(chat_id=target_chat, video=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        elif media_id and media_type == "animation":
                            await bot.send_animation(chat_id=target_chat, animation=media_id, caption=caption, reply_markup=markup, parse_mode="HTML")
                        else:
                            await bot.send_message(chat_id=target_chat, text=caption, reply_markup=markup, parse_mode="HTML")

                        await mark_channel_plan_broadcasted(plan_id)
                        logger.info(f"✅ [Broadcast Worker] Plan {plan_id} difundido exitosamente en chat {target_chat}.")
                    except (TelegramForbiddenError, TelegramBadRequest) as perm_err:
                        logger.warning(f"⚠️️ [Broadcast Worker] Permiso denegado en chat {target_chat}. Difusión pausada para plan {plan_id}: {perm_err}")
                        await disable_channel_plan_broadcast(plan_id)
                    except TelegramRetryAfter as rate_err:
                        await asyncio.sleep(rate_err.retry_after + 1)
                    except Exception as send_err:
                        logger.error(f"❌ [Broadcast Worker] Error publicando plan {plan_id} en chat {target_chat}: {send_err}")

        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [Broadcast Worker Error]: {ex}")

        await asyncio.sleep(60)