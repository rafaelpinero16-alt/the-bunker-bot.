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


async def _set_native_tag(group_id: int, user_id: int, tag: Optional[str], bot=None) -> tuple:
    """Llama a setChatMemberTag con el primer bot candidato que pueda. → (estado, error). No escribe en la base."""
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
    return status, error


async def apply_tag(group_id: int, user_id: int, tag: Optional[str], bot=None) -> str:
    """Aplica (tag) o retira (None) la etiqueta. Devuelve el estado guardado: applied/removed/no_right/admin/…"""
    db = _db()
    status, error = await _set_native_tag(group_id, user_id, tag, bot=bot)
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


def _mention(user_id: int, full_name: Optional[str] = None, username: Optional[str] = None) -> str:
    name = html.escape((full_name or (f"@{username}" if username else "") or str(user_id))[:64])
    return f"<a href='tg://user?id={int(user_id)}'>{name}</a>"


async def on_payment(*, group_id: int, user_id: int, charge_id: str, amount: int, payload: str,
                     username: Optional[str] = None, full_name: Optional[str] = None, bot=None) -> Optional[dict]:
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
                       username: Optional[str] = None, full_name: Optional[str] = None) -> dict:
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


async def update_config(group_id: int, patch: dict, updated_by: Optional[int] = None) -> dict:
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
# v28 · Pase MicVIP con etiqueta nativa (el comprador YA NO se asciende a administrador)
# --------------------------------------------------------------------------
LEGACY_PER_SWEEP = 10
# Derechos que un ascenso "solo por título" nunca concedió: si el admin tiene alguno, alguien se los dio a propósito.
_REAL_ADMIN_RIGHTS = ("can_delete_messages", "can_restrict_members", "can_promote_members", "can_change_info",
                      "can_invite_users", "can_pin_messages", "can_manage_video_chats", "can_post_messages",
                      "can_edit_messages", "can_manage_topics", "can_post_stories", "can_edit_stories",
                      "can_delete_stories")


def _status_str(member) -> str:
    raw = getattr(member, "status", "")
    return str(getattr(raw, "value", raw) or "").lower()


async def _micvip_badge_title(group_id: int) -> str:
    db = _db()
    with contextlib.suppress(Exception):
        custom = await db.get_mic_vip_custom_config(int(group_id)) or {}
        if custom.get("tag"):
            return str(custom["tag"])
    with contextlib.suppress(Exception):
        return str(await db.get_vip_badge_title(int(group_id)) or "")
    return ""


async def _apply_micvip_tag(group_id: int, user_id: int, tag: str, bot=None) -> str:
    status, error = await _set_native_tag(group_id, user_id, tag, bot=bot)
    with contextlib.suppress(Exception):
        await _db().micvip_tag_set_status(group_id, user_id, status, error or None)
    if status == "applied":
        _publish(group_id, user_id, "granted", "micvip")
    elif status != "admin":   # un admin real conserva su título: no es un fallo
        logger.warning(f"🎙️ [MicVIP] Etiqueta del pase en {group_id}/{user_id}: {status}")
    return status


async def grant_micvip_tag(group_id: int, user_id: int, badge_title: Optional[str] = None, bot=None) -> str:
    """
    Rama vip_mic_ de payments: pone la etiqueta del pase con setChatMemberTag, con el vencimiento real del pase.
    Si la comunidad tiene Roles VIP activos para MicVIP, el rol (listener de micvip_audit) ya aplica SU etiqueta con la
    misma expiración → devuelve 'role' y no compite por la única etiqueta del miembro.
    """
    db = _db()
    cfg = await get_config(group_id)
    if cfg.get("enabled") and cfg.get("micvip_role") and cfg.get("tag_text"):
        return "role"
    title = badge_title if badge_title is not None else await _micvip_badge_title(group_id)
    tag = db.sanitize_vip_tag(title) or "VIP"
    expires_at = await db.get_vip_mic_expiry_ts(int(user_id), int(group_id))
    record = await db.micvip_tag_upsert(int(group_id), int(user_id), tag, expires_at)
    if record.get("tag_status") == "applied":
        return "applied"
    return await _apply_micvip_tag(int(group_id), int(user_id), tag, bot=bot)


async def _demote_title_only_admin(bot, group_id: int, user_id: int) -> None:
    await asyncio.wait_for(bot.promote_chat_member(
        chat_id=group_id, user_id=user_id,
        can_manage_chat=False, can_change_info=False, can_delete_messages=False, can_invite_users=False,
        can_restrict_members=False, can_pin_messages=False, can_promote_members=False, can_manage_video_chats=False,
    ), timeout=TG_TIMEOUT_S)


async def migrate_legacy_promotion(group_id: int, user_id: int) -> str:
    """
    Degrada a un comprador MicVIP que versiones anteriores ascendieron a admin solo por el título, y le pone la
    etiqueta nativa si su pase sigue vigente. Respeta a quien hoy tenga derechos reales de administración.
    → 'demoted' | 'kept_real_admin' | 'not_admin' | 'gone' | 'retry'
    """
    db = _db()
    gid, uid = int(group_id), int(user_id)
    for candidate in await _candidate_bots(gid):
        try:
            member = await asyncio.wait_for(candidate.get_chat_member(chat_id=gid, user_id=uid), timeout=TG_TIMEOUT_S)
        except Exception as ex:
            text = str(ex).lower()
            if "chat not found" in text or "user not found" in text or "participant_id_invalid" in text:
                await db.micvip_legacy_resolve(gid, uid, True)
                return "gone"
            continue
        status = _status_str(member)
        if status != "administrator":
            await db.micvip_legacy_resolve(gid, uid, True)
            if status in ("member", "restricted"):
                with contextlib.suppress(Exception):
                    expiry = await db.get_vip_mic_expiry_ts(uid, gid)
                    if expiry and expiry > int(time.time()):
                        await grant_micvip_tag(gid, uid, bot=candidate)
            return "not_admin"
        if not getattr(member, "can_be_edited", False):
            continue   # lo ascendió otro bot o una persona: probar con el siguiente candidato
        if any(bool(getattr(member, right, False)) for right in _REAL_ADMIN_RIGHTS):
            await db.micvip_legacy_resolve(gid, uid, True)
            logger.info(f"🎙️ [MicVIP] {gid}/{uid}: admin con derechos reales asignados después; se respeta.")
            return "kept_real_admin"
        try:
            await _demote_title_only_admin(candidate, gid, uid)
        except Exception as ex:
            logger.warning(f"🎙️ [MicVIP] No se pudo degradar el ascenso antiguo {gid}/{uid}: {str(ex)[:120]}")
            continue
        await db.micvip_legacy_resolve(gid, uid, True)
        with contextlib.suppress(Exception):
            expiry = await db.get_vip_mic_expiry_ts(uid, gid)
            if expiry and expiry > int(time.time()):
                await grant_micvip_tag(gid, uid, bot=candidate)
        _publish(gid, uid, "demoted", "micvip")
        logger.info(f"🎙️ [MicVIP] {gid}/{uid}: ascenso antiguo retirado; ahora usa etiqueta nativa.")
        return "demoted"
    await db.micvip_legacy_resolve(gid, uid, False)
    return "retry"


async def _micvip_sweep() -> dict:
    db = _db()
    expired = 0
    for group_id, user_id, prev in await db.micvip_tag_expire_due(limit=200):
        expired += 1
        if prev == "applied":
            cfg = await get_config(group_id)
            member = None
            with contextlib.suppress(Exception):
                member = await db.vip_role_member_get(group_id, user_id)
            if member and cfg.get("enabled") and cfg.get("tag_text"):
                await apply_tag(group_id, user_id, cfg["tag_text"])   # sigue siendo VIP por otra vía: conserva SU etiqueta
            else:
                await _set_native_tag(group_id, user_id, None)
        _publish(group_id, user_id, "expired", "micvip")
        await asyncio.sleep(0.1)
    retried = 0
    for group_id, user_id, tag in await db.micvip_tag_pending(limit=20):
        await _apply_micvip_tag(group_id, user_id, tag)
        retried += 1
        await asyncio.sleep(0.2)
    migrated = 0
    for group_id, user_id in await db.micvip_legacy_promotions(limit=LEGACY_PER_SWEEP):
        if await migrate_legacy_promotion(group_id, user_id) == "demoted":
            migrated += 1
        await asyncio.sleep(0.3)
    return {"micvip_expired": expired, "micvip_retried": retried, "micvip_migrated": migrated}


# --------------------------------------------------------------------------
# Barrido periódico: vencimientos y reintentos de etiqueta
# --------------------------------------------------------------------------
async def sweep_once() -> dict:
    db = _db()
    expired = await db.vip_role_expire_due(limit=200)
    for group_id, user_id, tag_status in expired:
        invalidate(group_id, user_id)
        if tag_status == "applied":
            pass_tag = None
            with contextlib.suppress(Exception):
                rec = await db.micvip_tag_get(group_id, user_id)
                if rec and rec.get("tag") and rec.get("expires_at") and int(rec["expires_at"]) > int(time.time()):
                    pass_tag = rec["tag"]
            if pass_tag:
                await _apply_micvip_tag(group_id, user_id, pass_tag)   # el pase MicVIP sigue vigente
            else:
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
    stats = {"expired": len(expired), "retried": retried}
    try:
        stats.update(await _micvip_sweep())
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error(f"❌ [MicVIP] Barrido de etiquetas fallido: {ex}")
    return stats


async def vip_sweeper_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            stats = await sweep_once()
            if any(stats.values()):
                logger.info(f"👑 [VIP Roles] Barrido: {stats}")
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
