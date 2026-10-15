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
import re
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
    register_channel_plan_broadcast_failure,
    get_due_log_reports,
    mark_log_report_result,
    get_log_report_config,
    get_admin_performance,
    get_expiring_channel_subscriptions,
    get_expired_channel_subscriptions,
    mark_subscription_warned,
    update_subscription_status,
    get_channel_settings,
    get_channel_plans,
    get_channel_live_telemetry,
    get_db_connection,
    get_sentinel_service_messages_config,
    get_due_gamification_announcements,
    mark_gamification_announcement_sent,
    reschedule_gamification_announcement,
    register_gamification_announcement_failure,
    compose_gamification_card
)
import raid_shield

try:
    from telegram_html import normalize_telegram_html, visible_length  # misma gramática que la vista previa
except ImportError:  # pragma: no cover - despliegues mínimos
    def normalize_telegram_html(text: str) -> str:
        return html.escape(text or "")

    def visible_length(text: str) -> int:
        return len(re.sub(r"<[^>]+>", "", text or ""))

logger = logging.getLogger("ecosystem_handler")
router = Router()

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://the-bunker-bot-bunkerminiapp.vercel.app/").strip()

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

                # 🛡️ Aislamiento anti-raid: nada de anuncios públicos mientras el escudo esté activo.
                if raid_shield.is_isolated(group_id):
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


# ==========================================================
# 🎮 DIFUSIÓN RECURRENTE: ANUNCIOS DE GAMIFICACIÓN INTERACTIVA
# ==========================================================
GAMI_BATCH_SIZE = 20                 # Tarjetas máximas por ciclo del Broadcast Worker (60 s)
GAMI_SEND_SPACING_SECONDS = 0.5      # Separación entre envíos (muy por debajo del límite global de 30 msg/s)
GAMI_MIN_INTERVAL_BY_RANK = {1: 180, 2: 30}   # PRO: cada ≥3 h | ULTRA PRO: cada ≥30 min
_GAMI_PERMANENT_ERROR_MARKERS = (
    "chat not found", "bot was kicked", "bot is not a member", "not enough rights",
    "have no rights", "need administrator rights", "chat_write_forbidden", "chat_admin_required",
    "user is deactivated", "group chat was upgraded", "chat was deleted", "channel_private",
    "bot was blocked", "peer_id_invalid",
)
_GAMI_PARSE_ERROR_MARKERS = ("can't parse entities", "unsupported start tag", "can't find end", "unmatched end tag")
_GAMI_TAG_RE = re.compile(r"<[^>]+>")


def _gami_tier_rank(tier) -> int:
    normalized = str(tier or "free").strip().lower().replace("-", "_").replace(" ", "_")
    if "ultra" in normalized or normalized in ("enterprise", "elite", "unlimited"):
        return 2
    if normalized == "pro":
        return 1
    return 0


def _gami_plain_text(text: str) -> str:
    """Versión sin etiquetas HTML (respaldo si el formato del administrador es inválido)."""
    return html.unescape(_GAMI_TAG_RE.sub("", text or ""))


def build_gamification_markup(card: dict) -> InlineKeyboardMarkup | None:
    if card.get("button_url") and card.get("button_text"):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=card["button_text"], url=card["button_url"])
        ]])
    return None


async def send_gamification_card(bot: Bot, chat_id: int, card: dict, parse_mode: str | None = "HTML") -> list:
    """
    Publica la tarjeta (multimedia + texto + botón inline). Devuelve los mensajes enviados.
    • Si el texto cabe en el caption (≤1024), va dentro del multimedia.
    • Si no cabe, se publica el multimedia y a continuación el texto con el botón.
    • Si el HTML del administrador es inválido, ese mismo paso se reintenta en texto plano
      (sin duplicar el multimedia ya publicado).
    """
    markup = build_gamification_markup(card)
    text = card.get("text") or ""
    media_id = card.get("media_id")
    media_type = card.get("media_type")
    senders = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation}

    def _is_parse_error(err: Exception) -> bool:
        return bool(parse_mode) and any(marker in str(err).lower() for marker in _GAMI_PARSE_ERROR_MARKERS)

    sent: list = []
    if media_id and media_type in senders:
        send_media = senders[media_type]
        media_kwargs = {media_type: media_id}
        if len(text) <= 1024:
            try:
                sent.append(await send_media(chat_id=chat_id, caption=text, parse_mode=parse_mode, reply_markup=markup, **media_kwargs))
            except TelegramBadRequest as err:
                if not _is_parse_error(err):
                    raise
                logger.warning(f"⚠️ [Gamificación] HTML inválido en {chat_id}; caption reenviado como texto plano.")
                sent.append(await send_media(chat_id=chat_id, caption=_gami_plain_text(text)[:1024], parse_mode=None, reply_markup=markup, **media_kwargs))
            return sent
        sent.append(await send_media(chat_id=chat_id, **media_kwargs))

    try:
        sent.append(await bot.send_message(chat_id=chat_id, text=text[:4096], parse_mode=parse_mode, reply_markup=markup))
    except TelegramBadRequest as err:
        if not _is_parse_error(err):
            raise
        logger.warning(f"⚠️ [Gamificación] HTML inválido en {chat_id}; texto reenviado como texto plano.")
        sent.append(await bot.send_message(chat_id=chat_id, text=_gami_plain_text(text)[:4096], parse_mode=None, reply_markup=markup))
    return sent


async def _gami_auto_delete(messages: list, delay: int):
    await asyncio.sleep(delay)
    for msg in messages:
        try:
            await msg.delete()
        except Exception:
            pass


async def _dispatch_gamification_announcements(bot: Bot) -> int:
    """
    Evalúa y publica los anuncios de gamificación vencidos. Se ejecuta en cada ciclo del
    Broadcast Worker. Respeta la licencia vigente (PRO ≥3 h, ULTRA PRO ≥30 min; el multimedia
    es exclusivo de ULTRA PRO), espacia los envíos y obedece los FloodWait de Telegram.
    Devuelve cuántas tarjetas se publicaron.
    """
    due = await get_due_gamification_announcements(limit=GAMI_BATCH_SIZE)
    if not due:
        return 0

    published = 0
    for index, cfg in enumerate(due):
        chat_id = cfg["chat_id"]
        if raid_shield.is_isolated(chat_id):
            # Aislamiento anti-raid: se aplaza sin contar como fallo.
            await reschedule_gamification_announcement(chat_id, 15)
            continue
        try:
            rank = _gami_tier_rank(await get_group_tier(chat_id))
        except Exception:
            rank = 0
        if rank < 1:
            # Licencia vencida o plan gratuito: se pausa sin perder la configuración.
            await reschedule_gamification_announcement(chat_id, 360)
            logger.info(f"⏸️ [Gamificación] {chat_id} sin licencia PRO/ULTRA PRO vigente; envío aplazado 6 h.")
            continue

        interval = max(GAMI_MIN_INTERVAL_BY_RANK.get(rank, 180), int(cfg.get("interval_minutes") or 360))
        card = compose_gamification_card(cfg)
        if rank < 2:
            card["media_id"], card["media_type"] = None, None

        try:
            sent_messages = await send_gamification_card(bot, chat_id, card)
        except TelegramRetryAfter as rate_err:
            wait_s = int(getattr(rate_err, "retry_after", 5) or 5)
            logger.warning(f"⏳ [Gamificación] FloodWait {wait_s}s en {chat_id}; se reprograma el envío.")
            await reschedule_gamification_announcement(chat_id, max(2, wait_s // 60 + 1))
            await asyncio.sleep(min(wait_s + 1, 60))
            continue
        except (TelegramForbiddenError, TelegramBadRequest) as send_err:
            err_text = str(send_err).lower()
            permanent = isinstance(send_err, TelegramForbiddenError) or any(m in err_text for m in _GAMI_PERMANENT_ERROR_MARKERS)
            disabled = await register_gamification_announcement_failure(chat_id, permanent=permanent)
            logger.warning(
                f"⚠️ [Gamificación] Envío fallido en {chat_id}: {send_err}"
                + (" — anuncio desactivado." if disabled else " — se reintentará más tarde.")
            )
            if disabled:
                _notify_radar_ws(chat_id, "gamification_disabled", {"reason": str(send_err)[:200]})
            continue
        except Exception as send_err:
            await register_gamification_announcement_failure(chat_id, permanent=False)
            logger.error(f"❌ [Gamificación] Error inesperado publicando en {chat_id}: {send_err}")
            continue

        last_id = sent_messages[-1].message_id if sent_messages else None
        await mark_gamification_announcement_sent(chat_id, last_id, interval)
        published += 1
        logger.info(f"🎮 [Gamificación] Tarjeta publicada en {chat_id}; próxima en {interval} min.")
        _notify_radar_ws(chat_id, "gamification_announced", {"next_in_minutes": interval})

        try:
            autodel = int(cfg.get("auto_delete_after") or 0)
        except (TypeError, ValueError):
            autodel = 0
        if autodel > 0 and sent_messages:
            _spawn(_gami_auto_delete(sent_messages, autodel), name=f"gami_autodel:{chat_id}")

        if index < len(due) - 1:
            await asyncio.sleep(GAMI_SEND_SPACING_SECONDS)

    return published


# ==========================================
# 📡 BACKGROUND WORKER: DIFUSIÓN RECURRENTE DE PLANES (v9 · intervalo estricto 6/12/24/48 h)
# ==========================================
PLAN_BROADCAST_SPACING_SECONDS = 1.0
PLAN_ISOLATION_RETRY_SECONDS = 600
PLAN_TEXTS = {
    "es": {"duration": "Duración", "price": "Precio", "days": "días",
           "buy": "⭐ Obtener Acceso ({stars} Stars)", "resource": "🔗 Ver Recurso / Canal VIP"},
    "en": {"duration": "Duration", "price": "Price", "days": "days",
           "buy": "⭐ Get Access ({stars} Stars)", "resource": "🔗 View VIP Resource"},
}


def compose_plan_announcement(plan: dict, lang: str = "es") -> str:
    """Tarjeta comercial del plan (ES/EN). El HTML del operador se normaliza: Telegram no puede rechazarlo."""
    t = PLAN_TEXTS["en" if lang == "en" else "es"]
    promo = normalize_telegram_html(str(plan.get("promo_text") or "").strip())
    body = (
        f"💎 <b>{html.escape(str(plan.get('plan_name') or ''))}</b>\n\n"
        f"⏳ <b>{t['duration']}:</b> {int(plan.get('duration_days') or 0)} {t['days']}\n"
        f"⭐ <b>{t['price']}:</b> {int(plan.get('stars_price') or 0)} XTR"
    )
    if promo:
        body += f"\n\n{promo}"
    return body + "\n\n🛡️ <i>Cloud Media Management</i>"


def build_plan_markup(plan: dict, bot_username: str, lang: str = "es") -> InlineKeyboardMarkup:
    t = PLAN_TEXTS["en" if lang == "en" else "es"]
    stars = int(plan.get("stars_price") or 0)
    pay_link = f"https://t.me/{bot_username}?start=chanplan_{plan['plan_id']}_{plan['channel_id']}"
    rows = [[InlineKeyboardButton(text=t["buy"].format(stars=stars), url=pay_link)]]
    target_link = str(plan.get("target_link") or "").strip()
    if target_link:
        target_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
        rows.append([InlineKeyboardButton(text=t["resource"], url=target_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _plan_target_chat(raw_chat_id):
    if isinstance(raw_chat_id, str) and raw_chat_id.lstrip("-").isdigit():
        return int(raw_chat_id)
    return raw_chat_id


async def send_plan_announcement(bot: Bot, chat_id, plan: dict, bot_username: str, lang: str = "es") -> int:
    """Publica la tarjeta (con multimedia si cabe en el caption). Devuelve el message_id del texto con botón."""
    caption = compose_plan_announcement(plan, lang)
    markup = build_plan_markup(plan, bot_username, lang)
    media_id, media_type = plan.get("media_id"), plan.get("media_type")
    if media_id and media_type in ("photo", "video", "animation"):
        send_media = getattr(bot, f"send_{media_type}")
        if visible_length(caption) <= 1024:
            sent = await send_media(chat_id=chat_id, **{media_type: media_id}, caption=caption,
                                    reply_markup=markup, parse_mode="HTML")
            return sent.message_id
        await send_media(chat_id=chat_id, **{media_type: media_id})
    sent = await bot.send_message(chat_id=chat_id, text=caption[:4096], reply_markup=markup, parse_mode="HTML")
    return sent.message_id


def _is_permanent_send_error(err: Exception) -> bool:
    if isinstance(err, TelegramForbiddenError):
        return True
    text = str(err).lower()
    return any(marker in text for marker in _GAMI_PERMANENT_ERROR_MARKERS)


async def _dispatch_due_plan_broadcasts(bot: Bot) -> int:
    """
    Publica los planes vencidos. El reloj de cada plan se mide desde su último envío REAL:
      • éxito → mark_channel_plan_broadcasted (last_broadcast_at = ahora; siguiente = ahora + intervalo);
      • FloodWait / red / 5xx → reintento corto, sin mover last_broadcast_at ni desactivar;
      • error permanente (bot expulsado, sin permisos) → cuenta hacia la desactivación (3 seguidos).
    """
    due_plans = await get_due_channel_plan_broadcasts()
    if not due_plans:
        return 0
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "thebunkerapp_bot"
    published = 0
    for index, plan in enumerate(due_plans):
        plan_id = plan["plan_id"]
        target_chat = _plan_target_chat(plan["broadcast_chat_id"])
        lang = plan.get("broadcast_lang") or "es"
        if isinstance(target_chat, int) and raid_shield.is_isolated(target_chat):
            await register_channel_plan_broadcast_failure(plan_id, permanent=False, retry_after_seconds=PLAN_ISOLATION_RETRY_SECONDS)
            logger.info(f"🛡️ [Broadcast Worker] Plan {plan_id} aplazado: Escudo Anti-Raid activo en {target_chat}.")
            continue
        try:
            message_id = await send_plan_announcement(bot, target_chat, plan, bot_username, lang)
        except TelegramRetryAfter as rate_err:
            wait_s = int(getattr(rate_err, "retry_after", 5) or 5)
            await register_channel_plan_broadcast_failure(plan_id, permanent=False, retry_after_seconds=wait_s)
            logger.warning(f"⏳ [Broadcast Worker] FloodWait {wait_s}s publicando el plan {plan_id}; reintento programado.")
            await asyncio.sleep(min(wait_s + 1, 60))
            continue
        except (TelegramForbiddenError, TelegramBadRequest) as send_err:
            permanent = _is_permanent_send_error(send_err)
            disabled = await register_channel_plan_broadcast_failure(plan_id, permanent=permanent)
            logger.warning(
                f"⚠️ [Broadcast Worker] Plan {plan_id} no publicado en {target_chat}: {send_err}"
                + (" — difusión desactivada tras fallos permanentes." if disabled else " — reintento programado.")
            )
            if disabled:
                _notify_radar_ws(plan["channel_id"], "plan_broadcast_disabled", {"plan_id": plan_id, "reason": str(send_err)[:200]})
            continue
        except asyncio.CancelledError:
            raise
        except Exception as send_err:
            # Errores de red / timeouts: el envío pudo no salir; se reintenta pronto sin tocar el reloj.
            await register_channel_plan_broadcast_failure(plan_id, permanent=False)
            logger.error(f"❌ [Broadcast Worker] Error transitorio publicando el plan {plan_id} en {target_chat}: {send_err}")
            continue

        await mark_channel_plan_broadcasted(plan_id)
        published += 1
        logger.info(f"✅ [Broadcast Worker] Plan {plan_id} difundido en {target_chat} (mensaje {message_id}); "
                    f"próximo en {plan.get('broadcast_interval_hours')} h.")
        _notify_radar_ws(plan["channel_id"], "plan_broadcasted", {"plan_id": plan_id, "message_id": message_id})
        if index < len(due_plans) - 1:
            await asyncio.sleep(PLAN_BROADCAST_SPACING_SECONDS)
    return published


# ==========================================
# 🧾 v9 · INFORME PERIÓDICO DE RENDIMIENTO DE ADMINISTRADORES (CANAL DE REGISTRO)
# ==========================================
LOG_REPORT_TICK_SECONDS = 120
LOG_REPORT_TEXTS = {
    "es": {
        "title": "🧾 <b>Rendimiento de Administradores — {title}</b>",
        "period": "Periodo: últimas {hours} h",
        "admin_messages": "💬 <b>Actividad en el chat</b>",
        "admin_replies": "↩️ <b>Respuestas a miembros</b>",
        "admin_mod_actions": "👮 <b>Acciones de moderación</b>",
        "top_admin": "🏆 <b>Administrador destacado:</b> {name} ({score} pts)",
        "inactive_admins": "💤 <b>Sin actividad en el periodo</b>",
        "security_blocks": "🛡️ <b>Bloqueos automáticos del sistema</b>",
        "captcha_summary": "🤖 <b>Aduana Captcha Pro</b>",
        "raid_shield": "🚨 <b>Escudo Anti-Raid</b>",
        "msgs": "{n} mensajes", "replies": "{n} respuestas", "none": "Sin registros.",
        "total": "Total: {n}", "expelled": "expulsados", "bots": "bots", "users": "usuarios",
        "verified": "verificados", "failed": "rechazados", "pass_rate": "tasa de acierto",
        "shield_on": "ACTIVO ({m} min restantes)", "shield_off": "inactivo", "activations": "intervenciones del escudo: {n}",
        "sources": {"captcha": "Captcha", "antiflood": "Anti-Flood", "antispam": "Anti-Spam", "ai_guardian": "Guardián IA", "raid_shield": "Escudo Anti-Raid"},
        "actions": {"ban": "ban", "kick": "expulsión", "mute": "silencio", "unban": "desbaneo", "unmute": "reactivación", "promote": "ascenso", "demote": "degradación"},
    },
    "en": {
        "title": "🧾 <b>Admin Performance — {title}</b>",
        "period": "Period: last {hours} h",
        "admin_messages": "💬 <b>Chat activity</b>",
        "admin_replies": "↩️ <b>Replies to members</b>",
        "admin_mod_actions": "👮 <b>Moderation actions</b>",
        "top_admin": "🏆 <b>Top administrator:</b> {name} ({score} pts)",
        "inactive_admins": "💤 <b>No activity this period</b>",
        "security_blocks": "🛡️ <b>Automatic system blocks</b>",
        "captcha_summary": "🤖 <b>Captcha Pro customs</b>",
        "raid_shield": "🚨 <b>Anti-Raid Shield</b>",
        "msgs": "{n} messages", "replies": "{n} replies", "none": "No records.",
        "total": "Total: {n}", "expelled": "expelled", "bots": "bots", "users": "users",
        "verified": "verified", "failed": "rejected", "pass_rate": "pass rate",
        "shield_on": "ACTIVE ({m} min left)", "shield_off": "inactive", "activations": "shield interventions: {n}",
        "sources": {"captcha": "Captcha", "antiflood": "Anti-Flood", "antispam": "Anti-Spam", "ai_guardian": "AI Guardian", "raid_shield": "Anti-Raid Shield"},
        "actions": {"ban": "ban", "kick": "kick", "mute": "mute", "unban": "unban", "unmute": "unmute", "promote": "promote", "demote": "demote"},
    },
}
LOG_REPORT_LIST_MAX = 10


def compose_log_report(title: str, perf: dict, metrics: list, hours: int, lang: str = "es") -> str:
    """Informe HTML (≤ 4096) con SOLO las métricas elegidas por el propietario, en su idioma."""
    t = LOG_REPORT_TEXTS["en" if lang == "en" else "es"]
    esc = html.escape
    selected = set(metrics or [])
    admins = perf.get("admins") or []
    lines = [t["title"].format(title=esc(title or "")), f"<i>{t['period'].format(hours=int(hours))}</i>"]

    def section(header: str, rows: list) -> None:
        lines.append("")
        lines.append(header)
        lines.extend(rows or [f"<i>{t['none']}</i>"])

    if "admin_messages" in selected:
        rows = []
        for a in sorted(admins, key=lambda x: x["messages"], reverse=True)[:LOG_REPORT_LIST_MAX]:
            if not a["messages"] and not a["replies"]:
                continue
            extra = f" · {t['replies'].format(n=a['replies'])}" if "admin_replies" in selected else ""
            rows.append(f"• {esc(a['name'])} — {t['msgs'].format(n=a['messages'])}{extra}")
        section(t["admin_messages"], rows)
    elif "admin_replies" in selected:
        rows = [f"• {esc(a['name'])} — {t['replies'].format(n=a['replies'])}"
                for a in sorted(admins, key=lambda x: x["replies"], reverse=True)[:LOG_REPORT_LIST_MAX] if a["replies"]]
        section(t["admin_replies"], rows)

    if "admin_mod_actions" in selected:
        rows = []
        acting = [a for a in admins if a["actions_total"]]
        for a in sorted(acting, key=lambda x: x["actions_total"], reverse=True)[:LOG_REPORT_LIST_MAX]:
            detail = " · ".join(f"{n} {t['actions'].get(k, k)}" for k, n in sorted(a["actions"].items(), key=lambda kv: -kv[1]))
            rows.append(f"• {esc(a['name'])} — {detail}")
        if acting:
            rows.append(t["total"].format(n=sum(a["actions_total"] for a in acting)))
        section(t["admin_mod_actions"], rows)

    if "top_admin" in selected and admins:
        best = admins[0]
        score = best["actions_total"] * 3 + best["messages"] + best["replies"]
        if score:
            lines.append("")
            lines.append(t["top_admin"].format(name=esc(best["name"]), score=score))

    if "inactive_admins" in selected:
        rows = [f"• {esc(a['name'])}" + (f" (@{esc(a['username'])})" if a.get("username") else "")
                for a in (perf.get("inactive") or [])[:LOG_REPORT_LIST_MAX]]
        section(t["inactive_admins"], rows)

    sec = perf.get("security") or {}
    if "security_blocks" in selected:
        rows = []
        if sec.get("total"):
            rows.append(f"{t['total'].format(n=sec['total'])} · {sec.get('expelled', 0)} {t['expelled']} · "
                        f"{sec.get('users', 0)} {t['users']} · {sec.get('bots', 0)} {t['bots']}")
            for src, bucket in (sec.get("by_source") or {}).items():
                if bucket.get("total"):
                    rows.append(f"• {t['sources'].get(src, src)}: {bucket['total']} ({bucket.get('expelled', 0)} {t['expelled']})")
        section(t["security_blocks"], rows)

    if "captcha_summary" in selected:
        cap = sec.get("captcha") or {}
        rows = []
        if cap.get("verified") or cap.get("failed"):
            rate = cap.get("pass_rate")
            rows.append(f"{cap.get('verified', 0)} {t['verified']} · {cap.get('failed', 0)} {t['failed']}"
                        + (f" · {t['pass_rate']} {rate}%" if rate is not None else ""))
        section(t["captcha_summary"], rows)

    if "raid_shield" in selected:
        raid = perf.get("raid") or {}
        state = t["shield_on"].format(m=max(1, int(((raid.get("active_until") or 0) - time.time()) // 60))) if raid.get("active") else t["shield_off"]
        section(t["raid_shield"], [f"• {state}", f"• {t['activations'].format(n=int(raid.get('actions') or 0))}"])

    lines.append("")
    lines.append("🛡️ <i>Cloud Media Management</i>")
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4000].rsplit("\n", 1)[0] + "\n…\n\n🛡️ <i>Cloud Media Management</i>"
    return text


def _log_target(raw):
    return int(raw) if isinstance(raw, (int, str)) and str(raw).lstrip("-").isdigit() else raw


async def send_log_report(bot: Bot, group_id: int, cfg: dict) -> int:
    """Compone y publica el informe en el canal de registro. Devuelve el message_id (lanza si falla)."""
    hours = int(cfg.get("interval_hours") or 24)
    since = int(time.time()) - hours * 3600
    perf = await get_admin_performance(group_id, since)
    try:
        title = (await bot.get_chat(group_id)).title or str(group_id)
    except Exception:
        title = str(group_id)
    text = compose_log_report(title, perf, cfg.get("metrics") or [], hours, cfg.get("lang") or "es")
    sent = await bot.send_message(chat_id=_log_target(cfg.get("log_channel_id")), text=text, parse_mode="HTML",
                                  disable_web_page_preview=True)
    return sent.message_id


async def send_log_report_now(bot: Bot, group_id: int) -> dict:
    """"Enviar ahora" desde la Mini App: usa la configuración guardada y cuenta como envío real."""
    cfg = await get_log_report_config(group_id)
    if not cfg.get("log_channel_id"):
        raise ValueError("log_channel_id: configura primero el canal de registro.")
    if not cfg.get("metrics"):
        raise ValueError("metrics: elige al menos una métrica.")
    message_id = await send_log_report(bot, group_id, cfg)
    if cfg.get("enabled"):
        await mark_log_report_result(group_id, True)
    return {"sent": True, "message_id": message_id}


async def start_log_report_worker(bot: Bot):
    """Publica los informes vencidos. Intervalo estricto desde el último envío real; fallos → reintento corto."""
    logger.info("🧾 [Log Report Worker]: Informes periódicos de rendimiento de administradores iniciados.")
    while True:
        try:
            for cfg in await get_due_log_reports():
                group_id = int(cfg["group_id"])
                try:
                    await send_log_report(bot, group_id, cfg)
                    await mark_log_report_result(group_id, True)
                    logger.info(f"🧾 [Log Report] Informe de {group_id} publicado en {cfg.get('log_channel_id')}.")
                except TelegramRetryAfter as rate_err:
                    wait_s = int(getattr(rate_err, "retry_after", 5) or 5)
                    await mark_log_report_result(group_id, False, permanent=False, retry_after_seconds=wait_s)
                    await asyncio.sleep(min(wait_s + 1, 60))
                except (TelegramForbiddenError, TelegramBadRequest) as send_err:
                    disabled = await mark_log_report_result(group_id, False, permanent=_is_permanent_send_error(send_err))
                    logger.warning(f"⚠️ [Log Report] Informe de {group_id} no publicado: {send_err}"
                                   + (" — desactivado tras fallos permanentes." if disabled else " — reintento programado."))
                except asyncio.CancelledError:
                    raise
                except Exception as send_err:
                    await mark_log_report_result(group_id, False, permanent=False)
                    logger.error(f"❌ [Log Report] Error transitorio con {group_id}: {send_err}")
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [Log Report Worker Error]: {ex}")
        await asyncio.sleep(LOG_REPORT_TICK_SECONDS)


async def start_channel_broadcast_worker(bot: Bot):
    """Worker perimetral en segundo plano para difusión recurrente de planes en canales."""
    try:
        restored = await raid_shield.load_active_shields()
        if restored:
            logger.warning(f"🚨 [RaidShield] {restored} escudo(s) anti-raid siguen activos tras el reinicio.")
    except Exception as ex:
        logger.warning(f"⚠️ [RaidShield] No se pudo rehidratar el estado: {ex}")
    child_workers = [
        _spawn(start_subscription_watchdog_worker(bot), name="subscription_watchdog"),
        _spawn(start_meeting_announcement_worker(bot), name="meeting_announcer"),
        _spawn(start_vip_badge_expiry_worker(bot), name="vip_badge_watchdog"),
        _spawn(start_log_report_worker(bot), name="log_report_worker"),
    ]
    logger.info("📡 [Broadcast Worker]: Bucle de difusión recurrente de planes y anuncios de gamificación iniciado.")
    try:
        await _channel_broadcast_loop(bot)
    finally:
        # main.py cancela este worker al apagar: los sub-workers se detienen con él.
        for child in child_workers:
            child.cancel()


async def _channel_broadcast_loop(bot: Bot):
    while True:
        try:
            await _dispatch_due_plan_broadcasts(bot)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [Broadcast Worker Error]: {ex}")

        # 🎮 Anuncios recurrentes de gamificación (mismo ciclo del Broadcast Worker)
        try:
            await _dispatch_gamification_announcements(bot)
        except asyncio.CancelledError:
            raise
        except Exception as gami_err:
            logger.error(f"❌ [Gamificación Worker Error]: {gami_err}")

        await asyncio.sleep(60)