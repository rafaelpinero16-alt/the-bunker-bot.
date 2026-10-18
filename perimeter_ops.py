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
                              is_creator: Optional[Callable[[int, int], Awaitable[bool]]] = None,
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


async def apply_ai_sentinel_patch(group_id: int, patch: Any) -> dict:   # validate_ai_sentinel_patch rechaza lo que no sea dict
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


def _item(key: str, enabled: bool, state: str, detail: Optional[str] = None, **extra) -> dict:
    return {"key": key, "enabled": bool(enabled), "effective": state in ("on", "alert"), "state": state,
            "detail": detail, **extra}


async def _safe(coro, default):
    try:
        return await coro
    except Exception as ex:
        logger.debug(f"[Perímetro] Lectura fallida: {ex}")
        return default


async def build_perimeter_status(chat_id: int, *, sentinel: Optional[dict] = None, raid: Optional[dict] = None, tier: Optional[str] = None,
                                 log_health: Optional[dict] = None, ai_online: Optional[bool] = None) -> dict:
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
# 5. SOPORTE TÉCNICO IA — «Arquitecto de Soporte» (v31)
# ==========================================================================
# Flujo de support_ai_reply():
#   1. detect_message_lang(): el idioma REAL del mensaje manda (un operador con la interfaz en inglés que escribe
#      «como programo el captcha» recibe la respuesta en español), con el idioma de la interfaz como desempate.
#   2. Mistral AI con la base de conocimientos enciclopédica + directivas del Arquitecto (Capa 0 aplicada).
#   3. Si Mistral no está disponible o falla (clave inválida, SDK, red, disyuntor): local_knowledge_fallback()
#      responde con la guía paso a paso del módulo detectado. Solo si la pregunta no encaja en ningún tema se usa
#      el aviso genérico. El administrador nunca se queda sin respuesta.
SUPPORT_DIRECTIVES_KEY = "support_ai_directives"
SUPPORT_DIRECTIVES_MAX = 3000
SUPPORT_HUMAN_CONTACT = "@therealonetom"
SUPPORT_MAX_INPUT = 1500
SUPPORT_USER_COOLDOWN_S = 3.0
SUPPORT_HOURLY_LIMIT = 30
SUPPORT_RATE_CACHE_MAX = 5000
SUPPORT_MAX_TOKENS = 600
SUPPORT_REPLY_MAX_CHARS = 3500
_support_rate: dict = {}          # user_id → [timestamps de la última hora]
_directives_cache: dict = {}      # "v" → (texto, monotonic_ts)
DIRECTIVES_CACHE_TTL_S = 60.0
_support_diag_logged: dict = {}   # motivo → monotonic_ts (un log por motivo cada 10 min, sin inundar Railway)


def _plan_prices() -> tuple:
    try:
        from handlers.payments import PRICE_PRO_STARS, PRICE_ULTRAPRO_STARS  # type: ignore[import-not-found]
        return int(PRICE_PRO_STARS), int(PRICE_ULTRAPRO_STARS)
    except Exception:
        return 300, 600


# --------------------------------------------------------------------------
# 5.1 Detección del idioma del mensaje
# --------------------------------------------------------------------------
_ES_WORDS = frozenset("""
como cómo que qué por porque porqué para donde dónde cuando cuándo cual cuál cuales cuanto cuánto cuesta cuestan
configuro configurar programo programar activo activar desactivar desactivo funciona funcionan puedo puede pueden
quiero necesito hago hacer tengo tiene canal canales grupo grupos precio precios estrellas botón boton ayuda
el la los las del al un una unos unas mi mis tu tus su sus es son esta está estan están hay también tambien pero
sin con desde hasta sobre entre cambiar poner quitar agregar añadir anadir instalar empezar comenzar mensaje
mensajes miembros usuario usuarios permisos pago pagar comprar renovar plan planes propinas cerraduras bienvenida
ajustes configuración configuracion panel sala micrófono microfono volumen llamada videochat cuenta cuentas
hola gracias favor ok sí si ya mas más menos error falla fallo sale aparece veo ver dónde aquí aqui eso esto
""".split())
_EN_WORDS = frozenset("""
how what why where when which who can could should would does do did is are was were the a an to of in on for
with without from into setup set configure configuring enable disable turn work works working price prices pricing
channel channels group groups help please want need make get have has my your their this that these those
change add remove install start begin message messages members user users permissions pay buy renew plan plans
tips locks welcome settings panel room microphone volume call video account accounts hello hi thanks thank yes
not more less error fails failing shows show see here there it its
""".split())
_ES_CHARS_RE = re.compile(r"[¿¡ñáéíóúü]")
_WORD_RE = re.compile(r"[a-záéíóúñü']+")


def detect_message_lang(text: str, default: str = "es") -> str:
    """
    'es' | 'en' según el texto del usuario. Señales: signos ¿ ¡, tildes y ñ (español inequívoco) y recuento de
    palabras funcionales de cada idioma. Empate o texto sin señales → `default` (idioma de la interfaz).
    """
    fallback = "en" if default == "en" else "es"
    value = str(text or "").lower()
    if not value.strip():
        return fallback
    if _ES_CHARS_RE.search(value):
        return "es"
    words = _WORD_RE.findall(value)
    es = sum(1 for w in words if w in _ES_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    if es > en:
        return "es"
    if en > es:
        return "en"
    return fallback


# --------------------------------------------------------------------------
# 5.2 Base de conocimientos enciclopédica (fuente única para la IA y el respondedor local)
# --------------------------------------------------------------------------
_SUPPORT_KB_TEMPLATE = """
BASE DE CONOCIMIENTOS OFICIAL — The Bunker Command OS v30 (Cloud Media Management)
Todo lo que sigue es verificado. Úsalo para explicar QUÉ es cada módulo, CÓMO funciona, QUÉ PLAN requiere y CÓMO se
configura paso a paso en el Bot (chat privado) y en la Mini App (Command Center).

══ 1. CÓMO EMPEZAR (onboarding) ══
Paso 1 · Añadir el bot: abrir el bot en privado → /start → «➕ Añadir a un Grupo» o «📢 Añadir a un Canal». Darle
permisos de administrador: restringir miembros, eliminar mensajes, fijar mensajes, invitar usuarios, gestionar
videochats y gestionar etiquetas (o promover miembros). En canales: publicar, editar, eliminar e invitar usuarios.
Paso 2 · Regla de Oro de propiedad: SOLO el Creador/Propietario real de la comunidad (verificado en vivo ante
Telegram) o un Delegado autorizado expresamente por el Arquitecto puede configurarla, en el Bot o en la Mini App. Los
administradores comunes NO pueden alterar el perímetro ni los ajustes avanzados.
Paso 3 · Abrir el panel:
- Bot: /start → «⚙️ Configuración de Grupos» (o «📡 Configuración de Canales») → elegir la comunidad.
- Mini App: /start → «⚡ Command Center» (en navegador web: /login) → elegir la comunidad en el selector superior o en
  «Matriz de Grupos» / «Ajustes del Bot ⚙️».
Comandos privados: /start (consola), /login (acceso web a la Mini App), /cancel (cancela un asistente en curso).
Comandos en grupo: /micvip, /speakers, /metrics, /stats, /rank, /top y los de moderación (/ban, /kick, /mute, /unmute).

══ 2. PLANES, LICENCIAS Y PAGOS (solo Telegram Stars ⭐ XTR) ══
Pagos 100 % in-app con Telegram Stars (XTR), activación instantánea, sin pasarelas externas. Cada licencia se vincula a
UNA comunidad concreta (su chat_id) y dura 30 días.
- BÁSICO / Free (0 ⭐): Aduana Captcha básica, Cerraduras multimedia (30 vectores), Anti-Spam y Anti-Flood, Refuerzo
  perimetral (ráfagas de reenvíos y bots autorizados), Escudo Antinota y hasta 3 comandos de moderación al día.
  Cupos: 1 plan activo en canales y 1 destino de propinas.
- PRO ({pro} ⭐ / 30 días): todo Free + comandos ilimitados, purga automática de mensajes de servicio, Captcha Pro con
  bienvenida personalizada ({{name}}, {{username}}, {{title}}), texto y agradecimiento personalizados en Propinas,
  anuncio recurrente de Gamificación (cada 3 h o más), Auditoría MicVIP, Apelación de Baneos por Stars y Cuotas
  diarias de enlaces para administradores. Cupos: 3 planes activos en canales y 3 destinos de propinas.
- ULTRA PRO ({ultra} ⭐ / 30 días): todo PRO + Bot Clon propio (100 % de las Stars para el dueño), Centinela de voz
  dedicado MTProto 24/7, Radar AutoLower (1 %–10 %), Roles y Etiquetas VIP automáticos, Centinela de IA (Guardián
  anti-toxicidad + Copiloto AMA), Ghost Purge v2, Modo Podcast y Audio Ducking, Cola de Speakers pagada (/speakers),
  Programador de videochats (VC Scheduler), Payload multimedia del Centinela, Bóveda de respaldo cifrada (.bunker),
  multimedia en Propinas/Advertencias/Gamificación (gamificación desde cada 30 min). Cupos: 10 planes activos en canales
  y 10 destinos de propinas.
Comprar o renovar:
- Bot: /start → «⚙️ Configuración de Grupos» → elegir comunidad → «⭐ PRO» o «💎 ULTRA» → «⭐ Pagar con Stars».
- Mini App: pestaña «Membresías & Stars» (o el botón de desbloqueo de cualquier tarjeta con candado) → «PRO (300 XTR)»
  o «ULTRA PRO (600 XTR)». La factura se emite siempre para la comunidad elegida; si no hay ninguna seleccionada, el bot
  muestra un selector de comunidades.
Si una función muestra 🔒 o una tarjeta de mejora, la comunidad no tiene el plan necesario (o la licencia caducó).

══ 3. ADUANA & BIENVENIDA — CAPTCHA PRO («🤖 Captcha Protection») ══
Cómo funciona: cuando entra un miembro nuevo, el bot lo restringe en la puerta y le envía un desafío de verificación con
cuenta regresiva. Si no lo resuelve a tiempo o falla, aplica el castigo configurado antes de que pueda enviar spam.
Modos: en la Mini App «Botón simple (Soy humano)» o «Desafío aritmético (math)»; en el Bot, modo Alfanumérico 🟢/🔴.
Parámetros: tiempo límite 30 s, 1 min, 3 min o 5 min (la Mini App también ofrece 120 s); castigo «👢 Expulsar (Kick)» o
«🔇 Silenciar (Mute)»; borrado de mensajes de servicio (uniones/salidas) 🟢/🔴; mensaje de bienvenida de hasta 1000
caracteres con variables {{name}}, {{username}} y {{title}} (personalización completa en PRO).
Paso a paso en el Bot: /start → «⚙️ Configuración de Grupos» → tu grupo → «🤖 Captcha Protection» → «🛡️ Captcha: 🟢»
para activarlo → modo → «⏱️ Tiempo» → «⚖️ Castigo» → «✍️ Mensaje» → «🗑️ Serv. Msg».
Paso a paso en la Mini App: menú ☰ → «Ajustes del Bot ⚙️» → tu comunidad → sección «Aduana & Bienvenida» → interruptor
«Aduana Captcha Pro» → «Modo de Reto» → «Tiempo Límite» → «Mensaje de Bienvenida» → «💾 Guardar».
Requisito: el bot debe ser administrador con permiso para restringir miembros y eliminar mensajes.

══ 4. CERRADURAS, MATRIZ MULTIMEDIA DE 30 VECTORES Y REFUERZO PERIMETRAL («🔒 Cerraduras») ══
Cerraduras rápidas (Bot): bloquear/permitir con un toque «🖼️ Multimedia», «🎨 Stickers y GIFs», «🔗 Enlaces Web» y
«⚙️ Comandos de Bots».
Matriz de 30 vectores (3 páginas, en Bot y Mini App):
- Página 1 Multimedia: fotos, videos, videos largos (más de 5 o 10 min), GIFs, stickers, notas de voz, audios, notas de
  video, archivos/documentos, álbumes.
- Página 2 Enlaces y reenvíos: enlaces ocultos, enlaces de Telegram, reenvíos, historias reenviadas, respuestas
  externas, botones inline, mensajes editados, encuestas, contactos, textos largos.
- Página 3 Evasión avanzada: mayúsculas excesivas, ubicación, juegos, facturas, texto spoiler, multimedia spoiler,
  sorteos, bots inline (via_bot), invitación de bots, mensajes enriquecidos.
Acción por vector: «☑️ Off» (permitido), «❕ Warn», «❗ Kick», «🔇 Mute», «🚫 Ban» y «🗑️» (borrar el mensaje; sobre Off
equivale a «solo eliminar»).
Refuerzo perimetral: límite de ráfagas de reenvíos masivos (Off, 3, 5 u 8 reenvíos en una ventana de 30 s, 60 s o
300 s) y lista de Bots Autorizados (hasta 25, p. ej. @AllowedBot) que no son bloqueados por los filtros de botones
inline, bots inline ni invitaciones de bots.
Bot: panel del grupo → «🔒 Cerraduras». Mini App: «Ajustes del Bot ⚙️» → comunidad → sección de cerraduras/matriz
multimedia y «Refuerzo perimetral». Disponible en todos los planes.

══ 5. ANTI-SPAM, ANTI-FLOOD, MODERACIÓN Y ADVERTENCIAS ══
- Anti-Spam («🛡️ Anti-Spam»): bloquea enlaces t.me, enlaces web, citas y reenvíos de forma granular (desde canales,
  usuarios, grupos o bots) con borrado automático opcional.
- Anti-Flood («🌊 Anti-Flood»): número de mensajes seguidos permitidos (3 a 50, por defecto 10) en una ventana (5 s a
  120 s, por defecto 15 s) y castigo (Off, Warn, Kick, Mute, Ban) con borrado de mensajes.
- Matriz de Moderación («🛡️ Matriz de Moderación»): ejecución remota de /ban (1 min, 10 min, 1 h, 24 h, 30 días o
  permanente), /kick, /mute, /unmute, Whitelist (inmunidad total para aliados) y Blacklist (palabras prohibidas con
  purga automática). En Free hay 3 comandos de moderación al día; en PRO+ son ilimitados.
- Advertencias («⚠️ Advertencias / Warns»): qué genera advertencia (enlaces, lista negra, flood), límite de strikes
  (p. ej. 3) y castigo al alcanzarlo (Mute, Kick, Ban). PRO+: texto de advertencia personalizado. ULTRA PRO: adjuntar
  foto/video/GIF.
Bot: panel del grupo → «🛡️ Anti-Spam», «🌊 Anti-Flood», «🛡️ Moderación» o «⚠️ Advertencias». Mini App: «Ajustes del
Bot ⚙️» → comunidad → secciones equivalentes.

══ 6. APELACIÓN DE BANEOS POR STARS («⚖️ Ban Appeals» — PRO o superior) ══
Cómo funciona: cuando un miembro es baneado AUTOMÁTICAMENTE por alcanzar el límite de advertencias, el bot le envía por
privado el motivo y una oferta para apelar pagando Stars. Al pagar se le desbanea (también se limpia la marca de baneo
global si se originó en esa comunidad) y recibe un enlace de reingreso de un solo uso con caducidad. Si el bot no puede
desbanearlo (por permisos), las Stars se reembolsan automáticamente. Los baneos manuales no son apelables.
Ajustes: activar/desactivar; precio 1–10000 ⭐ (por defecto 250 ⭐; atajos 50, 100, 250, 500, 1000, 2500); duración del
enlace 10 min, 30 min, 1 h o 24 h; vigencia de la oferta 1–30 días (por defecto 7); máximo de apelaciones pagadas por
usuario cada 90 días: 1, 3, 5, 10 o sin límite.
Bot: panel del grupo → «⚖️ Apelaciones de baneo». Mini App: «Ajustes del Bot ⚙️» → comunidad → «Ban Appeals».

══ 7. VOZ: AUTOLOWER, MICVIP, AUDITORÍA, PODCAST, ESCUDO ANTINOTA, SPEAKERS Y PROGRAMADOR ══
- Radar AutoLower (ULTRA PRO): baja automáticamente el micrófono de los participantes sin pase al 1 %–10 % (por
  defecto 2 %) para eliminar ruido. REQUISITO: un Centinela MTProto activo en el grupo, administrador con el permiso
  «Gestionar videochats». Sin Centinela, el perímetro lo muestra 🟡 «sin Centinela activo».
- Pase VIP de micrófono (/micvip): el miembro compra un pase de 24 h con Stars (precio del dueño: 25, 50, 100 o
  personalizado). Al pagar, el Centinela le devuelve el micrófono al 100 % al instante y el bot le asigna una etiqueta
  nativa de miembro (sin hacerlo administrador). A las 24 h el privilegio y la etiqueta se retiran solos.
- Auditoría MicVIP («🎟️ MicVIP Audit», PRO+): registro de solo lectura con exportación CSV de cada compra: comprador,
  fecha, expiración real, estado y la evidencia de que el Centinela aplicó el 100 %. Bot: panel del grupo →
  «🎟️ Auditoría MicVIP». Mini App: «Ajustes del Bot ⚙️» → comunidad → «MicVIP Audit».
- Modo Podcast y Audio Ducking (ULTRA PRO): cuando habla el anfitrión o un administrador, el volumen del resto baja
  suavemente al nivel de ducking (1 %–90 %, p. ej. 10, 20 o 30 %). Incluye escudo antirruido contra picos.
- Escudo Antinota («🎥 Screen-Share Shield»): corta al instante cualquier video o pantalla compartida no autorizada y
  retira al infractor de la llamada.
- Cola de Speakers pagada (/speakers, ULTRA PRO): los miembros pagan Stars para entrar en una cola prioritaria de
  palabra durante un AMA o debate.
- Programador de videochats («🗓️ VC Scheduler», ULTRA PRO): abre y cierra el videochat en el horario HH:MM-HH:MM y
  días elegidos y publica el aviso de apertura/cierre.
- Optimización audiovisual: las llamadas de más de 3,5 h se reinician preventivamente sin perder los pases VIP. Los 5
  avisos de voz (unión a la sala, MicVIP, reinicio, inicio programado y bienvenida fijada) admiten texto, botones,
  enlaces y auto-borrado personalizados.
Bot: panel del grupo → «📡 Radar y Ecosistema» / «🧰 Herramientas ULTRA». Mini App: «Ajustes del Bot ⚙️» → comunidad →
sección de control acústico.

══ 8. BOT CLON Y CENTINELA DEDICADO («🧬 Clon & Centinela» — ULTRA PRO) ══
Regla de seguridad absoluta: los tokens de @BotFather y las sesiones del Centinela NUNCA se escriben en la Mini App ni
en este chat de soporte. Se vinculan SOLO en el chat privado del bot, en «🧬 Clon & Centinela». La Mini App solo muestra
su estado (🟢 activo, 🟡 desconectado, 🔴 sin vincular) y el botón «Vincular en el bot».
Conectar un Bot Clon: 1) en @BotFather enviar /newbot y copiar el token; 2) en el bot: /start → «⚙️ Configuración de
Grupos» → comunidad → «🧬 Clon & Centinela» → «🔑 Conectar Token @BotFather» y pegar el token (se guarda cifrado);
3) añadir el bot clon al grupo como administrador. El dueño conserva el 100 % de las Stars cobradas por su clon. Las
licencias PRO/ULTRA se pagan siempre en el Bot Maestro.
Conectar un Centinela propio: 1) «🧬 Clon & Centinela» → «🎙️ Conectar Centinela Propio (Teléfono)»; 2) enviar el número
en formato internacional (+código de país y número), luego el código de 5 dígitos que envía Telegram y, si la cuenta
tiene verificación en dos pasos, la contraseña 2FA; 3) añadir esa cuenta al grupo como administrador con «Gestionar
videochats». Si la sesión se cierra desde «Dispositivos» o caduca, el sistema la marca como revocada, retira el
Centinela sin bucles y avisa por privado al dueño con un botón para volver a vincularla.

══ 9. ROLES Y ETIQUETAS VIP AUTOMÁTICOS («👑 VIP Roles & Tags» — ULTRA PRO) ══
Concede un rol VIP temporal o permanente, con etiqueta NATIVA de Telegram (máx. 16 caracteres, sin emojis; nunca hace
administrador a nadie), a quien done Stars alcanzando el mínimo (1, 25, 50, 100, 250, 500 o 1000 ⭐; por defecto 50),
compre un pase MicVIP o sea añadido manualmente por el dueño. Duración: 7, 30, 90, 365 días o permanente.
Beneficios: prioridad/inmunidad ante AutoLower, margen extra en Anti-Flood (×1 a ×5) y exenciones parciales en vectores
inofensivos (fotos, stickers, GIFs, voz…). Enlaces, reenvíos, botones inline, bots y ediciones NUNCA son eximibles, y
durante el Pánico o el modo nocturno las exenciones se suspenden.
Requisito: el bot necesita el permiso de administrador «Gestionar etiquetas».
Bot: panel del grupo → «👑 Roles VIP». Mini App: «Ajustes del Bot ⚙️» → comunidad → «VIP Roles & Tags».

══ 10. CUOTAS DIARIAS DE ENLACES POR ADMINISTRADOR («🔗 Admin Link Quotas» — PRO o superior) ══
Limita cuántas publicaciones con enlaces (y opcionalmente reenvíos de canales) puede hacer cada administrador cada 24 h
(ventana móvil). Límite general 0–100 (0 = no puede publicar enlaces) y excepciones individuales (0, N o ♾️ sin límite).
Al superar la cuota, el bot borra la publicación y avisa al administrador del tiempo que falta. El dueño nunca se limita.
Bot: panel del grupo → «🔗 Cuotas de enlaces». Mini App: «Ajustes del Bot ⚙️» → comunidad → «Admin Link Quotas».

══ 11. CANALES: SINCRONIZACIÓN, PLANES DE MEMBRESÍA Y PROPINAS ══
- Sincronizar canales: Bot → «📡 Configuración de Canales» → «🔄 Sincronizar Canales» → Telegram abre su selector
  nativo. Antes de registrar el canal, el bot verifica que es administrador con los 4 permisos críticos (Publicar,
  Editar, Eliminar e Invitar usuarios) y que tú eres el creador. Si falta algo, te dice exactamente qué permiso activar
  y ofrece «🔄 Volver a verificar».
- Planes de membresía VIP: cupos de planes activos Free 1, PRO 3, ULTRA PRO 10. Cada plan define nombre, duración
  (1–365 días), precio (1–10000 ⭐), texto promocional, multimedia opcional, enlace destino VIP y difusión recurrente
  automática (cada 6, 12, 24 o 48 h, o desactivada). Al pagar, el suscriptor recibe un enlace de un solo uso; el
  vigilante de suscripciones avisa 48 h antes del vencimiento y expulsa a quien no renueva. Bot: «📡 Configuración de
  Canales» → canal → «➕ Crear Nuevo Plan». Mini App: pestaña de canales → Estudio de Canales → Planes.
- Propinas en Stars («⭐ Propinas Stars»): donaciones voluntarias con montos rápidos (p. ej. 15, 50, 100, 250, 500 ⭐) y
  monto libre. Canales destino para difundir el panel: Free 1, PRO 3, ULTRA PRO 10 (el bot debe ser administrador con
  permiso de publicar; si un destino deja de ser válido se pausa automáticamente). PRO+: texto del anuncio y mensaje
  privado de agradecimiento personalizados (variables {{stars}}, {{mention}}, {{user_name}}, {{community}}). ULTRA PRO:
  multimedia. Bot: panel → «💰 Economía» → «⭐ Propinas Stars».

══ 12. HERRAMIENTAS AVANZADAS ══
- Centinela de IA («🤖 AI Sentinel», ULTRA PRO): Guardián anti-toxicidad (analiza los mensajes previa anonimización de
  datos personales y purga amenazas, acoso grave, estafas, phishing o doxxing) y Copiloto conversacional (responde en el
  grupo en modo «🎯 Solo mención», «🎲 Probabilidad» 1–100 % o «⚡ Siempre», con personalidad «🛡️ Guardián táctico»,
  «📝 Copiloto ejecutivo» o «🤝 Anfitrión/PR» y directivas personalizadas de hasta 1000 caracteres).
- Ghost Purge v2 («💀 Radar Anti-Fantasmas», ULTRA PRO): limpia según criterios (💀 cuentas eliminadas, 🚩 estafa,
  🎭 falsas, 🤖 userbots marcados por el radar y, opcional, 👻 inalcanzables) aplicando 🔴 baneo permanente o 🟡
  expulsión suave. Los administradores y bots nunca se purgan.
- Modo nocturno («🌙 Night Mode»): bloquea multimedia, enlaces, stickers y comandos en el horario HH:MM-HH:MM (p. ej.
  22:00-06:00) y restaura los permisos al terminar.
- Gamificación y niveles XP («🎮 Gamificación»): los miembros ganan XP al participar (/rank, /top), multiplicador 1x,
  2x, 3x o 5x y anuncio recurrente interactivo (PRO cada 3 h o más; ULTRA PRO con multimedia y desde 30 min).
- Estado del perímetro («🛰️ Perímetro»): semáforo en vivo 🟢 activo, 🟡 en espera/sin Centinela, 🟠 alerta de raid,
  🔴 fallo, 🔒 requiere licencia; distingue lo encendido de lo que realmente está operando.
- Analítica en vivo («📈 Analítica», Mini App): DAU/WAU/MAU, retención 7/30 días, Stars recaudadas, bloqueos de
  seguridad, mapa de calor 24/7, sala de audio en vivo, Top 10 XP y feed de eventos en tiempo real.
- Canal de registro («🧾 Canal de Registro»): canal o grupo donde el bot publica auditorías e informes periódicos
  (diario 24 h, cada 3 días o semanal). El bot debe ser administrador con permiso de publicar y tú su creador; debe ser
  distinto de la comunidad auditada. Bot: panel del grupo → «🧾 Canal de Registro» (envía @alias, ID -100… o reenvía un
  mensaje del canal).
- Bóveda de respaldo («🔐 .bunker», ULTRA PRO): exporta e importa toda la configuración de la comunidad en un archivo
  firmado con HMAC-SHA256 (a prueba de manipulaciones).

══ 13. PROBLEMAS FRECUENTES ══
- «Solo el propietario…» / acceso denegado: solo el creador real de la comunidad (o un delegado del Arquitecto) puede
  configurarla; los administradores comunes no.
- Una función aparece con 🔒 o tarjeta de mejora: falta el plan requerido o la licencia caducó; renueva desde el panel.
- AutoLower o MicVIP no actúan: falta un Centinela activo con «Gestionar videochats» (revisa «🛰️ Perímetro»).
- El bot no publica en un canal: falta el permiso «Publicar mensajes»; usa «🔄 Volver a verificar» tras corregirlo.
- Un pago de Stars no se acreditó: abre /start → «🆘 Soporte» y contacta al soporte humano con el ID del cargo.
SOPORTE HUMANO (solo para pagos no acreditados, petición expresa de hablar con una persona o proyectos a medida):
{human}.
""".strip()


def support_knowledge_base() -> str:
    """Hechos verificados del sistema. La IA solo puede afirmar lo que figura aquí o en las directivas."""
    pro, ultra = _plan_prices()
    return _SUPPORT_KB_TEMPLATE.format(pro=pro, ultra=ultra, human=SUPPORT_HUMAN_CONTACT)


# --------------------------------------------------------------------------
# 5.3 Respondedor local de emergencia (sin IA): guía paso a paso por tema
# --------------------------------------------------------------------------
def _fold(text: str) -> str:
    """minúsculas sin tildes (la búsqueda por palabras clave no depende de cómo escriba el usuario)."""
    table = str.maketrans("áéíóúüñ", "aeiouun")
    return str(text or "").lower().translate(table)


# (tema, patrones regex sobre texto plegado, guía ES, guía EN). El orden desempata: va primero lo más específico.
_LOCAL_TOPICS = (
    ("human", (r"hablar con (un |una )?(humano|persona|agente|asesor)", r"soporte humano", r"talk to (a )?(human|person|agent)",
               r"real person", r"human support", r"no se acredit", r"no me lleg", r"not credited", r"cobro duplicado",
               r"charged twice", r"reembolso", r"refund")),
    ("appeals", (r"apelac", r"\bappeal", r"desbane", r"\bunban")),
    ("micvip_audit", (r"auditoria", r"\baudit\b", r"\bcsv\b")),
    ("vip_roles", (r"roles? vip", r"vip roles?", r"etiquet", r"\btags?\b", r"\brol\b")),
    ("link_quotas", (r"cuota", r"\bquota", r"limite de enlaces", r"link limit")),
    ("micvip", (r"micvip", r"\bmic ?vip", r"\bpases?\b", r"\bpass(es)?\b", r"microfono al 100", r"mic(rophone)? 100")),
    ("autolower", (r"autolower", r"auto ?lower", r"volumen", r"\bvolume", r"\b2 ?%", r"ruido", r"noise", r"atenu")),
    ("podcast", (r"podcast", r"ducking", r"speakers?\b", r"oradores", r"cola de palabra")),
    ("vcsched", (r"programador", r"scheduler", r"vc ?sched", r"abrir (el )?videochat", r"horario de (la )?llamada")),
    ("screen", (r"antinota", r"pantalla compartida", r"screen ?share", r"screen shield")),
    ("clone", (r"\bclon", r"\bclone", r"botfather", r"\btoken")),
    ("sentinel", (r"centinela", r"sentinel", r"telefono", r"\bphone", r"sesion", r"session", r"mtproto", r"\b2fa\b")),
    ("ai", (r"\bia\b", r"\bai\b", r"guardian", r"copiloto", r"copilot", r"mistral", r"toxic")),
    ("ghost", (r"ghost", r"purge", r"purga", r"fantasma", r"cuentas eliminadas", r"deleted accounts")),
    ("captcha", (r"captcha", r"aduana", r"bienvenida", r"welcome", r"verificac", r"soy humano", r"i.?m human")),
    ("antiflood", (r"flood", r"inundac")),
    ("antispam", (r"spam",)),
    ("warns", (r"advertenc", r"\bwarns?\b", r"strikes?", r"blacklist", r"lista negra", r"whitelist", r"\bban\b",
               r"\bkick\b", r"\bmute\b", r"moderac", r"moderat")),
    ("locks", (r"cerradura", r"\blocks?\b", r"bloquear (fotos|videos|stickers|gifs|enlaces|links)", r"multimedia",
               r"stickers?", r"\bgifs?\b", r"vectores", r"rafaga", r"burst", r"bots? autorizados?", r"allowed bots?")),
    ("channel_plans", (r"membresia", r"membership", r"suscripc", r"subscription", r"plan(es)? (de|del) canal",
                       r"channel plans?", r"sincroniz", r"\bsync")),
    ("tips", (r"propina", r"\btips?\b", r"donac", r"donation")),
    ("log", (r"canal de registro", r"\blog\b", r"log channel", r"registro", r"telemetr", r"analitica", r"analytics",
             r"radar", r"perimetro", r"perimeter")),
    ("night", (r"nocturno", r"\bnight", r"noche")),
    ("backup", (r"backup", r"respaldo", r"\.bunker", r"\bexport(ar)?\b", r"\bimport(ar)?\b")),
    ("gamification", (r"gamific", r"\bxp\b", r"\brank\b", r"\btop\b", r"nivel(es)?", r"\blevels?\b", r"experiencia")),
    ("plans", (r"\bplan(es)?\b", r"precio", r"\bprice", r"\bpro\b", r"\bultra", r"\bstars?\b", r"estrellas", r"licencia",
               r"license", r"pagar", r"\bpay", r"comprar", r"\bbuy", r"renov", r"renew", r"suscrib", r"\bxtr\b")),
    ("start", (r"agregar", r"anadir", r"instalar", r"empezar", r"comenzar", r"\badd\b", r"install", r"get started",
               r"\bstart\b", r"como funciona", r"how does it work", r"mini ?app", r"command center", r"\blogin")),
)

_LOCAL_GUIDES = {
    "human": (
        "🧑‍💻 Soporte humano: escribe a " + SUPPORT_HUMAN_CONTACT + " indicando tu ID de Telegram (/start → «🛠️ Mi ID y "
        "Estado»), la comunidad afectada y, si es un pago con Stars no acreditado, el ID del cargo que muestra Telegram en "
        "el recibo. Los pagos fallidos de licencias y apelaciones se reembolsan automáticamente cuando el sistema no puede "
        "entregar el servicio.",
        "🧑‍💻 Human support: write to " + SUPPORT_HUMAN_CONTACT + " with your Telegram ID (/start → “🛠️ My ID & Status”), "
        "the affected community and, for a Stars payment that was not credited, the charge ID shown on Telegram's "
        "receipt. Failed license and appeal payments are refunded automatically when the system cannot deliver the service.",
    ),
    "start": (
        "🚀 Cómo empezar con The Bunker:\n"
        "1. Abre el bot en privado y envía /start → «➕ Añadir a un Grupo» o «📢 Añadir a un Canal».\n"
        "2. Dale permisos de administrador: restringir miembros, eliminar y fijar mensajes, invitar usuarios, gestionar "
        "videochats y gestionar etiquetas (en canales: publicar, editar, eliminar e invitar).\n"
        "3. Abre el panel: Bot → /start → «⚙️ Configuración de Grupos» (o «📡 Configuración de Canales») → tu comunidad. "
        "Mini App → «⚡ Command Center» (en navegador: /login) → elige la comunidad.\n"
        "• Regla de Oro: solo el creador real de la comunidad (o un delegado del Arquitecto) puede configurarla.",
        "🚀 Getting started with The Bunker:\n"
        "1. Open the bot in private and send /start → “➕ Add to a Group” or “📢 Add to a Channel”.\n"
        "2. Make it an admin with: restrict members, delete and pin messages, invite users, manage video chats and manage "
        "tags (in channels: post, edit, delete and invite).\n"
        "3. Open the panel: Bot → /start → “⚙️ Group Settings” (or “📡 Channel Settings”) → your community. Mini App → "
        "“⚡ Command Center” (in a browser: /login) → pick the community.\n"
        "• Golden Rule: only the community's real creator (or an Architect-approved delegate) can configure it.",
    ),
    "plans": (
        "⭐ Planes (pago solo con Telegram Stars, activación inmediata, 30 días por comunidad):\n"
        "• Free (0 ⭐): Captcha básico, cerraduras de 30 vectores, Anti-Spam/Anti-Flood, refuerzo perimetral, Escudo "
        "Antinota y 3 comandos de moderación al día.\n"
        "• PRO ({pro} ⭐): comandos ilimitados, Captcha Pro con bienvenida personalizada, Auditoría MicVIP, Apelación de "
        "baneos, Cuotas de enlaces, propinas personalizadas; 3 planes de canal y 3 destinos de propinas.\n"
        "• ULTRA PRO ({ultra} ⭐): todo PRO + Bot Clon, Centinela propio, AutoLower, Roles VIP, IA, Ghost Purge, Podcast, "
        "/speakers, Programador VC y respaldo .bunker; 10 planes y 10 destinos.\n"
        "Comprar: Bot → «⚙️ Configuración de Grupos» → comunidad → «⭐ PRO» o «💎 ULTRA» → «⭐ Pagar con Stars». Mini App → "
        "«Membresías & Stars» o el botón de desbloqueo de cualquier tarjeta con 🔒.",
        "⭐ Plans (Telegram Stars only, instant activation, 30 days per community):\n"
        "• Free (0 ⭐): basic Captcha, 30-vector locks, Anti-Spam/Anti-Flood, perimeter hardening, Screen Shield and 3 "
        "moderation commands per day.\n"
        "• PRO ({pro} ⭐): unlimited commands, Captcha Pro with custom welcome, MicVIP Audit, Ban Appeals, Admin Link "
        "Quotas, custom tips; 3 channel plans and 3 tip targets.\n"
        "• ULTRA PRO ({ultra} ⭐): everything in PRO + Clone Bot, own Sentinel, AutoLower, VIP Roles, AI, Ghost Purge, "
        "Podcast, /speakers, VC Scheduler and .bunker backups; 10 plans and 10 targets.\n"
        "Buy: Bot → “⚙️ Group Settings” → community → “⭐ PRO” or “💎 ULTRA” → “⭐ Pay with Stars”. Mini App → “Memberships & "
        "Stars” or the unlock button on any 🔒 card.",
    ),
    "captcha": (
        "🤖 Captcha Pro (Aduana): restringe a cada miembro nuevo y le envía un desafío con cuenta regresiva; si no lo "
        "resuelve, aplica el castigo.\n"
        "En el Bot:\n"
        "1. /start → «⚙️ Configuración de Grupos» → tu grupo → «🤖 Captcha Protection».\n"
        "2. Pulsa «🛡️ Captcha» hasta verlo en 🟢 y elige el modo (alfanumérico 🟢/🔴).\n"
        "3. «⏱️ Tiempo»: 30 s, 1, 3 o 5 min · «⚖️ Castigo»: Expulsar o Silenciar.\n"
        "4. «✍️ Mensaje»: bienvenida de hasta 1000 caracteres con {{name}}, {{username}} y {{title}} · «🗑️ Serv. Msg» borra "
        "los avisos de entrada/salida.\n"
        "En la Mini App: ☰ → «Ajustes del Bot ⚙️» → comunidad → «Aduana & Bienvenida» → activa «Aduana Captcha Pro», elige "
        "«Modo de Reto» (botón o aritmético), «Tiempo Límite» y mensaje → «💾 Guardar».\n"
        "• El bot necesita permiso para restringir miembros y eliminar mensajes.",
        "🤖 Captcha Pro (Gatekeeper): restricts every new member and sends a timed challenge; if they fail, the "
        "punishment is applied.\n"
        "In the Bot:\n"
        "1. /start → “⚙️ Group Settings” → your group → “🤖 Captcha Protection”.\n"
        "2. Tap “🛡️ Captcha” until it shows 🟢 and choose the mode (alphanumeric 🟢/🔴).\n"
        "3. “⏱️ Time”: 30 s, 1, 3 or 5 min · “⚖️ Punishment”: Kick or Mute.\n"
        "4. “✍️ Custom Text”: welcome of up to 1000 characters with {{name}}, {{username}} and {{title}} · “🗑️ Service Msg” "
        "deletes join/leave notices.\n"
        "In the Mini App: ☰ → “Bot Settings ⚙️” → community → “Gatekeeper & Welcome” → enable “Captcha Pro”, pick the "
        "challenge mode (button or math), time limit and message → “💾 Save”.\n"
        "• The bot needs the restrict-members and delete-messages rights.",
    ),
    "locks": (
        "🔒 Cerraduras y matriz multimedia:\n"
        "1. Bot → panel del grupo → «🔒 Cerraduras»: bloqueo rápido de Multimedia, Stickers/GIFs, Enlaces y Comandos.\n"
        "2. Matriz de 30 vectores en 3 páginas (multimedia; enlaces y reenvíos; evasión avanzada). Para cada vector elige "
        "Off, Warn, Kick, Mute o Ban y activa 🗑️ si quieres borrar el mensaje (sobre Off = solo eliminar).\n"
        "3. Refuerzo perimetral: límite de ráfagas de reenvíos (Off, 3, 5 u 8 en 30 s, 60 s o 300 s) y hasta 25 bots "
        "autorizados (@AllowedBot) que no se bloquean.\n"
        "Mini App: «Ajustes del Bot ⚙️» → comunidad → matriz multimedia y «Refuerzo perimetral». Disponible en todos los planes.",
        "🔒 Locks and media matrix:\n"
        "1. Bot → group panel → “🔒 Locks”: quick locks for Media, Stickers/GIFs, Links and Commands.\n"
        "2. 30-vector matrix over 3 pages (media; links and forwards; advanced evasion). For each vector pick Off, Warn, "
        "Kick, Mute or Ban and enable 🗑️ to delete the message (on Off = delete only).\n"
        "3. Perimeter hardening: forward-burst limit (Off, 3, 5 or 8 within 30 s, 60 s or 300 s) and up to 25 allowed "
        "bots (@AllowedBot) that are never blocked.\n"
        "Mini App: “Bot Settings ⚙️” → community → media matrix and “Perimeter hardening”. Available on every plan.",
    ),
    "antispam": (
        "🛡️ Anti-Spam: Bot → panel del grupo → «🛡️ Anti-Spam». Activa por separado el bloqueo de enlaces t.me, enlaces "
        "web, citas y reenvíos (desde canales, usuarios, grupos o bots) y decide si se borran automáticamente. En la Mini "
        "App está en «Ajustes del Bot ⚙️» → comunidad. Combínalo con «⚠️ Advertencias» para sancionar a los reincidentes.",
        "🛡️ Anti-Spam: Bot → group panel → “🛡️ Anti-Spam”. Toggle t.me links, web links, quotes and forwards (from "
        "channels, users, groups or bots) separately and choose whether they are auto-deleted. In the Mini App it lives in "
        "“Bot Settings ⚙️” → community. Combine it with “⚠️ Warnings” to punish repeat offenders.",
    ),
    "antiflood": (
        "🌊 Anti-Flood: Bot → panel del grupo → «🌊 Anti-Flood».\n"
        "1. Mensajes permitidos seguidos: 3 a 50 (por defecto 10).\n"
        "2. Ventana de tiempo: 5 s a 120 s (por defecto 15 s).\n"
        "3. Castigo: Off, Warn, Kick, Mute o Ban, con borrado de los mensajes del flood.\n"
        "Los VIP pueden tener margen extra (×1 a ×5) si usas Roles VIP.",
        "🌊 Anti-Flood: Bot → group panel → “🌊 Anti-Flood”.\n"
        "1. Allowed consecutive messages: 3 to 50 (default 10).\n"
        "2. Time window: 5 s to 120 s (default 15 s).\n"
        "3. Punishment: Off, Warn, Kick, Mute or Ban, deleting the flood messages.\n"
        "VIPs can get extra margin (×1 to ×5) with VIP Roles.",
    ),
    "warns": (
        "⚠️ Moderación y advertencias:\n"
        "• «🛡️ Matriz de Moderación»: /ban (1 min, 10 min, 1 h, 24 h, 30 días o permanente), /kick, /mute, /unmute, "
        "Whitelist (inmunidad) y Blacklist (palabras prohibidas con borrado automático). Free: 3 comandos al día; PRO+: "
        "ilimitados.\n"
        "• «⚠️ Advertencias»: elige qué genera advertencia (enlaces, lista negra, flood), el límite de strikes (p. ej. 3) y "
        "el castigo final (Mute, Kick o Ban). PRO+: texto personalizado; ULTRA PRO: foto/video/GIF.\n"
        "Ruta: Bot → /start → «⚙️ Configuración de Grupos» → tu grupo → «🛡️ Moderación» o «⚠️ Advertencias».",
        "⚠️ Moderation and warnings:\n"
        "• “🛡️ Moderation Matrix”: /ban (1 min, 10 min, 1 h, 24 h, 30 days or permanent), /kick, /mute, /unmute, "
        "Whitelist (immunity) and Blacklist (banned words auto-deleted). Free: 3 commands per day; PRO+: unlimited.\n"
        "• “⚠️ Warnings”: choose what triggers a warning (links, blacklist, flood), the strike limit (e.g. 3) and the final "
        "punishment (Mute, Kick or Ban). PRO+: custom text; ULTRA PRO: photo/video/GIF.\n"
        "Path: Bot → /start → “⚙️ Group Settings” → your group → “🛡️ Moderation” or “⚠️ Warnings”.",
    ),
    "appeals": (
        "⚖️ Apelación de baneos por Stars (PRO o superior): cuando alguien es baneado AUTOMÁTICAMENTE por el límite de "
        "advertencias, recibe por privado el motivo y una factura. Al pagar se le desbanea y recibe un enlace de reingreso "
        "de un solo uso; si el bot no puede desbanearlo, se reembolsa solo.\n"
        "Configurar: Bot → panel del grupo → «⚖️ Apelaciones de baneo» (Mini App: «Ban Appeals»):\n"
        "1. Activa el módulo.\n2. Precio: 1–10000 ⭐ (por defecto 250).\n3. Duración del enlace: 10 min, 30 min, 1 h o 24 h.\n"
        "4. Vigencia de la oferta: 1–30 días.\n5. Máximo por usuario cada 90 días: 1, 3, 5, 10 o sin límite.",
        "⚖️ Paid ban appeals (PRO or higher): when someone is banned AUTOMATICALLY by the warning limit, they get the "
        "reason and an invoice in private. On payment they are unbanned and receive a single-use rejoin link; if the bot "
        "cannot unban them, the Stars are refunded automatically.\n"
        "Setup: Bot → group panel → “⚖️ Ban appeals” (Mini App: “Ban Appeals”):\n"
        "1. Enable the module.\n2. Price: 1–10000 ⭐ (default 250).\n3. Link lifetime: 10 min, 30 min, 1 h or 24 h.\n"
        "4. Offer validity: 1–30 days.\n5. Max per user every 90 days: 1, 3, 5, 10 or unlimited.",
    ),
    "autolower": (
        "🔊 Radar AutoLower (ULTRA PRO): baja el micrófono de quien no tiene pase al 1 %–10 % (por defecto 2 %).\n"
        "1. Requisito: un Centinela activo en el grupo, administrador con «Gestionar videochats» (vincúlalo en «🧬 Clon & "
        "Centinela» o usa el Centinela Maestro).\n"
        "2. Bot → panel del grupo → «📡 Radar y Ecosistema» → AutoLower 🟢 y elige el porcentaje.\n"
        "3. Comprueba en «🛰️ Perímetro» que aparece 🟢 (🟡 = falta el Centinela).\n"
        "• Los miembros con /micvip o rol VIP quedan al 100 %.",
        "🔊 AutoLower radar (ULTRA PRO): lowers the mic of anyone without a pass to 1%–10% (default 2%).\n"
        "1. Requirement: an active Sentinel in the group, admin with “Manage video chats” (link it in “🧬 Clone & "
        "Sentinel” or use the Master Sentinel).\n"
        "2. Bot → group panel → “📡 Radar & Ecosystem” → AutoLower 🟢 and choose the percentage.\n"
        "3. Check “🛰️ Perimeter” shows 🟢 (🟡 = no Sentinel).\n"
        "• Members with /micvip or a VIP role stay at 100%.",
    ),
    "micvip": (
        "🎙️ MicVIP: los miembros envían /micvip en el grupo y compran un pase de 24 h con Stars (precio del dueño: 25, 50, "
        "100 o personalizado). Al pagar, el Centinela les devuelve el micrófono al 100 % y el bot les pone una etiqueta "
        "nativa de miembro (sin hacerlos admin). A las 24 h todo se retira solo.\n"
        "Configurar: Bot → panel del grupo → «📡 Radar y Ecosistema» → precio y etiqueta del MicVIP. Requiere un Centinela "
        "activo con «Gestionar videochats». La «🎟️ Auditoría MicVIP» (PRO+) muestra cada compra y su evidencia.",
        "🎙️ MicVIP: members send /micvip in the group and buy a 24 h pass with Stars (owner's price: 25, 50, 100 or "
        "custom). On payment the Sentinel restores their mic to 100% and the bot gives them a native member tag (never "
        "admin). After 24 h everything is removed automatically.\n"
        "Setup: Bot → group panel → “📡 Radar & Ecosystem” → MicVIP price and tag. Needs an active Sentinel with “Manage "
        "video chats”. “🎟️ MicVIP Audit” (PRO+) shows every purchase and its evidence.",
    ),
    "micvip_audit": (
        "🎟️ Auditoría MicVIP (PRO o superior): registro de solo lectura de cada pase: comprador, fecha, expiración real, "
        "estado (🟢/🔴) y la evidencia de que el Centinela aplicó el 100 %.\n"
        "• Bot: panel del grupo → «🎟️ Auditoría MicVIP» (filtros Todos / Activos / Expirados / Incidencias).\n"
        "• Mini App: «Ajustes del Bot ⚙️» → comunidad → «MicVIP Audit», con exportación CSV.",
        "🎟️ MicVIP Audit (PRO or higher): read-only log of every pass: buyer, date, real expiry, status (🟢/🔴) and the "
        "evidence that the Sentinel applied 100%.\n"
        "• Bot: group panel → “🎟️ MicVIP Audit” (filters All / Active / Expired / Issues).\n"
        "• Mini App: “Bot Settings ⚙️” → community → “MicVIP Audit”, with CSV export.",
    ),
    "podcast": (
        "🎧 Modo Podcast y Speakers (ULTRA PRO):\n"
        "• Audio Ducking: cuando habla el anfitrión o un admin, el resto baja al nivel elegido (1 %–90 %, p. ej. 20 %); "
        "incluye escudo antirruido.\n"
        "• /speakers: cola prioritaria de palabra pagada con Stars para AMAs y debates.\n"
        "Ruta: Bot → panel del grupo → «🧰 Herramientas ULTRA». Requiere un Centinela activo con «Gestionar videochats».",
        "🎧 Podcast mode and Speakers (ULTRA PRO):\n"
        "• Audio ducking: when the host or an admin speaks, everyone else drops to the chosen level (1%–90%, e.g. 20%); "
        "includes an anti-noise shield.\n"
        "• /speakers: priority speaking queue paid with Stars for AMAs and debates.\n"
        "Path: Bot → group panel → “🧰 ULTRA Tools”. Needs an active Sentinel with “Manage video chats”.",
    ),
    "vcsched": (
        "🗓️ Programador de videochats (ULTRA PRO): abre y cierra el videochat automáticamente.\n"
        "1. Bot → panel del grupo → «💰 Economía» / «🧰 Herramientas ULTRA» → «🗓️ Programador VC».\n"
        "2. Define el horario HH:MM-HH:MM y los días.\n3. Actívalo: el bot publica el aviso de apertura y cierre.\n"
        "Requiere un Centinela activo con «Gestionar videochats».",
        "🗓️ Video chat scheduler (ULTRA PRO): opens and closes the video chat automatically.\n"
        "1. Bot → group panel → “💰 Economy” / “🧰 ULTRA Tools” → “🗓️ VC Scheduler”.\n"
        "2. Set the HH:MM-HH:MM window and the days.\n3. Enable it: the bot posts the opening and closing notice.\n"
        "Needs an active Sentinel with “Manage video chats”.",
    ),
    "screen": (
        "🎥 Escudo Antinota: corta al instante cualquier video o pantalla compartida no autorizada en el videochat y "
        "retira al infractor. Actívalo en Bot → panel del grupo → «📡 Radar y Ecosistema» → Escudo Antinota 🟢. Funciona con "
        "un Centinela activo con «Gestionar videochats».",
        "🎥 Screen-Share Shield: instantly cuts any unauthorized video or screen share in the video chat and removes the "
        "offender. Enable it in Bot → group panel → “📡 Radar & Ecosystem” → Screen Shield 🟢. Works with an active Sentinel "
        "with “Manage video chats”.",
    ),
    "clone": (
        "🧬 Bot Clon (ULTRA PRO): tu propio bot con toda la lógica del Búnker y el 100 % de las Stars para ti.\n"
        "1. En @BotFather envía /newbot y copia el token.\n"
        "2. En el chat privado del bot: /start → «⚙️ Configuración de Grupos» → comunidad → «🧬 Clon & Centinela» → "
        "«🔑 Conectar Token @BotFather» y pega el token (se guarda cifrado).\n"
        "3. Añade el clon a tu grupo como administrador.\n"
        "🔐 Nunca pegues el token en la Mini App ni en este chat de soporte: solo en «🧬 Clon & Centinela».",
        "🧬 Clone Bot (ULTRA PRO): your own bot with all of The Bunker's logic and 100% of the Stars for you.\n"
        "1. In @BotFather send /newbot and copy the token.\n"
        "2. In the bot's private chat: /start → “⚙️ Group Settings” → community → “🧬 Clone & Sentinel” → “🔑 Connect "
        "@BotFather Token” and paste the token (stored encrypted).\n"
        "3. Add the clone to your group as an admin.\n"
        "🔐 Never paste the token in the Mini App or in this support chat: only in “🧬 Clone & Sentinel”.",
    ),
    "sentinel": (
        "🎙️ Centinela propio (ULTRA PRO): una cuenta de Telegram que supervisa tu sala de voz 24/7.\n"
        "1. Bot → /start → «⚙️ Configuración de Grupos» → comunidad → «🧬 Clon & Centinela» → «🎙️ Conectar Centinela "
        "Propio (Teléfono)».\n"
        "2. Envía el número en formato internacional (+código de país), después el código de 5 dígitos de Telegram y, si "
        "tienes verificación en dos pasos, la contraseña 2FA.\n"
        "3. Añade esa cuenta al grupo como administrador con «Gestionar videochats».\n"
        "• Si la sesión se cierra desde «Dispositivos», el sistema la marca como revocada y te avisa por privado con un "
        "botón para volver a vincularla. 🔐 Nunca envíes códigos ni contraseñas en este chat de soporte.",
        "🎙️ Own Sentinel (ULTRA PRO): a Telegram account that watches your voice room 24/7.\n"
        "1. Bot → /start → “⚙️ Group Settings” → community → “🧬 Clone & Sentinel” → “🎙️ Connect Own Sentinel (Phone)”.\n"
        "2. Send the number in international format (+country code), then Telegram's 5-digit code and, if you use "
        "two-step verification, your 2FA password.\n"
        "3. Add that account to the group as an admin with “Manage video chats”.\n"
        "• If the session is closed from “Devices”, the system marks it revoked and DMs you a button to relink it. "
        "🔐 Never send codes or passwords in this support chat.",
    ),
    "ai": (
        "🤖 Centinela de IA (ULTRA PRO):\n"
        "• Guardián anti-toxicidad: analiza los mensajes (sin datos personales) y purga amenazas, acoso grave, estafas, "
        "phishing o doxxing.\n"
        "• Copiloto: responde en el grupo en modo «🎯 Solo mención», «🎲 Probabilidad» (1–100 %) o «⚡ Siempre», con "
        "personalidad Guardián táctico, Copiloto ejecutivo o Anfitrión/PR y tus directivas (hasta 1000 caracteres).\n"
        "Ruta: Bot → panel del grupo → «🧰 Herramientas ULTRA» → Centinela IA; Mini App → «Ajustes del Bot ⚙️» → comunidad → "
        "«AI Sentinel». Necesita un Centinela activo.",
        "🤖 AI Sentinel (ULTRA PRO):\n"
        "• Anti-toxicity Guardian: scans messages (with personal data removed) and purges threats, severe harassment, "
        "scams, phishing or doxxing.\n"
        "• Copilot: replies in the group in “🎯 Mention only”, “🎲 Chance” (1–100%) or “⚡ Always” mode, with a Tactical "
        "guardian, Executive copilot or Host/PR personality and your directives (up to 1000 characters).\n"
        "Path: Bot → group panel → “🧰 ULTRA Tools” → AI Sentinel; Mini App → “Bot Settings ⚙️” → community → “AI "
        "Sentinel”. Needs an active Sentinel.",
    ),
    "ghost": (
        "💀 Ghost Purge v2 (ULTRA PRO): limpia cuentas fantasma.\n"
        "1. Bot → panel del grupo → «🧰 Herramientas ULTRA» → «💀 Ghost Purge» (Mini App: «Ajustes del Bot ⚙️» → «Ghost Purge»).\n"
        "2. Elige criterios: 💀 eliminadas, 🚩 estafa, 🎭 falsas, 🤖 userbots marcados y, opcional, 👻 inalcanzables.\n"
        "3. Elige la directiva: 🔴 baneo permanente o 🟡 expulsión suave.\n"
        "4. Ejecuta el escaneo: recibirás el informe (también en el canal de registro). Admins y bots nunca se purgan.",
        "💀 Ghost Purge v2 (ULTRA PRO): cleans ghost accounts.\n"
        "1. Bot → group panel → “🧰 ULTRA Tools” → “💀 Ghost Purge” (Mini App: “Bot Settings ⚙️” → “Ghost Purge”).\n"
        "2. Pick criteria: 💀 deleted, 🚩 scam, 🎭 fake, 🤖 flagged userbots and, optionally, 👻 unreachable.\n"
        "3. Pick the directive: 🔴 permanent ban or 🟡 soft kick.\n"
        "4. Run the scan: you get the report (also in the log channel). Admins and bots are never purged.",
    ),
    "vip_roles": (
        "👑 Roles y etiquetas VIP (ULTRA PRO): rol VIP automático con etiqueta nativa de Telegram (máx. 16 caracteres, sin "
        "emoji, nunca admin) para quien done Stars (mínimo 1–1000 ⭐, por defecto 50), compre MicVIP o añadas a mano.\n"
        "1. Bot → panel del grupo → «👑 Roles VIP» (Mini App: «VIP Roles & Tags») → actívalo.\n"
        "2. Escribe la etiqueta, el mínimo de Stars y la duración (7, 30, 90, 365 días o permanente).\n"
        "3. Beneficios: prioridad AutoLower, margen Anti-Flood ×1–×5 y exenciones parciales (fotos, stickers, GIFs, voz…).\n"
        "4. «➕ Conceder VIP» para añadir a alguien por ID.\n"
        "• El bot necesita el permiso «Gestionar etiquetas».",
        "👑 VIP roles & tags (ULTRA PRO): automatic VIP role with a native Telegram tag (max 16 characters, no emoji, "
        "never admin) for whoever donates Stars (minimum 1–1000 ⭐, default 50), buys MicVIP or is added by you.\n"
        "1. Bot → group panel → “👑 VIP Roles” (Mini App: “VIP Roles & Tags”) → enable it.\n"
        "2. Set the tag, the minimum Stars and the duration (7, 30, 90, 365 days or permanent).\n"
        "3. Perks: AutoLower priority, Anti-Flood margin ×1–×5 and partial exemptions (photos, stickers, GIFs, voice…).\n"
        "4. “➕ Grant VIP” to add someone by ID.\n"
        "• The bot needs the “Manage tags” right.",
    ),
    "link_quotas": (
        "🔗 Cuotas diarias de enlaces (PRO o superior): limita cuántas publicaciones con enlaces hace cada administrador en "
        "24 h (ventana móvil).\n"
        "1. Bot → panel del grupo → «🔗 Cuotas de enlaces» (Mini App: «Admin Link Quotas») → actívalo.\n"
        "2. Límite general con ➖/➕ (0–100; 0 = sin enlaces).\n"
        "3. Excepciones por administrador: 0, N o ♾️ ilimitado.\n"
        "4. Decide si cuentan los reenvíos de canales (📥).\n"
        "Al superar la cuota, el bot borra la publicación y avisa del tiempo restante. El dueño nunca se limita.",
        "🔗 Daily admin link quotas (PRO or higher): limits how many link posts each admin can make in 24 h (rolling "
        "window).\n"
        "1. Bot → group panel → “🔗 Link quotas” (Mini App: “Admin Link Quotas”) → enable it.\n"
        "2. Default limit with ➖/➕ (0–100; 0 = no links).\n"
        "3. Per-admin exceptions: 0, N or ♾️ unlimited.\n"
        "4. Choose whether channel forwards count (📥).\n"
        "Over quota, the bot deletes the post and tells the admin the time left. The owner is never limited.",
    ),
    "channel_plans": (
        "📡 Canales y planes de membresía:\n"
        "1. Sincroniza: Bot → «📡 Configuración de Canales» → «🔄 Sincronizar Canales» → elige tu canal. El bot verifica que "
        "tiene Publicar, Editar, Eliminar e Invitar usuarios; si falta uno, te lo indica y ofrece «🔄 Volver a verificar».\n"
        "2. Crea el plan: canal → «➕ Crear Nuevo Plan» → nombre, duración (1–365 días), precio (1–10000 ⭐), texto, "
        "multimedia opcional, enlace VIP y difusión automática (6, 12, 24 o 48 h).\n"
        "3. Cupos: Free 1, PRO 3, ULTRA PRO 10 planes activos.\n"
        "• Cada comprador recibe un enlace de un solo uso; se avisa 48 h antes del vencimiento y se expulsa a quien no renueva.",
        "📡 Channels and membership plans:\n"
        "1. Sync: Bot → “📡 Channel Settings” → “🔄 Sync Channels” → pick your channel. The bot checks it has Post, Edit, "
        "Delete and Invite users; if one is missing it tells you and offers “🔄 Check again”.\n"
        "2. Create the plan: channel → “➕ Create New Plan” → name, duration (1–365 days), price (1–10000 ⭐), text, optional "
        "media, VIP link and automatic broadcast (6, 12, 24 or 48 h).\n"
        "3. Slots: Free 1, PRO 3, ULTRA PRO 10 active plans.\n"
        "• Each buyer gets a single-use link; a reminder goes out 48 h before expiry and non-renewers are removed.",
    ),
    "tips": (
        "⭐ Propinas en Stars:\n"
        "1. Bot → panel del grupo → «💰 Economía» → «⭐ Propinas Stars» → actívalo.\n"
        "2. Ajusta los montos rápidos (p. ej. 15, 50, 100, 250, 500 ⭐); también hay monto libre.\n"
        "3. «➕ Añadir canal destino»: envía @canal o su ID; el bot debe ser admin con permiso de publicar (cupos: Free 1, "
        "PRO 3, ULTRA PRO 10). Un destino que falla se pausa solo.\n"
        "4. PRO+: texto del anuncio y agradecimiento privado ({{stars}}, {{mention}}, {{user_name}}, {{community}}). ULTRA PRO: "
        "multimedia.\n5. «📡 Publicar» para difundir el panel.",
        "⭐ Stars tips:\n"
        "1. Bot → group panel → “💰 Economy” → “⭐ Stars Tips” → enable it.\n"
        "2. Set the quick amounts (e.g. 15, 50, 100, 250, 500 ⭐); a free amount is also available.\n"
        "3. “➕ Add target channel”: send @channel or its ID; the bot must be an admin allowed to post (slots: Free 1, PRO 3, "
        "ULTRA PRO 10). A failing target is paused automatically.\n"
        "4. PRO+: custom announcement and private thank-you ({{stars}}, {{mention}}, {{user_name}}, {{community}}). ULTRA "
        "PRO: media.\n5. “📡 Broadcast” to publish the panel.",
    ),
    "log": (
        "🧾 Canal de registro, perímetro y analítica:\n"
        "• Canal de registro: Bot → panel del grupo → «🧾 Canal de Registro» → envía el @alias, el ID -100… o reenvía un "
        "mensaje del canal. Debes ser su creador, el bot debe poder publicar y debe ser distinto de la comunidad. Informes "
        "automáticos: diario, cada 3 días o semanal.\n"
        "• «🛰️ Perímetro»: semáforo en vivo (🟢 activo, 🟡 en espera, 🟠 raid, 🔴 fallo, 🔒 requiere licencia).\n"
        "• «📈 Analítica» (Mini App): DAU/WAU/MAU, retención, Stars, mapa de calor, sala de audio y Top XP en tiempo real.",
        "🧾 Log channel, perimeter and analytics:\n"
        "• Log channel: Bot → group panel → “🧾 Log Channel” → send the @alias, the -100… ID or forward a message from the "
        "channel. You must be its creator, the bot must be able to post and it must differ from the community. Automatic "
        "reports: daily, every 3 days or weekly.\n"
        "• “🛰️ Perimeter”: live status light (🟢 on, 🟡 standby, 🟠 raid, 🔴 fault, 🔒 needs a license).\n"
        "• “📈 Analytics” (Mini App): DAU/WAU/MAU, retention, Stars, heatmap, audio room and Top XP in real time.",
    ),
    "night": (
        "🌙 Modo nocturno: bloquea multimedia, enlaces, stickers y comandos en un horario y restaura los permisos al "
        "terminar.\n1. Bot → panel del grupo → «🌙 Modo Nocturno».\n2. Define el horario HH:MM-HH:MM (p. ej. 22:00-06:00).\n"
        "3. Actívalo 🟢. Durante la noche las exenciones VIP se suspenden.",
        "🌙 Night mode: locks media, links, stickers and commands during a time window and restores permissions "
        "afterwards.\n1. Bot → group panel → “🌙 Night Mode”.\n2. Set the HH:MM-HH:MM window (e.g. 22:00-06:00).\n"
        "3. Turn it 🟢. VIP exemptions are suspended at night.",
    ),
    "backup": (
        "🔐 Respaldo .bunker (ULTRA PRO): exporta toda la configuración de la comunidad a un archivo firmado con "
        "HMAC-SHA256 y vuelve a importarla cuando quieras (cualquier manipulación invalida el archivo).\n"
        "Ruta: Bot → panel del grupo → «🧰 Herramientas ULTRA» → «🔐 Respaldo» → Exportar, o Importar y enviar el archivo .bunker.",
        "🔐 .bunker backup (ULTRA PRO): exports the whole community configuration to an HMAC-SHA256-signed file and imports "
        "it back whenever you want (any tampering invalidates the file).\n"
        "Path: Bot → group panel → “🧰 ULTRA Tools” → “🔐 Backup” → Export, or Import and send the .bunker file.",
    ),
    "gamification": (
        "🎮 Gamificación y XP: los miembros ganan XP al participar y suben de nivel; consultan /rank y /top.\n"
        "1. Bot → panel del grupo → «💰 Economía» → «🎮 Gamificación».\n2. Multiplicador de XP: 1x, 2x, 3x o 5x.\n"
        "3. Anuncio recurrente interactivo: PRO cada 3 h o más; ULTRA PRO con multimedia y desde 30 min.",
        "🎮 Gamification and XP: members earn XP by participating and level up; they check /rank and /top.\n"
        "1. Bot → group panel → “💰 Economy” → “🎮 Gamification”.\n2. XP multiplier: 1x, 2x, 3x or 5x.\n"
        "3. Interactive recurring card: PRO every 3 h or more; ULTRA PRO with media and from 30 min.",
    ),
}


def local_knowledge_fallback(query: str, lang: str) -> Optional[str]:
    """
    Guía paso a paso del tema que mejor encaja con la consulta (palabras clave, sin IA) en `lang`, o None si la
    consulta no corresponde a ningún módulo conocido. Si dos temas empatan en puntuación gana el más específico.
    """
    folded = _fold(query)
    if not folded.strip():
        return None
    best, best_score = None, 0
    for topic, patterns in _LOCAL_TOPICS:
        score = sum(1 for p in patterns if re.search(p, folded))
        if score > best_score:
            best, best_score = topic, score
    if not best:
        return None
    pro, ultra = _plan_prices()
    es, en = _LOCAL_GUIDES[best]
    text = (en if lang == "en" else es).replace("{{", "{").replace("}}", "}")
    return text.replace("{pro}", str(pro)).replace("{ultra}", str(ultra))


def _support_fallback(lang: str) -> str:
    """Último recurso: la consulta no encaja en ningún tema y la IA no está disponible."""
    if lang == "en":
        return ("The AI assistant is temporarily unavailable, but I can still guide you. Ask me about a specific module, "
                "for example: captcha, locks, anti-spam, anti-flood, warnings, plans and prices, MicVIP, AutoLower, clone "
                "bot, Sentinel, channel plans, tips, VIP roles, ban appeals, link quotas, night mode, gamification, log "
                f"channel or backups. For a Stars payment that was not credited, contact {SUPPORT_HUMAN_CONTACT}.")
    return ("El asistente de IA no está disponible en este momento, pero puedo guiarte igualmente. Pregúntame por un "
            "módulo concreto, por ejemplo: captcha, cerraduras, anti-spam, anti-flood, advertencias, planes y precios, "
            "MicVIP, AutoLower, bot clon, Centinela, planes de canal, propinas, roles VIP, apelaciones, cuotas de "
            "enlaces, modo nocturno, gamificación, canal de registro o respaldos. Si un pago con Stars no se acreditó, "
            f"escribe a {SUPPORT_HUMAN_CONTACT}.")


def _log_support_degraded(reason: str, detail: str = "") -> None:
    """Log visible en Railway (warning) del motivo por el que el Soporte IA no usó Mistral; uno por motivo cada 10 min."""
    now = time.monotonic()
    if now - _support_diag_logged.get(reason, 0.0) < 600:
        return
    _bounded_put(_support_diag_logged, reason, now, 50)
    logger.warning(f"🆘 [Soporte IA] Sin Mistral → respondedor local · motivo={reason} {detail}".rstrip())


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


async def set_support_directives(text: str, updated_by: Optional[int] = None) -> str:
    clean = _CONTROL_CHARS_RE.sub("", str(text or "")).strip()[:SUPPORT_DIRECTIVES_MAX]
    await _db().set_kv_setting(SUPPORT_DIRECTIVES_KEY, clean, updated_by=updated_by)
    _directives_cache["v"] = (clean, time.monotonic())
    return clean


class SupportRateLimited(Exception):
    def __init__(self, retry_after: float, lang: str = "es"):
        self.retry_after = max(1, int(round(retry_after)))
        self.lang = "en" if lang == "en" else "es"
        super().__init__(f"retry_after={self.retry_after}")


def _support_check_rate(user_id: int, lang: str = "es") -> None:
    now = time.monotonic()
    history = [ts for ts in _support_rate.get(user_id, []) if now - ts < 3600]
    if history and now - history[-1] < SUPPORT_USER_COOLDOWN_S:
        raise SupportRateLimited(SUPPORT_USER_COOLDOWN_S - (now - history[-1]), lang)
    if len(history) >= SUPPORT_HOURLY_LIMIT:
        raise SupportRateLimited(3600 - (now - history[0]), lang)
    history.append(now)
    _bounded_put(_support_rate, user_id, history, SUPPORT_RATE_CACHE_MAX)


def _support_system_prompt(effective_lang: str, directives: str, pii_note: str) -> str:
    language = "ESPAÑOL" if effective_lang == "es" else "INGLÉS (ENGLISH)"
    return (
        "Eres el Arquitecto de Soporte IA oficial de The Bunker Command OS (Cloud Media Management). "
        "Tu misión es ayudar a administradores, creadores y usuarios explicando con claridad, precisión y "
        "paso a paso cómo funciona cada módulo del bot y de la Mini App y cómo configurarlo.\n\n"
        "REGLAS OBLIGATORIAS DE COMPORTAMIENTO:\n"
        f"1. IDIOMA OBLIGATORIO: El usuario te está escribiendo en {language}. Responde ÚNICA Y EXCLUSIVAMENTE en "
        f"{language}, sin importar el idioma del menú ni de los mensajes anteriores."
        + (" Respond MANDATORILY in ENGLISH." if effective_lang == "en" else "") + "\n"
        "2. PROHIBIDO DERIVAR INNECESARIAMENTE A SOPORTE HUMANO: Tienes toda la documentación oficial del sistema abajo. "
        "Cuando te pregunten cómo funciona o cómo se programa cualquier función (Captcha, AutoLower, MicVIP, Cerraduras, "
        "Anti-Spam, Anti-Flood, Bot Clon, Centinela, Planes de Canal, Propinas, Ghost Purge, Roles VIP, Apelaciones, "
        "Cuotas de enlaces, Modo Nocturno, Gamificación, Telemetría, Canal de Registro o Backups), RESPONDE TÚ MISMO "
        "con una explicación clara y los pasos exactos para configurarlo en el Bot y en la Mini App. "
        f"SOLO menciona a {SUPPORT_HUMAN_CONTACT} si el usuario pide expresamente hablar con un humano, reporta un pago de "
        "Stars no acreditado o solicita una arquitectura empresarial a medida.\n"
        "3. FORMATO LIMPIO Y DIDÁCTICO: Responde en texto plano limpio (sin etiquetas HTML ni bloques Markdown, sin ** ni "
        "#), usando viñetas simples (• o 1., 2., 3.) y emojis tácticos moderados. Sé directo, amigable y práctico (entre "
        "60 y 220 palabras según la complejidad de la pregunta). Cita los botones tal como aparecen entre comillas «».\n"
        "4. FIDELIDAD ESTRICTA: Usa solo los precios, límites, permisos y rutas de la base de conocimientos y directivas. "
        "Nunca inventes precios, comandos ni funciones que no existan. Si algo no figura, dilo con honestidad y ofrece "
        "la ruta más cercana que sí exista.\n"
        "5. SEGURIDAD DE CREDENCIALES: Nunca pidas ni aceptes tokens de @BotFather, String Sessions, códigos SMS ni "
        "contraseñas 2FA en este chat; recuerda siempre que se vinculan únicamente en el menú privado «🧬 Clon & "
        "Centinela» del bot.\n"
        f"6. {pii_note}\n\n"
        f"{support_knowledge_base()}\n"
        + (f"\nDIRECTIVAS ADICIONALES DEL ARQUITECTO:\n{directives}\n" if directives else "")
    )


def _local_reply(text: str, lang: str, reason: str, detail: str = "") -> dict:
    _log_support_degraded(reason, detail)
    guide = local_knowledge_fallback(text, lang)
    return {"reply": guide or _support_fallback(lang), "fallback": True,
            "source": "local_kb" if guide else "generic", "lang": lang}


async def support_ai_reply(user_id: int, message: str, lang: str = "es") -> dict:
    """
    Respuesta del Arquitecto de Soporte → {'reply', 'fallback', 'source', 'lang'}.
      • lang = idioma de la interfaz; el idioma EFECTIVO se detecta del propio mensaje (detect_message_lang).
      • source: 'mistral' | 'local_kb' (guía local por tema) | 'generic' (sin tema reconocible).
    Lanza SupportRateLimited (con .lang efectivo) si el usuario envía demasiado. El historial se guarda en
    ai_chat_context con chat_id = user_id (positivo: no colisiona con los chats del Copiloto) y SIEMPRE ya minimizado
    por la Capa 0.
    """
    ui_lang = "en" if lang == "en" else "es"
    uid = int(user_id)
    text = _CONTROL_CHARS_RE.sub("", str(message or "")).strip()[:SUPPORT_MAX_INPUT]
    effective_lang = detect_message_lang(text, default=ui_lang)
    if not text:
        return {"reply": ("¿En qué te ayudo? Pregúntame por planes, comandos o la configuración de cualquier módulo."
                          if effective_lang == "es" else
                          "How can I help? Ask me about plans, commands or how to set up any module."),
                "fallback": True, "source": "generic", "lang": effective_lang}
    _support_check_rate(uid, effective_lang)

    assistant = _assistant()
    if assistant is None:
        return _local_reply(text, effective_lang, "assistant_module_unavailable",
                            "(no se pudo importar assistant.py; revisa el log de arranque)")
    if getattr(assistant, "mistral_client", None) is None:
        return _local_reply(text, effective_lang, "mistral_not_configured",
                            f"({getattr(assistant, 'MISTRAL_INIT_ERROR', '') or 'sin cliente'})")
    available = getattr(assistant, "_mistral_available", None)
    try:
        is_up = bool(available("support")) if callable(available) else False
    except TypeError:   # assistant.py anterior a v31 (sin disyuntor por propósito)
        is_up = bool(available()) if callable(available) else False
    if not is_up:
        return _local_reply(text, effective_lang, "breaker_open", "(fallos recientes de Mistral para 'support')")

    sanitize = getattr(assistant, "sanitize_pii_for_ai", None) or (lambda value: value)
    safe_text = sanitize(text)
    directives = sanitize(await get_support_directives())
    system = _support_system_prompt(effective_lang, directives, getattr(assistant, "PII_PLACEHOLDER_NOTE", ""))
    messages = [{"role": "system", "content": system}]
    try:
        history = await _db().get_ai_chat_context(uid, limit=6)
    except Exception:
        history = []
    for item in history or []:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": sanitize(str(item["content"]))[:1500]})
    # Mistral exige que el último turno sea del usuario y alterna roles: se descartan restos inconsistentes.
    while len(messages) > 1 and messages[-1]["role"] == "user":
        messages.pop()
    messages.append({"role": "user", "content": safe_text})

    timeout = max(float(getattr(assistant, "MISTRAL_TIMEOUT_SECONDS", 25.0) or 25.0), 30.0)
    try:
        response = await assistant._mistral_complete(
            "support", timeout=timeout, model=getattr(assistant, "MISTRAL_MODEL", "mistral-small-latest"),
            messages=messages, max_tokens=SUPPORT_MAX_TOKENS, temperature=0.3,
        )
        reply = assistant._extract_mistral_text(response)
    except Exception as ex:
        describe = getattr(assistant, "describe_mistral_error", None)
        detail = describe(ex) if callable(describe) else f"{type(ex).__name__}: {ex}"
        logger.warning(f"🆘 [Soporte IA] Mistral falló para {uid}: {detail}")
        return _local_reply(text, effective_lang, "mistral_call_failed", f"({detail[:160]})")
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", reply or "").replace("**", "").strip()[:SUPPORT_REPLY_MAX_CHARS]
    if not reply:
        return _local_reply(text, effective_lang, "mistral_empty_reply")
    try:
        await _db().save_ai_chat_context(uid, uid, "user", safe_text)
        await _db().save_ai_chat_context(uid, 0, "assistant", sanitize(reply))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo guardar el contexto de {uid}: {ex}")
    return {"reply": reply, "fallback": False, "source": "mistral", "lang": effective_lang}


async def support_ai_reset(user_id: int) -> None:
    try:
        await _db().clear_ai_chat_context(int(user_id))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo limpiar el contexto de {user_id}: {ex}")


def escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)