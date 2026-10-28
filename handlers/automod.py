"""
automod.py — The Bunker OS · v40 · Fase 3 · Motor de Automatización Auto-Mod

Etapa del filtro perimetral (groups.group_security_matrix la invoca UNA vez por mensaje, después de las exenciones
de administradores / VIP / whitelist y antes de los filtros clásicos): así un mismo mensaje nunca recibe dos
sanciones de dos motores distintos.

Diseño no bloqueante
--------------------
1. analyze(): función PURA y síncrona con expresiones regulares precompiladas y acotadas (sin retroceso
   catastrófico, texto limitado a 4096 caracteres). Coste: microsegundos; no toca la red ni la base de datos.
2. inspect_message(): si hay detección, borra el mensaje (una llamada) y delega TODO lo demás —registro de la
   infracción, conteo en la ventana, silencio / baneo, tarjeta de auditoría, aviso temporal— a una tarea en
   segundo plano (logger_hub.spawn_audit_task). El handler de aiogram queda libre de inmediato.

Reglas (activables por comunidad)
---------------------------------
phishing ..... dominios punycode / IP literal / imitaciones de Telegram y billeteras / acortadores o TLD de alto
               riesgo junto a señuelos ("claim", "airdrop", "connect wallet"…), y peticiones de frase semilla.
scam ......... promesas de ganancias diarias, "duplica tu cripto", Premium gratis, "escríbeme para invertir".
invite_spam .. enlaces de invitación privados (t.me/+…, joinchat).
mention_flood  8 o más menciones en un mensaje.
char_flood ... 20+ repeticiones del mismo carácter o 8+ de la misma palabra seguidas.
repeat_flood . el mismo texto 3 veces en 2 minutos (memoria acotada, sin base de datos).
obfuscation .. caracteres invisibles o mezcla latino/cirílico dentro de una palabra (homoglifos) junto a enlaces
               o señuelos.

Escalado automático: al alcanzar `escalation_threshold` infracciones (3 por defecto) dentro de
`escalation_window_hours` (24 h), el usuario recibe un BANEO PERMANENTE sin intervención humana, con la tarjeta
#AUTOMOD_ACTION (rastro de evidencias + botón «Reportar falso positivo») y, si la comunidad tiene apelaciones
activas, la oferta de apelación pagada con Stars.

Comando del Dueño: /automod [on|off|status]  (también desde la Mini App: GET/POST /api/chat/{id}/automod).
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database import crud_fase3 as crud
from database.models_fase3 import AUTOMOD_RULES, AutomodConfig
from handlers import telegram_ops
from handlers.logger_hub import dispatch_audit_log, mark_audit_handled, spawn_audit_task

logger = logging.getLogger("bunker.automod")
router = Router(name="automod")

TEXT_LIMIT = 4096
CONFIG_TTL_S = 60.0
NOTICE_TTL_S = 15
REPEAT_WINDOW_S = 120.0
REPEAT_THRESHOLD = 3
REPEAT_TRACK_MAX = 5000
MENTION_FLOOD_MIN = 8
EVIDENCE_TRAIL_MAX = 6


# ==========================================
# 🔎 PATRONES (precompilados, cuantificadores acotados)
# ==========================================
_INVISIBLE_RE = re.compile("[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮"
                           "⁠-⁤⁪-⁯ㅤ﻿ﾠ]")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'`]{1,512}")
_BARE_HOST_RE = re.compile(r"(?i)(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,8}[a-z]{2,24})(?:/[^\s<>\"']{0,256})?")
_IP_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_INVITE_RE = re.compile(r"(?i)(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)[A-Za-z0-9_-]{6,64}")
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
_CHAR_FLOOD_RE = re.compile(r"(\S)\1{19,}")
_WORD_FLOOD_RE = re.compile(r"(?i)\b(\w{2,30})\b(?:[\s,.!?;:]+\1\b){7,}")
_MIXED_SCRIPT_TOKEN_RE = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[Ѐ-ӿ])\w{4,40}\b")

_LURE_RE = re.compile(
    r"(?i)\b(?:claim(?:\s+now)?|airdrop|giveaway|free\s+(?:usdt|ton|btc|eth|nft|premium|stars|crypto)|"
    r"verify\s+(?:your\s+)?(?:account|wallet|identity)|connect\s+(?:your\s+)?wallet|wallet\s+connect|"
    r"unlock\s+(?:your\s+)?(?:account|funds)|reclama(?:r)?(?:\s+ya)?|sorteo|regalo\s+de|"
    r"verifica\s+tu\s+(?:cuenta|billetera)|conecta\s+tu\s+(?:billetera|wallet)|desbloquea\s+tu\s+cuenta)\b")
_SEED_RE = re.compile(
    r"(?i)\b(?:enter|send|share|type|paste|submit|introduce|env[ií]a(?:nos)?|comparte|escribe|pega)\b[^\n]{0,40}?"
    r"\b(?:seed|recovery|secret|mnemonic)\s+(?:phrase|words|key)|"
    r"\b(?:env[ií]a|comparte|escribe|introduce|pega)\b[^\n]{0,40}?\bfrase\s+(?:semilla|de\s+recuperaci[oó]n)")
_SCAM_RES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:earn|make|gana[rs]?|genera[rs]?|cobra[rs]?)\b[^\n]{0,40}?(?:\$|usd\b|usdt\b|€|eur\b)\s?\d{2,}"
                r"[^\n]{0,40}?\b(?:per|a|al|por|cada|every)\s+(?:day|daily|week|hour|d[ií]a|semana|hora)\b"), "daily earnings"),
    (re.compile(r"(?i)(?:\$|usdt?\s?|€)\s?\d{2,}[^\n]{0,30}?\b(?:per|a|al|por|cada)\s+(?:day|week|hour|d[ií]a|semana|hora)\b"),
     "daily earnings"),
    (re.compile(r"(?i)\b(?:double|triple|duplica|triplica)\s+(?:your|tu|tus)\s+(?:crypto|btc|eth|usdt|money|dinero|inversi[oó]n)"),
     "doubling scheme"),
    (re.compile(r"(?i)\btelegram\s+premium\b[^\n]{0,30}?\b(?:free|gratis|for\s+free|regalo)\b"), "free Premium"),
    (re.compile(r"(?i)\b(?:dm|pm|inbox|md|escr[ií]beme|h[aá]blame|mensaje\s+privado)\b[^\n]{0,30}?"
                r"\b(?:invest\w*|inversi[oó]n|profit\w*|ganancias|trading|forex|signals?|se[nñ]ales)\b"), "investment DM bait"),
    (re.compile(r"(?i)\b(?:guaranteed|garantizad[oa]s?)\s+(?:profit|returns?|ganancias?|retorno)"), "guaranteed returns"),
)

_SHORTENERS = frozenset({"bit.ly", "tinyurl.com", "cutt.ly", "is.gd", "rb.gy", "shorturl.at", "s.id", "tiny.cc",
                         "goo.su", "t.ly", "ow.ly", "buff.ly", "rebrand.ly", "shorte.st", "adf.ly", "v.gd", "clck.ru"})
_RISKY_TLDS = frozenset({"xyz", "top", "click", "zip", "mov", "gq", "tk", "ml", "cf", "ga", "icu", "cyou", "rest", "sbs",
                         "lol", "monster", "quest", "cfd", "bond", "buzz", "live", "support", "claims", "gift"})
_OFFICIAL_HOSTS = frozenset({"telegram.org", "t.me", "telegram.me", "telegra.ph", "telesco.pe", "fragment.com",
                             "telegram.dog", "core.telegram.org", "desktop.telegram.org", "web.telegram.org",
                             "metamask.io", "trustwallet.com", "tonkeeper.com", "binance.com", "coinbase.com",
                             "phantom.app", "ledger.com", "ton.org", "wallet.tg"})
_BRAND_RE = re.compile(r"(?i)(?:te[l1][e3]g[r]?[a@]m|te[l1][e3]gra(?:rn|nn|n)|tel[e3]qram|t-me\b|tgram|metamask|"
                       r"trust-?wallet|tonkeeper|binance|coinbase|phantom-?wallet|phantom-app|tonspace|wallet-?tg)")


@dataclass(frozen=True)
class Detection:
    rule: str
    evidence: str          # descripción corta y segura (sin el mensaje completo)


# ==========================================
# 🧪 ANÁLISIS PURO
# ==========================================
def normalize_text(text: str) -> Tuple[str, bool]:
    """NFKC + sin caracteres invisibles. → (texto normalizado, ¿había caracteres invisibles?)"""
    clipped = (text or "")[:TEXT_LIMIT]
    without = _INVISIBLE_RE.sub("", clipped)
    return unicodedata.normalize("NFKC", without), without != clipped


def _host_of(url: str) -> str:
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_official(host: str) -> bool:
    return host in _OFFICIAL_HOSTS or any(host.endswith("." + official) for official in _OFFICIAL_HOSTS)


def extract_hosts(text: str, entity_urls: List[str]) -> List[str]:
    """Hosts únicos de URLs explícitas, hipervínculos (text_link) y dominios sueltos."""
    seen: "OrderedDict[str, None]" = OrderedDict()
    for url in list(entity_urls) + _URL_RE.findall(text):
        host = _host_of(url)
        if host:
            seen.setdefault(host, None)
    for match in _BARE_HOST_RE.finditer(text):
        host = match.group(1).lower()
        tld = host.rsplit(".", 1)[-1]
        if (tld in _RISKY_TLDS or host.startswith("xn--") or ".xn--" in host or host in _SHORTENERS
                or _BRAND_RE.search(host)):
            seen.setdefault(host[4:] if host.startswith("www.") else host, None)
    return list(seen.keys())[:20]


def _phishing(text: str, hosts: List[str]) -> Optional[Detection]:
    if _SEED_RE.search(text):
        return Detection("phishing", "asks for a wallet seed / recovery phrase")
    lure = _LURE_RE.search(text)
    for host in hosts:
        if _is_official(host):
            continue
        if "xn--" in host:
            return Detection("phishing", f"punycode domain {host}")
        if _IP_HOST_RE.match(host):
            return Detection("phishing", f"raw IP link {host}")
        if _BRAND_RE.search(host):
            return Detection("phishing", f"look-alike domain {host}")
        tld = host.rsplit(".", 1)[-1]
        if lure and (host in _SHORTENERS or tld in _RISKY_TLDS):
            return Detection("phishing", f"lure “{lure.group(0)[:40]}” + {host}")
    return None


def analyze(text: str, *, entity_urls: Optional[List[str]] = None, mention_count: int = 0,
            rules: Optional[Dict[str, bool]] = None) -> Optional[Detection]:
    """Primera regla que se cumple (de mayor a menor gravedad). Pura: sin red, sin base de datos."""
    enabled = rules or {rule: True for rule in AUTOMOD_RULES}
    normalized, had_invisible = normalize_text(text)
    hosts = extract_hosts(normalized, list(entity_urls or []))

    if enabled.get("phishing", True):
        found = _phishing(normalized, hosts)
        if found:
            return found
    if enabled.get("obfuscation", True):
        mixed = _MIXED_SCRIPT_TOKEN_RE.search(normalized)
        risky = bool(hosts) or bool(_LURE_RE.search(normalized)) or bool(_INVITE_RE.search(normalized))
        if (had_invisible and risky) or (mixed and (risky or _BRAND_RE.search(mixed.group(0)))):
            token = mixed.group(0) if mixed else "invisible characters"
            return Detection("obfuscation", f"obfuscated text ({token[:40]})")
    if enabled.get("scam", True):
        for pattern, label in _SCAM_RES:
            if pattern.search(normalized):
                return Detection("scam", label)
    if enabled.get("invite_spam", True):
        invite = _INVITE_RE.search(normalized)
        if invite:
            return Detection("invite_spam", f"private invite link {invite.group(0)[:48]}")
    if enabled.get("mention_flood", True):
        mentions = max(int(mention_count), len(_HANDLE_RE.findall(normalized)))
        if mentions >= MENTION_FLOOD_MIN:
            return Detection("mention_flood", f"{mentions} mentions in one message")
    if enabled.get("char_flood", True):
        if _CHAR_FLOOD_RE.search(normalized):
            return Detection("char_flood", "20+ repeated characters")
        word = _WORD_FLOOD_RE.search(normalized)
        if word:
            return Detection("char_flood", f"word “{word.group(1)[:20]}” repeated 8+ times")
    return None


class RepeatTracker:
    """Mismo texto del mismo usuario N veces en la ventana. Memoria acotada (LRU), sin base de datos."""

    def __init__(self, window_s: float = REPEAT_WINDOW_S, threshold: int = REPEAT_THRESHOLD,
                 max_keys: int = REPEAT_TRACK_MAX) -> None:
        self.window_s, self.threshold, self.max_keys = window_s, threshold, max_keys
        self._data: "OrderedDict[Tuple[int, int], List[Tuple[float, str]]]" = OrderedDict()

    def hit(self, chat_id: int, user_id: int, text: str, now: Optional[float] = None) -> bool:
        normalized = re.sub(r"\s+", " ", normalize_text(text)[0].lower()).strip()
        if len(normalized) < 6:
            return False
        digest = hashlib.blake2s(normalized.encode("utf-8"), digest_size=12).hexdigest()
        stamp = time.monotonic() if now is None else now
        key = (int(chat_id), int(user_id))
        entries = [e for e in self._data.pop(key, []) if stamp - e[0] <= self.window_s]
        entries.append((stamp, digest))
        self._data[key] = entries[-10:]
        while len(self._data) > self.max_keys:
            self._data.popitem(last=False)
        if sum(1 for _, d in entries if d == digest) >= self.threshold:
            self._data[key] = [e for e in entries if e[1] != digest]   # una infracción por ráfaga
            return True
        return False


_REPEATS = RepeatTracker()


# ==========================================
# ⚙️ CONFIGURACIÓN EN CACHÉ
# ==========================================
_CONFIG_CACHE: Dict[int, Tuple[float, AutomodConfig]] = {}


async def get_config(group_id: int) -> AutomodConfig:
    now = time.monotonic()
    cached = _CONFIG_CACHE.get(int(group_id))
    if cached and now - cached[0] < CONFIG_TTL_S:
        return cached[1]
    cfg = await crud.get_automod_config(int(group_id))
    if len(_CONFIG_CACHE) > 5000:
        _CONFIG_CACHE.clear()
    _CONFIG_CACHE[int(group_id)] = (now, cfg)
    return cfg


def forget_config(group_id: int) -> None:
    _CONFIG_CACHE.pop(int(group_id), None)


async def save_config(group_id: int, patch: Dict[str, Any], updated_by: Optional[int],
                      bot: Optional[Bot] = None, actor: Any = None) -> AutomodConfig:
    """Guarda (validado, todo o nada), invalida la caché y deja constancia en el canal de registro."""
    cfg = await crud.save_automod_config(int(group_id), patch, updated_by)
    forget_config(group_id)
    rules_on = ", ".join(rule for rule, on in cfg["rules"].items() if on) or "—"
    spawn_audit_task(dispatch_audit_log(
        bot, int(group_id), "automod_config", actor=actor if actor is not None else updated_by,
        detail=("enabled" if cfg["enabled"] else "disabled"),
        extra={("Escalation", "Escalado"): f"{cfg['escalation_threshold']} / {cfg['escalation_window_hours']} h → ban",
               ("Rules", "Reglas"): rules_on, ("On infraction", "Por infracción"): cfg["infraction_action"]},
    ), name=f"automod_config:{group_id}")
    return cfg


# ==========================================
# 🚦 ETAPA DE LA MATRIZ PERIMETRAL
# ==========================================
def _entity_urls(message: Message) -> Tuple[List[str], int]:
    urls: List[str] = []
    mentions = 0
    text = message.text or message.caption or ""
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        kind = str(getattr(entity.type, "value", entity.type))
        if kind == "text_link" and entity.url:
            urls.append(entity.url)
        elif kind == "url":
            with contextlib.suppress(Exception):
                urls.append(entity.extract_from(text))
        elif kind in ("mention", "text_mention"):
            mentions += 1
    return urls, mentions


async def inspect_message(bot: Bot, message: Message, *, is_edit: bool = False) -> bool:
    """
    True si Auto-Mod tomó el mensaje (ya borrado; la sanción sigue en segundo plano) y la matriz debe detenerse.
    Nunca lanza: un fallo del motor deja pasar el mensaje a los filtros clásicos.
    """
    user = message.from_user
    if user is None or user.is_bot:
        return False
    try:
        cfg = await get_config(message.chat.id)
        if not cfg["enabled"]:
            return False
        text = message.text or message.caption or ""
        urls, mentions = _entity_urls(message)
        detection = analyze(text, entity_urls=urls, mention_count=mentions, rules=cfg["rules"]) if (text or urls) else None
        if detection is None and not is_edit and cfg["rules"].get("repeat_flood", True) and text:
            if _REPEATS.hit(message.chat.id, user.id, text):
                detection = Detection("repeat_flood", f"same message {REPEAT_THRESHOLD}× in {int(REPEAT_WINDOW_S)} s")
        if detection is None:
            return False
    except Exception as ex:
        logger.debug(f"[Auto-Mod] Análisis omitido en {message.chat.id}: {ex}")
        return False

    with contextlib.suppress(TelegramAPIError, asyncio.TimeoutError):
        await asyncio.wait_for(message.delete(), timeout=telegram_ops.TG_TIMEOUT_S)
    spawn_audit_task(_enforce(bot, message.chat.id, message.chat.title, user, detection, text, message.message_id, cfg),
                     name=f"automod:{message.chat.id}:{user.id}")
    return True


def _rule_label(rule: str, lang: str) -> str:
    labels = {
        "phishing": ("Phishing", "Phishing"), "scam": ("Scam", "Estafa"), "invite_spam": ("Invite spam", "Spam de invitaciones"),
        "mention_flood": ("Mention flood", "Inundación de menciones"), "char_flood": ("Character flood", "Inundación de caracteres"),
        "repeat_flood": ("Repeated messages", "Mensajes repetidos"), "obfuscation": ("Obfuscated text", "Texto ofuscado"),
    }
    en, es = labels.get(rule, (rule, rule))
    return es if lang == "es" else en


async def _service_lang(group_id: int) -> str:
    try:
        from database.database import get_service_prefs
        prefs = await get_service_prefs(int(group_id))
        return "es" if str(prefs.get("lang")) == "es" else "en"
    except Exception:
        return "en"


async def _temp_notice(bot: Bot, group_id: int, text: str) -> None:
    try:
        sent = await asyncio.wait_for(bot.send_message(int(group_id), text, parse_mode="HTML",
                                                       disable_notification=True), timeout=telegram_ops.TG_TIMEOUT_S)
    except (TelegramAPIError, asyncio.TimeoutError):
        return
    await asyncio.sleep(NOTICE_TTL_S)
    with contextlib.suppress(TelegramAPIError, asyncio.TimeoutError):
        await asyncio.wait_for(sent.delete(), timeout=telegram_ops.TG_TIMEOUT_S)


def _claim(group_id: int, user_id: int, *kinds: str) -> None:
    for kind in kinds:
        mark_audit_handled(f"{int(group_id)}:{kind}:{int(user_id)}", 30.0)


def fp_button(sanction_id: int) -> Tuple[str, str, str]:
    """Botón de la tarjeta: lo pulsa el Dueño para revertir (handlers/moderation.py → cb_false_positive)."""
    return ("↩️ Report false positive", "↩️ Reportar falso positivo", f"fp_{int(sanction_id)}")


async def _enforce(bot: Bot, group_id: int, chat_title: Optional[str], user: Any, detection: Detection, text: str,
                   message_id: Optional[int], cfg: AutomodConfig) -> None:
    """Registro + escalado + sanción + auditoría. Corre en segundo plano (nunca bloquea al handler)."""
    lang = await _service_lang(group_id)
    excerpt = text[:300] if text else None
    threshold, window = int(cfg["escalation_threshold"]), int(cfg["escalation_window_hours"])
    action_taken = "mute" if cfg["infraction_action"] == "mute" else "delete"
    try:
        _infraction_id, count = await crud.record_infraction_and_count(
            group_id, user.id, detection.rule, evidence=detection.evidence, excerpt=excerpt, message_id=message_id,
            action_taken=action_taken, window_hours=window)
    except Exception as ex:
        logger.error(f"❌ [Auto-Mod] No se pudo registrar la infracción de {user.id} en {group_id}: {ex}")
        return
    with contextlib.suppress(Exception):
        from handlers.groups import track_security
        track_security(group_id, user.id, "automod", "delete", False)

    mention = f'<a href="tg://user?id={int(user.id)}">{html.escape(getattr(user, "full_name", None) or str(user.id))}</a>'
    rule_txt = html.escape(_rule_label(detection.rule, lang))

    if count >= threshold:
        await _escalate(bot, group_id, chat_title, user, detection, excerpt, count, threshold, window, lang, mention)
        return

    mute_minutes = int(cfg["mute_minutes"])
    measure = "delete"
    if action_taken == "mute":
        _claim(group_id, user.id, "restrict")
        result = await telegram_ops.mute_member(group_id, user.id, preferred=bot, minutes=mute_minutes)
        if result.ok:
            measure = "mute"
        else:
            logger.warning(f"[Auto-Mod] Silencio no aplicado a {user.id} en {group_id}: {result.code} {result.detail}")
    await dispatch_audit_log(
        bot, group_id, "automod_infraction", user=user, automatic=True, system="automod", action=measure,
        reason=_rule_label(detection.rule, lang), strikes=count, limit=threshold,
        duration_minutes=mute_minutes if measure == "mute" else None, detail=detection.evidence, excerpt=excerpt,
        chat_title=chat_title, extra={("Window", "Ventana"): f"{window} h"},
    )
    notice = (f"🤖 <b>Auto-Mod</b> · {mention}: mensaje eliminado ({rule_txt}). Infracción <b>{count}/{threshold}</b> "
              f"— al llegar a {threshold} en {window} h el baneo es permanente.") if lang == "es" else \
             (f"🤖 <b>Auto-Mod</b> · {mention}: message removed ({rule_txt}). Infraction <b>{count}/{threshold}</b> "
              f"— reaching {threshold} within {window} h means a permanent ban.")
    await _temp_notice(bot, group_id, notice)


async def _escalate(bot: Bot, group_id: int, chat_title: Optional[str], user: Any, detection: Detection,
                    excerpt: Optional[str], count: int, threshold: int, window: int, lang: str, mention: str) -> None:
    """Umbral alcanzado: baneo PERMANENTE sin intervención humana + #AUTOMOD_ACTION con el rastro de evidencias."""
    _claim(group_id, user.id, "removal", "restrict")
    result = await telegram_ops.ban_member(group_id, user.id, preferred=bot)
    if not result.ok:
        if result.code == "user_is_admin":
            logger.info(f"[Auto-Mod] {user.id} es administrador en {group_id}: escalada omitida.")
            return
        logger.warning(f"[Auto-Mod] Baneo automático fallido de {user.id} en {group_id}: {result.code} {result.detail}")
        await dispatch_audit_log(
            bot, group_id, "automod_action", user=user, automatic=True, system="automod", action="ban",
            reason=_rule_label(detection.rule, lang), strikes=count, limit=threshold,
            detail=f"⚠️ Telegram refused the ban ({result.code}). Give the bot “Ban users” rights.",
            excerpt=excerpt, chat_title=chat_title)
        return

    with contextlib.suppress(Exception):
        from handlers.groups import registry_forget, track_security
        await registry_forget(group_id, user.id)
        track_security(group_id, user.id, "automod", "ban", False)
    sanction_id = 0
    try:
        sanction_id = await crud.record_sanction(
            group_id, user.id, "ban", "automod", user_name=getattr(user, "full_name", None),
            reason=f"Auto-Mod escalation: {count} infractions / {window} h (last: {detection.rule})")
    except Exception as ex:
        logger.error(f"❌ [Auto-Mod] Baneo aplicado pero no registrado ({user.id} en {group_id}): {ex}")

    trail: List[str] = []
    with contextlib.suppress(Exception):
        for inf in await crud.recent_infractions(group_id, user.id, window, EVIDENCE_TRAIL_MAX):
            stamp = time.strftime("%m-%d %H:%M", time.gmtime(inf["created_at"]))
            snippet = (inf["excerpt"] or "").replace("\n", " ")[:60]
            trail.append(f"{stamp} UTC · {_rule_label(inf['rule'], lang)} · {inf['evidence'] or '—'}"
                         + (f" · «{snippet}»" if snippet else ""))
    await dispatch_audit_log(
        bot, group_id, "automod_action", user=user, automatic=True, system="automod", action="ban",
        reason=_rule_label(detection.rule, lang), strikes=count, limit=threshold, duration_minutes=0,
        detail=f"{count} infractions in {window} h → permanent ban (no human intervention)",
        evidence=trail or [detection.evidence], excerpt=excerpt, chat_title=chat_title,
        buttons=[fp_button(sanction_id)] if sanction_id else None,
    )
    await _offer_appeal(bot, group_id, user, detection, count, threshold, lang)
    notice = (f"⛔ <b>Auto-Mod</b> · {mention} fue baneado de forma permanente ({count} infracciones en {window} h).")\
        if lang == "es" else (f"⛔ <b>Auto-Mod</b> · {mention} was permanently banned ({count} infractions in {window} h).")
    await _temp_notice(bot, group_id, notice)


async def _offer_appeal(bot: Bot, group_id: int, user: Any, detection: Detection, count: int, threshold: int,
                        lang: str) -> None:
    """Si la comunidad tiene apelaciones activas, el baneado recibe la oferta de desbaneo pagado (Stars)."""
    try:
        from database.database import is_user_globally_banned
        prior_global = bool(await is_user_globally_banned(int(user.id)))
    except Exception:
        prior_global = False
    with contextlib.suppress(Exception):
        appeals = telegram_ops._ban_appeals_module()
        hook = getattr(appeals, "on_ladder_ban", None) if appeals is not None else None
        if callable(hook):
            await hook(bot, group_id=int(group_id), user=user, reason_key=f"automod_{detection.rule}",
                       reason_es=f"Auto-Mod: {_rule_label(detection.rule, 'es')} ({count} infracciones)",
                       reason_en=f"Auto-Mod: {_rule_label(detection.rule, 'en')} ({count} infractions)",
                       strikes=count, limit=threshold, prior_global_ban=prior_global)


# ==========================================
# 👑 /automod (solo el Dueño)
# ==========================================
@router.message(Command("automod"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_automod(message: Message, command: CommandObject, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    status = await telegram_ops.member_status(message.chat.id, user.id, preferred=bot)
    try:
        from handlers.moderation import is_super_admin
        architect = is_super_admin(user.id)
    except Exception:
        architect = False
    if status != "creator" and not architect:
        with contextlib.suppress(TelegramAPIError):
            await message.delete()
        return
    lang = await _service_lang(message.chat.id)
    arg = (command.args or "").strip().lower() if command is not None else ""
    try:
        if arg in ("on", "off", "activar", "desactivar"):
            cfg = await save_config(message.chat.id, {"enabled": arg in ("on", "activar")}, user.id, bot=bot, actor=user)
        else:
            cfg = await get_config(message.chat.id)
    except crud.Fase3ValidationError as err:
        await message.reply(f"❌ {html.escape(str(err))}")
        return
    rules = ", ".join(_rule_label(r, lang) for r, on in cfg["rules"].items() if on) or "—"
    if lang == "es":
        text = (f"🤖 <b>Auto-Mod</b>: {'🟢 activo' if cfg['enabled'] else '🔴 inactivo'}\n"
                f"• Escalado: <b>{cfg['escalation_threshold']}</b> infracciones en <b>{cfg['escalation_window_hours']} h</b> → baneo permanente\n"
                f"• Por infracción: <b>{'silencio ' + str(cfg['mute_minutes']) + ' min' if cfg['infraction_action'] == 'mute' else 'borrado'}</b>\n"
                f"• Reglas: {html.escape(rules)}\n\nUsa <code>/automod on</code> u <code>/automod off</code>; el detalle se ajusta en la Mini App.")
    else:
        text = (f"🤖 <b>Auto-Mod</b>: {'🟢 active' if cfg['enabled'] else '🔴 inactive'}\n"
                f"• Escalation: <b>{cfg['escalation_threshold']}</b> infractions within <b>{cfg['escalation_window_hours']} h</b> → permanent ban\n"
                f"• On infraction: <b>{'mute ' + str(cfg['mute_minutes']) + ' min' if cfg['infraction_action'] == 'mute' else 'delete'}</b>\n"
                f"• Rules: {html.escape(rules)}\n\nUse <code>/automod on</code> or <code>/automod off</code>; fine-tune it in the Mini App.")
    sent = await message.reply(text + "\n\n🛡️ <i>Cloud Media Management</i>", parse_mode="HTML")
    spawn_audit_task(_delete_pair(message, sent, 30), name=f"automod_cmd:{message.chat.id}")


async def _delete_pair(first: Message, second: Message, delay: int) -> None:
    await asyncio.sleep(delay)
    for msg in (first, second):
        with contextlib.suppress(TelegramAPIError, asyncio.TimeoutError):
            await asyncio.wait_for(msg.delete(), timeout=telegram_ops.TG_TIMEOUT_S)


__all__ = ["router", "analyze", "inspect_message", "get_config", "save_config", "forget_config", "fp_button",
           "Detection", "RepeatTracker", "normalize_text", "extract_hosts"]
