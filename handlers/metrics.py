"""
handlers/metrics.py — The Bunker OS (Aiogram 3.x)

Puente in-chat de la analítica en caliente:
- /metrics · /stats (grupos): tarjeta de resumen ejecutivo para administradores.
- /metrics · /metricas (privado): selector de comunidades y tarjeta con botón
  WebApp nativo hacia el Dashboard en Vivo.

Nota de plataforma: Telegram solo acepta botones `web_app` en chats privados
(en grupos devuelve BUTTON_TYPE_INVALID). Por eso en grupos se usa un Direct
Link Mini App (t.me/<bot>/<app>?startapp=<chat_id>) y, además, se entrega al
administrador por privado la tarjeta con el botón WebAppInfo nativo.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import importlib
import logging
import os
import time
import urllib.parse
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)


def get_community_full_analytics(*args, **kwargs):
    """Importa la analítica sólo cuando se necesita, evitando errores de resolución estática."""
    try:
        module = importlib.import_module("database.analytics")
    except ImportError as exc:
        raise RuntimeError("No se pudo importar database.analytics") from exc
    func = getattr(module, "get_community_full_analytics", None)
    if func is None:
        raise AttributeError("database.analytics no define get_community_full_analytics")
    return func(*args, **kwargs)

logger = logging.getLogger("bunker.metrics")
router = Router(name="community_metrics")

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://thebunkerapp2.netlify.app/").strip()
# Direct Link de la Mini App registrada en @BotFather (/newapp), p. ej. https://t.me/TheBunkerBot/dashboard
MINIAPP_DIRECT_LINK = os.getenv("MINIAPP_DIRECT_LINK", "").strip()
METRICS_CARD_TTL = int(os.getenv("METRICS_CARD_TTL", "120") or 0)
METRICS_COOLDOWN = float(os.getenv("METRICS_COOLDOWN", "15") or 0)
METRICS_DM_WEBAPP = os.getenv("METRICS_DM_WEBAPP", "1").strip().lower() in ("1", "true", "yes", "on", "si", "sí")

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

_BG_TASKS: set = set()
_LAST_CARD: dict[int, float] = {}


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def _auto_delete(message: Optional[Message], delay: int) -> None:
    if message is None or delay <= 0:
        return
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await message.delete()


# ==========================================
# 🔗 ENLACES A LA MINI APP
# ==========================================
def build_webapp_url(chat_id: int) -> str:
    """WEBAPP_URL con ?chat_id= añadido sin romper parámetros existentes."""
    parts = urllib.parse.urlsplit(WEBAPP_URL)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["chat_id"] = str(chat_id)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urllib.parse.urlencode(query), parts.fragment))


def build_group_launch_url(chat_id: int) -> str:
    """En grupos: Direct Link Mini App (abre dentro de Telegram con initData) o URL web."""
    if MINIAPP_DIRECT_LINK:
        separator = "&" if "?" in MINIAPP_DIRECT_LINK else "?"
        # startapp admite [A-Za-z0-9_-]: un chat_id negativo es válido tal cual.
        return f"{MINIAPP_DIRECT_LINK}{separator}startapp={chat_id}"
    return build_webapp_url(chat_id)


# ==========================================
# 🌐 TEXTOS BILINGÜES
# ==========================================
TEXTS = {
    "es": {
        "title": "📊 <b>Resumen Ejecutivo · {title}</b>",
        "cut": "<i>Corte: {stamp} ({tz})</i>",
        "messages": "💬 <b>Mensajes hoy:</b> <code>{n}</code>{delta}",
        "delta": " ({arrow} {pct}% vs. ayer a esta hora)",
        "active": "👥 <b>Activos hoy (DAU):</b> <code>{dau}</code> · <b>7d:</b> <code>{wau}</code> · <b>30d (MAU):</b> <code>{mau}</code>",
        "stickiness": "🧲 <b>Stickiness DAU/MAU:</b> <code>{pct}%</code>",
        "members": "🧭 <b>Miembros:</b> <code>{live}</code> en vivo · <code>{tracked}</code> en padrón",
        "growth": "🌱 <b>Altas 7d / 30d:</b> <code>{n7}</code> / <code>{n30}</code>",
        "retention": "🔁 <b>Retención cohorte 7d:</b> <code>{pct}</code> (n={size})",
        "stars": "⭐ <b>Recaudación:</b> <code>{total}</code> Stars · hoy <code>{today}</code> · 30d <code>{d30}</code>",
        "peak": "🔥 <b>Pico de actividad:</b> {day} {hour:02d}:00",
        "mix": "🧩 <b>Mezcla 30d:</b> {mix}",
        "top": "🏆 <b>Cuadro de Honor:</b>",
        "top_line": "{medal} {name} — Nivel {level} · <code>{xp}</code> XP · {msgs} msgs/30d",
        "empty_top": "<i>Aún sin actividad suficiente para el ranking.</i>",
        "dm_sent": "📬 <i>Te envié por privado el acceso directo al Dashboard.</i>",
        "dm_failed": "📬 <i>Inicia el bot en privado para recibir el acceso directo nativo.</i>",
        "button": "🌐 Abrir Dashboard en Vivo",
        "admin_only": "⛔ Solo los administradores pueden consultar las métricas.",
        "error": "⚠️ No se pudieron compilar las métricas en este momento. Inténtalo de nuevo.",
        "pick": "📊 <b>Métricas en Vivo</b>\n\nElige la comunidad que quieres auditar:",
        "no_groups": "📭 No tienes comunidades vinculadas. Añade el bot como administrador y usa <code>/metrics</code> dentro del grupo.",
        "usage": "Uso: <code>/metrics</code> o <code>/metrics -100XXXXXXXXXX</code>",
        "na": "—",
    },
    "en": {
        "title": "📊 <b>Executive Summary · {title}</b>",
        "cut": "<i>Snapshot: {stamp} ({tz})</i>",
        "messages": "💬 <b>Messages today:</b> <code>{n}</code>{delta}",
        "delta": " ({arrow} {pct}% vs. yesterday at this hour)",
        "active": "👥 <b>Active today (DAU):</b> <code>{dau}</code> · <b>7d:</b> <code>{wau}</code> · <b>30d (MAU):</b> <code>{mau}</code>",
        "stickiness": "🧲 <b>DAU/MAU stickiness:</b> <code>{pct}%</code>",
        "members": "🧭 <b>Members:</b> <code>{live}</code> live · <code>{tracked}</code> tracked",
        "growth": "🌱 <b>Joins 7d / 30d:</b> <code>{n7}</code> / <code>{n30}</code>",
        "retention": "🔁 <b>7d cohort retention:</b> <code>{pct}</code> (n={size})",
        "stars": "⭐ <b>Revenue:</b> <code>{total}</code> Stars · today <code>{today}</code> · 30d <code>{d30}</code>",
        "peak": "🔥 <b>Peak activity:</b> {day} {hour:02d}:00",
        "mix": "🧩 <b>30d mix:</b> {mix}",
        "top": "🏆 <b>Honor Board:</b>",
        "top_line": "{medal} {name} — Level {level} · <code>{xp}</code> XP · {msgs} msgs/30d",
        "empty_top": "<i>Not enough activity for the leaderboard yet.</i>",
        "dm_sent": "📬 <i>I sent you the direct Dashboard access in private.</i>",
        "dm_failed": "📬 <i>Start the bot in private to receive the native direct access.</i>",
        "button": "🌐 Open Live Dashboard",
        "admin_only": "⛔ Only administrators can view the metrics.",
        "error": "⚠️ Metrics could not be compiled right now. Please try again.",
        "pick": "📊 <b>Live Metrics</b>\n\nChoose the community you want to audit:",
        "no_groups": "📭 You have no linked communities. Add the bot as admin and use <code>/metrics</code> inside the group.",
        "usage": "Usage: <code>/metrics</code> or <code>/metrics -100XXXXXXXXXX</code>",
        "na": "—",
    },
}
FOOTER = "\n\n🛡️ <i>Cloud Media Management</i>"


def _lang(user) -> str:
    code = (getattr(user, "language_code", "") or "").lower()
    return "es" if (not code or code.startswith("es")) else "en"


def _fmt(n, lang: str) -> str:
    try:
        text = f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)
    return text.replace(",", ".") if lang == "es" else text


def render_metrics_card(data: dict, title: str, lang: str, live_members: Optional[int]) -> str:
    t = TEXTS[lang]
    summary = data.get("summary", {})
    growth = data.get("growth", {})
    heatmap = data.get("heatmap", {})
    breakdown = data.get("message_breakdown", {})

    delta = ""
    delta_pct = summary.get("messages_delta_pct")
    if delta_pct is not None:
        arrow = "▲" if delta_pct >= 0 else "▼"
        delta = t["delta"].format(arrow=arrow, pct=abs(delta_pct))

    cohort = growth.get("cohort_7d") or {}
    retention_pct = cohort.get("retention_pct")
    retention_text = f"{retention_pct}%" if retention_pct is not None else t["na"]

    lines = [
        t["title"].format(title=html.escape(title or str(data.get("chat_id", "")))),
        t["cut"].format(stamp=html.escape(data.get("generated_at_local", "")), tz=html.escape(data.get("timezone", ""))),
        "",
        t["messages"].format(n=_fmt(summary.get("messages_today", 0), lang), delta=delta),
        t["active"].format(
            dau=_fmt(summary.get("dau", 0), lang),
            wau=_fmt(summary.get("wau", 0), lang),
            mau=_fmt(summary.get("mau", 0), lang),
        ),
        t["stickiness"].format(pct=summary.get("stickiness_pct", 0)),
        t["members"].format(
            live=_fmt(live_members, lang) if live_members is not None else t["na"],
            tracked=_fmt(summary.get("members_tracked", 0), lang),
        ),
        t["growth"].format(n7=_fmt(growth.get("new_members_7d", 0), lang), n30=_fmt(growth.get("new_members_30d", 0), lang)),
        t["retention"].format(pct=retention_text, size=_fmt(cohort.get("size", 0), lang)),
        t["stars"].format(
            total=_fmt(summary.get("stars_total", 0), lang),
            today=_fmt(summary.get("stars_today", 0), lang),
            d30=_fmt(summary.get("stars_30d", 0), lang),
        ),
    ]

    peak = heatmap.get("peak")
    if peak:
        lines.append(t["peak"].format(day=peak["day_es"] if lang == "es" else peak["day_en"], hour=int(peak["hour"])))

    mix_parts = [
        f"{item['label_es'] if lang == 'es' else item['label_en']} {item['pct']}%"
        for item in breakdown.get("types", [])
        if item.get("count")
    ]
    if mix_parts:
        lines.append(t["mix"].format(mix=" · ".join(mix_parts)))

    lines.append("")
    lines.append(t["top"])
    top = data.get("leaderboard", [])[:3]
    medals = ["🥇", "🥈", "🥉"]
    if top:
        for idx, entry in enumerate(top):
            lines.append(t["top_line"].format(
                medal=medals[idx],
                name=html.escape(str(entry.get("name") or "—"))[:48],
                level=entry.get("level", 1),
                xp=_fmt(entry.get("xp", 0), lang),
                msgs=_fmt(entry.get("messages_30d", 0), lang),
            ))
    else:
        lines.append(t["empty_top"])

    return "\n".join(lines)


def _webapp_keyboard(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    """Botón WebApp nativo (solo válido en chats privados)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXTS[lang]["button"], web_app=WebAppInfo(url=build_webapp_url(chat_id)))
    ]])


def _group_keyboard(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXTS[lang]["button"], url=build_group_launch_url(chat_id))
    ]])


# ==========================================
# 🔐 VERIFICACIÓN DE ADMINISTRADOR
# ==========================================
def _status_value(member) -> str:
    status = getattr(member, "status", "")
    return str(getattr(status, "value", status) or "")


async def _user_is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in SUPER_ADMIN_IDS:
        return True
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, user_id), timeout=5)
    except Exception:
        return False
    return _status_value(member) in ("creator", "administrator")


async def _message_from_admin(bot: Bot, message: Message) -> bool:
    # Administrador anónimo: escribe "como el grupo".
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    if not message.from_user:
        return False
    return await _user_is_chat_admin(bot, message.chat.id, message.from_user.id)


async def _live_member_count(bot: Bot, chat_id: int) -> Optional[int]:
    try:
        return int(await asyncio.wait_for(bot.get_chat_member_count(chat_id), timeout=4))
    except Exception:
        return None


async def _resolve_title(bot: Bot, chat_id: int, fallback: str = "") -> str:
    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_id), timeout=4)
        return chat.title or fallback or str(chat_id)
    except Exception:
        return fallback or str(chat_id)


async def _compose_card(bot: Bot, chat_id: int, title: str, lang: str) -> str:
    data, live = await asyncio.gather(
        get_community_full_analytics(chat_id),
        _live_member_count(bot, chat_id),
    )
    return render_metrics_card(data, title, lang, live)


# ==========================================
# 📊 /metrics · /stats EN GRUPOS
# ==========================================
@router.message(Command("metrics", "stats", "metricas"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_metrics(message: Message, bot: Bot):
    chat_id = message.chat.id
    lang = _lang(message.from_user)

    if not await _message_from_admin(bot, message):
        # No consumir el comando: el resto de routers (cerradura de comandos, XP…) lo procesan.
        raise SkipHandler()

    with contextlib.suppress(Exception):
        await message.delete()

    now = time.monotonic()
    if METRICS_COOLDOWN and now - _LAST_CARD.get(chat_id, 0.0) < METRICS_COOLDOWN:
        return
    _LAST_CARD[chat_id] = now

    try:
        card = await _compose_card(bot, chat_id, message.chat.title or "", lang)
    except Exception as ex:
        logger.error("❌ [/metrics] Error compilando analítica de %s: %s", chat_id, ex, exc_info=True)
        with contextlib.suppress(Exception):
            err = await message.answer(TEXTS[lang]["error"] + FOOTER, parse_mode="HTML")
            _spawn(_auto_delete(err, 20))
        return

    dm_note = ""
    requester = message.from_user if (message.from_user and not message.from_user.is_bot) else None
    if METRICS_DM_WEBAPP and requester is not None:
        try:
            await bot.send_message(
                requester.id,
                card + FOOTER,
                reply_markup=_webapp_keyboard(chat_id, lang),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            dm_note = "\n\n" + TEXTS[lang]["dm_sent"]
        except TelegramAPIError:
            dm_note = "\n\n" + TEXTS[lang]["dm_failed"]
        except Exception as ex:
            logger.debug("Aviso enviando /metrics por privado: %s", ex)

    try:
        sent = await message.answer(
            card + dm_note + FOOTER,
            reply_markup=_group_keyboard(chat_id, lang),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        _spawn(_auto_delete(sent, METRICS_CARD_TTL))
    except Exception as ex:
        logger.debug("Aviso publicando /metrics en %s: %s", chat_id, ex)


# ==========================================
# 📊 /metrics EN PRIVADO (BOTÓN WEBAPP NATIVO)
# ==========================================
async def _send_private_card(bot: Bot, user, chat_id: int, target: Message, edit: bool = False) -> None:
    lang = _lang(user)
    if not await _user_is_chat_admin(bot, chat_id, user.id):
        text = TEXTS[lang]["admin_only"] + FOOTER
        if edit:
            with contextlib.suppress(Exception):
                await target.edit_text(text, parse_mode="HTML")
        else:
            await target.answer(text, parse_mode="HTML")
        return
    title = await _resolve_title(bot, chat_id)
    try:
        card = await _compose_card(bot, chat_id, title, lang)
    except Exception as ex:
        logger.error("❌ [/metrics privado] %s: %s", chat_id, ex, exc_info=True)
        card = TEXTS[lang]["error"]
    markup = _webapp_keyboard(chat_id, lang)
    if edit:
        with contextlib.suppress(Exception):
            await target.edit_text(card + FOOTER, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            return
    await target.answer(card + FOOTER, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


# /stats en privado se deja libre a propósito: user_private.py puede usarlo para otra consola.
@router.message(Command("metrics", "metricas"), F.chat.type == "private")
async def cmd_private_metrics(message: Message, command: CommandObject, bot: Bot):
    if not message.from_user:
        return
    lang = _lang(message.from_user)
    arg = (command.args or "").strip().split()[0] if command and command.args else ""

    if arg:
        try:
            chat_id = int(arg)
        except ValueError:
            await message.answer(TEXTS[lang]["usage"] + FOOTER, parse_mode="HTML")
            return
        await _send_private_card(bot, message.from_user, chat_id, message)
        return

    try:
        from database.database import get_user_groups
        groups = await get_user_groups(message.from_user.id)
    except Exception as ex:
        logger.debug("Aviso leyendo grupos de %s: %s", message.from_user.id, ex)
        groups = []

    if not groups:
        await message.answer(TEXTS[lang]["no_groups"] + FOOTER, parse_mode="HTML")
        return

    rows = [
        [InlineKeyboardButton(text=f"📍 {str(name or gid)[:40]}", callback_data=f"mtr:{gid}")]
        for gid, name in list(groups)[:20]
    ]
    await message.answer(
        TEXTS[lang]["pick"] + FOOTER,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("mtr:"))
async def cb_private_metrics(callback: CallbackQuery, bot: Bot):
    with contextlib.suppress(Exception):
        await callback.answer()
    if not callback.from_user or not callback.message or not callback.data:
        return
    try:
        chat_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return
    await _send_private_card(bot, callback.from_user, chat_id, callback.message, edit=True)