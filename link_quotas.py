"""
link_quotas.py — The Bunker OS · v23 · Cuota diaria de enlaces / publicaciones promocionales por administrador

El DUEÑO fija cuántas publicaciones promocionales (mensajes con enlaces web, enlaces de Telegram o enlaces camuflados
y, opcionalmente, reenvíos de canales / grupos) puede publicar cada administrador en una ventana MÓVIL de 24 h. Puede
fijar un límite general y excepciones por administrador (0 = ninguna, -1 = sin límite).

Al superarse: el bot borra la publicación y avisa al administrador (por privado si es posible; si no, con un aviso
temporal en el grupo) indicando que alcanzó su límite y el tiempo restante para volver a promocionar.

Alcance: SOLO administradores comunes (tier 'admin'). El dueño, el Arquitecto, las cuentas de servicio y el Centinela
nunca se limitan. Las ediciones solo cuentan si INYECTAN un enlace que el mensaje no tenía (huella de media_locks).

Memoria: _cfg_cache y _notice_last con TTL + tamaño máximo + purge_state(). Telegram siempre con wait_for/try.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from typing import Optional

logger = logging.getLogger("link_quotas")

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
TG_TIMEOUT_S = 8
CFG_TTL_S = 30
CFG_MAX = 2000
NOTICE_COOLDOWN_S = 120
NOTICE_MAX = 5000
PROMO_VECTORS = frozenset({"masked_links", "tg_links"})
FORWARD_VECTOR = "forward_any"

_cfg_cache: dict = {}       # group -> (monotonic, cfg)
_notice_last: dict = {}     # (group, admin, kind) -> monotonic
_bg: set = set()


def _db():
    import database.database as db
    return db


def _spawn(coro, name: str = "link_quotas"):
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError:
        return None
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return task


def invalidate(group_id: int) -> None:
    _cfg_cache.pop(int(group_id), None)


def purge_state() -> dict:
    now = time.monotonic()
    for key in [k for k, (ts, _c) in list(_cfg_cache.items()) if now - ts > CFG_TTL_S]:
        _cfg_cache.pop(key, None)
    for key in [k for k, ts in list(_notice_last.items()) if now - ts > NOTICE_COOLDOWN_S]:
        _notice_last.pop(key, None)
    return {"cfg": len(_cfg_cache), "notices": len(_notice_last)}


async def get_config(group_id: int) -> dict:
    now = time.monotonic()
    hit = _cfg_cache.get(int(group_id))
    if hit and now - hit[0] < CFG_TTL_S:
        return hit[1]
    cfg = await _db().get_admin_link_quota_config(int(group_id))
    if len(_cfg_cache) >= CFG_MAX:
        purge_state()
        if len(_cfg_cache) >= CFG_MAX:
            _cfg_cache.clear()
    _cfg_cache[int(group_id)] = (now, cfg)
    return cfg


def promo_vectors(message, bot_id: Optional[int] = None) -> set:
    """Vectores promocionales del mensaje: {'masked_links', 'tg_links', 'forward_any'} ∩ lo detectado."""
    from handlers import media_locks
    found = media_locks.detect_vectors(message, 10 ** 9, bot_id=bot_id)
    out = {k for k, _d in found if k in PROMO_VECTORS}
    origin = getattr(message, "forward_origin", None)
    kind = type(origin).__name__ if origin is not None else ""
    # Solo cuentan como "promoción" los reenvíos de canales y grupos (no los de usuarios).
    if any(k == FORWARD_VECTOR for k, _d in found) and ("Channel" in kind or "Chat" in kind):
        out.add(FORWARD_VECTOR)
    return out


def fmt_remaining(seconds: int, lang: str) -> str:
    seconds = max(60, int(seconds))
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def _texts(lang: str, used: int, limit, remaining_s: Optional[int], group_title: str) -> dict:
    title = html.escape(group_title or "")
    when_es = f"Podrás volver a promocionar en <b>{fmt_remaining(remaining_s, 'es')}</b>." if remaining_s else \
        "Tu cuota actual es 0: pide al dueño que la amplíe."
    when_en = f"You can promote again in <b>{fmt_remaining(remaining_s, 'en')}</b>." if remaining_s else \
        "Your current quota is 0: ask the owner to raise it."
    return {
        "dm": {
            "es": (f"🔗 <b>Límite diario de enlaces alcanzado</b>\n\nEn <b>{title}</b> ya publicaste <b>{used}/{limit}</b> "
                   f"enlaces o promociones en las últimas 24 h, así que tu última publicación fue retirada.\n{when_es}" + SIG),
            "en": (f"🔗 <b>Daily link limit reached</b>\n\nIn <b>{title}</b> you already posted <b>{used}/{limit}</b> links or "
                   f"promotions in the last 24 h, so your latest post was removed.\n{when_en}" + SIG),
        },
        "last": {
            "es": (f"🔗 <b>Última promoción del día</b>\n\nEn <b>{title}</b> usaste <b>{used}/{limit}</b> enlaces de tu cuota "
                   f"de 24 h. La siguiente publicación con enlaces se retirará automáticamente.{SIG}"),
            "en": (f"🔗 <b>Last promotion for today</b>\n\nIn <b>{title}</b> you used <b>{used}/{limit}</b> links of your 24 h "
                   f"quota. Your next post with links will be removed automatically.{SIG}"),
        },
    }


def _throttled(group_id: int, admin_id: int, kind: str) -> bool:
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
    with contextlib.suppress(Exception):
        db = _db()
        stored = await db.get_user_lang(int(user_id))
        return stored or db.DEFAULT_LANG
    return "en"


async def _notify(bot, message, result: dict, kind: str) -> None:
    group_id, admin = message.chat.id, message.from_user
    if _throttled(group_id, admin.id, kind):
        return
    lang = await _user_lang(admin.id)
    remaining_s = (int(result["reset_at"]) - int(time.time())) if result.get("reset_at") else None
    texts = _texts(lang, result["used"], result["limit"], remaining_s, getattr(message.chat, "title", "") or "")
    try:
        await asyncio.wait_for(bot.send_message(admin.id, texts[kind]["es" if lang == "es" else "en"], parse_mode="HTML"),
                               timeout=TG_TIMEOUT_S)
        return
    except Exception as ex:
        logger.debug(f"[LinkQuota] Aviso privado no entregado a {admin.id}: {ex}")
    if kind != "dm":
        return   # el aviso informativo solo va por privado; el de bloqueo sí cae al grupo
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
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(bot.delete_message(chat_id=chat_id, message_id=message_id), timeout=TG_TIMEOUT_S)


def _publish(group_id: int, admin_id: int, result: dict) -> None:
    with contextlib.suppress(Exception):
        from radar_bus import publish_radar_event  # type: ignore[import-not-found]
        publish_radar_event(int(group_id), "link_quota", {"a": int(admin_id), "u": int(result.get("used") or 0),
                                                           "l": result.get("limit"), "b": not result.get("allowed")})


async def enforce_admin_link_quota(bot, message, tier: str, *, is_edit: bool = False) -> bool:
    """
    Gancho de groups.group_security_matrix para miembros privilegiados. Devuelve True si la publicación se retiró
    (el pipeline debe detenerse). Nunca lanza.
    """
    if tier != "admin" or message is None or not getattr(message, "from_user", None):
        return False
    from handlers import media_locks
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
    previous = media_locks.original_vectors(group_id, message.message_id) if is_edit else None
    media_locks.remember_fingerprint(group_id, message.message_id, promo | (previous or frozenset()))
    if not promo:
        return False
    if is_edit and (previous is None or previous & promo):
        # Sin huella (reinicio) o el enlace ya estaba: la publicación ya se contó (o no se puede saber). No se toca.
        return False

    kind = "forward" if promo == {FORWARD_VECTOR} else "link"
    try:
        result = await _db().admin_link_quota_consume(group_id, admin_id, message_id=message.message_id, kind=kind)
    except Exception as ex:
        logger.error(f"❌ [LinkQuota] Consumo de cuota fallido en {group_id}: {ex}")
        return False
    _publish(group_id, admin_id, result)
    if result.get("allowed"):
        if result.get("remaining") == 0 and not result.get("unlimited"):
            _spawn(_notify(bot, message, result, "last"), name="alq_last_notice")
        return False

    try:
        await asyncio.wait_for(message.delete(), timeout=TG_TIMEOUT_S)
        deleted = True
    except Exception as ex:
        deleted = False
        logger.warning(f"⚠️ [LinkQuota] El bot no pudo retirar la publicación de {admin_id} en {group_id}: {ex}")
    with contextlib.suppress(Exception):
        from handlers import groups as g
        g.track_security(group_id, admin_id, "antispam", "delete", False)
    logger.info(f"🔗 [LinkQuota] Admin {admin_id} superó su cuota en {group_id} ({result['used']}/{result['limit']}).")
    _spawn(_notify(bot, message, result, "dm"), name="alq_block_notice")
    return deleted


# --------------------------------------------------------------------------
# Lectura para la consola del bot / Mini App
# --------------------------------------------------------------------------
async def get_overview(bot, group_id: int) -> dict:
    """Configuración + uso de cada administrador (lista de Telegram, sin el creador) en la ventana de 24 h."""
    db = _db()
    cfg = await db.get_admin_link_quota_config(int(group_id))
    usage = await db.admin_link_usage_summary(int(group_id))
    admins = []
    try:
        members = await asyncio.wait_for(bot.get_chat_administrators(int(group_id)), timeout=TG_TIMEOUT_S)
    except Exception as ex:
        logger.debug(f"[LinkQuota] Lista de administradores no disponible en {group_id}: {ex}")
        members = []
    now = int(time.time())
    seen = set()
    for m in members:
        user = getattr(m, "user", None)
        status = str(getattr(getattr(m, "status", ""), "value", getattr(m, "status", "")) or "")
        if user is None or getattr(user, "is_bot", False) or status == "creator":
            continue
        seen.add(str(user.id))
        admins.append(_admin_row(cfg, usage, str(user.id), user.full_name or "", user.username or "", now))
    # Administradores con excepción guardada o uso registrado que ya no figuran (p. ej. degradados).
    for admin_id in set(cfg["overrides"]) | set(usage):
        if admin_id not in seen:
            admins.append(_admin_row(cfg, usage, admin_id, "", "", now, listed=False))
    return {"config": cfg, "admins": admins, "checked_at": now}


def _admin_row(cfg: dict, usage: dict, admin_id: str, name: str, username: str, now: int, listed: bool = True) -> dict:
    limit = cfg["overrides"].get(admin_id, cfg["default_limit"])
    u = usage.get(admin_id, {})
    used = int(u.get("used", 0))
    reset_at = None
    if limit != -1 and used >= limit and u.get("oldest_at"):
        reset_at = int(u["oldest_at"]) + int(cfg.get("window_seconds", 86400))
    return {"admin_id": admin_id, "full_name": name, "username": username, "limit": limit,
            "override": admin_id in cfg["overrides"], "used": used, "blocked": int(u.get("blocked", 0)),
            "last_at": u.get("last_at"), "reset_at": reset_at if reset_at and reset_at > now else None, "listed": listed}
