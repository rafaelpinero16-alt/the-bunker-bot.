"""
link_quotas.py — The Bunker OS · v23/v41.1 · Cuota diaria de enlaces y promociones para administradores

El DUEÑO fija cuántas publicaciones promocionales (mensajes con enlaces web, enlaces de Telegram o enlaces camuflados
y, opcionalmente, reenvíos de canales / grupos) puede publicar cada administrador en una ventana MÓVIL de 24 h. Puede
fijar un límite general y excepciones por administrador (0 = ninguna, -1 = sin límite).

Al superarse: el bot borra la publicación y avisa al administrador (por privado si es posible; si no, con un aviso
temporal en el grupo) indicando que alcanzó su límite y el tiempo restante para volver a promocionar.

Alcance: SOLO administradores comunes (tier 'admin'). El dueño, el Arquitecto, las cuentas de servicio y el Centinela
nunca se limitan. Las ediciones solo cuentan si INYECTAN un enlace que el mensaje no tenía.

Memoria: _cfg_cache y _notice_last con TTL + tamaño máximo + purge_state().
Sincronización con Aiogram 3.x, asyncio.to_thread para I/O SQLite y manejo robusto de excepciones de Telegram.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger("link_quotas")

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
TG_TIMEOUT_S = 8
CFG_TTL_S = 30
CFG_MAX = 2000
NOTICE_COOLDOWN_S = 120
NOTICE_MAX = 5000
PROMO_VECTORS = frozenset({"masked_links", "tg_links"})
FORWARD_VECTOR = "forward_any"

_cfg_cache: Dict[int, Tuple[float, dict]] = {}     # group_id -> (monotonic_ts, cfg_dict)
_notice_last: Dict[Tuple[int, int, str], float] = {} # (group_id, admin_id, kind) -> monotonic_ts
_bg: Set[asyncio.Task] = set()


def _db():
    import database.database as db
    return db


def _spawn(coro, name: str = "link_quotas") -> Optional[asyncio.Task]:
    """Crea una tarea en segundo plano de manera segura contra la recolección de basura."""
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError:
        return None
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return task


def invalidate(group_id: int) -> None:
    """Invalida la caché de configuración para una comunidad específica."""
    _cfg_cache.pop(int(group_id), None)


def purge_state() -> dict:
    """Purga entradas obsoletas en la caché para evitar fuga de memoria bajo alto tráfico."""
    now = time.monotonic()
    for key in [k for k, (ts, _c) in list(_cfg_cache.items()) if now - ts > CFG_TTL_S]:
        _cfg_cache.pop(key, None)
    for key in [k for k, ts in list(_notice_last.items()) if now - ts > NOTICE_COOLDOWN_S]:
        _notice_last.pop(key, None)
    return {"cfg": len(_cfg_cache), "notices": len(_notice_last)}


async def get_config(group_id: int) -> dict:
    """Obtiene la configuración de cuota de una comunidad de forma asíncrona con caché en memoria."""
    now = time.monotonic()
    gid = int(group_id)
    hit = _cfg_cache.get(gid)
    if hit and now - hit[0] < CFG_TTL_S:
        return hit[1]
    
    db = _db()
    # Ejecución asíncrona adaptada para no bloquear el Event Loop con SQLite
    if hasattr(db, "get_admin_link_quota_config_async"):
        cfg = await db.get_admin_link_quota_config_async(gid)
    else:
        cfg = await asyncio.to_thread(db.get_admin_link_quota_config, gid)

    if len(_cfg_cache) >= CFG_MAX:
        purge_state()
        if len(_cfg_cache) >= CFG_MAX:
            _cfg_cache.clear()
            
    _cfg_cache[gid] = (now, cfg)
    return cfg


def promo_vectors(message, bot_id: Optional[int] = None) -> set:
    """Detecta los vectores promocionales del mensaje: {'masked_links', 'tg_links', 'forward_any'}."""
    try:
        from handlers import media_locks
        found = media_locks.detect_vectors(message, 10 ** 9, bot_id=bot_id)
        out = {k for k, _d in found if k in PROMO_VECTORS}
    except Exception:
        out = set()

    origin = getattr(message, "forward_origin", None)
    kind = type(origin).__name__ if origin is not None else ""
    
    # Evaluar reenvíos desde canales y grupos (no chats privados de usuarios)
    if origin is not None and ("Channel" in kind or "Chat" in kind):
        out.add(FORWARD_VECTOR)
    elif getattr(message, "forward_from_chat", None) is not None:
        out.add(FORWARD_VECTOR)
        
    return out


def fmt_remaining(seconds: int, lang: str = "es") -> str:
    """Formatea la cuenta regresiva en un formato legible en español o inglés."""
    seconds = max(60, int(seconds))
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def _texts(lang: str, used: int, limit: Any, remaining_s: Optional[int], group_title: str) -> dict:
    title = html.escape(group_title or "")
    limit_str = "∞" if limit == -1 else str(limit)
    
    when_es = f"Podrás volver a promocionar en <b>{fmt_remaining(remaining_s, 'es')}</b>." if remaining_s else \
        "Tu cuota actual es 0: pide al dueño que la amplíe."
    when_en = f"You can promote again in <b>{fmt_remaining(remaining_s, 'en')}</b>." if remaining_s else \
        "Your current quota is 0: ask the owner to raise it."
        
    return {
        "dm": {
            "es": (f"🔗 <b>Límite diario de enlaces alcanzado</b>\n\nEn <b>{title}</b> ya publicaste <b>{used}/{limit_str}</b> "
                   f"enlaces o promociones en las últimas 24 h, así que tu última publicación fue retirada.\n{when_es}" + SIG),
            "en": (f"🔗 <b>Daily link limit reached</b>\n\nIn <b>{title}</b> you already posted <b>{used}/{limit_str}</b> links or "
                   f"promotions in the last 24 h, so your latest post was removed.\n{when_en}" + SIG),
        },
        "last": {
            "es": (f"🔗 <b>Última promoción del día</b>\n\nEn <b>{title}</b> usaste <b>{used}/{limit_str}</b> enlaces de tu cuota "
                   f"de 24 h. La siguiente publicación con enlaces se retirará automáticamente.{SIG}"),
            "en": (f"🔗 <b>Last promotion for today</b>\n\nIn <b>{title}</b> you used <b>{used}/{limit_str}</b> links of your 24 h "
                   f"quota. Your next post with links will be removed automatically.{SIG}"),
        },
    }


def _throttled(group_id: int, admin_id: int, kind: str) -> bool:
    """Evita saturar al administrador con notificaciones duplicadas en poco tiempo."""
    key = (int(group_id), int(admin_id), kind)
    now = time.monotonic()
    if now - _notice_last.get(key, 0) < NOTICE_COOLDOWN_S:
        return True
    if len(_notice_last) >= NOTICE_MAX:
        purge_state()
        if len(_notice_last) >= NOTICE_MAX:
            _notice_last.clear()
    _notice_last[key] = now
    return False


async def _user_lang(user_id: int) -> str:
    """Obtiene el idioma preferido del usuario desde la base de datos."""
    with contextlib.suppress(Exception):
        db = _db()
        if hasattr(db, "get_user_lang_async"):
            stored = await db.get_user_lang_async(int(user_id))
        else:
            stored = await asyncio.to_thread(db.get_user_lang, int(user_id))
        return stored or getattr(db, "DEFAULT_LANG", "es")
    return "es"


async def _notify(bot, message, result: dict, kind: str) -> None:
    """Envía la notificación de advertencia o bloqueo al administrador por DM o en el grupo."""
    group_id, admin = message.chat.id, message.from_user
    if _throttled(group_id, admin.id, kind):
        return
        
    lang = await _user_lang(admin.id)
    remaining_s = (int(result["reset_at"]) - int(time.time())) if result.get("reset_at") else None
    group_title = getattr(message.chat, "title", "") or ""
    texts = _texts(lang, result["used"], result["limit"], remaining_s, group_title)
    
    # 1. Intentar enviar mensaje privado al administrador
    try:
        await asyncio.wait_for(
            bot.send_message(admin.id, texts[kind]["es" if lang == "es" else "en"], parse_mode="HTML"),
            timeout=TG_TIMEOUT_S
        )
        return
    except Exception as ex:
        logger.debug(f"[LinkQuota] Aviso privado no entregado a {admin.id}: {ex}")

    if kind != "dm":
        return   # Los avisos preventivos ("last") solo se envían por privado

    # 2. Si el DM falla, enviar aviso temporal en el grupo
    mention = admin.mention_html() if hasattr(admin, "mention_html") else f"<a href='tg://user?id={admin.id}'>{admin.id}</a>"
    when_es = f"Vuelve a intentarlo en {fmt_remaining(remaining_s, 'es')}." if remaining_s else "Cuota actual: 0."
    when_en = f"Try again in {fmt_remaining(remaining_s, 'en')}." if remaining_s else "Current quota: 0."
    
    text = (f"🔗 <b>Cuota de enlaces / Link quota</b>\n{mention} alcanzó su límite diario de enlaces "
            f"(<b>{result['used']}/{result['limit']}</b>). {when_es}\n"
            f"🇺🇸 <i>Daily link limit reached ({result['used']}/{result['limit']}). {when_en}</i>" + SIG)
    try:
        notice = await asyncio.wait_for(bot.send_message(group_id, text, parse_mode="HTML"), timeout=TG_TIMEOUT_S)
        _spawn(_delete_later(bot, group_id, notice.message_id, 30), name="alq_notice_cleanup")
    except Exception as ex:
        logger.debug(f"[LinkQuota] Aviso de grupo no enviado en {group_id}: {ex}")


async def _delete_later(bot, chat_id: int, message_id: int, delay: int) -> None:
    """Elimina un mensaje tras un retardo específico."""
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(bot.delete_message(chat_id=chat_id, message_id=message_id), timeout=TG_TIMEOUT_S)


def _publish(group_id: int, admin_id: int, result: dict) -> None:
    """Publica el evento en el bus de telemetría de Live Radar."""
    with contextlib.suppress(Exception):
        from radar_bus import publish_radar_event  # type: ignore[import-not-found]
        publish_radar_event(int(group_id), "link_quota", {
            "a": int(admin_id),
            "u": int(result.get("used") or 0),
            "l": result.get("limit"),
            "b": not result.get("allowed")
        })


async def enforce_admin_link_quota(bot, message, tier: str, *, is_edit: bool = False) -> bool:
    """
    Gancho principal para la matriz de seguridad perimetral.
    Devuelve True si el mensaje con enlace superó la cuota y fue eliminado.
    """
    if tier != "admin" or message is None or not getattr(message, "from_user", None):
        return False

    group_id, admin_id = message.chat.id, message.from_user.id
    
    try:
        cfg = await get_config(group_id)
    except Exception as ex:
        logger.debug(f"[LinkQuota] Configuración no disponible en {group_id}: {ex}")
        return False

    if not cfg.get("enabled"):
        return False

    try:
        promo = promo_vectors(message, bot_id=getattr(bot, "id", None))
    except Exception as ex:
        logger.debug(f"[LinkQuota] Inspección fallida en {group_id}: {ex}")
        return False

    if FORWARD_VECTOR in promo and not cfg.get("count_forwards"):
        promo.discard(FORWARD_VECTOR)

    # Lógica de huellas para mensajes editados
    previous = None
    try:
        from handlers import media_locks
        previous = media_locks.original_vectors(group_id, message.message_id) if is_edit else None
        media_locks.remember_fingerprint(group_id, message.message_id, promo | (previous or frozenset()))
    except Exception:
        pass

    if not promo:
        return False

    if is_edit and (previous is None or previous & promo):
        # Si ya contenía enlace antes de editar, no vuelve a consumir cuota
        return False

    kind = "forward" if promo == {FORWARD_VECTOR} else "link"
    db = _db()

    try:
        if hasattr(db, "admin_link_quota_consume_async"):
            result = await db.admin_link_quota_consume_async(group_id, admin_id, message_id=message.message_id, kind=kind)
        else:
            result = await asyncio.to_thread(db.admin_link_quota_consume, group_id, admin_id, message_id=message.message_id, kind=kind)
    except Exception as ex:
        logger.error(f"❌ [LinkQuota] Consumo de cuota fallido en {group_id}: {ex}")
        return False

    _publish(group_id, admin_id, result)

    # Permitido dentro del límite
    if result.get("allowed"):
        if result.get("remaining") == 0 and not result.get("unlimited"):
            _spawn(_notify(bot, message, result, "last"), name="alq_last_notice")
        return False

    # Excedido: proceder con la eliminación del mensaje
    deleted = False
    try:
        await asyncio.wait_for(message.delete(), timeout=TG_TIMEOUT_S)
        deleted = True
    except Exception as ex:
        logger.warning(f"⚠️ [LinkQuota] El bot no pudo retirar la publicación de {admin_id} en {group_id}: {ex}")

    with contextlib.suppress(Exception):
        from handlers import groups as g
        if hasattr(g, "track_security"):
            g.track_security(group_id, admin_id, "antispam", "delete", False)

    logger.info(f"🔗 [LinkQuota] Admin {admin_id} superó su cuota en {group_id} ({result.get('used')}/{result.get('limit')}).")
    _spawn(_notify(bot, message, result, "dm"), name="alq_block_notice")
    
    return deleted


# --------------------------------------------------------------------------
# Lectura para la consola del bot / Mini App
# --------------------------------------------------------------------------
async def get_overview(bot, group_id: int) -> dict:
    """Configuración y resumen de uso de cada administrador en las últimas 24 horas."""
    db = _db()
    gid = int(group_id)

    if hasattr(db, "get_admin_link_quota_config_async"):
        cfg = await db.get_admin_link_quota_config_async(gid)
        usage = await db.admin_link_usage_summary_async(gid)
    else:
        cfg = await asyncio.to_thread(db.get_admin_link_quota_config, gid)
        usage = await asyncio.to_thread(db.admin_link_usage_summary, gid)

    admins = []
    try:
        members = await asyncio.wait_for(bot.get_chat_administrators(gid), timeout=TG_TIMEOUT_S)
    except Exception as ex:
        logger.debug(f"[LinkQuota] Lista de administradores no disponible en {gid}: {ex}")
        members = []

    now = int(time.time())
    seen = set()

    for m in members:
        user = getattr(m, "user", None)
        status_val = getattr(m, "status", "")
        status = str(getattr(status_val, "value", status_val) or "")
        
        if user is None or getattr(user, "is_bot", False) or status == "creator":
            continue
            
        uid_str = str(user.id)
        seen.add(uid_str)
        admins.append(_admin_row(cfg, usage, uid_str, user.full_name or "", user.username or "", now))

    # Incluir administradores con excepciones u orden histórica que ya no estén listados
    for admin_id in set(cfg.get("overrides", {})) | set(usage):
        if admin_id not in seen:
            admins.append(_admin_row(cfg, usage, admin_id, "", "", now, listed=False))

    return {"config": cfg, "admins": admins, "checked_at": now}


def _admin_row(cfg: dict, usage: dict, admin_id: str, name: str, username: str, now: int, listed: bool = True) -> dict:
    overrides = cfg.get("overrides", {})
    default_limit = cfg.get("default_limit", 3)
    limit = overrides.get(admin_id, default_limit)
    
    u = usage.get(admin_id, {})
    used = int(u.get("used", 0))
    reset_at = None
    
    if limit != -1 and used >= limit and u.get("oldest_at"):
        reset_at = int(u["oldest_at"]) + int(cfg.get("window_seconds", 86400))

    return {
        "admin_id": admin_id,
        "full_name": name,
        "username": username,
        "limit": limit,
        "override": admin_id in overrides,
        "used": used,
        "blocked": int(u.get("blocked", 0)),
        "last_at": u.get("last_at"),
        "reset_at": reset_at if reset_at and reset_at > now else None,
        "listed": listed
    }