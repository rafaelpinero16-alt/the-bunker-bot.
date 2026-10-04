"""
main.py — The Bunker OS (Aiogram 3.x / FastAPI / Pyrogram)

Núcleo de arranque maestro, sincronización de enrutadores, pasarela Web API y
administración concurrente de clones y Centinelas acústicos.
Fase 3: Telemetría Reactiva en Vivo mediante WebSockets (FastAPI) + Endpoints de Heatmaps, Reputación y Backups.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
from contextlib import asynccontextmanager
import hashlib
import hmac
import html
import json
import logging
import os
import sys
import time
from typing import Any, Optional, Set, Dict
import urllib.parse
from dotenv import load_dotenv

if __name__ == "__main__":
    # Evita la doble importación cuando los handlers hacen `import main`.
    sys.modules.setdefault("main", sys.modules[__name__])

load_dotenv()

# ==========================================
# 🧾 LOGGING Y SALIDA SIN BUFFER (RAILWAY / PYTHONUNBUFFERED=1)
# ==========================================
# Aunque PYTHONUNBUFFERED=1 no esté definido, se fuerza el vaciado por línea
# para que los logs aparezcan en tiempo real en la consola de Railway.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(line_buffering=True)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bunker.main")

# ==========================================
# 🌐 IMPORTACIONES Y COMPATIBILIDAD CON FASTAPI & WEBSOCKETS
# ==========================================
try:
    from fastapi import (  # type: ignore[import-not-found]
        Body, FastAPI, Header, HTTPException, APIRouter,
        WebSocket, WebSocketDisconnect, Query
    )
    from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
except ImportError:
    class _FastAPIStub:
        def __init__(self, *args, **kwargs): pass
        def add_middleware(self, *args, **kwargs): pass
        def include_router(self, *args, **kwargs): pass
        def on_event(self, *args, **kwargs): return lambda f: f
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def websocket(self, *args, **kwargs): return lambda f: f
        def exception_handler(self, *args, **kwargs): return lambda f: f

    class _APIRouterStub:
        def __init__(self, *args, **kwargs): pass
        def add_api_route(self, *args, **kwargs): pass
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def websocket(self, *args, **kwargs): return lambda f: f

    class _HTTPExceptionStub(Exception):
        def __init__(self, status_code=None, detail=None, *args, **kwargs):
            super().__init__(detail or status_code)
            self.status_code = status_code
            self.detail = detail

    class WebSocket:  # type: ignore[no-redef]
        async def accept(self): pass
        async def send_json(self, data): pass
        async def receive_text(self): return ""
        async def close(self, code=1000): pass

    class WebSocketDisconnect(Exception): pass  # type: ignore[no-redef]

    class _CORSMiddlewareStub:
        def __init__(self, *args, **kwargs): pass

    FastAPI = _FastAPIStub  # type: ignore[misc]
    APIRouter = _APIRouterStub  # type: ignore[misc]
    Header = lambda *args, **kwargs: None  # type: ignore[assignment]
    Body = lambda *args, **kwargs: None  # type: ignore[assignment]
    Query = lambda *args, **kwargs: None  # type: ignore[assignment]
    HTTPException = _HTTPExceptionStub  # type: ignore[misc]
    CORSMiddleware = _CORSMiddlewareStub  # type: ignore[misc]

try:
    import uvicorn  # type: ignore[import-not-found]
except ImportError:
    uvicorn = None

if uvicorn is not None:
    class _EmbeddedUvicornServer(uvicorn.Server):  # type: ignore[misc, name-defined]
        """
        Servidor uvicorn embebido en el mismo event loop que Aiogram.

        Se desactiva la captura de señales de uvicorn porque Aiogram ya gestiona
        SIGINT/SIGTERM (Railway envía SIGTERM al redeploy). Si ambos instalan
        manejadores, uno pisa al otro y el apagado queda a medias. El cierre del
        servidor se hace explícitamente con `should_exit = True` desde main().
        """

        def install_signal_handlers(self) -> None:  # uvicorn < 0.29
            return None

        @contextlib.contextmanager
        def capture_signals(self):  # uvicorn >= 0.29
            yield
else:
    _EmbeddedUvicornServer = None  # type: ignore[assignment, misc]

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import CallbackQuery, ChatMemberUpdated, ErrorEvent, Message, Update

from assistant import (
    close_all_sentinels,
    execute_ghost_purge,
    register_or_update_sentinel,
    start_voice_radar
)
from database.database import (
    get_all_active_clone_tokens,
    get_channel_plans,
    get_chat_admin_stats,
    get_chat_dashboard_data,
    get_chat_timeseries_stats,
    get_chat_top_users,
    get_db_connection,
    get_group_tier,
    get_or_create_user,
    get_user_by_web_session,
    get_user_channels,
    get_user_global_stats,
    get_user_groups,
    get_user_subscribers_audit,
    init_db,
    mark_payment_processed,
    record_chat_activity,
    register_bot_clone,
    register_user_group,
    revoke_owner_session,
    save_owner_session,
    update_chat_operational_settings,
    update_ghost_purge_scan_time,
    get_community_live_telemetry,
    get_chat_heatmap_matrix,
    get_top_reputation,
    get_user_reputation,
    export_group_configuration,
    import_group_configuration
)
from handlers import (
    admin_group,
    ecosystem,
    groups,
    moderation,
    payments,
    user_private,
    vc_manager
)
from handlers.user_private import (
    get_active_user_channels,
    get_active_user_groups,
    revoke_bot_clone_db,
    send_official_welcome,
    set_master_bot_id,
    set_master_bot_username
)
from middlewares.anti_spam import AntiSpamMiddleware

# ==========================================
# ⚙️ CONFIGURACIÓN Y VARIABLES GLOBALES
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_GROUP_ID_RAW = os.getenv("ADMIN_GROUP_ID", "-1004351489258").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://thebunkerapp2.netlify.app/").strip()

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN no está definido en las variables de entorno.")

try:
    ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_RAW)
except ValueError:
    ADMIN_GROUP_ID = -1004351489258

CREATOR_FALLBACK_ID = 8269470905
active_clone_tasks: dict[str, dict] = {}
dp = Dispatcher()
master_bot_instance: Optional[Bot] = None
_uvicorn_server = None

# ⚠️ Solo para desarrollo local: si se activa, las peticiones sin firma válida se
# atribuyen al creador (comportamiento heredado). En producción DEBE quedar en 0,
# porque cualquiera podría operar la API con privilegios de super-admin.
ALLOW_INSECURE_AUTH_FALLBACK = os.getenv("ALLOW_INSECURE_AUTH_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])


def is_super_admin(user_id: int) -> bool:
    try:
        return int(user_id) in SUPER_ADMIN_IDS
    except (TypeError, ValueError):
        return False


_BG_TASKS: Set[asyncio.Task] = set()


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("❌ [Tarea en segundo plano] %s falló: %r", task.get_name(), exc, exc_info=exc)


def _spawn(coro, name: Optional[str] = None) -> asyncio.Task:
    """Ejecuta corrutinas en segundo plano reteniendo referencia fuerte para evitar recolección por GC."""
    task = asyncio.create_task(coro, name=name)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    task.add_done_callback(_log_task_exception)
    return task


# ==========================================
# ⚡ GESTOR DE CONEXIONES WEBSOCKET (FASE 3)
# ==========================================
class ConnectionManager:
    """Administra conexiones reactivas WebSocket por chat_id para telemetría en tiempo real."""
    SEND_TIMEOUT_SECONDS = 5.0

    def __init__(self):
        self.active_connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def _safe_send(self, chat_id: int, ws: WebSocket, message: dict) -> None:
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=self.SEND_TIMEOUT_SECONDS)
        except Exception:
            await self.disconnect(chat_id, ws)

    async def connect(self, chat_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            if chat_id not in self.active_connections:
                self.active_connections[chat_id] = set()
            self.active_connections[chat_id].add(websocket)

    async def disconnect(self, chat_id: int, websocket: WebSocket):
        async with self._lock:
            if chat_id in self.active_connections:
                self.active_connections[chat_id].discard(websocket)
                if not self.active_connections[chat_id]:
                    self.active_connections.pop(chat_id, None)

    async def broadcast(self, chat_id: int, message: dict):
        async with self._lock:
            connections = list(self.active_connections.get(chat_id, []))
        if connections:
            await asyncio.gather(*(self._safe_send(chat_id, ws, message) for ws in connections))

    async def broadcast_global(self, message: dict):
        async with self._lock:
            all_connections = [
                (cid, ws)
                for cid, conns in self.active_connections.items()
                for ws in list(conns)
            ]
        if all_connections:
            await asyncio.gather(*(self._safe_send(cid, ws, message) for cid, ws in all_connections))

    def total_connections(self) -> int:
        return sum(len(conns) for conns in self.active_connections.values())


ws_manager = ConnectionManager()


async def emit_radar_event(chat_id: int, event_type: str, data: dict = None):
    """Emite un evento reactivo en milisegundos a todos los clientes conectados a la sala."""
    payload = {
        "event": event_type,
        "chat_id": str(chat_id),
        "timestamp": int(time.time()),
        "data": data or {}
    }
    try:
        await ws_manager.broadcast(int(chat_id), payload)
    except Exception as ex:
        logger.debug("Aviso emitiendo evento WS (%s): %s", chat_id, ex)


# ==========================================
# 🌐 SERVIDOR WEB API (FASTAPI)
# ==========================================
app = FastAPI(title="The Bunker OS Backend API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter()


@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "service": "The Bunker Command OS API",
        "bot_connected": master_bot_instance is not None,
        "active_clones": len(active_clone_tasks),
        "websocket_clients": ws_manager.total_connections()
    }


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
            logger.warning("⚠️ [initData] Firma inválida rechazada.")
            return 0

        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return 0

        user_json = json.loads(parsed.get("user", "{}") or "{}")
        return int(user_json.get("id", 0))
    except Exception as e:
        logger.debug(f"Error parseando initData: {e}")
        return 0


SESSION_SECRET = hashlib.sha256(f"bunker-web-session::{BOT_TOKEN}".encode()).digest()
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 días


def issue_session_token(user_id: int, first_name: str = "", username: str = "", photo_url: str = "") -> str:
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


def resolve_user_id(x_telegram_init_data: str = None, authorization: str = None) -> int:
    """
    Resuelve el user_id SOLO a partir de credenciales firmadas (initData HMAC o
    token de sesión firmado). Devuelve 0 si no hay credenciales válidas.
    Con ALLOW_INSECURE_AUTH_FALLBACK=1 se restaura el comportamiento heredado
    (id sin firmar / creador por defecto) para desarrollo local.
    """
    if x_telegram_init_data:
        uid = parse_telegram_user_id(x_telegram_init_data)
        if uid:
            return uid

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            try:
                return int(payload["uid"])
            except (TypeError, ValueError):
                pass

    if ALLOW_INSECURE_AUTH_FALLBACK:
        if x_telegram_init_data:
            try:
                parsed = dict(urllib.parse.parse_qsl(x_telegram_init_data, keep_blank_values=True))
                user_json = json.loads(parsed.get("user", "{}") or "{}")
                raw_id = int(user_json.get("id", 0))
                if raw_id:
                    return raw_id
            except Exception:
                pass
        return CREATOR_FALLBACK_ID

    return 0


def require_authenticated_user(x_telegram_init_data: str = None, authorization: str = None) -> int:
    uid = resolve_user_id(x_telegram_init_data, authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Autenticación requerida: initData o sesión inválida.")
    return uid


async def assert_chat_ownership(user_id: int, chat_id: int):
    if not user_id:
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    if is_super_admin(user_id) or user_id == CREATOR_FALLBACK_ID:
        return
    owned_channels = await get_user_channels(user_id)
    owned_groups = await get_user_groups(user_id)
    owned_ids = {int(c[0]) for c in owned_channels} | {int(g[0]) for g in owned_groups}
    if chat_id not in owned_ids:
        raise HTTPException(status_code=403, detail="No tienes permisos de administración sobre este chat.")


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


# ==========================================
# 📡 RUTAS DE LA API FASTAPI
# ==========================================
@api_router.post("/auth/telegram-widget")
async def api_auth_telegram_widget(payload: dict = Body(...)):
    if not verify_telegram_widget_login(payload):
        raise HTTPException(status_code=401, detail="Firma de autenticación inválida.")

    try:
        user_id = int(payload.get("id", 0))
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        raise HTTPException(status_code=400, detail="ID de usuario ausente.")

    first_name = payload.get("first_name", "") or ""
    username = payload.get("username", "") or ""
    photo_url = payload.get("photo_url", "") or ""

    try:
        await get_or_create_user(user_id, username or "Sin username", first_name or "Operador")
    except Exception as e:
        logger.warning(f"⚠️ [Auth Widget] Aviso BD: {e}")

    token = issue_session_token(user_id, first_name, username, photo_url)
    return {
        "status": "success",
        "session_token": token,
        "user": {"id": user_id, "first_name": first_name, "username": username, "photo_url": photo_url}
    }


@api_router.post("/auth/exchange-token")
async def api_exchange_web_token(payload: dict = Body(...)):
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


@api_router.get("/auth/session-check")
async def api_auth_session_check(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta encabezado Authorization.")
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


@api_router.get("/stats")
async def api_stats(
    context: str = "global",
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        return await get_user_global_stats(user_id)
    except Exception as e:
        logger.error(f"❌ [API Stats Error]: {e}")
        return {
            "subscribers": 0, "revenue_stars": 0, "verified": 0, "expelled": 0, "purges": 0,
            "perimeter": {
                "captcha": "Activo 🟢", "autolower": "2% Activo 🟢", "shield": "Blindado 🟢", "broadcast": "Worker Activo 🟢",
                "captcha_active": True, "autolower_active": True, "shield_active": True, "linklock_active": False
            }
        }


@api_router.get("/channels")
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

        # La vista global solo se expone a super-admins (antes se filtraba a cualquiera).
        if not channels and is_super_admin(user_id):
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
        logger.error(f"❌ [API Channels Error]: {e}")
        return {"channels": []}


@api_router.get("/groups")
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

        if not groups_list and is_super_admin(user_id):
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
        logger.error(f"❌ [API Groups Error]: {e}")
        return {"groups": []}


@api_router.post("/sync-chats")
async def api_sync_chats(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    # Solo los super-admins quedan vinculados al grupo de administración maestro.
    if is_super_admin(user_id):
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
        except Exception:
            channels = await get_user_channels(user_id)
            groups_list = await get_user_groups(user_id)
            synced_channels = len(channels)
            synced_groups = len(groups_list)
    else:
        channels = await get_user_channels(user_id)
        groups_list = await get_user_groups(user_id)
        synced_channels = len(channels)
        synced_groups = len(groups_list)

    if synced_groups == 0 and is_super_admin(user_id):
        synced_groups = await asyncio.to_thread(_get_groups_count_sync)

    return {
        "status": "success",
        "total_channels": synced_channels,
        "total_groups": max(1, synced_groups)
    }


@api_router.get("/subscribers")
async def api_subscribers(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        subs = await get_user_subscribers_audit(user_id)
        return {"subscribers": subs if subs is not None else []}
    except Exception as e:
        logger.error(f"❌ [API Subscribers Error]: {e}")
        return {"subscribers": []}


@api_router.get("/chat/{chat_id}/dashboard")
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
                "switches": {"captcha": True, "autolower": True, "shield": True, "linklock": False},
                "modules_errors": [],
                "footer_metrics": {}
            }

        try:
            plans = await get_channel_plans(numeric_id, only_active=True)
            if plans:
                first_plan = plans[0]
                data["duration_days"] = first_plan[2]
                data["stars_price"] = first_plan[3]
                data["target_link"] = first_plan[9] or ""
            else:
                data.setdefault("duration_days", 30)
                data.setdefault("stars_price", 150)
                data.setdefault("target_link", "")
        except Exception:
            data.setdefault("duration_days", 30)
            data.setdefault("stars_price", 150)
            data.setdefault("target_link", "")

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
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/chat/{chat_id}/stats")
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
        return stats if isinstance(stats, dict) else {"months": [], "mau": [], "messages": [], "messages_per_user": []}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        return {"months": [], "mau": [], "messages": [], "messages_per_user": []}


@api_router.get("/chat/{chat_id}/admin-stats")
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
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/chat/{chat_id}/top-users")
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
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        return {"top_users": []}


# ==========================================
# 📊 FASE 1 / 4: MAPAS DE CALOR Y GAMIFICACIÓN (REST)
# ==========================================
@api_router.get("/chat/{chat_id}/heatmap")
async def api_chat_heatmap(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        return await get_chat_heatmap_matrix(numeric_id)
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/chat/{chat_id}/reputation/top")
async def api_chat_top_reputation(
    chat_id: str,
    limit: int = 10,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        safe_limit = max(1, min(100, int(limit or 10)))
        return {"top": await get_top_reputation(numeric_id, limit=safe_limit)}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/chat/{chat_id}/reputation/{target_user_id}")
async def api_chat_user_reputation(
    chat_id: str,
    target_user_id: int,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        return await get_user_reputation(numeric_id, target_user_id)
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 🔐 FASE 1 / 5: BACKUP & RESTORE CRIPTOGRÁFICO (REST)
# ==========================================
@api_router.get("/chat/{chat_id}/backup/export")
async def api_export_backup(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        package_str = await export_group_configuration(numeric_id)
        return json.loads(package_str)
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/chat/{chat_id}/backup/import")
async def api_import_backup(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_ownership(user_id, numeric_id)
        backup_json = json.dumps(payload)
        success, msg = await import_group_configuration(numeric_id, backup_json)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"status": "success", "message": msg}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/chat/{chat_id}/settings")
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
        action = payload.get("action")

        # 1. Despliegue de Bot Clon
        if action == "deploy_clone":
            bot_token = (payload.get("bot_token") or "").strip()
            if not bot_token:
                raise HTTPException(status_code=400, detail="Token de bot no proporcionado.")
            if bot_token == BOT_TOKEN:
                raise HTTPException(status_code=400, detail="No puedes usar el token del bot maestro como clon.")
            test_bot = None
            try:
                test_bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                bot_info = await test_bot.get_me()
            except Exception as ex:
                raise HTTPException(status_code=400, detail=f"Token inválido o bot inaccesible: {ex}")
            finally:
                if test_bot is not None:
                    with contextlib.suppress(Exception):
                        await test_bot.session.close()

            await register_bot_clone(user_id, numeric_id, bot_token, bot_info.username or "")
            await start_clone_polling_task(bot_token)
            return {
                "status": "success",
                "action": "deploy_clone",
                "clone_username": bot_info.username,
                "chat_id": chat_id
            }

        # 2. Conexión de Centinela MTProto
        elif action == "connect_sentinel":
            session_str = (payload.get("session_string") or "").strip()
            if not session_str:
                raise HTTPException(status_code=400, detail="String Session no proporcionada.")

            await save_owner_session(user_id, numeric_id, session_str)
            connected = await register_or_update_sentinel(user_id, numeric_id, session_str)
            if not connected:
                # Evita que una sesión rota quede marcada como activa y se reintente en cada arranque.
                with contextlib.suppress(Exception):
                    await revoke_owner_session(user_id, numeric_id, reason="Conexión MTProto fallida al registrar")
                raise HTTPException(status_code=400, detail="No se pudo conectar la sesión MTProto. Verifica que la sesión sea válida.")
            return {
                "status": "success",
                "action": "connect_sentinel",
                "chat_id": chat_id
            }

        # 3. Ghost Purge
        elif action == "run_ghost_purge":
            _spawn(execute_ghost_purge(numeric_id, action="ban"), name=f"ghost_purge:{numeric_id}")
            return {
                "status": "success",
                "action": "run_ghost_purge",
                "chat_id": chat_id,
                "message": "Ghost Purge iniciada en segundo plano."
            }

        # 4. Actualización general
        await update_chat_operational_settings(numeric_id, payload or {})

        # Notificar en vivo a los WebSockets de la sala
        _spawn(emit_radar_event(numeric_id, "settings_updated", payload or {}))

        return {"status": "success", "chat_id": chat_id, "updated": payload}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        logger.error(f"❌ [Settings API Error]: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/affiliates/me")
async def api_affiliates(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    return {"invited_communities": 0, "earned_stars": 0, "balance": 0}


# ==========================================
# ⚡ WEBSOCKET DE TELEMETRÍA REACTIVA EN VIVO (FASE 3)
# ==========================================
@app.websocket("/ws/live-radar/{chat_id}")
@app.websocket("/api/ws/live-radar/{chat_id}")
async def websocket_live_radar(
    websocket: WebSocket,
    chat_id: str,
    token: str = Query(None),
    init_data: str = Query(None)
):
    """Canal bidireccional reactivo en tiempo real para Mini App y Dashboard."""
    try:
        numeric_id = int(chat_id)
    except ValueError:
        await websocket.close(code=1003)
        return

    # 1. Autenticación de la sesión WebSocket (token de sesión firmado o initData firmado)
    user_id = 0
    user_payload = verify_session_token(token) if token else None
    if user_payload and user_payload.get("uid"):
        try:
            user_id = int(user_payload["uid"])
        except (TypeError, ValueError):
            user_id = 0
    if not user_id:
        user_id = resolve_user_id(
            x_telegram_init_data=init_data or (token if token and "hash=" in token else None),
            authorization=f"Bearer {token}" if token else None
        )

    if not user_id:
        await websocket.close(code=1008)
        return

    if not is_super_admin(user_id) and user_id != CREATOR_FALLBACK_ID:
        try:
            await assert_chat_ownership(user_id, numeric_id)
        except Exception:
            await websocket.close(code=1008)
            return

    await ws_manager.connect(numeric_id, websocket)

    # 2. Despacho inmediato del snapshot de estado al conectar
    try:
        snapshot = await get_community_live_telemetry(numeric_id)
        await websocket.send_json({
            "event": "initial_state",
            "chat_id": str(numeric_id),
            "timestamp": int(time.time()),
            "data": snapshot
        })
    except Exception as e:
        logger.debug(f"Aviso enviando snapshot inicial WS ({numeric_id}): {e}")

    # 3. Bucle de escucha reactivo con soporte de heartbeat (ping / pong)
    try:
        while True:
            client_msg = await websocket.receive_text()
            if client_msg == "ping":
                await websocket.send_json({"event": "pong", "timestamp": int(time.time())})
            elif client_msg.startswith("{"):
                try:
                    parsed_req = json.loads(client_msg)
                    if parsed_req.get("action") == "refresh":
                        snapshot = await get_community_live_telemetry(numeric_id)
                        await websocket.send_json({
                            "event": "state_refresh",
                            "chat_id": str(numeric_id),
                            "timestamp": int(time.time()),
                            "data": snapshot
                        })
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as ws_err:
        logger.debug(f"Aviso en conexión WebSocket ({numeric_id}): {ws_err}")
    finally:
        await ws_manager.disconnect(numeric_id, websocket)


app.include_router(api_router, prefix="/api")
app.include_router(api_router)


def _resolve_port() -> int:
    raw_port = os.getenv("PORT", "8080").strip()
    try:
        port = int(raw_port)
        if 0 < port < 65536:
            return port
    except ValueError:
        pass
    logger.warning(f"⚠️ [FastAPI] PORT inválido ({raw_port!r}); se usa 8080.")
    return 8080


def _websocket_backend_available() -> bool:
    for module_name in ("websockets", "wsproto"):
        try:
            __import__(module_name)
            return True
        except ImportError:
            continue
    return False


async def run_fastapi_server():
    global _uvicorn_server
    if uvicorn is None or _EmbeddedUvicornServer is None:
        logger.warning("uvicorn no está instalado; el servidor FastAPI no iniciará.")
        return
    if not _websocket_backend_available():
        logger.warning(
            "⚠️ [FastAPI] No hay librería WebSocket instalada (websockets/wsproto). "
            "Instala 'uvicorn[standard]' o 'websockets' para activar /ws/live-radar."
        )
    port = _resolve_port()
    config = uvicorn.Config(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=port,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info").lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        timeout_keep_alive=30,
    )
    server = _EmbeddedUvicornServer(config)
    _uvicorn_server = server
    logger.info(f"🌐 [API Backend & WebSockets]: Iniciando servidor en 0.0.0.0:{port}.")
    try:
        await server.serve()
    except asyncio.CancelledError:
        raise
    except SystemExit as ex:
        # uvicorn llama a sys.exit(1) si no puede enlazar el puerto; sin este bloque
        # el SystemExit escaparía del event loop y tumbaría también al bot.
        logger.critical(f"❌ [FastAPI] El servidor no pudo arrancar (código {ex.code}). Revisa el puerto {port}.")
    except Exception as ex:
        logger.error(f"⚠️ [FastAPI Server Error]: {ex}", exc_info=True)


async def stop_fastapi_server(timeout: float = 10.0):
    server = _uvicorn_server
    if server is None:
        return
    server.should_exit = True
    deadline = time.monotonic() + timeout
    while getattr(server, "started", False) and not getattr(server, "force_exit", False):
        if time.monotonic() >= deadline:
            server.force_exit = True
            break
        await asyncio.sleep(0.1)


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
            logger.error(f"❌ [Fallback Rescue] Error: {ex}", exc_info=True)

    user_id = callback.from_user.id if callback.from_user else 0
    logger.warning(f"🧭 [Callback sin handler] usuario={user_id} callback_data={callback.data!r}")

    lang_code = callback.from_user.language_code if callback.from_user else ""
    is_es = bool(lang_code and lang_code.startswith("es"))
    text = "⚠️ Este botón ya no está activo. Envía /start." if is_es else "⚠️ This button is no longer active. Send /start."

    try:
        await callback.answer(text, show_alert=True)
    except Exception:
        pass


@dp.errors()
async def on_dispatcher_error(event: ErrorEvent) -> bool:
    logger.error(f"❌ [Error Dispatcher]: {event.exception!r}", exc_info=event.exception)
    if event.update and event.update.callback_query:
        try:
            await event.update.callback_query.answer("⚠️ Error temporal", show_alert=False)
        except Exception:
            pass
    return True


async def on_bot_promoted_or_added(event: ChatMemberUpdated, bot: Bot = None):
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
            logger.info(f"🎯 [Auto-Detección]: '{chat_title}' ({event.chat.id}) vinculado.")
    except Exception as e:
        logger.error(f"❌ [Error en my_chat_member]: {e}", exc_info=True)


class ChatAutoDetectMiddleware(BaseMiddleware):
    """
    Registra el chat cuando el bot es añadido/promovido y DESPUÉS deja pasar el
    evento a los routers. Como handler directo del Dispatcher se ejecutaba antes
    que cualquier router y consumía el evento, de modo que los handlers
    my_chat_member de groups/user_private nunca se disparaban.
    """

    async def __call__(self, handler, event: ChatMemberUpdated, data: dict):
        await on_bot_promoted_or_added(event, data.get("bot"))
        return await handler(event, data)


async def _dispatch_clone_update(clone_bot: Bot, bot_username: str, update: Update):
    try:
        is_private_start = False
        if update.message:
            # La actividad de grupo la registra ActivityTrackerMiddleware (outer middleware
            # del Dispatcher), que también se ejecuta en dp.feed_update; registrarla aquí
            # duplicaba los contadores de los clones.
            if update.message.chat.type == "private":
                text = (update.message.text or "").strip()
                is_private_start = text.startswith("/start")

        result = await dp.feed_update(clone_bot, update)

        if result is UNHANDLED and is_private_start and update.message:
            user = update.message.from_user
            try:
                if user:
                    await get_or_create_user(user.id, user.username or "Sin username", user.full_name)
            except Exception:
                pass
            await send_official_welcome(clone_bot, update.message.chat.id, user, bot_username)
    except Exception as feed_err:
        logger.error(f"❌ [Error clon @{bot_username}]: {feed_err}", exc_info=True)


CLONE_MAX_CONCURRENT_UPDATES = 16


async def _dispatch_clone_update_limited(semaphore: asyncio.Semaphore, clone_bot: Bot, bot_username: str, update: Update):
    async with semaphore:
        await _dispatch_clone_update(clone_bot, bot_username, update)


async def _clone_worker(clone_bot: Bot, token: str):
    allowed_updates = ["message", "callback_query", "pre_checkout_query", "chat_join_request", "chat_member", "my_chat_member"]
    token_tag = token.split(":", 1)[0]
    try:
        # 1. Arranque con reintentos (fallos de red transitorios no deben matar el clon)
        backoff = 2.0
        while True:
            try:
                await clone_bot.delete_webhook(drop_pending_updates=True)
                bot_info = await clone_bot.get_me()
                bot_username = bot_info.username or "BotClon"
                break
            except TelegramUnauthorizedError:
                logger.warning(f"🔒 [Clon {token_tag}] Token revocado o inválido; se detiene el clon.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(f"⚠️ [Clon {token_tag}] Error al iniciar ({ex}); reintento en {backoff:.0f}s.")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        logger.info(f"🤖 [Clon @{bot_username}] Polling activo.")

        # 2. Bucle de long-polling; cada update se procesa como tarea (igual que Aiogram)
        semaphore = asyncio.Semaphore(CLONE_MAX_CONCURRENT_UPDATES)
        offset = None
        error_backoff = 2.0
        while token in active_clone_tasks:
            try:
                updates = await clone_bot.get_updates(offset=offset, timeout=15, allowed_updates=allowed_updates)
                error_backoff = 2.0
                for update in updates:
                    offset = update.update_id + 1
                    _spawn(
                        _dispatch_clone_update_limited(semaphore, clone_bot, bot_username, update),
                        name=f"clone_update:{token_tag}:{update.update_id}"
                    )
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                logger.warning(f"🔒 [Clon @{bot_username}] Token revocado durante el polling; se detiene.")
                return
            except Exception as ex:
                logger.debug(f"Aviso polling clon @{bot_username}: {ex}")
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, 30.0)
    finally:
        current = active_clone_tasks.get(token)
        if current is not None and current.get("task") is asyncio.current_task():
            active_clone_tasks.pop(token, None)
        with contextlib.suppress(Exception):
            await clone_bot.session.close()


async def start_clone_polling_task(token: str):
    token = (token or "").strip()
    if not token or token in active_clone_tasks:
        return
    if token == BOT_TOKEN:
        logger.warning("⚠️ [Clones] Se ignoró un clon con el token del bot maestro (provocaría conflicto de polling).")
        return
    try:
        clone_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        entry: dict = {"bot": clone_bot, "task": None}
        active_clone_tasks[token] = entry
        entry["task"] = asyncio.create_task(_clone_worker(clone_bot, token), name=f"clone_worker:{token.split(':', 1)[0]}")
    except Exception as e:
        active_clone_tasks.pop(token, None)
        logger.error(f"⚠️ [Error Clon {token[:10]}]: {e}")


async def stop_clone_polling_task(token: str):
    task_data = active_clone_tasks.pop(token, None)
    if not task_data:
        return
    task = task_data.get("task")
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=10)
    with contextlib.suppress(Exception):
        await task_data["bot"].session.close()


def trigger_dynamic_clone(token: str):
    _spawn(start_clone_polling_task(token), name="clone_start")


def trigger_disconnect_clone(token: str):
    _spawn(stop_clone_polling_task(token), name="clone_stop")


# ==========================================
# 📊 MIDDLEWARE ROBUSTO DE ACTIVIDAD DE CHAT
# ==========================================
class ActivityTrackerMiddleware(BaseMiddleware):
    """
    Registra la actividad de chat sin bloquear el pipeline de handlers.
    El estado de administrador se cachea (antes se hacía una llamada
    getChatMember a Telegram por CADA mensaje, con riesgo de flood-wait).
    """
    ADMIN_CACHE_TTL_SECONDS = 300.0
    ADMIN_CACHE_MAX_ENTRIES = 20000

    def __init__(self):
        super().__init__()
        self._admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}

    async def _is_admin(self, event: Message) -> bool:
        key = (event.chat.id, event.from_user.id)
        now = time.monotonic()
        cached = self._admin_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        is_admin = False
        try:
            member = await event.chat.get_member(event.from_user.id)
            is_admin = member.status in ("creator", "administrator")
        except Exception:
            pass
        if len(self._admin_cache) >= self.ADMIN_CACHE_MAX_ENTRIES:
            expired = [k for k, (_, exp) in self._admin_cache.items() if exp <= now]
            for k in expired:
                self._admin_cache.pop(k, None)
            if len(self._admin_cache) >= self.ADMIN_CACHE_MAX_ENTRIES:
                self._admin_cache.clear()
        self._admin_cache[key] = (is_admin, now + self.ADMIN_CACHE_TTL_SECONDS)
        return is_admin

    async def _track(self, event: Message) -> None:
        try:
            is_admin = await self._is_admin(event)
            await record_chat_activity(
                group_id=event.chat.id,
                user_id=event.from_user.id,
                full_name=event.from_user.full_name or "Usuario",
                username=event.from_user.username or "",
                is_reply=bool(event.reply_to_message),
                is_admin=is_admin
            )
        except Exception as ex:
            logger.debug(f"Aviso registrando actividad ({event.chat.id}): {ex}")

    async def __call__(self, handler, event: Message, data: dict):
        if (
            isinstance(event, Message)
            and event.chat
            and event.chat.type in ("group", "supergroup")
            and event.from_user
            and not event.from_user.is_bot
        ):
            _spawn(self._track(event), name="activity_tracker")
        return await handler(event, data)


# ==========================================
# 🚀 FUNCIÓN PRINCIPAL DE ARRANQUE (MAIN)
# ==========================================
async def main():
    global master_bot_instance

    # 0. Base de datos primero: todo lo demás depende del esquema.
    init_db()
    logger.info("🛡️ [Base de Datos]: Inicializada correctamente.")

    if ALLOW_INSECURE_AUTH_FALLBACK:
        logger.warning("🚨 [Seguridad] ALLOW_INSECURE_AUTH_FALLBACK=1: la API acepta peticiones sin firma. NO usar en producción.")

    # 1. API + WebSockets en el mismo event loop (Railway enruta el tráfico a $PORT)
    fastapi_task = _spawn(run_fastapi_server(), name="fastapi_server")

    master_bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    master_bot_instance = master_bot
    set_master_bot_id(master_bot.id)
    try:
        master_info = await master_bot.get_me()
        set_master_bot_username(master_info.username or "")
        logger.info(f"🤖 [Identidad Maestro]: Online como @{master_info.username} (ID: {master_info.id})")
    except TelegramUnauthorizedError:
        logger.critical("❌ [Identidad Maestro]: BOT_TOKEN rechazado por Telegram. Revisa la variable en Railway.")
        await stop_fastapi_server()
        with contextlib.suppress(Exception):
            await master_bot.session.close()
        raise
    except Exception as e:
        logger.warning(f"⚠️ [Identidad Maestro]: {e}")

    # Registro formal de middlewares
    dp.message.middleware(AntiSpamMiddleware())
    dp.message.outer_middleware.register(ActivityTrackerMiddleware())
    dp.my_chat_member.outer_middleware.register(ChatAutoDetectMiddleware())

    # 🎯 ORDEN ESTRICTO DE ROUTERS:
    # 1. payments: captura facturas Stars, pre_checkouts y deep-links (/start tip_, sub_, vipmic_)
    dp.include_router(payments.router)
    # 2. user_private: consolas privadas, Sentinel Settings, sincronización y creador de planes
    dp.include_router(user_private.router)
    # 3. moderation y admin_group: comandos ejecutivos y /reload antes de procesar el chat general
    dp.include_router(moderation.router)
    dp.include_router(admin_group.router)
    # 4. ecosystem y vc_manager: telemetría y directivas de videollamada
    dp.include_router(ecosystem.router)
    dp.include_router(vc_manager.router)
    # 5. groups: aduana captcha, anti-flood, gamificación y matriz perimetral
    dp.include_router(groups.router)
    # 6. fallback_router: salvaguarda de callbacks huérfanos
    dp.include_router(fallback_router)

    # Inicialización del Radar Acústico MTProto y Centinelas dedicados
    try:
        radar_result = start_voice_radar(master_bot)
        if asyncio.iscoroutine(radar_result):
            _spawn(radar_result, name="voice_radar")
    except Exception as e:
        logger.warning(f"⚠️ [Radar MTProto]: {e}")

    # Inicialización del worker de difusión recurrente de planes en canales
    if hasattr(ecosystem, "start_channel_broadcast_worker"):
        try:
            _spawn(ecosystem.start_channel_broadcast_worker(master_bot), name="channel_broadcast_worker")
        except Exception as e:
            logger.warning(f"⚠️ [Broadcast Worker]: {e}")

    # Carga concurrente de bots clones persistidos en SQLite
    try:
        stored_clones = await get_all_active_clone_tokens()
        for clone_token in stored_clones:
            _spawn(start_clone_polling_task(clone_token), name="clone_boot")
        logger.info(f"🧬 [Clones BD]: {len(stored_clones)} clon(es) en cola de arranque.")
    except Exception as e:
        logger.warning(f"⚠️ [Clones BD]: {e}")

    try:
        for _att in range(3):
            try:
                await master_bot.delete_webhook(drop_pending_updates=True)
                break
            except Exception as w_err:
                logger.warning(f"Reintento de delete_webhook ({_att + 1}/3): {w_err}")
                await asyncio.sleep(2)

        allowed_updates = dp.resolve_used_update_types()
        required_updates = [
            "message", "callback_query", "pre_checkout_query",
            "chat_join_request", "chat_member", "my_chat_member"
        ]
        for update_type in required_updates:
            if update_type not in allowed_updates:
                allowed_updates.append(update_type)

        logger.info("🚀 [Polling Maestro]: Iniciando recepción de updates.")
        await dp.start_polling(master_bot, allowed_updates=allowed_updates)
    finally:
        logger.info("🛑 [Apagado]: Liberando recursos...")
        # 1. Cerrar la API para no aceptar más tráfico
        try:
            await stop_fastapi_server()
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(fastapi_task, timeout=10)
        except Exception:
            pass
        # 2. Cancelar tareas en segundo plano (incluidas las de arranque de clones,
        #    para que ninguna cree un clon nuevo después de este punto)
        pending = [t for t in list(_BG_TASKS) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            with contextlib.suppress(Exception):
                await asyncio.wait(pending, timeout=5)
        # 3. Detener clones y Centinelas MTProto
        for token in list(active_clone_tasks.keys()):
            try:
                await stop_clone_polling_task(token)
            except Exception:
                pass
        try:
            await close_all_sentinels()
        except Exception:
            pass
        # 4. Cerrar la sesión HTTP del bot maestro
        try:
            await master_bot.session.close()
        except Exception:
            pass
        master_bot_instance = None
        logger.info("✅ [Apagado]: Completado.")


# ==========================================
# ▶️ PUNTO DE ENTRADA (python main.py)
# ==========================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("👋 [The Bunker OS]: Proceso detenido.")