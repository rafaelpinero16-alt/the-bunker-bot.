"""
ban_appeals.py — The Bunker OS · v24 · Apelación de baneos por infracciones (Telegram Stars · XTR)

Flujo autónomo de extremo a extremo:
  1. BANEO ...... groups.enforce_warn_ladder alcanza warns_limit con acción 'ban' → on_ladder_ban() registra la apelación
                  (precio y vigencia fijados en ese instante) y envía por PRIVADO el motivo perimetral + la factura.
                  Si el usuario nunca abrió el bot (Telegram no permite escribirle), queda 'dm_failed' y se le entrega
                  en cuanto pulse /start, o con el enlace t.me/<bot>?start=appeal_<grupo>.
  2. PRE-PAGO ... pre_checkout_query (payload appeal_<grupo>_<usuario>_<id>): titular, importe, vigencia y que el
                  usuario siga baneado. Cualquier discrepancia se rechaza ANTES de cobrar.
  3. PAGO ....... successful_payment → claim atómico por charge_id (BEGIN IMMEDIATE + UNIQUE): un reintento o un
                  update duplicado nunca desbanea dos veces ni cobra sin servicio.
  4. REINGRESO .. unban_chat_member + retirada de la marca global users.is_banned (solo si la puso ESTE baneo) +
                  reinicio de strikes + enlace de invitación de UN uso con caducidad, enviado por privado.
                  Si ningún bot puede desbanear → reembolso automático (refund_star_payment) y aviso.
  5. LIMPIEZA ... al entrar por el enlace, o al caducar, se revoca el enlace y se borra el mensaje que lo contenía.

v25 · Integración nativa con handlers/payments.py: payments es la ÚNICA puerta de entrada de facturas, pre-checkouts,
pagos y deep links (familia appeal_ en sus regex). Este módulo aporta la lógica y reutiliza send_stars_invoice (registro
de ofertas en memoria), process_star_refund (reembolso idempotente anotado en stars_payment_ledger) y la idempotencia
por processed_payments. Sin payments disponible (pruebas, despliegues parciales) recurre a la Bot API directa.
Memoria: _bg y _title_cache acotados. Telegram siempre con asyncio.wait_for + try/except. Logs solo con ids.

v40 · Fase 3 (Monetización):
  • PRE-PAGO: además del estado en Telegram, se exige evidencia en BASE DE DATOS de que el baneo sigue vigente
    (crud_fase3.ban_state): si el Dueño ya lo revirtió como falso positivo, se rechaza antes de cobrar.
  • REINGRESO: tras el desbaneo se limpia TODO el historial negativo del usuario en la comunidad
    (crud_fase3.clear_negative_history: strikes, infracciones Auto-Mod, sanciones vigentes, lista negra Anti-Raid)
    y se publica la tarjeta #UNBAN_PAGADO en el canal de registro (Log Channel Hub, caché y disyuntor actuales).
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

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

logger = logging.getLogger("ban_appeals")
router = Router(name="ban_appeals")   # v25: sin manejadores propios (todo entra por payments.router); se conserva por compatibilidad

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
TG_TIMEOUT_S = 8
SWEEP_INTERVAL_S = int(os.getenv("BAN_APPEALS_SWEEP_S", "60") or 60)
PAYLOAD_RE = re.compile(r"^appeal_(-\d{6,})_(\d{1,20})_(\d{1,12})$")
DEEPLINK_RE = re.compile(r"^/start\s+appeal_(-\d{6,})$")
LINK_NAME_RE = re.compile(r"^appeal-(\d{1,12})$")
PRECHECKOUT_EXTERNAL_BUDGET_S = 1.5      # get_chat_member + CAS en paralelo: dentro del SLA de 2 s de payments (Telegram corta a los 10 s)
TITLE_CACHE_TTL_S = 600
TITLE_CACHE_MAX = 2000

_master_bot = None
_bot_resolver: Optional[Callable] = None
_bg: set = set()
_title_cache: dict = {}   # group_id -> (monotonic, title)


def _db():
    import database.database as db
    return db


def _spawn(coro, name: str = "ban_appeals"):
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError:
        return None
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return task


def register_master_bot(bot) -> None:
    global _master_bot
    _master_bot = bot


def register_bot_resolver(fn: Callable) -> None:
    """fn(group_id) -> Bot | corrutina: bot clon en ejecución de la comunidad (desbaneo y enlaces)."""
    global _bot_resolver
    _bot_resolver = fn


async def _candidate_bots(group_id: int, preferred=None) -> list:
    bots = [preferred] if preferred is not None else []
    if _bot_resolver is not None:
        with contextlib.suppress(Exception):
            found = _bot_resolver(group_id)
            if asyncio.iscoroutine(found):
                found = await asyncio.wait_for(found, timeout=TG_TIMEOUT_S)
            if found is not None:
                bots.append(found)
    if _master_bot is not None:
        bots.append(_master_bot)
    unique, seen = [], set()
    for b in bots:
        key = getattr(b, "id", id(b))
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique


async def _bot_by_id(group_id: int, bot_id: Optional[int]):
    for b in await _candidate_bots(group_id):
        if bot_id is None or getattr(b, "id", None) == int(bot_id):
            return b
    return None


def build_payload(appeal: dict) -> str:
    return f"appeal_{int(appeal['group_id'])}_{int(appeal['user_id'])}_{int(appeal['id'])}"


def parse_payload(payload: str) -> Optional[tuple]:
    m = PAYLOAD_RE.match(str(payload or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def purge_state() -> dict:
    now = time.monotonic()
    for key in [k for k, (ts, _t) in list(_title_cache.items()) if now - ts > TITLE_CACHE_TTL_S]:
        _title_cache.pop(key, None)
    return {"titles": len(_title_cache), "tasks": len(_bg)}


async def _group_title(bot, group_id: int) -> str:
    hit = _title_cache.get(int(group_id))
    if hit and time.monotonic() - hit[0] < TITLE_CACHE_TTL_S:
        return hit[1]
    title = ""
    with contextlib.suppress(Exception):
        chat = await asyncio.wait_for(bot.get_chat(int(group_id)), timeout=4)
        title = getattr(chat, "title", "") or ""
    if len(_title_cache) >= TITLE_CACHE_MAX:
        _title_cache.clear()
    _title_cache[int(group_id)] = (time.monotonic(), title)
    return title


async def _lang_for(user) -> str:
    db = _db()
    stored = None
    with contextlib.suppress(Exception):
        stored = await db.get_user_lang(int(user.id))
    if stored in ("es", "en"):
        return stored
    if os.getenv("BUNKER_LANG_AUTODETECT", "0") == "1":
        code = str(getattr(user, "language_code", "") or "").lower()
        if code.startswith("es"):
            return "es"
        if code.startswith("en"):
            return "en"
    return getattr(db, "DEFAULT_LANG", "en")


def _publish(group_id: int, user_id: int, status: str) -> None:
    with contextlib.suppress(Exception):
        from radar_bus import publish_radar_event  # type: ignore[import-not-found]
        publish_radar_event(int(group_id), "ban_appeal", {"u": int(user_id), "s": status})


# --------------------------------------------------------------------------
# Textos (ES / EN)
# --------------------------------------------------------------------------
def _t(lang: str, es: str, en: str) -> str:
    return es if lang == "es" else en


def _offer_text(appeal: dict, title: str, lang: str) -> str:
    group = html.escape(title or str(appeal["group_id"]))
    reason = html.escape((appeal.get("reason_es") if lang == "es" else appeal.get("reason_en")) or appeal.get("reason_key") or "—")
    until = time.strftime("%d/%m/%Y %H:%M UTC" if lang == "es" else "%Y-%m-%d %H:%M UTC", time.gmtime(int(appeal["offer_expires_at"])))
    return _t(
        lang,
        f"🚫 <b>Has sido baneado de {group}</b>\n\n"
        f"• <b>Motivo perimetral:</b> {reason}\n"
        f"• <b>Advertencias:</b> {appeal.get('strikes')}/{appeal.get('warn_limit')} (límite alcanzado)\n\n"
        f"⚖️ <b>¿Fue un error o tu cuenta se vio afectada por un virus de spam?</b>\n"
        f"Puedes apelar el baneo con un pago compensatorio de <b>{appeal['price_stars']} ⭐ Telegram Stars</b>. "
        f"Al confirmarse el pago se te desbanea al instante y recibes aquí un enlace de reingreso de un solo uso.\n\n"
        f"<i>Oferta válida hasta {until}. Si no puede completarse el reingreso, el pago se reembolsa automáticamente.</i>" + SIG,
        f"🚫 <b>You have been banned from {group}</b>\n\n"
        f"• <b>Perimeter reason:</b> {reason}\n"
        f"• <b>Warnings:</b> {appeal.get('strikes')}/{appeal.get('warn_limit')} (limit reached)\n\n"
        f"⚖️ <b>Was it a mistake, or was your account hit by spam malware?</b>\n"
        f"You can appeal the ban with a compensation payment of <b>{appeal['price_stars']} ⭐ Telegram Stars</b>. "
        f"Once paid you are unbanned immediately and receive a single-use rejoin link here.\n\n"
        f"<i>Offer valid until {until}. If the rejoin cannot be completed, the payment is refunded automatically.</i>" + SIG,
    )


def _payments():
    """handlers.payments (o None): reutiliza su emisión de facturas, su libro mayor y sus reembolsos."""
    with contextlib.suppress(Exception):
        from handlers import payments as _p
        return _p
    return None


async def send_appeal_invoice(bot, chat_id: int, appeal: dict, title: str, lang: str):
    group = (title or str(appeal["group_id"]))[:60]
    inv_title = _t(lang, "Apelación de baneo", "Ban appeal")[:32]
    description = _t(lang, f"Desbaneo inmediato y enlace de reingreso de un solo uso a «{group}».",
                     f"Immediate unban and a single-use rejoin link to “{group}”.")[:255]
    pay = _payments()
    if pay is not None and hasattr(pay, "send_stars_invoice"):
        # Punto único de emisión de payments: XTR y oferta registrada en memoria para su pre-checkout.
        return await asyncio.wait_for(pay.send_stars_invoice(bot, chat_id, inv_title, description, build_payload(appeal),
                                                             int(appeal["price_stars"])), timeout=TG_TIMEOUT_S)
    return await asyncio.wait_for(bot.send_invoice(
        chat_id=chat_id, title=inv_title, description=description, payload=build_payload(appeal), currency="XTR",
        prices=[LabeledPrice(label=_t(lang, "Apelación", "Appeal"), amount=int(appeal["price_stars"]))],
        provider_token="",
    ), timeout=TG_TIMEOUT_S)


async def deliver_offer(bot, appeal: dict, user, title: str = "") -> bool:
    """Envía por privado el motivo + la factura. Devuelve True si llegó; si no, la apelación queda 'dm_failed'."""
    db = _db()
    lang = await _lang_for(user)
    title = title or await _group_title(bot, appeal["group_id"])
    try:
        await asyncio.wait_for(bot.send_message(int(appeal["user_id"]), _offer_text(appeal, title, lang), parse_mode="HTML"),
                               timeout=TG_TIMEOUT_S)
        invoice = await send_appeal_invoice(bot, int(appeal["user_id"]), appeal, title, lang)
    except Exception as ex:
        logger.info(f"⚖️ [Apelaciones] Sin chat privado con {appeal['user_id']} (apelación {appeal['id']}): {type(ex).__name__}")
        await db.appeal_update(appeal["id"], status="dm_failed", error=str(ex))
        return False
    await db.appeal_update(appeal["id"], status="offered", dm_bot_id=getattr(bot, "id", None),
                           dm_message_id=getattr(invoice, "message_id", None), error=None)
    return True


# --------------------------------------------------------------------------
# 1 · Detección (gancho de groups.enforce_warn_ladder)
# --------------------------------------------------------------------------
async def on_ladder_ban(bot, *, group_id: int, user, reason_key: str, reason_es: str, reason_en: str,
                        strikes: int, limit: int, prior_global_ban: bool) -> Optional[dict]:
    """Baneo automático por warns_limit. Nunca lanza: un fallo aquí no puede afectar a la sanción ya aplicada."""
    try:
        db = _db()
        with contextlib.suppress(Exception):
            if await db.is_userbot_flagged(int(user.id), int(group_id)):
                return None   # userbots marcados (spam automatizado confirmado): sin apelación
        res = await db.appeal_create(int(group_id), int(user.id), reason_key=reason_key, reason_es=reason_es,
                                     reason_en=reason_en, strikes=strikes, warn_limit=limit,
                                     prior_global_ban=bool(prior_global_ban),
                                     username=getattr(user, "username", None), full_name=getattr(user, "full_name", None))
        if not res.get("created"):
            logger.info(f"⚖️ [Apelaciones] Sin oferta para {user.id} en {group_id}: {res.get('reason')}")
            return None
        appeal = res["appeal"]
        delivered = False
        for candidate in await _candidate_bots(group_id, bot):
            if await deliver_offer(candidate, appeal, user):
                delivered = True
                break
        _publish(group_id, user.id, "offered" if delivered else "dm_failed")
        logger.info(f"⚖️ [Apelaciones] Apelación {appeal['id']} para {user.id} en {group_id}: "
                    f"{'entregada' if delivered else 'pendiente de /start'} ({appeal['price_stars']} ⭐).")
        return appeal
    except Exception as ex:
        logger.error(f"❌ [Apelaciones] No se pudo registrar la apelación de {getattr(user, 'id', '?')} en {group_id}: {ex}")
        return None


async def deliver_pending(bot, user) -> int:
    """Gancho de /start: entrega las apelaciones que no pudieron enviarse por privado al banearse."""
    if user is None or getattr(user, "is_bot", False):
        return 0
    sent = 0
    try:
        for appeal in await _db().appeal_pending_for_user(int(user.id)):
            if await deliver_offer(bot, appeal, user):
                sent += 1
                _publish(appeal["group_id"], user.id, "offered")
    except Exception as ex:
        logger.debug(f"[Apelaciones] Entrega diferida fallida para {getattr(user, 'id', '?')}: {ex}")
    return sent


async def handle_deeplink(message, bot) -> bool:
    """t.me/<bot>?start=appeal_<grupo> (despachado por payments.cmd_start_deep_linking): reenvía la factura abierta."""
    match = DEEPLINK_RE.match(message.text or "")
    if not match:
        return False
    group_id = int(match.group(1))
    lang = await _lang_for(message.from_user)
    appeal = await _db().appeal_open_for(group_id, message.from_user.id)
    if not appeal:
        await message.answer(_t(lang, "ℹ️ No tienes ninguna apelación abierta en esa comunidad.",
                                "ℹ️ You have no open appeal in that community.") + SIG, parse_mode="HTML")
        return True
    await deliver_offer(bot, appeal, message.from_user)
    return True


# --------------------------------------------------------------------------
# 2 · Validación previa al cobro
# --------------------------------------------------------------------------
async def check_pre_checkout(query) -> tuple:
    """→ (ok, mensaje_de_error). Puro respecto a Telegram salvo la comprobación de que el usuario siga baneado."""
    parsed = parse_payload(query.invoice_payload)
    lang = await _lang_for(query.from_user)
    if not parsed:
        return False, _t(lang, "Factura de apelación no válida.", "Invalid appeal invoice.")
    group_id, user_id, appeal_id = parsed
    appeal = await _db().appeal_get(appeal_id)
    now = int(time.time())
    if (not appeal or int(appeal["group_id"]) != group_id or int(appeal["user_id"]) != user_id
            or int(query.from_user.id) != user_id):
        return False, _t(lang, "Esta apelación no te pertenece.", "This appeal does not belong to you.")
    if appeal["status"] not in ("offered", "dm_failed") or int(appeal["offer_expires_at"]) <= now:
        return False, _t(lang, "Esta apelación ya no está disponible.", "This appeal is no longer available.")
    if (query.currency or "").upper() != "XTR" or int(query.total_amount) != int(appeal["price_stars"]):
        return False, _t(lang, "El importe no coincide con la apelación.", "The amount does not match the appeal.")

    # v40 · Evidencia en base de datos: el baneo debe seguir vigente (no revertido como falso positivo).
    state = await _ban_state(group_id, user_id, int(appeal.get("created_at") or 0))
    if state is not None and state["reverted_after_appeal"] and not state["has_ban_record"]:
        return False, _t(lang, "Tu baneo ya fue revertido por el Dueño: no hace falta pagar.",
                         "Your ban was already reverted by the owner: no payment needed.")

    # Comprobaciones externas EN PARALELO con presupuesto total acotado (SLA del pre-checkout). Si Telegram o CAS no
    # responden a tiempo se aprueba: las validaciones de base de datos ya pasaron y, si el desbaneo no es posible, la
    # entrega reembolsa automáticamente.
    async def cas_flagged() -> bool:
        from middlewares.anti_spam import check_global_cas_spam
        return bool(await check_global_cas_spam(user_id))

    async def member_status() -> str:
        for candidate in await _candidate_bots(group_id):
            try:
                member = await candidate.get_chat_member(group_id, user_id)
            except Exception:
                continue
            return str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", "")) or "")
        return ""

    results = await asyncio.gather(
        asyncio.wait_for(cas_flagged(), timeout=PRECHECKOUT_EXTERNAL_BUDGET_S),
        asyncio.wait_for(member_status(), timeout=PRECHECKOUT_EXTERNAL_BUDGET_S),
        return_exceptions=True)
    if results[0] is True:
        return False, _t(lang, "Tu cuenta figura en la base global anti-spam (CAS): la apelación no es posible.",
                         "Your account is listed in the global anti-spam database (CAS): appeal not possible.")
    status = results[1] if isinstance(results[1], str) else ""
    if status and status != "kicked":
        return False, _t(lang, "Ya no estás baneado en esa comunidad: no hace falta pagar.",
                         "You are no longer banned in that community: no payment needed.")
    return True, ""


async def _ban_state(group_id: int, user_id: int, since_ts: int) -> Optional[dict]:
    """crud_fase3.ban_state o None si la Fase 3 no está desplegada (el flujo v24 sigue funcionando igual)."""
    try:
        from database import crud_fase3
        return dict(await crud_fase3.ban_state(int(group_id), int(user_id), int(since_ts)))
    except Exception as ex:
        logger.debug(f"[Apelaciones] Estado de baneo f3 no disponible para {user_id} en {group_id}: {ex}")
        return None


async def _after_paid_unban(bot, appeal: dict, outcome: dict, lang: str) -> None:
    """
    v40 · Tras un desbaneo pagado: limpia el historial negativo y emite #UNBAN_PAGADO. Nunca lanza (el desbaneo y el
    enlace ya se entregaron al usuario; un fallo aquí solo puede afectar a la auditoría).
    """
    group_id, user_id = int(appeal["group_id"]), int(appeal["user_id"])
    report: dict = {}
    try:
        from database import crud_fase3
        report = dict(await crud_fase3.clear_negative_history(group_id, user_id))
    except Exception as ex:
        logger.warning(f"⚠️ [Apelaciones] Historial negativo de {user_id} en {group_id} no limpiado: {ex}")
    try:
        from handlers.logger_hub import dispatch_audit_log, mark_audit_handled
        mark_audit_handled(f"{group_id}:unban:{user_id}", 60.0)
        reason = appeal.get("reason_es") if lang == "es" else appeal.get("reason_en")
        cleared = []
        if report.get("strikes_reset"):
            cleared.append("strikes")
        if report.get("infractions_cleared"):
            cleared.append(f"{report['infractions_cleared']} Auto-Mod")
        if report.get("sanctions_cleared"):
            cleared.append(f"{report['sanctions_cleared']} sanction(s)")
        if report.get("raid_released"):
            cleared.append("raid blacklist")
        await dispatch_audit_log(
            outcome.get("bot") or bot, group_id, "unban_paid",
            user_id=user_id, user_name=appeal.get("full_name") or None, username=appeal.get("username") or None,
            automatic=True, system="payments", action="unban", amount=int(appeal.get("price_stars") or 0),
            reason=reason or None,
            detail=f"appeal #{int(appeal['id'])} · charge {str(appeal.get('charge_id') or '')[:24]}",
            extra={("Negative history cleared", "Historial negativo limpiado"): ", ".join(cleared) or "—",
                   ("Global ban lifted", "Marca global retirada"): "—" if appeal.get("prior_global_ban") else "✅",
                   ("Rejoin link", "Enlace de reingreso"): (f"✅ {int(outcome.get('ttl_min') or 0)} min"
                                                          if outcome.get("link") else "⚠️ not created")},
            dedupe_key=f"unban_paid:{int(appeal['id'])}", dedupe_ttl=3600,
        )
    except Exception as ex:
        logger.debug(f"[Apelaciones] Tarjeta #UNBAN_PAGADO omitida para la apelación {appeal.get('id')}: {ex}")


async def appeal_pre_checkout(query, bot=None):
    """Responde el pre-checkout de una apelación (lo invoca payments.process_pre_checkout_query)."""
    try:
        ok, error = await check_pre_checkout(query)
    except Exception as ex:
        logger.error(f"❌ [Apelaciones] Pre-checkout fallido: {ex}")
        ok, error = False, "Temporary error / Error temporal. Try again."
    with contextlib.suppress(Exception):
        await asyncio.wait_for(query.answer(ok=ok, error_message=None if ok else error[:200]), timeout=TG_TIMEOUT_S)


# --------------------------------------------------------------------------
# 3 + 4 · Cobro, desbaneo y enlace de reingreso
# --------------------------------------------------------------------------
async def _refund(bot, user_id: int, charge_id: str, reason: str = "Apelación de baneo no entregable") -> bool:
    pay = _payments()
    if pay is not None and hasattr(pay, "process_star_refund"):
        # Idempotente (ALREADY_REFUNDED = éxito) y anotado en stars_payment_ledger.
        with contextlib.suppress(Exception):
            return bool(await asyncio.wait_for(pay.process_star_refund(bot, int(user_id), str(charge_id), reason=reason),
                                               timeout=TG_TIMEOUT_S + 2))
    try:
        await asyncio.wait_for(bot.refund_star_payment(user_id=int(user_id), telegram_payment_charge_id=str(charge_id)),
                               timeout=TG_TIMEOUT_S)
        return True
    except Exception as ex:
        logger.error(f"🚨 [Apelaciones] Reembolso FALLIDO del cobro {str(charge_id)[:12]}… de {user_id}: {ex}")
        return False


async def reinstate(event_bot, appeal: dict) -> dict:
    """Desbanea + limpia la marca global y los strikes + crea el enlace de un uso. → {ok, bot, link, expires_at, error}"""
    db = _db()
    group_id, user_id = int(appeal["group_id"]), int(appeal["user_id"])
    unban_bot, error = None, ""
    for candidate in await _candidate_bots(group_id, event_bot):
        try:
            await asyncio.wait_for(candidate.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True),
                                   timeout=TG_TIMEOUT_S)
            unban_bot = candidate
            break
        except Exception as ex:
            error = str(ex)
    if unban_bot is None:
        return {"ok": False, "error": error or "no bot can unban"}
    if not appeal.get("prior_global_ban"):
        # La marca la activó ESTE baneo (comprobado al ofrecer). v25: también se borra su registro de origen.
        with contextlib.suppress(Exception):
            if not await db.lift_global_ban_if_origin(user_id, group_id):
                await db.unban_user_global(user_id)   # baneo anterior a v25 (sin origen registrado)
    with contextlib.suppress(Exception):
        await db.reset_user_strikes(group_id, user_id)
    cfg = await db.get_appeal_config(group_id)
    expires_at = int(time.time()) + int(cfg["link_ttl_min"]) * 60
    link = None
    for candidate in [unban_bot] + [b for b in await _candidate_bots(group_id) if b is not unban_bot]:
        try:
            invite = await asyncio.wait_for(candidate.create_chat_invite_link(
                chat_id=group_id, name=f"appeal-{int(appeal['id'])}", expire_date=expires_at, member_limit=1),
                timeout=TG_TIMEOUT_S)
            link, unban_bot = invite.invite_link, candidate
            break
        except Exception as ex:
            error = str(ex)
    return {"ok": True, "bot": unban_bot, "link": link, "expires_at": expires_at if link else None,
            "ttl_min": int(cfg["link_ttl_min"]), "error": "" if link else error}


async def process_payment(bot, message) -> str:
    """successful_payment de una apelación. Idempotente. Devuelve el estado final ('reinstated', 'refunded', …)."""
    db = _db()
    payment = message.successful_payment
    user = message.from_user
    parsed = parse_payload(payment.invoice_payload)
    charge = payment.telegram_payment_charge_id
    lang = await _lang_for(user)
    if not parsed:
        await _refund(bot, user.id, charge)
        return "refunded"
    group_id, _uid, appeal_id = parsed
    result, appeal = await db.appeal_claim_payment(appeal_id, user.id, charge, int(payment.total_amount),
                                                    bot_id=getattr(bot, "id", None))
    if result == "duplicate":
        logger.info(f"⚖️ [Apelaciones] Cobro {charge[:12]}… ya procesado (apelación {appeal_id}).")
        return appeal.get("status") if appeal else "duplicate"
    if result == "invalid":
        refunded = await _refund(bot, user.id, charge)
        with contextlib.suppress(Exception):
            await message.answer(_t(lang, "⚠️ Esta apelación ya no era válida. ", "⚠️ This appeal was no longer valid. ")
                                 + (_t(lang, "Se reembolsaron tus Stars.", "Your Stars were refunded.") if refunded else
                                    _t(lang, "Contacta al soporte para el reembolso.", "Contact support for a refund.")) + SIG,
                                 parse_mode="HTML")
        logger.warning(f"⚖️ [Apelaciones] Cobro {charge[:12]}… inválido para la apelación {appeal_id}: reembolso={refunded}.")
        return "refunded" if refunded else "failed"

    return await complete_appeal(bot, appeal, lang)


async def complete_appeal(bot, appeal: dict, lang: str) -> str:
    """Tras el claim: desbaneo + enlace, o reembolso si no es posible. Escribe al usuario con el bot que cobró."""
    db = _db()
    group_id, user_id, appeal_id, charge = int(appeal["group_id"]), int(appeal["user_id"]), int(appeal["id"]), appeal["charge_id"]

    async def tell(text, kb=None):
        with contextlib.suppress(Exception):
            return await asyncio.wait_for(bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML"), timeout=TG_TIMEOUT_S)
        return None

    title = await _group_title(bot, group_id)
    outcome = await reinstate(bot, appeal)
    if not outcome["ok"]:
        refunded = await _refund(bot, user_id, charge)
        await db.appeal_update(appeal_id, status="refunded" if refunded else "failed", error=outcome["error"])
        await tell(_t(lang, "⚠️ <b>No se pudo completar el desbaneo</b> (el bot no tiene permisos en la comunidad). ",
                      "⚠️ <b>The unban could not be completed</b> (the bot lacks rights in the community). ")
                   + (_t(lang, "Tus Stars fueron reembolsadas.", "Your Stars were refunded.") if refunded else
                      _t(lang, "Contacta al soporte: el reembolso automático falló.",
                         "Contact support: the automatic refund failed.")) + SIG)
        _publish(group_id, user_id, "refunded" if refunded else "failed")
        logger.error(f"🚨 [Apelaciones] Apelación {appeal_id} pagada sin desbaneo posible en {group_id}: reembolso={refunded}.")
        return "refunded" if refunded else "failed"

    group = html.escape(title or str(group_id))
    if outcome["link"]:
        text = _t(lang,
                  f"✅ <b>Apelación aceptada</b>\n\nYa no estás baneado de <b>{group}</b> y tus advertencias se reiniciaron.\n"
                  f"Usa el botón para volver: el enlace es de <b>un solo uso</b> y caduca en <b>{outcome['ttl_min']} min</b>.",
                  f"✅ <b>Appeal accepted</b>\n\nYou are no longer banned from <b>{group}</b> and your warnings were reset.\n"
                  f"Use the button to rejoin: the link is <b>single-use</b> and expires in <b>{outcome['ttl_min']} min</b>.") + SIG
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=_t(lang, f"🚪 Volver a {title or 'la comunidad'}", f"🚪 Rejoin {title or 'the community'}")[:60], url=outcome["link"])]])
    else:
        text = _t(lang,
                  f"✅ <b>Apelación aceptada</b>\n\nYa no estás baneado de <b>{group}</b>. El bot no pudo crear un enlace de "
                  f"invitación: vuelve con el enlace público de la comunidad o pide uno a un administrador.",
                  f"✅ <b>Appeal accepted</b>\n\nYou are no longer banned from <b>{group}</b>. The bot could not create an "
                  f"invite link: rejoin with the community's public link or ask an admin for one.") + SIG
        kb = None
    link_msg = await tell(text, kb)
    await db.appeal_update(appeal_id, status="reinstated", reinstated_at=int(time.time()), invite_link=outcome["link"],
                           link_expires_at=outcome["expires_at"], link_bot_id=getattr(outcome["bot"], "id", None),
                           dm_bot_id=getattr(bot, "id", None),
                           link_message_id=getattr(link_msg, "message_id", None), error=outcome["error"] or None)
    _publish(group_id, user_id, "reinstated")
    logger.info(f"⚖️ [Apelaciones] Apelación {appeal_id}: {user_id} desbaneado en {group_id} ({appeal['price_stars']} ⭐).")
    await _after_paid_unban(bot, appeal, outcome, lang)   # v40 · historial limpio + #UNBAN_PAGADO
    return "reinstated"


# --------------------------------------------------------------------------
# 5 · Limpieza del enlace (al usarse o al caducar)
# --------------------------------------------------------------------------
async def _cleanup_link(appeal: dict, status: str) -> None:
    db = _db()
    group_id = int(appeal["group_id"])
    link_bot = await _bot_by_id(group_id, appeal.get("link_bot_id"))
    if link_bot is not None and appeal.get("invite_link"):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(link_bot.revoke_chat_invite_link(chat_id=group_id, invite_link=appeal["invite_link"]),
                                   timeout=TG_TIMEOUT_S)
    dm_bot = await _bot_by_id(group_id, appeal.get("dm_bot_id"))
    if dm_bot is not None and appeal.get("link_message_id"):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(dm_bot.delete_message(chat_id=int(appeal["user_id"]), message_id=int(appeal["link_message_id"])),
                                   timeout=TG_TIMEOUT_S)
    fields = {"status": status, "invite_link": None}
    if status == "rejoined":
        fields["rejoined_at"] = int(time.time())
    await db.appeal_update(int(appeal["id"]), **fields)


async def on_member_joined(group_id: int, user_id: int, invite_link) -> bool:
    """Gancho de groups.track_member_updates: entrada por un enlace de apelación → revocar + borrar el mensaje."""
    name = str(getattr(invite_link, "name", "") or "")
    match = LINK_NAME_RE.match(name)
    if not match:
        return False
    try:
        appeal = await _db().appeal_get(int(match.group(1)))
        if not appeal or int(appeal["group_id"]) != int(group_id) or appeal["status"] != "reinstated":
            return False
        if int(appeal["user_id"]) != int(user_id):
            logger.warning(f"⚠️ [Apelaciones] El enlace de la apelación {appeal['id']} lo usó otra cuenta en {group_id}.")
        await _cleanup_link(appeal, "rejoined")
        _publish(group_id, user_id, "rejoined")
        return True
    except Exception as ex:
        logger.debug(f"[Apelaciones] Limpieza al reingresar fallida ({group_id}/{user_id}): {ex}")
        return False


async def sweep_once() -> dict:
    db = _db()
    expired_offers = await db.appeal_expire_offers()
    links = 0
    for appeal in await db.appeal_due_links(limit=50):
        await _cleanup_link(appeal, "reinstated")   # el desbaneo se mantiene; solo se retira el enlace caducado
        links += 1
    recovered = 0
    for appeal in await db.appeal_stuck_paid():
        # Cobro reclamado pero el proceso se interrumpió antes del desbaneo: se completa (o reembolsa) con el bot que cobró.
        paid_bot = await _bot_by_id(int(appeal["group_id"]), appeal.get("dm_bot_id"))
        if paid_bot is None:
            continue
        lang = getattr(_db(), "DEFAULT_LANG", "en")
        with contextlib.suppress(Exception):
            lang = (await _db().get_user_lang(int(appeal["user_id"]))) or lang
        await complete_appeal(paid_bot, appeal, lang)
        recovered += 1
    purge_state()
    return {"expired_offers": expired_offers, "expired_links": links, "recovered": recovered}


async def appeals_sweeper_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            stats = await sweep_once()
            if stats["expired_offers"] or stats["expired_links"] or stats["recovered"]:
                logger.info(f"⚖️ [Apelaciones] Barrido: {stats['expired_offers']} oferta(s) y {stats['expired_links']} enlace(s) caducados.")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(f"❌ [Apelaciones] Barrido fallido: {ex}")
        await asyncio.sleep(SWEEP_INTERVAL_S)


STATUS_LABELS = {
    "offered": ("📨 Ofrecida", "📨 Offered"), "dm_failed": ("📭 Sin entregar (espera /start)", "📭 Undelivered (awaits /start)"),
    "paid": ("💳 Pagada", "💳 Paid"), "reinstated": ("✅ Desbaneado", "✅ Unbanned"), "rejoined": ("🚪 Reingresó", "🚪 Rejoined"),
    "refunded": ("↩️ Reembolsada", "↩️ Refunded"), "failed": ("🚨 Error", "🚨 Error"), "expired": ("⌛ Caducada", "⌛ Expired"),
    "superseded": ("🔁 Sustituida", "🔁 Superseded"),
}