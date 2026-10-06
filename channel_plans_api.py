"""
channel_plans_api.py — The Bunker OS · API REST de Planes de Membresía de Canal

Expone a la Mini App las mismas acciones que el panel "💎 Planes de Membresía" del bot
(handlers/user_private.py → cb_channel_plans_dispatch):

    GET    /api/channel/{channel_id}/plans
    POST   /api/channel/{channel_id}/plan/{plan_id}/toggle
    DELETE /api/channel/{channel_id}/plan/{plan_id}
    POST   /api/channel/{channel_id}/plan/{plan_id}/broadcast
    POST   /api/channel/{channel_id}/plan/{plan_id}/invite-link

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
    _invalidate(deps, plan_id)   # un plan pausado debe dejar de cobrarse YA, no en 60 s

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
        if len(body) <= CAPTION_LIMIT:
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
        promo = str(plan.get("promo_text") or "")
        try:
            message_id = await _send_announcement(bot, int(channel_id), plan, buy_url, lang, promo)
        except Exception as first_error:
            # El texto promocional lo escribe el operador y puede traer HTML inválido: se reintenta escapado.
            if "parse entities" in str(first_error).lower():
                message_id = await _send_announcement(bot, int(channel_id), plan, buy_url, lang, html.escape(promo))
            else:
                raise
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
    from fastapi import APIRouter, Body, Header, HTTPException  # importación perezosa

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
