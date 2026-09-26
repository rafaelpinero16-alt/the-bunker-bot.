import asyncio
import logging
import sys
import os
import urllib.parse
import json
import hmac
import hashlib
import base64
import time
from importlib import import_module
from dotenv import load_dotenv

if __name__ == "__main__":
    sys.modules.setdefault("main", sys.modules[__name__])

load_dotenv()

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, ErrorEvent, Update, ChatMemberUpdated
from aiogram.exceptions import TelegramUnauthorizedError

_fastapi = import_module("fastapi")
FastAPI = _fastapi.FastAPI
Header = _fastapi.Header
HTTPException = _fastapi.HTTPException
Body = _fastapi.Body
CORSMiddleware = import_module("fastapi.middleware.cors").CORSMiddleware
uvicorn = import_module("uvicorn")

from database.database import (
    init_db, 
    get_all_active_clone_tokens, 
    get_or_create_user,
    get_user_global_stats,
    get_user_channels,
    get_user_groups,
    get_user_subscribers_audit,
    get_group_tier,
    get_db_connection,
    register_user_group,
    record_chat_activity,
    get_chat_dashboard_data,
    get_chat_timeseries_stats,
    get_chat_top_users,
    get_chat_admin_stats,
    update_chat_operational_settings,
    get_user_by_web_session,
    mark_payment_processed
)
from middlewares.anti_spam import AntiSpamMiddleware
from handlers import (
    payments,
    user_private, 
    moderation, 
    admin_group, 
    ecosystem, 
    vc_manager, 
    groups
)
from handlers.user_private import (
    send_official_welcome,
    set_master_bot_id,
    set_master_bot_username,
    revoke_bot_clone_db,
    get_active_user_channels,
    get_active_user_groups
)
from assistant import (
    start_voice_radar, 
    close_all_sentinels
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID_RAW = os.getenv("ADMIN_GROUP_ID")
WEBAPP_URL = "https://thebunkerapp2.netlify.app/"

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN no está definido en las variables de entorno.")
if not ADMIN_GROUP_ID_RAW:
    raise RuntimeError("❌ ADMIN_GROUP_ID no está definido en las variables de entorno.")

ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_RAW)
CREATOR_FALLBACK_ID = 8269470905

active_clone_tasks = {}
dp = Dispatcher()
master_bot_instance: Bot = None

# ==========================================
# 🌐 CONFIGURACIÓN DEL SERVIDOR WEB API (FASTAPI)
# ==========================================
app = FastAPI(title="The Bunker OS Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_telegram_user_id(init_data: str) -> int:
    """Valida la firma HMAC del initData de Telegram WebApp y extrae el user_id legítimo."""
    if not init_data:
        return 0
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return 0

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logging.warning("⚠️ [initData] Firma inválida rechazada — posible intento de suplantación.")
            return 0

        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return 0

        user_json = json.loads(parsed.get("user", "{}"))
        return int(user_json.get("id", 0))
    except Exception as e:
        logging.debug(f"Error parseando initData: {e}")
        return 0

# ==========================================
# 🔐 AUTENTICACIÓN DUAL: Telegram initData nativo O Sesión Web (Widget/Bot)
# ==========================================
SESSION_SECRET = hashlib.sha256(f"bunker-web-session::{BOT_TOKEN}".encode()).digest()
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 días de validez de sesión web

def issue_session_token(user_id: int, first_name: str = "", username: str = "", photo_url: str = "") -> str:
    """Emite un token de sesión web firmado (HMAC-SHA256)."""
    payload = {
        "uid": int(user_id),
        "fn": first_name or "",
        "un": username or "",
        "ph": photo_url or "",
        "iat": int(time.time())
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    raw_b64 = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    signature = hmac.new(SESSION_SECRET, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_b64}.{signature}"

def verify_session_token(token: str):
    """Valida un token de sesión web: firma HMAC + expiración."""
    try:
        if not token or "." not in token:
            return None
        raw_b64, signature = token.rsplit(".", 1)
        expected_signature = hmac.new(SESSION_SECRET, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
        if int(time.time()) - int(payload.get("iat", 0)) > SESSION_TTL_SECONDS:
            return None
        return payload
    except Exception:
        return None

def verify_telegram_widget_login(data: dict) -> bool:
    """Verifica la autenticidad de los datos entregados por el Telegram Login Widget."""
    if not isinstance(data, dict):
        return False
    received_hash = data.get("hash")
    if not received_hash:
        return False

    check_fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields.keys()))

    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, str(received_hash)):
        return False

    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False
    if time.time() - auth_date > 86400:
        return False

    return True

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS

def resolve_user_id(x_telegram_init_data: str = None, authorization: str = None) -> int:
    """Resuelve el user_id operador desde initData o Token Bearer sin fallbacks vulnerables."""
    uid = parse_telegram_user_id(x_telegram_init_data)
    if uid:
        return uid
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            return int(payload["uid"])
    return 0

def require_authenticated_user(x_telegram_init_data: str = None, authorization: str = None) -> int:
    """Lanza 401 si no hay identidad comprobada."""
    uid = resolve_user_id(x_telegram_init_data, authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="No autenticado. Abre la app desde Telegram o inicia sesión.")
    return uid

async def assert_chat_ownership(user_id: int, chat_id: int):
    """Lanza 403 si el operador no es dueño o administrador del chat (Protección Anti-IDOR)."""
    if is_super_admin(user_id):
        return
    owned_channels = await get_user_channels(user_id)
    owned_groups = await get_user_groups(user_id)
    owned_ids = {int(c[0]) for c in owned_channels} | {int(g[0]) for g in owned_groups}
    if chat_id not in owned_ids:
        raise HTTPException(status_code=403, detail="No tienes permisos de administración sobre este chat.")

# --- 0. AUTENTICACIÓN WEB DUAL (WIDGET Y CANJE DE TOKEN TEMPORAL /LOGIN) ---
@app.post("/api/auth/telegram-widget")
async def api_auth_telegram_widget(payload: dict = Body(...)):
    if not verify_telegram_widget_login(payload):
        raise HTTPException(status_code=401, detail="Firma de autenticación de Telegram inválida.")

    try:
        user_id = int(payload.get("id", 0))
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        raise HTTPException(status_code=400, detail="ID de usuario ausente en el payload del widget.")

    first_name = payload.get("first_name", "") or ""
    username = payload.get("username", "") or ""
    photo_url = payload.get("photo_url", "") or ""

    try:
        await get_or_create_user(user_id, username or "Sin username", first_name or "Operador")
    except Exception as e:
        logging.warning(f"⚠️ [Auth Widget] No se pudo registrar/actualizar el usuario en BD: {e}")

    token = issue_session_token(user_id, first_name, username, photo_url)
    return {
        "status": "success",
        "session_token": token,
        "user": {"id": user_id, "first_name": first_name, "username": username, "photo_url": photo_url}
    }

@app.post("/api/auth/exchange-token")
async def api_exchange_web_token(payload: dict = Body(...)):
    """Canjea el token temporal de 5 minutos generado por el comando /login en privado."""
    temp_token = payload.get("token")
    if not temp_token:
        raise HTTPException(status_code=400, detail="Token no proporcionado.")
    
    user_id = await get_user_by_web_session(temp_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token temporal inválido o expirado.")
    
    session_token = issue_session_token(user_id, first_name="Operador", username="", photo_url="")
    return {
        "status": "success",
        "session_token": session_token,
        "user": {"id": user_id, "first_name": "Operador", "username": "", "photo_url": ""}
    }

@app.get("/api/auth/session-check")
async def api_auth_session_check(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el encabezado Authorization Bearer.")
    token = authorization.split(" ", 1)[1].strip()
    payload = verify_session_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")
    return {
        "status": "success",
        "user_id": payload.get("uid"),
        "first_name": payload.get("fn", ""),
        "username": payload.get("un", ""),
        "photo_url": payload.get("ph", "")
    }

# --- HELPERS DE CONSULTA SEGURA FUERA DEL EVENT LOOP ---
def _get_global_channels_sync():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, group_name FROM user_groups WHERE chat_type = 'channel'")
        return cursor.fetchall()

def _get_global_groups_sync():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, group_name FROM user_groups WHERE chat_type != 'channel' OR chat_type IS NULL")
        return cursor.fetchall()

def _get_groups_count_sync():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_groups")
        row = cursor.fetchone()
        return row[0] if row else 0


# --- 1. TELEMETRÍA GLOBAL Y FILTRADA ---
@app.get("/api/stats")
async def api_stats(
    context: str = "global", 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        stats = await get_user_global_stats(user_id)
        return stats
    except Exception as e:
        logging.error(f"❌ [API Stats Error]: {e}")
        return {
            "subscribers": 0, "revenue_stars": 0, "verified": 0, "expelled": 0, "purges": 0,
            "perimeter": {
                "captcha": "Activo 🟢", 
                "autolower": "2% Activo 🟢", 
                "shield": "Blindado 🟢", 
                "broadcast": "Worker Activo 🟢",
                "captcha_active": True,
                "autolower_active": True,
                "shield_active": True,
                "linklock_active": False
            }
        }


# --- 2. CANALES VINCULADOS (VERSIÓN ÚNICA Y SIN BLOQUEOS) ---
@app.get("/api/channels")
async def api_channels(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        channels = []
        if master_bot_instance:
            try:
                channels = await get_active_user_channels(master_bot_instance, user_id)
            except Exception:
                channels = await get_user_channels(user_id)
        else:
            channels = await get_user_channels(user_id)

        # 🛡️ Blindaje anti-vacío ejecutado fuera del event loop
        if not channels:
            channels = await asyncio.to_thread(_get_global_channels_sync)

        res = []
        for ch_id, ch_name in channels:
            tier = await get_group_tier(ch_id)
            member_count = 0
            resolved_title = ch_name
            if master_bot_instance:
                try:
                    chat_obj = await master_bot_instance.get_chat(ch_id)
                    resolved_title = chat_obj.title or ch_name
                    member_count = await master_bot_instance.get_chat_member_count(ch_id)
                except Exception:
                    pass

            timeseries = await get_chat_timeseries_stats(ch_id)
            activity_curve = timeseries.get("messages", [])[-7:]
            if len(activity_curve) < 7:
                activity_curve = [0] * (7 - len(activity_curve)) + activity_curve

            res.append({
                "id": str(ch_id),
                "title": resolved_title,
                "type": "channel",
                "license_status": "active" if tier != "free" else "expired",
                "members": member_count,
                "activity": activity_curve,
                "joined": 0,
                "left": 0,
                "avatar_url": None
            })
        return {"channels": res}
    except Exception as e:
        logging.error(f"❌ [API Channels Error]: {e}")
        return {"channels": []}


# --- 3. COMUNIDADES BLINDADAS (SIN BLOQUEOS) ---
@app.get("/api/groups")
async def api_groups(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        groups_list = []
        if master_bot_instance:
            try:
                groups_list = await get_active_user_groups(master_bot_instance, user_id)
            except Exception:
                groups_list = await get_user_groups(user_id)
        else:
            groups_list = await get_user_groups(user_id)
        
        # 🛡️ Blindaje anti-vacío ejecutado fuera del event loop
        if not groups_list:
            groups_list = await asyncio.to_thread(_get_global_groups_sync)

        res = []
        for g_id, g_name in groups_list:
            tier = await get_group_tier(g_id)
            member_count = 0
            resolved_title = g_name
            if master_bot_instance:
                try:
                    chat_obj = await master_bot_instance.get_chat(g_id)
                    resolved_title = chat_obj.title or g_name
                    member_count = await master_bot_instance.get_chat_member_count(g_id)
                except Exception:
                    pass

            timeseries = await get_chat_timeseries_stats(g_id)
            activity_curve = timeseries.get("messages", [])[-7:]
            if len(activity_curve) < 7:
                activity_curve = [0] * (7 - len(activity_curve)) + activity_curve

            res.append({
                "id": str(g_id),
                "title": resolved_title,
                "type": "supergroup",
                "license_status": "active" if tier != "free" else "expired",
                "members": member_count,
                "activity": activity_curve,
                "joined": 0,
                "left": 0,
                "avatar_url": None
            })
        return {"groups": res}
    except Exception as e:
        logging.error(f"❌ [API Groups Error]: {e}")
        return {"groups": []}


# --- 4. ENDPOINT TÁCTICO: SINCRONIZACIÓN FORZADA EN VIVO ---
@app.post("/api/sync-chats")
async def api_sync_chats(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    
    try:
        await register_user_group(
            user_id=user_id,
            group_id=ADMIN_GROUP_ID,
            group_name="The Bunker Admin Matrix",
            chat_type="supergroup"
        )
    except Exception:
        pass

    synced_channels = 0
    synced_groups = 0
    
    if master_bot_instance:
        try:
            channels = await get_active_user_channels(master_bot_instance, user_id)
            groups_list = await get_active_user_groups(master_bot_instance, user_id)
            synced_channels = len(channels)
            synced_groups = len(groups_list)
        except Exception as sync_err:
            logging.warning(f"⚠️ [Sync Chats Active Scan Error]: {sync_err}")
            channels = await get_user_channels(user_id)
            groups_list = await get_user_groups(user_id)
            synced_channels = len(channels)
            synced_groups = len(groups_list)
    else:
        channels = await get_user_channels(user_id)
        groups_list = await get_user_groups(user_id)
        synced_channels = len(channels)
        synced_groups = len(groups_list)

    if synced_groups == 0:
        synced_groups = await asyncio.to_thread(_get_groups_count_sync)

    return {
        "status": "success",
        "total_channels": synced_channels,
        "total_groups": max(1, synced_groups)
    }


# --- 5. AUDITOR DE SUSCRIPTORES ---
@app.get("/api/subscribers")
async def api_subscribers(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        subs = await get_user_subscribers_audit(user_id)
        return {"subscribers": subs if subs is not None else []}
    except Exception as e:
        logging.error(f"❌ [API Subscribers Error] Usuario {user_id}: {e}")
        return {"subscribers": []}


# --- 6. DASHBOARD GRANULAR POR CHAT ---
@app.get("/api/chat/{chat_id}/dashboard")
async def api_chat_dashboard(
    chat_id: str, 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        data = await get_chat_dashboard_data(numeric_id)
        
        if not isinstance(data, dict):
            data = {
                "chat_id": str(chat_id),
                "plan": {"name": "Free", "status": "empty"},
                "log_channel": {"enabled": False, "channel_id": None},
                "modules": {"active": 0, "total": 91},
                "protection": {"enabled": True, "spam_mode": "smart", "timezone": "Bogota (UTC-05)", "language": "ES"},
                "modules_errors": [],
                "footer_metrics": {}
            }

        if master_bot_instance:
            try:
                chat_obj = await master_bot_instance.get_chat(numeric_id)
                data["title"] = chat_obj.title or f"Chat {chat_id}"
            except Exception:
                data.setdefault("title", f"Chat {chat_id}")
        else:
            data.setdefault("title", f"Chat {chat_id}")

        return data
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero válido.")
    except Exception as e:
        logging.error(f"❌ [API Chat Dashboard Error] Chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- 7. ESTADÍSTICAS TEMPORALES EN VIVO ---
@app.get("/api/chat/{chat_id}/stats")
async def api_chat_stats(
    chat_id: str, 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        stats = await get_chat_timeseries_stats(numeric_id)
        if not isinstance(stats, dict):
            return {"months": [], "mau": [], "messages": [], "messages_per_user": []}
        return stats
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero válido.")
    except Exception as e:
        logging.error(f"❌ [API Chat Stats Error] Chat {chat_id}: {e}")
        return {"months": [], "mau": [], "messages": [], "messages_per_user": []}


# --- 8. RENDIMIENTO DE ADMINISTRADORES ---
@app.get("/api/chat/{chat_id}/admin-stats")
async def api_chat_admin_stats(
    chat_id: str, 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        admins = await get_chat_admin_stats(numeric_id)
        return {"admins": admins if isinstance(admins, list) else []}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero válido.")
    except Exception as e:
        logging.error(f"❌ [API Admin Stats Error]: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- 9. TOP 10 USUARIOS MÁS ACTIVOS ---
@app.get("/api/chat/{chat_id}/top-users")
async def api_chat_top_users(
    chat_id: str, 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        top_users = await get_chat_top_users(numeric_id, limit=10)
        return {"top_users": top_users if isinstance(top_users, list) else []}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero válido.")
    except Exception as e:
        logging.error(f"❌ [API Top Users Error] Chat {chat_id}: {e}")
        return {"top_users": []}


# --- 10. GUARDAR CONFIGURACIONES EN VIVO ---
@app.post("/api/chat/{chat_id}/settings")
async def api_update_chat_settings(
    chat_id: str, 
    payload: dict = Body(...), 
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        if not isinstance(payload, dict):
            payload = {}
        await update_chat_operational_settings(numeric_id, payload)
        logging.info(f"⚙️ [Configuración Guardada para {chat_id}]: {payload}")
        return {"status": "success", "chat_id": chat_id, "updated": payload}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero válido.")
    except Exception as e:
        logging.error(f"❌ [API Settings Save Error] Chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/affiliates/me")
async def api_affiliates(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"), 
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    return {"invited_communities": 0, "earned_stars": 0, "balance": 0}

async def run_fastapi_server():
    port = int(os.getenv("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

# ==========================================
# ⚙️ GESTIÓN DE CALLBACKS Y CLONES DE AIOGRAM
# ==========================================
fallback_router = Router(name="callback_fallback")

@fallback_router.callback_query()
async def cb_unhandled_fallback(callback: CallbackQuery, bot: Bot):
    if callback.data and callback.data.startswith("chplans_"):
        try:
            return await user_private.cb_channel_plans_dispatch(callback, bot)
        except Exception as ex:
            logging.error(f"❌ [Fallback Rescue chplans] Fallo al despachar plan: {ex}", exc_info=True)

    user_id = callback.from_user.id if callback.from_user else 0
    logging.warning(f"🧭 [Callback sin handler] bot_id={bot.id} usuario={user_id} callback_data={callback.data!r}")
    
    lang_code = callback.from_user.language_code if callback.from_user else ""
    is_es = bool(lang_code and lang_code.startswith("es"))
    text = "⚠️ Este botón ya no está activo. Envía /start para renovar el menú." if is_es else "⚠️ This button is no longer active. Send /start to refresh the menu."
    
    try:
        await callback.answer(text, show_alert=True)
    except Exception:
        pass


@dp.errors()
async def on_dispatcher_error(event: ErrorEvent) -> bool:
    update = event.update
    update_id = update.update_id if update else 0
    logging.error(f"❌ [Error de despacho] update_id={update_id}: {event.exception!r}", exc_info=event.exception)
    
    if update and update.callback_query:
        try:
            await update.callback_query.answer("⚠️ Error temporal / Temporary error", show_alert=False)
        except Exception:
            pass
    return True


# ==========================================
# 📡 AUTO-DETECCIÓN DE CHATS (MY_CHAT_MEMBER)
# ==========================================
@dp.my_chat_member()
async def on_bot_promoted_or_added(event: ChatMemberUpdated, bot: Bot):
    try:
        new_status = event.new_chat_member.status
        if new_status in ("administrator", "member"):
            chat_type = "channel" if event.chat.type == "channel" else "supergroup"
            promoter_id = event.from_user.id if event.from_user else CREATOR_FALLBACK_ID
            chat_title = event.chat.title or f"Chat {event.chat.id}"

            await register_user_group(
                user_id=promoter_id,
                group_id=event.chat.id,
                group_name=chat_title,
                chat_type=chat_type
            )
            logging.info(f"🎯 [Auto-Detección Exitosa]: '{chat_title}' ({event.chat.id}) vinculado a usuario {promoter_id} como {chat_type}.")
    except Exception as e:
        logging.error(f"❌ [Error en my_chat_member auto-detección]: {e}", exc_info=True)


async def _dispatch_clone_update(clone_bot: Bot, bot_username: str, update: Update):
    try:
        is_private_start = False
        if update.callback_query:
            cq = update.callback_query
            user_id = cq.from_user.id if cq.from_user else 0
            logging.info(f"🔘 [Clon @{bot_username}] Callback de {user_id}: {cq.data!r}")
        elif update.message:
            if update.message.chat.type in ("group", "supergroup") and update.message.from_user:
                try:
                    await record_chat_activity(
                        group_id=update.message.chat.id,
                        user_id=update.message.from_user.id,
                        full_name=update.message.from_user.full_name or "Usuario",
                        username=update.message.from_user.username or "",
                        is_reply=bool(update.message.reply_to_message),
                        is_admin=False
                    )
                except Exception as db_act_err:
                    logging.warning(f"⚠️ [Actividad Clon BD]: {db_act_err}")

            if update.message.chat.type == "private":
                text = (update.message.text or "").strip()
                user_id = update.message.from_user.id if update.message.from_user else 0
                is_private_start = text.startswith("/start")
                logging.info(f"📩 [Clon @{bot_username}] Mensaje de {user_id}: '{text}'")

        result = await dp.feed_update(clone_bot, update)

        if result is UNHANDLED and is_private_start and update.message:
            user = update.message.from_user
            logging.warning(f"⚠️ [Clon @{bot_username}] /start sin handler; aplicando bienvenida de respaldo.")
            try:
                if user:
                    await get_or_create_user(user.id, user.username or "Sin username", user.full_name)
            except Exception as db_err:
                logging.error(f"Aviso BD Clon: {db_err}")
            await send_official_welcome(clone_bot, update.message.chat.id, user, bot_username)
    except Exception as feed_err:
        logging.error(f"❌ [Error en clon @{bot_username}]: {feed_err}", exc_info=True)


async def _clone_worker(clone_bot: Bot, token: str):
    allowed_updates = ["message", "callback_query", "pre_checkout_query", "chat_join_request", "chat_member", "my_chat_member"]
    try:
        await clone_bot.delete_webhook(drop_pending_updates=True)
        bot_info = await clone_bot.get_me()
        bot_username = bot_info.username or "BotClon"
        logging.info(f"🧬 [Bot Clon Activo]: Poller iniciado para @{bot_username} (ID: {bot_info.id}).")
    except TelegramUnauthorizedError as auth_err:
        logging.error(f"❌ [Error Fatal] El token del clon {token[:10]} fue revocado o es inválido: {auth_err}")
        try:
            def _revoke_sync():
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = '' WHERE bot_token = ?", (token,))
                    conn.commit()
            await asyncio.to_thread(_revoke_sync)
        except Exception:
            pass
        finally:
            active_clone_tasks.pop(token, None)
            return
    except Exception as e:
        logging.error(f"❌ [Error Handshake Clon {token[:10]}]: {e}")
        return

    offset = None
    while token in active_clone_tasks:
        try:
            updates = await clone_bot.get_updates(offset=offset, timeout=15, allowed_updates=allowed_updates)
            for update in updates:
                offset = update.update_id + 1
                await _dispatch_clone_update(clone_bot, bot_username, update)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"⚠️ [Loop Clon @{bot_username}]: {e}")
            await asyncio.sleep(2)


async def start_clone_polling_task(token: str):
    if not token or token in active_clone_tasks:
        return
    try:
        clone_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        task = asyncio.create_task(_clone_worker(clone_bot, token))
        active_clone_tasks[token] = {"bot": clone_bot, "task": task}
    except Exception as e:
        logging.error(f"⚠️ [Error Inicializando Bot Clon {token[:10]}]: {e}")


async def stop_clone_polling_task(token: str):
    task_data = active_clone_tasks.pop(token, None)
    if task_data:
        task_data["task"].cancel()
        try:
            await task_data["bot"].session.close()
        except Exception:
            pass


def trigger_dynamic_clone(token: str):
    asyncio.create_task(start_clone_polling_task(token))


def trigger_disconnect_clone(token: str):
    asyncio.create_task(stop_clone_polling_task(token))


async def main():
    global master_bot_instance
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
        stream=sys.stdout
    )
    logging.getLogger("aiogram.event").setLevel(logging.INFO)

    init_db()
    print("🛡️ [Base de Datos]: Esquema relacional y matrices perimetrales inicializadas.")

    asyncio.create_task(run_fastapi_server())
    print(f"🌐 [API Backend Web]: Servidor FastAPI activo en puerto {os.getenv('PORT', 8080)}.")

    master_bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    master_bot_instance = master_bot
    set_master_bot_id(master_bot.id)
    try:
        master_info = await master_bot.get_me()
        set_master_bot_username(master_info.username or "")
    except Exception as e:
        print(f"⚠️ [Aviso Identidad Maestro]: No se pudo resolver el @username del Maestro: {e}")

    dp.message.middleware(AntiSpamMiddleware())

    @dp.message.outer_middleware()
    async def track_chat_activity_middleware(handler, event, data):
        if event.chat and event.chat.type in ("group", "supergroup") and event.from_user:
            try:
                is_admin = False
                try:
                    member = await event.chat.get_member(event.from_user.id)
                    is_admin = member.status in ("creator", "administrator")
                except Exception:
                    pass

                await record_chat_activity(
                    group_id=event.chat.id,
                    user_id=event.from_user.id,
                    full_name=event.from_user.full_name or "Usuario",
                    username=event.from_user.username or "",
                    is_reply=bool(event.reply_to_message),
                    is_admin=is_admin
                )
            except Exception as act_err:
                logging.warning(f"⚠️ [Fallo al registrar actividad]: {act_err}")
        return await handler(event, data)

    dp.include_router(payments.router)
    dp.include_router(user_private.router)
    dp.include_router(moderation.router)
    dp.include_router(admin_group.router)
    dp.include_router(ecosystem.router)
    dp.include_router(vc_manager.router)
    dp.include_router(groups.router)
    dp.include_router(fallback_router)

    print("📡 [Radar MTProto]: Desplegando clúster de Centinelas...")
    try:
        start_voice_radar(master_bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    asyncio.create_task(ecosystem.start_channel_broadcast_worker(master_bot))

    print("🧬 [Gestor de Clones]: Sincronizando bots clones...")
    try:
        stored_clones = await get_all_active_clone_tokens()
        for clone_token in stored_clones:
            await start_clone_polling_task(clone_token)
        print(f"🚀 ¡El Búnker Bot Maestro y {len(stored_clones)} Clones están completamente operativos, Rafa!")
    except Exception as e:
        print(f"⚠️ [Aviso Clones BD]: No se pudieron precargar los clones: {e}")

    try:
        await master_bot.delete_webhook(drop_pending_updates=True)
        allowed_updates = dp.resolve_used_update_types()
        required_updates = ["message", "callback_query", "pre_checkout_query", "chat_join_request", "chat_member", "my_chat_member"]
        for update_type in required_updates:
            if update_type not in allowed_updates:
                allowed_updates.append(update_type)

        await dp.start_polling(master_bot, allowed_updates=allowed_updates)
    finally:
        print("🛑 [Sistema]: Deteniendo clúster y cerrando sesiones...")
        for token in list(active_clone_tasks.keys()):
            await stop_clone_polling_task(token)
        await close_all_sentinels()
        await master_bot.session.close()
        print("🛡️ [Sistema]: El Búnker se ha cerrado de forma ordenada.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ [Sistema]: Bot detenido manualmente por el operador.")