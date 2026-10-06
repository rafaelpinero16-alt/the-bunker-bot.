"""
channel_plans_api.py — The Bunker OS · API REST de Planes de Membresía de Canal

Expone a la Mini App las mismas acciones que el panel "💎 Planes de Membresía" del bot
(handlers/user_private.py → cb_channel_plans_dispatch):

    GET    /api/channel/{channel_id}/plans
    POST   /api/channel/{channel_id}/plan/{plan_id}/toggle
    DELETE /api/channel/{channel_id}/plan/{plan_id}
    POST   /api/channel/{channel_id}/plan/{plan_id}/broadcast
    POST   /api/channel/{channel_id}/plan/{plan_id}/invite-link
    GET    /api/channel/{channel_id}/broadcast-config            (v8.2)
    POST   /api/channel/{channel_id}/custom-broadcast            (v8.2)

v8.2 · Difusión personalizada del Estudio de Canales
----------------------------------------------------
• La configuración (chat destino, intervalo y texto promocional) vive en la tabla propia
  `channel_broadcast_config` (SQLite WAL, escrituras con BEGIN IMMEDIATE). main.py la guarda desde
  POST /api/chat/{id}/settings con `save_broadcast_config()` y la devuelve en el dashboard.
• El chat destino se resuelve contra Telegram y se exige que el operador sea creador/administrador
  de ese chat (o que sea el propio canal) y que el bot pueda publicar en él: nadie puede usar el bot
  para publicar en chats ajenos.
• "Difundir ahora" publica la configuración GUARDADA (lo mismo que muestra la vista previa) en el
  destino o, si no hay destino, en el propio canal. El texto se normaliza con telegram_html (misma
  gramática que ui.safeTelegramHtml) y lleva el botón de compra del primer plan activo, si existe.
• `run_custom_broadcast_scheduler()` repite la publicación cada N horas solo cuando el operador fijó
  un chat destino explícito; tras 3 fallos consecutivos de permisos la difusión se desactiva sola.

Diseño
------
• Capa de servicio pura (funciones async + PlanDeps): no depende de FastAPI ni de aiogram en
  tiempo de importación, de modo que se prueba con dobles. `build_channel_plans_router` es una
  envoltura fina que traduce PlanApiError → HTTPException.
• Solo usa funciones de base de datos que el bot ya utiliza: get_channel_plans, get_channel_plan,
  toggle_channel_plan_status, delete_channel_plan y get_active_subscribers_count.
• Autorización: SOLO el propietario del canal (assert_chat_ownership) o un superadmin. Se trata de
  acciones comerciales, por lo que no basta ser administrador vivo del chat.
• Anti-IDOR: cada acción verifica que el plan pertenezca al canal de la URL; si no, responde 404 sin
  revelar si el plan existe en otro canal.
• `invite-link` devuelve el ENLACE DE COMPRA del plan (t.me/<bot>?start=chanplan_<plan>_<canal>), el
  mismo que difunde el bot. NO acuña enlaces de unión al canal: tras un pago el bot genera un enlace de
  un solo uso (member_limit=1); un endpoint que los acuñara sin pago permitiría saltarse la compra.
• Al pausar o eliminar un plan se invalida la caché del pre-checkout (handlers.payments) para que
  deje de poder comprarse al instante.
• La difusión tiene enfriamiento por (canal, plan) para impedir publicaciones duplicadas por doble
  toque o reintentos, y solo se permite con el plan activo.

Integración en main.py (antes de `app.include_router(api_router, prefix="/api")`):

    from channel_plans_api import build_channel_plans_router
    api_router.include_router(build_channel_plans_router(
        require_user=require_authenticated_user,
        assert_owner=assert_chat_ownership,
        get_bot=lambda: master_bot_instance,
    ))

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from telegram_html import normalize_telegram_html, visible_length

logger = logging.getLogger("bunker.channel_plans")

PERIMETER_SIGNATURE = "\n\n🛡️ <i>Cloud Media Management</i>"
MEDIA_TYPES = ("photo", "video", "animation")
CAPTION_LIMIT = 1024
FALLBACK_BOT_USERNAME = "TheBunkerBot"   # el bot usa este mismo respaldo si get_me() no trae username


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


BROADCAST_COOLDOWN_SECONDS = max(5.0, _env_float("PLAN_BROADCAST_COOLDOWN", 30.0))


class PlanApiError(Exception):
    """Error de negocio con código HTTP y mensaje legible para la Mini App."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = int(status)
        self.detail = str(detail)


# ==========================================
# 🔌 DEPENDENCIAS DE DATOS (inyectables)
# ==========================================
@dataclass
class PlanDeps:
    get_channel_plans: Callable[..., Awaitable[Any]]
    get_channel_plan: Callable[[int], Awaitable[Any]]
    toggle_channel_plan_status: Callable[[int], Awaitable[Any]]
    delete_channel_plan: Callable[[int], Awaitable[Any]]
    get_active_subscribers_count: Optional[Callable[[int], Awaitable[Any]]] = None
    invalidate_plan_cache: Optional[Callable[[Optional[int]], Any]] = None
    db_connect: Optional[Callable[[], Any]] = None   # database.database.get_db_connection (v8.2)


def load_db_deps() -> PlanDeps:
    """Resuelve las funciones reales de database.database (importación perezosa: sin ciclos con main)."""
    import database.database as db  # type: ignore[import-not-found]

    invalidate = None
    try:
        from handlers.payments import invalidate_channel_plan_cache  # type: ignore[import-not-found]
        invalidate = invalidate_channel_plan_cache
    except Exception as ex:  # pragma: no cover - depende del despliegue
        logger.debug("Sin caché de pre-checkout que invalidar: %s", ex)

    return PlanDeps(
        get_channel_plans=db.get_channel_plans,
        get_channel_plan=db.get_channel_plan,
        toggle_channel_plan_status=db.toggle_channel_plan_status,
        delete_channel_plan=db.delete_channel_plan,
        get_active_subscribers_count=getattr(db, "get_active_subscribers_count", None),
        invalidate_plan_cache=invalidate,
        db_connect=getattr(db, "get_db_connection", None),
    )


# ==========================================
# 🧩 NORMALIZACIÓN
# ==========================================
def _norm_status(value: Any) -> str:
    """El bot considera activo solo el valor exacto "active"; cualquier otro es pausado."""
    return "active" if str(value or "").strip().lower() == "active" else "paused"


def _plan_from_row(row: Any) -> Dict[str, Any]:
    """Respaldo con la tupla de get_channel_plans: [0]=id [1]=nombre [2]=días [3]=Stars [4]=estado [9]=enlace."""
    seq = list(row)
    return {
        "plan_id": seq[0],
        "plan_name": seq[1] if len(seq) > 1 else "",
        "duration_days": seq[2] if len(seq) > 2 else 0,
        "stars_price": seq[3] if len(seq) > 3 else 0,
        "status": seq[4] if len(seq) > 4 else "paused",
        "target_link": (seq[9] if len(seq) > 9 else "") or "",
    }


def serialize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Representación pública de un plan. No expone media_id (identificador interno de Telegram)."""
    media_type = str(plan.get("media_type") or "")
    has_media = bool(plan.get("media_id")) and media_type in MEDIA_TYPES
    status = _norm_status(plan.get("status"))
    return {
        "plan_id": int(plan["plan_id"]),
        "name": str(plan.get("plan_name") or ""),
        "duration_days": int(plan.get("duration_days") or 0),
        "stars_price": int(plan.get("stars_price") or 0),
        "status": status,
        "is_active": status == "active",
        "promo_text": str(plan.get("promo_text") or ""),
        "target_link": str(plan.get("target_link") or ""),
        "media_type": media_type if has_media else "",
        "has_media": has_media,
    }


async def _load_owned_plan(channel_id: int, plan_id: int, deps: PlanDeps) -> Dict[str, Any]:
    """Plan verificado contra el canal de la URL (anti-IDOR). 404 genérico si no coincide."""
    plan = await deps.get_channel_plan(int(plan_id))
    try:
        belongs = bool(plan) and int(plan.get("channel_id") or 0) == int(channel_id)
    except (TypeError, ValueError):
        belongs = False
    if not belongs:
        raise PlanApiError(404, "Plan no encontrado en este canal.")
    return plan


def _invalidate(deps: PlanDeps, plan_id: Optional[int]) -> None:
    if deps.invalidate_plan_cache is None:
        return
    try:
        deps.invalidate_plan_cache(plan_id)
    except Exception as ex:  # la caché es una optimización: nunca debe romper la acción
        logger.debug("No se pudo invalidar la caché del plan %s: %s", plan_id, ex)


# ==========================================
# 📋 LISTADO
# ==========================================
async def list_plans(channel_id: int, deps: PlanDeps) -> Dict[str, Any]:
    rows = await deps.get_channel_plans(int(channel_id), only_active=False) or []

    async def _detail(row: Any) -> Optional[Dict[str, Any]]:
        try:
            full = await deps.get_channel_plan(int(row[0]))
            if full and int(full.get("channel_id") or 0) == int(channel_id):
                return full
        except Exception as ex:
            logger.debug("Detalle del plan %s no disponible, se usa la fila: %s", row[0], ex)
        return _plan_from_row(row)

    details = await asyncio.gather(*[_detail(row) for row in rows])
    plans = [serialize_plan(d) for d in details if d]
    plans.sort(key=lambda p: (not p["is_active"], p["plan_id"]))

    subscribers = None
    if deps.get_active_subscribers_count is not None:
        try:
            subscribers = int(await deps.get_active_subscribers_count(int(channel_id)))
        except Exception as ex:
            logger.debug("Sin conteo de suscriptores para %s: %s", channel_id, ex)

    return {
        "channel_id": str(channel_id),
        "plans": plans,
        "total": len(plans),
        "active_count": sum(1 for p in plans if p["is_active"]),
        "subscribers": subscribers,
    }


# ==========================================
# 🔄 ACTIVAR / PAUSAR · 🗑️ ELIMINAR
# ==========================================
async def toggle_plan(channel_id: int, plan_id: int, deps: PlanDeps) -> Dict[str, Any]:
    await _load_owned_plan(channel_id, plan_id, deps)
    returned = await deps.toggle_channel_plan_status(int(plan_id))
    _invalidate(deps, plan_id)   # un plan pausado debe dejar de cobrarse YA, no al vencer el TTL de 10 s

    if isinstance(returned, str) and returned:
        new_status = _norm_status(returned)
    else:
        fresh = await deps.get_channel_plan(int(plan_id))
        new_status = _norm_status((fresh or {}).get("status"))
    return {"status": "success", "plan_id": int(plan_id), "new_status": new_status, "is_active": new_status == "active"}


async def delete_plan(channel_id: int, plan_id: int, deps: PlanDeps) -> Dict[str, Any]:
    await _load_owned_plan(channel_id, plan_id, deps)
    await deps.delete_channel_plan(int(plan_id))
    _invalidate(deps, plan_id)
    _BROADCAST_LAST.pop((int(channel_id), int(plan_id)), None)
    return {"status": "success", "plan_id": int(plan_id), "deleted": True}


# ==========================================
# 🔗 ENLACE DE COMPRA ("ENLACE VIP" con entrega automática)
# ==========================================
_BOT_USERNAME_CACHE: Dict[int, str] = {}


async def _bot_username(bot: Any) -> str:
    key = int(getattr(bot, "id", 0) or 0)
    cached = _BOT_USERNAME_CACHE.get(key)
    if cached:
        return cached
    me = await bot.get_me()
    username = (getattr(me, "username", "") or "").strip() or FALLBACK_BOT_USERNAME
    _BOT_USERNAME_CACHE[key] = username
    return username


def purchase_link(bot_username: str, plan_id: int, channel_id: int) -> str:
    """Mismo formato que difunde el bot y que resuelve el handler /start chanplan_<plan>_<canal>."""
    return f"https://t.me/{bot_username}?start=chanplan_{int(plan_id)}_{int(channel_id)}"


async def generate_invite_link(channel_id: int, plan_id: int, deps: PlanDeps, bot: Any) -> Dict[str, Any]:
    if bot is None:
        raise PlanApiError(503, "El bot maestro no está disponible en este momento.")
    plan = await _load_owned_plan(channel_id, plan_id, deps)
    if _norm_status(plan.get("status")) != "active":
        raise PlanApiError(409, "Activa el plan primero: el enlace de un plan pausado no permite comprarlo.")
    username = await _bot_username(bot)
    return {
        "status": "success",
        "plan_id": int(plan_id),
        "link": purchase_link(username, plan_id, channel_id),
        "kind": "purchase",
    }


# ==========================================
# 📢 DIFUSIÓN AL CANAL
# ==========================================
_BROADCAST_LAST: Dict[Tuple[int, int], float] = {}


def _clean_lang(lang: Any) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "es"


def _announcement_text(plan: Dict[str, Any], lang: str, promo_html: str) -> str:
    name = html.escape(str(plan.get("plan_name") or ""))
    days = int(plan.get("duration_days") or 0)
    stars = int(plan.get("stars_price") or 0)
    days_word = "days" if lang == "en" else "días"
    duration_label = "Duration" if lang == "en" else "Duración"
    price_label = "Price" if lang == "en" else "Precio"
    return (
        f"💎 <b>{name}</b>\n\n"
        f"⏳ <b>{duration_label}:</b> {days} {days_word}\n"
        f"⭐ <b>{price_label}:</b> {stars} XTR\n\n"
        f"{promo_html}"
    ) + PERIMETER_SIGNATURE


async def _send_announcement(bot: Any, chat_id: int, plan: Dict[str, Any], buy_url: str, lang: str, promo_html: str) -> Optional[int]:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # importación perezosa

    stars = int(plan.get("stars_price") or 0)
    button_text = f"⭐ Get Access ({stars} Stars)" if lang == "en" else f"⭐ Obtener Acceso ({stars} Stars)"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=buy_url)]])
    body = _announcement_text(plan, lang, promo_html)

    media_id = plan.get("media_id")
    media_type = plan.get("media_type")
    if media_id and media_type in MEDIA_TYPES:
        send_media = getattr(bot, f"send_{media_type}")
        # Telegram cuenta el texto ya parseado (sin etiquetas) en unidades UTF-16, no len(html).
        if visible_length(body) <= CAPTION_LIMIT:
            sent = await send_media(chat_id=chat_id, **{media_type: media_id}, caption=body, reply_markup=keyboard, parse_mode="HTML")
        else:
            await send_media(chat_id=chat_id, **{media_type: media_id})
            sent = await bot.send_message(chat_id=chat_id, text=body, reply_markup=keyboard, parse_mode="HTML")
    else:
        sent = await bot.send_message(chat_id=chat_id, text=body, reply_markup=keyboard, parse_mode="HTML")
    return getattr(sent, "message_id", None)


def _classify_telegram_error(ex: Exception) -> PlanApiError:
    text = f"{type(ex).__name__} {ex}".lower()
    retry_after = getattr(ex, "retry_after", None)
    if retry_after:
        return PlanApiError(429, f"Telegram pidió esperar {int(retry_after)} s antes de volver a publicar.")
    if any(token in text for token in ("forbidden", "not enough rights", "chat not found", "not a member", "need administrator", "have no rights")):
        return PlanApiError(502, "El bot no puede publicar en este canal. Verifica que sea administrador con permiso para publicar mensajes.")
    return PlanApiError(502, "Telegram rechazó la publicación del anuncio. Inténtalo de nuevo en unos segundos.")


def _prune_cooldowns(now: float) -> None:
    if len(_BROADCAST_LAST) < 500:
        return
    for key in [k for k, ts in _BROADCAST_LAST.items() if now - ts > BROADCAST_COOLDOWN_SECONDS]:
        _BROADCAST_LAST.pop(key, None)


async def broadcast_plan(channel_id: int, plan_id: int, deps: PlanDeps, bot: Any, lang: Any = "es",
                         clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Publica el anuncio comercial del plan en el PROPIO canal, con botón de pago en Stars."""
    if bot is None:
        raise PlanApiError(503, "El bot maestro no está disponible en este momento.")
    lang = _clean_lang(lang)
    plan = await _load_owned_plan(channel_id, plan_id, deps)
    if _norm_status(plan.get("status")) != "active":
        raise PlanApiError(409, "Activa el plan antes de difundirlo: un plan pausado no se puede comprar.")

    key = (int(channel_id), int(plan_id))
    now = clock()
    last = _BROADCAST_LAST.get(key)
    if last is not None and now - last < BROADCAST_COOLDOWN_SECONDS:
        wait = int(BROADCAST_COOLDOWN_SECONDS - (now - last)) + 1
        raise PlanApiError(429, f"Este plan se difundió hace poco. Espera {wait} s para publicarlo de nuevo.")
    # Se reserva ANTES de enviar: dos toques concurrentes no pueden publicar dos veces.
    _prune_cooldowns(now)
    _BROADCAST_LAST[key] = now

    try:
        buy_url = purchase_link(await _bot_username(bot), plan_id, channel_id)
        # El HTML del operador se normaliza con la MISMA gramática que la vista previa (ui.safeTelegramHtml):
        # lo que se ve en la Mini App es lo que se publica, y Telegram ya no puede rechazarlo por
        # "can't parse entities". (Antes, ese error disparaba un reintento que, en el caso de caption
        # largo, volvía a enviar la foto: el canal recibía el medio duplicado y las etiquetas en crudo.)
        promo = normalize_telegram_html(plan.get("promo_text") or "")
        message_id = await _send_announcement(bot, int(channel_id), plan, buy_url, lang, promo)
    except PlanApiError:
        _BROADCAST_LAST.pop(key, None)
        raise
    except Exception as ex:
        _BROADCAST_LAST.pop(key, None)   # un fallo no debe consumir el enfriamiento
        logger.warning("❌ [Planes] Difusión fallida plan=%s canal=%s: %s", plan_id, channel_id, ex)
        raise _classify_telegram_error(ex)

    logger.info("📢 [Planes] Plan %s difundido en el canal %s (mensaje %s).", plan_id, channel_id, message_id)
    return {"status": "success", "plan_id": int(plan_id), "target_chat_id": str(channel_id), "message_id": message_id, "sent": True}


# ==========================================
# 📡 v8.2 · DIFUSIÓN PERSONALIZADA DEL ESTUDIO (chat destino · intervalo · texto)
# ==========================================
BROADCAST_INTERVALS = (6, 12, 24, 48)
DEFAULT_BROADCAST_INTERVAL = 12
PROMO_TEXT_MAX = 1000                # texto crudo del operador (el mensaje final cabe de sobra en 4096)
MESSAGE_LIMIT = 4096
BROADCAST_TARGET_CACHE_TTL = 120.0
BROADCAST_MAX_FAILURES = 3
_ALIAS_RE_TEXT = r"^@[A-Za-z][A-Za-z0-9_]{4,31}$"
_CUSTOM_BROADCAST_KEY = -1           # clave de enfriamiento (canal, -1): no colisiona con ids de plan (> 0)
_TARGET_CACHE: Dict[Tuple[int, str], Tuple[float, Dict[str, Any]]] = {}

_BROADCAST_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS channel_broadcast_config ("
    "channel_id INTEGER PRIMARY KEY, "
    "target_chat_id INTEGER, "
    "target_label TEXT NOT NULL DEFAULT '', "
    "interval_hours INTEGER NOT NULL DEFAULT 12, "
    "promo_text TEXT NOT NULL DEFAULT '', "
    "enabled INTEGER NOT NULL DEFAULT 0, "
    "last_sent_at INTEGER, "
    "last_message_id INTEGER, "
    "failures INTEGER NOT NULL DEFAULT 0, "
    "updated_by INTEGER, "
    "updated_at INTEGER NOT NULL)"
)
_BROADCAST_SCHEMA_READY = False


def _empty_broadcast_config(channel_id: int) -> Dict[str, Any]:
    return {
        "channel_id": int(channel_id), "target_chat_id": None, "broadcast_target": "",
        "broadcast_interval": DEFAULT_BROADCAST_INTERVAL, "promo_text": "", "enabled": False,
        "last_sent_at": None, "last_message_id": None, "failures": 0,
    }


def _row_to_broadcast_config(row: Any) -> Dict[str, Any]:
    seq = list(row)
    return {
        "channel_id": int(seq[0]),
        "target_chat_id": int(seq[1]) if seq[1] is not None else None,
        "broadcast_target": str(seq[2] or ""),
        "broadcast_interval": int(seq[3] or DEFAULT_BROADCAST_INTERVAL),
        "promo_text": str(seq[4] or ""),
        "enabled": bool(seq[5]),
        "last_sent_at": int(seq[6]) if seq[6] is not None else None,
        "last_message_id": int(seq[7]) if seq[7] is not None else None,
        "failures": int(seq[8] or 0),
    }


def _ensure_broadcast_schema(conn: Any) -> None:
    global _BROADCAST_SCHEMA_READY
    if _BROADCAST_SCHEMA_READY:
        return
    conn.execute(_BROADCAST_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_broadcast_enabled ON channel_broadcast_config (enabled, last_sent_at)")
    _BROADCAST_SCHEMA_READY = True


_BROADCAST_COLUMNS = ("channel_id, target_chat_id, target_label, interval_hours, promo_text, enabled, "
                      "last_sent_at, last_message_id, failures")


def _sync_load_broadcast_config(connect: Callable[[], Any], channel_id: int) -> Dict[str, Any]:
    with connect() as conn:
        _ensure_broadcast_schema(conn)
        row = conn.execute(f"SELECT {_BROADCAST_COLUMNS} FROM channel_broadcast_config WHERE channel_id = ?",
                           (int(channel_id),)).fetchone()
    return _row_to_broadcast_config(row) if row else _empty_broadcast_config(channel_id)


def _sync_save_broadcast_config(connect: Callable[[], Any], channel_id: int, target_chat_id: Optional[int],
                                target_label: str, interval_hours: int, promo_text: str, user_id: int) -> Dict[str, Any]:
    """
    Guarda la configuración en una transacción BEGIN IMMEDIATE. Si cambian destino, intervalo o texto,
    el reloj del programador se reinicia (last_sent_at = ahora): guardar no publica nada por sí solo;
    para publicar al instante está "Difundir ahora".
    """
    now = int(time.time())
    enabled = 1 if (target_chat_id is not None and promo_text.strip()) else 0
    with connect() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_broadcast_schema(conn)
            prev = conn.execute(
                "SELECT target_chat_id, interval_hours, promo_text, last_sent_at FROM channel_broadcast_config WHERE channel_id = ?",
                (int(channel_id),)).fetchone()
            changed = prev is None or (prev[0], int(prev[1] or 0), str(prev[2] or "")) != (target_chat_id, int(interval_hours), promo_text)
            last_sent = now if changed else (prev[3] if prev else None)
            conn.execute(
                "INSERT INTO channel_broadcast_config (channel_id, target_chat_id, target_label, interval_hours, "
                "promo_text, enabled, last_sent_at, failures, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET target_chat_id = excluded.target_chat_id, "
                "target_label = excluded.target_label, interval_hours = excluded.interval_hours, "
                "promo_text = excluded.promo_text, enabled = excluded.enabled, "
                "last_sent_at = excluded.last_sent_at, failures = 0, updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at",
                (int(channel_id), target_chat_id, target_label, int(interval_hours), promo_text, enabled,
                 last_sent, int(user_id), now))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _sync_load_broadcast_config(connect, channel_id)


def _sync_mark_broadcast_result(connect: Callable[[], Any], channel_id: int, ok: bool,
                                message_id: Optional[int], disable: bool = False) -> None:
    with connect() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_broadcast_schema(conn)
            if ok:
                conn.execute("UPDATE channel_broadcast_config SET last_sent_at = ?, last_message_id = ?, failures = 0 "
                             "WHERE channel_id = ?", (int(time.time()), message_id, int(channel_id)))
            else:
                conn.execute("UPDATE channel_broadcast_config SET failures = failures + 1, "
                             "enabled = CASE WHEN ? THEN 0 ELSE enabled END, last_sent_at = ? WHERE channel_id = ?",
                             (1 if disable else 0, int(time.time()), int(channel_id)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _sync_due_broadcasts(connect: Callable[[], Any], now: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        _ensure_broadcast_schema(conn)
        rows = conn.execute(
            f"SELECT {_BROADCAST_COLUMNS} FROM channel_broadcast_config WHERE enabled = 1 "
            "AND target_chat_id IS NOT NULL AND (last_sent_at IS NULL OR last_sent_at + interval_hours * 3600 <= ?) "
            "ORDER BY last_sent_at LIMIT 20", (int(now),)).fetchall()
    return [_row_to_broadcast_config(r) for r in rows]


def _require_connect(deps: PlanDeps) -> Callable[[], Any]:
    if deps.db_connect is None:
        raise PlanApiError(503, "La base de datos de difusión no está disponible.")
    return deps.db_connect


async def load_broadcast_config(channel_id: int, deps: PlanDeps) -> Dict[str, Any]:
    return await asyncio.to_thread(_sync_load_broadcast_config, _require_connect(deps), int(channel_id))


def public_broadcast_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Forma que consume la Mini App (sin ids internos de mensajes)."""
    return {
        "broadcast_target": cfg.get("broadcast_target") or "",
        "broadcast_interval": int(cfg.get("broadcast_interval") or DEFAULT_BROADCAST_INTERVAL),
        "promo_text": cfg.get("promo_text") or "",
        "broadcast_enabled": bool(cfg.get("enabled")),
        "broadcast_last_sent_at": cfg.get("last_sent_at"),
    }


def validate_broadcast_fields(payload: Dict[str, Any]) -> Tuple[str, int, str]:
    """Valida la forma (sin red). Devuelve (destino_crudo, intervalo, texto). Errores → PlanApiError 422."""
    import re as _re
    raw_target = str(payload.get("broadcast_target") or "").strip()
    if raw_target and not (_re.match(_ALIAS_RE_TEXT, raw_target) or _re.match(r"^-?\d{5,20}$", raw_target)):
        raise PlanApiError(422, "broadcast_target: usa un @alias público (5-32 caracteres) o el ID numérico del chat.")
    try:
        interval = int(payload.get("broadcast_interval") or DEFAULT_BROADCAST_INTERVAL)
    except (TypeError, ValueError):
        interval = -1
    if interval not in BROADCAST_INTERVALS:
        raise PlanApiError(422, f"broadcast_interval: valores permitidos {', '.join(map(str, BROADCAST_INTERVALS))} horas.")
    promo = str(payload.get("promo_text") or "").replace("\r\n", "\n").strip()
    if len(promo) > PROMO_TEXT_MAX:
        raise PlanApiError(422, f"promo_text: máximo {PROMO_TEXT_MAX} caracteres.")
    if raw_target and not promo:
        raise PlanApiError(422, "promo_text: escribe el texto promocional para difundir en el chat destino.")
    return raw_target, interval, promo


async def resolve_broadcast_target(bot: Any, user_id: int, raw_target: str, channel_id: int,
                                   is_chat_admin: Callable[[int, int], Awaitable[bool]]) -> Dict[str, Any]:
    """
    Resuelve @alias/ID contra Telegram y exige: (1) que el operador sea creador/administrador del destino
    (o que el destino sea el propio canal, ya verificado como suyo) y (2) que el bot pueda publicar ahí.
    Resultado cacheado 120 s por (usuario, destino): el guardado reactivo no martillea la Bot API.
    """
    if bot is None:
        raise PlanApiError(503, "broadcast_target: el bot maestro no está disponible para verificar el destino.")
    key = (int(user_id), raw_target.lower())
    now = time.monotonic()
    cached = _TARGET_CACHE.get(key)
    if cached and now - cached[0] < BROADCAST_TARGET_CACHE_TTL:
        return cached[1]

    lookup: Any = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
    try:
        chat = await asyncio.wait_for(bot.get_chat(lookup), timeout=6)
    except Exception:
        raise PlanApiError(422, "broadcast_target: no se encontró el chat. Añade el bot y verifica el @alias o ID.")
    target_id = int(chat.id)
    chat_type = str(getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", "")) or "")
    if chat_type == "private":
        raise PlanApiError(422, "broadcast_target: el destino debe ser un canal o grupo, no un chat privado.")

    if target_id != int(channel_id) and not await is_chat_admin(int(user_id), target_id):
        raise PlanApiError(403, "broadcast_target: debes ser creador o administrador del chat destino.")

    try:
        me = await bot.get_me()
        member = await asyncio.wait_for(bot.get_chat_member(target_id, me.id), timeout=6)
    except Exception:
        raise PlanApiError(422, "broadcast_target: el bot no es miembro del chat destino.")
    status = str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", "")) or "")
    if chat_type == "channel":
        can_post = status == "creator" or (status == "administrator" and bool(getattr(member, "can_post_messages", False)))
    else:
        can_post = status in ("creator", "administrator", "member") or (
            status == "restricted" and bool(getattr(member, "can_send_messages", False)))
    if not can_post:
        raise PlanApiError(422, "broadcast_target: el bot no tiene permiso para publicar en el chat destino.")

    label = f"@{chat.username}" if getattr(chat, "username", None) else str(target_id)
    result = {"target_chat_id": target_id, "target_label": label, "title": getattr(chat, "title", "") or label}
    if len(_TARGET_CACHE) > 5000:
        _TARGET_CACHE.clear()
    _TARGET_CACHE[key] = (now, result)
    return result


async def save_broadcast_config(channel_id: int, user_id: int, payload: Dict[str, Any], deps: PlanDeps, bot: Any,
                                is_chat_admin: Callable[[int, int], Awaitable[bool]]) -> Dict[str, Any]:
    """Valida (forma + Telegram) y persiste. Llamar ANTES de guardar el resto de ajustes: si falla, nada se guarda."""
    raw_target, interval, promo = validate_broadcast_fields(payload)
    target_chat_id: Optional[int] = None
    label = ""
    if raw_target:
        resolved = await resolve_broadcast_target(bot, user_id, raw_target, channel_id, is_chat_admin)
        target_chat_id, label = resolved["target_chat_id"], raw_target
    cfg = await asyncio.to_thread(_sync_save_broadcast_config, _require_connect(deps), int(channel_id),
                                  target_chat_id, label, interval, promo, int(user_id))
    return public_broadcast_config(cfg)


def _custom_announcement(promo_html: str) -> str:
    return f"{promo_html}{PERIMETER_SIGNATURE}"


async def _first_active_plan(channel_id: int, deps: PlanDeps) -> Optional[Dict[str, Any]]:
    try:
        rows = await deps.get_channel_plans(int(channel_id), only_active=True) or []
    except Exception:
        return None
    for row in rows:
        try:
            plan = await deps.get_channel_plan(int(row[0]))
        except Exception:
            plan = None
        if plan and int(plan.get("channel_id") or 0) == int(channel_id) and _norm_status(plan.get("status")) == "active":
            return plan
    return None


async def _send_custom_broadcast(bot: Any, chat_id: int, channel_id: int, promo_text: str, deps: PlanDeps, lang: str) -> Optional[int]:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # importación perezosa

    body = _custom_announcement(normalize_telegram_html(promo_text))
    if visible_length(body) > MESSAGE_LIMIT:
        raise PlanApiError(422, "promo_text: el anuncio supera el límite de 4096 caracteres de Telegram.")
    keyboard = None
    plan = await _first_active_plan(channel_id, deps)
    if plan:
        stars = int(plan.get("stars_price") or 0)
        text = f"⭐ Get Access ({stars} Stars)" if lang == "en" else f"⭐ Obtener Acceso ({stars} Stars)"
        url = purchase_link(await _bot_username(bot), int(plan["plan_id"]), int(channel_id))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])
    sent = await bot.send_message(chat_id=int(chat_id), text=body, reply_markup=keyboard, parse_mode="HTML")
    return getattr(sent, "message_id", None)


async def broadcast_custom_now(channel_id: int, deps: PlanDeps, bot: Any, lang: Any = "es",
                               clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Publica YA la difusión guardada (destino configurado o, si no hay, el propio canal)."""
    if bot is None:
        raise PlanApiError(503, "El bot maestro no está disponible en este momento.")
    lang = _clean_lang(lang)
    cfg = await load_broadcast_config(channel_id, deps)
    promo = cfg.get("promo_text") or ""
    if not promo.strip():
        raise PlanApiError(409, "Guarda primero un texto promocional en el Estudio del canal.")
    target = cfg.get("target_chat_id") or int(channel_id)

    key = (int(channel_id), _CUSTOM_BROADCAST_KEY)
    now = clock()
    last = _BROADCAST_LAST.get(key)
    if last is not None and now - last < BROADCAST_COOLDOWN_SECONDS:
        wait = int(BROADCAST_COOLDOWN_SECONDS - (now - last)) + 1
        raise PlanApiError(429, f"La difusión se publicó hace poco. Espera {wait} s para repetirla.")
    _prune_cooldowns(now)
    _BROADCAST_LAST[key] = now
    try:
        message_id = await _send_custom_broadcast(bot, int(target), int(channel_id), promo, deps, lang)
    except PlanApiError:
        _BROADCAST_LAST.pop(key, None)
        raise
    except Exception as ex:
        _BROADCAST_LAST.pop(key, None)
        logger.warning("❌ [Difusión] Publicación inmediata fallida canal=%s destino=%s: %s", channel_id, target, ex)
        raise _classify_telegram_error(ex)

    if cfg.get("target_chat_id"):
        # Una publicación manual al destino programado reinicia su reloj (no se duplica a los pocos minutos).
        with _suppress_log("No se pudo registrar la difusión manual"):
            await asyncio.to_thread(_sync_mark_broadcast_result, _require_connect(deps), int(channel_id), True, message_id)
    logger.info("📡 [Difusión] Canal %s publicó su promoción en %s (mensaje %s).", channel_id, target, message_id)
    return {"status": "success", "sent": True, "target_chat_id": str(target), "message_id": message_id,
            "target_label": cfg.get("broadcast_target") or str(channel_id)}


class _suppress_log:
    """Context manager: registra (sin propagar) un fallo no crítico."""

    def __init__(self, what: str):
        self.what = what

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            logger.warning("⚠️ [Difusión] %s: %s", self.what, exc)
        return True


def _is_permission_error(ex: Exception) -> bool:
    text = f"{type(ex).__name__} {ex}".lower()
    return any(t in text for t in ("forbidden", "not enough rights", "chat not found", "have no rights",
                                    "need administrator", "bot was kicked", "not a member"))


async def run_custom_broadcast_scheduler(get_bot: Callable[[], Any], get_deps: Callable[[], PlanDeps] = load_db_deps,
                                         tick_seconds: float = 60.0, stop: Optional[asyncio.Event] = None) -> None:
    """
    Programador de la difusión automática. Una sola réplica (requisito de SQLite): sin coordinación
    distribuida. Cada minuto publica las configuraciones vencidas (máx. 20 por ciclo, 1 s entre envíos).
    """
    deps: Optional[PlanDeps] = None
    logger.info("📡 [Difusión] Programador de difusión personalizada activo (ciclo %ss).", int(tick_seconds))
    while stop is None or not stop.is_set():
        try:
            bot = get_bot()
            if deps is None:
                deps = get_deps()
            if bot is not None and deps.db_connect is not None:
                due = await asyncio.to_thread(_sync_due_broadcasts, deps.db_connect, int(time.time()))
                for cfg in due:
                    channel_id = int(cfg["channel_id"])
                    try:
                        message_id = await _send_custom_broadcast(bot, int(cfg["target_chat_id"]), channel_id,
                                                                  cfg["promo_text"], deps, "es")
                        await asyncio.to_thread(_sync_mark_broadcast_result, deps.db_connect, channel_id, True, message_id)
                        logger.info("📡 [Difusión] Automática canal=%s → %s (mensaje %s).", channel_id, cfg["target_chat_id"], message_id)
                    except Exception as ex:
                        retry_after = getattr(ex, "retry_after", None)
                        if retry_after:
                            await asyncio.sleep(min(float(retry_after), 60.0))
                            continue
                        disable = _is_permission_error(ex) and int(cfg.get("failures") or 0) + 1 >= BROADCAST_MAX_FAILURES
                        await asyncio.to_thread(_sync_mark_broadcast_result, deps.db_connect, channel_id, False, None, disable)
                        logger.warning("⚠️ [Difusión] Automática fallida canal=%s: %s%s", channel_id, ex,
                                       " (desactivada tras fallos repetidos)" if disable else "")
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.warning("⚠️ [Difusión] Ciclo del programador con error: %s", ex)
        try:
            if stop is not None:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            else:
                await asyncio.sleep(tick_seconds)
        except asyncio.TimeoutError:
            pass


# ==========================================
# 🌐 ROUTER FASTAPI (envoltura fina)
# ==========================================
def _parse_channel_id(raw: Any) -> int:
    text = str(raw).strip()
    if not text.lstrip("-").isdigit() or not text.startswith("-"):
        raise PlanApiError(400, "channel_id inválido: debe ser el id numérico (negativo) de un canal.")
    return int(text)


def build_channel_plans_router(
    require_user: Callable[[Optional[str], Optional[str]], int],
    assert_owner: Callable[[int, int], Awaitable[None]],
    get_bot: Callable[[], Any],
    get_deps: Callable[[], PlanDeps] = load_db_deps,
):
    """
    Crea el APIRouter. `require_user(init_data, authorization)` debe devolver el user_id firmado o lanzar
    HTTPException 401; `assert_owner(user_id, chat_id)` lanza HTTPException 403 si no es el propietario.
    """
    from fastapi import APIRouter, Body, Header, HTTPException  # type: ignore[import-not-found,reportMissingImports]

    router = APIRouter()
    state: Dict[str, Optional[PlanDeps]] = {"deps": None}

    def deps() -> PlanDeps:
        if state["deps"] is None:
            state["deps"] = get_deps()
        return state["deps"]  # type: ignore[return-value]

    async def authorize(channel_id: str, init_data: Optional[str], authorization: Optional[str]) -> int:
        user_id = require_user(init_data, authorization)
        cid = _parse_channel_id(channel_id)
        await assert_owner(user_id, cid)
        return cid

    async def guarded(action: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
        try:
            return await action()
        except PlanApiError as err:
            raise HTTPException(status_code=err.status, detail=err.detail)
        except HTTPException:
            raise
        except Exception:
            logger.error("❌ [Planes API] Error inesperado", exc_info=True)
            raise HTTPException(status_code=500, detail="Error interno procesando el plan.")

    @router.get("/channel/{channel_id}/plans")
    async def api_list_plans(
        channel_id: str,
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            return await list_plans(cid, deps())
        return await guarded(run)

    @router.post("/channel/{channel_id}/plan/{plan_id}/toggle")
    async def api_toggle_plan(
        channel_id: str,
        plan_id: int,
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            return await toggle_plan(cid, plan_id, deps())
        return await guarded(run)

    @router.delete("/channel/{channel_id}/plan/{plan_id}")
    async def api_delete_plan(
        channel_id: str,
        plan_id: int,
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            return await delete_plan(cid, plan_id, deps())
        return await guarded(run)

    @router.post("/channel/{channel_id}/plan/{plan_id}/broadcast")
    async def api_broadcast_plan(
        channel_id: str,
        plan_id: int,
        payload: dict = Body(default=None),
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            lang = (payload or {}).get("lang", "es") if isinstance(payload, dict) else "es"
            return await broadcast_plan(cid, plan_id, deps(), get_bot(), lang=lang)
        return await guarded(run)

    @router.get("/channel/{channel_id}/broadcast-config")
    async def api_broadcast_config(
        channel_id: str,
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            return {"status": "success", "channel_id": str(cid), **public_broadcast_config(await load_broadcast_config(cid, deps()))}
        return await guarded(run)

    @router.post("/channel/{channel_id}/custom-broadcast")
    async def api_custom_broadcast(
        channel_id: str,
        payload: dict = Body(default=None),
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            lang = (payload or {}).get("lang", "es") if isinstance(payload, dict) else "es"
            return await broadcast_custom_now(cid, deps(), get_bot(), lang=lang)
        return await guarded(run)

    @router.post("/channel/{channel_id}/plan/{plan_id}/invite-link")
    async def api_plan_invite_link(
        channel_id: str,
        plan_id: int,
        x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
        authorization: str = Header(None),
    ):
        async def run():
            cid = await authorize(channel_id, x_telegram_init_data, authorization)
            return await generate_invite_link(cid, plan_id, deps(), get_bot())
        return await guarded(run)

    return router