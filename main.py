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
import importlib
import importlib.util
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, cast
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
    _reconfigure = getattr(_stream, "reconfigure", None)   # TextIOWrapper lo tiene; TextIO genérico (tipado) no
    if callable(_reconfigure):
        with contextlib.suppress(AttributeError, ValueError):
            _reconfigure(line_buffering=True)

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
        def include_router(self, *args, **kwargs): pass
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def websocket(self, *args, **kwargs): return lambda f: f

    class _HTTPExceptionStub(Exception):
        def __init__(self, status_code=None, detail=None, *args, **kwargs):
            super().__init__(detail or status_code)
            self.status_code = status_code
            self.detail = detail

    class WebSocket:  # type: ignore[no-redef]
        headers: Dict[str, str] = {}
        query_params: Dict[str, str] = {}

        async def accept(self): pass
        async def send_json(self, data): pass
        async def send_text(self, data): pass
        async def receive(self) -> Dict[str, Any]: return {"type": "websocket.disconnect"}
        async def receive_text(self): return ""
        async def close(self, code: int = 1000, reason: Optional[str] = None): pass

    class WebSocketDisconnect(Exception): pass  # type: ignore[no-redef]

    class _CORSMiddlewareStub:
        def __init__(self, *args, **kwargs): pass

    FastAPI = _FastAPIStub  # type: ignore[misc]
    APIRouter = _APIRouterStub  # type: ignore[misc]
    # v26 · Stubs tipados como Any (igual que los de FastAPI): con lambdas devolviendo None, Pylance infería cada
    #       `param: str = Header(None)` como "None no asignable a str" en TODAS las rutas.
    def Header(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None

    def Body(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None

    def Query(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None

    def JSONResponse(content: Any = None, status_code: int = 200, headers: Any = None, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return content

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

import assistant as _assistant_runtime   # v17 · lectura del estado EN VIVO de los Centinelas (active_sentinels)
import perimeter_ops                      # v20 · purga v2, canal de registro, IA, estado del perímetro y soporte IA
import micvip_audit                       # v22 · auditoría MicVIP de extremo a extremo (cobro → pase → Centinela)
import vip_roles                          # v23 · roles y etiquetas VIP automáticos post-donación / pase
import link_quotas                        # v23 · cuota diaria de enlaces por administrador
from assistant import (
    close_all_sentinels,
    execute_ghost_purge,
    register_clone_bot_resolver,
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
    import_group_configuration,
    # v9 · Licencia vigente, bloqueos de seguridad, informe del canal de registro y escudo anti-raid
    get_user_global_tier,
    describe_license,
    get_security_block_stats,
    get_log_report_config,
    save_log_report_config,
    record_admin_mod_action,
    # v13 · Matriz del Arquitecto: delegados con acceso de lectura a la analítica
    has_delegate_permission,
    # v14 · Telemetría de ajustes en caliente hacia /ws/radar
    get_settings_snapshot,
    # v15 · Respaldo del Centinela: bot clon activo de la comunidad
    get_active_clone_tokens_for_group,
    # v17 · Supervisión de activos (sin credenciales)
    get_chat_assets_registry,
)
try:
    import raid_shield  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - el escudo es opcional en entornos minimalistas.
    class _RaidShieldFallback:
        @staticmethod
        def status_payload(chat_id: int) -> dict:
            return {}

    raid_shield = _RaidShieldFallback()
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
    # v26 · Los respaldos respetan la naturaleza de las funciones reales: las que main.py usa con `await` son
    #       corrutinas. Antes eran síncronas y, sin database/analytics.py, `await record_stars_payment(...)` o
    #       `await stop_analytics_flusher()` lanzaban TypeError (NoneType can't be used in 'await' expression).
    class _NullAnalyticsBuffer:
        def record(self, *args: Any, **kwargs: Any) -> None:
            return None

    analytics_buffer = _NullAnalyticsBuffer()

    def classify_message(*args, **kwargs):
        return None

    def extract_group_id_from_payload(*args, **kwargs):
        return None

    async def get_community_full_analytics(*args, **kwargs) -> dict:
        return {}

    async def record_stars_payment(*args, **kwargs):
        return None

    def start_analytics_flusher(*args, **kwargs):
        return None

    async def stop_analytics_flusher(*args, **kwargs):
        return None

class _NullRadarHub:
    """
    v26 · Sustituto inerte de radar_bus.radar_hub con los mismos métodos que usa main.py. Solo se activa si radar_bus
    no está desplegado: /health sigue respondiendo 200 y /ws/radar rechaza con un motivo claro ("radar_unavailable")
    en lugar de lanzar AttributeError sobre None. Con radar_bus presente no interviene.
    """

    def total_connections(self) -> int:
        return 0

    def listener_count(self, chat_id: int) -> int:
        return 0

    def current_seq(self, chat_id: int) -> int:
        return 0

    def has_listeners(self, chat_id: int) -> bool:
        return False

    def can_accept(self, chat_id: int, user_id: int) -> Optional[str]:
        return "radar_unavailable"

    async def connect(self, chat_id: int, websocket: Any, user_id: int = 0, accept: bool = False) -> Any:
        return None

    def send_to(self, client: Any, payload: Any) -> None:
        return None

    def set_filter(self, client: Any, events: Any) -> None:
        return None

    async def disconnect(self, chat_id: int, websocket: Any) -> None:
        return None

    async def close_all(self) -> None:
        return None


try:
    from radar_bus import publish_radar_event, radar_hub  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - módulo opcional disponible solo en algunos entornos.
    def publish_radar_event(*args, **kwargs):
        return None

    radar_hub = _NullRadarHub()

from handlers import (
    admin_group,
    architect_matrix,
    ecosystem,
    groups,
    media_locks,
    moderation,
    payments,
    user_private,
    vc_manager
)
from handlers import metrics as community_metrics


def _ban_appeals_module():
    """
    v26 · Módulo de apelaciones: ubicación oficial handlers/ban_appeals.py; se tolera ban_appeals.py en la raíz.
    find_spec decide la ruta ANTES de importar: un ImportError real dentro del módulo nunca queda enmascarado.
    """
    name = "handlers.ban_appeals" if importlib.util.find_spec("handlers.ban_appeals") is not None else "ban_appeals"
    return importlib.import_module(name)


ban_appeals = _ban_appeals_module()   # ⚖️ v24/v25 · apelaciones (registro del bot maestro y barrido)
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


async def emit_radar_event(chat_id: int, event_type: str, data: Optional[dict] = None):
    """Emite un evento reactivo a todos los clientes de la sala (encolado, no bloqueante)."""
    try:
        publish_radar_event(int(chat_id), event_type, data or {})
    except Exception as ex:
        logger.debug("Aviso emitiendo evento WS (%s): %s", chat_id, ex)


# v14 · Campos cuyo cambio debe llegar EN CALIENTE a la Mini App (switches acústicos y del Centinela).
ACOUSTIC_SETTING_KEYS = frozenset({
    "autolower", "autolower_enabled", "autolower_pct", "shield", "shield_enabled",
    "micvip_price", "speaker_price",
})
async def build_settings_snapshot(chat_id: int) -> Dict[str, Any]:
    """
    Estado vigente para repintar la Mini App: árbol de configuración (misma forma que
    GET /chat/{id}/configuration, la que consume ui.renderChatConfiguration) + interruptores del Centinela.
    """
    try:
        return await get_settings_snapshot(int(chat_id))
    except Exception as ex:
        logger.debug("Aviso leyendo el estado para el radar (%s): %s", chat_id, ex)
        return {}


async def emit_settings_updated(chat_id: int, source: str, changed, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    v14 · Emite 'settings_updated' a /ws/radar/{chat_id} con el estado REAL ya guardado. `extra` conserva las
    claves que ya enviaba cada emisor (compatibilidad con clientes que leen el payload plano).
    """
    try:
        event: Dict[str, Any] = dict(extra or {})
        event["source"] = source
        event["changed"] = sorted({str(c) for c in (changed or [])})
        event.update(await build_settings_snapshot(chat_id))
        acoustic = sorted(set(event["changed"]) & ACOUSTIC_SETTING_KEYS)
        if acoustic:
            event["acoustic_changed"] = acoustic
        await emit_radar_event(chat_id, "settings_updated", event)
    except Exception as ex:
        logger.debug("Aviso emitiendo settings_updated (%s): %s", chat_id, ex)


# ==========================================
# 🌐 SERVIDOR WEB API (FASTAPI)
# ==========================================
# ==========================================
# 🚦 v26 · ARRANQUE RÁPIDO PARA EL HEALTHCHECK DE RAILWAY
# ==========================================
# uvicorn se levanta al INICIO de main(), antes de esperar el candado de instancia única: /health responde 200 al
# instante aunque la réplica anterior siga viva durante un redeploy (si la nueva esperaba el candado antes de abrir el
# puerto, ninguna de las dos avanzaba y Railway daba el despliegue por fallido). Hasta que esta instancia tiene el
# candado y la base de datos lista (mark_startup_ready), el resto de la API responde 503 + Retry-After y los WebSockets
# se cierran con 1013 ("try again later"): se conserva la regla de UN solo proceso escritor sobre SQLite.
_STARTUP_READY = False
HEALTH_PATHS = frozenset({"/", "/health", "/api/health"})


def mark_startup_ready() -> None:
    global _STARTUP_READY
    _STARTUP_READY = True


class StartupGateMiddleware:
    """Middleware ASGI puro (HTTP y WebSocket): solo deja pasar el healthcheck hasta que la instancia esté lista."""

    def __init__(self, app_: Any) -> None:
        self.app = app_

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if _STARTUP_READY or kind not in ("http", "websocket") or scope.get("path") in HEALTH_PATHS:
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1013, "reason": "starting"})
            return
        body = json.dumps({"detail": "Servicio iniciando, reintenta en unos segundos."}).encode("utf-8")
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json"), (b"retry-after", b"5"),
                                (b"content-length", str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})


app = FastAPI(title="The Bunker OS Backend API", version="2.0")

# Orden: en Starlette el ÚLTIMO middleware añadido es el más externo. La compuerta se añade ANTES que CORS para que
# CORS la envuelva y también firme las respuestas 503 del arranque (la Mini App ve "iniciando", no un error CORS).
app.add_middleware(StartupGateMiddleware)
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
    """Siempre 200 y sin E/S: Railway lo consulta durante el despliegue (también antes de que el bot esté listo)."""
    try:
        clients = int(ws_manager.total_connections())
    except Exception:
        clients = 0
    return {
        "status": "online",
        "ready": _STARTUP_READY,
        "service": "The Bunker Command OS API",
        "bot_connected": master_bot_instance is not None,
        "active_clones": len(active_clone_tasks),
        "websocket_clients": clients
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


def resolve_user_id(x_telegram_init_data: Optional[str] = None, authorization: Optional[str] = None) -> int:
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


def require_authenticated_user(x_telegram_init_data: Optional[str] = None, authorization: Optional[str] = None) -> int:
    uid = resolve_user_id(x_telegram_init_data, authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Autenticación requerida: initData o sesión inválida.")
    return uid


# 👑 v12 · REGLA DE ORO: solo el CREADOR (en vivo) y los Arquitectos operan los paneles. Un administrador común,
# con todos o algunos permisos, recibe 403 aunque figure en el registro de dueños.
OWNER_LIVE_ROLES = ("creator",)
_OWNER_DENIED_DETAIL = ("⛔ Acceso Denegado: este panel está reservado única y exclusivamente al Dueño (creador) "
                        "de la comunidad o canal. Los administradores no tienen acceso.")
# Último rango CONFIRMADO por Telegram; ante una caída de la Bot API impide que un no-creador ya conocido
# entre por la vía del registro.
_OWNER_LAST_KNOWN: Dict[tuple, str] = {}
_OWNER_LAST_KNOWN_MAX = 50000


def _sync_registered_owner_link(user_id: int, chat_id: int) -> bool:
    """
    ¿El usuario está registrado como dueño del chat? Mismas tres fuentes que
    handlers/user_private.is_legitimate_owner: user_groups (quien vinculó el bot), bot_clones activos
    y channel_owner_registry (canales sincronizados con el selector nativo de Telegram).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ? LIMIT 1", (int(user_id), int(chat_id)))
        if cursor.fetchone():
            return True
        try:
            cursor.execute("SELECT 1 FROM bot_clones WHERE user_id = ? AND group_id = ? AND status != 'revoked' LIMIT 1",
                           (int(user_id), int(chat_id)))
            if cursor.fetchone():
                return True
            cursor.execute("SELECT 1 FROM channel_owner_registry WHERE user_id = ? AND channel_id = ? LIMIT 1",
                           (int(user_id), int(chat_id)))
            return cursor.fetchone() is not None
        except Exception:      # tablas opcionales en instalaciones antiguas
            return False


async def is_legitimate_chat_owner(user_id: int, chat_id: int) -> bool:
    """
    Regla ÚNICA y estricta de propiedad (paridad 1:1 con handlers/user_private.is_legitimate_owner):
      1. Arquitecto Supremo (SUPER_ADMIN_IDS) → sí, en cualquier comunidad o canal.
      2. Creador ('creator') del chat según Telegram en vivo → sí.
      3. Cualquier otro rango confirmado ('administrator', 'member', 'left'…) → NO, figure o no en
         user_groups / bot_clones / channel_owner_registry.
      4. Telegram no responde → solo el dueño REGISTRADO cuyo último rango confirmado no contradiga ser el
         creador conserva el acceso (con aviso en el log). Nadie sin registro entra por esta vía.
    """
    if not user_id:
        return False
    if is_super_admin(user_id):
        return True
    status = await _live_member_status(int(user_id), int(chat_id))
    if status == "creator":
        return True
    if status:
        if status == "administrator":
            logger.warning(f"⛔ [Propiedad] {user_id} es administrador (no creador) de {chat_id}: panel denegado.")
        return False
    last_known = _OWNER_LAST_KNOWN.get((int(chat_id), int(user_id)))
    if last_known and last_known != "creator":
        logger.warning(f"⛔ [Propiedad] Telegram no respondió y el último rango conocido de {user_id} en {chat_id} es '{last_known}': denegado.")
        return False
    try:
        registered = await asyncio.to_thread(_sync_registered_owner_link, int(user_id), int(chat_id))
    except Exception as ex:
        logger.error(f"❌ [Propiedad] No se pudo consultar el registro de dueños ({user_id}/{chat_id}): {ex}")
        return False
    if registered:
        logger.warning(f"⚠️ [Propiedad] Telegram no confirmó el rango de {user_id} en {chat_id}; se mantiene el acceso del dueño registrado.")
        return True
    return False


async def assert_chat_ownership(user_id: int, chat_id: int):
    """Exige al dueño legítimo (is_legitimate_chat_owner). 401 sin sesión, 403 si no es el dueño."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    if not await is_legitimate_chat_owner(int(user_id), int(chat_id)):
        raise HTTPException(status_code=403, detail=_OWNER_DENIED_DETAIL)


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
        if len(_OWNER_LAST_KNOWN) >= _OWNER_LAST_KNOWN_MAX:
            _OWNER_LAST_KNOWN.clear()
        _OWNER_LAST_KNOWN[key] = status
    except Exception:
        status, ttl = "", 30.0
    if len(_ADMIN_ACCESS_CACHE) >= _ADMIN_ACCESS_CACHE_MAX:
        _ADMIN_ACCESS_CACHE.clear()
    _ADMIN_ACCESS_CACHE[key] = (status, now + ttl)
    return status


async def _is_live_chat_admin(user_id: int, chat_id: int, fresh: bool = False) -> bool:
    """Verifica en Telegram si el usuario es creador/administrador del chat (solo uso informativo, NUNCA autoriza)."""
    return await _live_member_status(user_id, chat_id, fresh=fresh) in ("creator", "administrator")


async def _is_live_chat_creator(user_id: int, chat_id: int, fresh: bool = False) -> bool:
    """👑 Autorización de destinos (canal de registro, difusión): Arquitecto o CREADOR en vivo del chat destino."""
    if is_super_admin(user_id):
        return True
    return await _live_member_status(user_id, chat_id, fresh=fresh) == "creator"


async def assert_chat_access(user_id: int, chat_id: int):
    """
    Acceso de SOLO LECTURA a la analítica (REST y /ws/radar): dueño legítimo, Arquitecto o delegado al que el
    Arquitecto activó `can_view_analytics` en la Matriz (v13). Un administrador común sigue recibiendo 403.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    if await is_legitimate_chat_owner(int(user_id), int(chat_id)):
        return
    try:
        delegated = await has_delegate_permission(int(chat_id), int(user_id), "can_view_analytics")
    except Exception as ex:
        logger.warning(f"⚠️ [Delegados] Consulta de analítica fallida ({user_id}/{chat_id}): {ex}")
        delegated = False
    if delegated and await _live_member_status(int(user_id), int(chat_id)) not in ("left", "kicked"):
        return
    raise HTTPException(status_code=403, detail=_OWNER_DENIED_DETAIL)


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
# v11: por defecto la sincronización solo vincula al CREADOR. Antes también vinculaba a cualquier administrador,
# que así pasaba a figurar como "dueño" y obtenía todos los paneles. Configurable con SYNC_LINK_ROLES.
# v12: fijo en el creador. Se ignora cualquier SYNC_LINK_ROLES heredado que incluya 'administrator'.
SYNC_LINK_ROLES = frozenset({"creator"})
if os.getenv("SYNC_LINK_ROLES", "creator").strip().lower() not in ("", "creator"):
    logger.warning("⚠️ [Sync] SYNC_LINK_ROLES ignorado: por la Regla de Oro solo se vincula al creador del chat.")
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


async def _resolve_dashboard_plan(user_id: int, community_plan: Any) -> dict:
    # 👑 El Arquitecto Supremo opera con ULTRA PRO universal en cualquier comunidad.
    if is_super_admin(user_id):
        try:
            return describe_license("ultra_pro", None, "architect")
        except Exception:
            return {"tier": "ultra_pro", "name": "ULTRA PRO", "status": "active", "source": "architect"}
    plan = community_plan if isinstance(community_plan, dict) else describe_license("free")
    if plan.get("status") == "active" and plan.get("tier") in ("pro", "ultra_pro"):
        return plan
    try:
        operator_tier = await get_user_global_tier(user_id)
    except Exception as ex:
        logger.debug(f"Sin licencia global para {user_id}: {ex}")
        operator_tier = "free"
    operator_plan = describe_license(operator_tier, None, "operator")
    return operator_plan if operator_plan["status"] == "active" else describe_license("free")


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

        # v9 · Plan tarifario: el operador ya pasó assert_chat_ownership (creador/administrador verificado).
        # Si la comunidad no tiene licencia propia se muestra la licencia VIGENTE del operador (PRO ⭐ /
        # ULTRA PRO 💎, source="operator") en lugar de un plan vacío.
        data["plan"] = await _resolve_dashboard_plan(user_id, data.get("plan"))
        # v29 · Derechos por función (muro de pago): la Mini App bloquea paneles sin adivinar el nivel.
        data["entitlements"] = await _v29_entitlements(numeric_id, user_id)
        data["raid_shield"] = raid_shield.status_payload(numeric_id)
        # v17 · Supervisión del Bot Clon y del Centinela (estado en vivo; jamás credenciales).
        data.update(await build_assets_status(numeric_id))
        # v20 · Estado REAL del perímetro en la primera vista (sin bloquear el dashboard si algo tarda).
        try:
            data["perimeter"] = await asyncio.wait_for(build_perimeter_payload(numeric_id), timeout=8)
        except Exception as ex:
            logger.debug(f"[Perímetro] Estado no disponible para {numeric_id}: {ex}")

        # v8.2: difusión personalizada del Estudio (chat destino, intervalo, texto promocional).
        if load_broadcast_config is not None and public_broadcast_config is not None:
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


@api_router.get("/chat/{chat_id}/assets-status")
async def api_chat_assets_status(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """v17 · Estado en vivo del Bot Clon y del Centinela (botón 🔄 y sondeo de la Mini App). Sin credenciales."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero.")
    await assert_chat_ownership(user_id, numeric_id)
    payload = {"status": "success", "chat_id": str(numeric_id), **(await build_assets_status(numeric_id))}
    # v19 · Estado en vivo: el WebView de Telegram (iOS/Android) y los proxies no deben cachear esta respuesta.
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


# ==========================================================================
# 🛰️ v20 · PERÍMETRO OPERATIVO, CENTINELA IA, GHOST PURGE v2 Y SOPORTE IA
# ==========================================================================
_NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _assistant_runtime_is_purging(chat_id: int) -> bool:
    fn = getattr(_assistant_runtime, "is_ghost_purge_running", None)
    return bool(fn(int(chat_id))) if callable(fn) else False


def _parse_chat_id(chat_id: str) -> int:
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="chat_id debe ser un entero.")


async def _read_log_channel_id(chat_id: int):
    def _sync():
        with get_db_connection() as conn:
            row = conn.execute("SELECT log_channel_id FROM group_settings WHERE group_id = ?", (int(chat_id),)).fetchone()
            return row[0] if row and row[0] else None
    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        return None


async def build_perimeter_payload(chat_id: int) -> dict:
    """Estado real del perímetro: configuración + capacidad efectiva (Centinela, licencia, IA, canal de registro)."""
    cid = int(chat_id)
    log_id = await _read_log_channel_id(cid)
    log_health = (await perimeter_ops.check_log_channel_health(master_bot_instance, log_id)) if log_id \
        else {"ok": False, "reason": "not_configured"}
    try:
        raid = raid_shield.status_payload(cid) or {}
    except Exception:
        raid = {}
    return await perimeter_ops.build_perimeter_status(
        cid, sentinel=_sentinel_runtime(cid), raid=raid, log_health=log_health,
    )


async def _require_ultra_for_ai(user_id: int, chat_id: int) -> None:
    if is_super_admin(user_id):
        return
    try:
        tier = await get_group_tier(int(chat_id))
    except Exception:
        tier = "free"
    if not perimeter_ops._is_ultra(tier):
        raise HTTPException(status_code=402, detail="El Centinela de IA requiere licencia ULTRA PRO.")


# ==========================================
# 🔐 v29 · MURO DE PAGO POR NIVEL EN LA API (402 Payment Required)
# ==========================================
# Orden de defensa en cada endpoint: 401 (sesión) → 403 (Regla de Oro: assert_chat_ownership/assert_chat_access)
# → 402 (licencia). Así un tercero nunca descubre el plan de una comunidad ajena y una comunidad Free nunca
# lee ni escribe datos de funciones de pago, aunque llame a la API a mano. El `detail` es un objeto estable que
# la Mini App usa para pintar la tarjeta de conversión (PRO ⭐ / ULTRA PRO 💎) con el chat_id correcto.
_V29_TIER_RANK = {"free": 0, "pro": 1, "ultra_pro": 2}
_V29_TIER_LABELS = {"free": "Free", "pro": "PRO ⭐", "ultra_pro": "ULTRA PRO 💎"}


def _v29_norm_tier(tier) -> str:
    value = str(tier or "free").strip().lower().replace("-", "_").replace(" ", "_")
    if value in ("ultra_pro", "ultrapro", "ultra"):
        return "ultra_pro"
    return "pro" if value == "pro" else "free"


def _v29_required_tier(feature: str) -> str:
    try:
        from database.database import FEATURE_MIN_TIER
        return FEATURE_MIN_TIER.get(feature, "pro")
    except Exception:
        return "pro"


async def require_chat_feature(user_id: int, chat_id: int, feature: str) -> str:
    """Lanza 402 si la licencia VIGENTE de la comunidad no alcanza el nivel de `feature`. Devuelve el nivel."""
    if is_super_admin(user_id):
        return "ultra_pro"
    try:
        tier = _v29_norm_tier(await get_group_tier(int(chat_id)))
    except Exception as ex:
        logger.warning(f"⚠️ [Paywall] Licencia ilegible para {chat_id}: {ex}")
        tier = "free"   # fallo cerrado
    required = _v29_required_tier(feature)
    if _V29_TIER_RANK[tier] >= _V29_TIER_RANK[required]:
        return tier
    raise HTTPException(status_code=402, detail={
        "code": "tier_required",
        "feature": feature,
        "required_tier": required,
        "current_tier": tier,
        "chat_id": str(chat_id),
        "message": f"Esta función requiere licencia {_V29_TIER_LABELS[required]} en esta comunidad.",
    })


async def _v29_entitlements(chat_id: int, user_id: int) -> dict:
    """Bloque `entitlements` del dashboard (misma regla que require_chat_feature)."""
    try:
        from database.database import FEATURE_LABELS, FEATURE_MIN_TIER
    except Exception:
        FEATURE_MIN_TIER, FEATURE_LABELS = {}, {}
    if is_super_admin(user_id):
        tier = "ultra_pro"
    else:
        try:
            tier = _v29_norm_tier(await get_group_tier(int(chat_id)))
        except Exception:
            tier = "free"
    return {
        "tier": tier,
        "label": _V29_TIER_LABELS[tier],
        "features": {name: {"allowed": _V29_TIER_RANK[tier] >= _V29_TIER_RANK[_v29_norm_tier(req)],
                            "required": _v29_norm_tier(req), "label": FEATURE_LABELS.get(name, name)}
                     for name, req in FEATURE_MIN_TIER.items()},
    }


@api_router.get("/chat/{chat_id}/perimeter-status")
async def api_chat_perimeter_status(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """🟢 / 🟡 / 🔴 en vivo de captcha, AutoLower, escudos, Guardián y Copiloto IA, modo nocturno y canal de registro."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    payload = {"status": "success", "perimeter": await build_perimeter_payload(numeric_id)}
    return JSONResponse(content=payload, headers=_NO_STORE)


@api_router.get("/chat/{chat_id}/ai-sentinel")
async def api_get_ai_sentinel(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    try:
        tier = await get_group_tier(numeric_id)
    except Exception:
        tier = "free"
    from database.database import get_ai_sentinel_config
    cfg = perimeter_ops.public_ai_sentinel_config(await get_ai_sentinel_config(numeric_id))
    licensed = is_super_admin(user_id) or perimeter_ops._is_ultra(tier)
    return JSONResponse(content={"status": "success", "ai_sentinel": cfg, "licensed": licensed}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/ai-sentinel")
async def api_update_ai_sentinel(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{ai_sentinel: {guardian_status, copilot_status, response_mode, response_chance, personality_tone, custom_prompt}}"""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    await _require_ultra_for_ai(user_id, numeric_id)
    patch = (payload or {}).get("ai_sentinel")
    try:
        cfg = await perimeter_ops.apply_ai_sentinel_patch(numeric_id, patch)
    except perimeter_ops.AISettingsError as err:
        raise HTTPException(status_code=422, detail=f"{err.field}: {err.message}")
    _spawn(emit_settings_updated(numeric_id, "settings", [f"ai_{k}" for k in (patch or {})]), name=f"ai_sync:{numeric_id}")
    return {"status": "success", "ai_sentinel": cfg}


@api_router.get("/chat/{chat_id}/ghost-purge")
async def api_get_ghost_purge(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    settings = await perimeter_ops.get_purge_settings(numeric_id)
    settings["running"] = _assistant_runtime_is_purging(numeric_id)
    return JSONResponse(content={"status": "success", "ghost_purge": settings}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/ghost-purge")
async def api_update_ghost_purge(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{ghost_purge: {action: 'ban'|'kick', criteria: ['deleted','scam','fake','flagged','unreachable']}}"""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    body = (payload or {}).get("ghost_purge") or {}
    action, criteria = body.get("action"), body.get("criteria")
    if action is not None and str(action).strip().lower() not in perimeter_ops._PURGE_ACTION_ALIASES:
        raise HTTPException(status_code=422, detail="action: usa 'ban' o 'kick'.")
    if criteria is not None:
        if not isinstance(criteria, list) or any(str(c) not in perimeter_ops.PURGE_CRITERIA for c in criteria):
            raise HTTPException(status_code=422, detail=f"criteria: valores válidos {', '.join(perimeter_ops.PURGE_CRITERIA)}.")
        if not criteria:
            raise HTTPException(status_code=422, detail="criteria: activa al menos un criterio.")
    settings = await perimeter_ops.save_purge_settings(numeric_id, action=action, criteria=criteria)
    settings["running"] = _assistant_runtime_is_purging(numeric_id)
    return {"status": "success", "ghost_purge": settings}


@api_router.post("/support/ask")
async def api_support_ask(
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """Asistente de Soporte IA (Arquitecto de Soporte). {message, lang} → {reply, fallback}."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    message = str((payload or {}).get("message") or "")
    lang = "en" if str((payload or {}).get("lang") or "es").lower() == "en" else "es"
    try:
        result = await perimeter_ops.support_ai_reply(user_id, message, lang)
    except perimeter_ops.SupportRateLimited as rl:
        # v31 · El aviso sigue el idioma REAL del mensaje (rl.lang), no solo el de la interfaz.
        wait_text = (f"Wait {rl.retry_after} s before sending another question." if getattr(rl, "lang", lang) == "en"
                     else f"Espera {rl.retry_after} s antes de enviar otra consulta.")
        return JSONResponse(status_code=429, content={"detail": wait_text},
                            headers={"Retry-After": str(rl.retry_after)})
    return {"status": "success", **result}


@api_router.post("/support/reset")
async def api_support_reset(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    await perimeter_ops.support_ai_reset(user_id)
    return {"status": "success"}


@api_router.get("/chat/{chat_id}/micvip-audit")
async def api_chat_micvip_audit(
    chat_id: str,
    status: str = "all",
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """
    🎟️ v22 · Registro de auditoría MicVIP: alias, compra, expiración real, estado (🟢 / 🔴) y evidencia del
    Centinela. Lectura para el dueño, el Arquitecto y los delegados con can_view_analytics.
    """
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_access(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "micvip_audit")
    status = status if status in ("all", "active", "expired", "issues") else "all"
    data = await micvip_audit.get_audit(numeric_id, status=status, limit=limit, offset=offset)
    # Sin AutoLower nadie queda atenuado: el pase no tiene restricción que levantar (la interfaz lo explica).
    try:
        from database.database import get_autolower_status
        data["autolower_enabled"] = int(await get_autolower_status(numeric_id) or 0) == 1
    except Exception:
        data["autolower_enabled"] = None
    data["sentinel_active"] = bool(_sentinel_runtime(numeric_id).get("active"))
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "filter": status, **data}, headers=_NO_STORE)


# ==========================================
# 🛡️👑🔗 v23 · REFUERZO PERIMETRAL · ROLES VIP · CUOTAS DE ENLACES DE ADMINISTRADORES
# ==========================================
# Lectura: dueño, Arquitecto o delegado con can_view_analytics (assert_chat_access).
# Escritura: SOLO el dueño legítimo o el Arquitecto (assert_chat_ownership), igual que /configuration.
# Toda escritura emite 'settings_updated' con `changed` prefijado (shield_* / vip_* / alq_*): el bot y las demás
# sesiones de la Mini App repintan sin recargar.
def _v23_validation_error(err) -> Exception:
    errors = getattr(err, "errors", None) or {}
    detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or str(err)
    return HTTPException(status_code=422, detail=detail)


def _v23_emit(chat_id: int, changed: list) -> None:
    _spawn(emit_settings_updated(int(chat_id), "configuration", changed), name=f"settings_updated:{chat_id}")


def _v23_forget_shield(chat_id: int) -> None:
    with contextlib.suppress(Exception):
        from handlers import media_locks as _ml
        _ml.forget_shield_cache(int(chat_id))


@api_router.get("/chat/{chat_id}/shield")
async def api_get_chat_shield(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """🛡️ v23 · Ráfaga de reenvíos masivos + bots autorizados (inline, botones, invitaciones)."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_access(user_id, numeric_id)
    from database.database import get_shield_config
    shield = await get_shield_config(numeric_id)
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "shield": shield}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/shield")
async def api_update_chat_shield(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{shield: {fwd_burst_limit?, fwd_burst_window?, add_bot?: "@alias", remove_bot?: "alias"}} → 422 "campo: motivo"."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    from database.database import (BunkerValidationError, add_authorized_bot, get_shield_config, remove_authorized_bot,
                                   set_fwd_burst)
    body = payload.get("shield", payload) if isinstance(payload, dict) else {}
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=422, detail="shield: se esperaba un objeto con al menos un campo.")
    unknown = [k for k in body if k not in ("fwd_burst_limit", "fwd_burst_window", "add_bot", "remove_bot")]
    if unknown:
        raise HTTPException(status_code=422, detail=f"shield.{unknown[0]}: campo desconocido.")
    changed = []
    try:
        if "fwd_burst_limit" in body or "fwd_burst_window" in body:
            await set_fwd_burst(numeric_id, limit=body.get("fwd_burst_limit"), window=body.get("fwd_burst_window"))
            changed.append("shield_fwd_burst")
        if body.get("add_bot"):
            await add_authorized_bot(numeric_id, str(body["add_bot"]), added_by=user_id)
            changed.append("shield_authorized_bots")
        if body.get("remove_bot"):
            await remove_authorized_bot(numeric_id, str(body["remove_bot"]))
            changed.append("shield_authorized_bots")
    except BunkerValidationError as err:
        raise _v23_validation_error(err)
    _v23_forget_shield(numeric_id)
    _v23_emit(numeric_id, changed)
    shield = await get_shield_config(numeric_id)
    logger.info(f"🛡️ [Refuerzo] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(changed)}")
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "shield": shield}, headers=_NO_STORE)


@api_router.get("/chat/{chat_id}/vip-roles")
async def api_get_vip_roles(
    chat_id: str,
    status: str = "all",
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """👑 v23 · Configuración de roles VIP + miembros (estado, origen, expiración, estado de la etiqueta)."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_access(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "vip_roles")
    from database.database import get_vip_role_config, vip_role_list
    status = status if status in ("all", "active", "expired") else "all"
    config = await get_vip_role_config(numeric_id)
    members = await vip_role_list(numeric_id, status=status, limit=limit, offset=offset)
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "filter": status,
                                 "vip_roles": config, **members}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/vip-roles")
async def api_update_vip_roles(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{vip_roles: {enabled?, tag_text?, donor_min_stars?, donor_days?, micvip_role?, exempt_vectors?, flood_bonus?, …}}"""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "vip_roles")
    from database.database import BunkerValidationError
    patch = payload.get("vip_roles", payload) if isinstance(payload, dict) else {}
    try:
        config = await vip_roles.update_config(numeric_id, patch, updated_by=user_id)
    except BunkerValidationError as err:
        raise _v23_validation_error(err)
    _v23_emit(numeric_id, [f"vip_{k}" for k in patch])
    logger.info(f"👑 [VIP Roles] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(sorted(patch))}")
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "vip_roles": config}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/vip-roles/member")
async def api_vip_role_member(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{action: 'grant'|'revoke'|'retag', user_id, days?: 0(permanente)…3650} — gestión manual del dueño."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "vip_roles")
    action = str((payload or {}).get("action") or "")
    try:
        target = int(cast(Any, (payload or {}).get("user_id")))   # None / texto → TypeError / ValueError (422)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="user_id: se esperaba un id numérico.")
    if target <= 0:
        raise HTTPException(status_code=422, detail="user_id: se esperaba un id de usuario positivo.")
    if action == "grant":
        try:
            days = int((payload or {}).get("days", 30))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="days: se esperaba un entero.")
        if not 0 <= days <= 3650:
            raise HTTPException(status_code=422, detail="days: entre 0 (permanente) y 3650.")
        result = await vip_roles.grant_manual(numeric_id, target, days, actor_id=user_id)
        member = result.get("member")
    elif action == "revoke":
        member = await vip_roles.revoke(numeric_id, target, actor_id=user_id)
        if not member:
            raise HTTPException(status_code=404, detail="Ese usuario no tiene rol VIP en esta comunidad.")
    elif action == "retag":
        await vip_roles.retag(numeric_id, target)
        from database.database import vip_role_member_get
        member = await vip_role_member_get(numeric_id, target)
    else:
        raise HTTPException(status_code=422, detail="action: usa grant, revoke o retag.")
    _v23_emit(numeric_id, ["vip_members"])
    logger.info(f"👑 [VIP Roles] {action} manual en {numeric_id} para {target} por {user_id}.")
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "member": member}, headers=_NO_STORE)


@api_router.get("/chat/{chat_id}/admin-link-quotas")
async def api_get_admin_link_quotas(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """🔗 v23 · Cuota diaria de enlaces: configuración + uso de cada administrador en la ventana móvil de 24 h."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_access(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "link_quotas")
    if master_bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot no disponible todavía.")
    overview = await link_quotas.get_overview(master_bot_instance, numeric_id)
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "admin_link_quotas": overview},
                        headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/admin-link-quotas")
async def api_update_admin_link_quotas(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{admin_link_quotas: {enabled?, default_limit?, count_forwards?, overrides?: {admin_id: n | -1 | null}}} — solo el dueño."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "link_quotas")
    from database.database import BunkerValidationError, update_admin_link_quota_config
    patch = payload.get("admin_link_quotas", payload) if isinstance(payload, dict) else {}
    try:
        await update_admin_link_quota_config(numeric_id, patch, updated_by=user_id)
    except BunkerValidationError as err:
        raise _v23_validation_error(err)
    link_quotas.invalidate(numeric_id)
    _v23_emit(numeric_id, [f"alq_{k}" for k in patch])
    overview = await link_quotas.get_overview(master_bot_instance, numeric_id) if master_bot_instance else None
    logger.info(f"🔗 [LinkQuota] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(sorted(patch))}")
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "admin_link_quotas": overview},
                        headers=_NO_STORE)


# ==========================================
# ⚖️ v24 · APELACIÓN DE BANEOS POR TELEGRAM STARS
# ==========================================
@api_router.get("/chat/{chat_id}/ban-appeals")
async def api_get_ban_appeals(
    chat_id: str,
    status: str = "all",
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """Configuración de apelaciones + historial (sin enlaces de reingreso). Lectura: dueño, Arquitecto, delegados de analítica."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_access(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "ban_appeals")
    from database.database import appeal_list, get_appeal_config
    status = status if status in ("all", "open", "paid", "closed") else "all"
    config = await get_appeal_config(numeric_id)
    listing = await appeal_list(numeric_id, status=status, limit=limit, offset=offset)
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "filter": status,
                                 "ban_appeals": config, **listing}, headers=_NO_STORE)


@api_router.post("/chat/{chat_id}/ban-appeals")
async def api_update_ban_appeals(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{ban_appeals: {enabled?, price_stars?, link_ttl_min?, window_days?, max_per_user?}} — solo el dueño. 422 "campo: motivo"."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    numeric_id = _parse_chat_id(chat_id)
    await assert_chat_ownership(user_id, numeric_id)
    await require_chat_feature(user_id, numeric_id, "ban_appeals")
    from database.database import BunkerValidationError, update_appeal_config
    patch = payload.get("ban_appeals", payload) if isinstance(payload, dict) else {}
    try:
        config = await update_appeal_config(numeric_id, patch)
    except BunkerValidationError as err:
        raise _v23_validation_error(err)
    _v23_emit(numeric_id, [f"appeal_{k}" for k in patch])
    logger.info(f"⚖️ [Apelaciones] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(sorted(patch))}")
    return JSONResponse(content={"status": "success", "chat_id": str(numeric_id), "ban_appeals": config}, headers=_NO_STORE)


@api_router.get("/user/language")
async def api_get_user_language(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """🌐 v21 · Idioma guardado del operador (compartido con el bot). chosen=false → nunca eligió (se usa 'default')."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    from database.database import get_user_lang, DEFAULT_LANG
    stored = await get_user_lang(user_id)
    return JSONResponse(content={"status": "success", "lang": stored or DEFAULT_LANG, "chosen": bool(stored),
                                 "default": DEFAULT_LANG, "supported": ["en", "es"]}, headers=_NO_STORE)


@api_router.post("/user/language")
async def api_set_user_language(
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{lang: 'en' | 'es'} → se guarda en users.lang y el bot lo aplica en el siguiente mensaje."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    from database.database import set_user_lang, normalize_lang
    lang = normalize_lang((payload or {}).get("lang"))
    if lang is None:
        raise HTTPException(status_code=422, detail="lang: usa 'en' o 'es'.")
    saved = await set_user_lang(user_id, lang)
    try:
        from handlers.user_private import forget_user_lang_cache
        forget_user_lang_cache(user_id)
    except Exception as ex:
        logger.debug(f"[i18n] Caché de idioma del bot no invalidada para {user_id}: {ex}")
    return {"status": "success", "lang": saved}


@api_router.get("/support/directives")
async def api_get_support_directives(
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    if not is_super_admin(user_id):
        raise HTTPException(status_code=403, detail="Solo el Arquitecto puede ver las directivas del soporte IA.")
    return {"status": "success", "directives": await perimeter_ops.get_support_directives(force=True)}


@api_router.post("/support/directives")
async def api_set_support_directives(
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    if not is_super_admin(user_id):
        raise HTTPException(status_code=403, detail="Solo el Arquitecto puede editar las directivas del soporte IA.")
    saved = await perimeter_ops.set_support_directives((payload or {}).get("directives", ""), updated_by=user_id)
    return {"status": "success", "directives": saved}


@api_router.get("/chat/{chat_id}/stats")
async def api_chat_stats(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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
        await assert_chat_access(user_id, numeric_id)   # lectura: dueño, Arquitecto o delegado con analítica
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


# v8.2: servicio de difusión personalizada y planes de canal (channel_plans_api.py).
# v10: el módulo vive en el paquete handlers/ (handlers/channel_plans_api.py). Se mantiene el respaldo a la
# raíz del proyecto para que un despliegue a medio migrar no pierda los endpoints de planes ni el programador.
import importlib


def _import_channel_plans_api():
    """Resuelve channel_plans_api una sola vez: primero handlers/, luego la raíz. None si no existe."""
    for module_name in ("handlers.channel_plans_api", "channel_plans_api"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as ex:
            # Solo se ignora la ausencia del PROPIO módulo; un import roto dentro de él debe verse en los logs.
            if ex.name not in (module_name, module_name.split(".")[0]):
                logger.error(f"❌ [Planes] {module_name} no pudo importar una dependencia: {ex}", exc_info=True)
                return None
            continue
        logger.info(f"💎 [Planes] Módulo de planes de canal cargado desde '{module_name}'.")
        return module
    logger.warning("⚠️ [Planes] No se encontró channel_plans_api (handlers/ ni raíz); se omiten sus endpoints.")
    return None


_channel_plans_api = _import_channel_plans_api()
if _channel_plans_api is not None:
    PlanApiError = _channel_plans_api.PlanApiError
    load_broadcast_config = _channel_plans_api.load_broadcast_config
    _load_plan_deps = _channel_plans_api.load_db_deps
    public_broadcast_config = _channel_plans_api.public_broadcast_config
    run_custom_broadcast_scheduler = _channel_plans_api.run_custom_broadcast_scheduler
    save_broadcast_config = _channel_plans_api.save_broadcast_config
    build_channel_plans_router = _channel_plans_api.build_channel_plans_router
else:
    class PlanApiError(Exception):  # type: ignore[no-redef]
        status, detail = 500, ""
    load_broadcast_config = save_broadcast_config = run_custom_broadcast_scheduler = None  # type: ignore[assignment]
    public_broadcast_config = _load_plan_deps = build_channel_plans_router = None  # type: ignore[assignment]

_PLAN_DEPS_CACHE: Dict[str, Any] = {}


def _plan_deps():
    if "deps" not in _PLAN_DEPS_CACHE:
        if _load_plan_deps is None:   # módulo de planes ausente: los llamadores ya lo comprueban antes
            raise RuntimeError("channel_plans_api no disponible")
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
    # v20 · Misma validación EN VIVO que la consola del bot (perimeter_ops.resolve_log_channel): mensajes y
    # códigos HTTP idénticos a v19, más el rechazo explícito de chats privados como destino.
    try:
        value = await perimeter_ops.resolve_log_channel(
            master_bot_instance, user_id, chat_id, raw,
            is_creator=lambda uid, cid: _is_live_chat_creator(uid, cid, fresh=True),
        )
    except perimeter_ops.LogChannelError as err:
        raise HTTPException(status_code=err.status, detail=f"log_channel_id: {err.message}")
    if value:
        perimeter_ops.forget_log_channel_health(value)
    return value


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

        # v17 · Separación de responsabilidades: los tokens de Bot Clon y las String Sessions del Centinela se
        # vinculan EXCLUSIVAMENTE en el chat privado del bot (flujos guiados con verificación). La Mini App solo
        # supervisa. Cualquier credencial que llegue por aquí se rechaza SIN procesarla ni registrarla.
        if action in ("deploy_clone", "connect_sentinel") or "bot_token" in (payload or {}) or "session_string" in (payload or {}):
            logger.warning(f"⛔ [Activos] {user_id} intentó vincular credenciales desde la Mini App en {numeric_id}: rechazado.")
            raise HTTPException(
                status_code=410,
                detail="Las credenciales del Bot Clon y del Centinela se vinculan solo en el chat privado del bot "
                       "(🧬 Clon & Centinela). La Mini App únicamente muestra su estado."
            )

        # 1. Despliegue de Bot Clon (ruta heredada; inalcanzable desde v17: se rechaza arriba)
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
            # v20 · Directiva guardada de la comunidad (ban / expulsión suave + criterios), salvo que la petición la
            # fije. Antes siempre era "ban" y solo detectaba cuentas eliminadas.
            if _assistant_runtime_is_purging(numeric_id):
                raise HTTPException(status_code=409, detail="Ya hay una Ghost Purge en curso en esta comunidad.")
            purge_action = payload.get("purge_action")
            purge_criteria = payload.get("criteria")
            _spawn(execute_ghost_purge(
                numeric_id,
                action=perimeter_ops.normalize_purge_action(purge_action) if purge_action else None,
                criteria=perimeter_ops.parse_purge_criteria(purge_criteria) if purge_criteria is not None else None,
                origin="miniapp", lang="en" if str(payload.get("lang", "es")).lower() == "en" else "es",
            ), name=f"ghost_purge:{numeric_id}")
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
        broadcast_keys = ("broadcast_target", "broadcast_interval", "promo_text", "broadcast_lang")
        if any(k in payload for k in broadcast_keys):
            if save_broadcast_config is None:
                raise HTTPException(status_code=503, detail="Difusión personalizada no disponible en este despliegue.")
            broadcast_fields = {k: payload.pop(k) for k in broadcast_keys if k in payload}
            try:
                response_extra["broadcast"] = await save_broadcast_config(
                    numeric_id, user_id, broadcast_fields, _plan_deps(), master_bot_instance, _is_live_chat_creator)
            except PlanApiError as err:
                raise HTTPException(status_code=err.status, detail=err.detail)

        # 6. Actualización general (interruptores, Estudio: enlace VIP, tarifa, duración, …)
        if payload:
            await update_chat_operational_settings(numeric_id, payload)

        # Notificar en vivo a los WebSockets de la sala: payload original (compatibilidad) + estado real
        # guardado (árbol de configuración + Centinela) para repintar los switches sin recargar.
        if payload or response_extra:
            _spawn(emit_settings_updated(numeric_id, "settings", list(payload.keys()) + list(response_extra.keys()), payload),
                   name=f"settings_updated:{numeric_id}")

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
    # Árbol ya validado y guardado + interruptores del Centinela (la misma forma que /settings y el bot).
    _spawn(emit_settings_updated(numeric_id, "configuration", changed, {"configuration": configuration}),
           name=f"settings_updated:{numeric_id}")
    logger.info(f"🎛️ [Configuración] Comunidad {numeric_id} actualizada por {user_id}: {', '.join(changed)}")
    return {"status": "success", "chat_id": str(numeric_id), "configuration": configuration}


# ==========================================
# 🧾 v9 · INFORME PERIÓDICO DEL CANAL DE REGISTRO (RENDIMIENTO DE ADMINISTRADORES)
# ==========================================
LOG_REPORT_SEND_COOLDOWN_S = 60.0
_LOG_REPORT_LAST_SEND: Dict[int, float] = {}


@api_router.get("/chat/{chat_id}/log-report")
async def api_get_log_report(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    await assert_chat_ownership(user_id, numeric_id)
    return {"status": "success", "chat_id": str(numeric_id), "log_report": await get_log_report_config(numeric_id)}


@api_router.post("/chat/{chat_id}/log-report")
async def api_save_log_report(
    chat_id: str,
    payload: dict = Body(...),
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    """{enabled, interval_hours: 24|72|168, metrics: [...], lang}. 422 'campo: motivo'. Exige canal de registro."""
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    await assert_chat_ownership(user_id, numeric_id)
    body = payload.get("log_report", payload) if isinstance(payload, dict) else payload
    if isinstance(body, dict) and body.get("enabled"):
        current = await get_log_report_config(numeric_id)
        if not current.get("log_channel_id"):
            raise HTTPException(status_code=422, detail="log_channel_id: configura primero el canal de registro.")
    try:
        cfg = await save_log_report_config(numeric_id, body, updated_by=user_id)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    _spawn(emit_radar_event(numeric_id, "settings_updated", {"source": "log_report", "log_report": cfg}))
    return {"status": "success", "chat_id": str(numeric_id), "log_report": cfg}


@api_router.post("/chat/{chat_id}/log-report/send")
async def api_send_log_report(
    chat_id: str,
    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
    authorization: str = Header(None)
):
    user_id = require_authenticated_user(x_telegram_init_data, authorization)
    try:
        numeric_id = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id inválido.")
    await assert_chat_ownership(user_id, numeric_id)
    if master_bot_instance is None:
        raise HTTPException(status_code=503, detail="El bot no está disponible en este momento.")
    now = time.monotonic()
    last = _LOG_REPORT_LAST_SEND.get(numeric_id)
    if last is not None and now - last < LOG_REPORT_SEND_COOLDOWN_S:
        raise HTTPException(status_code=429, detail=f"Espera {int(LOG_REPORT_SEND_COOLDOWN_S - (now - last)) + 1} s para enviar otro informe.")
    _LOG_REPORT_LAST_SEND[numeric_id] = now
    try:
        result = await ecosystem.send_log_report_now(master_bot_instance, numeric_id)
    except ValueError as err:
        _LOG_REPORT_LAST_SEND.pop(numeric_id, None)
        raise HTTPException(status_code=422, detail=str(err))
    except Exception as ex:
        _LOG_REPORT_LAST_SEND.pop(numeric_id, None)
        logger.warning(f"⚠️ [Log Report] Envío manual fallido en {numeric_id}: {ex}")
        raise HTTPException(status_code=502, detail="Telegram rechazó el informe. Verifica que el bot pueda publicar en el canal de registro.")
    return {"status": "success", "chat_id": str(numeric_id), **result}


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


SECURITY_STATS_TTL = 5.0
_SECURITY_STATS_CACHE: Dict[int, tuple] = {}


async def _security_stats(chat_id: int) -> Optional[dict]:
    """Bloque `security` (bloqueos/expulsiones del sistema) con caché corta; nunca bloquea la analítica."""
    entry = _SECURITY_STATS_CACHE.get(chat_id)
    if entry and time.monotonic() - entry[0] < SECURITY_STATS_TTL:
        return entry[1]
    try:
        stats = await asyncio.wait_for(get_security_block_stats(chat_id), timeout=2.0)
    except Exception as ex:
        logger.debug(f"Aviso calculando bloqueos de seguridad ({chat_id}): {ex}")
        return entry[1] if entry else None
    stats["shield"] = {**(stats.get("shield") or {}), **raid_shield.status_payload(chat_id)}
    if len(_SECURITY_STATS_CACHE) > 2000:
        _SECURITY_STATS_CACHE.clear()
    _SECURITY_STATS_CACHE[chat_id] = (time.monotonic(), stats)
    return stats


async def _decorate_analytics_full(chat_id: int, data: dict) -> dict:
    payload = _decorate_analytics(chat_id, data)
    security = await _security_stats(chat_id)
    if security is not None:
        payload["security"] = security
    return payload


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

    payload = await _decorate_analytics_full(numeric_id, data)
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
    "stars_payment", "settings_updated", "security_block", "raid_shield",
    "assets_status",   # v18 · supervisión en vivo del Bot Clon y del Centinela (sin credenciales)
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
            return await _decorate_analytics_full(chat_id, data)
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
    _assets_watch_add(numeric_id)

    # 3. Saludo + snapshots iniciales (en segundo plano: el lector arranca ya)
    radar_hub.send_to(client, _ws_envelope(numeric_id, "hello", {
        "user_id": user_id,
        "events": sorted(RADAR_EVENTS),
        "idle_timeout": WS_IDLE_TIMEOUT,
        "signature": "Cloud Media Management",
    }))
    snapshot_task = _spawn(_ws_send_snapshots(client, numeric_id), name=f"ws_snapshot:{numeric_id}")
    _spawn(_ws_send_assets_status(client, numeric_id), name=f"ws_assets:{numeric_id}")

    # 4. Bucle lector con límite de tamaño, token bucket e inactividad
    tokens = WS_RATE_CAPACITY
    last_refill = time.monotonic()
    violations = 0
    last_refresh = 0.0
    last_assets_request = 0.0
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
            elif action == "assets":
                # v19 · Botón 🔄 de la Mini App: estado de activos recalculado al momento, solo para este cliente.
                if now_mono - last_assets_request < ASSETS_WS_REQUEST_COOLDOWN_S:
                    continue
                last_assets_request = now_mono
                _spawn(_ws_send_assets_status(client, numeric_id), name=f"ws_assets:{numeric_id}")
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
        _assets_watch_remove(numeric_id)
        await radar_hub.disconnect(numeric_id, websocket)

# Router de planes de canal (handlers/channel_plans_api.py, resuelto una sola vez por
# _import_channel_plans_api). Si el módulo no existe en este despliegue, se omite de forma segura.
if build_channel_plans_router is not None:
    api_router.include_router(build_channel_plans_router(
        require_user=require_authenticated_user,
        assert_owner=assert_chat_ownership,
        get_bot=lambda: master_bot_instance,
    ))


app.include_router(api_router, prefix="/api")


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
        cast(Any, app),   # app puede ser el stub sin FastAPI: Pylance no lo acepta como ASGIApplication
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


async def _resolve_creator_id(bot: Optional[Bot], chat_id: int) -> Optional[int]:
    """ID del creador real del chat según Telegram (None si no se puede determinar)."""
    if bot is None:
        return None
    try:
        admins = await asyncio.wait_for(bot.get_chat_administrators(chat_id), timeout=6)
    except Exception as ex:
        logger.debug(f"No se pudo listar administradores de {chat_id}: {ex}")
        return None
    for adm in admins:
        adm_status = str(getattr(adm.status, "value", adm.status) or "")
        if adm_status == "creator" and adm.user and not adm.user.is_bot:
            return int(adm.user.id)
    return None


async def on_bot_promoted_or_added(event: ChatMemberUpdated, bot: Optional[Bot] = None):
    """
    👑 v12 · Registro de propiedad estricto: al añadir/promover el bot SOLO se vincula al CREADOR real del chat.
    Antes se vinculaba también a quien lo añadió (aunque fuera un administrador común), lo que le daba acceso a
    las consolas y a la Mini App. Si el creador no se puede determinar, el chat queda bajo custodia del
    Arquitecto (sigue registrado para la auditoría de sincronización, pero ningún administrador gana acceso).
    """
    try:
        new_status = event.new_chat_member.status
        if new_status in ("administrator", "member"):
            chat_id = event.chat.id
            chat_type = "channel" if event.chat.type == "channel" else "supergroup"
            chat_title = event.chat.title or f"Chat {chat_id}"
            promoter_id = event.from_user.id if event.from_user else None

            creator_id = await _resolve_creator_id(bot, chat_id)
            owner_id = creator_id if creator_id is not None else CREATOR_FALLBACK_ID

            await register_user_group(
                user_id=owner_id,
                group_id=chat_id,
                group_name=chat_title,
                chat_type=chat_type
            )
            if creator_id is None:
                logger.warning(f"⚠️ [Auto-Detección]: '{chat_title}' ({chat_id}) sin creador verificable; custodia del Arquitecto.")
            else:
                logger.info(f"👑 [Auto-Detección]: '{chat_title}' ({chat_id}) vinculado a su creador {creator_id}.")
            if promoter_id is not None and promoter_id != creator_id and not is_super_admin(promoter_id):
                logger.info(f"⛔ [Auto-Detección]: {promoter_id} añadió el bot a {chat_id} pero NO es el creador; no se le vincula.")
    except Exception as e:
        logger.error(f"❌ [Error en my_chat_member]: {e}", exc_info=True)


class AdminActionTrackerMiddleware(BaseMiddleware):
    """
    v9 · Acciones de moderación de los administradores HUMANOS (ban, expulsión, silencio, ascensos…) a partir
    de las actualizaciones chat_member, sin depender del comando o la app usados. Las acciones del propio bot
    ya se registran como bloqueos del sistema (security_events). Una expulsión (ban + desbaneo inmediato) se
    registra como 'kick' y no como ban + unban.
    """
    KICK_WINDOW_S = 6.0

    def __init__(self):
        super().__init__()
        self._pending_bans: Dict[tuple, asyncio.Task] = {}

    @staticmethod
    def _status(member) -> str:
        return str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", "")) or "")

    async def _delayed_ban(self, key: tuple, group_id: int, admin_id: int, admin_name: str, target_id: int) -> None:
        try:
            await asyncio.sleep(self.KICK_WINDOW_S)
        except asyncio.CancelledError:
            return
        self._pending_bans.pop(key, None)
        await self._record(group_id, admin_id, admin_name, target_id, "ban")

    @staticmethod
    async def _record(group_id: int, admin_id: int, admin_name: str, target_id: int, action: str) -> None:
        try:
            await record_admin_mod_action(group_id, admin_id, admin_name, target_id, action)
        except Exception as ex:
            logger.debug(f"Aviso registrando acción de moderación ({group_id}): {ex}")

    def _classify(self, event: ChatMemberUpdated) -> Optional[str]:
        old_s, new_s = self._status(event.old_chat_member), self._status(event.new_chat_member)
        if new_s == "kicked":
            return "ban"
        if old_s == "kicked" and new_s in ("left", "member"):
            return "unban"
        if new_s == "restricted" and not getattr(event.new_chat_member, "can_send_messages", True):
            return "mute" if old_s != "restricted" or getattr(event.old_chat_member, "can_send_messages", True) else None
        if old_s == "restricted" and (new_s == "member" or (new_s == "restricted" and getattr(event.new_chat_member, "can_send_messages", False))):
            return "unmute" if not getattr(event.old_chat_member, "can_send_messages", True) else None
        if new_s == "administrator" and old_s in ("member", "restricted"):
            return "promote"
        if old_s == "administrator" and new_s in ("member", "restricted"):
            return "demote"
        return None

    def _track(self, event: ChatMemberUpdated) -> None:
        actor = event.from_user
        target = getattr(event.new_chat_member, "user", None)
        if not actor or actor.is_bot or not target or actor.id == target.id:
            return
        if event.chat.type not in ("group", "supergroup"):
            return
        action = self._classify(event)
        if not action:
            return
        group_id, name = event.chat.id, (actor.full_name or "")[:64]
        key = (group_id, target.id, actor.id)
        if action == "ban":
            previous = self._pending_bans.pop(key, None)
            if previous:
                previous.cancel()
            self._pending_bans[key] = _spawn(self._delayed_ban(key, group_id, actor.id, name, target.id), name="admin_ban_window")
            return
        if action == "unban":
            pending = self._pending_bans.pop(key, None)
            if pending and not pending.done():
                pending.cancel()
                action = "kick"
        _spawn(self._record(group_id, actor.id, name, target.id, action), name="admin_action")

    async def __call__(self, handler, event, data: dict):
        if isinstance(event, ChatMemberUpdated):
            # 👑 Ascensos, degradaciones o transferencias de propiedad: el rango cacheado se descarta al instante.
            with contextlib.suppress(Exception):
                member_user = getattr(event.new_chat_member, "user", None)
                if member_user is not None:
                    _ADMIN_ACCESS_CACHE.pop((int(event.chat.id), int(member_user.id)), None)
            try:
                self._track(event)
            except Exception as ex:
                logger.debug(f"Aviso clasificando chat_member: {ex}")
        return await handler(event, data)


class OwnerCallbackGuardMiddleware(BaseMiddleware):
    """
    👑 v12 · Guardia global (todo el Dispatcher, maestro y clones) para botones de consola definidos FUERA de
    handlers/user_private.py — p. ej. la factura de licencias `inv_{tier}_{chat_id}_{lang}` de payments.py.
    Exige que quien pulsa sea el dueño legítimo (creador en vivo) del chat referenciado o un Arquitecto.
    Los botones de miembros (captcha, compra de turno, propinas…) no se tocan.
    """
    GUARDED_PREFIXES = ("inv_",)
    _CHAT_ID_RE = re.compile(r"^-\d{5,20}$")

    async def __call__(self, handler, event: CallbackQuery, data: dict):
        raw = getattr(event, "data", None) or ""
        user = getattr(event, "from_user", None)
        if raw.startswith(self.GUARDED_PREFIXES) and user and not is_super_admin(user.id):
            bot = data.get("bot")
            chat_ids = {int(tok) for tok in raw.split("_") if self._CHAT_ID_RE.match(tok)}
            for chat_id in chat_ids:
                allowed = False
                try:
                    from handlers.user_private import is_legitimate_owner as _owner_check
                    allowed = await _owner_check(bot, user.id, chat_id) if bot is not None else \
                        await is_legitimate_chat_owner(user.id, chat_id)
                except Exception as ex:
                    logger.warning(f"⚠️ [Aduana] Verificación de dueño fallida para '{raw[:48]}': {ex}")
                if not allowed:
                    logger.warning(f"⛔ [Aduana] Botón de cobro '{raw[:48]}' bloqueado: user={user.id} no es dueño de {chat_id}.")
                    is_es = bool(user.language_code and user.language_code.startswith("es"))
                    with contextlib.suppress(Exception):
                        await event.answer(
                            "⛔ Acceso Denegado: esta consola está reservada única y exclusivamente para el Dueño de la comunidad o canal."
                            if is_es else
                            "⛔ Access Denied: This command center is strictly restricted to the community/channel Owner.",
                            show_alert=True
                        )
                    return None
        return await handler(event, data)


# ==========================================
# 🔄 v16 · SINCRONIZACIÓN UNIVERSAL BOT → MINI APP
# ==========================================
# La consola privada tiene decenas de ajustes (captcha, anti-flood, propinas, advertencias, MicVIP, modo nocturno…)
# repartidos en muchos handlers. En lugar de emitir a mano en cada uno, este middleware toma el estado REAL de la
# comunidad (get_settings_snapshot) antes y después de cada acción de consola y emite 'settings_updated' solo si
# algo cambió, con la lista exacta de campos. Cubre también los ajustes futuros y nunca emite en falso.
_SETTINGS_SYNC_CHAT_RE = re.compile(r"^-\d{5,20}$")
# Prefijos que ya emiten por su cuenta (evita eventos duplicados).
_SETTINGS_SELF_EMITTING = ("mlk_", "sentinelcfg_", "alset_", "toglock_")
_SETTINGS_SYNC_MAX_CHATS = 2


def _settings_flatten(value, prefix: str = "") -> dict:
    """Árbol → {ruta.hoja: valor} (las listas se comparan enteras)."""
    out = {}
    if isinstance(value, dict):
        for key, inner in value.items():
            out.update(_settings_flatten(inner, f"{prefix}{key}."))
    else:
        out[prefix.rstrip(".")] = value
    return out


async def _settings_state(chat_id: int) -> Optional[dict]:
    try:
        snapshot = await get_settings_snapshot(int(chat_id))
    except Exception as ex:
        logger.debug(f"[Sync] Estado no disponible para {chat_id}: {ex}")
        return None
    snapshot = dict(snapshot or {})
    configuration = dict(snapshot.get("configuration") or {})
    configuration.pop("meta", None)          # marcas de tiempo de auditoría: no son ajustes
    snapshot["configuration"] = configuration
    return _settings_flatten(snapshot)


def _settings_changed_fields(before: dict, after: dict) -> list:
    keys = set(before) | set(after)
    changed = sorted(k for k in keys if before.get(k) != after.get(k))
    # Nombre corto del campo (lo que ya entiende la Mini App): "configuration.aduana.captcha_enabled" → "captcha_enabled"
    short = []
    for key in changed:
        parts = key.split(".")
        if parts[0] == "configuration" and len(parts) >= 3:
            short.append("media_locks" if parts[1] == "media_locks" else parts[2])
        else:
            short.append(parts[-1])
    return sorted(set(short))


class SettingsSyncMiddleware(BaseMiddleware):
    """Callbacks y respuestas conversacionales de la consola PRIVADA: compara el estado antes/después y emite."""

    @staticmethod
    def _chat_ids_from_callback(event: CallbackQuery) -> list:
        raw = getattr(event, "data", None) or ""
        msg = getattr(event, "message", None)
        chat = getattr(msg, "chat", None)
        if not raw or raw.startswith(_SETTINGS_SELF_EMITTING) or getattr(chat, "type", None) != "private":
            return []
        found = []
        for token in re.split(r"[_:]", raw):
            if _SETTINGS_SYNC_CHAT_RE.match(token) and int(token) not in found:
                found.append(int(token))
        return found[:_SETTINGS_SYNC_MAX_CHATS]

    @staticmethod
    def _chat_ids_from_message(event: Message, bot) -> list:
        if getattr(getattr(event, "chat", None), "type", None) != "private" or not event.from_user or bot is None:
            return []
        key = (bot.id, event.from_user.id)
        found = []
        for store in getattr(user_private, "ALL_STATE_DICTS", ()):   # se lee en cada llamada: el registro crece
            if key in store:
                chat_id = user_private._state_target_chat(store.get(key))
                if chat_id is not None and chat_id not in found:
                    found.append(chat_id)
        return found[:_SETTINGS_SYNC_MAX_CHATS]

    async def __call__(self, handler, event, data: dict):
        if isinstance(event, CallbackQuery):
            chat_ids = self._chat_ids_from_callback(event)
        elif isinstance(event, Message):
            chat_ids = self._chat_ids_from_message(event, data.get("bot"))
        else:
            chat_ids = []
        if not chat_ids:
            return await handler(event, data)

        before = {cid: await _settings_state(cid) for cid in chat_ids}
        try:
            return await handler(event, data)
        finally:
            for cid in chat_ids:
                previous = before.get(cid)
                if previous is None:
                    continue
                after = await _settings_state(cid)
                if after is None or after == previous:
                    continue
                changed = _settings_changed_fields(previous, after)
                logger.info(f"🔄 [Sync] Consola del bot cambió {cid}: {', '.join(changed)}")
                _spawn(emit_settings_updated(cid, "bot_console", changed), name=f"settings_sync:{cid}")
                if any(f.startswith(("clone_", "sentinel_")) for f in changed):
                    request_assets_push(cid)   # v18 · la tarjeta de la Mini App cambia al instante


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


CLONE_HEALTHY_WINDOW_S = 90.0   # un long-poll dura 15 s: sin respuesta correcta en 90 s el clon no está conectado


def _clone_health(token: str, state: str, username: Optional[str] = None, ok: bool = False) -> None:
    """v17 · Estado real del worker del clon (lo lee la supervisión de la Mini App)."""
    entry = active_clone_tasks.get(token)
    if entry is None:
        return
    entry["state"] = state
    if username:
        entry["username"] = username
    if ok:
        entry["last_ok"] = time.monotonic()


def _clone_runtime_for_tokens(tokens: list) -> dict:
    """{connected, state, username, last_ok_seconds} del clon en ejecución que coincida con alguno de los tokens."""
    for stored, plain in tokens:
        for key in (plain, stored):
            entry = active_clone_tasks.get((key or "").strip())
            if not entry:
                continue
            task = entry.get("task")
            alive = task is not None and not task.done()
            last_ok = entry.get("last_ok")
            age = (time.monotonic() - last_ok) if last_ok else None
            connected = bool(alive and entry.get("state") == "polling" and age is not None and age <= CLONE_HEALTHY_WINDOW_S)
            return {"running": alive, "connected": connected, "state": entry.get("state") or "starting",
                    "username": entry.get("username"), "last_ok_seconds": int(age) if age is not None else None}
    return {"running": False, "connected": False, "state": "stopped", "username": None, "last_ok_seconds": None}


def _sentinel_runtime(chat_id: int) -> dict:
    """{active, mode: own|master|none, connected, …} del Centinela que vigila la comunidad (assistant.active_sentinels)."""
    # v19 · Si assistant expone la instantánea con latido, se usa: tolera reconexiones breves de Pyrogram.
    snapshot = getattr(_assistant_runtime, "get_sentinel_runtime_snapshot", None)
    if callable(snapshot):
        try:
            return cast(Dict[str, Any], snapshot(int(chat_id)))
        except Exception as ex:
            logger.debug(f"[Activos] Instantánea del Centinela no disponible para {chat_id}: {ex}")
    entry = getattr(_assistant_runtime, "active_sentinels", {}).get(int(chat_id))
    if not entry:
        return {"active": False, "mode": "none", "connected": False}
    task = entry.get("task")
    alive = task is not None and not task.done()
    client = entry.get("client")
    connected = bool(getattr(client, "is_connected", False)) if client is not None else False
    mode = "own" if entry.get("user_id") else "master"
    return {"active": bool(alive and connected), "mode": mode, "connected": connected}


async def build_assets_status(chat_id: int) -> dict:
    """
    v17 · Supervisión del Bot Clon y del Centinela para la Mini App: registro (base de datos) + estado EN VIVO
    (worker de polling y cliente MTProto). Nunca incluye tokens ni sesiones.
    """
    cid = int(chat_id)
    registry_stale = False
    try:
        registry = await asyncio.wait_for(get_chat_assets_registry(cid), timeout=ASSETS_REGISTRY_TIMEOUT_S)
        _assets_registry_remember(cid, registry)
    except Exception as ex:
        # v19 · Un "database is locked" o un time-out transitorio ya no pinta «no vinculado» (ni se empuja por WS):
        # se reutiliza el último registro bueno de esta comunidad.
        logger.debug(f"[Activos] Registro no disponible para {cid}: {ex}")
        registry = _assets_registry_recall(cid)
        registry_stale = True
    clone_reg = dict(registry.get("clone") or {})
    sentinel_reg = dict(registry.get("sentinel") or {})
    clone_reg.setdefault("registered", False)
    clone_reg.setdefault("username", None)
    sentinel_reg.setdefault("registered", False)

    # v19 · Los tokens se consultan SIEMPRE (antes solo si el registro decía «registrado»): así el runtime
    # reconcilia la tarjeta aunque la fila de bot_clones esté desfasada o la lectura del registro fallara.
    try:
        tokens = await asyncio.wait_for(get_active_clone_tokens_for_group(cid), timeout=ASSETS_REGISTRY_TIMEOUT_S)
    except Exception:
        tokens = []
    clone_rt = _clone_runtime_for_tokens(tokens)
    sentinel_rt = _sentinel_runtime(cid)

    clone = {**clone_reg, **{k: v for k, v in clone_rt.items() if k != "username"},
             "username": clone_rt.get("username") or clone_reg.get("username")}
    if clone_rt.get("running") and not clone["registered"]:
        clone["registered"] = True       # un clon EN EJECUCIÓN para este grupo está vinculado de hecho
        clone["source"] = "runtime"
    sentinel = {**sentinel_reg, **sentinel_rt}
    # Centinela Maestro (MASTER_SESSION, p. ej. @Alphacentinel): protege sin fila en owner_sessions. `registered`
    # conserva su significado (sesión propia vinculada); `linked` indica que hay un Centinela operativo.
    sentinel["linked"] = bool(sentinel_reg.get("registered") or sentinel_rt.get("running"))

    clone["ui_state"] = "online" if clone.get("connected") else ("offline" if clone["registered"] else "unlinked")
    sentinel["ui_state"] = "online" if sentinel.get("active") else ("offline" if sentinel["linked"] else "unlinked")
    return {
        "assets": {"clone": clone, "sentinel": sentinel, "checked_at": datetime.now(timezone.utc).isoformat(),
                   "registry_stale": registry_stale},
        "has_active_clone": bool(clone["registered"] and clone.get("connected")),
        "has_active_sentinel": bool(sentinel.get("active")),
    }


# v19 · Último registro bueno por comunidad (acotado por TTL y tamaño: sin fugas en despliegues 24/7).
ASSETS_REGISTRY_TIMEOUT_S = 5.0
ASSETS_REGISTRY_CACHE_TTL_S = 600.0
ASSETS_REGISTRY_CACHE_MAX = 2000
_ASSETS_REGISTRY_CACHE: Dict[int, tuple] = {}     # chat_id → (registry, monotonic_ts)
_ASSETS_EMPTY_REGISTRY = {
    "clone": {"registered": False, "username": None, "status": "none", "since": None},
    "sentinel": {"registered": False, "status": "none", "last_error": None, "since": None},
}


def _assets_registry_remember(chat_id: int, registry: dict) -> None:
    if not isinstance(registry, dict):
        return
    _ASSETS_REGISTRY_CACHE.pop(chat_id, None)
    _ASSETS_REGISTRY_CACHE[chat_id] = (registry, time.monotonic())
    overflow = len(_ASSETS_REGISTRY_CACHE) - ASSETS_REGISTRY_CACHE_MAX
    if overflow > 0:
        for key in list(_ASSETS_REGISTRY_CACHE)[:overflow]:
            _ASSETS_REGISTRY_CACHE.pop(key, None)


def _assets_registry_recall(chat_id: int) -> dict:
    cached = _ASSETS_REGISTRY_CACHE.get(chat_id)
    if cached and time.monotonic() - cached[1] <= ASSETS_REGISTRY_CACHE_TTL_S:
        return cached[0]
    _ASSETS_REGISTRY_CACHE.pop(chat_id, None)
    return _ASSETS_EMPTY_REGISTRY


# ------------------------------------------------------------------
# 📡 v18 · Radar de activos: empuja 'assets_status' por WebSocket cuando cambia el estado
# ------------------------------------------------------------------
# Solo se vigilan las salas con al menos una Mini App conectada. Cada ASSETS_WATCH_INTERVAL_S se recalcula el
# estado (registro + runtime) y, si su huella cambió, se emite a la sala. Así la tarjeta pasa a «Desconectado»
# en cuanto vence la ventana de salud de 90 s, sin esperar al sondeo REST de la Mini App.
ASSETS_WATCH_INTERVAL_S = 15.0
ASSETS_WS_REQUEST_COOLDOWN_S = 2.0   # v19 · {action: 'assets'} por conexión
ASSETS_WATCH_MAX_CHATS = 2000
_ASSETS_WATCH: Dict[int, int] = {}            # chat_id → nº de conexiones WS abiertas
_ASSETS_LAST: Dict[int, tuple] = {}           # chat_id → última huella emitida


def _assets_watch_add(chat_id: int) -> None:
    cid = int(chat_id)
    if cid in _ASSETS_WATCH or len(_ASSETS_WATCH) < ASSETS_WATCH_MAX_CHATS:
        _ASSETS_WATCH[cid] = _ASSETS_WATCH.get(cid, 0) + 1


def _assets_watch_remove(chat_id: int) -> None:
    cid = int(chat_id)
    left = _ASSETS_WATCH.get(cid, 0) - 1
    if left > 0:
        _ASSETS_WATCH[cid] = left
    else:
        _ASSETS_WATCH.pop(cid, None)
        _ASSETS_LAST.pop(cid, None)


def _assets_fingerprint(status: dict) -> tuple:
    a = (status or {}).get("assets") or {}
    c, s = a.get("clone") or {}, a.get("sentinel") or {}
    return (bool(c.get("registered")), bool(c.get("connected")), bool(c.get("running")), c.get("state"), c.get("username"),
            bool(s.get("registered")), bool(s.get("active")), s.get("mode"), s.get("status"), s.get("last_error"),
            c.get("ui_state"), s.get("ui_state"), bool(s.get("linked")))


_ASSETS_PUSH_PENDING: set = set()   # salas con un recálculo ya en curso (acotado por _ASSETS_WATCH)


def request_assets_push(chat_id: int) -> None:
    """
    Recalcula YA (y emite si cambió) el estado de activos de una sala vigilada (p. ej. tras vincular en el bot, o
    cuando assistant avisa de que un Centinela arrancó, se reconectó o se retiró). Las ráfagas se agrupan.
    """
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return
    if cid not in _ASSETS_WATCH or cid in _ASSETS_PUSH_PENDING:
        return
    _ASSETS_PUSH_PENDING.add(cid)

    async def _run() -> None:
        try:
            await asyncio.sleep(0.3)   # agrupa disconnect + launch del mismo cambio en una sola emisión
            await push_assets_status(cid)
        except Exception as ex:
            logger.debug(f"[Activos] Empuje inmediato fallido ({cid}): {ex}")
        finally:
            _ASSETS_PUSH_PENDING.discard(cid)

    _spawn(_run(), name=f"assets_push:{cid}")


async def push_assets_status(chat_id: int, force: bool = False) -> bool:
    """Emite 'assets_status' a la sala si la huella cambió (o si force). Devuelve True si emitió."""
    cid = int(chat_id)
    status = await build_assets_status(cid)
    fp = _assets_fingerprint(status)
    if not force and _ASSETS_LAST.get(cid) == fp:
        return False
    _ASSETS_LAST[cid] = fp
    await emit_radar_event(cid, "assets_status", {"chat_id": str(cid), **status})
    return True


async def _ws_send_assets_status(client, chat_id: int) -> None:
    """Estado de activos para UN cliente: al conectar y cuando lo pide con {action: 'assets'} (botón 🔄)."""
    try:
        status = await asyncio.wait_for(build_assets_status(int(chat_id)), timeout=8)
        _ASSETS_LAST.setdefault(int(chat_id), _assets_fingerprint(status))
        radar_hub.send_to(client, _ws_envelope(int(chat_id), "assets_status", {"chat_id": str(chat_id), **status}))
    except Exception as ex:
        logger.debug(f"Aviso snapshot de activos WS ({chat_id}): {ex}")


async def assets_watch_loop() -> None:
    """Bucle del radar de activos (una sola tarea para todas las salas vigiladas)."""
    logger.info("📡 [Radar de Activos]: supervisión en vivo del Bot Clon y del Centinela activa.")
    while True:
        try:
            await asyncio.sleep(ASSETS_WATCH_INTERVAL_S)
            for cid in list(_ASSETS_WATCH):
                try:
                    await push_assets_status(cid, force=False)
                except Exception as ex:
                    logger.debug(f"[Radar de Activos] {cid}: {ex}")
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.warning(f"⚠️ [Radar de Activos] ciclo fallido: {ex}")


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
                _clone_health(token, state="polling", username=bot_username, ok=True)
                break
            except TelegramUnauthorizedError:
                logger.warning(f"🔒 [Clon {token_tag}] Token revocado o inválido; se detiene el clon.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(f"⚠️ [Clon {token_tag}] Error al iniciar ({ex}); reintento en {backoff:.0f}s.")
                _clone_health(token, state="retrying")
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
                _clone_health(token, state="polling", ok=True)
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
                _clone_health(token, state="retrying")
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, 30.0)
    finally:
        current = active_clone_tasks.get(token)
        if current is not None and current.get("task") is asyncio.current_task():
            active_clone_tasks.pop(token, None)
        with contextlib.suppress(Exception):
            await clone_bot.session.close()


async def _clone_bot_for_chat(chat_id: int):
    """
    v15 · Bot clon EN EJECUCIÓN de la comunidad (o None). Se busca tanto por el valor guardado como por el token
    descifrado, porque active_clone_tasks se indexa con el token con el que arrancó cada clon.
    """
    try:
        tokens = await get_active_clone_tokens_for_group(int(chat_id))
    except Exception as ex:
        logger.debug(f"[Clones] No se pudieron leer los clones de {chat_id}: {ex}")
        return None
    for stored, plain in tokens:
        for key in (plain, stored):
            entry = active_clone_tasks.get((key or "").strip())
            if entry and entry.get("bot") is not None:
                return entry["bot"]
    return None


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
            # v22 · El payload de MicVIP siempre lleva la comunidad: respaldo propio si analytics no la extrae.
            if not group_id and micvip_audit.is_micvip_payload(payment.invoice_payload):
                group_id = micvip_audit.group_from_payload(payment.invoice_payload)
            if not group_id:
                return
            payer_id = event.from_user.id if event.from_user else 0
            if micvip_audit.is_micvip_payload(payment.invoice_payload):
                # 🎟️ v22 · Registro de auditoría ANTES de la analítica: si esta falla, la compra igual queda trazada.
                try:
                    await micvip_audit.record_purchase(
                        group_id=group_id, user_id=payer_id, charge_id=payment.telegram_payment_charge_id,
                        amount=payment.total_amount, payload=payment.invoice_payload or "", currency=payment.currency,
                        username=getattr(event.from_user, "username", None),
                        full_name=getattr(event.from_user, "full_name", None),
                    )
                except Exception as audit_ex:
                    logger.error(f"❌ [MicVIP Audit] No se pudo registrar el cobro {payment.telegram_payment_charge_id}: {audit_ex}")
            # 👑 v23 · Donación → rol / etiqueta VIP automáticos (idempotente por charge_id; nunca bloquea la analítica).
            try:
                # v29 · El rol VIP es una función ULTRA PRO: en comunidades sin licencia (o caducada) no se concede.
                vip_allowed = False
                if vip_roles.is_donation_payload(payment.invoice_payload):
                    from database.database import feature_allowed
                    vip_allowed = await feature_allowed(group_id, "vip_roles")
                    if not vip_allowed:
                        logger.info(f"🔒 [VIP Roles] {group_id} sin licencia suficiente: donación sin rol VIP.")
                if vip_allowed:
                    _spawn(vip_roles.on_payment(
                        group_id=group_id, user_id=payer_id, charge_id=payment.telegram_payment_charge_id,
                        amount=payment.total_amount, payload=payment.invoice_payload or "",
                        username=getattr(event.from_user, "username", None),
                        full_name=getattr(event.from_user, "full_name", None), bot=getattr(event, "bot", None),
                    ), name="vip_role_donation")
            except Exception as vip_ex:
                logger.error(f"❌ [VIP Roles] Donación {payment.telegram_payment_charge_id} no encolada: {vip_ex}")
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


async def _enforce_single_instance(timeout_s: Optional[float] = None) -> None:
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

    # 🚦 v26 · Puerto abierto de inmediato: /health responde 200 mientras se espera el candado de instancia única (el
    #    resto de la API queda en 503 hasta mark_startup_ready). Es la MISMA tarea fastapi_server, solo que antes.
    fastapi_task = _spawn(run_fastapi_server(), name="fastapi_server")
    await asyncio.sleep(0)   # cede el loop una vez para que uvicorn empiece a enlazar el puerto

    # 0. Base de datos primero: todo lo demás depende del esquema.
    await _enforce_single_instance()
    init_db()
    _verify_sqlite_wal()
    logger.info("🛡️ [Base de Datos]: Inicializada correctamente.")
    start_analytics_flusher()
    logger.info("📈 [Analítica en Caliente]: Búfer de volcado por lotes activo.")
    _spawn(assets_watch_loop(), name="assets_watch")
    # v22 · Importa (una vez, idempotente) los cobros MicVIP anteriores a la auditoría.
    _spawn(micvip_audit.backfill_legacy(), name="micvip_backfill")
    # v19 · assistant avisa al instante cuando un Centinela arranca, se reconecta, se retira o se desconecta.
    _register_assets_listener = getattr(_assistant_runtime, "register_assets_change_listener", None)
    if callable(_register_assets_listener):
        _register_assets_listener(request_assets_push)

    if ALLOW_INSECURE_AUTH_FALLBACK:
        logger.warning("🚨 [Seguridad] ALLOW_INSECURE_AUTH_FALLBACK=1: la API acepta peticiones sin firma. NO usar en producción.")

    # 1. API + WebSockets en el mismo event loop (Railway enruta el tráfico a $PORT): el servidor ya escucha desde el
    #    inicio de main(); a partir de aquí la API completa queda habilitada (mismo punto que en v25).
    mark_startup_ready()

    master_bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    master_bot_instance = master_bot
    set_master_bot_id(master_bot.id)
    # 👑 v23 · Etiquetas VIP: primero el clon de la comunidad (si está en el grupo), luego el maestro.
    vip_roles.register_master_bot(master_bot)
    vip_roles.register_bot_resolver(_clone_bot_for_chat)
    _spawn(vip_roles.vip_sweeper_loop(), name="vip_roles_sweeper")
    # ⚖️ v24 · Apelaciones: desbaneo / enlaces con el clon de la comunidad o el maestro; barrido de enlaces y ofertas.
    ban_appeals.register_master_bot(master_bot)
    ban_appeals.register_bot_resolver(_clone_bot_for_chat)
    _spawn(ban_appeals.appeals_sweeper_loop(), name="ban_appeals_sweeper")
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
    dp.chat_member.outer_middleware.register(AdminActionTrackerMiddleware())
    # 👑 Regla de Oro: guardia de propiedad para botones de cobro de licencias (payments.py).
    dp.callback_query.outer_middleware.register(OwnerCallbackGuardMiddleware())
    # 🔄 v16 · Toda acción de la consola privada que cambie ajustes reales se refleja al instante en la Mini App.
    dp.callback_query.outer_middleware.register(SettingsSyncMiddleware())
    dp.message.outer_middleware.register(SettingsSyncMiddleware())

    # 🎯 ORDEN ESTRICTO DE ROUTERS:
    # (v25) Las apelaciones de baneo entran por payments.router (familia appeal_ en sus regex): ban_appeals ya no
    #       registra manejadores propios, así ningún pago puede procesarse por dos rutas.
    # 1. payments: captura facturas Stars, pre_checkouts y deep-links (/start tip_, sub_, vipmic_)
    dp.include_router(payments.router)
    # 1a. architect_matrix: Matriz del Arquitecto Supremo (100 % exclusiva de SUPER_ADMIN_IDS). Va ANTES de
    #     user_private para que sus entradas conversacionales no caigan en el catch-all de la consola privada.
    dp.include_router(architect_matrix.router)
    # 1b. community_metrics: /metrics · /stats (grupos) y /metrics (privado) con Dashboard en Vivo
    dp.include_router(community_metrics.router)
    # 1c. media_locks: panel paginado de la matriz multimedia (callbacks mlk_, verificados como el resto de la consola)
    dp.include_router(media_locks.router)
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

    # v15 · El Centinela publica con el bot clon de la comunidad si el maestro no puede hacerlo.
    register_clone_bot_resolver(_clone_bot_for_chat)

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

    # v13 · Worker de la Matriz: vence licencias concedidas (→ Free) y retira etiquetas MicVIP caducadas.
    _spawn(architect_matrix.start_architect_matrix_worker(master_bot), name="architect_matrix_worker")

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