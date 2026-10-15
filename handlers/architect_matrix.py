"""
architect_matrix.py — The Bunker Architect Command Matrix (Matriz del Arquitecto Supremo) · Aiogram 3.x

Subsistema 100 % EXCLUSIVO del Arquitecto Supremo (SUPER_ADMIN_IDS / ADMIN_IDS). Ningún creador de comunidad,
administrador ni usuario regular puede verlo, invocarlo ni operarlo: el router entero está detrás de un middleware
que responde con `owner_only_alert` y descarta el evento antes de que llegue a cualquier handler.

Módulos:
  1. 🛡️ Administradores Delegados — por comunidad, con 6 interruptores 🟢/🔴 estilo Telegram que se guardan al
     toque y solo redibujan la botonera (edit_reply_markup), sin recargar el panel.
  2. 💎 Licencias PRO ⭐ / ULTRA PRO 💎 — a comunidades o usuarios, sin pasarela de Stars: 7 / 30 / 90 / 365 días
     o permanente. Se superponen a la licencia pagada y vencen solas (worker de expiración cada 5 min).
  3. 🏆 Centro de Premios — sobre el Top 10 de reputación o un usuario manual: inyección de XP / niveles, pases
     MicVIP (24 h / 48 h / 7 d) con etiqueta VIP nativa, membresías VIP gratuitas a planes de canal y anuncio
     ceremonial opcional en la comunidad.
  4. 📜 Bitácora — cada acción del Arquitecto queda registrada (architect_audit).

Entradas: comando /architect (/arquitecto, /matrix) en privado y botón «👑 Matriz del Arquitecto» del menú
principal (solo visible para Arquitectos). Callbacks con prefijo `amx:` (≤ 64 bytes).

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

from database.database import (
    DELEGATE_PERMISSIONS,
    architect_grant_xp,
    expire_architect_licenses,
    find_known_user,
    get_architect_audit,
    get_architect_award,
    get_architect_delegate,
    get_architect_license,
    get_architect_matrix_summary,
    get_channel_plan,
    get_channel_plans,
    get_effective_license,
    get_expired_architect_vip_tags,
    get_top_reputation,
    get_user_global_tier,
    get_user_reputation,
    get_vip_badge_title,
    grant_architect_license,
    grant_vip_mic,
    is_super_admin,
    is_vip_mic_active,
    list_architect_delegates,
    list_architect_licenses,
    list_registered_chats,
    mark_architect_award_announced,
    mark_architect_vip_tag_cleared,
    normalize_tier,
    record_architect_audit,
    record_architect_award,
    record_channel_subscription,
    remove_architect_delegate,
    revoke_architect_license,
    set_all_architect_delegate_permissions,
    toggle_architect_delegate_permission,
    unmark_architect_award_announced,
    upsert_architect_delegate,
)
from . import user_private as up

try:
    from aiogram.types import KeyboardButtonRequestUsers  # Bot API 7.0+ (aiogram ≥ 3.4)
except ImportError:  # pragma: no cover - aiogram antiguo: se usa solo la entrada por ID / alias / reenvío
    KeyboardButtonRequestUsers = None

try:
    from radar_bus import publish_radar_event  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    def publish_radar_event(*args, **kwargs):
        return None

logger = logging.getLogger("architect_matrix")
router = Router(name="architect_matrix")

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
PAGE_SIZE = 8
STATE_TTL_SECONDS = 600
USER_PICKER_REQUEST_ID = 7401
WORKER_INTERVAL_SECONDS = 300
MAX_CUSTOM_XP = 1_000_000

XP_PRESETS = (500, 1000, 5000)
LEVEL_PRESETS = (1, 5)
MICVIP_PRESETS = (24, 48, 168)
MEMBERSHIP_DAY_PRESETS = (7, 30, 90)
LICENSE_DURATIONS = (7, 30, 90, 365, 0)

PERM_LABELS = {
    "can_manage_captcha": ("🤖 Aduana y Captcha", "🤖 Checkpoint & Captcha"),
    "can_manage_locks": ("🔒 Cerraduras de contenido", "🔒 Content locks"),
    "can_manage_antispam": ("🛡️ Anti-Spam y Anti-Flood", "🛡️ Anti-Spam & Anti-Flood"),
    "can_manage_broadcast": ("📢 Difusión de planes y anuncios", "📢 Plans & announcements"),
    "can_view_analytics": ("📊 Métricas y telemetría (lectura)", "📊 Metrics & telemetry (read-only)"),
    "can_manage_sentinel": ("🎙️ Radar de voz y Centinelas", "🎙️ Voice radar & Sentinels"),
}
TIER_BADGE = {"free": "Free", "pro": "PRO ⭐", "ultra_pro": "ULTRA PRO 💎"}
SOURCE_LABEL = {
    "payment": ("pago con Stars", "Stars payment"),
    "architect": ("concesión del Arquitecto", "Architect grant"),
    "architect_user": ("licencia personal del dueño", "owner's personal license"),
}

# Estado conversacional del Arquitecto: (bot_id, user_id) → {"kind", "ts", ...}
AMX_STATES: dict = {}
# Idioma elegido por cada Arquitecto dentro de la Matriz
AMX_LANG: dict = {}
_BG_TASKS: set = set()

# Integración con la consola privada: /start, /cancel y la navegación de user_private liberan también estos
# estados, para que una entrada pendiente de la Matriz nunca capture un texto destinado a otro editor.
if hasattr(up, "register_external_state_store"):
    up.register_external_state_store(AMX_STATES)

# Tarjeta HUD propia (assets/arquitecto.jfif · assets/architect.jfif); si no existe se usa la Matriz de Seguridad.
up.CARD_IMAGES.setdefault("architect", {"es": "arquitecto.jfif", "en": "architect.jfif"})


# ==========================================
# 🧰 UTILIDADES
# ==========================================
def tr(lang: str, es: str, en: str) -> str:
    return es if lang == "es" else en


def _lang(user) -> str:
    return AMX_LANG.get(getattr(user, "id", 0)) or up.user_lang(user)


def _owner_alert(lang: str) -> str:
    return up.TEXTS.get(lang, up.TEXTS["es"])["owner_only_alert"]


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _fmt_ts(ts: Optional[int], lang: str) -> str:
    if not ts:
        return tr(lang, "Permanente ♾️", "Permanent ♾️")
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _remaining(ts: Optional[int], lang: str) -> str:
    if not ts:
        return "♾️"
    left = int(ts) - int(time.time())
    if left <= 0:
        return tr(lang, "vencida", "expired")
    days, rem = divmod(left, 86400)
    hours = rem // 3600
    return tr(lang, f"{days} d {hours} h", f"{days} d {hours} h")


def _duration_label(days: int, lang: str) -> str:
    if days == 0:
        return tr(lang, "♾️ Permanente", "♾️ Permanent")
    if days == 365:
        return tr(lang, "📅 1 año", "📅 1 year")
    return tr(lang, f"📅 {days} días", f"📅 {days} days")


def _hours_label(hours: int, lang: str) -> str:
    return tr(lang, "7 días", "7 days") if hours == 168 else f"{hours} h"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data demasiado largo: {data!r}")
    return InlineKeyboardButton(text=text, callback_data=data)


def _home_btn(lang: str) -> InlineKeyboardButton:
    return _btn(tr(lang, "👑 Matriz", "👑 Matrix"), "amx:home")


def _clear_state(bot_id: int, user_id: int) -> None:
    AMX_STATES.pop((bot_id, user_id), None)


def _set_state(bot_id: int, owner_id: int, kind: str, /, **data) -> None:
    """Parámetros posicionales: los datos del estado pueden llevar su propio `user_id` (destinatario del premio)."""
    AMX_STATES[(bot_id, owner_id)] = {"kind": kind, "ts": time.time(), **data}


async def _chat_title(bot: Bot, chat_id: int) -> str:
    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_id), timeout=5)
        return chat.title or chat.full_name or str(chat_id)
    except Exception:
        return f"Chat {chat_id}"


async def _user_label(bot: Bot, chat_id: Optional[int], user_id: int, fallback: str = "") -> str:
    if fallback:
        return fallback
    if chat_id:
        with contextlib.suppress(Exception):
            member = await asyncio.wait_for(bot.get_chat_member(chat_id, user_id), timeout=5)
            if member.user:
                return member.user.full_name or str(user_id)
    with contextlib.suppress(Exception):
        chat = await asyncio.wait_for(bot.get_chat(user_id), timeout=5)
        return chat.full_name or str(user_id)
    return f"ID {user_id}"


async def _show(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Edita la vista actual; si es una tarjeta con foto y el texto no cabe en el caption, la reemplaza."""
    msg = callback.message
    if msg is not None and getattr(msg, "photo", None) and len(text) > 1000:
        with contextlib.suppress(Exception):
            await msg.delete()
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=keyboard, parse_mode="HTML")
        return
    await up.safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")


async def _dismiss_reply_keyboard(bot: Bot, chat_id: int) -> None:
    with contextlib.suppress(Exception):
        ghost = await bot.send_message(chat_id, "⏳", reply_markup=ReplyKeyboardRemove())
        await ghost.delete()


async def _audit(actor_id: int, action: str, chat_id: int = None, target_id: int = None, detail: str = "") -> None:
    try:
        await record_architect_audit(actor_id, action, chat_id, target_id, detail)
    except Exception as ex:
        logger.warning(f"⚠️ [Matriz] No se pudo registrar la auditoría ({action}): {ex}")
    logger.info(f"👑 [Matriz] {actor_id} → {action} chat={chat_id} target={target_id} {detail}")


# ==========================================
# 🔐 BARRERA DE ACCESO (100 % ARQUITECTO)
# ==========================================
class ArchitectOnlyMiddleware(BaseMiddleware):
    """
    Cualquier evento que llegue a un handler de este router y NO provenga de un Arquitecto se descarta:
    los callbacks reciben `owner_only_alert` como alerta; los mensajes, el mismo aviso como respuesta.
    """
    async def __call__(self, handler, event, data: dict):
        user = getattr(event, "from_user", None)
        if user is not None and is_super_admin(user.id):
            return await handler(event, data)
        lang = up.user_lang(user) if user else "es"
        logger.warning(f"⛔ [Matriz] Acceso bloqueado: user={getattr(user, 'id', None)} no es Arquitecto.")
        if isinstance(event, CallbackQuery):
            with contextlib.suppress(Exception):
                await event.answer(_owner_alert(lang), show_alert=True)
        elif isinstance(event, Message):
            with contextlib.suppress(Exception):
                await event.answer(_owner_alert(lang))
        return None


class _CallbackAutoAnswer(BaseMiddleware):
    """Libera siempre el spinner del botón (Telegram admite una sola respuesta: la segunda se ignora)."""
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        try:
            return await handler(event, data)
        finally:
            with contextlib.suppress(Exception):
                await event.answer()


router.message.middleware(ArchitectOnlyMiddleware())
router.callback_query.middleware(_CallbackAutoAnswer())
router.callback_query.middleware(ArchitectOnlyMiddleware())


def _has_pending_state(message: Message) -> bool:
    """Filtro: solo intercepta mensajes privados de un Arquitecto con una entrada de la Matriz pendiente."""
    user = message.from_user
    if not user or not is_super_admin(user.id):
        return False
    key = (message.bot.id, user.id)
    state = AMX_STATES.get(key)
    if not state:
        return False
    if time.time() - state.get("ts", 0) > STATE_TTL_SECONDS:
        AMX_STATES.pop(key, None)
        return False
    return True


# ==========================================
# 🏠 VISTAS
# ==========================================
async def view_home(lang: str) -> tuple:
    try:
        summary = await get_architect_matrix_summary()
    except Exception as ex:
        logger.warning(f"⚠️ [Matriz] Resumen no disponible: {ex}")
        summary = {"delegates": 0, "licenses": 0, "awards_30d": 0, "chats": 0}
    text = tr(
        lang,
        "👑 <b>The Bunker Architect Command Matrix</b>\n"
        "<i>Consola exclusiva del Arquitecto Supremo.</i>\n\n"
        f"• 🏘️ <b>Comunidades registradas:</b> <code>{summary['chats']}</code>\n"
        f"• 🛡️ <b>Delegados activos:</b> <code>{summary['delegates']}</code>\n"
        f"• 💎 <b>Licencias concedidas vigentes:</b> <code>{summary['licenses']}</code>\n"
        f"• 🏆 <b>Premios (30 días):</b> <code>{summary['awards_30d']}</code>",
        "👑 <b>The Bunker Architect Command Matrix</b>\n"
        "<i>Supreme Architect exclusive console.</i>\n\n"
        f"• 🏘️ <b>Registered communities:</b> <code>{summary['chats']}</code>\n"
        f"• 🛡️ <b>Active delegates:</b> <code>{summary['delegates']}</code>\n"
        f"• 💎 <b>Active granted licenses:</b> <code>{summary['licenses']}</code>\n"
        f"• 🏆 <b>Awards (30 days):</b> <code>{summary['awards_30d']}</code>",
    ) + SIG
    other = "en" if lang == "es" else "es"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn(tr(lang, "🛡️ Administradores Delegados", "🛡️ Delegated Admins"), "amx:pk:dl:0")],
        [_btn(tr(lang, "💎 Licencias PRO / ULTRA PRO", "💎 PRO / ULTRA PRO Licenses"), "amx:lc")],
        [_btn(tr(lang, "🏆 Centro de Premios", "🏆 Awards Center"), "amx:pk:aw:0")],
        [_btn(tr(lang, "📜 Bitácora", "📜 Audit Log"), "amx:log"),
         _btn("🌐 English" if lang == "es" else "🌐 Español", f"amx:lang:{other}")],
        [InlineKeyboardButton(text=tr(lang, "🏠 Menú Principal", "🏠 Main Menu"), callback_data=f"menu_main_{lang}")],
    ])
    return text, kb


PICKER_PURPOSES = {
    "dl": ("all", ("🛡️ <b>Administradores Delegados</b>", "🛡️ <b>Delegated Admins</b>")),
    "lc": ("all", ("💎 <b>Licencia para una comunidad</b>", "💎 <b>License for a community</b>")),
    "aw": ("group", ("🏆 <b>Centro de Premios</b>", "🏆 <b>Awards Center</b>")),
}


async def view_picker(purpose: str, page: int, lang: str) -> tuple:
    kind, titles = PICKER_PURPOSES[purpose]
    page = max(0, page)
    rows, total = await list_registered_chats(kind, PAGE_SIZE, page * PAGE_SIZE)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page >= pages and total:
        page = pages - 1
        rows, total = await list_registered_chats(kind, PAGE_SIZE, page * PAGE_SIZE)
    text = (
        f"{tr(lang, titles[0], titles[1])}\n\n"
        + tr(lang, f"Selecciona la comunidad ({total} registradas · página {page + 1}/{pages}):",
             f"Select the community ({total} registered · page {page + 1}/{pages}):")
        + SIG
    )
    kb = []
    for row in rows:
        icon = "📢" if row["chat_type"] == "channel" else "👥"
        kb.append([_btn(f"{icon} {row['title'][:40]}", f"amx:c:{purpose}:{row['chat_id']}")])
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"amx:pk:{purpose}:{page - 1}"))
    if page + 1 < pages:
        nav.append(_btn("➡️", f"amx:pk:{purpose}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([_btn(tr(lang, "✍️ Ingresar ID / @canal", "✍️ Enter ID / @channel"), f"amx:pm:{purpose}")])
    back = "amx:lc" if purpose == "lc" else "amx:home"
    kb.append([_btn(tr(lang, "🔙 Volver", "🔙 Back"), back), _home_btn(lang)])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


# ---------- 🛡️ Delegados ----------
async def view_delegates(bot: Bot, chat_id: int, lang: str) -> tuple:
    title = html.escape(await _chat_title(bot, chat_id))
    delegates = await list_architect_delegates(chat_id)
    lines = []
    kb = []
    for d in delegates:
        name = html.escape(d["user_name"] or f"ID {d['user_id']}")
        lines.append(f"• {name} — <code>{d['active_count']}/{len(DELEGATE_PERMISSIONS)}</code>")
        kb.append([_btn(f"👤 {(d['user_name'] or str(d['user_id']))[:30]} · {d['active_count']}/{len(DELEGATE_PERMISSIONS)}",
                        f"amx:de:{chat_id}:{d['user_id']}")])
    body = "\n".join(lines) if lines else tr(lang, "<i>Sin delegados todavía.</i>", "<i>No delegates yet.</i>")
    text = tr(
        lang,
        f"🛡️ <b>Administradores Delegados — {title}</b>\n\n{body}\n\n"
        "<i>Un delegado solo opera los módulos que le actives. El resto de la consola sigue reservado al Dueño.</i>",
        f"🛡️ <b>Delegated Admins — {title}</b>\n\n{body}\n\n"
        "<i>A delegate only operates the modules you enable. The rest of the console stays Owner-only.</i>",
    ) + SIG
    kb.append([_btn(tr(lang, "➕ Añadir delegado", "➕ Add delegate"), f"amx:da:{chat_id}")])
    kb.append([_btn(tr(lang, "🔙 Comunidades", "🔙 Communities"), "amx:pk:dl:0"), _home_btn(lang)])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def delegate_toggles_keyboard(chat_id: int, user_id: int, delegate: dict, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for idx, perm in enumerate(DELEGATE_PERMISSIONS):
        on = bool(delegate.get(perm))
        label = PERM_LABELS[perm][0 if lang == "es" else 1]
        rows.append([_btn(f"{'🟢' if on else '🔴'} {label}", f"amx:tg:{chat_id}:{user_id}:{idx}")])
    rows.append([
        _btn(tr(lang, "✅ Activar todo", "✅ Enable all"), f"amx:ta:{chat_id}:{user_id}:1"),
        _btn(tr(lang, "⛔ Desactivar todo", "⛔ Disable all"), f"amx:ta:{chat_id}:{user_id}:0"),
    ])
    rows.append([_btn(tr(lang, "🗑️ Revocar delegado", "🗑️ Revoke delegate"), f"amx:dr:{chat_id}:{user_id}")])
    rows.append([_btn(tr(lang, "🔙 Delegados", "🔙 Delegates"), f"amx:dl:{chat_id}"), _home_btn(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def view_delegate_editor(bot: Bot, chat_id: int, user_id: int, lang: str) -> Optional[tuple]:
    delegate = await get_architect_delegate(chat_id, user_id)
    if not delegate:
        return None
    title = html.escape(await _chat_title(bot, chat_id))
    name = html.escape(delegate["user_name"] or f"ID {user_id}")
    membership = ""
    with contextlib.suppress(Exception):
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, user_id), timeout=5)
        status = str(getattr(member.status, "value", member.status) or "")
        if status in ("left", "kicked"):
            membership = tr(lang, "\n⚠️ <i>No es miembro de la comunidad: no verá sus paneles hasta unirse.</i>",
                            "\n⚠️ <i>Not a member of the community: panels stay hidden until they join.</i>")
    text = tr(
        lang,
        f"🛡️ <b>Permisos del delegado</b>\n\n• <b>Comunidad:</b> {title}\n• <b>Delegado:</b> {name} (<code>{user_id}</code>)"
        f"{membership}\n\nToca un interruptor para activarlo 🟢 o desactivarlo 🔴. Se guarda al instante.",
        f"🛡️ <b>Delegate permissions</b>\n\n• <b>Community:</b> {title}\n• <b>Delegate:</b> {name} (<code>{user_id}</code>)"
        f"{membership}\n\nTap a switch to enable 🟢 or disable 🔴 it. Saved instantly.",
    ) + SIG
    return text, delegate_toggles_keyboard(chat_id, user_id, delegate, lang)


# ---------- 💎 Licencias ----------
def view_license_menu(lang: str) -> tuple:
    text = tr(
        lang,
        "💎 <b>Concesión de Licencias</b>\n\n"
        "Otorga <b>PRO ⭐</b> o <b>ULTRA PRO 💎</b> sin pasar por Telegram Stars. La concesión se suma a cualquier "
        "licencia pagada (gana la mayor) y vuelve a <b>Free</b> sola al vencer.\n\n"
        "• <b>Comunidad:</b> aplica a ese grupo o canal.\n"
        "• <b>Usuario:</b> aplica a todas las comunidades que ese usuario posee.",
        "💎 <b>License Grants</b>\n\n"
        "Grant <b>PRO ⭐</b> or <b>ULTRA PRO 💎</b> without Telegram Stars. The grant stacks with any paid license "
        "(the highest wins) and reverts to <b>Free</b> automatically when it expires.\n\n"
        "• <b>Community:</b> applies to that group or channel.\n"
        "• <b>User:</b> applies to every community that user owns.",
    ) + SIG
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn(tr(lang, "🏘️ Comunidad", "🏘️ Community"), "amx:pk:lc:0"),
         _btn(tr(lang, "👤 Usuario", "👤 User"), "amx:lu")],
        [_btn(tr(lang, "📋 Licencias activas", "📋 Active licenses"), "amx:la:0")],
        [_home_btn(lang)],
    ])
    return text, kb


async def view_license_target(bot: Bot, scope: str, target_id: int, lang: str) -> tuple:
    db_scope = "chat" if scope == "c" else "user"
    grant = await get_architect_license(db_scope, target_id)
    if scope == "c":
        name = html.escape(await _chat_title(bot, target_id))
        info = await get_effective_license(target_id)
        source = SOURCE_LABEL.get(info.get("source") or "", ("—", "—"))
        status_line = tr(
            lang,
            f"• <b>Licencia vigente:</b> {TIER_BADGE[info['tier']]} ({source[0]})\n"
            f"• <b>Licencia pagada:</b> {TIER_BADGE[info['base_tier']]}",
            f"• <b>Current license:</b> {TIER_BADGE[info['tier']]} ({source[1]})\n"
            f"• <b>Paid license:</b> {TIER_BADGE[info['base_tier']]}",
        )
        header = tr(lang, f"🏘️ <b>Comunidad:</b> {name} (<code>{target_id}</code>)",
                    f"🏘️ <b>Community:</b> {name} (<code>{target_id}</code>)")
        back = "amx:pk:lc:0"
    else:
        name = html.escape(await _user_label(bot, None, target_id))
        tier = normalize_tier(await get_user_global_tier(target_id))
        status_line = tr(lang, f"• <b>Licencia global vigente:</b> {TIER_BADGE[tier]}",
                         f"• <b>Current global license:</b> {TIER_BADGE[tier]}")
        header = tr(lang, f"👤 <b>Usuario:</b> {name} (<code>{target_id}</code>)",
                    f"👤 <b>User:</b> {name} (<code>{target_id}</code>)")
        back = "amx:lc"
    if grant:
        grant_line = tr(
            lang,
            f"• <b>Concesión del Arquitecto:</b> {TIER_BADGE[grant['tier']]} · vence {_fmt_ts(grant['expires_at'], lang)} "
            f"({_remaining(grant['expires_at'], lang)})",
            f"• <b>Architect grant:</b> {TIER_BADGE[grant['tier']]} · expires {_fmt_ts(grant['expires_at'], lang)} "
            f"({_remaining(grant['expires_at'], lang)})",
        )
    else:
        grant_line = tr(lang, "• <b>Concesión del Arquitecto:</b> ninguna", "• <b>Architect grant:</b> none")
    text = (
        f"💎 <b>{tr(lang, 'Licencia', 'License')}</b>\n\n{header}\n{status_line}\n{grant_line}\n\n"
        + tr(lang, "<i>Elegir un nivel y una duración reemplaza la concesión actual.</i>",
             "<i>Choosing a tier and duration replaces the current grant.</i>")
        + SIG
    )
    rows = [[_btn("⭐ PRO", f"amx:lv:{scope}:{target_id}:p"), _btn("💎 ULTRA PRO", f"amx:lv:{scope}:{target_id}:u")]]
    if grant:
        rows.append([_btn(tr(lang, "🗑️ Revocar concesión", "🗑️ Revoke grant"), f"amx:lr:{scope}:{target_id}")])
    rows.append([_btn(tr(lang, "🔙 Volver", "🔙 Back"), back), _home_btn(lang)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def view_license_durations(scope: str, target_id: int, tier_code: str, lang: str) -> tuple:
    tier = "ultra_pro" if tier_code == "u" else "pro"
    text = tr(lang, f"⏳ <b>Duración de la licencia {TIER_BADGE[tier]}</b>\n\nSelecciona el periodo de vigencia:",
              f"⏳ <b>{TIER_BADGE[tier]} license duration</b>\n\nSelect the validity period:") + SIG
    rows = [
        [_btn(_duration_label(7, lang), f"amx:lg:{scope}:{target_id}:{tier_code}:7"),
         _btn(_duration_label(30, lang), f"amx:lg:{scope}:{target_id}:{tier_code}:30")],
        [_btn(_duration_label(90, lang), f"amx:lg:{scope}:{target_id}:{tier_code}:90"),
         _btn(_duration_label(365, lang), f"amx:lg:{scope}:{target_id}:{tier_code}:365")],
        [_btn(_duration_label(0, lang), f"amx:lg:{scope}:{target_id}:{tier_code}:0")],
        [_btn(tr(lang, "🔙 Volver", "🔙 Back"), f"amx:lt:{scope}:{target_id}"), _home_btn(lang)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def view_license_list(bot: Bot, page: int, lang: str) -> tuple:
    page = max(0, page)
    grants, total = await list_architect_licenses(PAGE_SIZE, page * PAGE_SIZE)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    lines, kb = [], []
    for g in grants:
        scope = "c" if g["scope"] == "chat" else "u"
        icon = "🏘️" if scope == "c" else "👤"
        name = await (_chat_title(bot, g["target_id"]) if scope == "c" else _user_label(bot, None, g["target_id"]))
        lines.append(f"{icon} {html.escape(name)} — {TIER_BADGE[g['tier']]} · {_remaining(g['expires_at'], lang)}")
        kb.append([_btn(f"{icon} {name[:28]} · {TIER_BADGE[g['tier']]}", f"amx:lt:{scope}:{g['target_id']}")])
    body = "\n".join(lines) if lines else tr(lang, "<i>No hay concesiones vigentes.</i>", "<i>No active grants.</i>")
    text = tr(lang, f"📋 <b>Licencias concedidas vigentes</b> ({total})\n\n{body}",
              f"📋 <b>Active granted licenses</b> ({total})\n\n{body}") + SIG
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"amx:la:{page - 1}"))
    if page + 1 < pages:
        nav.append(_btn("➡️", f"amx:la:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([_btn(tr(lang, "🔙 Licencias", "🔙 Licenses"), "amx:lc"), _home_btn(lang)])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


# ---------- 🏆 Premios ----------
async def view_awards_chat(bot: Bot, chat_id: int, lang: str) -> tuple:
    title = html.escape(await _chat_title(bot, chat_id))
    top = await get_top_reputation(chat_id, limit=10)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    kb = []
    for idx, item in enumerate(top):
        kb.append([_btn(f"{medals[idx]} {item['name'][:26]} · Lv {item['level']} · {item['xp']} XP",
                        f"amx:au:{chat_id}:{item['user_id']}")])
    body = tr(lang, "Elige a un miembro del Top 10 o ingresa uno manualmente:",
              "Pick a Top 10 member or enter one manually:") if top else \
        tr(lang, "<i>Aún no hay actividad de reputación. Ingresa un usuario manualmente.</i>",
           "<i>No reputation activity yet. Enter a user manually.</i>")
    text = f"🏆 <b>{tr(lang, 'Centro de Premios', 'Awards Center')} — {title}</b>\n\n{body}" + SIG
    kb.append([_btn(tr(lang, "✍️ ID / @alias / selector", "✍️ ID / @alias / picker"), f"amx:um:{chat_id}")])
    kb.append([_btn(tr(lang, "🔙 Comunidades", "🔙 Communities"), "amx:pk:aw:0"), _home_btn(lang)])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def view_award_user(bot: Bot, chat_id: int, user_id: int, lang: str) -> tuple:
    title = html.escape(await _chat_title(bot, chat_id))
    name = html.escape(await _user_label(bot, chat_id, user_id))
    rep = await get_user_reputation(chat_id, user_id)
    vip = await is_vip_mic_active(user_id, chat_id)
    rank = f"#{rep['rank']}" if rep.get("rank") else "—"
    text = tr(
        lang,
        f"🏆 <b>Premiar a {name}</b>\n\n• <b>Comunidad:</b> {title}\n• <b>ID:</b> <code>{user_id}</code>\n"
        f"• <b>Nivel:</b> {rep['level']} · <b>XP:</b> <code>{rep['xp']}</code> · <b>Puesto:</b> {rank}\n"
        f"• <b>MicVIP:</b> {'🟢 activo' if vip else '🔴 inactivo'}",
        f"🏆 <b>Award {name}</b>\n\n• <b>Community:</b> {title}\n• <b>ID:</b> <code>{user_id}</code>\n"
        f"• <b>Level:</b> {rep['level']} · <b>XP:</b> <code>{rep['xp']}</code> · <b>Rank:</b> {rank}\n"
        f"• <b>MicVIP:</b> {'🟢 active' if vip else '🔴 inactive'}",
    ) + SIG
    base = f"{chat_id}:{user_id}"
    rows = [
        [_btn(f"⚡ +{x} XP", f"amx:ax:{base}:{x}") for x in XP_PRESETS],
        [_btn(tr(lang, f"🆙 +{n} nivel" if n == 1 else f"🆙 +{n} niveles", f"🆙 +{n} level" if n == 1 else f"🆙 +{n} levels"),
              f"amx:al:{base}:{n}") for n in LEVEL_PRESETS]
        + [_btn(tr(lang, "✍️ XP libre", "✍️ Custom XP"), f"amx:xc:{base}")],
        [_btn(f"🎙️ MicVIP {_hours_label(h, lang)}", f"amx:mv:{base}:{h}") for h in MICVIP_PRESETS],
        [_btn(tr(lang, "🎟️ Membresía VIP gratuita", "🎟️ Free VIP membership"), f"amx:mb:{base}:0")],
        [_btn(tr(lang, "🔙 Top 10", "🔙 Top 10"), f"amx:aw:{chat_id}"), _home_btn(lang)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def view_membership_channels(bot: Bot, chat_id: int, user_id: int, page: int, lang: str) -> tuple:
    page = max(0, page)
    rows, total = await list_registered_chats("channel", PAGE_SIZE, page * PAGE_SIZE)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    kb = [[_btn(f"📢 {r['title'][:40]}", f"amx:mc:{chat_id}:{user_id}:{r['chat_id']}")] for r in rows]
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"amx:mb:{chat_id}:{user_id}:{page - 1}"))
    if page + 1 < pages:
        nav.append(_btn("➡️", f"amx:mb:{chat_id}:{user_id}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([_btn(tr(lang, "🔙 Usuario", "🔙 User"), f"amx:au:{chat_id}:{user_id}"), _home_btn(lang)])
    body = tr(lang, "Selecciona el canal cuya membresía VIP vas a regalar:", "Select the channel whose VIP membership you'll gift:") \
        if rows else tr(lang, "<i>No hay canales registrados.</i>", "<i>No registered channels.</i>")
    return f"🎟️ <b>{tr(lang, 'Membresía VIP gratuita', 'Free VIP membership')}</b>\n\n{body}" + SIG, InlineKeyboardMarkup(inline_keyboard=kb)


async def view_membership_plans(bot: Bot, chat_id: int, user_id: int, channel_id: int, lang: str) -> tuple:
    title = html.escape(await _chat_title(bot, channel_id))
    plans = await get_channel_plans(channel_id, only_active=True)
    kb = []
    for p in plans or []:
        plan_id, plan_name, days = p[0], p[1], p[2]
        kb.append([_btn(f"💎 {str(plan_name)[:28]} · {days} {tr(lang, 'días', 'days')}",
                        f"amx:mp:{chat_id}:{user_id}:{channel_id}:{plan_id}")])
    kb.append([_btn(tr(lang, f"📅 {d} días", f"📅 {d} days"), f"amx:md:{chat_id}:{user_id}:{channel_id}:{d}")
               for d in MEMBERSHIP_DAY_PRESETS])
    kb.append([_btn(tr(lang, "🔙 Canales", "🔙 Channels"), f"amx:mb:{chat_id}:{user_id}:0"), _home_btn(lang)])
    text = tr(
        lang,
        f"🎟️ <b>Membresía VIP — {title}</b>\n\nElige un plan del canal (usa su duración y su recurso VIP) o una duración libre. "
        "Se registra como suscripción activa sin cobro y el auditor de suscripciones la retirará al vencer.",
        f"🎟️ <b>VIP membership — {title}</b>\n\nPick a channel plan (uses its duration and VIP resource) or a free duration. "
        "It is recorded as an active, unpaid subscription and the subscription auditor removes it on expiry.",
    ) + SIG
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def view_award_result(award_id: int, chat_id: int, user_id: int, summary: str, lang: str) -> tuple:
    text = f"✅ <b>{tr(lang, 'Premio otorgado', 'Award granted')}</b>\n\n{summary}" + SIG
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn(tr(lang, "📣 Anunciar en la comunidad", "📣 Announce in the community"), f"amx:an:{award_id}")],
        [_btn(tr(lang, "🔙 Usuario", "🔙 User"), f"amx:au:{chat_id}:{user_id}"), _home_btn(lang)],
    ])
    return text, kb


async def view_log(lang: str) -> tuple:
    entries = await get_architect_audit(15)
    lines = []
    for e in entries:
        stamp = datetime.fromtimestamp(int(e["created_at"]), timezone.utc).strftime("%m-%d %H:%M")
        target = f" → <code>{e['target_id']}</code>" if e.get("target_id") else ""
        chat = f" @ <code>{e['chat_id']}</code>" if e.get("chat_id") else ""
        detail = f" · {html.escape(e['detail'][:60])}" if e.get("detail") else ""
        lines.append(f"<code>{stamp}</code> {html.escape(e['action'])}{chat}{target}{detail}")
    body = "\n".join(lines) if lines else tr(lang, "<i>Sin registros.</i>", "<i>No entries.</i>")
    text = f"📜 <b>{tr(lang, 'Bitácora del Arquitecto', 'Architect Audit Log')}</b> (UTC)\n\n{body}" + SIG
    return text, InlineKeyboardMarkup(inline_keyboard=[[_btn("🔄", "amx:log"), _home_btn(lang)]])


# ==========================================
# 🚪 ENTRADAS
# ==========================================
async def open_matrix(bot: Bot, user, chat_id: int, old_message=None) -> None:
    lang = _lang(user)
    text, kb = await view_home(lang)
    for card in ("architect", "security_matrix"):
        if await up.render_card(bot, chat_id, card, lang, kb, caption=text, old_message=old_message):
            return
    if old_message is not None:
        with contextlib.suppress(Exception):
            await old_message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
    await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("architect", "arquitecto", "matrix"), F.chat.type == "private")
async def cmd_architect(message: Message, bot: Bot):
    up.clear_user_states(bot.id, message.from_user.id)
    _clear_state(bot.id, message.from_user.id)
    with contextlib.suppress(Exception):
        await message.delete()
    await _audit(message.from_user.id, "open_matrix")
    await open_matrix(bot, message.from_user, message.chat.id)


# ==========================================
# 🎛️ DESPACHADOR DE CALLBACKS
# ==========================================
_INT_RE = re.compile(r"^-?\d{1,20}$")


def _ints(parts: list, start: int, count: int) -> Optional[list]:
    values = parts[start:start + count]
    if len(values) != count or not all(_INT_RE.match(v) for v in values):
        return None
    return [int(v) for v in values]


@router.callback_query(F.data.startswith("amx:"))
async def amx_dispatch(callback: CallbackQuery, bot: Bot):
    user = callback.from_user
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    lang = _lang(user)
    # Cualquier botón de la Matriz cancela entradas pendientes (de la Matriz y de la consola privada).
    _clear_state(bot.id, user.id)
    up.clear_user_states(bot.id, user.id)

    try:
        await _route(callback, bot, action, parts, lang)
    except TelegramBadRequest as ex:
        if "message is not modified" not in str(ex).lower():
            logger.warning(f"⚠️ [Matriz] {callback.data}: {ex}")
            with contextlib.suppress(Exception):
                await callback.answer(tr(lang, "⚠️ Telegram rechazó la acción.", "⚠️ Telegram rejected the action."), show_alert=True)
    except ValueError as ex:
        with contextlib.suppress(Exception):
            await callback.answer(f"⚠️ {str(ex)[:180]}", show_alert=True)
    except Exception as ex:
        logger.exception(f"❌ [Matriz] Error en {callback.data}: {ex}")
        with contextlib.suppress(Exception):
            await callback.answer(tr(lang, "⚠️ Error interno. Revisa los logs.", "⚠️ Internal error. Check the logs."), show_alert=True)


async def _route(callback: CallbackQuery, bot: Bot, action: str, parts: list, lang: str) -> None:
    user = callback.from_user

    # ---------- Navegación general ----------
    if action == "home":
        text, kb = await view_home(lang)
        await _show(callback, text, kb)
        return

    if action == "lang":
        new_lang = parts[2] if len(parts) > 2 and parts[2] in ("es", "en") else "es"
        AMX_LANG[user.id] = new_lang
        text, kb = await view_home(new_lang)
        await _show(callback, text, kb)
        return

    if action == "log":
        text, kb = await view_log(lang)
        await _show(callback, text, kb)
        return

    if action == "pk":
        purpose = parts[2] if len(parts) > 2 else ""
        if purpose not in PICKER_PURPOSES:
            return
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, kb = await view_picker(purpose, page, lang)
        await _show(callback, text, kb)
        return

    if action == "pm":
        purpose = parts[2] if len(parts) > 2 else ""
        if purpose not in PICKER_PURPOSES:
            return
        _set_state(bot.id, user.id, "pick_chat", purpose=purpose, lang=lang)
        prompt = tr(lang, "✍️ <b>Comunidad manual</b>\n\nEnvía el ID (ej. <code>-1001234567890</code>), un @canal público o un enlace t.me/…",
                    "✍️ <b>Manual community</b>\n\nSend the ID (e.g. <code>-1001234567890</code>), a public @channel or a t.me/… link.")
        await _show(callback, prompt + SIG, InlineKeyboardMarkup(inline_keyboard=[[_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:pk:{purpose}:0")]]))
        return

    if action == "c":
        purpose = parts[2] if len(parts) > 2 else ""
        ids = _ints(parts, 3, 1)
        if purpose not in PICKER_PURPOSES or not ids:
            return
        await _open_purpose(callback, bot, purpose, ids[0], lang)
        return

    # ---------- 🛡️ Delegados ----------
    if action == "dl":
        ids = _ints(parts, 2, 1)
        if ids:
            text, kb = await view_delegates(bot, ids[0], lang)
            await _show(callback, text, kb)
        return

    if action == "da":
        ids = _ints(parts, 2, 1)
        if not ids:
            return
        _set_state(bot.id, user.id, "dl_add", chat_id=ids[0], lang=lang)
        await _show(callback, tr(lang, "➕ <b>Nuevo delegado</b>\n\nUsa el teclado que aparece abajo.",
                                 "➕ <b>New delegate</b>\n\nUse the keyboard below.") + SIG,
                    InlineKeyboardMarkup(inline_keyboard=[[_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:dl:{ids[0]}")]]))
        await _send_user_prompt(bot, user.id, lang)
        return

    if action == "de":
        ids = _ints(parts, 2, 2)
        if not ids:
            return
        view = await view_delegate_editor(bot, ids[0], ids[1], lang)
        if view is None:
            await callback.answer(tr(lang, "⚠️ Ese delegado ya no existe.", "⚠️ That delegate no longer exists."), show_alert=True)
            text, kb = await view_delegates(bot, ids[0], lang)
        else:
            text, kb = view
        await _show(callback, text, kb)
        return

    if action == "tg":
        ids = _ints(parts, 2, 3)
        if not ids or not (0 <= ids[2] < len(DELEGATE_PERMISSIONS)):
            return
        chat_id, user_id, idx = ids
        perm = DELEGATE_PERMISSIONS[idx]
        delegate = await toggle_architect_delegate_permission(chat_id, user_id, perm)
        if delegate is None:
            await callback.answer(tr(lang, "⚠️ Ese delegado ya no existe.", "⚠️ That delegate no longer exists."), show_alert=True)
            return
        on = bool(delegate[perm])
        label = PERM_LABELS[perm][0 if lang == "es" else 1]
        await _audit(user.id, "delegate_toggle", chat_id, user_id, f"{perm}={int(on)}")
        # Solo se redibuja la botonera: el panel no parpadea.
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=delegate_toggles_keyboard(chat_id, user_id, delegate, lang))
        await callback.answer(f"{'🟢' if on else '🔴'} {label}")
        return

    if action == "ta":
        ids = _ints(parts, 2, 3)
        if not ids:
            return
        chat_id, user_id, flag = ids
        delegate = await set_all_architect_delegate_permissions(chat_id, user_id, bool(flag))
        if delegate is None:
            await callback.answer(tr(lang, "⚠️ Ese delegado ya no existe.", "⚠️ That delegate no longer exists."), show_alert=True)
            return
        await _audit(user.id, "delegate_all", chat_id, user_id, f"value={flag}")
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=delegate_toggles_keyboard(chat_id, user_id, delegate, lang))
        await callback.answer(tr(lang, "✅ Todo activado", "✅ All enabled") if flag else tr(lang, "⛔ Todo desactivado", "⛔ All disabled"))
        return

    if action == "dr":
        ids = _ints(parts, 2, 2)
        if not ids:
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [_btn(tr(lang, "✅ Sí, revocar", "✅ Yes, revoke"), f"amx:drok:{ids[0]}:{ids[1]}")],
            [_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:de:{ids[0]}:{ids[1]}")],
        ])
        await _show(callback, tr(lang, "🗑️ <b>¿Revocar a este delegado?</b>\n\nPerderá de inmediato todo acceso a la comunidad.",
                                 "🗑️ <b>Revoke this delegate?</b>\n\nThey immediately lose all access to the community.") + SIG, kb)
        return

    if action == "drok":
        ids = _ints(parts, 2, 2)
        if not ids:
            return
        removed = await remove_architect_delegate(ids[0], ids[1])
        if removed:
            await _audit(user.id, "delegate_revoke", ids[0], ids[1])
        await callback.answer(tr(lang, "🗑️ Delegado revocado.", "🗑️ Delegate revoked.") if removed
                              else tr(lang, "ℹ️ Ya no existía.", "ℹ️ It no longer existed."))
        text, kb = await view_delegates(bot, ids[0], lang)
        await _show(callback, text, kb)
        return

    # ---------- 💎 Licencias ----------
    if action == "lc":
        text, kb = view_license_menu(lang)
        await _show(callback, text, kb)
        return

    if action == "lu":
        _set_state(bot.id, user.id, "lc_user", lang=lang)
        await _show(callback, tr(lang, "👤 <b>Licencia para un usuario</b>\n\nUsa el teclado que aparece abajo.",
                                 "👤 <b>License for a user</b>\n\nUse the keyboard below.") + SIG,
                    InlineKeyboardMarkup(inline_keyboard=[[_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), "amx:lc")]]))
        await _send_user_prompt(bot, user.id, lang)
        return

    if action == "la":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        text, kb = await view_license_list(bot, page, lang)
        await _show(callback, text, kb)
        return

    if action in ("lt", "lv", "lg", "lr", "lrok"):
        scope = parts[2] if len(parts) > 2 else ""
        ids = _ints(parts, 3, 1)
        if scope not in ("c", "u") or not ids:
            return
        target_id = ids[0]
        if action == "lt":
            text, kb = await view_license_target(bot, scope, target_id, lang)
        elif action == "lv":
            tier_code = parts[4] if len(parts) > 4 else ""
            if tier_code not in ("p", "u"):
                return
            text, kb = view_license_durations(scope, target_id, tier_code, lang)
        elif action == "lg":
            tier_code = parts[4] if len(parts) > 4 else ""
            days = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else -1
            if tier_code not in ("p", "u") or days not in LICENSE_DURATIONS:
                return
            tier = "ultra_pro" if tier_code == "u" else "pro"
            grant = await grant_architect_license("chat" if scope == "c" else "user", target_id, tier, days, user.id)
            await _audit(user.id, "license_grant", target_id if scope == "c" else None,
                         target_id if scope == "u" else None, f"{tier} {days or '∞'}d")
            await callback.answer(tr(lang, f"✅ {TIER_BADGE[tier]} concedida · {_remaining(grant['expires_at'], lang)}",
                                     f"✅ {TIER_BADGE[tier]} granted · {_remaining(grant['expires_at'], lang)}"), show_alert=True)
            if scope == "u":
                _spawn(_notify_license_user(bot, target_id, grant))
            text, kb = await view_license_target(bot, scope, target_id, lang)
        elif action == "lr":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [_btn(tr(lang, "✅ Sí, revocar", "✅ Yes, revoke"), f"amx:lrok:{scope}:{target_id}")],
                [_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:lt:{scope}:{target_id}")],
            ])
            text = tr(lang, "🗑️ <b>¿Revocar la concesión?</b>\n\nLa licencia pagada con Stars (si existe) no se toca.",
                      "🗑️ <b>Revoke the grant?</b>\n\nAny Stars-paid license is left untouched.") + SIG
        else:
            revoked = await revoke_architect_license("chat" if scope == "c" else "user", target_id, user.id)
            if revoked:
                await _audit(user.id, "license_revoke", target_id if scope == "c" else None,
                             target_id if scope == "u" else None)
            await callback.answer(tr(lang, "🗑️ Concesión revocada.", "🗑️ Grant revoked.") if revoked
                                  else tr(lang, "ℹ️ No había concesión activa.", "ℹ️ There was no active grant."))
            text, kb = await view_license_target(bot, scope, target_id, lang)
        await _show(callback, text, kb)
        return

    # ---------- 🏆 Premios ----------
    if action == "aw":
        ids = _ints(parts, 2, 1)
        if ids:
            text, kb = await view_awards_chat(bot, ids[0], lang)
            await _show(callback, text, kb)
        return

    if action == "um":
        ids = _ints(parts, 2, 1)
        if not ids:
            return
        _set_state(bot.id, user.id, "aw_user", chat_id=ids[0], lang=lang)
        await _show(callback, tr(lang, "🏆 <b>Usuario a premiar</b>\n\nUsa el teclado que aparece abajo.",
                                 "🏆 <b>User to award</b>\n\nUse the keyboard below.") + SIG,
                    InlineKeyboardMarkup(inline_keyboard=[[_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:aw:{ids[0]}")]]))
        await _send_user_prompt(bot, user.id, lang)
        return

    if action == "au":
        ids = _ints(parts, 2, 2)
        if ids:
            text, kb = await view_award_user(bot, ids[0], ids[1], lang)
            await _show(callback, text, kb)
        return

    if action in ("ax", "al"):
        ids = _ints(parts, 2, 3)
        if not ids:
            return
        chat_id, user_id, amount = ids
        allowed = XP_PRESETS if action == "ax" else LEVEL_PRESETS
        if amount not in allowed:
            return
        await _grant_xp(callback, bot, chat_id, user_id, xp=amount if action == "ax" else 0,
                        levels=amount if action == "al" else 0, lang=lang)
        return

    if action == "xc":
        ids = _ints(parts, 2, 2)
        if not ids:
            return
        _set_state(bot.id, user.id, "xp_custom", chat_id=ids[0], user_id=ids[1], lang=lang)
        await _show(callback, tr(lang, f"✍️ <b>XP personalizada</b>\n\nEnvía la cantidad de XP a sumar (1 – {MAX_CUSTOM_XP:,}).",
                                 f"✍️ <b>Custom XP</b>\n\nSend the XP amount to add (1 – {MAX_CUSTOM_XP:,}).") + SIG,
                    InlineKeyboardMarkup(inline_keyboard=[[_btn(tr(lang, "❌ Cancelar", "❌ Cancel"), f"amx:au:{ids[0]}:{ids[1]}")]]))
        return

    if action == "mv":
        ids = _ints(parts, 2, 3)
        if not ids or ids[2] not in MICVIP_PRESETS:
            return
        await _grant_micvip(callback, bot, ids[0], ids[1], ids[2], lang)
        return

    if action == "mb":
        ids = _ints(parts, 2, 3)
        if ids:
            text, kb = await view_membership_channels(bot, ids[0], ids[1], ids[2], lang)
            await _show(callback, text, kb)
        return

    if action == "mc":
        ids = _ints(parts, 2, 3)
        if ids:
            text, kb = await view_membership_plans(bot, ids[0], ids[1], ids[2], lang)
            await _show(callback, text, kb)
        return

    if action in ("mp", "md"):
        ids = _ints(parts, 2, 4)
        if not ids:
            return
        chat_id, user_id, channel_id, value = ids
        if action == "mp":
            await _grant_membership(callback, bot, chat_id, user_id, channel_id, plan_id=value, days=None, lang=lang)
        elif value in MEMBERSHIP_DAY_PRESETS:
            await _grant_membership(callback, bot, chat_id, user_id, channel_id, plan_id=None, days=value, lang=lang)
        return

    if action == "an":
        ids = _ints(parts, 2, 1)
        if ids:
            await _announce_award(callback, bot, ids[0], lang)
        return


async def _open_purpose(callback: Optional[CallbackQuery], bot: Bot, purpose: str, chat_id: int, lang: str,
                        send_to: Optional[int] = None) -> None:
    if purpose == "dl":
        text, kb = await view_delegates(bot, chat_id, lang)
    elif purpose == "lc":
        text, kb = await view_license_target(bot, "c", chat_id, lang)
    else:
        text, kb = await view_awards_chat(bot, chat_id, lang)
    if callback is not None:
        await _show(callback, text, kb)
    else:
        await bot.send_message(send_to, text, reply_markup=kb, parse_mode="HTML")


# ==========================================
# 🏆 ACCIONES DE PREMIO
# ==========================================
async def _deliver(callback: Optional[CallbackQuery], bot: Bot, chat: int, text: str, kb: InlineKeyboardMarkup) -> None:
    if callback is not None:
        await _show(callback, text, kb)
    else:
        await bot.send_message(chat, text, reply_markup=kb, parse_mode="HTML")


async def _grant_xp(callback: Optional[CallbackQuery], bot: Bot, chat_id: int, user_id: int, xp: int, levels: int,
                    lang: str, architect_id: int = None, reply_chat: int = None) -> None:
    architect_id = architect_id or callback.from_user.id
    name = await _user_label(bot, chat_id, user_id)
    result = await architect_grant_xp(chat_id, user_id, full_name=name, xp=xp, levels=levels)
    amount = result["gained_xp"]
    award_type = "level" if levels else "xp"
    detail = f"+{levels} lv" if levels else f"+{xp} XP"
    award_id = await record_architect_award(chat_id, user_id, name, award_type, levels or xp, detail, architect_id)
    await _audit(architect_id, f"award_{award_type}", chat_id, user_id, detail)
    if result["leveled_up"]:
        publish_radar_event(chat_id, "level_up", {"u": user_id, "n": name[:48], "l": result["level"], "x": result["xp"]})
    summary = tr(
        lang,
        f"• <b>Usuario:</b> {html.escape(name)}\n• <b>Bonificación:</b> +{amount} XP"
        + (f" ({levels} nivel{'es' if levels > 1 else ''})" if levels else "")
        + f"\n• <b>Nivel:</b> {result['old_level']} → <b>{result['level']}</b>\n• <b>XP total:</b> <code>{result['xp']}</code>",
        f"• <b>User:</b> {html.escape(name)}\n• <b>Bonus:</b> +{amount} XP"
        + (f" ({levels} level{'s' if levels > 1 else ''})" if levels else "")
        + f"\n• <b>Level:</b> {result['old_level']} → <b>{result['level']}</b>\n• <b>Total XP:</b> <code>{result['xp']}</code>",
    )
    text, kb = view_award_result(award_id, chat_id, user_id, summary, lang)
    await _deliver(callback, bot, reply_chat, text, kb)


async def _apply_vip_tag(bot: Bot, chat_id: int, user_id: int) -> tuple:
    """
    Etiqueta VIP nativa: solo a miembros regulares de un supergrupo. Se le asciende con un único permiso
    (gestionar videochats, coherente con el pase de micrófono) y se fija el título personalizado. El worker la
    retira al vencer. Devuelve (aplicada, título, motivo_si_no).
    """
    try:
        chat = await bot.get_chat(chat_id)
        if str(getattr(chat.type, "value", chat.type)) != "supergroup":
            return False, None, "not_supergroup"
        member = await bot.get_chat_member(chat_id, user_id)
        status = str(getattr(member.status, "value", member.status) or "")
        if status in ("creator", "administrator"):
            return False, None, "already_admin"
        if status in ("left", "kicked"):
            return False, None, "not_member"
        title = (await get_vip_badge_title(chat_id) or "⚜️MIC🎙️VIP⚜️")[:16]
        await bot.promote_chat_member(
            chat_id=chat_id, user_id=user_id, is_anonymous=False, can_manage_chat=False,
            can_delete_messages=False, can_manage_video_chats=True, can_restrict_members=False,
            can_promote_members=False, can_change_info=False, can_invite_users=False, can_pin_messages=False,
        )
        await bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=user_id, custom_title=title)
        return True, title, None
    except (TelegramBadRequest, TelegramForbiddenError) as ex:
        logger.info(f"ℹ️ [Matriz] Etiqueta VIP no aplicada a {user_id} en {chat_id}: {ex}")
        return False, None, "no_rights"
    except Exception as ex:
        logger.warning(f"⚠️ [Matriz] Etiqueta VIP fallida ({chat_id}/{user_id}): {ex}")
        return False, None, "error"


async def _grant_micvip(callback: CallbackQuery, bot: Bot, chat_id: int, user_id: int, hours: int, lang: str) -> None:
    name = await _user_label(bot, chat_id, user_id)
    await grant_vip_mic(user_id, chat_id, hours)
    tag_ok, tag_title, reason = await _apply_vip_tag(bot, chat_id, user_id)
    expires_at = int(time.time()) + hours * 3600
    award_id = await record_architect_award(chat_id, user_id, name, "micvip", hours, f"MicVIP {hours}h",
                                            callback.from_user.id, expires_at, tag_ok, tag_title)
    await _audit(callback.from_user.id, "award_micvip", chat_id, user_id, f"{hours}h tag={int(tag_ok)}")
    reasons = {
        "not_supergroup": tr(lang, "la comunidad no es un supergrupo", "the community is not a supergroup"),
        "already_admin": tr(lang, "ya es administrador (se conserva su título)", "already an admin (title kept)"),
        "not_member": tr(lang, "no es miembro de la comunidad", "not a member of the community"),
        "no_rights": tr(lang, "el bot no tiene permiso para promover miembros", "the bot cannot promote members"),
        "error": tr(lang, "error de Telegram", "Telegram error"),
    }
    tag_line = (f"🟢 <code>{html.escape(tag_title)}</code>" if tag_ok
                else f"🔴 {reasons.get(reason, '—')}")
    summary = tr(
        lang,
        f"• <b>Usuario:</b> {html.escape(name)}\n• <b>Pase MicVIP:</b> {_hours_label(hours, lang)}\n"
        f"• <b>Vence:</b> {_fmt_ts(expires_at, lang)}\n• <b>Etiqueta VIP nativa:</b> {tag_line}",
        f"• <b>User:</b> {html.escape(name)}\n• <b>MicVIP pass:</b> {_hours_label(hours, lang)}\n"
        f"• <b>Expires:</b> {_fmt_ts(expires_at, lang)}\n• <b>Native VIP tag:</b> {tag_line}",
    )
    text, kb = view_award_result(award_id, chat_id, user_id, summary, lang)
    await _show(callback, text, kb)


async def _grant_membership(callback: CallbackQuery, bot: Bot, chat_id: int, user_id: int, channel_id: int,
                            plan_id: Optional[int], days: Optional[int], lang: str) -> None:
    plan = None
    if plan_id is not None:
        plan = await get_channel_plan(plan_id)
        if not plan or int(plan.get("channel_id") or 0) != channel_id:
            await callback.answer(tr(lang, "⚠️ Ese plan ya no existe en el canal.", "⚠️ That plan no longer exists in the channel."), show_alert=True)
            return
        days = int(plan.get("duration_days") or 30)
    days = int(days or 30)
    name = await _user_label(bot, chat_id, user_id)
    channel_title = await _chat_title(bot, channel_id)

    invite_link = None
    with contextlib.suppress(Exception):
        link = await bot.create_chat_invite_link(
            chat_id=channel_id, name=f"Architect VIP {user_id}"[:32], member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(days=2),
        )
        invite_link = link.invite_link
    await record_channel_subscription(channel_id, user_id, plan_id, 0, days, invite_link)
    expires_at = int(time.time()) + days * 86400
    plan_label = (plan or {}).get("plan_name") or tr(lang, "Membresía VIP", "VIP membership")
    award_id = await record_architect_award(chat_id, user_id, name, "membership", days,
                                            f"{channel_id}:{plan_id or 0}:{plan_label}"[:300], callback.from_user.id, expires_at)
    await _audit(callback.from_user.id, "award_membership", channel_id, user_id, f"{days}d plan={plan_id or '-'}")

    # Entrega privada (solo funciona si el usuario ya inició el bot; si no, el Arquitecto reenvía el enlace).
    delivered = False
    resource = (plan or {}).get("target_link")
    lines = [f"🎁 <b>{tr(lang, '¡Membresía VIP de regalo!', 'Gifted VIP membership!')}</b>\n",
             tr(lang, f"El Arquitecto de The Bunker te concedió <b>{html.escape(plan_label)}</b> en <b>{html.escape(channel_title)}</b> "
                      f"durante <b>{days} días</b>.",
                f"The Bunker Architect granted you <b>{html.escape(plan_label)}</b> in <b>{html.escape(channel_title)}</b> "
                f"for <b>{days} days</b>.")]
    rows = []
    if invite_link:
        rows.append([InlineKeyboardButton(text=tr(lang, "🔗 Entrar al canal (1 solo uso)", "🔗 Join the channel (single use)"), url=invite_link)])
    if resource:
        clean = resource if resource.startswith("http") else f"https://t.me/{resource.lstrip('@')}"
        rows.append([InlineKeyboardButton(text=tr(lang, "💎 Recurso VIP", "💎 VIP resource"), url=clean)])
    with contextlib.suppress(Exception):
        await bot.send_message(user_id, "\n".join(lines) + SIG, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
        delivered = True

    summary = tr(
        lang,
        f"• <b>Usuario:</b> {html.escape(name)}\n• <b>Canal:</b> {html.escape(channel_title)}\n"
        f"• <b>Plan:</b> {html.escape(plan_label)} · {days} días\n• <b>Vence:</b> {_fmt_ts(expires_at, lang)}\n"
        f"• <b>Enlace de invitación:</b> {('<code>' + html.escape(invite_link) + '</code>') if invite_link else '🔴 el bot no puede crear invitaciones'}\n"
        f"• <b>Aviso privado:</b> {'🟢 entregado' if delivered else '🔴 no entregado (reenvía el enlace)'}",
        f"• <b>User:</b> {html.escape(name)}\n• <b>Channel:</b> {html.escape(channel_title)}\n"
        f"• <b>Plan:</b> {html.escape(plan_label)} · {days} days\n• <b>Expires:</b> {_fmt_ts(expires_at, lang)}\n"
        f"• <b>Invite link:</b> {('<code>' + html.escape(invite_link) + '</code>') if invite_link else '🔴 the bot cannot create invites'}\n"
        f"• <b>Private notice:</b> {'🟢 delivered' if delivered else '🔴 not delivered (forward the link)'}",
    )
    text, kb = view_award_result(award_id, chat_id, user_id, summary, lang)
    await _show(callback, text, kb)


async def _announce_award(callback: CallbackQuery, bot: Bot, award_id: int, lang: str) -> None:
    award = await get_architect_award(award_id)
    if not award:
        await callback.answer(tr(lang, "⚠️ Premio no encontrado.", "⚠️ Award not found."), show_alert=True)
        return
    if not await mark_architect_award_announced(award_id):
        await callback.answer(tr(lang, "ℹ️ Este premio ya fue anunciado.", "ℹ️ This award was already announced."), show_alert=True)
        return
    name = html.escape(award["user_name"] or f"ID {award['user_id']}")
    mention = f"<a href='tg://user?id={award['user_id']}'>{name}</a>"
    kind = award["award_type"]
    amount = int(award["amount"] or 0)
    if kind == "xp":
        es_prize, en_prize = f"⚡ <b>+{amount} XP</b> de reputación", f"⚡ <b>+{amount} XP</b> reputation"
    elif kind == "level":
        es_prize, en_prize = f"🆙 <b>+{amount} nivel(es)</b> de reputación", f"🆙 <b>+{amount} reputation level(s)</b>"
    elif kind == "micvip":
        es_prize = f"🎙️ <b>Pase MicVIP de {_hours_label(amount, 'es')}</b>"
        en_prize = f"🎙️ <b>{_hours_label(amount, 'en')} MicVIP pass</b>"
    else:
        es_prize, en_prize = f"🎟️ <b>Membresía VIP de {amount} días</b>", f"🎟️ <b>{amount}-day VIP membership</b>"
    text = (
        "👑 <b>RECONOCIMIENTO OFICIAL DEL ARQUITECTO</b>\n\n"
        f"La comunidad celebra a {mention}, distinguido con {es_prize} por su aporte y lealtad al Búnker. 🏆\n\n"
        f"🇺🇸 <i>Official Architect recognition: {mention} has been awarded {en_prize}. Congratulations!</i>"
        + SIG
    )
    try:
        await bot.send_message(award["chat_id"], text, parse_mode="HTML")
    except Exception as ex:
        await unmark_architect_award_announced(award_id)
        logger.warning(f"⚠️ [Matriz] Anuncio fallido en {award['chat_id']}: {ex}")
        await callback.answer(tr(lang, "⚠️ No se pudo publicar: verifica que el bot pueda escribir en la comunidad.",
                                 "⚠️ Could not post: check the bot can write in the community."), show_alert=True)
        return
    await _audit(callback.from_user.id, "award_announce", award["chat_id"], award["user_id"], kind)
    await callback.answer(tr(lang, "📣 Anuncio publicado en la comunidad.", "📣 Announcement posted in the community."), show_alert=True)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn(tr(lang, "✅ Anunciado", "✅ Announced"), "amx:home")],
            [_btn(tr(lang, "🔙 Usuario", "🔙 User"), f"amx:au:{award['chat_id']}:{award['user_id']}"), _home_btn(lang)],
        ]))


async def _notify_license_user(bot: Bot, user_id: int, grant: dict) -> None:
    """Aviso privado al usuario premiado con una licencia personal (solo llega si ya inició el bot)."""
    tier = TIER_BADGE.get(grant["tier"], grant["tier"])
    expiry_es = "sin vencimiento" if grant.get("permanent") else f"hasta {_fmt_ts(grant['expires_at'], 'es')}"
    expiry_en = "with no expiry" if grant.get("permanent") else f"until {_fmt_ts(grant['expires_at'], 'en')}"
    with contextlib.suppress(Exception):
        await bot.send_message(
            user_id,
            f"💎 <b>Licencia {tier} concedida</b>\n\nEl Arquitecto de The Bunker activó {tier} en todas tus comunidades {expiry_es}.\n\n"
            f"🇺🇸 <i>{tier} license granted to all your communities {expiry_en}.</i>" + SIG,
            parse_mode="HTML",
        )


# ==========================================
# ⌨️ ENTRADAS CONVERSACIONALES
# ==========================================
def _user_picker_keyboard(lang: str) -> ReplyKeyboardMarkup:
    rows = []
    if KeyboardButtonRequestUsers is not None:
        try:
            request = KeyboardButtonRequestUsers(request_id=USER_PICKER_REQUEST_ID, user_is_bot=False, max_quantity=1,
                                                 request_name=True, request_username=True)
        except TypeError:   # aiogram anterior a Bot API 7.2
            request = KeyboardButtonRequestUsers(request_id=USER_PICKER_REQUEST_ID, user_is_bot=False)
        rows.append([KeyboardButton(text=tr(lang, "👤 Elegir usuario", "👤 Pick user"), request_users=request)])
    rows.append([KeyboardButton(text=tr(lang, "❌ Cancelar", "❌ Cancel"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True,
                               input_field_placeholder=tr(lang, "ID, @alias o reenvía un mensaje", "ID, @alias or forward a message"))


async def _send_user_prompt(bot: Bot, chat_id: int, lang: str) -> None:
    await bot.send_message(
        chat_id,
        tr(lang, "👤 Toca <b>Elegir usuario</b>, envía su <b>ID numérico</b> o <b>@alias</b>, o <b>reenvía un mensaje</b> suyo.",
           "👤 Tap <b>Pick user</b>, send their <b>numeric ID</b> or <b>@alias</b>, or <b>forward a message</b> from them."),
        reply_markup=_user_picker_keyboard(lang), parse_mode="HTML",
    )


_CANCEL_WORDS = {"❌ cancelar", "❌ cancel", "/cancel", "cancelar", "cancel"}
_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


async def _resolve_user_from_message(bot: Bot, message: Message) -> Optional[dict]:
    """Usuario desde el selector nativo, un reenvío, un ID numérico o un @alias conocido."""
    shared = getattr(message, "users_shared", None)
    if shared is not None:
        users = getattr(shared, "users", None)
        if users:
            u = users[0]
            name = " ".join(x for x in (getattr(u, "first_name", None), getattr(u, "last_name", None)) if x)
            return {"user_id": int(u.user_id), "full_name": name or "", "username": getattr(u, "username", None) or ""}
        ids = getattr(shared, "user_ids", None) or []
        if ids:
            return {"user_id": int(ids[0]), "full_name": "", "username": ""}
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) if origin is not None else None
    if sender is not None:
        return {"user_id": int(sender.id), "full_name": sender.full_name or "", "username": sender.username or ""}
    text = (message.text or "").strip()
    if re.fullmatch(r"\d{4,20}", text):
        return {"user_id": int(text), "full_name": "", "username": ""}
    match = _USERNAME_RE.match(text)
    if match:
        known = await find_known_user(match.group(1))
        if known:
            return known
        with contextlib.suppress(Exception):
            chat = await asyncio.wait_for(bot.get_chat(f"@{match.group(1)}"), timeout=5)
            if str(getattr(chat.type, "value", chat.type)) == "private":
                return {"user_id": int(chat.id), "full_name": chat.full_name or "", "username": chat.username or ""}
    return None


async def _resolve_chat_from_text(bot: Bot, text: str) -> Optional[int]:
    value = (text or "").strip()
    value = re.sub(r"^(https?://)?(www\.)?t\.me/", "@", value, flags=re.IGNORECASE).split("?")[0].rstrip("/")
    if re.fullmatch(r"-\d{5,20}", value):
        return int(value)
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", value):
        with contextlib.suppress(Exception):
            chat = await asyncio.wait_for(bot.get_chat(value), timeout=5)
            if str(getattr(chat.type, "value", chat.type)) != "private":
                return int(chat.id)
    return None


@router.message(F.chat.type == "private", _has_pending_state)
async def amx_state_input(message: Message, bot: Bot):
    user = message.from_user
    key = (bot.id, user.id)
    state = AMX_STATES.get(key) or {}
    kind = state.get("kind")
    lang = state.get("lang") or _lang(user)
    text = (message.text or "").strip()

    if text.lower() in _CANCEL_WORDS:
        AMX_STATES.pop(key, None)
        await _dismiss_reply_keyboard(bot, message.chat.id)
        await open_matrix(bot, user, message.chat.id)
        return

    async def retry(msg_es: str, msg_en: str) -> None:
        state["ts"] = time.time()
        await message.answer(tr(lang, msg_es, msg_en) + SIG, parse_mode="HTML")

    if kind == "pick_chat":
        chat_id = await _resolve_chat_from_text(bot, text)
        if chat_id is None:
            await retry("⚠️ No reconocí esa comunidad. Envía el ID <code>-100…</code> o un @canal público.",
                        "⚠️ I couldn't recognize that community. Send the <code>-100…</code> ID or a public @channel.")
            return
        AMX_STATES.pop(key, None)
        await _open_purpose(None, bot, state["purpose"], chat_id, lang, send_to=message.chat.id)
        return

    if kind == "xp_custom":
        if not re.fullmatch(r"\d{1,7}", text) or not (1 <= int(text) <= MAX_CUSTOM_XP):
            await retry(f"⚠️ Envía un número entero entre 1 y {MAX_CUSTOM_XP:,}.", f"⚠️ Send a whole number between 1 and {MAX_CUSTOM_XP:,}.")
            return
        AMX_STATES.pop(key, None)
        await _grant_xp(None, bot, state["chat_id"], state["user_id"], xp=int(text), levels=0, lang=lang,
                        architect_id=user.id, reply_chat=message.chat.id)
        return

    if kind in ("dl_add", "lc_user", "aw_user"):
        target = await _resolve_user_from_message(bot, message)
        if target is None:
            await retry("⚠️ No pude identificar al usuario. Usa <b>Elegir usuario</b>, su ID numérico o reenvía un mensaje suyo.",
                        "⚠️ I couldn't identify the user. Use <b>Pick user</b>, their numeric ID or forward a message from them.")
            return
        if target["user_id"] == bot.id or (kind == "dl_add" and is_super_admin(target["user_id"])):
            await retry("⚠️ Ese usuario no puede recibir esta asignación.", "⚠️ That user cannot receive this assignment.")
            return
        AMX_STATES.pop(key, None)
        await _dismiss_reply_keyboard(bot, message.chat.id)
        target_id = target["user_id"]
        label = target["full_name"] or (f"@{target['username']}" if target.get("username") else "")

        if kind == "dl_add":
            chat_id = state["chat_id"]
            label = label or await _user_label(bot, chat_id, target_id)
            await upsert_architect_delegate(chat_id, target_id, label, user.id)
            await _audit(user.id, "delegate_add", chat_id, target_id, label)
            view = await view_delegate_editor(bot, chat_id, target_id, lang)
            if view:
                await message.answer(view[0], reply_markup=view[1], parse_mode="HTML")
            return
        if kind == "lc_user":
            out_text, kb = await view_license_target(bot, "u", target_id, lang)
            await message.answer(out_text, reply_markup=kb, parse_mode="HTML")
            return
        out_text, kb = await view_award_user(bot, state["chat_id"], target_id, lang)
        await message.answer(out_text, reply_markup=kb, parse_mode="HTML")
        return

    AMX_STATES.pop(key, None)


# ==========================================
# ⏱️ WORKER: EXPIRACIÓN DE LICENCIAS Y ETIQUETAS VIP
# ==========================================
async def _clear_expired_vip_tags(bot: Bot) -> None:
    for item in await get_expired_architect_vip_tags():
        chat_id, user_id = item["chat_id"], item["user_id"]
        try:
            if await is_vip_mic_active(user_id, chat_id):
                continue     # el pase se renovó (p. ej. con Stars): se conserva la etiqueta
            member = await bot.get_chat_member(chat_id, user_id)
            status = str(getattr(member.status, "value", member.status) or "")
            same_tag = (getattr(member, "custom_title", None) or "") == (item.get("tag_title") or "")
            if status == "administrator" and same_tag and getattr(member, "can_be_edited", True):
                await bot.promote_chat_member(
                    chat_id=chat_id, user_id=user_id, is_anonymous=False, can_manage_chat=False,
                    can_delete_messages=False, can_manage_video_chats=False, can_restrict_members=False,
                    can_promote_members=False, can_change_info=False, can_invite_users=False, can_pin_messages=False,
                )
                logger.info(f"🎙️ [Matriz] Etiqueta MicVIP retirada a {user_id} en {chat_id}.")
        except Exception as ex:
            logger.info(f"ℹ️ [Matriz] No se pudo retirar la etiqueta VIP ({chat_id}/{user_id}): {ex}")
        await mark_architect_vip_tag_cleared(item["id"])


async def _notify_expired_licenses(bot: Bot, expired: list) -> None:
    for grant in expired:
        target = grant["target_id"]
        scope_es = "comunidad" if grant["scope"] == "chat" else "usuario"
        await _audit(grant.get("granted_by") or 0, "license_expired",
                     target if grant["scope"] == "chat" else None, target if grant["scope"] == "user" else None, grant["tier"])
        architect = grant.get("granted_by")
        if not architect or not is_super_admin(architect):
            continue
        name = await (_chat_title(bot, target) if grant["scope"] == "chat" else _user_label(bot, None, target))
        with contextlib.suppress(Exception):
            await bot.send_message(
                architect,
                f"⏳ <b>Licencia vencida</b>\n\nLa concesión {TIER_BADGE[grant['tier']]} de la {scope_es} "
                f"<b>{html.escape(name)}</b> (<code>{target}</code>) expiró y volvió a <b>Free</b> "
                f"(salvo licencia pagada vigente).\n\n🇺🇸 <i>Granted license expired and reverted to Free.</i>" + SIG,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    _btn("💎 Renovar / Renew", f"amx:lt:{'c' if grant['scope'] == 'chat' else 'u'}:{target}")
                ]]),
            )


async def architect_matrix_worker(bot: Bot, interval: int = WORKER_INTERVAL_SECONDS) -> None:
    """Bucle de mantenimiento: cierra licencias vencidas (con aviso) y retira etiquetas MicVIP caducadas."""
    logger.info("👑 [Matriz] Worker de expiración activo.")
    while True:
        try:
            expired = await expire_architect_licenses()
            if expired:
                logger.info(f"⏳ [Matriz] {len(expired)} licencia(s) concedida(s) vencida(s) → Free.")
                await _notify_expired_licenses(bot, expired)
            await _clear_expired_vip_tags(bot)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.warning(f"⚠️ [Matriz] Ciclo del worker fallido: {ex}")
        await asyncio.sleep(interval)


def start_architect_matrix_worker(bot: Bot):
    """Devuelve la corrutina del worker para que main.py la lance con su _spawn (referencia fuerte + logs)."""
    return architect_matrix_worker(bot)
