"""
media_locks.py — The Bunker OS · Matriz granular de bloqueo multimedia y vectores de evasión (v15)

Inspirada en Group Help / ChatKeeper: 30 vectores en 3 páginas, cada uno con su propia acción
(☑️ Off · 🗑️ Eliminación · ❕ Warn · ❗ Kick · 🔇 Silenciar · 🚫 Ban) y su interruptor de borrado.

Este módulo contiene:
  • CATALOG — los 30 vectores (icono, nombre ES/EN, descripción, página).
  • detect_vectors() — inspector puro de un mensaje (multimedia, enlaces camuflados con caracteres invisibles
    u homoglifos, reenvíos, historias, citas externas, botones inline, spoilers, mayúsculas, etc.).
  • inspect_and_enforce() — punto de entrada desde handlers/groups.group_security_matrix (mensajes y EDICIONES).
  • handle_bot_added() — vector 29: bot invitado por un miembro sin privilegios.
  • Panel paginado de la consola privada (callbacks `mlk_…`), exclusivo del Dueño legítimo, del Arquitecto o de
    un delegado con `can_manage_locks` (misma verificación que el resto de la consola: verify_admin_privileges).

Persistencia: database.media_locks_config (JSON) + long_video_max_seconds, sincronizados con las cerraduras
heredadas lock_media / lock_stickers / lock_links (ver bloque v15 de database.py).

Importaciones diferidas: handlers.groups y handlers.user_private importan este módulo, así que sus utilidades
(enforce_warn_ladder, track_security, verify_admin_privileges, …) se resuelven en tiempo de llamada.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time
from collections import OrderedDict, deque
from typing import Optional

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.database import (
    MEDIA_LOCK_ACTIONS,
    MEDIA_LOCK_KEYS,
    MEDIA_LOCK_SEVERITY,
    LEGACY_LOCK_GROUPS,
    LONG_VIDEO_RANGE,
    FWD_BURST_LIMITS,
    FWD_BURST_WINDOWS,
    BunkerValidationError,
    add_authorized_bot,
    ban_user,
    effective_media_lock_rule,
    get_lock_status,
    get_media_locks_config,
    get_shield_config,
    remove_authorized_bot,
    set_fwd_burst,
    set_lock_status,
    set_long_video_max_seconds,
    set_media_lock_rule,
    toggle_media_lock_delete,
)

logger = logging.getLogger("media_locks")
router = Router(name="media_locks")

SIG = "\n\n🛡️ <i>Cloud Media Management</i>"
PAGE_SIZE = 10
PAGES = 3
LONG_VIDEO_CHOICES = (300, 600)
LONG_TEXT_LIMIT = 1000
CAPS_MIN_LETTERS = 12
CAPS_RATIO = 0.70
RICH_ENTITY_LIMIT = 6
LARGE_VIDEO_BYTES = 50 * 1024 * 1024
DANGEROUS_EXTENSIONS = (".apk", ".exe", ".scr", ".bat", ".msi", ".zip", ".rar", ".7z", ".cmd", ".com", ".vbs", ".js",
                        ".jar", ".ps1", ".dll", ".xapk", ".apks", ".iso")
ALBUM_TTL_SECONDS = 120
# Vectores que, si aparecen en un mensaje EDITADO, delatan una inyección posterior (vector 17 · edited_msg).
EDIT_INJECTION_VECTORS = ("masked_links", "tg_links", "spoiler_text", "inline_buttons", "rich_message", "external_reply")

# ==========================================
# 📚 CATÁLOGO (orden = páginas 1, 2 y 3)
# ==========================================
CATALOG = {
    # ---- Página 1 · Multimedia y archivos ----
    "photo": ("📸", "Fotos", "Photos", "Imágenes comprimidas.", "Compressed images."),
    "video": ("🎬", "Videos", "Videos", "Videos regulares.", "Regular videos."),
    "long_video": ("🎞️", "Películas", "Movies", "Videos que superan el umbral de duración (5 o 10 min) o pesan más de 50 MB.",
                   "Videos over the duration threshold (5 or 10 min) or larger than 50 MB."),
    "animation": ("👾", "GIFs", "GIFs", "GIFs y animaciones MP4 sin sonido.", "GIFs and silent MP4 animations."),
    "sticker": ("🎨", "Stickers", "Stickers", "Stickers estáticos, animados y de video.", "Static, animated and video stickers."),
    "voice": ("🎤", "Notas de voz", "Voice notes", "Audios grabados en vivo.", "Recorded voice messages."),
    "audio": ("🎵", "Música / Audio", "Music / Audio", "Archivos MP3, FLAC, WAV…", "MP3, FLAC, WAV… files."),
    "video_note": ("⚪", "Notas de video", "Video notes", "Videos circulares.", "Round video messages."),
    "document": ("📁", "Archivos", "Files", "Documentos y archivos; marca los peligrosos (.apk, .exe, .scr, .bat, .msi, .zip…).",
                 "Documents and files; flags dangerous ones (.apk, .exe, .scr, .bat, .msi, .zip…)."),
    "media_group": ("🖼️", "Álbumes", "Albums", "Ráfagas de fotos o videos agrupados (una sola sanción por álbum).",
                    "Grouped photo/video bursts (one sanction per album)."),
    # ---- Página 2 · Enlaces, reenvíos, citas, botones inline y ediciones ----
    "masked_links": ("🔗", "Enlaces / camuflados", "Links / masked",
                     "URLs y dominios, enlaces ocultos tras texto normal (text_link) y enlaces ofuscados con caracteres invisibles u homoglifos.",
                     "URLs and domains, links hidden behind normal text (text_link) and links obfuscated with invisible characters or homoglyphs."),
    "tg_links": ("📘", "Enlaces de Telegram", "Telegram links", "t.me, telegram.me, tg:// e invitaciones +hash.",
                 "t.me, telegram.me, tg:// and +hash invites."),
    "forward_any": ("📥", "Reenvíos", "Forwards", "Mensajes reenviados desde canales, grupos, usuarios o bots externos.",
                    "Messages forwarded from external channels, groups, users or bots."),
    "forward_story": ("📖", "Historias", "Stories", "Historias de Telegram reenviadas.", "Forwarded Telegram stories."),
    "external_reply": ("💬", "Citas externas", "External quotes", "Respuestas que citan mensajes de otro chat o canal.",
                       "Replies quoting messages from another chat or channel."),
    "inline_buttons": ("⌨️", "Botones inline", "Inline buttons", "Mensajes de miembros o userbots con teclado inline (phishing).",
                       "Member or userbot messages with an inline keyboard (phishing)."),
    "edited_msg": ("✏️", "Ediciones maliciosas", "Malicious edits",
                   "Mensaje que, al EDITARSE, inyecta enlaces, enlaces de Telegram, spoilers, botones, formato abusivo, citas externas o palabras de la lista negra.",
                   "A message that, when EDITED, injects links, Telegram links, spoilers, buttons, abusive formatting, external quotes or blacklisted words."),
    "poll": ("📊", "Encuestas", "Polls", "Encuestas y cuestionarios.", "Polls and quizzes."),
    "contact": ("👤", "Contactos", "Contacts", "Tarjetas de contacto compartidas.", "Shared contact cards."),
    "long_text": ("📝", "Textos largos", "Long texts", f"Mensajes de más de {LONG_TEXT_LIMIT} caracteres.",
                  f"Messages longer than {LONG_TEXT_LIMIT} characters."),
    # ---- Página 3 · Evasión avanzada ----
    "caps": ("🆎", "Mayúsculas", "Caps", f"Más del {int(CAPS_RATIO * 100)} % en mayúsculas (mín. {CAPS_MIN_LETTERS} letras).",
             f"Over {int(CAPS_RATIO * 100)} % uppercase (min. {CAPS_MIN_LETTERS} letters)."),
    "location": ("📍", "Ubicación", "Location", "Ubicaciones fijas, en tiempo real y lugares.", "Static, live locations and venues."),
    "game": ("🎮", "Juegos", "Games", "Juegos HTML5.", "HTML5 games."),
    "invoice": ("💶", "Pagos / Facturas", "Payments / Invoices", "Facturas de cobro.", "Payment invoices."),
    "spoiler_text": ("💭", "Spoiler de texto", "Text spoiler", "Texto oculto con formato spoiler.", "Text hidden with spoiler formatting."),
    "spoiler_media": ("🌌", "Spoiler multimedia", "Media spoiler", "Fotos o videos difuminados.", "Blurred photos or videos."),
    "giveaway": ("🎁", "Sorteos", "Giveaways", "Sorteos nativos y sus ganadores.", "Native giveaways and their winners."),
    "via_bot": ("🤖", "Bot inline", "Inline bot", "Mensajes enviados a través de bots inline (@gif, @vid…).",
                "Messages sent via inline bots (@gif, @vid…)."),
    "bot_added": ("🛸", "Bot invitado", "Bot added",
                  "Un miembro sin privilegios añade un bot: el bot se expulsa al instante y quien lo invitó recibe la sanción.",
                  "A non-privileged member adds a bot: the bot is removed at once and the inviter is sanctioned."),
    "rich_message": ("🔣", "Mensaje enriquecido", "Rich message",
                     f"{RICH_ENTITY_LIMIT}+ bloques de cita, código o emojis personalizados para simular anuncios oficiales.",
                     f"{RICH_ENTITY_LIMIT}+ quote, code or custom-emoji blocks faking official notices."),
}
assert tuple(CATALOG) == MEDIA_LOCK_KEYS, "El catálogo debe seguir el orden de database.MEDIA_LOCK_KEYS"

ACTION_ICONS = {"off": "☑️", "del": "🗑️", "warn": "❕", "kick": "❗", "mute": "🔇", "ban": "🚫"}
ACTION_NAMES = {
    "off": ("Off", "Off"), "del": ("Eliminación", "Delete"), "warn": ("Warn", "Warn"),
    "kick": ("Kick", "Kick"), "mute": ("Silenciar", "Mute"), "ban": ("Ban", "Ban"),
}
ACTION_CODES = {"o": "off", "w": "warn", "k": "kick", "m": "mute", "b": "ban"}
PAGE_TITLES = {
    1: ("📄 Página 1 · Multimedia y archivos", "📄 Page 1 · Media & files"),
    2: ("📄 Página 2 · Enlaces, reenvíos y ediciones", "📄 Page 2 · Links, forwards & edits"),
    3: ("📄 Página 3 · Evasión avanzada", "📄 Page 3 · Advanced evasion"),
}
# Motivo legible para la escalera de advertencias (se fusiona en groups._REASON_TEXT).
REASON_TEXTS = {
    f"lock_{key}": (f"tu mensaje fue retirado: {meta[0]} {meta[1].lower()} no está permitido en esta comunidad.",
                    f"your message was removed: {meta[0]} {meta[2].lower()} is not allowed in this community. Strike logged.")
    for key, meta in CATALOG.items()
}


def tr(lang: str, es: str, en: str) -> str:
    return es if lang == "es" else en


def label(key: str, lang: str) -> str:
    meta = CATALOG[key]
    return f"{meta[0]} {meta[1] if lang == 'es' else meta[2]}"


# ==========================================
# 🔎 INSPECTOR DE VECTORES
# ==========================================
_ZW_RE = re.compile("[​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎]")
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
    "һ": "h", "ӏ": "l", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "ո": "n", "ս": "u", "ɡ": "g", "ν": "v",
    "ο": "o", "ρ": "p", "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t", "υ": "u", "χ": "x",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "В": "B", "М": "M", "Н": "H", "К": "K", "Т": "T",
    "。": ".", "．": ".", "｡": ".", "⁄": "/", "∕": "/",
})
_TG_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|telesco\.pe)/\S+|tg://\S+", re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_TLDS = ("com|net|org|io|me|xyz|ru|co|info|biz|top|site|online|link|club|app|dev|ly|gg|tk|cc|to|in|us|uk|es|mx|ar|"
         "cl|pe|ve|br|shop|store|live|pro|vip|win|bet|cash|click|fun|icu|cyou|buzz|sbs|monster|lol|ws|su|ua|tv|gl|"
         "eu|de|fr|it|nl|pw|ml|ga|cf|gq|xn--[a-z0-9-]+")
_DOMAIN_RE = re.compile(rf"(?<![@\w.])(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})(?:/[^\s<>\"']*)?(?![\w.])",
                        re.IGNORECASE)
_RICH_TYPES = {"blockquote", "expandable_blockquote", "pre", "code", "custom_emoji"}


def _etype(entity) -> str:
    return str(getattr(getattr(entity, "type", None), "value", getattr(entity, "type", "")) or "")


def _normalize_text(text: str) -> str:
    return _ZW_RE.sub("", text or "").translate(_HOMOGLYPHS)


def _is_tg_url(url: str) -> bool:
    return bool(_TG_RE.search(url or ""))


def _hidden_urls(message: Message) -> list:
    """
    v23 · URLs que NO están en el texto visible: vista previa forzada (link_preview_options.url) y botones URL de un
    teclado inline (mensajes reenviados de bots o enviados vía bot inline). Son la forma más común de camuflar un enlace.
    """
    out = []
    preview = getattr(message, "link_preview_options", None)
    if preview is not None and getattr(preview, "url", None):
        out.append(("vista previa", str(preview.url)))
    markup = getattr(message, "reply_markup", None)
    for row in (getattr(markup, "inline_keyboard", None) or []):
        for button in row or []:
            url = getattr(button, "url", None) or getattr(getattr(button, "login_url", None), "url", None)
            if url:
                out.append(("botón", str(url)))
    return out[:20]


def _link_scan(message: Message, text: str) -> dict:
    """Análisis de enlaces: {'web': bool, 'tg': bool, 'masked': bool, 'detail': str}."""
    entities = list(message.entities or []) + list(message.caption_entities or [])
    web = tg = masked = False
    detail = ""
    for where, url in _hidden_urls(message):
        masked = True
        detail = detail or where
        if _is_tg_url(url):
            tg = True
        else:
            web = True
    for ent in entities:
        kind = _etype(ent)
        if kind == "text_link" and getattr(ent, "url", None):
            masked = True
            detail = detail or "text_link"
            if _is_tg_url(ent.url):
                tg = True
            else:
                web = True
        elif kind == "url":
            visible = ""
            with contextlib.suppress(Exception):
                visible = ent.extract_from(text)
            if _is_tg_url(visible):
                tg = True
            else:
                web = True

    raw_tg = bool(_TG_RE.search(text))
    raw_web = bool(_URL_RE.search(text) or _DOMAIN_RE.search(text))
    norm = _normalize_text(text)
    norm_tg = bool(_TG_RE.search(norm))
    norm_web = bool(_URL_RE.search(norm) or _DOMAIN_RE.search(norm))
    tg = tg or raw_tg or norm_tg
    if raw_web or norm_web:
        # Un enlace de Telegram también casa con el patrón web: solo cuenta como web si hay algo más.
        without_tg = _TG_RE.sub(" ", norm)
        if _URL_RE.search(without_tg) or _DOMAIN_RE.search(without_tg):
            web = True
    if (norm_tg and not raw_tg) or (norm_web and not raw_web):
        masked = True
        detail = detail or "ofuscado"   # solo aparece tras quitar invisibles / homoglifos
    return {"web": web, "tg": tg, "masked": masked, "detail": detail}


def _origin_bot_username(message: Message) -> str:
    """Alias (minúsculas) del bot de origen de un reenvío o de un mensaje vía bot inline; '' si no hay bot."""
    via = getattr(message, "via_bot", None)
    if via is not None and getattr(via, "username", None):
        return via.username.lower()
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) if origin is not None else None
    if sender is not None and getattr(sender, "is_bot", False) and getattr(sender, "username", None):
        return sender.username.lower()
    return ""


def detect_vectors(message: Message, long_video_seconds: int, bot_id: Optional[int] = None,
                   authorized_bots: frozenset = frozenset()) -> list:
    """
    Vectores presentes en el mensaje → [(vector, detalle)]. Puro (sin E/S). No evalúa reglas: solo detecta.
    'bot_added' y 'edited_msg' no se detectan aquí (son eventos, no contenido).
    v23: authorized_bots = alias (minúsculas) que el dueño autorizó. Un mensaje vía / reenviado desde uno de ellos
    no dispara via_bot, inline_buttons ni forward_any (sí los enlaces y el resto de vectores de contenido).
    """
    found = []
    add = found.append
    text = message.text or message.caption or ""
    origin_bot = _origin_bot_username(message)
    own_via = bot_id is not None and getattr(getattr(message, "via_bot", None), "id", None) == bot_id
    trusted_bot = own_via or (bool(origin_bot) and origin_bot in authorized_bots)

    # ---- Página 1 ----
    if message.photo:
        add(("photo", ""))
    video = message.video
    if video:
        add(("video", ""))
        duration = int(getattr(video, "duration", 0) or 0)
        size = int(getattr(video, "file_size", 0) or 0)
        if duration > long_video_seconds or size > LARGE_VIDEO_BYTES:
            add(("long_video", f"{duration // 60} min" if duration else f"{size // (1024 * 1024)} MB"))
    if message.animation:
        add(("animation", ""))
        # v23 · Película camuflada como GIF (MP4 "animación" sin sonido de larga duración).
        anim_duration = int(getattr(message.animation, "duration", 0) or 0)
        anim_size = int(getattr(message.animation, "file_size", 0) or 0)
        if anim_duration > long_video_seconds or anim_size > LARGE_VIDEO_BYTES:
            add(("long_video", f"GIF {anim_duration // 60} min" if anim_duration else f"GIF {anim_size // (1024 * 1024)} MB"))
    if message.sticker:
        add(("sticker", ""))
    if message.voice:
        add(("voice", ""))
    if message.audio:
        add(("audio", ""))
    if message.video_note:
        add(("video_note", ""))
    document = message.document
    if document and not message.animation:
        name = (getattr(document, "file_name", "") or "").lower()
        dangerous = next((ext for ext in DANGEROUS_EXTENSIONS if name.endswith(ext)), "")
        add(("document", f"⚠️ {dangerous}" if dangerous else ""))
        mime = (getattr(document, "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            add(("video", "documento de video"))
            if int(getattr(document, "file_size", 0) or 0) > LARGE_VIDEO_BYTES:
                add(("long_video", f"{int(document.file_size) // (1024 * 1024)} MB"))
    if message.media_group_id:
        add(("media_group", ""))

    # ---- Página 2 ----
    if text or message.entities or message.caption_entities or _hidden_urls(message):
        links = _link_scan(message, text)
        if links["web"] or links["masked"]:
            add(("masked_links", links["detail"]))
        if links["tg"]:
            add(("tg_links", ""))
    origin = getattr(message, "forward_origin", None)
    if origin is not None and not getattr(message, "is_automatic_forward", False) and not trusted_bot:
        origin_chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        if not (origin_chat is not None and getattr(origin_chat, "id", None) == message.chat.id):
            kind = type(origin).__name__.replace("MessageOrigin", "").lower()
            if origin_bot:
                kind = f"bot @{origin_bot}"   # v23 · reenvío desde un bot externo
            add(("forward_any", kind))
    if getattr(message, "story", None) is not None:
        add(("forward_story", ""))
    if getattr(message, "external_reply", None) is not None:
        add(("external_reply", ""))
    markup = getattr(message, "reply_markup", None)
    if markup is not None and getattr(markup, "inline_keyboard", None) and not trusted_bot:
        add(("inline_buttons", f"@{origin_bot}" if origin_bot else ""))
    if message.poll:
        add(("poll", ""))
    if message.contact:
        add(("contact", ""))
    if len(text) > LONG_TEXT_LIMIT:
        add(("long_text", f"{len(text)} car."))

    # ---- Página 3 ----
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= CAPS_MIN_LETTERS and sum(1 for c in letters if c.isupper()) / len(letters) > CAPS_RATIO:
        add(("caps", ""))
    if message.location or getattr(message, "venue", None):
        add(("location", ""))
    if getattr(message, "game", None) is not None:
        add(("game", ""))
    if getattr(message, "invoice", None) is not None:
        add(("invoice", ""))
    entities = list(message.entities or []) + list(message.caption_entities or [])
    if any(_etype(e) == "spoiler" for e in entities):
        add(("spoiler_text", ""))
    if getattr(message, "has_media_spoiler", False):
        add(("spoiler_media", ""))
    if getattr(message, "giveaway", None) is not None or getattr(message, "giveaway_winners", None) is not None:
        add(("giveaway", ""))
    via = getattr(message, "via_bot", None)
    if via is not None and getattr(via, "id", None) != bot_id and not trusted_bot:
        add(("via_bot", f"@{via.username}" if getattr(via, "username", None) else ""))
    rich = sum(1 for e in entities if _etype(e) in _RICH_TYPES)
    if rich >= RICH_ENTITY_LIMIT:
        add(("rich_message", f"{rich} bloques"))
    return found


def _most_severe(candidates: list) -> Optional[tuple]:
    """[(vector, detalle, regla)] → el de mayor severidad; ante empate, el primero del catálogo."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: (MEDIA_LOCK_SEVERITY[c[2]["action"]], -MEDIA_LOCK_KEYS.index(c[0])))


def evaluate_message(message: Message, state: dict, *, is_edit: bool = False, blacklist_hit: str = "",
                     bot_id: Optional[int] = None, authorized_bots: frozenset = frozenset(),
                     exempt: frozenset = frozenset(), original: Optional[frozenset] = None,
                     found: Optional[list] = None) -> Optional[dict]:
    """
    Decide la sanción de un mensaje. Devuelve {'key', 'detail', 'action', 'delete', 'edit'} o None si se permite.
    Una sola sanción por mensaje (la más severa); el borrado se aplica si CUALQUIER regla implicada lo pide.
    En EDICIONES se aplican las reglas de cada vector y, además, la de 'edited_msg' si la edición inyecta un
    vector de EDIT_INJECTION_VECTORS o una palabra de la lista negra.
    v23:
      • exempt: vectores eximidos por un rol VIP (nunca durante el Pánico / modo nocturno: el piso manda).
      • original: huella de vectores del mensaje ANTES de editarse. Si se conoce, la edición solo se evalúa por
        lo que AÑADE (no se re-sanciona contenido ya juzgado al publicarse); sin huella, criterio heredado.
    """
    if found is None:
        found = detect_vectors(message, state["long_video_max_seconds"], bot_id=bot_id, authorized_bots=authorized_bots)
    floor = state.get("floor", ())
    candidates = []
    for key, detail in found:
        if is_edit and original is not None and key in original:
            continue
        if key in exempt and key not in floor:
            continue
        rule = effective_media_lock_rule(state, key)
        if rule["action"] != "off":
            candidates.append((key, detail, rule))
    if is_edit:
        edit_rule = effective_media_lock_rule(state, "edited_msg")
        injected = [(k, d) for k, d in found if k in EDIT_INJECTION_VECTORS and (original is None or k not in original)]
        if blacklist_hit:
            injected.append(("edited_msg", f"lista negra: {blacklist_hit}"))
        if edit_rule["action"] != "off" and injected:
            key, detail = injected[0]
            candidates.append(("edited_msg", f"{label(key, 'es') if key != 'edited_msg' else ''} {detail}".strip(), edit_rule))
    chosen = _most_severe(candidates)
    if chosen is None:
        return None
    key, detail, rule = chosen
    delete = rule["action"] == "del" or any(c[2]["delete"] for c in candidates)
    return {"key": key, "detail": detail, "action": rule["action"], "delete": delete, "edit": is_edit}


# ==========================================
# ⚖️ EJECUCIÓN DE SANCIONES
# ==========================================
_album_seen: dict = {}   # (chat, user, media_group_id) -> marca de tiempo


def _album_already_sanctioned(chat_id: int, user_id: int, group_key: Optional[str]) -> bool:
    if not group_key:
        return False
    now = time.monotonic()
    if len(_album_seen) > 2000:
        for k in [k for k, ts in _album_seen.items() if now - ts > ALBUM_TTL_SECONDS]:
            _album_seen.pop(k, None)
    key = (chat_id, user_id, group_key)
    if now - _album_seen.get(key, 0) < ALBUM_TTL_SECONDS:
        return True
    _album_seen[key] = now
    return False


def _sanction_notice(action: str, mention: str, reason_es: str, reason_en: str) -> str:
    icon, title_es, title_en = {
        "kick": ("👢", "Expulsión", "Kick"),
        "mute": ("🔇", "Silencio", "Mute"),
        "ban": ("🚫", "Bloqueo definitivo", "Ban"),
    }[action]
    return (
        f"{icon} <b>{title_es} / {title_en}:</b>\n"
        f"{mention} — {html.escape(reason_es)}.\n"
        f"🇺🇸 <i>{html.escape(reason_en)}.</i>" + SIG
    )


async def apply_sanction(bot: Bot, chat_id: int, user, verdict: dict, message: Optional[Message] = None) -> None:
    """
    Ejecuta la decisión: borra si procede y aplica Warn (escalera de strikes), Kick, Mute (chat + llamada de voz)
    o Ban. Registra el bloqueo en la analítica de seguridad. Nunca lanza.
    """
    from handlers import groups as g   # importación diferida (groups importa este módulo)

    user_id = user.id
    username = getattr(user, "username", "") or ""
    mention = user.mention_html() if hasattr(user, "mention_html") else f"<a href='tg://user?id={user_id}'>{user_id}</a>"
    key, action = verdict["key"], verdict["action"]
    meta = CATALOG[key]
    edit_es = " (mensaje editado)" if verdict.get("edit") else ""
    edit_en = " (edited message)" if verdict.get("edit") else ""
    detail = f" · {verdict['detail']}" if verdict.get("detail") else ""
    reason_es = f"{meta[0]} {meta[1]}{edit_es}{detail}"
    reason_en = f"{meta[0]} {meta[2]}{edit_en}{detail}"

    album = getattr(message, "media_group_id", None) if message is not None else None
    repeated_album = _album_already_sanctioned(chat_id, user_id, album)

    if message is not None and verdict.get("delete"):
        try:
            await message.delete()
            g.track_security(chat_id, user_id, "antispam", "delete", bool(getattr(user, "is_bot", False)))
        except Exception as ex:
            logger.debug(f"[MediaLocks] No se pudo borrar en {chat_id}: {ex}")

    if action in ("off", "del") or repeated_album:
        return

    logger.info(f"🔒 [MediaLocks] {action.upper()} a {user_id} en {chat_id} por {key}{edit_es}{detail}")
    try:
        if action == "warn":
            await g.enforce_warn_ladder(bot, chat_id, user_id, username, mention,
                                        reply_to=None if verdict.get("delete") else message, reason=f"lock_{key}")
            return
        if action == "mute":
            await g._acoustic_attenuate(bot, chat_id, user_id, username, strikes=1, limit=1, force_mute=True)
            await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
        elif action == "kick":
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=int(time.time() + 35))
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            await g.registry_forget(chat_id, user_id)
        elif action == "ban":
            await ban_user(user_id)
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await g.registry_forget(chat_id, user_id)
        g.track_security(chat_id, user_id, "antispam", action, bool(getattr(user, "is_bot", False)))
        await g._send_temp(bot, chat_id, _sanction_notice(action, mention, reason_es, reason_en), ttl=20)
    except Exception as ex:
        logger.error(f"❌ [MediaLocks] Sanción '{action}' fallida para {user_id} en {chat_id}: {ex}")


# ==========================================
# 🛡️ v23 · REFUERZO: HUELLAS DE EDICIÓN, RÁFAGAS DE REENVÍOS Y BOTS AUTORIZADOS
# ==========================================
# Todas las estructuras en memoria son ACOTADAS (tamaño máximo + TTL) y se purgan solas (purge_v23_state).
FINGERPRINT_TTL_S = 48 * 3600
FINGERPRINT_MAX = 20000
_fingerprints: "OrderedDict" = OrderedDict()   # (chat, message_id) -> (monotonic, frozenset(vectores))

FWD_BURST_MAX_KEYS = 5000
_fwd_bursts: dict = {}                          # (chat, user) -> deque[(monotonic, message_id)]

SHIELD_CACHE_TTL_S = 30
SHIELD_CACHE_MAX = 2000
_shield_cache: dict = {}                        # chat -> (monotonic, config)


def remember_fingerprint(chat_id: int, message_id: int, vectors) -> None:
    key = (int(chat_id), int(message_id))
    _fingerprints.pop(key, None)
    _fingerprints[key] = (time.monotonic(), frozenset(vectors))
    while len(_fingerprints) > FINGERPRINT_MAX:
        _fingerprints.popitem(last=False)


def original_vectors(chat_id: int, message_id: int) -> Optional[frozenset]:
    """Vectores que tenía el mensaje al publicarse (o en su última edición revisada); None si no se conoce."""
    entry = _fingerprints.get((int(chat_id), int(message_id)))
    if not entry:
        return None
    if time.monotonic() - entry[0] > FINGERPRINT_TTL_S:
        _fingerprints.pop((int(chat_id), int(message_id)), None)
        return None
    return entry[1]


def forget_shield_cache(chat_id: Optional[int] = None) -> None:
    if chat_id is None:
        _shield_cache.clear()
    else:
        _shield_cache.pop(int(chat_id), None)


async def get_shield(chat_id: int) -> dict:
    """Configuración de refuerzo (ráfaga + bots autorizados) con caché de 30 s. Nunca lanza."""
    now = time.monotonic()
    hit = _shield_cache.get(int(chat_id))
    if hit and now - hit[0] < SHIELD_CACHE_TTL_S:
        return hit[1]
    try:
        cfg = await get_shield_config(int(chat_id))
    except Exception as ex:
        logger.debug(f"[MediaLocks] Refuerzo no disponible en {chat_id}: {ex}")
        cfg = {"fwd_burst_limit": 0, "fwd_burst_window": 60, "authorized_bots": []}
    cfg["authorized_set"] = frozenset(b["username"] for b in cfg.get("authorized_bots") or [])
    if len(_shield_cache) >= SHIELD_CACHE_MAX:
        _shield_cache.clear()
    _shield_cache[int(chat_id)] = (now, cfg)
    return cfg


def _register_forward(chat_id: int, user_id: int, message_id: int, window: int) -> list:
    """Registra un reenvío y devuelve los (ts, message_id) de la ventana (incluido el actual)."""
    now = time.monotonic()
    key = (int(chat_id), int(user_id))
    bucket = _fwd_bursts.get(key)
    if bucket is None:
        if len(_fwd_bursts) >= FWD_BURST_MAX_KEYS:
            purge_v23_state()
            if len(_fwd_bursts) >= FWD_BURST_MAX_KEYS:
                _fwd_bursts.pop(next(iter(_fwd_bursts)), None)
        bucket = _fwd_bursts[key] = deque(maxlen=20)
    while bucket and now - bucket[0][0] > window:
        bucket.popleft()
    bucket.append((now, int(message_id)))
    return list(bucket)


def purge_v23_state() -> dict:
    """Purga de las estructuras v23 (la invoca el janitor y, por seguridad, el propio módulo al crecer)."""
    now = time.monotonic()
    stale = [k for k, (ts, _v) in list(_fingerprints.items()) if now - ts > FINGERPRINT_TTL_S]
    for k in stale:
        _fingerprints.pop(k, None)
    dead = [k for k, b in list(_fwd_bursts.items()) if not b or now - b[-1][0] > max(FWD_BURST_WINDOWS)]
    for k in dead:
        _fwd_bursts.pop(k, None)
    old = [k for k, (ts, _c) in list(_shield_cache.items()) if now - ts > SHIELD_CACHE_TTL_S]
    for k in old:
        _shield_cache.pop(k, None)
    return {"fingerprints": len(_fingerprints), "fwd_bursts": len(_fwd_bursts), "shield_cache": len(_shield_cache)}


async def _delete_burst(bot: Bot, chat_id: int, message_ids: list) -> int:
    """Borra los reenvíos previos de la ráfaga (máx. 20). Nunca lanza."""
    deleted = 0
    ids = [m for m in message_ids if m]
    if not ids:
        return 0
    try:
        if hasattr(bot, "delete_messages"):
            await asyncio.wait_for(bot.delete_messages(chat_id=chat_id, message_ids=ids[:100]), timeout=8)
            return len(ids)
    except Exception as ex:
        logger.debug(f"[MediaLocks] Borrado en lote de la ráfaga fallido en {chat_id}: {ex}")
    for mid in ids:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(bot.delete_message(chat_id=chat_id, message_id=mid), timeout=5)
            deleted += 1
    return deleted


def _burst_verdict(state: dict, count: int, window: int) -> dict:
    """Ráfaga de reenvíos: al menos Silenciar + borrado (o la regla de 📥 si es más severa)."""
    rule = effective_media_lock_rule(state, "forward_any")
    action = rule["action"] if MEDIA_LOCK_SEVERITY[rule["action"]] > MEDIA_LOCK_SEVERITY["mute"] else "mute"
    return {"key": "forward_any", "detail": f"ráfaga {count} / {window} s", "action": action, "delete": True,
            "edit": False, "burst": True}


async def inspect_and_enforce(bot: Bot, message: Message, *, is_edit: bool = False, blacklist=None,
                              exempt=None) -> bool:
    """
    Punto de entrada desde group_security_matrix (miembros NO privilegiados). Devuelve True si el mensaje fue
    sancionado o eliminado (el pipeline debe detenerse). v23: exempt = vectores eximidos por un rol VIP.
    """
    chat_id = message.chat.id
    try:
        state = await get_media_locks_config(chat_id)
    except Exception as ex:
        logger.warning(f"⚠️ [MediaLocks] Configuración no disponible en {chat_id}: {ex}")
        return False
    shield = await get_shield(chat_id)

    blacklist_hit = ""
    if is_edit and blacklist:
        lowered = _normalize_text((message.text or message.caption or "")).lower()
        blacklist_hit = next((w for w in blacklist if w and w in lowered), "")

    found = detect_vectors(message, state["long_video_max_seconds"], bot_id=bot.id,
                           authorized_bots=shield["authorized_set"])
    original = original_vectors(chat_id, message.message_id) if is_edit else None
    verdict = evaluate_message(message, state, is_edit=is_edit, blacklist_hit=blacklist_hit, bot_id=bot.id,
                               authorized_bots=shield["authorized_set"], exempt=frozenset(exempt or ()),
                               original=original, found=found)

    # 📥 Ráfaga de reenvíos masivos: N reenvíos externos en la ventana → borrado de TODA la ráfaga + sanción.
    limit = int(shield.get("fwd_burst_limit") or 0)
    if not is_edit and limit and message.from_user and any(k == "forward_any" for k, _ in found):
        window = int(shield.get("fwd_burst_window") or 60)
        burst = _register_forward(chat_id, message.from_user.id, message.message_id, window)
        if len(burst) >= limit:
            previous = [mid for _ts, mid in burst if mid != message.message_id]
            _fwd_bursts.pop((chat_id, message.from_user.id), None)
            burst_verdict = _burst_verdict(state, len(burst), window)
            if verdict is None or MEDIA_LOCK_SEVERITY[burst_verdict["action"]] >= MEDIA_LOCK_SEVERITY[verdict["action"]]:
                verdict = burst_verdict
            verdict["delete"] = True
            await _delete_burst(bot, chat_id, previous)

    if verdict is None or not verdict.get("delete"):
        remember_fingerprint(chat_id, message.message_id, (k for k, _ in found))
    if verdict is None:
        return False
    await apply_sanction(bot, chat_id, message.from_user, verdict, message=message)
    return True


async def inspect_sender_chat(bot: Bot, message: Message, *, is_edit: bool = False) -> bool:
    """
    v23 · Mensajes publicados COMO un canal externo (sender_chat ≠ grupo). Un canal no acumula strikes ni puede
    silenciarse: Warn / Del → solo se borra; Kick / Mute / Ban → se borra y se bloquea el canal emisor
    (ban_chat_sender_chat). Devuelve True si se actuó. Nunca lanza.
    """
    sender = getattr(message, "sender_chat", None)
    if sender is None or sender.id == message.chat.id:
        return False
    chat_id = message.chat.id
    try:
        state = await get_media_locks_config(chat_id)
    except Exception:
        return False
    shield = await get_shield(chat_id)
    verdict = evaluate_message(message, state, is_edit=is_edit, bot_id=bot.id, authorized_bots=shield["authorized_set"])
    if verdict is None:
        return False
    from handlers import groups as g
    with contextlib.suppress(Exception):
        await asyncio.wait_for(message.delete(), timeout=8)
        g.track_security(chat_id, sender.id, "antispam", "delete", True)
    if verdict["action"] in ("kick", "mute", "ban"):
        try:
            await asyncio.wait_for(bot.ban_chat_sender_chat(chat_id, sender.id), timeout=8)
            g.track_security(chat_id, sender.id, "antispam", "ban", True)
            logger.warning(f"🛡️ [MediaLocks] Canal emisor {sender.id} bloqueado en {chat_id} por {verdict['key']}.")
        except Exception as ex:
            logger.debug(f"[MediaLocks] No se pudo bloquear el canal emisor {sender.id} en {chat_id}: {ex}")
    return True


async def handle_bot_added(bot: Bot, message: Message, adder, new_bot) -> bool:
    """
    Vector 29 · bot_added. Si un miembro SIN privilegios añade un bot y la regla no es Off: el bot invasor se
    expulsa (ban) y quien lo invitó recibe la sanción configurada. Devuelve True si el bot fue retirado.
    """
    from handlers import groups as g

    chat_id = message.chat.id
    if adder is None or adder.id == new_bot.id or adder.id == bot.id:
        return False
    try:
        state = await get_media_locks_config(chat_id)
    except Exception:
        return False
    rule = effective_media_lock_rule(state, "bot_added")
    if rule["action"] == "off":
        return False
    # v23 · Bot autorizado por el dueño (lista blanca de la consola / Mini App): se permite.
    shield = await get_shield(chat_id)
    if (getattr(new_bot, "username", "") or "").lower() in shield["authorized_set"]:
        logger.info(f"🛸 [MediaLocks] Bot autorizado @{new_bot.username} añadido por {adder.id} en {chat_id}: permitido.")
        return False
    tier = await g.get_privilege_tier(bot, chat_id, adder.id, adder.username or "")
    if g._tier_is_privileged(tier):
        return False
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=new_bot.id)
        g.track_security(chat_id, new_bot.id, "antispam", "ban", True)
        logger.warning(f"🛸 [MediaLocks] Bot {new_bot.id} añadido por {adder.id} en {chat_id}: retirado.")
    except Exception as ex:
        logger.warning(f"⚠️ [MediaLocks] No se pudo retirar el bot {new_bot.id} de {chat_id}: {ex}")
    bot_name = f"@{new_bot.username}" if getattr(new_bot, "username", None) else str(new_bot.id)
    verdict = {"key": "bot_added", "detail": bot_name, "action": rule["action"], "delete": False, "edit": False}
    if rule["action"] != "del":
        await apply_sanction(bot, chat_id, adder, verdict, message=None)
    return True


# ==========================================
# 🎛️ PANEL PAGINADO (CONSOLA PRIVADA)
# ==========================================
def _legend(lang: str) -> str:
    return tr(lang,
              "❕ = Warn | ❗ = Kick | 🔇 = Silenciar | 🚫 = Ban | 🗑️ = Eliminación | ☑️ = Off",
              "❕ = Warn | ❗ = Kick | 🔇 = Mute | 🚫 = Ban | 🗑️ = Delete | ☑️ = Off")


def _rule_summary(rule: dict, lang: str) -> str:
    action = rule["action"]
    name = ACTION_NAMES[action][0 if lang == "es" else 1]
    if action in ("off", "del"):
        return f"{ACTION_ICONS[action]} {name}"
    return f"{ACTION_ICONS[action]} {name}" + (" + 🗑️" if rule["delete"] else "")


def _mark(icon: str, active: bool) -> str:
    return f"•{icon}•" if active else icon


async def render_panel(group_id: int, page: int, lang: str) -> tuple:
    page = max(1, min(PAGES, int(page)))
    state = await get_media_locks_config(group_id)
    try:
        commands_on = int(await get_lock_status(group_id, "lock_commands") or 0) == 1
    except Exception:
        commands_on = False
    keys = MEDIA_LOCK_KEYS[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    lines = []
    for key in keys:
        rule = state["rules"][key]
        floor = " 🚨" if key in state["floor"] else ""
        lines.append(f"{label(key, lang)}: <b>{_rule_summary(rule, lang)}</b>{floor}")
    notes = []
    if state["floor"]:
        notes.append(tr(lang,
                        "🚨 <i>Pánico / modo nocturno activo: los vectores marcados aplican como mínimo Warn + 🗑️ hasta que se levante.</i>",
                        "🚨 <i>Panic / night mode active: marked vectors apply at least Warn + 🗑️ until lifted.</i>"))
    minutes = state["long_video_max_seconds"] // 60
    forget_shield_cache(group_id)
    shield = await get_shield(group_id)
    burst_limit, burst_window = int(shield["fwd_burst_limit"]), int(shield["fwd_burst_window"])
    shield_line = tr(
        lang,
        f"🛡️ <b>Refuerzo v23</b> · 📥 Ráfaga de reenvíos: <b>{f'{burst_limit} en {burst_window} s' if burst_limit else 'Off'}</b>"
        f" · 🤖 Bots autorizados: <b>{len(shield['authorized_bots'])}</b>",
        f"🛡️ <b>Hardening v23</b> · 📥 Forward burst: <b>{f'{burst_limit} in {burst_window} s' if burst_limit else 'Off'}</b>"
        f" · 🤖 Authorized bots: <b>{len(shield['authorized_bots'])}</b>")
    text = (
        f"🔒 <b>{tr(lang, 'Bloqueo de contenido multimedia', 'Media content locks')}</b>\n"
        f"{_legend(lang)}\n\n"
        f"<b>{PAGE_TITLES[page][0 if lang == 'es' else 1]}</b>\n"
        + "\n".join(lines)
        + ("\n\n" + "\n".join(notes) if notes else "")
        + "\n\n" + shield_line
        + "\n\n"
        + tr(lang,
             f"<i>Toca una acción en la fila de cada vector. 🗑️ activa o quita el borrado del mensaje (sobre ☑️ Off equivale a «solo eliminar»). Películas: más de {minutes} min.</i>",
             f"<i>Tap an action on each vector's row. 🗑️ toggles message deletion (on ☑️ Off it means “delete only”). Movies: over {minutes} min.</i>")
        + SIG
    )

    rows = []
    for key in keys:
        idx = MEDIA_LOCK_KEYS.index(key)
        rule = state["rules"][key]
        action = rule["action"]
        base = f"{group_id}_{idx}"
        rows.append([
            InlineKeyboardButton(text=CATALOG[key][0], callback_data=f"mlk_i_{base}_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("☑️", action == "off"), callback_data=f"mlk_s_{base}_o_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("❕", action == "warn"), callback_data=f"mlk_s_{base}_w_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("❗", action == "kick"), callback_data=f"mlk_s_{base}_k_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("🔇", action == "mute"), callback_data=f"mlk_s_{base}_m_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("🚫", action == "ban"), callback_data=f"mlk_s_{base}_b_{page}_{lang}"),
            InlineKeyboardButton(text=_mark("🗑️", action == "del" or (action != "off" and rule["delete"])),
                                 callback_data=f"mlk_d_{base}_{page}_{lang}"),
        ])
    rows.append([
        InlineKeyboardButton(text=f"•{n}•" if n == page else str(n), callback_data=f"mlk_p_{group_id}_{n}_{lang}")
        for n in range(1, PAGES + 1)
    ])
    current = state["long_video_max_seconds"]
    rows.append([
        InlineKeyboardButton(text=("✅ " if current == secs else "") + tr(lang, f"🎬 Películas > {secs // 60} min", f"🎬 Movies > {secs // 60} min"),
                             callback_data=f"mlk_v_{group_id}_{secs}_{page}_{lang}")
        for secs in LONG_VIDEO_CHOICES
    ])
    rows.append([InlineKeyboardButton(
        text=tr(lang, f"⌨️ Comandos de bots: {'🟢 Bloqueados' if commands_on else '🔴 Permitidos'}",
                f"⌨️ Bot commands: {'🟢 Locked' if commands_on else '🔴 Allowed'}"),
        callback_data=f"mlk_c_{group_id}_{page}_{lang}")])
    # 🛡️ v23 · Ráfaga de reenvíos masivos (umbral y ventana) y lista blanca de bots.
    rows.append([
        InlineKeyboardButton(text=tr(lang, f"📥 Ráfaga: {burst_limit or 'Off'}", f"📥 Burst: {burst_limit or 'Off'}"),
                             callback_data=f"mlk_b_{group_id}_{page}_{lang}"),
        InlineKeyboardButton(text=f"⏱️ {burst_window} s", callback_data=f"mlk_w_{group_id}_{page}_{lang}"),
        InlineKeyboardButton(text=tr(lang, f"🤖 Bots ({len(shield['authorized_bots'])})", f"🤖 Bots ({len(shield['authorized_bots'])})"),
                             callback_data=f"mlk_a_{group_id}_{page}_{lang}"),
    ])
    back_text = "🔙 Volver" if lang == "es" else "🔙 Back"
    rows.append([InlineKeyboardButton(text=back_text, callback_data=f"gpanel_{group_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


AUTH_BOT_STATES: dict = {}   # (bot_id, user_id) -> {"group_id", "lang", "page", "ts"} · registrado en user_private
AUTH_BOT_STATE_TTL_S = 600


async def render_bots_panel(group_id: int, page: int, lang: str) -> tuple:
    forget_shield_cache(group_id)
    shield = await get_shield(group_id)
    bots = shield["authorized_bots"]
    listing = "\n".join(f"• <code>@{html.escape(b['username'])}</code>" for b in bots) or tr(lang, "<i>Ninguno.</i>", "<i>None.</i>")
    text = (
        f"🤖 <b>{tr(lang, 'Bots autorizados', 'Authorized bots')}</b>\n\n"
        + tr(lang,
             "Solo estos bots pueden: ser invitados por miembros (🛸), publicar vía modo inline (🤖) y traer botones "
             "inline (⌨️) o reenvíos (📥). Los enlaces que contengan se siguen inspeccionando.",
             "Only these bots may be invited by members (🛸), post via inline mode (🤖) and carry inline buttons (⌨️) "
             "or forwards (📥). Any links they contain are still inspected.")
        + f"\n\n{listing}" + SIG
    )
    rows = [[InlineKeyboardButton(text=f"❌ @{b['username']}", callback_data=f"mlk_x_{group_id}_{i}_{page}_{lang}")]
            for i, b in enumerate(bots[:20])]
    rows.append([InlineKeyboardButton(text=tr(lang, "➕ Autorizar bot", "➕ Authorize bot"), callback_data=f"mlk_n_{group_id}_{page}_{lang}")])
    rows.append([InlineKeyboardButton(text=tr(lang, "🔙 Volver a la matriz", "🔙 Back to matrix"), callback_data=f"mlk_p_{group_id}_{page}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_authorized_bot_input(message: Message, bot: Bot, st: dict, text_input: str) -> None:
    """Respuesta al asistente «➕ Autorizar bot»: @alias, enlace t.me o un mensaje reenviado desde el bot."""
    group_id, lang, page = int(st["group_id"]), st.get("lang", "es"), int(st.get("page", 2))
    back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=tr(lang, "🔙 Bots autorizados", "🔙 Authorized bots"), callback_data=f"mlk_a_{group_id}_{page}_{lang}")]])
    if time.time() - float(st.get("ts", 0)) > AUTH_BOT_STATE_TTL_S:
        await message.answer(tr(lang, "⌛ La edición caducó; vuelve a abrir el menú.", "⌛ This edit expired; reopen the menu.") + SIG,
                             reply_markup=back, parse_mode="HTML")
        return
    raw, bot_id = text_input or "", None
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) or getattr(message, "forward_from", None)
    if sender is not None and getattr(sender, "is_bot", False) and getattr(sender, "username", None):
        raw, bot_id = sender.username, sender.id
    try:
        await add_authorized_bot(group_id, raw, bot_id=bot_id, added_by=message.from_user.id)
    except BunkerValidationError as err:
        await message.answer(f"❌ {html.escape('; '.join(err.errors.values()))}" + SIG, reply_markup=back, parse_mode="HTML")
        return
    except Exception as ex:
        logger.error(f"❌ [MediaLocks] No se pudo autorizar el bot en {group_id}: {ex}")
        await message.answer(tr(lang, "⚠️ No se pudo guardar.", "⚠️ Could not save.") + SIG, reply_markup=back, parse_mode="HTML")
        return
    forget_shield_cache(group_id)
    _emit_change(group_id, ["shield_authorized_bots"])
    text, kb = await render_bots_panel(group_id, page, lang)
    await message.answer(tr(lang, "✅ <b>Bot autorizado.</b>\n\n", "✅ <b>Bot authorized.</b>\n\n") + text, reply_markup=kb, parse_mode="HTML")


class _CallbackAutoAnswer(BaseMiddleware):
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        try:
            return await handler(event, data)
        finally:
            with contextlib.suppress(Exception):
                await event.answer()


router.callback_query.middleware(_CallbackAutoAnswer())
_INT = re.compile(r"^-?\d{1,20}$")


def _emit_change(group_id: int, changed: list) -> None:
    with contextlib.suppress(Exception):
        from handlers.user_private import emit_settings_updated_bg
        emit_settings_updated_bg(group_id, "bot_console", changed)


async def _show(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    from handlers.user_private import safe_edit_text
    try:
        await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as ex:
        if "message is not modified" not in str(ex).lower():
            with contextlib.suppress(Exception):
                await callback.message.delete()
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def open_media_locks_panel(callback: CallbackQuery, bot: Bot, group_id: int, lang: str, page: int = 1) -> None:
    """Entrada desde la consola privada (botón 🔒 Cerraduras / gset_locks_). La autorización la hace el llamador."""
    text, kb = await render_panel(group_id, page, lang)
    await _show(callback, text, kb)


@router.callback_query(F.data.startswith("mlk_"))
async def media_locks_callbacks(callback: CallbackQuery, bot: Bot):
    from handlers.user_private import clear_user_states, verify_admin_privileges

    parts = (callback.data or "").split("_")
    if len(parts) < 4 or not _INT.match(parts[2]):
        return
    sub, group_id = parts[1], int(parts[2])
    lang = parts[-1] if parts[-1] in ("es", "en") else "es"
    # 👑 Dueño legítimo, Arquitecto o delegado con can_manage_locks (regla 'mlk_' del mapa de delegados).
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    clear_user_states(bot.id, callback.from_user.id)

    def _int(i: int, default: int = 1) -> int:
        try:
            return int(parts[i])
        except (IndexError, ValueError):
            return default

    if sub == "p":
        await open_media_locks_panel(callback, bot, group_id, lang, page=_int(3))
        return

    if sub == "i":
        idx = _int(3, -1)
        if 0 <= idx < len(MEDIA_LOCK_KEYS):
            key = MEDIA_LOCK_KEYS[idx]
            meta = CATALOG[key]
            await callback.answer(f"{meta[0]} {meta[1] if lang == 'es' else meta[2]}\n\n{meta[3] if lang == 'es' else meta[4]}"[:200],
                                  show_alert=True)
        return

    if sub == "s":
        idx, code, page = _int(3, -1), (parts[4] if len(parts) > 4 else ""), _int(5)
        action = ACTION_CODES.get(code)
        if not (0 <= idx < len(MEDIA_LOCK_KEYS)) or action is None:
            return
        key = MEDIA_LOCK_KEYS[idx]
        state = await set_media_lock_rule(group_id, key, action)
        rule = state["rules"][key]
        await callback.answer(f"{label(key, lang)} → {_rule_summary(rule, lang)}")
        _emit_change(group_id, ["media_locks", key] + (["linklock_enabled"] if key in LEGACY_LOCK_GROUPS["lock_links"] else []))
        await open_media_locks_panel(callback, bot, group_id, lang, page=page)
        return

    if sub == "d":
        idx, page = _int(3, -1), _int(4)
        if not (0 <= idx < len(MEDIA_LOCK_KEYS)):
            return
        key = MEDIA_LOCK_KEYS[idx]
        state = await toggle_media_lock_delete(group_id, key)
        await callback.answer(f"{label(key, lang)} → {_rule_summary(state['rules'][key], lang)}")
        _emit_change(group_id, ["media_locks", key] + (["linklock_enabled"] if key in LEGACY_LOCK_GROUPS["lock_links"] else []))
        await open_media_locks_panel(callback, bot, group_id, lang, page=page)
        return

    if sub == "v":
        seconds, page = _int(3, LONG_VIDEO_CHOICES[0]), _int(4)
        if not (LONG_VIDEO_RANGE[0] <= seconds <= LONG_VIDEO_RANGE[1]):
            return
        await set_long_video_max_seconds(group_id, seconds)
        await callback.answer(tr(lang, f"🎬 Películas: más de {seconds // 60} min", f"🎬 Movies: over {seconds // 60} min"))
        _emit_change(group_id, ["media_locks", "long_video_max_seconds"])
        await open_media_locks_panel(callback, bot, group_id, lang, page=page)
        return

    if sub in ("b", "w"):
        page = _int(3)
        shield = await get_shield(group_id)
        if sub == "b":
            seq, cur = FWD_BURST_LIMITS, int(shield["fwd_burst_limit"])
        else:
            seq, cur = FWD_BURST_WINDOWS, int(shield["fwd_burst_window"])
        nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
        try:
            await (set_fwd_burst(group_id, limit=nxt) if sub == "b" else set_fwd_burst(group_id, window=nxt))
        except Exception as ex:
            logger.error(f"❌ [MediaLocks] Ráfaga no guardada en {group_id}: {ex}")
            return
        forget_shield_cache(group_id)
        _emit_change(group_id, ["shield_fwd_burst"])
        await callback.answer(tr(lang, "📥 Ráfaga de reenvíos actualizada", "📥 Forward burst updated"))
        await open_media_locks_panel(callback, bot, group_id, lang, page=page)
        return

    if sub == "a":
        text, kb = await render_bots_panel(group_id, _int(3, 2), lang)
        await _show(callback, text, kb)
        return

    if sub == "x":
        idx, page = _int(3, -1), _int(4, 2)
        bots = (await get_shield(group_id))["authorized_bots"]
        if 0 <= idx < len(bots):
            await remove_authorized_bot(group_id, bots[idx]["username"])
            forget_shield_cache(group_id)
            _emit_change(group_id, ["shield_authorized_bots"])
            await callback.answer(f"❌ @{bots[idx]['username']}")
        text, kb = await render_bots_panel(group_id, page, lang)
        await _show(callback, text, kb)
        return

    if sub == "n":
        page = _int(3, 2)
        if len(AUTH_BOT_STATES) > 2000:
            cutoff = time.time() - AUTH_BOT_STATE_TTL_S
            for k in [k for k, v in AUTH_BOT_STATES.items() if v.get("ts", 0) < cutoff]:
                AUTH_BOT_STATES.pop(k, None)
        AUTH_BOT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang, "page": page, "ts": time.time()}
        await callback.message.answer(
            tr(lang, "🤖 Envía el <b>@alias</b> del bot a autorizar (debe terminar en <i>bot</i>) o <b>reenvía</b> aquí un mensaje suyo.",
               "🤖 Send the <b>@username</b> of the bot to authorize (must end in <i>bot</i>) or <b>forward</b> one of its messages here.") + SIG,
            parse_mode="HTML")
        return

    if sub == "c":
        page = _int(3)
        current = int(await get_lock_status(group_id, "lock_commands") or 0)
        await set_lock_status(group_id, "lock_commands", 0 if current == 1 else 1)
        await callback.answer(tr(lang, "⌨️ Comandos: " + ("🔴 permitidos" if current == 1 else "🟢 bloqueados"),
                                 "⌨️ Commands: " + ("🔴 allowed" if current == 1 else "🟢 locked")))
        await open_media_locks_panel(callback, bot, group_id, lang, page=page)
        return