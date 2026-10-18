"""
micvip_audit.py — The Bunker OS v22 · Auditoría MicVIP de extremo a extremo (anti-estafa)

Cadena de evidencia por cada compra en Telegram Stars (XTR):
  1. COBRO ....... main.py (ActivityTrackerMiddleware) ve el successful_payment → record_purchase() guarda alias,
                   importe, charge_id y la hora exacta de compra. Idempotente por charge_id.
  2. PASE ........ verify_grant() relee vip_mic_passes unos segundos después: si el pase existe y cubre la compra,
                   guarda la expiración REAL (grant_status='granted'); si nunca aparece → 'missing' (alarma ⚠️).
  3. CENTINELA ... assistant.py llama note_sentinel_applied() cuando el radar pone (o encuentra) al comprador al 100 %
                   en la sala: queda la hora en que el permiso se aplicó de forma autónoma, sin intervención manual.

No depende de handlers/payments.py: detecta MicVIP por el payload de la factura (MICVIP_PAYLOAD_MARKERS).
Ningún diccionario en memoria crece sin límite. The Bunker Command OS © 2026 — Cloud Media Management
"""
from typing import Optional
import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("micvip_audit")

MICVIP_PAYLOAD_MARKERS = tuple(
    m.strip().lower() for m in os.getenv("MICVIP_PAYLOAD_MARKERS", "vipmic,micvip,mic_vip,vip_mic").split(",") if m.strip()
)
MICVIP_NOMINAL_HOURS = 24
VERIFY_DELAYS_S = (3, 10, 30, 120)        # el pase lo concede el handler de pagos justo después del cobro
SENTINEL_NOTE_INTERVAL_S = 60.0           # como mucho una escritura por (grupo, usuario) y minuto
SENTINEL_NOTE_MAX = 5000
_sentinel_notes: dict = {}                # (group_id, user_id, applied) → monotonic_ts
_BG: set = set()
_GROUP_ID_RE = re.compile(r"-100\d{6,}|-\d{6,}")
GRANT_LISTENERS_MAX = 8
_grant_listeners: list = []               # v23 · async fn(group_id, user_id, charge_id, expires_at) — p. ej. vip_roles


def register_grant_listener(fn) -> None:
    """v23 · Suscribe una corrutina a los pases VERIFICADOS (lista acotada, sin duplicados)."""
    if fn not in _grant_listeners and len(_grant_listeners) < GRANT_LISTENERS_MAX:
        _grant_listeners.append(fn)


def _notify_granted(group_id: int, user_id: int, charge_id: str, expires_at: int) -> None:
    for fn in list(_grant_listeners):
        _spawn(fn(int(group_id), int(user_id), str(charge_id), int(expires_at)), name=f"micvip_listener:{group_id}:{user_id}")


def _db():
    from database import database as db
    return db


def _spawn(coro, name: Optional[str] = None):
    try:
        task = asyncio.get_running_loop().create_task(coro, name=name)
    except RuntimeError:
        return None
    _BG.add(task)
    task.add_done_callback(_BG.discard)
    return task


# --------------------------------------------------------------------------
# Detección
# --------------------------------------------------------------------------
def is_micvip_payload(payload) -> bool:
    raw = str(payload or "").lower()
    return bool(raw) and any(marker in raw for marker in MICVIP_PAYLOAD_MARKERS)


def group_from_payload(payload):
    """Primer id de comunidad (negativo) del payload, p. ej. 'vipmic_-1001234567890_42' → -1001234567890."""
    match = _GROUP_ID_RE.search(str(payload or ""))
    return int(match.group(0)) if match else None


# --------------------------------------------------------------------------
# 1 + 2 · Cobro y verificación del pase
# --------------------------------------------------------------------------
async def record_purchase(*, group_id: int, user_id: int, charge_id: str, amount: int = 0, payload: str = "",
                          currency: str = "XTR", username: Optional[str] = None, full_name: Optional[str] = None,
                          purchased_at: Optional[int] = None, verify: bool = True) -> bool:
    """Registra la compra y programa la verificación del pase. Devuelve True si el cobro es nuevo."""
    if not (group_id and user_id and charge_id):
        return False
    ts = int(purchased_at or time.time())
    inserted = await _db().micvip_audit_record_purchase(
        charge_id, int(group_id), int(user_id), ts, amount_stars=int(amount or 0), currency=currency,
        payload=payload, username=username, full_name=full_name,
    )
    if inserted:
        logger.info(f"🎟️ [MicVIP Audit] Compra {charge_id[:12]}… de {user_id} en {group_id}: {amount} {currency}.")
        if verify:
            _spawn(verify_grant(int(group_id), int(user_id), charge_id, ts), name=f"micvip_verify:{group_id}:{user_id}")
    return inserted


async def verify_grant(group_id: int, user_id: int, charge_id: str, purchased_at: int, delays=None) -> str:
    """
    Comprueba que el pase cubra la compra: expiración ≥ compra + 23 h (margen para renovaciones y relojes).
    Guarda la expiración real. Si tras todos los intentos no existe → 'missing' y alerta en el log.
    """
    db = _db()
    minimum = int(purchased_at) + (MICVIP_NOMINAL_HOURS - 1) * 3600
    for delay in (delays if delays is not None else VERIFY_DELAYS_S):
        await asyncio.sleep(delay)
        try:
            expiry = await db.get_vip_mic_expiry_ts(user_id, group_id)
        except Exception as ex:
            logger.debug(f"[MicVIP Audit] Lectura del pase fallida ({group_id}/{user_id}): {ex}")
            continue
        if expiry and expiry >= minimum:
            await db.micvip_audit_set_grant(charge_id, "granted", expiry)
            _notify_granted(group_id, user_id, charge_id, expiry)   # v23 · rol VIP automático
            return "granted"
    await db.micvip_audit_set_grant(charge_id, "missing")
    logger.error(f"🚨 [MicVIP Audit] Cobro {charge_id} de {user_id} en {group_id} SIN pase MicVIP concedido. Revisar de inmediato.")
    return "missing"


async def backfill_legacy(limit: int = 5000) -> int:
    """
    Una sola vez al arrancar: importa los cobros MicVIP históricos de processed_payments (idempotente). La
    expiración se estima en compra + 24 h y la fila queda marcada source='backfill' para no confundirla con
    evidencia en vivo.
    """
    db = _db()
    try:
        rows = await db.micvip_audit_legacy_payments(MICVIP_PAYLOAD_MARKERS, limit)
    except Exception as ex:
        logger.debug(f"[MicVIP Audit] Backfill no disponible: {ex}")
        return 0
    imported = 0
    for row in rows:
        group_id = group_from_payload(row["payload"])
        if not group_id or not row.get("processed_at"):
            continue
        ts = int(row["processed_at"])
        ok = await db.micvip_audit_record_purchase(
            row["charge_id"], group_id, int(row["user_id"]), ts, payload=row["payload"], source="backfill",
            expires_at=ts + MICVIP_NOMINAL_HOURS * 3600, grant_status="granted",
        )
        imported += 1 if ok else 0
    if imported:
        logger.info(f"🎟️ [MicVIP Audit] Backfill: {imported} compra(s) históricas importadas.")
    return imported


# --------------------------------------------------------------------------
# 3 · Evidencia del Centinela (llamada síncrona desde el bucle del radar)
# --------------------------------------------------------------------------
def note_sentinel_applied(group_id: int, user_id: int, applied: bool = True) -> None:
    """No bloquea el radar: como mucho una escritura por (grupo, usuario, resultado) y minuto."""
    key = (int(group_id), int(user_id), bool(applied))
    now = time.monotonic()
    last = _sentinel_notes.get(key)
    if last is not None and now - last < SENTINEL_NOTE_INTERVAL_S:
        return
    _sentinel_notes.pop(key, None)
    _sentinel_notes[key] = now
    overflow = len(_sentinel_notes) - SENTINEL_NOTE_MAX
    if overflow > 0:
        for old in list(_sentinel_notes)[:overflow]:
            _sentinel_notes.pop(old, None)

    async def _write():
        try:
            await _db().micvip_audit_mark_sentinel(int(group_id), int(user_id), applied=applied)
        except Exception as ex:
            logger.debug(f"[MicVIP Audit] Evidencia del Centinela no guardada ({group_id}/{user_id}): {ex}")
    _spawn(_write(), name=f"micvip_sentinel:{group_id}:{user_id}")


def purge_sentinel_notes(max_age_s: float = SENTINEL_NOTE_INTERVAL_S * 2) -> int:
    now = time.monotonic()
    stale = [k for k, ts in list(_sentinel_notes.items()) if now - ts > max_age_s]
    for k in stale:
        _sentinel_notes.pop(k, None)
    return len(stale)


# --------------------------------------------------------------------------
# Lectura y presentación
# --------------------------------------------------------------------------
def classify(record: dict, now: int) -> dict:
    """Estado operativo + evidencia de un registro (misma regla en la API y en el bot)."""
    grant = record.get("grant_status") or "pending"
    expires = record.get("expires_at")
    if grant == "missing":
        state = "issue"
    elif grant == "pending" and not expires:
        state = "verifying"
    elif expires and int(expires) > now:
        state = "active"
    else:
        state = "expired"
    if record.get("sentinel_applied_at"):
        evidence = "applied"
    elif record.get("source") == "backfill":
        evidence = "historic"
    elif state == "active":
        evidence = "awaiting_join"
    elif state == "expired":
        evidence = "not_joined"
    else:
        evidence = "none"
    return {"state": state, "evidence": evidence}


async def get_audit(group_id: int, status: str = "all", limit: int = 20, offset: int = 0) -> dict:
    data = await _db().micvip_audit_list(int(group_id), status, limit, offset)
    now = data["now"]
    for record in data["records"]:
        record.update(classify(record, now))
        record.pop("payload", None)
    data["window_hours"] = MICVIP_NOMINAL_HOURS
    return data


STATE_ICON = {"active": "🟢", "expired": "🔴", "issue": "⚠️", "verifying": "⏳"}
TEXT = {
    "es": {"title": "🎟️ <b>Auditoría MicVIP</b>", "purchases": "Compras", "active": "Activos", "stars": "Stars",
           "issues": "Incidencias", "buyers": "Compradores", "window": "Pase de {h} h · Horario {tz}",
           "empty": "Aún no hay compras de MicVIP registradas en esta comunidad.",
           "bought": "Compra", "until": "Vence", "active_s": "Activo", "expired_s": "Expirado",
           "issue_s": "Pase NO concedido — revisar", "verifying_s": "Verificando pase…",
           "applied": "Centinela aplicó el 100 % a las {t}", "awaiting_join": "Pase listo; aún no entra al videochat",
           "not_joined": "No entró al videochat durante el pase", "historic": "Registro histórico (anterior a la auditoría)",
           "page": "Página {p}/{n}", "failures": "{n} intento(s) del Centinela fallido(s)"},
    "en": {"title": "🎟️ <b>MicVIP Audit</b>", "purchases": "Purchases", "active": "Active", "stars": "Stars",
           "issues": "Issues", "buyers": "Buyers", "window": "{h} h pass · Timezone {tz}",
           "empty": "No MicVIP purchases recorded in this community yet.",
           "bought": "Purchased", "until": "Expires", "active_s": "Active", "expired_s": "Expired",
           "issue_s": "Pass NOT granted — review", "verifying_s": "Verifying pass…",
           "applied": "Sentinel applied 100% at {t}", "awaiting_join": "Pass ready; not in the voice chat yet",
           "not_joined": "Did not join the voice chat during the pass", "historic": "Historic record (pre-audit)",
           "page": "Page {p}/{n}", "failures": "{n} failed Sentinel attempt(s)"},
}


def _tz():
    name = os.getenv("BOT_TIMEZONE", "America/Bogota")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:
        return timezone.utc, "UTC"


def fmt_ts(ts, with_seconds: bool = True) -> str:
    if not ts:
        return "—"
    tz, _ = _tz()
    return datetime.fromtimestamp(int(ts), tz).strftime("%Y-%m-%d %H:%M:%S" if with_seconds else "%H:%M")


def render_bot_page(data: dict, lang: str = "es", page: int = 1, per_page: int = 8) -> str:
    """Página HTML de la consola del bot (alias escapados; nunca expone el payload ni el charge_id completo)."""
    t = TEXT["en" if lang == "en" else "es"]
    s = data.get("summary", {})
    _, tz_name = _tz()
    lines = [t["title"], "",
             f"🛒 {t['purchases']}: <b>{s.get('purchases', 0)}</b> · 🟢 {t['active']}: <b>{s.get('active', 0)}</b> · "
             f"⭐ {t['stars']}: <b>{s.get('stars', 0)}</b>",
             f"👥 {t['buyers']}: <b>{s.get('buyers', 0)}</b>" + (f" · ⚠️ {t['issues']}: <b>{s['issues']}</b>" if s.get("issues") else ""),
             f"<i>{t['window'].format(h=data.get('window_hours', MICVIP_NOMINAL_HOURS), tz=tz_name)}</i>", ""]
    records = data.get("records") or []
    if not records:
        lines.append(t["empty"])
        return "\n".join(lines)
    start = (page - 1) * per_page
    for i, r in enumerate(records, start=start + 1):
        who = f"@{html.escape(r['username'])}" if r.get("username") else html.escape(r.get("full_name") or "—")
        state_label = {"active": t["active_s"], "expired": t["expired_s"], "issue": t["issue_s"],
                       "verifying": t["verifying_s"]}[r["state"]]
        evidence = t["applied"].format(t=fmt_ts(r["sentinel_applied_at"], False)) if r["evidence"] == "applied" \
            else t.get(r["evidence"], "")
        lines.append(f"<b>{i}. {who}</b> <code>{int(r['user_id'])}</code> · ⭐ {int(r.get('amount_stars') or 0)}")
        lines.append(f"   🛒 {t['bought']}: <code>{fmt_ts(r['purchased_at'])}</code>")
        lines.append(f"   ⏳ {t['until']}: <code>{fmt_ts(r.get('expires_at'))}</code>")
        lines.append(f"   {STATE_ICON[r['state']]} {state_label}" + (f" · {evidence}" if evidence else ""))
        if r.get("sentinel_failures"):
            lines.append(f"   ⚠️ {t['failures'].format(n=int(r['sentinel_failures']))}")
    pages = max(1, -(-int(data.get("total", 0)) // per_page))
    lines += ["", f"<i>{t['page'].format(p=page, n=pages)}</i>"]
    return "\n".join(lines)