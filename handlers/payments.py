"""
payments.py — The Bunker OS (Aiogram 3.x)

Facturación oficial EXCLUSIVA con Telegram Stars (XTR).
Gestiona la facturación automatizada de licencias PRO/ULTRA PRO, pases VIP de micrófono,
membresías de canales con enlaces criptográficos de un solo uso, cola de speakers y propinas.

Fase 3 · Exclusividad financiera y SLA de pagos:
- Sin pasarelas externas (PayPal, Binance Pay, TON, Stripe ni fiat): todo bien o servicio
  digital se adquiere in-app con Telegram Stars, conforme a las políticas de Telegram,
  Apple App Store (Guideline 3.1.1) y Google Play Payments.
- Pre-checkout en memoria: registro de ofertas emitidas + caché de planes; ninguna consulta
  a disco bloquea la confirmación (SLA < 2 s, objetivo < 50 ms).
- Libro mayor `stars_payment_ledger`: telegram_payment_charge_id, user_id, chat_id e importe
  de cada pago, con estado de reembolso, para trazabilidad y conciliación.
- `process_star_refund()`: devolución transparente vía refundStarPayment, idempotente.
The Bunker Command OS © 2026 — Cloud Media Management
"""
import os
import sys
import asyncio
import contextlib
import html
import importlib
import logging
import re
import time
from aiogram import Router, F, Bot
try:
    from telegram_html import normalize_telegram_html, telegram_html_to_plain  # type: ignore[import-not-found]
except ImportError:  # Compatibilidad con entornos sin la dependencia opcional.
    def normalize_telegram_html(value: str) -> str:
        """
        Respaldo SEGURO sin telegram_html.py: quita las etiquetas y ESCAPA el resto. El resultado se envía
        con parse_mode=HTML, así que un '<' o '&' sin escapar haría fallar el mensaje ("can't parse
        entities"). Se pierde el formato, nunca la entrega. Despliega telegram_html.py para conservarlo.
        """
        if not value:
            return ""
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = html.unescape(re.sub(r"</?[A-Za-z][^>]*>", "", text))
        return html.escape(text.strip(), quote=False)

    def telegram_html_to_plain(value: str) -> str:
        """Convierte etiquetas HTML a texto plano de forma conservadora."""
        if not value:
            return ""
        text = html.unescape(str(value))
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</?[^>]+>", "", text)
        text = text.replace("\xa0", " ")
        return text.strip()
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, WebAppInfo
)
from aiogram.filters import Command, CommandObject

# ==========================================
# 🧠 ESTADOS Y CONFIGURACIÓN BASE
# ==========================================
logger = logging.getLogger("payments_gateway")
router = Router()

# Diccionario de estado conversacional para capturar montos manuales de Stars
CUSTOM_TIP_STATES: dict[tuple[int, int], int] = {}  # (bot_id, user_id) -> group_id
_CUSTOM_TIP_TS: dict[tuple[int, int], float] = {}   # (bot_id, user_id) -> instante de apertura
CUSTOM_TIP_TTL_SECONDS = 600
MAX_TIP_STARS = 10000                               # Tope de Telegram por factura en XTR

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    """Tarea en segundo plano con referencia fuerte (evita que el GC la cancele)."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _open_custom_tip(bot_id: int, user_id: int, group_id: int) -> None:
    key = (bot_id, user_id)
    CUSTOM_TIP_STATES[key] = group_id
    _CUSTOM_TIP_TS[key] = time.monotonic()


def _close_custom_tip(bot_id: int, user_id: int):
    key = (bot_id, user_id)
    _CUSTOM_TIP_TS.pop(key, None)
    return CUSTOM_TIP_STATES.pop(key, None)


def _awaiting_custom_tip(message: Message, bot: Bot) -> bool:
    """
    Filtro del handler de monto libre. CRÍTICO: antes el handler capturaba TODO texto privado
    y retornaba sin hacer nada cuando no había propina pendiente. Como este router se incluye
    primero, la consola de user_private.py jamás recibía las respuestas de sus asistentes.
    """
    if not message.from_user:
        return False
    key = (bot.id, message.from_user.id)
    if key not in CUSTOM_TIP_STATES:
        return False
    opened = _CUSTOM_TIP_TS.get(key, 0.0)
    if time.monotonic() - opened > CUSTOM_TIP_TTL_SECONDS:
        _close_custom_tip(bot.id, message.from_user.id)
        return False
    return True

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://thebunkerapp2.netlify.app/")

# ==========================================
# 💰 TARIFAS Y CONFIGURACIÓN DE FACTURACIÓN
# ==========================================
# Moneda única de la plataforma: Telegram Stars (XTR). Fuente de verdad de precios,
# también consumida por handlers/user_private.py.
STARS_CURRENCY = "XTR"
PRICE_PRO_STARS = 300          # Licencia PRO (30 días)
PRICE_ULTRAPRO_STARS = 600     # Licencia ULTRA PRO (30 días)
DEFAULT_PRICE_VIP_MIC = 50     # Tarifa base en Stars para pase 24h
DEFAULT_PRICE_SPEAKER = 25     # Tarifa base turno prioritario AMA
DEFAULT_TIP_AMOUNT = 10        # Tarifa sugerida de propina

ASSISTANT_INVITE_URL = "https://t.me/Alphacentinel?startgroup=true"


def get_lang(lang_code: str) -> str:
    """Detecta el idioma del operador para renderizar la facturación en Stars."""
    return "es" if lang_code and lang_code.startswith("es") else "en"


def _user_private_module():
    """Acceso diferido a handlers.user_private (fuente de verdad de la identidad maestro/clon)."""
    return sys.modules.get("handlers.user_private") or sys.modules.get(f"{__package__}.user_private" if __package__ else "")


def _master_bot_id() -> int:
    up_mod = _user_private_module()
    if up_mod is not None:
        resolver = getattr(up_mod, "_resolve_master_bot_id", None)
        if callable(resolver):
            try:
                resolved = int(resolver() or 0)
                if resolved:
                    return resolved
            except Exception:
                pass
    head = os.getenv("BOT_TOKEN", "").split(":", 1)[0].strip()
    return int(head) if head.isdigit() else 0


def is_clone_bot(bot: Bot) -> bool:
    """
    Detecta si la instancia actual es un bot clon del maestro comparando bot.id con el ID del
    token maestro. (La versión anterior leía bot.username, atributo que aiogram no expone, y
    siempre devolvía False: los clones cobraban licencias en su propio balance.)
    """
    master_id = _master_bot_id()
    return bool(master_id) and getattr(bot, "id", 0) != master_id


def is_super_admin(user_id: int) -> bool:
    try:
        return int(user_id) in SUPER_ADMIN_IDS
    except (TypeError, ValueError):
        return False


def get_master_bot_username() -> str | None:
    """Devuelve el nombre de usuario del Bot Maestro configurado para redirigir pagos."""
    up_mod = _user_private_module()
    if up_mod is not None:
        getter = getattr(up_mod, "get_master_bot_username", None)
        if callable(getter):
            try:
                runtime_username = (getter() or "").strip().lstrip("@")
                if runtime_username:
                    return runtime_username
            except Exception:
                pass
    username = (
        os.getenv("MASTER_BOT_USERNAME")
        or os.getenv("MASTER_BOT_USERNAME_TG")
        or os.getenv("MASTER_BOT")
        or os.getenv("MASTER_BOT_USER")
        or "thebunkerapp_bot"
    ).strip().lstrip("@")
    return username or None


async def is_user_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el usuario ostenta el rango de Creador del grupo/canal o Arquitecto Supremo."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status == "creator"
    except Exception:
        return False


async def auto_delete_pair(msg1: Message, msg2: Message, delay: int = 15):
    """Auto-destrucción dual para mantener el chat grupal libre de clutter visual."""
    await asyncio.sleep(delay)
    try:
        await msg1.delete()
    except Exception:
        pass
    try:
        await msg2.delete()
    except Exception:
        pass


async def resolve_chat_context(bot: Bot, chat_id: int) -> tuple[str, str]:
    """Identifica si el chat_id corresponde a un Canal o a un Grupo/Supergrupo."""
    try:
        chat = await bot.get_chat(chat_id)
        if chat.type == "channel":
            return "c", f"cpanel_{chat_id}"
    except Exception:
        pass
    return "g", f"gpanel_{chat_id}"


def _clone_subscription_redirect(lang: str, plan: str, chat_id: int):
    """Construye el aviso y botón que redirige el cobro de una suscripción hacia el Bot Maestro."""
    master_username = get_master_bot_username()
    if not master_username:
        return None, None

    text = (
        "⭐ <b>Suscripción Oficial — Cloud Media Management</b>\n\n"
        "Los planes PRO y ULTRA PRO son un servicio directo de la plataforma y se facturan siempre desde el <b>Bot Maestro</b>, para no descontar Stars del balance de tu Bot Clon.\n\n"
        "Pulsa el botón para completar el pago de forma segura:\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        "⭐ <b>Official Subscription — Cloud Media Management</b>\n\n"
        "PRO and ULTRA PRO plans are a direct platform service and are always billed through the <b>Master Bot</b>, so they never draw Stars from your Bot Clone's balance.\n\n"
        "Tap the button to complete payment securely:\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⭐ Pagar en el Bot Maestro" if lang == "es" else "⭐ Pay via Master Bot",
            url=f"https://t.me/{master_username}?start=sub_{plan}_{chat_id}"
        )]
    ])
    return text, markup


# ==========================================
# 🗄️ RESOLUTORES DINÁMICOS DE BASE DE DATOS
# ==========================================
_DB_MODULE = None


def _get_db_module():
    """
    Resuelve el módulo de persistencia. La versión anterior intentaba `handlers.database`
    (inexistente): TODAS las llamadas fallaban y se ocultaban en silencio — licencias sin
    aprobar, pases VIP sin registrar, idempotencia solo en RAM.
    """
    global _DB_MODULE
    if _DB_MODULE is not None:
        return _DB_MODULE
    last_error = None
    for name in ("database.database", f"{__package__}.database" if __package__ else None):
        if not name:
            continue
        try:
            _DB_MODULE = importlib.import_module(name)
            return _DB_MODULE
        except ImportError as ex:
            last_error = ex
    raise ImportError(f"No se encontró el módulo de base de datos: {last_error}")


async def _call_db_fn(function_name: str, *args, **kwargs):
    """Resuelve funciones de persistencia de forma lazy evitando dependencias circulares."""
    db_module = _get_db_module()
    func = getattr(db_module, function_name, None)
    if func is None:
        raise AttributeError(f"{function_name} not found in database module")
    return await func(*args, **kwargs)


_SEEN_PAYMENTS: dict = {}
_SEEN_PAYMENTS_MAX = 5000


async def mark_payment_processed(charge_id: str, user_id: int, payload: str) -> bool:
    key = f"{charge_id}:{user_id}:{payload}"
    if key in _SEEN_PAYMENTS:
        return False

    try:
        result = await _call_db_fn("mark_payment_processed", charge_id, user_id, payload)
        if result is False:
            return False
    except Exception as ex:
        # Sin base de datos se conserva al menos la idempotencia en memoria.
        logger.error(f"❌ [Pagos] No se pudo persistir el cargo {charge_id}: {ex}")

    _SEEN_PAYMENTS[key] = time.time()
    if len(_SEEN_PAYMENTS) > _SEEN_PAYMENTS_MAX:
        for old_key in sorted(_SEEN_PAYMENTS, key=_SEEN_PAYMENTS.get)[: _SEEN_PAYMENTS_MAX // 2]:
            _SEEN_PAYMENTS.pop(old_key, None)
    return True


async def get_group_tier(chat_id: int) -> str:
    try:
        return await _call_db_fn("get_group_tier", chat_id) or "free"
    except Exception:
        return "free"


async def approve_group(group_id: int, tier: str, duration_days: int = 30):
    return await _call_db_fn("approve_group", group_id=group_id, tier=tier, duration_days=duration_days)


def _sync_current_license(chat_id: int):
    db_module = _get_db_module()
    with db_module.get_db_connection() as conn:
        return conn.execute(
            "SELECT tier, CAST(ROUND(julianday(expires_at) - julianday('now')) AS INTEGER) "
            "FROM approved_groups WHERE group_id = ? AND expires_at IS NOT NULL",
            (chat_id,)
        ).fetchone()


_LICENSE_LOCKS: dict[int, asyncio.Lock] = {}


def license_lock(chat_id: int) -> asyncio.Lock:
    """Serializa leer-vigencia → aprobar por comunidad: dos compras simultáneas no se pisan los días.
    Válido porque el proceso corre en UNA sola réplica (requisito de SQLite en Railway)."""
    lock = _LICENSE_LOCKS.get(int(chat_id))
    if lock is None:
        lock = _LICENSE_LOCKS[int(chat_id)] = asyncio.Lock()
    return lock


async def compute_license_grant(chat_id: int, purchased_tier: str, base_days: int = 30) -> tuple[str, int]:
    """
    Calcula el nivel y los días a conceder sin que el cliente pierda tiempo pagado:
      • Renovación del mismo nivel → se suman los días restantes.
      • PRO → ULTRA PRO → los días PRO restantes se prorratean a la mitad (ULTRA cuesta el doble).
      • Compra de PRO con ULTRA PRO vigente → se conserva ULTRA PRO y se suman 15 días.
    Antes cada pago reiniciaba la vigencia a 30 días desde hoy (renovar antes de tiempo
    hacía perder los días restantes).
    """
    try:
        row = await asyncio.to_thread(_sync_current_license, chat_id)
    except Exception as ex:
        logger.warning(f"⚠️ [Licencias] No se pudo leer la vigencia actual de {chat_id}: {ex}")
        row = None

    if not row or row[1] is None or int(row[1]) <= 0:
        return purchased_tier, base_days

    current_tier, remaining = str(row[0] or "free"), int(row[1])
    if current_tier == purchased_tier:
        return purchased_tier, base_days + remaining
    if current_tier == "pro" and purchased_tier == "ultra_pro":
        return "ultra_pro", base_days + remaining // 2
    if current_tier == "ultra_pro" and purchased_tier == "pro":
        return "ultra_pro", remaining + base_days // 2
    return purchased_tier, base_days


def _sync_record_vip_badge(chat_id: int, user_id: int) -> None:
    """Registra que ESTE bot promovió al usuario solo por el título VIP (para revertirlo al expirar)."""
    db_module = _get_db_module()
    with db_module.get_db_connection() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")   # toma el lock de escritura de entrada (WAL + busy_timeout)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vip_badge_promotions ("
            "group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
            "promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, user_id))"
        )
        conn.execute(
            "INSERT INTO vip_badge_promotions (group_id, user_id, promoted_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET promoted_at = CURRENT_TIMESTAMP",
            (chat_id, user_id)
        )
        conn.commit()


async def get_speaker_price(chat_id: int) -> int | None:
    try:
        return await _call_db_fn("get_speaker_price", chat_id)
    except Exception:
        return None


async def grant_vip_mic(user_id: int, group_id: int):
    try:
        return await _call_db_fn("grant_vip_mic", user_id, group_id)
    except Exception:
        return False


async def set_participant_mic(chat_id: int, user_id: int, muted: bool = False, volume: int = 10000):
    try:
        from assistant import set_participant_mic as spm
        return await spm(chat_id, user_id, muted, volume)
    except Exception:
        try:
            return await _call_db_fn("set_participant_mic", chat_id, user_id, muted, volume)
        except Exception:
            return None


async def get_vip_badge_title(chat_id: int) -> str:
    try:
        return await _call_db_fn("get_vip_badge_title", chat_id) or "VIP 24h"
    except Exception:
        return "VIP 24h"


async def add_to_speaker_queue(chat_id: int, user_id: int, full_name: str, username: str, stars: int):
    try:
        return await _call_db_fn("add_to_speaker_queue", chat_id, user_id, full_name, username, stars)
    except Exception:
        return None


async def get_user_speaker_position(chat_id: int, user_id: int) -> int:
    try:
        pos = await _call_db_fn("get_user_speaker_position", chat_id, user_id)
        return int(pos or 0)
    except Exception:
        return 0


async def record_group_tip(chat_id: int, user_id: int, stars: int):
    try:
        return await _call_db_fn("record_group_tip", chat_id, user_id, stars)
    except Exception:
        return None


async def get_mic_vip_custom_config(chat_id: int) -> dict:
    try:
        return await _call_db_fn("get_mic_vip_custom_config", chat_id) or {}
    except Exception:
        return {}


async def get_tips_config(chat_id: int) -> dict:
    try:
        return await _call_db_fn("get_tips_config", chat_id) or {}
    except Exception:
        return {}


DEFAULT_TIP_PRESETS = (15, 50, 100, 250, 500)


async def get_tip_rules(chat_id: int) -> dict:
    """
    Reglas de propinas que el operador programa desde la Mini App (v8.3): interruptor general, presets y
    monto libre. Si la base de datos no responde se permite la propina con los presets por defecto:
    perder una donación por un fallo transitorio es peor que aceptarla.
    """
    cfg = await get_tips_config(chat_id)
    if not cfg:
        return {"enabled": True, "presets": list(DEFAULT_TIP_PRESETS), "custom_allowed": True, "known": False}
    presets = []
    for value in cfg.get("presets") or DEFAULT_TIP_PRESETS:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= amount <= MAX_TIP_STARS and amount not in presets:
            presets.append(amount)
    return {
        "enabled": bool(cfg.get("enabled")),
        "presets": sorted(presets)[:6] or list(DEFAULT_TIP_PRESETS),
        "custom_allowed": bool(cfg.get("custom_allowed", 1)),
        "known": True,
    }


def _tips_disabled_text(lang: str) -> str:
    return ("⛔ Las propinas están desactivadas en esta comunidad." if lang == "es"
            else "⛔ Tips are disabled in this community.")


async def get_channel_plan(plan_id: int):
    return await _call_db_fn("get_channel_plan", plan_id)


async def get_channel_settings(channel_id: int):
    return await _call_db_fn("get_channel_settings", channel_id)


async def record_channel_subscription(**kwargs):
    return await _call_db_fn("record_channel_subscription", **kwargs)


# ==========================================
# ⚡ FASE 3 · REGISTRO DE OFERTAS Y CACHÉ EN MEMORIA (SLA PRE-CHECKOUT < 2 s)
# ==========================================
# Telegram exige responder al pre_checkout_query en < 10 s; el objetivo de la plataforma es
# < 2 s. Toda factura emitida por este proceso queda registrada aquí (payload → importe), de
# modo que la validación previa al cobro es una búsqueda O(1) en RAM, sin E/S de disco.
# Nota de seguridad: el payload y el importe de una factura los fija el bot al emitirla y el
# usuario no puede alterarlos; la validación solo protege contra ofertas desactualizadas.
OFFER_TTL_SECONDS = 72 * 3600
OFFER_REGISTRY_MAX = 20000
CHANNEL_PLAN_CACHE_TTL = 10.0
PRECHECKOUT_DB_BUDGET_SECONDS = 1.2
PRECHECKOUT_SLA_SECONDS = 2.0

_ISSUED_OFFERS: dict[str, tuple[int, float]] = {}            # payload -> (importe XTR, instante)
_CHANNEL_PLAN_CACHE: dict[int, tuple[float, dict | None]] = {}  # plan_id -> (instante, plan)
# Generación por plan + época global. Una lectura de disco que empezó ANTES de una invalidación
# no puede volver a sembrar la caché con el estado viejo (p. ej. "active" de un plan recién pausado).
_PLAN_CACHE_EPOCH = 0
_PLAN_CACHE_GEN: dict[int, int] = {}


def _plan_cache_token(plan_id: int) -> tuple[int, int]:
    return _PLAN_CACHE_EPOCH, _PLAN_CACHE_GEN.get(int(plan_id), 0)


def _register_offer(payload: str, amount: int) -> None:
    """Registra en memoria una factura emitida (llamar justo antes de send_invoice)."""
    now = time.monotonic()
    _ISSUED_OFFERS[payload] = (int(amount), now)
    if len(_ISSUED_OFFERS) > OFFER_REGISTRY_MAX:
        cutoff = now - OFFER_TTL_SECONDS
        for key in [k for k, (_, ts) in _ISSUED_OFFERS.items() if ts < cutoff]:
            _ISSUED_OFFERS.pop(key, None)
        if len(_ISSUED_OFFERS) > OFFER_REGISTRY_MAX:
            for key in sorted(_ISSUED_OFFERS, key=lambda k: _ISSUED_OFFERS[k][1])[: OFFER_REGISTRY_MAX // 2]:
                _ISSUED_OFFERS.pop(key, None)


def _offer_matches(payload: str, amount: int):
    """True/False si la oferta es conocida; None si no consta (p. ej. tras un reinicio)."""
    entry = _ISSUED_OFFERS.get(payload)
    if entry is None or time.monotonic() - entry[1] > OFFER_TTL_SECONDS:
        return None
    return entry[0] == int(amount)


def _cache_channel_plan(plan_id: int, plan, token: tuple[int, int] | None = None) -> None:
    """token = _plan_cache_token() tomado ANTES de leer de disco; si hubo invalidación entre medias, no se cachea."""
    if token is not None and token != _plan_cache_token(plan_id):
        return
    _CHANNEL_PLAN_CACHE[int(plan_id)] = (time.monotonic(), dict(plan) if plan else None)


def _cached_channel_plan(plan_id: int, max_age: float = CHANNEL_PLAN_CACHE_TTL):
    """(hit, plan): hit=False si no hay entrada fresca en caché."""
    entry = _CHANNEL_PLAN_CACHE.get(int(plan_id))
    if entry is None or time.monotonic() - entry[0] > max_age:
        return False, None
    return True, entry[1]


async def get_channel_plan_cached(plan_id: int):
    """Lectura de plan con caché de CHANNEL_PLAN_CACHE_TTL (10 s); la fuente de verdad es la base de datos."""
    hit, plan = _cached_channel_plan(plan_id)
    if hit:
        return plan
    token = _plan_cache_token(plan_id)
    plan = await get_channel_plan(plan_id)
    _cache_channel_plan(plan_id, plan, token)
    return plan


def invalidate_channel_plan_cache(plan_id: int | None = None) -> None:
    """Llamar al pausar, editar o borrar un plan para que el pre-checkout lo note al instante."""
    global _PLAN_CACHE_EPOCH
    if plan_id is None:
        _PLAN_CACHE_EPOCH += 1
        _CHANNEL_PLAN_CACHE.clear()
        for key in [k for k in _ISSUED_OFFERS if k.startswith("chan_sub_")]:
            _ISSUED_OFFERS.pop(key, None)
        return
    _PLAN_CACHE_GEN[int(plan_id)] = _PLAN_CACHE_GEN.get(int(plan_id), 0) + 1
    _CHANNEL_PLAN_CACHE.pop(int(plan_id), None)
    for key in [k for k in _ISSUED_OFFERS if k.startswith("chan_sub_")]:
        parts = key.split("_")
        if len(parts) > 3 and parts[3] == str(plan_id):
            _ISSUED_OFFERS.pop(key, None)


async def _refresh_channel_plan_cache(plan_id: int) -> None:
    token = _plan_cache_token(plan_id)
    try:
        _cache_channel_plan(plan_id, await asyncio.wait_for(get_channel_plan(plan_id), timeout=10), token)
    except Exception as ex:
        logger.debug(f"Aviso refrescando caché del plan {plan_id}: {ex}")


async def send_stars_invoice(bot: Bot, chat_id: int, title: str, description: str, payload: str,
                             amount: int, reply_markup: InlineKeyboardMarkup | None = None):
    """Único punto de emisión de facturas: siempre XTR, siempre registrada en memoria."""
    amount = int(amount)
    _register_offer(payload, amount)
    return await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label=title, amount=amount)],
        reply_markup=reply_markup
    )


# ==========================================
# 📒 FASE 3 · LIBRO MAYOR DE PAGOS STARS (TRAZABILIDAD)
# ==========================================
_LEDGER_SCHEMA_READY = False


def _payload_chat_id(payload: str) -> int:
    """chat_id de la comunidad asociada a cada familia de payload."""
    try:
        parts = (payload or "").split("_")
        if payload.startswith("chan_sub_"):
            return int(parts[2])
        if payload.startswith("vip_mic_"):
            return int(parts[2])
        if payload.startswith("sub_"):
            return int(parts[2])
        if payload.startswith(("speaker_", "tip_")):
            return int(parts[1])
    except (IndexError, ValueError):
        pass
    return 0


def _ensure_ledger_schema(conn) -> None:
    global _LEDGER_SCHEMA_READY
    if _LEDGER_SCHEMA_READY:
        return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stars_payment_ledger ("
        "telegram_payment_charge_id TEXT PRIMARY KEY, "
        "provider_payment_charge_id TEXT NOT NULL DEFAULT '', "
        "user_id INTEGER NOT NULL, "
        "chat_id INTEGER NOT NULL DEFAULT 0, "
        "stars_amount INTEGER NOT NULL, "
        "currency TEXT NOT NULL DEFAULT 'XTR', "
        "payload TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'paid', "
        "refund_reason TEXT NOT NULL DEFAULT '', "
        "created_at INTEGER NOT NULL, "
        "refunded_at INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stars_ledger_user ON stars_payment_ledger (user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stars_ledger_chat ON stars_payment_ledger (chat_id, created_at)")
    _LEDGER_SCHEMA_READY = True


def _sync_ledger_record(charge_id: str, provider_charge_id: str, user_id: int, chat_id: int,
                        stars_amount: int, currency: str, payload: str) -> bool:
    db_module = _get_db_module()
    with db_module.get_db_connection() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_ledger_schema(conn)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO stars_payment_ledger "
                "(telegram_payment_charge_id, provider_payment_charge_id, user_id, chat_id, "
                "stars_amount, currency, payload, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'paid', ?)",
                (charge_id, provider_charge_id or "", int(user_id), int(chat_id), int(stars_amount),
                 currency or STARS_CURRENCY, (payload or "")[:128], int(time.time()))
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise


def _sync_ledger_mark_refund(charge_id: str, user_id: int, status: str, reason: str) -> None:
    db_module = _get_db_module()
    with db_module.get_db_connection() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_ledger_schema(conn)
            conn.execute(
                "UPDATE stars_payment_ledger SET status = ?, refund_reason = ?, "
                "refunded_at = CASE WHEN ? = 'refunded' THEN ? ELSE refunded_at END "
                "WHERE telegram_payment_charge_id = ? AND user_id = ?",
                (status, (reason or "")[:200], status, int(time.time()), charge_id, int(user_id))
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


async def record_star_payment(message: Message) -> bool:
    """
    Persiste el pago en el libro mayor ANTES de entregar el beneficio: charge_id oficial de
    Telegram, user_id, chat_id de la comunidad e importe en Stars. Idempotente por charge_id.
    """
    sp = message.successful_payment
    try:
        return await asyncio.to_thread(
            _sync_ledger_record,
            sp.telegram_payment_charge_id,
            getattr(sp, "provider_payment_charge_id", "") or "",
            message.from_user.id,
            _payload_chat_id(sp.invoice_payload or ""),
            int(sp.total_amount),
            sp.currency,
            sp.invoice_payload or "",
        )
    except Exception as ex:
        logger.error(f"❌ [Ledger Stars] No se pudo registrar el cargo {sp.telegram_payment_charge_id}: {ex}")
        return False


async def process_star_refund(bot: Bot, user_id: int, charge_id: str, reason: str = "") -> bool:
    """
    Devuelve un pago en Stars (Bot API refundStarPayment) y lo refleja en el libro mayor.
    Idempotente: si Telegram indica que el cargo ya fue reembolsado, se considera éxito.
    Usar ante caídas del servicio, entregas fallidas o cancelaciones legítimas.
    """
    if not charge_id or not user_id:
        return False
    refunded = False
    status = "refund_failed"
    try:
        refunded = bool(await bot.refund_star_payment(user_id=int(user_id), telegram_payment_charge_id=charge_id))
        status = "refunded" if refunded else "refund_failed"
    except TelegramBadRequest as ex:
        if "ALREADY_REFUNDED" in str(ex).upper():
            refunded, status = True, "refunded"
        else:
            logger.error(f"❌ [Reembolso Stars] Rechazado por Telegram ({charge_id}): {ex}")
    except Exception as ex:
        logger.error(f"❌ [Reembolso Stars] Error ejecutando el reembolso de {charge_id}: {ex}")

    try:
        await asyncio.to_thread(_sync_ledger_mark_refund, charge_id, user_id, status, reason)
    except Exception as ex:
        logger.warning(f"⚠️ [Ledger Stars] No se pudo anotar el estado de reembolso de {charge_id}: {ex}")

    if refunded:
        logger.info(f"↩️ [Reembolso Stars] Cargo {charge_id} devuelto a {user_id}. Motivo: {reason or 'no especificado'}")
    return refunded


# ==========================================
# 🌐 DICCIONARIO BILINGÜE DE FACTURACIÓN (SOLO TELEGRAM STARS)
# ==========================================
TEXTS = {
    "en": {
        "owner_only": "⛔ <b>Access Denied:</b> Subscription and billing protocols are exclusive to the Community Owner.\n\n🛡️ <i>Cloud Media Management</i>",
        "active": (
            "✨ <b>Command Center: Subscriptions & Licensing</b>\n\n"
            "• <b>Community ID:</b> <code>{chat_id}</code>\n"
            "• <b>Operational Tier:</b> <code>{tier}</code>\n\n"
            "✅ <i>This ecosystem is operating under an active premium license. All elite modules, anti-spam barriers, and voice controls are fully unlocked.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "free": (
            "🤖 <b>Command Center: Subscriptions & Licensing</b>\n\n"
            "• <b>Community ID:</b> <code>{chat_id}</code>\n"
            "• <b>Operational Tier:</b> <code>BASIC (Free Tier)</code>\n\n"
            "Upgrade your network to remove daily rate limits, activate automated cleansers, or deploy autonomous clone architectures:\n\n"
            "⭐ <i>Payment is 100% in-app with Telegram Stars (XTR). Activation is instant once Telegram confirms the charge.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pro_stars": "⭐ Upgrade to PRO (300 XTR)",
        "btn_ultra_stars": "💎 Upgrade to ULTRA PRO (600 XTR)",
        "btn_miniapp": "🌐 Open Command Center",
        "btn_back": "🔙 Back to Main Menu",
        "btn_back_group": "🔙 Back to Panel",
        "btn_pay_stars": "⭐ Pay with Stars",

        "inv_pro_t": "PRO Subscription (300 XTR)",
        "inv_pro_d": "Unlimited bot commands, automated purge center, custom captcha pro, and master sentinel shielding.",
        "inv_ultra_t": "ULTRA PRO License (600 XTR)",
        "inv_ultra_d": "All PRO features + Bot Clone architecture + Dedicated Voice Sentinel + Weekly VC Scheduler (100% Stars yours).",
        "inv_vip_t": "VIP Mic Pass (24h)",
        "inv_vip_d": "Unrestricted 100% voice transmission privileges for 24 hours in community voice chats.",
        "inv_speaker_t": "Priority Speaker Mic Turn (AMA)",
        "inv_speaker_d": "Priority placement in the voice room speaker queue with uninterrupted mic privilege.",
        "inv_tip_t": "Community Stars Tip (XTR)",
        "inv_tip_d": "Voluntary Telegram Stars donation directly supporting the community and creators.",

        "pmt_ok_pro": (
            "🎉 <b>Payment Confirmed! PRO Plan Active</b>\n\n"
            "• Environment: <code>{chat_id}</code> upgraded to <b>PRO ⭐</b>\n"
            "• All daily command caps and service message purge quotas have been removed.\n\n"
            "💡 <b>Deployment Step:</b> Add our Master Sentinel to your voice chats to manage microphones:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_ok_ultra": (
            "💎 <b>Payment Confirmed! ULTRA PRO License Active</b>\n\n"
            "• Environment: <code>{chat_id}</code> upgraded to <b>ULTRA PRO 💎</b>\n"
            "• Autonomous Bot Clone deployment, isolated Voice Sentinel node, and Weekly VC Cron unlocked.\n"
            "• <b>Direct Monetization:</b> 100% of all Telegram Stars collected enter your bot clone balance!\n\n"
            "💡 <b>Deployment Step:</b> Link your @BotFather token and secondary session string to activate your private node:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_vip_ok": (
            "🎙️ <b>¡MICVIP Pass Activated Successfully!</b>\n\n"
            "• Voice permission unlocked at <b>100% volume</b>.\n"
            "• Validity: <b>24 continuous hours</b>.\n"
            "• The Sentinel will no longer dial your mic down to 2%.\n\n"
            "💡 <b>Instructions:</b> Rejoin the Voice Chat or unmute your mic. You can now speak freely without acoustic attenuation.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_speaker_ok": (
            "🎙️ <b>Speaker Priority Turn Secured!</b>\n\n"
            "• Position in Queue: <b>#{position}</b>\n"
            "• Contribution: <b>{stars} Stars (XTR)</b>\n"
            "• The Sentinel has queued your spot. When the host calls next or triggers <code>/speakers next</code>, your mic will be open.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_tip_ok": (
            "🌟 <b>Tip Received! Thank You!</b>\n\n"
            "• Contribution: <b>{stars} Stars (XTR)</b>\n"
            "• Your support has been registered in the community treasury.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_add_master": "🤖 Add Master Sentinel (@Alphacentinel)",
        "btn_setup_clone": "🧬 Setup Clone & Dedicated Sentinel",
        "btn_join_channel": "🚀 Join Secure Channel",
        "btn_view_target": "🔗 Access VIP Target / Resource",
        "btn_return_vc": "🎙️ Return to Voice Chat",
        "btn_return_group": "👥 Back to Community",
        "err_inv": "⚠️ An error occurred while generating the invoice. Please try again.",
        "err_link": "⚠️ Invalid activation link or expired parameters.",
        "private_only": "⚠️ Please open a private chat with me to access the billing terminal: t.me/{bot_username}"
    },
    "es": {
        "owner_only": "⛔ <b>Access Denied:</b> Las opciones de suscripción y facturación son exclusivas para el Dueño de la comunidad o canal.\n\n🛡️ <i>Cloud Media Management</i>",
        "active": (
            "✨ <b>Centro de Mando: Suscripciones y Licencias</b>\n\n"
            "• <b>Entorno ID:</b> <code>{chat_id}</code>\n"
            "• <b>Nivel Operativo:</b> <code>{tier}</code>\n\n"
            "✅ <i>Este ecosistema opera bajo una licencia premium activa. Todas las barreras de antispam, aduana y control de voz están desbloqueadas.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "free": (
            "🤖 <b>Centro de Mando: Suscripciones y Licencias</b>\n\n"
            "• <b>Entorno ID:</b> <code>{chat_id}</code>\n"
            "• <b>Nivel Operativo:</b> <code>BÁSICO (Plan Gratuito)</code>\n\n"
            "Eleva tu entorno para eliminar topes de comandos diarios, activar purgas automatizadas y desplegar clones autónomos:\n\n"
            "⭐ <i>El pago es 100% in-app con Telegram Stars (XTR). La activación es inmediata cuando Telegram confirma el cobro.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_pro_stars": "⭐ Mejorar a PRO (300 XTR)",
        "btn_ultra_stars": "💎 Mejorar a ULTRA PRO (600 XTR)",
        "btn_miniapp": "🌐 Abrir Command Center",
        "btn_back": "🔙 Volver al Menú Principal",
        "btn_back_group": "🔙 Volver al Panel",
        "btn_pay_stars": "⭐ Pagar con Stars",

        "inv_pro_t": "Suscripción PRO (300 XTR)",
        "inv_pro_d": "Comandos ilimitados, purga de mensajes automatizada, captcha pro y centinela maestro.",
        "inv_ultra_t": "Licencia ULTRA PRO (600 XTR)",
        "inv_ultra_d": "Todo PRO + Arquitectura Bot Clone + Centinela Dedicado Propio + Programador VC Semanal (100% Stars para ti).",
        "inv_vip_t": "Pase VIP Micrófono (24h)",
        "inv_vip_d": "Privilegios de voz continua al 100% de volumen por 24 horas en salas de voz y videochats.",
        "inv_speaker_t": "Turno Prioritario de Micrófono (AMA)",
        "inv_speaker_d": "Prioridad en la cola de oradores del videochat con micrófono abierto según tu turno.",
        "inv_tip_t": "Propina Stars para la Comunidad",
        "inv_tip_d": "Aporte voluntario en Telegram Stars en apoyo directo a la comunidad y a sus creadores.",

        "pmt_ok_pro": (
            "🎉 <b>¡Pago Confirmado! Plan PRO Activado</b>\n\n"
            "• Entorno <code>{chat_id}</code> elevado al estándar <b>PRO ⭐</b> con éxito.\n"
            "• Los topes diarios de 3 comandos y restricciones de purga han sido levantados.\n\n"
            "💡 <b>Paso Siguiente:</b> Añade al Centinela Maestro a tu comunidad para moderar llamadas de voz:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_ok_ultra": (
            "💎 <b>¡Pago Confirmado! Nivel ULTRA PRO Activado</b>\n\n"
            "• Entorno <code>{chat_id}</code> elevado a <b>ULTRA PRO 💎</b>.\n"
            "• Clonación autónoma con @BotFather, Centinela aislado antiban y cronograma semanal desbloqueados.\n"
            "• <b>Monetización Directa:</b> El 100% de las Stars cobradas van directamente y sin comisiones a tu propio bot clon.\n\n"
            "💡 <b>Paso Siguiente:</b> Conecta tu token de bot y tu sesión de Pyrogram en el panel privado:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_vip_ok": (
            "🎙️ <b>¡Pase VIP de Micrófono Activado con Éxito!</b>\n\n"
            "• Tu micrófono ha sido desbloqueado al <b>100% de volumen</b>.\n"
            "• Vigencia: <b>24 horas continuas</b>.\n"
            "• El Centinela ya no atenuará tu audio al 2%.\n\n"
            "💡 <b>Instrucciones:</b> Vuelve al videochat o activa tu micrófono en el grupo. Ya puedes participar y hablar sin restricciones acústicas.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_speaker_ok": (
            "🎙️ <b>¡Turno de Orador Asegurado en la Cola!</b>\n\n"
            "• Posición actual en fila: <b>#{position}</b>\n"
            "• Aporte: <b>{stars} Stars (XTR)</b>\n"
            "• El Centinela ha registrado tu turno. Cuando el anfitrión de la llamada despache <code>/speakers next</code>, se te otorgará el micrófono.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pmt_tip_ok": (
            "🌟 <b>¡Propina Recibida con Éxito!</b>\n\n"
            "• Aporte procesado: <b>{stars} Stars (XTR)</b>\n"
            "• Tu apoyo ha quedado asentado en la tesorería de la comunidad. ¡Muchas gracias!\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_add_master": "🤖 Añadir Centinela Maestro (@Alphacentinel)",
        "btn_setup_clone": "🧬 Configurar Clon & Centinela Propio",
        "btn_join_channel": "🚀 Entrar al Canal Seguro",
        "btn_view_target": "🔗 Ver Destino / Canal VIP",
        "btn_return_vc": "🎙️ Volver al Videochat",
        "btn_return_group": "👥 Volver al Grupo",
        "err_inv": "⚠️ Error al generar la factura. Intenta nuevamente.",
        "err_link": "⚠️ Enlace de facturación no válido, sin entorno asociado o expirado.",
        "private_only": "⚠️️ Inicia un chat privado conmigo para gestionar suscripciones: t.me/{bot_username}"
    }
}


# ==========================================
# 🌟 MENÚ DE SELECCIÓN Y EMISIÓN DE PROPINAS EN STARS
# ==========================================
async def show_tip_selection(message: Message, group_id: int, lang: str = "es"):
    """Despliega los presets que el operador programó (Mini App) y, si lo permite, el monto libre."""
    rules = await get_tip_rules(group_id)
    if not rules["enabled"]:
        await message.answer(_tips_disabled_text(lang), parse_mode="HTML")
        return

    btn_custom_text = "✍️ Donar otro monto / Custom amount" if lang == "es" else "✍️ Custom amount / Other amount"
    presets = rules["presets"]
    rows = [
        [InlineKeyboardButton(text=f"⭐ {amount}", callback_data=f"paytip_{group_id}_{amount}") for amount in presets[i:i + 3]]
        for i in range(0, len(presets), 3)
    ]
    if rules["custom_allowed"]:
        rows.append([InlineKeyboardButton(text=btn_custom_text, callback_data=f"paytip_custom_{group_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    if rules["custom_allowed"]:
        text = (
            "⭐ <b>Aporte Voluntario a la Comunidad</b>\n\n"
            "Selecciona uno de los montos predeterminados o pulsa <b>«Donar otro monto»</b> "
            "para ingresar la cantidad exacta de Telegram Stars que deseas enviar.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            "⭐ <b>Voluntary Community Tip</b>\n\n"
            "Select one of the preset amounts below or tap <b>«Custom amount»</b> "
            "to specify the exact amount of Telegram Stars you wish to contribute.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
    else:
        text = (
            "⭐ <b>Aporte Voluntario a la Comunidad</b>\n\n"
            "Selecciona uno de los montos disponibles para enviar tu aporte en Telegram Stars.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            "⭐ <b>Voluntary Community Tip</b>\n\n"
            "Select one of the available amounts to send your tip in Telegram Stars.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def send_stars_tip_invoice(bot: Bot, user_id: int, group_id: int, amount: int, lang: str = "es"):
    """Emite la factura oficial de Telegram Stars con la cantidad dinámica solicitada."""
    t = TEXTS.get(lang, TEXTS["es"])
    title = f"{t['inv_tip_t']} ({amount} ⭐)"[:32]
    description = f"{t['inv_tip_d']} ({amount} XTR)"[:255]
    payload = f"tip_{group_id}_{amount}"

    kb_rows = [
        [InlineKeyboardButton(text=f"⭐ Donar {amount} Stars" if lang == "es" else f"⭐ Donate {amount} Stars", pay=True)]
    ]
    try:
        chat_info = await bot.get_chat(group_id)
        if chat_info and getattr(chat_info, "username", None):
            kb_rows.append([InlineKeyboardButton(text=t["btn_return_group"], url=f"https://t.me/{chat_info.username}")])
    except Exception:
        pass
    kb_rows.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])

    await send_stars_invoice(
        bot, user_id, title, description, payload, amount,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


# ==========================================
# 🚀 COMANDOS DE ACCESO A PLANES EN GRUPOS (EXCLUSIVO DUEÑO)
# ==========================================
@router.message(Command("pro", "ultra"))
async def cmd_pro_ultra(message: Message, command: CommandObject, bot: Bot):
    if not message.from_user or (message.sender_chat and message.chat.type != "private"):
        # Administrador anónimo o publicación como canal: la factura necesita un DM real.
        return
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]

    if message.chat.type == "private":
        await message.answer(
            f"⚠️ Por favor ejecuta /{command.command} dentro de tu comunidad para vincular la facturación a ese entorno.",
            parse_mode="HTML"
        )
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_user_creator(bot, chat_id, user_id):
        try:
            warn = await message.reply(t["owner_only"], parse_mode="HTML")
            _spawn(auto_delete_pair(message, warn, delay=8))
        except Exception:
            pass
        return

    try:
        await message.delete()
    except Exception:
        pass

    current_tier = await get_group_tier(chat_id)

    if current_tier in ["pro", "ultra_pro"]:
        tier_display = "PRO ⭐" if current_tier == "pro" else "ULTRA PRO 💎"
        private_text = t["active"].format(chat_id=chat_id, tier=tier_display)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
        ])
    else:
        private_text = t["free"].format(chat_id=chat_id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_pro_stars"], callback_data=f"inv_pro_{chat_id}_{lang}"),
                InlineKeyboardButton(text=t["btn_ultra_stars"], callback_data=f"inv_ultra_{chat_id}_{lang}")
            ],
            [InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")]
        ])

    try:
        await bot.send_message(chat_id=user_id, text=private_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        bot_info = await bot.get_me()
        temp_msg = await message.answer(t["private_only"].format(bot_username=bot_info.username), parse_mode="HTML")
        _spawn(auto_delete_pair(message, temp_msg, delay=12))


# ==========================================
# 🔗 ENRUTAMIENTO UNIFICADO DE DEEP LINKS (/start sub_, vipmic_, chanplan_, speaker_, tip_)
# ==========================================
@router.message(Command("start"), F.text.regexp(r"^/start\s+(sub_|vipmic_|chanplan_|speaker_|tip_)"))
async def cmd_start_deep_linking(message: Message, command: CommandObject, bot: Bot):
    if message.chat.type != "private":
        return

    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    args = command.args or ""

    # 1. SUSCRIPCIONES DE COMUNIDAD
    if args.startswith("sub_"):
        if is_clone_bot(bot) and (args.startswith("sub_pro") or args.startswith("sub_ultra")):
            redirect_plan = "pro" if args.startswith("sub_pro") else "ultra"
            redirect_chat_id = 0
            redirect_parts = args.split("_")
            if len(redirect_parts) > 2:
                try:
                    redirect_chat_id = int(redirect_parts[2])
                except ValueError:
                    redirect_chat_id = 0
            redirect_text, redirect_markup = _clone_subscription_redirect(lang, redirect_plan, redirect_chat_id)
            if redirect_text:
                try:
                    await message.answer(redirect_text, reply_markup=redirect_markup, parse_mode="HTML")
                except Exception:
                    pass
                return

        try:
            chat_id_target = 0
            if args.startswith("sub_pro"):
                parts = args.split("_")
                if len(parts) > 2:
                    chat_id_target = int(parts[2])
                price = PRICE_PRO_STARS
                title = t["inv_pro_t"]
                desc = t["inv_pro_d"]
                payload = f"sub_pro_{chat_id_target}"
            elif args.startswith("sub_ultra"):
                parts = args.split("_")
                if len(parts) > 2:
                    chat_id_target = int(parts[2])
                price = PRICE_ULTRAPRO_STARS
                title = t["inv_ultra_t"]
                desc = t["inv_ultra_d"]
                payload = f"sub_ultra_{chat_id_target}"
            else:
                return

            if chat_id_target >= 0:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            _, back_cb = await resolve_chat_context(bot, chat_id_target)
            back_btn = InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"{back_cb}_{lang}")

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({price} XTR)", pay=True)],
                [back_btn]
            ])

            await send_stars_invoice(bot, message.chat.id, title, desc, payload, price, reply_markup=markup)
        except Exception as e:
            logger.error(f"Error generando factura de suscripción en start: {e}")
            await message.answer(t["err_inv"], parse_mode="HTML")
        return

    # 2. MEMBRESÍAS DE CANAL
    elif args.startswith("chanplan_"):
        try:
            parts = args.split("_")
            if len(parts) < 3:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            plan_id = int(parts[1])
            channel_id = int(parts[2])

            token = _plan_cache_token(plan_id)
            target_plan = await get_channel_plan(plan_id)
            _cache_channel_plan(plan_id, target_plan, token)
            if not target_plan or target_plan.get("channel_id") != channel_id or target_plan.get("status") != "active":
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            plan_name = target_plan["plan_name"]
            duration_days = target_plan["duration_days"]
            stars_price = target_plan["stars_price"]
            promo_text = target_plan.get("promo_text") or ""
            media_id = target_plan.get("media_id")
            media_type = target_plan.get("media_type")
            target_link = target_plan.get("target_link")

            # Mismo normalizador que la vista previa de la Mini App: el HTML del operador siempre es válido.
            caption = (normalize_telegram_html(promo_text.strip()) if promo_text and promo_text.strip()
                       else f"💎 <b>{html.escape(str(plan_name))}</b>\n\n{duration_days} días — {stars_price} ⭐")

            if media_id and media_type:
                try:
                    if media_type == "photo":
                        await bot.send_photo(chat_id=message.chat.id, photo=media_id, caption=caption, parse_mode="HTML")
                    elif media_type == "video":
                        await bot.send_video(chat_id=message.chat.id, video=media_id, caption=caption, parse_mode="HTML")
                    elif media_type == "animation":
                        await bot.send_animation(chat_id=message.chat.id, animation=media_id, caption=caption, parse_mode="HTML")
                except Exception as promo_err:
                    logger.warning(f"Aviso despachando multimedia en chanplan: {promo_err}")
            elif caption:
                try:
                    await message.answer(caption, parse_mode="HTML")
                except Exception:
                    pass

            title = f"Membresía: {plan_name}"[:32]
            # La descripción de una factura es texto plano: sin etiquetas ni entidades cortadas a la mitad.
            plain_promo = telegram_html_to_plain(promo_text).strip() if promo_text else ""
            desc = (plain_promo or f"Acceso exclusivo al canal por {duration_days} días.")[:255]
            payload = f"chan_sub_{channel_id}_{plan_id}_{duration_days}"

            kb_rows = [
                [InlineKeyboardButton(text=f"⭐ Pagar {stars_price} XTR", pay=True)]
            ]
            if target_link:
                target_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
                kb_rows.append([InlineKeyboardButton(text=t["btn_view_target"], url=target_url)])

            kb_rows.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])
            markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            await send_stars_invoice(bot, message.chat.id, title, desc, payload, int(stars_price), reply_markup=markup)
        except Exception as e:
            logger.error(f"Error generando factura de membresía de canal: {e}")
            await message.answer(t["err_inv"], parse_mode="HTML")
        return

    # 3. PASES VIP DE MICRÓFONO PERSONALIZADOS
    elif args.startswith("vipmic_"):
        try:
            chat_id = int(args.split("_")[1])
            if chat_id >= 0:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            custom_cfg = await get_mic_vip_custom_config(chat_id)
            final_price = custom_cfg.get("price") or DEFAULT_PRICE_VIP_MIC
            tag = custom_cfg.get("tag") or "Pase VIP 24h"
            custom_desc = custom_cfg.get("text")

            title = f"MicVIP: {tag}"[:32]
            desc = custom_desc[:255] if custom_desc else t["inv_vip_d"]
            payload = f"vip_mic_{chat_id}"

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({final_price} XTR)", pay=True)],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])

            await send_stars_invoice(bot, message.chat.id, title, desc, payload, int(final_price), reply_markup=markup)
        except Exception as e:
            logger.error(f"Error generando factura de Micrófono VIP: {e}")
            await message.answer(t["err_link"], parse_mode="HTML")
        return

    # 4. COLA DE SPEAKERS AMA (PRIORIDAD DE MICRÓFONO EN VIDEOCHAT)
    elif args.startswith("speaker_"):
        try:
            chat_id = int(args.split("_")[1])
            if chat_id >= 0:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            speaker_price = await get_speaker_price(chat_id) or DEFAULT_PRICE_SPEAKER
            title = t["inv_speaker_t"][:32]
            desc = t["inv_speaker_d"]
            payload = f"speaker_{chat_id}"

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({speaker_price} XTR)", pay=True)],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])

            await send_stars_invoice(bot, message.chat.id, title, desc, payload, int(speaker_price), reply_markup=markup)
        except Exception as e:
            logger.error(f"Error generando factura de Speaker Priority: {e}")
            await message.answer(t["err_link"], parse_mode="HTML")
        return

    # 5. MOTOR DE PROPINAS STARS (TIPS ENGINE CON SELECTOR INTERACTIVO)
    elif args.startswith("tip_"):
        try:
            parts = args.split("_")
            chat_id = int(parts[1])
            tip_amount = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

            if chat_id >= 0:
                await message.answer(t["err_link"], parse_mode="HTML")
                return

            # Si el enlace no trae monto fijo (ej: /start tip_<chat_id>), muestra presets + monto libre
            if tip_amount <= 0:
                await show_tip_selection(message, chat_id, lang)
                return

            # Si ya trae monto fijo definido, genera la factura directa (respetando lo programado en la Mini App)
            rules = await get_tip_rules(chat_id)
            if not rules["enabled"]:
                await message.answer(_tips_disabled_text(lang), parse_mode="HTML")
                return
            if tip_amount > MAX_TIP_STARS or (tip_amount not in rules["presets"] and not rules["custom_allowed"]):
                await show_tip_selection(message, chat_id, lang)
                return
            await send_stars_tip_invoice(bot, message.chat.id, chat_id, tip_amount, lang)
        except Exception as e:
            logger.error(f"Error generando factura de propina: {e}")
            await message.answer(t["err_link"], parse_mode="HTML")
        return


# ==========================================
# ⚡ DESPACHO DE FACTURAS DESDE BOTONES INLINE (inv_)
# ==========================================
@router.callback_query(F.data.startswith("inv_"))
async def process_invoice_callback(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    lang = get_lang(callback.from_user.language_code)
    t = TEXTS[lang]
    parts = callback.data.split("_")

    if len(parts) >= 3:
        plan = parts[1]
        chat_id = int(parts[2])

        if chat_id >= 0:
            await callback.message.answer(t["err_link"], parse_mode="HTML")
            return

        if is_clone_bot(bot):
            redirect_text, redirect_markup = _clone_subscription_redirect(lang, plan, chat_id)
            if redirect_text:
                try:
                    await callback.message.answer(redirect_text, reply_markup=redirect_markup, parse_mode="HTML")
                except Exception:
                    pass
                return

        if plan == "pro":
            price = PRICE_PRO_STARS
            title = t["inv_pro_t"]
            desc = t["inv_pro_d"]
            payload = f"sub_pro_{chat_id}"
        elif plan == "ultra":
            price = PRICE_ULTRAPRO_STARS
            title = t["inv_ultra_t"]
            desc = t["inv_ultra_d"]
            payload = f"sub_ultra_{chat_id}"
        elif plan == "speaker":
            price = await get_speaker_price(chat_id) or DEFAULT_PRICE_SPEAKER
            title = t["inv_speaker_t"]
            desc = t["inv_speaker_d"]
            payload = f"speaker_{chat_id}"
        else:
            return

        _, back_cb = await resolve_chat_context(bot, chat_id)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{t['btn_pay_stars']} ({price} XTR)", pay=True)],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"{back_cb}_{lang}")]
        ])

        try:
            await send_stars_invoice(bot, callback.message.chat.id, title, desc, payload, int(price), reply_markup=markup)
        except Exception as e:
            logger.error(f"Error despachando factura mediante callback: {e}")
            await callback.message.answer(t["err_inv"], parse_mode="HTML")


# ==========================================
# 🌟 BOTONES DE SELECCIÓN Y CAPTURA MANUAL DE PROPINAS
# ==========================================
@router.callback_query(F.data.startswith("paytip_"))
async def handle_tip_selection(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    lang = get_lang(callback.from_user.language_code)

    # Caso 1: El usuario pulsa "Donar otro monto"
    if len(parts) >= 3 and parts[1] == "custom":
        try:
            group_id = int(parts[2])
        except ValueError:
            await callback.answer()
            return
        rules = await get_tip_rules(group_id)
        if not rules["enabled"] or not rules["custom_allowed"]:
            await callback.answer(
                _tips_disabled_text(lang) if not rules["enabled"] else
                ("El monto libre está desactivado en esta comunidad." if lang == "es" else "Custom amounts are disabled here."),
                show_alert=True,
            )
            return
        _open_custom_tip(bot.id, callback.from_user.id, group_id)

        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancelar / Cancel", callback_data="cancel_custom_tip")]
        ])
        prompt_text = (
            "✍️ <b>Ingresa tu monto personalizado:</b>\n\n"
            "Escribe en este chat el número exacto de Telegram Stars que deseas donar (ejemplo: <code>75</code>, <code>300</code>, <code>1500</code>):\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            "✍️ <b>Enter your custom amount:</b>\n\n"
            "Send the exact number of Telegram Stars you wish to donate (e.g. <code>75</code>, <code>300</code>, <code>1500</code>):\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
        await callback.message.answer(prompt_text, reply_markup=cancel_kb, parse_mode="HTML")
        await callback.answer()
        return

    # Caso 2: El usuario selecciona un preset (ej. 15, 50, 100, 250, 500)
    if len(parts) >= 3:
        try:
            group_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            await callback.answer()
            return
        if group_id >= 0 or not (1 <= amount <= MAX_TIP_STARS):
            await callback.answer()
            return
        rules = await get_tip_rules(group_id)
        if not rules["enabled"] or (amount not in rules["presets"] and not rules["custom_allowed"]):
            # Botón de un menú antiguo (presets cambiados desde la Mini App) o callback manipulado.
            await callback.answer(
                _tips_disabled_text(lang) if not rules["enabled"] else
                ("Ese monto ya no está disponible. Abre de nuevo el menú de propinas." if lang == "es"
                 else "That amount is no longer available. Open the tip menu again."),
                show_alert=True,
            )
            return
        await callback.answer()
        try:
            await send_stars_tip_invoice(bot, callback.from_user.id, group_id, amount, lang)
        except Exception as ex:
            logger.error(f"Error emitiendo factura de propina preset: {ex}")
            try:
                await callback.message.answer(TEXTS[lang]["err_inv"], parse_mode="HTML")
            except Exception:
                pass


@router.callback_query(F.data == "cancel_custom_tip")
async def cancel_custom_tip_callback(callback: CallbackQuery, bot: Bot):
    """Cancela la solicitud de monto manual y libera el estado en memoria."""
    _close_custom_tip(bot.id, callback.from_user.id)
    lang = get_lang(callback.from_user.language_code)
    await callback.answer("Donación cancelada." if lang == "es" else "Donation cancelled.")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), _awaiting_custom_tip)
async def process_custom_tip_input(message: Message, bot: Bot):
    """Captura el número de Stars ingresado por el usuario y genera la factura."""
    group_id = _close_custom_tip(bot.id, message.from_user.id)
    if group_id is None:
        return
    lang = get_lang(message.from_user.language_code)
    text_val = (message.text or "").strip().replace(".", "").replace(",", "")

    if not text_val.isdigit() or not (1 <= int(text_val) <= MAX_TIP_STARS):
        _open_custom_tip(bot.id, message.from_user.id, group_id)  # Mantiene la espera activa si se equivoca
        err_msg = (
            f"⚠️ Ingresa un número entero entre 1 y {MAX_TIP_STARS} ⭐."
            if lang == "es" else
            f"⚠️ Please enter a whole number of Stars between 1 and {MAX_TIP_STARS} ⭐."
        )
        await message.answer(err_msg)
        return

    amount = int(text_val)
    rules = await get_tip_rules(group_id)
    if not rules["enabled"] or not rules["custom_allowed"]:
        # El operador desactivó el monto libre mientras el usuario escribía.
        await message.answer(_tips_disabled_text(lang) if not rules["enabled"] else
                             ("El monto libre está desactivado en esta comunidad." if lang == "es"
                              else "Custom amounts are disabled here."))
        return
    try:
        await send_stars_tip_invoice(bot, message.chat.id, group_id, amount, lang)
    except Exception as ex:
        logger.error(f"Error emitiendo factura de propina personalizada: {ex}")
        await message.answer(TEXTS[lang]["err_inv"], parse_mode="HTML")


# ==========================================
# 🛡️ VALIDACIÓN DE PRE-CHECKOUT FILTRADA
# ==========================================
PRECHECKOUT_DECLINE_MESSAGE = (
    "La oferta cambió o ya no está disponible. Solicita una nueva factura. / "
    "This offer changed; please request a new invoice."
)


async def _validate_channel_offer(payload: str, channel_id: int, plan_id: int, amount: int) -> bool:
    """
    Orden de validación sin bloquear el SLA:
      1. Caché de planes fresca (≤ CHANNEL_PLAN_CACHE_TTL = 10 s) → decisión en RAM.
      2. Oferta emitida por este proceso → aprobada en RAM; la caché se refresca en segundo plano.
      3. Oferta desconocida (reinicio) → lectura de disco acotada a 1,2 s; si se agota el
         presupuesto se aprueba, porque la factura la emitió el propio bot y el importe es
         inalterable por el usuario (la entrega reembolsa si el plan ya no existe).
    """
    hit, plan = _cached_channel_plan(plan_id)
    if hit:
        return bool(
            plan and plan.get("channel_id") == channel_id and plan.get("status") == "active"
            and amount == int(plan.get("stars_price") or 0)
        )
    known = _offer_matches(payload, amount)
    if known is not None:
        _spawn(_refresh_channel_plan_cache(plan_id))
        return known
    token = _plan_cache_token(plan_id)
    try:
        plan = await asyncio.wait_for(get_channel_plan(plan_id), timeout=PRECHECKOUT_DB_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ [Pre-Checkout] Presupuesto de disco agotado para {payload!r}; se aprueba la oferta emitida por el bot.")
        _spawn(_refresh_channel_plan_cache(plan_id))
        return True
    _cache_channel_plan(plan_id, plan, token)
    return bool(
        plan and plan.get("channel_id") == channel_id and plan.get("status") == "active"
        and amount == int(plan.get("stars_price") or 0)
    )


def _validate_offer_in_memory(payload: str, amount: int):
    """
    Validación pura en CPU para todas las familias excepto chan_sub_ (que puede requerir el plan).
    Devuelve True/False o None si la familia necesita _validate_channel_offer.
    """
    if payload.startswith("sub_"):
        parts = payload.split("_")
        expected = {"pro": PRICE_PRO_STARS, "ultra": PRICE_ULTRAPRO_STARS}.get(parts[1])
        return expected is not None and int(parts[2]) < 0 and amount == expected
    if payload.startswith("chan_sub_"):
        return None
    if payload.startswith("vip_mic_"):
        chat_ok = int(payload.split("_")[2]) < 0
    elif payload.startswith("speaker_"):
        chat_ok = int(payload.split("_")[1]) < 0
    elif payload.startswith("tip_"):
        chat_ok = int(payload.split("_")[1]) < 0 and amount <= MAX_TIP_STARS
    else:
        return False
    if not chat_ok:
        return False
    # Si la oferta consta en memoria, el importe debe coincidir exactamente con el emitido.
    known = _offer_matches(payload, amount)
    return True if known is None else known


@router.pre_checkout_query(F.invoice_payload.regexp(r"^(sub_|chan_sub_|vip_mic_|speaker_|tip_)"))
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """
    SLA < 2 s (Telegram corta a los 10 s y el usuario ve un error de pago).
    La validación ocurre en memoria: moneda XTR, comunidad válida, precio vigente de licencias
    y coincidencia con la oferta emitida. Solo un chan_sub_ desconocido tras un reinicio toca
    disco, con un presupuesto máximo de 1,2 s.
    """
    started = time.monotonic()
    payload = pre_checkout_query.invoice_payload or ""
    amount = int(pre_checkout_query.total_amount or 0)
    ok = False
    try:
        if pre_checkout_query.currency != STARS_CURRENCY or amount < 1:
            ok = False
        else:
            decision = _validate_offer_in_memory(payload, amount)
            if decision is None:
                parts = payload.split("_")
                decision = await _validate_channel_offer(payload, int(parts[2]), int(parts[3]), amount)
            ok = bool(decision)
    except Exception as ex:
        logger.warning(f"⚠️ [Pre-Checkout] Payload no válido {payload!r}: {ex}")
        ok = False

    try:
        if ok:
            await pre_checkout_query.answer(ok=True)
        else:
            await pre_checkout_query.answer(ok=False, error_message=PRECHECKOUT_DECLINE_MESSAGE)
    finally:
        elapsed = time.monotonic() - started
        if elapsed > PRECHECKOUT_SLA_SECONDS:
            logger.warning(f"🐢 [Pre-Checkout] SLA excedido: {elapsed:.2f}s para {payload!r} (ok={ok}).")
        else:
            logger.debug(f"⚡ [Pre-Checkout] {payload!r} respondido en {elapsed * 1000:.0f} ms (ok={ok}).")


# ==========================================
# 💎 PROCESADOR DE PAGO EXITOSO CON CONTROL DE IDEMPOTENCIA
# ==========================================
@router.message(F.successful_payment, F.successful_payment.invoice_payload.regexp(r"^(sub_|chan_sub_|vip_mic_|speaker_|tip_)"))
async def process_successful_payment(message: Message, bot: Bot):
    lang = get_lang(message.from_user.language_code)
    t = TEXTS[lang]
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id
    charge_id = message.successful_payment.telegram_payment_charge_id

    # 🛡️ Blindaje de Idempotencia: evita pagos duplicados ante reintentos de red o reinicios
    if not await mark_payment_processed(charge_id, user_id, payload):
        logger.warning(f"⚠️ [Pago Duplicado Ignorado] charge_id={charge_id} usuario={user_id}. Beneficio ya concedido.")
        return

    # 📒 Trazabilidad: el cargo queda en el libro mayor ANTES de entregar el beneficio.
    await record_star_payment(message)
    logger.info(
        f"💳 [Pago Stars] charge_id={charge_id} user_id={user_id} "
        f"chat_id={_payload_chat_id(payload)} stars={message.successful_payment.total_amount} payload={payload!r}"
    )

    # CASO 1: SUSCRIPCIONES PRO / ULTRA PRO (GRUPOS Y CANALES)
    if payload.startswith("sub_"):
        license_granted = False
        try:
            parts = payload.split("_")
            plan_type = parts[1]
            chat_id = int(parts[2])

            tier_db = "pro" if plan_type == "pro" else "ultra_pro"
            async with license_lock(chat_id):
                granted_tier, granted_days = await compute_license_grant(chat_id, tier_db, base_days=30)
                await approve_group(group_id=chat_id, tier=granted_tier, duration_days=granted_days)
            license_granted = True
            logger.info(f"⭐ [Licencia] {chat_id} → {granted_tier} por {granted_days} días (compra: {tier_db}).")
            if granted_tier != tier_db:
                plan_type = "ultra"

            chat_kind, back_cb = await resolve_chat_context(bot, chat_id)

            if plan_type == "pro":
                confirm_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t["btn_add_master"], url=ASSISTANT_INVITE_URL)],
                    [InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"{back_cb}_{lang}")]
                ])
                await message.answer(
                    t["pmt_ok_pro"].format(chat_id=chat_id),
                    parse_mode="HTML",
                    reply_markup=confirm_markup
                )
            else:
                clone_cb = f"gset_clone_{chat_id}_{lang}"
                confirm_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t["btn_setup_clone"], callback_data=clone_cb)],
                    [InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"{back_cb}_{lang}")]
                ])
                await message.answer(
                    t["pmt_ok_ultra"].format(chat_id=chat_id),
                    parse_mode="HTML",
                    reply_markup=confirm_markup
                )
        except Exception as e:
            if license_granted:
                # La licencia YA está activa: solo falló la confirmación. Reembolsar aquí dejaría
                # al cliente con el servicio y su dinero, o le revocaría un plan pagado.
                logger.error(f"Licencia {payload!r} concedida pero falló la confirmación al usuario {user_id}: {e}")
            else:
                logger.error(f"Error procesando la entrega de suscripción adquirida (Iniciando reembolso automático): {e}")
                await process_star_refund(bot, user_id, charge_id, reason=f"Entrega de licencia fallida: {e}")
                with contextlib.suppress(Exception):
                    await message.answer(t["err_inv"], parse_mode="HTML")

    # CASO 2: MEMBRESÍAS DE CANAL Y ENTREGA EXCLUSIVA POR BOTONES
    elif payload.startswith("chan_sub_"):
        membership_delivered = False
        try:
            parts = payload.split("_")
            channel_id = int(parts[2])
            plan_id = int(parts[3])
            duration_days = int(parts[4])
            stars_paid = message.successful_payment.total_amount

            target_plan = await get_channel_plan(plan_id)
            if not target_plan or target_plan.get("channel_id") != channel_id:
                raise ValueError(f"El plan {plan_id} ya no existe para el canal {channel_id}")
            if target_plan.get("status") != "active":
                # Solo ocurre si el pre-checkout aprobó por presupuesto de disco agotado justo tras una pausa.
                raise ValueError(f"El plan {plan_id} está pausado: no se entrega y se reembolsa")
            target_link = target_plan.get("target_link") if target_plan else None
            ch_settings = await get_channel_settings(channel_id)
            custom_welcome = ch_settings.get("custom_welcome") if ch_settings else ""

            invite = await bot.create_chat_invite_link(
                chat_id=channel_id,
                member_limit=1,
                expire_date=int(time.time()) + 86400 * 3
            )
            invite_link = invite.invite_link

            await record_channel_subscription(
                channel_id=channel_id,
                user_id=user_id,
                plan_id=plan_id,
                stars_paid=stars_paid,
                duration_days=duration_days,
                invite_link=invite_link
            )
            # Desde aquí la membresía EXISTE (registro + enlace): un fallo al notificar no debe
            # reembolsar, o el usuario quedaría con acceso y con su dinero.
            membership_delivered = True

            kb_rows = [
                [InlineKeyboardButton(text=t["btn_join_channel"], url=invite_link)]
            ]

            if target_link:
                target_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
                kb_rows.append([InlineKeyboardButton(text=t["btn_view_target"], url=target_url)])

            join_markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
            welcome_extra = f"\n\n💬 <i>{normalize_telegram_html(custom_welcome)}</i>" if custom_welcome else ""

            success_text = (
                f"💎 <b>¡Membresía de Canal Activada con Éxito!</b>\n\n"
                f"• Pago procesado: <b>{stars_paid} Stars (XTR)</b>\n"
                f"• Período de vigencia: <b>{duration_days} días</b>\n"
                f"• Tu <b>enlace criptográfico de un solo uso</b> está listo (se quemará automáticamente al unirte):\n\n"
                f"Usa los botones interactivos abajo para ingresar:{welcome_extra}\n\n"
                f"🛡️️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                f"💎 <b>Channel Membership Activated Successfully!</b>\n\n"
                f"• Payment processed: <b>{stars_paid} Stars (XTR)</b>\n"
                f"• Validity period: <b>{duration_days} days</b>\n"
                f"• Your <b>single-use cryptographic invite link</b> is ready (burns automatically upon joining):\n\n"
                f"Use the interactive buttons below to join:{welcome_extra}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            try:
                await message.answer(success_text, reply_markup=join_markup, parse_mode="HTML")
            except TelegramBadRequest:
                await message.answer(telegram_html_to_plain(success_text), reply_markup=join_markup, parse_mode=None)
            logger.info(f"✅ [Membresía Activada]: Usuario {user_id} en canal {channel_id} por {duration_days} días ({stars_paid} Stars).")
        except Exception as e:
            if membership_delivered:
                logger.error(f"Membresía {payload!r} registrada pero no se pudo notificar a {user_id}: {e}")
            else:
                logger.error(f"Error procesando el pago de membresía para canal (Iniciando reembolso automático): {e}")
                await process_star_refund(bot, user_id, charge_id, reason=f"Entrega de membresía fallida: {e}")
                with contextlib.suppress(Exception):
                    await message.answer(t["err_inv"], parse_mode="HTML")

    # CASO 3: PASES VIP DE MICRÓFONO PERSONALIZADOS (24 HORAS)
    elif payload.startswith("vip_mic_"):
        benefit_granted = False
        try:
            chat_id = int(payload.split("_")[2])

            await grant_vip_mic(user_id=user_id, group_id=chat_id)
            benefit_granted = True

            try:
                await set_participant_mic(
                    chat_id=chat_id,
                    user_id=user_id,
                    muted=False,
                    volume=10000
                )
            except Exception as radar_err:
                logger.warning(f"Aviso Centinela al restaurar volumen de pase VIP: {radar_err}")

            try:
                custom_cfg = await get_mic_vip_custom_config(chat_id)
                badge_title = custom_cfg.get("tag") or await get_vip_badge_title(chat_id)

                # Un administrador real NO se re-promueve: promote_chat_member con todos los
                # permisos en False le quitaría sus facultades reales de moderación.
                current_status = None
                try:
                    current_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                    current_status = current_member.status
                except Exception:
                    pass

                if current_status not in ("creator", "administrator"):
                    await bot.promote_chat_member(
                        chat_id=chat_id, user_id=user_id,
                        can_manage_chat=True, can_change_info=False, can_delete_messages=False,
                        can_invite_users=False, can_restrict_members=False, can_pin_messages=False,
                        can_promote_members=False, can_manage_video_chats=False
                    )
                    await bot.set_chat_administrator_custom_title(
                        chat_id=chat_id, user_id=user_id, custom_title=badge_title[:16]
                    )
                    try:
                        await asyncio.to_thread(_sync_record_vip_badge, chat_id, user_id)
                    except Exception as rec_err:
                        logger.warning(f"Aviso registrando insignia VIP temporal ({chat_id}/{user_id}): {rec_err}")
            except TelegramBadRequest as admin_err:
                logger.warning(f"Aviso al asignar título VIP: {admin_err}")
            except Exception as admin_err:
                logger.warning(f"Error general al asignar título VIP de micrófono: {admin_err}")

            kb_rows = []
            try:
                chat_info = await bot.get_chat(chat_id)
                if chat_info and getattr(chat_info, "username", None):
                    kb_rows.append([InlineKeyboardButton(text=t["btn_return_vc"], url=f"https://t.me/{chat_info.username}")])
            except Exception:
                pass

            kb_rows.append([InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))])
            kb_rows.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])

            markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
            await message.answer(t["pmt_vip_ok"], parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            if benefit_granted:
                logger.error(f"Pase VIP {payload!r} concedido; falló un paso posterior no crítico para {user_id}: {e}")
                return
            logger.error(f"Error procesando la entrega del pase VIP de micrófono (Iniciando reembolso automático): {e}")
            await process_star_refund(bot, user_id, charge_id, reason=f"Entrega de pase VIP fallida: {e}")
            with contextlib.suppress(Exception):
                await message.answer(t["err_inv"], parse_mode="HTML")

    # CASO 4: COLA DE SPEAKERS AMA
    elif payload.startswith("speaker_"):
        benefit_granted = False
        try:
            chat_id = int(payload.split("_")[1])
            stars_paid = message.successful_payment.total_amount
            full_name = message.from_user.full_name or "Usuario"
            username = message.from_user.username or ""

            await add_to_speaker_queue(chat_id, user_id, full_name, username, stars_paid)
            benefit_granted = True
            position = await get_user_speaker_position(chat_id, user_id)

            kb_rows = []
            try:
                chat_info = await bot.get_chat(chat_id)
                if chat_info and getattr(chat_info, "username", None):
                    kb_rows.append([InlineKeyboardButton(text=t["btn_return_vc"], url=f"https://t.me/{chat_info.username}")])
            except Exception:
                pass

            kb_rows.append([InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))])
            markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            confirm_text = t["pmt_speaker_ok"].format(position=position, stars=stars_paid)
            await message.answer(confirm_text, parse_mode="HTML", reply_markup=markup)
            logger.info(f"🎙️ [Speaker Encolado]: Usuario {user_id} en posición #{position} para grupo {chat_id} ({stars_paid} Stars).")
        except Exception as e:
            if benefit_granted:
                logger.error(f"Turno de speaker {payload!r} registrado; falló un paso posterior no crítico para {user_id}: {e}")
                return
            logger.error(f"Error procesando turno de speaker (Iniciando reembolso automático): {e}")
            await process_star_refund(bot, user_id, charge_id, reason=f"Entrega de speaker fallida: {e}")
            with contextlib.suppress(Exception):
                await message.answer(t["err_inv"], parse_mode="HTML")

    # CASO 5: PROPINAS STARS (TIPS ENGINE)
    elif payload.startswith("tip_"):
        benefit_granted = False
        try:
            parts = payload.split("_")
            chat_id = int(parts[1])
            stars_paid = message.successful_payment.total_amount

            await record_group_tip(chat_id, user_id, stars_paid)
            benefit_granted = True

            # Despacho de agradecimiento si existe la función en user_private
            try:
                package = __package__ or __name__.rpartition(".")[0]
                up_mod = (
                    importlib.import_module(".user_private", package=package)
                    if package else importlib.import_module("handlers.user_private")
                )
                send_thanks_fn = getattr(up_mod, "send_tip_thanks", None)
                if send_thanks_fn:
                    await send_thanks_fn(
                        bot=bot,
                        user_id=user_id,
                        stars=stars_paid,
                        group_id=chat_id,
                        lang=lang
                    )
            except Exception as thanks_err:
                logger.debug(f"Aviso en agradecimiento de propina: {thanks_err}")

            kb_rows = []
            try:
                chat_info = await bot.get_chat(chat_id)
                if chat_info and getattr(chat_info, "username", None):
                    kb_rows.append([InlineKeyboardButton(text=t["btn_return_group"], url=f"https://t.me/{chat_info.username}")])
            except Exception:
                pass

            kb_rows.append([InlineKeyboardButton(text=t["btn_miniapp"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat_id}"))],)
            markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            confirm_text = t["pmt_tip_ok"].format(stars=stars_paid)
            await message.answer(confirm_text, parse_mode="HTML", reply_markup=markup)

            # Anuncio opcional en la comunidad
            if chat_id:
                try:
                    buyer_name = html.escape(message.from_user.full_name or "Usuario")
                    public_notice = (
                        f"⭐ <a href='tg://user?id={user_id}'>{buyer_name}</a> acaba de enviar una propina "
                        f"de <b>{stars_paid} Stars (XTR)</b>. ¡Gracias por respaldar el proyecto!\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    ) if lang == "es" else (
                        f"⭐ <a href='tg://user?id={user_id}'>{buyer_name}</a> just sent a tip "
                        f"of <b>{stars_paid} Stars (XTR)</b>. Thank you for supporting the community!\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                    pub_msg = await bot.send_message(chat_id=chat_id, text=public_notice, parse_mode="HTML")
                    _spawn(auto_delete_pair(pub_msg, pub_msg, delay=45))
                except Exception:
                    pass

            logger.info(f"🌟 [Propina Procesada]: Usuario {user_id} donó {stars_paid} Stars a la comunidad {chat_id}.")
        except Exception as e:
            if benefit_granted:
                logger.error(f"Propina {payload!r} registrada; falló un paso posterior no crítico para {user_id}: {e}")
                return
            logger.error(f"Error procesando propina (Iniciando reembolso automático): {e}")
            await process_star_refund(bot, user_id, charge_id, reason=f"Entrega de propina fallida: {e}")
            with contextlib.suppress(Exception):
                await message.answer(t["err_inv"], parse_mode="HTML")