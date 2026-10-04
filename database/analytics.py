"""
database/analytics.py — The Bunker OS (SQLite WAL)

Capa de analítica en caliente para la Mini App y el comando /metrics.

Fuentes:
- `group_members` (padrón local de groups.py): miembros rastreados, altas y cohortes.
- `analytics_activity_hourly` / `analytics_user_daily` / `analytics_user_profile`
  (nuevas, alimentadas por ActivityTrackerMiddleware vía un búfer en memoria con
  volcado por lotes): mensajes de hoy, DAU/WAU/MAU, desglose por tipo y actividad.
- `analytics_stars_ledger` (nueva): libro de Stars idempotente por charge_id.
- `get_chat_heatmap_matrix` / `get_top_reputation` (database.database existentes):
  mapa de calor 24/7 histórico y reputación XP/nivel.

Privacidad (Fase 2 · Retención y Purga):
- Este módulo NUNCA almacena el texto de los mensajes; solo tipo, hora y conteos.
- Los únicos datos personales son el nombre y @username de `analytics_user_profile` (para
  mostrar el ranking) y el payload crudo de facturas en `analytics_stars_ledger`.
- `prune_raw_analytics_history` los elimina por ventana de retención (24 h por defecto) y se
  ejecuta cada 48–72 h, con el último ciclo persistido en SQLite para sobrevivir a redeploys:
  ningún dato crudo supera las 72 h de antigüedad. Se conservan los identificadores
  seudónimos, conteos agregados, matrices de calor y métricas DAU/WAU/MAU.

Concurrencia:
- Nada toca SQLite en el event loop: lecturas y escrituras van por asyncio.to_thread.
- Cada hilo del pool reutiliza su propia conexión (threading.local) en modo WAL.
- Las lecturas se agrupan en UNA transacción diferida (snapshot WAL consistente);
  las escrituras usan BEGIN IMMEDIATE en lotes, nunca una escritura por mensaje.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

logger = logging.getLogger("bunker.analytics")

SIGNATURE = "Cloud Media Management"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


# Mismo archivo que el padrón de groups.py para que un único snapshot WAL cubra
# miembros y actividad.
ANALYTICS_DB_PATH = (os.getenv("ANALYTICS_DB") or os.getenv("MEMBER_REGISTRY_DB") or "database/bot_data.db").strip()
ANALYTICS_TZ_NAME = os.getenv("ANALYTICS_TZ", "America/Bogota").strip() or "UTC"
ANALYTICS_FLUSH_SECONDS = max(1.0, _env_float("ANALYTICS_FLUSH_SECONDS", 3.0))
ANALYTICS_CACHE_TTL = max(0.0, _env_float("ANALYTICS_CACHE_TTL", 4.0))
ANALYTICS_RETENTION_DAYS = max(45, _env_int("ANALYTICS_RETENTION_DAYS", 400))
ANALYTICS_SUBQUERY_TIMEOUT = max(0.2, _env_float("ANALYTICS_SUBQUERY_TIMEOUT", 1.5))
ANALYTICS_BUFFER_MAX_KEYS = max(1000, _env_int("ANALYTICS_BUFFER_MAX_KEYS", 50000))

DAY_SECONDS = 86400
HOUR_SECONDS = 3600


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# Fase 2 · Retención de datos crudos: ventana de conservación y periodicidad de la purga.
# Con la ventana por defecto (24 h) y el intervalo por defecto (48 h), ningún dato crudo
# supera las 72 h de antigüedad en el peor caso.
ANALYTICS_PII_RETENTION_HOURS = _clamp_float(_env_float("ANALYTICS_PII_RETENTION_HOURS", 24.0), 1.0, 72.0)
ANALYTICS_PII_PURGE_INTERVAL_HOURS = _clamp_float(_env_float("ANALYTICS_PII_PURGE_INTERVAL_HOURS", 48.0), 48.0, 72.0)
ANALYTICS_PII_CHECK_SECONDS = 15 * 60
_META_LAST_PII_PURGE = "last_pii_purge_ts"


def _resolve_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(ANALYTICS_TZ_NAME)
        except Exception as ex:
            logger.warning("⚠️ [Analytics] Zona horaria %r no disponible (%s); se usa UTC.", ANALYTICS_TZ_NAME, ex)
    return timezone.utc


ANALYTICS_TZ = _resolve_tz()

MSG_TYPES: Tuple[str, ...] = ("text", "media", "stickers_gifs", "commands", "other")
TYPE_LABELS: Dict[str, Tuple[str, str]] = {
    "text": ("Texto", "Text"),
    "media": ("Multimedia", "Media"),
    "stickers_gifs": ("Stickers/GIFs", "Stickers/GIFs"),
    "commands": ("Comandos", "Commands"),
    "other": ("Otros", "Other"),
}
DAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_ES_FULL = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DAYS_EN_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOUR_LABELS = [f"{h:02d}" for h in range(24)]


def local_now(ts: Optional[float] = None) -> datetime:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=ANALYTICS_TZ)


def _local_midnight_epoch(day_value: date) -> int:
    return int(datetime(day_value.year, day_value.month, day_value.day, tzinfo=ANALYTICS_TZ).timestamp())


# ==========================================
# 🧩 CLASIFICACIÓN DE MENSAJES
# ==========================================
_MEDIA_TYPES = {"photo", "video", "video_note", "voice", "audio", "document", "paid_media", "story"}
_STICKER_TYPES = {"sticker", "animation"}
_OTHER_CONTENT_TYPES = {
    "poll", "dice", "location", "venue", "contact", "game", "invoice", "giveaway",
    "giveaway_winners", "checklist",
}


def classify_message(message: Any) -> Optional[str]:
    """
    Devuelve el tipo analítico del mensaje o None si es un mensaje de servicio
    (altas, salidas, fijados, pagos, eventos de videollamada…), que no cuenta.
    """
    content_type = getattr(message, "content_type", None)
    content_type = str(getattr(content_type, "value", content_type) or "").lower()

    if content_type:
        if content_type == "text":
            return _classify_text(message)
        if content_type in _STICKER_TYPES:
            return "stickers_gifs"
        if content_type in _MEDIA_TYPES:
            return "media"
        if content_type in _OTHER_CONTENT_TYPES:
            return "other"
        return None

    # Respaldo por atributos (objetos que no exponen content_type).
    if getattr(message, "sticker", None) or getattr(message, "animation", None):
        return "stickers_gifs"
    if any(getattr(message, attr, None) for attr in _MEDIA_TYPES):
        return "media"
    if getattr(message, "text", None):
        return _classify_text(message)
    if any(getattr(message, attr, None) for attr in _OTHER_CONTENT_TYPES):
        return "other"
    return None


def _classify_text(message: Any) -> str:
    text = getattr(message, "text", "") or ""
    for entity in getattr(message, "entities", None) or []:
        entity_type = getattr(entity, "type", None)
        entity_type = getattr(entity_type, "value", entity_type)
        if entity_type == "bot_command" and getattr(entity, "offset", 1) == 0:
            return "commands"
    if text.startswith("/"):
        return "commands"
    return "text"


_GROUP_ID_IN_PAYLOAD = re.compile(r"(-100\d{5,})")


def extract_group_id_from_payload(payload: Optional[str]) -> int:
    """Heurística: localiza un chat_id de supergrupo/canal (-100…) en el invoice_payload."""
    if not payload:
        return 0
    match = _GROUP_ID_IN_PAYLOAD.search(payload)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


# ==========================================
# 🗄️ CONEXIONES WAL POR HILO Y ESQUEMA
# ==========================================
_tls = threading.local()
_schema_lock = threading.Lock()
_schema_ready = False

_SCHEMA_SQL = (
    # Mismo DDL que groups.py (_registry_connect) para no divergir si se crea aquí primero.
    "CREATE TABLE IF NOT EXISTS group_members ("
    "group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, "
    "PRIMARY KEY (group_id, user_id))",
    "CREATE INDEX IF NOT EXISTS idx_group_members_first_seen ON group_members (group_id, first_seen)",
    "CREATE TABLE IF NOT EXISTS analytics_activity_hourly ("
    "group_id INTEGER NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL, "
    "msg_type TEXT NOT NULL, messages INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (group_id, day, hour, msg_type)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS analytics_user_daily ("
    "group_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL, "
    "messages INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (group_id, day, user_id)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS analytics_user_profile ("
    "group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "full_name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '', "
    "last_seen INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (group_id, user_id)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS analytics_stars_ledger ("
    "charge_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0, "
    "amount INTEGER NOT NULL, payload TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_stars_ledger_group ON analytics_stars_ledger (group_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_user_profile_last_seen ON analytics_user_profile (last_seen)",
    # Metadatos operativos (p. ej. instante de la última purga, que debe sobrevivir a redeploys).
    "CREATE TABLE IF NOT EXISTS analytics_meta ("
    "meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)",
)


def _open_connection() -> sqlite3.Connection:
    directory = os.path.dirname(ANALYTICS_DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # isolation_level=None: control explícito de transacciones (BEGIN / COMMIT).
    conn = sqlite3.connect(ANALYTICS_DB_PATH, timeout=15, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA_SQL:
                conn.execute(statement)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        _schema_ready = True


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _open_connection()
        _tls.conn = conn
    elif conn.in_transaction:
        # Restos de una operación interrumpida en este hilo: limpiar antes de reutilizar.
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
    _ensure_schema(conn)
    return conn


@contextlib.contextmanager
def _read_tx():
    """Transacción de lectura limpia: snapshot WAL consistente para todas las SELECT."""
    conn = _get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("COMMIT")


@contextlib.contextmanager
def _write_tx():
    conn = _get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_analytics_schema() -> None:
    """Inicialización explícita (opcional): el esquema también se crea de forma perezosa."""
    _get_conn()


# ==========================================
# ✍️ BÚFER DE ACTIVIDAD CON VOLCADO POR LOTES
# ==========================================
def _write_activity_batch_sync(
    hourly: Dict[Tuple[int, str, int, str], int],
    users: Dict[Tuple[int, str, int], int],
    profiles: Dict[Tuple[int, int], Tuple[str, str, int]],
) -> None:
    with _write_tx() as conn:
        if hourly:
            conn.executemany(
                "INSERT INTO analytics_activity_hourly (group_id, day, hour, msg_type, messages) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id, day, hour, msg_type) DO UPDATE SET messages = messages + excluded.messages",
                [(g, d, h, t, n) for (g, d, h, t), n in hourly.items()],
            )
        if users:
            conn.executemany(
                "INSERT INTO analytics_user_daily (group_id, day, user_id, messages) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(group_id, day, user_id) DO UPDATE SET messages = messages + excluded.messages",
                [(g, d, u, n) for (g, d, u), n in users.items()],
            )
        if profiles:
            conn.executemany(
                "INSERT INTO analytics_user_profile (group_id, user_id, full_name, username, last_seen) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "full_name = excluded.full_name, username = excluded.username, "
                "last_seen = MAX(last_seen, excluded.last_seen)",
                [(g, u, name, uname, ts) for (g, u), (name, uname, ts) in profiles.items()],
            )


def _prune_old_rows_sync(cutoff_day: str) -> None:
    with _write_tx() as conn:
        conn.execute("DELETE FROM analytics_activity_hourly WHERE day < ?", (cutoff_day,))
        conn.execute("DELETE FROM analytics_user_daily WHERE day < ?", (cutoff_day,))


# ==========================================
# 🧹 FASE 2 · PURGA DE DATOS CRUDOS / PII
# ==========================================
def _read_meta_sync(key: str) -> Optional[str]:
    with _read_tx() as conn:
        row = conn.execute("SELECT meta_value FROM analytics_meta WHERE meta_key = ?", (key,)).fetchone()
    return row[0] if row else None


def _prune_raw_analytics_sync(cutoff_ts: int, now_ts: int) -> Dict[str, int]:
    """
    Una sola transacción BEGIN IMMEDIATE:
      • DELETE de perfiles (nombre real + @username) sin actividad desde `cutoff_ts`.
      • Vaciado del payload crudo de facturas Stars anteriores a `cutoff_ts`
        (se conservan charge_id, user_id, group_id, importe y fecha: trazabilidad financiera).
      • Registro del instante de la purga en analytics_meta.
    Con secure_delete activo SQLite sobrescribe con ceros las páginas liberadas y, tras el
    commit, un checkpoint TRUNCATE vacía el WAL para que las copias antiguas no persistan.
    """
    conn = _get_conn()
    conn.execute("PRAGMA secure_delete=ON")
    try:
        with _write_tx() as tx:
            profiles = tx.execute(
                "DELETE FROM analytics_user_profile WHERE last_seen < ?", (int(cutoff_ts),)
            ).rowcount
            payloads = tx.execute(
                "UPDATE analytics_stars_ledger SET payload = '' "
                "WHERE created_at < ? AND payload <> ''", (int(cutoff_ts),)
            ).rowcount
            tx.execute(
                "INSERT INTO analytics_meta (meta_key, meta_value) VALUES (?, ?) "
                "ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value",
                (_META_LAST_PII_PURGE, str(int(now_ts))),
            )
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("PRAGMA secure_delete=OFF")
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"profiles_deleted": max(0, int(profiles or 0)), "payloads_cleared": max(0, int(payloads or 0))}


async def prune_raw_analytics_history(retention_hours: Optional[float] = None) -> Dict[str, Any]:
    """
    Purga de datos crudos e identificables de la capa analítica.

    Elimina nombres reales, @usernames y payloads crudos con antigüedad superior a
    `retention_hours` (ANALYTICS_PII_RETENTION_HOURS por defecto). NO toca los identificadores
    seudónimos (user_id), los conteos agregados por hora/día/tipo, las matrices de calor ni
    las métricas de retención DAU/WAU/MAU. Se ejecuta en asyncio.to_thread dentro de
    BEGIN IMMEDIATE, sin bloquear el event loop.
    """
    hours = ANALYTICS_PII_RETENTION_HOURS if retention_hours is None else _clamp_float(float(retention_hours), 0.0, 72.0)
    now_ts = int(time.time())
    cutoff_ts = now_ts - int(hours * HOUR_SECONDS)
    started = time.perf_counter()
    result = await asyncio.to_thread(_prune_raw_analytics_sync, cutoff_ts, now_ts)
    invalidate_analytics_cache()
    result.update({
        "retention_hours": hours,
        "cutoff_ts": cutoff_ts,
        "executed_at": now_ts,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
    })
    logger.info(
        "🧹 [Analytics] Purga PII: %s perfiles eliminados, %s payloads vaciados (retención %.0f h, %.1f ms).",
        result["profiles_deleted"], result["payloads_cleared"], hours, result["duration_ms"],
    )
    return result


class AnalyticsBuffer:
    """
    Acumula contadores en memoria (solo desde el event loop, sin locks) y los vuelca
    a SQLite en lotes cada ANALYTICS_FLUSH_SECONDS. Con 1.000 mensajes/minuto se
    ejecutan ~20 transacciones/minuto en lugar de 3.000 escrituras sueltas.
    """

    def __init__(self) -> None:
        self._hourly: Dict[Tuple[int, str, int, str], int] = defaultdict(int)
        self._users: Dict[Tuple[int, str, int], int] = defaultdict(int)
        self._profiles: Dict[Tuple[int, int], Tuple[str, str, int]] = {}
        self._flush_lock: Optional[asyncio.Lock] = None
        self._task: Optional[asyncio.Task] = None
        self._urgent: Optional[asyncio.Task] = None
        self._last_prune_day: Optional[str] = None
        self._pii_next_check = 0.0
        self._pii_last_purge_ts: Optional[int] = None

    def pending(self) -> int:
        return len(self._hourly) + len(self._users)

    def record(
        self,
        group_id: int,
        user_id: int,
        msg_type: str,
        full_name: str = "",
        username: str = "",
        ts: Optional[float] = None,
    ) -> None:
        if msg_type not in MSG_TYPES:
            msg_type = "other"
        moment = local_now(ts)
        day_key = moment.date().isoformat()
        self._hourly[(int(group_id), day_key, moment.hour, msg_type)] += 1
        if user_id and user_id > 0:
            self._users[(int(group_id), day_key, int(user_id))] += 1
            self._profiles[(int(group_id), int(user_id))] = (
                (full_name or "")[:128],
                (username or "")[:64],
                int(ts if ts is not None else time.time()),
            )
        if self.pending() >= ANALYTICS_BUFFER_MAX_KEYS and (self._urgent is None or self._urgent.done()):
            with contextlib.suppress(RuntimeError):
                self._urgent = asyncio.get_running_loop().create_task(self.flush(), name="analytics_urgent_flush")

    async def flush(self) -> None:
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        async with self._flush_lock:
            if not self._hourly and not self._users and not self._profiles:
                return
            hourly, users, profiles = dict(self._hourly), dict(self._users), dict(self._profiles)
            self._hourly, self._users, self._profiles = defaultdict(int), defaultdict(int), {}
            try:
                await asyncio.to_thread(_write_activity_batch_sync, hourly, users, profiles)
            except Exception as ex:
                logger.warning("⚠️ [Analytics] Volcado fallido (%s); se reintenta en el próximo ciclo.", ex)
                if self.pending() + len(hourly) + len(users) > ANALYTICS_BUFFER_MAX_KEYS * 2:
                    logger.error("❌ [Analytics] Búfer saturado: se descarta un lote de %s claves.", len(hourly) + len(users))
                    return
                for key, value in hourly.items():
                    self._hourly[key] += value
                for key, value in users.items():
                    self._users[key] += value
                for key, value in profiles.items():
                    self._profiles.setdefault(key, value)

    async def _maybe_prune(self) -> None:
        today_key = local_now().date().isoformat()
        if self._last_prune_day == today_key:
            return
        self._last_prune_day = today_key
        cutoff = (local_now().date() - timedelta(days=ANALYTICS_RETENTION_DAYS)).isoformat()
        try:
            await asyncio.to_thread(_prune_old_rows_sync, cutoff)
        except Exception as ex:
            logger.debug("Aviso en poda analítica: %s", ex)

    async def _maybe_purge_pii(self) -> None:
        """Lanza la purga PII si pasó el intervalo (48–72 h) desde la última, persistida en SQLite."""
        now_mono = time.monotonic()
        if now_mono < self._pii_next_check:
            return
        self._pii_next_check = now_mono + ANALYTICS_PII_CHECK_SECONDS
        try:
            if self._pii_last_purge_ts is None:
                stored = await asyncio.to_thread(_read_meta_sync, _META_LAST_PII_PURGE)
                self._pii_last_purge_ts = int(stored) if stored and stored.isdigit() else 0
            due_at = self._pii_last_purge_ts + int(ANALYTICS_PII_PURGE_INTERVAL_HOURS * HOUR_SECONDS)
            if int(time.time()) < due_at:
                return
            # Vuelca el búfer antes de purgar para que la purga vea el last_seen más reciente.
            await self.flush()
            result = await prune_raw_analytics_history()
            self._pii_last_purge_ts = int(result.get("executed_at") or time.time())
        except Exception as ex:
            logger.warning("⚠️ [Analytics] Purga PII pendiente; se reintenta en el próximo chequeo: %s", ex)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(ANALYTICS_FLUSH_SECONDS)
            try:
                await self.flush()
                await self._maybe_prune()
                await self._maybe_purge_pii()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.debug("Aviso en ciclo de analítica: %s", ex)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="analytics_flusher")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.flush(), timeout=10)


analytics_buffer = AnalyticsBuffer()


def start_analytics_flusher() -> None:
    analytics_buffer.start()


async def stop_analytics_flusher() -> None:
    await analytics_buffer.stop()


# ==========================================
# ⭐ LIBRO DE STARS (IDEMPOTENTE)
# ==========================================
def _insert_stars_sync(charge_id: str, group_id: int, user_id: int, amount: int, payload: str) -> bool:
    with _write_tx() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO analytics_stars_ledger (charge_id, group_id, user_id, amount, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (charge_id, int(group_id), int(user_id or 0), int(amount), (payload or "")[:256], int(time.time())),
        )
        return cursor.rowcount > 0


async def record_stars_payment(
    group_id: int,
    user_id: int,
    amount: int,
    charge_id: str,
    payload: str = "",
    currency: str = "XTR",
) -> bool:
    """Registra un pago en Stars una sola vez por telegram_payment_charge_id."""
    if (currency or "").upper() != "XTR" or not charge_id or not group_id or int(amount or 0) <= 0:
        return False
    try:
        inserted = await asyncio.to_thread(_insert_stars_sync, str(charge_id), int(group_id), int(user_id or 0), int(amount), payload or "")
    except Exception as ex:
        logger.warning("⚠️ [Analytics] No se pudo registrar el pago Stars %s: %s", charge_id, ex)
        return False
    if inserted:
        invalidate_analytics_cache(group_id)
    return inserted


# ==========================================
# 📊 CÓMPUTO ANALÍTICO (SÍNCRONO, EN HILO)
# ==========================================
def _pct(part: float, whole: float, digits: int = 1) -> float:
    if not whole:
        return 0.0
    return round((part / whole) * 100.0, digits)


def _type_breakdown(counts: Dict[str, int]) -> dict:
    total = sum(counts.values())
    types = []
    for key in MSG_TYPES:
        count = int(counts.get(key, 0))
        types.append({
            "key": key,
            "label_es": TYPE_LABELS[key][0],
            "label_en": TYPE_LABELS[key][1],
            "count": count,
            "pct": _pct(count, total),
        })
    return {"total": total, "types": types}


def _compute_core_sync(chat_id: int, now_ts: float) -> dict:
    chat_id = int(chat_id)
    now = local_now(now_ts)
    today = now.date()
    today_key = today.isoformat()
    yesterday_key = (today - timedelta(days=1)).isoformat()
    d7_key = (today - timedelta(days=6)).isoformat()
    d28_key = (today - timedelta(days=27)).isoformat()
    d30 = today - timedelta(days=29)
    d30_key = d30.isoformat()
    day_keys = [(today - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]

    now_i = int(now_ts)
    today_start = _local_midnight_epoch(today)
    d30_start = _local_midnight_epoch(d30)

    with _read_tx() as conn:
        # --- Padrón y cohortes -------------------------------------------------
        members_tracked = conn.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (chat_id,)
        ).fetchone()[0]
        new_7d = conn.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id = ? AND first_seen >= ?",
            (chat_id, now_i - 7 * DAY_SECONDS),
        ).fetchone()[0]
        new_30d = conn.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id = ? AND first_seen >= ?",
            (chat_id, now_i - 30 * DAY_SECONDS),
        ).fetchone()[0]

        def _cohort(window_days: int) -> dict:
            window = window_days * DAY_SECONDS
            size, retained = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END), 0) "
                "FROM group_members WHERE group_id = ? AND first_seen >= ? AND first_seen < ?",
                (now_i - window, chat_id, now_i - 2 * window, now_i - window),
            ).fetchone()
            return {
                "window_days": window_days,
                "cohort_from": now_i - 2 * window,
                "cohort_to": now_i - window,
                "size": int(size or 0),
                "retained": int(retained or 0),
                "retention_pct": _pct(retained or 0, size or 0) if size else None,
            }

        cohort_7d = _cohort(7)
        cohort_30d = _cohort(30)

        new_by_day: Dict[str, int] = defaultdict(int)
        for (first_seen,) in conn.execute(
            "SELECT first_seen FROM group_members WHERE group_id = ? AND first_seen >= ?",
            (chat_id, d30_start),
        ):
            new_by_day[local_now(first_seen).date().isoformat()] += 1

        # --- Actividad agregada ------------------------------------------------
        messages_by_day = dict(conn.execute(
            "SELECT day, SUM(messages) FROM analytics_activity_hourly "
            "WHERE group_id = ? AND day >= ? GROUP BY day",
            (chat_id, d30_key),
        ).fetchall())
        dau_by_day = dict(conn.execute(
            "SELECT day, COUNT(*) FROM analytics_user_daily "
            "WHERE group_id = ? AND day >= ? GROUP BY day",
            (chat_id, d30_key),
        ).fetchall())
        wau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_user_daily WHERE group_id = ? AND day >= ?",
            (chat_id, d7_key),
        ).fetchone()[0]
        mau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_user_daily WHERE group_id = ? AND day >= ?",
            (chat_id, d30_key),
        ).fetchone()[0]
        yesterday_same_time = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM analytics_activity_hourly "
            "WHERE group_id = ? AND day = ? AND hour <= ?",
            (chat_id, yesterday_key, now.hour),
        ).fetchone()[0]

        types_30d = dict(conn.execute(
            "SELECT msg_type, SUM(messages) FROM analytics_activity_hourly "
            "WHERE group_id = ? AND day >= ? GROUP BY msg_type",
            (chat_id, d30_key),
        ).fetchall())
        types_today = dict(conn.execute(
            "SELECT msg_type, SUM(messages) FROM analytics_activity_hourly "
            "WHERE group_id = ? AND day = ? GROUP BY msg_type",
            (chat_id, today_key),
        ).fetchall())

        ledger_heat = [[0] * 24 for _ in range(7)]
        for day_key, hour, total in conn.execute(
            "SELECT day, hour, SUM(messages) FROM analytics_activity_hourly "
            "WHERE group_id = ? AND day >= ? GROUP BY day, hour",
            (chat_id, d28_key),
        ):
            try:
                weekday = date.fromisoformat(day_key).weekday()
            except ValueError:
                continue
            if 0 <= int(hour) <= 23:
                ledger_heat[weekday][int(hour)] += int(total or 0)

        most_active_rows = conn.execute(
            "SELECT d.user_id, SUM(d.messages) AS total, "
            "COALESCE(p.full_name, ''), COALESCE(p.username, '') "
            "FROM analytics_user_daily d "
            "LEFT JOIN analytics_user_profile p ON p.group_id = d.group_id AND p.user_id = d.user_id "
            "WHERE d.group_id = ? AND d.day >= ? "
            "GROUP BY d.user_id ORDER BY total DESC LIMIT 50",
            (chat_id, d30_key),
        ).fetchall()
        today_by_user = dict(conn.execute(
            "SELECT user_id, messages FROM analytics_user_daily WHERE group_id = ? AND day = ?",
            (chat_id, today_key),
        ).fetchall())

        # --- Stars -------------------------------------------------------------
        stars_total, payments_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM analytics_stars_ledger WHERE group_id = ?",
            (chat_id,),
        ).fetchone()
        stars_30d = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM analytics_stars_ledger WHERE group_id = ? AND created_at >= ?",
            (chat_id, now_i - 30 * DAY_SECONDS),
        ).fetchone()[0]
        stars_today = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM analytics_stars_ledger WHERE group_id = ? AND created_at >= ?",
            (chat_id, today_start),
        ).fetchone()[0]

    messages_today = int(messages_by_day.get(today_key, 0) or 0)
    messages_yesterday = int(messages_by_day.get(yesterday_key, 0) or 0)
    dau = int(dau_by_day.get(today_key, 0) or 0)
    delta_pct = None
    if yesterday_same_time:
        delta_pct = round(((messages_today - yesterday_same_time) / yesterday_same_time) * 100.0, 1)

    return {
        "now": now,
        "summary": {
            "members_tracked": int(members_tracked or 0),
            "dau": dau,
            "wau": int(wau or 0),
            "mau": int(mau or 0),
            "stickiness_pct": _pct(dau, mau) if mau else 0.0,
            "messages_today": messages_today,
            "messages_yesterday": messages_yesterday,
            "messages_yesterday_same_time": int(yesterday_same_time or 0),
            "messages_delta_pct": delta_pct,
            "messages_30d": int(sum(int(v or 0) for v in messages_by_day.values())),
            "stars_total": int(stars_total or 0),
            "stars_30d": int(stars_30d or 0),
            "stars_today": int(stars_today or 0),
            "payments_count": int(payments_total or 0),
        },
        "growth": {
            "new_members_7d": int(new_7d or 0),
            "new_members_30d": int(new_30d or 0),
            "cohort_7d": cohort_7d,
            "cohort_30d": cohort_30d,
            "daily": {
                "labels": day_keys,
                "new_members": [int(new_by_day.get(k, 0)) for k in day_keys],
                "messages": [int(messages_by_day.get(k, 0) or 0) for k in day_keys],
                "active_users": [int(dau_by_day.get(k, 0) or 0) for k in day_keys],
            },
        },
        "ledger_heatmap": ledger_heat,
        "types_30d": {k: int(v or 0) for k, v in types_30d.items()},
        "types_today": {k: int(v or 0) for k, v in types_today.items()},
        "most_active": [
            {"user_id": int(uid), "messages_30d": int(total or 0), "name": name or "", "username": uname or ""}
            for uid, total, name, uname in most_active_rows
        ],
        "today_by_user": {int(k): int(v or 0) for k, v in today_by_user.items()},
    }


def _user_activity_sync(chat_id: int, user_ids: List[int], now_ts: float) -> Dict[int, Tuple[int, int]]:
    """Mensajes (30d, hoy) para usuarios del ranking XP fuera del Top-50 de actividad."""
    if not user_ids:
        return {}
    today = local_now(now_ts).date()
    d30_key = (today - timedelta(days=29)).isoformat()
    today_key = today.isoformat()
    placeholders = ",".join("?" for _ in user_ids)
    result: Dict[int, Tuple[int, int]] = {}
    with _read_tx() as conn:
        for uid, total, today_total in conn.execute(
            f"SELECT user_id, SUM(messages), SUM(CASE WHEN day = ? THEN messages ELSE 0 END) "
            f"FROM analytics_user_daily WHERE group_id = ? AND day >= ? AND user_id IN ({placeholders}) "
            f"GROUP BY user_id",
            (today_key, int(chat_id), d30_key, *[int(u) for u in user_ids]),
        ):
            result[int(uid)] = (int(total or 0), int(today_total or 0))
    return result


# ==========================================
# 🔥 NORMALIZACIÓN DEL MAPA DE CALOR
# ==========================================
def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _matrix_from_existing(raw: Any) -> Optional[List[List[int]]]:
    """
    Convierte la salida de get_chat_heatmap_matrix ({"matrix": {dia: {hora: n}}})
    a una matriz 7x24 (índice 0 = lunes). Acepta claves str/int y base 0 o 1.
    """
    if not isinstance(raw, dict):
        return None
    source = raw.get("matrix")
    grid = [[0] * 24 for _ in range(7)]

    if isinstance(source, list) and len(source) == 7:
        for d_idx, row in enumerate(source):
            if isinstance(row, (list, tuple)):
                for h_idx, value in enumerate(list(row)[:24]):
                    grid[d_idx][h_idx] = _safe_int(value)
    elif isinstance(source, dict) and source:
        day_keys = [_safe_int(k) for k in source.keys()]
        base = 0 if 0 in day_keys else 1
        for day_key, hours in source.items():
            d_idx = _safe_int(day_key) - base
            if not 0 <= d_idx <= 6:
                continue
            if isinstance(hours, dict):
                iterable = hours.items()
            elif isinstance(hours, (list, tuple)):
                iterable = enumerate(hours)
            else:
                continue
            for hour_key, value in iterable:
                h_idx = _safe_int(hour_key)
                if 0 <= h_idx <= 23:
                    grid[d_idx][h_idx] += _safe_int(value)
    else:
        return None

    return grid if any(any(row) for row in grid) else None


def _build_heatmap(grid: List[List[int]], source: str) -> dict:
    total = sum(sum(row) for row in grid)
    peak_value, peak_day, peak_hour = 0, 0, 0
    for d_idx, row in enumerate(grid):
        for h_idx, value in enumerate(row):
            if value > peak_value:
                peak_value, peak_day, peak_hour = value, d_idx, h_idx
    hourly_totals = [sum(grid[d][h] for d in range(7)) for h in range(24)]
    daily_totals = [sum(row) for row in grid]
    return {
        "source": source,
        "timezone": ANALYTICS_TZ_NAME,
        "days": DAYS_ES,
        "days_en": DAYS_EN,
        "hours": HOUR_LABELS,
        "matrix": grid,
        "max": peak_value,
        "total": total,
        "hourly_totals": hourly_totals,
        "daily_totals": daily_totals,
        "peak": {
            "day_index": peak_day,
            "day_es": DAYS_ES_FULL[peak_day],
            "day_en": DAYS_EN_FULL[peak_day],
            "hour": peak_hour,
            "count": peak_value,
        } if peak_value else None,
        # ApexCharts (type: "heatmap"): una serie por día, 24 puntos x/y.
        "apex_series": [
            {"name": DAYS_ES[d], "name_en": DAYS_EN[d], "data": [{"x": HOUR_LABELS[h], "y": grid[d][h]} for h in range(24)]}
            for d in range(7)
        ],
        # Chart.js + chartjs-chart-matrix: puntos {x: hora, y: día, v: valor}.
        "chartjs": [{"x": h, "y": d, "v": grid[d][h]} for d in range(7) for h in range(24)],
    }


# ==========================================
# 🏆 CUADRO DE HONOR
# ==========================================
def _level_progress(level: int, xp: int) -> float:
    level = max(1, int(level or 1))
    prev_xp = ((level - 1) ** 2) * 100
    next_xp = (level ** 2) * 100
    span = max(1, next_xp - prev_xp)
    return round(min(100.0, max(0.0, (int(xp or 0) - prev_xp) / span * 100.0)), 1)


def _rep_user_id(item: dict) -> int:
    for key in ("user_id", "id", "uid"):
        value = _safe_int(item.get(key))
        if value:
            return value
    return 0


def _build_leaderboard(rep_list: Any, most_active: List[dict], today_by_user: Dict[int, int],
                       extra_activity: Dict[int, Tuple[int, int]]) -> List[dict]:
    activity = {row["user_id"]: row for row in most_active}
    entries: Dict[Any, dict] = {}

    for item in rep_list or []:
        if not isinstance(item, dict):
            continue
        uid = _rep_user_id(item)
        name = str(item.get("name") or item.get("full_name") or "").strip()
        key = uid or f"name:{name}"
        act = activity.get(uid)
        extra = extra_activity.get(uid, (0, 0))
        xp = _safe_int(item.get("xp"))
        level = max(1, _safe_int(item.get("level")) or 1)
        entries[key] = {
            "user_id": uid or None,
            "name": name or (act["name"] if act else "") or (f"ID {uid}" if uid else "—"),
            "username": str(item.get("username") or (act["username"] if act else "") or ""),
            "xp": xp,
            "level": level,
            "level_progress_pct": _level_progress(level, xp),
            "messages_30d": act["messages_30d"] if act else extra[0],
            "messages_today": today_by_user.get(uid, extra[1]) if uid else 0,
        }

    for uid, act in activity.items():
        if uid in entries:
            continue
        entries[uid] = {
            "user_id": uid,
            "name": act["name"] or f"ID {uid}",
            "username": act["username"],
            "xp": 0,
            "level": 1,
            "level_progress_pct": 0.0,
            "messages_30d": act["messages_30d"],
            "messages_today": today_by_user.get(uid, 0),
        }

    ranked = sorted(entries.values(), key=lambda e: (e["xp"], e["messages_30d"]), reverse=True)[:10]
    for position, entry in enumerate(ranked, start=1):
        entry["rank"] = position
    return ranked


# ==========================================
# 🚀 API ASÍNCRONA PÚBLICA
# ==========================================
_CACHE: Dict[int, Tuple[float, dict]] = {}
_INFLIGHT: Dict[int, asyncio.Future] = {}
_CACHE_MAX_ENTRIES = 2000


def invalidate_analytics_cache(chat_id: Optional[int] = None) -> None:
    if chat_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(_safe_int(chat_id), None)


def _legacy_db_function(name: str):
    try:
        import database.database as legacy_db  # importación perezosa: evita ciclos
    except Exception as ex:
        logger.debug("database.database no disponible para %s: %s", name, ex)
        return None
    return getattr(legacy_db, name, None)


async def _safe_await(func, *args, **kwargs):
    if func is None:
        return None
    try:
        return await asyncio.wait_for(func(*args, **kwargs), timeout=ANALYTICS_SUBQUERY_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.debug("Subconsulta analítica %s falló: %s", getattr(func, "__name__", func), ex)
        return None


async def _build_full_analytics(chat_id: int) -> dict:
    started = time.perf_counter()
    now_ts = time.time()

    core, reputation, legacy_heat = await asyncio.gather(
        asyncio.to_thread(_compute_core_sync, chat_id, now_ts),
        _safe_await(_legacy_db_function("get_top_reputation"), chat_id, limit=25),
        _safe_await(_legacy_db_function("get_chat_heatmap_matrix"), chat_id),
    )

    rep_list = reputation if isinstance(reputation, list) else []
    known_ids = {row["user_id"] for row in core["most_active"]}
    missing_ids = [uid for uid in (_rep_user_id(i) for i in rep_list if isinstance(i, dict)) if uid and uid not in known_ids]
    extra_activity: Dict[int, Tuple[int, int]] = {}
    if missing_ids:
        try:
            extra_activity = await asyncio.to_thread(_user_activity_sync, chat_id, missing_ids, now_ts)
        except Exception as ex:
            logger.debug("Aviso completando actividad del ranking: %s", ex)

    legacy_grid = _matrix_from_existing(legacy_heat)
    if legacy_grid is not None:
        heatmap = _build_heatmap(legacy_grid, "chat_hourly_activity")
    else:
        heatmap = _build_heatmap(core["ledger_heatmap"], "analytics_ledger_28d")

    breakdown_30d = _type_breakdown(core["types_30d"])
    breakdown_today = _type_breakdown(core["types_today"])
    now: datetime = core["now"]

    return {
        "version": 2,
        "chat_id": str(chat_id),
        "generated_at": int(now_ts),
        "generated_at_local": now.strftime("%Y-%m-%d %H:%M"),
        "timezone": ANALYTICS_TZ_NAME,
        "signature": SIGNATURE,
        "summary": core["summary"],
        "growth": core["growth"],
        "heatmap": heatmap,
        "leaderboard": _build_leaderboard(rep_list, core["most_active"], core["today_by_user"], extra_activity),
        "most_active": [
            {**row, "rank": idx, "messages_today": core["today_by_user"].get(row["user_id"], 0),
             "name": row["name"] or f"ID {row['user_id']}"}
            for idx, row in enumerate(core["most_active"][:10], start=1)
        ],
        "message_breakdown": {
            "window": "30d",
            "total": breakdown_30d["total"],
            "types": breakdown_30d["types"],
            "today": breakdown_today,
        },
        "meta": {
            "build_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "sources": {
                "reputation": reputation is not None,
                "legacy_heatmap": legacy_grid is not None,
            },
        },
    }


async def get_community_full_analytics(chat_id: int, max_age: Optional[float] = None) -> dict:
    """
    Analítica completa de la comunidad en un único dict serializable a JSON.

    - Caché por chat (ANALYTICS_CACHE_TTL, 4 s por defecto) + single-flight: N clientes
      simultáneos de la Mini App disparan UNA sola compilación.
    - `max_age=0` fuerza un recálculo (sigue compartiendo el cómputo en curso).
    - El dict devuelto es compartido: los llamadores no deben mutarlo.
    """
    chat_id = int(chat_id)
    ttl = ANALYTICS_CACHE_TTL if max_age is None else max(0.0, float(max_age))

    cached = _CACHE.get(chat_id)
    if cached and (time.monotonic() - cached[0]) <= ttl:
        return cached[1]

    pending = _INFLIGHT.get(chat_id)
    if pending is not None:
        return await asyncio.shield(pending)

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _INFLIGHT[chat_id] = future
    try:
        data = await _build_full_analytics(chat_id)
    except BaseException as ex:
        if not future.done():
            future.set_exception(ex if isinstance(ex, Exception) else RuntimeError("analytics cancelled"))
            future.exception()  # marca la excepción como consumida si nadie más espera
        raise
    else:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            _CACHE.clear()
        _CACHE[chat_id] = (time.monotonic(), data)
        if not future.done():
            future.set_result(data)
        return data
    finally:
        _INFLIGHT.pop(chat_id, None)