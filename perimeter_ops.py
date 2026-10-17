"""
perimeter_ops.py — The Bunker OS v20 · Operaciones perimetrales compartidas

Un único lugar para la lógica que usan a la vez la API de la Mini App (main.py) y la consola del bot
(handlers/user_private.py), para que ambos caminos se comporten exactamente igual:

  1. Ghost Purge v2 ........ criterios de detección (eliminadas, estafa, falsas, userbots marcados,
                              inalcanzables), acción (ban / expulsión suave) y texto del informe.
  2. Canal de registro ..... validación EN VIVO (bot administrador con permiso de publicación, operador
                              creador, destino distinto de la comunidad) antes de guardar, y salud periódica.
  3. Centinela IA .......... validación de ajustes (modos mention_only / always / chance, probabilidad, tonos,
                              directivas) e invalidación inmediata de la caché del Centinela.
  4. Estado del perímetro .. 🟢 / 🟡 / 🔴 real de captcha, AutoLower, escudos, Guardián y Copiloto IA, modo
                              nocturno y canal de registro (configuración + capacidad efectiva).
  5. Soporte técnico IA .... «Arquitecto de Soporte» con Mistral AI, base de conocimientos y directivas del
                              Arquitecto. Todo texto pasa por la Capa 0 (sanitize_pii_for_ai) antes de salir.

assistant y database se importan de forma diferida: este módulo no crea dependencias circulares y puede
probarse de forma aislada. Ningún diccionario en memoria crece sin límite (TTL + tope de tamaño).

The Bunker Command OS © 2026 — Cloud Media Management
"""
import asyncio
import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger("perimeter_ops")


# ==========================================================================
# 0. Utilidades
# ==========================================================================
def _db():
    from database import database as db  # importación diferida (evita ciclos y facilita las pruebas)
    return db


def _assistant():
    try:
        import assistant  # type: ignore[import-not-found]
        return assistant
    except Exception:  # pragma: no cover - el bot puede arrancar sin el radar
        return None


def _bounded_put(store: dict, key, value, cap: int) -> None:
    """Inserta y, si se supera el tope, descarta las entradas más antiguas (orden de inserción)."""
    store.pop(key, None)
    store[key] = value
    overflow = len(store) - cap
    if overflow > 0:
        for old in list(store)[:overflow]:
            store.pop(old, None)


def _flag(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return 1 if int(value) == 1 else 0
    except (TypeError, ValueError):
        return 1 if str(value).strip().lower() in ("true", "on", "yes", "si", "sí") else 0


def _enum_name(value) -> str:
    return str(getattr(value, "name", None) or getattr(value, "value", None) or value or "").upper()


# ==========================================================================
# 1. GHOST PURGE v2 — criterios, acción e informe
# ==========================================================================
PURGE_CRITERIA = ("deleted", "scam", "fake", "flagged", "unreachable")
DEFAULT_PURGE_CRITERIA = ("deleted", "scam", "fake", "flagged")   # «inalcanzables» es opcional (ver docstring)
PURGE_ACTIONS = ("ban", "kick")
_PURGE_ACTION_ALIASES = {
    "ban": "ban", "permanent": "ban", "perma": "ban",
    "kick": "kick", "soft": "kick", "expulsion": "kick", "expulsión": "kick", "unban": "kick",
}
_DELETED_PLACEHOLDER_NAMES = {"deleted account", "cuenta eliminada"}

PURGE_REASON_LABELS = {
    "es": {
        "deleted": "💀 Cuentas eliminadas",
        "scam": "🚩 Marcadas como estafa por Telegram",
        "fake": "🎭 Marcadas como falsas por Telegram",
        "flagged": "🤖 Userbots marcados por el radar",
        "unreachable": "👻 Inalcanzables (vistas hace mucho / bloqueo)",
    },
    "en": {
        "deleted": "💀 Deleted accounts",
        "scam": "🚩 Flagged as scam by Telegram",
        "fake": "🎭 Flagged as fake by Telegram",
        "flagged": "🤖 Userbots flagged by the radar",
        "unreachable": "👻 Unreachable (long time ago / blocked)",
    },
}


def parse_purge_criteria(raw) -> tuple:
    """
    CSV o lista → tupla canónica (orden de PURGE_CRITERIA, sin duplicados ni valores desconocidos).
    None → criterios por defecto. Cadena vacía → () (el operador desactivó todos los criterios).
    """
    if raw is None:
        return DEFAULT_PURGE_CRITERIA
    if isinstance(raw, str):
        items = [p.strip().lower() for p in raw.split(",")]
    else:
        try:
            items = [str(p).strip().lower() for p in raw]
        except TypeError:
            return DEFAULT_PURGE_CRITERIA
    wanted = {p for p in items if p}
    return tuple(c for c in PURGE_CRITERIA if c in wanted)


def serialize_purge_criteria(criteria: Iterable[str]) -> str:
    return ",".join(parse_purge_criteria(list(criteria or [])))


def normalize_purge_action(value, default: str = "ban") -> str:
    return _PURGE_ACTION_ALIASES.get(str(value or "").strip().lower(), default if default in PURGE_ACTIONS else "ban")


def toggle_purge_criterion(criteria: Iterable[str], criterion: str) -> tuple:
    current = set(parse_purge_criteria(list(criteria or [])))
    if criterion not in PURGE_CRITERIA:
        return tuple(c for c in PURGE_CRITERIA if c in current)
    current.symmetric_difference_update({criterion})
    return tuple(c for c in PURGE_CRITERIA if c in current)


def classify_ghost(user, *, criteria: Iterable[str], flagged_ids: Iterable[int] = ()) -> Optional[str]:
    """
    Motivo de purga de un miembro (o None). Funciona con objetos User de Pyrogram (is_deleted, is_scam, is_fake,
    status) y de la Bot API (sin esos campos: solo «eliminada» por su nombre y «userbot marcado»).
    Los bots nunca se purgan: los añade un administrador y no son cuentas fantasma.
    """
    if user is None or getattr(user, "is_bot", False):
        return None
    wanted = set(criteria or ())
    try:
        uid = int(getattr(user, "id", 0) or 0)
    except (TypeError, ValueError):
        uid = 0
    if "deleted" in wanted:
        first = str(getattr(user, "first_name", "") or "").strip().lower()
        if getattr(user, "is_deleted", False) or (
            first in _DELETED_PLACEHOLDER_NAMES and not getattr(user, "username", None) and not getattr(user, "last_name", None)
        ):
            return "deleted"
    if "flagged" in wanted and uid and uid in set(flagged_ids or ()):
        return "flagged"
    if "scam" in wanted and getattr(user, "is_scam", False):
        return "scam"
    if "fake" in wanted and getattr(user, "is_fake", False):
        return "fake"
    if "unreachable" in wanted and _enum_name(getattr(user, "status", None)) == "LONG_TIME_AGO":
        return "unreachable"
    return None


def build_purge_report_text(report: dict, lang: str = "es") -> str:
    """Aviso HTML del resultado de la purga (comunidad y canal de registro)."""
    lang = "en" if lang == "en" else "es"
    labels = PURGE_REASON_LABELS[lang]
    by_reason = report.get("by_reason") or {}
    action = normalize_purge_action(report.get("action"))
    if lang == "es":
        head = "💀 <b>The Bunker Bot: Ghost Purge Completada</b>"
        act = "Baneo permanente 🔴" if action == "ban" else "Expulsión suave 🟡"
        lines = [
            head, "",
            f"• Miembros analizados: <b>{int(report.get('scanned', 0))}</b>",
            f"• Amenazas detectadas: <b>{int(report.get('found', 0))}</b>",
            f"• Cuentas purgadas: <b>{int(report.get('purged', 0))}</b>",
        ]
        if report.get("failed"):
            lines.append(f"• Sin poder purgar (permisos/admins): <b>{int(report['failed'])}</b>")
        lines.append(f"• Directiva: <code>{act}</code>")
    else:
        act = "Permanent ban 🔴" if action == "ban" else "Soft kick 🟡"
        lines = [
            "💀 <b>The Bunker Bot: Ghost Purge Completed</b>", "",
            f"• Members scanned: <b>{int(report.get('scanned', 0))}</b>",
            f"• Threats detected: <b>{int(report.get('found', 0))}</b>",
            f"• Accounts purged: <b>{int(report.get('purged', 0))}</b>",
        ]
        if report.get("failed"):
            lines.append(f"• Could not purge (rights/admins): <b>{int(report['failed'])}</b>")
        lines.append(f"• Directive: <code>{act}</code>")
    breakdown = [f"  {labels[r]}: <b>{int(by_reason[r])}</b>" for r in PURGE_CRITERIA if by_reason.get(r)]
    if breakdown:
        lines += ["", *breakdown]
    if report.get("truncated"):
        lines += ["", "⚠️ <i>" + ("Telegram solo expone parte de los miembros de grupos muy grandes; vuelve a ejecutar más tarde."
                                    if lang == "es" else
                                    "Telegram only exposes part of the members of very large groups; run again later.") + "</i>"]
    lines += ["", "🛡️ <i>Cloud Media Management</i>"]
    return "\n".join(lines)


async def get_purge_settings(group_id: int) -> dict:
    """{'action', 'criteria': [...], 'last_scan'} a partir de la configuración persistida."""
    cfg = await _db().get_ghost_purge_config(int(group_id)) or {}
    return {
        "action": normalize_purge_action(cfg.get("purge_action") or cfg.get("action")),
        "criteria": list(parse_purge_criteria(cfg.get("criteria"))),
        "last_scan": cfg.get("purge_last_free_scan") or cfg.get("last_free_scan"),
        "available_criteria": list(PURGE_CRITERIA),
    }


async def save_purge_settings(group_id: int, *, action=None, criteria=None) -> dict:
    db = _db()
    if action is not None:
        await db.set_ghost_purge_config(int(group_id), "purge_action", normalize_purge_action(action))
    if criteria is not None:
        await db.set_ghost_purge_config(int(group_id), "purge_criteria", serialize_purge_criteria(criteria))
    return await get_purge_settings(group_id)


# ==========================================================================
# 2. CANAL DE REGISTRO — validación en vivo y salud
# ==========================================================================
LOG_ALIAS_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
LOG_ID_RE = re.compile(r"^-?\d{5,20}$")


class LogChannelError(Exception):
    """Error de validación del canal de registro. `status` sigue la semántica HTTP que ya usa la Mini App."""

    def __init__(self, code: str, status: int, message: str):
        self.code, self.status, self.message = code, int(status), message
        super().__init__(message)


async def _default_is_creator(bot, user_id: int, chat_id: int) -> bool:
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, user_id), timeout=6)
    except Exception:
        return False
    return _enum_name(getattr(member, "status", "")) in ("CREATOR", "OWNER")


def _bot_can_post(member, chat_kind: str) -> bool:
    status = _enum_name(getattr(member, "status", ""))
    if status in ("CREATOR", "OWNER"):
        return True
    if status == "ADMINISTRATOR":
        return chat_kind != "CHANNEL" or bool(getattr(member, "can_post_messages", False))
    if status == "MEMBER":
        return chat_kind != "CHANNEL"
    if status == "RESTRICTED":
        return chat_kind != "CHANNEL" and bool(getattr(member, "can_send_messages", False))
    return False


async def resolve_log_channel(bot, user_id: int, chat_id: int, raw: Any, *,
                              is_creator: Callable[[int, int], Awaitable[bool]] = None,
                              timeout: float = 6.0) -> str:
    """
    Normaliza y verifica el canal de registro ANTES de guardarlo (misma regla en la Mini App y en el bot):
      • '' / 'off' → desactivar (devuelve '').
      • @alias o ID numérico → el destino debe existir, ser canal o grupo, distinto de la comunidad auditada;
        el operador debe ser su creador y el bot debe poder publicar en él.
    Devuelve el ID numérico como texto o lanza LogChannelError.
    """
    value = str(raw or "").strip()
    if not value or value.lower() in ("off", "none", "0", "desactivar", "disable"):
        return ""
    if not (LOG_ALIAS_RE.match(value) or LOG_ID_RE.match(value)):
        raise LogChannelError("format", 422, "usa un @alias público o el ID numérico (-100…) del canal.")
    if bot is None:
        raise LogChannelError("bot_unavailable", 503, "el bot no está disponible para verificar el canal.")
    lookup: Any = int(value) if value.lstrip("-").isdigit() else value
    try:
        target = await asyncio.wait_for(bot.get_chat(lookup), timeout=timeout)
    except Exception:
        raise LogChannelError("not_found", 422, "no se encontró el canal. Añade el bot como administrador y revisa el ID/@alias.")
    target_id = int(target.id)
    kind = _enum_name(getattr(target, "type", ""))
    if kind == "PRIVATE":
        raise LogChannelError("private", 422, "el canal de registro debe ser un canal o un grupo, no un chat privado.")
    if target_id == int(chat_id):
        raise LogChannelError("same_chat", 422, "el canal de registro debe ser distinto de la comunidad auditada.")
    creator_check = is_creator or (lambda uid, cid: _default_is_creator(bot, uid, cid))
    if not await creator_check(int(user_id), target_id):
        raise LogChannelError("not_creator", 403, "debes ser el creador del canal de registro.")
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=timeout)
        member = await asyncio.wait_for(bot.get_chat_member(target_id, me.id), timeout=timeout)
        can_post = _bot_can_post(member, kind)
    except Exception:
        can_post = False
    if not can_post:
        raise LogChannelError("cannot_post", 422, "el bot debe ser administrador con permiso para publicar en el canal de registro.")
    return str(target_id)


LOG_HEALTH_TTL_S = 300.0
LOG_HEALTH_CACHE_MAX = 2000
_log_health_cache: dict = {}   # channel_id → (dict, monotonic_ts)


async def check_log_channel_health(bot, channel_id, *, force: bool = False) -> dict:
    """{'ok', 'reason'} — ¿puede el bot seguir publicando en el canal de registro? Caché de 5 min por canal."""
    if not channel_id:
        return {"ok": False, "reason": "not_configured"}
    key = str(channel_id)
    cached = _log_health_cache.get(key)
    if cached and not force and time.monotonic() - cached[1] < LOG_HEALTH_TTL_S:
        return cached[0]
    if bot is None:
        return {"ok": False, "reason": "bot_unavailable"}
    try:
        target_id = int(key) if key.lstrip("-").isdigit() else key
        chat = await asyncio.wait_for(bot.get_chat(target_id), timeout=6)
        me = await asyncio.wait_for(bot.get_me(), timeout=6)
        member = await asyncio.wait_for(bot.get_chat_member(chat.id, me.id), timeout=6)
        result = {"ok": _bot_can_post(member, _enum_name(getattr(chat, "type", ""))), "reason": None}
        if not result["ok"]:
            result["reason"] = "cannot_post"
    except Exception:
        result = {"ok": False, "reason": "unreachable"}
    _bounded_put(_log_health_cache, key, (result, time.monotonic()), LOG_HEALTH_CACHE_MAX)
    return result


def forget_log_channel_health(channel_id) -> None:
    _log_health_cache.pop(str(channel_id), None)


# ==========================================================================
# 3. CENTINELA IA — ajustes granulares
# ==========================================================================
AI_MODES = ("mention_only", "always", "chance")
AI_TONES = ("guardian", "copilot", "pr")
AI_CHANCE_STEPS = (5, 15, 30, 50)
AI_PROMPT_MAX = 1000
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AISettingsError(ValueError):
    def __init__(self, field: str, message: str):
        self.field, self.message = field, message
        super().__init__(f"{field}: {message}")


def validate_ai_sentinel_patch(patch: dict) -> dict:
    """
    Valida un cambio parcial de la Mini App o del bot → {columna_db: valor}. Lanza AISettingsError en el
    primer campo inválido (el lote completo se rechaza: nunca queda una configuración a medias).
    """
    if not isinstance(patch, dict) or not patch:
        raise AISettingsError("ai_sentinel", "no hay cambios que guardar.")
    out: dict = {}
    for key, value in patch.items():
        if key in ("guardian_status", "copilot_status"):
            out[f"ai_{key}"] = _flag(value)
        elif key == "response_mode":
            mode = str(value or "").strip().lower()
            if mode not in AI_MODES:
                raise AISettingsError(key, f"usa uno de: {', '.join(AI_MODES)}.")
            out["ai_response_mode"] = mode
        elif key == "response_chance":
            try:
                chance = int(value)
            except (TypeError, ValueError):
                raise AISettingsError(key, "debe ser un número entre 1 y 100.")
            if not 1 <= chance <= 100:
                raise AISettingsError(key, "debe estar entre 1 y 100.")
            out["ai_response_chance"] = chance
        elif key == "personality_tone":
            tone = str(value or "").strip().lower()
            if tone not in AI_TONES:
                raise AISettingsError(key, f"usa uno de: {', '.join(AI_TONES)}.")
            out["ai_personality_tone"] = tone
        elif key == "custom_prompt":
            text = _CONTROL_CHARS_RE.sub("", str(value or "")).strip()
            if len(text) > AI_PROMPT_MAX:
                raise AISettingsError(key, f"máximo {AI_PROMPT_MAX} caracteres.")
            out["ai_custom_prompt"] = text
        else:
            raise AISettingsError(key, "campo desconocido.")
    return out


def next_chance_step(current) -> int:
    try:
        value = int(current)
    except (TypeError, ValueError):
        return AI_CHANCE_STEPS[0]
    for step in AI_CHANCE_STEPS:
        if step > value:
            return step
    return AI_CHANCE_STEPS[0]


def public_ai_sentinel_config(cfg: dict) -> dict:
    cfg = cfg or {}
    mode = str(cfg.get("response_mode") or "mention_only").lower()
    tone = str(cfg.get("personality_tone") or "guardian").lower()
    try:
        chance = max(1, min(100, int(cfg.get("response_chance", 15))))
    except (TypeError, ValueError):
        chance = 15
    return {
        "guardian_status": _flag(cfg.get("guardian_status", 0)),
        "copilot_status": _flag(cfg.get("copilot_status", 0)),
        "response_mode": mode if mode in AI_MODES else "mention_only",
        "response_chance": chance,
        "personality_tone": tone if tone in AI_TONES else "guardian",
        "custom_prompt": str(cfg.get("custom_prompt") or ""),
        "modes": list(AI_MODES), "tones": list(AI_TONES), "chance_steps": list(AI_CHANCE_STEPS),
    }


def invalidate_ai_runtime(group_id: int) -> None:
    """El Centinela relee licencia y ajustes en su próximo mensaje (antes, hasta 10 s de desfase)."""
    assistant = _assistant()
    fn = getattr(assistant, "invalidate_ai_sentinel_cache", None) if assistant else None
    if callable(fn):
        try:
            fn(int(group_id))
        except Exception as ex:
            logger.debug(f"[IA] No se pudo invalidar la caché del Centinela ({group_id}): {ex}")


async def apply_ai_sentinel_patch(group_id: int, patch: dict) -> dict:
    """Valida, persiste e invalida la caché del Centinela. Devuelve la configuración pública resultante."""
    columns = validate_ai_sentinel_patch(patch)
    db = _db()
    for column, value in columns.items():
        await db.set_ai_sentinel_config(int(group_id), column, value)
    invalidate_ai_runtime(group_id)
    return public_ai_sentinel_config(await db.get_ai_sentinel_config(int(group_id)))


# ==========================================================================
# 4. ESTADO DEL PERÍMETRO — 🟢 on · 🟡 standby/alert · 🔴 off/fault · 🔒 locked
# ==========================================================================
ULTRA_TIERS = {"ultra", "ultra_pro", "ultrapro", "enterprise", "elite", "unlimited", "diamond", "max"}


def _is_ultra(tier) -> bool:
    normalized = str(tier or "free").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in ULTRA_TIERS or "ultra" in normalized


def _item(key: str, enabled: bool, state: str, detail: str = None, **extra) -> dict:
    return {"key": key, "enabled": bool(enabled), "effective": state in ("on", "alert"), "state": state,
            "detail": detail, **extra}


async def _safe(coro, default):
    try:
        return await coro
    except Exception as ex:
        logger.debug(f"[Perímetro] Lectura fallida: {ex}")
        return default


async def build_perimeter_status(chat_id: int, *, sentinel: dict = None, raid: dict = None, tier: str = None,
                                 log_health: dict = None, ai_online: bool = None) -> dict:
    """
    Estado REAL del perímetro para la primera vista de la Mini App y la consola del bot. Distingue lo que está
    configurado (`enabled`) de lo que de verdad se está aplicando (`effective`): p. ej. AutoLower activado sin
    un Centinela operativo queda en 🟡 standby con detail='no_sentinel'.
    """
    db = _db()
    cid = int(chat_id)
    sentinel = sentinel or {}
    raid = raid or {}
    captcha = _flag(await _safe(db.get_captcha_status(cid), 0))
    autolower = _flag(await _safe(db.get_autolower_status(cid), 1))
    autolower_pct = await _safe(db.get_autolower_pct(cid), 2)
    screen = _flag(await _safe(db.get_screen_shield_status(cid), 1))
    ai_cfg = public_ai_sentinel_config(await _safe(db.get_ai_sentinel_config(cid), {}))
    night = await _safe(db.get_night_mode_config(cid), {}) or {}
    if tier is None:
        tier = await _safe(db.get_group_tier(cid), "free")
    ultra = _is_ultra(tier)
    sentinel_up = bool(sentinel.get("active"))
    if ai_online is None:
        assistant = _assistant()
        available = getattr(assistant, "_mistral_available", None) if assistant else None
        ai_online = bool(available()) if callable(available) else False

    def _voice(key, enabled, **extra):
        if not enabled:
            return _item(key, False, "off", **extra)
        return _item(key, True, "on" if sentinel_up else "standby", None if sentinel_up else "no_sentinel", **extra)

    def _ai(key, enabled):
        if not enabled:
            return _item(key, False, "off")
        if not ultra:
            return _item(key, True, "locked", "no_license")
        return _item(key, True, "on" if ai_online else "standby", None if ai_online else "ai_offline")

    raid_active = bool(raid.get("active"))
    items = [
        _item("captcha", captcha == 1, "on" if captcha == 1 else "off"),
        _voice("autolower", autolower == 1, pct=autolower_pct),
        _voice("screen_shield", screen == 1),
        _item("raid_shield", True, "alert" if raid_active else "on", "raid_in_progress" if raid_active else None,
              remaining_seconds=raid.get("remaining_seconds")),
        _ai("ai_guardian", ai_cfg["guardian_status"] == 1),
        _ai("ai_copilot", ai_cfg["copilot_status"] == 1),
        _item("night_mode", _flag(night.get("status")) == 1, "on" if _flag(night.get("status")) == 1 else "off",
              window=f"{night.get('start', '22:00')}–{night.get('end', '06:00')}"),
    ]
    if log_health is not None:
        configured = log_health.get("reason") != "not_configured"
        state = "on" if log_health.get("ok") else ("fault" if configured else "off")
        items.append(_item("log_channel", configured, state, log_health.get("reason") if configured else None))
    return {
        "chat_id": str(cid),
        "items": items,
        "summary": {"effective": sum(1 for i in items if i["effective"]), "total": len(items)},
        "sentinel": {"active": sentinel_up, "mode": sentinel.get("mode", "none")},
        "ai": {"mode": ai_cfg["response_mode"], "chance": ai_cfg["response_chance"], "tone": ai_cfg["personality_tone"],
               "online": bool(ai_online), "licensed": ultra},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


PERIMETER_LABELS = {
    "es": {"captcha": "Captcha", "autolower": "AutoLower", "screen_shield": "Escudo Antinota",
           "raid_shield": "Escudo Anti-Raid", "ai_guardian": "Guardián IA", "ai_copilot": "Copiloto IA",
           "night_mode": "Modo Nocturno", "log_channel": "Canal de Registro"},
    "en": {"captcha": "Captcha", "autolower": "AutoLower", "screen_shield": "Screen Shield",
           "raid_shield": "Anti-Raid Shield", "ai_guardian": "AI Guardian", "ai_copilot": "AI Copilot",
           "night_mode": "Night Mode", "log_channel": "Log Channel"},
}
_STATE_ICONS = {"on": "🟢", "alert": "🟠", "standby": "🟡", "locked": "🔒", "fault": "🔴", "off": "⚪"}


def render_perimeter_text(status: dict, lang: str = "es") -> str:
    """Resumen HTML para la consola del bot (misma fuente de verdad que la Mini App)."""
    lang = "en" if lang == "en" else "es"
    labels = PERIMETER_LABELS[lang]
    title = "🛰️ <b>Estado del Perímetro</b>" if lang == "es" else "🛰️ <b>Perimeter Status</b>"
    rows = [title, ""]
    for item in status.get("items", []):
        icon = _STATE_ICONS.get(item.get("state"), "⚪")
        extra = ""
        if item["key"] == "autolower" and item.get("enabled"):
            extra = f" ({item.get('pct', 2)}%)"
        if item.get("detail") == "no_sentinel":
            extra += " — " + ("sin Centinela activo" if lang == "es" else "no active Sentinel")
        elif item.get("detail") == "no_license":
            extra += " — " + ("requiere ULTRA PRO" if lang == "es" else "requires ULTRA PRO")
        elif item.get("detail") == "ai_offline":
            extra += " — " + ("IA no disponible" if lang == "es" else "AI unavailable")
        elif item.get("detail") in ("cannot_post", "unreachable"):
            extra += " — " + ("el bot no puede publicar" if lang == "es" else "bot cannot post")
        rows.append(f"{icon} {labels.get(item['key'], item['key'])}{extra}")
    s = status.get("summary", {})
    rows += ["", (f"⚡ Activos: <b>{s.get('effective', 0)}/{s.get('total', 0)}</b>" if lang == "es"
                  else f"⚡ Active: <b>{s.get('effective', 0)}/{s.get('total', 0)}</b>")]
    return "\n".join(rows)


# ==========================================================================
# 5. SOPORTE TÉCNICO IA — «Arquitecto de Soporte»
# ==========================================================================
SUPPORT_DIRECTIVES_KEY = "support_ai_directives"
SUPPORT_DIRECTIVES_MAX = 3000
SUPPORT_HUMAN_CONTACT = "@therealonetom"
SUPPORT_MAX_INPUT = 1500
SUPPORT_USER_COOLDOWN_S = 3.0
SUPPORT_HOURLY_LIMIT = 30
SUPPORT_RATE_CACHE_MAX = 5000
_support_rate: dict = {}          # user_id → [timestamps de la última hora]
_directives_cache: dict = {}      # "v" → (texto, monotonic_ts)
DIRECTIVES_CACHE_TTL_S = 60.0


def _plan_prices() -> tuple:
    try:
        from handlers.payments import PRICE_PRO_STARS, PRICE_ULTRAPRO_STARS  # type: ignore[import-not-found]
        return int(PRICE_PRO_STARS), int(PRICE_ULTRAPRO_STARS)
    except Exception:
        return 300, 600


def support_knowledge_base() -> str:
    """Hechos verificados del sistema. La IA solo puede afirmar lo que figura aquí o en las directivas."""
    pro, ultra = _plan_prices()
    return (
        "BASE DE CONOCIMIENTOS — The Bunker OS (Cloud Media Management)\n"
        "PLANES (pago exclusivo con Telegram Stars, sin pasarelas externas):\n"
        "- BÁSICO (gratis): comandos de moderación limitados; sin automatización de radar ni soporte prioritario.\n"
        f"- PRO ({pro} ⭐): automatización total, moderación de voz y comandos ilimitados.\n"
        f"- ULTRA PRO ({ultra} ⭐): todo lo de PRO más Centinela de IA (Guardián anti-toxicidad y Copiloto), "
        "radar avanzado anti-fantasmas (Ghost Purge), Escudo Antinota, Modo Podcast, Payload Multimedia, "
        "gamificación XP y respaldo cifrado (.bunker).\n"
        "MÓDULOS:\n"
        "- Aduana: captcha alfanumérico para nuevos miembros; anti-spam y anti-flood configurables.\n"
        "- AutoLower: el Centinela baja al 1–10 % (por defecto 2 %) el micrófono de los miembros sin pase en el "
        "videochat. Requiere un Centinela MTProto activo con el permiso «Gestionar videochats».\n"
        "- MicVIP (/micvip): pase pagado con Stars que devuelve el micrófono al 100 %.\n"
        "- Centinela: cuenta MTProto que supervisa la sala de voz 24/7. Puede ser el Centinela Maestro del "
        "sistema o uno propio vinculado por teléfono en el chat privado del bot (🧬 Clon & Centinela).\n"
        "- Bot Clon: tu propio bot (token de BotFather) que opera con la misma lógica; se vincula SOLO en el "
        "chat privado del bot, nunca en la Mini App.\n"
        "- Ghost Purge: elimina cuentas eliminadas, marcadas como estafa o falsas por Telegram y userbots "
        "marcados por el radar; opcionalmente cuentas inalcanzables. Directiva: baneo permanente o expulsión suave.\n"
        "- Centinela IA: modos de respuesta Solo mención, Siempre o Probabilidad (porcentaje configurable) y "
        "tonos Guardián táctico, Copiloto ejecutivo o Anfitrión/PR.\n"
        "- Canal de registro: canal o grupo donde el bot publica auditorías; el bot debe ser administrador con "
        "permiso de publicar y el operador debe ser su creador.\n"
        "- Modo nocturno: bloqueo automático por horario.\n"
        "COMANDOS PRIVADOS CONOCIDOS: /start (consola), /login (acceso web a la Mini App), /cancel (cancelar un "
        "asistente). En grupos: /micvip, /metrics y /stats.\n"
        f"SOPORTE HUMANO: {SUPPORT_HUMAN_CONTACT}."
    )


async def get_support_directives(force: bool = False) -> str:
    cached = _directives_cache.get("v")
    if cached and not force and time.monotonic() - cached[1] < DIRECTIVES_CACHE_TTL_S:
        return cached[0]
    try:
        text = await _db().get_kv_setting(SUPPORT_DIRECTIVES_KEY, "") or ""
    except Exception as ex:
        logger.debug(f"[Soporte IA] Directivas no disponibles: {ex}")
        text = cached[0] if cached else ""
    _directives_cache["v"] = (text, time.monotonic())
    return text


async def set_support_directives(text: str, updated_by: int = None) -> str:
    clean = _CONTROL_CHARS_RE.sub("", str(text or "")).strip()[:SUPPORT_DIRECTIVES_MAX]
    await _db().set_kv_setting(SUPPORT_DIRECTIVES_KEY, clean, updated_by=updated_by)
    _directives_cache["v"] = (clean, time.monotonic())
    return clean


class SupportRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = max(1, int(round(retry_after)))
        super().__init__(f"retry_after={self.retry_after}")


def _support_check_rate(user_id: int) -> None:
    now = time.monotonic()
    history = [ts for ts in _support_rate.get(user_id, []) if now - ts < 3600]
    if history and now - history[-1] < SUPPORT_USER_COOLDOWN_S:
        raise SupportRateLimited(SUPPORT_USER_COOLDOWN_S - (now - history[-1]))
    if len(history) >= SUPPORT_HOURLY_LIMIT:
        raise SupportRateLimited(3600 - (now - history[0]))
    history.append(now)
    _bounded_put(_support_rate, user_id, history, SUPPORT_RATE_CACHE_MAX)


def _support_fallback(lang: str) -> str:
    if lang == "en":
        return (f"The AI assistant is temporarily unavailable. For plans, the Sentinel or the clone bot, "
                f"open /start → Support, or write to {SUPPORT_HUMAN_CONTACT}.")
    return (f"El asistente de IA no está disponible en este momento. Para planes, Centinela o Bot Clon abre "
            f"/start → Soporte, o escribe a {SUPPORT_HUMAN_CONTACT}.")


async def support_ai_reply(user_id: int, message: str, lang: str = "es") -> dict:
    """
    Respuesta del Arquitecto de Soporte. {'reply', 'fallback'}; lanza SupportRateLimited si el usuario envía
    demasiado. El historial se guarda en ai_chat_context con chat_id = user_id (positivo: no colisiona con los
    chats grupales del Copiloto) y SIEMPRE ya minimizado por la Capa 0.
    """
    lang = "en" if lang == "en" else "es"
    uid = int(user_id)
    text = _CONTROL_CHARS_RE.sub("", str(message or "")).strip()[:SUPPORT_MAX_INPUT]
    if not text:
        return {"reply": ("¿En qué te ayudo? Pregúntame por planes, comandos o configuración."
                          if lang == "es" else "How can I help? Ask about plans, commands or setup."), "fallback": True}
    _support_check_rate(uid)
    assistant = _assistant()
    if assistant is None or not callable(getattr(assistant, "_mistral_available", None)) or not assistant._mistral_available():
        return {"reply": _support_fallback(lang), "fallback": True}

    sanitize = assistant.sanitize_pii_for_ai
    safe_text = sanitize(text)
    directives = sanitize(await get_support_directives())
    system = (
        "Eres el Arquitecto de Soporte de The Bunker OS (Cloud Media Management). Respondes dudas sobre planes "
        "tácticos, comandos y configuración del bot y de la Mini App.\n"
        "Reglas obligatorias:\n"
        "- Usa SOLO la base de conocimientos y las directivas. Si algo no figura, dilo y deriva al soporte humano "
        f"{SUPPORT_HUMAN_CONTACT}. Nunca inventes precios, plazos, comandos ni funciones.\n"
        "- Nunca pidas ni aceptes tokens de BotFather, String Sessions, códigos de inicio de sesión, contraseñas "
        "ni datos de pago: se vinculan solo en el chat privado del bot mediante sus asistentes guiados.\n"
        "- Responde en el idioma del usuario, en texto plano (sin Markdown ni HTML), máximo 120 palabras, con "
        "pasos concretos cuando aplique.\n"
        f"- {getattr(assistant, 'PII_PLACEHOLDER_NOTE', '')}\n\n"
        f"{support_knowledge_base()}\n"
        + (f"\nDIRECTIVAS DEL ARQUITECTO:\n{directives}\n" if directives else "")
    )
    messages = [{"role": "system", "content": system}]
    try:
        history = await _db().get_ai_chat_context(uid, limit=6)
    except Exception:
        history = []
    for item in history or []:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": sanitize(str(item["content"]))[:1500]})
    messages.append({"role": "user", "content": safe_text})

    try:
        response = await assistant._mistral_complete(
            "support", model=assistant.MISTRAL_MODEL, messages=messages, max_tokens=350, temperature=0.3
        )
        reply = assistant._extract_mistral_text(response)[:3500]
    except Exception as ex:
        logger.info(f"🆘 [Soporte IA] Respuesta de respaldo para {uid}: {ex}")
        return {"reply": _support_fallback(lang), "fallback": True}
    if not reply:
        return {"reply": _support_fallback(lang), "fallback": True}
    try:
        await _db().save_ai_chat_context(uid, uid, "user", safe_text)
        await _db().save_ai_chat_context(uid, 0, "assistant", sanitize(reply))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo guardar el contexto de {uid}: {ex}")
    return {"reply": reply, "fallback": False}


async def support_ai_reset(user_id: int) -> None:
    try:
        await _db().clear_ai_chat_context(int(user_id))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo limpiar el contexto de {user_id}: {ex}")


def escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)
