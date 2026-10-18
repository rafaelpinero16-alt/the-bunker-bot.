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
# 5. SOPORTE TÉCNICO IA — «Arquitecto de Soporte» (v31 · v32: intención + enciclopedia de 4 dimensiones)
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
SUPPORT_MAX_TOKENS = 850
SUPPORT_REPLY_MAX_CHARS = 3900
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
Todo lo que sigue es verificado contra el código del bot. Úsalo para explicar QUÉ es cada módulo, CUÁLES son sus
funciones y CÓMO opera, QUÉ PLAN requiere y QUÉ beneficios aporta, y CÓMO se configura paso a paso en el Bot (chat
privado) y en la Mini App (Command Center). La parte «ENCICLOPEDIA DE MÓDULOS» documenta cada módulo en 4 bloques:
[QUÉ ES / DEFINICIÓN] · [FUNCIONES Y CÓMO OPERA] · [PLAN Y BENEFICIOS] · [CÓMO SE CONFIGURA EN BOT Y MINI APP].

══ A. ARQUITECTURA GENERAL ══
The Bunker Command OS es un sistema de seguridad, control acústico, monetización y analítica para comunidades de
Telegram (grupos, supergrupos y canales). Tiene cuatro piezas:
1. Bot Maestro (Bot API): modera el chat, cobra en Telegram Stars, envía los avisos y aloja la consola privada.
2. Centinela MTProto: una cuenta de usuario real (Pyrogram) que entra a los videochats; puede ser el Centinela Maestro
   del ecosistema o un Centinela Dedicado Propio del dueño (ULTRA PRO). Un bot de BotFather NO puede entrar a llamadas
   ni regular micrófonos individuales: por eso existe el Centinela.
3. Mini App «⚡ Command Center»: panel web dentro de Telegram (o en navegador con /login) con analítica en vivo,
   ajustes de la comunidad, estudio de canales y pagos.
4. Bot Clon (opcional, ULTRA PRO): el bot propio del dueño (token de @BotFather) con la misma lógica y el 100 % de
   las Stars para él.
Áreas: Seguridad perimetral · Control acústico 24/7 · Monetización con Stars · Automatización e IA · Analítica.

══ B. REGLA DE ORO DE PROPIEDAD Y SEGURIDAD ══
- Solo el CREADOR/propietario real de la comunidad (verificado en vivo ante Telegram) o un Delegado autorizado por el
  Arquitecto puede configurar la comunidad, en el Bot o en la Mini App. Los administradores comunes no pueden alterar
  el perímetro; si lo intentan ven «⛔ Solo el propietario…».
- Los Delegados solo operan el módulo que el Arquitecto les activó (captcha, cerraduras, anti-spam, difusión,
  analítica o centinela); nunca planes, pagos, clon, sesiones ni respaldos.
- Tokens de @BotFather, sesiones del Centinela, códigos SMS y contraseñas 2FA se introducen ÚNICAMENTE en el chat
  privado del bot, en «🧬 Clon & Centinela». La Mini App jamás los pide ni los acepta; el Soporte IA tampoco.
- Los textos que pasan por IA se anonimizan antes (Capa 0: teléfonos, correos, enlaces privados y datos personales se
  sustituyen por marcadores).

══ C. MAPA DE MENÚS DEL BOT (rutas exactas) ══
/start (menú principal): «➕ Añadir a un Grupo» · «📢 Añadir a un Canal» · «⚙️ Configuración de Grupos» ·
«📡 Configuración de Canales» · «⚡ Command Center» · «🛠️ Mi ID y Estado» · «🆘 Soporte» (incluye el Arquitecto de
Soporte IA) · «ℹ️ Información» · «📖 ¿Cómo funciona el Búnker?».
Panel del grupo («⚙️ Configuración de Grupos» → elegir grupo):
- «⭐ PRO» / «💎 ULTRA» (comprar o renovar la licencia de ESA comunidad)
- «🛡️ Matriz de Moderación» (🚫 /ban, 👢 /kick, 🔇 /mute, 🔊 /unmute, «➕ Registrar Usuario en BD» = Whitelist,
  «➕ Registrar Término en BD» = Blacklist)
- «📡 Radar y Ecosistema» (📡 Radar Ecosistema, 🎥 /cams, ⚙️ /autolower, 🗓️ Programador VC, 🎙️ /mic_vip,
  🏷️ Etiqueta VIP, ⭐ Propinas Stars, 🌙 Modo Nocturno Autónomo, 🎮 Anuncio de Gamificación)
- «🛡️ Anti-Spam» · «🌊 Anti-Flood» · «🤖 Captcha Protection» · «🔒 Cerraduras» · «⚠️ Advertencias» ·
  «🗑️ Borrar Mensajes» (Centro de purga de mensajes)
- «🧬 Clon & Centinela» (🔑 Conectar Token @BotFather · 🎙️ Conectar Centinela Propio (Teléfono) · 🛑 Desconectar)
- «🛰️ Perímetro» · «🧾 Canal de Registro»
- «🎟️ Auditoría MicVIP» · «👑 Roles VIP» · «🔗 Cuotas de enlaces» · «⚖️ Apelaciones de baneo»
- «💎 Herramientas ULTRA» (🤖 Centinela de IA (ULTRA) · ⚙️ Configuración del Centinela · 🎮 Gamificación & Niveles XP ·
  🔐 Respaldo Criptográfico (.bunker) · 🚨 Botón de Pánico · 🎥 Escudo Antinota · 🎙️ Modo Podcast ·
  🌟 Gestión de Speakers · 💎 Payload Multimedia · 💀 Ghost Purge (Cuentas Fantasma))
- botón de idioma 🇺🇸/🇪🇸 y «🔙 Volver a Grupos».
Panel del canal («📡 Configuración de Canales» → canal): «🔄 Sincronizar Canales», planes de membresía
(«➕ Crear Nuevo Plan»), difusión, propinas y «💎 Herramientas ULTRA».
MAPA DE BOTONES ES ↔ EN (cítalos en el idioma de la respuesta): ⚙️ Configuración de Grupos = ⚙️ Group Settings ·
📡 Configuración de Canales = 📡 Channel Settings · 🛡️ Matriz de Moderación = 🛡️ Moderation Matrix ·
📡 Radar y Ecosistema = 📡 Radar & Ecosystem · 🔒 Cerraduras = 🔒 Locks · ⚠️ Advertencias = ⚠️ Warns ·
🗑️ Borrar Mensajes = 🗑️ Delete Messages · 🧬 Clon & Centinela = 🧬 Clone & Sentinel · 💎 Herramientas ULTRA =
💎 ULTRA Elite Tools · ⭐ Propinas Stars = ⭐ Stars Tips & Donations · 🌙 Modo Nocturno Autónomo = 🌙 Autonomous Night
Mode · 🎮 Gamificación & Niveles XP = 🎮 Gamification & XP Levels · 🔐 Respaldo Criptográfico (.bunker) =
🔐 Cryptographic Backup (.bunker) · 🤖 Centinela de IA (ULTRA) = 🤖 AI Sentinel (ULTRA) · ⚙️ Configuración del
Centinela = ⚙️ Sentinel Settings · 🚨 Botón de Pánico = 🚨 Panic Button · 🎙️ Modo Podcast = 🎙️ Podcast Mode ·
🌟 Gestión de Speakers = 🌟 Speakers Management · 🎟️ Auditoría MicVIP = 🎟️ MicVIP Audit · 👑 Roles VIP = 👑 VIP Roles ·
🔗 Cuotas de enlaces = 🔗 Link quotas · ⚖️ Apelaciones de baneo = ⚖️ Ban appeals · 🛰️ Perímetro = 🛰️ Perimeter ·
🧾 Canal de Registro = 🧾 Log Channel · 🔄 Sincronizar Canales = 🔄 Sync Channels · ➕ Crear Nuevo Plan = ➕ Create New
Plan · 🛠️ Mi ID y Estado = 🛠️ My ID & Status · 🆘 Soporte = 🆘 Support.

══ D. MAPA DE LA MINI APP (Command Center) ══
Menú lateral ☰: «Telemetría & Stats» (resumen global) · «Analítica en Vivo» · «Estudio de Canales» (ajustes del canal,
Generador de Planes VIP, difusión automática, planes activos) · «Matriz de Grupos» / «Ajustes del Bot ⚙️» (ajustes de
la comunidad) · «Auditor Suscripciones» (aviso de suscripciones) · «Membresías & Stars» (comprar PRO/ULTRA PRO) ·
«Red de Afiliados» · «Arquitecto de Soporte IA» · «Instrucciones & Manual Oficial».
Dentro de «Ajustes del Bot ⚙️» (tras elegir la comunidad): «Estado del Perímetro» · «Programación del Bot» con las
secciones «Aduana & Bienvenida», «Acústica & Videochat», «Propinas & Monetización», «Perímetro & Filtros» y «Bloqueo
Multimedia» (30 vectores) · «MicVIP Audit» · «Perimeter Hardening» · «VIP Roles & Tags» · «Ban Appeals» · «Admin Link
Quotas» · «Herramientas Ultra & Clones MTProto» (estado de Bot Clon y Centinela MTProto, solo lectura, con el botón
«Vincular en el bot») · «Centinela de IA» · «Radar Anti-Fantasmas» · «Canal de Registro» e «Informe de Rendimiento de
Admins». Cada sección se guarda con «💾 Guardar» y los cambios se sincronizan en vivo con el bot. Las funciones de pago
sin licencia muestran una tarjeta con candado y los botones «Activar PRO ⭐» / «Activar ULTRA PRO 💎».

══ E. PLANES — COMPARATIVA EXHAUSTIVA (solo Telegram Stars ⭐ XTR, 30 días por comunidad) ══
Pagos 100 % in-app con Telegram Stars, activación instantánea, sin tarjetas ni pasarelas externas. Cada licencia se
vincula a UNA comunidad (su chat_id); al caducar vuelve sola a Free. Si se compra desde un Bot Clon, el cobro se
redirige al Bot Maestro (las licencias nunca descuentan Stars del clon).
- BÁSICO / Free (0 ⭐) — Seguridad base: Aduana Captcha básica, Cerraduras rápidas y matriz multimedia de 30 vectores,
  Anti-Spam, Anti-Flood, Advertencias, Refuerzo perimetral (ráfagas de reenvíos + bots autorizados), Escudo Antinota,
  Modo nocturno, Estado del perímetro, Canal de registro, MicVIP básico y Propinas con mensaje por defecto. Límites:
  3 comandos de moderación al día, 1 plan activo por canal y 1 canal destino de propinas.
- PRO ({pro} ⭐ / 30 días) — Free + Control y profesionalización: comandos de moderación ilimitados, purga automática
  de mensajes de servicio, Captcha Pro con bienvenida personalizada ({{name}}, {{username}}, {{title}}), texto de
  advertencia personalizado, texto y agradecimiento personalizados en Propinas, anuncio recurrente de Gamificación
  (cada 3 h o más), 🎟️ Auditoría MicVIP, ⚖️ Apelación de baneos por Stars y 🔗 Cuotas diarias de enlaces para admins.
  Límites: 3 planes activos por canal y 3 destinos de propinas.
- ULTRA PRO ({ultra} ⭐ / 30 días) — PRO + Voz, IA y marca propia: 🧬 Bot Clon (100 % de las Stars para el dueño),
  🎙️ Centinela Dedicado Propio 24/7, Radar AutoLower (1 %–10 %), 👑 Roles y Etiquetas VIP automáticos, 🤖 Centinela de IA
  (Guardián + Copiloto), 💀 Ghost Purge v2, 🎙️ Modo Podcast y Audio Ducking, 🌟 Cola de Speakers pagada (/speakers),
  🗓️ Programador de videochats, 💎 Payload multimedia del Centinela, 🚨 Botón de Pánico, 🔐 Respaldo .bunker,
  multimedia en Propinas, Advertencias, avisos del Centinela y Gamificación (desde cada 30 min). Límites: 10 planes
  activos por canal y 10 destinos de propinas.
Qué se gana al subir: Free → PRO = profesionaliza la moderación (sin límites diarios), personaliza la imagen y abre dos
fuentes de ingresos (apelaciones pagadas y MicVIP auditado) con control de spam de los propios admins. PRO → ULTRA PRO
= control total de la voz (Centinela propio, AutoLower, Podcast, Speakers, Programador), IA en el chat, limpieza de
cuentas fantasma, marca propia con Bot Clon quedándose el 100 % de las Stars y respaldo cifrado.
Comprar o renovar: Bot → «⚙️ Configuración de Grupos» → comunidad → «⭐ PRO» o «💎 ULTRA» → «⭐ Pagar con Stars».
Mini App → «Membresías & Stars» (o el botón de cualquier tarjeta con 🔒). Si no hay comunidad elegida, el bot muestra
un selector de comunidades antes de emitir la factura.

══ F. MENSAJES DE SERVICIO QUE ENVÍA EL SISTEMA (qué significan) ══
- Captcha: al entrar un miembro, desafío con cuenta regresiva; al superarlo, bienvenida (personalizable en PRO).
- Advertencia: «⚠️ … (n/límite)» al infringir un filtro; al llegar al límite se aplica el castigo configurado.
- Apelación: DM al baneado automático con el motivo y la factura; tras pagar, «desbaneado» + enlace de un solo uso.
- MicVIP: anuncio de inicio del videochat con el volumen por defecto (2 %) y los botones para comprar /micvip;
  confirmación al pagar.
- Escudo Antinota: «🎥 Escudo Antinota Activado»: se cortó una transmisión de pantalla no autorizada y se retiró al
  infractor. Escudo Antirruido: «🔇 Escudo Antirruido Activado»: alguien fue silenciado por picos de ruido repetidos.
- Reinicio preventivo: aviso de mantenimiento cuando la llamada supera 3,5 h; la sala se reabre en segundos.
- Programador VC: avisos de apertura y cierre automáticos de la sala. Bienvenida fijada del videochat.
- Cuota de enlaces: aviso al admin cuyo enlace se borró, con el tiempo restante para recuperar cupo.
- Ghost Purge: informe con analizados, detectados, purgados y la directiva aplicada.
- Planes de canal: enlace de acceso de un solo uso al pagar y recordatorio 48 h antes del vencimiento.
- Propinas: panel con montos rápidos y agradecimiento privado al donante (personalizable en PRO).
- Sesión revocada: DM al dueño «🔒 Centinela desconectado: sesión revocada» con el botón «🧬 Vincular nueva sesión».
- Sincronización de canales: «🛠️ Autodiagnóstico de permisos» con la lista exacta de permisos que faltan.
- Botón de pánico: «🚨 BLOQUEO DE EMERGENCIA ACTIVO» / «🟢 Bloqueo levantado».
- Muro de pago: tarjeta «🔒 PRO ⭐ / ULTRA PRO 💎» con los botones de activación de esa comunidad.

══ G. PROBLEMAS FRECUENTES Y SOLUCIONES ══
- «Solo el propietario…» / acceso denegado: solo el creador real (o un delegado del Arquitecto) puede configurar.
- Función con 🔒 o tarjeta de mejora: falta el plan requerido o la licencia caducó; renueva desde el panel.
- AutoLower, MicVIP, Podcast o Escudo Antinota no actúan: falta un Centinela activo con «Gestionar videochats»
  (revisa «🛰️ Perímetro»: 🟡 = sin Centinela).
- El bot no publica en un canal o destino: falta «Publicar mensajes»; corrígelo y usa «🔄 Volver a verificar».
- El Centinela aparece desconectado/revocado: la sesión se cerró desde «Dispositivos»; vuelve a vincularla en
  «🧬 Clon & Centinela».
- El captcha no restringe: el bot necesita «Restringir miembros» y «Eliminar mensajes».
- Las etiquetas VIP no aparecen: el bot necesita «Gestionar etiquetas».
- Un pago con Stars no se acreditó: soporte humano con el ID del cargo (las entregas fallidas se reembolsan solas).
SOPORTE HUMANO (solo para pagos no acreditados, petición expresa de hablar con una persona o proyectos a medida):
{human}.
""".strip()


def support_knowledge_base() -> str:
    """
    Base de conocimientos completa para Mistral: parte global (_SUPPORT_KB_TEMPLATE) + ENCICLOPEDIA DE MÓDULOS
    generada desde el mismo catálogo que usa el respondedor local (_LOCAL_GUIDES), en 4 bloques por módulo. Una sola
    fuente de verdad: la IA y el modo sin IA nunca se contradicen.
    """
    pro, ultra = _plan_prices()
    head = _SUPPORT_KB_TEMPLATE.format(pro=pro, ultra=ultra, human=SUPPORT_HUMAN_CONTACT)
    blocks = ["", "══ H. ENCICLOPEDIA DE MÓDULOS (4 bloques por módulo) ══"]
    for topic in _KB_MODULE_ORDER:
        guide = _LOCAL_GUIDES.get(topic)
        if not guide:
            continue
        blocks.append(f"\n▶ {guide['title'][0]}")
        for section, label in _KB_SECTION_LABELS:
            blocks.append(f"[{label}]\n{_render_guide_text(guide[section][0], pro, ultra)}")
    return head + "\n".join(blocks)


def _render_guide_text(text: str, pro: int, ultra: int) -> str:
    return str(text).replace("{pro}", str(pro)).replace("{ultra}", str(ultra))


# --------------------------------------------------------------------------
# 5.3 Respondedor local de emergencia (sin IA): guía por tema e INTENCIÓN
# --------------------------------------------------------------------------
def _fold(text: str) -> str:
    """minúsculas sin tildes (la búsqueda por palabras clave no depende de cómo escriba el usuario)."""
    table = str.maketrans("áéíóúüñ", "aeiouun")
    return str(text or "").lower().translate(table)


# ---- Clasificador de intención -------------------------------------------------------------------------------
QUERY_INTENTS = ("concept_and_functions", "concept", "functions", "benefits_and_plans", "setup", "full")
_INTENT_SETUP_RE = re.compile(
    r"\bcomo (lo |la |los |las |le |se )?(programo|programa|configuro|configura|activo|activa|conecto|conecta|vinculo|"
    r"vincula|instalo|instala|pongo|habilito|habilita|desactivo|desactiva|apago|enciendo|cambio|creo|crea|agrego|anado|"
    r"uso|utilizo|edito|ajusto|personalizo|compro|pago|renuevo|sincronizo|reconecto)\b"
    r"|\b(programar|configurar|activar|desactivar|conectar|vincular|instalar|habilitar|ajustar|personalizar|reconectar)lo\b"
    r"|\bpara (programar|configurar|activar|conectar|vincular|instalar|habilitar)\b"
    r"|\bpaso a paso\b|\bdonde (esta|estan|encuentro|se encuentra|lo encuentro|configuro)\b|\bque boton\b|\bruta\b"
    r"|\bhow (do|can|should) (i|we|you) (set ?up|configure|enable|disable|turn on|turn off|connect|link|install|"
    r"activate|use|create|add|change|customi[sz]e|buy|renew|sync)\b"
    r"|\bhow to (set ?up|configure|enable|disable|turn on|turn off|connect|link|install|activate|use|create|add|change|"
    r"customi[sz]e|buy|renew|sync)\b"
    r"|\bset ?up\b|\bsetup\b|\bstep[- ]by[- ]step\b|\bwhere (is|are|do i find|can i find)\b|\bwhich button\b"
)
_INTENT_CONCEPT_RE = re.compile(
    r"\bque (es|son|significa|quiere decir)\b|\bdefinicion\b|\ben que consiste\b|\bque viene siendo\b"
    r"|\bwhat (is|s)\b|\bwhat'?s\b|\bwhat are\b(?! (the |its |their )?(functions?|features?|benefits?|advantages?))"
    r"|\bmeaning of\b|\bdefine\b|\bdefinition\b"
)
_INTENT_FUNCTIONS_RE = re.compile(
    r"\bfunciones\b|\bfuncionalidades\b|\bque hace\b|\bque hacen\b|\bcomo funciona\b|\bcomo funcionan\b|\bcomo opera\b"
    r"|\bpara que sirve\b|\bpara que sirven\b|\bque controla\b|\bque bloquea\b|\bque incluye\b|\bque puede hacer\b"
    r"|\bcapacidades\b|\bcaracteristicas\b|\bque detecta\b|\bque protege\b"
    r"|\bwhat does\b.*\bdo\b|\bwhat do(es)?\b.*\bdo\b|\bhow does\b.*\bwork\b|\bhow do\b.*\bwork\b|\bhow it works\b"
    r"|\bfunctions?\b|\bfeatures?\b|\bcapabilit|\bwhat can\b.*\bdo\b|\bwhat does it (include|block|control|detect)\b"
)
_INTENT_BENEFITS_RE = re.compile(
    r"\bbeneficio|\bventaja|\bdiferencia|\bcuanto (cuesta|vale|cobra|sale)\b|\bprecio|\bque plan\b|\bpor que pagar\b"
    r"|\bvale la pena\b|\bcompar|\bque incluye (el |la )?(plan|licencia|pro|ultra|basico|free)\b|\bque gano\b"
    r"|\b(plan|planes) (free|basico|pro|ultra)\b|\bfree (vs|o|y) pro\b|\bpro (vs|o|y) ultra\b|\bmejorar (a|el plan)\b"
    r"|\bbenefit|\badvantage|\bdifference|\bhow much\b|\bprices?\b|\bpricing\b|\bcost\b|\bwhich plan\b|\bworth\b"
    r"|\bwhy (should i )?(pay|upgrade)\b|\bvs\.?\b|\bupgrade\b"
)


def detect_query_intent(query: str) -> str:
    """
    Intención de la consulta (ES/EN): 'concept_and_functions' | 'concept' | 'functions' | 'benefits_and_plans' |
    'setup' | 'full'. Reglas: «cómo funciona X» = functions (NO configuración); «cómo programo/configuro/activo/conecto
    X» = setup; funciones + configuración = full; solo el nombre del módulo («captcha») = full.
    """
    folded = _fold(query)
    if not folded.strip():
        return "full"
    setup = bool(_INTENT_SETUP_RE.search(folded))
    concept = bool(_INTENT_CONCEPT_RE.search(folded))
    functions = bool(_INTENT_FUNCTIONS_RE.search(folded))
    benefits = bool(_INTENT_BENEFITS_RE.search(folded))
    if setup and (functions or concept):
        return "full"
    if setup:
        return "setup"
    if benefits and not (concept and functions):
        return "benefits_and_plans"
    if concept and functions:
        return "concept_and_functions"
    if concept:
        return "concept"
    if functions:
        return "functions"
    return "full"


# Secciones que ensambla el respondedor local según la intención.
_INTENT_SECTIONS = {
    "concept": ("what_is", "benefits"),
    "functions": ("functions", "benefits"),
    "concept_and_functions": ("what_is", "functions", "benefits"),
    "benefits_and_plans": ("benefits", "functions"),
    "setup": ("what_is_short", "setup"),
    "full": ("what_is", "functions", "setup"),
}
_KB_SECTION_LABELS = (
    ("what_is", "QUÉ ES / DEFINICIÓN"),
    ("functions", "FUNCIONES Y CÓMO OPERA"),
    ("benefits", "PLAN Y BENEFICIOS"),
    ("setup", "CÓMO SE CONFIGURA EN BOT Y MINI APP"),
)

# (tema, patrones regex sobre texto plegado). El orden desempata: va primero lo más específico.
_LOCAL_TOPICS = (
    ("human", (r"hablar con (un |una )?(humano|persona|agente|asesor)", r"soporte humano", r"talk to (a )?(human|person|agent)",
               r"real person", r"human support", r"no se acredit", r"no me lleg", r"not credited", r"cobro duplicado",
               r"charged twice", r"reembolso", r"refund")),
    ("bot_overview", (r"(que|what) (hace|does|puede hacer|can) (el |the |este |this )?(bot|bunker|sistema|system)",
                      r"funciones del (bot|bunker|sistema)", r"todas las funciones", r"(bot|bunker) (features|functions)",
                      r"que es (el |the )?(bot|bunker|the bunker|sistema)\b", r"what is (the )?(bot|bunker|the bunker)\b",
                      r"beneficios del (bot|bunker|sistema)", r"all (the )?features", r"what can (the |this )?bot do",
                      r"para que sirve (el |este )?bot", r"que ofrece", r"what does it offer", r"command os\b",
                      r"como funciona (el |the |este )?(bot|bunker|sistema)", r"how does (the |this )?(bot|bunker) work")),
    ("appeals", (r"apelac", r"\bappeal", r"desbane", r"\bunban")),
    ("micvip_audit", (r"auditoria", r"\baudit\b", r"\bcsv\b")),
    ("vip_roles", (r"roles? vip", r"vip roles?", r"etiquet", r"\btags?\b", r"\brol\b")),
    ("link_quotas", (r"cuota", r"\bquota", r"limite de enlaces", r"link limit")),
    ("panic", (r"panico", r"\bpanic", r"lockdown", r"bloqueo de emergencia", r"\braid\b", r"anti-?raid")),
    ("micvip", (r"micvip", r"\bmic ?vip", r"\bpases?\b", r"\bpass(es)?\b", r"microfono al 100", r"mic(rophone)? 100")),
    ("autolower", (r"autolower", r"auto ?lower", r"volumen", r"\bvolume", r"\b2 ?%", r"ruido", r"noise", r"atenu")),
    ("podcast", (r"podcast", r"ducking", r"speakers?\b", r"oradores", r"cola de palabra")),
    ("vcsched", (r"programador", r"scheduler", r"vc ?sched", r"abrir (el )?videochat", r"horario de (la )?llamada")),
    ("screen", (r"antinota", r"pantalla compartida", r"screen ?share", r"screen shield", r"\bcams?\b", r"camara")),
    ("payload", (r"payload", r"avisos? del centinela", r"configuracion del centinela", r"sentinel settings",
                 r"avisos? de (voz|sala)", r"vc_join", r"reset_notice", r"vc_welcome")),
    ("clone", (r"\bclon", r"\bclone", r"botfather", r"\btoken")),
    ("sentinel", (r"centinela", r"sentinel", r"telefono", r"\bphone", r"sesion", r"session", r"mtproto", r"\b2fa\b",
                  r"userbot")),
    ("ai", (r"\bia\b", r"\bai\b", r"guardian", r"copiloto", r"copilot", r"mistral", r"toxic")),
    ("ghost", (r"ghost", r"purge", r"purga", r"fantasma", r"cuentas eliminadas", r"deleted accounts")),
    ("captcha", (r"captcha", r"aduana", r"bienvenida", r"welcome", r"verificac", r"soy humano", r"i.?m human")),
    ("antiflood", (r"flood", r"inundac")),
    ("antispam", (r"spam",)),
    ("delmsgs", (r"borrar mensajes", r"delete messages", r"mensajes de servicio", r"service messages", r"limpieza de avisos")),
    ("warns", (r"advertenc", r"\bwarns?\b", r"strikes?", r"blacklist", r"lista negra", r"whitelist", r"\bban\b",
               r"\bkick\b", r"\bmute\b", r"moderac", r"moderat")),
    ("locks", (r"cerradura", r"\blocks?\b", r"bloquear (fotos|videos|stickers|gifs|enlaces|links)", r"multimedia",
               r"stickers?", r"\bgifs?\b", r"vectores", r"rafaga", r"burst", r"bots? autorizados?", r"allowed bots?",
               r"hardening", r"refuerzo")),
    ("channel_plans", (r"membresia", r"membership", r"suscripc", r"subscription", r"plan(es)? (de|del) canal",
                       r"channel plans?", r"sincroniz", r"\bsync", r"watchdog")),
    ("tips", (r"propina", r"\btips?\b", r"donac", r"donation")),
    ("log", (r"canal de registro", r"\blog\b", r"log channel", r"registro", r"telemetr", r"analitica", r"analytics",
             r"radar", r"perimetro", r"perimeter", r"informe", r"report")),
    ("night", (r"nocturno", r"\bnight", r"noche")),
    ("backup", (r"backup", r"respaldo", r"\.bunker", r"\bexport(ar)?\b", r"\bimport(ar)?\b")),
    ("gamification", (r"gamific", r"\bxp\b", r"\brank\b", r"\btop\b", r"nivel(es)?", r"\blevels?\b", r"experiencia")),
    ("plans", (r"\bplan(es)?\b", r"precio", r"\bprice", r"\bpro\b", r"\bultra", r"\bstars?\b", r"estrellas", r"licencia",
               r"license", r"pagar", r"\bpay", r"comprar", r"\bbuy", r"renov", r"renew", r"suscrib", r"\bxtr\b",
               r"beneficio", r"benefit", r"free", r"basico")),
    ("start", (r"agregar", r"anadir", r"instalar", r"empezar", r"comenzar", r"\badd\b", r"install", r"get started",
               r"\bstart\b", r"mini ?app", r"command center", r"\blogin", r"permisos del bot", r"bot permissions")),
)

# Orden de la enciclopedia que recibe Mistral.
_KB_MODULE_ORDER = (
    "bot_overview", "start", "plans", "sentinel", "captcha", "locks", "antispam", "antiflood", "warns", "delmsgs",
    "appeals", "autolower", "micvip", "micvip_audit", "podcast", "screen", "vcsched", "payload", "clone", "vip_roles",
    "link_quotas", "channel_plans", "tips", "ai", "ghost", "night", "panic", "gamification", "log", "backup", "human",
)

# ---- Catálogo de 4 dimensiones por módulo (ES, EN) -------------------------------------------------------------
# Cada tema: title · what_is · functions · benefits · setup. Es la fuente de la ENCICLOPEDIA DE MÓDULOS que recibe
# Mistral y del respondedor local. {pro} / {ultra} se sustituyen por los precios vigentes.
_LOCAL_GUIDES = {
    "bot_overview": {
        "title": ("🛡️ The Bunker Command OS — visión general", "🛡️ The Bunker Command OS — overview"),
        "what_is": (
            "🛡️ The Bunker Command OS es un sistema integral para comunidades de Telegram (grupos y canales) que une "
            "seguridad perimetral, control de la sala de voz 24/7, monetización con Telegram Stars, automatización con "
            "IA y analítica en vivo. Funciona con cuatro piezas: el Bot Maestro (modera, cobra y avisa), el Centinela "
            "MTProto (una cuenta real que entra a los videochats), la Mini App «⚡ Command Center» y, opcionalmente, tu "
            "propio Bot Clon.",
            "🛡️ The Bunker Command OS is an all-in-one system for Telegram communities (groups and channels) combining "
            "perimeter security, 24/7 voice-room control, Telegram Stars monetization, AI automation and live analytics. "
            "It runs on four pieces: the Master Bot (moderates, charges and notifies), the MTProto Sentinel (a real "
            "account that joins video chats), the “⚡ Command Center” Mini App and, optionally, your own Clone Bot.",
        ),
        "functions": (
            "Funciones por área:\n"
            "• 🔐 Seguridad perimetral: Captcha Pro, cerraduras de 30 vectores, Anti-Spam, Anti-Flood, advertencias con "
            "strikes, Matriz de Moderación (/ban, /kick, /mute, Whitelist, Blacklist), refuerzo contra ráfagas de "
            "reenvíos, bots autorizados, modo nocturno, botón de pánico y cuotas de enlaces para admins.\n"
            "• 🎙️ Control acústico 24/7: Centinela MTProto con AutoLower (1 %–10 %), pases /micvip al 100 %, Modo "
            "Podcast con ducking, Escudo Antirruido, Escudo Antinota, cola /speakers, programador de videochats y "
            "reinicio preventivo cada 3,5 h.\n"
            "• ⭐ Monetización con Stars: licencias PRO/ULTRA, MicVIP, apelaciones de baneo pagadas, planes de "
            "membresía para canales, propinas, speakers pagados y Bot Clon con el 100 % de las Stars.\n"
            "• 🤖 Automatización e IA: Guardián anti-toxicidad, Copiloto conversacional, Ghost Purge v2, roles VIP "
            "automáticos, gamificación XP y difusiones recurrentes.\n"
            "• 📈 Analítica: estado del perímetro en vivo, DAU/WAU/MAU, retención, mapa de calor 24/7, sala de audio, "
            "Top XP, canal de registro con informes y auditoría MicVIP.",
            "Features by area:\n"
            "• 🔐 Perimeter security: Captcha Pro, 30-vector locks, Anti-Spam, Anti-Flood, strike-based warnings, "
            "Moderation Matrix (/ban, /kick, /mute, Whitelist, Blacklist), forward-burst hardening, allowed bots, night "
            "mode, panic button and admin link quotas.\n"
            "• 🎙️ 24/7 voice control: MTProto Sentinel with AutoLower (1%–10%), /micvip passes at 100%, Podcast mode "
            "with ducking, Noise Shield, Screen-Share Shield, /speakers queue, video chat scheduler and a preventive "
            "restart every 3.5 h.\n"
            "• ⭐ Stars monetization: PRO/ULTRA licenses, MicVIP, paid ban appeals, channel membership plans, tips, "
            "paid speakers and a Clone Bot keeping 100% of the Stars.\n"
            "• 🤖 Automation & AI: anti-toxicity Guardian, conversational Copilot, Ghost Purge v2, automatic VIP roles, "
            "XP gamification and recurring broadcasts.\n"
            "• 📈 Analytics: live perimeter status, DAU/WAU/MAU, retention, 24/7 heatmap, audio room, Top XP, log channel "
            "with reports and MicVIP audit.",
        ),
        "benefits": (
            "Planes: Free (0 ⭐) cubre la seguridad base; PRO ({pro} ⭐/30 días) quita límites de moderación, personaliza "
            "mensajes y añade Auditoría MicVIP, Apelaciones pagadas y Cuotas de enlaces; ULTRA PRO ({ultra} ⭐/30 días) "
            "añade Centinela propio, AutoLower, Podcast, Speakers, IA, Ghost Purge, Roles VIP, Bot Clon y respaldo "
            ".bunker. Todo se paga con Telegram Stars y se activa al instante.",
            "Plans: Free (0 ⭐) covers core security; PRO ({pro} ⭐/30 days) removes moderation limits, customizes messages "
            "and adds MicVIP Audit, paid Appeals and Link Quotas; ULTRA PRO ({ultra} ⭐/30 days) adds your own Sentinel, "
            "AutoLower, Podcast, Speakers, AI, Ghost Purge, VIP Roles, Clone Bot and .bunker backups. Everything is paid "
            "with Telegram Stars and activates instantly.",
        ),
        "setup": (
            "Para empezar: /start → «➕ Añadir a un Grupo» (o «📢 Añadir a un Canal») → dale permisos de administrador → "
            "/start → «⚙️ Configuración de Grupos» → tu comunidad. En la Mini App: «⚡ Command Center» → elige la "
            "comunidad → «Ajustes del Bot ⚙️». Pregúntame por cualquier módulo y te explico sus pasos exactos.",
            "To start: /start → “➕ Add to a Group” (or “📢 Add to a Channel”) → make it an admin → /start → “⚙️ Group "
            "Settings” → your community. In the Mini App: “⚡ Command Center” → pick the community → “Bot Settings ⚙️”. "
            "Ask me about any module and I'll give you its exact steps.",
        ),
    },
    "start": {
        "title": ("🚀 Primeros pasos (onboarding)", "🚀 Getting started (onboarding)"),
        "what_is": (
            "🚀 El onboarding conecta el bot a tu comunidad y te da acceso al panel. Solo el creador real de la "
            "comunidad (o un delegado del Arquitecto) puede configurarla: es la Regla de Oro de propiedad, verificada "
            "en vivo ante Telegram.",
            "🚀 Onboarding connects the bot to your community and gives you panel access. Only the community's real "
            "creator (or an Architect-approved delegate) can configure it: that's the ownership Golden Rule, verified "
            "live with Telegram.",
        ),
        "functions": (
            "• El bot detecta las comunidades donde es administrador y las registra a tu nombre.\n"
            "• Permisos recomendados en grupos: restringir miembros, eliminar mensajes, fijar mensajes, invitar "
            "usuarios, gestionar videochats y gestionar etiquetas. En canales: publicar, editar, eliminar e invitar.\n"
            "• El panel existe por duplicado y sincronizado: consola privada del bot y Mini App «⚡ Command Center» "
            "(en navegador web con /login).\n"
            "• Los administradores comunes no pueden cambiar ajustes; los delegados solo el módulo que se les habilitó.",
            "• The bot detects the communities where it is an admin and registers them under your name.\n"
            "• Recommended group rights: restrict members, delete messages, pin messages, invite users, manage video "
            "chats and manage tags. In channels: post, edit, delete and invite.\n"
            "• The panel exists twice and in sync: the bot's private console and the “⚡ Command Center” Mini App (in a "
            "web browser via /login).\n"
            "• Regular admins cannot change settings; delegates only the module enabled for them.",
        ),
        "benefits": (
            "Disponible en todos los planes (Free incluido). Con PRO y ULTRA PRO se desbloquean más módulos en el mismo "
            "panel, sin reinstalar nada.",
            "Available on every plan (Free included). PRO and ULTRA PRO unlock more modules in the same panel, with "
            "nothing to reinstall.",
        ),
        "setup": (
            "1. Abre el bot en privado → /start → «➕ Añadir a un Grupo» o «📢 Añadir a un Canal».\n"
            "2. Elige tu comunidad y concede los permisos de administrador indicados.\n"
            "3. Bot: /start → «⚙️ Configuración de Grupos» (o «📡 Configuración de Canales») → tu comunidad.\n"
            "4. Mini App: /start → «⚡ Command Center» → selector superior o «Matriz de Grupos» → «Ajustes del Bot ⚙️».\n"
            "5. Para canales usa «🔄 Sincronizar Canales»: el bot verifica sus permisos antes de registrarlo.",
            "1. Open the bot in private → /start → “➕ Add to a Group” or “📢 Add to a Channel”.\n"
            "2. Pick your community and grant the admin rights listed.\n"
            "3. Bot: /start → “⚙️ Group Settings” (or “📡 Channel Settings”) → your community.\n"
            "4. Mini App: /start → “⚡ Command Center” → top selector or “Groups Matrix” → “Bot Settings ⚙️”.\n"
            "5. For channels use “🔄 Sync Channels”: the bot checks its rights before registering it.",
        ),
    },
    "plans": {
        "title": ("⭐ Planes, licencias y pagos (Free · PRO · ULTRA PRO)", "⭐ Plans, licenses and payments (Free · PRO · ULTRA PRO)"),
        "what_is": (
            "⭐ Las licencias desbloquean módulos por comunidad. Se pagan solo con Telegram Stars (XTR), se activan al "
            "instante, duran 30 días y quedan vinculadas a UNA comunidad; al vencer vuelve sola a Free.",
            "⭐ Licenses unlock modules per community. They are paid only with Telegram Stars (XTR), activate instantly, "
            "last 30 days and are bound to ONE community; when they expire it returns to Free automatically.",
        ),
        "functions": (
            "• Free (0 ⭐): Captcha básico, cerraduras de 30 vectores, Anti-Spam, Anti-Flood, advertencias, refuerzo "
            "perimetral, Escudo Antinota, modo nocturno, perímetro y canal de registro. Límites: 3 comandos de "
            "moderación/día, 1 plan de canal, 1 destino de propinas.\n"
            "• PRO ({pro} ⭐): Free + comandos ilimitados, purga de mensajes de servicio, Captcha Pro con bienvenida "
            "personalizada, advertencias y propinas con texto propio, gamificación cada 3 h+, Auditoría MicVIP, "
            "Apelación de baneos y Cuotas de enlaces. Límites: 3 planes de canal, 3 destinos.\n"
            "• ULTRA PRO ({ultra} ⭐): PRO + Bot Clon, Centinela propio, AutoLower, Roles VIP, Centinela de IA, Ghost "
            "Purge v2, Modo Podcast, /speakers, Programador VC, Payload multimedia, Botón de Pánico, respaldo .bunker y "
            "multimedia en propinas/advertencias/avisos/gamificación (desde 30 min). Límites: 10 planes, 10 destinos.",
            "• Free (0 ⭐): basic Captcha, 30-vector locks, Anti-Spam, Anti-Flood, warnings, perimeter hardening, "
            "Screen Shield, night mode, perimeter and log channel. Limits: 3 moderation commands/day, 1 channel plan, "
            "1 tip target.\n"
            "• PRO ({pro} ⭐): Free + unlimited commands, service-message purge, Captcha Pro with custom welcome, "
            "custom warnings and tips text, gamification every 3 h+, MicVIP Audit, Ban Appeals and Link Quotas. "
            "Limits: 3 channel plans, 3 targets.\n"
            "• ULTRA PRO ({ultra} ⭐): PRO + Clone Bot, own Sentinel, AutoLower, VIP Roles, AI Sentinel, Ghost Purge v2, "
            "Podcast mode, /speakers, VC Scheduler, Multimedia Payload, Panic Button, .bunker backups and media in "
            "tips/warnings/notices/gamification (from 30 min). Limits: 10 plans, 10 targets.",
        ),
        "benefits": (
            "Qué ganas al subir: Free → PRO profesionaliza la moderación sin límites diarios, personaliza la imagen y "
            "abre ingresos (apelaciones pagadas, MicVIP auditado) con control del spam de tus propios admins. PRO → "
            "ULTRA PRO te da control total de la voz (Centinela propio, AutoLower, Podcast, Speakers, Programador), IA "
            "en el chat, limpieza de cuentas fantasma, marca propia con Bot Clon quedándote el 100 % de las Stars y "
            "respaldo cifrado.",
            "What you gain by upgrading: Free → PRO makes moderation professional with no daily limits, customizes your "
            "branding and opens revenue (paid appeals, audited MicVIP) while controlling spam from your own admins. "
            "PRO → ULTRA PRO gives you full voice control (own Sentinel, AutoLower, Podcast, Speakers, Scheduler), AI in "
            "the chat, ghost-account cleanup, your own brand with a Clone Bot keeping 100% of the Stars, and encrypted "
            "backups.",
        ),
        "setup": (
            "1. Bot: /start → «⚙️ Configuración de Grupos» → comunidad → «⭐ PRO» o «💎 ULTRA» → «⭐ Pagar con Stars».\n"
            "2. Mini App: «Membresías & Stars» → «PRO (300 XTR)» o «ULTRA PRO (600 XTR)», o el botón de cualquier tarjeta "
            "con 🔒 (paga la licencia de esa comunidad).\n"
            "3. Telegram abre la factura de Stars; al pagar, la licencia se activa al instante.\n"
            "• Sin comunidad elegida, el bot muestra un selector antes de emitir la factura. Desde un Bot Clon, el cobro "
            "se redirige al Bot Maestro.",
            "1. Bot: /start → “⚙️ Group Settings” → community → “⭐ PRO” or “💎 ULTRA” → “⭐ Pay with Stars”.\n"
            "2. Mini App: “Memberships & Stars” → “PRO (300 XTR)” or “ULTRA PRO (600 XTR)”, or the button on any 🔒 card "
            "(pays that community's license).\n"
            "3. Telegram opens the Stars invoice; once paid, the license activates instantly.\n"
            "• With no community selected the bot shows a picker before issuing the invoice. From a Clone Bot, billing "
            "is redirected to the Master Bot.",
        ),
    },
    "sentinel": {
        "title": ("🎙️ El Centinela MTProto (Maestro vs. Dedicado Propio)", "🎙️ The MTProto Sentinel (Master vs. Own)"),
        "what_is": (
            "🎙️ El Centinela es una cuenta de usuario real de Telegram conectada por MTProto (Pyrogram) que actúa como "
            "operador acústico, visual y semántico autónomo 24 h dentro del grupo y de sus videochats. Un bot de "
            "BotFather no puede entrar a llamadas ni regular micrófonos individuales; el Centinela sí, porque tiene "
            "presencia real en la sala. Hay dos modalidades:\n"
            "1. Centinela Maestro: la cuenta global del ecosistema Búnker que patrulla las comunidades.\n"
            "2. Centinela Dedicado Propio (ULTRA PRO): una cuenta telefónica tuya, con identidad propia en la sala, "
            "independencia total y su propio limitador anti-FloodWait (3,5 a 5 peticiones por segundo).",
            "🎙️ The Sentinel is a real Telegram user account connected via MTProto (Pyrogram) that acts as an "
            "autonomous acoustic, visual and semantic operator 24/7 inside the group and its video chats. A BotFather "
            "bot cannot join calls or control individual microphones; the Sentinel can, because it is really present "
            "in the room. Two modes:\n"
            "1. Master Sentinel: the ecosystem's global account patrolling communities.\n"
            "2. Own Dedicated Sentinel (ULTRA PRO): your own phone account, with its own identity in the room, full "
            "independence and its own anti-FloodWait limiter (3.5 to 5 requests per second).",
        ),
        "functions": (
            "Las 10 funciones operativas del Centinela:\n"
            "1. AutoLower en tiempo real: a quien entra o abre micrófono y no es admin, ni está en Whitelist, ni tiene "
            "rol VIP con inmunidad ni pase /micvip, le baja el volumen al 1 %–10 % (por defecto 2 %) sin expulsarlo.\n"
            "2. Pases MicVIP: al pagar el pase de 24 h, sube ese micrófono al 100 % al instante y registra la evidencia "
            "en la Auditoría MicVIP.\n"
            "3. Modo Podcast y ducking: cuando habla el anfitrión o un admin, atenúa a los demás al nivel configurado "
            "(1 %–90 %) y los devuelve al 100 % cuando calla.\n"
            "4. Escudo Antirruido: 3 picos de ruido en 12 s → silencio preventivo de 60 s y aviso «🔇 Escudo Antirruido».\n"
            "5. Escudo Antinota: si alguien sin autorización enciende cámara o comparte pantalla, corta el video, lo "
            "silencia, lo retira de la sala y publica la alerta.\n"
            "6. Userbots marcados: los silencia de forma permanente en la llamada.\n"
            "7. Reinicio preventivo cada 3,5 h: anuncia mantenimiento, cierra y reabre la sala en segundos para limpiar "
            "la caché de audio/video sin que nadie pierda su pase MicVIP.\n"
            "8. Programador de videochats: abre y cierra la sala según días y horario HH:MM-HH:MM en la zona horaria de "
            "la comunidad.\n"
            "9. 5 avisos de sala + Payload multimedia: entrada al VC (agrupada para no inundar), MicVIP, reinicio, "
            "apertura programada y bienvenida fijada, con texto, botón, enlace, multimedia (ULTRA PRO) y auto-borrado.\n"
            "10. Centinela de IA y Ghost Purge: analiza el chat con Mistral (tras anonimizar datos) para el Guardián "
            "anti-toxicidad y el Copiloto, y hace el barrido de miembros por MTProto para Ghost Purge v2.\n"
            "Reglas: cero mensajes privados (nunca escribe por DM a nadie); si la sesión se revoca desde «Dispositivos», "
            "el sistema la marca como revocada, detiene el monitor sin bucles y avisa al dueño con «🧬 Vincular nueva "
            "sesión»; debe ser administrador del grupo con «Gestionar videochats».",
            "The Sentinel's 10 operational functions:\n"
            "1. Real-time AutoLower: anyone who joins or unmutes and is not an admin, Whitelisted, an immune VIP or a "
            "/micvip holder is lowered to 1%–10% (default 2%) without being removed.\n"
            "2. MicVIP passes: when the 24 h pass is paid, it raises that mic to 100% instantly and records the evidence "
            "in the MicVIP Audit.\n"
            "3. Podcast mode and ducking: when the host or an admin speaks, others are lowered to the configured level "
            "(1%–90%) and restored to 100% afterwards.\n"
            "4. Noise Shield: 3 noise spikes within 12 s → a 60 s preventive mute and the “🔇 Noise Shield” notice.\n"
            "5. Screen-Share Shield: if someone unauthorized turns on the camera or shares the screen, it cuts the "
            "video, mutes them, removes them from the room and posts the alert.\n"
            "6. Flagged userbots: permanently muted in the call.\n"
            "7. Preventive restart every 3.5 h: announces maintenance, closes and reopens the room in seconds to clear "
            "the audio/video cache without anyone losing their MicVIP pass.\n"
            "8. Video chat scheduler: opens and closes the room by days and HH:MM-HH:MM in the community's timezone.\n"
            "9. 5 room notices + Multimedia payload: VC join (grouped to avoid floods), MicVIP, restart, scheduled "
            "opening and pinned welcome, with text, button, link, media (ULTRA PRO) and auto-delete.\n"
            "10. AI Sentinel and Ghost Purge: analyzes the chat with Mistral (after anonymizing data) for the "
            "anti-toxicity Guardian and the Copilot, and performs the MTProto member sweep for Ghost Purge v2.\n"
            "Rules: zero private messages (it never DMs anyone); if the session is revoked from “Devices” the system "
            "marks it revoked, stops the monitor without loops and alerts the owner with “🧬 Relink”; it must be a "
            "group admin with “Manage video chats”.",
        ),
        "benefits": (
            "El Centinela Maestro da servicio a las funciones de voz de cada plan; el Centinela Dedicado Propio es "
            "exclusivo de ULTRA PRO ({ultra} ⭐/30 días). Beneficios: sala de voz limpia de ruido, monetización por "
            "micrófono (MicVIP), protección contra transmisiones no autorizadas, AMAs ordenados y, con el propio, "
            "identidad y capacidad independientes del resto del ecosistema.",
            "The Master Sentinel serves each plan's voice features; the Own Dedicated Sentinel is exclusive to ULTRA PRO "
            "({ultra} ⭐/30 days). Benefits: a noise-free voice room, microphone monetization (MicVIP), protection "
            "against unauthorized streams, orderly AMAs and, with your own, identity and capacity independent from the "
            "rest of the ecosystem.",
        ),
        "setup": (
            "Vincular el Centinela propio (solo en el chat privado del bot):\n"
            "1. /start → «⚙️ Configuración de Grupos» → comunidad → «🧬 Clon & Centinela» → «🎙️ Conectar Centinela Propio "
            "(Teléfono)».\n"
            "2. Envía el número en formato internacional (+código de país y número).\n"
            "3. Envía el código de 5 dígitos que te manda Telegram y, si tienes verificación en dos pasos, tu contraseña "
            "2FA.\n"
            "4. Añade esa cuenta al grupo como administrador con «Gestionar videochats».\n"
            "5. Personaliza los avisos en «💎 Herramientas ULTRA» → «⚙️ Configuración del Centinela» (activar/desactivar "
            "cada aviso, texto, multimedia, botón «Texto | URL» y auto-borrado).\n"
            "En la Mini App solo verás su estado en «Herramientas Ultra & Clones MTProto» con el botón «Vincular en el "
            "bot». 🔐 Nunca envíes números, códigos ni contraseñas en este chat de soporte.",
            "Linking your own Sentinel (only in the bot's private chat):\n"
            "1. /start → “⚙️ Group Settings” → community → “🧬 Clone & Sentinel” → “🎙️ Connect Own Sentinel (Phone)”.\n"
            "2. Send the number in international format (+country code and number).\n"
            "3. Send the 5-digit code Telegram gives you and, if you use two-step verification, your 2FA password.\n"
            "4. Add that account to the group as an admin with “Manage video chats”.\n"
            "5. Customize notices in “💎 ULTRA Elite Tools” → “⚙️ Sentinel Settings” (toggle each notice, text, media, "
            "“Text | URL” button and auto-delete).\n"
            "The Mini App only shows its status in “Ultra Tools & MTProto Clones” with the “Link in the bot” button. "
            "🔐 Never send numbers, codes or passwords in this support chat.",
        ),
    },
    "captcha": {
        "title": ("🤖 Aduana Captcha Pro («🤖 Captcha Protection»)", "🤖 Captcha Pro gatekeeper (“🤖 Captcha Protection”)"),
        "what_is": (
            "🤖 La Aduana Captcha Pro es el filtro de entrada: verifica que cada miembro nuevo sea humano antes de que "
            "pueda escribir, frenando bots, cuentas de spam y raids en la puerta.",
            "🤖 The Captcha Pro gatekeeper is the entry filter: it verifies every new member is human before they can "
            "write, stopping bots, spam accounts and raids at the door.",
        ),
        "functions": (
            "• Restringe al recién llegado y le muestra un desafío con cuenta regresiva.\n"
            "• Modos: en la Mini App «Botón simple (Soy humano)» o «Desafío aritmético (math)»; en el Bot, modo "
            "«🔤 Alfanumérico» 🟢/🔴.\n"
            "• Tiempo límite: 30 s, 1 min, 3 min o 5 min (la Mini App también ofrece 120 s).\n"
            "• Castigo si falla o no responde: «👢 Expulsar (Kick)» o «🔇 Silenciar (Mute)».\n"
            "• «🗑️ Serv. Msg»: borra los avisos de entrada/salida.\n"
            "• «✍️ Mensaje»: bienvenida de hasta 1000 caracteres con {name}, {username} y {title}.\n"
            "• Requisito: el bot necesita «Restringir miembros» y «Eliminar mensajes».",
            "• Restricts the newcomer and shows a timed challenge.\n"
            "• Modes: in the Mini App “Simple button (I'm human)” or “Math challenge”; in the Bot, “🔤 Alphanumeric” "
            "mode 🟢/🔴.\n"
            "• Time limit: 30 s, 1 min, 3 min or 5 min (the Mini App also offers 120 s).\n"
            "• Punishment on failure or no answer: “👢 Kick” or “🔇 Mute”.\n"
            "• “🗑️ Service Msg”: deletes join/leave notices.\n"
            "• “✍️ Custom Text”: welcome of up to 1000 characters with {name}, {username} and {title}.\n"
            "• Requirement: the bot needs “Restrict members” and “Delete messages”.",
        ),
        "benefits": (
            "El captcha base está en todos los planes (Free incluido). PRO ({pro} ⭐) añade la bienvenida totalmente "
            "personalizada y la purga automática de mensajes de servicio. Beneficio: cero bots en la puerta y una "
            "bienvenida con tu marca.",
            "The base captcha is on every plan (Free included). PRO ({pro} ⭐) adds a fully customized welcome and "
            "automatic service-message purge. Benefit: zero bots at the door and a branded welcome.",
        ),
        "setup": (
            "Bot:\n1. /start → «⚙️ Configuración de Grupos» → tu grupo → «🤖 Captcha Protection».\n"
            "2. «🛡️ Captcha» hasta verlo en 🟢 · «🔤 Alfanumérico» 🟢/🔴.\n"
            "3. «⏱️ Tiempo» (30 s / 1 / 3 / 5 min) · «⚖️ Castigo» (Expulsar / Silenciar).\n"
            "4. «✍️ Mensaje» para la bienvenida · «🗑️ Serv. Msg» para limpiar avisos.\n"
            "Mini App: ☰ → «Ajustes del Bot ⚙️» → comunidad → «Programación del Bot» → «Aduana & Bienvenida» → activa "
            "«Aduana Captcha Pro», elige «Modo de Reto», «Tiempo Límite», escribe el mensaje → «💾 Guardar».",
            "Bot:\n1. /start → “⚙️ Group Settings” → your group → “🤖 Captcha Protection”.\n"
            "2. “🛡️ Captcha” until 🟢 · “🔤 Alphanumeric” 🟢/🔴.\n"
            "3. “⏱️ Time” (30 s / 1 / 3 / 5 min) · “⚖️ Punishment” (Kick / Mute).\n"
            "4. “✍️ Custom Text” for the welcome · “🗑️ Service Msg” to clean notices.\n"
            "Mini App: ☰ → “Bot Settings ⚙️” → community → “Bot Programming” → “Gatekeeper & Welcome” → enable “Captcha "
            "Pro”, pick the challenge mode, time limit, write the message → “💾 Save”.",
        ),
    },
    "locks": {
        "title": ("🔒 Cerraduras, matriz de 30 vectores y refuerzo perimetral", "🔒 Locks, 30-vector matrix and perimeter hardening"),
        "what_is": (
            "🔒 Las cerraduras deciden qué tipo de contenido se permite en el grupo y qué sanción recibe quien envía lo "
            "prohibido. Incluyen cerraduras rápidas, una matriz granular de 30 vectores y un refuerzo contra ráfagas de "
            "reenvíos y bots no autorizados.",
            "🔒 Locks decide which content types are allowed in the group and what punishment whoever sends forbidden "
            "content gets. They include quick locks, a granular 30-vector matrix and hardening against forward bursts "
            "and unauthorized bots.",
        ),
        "functions": (
            "• Cerraduras rápidas: «🖼️ Multimedia», «🎨 Stickers y GIFs», «🔗 Enlaces Web», «⚙️ Comandos de Bots».\n"
            "• Matriz de 30 vectores en 3 páginas:\n"
            "  1) Multimedia: fotos, videos, videos largos (> 5 o 10 min), GIFs, stickers, notas de voz, audios, notas de "
            "video, archivos, álbumes.\n"
            "  2) Enlaces y reenvíos: enlaces ocultos, enlaces de Telegram, reenvíos, historias reenviadas, respuestas "
            "externas, botones inline, mensajes editados, encuestas, contactos, textos largos.\n"
            "  3) Evasión avanzada: mayúsculas excesivas, ubicación, juegos, facturas, texto spoiler, multimedia "
            "spoiler, sorteos, bots inline (via_bot), invitación de bots, mensajes enriquecidos.\n"
            "• Sanción por vector: «☑️ Off», «❕ Warn», «❗ Kick», «🔇 Mute», «🚫 Ban» + interruptor «🗑️» de eliminación "
            "(sobre Off = solo borrar).\n"
            "• Refuerzo perimetral: ráfagas de reenvíos (Off, 3, 5 u 8 en 30 s, 60 s o 300 s) y hasta 25 bots "
            "autorizados (@AllowedBot) que no se bloquean por botones inline, bots inline ni invitaciones de bots.",
            "• Quick locks: “🖼️ Media”, “🎨 Stickers & GIFs”, “🔗 Web Links”, “⚙️ Bot Commands”.\n"
            "• 30-vector matrix over 3 pages:\n"
            "  1) Media: photos, videos, long videos (> 5 or 10 min), GIFs, stickers, voice notes, audio, video notes, "
            "files, albums.\n"
            "  2) Links & forwards: hidden links, Telegram links, forwards, forwarded stories, external replies, inline "
            "buttons, edited messages, polls, contacts, long texts.\n"
            "  3) Advanced evasion: excessive caps, location, games, invoices, spoiler text, spoiler media, giveaways, "
            "inline bots (via_bot), bot invites, rich messages.\n"
            "• Punishment per vector: “☑️ Off”, “❕ Warn”, “❗ Kick”, “🔇 Mute”, “🚫 Ban” + the “🗑️” delete switch (on "
            "Off = delete only).\n"
            "• Perimeter hardening: forward bursts (Off, 3, 5 or 8 within 30 s, 60 s or 300 s) and up to 25 allowed bots "
            "(@AllowedBot) never blocked by inline buttons, inline bots or bot invites.",
        ),
        "benefits": (
            "Incluido en todos los planes, también Free. Beneficio: control total del contenido sin moderadores "
            "humanos; los VIP (ULTRA PRO) pueden tener exenciones parciales en vectores inofensivos.",
            "Included on every plan, Free too. Benefit: full content control without human moderators; VIPs (ULTRA PRO) "
            "can get partial exemptions on harmless vectors.",
        ),
        "setup": (
            "Bot: panel del grupo → «🔒 Cerraduras» → cerraduras rápidas y matriz (páginas 1-3): toca un vector para "
            "elegir su sanción y activa 🗑️ si quieres borrar el mensaje.\n"
            "Mini App: «Ajustes del Bot ⚙️» → comunidad → «Bloqueo Multimedia» (30 vectores) y «Perimeter Hardening» "
            "(ráfagas y «➕ Autorizar bot» con @alias) → «💾 Guardar».",
            "Bot: group panel → “🔒 Locks” → quick locks and matrix (pages 1-3): tap a vector to choose its punishment "
            "and enable 🗑️ to delete the message.\n"
            "Mini App: “Bot Settings ⚙️” → community → “Media Lock” (30 vectors) and “Perimeter Hardening” (bursts and "
            "“➕ Authorize bot” with @alias) → “💾 Save”.",
        ),
    },
    "antispam": {
        "title": ("🛡️ Escudo Anti-Spam", "🛡️ Anti-Spam shield"),
        "what_is": (
            "🛡️ El Anti-Spam bloquea la publicidad no deseada: enlaces, citas y reenvíos que suelen usar los spammers.",
            "🛡️ Anti-Spam blocks unwanted promotion: links, quotes and forwards typically used by spammers.",
        ),
        "functions": (
            "• Filtros independientes: enlaces de Telegram (t.me), enlaces web, citas y reenvíos desde canales, usuarios, "
            "grupos o bots.\n• Borrado automático opcional de lo bloqueado.\n"
            "• Se combina con «⚠️ Advertencias» para sumar strikes y sancionar a los reincidentes.",
            "• Independent filters: Telegram links (t.me), web links, quotes and forwards from channels, users, groups "
            "or bots.\n• Optional auto-delete of blocked content.\n"
            "• Works with “⚠️ Warns” to add strikes and punish repeat offenders.",
        ),
        "benefits": (
            "Incluido en todos los planes (Free incluido). Beneficio: chat limpio de promociones ajenas sin intervención "
            "manual.",
            "Included on every plan (Free included). Benefit: a chat free of outside promotion with no manual work.",
        ),
        "setup": (
            "Bot: panel del grupo → «🛡️ Anti-Spam» → activa cada filtro y el borrado. Mini App: «Ajustes del Bot ⚙️» → "
            "comunidad → «Perímetro & Filtros» → «💾 Guardar».",
            "Bot: group panel → “🛡️ Anti-Spam” → enable each filter and deletion. Mini App: “Bot Settings ⚙️” → "
            "community → “Perimeter & Filters” → “💾 Save”.",
        ),
    },
    "antiflood": {
        "title": ("🌊 Escudo Anti-Flood", "🌊 Anti-Flood shield"),
        "what_is": (
            "🌊 El Anti-Flood impide que alguien inunde el chat enviando demasiados mensajes seguidos.",
            "🌊 Anti-Flood stops anyone from flooding the chat with too many consecutive messages.",
        ),
        "functions": (
            "• Umbral: de 3 a 50 mensajes (por defecto 10) dentro de una ventana de 5 s a 120 s (por defecto 15 s).\n"
            "• Castigo: Off, Warn, Kick, Mute o Ban, con borrado de los mensajes del flood.\n"
            "• Los VIP pueden tener margen extra (×1 a ×5) con Roles VIP.",
            "• Threshold: 3 to 50 messages (default 10) within a 5 s to 120 s window (default 15 s).\n"
            "• Punishment: Off, Warn, Kick, Mute or Ban, deleting the flood messages.\n"
            "• VIPs can get extra margin (×1 to ×5) with VIP Roles.",
        ),
        "benefits": (
            "Incluido en todos los planes. Beneficio: conversación legible y protección contra ataques de flood.",
            "Included on every plan. Benefit: readable conversations and protection against flood attacks.",
        ),
        "setup": (
            "Bot: panel del grupo → «🌊 Anti-Flood» → mensajes, tiempo, acción y borrado. Mini App: «Ajustes del Bot ⚙️» "
            "→ comunidad → «Perímetro & Filtros» → «💾 Guardar».",
            "Bot: group panel → “🌊 Anti-Flood” → messages, time, action and deletion. Mini App: “Bot Settings ⚙️” → "
            "community → “Perimeter & Filters” → “💾 Save”.",
        ),
    },
    "warns": {
        "title": ("⚠️ Moderación y advertencias", "⚠️ Moderation and warnings"),
        "what_is": (
            "⚠️ La Matriz de Moderación y las Advertencias son el sistema disciplinario: ejecutar sanciones a distancia "
            "y acumular strikes automáticos hasta un castigo final.",
            "⚠️ The Moderation Matrix and Warnings are the disciplinary system: remote punishments and automatic strikes "
            "up to a final penalty.",
        ),
        "functions": (
            "• «🛡️ Matriz de Moderación»: «🚫 /ban» (1 min, 10 min, 1 h, 24 h, 30 días o permanente), «👢 /kick», "
            "«🔇 /mute», «🔊 /unmute», Whitelist («➕ Registrar Usuario en BD»: inmunidad total) y Blacklist "
            "(«➕ Registrar Término en BD»: palabras prohibidas con borrado automático).\n"
            "• «⚠️ Advertencias»: qué genera strike (enlaces, lista negra, flood), límite (p. ej. 3) y castigo al "
            "alcanzarlo (Mute, Kick o Ban). Los baneos automáticos por strikes son apelables con Stars (PRO+).\n"
            "• Personalización del aviso: texto propio en PRO; foto/video/GIF en ULTRA PRO.",
            "• “🛡️ Moderation Matrix”: “🚫 /ban” (1 min, 10 min, 1 h, 24 h, 30 days or permanent), “👢 /kick”, "
            "“🔇 /mute”, “🔊 /unmute”, Whitelist (“➕ Register User to DB”: full immunity) and Blacklist (“➕ Register Term "
            "to DB”: banned words auto-deleted).\n"
            "• “⚠️ Warns”: what triggers a strike (links, blacklist, flood), the limit (e.g. 3) and the penalty on "
            "reaching it (Mute, Kick or Ban). Automatic strike bans can be appealed with Stars (PRO+).\n"
            "• Notice customization: own text on PRO; photo/video/GIF on ULTRA PRO.",
        ),
        "benefits": (
            "Free: 3 comandos de moderación al día. PRO ({pro} ⭐): ilimitados + texto de advertencia propio. ULTRA PRO "
            "({ultra} ⭐): advertencias con multimedia. Beneficio: disciplina automática y coherente.",
            "Free: 3 moderation commands per day. PRO ({pro} ⭐): unlimited + own warning text. ULTRA PRO ({ultra} ⭐): "
            "warnings with media. Benefit: automatic, consistent discipline.",
        ),
        "setup": (
            "Bot: panel del grupo → «🛡️ Matriz de Moderación» (acciones y listas) o «⚠️ Advertencias» (disparadores, "
            "límite y castigo). Para /ban elige la duración en el selector. Mini App: «Ajustes del Bot ⚙️» → comunidad → "
            "«Perímetro & Filtros».",
            "Bot: group panel → “🛡️ Moderation Matrix” (actions and lists) or “⚠️ Warns” (triggers, limit and penalty). "
            "For /ban pick the duration in the selector. Mini App: “Bot Settings ⚙️” → community → “Perimeter & Filters”.",
        ),
    },
    "delmsgs": {
        "title": ("🗑️ Centro de purga de mensajes («🗑️ Borrar Mensajes»)", "🗑️ Message purge center (“🗑️ Delete Messages”)"),
        "what_is": (
            "🗑️ El Centro Avanzado de Purga de Mensajes limpia mensajes del grupo y los avisos de servicio de Telegram "
            "(entradas, salidas, fijados) para mantener el chat ordenado.",
            "🗑️ The Advanced Message Purge Center cleans group messages and Telegram service notices (joins, leaves, "
            "pins) to keep the chat tidy.",
        ),
        "functions": (
            "• Purga de mensajes con la cuota que permite tu plan.\n• Limpieza automática de mensajes de servicio.\n"
            "• El panel muestra el nivel del plan y el privilegio/cuota disponible.",
            "• Message purge within your plan's quota.\n• Automatic service-message cleanup.\n"
            "• The panel shows your plan level and available privilege/quota.",
        ),
        "benefits": (
            "La purga automática de mensajes de servicio es PRO o superior ({pro} ⭐); Free tiene cuota limitada.",
            "Automatic service-message purge is PRO or higher ({pro} ⭐); Free has a limited quota.",
        ),
        "setup": (
            "Bot: panel del grupo → «🗑️ Borrar Mensajes». Mini App: «Ajustes del Bot ⚙️» → comunidad → «Perímetro & "
            "Filtros» (limpieza de avisos de servicio).",
            "Bot: group panel → “🗑️ Delete Messages”. Mini App: “Bot Settings ⚙️” → community → “Perimeter & Filters” "
            "(service-notice cleanup).",
        ),
    },
    "appeals": {
        "title": ("⚖️ Apelación de baneos por Stars (PRO+)", "⚖️ Paid ban appeals (PRO+)"),
        "what_is": (
            "⚖️ La apelación de baneos permite que quien fue baneado AUTOMÁTICAMENTE por alcanzar el límite de "
            "advertencias recupere su acceso pagando una tarifa en Telegram Stars. Los baneos manuales no son apelables.",
            "⚖️ Ban appeals let someone banned AUTOMATICALLY for reaching the warning limit regain access by paying a "
            "Telegram Stars fee. Manual bans cannot be appealed.",
        ),
        "functions": (
            "• Al banearse por strikes, el bot envía por privado el motivo y la factura de la apelación.\n"
            "• Al pagar: desbaneo automático (también limpia la marca de baneo global si se originó en esa comunidad) y "
            "enlace de reingreso de un solo uso con caducidad.\n"
            "• Si el bot no puede desbanear (permisos), reembolsa las Stars automáticamente.\n"
            "• Precio 1–10000 ⭐ (por defecto 250; atajos 50, 100, 250, 500, 1000, 2500), enlace de 10 min, 30 min, 1 h o "
            "24 h, oferta vigente 1–30 días (por defecto 7) y máximo por usuario cada 90 días: 1, 3, 5, 10 o sin límite.",
            "• On a strike ban, the bot privately sends the reason and the appeal invoice.\n"
            "• On payment: automatic unban (also clears the global ban mark if it originated in that community) and a "
            "single-use, expiring rejoin link.\n"
            "• If the bot cannot unban (rights), the Stars are refunded automatically.\n"
            "• Price 1–10000 ⭐ (default 250; shortcuts 50, 100, 250, 500, 1000, 2500), link of 10 min, 30 min, 1 h or 24 h, "
            "offer valid 1–30 days (default 7) and max per user every 90 days: 1, 3, 5, 10 or unlimited.",
        ),
        "benefits": (
            "Requiere PRO ({pro} ⭐) o ULTRA PRO. Beneficio: segunda oportunidad justa y una fuente de ingresos, sin "
            "trabajo manual del staff.",
            "Requires PRO ({pro} ⭐) or ULTRA PRO. Benefit: a fair second chance and a revenue stream, with no manual "
            "staff work.",
        ),
        "setup": (
            "Bot: panel del grupo → «⚖️ Apelaciones de baneo» → 🟢 activar → «💫» precio (o «✏️ ⭐» para uno propio) → "
            "«🔗» duración del enlace → «⏳» vigencia → «🔁» máximo por usuario. Mini App: «Ajustes del Bot ⚙️» → "
            "comunidad → «Ban Appeals».",
            "Bot: group panel → “⚖️ Ban appeals” → 🟢 enable → “💫” price (or “✏️ ⭐” for a custom one) → “🔗” link "
            "lifetime → “⏳” validity → “🔁” max per user. Mini App: “Bot Settings ⚙️” → community → “Ban Appeals”.",
        ),
    },
    "autolower": {
        "title": ("🔊 Radar AutoLower", "🔊 AutoLower radar"),
        "what_is": (
            "🔊 AutoLower es el control acústico automático del videochat: baja el micrófono de quien no tiene "
            "privilegios para eliminar el ruido sin expulsar a nadie. Lo ejecuta el Centinela.",
            "🔊 AutoLower is the video chat's automatic acoustic control: it lowers the mic of anyone without "
            "privileges to remove noise without kicking anyone. The Sentinel runs it.",
        ),
        "functions": (
            "• Atenúa al 1 %–10 % (por defecto 2 %) a quien entra o abre micrófono.\n"
            "• Exentos: administradores, Whitelist, VIP con inmunidad y titulares de /micvip (quedan al 100 %).\n"
            "• Requiere un Centinela activo, administrador con «Gestionar videochats»; sin él, «🛰️ Perímetro» lo "
            "muestra 🟡 «sin Centinela activo».",
            "• Lowers to 1%–10% (default 2%) anyone who joins or unmutes.\n"
            "• Exempt: admins, Whitelist, immune VIPs and /micvip holders (they stay at 100%).\n"
            "• Requires an active Sentinel, admin with “Manage video chats”; without it, “🛰️ Perimeter” shows 🟡 “no "
            "active Sentinel”.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: sala de voz profesional y base de la monetización con MicVIP.",
            "ULTRA PRO ({ultra} ⭐). Benefit: a professional voice room and the basis for MicVIP monetization.",
        ),
        "setup": (
            "Bot: panel del grupo → «📡 Radar y Ecosistema» → «⚙️ /autolower» → actívalo y elige el porcentaje. Mini App: "
            "«Ajustes del Bot ⚙️» → comunidad → «Acústica & Videochat» → «💾 Guardar». Verifica en «🛰️ Perímetro» que "
            "está 🟢.",
            "Bot: group panel → “📡 Radar & Ecosystem” → “⚙️ /autolower” → enable it and choose the percentage. Mini App: "
            "“Bot Settings ⚙️” → community → “Acoustics & Video chat” → “💾 Save”. Check “🛰️ Perimeter” shows 🟢.",
        ),
    },
    "micvip": {
        "title": ("🎙️ Pase VIP de micrófono (/micvip)", "🎙️ Microphone VIP pass (/micvip)"),
        "what_is": (
            "🎙️ MicVIP es un pase de 24 h que cualquier miembro compra con Telegram Stars para hablar al 100 % de "
            "volumen en el videochat mientras el resto está atenuado por AutoLower.",
            "🎙️ MicVIP is a 24 h pass any member buys with Telegram Stars to speak at 100% volume in the video chat "
            "while everyone else is lowered by AutoLower.",
        ),
        "functions": (
            "• El miembro envía /micvip (o usa los botones del anuncio del videochat) y paga con Stars.\n"
            "• El Centinela sube su micrófono al 100 % al instante y el bot le pone una etiqueta nativa de miembro (sin "
            "hacerlo administrador).\n• A las 24 h el privilegio y la etiqueta se retiran solos.\n"
            "• Cada compra queda en la Auditoría MicVIP con la evidencia del Centinela.\n"
            "• Precio fijado por el dueño: 25, 50, 100 o personalizado. Puede conceder también rol VIP.",
            "• The member sends /micvip (or uses the video chat announcement buttons) and pays with Stars.\n"
            "• The Sentinel raises their mic to 100% instantly and the bot gives them a native member tag (never "
            "admin).\n• After 24 h the privilege and tag are removed automatically.\n"
            "• Each purchase is recorded in the MicVIP Audit with the Sentinel's evidence.\n"
            "• Price set by the owner: 25, 50, 100 or custom. It can also grant a VIP role.",
        ),
        "benefits": (
            "Funciona junto al Centinela (AutoLower es ULTRA PRO). Beneficio: monetiza cada llamada de voz y premia a "
            "los miembros que participan.",
            "Works together with the Sentinel (AutoLower is ULTRA PRO). Benefit: monetizes every voice call and rewards "
            "participating members.",
        ),
        "setup": (
            "Bot: panel del grupo → «📡 Radar y Ecosistema» → «🎙️ /mic_vip» (precio y texto) y «🏷️ Etiqueta VIP» "
            "(etiqueta). Mini App: «Ajustes del Bot ⚙️» → comunidad → «Acústica & Videochat» (tarifas del videochat). "
            "Requiere un Centinela activo con «Gestionar videochats».",
            "Bot: group panel → “📡 Radar & Ecosystem” → “🎙️ /mic_vip” (price and text) and “🏷️ VIP Tag” (tag). Mini "
            "App: “Bot Settings ⚙️” → community → “Acoustics & Video chat” (video chat rates). Needs an active Sentinel "
            "with “Manage video chats”.",
        ),
    },
    "micvip_audit": {
        "title": ("🎟️ Auditoría MicVIP (PRO+)", "🎟️ MicVIP Audit (PRO+)"),
        "what_is": (
            "🎟️ La Auditoría MicVIP es el registro de transparencia anti-estafa de los pases: demuestra que cada compra "
            "se entregó de verdad.",
            "🎟️ The MicVIP Audit is the passes' anti-fraud transparency log: it proves every purchase was really "
            "delivered.",
        ),
        "functions": (
            "• Solo lectura: comprador, fecha de compra, expiración real, estado (🟢 vigente / 🔴 expirado) y la "
            "evidencia de que el Centinela aplicó el 100 %.\n• Filtros: Todos, Activos, Expirados, Incidencias.\n"
            "• Exportación CSV en la Mini App.\n• Lectura también para delegados con permiso de analítica.",
            "• Read-only: buyer, purchase date, real expiry, status (🟢 active / 🔴 expired) and evidence that the "
            "Sentinel applied 100%.\n• Filters: All, Active, Expired, Issues.\n• CSV export in the Mini App.\n"
            "• Also readable by delegates with analytics permission.",
        ),
        "benefits": (
            "PRO ({pro} ⭐) o superior. Beneficio: confianza de tus miembros y resolución inmediata de reclamaciones.",
            "PRO ({pro} ⭐) or higher. Benefit: member trust and instant dispute resolution.",
        ),
        "setup": (
            "Bot: panel del grupo → «🎟️ Auditoría MicVIP». Mini App: «Ajustes del Bot ⚙️» → comunidad → «MicVIP Audit» "
            "(botón CSV para exportar).",
            "Bot: group panel → “🎟️ MicVIP Audit”. Mini App: “Bot Settings ⚙️” → community → “MicVIP Audit” (CSV button "
            "to export).",
        ),
    },
    "podcast": {
        "title": ("🎙️ Modo Podcast, Audio Ducking y Speakers", "🎙️ Podcast mode, audio ducking and Speakers"),
        "what_is": (
            "🎙️ El Modo Podcast convierte el videochat en una emisión ordenada: el anfitrión se escucha por encima del "
            "resto. La Gestión de Speakers organiza turnos de palabra pagados.",
            "🎙️ Podcast mode turns the video chat into an orderly broadcast: the host is heard above everyone else. "
            "Speakers Management organizes paid speaking turns.",
        ),
        "functions": (
            "• Ducking: cuando habla el anfitrión o un admin, el resto baja al nivel elegido (1 %–90 %, p. ej. 10, 20 o "
            "30 %) y vuelve al 100 % cuando calla.\n• Incluye el Escudo Antirruido (silencio preventivo de 60 s tras 3 "
            "picos en 12 s).\n• /speakers: cola prioritaria de palabra pagada con Stars; el dueño fija la tarifa y "
            "puede vaciar la cola.",
            "• Ducking: when the host or an admin speaks, others drop to the chosen level (1%–90%, e.g. 10, 20 or 30%) "
            "and return to 100% afterwards.\n• Includes the Noise Shield (60 s preventive mute after 3 spikes in 12 s).\n"
            "• /speakers: priority speaking queue paid with Stars; the owner sets the rate and can clear the queue.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: AMAs, podcasts y debates profesionales y monetizados.",
            "ULTRA PRO ({ultra} ⭐). Benefit: professional, monetized AMAs, podcasts and debates.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🎙️ Modo Podcast» (activar y «🎚️ Nivel de Ducking») y "
            "«🌟 Gestión de Speakers» («⭐ Tarifa Prioridad», «🧹 Vaciar Cola»). Requiere un Centinela activo.",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🎙️ Podcast Mode” (enable and “🎚️ Ducking level”) and “🌟 "
            "Speakers Management” (“⭐ Priority Rate”, “🧹 Clear Queue”). Needs an active Sentinel.",
        ),
    },
    "screen": {
        "title": ("🎥 Escudo Antinota (pantalla y cámaras)", "🎥 Screen-Share Shield (screen and cameras)"),
        "what_is": (
            "🎥 El Escudo Antinota protege el videochat de transmisiones de video o pantalla no autorizadas (contenido "
            "inapropiado, spam visual).",
            "🎥 The Screen-Share Shield protects the video chat from unauthorized video or screen broadcasts "
            "(inappropriate content, visual spam).",
        ),
        "functions": (
            "• Detecta al instante la cámara o pantalla compartida de alguien no autorizado.\n• Corta la transmisión, "
            "lo silencia, lo retira de la sala y publica «🎥 Escudo Antinota Activado».\n"
            "• «🎥 /cams» muestra la supervisión de cámaras y la estabilidad de las transmisiones.",
            "• Instantly detects an unauthorized camera or screen share.\n• Cuts the stream, mutes the person, removes "
            "them from the room and posts “🎥 Screen-Share Shield Activated”.\n"
            "• “🎥 /cams” shows camera supervision and stream stability.",
        ),
        "benefits": (
            "Disponible en todos los planes (ejecuta el Centinela). Beneficio: ninguna transmisión indebida llega a tu "
            "comunidad.",
            "Available on every plan (run by the Sentinel). Benefit: no improper stream ever reaches your community.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🎥 Escudo Antinota» 🟢, o «📡 Radar y Ecosistema» → "
            "«🎥 /cams». Mini App: «Ajustes del Bot ⚙️» → comunidad → «Acústica & Videochat».",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🎥 Screen-Share Shield” 🟢, or “📡 Radar & Ecosystem” → "
            "“🎥 /cams”. Mini App: “Bot Settings ⚙️” → community → “Acoustics & Video chat”.",
        ),
    },
    "vcsched": {
        "title": ("🗓️ Programador de videochats (VC Scheduler)", "🗓️ Video chat scheduler"),
        "what_is": (
            "🗓️ El Programador abre y cierra la sala de voz automáticamente en tus horarios, sin que nadie tenga que "
            "estar conectado.",
            "🗓️ The scheduler opens and closes the voice room automatically on your schedule, with nobody needing to "
            "be online.",
        ),
        "functions": (
            "• Días de la semana y horario HH:MM-HH:MM en la zona horaria de la comunidad.\n"
            "• Publica los avisos de apertura (vc_sched_start) y cierre, y fija la bienvenida del videochat.\n"
            "• Lo ejecuta el Centinela con «Gestionar videochats».",
            "• Days of the week and HH:MM-HH:MM window in the community's timezone.\n"
            "• Posts the opening (vc_sched_start) and closing notices and pins the video chat welcome.\n"
            "• Run by the Sentinel with “Manage video chats”.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: rutinas de voz puntuales y automáticas.",
            "ULTRA PRO ({ultra} ⭐). Benefit: punctual, automatic voice routines.",
        ),
        "setup": (
            "Bot: panel del grupo → «📡 Radar y Ecosistema» → «🗓️ Programador VC» → días y horario → activar.",
            "Bot: group panel → “📡 Radar & Ecosystem” → “🗓️ VC Scheduler” → days and window → enable.",
        ),
    },
    "payload": {
        "title": ("💎 Avisos del Centinela y Payload multimedia", "💎 Sentinel notices and Multimedia payload"),
        "what_is": (
            "💎 Son los mensajes que el sistema publica alrededor del videochat. Puedes personalizarlos con tu marca.",
            "💎 These are the messages the system posts around the video chat. You can customize them with your brand.",
        ),
        "functions": (
            "• 5 avisos: entrada al videochat (vc_join, agrupa menciones para no inundar), MicVIP (mic_vip), reinicio "
            "preventivo de 3,5 h (reset_notice), apertura programada (vc_sched_start) y bienvenida fijada (vc_welcome).\n"
            "• Por aviso: activar/desactivar, texto, botón «Texto | URL», enlaces, multimedia (ULTRA PRO) y auto-borrado.\n"
            "• Payload multimedia: contenido promocional que el Centinela publica en la sala.",
            "• 5 notices: video chat join (vc_join, groups mentions to avoid floods), MicVIP (mic_vip), 3.5 h preventive "
            "restart (reset_notice), scheduled opening (vc_sched_start) and pinned welcome (vc_welcome).\n"
            "• Per notice: enable/disable, text, “Text | URL” button, links, media (ULTRA PRO) and auto-delete.\n"
            "• Multimedia payload: promotional content the Sentinel posts in the room.",
        ),
        "benefits": (
            "Personalización completa y multimedia en ULTRA PRO ({ultra} ⭐). Beneficio: cada llamada promociona tu "
            "comunidad y tus pases.",
            "Full customization and media on ULTRA PRO ({ultra} ⭐). Benefit: every call promotes your community and "
            "your passes.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «⚙️ Configuración del Centinela» (los 5 avisos) y "
            "«💎 Payload Multimedia».",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “⚙️ Sentinel Settings” (the 5 notices) and “💎 Multimedia "
            "Payload”.",
        ),
    },
    "clone": {
        "title": ("🧬 Bot Clon (ULTRA PRO)", "🧬 Clone Bot (ULTRA PRO)"),
        "what_is": (
            "🧬 El Bot Clon es tu propio bot (creado en @BotFather) funcionando con toda la lógica del Búnker bajo tu "
            "nombre y marca.",
            "🧬 The Clone Bot is your own bot (created in @BotFather) running all of The Bunker's logic under your name "
            "and brand.",
        ),
        "functions": (
            "• Modera, avisa y cobra como el Bot Maestro, pero con tu identidad.\n"
            "• Conservas el 100 % de las Stars de pases MicVIP, planes de canal y propinas cobradas por tu clon.\n"
            "• Las licencias PRO/ULTRA siempre se pagan en el Bot Maestro (nunca descuentan del balance del clon).\n"
            "• Si el bot maestro no puede publicar un aviso, el clon lo publica como respaldo.\n"
            "• Por seguridad, el token se vincula SOLO en el chat privado del bot y se guarda cifrado; la Mini App solo "
            "muestra el estado (🟢 activo / 🔴 sin vincular).",
            "• Moderates, notifies and charges like the Master Bot, but with your identity.\n"
            "• You keep 100% of the Stars from MicVIP passes, channel plans and tips charged by your clone.\n"
            "• PRO/ULTRA licenses are always paid in the Master Bot (never from the clone's balance).\n"
            "• If the master bot cannot post a notice, the clone posts it as a fallback.\n"
            "• For security the token is linked ONLY in the bot's private chat and stored encrypted; the Mini App only "
            "shows status (🟢 active / 🔴 not linked).",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: marca propia y el 100 % de los ingresos en Stars.",
            "ULTRA PRO ({ultra} ⭐). Benefit: your own brand and 100% of Stars revenue.",
        ),
        "setup": (
            "1. En @BotFather envía /newbot y copia el token.\n2. Bot: /start → «⚙️ Configuración de Grupos» → comunidad → "
            "«🧬 Clon & Centinela» → «🔑 Conectar Token @BotFather» → pega el token.\n3. Añade el clon al grupo como "
            "administrador.\n• Para cambiarlo: «🔄 Actualizar Token @BotFather»; para retirarlo: «🛑 Desconectar Bot "
            "Clon». 🔐 Nunca pegues el token en la Mini App ni en este chat.",
            "1. In @BotFather send /newbot and copy the token.\n2. Bot: /start → “⚙️ Group Settings” → community → "
            "“🧬 Clone & Sentinel” → “🔑 Connect @BotFather Token” → paste the token.\n3. Add the clone to the group as "
            "an admin.\n• To change it: “🔄 Update @BotFather Token”; to remove it: “🛑 Disconnect Clone Bot”. 🔐 Never "
            "paste the token in the Mini App or in this chat.",
        ),
    },
    "vip_roles": {
        "title": ("👑 Roles y etiquetas VIP automáticos (ULTRA PRO)", "👑 Automatic VIP roles & tags (ULTRA PRO)"),
        "what_is": (
            "👑 Roles VIP otorga automáticamente un estatus VIP, con etiqueta NATIVA de Telegram, a quien apoya a la "
            "comunidad, sin hacer administrador a nadie.",
            "👑 VIP Roles automatically grants VIP status, with a NATIVE Telegram tag, to whoever supports the "
            "community, without making anyone an admin.",
        ),
        "functions": (
            "• Se concede al donar Stars alcanzando el mínimo (1, 25, 50, 100, 250, 500 o 1000 ⭐; por defecto 50), al "
            "comprar MicVIP o manualmente por el dueño («➕ Conceder VIP» por ID o mensaje reenviado).\n"
            "• Duración: 7, 30, 90, 365 días o permanente; vence sola.\n"
            "• Etiqueta nativa (setChatMemberTag): máx. 16 caracteres, sin emojis.\n"
            "• 3 beneficios: prioridad/inmunidad ante AutoLower, margen extra en Anti-Flood (×1 a ×5) y exenciones "
            "parciales en vectores inofensivos (fotos, stickers, GIFs, voz…).\n"
            "• Nunca eximibles: enlaces, reenvíos, botones inline, bots y ediciones; durante el Pánico o el modo "
            "nocturno las exenciones se suspenden.\n• El bot necesita «Gestionar etiquetas».",
            "• Granted on donating Stars reaching the minimum (1, 25, 50, 100, 250, 500 or 1000 ⭐; default 50), on "
            "buying MicVIP, or manually by the owner (“➕ Grant VIP” by ID or forwarded message).\n"
            "• Duration: 7, 30, 90, 365 days or permanent; expires on its own.\n"
            "• Native tag (setChatMemberTag): max 16 characters, no emoji.\n"
            "• 3 perks: AutoLower priority/immunity, extra Anti-Flood margin (×1 to ×5) and partial exemptions on "
            "harmless vectors (photos, stickers, GIFs, voice…).\n"
            "• Never exemptible: links, forwards, inline buttons, bots and edits; during Panic or night mode exemptions "
            "are suspended.\n• The bot needs “Manage tags”.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: fideliza a donantes y compradores con estatus visible y ventajas reales.",
            "ULTRA PRO ({ultra} ⭐). Benefit: builds donor and buyer loyalty with visible status and real perks.",
        ),
        "setup": (
            "Bot: panel del grupo → «👑 Roles VIP» → 🟢 activar → «🏷️ Etiqueta ✏️» → mínimo de Stars «💝» → duración «⏳» → "
            "anti-flood «🌊» → «🛡️ Exenciones» → «👥 Miembros» / «➕ Conceder VIP». Mini App: «Ajustes del Bot ⚙️» → "
            "comunidad → «VIP Roles & Tags».",
            "Bot: group panel → “👑 VIP Roles” → 🟢 enable → “🏷️ Tag ✏️” → minimum Stars “💝” → duration “⏳” → anti-flood "
            "“🌊” → “🛡️ Exemptions” → “👥 Members” / “➕ Grant VIP”. Mini App: “Bot Settings ⚙️” → community → “VIP Roles "
            "& Tags”.",
        ),
    },
    "link_quotas": {
        "title": ("🔗 Cuotas diarias de enlaces por administrador (PRO+)", "🔗 Daily admin link quotas (PRO+)"),
        "what_is": (
            "🔗 Las cuotas de enlaces frenan el exceso de promoción de los propios administradores: cada uno solo puede "
            "publicar un número de enlaces cada 24 h.",
            "🔗 Link quotas curb over-promotion by your own admins: each can only post a set number of links every 24 h.",
        ),
        "functions": (
            "• Ventana móvil de 24 h por administrador.\n• Límite general 0–100 (0 = no puede publicar enlaces).\n"
            "• Excepciones individuales: 0, N o ♾️ sin límite.\n• Opcional: contar también reenvíos de canales (📥).\n"
            "• Al superar la cuota se borra la publicación y el admin recibe un aviso con el tiempo restante. El dueño "
            "nunca se limita.",
            "• 24 h rolling window per admin.\n• Default limit 0–100 (0 = cannot post links).\n"
            "• Individual exceptions: 0, N or ♾️ unlimited.\n• Optional: also count channel forwards (📥).\n"
            "• Over quota the post is deleted and the admin gets a notice with the time left. The owner is never limited.",
        ),
        "benefits": (
            "PRO ({pro} ⭐) o superior. Beneficio: equipo disciplinado y chat sin saturación publicitaria.",
            "PRO ({pro} ⭐) or higher. Benefit: a disciplined team and a chat free of ad overload.",
        ),
        "setup": (
            "Bot: panel del grupo → «🔗 Cuotas de enlaces» → 🟢 activar → «➖/➕» límite general → toca un admin para su "
            "excepción («♾️ Sin límite» / «↩️ Usar general») → «📥» reenvíos. Mini App: «Ajustes del Bot ⚙️» → comunidad → "
            "«Admin Link Quotas».",
            "Bot: group panel → “🔗 Link quotas” → 🟢 enable → “➖/➕” default limit → tap an admin for their exception "
            "(“♾️ Unlimited” / “↩️ Use default”) → “📥” forwards. Mini App: “Bot Settings ⚙️” → community → “Admin Link "
            "Quotas”.",
        ),
    },
    "channel_plans": {
        "title": ("📡 Canales: sincronización y planes de membresía VIP", "📡 Channels: sync and VIP membership plans"),
        "what_is": (
            "📡 El Estudio de Canales convierte tu canal en un negocio de suscripción: vendes acceso VIP con Stars y el "
            "sistema entrega, renueva y expulsa automáticamente.",
            "📡 The Channel Studio turns your channel into a subscription business: you sell VIP access with Stars and "
            "the system delivers, renews and removes automatically.",
        ),
        "functions": (
            "• Sincronización con autodiagnóstico: antes de registrar el canal, el bot comprueba que es administrador "
            "con Publicar, Editar, Eliminar e Invitar usuarios, y que tú eres el creador; si falta algo te dice qué "
            "activar y ofrece «🔄 Volver a verificar».\n"
            "• Plan: nombre, duración 1–365 días, precio 1–10000 ⭐, texto promocional, multimedia opcional, enlace VIP "
            "y difusión automática cada 6, 12, 24 o 48 h.\n"
            "• Enlaces de pago de un solo uso que se queman al entrar.\n"
            "• Vigilante de suscripciones: aviso 48 h antes del vencimiento y expulsión de quien no renueva.",
            "• Sync with self-check: before registering the channel the bot verifies it is an admin with Post, Edit, "
            "Delete and Invite users, and that you are the creator; if anything is missing it tells you what to enable "
            "and offers “🔄 Check again”.\n"
            "• Plan: name, duration 1–365 days, price 1–10000 ⭐, promo text, optional media, VIP link and automatic "
            "broadcast every 6, 12, 24 or 48 h.\n• Single-use paid links that burn on entry.\n"
            "• Subscription watchdog: reminder 48 h before expiry and removal of non-renewers.",
        ),
        "benefits": (
            "Planes activos por canal: Free 1, PRO 3, ULTRA PRO 10. Beneficio: ingresos recurrentes sin gestionar "
            "accesos a mano (con Bot Clon te quedas el 100 %).",
            "Active plans per channel: Free 1, PRO 3, ULTRA PRO 10. Benefit: recurring revenue without managing access "
            "by hand (with a Clone Bot you keep 100%).",
        ),
        "setup": (
            "1. Bot: «📡 Configuración de Canales» → «🔄 Sincronizar Canales» → «📡 Seleccionar mi canal».\n"
            "2. Canal → «➕ Crear Nuevo Plan» → sigue el asistente (nombre, días, precio, texto, multimedia, enlace, "
            "difusión).\n3. Mini App: «Estudio de Canales» → «Generador de Planes VIP» y «Difusión Automática de "
            "Promoción»; los planes activos se pausan, comparten o borran desde «Planes Activos del Canal».",
            "1. Bot: “📡 Channel Settings” → “🔄 Sync Channels” → “📡 Select my channel”.\n"
            "2. Channel → “➕ Create New Plan” → follow the wizard (name, days, price, text, media, link, broadcast).\n"
            "3. Mini App: “Channel Studio” → “VIP Plan Generator” and “Automatic Promotion Broadcast”; active plans are "
            "paused, shared or deleted from “Active Channel Plans”.",
        ),
    },
    "tips": {
        "title": ("⭐ Propinas y donaciones en Stars", "⭐ Stars tips and donations"),
        "what_is": (
            "⭐ Las Propinas permiten que tu comunidad te apoye con donaciones voluntarias en Telegram Stars.",
            "⭐ Tips let your community support you with voluntary Telegram Stars donations.",
        ),
        "functions": (
            "• Montos rápidos configurables (p. ej. 15, 50, 100, 250, 500 ⭐) y monto libre.\n"
            "• Difusión del panel de propinas en canales destino: el bot debe ser admin con permiso de publicar; los "
            "destinos se validan antes de guardarlos y uno que falla se pausa solo (⏸️).\n"
            "• Agradecimiento privado al donante con {stars}, {mention}, {user_name}, {community}.\n"
            "• Las donaciones pueden conceder rol VIP (ULTRA PRO).",
            "• Configurable quick amounts (e.g. 15, 50, 100, 250, 500 ⭐) and a free amount.\n"
            "• Broadcasting the tips panel to target channels: the bot must be an admin allowed to post; targets are "
            "validated before saving and a failing one is paused automatically (⏸️).\n"
            "• Private thank-you to the donor with {stars}, {mention}, {user_name}, {community}.\n"
            "• Donations can grant a VIP role (ULTRA PRO).",
        ),
        "benefits": (
            "Destinos: Free 1, PRO 3, ULTRA PRO 10. PRO ({pro} ⭐): texto del anuncio y agradecimiento personalizados. "
            "ULTRA PRO ({ultra} ⭐): multimedia. Beneficio: ingresos voluntarios sin fricción.",
            "Targets: Free 1, PRO 3, ULTRA PRO 10. PRO ({pro} ⭐): custom announcement and thank-you. ULTRA PRO "
            "({ultra} ⭐): media. Benefit: frictionless voluntary revenue.",
        ),
        "setup": (
            "Bot: panel del grupo → «📡 Radar y Ecosistema» → «⭐ Propinas Stars» → activar → montos → «➕ Añadir Canal "
            "Destino» (@canal o ID) → texto/agradecimiento → publicar. Mini App: «Ajustes del Bot ⚙️» → comunidad → "
            "«Propinas & Monetización» → «💾 Guardar».",
            "Bot: group panel → “📡 Radar & Ecosystem” → “⭐ Stars Tips & Donations” → enable → amounts → “➕ Add Target” "
            "(@channel or ID) → text/thank-you → broadcast. Mini App: “Bot Settings ⚙️” → community → “Tips & "
            "Monetization” → “💾 Save”.",
        ),
    },
    "ai": {
        "title": ("🤖 Centinela de IA (Guardián + Copiloto)", "🤖 AI Sentinel (Guardian + Copilot)"),
        "what_is": (
            "🤖 El Centinela de IA lleva Mistral AI al chat de texto de tu grupo: un Guardián que elimina contenido "
            "dañino y un Copiloto que conversa con los miembros.",
            "🤖 The AI Sentinel brings Mistral AI to your group's text chat: a Guardian that removes harmful content "
            "and a Copilot that chats with members.",
        ),
        "functions": (
            "• Guardián anti-toxicidad: analiza los mensajes en segundo plano (tras anonimizar datos personales) y purga "
            "amenazas, acoso grave, estafas, phishing o doxxing.\n"
            "• Copiloto: responde en modo «🎯 Solo mención», «🎲 Probabilidad» (1–100 %) o «⚡ Siempre», con "
            "personalidad «🛡️ Guardián táctico», «📝 Copiloto ejecutivo» o «🤝 Anfitrión/PR» y directivas propias de "
            "hasta 1000 caracteres.\n• Necesita un Centinela activo en el grupo.",
            "• Anti-toxicity Guardian: scans messages in the background (after anonymizing personal data) and purges "
            "threats, severe harassment, scams, phishing or doxxing.\n"
            "• Copilot: replies in “🎯 Mention only”, “🎲 Chance” (1–100%) or “⚡ Always” mode, with a “🛡️ Tactical "
            "guardian”, “📝 Executive copilot” or “🤝 Host/PR” personality and custom directives of up to 1000 "
            "characters.\n• Needs an active Sentinel in the group.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: moderación inteligente 24/7 y una comunidad atendida en todo momento.",
            "ULTRA PRO ({ultra} ⭐). Benefit: smart 24/7 moderation and a community that is always attended.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🤖 Centinela de IA (ULTRA)» → «🛡️ Guardián Anti-Toxicidad» "
            "y Copiloto, modo, probabilidad, tono y directivas. Mini App: «Ajustes del Bot ⚙️» → comunidad → «Centinela "
            "de IA».",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🤖 AI Sentinel (ULTRA)” → “🛡️ Anti-Toxicity Guardian” and "
            "Copilot, mode, chance, tone and directives. Mini App: “Bot Settings ⚙️” → community → “AI Sentinel”.",
        ),
    },
    "ghost": {
        "title": ("💀 Ghost Purge v2 (Radar Anti-Fantasmas)", "💀 Ghost Purge v2 (anti-ghost radar)"),
        "what_is": (
            "💀 Ghost Purge limpia la comunidad de cuentas fantasma o peligrosas que inflan el número de miembros y "
            "son vector de spam.",
            "💀 Ghost Purge cleans the community of ghost or dangerous accounts that inflate member counts and spread "
            "spam.",
        ),
        "functions": (
            "• Criterios: 💀 cuentas eliminadas, 🚩 marcadas como estafa, 🎭 falsas, 🤖 userbots marcados por el radar y, "
            "opcional, 👻 inalcanzables.\n• Directiva: 🔴 baneo permanente o 🟡 expulsión suave.\n"
            "• Barrido por MTProto (Centinela) o, sin él, por los usuarios registrados por el bot.\n"
            "• Administradores y bots nunca se purgan. Informe en el grupo y en el canal de registro.",
            "• Criteria: 💀 deleted accounts, 🚩 flagged scam, 🎭 fake, 🤖 radar-flagged userbots and, optionally, "
            "👻 unreachable.\n• Directive: 🔴 permanent ban or 🟡 soft kick.\n"
            "• MTProto sweep (Sentinel) or, without it, the users registered by the bot.\n"
            "• Admins and bots are never purged. Report in the group and the log channel.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: métricas reales y menos riesgo de spam.",
            "ULTRA PRO ({ultra} ⭐). Benefit: real metrics and lower spam risk.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «💀 Ghost Purge (Cuentas Fantasma)» → criterios → directiva "
            "→ ejecutar. Mini App: «Ajustes del Bot ⚙️» → comunidad → «Radar Anti-Fantasmas».",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “💀 Ghost Purge (Ghost Accounts)” → criteria → directive → run. "
            "Mini App: “Bot Settings ⚙️” → community → “Anti-Ghost Radar”.",
        ),
    },
    "night": {
        "title": ("🌙 Modo Nocturno Autónomo", "🌙 Autonomous Night Mode"),
        "what_is": (
            "🌙 El Modo Nocturno cierra automáticamente el grupo al contenido de riesgo durante las horas en que no hay "
            "moderadores.",
            "🌙 Night Mode automatically closes the group to risky content during hours without moderators.",
        ),
        "functions": (
            "• Bloquea multimedia, enlaces, stickers y comandos en el horario HH:MM-HH:MM (p. ej. 22:00-06:00).\n"
            "• Restaura los permisos diurnos al terminar.\n• Suspende las exenciones VIP durante la noche.",
            "• Locks media, links, stickers and commands during HH:MM-HH:MM (e.g. 22:00-06:00).\n"
            "• Restores daytime permissions afterwards.\n• Suspends VIP exemptions during the night.",
        ),
        "benefits": (
            "Disponible en todos los planes. Beneficio: protección 24 h sin vigilancia humana.",
            "Available on every plan. Benefit: 24 h protection without human watch.",
        ),
        "setup": (
            "Bot: panel del grupo → «📡 Radar y Ecosistema» → «🌙 Modo Nocturno Autónomo» → «⏰ Modificar Horario "
            "(HH:MM-HH:MM)» → «🟢 Activar Modo Nocturno».",
            "Bot: group panel → “📡 Radar & Ecosystem” → “🌙 Autonomous Night Mode” → “⏰ Modify Schedule (HH:MM-HH:MM)” "
            "→ “🟢 Enable Night Mode”.",
        ),
    },
    "panic": {
        "title": ("🚨 Botón de Pánico y Escudo Anti-Raid", "🚨 Panic Button and Anti-Raid Shield"),
        "what_is": (
            "🚨 El Botón de Pánico es el bloqueo de emergencia ante un raid: cierra el grupo de golpe hasta que lo "
            "levantes. El Escudo Anti-Raid vigila entradas masivas y aparece en el perímetro.",
            "🚨 The Panic Button is the emergency lockdown against a raid: it closes the group at once until you lift "
            "it. The Anti-Raid Shield watches mass joins and shows on the perimeter.",
        ),
        "functions": (
            "• «🚨 Activar Bloqueo» → «✅ Confirmar Bloqueo»: publica «🚨 BLOQUEO DE EMERGENCIA ACTIVO» y restringe el "
            "grupo.\n• «🟢 Levantar Bloqueo»: restaura la normalidad («🟢 Bloqueo levantado»).\n"
            "• Durante el pánico se suspenden las exenciones VIP. El perímetro muestra 🟠 cuando hay un raid en curso.",
            "• “🚨 Activate Lockdown” → “✅ Confirm Lockdown”: posts “🚨 RAID LOCKDOWN ACTIVE” and restricts the group.\n"
            "• “🟢 Lift Lockdown”: restores normality (“🟢 Lockdown lifted”).\n"
            "• VIP exemptions are suspended during panic. The perimeter shows 🟠 while a raid is in progress.",
        ),
        "benefits": (
            "Botón de Pánico: ULTRA PRO ({ultra} ⭐). Beneficio: frenas un ataque coordinado en un toque.",
            "Panic Button: ULTRA PRO ({ultra} ⭐). Benefit: stop a coordinated attack with one tap.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🚨 Botón de Pánico» → «🚨 Activar Bloqueo» → «✅ Confirmar "
            "Bloqueo»; para terminar, «🟢 Levantar Bloqueo».",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🚨 Panic Button” → “🚨 Activate Lockdown” → “✅ Confirm "
            "Lockdown”; to end it, “🟢 Lift Lockdown”.",
        ),
    },
    "gamification": {
        "title": ("🎮 Gamificación y niveles XP", "🎮 Gamification and XP levels"),
        "what_is": (
            "🎮 La gamificación premia la participación: los miembros ganan XP, suben de nivel y compiten en el ranking.",
            "🎮 Gamification rewards participation: members earn XP, level up and compete on the leaderboard.",
        ),
        "functions": (
            "• XP por participar con multiplicador 1x, 2x, 3x o 5x.\n• Comandos /rank y /top; Top 10 XP en la Mini App.\n"
            "• Anuncio recurrente interactivo de gamificación.",
            "• XP for participating with a 1x, 2x, 3x or 5x multiplier.\n• /rank and /top commands; Top 10 XP in the "
            "Mini App.\n• Interactive recurring gamification card.",
        ),
        "benefits": (
            "Anuncio recurrente: PRO cada 3 h o más; ULTRA PRO con multimedia y desde cada 30 min. Beneficio: más "
            "actividad y retención.",
            "Recurring card: PRO every 3 h or more; ULTRA PRO with media and from every 30 min. Benefit: more activity "
            "and retention.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🎮 Gamificación & Niveles XP» (multiplicador) y «📡 Radar y "
            "Ecosistema» → «🎮 Anuncio de Gamificación» (frecuencia, texto, multimedia, «🟢 Activar anuncio»).",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🎮 Gamification & XP Levels” (multiplier) and “📡 Radar & "
            "Ecosystem” → “🎮 Gamification Announcement” (frequency, text, media, “🟢 Enable announcement”).",
        ),
    },
    "log": {
        "title": ("🧾 Canal de registro, Perímetro y Analítica", "🧾 Log channel, Perimeter and Analytics"),
        "what_is": (
            "🧾 Son las herramientas de visibilidad: el Perímetro muestra qué protecciones operan de verdad, la "
            "Analítica mide la comunidad en vivo y el Canal de registro guarda auditorías e informes.",
            "🧾 These are the visibility tools: the Perimeter shows which protections are really operating, Analytics "
            "measures the community live and the Log channel keeps audits and reports.",
        ),
        "functions": (
            "• «🛰️ Perímetro»: 🟢 activo, 🟡 en espera/sin Centinela, 🟠 alerta de raid, 🔴 fallo, 🔒 requiere licencia; "
            "distingue lo encendido de lo que opera.\n"
            "• «📈 Analítica en Vivo» (Mini App): DAU/WAU/MAU, retención 7/30 días, Stars recaudadas, bloqueos de "
            "seguridad, mapa de calor 24/7, desglose de mensajes, sala de audio, Top 10 y feed en tiempo real.\n"
            "• «🧾 Canal de Registro»: eventos de moderación e informe periódico de rendimiento de admins (diario 24 h, "
            "cada 3 días o semanal). Debe ser distinto de la comunidad, tú su creador y el bot capaz de publicar.",
            "• “🛰️ Perimeter”: 🟢 on, 🟡 standby/no Sentinel, 🟠 raid alert, 🔴 fault, 🔒 needs a license; separates what "
            "is enabled from what is operating.\n"
            "• “📈 Live Analytics” (Mini App): DAU/WAU/MAU, 7/30-day retention, Stars raised, security blocks, 24/7 "
            "heatmap, message breakdown, audio room, Top 10 and a real-time feed.\n"
            "• “🧾 Log Channel”: moderation events and periodic admin performance report (daily 24 h, every 3 days or "
            "weekly). It must differ from the community, you must be its creator and the bot able to post.",
        ),
        "benefits": (
            "Perímetro y canal de registro en todos los planes; la analítica completa vive en la Mini App. Beneficio: "
            "decisiones con datos reales.",
            "Perimeter and log channel on every plan; full analytics lives in the Mini App. Benefit: data-driven "
            "decisions.",
        ),
        "setup": (
            "Bot: panel del grupo → «🛰️ Perímetro» / «🧾 Canal de Registro» (envía @alias, ID -100… o reenvía un mensaje "
            "del canal). Mini App: «Analítica en Vivo», «Estado del Perímetro», «Canal de Registro» e «Informe de "
            "Rendimiento de Admins».",
            "Bot: group panel → “🛰️ Perimeter” / “🧾 Log Channel” (send @alias, -100… ID or forward a message from the "
            "channel). Mini App: “Live Analytics”, “Perimeter Status”, “Log Channel” and “Admin Performance Report”.",
        ),
    },
    "backup": {
        "title": ("🔐 Respaldo criptográfico (.bunker)", "🔐 Cryptographic backup (.bunker)"),
        "what_is": (
            "🔐 El respaldo .bunker guarda toda la configuración de tu comunidad en un archivo firmado para restaurarla "
            "o clonarla cuando quieras.",
            "🔐 The .bunker backup stores your whole community configuration in a signed file to restore or clone it "
            "whenever you want.",
        ),
        "functions": (
            "• Exporta e importa toda la configuración.\n• Firma HMAC-SHA256: cualquier manipulación invalida el archivo.",
            "• Exports and imports the full configuration.\n• HMAC-SHA256 signature: any tampering invalidates the file.",
        ),
        "benefits": (
            "ULTRA PRO ({ultra} ⭐). Beneficio: recuperación inmediata ante errores o migraciones.",
            "ULTRA PRO ({ultra} ⭐). Benefit: instant recovery from mistakes or migrations.",
        ),
        "setup": (
            "Bot: panel del grupo → «💎 Herramientas ULTRA» → «🔐 Respaldo Criptográfico (.bunker)» → Exportar, o Importar "
            "y enviar el archivo .bunker.",
            "Bot: group panel → “💎 ULTRA Elite Tools” → “🔐 Cryptographic Backup (.bunker)” → Export, or Import and send "
            "the .bunker file.",
        ),
    },
    "human": {
        "title": ("🧑‍💻 Soporte humano", "🧑‍💻 Human support"),
        "what_is": (
            "🧑‍💻 El soporte humano atiende pagos con Stars no acreditados, peticiones expresas de hablar con una persona "
            "y proyectos a medida.",
            "🧑‍💻 Human support handles uncredited Stars payments, explicit requests to talk to a person and custom "
            "projects.",
        ),
        "functions": (
            "Escribe a " + SUPPORT_HUMAN_CONTACT + " indicando tu ID de Telegram (/start → «🛠️ Mi ID y Estado»), la "
            "comunidad y, si es un pago, el ID del cargo del recibo de Telegram. Las entregas fallidas de licencias y "
            "apelaciones se reembolsan automáticamente.",
            "Write to " + SUPPORT_HUMAN_CONTACT + " with your Telegram ID (/start → “🛠️ My ID & Status”), the community "
            "and, for a payment, the charge ID on Telegram's receipt. Failed license and appeal deliveries are refunded "
            "automatically.",
        ),
        "benefits": ("Disponible para todos los planes.", "Available on every plan."),
        "setup": (
            "Contacto: " + SUPPORT_HUMAN_CONTACT + " (o /start → «🆘 Soporte»).",
            "Contact: " + SUPPORT_HUMAN_CONTACT + " (or /start → “🆘 Support”).",
        ),
    },
}

_SECTION_HEADERS = {
    "what_is": ("📘 Qué es", "📘 What it is"),
    "functions": ("⚙️ Funciones y cómo opera", "⚙️ Functions and how it works"),
    "benefits": ("⭐ Plan y beneficios", "⭐ Plan and benefits"),
    "setup": ("🛠️ Cómo configurarlo", "🛠️ How to set it up"),
}


def _first_sentence(text: str) -> str:
    """Primera frase (o línea) de un bloque: el «qué es» breve que precede a una guía de configuración."""
    line = str(text).strip().split("\n", 1)[0]
    match = re.match(r"(.+?[.!?])(\s|$)", line)
    return (match.group(1) if match else line).strip()


def local_knowledge_fallback(query: str, lang: str) -> Optional[str]:
    """
    Respuesta local (sin IA) ensamblada según el TEMA y la INTENCIÓN de la consulta, en `lang`, o None si la
    consulta no corresponde a ningún módulo. «¿Qué es…?» ya no devuelve el tutorial de instalación:
      concept → qué es + plan · functions → funciones + plan · concept_and_functions → qué es + funciones + plan ·
      benefits_and_plans → plan + funciones · setup → qué es (1 frase) + pasos · full → qué es + funciones + pasos.
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
    guide = _LOCAL_GUIDES.get(best)
    if not guide:
        return None
    idx = 1 if lang == "en" else 0
    intent = detect_query_intent(query)
    if best == "human":
        intent = "functions"   # contacto directo, sin secciones de producto
    pro, ultra = _plan_prices()
    parts = []
    for section in _INTENT_SECTIONS.get(intent, _INTENT_SECTIONS["full"]):
        if section == "what_is_short":
            parts.append(_first_sentence(guide["what_is"][idx]))
            continue
        body = _render_guide_text(guide[section][idx], pro, ultra)
        if section != "what_is" and len(parts) > 0:
            body = f"{_SECTION_HEADERS[section][idx]}:\n{body}"
        parts.append(body)
    return "\n\n".join(p for p in parts if p).strip()[:SUPPORT_REPLY_MAX_CHARS]


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


_INTENT_GUIDANCE = {
    "concept": "El usuario pregunta QUÉ ES. Explica qué es el módulo, su propósito en The Bunker OS y qué problema "
               "resuelve; menciona en qué plan está. NO des pasos de instalación salvo una línea final ofreciéndolos.",
    "functions": "El usuario pregunta por las FUNCIONES / qué hace / cómo funciona. Enumera y explica TODAS las funciones "
                 "operativas en tiempo real del módulo (con cifras, límites y permisos) y en qué plan está. «Cómo "
                 "funciona» NO es «cómo se configura»: no conviertas la respuesta en un tutorial.",
    "concept_and_functions": "El usuario pregunta QUÉ ES y CUÁLES SON SUS FUNCIONES. Responde en dos partes rotuladas: "
                             "1) Qué es; 2) Funciones operativas (todas, numeradas). Cierra con el plan en una línea.",
    "benefits_and_plans": "El usuario pregunta por BENEFICIOS / PLANES / PRECIOS. Detalla las ventajas y la comparativa "
                          "exacta Free (0 ⭐) vs PRO vs ULTRA PRO con precios, límites y qué se gana al subir.",
    "setup": "El usuario pregunta CÓMO PROGRAMAR / CONFIGURAR / CONECTAR / ACTIVAR. Da el instructivo paso a paso con la "
             "ruta exacta de botones en el Bot y en la Mini App, requisitos y permisos.",
    "full": "Pregunta general o solo el nombre del módulo: da un resumen de qué es, sus funciones clave y cómo "
            "configurarlo (Bot y Mini App).",
}


def _support_system_prompt(effective_lang: str, directives: str, pii_note: str, query_intent: str = "full") -> str:
    language = "ESPAÑOL" if effective_lang == "es" else "INGLÉS (ENGLISH)"
    intent = query_intent if query_intent in _INTENT_GUIDANCE else "full"
    return (
        "Eres el Arquitecto de Soporte IA oficial de The Bunker Command OS (Cloud Media Management). "
        "Tu misión es ayudar a administradores, creadores y usuarios explicando con claridad, precisión y "
        "paso a paso cómo funciona cada módulo del bot y de la Mini App y cómo configurarlo.\n\n"
        "REGLAS OBLIGATORIAS DE COMPORTAMIENTO:\n"
        f"1. IDIOMA OBLIGATORIO: El usuario te está escribiendo en {language}. Responde ÚNICA Y EXCLUSIVAMENTE en "
        f"{language}, sin importar el idioma del menú ni de los mensajes anteriores."
        + (" Respond MANDATORILY in ENGLISH and quote buttons with their English labels from the ES ↔ EN map."
           if effective_lang == "en" else "") + "\n"
        "2. REGLA DE DIFERENCIACIÓN DE INTENCIÓN (CRÍTICA): analiza qué pregunta exactamente el usuario.\n"
        "   • «¿Qué es X?» → qué es, propósito arquitectónico y problema que resuelve; sin saturar con instalación.\n"
        "   • «¿Cuáles son las funciones de X? / ¿Qué hace X? / ¿Cómo funciona X?» → todas sus capacidades y funciones "
        "operativas en tiempo real (p. ej. el Centinela: AutoLower, MicVIP, Modo Podcast, Escudo Antinota, Escudo "
        "Antirruido, reinicio de 3,5 h, Programador VC, avisos, Guardián IA, Copiloto y Ghost Purge).\n"
        "   • «¿Qué es X y cuáles son sus funciones?» → dos partes: 1) Qué es; 2) Todas sus funciones; y el plan.\n"
        "   • «¿Beneficios / planes / precios?» → ventajas y comparativa exacta Free (0 ⭐), PRO y ULTRA PRO.\n"
        "   • «¿Cómo lo programo / configuro / conecto / activo?» → instructivo paso a paso con la ruta exacta de "
        "botones en el Bot y en la Mini App.\n"
        f"   Intención detectada para ESTA pregunta: {intent}. {_INTENT_GUIDANCE[intent]}\n"
        "3. PROHIBIDO DERIVAR INNECESARIAMENTE A SOPORTE HUMANO: Tienes toda la documentación oficial del sistema abajo "
        "(arquitectura, mapa de menús, mensajes de servicio, planes y enciclopedia de módulos). Cuando te pregunten qué "
        "es, qué hace, cómo funciona o cómo se programa cualquier función (Captcha, AutoLower, MicVIP, Cerraduras, "
        "Anti-Spam, Anti-Flood, Bot Clon, Centinela, Planes de Canal, Propinas, Ghost Purge, Roles VIP, Apelaciones, "
        "Cuotas de enlaces, Modo Nocturno, Pánico, Gamificación, Telemetría, Canal de Registro o Backups), RESPONDE TÚ "
        f"MISMO. SOLO menciona a {SUPPORT_HUMAN_CONTACT} si el usuario pide expresamente hablar con un humano, reporta "
        "un pago de Stars no acreditado o solicita una arquitectura empresarial a medida.\n"
        "4. FORMATO LIMPIO Y DIDÁCTICO: texto plano (sin HTML, sin Markdown, sin ** ni #, sin bloques ```), viñetas "
        "simples (• o 1., 2., 3.), emojis tácticos moderados, botones citados entre comillas «» tal como aparecen. "
        "Extensión: 60–150 palabras para preguntas simples; hasta 350 palabras cuando pidan todas las funciones, "
        "comparativas de planes o guías completas. Nunca cortes una lista a la mitad.\n"
        "5. FIDELIDAD ESTRICTA: usa solo los precios, límites, permisos, rutas y mensajes de la base de conocimientos y "
        "las directivas. Nunca inventes precios, comandos, botones ni funciones. Si algo no figura, dilo con honestidad "
        "y ofrece la ruta más cercana que sí exista.\n"
        "6. SEGURIDAD DE CREDENCIALES: nunca pidas ni aceptes tokens de @BotFather, String Sessions, números de "
        "teléfono, códigos SMS ni contraseñas 2FA en este chat; se vinculan únicamente en el menú privado «🧬 Clon & "
        "Centinela» del bot.\n"
        f"7. {pii_note}\n\n"
        f"{support_knowledge_base()}\n"
        + (f"\nDIRECTIVAS ADICIONALES DEL ARQUITECTO:\n{directives}\n" if directives else "")
    )


def _local_reply(text: str, lang: str, reason: str, detail: str = "") -> dict:
    _log_support_degraded(reason, detail)
    guide = local_knowledge_fallback(text, lang)
    return {"reply": guide or _support_fallback(lang), "fallback": True,
            "source": "local_kb" if guide else "generic", "lang": lang, "intent": detect_query_intent(text)}


async def support_ai_reply(user_id: int, message: str, lang: str = "es") -> dict:
    """
    Respuesta del Arquitecto de Soporte → {'reply', 'fallback', 'source', 'lang', 'intent'}.
      • lang = idioma de la interfaz; el idioma EFECTIVO se detecta del propio mensaje (detect_message_lang).
      • intent = detect_query_intent(): qué es / funciones / beneficios / configuración / completo.
      • source: 'mistral' | 'local_kb' (guía local por tema e intención) | 'generic' (sin tema reconocible).
    Lanza SupportRateLimited (con .lang efectivo) si el usuario envía demasiado. El historial se guarda en
    ai_chat_context con chat_id = user_id (positivo: no colisiona con los chats del Copiloto) y SIEMPRE ya minimizado
    por la Capa 0.
    """
    ui_lang = "en" if lang == "en" else "es"
    uid = int(user_id)
    text = _CONTROL_CHARS_RE.sub("", str(message or "")).strip()[:SUPPORT_MAX_INPUT]
    effective_lang = detect_message_lang(text, default=ui_lang)
    query_intent = detect_query_intent(text)
    if not text:
        return {"reply": ("¿En qué te ayudo? Pregúntame por planes, comandos o la configuración de cualquier módulo."
                          if effective_lang == "es" else
                          "How can I help? Ask me about plans, commands or how to set up any module."),
                "fallback": True, "source": "generic", "lang": effective_lang, "intent": query_intent}
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

    try:
        sanitize = getattr(assistant, "sanitize_pii_for_ai", None) or (lambda value: value)
        safe_text = str(sanitize(text))
        directives = str(sanitize(await get_support_directives()))
        system = _support_system_prompt(effective_lang, directives, getattr(assistant, "PII_PLACEHOLDER_NOTE", ""),
                                        query_intent)
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        try:
            history = await _db().get_ai_chat_context(uid, limit=6)
        except Exception:
            history = []
        for item in history or []:
            if isinstance(item, dict) and item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": str(item["role"]), "content": str(sanitize(str(item["content"])))[:1500]})
        # Mistral exige que el último turno sea del usuario y alterna roles: se descartan restos inconsistentes.
        while len(messages) > 1 and messages[-1]["role"] == "user":
            messages.pop()
        messages.append({"role": "user", "content": safe_text})
    except Exception as ex:
        logger.error(f"🆘 [Soporte IA] No se pudo preparar la consulta de {uid}: {type(ex).__name__}: {ex}")
        return _local_reply(text, effective_lang, "prompt_build_failed", f"({type(ex).__name__})")

    timeout = max(float(getattr(assistant, "MISTRAL_TIMEOUT_SECONDS", 25.0) or 25.0), 35.0)
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
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", reply or "").replace("**", "")
    reply = re.sub(r"(?m)^#{1,6}\s*", "", reply).strip()[:SUPPORT_REPLY_MAX_CHARS]
    if not reply:
        return _local_reply(text, effective_lang, "mistral_empty_reply")
    try:
        await _db().save_ai_chat_context(uid, uid, "user", safe_text)
        await _db().save_ai_chat_context(uid, 0, "assistant", str(sanitize(reply)))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo guardar el contexto de {uid}: {ex}")
    return {"reply": reply, "fallback": False, "source": "mistral", "lang": effective_lang, "intent": query_intent}


async def support_ai_diagnostics(run_probe: bool = True) -> dict:
    """
    v32 · Diagnóstico del Soporte IA para Arquitectos (/aidiag en el bot): estado del SDK, del cliente y de los
    disyuntores y, con run_probe, una llamada real mínima a Mistral. Nunca expone la API key.
    """
    assistant = _assistant()
    if assistant is None:
        return {"ok": False, "stage": "import", "detail": "assistant.py no se pudo importar"}
    status_fn = getattr(assistant, "mistral_status", None)
    status = status_fn() if callable(status_fn) else {"client_ready": getattr(assistant, "mistral_client", None) is not None}
    out = {"ok": bool(status.get("client_ready")), "stage": "client", "status": status,
           "kb_chars": len(support_knowledge_base())}
    if not out["ok"] or not run_probe:
        return out
    started = time.monotonic()
    try:
        response = await assistant._mistral_complete(
            "diagnostic", timeout=30.0, model=getattr(assistant, "MISTRAL_MODEL", "mistral-small-latest"),
            messages=[{"role": "user", "content": "Responde solo: OK"}], max_tokens=5, temperature=0,
        )
        out.update(ok=True, stage="probe", latency_ms=int((time.monotonic() - started) * 1000),
                   sample=assistant._extract_mistral_text(response)[:40])
    except Exception as ex:
        describe = getattr(assistant, "describe_mistral_error", None)
        out.update(ok=False, stage="probe", detail=describe(ex) if callable(describe) else f"{type(ex).__name__}: {ex}")
    return out


async def support_ai_reset(user_id: int) -> None:
    try:
        await _db().clear_ai_chat_context(int(user_id))
    except Exception as ex:
        logger.debug(f"[Soporte IA] No se pudo limpiar el contexto de {user_id}: {ex}")


def escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)