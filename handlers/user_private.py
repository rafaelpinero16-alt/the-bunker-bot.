"""
user_private.py — Consola privada de The Bunker OS (Aiogram 3.x) · Cloud Media Management.

Revisión de resiliencia y blindaje:
  1. Estados conversacionales seguros ante reinicios de RAM (TTL, liberación en bloque, fallback anti-bloqueo, /cancel).
  2. Sincronización Activa de Canales estilo GroupHelp (verificación en vivo + registro autorreparable + selector nativo).
  3. Navegación contextual estricta: canal → cpanel | grupo → gpanel, también en el checkout y herramientas ULTRA.
  4. Identidad visual: botones inline, soporte bilingüe ES/EN y firma perimetral "Cloud Media Management".
  5. Fase 3 · Exclusividad financiera: PRO, ULTRA PRO, MicVIP y membresías se adquieren solo con
     Telegram Stars (XTR) in-app; sin pasarelas externas ni precios fiat (Telegram, Apple 3.1.1, Google Play).
"""
from typing import Optional, cast
import os
import sys
import re
import time
import html
import importlib
import importlib.util
import json
import asyncio
import logging
import contextlib
from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ChatPermissions,
    ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat,
    ReplyKeyboardRemove, FSInputFile, InputMediaPhoto, BufferedInputFile
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramMigrateToChat
from database.database import (
    get_gamification_announcement, set_gamification_announcement_field,
    set_gamification_announcement_media, clear_gamification_announcement_button,
    schedule_gamification_announcement_now, reset_gamification_announcement,
    normalize_announcement_url, normalize_announcement_button_text, compose_gamification_card,
    GAMI_DEFAULT_BUTTON_TEXTS, GAMI_DEFAULT_INTERVAL_MINUTES, GAMI_TEXT_MAX_CHARS,
)
from database.database import (
    has_delegate_permission, get_delegated_chats,   # 👑 v13 · Matriz del Arquitecto (delegados)
)
from database.database import (
    toggle_sentinel_service_flag, get_settings_snapshot,   # 🎙️ v14 · Centinela atómico + telemetría en caliente
)

try:
    from radar_bus import publish_radar_event  # type: ignore[import-not-found]
except ImportError:  # radar_bus es opcional: sin él no hay oyentes de la Mini App
    def publish_radar_event(*args, **kwargs):
        return None
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
    export_group_configuration,
    import_group_configuration,
    get_top_reputation,
    get_user_reputation,
    get_reputation_settings,
    set_reputation_setting,
)

from assistant import (
    register_or_update_sentinel, disconnect_sentinel,
    start_phone_auth, verify_phone_code, verify_2fa_password, cancel_phone_auth,
    engage_screen_shield, disengage_screen_shield,
    engage_podcast_ducking, disengage_podcast_ducking
)
import perimeter_ops   # 🛰️ v20 · lógica compartida con la Mini App (purga v2, canal de registro, IA, soporte IA)
import micvip_audit    # 🎟️ v22 · auditoría MicVIP compartida con la Mini App
import vip_roles       # 👑 v23 · roles y etiquetas VIP automáticos (compartido con la Mini App)
import link_quotas     # 🔗 v23 · cuota diaria de enlaces por administrador (compartido con la Mini App)
from handlers.groups import (
    execute_raid_lockdown, lift_raid_lockdown,
    clear_speaker_queue, get_speaker_queue
)

# 💳 Fase 3 · Exclusividad financiera: los planes se cobran SOLO en Telegram Stars (XTR).
# Los precios provienen de handlers/payments.py (única fuente de verdad); payments.py no
# importa este módulo en tiempo de carga, por lo que no hay importación circular.
try:
    from handlers.payments import PRICE_PRO_STARS, PRICE_ULTRAPRO_STARS, invalidate_channel_plan_cache
except Exception:  # pragma: no cover - respaldo si payments no está disponible
    PRICE_PRO_STARS, PRICE_ULTRAPRO_STARS = 300, 600

    def invalidate_channel_plan_cache(plan_id=None) -> None:
        return None

# Las tarjetas HUD de las vistas de cobro son imágenes; si su arte original mostraba
# pasarelas externas, desactívalas con PAYMENT_HUD_CARDS=0 hasta reemplazar los assets.
PAYMENT_HUD_CARDS = os.getenv("PAYMENT_HUD_CARDS", "1").strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def plan_price_stars(tier_level: str) -> int:
    return PRICE_ULTRAPRO_STARS if tier_level == "ultra" else PRICE_PRO_STARS


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


_CALLBACK_CHAT_ID_RE = re.compile(r"^-\d{5,20}$")


class OwnerCallbackGuardMiddleware(BaseMiddleware):
    """
    👑 v12/v13 · Defensa en profundidad de la Regla de Oro. Antes de ejecutar CUALQUIER botón de esta consola,
    toma cada ID de chat presente en el callback_data (los IDs de grupos/canales siempre son negativos) y exige
    que quien pulsa sea su dueño legítimo (creador en vivo), un Arquitecto o un DELEGADO designado por el
    Arquitecto con el permiso exacto que ese botón requiere (Matriz del Arquitecto). Un administrador común recibe
    owner_only_alert y el handler ni siquiera se ejecuta; así ningún botón nuevo puede olvidar la verificación.
    """
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        raw = getattr(event, "data", None) or ""
        user = getattr(event, "from_user", None)
        bot = data.get("bot")
        if raw and user and bot is not None and not is_super_admin(user.id):
            chat_ids = {int(tok) for tok in raw.split("_") if _CALLBACK_CHAT_ID_RE.match(tok)}
            for chat_id in chat_ids:
                if not await is_authorized_for_callback(bot, user.id, chat_id, raw):
                    lang = user_lang(user)
                    logging.warning(f"⛔ [Aduana] Botón '{raw[:64]}' bloqueado: user={user.id} no es dueño ni delegado autorizado de {chat_id}.")
                    clear_user_states(bot.id, user.id)
                    try:
                        await event.answer(TEXTS.get(lang, TEXTS[DEFAULT_LANG])["owner_only_alert"], show_alert=True)
                    except Exception:
                        pass
                    return None
        return await handler(event, data)


router.callback_query.middleware(OwnerCallbackGuardMiddleware())

ADMIN_GROUP_ID = -1004351489258
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://the-bunker-bot-bunkerminiapp.vercel.app/").strip()

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
    # 19 y 20: Checkout Plan PRO (solo Telegram Stars)
    "pay_pro":           {"es": "pro.es.jfif",                "en": "pro.en.jfif"},
    # 21 y 22: Checkout Plan ULTRA PRO (solo Telegram Stars)
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
                      caption: Optional[str] = None, old_message=None) -> bool:
    """
    Renderiza la tarjeta visual HUD asociada a `card_key` en el idioma `lang`:
    - Si el mensaje anterior ya contiene foto, realiza un `edit_media` fluido sin parpadeos.
    - Si el mensaje anterior era de texto, despacha la foto y elimina el mensaje previo.
    - Si la imagen no está en disco o falla Telegram, devuelve False para ejecutar el fallback de texto.
    """
    names = CARD_IMAGES.get(card_key) or {}
    filename = names.get(lang) or names.get("es")
    path = _find_asset(filename) if filename else None
    if not path or not filename:
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


async def send_card_message(bot: Bot, chat_id: int, card_key: str, lang: str, keyboard=None, caption: Optional[str] = None):
    """Despacha una tarjeta gráfica como mensaje nuevo e independiente (Message)."""
    names = CARD_IMAGES.get(card_key) or {}
    filename = names.get(lang) or names.get("es")
    path = _find_asset(filename) if filename else None
    if not path or not filename or (caption and len(caption) > 1024):
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
TIPS_THANKS_STATES = {}   # 💌 Edición del agradecimiento de propinas (PRO / ULTRA PRO)
SENTINEL_PAYLOAD_TEXT_STATES = {}
SENTINEL_PAYLOAD_MEDIA_STATES = {}
SENTINEL_PAYLOAD_AUTODEL_STATES = {}
CHAN_PLAN_STATES = {}
AI_PROMPT_STATES = {}
SENTINEL_CFG_STATES = {}
WARN_CUSTOM_TEXT_STATES = {}
WARN_CUSTOM_MEDIA_STATES = {}
MIC_VIP_TEXT_STATES = {}
BACKUP_IMPORT_STATES = {}
REP_MULTIPLIER_STATES = {}
GAMI_STATES = {}          # 🎮 Edición del anuncio recurrente de gamificación
LOG_CHANNEL_STATES = {}   # 🧾 v20 · Vinculación del canal de registro desde el bot
SUPPORT_AI_STATES = {}    # 🤖 v20 · Conversación con el Asistente de Soporte IA
VIP_ROLE_STATES = {}      # 👑 v23 · Etiqueta VIP y concesión manual de roles desde el bot
APPEAL_PRICE_STATES = {}  # ⚖️ v24 · Precio personalizado de la apelación de baneos

# 🧯 Registro central de TODOS los estados conversacionales: permite liberarlos en bloque (navegación, /start,
# /cancel) y garantiza que ningún menú privado quede bloqueado por una conversación huérfana.
ALL_STATE_DICTS = (
    CAPTCHA_STATES, CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, SENTINEL_2FA_STATES,
    VC_SCHED_STATES, NIGHT_STATES, DB_REG_STATES, MOD_TARGET_STATES, MIC_VIP_STATES, MIC_TAG_STATES,
    PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES, TIPS_AMOUNT_STATES, TIPS_TARGET_STATES, TIPS_TEXT_STATES,
    TIPS_MEDIA_STATES, TIPS_THANKS_STATES,
    SENTINEL_PAYLOAD_TEXT_STATES, SENTINEL_PAYLOAD_MEDIA_STATES, SENTINEL_PAYLOAD_AUTODEL_STATES,
    CHAN_PLAN_STATES, AI_PROMPT_STATES, WARN_CUSTOM_TEXT_STATES, WARN_CUSTOM_MEDIA_STATES, MIC_VIP_TEXT_STATES,
    SENTINEL_CFG_STATES, BACKUP_IMPORT_STATES, REP_MULTIPLIER_STATES, GAMI_STATES,
    LOG_CHANNEL_STATES, SUPPORT_AI_STATES, VIP_ROLE_STATES, APPEAL_PRICE_STATES
)



def _ban_appeals_module():
    """
    v26 · Módulo de apelaciones: ubicación oficial handlers/ban_appeals.py; se tolera ban_appeals.py en la raíz.
    find_spec decide la ruta ANTES de importar: un ImportError real dentro del módulo nunca queda enmascarado.
    """
    name = "handlers.ban_appeals" if importlib.util.find_spec("handlers.ban_appeals") is not None else "ban_appeals"
    return importlib.import_module(name)


def register_external_state_store(store: dict) -> None:
    """
    👑 v13 · Permite que otros módulos (p. ej. la Matriz del Arquitecto) registren su almacén de estados para que
    /start, /cancel y la navegación de esta consola también los liberen. Comparación por identidad, no por valor.
    """
    global ALL_STATE_DICTS
    if not any(existing is store for existing in ALL_STATE_DICTS):
        ALL_STATE_DICTS = ALL_STATE_DICTS + (store,)


_SETTINGS_EMIT_TASKS: set = set()


async def _emit_settings_updated(chat_id: int, source: str, changed) -> None:
    """
    🎙️ v14 · Sincronización bot → Mini App: tras un cambio hecho en la consola privada (AutoLower, Centinela…)
    se emite 'settings_updated' a /ws/radar/{chat_id} con el estado REAL guardado, en la misma forma que los
    endpoints /settings y /configuration de main.py, para que la Mini App repinte sus switches sin recargar.
    """
    try:
        event = {"source": source, "changed": sorted({str(c) for c in (changed or [])})}
        event.update(await get_settings_snapshot(int(chat_id)))
        acoustic = sorted(set(event["changed"]) & {"autolower", "autolower_enabled", "autolower_pct"})
        if acoustic:
            event["acoustic_changed"] = acoustic
        publish_radar_event(int(chat_id), "settings_updated", event)
    except Exception as ex:
        logging.debug(f"[Radar] No se pudo emitir settings_updated para {chat_id}: {ex}")


def emit_settings_updated_bg(chat_id: int, source: str, changed) -> None:
    """Versión en segundo plano (referencia fuerte: la tarea no la recoge el GC antes de terminar)."""
    try:
        task = asyncio.create_task(_emit_settings_updated(chat_id, source, changed))
    except RuntimeError:
        return
    _SETTINGS_EMIT_TASKS.add(task)
    task.add_done_callback(_SETTINGS_EMIT_TASKS.discard)


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
            "⭐ <b>PRO Plan Subscription — {group_name} ({stars} Stars)</b>\n\n"
            "Upgrade your community to elite operational status:\n\n"
            "• ⚡ <b>Unlimited Bot Commands:</b> Bypass the 3 daily uses limit.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Service logs and chat clutter cleanup.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and timeout parameters.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Advanced shielding against channels, bots, and links.\n\n"
            "⭐ <i>Price: <b>{stars} Stars (XTR)</b> · 30 days. Paid 100% in-app with Telegram Stars; activation is instant.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>ULTRA PRO Subscription — {group_name} ({stars} Stars)</b>\n\n"
            "Total command, decentralized automation, and high-tier monetization:\n\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as a 24/7 voice mod via phone.\n"
            "• 🏷️ <b>Native VIP Tag Assignment:</b> Immovable badges (VIP 24/7) on Stars tips.\n"
            "• 🔇 <b>Smart AutoLower Radar:</b> Unverified mics get dialed down to 2% in milliseconds.\n"
            "• 💰 <b>Telegram Stars Monetization:</b> 100% revenue into your balance.\n"
            "• 📢 <b>Channel Membreships Engine:</b> Single-use cryptographic invite links & auto-kick.\n\n"
            "⭐ <i>Price: <b>{stars} Stars (XTR)</b> · 30 days. Paid 100% in-app with Telegram Stars; activation is instant.</i>\n\n"
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
        "ai_prompt_saved": "✅ <b>AI Prompt updated successfully!</b>",

        # --- Ultra Pro: AI Extended, Gamification & Backup ---
        "btn_rep_tools": "🎮 Gamification & XP Levels",
        "btn_backup_tools": "🔐 Cryptographic Backup (.bunker)",
        "rep_main": (
            "🎮 <b>Gamification & XP Levels (Phase 4)</b>\n\n"
            "Reward member participation, boost chat engagement, and unlock automated badges:\n\n"
            "• <b>Status:</b> {st_badge}\n"
            "• <b>Active Multiplier:</b> <code>{multiplier}x XP</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_main": (
            "🔐 <b>Cryptographic Perimeter Vault (HMAC-SHA256)</b>\n\n"
            "Export or restore your full community architecture (locks, captcha, schedules, and tips) "
            "with tamper-proof cryptographic verification:\n\n"
            "• <b>Community:</b> <code>{title}</code>\n"
            "• <b>Format:</b> <code>.bunker (Encrypted JSON)</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_prompt": (
            "📥 <b>Perimeter Vault: Import Configuration</b>\n\n"
            "Send your <code>.bunker</code> backup file or paste the verified cryptographic JSON to apply all rules instantly:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
    },
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
            "⭐ <b>Suscripción Plan PRO — {group_name} ({stars} Stars)</b>\n\n"
            "• ⚡ <b>Comandos de Bot Ilimitados.</b>\n"
            "• 🗑️ <b>Purga Automatizada de mensajes de servicio.</b>\n"
            "• 🤖 <b>Aduana Captcha Pro Avanzada.</b>\n"
            "• 🛡️ <b>Anti-Spam Granular Total.</b>\n\n"
            "⭐ <i>Precio: <b>{stars} Stars (XTR)</b> · 30 días. Pago 100% in-app con Telegram Stars; activación inmediata.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>Suscripción ULTRA PRO — {group_name} ({stars} Stars)</b>\n\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Tu réplica bajo tu propio token.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Moderación acústica en videollamadas vía teléfono.\n"
            "• 🏷️ <b>Etiquetas Nativas VIP y Radar AutoLower al 2%.</b>\n"
            "• 💰 <b>Monetización Stars directa a tu balance.</b>\n"
            "• 📢 <b>Motor de Membresías en Canales:</b> Enlaces de 1 solo uso y expulsión automática.\n\n"
            "⭐ <i>Precio: <b>{stars} Stars (XTR)</b> · 30 días. Pago 100% in-app con Telegram Stars; activación inmediata.</i>\n\n"
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
         "• <b>Copiloto AMA (Resúmenes):</b> {copilot_st}\n"
         "• <b>Prompt Personalizado:</b> <code>{custom_prompt}</code>\n\n"
         "🛡️ <i>Cloud Media Management</i>"
     ),
     "btn_ai_guardian": "🛡️ Guardián Anti-Toxicidad: {guardian_st}",
     "btn_ai_copilot": "📝 Copilot AMA: {copilot_st}",
     "btn_ai_prompt": "✍️ Editar Prompt de IA",
     "ai_prompt_prompt": "✍️ <b>Editor de Prompt de IA:</b>\n\nEnvía las instrucciones operativas o de comportamiento para tu asistente de inteligencia artificial:",
      "ai_prompt_saved": "✅ <b>¡Prompt de IA actualizado con éxito!</b>",
          # --- Ultra Pro: IA Extendida, Gamificación y Backup ---
        "btn_rep_tools": "🎮 Gamificación & Niveles XP",
        "btn_backup_tools": "🔐 Respaldo Criptográfico (.bunker)",
        "rep_main": (
            "🎮 <b>Gamificación y Niveles de Reputación XP (Fase 4)</b>\n\n"
            "Fomenta la retención comunitaria otorgando experiencia por mensajes legítimos y desbloqueo de rangos:\n\n"
            "• <b>Estado del Sistema:</b> {st_badge}\n"
            "• <b>Multiplicador Activo:</b> <code>{multiplier}x XP</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_main": (
            "🔐 <b>Bóveda Criptográfica Perimetral (HMAC-SHA256)</b>\n\n"
            "Exporta o restaura toda la arquitectura de tu comunidad (cerraduras, captcha, filtros, cronogramas y propinas) "
            "con validación criptográfica anti-manipulaciones:\n\n"
            "• <b>Comunidad:</b> <code>{title}</code>\n"
            "• <b>Formato:</b> <code>.bunker (JSON Firmado)</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_prompt": (
            "📥 <b>Bóveda Perimetral: Importar Configuración</b>\n\n"
            "Envía en este chat tu archivo de respaldo <code>.bunker</code> o pega el paquete JSON verificado para aplicar todas las reglas al instante:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
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
        "btn_architect": "👑 Matriz del Arquitecto",
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
        "btn_architect": "👑 Architect Matrix",
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


# ==========================================
# 🌐 v21 · IDIOMA DEL OPERADOR: inglés por defecto, español latino persistente
# ==========================================
# Orden de resolución: 1) preferencia guardada (users.lang) → 2) idioma de Telegram SOLO si
# BUNKER_LANG_AUTODETECT=1 → 3) DEFAULT_LANG ('en', o BUNKER_DEFAULT_LANG). La preferencia se precarga en una
# caché acotada (TTL + tope) mediante LangPreferenceMiddleware, así user_lang() sigue siendo síncrona.
try:
    from database.database import (
        get_user_lang, set_user_lang, normalize_lang, DEFAULT_LANG as _DB_DEFAULT_LANG,
    )
except ImportError:  # pragma: no cover - base de datos anterior a v21: sin persistencia, inglés por defecto
    _DB_DEFAULT_LANG = "en"

    async def get_user_lang(user_id: int) -> Optional[str]:   # misma firma que database.database
        return None

    async def set_user_lang(user_id: int, lang: str, username: Optional[str] = None, full_name: Optional[str] = None) -> str:
        return "es" if str(lang).lower().startswith("es") else "en"

    def normalize_lang(value, default=None):
        raw = str(value or "").strip().lower()
        return "en" if raw.startswith("en") else ("es" if raw.startswith("es") else default)

DEFAULT_LANG = _DB_DEFAULT_LANG
LANG_AUTODETECT = os.getenv("BUNKER_LANG_AUTODETECT", "0").strip() == "1"
LANG_PREF_TTL_SECONDS = 600
LANG_PREF_CACHE_MAX = 20000
_LANG_PREFS: dict = {}   # user_id → (lang | None, monotonic_ts)


def _cache_user_lang(user_id: int, lang) -> None:
    _LANG_PREFS.pop(user_id, None)
    _LANG_PREFS[user_id] = (lang, time.monotonic())
    overflow = len(_LANG_PREFS) - LANG_PREF_CACHE_MAX
    if overflow > 0:
        for old in list(_LANG_PREFS)[:overflow]:
            _LANG_PREFS.pop(old, None)


def _cached_user_lang(user_id: int):
    entry = _LANG_PREFS.get(user_id)
    if entry and time.monotonic() - entry[1] < LANG_PREF_TTL_SECONDS:
        return entry[0], True
    return None, False


def user_lang(user) -> str:
    """Idioma con el que se habla a este usuario (síncrono; la preferencia ya está precargada)."""
    uid = getattr(user, "id", None)
    if uid:
        pref, _hit = _cached_user_lang(int(uid))
        if pref:
            return pref
    if LANG_AUTODETECT:
        detected = normalize_lang(getattr(user, "language_code", None))
        if detected:
            return detected
    return DEFAULT_LANG


async def prime_user_lang(user) -> str:
    """Carga (una vez cada 10 min) la preferencia guardada en la caché y devuelve el idioma efectivo."""
    uid = getattr(user, "id", None)
    if not uid:
        return DEFAULT_LANG
    _pref, hit = _cached_user_lang(int(uid))
    if not hit:
        try:
            stored = await get_user_lang(int(uid))
        except Exception as ex:
            logging.debug(f"[i18n] Preferencia de idioma no disponible para {uid}: {ex}")
            return user_lang(user)
        _cache_user_lang(int(uid), stored)
    return user_lang(user)


async def remember_user_lang(user, lang: str) -> str:
    """Guarda la elección del operador (bot y Mini App comparten users.lang) y la aplica al instante."""
    clean = normalize_lang(lang, DEFAULT_LANG)
    uid = int(getattr(user, "id", 0) or 0)
    if not uid:
        return clean
    _cache_user_lang(uid, clean)
    try:
        await set_user_lang(uid, clean, getattr(user, "username", None), getattr(user, "full_name", None))
    except Exception as ex:
        logging.warning(f"⚠️ [i18n] No se pudo guardar el idioma de {uid}: {ex}")
    return clean


def forget_user_lang_cache(user_id: int) -> None:
    """Invalida la caché (p. ej. cuando la Mini App cambia el idioma por la API)."""
    _LANG_PREFS.pop(int(user_id), None)


def lang_from_callback(callback, data: list) -> str:
    """Idioma de un botón: el sufijo _es/_en del menú que lo generó o, si no lo trae, el del operador."""
    tail = data[-1] if data else ""
    return tail if tail in ("es", "en") else user_lang(callback.from_user)


class LangPreferenceMiddleware(BaseMiddleware):
    """Precarga users.lang antes de cada handler de esta consola (mensajes y botones)."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and not getattr(user, "is_bot", False):
            await prime_user_lang(user)
        return await handler(event, data)


router.message.middleware(LangPreferenceMiddleware())
router.callback_query.middleware(LangPreferenceMiddleware())


def language_selector_keyboard(current: str, back_callback: Optional[str] = None) -> InlineKeyboardMarkup:
    """Selector 🇺🇸 EN / 🇪🇸 ES con la opción vigente marcada."""
    rows = [[
        InlineKeyboardButton(text=("✅ " if current == "en" else "") + "🇺🇸 English", callback_data="setlang_en"),
        InlineKeyboardButton(text=("✅ " if current == "es" else "") + "🇪🇸 Español", callback_data="setlang_es"),
    ]]
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ " + ("Back" if current == "en" else "Volver"), callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


async def _delegated_chat_entries(bot: Bot, user_id: int, want_channels: bool, perm: str = "any") -> list:
    """
    👑 v13 · Comunidades donde el usuario es DELEGADO del Arquitecto con el permiso `perm` ('any' = alguno),
    el bot sigue siendo administrador y el delegado no fue expulsado. [(chat_id, título)].
    """
    try:
        delegated = await get_delegated_chats(user_id)
    except Exception as ex:
        logging.warning(f"⚠️ [Delegados] No se pudieron leer las delegaciones de {user_id}: {ex}")
        return []
    if not delegated:
        return []
    try:
        bot_id = (await bot.get_me()).id
    except Exception:
        return []

    async def check(entry: dict):
        perms = entry.get("perms") or {}
        if perm != "any" and not perms.get(perm):
            return None
        chat_id = int(entry["chat_id"])
        try:
            bot_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_id)
            if str(getattr(bot_member.status, "value", bot_member.status)) not in ("administrator", "creator"):
                return None
            chat_obj = await bot.get_chat(chat_id)
        except Exception:
            return None
        is_channel = str(getattr(chat_obj.type, "value", chat_obj.type)) == "channel"
        if is_channel != want_channels:
            return None
        if await _live_owner_status(bot, chat_id, user_id) in ("left", "kicked"):
            return None
        CHAT_KIND_CACHE[chat_id] = "c" if is_channel else "g"
        return (chat_id, f"🛡️ {chat_obj.title or entry.get('title') or chat_id}")

    results = await asyncio.gather(*(check(e) for e in delegated))
    return [r for r in results if r is not None]


def _merge_chat_lists(primary: list, extra: list) -> list:
    seen = {int(row[0]) for row in primary}
    return list(primary) + [row for row in extra if int(row[0]) not in seen]


async def get_active_user_groups(bot: Bot, user_id: int, include_delegated: Optional[str] = None) -> list:
    """
    Grupos que el usuario puede operar: los que POSEE (creador en vivo) y, si include_delegated se indica,
    también aquellos donde el Arquitecto lo designó delegado con ese permiso ('any' = cualquiera).
    """
    raw_groups = await get_user_groups(user_id)
    delegated = await _delegated_chat_entries(bot, user_id, False, include_delegated) if include_delegated else []
    if not raw_groups:
        return delegated
    if is_super_admin(user_id):
        return _merge_chat_lists(list(raw_groups), delegated)
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
    return _merge_chat_lists([res for res in results if res is not None], delegated)


async def get_active_user_channels(bot: Bot, user_id: int, include_delegated: Optional[str] = None) -> list:
    owned = await _get_owned_user_channels(bot, user_id)
    if not include_delegated:
        return owned
    return _merge_chat_lists(owned, await _delegated_chat_entries(bot, user_id, True, include_delegated))


async def _get_owned_user_channels(bot: Bot, user_id: int) -> list:
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
            # 👑 Solo el CREADOR del canal lo ve en su selector; un administrador común queda fuera.
            return (c_id, title, True) if str(getattr(user_member.status, "value", user_member.status)) == "creator" else None
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


# 👑 v12 · REGLA DE ORO: solo el CREADOR del chat (verificado en vivo) y los Arquitectos configuran el bot.
# Un administrador común — con todos o algunos permisos — NUNCA es dueño, figure o no en el registro.
OWNER_LIVE_ROLES = ("creator",)
_OWNER_STATUS_CACHE: dict = {}          # (bot_id, chat_id, user_id) -> (estado, expira)
_OWNER_STATUS_CACHE_MAX = 20000
# Último rango CONFIRMADO por Telegram (sin caducidad corta). Si alguna vez se vio que el usuario NO es el
# creador, una caída posterior de la Bot API no le abre la puerta por la vía del registro.
_OWNER_LAST_KNOWN: dict = {}            # (chat_id, user_id) -> estado confirmado
_OWNER_LAST_KNOWN_MAX = 50000


async def _live_owner_status(bot: Bot, chat_id: int, user_id: int, fresh: bool = False) -> str:
    """
    Estado real del usuario en el chat ('creator', 'administrator', 'member', 'left'…). Caché corta para no
    llamar a getChatMember en cada botón: 5 min si es el creador, 1 min si no, 30 s si Telegram falló ('').
    fresh=True ignora la caché (acciones sensibles).
    """
    key = (bot.id, int(chat_id), int(user_id))
    now = time.monotonic()
    cached = _OWNER_STATUS_CACHE.get(key)
    if cached and cached[1] > now and not fresh:
        return cached[0]
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=user_id), timeout=5)
        status = str(getattr(member.status, "value", member.status) or "")
        ttl = 300.0 if status == "creator" else 60.0
        if len(_OWNER_LAST_KNOWN) >= _OWNER_LAST_KNOWN_MAX:
            _OWNER_LAST_KNOWN.clear()
        _OWNER_LAST_KNOWN[(int(chat_id), int(user_id))] = status
    except Exception:
        status, ttl = "", 30.0
    if len(_OWNER_STATUS_CACHE) >= _OWNER_STATUS_CACHE_MAX:
        _OWNER_STATUS_CACHE.clear()
    _OWNER_STATUS_CACHE[key] = (status, now + ttl)
    return status


def invalidate_owner_status(chat_id: int, user_id: int) -> None:
    """Olvida el rango cacheado (cambio de membresía, transferencia de propiedad…)."""
    for key in [k for k in _OWNER_STATUS_CACHE if k[1] == int(chat_id) and k[2] == int(user_id)]:
        _OWNER_STATUS_CACHE.pop(key, None)


async def is_legitimate_owner(bot: Bot, user_id: int, group_id: int) -> bool:
    """
    Regla ÚNICA y estricta de propiedad (paridad 1:1 con main.is_legitimate_chat_owner):
      1. Arquitecto Supremo (SUPER_ADMIN_IDS) → sí, en cualquier comunidad o canal.
      2. Creador ('creator') del chat según Telegram en vivo → sí.
      3. Cualquier otro rango confirmado por Telegram ('administrator', 'member', 'left'…) → NO, aunque figure
         como dueño en user_groups / bot_clones / channel_owner_registry. Un administrador común, con todos o
         algunos permisos, jamás accede a las consolas.
      4. Telegram no responde → solo el dueño REGISTRADO cuyo último rango confirmado no contradiga ser el
         creador conserva el acceso (con aviso en el log). Nadie sin registro entra por esta vía.
    """
    if not user_id:
        return False
    if is_super_admin(user_id):
        return True
    status = await _live_owner_status(bot, group_id, user_id)
    if status == "creator":
        return True
    if status:
        if status == "administrator":
            logging.warning(f"⛔ [Aduana] {user_id} es administrador (no creador) de {group_id}: consola denegada.")
        return False
    # Telegram no confirmó el rango: fallback acotado al registro.
    last_known = _OWNER_LAST_KNOWN.get((int(group_id), int(user_id)))
    if last_known and last_known != "creator":
        logging.warning(f"⛔ [Aduana] Telegram no respondió y el último rango conocido de {user_id} en {group_id} es '{last_known}': denegado.")
        return False
    registered = await is_registered_owner_db(user_id, group_id) or await registry_has_owner(user_id, group_id)
    if registered:
        logging.warning(f"⚠️ [Aduana] Telegram no confirmó el rango de {user_id} en {group_id}; se mantiene el acceso del dueño registrado.")
        return True
    return False


def _state_target_chat(value) -> int | None:
    """Comunidad/canal sobre el que actúa una conversación pendiente (int o dict con group_id/channel_id/chat_id)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value < 0 else None
    if isinstance(value, dict):
        for field in ("group_id", "channel_id", "chat_id"):
            raw = value.get(field)
            try:
                chat_id = int(raw)  # pyright: ignore[reportArgumentType]  (None → TypeError capturado)
            except (TypeError, ValueError):
                continue
            if chat_id < 0:
                return chat_id
    return None


# ==========================================
# 👑 v13 · DELEGADOS DEL ARQUITECTO — MAPA BOTÓN → PERMISO
# ==========================================
# Un delegado (designado SOLO por el Arquitecto en la Matriz) opera exclusivamente los botones del módulo que
# tiene activado. Todo botón que no figure aquí sigue siendo exclusivo del Dueño legítimo y del Arquitecto
# (moderación ejecutiva, pasarela de planes, clon y centinela por teléfono, respaldos, pánico, etc.).
DELEGATE_NAV_PREFIXES = ("gpanel_", "cpanel_", "langpanel_", "langcpanel_", "menu_eco_", "menu_ultra_")
DELEGATE_CALLBACK_RULES = (
    # 🤖 Aduana y Captcha
    ("gset_captcha_", "can_manage_captcha"), ("togcap_", "can_manage_captcha"), ("togmode_", "can_manage_captcha"),
    ("cap_set_", "can_manage_captcha"), ("capval_", "can_manage_captcha"),
    # 🔒 Cerraduras de contenido
    ("gset_locks_", "can_manage_locks"), ("toglock_", "can_manage_locks"), ("mlk_", "can_manage_locks"),
    # 🛡️ Anti-Spam y Anti-Flood
    ("gset_antispam_", "can_manage_antispam"), ("astog_", "can_manage_antispam"), ("as_fwd_", "can_manage_antispam"),
    ("as_togdel_", "can_manage_antispam"), ("gset_antiflood_", "can_manage_antispam"), ("afset_", "can_manage_antispam"),
    ("afval_", "can_manage_antispam"), ("afact_", "can_manage_antispam"),
    # 📢 Difusión de planes y anuncios (sin crear, pausar ni borrar planes: eso es del Dueño)
    ("chplans_menu_", "can_manage_broadcast"), ("chplans_view_", "can_manage_broadcast"),
    ("chplans_share_", "can_manage_broadcast"), ("chplans_postto_", "can_manage_broadcast"),
    ("chplans_sched_", "can_manage_broadcast"), ("chplans_setsched_", "can_manage_broadcast"),
    ("tips_menu_", "can_manage_broadcast"), ("tips_share_", "can_manage_broadcast"),
    ("tips_postto_", "can_manage_broadcast"), ("tips_broadcast_", "can_manage_broadcast"),
    ("gami_", "can_manage_broadcast"),
    # 📊 Métricas y telemetría (solo lectura)
    ("radar_", "can_view_analytics"), ("tips_telemetry_", "can_view_analytics"), ("rep_viewtop_", "can_view_analytics"),
    ("mvaudit_", "can_view_analytics"),   # 🎟️ v22 · Auditoría MicVIP (solo lectura)
    # 🎙️ Radar de voz y Centinelas (sin conectar clones ni sesiones por teléfono)
    ("sentinelcfg_", "can_manage_sentinel"), ("payload_", "can_manage_sentinel"), ("cmd_autolower_", "can_manage_sentinel"),
    ("alset_", "can_manage_sentinel"), ("cmd_cams_", "can_manage_sentinel"), ("vcsched_", "can_manage_sentinel"),
)


def required_delegate_permission(callback_data: str):
    """'any' para navegación, el permiso exacto para un módulo delegable, o None si el botón es solo del Dueño."""
    raw = callback_data or ""
    for prefix, perm in DELEGATE_CALLBACK_RULES:
        if raw.startswith(prefix):
            return perm
    if raw.startswith(DELEGATE_NAV_PREFIXES):
        return "any"
    return None


async def is_authorized_operator(bot: Bot, user_id: int, chat_id: int, perm: Optional[str] = None) -> bool:
    """
    Dueño legítimo / Arquitecto → siempre. Delegado → solo si el Arquitecto le activó `perm` en esa comunidad
    ('any' = al menos un permiso) y no salió ni fue expulsado del chat. perm=None → exclusivo del Dueño.
    """
    if await is_legitimate_owner(bot, user_id, chat_id):
        return True
    if not perm:
        return False
    try:
        if not await has_delegate_permission(chat_id, user_id, perm):
            return False
    except Exception as ex:
        logging.warning(f"⚠️ [Delegados] Consulta fallida ({chat_id}/{user_id}): {ex}")
        return False
    if await _live_owner_status(bot, chat_id, user_id) in ("left", "kicked"):
        return False
    return True


async def is_authorized_for_callback(bot: Bot, user_id: int, chat_id: int, callback_data: str) -> bool:
    return await is_authorized_operator(bot, user_id, chat_id, required_delegate_permission(callback_data))


def _state_delegate_permission(state_dict: dict):
    """Permiso delegado con el que puede responderse cada editor conversacional (None = solo el Dueño)."""
    mapping = (
        (CAPTCHA_STATES, "can_manage_captcha"),
        (GAMI_STATES, "can_manage_broadcast"),
        (SENTINEL_CFG_STATES, "can_manage_sentinel"),
        (SENTINEL_PAYLOAD_TEXT_STATES, "can_manage_sentinel"),
        (SENTINEL_PAYLOAD_MEDIA_STATES, "can_manage_sentinel"),
        (SENTINEL_PAYLOAD_AUTODEL_STATES, "can_manage_sentinel"),
        (VC_SCHED_STATES, "can_manage_sentinel"),
        (_v23_auth_bot_states(), "can_manage_locks"),   # 🛡️ v23 · asistente «➕ Autorizar bot» de la matriz
    )
    for store, perm in mapping:
        if store is state_dict:
            return perm
    return None


async def guard_pending_state_ownership(message: Message, bot: Bot, t: dict) -> bool:
    """
    Re-verifica la autorización ANTES de aplicar cualquier respuesta conversacional. Cada editor (captcha,
    propinas, centinela, clones, moderación, planes…) guarda el group_id al abrirse; si entre la apertura y
    la respuesta el usuario dejó de ser el dueño legítimo (o perdió el permiso delegado de ese editor), la
    entrada se descarta y se liberan sus estados. Devuelve True si puede continuar.
    """
    user_id = message.from_user.id
    key = (bot.id, user_id)
    checks = []
    for state_dict in ALL_STATE_DICTS:
        if key in state_dict:
            chat = _state_target_chat(state_dict.get(key))
            if chat is not None:
                checks.append((chat, _state_delegate_permission(state_dict)))
    for chat_id, perm in checks:
        if not await is_authorized_operator(bot, user_id, chat_id, perm):
            logging.warning(f"⛔ [Aduana] Entrada conversacional de {user_id} descartada: no está autorizado en {chat_id}.")
            clear_user_states(bot.id, user_id)
            await message.answer(t["owner_only_alert"])
            return False
    return True


async def verify_admin_privileges(callback: CallbackQuery, bot: Bot, group_id: int) -> bool:
    """Dueño legítimo, Arquitecto o delegado con el permiso exacto que exige este botón (ver DELEGATE_CALLBACK_RULES)."""
    lang = user_lang(callback.from_user)
    t = TEXTS[lang]
    if await is_authorized_for_callback(bot, callback.from_user.id, group_id, callback.data or ""):
        return True
    logging.warning(f"⛔ [Aduana] Acceso rechazado — user={callback.from_user.id} intentó operar group={group_id} sin autorización.")
    await callback.answer(t["owner_only_alert"], show_alert=True)
    return False


async def verify_admin_privileges_msg(message: Message, bot: Bot, group_id: int, perm: Optional[str] = None) -> bool:
    """Igual que verify_admin_privileges para mensajes; perm=None → exclusivo del Dueño / Arquitecto."""
    lang = user_lang(message.from_user)
    t = TEXTS[lang]
    if await is_authorized_operator(bot, message.from_user.id, group_id, perm):
        return True
    logging.warning(f"⛔ [Aduana] Acceso rechazado — user={message.from_user.id} intentó operar group={group_id} sin autorización.")
    await message.answer(t["owner_only_alert"])
    return False


async def _reply_plan_state_lost(message: Message, lang: str, channel_id=None) -> None:
    """Reply when the persisted channel-plan assistant state is missing or invalid."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    await message.answer(t["plan_state_lost"], parse_mode="HTML")


async def _finalize_and_preview_channel_plan(
    bot: Bot, chat_id: int, channel_id: int, lang: str, name: str,
    days: int, price: int, promo_text: str, media_id: Optional[str] = None,
    media_type: Optional[str] = None, target_link: Optional[str] = None, recurrence_hours: int = 0
) -> None:
    """Guarda el plan del canal y envía una vista previa donde el enlace VIP es un botón inline."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    try:
        plan_id = await create_channel_plan(
            channel_id, name, days, price, promo_text, media_id, media_type, target_link
        )
        if recurrence_hours and recurrence_hours > 0:
            if not await set_channel_plan_broadcast_config(plan_id, channel_id, recurrence_hours, lang):
                recurrence_hours = 0
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
        preview += "\n\n" + tr(lang, f"⏰ <b>Difusión automática:</b> cada {recurrence_hours} h en el canal",
                                 f"⏰ <b>Automatic broadcast:</b> every {recurrence_hours} h in the channel")

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

async def get_reputation_settings(group_id: int) -> dict:
    """Lee el estado del motor de gamificación y multiplicador de XP del grupo."""
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT reputation_enabled, reputation_xp_multiplier FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "enabled": row[0] if row[0] is not None else 1,
                    "multiplier": row[1] if row[1] is not None else 1.0
                }
            return {"enabled": 1, "multiplier": 1.0}
    return await asyncio.to_thread(_sync)


async def set_reputation_setting(group_id: int, field: str, value):
    """Guarda parámetros de gamificación en group_settings."""
    if field not in ("reputation_enabled", "reputation_xp_multiplier"):
        return
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE group_settings SET {field} = ? WHERE group_id = ?", (value, group_id))
            conn.commit()
    await asyncio.to_thread(_sync)        

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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    if await resolve_chat_kind(bot, chat_id) == "c":
        return InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{chat_id}_{lang}")
    return InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{chat_id}_{lang}")


def ultra_back_button(chat_type: str, chat_id: int, lang: str) -> InlineKeyboardButton:
    """Retorno de las Herramientas Ultra: canal → cpanel | grupo → menú de herramientas Ultra del grupo."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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


async def _tips_finish(bot: Bot, message: Message, state: dict, group_id: int, lang: str, notice_text: str, status_msg: Optional[Message] = None) -> None:
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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


def get_main_keyboard(bot_username: str, lang: str, is_clone: bool = False, user_id: int = 0):
    """Teclado principal con bifurcación dual independiente para Grupos y Canales (+ Matriz para Arquitectos)."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
        # 🌐 v21 · Muestra el idioma al que se cambia (🇺🇸 EN por defecto, 🇪🇸 ES persistente)
        InlineKeyboardButton(text="🇪🇸 Español" if lang == "en" else "🇺🇸 English", callback_data=f"lang_{'es' if lang == 'en' else 'en'}")
    ])
    rows.append([
        InlineKeyboardButton(text=t["btn_support"], callback_data=f"menu_support_{lang}"),
        InlineKeyboardButton(text=t["btn_info"], callback_data=f"menu_info_{lang}")
    ])
    # 👑 Acceso directo a la Matriz del Arquitecto: el botón solo existe para los Arquitectos y, aun así, el
    # router de la Matriz vuelve a verificar is_super_admin en cada evento.
    if user_id and is_super_admin(user_id):
        rows.insert(0, [InlineKeyboardButton(text=t["btn_architect"], callback_data=f"amx:lang:{lang}")])
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


async def send_official_welcome(bot: Bot, chat_id: int, user, bot_username: Optional[str] = None) -> None:
    if not bot_username:
        bot_username = (await bot.get_me()).username or "BunkerBot"
    lang = user_lang(user) if user else DEFAULT_LANG
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    name = html.escape(user.full_name) if user else "Comandante"
    text = t["welcome"].format(name=name)
    keyboard = get_main_keyboard(bot_username, lang, is_clone=is_clone_bot(bot), user_id=getattr(user, "id", 0))
    # 🖼️ Prioridad: tarjeta gráfica (assets) -> video de bienvenida -> texto
    if await render_card(bot, chat_id, "welcome", lang, keyboard):
        return
    if await _send_welcome_video(bot, chat_id, text, keyboard):
        return
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


def get_simple_back_keyboard(lang: str, target: str = "main"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_{target}_{lang}")]])


def get_info_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_how_works"], callback_data=f"menu_infohow_{lang}")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])


def get_support_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    return InlineKeyboardMarkup(inline_keyboard=[
        # 🤖 v20 · Arquitecto de Soporte con IA (Mistral, Capa 0 PII)
        [InlineKeyboardButton(text="🤖 " + ("Asistente de Soporte IA" if lang == "es" else "AI Support Assistant"), callback_data=f"supai_start_{lang}")],
        [InlineKeyboardButton(text=t["btn_contact_support"], url="https://t.me/m/RGx4ohGTMTk5")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])


def get_groups_keyboard(groups: list, lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
            InlineKeyboardButton(text="🧬 " + ("Clon & Centinela" if lang == "es" else "Clone & Sentinel"), callback_data=f"gset_clone_{channel_id}_{lang}"),
            InlineKeyboardButton(text="🎮 " + ("Anuncio Gamificación" if lang == "es" else "Gamification Ad"), callback_data=f"gami_menu_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="🇺🇸 English" if lang == "es" else "🇪🇸 Español", callback_data=f"langcpanel_{channel_id}_{toggle_lang}"),
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


PLAN_RECURRENCE_CHOICES = (6, 12, 24, 48)


def _plan_recurrence_keyboard(t: dict, channel_id: int, lang: str, plan_id: Optional[int] = None, current: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Intervalos de difusión recurrente (6/12/24/48 h o desactivada). Sin plan_id → paso final del asistente
    (chplans_recur_*); con plan_id → editor de un plan existente (chplans_setsched_*).
    """
    def cb(hours: int) -> str:
        if plan_id is None:
            return f"chplans_recur_{hours}_{channel_id}_{lang}"
        return f"chplans_setsched_{plan_id}_{channel_id}_{hours}_{lang}"

    def label(hours: int) -> str:
        mark = "✅ " if current is not None and int(current) == hours else ""
        if hours == 0:
            return mark + tr(lang, "🚫 Sin difusión automática", "🚫 No automatic broadcast")
        return mark + tr(lang, f"⏰ Cada {hours} h", f"⏰ Every {hours} h")

    rows = [
        [InlineKeyboardButton(text=label(6), callback_data=cb(6)), InlineKeyboardButton(text=label(12), callback_data=cb(12))],
        [InlineKeyboardButton(text=label(24), callback_data=cb(24)), InlineKeyboardButton(text=label(48), callback_data=cb(48))],
        [InlineKeyboardButton(text=label(0), callback_data=cb(0))],
        [InlineKeyboardButton(text=t["btn_cancel_ret"] if plan_id is None else "🔙 " + tr(lang, "Volver a Planes", "Back to Plans"),
                              callback_data=f"chplans_menu_{channel_id}_{lang}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_group_panel_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ PRO", callback_data=f"pay_pro_{group_id}_{lang}"), InlineKeyboardButton(text="💎 ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_mod"], callback_data=f"menu_mod_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_eco"], callback_data=f"menu_eco_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_antispam"], callback_data=f"gset_antispam_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_antiflood"], callback_data=f"gset_antiflood_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_captcha"], callback_data=f"gset_captcha_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_locks"], callback_data=f"gset_locks_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_warns"], callback_data=f"gset_warns_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_delmsgs"], callback_data=f"gset_delmsgs_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_clone"], callback_data=f"gset_clone_{group_id}_{lang}")],
        # 🛰️ v20 · Estado real del perímetro y canal de registro con validación en vivo (paridad con la Mini App)
        [
            InlineKeyboardButton(text="🛰️ " + ("Perímetro" if lang == "es" else "Perimeter"), callback_data=f"perim_view_{group_id}_{lang}"),
            InlineKeyboardButton(text="🧾 " + ("Canal de Registro" if lang == "es" else "Log Channel"), callback_data=f"logch_menu_{group_id}_{lang}"),
        ],
        # 🎟️ v22 · Registro auditable de compras MicVIP (compra → pase → Centinela)
        [InlineKeyboardButton(text="🎟️ " + ("Auditoría MicVIP" if lang == "es" else "MicVIP Audit"), callback_data=f"mvaudit_{group_id}_all_1_{lang}")],
        # 👑🔗 v23 · Roles VIP post-donación y cuota diaria de enlaces de administradores (solo el Dueño)
        [
            InlineKeyboardButton(text="👑 " + ("Roles VIP" if lang == "es" else "VIP Roles"), callback_data=f"vipr_m_{group_id}_{lang}"),
            InlineKeyboardButton(text="🔗 " + ("Cuotas de enlaces" if lang == "es" else "Link quotas"), callback_data=f"alq_m_{group_id}_{lang}"),
        ],
        # ⚖️ v24 · Apelación de baneos por Telegram Stars (solo el Dueño)
        [InlineKeyboardButton(text="⚖️ " + ("Apelaciones de baneo" if lang == "es" else "Ban appeals"), callback_data=f"apl_m_{group_id}_all_{lang}")],
        [InlineKeyboardButton(text=t["btn_ultra_tools"], callback_data=f"menu_ultra_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="🇺🇸 English" if lang == "es" else "🇪🇸 Español", callback_data=f"langpanel_{group_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_settings"], callback_data=f"menu_settings_{lang}")
        ]
    ])


def get_ultra_tools_keyboard(group_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_ai_sentinel"], callback_data=f"ai_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚙️ " + ("Configuración del Centinela" if lang == "es" else "Sentinel Settings"), callback_data=f"sentinelcfg_menu_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=t["btn_rep_tools"], callback_data=f"rep_menu_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_backup_tools"], callback_data=f"backup_menu_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text="🚨 " + ("Botón de Pánico" if lang == "es" else "Panic Button"), callback_data=f"panic_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎥 " + ("Escudo Antinota" if lang == "es" else "Screen-Share Shield"), callback_data=f"shield_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎙️ " + ("Modo Podcast" if lang == "es" else "Podcast Mode"), callback_data=f"podcast_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🌟 " + ("Gestión de Speakers" if lang == "es" else "Speakers Management"), callback_data=f"speakers_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="💎 " + ("Payload Multimedia" if lang == "es" else "Multimedia Payload"), callback_data=f"payload_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="💀 " + ("Ghost Purge (Cuentas Fantasma)" if lang == "es" else "Ghost Purge (Ghost Accounts)"), callback_data=f"gpurge_menu_{group_id}_{lang}")],
        [back_btn]
    ])


def build_ultra_lock_view(group_id: int, lang: str, feature_title: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_panic_confirm"], callback_data=f"panic_activate_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"panic_menu_{group_id}_{lang}")]
    ])


def get_shield_keyboard(group_id: int, lang: str, status: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    back_btn = ultra_back_button(chat_type, group_id, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_shield_1"], callback_data=f"shield_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_shield_0"], callback_data=f"shield_toggle_0_{group_id}_{lang}")
        ],
        [back_btn]
    ])


def get_podcast_keyboard(group_id: int, lang: str, status: int, duck_level: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
TIPS_THANKS_MAX_LEN = 3000   # Mismo tope que payments.TIPS_THANKS_MAX_LEN
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
        return max(1, int(_tips_cfg(cfg, "amount", "tips_amount", default=10)))  # pyright: ignore[reportArgumentType]
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
        f"({active_n} {tr(lang, 'activos', 'active')})\n"
        f"• <b>{tr(lang, 'Agradecimiento', 'Thank-you message')}:</b> "
        f"{tr(lang, '✅ Personalizado', '✅ Custom') if (cfg or {}).get('tips_thanks_text') and tier in ('pro', 'ultra_pro') else tr(lang, '⚪ Oficial', '⚪ Official')}\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )


async def get_tips_keyboard(group_id: int, lang: str, cfg: dict, chat_type: str = "g", viewer_id: int = 0, targets: Optional[list] = None):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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

    has_thanks = bool(_tips_cfg(cfg, "tips_thanks_text"))
    if tier in ("pro", "ultra_pro"):
        rows.append([
            InlineKeyboardButton(text=f"💌 {tr(lang, 'Agradecimiento', 'Thank-you')} (PRO) {'✅' if has_thanks else '⬜'}", callback_data=f"tips_setthanks_{group_id}_{lang}"),
            InlineKeyboardButton(text=f"👁️ {tr(lang, 'Vista previa', 'Preview')}", callback_data=f"tips_previewthanks_{group_id}_{lang}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text=f"🔒 {tr(lang, 'Agradecimiento Personalizado', 'Custom Thank-you')} (PRO)", callback_data=f"pay_pro_{group_id}_{lang}")])

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


async def build_tips_panel(group_id: int, lang: str, chat_kind: str, viewer_id: int, bot: Optional[Bot] = None):
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


TIPS_TARGET_CHECK_TIMEOUT = 8.0


def _tips_member_status(member) -> str:
    return str(getattr(getattr(member, "status", None), "value", getattr(member, "status", "")) or "").lower()


async def validate_tip_target(bot: Bot, user_id: int, ref: str):
    """
    v30 · Validación ESTRICTA del destino antes de insertarlo (nada se guarda si algo falla):
      1. el chat existe y es canal / supergrupo / grupo (con time-out: Telegram lento ≠ destino inválido),
      2. el bot es miembro y PUEDE PUBLICAR: en canales, admin con can_post_messages; en grupos, admin, o miembro
         sin restricción de envío y con envío permitido por los permisos por defecto del grupo,
      3. si el plan usa multimedia, que el bot pueda enviar fotos/vídeos (se avisa, no bloquea),
      4. 👑 Regla de Oro: solo el CREADOR del destino (o un Arquitecto) puede vincularlo.
    Devuelve (info, None) o (None, código). info.value es SIEMPRE el id numérico estable (un @alias puede cambiar
    o liberarse y romper la difusión); info.aliases sirve para detectar duplicados guardados con formato antiguo.
    """
    if not ref:
        return None, "invalid"
    chat_ref = int(ref) if ref.lstrip("-").isdigit() else ref
    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_ref), timeout=TIPS_TARGET_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return None, "unreachable"
    except TelegramForbiddenError:
        return None, "bot_not_member"
    except TelegramBadRequest as ex:
        text = str(ex).lower()
        return None, ("bot_not_member" if "kicked" in text or "forbidden" in text else "not_found")
    except Exception as ex:
        logging.warning("⚠️ [Tips] get_chat falló para %s: %s", ref, ex)
        return None, "unreachable"

    chat_type = str(getattr(getattr(chat, "type", None), "value", getattr(chat, "type", "")) or "")
    if not chat or chat_type not in ("channel", "supergroup", "group"):
        return None, "bad_type"
    if int(chat.id) >= 0:
        return None, "bad_type"

    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=TIPS_TARGET_CHECK_TIMEOUT)
        bot_member = await asyncio.wait_for(bot.get_chat_member(chat.id, me.id), timeout=TIPS_TARGET_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return None, "unreachable"
    except (TelegramForbiddenError, TelegramBadRequest):
        return None, "bot_not_member"
    except Exception as ex:
        logging.warning("⚠️ [Tips] get_chat_member(bot) falló en %s: %s", chat.id, ex)
        return None, "unreachable"

    status = _tips_member_status(bot_member)
    if status in ("left", "kicked", "banned"):
        return None, "bot_not_member"
    if chat_type == "channel":
        if status not in ("administrator", "creator"):
            return None, "bot_not_admin"
        if status == "administrator" and getattr(bot_member, "can_post_messages", None) is not True:
            return None, "bot_cannot_post"
    else:
        if status == "restricted" and getattr(bot_member, "can_send_messages", None) is False:
            return None, "bot_cannot_post"
        if status == "member":
            perms = getattr(chat, "permissions", None)
            if perms is not None and getattr(perms, "can_send_messages", None) is False:
                return None, "bot_cannot_post"

    # 👑 Regla de Oro (se evalúa al final: los fallos del bot se explican aunque el usuario no sea el creador).
    if not is_super_admin(user_id):
        try:
            owner_ok = await asyncio.wait_for(is_legitimate_owner(bot, user_id, chat.id), timeout=TIPS_TARGET_CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            return None, "unreachable"
        except Exception:
            owner_ok = False
        if not owner_ok:
            return None, "not_admin"

    value = str(int(chat.id))
    aliases = {value.lower()}
    if getattr(chat, "username", None):
        aliases.add(f"@{chat.username}".lower())
        aliases.add(str(chat.username).lower())
    media_ok = True
    if status == "restricted":
        media_ok = getattr(bot_member, "can_send_photos", True) is not False and getattr(bot_member, "can_send_videos", True) is not False
    return {"value": value, "title": chat.title or value, "chat_id": int(chat.id), "aliases": aliases,
            "chat_type": chat_type, "media_ok": media_ok}, None


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
        "not_admin": ("⚠️ Solo el <b>creador</b> del canal destino puede vincularlo.",
                      "⚠️ Only the <b>creator</b> of the target channel can link it."),
        "duplicate": ("⚠️ Ese canal ya está vinculado.", "⚠️ That channel is already linked."),
        "limit": ("⚠️ Límite de canales destino alcanzado para tu nivel ({n}/{limit}).",
                  "⚠️ Target channel limit reached for your tier ({n}/{limit})."),
        "save_failed": ("⚠️ No pude registrar el canal. Inténtalo de nuevo.", "⚠️ I couldn't save the channel. Please try again."),
        "bot_not_member": ("⚠️ El bot <b>no es miembro</b> del destino (o fue expulsado). Añádelo como administrador y vuelve a intentarlo.",
                           "⚠️ The bot is <b>not a member</b> of the target (or was removed). Add it as an administrator and try again."),
        "unreachable": ("⏳ Telegram no respondió a tiempo al verificar el destino. No se guardó nada; inténtalo de nuevo.",
                        "⏳ Telegram did not answer in time while checking the target. Nothing was saved; please try again."),
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


_TIPS_PERMANENT_ERRORS = ("chat not found", "bot was kicked", "bot is not a member", "not enough rights",
                          "have no rights", "chat_write_forbidden", "need administrator rights", "channel_private",
                          "chat_admin_required", "bot was blocked", "group chat was deactivated", "peer_id_invalid")


def _tips_is_permanent_failure(ex: BaseException) -> bool:
    """Fallos que no se arreglan reintentando: el destino se pausa en vez de fallar en cada difusión."""
    if isinstance(ex, TelegramForbiddenError):
        return True
    text = str(ex).lower()
    return isinstance(ex, TelegramBadRequest) and any(k in text for k in _TIPS_PERMANENT_ERRORS)


async def dispatch_tips_broadcast(bot: Bot, tier: str, cfg: dict, lang: str, active_targets: list, group_id: int = 0,
                                  target_ids: Optional[dict] = None):
    """
    Despacha la publicación a los canales activos con botón inline de Stars. Devuelve (enviados:int, fallidos:list[str]).
    v30 · Cada destino se aísla (time-out propio, ningún fallo corta el bucle) y los fallos PERMANENTES (bot expulsado,
    sin permiso de publicar, canal inexistente) pausan el destino (is_active=0) para no repetir el error; el informe
    los marca con ⏸️. Los destinos con valor corrupto se omiten sin llamar a Telegram.
    """
    text, media_id, media_type = build_tips_publication(tier, cfg, lang)

    bot_username = "BunkerBot"
    with contextlib.suppress(Exception):
        bot_username = (await asyncio.wait_for(bot.get_me(), timeout=TIPS_TARGET_CHECK_TIMEOUT)).username or bot_username

    # 🌟 Construcción del botón inline interactivo para enviar la propina con Stars
    tip_btn_label = tr(lang, "⭐ Enviar Propina en Stars", "⭐ Send Stars Tip")
    tip_url = f"https://t.me/{bot_username}?start=tip_{group_id}" if group_id else f"https://t.me/{bot_username}?start=true"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tip_btn_label, url=tip_url)]
    ])

    sent, failed = 0, []
    for value in active_targets:
        raw = str(value or "").strip()
        if _TIPS_NUMERIC_ID_RE.match(raw):
            chat = int(raw)
        elif _TIPS_USERNAME_RE.match(raw.lstrip("@")):
            chat = "@" + raw.lstrip("@")
        else:
            logging.warning(f"⚠️ [Tips Broadcast] Destino con formato inválido omitido: {raw!r}")
            failed.append(raw or "?")
            continue
        for attempt in (1, 2):
            try:
                await asyncio.wait_for(_tips_send_one(bot, chat, text, media_id, media_type, reply_markup=keyboard),
                                       timeout=TIPS_TARGET_CHECK_TIMEOUT * 2)
                sent += 1
                break
            except TelegramRetryAfter as ex:
                if attempt == 2:
                    failed.append(raw)
                    break
                await asyncio.sleep(min(ex.retry_after, 30) + 1)
            except TelegramMigrateToChat as ex:
                # El grupo pasó a supergrupo: se publica en el nuevo id (el destino guardado se re-vincula a mano).
                chat = int(ex.migrate_to_chat_id)
                if attempt == 2:
                    failed.append(raw)
            except asyncio.TimeoutError:
                if attempt == 2:
                    logging.warning(f"⏱️ [Tips Broadcast] Time-out publicando en {raw}.")
                    failed.append(raw)
            except Exception as ex:
                logging.warning(f"⚠️ [Tips Broadcast] No se pudo enviar al canal {raw}: {ex}")
                paused = False
                target_id = (target_ids or {}).get(value)
                if group_id and target_id is not None and _tips_is_permanent_failure(ex):
                    try:
                        from database.database import set_group_tip_target_active
                        paused = await set_group_tip_target_active(int(group_id), int(target_id), False)
                    except Exception as db_ex:
                        logging.error(f"❌ [Tips Broadcast] No se pudo pausar el destino {raw}: {db_ex}")
                failed.append(f"⏸️ {raw}" if paused else raw)
                break
        await asyncio.sleep(0.05)
    return sent, failed


def get_payment_keyboard(group_id: int, lang: str, tier_level: str = "pro", chat_type: str = "g"):
    """
    Botonera de cobro EXCLUSIVA en Telegram Stars (XTR): un único botón genera la factura
    nativa in-app (sendInvoice con currency=XTR). Sin enlaces a pasarelas externas ni precios
    fiat, conforme a Telegram, Apple App Store (3.1.1) y Google Play Payments.
    """
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    stars_price = f"{plan_price_stars(tier_level)} XTR"
    stars_label = f"⭐ Pagar con Stars ({stars_price})" if lang == "es" else f"⭐ Pay with Stars ({stars_price})"

    keyboard_rows = [
        [InlineKeyboardButton(text=stars_label, callback_data=f"inv_{tier_level}_{group_id}_{lang}")]
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    st_tg = "🟢" if await get_antispam_filter(group_id, "tg_links") == 1 else "🔴"
    st_fwd = "🟢" if await get_antispam_filter(group_id, "forwards") == 1 else "🔴"
    st_q = "🟢" if await get_antispam_filter(group_id, "quotes") == 1 else "🔴"
    st_web = "🟢" if await get_antispam_filter(group_id, "web_links") == 1 else "🔴"
    del_st = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    return t["antispam_main_title"].format(st_tg=st_tg, st_fwd=st_fwd, st_q=st_q, st_web=st_web, st_del=del_st)


async def get_antispam_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 /ban", callback_data=f"cmd_ban_{group_id}_{lang}"), InlineKeyboardButton(text="👢 /kick", callback_data=f"cmd_kick_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🔇 /mute", callback_data=f"cmd_mute_{group_id}_{lang}"), InlineKeyboardButton(text="🔊 /unmute", callback_data=f"cmd_unmute_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_add_wl"], callback_data=f"cmd_wl_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_add_bl"], callback_data=f"cmd_bl_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


def get_time_selection_keyboard(action_name: str, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
        [InlineKeyboardButton(text=tr(lang, "🎮 Anuncio de Gamificación", "🎮 Gamification Announcement"), callback_data=f"gami_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


async def send_clone_panel(bot: Bot, user, group_id: int, lang: str) -> None:
    """v17 · Panel 🧬 Clon & Centinela como mensaje nuevo (deep link gclone_ desde la Mini App)."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    tier = await get_effective_group_tier(group_id, user.id)
    try:
        g_name = html.escape((await bot.get_chat(group_id)).title or "")
    except Exception:
        g_name = tr(lang, "Comunidad", "Community")
    clone_info = await get_bot_clone(user.id, group_id)
    has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
    status = tr(lang, "Operativo 🟢", "Active 🟢") if has_clone else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
    session_info = await get_owner_session(user.id, group_id)
    sentinel_status = tr(lang, "Conectado 🟢", "Connected 🟢") if session_info else tr(lang, "No Configurado 🔴", "Not Configured 🔴")
    chat_kind = await resolve_chat_kind(bot, group_id)
    clone_text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
    clone_kb = await get_clone_keyboard(group_id, user.id, lang, chat_type=chat_kind)
    if not await render_card(bot, user.id, "sentinel", lang, clone_kb, caption=clone_text):
        await bot.send_message(user.id, clone_text, reply_markup=clone_kb, parse_mode="HTML")


async def get_clone_keyboard(group_id: int, user_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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


# raidv_: puerta del Escudo Anti-Raid (handlers/groups.py) — se cede igual que los enlaces de cobro.
_PAYMENT_ROUTES_RE = re.compile(r"^/(start\s+(sub_|vipmic_|chanplan_|speaker_|tip_|raidv_|appeal_)|(pro|ultra)(@\w+)?(\s|$))")


def _not_payments_route(message: Message) -> bool:
    """Filtro: cede a handlers/payments.py los pagos y enlaces de cobro, sin depender del orden de include_router."""
    return not message.successful_payment and not _PAYMENT_ROUTES_RE.match(message.text or "")


@router.message(CommandStart(), F.chat.type == "private", _not_payments_route)
async def cmd_start(message: Message, bot: Bot, command: CommandObject):
    try:
        try:
            await message.delete()
        except Exception:
            pass
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "BunkerBot"
        role = "CLON" if is_clone_bot(bot) else "MAESTRO"
        logging.info(f"🚀 [cmd_start INICIO] {role} | Bot ID: {bot_info.id} (@{bot_username}) | Usuario: {message.from_user.id}")

        # 🧹 Solo liberar flujos si el usuario NO está ingresando código de verificación o 2FA
        user_key = (bot.id, message.from_user.id)
        if user_key not in SENTINEL_CODE_STATES and user_key not in SENTINEL_2FA_STATES:
            clear_user_states(bot.id, message.from_user.id)
            try:
                await cancel_phone_auth(message.from_user.id)
            except Exception:
                pass

        lang = user_lang(message.from_user)
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
        
        # Registro del usuario y despacho de la bienvenida / deep-links.

        try:
            await get_or_create_user(message.from_user.id, message.from_user.username or "Sin username", message.from_user.full_name)
        except Exception as db_ex:
            logging.error(f"❌ [cmd_start DB Error]: {db_ex}")

        # ⚖️ v24 · Apelaciones de baneo que no pudieron entregarse por privado (el usuario aún no había abierto el bot).
        try:
            _deliver_pending_appeals = _ban_appeals_module().deliver_pending
            _appeal_task = asyncio.create_task(_deliver_pending_appeals(bot, message.from_user))
            _SETTINGS_EMIT_TASKS.add(_appeal_task)
            _appeal_task.add_done_callback(_SETTINGS_EMIT_TASKS.discard)
        except Exception as appeal_ex:
            logging.debug(f"[Apelaciones] Entrega diferida no lanzada: {appeal_ex}")

        # 🎮 Deep link desde /gamification en el grupo: abre el panel del anuncio recurrente.
        if command.args and command.args.startswith("gami_"):
            try:
                gami_chat_id = int(command.args.split("_", 1)[1])
            except (ValueError, IndexError):
                gami_chat_id = 0
            if gami_chat_id:
                if not await verify_admin_privileges_msg(message, bot, gami_chat_id, perm="can_manage_broadcast"):
                    return
                await send_gamification_panel(bot, message.from_user.id, gami_chat_id, lang)
                return

        # 🧬 v17 · Deep link desde la Mini App («Configurar en el bot»): abre directamente el panel Clon & Centinela.
        # La vinculación de tokens y String Sessions solo ocurre aquí, en el chat privado, con flujos guiados.
        if command.args and command.args.startswith("gclone_"):
            try:
                clone_chat_id = int(command.args.split("_", 1)[1])
            except (ValueError, IndexError):
                clone_chat_id = 0
            if clone_chat_id:
                # perm=None: exclusivo del Dueño legítimo y del Arquitecto (las credenciales nunca se delegan).
                if not await verify_admin_privileges_msg(message, bot, clone_chat_id):
                    return
                await send_clone_panel(bot, message.from_user, clone_chat_id, lang)
                return

        if command.args and command.args.startswith("gset_"):
            try:
                group_id = int(command.args.split("_")[1])
                if not await verify_admin_privileges_msg(message, bot, group_id, perm="any"):
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
    clear_user_states(bot.id, message.from_user.id)
    lang = user_lang(message.from_user)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    await _dismiss_reply_keyboard(message)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_main_menu"], callback_data=f"menu_main_{lang}")]
    ])
    await message.answer(f"✅ {t['op_canceled']}{PERIMETER_SIGNATURE}", reply_markup=kb, parse_mode="HTML")

@router.message(Command("language", "lang", "idioma"), F.chat.type == "private")
async def cmd_language(message: Message, bot: Bot):
    """🌐 v21 · Selector de idioma persistente (🇺🇸 EN por defecto · 🇪🇸 ES)."""
    lang = user_lang(message.from_user)
    text = ("🌐 <b>Language</b>\n\nChoose the language for the bot menus, notices and the Mini App. "
            "Your choice is saved and applied everywhere."
            if lang == "en" else
            "🌐 <b>Idioma</b>\n\nElige el idioma de los menús, avisos del bot y la Mini App. "
            "Tu elección se guarda y se aplica en todo el ecosistema.")
    await message.answer(text + PERIMETER_SIGNATURE, reply_markup=language_selector_keyboard(lang, f"menu_main_{lang}"),
                         parse_mode="HTML")


@router.callback_query(F.data.startswith("setlang_"))
async def cb_set_language(callback: CallbackQuery, bot: Bot):
    """Guarda el idioma elegido y repinta el selector y el menú principal en el nuevo idioma al instante."""
    lang = await remember_user_lang(callback.from_user, (callback.data or "").split("_", 1)[-1])
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    await callback.answer("✅ Language: English" if lang == "en" else "✅ Idioma: Español")
    try:
        await callback.message.edit_text(
            t["welcome"].format(name=html.escape(callback.from_user.full_name or "")),
            reply_markup=get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot),
                                           user_id=callback.from_user.id),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await callback.message.answer(t["welcome"].format(name=html.escape(callback.from_user.full_name or "")),
                                      reply_markup=get_main_keyboard((await bot.get_me()).username, lang,
                                                                     is_clone=is_clone_bot(bot), user_id=callback.from_user.id),
                                      parse_mode="HTML")


@router.message(Command("aidiag"), F.chat.type == "private")
async def cmd_support_ai_diagnostics(message: Message):
    """
    🩺 v32 · Diagnóstico del Soporte IA (solo Arquitectos): SDK, cliente, modelo, disyuntores abiertos y una llamada real
    mínima a Mistral. Explica en una línea por qué el asistente estaría usando el respondedor local. Nunca muestra la key.
    """
    if not is_super_admin(message.from_user.id):
        return
    wait = await message.answer("🩺 <i>Probando Mistral AI…</i>", parse_mode="HTML")
    try:
        diag = await perimeter_ops.support_ai_diagnostics(run_probe=True)
    except Exception as ex:
        diag = {"ok": False, "stage": "internal", "detail": f"{type(ex).__name__}: {ex}"}
    st = diag.get("status") or {}
    lines = [
        "🩺 <b>Diagnóstico del Soporte IA</b>", "",
        f"• Resultado: <b>{'🟢 Mistral responde' if diag.get('ok') else '🔴 Mistral NO responde'}</b>",
        f"• Etapa: <code>{html.escape(str(diag.get('stage')))}</code>",
        f"• SDK mistralai: <code>{html.escape(str(st.get('sdk_version') or '—'))}</code>",
        f"• Cliente: {'🟢 listo' if st.get('client_ready') else '🔴 no inicializado'} · API key: "
        f"{'presente' if st.get('key_present') else 'AUSENTE'}",
        f"• Modelo: <code>{html.escape(str(st.get('model') or '—'))}</code>",
    ]
    if st.get("init_error"):
        lines.append(f"• Error de arranque: <code>{html.escape(str(st['init_error'])[:300])}</code>")
    if st.get("open_breakers"):
        lines.append(f"• Disyuntores abiertos (s): <code>{html.escape(str(st['open_breakers']))}</code>")
    if diag.get("latency_ms") is not None:
        lines.append(f"• Latencia: <b>{diag['latency_ms']} ms</b> · muestra: <code>{html.escape(str(diag.get('sample')))}</code>")
    if diag.get("detail"):
        lines.append(f"• Detalle: <code>{html.escape(str(diag['detail'])[:400])}</code>")
    if diag.get("kb_chars"):
        lines.append(f"• Base de conocimientos: <b>{diag['kb_chars']}</b> caracteres")
    text = "\n".join(lines) + PERIMETER_SIGNATURE
    try:
        await wait.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        await message.answer(text, parse_mode="HTML")


@router.message(Command("supportkb"), F.chat.type == "private")
async def cmd_support_directives(message: Message, command: CommandObject):
    """
    👑 v20 · Directivas del Asistente de Soporte IA (solo Arquitectos).
    /supportkb → muestra · /supportkb <texto> → reemplaza · /supportkb off → borra.
    """
    if not is_super_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg:
        current = await perimeter_ops.get_support_directives(force=True)
        body = html.escape(current) if current else "<i>(sin directivas: se usa solo la base de conocimientos)</i>"
        await message.answer(f"🤖 <b>Directivas del Soporte IA</b>\n\n{body[:3500]}{PERIMETER_SIGNATURE}", parse_mode="HTML")
        return
    saved = await perimeter_ops.set_support_directives("" if arg.lower() == "off" else arg, updated_by=message.from_user.id)
    await message.answer(
        ("✅ Directivas actualizadas" if saved else "🧹 Directivas borradas") + f" ({len(saved)} caracteres).{PERIMETER_SIGNATURE}",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    return


# --------------------------------------------------------------------------
# 📡 v30 · AUTODIAGNÓSTICO PREVENTIVO DE PERMISOS AL SINCRONIZAR CANALES
# --------------------------------------------------------------------------
# Un canal solo se registra si el bot tiene, EN ESTE MOMENTO, los permisos que el Estudio necesita. Antes bastaba
# con ser administrador: un admin sin «Publicar» quedaba vinculado y luego fallaban difusiones, planes y anuncios.
CHSYNC_CRITICAL_RIGHTS = (
    ("can_post_messages", "📝 Publicar mensajes", "📝 Post messages"),
    ("can_edit_messages", "✏️ Editar mensajes de otros", "✏️ Edit messages of others"),
    ("can_delete_messages", "🗑️ Eliminar mensajes de otros", "🗑️ Delete messages of others"),
    ("can_invite_users", "🔗 Invitar usuarios mediante enlace", "🔗 Invite users via link"),
)
CHSYNC_RECOMMENDED_RIGHTS = (
    ("can_manage_video_chats", "🎙️ Gestionar transmisiones en directo", "🎙️ Manage live streams"),
)
CHSYNC_CHECK_TIMEOUT = 8.0


def _chsync_status(member) -> str:
    return str(getattr(getattr(member, "status", None), "value", getattr(member, "status", "")) or "").lower()


async def diagnose_channel_sync(bot: Bot, user_id: int, channel_id: int) -> dict:
    """
    Verificación atómica (una sola pasada, en paralelo y con time-out) de: membresía y rango del bot, cada
    permiso crítico, tipo de chat y propiedad del usuario. NO escribe nada: el registro solo ocurre si ok=True.
    → {ok, error, title, missing: [(key, es, en)], recommended: [...]}
    error ∈ None | 'bot_not_member' | 'bot_not_admin' | 'missing_rights' | 'not_owner' | 'not_channel' | 'unreachable'
    """
    result = {"ok": False, "error": None, "title": "", "missing": [], "recommended": []}
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=CHSYNC_CHECK_TIMEOUT)
        owner_check = (bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                       if not is_super_admin(user_id) else asyncio.sleep(0, result=None))
        chat_obj, bot_member, user_member = await asyncio.wait_for(asyncio.gather(
            bot.get_chat(channel_id),
            bot.get_chat_member(chat_id=channel_id, user_id=me.id),
            owner_check,
            return_exceptions=True,
        ), timeout=CHSYNC_CHECK_TIMEOUT)
    except Exception as ex:
        logging.warning(f"⚠️ [Sync Canales] Diagnóstico sin respuesta de Telegram en {channel_id}: {ex}")
        result["error"] = "unreachable"
        return result

    if not isinstance(chat_obj, Exception):
        result["title"] = chat_obj.title or ""
        if str(getattr(getattr(chat_obj, "type", None), "value", getattr(chat_obj, "type", ""))) != "channel":
            result["error"] = "not_channel"
            return result
    if isinstance(bot_member, Exception):
        text = str(bot_member).lower()
        result["error"] = "bot_not_member" if any(k in text for k in ("not found", "forbidden", "not a member", "kicked")) else "unreachable"
        return result
    status = _chsync_status(bot_member)
    if status in ("left", "kicked", "banned"):
        result["error"] = "bot_not_member"
        return result
    if status not in ("administrator", "creator"):
        result["error"] = "bot_not_admin"
        return result
    if status == "administrator":
        result["missing"] = [r for r in CHSYNC_CRITICAL_RIGHTS if getattr(bot_member, r[0], None) is not True]
        result["recommended"] = [r for r in CHSYNC_RECOMMENDED_RIGHTS if getattr(bot_member, r[0], None) is not True]
    if result["missing"]:
        result["error"] = "missing_rights"
        return result
    if not is_super_admin(user_id):
        if isinstance(user_member, Exception) or _chsync_status(user_member) != "creator":
            result["error"] = "not_owner"
            return result
    result["ok"] = True
    return result


def _chsync_report(lang: str, t: dict, diag: dict, channel_id: int, bot_username: str) -> str:
    title = html.escape(diag.get("title") or str(channel_id))
    err = diag.get("error")
    if err == "missing_rights":
        lines = "\n".join(f"  ❌ {tr(lang, es, en)}" for _, es, en in diag["missing"])
        return tr(lang,
                  f"🛠️ <b>Autodiagnóstico de permisos</b>\n\n<b>{title}</b> no se registró: el bot es administrador pero le faltan "
                  f"permisos críticos:\n\n{lines}\n\nAbre <i>Administradores → @{bot_username}</i>, activa los permisos marcados "
                  "y pulsa «🔄 Volver a verificar».",
                  f"🛠️ <b>Permission self-check</b>\n\n<b>{title}</b> was not registered: the bot is an admin but lacks critical "
                  f"permissions:\n\n{lines}\n\nOpen <i>Administrators → @{bot_username}</i>, enable the marked permissions "
                  "and tap “🔄 Check again”.") + PERIMETER_SIGNATURE
    if err == "bot_not_member":
        return tr(lang,
                  f"⚠️ <b>El bot no está en {title}</b>\n\nAñade a @{bot_username} como administrador del canal y pulsa «🔄 Volver a verificar».",
                  f"⚠️ <b>The bot is not in {title}</b>\n\nAdd @{bot_username} as a channel administrator and tap “🔄 Check again”.") + PERIMETER_SIGNATURE
    if err == "not_channel":
        return tr(lang, "⚠️ Ese chat no es un canal. Usa el panel de grupos para vincular grupos.",
                  "⚠️ That chat is not a channel. Use the groups panel to link groups.") + PERIMETER_SIGNATURE
    if err == "unreachable":
        return tr(lang, "⏳ Telegram no respondió a tiempo al verificar el canal. No se registró nada; inténtalo de nuevo en unos segundos.",
                  "⏳ Telegram did not answer in time while checking the channel. Nothing was registered; try again in a few seconds.") + PERIMETER_SIGNATURE
    if err == "not_owner":
        return t["chsync_not_owner"]
    return t["chsync_bot_not_admin"].format(title=title, bot_username=bot_username)


async def run_channel_sync(bot: Bot, user, channel_id: int, lang: str, fallback_title: str = "") -> tuple:
    """Diagnostica y, solo si todo está en verde, registra. → (texto, teclado, registrado:bool)."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    bot_username = "BunkerBot"
    with contextlib.suppress(Exception):
        bot_username = (await bot.get_me()).username or bot_username
    diag = await diagnose_channel_sync(bot, user.id, channel_id)
    title = diag.get("title") or fallback_title or str(channel_id)
    back_rows = [
        [InlineKeyboardButton(text=t["btn_sync_channels"], callback_data=f"menu_chsync_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")],
    ]
    if not diag["ok"]:
        diag["title"] = title
        rows = list(back_rows)
        if diag["error"] in ("missing_rights", "bot_not_member", "bot_not_admin", "unreachable"):
            rows.insert(0, [InlineKeyboardButton(text=tr(lang, "🔄 Volver a verificar", "🔄 Check again"),
                                                 callback_data=f"chsyncchk_{channel_id}_{lang}")])
        logging.info(f"⛔ [Sync Canales] user={user.id} channel={channel_id} rechazado: {diag['error']} "
                     f"{[r[0] for r in diag['missing']]}")
        return _chsync_report(lang, t, diag, channel_id, bot_username), InlineKeyboardMarkup(inline_keyboard=rows), False

    CHAT_KIND_CACHE[channel_id] = "c"
    CHANNEL_SYNC_CACHE.setdefault(user.id, {})[channel_id] = title
    await registry_upsert_channel(user.id, channel_id, title)
    logging.info(f"✅ [Sync Canales] user={user.id} sincronizó channel={channel_id} ('{title}') con permisos verificados.")
    text = t["chsync_ok"].format(title=html.escape(title))
    if diag["recommended"]:
        rec = ", ".join(tr(lang, es, en) for _, es, en in diag["recommended"])
        text += "\n\n" + tr(lang, f"💡 Recomendado (opcional): {rec}.", f"💡 Recommended (optional): {rec}.")
    ok_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_studio"], callback_data=f"cpanel_{channel_id}_{lang}")],
        *back_rows,
    ])
    return text, ok_kb, True


@router.message(F.chat.type == "private", F.chat_shared)
async def handle_channel_shared(message: Message, bot: Bot):
    """
    Sincronización Activa de Canales: recibe el canal elegido en el selector nativo de Telegram y, v30, ejecuta el
    autodiagnóstico de permisos ANTES de guardar (membresía, rango, permisos críticos y propiedad del usuario).
    """
    user = message.from_user
    lang = user_lang(user)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    shared = message.chat_shared

    await _dismiss_reply_keyboard(message)

    if getattr(shared, "request_id", None) != CHANNEL_SYNC_REQUEST_ID:
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_sync_channels"], callback_data=f"menu_chsync_{lang}")],
            [InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")]
        ])
        resp = await message.answer(t["chsync_stale"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([resp], delay=60)
        return

    status = await message.answer(tr(lang, "🛠️ <b>Verificando permisos del bot…</b>", "🛠️ <b>Checking the bot's permissions…</b>"),
                                  parse_mode="HTML")
    text, kb, ok = await run_channel_sync(bot, user, shared.chat_id, lang, getattr(shared, "title", None) or "")
    try:
        await status.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    if not ok:
        fire_and_forget_auto_delete([status], delay=300)


@router.callback_query(F.data.startswith("chsyncchk_"))
async def cb_channel_sync_recheck(callback: CallbackQuery, bot: Bot):
    """🔄 Volver a verificar: repite el autodiagnóstico completo (incluida la propiedad) sin reabrir el selector."""
    data = (callback.data or "").split("_")
    try:
        channel_id = int(data[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    lang = lang_from_callback(callback, data)
    await callback.answer(tr(lang, "🛠️ Verificando…", "🛠️ Checking…"))
    text, kb, _ = await run_channel_sync(bot, callback.from_user, channel_id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

# ==========================================================
# 🎮 CONSOLA: ANUNCIOS RECURRENTES DE GAMIFICACIÓN INTERACTIVA
# ==========================================================
GAMI_INTERVAL_OPTIONS = {
    "pro": (180, 360, 720, 1440, 2880),                 # PRO: cada ≥ 3 h
    "ultra_pro": (30, 60, 120, 180, 360, 720, 1440),    # ULTRA PRO: cada ≥ 30 min
}
GAMI_AUTODEL_OPTIONS = (0, 300, 1800, 3600, 21600, 86400)

GAMI_TEXTS = {
    "es": {
        "title": "🎮 <b>Anuncio Recurrente de Gamificación</b>",
        "locked": (
            "🎮 <b>Anuncio Recurrente de Gamificación</b>\n\n"
            "🔒 Esta función está disponible a partir del plan <b>PRO</b>:\n"
            "• <b>PRO ⭐</b> — texto bilingüe y botón interactivo, cada 3 h o más.\n"
            "• <b>ULTRA PRO 💎</b> — además multimedia (foto, video o GIF) y frecuencia desde 30 min."
        ),
        "body": (
            "🎮 <b>Anuncio Recurrente de Gamificación</b>\n\n"
            "• <b>Comunidad:</b> {chat}\n"
            "• <b>Licencia:</b> {tier}\n"
            "• <b>Estado:</b> {status}\n"
            "• <b>Frecuencia:</b> cada {interval}\n"
            "• <b>Idioma de la tarjeta:</b> {card_lang}\n"
            "• <b>Texto ES:</b> {text_es}\n"
            "• <b>Texto EN:</b> {text_en}\n"
            "• <b>Multimedia:</b> {media}\n"
            "• <b>Botón:</b> {button}\n"
            "• <b>Auto-borrado:</b> {autodel}\n"
            "• <b>Último envío:</b> {last}\n"
            "• <b>Próximo envío:</b> {next}\n\n"
            "<i>{hint}</i>"
        ),
        "hint_pro": "Plan PRO: frecuencia mínima de 3 h. El multimedia requiere ULTRA PRO.",
        "hint_ultra": "ULTRA PRO: multimedia y frecuencia desde 30 min habilitados.",
        "status_on": "🟢 Activo",
        "status_off": "🔴 Inactivo",
        "status_failing": "🟠 Reintentando ({n} fallos)",
        "custom": "✅ Personalizado",
        "default": "⚪ Plantilla oficial",
        "none": "—",
        "no_button": "Sin botón",
        "autodel_off": "Desactivado",
        "never": "Aún no enviado",
        "lang_es": "🇪🇸 Español",
        "lang_en": "🇬🇧 English",
        "lang_both": "🌐 Bilingüe (ES + EN)",
        "media_photo": "🖼️ Foto",
        "media_video": "🎬 Video",
        "media_animation": "🎞️ GIF",
        "media_locked": "🔒 Solo ULTRA PRO",
        "btn_activate": "🟢 Activar anuncio",
        "btn_deactivate": "🔴 Desactivar anuncio",
        "btn_interval": "⏱️ Frecuencia",
        "btn_cardlang": "🌐 Idioma: {label}",
        "btn_text_es": "✍️ Texto ES",
        "btn_text_en": "✍️ Texto EN",
        "btn_media": "🖼️ Multimedia",
        "btn_media_del": "🗑️ Quitar multimedia",
        "btn_btn_es": "🔘 Botón ES",
        "btn_btn_en": "🔘 Botón EN",
        "btn_btn_url": "🔗 Enlace del botón",
        "btn_btn_del": "🗑️ Quitar botón",
        "btn_autodel": "🧹 Auto-borrado",
        "btn_preview": "👁️ Vista previa",
        "btn_send_now": "🚀 Publicar ahora",
        "btn_reset": "♻️ Restablecer",
        "btn_back_gami": "🔙 Volver al anuncio",
        "btn_cancel": "❌ Cancelar",
        "btn_upgrade_pro": "⭐ Mejorar a PRO",
        "btn_upgrade_ultra": "💎 Mejorar a ULTRA PRO",
        "interval_title": "⏱️ <b>Frecuencia del anuncio</b>\n\nElige cada cuánto se publicará la tarjeta interactiva:",
        "autodel_title": "🧹 <b>Auto-borrado</b>\n\nCada tarjeta publicada puede eliminarse sola tras el tiempo elegido:",
        "prompt_text_es": (
            "✍️ <b>Texto en Español</b>\n\n"
            "Escribe el texto del anuncio (máx. 3500 caracteres). Puedes usar el formato nativo de Telegram "
            "(negrita, cursiva, enlaces) o etiquetas HTML.\n\n"
            "Envía <code>-</code> para volver a la plantilla oficial."
        ),
        "prompt_text_en": (
            "✍️ <b>Texto en Inglés</b>\n\n"
            "Escribe la versión en inglés del anuncio (máx. 3500 caracteres). Formato nativo o HTML.\n\n"
            "Envía <code>-</code> para volver a la plantilla oficial."
        ),
        "prompt_media": (
            "🖼️ <b>Multimedia del anuncio</b>\n\n"
            "Envía una <b>foto</b>, un <b>video</b> o un <b>GIF</b>. Si el texto supera 1024 caracteres, "
            "se publicará el multimedia seguido del texto con el botón."
        ),
        "prompt_btn_es": "🔘 <b>Texto del botón (Español)</b>\n\nEscribe el nombre del botón (máx. 40 caracteres).\nEnvía <code>-</code> para usar el texto predeterminado.",
        "prompt_btn_en": "🔘 <b>Texto del botón (Inglés)</b>\n\nEscribe el nombre del botón en inglés (máx. 40 caracteres).\nEnvía <code>-</code> para usar el texto predeterminado.",
        "prompt_btn_url": (
            "🔗 <b>Enlace del botón</b>\n\n"
            "Formatos admitidos:\n"
            "• <code>@micanal</code> o <code>t.me/migrupo</code>\n"
            "• Invitaciones: <code>t.me/+AbCdEf…</code>\n"
            "• Webs: <code>https://misitio.com/promo</code>\n\n"
            "Se rechazan enlaces inseguros (javascript:, IPs locales, credenciales)."
        ),
        "saved": "✅ Cambios guardados.",
        "err_text_long": "⚠️ El texto supera 3500 caracteres. Acórtalo e inténtalo de nuevo.",
        "err_html": "⚠️ El formato HTML no es válido ({error}). Revisa las etiquetas e inténtalo de nuevo.",
        "err_need_text": "⚠️ Envía un mensaje de texto.",
        "err_media": "⚠️ Envía una foto, un video o un GIF.",
        "err_media_locked": "🔒 El multimedia del anuncio es exclusivo de ULTRA PRO.",
        "err_url": "⚠️ Enlace no válido o inseguro. Usa @usuario, un enlace t.me/… o una URL https:// pública.",
        "err_btn_text": "⚠️ El texto del botón no puede estar vacío.",
        "err_tier": "🔒 Necesitas un plan PRO o ULTRA PRO activo para esta comunidad.",
        "err_bot_absent": "⚠️ El bot no puede publicar en esta comunidad. Añádelo como administrador con permiso para enviar mensajes.",
        "toggled_on": "🟢 Anuncio activado: la primera tarjeta se publicará en el próximo ciclo (≈1 min).",
        "toggled_off": "🔴 Anuncio desactivado.",
        "send_now_ok": "🚀 Publicación programada para el próximo ciclo (≈1 min).",
        "send_now_inactive": "⚠️ Activa el anuncio antes de publicarlo.",
        "preview_sent": "👁️ Vista previa enviada a este chat.",
        "preview_error": "⚠️ No se pudo generar la vista previa: {error}",
        "reset_done": "♻️ Configuración restablecida.",
        "expired": "⏱️ La edición expiró. Abre de nuevo el panel del anuncio.",
    },
    "en": {
        "title": "🎮 <b>Recurring Gamification Announcement</b>",
        "locked": (
            "🎮 <b>Recurring Gamification Announcement</b>\n\n"
            "🔒 This feature is available from the <b>PRO</b> plan:\n"
            "• <b>PRO ⭐</b> — bilingual text and interactive button, every 3 h or more.\n"
            "• <b>ULTRA PRO 💎</b> — plus media (photo, video or GIF) and frequency from 30 min."
        ),
        "body": (
            "🎮 <b>Recurring Gamification Announcement</b>\n\n"
            "• <b>Community:</b> {chat}\n"
            "• <b>License:</b> {tier}\n"
            "• <b>Status:</b> {status}\n"
            "• <b>Frequency:</b> every {interval}\n"
            "• <b>Card language:</b> {card_lang}\n"
            "• <b>ES text:</b> {text_es}\n"
            "• <b>EN text:</b> {text_en}\n"
            "• <b>Media:</b> {media}\n"
            "• <b>Button:</b> {button}\n"
            "• <b>Auto-delete:</b> {autodel}\n"
            "• <b>Last sent:</b> {last}\n"
            "• <b>Next send:</b> {next}\n\n"
            "<i>{hint}</i>"
        ),
        "hint_pro": "PRO plan: minimum frequency of 3 h. Media requires ULTRA PRO.",
        "hint_ultra": "ULTRA PRO: media and frequency from 30 min enabled.",
        "status_on": "🟢 Active",
        "status_off": "🔴 Inactive",
        "status_failing": "🟠 Retrying ({n} failures)",
        "custom": "✅ Custom",
        "default": "⚪ Official template",
        "none": "—",
        "no_button": "No button",
        "autodel_off": "Disabled",
        "never": "Not sent yet",
        "lang_es": "🇪🇸 Español",
        "lang_en": "🇬🇧 English",
        "lang_both": "🌐 Bilingual (ES + EN)",
        "media_photo": "🖼️ Photo",
        "media_video": "🎬 Video",
        "media_animation": "🎞️ GIF",
        "media_locked": "🔒 ULTRA PRO only",
        "btn_activate": "🟢 Enable announcement",
        "btn_deactivate": "🔴 Disable announcement",
        "btn_interval": "⏱️ Frequency",
        "btn_cardlang": "🌐 Language: {label}",
        "btn_text_es": "✍️ ES text",
        "btn_text_en": "✍️ EN text",
        "btn_media": "🖼️ Media",
        "btn_media_del": "🗑️ Remove media",
        "btn_btn_es": "🔘 ES button",
        "btn_btn_en": "🔘 EN button",
        "btn_btn_url": "🔗 Button link",
        "btn_btn_del": "🗑️ Remove button",
        "btn_autodel": "🧹 Auto-delete",
        "btn_preview": "👁️ Preview",
        "btn_send_now": "🚀 Publish now",
        "btn_reset": "♻️ Reset",
        "btn_back_gami": "🔙 Back to announcement",
        "btn_cancel": "❌ Cancel",
        "btn_upgrade_pro": "⭐ Upgrade to PRO",
        "btn_upgrade_ultra": "💎 Upgrade to ULTRA PRO",
        "interval_title": "⏱️ <b>Announcement frequency</b>\n\nChoose how often the interactive card will be published:",
        "autodel_title": "🧹 <b>Auto-delete</b>\n\nEach published card can delete itself after the selected time:",
        "prompt_text_es": (
            "✍️ <b>Spanish text</b>\n\n"
            "Write the Spanish version of the announcement (max. 3500 characters). Native Telegram formatting "
            "(bold, italic, links) or HTML tags are supported.\n\n"
            "Send <code>-</code> to restore the official template."
        ),
        "prompt_text_en": (
            "✍️ <b>English text</b>\n\n"
            "Write the announcement text (max. 3500 characters). Native formatting or HTML.\n\n"
            "Send <code>-</code> to restore the official template."
        ),
        "prompt_media": (
            "🖼️ <b>Announcement media</b>\n\n"
            "Send a <b>photo</b>, a <b>video</b> or a <b>GIF</b>. If the text exceeds 1024 characters, "
            "the media will be posted followed by the text with the button."
        ),
        "prompt_btn_es": "🔘 <b>Button text (Spanish)</b>\n\nWrite the Spanish button label (max. 40 characters).\nSend <code>-</code> to use the default label.",
        "prompt_btn_en": "🔘 <b>Button text (English)</b>\n\nWrite the button label (max. 40 characters).\nSend <code>-</code> to use the default label.",
        "prompt_btn_url": (
            "🔗 <b>Button link</b>\n\n"
            "Supported formats:\n"
            "• <code>@mychannel</code> or <code>t.me/mygroup</code>\n"
            "• Invites: <code>t.me/+AbCdEf…</code>\n"
            "• Websites: <code>https://mysite.com/promo</code>\n\n"
            "Unsafe links (javascript:, local IPs, credentials) are rejected."
        ),
        "saved": "✅ Changes saved.",
        "err_text_long": "⚠️ The text exceeds 3500 characters. Shorten it and try again.",
        "err_html": "⚠️ Invalid HTML formatting ({error}). Check the tags and try again.",
        "err_need_text": "⚠️ Please send a text message.",
        "err_media": "⚠️ Send a photo, a video or a GIF.",
        "err_media_locked": "🔒 Announcement media is exclusive to ULTRA PRO.",
        "err_url": "⚠️ Invalid or unsafe link. Use @username, a t.me/… link or a public https:// URL.",
        "err_btn_text": "⚠️ The button text cannot be empty.",
        "err_tier": "🔒 You need an active PRO or ULTRA PRO plan for this community.",
        "err_bot_absent": "⚠️ The bot cannot post in this community. Add it as an administrator allowed to send messages.",
        "toggled_on": "🟢 Announcement enabled: the first card will be published in the next cycle (≈1 min).",
        "toggled_off": "🔴 Announcement disabled.",
        "send_now_ok": "🚀 Publication scheduled for the next cycle (≈1 min).",
        "send_now_inactive": "⚠️ Enable the announcement before publishing it.",
        "preview_sent": "👁️ Preview sent to this chat.",
        "preview_error": "⚠️ Could not generate the preview: {error}",
        "reset_done": "♻️ Configuration reset.",
        "expired": "⏱️ The edit session expired. Open the announcement panel again.",
    },
}


def _gami_t(lang: str) -> dict:
    return GAMI_TEXTS.get(lang, GAMI_TEXTS["es"])


def _gami_tier_key(tier) -> str:
    normalized = str(tier or "free").strip().lower().replace("-", "_").replace(" ", "_")
    if "ultra" in normalized:
        return "ultra_pro"
    if normalized == "pro":
        return "pro"
    return "free"


def _gami_interval_label(minutes: int, lang: str) -> str:
    minutes = int(minutes or 0)
    if minutes and minutes % 1440 == 0:
        days = minutes // 1440
        return tr(lang, f"{days} día" + ("s" if days > 1 else ""), f"{days} day" + ("s" if days > 1 else ""))
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return tr(lang, f"{hours} h", f"{hours} h")
    return f"{minutes} min"


def _gami_autodel_label(seconds: int, lang: str) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return _gami_t(lang)["autodel_off"]
    return _gami_interval_label(seconds // 60, lang) if seconds % 60 == 0 else f"{seconds} s"


def _gami_card_lang_label(card_lang: Optional[str], lang: str) -> str:
    gt = _gami_t(lang)
    return {"es": gt["lang_es"], "en": gt["lang_en"], "both": gt["lang_both"]}.get(card_lang or "es", gt["lang_es"])


def _gami_cb(action: str, chat_id: Optional[int], lang: str, value=None) -> str:
    return f"gami_{action}_{chat_id}_{value}_{lang}" if value is not None else f"gami_{action}_{chat_id}_{lang}"


async def _gami_panel_text(bot: Bot, chat_id: int, lang: str, cfg: dict, tier_key: str) -> str:
    gt = _gami_t(lang)
    try:
        chat_title = html.escape((await bot.get_chat(chat_id)).title or str(chat_id))
    except Exception:
        chat_title = f"<code>{chat_id}</code>"

    if cfg.get("status") == 1 and int(cfg.get("fail_count") or 0) > 0:
        status = gt["status_failing"].format(n=int(cfg.get("fail_count") or 0))
    else:
        status = gt["status_on"] if cfg.get("status") == 1 else gt["status_off"]

    media_type = cfg.get("media_type")
    if tier_key != "ultra_pro":
        media = gt["media_locked"]
    elif cfg.get("media_id") and media_type:
        media = gt.get(f"media_{media_type}", media_type)
    else:
        media = gt["none"]

    if cfg.get("btn_url"):
        label = (cfg.get("btn_text_es") if lang == "es" else cfg.get("btn_text_en")) or cfg.get("btn_text_es") or cfg.get("btn_text_en") \
            or GAMI_DEFAULT_BUTTON_TEXTS.get(lang, GAMI_DEFAULT_BUTTON_TEXTS["es"])
        button = f"{html.escape(label)} → <code>{html.escape(cfg['btn_url'])}</code>"
    else:
        button = gt["no_button"]

    tier_label = {"ultra_pro": "ULTRA PRO 💎", "pro": "PRO ⭐"}.get(tier_key, "FREE")
    return gt["body"].format(
        chat=chat_title,
        tier=tier_label,
        status=status,
        interval=_gami_interval_label(cfg.get("interval_minutes") or GAMI_DEFAULT_INTERVAL_MINUTES, lang),
        card_lang=_gami_card_lang_label(cfg.get("card_lang"), lang),
        text_es=gt["custom"] if cfg.get("text_es") else gt["default"],
        text_en=gt["custom"] if cfg.get("text_en") else gt["default"],
        media=media,
        button=button,
        autodel=_gami_autodel_label(cfg.get("auto_delete_after") or 0, lang),
        last=f"{cfg['last_sent_at']} UTC" if cfg.get("last_sent_at") else gt["never"],
        next=f"{cfg['next_send_at']} UTC" if (cfg.get("status") == 1 and cfg.get("next_send_at")) else gt["none"],
        hint=gt["hint_ultra"] if tier_key == "ultra_pro" else gt["hint_pro"],
    ) + PERIMETER_SIGNATURE


async def get_gamification_keyboard(bot: Bot, chat_id: int, lang: str, cfg: dict, tier_key: str) -> InlineKeyboardMarkup:
    gt = _gami_t(lang)
    back = await origin_back_button(bot, chat_id, lang)
    if tier_key == "free":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=gt["btn_upgrade_pro"], callback_data=f"pay_pro_{chat_id}_{lang}")],
            [InlineKeyboardButton(text=gt["btn_upgrade_ultra"], callback_data=f"pay_ultra_{chat_id}_{lang}")],
            [back],
        ])

    rows = [
        [InlineKeyboardButton(
            text=gt["btn_deactivate"] if cfg.get("status") == 1 else gt["btn_activate"],
            callback_data=_gami_cb("toggle", chat_id, lang)
        )],
        [
            InlineKeyboardButton(text=gt["btn_interval"], callback_data=_gami_cb("intmenu", chat_id, lang)),
            InlineKeyboardButton(
                text=gt["btn_cardlang"].format(label=(cfg.get("card_lang") or "es").upper()),
                callback_data=_gami_cb("cardlang", chat_id, lang)
            ),
        ],
        [
            InlineKeyboardButton(text=gt["btn_text_es"], callback_data=_gami_cb("txtes", chat_id, lang)),
            InlineKeyboardButton(text=gt["btn_text_en"], callback_data=_gami_cb("txten", chat_id, lang)),
        ],
    ]
    if tier_key == "ultra_pro":
        media_row = [InlineKeyboardButton(text=gt["btn_media"], callback_data=_gami_cb("media", chat_id, lang))]
        if cfg.get("media_id"):
            media_row.append(InlineKeyboardButton(text=gt["btn_media_del"], callback_data=_gami_cb("delmedia", chat_id, lang)))
        rows.append(media_row)
    rows.append([
        InlineKeyboardButton(text=gt["btn_btn_es"], callback_data=_gami_cb("btnes", chat_id, lang)),
        InlineKeyboardButton(text=gt["btn_btn_en"], callback_data=_gami_cb("btnen", chat_id, lang)),
    ])
    url_row = [InlineKeyboardButton(text=gt["btn_btn_url"], callback_data=_gami_cb("btnurl", chat_id, lang))]
    if cfg.get("btn_url"):
        url_row.append(InlineKeyboardButton(text=gt["btn_btn_del"], callback_data=_gami_cb("delbtn", chat_id, lang)))
    rows.append(url_row)
    rows.append([InlineKeyboardButton(text=gt["btn_autodel"], callback_data=_gami_cb("delmenu", chat_id, lang))])
    rows.append([
        InlineKeyboardButton(text=gt["btn_preview"], callback_data=_gami_cb("preview", chat_id, lang)),
        InlineKeyboardButton(text=gt["btn_send_now"], callback_data=_gami_cb("sendnow", chat_id, lang)),
    ])
    rows.append([InlineKeyboardButton(text=gt["btn_reset"], callback_data=_gami_cb("reset", chat_id, lang))])
    rows.append([back])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _gami_build_panel(bot: Bot, chat_id: int, user_id: int, lang: str) -> tuple:
    tier_key = _gami_tier_key(await get_effective_group_tier(chat_id, user_id))
    if tier_key == "free":
        cfg = await get_gamification_announcement(chat_id)
        return _gami_t(lang)["locked"] + PERIMETER_SIGNATURE, await get_gamification_keyboard(bot, chat_id, lang, cfg, tier_key)
    cfg = await get_gamification_announcement(chat_id)
    return await _gami_panel_text(bot, chat_id, lang, cfg, tier_key), await get_gamification_keyboard(bot, chat_id, lang, cfg, tier_key)


async def send_gamification_panel(bot: Bot, user_id: int, chat_id: int, lang: str):
    """Envía el panel del anuncio como mensaje nuevo (deep link, /gamification o tras una edición)."""
    text, keyboard = await _gami_build_panel(bot, chat_id, user_id, lang)
    return await bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard, parse_mode="HTML")


def _gami_compose_for_tier(cfg: dict, tier_key: str) -> dict:
    card = compose_gamification_card(cfg)
    if tier_key != "ultra_pro":
        card["media_id"], card["media_type"] = None, None
    return card


async def _gami_bot_can_post(bot: Bot, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
    except Exception:
        return False
    status = str(getattr(member.status, "value", member.status)).lower()
    if status in ("left", "kicked", "banned"):
        return False
    if await resolve_chat_kind(bot, chat_id) == "c":
        return status == "creator" or bool(getattr(member, "can_post_messages", False))
    if status == "restricted":
        return bool(getattr(member, "can_send_messages", False))
    return True


@router.callback_query(F.data.startswith("gami_"))
async def cb_gamification_console(callback: CallbackQuery, bot: Bot):
    """Consola privada del anuncio recurrente de gamificación (ownership + licencia verificados)."""
    parts = (callback.data or "").split("_")
    if len(parts) < 4:
        await callback.answer()
        return
    action = parts[1]
    lang = parts[-1] if parts[-1] in ("es", "en") else user_lang(callback.from_user)
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    value = parts[3] if len(parts) >= 5 else None
    gt = _gami_t(lang)
    user_id = callback.from_user.id

    # Toda navegación libera conversaciones pendientes (mismo criterio que el resto de la consola).
    clear_user_states(bot.id, user_id)

    if not await verify_admin_privileges(callback, bot, chat_id):
        return

    tier_key = _gami_tier_key(await get_effective_group_tier(chat_id, user_id))
    if tier_key == "free" and action != "menu":
        await callback.answer(gt["err_tier"], show_alert=True)
        text, keyboard = await _gami_build_panel(bot, chat_id, user_id, lang)
        await safe_edit_text(callback, text, reply_markup=keyboard)
        return

    async def _refresh(alert: Optional[str] = None, show_alert: bool = False):
        text, keyboard = await _gami_build_panel(bot, chat_id, user_id, lang)
        await safe_edit_text(callback, text, reply_markup=keyboard)
        try:
            await callback.answer(alert or "", show_alert=show_alert)
        except Exception:
            pass

    def _cancel_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=gt["btn_cancel"], callback_data=_gami_cb("menu", chat_id, lang))]
        ])

    def _prompt(field: str, prompt_key: str):
        GAMI_STATES[(bot.id, user_id)] = {"chat_id": chat_id, "field": field, "lang": lang, "ts": time.time()}
        return safe_edit_text(callback, gt[prompt_key] + PERIMETER_SIGNATURE, reply_markup=_cancel_kb())

    if action == "menu":
        await _refresh()
        return

    if action == "toggle":
        cfg = await get_gamification_announcement(chat_id)
        if cfg.get("status") == 1:
            await set_gamification_announcement_field(chat_id, "status", 0)
            await _refresh(gt["toggled_off"])
            return
        if not await _gami_bot_can_post(bot, chat_id):
            await callback.answer(gt["err_bot_absent"], show_alert=True)
            return
        if not cfg.get("exists"):
            await set_gamification_announcement_field(chat_id, "card_lang", lang)
            await set_gamification_announcement_field(chat_id, "created_by", user_id)
        min_interval = GAMI_INTERVAL_OPTIONS[tier_key][0]
        if int(cfg.get("interval_minutes") or GAMI_DEFAULT_INTERVAL_MINUTES) < min_interval:
            await set_gamification_announcement_field(chat_id, "interval_minutes", min_interval)
        await set_gamification_announcement_field(chat_id, "status", 1)
        logging.info(f"🎮 [Gamificación] Anuncio activado en {chat_id} por {user_id}.")
        await _refresh(gt["toggled_on"], show_alert=True)
        return

    if action == "intmenu":
        cfg = await get_gamification_announcement(chat_id)
        current = int(cfg.get("interval_minutes") or GAMI_DEFAULT_INTERVAL_MINUTES)
        options = GAMI_INTERVAL_OPTIONS[tier_key]
        rows, row = [], []
        for minutes in options:
            mark = "✅ " if minutes == current else ""
            row.append(InlineKeyboardButton(
                text=f"{mark}{_gami_interval_label(minutes, lang)}",
                callback_data=_gami_cb("setint", chat_id, lang, minutes)
            ))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text=gt["btn_back_gami"], callback_data=_gami_cb("menu", chat_id, lang))])
        await safe_edit_text(callback, gt["interval_title"] + PERIMETER_SIGNATURE, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()
        return

    if action == "setint":
        try:
            minutes = int(value)  # pyright: ignore[reportArgumentType]  (None → TypeError capturado)
        except (TypeError, ValueError):
            await callback.answer()
            return
        # El valor llega en el callback (manipulable): solo se aceptan opciones de la licencia vigente.
        if minutes not in GAMI_INTERVAL_OPTIONS[tier_key]:
            await callback.answer(gt["err_tier"], show_alert=True)
            return
        await set_gamification_announcement_field(chat_id, "interval_minutes", minutes)
        await _refresh(gt["saved"])
        return

    if action == "cardlang":
        cfg = await get_gamification_announcement(chat_id)
        cycle = {"es": "en", "en": "both", "both": "es"}
        await set_gamification_announcement_field(chat_id, "card_lang", cycle.get(cfg.get("card_lang") or "es", "es"))
        await _refresh(gt["saved"])
        return

    if action == "delmenu":
        cfg = await get_gamification_announcement(chat_id)
        current = int(cfg.get("auto_delete_after") or 0)
        rows, row = [], []
        for seconds in GAMI_AUTODEL_OPTIONS:
            mark = "✅ " if seconds == current else ""
            row.append(InlineKeyboardButton(
                text=f"{mark}{_gami_autodel_label(seconds, lang)}",
                callback_data=_gami_cb("setdel", chat_id, lang, seconds)
            ))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text=gt["btn_back_gami"], callback_data=_gami_cb("menu", chat_id, lang))])
        await safe_edit_text(callback, gt["autodel_title"] + PERIMETER_SIGNATURE, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()
        return

    if action == "setdel":
        try:
            seconds = int(value)  # pyright: ignore[reportArgumentType]  (None → TypeError capturado)
        except (TypeError, ValueError):
            await callback.answer()
            return
        if seconds not in GAMI_AUTODEL_OPTIONS:
            await callback.answer()
            return
        await set_gamification_announcement_field(chat_id, "auto_delete_after", seconds)
        await _refresh(gt["saved"])
        return

    if action == "txtes":
        await _prompt("text_es", "prompt_text_es")
        await callback.answer()
        return
    if action == "txten":
        await _prompt("text_en", "prompt_text_en")
        await callback.answer()
        return
    if action == "btnes":
        await _prompt("btn_text_es", "prompt_btn_es")
        await callback.answer()
        return
    if action == "btnen":
        await _prompt("btn_text_en", "prompt_btn_en")
        await callback.answer()
        return
    if action == "btnurl":
        await _prompt("btn_url", "prompt_btn_url")
        await callback.answer()
        return
    if action == "media":
        if tier_key != "ultra_pro":
            await callback.answer(gt["err_media_locked"], show_alert=True)
            return
        await _prompt("media", "prompt_media")
        await callback.answer()
        return

    if action == "delmedia":
        await set_gamification_announcement_media(chat_id, None, None)
        await _refresh(gt["saved"])
        return

    if action == "delbtn":
        await clear_gamification_announcement_button(chat_id)
        await _refresh(gt["saved"])
        return

    if action == "preview":
        cfg = await get_gamification_announcement(chat_id)
        card = _gami_compose_for_tier(cfg, tier_key)
        try:
            from handlers.ecosystem import send_gamification_card
            await send_gamification_card(bot, user_id, card)
            await callback.answer(gt["preview_sent"])
        except Exception as ex:
            logging.warning(f"⚠️ [Gamificación] Vista previa fallida en {chat_id}: {ex}")
            await callback.answer(gt["preview_error"].format(error=str(ex)[:120]), show_alert=True)
        return

    if action == "sendnow":
        if await schedule_gamification_announcement_now(chat_id):
            await _refresh(gt["send_now_ok"], show_alert=True)
        else:
            await callback.answer(gt["send_now_inactive"], show_alert=True)
        return

    if action == "reset":
        await reset_gamification_announcement(chat_id)
        await _refresh(gt["reset_done"])
        return

    await callback.answer()


async def _handle_gamification_input(message: Message, bot: Bot, lang: str) -> None:
    """Procesa la respuesta del administrador a un prompt de la consola del anuncio."""
    user_id = message.from_user.id
    state = GAMI_STATES.get((bot.id, user_id)) or {}
    chat_id = state.get("chat_id")
    field = state.get("field")
    lang = state.get("lang") or lang
    gt = _gami_t(lang)
    retry_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gt["btn_cancel"], callback_data=_gami_cb("menu", chat_id, lang))]
    ])

    if not chat_id or not field or time.time() - state.get("ts", 0) > STATE_TTL_SECONDS:
        GAMI_STATES.pop((bot.id, user_id), None)
        resp = await message.answer(gt["expired"] + PERIMETER_SIGNATURE, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=30)
        return

    # Defensa en profundidad: la autorización y la licencia se verifican de nuevo al recibir el dato.
    if not await is_authorized_operator(bot, user_id, chat_id, "can_manage_broadcast"):
        GAMI_STATES.pop((bot.id, user_id), None)
        await message.answer(TEXTS.get(lang, TEXTS[DEFAULT_LANG])["owner_only_alert"])
        return
    tier_key = _gami_tier_key(await get_effective_group_tier(chat_id, user_id))
    if tier_key == "free":
        GAMI_STATES.pop((bot.id, user_id), None)
        await message.answer(gt["err_tier"] + PERIMETER_SIGNATURE, parse_mode="HTML")
        return

    async def _error(key: str, **fmt):
        resp = await message.answer(gt[key].format(**fmt) + PERIMETER_SIGNATURE, reply_markup=retry_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=45)

    raw_text = (message.text or "").strip()

    if field in ("text_es", "text_en"):
        if not message.text:
            await _error("err_need_text")
            return
        if raw_text == "-":
            new_value = None
        else:
            # Con formato nativo de Telegram se conserva vía html_text; sin entidades, se admite HTML manual.
            new_value = (message.html_text if message.entities else message.text).strip()
            if len(new_value) > GAMI_TEXT_MAX_CHARS:
                await _error("err_text_long")
                return
            try:
                probe = await bot.send_message(chat_id=user_id, text=new_value, parse_mode="HTML")
                fire_and_forget_auto_delete([probe], delay=1)
            except TelegramBadRequest as html_err:
                await _error("err_html", error=html.escape(str(html_err))[:200])
                return
        await set_gamification_announcement_field(chat_id, field, new_value)

    elif field in ("btn_text_es", "btn_text_en"):
        if not message.text:
            await _error("err_need_text")
            return
        if raw_text == "-":
            new_value = None
        else:
            new_value = normalize_announcement_button_text(raw_text)
            if not new_value:
                await _error("err_btn_text")
                return
        await set_gamification_announcement_field(chat_id, field, new_value)

    elif field == "btn_url":
        normalized = normalize_announcement_url(raw_text)
        if not normalized:
            await _error("err_url")
            return
        await set_gamification_announcement_field(chat_id, "btn_url", normalized)

    elif field == "media":
        if tier_key != "ultra_pro":
            GAMI_STATES.pop((bot.id, user_id), None)
            await _error("err_media_locked")
            return
        if message.animation:
            media_id, media_type = message.animation.file_id, "animation"
        elif message.photo:
            media_id, media_type = message.photo[-1].file_id, "photo"
        elif message.video:
            media_id, media_type = message.video.file_id, "video"
        else:
            await _error("err_media")
            return
        await set_gamification_announcement_media(chat_id, media_id, media_type)
    else:
        GAMI_STATES.pop((bot.id, user_id), None)
        return

    GAMI_STATES.pop((bot.id, user_id), None)
    logging.info(f"🎮 [Gamificación] Campo '{field}' actualizado en {chat_id} por {user_id}.")
    saved = await message.answer(gt["saved"], parse_mode="HTML")
    fire_and_forget_auto_delete([message, saved], delay=8)
    await send_gamification_panel(bot, user_id, chat_id, lang)


@router.message(F.chat.type == "private", _not_payments_route)
async def handle_private_inputs(message: Message, bot: Bot):
    user_id = message.from_user.id
    text_input = (message.text or "").strip()

    lang = user_lang(message.from_user)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

    # 🔐 v11 · Propiedad exclusiva: ninguna respuesta pendiente se aplica si el usuario ya no es el dueño.
    if not await guard_pending_state_ownership(message, bot, t):
        return

    # 🎮 ANUNCIO RECURRENTE DE GAMIFICACIÓN (texto bilingüe, multimedia, botón y enlace)
    if (bot.id, user_id) in GAMI_STATES:
        await _handle_gamification_input(message, bot, lang)
        return

    # 🔐 BÓVEDA PERIMETRAL: IMPORTACIÓN DE RESPALDO CRIPTOGRÁFICO (.bunker / JSON)
    if (bot.id, user_id) in BACKUP_IMPORT_STATES:
        b_data = BACKUP_IMPORT_STATES.pop((bot.id, user_id))
        group_id = b_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_ultra"], callback_data=f"backup_menu_{group_id}_{lang}")]
        ])

        json_content = None
        if message.document:
            try:
                file_obj = await bot.get_file(message.document.file_id)
                downloaded = await bot.download_file(file_obj.file_path)
                json_content = downloaded.read().decode("utf-8")
            except Exception as dl_err:
                resp = await message.answer(f"❌ {tr(lang, 'Error leyendo archivo', 'Error reading file')}: {dl_err}", reply_markup=back_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
                return
        elif text_input and "payload" in text_input and "signature" in text_input:
            json_content = text_input

        if json_content:
            success, report = await import_group_configuration(group_id, json_content)
            if success:
                resp = await message.answer(
                    f"✅ <b>{tr(lang, '¡Respaldo perimetral restaurado con éxito!', 'Perimeter backup restored successfully!')}</b>\n\n"
                    f"• <b>{tr(lang, 'Comunidad ID', 'Community ID')}:</b> <code>{group_id}</code>\n"
                    f"• <b>{tr(lang, 'Autenticación', 'Authentication')}:</b> {tr(lang, 'Firma criptográfica HMAC verificada', 'HMAC cryptographic signature verified')} 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb, parse_mode="HTML"
                )
            else:
                resp = await message.answer(f"❌ <b>{tr(lang, 'Fallo al restaurar', 'Restore failed')}:</b> {report}", reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(tr(lang, "⚠️ Envía un archivo <code>.bunker</code> válido o pega el JSON con firma HMAC.", "⚠️ Send a valid <code>.bunker</code> file or paste the HMAC-signed JSON."), reply_markup=back_kb, parse_mode="HTML")

        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    # 0. CANCELACIÓN DEL SELECTOR DE CANALES (teclado de respuesta)
    if text_input in (TEXTS["es"]["btn_sync_cancel"], TEXTS["en"]["btn_sync_cancel"]):
        clear_user_states(bot.id, user_id)
        await _dismiss_reply_keyboard(message)
        channels_now = await get_active_user_channels(bot, user_id, include_delegated="any")
        await message.answer(
            t["chsettings_main"] if channels_now else t["chsettings_main_empty"],
            reply_markup=get_channels_keyboard(channels_now, lang), parse_mode="HTML"
        )
        return
    
    # 🤖 PROMPT PERSONALIZADO DE INTELIGENCIA ARTIFICIAL (CENTINELA DE IA)
    if (bot.id, user_id) in AI_PROMPT_STATES:
        await _handle_ai_prompt_input(message, bot, AI_PROMPT_STATES.pop((bot.id, user_id)), text_input)
        return

    # 🧾 v20 · CANAL DE REGISTRO: @alias, ID numérico o un mensaje reenviado desde el canal
    if (bot.id, user_id) in LOG_CHANNEL_STATES:
        await _handle_log_channel_input(message, bot, LOG_CHANNEL_STATES.pop((bot.id, user_id)), text_input)
        return

    # ⚖️ v24 · APELACIONES: precio personalizado en Stars
    if (bot.id, user_id) in APPEAL_PRICE_STATES:
        await _handle_appeal_price_input(message, bot, APPEAL_PRICE_STATES.pop((bot.id, user_id)), text_input)
        return

    # 👑 v23 · ROLES VIP: etiqueta personalizada o concesión manual («id [días]» o un mensaje reenviado del usuario)
    if (bot.id, user_id) in VIP_ROLE_STATES:
        await _handle_vip_role_input(message, bot, VIP_ROLE_STATES.pop((bot.id, user_id)), text_input)
        return

    # 🛡️ v23 · MATRIZ: «➕ Autorizar bot» (@alias o mensaje reenviado del bot)
    if (bot.id, user_id) in _v23_auth_bot_states():
        from handlers.media_locks import handle_authorized_bot_input
        await handle_authorized_bot_input(message, bot, _v23_auth_bot_states().pop((bot.id, user_id)), text_input)
        return

    # 🤖 v20 · ASISTENTE DE SOPORTE IA (conversación multi-turno hasta «Salir»)
    if (bot.id, user_id) in SUPPORT_AI_STATES:
        await _handle_support_ai_input(message, bot, SUPPORT_AI_STATES[(bot.id, user_id)], text_input)
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
                resp = await message.answer(tr(lang, "⚠️ Ingresa un número entero de segundos (0 para no borrar).", "⚠️ Enter a whole number of seconds (0 to keep the message)."), reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        # 2. Nombre del Botón y Enlace Embebido
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

                resp = await message.answer(
                    f"✅ <b>{tr(lang, 'Botón configurado para', 'Button configured for')} {target.upper()}!</b>\n"
                    f"🏷️ <b>Texto:</b> <code>{html.escape(btn_text)}</code>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb, parse_mode="HTML"
                )
            else:
                resp = await message.answer(tr(lang, "⚠️ El texto del botón no puede estar vacío.", "⚠️️ Button label cannot be empty."), reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        # 3. Edición de Mensaje / Copy y Multimedia (Soporte Pro y Ultra Pro)
        elif field_type == "msg":
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

            if media_id and tier != "ultra_pro":
                resp = await message.answer(
                    tr(lang, "💎 El envío de multimedia (fotos/videos/GIFs) requiere nivel ULTRA PRO.", "💎 Sending media requires ULTRA PRO tier.") + PERIMETER_SIGNATURE,
                    reply_markup=back_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            if not text_val and not media_id:
                resp = await message.answer(
                    tr(lang, "⚠️ Debes enviar un texto o un archivo multimedia válido.", "⚠️ You must send text or a valid media asset.") + PERIMETER_SIGNATURE,
                    reply_markup=back_kb, parse_mode="HTML"
                )
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            col_text_map = {
                "vc": "vc_join_custom_text",
                "micvip": "mic_vip_custom_text",
                "reset": "reset_notice_custom_text",
                "sched": "vc_sched_start_custom_text",
                "vcwelcome": "vc_welcome_custom_text"
            }
            col_media_id_map = {
                "vc": "vc_join_custom_media_id",
                "micvip": "mic_vip_custom_media_id",
                "reset": "reset_notice_custom_media_id",
                "sched": "vc_sched_start_custom_media_id",
                "vcwelcome": "vc_welcome_custom_media_id"
            }
            col_media_type_map = {
                "vc": "vc_join_custom_media_type",
                "micvip": "mic_vip_custom_media_type",
                "reset": "reset_notice_custom_media_type",
                "sched": "vc_sched_start_custom_media_type",
                "vcwelcome": "vc_welcome_custom_media_type"
            }

            if text_val:
                await set_sentinel_service_message(group_id, col_text_map[target], text_val[:1000])
            if media_id and tier == "ultra_pro":
                await set_sentinel_service_message(group_id, col_media_id_map[target], media_id)
                await set_sentinel_service_message(group_id, col_media_type_map[target], media_type)

            resp = await message.answer(
                f"✅ <b>{tr(lang, 'Mensaje actualizado para', 'Notice updated for')} {target.upper()}!</b>\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

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
                resp = await message.answer(tr(lang, "⚠️ Ingresa un número entero de segundos (0 para no borrar).", "⚠️ Enter a whole number of seconds (0 to keep the message)."), reply_markup=back_kb, parse_mode="HTML")
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
                tr(lang, "✅ <b>¡Mensaje de advertencia actualizado con éxito!</b>", "✅ <b>Warning message updated successfully!</b>") + "\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer(tr(lang, "⚠️ El mensaje no puede estar vacío.", "⚠️ The message cannot be empty."), reply_markup=back_kb, parse_mode="HTML")
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
                tr(lang, "✅ <b>¡Multimedia asociada a advertencias guardada con éxito!</b>", "✅ <b>Warning media saved successfully!</b>") + "\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer(tr(lang, "⚠️ Envía una Foto, Video o GIF válido.", "⚠️ Send a valid photo, video or GIF."), reply_markup=back_kb, parse_mode="HTML")
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
                tr(lang, "✅ <b>¡Descripción explicativa de MicVIP guardada con éxito!</b>", "✅ <b>MicVIP description saved successfully!</b>") + "\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        else:
            resp = await message.answer(tr(lang, "⚠️ El texto no puede estar vacío.", "⚠️ The text cannot be empty."), reply_markup=back_kb, parse_mode="HTML")
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
        state_data = SENTINEL_PHONE_STATES[(bot.id, user_id)]
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["phone_requesting"], parse_mode="HTML")
        
        try:
            res = await start_phone_auth(user_id, group_id, text_input)
        except Exception as e:
            res = {"status": "error", "message": str(e)}
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if res.get("status") == "ok":
            # Transición limpia de estado: solo se retira cuando Telegram ya envió el código
            SENTINEL_PHONE_STATES.pop((bot.id, user_id), None)
            SENTINEL_CODE_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            # No se auto-borra en 60s para darle tiempo al usuario de ver su código
            await message.answer(t["phone_sent"].format(phone=res['phone']), reply_markup=cancel_kb, parse_mode="HTML")
        else:
            # Si falla, se conserva el estado para permitir corrección directa
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [(await origin_back_button(bot, group_id, lang))]
            ])
            raw_err = res.get("message", "Error de conexión")
            error_reason = (
                tr(lang, "Número no válido. Asegúrate de incluir el código internacional (ej. +57...).", 
                         "Invalid phone number. Ensure you include the country code (e.g. +1...).") 
                if raw_err == "invalid_phone" else f"Telegram: {html.escape(str(raw_err))}"
            )
            resp = await message.answer(t["phone_error"].format(reason=error_reason), reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([resp], delay=45)
        return

    # 4. CENTINELA CÓDIGO 5 DÍGITOS
    if (bot.id, user_id) in SENTINEL_CODE_STATES:
        state_data = SENTINEL_CODE_STATES[(bot.id, user_id)]
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["code_verifying"], parse_mode="HTML")
        
        try:
            res = await verify_phone_code(user_id, text_input)
        except Exception as e:
            res = {"status": "error", "message": str(e)}
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if res.get("status") == "success":
            SENTINEL_CODE_STATES.pop((bot.id, user_id), None)
            session_str = res["session_string"]
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [(await origin_back_button(bot, group_id, lang))]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
        elif res.get("status") == "2fa_required":
            SENTINEL_CODE_STATES.pop((bot.id, user_id), None)
            SENTINEL_2FA_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            await message.answer(t["twofa_required"], reply_markup=cancel_kb, parse_mode="HTML")
        else:
            # Si el código es incorrecto, no se destruye el estado para permitir volver a escribirlo
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [(await origin_back_button(bot, group_id, lang))]
            ])
            raw_err = res.get("message", "invalid_code")
            err_msg = t["code_invalid"] if raw_err == "invalid_code" else f"❌ Error: {html.escape(str(raw_err))}"
            resp = await message.answer(err_msg, reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([resp], delay=30)
        return

    # 5. CENTINELA 2FA
    if (bot.id, user_id) in SENTINEL_2FA_STATES:
        state_data = SENTINEL_2FA_STATES[(bot.id, user_id)]
        group_id = state_data["group_id"]
        status_msg = await message.answer(t["twofa_verifying"], parse_mode="HTML")
        
        try:
            res = await verify_2fa_password(user_id, text_input)
        except Exception as e:
            res = {"status": "error", "message": str(e)}
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if res.get("status") == "success":
            SENTINEL_CODE_STATES.pop((bot.id, user_id), None)
            session_str = res["session_string"]
            
            # Guardar la sesión y registrar el centinela activo
            await save_owner_session(user_id, group_id, session_str)
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            
            chat_kind = await resolve_chat_kind(bot, group_id)
            tier = await get_effective_group_tier(group_id, user_id)
            
            try:
                g_name = html.escape((await bot.get_chat(group_id)).title or "Comunidad")
            except Exception:
                g_name = "Comunidad"
                
            clone_info = await get_bot_clone(user_id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
            status_clone = "Operativo 🟢" if has_clone else "No Configurado 🔴"
            
            # Aquí es donde el estado cambia a verde con sesión activa
            sentinel_status = "Conectado 🟢" if connected else "Error de Conexión 🔴"
            
            panel_text = TEXTS[lang]["clone_main_title"].format(
                group_name=g_name, 
                tier=tier.upper(), 
                status=status_clone, 
                sentinel_status=sentinel_status
            )
            panel_keyboard = await get_clone_keyboard(group_id, user_id, lang, chat_type=chat_kind)
            
            # Envía el aviso de éxito y redibuja el panel con el botón en verde
            success_notice = await message.answer(
                tr(lang, "💎 <b>¡Centinela Propio Conectado con Éxito!</b>\n\nEl nodo acústico perimetral está patrullando la sala.", "💎 <b>Dedicated Sentinel Connected!</b>\n\nThe acoustic perimeter node is now patrolling the room.") + "\n\n🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            fire_and_forget_auto_delete([message, success_notice], delay=10)
            
            # Redibuja la consola principal de clon/centinela actualizada
            await message.answer(panel_text, reply_markup=panel_keyboard, parse_mode="HTML")
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
            known = {v.lower() for _, v, _ in targets_before} | {v.lower().lstrip("@") for _, v, _ in targets_before}
            if err or info is None:
                await _tips_say(status, tips_target_error_text(lang, err or "invalid"), cancel_kb)
            elif known & set(info.get("aliases") or {info["value"].lower(), str(info["chat_id"])}):
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
                           f"• Channels: <code>{len(targets_before) + 1}/{limit}</code>")
                        + ("" if info.get("media_ok", True) else "\n" + tr(lang,
                           "⚠️ El bot no puede enviar fotos/vídeos ahí: la multimedia se publicará como texto.",
                           "⚠️ The bot cannot send photos/videos there: media will be posted as text."))
                        + PERIMETER_SIGNATURE,
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

    # 11c-bis. MENSAJE DE AGRADECIMIENTO DE PROPINAS (PRO / ULTRA PRO)
    if tips_key in TIPS_THANKS_STATES:
        st = TIPS_THANKS_STATES[tips_key]
        group_id = st["group_id"]
        cancel_kb = _cancel_kb(t, f"tips_menu_{group_id}_{lang}")
        tier = _tips_tier(await get_effective_group_tier(group_id, user_id))
        plain = (message.text or message.caption or "").strip()

        if tier not in ("pro", "ultra_pro"):
            TIPS_THANKS_STATES.pop(tips_key, None)
            resp = await message.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires a PRO or ULTRA PRO plan.") + PERIMETER_SIGNATURE,
                                        reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
        elif not plain:
            resp = await message.answer(tr(lang, "⚠️ Envía el mensaje como <b>texto</b>; no puede estar vacío.", "⚠️ Send the message as <b>text</b>; it cannot be empty.") + PERIMETER_SIGNATURE,
                                        reply_markup=cancel_kb, parse_mode="HTML")
        elif len(plain) > TIPS_THANKS_MAX_LEN:
            resp = await message.answer(tr(lang, f"⚠️ El texto excede {TIPS_THANKS_MAX_LEN} caracteres ({len(plain)}). Acórtalo.",
                                           f"⚠️ The text exceeds {TIPS_THANKS_MAX_LEN} characters ({len(plain)}). Shorten it.") + PERIMETER_SIGNATURE,
                                        reply_markup=cancel_kb, parse_mode="HTML")
        else:
            TIPS_THANKS_STATES.pop(tips_key, None)
            new_value = None if plain == "-" else (message.html_text if (message.entities or message.caption_entities) else html.escape(plain)).strip()
            try:
                await set_tips_config(group_id, "tips_thanks_text", new_value)
            except Exception as ex:
                logging.error(f"❌ [Tips] Error guardando agradecimiento en group={group_id}: {ex}")
                resp = await message.answer(tips_target_error_text(lang, "save_failed"), reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
            else:
                notice = (tr(lang, "♻️ <b>Se restauró el agradecimiento oficial.</b>", "♻️ <b>Official thank-you message restored.</b>")
                          if new_value is None else
                          tr(lang, "✅ <b>¡Mensaje de agradecimiento actualizado!</b> Usa 👁️ Vista previa para revisarlo.",
                             "✅ <b>Thank-you message updated!</b> Use 👁️ Preview to check it."))
                await _tips_finish(bot, message, st, group_id, lang, notice + PERIMETER_SIGNATURE)
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
                "Elige cada cuánto se publicará este anuncio en el canal con su botón de pago en Stars. "
                "El intervalo se mide desde la última publicación real (también puedes escribir <code>6</code>, "
                "<code>12</code>, <code>24</code>, <code>48</code> o <code>0</code>):"
            ) if lang == "es" else (
                "⏰ <b>Automatic Recurring Broadcast:</b>\n\n"
                "Choose how often this announcement will be posted in the channel with its Stars payment button. "
                "The interval is measured from the last real post (you can also type <code>6</code>, "
                "<code>12</code>, <code>24</code>, <code>48</code> or <code>0</code>):"
            )
            resp = await message.answer(prompt_recurrence + PERIMETER_SIGNATURE,
                                        reply_markup=_plan_recurrence_keyboard(t, channel_id, lang), parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "recurrence":
            if not text_input.isdigit() or int(text_input) not in (0,) + PLAN_RECURRENCE_CHOICES:
                resp = await message.answer(
                    tr(lang, "⚠️ Elige un intervalo válido: 6, 12, 24 o 48 horas (0 para desactivar).",
                       "⚠️ Choose a valid interval: 6, 12, 24 or 48 hours (0 to disable).") + PERIMETER_SIGNATURE,
                    reply_markup=_plan_recurrence_keyboard(t, channel_id, lang), parse_mode="HTML"
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
    lang = lang_from_callback(callback, data)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

    text = ""
    keyboard = None
    card_key = None      # 🖼️ Clave de CARD_IMAGES: si existe la imagen, reemplaza al texto
    card_caption = None  # Caption opcional para datos dinámicos

    if action == "lang":
        # 🌐 v21 · La elección se guarda (users.lang): bot y Mini App la recuerdan en adelante.
        lang = await remember_user_lang(callback.from_user, data[1])
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
        text = t["welcome"].format(name=callback.from_user.full_name)
        keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot), user_id=callback.from_user.id)
        card_key = "welcome"

    elif action == "langpanel":
        group_id = int(data[1])
        lang = await remember_user_lang(callback.from_user, data[2])   # 🌐 v21 · persistente
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
        lang = await remember_user_lang(callback.from_user, data[2])   # 🌐 v21 · persistente
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
            keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot), user_id=callback.from_user.id)
            card_key = "welcome"

        elif target == "settings":
            active_groups = await get_active_user_groups(bot, callback.from_user.id, include_delegated="any")
            keyboard = get_groups_keyboard(active_groups, lang)
            text = t["settings_main"]
            card_key = "settings"

        elif target == "chsettings":
            active_channels = await get_active_user_channels(bot, callback.from_user.id, include_delegated="any")
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

        elif sub == "setthanks":
            if tier not in ("pro", "ultra_pro"):
                await callback.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires a PRO or ULTRA PRO plan."), show_alert=True)
                return
            await callback.answer()
            await _tips_open_prompt(
                callback, TIPS_THANKS_STATES, state_key, group_id, lang,
                tr(lang,
                   "💌 <b>Mensaje de Agradecimiento (PRO / ULTRA PRO):</b>\n\n"
                   "Escribe el mensaje que recibirá en privado cada persona que envíe una propina "
                   f"(máx. {TIPS_THANKS_MAX_LEN} caracteres). Puedes usar formato de Telegram y estas variables:\n"
                   "• <code>{stars}</code> — Stars donadas\n"
                   "• <code>{mention}</code> — mención al donante\n"
                   "• <code>{user_name}</code> — nombre del donante\n"
                   "• <code>{community}</code> — nombre de la comunidad\n\n"
                   "Envía <code>-</code> para volver al mensaje oficial.",
                   "💌 <b>Thank-you Message (PRO / ULTRA PRO):</b>\n\n"
                   "Write the message every tipper will receive privately "
                   f"(max. {TIPS_THANKS_MAX_LEN} characters). Telegram formatting and these variables are supported:\n"
                   "• <code>{stars}</code> — Stars donated\n"
                   "• <code>{mention}</code> — mention of the tipper\n"
                   "• <code>{user_name}</code> — tipper's name\n"
                   "• <code>{community}</code> — community name\n\n"
                   "Send <code>-</code> to restore the official message.") + PERIMETER_SIGNATURE,
                _cancel_kb(t, back_ctx)
            )
            return

        elif sub == "previewthanks":
            if tier not in ("pro", "ultra_pro"):
                await callback.answer(tr(lang, "⭐ Requiere plan PRO o ULTRA PRO.", "⭐ Requires a PRO or ULTRA PRO plan."), show_alert=True)
                return
            await callback.answer(tr(lang, "👁️ Vista previa enviada a este chat.", "👁️ Preview sent to this chat."))
            await send_tip_thanks(bot, viewer_id, _tips_amount(cfg), group_id, lang,
                                  full_name=callback.from_user.full_name or "")
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

            target_ids = {v: tid for tid, v, active in await _tips_get_targets(group_id) if active}
            sent, failed = await dispatch_tips_broadcast(bot, tier, cfg, lang, active_targets, group_id=group_id,
                                                         target_ids=target_ids)
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
                if any(str(v).startswith("⏸️") for v in failed):
                    report += tr(lang, "\n<i>⏸️ = destino pausado automáticamente; reactívalo desde el panel cuando el bot recupere el acceso.</i>",
                                 "\n<i>⏸️ = target paused automatically; re-enable it from the panel once the bot regains access.</i>")
            resp = await bot.send_message(chat_id=viewer_id, text=report + PERIMETER_SIGNATURE, reply_markup=_tips_back_kb(t, group_id, lang), parse_mode="HTML")
            fire_and_forget_auto_delete([resp], delay=60)
            return

        elif sub == "share":
            groups = await get_active_user_groups(bot, callback.from_user.id, include_delegated="can_manage_broadcast")
            channels = await get_active_user_channels(bot, callback.from_user.id, include_delegated="can_manage_broadcast")

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
        text = t[f"pay_{tier_level}_title"].format(group_name=g_name, stars=plan_price_stars(tier_level))
        keyboard = get_payment_keyboard(group_id, lang, tier_level=tier_level, chat_type=chat_kind)
        card_key = f"pay_{tier_level}" if PAYMENT_HUD_CARDS else None

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
            # Despacho de la tarjeta gráfica sin auto-borrado restrictivo para no apresurar al operador
            if not await send_card_message(bot, callback.from_user.id, "sentinel_info", lang, cancel_kb,
                                           caption=t["sentinel_phone_guide"]):
                await callback.message.answer(t["sentinel_phone_guide"], reply_markup=cancel_kb, parse_mode="HTML")
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
        emit_settings_updated_bg(group_id, "bot_console", ["autolower_enabled"])
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

    lang = lang_from_callback(callback, data)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

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
            # 🔒 v15 · Matriz granular de 30 vectores en 3 páginas (estilo Group Help), en handlers/media_locks.py.
            # get_locks_keyboard (4 cerraduras binarias) se conserva por compatibilidad, pero ya no es la entrada.
            from handlers.media_locks import open_media_locks_panel
            await open_media_locks_panel(callback, bot, group_id, lang, page=1)
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
        # Botones heredados (mensajes antiguos del chat): siguen funcionando. set_lock_status alinea la matriz
        # granular en la misma transacción y la vista desemboca en el panel nuevo de 3 páginas.
        lock_type = data[1]
        lock_key = f"lock_{lock_type}"
        if lock_key not in ("lock_media", "lock_stickers", "lock_links", "lock_commands"):
            return
        current = await get_lock_status(group_id, lock_key)
        await set_lock_status(group_id, lock_key, 0 if current == 1 else 1)
        emit_settings_updated_bg(group_id, "bot_console", ["media_locks", lock_key] + (["linklock_enabled"] if lock_key == "lock_links" else []))
        try:
            from handlers.media_locks import open_media_locks_panel
            await open_media_locks_panel(callback, bot, group_id, lang, page=1)
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
AI_TONE_CYCLE: dict[str, str] = {"guardian": "copilot", "copilot": "pr", "pr": "guardian"}
AI_MODE_CYCLE: dict[str, str] = {"mention_only": "always", "always": "chance", "chance": "mention_only"}
AI_PROMPT_PREVIEW_CHARS = 60
AI_PROMPT_MAX_CHARS = 1000


def _ai_tone_label(tone: str, lang: str) -> str:
    labels: dict[str, tuple[str, str]] = {
        "guardian": ("🛡️ Guardián Táctico", "🛡️ Tactical Guardian"),
        "copilot": ("📝 Copiloto Ejecutivo", "📝 Executive Copilot"),
        "pr": ("🤝 Anfitrión / PR", "🤝 Host / PR"),
    }
    es, en = labels.get(tone, labels["guardian"])
    return tr(lang, es, en)


def _ai_mode_label(mode: str, chance: int, lang: str) -> str:
    if mode == "always":
        return tr(lang, "⚡ Siempre Activo", "⚡ Always Active")
    if mode == "chance":
        return tr(lang, f"🎲 Probabilidad ({chance}%)", f"🎲 Chance ({chance}%)")
    return tr(lang, "🎯 Solo mención", "🎯 Mentions only")


def _ai_public_cfg(raw: Optional[dict]) -> dict:
    """Normaliza la configuración de BD (claves, rangos y valores por defecto) con la misma regla que la Mini App."""
    return perimeter_ops.public_ai_sentinel_config(raw or {})


def get_ai_sentinel_keyboard(group_id: int, lang: str, ai_cfg: dict, chat_type: str = "g") -> InlineKeyboardMarkup:
    """
    Botonera del Centinela de IA (ULTRA PRO) — v35, en este orden exacto:
      1 Guardián (ai_togguardian) · 2 Copiloto (ai_togcopilot) · 3 Tono (ai_tonemenu) · 4 Modo (ai_modemenu) ·
      5 Probabilidad (ai_chance) · 6 Editar prompt (ai_prompt) · 7 Volver a Herramientas ULTRA (menu_ultra / cpanel).
    """
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    cfg = _ai_public_cfg(ai_cfg)
    on, off = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE"), tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
    guardian_st = on if cfg["guardian_status"] == 1 else off
    copilot_st = on if cfg["copilot_status"] == 1 else off
    chance = int(cfg["response_chance"])
    chance_label = tr(lang, f"🎲 Probabilidad: {chance}%", f"🎲 Chance: {chance}%")
    if cfg["response_mode"] != "chance":
        chance_label += tr(lang, " (modo chance)", " (chance mode)")
    g = group_id
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_ai_guardian"].format(guardian_st=guardian_st), callback_data=f"ai_togguardian_{g}_{lang}")],
        [InlineKeyboardButton(text=t["btn_ai_copilot"].format(copilot_st=copilot_st), callback_data=f"ai_togcopilot_{g}_{lang}")],
        [InlineKeyboardButton(text=f"🎭 {_ai_tone_label(cfg['personality_tone'], lang)}", callback_data=f"ai_tonemenu_{g}_{lang}")],
        [InlineKeyboardButton(text=f"⚙️ {_ai_mode_label(cfg['response_mode'], chance, lang)}", callback_data=f"ai_modemenu_{g}_{lang}")],
        [InlineKeyboardButton(text=chance_label, callback_data=f"ai_chance_{g}_{lang}")],
        [InlineKeyboardButton(text=t["btn_ai_prompt"], callback_data=f"ai_prompt_{g}_{lang}")],
        [ultra_back_button(chat_type, g, lang)],
    ])


def build_ai_sentinel_panel(group_id: int, lang: str, ai_cfg: Optional[dict], chat_type: str = "g") -> tuple[str, InlineKeyboardMarkup]:
    """Texto + teclado del panel del Centinela de IA a partir de la configuración REAL de la base de datos."""
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    cfg = _ai_public_cfg(ai_cfg)
    on, off = tr(lang, "🟢 ACTIVO", "🟢 ACTIVE"), tr(lang, "🔴 INACTIVO", "🔴 INACTIVE")
    prompt = str(cfg.get("custom_prompt") or "").strip()
    if prompt:
        preview = prompt[:AI_PROMPT_PREVIEW_CHARS] + ("…" if len(prompt) > AI_PROMPT_PREVIEW_CHARS else "")
    else:
        preview = tr(lang, "Por defecto (Estándar)", "Default (Standard)")
    # El prompt lo escribe el usuario: se escapa SIEMPRE (un «<» suelto rompía el panel con TelegramBadRequest).
    text = t["ai_menu"].format(
        guardian_st=on if cfg["guardian_status"] == 1 else off,
        copilot_st=on if cfg["copilot_status"] == 1 else off,
        custom_prompt=html.escape(preview),
    )
    return text, get_ai_sentinel_keyboard(group_id, lang, cfg, chat_type=chat_type)


async def render_ai_sentinel_panel(group_id: int, lang: str, chat_type: str = "g") -> tuple[str, InlineKeyboardMarkup]:
    return build_ai_sentinel_panel(group_id, lang, await get_ai_sentinel_config(group_id), chat_type=chat_type)


async def _apply_ai_patch(group_id: int, patch: dict, lang: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Persiste un cambio del Centinela de IA con la MISMA validación que la Mini App (perimeter_ops.apply_ai_sentinel_patch:
    valida, guarda e invalida la caché del Centinela) y notifica a las demás sesiones. → (config pública, error).
    """
    try:
        cfg = await perimeter_ops.apply_ai_sentinel_patch(group_id, patch)
    except perimeter_ops.AISettingsError as err:
        return None, f"⚠️ {err.field}: {err.message}"
    except Exception as ex:
        logging.error(f"❌ [IA] No se pudo guardar {list(patch)} en {group_id}: {ex}")
        return None, tr(lang, "⚠️ No se pudo guardar el cambio. Inténtalo de nuevo.", "⚠️ Could not save the change. Please try again.")
    emit_settings_updated_bg(group_id, "bot_console", [f"ai_{k}" for k in patch])
    return cfg, None


def get_reputation_keyboard(group_id: int, lang: str, rep_cfg: dict, chat_type: str = "g") -> InlineKeyboardMarkup:
    """Botonera de control de Gamificación y Niveles XP con indicadores visuales activos."""
    is_on = (int(rep_cfg.get("enabled", 1) or 0) == 1)
    st_btn = f"🎮 {'Sistema XP: 🟢 Activado' if is_on else 'Sistema XP: 🔴 Desactivado'}"
    mult = float(rep_cfg.get("multiplier", 1.0) or 1.0)
    back_btn = ultra_back_button(chat_type, group_id, lang)

    btn_1_0 = "⚡ 1.0x 🟢" if abs(mult - 1.0) < 0.05 else "⚡ 1.0x"
    btn_1_5 = "⚡ 1.5x 🟢" if abs(mult - 1.5) < 0.05 else "⚡ 1.5x"
    btn_2_0 = "⚡ 2.0x 🟢" if abs(mult - 2.0) < 0.05 else "⚡ 2.0x"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_btn, callback_data=f"rep_toggle_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=btn_1_0, callback_data=f"rep_setmult_1.0_{group_id}_{lang}"),
            InlineKeyboardButton(text=btn_1_5, callback_data=f"rep_setmult_1.5_{group_id}_{lang}"),
            InlineKeyboardButton(text=btn_2_0, callback_data=f"rep_setmult_2.0_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=f"🏆 {'Ver Cuadro de Honor (Top 10)' if lang == 'es' else 'View Leaderboard (Top 10)'}", callback_data=f"rep_viewtop_{group_id}_{lang}")],
        [back_btn],
    ])


def get_backup_vault_keyboard(group_id: int, lang: str, chat_type: str = "g") -> InlineKeyboardMarkup:
    """Botonera de exportación e importación criptográfica (.bunker)."""
    back_btn = ultra_back_button(chat_type, group_id, lang)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📤 {'Exportar Respaldo (.bunker)' if lang == 'es' else 'Export Backup (.bunker)'}", callback_data=f"backup_export_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"📥 {'Importar / Migrar Respaldo' if lang == 'es' else 'Import / Restore Backup'}", callback_data=f"backup_import_{group_id}_{lang}")],
        [back_btn],
    ])


@router.callback_query(
    F.data.startswith("panic_") | F.data.startswith("shield_") |
    F.data.startswith("podcast_") | F.data.startswith("speakers_") |
    F.data.startswith("payload_") | F.data.startswith("ai_") |
    F.data.startswith("rep_") | F.data.startswith("backup_")
)
async def cb_ultra_tools_dispatch(callback: CallbackQuery, bot: Bot):
    clear_user_states(bot.id, callback.from_user.id)

    data = callback.data.split("_")
    module = data[0]
    sub = data[1]
    lang = lang_from_callback(callback, data)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

    text = ""
    keyboard = None

    if module == "ai":
        # v35 · Callbacks: ai_menu · ai_togguardian · ai_togcopilot · ai_tonemenu · ai_modemenu · ai_chance · ai_prompt
        # (alias heredados de mensajes antiguos: ai_toggle_<guardian|copilot>_<0|1>, ai_cycletone, ai_cyclemode, ai_cyclechance).
        legacy = {"cycletone": "tonemenu", "cyclemode": "modemenu", "cyclechance": "chance"}
        try:
            if sub == "toggle":
                sub = "togguardian" if (len(data) > 2 and data[2] == "guardian") else "togcopilot"
                group_id = int(data[4])
            else:
                sub = legacy.get(sub, sub)
                group_id = int(data[2])
        except (IndexError, ValueError):
            await callback.answer()
            return

        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🤖 <b>Centinela de Inteligencia Artificial</b>", "🤖 <b>Artificial Intelligence Sentinel</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
            with contextlib.suppress(Exception):
                await callback.answer("🔒 ULTRA PRO")
        elif sub == "prompt":
            AI_PROMPT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang, "ts": time.time()}
            hint = tr(lang,
                      f"\n\n<i>Máximo {AI_PROMPT_MAX_CHARS} caracteres. Envía <code>-</code> para volver al prompt por defecto.</i>",
                      f"\n\n<i>Up to {AI_PROMPT_MAX_CHARS} characters. Send <code>-</code> to restore the default prompt.</i>")
            await callback.answer()
            prompt_msg = await callback.message.answer(t["ai_prompt_prompt"] + hint + PERIMETER_SIGNATURE,
                                                       reply_markup=_cancel_kb(t, f"ai_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt_msg], delay=120)
            return
        else:
            current = _ai_public_cfg(await get_ai_sentinel_config(group_id))
            patch: dict = {}
            toast = ""
            if sub == "togguardian":
                patch = {"guardian_status": 0 if current["guardian_status"] == 1 else 1}
                toast = tr(lang, "🛡️ Guardián ", "🛡️ Guardian ") + ("🟢" if patch["guardian_status"] else "🔴")
            elif sub == "togcopilot":
                patch = {"copilot_status": 0 if current["copilot_status"] == 1 else 1}
                toast = tr(lang, "📝 Copiloto ", "📝 Copilot ") + ("🟢" if patch["copilot_status"] else "🔴")
            elif sub == "tonemenu":
                patch = {"personality_tone": AI_TONE_CYCLE.get(current["personality_tone"], "guardian")}
                toast = _ai_tone_label(patch["personality_tone"], lang)
            elif sub == "modemenu":
                patch = {"response_mode": AI_MODE_CYCLE.get(current["response_mode"], "mention_only")}
                toast = _ai_mode_label(patch["response_mode"], int(current["response_chance"]), lang)
            elif sub == "chance":
                patch = {"response_chance": perimeter_ops.next_chance_step(current["response_chance"])}
                toast = tr(lang, f"🎲 Probabilidad: {patch['response_chance']}%", f"🎲 Chance: {patch['response_chance']}%")
            elif sub != "menu":
                await callback.answer()
                return
            cfg = current
            if patch:
                saved, error = await _apply_ai_patch(group_id, patch, lang)
                if error:
                    await callback.answer(error[:190], show_alert=True)
                    return
                cfg = saved or current
            with contextlib.suppress(Exception):
                await callback.answer(toast or None)
            # Se relee la BD para que el panel muestre el valor persistido (y no un valor optimista).
            text, keyboard = await render_ai_sentinel_panel(group_id, lang, chat_type=chat_kind) if patch else \
                build_ai_sentinel_panel(group_id, lang, cfg, chat_type=chat_kind)

    elif module == "rep":
        # 🎯 EXTRACCIÓN SEGURA DE ARGUMENTOS (Evita ValueError en floats)
        if sub == "setmult":
            val = float(data[2])
            group_id = int(data[3])
        else:
            group_id = int(data[2])

        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🎮 <b>Gamificación y Niveles de Reputación</b>", "🎮 <b>Gamification & XP Levels</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            rep_cfg = await get_reputation_settings(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if rep_cfg["enabled"] == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["rep_main"].format(st_badge=st_badge, multiplier=rep_cfg["multiplier"])
            keyboard = get_reputation_keyboard(group_id, lang, rep_cfg, chat_type=chat_kind)
        elif sub == "toggle":
            rep_cfg = await get_reputation_settings(group_id)
            new_st = 0 if rep_cfg["enabled"] == 1 else 1
            await set_reputation_setting(group_id, "reputation_enabled", new_st)
            rep_cfg["enabled"] = new_st
            await callback.answer(tr(lang, f"Sistema XP: {'Activado 🟢' if new_st == 1 else 'Pausado 🔴'}", f"XP System: {'Enabled 🟢' if new_st == 1 else 'Disabled 🔴'}"))
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if new_st == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["rep_main"].format(st_badge=st_badge, multiplier=rep_cfg["multiplier"])
            keyboard = get_reputation_keyboard(group_id, lang, rep_cfg, chat_type=chat_kind)
        elif sub == "setmult":
            await set_reputation_setting(group_id, "reputation_xp_multiplier", val)
            await callback.answer(f"Multiplicador ajustado a {val}x XP 🟢")
            rep_cfg = await get_reputation_settings(group_id)
            st_badge = tr(lang, "🟢 ACTIVADO", "🟢 ACTIVE") if rep_cfg["enabled"] == 1 else tr(lang, "🔴 DESACTIVADO", "🔴 DISABLED")
            text = t["rep_main"].format(st_badge=st_badge, multiplier=val)
            keyboard = get_reputation_keyboard(group_id, lang, rep_cfg, chat_type=chat_kind)
        elif sub == "viewtop":
            top_list = await get_top_reputation(group_id, limit=10)
            lines = [f"#{idx+1} {html.escape(item['name'])} — Nivel {item['level']} ({item['xp']} XP)" for idx, item in enumerate(top_list)] if top_list else [tr(lang, "(Sin actividad registrada aún)", "(No activity recorded yet)")]
            text = f"🏆 <b>Cuadro de Honor (Top 10):</b>\n\n" + "\n".join(lines) + PERIMETER_SIGNATURE
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=f"rep_menu_{group_id}_{lang}")]
            ])

        if text and keyboard:
            await safe_edit_text(callback, text, reply_markup=keyboard, parse_mode="HTML")
        return

    elif module == "backup":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = tr(lang, "🔐 <b>Respaldo Criptográfico Perimetral</b>", "🔐 <b>Cryptographic Perimeter Backup</b>")
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title, chat_type=chat_kind)
        elif sub == "menu":
            try:
                g_title = (await bot.get_chat(group_id)).title or "Comunidad"
            except Exception:
                g_title = "Comunidad"
            text = t["backup_main"].format(title=html.escape(g_title))
            keyboard = get_backup_vault_keyboard(group_id, lang, chat_type=chat_kind)
        elif sub == "export":
            await callback.answer(tr(lang, "📦 Generando respaldo cifrado...", "📦 Generating encrypted backup..."))
            package_str = await export_group_configuration(group_id)
            clean_filename = f"bunker_vault_{abs(group_id)}_{int(time.time())}.bunker"
            doc_file = BufferedInputFile(package_str.encode("utf-8"), filename=clean_filename)
            caption = (
                f"🔐 <b>Bóveda Perimetral — The Bunker OS</b>\n\n"
                f"• <b>Comunidad:</b> <code>{group_id}</code>\n"
                f"• <b>Firma Digital:</b> <code>HMAC-SHA256 (Válida 🟢)</code>\n\n"
                f"<i>Guarda este archivo. Puedes restaurarlo en cualquier momento respondiendo con /importsettings o desde esta misma consola.</i>\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=f"backup_menu_{group_id}_{lang}")]
            ])
            await bot.send_document(chat_id=callback.from_user.id, document=doc_file, caption=caption, reply_markup=back_kb, parse_mode="HTML")
            return
        elif sub == "import":
            BACKUP_IMPORT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["backup_import_prompt"], reply_markup=_cancel_kb(t, f"backup_menu_{group_id}_{lang}"), parse_mode="HTML")
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
# SENTINEL_CFG_STATES se declara una sola vez junto al resto de estados (registro ALL_STATE_DICTS).

async def _render_sentinel_cfg_menu(bot: Bot, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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


# ==========================================
# ⚙️ GESTIÓN BILINGÜE Y SIMÉTRICA DEL CENTINELA (SENTINEL SETTINGS)
# ==========================================
@router.callback_query(F.data.startswith("sentinelcfg_"))
async def cb_sentinel_config_dispatch(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    sub = data[1]
    lang = lang_from_callback(callback, data)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

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
        prefix_sw = {
            "vc": ("vc_join_enabled", "vc_enabled"),
            "micvip": ("mic_vip_enabled", "micvip_enabled"),
            "reset": ("reset_notice_enabled", "reset_enabled"),
            "sched": ("vc_sched_start_enabled", "sched_enabled"),
            "vcwelcome": ("vc_welcome_enabled", "vc_welcome_enabled")
        }
        if target_msg not in prefix_sw:
            return
        col_db, key_cfg = prefix_sw[target_msg]
        # v14 · Leer-invertir-escribir en UNA transacción (BEGIN IMMEDIATE): un doble toque o un cambio simultáneo
        # desde la Mini App ya no deja el interruptor en un estado inesperado. El aviso de entrada al VC escribe
        # vc_join_enabled y vc_enabled a la vez, así ningún módulo queda desincronizado.
        new_st = await toggle_sentinel_service_flag(group_id, target_msg)
        if new_st is None:
            return
        emit_settings_updated_bg(group_id, "bot_console", [key_cfg] + (["vc_join_enabled"] if target_msg == "vc" else []))

        msg_alert = (
            tr(lang, f"🟢 {(target_msg or "").upper()}: Servicio activado.", f"🟢 {(target_msg or "").upper()}: Service enabled.")
            if new_st == 1 else
            tr(lang, f"🔴 {(target_msg or "").upper()}: Servicio desactivado (en edición).", f"🔴 {(target_msg or "").upper()}: Service disabled (editing).")
        )
        await callback.answer(msg_alert)
        text, kb = await _render_sentinel_cfg_menu(bot, group_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")
        return

    # 3. Reset por defecto (🗑️)
    elif sub == "default":
        prefix_map = {
            "vc": "vc_join",
            "micvip": "mic_vip",
            "reset": "reset_notice",
            "sched": "vc_sched_start",
            "vcwelcome": "vc_welcome"
        }
        p = prefix_map.get(target_msg or "", "vc_join")

        # Columnas exactas de base de datos
        await set_sentinel_service_message(group_id, f"{p}_custom_text", None)
        await set_sentinel_service_message(group_id, f"{p}_custom_media_id", None)
        await set_sentinel_service_message(group_id, f"{p}_custom_media_type", None)
        await set_sentinel_service_message(group_id, f"{p}_btn_text", None)
        await set_sentinel_service_message(group_id, f"{p}_btn_url", None)

        default_secs = 20 if target_msg == "reset" else (30 if target_msg in ("vc", "micvip") else 0)
        await set_sentinel_service_message(group_id, f"{p}_autodel_seconds", default_secs)
        await set_sentinel_service_message(group_id, f"{p}_enabled", 1)   # vc_join_enabled → escribe también vc_enabled
        emit_settings_updated_bg(group_id, "bot_console", [f"{p}_enabled"])

        await callback.answer(tr(lang, f"🗑️ Módulo {(target_msg or "").upper()} restablecido por defecto.", f"🗑️ Module {(target_msg or "").upper()} restored to default."), show_alert=True)
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
            btn = cfg.get("sched_start_btn") or tr(lang, "Sin botón", "No button")
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
            f"👁️ <b>{tr(lang, 'Vista Previa', 'Preview')} ({(target_msg or "").upper()}):</b>\n\n"
            f"{content}\n\n"
            f"• <b>{tr(lang, 'Estado del Servicio', 'Service Status')}:</b> <code>{st_label}</code>\n"
            f"• <b>{tr(lang, 'Texto del Botón', 'Button Label')}:</b> <code>{btn}</code>\n"
            f"• <b>{tr(lang, 'Auto-borrado', 'Auto-delete')}:</b> <code>{autodel_label}</code>\n\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 " + tr(lang, "Volver", "Back"), callback_data=f"sentinelcfg_menu_{group_id}_{lang}")]
        ])

        try:
            await callback.message.delete()
        except Exception:
            pass

        if m_id and m_type in ("photo", "video", "animation"):
            if len(preview_text) <= 1024:
                send_method = getattr(bot, f"send_{m_type}")
                await send_method(chat_id=callback.from_user.id, **{m_type: m_id}, caption=preview_text, reply_markup=back_kb, parse_mode="HTML")
            else:
                send_method = getattr(bot, f"send_{m_type}")
                await send_method(chat_id=callback.from_user.id, **{m_type: m_id})
                await bot.send_message(chat_id=callback.from_user.id, text=preview_text, reply_markup=back_kb, parse_mode="HTML")
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
            "lang": lang,
            "ts": time.time()
        }

        if field_name == "autodel":
            prompt_txt = (
                f"⏱️️ <b>{tr(lang, 'Tiempo de Auto-Borrado', 'Auto-Delete Timer')} ({(target_msg or "").upper()}):</b>\n\n"
                f"{tr(lang, 'Envía un número entero en segundos (0 para no borrar automáticamente):', 'Send an integer in seconds (0 to keep permanently):')}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
        elif field_name == "btn":
            prompt_txt = (
                f"🏷️ <b>{tr(lang, 'Nombre del Botón y Enlace', 'Button Label and URL')} ({(target_msg or "").upper()}):</b>\n\n"
                f"{tr(lang, 'Envía el texto del botón, o usa la sintaxis:', 'Send the button text, or use the syntax:')}\n"
                f"<code>Texto del Botón | https://t.me/tu_enlace</code>\n\n"
                f"{tr(lang, 'El texto cubrirá el enlace al 100% sin exponer URLs en el mensaje.', 'The button text will seamlessly embed the link without exposing URLs.')}\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
        else:
            ultra_note = tr(lang, "<i>(Nivel ULTRA PRO: puedes adjuntar Foto, Video o GIF junto al mensaje)</i>", "<i>(ULTRA PRO tier: you may attach Photo, Video or GIF with your message)</i>")
            prompt_txt = (
                f"✍️ <b>{tr(lang, 'Editor de Mensaje', 'Notice Editor')} ({(target_msg or "").upper()}):</b>\n\n"
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
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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
            p_interval = p[11] if len(p) > 11 and p[13] else 0
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
                    text=f"⏰ {p_interval}h" if p_interval else "⏰ Off",
                    callback_data=f"chplans_sched_{p_id}_{channel_id}_{lang}"
                ),
                InlineKeyboardButton(
                    text="🗑️",
                    callback_data=f"chplans_del_{p_id}_{channel_id}_{lang}"
                ),
            ])
    else:
        kb_rows.append([InlineKeyboardButton(text="ℹ️️ " + tr(lang, "Sin planes activos", "No plans active"), callback_data="noop")])

    text = (
        f"💎 <b>Gestión de Membresías — {c_name}</b>\n\n"
        f"• <b>Suscriptores Activos:</b> <code>{sub_count}</code>\n"
        f"• <b>Total de Planes:</b> <code>{len(plans)}</code>\n\n"
        f"<i>Toca el nombre del plan para ver su vista previa comercial, "
        f"o usa los botones inferiores para activarlo/pausarlo, difundirlo, programar su difusión automática (⏰) o eliminarlo.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        f"💎 <b>Membership Management — {c_name}</b>\n\n"
        f"• <b>Active Subscribers:</b> <code>{sub_count}</code>\n"
        f"• <b>Total Plans:</b> <code>{len(plans)}</code>\n\n"
        f"<i>Tap the plan name to preview its commercial layout, "
        f"or use the controls below to activate/pause, broadcast, schedule automatic posts (⏰), or delete.</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    kb_rows.append([InlineKeyboardButton(text=t["btn_create_plan"], callback_data=f"chplans_add_{channel_id}_{lang}")])
    kb_rows.append([InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.callback_query(F.data.startswith("chplans_"))
async def cb_channel_plans_dispatch(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    lang = lang_from_callback(callback, data)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])

    try:
        sub = data[1]
        recur_hours = None
        if sub in ("del", "toggle", "view", "share", "postto", "sched", "setsched"):
            plan_id = int(data[2])
            channel_id = int(data[3])
            target_chat_id = int(data[4]) if sub == "postto" else None
            if sub == "setsched":
                recur_hours = int(data[4])
        elif sub == "recur":
            recur_hours = int(data[2])
            channel_id = int(data[3])
            plan_id = None
        else:
            channel_id = int(data[2])
            plan_id = None
    except (IndexError, ValueError):
        return

    if not await verify_admin_privileges(callback, bot, channel_id):
        return

    CHAT_KIND_CACHE[channel_id] = "c"

    if sub not in ("skip", "view", "share", "postto", "recur", "sched", "setsched"):
        clear_user_states(bot.id, callback.from_user.id)

    # 1. Menú principal de planes
    if sub == "menu":
        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")

    # 2. Activar / pausar plan de membresía
    elif sub == "toggle":
        plan = await get_channel_plan(cast(int, plan_id))
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
            return

        new_status = await toggle_channel_plan_status(cast(int, plan_id))
        # El pre-checkout valida contra caché en memoria: un plan pausado debe dejar de cobrarse ya.
        invalidate_channel_plan_cache(plan_id)
        status_label = tr(lang, "🟢 Plan activado.", "🟢 Plan activated.") if new_status == "active" else tr(lang, "🔴 Plan pausado.", "🔴 Plan paused.")
        await callback.answer(status_label)

        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")
        return

    # 3. Vista previa comercial limpia (el enlace VIP va sólo en botón inline)
    elif sub == "view":
        plan = await get_channel_plan(cast(int, plan_id))
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
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

        m_id, m_type = plan.get("media_id"), plan.get("media_type")
        try:
            await callback.message.delete()
        except Exception:
            pass

        if m_id and m_type in ("photo", "video", "animation"):
            if len(caption_body) <= 1024:
                send_method = getattr(bot, f"send_{m_type}")
                await send_method(chat_id=callback.from_user.id, **{m_type: m_id}, caption=caption_body, reply_markup=preview_kb, parse_mode="HTML")
            else:
                send_method = getattr(bot, f"send_{m_type}")
                await send_method(chat_id=callback.from_user.id, **{m_type: m_id})
                await bot.send_message(chat_id=callback.from_user.id, text=caption_body, reply_markup=preview_kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=callback.from_user.id, text=caption_body, reply_markup=preview_kb, parse_mode="HTML")

    # 4. Menú para compartir / difundir en canales o grupos administrados
    elif sub == "share":
        plan = await get_channel_plan(cast(int, plan_id))
        if not plan:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
            return

        groups = await get_active_user_groups(bot, callback.from_user.id, include_delegated="can_manage_broadcast")
        channels = await get_active_user_channels(bot, callback.from_user.id, include_delegated="can_manage_broadcast")

        share_rows = []
        for g_id, g_name in groups:
            share_rows.append([InlineKeyboardButton(text=f"👥 {g_name[:24]}", callback_data=f"chplans_postto_{plan_id}_{channel_id}_{g_id}_{lang}")])
        for c_id, c_name in channels:
            if c_id != channel_id:
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
        plan = await get_channel_plan(cast(int, plan_id))
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
            if m_id and m_type in ("photo", "video", "animation"):
                if len(post_body) <= 1024:
                    send_method = getattr(bot, f"send_{m_type}")
                    await send_method(chat_id=target_chat_id, **{m_type: m_id}, caption=post_body, reply_markup=post_kb, parse_mode="HTML")
                else:
                    send_method = getattr(bot, f"send_{m_type}")
                    await send_method(chat_id=target_chat_id, **{m_type: m_id})
                    await bot.send_message(chat_id=target_chat_id, text=post_body, reply_markup=post_kb, parse_mode="HTML")
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
                "ℹ️ There's no plan creation in progress to skip (the server may have restarted)."
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

        t_dict = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
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

    # 7b. Paso final del asistente: intervalo de difusión elegido con botones (6/12/24/48 h o sin difusión)
    elif sub == "recur":
        key = (bot.id, callback.from_user.id)
        if key not in CHAN_PLAN_STATES:
            await restore_plan_state(key)
        st_data = CHAN_PLAN_STATES.get(key)
        if (recur_hours not in (0,) + PLAN_RECURRENCE_CHOICES or not st_data
                or st_data.get("channel_id") != channel_id or st_data.get("step") != "recurrence"
                or any(st_data.get(k) in (None, "") for k in ("name", "days", "price", "promo", "link"))):
            forget_plan_state(*key)
            await callback.answer(tr(lang, "ℹ️ No hay una creación de plan en curso.", "ℹ️ No plan creation in progress."), show_alert=True)
            return
        await callback.answer()
        forget_plan_state(*key)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _finalize_and_preview_channel_plan(
            bot=bot, chat_id=callback.from_user.id, channel_id=channel_id, lang=lang,
            name=st_data["name"], days=st_data["days"], price=st_data["price"],
            promo_text=st_data.get("promo", ""), media_id=st_data.get("media_id"),
            media_type=st_data.get("media_type"), target_link=st_data.get("link"),
            recurrence_hours=recur_hours
        )
        return

    # 7c. Editor del programador recurrente de un plan existente
    elif sub in ("sched", "setsched"):
        plan = await get_channel_plan(cast(int, plan_id))
        if not plan or int(plan.get("channel_id") or 0) != channel_id:
            await callback.answer(tr(lang, "⚠️ Plan no encontrado.", "⚠️ Plan not found."), show_alert=True)
            return
        if sub == "setsched":
            if recur_hours not in (0,) + PLAN_RECURRENCE_CHOICES:
                await callback.answer()
                return
            if recur_hours and plan.get("status") != "active":
                await callback.answer(tr(lang, "⚠️ Activa el plan antes de programar su difusión.",
                                         "⚠️ Activate the plan before scheduling its broadcast."), show_alert=True)
                return
            ok = await set_channel_plan_broadcast_config(cast(int, plan_id), channel_id, recur_hours, lang)
            if not ok:
                await callback.answer(tr(lang, "⚠️ No se pudo aplicar el intervalo.", "⚠️ Could not apply the interval."), show_alert=True)
                return
            await callback.answer(
                tr(lang, f"⏰ Difusión cada {recur_hours} h activada.", f"⏰ Broadcast every {recur_hours} h enabled.")
                if recur_hours else tr(lang, "🚫 Difusión automática desactivada.", "🚫 Automatic broadcast disabled.")
            )
            text, kb = await _render_plans_menu(bot, channel_id, lang)
            await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")
            return

        current = int(plan.get("broadcast_interval_hours") or 0) if plan.get("broadcast_enabled") else 0
        last = plan.get("last_broadcast_at")
        nxt = plan.get("next_broadcast_at") if current else None
        status_line = (tr(lang, f"Programada cada <b>{current} h</b>.", f"Scheduled every <b>{current} h</b>.")
                       if current else tr(lang, "Sin difusión automática.", "No automatic broadcast."))
        sched_text = (
            tr(lang, "⏰ <b>Difusión Automática — ", "⏰ <b>Automatic Broadcast — ") + f"{html.escape(plan['plan_name'])}</b>\n\n"
            f"• {status_line}\n"
            + (tr(lang, f"• Última publicación: <code>{last} UTC</code>\n", f"• Last post: <code>{last} UTC</code>\n") if last else "")
            + (tr(lang, f"• Próxima publicación: <code>{nxt} UTC</code>\n", f"• Next post: <code>{nxt} UTC</code>\n") if nxt else "")
            + "\n"
            + tr(lang,
                 "<i>El anuncio se publica en el canal con su botón de pago en Stars. El intervalo se mide desde la "
                 "última publicación real: un fallo de red no reinicia el reloj.</i>",
                 "<i>The announcement is posted in the channel with its Stars payment button. The interval is measured "
                 "from the last real post: a network failure never resets the clock.</i>")
            + PERIMETER_SIGNATURE
        )
        await safe_edit_text(callback, sched_text,
                             reply_markup=_plan_recurrence_keyboard(t, channel_id, lang, plan_id=plan_id, current=current),
                             parse_mode="HTML")
        return

    # 8. Eliminar plan
    elif sub == "del":
        plans = await get_channel_plans(channel_id, only_active=False)
        if plan_id in {p[0] for p in plans}:
            await delete_channel_plan(cast(int, plan_id))
            invalidate_channel_plan_cache(plan_id)
            await callback.answer(tr(lang, "🗑️ Plan eliminado.", "🗑️ Plan deleted."))

        text, kb = await _render_plans_menu(bot, channel_id, lang)
        await safe_edit_text(callback, text, reply_markup=kb, parse_mode="HTML")


# ==========================================
# 💖 AGRADECIMIENTO OFICIAL DE PROPINAS STARS
# ==========================================
async def send_tip_thanks(bot: Bot, user_id: int, stars: int, group_id: int, lang: str = "es", full_name: str = "") -> bool:
    """
    Compatibilidad: la lógica vive en handlers/payments.py (fuente única). Un solo mensaje privado,
    texto personalizable para PRO / ULTRA PRO y un único botón de retorno a la comunidad.
    """
    from handlers.payments import send_tip_thanks as _payments_send_tip_thanks
    return await _payments_send_tip_thanks(bot, user_id, stars, group_id, lang, full_name=full_name)

# ==========================================================================
# 🛰️ v20 · PARIDAD BOT ↔ MINI APP: PERÍMETRO, CANAL DE REGISTRO, GHOST PURGE v2 Y SOPORTE IA
# ==========================================================================
# Toda la lógica vive en perimeter_ops (la misma que usa main.py): aquí solo hay menús y conversación.
async def _handle_ai_prompt_input(message: Message, bot: Bot, st: dict, text_input: Optional[str]) -> None:
    """
    v35 · Respuesta del editor «✍️ Editar Prompt de IA»: re-verifica licencia ULTRA PRO, limpia caracteres de control,
    recorta a 1000 caracteres, guarda con la validación compartida (que invalida la caché del Centinela), avisa con
    auto-borrado y reabre el panel de IA actualizado. «-» restaura el prompt por defecto.
    """
    group_id = int(st["group_id"])
    lang = st.get("lang") or user_lang(message.from_user)
    t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_back_tool"], callback_data=f"ai_menu_{group_id}_{lang}")]
    ])
    if time.time() - float(st.get("ts", time.time())) > STATE_TTL_SECONDS:
        resp = await message.answer(tr(lang, "⌛ La edición caducó; vuelve a abrir el menú.", "⌛ This edit expired; reopen the menu.")
                                    + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([resp], delay=60)
        return
    if await get_effective_group_tier(group_id, message.from_user.id) != "ultra_pro":
        resp = await message.answer(tr(lang, "🔒 El Centinela de IA requiere licencia ULTRA PRO.", "🔒 The AI Sentinel requires an ULTRA PRO license.")
                                    + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([resp], delay=60)
        return
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text_input or "")).strip()
    if not raw:
        resp = await message.answer(tr(lang, "⚠️ El prompt no puede estar vacío. Envía texto o <code>-</code> para restaurar el estándar.",
                                       "⚠️ The prompt cannot be empty. Send text or <code>-</code> to restore the default.")
                                    + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return
    new_prompt = "" if raw == "-" else raw[:AI_PROMPT_MAX_CHARS]
    truncated = raw != "-" and len(raw) > AI_PROMPT_MAX_CHARS
    _, error = await _apply_ai_patch(group_id, {"custom_prompt": new_prompt}, lang)
    if error:
        resp = await message.answer(html.escape(error) + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return
    note = ""
    if not new_prompt:
        note = "\n" + tr(lang, "♻️ Se restauró el prompt por defecto.", "♻️ Default prompt restored.")
    elif truncated:
        note = "\n" + tr(lang, f"✂️ Se recortó a {AI_PROMPT_MAX_CHARS} caracteres.", f"✂️ Trimmed to {AI_PROMPT_MAX_CHARS} characters.")
    resp = await message.answer(t["ai_prompt_saved"] + note + PERIMETER_SIGNATURE, parse_mode="HTML")
    fire_and_forget_auto_delete([message, resp], delay=30)
    # Panel actualizado como mensaje nuevo (el anterior quedó arriba en el chat).
    try:
        chat_kind = await resolve_chat_kind(bot, group_id)
        panel_text, panel_kb = await render_ai_sentinel_panel(group_id, lang, chat_type=chat_kind)
        await message.answer(panel_text, reply_markup=panel_kb, parse_mode="HTML")
    except TelegramBadRequest as ex:
        logging.warning(f"⚠️ [IA] No se pudo redibujar el panel de IA de {group_id}: {ex}")
        await message.answer(tr(lang, "✅ Guardado.", "✅ Saved.") + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")


def _ai_settings_changed(group_id: int, fields) -> None:
    """Tras guardar ajustes de IA: el Centinela los aplica en el siguiente mensaje y la Mini App se repinta."""
    perimeter_ops.invalidate_ai_runtime(group_id)
    emit_settings_updated_bg(group_id, "bot_console", list(fields or []))


def _master_bot_or(bot: Bot) -> Bot:
    """Bot que publica auditorías e informes (el maestro), aunque el operador escriba desde un bot clon."""
    try:
        import assistant as _assistant_mod
        return getattr(_assistant_mod, "_global_bot", None) or bot
    except Exception:
        return bot


async def _safe_edit(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _perimeter_status_for(bot: Bot, group_id: int) -> dict:
    sentinel = {}
    try:
        import assistant as _assistant_mod
        snapshot = getattr(_assistant_mod, "get_sentinel_runtime_snapshot", None)
        sentinel = cast(dict, snapshot(group_id)) if callable(snapshot) else {}
    except Exception:
        sentinel = {}
    try:
        import raid_shield as _raid  # type: ignore[import-not-found]
        raid = _raid.status_payload(group_id) or {}
    except Exception:
        raid = {}
    try:
        from database.database import get_log_report_config
        log_id = ((await get_log_report_config(int(group_id))) or {}).get("log_channel_id")
    except Exception:
        log_id = None
    log_health = (await perimeter_ops.check_log_channel_health(_master_bot_or(bot), log_id)) if log_id \
        else {"ok": False, "reason": "not_configured"}
    status = await perimeter_ops.build_perimeter_status(group_id, sentinel=sentinel, raid=raid, log_health=log_health)
    status["log_channel_id"] = log_id
    return status


def _gpurge_keyboard(group_id: int, lang: str, settings: dict, running: bool) -> InlineKeyboardMarkup:
    labels = perimeter_ops.PURGE_REASON_LABELS["en" if lang == "en" else "es"]
    active = set(settings.get("criteria") or [])
    rows = [[InlineKeyboardButton(text=f"{'✅' if c in active else '⬜'} {labels[c]}", callback_data=f"gpurge_tog_{c}_{group_id}_{lang}")]
            for c in perimeter_ops.PURGE_CRITERIA]
    is_ban = settings.get("action") == "ban"
    rows.append([
        InlineKeyboardButton(text=("🔴 Ban permanente" if lang == "es" else "🔴 Permanent ban") + (" ✓" if is_ban else ""),
                             callback_data=f"gpurge_act_ban_{group_id}_{lang}"),
        InlineKeyboardButton(text=("🟡 Expulsión suave" if lang == "es" else "🟡 Soft kick") + ("" if is_ban else " ✓"),
                             callback_data=f"gpurge_act_kick_{group_id}_{lang}"),
    ])
    rows.append([InlineKeyboardButton(
        text=("⏳ Purga en curso…" if lang == "es" else "⏳ Purge running…") if running else ("💀 Ejecutar purga ahora" if lang == "es" else "💀 Run purge now"),
        callback_data="noop" if running else f"gpurge_run_{group_id}_{lang}")])
    rows.append([InlineKeyboardButton(text="⬅️ " + ("Herramientas Ultra" if lang == "es" else "Ultra Tools"), callback_data=f"menu_ultra_{group_id}_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gpurge_text(lang: str, settings: dict, running: bool) -> str:
    if lang == "es":
        head = ("💀 <b>Ghost Purge — Radar Anti-Fantasmas</b>\n\n"
                "Marca qué amenazas detectar y la directiva a aplicar. Nunca se tocan administradores, Arquitectos, "
                "bots ni cuentas en lista blanca. Cada expulsión queda en el registro de seguridad.\n\n"
                "👻 <i>«Inalcanzables» incluye cuentas que bloquearon al Centinela o llevan más de un mes sin conexión: "
                "puede afectar a miembros legítimos.</i>")
        last = f"\n\n🕒 Última purga: <code>{settings.get('last_scan') or '—'}</code>"
    else:
        head = ("💀 <b>Ghost Purge — Anti-Ghost Radar</b>\n\n"
                "Choose which threats to detect and the directive to apply. Admins, Architects, bots and whitelisted "
                "accounts are never touched. Every removal is logged as a security event.\n\n"
                "👻 <i>«Unreachable» includes accounts that blocked the Sentinel or were last seen over a month ago: "
                "it can affect legitimate members.</i>")
        last = f"\n\n🕒 Last purge: <code>{settings.get('last_scan') or '—'}</code>"
    return head + last + ("\n⏳ " + ("Purga en curso." if lang == "es" else "Purge running.") if running else "") + PERIMETER_SIGNATURE


async def _logch_view(bot: Bot, group_id: int, lang: str) -> tuple:
    status = await _perimeter_status_for(bot, group_id)
    log_id = status.get("log_channel_id")
    item = next((i for i in status["items"] if i["key"] == "log_channel"), {})
    state_txt = {
        "on": "🟢 " + ("Activo: el bot publica correctamente" if lang == "es" else "Active: the bot can post"),
        "fault": "🔴 " + ("Configurado, pero el bot NO puede publicar" if lang == "es" else "Configured, but the bot can NOT post"),
        "off": "⚪ " + ("Sin configurar" if lang == "es" else "Not configured"),
    }.get(item.get("state") or "", "⚪ —")
    master = f"@{MASTER_BOT_USERNAME}" if MASTER_BOT_USERNAME else ("el bot" if lang == "es" else "the bot")
    if lang == "es":
        text = (f"🧾 <b>Canal de Registro</b>\n\n• Canal: <code>{log_id or '—'}</code>\n• Estado: {state_txt}\n\n"
                f"Requisitos: {master} debe ser <b>administrador con permiso de publicar</b> y tú debes ser el "
                f"<b>creador</b> del canal. Se verifica en vivo antes de guardar.")
    else:
        text = (f"🧾 <b>Log Channel</b>\n\n• Channel: <code>{log_id or '—'}</code>\n• Status: {state_txt}\n\n"
                f"Requirements: {master} must be an <b>admin allowed to post</b> and you must be the channel "
                f"<b>creator</b>. Checked live before saving.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ " + ("Vincular / cambiar" if lang == "es" else "Link / change"), callback_data=f"logch_set_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🔄 " + ("Verificar ahora" if lang == "es" else "Check now"), callback_data=f"logch_check_{group_id}_{lang}"),
         InlineKeyboardButton(text="🚫 " + ("Desactivar" if lang == "es" else "Disable"), callback_data=f"logch_off_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⬅️ " + ("Panel del grupo" if lang == "es" else "Group panel"), callback_data=f"gpanel_{group_id}_{lang}")],
    ])
    return text + PERIMETER_SIGNATURE, kb


async def _save_log_channel(bot: Bot, group_id: int, value: str, user_id: int) -> None:
    from database.database import update_chat_operational_settings
    await update_chat_operational_settings(int(group_id), {"log_channel_id": value})
    if value:
        perimeter_ops.forget_log_channel_health(value)
    emit_settings_updated_bg(group_id, "bot_console", ["log_channel_id"])
    logging.info(f"🧾 [Canal de Registro] {user_id} fijó {value or '(desactivado)'} en {group_id} desde el bot.")


async def _handle_log_channel_input(message: Message, bot: Bot, st: dict, text_input: str) -> None:
    group_id, lang = int(st["group_id"]), st.get("lang", "es")
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ " + ("Reintentar" if lang == "es" else "Try again"), callback_data=f"logch_set_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⬅️ " + ("Canal de Registro" if lang == "es" else "Log Channel"), callback_data=f"logch_menu_{group_id}_{lang}")],
    ])
    if time.time() - st.get("ts", 0) > STATE_TTL_SECONDS:
        await message.answer(("⌛ La edición caducó; vuelve a abrir el menú." if lang == "es" else "⌛ This edit expired; reopen the menu.") + PERIMETER_SIGNATURE,
                             reply_markup=back_kb, parse_mode="HTML")
        return
    raw = text_input
    forwarded = getattr(message, "forward_from_chat", None) or getattr(getattr(message, "forward_origin", None), "chat", None)
    if forwarded is not None and getattr(forwarded, "id", None):
        raw = str(forwarded.id)
    try:
        value = await perimeter_ops.resolve_log_channel(_master_bot_or(bot), message.from_user.id, group_id, raw)
    except perimeter_ops.LogChannelError as err:
        resp = await message.answer(f"❌ <b>{'No se guardó' if lang == 'es' else 'Not saved'}:</b> {html.escape(err.message)}{PERIMETER_SIGNATURE}",
                                    reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=90)
        return
    await _save_log_channel(bot, group_id, value, message.from_user.id)
    text, kb = await _logch_view(bot, group_id, lang)
    resp = await message.answer(("✅ <b>Canal de registro verificado y guardado.</b>\n\n" if lang == "es" else
                                 "✅ <b>Log channel verified and saved.</b>\n\n") + text, reply_markup=kb, parse_mode="HTML")
    fire_and_forget_auto_delete([message], delay=30)


def _support_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 " + ("Nueva conversación" if lang == "es" else "New conversation"), callback_data=f"supai_reset_{lang}"),
         InlineKeyboardButton(text="🚪 " + ("Salir" if lang == "es" else "Exit"), callback_data=f"supai_exit_{lang}")],
    ])


async def _handle_support_ai_input(message: Message, bot: Bot, st: dict, text_input: str) -> None:
    ui_lang = st.get("lang", "es")
    st["ts"] = time.time()
    # v31 · Texto limpio (sin espacios sobrantes ni caracteres de control) e idioma REAL del mensaje: un operador con
    # la interfaz en inglés que escribe «como programo el captcha» recibe todo (IA, avisos y botones) en español.
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text_input or "")).strip()
    lang = perimeter_ops.detect_message_lang(clean, default=ui_lang) if clean else ui_lang
    if not clean:
        await message.answer("✍️ " + ("Escribe tu pregunta en texto." if lang == "es" else "Type your question as text."), reply_markup=_support_kb(lang))
        return
    typing_task = None

    async def _keep_typing():
        with contextlib.suppress(Exception):
            while True:
                await bot.send_chat_action(message.chat.id, "typing")
                await asyncio.sleep(4.5)

    typing_task = asyncio.create_task(_keep_typing())
    try:
        result = await perimeter_ops.support_ai_reply(message.from_user.id, clean, ui_lang)
    except perimeter_ops.SupportRateLimited as rl:
        rl_lang = getattr(rl, "lang", lang)
        await message.answer(("⏳ Espera " if rl_lang == "es" else "⏳ Wait ") + f"{rl.retry_after} s.", reply_markup=_support_kb(rl_lang))
        return
    except Exception as ex:
        logging.error(f"❌ [Soporte IA] Error inesperado respondiendo a {message.from_user.id}: {ex}")
        result = {"reply": perimeter_ops.local_knowledge_fallback(clean, lang) or perimeter_ops._support_fallback(lang),
                  "lang": lang}
    finally:
        if typing_task:
            typing_task.cancel()
    lang = result.get("lang", lang)
    st["lang"] = lang   # los botones del asistente siguen el idioma de la conversación
    # Texto de la IA en plano: caracteres sueltos (<, *, _) nunca rompen el envío.
    await message.answer(f"🤖 {result['reply']}", reply_markup=_support_kb(lang), parse_mode=None)


@router.callback_query(
    F.data.startswith("perim_") | F.data.startswith("logch_") | F.data.startswith("gpurge_") | F.data.startswith("supai_")
)
async def cb_perimeter_v20(callback: CallbackQuery, bot: Bot):
    data = (callback.data or "").split("_")
    module, sub = data[0], data[1] if len(data) > 1 else ""
    lang = lang_from_callback(callback, data)
    uid = callback.from_user.id

    # 🤖 Soporte IA: personal, sin comunidad asociada.
    if module == "supai":
        if sub == "start":
            clear_user_states(bot.id, uid)
            SUPPORT_AI_STATES[(bot.id, uid)] = {"lang": lang, "ts": time.time()}
            intro = ("🤖 <b>Arquitecto de Soporte IA</b>\n\nPregúntame por planes, comandos, el Centinela, el Bot Clon o la "
                     "configuración de tu comunidad. Nunca me envíes tokens, sesiones ni códigos de acceso."
                     if lang == "es" else
                     "🤖 <b>AI Support Architect</b>\n\nAsk me about plans, commands, the Sentinel, the clone bot or your "
                     "community setup. Never send me tokens, sessions or login codes.")
            await callback.message.answer(intro + PERIMETER_SIGNATURE, reply_markup=_support_kb(lang), parse_mode="HTML")
        elif sub == "reset":
            await perimeter_ops.support_ai_reset(uid)
            SUPPORT_AI_STATES[(bot.id, uid)] = {"lang": lang, "ts": time.time()}
            await callback.answer("🧹 " + ("Conversación reiniciada" if lang == "es" else "Conversation reset"))
            return
        elif sub == "exit":
            SUPPORT_AI_STATES.pop((bot.id, uid), None)
            t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
            await _safe_edit(callback, t["support_main"], get_support_keyboard(lang))
        await callback.answer()
        return

    try:
        if module == "gpurge" and sub in ("act", "tog"):
            target, group_id = data[2], int(data[3])
        else:
            target, group_id = None, int(data[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    clear_user_states(bot.id, uid)

    if module == "perim":
        status = await _perimeter_status_for(bot, group_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 " + ("Actualizar" if lang == "es" else "Refresh"), callback_data=f"perim_view_{group_id}_{lang}")],
            [InlineKeyboardButton(text="⬅️ " + ("Panel del grupo" if lang == "es" else "Group panel"), callback_data=f"gpanel_{group_id}_{lang}")],
        ])
        await _safe_edit(callback, perimeter_ops.render_perimeter_text(status, lang) + PERIMETER_SIGNATURE, kb)
        await callback.answer()
        return

    if module == "logch":
        if sub == "set":
            LOG_CHANNEL_STATES[(bot.id, uid)] = {"group_id": group_id, "lang": lang, "ts": time.time()}
            prompt = ("🧾 Envía el <b>@alias</b> o el <b>ID numérico</b> (-100…) del canal de registro, o <b>reenvía</b> aquí "
                      "cualquier mensaje de ese canal." if lang == "es" else
                      "🧾 Send the log channel <b>@alias</b> or <b>numeric ID</b> (-100…), or <b>forward</b> any message from it.")
            t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
            msg = await callback.message.answer(prompt + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"logch_menu_{group_id}_{lang}"), parse_mode="HTML")
            fire_and_forget_auto_delete([msg], delay=120)
            await callback.answer()
            return
        if sub == "off":
            await _save_log_channel(bot, group_id, "", uid)
            await callback.answer("🚫 " + ("Canal de registro desactivado" if lang == "es" else "Log channel disabled"))
        elif sub == "check":
            current = (await _perimeter_status_for(bot, group_id)).get("log_channel_id")
            if current:
                perimeter_ops.forget_log_channel_health(current)
            await callback.answer("🔄 " + ("Verificado" if lang == "es" else "Checked"))
        else:
            await callback.answer()
        text, kb = await _logch_view(bot, group_id, lang)
        await _safe_edit(callback, text, kb)
        return

    # 💀 Ghost Purge v2
    import assistant as _assistant_mod
    running = bool(getattr(_assistant_mod, "is_ghost_purge_running", lambda _c: False)(group_id))
    settings = await perimeter_ops.get_purge_settings(group_id)
    if sub == "act" and target in perimeter_ops.PURGE_ACTIONS:
        settings = await perimeter_ops.save_purge_settings(group_id, action=target)
        await callback.answer("✅")
    elif sub == "tog" and target in perimeter_ops.PURGE_CRITERIA:
        new_criteria = perimeter_ops.toggle_purge_criterion(settings["criteria"], target)
        if not new_criteria:
            await callback.answer("⚠️ " + ("Debe quedar al menos un criterio activo." if lang == "es" else "Keep at least one criterion."), show_alert=True)
            return
        settings = await perimeter_ops.save_purge_settings(group_id, criteria=new_criteria)
        await callback.answer("✅")
    elif sub == "run":
        if running:
            await callback.answer("⏳ " + ("Ya hay una purga en curso." if lang == "es" else "A purge is already running."), show_alert=True)
            return
        confirm = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💀 " + ("Confirmar purga" if lang == "es" else "Confirm purge"), callback_data=f"gpurge_go_{group_id}_{lang}")],
            [InlineKeyboardButton(text="✖️ " + ("Cancelar" if lang == "es" else "Cancel"), callback_data=f"gpurge_menu_{group_id}_{lang}")],
        ])
        labels = perimeter_ops.PURGE_REASON_LABELS["en" if lang == "en" else "es"]
        crit = "\n".join(f"• {labels[c]}" for c in settings["criteria"])
        directive = ("🔴 Ban permanente" if settings["action"] == "ban" else "🟡 Expulsión suave") if lang == "es" else \
                    ("🔴 Permanent ban" if settings["action"] == "ban" else "🟡 Soft kick")
        await _safe_edit(callback, (f"⚠️ <b>{'¿Ejecutar la Ghost Purge?' if lang == 'es' else 'Run the Ghost Purge?'}</b>\n\n{crit}\n\n"
                                    f"{'Directiva' if lang == 'es' else 'Directive'}: <b>{directive}</b>{PERIMETER_SIGNATURE}"), confirm)
        await callback.answer()
        return
    elif sub == "go":
        if running:
            await callback.answer("⏳", show_alert=False)
        else:
            task = asyncio.create_task(_assistant_mod.execute_ghost_purge(group_id, origin="bot", lang=lang), name=f"ghost_purge_bot:{group_id}")
            _SETTINGS_EMIT_TASKS.add(task)   # referencia fuerte hasta que termine
            task.add_done_callback(_SETTINGS_EMIT_TASKS.discard)
            running = True
            await callback.answer("💀 " + ("Purga iniciada en segundo plano. El informe llegará al grupo." if lang == "es"
                                           else "Purge started in background. The report will be posted in the group."), show_alert=True)
    else:
        await callback.answer()
    await _safe_edit(callback, _gpurge_text(lang, settings, running), _gpurge_keyboard(group_id, lang, settings, running))



# ==========================================================================
# 🎟️ v22 · AUDITORÍA MICVIP EN LA CONSOLA DEL BOT (dueño, Arquitecto y delegados con can_view_analytics)
# ==========================================================================
# --------------------------------------------------------------------------
# 🔐 v29 · MURO DE PAGO POR NIVEL (paridad 1:1 con la Mini App y los 402 de la API)
# --------------------------------------------------------------------------
# Fuente única: database.FEATURE_MIN_TIER. Se evalúa con get_effective_group_tier (licencia VIGENTE con
# caducidad y concesiones del Arquitecto; el Arquitecto siempre pasa). Una comunidad Free nunca ve las
# tablas ni puede escribir: recibe la tarjeta de conversión PRO ⭐ / ULTRA PRO 💎.
_TIER_RANK_BOT = {"free": 0, "pro": 1, "ultra_pro": 2}
_PAYWALL_COPY = {
    "micvip_audit": ("🎟️", "Auditoría MicVIP", "MicVIP Audit",
                     "Registro auditable de cada pase: comprador, expiración real, estado y evidencia del Centinela.",
                     "Auditable log of every pass: buyer, real expiry, status and Sentinel evidence."),
    "ban_appeals": ("⚖️", "Apelación de baneos", "Ban appeals",
                    "Convierte los baneos automáticos en ingresos: el usuario paga en Stars y recibe un enlace de un solo uso.",
                    "Turn automatic bans into revenue: the user pays in Stars and gets a single-use rejoin link."),
    "vip_roles": ("👑", "Roles y etiquetas VIP", "VIP roles & tags",
                  "Rol VIP automático tras donar o comprar MicVIP, con etiqueta nativa de Telegram y exenciones parciales.",
                  "Automatic VIP role after a donation or MicVIP purchase, with Telegram's native tag and partial exemptions."),
    "link_quotas": ("🔗", "Cuotas de enlaces de administradores", "Admin link quotas",
                    "Límite diario de enlaces por administrador con excepciones individuales y aviso automático.",
                    "Daily link limit per admin with individual exceptions and automatic notices."),
}


def _feature_required_tier(feature: str) -> str:
    try:
        from database.database import FEATURE_MIN_TIER
        return FEATURE_MIN_TIER.get(feature, "pro")
    except Exception:
        return "pro"


async def feature_unlocked(group_id: int, user_id: int, feature: str) -> bool:
    try:
        tier = await get_effective_group_tier(int(group_id), int(user_id))
    except Exception as ex:
        logging.warning(f"⚠️ [Paywall] No se pudo leer la licencia de {group_id}: {ex}")
        tier = "free"   # fallo cerrado: sin licencia verificable no se abre una función de pago
    tier = str(tier or "free").lower()
    return _TIER_RANK_BOT.get(tier, 0) >= _TIER_RANK_BOT[_feature_required_tier(feature)]


def build_paywall_card(group_id: int, feature: str, lang: str) -> tuple:
    """Tarjeta de conversión (texto + teclado) para una función bloqueada por nivel."""
    icon, name_es, name_en, pitch_es, pitch_en = _PAYWALL_COPY.get(feature, ("🔒", feature, feature, "", ""))
    required = _feature_required_tier(feature)
    req_label = "ULTRA PRO 💎" if required == "ultra_pro" else "PRO ⭐"
    text = (
        f"{icon} <b>{tr(lang, name_es, name_en)}</b> · 🔒 <b>{req_label}</b>\n\n"
        f"{tr(lang, pitch_es, pitch_en)}\n\n"
        + tr(lang,
             f"Tu comunidad está en el plan <b>Free</b>. Actívala con <b>{req_label}</b> para desbloquear esta función "
             "al instante: la licencia queda vinculada a esta comunidad.",
             f"Your community is on the <b>Free</b> plan. Activate <b>{req_label}</b> to unlock this feature instantly: "
             "the license is bound to this community.")
        + PERIMETER_SIGNATURE
    )
    rows = []
    if required == "pro":
        rows.append([InlineKeyboardButton(text=tr(lang, "⭐ Activar PRO", "⭐ Get PRO"), callback_data=f"pay_pro_{group_id}_{lang}")])
    rows.append([InlineKeyboardButton(text=tr(lang, "💎 Activar ULTRA PRO", "💎 Get ULTRA PRO"), callback_data=f"pay_ultra_{group_id}_{lang}")])
    rows.append(_v23_back(group_id, lang) if "_v23_back" in globals() else
                [InlineKeyboardButton(text="⬅️ " + tr(lang, "Panel del grupo", "Group panel"), callback_data=f"gpanel_{group_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def enforce_feature_tier(callback: CallbackQuery, bot: Bot, group_id: int, feature: str, lang: str) -> bool:
    """True si puede continuar; si no, pinta la tarjeta de conversión y responde el callback."""
    if await feature_unlocked(group_id, callback.from_user.id, feature):
        return True
    clear_user_states(bot.id, callback.from_user.id)
    text, kb = build_paywall_card(group_id, feature, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            with contextlib.suppress(Exception):
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    with contextlib.suppress(Exception):
        await callback.answer("🔒 " + tr(lang, "Función de pago", "Paid feature"))
    logging.info(f"🔒 [Paywall] {feature} bloqueado en {group_id} para {callback.from_user.id} (sin licencia suficiente).")
    return False


async def enforce_feature_tier_msg(message: Message, group_id: int, feature: str, lang: str) -> bool:
    """Variante para entradas conversacionales: la licencia pudo caducar entre la apertura y la respuesta."""
    if await feature_unlocked(group_id, message.from_user.id, feature):
        return True
    text, kb = build_paywall_card(group_id, feature, lang)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    return False


MVAUDIT_PER_PAGE = 8
MVAUDIT_FILTERS = ("all", "active", "expired", "issues")


def _mvaudit_keyboard(group_id: int, flt: str, page: int, pages: int, lang: str) -> InlineKeyboardMarkup:
    labels = {"all": ("Todos", "All"), "active": ("🟢 Activos", "🟢 Active"),
              "expired": ("🔴 Expirados", "🔴 Expired"), "issues": ("⚠️ Incidencias", "⚠️ Issues")}
    filt_row = [InlineKeyboardButton(text=("✓ " if f == flt else "") + labels[f][0 if lang == "es" else 1],
                                     callback_data=f"mvaudit_{group_id}_{f}_1_{lang}") for f in MVAUDIT_FILTERS]
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"mvaudit_{group_id}_{flt}_{page - 1}_{lang}"))
    nav.append(InlineKeyboardButton(text="🔄", callback_data=f"mvaudit_{group_id}_{flt}_{page}_{lang}"))
    if page < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"mvaudit_{group_id}_{flt}_{page + 1}_{lang}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        filt_row[:2], filt_row[2:], nav,
        [InlineKeyboardButton(text="⬅️ " + ("Panel del grupo" if lang == "es" else "Group panel"), callback_data=f"gpanel_{group_id}_{lang}")],
    ])


@router.callback_query(F.data.startswith("mvaudit_"))
async def cb_micvip_audit(callback: CallbackQuery, bot: Bot):
    """mvaudit_{group_id}_{filtro}_{página}_{lang} — registro paginado con estado y evidencia del Centinela."""
    data = (callback.data or "").split("_")
    try:
        group_id, flt, page = int(data[1]), data[2], max(1, int(data[3]))
    except (IndexError, ValueError):
        await callback.answer()
        return
    lang = lang_from_callback(callback, data)
    flt = flt if flt in MVAUDIT_FILTERS else "all"
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    if not await enforce_feature_tier(callback, bot, group_id, "micvip_audit", lang):
        return
    try:
        audit = await micvip_audit.get_audit(group_id, status=flt, limit=MVAUDIT_PER_PAGE, offset=(page - 1) * MVAUDIT_PER_PAGE)
    except Exception as ex:
        logging.error(f"❌ [MicVIP Audit] Lectura fallida en {group_id}: {ex}")
        await callback.answer("⚠️ " + tr(lang, "No se pudo leer el registro.", "Could not read the log."), show_alert=True)
        return
    pages = max(1, -(-int(audit.get("total", 0)) // MVAUDIT_PER_PAGE))
    page = min(page, pages)
    text = micvip_audit.render_bot_page(audit, lang, page=page, per_page=MVAUDIT_PER_PAGE) + PERIMETER_SIGNATURE
    keyboard = _mvaudit_keyboard(group_id, flt, page, pages, lang)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# ==========================================================================
# 🛡️👑🔗 v23 · REFUERZO · ROLES VIP · CUOTAS DE ENLACES (paridad 1:1 con la Mini App y el Dashboard)
# ==========================================================================
def _v23_auth_bot_states() -> dict:
    """Almacén del asistente «➕ Autorizar bot» (vive en media_locks); se registra para /start, /cancel y la navegación."""
    from handlers import media_locks as _ml
    register_external_state_store(_ml.AUTH_BOT_STATES)
    return _ml.AUTH_BOT_STATES


_v23_auth_bot_states()

VIPR_PER_PAGE = 6
_VIPR_TOGGLES = {"en": "enabled", "mv": "micvip_role", "al": "autolower_immune", "an": "announce"}
_VIPR_CYCLES = {"ms": "donor_min_stars", "dd": "donor_days", "fb": "flood_bonus"}


def _v23_back(group_id: int, lang: str) -> list:
    return [InlineKeyboardButton(text="⬅️ " + tr(lang, "Panel del grupo", "Group panel"), callback_data=f"gpanel_{group_id}_{lang}")]


def _vip_days_label(days: int, lang: str) -> str:
    return tr(lang, "♾️ permanente", "♾️ permanent") if int(days) == 0 else tr(lang, f"{days} días", f"{days} days")


def _vip_main_view(group_id: int, cfg: dict, summary: dict, lang: str) -> tuple:
    on = lambda v: "✅" if v else "❌"
    exempt = cfg.get("exempt_vectors") or []
    text = (
        f"👑 <b>{tr(lang, 'Roles y etiquetas VIP', 'VIP roles & tags')}</b>\n\n"
        f"• {tr(lang, 'Estado', 'Status')}: <b>{tr(lang, '🟢 Activo', '🟢 Active') if cfg['enabled'] else tr(lang, '🔴 Inactivo', '🔴 Inactive')}</b>\n"
        f"• 🏷️ {tr(lang, 'Etiqueta', 'Tag')}: <code>{html.escape(cfg.get('tag_text') or '—')}</code>\n"
        f"• 💝 {tr(lang, 'Donación mínima', 'Minimum donation')}: <b>{cfg['donor_min_stars']} ⭐</b> · ⏳ {_vip_days_label(cfg['donor_days'], lang)}\n"
        f"• 🎙️ {tr(lang, 'MicVIP otorga rol', 'MicVIP grants role')}: {on(cfg['micvip_role'])}\n"
        f"• 🛡️ {tr(lang, 'Exenciones parciales', 'Partial exemptions')}: <b>{len(exempt)}</b> · 🌊 {tr(lang, 'Anti-flood', 'Anti-flood')} ×{cfg['flood_bonus']}"
        f" · 🔊 {tr(lang, 'Prioridad AutoLower', 'AutoLower priority')} {on(cfg['autolower_immune'])}\n\n"
        f"👥 {tr(lang, 'VIP vigentes', 'Active VIPs')}: <b>{summary.get('active', 0)}</b> · 🏷️ {tr(lang, 'etiquetados', 'tagged')}: "
        f"<b>{summary.get('tagged', 0)}</b> · ⭐ {summary.get('stars', 0)}\n\n"
        + tr(lang,
             "<i>Se concede solo al pagar (donación o pase MicVIP) y vence solo. Las etiquetas usan la función nativa de "
             "Telegram: el bot necesita el permiso «Gestionar etiquetas». Nunca se asciende a nadie a administrador.</i>",
             "<i>Granted automatically on payment (donation or MicVIP pass) and expires on its own. Tags use Telegram's "
             "native feature: the bot needs the “Manage tags” right. Nobody is ever promoted to admin.</i>")
        + PERIMETER_SIGNATURE
    )
    g = group_id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(tr(lang, "🟢 Activado", "🟢 Enabled") if cfg["enabled"] else tr(lang, "🔴 Desactivado", "🔴 Disabled")),
                              callback_data=f"vipr_t_{g}_en_{lang}"),
         InlineKeyboardButton(text="🏷️ " + tr(lang, "Etiqueta", "Tag") + " ✏️", callback_data=f"vipr_tag_{g}_{lang}")],
        [InlineKeyboardButton(text=f"💝 {cfg['donor_min_stars']} ⭐", callback_data=f"vipr_c_{g}_ms_{lang}"),
         InlineKeyboardButton(text=f"⏳ {_vip_days_label(cfg['donor_days'], lang)}", callback_data=f"vipr_c_{g}_dd_{lang}"),
         InlineKeyboardButton(text=f"🌊 ×{cfg['flood_bonus']}", callback_data=f"vipr_c_{g}_fb_{lang}")],
        [InlineKeyboardButton(text=f"🎙️ MicVIP {on(cfg['micvip_role'])}", callback_data=f"vipr_t_{g}_mv_{lang}"),
         InlineKeyboardButton(text=f"🔊 AutoLower {on(cfg['autolower_immune'])}", callback_data=f"vipr_t_{g}_al_{lang}"),
         InlineKeyboardButton(text=f"📣 {on(cfg['announce'])}", callback_data=f"vipr_t_{g}_an_{lang}")],
        [InlineKeyboardButton(text=f"🛡️ {tr(lang, 'Exenciones', 'Exemptions')} ({len(exempt)})", callback_data=f"vipr_x_{g}_{lang}"),
         InlineKeyboardButton(text="👥 " + tr(lang, "Miembros", "Members"), callback_data=f"vipr_l_{g}_1_{lang}")],
        [InlineKeyboardButton(text="➕ " + tr(lang, "Conceder VIP", "Grant VIP"), callback_data=f"vipr_g_{g}_{lang}")],
        _v23_back(g, lang),
    ])
    return text, kb


def _vip_exempt_view(group_id: int, cfg: dict, lang: str) -> tuple:
    from handlers.media_locks import label as _ml_label
    from database.database import VIP_EXEMPTIBLE_VECTORS
    chosen = set(cfg.get("exempt_vectors") or [])
    text = (f"🛡️ <b>{tr(lang, 'Exenciones parciales VIP', 'VIP partial exemptions')}</b>\n\n"
            + tr(lang, "Los VIP vigentes pueden enviar estos tipos de contenido aunque estén bloqueados para el resto. "
                       "Enlaces, reenvíos, botones inline, bots y ediciones maliciosas <b>nunca</b> son eximibles, y durante "
                       "el Pánico o el modo nocturno las exenciones se suspenden.",
                 "Active VIPs may send these content types even when they are locked for everyone else. Links, forwards, "
                 "inline buttons, bots and malicious edits are <b>never</b> exemptible, and exemptions are suspended during "
                 "Panic or night mode.")
            + PERIMETER_SIGNATURE)
    rows, row = [], []
    for idx, key in enumerate(VIP_EXEMPTIBLE_VECTORS):
        row.append(InlineKeyboardButton(text=("✅ " if key in chosen else "▫️ ") + _ml_label(key, lang),
                                        callback_data=f"vipr_e_{group_id}_{idx}_{lang}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ " + tr(lang, "Roles VIP", "VIP Roles"), callback_data=f"vipr_m_{group_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _vip_members_view(group_id: int, data: dict, page: int, lang: str) -> tuple:
    records = data.get("records") or []
    total = int(data.get("total") or 0)
    pages = max(1, -(-total // VIPR_PER_PAGE))
    lines = []
    for rec in records:
        who = html.escape(rec.get("full_name") or (f"@{rec['username']}" if rec.get("username") else str(rec["user_id"])))
        src = vip_roles.SOURCE_LABELS.get(rec.get("source"), ("—", "—"))[0 if lang == "es" else 1]
        state = tr(lang, "🟢 Vigente", "🟢 Active") if rec.get("active") else tr(lang, "🔴 Vencido", "🔴 Expired")
        tag = vip_roles.TAG_STATUS_LABELS.get(rec.get("tag_status"), ("", ""))[0 if lang == "es" else 1]
        lines.append(f"• <b>{who}</b> <code>{rec['user_id']}</code>\n   {state} · {src} · ⏳ {vip_roles.fmt_expiry(rec.get('expires_at'), lang)}"
                     f" · ⭐ {rec.get('total_stars') or 0}\n   {tag}")
    text = (f"👥 <b>{tr(lang, 'Miembros VIP', 'VIP members')}</b> · {page}/{pages}\n\n"
            + ("\n".join(lines) if lines else tr(lang, "<i>Aún no hay miembros VIP.</i>", "<i>No VIP members yet.</i>"))
            + PERIMETER_SIGNATURE)
    rows = []
    for rec in records:
        if rec.get("active"):
            rows.append([
                InlineKeyboardButton(text=f"🏷️ {rec['user_id']}", callback_data=f"vipr_rt_{group_id}_{rec['user_id']}_{page}_{lang}"),
                InlineKeyboardButton(text=f"❌ {tr(lang, 'Retirar', 'Revoke')}", callback_data=f"vipr_r_{group_id}_{rec['user_id']}_{page}_{lang}"),
            ])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"vipr_l_{group_id}_{page - 1}_{lang}"))
    nav.append(InlineKeyboardButton(text="🔄", callback_data=f"vipr_l_{group_id}_{page}_{lang}"))
    if page < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"vipr_l_{group_id}_{page + 1}_{lang}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ " + tr(lang, "Roles VIP", "VIP Roles"), callback_data=f"vipr_m_{group_id}_{lang}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _vip_render_main(group_id: int, lang: str) -> tuple:
    from database.database import vip_role_list
    cfg = await vip_roles.get_config(group_id)
    summary = (await vip_role_list(group_id, status="active", limit=1)).get("summary") or {}
    return _vip_main_view(group_id, cfg, summary, lang)


def _vip_changed(group_id: int, fields) -> None:
    emit_settings_updated_bg(group_id, "bot_console", [f"vip_{f}" for f in fields])


@router.callback_query(F.data.startswith("vipr_"))
async def cb_vip_roles(callback: CallbackQuery, bot: Bot):
    """vipr_{sub}_{group_id}_…_{lang} — solo el Dueño legítimo o el Arquitecto (no figura en DELEGATE_CALLBACK_RULES)."""
    data = (callback.data or "").split("_")
    lang = lang_from_callback(callback, data)
    try:
        sub, group_id = data[1], int(data[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    if not await enforce_feature_tier(callback, bot, group_id, "vip_roles", lang):
        return
    uid = callback.from_user.id
    clear_user_states(bot.id, uid)
    from database.database import (BunkerValidationError, VIP_DAYS_CHOICES, VIP_EXEMPTIBLE_VECTORS, VIP_FLOOD_BONUS_RANGE,
                                   VIP_MIN_STARS_CHOICES, vip_role_list)

    async def save(patch: dict) -> bool:
        try:
            await vip_roles.update_config(group_id, patch, updated_by=uid)
        except BunkerValidationError as err:
            await callback.answer("⚠️ " + "; ".join(err.errors.values())[:180], show_alert=True)
            return False
        _vip_changed(group_id, patch.keys())
        return True

    if sub == "t" and len(data) > 4 and data[3] in _VIPR_TOGGLES:
        field = _VIPR_TOGGLES[data[3]]
        cfg = await vip_roles.get_config(group_id)
        if not await save({field: not cfg[field]}):
            return
        await callback.answer("✅")
    elif sub == "c" and len(data) > 4 and data[3] in _VIPR_CYCLES:
        field = _VIPR_CYCLES[data[3]]
        seq = {"donor_min_stars": VIP_MIN_STARS_CHOICES, "donor_days": VIP_DAYS_CHOICES,
               "flood_bonus": tuple(range(VIP_FLOOD_BONUS_RANGE[0], VIP_FLOOD_BONUS_RANGE[1] + 1))}[field]
        cur = (await vip_roles.get_config(group_id))[field]
        nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
        if not await save({field: nxt}):
            return
        await callback.answer("✅")
    elif sub == "x":
        text, kb = _vip_exempt_view(group_id, await vip_roles.get_config(group_id), lang)
        await _safe_edit(callback, text, kb)
        await callback.answer()
        return
    elif sub == "e":
        try:
            key = VIP_EXEMPTIBLE_VECTORS[int(data[3])]
        except (IndexError, ValueError):
            await callback.answer()
            return
        chosen = list((await vip_roles.get_config(group_id)).get("exempt_vectors") or [])
        chosen = [k for k in chosen if k != key] if key in chosen else chosen + [key]
        if not await save({"exempt_vectors": chosen}):
            return
        text, kb = _vip_exempt_view(group_id, await vip_roles.get_config(group_id), lang)
        await _safe_edit(callback, text, kb)
        await callback.answer("✅")
        return
    elif sub in ("l", "r", "rt"):
        try:
            page = max(1, int(data[-2])) if sub == "l" else max(1, int(data[4]))
        except (IndexError, ValueError):
            page = 1
        if sub in ("r", "rt"):
            try:
                target = int(data[3])
            except (IndexError, ValueError):
                await callback.answer()
                return
            if sub == "r":
                await vip_roles.revoke(group_id, target, actor_id=uid, bot=bot)
                _vip_changed(group_id, ["members"])
                await callback.answer("❌ " + tr(lang, "Rol VIP retirado", "VIP role revoked"))
            else:
                status = await vip_roles.retag(group_id, target, bot=bot)
                await callback.answer(vip_roles.TAG_STATUS_LABELS.get(status, (status, status))[0 if lang == "es" else 1][:180],
                                      show_alert=status not in ("applied",))
        listing = await vip_role_list(group_id, status="all", limit=VIPR_PER_PAGE, offset=(page - 1) * VIPR_PER_PAGE)
        text, kb = _vip_members_view(group_id, listing, page, lang)
        await _safe_edit(callback, text, kb)
        if sub == "l":
            await callback.answer()
        return
    elif sub in ("tag", "g"):
        VIP_ROLE_STATES[(bot.id, uid)] = {"group_id": group_id, "lang": lang, "mode": sub, "ts": time.time()}
        prompt = (tr(lang, "🏷️ Escribe la nueva etiqueta VIP (máx. 16 caracteres, sin emoji). Envía <code>-</code> para quitarla.",
                     "🏷️ Type the new VIP tag (max 16 characters, no emoji). Send <code>-</code> to remove it.")
                  if sub == "tag" else
                  tr(lang, "➕ Envía el <b>ID numérico</b> del usuario y, opcional, los días (0 = permanente). Ej.: <code>123456789 30</code>. "
                           "También puedes <b>reenviar</b> aquí un mensaje suyo.",
                     "➕ Send the user's <b>numeric ID</b> and, optionally, the days (0 = permanent). E.g. <code>123456789 30</code>. "
                     "You can also <b>forward</b> one of their messages here."))
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
        msg = await callback.message.answer(prompt + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"vipr_m_{group_id}_{lang}"), parse_mode="HTML")
        fire_and_forget_auto_delete([msg], delay=120)
        await callback.answer()
        return
    else:
        await callback.answer()
    text, kb = await _vip_render_main(group_id, lang)
    await _safe_edit(callback, text, kb)


async def _handle_vip_role_input(message: Message, bot: Bot, st: dict, text_input: str) -> None:
    group_id, lang, mode = int(st["group_id"]), st.get("lang", DEFAULT_LANG), st.get("mode")
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬅️ " + tr(lang, "Roles VIP", "VIP Roles"), callback_data=f"vipr_m_{group_id}_{lang}")]])
    if time.time() - st.get("ts", 0) > STATE_TTL_SECONDS:
        await message.answer(tr(lang, "⌛ La edición caducó; vuelve a abrir el menú.", "⌛ This edit expired; reopen the menu.") + PERIMETER_SIGNATURE,
                             reply_markup=back_kb, parse_mode="HTML")
        return
    if not await enforce_feature_tier_msg(message, group_id, "vip_roles", lang):
        return
    from database.database import BunkerValidationError
    raw = (text_input or "").strip()
    if mode == "tag":
        try:
            cfg = await vip_roles.update_config(group_id, {"tag_text": "" if raw == "-" else raw}, updated_by=message.from_user.id)
        except BunkerValidationError as err:
            await message.answer("❌ " + html.escape("; ".join(err.errors.values())) + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
            return
        _vip_changed(group_id, ["tag_text"])
        await message.answer(tr(lang, f"✅ Etiqueta guardada: <code>{html.escape(cfg.get('tag_text') or '—')}</code>. Se re-aplica a los VIP vigentes.",
                                f"✅ Tag saved: <code>{html.escape(cfg.get('tag_text') or '—')}</code>. It is being re-applied to active VIPs.")
                             + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        return
    # Concesión manual: "id [días]" o mensaje reenviado (si el remitente no ocultó su cuenta).
    target, days = None, 30
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) or getattr(message, "forward_from", None)
    parts = raw.split()
    if sender is not None and not getattr(sender, "is_bot", False):
        target = sender.id
    elif parts and parts[0].lstrip("-").isdigit():
        target = int(parts[0])
        parts = parts[1:]
    if parts and parts[0].isdigit():
        days = int(parts[0])
    if not target or target <= 0 or not 0 <= days <= 3650:
        await message.answer(tr(lang, "⚠️ Formato: <code>ID [días]</code> (días 0-3650, 0 = permanente).",
                                "⚠️ Format: <code>ID [days]</code> (days 0-3650, 0 = permanent).") + PERIMETER_SIGNATURE,
                             reply_markup=back_kb, parse_mode="HTML")
        return
    result = await vip_roles.grant_manual(group_id, target, days, actor_id=message.from_user.id, bot=bot,
                                          username=getattr(sender, "username", None), full_name=getattr(sender, "full_name", None))
    _vip_changed(group_id, ["members"])
    member = result.get("member") or {}
    tag = vip_roles.TAG_STATUS_LABELS.get(str(result.get("tag_status") or member.get("tag_status") or ""), ("", ""))[0 if lang == "es" else 1]
    await message.answer(tr(lang, f"✅ <code>{target}</code> es VIP · ⏳ {vip_roles.fmt_expiry(member.get('expires_at'), lang)}\n{tag}",
                            f"✅ <code>{target}</code> is now VIP · ⏳ {vip_roles.fmt_expiry(member.get('expires_at'), lang)}\n{tag}")
                         + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")


# --------------------------------------------------------------------------
# 🔗 Cuota diaria de enlaces por administrador — alq_{sub}_{group_id}_…_{lang} (solo el Dueño / Arquitecto)
# --------------------------------------------------------------------------
ALQ_PANEL_MAX_ADMINS = 12


def _alq_limit_label(limit, lang: str) -> str:
    return "♾️" if limit == -1 else str(limit)


def _alq_main_view(group_id: int, ov: dict, lang: str) -> tuple:
    cfg = ov["config"]
    now = int(time.time())
    lines = []
    for a in ov["admins"][:ALQ_PANEL_MAX_ADMINS]:
        who = html.escape(a["full_name"] or (f"@{a['username']}" if a["username"] else a["admin_id"]))
        mark = " ✳️" if a["override"] else ""
        reset = f" · ⏱️ {link_quotas.fmt_remaining(a['reset_at'] - now, lang)}" if a.get("reset_at") else ""
        blocked = f" · 🚫 {a['blocked']}" if a.get("blocked") else ""
        lines.append(f"• {who}: <b>{a['used']}/{_alq_limit_label(a['limit'], lang)}</b>{mark}{reset}{blocked}")
    text = (
        f"🔗 <b>{tr(lang, 'Cuota diaria de enlaces de administradores', 'Daily admin link quota')}</b>\n\n"
        f"• {tr(lang, 'Estado', 'Status')}: <b>{tr(lang, '🟢 Activa', '🟢 On') if cfg['enabled'] else tr(lang, '🔴 Inactiva', '🔴 Off')}</b>\n"
        f"• {tr(lang, 'Límite general', 'Default limit')}: <b>{cfg['default_limit']}</b> {tr(lang, 'por 24 h', 'per 24 h')}\n"
        f"• 📥 {tr(lang, 'Reenvíos de canales cuentan', 'Channel forwards count')}: {'✅' if cfg['count_forwards'] else '❌'}\n\n"
        + ("\n".join(lines) if lines else tr(lang, "<i>No se pudo leer la lista de administradores.</i>", "<i>Could not read the admin list.</i>"))
        + "\n\n"
        + tr(lang, "<i>Al superar su cuota, la publicación se borra y el administrador recibe un aviso con el tiempo restante. "
                   "✳️ = excepción individual. El dueño nunca se limita.</i>",
             "<i>Over quota, the post is deleted and the admin gets a notice with the time left. ✳️ = individual exception. "
             "The owner is never limited.</i>")
        + PERIMETER_SIGNATURE
    )
    g = group_id
    rows = [
        [InlineKeyboardButton(text=(tr(lang, "🟢 Activada", "🟢 Enabled") if cfg["enabled"] else tr(lang, "🔴 Desactivada", "🔴 Disabled")),
                              callback_data=f"alq_t_{g}_{lang}"),
         InlineKeyboardButton(text=f"📥 {'✅' if cfg['count_forwards'] else '❌'}", callback_data=f"alq_f_{g}_{lang}")],
        [InlineKeyboardButton(text="➖", callback_data=f"alq_d_{g}_dec_{lang}"),
         InlineKeyboardButton(text=tr(lang, f"General: {cfg['default_limit']}", f"Default: {cfg['default_limit']}"), callback_data=f"alq_m_{g}_{lang}"),
         InlineKeyboardButton(text="➕", callback_data=f"alq_d_{g}_inc_{lang}")],
    ]
    for a in ov["admins"][:ALQ_PANEL_MAX_ADMINS]:
        name = (a["full_name"] or a["username"] or a["admin_id"])[:18]
        rows.append([InlineKeyboardButton(text=f"👤 {name} · {a['used']}/{_alq_limit_label(a['limit'], lang)}",
                                          callback_data=f"alq_a_{g}_{a['admin_id']}_{lang}")])
    rows.append([InlineKeyboardButton(text="🔄 " + tr(lang, "Actualizar", "Refresh"), callback_data=f"alq_m_{g}_{lang}")])
    rows.append(_v23_back(g, lang))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _alq_admin_view(group_id: int, ov: dict, admin_id: str, lang: str) -> tuple:
    row = next((a for a in ov["admins"] if a["admin_id"] == admin_id), None) or {
        "admin_id": admin_id, "full_name": "", "username": "", "limit": ov["config"]["default_limit"], "override": False,
        "used": 0, "blocked": 0, "reset_at": None}
    who = html.escape(row["full_name"] or (f"@{row['username']}" if row["username"] else admin_id))
    text = (f"👤 <b>{who}</b> <code>{admin_id}</code>\n\n"
            f"• {tr(lang, 'Cuota', 'Quota')}: <b>{_alq_limit_label(row['limit'], lang)}</b> "
            f"({tr(lang, 'excepción', 'exception') if row['override'] else tr(lang, 'general', 'default')})\n"
            f"• {tr(lang, 'Usadas en 24 h', 'Used in 24 h')}: <b>{row['used']}</b> · 🚫 {row['blocked']}" + PERIMETER_SIGNATURE)
    g = group_id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖", callback_data=f"alq_o_{g}_{admin_id}_dec_{lang}"),
         InlineKeyboardButton(text=_alq_limit_label(row["limit"], lang), callback_data=f"alq_a_{g}_{admin_id}_{lang}"),
         InlineKeyboardButton(text="➕", callback_data=f"alq_o_{g}_{admin_id}_inc_{lang}")],
        [InlineKeyboardButton(text="♾️ " + tr(lang, "Sin límite", "Unlimited"), callback_data=f"alq_o_{g}_{admin_id}_unl_{lang}"),
         InlineKeyboardButton(text="↩️ " + tr(lang, "Usar general", "Use default"), callback_data=f"alq_o_{g}_{admin_id}_def_{lang}")],
        [InlineKeyboardButton(text="⬅️ " + tr(lang, "Cuotas de enlaces", "Link quotas"), callback_data=f"alq_m_{g}_{lang}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("alq_"))
async def cb_admin_link_quotas(callback: CallbackQuery, bot: Bot):
    data = (callback.data or "").split("_")
    lang = lang_from_callback(callback, data)
    try:
        sub, group_id = data[1], int(data[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    if not await enforce_feature_tier(callback, bot, group_id, "link_quotas", lang):
        return
    uid = callback.from_user.id
    clear_user_states(bot.id, uid)
    from database.database import ALQ_LIMIT_RANGE, BunkerValidationError, get_admin_link_quota_config, update_admin_link_quota_config

    async def save(patch: dict) -> bool:
        try:
            await update_admin_link_quota_config(group_id, patch, updated_by=uid)
        except BunkerValidationError as err:
            await callback.answer("⚠️ " + "; ".join(err.errors.values())[:180], show_alert=True)
            return False
        link_quotas.invalidate(group_id)
        emit_settings_updated_bg(group_id, "bot_console", [f"alq_{k}" for k in patch])
        return True

    cfg = await get_admin_link_quota_config(group_id)
    admin_view = None
    if sub == "t":
        if not await save({"enabled": not cfg["enabled"]}):
            return
    elif sub == "f":
        if not await save({"count_forwards": not cfg["count_forwards"]}):
            return
    elif sub == "d" and len(data) > 4:
        step = 1 if data[3] == "inc" else -1
        new = max(ALQ_LIMIT_RANGE[0], min(ALQ_LIMIT_RANGE[1], cfg["default_limit"] + step))
        if new != cfg["default_limit"] and not await save({"default_limit": new}):
            return
    elif sub == "a" and len(data) > 4:
        admin_view = data[3]
    elif sub == "o" and len(data) > 5 and data[3].isdigit():
        admin_view, op = data[3], data[4]
        cur = cfg["overrides"].get(admin_view, cfg["default_limit"])
        if op == "unl":
            value = -1
        elif op == "def":
            value = None
        else:
            base = cfg["default_limit"] if cur == -1 else cur
            value = max(ALQ_LIMIT_RANGE[0], min(ALQ_LIMIT_RANGE[1], base + (1 if op == "inc" else -1)))
        if not await save({"overrides": {admin_view: value}}):
            return
    overview = await link_quotas.get_overview(_master_bot_or(bot), group_id)
    text, kb = (_alq_admin_view(group_id, overview, admin_view, lang) if admin_view else _alq_main_view(group_id, overview, lang))
    await _safe_edit(callback, text, kb)
    await callback.answer()



# ==========================================================================
# ⚖️ v24 · APELACIÓN DE BANEOS POR TELEGRAM STARS — apl_{sub}_{group_id}_…_{lang} (solo el Dueño / Arquitecto)
# ==========================================================================
APL_PER_PAGE = 6
APL_FILTERS = ("all", "open", "paid", "closed")


def _apl_main_view(group_id: int, cfg: dict, data: dict, flt: str, lang: str) -> tuple:
    STATUS_LABELS = _ban_appeals_module().STATUS_LABELS
    s = data.get("summary") or {}
    lines = []
    for rec in data.get("records") or []:
        who = html.escape(rec.get("full_name") or (f"@{rec['username']}" if rec.get("username") else str(rec["user_id"])))
        state = STATUS_LABELS.get(rec.get("status"), (rec.get("status"), rec.get("status")))[0 if lang == "es" else 1]
        when = time.strftime("%d/%m %H:%M", time.gmtime(int(rec.get("created_at") or 0)))
        reason = html.escape(((rec.get("reason_es") if lang == "es" else rec.get("reason_en")) or rec.get("reason_key") or "")[:60])
        lines.append(f"• <b>{who}</b> <code>{rec['user_id']}</code> · {when} UTC\n   {state} · {rec.get('price_stars')} ⭐\n   <i>{reason}</i>")
    flt_name = {"all": tr(lang, "Todas", "All"), "open": tr(lang, "Abiertas", "Open"), "paid": tr(lang, "Pagadas", "Paid"),
                "closed": tr(lang, "Cerradas", "Closed")}
    text = (
        f"⚖️ <b>{tr(lang, 'Apelación de baneos', 'Ban appeals')}</b>\n\n"
        f"• {tr(lang, 'Estado', 'Status')}: <b>{tr(lang, '🟢 Activa', '🟢 On') if cfg['enabled'] else tr(lang, '🔴 Inactiva', '🔴 Off')}</b>\n"
        f"• 💫 {tr(lang, 'Precio', 'Price')}: <b>{cfg['price_stars']} ⭐</b> · 🔗 {tr(lang, 'Enlace', 'Link')}: <b>{cfg['link_ttl_min']} min</b>"
        f" · ⏳ {tr(lang, 'Oferta', 'Offer')}: <b>{cfg['window_days']} {tr(lang, 'días', 'days')}</b>\n"
        f"• 🔁 {tr(lang, 'Máx. por usuario (90 días)', 'Max per user (90 days)')}: <b>{cfg['max_per_user'] or '♾️'}</b>\n\n"
        f"📊 {tr(lang, 'Apelaciones', 'Appeals')}: <b>{s.get('appeals', 0)}</b> · {tr(lang, 'pagadas', 'paid')}: <b>{s.get('paid', 0)}</b>"
        f" · ⭐ <b>{s.get('stars', 0)}</b> · ↩️ {s.get('refunded', 0)} · 📭 {s.get('undelivered', 0)}\n\n"
        f"<b>{flt_name.get(flt, flt)}</b>\n" + ("\n".join(lines) if lines else tr(lang, "<i>Sin registros.</i>", "<i>No records.</i>"))
        + "\n\n" + tr(lang,
                      "<i>Solo los baneos AUTOMÁTICOS por límite de advertencias son apelables. El usuario recibe el motivo y la "
                      "factura por privado; al pagar se le desbanea y recibe un enlace de un solo uso. Si el bot no puede "
                      "desbanear, el pago se reembolsa solo.</i>",
                      "<i>Only AUTOMATIC bans from the warn limit are appealable. The user gets the reason and the invoice in "
                      "private; on payment they are unbanned and receive a single-use link. If the bot cannot unban, the "
                      "payment is refunded automatically.</i>")
        + PERIMETER_SIGNATURE
    )
    g = group_id
    rows = [
        [InlineKeyboardButton(text=(tr(lang, "🟢 Activada", "🟢 Enabled") if cfg["enabled"] else tr(lang, "🔴 Desactivada", "🔴 Disabled")),
                              callback_data=f"apl_t_{g}_{flt}_{lang}"),
         InlineKeyboardButton(text=f"💫 {cfg['price_stars']} ⭐", callback_data=f"apl_p_{g}_{flt}_{lang}"),
         InlineKeyboardButton(text="✏️ ⭐", callback_data=f"apl_e_{g}_{flt}_{lang}")],
        [InlineKeyboardButton(text=f"🔗 {cfg['link_ttl_min']} min", callback_data=f"apl_l_{g}_{flt}_{lang}"),
         InlineKeyboardButton(text=f"⏳ {cfg['window_days']} d", callback_data=f"apl_w_{g}_{flt}_{lang}"),
         InlineKeyboardButton(text=f"🔁 {cfg['max_per_user'] or '♾️'}", callback_data=f"apl_x_{g}_{flt}_{lang}")],
        [InlineKeyboardButton(text=("✓ " if f == flt else "") + flt_name[f], callback_data=f"apl_m_{g}_{f}_{lang}") for f in APL_FILTERS],
        [InlineKeyboardButton(text="🔄 " + tr(lang, "Actualizar", "Refresh"), callback_data=f"apl_m_{g}_{flt}_{lang}")],
        _v23_back(g, lang),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _apl_render(group_id: int, flt: str, lang: str) -> tuple:
    from database.database import appeal_list, get_appeal_config
    cfg = await get_appeal_config(group_id)
    data = await appeal_list(group_id, status=flt, limit=APL_PER_PAGE)
    return _apl_main_view(group_id, cfg, data, flt, lang)


@router.callback_query(F.data.startswith("apl_"))
async def cb_ban_appeals(callback: CallbackQuery, bot: Bot):
    data = (callback.data or "").split("_")
    lang = lang_from_callback(callback, data)
    try:
        sub, group_id = data[1], int(data[2])
        flt = data[3] if data[3] in APL_FILTERS else "all"
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not await verify_admin_privileges(callback, bot, group_id):
        return
    if not await enforce_feature_tier(callback, bot, group_id, "ban_appeals", lang):
        return
    uid = callback.from_user.id
    clear_user_states(bot.id, uid)
    from database.database import (APPEAL_LINK_TTL_CHOICES, APPEAL_MAX_CHOICES, APPEAL_PRICE_CHOICES, BunkerValidationError,
                                   get_appeal_config, update_appeal_config)
    cfg = await get_appeal_config(group_id)

    def nxt(seq, cur):
        return seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]

    patch = None
    if sub == "t":
        patch = {"enabled": not cfg["enabled"]}
    elif sub == "p":
        patch = {"price_stars": nxt(APPEAL_PRICE_CHOICES, cfg["price_stars"])}
    elif sub == "l":
        patch = {"link_ttl_min": nxt(APPEAL_LINK_TTL_CHOICES, cfg["link_ttl_min"])}
    elif sub == "w":
        patch = {"window_days": nxt((1, 3, 7, 14, 30), cfg["window_days"])}
    elif sub == "x":
        patch = {"max_per_user": nxt(APPEAL_MAX_CHOICES, cfg["max_per_user"])}
    elif sub == "e":
        APPEAL_PRICE_STATES[(bot.id, uid)] = {"group_id": group_id, "lang": lang, "flt": flt, "ts": time.time()}
        t = TEXTS.get(lang, TEXTS[DEFAULT_LANG])
        msg = await callback.message.answer(
            tr(lang, "💫 Escribe el precio de la apelación en Stars (1-10000).", "💫 Type the appeal price in Stars (1-10000).")
            + PERIMETER_SIGNATURE, reply_markup=_cancel_kb(t, f"apl_m_{group_id}_{flt}_{lang}"), parse_mode="HTML")
        fire_and_forget_auto_delete([msg], delay=120)
        await callback.answer()
        return
    if patch is not None:
        try:
            await update_appeal_config(group_id, patch)
        except BunkerValidationError as err:
            await callback.answer("⚠️ " + "; ".join(err.errors.values())[:180], show_alert=True)
            return
        emit_settings_updated_bg(group_id, "bot_console", [f"appeal_{k}" for k in patch])
        await callback.answer("✅")
    else:
        await callback.answer()
    text, kb = await _apl_render(group_id, flt, lang)
    await _safe_edit(callback, text, kb)


async def _handle_appeal_price_input(message: Message, bot: Bot, st: dict, text_input: str) -> None:
    group_id, lang, flt = int(st["group_id"]), st.get("lang", DEFAULT_LANG), st.get("flt", "all")
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬅️ " + tr(lang, "Apelaciones de baneo", "Ban appeals"), callback_data=f"apl_m_{group_id}_{flt}_{lang}")]])
    if time.time() - st.get("ts", 0) > STATE_TTL_SECONDS:
        await message.answer(tr(lang, "⌛ La edición caducó; vuelve a abrir el menú.", "⌛ This edit expired; reopen the menu.")
                             + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        return
    if not await enforce_feature_tier_msg(message, group_id, "ban_appeals", lang):
        return
    from database.database import BunkerValidationError, update_appeal_config
    raw = (text_input or "").strip().replace("⭐", "").strip()
    try:
        if not raw.isdigit():
            raise BunkerValidationError({"price_stars": tr(lang, "escribe solo un número entero.", "type a whole number only.")})
        cfg = await update_appeal_config(group_id, {"price_stars": int(raw)})
    except BunkerValidationError as err:
        await message.answer("❌ " + html.escape("; ".join(err.errors.values())) + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")
        return
    emit_settings_updated_bg(group_id, "bot_console", ["appeal_price_stars"])
    await message.answer(tr(lang, f"✅ Precio de apelación: <b>{cfg['price_stars']} ⭐</b>. Aplica a los próximos baneos.",
                            f"✅ Appeal price: <b>{cfg['price_stars']} ⭐</b>. Applies to future bans.")
                         + PERIMETER_SIGNATURE, reply_markup=back_kb, parse_mode="HTML")