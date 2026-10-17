"""
vip_roles.py — The Bunker OS · v23 · Roles y etiquetas VIP automáticos post-donación / pase

Flujo (sin intervención manual):
  cobro en Stars (main.ActivityTrackerMiddleware) ─┬─ donación (payload tip_…)  → on_payment() → vip_role_grant('donation')
                                                    └─ MicVIP (pase verificado) → micvip_audit (listener) → vip_role_grant('micvip')
  → etiqueta nativa de Telegram (setChatMemberTag, sin ascender a administrador: el donante NO gana inmunidad)
  → privilegios que la matriz perimetral consulta en caliente (get_active_perks):
       • exenciones PARCIALES de vectores de contenido inofensivo (nunca enlaces, reenvíos, botones ni bots),
       • margen extra en el anti-flood (×flood_bonus) y
       • inmunidad a AutoLower (prioridad en el videochat).
  → vencimiento automático (vip_sweeper_loop): rol expirado + etiqueta retirada + aviso al radar.

Memoria: _perks_cache y _cfg_cache con TTL y tamaño máximo; _notice_sent con purga. Todas las llamadas a Telegram con
asyncio.wait_for y try/except. Nada de datos personales en los logs (solo ids numéricos).

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import time
from typing import Callable, Optional

logger = logging.getLogger("vip_roles")

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
TG_TIMEOUT_S = 8
SWEEP_INTERVAL_S = int(os.getenv("VIP_ROLES_SWEEP_S", "300") or 300)
PERKS_TTL_S = 60
PERKS_MAX = 10000
CFG_TTL_S = 30
CFG_MAX = 2000

_DONATION_MARKERS = tuple(m.strip().lower() for m in os.getenv(
    "DONATION_PAYLOAD_MARKERS", "tip,tips,donation,donate,donacion,propina").split(",") if m.strip())
_DONATION_RE = re.compile(r"(?:^|[_:|\-.])(?:%s)(?:[_:|\-.]|$)" % "|".join(re.escape(m) for m in _DONATION_MARKERS),
                          re.IGNORECASE) if _DONATION_MARKERS else None

_perks_cache: dict = {}    # (group, user) -> (monotonic, perks | None)
_cfg_cache: dict = {}      # group -> (monotonic, config)
_master_bot = None
_bot_resolver: Optional[Callable] = None
_bg_tasks: set = set()


def _db():
    import database.database as db   # importación diferida: database importa mucho al cargar
    return db


def _spawn(coro, name: str = "vip_roles") -> Optional[asyncio.Task]:
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError:
        return None
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def register_master_bot(bot) -> None:
    global _master_bot
    _master_bot = bot


def register_bot_resolver(fn: Callable) -> None:
    """fn(group_id) -> Bot | [Bot] | corrutina: instancias candidatas (clon de la comunidad, maestro…)."""
    global _bot_resolver
    _bot_resolver = fn


def is_donation_payload(payload: str) -> bool:
    text = str(payload or "")
    if not text or _DONATION_RE is None:
        return False
    with contextlib.suppress(Exception):
        import micvip_audit
        if micvip_audit.is_micvip_payload(text):
            return False
    return bool(_DONATION_RE.search(text))


# --------------------------------------------------------------------------
# Cachés acotadas (protocolo: TTL + tamaño máximo + purga)
# --------------------------------------------------------------------------
def invalidate(group_id: int, user_id: Optional[int] = None) -> None:
    gid = int(group_id)
    _cfg_cache.pop(gid, None)
    if user_id is None:
        for key in [k for k in _perks_cache if k[0] == gid]:
            _perks_cache.pop(key, None)
    else:
        _perks_cache.pop((gid, int(user_id)), None)


def purge_caches() -> dict:
    now = time.monotonic()
    for key in [k for k, (ts, _v) in list(_perks_cache.items()) if now - ts > PERKS_TTL_S]:
        _perks_cache.pop(key, None)
    for key in [k for k, (ts, _v) in list(_cfg_cache.items()) if now - ts > CFG_TTL_S]:
        _cfg_cache.pop(key, None)
    return {"perks": len(_perks_cache), "cfg": len(_cfg_cache)}


async def get_config(group_id: int) -> dict:
    now = time.monotonic()
    hit = _cfg_cache.get(int(group_id))
    if hit and now - hit[0] < CFG_TTL_S:
        return hit[1]
    cfg = await _db().get_vip_role_config(int(group_id))
    if len(_cfg_cache) >= CFG_MAX:
        purge_caches()
        if len(_cfg_cache) >= CFG_MAX:
            _cfg_cache.clear()
    _cfg_cache[int(group_id)] = (now, cfg)
    return cfg


async def get_active_perks(group_id: int, user_id: int) -> Optional[dict]:
    """
    Privilegios VIP vigentes o None. Ruta caliente de la matriz perimetral: caché 60 s, nunca lanza.
    → {"exempt": frozenset, "flood_bonus": int, "autolower_immune": bool, "tag": str, "source": str, "expires_at": int|None}
    """
    key = (int(group_id), int(user_id))
    now = time.monotonic()
    hit = _perks_cache.get(key)
    if hit and now - hit[0] < PERKS_TTL_S:
        return hit[1]
    perks = None
    try:
        cfg = await get_config(group_id)
        if cfg.get("enabled"):
            member = await _db().vip_role_member_get(int(group_id), int(user_id))
            if member:
                perks = {"exempt": frozenset(cfg.get("exempt_vectors") or ()), "flood_bonus": int(cfg.get("flood_bonus") or 1),
                         "autolower_immune": bool(cfg.get("autolower_immune")), "tag": cfg.get("tag_text") or "",
                         "source": member.get("source"), "expires_at": member.get("expires_at")}
    except Exception as ex:
        logger.debug(f"[VIP Roles] Consulta de privilegios fallida ({group_id}/{user_id}): {ex}")
        return None   # sin caché: el siguiente mensaje reintenta
    if len(_perks_cache) >= PERKS_MAX:
        purge_caches()
        if len(_perks_cache) >= PERKS_MAX:
            _perks_cache.clear()
    _perks_cache[key] = (now, perks)
    return perks


# --------------------------------------------------------------------------
# Etiqueta nativa de Telegram (Bot API: setChatMemberTag · 0-16 caracteres, sin emoji, derecho can_manage_tags)
# --------------------------------------------------------------------------
def _classify_tag_error(ex: Exception) -> str:
    text = str(ex).lower()
    if "not enough rights" in text or "admin_required" in text or "manage_tags" in text or "rights" in text:
        return "no_right"
    if "administrator" in text or "creator" in text or "admin" in text:
        return "admin"
    if "user not found" in text or "participant" in text or "not a member" in text or "user_not_participant" in text:
        return "not_member"
    return "failed"


async def _call_set_tag(bot, group_id: int, user_id: int, tag: Optional[str]) -> None:
    method = getattr(bot, "set_chat_member_tag", None)
    if method is not None:
        await asyncio.wait_for(method(chat_id=group_id, user_id=user_id, tag=tag), timeout=TG_TIMEOUT_S)
        return
    try:
        from aiogram.methods import SetChatMemberTag  # type: ignore[attr-defined]
    except ImportError:
        raise NotImplementedError("aiogram sin SetChatMemberTag (actualiza a aiogram ≥ 3.30)")
    await asyncio.wait_for(bot(SetChatMemberTag(chat_id=group_id, user_id=user_id, tag=tag)), timeout=TG_TIMEOUT_S)


async def _candidate_bots(group_id: int, preferred=None) -> list:
    bots = []
    if preferred is not None:
        bots.append(preferred)
    if _bot_resolver is not None:
        with contextlib.suppress(Exception):
            found = _bot_resolver(group_id)
            if asyncio.iscoroutine(found):
                found = await asyncio.wait_for(found, timeout=TG_TIMEOUT_S)
            if found is not None and not isinstance(found, (list, tuple)):
                found = [found]
            bots.extend(b for b in (found or []) if b is not None)
    if _master_bot is not None:
        bots.append(_master_bot)
    unique, seen = [], set()
    for b in bots:
        if id(b) not in seen:
            seen.add(id(b))
            unique.append(b)
    return unique


async def apply_tag(group_id: int, user_id: int, tag: Optional[str], bot=None) -> str:
    """Aplica (tag) o retira (None) la etiqueta. Devuelve el estado guardado: applied/removed/no_right/admin/…"""
    db = _db()
    status, error = "failed", ""
    for candidate in await _candidate_bots(group_id, bot):
        try:
            await _call_set_tag(candidate, int(group_id), int(user_id), tag or None)
            status, error = ("applied" if tag else "removed"), ""
            break
        except NotImplementedError as ex:
            status, error = "unsupported", str(ex)
            break
        except Exception as ex:
            status, error = _classify_tag_error(ex), str(ex)[:200]
            if status in ("admin", "not_member"):
                break   # no depende del bot: otro bot obtendría la misma respuesta
    if not tag and status not in ("removed",):
        status = "removed" if status in ("admin", "not_member") else status
    with contextlib.suppress(Exception):
        await db.vip_role_set_tag_status(group_id, user_id, status, error or None)
    if status not in ("applied", "removed"):
        logger.warning(f"🏷️ [VIP Roles] Etiqueta {('aplicar' if tag else 'retirar')} en {group_id}/{user_id}: {status}")
    return status


# --------------------------------------------------------------------------
# Concesión automática y manual
# --------------------------------------------------------------------------
def _publish(group_id: int, user_id: int, status: str, source: str) -> None:
    with contextlib.suppress(Exception):
        from radar_bus import publish_radar_event  # type: ignore[import-not-found]
        publish_radar_event(int(group_id), "vip_role", {"u": int(user_id), "s": status, "src": source})


def _announce_text(mention: str, tag: str, source: str, expires_at) -> str:
    when_es = "de forma permanente" if not expires_at else f"hasta el {time.strftime('%d/%m/%Y %H:%M UTC', time.gmtime(int(expires_at)))}"
    when_en = "permanently" if not expires_at else f"until {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(int(expires_at)))}"
    why_es = {"donation": "por su donación", "micvip": "por su pase MicVIP", "manual": "por decisión del dueño"}.get(source, "")
    why_en = {"donation": "for their donation", "micvip": "for their MicVIP pass", "manual": "by the owner"}.get(source, "")
    label = html.escape(tag or "VIP")
    return (f"👑 <b>Nuevo miembro VIP / New VIP member</b>\n\n"
            f"{mention} recibe la etiqueta <b>{label}</b> {why_es} {when_es}. ¡Gracias por apoyar la comunidad!\n"
            f"🇺🇸 <i>{mention} gets the <b>{label}</b> tag {why_en} {when_en}. Thanks for supporting the community!</i>" + SIG)


async def _after_grant(result: dict, group_id: int, user_id: int, source: str, mention: str, bot=None) -> dict:
    invalidate(group_id, user_id)
    if not result.get("granted"):
        return result
    cfg = result.get("config") or {}
    member = result.get("member") or {}
    tag = cfg.get("tag_text") or ""
    if tag and member.get("tag_status") != "applied":
        result["tag_status"] = await apply_tag(group_id, user_id, tag, bot=bot)
    _publish(group_id, user_id, "granted", source)
    if result.get("is_new") and cfg.get("announce"):
        for candidate in await _candidate_bots(group_id, bot):
            try:
                msg = await asyncio.wait_for(candidate.send_message(int(group_id), _announce_text(
                    mention, tag, source, member.get("expires_at")), parse_mode="HTML"), timeout=TG_TIMEOUT_S)
                _spawn(_delete_later(candidate, int(group_id), msg.message_id, 180), name="vip_notice_cleanup")
                break
            except Exception as ex:
                logger.debug(f"[VIP Roles] Aviso de grupo no enviado en {group_id}: {ex}")
    logger.info(f"👑 [VIP Roles] Rol VIP {('nuevo' if result.get('is_new') else 'extendido')} en {group_id} para {user_id} ({source}).")
    return result


async def _delete_later(bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(bot.delete_message(chat_id=chat_id, message_id=message_id), timeout=TG_TIMEOUT_S)


def _mention(user_id: int, full_name: str = None, username: str = None) -> str:
    name = html.escape((full_name or (f"@{username}" if username else "") or str(user_id))[:64])
    return f"<a href='tg://user?id={int(user_id)}'>{name}</a>"


async def on_payment(*, group_id: int, user_id: int, charge_id: str, amount: int, payload: str,
                     username: str = None, full_name: str = None, bot=None) -> Optional[dict]:
    """Gancho de main._record_payment. Solo procesa DONACIONES (MicVIP llega verificado por on_micvip_granted)."""
    if not group_id or not user_id or not is_donation_payload(payload):
        return None
    try:
        result = await _db().vip_role_grant(int(group_id), int(user_id), "donation", stars=int(amount or 0),
                                            charge_id=charge_id, username=username, full_name=full_name)
    except Exception as ex:
        logger.error(f"❌ [VIP Roles] Donación {charge_id} no procesada en {group_id}: {ex}")
        return None
    if result.get("duplicate"):
        return result
    return await _after_grant(result, int(group_id), int(user_id), "donation", _mention(user_id, full_name, username), bot=bot)


async def on_micvip_granted(group_id: int, user_id: int, charge_id: str, expires_at: int) -> None:
    """Listener de micvip_audit: el pase está verificado en vip_mic_passes → rol VIP con la misma expiración."""
    try:
        result = await _db().vip_role_grant(int(group_id), int(user_id), "micvip", expires_at=int(expires_at),
                                            charge_id=f"micvip:{charge_id}")
    except Exception as ex:
        logger.error(f"❌ [VIP Roles] MicVIP {charge_id} no convertido en rol en {group_id}: {ex}")
        return
    if not result.get("duplicate"):
        member = result.get("member") or {}
        await _after_grant(result, int(group_id), int(user_id), "micvip",
                           _mention(user_id, member.get("full_name"), member.get("username")))


async def grant_manual(group_id: int, user_id: int, days: int, actor_id: int, bot=None,
                       username: str = None, full_name: str = None) -> dict:
    result = await _db().vip_role_grant(int(group_id), int(user_id), "manual", days=int(days), actor_id=actor_id,
                                        username=username, full_name=full_name)
    return await _after_grant(result, int(group_id), int(user_id), "manual", _mention(user_id, full_name, username), bot=bot)


async def revoke(group_id: int, user_id: int, actor_id: int, bot=None) -> dict:
    member = await _db().vip_role_revoke(int(group_id), int(user_id), actor_id=actor_id)
    invalidate(group_id, user_id)
    if member:
        await apply_tag(group_id, user_id, None, bot=bot)
        _publish(group_id, user_id, "revoked", "manual")
    return member or {}


async def retag(group_id: int, user_id: int, bot=None) -> str:
    cfg = await get_config(group_id)
    invalidate(group_id, user_id)
    return await apply_tag(group_id, user_id, cfg.get("tag_text") or None, bot=bot)


async def update_config(group_id: int, patch: dict, updated_by: int = None) -> dict:
    """Guarda la configuración; si cambia la etiqueta, la re-aplica en segundo plano a los VIP vigentes."""
    before = await _db().get_vip_role_config(int(group_id))
    cfg = await _db().update_vip_role_config(int(group_id), patch, updated_by=updated_by)
    invalidate(group_id)
    if "tag_text" in patch and cfg.get("tag_text") != before.get("tag_text"):
        _spawn(_retag_all(int(group_id), cfg.get("tag_text") or None), name=f"vip_retag:{group_id}")
    return cfg


async def _retag_all(group_id: int, tag: Optional[str]) -> int:
    done, offset = 0, 0
    while done < 500:
        page = await _db().vip_role_list(group_id, status="active", limit=100, offset=offset)
        records = page.get("records") or []
        if not records:
            break
        for rec in records:
            await apply_tag(group_id, int(rec["user_id"]), tag)
            done += 1
            await asyncio.sleep(0.2)   # sin ráfagas contra la Bot API
        offset += len(records)
    return done


# --------------------------------------------------------------------------
# Barrido periódico: vencimientos y reintentos de etiqueta
# --------------------------------------------------------------------------
async def sweep_once() -> dict:
    db = _db()
    expired = await db.vip_role_expire_due(limit=200)
    for group_id, user_id, tag_status in expired:
        invalidate(group_id, user_id)
        if tag_status == "applied":
            await apply_tag(group_id, user_id, None)
        _publish(group_id, user_id, "expired", "sweep")
        await asyncio.sleep(0.1)
    retried = 0
    for group_id, user_id, _status in await db.vip_role_pending_tags(limit=20):
        cfg = await get_config(group_id)
        if cfg.get("enabled") and cfg.get("tag_text"):
            await apply_tag(group_id, user_id, cfg["tag_text"])
            retried += 1
            await asyncio.sleep(0.2)
    purge_caches()
    return {"expired": len(expired), "retried": retried}


async def vip_sweeper_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            stats = await sweep_once()
            if stats["expired"] or stats["retried"]:
                logger.info(f"👑 [VIP Roles] Barrido: {stats['expired']} vencido(s), {stats['retried']} etiqueta(s) reintentada(s).")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [VIP Roles] Barrido fallido: {ex}")
        await asyncio.sleep(SWEEP_INTERVAL_S)


# Registro del listener de MicVIP (sin acoplar micvip_audit a este módulo).
with contextlib.suppress(Exception):
    import micvip_audit
    micvip_audit.register_grant_listener(on_micvip_granted)


# --------------------------------------------------------------------------
# Textos de la consola del bot (ES / EN)
# --------------------------------------------------------------------------
TAG_STATUS_LABELS = {
    "applied": ("🏷️ etiqueta aplicada", "🏷️ tag applied"),
    "pending": ("⏳ etiqueta pendiente", "⏳ tag pending"),
    "no_right": ("⚠️ el bot necesita el permiso «Gestionar etiquetas»", "⚠️ bot needs the “Manage tags” right"),
    "admin": ("ℹ️ es administrador (usa título de admin)", "ℹ️ is an admin (uses an admin title)"),
    "not_member": ("ℹ️ no está en el grupo", "ℹ️ not in the group"),
    "unsupported": ("⚠️ actualiza aiogram ≥ 3.30", "⚠️ upgrade aiogram ≥ 3.30"),
    "failed": ("⚠️ reintentando", "⚠️ retrying"),
    "removed": ("🏷️ etiqueta retirada", "🏷️ tag removed"),
}
SOURCE_LABELS = {"donation": ("💝 Donación", "💝 Donation"), "micvip": ("🎙️ MicVIP", "🎙️ MicVIP"),
                 "manual": ("✋ Manual", "✋ Manual")}


def fmt_expiry(expires_at, lang: str) -> str:
    if not expires_at:
        return "♾️ " + ("permanente" if lang == "es" else "permanent")
    return time.strftime("%d/%m/%Y %H:%M UTC" if lang == "es" else "%Y-%m-%d %H:%M UTC", time.gmtime(int(expires_at)))
