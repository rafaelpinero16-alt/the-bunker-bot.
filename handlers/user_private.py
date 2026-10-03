"""
user_private.py — Consola privada de The Bunker OS (Aiogram 3.x) · Cloud Media Management.

Revisión de resiliencia y blindaje:
  1. Estados conversacionales seguros ante reinicios de RAM (TTL, liberación en bloque, fallback anti-bloqueo, /cancel).
  2. Sincronización Activa de Canales estilo GroupHelp (verificación en vivo + registro autorreparable + selector nativo).
  3. Navegación contextual estricta: canal → cpanel | grupo → gpanel, también en pasarelas de pago y herramientas ULTRA.
  4. Identidad visual: botones inline, soporte bilingüe ES/EN y firma perimetral "Cloud Media Management".
"""
import os
import sys
import re
import time
import html
import json
import asyncio
import logging
from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ChatPermissions,
    ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat,
    ReplyKeyboardRemove, FSInputFile, InputMediaPhoto
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from database.database import (
    get_or_create_user, get_user_groups, get_user_channels,
    get_group_tier, get_user_global_tier,
    get_antispam_filter, set_antispam_filter,
    get_antiflood_config, set_antiflood_config,
    get_antispam_delete, set_antispam_delete,
    set_captcha_status,
    get_captcha_config, set_captcha_config,
    get_lock_status, set_lock_status,
    get_warns_config, set_warns_config,
    add_to_blacklist, add_to_whitelist,
    get_autolower_status, set_autolower_status,
    register_bot_clone, get_bot_clone, get_db_connection,
    save_owner_session, get_owner_session, revoke_owner_session,
    get_vc_schedule, set_vc_schedule,
    get_mic_vip_custom_config, set_mic_vip_custom_config,
    # 🚨 ULTRA PRO — Panel de Élite & Módulos Corporativos
    get_panic_status, set_panic_status,
    get_shield_status, set_shield_status,
    get_podcast_status, set_podcast_status,
    get_service_msgs_mode, set_service_msgs_mode,
    get_tips_config, set_tips_config, get_group_tip_targets,
    get_sentinel_payload_config, set_sentinel_payload_config,
    get_community_live_telemetry, set_vip_badge_title,
    # 💎 Módulos de Canales & Membresías
    get_channel_plans, get_active_subscribers_count,
    create_channel_plan, delete_channel_plan,
    toggle_channel_plan_status,  # <--- AGREGAR AQUÍ
    get_channel_plan,
    create_web_session,  # <--- Sesiones web temporales (ChatKeeper Style)
    set_channel_plan_broadcast_config,
    get_night_mode_config,
    set_night_mode_config,
    get_ai_sentinel_config,
    set_ai_sentinel_config,
    get_sentinel_service_messages_config,
    set_sentinel_service_message,
)
from assistant import (
    register_or_update_sentinel, disconnect_sentinel,
    start_phone_auth, verify_phone_code, verify_2fa_password, cancel_phone_auth,
    engage_screen_shield, disengage_screen_shield,
    engage_podcast_ducking, disengage_podcast_ducking
)
from .groups import (
    execute_raid_lockdown, lift_raid_lockdown,
    clear_speaker_queue, get_speaker_queue
)

router = Router()


class CallbackAutoAnswerMiddleware(BaseMiddleware):
    """
    Telegram admite UNA sola respuesta por callback_query.
    Garantiza que el spinner del botón se libere siempre sin colisiones de respuesta doble.
    """
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        try:
            return await handler(event, data)
        finally:
            try:
                await event.answer()
            except Exception:
                pass


router.callback_query.middleware(CallbackAutoAnswerMiddleware())

ADMIN_GROUP_ID = -1004351489258
WEBAPP_URL = "https://thebunkerapp2.netlify.app/"

# ------------------------------------------
# 🖼️ GESTIÓN MAESTRA DE TARJETAS GRÁFICAS HUD (26 IMÁGENES ES/EN)
# ------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _build_asset_dirs() -> list:
    """Construye un árbol de búsqueda ascendente para garantizar la localización de /assets."""
    dirs = ["assets"]
    cur = _BASE_DIR
    for _ in range(5):
        dirs.append(os.path.join(cur, "assets"))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return dirs

ASSET_DIRS = _build_asset_dirs()

# Mapeo exacto de las 13 parejas estratégicas (26 imágenes HUD en ES / EN)
CARD_IMAGES = {
    # 1 y 2: Pantalla de Bienvenida (/start)
    "welcome":           {"es": "bienvenida.jfif",            "en": "welcome_2.jfif"},
    # 3 y 4: Centro de Mando de Comunidades (Grupos)
    "settings":          {"es": "centrodecomando_2.jfif",     "en": "commandcenter_2.jfif"},
    # 5 y 6: Estudio de Canales y Membresías
    "settings_channels": {"es": "config.canales.jfif",        "en": "setting.channels_2.jfif"},
    # 7 y 8: Guía Maestra Operativa (Cómo Funciona)
    "info_how":          {"es": "informacion.jfif",           "en": "information.jfif"},
    # 9 y 10: Guía Operativa: Grupos y Perímetro
    "info_groups":       {"es": "gruposyperimetro.jfif",      "en": "groupsandperimeter.jfif"},
    # 11 y 12: Guía Operativa: Canales y Lives
    "info_channels":     {"es": "canales&lives.jfif",         "en": "channels&lives.jfif"},
    # 13 y 14: Guía Operativa: Monetización Stars
    "info_monetization": {"es": "monetizacion.de.stars.jfif", "en": "stars.monetization.jfif"},
    # 15 y 16: Núcleo del Sistema v6.0
    "info_core":         {"es": "nucleo.sistema.jfif",        "en": "information.jfif"},
    # 17 y 18: Matriz de Seguridad (Panel Individual de Grupo)
    "security_matrix":   {"es": "matriz.seguridad.jfif",      "en": "security.matrix.jfif"},
    # 19 y 20: Pasarela de Pago Plan PRO (300 Stars)
    "pay_pro":           {"es": "pro.es.jfif",                "en": "pro.en.jfif"},
    # 21 y 22: Pasarela de Pago Plan ULTRA PRO (600 Stars)
    "pay_ultra":         {"es": "ultra.pro.subscrib.jfif",    "en": "ultra.pro.subscrip.jfif"},
    # 23 y 24: Funciones Operativas del Centinela
    "sentinel":          {"es": "config.centinela.jfif",      "en": "setting.centinel.jfif"},
    # 25 y 26: Control Acústico y Supervisión 24/7 del Centinela
    "sentinel_info":     {"es": "centinela.jfif",             "en": "centinel.jfif"},
}

# Caché de file_id por bot (los clones tienen su propio bot.id => caché aislada)
_CARD_FILE_IDS: dict = {}


def _find_asset(filename: str):
    """
    Localizador tolerante a variaciones de formato:
    Resuelve intercambios entre puntos (.), guiones bajos (_), ampersands (&),
    sufijos de versión (_2) y extensiones (.jfif <-> .jpg).
    """
    if not filename:
        return None

    candidates = {filename}
    candidates.add(filename.replace(".", "_"))
    candidates.add(filename.replace("_", "."))
    candidates.add(filename.replace("&", "_"))
    candidates.add(filename.replace("_", "&"))

    base_name, ext = os.path.splitext(filename)
    if base_name.endswith("_2"):
        candidates.add(base_name[:-2] + ext)
    else:
        candidates.add(f"{base_name}_2{ext}")

    expanded = set()
    for cand in candidates:
        expanded.add(cand)
        b, e = os.path.splitext(cand)
        if e.lower() == ".jfif":
            expanded.add(b + ".jpg")
            expanded.add(b + ".jpeg")
        elif e.lower() in (".jpg", ".jpeg"):
            expanded.add(b + ".jfif")

    for d in ASSET_DIRS:
        for target in expanded:
            p = os.path.join(d, target)
            if os.path.isfile(p):
                return p
    return None


def _card_media(bot: Bot, filename: str, path: str):
    cached = _CARD_FILE_IDS.get((bot.id, filename))
    return cached or FSInputFile(path, filename=os.path.splitext(filename)[0] + ".jpg")


def _remember_card(bot: Bot, filename: str, msg) -> None:
    try:
        if msg is not None and getattr(msg, "photo", None):
            _CARD_FILE_IDS[(bot.id, filename)] = msg.photo[-1].file_id
    except Exception:
        pass


async def render_card(bot: Bot, chat_id: int, card_key: str, lang: str, keyboard=None,
                      caption: str = None, old_message=None) -> bool:
    """
    Renderiza la tarjeta visual HUD asociada a `card_key` en el idioma `lang`:
    - Si el mensaje anterior ya contiene foto, realiza un `edit_media` fluido sin parpadeos.
    - Si el mensaje anterior era de texto, despacha la foto y elimina el mensaje previo.
    - Si la imagen no está en disco o falla Telegram, devuelve False para ejecutar el fallback de texto.
    """
    names = CARD_IMAGES.get(card_key) or {}
    filename = names.get(lang) or names.get("es")
    path = _find_asset(filename) if filename else None
    if not path:
        logging.warning(f"⚠️ [Card] Imagen '{filename}' no encontrada en {ASSET_DIRS}; activando fallback textual.")
        return False
    if caption and len(caption) > 1024:
        logging.warning(f"⚠️ [Card] Caption de '{card_key}' excede 1024 caracteres; activando fallback textual.")
        return False

    for _attempt in range(2):
        media = _card_media(bot, filename, path)
        try:
            if old_message is not None and getattr(old_message, "photo", None):
                try:
                    res = await old_message.edit_media(
                        InputMediaPhoto(media=media, caption=caption, parse_mode="HTML"),
                        reply_markup=keyboard,
                    )
                    _remember_card(bot, filename, res if not isinstance(res, bool) else None)
                    return True
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e).lower():
                        return True
                    raise

            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=media,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            _remember_card(bot, filename, sent)

            if old_message is not None and hasattr(old_message, "delete"):
                try:
                    await old_message.delete()
                except Exception:
                    pass
            return True
        except Exception as ex:
            _CARD_FILE_IDS.pop((bot.id, filename), None)
            logging.warning(f"⚠️ [Card] Falló el despacho de '{filename}' (intento {_attempt + 1}): {ex}")
    return False


async def send_card_message(bot: Bot, chat_id: int, card_key: str, lang: str, keyboard=None, caption: str = None):
    """Despacha una tarjeta gráfica como mensaje nuevo e independiente (Message)."""
    names = CARD_IMAGES.get(card_key) or {}
    filename = names.get(lang) or names.get("es")
    path = _find_asset(filename) if filename else None
    if not path or (caption and len(caption) > 1024):
        return None

    for _attempt in range(2):
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=_card_media(bot, filename, path),
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            _remember_card(bot, filename, sent)
            return sent
        except Exception as ex:
            _CARD_FILE_IDS.pop((bot.id, filename), None)
            logging.warning(f"⚠️ [Card] Error en send_card_message para '{filename}' (intento {_attempt + 1}): {ex}")
    return None


# 🎬 Video de bienvenida (/start). Ruta relativa a la raíz del proyecto; sobreescribible por entorno.
WELCOME_VIDEO_PATH = os.getenv("WELCOME_VIDEO_PATH", "assets/bunker_intro.gif")

# file_id de Telegram por bot: tras el primer envío se almacena en caché.
_WELCOME_VIDEO_FILE_IDS: dict = {}


_BG_TASKS: set = set()


def fire_and_forget_auto_delete(messages: list, delay: int = 60):
    """Worker no bloqueante que auto-destruye mensajes tras 60s en DM para mantener la consola limpia."""
    async def _del_task():
        await asyncio.sleep(delay)
        for m in messages:
            if m:
                try:
                    await m.delete()
                except Exception:
                    pass
    task = asyncio.create_task(_del_task())
    _BG_TASKS.add(task)                       # referencia fuerte: evita que el GC cancele el borrado
    task.add_done_callback(_BG_TASKS.discard)


async def get_group_total_tips(group_id: int) -> int:
    """Return the confirmed Stars total for a group, tolerating older DB schemas."""
    def _sync() -> int:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                tables = [row[0] for row in cursor.fetchall()]
                for table in tables:
                    if not any(token in table.lower() for token in ("tip", "star", "donat")):
                        continue
                    cursor.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")')
                    columns = {row[1].lower(): row[1] for row in cursor.fetchall()}
                    group_col = next((columns[name] for name in ("group_id", "chat_id") if name in columns), None)
                    amount_col = next((columns[name] for name in ("amount", "stars", "total_amount", "star_amount") if name in columns), None)
                    if not group_col or not amount_col:
                        continue
                    where = f'"{group_col.replace(chr(34), chr(34) * 2)}" = ?'
                    if "status" in columns:
                        where += f' AND LOWER(CAST("{columns["status"]}" AS TEXT)) IN (\'paid\', \'confirmed\', \'completed\', \'successful\', \'success\')'
                    cursor.execute(
                        f'SELECT COALESCE(SUM("{amount_col.replace(chr(34), chr(34) * 2)}"), 0) FROM "{table.replace(chr(34), chr(34) * 2)}" WHERE {where}',
                        (group_id,),
                    )
                    return int(cursor.fetchone()[0] or 0)
        except Exception:
            logging.exception("Unable to calculate group tips total")
        return 0

    return await asyncio.to_thread(_sync)


async def delete_group_tip_target(group_id: int, target_id: int) -> None:
    """Remove one tips destination belonging to the specified group."""
    def _sync() -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM group_tip_targets WHERE id = ? AND group_id = ?",
                (target_id, group_id),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def add_group_tip_target(group_id: int, target: str) -> None:
    """Add a tips destination to the database safely with schema detection."""
    def _sync() -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(group_tip_targets)")
            columns = {row[1] for row in cursor.fetchall()}
            
            # Detección flexible de la columna de destino
            target_column = next(
                (name for name in ("target_value", "target", "target_username", "username", "chat_id") if name in columns),
                "target_value",
            )
            quoted_column = '"' + target_column.replace('"', '""') + '"'
            
            # Detección de columna de estado activo para inicializarla en 1
            active_col = next((name for name in ("is_active", "active", "enabled", "status") if name in columns), None)
            
            if active_col:
                quoted_active = '"' + active_col.replace('"', '""') + '"'
                cursor.execute(
                    f"INSERT OR IGNORE INTO group_tip_targets (group_id, {quoted_column}, {quoted_active}) VALUES (?, ?, 1)",
                    (group_id, target),
                )
            else:
                cursor.execute(
                    f"INSERT OR IGNORE INTO group_tip_targets (group_id, {quoted_column}) VALUES (?, ?)",
                    (group_id, target),
                )
            conn.commit()

    await asyncio.to_thread(_sync)


async def toggle_group_tip_target(group_id: int, target_id: int) -> None:
    """Toggle the enabled/active state of a tips target belonging to the given group."""
    def _sync() -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(group_tip_targets)")
            columns = {row[1].lower(): row[1] for row in cursor.fetchall()}
            
            active_col = next((columns[name] for name in ("is_active", "active", "enabled", "status") if name in columns), None)
            if active_col is None:
                return
                
            quoted_col = '"' + active_col.replace('"', '""') + '"'
            cursor.execute(
                f'SELECT {quoted_col} FROM group_tip_targets WHERE id = ? AND group_id = ?',
                (target_id, group_id),
            )
            row = cursor.fetchone()
            if row is None:
                return
                
            current = row[0]
            current_flag = str(current).strip().lower() in {"1", "true", "yes", "active", "enabled"}
            new_value = 0 if current_flag else 1
            
            cursor.execute(
                f'UPDATE group_tip_targets SET {quoted_col} = ? WHERE id = ? AND group_id = ?',
                (new_value, target_id, group_id),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS

# Cuentas de servicio (Sentinel / userbots / asistentes): inmunes igual que los Arquitectos en toda directiva de moderación.
RAW_SERVICE_ACCOUNTS = os.getenv("SERVICE_ACCOUNT_IDS", "")
SERVICE_ACCOUNT_IDS = {int(x.strip()) for x in RAW_SERVICE_ACCOUNTS.split(",") if x.strip().isdigit()}

def is_immune_account(user_id: int, bot_id: int = 0) -> bool:
    """Único punto de verdad de inmunidad: Arquitectos, cuentas de servicio y el propio bot no se sancionan."""
    return is_super_admin(user_id) or user_id in SERVICE_ACCOUNT_IDS or (bool(bot_id) and user_id == bot_id)

# ==========================================
# 🧬 IDENTIDAD MAESTRO / CLON (por bot.id)
# ==========================================
MASTER_BOT_ID = 0

def set_master_bot_id(bot_id: int) -> None:
    global MASTER_BOT_ID
    MASTER_BOT_ID = int(bot_id)

MASTER_BOT_USERNAME = ""

def set_master_bot_username(username: str) -> None:
    global MASTER_BOT_USERNAME
    MASTER_BOT_USERNAME = (username or "").lstrip("@")

def get_master_bot_username() -> str:
    return MASTER_BOT_USERNAME

def _resolve_master_bot_id() -> int:
    if MASTER_BOT_ID:
        return MASTER_BOT_ID
    head = os.getenv("BOT_TOKEN", "").split(":", 1)[0].strip()
    return int(head) if head.isdigit() else 0

def is_clone_bot(bot: Bot) -> bool:
    master_id = _resolve_master_bot_id()
    return bool(master_id) and bot.id != master_id

def _call_clone_trigger(name: str, token: str) -> None:
    for mod_name in ("__main__", "main"):
        mod = sys.modules.get(mod_name)
        fn = getattr(mod, name, None) if mod else None
        if callable(fn):
            fn(token)
            return
    logging.error(f"❌ [Clones] No se encontró {name} en __main__/main; token no procesado.")

async def get_effective_group_tier(group_id: int, user_id: int) -> str:
    if is_super_admin(user_id):
        return "ultra_pro"
    return await get_group_tier(group_id)

async def revoke_bot_clone_db(user_id: int, group_id: int):
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = '' WHERE user_id = ? AND group_id = ?", (user_id, group_id))
            conn.commit()
    await asyncio.to_thread(_sync)

# ==========================================
# 🧠 ESTADOS DE EDICIÓN CONVERSACIONAL AISLADOS POR BOT
# ==========================================
CAPTCHA_STATES = {}
CLONE_STATES = {}
SENTINEL_PHONE_STATES = {}
SENTINEL_CODE_STATES = {}
SENTINEL_2FA_STATES = {}
VC_SCHED_STATES = {}
NIGHT_STATES = {}
DB_REG_STATES = {}
MOD_TARGET_STATES = {}
MIC_VIP_STATES = {}
MIC_TAG_STATES = {}
GROUP_MIC_PRICE = {}
GROUP_VIP_TAG = {}
PODCAST_DUCK_STATES = {}
SPEAKER_PRICE_STATES = {}
GROUP_DUCK_LEVEL = {}
GROUP_SPEAKER_PRICE = {}
TIPS_AMOUNT_STATES = {}
TIPS_TARGET_STATES = {}
TIPS_TEXT_STATES = {}
TIPS_MEDIA_STATES = {}
SENTINEL_PAYLOAD_TEXT_STATES = {}
SENTINEL_PAYLOAD_MEDIA_STATES = {}
SENTINEL_PAYLOAD_AUTODEL_STATES = {}
CHAN_PLAN_STATES = {}
AI_PROMPT_STATES = {}
SENTINEL_CFG_STATES = {}
WARN_CUSTOM_TEXT_STATES = {}
WARN_CUSTOM_MEDIA_STATES = {}
MIC_VIP_TEXT_STATES = {}

# 🧯 Registro central de TODOS los estados conversacionales: permite liberarlos en bloque (navegación, /start,
# /cancel) y garantiza que ningún menú privado quede bloqueado por una conversación huérfana.
ALL_STATE_DICTS = (
    CAPTCHA_STATES, CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, SENTINEL_2FA_STATES,
    VC_SCHED_STATES, NIGHT_STATES, DB_REG_STATES, MOD_TARGET_STATES, MIC_VIP_STATES, MIC_TAG_STATES,
    PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES, TIPS_AMOUNT_STATES, TIPS_TARGET_STATES, TIPS_TEXT_STATES,
    TIPS_MEDIA_STATES, 
    SENTINEL_PAYLOAD_TEXT_STATES, SENTINEL_PAYLOAD_MEDIA_STATES, SENTINEL_PAYLOAD_AUTODEL_STATES,
    CHAN_PLAN_STATES, AI_PROMPT_STATES, WARN_CUSTOM_TEXT_STATES, WARN_CUSTOM_MEDIA_STATES, MIC_VIP_TEXT_STATES,
    SENTINEL_CFG_STATES
)

# ⏳ Caducidad de asistentes multi-paso (segundos) y enfriamiento del mensaje de "sin acción pendiente".
STATE_TTL_SECONDS = 900
FALLBACK_COOLDOWN_SECONDS = 20

# 📡 Sincronización Activa de Canales (selector nativo de Telegram) y cachés de sesión.
CHANNEL_SYNC_REQUEST_ID = 7301
CHAT_KIND_CACHE: dict = {}      # chat_id -> "c" (canal) | "g" (grupo)
CHANNEL_SYNC_CACHE: dict = {}   # user_id -> {channel_id: título}
_FALLBACK_LAST_REPLY: dict = {} # (bot_id, user_id) -> timestamp del último aviso

# 🎚️ Cupos de planes de membresía activos por nivel de licencia del Canal
CHAN_PLAN_TIER_LIMITS = {"free": 1, "pro": 3, "ultra_pro": 10}

FILTER_MAP = {
    "tglinks": "tg_links", "fwdchan": "fwd_channels", "fwdusr": "fwd_users",
    "fwdgrp": "fwd_groups", "fwdbot": "fwd_bots", "quotes": "quotes", "weblinks": "web_links"
}

# ==========================================
# 🌐 DICCIONARIO BILINGÜE CON IDENTIDAD DE MARCA
# ==========================================
TEXTS = {
    "en": {
        "owner_only_alert": "⛔ Access Denied: This command center is strictly restricted to the community/channel Owner.",
        "welcome": (
            "🏴‍☠️ <b>Welcome to the Inner Circle, {name}.</b>\n\n"
            "I am <b>The Bunker Bot</b>, the architectural security core designed by <b>Master Tom</b>. Within this domain, you wield absolute authority to forge order out of chaos.\n\n"
            "Deploy me to your groups for elite perimeter defense, or to your channels to manage automated subscriptions and live studio moderation.\n\n"
            "Select an option below to audit tactical modules or access the Command Center.\n\n"
            "🛡️ <i>Powered by <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add_group": "➕ Add to a Group",
        "btn_add_channel": "📢 Add to a Channel",
        "btn_settings": "⚙️ Group Settings",
        "btn_chsettings": "📡 Channel Settings",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ My ID & Status",
        "btn_support": "🆘 Support",
        "btn_info": "ℹ️ Information",
        "btn_how_works": "📖 How The Bunker Works",
        "settings_main": (
            "🛡️ <b>Tactical Community Command (Groups)</b>\n\n"
            "Take total perimeter control over your community:\n\n"
            "• 🤖 Alphanumeric Captcha checkpoint.\n"
            "• 🔒 Content Locks and Anti-Spam shields.\n"
            "• 🎙️ Voice Sentinel and acoustic ducking.\n"
            "• 💰 Telegram Stars Monetization with custom VIP tags.\n\n"
            "<i>Select the group below you wish to audit and shield:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "chsettings_main": (
            "📡 <b>Live Studio & Membership Command (Channels)</b>\n\n"
            "Direct broadcasting studio and subscriber monetizer:\n\n"
            "• 🎙️ <b>Live Sentinel:</b> Instant mic unmuting upon 'Raise Hand' verification.\n"
            "• 💎 <b>Paywalled Invites:</b> Cryptographic single-use links burned on join.\n"
            "• ⏳ <b>Subscription Auditor:</b> Auto-renewal alerts & automated kick for unpaid members.\n"
            "• ⭐ <b>Live Stars Tipping:</b> Direct monetized broadcasts.\n\n"
            "<i>Select the channel below you wish to manage:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "support_main": (
            "🆘 <b>Official Tactical Support</b>\n\n"
            "For direct assistance, elite passes, or custom architectures, contact our Chief Architect:\n\n"
            "👤 <b>Contact:</b> @therealonetom\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "id_status": (
            "🔍 <b>Tactical Identity Telemetry:</b>\n\n"
            "• <b>User ID:</b> <code>{id}</code>\n"
            "• <b>Alias:</b> @{username}\n"
            "• <b>Operational Rank:</b> {rank}\n"
            "• <b>Status:</b> Active 🟢\n\n"
            "<i>© Cloud Media Management</i>"
        ),
        "info_main": (
            "ℹ️ <b>System Core Architecture</b>\n\n"
            "<b>The Bunker Bot</b>\n"
            "• <b>Version:</b> 6.0 (Dual Group & Channel OS Core)\n"
            "• <b>Architect:</b> Master Tom\n"
            "• <b>Tactical Focus:</b> Multi-Sentinel Grid, Channel Membreships, Native Badges, and Absolute Security.\n\n"
            "🛡️ <i>Developed and supported by <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>How The Bunker Bot Works — The Master Guide</b>\n\n"
            "1️⃣ <b>Groups:</b> Add as admin to run Alphanumeric Captcha and AutoLower Radar.\n"
            "2️⃣ <b>Channels:</b> Add as admin to automate Star subscriptions and moderate Lives.\n"
            "3️⃣ <b>Clone Bots:</b> Link your BotFather token to keep 100% of pass revenues.\n"
            "4️⃣ <b>Dedicated Sentinel:</b> Connect secondary phone for 24/7 autonomous audio control.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_groups": (
            "🛡️ <b>Groups & Perimeter — Operations Guide</b>\n\n"
            "🔐 <b>Captcha Customs Pro:</b> Every new member faces a private DM challenge before "
            "gaining access to the group. The challenge carries a countdown timer — if the member fails "
            "to respond or answers incorrectly, they are automatically removed at the door, keeping bots "
            "and raiders out before they ever touch the community.\n\n"
            "🔒 <b>Granular Locks:</b> Independent switches let you restrict, per category, what regular "
            "members can post: Media (photos/videos), Links, Stickers & GIFs, and Bot Commands. Each toggle "
            "applies instantly and does not affect admins.\n\n"
            "🚫 <b>Anti-Spam Shield:</b> Continuously scans the chat for forwarded messages and quoted/reply "
            "spam patterns, deleting them automatically to stop flood attacks and copy-paste raids before "
            "they spread.\n\n"
            "📡 <b>AutoLower Radar:</b> During active voice chats, automatically attenuates every "
            "participant's ambient microphone down to 2% volume, eliminating background noise interference "
            "in real time without requiring manual moderation.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_channels": (
            "📡 <b>Channels & Lives — Operations Guide</b>\n\n"
            "🎙️ <b>Live Sentinel:</b> Automatically opens a member's microphone the moment they raise their "
            "hand during a live broadcast, granting speaking access without manual admin intervention and "
            "muting it back once they finish.\n\n"
            "🔗 <b>Paywalled Invites:</b> Generates single-use, cryptographically signed invite links tied "
            "to a specific subscriber and plan. The link self-destructs — it burns permanently — the instant "
            "it is used to join, making it impossible to resell or redistribute.\n\n"
            "🔁 <b>Recurring Audit:</b> Continuously monitors active subscriptions and sends an automatic "
            "renewal alert 48 hours before expiration. If the member does not renew in time, the system "
            "executes an auto-kick, removing delinquent accounts without manual follow-up.\n\n"
            "⭐ <b>Live Tips:</b> Allows viewers to send Stars-based tips directly during a live broadcast, "
            "crediting the channel owner's balance in real time.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_monetization": (
            "💰 <b>Stars Monetization — Operations Guide</b>\n\n"
            "🎙️ <b>VIP Microphone Pass (/micvip):</b> Sells temporary 24-hour speaking privileges in a "
            "voice chat. The buyer pays in Stars and automatically receives microphone access for the full "
            "duration, after which the privilege expires on its own — no manual revocation needed.\n\n"
            "🧬 <b>Bot Clone Architecture:</b> Owners can link their own BotFather token to run a fully "
            "independent clone of the system. 100% of the Stars generated through that clone are credited "
            "directly to the owner's own balance, with zero platform commission.\n\n"
            "💎 <b>Recurring Channel Plans:</b> Lets channel owners configure commercial subscription plans "
            "(e.g. Monthly VIP Pass) with a fixed duration and Stars price. Each plan generates its own "
            "deep-link for subscribers, and active subscriber counts are tracked automatically per plan.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Security Matrix:</b> {group_name}\n\nSelect a tactical module to alter community parameters.",
        "channel_panel_title": "📡 <b>Broadcast Studio:</b> {channel_name}\n\nSelect a module to manage lives, memberships, or studio automation.{perm_warning}",
        "pay_pro_title": (
            "⭐ <b>PRO Plan Subscription — {group_name} (300 Stars)</b>\n\n"
            "Upgrade your community to elite operational status:\n\n"
            "• ⚡ <b>Unlimited Bot Commands:</b> Bypass the 3 daily uses limit.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Service logs and chat clutter cleanup.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and timeout parameters.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Advanced shielding against channels, bots, and links.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>ULTRA PRO Subscription — {group_name} (600 Stars)</b>\n\n"
            "Total command, decentralized automation, and high-tier monetization:\n\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as a 24/7 voice mod via phone.\n"
            "• 🏷️ <b>Native VIP Tag Assignment:</b> Immovable badges (VIP 24/7) on Stars tips.\n"
            "• 🔇 <b>Smart AutoLower Radar:</b> Unverified mics get dialed down to 2% in milliseconds.\n"
            "• 💰 <b>Telegram Stars Monetization:</b> 100% revenue into your balance.\n"
            "• 📢 <b>Channel Membreships Engine:</b> Single-use cryptographic invite links & auto-kick.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Back to Main Menu",
        "btn_back_settings": "🔙 Back to Groups",
        "btn_back_chsettings": "🔙 Back to Channels",
        "btn_back_group": "🔙 Group Panel",
        "btn_back_channel": "🔙 Channel Panel",
        "btn_back_captcha": "🔙 Back to Captcha",
        "btn_back_antispam": "🔙 Back to Anti-Spam",
        "btn_back_antiflood": "🔙 Back to Anti-Flood",
        "mod_main": (
            "🛡️ <b>Tactical Moderation Matrix</b>\n\n"
            "Direct remote command console for <b>{group_name}</b>:\n\n"
            "• 🚫 <b>Ban (/ban):</b> Expulsion with time selector.\n"
            "• 👢 <b>Kick (/kick):</b> Immediate expulsion without permanent ban.\n"
            "• 🔇 <b>Mute (/mute):</b> Voice and message restriction with timeout.\n"
            "• 🔊 <b>Unmute (/unmute):</b> Immediate restoration of privileges.\n"
            "• ⚪ <b>Whitelist:</b> Total immunity for trusted allies.\n"
            "• ⚫ <b>Blacklist:</b> Banned keywords with automatic purging.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "eco_main": (
            "📡 <b>Radar & Ecosystem Control</b>\n\n"
            "Real-time telemetry and Voice Sentinel supervision for <b>{group_name}</b>:\n\n"
            "• 📡 <b>Radar Ecosistema:</b> Live ecosystem report and network latency.\n"
            "• 🎥 <b>/cams:</b> Stream quality audit & continuous audiovisual optimization.\n"
            "• ⚙️ <b>/autolower:</b> Voice chat volume moderation (2% vs 100%).\n"
            "• 🗓️ <b>/vcsched:</b> Automated Voice Chat opening/closing cron.\n"
            "• 🎙️ <b>/mic_vip:</b> VIP Microphone 24h pass pricing in Stars & Custom Tag.\n"
            "• ⭐ <b>Tips & Donations:</b> Channel tips & custom donation presets.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_mod": "🛡️ Moderation Matrix",
        "btn_eco": "📡 Radar & Ecosystem",
        "btn_antispam": "🛡️ Anti-Spam",
        "btn_antiflood": "🌊 Anti-Flood",
        "btn_captcha": "🤖 Captcha Protection",
        "btn_locks": "🔒 Locks",
        "btn_warns": "⚠️ Warns",
        "btn_delmsgs": "🗑️ Delete Messages",
        "btn_clone": "🧬 Clone & Sentinel",
        "captcha_main_title": (
            "🤖 <b>Captcha Protection (Captcha Pro)</b>\n\n"
            "When active, incoming recruits are restricted until they solve an alphanumeric challenge via Direct Message.\n\n"
            "• <b>Status:</b> {status_text}\n"
            "• <b>Alphanumeric Mode:</b> {mode_text}\n"
            "• <b>Time Limit:</b> {time_text}s\n"
            "• <b>Punishment:</b> {action_text}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "captcha_action_title": "⚖️ <b>Punishment Configuration</b>\n\n• <b>Active Punishment:</b> {mode_name} 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "antispam_main_title": (
            "✉️ <b>Granular Anti-Spam Matrix</b>\n\n"
            "• 📘 <b>Telegram Links:</b> {st_tg}\n"
            "• 📥 <b>Forwards Shield:</b> {st_fwd}\n"
            "• 💭 <b>Quotes Filter:</b> {st_q}\n"
            "• 🔗 <b>Internet Links:</b> {st_web}\n"
            "• 🗑️ <b>Delete Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Locks & Restrictions</b>\n\n"
            "• <b>Media (Photos/Videos):</b> {media}\n"
            "• <b>Stickers & GIFs:</b> {stickers}\n"
            "• <b>Web Links:</b> {links}\n"
            "• <b>Bot Commands:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Tactical Warnings (Warns Matrix)</b>\n\n"
            "• <b>Strike Limit:</b> {limit} warnings\n"
            "• <b>Automated Punishment:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Advanced Message Purge Center</b>\n\n"
            "• <b>Tier Status:</b> {tier_display}\n"
            "• <b>Privilege / Quota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Anti-Flood Shield</b>\n\n<b>Threshold:</b> {msgs} msgs in {time}s.\n<b>Active Action:</b> {action}\n<b>Delete Messages:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Forwards Shield Configuration</b>\n\nControl what forwarded transmissions are blocked in the community:",
        "clone_main_title": (
            "🧬 <b>Clone & Dedicated Sentinel Architecture</b>\n\n"
            "• <b>License:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Dedicated Sentinel:</b> {sentinel_status}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "botfather_guide": "🔑 <b>How to Connect Your Bot Clone</b>\n\nSend <code>/newbot</code> to @BotFather and paste the token here.",
        "sentinel_phone_guide": "🎙️ <b>Connect Dedicated Sentinel</b>\n\nSend your phone number with country code (e.g. <code>+12025550143</code>).",
        "captcha_saved": "✅ <b>Captcha message saved successfully!</b>\n\n<i>(This prompt will self-destruct in 60s)</i>",
        "token_verifying": "🔄 <b>Verifying bot token with Telegram servers...</b>",
        "token_success": "✅ <b>Token received and verified successfully!</b>",
        "token_error": "❌ <b>Invalid bot token.</b>",
        "phone_requesting": "🔄 <b>Requesting official Telegram code...</b>",
        "phone_sent": "📩 <b>Official Code Sent!</b>\n\nEnter the numerical code received below:",
        "phone_error": "❌ <b>Error sending code:</b> {reason}",
        "code_verifying": "🔄 <b>Verifying code and authorizing Sentinel...</b>",
        "sentinel_success": "💎 <b>Own Sentinel Connected Successfully!</b>",
        "sentinel_error": "❌ <b>Error initializing session.</b>",
        "twofa_required": "🔐 <b>Two-Step Verification (2FA) Required</b>\n\nEnter your password:",
        "code_invalid": "❌ <b>Invalid or expired code.</b>",
        "twofa_verifying": "🔄 <b>Validating 2FA password...</b>",
        "twofa_invalid": "❌ <b>Incorrect 2FA password.</b>",
        "sched_updated": "✅ <b>Schedule Updated!</b> (Start: {start} | End: {end})",
        "sched_err": "⚠️ Invalid format. Use HH:MM-HH:MM (e.g. <code>20:00-23:30</code>).",
        "wl_success": "✅ <b>User successfully whitelisted!</b> (ID: <code>{target_id}</code>)",
        "id_err": "⚠️ <b>Invalid ID.</b>",
        "bl_success": "✅ <b>Term blacklisted!</b> (<code>{word}</code>)",
        "dir_ban": "🚫 <b>Ban Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_kick": "👢 <b>Kick Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_mute": "🔇 <b>Mute Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_unmute": "🔊 <b>Privileges Restored!</b> (ID: <code>{target_id}</code>)",
        "dir_err": "❌ <b>Error executing directive:</b> {ex}",
        "mic_updated": "⭐ <b>VIP Mic Rate Updated to {price_val} Stars!</b>",
        "mic_err": "⚠️ Enter a positive integer.",
        "tag_updated": "🏷️ <b>VIP Tag Updated to: {text_input}</b>",
        "tag_err": "⚠️ Tag must be between 1 and 16 characters.",
        "mod_id_err": "⚠️ <b>Invalid Target.</b>",
        "btn_cancel_ret": "❌ Cancel & Return",
        "btn_retry": "🔄 Retry",
        "op_canceled": "Operation canceled and memory cleared.",
        "sentinel_disc": "🛑 Own Sentinel disconnected successfully.",
        "clone_disc": "🛑 Bot Clone disconnected successfully.",
        "tag_pro_req": "💎 Native tag editing requires ULTRA PRO tier.",
        "al_updated_1": "AutoLower updated 🟢",
        "al_updated_0": "AutoLower disabled 🔴",
        "mic_alert_set": "Rate set to {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directive: Tactical Whitelist</b>\n\nRegistered identities receive <b>Absolute Immunity</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directive: Global Blacklist</b>\n\nForbidden terms trigger auto-purge and warnings.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Camera & Video Chat Supervision</b>\n\nStream stability audit active.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Remote Control: Video Chat AutoLower</b>\n\n• <b>Current Status:</b> {status_str}\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>VIP Microphone Pass (Stars Monetization)</b>\n\n• <b>Current Rate:</b> <code>{curr_price} Stars</code>\n• <b>Tag:</b> <code>{curr_tag}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Native VIP Tag Editor</b> (Max 16 chars):",
        "mod_ask_time": "⚡ <b>Moderation Directive: /{sub_cmd}</b>\n\nSelect duration:",
        "mod_ask_target": "🎯 <b>Target Configuration — /{sub_cmd_upper}</b>\n\nSend @username or numeric ID:",
        "reg_ask": "📝 <b>Database Registration</b>\n\nSend {target_name}:",
        "reg_ask_wl": "the user ID for Whitelist",
        "reg_ask_bl": "the forbidden term for Blacklist",
        "vcsched_prompt": "⏰ <b>VC Schedule Configuration</b> (e.g. <code>20:00-23:30</code>):",
        "mic_custom_prompt": "⭐ <b>Custom Stars Rate</b>\n\nSend Stars amount per 24h pass:",
        "vcsched_main": "🗓️ <b>Voice Chat Scheduler (ULTRA PRO)</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Schedule:</b> <code>{start} - {end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_add_wl": "➕ Register User to DB",
        "btn_add_bl": "➕ Register Term to DB",
        "btn_al_1": "🟢 Activate AutoLower (2%)",
        "btn_al_0": "🔴 Disable (Free)",
        "btn_custom_rate": "✍️ Custom Rate",
        "btn_mictag": "🏷️ Tag: {curr_tag}",
        "btn_sched_mod": "⏰ Modify Schedule (HH:MM-HH:MM)",
        "btn_sched_off": "🔴 Disable Schedule",
        "btn_sched_on": "🟢 Enable Schedule",
        "af_msgs": "📄 Messages Threshold",
        "af_time": "⏱️ Time Window",
        "af_off": "❌ Disable",
        "af_warn": "⚠️ Warn",
        "af_kick": "👢 Kick",
        "af_mute": "🔇 Mute",
        "af_ban": "🚫 Ban",
        "af_del_msgs": "🗑️ Delete Messages",
        "fwd_chan": "📢 Channels",
        "fwd_usr": "👤 Users",
        "fwd_grp": "👥 Groups",
        "fwd_bot": "🤖 Bots",
        "btn_cfg_action": "⚙️ Configure",
        "btn_add_word": "➕ Register in DB",
        "btn_contact_support": "💬 Contact Support",
        "btn_back_mod": "🔙 Moderation",
        "btn_back_eco": "🔙 Ecosystem",
        "ultra_tools_main": "💎 <b>ULTRA PRO Elite Tools — {group_name}</b>\n\nDirect panel control over your highest-tier modules.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_ultra_tools": "💎 ULTRA Elite Tools",
        "ultra_lock_generic": "🔒 <i>This module is available exclusively at the ULTRA PRO tier.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_menu": "🚨 <b>Panic Button — Raid Lockdown</b>\n\n• <b>Status:</b> {status_str}",
        "panic_confirm": "⚠️ <b>Confirm Emergency Lockdown?</b>",
        "panic_activated": "🚨 <b>RAID LOCKDOWN ACTIVE</b>",
        "panic_deactivated": "🟢 <b>Lockdown lifted.</b>",
        "btn_panic_activate": "🚨 Activate Lockdown",
        "btn_panic_confirm": "✅ Confirm Lockdown",
        "btn_panic_deactivate": "🟢 Lift Lockdown",
        "shield_menu": "🎥 <b>Screen-Share Shield</b>\n\n• <b>Status:</b> {status_str}",
        "shield_updated_1": "🎥 Screen-Share Shield activated 🟢",
        "shield_updated_0": "🎥 Screen-Share Shield disabled 🔴",
        "btn_shield_1": "🟢 Activate Shield",
        "btn_shield_0": "🔴 Disable Shield",
        "podcast_menu": "🎙️ <b>Podcast Mode & Audio Ducking</b>\n\n• <b>Status:</b> {status_str}\n• <b>Level:</b> <code>{duck_level}%</code>",
        "podcast_updated_1": "🎙️ Podcast Mode activated 🟢",
        "podcast_updated_0": "🎙️ Podcast Mode disabled 🔴",
        "btn_podcast_1": "🟢 Activate Podcast Mode",
        "btn_podcast_0": "🔴 Disable",
        "btn_duck_level": "🎚️ Ducking Level: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Custom Ducking Level (1-90):</b>",
        "duck_updated": "🎚️ <b>Ducking level updated to {duck_level}%!</b>",
        "duck_err": "⚠️ Enter a number between 1 and 90.",
        "speakers_menu": "🌟 <b>Paid Speakers Queue (/speakers)</b>\n\n• <b>Status:</b> {status_str}\n• <b>Rate:</b> <code>{price} Stars</code>\n• <b>In Queue:</b> <code>{queue_count}</code>",
        "speakers_updated_1": "🌟 Speakers Queue activated 🟢",
        "speakers_updated_0": "🌟 Speakers Queue disabled 🔴",
        "btn_speakers_1": "🟢 Activate Queue",
        "btn_speakers_0": "🔴 Disable Queue",
        "btn_speakers_price": "⭐ Priority Rate: {price} Stars",
        "btn_speakers_clear": "🧹 Clear Queue",
        "speakers_price_prompt": "⭐ <b>Custom Priority Rate (Stars):</b>",
        "speakers_price_updated": "⭐ <b>Priority Rate Updated to {price} Stars!</b>",
        "speakers_price_err": "⚠️ Enter a positive integer.",
        "speakers_cleared": "🧹 Speakers queue cleared 🟢",
        "tips_main": "⭐ <b>Telegram Stars Tips & Donations</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Amount:</b> <code>{amount} Stars</code>\n• <b>Target:</b> <code>{target}</code>",
        "btn_tips": "⭐ Stars Tips & Donations",
        "tips_updated": "✅ Tips settings updated successfully!",
        "tips_prompt_amount": "💰 <b>Suggested Tip Amount (Stars):</b>",
        "tips_prompt_target": "📢 <b>Destination Channel (@username or ID):</b>",
        "tips_amount_err": "⚠️ Enter a positive integer.",
        "tips_target_err": "⚠️ Invalid target channel.",
        "sentinel_payload_main": "💎 <b>Sentinel Multimedia Payload (ULTRA PRO)</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Text:</b> {has_text}\n• <b>Media:</b> {has_media}\n• <b>Auto-Delete:</b> <code>{autodel}</code>",
        "btn_sentinel_payload": "💎 Multimedia Payload",
        "sentinel_payload_prompt_text": "✍️ <b>Custom Payload Text (HTML supported):</b>",
        "sentinel_payload_prompt_media": "🖼️ <b>Upload Media Asset (Photo, GIF, or Video):</b>",
        "sentinel_payload_prompt_del": "⏱️ <b>Auto-Delete Timeout in seconds (0 to keep):</b>",
        "sentinel_payload_saved": "✅ <b>Payload asset updated successfully!</b>",
        "sentinel_payload_err": "⚠️ Invalid input for payload asset.",

        "btn_night_mode": "🌙 Autonomous Night Mode",
        "night_main": (
            "🏴‍☠️ <b>Autonomous Night Mode (Phase 4)</b>\n\n"
            "Automates perimeter shielding during low-supervision hours:\n\n"
            "• <b>Status:</b> {st_badge}\n"
            "• <b>Schedule:</b> <code>{start} - {end}</code>\n"
            "• <b>Action:</b> <code>{action}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),  # <--- ¡Esta coma es la clave que faltaba!
        "night_prompt": "⏰ <b>Night Mode Schedule</b>\n\nSend the start and end interval in 24h format (example: <code>22:00-06:00</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "night_updated": "✅ <b>Night Mode schedule updated successfully!</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "night_err": "⚠️ Invalid format. Use HH:MM-HH:MM (Example: <code>22:00-06:00</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_night_on": "🟢 Enable Night Mode",
        "btn_night_off": "🔴 Disable Night Mode",
        "btn_night_mod": "⏰ Modify Schedule (HH:MM-HH:MM)",
        "btn_ai_sentinel": "🤖 AI Sentinel (ULTRA)",
        "ai_menu": (
         "🤖 <b>Artificial Intelligence Sentinel (ULTRA PRO)</b>\n\n"
         "• <b>Voice Guardian (Anti-Toxicity):</b> {guardian_st}\n"
         "• <b>Copilot AMA (Summaries):</b> {copilot_st}\n"
         "• <b>Custom Prompt:</b> <code>{custom_prompt}</code>\n\n"
         "🛡️ <i>Cloud Media Management</i>"
       ),
        "btn_ai_guardian": "🛡️ Anti-Toxicity Guardian: {guardian_st}",
        "btn_ai_copilot": "📝 Copilot AMA: {copilot_st}",
        "btn_ai_prompt": "✍️ Edit Custom AI Prompt",
        "ai_prompt_prompt": "✍️ <b>AI Prompt Editor:</b>\n\nSend the operational or behavioral instructions for your AI assistant:",
        "ai_prompt_saved": "✅ <b>AI Prompt updated successfully!</b>",  # <--- ¡Verifica que esta coma exista!
    },  # <--- Aquí cierra el bloque "en"
    "es": {
        "owner_only_alert": "⛔ Acceso Denegado: Esta consola táctica está reservada única y exclusivamente para el Dueño de la comunidad o canal.",
        "welcome": (
            "🏴‍☠️ <b>Bienvenido al Círculo Interno, {name}.</b>\n\n"
            "Soy <b>The Bunker Bot</b>, el núcleo arquitectónico de seguridad diseñado por <b>Master Tom</b>. Dentro de este dominio, posees autoridad absoluta para forjar el orden a partir del caos.\n\n"
            "Despliégame en tus grupos para defensa perimetral de élite, o en tus canales para automatizar suscripciones de pago y moderar transmisiones en vivo.\n\n"
            "Selecciona una opción abajo para auditar los módulos tácticos o acceder al Command Center.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add_group": "➕ Añadir a un Grupo",
        "btn_add_channel": "📢 Añadir a un Canal",
        "btn_settings": "⚙️ Configuración de Grupos",
        "btn_chsettings": "📡 Configuración de Canales",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ Mi ID y Estado",
        "btn_support": "🆘 Soporte",
        "btn_info": "ℹ️ Información",
        "btn_how_works": "📖 ¿Cómo funciona el Búnker?",
        "settings_main": (
            "🛡️ <b>Centro de Mando de Comunidades (Grupos)</b>\n\n"
            "Toma el control perimetral total de tu comunidad:\n\n"
            "• 🤖 Aduana Captcha alfanumérica.\n"
            "• 🔒 Cerraduras de contenido y filtros Anti-Spam.\n"
            "• 🎙️ Centinela Dedicado y atenuación acústica en videollamadas.\n"
            "• 💰 Monetización con Telegram Stars y etiquetas VIP personalizadas.\n\n"
            "<i>Selecciona abajo el grupo que deseas auditar y blindar:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "chsettings_main": (
            "📡 <b>Estudio de Lives & Membresías (Canales)</b>\n\n"
            "Consola de transmisión en vivo y gestión de suscriptores para canales:\n\n"
            "• 🎙️ <b>Centinela en Vivo:</b> Apertura de micrófono al levantar la mano solo a usuarios verificados.\n"
            "• 💎 <b>Aduana de Suscripciones:</b> Enlaces criptográficos de un solo uso que se queman al entrar.\n"
            "• ⏳ <b>Auditoría Recurrente:</b> Alertas previas y expulsión automática de miembros morosos.\n"
            "• ⭐ <b>Propinas en Directo:</b> Monetización transparente con Telegram Stars.\n\n"
            "<i>Selecciona abajo el canal que deseas gestionar:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "support_main": (
            "🆘 <b>Soporte Táctico Oficial</b>\n\n"
            "Para asistencia directa, pases de élite o arquitecturas personalizadas, contacta a nuestro Arquitecto Jefe:\n\n"
            "👤 <b>Contacto Directo:</b> @therealonetom\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "id_status": (
            "🔍 <b>Telemetría de Identidad Táctica:</b>\n\n"
            "• <b>User ID:</b> <code>{id}</code>\n"
            "• <b>Alias:</b> @{username}\n"
            "• <b>Rango Operativo:</b> {rank}\n"
            "• <b>Estado:</b> Activo 🟢\n\n"
            "<i>© Cloud Media Management</i>"
        ),
        "info_main": (
            "ℹ️ <b>Núcleo del Sistema</b>\n\n"
            "<b>The Bunker Bot</b>\n"
            "• <b>Versión:</b> 6.0 (Núcleo Dual de Grupos y Canales)\n"
            "• <b>Arquitecto:</b> Master Tom\n"
            "• <b>Enfoque Táctico:</b> Red Multi-Centinela, Membresías en Canales, Etiquetas Nativas VIP y Seguridad Absoluta.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>¿Cómo Funciona The Bunker Bot? — Guía Maestra</b>\n\n"
            "1️⃣ <b>Grupos:</b> Añade como admin para operar Captcha Alfanumérico y Radar AutoLower.\n"
            "2️⃣ <b>Canales:</b> Añade como admin para cobrar membresías en Stars y moderar Lives.\n"
            "3️⃣ <b>Bot Clon:</b> Conecta tu token de BotFather para conservar el 100% de ingresos.\n"
            "4️⃣ <b>Centinela Dedicado:</b> Asocia tu número de teléfono para moderación de voz 24/7 autónoma.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_groups": (
            "🛡️ <b>Grupos y Perímetro — Guía Operativa</b>\n\n"
            "🔐 <b>Aduana Captcha Pro:</b> Cada nuevo miembro enfrenta un desafío privado en DM antes de "
            "obtener acceso al grupo. El desafío tiene un temporizador de cuenta regresiva — si el miembro "
            "no responde a tiempo o falla la respuesta, es expulsado automáticamente en la puerta, "
            "manteniendo bots y raiders fuera antes de que toquen la comunidad.\n\n"
            "🔒 <b>Cerraduras Granulares:</b> Interruptores independientes te permiten restringir, por "
            "categoría, lo que los miembros regulares pueden publicar: Multimedia (fotos/videos), Enlaces, "
            "Stickers y GIFs, y Comandos de Bots. Cada interruptor aplica de forma instantánea y no afecta "
            "a los administradores.\n\n"
            "🚫 <b>Escudo Anti-Spam:</b> Escanea continuamente el chat en busca de mensajes reenviados y "
            "patrones de spam por citas/respuestas, eliminándolos automáticamente para frenar ataques de "
            "flood y raids de copiar-pegar antes de que se propaguen.\n\n"
            "📡 <b>Radar AutoLower:</b> Durante llamadas de voz activas, atenúa automáticamente el "
            "micrófono ambiental de cada participante hasta un 2% de volumen, eliminando la interferencia "
            "de ruido de fondo en tiempo real sin requerir moderación manual.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_channels": (
            "📡 <b>Canales y Lives — Guía Operativa</b>\n\n"
            "🎙️ <b>Centinela en Vivo:</b> Abre automáticamente el micrófono de un miembro en el instante "
            "en que levanta la mano durante una transmisión en vivo, otorgando acceso para hablar sin "
            "intervención manual del administrador, y lo silencia de nuevo al terminar.\n\n"
            "🔗 <b>Paywalled Invites:</b> Genera enlaces de invitación de un solo uso, firmados "
            "criptográficamente y ligados a un suscriptor y plan específicos. El enlace se autodestruye "
            "— se quema permanentemente — en el instante en que se usa para unirse, haciendo imposible "
            "revenderlo o redistribuirlo.\n\n"
            "🔁 <b>Auditoría Recurrente:</b> Monitorea de forma continua las suscripciones activas y envía "
            "una alerta automática de renovación 48 horas antes del vencimiento. Si el miembro no renueva "
            "a tiempo, el sistema ejecuta un auto-kick, removiendo cuentas morosas sin seguimiento manual.\n\n"
            "⭐ <b>Propinas en Directo:</b> Permite a los espectadores enviar propinas en Stars directamente "
            "durante una transmisión en vivo, acreditando el balance del dueño del canal en tiempo real.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "info_mod_monetization": (
            "💰 <b>Monetización Stars — Guía Operativa</b>\n\n"
            "🎙️ <b>Pase VIP de Micrófono (/micvip):</b> Vende privilegios temporales de 24 horas para "
            "hablar en una llamada de voz. El comprador paga en Stars y recibe automáticamente acceso al "
            "micrófono durante toda la duración, tras lo cual el privilegio expira por sí solo — sin "
            "revocación manual necesaria.\n\n"
            "🧬 <b>Arquitectura de Bots Clones:</b> Los dueños pueden conectar su propio token de "
            "BotFather para operar un clon totalmente independiente del sistema. El 100% de las Stars "
            "generadas a través de ese clon se acreditan directamente al balance del propio dueño, sin "
            "comisión alguna de la plataforma.\n\n"
            "💎 <b>Planes Comerciales de Canales Recurrentes:</b> Permite a los dueños de canales "
            "configurar planes de suscripción comercial (ej. Pase Mensual VIP) con duración fija y precio "
            "en Stars. Cada plan genera su propio deep-link para suscriptores, y el conteo de suscriptores "
            "activos se rastrea automáticamente por plan.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Matriz de Seguridad:</b> {group_name}\n\nSelecciona un módulo para alterar los parámetros de la comunidad.",
        "channel_panel_title": "📡 <b>Estudio de Transmisión:</b> {channel_name}\n\nSelecciona un módulo para configurar transmisiones en vivo, suscripciones o automatizaciones.{perm_warning}",
        "pay_pro_title": (
            "⭐ <b>Suscripción Plan PRO — {group_name} (300 Stars)</b>\n\n"
            "• ⚡ <b>Comandos de Bot Ilimitados.</b>\n"
            "• 🗑️ <b>Purga Automatizada de mensajes de servicio.</b>\n"
            "• 🤖 <b>Aduana Captcha Pro Avanzada.</b>\n"
            "• 🛡️ <b>Anti-Spam Granular Total.</b>\n\n"
            "<i>Selecciona tu pasarela preferida:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>Suscripción ULTRA PRO — {group_name} (600 Stars)</b>\n\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Tu réplica bajo tu propio token.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Moderación acústica en videollamadas vía teléfono.\n"
            "• 🏷️ <b>Etiquetas Nativas VIP y Radar AutoLower al 2%.</b>\n"
            "• 💰 <b>Monetización Stars directa a tu balance.</b>\n"
            "• 📢 <b>Motor de Membresías en Canales:</b> Enlaces de 1 solo uso y expulsión automática.\n\n"
            "<i>Selecciona tu pasarela preferida:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Volver al Menú Principal",
        "btn_back_settings": "🔙 Volver a Grupos",
        "btn_back_chsettings": "🔙 Volver a Canales",
        "btn_back_group": "🔙 Panel del Grupo",
        "btn_back_channel": "🔙 Panel del Canal",
        "btn_back_captcha": "🔙 Volver a Captcha",
        "btn_back_antispam": "🔙 Volver a Anti-Spam",
        "btn_back_antiflood": "🔙 Volver a Anti-Flood",
        "mod_main": (
            "🛡️ <b>Matriz de Moderación Táctica</b>\n\n"
            "Consola remota para <b>{group_name}</b>:\n\n"
            "• 🚫 <b>Baneo (/ban) y Expulsión (/kick)</b>\n"
            "• 🔇 <b>Silencio (/mute) y Restaurar (/unmute)</b>\n"
            "• ⚪ <b>Lista Blanca (Whitelist) y Lista Negra (Blacklist)</b>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "eco_main": (
            "📡 <b>Radar y Control del Ecosistema</b>\n\n"
            "• 📡 <b>Radar Ecosistema y /cams</b>\n"
            "• ⚙️ <b>/autolower y Programador VC</b>\n"
            "• 🎙️ <b>/mic_vip y Propinas Stars</b>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_mod": "🛡️ Matriz de Moderación",
        "btn_eco": "📡 Radar y Ecosistema",
        "btn_antispam": "🛡️ Anti-Spam",
        "btn_antiflood": "🌊 Anti-Flood",
        "btn_captcha": "🤖 Captcha Protection",
        "btn_locks": "🔒 Cerraduras",
        "btn_warns": "⚠️ Advertencias",
        "btn_delmsgs": "🗑️ Borrar Mensajes",
        "btn_clone": "🧬 Clon & Centinela",
        "captcha_main_title": (
            "🤖 <b>Protección Captcha (Captcha Pro)</b>\n\n"
            "• <b>Estado:</b> {status_text}\n"
            "• <b>Modo Alfanumérico:</b> {mode_text}\n"
            "• <b>Tiempo Límite:</b> {time_text}s\n"
            "• <b>Castigo:</b> {action_text}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "captcha_action_title": "⚖️ <b>Configuración de Castigo</b>\n\n• <b>Castigo Activo:</b> {mode_name} 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "antispam_main_title": (
            "✉️ <b>Matriz Anti-Spam Granular</b>\n\n"
            "• 📘 <b>Enlaces Telegram:</b> {st_tg}\n"
            "• 📥 <b>Escudo Reenvíos:</b> {st_fwd}\n"
            "• 💭 <b>Filtro Citas:</b> {st_q}\n"
            "• 🔗 <b>Enlaces Internet:</b> {st_web}\n"
            "• 🗑️ <b>Borrado Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Panel de Cerraduras (Locks)</b>\n\n"
            "• <b>Multimedia:</b> {media}\n"
            "• <b>Stickers/GIFs:</b> {stickers}\n"
            "• <b>Enlaces:</b> {links}\n"
            "• <b>Comandos:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Matriz de Advertencias (Warns)</b>\n\n"
            "• <b>Límite Strikes:</b> {limit}\n"
            "• <b>Castigo:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Centro Avanzado de Purga de Mensajes</b>\n\n"
            "• <b>Nivel del Plan:</b> {tier_display}\n"
            "• <b>Privilegio / Cuota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Escudo Anti-Flood</b>\n\n<b>Umbral:</b> {msgs} msgs en {time}s.\n<b>Acción:</b> {action}\n<b>Borrar:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Configuración del Escudo de Reenvíos</b>",
        "clone_main_title": (
            "🧬 <b>Arquitectura de Clonación & Centinela Dedicado</b>\n\n"
            "• <b>Licencia:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Centinela Dedicado:</b> {sentinel_status}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "botfather_guide": "🔑 <b>Conectar Bot Clon:</b> Envía <code>/newbot</code> a @BotFather y pega el token aquí.",
        "sentinel_phone_guide": "🎙️ <b>Conectar Centinela:</b> Envía tu número telefónico con código internacional (ej. <code>+573001234567</code>).",
        "captcha_saved": "✅ <b>¡Mensaje de captcha guardado!</b>\n\n<i>(Este aviso se auto-eliminará en 60s)</i>",
        "token_verifying": "🔄 <b>Verificando token con Telegram...</b>",
        "token_success": "✅ <b>¡Token recibido y verificado correctamente!</b>",
        "token_error": "❌ <b>Token inválido.</b>",
        "phone_requesting": "🔄 <b>Solicitando código oficial de Telegram...</b>",
        "phone_sent": "📩 <b>¡Código Oficial Enviado!</b>\n\nEscribe el código numérico recibido a continuación:",
        "phone_error": "❌ <b>Error al enviar código:</b> {reason}",
        "code_verifying": "🔄 <b>Verificando código y autorizando Centinela...</b>",
        "sentinel_success": "💎 <b>¡Centinela Propio Conectado con Éxito!</b>",
        "sentinel_error": "❌ <b>Error al inicializar sesión.</b>",
        "twofa_required": "🔐 <b>Verificación en Dos Pasos (2FA) Requerida</b>\n\nIngresa tu contraseña:",
        "code_invalid": "❌ <b>Código inválido o expirado.</b>",
        "twofa_verifying": "🔄 <b>Validando contraseña 2FA...</b>",
        "twofa_invalid": "❌ <b>Contraseña 2FA incorrecta.</b>",
        "sched_updated": "✅ <b>¡Horario Actualizado!</b> (Inicio: {start} | Cierre: {end})",
        "sched_err": "⚠️ Formato incorrecto. Usa HH:MM-HH:MM (ej. <code>20:00-23:30</code>).",
        "wl_success": "✅ <b>¡Usuario registrado en Whitelist!</b> (ID: <code>{target_id}</code>)",
        "id_err": "⚠️ <b>ID inválido.</b>",
        "bl_success": "✅ <b>¡Término prohibido registrado en Blacklist!</b> (<code>{word}</code>)",
        "dir_ban": "🚫 <b>¡Baneo ejecutado remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_kick": "👢 <b>¡Expulsión ejecutada remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_mute": "🔇 <b>¡Silencio ejecutado remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_unmute": "🔊 <b>¡Permisos restaurados!</b> (ID: <code>{target_id}</code>)",
        "dir_err": "❌ <b>Error al ejecutar directiva:</b> {ex}",
        "mic_updated": "⭐ <b>¡Tarifa VIP de Micrófono actualizada a {price_val} Stars!</b>",
        "mic_err": "⚠️ Ingresa un número entero positivo.",
        "tag_updated": "🏷️ <b>¡Etiqueta VIP actualizada a: {text_input}!</b>",
        "tag_err": "⚠️ La etiqueta debe tener entre 1 y 16 caracteres.",
        "mod_id_err": "⚠️ <b>Objetivo inválido.</b>",
        "btn_cancel_ret": "❌ Cancelar y Volver",
        "btn_retry": "🔄 Reintentar",
        "op_canceled": "Operación cancelada y memoria liberada.",
        "sentinel_disc": "🛑 Centinela propio desconectado con éxito.",
        "clone_disc": "🛑 Bot Clon desconectado con éxito.",
        "tag_pro_req": "💎 Requiere nivel ULTRA PRO.",
        "al_updated_1": "AutoLower actualizado 🟢",
        "al_updated_0": "AutoLower desactivado 🔴",
        "mic_alert_set": "Tarifa configurada a {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directiva: Lista Blanca Táctica (Whitelist)</b>\n\nIdentidades con <b>Inmunidad Absoluta</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directiva: Lista Negra Global (Blacklist)</b>\n\nTérminos con purga automática y advertencias.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Supervisión de Cámaras y Transmisiones</b>\n\nAuditoría en vivo de estabilidad activa.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Control Remoto: AutoLower de Videollamada</b>\n\n• <b>Estado:</b> {status_str}\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>Pase VIP de Micrófono (Stars)</b>\n\n• <b>Tarifa:</b> <code>{curr_price} Stars</code>\n• <b>Etiqueta:</b> <code>{curr_tag}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Editor de Etiqueta VIP Nativa</b> (Máx 16 caracteres):",
        "mod_ask_time": "⚡ <b>Directiva: /{sub_cmd}</b>\n\nSelecciona la duración:",
        "mod_ask_target": "🎯 <b>Objetivo — /{sub_cmd_upper}</b>\n\nEnvía el @usuario o ID:",
        "reg_ask": "📝 <b>Registro en Base de Datos</b>\n\nEnvía {target_name}:",
        "reg_ask_wl": "la ID del usuario para Whitelist",
        "reg_ask_bl": "el término prohibido para Blacklist",
        "vcsched_prompt": "⏰ <b>Configuración Horario VC</b> (ej. <code>20:00-23:30</code>):",
        "mic_custom_prompt": "⭐ <b>Tarifa Personalizada de Stars:</b>",
        "vcsched_main": "🗓️ <b>Programador de Videochats (ULTRA PRO)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Horario:</b> <code>{start} - {end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_add_wl": "➕ Registrar Usuario en BD",
        "btn_add_bl": "➕ Registrar Término en BD",
        "btn_al_1": "🟢 Activar AutoLower (2%)",
        "btn_al_0": "🔴 Desactivar (Libre)",
        "btn_custom_rate": "✍️ Tarifa Personalizada",
        "btn_mictag": "🏷️ Etiqueta: {curr_tag}",
        "btn_sched_mod": "⏰ Modificar Horario (HH:MM-HH:MM)",
        "btn_sched_off": "🔴 Desactivar Cronograma",
        "btn_sched_on": "🟢 Activar Cronograma",
        "af_msgs": "📄 Umbral Mensajes",
        "af_time": "⏱️ Ventana de Tiempo",
        "af_off": "❌ Desactivar",
        "af_warn": "⚠️ Advertir",
        "af_kick": "👢 Expulsar",
        "af_mute": "🔇 Silenciar",
        "af_ban": "🚫 Bloquear",
        "af_del_msgs": "🗑️ Borrar Mensajes",
        "fwd_chan": "📢 Canales",
        "fwd_usr": "👤 Usuarios",
        "fwd_grp": "👥 Grupos",
        "fwd_bot": "🤖 Bots",
        "btn_cfg_action": "⚙️ Configurar",
        "btn_add_word": "➕ Registrar Término / Usuario",
        "btn_contact_support": "💬 Contactar Soporte",
        "btn_back_mod": "🔙 Moderación",
        "btn_back_eco": "🔙 Ecosistema",
        "ultra_tools_main": "💎 <b>Herramientas de Élite ULTRA PRO — {group_name}</b>\n\nControl perimetral avanzado y transmisión.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_ultra_tools": "💎 Herramientas ULTRA",
        "ultra_lock_generic": "🔒 <i>Este módulo está disponible exclusivamente en el nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_menu": "🚨 <b>Botón de Pánico — Bloqueo de Emergencia</b>\n\n• <b>Estado:</b> {status_str}",
        "panic_confirm": "⚠️ <b>¿Confirmar Bloqueo de Emergencia?</b>",
        "panic_activated": "🚨 <b>BLOQUEO DE EMERGENCIA ACTIVO</b>",
        "panic_deactivated": "🟢 <b>Bloqueo levantado.</b>",
        "btn_panic_activate": "🚨 Activar Bloqueo",
        "btn_panic_confirm": "✅ Confirmar Bloqueo",
        "btn_panic_deactivate": "🟢 Levantar Bloqueo",
        "shield_menu": "🎥 <b>Escudo Antinota (Pantalla Compartida)</b>\n\n• <b>Estado:</b> {status_str}",
        "shield_updated_1": "🎥 Escudo Antinota activado 🟢",
        "shield_updated_0": "🎥 Escudo Antinota desactivado 🔴",
        "btn_shield_1": "🟢 Activar Escudo",
        "btn_shield_0": "🔴 Desactivar Escudo",
        "podcast_menu": "🎙️ <b>Modo Podcast & Audio Ducking</b>\n\n• <b>Estado:</b> {status_str}\n• <b>Ducking:</b> <code>{duck_level}%</code>",
        "podcast_updated_1": "🎙️ Modo Podcast activado 🟢",
        "podcast_updated_0": "🎙️ Modo Podcast desactivado 🔴",
        "btn_podcast_1": "🟢 Activar Modo Podcast",
        "btn_podcast_0": "🔴 Desactivar",
        "btn_duck_level": "🎚️ Nivel de Ducking: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Nivel de Ducking Personalizado (1-90):</b>",
        "duck_updated": "🎚️ <b>¡Ducking actualizado al {duck_level}%!</b>",
        "duck_err": "⚠️ Ingresa un número entero entre 1 y 90.",
        "speakers_menu": "🌟 <b>Cola de Speakers Pagada (/speakers)</b>\n\n• <b>Estado:</b> {status_str}\n• <b>Tarifa:</b> <code>{price} Stars</code>\n• <b>En Cola:</b> <code>{queue_count}</code>",
        "speakers_updated_1": "🌟 Cola de Speakers activada 🟢",
        "speakers_updated_0": "🌟 Cola de Speakers desactivada 🔴",
        "btn_speakers_1": "🟢 Activar Cola",
        "btn_speakers_0": "🔴 Desactivar Cola",
        "btn_speakers_price": "⭐ Tarifa Prioridad: {price} Stars",
        "btn_speakers_clear": "🧹 Vaciar Cola",
        "speakers_price_prompt": "⭐ <b>Tarifa de Prioridad Personalizada (Stars):</b>",
        "speakers_price_updated": "⭐ <b>¡Tarifa de Prioridad Actualizada a {price} Stars!</b>",
        "speakers_price_err": "⚠️ Ingresa un entero positivo de Stars.",
        "speakers_cleared": "🧹 Cola de speakers vaciada 🟢",
        "tips_main": "⭐ <b>Propinas y Donaciones con Telegram Stars (XTR)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Monto:</b> <code>{amount} Stars</code>\n• <b>Canal:</b> <code>{target}</code>",
        "btn_tips": "⭐ Propinas Stars",
        "tips_updated": "✅ ¡Ajustes de propinas actualizados con éxito!",
        "tips_prompt_amount": "💰 <b>Monto Sugerido de Propinas (Stars):</b>",
        "tips_prompt_target": "📢 <b>Canal Destino (@usuario o ID):</b>",
        "tips_amount_err": "⚠️ Ingresa un número entero positivo.",
        "tips_target_err": "⚠️ Canal objetivo no válido.",
        "sentinel_payload_main": "💎 <b>Payload Multimedia del Centinela (ULTRA PRO)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Texto:</b> {has_text}\n• <b>Multimedia:</b> {has_media}\n• <b>Auto-Borrado:</b> <code>{autodel}</code>",
        "btn_sentinel_payload": "💎 Payload Multimedia",
        "sentinel_payload_prompt_text": "✍️ <b>Texto Personalizado del Payload (HTML soportado):</b>",
        "sentinel_payload_prompt_media": "🖼️ <b>Carga de Multimedia del Payload (Foto, GIF o Video):</b>",
        "sentinel_payload_prompt_del": "⏱️ <b>Tiempo de Auto-Borrado en segundos (0 para mantener):</b>",
        "sentinel_payload_saved": "✅ <b>¡Activo de payload guardado correctamente!</b>",
       "sentinel_payload_err": "⚠️ Entrada no válida para el activo multimedia del payload.",
        "btn_night_mode": "🌙 Modo Nocturno Autónomo",
        "night_main": (
            "🌙 <b>Modo Nocturno Autónomo (Fase 4)</b>\n\n"
            "Automatiza el blindaje perimetral de tu comunidad durante las horas de menor supervisión:\n\n"
            "• <b>Estado:</b> {st_badge}\n"
            "• <b>Horario:</b> <code>{start} - {end}</code>\n"
            "• <b>Acción:</b> <code>{action}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "night_prompt": "⏰ <b>Configuración de Horario Nocturno</b>\n\nEnvía en este chat el intervalo de inicio y cierre en formato 24h (ejemplo: <code>22:00-06:00</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "night_updated": "✅ <b>¡Horario del Modo Nocturno actualizado con éxito!</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "night_err": "⚠️ Formato incorrecto. Usa HH:MM-HH:MM (Ejemplo: <code>22:00-06:00</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_night_on": "🟢 Activar Modo Nocturno",
        "btn_night_off": "🔴 Desactivar Modo Nocturno",
        "btn_night_mod": "⏰ Modificar Horario (HH:MM-HH:MM)",
        "btn_ai_sentinel": "🤖 Centinela de IA (ULTRA)",
     "ai_menu": (
         "🤖 <b>Centinela de Inteligencia Artificial (ULTRA PRO)</b>\n\n"
         "• <b>Guardián de Voz (Anti-Toxicidad):</b> {guardian_st}\n"
         "• <b>Copilot AMA (Resúmenes):</b> {copilot_st}\n"
         "• <b>Prompt Personalizado:</b> <code>{custom_prompt}</code>\n\n"
         "🛡️ <i>Cloud Media Management</i>"
     ),
     "btn_ai_guardian": "🛡️ Guardián Anti-Toxicidad: {guardian_st}",
     "btn_ai_copilot": "📝 Copilot AMA: {copilot_st}",
     "btn_ai_prompt": "✍️ Editar Prompt de IA",
     "ai_prompt_prompt": "✍️ <b>Editor de Prompt de IA:</b>\n\nEnvía las instrucciones operativas o de comportamiento para tu asistente de inteligencia artificial:",
     "ai_prompt_saved": "✅ <b>¡Prompt de IA actualizado con éxito!</b>",
    }
}

# ==========================================
# 🧩 TEXTOS COMPLEMENTARIOS (SINCRONIZACIÓN, RESILIENCIA Y NAVEGACIÓN)
# ==========================================
_EXTRA_TEXTS = {
    "es": {
        "btn_sync_channels": "🔄 Sincronizar Canales",
        "btn_pick_channel": "📡 Seleccionar mi canal",
        "btn_sync_cancel": "❌ Cancelar",
        "btn_sync_placeholder": "Toca «Seleccionar mi canal»",
        "btn_open_studio": "📡 Abrir Estudio",
        "btn_main_menu": "🏠 Menú Principal",
        "btn_back_ultra": "🔙 Herramientas ULTRA",
        "btn_back_tool": "🔙 Volver al Módulo",
        "btn_create_plan": "➕ Crear Nuevo Plan",
        "chsync_prompt": (
            "📡 <b>Sincronización de Canales</b>\n\n"
            "Toca el botón de abajo y Telegram te mostrará <b>tus canales</b> donde el bot ya es miembro. "
            "Elige el que quieras vincular: verificaré tus permisos en vivo y lo añadiré a tu lista al instante, "
            "sin volver a agregar el bot.\n\n"
            "<i>Solo el propietario del canal puede sincronizarlo.</i>"
        ),
        "chsync_ok": (
            "✅ <b>Canal sincronizado</b>\n\n"
            "• <b>Canal:</b> {title}\n"
            "• <b>Bot:</b> Administrador 🟢\n\n"
            "Ya aparece en tu lista de canales."
        ),
        "chsync_bot_not_admin": (
            "⚠️ <b>Falta un paso</b>\n\n"
            "El bot no es administrador de <b>{title}</b>. Abre <i>Administradores</i> del canal, añade a @{bot_username} "
            "con permisos para publicar, editar y eliminar mensajes, gestionar videochats e invitar usuarios, "
            "y vuelve a pulsar «Sincronizar Canales»."
        ),
        "chsync_not_owner": "⛔ Solo el propietario del canal puede sincronizarlo con este panel.",
        "chsync_stale": "ℹ️ Esa solicitud ya no es válida. Pulsa «Sincronizar Canales» para intentarlo de nuevo.",
        "chsettings_empty_hint": (
            "ℹ️ <b>No encontré canales vinculados a tu cuenta.</b>\n"
            "Pulsa «🔄 Sincronizar Canales», elige el tuyo y quedará listo al instante (sin volver a agregar el bot)."
        ),
        "session_expired": (
            "⏳ <b>No tengo ninguna acción pendiente contigo</b>\n\n"
            "Si el servidor se reinició o pasó mucho tiempo, la operación en curso se canceló por seguridad. "
            "Vuelve al menú para retomarla."
        ),
        "plan_state_lost": (
            "⏳ <b>La creación del plan expiró</b>\n\n"
            "El servidor se reinició o pasó demasiado tiempo. No se guardó nada; puedes iniciar de nuevo."
        ),
        "plan_create_error": (
            "⚠️ <b>No pude crear el plan</b>\n\n"
            "Ocurrió un error al guardarlo y no se publicó nada. Inténtalo de nuevo en unos segundos."
        ),
        "night_save_failed": "⚠️ No pude guardar el horario del Modo Nocturno. Inténtalo de nuevo en unos segundos.",
        "mod_immune": "🛡️ <b>Objetivo inmune.</b> Los Arquitectos, las cuentas de servicio y el propio bot no pueden ser sancionados.",
        "mod_flood_wait": "⏳ Telegram limitó temporalmente las acciones. Reintenta en <b>{seconds}s</b>.",
        "perm_bot_not_admin": (
            "\n\n⚠️ <b>El bot ya no es administrador de este canal.</b> "
            "Vuelve a otorgarle permisos para reactivar todas las funciones."
        ),
        "perm_missing": (
            "\n\n⚠️ <b>Permisos faltantes del bot:</b> {missing}\n"
            "<i>Actívalos en Administradores del canal para habilitar todas las funciones.</i>"
        ),
    },
    "en": {
        "btn_sync_channels": "🔄 Sync Channels",
        "btn_pick_channel": "📡 Select my channel",
        "btn_sync_cancel": "❌ Cancel",
        "btn_sync_placeholder": "Tap “Select my channel”",
        "btn_open_studio": "📡 Open Studio",
        "btn_main_menu": "🏠 Main Menu",
        "btn_back_ultra": "🔙 ULTRA Tools",
        "btn_back_tool": "🔙 Back to Module",
        "btn_create_plan": "➕ Create New Plan",
        "chsync_prompt": (
            "📡 <b>Channel Sync</b>\n\n"
            "Tap the button below and Telegram will show <b>your channels</b> where the bot is already a member. "
            "Pick the one you want to link: I'll verify your permissions live and add it to your list instantly, "
            "with no need to re-add the bot.\n\n"
            "<i>Only the channel owner can sync it.</i>"
        ),
        "chsync_ok": (
            "✅ <b>Channel synced</b>\n\n"
            "• <b>Channel:</b> {title}\n"
            "• <b>Bot:</b> Administrator 🟢\n\n"
            "It now shows up in your channel list."
        ),
        "chsync_bot_not_admin": (
            "⚠️ <b>One step missing</b>\n\n"
            "The bot is not an administrator of <b>{title}</b>. Open the channel's <i>Administrators</i>, add @{bot_username} "
            "with permission to post, edit and delete messages, manage video chats and invite users, "
            "then tap “Sync Channels” again."
        ),
        "chsync_not_owner": "⛔ Only the channel owner can sync it with this panel.",
        "chsync_stale": "ℹ️ That request is no longer valid. Tap “Sync Channels” to try again.",
        "chsettings_empty_hint": (
            "ℹ️ <b>I couldn't find any channels linked to your account.</b>\n"
            "Tap “🔄 Sync Channels”, pick yours and it will be ready instantly (no need to re-add the bot)."
        ),
        "session_expired": (
            "⏳ <b>I have no pending action with you</b>\n\n"
            "If the server restarted or too much time passed, the operation in progress was canceled for safety. "
            "Go back to the menu to resume it."
        ),
        "plan_state_lost": (
            "⏳ <b>Plan creation expired</b>\n\n"
            "The server restarted or too much time passed. Nothing was saved; you can start again."
        ),
        "plan_create_error": (
            "⚠️ <b>I couldn't create the plan</b>\n\n"
            "An error occurred while saving it and nothing was published. Please try again in a few seconds."
        ),
        "night_save_failed": "⚠️ I couldn't save the Night Mode schedule. Please try again in a few seconds.",
        "mod_immune": "🛡️ <b>Immune target.</b> Architects, service accounts and the bot itself cannot be sanctioned.",
        "mod_flood_wait": "⏳ Telegram temporarily rate-limited actions. Retry in <b>{seconds}s</b>.",
        "perm_bot_not_admin": (
            "\n\n⚠️ <b>The bot is no longer an administrator of this channel.</b> "
            "Grant it permissions again to re-enable all features."
        ),
        "perm_missing": (
            "\n\n⚠️ <b>Missing bot permissions:</b> {missing}\n"
            "<i>Enable them in the channel's Administrators to unlock all features.</i>"
        ),
    },
}

for _lang_code, _extra in _EXTRA_TEXTS.items():
    TEXTS[_lang_code].update(_extra)

# Vista del selector de canales cuando aún no hay ninguno vinculado (incluye la guía de sincronización).
_SIGNATURE_LINE = "© <i>Cloud Media Management</i>"
for _lang_code, _bundle in TEXTS.items():
    _hint = _bundle["chsettings_empty_hint"]
    _base = _bundle["chsettings_main"]
    _bundle["chsettings_main_empty"] = (
        _base.replace(_SIGNATURE_LINE, f"{_hint}\n\n{_SIGNATURE_LINE}", 1)
        if _SIGNATURE_LINE in _base else f"{_base}\n\n{_hint}"
    )

# ==========================================
# 🏷️ FIRMA PERIMETRAL OFICIAL — "Cloud Media Management"
# Se aplica automáticamente a todo mensaje de consola. Quedan exentos: botones (btn_*), etiquetas cortas,
# alertas emergentes de Telegram (límite de 200 caracteres) y fragmentos que se incrustan en otros textos.
# ==========================================
PERIMETER_SIGNATURE = "\n\n🛡️ <i>Cloud Media Management</i>"
_SIGNATURE_EXEMPT_PREFIXES = ("btn_", "af_", "fwd_", "perm_")
_SIGNATURE_EXEMPT_KEYS = {
    "owner_only_alert", "op_canceled", "sentinel_disc", "clone_disc", "tag_pro_req",
    "al_updated_1", "al_updated_0", "mic_alert_set", "shield_updated_1", "shield_updated_0",
    "podcast_updated_1", "podcast_updated_0", "speakers_updated_1", "speakers_updated_0",
    "speakers_cleared", "duck_updated", "speakers_price_updated",
    "reg_ask_wl", "reg_ask_bl", "chsettings_empty_hint",
}
for _bundle in TEXTS.values():
    for _key, _value in list(_bundle.items()):
        if (
            isinstance(_value, str)
            and _key not in _SIGNATURE_EXEMPT_KEYS
            and not _key.startswith(_SIGNATURE_EXEMPT_PREFIXES)
            and "Cloud Media Management" not in _value
        ):
            _bundle[_key] = _value + PERIMETER_SIGNATURE


# ==========================================
# 🛡️ NÚCLEO DE RESILIENCIA (SEGURO ANTE REINICIOS DE RAM / RAILWAY)
# ==========================================
def tr(lang: str, es: str, en: str) -> str:
    """Selector bilingüe inline (Español / Inglés)."""
    return es if lang == "es" else en


def user_lang(user) -> str:
    code = getattr(user, "language_code", None) or ""
    return "es" if code.startswith("es") else "en"


def clear_user_states(bot_id: int, user_id: int) -> None:
    """Libera TODA conversación pendiente del usuario en este bot (evita menús privados bloqueados)."""
    key = (bot_id, user_id)
    for state_dict in ALL_STATE_DICTS:
        state_dict.pop(key, None)
    forget_plan_state(bot_id, user_id)


async def safe_edit_text(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """
    Edición blindada de la consola: si el mensaje previo tiene foto, edita el caption
    o reemplaza el mensaje de forma segura sin disparar 'there is no text in the message to edit'.
    """
    msg = callback.message
    if msg is not None:
        try:
            if getattr(msg, "photo", None):
                # Si el mensaje actual es una foto, editamos su caption en lugar de edit_text
                return await msg.edit_caption(caption=text[:1024], reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
            elif hasattr(msg, "edit_text"):
                return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
        except TelegramBadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                return None
            logging.info(f"ℹ️ [safe_edit_text] Mensaje no editable ({e}), reemplazando...")
        except TelegramForbiddenError:
            return None

    # Fallback si el mensaje fue borrado o no se puede transformar
    try:
        if msg is not None and hasattr(msg, "delete"):
            try:
                await msg.delete()
            except Exception:
                pass
        target_bot = getattr(callback, "bot", None)
        if target_bot is None:
            return None
        return await target_bot.send_message(
            chat_id=callback.from_user.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
    except Exception as ex:
        logging.error(f"❌ [safe_edit_text] No se pudo renderizar la vista: {ex}")
        return None

# ------------------------------------------
# 📒 REGISTRO PROPIO DE CANALES (persistente y autorreparable)
# Se alimenta solo con canales verificados EN VIVO contra Telegram (bot admin + usuario propietario).
# ------------------------------------------
_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS channel_owner_registry (
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    title      TEXT,
    updated_at INTEGER,
    PRIMARY KEY (user_id, channel_id)
)
"""


async def registry_get_channels(user_id: int) -> list:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_REGISTRY_DDL)
            cursor.execute("SELECT channel_id, title FROM channel_owner_registry WHERE user_id = ?", (user_id,))
            rows = cursor.fetchall()
            conn.commit()
            return [(int(r[0]), r[1] or "") for r in rows]
    try:
        return await asyncio.to_thread(_sync)
    except Exception as ex:
        logging.warning(f"⚠️ [Registro Canales] Lectura fallida para user={user_id}: {ex}")
        return []


async def registry_upsert_channel(user_id: int, channel_id: int, title: str) -> None:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_REGISTRY_DDL)
            cursor.execute(
                "INSERT OR REPLACE INTO channel_owner_registry (user_id, channel_id, title, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, channel_id, title or "", int(time.time()))
            )
            conn.commit()
    try:
        await asyncio.to_thread(_sync)
    except Exception as ex:
        logging.warning(f"⚠️ [Registro Canales] Escritura fallida user={user_id} channel={channel_id}: {ex}")


async def registry_has_owner(user_id: int, channel_id: int) -> bool:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_REGISTRY_DDL)
            cursor.execute(
                "SELECT 1 FROM channel_owner_registry WHERE user_id = ? AND channel_id = ? LIMIT 1",
                (user_id, channel_id)
            )
            found = cursor.fetchone() is not None
            conn.commit()
            return found
    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        return False


async def registry_has_channel(channel_id: int) -> bool:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_REGISTRY_DDL)
            cursor.execute("SELECT 1 FROM channel_owner_registry WHERE channel_id = ? LIMIT 1", (channel_id,))
            found = cursor.fetchone() is not None
            conn.commit()
            return found
    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        return False


# ------------------------------------------
# 💾 PERSISTENCIA DEL ASISTENTE DE PLANES (sobrevive a reinicios en frío de Railway)
# CHAN_PLAN_STATES vive en RAM; cada paso se replica en SQLite y se rehidrata al primer mensaje tras un reinicio,
# de modo que el creador de un plan continúa exactamente donde se quedó (dentro de la ventana STATE_TTL_SECONDS).
# ------------------------------------------
_PLAN_STATE_DDL = """
CREATE TABLE IF NOT EXISTS conversation_state_store (
    bot_id     INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    payload    TEXT,
    updated_at INTEGER,
    PRIMARY KEY (bot_id, user_id, kind)
)
"""
_PLAN_STATE_KIND = "chan_plan"


async def persist_plan_state(plan_key: tuple) -> None:
    """Replica el estado actual del asistente de planes en SQLite (best-effort; nunca rompe el flujo)."""
    state = CHAN_PLAN_STATES.get(plan_key)
    if state is None:
        return
    bot_id, user_id = plan_key
    payload = json.dumps(state, ensure_ascii=False)

    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_PLAN_STATE_DDL)
            cursor.execute(
                "INSERT OR REPLACE INTO conversation_state_store (bot_id, user_id, kind, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (bot_id, user_id, _PLAN_STATE_KIND, payload, int(time.time()))
            )
            conn.commit()
    try:
        await asyncio.to_thread(_sync)
    except Exception as ex:
        logging.warning(f"⚠️ [Estados] No se pudo persistir el asistente de planes de user={user_id}: {ex}")


async def restore_plan_state(plan_key: tuple) -> bool:
    """Rehidrata el asistente de planes desde SQLite tras un reinicio. Devuelve True si se recuperó un estado válido."""
    if plan_key in CHAN_PLAN_STATES:
        return True
    bot_id, user_id = plan_key

    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_PLAN_STATE_DDL)
            cursor.execute(
                "SELECT payload FROM conversation_state_store WHERE bot_id = ? AND user_id = ? AND kind = ?",
                (bot_id, user_id, _PLAN_STATE_KIND)
            )
            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else None
    try:
        raw = await asyncio.to_thread(_sync)
        if not raw:
            return False
        state = json.loads(raw)
        if not isinstance(state, dict):
            return False
        CHAN_PLAN_STATES[plan_key] = state
        logging.info(f"♻️ [Estados] Asistente de planes rehidratado para user={user_id} (paso '{state.get('step')}').")
        return True
    except Exception as ex:
        logging.warning(f"⚠️ [Estados] No se pudo rehidratar el asistente de planes de user={user_id}: {ex}")
        return False


def forget_plan_state(bot_id: int, user_id: int) -> None:
    """Elimina el asistente de planes de la RAM y de SQLite (fire-and-forget; seguro fuera de un event loop)."""
    CHAN_PLAN_STATES.pop((bot_id, user_id), None)

    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(_PLAN_STATE_DDL)
            cursor.execute(
                "DELETE FROM conversation_state_store WHERE bot_id = ? AND user_id = ? AND kind = ?",
                (bot_id, user_id, _PLAN_STATE_KIND)
            )
            conn.commit()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            loop.run_in_executor(None, _sync)
        else:
            _sync()
    except Exception as ex:
        logging.warning(f"⚠️ [Estados] No se pudo limpiar el asistente de planes de user={user_id}: {ex}")


async def get_active_user_groups(bot: Bot, user_id: int) -> list:
    raw_groups = await get_user_groups(user_id)
    if not raw_groups:
        return []
    if is_super_admin(user_id):
        return raw_groups
    bot_info = await bot.get_me()
    bot_id = bot_info.id

    async def check_ownership(g_id, g_name):
        try:
            bot_member = await bot.get_chat_member(chat_id=g_id, user_id=bot_id)
            if bot_member.status not in ("administrator", "creator"):
                return None
            user_member = await bot.get_chat_member(chat_id=g_id, user_id=user_id)
            # 🛡️ Blindaje estricto: único dueño/creador autorizado
            return (g_id, g_name) if user_member.status == "creator" else None
        except Exception:
            return None

    results = await asyncio.gather(*(check_ownership(g_id, g_name) for g_id, g_name in raw_groups))
    return [res for res in results if res is not None]


async def get_active_user_channels(bot: Bot, user_id: int) -> list:
    candidates: dict = {}
    try:
        for row in (await get_user_channels(user_id)) or []:
            candidates[int(row[0])] = row[1]
    except Exception:
        pass

    for c_id, c_name in await registry_get_channels(user_id):
        candidates.setdefault(c_id, c_name)
    for c_id, c_name in CHANNEL_SYNC_CACHE.get(user_id, {}).items():
        candidates.setdefault(c_id, c_name)

    if not candidates:
        return []

    def _ordered(items) -> list:
        return sorted(items, key=lambda pair: str(pair[1]).lower())

    try:
        bot_id = (await bot.get_me()).id
    except Exception:
        return _ordered(candidates.items())

    async def check_channel(c_id: int, c_name: str):
        try:
            bot_member = await bot.get_chat_member(chat_id=c_id, user_id=bot_id)
            if bot_member.status not in ("administrator", "creator"):
                return None

            # 2) Título fresco (si el canal fue renombrado).
            title = c_name
            try:
                chat_obj = await bot.get_chat(c_id)
                title = chat_obj.title or c_name
            except Exception:
                pass

            # 3) Propiedad del usuario.
            if is_super_admin(user_id):
                return (c_id, title, True)
            try:
                user_member = await bot.get_chat_member(chat_id=c_id, user_id=user_id)
            except Exception as e:
                logging.warning(f"⚠️ [Sync Canales] Fallo transitorio verificando propietario user={user_id} channel={c_id}: {e}. Se conserva.")
                return (c_id, title, False)
            # Permitir tanto creador como administrador en el canal
            return (c_id, title, True) if user_member.status in ("creator", "administrator") else None
        except Exception:
            return None

    results = await asyncio.gather(*(check_channel(c_id, c_name) for c_id, c_name in candidates.items()))
    kept = [res for res in results if res is not None]

    # Autorreparación: los canales confirmados en vivo se re-persisten y se cachean.
    for c_id, title, confirmed in kept:
        CHAT_KIND_CACHE[c_id] = "c"
        if confirmed:
            CHANNEL_SYNC_CACHE.setdefault(user_id, {})[c_id] = title
            await registry_upsert_channel(user_id, c_id, title)

    return _ordered((c_id, title) for c_id, title, _ in kept)


async def is_registered_owner_db(user_id: int, group_id: int) -> bool:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ? LIMIT 1",
                (user_id, group_id)
            )
            if cursor.fetchone():
                return True
            cursor.execute(
                "SELECT 1 FROM bot_clones WHERE user_id = ? AND group_id = ? AND status != 'revoked' LIMIT 1",
                (user_id, group_id)
            )
            return cursor.fetchone() is not None
    try:
        return await asyncio.to_thread(_sync)
    except Exception as ex:
        logging.error(f"❌ [Aduana] Fallo al consultar propiedad en DB para user={user_id} group={group_id}: {ex}")
        return False


async def is_legitimate_owner(bot: Bot, user_id: int, group_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    if await is_registered_owner_db(user_id, group_id):
        return True
    if await registry_has_owner(user_id, group_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        if member.status == "creator":
            return True
    except Exception:
        pass
    return False


async def verify_admin_privileges(callback: CallbackQuery, bot: Bot, group_id: int) -> bool:
    lang = "es" if callback.from_user.language_code and callback.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    if await is_legitimate_owner(bot, callback.from_user.id, group_id):
        return True
    logging.warning(f"⛔ [Aduana] Acceso rechazado — user={callback.from_user.id} intentó operar group={group_id} sin ser propietario.")
    await callback.answer(t["owner_only_alert"], show_alert=True)
    return False


async def verify_admin_privileges_msg(message: Message, bot: Bot, group_id: int) -> bool:
    lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    if await is_legitimate_owner(bot, message.from_user.id, group_id):
        return True
    logging.warning(f"⛔ [Aduana] Acceso rechazado — user={message.from_user.id} intentó operar group={group_id} sin ser propietario.")
    await message.answer(t["owner_only_alert"])
    return False


async def _reply_plan_state_lost(message: Message, lang: str, channel_id=None) -> None:
    """Reply when the persisted channel-plan assistant state is missing or invalid."""
    t = TEXTS.get(lang, TEXTS["es"])
    await message.answer(t["plan_state_lost"], parse_mode="HTML")


async def _finalize_and_preview_channel_plan(
    bot: Bot, chat_id: int, channel_id: int, lang: str, name: str,
    days: int, price: int, promo_text: str, media_id: str = None,
    media_type: str = None, target_link: str = None, recurrence_hours: int = 0
) -> None:
    """Guarda el plan del canal y envía una vista previa donde el enlace VIP es un botón inline."""
    t = TEXTS.get(lang, TEXTS["es"])
    try:
        plan_id = await create_channel_plan(
            channel_id, name, days, price, promo_text, media_id, media_type, target_link
        )
        if recurrence_hours and recurrence_hours > 0:
            await set_channel_plan_broadcast_config(plan_id, channel_id, recurrence_hours)
    except Exception as ex:
        logging.error(f"❌ [Channel Plans] No se pudo crear el plan en {channel_id}: {ex}")
        await bot.send_message(chat_id, t["plan_create_error"], parse_mode="HTML")
        return

    # Mensaje limpio: el texto promocional ya no lleva la URL en bruto
    preview = (
        f"💎 <b>{html.escape(name)}</b>\n\n"
        f"⏳ {days} {'días' if lang == 'es' else 'days'}\n"
        f"⭐ {price} XTR\n\n{promo_text}"
    )
    if recurrence_hours and recurrence_hours > 0:
        preview += f"\n\n⏰ <b>Recurrencia:</b> Cada {recurrence_hours}h"

    # Filas de botones inline
    inline_rows = []
    
    # 🔗 El enlace VIP ahora vive dentro de su propio botón inline
    if target_link:
        btn_label = "🔗 Acceder al Recurso / Canal VIP" if lang == "es" else "🔗 Access VIP Resource / Channel"
        clean_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
        inline_rows.append([InlineKeyboardButton(text=btn_label, url=clean_url)])

    inline_rows.append([
        InlineKeyboardButton(
            text=t["btn_back_channel"],
            callback_data=f"cpanel_{channel_id}_{lang}"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_rows)

    if media_id and media_type == "photo":
        await bot.send_photo(chat_id, media_id, caption=preview, reply_markup=keyboard, parse_mode="HTML")
    elif media_id and media_type == "video":
        await bot.send_video(chat_id, media_id, caption=preview, reply_markup=keyboard, parse_mode="HTML")
    elif media_id and media_type == "animation":
        await bot.send_animation(chat_id, media_id, caption=preview, reply_markup=keyboard, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, preview, reply_markup=keyboard, parse_mode="HTML")

# ==========================================
# 🧭 BLINDAJE DE NAVEGACIÓN CONTEXTUAL (CANAL VS GRUPO)
# ==========================================
async def resolve_chat_kind(bot: Bot, chat_id: int) -> str:
    """
    Determina si el chat_id pertenece a un Canal ('c') o a un Grupo/Supergrupo ('g').
    Blindaje anti-degradación: el tipo de chat no cambia jamás, así que se cachea; si Telegram falla
    (arranque en frío / flood-control) se recurre al registro de canales antes de asumir 'g'. Esto evita que un
    canal sea desviado al panel de grupo (gpanel) por un fallo transitorio.
    """
    cached = CHAT_KIND_CACHE.get(chat_id)
    if cached:
        return cached
    try:
        chat_obj = await bot.get_chat(chat_id)
        kind = "c" if chat_obj.type == "channel" else "g"
        CHAT_KIND_CACHE[chat_id] = kind
        return kind
    except Exception:
        if await registry_has_channel(chat_id):
            return "c"
        return "g"


async def origin_back_button(bot: Bot, chat_id: int, lang: str) -> InlineKeyboardButton:
    """Botón de retorno que respeta el origen real: canal → cpanel (estudio) | grupo → gpanel."""
    t = TEXTS.get(lang, TEXTS["es"])
    if await resolve_chat_kind(bot, chat_id) == "c":
        return InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{chat_id}_{lang}")
    return InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")


def ultra_back_button(chat_type: str, chat_id: int, lang: str) -> InlineKeyboardButton:
    """Retorno de las Herramientas Ultra: canal → cpanel | grupo → menú de herramientas Ultra del grupo."""
    t = TEXTS.get(lang, TEXTS["es"])
    if chat_type == "c":
        return InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{chat_id}_{lang}")
    return InlineKeyboardButton(text=t["btn_back_ultra"], callback_data=f"menu_ultra_{chat_id}_{lang}")


def _cancel_kb(t: dict, callback_data: str) -> InlineKeyboardMarkup:
    """Botón 'Cancelar y volver' para todo prompt conversacional (jamás deja al usuario sin salida)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=callback_data)]
    ])
# ---------- Utilidades en vivo del módulo: alias, avisos temporales, refresco de panel y agradecimiento ----------
_TIPS_ALIAS: dict = {}           # destino (minúsculas) → nombre visible del canal
_TIPS_NOTICE_SECONDS = 6         # vida de los avisos temporales


def _tips_alias(value: str) -> str:
    """Nombre visible del canal destino; si aún no se resolvió, muestra el @usuario/ID."""
    return _TIPS_ALIAS.get(value.lower()) or value


async def _tips_resolve_aliases(bot: Bot, targets: list) -> None:
    """Resuelve en paralelo el título real de los canales que aún no tienen alias (1 consulta por canal)."""
    pending = [v for _, v, _ in targets if v.lower() not in _TIPS_ALIAS]
    if bot is None or not pending:
        return

    async def _one(value: str) -> None:
        ref = int(value) if _TIPS_NUMERIC_ID_RE.match(value) else value
        try:
            chat = await asyncio.wait_for(bot.get_chat(ref), timeout=4)
            if chat.title:
                _TIPS_ALIAS[value.lower()] = chat.title
        except Exception:
            pass

    await asyncio.gather(*(_one(v) for v in pending))


async def _tips_open_prompt(callback: CallbackQuery, states: dict, key: tuple, group_id: int, lang: str, text: str, cancel_kb) -> None:
    """Registra el estado conversacional (con referencia al panel) y envía el prompt con autoborrado."""
    state = {
        "group_id": group_id, "lang": lang,
        "panel_chat_id": callback.message.chat.id, "panel_msg_id": callback.message.message_id,
    }
    states[key] = state
    prompt = await callback.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")
    state["prompt_id"] = prompt.message_id
    fire_and_forget_auto_delete([prompt], delay=60)


async def _tips_say(msg: Message, text: str, reply_markup=None) -> Message:
    """Edita un mensaje de estado; si Telegram lo rechaza (idéntico/borrado) devuelve el original sin romper el flujo."""
    try:
        result = await msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return result if isinstance(result, Message) else msg
    except TelegramBadRequest:
        return msg


async def _tips_refresh_panel(bot: Bot, state: dict, group_id: int, lang: str, viewer_id: int) -> None:
    """Redibuja el panel de propinas original en tiempo real; si ya no es editable, lo reenvía."""
    try:
        text, keyboard = await build_tips_panel(group_id, lang, await resolve_chat_kind(bot, group_id), viewer_id, bot=bot)
        chat_id, msg_id = state.get("panel_chat_id"), state.get("panel_msg_id")
        if chat_id and msg_id:
            try:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=keyboard, parse_mode="HTML")
                return
            except TelegramBadRequest as ex:
                if "message is not modified" in str(ex).lower():
                    return
        await bot.send_message(chat_id=viewer_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as ex:
        logging.error(f"❌ [Tips] No se pudo refrescar el panel de group={group_id}: {ex}")


async def _tips_finish(bot: Bot, message: Message, state: dict, group_id: int, lang: str, notice_text: str, status_msg: Message = None) -> None:
    """Cierre de un flujo exitoso: limpia el prompt, refresca el panel en vivo y deja un aviso temporal."""
    try:
        await bot.delete_message(message.chat.id, state["prompt_id"])
    except Exception:
        pass
    await _tips_refresh_panel(bot, state, group_id, lang, message.from_user.id)
    notice = await _tips_say(status_msg, notice_text) if status_msg else await message.answer(notice_text, parse_mode="HTML")
    fire_and_forget_auto_delete([message, notice], delay=_TIPS_NOTICE_SECONDS)

async def get_channel_perm_warning(bot: Bot, channel_id: int, lang: str) -> str:
    """Auditoría dinámica de permisos del bot en el canal; devuelve un aviso HTML o '' si todo está en orden."""
    t = TEXTS.get(lang, TEXTS["es"])
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=channel_id, user_id=me.id)
    except Exception:
        return ""
    if member.status == "creator":
        return ""
    if member.status != "administrator":
        return t["perm_bot_not_admin"]
    required = (
        ("can_post_messages", "Publicar mensajes", "Post messages"),
        ("can_edit_messages", "Editar mensajes", "Edit messages"),
        ("can_delete_messages", "Eliminar mensajes", "Delete messages"),
        ("can_manage_video_chats", "Gestionar videochats", "Manage video chats"),
        ("can_invite_users", "Invitar usuarios", "Invite users"),
    )
    missing = [tr(lang, es, en) for attr, es, en in required if getattr(member, attr, None) is False]
    if not missing:
        return ""
    return t["perm_missing"].format(missing=", ".join(missing))


def build_channel_request_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Selector nativo de Telegram: lista los canales cuyo propietario es el usuario y donde el bot es miembro."""
    t = TEXTS.get(lang, TEXTS["es"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text=t["btn_pick_channel"],
                request_chat=KeyboardButtonRequestChat(
                    request_id=CHANNEL_SYNC_REQUEST_ID,
                    chat_is_channel=True,
                    chat_is_created=True,
                    bot_is_member=True
                )
            )],
            [KeyboardButton(text=t["btn_sync_cancel"])]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=t["btn_sync_placeholder"]
    )


def get_main_keyboard(bot_username: str, lang: str, is_clone: bool = False):
    """Teclado principal con bifurcación dual independiente para Grupos y Canales."""
    t = TEXTS.get(lang, TEXTS["es"])
    add_group_url = f"https://t.me/{bot_username}?startgroup=true&admin=restrict_members+ban_users+delete_messages+pin_messages+manage_video_chats+promote_members"
    add_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages+delete_messages+manage_video_chats+invite_users"

    rows = [
        [
            InlineKeyboardButton(text=t["btn_add_group"], url=add_group_url),
            InlineKeyboardButton(text=t["btn_add_channel"], url=add_channel_url)
        ],
        [
            InlineKeyboardButton(text=t["btn_settings"], callback_data=f"menu_settings_{lang}"),
            InlineKeyboardButton(text=t["btn_chsettings"], callback_data=f"menu_chsettings_{lang}")
        ]
    ]
    if not is_clone:
        rows.append([InlineKeyboardButton(text=t["btn_saas"], web_app=WebAppInfo(url=WEBAPP_URL))])
    rows.append([
        InlineKeyboardButton(text=t["btn_id"], callback_data=f"menu_id_{lang}"),
        InlineKeyboardButton(text="🇪🇸 ES / 🇬🇧 EN", callback_data=f"lang_{'es' if lang == 'en' else 'en'}")
    ])
    rows.append([
        InlineKeyboardButton(text=t["btn_support"], callback_data=f"menu_support_{lang}"),
        InlineKeyboardButton(text=t["btn_info"], callback_data=f"menu_info_{lang}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_welcome_video(bot: Bot, chat_id: int, caption: str, keyboard) -> bool:
    """Envía la bienvenida como video + caption + botones. Devuelve False si no fue posible (el llamador usa texto)."""
    if len(caption) > 1024:  # límite de Telegram para captions
        logging.warning("⚠️ [Welcome] Caption > 1024 caracteres; se envía la bienvenida solo como texto.")
        return False
    cached_id = _WELCOME_VIDEO_FILE_IDS.get(bot.id)
    if not cached_id and not os.path.isfile(WELCOME_VIDEO_PATH):
        logging.warning(f"⚠️ [Welcome] Video no encontrado en '{WELCOME_VIDEO_PATH}'; se envía solo texto.")
        return False
    try:
        msg = await bot.send_video(
            chat_id=chat_id,
            video=cached_id or FSInputFile(WELCOME_VIDEO_PATH),
            caption=caption,
            reply_markup=keyboard,
            parse_mode="HTML",
            supports_streaming=True,
            width=1280, height=720, duration=10,
        )
        if not cached_id and getattr(msg, "video", None):
            _WELCOME_VIDEO_FILE_IDS[bot.id] = msg.video.file_id
        return True
    except Exception as ex:
        _WELCOME_VIDEO_FILE_IDS.pop(bot.id, None)  # file_id inválido o fallo de subida: se reintenta desde disco
        logging.warning(f"⚠️ [Welcome] Falló el envío del video, se usa texto: {ex}")
        return False


async def send_official_welcome(bot: Bot, chat_id: int, user, bot_username: str = None) -> None:
    if not bot_username:
        bot_username = (await bot.get_me()).username or "BunkerBot"
    lang = "es" if user and user.language_code and user.language_code.startswith("es") else "en"
    t = TEXTS.get(lang, TEXTS["es"])
    name = html.escape(user.full_name) if user else "Comandante"
    text = t["welcome"].format(name=name)
    keyboard = get_main_keyboard(bot_username, lang, is_clone=is_clone_bot(bot))
    # 🖼️ Prioridad: tarjeta gráfica (assets) -> video de bienvenida -> texto
    if await render_card(bot, chat_id, "welcome", lang, keyboard):
        return
    if await _send_welcome_video(bot, chat_id, text, keyboard):
        return
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


def get_simple_back_keyboard(lang: str, target: str = "main"):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_{target}_{lang}")]])


def get_info_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_how_works"], callback_data=f"menu_infohow_{lang}")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])


def get_support_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_contact_support"], url="https://t.me/m/RGx4ohGTMTk5")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])


def get_groups_keyboard(groups: list, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    if not groups:
        no_groups_text = "⚠️ No hay comunidades activas vinculadas" if lang == "es" else "⚠️ No active communities linked"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=no_groups_text, callback_data="noop")],
            [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
        ])
    kb = [[InlineKeyboardButton(text=g_name, callback_data=f"gpanel_{g_id}_{lang}")] for g_id, g_name in groups]
    kb.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_channels_keyboard(channels: list, lang: str):
    """Selector de canales vinculados + acceso permanente a la Sincronización Activa."""
    t = TEXTS.get(lang, TEXTS["es"])
    rows = []
    if not channels:
        no_ch_text = "⚠️ No hay canales activos vinculados" if lang == "es" else "⚠️ No active channels linked"
        rows.append([InlineKeyboardButton(text=no_ch_text, callback_data="noop")])
    else:
        for c_id, c_name in channels:
            rows.append([InlineKeyboardButton(text=f"📢 {c_name}", callback_data=f"cpanel_{c_id}_{lang}")])
    rows.append([InlineKeyboardButton(text=t["btn_sync_channels"], callback_data=f"menu_chsync_{lang}")])
    rows.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_channel_panel_keyboard(channel_id: int, lang: str):
    """Consola especializada de estudio para canales (Lives, Membresías y Payload)."""
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 ULTRA PRO", callback_data=f"pay_ultra_{channel_id}_{lang}")],
        [
            InlineKeyboardButton(text="💎 " + ("Planes de Membresía" if lang == "es" else "Membership Plans"), callback_data=f"chplans_menu_{channel_id}_{lang}"),
            InlineKeyboardButton(text="🎙️ " + ("Moderación de Live" if lang == "es" else "Live Moderation"), callback_data=f"menu_ultra_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⭐ " + ("Propinas Stars" if lang == "es" else "Stars Tips"), callback_data=f"tips_menu_{channel_id}_{lang}"),
            InlineKeyboardButton(text="💎 " + ("Payload Multimedia" if lang == "es" else "Media Payload"), callback_data=f"payload_menu_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="🧬 " + ("Clon & Centinela" if lang == "es" else "Clone & Sentinel"), callback_data=f"gset_clone_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=f"🌐 {'English' if lang == 'es' else 'Español'}", callback_data=f"langcpanel_{channel_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")
        ]
    ])


def _plan_step_keyboard(t: dict, channel_id: int, lang: str) -> InlineKeyboardMarkup:
    """Return the cancel/return keyboard displayed during plan creation."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t["btn_cancel_ret"],
            callback_data=f"chplans_menu_{channel_id}_{lang}"
        )]
    ])


def get_group_panel_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ PRO", callback_data=f"pay_pro_{group_id}_{lang}"), InlineKeyboardButton(text="💎 ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_mod"], callback_data=f"menu_mod_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_eco"], callback_data=f"menu_eco_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_antispam"], callback_data=f"gset_antispam_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_antiflood"], callback_data=f"gset_antiflood_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_captcha"], callback_data=f"gset_captcha_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_locks"], callback_data=f"gset_locks_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_warns"], callback_data=f"gset_warns_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_delmsgs"], callback_data=f"gset_delmsgs_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_clone"], callback_data=f"gset_clone_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_ultra_tools"], callback_data=f"menu_ultra_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=f"🌐 {'English' if lang == 'es' else 'Español'}", callback_data=f"langpanel_{group_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_settings"], callback_data=f"menu_settings_{lang}")
        ]
    ])


def get_ultra_tools_keyboard(group_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_ai_sentinel"], callback_data=f"ai_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚙️ " + ("Configuración del Centinela" if lang == "es" else "Sentinel Settings"), callback_data=f"sentinelcfg_menu_{group_id}_{lang}")], # <--- BOTÓN AÑADIDO
        [InlineKeyboardButton(text="🚨 " + ("Botón de Pánico" if lang == "es" else "Panic Button"), callback_data=f"panic_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎥 " + ("Escudo Antinota" if lang == "es" else "Screen-Share Shield"), callback_data=f"shield_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎙️ " + ("Modo Podcast" if lang == "es" else "Podcast Mode"), callback_data=f"podcast_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🌟 " + ("Gestión de Speakers" if lang == "es" else "Speakers Management"), callback_data=f"speakers_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="💎 " + ("Payload Multimedia" if lang == "es" else "Multimedia Payload"), callback_data=f"payload_menu_{group_id}_{lang}")],
        [back_btn]
    ])


def build_ultra_lock_view(group_id: int, lang: str, feature_title: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    lock_text = f"{feature_title}\n\n{t['ultra_lock_generic']}"
    # 🧭 Blindaje contextual: canal → cpanel | grupo → gpanel.
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [back_btn]
    ])
    return lock_text, keyboard


def get_panic_keyboard(group_id: int, lang: str, status: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    action_btn = (
        InlineKeyboardButton(text=t["btn_panic_deactivate"], callback_data=f"panic_deactivate_{group_id}_{lang}")
        if status == 1 else
        InlineKeyboardButton(text=t["btn_panic_activate"], callback_data=f"panic_confirm_{group_id}_{lang}")
    )
    back_btn = ultra_back_button(chat_type, group_id, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [action_btn],
        [back_btn]
    ])
def get_night_keyboard(group_id: int, lang: str, status: int):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_btn = (
        InlineKeyboardButton(text=t["btn_night_off"], callback_data=f"night_toggle_0_{group_id}_{lang}")
        if status == 1 else
        InlineKeyboardButton(text=t["btn_night_on"], callback_data=f"night_toggle_1_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle_btn],
        [InlineKeyboardButton(text=t["btn_night_mod"], callback_data=f"night_prompt_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
    ])


def get_panic_confirm_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_panic_confirm"], callback_data=f"panic_activate_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"panic_menu_{group_id}_{lang}")]
    ])


def get_shield_keyboard(group_id: int, lang: str, status: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = ultra_back_button(chat_type, group_id, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_shield_1"], callback_data=f"shield_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_shield_0"], callback_data=f"shield_toggle_0_{group_id}_{lang}")
        ],
        [back_btn]
    ])


def get_podcast_keyboard(group_id: int, lang: str, status: int, duck_level: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = ultra_back_button(chat_type, group_id, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_podcast_1"], callback_data=f"podcast_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_podcast_0"], callback_data=f"podcast_toggle_0_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="10%", callback_data=f"podcast_duckval_10_{group_id}_{lang}"),
            InlineKeyboardButton(text="20%", callback_data=f"podcast_duckval_20_{group_id}_{lang}"),
            InlineKeyboardButton(text="30%", callback_data=f"podcast_duckval_30_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_duck_level"].format(duck_level=duck_level), callback_data=f"podcast_duckset_{group_id}_{lang}")],
        [back_btn]
    ])


def get_speakers_keyboard(group_id: int, lang: str, status: int, price: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = ultra_back_button(chat_type, group_id, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_speakers_1"], callback_data=f"speakers_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_speakers_0"], callback_data=f"speakers_toggle_0_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⭐ 20", callback_data=f"speakers_priceval_20_{group_id}_{lang}"),
            InlineKeyboardButton(text="⭐ 30", callback_data=f"speakers_priceval_30_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_speakers_price"].format(price=price), callback_data=f"speakers_priceset_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_speakers_clear"], callback_data=f"speakers_clear_{group_id}_{lang}")],
        [back_btn]
    ])


def get_sentinel_payload_keyboard(group_id: int, lang: str, cfg: dict, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    st = cfg.get("enabled", 0)
    has_text = "🟢" if cfg.get("text") else "🔴"
    has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
    autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get("auto_delete_after") else ("Desactivado" if lang == "es" else "Off")
    st_label = f"💎 {'Payload: 🟢' if st == 1 else 'Payload: 🔴'}"
    back_btn = ultra_back_button(chat_type, group_id, lang)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_label, callback_data=f"payload_toggle_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'✍️ Texto Personalizado' if lang == 'es' else '✍️ Custom Text'} {has_text}", callback_data=f"payload_text_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'🖼️ Multimedia (Foto/Anim)' if lang == 'es' else '🖼️ Media (Photo/Anim)'} {has_media}", callback_data=f"payload_media_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'⏱️ Auto-Borrado' if lang == 'es' else '⏱️ Auto-Delete'}: {autodel}", callback_data=f"payload_autodel_{group_id}_{lang}")],
        [back_btn]
    ])


# ==========================================================================================
# ⭐ MÓDULO DE PROPINAS Y DONACIONES (TELEGRAM STARS) — NÚCLEO
# ==========================================================================================
TIPS_TARGET_LIMITS = {"free": 1, "pro": 3, "ultra_pro": 10}   # Canales destino por licencia
TIPS_TEXT_MAX_LEN = 4000
TIPS_MAX_AMOUNT = 999999         # Margen bajo el límite de 1024 caracteres de un caption con multimedia
TIPS_BROADCAST_COOLDOWN = 30     # Segundos de enfriamiento por grupo (anti doble-clic / anti-flood)
_TIPS_LAST_BROADCAST: dict = {}
_TIPS_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_TIPS_NUMERIC_ID_RE = re.compile(r"^-?[0-9]{5,20}$")
_TIPS_MEDIA_SENDERS = {
    "photo": ("send_photo", "photo"),
    "video": ("send_video", "video"),
    "animation": ("send_animation", "animation"),
}


# ---------- Utilidades de configuración (lectura tolerante al esquema de la BD) ----------
def _tips_tier(tier: str) -> str:
    tier = (tier or "free").lower()
    return "ultra_pro" if tier == "ultra" else (tier if tier in TIPS_TARGET_LIMITS else "free")


def _tips_limit(tier: str) -> int:
    return TIPS_TARGET_LIMITS[_tips_tier(tier)]


def _tips_cfg(cfg: dict, *keys, default=None):
    """Devuelve el primer valor no vacío entre varias claves posibles (0 es un valor válido)."""
    for key in keys:
        value = (cfg or {}).get(key)
        if value is not None and value != "":
            return value
    return default


def _tips_is_on(cfg: dict) -> bool:
    return str(_tips_cfg(cfg, "enabled", "tips_enabled", default=0)).strip().lower() in {"1", "true", "yes", "on"}


def _tips_amount(cfg: dict) -> int:
    try:
        return max(1, int(_tips_cfg(cfg, "amount", "tips_amount", default=10)))
    except (TypeError, ValueError):
        return 10


def _tips_normalize_targets(raw) -> list:
    """Normaliza la salida de get_group_tip_targets → [(id:int, valor:str, activo:bool)]."""
    out = []
    for row in raw or []:
        try:
            if isinstance(row, dict):
                t_id = row.get("id")
                t_val = next((row[k] for k in ("target_value", "target", "target_username", "username", "chat_id") if row.get(k)), None)
                t_act = next((row[k] for k in ("is_active", "active", "enabled") if k in row), 1)
            else:
                t_id, t_val = row[0], row[1]
                t_act = row[2] if len(row) > 2 else 1
            if t_id is None or t_val in (None, ""):
                continue
            active = str(t_act).strip().lower() in {"1", "true", "yes", "active", "enabled"}
            out.append((int(t_id), str(t_val), active))
        except Exception:
            continue
    return out


async def _tips_get_targets(group_id: int) -> list:
    return _tips_normalize_targets(await get_group_tip_targets(group_id))


def _tips_back_kb(t: dict, group_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"tips_menu_{group_id}_{lang}")]
    ])


# ---------- Panel principal (texto + teclado) ----------
def build_tips_panel_text(lang: str, cfg: dict, tier: str, total_stars: int, targets: list) -> str:
    st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if _tips_is_on(cfg) else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
    active_n = sum(1 for _, _, active in targets if active)
    return (
        f"⭐ <b>{tr(lang, 'Propinas y Donaciones con Telegram Stars', 'Telegram Stars Tips & Donations')}</b>\n\n"
        f"• <b>{tr(lang, 'Estado', 'Status')}:</b> {st_badge}\n"
        f"• <b>{tr(lang, 'Monto Sugerido', 'Suggested Amount')}:</b> <code>{_tips_amount(cfg)} Stars</code>\n"
        f"• <b>{tr(lang, 'Recaudación Total', 'Total Raised')}:</b> <code>{total_stars} ⭐</code>\n"
        f"• <b>{tr(lang, 'Canales Destino', 'Target Channels')}:</b> <code>{len(targets)}/{_tips_limit(tier)}</code> "
        f"({active_n} {tr(lang, 'activos', 'active')})\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )


async def get_tips_keyboard(group_id: int, lang: str, cfg: dict, chat_type: str = "g", viewer_id: int = 0, targets: list = None):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = _tips_tier(await get_effective_group_tier(group_id, viewer_id))
    limit = _tips_limit(tier)
    if targets is None:
        targets = await _tips_get_targets(group_id)

    on = _tips_is_on(cfg)
    has_text = bool(_tips_cfg(cfg, "tips_custom_text", "custom_text"))
    has_media = bool(_tips_cfg(cfg, "tips_media_id", "media_id"))
    active_n = sum(1 for _, _, active in targets if active)

    rows = [
        # 1. Toggle global del módulo
        [InlineKeyboardButton(text=f"⭐ {tr(lang, 'Propinas', 'Tips')}: {'🟢' if on else '🔴'}", callback_data=f"tips_toggle_{group_id}_{lang}")],
        # 2. Monto sugerido
        [InlineKeyboardButton(text=f"💰 {tr(lang, 'Monto Sugerido', 'Suggested')}: {_tips_amount(cfg)} Stars", callback_data=f"tips_setamount_{group_id}_{lang}")],
        # 3. Telemetría y Botón de Difusión Selectiva
        [
            InlineKeyboardButton(text=f"📊 {tr(lang, 'Telemetría', 'Telemetry')}", callback_data=f"tips_telemetry_{group_id}_{lang}"),
            InlineKeyboardButton(text=f"📢 {tr(lang, 'Difundir Panel', 'Broadcast Panel')}", callback_data=f"tips_share_{group_id}_{lang}")
        ],
    ]

    # 4. Rangos: texto personalizado (PRO+) y multimedia (ULTRA PRO). Bloqueado → muro de pago hacia el plan que lo habilita.
    if tier in ("pro", "ultra_pro"):
        rows.append([InlineKeyboardButton(text=f"✍️ {tr(lang, 'Editar Texto', 'Edit Text')} (PRO) {'✅' if has_text else '⬜'}", callback_data=f"tips_settext_{group_id}_{lang}")])
    else:
        rows.append([InlineKeyboardButton(text=f"🔒 {tr(lang, 'Texto Personalizado', 'Custom Text')} (PRO)", callback_data=f"pay_pro_{group_id}_{lang}")])

    if tier == "ultra_pro":
        rows.append([InlineKeyboardButton(text=f"🖼️ {tr(lang, 'Adjuntar Multimedia', 'Attach Media')} (ULTRA PRO) {'✅' if has_media else '⬜'}", callback_data=f"tips_setmedia_{group_id}_{lang}")])
    else:
        rows.append([InlineKeyboardButton(text=f"🔒 {tr(lang, 'Adjuntar Multimedia', 'Attach Media')} (ULTRA PRO)", callback_data=f"pay_ultra_{group_id}_{lang}")])

    # 5. Motor de difusión
    if targets:
        rows.append([InlineKeyboardButton(text=f"📢 {tr(lang, 'Enviar Publicación', 'Broadcast Tips')} ({active_n})", callback_data=f"tips_broadcast_{group_id}_{lang}")])

    # 6. Botonera en cascada: [alias del canal] [🟢/🔴 tips_toggletarget] [🗑️ tips_deltarget]
    for t_id, t_val, t_active in targets:
        rows.append([
            InlineKeyboardButton(text=f"📢 {_tips_alias(t_val)[:24]}", callback_data="noop"),
            InlineKeyboardButton(text="🟢" if t_active else "🔴", callback_data=f"tips_toggletarget_{t_id}_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"tips_deltarget_{t_id}_{group_id}_{lang}"),
        ])

    # 7. Añadir canal destino (o aviso de límite de licencia)
    if len(targets) < limit:
        rows.append([InlineKeyboardButton(text=f"➕ {tr(lang, 'Añadir Canal Destino', 'Add Target')} ({len(targets)}/{limit})", callback_data=f"tips_addtarget_{group_id}_{lang}")],)
    else:
        rows.append([InlineKeyboardButton(text=f"🔒 {tr(lang, 'Límite de canales alcanzado', 'Channel limit reached')} ({len(targets)}/{limit})", callback_data=f"tips_addtarget_{group_id}_{lang}")],)

    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")
    )
    rows.append([back_btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_tips_panel(group_id: int, lang: str, chat_kind: str, viewer_id: int, bot: Bot = None):
    cfg = await get_tips_config(group_id) or {}
    tier = _tips_tier(await get_effective_group_tier(group_id, viewer_id))
    targets = await _tips_get_targets(group_id)
    await _tips_resolve_aliases(bot, targets)   # nombres reales de los canales (cacheados)
    total_stars = await get_group_total_tips(group_id)
    text = build_tips_panel_text(lang, cfg, tier, total_stars, targets)
    keyboard = await get_tips_keyboard(group_id, lang, cfg, chat_type=chat_kind, viewer_id=viewer_id, targets=targets)
    return text, keyboard


# ---------- Alta de canal destino: parseo + validación ----------
def parse_tip_target_input(raw: str):
    """Acepta @usuario, t.me/usuario, https://t.me/usuario o ID numérico. Devuelve '@usuario' | 'ID' | None."""
    value = re.sub(r"^(https?://)?(www\.)?t\.me/", "", (raw or "").strip(), flags=re.IGNORECASE)
    value = value.split("?")[0].strip("/ ").split("/")[0].lstrip("@")
    if value.lower() == "joinchat":          # enlace de invitación, no es un canal resoluble
        return None
    if _TIPS_NUMERIC_ID_RE.match(value):
        return value
    if _TIPS_USERNAME_RE.match(value):
        return "@" + value
    return None


async def validate_tip_target(bot: Bot, user_id: int, ref: str):
    """Valida el canal destino sin bloquearse por las restricciones de Telegram en canales."""
    try:
        if not ref:
            return None, "invalid"

        chat_ref = int(ref) if ref.lstrip("-").isdigit() else ref
        try:
            chat = await bot.get_chat(chat_ref)
        except TelegramBadRequest:
            return None, "not_found"
        except TelegramForbiddenError:
            return None, "chat"

        if not chat or chat.type not in ("channel", "supergroup", "group"):
            return None, "bad_type"

        # 1. Verificar que el bot sea administrador con permiso para publicar
        try:
            me = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
        except Exception:
            return None, "bot_not_admin"

        if chat.type == "channel":
            if me.status not in ("administrator", "creator"):
                return None, "bot_not_admin"
            if getattr(me, "can_post_messages", None) is False:
                return None, "bot_cannot_post"

        # 2. Nota: En canales, get_chat_member(chat.id, user_id) falla porque Telegram oculta los suscriptores.
        # Si es un supergrupo/grupo sí validamos al usuario; si es canal, confiamos en la gestión privada del dueño.
        if chat.type != "channel" and not is_super_admin(user_id):
            try:
                requester = await bot.get_chat_member(chat.id, user_id)
                if requester.status not in ("administrator", "creator"):
                    return None, "not_admin"
            except Exception:
                return None, "not_admin"

        value = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
        return {"value": value, "title": chat.title or value, "chat_id": chat.id}, None

    except Exception as ex:
        logging.error("❌ [Tips] validate_tip_target failed for %s: %s", ref, ex)
        return None, "invalid"


def tips_target_error_text(lang: str, err: str, **fmt) -> str:
    msgs = {
        "invalid": ("⚠️ Formato no válido. Envía <code>@usuario</code>, un enlace <code>t.me/usuario</code> o el ID numérico (ej. <code>-1001234567890</code>).",
                    "⚠️ Invalid format. Send <code>@username</code>, a <code>t.me/username</code> link or the numeric ID (e.g. <code>-1001234567890</code>)."),
        "not_found": ("⚠️ No encontré ese canal. Verifica el @usuario/ID y que el bot esté agregado.",
                      "⚠️ I couldn't find that channel. Check the @username/ID and that the bot was added."),
        "bad_type": ("⚠️ El destino debe ser un canal o supergrupo, no un chat privado.",
                     "⚠️ The target must be a channel or supergroup, not a private chat."),
        "bot_not_admin": ("⚠️ El bot debe ser <b>administrador</b> del canal destino para poder publicar.",
                          "⚠️ The bot must be an <b>administrator</b> of the target channel to publish."),
        "bot_cannot_post": ("⚠️ El bot es admin, pero no tiene permiso para <b>publicar mensajes</b> en ese canal.",
                            "⚠️ The bot is an admin but lacks the <b>post messages</b> permission in that channel."),
        "not_admin": ("⚠️ Solo un administrador del canal destino puede vincularlo.",
                      "⚠️ Only an administrator of the target channel can link it."),
        "duplicate": ("⚠️ Ese canal ya está vinculado.", "⚠️ That channel is already linked."),
        "limit": ("⚠️ Límite de canales destino alcanzado para tu nivel ({n}/{limit}).",
                  "⚠️ Target channel limit reached for your tier ({n}/{limit})."),
        "save_failed": ("⚠️ No pude registrar el canal. Inténtalo de nuevo.", "⚠️ I couldn't save the channel. Please try again."),
    }
    es, en = msgs.get(err, msgs["save_failed"])
    return tr(lang, es, en).format(**fmt) + PERIMETER_SIGNATURE


# ---------- Motor de difusión (Broadcast) ----------
def build_tips_publication(tier: str, cfg: dict, lang: str):
    """
    Compila (texto, media_id, media_type) según la licencia:
    Free = mensaje por defecto · Pro = texto editable · Ultra Pro = texto + multimedia.
    """
    tier = _tips_tier(tier)
    amount = _tips_amount(cfg)
    custom_text = _tips_cfg(cfg, "tips_custom_text", "custom_text")

    if tier in ("pro", "ultra_pro") and custom_text:
        text = str(custom_text).replace("{amount}", str(amount))
    else:
        text = (
            f"⭐ <b>{tr(lang, '¡Apoya a la comunidad con Telegram Stars!', 'Support the community with Telegram Stars!')}</b>\n\n"
            + tr(lang,
                 f"Puedes enviar aportes voluntarios sugeridos de <code>{amount} Stars</code> para potenciar nuestras transmisiones y desarrollo.",
                 f"You can send voluntary contributions of <code>{amount} Stars</code> to power our broadcasts and development.")
            + PERIMETER_SIGNATURE
        )

    media_id = media_type = None
    if tier == "ultra_pro":
        media_id = _tips_cfg(cfg, "tips_media_id", "media_id")
        media_type = _tips_cfg(cfg, "tips_media_type", "media_type")
        if media_type not in _TIPS_MEDIA_SENDERS:
            media_id = media_type = None
    return text, media_id, media_type


async def _tips_send_one(bot: Bot, chat, text: str, media_id, media_type, reply_markup=None) -> None:
    """Envía una publicación; si la multimedia falla (p. ej. file_id de otro bot), degrada a solo texto."""
    sender = _TIPS_MEDIA_SENDERS.get(media_type)
    if media_id and sender:
        method, arg = sender
        try:
            await getattr(bot, method)(chat_id=chat, caption=text, reply_markup=reply_markup, parse_mode="HTML", **{arg: media_id})
            return
        except TelegramBadRequest as ex:
            logging.warning(f"⚠️ [Tips Broadcast] Multimedia rechazada en {chat}, se envía solo texto: {ex}")
    await bot.send_message(chat_id=chat, text=text, reply_markup=reply_markup, parse_mode="HTML")


async def dispatch_tips_broadcast(bot: Bot, tier: str, cfg: dict, lang: str, active_targets: list, group_id: int = 0):
    """Despacha la publicación a los canales activos con botón inline de Stars. Devuelve (enviados:int, fallidos:list[str])."""
    text, media_id, media_type = build_tips_publication(tier, cfg, lang)
    
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "BunkerBot"
    
    # 🌟 Construcción del botón inline interactivo para enviar la propina con Stars
    tip_btn_label = tr(lang, "⭐ Enviar Propina en Stars", "⭐ Send Stars Tip")
    tip_url = f"https://t.me/{bot_username}?start=tip_{group_id}" if group_id else f"https://t.me/{bot_username}?start=true"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tip_btn_label, url=tip_url)]
    ])

    sent, failed = 0, []
    for value in active_targets:
        chat = int(value) if _TIPS_NUMERIC_ID_RE.match(value) else "@" + value.lstrip("@")
        for attempt in (1, 2):
            try:
                await _tips_send_one(bot, chat, text, media_id, media_type, reply_markup=keyboard)
                sent += 1
                break
            except TelegramRetryAfter as ex:
                if attempt == 2:
                    failed.append(value)
                    break
                await asyncio.sleep(min(ex.retry_after, 30) + 1)
            except Exception as ex:
                logging.warning(f"⚠️ [Tips Broadcast] No se pudo enviar al canal {value}: {ex}")
                failed.append(value)
                break
        await asyncio.sleep(0.05)
    return sent, failed

def get_payment_keyboard(group_id: int, lang: str, tier_level: str = "pro", chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    stars_price = "300 XTR" if tier_level == "pro" else "600 XTR"
    stars_label = f"⭐ Pagar con Stars ({stars_price})" if lang == "es" else f"⭐ Pay with Stars ({stars_price})"

    keyboard_rows = [
        [InlineKeyboardButton(text=stars_label, callback_data=f"inv_{tier_level}_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="💳 PayPal", url="https://paypal.me/Felipecosmic"),
            InlineKeyboardButton(text="🟡 Binance Pay", url="https://app.binance.com/uni-qr/request-to-pay?billOrderId=452404659499556864&billType=request_a_payment")
        ]
    ]

    if tier_level == "ultra":
        btn_text = "🧬 Configurar Clon & Centinela Propio" if lang == "es" else "🧬 Setup Own Clone & Sentinel"
        keyboard_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"gset_clone_{group_id}_{lang}")])

    # 🧭 Blindaje contextual: si la compra se originó desde un Canal, el regreso es al cpanel,
    # nunca al gpanel general de grupos.
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    keyboard_rows.append([back_btn])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


async def get_captcha_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_captcha_config(group_id)
    st = cfg["status"]
    st_btn = f"🛡️ Captcha: {'🟢' if st == 1 else '🔴'}"
    st_cb = f"togcap_off_{group_id}_{lang}" if st == 1 else f"togcap_on_{group_id}_{lang}"

    mode_st = cfg["mode"]
    mode_btn = f"🔤 Alphanumeric: {'🟢' if mode_st == 1 else '🔴'}" if lang == "en" else f"🔤 Alfanumérico: {'🟢' if mode_st == 1 else '🔴'}"
    mode_cb = f"togmode_off_{group_id}_{lang}" if mode_st == 1 else f"togmode_on_{group_id}_{lang}"

    time_label = f"⏱️ Time: {cfg['time']}s" if lang == "en" else f"⏱️ Tiempo: {cfg['time']}s"
    action_label = f"⚖️ Punishment: {cfg['action'].upper()}" if lang == "en" else f"⚖️ Castigo: {cfg['action'].upper()}"
    srv_label = f"🗑️ Service Msg: {'🟢' if cfg['service_del'] == 1 else '🔴'}" if lang == "en" else f"🗑️ Serv. Msg: {'🟢' if cfg['service_del'] == 1 else '🔴'}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_btn, callback_data=st_cb)],
        [InlineKeyboardButton(text=mode_btn, callback_data=mode_cb)],
        [
            InlineKeyboardButton(text=time_label, callback_data=f"cap_set_time_{group_id}_{lang}"),
            InlineKeyboardButton(text=action_label, callback_data=f"cap_set_action_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="✍️ Custom Text" if lang == "en" else "✍️ Mensaje", callback_data=f"cap_set_text_{group_id}_{lang}"),
            InlineKeyboardButton(text=srv_label, callback_data=f"cap_set_srvdel_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


def get_captcha_time_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱️ 30s", callback_data=f"capval_time_30_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️ 1m", callback_data=f"capval_time_60_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⏱️ 3m", callback_data=f"capval_time_180_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️ 5m", callback_data=f"capval_time_300_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver a Captcha"), callback_data=f"gset_captcha_{group_id}_{lang}")]
    ])


async def get_captcha_action_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_captcha_config(group_id)
    current_action = cfg["action"]
    kick_status = "🟢" if current_action == "kick" else "🔴"
    mute_status = "🟢" if current_action == "mute" else "🔴"
    kick_text = f"👢 Kick {kick_status}" if lang == "en" else f"👢 Expulsar {kick_status}"
    mute_text = f"🔇 Mute {mute_status}" if lang == "en" else f"🔇 Silenciar {mute_status}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=kick_text, callback_data=f"capval_action_kick_{group_id}_{lang}"),
            InlineKeyboardButton(text=mute_text, callback_data=f"capval_action_mute_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver a Captcha"), callback_data=f"gset_captcha_{group_id}_{lang}")]
    ])


async def get_antispam_text(group_id: int, lang: str) -> str:
    t = TEXTS.get(lang, TEXTS["es"])
    st_tg = "🟢" if await get_antispam_filter(group_id, "tg_links") == 1 else "🔴"
    st_fwd = "🟢" if await get_antispam_filter(group_id, "forwards") == 1 else "🔴"
    st_q = "🟢" if await get_antispam_filter(group_id, "quotes") == 1 else "🔴"
    st_web = "🟢" if await get_antispam_filter(group_id, "web_links") == 1 else "🔴"
    del_st = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    return t["antispam_main_title"].format(st_tg=st_tg, st_fwd=st_fwd, st_q=st_q, st_web=st_web, st_del=del_st)


async def get_antispam_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    st_tg = "🟢" if await get_antispam_filter(group_id, "tg_links") == 1 else "🔴"
    st_fwd = "🟢" if await get_antispam_filter(group_id, "forwards") == 1 else "🔴"
    st_q = "🟢" if await get_antispam_filter(group_id, "quotes") == 1 else "🔴"
    st_web = "🟢" if await get_antispam_filter(group_id, "web_links") == 1 else "🔴"
    del_st = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"

    tg_lbl = "📘 Telegram Links" if lang == "en" else "📘 Enlaces Telegram"
    fwd_lbl = "📥 Forwards Shield" if lang == "en" else "📥 Escudo Reenvíos"
    q_lbl = "💭 Quotes Filter" if lang == "en" else "💭 Filtro Citas"
    web_lbl = "🔗 Internet Links" if lang == "en" else "🔗 Enlaces Internet"
    purge_lbl = "🗑️ Delete Spam" if lang == "en" else "🗑️ Borrar Spam"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{tg_lbl} {st_tg}", callback_data=f"astog_tglinks_{group_id}_{lang}"), InlineKeyboardButton(text=f"{fwd_lbl} {st_fwd}", callback_data=f"as_fwd_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{q_lbl} {st_q}", callback_data=f"astog_quotes_{group_id}_{lang}"), InlineKeyboardButton(text=f"{web_lbl} {st_web}", callback_data=f"astog_weblinks_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{purge_lbl} {del_st}", callback_data=f"as_togdel_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


async def get_forwards_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    st_chan = "🟢" if await get_antispam_filter(group_id, "fwd_channels") == 1 else "🔴"
    st_usr = "🟢" if await get_antispam_filter(group_id, "fwd_users") == 1 else "🔴"
    st_grp = "🟢" if await get_antispam_filter(group_id, "fwd_groups") == 1 else "🔴"
    st_bot = "🟢" if await get_antispam_filter(group_id, "fwd_bots") == 1 else "🔴"

    chan_lbl = "📢 Channels" if lang == "en" else "📢 Canales"
    usr_lbl = "👤 Users" if lang == "en" else "👤 Usuarios"
    grp_lbl = "👥 Groups" if lang == "en" else "👥 Grupos"
    bot_lbl = "🤖 Bots" if lang == "en" else "🤖 Bots"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{chan_lbl} {st_chan}", callback_data=f"astog_fwdchan_{group_id}_{lang}"), InlineKeyboardButton(text=f"{usr_lbl} {st_usr}", callback_data=f"astog_fwdusr_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{grp_lbl} {st_grp}", callback_data=f"astog_fwdgrp_{group_id}_{lang}"), InlineKeyboardButton(text=f"{bot_lbl} {st_bot}", callback_data=f"astog_fwdbot_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t.get("btn_back_antispam", "🔙 Volver a Anti-Spam"), callback_data=f"gset_antispam_{group_id}_{lang}")]
    ])


async def get_locks_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    media = "🟢" if await get_lock_status(group_id, "lock_media") == 1 else "🔴"
    stickers = "🟢" if await get_lock_status(group_id, "lock_stickers") == 1 else "🔴"
    links = "🟢" if await get_lock_status(group_id, "lock_links") == 1 else "🔴"
    commands = "🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "🔴"

    media_lbl = "🖼️ Media (Photos/Videos)" if lang == "en" else "🖼️ Multimedia (Fotos/Videos)"
    stickers_lbl = "🎨 Stickers & GIFs" if lang == "en" else "🎨 Stickers y GIFs"
    links_lbl = "🔗 Web Links" if lang == "en" else "🔗 Enlaces Web"
    cmds_lbl = "⚙️ Bot Commands" if lang == "en" else "⚙️ Comandos de Bots"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{media_lbl} {media}", callback_data=f"toglock_media_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{stickers_lbl} {stickers}", callback_data=f"toglock_stickers_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{links_lbl} {links}", callback_data=f"toglock_links_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{cmds_lbl} {commands}", callback_data=f"toglock_commands_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])
async def set_warn_custom_field(group_id: int, field: str, value: str | None):
    """Guarda copy o multimedia personalizada de advertencias (PRO / ULTRA PRO)."""
    if field not in {"warn_custom_text", "warn_custom_media_id", "warn_custom_media_type"}:
        raise ValueError(f"Columna de advertencias no permitida: {field}")   # el nombre se interpola en SQL
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(group_settings)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if field not in existing_cols:
                cursor.execute(f"ALTER TABLE group_settings ADD COLUMN {field} TEXT")
            cursor.execute(f"UPDATE group_settings SET {field} = ? WHERE group_id = ?", (value, group_id))
            conn.commit()
    await asyncio.to_thread(_sync)


async def get_warn_custom_fields(group_id: int) -> dict:
    """Lee el texto y multimedia personalizada de advertencias del grupo."""
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(group_settings)")
            cols = {row[1] for row in cursor.fetchall()}
            target = [c for c in ["warn_custom_text", "warn_custom_media_id", "warn_custom_media_type"] if c in cols]
            if target:
                cursor.execute(f"SELECT {', '.join(target)} FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                if row:
                    return {c: row[i] for i, c in enumerate(target)}
        return {}
    return await asyncio.to_thread(_sync)


async def get_warns_keyboard(group_id: int, lang: str, user_id: int = 0):
    """
    Matriz granular de Advertencias por Rangos:
    - Free: Toggles base, límite y castigo.
    - PRO: Desbloquea botón para editar texto de sanción.
    - ULTRA PRO: Desbloquea botón para editar texto + botón para adjuntar multimedia (foto/video/gif).
    """
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_warns_config(group_id)
    limit = cfg["limit"]
    action = cfg["action"].upper()

    tier = await get_effective_group_tier(group_id, user_id) if user_id else await get_group_tier(group_id)
    tier = (tier or "free").lower()

    links_on = cfg.get("warn_links", 1) == 1
    blacklist_on = cfg.get("warn_blacklist", 1) == 1
    flood_on = cfg.get("warn_flood", 1) == 1

    links_dot = "🟢" if links_on else "🔴"
    blacklist_dot = "🟢" if blacklist_on else "🔴"
    flood_dot = "🟢" if flood_on else "🔴"

    links_lbl = f"🔗 {'Warn Forbidden Links' if lang == 'en' else 'Aviso Enlaces'} {links_dot}"
    blacklist_lbl = f"🚫 {'Warn Blacklist' if lang == 'en' else 'Aviso Lista Negra'} {blacklist_dot}"
    flood_lbl = f"🌊 {'Warn Anti-Flood' if lang == 'en' else 'Aviso Anti-Flood'} {flood_dot}"

    limit_lbl = f"🔢 {'Limit' if lang == 'en' else 'Límite'}: {limit}"
    action_lbl = f"⚖️ {'Action' if lang == 'en' else 'Castigo'}: {action}"

    keyboard_rows = [
        [InlineKeyboardButton(text=links_lbl, callback_data=f"warnset_toglink_{group_id}_{lang}")],
        [InlineKeyboardButton(text=blacklist_lbl, callback_data=f"warnset_togblack_{group_id}_{lang}")],
        [InlineKeyboardButton(text=flood_lbl, callback_data=f"warnset_togflood_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=limit_lbl, callback_data=f"warnset_limit_{group_id}_{lang}"),
            InlineKeyboardButton(text=action_lbl, callback_data=f"warnset_action_{group_id}_{lang}")
        ]
    ]

    # Beneficios dinámicos PRO y ULTRA PRO
    custom_cfg = await get_warn_custom_fields(group_id)
    has_text = "🟢" if custom_cfg.get("warn_custom_text") else "🔴"
    has_media = "🟢" if custom_cfg.get("warn_custom_media_id") else "🔴"

    if tier in ("pro", "ultra_pro"):
        txt_label = f"✍️ {'Edit Warn Text' if lang == 'en' else 'Editar Texto Aviso'} {has_text}"
        keyboard_rows.append([InlineKeyboardButton(text=txt_label, callback_data=f"warnset_text_{group_id}_{lang}")])

    if tier in ("ultra_pro", "ultra"):
        med_label = f"🖼️ {'Attach Media' if lang == 'en' else 'Adjuntar Multimedia'} {has_media}"
        ultra_row = [InlineKeyboardButton(text=med_label, callback_data=f"warnset_media_{group_id}_{lang}")]
        if custom_cfg.get("warn_custom_media_id"):
            del_label = "🗑️ Quitar Media" if lang == "es" else "🗑️ Remove Media"
            ultra_row.append(InlineKeyboardButton(text=del_label, callback_data=f"warnset_delmedia_{group_id}_{lang}"))
        keyboard_rows.append(ultra_row)

    keyboard_rows.append([InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


async def get_delmsgs_keyboard(group_id: int, user_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    cfg = await get_captcha_config(group_id)
    srv_mode = await get_service_msgs_mode(group_id)

    srv_del = "🟢" if cfg["service_del"] == 1 else "🔴"
    main_chat = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    block_cmds = "🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "🔴"
    srv_mode_st = "🟢" if srv_mode == 1 else "🔴"

    if tier == "free":
        tier_text = "⭐ Tier: FREE (3 purges/day)" if lang == "en" else "⭐ Plan: BÁSICO (3 purgas/día)"
        pro_btn = "⭐ Mejorar a PRO" if lang == "es" else "⭐ Upgrade PRO"
        ultra_btn = "💎 Mejorar a ULTRA" if lang == "es" else "💎 Upgrade ULTRA"

        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{'🗑️ Service Msgs' if lang == 'en' else '🗑️ Msgs de Servicio'} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{'🧹 Service Mode' if lang == 'en' else '🧹 Modo Servicio'} {srv_mode_st}", callback_data=f"delmsgs_srvmode_{group_id}_{lang}")],
            [
                InlineKeyboardButton(text=pro_btn, callback_data=f"pay_pro_{group_id}_{lang}"),
                InlineKeyboardButton(text=ultra_btn, callback_data=f"pay_ultra_{group_id}_{lang}")
            ],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        ])
    else:
        tier_text = f"⭐ Tier: {tier.upper()} (Advanced Unlocked)" if lang == "en" else f"⭐ Plan: {tier.upper()} (Avanzado Desbloqueado)"
        purge_lbl = "🗑️ Purge Service Msgs" if lang == "en" else "🗑️ Purgar Mensajes de Servicio"
        main_chat_lbl = "🧹 Main Chat Cleanup" if lang == "en" else "🧹 Limpieza Chat Principal"
        cmd_block_lbl = "🛡️ Block Cmds to Regulars" if lang == "en" else "🛡️ Bloquear Cmds a Regulares"
        srv_mode_lbl = "🧹 Service Msgs Mode" if lang == "en" else "🧹 Modo Mensajes de Servicio"

        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{purge_lbl} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{srv_mode_lbl} {srv_mode_st}", callback_data=f"delmsgs_srvmode_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{main_chat_lbl} {main_chat}", callback_data=f"delmsgs_main_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{cmd_block_lbl} {block_cmds}", callback_data=f"delmsgs_cmds_{group_id}_{lang}")],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        ])


def get_antiflood_keyboard(group_id: int, lang: str, cfg: dict):
    t = TEXTS.get(lang, TEXTS["es"])
    del_st = "🟢" if cfg["delete"] == 1 else "🔴"
    curr_act = cfg["action"]

    def act_badge(key: str, label: str):
        return f"{label} {'🟢' if curr_act == key else ''}".strip()

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{t['af_msgs']}: {cfg['msgs']}", callback_data=f"afset_msgs_{group_id}_{lang}"),
            InlineKeyboardButton(text=f"{t['af_time']}: {cfg['time']}s", callback_data=f"afset_time_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=act_badge("off", t["af_off"]), callback_data=f"afact_off_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("warn", t["af_warn"]), callback_data=f"afact_warn_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=act_badge("kick", t["af_kick"]), callback_data=f"afact_kick_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("mute", t["af_mute"]), callback_data=f"afact_mute_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("ban", t["af_ban"]), callback_data=f"afact_ban_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=f"{t['af_del_msgs']} {del_st}", callback_data=f"afact_togdel_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


def get_antiflood_number_keyboard(group_id: int, lang: str, mode: str):
    t = TEXTS.get(lang, TEXTS["es"])
    numbers = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
    kb = []
    row = []
    for num in numbers:
        row.append(InlineKeyboardButton(text=str(num), callback_data=f"afval_{mode}_{num}_{group_id}_{lang}"))
        if len(row) == 4:
            kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton(text=t.get("btn_back_antiflood", "🔙 Volver a Anti-Flood"), callback_data=f"gset_antiflood_{group_id}_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_mod_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 /ban", callback_data=f"cmd_ban_{group_id}_{lang}"), InlineKeyboardButton(text="👢 /kick", callback_data=f"cmd_kick_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🔇 /mute", callback_data=f"cmd_mute_{group_id}_{lang}"), InlineKeyboardButton(text="🔊 /unmute", callback_data=f"cmd_unmute_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_add_wl"], callback_data=f"cmd_wl_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_add_bl"], callback_data=f"cmd_bl_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


def get_time_selection_keyboard(action_name: str, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    min1 = "1 Minuto" if lang == "es" else "1 Minute"
    min10 = "10 Minutos" if lang == "es" else "10 Minutes"
    h1 = "1 Hora" if lang == "es" else "1 Hour"
    h24 = "24 Horas" if lang == "es" else "24 Hours"
    d30 = "30 Días" if lang == "es" else "30 Days"
    perm = "Permanente" if lang == "es" else "Permanent"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏱️ {min1}", callback_data=f"time_{action_name}_1m_{group_id}_{lang}"), InlineKeyboardButton(text=f"⏱️ {min10}", callback_data=f"time_{action_name}_10m_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"⏰ {h1}", callback_data=f"time_{action_name}_1h_{group_id}_{lang}"), InlineKeyboardButton(text=f"⏰ {h24}", callback_data=f"time_{action_name}_24h_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"📅 {d30}", callback_data=f"time_{action_name}_30d_{group_id}_{lang}"), InlineKeyboardButton(text=f"♾️ {perm}", callback_data=f"time_{action_name}_perm_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
    ])


def get_eco_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Radar Ecosistema" if lang == "es" else "📡 Radar Ecosystem", callback_data=f"radar_eco_{group_id}"), InlineKeyboardButton(text="🎥 /cams", callback_data=f"cmd_cams_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚙️ /autolower", callback_data=f"cmd_autolower_{group_id}_{lang}"), InlineKeyboardButton(text="🗓️ Programador VC" if lang == "es" else "🗓️ VC Scheduler", callback_data=f"vcsched_menu_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="🎙️ /mic_vip", callback_data=f"cmd_mic_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️ Etiqueta VIP" if lang == "es" else "🏷️ VIP Tag", callback_data=f"cmd_mictag_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=t["btn_tips"], callback_data=f"tips_menu_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_night_mode"], callback_data=f"night_menu_{group_id}_{lang}") # <--- ¡Añadido aquí!
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


async def get_clone_keyboard(group_id: int, user_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    # 🧭 Blindaje contextual: el Clon & Centinela es compartido por Grupos y Canales;
    # el regreso debe respetar siempre el origen real (cpanel para canal, gpanel para grupo).
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    if tier == "ultra_pro":
        clone_info = await get_bot_clone(user_id, group_id)
        has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
        session_info = await get_owner_session(user_id, group_id)
        has_sentinel = session_info is not None

        token_btn_text = (
            tr(lang, "🔄 Actualizar Token @BotFather", "🔄 Update @BotFather Token") if has_clone
            else tr(lang, "🔑 Conectar Token @BotFather", "🔑 Connect @BotFather Token")
        )
        sentinel_btn_text = (
            tr(lang, "🔄 Actualizar Centinela (Teléfono) 🟢", "🔄 Update Sentinel (Phone) 🟢") if has_sentinel
            else tr(lang, "🎙️ Conectar Centinela Propio (Teléfono) 🔴", "🎙️ Connect Own Sentinel (Phone) 🔴")
        )

        kb = [
            [InlineKeyboardButton(text=token_btn_text, callback_data=f"clone_token_{group_id}_{lang}")],
            [InlineKeyboardButton(text=sentinel_btn_text, callback_data=f"clone_phone_{group_id}_{lang}")]
        ]
        if has_clone:
            kb.append([InlineKeyboardButton(text=tr(lang, "🛑 Desconectar Bot Clon", "🛑 Disconnect Clone Bot"), callback_data=f"clone_discbot_{group_id}_{lang}")])
        if has_sentinel:
            kb.append([InlineKeyboardButton(text=tr(lang, "🛑 Desconectar Centinela", "🛑 Disconnect Sentinel"), callback_data=f"clone_discsentinel_{group_id}_{lang}")])

        kb.append([back_btn])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Unlock with ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
            [back_btn]
        ])


# ==========================================
# 🚀 ENRUTAMIENTO Y MANEJADORES EN PRIVADO
# ==========================================
async def _dismiss_reply_keyboard(message: Message) -> None:
    """Retira el teclado de respuesta (selector de canales) sin dejar rastro en el chat."""
    try:
        ghost = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())
        await ghost.delete()
    except Exception:
        pass


_PAYMENT_ROUTES_RE = re.compile(r"^/(start\s+(sub_|vipmic_|chanplan_|speaker_|tip_)|(pro|ultra)(@\w+)?(\s|$))")


def _not_payments_route(message: Message) -> bool:
    """Filtro: cede a handlers/payments.py los pagos y enlaces de cobro, sin depender del orden de include_router."""
    return not message.successful_payment and not _PAYMENT_ROUTES_RE.match(message.text or "")


@router.message(CommandStart(), F.chat.type == "private", _not_payments_route)
async def cmd_start(message: Message, bot: Bot, command: CommandObject):
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "BunkerBot"
        role = "CLON" if is_clone_bot(bot) else "MAESTRO"
        logging.info(f"🚀 [cmd_start INICIO] {role} | Bot ID: {bot_info.id} (@{bot_username}) | Usuario: {message.from_user.id}")

        # 🧹 /start es siempre un punto de reinicio limpio: libera cualquier flujo conversacional colgado.
        clear_user_states(bot.id, message.from_user.id)
        try:
            await cancel_phone_auth(message.from_user.id)
        except Exception:
            pass

        lang = user_lang(message.from_user)
        t = TEXTS.get(lang, TEXTS["es"])

        try:
            await get_or_create_user(message.from_user.id, message.from_user.username or "Sin username", message.from_user.full_name)
        except Exception as db_ex:
            logging.error(f"❌ [cmd_start DB Error]: {db_ex}")

        if command.args and command.args.startswith("gset_"):
            try:
                group_id = int(command.args.split("_")[1])
                if not await verify_admin_privileges_msg(message, bot, group_id):
                    return
                chat_kind = await resolve_chat_kind(bot, group_id)
                try:
                    g_name = html.escape((await bot.get_chat(group_id)).title or "")
                except Exception:
                    g_name = tr(lang, "Comunidad", "Community")
                if chat_kind == "c":
                    # 🧭 Un canal jamás cae en el panel de grupo: se abre su estudio (cpanel).
                    perm_warning = await get_channel_perm_warning(bot, group_id, lang)
                    await message.answer(
                        t["channel_panel_title"].format(channel_name=g_name, perm_warning=perm_warning),
                        reply_markup=get_channel_panel_keyboard(group_id, lang), parse_mode="HTML"
                    )
                else:
                    await message.answer(t["group_panel_title"].format(group_name=g_name), reply_markup=get_group_panel_keyboard(group_id, lang), parse_mode="HTML")
                return
            except Exception as g_ex:
                logging.error(f"❌ [cmd_start Deeplink Error]: {g_ex}")

        await send_official_welcome(bot, message.chat.id, message.from_user, bot_username)
        logging.info(f"✅ [cmd_start ÉXITO] Matriz desplegada en @{bot_username} para {message.from_user.id}.")
    except Exception as e:
        logging.error(f"❌ [cmd_start ERROR CRÍTICO]: {e}", exc_info=True)


@router.message(Command("login"), F.chat.type == "private")
async def cmd_login(message: Message, bot: Bot):
    """
    Genera un enlace temporal de acceso directo al dashboard web autenticado
    con un token criptográfico de un solo uso (validez de 5 minutos).
    """
    user_id = message.from_user.id
    lang = user_lang(message.from_user)
    
    token = await create_web_session(user_id)
    login_url = f"{WEBAPP_URL.rstrip('/')}/?token={token}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Abrir Dashboard Web / Open Dashboard", url=login_url)]
    ])
    
    text = (
        "🔐 <b>Acceso Web Autorizado / Web Access Authorized</b>\n\n"
        "Pulsa el botón inferior para abrir tu consola de administración en el navegador. "
        "Este enlace es único y expirará en <b>5 minutos</b> por seguridad.\n\n"
        "🇺🇸 <i>Tap the button below to open your web console. This unique link expires in <b>5 minutes</b>.</i>\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
    
    resp = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    fire_and_forget_auto_delete([message, resp], delay=120)


@router.message(Command("cancel"), F.chat.type == "private")
async def cmd_cancel(message: Message, bot: Bot):
    """Salida de emergencia universal: libera cualquier flujo pendiente y devuelve al menú principal."""
    clear_user_states(bot.id, message.from_user.id)
    try:
        await cancel_phone_auth(message.from_user.id)
    except Exception:
        pass
    lang = user_lang(message.from_user)
    t = TEXTS.get(lang, TEXTS["es"])
    await _dismiss_reply_keyboard(message)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_main_menu"], callback_data=f"menu_main_{lang}")]
    ])
    await message.answer(f"✅ {t['op_canceled']}{PERIMETER_SIGNATURE}", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    return


@router.message(F.chat.type == "private", F.chat_shared)
async def handle_channel_shared(message: Message, bot: Bot):
    """
    Sincronización Activa de Canales: recibe el canal elegido en el selector nativo de Telegram,
    verifica EN VIVO los permisos del bot y la propiedad del usuario, y lo registra al instante.
    """
    user = message.from_user
    lang = user_lang(user)
    t = TEXTS.get(lang, TEXTS["es"])
    shared = message.chat_shared

    await _dismiss_reply_keyboard(message)

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_sync_channels"], callback_data=f"menu_chsync_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")]
    ])

    if getattr(shared, "request_id", None) != CHANNEL_SYNC_REQUEST_ID:
        resp = await message.answer(t["chsync_stale"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([resp], delay=60)
        return

    channel_id = shared.chat_id
    title = getattr(shared, "title", None) or ""

    bot_username = "BunkerBot"
    try:
        me = await bot.get_me()
        bot_username = me.username or bot_username
        bot_member = await bot.get_chat_member(chat_id=channel_id, user_id=me.id)
    except Exception as ex:
        logging.warning(f"⚠️ [Sync Canales] El bot no pudo verificarse en channel={channel_id}: {ex}")
        resp = await message.answer(
            t["chsync_bot_not_admin"].format(title=html.escape(title or str(channel_id)), bot_username=bot_username),
            reply_markup=back_kb, parse_mode="HTML"
        )
        fire_and_forget_auto_delete([resp], delay=90)
        return

    if bot_member.status not in ("administrator", "creator"):
        resp = await message.answer(
            t["chsync_bot_not_admin"].format(title=html.escape(title or str(channel_id)), bot_username=bot_username),
            reply_markup=back_kb, parse_mode="HTML"
        )
        fire_and_forget_auto_delete([resp], delay=90)
        return

    try:
        chat_obj = await bot.get_chat(channel_id)
        title = chat_obj.title or title
    except Exception:
        pass

    if not is_super_admin(user.id):
        try:
            user_member = await bot.get_chat_member(chat_id=channel_id, user_id=user.id)
            is_owner = user_member.status == "creator"
        except Exception:
            is_owner = False
        if not is_owner:
            resp = await message.answer(t["chsync_not_owner"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([resp], delay=60)
            return

    CHAT_KIND_CACHE[channel_id] = "c"
    CHANNEL_SYNC_CACHE.setdefault(user.id, {})[channel_id] = title
    await registry_upsert_channel(user.id, channel_id, title)
    logging.info(f"✅ [Sync Canales] user={user.id} sincronizó channel={channel_id} ('{title}').")

    ok_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_studio"], callback_data=f"cpanel_{channel_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_sync_channels"], callback_data=f"menu_chsync_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")]
    ])
    await message.answer(t["chsync_ok"].format(title=html.escape(title or str(channel_id))), reply_markup=ok_kb, parse_mode="HTML")

@router.message(F.chat.type == "private", _not_payments_route)
async def handle_private_inputs(message: Message, bot: Bot):
    user_id = message.from_user.id
    text_input = (message.text or "").strip()

    lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
    t = TEXTS.get(lang, TEXTS["es"])

    # 0. CANCELACIÓN DEL SELECTOR DE CANALES (teclado de respuesta)
    if text_input in (TEXTS["es"]["btn_sync_cancel"], TEXTS["en"]["btn_sync_cancel"]):
        clear_user_states(bot.id, user_id)
        await _dismiss_reply_keyboard(message)
        channels_now = await get_active_user_channels(bot, user_id)
        await message.answer(
            t["chsettings_main"] if channels_now else t["chsettings_main_empty"],
            reply_markup=get_channels_keyboard(channels_now, lang), parse_mode="HTML"
        )
        return
    # 🤖 PROMPT PERSONALIZADO DE INTELIGENCIA ARTIFICIAL (CENTINELA DE IA)
    if (bot.id, user_id) in AI_PROMPT_STATES:
        ai_data = AI_PROMPT_STATES.pop((bot.id, user_id))
        group_id = ai_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"ai_menu_{group_id}_{lang}")]
        ])
        if text_input:
            await set_ai_sentinel_config(group_id, "ai_custom_prompt", text_input[:1000])
            resp = await message.answer(t["ai_prompt_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            empty_prompt_msg = "⚠️ El prompt no puede estar vacío." if lang == "es" else "⚠️ Prompt cannot be empty."
            resp = await message.answer(empty_prompt_msg + "\n\n🛡️ <i>Cloud Media Management</i>", reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return
    # 🤖 PROMPT PERSONALIZADO DE INTELIGENCIA ARTIFICIAL (CENTINELA DE IA)
    if (bot.id, user_id) in AI_PROMPT_STATES:
        ai_data = AI_PROMPT_STATES.pop((bot.id, user_id))
        group_id = ai_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"ai_menu_{group_id}_{lang}")]
        ])
        if text_input:
            await set_ai_sentinel_config(group_id, "ai_custom_prompt", text_input[:1000])
            resp = await message.answer(t["ai_prompt_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            empty_prompt_msg = "⚠️ El prompt no puede estar vacío." if lang == "es" else "⚠️ Prompt cannot be empty."
            resp = await message.answer(empty_prompt_msg + "\n\n🛡️ <i>Cloud Media Management</i>", reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # ⚙️ CONFIGURACIÓN SIMÉTRICA DEL CENTINELA (SENTINEL SETTINGS)
    if (bot.id, user_id) in SENTINEL_CFG_STATES:
        st_data = SENTINEL_CFG_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        target = st_data["target"]       # 'vc', 'micvip', 'reset', 'sched', 'vcwelcome'
        field_type = st_data["field"]    # 'msg', 'btn', 'autodel'
        tier = (await get_effective_group_tier(group_id, user_id) or "free").lower()

        text_val = (message.text or message.caption or "").strip()
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"sentinelcfg_menu_{group_id}_{lang}")]
        ])

        # Mapeo de prefijos de columnas de base de datos
        prefix_map = {
            "vc": "vc_join",
            "micvip": "mic_vip",
            "reset": "reset_notice",
            "sched": "vc_sched_start",
            "vcwelcome": "vc_welcome"
        }
        prefix = prefix_map.get(target, "vc_join")

        # 1. Tiempo de Auto-Borrado
        if field_type == "autodel":
            if text_val.isdigit() and int(text_val) >= 0:
                col_name = f"{prefix}_autodel_seconds"
                await set_sentinel_service_message(group_id, col_name, int(text_val))
                resp = await message.answer(f"✅ <b>Tiempo de auto-borrado para {target.upper()} actualizado a {text_val}s.</b>\n\n🛡️ <i>Cloud Media Management</i>", reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer("⚠️ Ingresa un número entero de segundos (0 para no borrar).", reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        # 2. Nombre del Botón y Enlace Embebido (Sintaxis: Texto | URL)
        elif field_type == "btn":
            if text_val:
                btn_text = text_val
                btn_url = None
                if "|" in text_val:
                    parts = text_val.split("|", 1)
                    btn_text = parts[0].strip()[:35]
                    url_candidate = parts[1].strip()
                    if url_candidate.startswith("http://") or url_candidate.startswith("https://") or url_candidate.startswith("t.me/"):
                        btn_url = url_candidate if url_candidate.startswith("http") else f"https://{url_candidate}"

                col_name = f"{prefix}_btn_text"
                await set_sentinel_service_message(group_id, col_name, btn_text[:35])
                if btn_url is not None:
                    url_col = f"{prefix}_btn_url"
                    await set_sentinel_service_message(group_id, url_col, btn_url)

                url_info = f"\n🔗 <b>Enlace embebido:</b> <code>{btn_url}</code>" if btn_url else ""
                resp = await message.answer(
                    f"✅ <b>{tr(lang, 'Botón configurado para', 'Button configured for')} {target.upper()}!</b>\n"
                    f"🏷️ <b>Texto:</b> <code>{html.escape(btn_text)}</code>{url_info}\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb, parse_mode="HTML"
                )
            else:
                resp = await message.answer(tr(lang, "⚠️ El texto del botón no puede estar vacío.", "⚠️ Button label cannot be empty."), reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

    # 1. CAPTCHA CUSTOM TEXT (Con Auto-Purga a 60s)
    if (bot.id, user_id) in CAPTCHA_STATES:
        group_id = CAPTCHA_STATES.pop((bot.id, user_id))
        if not text_input:
            # Entrada no textual (foto, sticker...): se conserva el estado y se guía al usuario, sin bloquear el menú.
            CAPTCHA_STATES[(bot.id, user_id)] = group_id
            resp = await message.answer(
                tr(lang, "⚠️ Envía el mensaje del captcha como <b>texto</b>.", "⚠️ Send the captcha message as <b>text</b>.") + PERIMETER_SIGNATURE,
                reply_markup=_cancel_kb(t, f"gset_captcha_{group_id}_{lang}"), parse_mode="HTML"
            )
            fire_and_forget_auto_delete([message, resp], delay=60)
            return
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver"), callback_data=f"gset_captcha_{group_id}_{lang}")]
        ])
        try:
            await set_captcha_config(group_id, "captcha_text", text_input)
            resp = await message.answer(t["captcha_saved"].format(text_input=text_input), reply_markup=back_kb, parse_mode="HTML")
        except Exception as ex:
            logging.error(f"❌ [Captcha] No se pudo guardar el mensaje en group={group_id}: {ex}")
            resp = await message.answer(
                tr(lang, "⚠️ No pude guardar el mensaje del captcha. Inténtalo de nuevo.", "⚠️ I couldn't save the captcha message. Please try again.") + PERIMETER_SIGNATURE,
                reply_markup=back_kb, parse_mode="HTML"
            )
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 1b. COPY PERSONALIZADO DE ADVERTENCIAS (PRO / ULTRA PRO)
    if (bot.id, user_id) in WARN_CUSTOM_TEXT_STATES:
        w_data = WARN_CUSTOM_TEXT_STATES.pop((bot.id, user_id))
        group_id = w_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"gset_warns_{group_id}_{lang}")]
        ])
        if text_input:
            await set_warn_custom_field(group_id, "warn_custom_text", text_input[:1000])
            resp = await message.answer(
                "✅ <b>¡Mensaje de advertencia actualizado con éxito!</b>\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer("⚠️ El mensaje no puede estar vacío.", reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 1c. MULTIMEDIA PERSONALIZADA DE ADVERTENCIAS (ULTRA PRO)
    if (bot.id, user_id) in WARN_CUSTOM_MEDIA_STATES:
        w_data = WARN_CUSTOM_MEDIA_STATES.pop((bot.id, user_id))
        group_id = w_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"gset_warns_{group_id}_{lang}")]
        ])
        media_id = None
        media_type = None
        if message.photo:
            media_id = message.photo[-1].file_id
            media_type = "photo"
        elif message.animation:
            media_id = message.animation.file_id
            media_type = "animation"
        elif message.video:
            media_id = message.video.file_id
            media_type = "video"

        if media_id and media_type:
            await set_warn_custom_field(group_id, "warn_custom_media_id", media_id)
            await set_warn_custom_field(group_id, "warn_custom_media_type", media_type)
            resp = await message.answer(
                "✅ <b>¡Multimedia asociada a advertencias guardada con éxito!</b>\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer("⚠️ Envía una Foto, Video o GIF válido.", reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 1d. COPY EXPLICATIVO DE MICVIP (PRO / ULTRA PRO)
    if (bot.id, user_id) in MIC_VIP_TEXT_STATES:
        m_data = MIC_VIP_TEXT_STATES.pop((bot.id, user_id))
        group_id = m_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"cmd_mic_{group_id}_{lang}")]
        ])
        if text_input:
            await set_mic_vip_custom_config(group_id, "mic_vip_custom_text", text_input[:500])
            resp = await message.answer(
                "✅ <b>¡Descripción explicativa de MicVIP guardada con éxito!</b>\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer("⚠️ El texto no puede estar vacío.", reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 2. TOKEN BOTFATHER
    if (bot.id, user_id) in CLONE_STATES:
        state_data = CLONE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        token = text_input
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [(await origin_back_button(bot, group_id, lang))]
        ])

        if ":" in token and len(token) > 30:
            status_msg = await message.answer(t["token_verifying"], parse_mode="HTML")
            test_bot = Bot(token=token)
            try:
                bot_info = await test_bot.get_me()
                await test_bot.session.close()
                bot_username = bot_info.username or ""

                old_clone = await get_bot_clone(user_id, group_id)
                if old_clone and old_clone[0] and old_clone[0] != token:
                    try:
                        _call_clone_trigger("trigger_disconnect_clone", old_clone[0])
                    except Exception:
                        pass

                await register_bot_clone(user_id, group_id, token, bot_username)
                try:
                    _call_clone_trigger("trigger_dynamic_clone", token)
                except Exception:
                    pass

                try:
                    await status_msg.delete()
                except Exception:
                    pass

                success_text = (
                    f"✅ <b>¡Instancia de Réplica Conectada y Activa!</b>\n\n"
                    f"• <b>Bot Clon:</b> @{bot_username}\n"
                    f"• <b>Estado:</b> Operativo 🟢\n"
                    f"• <b>Comunidad:</b> Blindada con tu réplica\n\n"
                    f"<i>¡Listo! Ya puedes abrir @{bot_username} y presionar /start.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ) if lang == "es" else (
                    f"✅ <b>Replica Instance Connected and Active!</b>\n\n"
                    f"• <b>Clone Bot:</b> @{bot_username}\n"
                    f"• <b>Status:</b> Operational 🟢\n"
                    f"• <b>Community:</b> Shielded by your replica\n\n"
                    f"<i>All set! You can now open @{bot_username} and press /start.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                )
                resp = await message.answer(success_text, reply_markup=back_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
            except Exception:
                try:
                    await test_bot.session.close()
                except Exception:
                    pass
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                resp = await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            resp = await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return
    # 3. CENTINELA TELÉFONO
    if (bot.id, user_id) in SENTINEL_PHONE_STATES:
        state_data = SENTINEL_PHONE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["phone_requesting"], parse_mode="HTML")
        res = await start_phone_auth(user_id, group_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "ok":
            SENTINEL_CODE_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            resp = await message.answer(t["phone_sent"].format(phone=res['phone']), reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [(await origin_back_button(bot, group_id, lang))]
            ])
            error_reason = tr(lang, "Número no válido.", "Invalid phone number.") if res.get("message") == "invalid_phone" else f"Telegram: {html.escape(str(res.get('message')))}"
            resp = await message.answer(t["phone_error"].format(reason=error_reason), reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 4. CENTINELA CÓDIGO 5 DÍGITOS
    if (bot.id, user_id) in SENTINEL_CODE_STATES:
        state_data = SENTINEL_CODE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["code_verifying"], parse_mode="HTML")
        res = await verify_phone_code(user_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "success":
            session_str = res["session_string"]
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [(await origin_back_button(bot, group_id, lang))]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                resp = await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        elif res["status"] == "2fa_required":
            SENTINEL_2FA_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            resp = await message.answer(t["twofa_required"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [(await origin_back_button(bot, group_id, lang))]
            ])
            resp = await message.answer(t["code_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 5. CENTINELA 2FA
    if (bot.id, user_id) in SENTINEL_2FA_STATES:
        state_data = SENTINEL_2FA_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["twofa_verifying"], parse_mode="HTML")
        res = await verify_2fa_password(user_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "success":
            session_str = res["session_string"]
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [(await origin_back_button(bot, group_id, lang))]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                resp = await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [(await origin_back_button(bot, group_id, lang))]
            ])
            resp = await message.answer(t["twofa_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 6. PROGRAMADOR VC
    if (bot.id, user_id) in VC_SCHED_STATES:
        sched_data = VC_SCHED_STATES.pop((bot.id, user_id))
        group_id = sched_data["group_id"]
        current_sched = await get_vc_schedule(group_id)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"vcsched_menu_{group_id}_{lang}")]
        ])
        if "-" in text_input and len(text_input.split("-")) == 2:
            parts = text_input.split("-")
            start = parts[0].strip()
            end = parts[1].strip()
            await set_vc_schedule(group_id, current_sched["days"], start, end, current_sched["status"])
            resp = await message.answer(t["sched_updated"].format(start=start, end=end), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sched_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 6b. MODO NOCTURNO — HORARIO HH:MM-HH:MM
    if (bot.id, user_id) in NIGHT_STATES:
        night_data = NIGHT_STATES.pop((bot.id, user_id))
        group_id = night_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"night_menu_{group_id}_{lang}")]
        ])
        parsed = None
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", text_input)
        if match:
            h1, m1, h2, m2 = (int(g) for g in match.groups())
            if 0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59:
                parsed = (f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}")
        if not parsed:
            resp = await message.answer(t["night_err"], reply_markup=back_kb, parse_mode="HTML")
        else:
            start, end = parsed
            try:
                await set_night_mode_config(group_id, "night_mode_start", start)
                await set_night_mode_config(group_id, "night_mode_end", end)
                # Verificación de lectura: solo se confirma el éxito si el horario realmente quedó persistido.
                saved_cfg = await get_night_mode_config(group_id)
                saved = saved_cfg["start"] == start and saved_cfg["end"] == end
            except Exception as ex:
                logging.error(f"❌ [Night Mode] No se pudo persistir el horario en group={group_id}: {ex}")
                saved = False
            resp = await message.answer(t["night_updated"] if saved else t["night_save_failed"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 7. REGISTRO WL / BL
    if (bot.id, user_id) in DB_REG_STATES:
        data = DB_REG_STATES.pop((bot.id, user_id))
        reg_type = data["type"]
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
        ])
        if reg_type == "wl":
            target_id = None
            if text_input.isdigit():
                target_id = int(text_input)
            elif text_input.startswith("@"):
                try:
                    c = await bot.get_chat(text_input)
                    target_id = c.id
                except Exception:
                    target_id = None
            if target_id:
                await add_to_whitelist(target_id)
                resp = await message.answer(t["wl_success"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["id_err"], reply_markup=back_kb, parse_mode="HTML")
        else:
            word = text_input.lower()
            await add_to_blacklist(word)
            resp = await message.answer(t["bl_success"].format(word=word), reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 8. DIRECTIVAS DE MODERACIÓN
    if (bot.id, user_id) in MOD_TARGET_STATES:
        st = MOD_TARGET_STATES.pop((bot.id, user_id))
        action = st["action"]
        group_id = st["group_id"]
        duration = st["duration"]
        dur_label = st["dur_label"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
        ])
        target_id = None
        if text_input.isdigit():
            target_id = int(text_input)
        elif text_input.startswith("@"):
            try:
                c = await bot.get_chat(text_input)
                target_id = c.id
            except Exception:
                target_id = None

        if not target_id:
            resp = await message.answer(t["mod_id_err"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        if action in ("ban", "kick", "mute") and is_immune_account(target_id, bot.id):
            resp = await message.answer(t["mod_immune"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        try:
            if action == "ban":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until if until > 0 else None)
                resp = await message.answer(t["dir_ban"].format(target_id=target_id, dur_label=dur_label), reply_markup=back_kb, parse_mode="HTML")
            elif action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=target_id)
                resp = await message.answer(t["dir_kick"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
            elif action == "mute":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.restrict_chat_member(
                    chat_id=group_id, user_id=target_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until if until > 0 else None
                )
                resp = await message.answer(t["dir_mute"].format(target_id=target_id, dur_label=dur_label), reply_markup=back_kb, parse_mode="HTML")
            elif action == "unmute":
                await bot.restrict_chat_member(
                    chat_id=group_id, user_id=target_id,
                    permissions=ChatPermissions(
                        can_send_messages=True, can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                )
                resp = await message.answer(t["dir_unmute"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
        except TelegramRetryAfter as flood:
            resp = await message.answer(t["mod_flood_wait"].format(seconds=flood.retry_after), reply_markup=back_kb, parse_mode="HTML")
        except Exception as ex:
            resp = await message.answer(t["dir_err"].format(ex=html.escape(str(ex))), reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 9. MIC VIP STARS & TAG
    if (bot.id, user_id) in MIC_VIP_STATES:
        data = MIC_VIP_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            price_val = int(text_input)
            GROUP_MIC_PRICE[group_id] = price_val
            await set_mic_vip_custom_config(group_id, "mic_vip_custom_price", price_val)
            resp = await message.answer(t["mic_updated"].format(group_id=group_id, price_val=price_val), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["mic_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in MIC_TAG_STATES:
        data = MIC_TAG_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        if 1 <= len(text_input) <= 16:
            GROUP_VIP_TAG[group_id] = text_input
            await set_vip_badge_title(group_id, text_input)
            await set_mic_vip_custom_config(group_id, "mic_vip_custom_tag", text_input)
            resp = await message.answer(t["tag_updated"].format(group_id=group_id, text_input=text_input), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["tag_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 10. DUCKING PODCAST & SPEAKERS PRICE
    if (bot.id, user_id) in PODCAST_DUCK_STATES:
        data = PODCAST_DUCK_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"podcast_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and 1 <= int(text_input) <= 90:
            duck_level = int(text_input)
            GROUP_DUCK_LEVEL[group_id] = duck_level
            await disengage_podcast_ducking(group_id)
            await engage_podcast_ducking(group_id, duck_level)
            resp = await message.answer(t["duck_updated"].format(group_id=group_id, duck_level=duck_level) + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["duck_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SPEAKER_PRICE_STATES:
        data = SPEAKER_PRICE_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"speakers_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            price_val = int(text_input)
            GROUP_SPEAKER_PRICE[group_id] = price_val
            resp = await message.answer(t["speakers_price_updated"].format(group_id=group_id, price=price_val) + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["speakers_price_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 11. PROPINAS EN STARS (TIPS) — FLUJOS CONVERSACIONALES
    # Cada estado guarda group_id + referencia al panel (panel_chat_id / panel_msg_id) para refrescarlo en vivo.
    tips_key = (bot.id, user_id)

    # 11a. MONTO SUGERIDO
    if tips_key in TIPS_AMOUNT_STATES:
        st = TIPS_AMOUNT_STATES[tips_key]
        group_id = st["group_id"]
        if re.fullmatch(r"[0-9]{1,6}", text_input) and 1 <= int(text_input) <= TIPS_MAX_AMOUNT:
            TIPS_AMOUNT_STATES.pop(tips_key, None)
            await set_tips_config(group_id, "tips_amount", int(text_input))
            await _tips_finish(bot, message, st, group_id, lang, t["tips_updated"])
            return
        resp = await message.answer(
            f"{t['tips_amount_err'].replace(PERIMETER_SIGNATURE, '')} (1 - {TIPS_MAX_AMOUNT})" + PERIMETER_SIGNATURE,
            reply_markup=_cancel_kb(t, f"tips_menu_{group_id}_{lang}"), parse_mode="HTML"
        )
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 11b. AÑADIR CANAL DESTINO: procesa ID/@usuario → aviso temporal → panel actualizado en tiempo real
    if tips_key in TIPS_TARGET_STATES:
        st = TIPS_TARGET_STATES[tips_key]
        group_id = st["group_id"]
        cancel_kb = _cancel_kb(t, f"tips_menu_{group_id}_{lang}")
        back_kb = _tips_back_kb(t, group_id, lang)
        tier = _tips_tier(await get_effective_group_tier(group_id, user_id))
        limit = _tips_limit(tier)
        targets_before = await _tips_get_targets(group_id)

        status = await message.answer(tr(lang, "⏳ <b>Verificando canal destino...</b>", "⏳ <b>Checking target channel...</b>") + PERIMETER_SIGNATURE, parse_mode="HTML")

        if len(targets_before) >= limit:
            TIPS_TARGET_STATES.pop(tips_key, None)
            await _tips_say(status, tips_target_error_text(lang, "limit", n=len(targets_before), limit=limit), back_kb)
        else:
            ref = parse_tip_target_input(text_input)
            info, err = (None, "invalid") if not ref else await validate_tip_target(bot, user_id, ref)
            known = {v.lower() for _, v, _ in targets_before}
            if err:
                await _tips_say(status, tips_target_error_text(lang, err), cancel_kb)
            elif info["value"].lower() in known or str(info["chat_id"]) in known:
                await _tips_say(status, tips_target_error_text(lang, "duplicate"), cancel_kb)
            else:
                TIPS_TARGET_STATES.pop(tips_key, None)
                try:
                    await add_group_tip_target(group_id, info["value"])
                    saved = len(await _tips_get_targets(group_id)) > len(targets_before)
                except Exception as ex:
                    logging.error(f"❌ [Tips] Error registrando canal {info['value']} en group={group_id}: {ex}")
                    saved = False
                if saved:
                    _TIPS_ALIAS[info["value"].lower()] = info["title"]
                    await _tips_finish(
                        bot, message, st, group_id, lang,
                        tr(lang,
                           f"✅ <b>¡Canal destino añadido!</b>\n\n📢 {html.escape(info['title'])} (<code>{html.escape(info['value'])}</code>)\n"
                           f"• Canales: <code>{len(targets_before) + 1}/{limit}</code>",
                           f"✅ <b>Target channel added!</b>\n\n📢 {html.escape(info['title'])} (<code>{html.escape(info['value'])}</code>)\n"
                           f"• Channels: <code>{len(targets_before) + 1}/{limit}</code>") + PERIMETER_SIGNATURE,
                        status_msg=status
                    )
                    return
                await _tips_say(status, tips_target_error_text(lang, "save_failed"), back_kb)
        fire_and_forget_auto_delete([message, status], delay=60)
        return

    # 11c. EDITAR TEXTO DE PROPINAS (PRO / ULTRA PRO)
    if tips_key in TIPS_TEXT_STATES:
        st = TIPS_TEXT_STATES[tips_key]
        group_id = st["group_id"]
        cancel_kb = _cancel_kb(t, f"tips_menu_{group_id}_{lang}")
        tier = _tips_tier(await get_effective_group_tier(group_id, user_id))
        plain = (message.text or message.caption or "").strip()

        if tier not in ("pro", "ultra_pro"):
            TIPS_TEXT_STATES.pop(tips_key, None)
            resp = await message.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires a PRO or ULTRA PRO plan.") + PERIMETER_SIGNATURE,
                                        reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
        elif not plain:
            resp = await message.answer(tr(lang, "⚠️ Envía el mensaje como <b>texto</b>; no puede estar vacío.", "⚠️ Send the message as <b>text</b>; it cannot be empty.") + PERIMETER_SIGNATURE,
                                        reply_markup=cancel_kb, parse_mode="HTML")
        elif len(plain) > TIPS_TEXT_MAX_LEN:
            resp = await message.answer(tr(lang, f"⚠️ El texto excede {TIPS_TEXT_MAX_LEN} caracteres ({len(plain)}). Acórtalo.",
                                           f"⚠️ The text exceeds {TIPS_TEXT_MAX_LEN} characters ({len(plain)}). Shorten it.") + PERIMETER_SIGNATURE,
                                        reply_markup=cancel_kb, parse_mode="HTML")
        else:
            TIPS_TEXT_STATES.pop(tips_key, None)
            try:
                await set_tips_config(group_id, "tips_custom_text", message.html_text.strip())
            except Exception as ex:
                logging.error(f"❌ [Tips] Error guardando texto en group={group_id}: {ex}")
                resp = await message.answer(tips_target_error_text(lang, "save_failed"), reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
            else:
                await _tips_finish(bot, message, st, group_id, lang,
                                   tr(lang, "✅ <b>¡Texto de propinas actualizado con éxito!</b>", "✅ <b>Tips text updated successfully!</b>") + PERIMETER_SIGNATURE)
                return
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 11d. ADJUNTAR MULTIMEDIA A PROPINAS (EXCLUSIVO ULTRA PRO)
    if tips_key in TIPS_MEDIA_STATES:
        st = TIPS_MEDIA_STATES[tips_key]
        group_id = st["group_id"]
        tier = _tips_tier(await get_effective_group_tier(group_id, user_id))
        media_id = media_type = None
        if message.photo:
            media_id, media_type = message.photo[-1].file_id, "photo"
        elif message.animation:
            media_id, media_type = message.animation.file_id, "animation"
        elif message.video:
            media_id, media_type = message.video.file_id, "video"

        if tier != "ultra_pro":
            TIPS_MEDIA_STATES.pop(tips_key, None)
            resp = await message.answer(tr(lang, "💎 Requiere nivel ULTRA PRO.", "💎 Requires ULTRA PRO tier.") + PERIMETER_SIGNATURE,
                                        reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
        elif not media_id:
            resp = await message.answer(tr(lang, "⚠️ Envía una <b>Foto, Video o GIF</b> válido.", "⚠️ Send a valid <b>Photo, Video or GIF</b>.") + PERIMETER_SIGNATURE,
                                        reply_markup=_cancel_kb(t, f"tips_menu_{group_id}_{lang}"), parse_mode="HTML")
        else:
            TIPS_MEDIA_STATES.pop(tips_key, None)
            try:
                await set_tips_config(group_id, "tips_media_type", media_type)
                await set_tips_config(group_id, "tips_media_id", media_id)
            except Exception as ex:
                logging.error(f"❌ [Tips] Error guardando multimedia en group={group_id}: {ex}")
                resp = await message.answer(tips_target_error_text(lang, "save_failed"), reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
            else:
                await _tips_finish(bot, message, st, group_id, lang,
                                   tr(lang, "✅ <b>¡Multimedia de propinas guardada con éxito!</b>", "✅ <b>Tips media saved successfully!</b>") + PERIMETER_SIGNATURE)
                return
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 12. PAYLOAD MULTIMEDIA CENTINELA
    if (bot.id, user_id) in SENTINEL_PAYLOAD_TEXT_STATES:
        st_data = SENTINEL_PAYLOAD_TEXT_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        await set_sentinel_payload_config(group_id, "sentinel_payload_text", text_input)
        resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SENTINEL_PAYLOAD_AUTODEL_STATES:
        st_data = SENTINEL_PAYLOAD_AUTODEL_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) >= 0:
            val = int(text_input)
            await set_sentinel_payload_config(group_id, "sentinel_payload_auto_delete", val if val > 0 else None)
            resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sentinel_payload_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SENTINEL_PAYLOAD_MEDIA_STATES:
        st_data = SENTINEL_PAYLOAD_MEDIA_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        media_id = None
        media_type = None

        if message.photo:
            media_id = message.photo[-1].file_id
            media_type = "photo"
        elif message.animation:
            media_id = message.animation.file_id
            media_type = "animation"
        elif message.video:
            media_id = message.video.file_id
            media_type = "video"

        if media_id and media_type:
            await set_sentinel_payload_config(group_id, "sentinel_payload_media_id", media_id)
            await set_sentinel_payload_config(group_id, "sentinel_payload_media_type", media_type)
            resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sentinel_payload_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

   # 13. CREACIÓN DE PLANES DE MEMBRESÍA DE CANAL — resiliente a reinicios de RAM (Con Link VIP y Recurrencia)
    plan_key = (bot.id, user_id)
    if plan_key not in CHAN_PLAN_STATES:
        await restore_plan_state(plan_key)   # ♻️ tras un reinicio en frío se retoma el asistente donde quedó
    if plan_key in CHAN_PLAN_STATES:
        st_data = CHAN_PLAN_STATES[plan_key]
        channel_id = st_data.get("channel_id")
        step = st_data.get("step")
        expired = (time.time() - st_data.get("ts", 0)) > STATE_TTL_SECONDS

        # Estado corrupto, con paso desconocido o caducado → se libera y se guía al usuario.
        required_by_step = {
            "name": (), 
            "days": ("name",), 
            "price": ("name", "days"),
            "promo": ("name", "days", "price"), 
            "media": ("name", "days", "price", "promo"),
            "link": ("name", "days", "price", "promo"),
            "recurrence": ("name", "days", "price", "promo", "link")
        }
        incomplete = step in required_by_step and any(st_data.get(k) in (None, "") for k in required_by_step[step])
        if channel_id is None or step not in required_by_step or expired or incomplete:
            forget_plan_state(*plan_key)
            await _reply_plan_state_lost(message, lang, channel_id)
            return

        # Re-verificación de propiedad en cada paso del asistente.
        if not await verify_admin_privileges_msg(message, bot, channel_id):
            forget_plan_state(*plan_key)
            return

        st_data["ts"] = time.time()
        plan_kb = _plan_step_keyboard(t, channel_id, lang)

        if step == "name":
            plan_name = text_input[:30].strip()
            if not plan_name:
                resp = await message.answer(
                    tr(lang, "⚠️ Envía un nombre válido para el plan (texto).", "⚠️ Send a valid plan name (text).") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return
            st_data["name"] = plan_name
            st_data["step"] = "days"
            await persist_plan_state(plan_key)
            prompt_days = (
                "⏳ <b>Duración del plan en días:</b>\n\nEnvía un número entero (ejemplo: <code>30</code> para un mes):"
                if lang == "es" else
                "⏳ <b>Plan duration in days:</b>\n\nSend an integer (e.g. <code>30</code>):"
            ) + PERIMETER_SIGNATURE
            resp = await message.answer(prompt_days, reply_markup=plan_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "days":
            if not text_input.isdigit() or not (0 < int(text_input) <= 3650):
                resp = await message.answer(
                    tr(lang, "⚠️ Ingresa un número entero de días válido (1 a 3650).", "⚠️ Enter a valid whole number of days (1 to 3650).") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return
            st_data["days"] = int(text_input)
            st_data["step"] = "price"
            await persist_plan_state(plan_key)
            prompt_price = (
                "⭐ <b>Precio en Telegram Stars (XTR):</b>\n\nEnvía la tarifa en Stars que costará la membresía (ejemplo: <code>150</code>):"
                if lang == "es" else
                "⭐ <b>Price in Telegram Stars (XTR):</b>\n\nSend the price in Stars (e.g. <code>150</code>):"
            ) + PERIMETER_SIGNATURE
            resp = await message.answer(prompt_price, reply_markup=plan_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "price":
            if not text_input.isdigit() or int(text_input) <= 0:
                resp = await message.answer(
                    tr(lang, "⚠️ Ingresa un precio entero válido en Stars.", "⚠️ Enter a valid whole price in Stars.") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            st_data["price"] = int(text_input)
            st_data["step"] = "promo"
            await persist_plan_state(plan_key)
            prompt_promo = (
                "📝 <b>Mensaje promocional (Copy):</b>\n\n"
                "Envía el texto que verán tus suscriptores antes de pagar. Soporta formato HTML "
                "(<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a href&gt;</code>, etc.):"
            ) if lang == "es" else (
                "📝 <b>Promotional message (Copy):</b>\n\n"
                "Send the text your subscribers will see before paying. HTML formatting is supported "
                "(<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a href&gt;</code>, etc.):"
            )
            resp = await message.answer(prompt_promo + PERIMETER_SIGNATURE, reply_markup=plan_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "promo":
            promo_text = text_input[:1000].strip()
            if not promo_text:
                resp = await message.answer(
                    tr(lang, "⚠️ El mensaje promocional no puede estar vacío.", "⚠️ The promotional message cannot be empty.") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            st_data["promo"] = promo_text
            st_data["step"] = "media"
            await persist_plan_state(plan_key)

            skip_label = "⏭️ Omitir Multimedia" if lang == "es" else "⏭️ Skip Media"
            media_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=skip_label, callback_data=f"chplans_skip_{channel_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"chplans_menu_{channel_id}_{lang}")]
            ])
            prompt_media = (
                "🖼️ <b>Multimedia del anuncio (opcional):</b>\n\n"
                "Envía una Foto, Video o Animación/GIF para acompañar el mensaje promocional, "
                "o pulsa «Omitir» para publicarlo solo en texto:"
            ) if lang == "es" else (
                "🖼️ <b>Promo media (optional):</b>\n\n"
                "Send a Photo, Video or Animation/GIF to accompany the promo message, "
                "or tap «Skip» to publish it as text only:"
            )
            resp = await message.answer(prompt_media + PERIMETER_SIGNATURE, reply_markup=media_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "media":
            media_id = None
            media_type = None
            if message.photo:
                media_id = message.photo[-1].file_id
                media_type = "photo"
            elif message.video:
                media_id = message.video.file_id
                media_type = "video"
            elif message.animation:
                media_id = message.animation.file_id
                media_type = "animation"

            if not media_id:
                skip_label = "⏭️ Omitir Multimedia" if lang == "es" else "⏭️ Skip Media"
                retry_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=skip_label, callback_data=f"chplans_skip_{channel_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"chplans_menu_{channel_id}_{lang}")]
                ])
                warn_text = (
                    "⚠️ Envía una Foto, Video o Animación válida, o pulsa «Omitir»."
                    if lang == "es" else
                    "⚠️ Send a valid Photo, Video or Animation, or tap «Skip»."
                )
                resp = await message.answer(warn_text + PERIMETER_SIGNATURE, reply_markup=retry_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            st_data["media_id"] = media_id
            st_data["media_type"] = media_type
            st_data["step"] = "link"
            await persist_plan_state(plan_key)

            prompt_link = (
                "🔗 <b>Enlace de Destino VIP (Entrega Automática):</b>\n\n"
                "Envía el enlace de invitación o recurso exclusivo que el bot entregará de forma automática al usuario tras confirmar su pago con Stars:"
            ) if lang == "es" else (
                "🔗 <b>VIP Target Link (Automatic Delivery):</b>\n\n"
                "Send the invite link or exclusive resource the bot will automatically deliver upon confirming payment:"
            )
            resp = await message.answer(prompt_link + PERIMETER_SIGNATURE, reply_markup=plan_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "link":
            target_link = text_input[:255].strip()
            if not target_link:
                resp = await message.answer(
                    tr(lang, "⚠️ Envía un enlace de destino válido.", "⚠️ Send a valid destination link.") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            st_data["link"] = target_link
            st_data["step"] = "recurrence"
            await persist_plan_state(plan_key)

            prompt_recurrence = (
                "⏰ <b>Difusión Recurrente Automática:</b>\n\n"
                "Envía el intervalo en <b>horas</b> para republicar este anuncio automáticamente (ejemplo: <code>24</code> para cada día, o <code>0</code> para desactivar recurrencia):"
            ) if lang == "es" else (
                "⏰ <b>Automatic Recurring Broadcast:</b>\n\n"
                "Send the interval in <b>hours</b> to automatically repost this announcement (e.g. <code>24</code> for daily, or <code>0</code> to disable):"
            )
            resp = await message.answer(prompt_recurrence + PERIMETER_SIGNATURE, reply_markup=plan_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "recurrence":
            if not text_input.isdigit():
                resp = await message.answer(
                    tr(lang, "⚠️ Ingresa un número entero de horas válido (ej. 24 o 0).", "⚠️ Enter a valid integer hours value (e.g. 24 or 0).") + PERIMETER_SIGNATURE,
                    reply_markup=plan_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            recurrence_hours = int(text_input)
            plan_name = st_data["name"]
            duration_days = st_data["days"]
            price_stars = st_data["price"]
            promo_text = st_data.get("promo", "")
            media_id = st_data.get("media_id")
            media_type = st_data.get("media_type")
            target_link = st_data.get("link")
            forget_plan_state(*plan_key)

            await _finalize_and_preview_channel_plan(
                bot=bot,
                chat_id=message.chat.id,
                channel_id=channel_id,
                lang=lang,
                name=plan_name,
                days=duration_days,
                price=price_stars,
                promo_text=promo_text,
                media_id=media_id,
                media_type=media_type,
                target_link=target_link,
                recurrence_hours=recurrence_hours
            )
            return

    # 14. 🛟 FALLBACK ANTI-BLOQUEO: si no hay flujo activo (p. ej. tras un reinicio de RAM en Railway),
    # el bot jamás calla: informa y ofrece volver al menú (con enfriamiento para no saturar el chat).
    has_payload = bool(
        text_input or message.photo or message.video or message.animation
        or message.document or message.sticker or message.voice
    )
    if not has_payload or getattr(message, "successful_payment", None):
        return
    now = time.time()
    if len(_FALLBACK_LAST_REPLY) > 5000:
        _FALLBACK_LAST_REPLY.clear()
    if now - _FALLBACK_LAST_REPLY.get((bot.id, user_id), 0.0) < FALLBACK_COOLDOWN_SECONDS:
        return
    _FALLBACK_LAST_REPLY[(bot.id, user_id)] = now
    fallback_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_main_menu"], callback_data=f"menu_main_{lang}")]
    ])
    resp = await message.answer(t["session_expired"], reply_markup=fallback_kb, parse_mode="HTML")
    fire_and_forget_auto_delete([resp], delay=60)


@router.callback_query(
    F.data.startswith("menu_") | F.data.startswith("lang_") | F.data.startswith("langpanel_") |
    F.data.startswith("langcpanel_") | F.data.startswith("gpanel_") | F.data.startswith("cpanel_") |
    F.data.startswith("cmd_") | F.data.startswith("pay_") | F.data.startswith("time_") |
    F.data.startswith("clone_") | F.data.startswith("alset_") | F.data.startswith("micval_") |
    F.data.startswith("reg_") | F.data.startswith("vcsched_") | F.data.startswith("tips_") |
    F.data.startswith("night_") | F.data.startswith("radar_")
)
async def process_menu_navigation(callback: CallbackQuery, bot: Bot):
    # 🧹 Toda navegación libera las conversaciones pendientes (evita menús privados bloqueados tras reinicios)
    clear_user_states(bot.id, callback.from_user.id)
    try:
        await cancel_phone_auth(callback.from_user.id)
    except Exception as ex:
        logging.warning(f"⚠️ [Nav] cancel_phone_auth falló (se ignora): {ex}")

    data = callback.data.split("_")
    action = data[0]
    lang = data[-1] if len(data) > 1 and data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None
    card_key = None      # 🖼️ Clave de CARD_IMAGES: si existe la imagen, reemplaza al texto
    card_caption = None  # Caption opcional para datos dinámicos

    if action == "lang":
        lang = data[1]
        text = TEXTS.get(lang, TEXTS["es"])["welcome"].format(name=callback.from_user.full_name)
        keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot))
        card_key = "welcome"

    elif action == "langpanel":
        group_id = int(data[1])
        lang = data[2]
        t = TEXTS.get(lang, TEXTS["es"])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = html.escape((await bot.get_chat(group_id)).title or "")
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        CHAT_KIND_CACHE[group_id] = "g"
        text = t["group_panel_title"].format(group_name=g_name)
        keyboard = get_group_panel_keyboard(group_id, lang)
        card_key, card_caption = "security_matrix", text

    elif action == "langcpanel":
        channel_id = int(data[1])
        lang = data[2]
        t = TEXTS.get(lang, TEXTS["es"])
        if not await verify_admin_privileges(callback, bot, channel_id):
            return
        try:
            c_name = html.escape((await bot.get_chat(channel_id)).title or "")
        except Exception:
            c_name = "Canal" if lang == "es" else "Channel"
        CHAT_KIND_CACHE[channel_id] = "c"
        text = t["channel_panel_title"].format(channel_name=c_name, perm_warning=await get_channel_perm_warning(bot, channel_id, lang))
        keyboard = get_channel_panel_keyboard(channel_id, lang)
        card_key, card_caption = "settings_channels", text

    elif action == "cpanel":
        channel_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, channel_id):
            return
        try:
            c_name = html.escape((await bot.get_chat(channel_id)).title or "")
        except Exception:
            c_name = "Canal" if lang == "es" else "Channel"
        CHAT_KIND_CACHE[channel_id] = "c"
        text = t["channel_panel_title"].format(channel_name=c_name, perm_warning=await get_channel_perm_warning(bot, channel_id, lang))
        keyboard = get_channel_panel_keyboard(channel_id, lang)
        card_key, card_caption = "settings_channels", text

    elif action == "gpanel":
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = html.escape((await bot.get_chat(group_id)).title or "")
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        CHAT_KIND_CACHE[group_id] = "g"
        text = t["group_panel_title"].format(group_name=g_name)
        keyboard = get_group_panel_keyboard(group_id, lang)
        card_key, card_caption = "security_matrix", text

    elif action == "menu":
        target = data[1]
        if target == "main":
            text = t["welcome"].format(name=callback.from_user.full_name)
            keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot))
            card_key = "welcome"

        elif target == "settings":
            active_groups = await get_active_user_groups(bot, callback.from_user.id)
            keyboard = get_groups_keyboard(active_groups, lang)
            text = t["settings_main"]
            card_key = "settings"

        elif target == "chsettings":
            active_channels = await get_active_user_channels(bot, callback.from_user.id)
            keyboard = get_channels_keyboard(active_channels, lang)
            text = t["chsettings_main"] if active_channels else t["chsettings_main_empty"]
            card_key = "settings_channels"

        elif target == "chsync":
            try:
                await callback.message.answer(t["chsync_prompt"], reply_markup=build_channel_request_keyboard(lang), parse_mode="HTML")
            except Exception as ex:
                logging.error(f"❌ [Sync Canales] No se pudo abrir selector: {ex}")
            return

        elif target == "support":
            text, keyboard = t["support_main"], get_support_keyboard(lang)

        elif target == "info":
            text, keyboard = t["info_main"], get_info_keyboard(lang)
            card_key = "info_core"

        elif target == "infohow":
            text = t["info_how_main"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛡️ " + ("Grupos y Perímetro" if lang == "es" else "Groups & Perimeter"), callback_data=f"menu_infomod_groups_{lang}")],
                [InlineKeyboardButton(text="📡 " + ("Canales y Lives" if lang == "es" else "Channels & Lives"), callback_data=f"menu_infomod_channels_{lang}")],
                [InlineKeyboardButton(text="💰 " + ("Monetización Stars" if lang == "es" else "Stars Monetization"), callback_data=f"menu_infomod_monetization_{lang}")],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])
            card_key = "info_how"

        elif target == "infomod":
            mod_name = data[2] if len(data) > 2 else "groups"
            mod_text_key = f"info_mod_{mod_name}"
            mod_desc = t.get(mod_text_key, t["info_how_main"])
            text = f"📖 <b>{tr(lang, 'Centro de Conocimiento', 'Knowledge Center')}</b>\n\n{mod_desc}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Volver a Guías" if lang == "es" else "🔙 Back to Guides", callback_data=f"menu_infohow_{lang}")],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])
            card_key = {
                "groups": "info_groups",
                "channels": "info_channels",
                "monetization": "info_monetization"
            }.get(mod_name)

        elif target == "id":
            user, tier_db = callback.from_user, await get_user_global_tier(callback.from_user.id)
            if is_super_admin(user.id):
                rank_str = "Arquitecto Supremo (Inmunidad Total) ⚡" if lang == "es" else "Supreme Architect (Total Immunity) ⚡"
            else:
                rank_str = (tr(lang, "Comandante ULTRA 💎", "ULTRA Commander 💎") if tier_db == "ultra_pro" else (tr(lang, "Comandante PRO ⭐", "PRO Commander ⭐") if tier_db == "pro" else tr(lang, "Comandante (Free)", "Commander (Free)")))
            text, keyboard = t["id_status"].format(id=user.id, username=user.username or "N/A", rank=rank_str), get_simple_back_keyboard(lang)

        elif target in ["mod", "eco"]:
            group_id = int(data[2])
            if not await verify_admin_privileges(callback, bot, group_id):
                return
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            text = t[f"{target}_main"].format(group_name=g_name)
            keyboard = get_mod_keyboard(group_id, lang) if target == "mod" else get_eco_keyboard(group_id, lang)
            card_key, card_caption = "security_matrix", text

        elif target == "ultra":
            group_id = int(data[2])
            if not await verify_admin_privileges(callback, bot, group_id):
                return
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            chat_kind = await resolve_chat_kind(bot, group_id)
            text = t["ultra_tools_main"].format(group_name=g_name)
            keyboard = get_ultra_tools_keyboard(group_id, lang, chat_type=chat_kind)
            card_key, card_caption = "security_matrix", text

    elif action == "radar":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        telemetry = await get_community_live_telemetry(group_id)
        try:
            g_name = html.escape((await bot.get_chat(group_id)).title or "")
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"

        st_panic = "🚨 ACTIVO" if telemetry["panic_active"] else "🟢 Inactivo"
        st_shield = "🟢 Blindado" if telemetry["shield_status"] else "🔴 Desactivado"
        st_podcast = "🟢 Activo" if telemetry["podcast_status"] else "🔴 Inactivo"
        st_autolower = "🟢 Activo (2%)" if telemetry["autolower_status"] else "🔴 Desactivado"
        st_night = "🟢 Activo" if telemetry["night_mode_status"] else "🔴 Inactivo"
        st_clone = "🟢 Operativo" if telemetry["has_active_clone"] else "🔴 No configurado"
        st_sentinel = "🟢 Conectado" if telemetry["has_active_sentinel"] else "🔴 Desconectado"

        text = (
            f"📡 <b>Radar & Telemetría en Vivo — {g_name}</b>\n\n"
            f"• 🎙️ <b>Pases VIP Activos:</b> <code>{telemetry['vip_passes_active']}</code>\n"
            f"• ⏳ <b>Speakers en Cola:</b> <code>{telemetry['speakers_in_queue']}</code>\n\n"
            f"<b>Perímetro Defensivo:</b>\n"
            f"• 🚨 <b>Botón de Pánico:</b> {st_panic}\n"
            f"• 🎥 <b>Escudo Antinota:</b> {st_shield}\n"
            f"• 🎙️ <b>Modo Podcast:</b> {st_podcast}\n"
            f"• ⚙️ <b>AutoLower (2%):</b> {st_autolower}\n"
            f"• 🌙 <b>Modo Nocturno:</b> {st_night}\n\n"
            f"<b>Infraestructura Propia:</b>\n"
            f"• 🧬 <b>Bot Clon:</b> {st_clone}\n"
            f"• 🎙️ <b>Centinela MTProto:</b> {st_sentinel}\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            f"📡 <b>Live Ecosystem Radar — {g_name}</b>\n\n"
            f"• 🎙️ <b>Active VIP Passes:</b> <code>{telemetry['vip_passes_active']}</code>\n"
            f"• ⏳ <b>Speakers in Queue:</b> <code>{telemetry['speakers_in_queue']}</code>\n\n"
            f"<b>Perimeter Security:</b>\n"
            f"• 🚨 <b>Panic Button:</b> {st_panic}\n"
            f"• 🎥 <b>Screen-Share Shield:</b> {st_shield}\n"
            f"• 🎙️ <b>Podcast Mode:</b> {st_podcast}\n"
            f"• ⚙️ <b>AutoLower (2%):</b> {st_autolower}\n"
            f"• 🌙 <b>Night Mode:</b> {st_night}\n\n"
            f"<b>Private Infrastructure:</b>\n"
            f"• 🧬 <b>Bot Clone:</b> {st_clone}\n"
            f"• 🎙️ <b>MTProto Sentinel:</b> {st_sentinel}\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 " + ("Actualizar Telemetría" if lang == "es" else "Refresh"), callback_data=f"radar_eco_{group_id}_{lang}")],
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])

    elif action == "tips":
        sub = data[1] if len(data) > 1 else "menu"
        try:
            if sub in ("deltarget", "toggletarget"):
                target_id, group_id = int(data[2]), int(data[3])
            elif sub == "postto":
                group_id = int(data[2])
                target_chat_id = int(data[3])
            else:
                group_id = int(data[2])
        except (ValueError, IndexError):
            await callback.answer()
            return

        if not await verify_admin_privileges(callback, bot, group_id):
            return

        viewer_id = callback.from_user.id
        chat_kind = await resolve_chat_kind(bot, group_id)
        cfg = await get_tips_config(group_id) or {}
        tier = _tips_tier(await get_effective_group_tier(group_id, viewer_id))
        back_ctx = f"tips_menu_{group_id}_{lang}"
        state_key = (bot.id, viewer_id)

        if sub in ("menu", "toggle", "toggletarget", "deltarget"):
            if sub == "toggle":
                new_st = 0 if _tips_is_on(cfg) else 1
                await set_tips_config(group_id, "tips_enabled", new_st)
                await callback.answer(tr(lang, "🟢 Propinas activadas", "🟢 Tips enabled") if new_st else tr(lang, "🔴 Propinas desactivadas", "🔴 Tips disabled"))
            elif sub == "toggletarget":
                await toggle_group_tip_target(group_id, target_id)
                await callback.answer(tr(lang, "⚙️ Estado del canal actualizado.", "⚙️ Channel status updated."))
            elif sub == "deltarget":
                await delete_group_tip_target(group_id, target_id)
                await callback.answer(tr(lang, "🗑️ Canal destino eliminado.", "🗑️ Target channel removed."))
            else:
                await callback.answer()

            text, keyboard = await build_tips_panel(group_id, lang, chat_kind, viewer_id, bot=bot)

        elif sub == "setamount":
            await callback.answer()
            await _tips_open_prompt(
                callback, TIPS_AMOUNT_STATES, state_key, group_id, lang,
                f"{t['tips_prompt_amount'].replace(PERIMETER_SIGNATURE, '')}\n<i>{tr(lang, 'Número entero entre', 'Whole number between')} 1 - {TIPS_MAX_AMOUNT}</i>" + PERIMETER_SIGNATURE,
                _cancel_kb(t, back_ctx)
            )
            return

        elif sub == "settext":
            if tier not in ("pro", "ultra_pro"):
                await callback.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires a PRO or ULTRA PRO plan."), show_alert=True)
                return
            await callback.answer()
            await _tips_open_prompt(
                callback, TIPS_TEXT_STATES, state_key, group_id, lang,
                tr(lang,
                   f"✍️ <b>Editor de Mensaje de Propinas (PRO / ULTRA):</b>\n\nEnvía el texto que acompañará la publicación (máx. {TIPS_TEXT_MAX_LEN} caracteres). "
                   f"Puedes usar formato de Telegram y el marcador <code>{{amount}}</code> para insertar el monto sugerido.",
                   f"✍️ <b>Tip Message Editor (PRO / ULTRA):</b>\n\nSend the text that will accompany the broadcast (max. {TIPS_TEXT_MAX_LEN} characters). "
                   f"You can use Telegram formatting and the <code>{{amount}}</code> placeholder to insert the suggested amount.") + PERIMETER_SIGNATURE,
                _cancel_kb(t, back_ctx)
            )
            return

        elif sub == "setmedia":
            if tier != "ultra_pro":
                await callback.answer(tr(lang, "💎 Requiere nivel ULTRA PRO.", "💎 Requires ULTRA PRO tier."), show_alert=True)
                return
            await callback.answer()
            await _tips_open_prompt(
                callback, TIPS_MEDIA_STATES, state_key, group_id, lang,
                tr(lang,
                   "🖼️ <b>Adjuntar Multimedia a Propinas (ULTRA PRO):</b>\n\nEnvía una <b>Foto, Video o GIF</b> que se publicará junto con las propinas.",
                   "🖼️ <b>Attach Tip Media (ULTRA PRO):</b>\n\nSend a <b>Photo, Video or GIF</b> to publish along with the tips.") + PERIMETER_SIGNATURE,
                _cancel_kb(t, back_ctx)
            )
            return

        elif sub == "addtarget":
            limit = _tips_limit(tier)
            current = len(await _tips_get_targets(group_id))
            if current >= limit:
                await callback.answer(tips_target_error_text(lang, "limit", n=current, limit=limit).replace(PERIMETER_SIGNATURE, ""), show_alert=True)
                return
            await callback.answer()
            await _tips_open_prompt(
                callback, TIPS_TARGET_STATES, state_key, group_id, lang,
                f"{t['tips_prompt_target'].replace(PERIMETER_SIGNATURE, '')}\n<i>{tr(lang, 'El bot debe ser administrador del canal.', 'The bot must be an administrator of the channel.')}</i>"
                f"\n<code>{current}/{limit}</code>" + PERIMETER_SIGNATURE,
                _cancel_kb(t, back_ctx)
            )
            return

        elif sub == "broadcast":
            if not _tips_is_on(cfg):
                await callback.answer(tr(lang, "⚠️ Activa el módulo de propinas antes de publicar.", "⚠️ Enable the tips module before broadcasting."), show_alert=True)
                return
            active_targets = [v for _, v, active in await _tips_get_targets(group_id) if active]
            if not active_targets:
                await callback.answer(tr(lang, "⚠️ No hay canales activos habilitados para difusión.", "⚠️ No active channels enabled for broadcast."), show_alert=True)
                return
            skipped = max(0, len(active_targets) - _tips_limit(tier))
            active_targets = active_targets[:_tips_limit(tier)]
            wait = int(TIPS_BROADCAST_COOLDOWN - (time.time() - _TIPS_LAST_BROADCAST.get(group_id, 0)))
            if wait > 0:
                await callback.answer(tr(lang, f"⏳ Espera {wait}s antes de publicar de nuevo.", f"⏳ Wait {wait}s before broadcasting again."), show_alert=True)
                return
            _TIPS_LAST_BROADCAST[group_id] = time.time()
            await callback.answer(tr(lang, "📡 Publicando en los canales activos...", "📡 Broadcasting to active channels..."))

            sent, failed = await dispatch_tips_broadcast(bot, tier, cfg, lang, active_targets, group_id=group_id)
            if sent == 0:
                _TIPS_LAST_BROADCAST.pop(group_id, None)
            report = tr(lang,
                        f"📡 <b>Difusión de propinas finalizada</b>\n\n✅ Enviados: <code>{sent}</code>\n❌ Fallidos: <code>{len(failed)}</code>",
                        f"📡 <b>Tips broadcast finished</b>\n\n✅ Sent: <code>{sent}</code>\n❌ Failed: <code>{len(failed)}</code>")
            if skipped:
                report += tr(lang, f"\n⚠️ Omitidos por el límite de tu licencia: <code>{skipped}</code>",
                             f"\n⚠️ Skipped due to your license limit: <code>{skipped}</code>")
            if failed:
                report += "\n" + "\n".join(f"• <code>{html.escape(v)}</code>" for v in failed[:10])
                report += tr(lang, "\n\n<i>Verifica que el bot siga siendo administrador con permiso para publicar.</i>",
                             "\n\n<i>Check that the bot is still an administrator with permission to post.</i>")
            resp = await bot.send_message(chat_id=viewer_id, text=report + PERIMETER_SIGNATURE, reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
            fire_and_forget_auto_delete([resp], delay=60)
            return

        elif sub == "share":
            groups = await get_active_user_groups(bot, callback.from_user.id)
            channels = await get_active_user_channels(bot, callback.from_user.id)

            share_rows = []
            for g_id, g_name in groups:
                share_rows.append([InlineKeyboardButton(text=f"👥 {g_name[:24]}", callback_data=f"tips_postto_{group_id}_{g_id}_{lang}")])
            for c_id, c_name in channels:
                share_rows.append([InlineKeyboardButton(text=f"📢 {c_name[:24]}", callback_data=f"tips_postto_{group_id}_{c_id}_{lang}")])

            share_rows.append([InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=back_ctx)])
            share_kb = InlineKeyboardMarkup(inline_keyboard=share_rows)

            share_text = (
                f"📢 <b>Difundir Propinas y Donaciones (Stars)</b>\n\n"
                f"Selecciona la comunidad o canal administrado donde deseas publicar la tarjeta de propinas con botón directo de pago en Stars:\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                f"📢 <b>Broadcast Tips & Donations (Stars)</b>\n\n"
                f"Select the managed group or channel where you want to post the tips card with direct Stars checkout:\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            await safe_edit_text(callback, share_text, reply_markup=share_kb, parse_mode="HTML")
            return

        elif sub == "postto":
            if not target_chat_id:
                await callback.answer(tr(lang, "⚠️ Destino no válido.", "⚠️ Invalid target."), show_alert=True)
                return

            text_pub, media_id, media_type = build_tips_publication(tier, cfg, lang)
            bot_info = await bot.get_me()
            tip_url = f"https://t.me/{bot_info.username or 'TheBunkerBot'}?start=tip_{group_id}"
            pub_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "⭐ Enviar Propina en Stars", "⭐ Send Stars Tip"), url=tip_url)]
            ])

            try:
                if media_id and media_type == "photo":
                    await bot.send_photo(chat_id=target_chat_id, photo=media_id, caption=text_pub, reply_markup=pub_kb, parse_mode="HTML")
                elif media_id and media_type == "video":
                    await bot.send_video(chat_id=target_chat_id, video=media_id, caption=text_pub, reply_markup=pub_kb, parse_mode="HTML")
                elif media_id and media_type == "animation":
                    await bot.send_animation(chat_id=target_chat_id, animation=media_id, caption=text_pub, reply_markup=pub_kb, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=target_chat_id, text=text_pub, reply_markup=pub_kb, parse_mode="HTML")

                await callback.answer(tr(lang, "✅ ¡Propina difundida con éxito!", "✅ Tips broadcasted successfully!"), show_alert=True)
            except Exception as e:
                logging.exception("Error difundiendo propinas en %s", target_chat_id)
                await callback.answer(tr(lang, "⚠️ No se pudo difundir. Verifica permisos.", "⚠️ Broadcast failed. Check bot permissions."), show_alert=True)

            text, keyboard = await build_tips_panel(group_id, lang, chat_kind, viewer_id, bot=bot)
            await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
            return

        elif sub == "telemetry":
            await callback.answer()
            total_stars = await get_group_total_tips(group_id)
            targets = await _tips_get_targets(group_id)
            channels_txt = "\n".join(f"{'🟢' if active else '🔴'} <code>{html.escape(v)}</code>" for _, v, active in targets) \
                or tr(lang, "<i>Sin canales vinculados</i>", "<i>No linked channels</i>")
            text = (
                f"📊 <b>{tr(lang, 'Telemetría de Propinas & Donaciones', 'Tips & Donations Telemetry')}</b>\n\n"
                f"• 💰 <b>{tr(lang, 'Total Recaudado', 'Total Raised')}:</b> <code>{total_stars} Stars (XTR)</code>\n"
                f"• ⚙ <b>{tr(lang, 'Módulo', 'Module')}:</b> {'🟢' if _tips_is_on(cfg) else '🔴'}\n"
                f"• 📢 <b>{tr(lang, 'Canales Vinculados', 'Linked Channels')}:</b> <code>{len(targets)}/{_tips_limit(tier)}</code>\n\n"
                f"{channels_txt}\n\n"
                f"<i>{tr(lang, 'Las propinas se acreditan en tiempo real al confirmar cada pago en Stars.', 'Tips are credited in real time once each Stars payment is confirmed.')}</i>"
                f"{PERIMETER_SIGNATURE}"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 " + tr(lang, "Actualizar", "Refresh"), callback_data=f"tips_telemetry_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=back_ctx)],
            ])

        else:
            await callback.answer()
            return

    elif action == "pay":
        tier_level = data[1]
        if tier_level not in ("pro", "ultra"):
            return
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = html.escape((await bot.get_chat(group_id)).title or "")
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        chat_kind = await resolve_chat_kind(bot, group_id)
        text = t[f"pay_{tier_level}_title"].format(group_name=g_name)
        keyboard = get_payment_keyboard(group_id, lang, tier_level=tier_level, chat_type=chat_kind)
        card_key = f"pay_{tier_level}"

    elif action == "vcsched":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            sched_lock_text = tr(lang, "🗓️ <b>Programador VC (ULTRA PRO)</b>\n\n🔒 <i>Exclusivo del nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>", "🗓️ <b>VC Scheduler (ULTRA PRO)</b>\n\n🔒 <i>Exclusive to the ULTRA PRO tier.</i>\n\n🛡️ <i>Cloud Media Management</i>")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "💎 Desbloquear con ULTRA", "💎 Upgrade to ULTRA"), callback_data=f"pay_ultra_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
            try:
                await safe_edit_text(callback, sched_lock_text, reply_markup=keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                pass
            return

        if sub == "menu" or sub == "toggle":
            if sub == "toggle":
                sched = await get_vc_schedule(group_id)
                new_st = 0 if sched["status"] == 1 else 1
                await set_vc_schedule(group_id, sched["days"], sched["start_time"], sched["end_time"], new_st)

            sched = await get_vc_schedule(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if sched["status"] == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["vcsched_main"].format(st_badge=st_badge, days=sched['days'], start=sched['start_time'], end=sched['end_time'])
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_sched_off"] if sched["status"] == 1 else t["btn_sched_on"], callback_data=f"vcsched_toggle_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_sched_mod"], callback_data=f"vcsched_timeprompt_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub == "timeprompt":
            VC_SCHED_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["vcsched_prompt"], reply_markup=_cancel_kb(t, f"vcsched_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif action == "clone":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        chat_kind = await resolve_chat_kind(bot, group_id)

        if sub in ("token", "phone"):
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                clone_lock_text = tr(lang, "🧬 <b>Clonación & Centinela (ULTRA PRO)</b>\n\n🔒 <i>Exclusivo del nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>", "🧬 <b>Clone & Sentinel (ULTRA PRO)</b>\n\n🔒 <i>Exclusive to the ULTRA PRO tier.</i>\n\n🛡️ <i>Cloud Media Management</i>")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=tr(lang, "💎 Desbloquear con ULTRA", "💎 Upgrade to ULTRA"), callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [await origin_back_button(bot, group_id, lang)]
                ])
                try:
                    await safe_edit_text(callback, clone_lock_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

        if sub == "token":
            CLONE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            prompt = await callback.message.answer(t["botfather_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "phone":
            SENTINEL_PHONE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            # 🖼️ Despacha la tarjeta del Centinela 24/7 en lugar de solo texto plano
            prompt = await send_card_message(bot, callback.from_user.id, "sentinel_info", lang, cancel_kb,
                                             caption=t["sentinel_phone_guide"]) \
                or await callback.message.answer(t["sentinel_phone_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "cancel":
            for d in [CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, SENTINEL_2FA_STATES]:
                d.pop((bot.id, callback.from_user.id), None)
            await cancel_phone_auth(callback.from_user.id)
            await callback.answer(t["op_canceled"], show_alert=False)
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"

            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
            status = tr(lang, "Operativo 🟢", "Active 🟢") if has_clone else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = tr(lang, "Conectado 🟢", "Connected 🟢") if session_info else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
            card_key, card_caption = "sentinel", text
        elif sub == "discbot":
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            if clone_info and clone_info[0]:
                try:
                    _call_clone_trigger("trigger_disconnect_clone", clone_info[0])
                except Exception:
                    pass
            await revoke_bot_clone_db(callback.from_user.id, group_id)
            await callback.answer(t["clone_disc"], show_alert=True)
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = tr(lang, "Conectado 🟢", "Connected 🟢") if session_info else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=tr(lang, "Desconectado 🔴", "Disconnected 🔴"), sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
            card_key, card_caption = "sentinel", text
        elif sub == "discsentinel":
            await disconnect_sentinel(group_id)
            await revoke_owner_session(callback.from_user.id, group_id)
            await callback.answer(t["sentinel_disc"], show_alert=True)
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
            status = tr(lang, "Operativo 🟢", "Active 🟢") if has_clone else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=tr(lang, "Desconectado 🔴", "Disconnected 🔴"))
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
            card_key, card_caption = "sentinel", text

    elif action == "cmd":
        sub_cmd = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        if sub_cmd == "wl":
            text = t["wl_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_add_wl"], callback_data=f"reg_wl_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "bl":
            text = t["bl_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_add_bl"], callback_data=f"reg_bl_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "cams":
            text = t["cams_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "autolower":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                al_lock_text = tr(lang, "🔇 <b>Radar AutoLower (ULTRA PRO)</b>\n\n🔒 <i>Disponible exclusivamente en ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>", "🔇 <b>AutoLower Radar (ULTRA PRO)</b>\n\n🔒 <i>Available exclusively on ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=tr(lang, "💎 Desbloquear con ULTRA", "💎 Upgrade to ULTRA"), callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
                ])
                try:
                    await safe_edit_text(callback, al_lock_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

            curr_al = await get_autolower_status(group_id)
            status_str = tr(lang, "🟢 ACTIVADO (2% para no autorizados)", "🟢 ACTIVE (2% for unauthorized)") if curr_al == 1 else tr(lang, "🔴 DESACTIVADO (Micrófonos Libres)", "🔴 DISABLED (Open Mics)")
            text = t["al_menu"].format(status_str=status_str)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=t["btn_al_1"], callback_data=f"alset_1_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_al_0"], callback_data=f"alset_0_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mic":
            custom_cfg = await get_mic_vip_custom_config(group_id)
            curr_price = custom_cfg.get("price") or 50
            curr_tag = custom_cfg.get("tag") or "⚜️MIC🎙️VIP⚜️"
            has_desc = "🟢" if custom_cfg.get("text") else "🔴"

            text = t["mic_menu"].format(curr_price=curr_price, curr_tag=curr_tag) + f"\n• <b>Copy Explicativo:</b> {has_desc}\n\n" + PERIMETER_SIGNATURE
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_custom_rate"], callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")],
                [InlineKeyboardButton(text=f"✍️ {'Explanatory Copy' if lang == 'en' else 'Mensaje Explicativo'} {has_desc}", callback_data=f"cmd_mictext_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])

        elif sub_cmd == "mictext":
            MIC_VIP_TEXT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt_text = (
                "✍️ <b>Editor de Mensaje Explicativo de MicVIP:</b>\n\n"
                "Envía el texto que se le presentará a los miembros antes de comprar su pase VIP:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "✍️ <b>MicVIP Explanatory Copy Editor:</b>\n\n"
                "Send the text members will see before activating their VIP mic pass:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            prompt = await callback.message.answer(prompt_text, reply_markup=_cancel_kb(t, f"cmd_mic_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

        elif sub_cmd == "mictag":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                await callback.answer(t["tag_pro_req"], show_alert=True)
                return
            MIC_TAG_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(
                t["tag_menu_prompt"] + PERIMETER_SIGNATURE,
                reply_markup=_cancel_kb(t, f"cmd_mic_{group_id}_{lang}"),
                parse_mode="HTML"
            )
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif action == "reg":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        DB_REG_STATES[(bot.id, callback.from_user.id)] = {"type": sub, "group_id": group_id, "lang": lang}
        target_name = t["reg_ask_wl"] if sub == "wl" else t["reg_ask_bl"]
        prompt = await callback.message.answer(t["reg_ask"].format(target_name=target_name), reply_markup=_cancel_kb(t, f"menu_mod_{group_id}_{lang}"), parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)
        return

    elif action == "alset":
        new_st = int(data[1])
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            await callback.answer(tr(lang, "🔒 Requiere ULTRA PRO.", "🔒 Requires ULTRA PRO."), show_alert=True)
            return
        await set_autolower_status(group_id, new_st)
        await callback.answer(t["al_updated_1"] if new_st == 1 else t["al_updated_0"])
        curr_al = await get_autolower_status(group_id)
        status_str = tr(lang, "🟢 ACTIVADO (2% para no autorizados)", "🟢 ACTIVE (2% for unauthorized)") if curr_al == 1 else tr(lang, "🔴 DESACTIVADO (Micrófonos Libres)", "🔴 DISABLED (Open Mics)")
        text = t["al_menu"].format(status_str=status_str)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_al_1"], callback_data=f"alset_1_{group_id}_{lang}"),
                InlineKeyboardButton(text=t["btn_al_0"], callback_data=f"alset_0_{group_id}_{lang}")
            ],
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])

    elif action == "micval":
        sub_val = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        if sub_val == "custom":
            MIC_VIP_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["mic_custom_prompt"] + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"cmd_mic_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        else:
            price_int = int(sub_val)
            await set_mic_vip_custom_config(group_id, "mic_vip_custom_price", price_int)
            GROUP_MIC_PRICE[group_id] = price_int
            await callback.answer(t["mic_alert_set"].format(price_int=price_int), show_alert=True)
            
            custom_cfg = await get_mic_vip_custom_config(group_id)
            curr_tag = custom_cfg.get("tag") or "⚜️MIC🎙️VIP⚜️"
            has_desc = "🟢" if custom_cfg.get("text") else "🔴"
            
            text = t["mic_menu"].format(curr_price=price_int, curr_tag=curr_tag) + f"\n• <b>Copy Explicativo:</b> {has_desc}\n\n" + PERIMETER_SIGNATURE
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_custom_rate"], callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")],
                [InlineKeyboardButton(text=f"✍️ {'Explanatory Copy' if lang == 'en' else 'Mensaje Explicativo'} {has_desc}", callback_data=f"cmd_mictext_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])

    elif action == "time":
        sub_cmd = data[1]
        dur_str = data[2]
        group_id = int(data[3])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        dur_map = {
            "1m": (60, "1 Minuto" if lang == "es" else "1 Minute"),
            "10m": (600, "10 Minutos" if lang == "es" else "10 Minutes"),
            "1h": (3600, "1 Hora" if lang == "es" else "1 Hour"),
            "24h": (86400, "24 Horas" if lang == "es" else "24 Hours"),
            "30d": (2592000, "30 Días" if lang == "es" else "30 Days"),
            "perm": (0, "Permanente" if lang == "es" else "Permanent")
        }
        sec, label = dur_map.get(dur_str, (0, "Permanente" if lang == "es" else "Permanent"))
        MOD_TARGET_STATES[(bot.id, callback.from_user.id)] = {
            "action": sub_cmd, "group_id": group_id, "duration": sec,
            "dur_label": label, "lang": lang
        }
        prompt = await callback.message.answer(t["mod_ask_target"].format(sub_cmd_upper=f"{sub_cmd.upper()} ({label})"), reply_markup=_cancel_kb(t, f"menu_mod_{group_id}_{lang}"), parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)
        return

    elif action == "night":
        sub = data[1]
        if sub == "toggle":
            new_st = int(data[2])
            group_id = int(data[3])
        else:
            group_id = int(data[2])

        if not await verify_admin_privileges(callback, bot, group_id):
            return

        if sub == "menu" or sub == "toggle":
            if sub == "toggle":
                await set_night_mode_config(group_id, "night_mode_status", new_st)

            cfg = await get_night_mode_config(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if cfg["status"] == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")

            text = t["night_main"].format(st_badge=st_badge, start=cfg["start"], end=cfg["end"], action=cfg["action"])
            keyboard = get_night_keyboard(group_id, lang, cfg["status"])
            try:
                await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                pass
        elif sub == "prompt":
            NIGHT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["night_prompt"], reply_markup=_cancel_kb(t, f"night_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    # 🖼️ Inyección y renderizado centralizado: si la vista tiene tarjeta HUD, se despacha la imagen
    if card_key and await render_card(bot, callback.from_user.id, card_key, lang, keyboard,
                                      caption=card_caption, old_message=callback.message):
        return

    if text and keyboard:
        try:
            await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(
    F.data.startswith("gset_") | F.data.startswith("astog_") | F.data.startswith("as_") |
    F.data.startswith("togcap_") | F.data.startswith("togmode_") | F.data.startswith("cap_set_") |
    F.data.startswith("capval_") | F.data.startswith("afset_") | F.data.startswith("afval_") |
    F.data.startswith("afact_") | F.data.startswith("toglock_") | F.data.startswith("warnset_") |
    F.data.startswith("delmsgs_")
)
async def cb_group_modules_interceptor(callback: CallbackQuery, bot: Bot):
    # 🧹 Cada interacción con un módulo libera conversaciones huérfanas (p. ej. editor de captcha abandonado).
    clear_user_states(bot.id, callback.from_user.id)
    data = callback.data.split("_")
    action = data[0]

    if callback.data.startswith("cap_set_"):
        action = "cap_set"

    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    if action == "gset" and len(data) == 2 and data[1].lstrip("-").isdigit():
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = html.escape((await bot.get_chat(group_id)).title or "")
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        await safe_edit_text(callback,
            t["group_panel_title"].format(group_name=g_name),
            reply_markup=get_group_panel_keyboard(group_id, lang),
            parse_mode="HTML"
        )
        return

    try:
        group_id = int(data[-2])
    except (ValueError, IndexError):
        return

    if not await verify_admin_privileges(callback, bot, group_id):
        return

    if action == "gset":
        module = data[1]
        if module == "captcha":
            cfg = await get_captcha_config(group_id)
            st_text = "🟢" if cfg["status"] == 1 else "🔴"
            mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
            time_text = str(cfg["time"])
            action_text = cfg["action"].upper()
            await safe_edit_text(callback,
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=time_text, action_text=action_text),
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "locks":
            media_lbl = "Bloqueado 🟢" if lang == "es" else "Locked 🟢"
            media_free = "Permitido 🔴" if lang == "es" else "Allowed 🔴"
            media = media_lbl if await get_lock_status(group_id, "lock_media") == 1 else media_free
            stickers = media_lbl if await get_lock_status(group_id, "lock_stickers") == 1 else media_free
            links = media_lbl if await get_lock_status(group_id, "lock_links") == 1 else media_free
            commands = media_lbl if await get_lock_status(group_id, "lock_commands") == 1 else media_free
            await safe_edit_text(callback,
                t["locks_main_title"].format(media=media, stickers=stickers, links=links, commands=commands),
                reply_markup=await get_locks_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "warns":
            cfg = await get_warns_config(group_id)
            await safe_edit_text(callback,
                t["warns_main_title"].format(limit=cfg["limit"], action=cfg["action"].upper()),
                reply_markup=await get_warns_keyboard(group_id, lang, user_id=callback.from_user.id),
                parse_mode="HTML"
            )
        elif module == "delmsgs":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            tier_display = tier.upper()
            quota_desc = (tr(lang, "3 purgas de servicio diarias (Plan Básico)", "3 daily service purges (Free Plan)") if tier == "free" else tr(lang, "Purga automatizada ilimitada (PRO / ULTRA)", "Unlimited automated purge (PRO / ULTRA)"))
            await safe_edit_text(callback,
                t["delmsgs_main_title"].format(tier_display=tier_display, quota_desc=quota_desc),
                reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang),
                parse_mode="HTML"
            )
        elif module == "antispam":
            await safe_edit_text(callback,
                await get_antispam_text(group_id, lang),
                reply_markup=await get_antispam_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "antiflood":
            cfg = await get_antiflood_config(group_id)
            delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
            await safe_edit_text(callback,
                t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st),
                reply_markup=get_antiflood_keyboard(group_id, lang, cfg),
                parse_mode="HTML"
            )
        elif module == "clone":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "")
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
            status = tr(lang, "Operativo 🟢", "Active 🟢") if has_clone else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = tr(lang, "Conectado 🟢", "Connected 🟢") if session_info else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
            chat_kind = await resolve_chat_kind(bot, group_id)

            clone_text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            clone_kb = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
            if not await render_card(bot, callback.from_user.id, "sentinel", lang, clone_kb,
                                     caption=clone_text, old_message=callback.message):
                await safe_edit_text(callback, clone_text, reply_markup=clone_kb, parse_mode="HTML")


    elif action == "astog":
        filter_str = data[1]
        real_filter = FILTER_MAP.get(filter_str)
        if real_filter:
            current = await get_antispam_filter(group_id, real_filter)
            await set_antispam_filter(group_id, real_filter, 0 if current == 1 else 1)
            try:
                if filter_str.startswith("fwd"):
                    await callback.message.edit_reply_markup(reply_markup=await get_forwards_keyboard(group_id, lang))
                else:
                    await safe_edit_text(callback,
                        await get_antispam_text(group_id, lang),
                        reply_markup=await get_antispam_keyboard(group_id, lang),
                        parse_mode="HTML"
                    )
            except TelegramBadRequest:
                pass

    elif action == "as":
        sub = data[1]
        if sub == "fwd":
            await safe_edit_text(callback, t["forwards_panel"], reply_markup=await get_forwards_keyboard(group_id, lang), parse_mode="HTML")
        elif sub == "togdel":
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
            try:
                await safe_edit_text(callback,
                    await get_antispam_text(group_id, lang),
                    reply_markup=await get_antispam_keyboard(group_id, lang),
                    parse_mode="HTML"
                )
            except TelegramBadRequest:
                pass

    elif action == "delmsgs":
        sub = data[1]
        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if sub == "tier":
            info = (tr(lang, "ℹ️ Plan Básico: Límite de 3 purgas diarias.", "ℹ️ Free Plan: 3 purges per day limit.") if tier == "free" else tr(lang, f"⭐ Plan {tier.upper()}: Cuota mensual automatizada.", f"⭐ {tier.upper()} Plan: Automated monthly quota."))
            await callback.answer(info, show_alert=True)
            return
        elif sub == "srvmode":
            current_srv = await get_service_msgs_mode(group_id)
            await set_service_msgs_mode(group_id, 0 if current_srv == 1 else 1)
        elif sub == "main":
            if tier == "free":
                await callback.answer(tr(lang, "⚠️ Requiere nivel PRO o ULTRA.", "⚠️ Requires PRO or ULTRA tier."), show_alert=True)
                return
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
        elif sub == "cmds":
            if tier == "free":
                await callback.answer(tr(lang, "⚠️ Requiere nivel PRO o ULTRA.", "⚠️ Requires PRO or ULTRA tier."), show_alert=True)
                return
            current_cmd = await get_lock_status(group_id, "lock_commands")
            await set_lock_status(group_id, "lock_commands", 0 if current_cmd == 1 else 1)

        try:
            await callback.message.edit_reply_markup(reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang))
        except TelegramBadRequest:
            pass

    elif action == "toglock":
        lock_type = data[1]
        lock_key = f"lock_{lock_type}"
        current = await get_lock_status(group_id, lock_key)
        await set_lock_status(group_id, lock_key, 0 if current == 1 else 1)
        media_lbl = "Bloqueado 🟢" if lang == "es" else "Locked 🟢"
        media_free = "Permitido 🔴" if lang == "es" else "Allowed 🔴"
        media = media_lbl if await get_lock_status(group_id, "lock_media") == 1 else media_free
        stickers = media_lbl if await get_lock_status(group_id, "lock_stickers") == 1 else media_free
        links = media_lbl if await get_lock_status(group_id, "lock_links") == 1 else media_free
        commands = media_lbl if await get_lock_status(group_id, "lock_commands") == 1 else media_free

        try:
            await safe_edit_text(callback,
                t["locks_main_title"].format(media=media, stickers=stickers, links=links, commands=commands),
                reply_markup=await get_locks_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "warnset":
        sub = data[1]
        cfg = await get_warns_config(group_id)

        if sub == "text":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier not in ("pro", "ultra_pro"):
                await callback.answer("⭐ Requiere plan PRO o ULTRA PRO.", show_alert=True)
                return
            WARN_CUSTOM_TEXT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt_text = (
                "✍️ <b>Editor de Copy de Advertencia (PRO / ULTRA PRO):</b>\n\n"
                "Envía el mensaje que recibirá el infractor. Puedes usar variables:\n"
                "• <code>{mention}</code> - Mención del usuario\n"
                "• <code>{strikes}</code> - Número de falta actual\n"
                "• <code>{limit}</code> - Límite de faltas\n"
                "• <code>{reason}</code> - Motivo de la infracción\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "✍️ <b>Warning Message Editor (PRO / ULTRA PRO):</b>\n\n"
                "Send the warning template. Supported variables:\n"
                "• <code>{mention}</code>, <code>{strikes}</code>, <code>{limit}</code>, <code>{reason}</code>\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            prompt = await callback.message.answer(prompt_text, reply_markup=_cancel_kb(t, f"gset_warns_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

        elif sub == "media":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                await callback.answer("💎 Requiere plan ULTRA PRO.", show_alert=True)
                return
            WARN_CUSTOM_MEDIA_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt_text = (
                "🖼️ <b>Adjuntar Multimedia a Sanciones (ULTRA PRO):</b>\n\n"
                "Envía una Foto, Video o GIF que acompañará las advertencias y sanciones automáticas:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "🖼️ <b>Attach Warning Media (ULTRA PRO):</b>\n\n"
                "Send a Photo, Video or GIF to accompany warning alerts:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            prompt = await callback.message.answer(prompt_text, reply_markup=_cancel_kb(t, f"gset_warns_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

        elif sub == "delmedia":
            await set_warn_custom_field(group_id, "warn_custom_media_id", None)
            await set_warn_custom_field(group_id, "warn_custom_media_type", None)
            await callback.answer("🗑️ Multimedia retirada de las advertencias.", show_alert=True)
            cfg = await get_warns_config(group_id)
            await safe_edit_text(callback,
                t["warns_main_title"].format(limit=cfg["limit"], action=cfg["action"].upper()),
                reply_markup=await get_warns_keyboard(group_id, lang, user_id=callback.from_user.id),
                parse_mode="HTML"
            )
            return

        elif sub == "limit":
            limits = [3, 4, 5]
            curr_limit = cfg["limit"]
            new_limit = limits[(limits.index(curr_limit) + 1) % len(limits)] if curr_limit in limits else 3
            await set_warns_config(group_id, "warns_limit", new_limit)
        elif sub == "action":
            actions = ["mute", "kick", "ban"]
            curr = cfg["action"]
            next_act = actions[(actions.index(curr) + 1) % len(actions)] if curr in actions else "mute"
            await set_warns_config(group_id, "warns_action", next_act)
        elif sub == "toglink":
            current = cfg.get("warn_links", 1)
            await set_warns_config(group_id, "warn_links", 0 if current == 1 else 1)
        elif sub == "togblack":
            current = cfg.get("warn_blacklist", 1)
            await set_warns_config(group_id, "warn_blacklist", 0 if current == 1 else 1)
        elif sub == "togflood":
            current = cfg.get("warn_flood", 1)
            await set_warns_config(group_id, "warn_flood", 0 if current == 1 else 1)

        updated_cfg = await get_warns_config(group_id)
        try:
            await safe_edit_text(callback,
                t["warns_main_title"].format(limit=updated_cfg["limit"], action=updated_cfg["action"].upper()),
                reply_markup=await get_warns_keyboard(group_id, lang, user_id=callback.from_user.id),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "togcap" or action == "togmode":
        if action == "togcap":
            await set_captcha_status(group_id, 1 if data[1] == "on" else 0)
        else:
            await set_captcha_config(group_id, "captcha_mode", 1 if data[1] == "on" else 0)

        cfg = await get_captcha_config(group_id)
        st_text = "🟢" if cfg["status"] == 1 else "🔴"
        mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
        try:
            await safe_edit_text(callback,
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg["time"]), action_text=cfg["action"].upper()),
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "cap_set":
        sub = data[2]
        if sub == "time":
            prompt = "⏱️ <b>Configuración de Tiempo Límite</b>" if lang == "es" else "⏱️ <b>Time Limit Configuration</b>"
            await safe_edit_text(callback, prompt, reply_markup=get_captcha_time_keyboard(group_id, lang), parse_mode="HTML")
        elif sub == "action":
            cfg = await get_captcha_config(group_id)
            await safe_edit_text(callback,
                t["captcha_action_title"].format(mode_name=cfg["action"].upper()),
                reply_markup=await get_captcha_action_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif sub == "text":
            CAPTCHA_STATES[(bot.id, callback.from_user.id)] = group_id
            prompt_text = (
                "✍️ <b>Editor de Mensaje de Captcha:</b>\n\n"
                "Envía el texto que recibirá el usuario al ingresar a la comunidad:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "✍️ <b>Captcha Message Editor:</b>\n\n"
                "Send the message users will receive upon entry:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            prompt = await callback.message.answer(
                prompt_text,
                reply_markup=_cancel_kb(t, f"gset_captcha_{group_id}_{lang}"),
                parse_mode="HTML"
            )
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "srvdel":
            cfg = await get_captcha_config(group_id)
            await set_captcha_config(group_id, "captcha_service_del", 0 if cfg["service_del"] == 1 else 1)
            cfg_updated = await get_captcha_config(group_id)

            if callback.message.text and any(w in callback.message.text for w in ("Service", "Purga", "Purge", "Centro")):
                tier = await get_effective_group_tier(group_id, callback.from_user.id)
                quota_desc = (tr(lang, "3 purgas de servicio diarias (Plan Básico)", "3 daily service purges (Free Plan)") if tier == "free" else tr(lang, "Purga automatizada ilimitada (PRO / ULTRA)", "Unlimited automated purge (PRO / ULTRA)"))
                try:
                    await safe_edit_text(callback,
                        t["delmsgs_main_title"].format(tier_display=tier.upper(), quota_desc=quota_desc),
                        reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    pass
            else:
                st_text = "🟢" if cfg_updated["status"] == 1 else "🔴"
                mode_text = "🟢" if cfg_updated["mode"] == 1 else "🔴"
                try:
                    await safe_edit_text(callback,
                        t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg_updated["time"]), action_text=cfg_updated["action"].upper()),
                        reply_markup=await get_captcha_keyboard(group_id, lang),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    pass

    elif action == "capval":
        sub_type = data[1]
        val = data[2]
        if sub_type == "time":
            await set_captcha_config(group_id, "captcha_time", int(val))
        elif sub_type == "action":
            await set_captcha_config(group_id, "captcha_action", val)

        cfg = await get_captcha_config(group_id)
        st_text = "🟢" if cfg["status"] == 1 else "🔴"
        mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
        try:
            await safe_edit_text(callback,
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg["time"]), action_text=cfg["action"].upper()),
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "afset":
        sub_mode = data[1]
        prompt = f"<b>{t['af_msgs']}</b>\n{tr(lang, 'Selecciona el límite:', 'Select the limit:')}" + PERIMETER_SIGNATURE
        await safe_edit_text(callback, prompt, reply_markup=get_antiflood_number_keyboard(group_id, lang, sub_mode), parse_mode="HTML")

    elif action == "afval":
        sub_mode = data[1]
        val = int(data[2])
        await set_antiflood_config(group_id, "antiflood_msgs" if sub_mode == "msgs" else "antiflood_time", val)
        cfg = await get_antiflood_config(group_id)
        delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
        await safe_edit_text(callback,
            t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st),
            reply_markup=get_antiflood_keyboard(group_id, lang, cfg),
            parse_mode="HTML"
        )

    elif action == "afact":
        sub_act = data[1]
        if sub_act == "togdel":
            cfg = await get_antiflood_config(group_id)
            await set_antiflood_config(group_id, "antiflood_delete", 0 if cfg["delete"] == 1 else 1)
        else:
            await set_antiflood_config(group_id, "antiflood_action", sub_act)

        cfg = await get_antiflood_config(group_id)
        delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
        await safe_edit_text(callback,
            t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st),
            reply_markup=get_antiflood_keyboard(group_id, lang, cfg),
            parse_mode="HTML"
        )


# ==========================================
# 💎 ULTRA PRO — HERRAMIENTAS DE ÉLITE & CENTINELA DE IA
# ==========================================
def get_ai_sentinel_keyboard(group_id: int, lang: str, ai_cfg: dict, chat_type: str = "g"):
    """Build the AI Sentinel controls for the group's contextual panel."""
    t = TEXTS.get(lang, TEXTS["es"])
    guardian_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg.get("guardian_status") == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
    copilot_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg.get("copilot_status") == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
    back_data = f"cpanel_{group_id}_{lang}" if chat_type == "c" else f"gpanel_{group_id}_{lang}"
    back_text = t["btn_back_channel"] if chat_type == "c" else t["btn_back_group"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_ai_guardian"].format(guardian_st=guardian_st), callback_data=f"ai_toggle_guardian_{1 if ai_cfg.get('guardian_status') != 1 else 0}_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_ai_copilot"].format(copilot_st=copilot_st), callback_data=f"ai_toggle_copilot_{1 if ai_cfg.get('copilot_status') != 1 else 0}_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_ai_prompt"], callback_data=f"ai_prompt_{group_id}_{lang}")],
        [InlineKeyboardButton(text=back_text, callback_data=back_data)],
    ])


@router.callback_query(
    F.data.startswith("panic_") | F.data.startswith("shield_") |
    F.data.startswith("podcast_") | F.data.startswith("speakers_") |
    F.data.startswith("payload_") | F.data.startswith("ai_")
)
async def cb_ultra_tools_dispatch(callback: CallbackQuery, bot: Bot):
    clear_user_states(bot.id, callback.from_user.id)

    data = callback.data.split("_")
    module = data[0]
    sub = data[1]
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None

    if module == "ai":
        if sub == "menu":
            group_id = int(data[2])
        elif sub == "toggle":
            sub_target = data[2]
            new_status = int(data[3])
            group_id = int(data[4])
        elif sub == "prompt":
            group_id = int(data[2])

        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🤖 <b>Centinela de Inteligencia Artificial</b>", "🤖 <b>Artificial Intelligence Sentinel</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        else:
            if sub == "menu":
                ai_cfg = await get_ai_sentinel_config(group_id)
                g_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg["guardian_status"] == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
                c_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg["copilot_status"] == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
                prompt_prev = ai_cfg["custom_prompt"][:40] + "..." if len(ai_cfg["custom_prompt"]) > 40 else (ai_cfg["custom_prompt"] or (tr(lang, "Por defecto (Estándar)", "Default (Standard)")))
                text = t["ai_menu"].format(guardian_st=g_st, copilot_st=c_st, custom_prompt=prompt_prev) + PERIMETER_SIGNATURE
                keyboard = get_ai_sentinel_keyboard(group_id, lang, ai_cfg, chat_type=chat_kind)
            elif sub == "toggle":
                db_field = "ai_guardian_status" if sub_target == "guardian" else "ai_copilot_status"
                await set_ai_sentinel_config(group_id, db_field, new_status)
                await callback.answer(tr(lang, "⚙️ Ajustes de IA actualizados", "⚙️ AI settings updated"))
                ai_cfg = await get_ai_sentinel_config(group_id)
                g_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg["guardian_status"] == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
                c_st = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if ai_cfg["copilot_status"] == 1 else tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
                prompt_prev = ai_cfg["custom_prompt"][:40] + "..." if len(ai_cfg["custom_prompt"]) > 40 else (ai_cfg["custom_prompt"] or (tr(lang, "Por defecto (Estándar)", "Default (Standard)")))
                text = t["ai_menu"].format(guardian_st=g_st, copilot_st=c_st, custom_prompt=prompt_prev) + PERIMETER_SIGNATURE
                keyboard = get_ai_sentinel_keyboard(group_id, lang, ai_cfg, chat_type=chat_kind)
            elif sub == "prompt":
                AI_PROMPT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
                prompt = await callback.message.answer(t["ai_prompt_prompt"] + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"ai_menu_{group_id}_{lang}"), parse_mode="HTML")
                fire_and_forget_auto_delete([prompt], delay=60)
                return

    elif module == "panic":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🚨 <b>Botón de Pánico</b>" if lang == "es" else "🚨 <b>Panic Button</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            status = await get_panic_status(group_id)
            status_str = tr(lang, "🚨 BLOQUEADO", "🚨 LOCKED") if status == 1 else "🟢 Normal"
            text = t["panic_menu"].format(status_str=status_str) + PERIMETER_SIGNATURE
            keyboard = get_panic_keyboard(group_id, lang, status, chat_type=chat_kind)
        elif sub == "confirm":
            text = t["panic_confirm"] + PERIMETER_SIGNATURE
            keyboard = get_panic_confirm_keyboard(group_id, lang)
        elif sub == "activate":
            await set_panic_status(group_id, 1)
            try:
                await execute_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo lockdown en {group_id}: {ex}")
            text = t["panic_activated"] + PERIMETER_SIGNATURE
            keyboard = get_panic_keyboard(group_id, lang, 1, chat_type=chat_kind)
        elif sub == "deactivate":
            await set_panic_status(group_id, 0)
            try:
                await lift_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo levantar lockdown en {group_id}: {ex}")
            text = t["panic_deactivated"] + PERIMETER_SIGNATURE
            keyboard = get_panic_keyboard(group_id, lang, 0, chat_type=chat_kind)

    elif module == "shield":
        if sub == "toggle":
            new_status = int(data[2])
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🎥 <b>Escudo Antinota</b>" if lang == "es" else "🎥 <b>Screen-Share Shield</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            status = await get_shield_status(group_id)
            status_str = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if status == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["shield_menu"].format(status_str=status_str) + PERIMETER_SIGNATURE
            keyboard = get_shield_keyboard(group_id, lang, status, chat_type=chat_kind)
        elif sub == "toggle":
            await set_shield_status(group_id, new_status)
            try:
                if new_status == 1:
                    await engage_screen_shield(group_id)
                else:
                    await disengage_screen_shield(group_id)
            except Exception as ex:
                logging.error(f"❌ [Shield] Fallo al aplicar escudo en {group_id}: {ex}")
            await callback.answer(t["shield_updated_1"] if new_status == 1 else t["shield_updated_0"])
            status = await get_shield_status(group_id)
            status_str = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if status == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["shield_menu"].format(status_str=status_str) + PERIMETER_SIGNATURE
            keyboard = get_shield_keyboard(group_id, lang, status, chat_type=chat_kind)

    elif module == "podcast":
        if sub in ("toggle", "duckval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🎙️ <b>Modo Podcast & Audio Ducking</b>", "🎙️ <b>Podcast Mode & Audio Ducking</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            status = await get_podcast_status(group_id)
            duck_level = GROUP_DUCK_LEVEL.get(group_id, 20)
            status_str = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if status == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level) + PERIMETER_SIGNATURE
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
        elif sub == "toggle":
            new_status = int(val)
            await set_podcast_status(group_id, new_status)
            duck_level = GROUP_DUCK_LEVEL.get(group_id, 20)
            try:
                if new_status == 1:
                    await engage_podcast_ducking(group_id, duck_level)
                else:
                    await disengage_podcast_ducking(group_id)
            except Exception as ex:
                logging.error(f"❌ [Podcast] Fallo ducking en {group_id}: {ex}")
            await callback.answer(t["podcast_updated_1"] if new_status == 1 else t["podcast_updated_0"])
            status = await get_podcast_status(group_id)
            status_str = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if status == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level) + PERIMETER_SIGNATURE
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
        elif sub == "duckval":
            duck_level = int(val)
            GROUP_DUCK_LEVEL[group_id] = duck_level
            status = await get_podcast_status(group_id)
            if status == 1:
                try:
                    await disengage_podcast_ducking(group_id)
                    await engage_podcast_ducking(group_id, duck_level)
                except Exception as ex:
                    logging.error(f"❌ [Podcast] Fallo actualizacion ducking en {group_id}: {ex}")
            await callback.answer(t["duck_updated"].format(group_id=group_id, duck_level=duck_level), show_alert=True)
            status_str = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if status == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level) + PERIMETER_SIGNATURE
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
        elif sub == "duckset":
            PODCAST_DUCK_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["duck_custom_prompt"] + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"podcast_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif module == "speakers":
        if sub in ("toggle", "priceval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🌟 <b>Cola de Speakers Pagada</b>", "🌟 <b>Paid Speakers Queue</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = tr(lang, "🟢 ACTIVA", "🟢 ACTIVE")
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count) + PERIMETER_SIGNATURE
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)
        elif sub == "toggle":
            new_status = int(val)
            await callback.answer(t["speakers_updated_1"] if new_status == 1 else t["speakers_updated_0"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = tr(lang, "🟢 ACTIVADA", "🟢 ACTIVE") if new_status == 1 else tr(lang, "🔴 DESACTIVADA", "🔴 DISABLED")
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count) + PERIMETER_SIGNATURE
            keyboard = get_speakers_keyboard(group_id, lang, new_status, price, chat_type=chat_kind)
        elif sub == "priceval":
            price = int(val)
            GROUP_SPEAKER_PRICE[group_id] = price
            await callback.answer(t["speakers_price_updated"].format(group_id=group_id, price=price), show_alert=True)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = tr(lang, "🟢 ACTIVA", "🟢 ACTIVE")
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count) + PERIMETER_SIGNATURE
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)
        elif sub == "priceset":
            SPEAKER_PRICE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["speakers_price_prompt"] + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"speakers_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "clear":
            try:
                await clear_speaker_queue(group_id)
            except Exception as ex:
                logging.error(f"❌ [Speakers] Fallo vaciado cola en {group_id}: {ex}")
            await callback.answer(t["speakers_cleared"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            status_str = tr(lang, "🟢 ACTIVA", "🟢 ACTIVE")
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=0) + PERIMETER_SIGNATURE
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)

    if text and keyboard:
        try:
            await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    elif module == "payload":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "💎 <b>Payload Multimedia del Centinela</b>", "💎 <b>Sentinel Media Payload</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            cfg = await get_sentinel_payload_config(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if cfg.get("enabled") == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            has_text = "🟢" if cfg.get("text") else "🔴"
            has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
            autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get("auto_delete_after") else "Off"
            text = t["sentinel_payload_main"].format(st_badge=st_badge, has_text=has_text, has_media=has_media, autodel=autodel)
            keyboard = get_sentinel_payload_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "toggle":
            cfg = await get_sentinel_payload_config(group_id)
            new_st = 0 if cfg.get("enabled") == 1 else 1
            await set_sentinel_payload_config(group_id, "sentinel_payload_enabled", new_st)
            cfg = await get_sentinel_payload_config(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if cfg.get("enabled") == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            has_text = "🟢" if cfg.get("text") else "🔴"
            has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
            autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get('auto_delete_after') else "Off"
            text = t["sentinel_payload_main"].format(st_badge=st_badge, has_text=has_text, has_media=has_media, autodel=autodel)
            keyboard = get_sentinel_payload_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "text":
            SENTINEL_PAYLOAD_TEXT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_text"], reply_markup=_cancel_kb(t, f"payload_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "media":
            SENTINEL_PAYLOAD_MEDIA_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_media"], reply_markup=_cancel_kb(t, f"payload_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "autodel":
            SENTINEL_PAYLOAD_AUTODEL_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_del"], reply_markup=_cancel_kb(t, f"payload_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    if text and keyboard:
        try:
            await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
# ==========================================
# ⚙️ GESTIÓN BILINGÜE Y SIMÉTRICA DEL CENTINELA (SENTINEL SETTINGS)
# ==========================================
SENTINEL_CFG_STATES = {}

async def _render_sentinel_cfg_menu(bot: Bot, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_sentinel_service_messages_config(group_id)

    # 1. Estados de activación (1 = 🟢 Activado, 0 = 🔴 Desactivado / En edición)
    sw_vc = "🟢" if int(cfg.get("vc_enabled", 1) or 0) == 1 else "🔴"
    sw_mic = "🟢" if int(cfg.get("micvip_enabled", 1) or 0) == 1 else "🔴"
    sw_reset = "🟢" if int(cfg.get("reset_enabled", 1) or 0) == 1 else "🔴"
    sw_sched = "🟢" if int(cfg.get("sched_enabled", 1) or 0) == 1 else "🔴"
    sw_welcome = "🟢" if int(cfg.get("vc_welcome_enabled", 1) or 0) == 1 else "🔴"

    txt_active = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE")
    txt_disabled = tr(lang, "🔴 DESACTIVADO (EN EDICIÓN)", "🔴 DISABLED (EDITING)")

    st_desc_vc = txt_active if sw_vc == "🟢" else txt_disabled
    st_desc_mic = txt_active if sw_mic == "🟢" else txt_disabled
    st_desc_reset = txt_active if sw_reset == "🟢" else txt_disabled
    st_desc_sched = txt_active if sw_sched == "🟢" else txt_disabled
    st_desc_welcome = txt_active if sw_welcome == "🟢" else txt_disabled

    text = (
        f"⚙️ <b>{tr(lang, 'Configuración del Centinela (Sentinel Settings)', 'Sentinel Settings')}</b>\n\n"
        f"{tr(lang, 'Control de avisos y directivas de voz en vivo:', 'Live voice notices and service directives control:')}\n\n"
        f"• 🎙️ <b>{tr(lang, 'Entrada al VC', 'VC Join Notice')}:</b> {st_desc_vc}\n"
        f"• ⭐ <b>{tr(lang, 'Aviso MicVIP', 'MicVIP Notice')}:</b> {st_desc_mic}\n"
        f"• 🔄 <b>{tr(lang, 'Optimización (3.5h)', 'Optimization (3.5h)')}:</b> {st_desc_reset}\n"
        f"• 📡 <b>{tr(lang, 'Apertura Programada', 'Scheduled Opening')}:</b> {st_desc_sched}\n"
        f"• 📌 <b>{tr(lang, 'Bienvenida General VC', 'VC Welcome Notice')}:</b> {st_desc_welcome}\n\n"
        f"<i>{tr(lang, 'Toca el botón indicador para activar (🟢) o desactivar (🔴) cualquier servicio mientras lo editas:', 'Tap the indicator button to enable (🟢) or disable (🔴) any service while editing:')}</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        # 1. Entrada al VC
        [
            InlineKeyboardButton(text=f"{sw_vc} VC", callback_data=f"sentinelcfg_toggle_vc_{group_id}_{lang}"),
            InlineKeyboardButton(text="✍️", callback_data=f"sentinelcfg_edit_vc_msg_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️", callback_data=f"sentinelcfg_edit_vc_btn_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️", callback_data=f"sentinelcfg_edit_vc_autodel_{group_id}_{lang}"),
            InlineKeyboardButton(text="👁️", callback_data=f"sentinelcfg_view_vc_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️️", callback_data=f"sentinelcfg_default_vc_{group_id}_{lang}")
        ],
        # 2. MicVIP
        [
            InlineKeyboardButton(text=f"{sw_mic} MicVIP", callback_data=f"sentinelcfg_toggle_micvip_{group_id}_{lang}"),
            InlineKeyboardButton(text="✍️", callback_data=f"sentinelcfg_edit_micvip_msg_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️", callback_data=f"sentinelcfg_edit_micvip_btn_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️", callback_data=f"sentinelcfg_edit_micvip_autodel_{group_id}_{lang}"),
            InlineKeyboardButton(text="👁️", callback_data=f"sentinelcfg_view_micvip_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"sentinelcfg_default_micvip_{group_id}_{lang}")
        ],
        # 3. Optimización (Reset 3.5h)
        [
            InlineKeyboardButton(text=f"{sw_reset} Reset", callback_data=f"sentinelcfg_toggle_reset_{group_id}_{lang}"),
            InlineKeyboardButton(text="✍️", callback_data=f"sentinelcfg_edit_reset_msg_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️", callback_data=f"sentinelcfg_edit_reset_btn_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️", callback_data=f"sentinelcfg_edit_reset_autodel_{group_id}_{lang}"),
            InlineKeyboardButton(text="👁️", callback_data=f"sentinelcfg_view_reset_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"sentinelcfg_default_reset_{group_id}_{lang}")
        ],
        # 4. Apertura Programada
        [
            InlineKeyboardButton(text=f"{sw_sched} " + tr(lang, "Prog.", "Sched."), callback_data=f"sentinelcfg_toggle_sched_{group_id}_{lang}"),
            InlineKeyboardButton(text="✍️", callback_data=f"sentinelcfg_edit_sched_msg_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️", callback_data=f"sentinelcfg_edit_sched_autodel_{group_id}_{lang}"),
            InlineKeyboardButton(text="👁️", callback_data=f"sentinelcfg_view_sched_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"sentinelcfg_default_sched_{group_id}_{lang}")
        ],
        # 5. Bienvenida General VC
        [
            InlineKeyboardButton(text=f"{sw_welcome} Pinned", callback_data=f"sentinelcfg_toggle_vcwelcome_{group_id}_{lang}"),
            InlineKeyboardButton(text="✍️️", callback_data=f"sentinelcfg_edit_vcwelcome_msg_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️", callback_data=f"sentinelcfg_edit_vcwelcome_btn_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️", callback_data=f"sentinelcfg_edit_vcwelcome_autodel_{group_id}_{lang}"),
            InlineKeyboardButton(text="👁️", callback_data=f"sentinelcfg_view_vcwelcome_{group_id}_{lang}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"sentinelcfg_default_vcwelcome_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_ultra"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])
    return text, kb


@router.callback_query(F.data.startswith("sentinelcfg_"))
async def cb_sentinel_config_dispatch(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    sub = data[1]
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    try:
        if sub in ("view", "default", "toggle"):
            target_msg = data[2]
            group_id = int(data[3])
            field_name = None
        elif sub == "edit":
            target_msg = data[2]
            field_name = data[3]
            group_id = int(data[4])
        else:
            group_id = int(data[2])
            target_msg = field_name = None
    except (IndexError, ValueError):
        return

    if not await verify_admin_privileges(callback, bot, group_id):
        return

    clear_user_states(bot.id, callback.from_user.id)

    # 1. Menú principal
    if sub == "menu":
        text, kb = await _render_sentinel_cfg_menu(bot, group_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

    # 2. Interruptor ON / OFF dinámico (Verde 🟢 <-> Rojo 🔴)
    elif sub == "toggle":
        cfg = await get_sentinel_service_messages_config(group_id)
        prefix_sw = {
            "vc": ("vc_join_enabled", "vc_enabled"),
            "micvip": ("mic_vip_enabled", "micvip_enabled"),
            "reset": ("reset_notice_enabled", "reset_enabled"),
            "sched": ("vc_sched_start_enabled", "sched_enabled"),
            "vcwelcome": ("vc_welcome_enabled", "vc_welcome_enabled")
        }
        col_db, key_cfg = prefix_sw.get(target_msg, ("vc_join_enabled", "vc_enabled"))
        current_st = int(cfg.get(key_cfg, 1) or 0)
        new_st = 0 if current_st == 1 else 1

        await set_sentinel_service_message(group_id, col_db, new_st)

        msg_alert = (
            tr(lang, f"🟢 {target_msg.upper()}: Servicio activado.", f"🟢 {target_msg.upper()}: Service enabled.")
            if new_st == 1 else
            tr(lang, f"🔴 {target_msg.upper()}: Servicio desactivado (en edición).", f"🔴 {target_msg.upper()}: Service disabled (editing).")
        )
        await callback.answer(msg_alert)
        text, kb = await _render_sentinel_cfg_menu(bot, group_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")
        return

    # 3. Reset por defecto (🗑️)
    elif sub == "default":
        prefix_map = {
            "vc": "vc_join", "micvip": "mic_vip", "reset": "reset_notice",
            "sched": "vc_sched_start", "vcwelcome": "vc_welcome"
        }
        p = prefix_map.get(target_msg, "vc_join")
        col_prefix = f"{p}_custom" if target_msg in ("vc", "micvip", "reset") else p

        await set_sentinel_service_message(group_id, f"{col_prefix}_text", None)
        await set_sentinel_service_message(group_id, f"{col_prefix}_media_id", None)
        await set_sentinel_service_message(group_id, f"{col_prefix}_media_type", None)
        if target_msg in ("vc", "micvip", "reset", "vcwelcome"):
            await set_sentinel_service_message(group_id, f"{p}_btn_text", None)
            await set_sentinel_service_message(group_id, f"{p}_btn_url", None)  # 🧹 Limpieza de URL
        
        default_secs = 20 if target_msg == "reset" else (30 if target_msg in ("vc", "micvip") else 0)
        await set_sentinel_service_message(group_id, f"{p}_autodel_seconds", default_secs)
        await set_sentinel_service_message(group_id, f"{p}_enabled", 1)

        await callback.answer(tr(lang, f"🗑️ Módulo {target_msg.upper()} restablecido por defecto.", f"🗑️ Module {target_msg.upper()} restored to default."), show_alert=True)
        text, kb = await _render_sentinel_cfg_menu(bot, group_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

    # 4. Vista previa bilingüe (👁️)
    elif sub == "view":
        cfg = await get_sentinel_service_messages_config(group_id)
        if target_msg == "vc":
            content = cfg.get("vc_text") or tr(lang, "*(Usando plantilla estándar de entrada al VC)*", "*(Using default VC entry template)*")
            m_id, m_type = cfg.get("vc_media_id"), cfg.get("vc_media_type")
            btn = cfg.get("vc_btn") or tr(lang, "⭐ ACTIVAR MICVIP AHORA", "⭐ ACTIVATE MICVIP NOW")
            autodel = cfg.get("vc_autodel", 30)
            is_enabled = cfg.get("vc_enabled", 1) == 1
        elif target_msg == "micvip":
            content = cfg.get("micvip_text") or tr(lang, "*(Usando plantilla estándar del aviso MicVIP)*", "*(Using default MicVIP notice template)*")
            m_id, m_type = cfg.get("micvip_media_id"), cfg.get("micvip_media_type")
            btn = cfg.get("micvip_btn") or tr(lang, "⭐ ACTIVAR MICVIP AHORA", "⭐ ACTIVATE MICVIP NOW")
            autodel = cfg.get("micvip_autodel", 30)
            is_enabled = cfg.get("micvip_enabled", 1) == 1
        elif target_msg == "reset":
            content = cfg.get("reset_text") or tr(lang, "*(Usando plantilla estándar de optimización 3.5h)*", "*(Using default 3.5h optimization template)*")
            m_id, m_type = cfg.get("reset_media_id"), cfg.get("reset_media_type")
            btn = cfg.get("reset_btn") or tr(lang, "Sin botón", "No button")
            autodel = cfg.get("reset_autodel", 20)
            is_enabled = cfg.get("reset_enabled", 1) == 1
        elif target_msg == "sched":
            content = cfg.get("sched_start_text") or tr(lang, "*(Usando aviso estándar de apertura programada)*", "*(Using default scheduled start notice)*")
            m_id, m_type = cfg.get("sched_start_media_id"), cfg.get("sched_start_media_type")
            btn = tr(lang, "Sin botón", "No button")
            autodel = cfg.get("sched_start_autodel", 0)
            is_enabled = cfg.get("sched_enabled", 1) == 1
        else:
            content = cfg.get("vc_welcome_text") or tr(lang, "*(Usando aviso estándar fijado de bienvenida al VC)*", "*(Using default pinned VC welcome notice)*")
            m_id, m_type = cfg.get("vc_welcome_media_id"), cfg.get("vc_welcome_media_type")
            btn = cfg.get("vc_welcome_btn") or tr(lang, "⭐ ACTIVAR MICVIP AHORA", "⭐ ACTIVATE MICVIP NOW")
            autodel = cfg.get("vc_welcome_autodel", 0)
            is_enabled = cfg.get("vc_welcome_enabled", 1) == 1

        st_label = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE") if is_enabled else tr(lang, "🔴 APAGADO (EN EDICIÓN)", "🔴 MUTED (EDITING)")
        autodel_label = f"{autodel} " + tr(lang, "segundos", "seconds") if autodel > 0 else tr(lang, "Permanente (sin auto-borrado)", "Permanent (no auto-delete)")

        preview_text = (
            f"👁️ <b>{tr(lang, 'Vista Previa', 'Preview')} ({target_msg.upper()}):</b>\n\n"
            f"{content}\n\n"
            f"• <b>{tr(lang, 'Estado del Servicio', 'Service Status')}:</b> <code>{st_label}</code>\n"
            f"• <b>{tr(lang, 'Texto del Botón', 'Button Label')}:</b> <code>{btn}</code>\n"
            f"• <b>{tr(lang, 'Auto-borrado', 'Auto-delete')}:</b> <code>{autodel_label}</code>\n\n"
            f"🛡️️ <i>Cloud Media Management</i>"
        )
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=f"sentinelcfg_menu_{group_id}_{lang}")]
        ])
        try:
            await callback.message.delete()
        except Exception:
            pass

        if m_id and m_type == "photo":
            await bot.send_photo(chat_id=callback.from_user.id, photo=m_id, caption=preview_text, reply_markup=back_kb, parse_mode="HTML")
        elif m_id and m_type == "video":
            await bot.send_video(chat_id=callback.from_user.id, video=m_id, caption=preview_text, reply_markup=back_kb, parse_mode="HTML")
        elif m_id and m_type == "animation":
            await bot.send_animation(chat_id=callback.from_user.id, animation=m_id, caption=preview_text, reply_markup=back_kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=callback.from_user.id, text=preview_text, reply_markup=back_kb, parse_mode="HTML")

    # 5. Edición de campos (Bilingüe)
    elif sub == "edit":
        tier = (await get_effective_group_tier(group_id, callback.from_user.id) or "free").lower()
        if tier == "free":
            await callback.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires PRO or ULTRA PRO plan."), show_alert=True)
            return

        SENTINEL_CFG_STATES[(bot.id, callback.from_user.id)] = {
            "group_id": group_id, 
            "target": target_msg, 
            "field": field_name, 
            "lang": lang
        }

        if field_name == "autodel":
            prompt_txt = (
                f"⏱️ <b>{tr(lang, 'Tiempo de Auto-Borrado', 'Auto-Delete Timer')} ({target_msg.upper()}):</b>\n\n"
                f"{tr(lang, 'Envía un número entero en segundos (0 para no borrar automáticamente):', 'Send an integer in seconds (0 to keep permanently):')}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
        elif field_name == "btn":
            prompt_txt = (
                f"🏷️ <b>{tr(lang, 'Nombre del Botón y Enlace', 'Button Label and URL')} ({target_msg.upper()}):</b>\n\n"
                f"{tr(lang, 'Envía el texto del botón, o usa la sintaxis:', 'Send the button text, or use the syntax:')}\n"
                f"<code>Texto del Botón | https://t.me/tu_enlace</code>\n\n"
                f"{tr(lang, 'El texto cubrirá el enlace al 100% sin exponer URLs en el mensaje.', 'The button text will seamlessly embed the link without exposing URLs.')}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
        else:
            ultra_note = tr(lang, "<i>(Nivel ULTRA PRO: puedes adjuntar Foto, Video o GIF junto al mensaje)</i>", "<i>(ULTRA PRO tier: you may attach Photo, Video or GIF with your message)</i>")
            prompt_txt = (
                f"✍️ <b>{tr(lang, 'Editor de Mensaje', 'Notice Editor')} ({target_msg.upper()}):</b>\n\n"
                f"{tr(lang, 'Envía el texto del aviso.', 'Send the notice template.')} "
                f"{ultra_note if tier == 'ultra_pro' else ''}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )

        prompt = await callback.message.answer(prompt_txt, reply_markup=_cancel_kb(t, f"sentinelcfg_menu_{group_id}_{lang}"), parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)

    # ==========================================
# 📢 FASE 6: DISPATCHER DE PLANES DE CANAL (CONTROL COMERCIAL & VISTA PREVIA)
# ==========================================
async def _render_plans_menu(bot: Bot, channel_id: int, lang: str):
    """Construye el panel de planes con botoneras en cascada: [Ver] + [🟢/🔴] [📢] [🗑️]."""
    t = TEXTS.get(lang, TEXTS["es"])
    # Trae todos los planes (activos y pausados) para administración total
    plans = await get_channel_plans(channel_id, only_active=False)
    sub_count = await get_active_subscribers_count(channel_id)

    try:
        c_name = html.escape((await bot.get_chat(channel_id)).title or "")
    except Exception:
        c_name = tr(lang, "Canal", "Channel")

    kb_rows = []
    if plans:
        for p in plans:
            p_id, p_name, p_days, p_stars, p_status = p[0], p[1], p[2], p[3], p[4]
            is_active = (p_status == "active")
            st_icon = "🟢" if is_active else "🔴"
            st_text = tr(lang, "Activo", "Active") if is_active else tr(lang, "Pausado", "Paused")

            # 1. Botón principal: abre la previsualización oficial
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"💎 {p_name} — {p_days}d ({p_stars} ⭐)",
                    callback_data=f"chplans_view_{p_id}_{channel_id}_{lang}"
                )
            ])
            # 2. Fila de controles operativos: [Estado] [Difundir] [Borrar]
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"{st_icon} {st_text}",
                    callback_data=f"chplans_toggle_{p_id}_{channel_id}_{lang}"
                ),
                InlineKeyboardButton(
                    text="📢 " + tr(lang, "Difundir", "Broadcast"),
                    callback_data=f"chplans_share_{p_id}_{channel_id}_{lang}"
                ),
                InlineKeyboardButton(
                    text="🗑️",
                    callback_data=f"chplans_del_{p_id}_{channel_id}_{lang}"
                ),
            ])
    else:
        empty_hint = "<i>(No hay planes configurados aún. Toca «Crear Nuevo Plan»)</i>" if lang == "es" else "<i>(No plans configured yet. Tap «Create New Plan»)</i>"
        kb_rows.append([InlineKeyboardButton(text="ℹ️ " + tr(lang, "Sin planes activos", "No plans active"), callback_data="noop")])

    text = (
        f"💎 <b>Gestión de Membresías — {c_name}</b>\n\n"
        f"• <b>Suscriptores Activos:</b> <code>{sub_count}</code>\n"
        f"• <b>Total de Planes:</b> <code>{len(plans)}</code>\n\n"
        f"<i>Toca el nombre del plan para ver su vista previa comercial, "
        f"o usa los botones inferiores para activarlo/pausarlo, difundirlo o eliminarlo.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        f"💎 <b>Membership Management — {c_name}</b>\n\n"
        f"• <b>Active Subscribers:</b> <code>{sub_count}</code>\n"
        f"• <b>Total Plans:</b> <code>{len(plans)}</code>\n\n"
        f"<i>Tap the plan name to preview its commercial layout, "
        f"or use the controls below to activate/pause, broadcast, or delete.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    kb_rows.append([InlineKeyboardButton(text=t["btn_create_plan"], callback_data=f"chplans_add_{channel_id}_{lang}")])
    kb_rows.append([InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.callback_query(F.data.startswith("chplans_"))
async def cb_channel_plans_dispatch(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    try:
        sub = data[1]
        if sub in ("del", "toggle", "view", "share", "postto"):
            plan_id = int(data[2])
            channel_id = int(data[3])
            target_chat_id = int(data[4]) if sub == "postto" else None
        else:
            channel_id = int(data[2])
            plan_id = None
    except (IndexError, ValueError):
        return

    if not await verify_admin_privileges(callback, bot, channel_id):
        return

    CHAT_KIND_CACHE[channel_id] = "c"

    if sub not in ("skip", "view", "share", "postto"):
        clear_user_states(bot.id, callback.from_user.id)

    # 1. Menú principal de planes
    if sub == "menu":
        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

    # 2. Activar / pausar plan de membresía
    elif sub == "toggle":
        plan = await get_channel_plan(plan_id)
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
            return

        new_status = await toggle_channel_plan_status(plan_id)
        status_label = tr(lang, "🟢 Plan activado.", "🟢 Plan activated.") if new_status == "active" else tr(lang, "🔴 Plan pausado.", "🔴 Plan paused.")
        await callback.answer(status_label)

        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")
        return

    # 3. Vista previa comercial limpia (el enlace VIP va sólo en botón inline)
    elif sub == "view":
        plan = await get_channel_plan(plan_id)
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️️ Plan not found."), show_alert=True)
            return

        bot_info = await bot.get_me()
        bot_user = bot_info.username or "TheBunkerBot"
        buy_url = f"https://t.me/{bot_user}?start=chanplan_{plan['plan_id']}_{channel_id}"

        # Cuerpo del copy formateado (sin links en texto plano)
        caption_body = (
            f"💎 <b>{html.escape(plan['plan_name'])}</b>\n\n"
            f"⏳ <b>Duración:</b> {plan['duration_days']} " + tr(lang, "días", "days") + "\n"
            f"⭐ <b>Precio:</b> {plan['stars_price']} XTR (Stars)\n\n"
            f"{plan.get('promo_text') or ''}"
        ) + PERIMETER_SIGNATURE

        inline_rows = [
            [InlineKeyboardButton(text=f"⭐ Suscribirme ({plan['stars_price']} Stars)" if lang == "es" else f"⭐ Subscribe ({plan['stars_price']} Stars)", url=buy_url)]
        ]
        if plan.get("target_link"):
            clean_link = plan["target_link"] if plan["target_link"].startswith("http") else f"https://t.me/{plan['target_link'].lstrip('@')}"
            inline_rows.append([InlineKeyboardButton(text="🔗 " + tr(lang, "Acceder al Recurso VIP", "Access VIP Resource"), url=clean_link)])

        inline_rows.append([InlineKeyboardButton(text="🔙 " + tr(lang, "Volver a Planes", "Back to Plans"), callback_data=f"chplans_menu_{channel_id}_{lang}")])
        preview_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)

        # Enviar con media o texto según corresponda
        m_id, m_type = plan.get("media_id"), plan.get("media_type")
        try:
            await callback.message.delete()
        except Exception:
            pass

        if m_id and m_type == "photo":
            await bot.send_photo(chat_id=callback.from_user.id, photo=m_id, caption=caption_body, reply_markup=preview_kb, parse_mode="HTML")
        elif m_id and m_type == "video":
            await bot.send_video(chat_id=callback.from_user.id, video=m_id, caption=caption_body, reply_markup=preview_kb, parse_mode="HTML")
        elif m_id and m_type == "animation":
            await bot.send_animation(chat_id=callback.from_user.id, animation=m_id, caption=caption_body, reply_markup=preview_kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=callback.from_user.id, text=caption_body, reply_markup=preview_kb, parse_mode="HTML")

    # 4. Menú para compartir / difundir en canales o grupos administrados
    elif sub == "share":
        plan = await get_channel_plan(plan_id)
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
            return

        groups = await get_active_user_groups(bot, callback.from_user.id)
        channels = await get_active_user_channels(bot, callback.from_user.id)

        share_rows = []
        for g_id, g_name in groups:
            share_rows.append([InlineKeyboardButton(text=f"👥 {g_name[:24]}", callback_data=f"chplans_postto_{plan_id}_{channel_id}_{g_id}_{lang}")])
        for c_id, c_name in channels:
            if c_id != channel_id:  # no mostrar el mismo canal de origen
                share_rows.append([InlineKeyboardButton(text=f"📢 {c_name[:24]}", callback_data=f"chplans_postto_{plan_id}_{channel_id}_{c_id}_{lang}")])

        share_rows.append([InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=f"chplans_menu_{channel_id}_{lang}")])
        share_kb = InlineKeyboardMarkup(inline_keyboard=share_rows)

        share_text = (
            f"📢 <b>Difundir Membresía — {html.escape(plan['plan_name'])}</b>\n\n"
            f"Selecciona la comunidad o canal administrado donde deseas publicar el anuncio comercial con botón directo de pago en Stars:\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            f"📢 <b>Broadcast Membership — {html.escape(plan['plan_name'])}</b>\n\n"
            f"Select the managed group or channel where you want to post this announcement with direct Stars checkout:\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )
        await safe_edit_text(callback, share_text, reply_markup=share_kb, parse_mode="HTML")

    # 5. Ejecutar la publicación en el destino seleccionado
    elif sub == "postto":
        plan = await get_channel_plan(plan_id)
        if not plan or not target_chat_id:
            await callback.answer(tr(lang, "⚠️ Error en la publicación.", "⚠️ Broadcast error."), show_alert=True)
            return

        bot_info = await bot.get_me()
        bot_user = bot_info.username or "TheBunkerBot"
        buy_url = f"https://t.me/{bot_user}?start=chanplan_{plan['plan_id']}_{channel_id}"

        post_body = (
            f"💎 <b>{html.escape(plan['plan_name'])}</b>\n\n"
            f"⏳ <b>Duración:</b> {plan['duration_days']} " + tr(lang, "días", "days") + "\n"
            f"⭐ <b>Precio:</b> {plan['stars_price']} XTR\n\n"
            f"{plan.get('promo_text') or ''}"
        ) + PERIMETER_SIGNATURE

        post_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Obtener Acceso ({plan['stars_price']} Stars)" if lang == "es" else f"⭐ Get Access ({plan['stars_price']} Stars)", url=buy_url)]
        ])

        m_id, m_type = plan.get("media_id"), plan.get("media_type")
        try:
            if m_id and m_type == "photo":
                await bot.send_photo(chat_id=target_chat_id, photo=m_id, caption=post_body, reply_markup=post_kb, parse_mode="HTML")
            elif m_id and m_type == "video":
                await bot.send_video(chat_id=target_chat_id, video=m_id, caption=post_body, reply_markup=post_kb, parse_mode="HTML")
            elif m_id and m_type == "animation":
                await bot.send_animation(chat_id=target_chat_id, animation=m_id, caption=post_body, reply_markup=post_kb, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=target_chat_id, text=post_body, reply_markup=post_kb, parse_mode="HTML")

            await callback.answer(tr(lang, "✅ ¡Anuncio publicado con éxito!", "✅ Announcement published successfully!"), show_alert=True)
        except Exception as post_err:
            logging.error(f"❌ [Channel Plans Share] Error publicando en {target_chat_id}: {post_err}")
            await callback.answer(tr(lang, "⚠️ No se pudo publicar. Verifica permisos del bot.", "⚠️ Broadcast failed. Check bot permissions."), show_alert=True)

        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

    # 6. Crear nuevo plan
    elif sub == "add":
        tier = await get_effective_group_tier(channel_id, callback.from_user.id)
        limit = CHAN_PLAN_TIER_LIMITS.get(tier, CHAN_PLAN_TIER_LIMITS["free"])
        active_plans = await get_channel_plans(channel_id, only_active=True)

        if len(active_plans) >= limit:
            limit_text = (
                f"🔒 <b>Límite de Planes Alcanzado</b>\n\n"
                f"Tu licencia actual (<b>{tier.upper()}</b>) permite un máximo de <b>{limit}</b> plan(es) de membresía activos, "
                f"y ya tienes <b>{len(active_plans)}</b> configurado(s).\n\n"
                f"Elimina o pausa un plan existente para desbloquear más cupos.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                f"🔒 <b>Plan Limit Reached</b>\n\n"
                f"Your current license (<b>{tier.upper()}</b>) allows a maximum of <b>{limit}</b> active membership plan(s), "
                f"and you already have <b>{len(active_plans)}</b> configured.\n\n"
                f"Delete or pause an existing plan to unlock more slots.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            limit_kb_rows = []
            if tier == "free":
                up_label = "⭐ Mejorar a PRO (3 Planes)" if lang == "es" else "⭐ Upgrade to PRO (3 Plans)"
                limit_kb_rows.append([InlineKeyboardButton(text=up_label, callback_data=f"pay_pro_{channel_id}_{lang}")])
            if tier in ("free", "pro"):
                up_label_ultra = "💎 Mejorar a ULTRA PRO (10 Planes)" if lang == "es" else "💎 Upgrade to ULTRA PRO (10 Plans)"
                limit_kb_rows.append([InlineKeyboardButton(text=up_label_ultra, callback_data=f"pay_ultra_{channel_id}_{lang}")])
            limit_kb_rows.append([InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"chplans_menu_{channel_id}_{lang}")])

            await safe_edit_text(callback, limit_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=limit_kb_rows), parse_mode="HTML")
            return

        CHAN_PLAN_STATES[(bot.id, callback.from_user.id)] = {
            "channel_id": channel_id, "step": "name", "lang": lang, "ts": time.time()
        }
        await persist_plan_state((bot.id, callback.from_user.id))
        prompt_name = (
            "✍️ <b>Nombre del nuevo plan:</b>\n\n"
            "Envía en este chat el nombre comercial de la suscripción (ejemplo: <code>Pase Mensual VIP</code>):"
        ) if lang == "es" else (
            "✍️ <b>New plan name:</b>\n\nSend the plan title (e.g. <code>Monthly VIP Pass</code>):"
        )
        prompt = await callback.message.answer(
            prompt_name + PERIMETER_SIGNATURE,
            reply_markup=_plan_step_keyboard(t, channel_id, lang), parse_mode="HTML"
        )
        fire_and_forget_auto_delete([prompt], delay=60)

    # 7. Omitir multimedia durante creación
    elif sub == "skip":
        skip_key = (bot.id, callback.from_user.id)
        restore_state = globals().get("restore_plan_state")
        if restore_state is not None:
            await restore_state(skip_key)
        st_data = CHAN_PLAN_STATES.get(skip_key)
        if (not st_data or st_data.get("channel_id") != channel_id or st_data.get("step") != "media"
                or any(st_data.get(k) in (None, "") for k in ("name", "days", "price", "promo"))):
            forget_plan_state(*skip_key)
            info_text = (
                "ℹ️ No hay una creación de plan en curso para omitir (el servidor pudo haberse reiniciado)."
                if lang == "es" else
                "ℹ️️ There's no plan creation in progress to skip (the server may have restarted)."
            ) + PERIMETER_SIGNATURE
            info_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_create_plan"], callback_data=f"chplans_add_{channel_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
            ])
            await safe_edit_text(callback, info_text, reply_markup=info_kb, parse_mode="HTML")
            return

        st_data["media_id"] = None
        st_data["media_type"] = None
        st_data["step"] = "link"
        await persist_plan_state(skip_key)

        try:
            await callback.message.delete()
        except Exception:
            pass

        t_dict = TEXTS.get(lang, TEXTS["es"])
        prompt_link = (
            "🔗 <b>Enlace de Destino VIP (Entrega Automática):</b>\n\n"
            "Envía el enlace de invitación o recurso exclusivo que el bot entregará de forma automática al usuario tras confirmar su pago con Stars:"
        ) if lang == "es" else (
            "🔗 <b>VIP Target Link (Automatic Delivery):</b>\n\n"
            "Send the invite link or exclusive resource the bot will automatically deliver upon confirming payment:"
        )
        plan_kb = _plan_step_keyboard(t_dict, channel_id, lang)
        resp = await bot.send_message(
            chat_id=callback.from_user.id,
            text=prompt_link + PERIMETER_SIGNATURE,
            reply_markup=plan_kb,
            parse_mode="HTML"
        )
        fire_and_forget_auto_delete([resp], delay=60)
        return

    # 8. Eliminar plan
    elif sub == "del":
        plans = await get_channel_plans(channel_id, only_active=False)
        if plan_id in {p[0] for p in plans}:
            await delete_channel_plan(plan_id)
            await callback.answer(tr(lang, "🗑️ Plan eliminado.", "🗑️ Plan deleted."))

        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

        # ==========================================
# 💖 AGRADECIMIENTO OFICIAL DE PROPINAS STARS
# ==========================================
async def send_tip_thanks(bot: Bot, user_id: int, stars: int, group_id: int, lang: str = "es") -> None:
    """Despacha un mensaje de gratitud oficial del Búnker en privado al donante de Stars."""
    t = TEXTS.get(lang, TEXTS["es"])
    thanks_text = (
        f"🌟 <b>¡Muchas gracias por tu contribución!</b>\n\n"
        f"Tu aporte voluntario de <b>{stars} Telegram Stars (XTR)</b> ha sido recibido y acreditado con éxito en la tesorería de la comunidad.\n\n"
        f"Tu apoyo impulsa la infraestructura del Búnker y el mantenimiento de nuestras transmisiones y herramientas de élite.\n\n"
        f"🛡️️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        f"🌟 <b>Thank you so much for your contribution!</b>\n\n"
        f"Your voluntary contribution of <b>{stars} Telegram Stars (XTR)</b> has been successfully received and credited to the community treasury.\n\n"
        f"Your support powers The Bunker's infrastructure, streaming servers, and elite toolset.\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    rows = []
    try:
        chat_info = await bot.get_chat(group_id)
        if chat_info and getattr(chat_info, "username", None):
            rows.append([InlineKeyboardButton(text=t["btn_back_group"], url=f"https://t.me/{chat_info.username}")])
    except Exception:
        pass
    rows.append([InlineKeyboardButton(text=t["btn_saas"], web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={group_id}"))])

    try:
        await bot.send_message(
            chat_id=user_id,
            text=thanks_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    except Exception as ex:
        logging.debug(f"Aviso al enviar agradecimiento de propina a {user_id}: {ex}")