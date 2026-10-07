"""
main.py — The Bunker OS (Aiogram 3.x / FastAPI / Pyrogram)

Núcleo de arranque maestro, sincronización de enrutadores, pasarela Web API y
administración concurrente de clones y Centinelas acústicos.
Fase 3: Telemetría Reactiva en Vivo mediante WebSockets (FastAPI) + Endpoints de Heatmaps, Reputación y Backups.
Fase 5: Analítica en Caliente nivel Combot (GET /api/community/{chat_id}/analytics + /ws/radar/{chat_id} blindado).
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
import re
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
    from fastapi.responses import JSONResponse  # type: ignore[import-not-found]
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
    JSONResponse = lambda content=None, status_code=200, headers=None, **kwargs: content  # type: ignore[assignment, misc]

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
    get_chat_full_configuration,
    update_chat_full_configuration,
    ChatConfigError,
    update_ghost_purge_scan_time,
    get_community_live_telemetry,
    get_chat_heatmap_matrix,
    get_top_reputation,
    get_user_reputation,
    export_group_configuration,
    import_group_configuration
)
try:
    from database.analytics import (  # type: ignore[import-not-found]
        analytics_buffer,
        classify_message,
        extract_group_id_from_payload,
        get_community_full_analytics,
        record_stars_payment,
        start_analytics_flusher,
        stop_analytics_flusher,
    )
except ImportError:  # pragma: no cover - se usa en entornos minimalistas o con módulos ausentes.
    analytics_buffer = None

    def classify_message(*args, **kwargs):
        return None

    def extract_group_id_from_payload(*args, **kwargs):
        return None

    def get_community_full_analytics(*args, **kwargs):
        return {}

    def record_stars_payment(*args, **kwargs):
        return None

    def start_analytics_flusher(*args, **kwargs):
        return None

    def stop_analytics_flusher(*args, **kwargs):
        return None

try:
    from radar_bus import publish_radar_event, radar_hub  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - módulo opcional disponible solo en algunos entornos.
    def publish_radar_event(*args, **kwargs):
        return None

    radar_hub = None

from handlers import (
    admin_group,
    ecosystem,
    groups,
    moderation,
    payments,
    user_private,
    vc_manager
)
from handlers import metrics as community_metrics
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
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://the-bunker-bot-bunkerminiapp.vercel.app/").strip()

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
# ⚡ GESTOR DE CONEXIONES WEBSOCKET (FASE 3 → FASE 5)
# ==========================================
# El gestor vive en radar_bus.py para que groups/assistant/ecosystem publiquen sin
# importar main.py. `ws_manager` conserva la API anterior (connect, disconnect,
# broadcast, broadcast_global, total_connections, active_connections).
ws_manager = radar_hub


async def emit_radar_event(chat_id: int, event_type: str, data: dict = None):
    """Emite un evento reactivo a todos los clientes de la sala (encolado, no bloqueante)."""
    try:
        publish_radar_event(int(chat_id), event_type, data or {})
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


_ADMIN_ACCESS_CACHE: Dict[tuple, tuple] = {}
_ADMIN_ACCESS_CACHE_MAX = 20000


async def _live_member_status(user_id: int, chat_id: int, fresh: bool = False) -> str:
    """
    Estado real del usuario en el chat según Telegram ('creator', 'administrator', 'member', 'left', …).
    Caché 5 min si es staff, 1 min si no, 30 s ante error. fresh=True ignora la caché (sincronización
    pedida explícitamente por el operador). '' si no se pudo consultar.
    """
    key = (int(chat_id), int(user_id))
    now = time.monotonic()
    cached = _ADMIN_ACCESS_CACHE.get(key)
    if cached and cached[1] > now and not fresh:
        return cached[0]
    if master_bot_instance is None:
        return ""
    try:
        member = await asyncio.wait_for(master_bot_instance.get_chat_member(chat_id, user_id), timeout=4)
        status = str(getattr(member.status, "value", member.status) or "")
        ttl = 300.0 if status in ("creator", "administrator") else 60.0
    except Exception:
        status, ttl = "", 30.0
    if len(_ADMIN_ACCESS_CACHE) >= _ADMIN_ACCESS_CACHE_MAX:
        _ADMIN_ACCESS_CACHE.clear()
    _ADMIN_ACCESS_CACHE[key] = (status, now + ttl)
    return status


async def _is_live_chat_admin(user_id: int, chat_id: int, fresh: bool = False) -> bool:
    """Verifica en Telegram si el usuario es creador/administrador del chat."""
    return await _live_member_status(user_id, chat_id, fresh=fresh) in ("creator", "administrator")


async def assert_chat_access(user_id: int, chat_id: int):
    """
    Acceso a la analítica: propietario registrado (assert_chat_ownership) o cualquier
    administrador vivo del chat. Así el botón de /metrics funciona para todo el staff,
    no solo para quien añadió el bot.
    """
    try:
        await assert_chat_ownership(user_id, chat_id)
        return
    except HTTPException as exc:
        if getattr(exc, "status_code", None) != 403:
            raise
    if await _is_live_chat_admin(user_id, chat_id):
        return
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


def _get_registered_chats_sync():
    """Todos los chats que el bot conoce (uno por group_id), con su nombre y tipo registrados."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT group_id, MAX(group_name), MAX(chat_type) FROM user_groups GROUP BY group_id"
        ).fetchall()


# ==========================================
# 🔄 v8.2 · SINCRONIZACIÓN REAL DE CANALES Y GRUPOS DEL OPERADOR
# ==========================================
# El registro inicial solo vincula a quien añadió/promovió al bot. Si fue otro administrador, el
# creador real no aparecía en los selectores. La auditoría consulta a Telegram (get_chat_member)
# el estado del operador en cada chat que el bot conoce y vincula los que administra.
SYNC_LINK_ROLES = {r.strip() for r in os.getenv("SYNC_LINK_ROLES", "creator,administrator").split(",") if r.strip()}
SYNC_AUDIT_MAX_CHATS = max(1, int(os.getenv("SYNC_AUDIT_MAX_CHATS", "400") or 400))
SYNC_AUDIT_CONCURRENCY = max(1, int(os.getenv("SYNC_AUDIT_CONCURRENCY", "8") or 8))
SYNC_AUDIT_COOLDOWN_S = float(os.getenv("SYNC_AUDIT_COOLDOWN_S", "60") or 60)
SYNC_AUTOHEAL_INTERVAL_S = 600.0
_SYNC_AUDIT_LAST: Dict[int, float] = {}
_SYNC_AUDIT_LOCKS: Dict[int, asyncio.Lock] = {}


def _norm_chat_type(raw: Any, fallback: str = "supergroup") -> str:
    value = str(getattr(raw, "value", raw) or "").lower()
    return value if value in ("channel", "group", "supergroup") else fallback


async def audit_user_chat_links(user_id: int, force: bool = False) -> Dict[str, int]:
    """
    Recorre los chats registrados que el operador aún no tiene vinculados y, si Telegram confirma que
    es creador/administrador (SYNC_LINK_ROLES), lo vincula en user_groups con register_user_group().
    Concurrencia acotada (SYNC_AUDIT_CONCURRENCY) para respetar los límites de la Bot API, tope de
    SYNC_AUDIT_MAX_CHATS por pasada y enfriamiento por usuario (SYNC_AUDIT_COOLDOWN_S) salvo force.
    """
    uid = int(user_id)
    result = {"audited": 0, "linked": 0, "skipped": 0}
    if master_bot_instance is None:
        return result
    lock = _SYNC_AUDIT_LOCKS.setdefault(uid, asyncio.Lock())
    if lock.locked():
        async with lock:          # una auditoría en curso ya cubre esta petición
            return result
    async with lock:
        now = time.monotonic()
        last = _SYNC_AUDIT_LAST.get(uid)
        if last is not None and now - last < (SYNC_AUDIT_COOLDOWN_S if force else SYNC_AUTOHEAL_INTERVAL_S):
            result["skipped"] = 1
            return result
        _SYNC_AUDIT_LAST[uid] = now

        try:
            registered = await asyncio.to_thread(_get_registered_chats_sync)
            linked = {int(c[0]) for c in (await get_user_channels(uid) or [])} | {int(g[0]) for g in (await get_user_groups(uid) or [])}
        except Exception as ex:
            logger.warning(f"⚠️ [Sync] No se pudo leer el registro de chats: {ex}")
            return result

        pending = [row for row in registered if row and row[0] is not None and int(row[0]) not in linked][:SYNC_AUDIT_MAX_CHATS]
        semaphore = asyncio.Semaphore(SYNC_AUDIT_CONCURRENCY)

        async def check(row) -> None:
            chat_id = int(row[0])
            async with semaphore:
                status = await _live_member_status(uid, chat_id, fresh=True)
            result["audited"] += 1
            if status not in SYNC_LINK_ROLES:
                return
            try:
                await register_user_group(
                    user_id=uid,
                    group_id=chat_id,
                    group_name=row[1] or f"Chat {chat_id}",
                    chat_type=_norm_chat_type(row[2], "channel" if str(row[2]) == "channel" else "supergroup"),
                )
                result["linked"] += 1
                logger.info(f"🔗 [Sync] Usuario {uid} vinculado a {chat_id} ({status}).")
            except Exception as ex:
                logger.warning(f"⚠️ [Sync] No se pudo vincular {chat_id} a {uid}: {ex}")

        await asyncio.gather(*(check(row) for row in pending))
        return result


async def _owned_chat_rows(user_id: int, kind: str) -> list:
    """Filas (id, nombre) del operador para kind ∈ {'channel','group'}; vista global solo para super-admins."""
    getter_active = get_active_user_channels if kind == "channel" else get_active_user_groups
    getter = get_user_channels if kind == "channel" else get_user_groups
    rows: list = []
    if master_bot_instance:
        try:
            rows = await getter_active(master_bot_instance, user_id)
        except Exception:
            rows = await getter(user_id)
    else:
        rows = await getter(user_id)
    if not rows and is_super_admin(user_id):
        rows = await asyncio.to_thread(_get_global_channels_sync if kind == "channel" else _get_global_groups_sync)
    return list(rows or [])


async def _build_chat_entries(rows: list, kind: str) -> list:
    """Entradas limpias para los selectores: id, título, miembros y tipo reales (consultas en paralelo)."""
    semaphore = asyncio.Semaphore(SYNC_AUDIT_CONCURRENCY)
    seen: set = set()

    async def entry(row):
        chat_id = int(row[0])
        if chat_id in seen:
            return None
        seen.add(chat_id)
        title = row[1] if len(row) > 1 and row[1] else f"Chat {chat_id}"
        chat_type = "channel" if kind == "channel" else "supergroup"
        members = 0
        if master_bot_instance:
            async with semaphore:
                try:
                    chat_obj = await asyncio.wait_for(master_bot_instance.get_chat(chat_id), timeout=5)
                    title = chat_obj.title or title
                    chat_type = _norm_chat_type(chat_obj.type, chat_type)
                    members = await asyncio.wait_for(master_bot_instance.get_chat_member_count(chat_id), timeout=5)
                except Exception:
                    pass
        try:
            tier = await get_group_tier(chat_id)
        except Exception:
            tier = "free"
        try:
            timeseries = await get_chat_timeseries_stats(chat_id)
            curve = list((timeseries or {}).get("messages", []))[-7:]
        except Exception:
            curve = []
        if len(curve) < 7:
            curve = [0] * (7 - len(curve)) + curve
        return {
            "id": str(chat_id),
            "title": str(title),
            "type": chat_type,
            "license_status": "active" if tier != "free" else "expired",
            "members": int(members or 0),
            "activity": curve,
            "joined": 0,
            "left": 0,
            "avatar_url": None,
        }

    entries = [e for e in await asyncio.gather(*(entry(r) for r in rows if r and r[0] is not None)) if e]
    # Un chat registrado con el tipo equivocado se muestra donde corresponde según Telegram.
    if kind == "channel":
        entries = [e for e in entries if e["type"] == "channel"]
    else:
        entries = [e for e in entries if e["type"] != "channel"]
    entries.sort(key=lambda e: e["title"].lower())
    return entries


async def _list_operator_chats(user_id: int, kind: str) -> list:
    """Listado del operador; si está vacío, autocura con una auditoría (máx. una cada 10 min)."""
    rows = await _owned_chat_rows(user_id, kind)
    if not rows:
        audit = await audit_user_chat_links(user_id, force=False)
        if audit.get("linked"):
            rows = await _owned_chat_rows(user_id, kind)
    return await _build_chat_entries(rows, kind)


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
        return {"channels": await _list_operator_chats(user_id, "channel")}
    except Exception as e:
        logger.error(f"❌ [API Channels Error]: {e}", exc_info=True)
        return {"channels": []}


@api_router.get("/groups")
async def api_groups(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        return {"groups": await _list_operator_chats(user_id, "group")}
    except Exception as e:
        logger.error(f"❌ [API Groups Error]: {e}", exc_info=True)
        return {"groups": []}


@api_router.post("/sync-chats")
async def api_sync_chats(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """
    Sincronización explícita: audita en Telegram los chats registrados, vincula los que el operador
    administra y devuelve los listados ya limpios (la Mini App puebla los selectores sin otra llamada).
    """
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

    audit = await audit_user_chat_links(user_id, force=True)
    channels = await _build_chat_entries(await _owned_chat_rows(user_id, "channel"), "channel")
    groups = await _build_chat_entries(await _owned_chat_rows(user_id, "group"), "group")
    return {
        "status": "success",
        "total_channels": len(channels),
        "total_groups": len(groups),
        "audited": audit.get("audited", 0),
        "newly_linked": audit.get("linked", 0),
        "throttled": bool(audit.get("skipped")),
        "channels": channels,
        "groups": groups,
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

        # v8.2: difusión personalizada del Estudio (chat destino, intervalo, texto promocional).
        if load_broadcast_config is not None:
            try:
                data.update(public_broadcast_config(await load_broadcast_config(numeric_id, _plan_deps())))
            except Exception as ex:
                logger.debug(f"Sin configuración de difusión para {numeric_id}: {ex}")

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


# v8.2: servicio de difusión personalizada (vive en channel_plans_api.py junto a los planes de canal).
try:
    from channel_plans_api import (  # type: ignore[import-not-found]
        PlanApiError,
        load_broadcast_config,
        load_db_deps as _load_plan_deps,
        public_broadcast_config,
        run_custom_broadcast_scheduler,
        save_broadcast_config,
    )
except ImportError:
    class PlanApiError(Exception):  # type: ignore[no-redef]
        status, detail = 500, ""
    load_broadcast_config = save_broadcast_config = run_custom_broadcast_scheduler = None  # type: ignore[assignment]
    public_broadcast_config = _load_plan_deps = None  # type: ignore[assignment]

_PLAN_DEPS_CACHE: Dict[str, Any] = {}


def _plan_deps():
    if "deps" not in _PLAN_DEPS_CACHE:
        _PLAN_DEPS_CACHE["deps"] = _load_plan_deps()
    return _PLAN_DEPS_CACHE["deps"]


_LOG_CHANNEL_ALIAS_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
_LOG_CHANNEL_ID_RE = re.compile(r"^-?\d{5,20}$")


async def _validate_log_channel(user_id: int, chat_id: int, raw: Any) -> str:
    """
    Normaliza y verifica el canal de auditorías antes de guardarlo:
      • '' → desactiva el registro.
      • @alias o ID numérico → se resuelve en Telegram; el operador debe ser creador/administrador del
        canal de registro y el bot debe poder publicar en él (si no, los logs se perderían en silencio).
    Devuelve el ID numérico como texto.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if not (_LOG_CHANNEL_ALIAS_RE.match(value) or _LOG_CHANNEL_ID_RE.match(value)):
        raise HTTPException(status_code=422, detail="log_channel_id: usa un @alias público o el ID numérico (-100…) del canal.")
    if master_bot_instance is None:
        raise HTTPException(status_code=503, detail="log_channel_id: el bot no está disponible para verificar el canal.")
    lookup: Any = int(value) if value.lstrip("-").isdigit() else value
    try:
        target = await asyncio.wait_for(master_bot_instance.get_chat(lookup), timeout=6)
    except Exception:
        raise HTTPException(status_code=422, detail="log_channel_id: no se encontró el canal. Añade el bot como administrador y revisa el ID/@alias.")
    target_id = int(target.id)
    if target_id == int(chat_id):
        raise HTTPException(status_code=422, detail="log_channel_id: el canal de registro debe ser distinto de la comunidad auditada.")
    if not (is_super_admin(user_id) or await _is_live_chat_admin(user_id, target_id, fresh=True)):
        raise HTTPException(status_code=403, detail="log_channel_id: debes ser creador o administrador del canal de registro.")
    try:
        me = await master_bot_instance.get_me()
        member = await asyncio.wait_for(master_bot_instance.get_chat_member(target_id, me.id), timeout=6)
        status = str(getattr(member.status, "value", member.status) or "")
        kind = _norm_chat_type(target.type, "supergroup")
        can_post = status == "creator" or (
            status == "administrator" and (kind != "channel" or bool(getattr(member, "can_post_messages", False)))
        ) or (kind != "channel" and status == "member")
    except Exception:
        can_post = False
    if not can_post:
        raise HTTPException(status_code=422, detail="log_channel_id: el bot debe ser administrador con permiso para publicar en el canal de registro.")
    return str(target_id)


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

        if action:
            raise HTTPException(status_code=400, detail=f"Acción desconocida: {action}")

        payload = dict(payload or {})
        response_extra: Dict[str, Any] = {}

        # 4. Canal de registro (auditorías): validado contra Telegram antes de guardarse.
        if "log_channel_id" in payload:
            payload["log_channel_id"] = await _validate_log_channel(user_id, numeric_id, payload.get("log_channel_id"))
            response_extra["log_channel_id"] = payload["log_channel_id"]

        # 5. Difusión personalizada del Estudio: se valida y guarda ANTES que el resto (si falla, nada cambia).
        broadcast_keys = ("broadcast_target", "broadcast_interval", "promo_text")
        if any(k in payload for k in broadcast_keys):
            if save_broadcast_config is None:
                raise HTTPException(status_code=503, detail="Difusión personalizada no disponible en este despliegue.")
            broadcast_fields = {k: payload.pop(k) for k in broadcast_keys if k in payload}
            try:
                response_extra["broadcast"] = await save_broadcast_config(
                    numeric_id, user_id, broadcast_fields, _plan_deps(), master_bot_instance, _is_live_chat_admin)
            except PlanApiError as err:
                raise HTTPException(status_code=err.status, detail=err.detail)

        # 6. Actualización general (interruptores, Estudio: enlace VIP, tarifa, duración, …)
        if payload:
            await update_chat_operational_settings(numeric_id, payload)

        # Notificar en vivo a los WebSockets de la sala
        _spawn(emit_radar_event(numeric_id, "settings_updated", payload))

        return {"status": "success", "chat_id": chat_id, "updated": payload, **response_extra}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    except Exception as e:
        logger.error(f"❌ [Settings API Error]: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 🎛️ v8.3 · CONFIGURACIÓN OPERATIVA COMPLETA DE LA COMUNIDAD
# ==========================================
_CONFIG_MAX_BODY_FIELDS = 64


@api_router.get("/chat/{chat_id}/configuration")
async def api_get_chat_configuration(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """Árbol completo de parámetros que rigen al bot en la comunidad (Aduana, Acústica, Propinas, Perímetro)."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero.")
    await assert_chat_ownership(user_id, numeric_id)
    configuration = await get_chat_full_configuration(numeric_id)
    return {"status": "success", "chat_id": str(numeric_id), "configuration": configuration}


@api_router.post("/chat/{chat_id}/configuration")
async def api_update_chat_configuration(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """
    Valida y persiste en lote (BEGIN IMMEDIATE, todo o nada). Acepta {"configuration": {...}} o el árbol
    directamente, completo o parcial. Responde 422 con "campo: motivo" ante el primer error y, si guarda,
    emite 'settings_updated' a la sala del radar con el árbol resultante.
    """
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero.")
    await assert_chat_ownership(user_id, numeric_id)

    body = payload.get("configuration", payload) if isinstance(payload, dict) else payload
    if isinstance(body, dict) and sum(len(v) if isinstance(v, dict) else 1 for v in body.values()) > _CONFIG_MAX_BODY_FIELDS:
        raise HTTPException(status_code=413, detail="configuration: demasiados campos.")
    try:
        configuration = await update_chat_full_configuration(numeric_id, body, updated_by=user_id)
    except ChatConfigError as err:
        raise HTTPException(status_code=422, detail=str(err))
    except Exception as e:
        logger.error(f"❌ [Configuration API Error] chat={numeric_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="No se pudo guardar la configuración.")

    changed = sorted({field
                      for key, value in body.items()
                      for field in (value.keys() if isinstance(value, dict) else [key])})
    _spawn(emit_radar_event(numeric_id, "settings_updated", {"source": "configuration", "changed": changed,
                                                             "configuration": configuration}))
    logger.info(f"🎛️ [Configuración] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(changed)}")
    return {"status": "success", "chat_id": str(numeric_id), "configuration": configuration}


@api_router.get("/affiliates/me")
async def api_affiliates(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    return {"invited_communities": 0, "earned_stars": 0, "balance": 0}


# ==========================================
# 📈 FASE 5: ANALÍTICA COMPLETA DE COMUNIDAD (REST)
# ==========================================
ANALYTICS_API_TIMEOUT = float(os.getenv("ANALYTICS_API_TIMEOUT", "2.5") or 2.5)
LIVE_MEMBERS_TTL = 300.0
_LIVE_MEMBER_CACHE: Dict[int, tuple] = {}
_LIVE_MEMBER_REFRESHING: Set[int] = set()


async def _refresh_live_member_count(chat_id: int) -> None:
    try:
        if master_bot_instance is None:
            return
        try:
            count = int(await asyncio.wait_for(master_bot_instance.get_chat_member_count(chat_id), timeout=5))
        except Exception:
            previous = _LIVE_MEMBER_CACHE.get(chat_id)
            count = previous[1] if previous else None
        _LIVE_MEMBER_CACHE[chat_id] = (time.monotonic(), count)
    finally:
        _LIVE_MEMBER_REFRESHING.discard(chat_id)


def _cached_live_member_count(chat_id: int) -> Optional[int]:
    """Nunca espera a Telegram: devuelve el último valor y refresca en segundo plano si caducó."""
    entry = _LIVE_MEMBER_CACHE.get(chat_id)
    stale = entry is None or (time.monotonic() - entry[0]) > LIVE_MEMBERS_TTL
    if stale and chat_id not in _LIVE_MEMBER_REFRESHING and master_bot_instance is not None:
        _LIVE_MEMBER_REFRESHING.add(chat_id)
        _spawn(_refresh_live_member_count(chat_id), name=f"live_members:{chat_id}")
    return entry[1] if entry else None


def _decorate_analytics(chat_id: int, data: dict) -> dict:
    """Copia superficial con datos en vivo (no se muta el objeto cacheado)."""
    payload = dict(data)
    summary = dict(payload.get("summary") or {})
    summary["members_live"] = _cached_live_member_count(chat_id)
    payload["summary"] = summary
    payload["live"] = {
        "ws_path": f"/ws/radar/{chat_id}",
        "listeners": radar_hub.listener_count(chat_id),
        "seq": radar_hub.current_seq(chat_id),
    }
    return payload


@api_router.get("/community/{chat_id}/analytics")
async def api_community_full_analytics(
    chat_id: str,
    fresh: bool = False,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """
    Analítica completa en un único JSON: resumen, crecimiento/retención, mapa de
    calor 7x24 (listo para ApexCharts y Chart.js), cuadro de honor y desglose por
    tipo de mensaje. Caché por chat de pocos segundos + cómputo compartido.
    """
    started = time.perf_counter()
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    await assert_chat_access(user_id, numeric_id)

    try:
        data = await asyncio.wait_for(
            get_community_full_analytics(numeric_id, max_age=0.0 if fresh else None),
            timeout=ANALYTICS_API_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="La compilación analítica excedió el tiempo límite.")
    except Exception as e:
        logger.error(f"❌ [API Analytics Error] {numeric_id}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Analítica temporalmente no disponible.")

    payload = _decorate_analytics(numeric_id, data)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "no-store",
            "Server-Timing": f"app;dur={elapsed_ms:.1f}",
            "X-Bunker-Signature": "Cloud Media Management",
        },
    )


# ==========================================
# ⚡ WEBSOCKET DE TELEMETRÍA REACTIVA EN VIVO (FASE 3 → FASE 5, BLINDADO)
# ==========================================
WS_AUTH_TIMEOUT = float(os.getenv("WS_AUTH_TIMEOUT", "10") or 10)
WS_IDLE_TIMEOUT = float(os.getenv("WS_IDLE_TIMEOUT", "90") or 90)
WS_MAX_INBOUND_BYTES = 4096
WS_RATE_CAPACITY = 20.0
WS_RATE_REFILL_PER_SEC = 2.0
WS_RATE_MAX_VIOLATIONS = 10
WS_REFRESH_COOLDOWN = 2.0
WS_ALLOWED_ORIGINS = {
    o.strip().rstrip("/") for o in os.getenv("WS_ALLOWED_ORIGINS", "").split(",") if o.strip()
}
RADAR_EVENTS = {
    "message", "level_up", "voice_call_started", "voice_call_ended", "voice_presence",
    "stars_payment", "settings_updated",
}

# Códigos de cierre de aplicación (4000-4999) legibles por la Mini App.
WS_CLOSE_BAD_REQUEST = 4400
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_TIMEOUT = 4408
WS_CLOSE_RATE_LIMIT = 4429


def _ws_user_from_credentials(token: Optional[str], init_data: Optional[str]) -> int:
    """Solo credenciales firmadas: token de sesión HMAC o initData de Telegram."""
    if token:
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            try:
                return int(payload["uid"])
            except (TypeError, ValueError):
                pass
        if "hash=" in token:
            uid = parse_telegram_user_id(token)
            if uid:
                return uid
    if init_data:
        uid = parse_telegram_user_id(init_data)
        if uid:
            return uid
    if ALLOW_INSECURE_AUTH_FALLBACK and (token or init_data):
        return resolve_user_id(init_data, f"Bearer {token}" if token else None)
    return 0


async def _ws_receive_text(websocket: WebSocket, timeout: float) -> Optional[str]:
    """Recibe un frame (texto o binario UTF-8). None = el cliente se desconectó."""
    message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    if message.get("type") == "websocket.disconnect":
        return None
    if message.get("text") is not None:
        return message["text"]
    raw = message.get("bytes")
    if raw is not None:
        return raw.decode("utf-8", errors="replace")
    return ""


async def _ws_reject(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json({"event": "error", "code": code, "reason": reason, "timestamp": int(time.time())})
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


async def _ws_close_registered(client, websocket: WebSocket, code: int, reason: str) -> None:
    """Cierre de un cliente ya registrado: el aviso viaja por su cola (sin envíos concurrentes)."""
    radar_hub.send_to(client, {"event": "error", "code": code, "reason": reason, "timestamp": int(time.time())})
    await asyncio.sleep(0.05)
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


def _ws_envelope(chat_id: int, event: str, data: Any) -> dict:
    return {
        "v": 2,
        "event": event,
        "chat_id": str(chat_id),
        "seq": radar_hub.current_seq(chat_id),
        "timestamp": int(time.time()),
        "data": data,
    }


async def _ws_send_snapshots(client, chat_id: int, include_legacy: bool = True, legacy_event: str = "initial_state") -> None:
    """Snapshot de telemetría heredada + analítica completa, encolados en el escritor del cliente."""
    async def _legacy():
        try:
            return await asyncio.wait_for(get_community_live_telemetry(chat_id), timeout=3)
        except Exception as e:
            logger.debug(f"Aviso snapshot telemetría WS ({chat_id}): {e}")
            return None

    async def _analytics():
        try:
            data = await asyncio.wait_for(get_community_full_analytics(chat_id), timeout=ANALYTICS_API_TIMEOUT)
            return _decorate_analytics(chat_id, data)
        except Exception as e:
            logger.debug(f"Aviso snapshot analítico WS ({chat_id}): {e}")
            return None

    legacy, analytics = await asyncio.gather(_legacy() if include_legacy else asyncio.sleep(0), _analytics())
    if include_legacy and legacy is not None:
        radar_hub.send_to(client, _ws_envelope(chat_id, legacy_event, legacy))
    if analytics is not None:
        radar_hub.send_to(client, _ws_envelope(chat_id, "analytics_snapshot", analytics))


@app.websocket("/ws/radar/{chat_id}")
@app.websocket("/api/ws/radar/{chat_id}")
@app.websocket("/ws/live-radar/{chat_id}")
@app.websocket("/api/ws/live-radar/{chat_id}")
async def websocket_radar(
    websocket: WebSocket,
    chat_id: str,
    token: str = Query(None),
    init_data: str = Query(None)
):
    """
    Canal bidireccional en caliente para la Mini App y el Dashboard.

    Autenticación (una de dos):
      a) ?token=<session_token> o ?init_data=<initData urlencoded>
      b) sin query: primer frame {"action":"auth","init_data":"..."} o {"action":"auth","token":"..."}
         en menos de WS_AUTH_TIMEOUT segundos (evita credenciales en logs de URL).
    Acciones del cliente: "ping" | {"action":"ping"} | {"action":"refresh"} |
      {"action":"analytics"} | {"action":"subscribe","events":[...]}.
    Cierres: 4400 petición inválida · 4401 sin autenticar · 4403 sin permisos ·
      4408 inactividad/timeout · 4429 límite de conexiones o de frecuencia.
    """
    await websocket.accept()

    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if WS_ALLOWED_ORIGINS and origin not in WS_ALLOWED_ORIGINS:
        await _ws_reject(websocket, WS_CLOSE_FORBIDDEN, "origin_not_allowed")
        return

    try:
        numeric_id = int(chat_id)
    except ValueError:
        await _ws_reject(websocket, WS_CLOSE_BAD_REQUEST, "invalid_chat_id")
        return

    # 1. Autenticación
    user_id = _ws_user_from_credentials(token, init_data)
    if not user_id:
        try:
            first = await _ws_receive_text(websocket, WS_AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            await _ws_reject(websocket, WS_CLOSE_TIMEOUT, "auth_timeout")
            return
        except Exception:
            return
        if first is None:
            return
        if first and len(first) <= 16384 and first.lstrip().startswith("{"):
            try:
                auth_msg = json.loads(first)
            except ValueError:
                auth_msg = {}
            if isinstance(auth_msg, dict) and auth_msg.get("action") == "auth":
                user_id = _ws_user_from_credentials(auth_msg.get("token"), auth_msg.get("init_data"))
    if not user_id:
        await _ws_reject(websocket, WS_CLOSE_UNAUTHORIZED, "unauthorized")
        return

    # 2. Autorización sobre la sala
    try:
        await assert_chat_access(user_id, numeric_id)
    except HTTPException:
        await _ws_reject(websocket, WS_CLOSE_FORBIDDEN, "forbidden")
        return
    except Exception as e:
        logger.debug(f"Aviso verificando acceso WS ({numeric_id}): {e}")
        await _ws_reject(websocket, WS_CLOSE_FORBIDDEN, "forbidden")
        return

    reject_reason = radar_hub.can_accept(numeric_id, user_id)
    if reject_reason:
        await _ws_reject(websocket, WS_CLOSE_RATE_LIMIT, reject_reason)
        return

    client = await radar_hub.connect(numeric_id, websocket, user_id=user_id, accept=False)

    # 3. Saludo + snapshots iniciales (en segundo plano: el lector arranca ya)
    radar_hub.send_to(client, _ws_envelope(numeric_id, "hello", {
        "user_id": user_id,
        "events": sorted(RADAR_EVENTS),
        "idle_timeout": WS_IDLE_TIMEOUT,
        "signature": "Cloud Media Management",
    }))
    snapshot_task = _spawn(_ws_send_snapshots(client, numeric_id), name=f"ws_snapshot:{numeric_id}")

    # 4. Bucle lector con límite de tamaño, token bucket e inactividad
    tokens = WS_RATE_CAPACITY
    last_refill = time.monotonic()
    violations = 0
    last_refresh = 0.0
    try:
        while not client.closed:
            try:
                raw = await _ws_receive_text(websocket, WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                await _ws_close_registered(client, websocket, WS_CLOSE_TIMEOUT, "idle_timeout")
                break
            if raw is None:
                break

            now_mono = time.monotonic()
            tokens = min(WS_RATE_CAPACITY, tokens + (now_mono - last_refill) * WS_RATE_REFILL_PER_SEC)
            last_refill = now_mono
            if tokens < 1.0:
                violations += 1
                if violations >= WS_RATE_MAX_VIOLATIONS:
                    await _ws_close_registered(client, websocket, WS_CLOSE_RATE_LIMIT, "rate_limited")
                    break
                continue
            tokens -= 1.0

            if len(raw) > WS_MAX_INBOUND_BYTES:
                await _ws_close_registered(client, websocket, 1009, "frame_too_large")
                break

            text = raw.strip()
            if text == "ping":
                radar_hub.send_to(client, {"event": "pong", "timestamp": int(time.time())})
                continue
            if not text.startswith("{"):
                continue
            try:
                request = json.loads(text)
            except ValueError:
                radar_hub.send_to(client, {"event": "error", "reason": "invalid_json", "timestamp": int(time.time())})
                continue
            if not isinstance(request, dict):
                continue

            action = str(request.get("action") or "")
            if action == "ping":
                radar_hub.send_to(client, {"event": "pong", "timestamp": int(time.time())})
            elif action in ("refresh", "analytics"):
                if now_mono - last_refresh < WS_REFRESH_COOLDOWN:
                    continue
                last_refresh = now_mono
                _spawn(
                    _ws_send_snapshots(client, numeric_id, include_legacy=(action == "refresh"), legacy_event="state_refresh"),
                    name=f"ws_refresh:{numeric_id}"
                )
            elif action == "subscribe":
                events = request.get("events")
                if isinstance(events, list):
                    wanted = {str(e) for e in events[:32]} & RADAR_EVENTS
                    radar_hub.set_filter(client, wanted or None)
                    radar_hub.send_to(client, _ws_envelope(numeric_id, "subscribed", {"events": sorted(wanted) or sorted(RADAR_EVENTS)}))
            elif action == "auth":
                continue
            else:
                radar_hub.send_to(client, {"event": "error", "reason": "unknown_action", "timestamp": int(time.time())})
    except WebSocketDisconnect:
        pass
    except Exception as ws_err:
        logger.debug(f"Aviso en conexión WebSocket ({numeric_id}): {ws_err}")
    finally:
        if not snapshot_task.done():
            snapshot_task.cancel()
        await radar_hub.disconnect(numeric_id, websocket)

# 1. Importa la función constructora del router de planes de canal
# Si el módulo no existe en este despliegue, se omite de forma segura.
try:
    from channel_plans_api import (
        build_channel_plans_router,
        run_custom_broadcast_scheduler,
        save_broadcast_config,
    )
except ImportError:
    build_channel_plans_router = None
    run_custom_broadcast_scheduler = None
    save_broadcast_config = None
    logger.warning("[FastAPI] No se encontró 'channel_plans_api'; se omite el router de planes de canal.")

# 2. Inclúyelo en tu api_router existente
if build_channel_plans_router is not None:
    api_router.include_router(build_channel_plans_router(
        require_user=require_authenticated_user,
        assert_owner=assert_chat_ownership,
        get_bot=lambda: master_bot_instance,
    ))


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
            "Instala 'uvicorn[standard]' o 'websockets' para activar /ws/radar y /ws/live-radar."
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

            # v8.2: si quien añadió el bot no es el creador, el creador real también queda vinculado
            # (antes no veía su propio chat en los selectores de la Mini App).
            if bot is not None:
                with contextlib.suppress(Exception):
                    admins = await asyncio.wait_for(bot.get_chat_administrators(event.chat.id), timeout=6)
                    for adm in admins:
                        adm_status = str(getattr(adm.status, "value", adm.status) or "")
                        if adm_status == "creator" and adm.user and not adm.user.is_bot and adm.user.id != promoter_id:
                            await register_user_group(
                                user_id=adm.user.id,
                                group_id=event.chat.id,
                                group_name=chat_title,
                                chat_type=chat_type
                            )
                            logger.info(f"👑 [Auto-Detección]: creador {adm.user.id} vinculado a {event.chat.id}.")
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
                await clone_bot.delete_webhook(drop_pending_updates=False)   # ver nota en main(): pagos pendientes
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

    @staticmethod
    def _analytics(event: Message) -> None:
        """Síncrono y sin I/O: suma al búfer analítico y emite el payload compacto al radar."""
        try:
            kind = classify_message(event)
            if kind is None:
                return
            user = event.from_user
            full_name = user.full_name or "Usuario"
            ts = event.date.timestamp() if getattr(event, "date", None) else None
            analytics_buffer.record(event.chat.id, user.id, kind, full_name, user.username or "", ts=ts)
            if radar_hub.has_listeners(event.chat.id):
                publish_radar_event(event.chat.id, "message", {
                    "u": user.id,
                    "n": full_name[:48],
                    "k": kind,
                    "m": event.message_id,
                    "r": 1 if event.reply_to_message else 0,
                })
        except Exception as ex:
            logger.debug(f"Aviso en analítica en caliente ({event.chat.id}): {ex}")

    @staticmethod
    async def _record_payment(event: Message) -> None:
        payment = event.successful_payment
        try:
            if (payment.currency or "").upper() != "XTR":
                return
            if event.chat.type in ("group", "supergroup", "channel"):
                group_id = event.chat.id
            else:
                group_id = extract_group_id_from_payload(payment.invoice_payload)
            if not group_id:
                return
            payer_id = event.from_user.id if event.from_user else 0
            inserted = await record_stars_payment(
                group_id=group_id,
                user_id=payer_id,
                amount=payment.total_amount,
                charge_id=payment.telegram_payment_charge_id,
                payload=payment.invoice_payload or "",
                currency=payment.currency,
            )
            if inserted:
                publish_radar_event(group_id, "stars_payment", {"u": payer_id, "a": int(payment.total_amount)})
        except Exception as ex:
            logger.debug(f"Aviso registrando pago Stars en analítica: {ex}")

    async def __call__(self, handler, event: Message, data: dict):
        if isinstance(event, Message) and event.chat:
            if event.successful_payment:
                _spawn(self._record_payment(event), name="stars_ledger")
            if (
                event.chat.type in ("group", "supergroup")
                and event.from_user
                and not event.from_user.is_bot
            ):
                _spawn(self._track(event), name="activity_tracker")
                self._analytics(event)
        return await handler(event, data)


# ==========================================
# 🚀 FUNCIÓN PRINCIPAL DE ARRANQUE (MAIN)
# ==========================================
# ==========================================
# 🔒 FASE 8.1 · INSTANCIA ÚNICA + VERIFICACIÓN DE SQLITE WAL
# ==========================================
# SQLite en un volumen de Railway solo es seguro con UN proceso escritor: dos réplicas (o el solape
# de un redeploy) compiten por el lock del archivo y, además, dos long-polling del mismo token se
# expulsan mutuamente (TelegramConflictError). La caché de planes y los asyncio.Lock de licencias
# también asumen un único proceso. Este candado lo hace cumplir en tiempo de ejecución, no solo
# en la configuración de Railway.
_INSTANCE_LOCK_HANDLE = None


def _instance_lock_path() -> str:
    explicit = os.getenv("INSTANCE_LOCK_PATH", "").strip()
    if explicit:
        return explicit
    base = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if not base:
        import database.database as _db  # type: ignore[import-not-found]
        db_file = next((getattr(_db, n) for n in ("DB_PATH", "DB_FILE", "DATABASE_PATH", "DB_NAME")
                        if isinstance(getattr(_db, n, None), str)), "")
        base = os.path.dirname(os.path.abspath(db_file)) if db_file else os.getcwd()
    return os.path.join(base, ".bunker_instance.lock")


async def _enforce_single_instance(timeout_s: float = None) -> None:
    """
    Candado exclusivo (fcntl.flock) junto a la base de datos. Durante un redeploy la instancia nueva
    ESPERA a que la vieja libere el candado (hasta INSTANCE_LOCK_WAIT_S, 90 s por defecto) en vez de
    escribir a la vez; si no lo obtiene, sale con error y Railway la reintenta.
    """
    global _INSTANCE_LOCK_HANDLE
    try:
        import fcntl
    except ImportError:          # Windows / desarrollo local: sin garantía, solo aviso
        logger.warning("⚠️ [Instancia] fcntl no disponible: no se puede garantizar instancia única.")
        return
    if timeout_s is None:
        timeout_s = float(os.getenv("INSTANCE_LOCK_WAIT_S", "90") or 90)
    path = _instance_lock_path()
    handle = open(path, "a+")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise RuntimeError(
                    f"Otra instancia mantiene {path}. The Bunker OS debe correr con UNA sola réplica "
                    f"(SQLite + long polling). Revisa 'Replicas' en Railway."
                )
            logger.warning("⏳ [Instancia] Otra instancia sigue activa (¿redeploy en curso?); esperando el candado…")
            await asyncio.sleep(2)
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} replica={os.getenv('RAILWAY_REPLICA_ID', '-')} since={int(time.time())}\n")
    handle.flush()
    _INSTANCE_LOCK_HANDLE = handle   # se libera solo al terminar el proceso
    logger.info(f"🔒 [Instancia] Candado exclusivo adquirido: {path}")


def _verify_sqlite_wal() -> None:
    """Comprueba (y si falta, activa) WAL y que las conexiones traigan busy_timeout."""
    with get_db_connection() as conn:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if mode != "wal":
            mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            logger.warning(f"⚠️ [SQLite] journal_mode no era WAL; activado ahora → {mode}. Fíjalo en init_db().")
        busy = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        sync = int(conn.execute("PRAGMA synchronous").fetchone()[0])
    if mode != "wal":
        logger.critical(f"❌ [SQLite] No se pudo activar WAL (modo={mode}). ¿El volumen soporta mmap/locks?")
    if busy < 1000:
        logger.critical(f"❌ [SQLite] busy_timeout={busy} ms en get_db_connection(): con escritores concurrentes "
                        f"habrá 'database is locked'. Configura PRAGMA busy_timeout >= 5000 al abrir cada conexión.")
    logger.info(f"🗄️ [SQLite] journal_mode={mode} busy_timeout={busy}ms synchronous={sync}")


async def main():
    global master_bot_instance

    # 0. Base de datos primero: todo lo demás depende del esquema.
    await _enforce_single_instance()
    init_db()
    _verify_sqlite_wal()
    logger.info("🛡️ [Base de Datos]: Inicializada correctamente.")
    start_analytics_flusher()
    logger.info("📈 [Analítica en Caliente]: Búfer de volcado por lotes activo.")

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
    # 1b. community_metrics: /metrics · /stats (grupos) y /metrics (privado) con Dashboard en Vivo
    dp.include_router(community_metrics.router)
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

    # v8.2: difusión personalizada del Estudio (chat destino + intervalo). Desactivable con
    # CUSTOM_BROADCAST_SCHEDULER=0 (p. ej. si otro worker ya publica esas promociones).
    if run_custom_broadcast_scheduler is not None and os.getenv("CUSTOM_BROADCAST_SCHEDULER", "1").strip().lower() not in ("0", "false", "no", "off"):
        _spawn(run_custom_broadcast_scheduler(lambda: master_bot_instance), name="custom_broadcast_scheduler")

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
                # NO descartar pendientes: entre ellos puede haber successful_payment de Stars ya
                # cobrados durante el redeploy; descartarlos = cobro sin entrega.
                await master_bot.delete_webhook(drop_pending_updates=False)
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
        # 1. Cerrar salas WebSocket y la API para no aceptar más tráfico
        with contextlib.suppress(Exception):
            await radar_hub.close_all()
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
        # 2b. Volcado final del búfer analítico (no se pierden los últimos segundos)
        with contextlib.suppress(Exception):
            await stop_analytics_flusher()
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