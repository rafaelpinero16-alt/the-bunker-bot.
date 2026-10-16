"""
database.py — The Bunker OS (Sqlite3 Async-Wrapper Pattern)
Núcleo relacional de persistencia, canales, telemetría y seguridad perimetral.
The Bunker Command OS © 2026 — Cloud Media Management

Notas de despliegue (Railway / Python 3.12):
  * El módulo no usa print(); todo pasa por `logging`, que escribe en stderr y
    es compatible con PYTHONUNBUFFERED=1.
  * La ruta de la base se puede sobrescribir con DB_PATH o DATABASE_PATH
    (por ejemplo, apuntando a un volumen persistente de Railway).
  * Cada hilo reutiliza su propia conexión SQLite en modo WAL. Las funciones
    públicas decoradas con @db_async se ejecutan vía asyncio.to_thread y su
    versión síncrona queda disponible en `fn.sync`.
"""
import asyncio
import contextlib
import functools
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import urllib.parse
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from database.vault import encrypt_secret, decrypt_secret

logger = logging.getLogger("database")

DB_PATH = os.getenv("DB_PATH") or os.getenv("DATABASE_PATH") or "database/bot_data.db"
_DB_TIMEOUT_SECONDS = 30.0

# Thread-local storage para reutilización de conexiones por hilo bajo alta concurrencia
_thread_local = threading.local()

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])


def is_super_admin(user_id) -> bool:
    try:
        return int(user_id) in SUPER_ADMIN_IDS
    except (TypeError, ValueError):
        return False


# ==========================================
# 🔌 CAPA DE CONEXIÓN (WAL + THREAD-LOCAL)
# ==========================================
def _open_connection() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    # isolation_level=None: el driver no abre transacciones implícitas; las escrituras atómicas
    # se controlan con BEGIN IMMEDIATE explícito en _write_transaction().
    conn = sqlite3.connect(DB_PATH, timeout=_DB_TIMEOUT_SECONDS, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(f"PRAGMA busy_timeout = {int(_DB_TIMEOUT_SECONDS * 1000)};")
    return conn


def _get_thread_connection() -> sqlite3.Connection:
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = _open_connection()
        _thread_local.conn = conn
        _thread_local.depth = 0
    return conn


@contextlib.contextmanager
def get_db_connection():
    """
    Genera y reutiliza conexiones SQLite por hilo optimizadas con WAL.

    Garantías:
      * Si ocurre una excepción, la transacción abierta se revierte.
      * Al salir del bloque más externo, cualquier transacción que haya quedado
        abierta (por ejemplo, un `return` anticipado tras un DELETE/INSERT sin
        commit) se confirma, de modo que el hilo nunca retiene el lock de
        escritura y no bloquea al resto de procesos/hilos.
      * Las llamadas anidadas dentro del mismo hilo comparten la conexión y solo
        el nivel más externo cierra la transacción.
    """
    conn = _get_thread_connection()
    depth = getattr(_thread_local, "depth", 0)
    if depth == 0 and conn.row_factory is not None:
        conn.row_factory = None
    _thread_local.depth = depth + 1
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            try:
                conn.rollback()
            except sqlite3.Error:
                logger.exception("❌ [DB] Falló el rollback de la transacción")
        raise
    else:
        if depth == 0 and conn.in_transaction:
            try:
                conn.commit()
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                raise
    finally:
        _thread_local.depth = depth


@contextlib.contextmanager
def _write_transaction():
    """
    Transacción de escritura con BEGIN IMMEDIATE: toma el lock de escritura antes
    de leer, evitando condiciones de carrera del tipo leer-luego-escribir entre
    hilos o procesos (bot, FastAPI y workers Pyrogram compartiendo el archivo).
    """
    with get_db_connection() as conn:
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        if conn.in_transaction:
            conn.commit()


def close_thread_connection() -> None:
    """Cierra la conexión del hilo actual (útil en hooks de apagado)."""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        return
    with contextlib.suppress(sqlite3.Error):
        if conn.in_transaction:
            conn.rollback()
        conn.close()
    _thread_local.conn = None
    _thread_local.depth = 0


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def db_async(fn):
    """Ejecuta la función SQLite síncrona en un hilo. La versión síncrona queda en `fn.sync` para usos internos."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    wrapper.sync = fn
    return wrapper


def _ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"Identificador SQL no permitido: {name!r}")
    return name


def _sql_modifier(amount, unit: str) -> str:
    """Construye un modificador de fecha SQLite seguro, p. ej. '+5 days'."""
    return f"{int(amount):+d} {_ident(unit)}"


def _table_columns(cursor, table: str) -> set:
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({_ident(table)})")}


def _ensure_columns(cursor, table: str, columns) -> None:
    existing = _table_columns(cursor, table)
    for col_name, col_def in columns:
        if col_name not in existing:
            try:
                cursor.execute(f"ALTER TABLE {_ident(table)} ADD COLUMN {_ident(col_name)} {col_def}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise


def _upsert_setting(group_id: int, column: str, value) -> None:
    column = _ident(column)
    with get_db_connection() as conn:
        conn.execute(
            f"INSERT INTO group_settings (group_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT(group_id) DO UPDATE SET {column} = excluded.{column}",
            (group_id, value),
        )
        conn.commit()


def _get_setting(group_id: int, column: str, default=0):
    column = _ident(column)
    with get_db_connection() as conn:
        try:
            row = conn.execute(f"SELECT {column} FROM group_settings WHERE group_id = ?", (group_id,)).fetchone()
        except sqlite3.OperationalError:
            return default
    return row[0] if row and row[0] is not None else default


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_positive_float(value, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _read_reputation_settings(group_id: int) -> dict:
    return {
        "enabled": _as_int(_get_setting(group_id, "reputation_enabled", 1), 1),
        "multiplier": _as_positive_float(_get_setting(group_id, "reputation_xp_multiplier", 1.0), 1.0),
    }


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with _write_transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                topic_id INTEGER,
                warnings INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0
            )
        """)

        cursor.execute("CREATE TABLE IF NOT EXISTS blacklist (word TEXT UNIQUE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approved_groups (
                group_id INTEGER PRIMARY KEY,
                tier TEXT DEFAULT 'free',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_id INTEGER PRIMARY KEY,
                autolower INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS web_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions (user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_payments (
                charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                payload TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        settings_columns = [
            ("antispam", "INTEGER DEFAULT 0"),
            ("captcha_status", "INTEGER DEFAULT 0"),
            ("captcha_mode", "INTEGER DEFAULT 1"),
            ("captcha_time", "INTEGER DEFAULT 60"),
            ("captcha_action", "TEXT DEFAULT 'kick'"),
            ("captcha_text", "TEXT"),
            ("captcha_service_del", "INTEGER DEFAULT 1"),
            ("filter_tg_links", "INTEGER DEFAULT 0"),
            ("filter_forwards", "INTEGER DEFAULT 0"),
            ("filter_quotes", "INTEGER DEFAULT 0"),
            ("filter_web_links", "INTEGER DEFAULT 0"),
            ("fwd_channels", "INTEGER DEFAULT 0"),
            ("fwd_users", "INTEGER DEFAULT 0"),
            ("fwd_groups", "INTEGER DEFAULT 0"),
            ("fwd_bots", "INTEGER DEFAULT 0"),
            ("antispam_delete", "INTEGER DEFAULT 0"),
            ("antiflood_msgs", "INTEGER DEFAULT 10"),
            ("antiflood_time", "INTEGER DEFAULT 15"),
            ("antiflood_action", "TEXT DEFAULT 'kick'"),
            ("antiflood_delete", "INTEGER DEFAULT 1"),
            ("warns_limit", "INTEGER DEFAULT 3"),
            ("warns_action", "TEXT DEFAULT 'mute'"),
            ("warn_links", "INTEGER DEFAULT 1"),
            ("warn_blacklist", "INTEGER DEFAULT 1"),
            ("warn_flood", "INTEGER DEFAULT 1"),
            ("lock_media", "INTEGER DEFAULT 0"),
            ("lock_stickers", "INTEGER DEFAULT 0"),
            ("lock_links", "INTEGER DEFAULT 0"),
            ("lock_commands", "INTEGER DEFAULT 0"),
            # v15 · Matriz granular de bloqueo multimedia (30 vectores) estilo Group Help / ChatKeeper
            ("media_locks_config", "TEXT DEFAULT '{}'"),
            ("long_video_max_seconds", "INTEGER DEFAULT 300"),
            ("mic_vip_price", "INTEGER DEFAULT 50"),
            ("mic_vip_custom_price", "INTEGER DEFAULT 50"),
            ("mic_vip_custom_tag", "TEXT DEFAULT '⚜️MIC🎙️VIP⚜️'"),
            ("free_badge_status", "INTEGER DEFAULT 0"),
            ("free_badge_title", "TEXT DEFAULT 'VIP Free 🎙️'"),
            ("vip_mic_badge_title", "TEXT DEFAULT 'Pase VIP 24h 🎙️'"),
            ("autolower_custom_text", "TEXT"),
            ("autolower_custom_media_id", "TEXT"),
            ("autolower_custom_media_type", "TEXT"),
            ("panic_active", "INTEGER DEFAULT 0"),
            ("screen_shield_status", "INTEGER DEFAULT 1"),
            ("podcast_mode_status", "INTEGER DEFAULT 0"),
            ("podcast_duck_volume", "INTEGER DEFAULT 500"),
            ("noise_shield_status", "INTEGER DEFAULT 1"),
            ("speaker_queue_price", "INTEGER DEFAULT 25"),
            ("service_msgs_mode", "INTEGER DEFAULT 1"),
            ("tips_enabled", "INTEGER DEFAULT 0"),
            ("tips_amount", "INTEGER DEFAULT 10"),
            ("tips_target_channel", "TEXT"),
            ("tips_custom_text", "TEXT"),
            ("tips_media_id", "TEXT"),
            ("tips_media_type", "TEXT"),
            ("sentinel_payload_enabled", "INTEGER DEFAULT 0"),
            ("sentinel_payload_text", "TEXT"),
            ("sentinel_payload_media_id", "TEXT"),
            ("sentinel_payload_media_type", "TEXT"),
            ("sentinel_payload_auto_delete", "INTEGER"),
            ("night_mode_status", "INTEGER DEFAULT 0"),
            ("night_mode_start", "TEXT DEFAULT '22:00'"),
            ("night_mode_end", "TEXT DEFAULT '06:00'"),
            ("night_action", "TEXT DEFAULT 'lock_universal'"),
            ("vc_enabled", "INTEGER DEFAULT 1"),
            ("purge_action", "TEXT DEFAULT 'ban'"),
            ("purge_last_free_scan", "TIMESTAMP"),
            ("purge_schedule_status", "INTEGER DEFAULT 0"),
            ("purge_schedule_time", "TEXT DEFAULT '03:00'"),
            ("purge_schedule_days", "TEXT DEFAULT '1,2,3,4,5,6,7'"),
            ("ai_guardian_status", "INTEGER DEFAULT 0"),
            ("ai_copilot_status", "INTEGER DEFAULT 0"),
            ("ai_custom_prompt", "TEXT"),
            ("vc_join_custom_text", "TEXT"),
            ("vc_join_custom_media_id", "TEXT"),
            ("vc_join_custom_media_type", "TEXT"),
            ("vc_join_btn_text", "TEXT"),
            ("vc_join_btn_url", "TEXT"),
            ("vc_join_autodel_seconds", "INTEGER DEFAULT 30"),
            ("vc_join_enabled", "INTEGER DEFAULT 1"),
            ("mic_vip_custom_text", "TEXT"),
            ("mic_vip_custom_media_id", "TEXT"),
            ("mic_vip_custom_media_type", "TEXT"),
            ("mic_vip_btn_text", "TEXT"),
            ("mic_vip_btn_url", "TEXT"),
            ("mic_vip_autodel_seconds", "INTEGER DEFAULT 30"),
            ("mic_vip_enabled", "INTEGER DEFAULT 1"),
            ("reset_notice_custom_text", "TEXT"),
            ("reset_notice_custom_media_id", "TEXT"),
            ("reset_notice_custom_media_type", "TEXT"),
            ("reset_notice_btn_text", "TEXT"),
            ("reset_notice_btn_url", "TEXT"),
            ("reset_notice_autodel_seconds", "INTEGER DEFAULT 20"),
            ("reset_notice_enabled", "INTEGER DEFAULT 1"),
            ("vc_sched_start_custom_text", "TEXT"),
            ("vc_sched_start_custom_media_id", "TEXT"),
            ("vc_sched_start_custom_media_type", "TEXT"),
            ("vc_sched_start_btn_text", "TEXT"),
            ("vc_sched_start_btn_url", "TEXT"),
            ("vc_sched_start_autodel_seconds", "INTEGER DEFAULT 0"),
            ("vc_sched_start_enabled", "INTEGER DEFAULT 1"),
            ("vc_welcome_custom_text", "TEXT"),
            ("vc_welcome_custom_media_id", "TEXT"),
            ("vc_welcome_custom_media_type", "TEXT"),
            ("vc_welcome_btn_text", "TEXT"),
            ("vc_welcome_btn_url", "TEXT"),
            ("vc_welcome_autodel_seconds", "INTEGER DEFAULT 0"),
            ("vc_welcome_enabled", "INTEGER DEFAULT 1"),
            ("warn_custom_text", "TEXT"),
            ("warn_custom_media_id", "TEXT"),
            ("warn_custom_media_type", "TEXT"),
            ("log_channel_id", "TEXT"),
            ("spam_detection_mode", "TEXT DEFAULT 'smart'"),
            ("timezone", "TEXT DEFAULT 'Bogota (UTC-05)'"),
            ("chat_language", "TEXT DEFAULT 'ES'"),
            ("active_modules_count", "INTEGER DEFAULT 11"),
            ("reputation_enabled", "INTEGER DEFAULT 1"),
            ("reputation_xp_multiplier", "REAL DEFAULT 1.0"),
            ("ai_response_mode", "TEXT DEFAULT 'mention_only'"),
            ("ai_response_chance", "INTEGER DEFAULT 15"),
            ("ai_personality_tone", "TEXT DEFAULT 'guardian'"),
            # v8.3 · Paridad Mini App ↔ bot
            ("autolower_pct", "INTEGER DEFAULT 2"),
            ("tips_presets", "TEXT DEFAULT '[15, 50, 100, 250, 500]'"),
            ("tips_custom_allowed", "INTEGER DEFAULT 1"),
            # Agradecimiento de propinas editable (PRO / ULTRA PRO) y enlace de retorno cacheado
            ("tips_thanks_text", "TEXT"),
            ("tips_return_link", "TEXT"),
            ("config_updated_at", "INTEGER"),
            ("config_updated_by", "INTEGER"),
        ]

        _ensure_columns(cursor, "group_settings", settings_columns)

        cursor.execute("CREATE TABLE IF NOT EXISTS command_usage (group_id INTEGER, command TEXT, usage_date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (group_id, command, usage_date))")
        cursor.execute("CREATE TABLE IF NOT EXISTS vip_mic_passes (user_id INTEGER, group_id INTEGER, expires_at TIMESTAMP, PRIMARY KEY (user_id, group_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vip_mic_passes ON vip_mic_passes (user_id, group_id, expires_at)")
        cursor.execute("CREATE TABLE IF NOT EXISTS user_groups (user_id INTEGER, group_id INTEGER, group_name TEXT, chat_type TEXT DEFAULT 'supergroup', PRIMARY KEY (user_id, group_id))")
        _ensure_columns(cursor, "user_groups", [("chat_type", "TEXT DEFAULT 'supergroup'")])
        cursor.execute("CREATE TABLE IF NOT EXISTS group_tip_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, target_value TEXT, is_active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(group_id, target_value))")
        _ensure_columns(cursor, "group_tip_targets", [("is_active", "INTEGER DEFAULT 1")])
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_clones (user_id INTEGER, group_id INTEGER, bot_token TEXT UNIQUE, bot_username TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, group_id))")
        cursor.execute("CREATE TABLE IF NOT EXISTS owner_sessions (user_id INTEGER, group_id INTEGER, session_string TEXT NOT NULL, phone_number TEXT, api_id INTEGER, api_hash TEXT, status TEXT DEFAULT 'active', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, group_id))")
        cursor.execute("CREATE TABLE IF NOT EXISTS vc_schedules (group_id INTEGER PRIMARY KEY, days TEXT DEFAULT '1,2,3,4,5,6,7', start_time TEXT DEFAULT '20:00', end_time TEXT DEFAULT '23:00', status INTEGER DEFAULT 0, call_active INTEGER DEFAULT 0)")
        _ensure_columns(cursor, "owner_sessions", [("last_error", "TEXT")])
        cursor.execute("CREATE TABLE IF NOT EXISTS panic_snapshots (group_id INTEGER PRIMARY KEY, lock_media INTEGER DEFAULT 0, lock_links INTEGER DEFAULT 0, lock_stickers INTEGER DEFAULT 0, captcha_status INTEGER DEFAULT 0, captcha_mode INTEGER DEFAULT 1, captcha_time INTEGER DEFAULT 60, antispam INTEGER DEFAULT 0, antispam_delete INTEGER DEFAULT 0, antiflood_msgs INTEGER DEFAULT 10, antiflood_time INTEGER DEFAULT 15, antiflood_action TEXT DEFAULT 'kick', chat_permissions_json TEXT, activated_by INTEGER, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS night_snapshots (group_id INTEGER PRIMARY KEY, lock_media INTEGER DEFAULT 0, lock_links INTEGER DEFAULT 0, lock_stickers INTEGER DEFAULT 0, lock_commands INTEGER DEFAULT 0, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS user_strikes (group_id INTEGER, user_id INTEGER, strikes INTEGER DEFAULT 0, last_strike_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_reason TEXT DEFAULT 'Regla violada', PRIMARY KEY (group_id, user_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_strikes ON user_strikes (group_id, user_id)")
        cursor.execute("CREATE TABLE IF NOT EXISTS speaker_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, user_id INTEGER, full_name TEXT, username TEXT, stars_paid INTEGER DEFAULT 0, status TEXT DEFAULT 'waiting', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_speaker_queue_group ON speaker_queue (group_id, status)")
        cursor.execute("CREATE TABLE IF NOT EXISTS group_tips (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, user_id INTEGER, stars_amount INTEGER, message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_tips ON group_tips (group_id, user_id)")
        cursor.execute("CREATE TABLE IF NOT EXISTS flagged_userbots (user_id INTEGER, group_id INTEGER, reason TEXT, flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, group_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_flagged_userbots ON flagged_userbots (group_id, user_id)")
        cursor.execute("CREATE TABLE IF NOT EXISTS channel_settings (channel_id INTEGER PRIMARY KEY, sub_price INTEGER DEFAULT 0, grace_days INTEGER DEFAULT 1, auto_kick INTEGER DEFAULT 1, notify_renewal INTEGER DEFAULT 1, custom_welcome TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS channel_plans (plan_id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL, plan_name TEXT NOT NULL, duration_days INTEGER NOT NULL, stars_price INTEGER NOT NULL, status TEXT DEFAULT 'active', promo_text TEXT, media_id TEXT, media_type TEXT, target_link TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_plans_channel ON channel_plans (channel_id, status)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gamification_announcements (
                chat_id INTEGER PRIMARY KEY,
                status INTEGER DEFAULT 0,
                interval_minutes INTEGER DEFAULT 360,
                text_es TEXT,
                text_en TEXT,
                media_id TEXT,
                media_type TEXT,
                btn_text_es TEXT,
                btn_text_en TEXT,
                btn_url TEXT,
                card_lang TEXT DEFAULT 'es',
                auto_delete_after INTEGER DEFAULT 0,
                last_sent_at TIMESTAMP,
                next_send_at TIMESTAMP,
                last_message_id INTEGER,
                fail_count INTEGER DEFAULT 0,
                created_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gami_due ON gamification_announcements (status, next_send_at)")
        _ensure_columns(cursor, "gamification_announcements", [
            ("card_lang", "TEXT DEFAULT 'es'"), ("auto_delete_after", "INTEGER DEFAULT 0"),
            ("last_message_id", "INTEGER"), ("fail_count", "INTEGER DEFAULT 0"),
            ("created_by", "INTEGER"), ("updated_at", "TIMESTAMP"),
        ])
        _ensure_columns(cursor, "channel_plans", [
            ("status", "TEXT DEFAULT 'active'"), ("promo_text", "TEXT"), ("media_id", "TEXT"), ("media_type", "TEXT"),
            ("target_link", "TEXT"), ("broadcast_chat_id", "INTEGER"), ("broadcast_interval_hours", "INTEGER"),
            ("next_broadcast_at", "TIMESTAMP"), ("broadcast_enabled", "INTEGER DEFAULT 0"),
            # v9 · Programador recurrente: el intervalo se mide desde el último envío REAL.
            ("last_broadcast_at", "TIMESTAMP"), ("broadcast_failures", "INTEGER DEFAULT 0"),
            ("broadcast_lang", "TEXT DEFAULT 'es'"),
        ])
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_plans_broadcast ON channel_plans (broadcast_enabled, next_broadcast_at)")

        # v9 · Bloqueos del sistema de seguridad (Captcha, Anti-Flood, Anti-Spam, Guardián IA, Escudo Anti-Raid)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER,
                is_bot INTEGER DEFAULT 0,
                source TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_events_group ON security_events (group_id, created_at)")

        # v9 · Rendimiento de administradores (actividad diaria + acciones de moderación)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_activity_daily (
                group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL,
                full_name TEXT, username TEXT,
                messages INTEGER DEFAULT 0, replies INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, user_id, day)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_mod_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL, admin_id INTEGER NOT NULL, admin_name TEXT,
                target_id INTEGER, action TEXT NOT NULL, created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_mod_actions ON admin_mod_actions (group_id, created_at)")

        # v9 · Informe periódico del canal de registro
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_report_config (
                group_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_hours INTEGER NOT NULL DEFAULT 24,
                metrics TEXT NOT NULL DEFAULT '[]',
                lang TEXT NOT NULL DEFAULT 'es',
                anchor_at INTEGER,
                last_sent_at INTEGER,
                retry_at INTEGER,
                failures INTEGER NOT NULL DEFAULT 0,
                updated_by INTEGER,
                updated_at INTEGER
            )
        """)

        # v9 · Escudo Anti-Raid / Anti-Reportes (persistido: sobrevive a reinicios)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raid_shield_state (
                group_id INTEGER PRIMARY KEY,
                active_until INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                joins_in_window INTEGER DEFAULT 0,
                triggered_at INTEGER,
                triggers_total INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS channel_subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, plan_id INTEGER, stars_paid INTEGER NOT NULL, invite_link TEXT, subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP NOT NULL, status TEXT DEFAULT 'active', last_warned_at TIMESTAMP, FOREIGN KEY(plan_id) REFERENCES channel_plans(plan_id), UNIQUE(channel_id, user_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_subs_audit ON channel_subscriptions (status, expires_at)")
        cursor.execute("CREATE TABLE IF NOT EXISTS channel_owner_registry (user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, title TEXT, updated_at INTEGER, PRIMARY KEY (user_id, channel_id))")
        cursor.execute("CREATE TABLE IF NOT EXISTS conversation_state_store (bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT, updated_at INTEGER, PRIMARY KEY (bot_id, user_id, kind))")
        cursor.execute("CREATE TABLE IF NOT EXISTS chat_user_activity (group_id INTEGER, user_id INTEGER, full_name TEXT, username TEXT, message_count INTEGER DEFAULT 0, reply_count INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, user_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_user_activity ON chat_user_activity (group_id, message_count DESC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS chat_monthly_metrics (group_id INTEGER, month_key TEXT, total_messages INTEGER DEFAULT 0, total_users INTEGER DEFAULT 0, PRIMARY KEY (group_id, month_key))")
        cursor.execute("CREATE TABLE IF NOT EXISTS chat_user_reputation (group_id INTEGER, user_id INTEGER, full_name TEXT, username TEXT, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, last_xp_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, user_id))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reputation_xp ON chat_user_reputation (group_id, xp DESC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS chat_hourly_activity (group_id INTEGER, day_of_week INTEGER, hour_of_day INTEGER, message_count INTEGER DEFAULT 0, PRIMARY KEY (group_id, day_of_week, hour_of_day))")
        cursor.execute("CREATE TABLE IF NOT EXISTS ai_chat_context (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_context_chat ON ai_chat_context (chat_id, created_at)")

        # v13 · The Bunker Architect Command Matrix (delegados, licencias concedidas, premios y auditoría)
        _init_architect_matrix_schema(cursor)
        conn.commit()

    init_default_blacklist()


def init_default_blacklist():
    banned_words = [
        "extasis", "cp", "c.p", "c-p", "cepe", "cheese", "pizza", "cheese pizza", "cheesepizza",
        "k9", "k-9", "zoo", "z00", "beast", "bestialismo", "zoofilia", "incest", "incesto",
        "tabu", "taboo", "tab00", "rape", "r4pe", "violacion", "violation", "gore", "g0re",
        "snuff", "necro", "murder", "matar", "asesinar", "sangre", "blood", "tortura", "torture",
        "stab", "kill", "nigger", "n1gger", "slave", "hitler", "nazi", "pedofilia", "pedophilia",
        "pedophile", "pedo", "p.e.d.o", "p3do", "p3d0", "paedo"
    ]
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", [(w.lower().strip(),) for w in banned_words])
        conn.commit()


@db_async
def get_or_create_user(user_id: int, username: str, full_name: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT topic_id, warnings, is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1], row[2]
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, topic_id, warnings, is_banned) VALUES (?, ?, ?, NULL, 0, 0)",
            (user_id, username, full_name)
        )
        conn.commit()
        cursor.execute("SELECT topic_id, warnings, is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1] or 0, row[2] or 0
        return None, 0, 0


@db_async
def update_user_topic(user_id: int, topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET topic_id = ? WHERE user_id = ?", (topic_id, user_id))
        conn.commit()


@db_async
def get_user_by_topic(topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE topic_id = ?", (topic_id,))
        row = cursor.fetchone()
        return row[0] if row else None


@db_async
def add_warning(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = COALESCE(warnings, 0) + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0


@db_async
def reset_warnings(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = 0 WHERE user_id = ?", (user_id,))
        conn.commit()


@db_async
def ban_user(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


@db_async
def get_blacklist():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM blacklist")
        return [row[0] for row in cursor.fetchall()]


@db_async
def add_to_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        clean = (word or "").lower().strip()
        if not clean:
            return
        cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (clean,))
        conn.commit()


@db_async
def remove_from_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE word = ?", ((word or "").lower().strip(),))
        conn.commit()


@db_async
def register_user_group(user_id: int, group_id: int, group_name: str, chat_type: str = "supergroup"):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_groups (user_id, group_id, group_name, chat_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                group_name = excluded.group_name,
                chat_type = excluded.chat_type
        """, (user_id, group_id, group_name, chat_type))
        conn.commit()


@db_async
def get_user_groups(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups
            WHERE user_id = ? AND (chat_type = 'supergroup' OR chat_type = 'group' OR chat_type IS NULL)
        """, (user_id,))
        return cursor.fetchall()


@db_async
def get_user_channels(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups
            WHERE user_id = ? AND chat_type = 'channel'
        """, (user_id,))
        return cursor.fetchall()


@db_async
def set_autolower_status(group_id: int, status: int):
    _upsert_setting(group_id, "autolower", status)


@db_async
def get_autolower_status(group_id: int) -> int:
    return _get_setting(group_id, "autolower", 1)


@db_async
def set_antispam_status(group_id: int, status: int):
    _upsert_setting(group_id, "antispam", status)


@db_async
def get_antispam_status(group_id: int) -> int:
    return _get_setting(group_id, "antispam", 0)


@db_async
def set_captcha_status(group_id: int, status: int):
    _upsert_setting(group_id, "captcha_status", status)


@db_async
def get_captcha_status(group_id: int) -> int:
    return _get_setting(group_id, "captcha_status", 0)


@db_async
def get_captcha_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT captcha_status, captcha_mode, captcha_time, captcha_action, captcha_text, captcha_service_del
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "status": row[0] if row[0] is not None else 0,
                    "mode": row[1] if row[1] is not None else 1,
                    "time": row[2] if row[2] is not None else 60,
                    "action": row[3] if row[3] is not None else "kick",
                    "text": row[4] if row[4] is not None else "",
                    "service_del": row[5] if row[5] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {"status": 0, "mode": 1, "time": 60, "action": "kick", "text": "", "service_del": 1}


@db_async
def set_captcha_config(group_id: int, field: str, value):
    valid_fields = ["captcha_mode", "captcha_time", "captcha_action", "captcha_text", "captcha_service_del"]
    if field not in valid_fields: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def get_warns_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT warns_limit, warns_action, warn_links, warn_blacklist, warn_flood
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "limit": row[0] if row[0] is not None else 3,
                    "action": row[1] if row[1] is not None else "mute",
                    "warn_links": row[2] if row[2] is not None else 1,
                    "warn_blacklist": row[3] if row[3] is not None else 1,
                    "warn_flood": row[4] if row[4] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {"limit": 3, "action": "mute", "warn_links": 1, "warn_blacklist": 1, "warn_flood": 1}


@db_async
def set_warns_config(group_id: int, field: str, value):
    valid_fields = ["warns_limit", "warns_action", "warn_links", "warn_blacklist", "warn_flood"]
    if field not in valid_fields: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def add_user_strike(group_id: int, user_id: int, reason: str = "Infracción de reglas") -> int:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_strikes (group_id, user_id, strikes, last_strike_at, last_reason)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(group_id, user_id) DO UPDATE SET
                strikes = strikes + 1,
                last_strike_at = CURRENT_TIMESTAMP,
                last_reason = excluded.last_reason
        """, (group_id, user_id, reason))
        conn.commit()
        cursor.execute("SELECT strikes FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        row = cursor.fetchone()
        return row[0] if row else 1


@db_async
def get_user_strikes(group_id: int, user_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT strikes FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        row = cursor.fetchone()
        return row[0] if row else 0


@db_async
def reset_user_strikes(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        conn.commit()


@db_async
def get_lock_status(group_id: int, lock_name: str) -> int:
    valid_locks = ["lock_media", "lock_stickers", "lock_links", "lock_commands"]
    if lock_name not in valid_locks: return 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT {lock_name} FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


@db_async
def set_lock_status(group_id: int, lock_name: str, status: int):
    valid_locks = ["lock_media", "lock_stickers", "lock_links", "lock_commands"]
    if lock_name not in valid_locks: return
    with _write_transaction() as conn:
        cursor = conn.cursor()
        # v15 · Decisión del dueño (botones heredados): la matriz granular se alinea en la misma transacción.
        if lock_name in LEGACY_LOCK_GROUPS:
            _push_legacy_lock_to_matrix(conn, group_id, lock_name, status)
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {lock_name}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {lock_name} = excluded.{lock_name}
        """, (group_id, status))


@db_async
def get_mic_vip_price(group_id: int) -> int:
    return _get_setting(group_id, "mic_vip_price", 50)


@db_async
def set_mic_vip_price(group_id: int, price: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, mic_vip_price, mic_vip_custom_price) VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                mic_vip_price = excluded.mic_vip_price,
                mic_vip_custom_price = excluded.mic_vip_custom_price
        """, (group_id, price, price))
        conn.commit()


@db_async
def get_mic_vip_custom_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT mic_vip_price, mic_vip_custom_price, mic_vip_custom_tag, mic_vip_custom_text
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                price_val = row[1] if (row[1] is not None and row[1] > 0) else (row[0] if (row[0] is not None and row[0] > 0) else 50)
                return {
                    "price": price_val,
                    "tag": row[2] if row[2] else "⚜️MIC🎙️VIP⚜️",
                    "text": row[3] if row[3] else ""
                }
        except sqlite3.OperationalError:
            pass
        return {"price": 50, "tag": "⚜️MIC🎙️VIP⚜️", "text": ""}


@db_async
def set_mic_vip_custom_config(group_id: int, field: str, value):
    valid_fields = ["mic_vip_price", "mic_vip_custom_price", "mic_vip_custom_tag", "mic_vip_custom_text"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if field in ("mic_vip_price", "mic_vip_custom_price"):
            cursor.execute("""
                INSERT INTO group_settings (group_id, mic_vip_price, mic_vip_custom_price) VALUES (?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    mic_vip_price = excluded.mic_vip_price,
                    mic_vip_custom_price = excluded.mic_vip_custom_price
            """, (group_id, value, value))
        else:
            cursor.execute(f"""
                INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
                ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
            """, (group_id, value))
        conn.commit()


@db_async
def get_free_badge_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT free_badge_status, free_badge_title FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "status": row[0] if row[0] is not None else 0,
                    "title": row[1] if row[1] else "VIP Free 🎙️"
                }
        except sqlite3.OperationalError:
            pass
        return {"status": 0, "title": "VIP Free 🎙️"}


@db_async
def set_free_badge_config(group_id: int, field: str, value):
    if field not in ["free_badge_status", "free_badge_title"]:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def get_vip_badge_title(group_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT mic_vip_custom_tag, vip_mic_badge_title FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row and row[0]:
                title = row[0]
            elif row and row[1]:
                title = row[1]
            else:
                title = "Pase VIP 24h 🎙️"
        except sqlite3.OperationalError:
            title = "Pase VIP 24h 🎙️"
    return title[:16]


@db_async
def set_vip_badge_title(group_id: int, title: str):
    clean_title = (title or "").strip()[:16]
    if not clean_title:
        clean_title = "Pase VIP 24h 🎙️"[:16]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, vip_mic_badge_title, mic_vip_custom_tag) VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                vip_mic_badge_title = excluded.vip_mic_badge_title,
                mic_vip_custom_tag = excluded.mic_vip_custom_tag
        """, (group_id, clean_title, clean_title))
        conn.commit()


@db_async
def get_speaker_price(group_id: int) -> int:
    return _get_setting(group_id, "speaker_queue_price", 25)


@db_async
def set_speaker_price(group_id: int, price: int):
    _upsert_setting(group_id, "speaker_queue_price", price)


@db_async
def add_to_speaker_queue(group_id: int, user_id: int, full_name: str, username: str, stars_paid: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO speaker_queue (group_id, user_id, full_name, username, stars_paid, status)
            VALUES (?, ?, ?, ?, ?, 'waiting')
        """, (group_id, user_id, full_name, username, stars_paid))
        conn.commit()
        return cursor.lastrowid


@db_async
def get_speaker_queue(group_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, full_name, username, stars_paid, created_at FROM speaker_queue
            WHERE group_id = ? AND status = 'waiting'
            ORDER BY stars_paid DESC, created_at ASC
        """, (group_id,))
        return cursor.fetchall()


@db_async
def get_user_speaker_position(group_id: int, user_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id FROM speaker_queue
            WHERE group_id = ? AND status = 'waiting'
            ORDER BY stars_paid DESC, created_at ASC
        """, (group_id,))
        rows = cursor.fetchall()
        for idx, (uid,) in enumerate(rows, start=1):
            if uid == user_id:
                return idx
        return 0


@db_async
def pop_next_speaker(group_id: int):
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, full_name, username, stars_paid FROM speaker_queue
            WHERE group_id = ? AND status = 'waiting'
            ORDER BY stars_paid DESC, created_at ASC LIMIT 1
        """, (group_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute("UPDATE speaker_queue SET status = 'done' WHERE id = ?", (row[0],))
        conn.commit()
        return row


@db_async
def remove_from_speaker_queue(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM speaker_queue WHERE group_id = ? AND user_id = ? AND status = 'waiting'",
            (group_id, user_id)
        )
        conn.commit()


@db_async
def clear_speaker_queue(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM speaker_queue WHERE group_id = ? AND status = 'waiting'", (group_id,))
        conn.commit()


@db_async
def get_vc_monitor_status(group_id: int) -> int:
    return _get_setting(group_id, "vc_enabled", 1)


@db_async
def set_vc_monitor_status(group_id: int, status: int):
    _upsert_setting(group_id, "vc_enabled", status)


@db_async
def create_web_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM web_sessions WHERE expires_at <= datetime('now')")
        cursor.execute("""
            INSERT INTO web_sessions (token, user_id, expires_at)
            VALUES (?, ?, datetime('now', '+5 minutes'))
        """, (token, user_id))
        conn.commit()
    return token


@db_async
def get_user_by_web_session(token: str) -> int:
    if not token:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id FROM web_sessions
            WHERE token = ? AND expires_at > datetime('now')
        """, (token,))
        row = cursor.fetchone()
        return row[0] if row else None


_RADAR_CONFIG_FIELDS = {
    "autolower_text": "autolower_custom_text",
    "autolower_media_id": "autolower_custom_media_id",
    "autolower_media_type": "autolower_custom_media_type",
    "reset_text": "reset_notice_custom_text",
    "reset_media_id": "reset_notice_custom_media_id",
    "reset_media_type": "reset_notice_custom_media_type",
}


@db_async
def get_radar_config(group_id: int) -> dict:
    cols = list(_RADAR_CONFIG_FIELDS.values())
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT {', '.join(cols)} FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            row = None

    if not row:
        return {key: None for key in _RADAR_CONFIG_FIELDS}
    return {key: row[i] for i, key in enumerate(_RADAR_CONFIG_FIELDS)}


@db_async
def set_radar_config(group_id: int, field: str, value):
    col_name = _RADAR_CONFIG_FIELDS.get(field)
    if not col_name:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {col_name}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {col_name} = excluded.{col_name}
        """, (group_id, value))
        conn.commit()


@db_async
def get_sentinel_service_messages_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT vc_join_custom_text, vc_join_custom_media_id, vc_join_custom_media_type, vc_join_btn_text, vc_join_btn_url, vc_join_autodel_seconds, vc_join_enabled,
                       mic_vip_custom_text, mic_vip_custom_media_id, mic_vip_custom_media_type, mic_vip_btn_text, mic_vip_btn_url, mic_vip_autodel_seconds, mic_vip_enabled,
                       reset_notice_custom_text, reset_notice_custom_media_id, reset_notice_custom_media_type, reset_notice_btn_text, reset_notice_btn_url, reset_notice_autodel_seconds, reset_notice_enabled,
                       vc_sched_start_custom_text, vc_sched_start_custom_media_id, vc_sched_start_custom_media_type, vc_sched_start_btn_text, vc_sched_start_btn_url, vc_sched_start_autodel_seconds, vc_sched_start_enabled,
                       vc_welcome_custom_text, vc_welcome_custom_media_id, vc_welcome_custom_media_type, vc_welcome_btn_text, vc_welcome_btn_url, vc_welcome_autodel_seconds, vc_welcome_enabled
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                # v14 · Valor efectivo ÚNICO del aviso de entrada al VC: columna vc_join_enabled (row[6]).
                # Se expone con las dos claves ("vc_enabled" heredada y "vc_join_enabled") para que ningún
                # módulo lea un valor distinto según el nombre que use.
                vc_join_effective = _as_flag(row[6], 1)
                return {
                    "vc_text": row[0], "vc_media_id": row[1], "vc_media_type": row[2], "vc_btn": row[3], "vc_btn_url": row[4], "vc_autodel": row[5] if row[5] is not None else 30,
                    "vc_enabled": vc_join_effective, "vc_join_enabled": vc_join_effective,
                    "micvip_text": row[7], "micvip_media_id": row[8], "micvip_media_type": row[9], "micvip_btn": row[10], "micvip_btn_url": row[11], "micvip_autodel": row[12] if row[12] is not None else 30, "micvip_enabled": row[13] if row[13] is not None else 1,
                    "reset_text": row[14], "reset_media_id": row[15], "reset_media_type": row[16], "reset_btn": row[17], "reset_btn_url": row[18], "reset_autodel": row[19] if row[19] is not None else 20, "reset_enabled": row[20] if row[20] is not None else 1,
                    "sched_start_text": row[21], "sched_start_media_id": row[22], "sched_start_media_type": row[23], "sched_start_btn": row[24], "sched_start_btn_url": row[25], "sched_start_autodel": row[26] if row[26] is not None else 0, "sched_enabled": row[27] if row[27] is not None else 1,
                    "vc_welcome_text": row[28], "vc_welcome_media_id": row[29], "vc_welcome_media_type": row[30], "vc_welcome_btn": row[31], "vc_welcome_btn_url": row[32], "vc_welcome_autodel": row[33] if row[33] is not None else 0, "vc_welcome_enabled": row[34] if row[34] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {
            "vc_text": None, "vc_media_id": None, "vc_media_type": None, "vc_btn": None, "vc_btn_url": None, "vc_autodel": 30, "vc_enabled": 1, "vc_join_enabled": 1,
            "micvip_text": None, "micvip_media_id": None, "micvip_media_type": None, "micvip_btn": None, "micvip_btn_url": None, "micvip_autodel": 30, "micvip_enabled": 1,
            "reset_text": None, "reset_media_id": None, "reset_media_type": None, "reset_btn": None, "reset_btn_url": None, "reset_autodel": 20, "reset_enabled": 1,
            "sched_start_text": None, "sched_start_media_id": None, "sched_start_media_type": None, "sched_start_btn": None, "sched_start_btn_url": None, "sched_start_autodel": 0, "sched_enabled": 1,
            "vc_welcome_text": None, "vc_welcome_media_id": None, "vc_welcome_media_type": None, "vc_welcome_btn": None, "vc_welcome_btn_url": None, "vc_welcome_autodel": 0, "vc_welcome_enabled": 1
        }


_SENTINEL_SERVICE_COLUMNS = (
    "vc_join_custom_text", "vc_join_custom_media_id", "vc_join_custom_media_type", "vc_join_btn_text", "vc_join_btn_url", "vc_join_autodel_seconds", "vc_join_enabled",
    "mic_vip_custom_text", "mic_vip_custom_media_id", "mic_vip_custom_media_type", "mic_vip_btn_text", "mic_vip_btn_url", "mic_vip_autodel_seconds", "mic_vip_enabled",
    "reset_notice_custom_text", "reset_notice_custom_media_id", "reset_notice_custom_media_type", "reset_notice_btn_text", "reset_notice_btn_url", "reset_notice_autodel_seconds", "reset_notice_enabled",
    "vc_sched_start_custom_text", "vc_sched_start_custom_media_id", "vc_sched_start_custom_media_type", "vc_sched_start_btn_text", "vc_sched_start_btn_url", "vc_sched_start_autodel_seconds", "vc_sched_start_enabled",
    "vc_welcome_custom_text", "vc_welcome_custom_media_id", "vc_welcome_custom_media_type", "vc_welcome_btn_text", "vc_welcome_btn_url", "vc_welcome_autodel_seconds", "vc_welcome_enabled",
)
# Interruptor del aviso de entrada al VC: las dos columnas se escriben SIEMPRE juntas (v14).
_VC_JOIN_FLAG_ALIASES = ("vc_enabled", "vc_join_enabled")
# Clave pública del getter → columna real de cada interruptor del Centinela.
SENTINEL_SERVICE_FLAG_COLUMNS = {
    "vc": "vc_join_enabled", "vc_enabled": "vc_join_enabled", "vc_join_enabled": "vc_join_enabled",
    "micvip": "mic_vip_enabled", "micvip_enabled": "mic_vip_enabled", "mic_vip_enabled": "mic_vip_enabled",
    "reset": "reset_notice_enabled", "reset_enabled": "reset_notice_enabled", "reset_notice_enabled": "reset_notice_enabled",
    "sched": "vc_sched_start_enabled", "sched_enabled": "vc_sched_start_enabled", "vc_sched_enabled": "vc_sched_start_enabled",
    "vc_sched_start_enabled": "vc_sched_start_enabled",
    "vcwelcome": "vc_welcome_enabled", "vc_welcome_enabled": "vc_welcome_enabled",
}


def _as_flag(value, default: int = 1) -> int:
    """Normaliza un interruptor guardado en SQLite (NULL → default, '0'/'1'/bool/int → 0 o 1)."""
    if value is None or value == "":
        return int(default)
    try:
        return 1 if int(value) else 0
    except (TypeError, ValueError):
        return 1 if str(value).strip().lower() in ("true", "on", "yes", "si", "sí") else 0


def _write_vc_join_flag(conn, group_id: int, value) -> None:
    """Escribe vc_join_enabled y vc_enabled en UNA sentencia (atómica dentro de la transacción del llamador)."""
    flag = _as_flag(value, 1)
    conn.execute("""
        INSERT INTO group_settings (group_id, vc_join_enabled, vc_enabled) VALUES (?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET vc_join_enabled = excluded.vc_join_enabled, vc_enabled = excluded.vc_enabled
    """, (group_id, flag, flag))


@db_async
def set_sentinel_service_message(group_id: int, field: str, value):
    # Alias heredado: la clave pública del getter es "sched_enabled", la columna real es vc_sched_start_enabled.
    if field in ("vc_sched_enabled", "sched_enabled"):
        field = "vc_sched_start_enabled"
    # v14 · Aviso de entrada al VC: "vc_enabled" y "vc_join_enabled" se actualizan juntos, de forma atómica.
    if field in _VC_JOIN_FLAG_ALIASES:
        with _write_transaction() as conn:
            _write_vc_join_flag(conn, group_id, value)
        return
    if field not in _SENTINEL_SERVICE_COLUMNS:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def toggle_sentinel_service_flag(group_id: int, key: str):
    """
    v14 · Invierte un interruptor del Centinela de forma ATÓMICA (BEGIN IMMEDIATE: leer-invertir-escribir sin
    carreras ante dobles toques o cambios simultáneos desde la Mini App). `key` acepta la clave corta del menú
    ('vc', 'micvip', 'reset', 'sched', 'vcwelcome'), la del getter o la columna. Devuelve el nuevo valor (0/1) o
    None si la clave no es un interruptor conocido. El aviso de entrada al VC escribe sus dos columnas.
    """
    column = SENTINEL_SERVICE_FLAG_COLUMNS.get(key)
    if column is None:
        return None
    with _write_transaction() as conn:
        row = conn.execute(f"SELECT {_ident(column)} FROM group_settings WHERE group_id = ?", (group_id,)).fetchone()
        new_value = 0 if _as_flag(row[0] if row else None, 1) == 1 else 1
        if column == "vc_join_enabled":
            _write_vc_join_flag(conn, group_id, new_value)
        else:
            conn.execute(f"""
                INSERT INTO group_settings (group_id, {_ident(column)}) VALUES (?, ?)
                ON CONFLICT(group_id) DO UPDATE SET {_ident(column)} = excluded.{_ident(column)}
            """, (group_id, new_value))
    return new_value


# ==========================================================================================
# 🔒 v15 · MATRIZ GRANULAR DE BLOQUEO MULTIMEDIA (estilo Group Help / ChatKeeper)
# ==========================================================================================
# Fuente de verdad: group_settings.media_locks_config (JSON {vector: {"action": ..., "delete": bool}}).
# Compatibilidad con las cerraduras heredadas (lock_media / lock_stickers / lock_links):
#   • INTENCIÓN del dueño: la matriz y las columnas heredadas se sincronizan en ambos sentidos (panel antiguo,
#     interruptor de enlaces de la Mini App ↔ matriz).
#   • PISO MÍNIMO: el Protocolo de Pánico y el modo nocturno suben esas columnas de forma TEMPORAL y luego las
#     restauran desde su instantánea. Mientras duran, cada vector de ese grupo exige al menos Warn + borrado,
#     sin tocar la matriz; al restaurarse, la configuración fina del dueño sigue intacta.
MEDIA_LOCK_KEYS = (
    # Página 1 · Multimedia y archivos
    "photo", "video", "long_video", "animation", "sticker", "voice", "audio", "video_note", "document", "media_group",
    # Página 2 · Enlaces, reenvíos, citas, botones inline y ediciones
    "masked_links", "tg_links", "forward_any", "forward_story", "external_reply", "inline_buttons", "edited_msg",
    "poll", "contact", "long_text",
    # Página 3 · Evasión avanzada
    "caps", "location", "game", "invoice", "spoiler_text", "spoiler_media", "giveaway", "via_bot", "bot_added",
    "rich_message",
)
MEDIA_LOCK_ACTIONS = ("off", "del", "warn", "kick", "mute", "ban")
MEDIA_LOCK_SEVERITY = {"off": 0, "del": 1, "warn": 2, "mute": 3, "kick": 4, "ban": 5}
LEGACY_LOCK_GROUPS = {
    "lock_media": ("photo", "video", "long_video", "animation", "voice", "audio", "video_note", "document", "media_group"),
    "lock_stickers": ("sticker",),
    "lock_links": ("masked_links", "tg_links"),
}
LEGACY_LOCK_RULE = {"action": "warn", "delete": True}   # lo que hacía una cerradura heredada: borrar + escalera
LONG_VIDEO_RANGE = (300, 600)
DEFAULT_LONG_VIDEO_SECONDS = 300
_OFF_RULE = {"action": "off", "delete": False}


def _normalize_media_rule(raw):
    """{"action", "delete"} normalizado o None. 'off' nunca borra; 'del' siempre borra. Acepta "warn" plano."""
    if isinstance(raw, str):
        raw = {"action": raw}
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action", "off") or "off").strip().lower()
    if action not in MEDIA_LOCK_ACTIONS:
        return None
    if action == "off":
        return dict(_OFF_RULE)
    if action == "del":
        return {"action": "del", "delete": True}
    delete = raw.get("delete", True)
    return {"action": action, "delete": bool(_as_flag(delete, 1)) if not isinstance(delete, bool) else delete}


def _parse_media_locks_json(raw) -> dict:
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        logger.warning("⚠️ [MediaLocks] media_locks_config corrupto; se ignora y se usan los valores heredados.")
        data = {}
    out = {}
    for key, value in (data or {}).items():
        if key in MEDIA_LOCK_KEYS:
            rule = _normalize_media_rule(value)
            if rule is not None:
                out[key] = rule
    return out


def _legacy_intent_sync(conn, group_id: int, current: dict, panic: bool, night: bool) -> dict:
    """
    Valor de las cerraduras heredadas que DECIDIÓ el dueño. Con pánico o modo nocturno activos, la columna
    está elevada temporalmente: la intención real está en la instantánea más antigua (la que se restaurará).
    """
    if not (panic or night):
        return dict(current)
    candidates = []
    for table, active in (("panic_snapshots", panic), ("night_snapshots", night)):
        if not active:
            continue
        try:
            row = conn.execute(f"SELECT lock_media, lock_stickers, lock_links, activated_at FROM {table} WHERE group_id = ?",
                               (group_id,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row:
            candidates.append((str(row[3] or ""), {"lock_media": _as_flag(row[0], 0), "lock_stickers": _as_flag(row[1], 0),
                                                   "lock_links": _as_flag(row[2], 0)}))
    if not candidates:
        return dict(current)
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _media_locks_state_sync(conn, group_id: int) -> dict:
    """Estado completo de la matriz en UNA lectura (más el snapshot solo si hay pánico / modo nocturno)."""
    row = conn.execute("""
        SELECT media_locks_config, long_video_max_seconds, lock_media, lock_stickers, lock_links,
               COALESCE(panic_active, 0), COALESCE(night_mode_status, 0)
        FROM group_settings WHERE group_id = ?
    """, (int(group_id),)).fetchone()
    explicit = _parse_media_locks_json(row[0] if row else None)
    seconds = _as_int(row[1] if row else None, DEFAULT_LONG_VIDEO_SECONDS) if row else DEFAULT_LONG_VIDEO_SECONDS
    seconds = max(LONG_VIDEO_RANGE[0], min(LONG_VIDEO_RANGE[1], seconds))
    current = {"lock_media": _as_flag(row[2], 0) if row else 0, "lock_stickers": _as_flag(row[3], 0) if row else 0,
               "lock_links": _as_flag(row[4], 0) if row else 0}
    panic, night = bool(row and row[5]), bool(row and row[6])
    intent = _legacy_intent_sync(conn, int(group_id), current, panic, night)

    rules, floor = {}, set()
    for key in MEDIA_LOCK_KEYS:
        rules[key] = dict(explicit[key]) if key in explicit else dict(_OFF_RULE)
    for column, keys in LEGACY_LOCK_GROUPS.items():
        for key in keys:
            if key not in explicit and intent[column] == 1:
                rules[key] = dict(LEGACY_LOCK_RULE)
            # Piso temporal: solo cuando un protocolo (pánico / nocturno) elevó la columna. En operación normal
            # la columna refleja la matriz y el dueño puede dejar vectores sueltos en Off sin que se le impongan.
            if current[column] == 1 and (intent[column] == 0 or panic or night):
                floor.add(key)
    return {"rules": rules, "explicit": set(explicit), "long_video_max_seconds": seconds, "floor": floor,
            "panic": panic, "night": night, "legacy_current": current, "legacy_intent": intent}


def effective_media_lock_rule(state: dict, key: str) -> dict:
    """Regla que se APLICA: la del dueño, elevada a Warn + borrado si un piso temporal (pánico/nocturno) lo exige."""
    rule = dict(state["rules"].get(key) or _OFF_RULE)
    if key in state.get("floor", ()) and MEDIA_LOCK_SEVERITY[rule["action"]] < MEDIA_LOCK_SEVERITY["warn"]:
        return dict(LEGACY_LOCK_RULE)
    if key in state.get("floor", ()):
        rule["delete"] = True
    return rule


def _write_media_rules_json(conn, group_id: int, rules: dict) -> None:
    payload = json.dumps({k: rules[k] for k in MEDIA_LOCK_KEYS if k in rules}, separators=(",", ":"), ensure_ascii=False)
    conn.execute("""
        INSERT INTO group_settings (group_id, media_locks_config) VALUES (?, ?)
        ON CONFLICT(group_id) DO UPDATE SET media_locks_config = excluded.media_locks_config
    """, (int(group_id), payload))


def _write_legacy_intent(conn, group_id: int, state: dict, rules: dict) -> None:
    """
    Recalcula las cerraduras heredadas a partir de la matriz (1 si algún vector del grupo no está en Off) y las
    escribe donde corresponde: en group_settings si no hay protocolo temporal activo, o en la instantánea que se
    restaurará si lo hay (así el piso temporal no se pierde y la intención del dueño sobrevive a la restauración).
    """
    intent = {col: (1 if any(rules[k]["action"] != "off" for k in keys) else 0) for col, keys in LEGACY_LOCK_GROUPS.items()}
    if state["panic"] or state["night"]:
        for table, active in (("panic_snapshots", state["panic"]), ("night_snapshots", state["night"])):
            if active:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(f"UPDATE {table} SET lock_media = ?, lock_stickers = ?, lock_links = ? WHERE group_id = ?",
                                 (intent["lock_media"], intent["lock_stickers"], intent["lock_links"], int(group_id)))
        return
    conn.execute("UPDATE group_settings SET lock_media = ?, lock_stickers = ?, lock_links = ? WHERE group_id = ?",
                 (intent["lock_media"], intent["lock_stickers"], intent["lock_links"], int(group_id)))


def _apply_media_rules_sync(conn, group_id: int, updates: dict) -> dict:
    """Materializa la matriz completa, aplica `updates` {vector: regla} y sincroniza las columnas heredadas."""
    conn.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (int(group_id),))
    state = _media_locks_state_sync(conn, group_id)
    rules = {k: dict(v) for k, v in state["rules"].items()}
    for key, rule in updates.items():
        rules[key] = dict(rule)
    _write_media_rules_json(conn, group_id, rules)
    _write_legacy_intent(conn, group_id, state, rules)
    return _media_locks_state_sync(conn, group_id)


def _push_legacy_lock_to_matrix(conn, group_id: int, column: str, value) -> None:
    """
    Cerradura heredada escrita por decisión del dueño (panel antiguo del bot o interruptor de la Mini App):
    1 → los vectores de su grupo en Off pasan a Warn + borrado (los que ya tenían regla propia la conservan);
    0 → todos los vectores de su grupo pasan a Off. El llamador escribe la columna en la misma transacción.
    """
    keys = LEGACY_LOCK_GROUPS.get(column)
    if not keys:
        return
    conn.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (int(group_id),))
    state = _media_locks_state_sync(conn, group_id)
    rules = {k: dict(v) for k, v in state["rules"].items()}
    on = _as_flag(value, 0) == 1
    for key in keys:
        if on and rules[key]["action"] == "off":
            rules[key] = dict(LEGACY_LOCK_RULE)
        elif not on:
            rules[key] = dict(_OFF_RULE)
    _write_media_rules_json(conn, group_id, rules)


@db_async
def get_media_locks_config(group_id: int) -> dict:
    """
    Estado de la matriz: {"rules": {vector: {"action","delete"}}, "long_video_max_seconds", "floor": set,
    "panic", "night", ...}. Usar effective_media_lock_rule(state, vector) para la regla que se aplica.
    """
    with get_db_connection() as conn:
        try:
            return _media_locks_state_sync(conn, int(group_id))
        except sqlite3.OperationalError as ex:
            logger.warning(f"⚠️ [MediaLocks] Esquema incompleto en {group_id}: {ex}")
            return {"rules": {k: dict(_OFF_RULE) for k in MEDIA_LOCK_KEYS}, "explicit": set(),
                    "long_video_max_seconds": DEFAULT_LONG_VIDEO_SECONDS, "floor": set(), "panic": False, "night": False,
                    "legacy_current": {}, "legacy_intent": {}}


@db_async
def set_media_lock_rule(group_id: int, lock_key: str, action: str, delete_flag=None) -> dict:
    """
    Fija la regla de UN vector (BEGIN IMMEDIATE). delete_flag=None conserva el borrado actual (o lo activa si el
    vector venía de Off). Devuelve el estado resultante de la matriz.
    """
    if lock_key not in MEDIA_LOCK_KEYS:
        raise ValueError(f"Vector de bloqueo desconocido: {lock_key!r}")
    action = str(action or "").strip().lower()
    if action not in MEDIA_LOCK_ACTIONS:
        raise ValueError(f"Acción no permitida: {action!r}")
    with _write_transaction() as conn:
        conn.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (int(group_id),))
        previous = _media_locks_state_sync(conn, group_id)["rules"][lock_key]
        if delete_flag is None:
            delete_flag = previous["delete"] if previous["action"] not in ("off", "del") else True
        rule = _normalize_media_rule({"action": action, "delete": bool(delete_flag)})
        return _apply_media_rules_sync(conn, group_id, {lock_key: rule})


@db_async
def toggle_media_lock_delete(group_id: int, lock_key: str) -> dict:
    """
    Botón 🗑️ de la matriz (atómico): en Off → pasa a 'del' (solo eliminar); en 'del' → vuelve a Off; en
    Warn/Kick/Mute/Ban → invierte el borrado del mensaje infractor.
    """
    if lock_key not in MEDIA_LOCK_KEYS:
        raise ValueError(f"Vector de bloqueo desconocido: {lock_key!r}")
    with _write_transaction() as conn:
        conn.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (int(group_id),))
        current = _media_locks_state_sync(conn, group_id)["rules"][lock_key]
        if current["action"] == "off":
            rule = {"action": "del", "delete": True}
        elif current["action"] == "del":
            rule = dict(_OFF_RULE)
        else:
            rule = {"action": current["action"], "delete": not current["delete"]}
        return _apply_media_rules_sync(conn, group_id, {lock_key: rule})


@db_async
def set_long_video_max_seconds(group_id: int, seconds: int) -> int:
    value = max(LONG_VIDEO_RANGE[0], min(LONG_VIDEO_RANGE[1], _as_int(seconds, DEFAULT_LONG_VIDEO_SECONDS)))
    with _write_transaction() as conn:
        conn.execute("""
            INSERT INTO group_settings (group_id, long_video_max_seconds) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET long_video_max_seconds = excluded.long_video_max_seconds
        """, (int(group_id), value))
    return value


def validate_media_locks_payload(payload) -> tuple:
    """
    Sección "media_locks" de POST /configuration: {"rules": {vector: {"action","delete"} | "warn"},
    "long_video_max_seconds": 300..600} (también acepta los vectores en plano). → (reglas, segundos|None).
    """
    if not isinstance(payload, dict):
        raise ChatConfigError({"media_locks": "se esperaba un objeto JSON."})
    rules_in = payload.get("rules") if isinstance(payload.get("rules"), dict) else {
        k: v for k, v in payload.items() if k in MEDIA_LOCK_KEYS}
    errors, rules = {}, {}
    for key, value in (rules_in or {}).items():
        if key not in MEDIA_LOCK_KEYS:
            errors[f"media_locks.{key}"] = "vector desconocido."
            continue
        rule = _normalize_media_rule(value)
        if rule is None:
            errors[f"media_locks.{key}"] = f"acción no permitida (usa {', '.join(MEDIA_LOCK_ACTIONS)})."
            continue
        rules[key] = rule
    seconds = None
    if "long_video_max_seconds" in payload:
        try:
            seconds = _coerce_int_range(payload["long_video_max_seconds"], *LONG_VIDEO_RANGE, " s")
        except ValueError as exc:
            errors["media_locks.long_video_max_seconds"] = str(exc)
    unknown = [k for k in payload if k not in ("rules", "long_video_max_seconds") and k not in MEDIA_LOCK_KEYS]
    for key in unknown:
        errors[f"media_locks.{key}"] = "campo desconocido."
    if errors:
        raise ChatConfigError(errors)
    if not rules and seconds is None:
        raise ChatConfigError({"media_locks": "no hay ninguna regla que guardar."})
    return rules, seconds


@db_async
def get_active_clone_tokens_for_group(group_id: int) -> list:
    """
    v15 · Tokens del bot clon activo de la comunidad como [(valor_guardado, token_descifrado)]. El Centinela los
    usa para publicar con el clon si el bot maestro no puede (main.py localiza la instancia en ejecución).
    """
    with get_db_connection() as conn:
        rows = conn.execute("SELECT bot_token FROM bot_clones WHERE group_id = ? AND status = 'active' "
                            "AND bot_token IS NOT NULL AND bot_token != ''", (int(group_id),)).fetchall()
    out = []
    for (stored,) in rows:
        try:
            out.append((stored, decrypt_secret(stored)))
        except Exception as ex:
            logger.debug(f"[Clones] Token no descifrable para {group_id}: {ex}")
            out.append((stored, stored))
    return out


SENTINEL_FLAG_KEYS = ("vc_enabled", "vc_join_enabled", "micvip_enabled", "reset_enabled", "sched_enabled", "vc_welcome_enabled")


@db_async
def get_settings_snapshot(chat_id: int) -> dict:
    """
    v14 · Estado vigente para la telemetría 'settings_updated' de /ws/radar: árbol de configuración (misma forma
    que GET /chat/{id}/configuration) + interruptores del Centinela. Lo usan por igual la API y el bot, para que
    la Mini App reciba siempre la misma estructura venga de donde venga el cambio.
    """
    snapshot = {}
    try:
        snapshot["configuration"] = get_chat_full_configuration.sync(int(chat_id))
    except Exception as ex:
        logger.debug(f"[Radar] Configuración no disponible para {chat_id}: {ex}")
    try:
        svc = get_sentinel_service_messages_config.sync(int(chat_id))
        snapshot["sentinel"] = {key: _as_flag(svc.get(key), 1) for key in SENTINEL_FLAG_KEYS}
    except Exception as ex:
        logger.debug(f"[Radar] Centinela no disponible para {chat_id}: {ex}")
    return snapshot


@db_async
def get_sentinel_payload_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT sentinel_payload_enabled, sentinel_payload_text, sentinel_payload_media_id,
                       sentinel_payload_media_type, sentinel_payload_auto_delete
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "enabled": row[0] if row[0] is not None else 0,
                    "text": row[1] if row[1] is not None else None,
                    "media_id": row[2] if row[2] is not None else None,
                    "media_type": row[3] if row[3] is not None else None,
                    "auto_delete_after": row[4] if row[4] is not None else None
                }
        except sqlite3.OperationalError:
            pass
        return {"enabled": 0, "text": None, "media_id": None, "media_type": None, "auto_delete_after": None}


_SENTINEL_PAYLOAD_FIELDS = {
    "sentinel_payload_enabled", "sentinel_payload_text", "sentinel_payload_media_id",
    "sentinel_payload_media_type", "sentinel_payload_auto_delete",
}


@db_async
def set_sentinel_payload_config(group_id: int, field: str, value):
    if field not in _SENTINEL_PAYLOAD_FIELDS:
        raise ValueError(f"Campo de payload no permitido: {field!r}")
    _upsert_setting(group_id, field, value)


@db_async
def get_ai_sentinel_config(group_id: int) -> dict:
    return {
        "guardian_status": _get_setting(group_id, "ai_guardian_status", 0),
        "copilot_status": _get_setting(group_id, "ai_copilot_status", 0),
        "custom_prompt": _get_setting(group_id, "ai_custom_prompt", "") or "",
        "response_mode": _get_setting(group_id, "ai_response_mode", "mention_only"),
        "response_chance": _get_setting(group_id, "ai_response_chance", 15),
        "personality_tone": _get_setting(group_id, "ai_personality_tone", "guardian")
    }


_AI_SENTINEL_FIELDS = {
    "ai_guardian_status": "ai_guardian_status",
    "ai_copilot_status": "ai_copilot_status",
    "ai_custom_prompt": "ai_custom_prompt",
    "ai_response_mode": "ai_response_mode",
    "ai_response_chance": "ai_response_chance",
    "ai_personality_tone": "ai_personality_tone",
    "guardian_status": "ai_guardian_status",
    "copilot_status": "ai_copilot_status",
    "custom_prompt": "ai_custom_prompt",
    "response_mode": "ai_response_mode",
    "response_chance": "ai_response_chance",
    "personality_tone": "ai_personality_tone",
}


@db_async
def set_ai_sentinel_config(group_id: int, field: str, value):
    column = _AI_SENTINEL_FIELDS.get(field)
    if not column:
        logger.warning("⚠️ [DB] Campo de IA no permitido: %r", field)
        return
    _upsert_setting(group_id, column, value)


_REPUTATION_FIELDS = {
    "reputation_enabled": "reputation_enabled",
    "reputation_xp_multiplier": "reputation_xp_multiplier",
    "enabled": "reputation_enabled",
    "multiplier": "reputation_xp_multiplier",
}


@db_async
def get_reputation_settings(group_id: int) -> dict:
    return _read_reputation_settings(group_id)


@db_async
def set_reputation_setting(group_id: int, field: str, value):
    column = _REPUTATION_FIELDS.get(field)
    if not column:
        logger.warning("⚠️ [DB] Campo de reputación no permitido: %r", field)
        return
    _upsert_setting(group_id, column, value)


@db_async
def save_ai_chat_context(chat_id: int, user_id: int, role: str, content: str, max_history: int = 12):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ai_chat_context (chat_id, user_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (chat_id, user_id, role, (content or "").strip()))
        cursor.execute("""
            DELETE FROM ai_chat_context
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM ai_chat_context
                WHERE chat_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
            )
        """, (chat_id, chat_id, max_history))
        conn.commit()


@db_async
def get_ai_chat_context(chat_id: int, limit: int = 8) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Toma los N mensajes MÁS RECIENTES y los devuelve en orden cronológico.
        cursor.execute("""
            SELECT role, content FROM (
                SELECT id, role, content, created_at FROM ai_chat_context
                WHERE chat_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
            ) ORDER BY created_at ASC, id ASC
        """, (chat_id, limit))
        return [{"role": r[0], "content": r[1]} for r in cursor.fetchall()]


@db_async
def clear_ai_chat_context(chat_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ai_chat_context WHERE chat_id = ?", (chat_id,))
        conn.commit()


@db_async
def get_ghost_purge_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT purge_action, purge_last_free_scan, purge_schedule_status,
                       purge_schedule_time, purge_schedule_days
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "action": row[0] if row[0] is not None else "ban",
                    "purge_action": row[0] if row[0] is not None else "ban",
                    "last_free_scan": row[1] if row[1] is not None else None,
                    "purge_last_free_scan": row[1] if row[1] is not None else None,
                    "schedule_status": row[2] if row[2] is not None else 0,
                    "purge_schedule_status": row[2] if row[2] is not None else 0,
                    "schedule_time": row[3] if row[3] is not None else "03:00",
                    "purge_schedule_time": row[3] if row[3] is not None else "03:00",
                    "schedule_days": row[4] if row[4] is not None else "1,2,3,4,5,6,7",
                    "purge_schedule_days": row[4] if row[4] is not None else "1,2,3,4,5,6,7"
                }
        except sqlite3.OperationalError:
            pass
    return {
        "action": "ban", "purge_action": "ban", "last_free_scan": None,
        "purge_last_free_scan": None, "schedule_status": 0, "purge_schedule_status": 0,
        "schedule_time": "03:00", "purge_schedule_time": "03:00",
        "schedule_days": "1,2,3,4,5,6,7", "purge_schedule_days": "1,2,3,4,5,6,7"
    }


@db_async
def set_ghost_purge_config(group_id: int, field: str, value):
    valid_fields = ["purge_action", "purge_last_free_scan", "purge_schedule_status", "purge_schedule_time", "purge_schedule_days"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def check_can_free_purge(group_id: int) -> bool:
    cfg = get_ghost_purge_config.sync(group_id)
    last_scan = cfg.get("last_free_scan")
    if not last_scan:
        return True
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT datetime('now') >= datetime(?, '+1 day')", (last_scan,))
            row = cursor.fetchone()
            return bool(row[0]) if row else True
    except Exception:
        return True


@db_async
def update_ghost_purge_scan_time(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, purge_last_free_scan) VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET purge_last_free_scan = CURRENT_TIMESTAMP
        """, (group_id,))
        conn.commit()


@db_async
def get_all_active_purge_schedules() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT group_id, purge_schedule_days, purge_schedule_time, purge_action FROM group_settings WHERE purge_schedule_status = 1")
            return cursor.fetchall()
        except sqlite3.OperationalError:
            return []


@db_async
def get_night_mode_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT night_mode_status, night_mode_start, night_mode_end, night_action
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "status": row[0] if row[0] is not None else 0,
                    "start": row[1] if row[1] else "22:00",
                    "end": row[2] if row[2] else "06:00",
                    "action": row[3] if row[3] else "lock_universal"
                }
        except sqlite3.OperationalError:
            pass
        return {"status": 0, "start": "22:00", "end": "06:00", "action": "lock_universal"}


@db_async
def set_night_mode_config(group_id: int, field: str, value):
    valid_fields = ["night_mode_status", "night_mode_start", "night_mode_end", "night_action"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def activate_universal_night_mode(group_id: int) -> bool:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(lock_media, 0), COALESCE(lock_links, 0),
                   COALESCE(lock_stickers, 0), COALESCE(lock_commands, 0)
            FROM group_settings WHERE group_id = ?
        """, (group_id,))
        prev = cursor.fetchone() or (0, 0, 0, 0)
        # Si ya existe un snapshot (modo noche ya activo), NO se sobrescribe:
        # de lo contrario se guardarían los locks nocturnos como "estado previo".
        cursor.execute("""
            INSERT INTO night_snapshots (group_id, lock_media, lock_links, lock_stickers, lock_commands, activated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO NOTHING
        """, (group_id, *prev))
        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 1, 1, 1, 1, 1)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 1, lock_media = 1, lock_links = 1, lock_stickers = 1, lock_commands = 1
        """, (group_id,))
        conn.commit()
        return True


@db_async
def deactivate_universal_night_mode(group_id: int) -> bool:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(lock_media, 0), COALESCE(lock_links, 0),
                   COALESCE(lock_stickers, 0), COALESCE(lock_commands, 0)
            FROM night_snapshots WHERE group_id = ?
        """, (group_id,))
        snap = cursor.fetchone()
        if snap is None:
            cursor.execute("SELECT night_mode_status FROM group_settings WHERE group_id = ?", (group_id,))
            status_row = cursor.fetchone()
            if not status_row or not status_row[0]:
                # Modo noche no estaba activo: no se tocan los locks configurados manualmente.
                cursor.execute("""
                    INSERT INTO group_settings (group_id, night_mode_status) VALUES (?, 0)
                    ON CONFLICT(group_id) DO UPDATE SET night_mode_status = 0
                """, (group_id,))
                conn.commit()
                return True
            snap = (0, 0, 0, 0)
        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 0, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 0, lock_media = excluded.lock_media,
                lock_links = excluded.lock_links, lock_stickers = excluded.lock_stickers, lock_commands = excluded.lock_commands
        """, (group_id, *snap))
        cursor.execute("DELETE FROM night_snapshots WHERE group_id = ?", (group_id,))
        conn.commit()
        return True


_UTC_OFFSET_RE = re.compile(r"UTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", re.IGNORECASE)


def _parse_utc_offset(tz_label):
    if not tz_label:
        return None
    match = _UTC_OFFSET_RE.search(str(tz_label))
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    if hours > 14 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def is_night_mode_time(start_str: str, end_str: str, tz_label: str = None) -> bool:
    """
    Indica si la hora actual cae dentro de la ventana [start, end].
    `tz_label` es opcional (p. ej. "Bogota (UTC-05)", el formato de la columna
    `timezone`). Sin él se usa la hora local del contenedor (UTC en Railway).
    """
    try:
        tzinfo = _parse_utc_offset(tz_label)
        now = datetime.now(tzinfo).time() if tzinfo else datetime.now().time()
        start_t = datetime.strptime(start_str.strip(), "%H:%M").time()
        end_t = datetime.strptime(end_str.strip(), "%H:%M").time()
        if start_t <= end_t:
            return start_t <= now <= end_t
        else:
            return now >= start_t or now <= end_t
    except Exception:
        return False


VALID_FILTERS = {
    "tg_links", "forwards", "quotes", "web_links",
    "fwd_channels", "fwd_users", "fwd_groups", "fwd_bots"
}


@db_async
def get_antispam_filter(group_id: int, filter_name: str) -> int:
    if filter_name not in VALID_FILTERS:
        return 0
    col_name = f"filter_{filter_name}" if filter_name in {"tg_links", "forwards", "quotes", "web_links"} else filter_name
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT {col_name} FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


@db_async
def set_antispam_filter(group_id: int, filter_name: str, status: int):
    if filter_name not in VALID_FILTERS:
        return
    col_name = f"filter_{filter_name}" if filter_name in {"tg_links", "forwards", "quotes", "web_links"} else filter_name
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {col_name}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {col_name} = excluded.{col_name}
        """, (group_id, status))
        conn.commit()


@db_async
def get_antispam_delete(group_id: int) -> int:
    return _get_setting(group_id, "antispam_delete", 0)


@db_async
def set_antispam_delete(group_id: int, status: int):
    _upsert_setting(group_id, "antispam_delete", status)


@db_async
def get_antiflood_config(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT antiflood_msgs, antiflood_time, antiflood_action, antiflood_delete FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "msgs": row[0] if row[0] is not None else 10,
                    "time": row[1] if row[1] is not None else 15,
                    "action": row[2] if row[2] is not None else "kick",
                    "delete": row[3] if row[3] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {"msgs": 10, "time": 15, "action": "kick", "delete": 1}


@db_async
def set_antiflood_config(group_id: int, field: str, value):
    valid_fields = ["antiflood_msgs", "antiflood_time", "antiflood_action", "antiflood_delete"]
    if field not in valid_fields: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def get_service_msgs_mode(group_id: int) -> int:
    return _get_setting(group_id, "service_msgs_mode", 1)


@db_async
def set_service_msgs_mode(group_id: int, status: int):
    _upsert_setting(group_id, "service_msgs_mode", status)


@db_async
def get_tips_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT tips_enabled, tips_amount, tips_target_channel,
                       tips_custom_text, tips_media_id, tips_media_type,
                       tips_presets, tips_custom_allowed,
                       tips_thanks_text, tips_return_link
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "enabled": row[0] if row[0] is not None else 0,
                    "amount": row[1] if row[1] is not None else 10,
                    "target_channel": row[2] if row[2] else "",
                    "tips_custom_text": row[3] if row[3] else None,
                    "tips_media_id": row[4] if row[4] else None,
                    "tips_media_type": row[5] if row[5] else None,
                    "presets": _parse_tip_presets(row[6]),
                    "custom_allowed": 1 if row[7] is None else int(bool(row[7])),
                    "tips_thanks_text": row[8] if row[8] else None,
                    "tips_return_link": row[9] if row[9] else None,
                }
        except sqlite3.OperationalError:
            pass
        return {
            "enabled": 0, "amount": 10, "target_channel": "",
            "tips_custom_text": None, "tips_media_id": None, "tips_media_type": None,
            "presets": list(DEFAULT_TIP_PRESETS), "custom_allowed": 1,
            "tips_thanks_text": None, "tips_return_link": None
        }


@db_async
def set_tips_config(group_id: int, field: str, value):
    valid_fields = [
        "tips_enabled", "tips_amount", "tips_target_channel",
        "tips_custom_text", "tips_media_id", "tips_media_type",
        "tips_thanks_text", "tips_return_link"
    ]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


@db_async
def record_group_tip(group_id: int, user_id: int, stars_amount: int, message: str = ""):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_tips (group_id, user_id, stars_amount, message)
            VALUES (?, ?, ?, ?)
        """, (group_id, user_id, stars_amount, message))
        conn.commit()


@db_async
def get_group_total_tips(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT SUM(stars_amount) FROM group_tips WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if (row and row[0]) else 0
        except sqlite3.OperationalError:
            return 0


@db_async
def get_group_tip_targets(group_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, target_value, is_active FROM group_tip_targets WHERE group_id = ?", (group_id,))
        return cursor.fetchall()


@db_async
def add_group_tip_target(group_id: int, target_value: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO group_tip_targets (group_id, target_value, is_active) VALUES (?, ?, 1)", (group_id, target_value))
        conn.commit()


@db_async
def toggle_group_tip_target(group_id: int, target_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE group_tip_targets SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ? AND group_id = ?", (target_id, group_id))
        conn.commit()


@db_async
def delete_group_tip_target(group_id: int, target_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM group_tip_targets WHERE id = ? AND group_id = ?", (target_id, group_id))
        conn.commit()


@db_async
def add_to_whitelist(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,))
        conn.commit()


@db_async
def remove_from_whitelist(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        conn.commit()


@db_async
def is_whitelisted(user_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None


@db_async
def approve_group(group_id: int, tier: str = "free", duration_days: int = 30):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if tier in ["pro", "ultra_pro"]:
            cursor.execute("""
                INSERT INTO approved_groups (group_id, tier, expires_at)
                VALUES (?, ?, datetime('now', ?))
                ON CONFLICT(group_id) DO UPDATE SET
                    tier = excluded.tier,
                    expires_at = excluded.expires_at
            """, (group_id, tier, _sql_modifier(duration_days, "days")))
        else:
            cursor.execute("""
                INSERT INTO approved_groups (group_id, tier, expires_at)
                VALUES (?, 'free', NULL)
                ON CONFLICT(group_id) DO UPDATE SET
                    tier = 'free',
                    expires_at = NULL
            """, (group_id,))
        conn.commit()


@db_async
def is_group_approved(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM approved_groups WHERE group_id = ?", (group_id,))
        return cursor.fetchone() is not None


@db_async
def get_group_tier(group_id: int) -> str:
    """
    Licencia VIGENTE de la comunidad = la mayor entre:
      • approved_groups (licencia pagada con Stars; caduca y vuelve a 'free' sola),
      • concesión del Arquitecto a la comunidad (architect_license_grants scope='chat'),
      • concesión del Arquitecto a cualquiera de sus dueños registrados (scope='user').
    Comunidades de Arquitectos → ULTRA PRO universal.
    """
    if is_super_admin(group_id):
        return "ultra_pro"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        owners = []
        try:
            cursor.execute("SELECT user_id FROM user_groups WHERE group_id = ?", (group_id,))
            owners = [uid for (uid,) in cursor.fetchall()]
            if any(is_super_admin(uid) for uid in owners):
                return "ultra_pro"
        except sqlite3.OperationalError:
            pass

        tier = "free"
        cursor.execute("SELECT tier, expires_at FROM approved_groups WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if row:
            tier, expires_at = row
            if expires_at:
                cursor.execute("SELECT datetime('now') > ?", (expires_at,))
                if cursor.fetchone()[0]:
                    cursor.execute("UPDATE approved_groups SET tier = 'free', expires_at = NULL WHERE group_id = ?", (group_id,))
                    conn.commit()
                    tier = "free"

        chat_grant = _architect_best_tier_sync(conn, "chat", [group_id])[0]
        user_grant = _architect_best_tier_sync(conn, "user", owners)[0]
        return _max_tier(tier, chat_grant, user_grant)


@db_async
def get_user_global_tier(user_id: int) -> str:
    """Mayor licencia del usuario: comunidades que posee (pagadas o concedidas) + concesión personal del Arquitecto."""
    if is_super_admin(user_id):
        return "ultra_pro"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        tiers = ["free"]
        owned = []
        try:
            cursor.execute("SELECT group_id FROM user_groups WHERE user_id = ?", (user_id,))
            owned = [gid for (gid,) in cursor.fetchall()]
            cursor.execute("""
                SELECT a.tier FROM approved_groups a
                JOIN user_groups u ON a.group_id = u.group_id
                WHERE u.user_id = ? AND (a.expires_at IS NULL OR a.expires_at > datetime('now'))
            """, (user_id,))
            tiers.extend(r[0] for r in cursor.fetchall())
        except sqlite3.OperationalError:
            pass
        tiers.append(_architect_best_tier_sync(conn, "user", [user_id])[0])
        tiers.append(_architect_best_tier_sync(conn, "chat", owned)[0])
        return _max_tier(*tiers)


@db_async
def check_command_limit(group_id: int, command: str, max_uses: int = 3) -> bool:
    tier = get_group_tier.sync(group_id)
    if tier in ["pro", "ultra_pro"]:
        return True

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM command_usage WHERE usage_date < date('now', '-7 days')")
        cursor.execute("SELECT count FROM command_usage WHERE group_id = ? AND command = ? AND usage_date = ?", (group_id, command, today))
        row = cursor.fetchone()
        current_count = row[0] if row else 0
        if current_count >= max_uses:
            return False
        if row:
            cursor.execute("UPDATE command_usage SET count = count + 1 WHERE group_id = ? AND command = ? AND usage_date = ?", (group_id, command, today))
        else:
            cursor.execute("INSERT INTO command_usage (group_id, command, usage_date, count) VALUES (?, ?, ?, 1)", (group_id, command, today))
        conn.commit()
        return True


@db_async
def grant_vip_mic(user_id: int, group_id: int, hours: int = 24):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        modifier = _sql_modifier(hours, "hours")
        cursor.execute("""
            INSERT INTO vip_mic_passes (user_id, group_id, expires_at)
            VALUES (?, ?, datetime('now', ?))
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                expires_at = datetime(
                    CASE WHEN expires_at > datetime('now') THEN expires_at ELSE datetime('now') END,
                    ?
                )
        """, (user_id, group_id, modifier, modifier))
        conn.commit()


@db_async
def is_vip_mic_active(user_id: int, group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM vip_mic_passes
            WHERE user_id = ? AND group_id = ? AND expires_at > datetime('now')
        """, (user_id, group_id))
        return cursor.fetchone() is not None


@db_async
def revoke_vip_mic(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vip_mic_passes WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()


@db_async
def register_bot_clone(user_id: int, group_id: int, bot_token: str, bot_username: str = ""):
    # Ciframos el token con AES-256 antes de guardarlo en la base de datos
    encrypted_token = encrypt_secret(bot_token)
    with _write_transaction() as conn:
        cursor = conn.cursor()
        # Opcional: si deseas revocar ocurrencias previas de este token en otros registros
        cursor.execute(
            "UPDATE bot_clones SET status = 'revoked', bot_token = NULL WHERE user_id != ? OR group_id != ?",
            (user_id, group_id)
        )
        cursor.execute("""
            INSERT INTO bot_clones (user_id, group_id, bot_token, bot_username, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                bot_token = excluded.bot_token,
                bot_username = excluded.bot_username,
                status = 'active'
        """, (user_id, group_id, encrypted_token, bot_username))
        conn.commit()


@db_async
def get_bot_clone(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bot_token, bot_username, status FROM bot_clones WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        row = cursor.fetchone()
        if row:
            # Desciframos el token en memoria RAM antes de retornarlo
            decrypted_token = decrypt_secret(row[0])
            return (decrypted_token, row[1], row[2])
        return None


@db_async
def revoke_bot_clone(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = NULL WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()


@db_async
def get_all_active_clones():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, group_id, bot_token, bot_username FROM bot_clones WHERE status = 'active'")
        return cursor.fetchall()


@db_async
def get_all_active_clone_tokens() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT bot_token FROM bot_clones WHERE status = 'active' AND bot_token IS NOT NULL AND bot_token != ''")
        return [row[0] for row in cursor.fetchall() if row[0]]


@db_async
def save_owner_session(user_id: int, group_id: int, session_string: str, phone_number: str = None, api_id: int = None, api_hash: str = None):
    # Ciframos la cadena de sesión con AES-256 antes de guardarla en la base de datos
    encrypted_session = encrypt_secret(session_string)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO owner_sessions (user_id, group_id, session_string, phone_number, api_id, api_hash, status, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                session_string = excluded.session_string,
                phone_number = COALESCE(excluded.phone_number, owner_sessions.phone_number),
                api_id = COALESCE(excluded.api_id, owner_sessions.api_id),
                api_hash = COALESCE(excluded.api_hash, owner_sessions.api_hash),
                status = 'active',
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, group_id, encrypted_session, phone_number, api_id, api_hash))
        conn.commit()


@db_async
def get_owner_session(user_id: int, group_id: int = None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if group_id is not None:
            cursor.execute(
                "SELECT session_string, api_id, api_hash FROM owner_sessions WHERE user_id = ? AND group_id = ? AND status = 'active'",
                (user_id, group_id)
            )
        else:
            cursor.execute(
                "SELECT session_string, api_id, api_hash FROM owner_sessions WHERE user_id = ? AND status = 'active' "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            )
        row = cursor.fetchone()
        if row:
            # Desciframos la sesión en memoria RAM antes de retornarla
            decrypted_session = decrypt_secret(row[0])
            return (decrypted_session, row[1], row[2])
        return None


@db_async
def get_session_by_group(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, session_string, api_id, api_hash FROM owner_sessions "
            "WHERE group_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
            (group_id,)
        )
        row = cursor.fetchone()
        if row:
            # Desciframos la sesión (índice 1)
            decrypted_session = decrypt_secret(row[1])
            return (row[0], decrypted_session, row[2], row[3])
        return None


@db_async
def get_all_active_sessions():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, group_id, session_string, api_id, api_hash FROM owner_sessions WHERE status = 'active'")
        rows = cursor.fetchall()
        results = []
        for row in rows:
            # Desciframos la sesión de cada fila (índice 2)
            decrypted_session = decrypt_secret(row[2])
            results.append((row[0], row[1], decrypted_session, row[3], row[4]))
        return results


@db_async
def revoke_owner_session(user_id: int, group_id: int = None, reason: str = None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if group_id is not None:
            cursor.execute(
                "UPDATE owner_sessions SET status = 'revoked', last_error = ? WHERE user_id = ? AND group_id = ?",
                (reason, user_id, group_id)
            )
        else:
            cursor.execute(
                "UPDATE owner_sessions SET status = 'revoked', last_error = ? WHERE user_id = ?",
                (reason, user_id)
            )
        conn.commit()


@db_async
def get_vc_schedule(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT days, start_time, end_time, status, call_active FROM vc_schedules WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if row:
            return {
                "days": row[0],
                "start_time": row[1],
                "end_time": row[2],
                "status": row[3],
                "call_active": row[4]
            }
        return {"days": "1,2,3,4,5,6,7", "start_time": "20:00", "end_time": "23:00", "status": 0, "call_active": 0}


@db_async
def set_vc_schedule(group_id: int, days: str, start_time: str, end_time: str, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vc_schedules (group_id, days, start_time, end_time, status) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                days = excluded.days,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                status = excluded.status
        """, (group_id, days, start_time, end_time, status))
        conn.commit()


@db_async
def update_vc_call_status(group_id: int, call_active: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE vc_schedules SET call_active = ? WHERE group_id = ?", (call_active, group_id))
        conn.commit()


@db_async
def get_all_active_vc_schedules():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, days, start_time, end_time, status, call_active FROM vc_schedules WHERE status = 1")
        return cursor.fetchall()


@db_async
def get_panic_status(group_id: int) -> int:
    return _get_setting(group_id, "panic_active", 0)


@db_async
def set_panic_status(group_id: int, status: int):
    _upsert_setting(group_id, "panic_active", status)


@db_async
def get_screen_shield_status(group_id: int) -> int:
    return _get_setting(group_id, "screen_shield_status", 1)


@db_async
def set_screen_shield_status(group_id: int, status: int):
    _upsert_setting(group_id, "screen_shield_status", status)


@db_async
def get_shield_status(group_id: int) -> int:
    return get_screen_shield_status.sync(group_id)


@db_async
def set_shield_status(group_id: int, status: int):
    set_screen_shield_status.sync(group_id, status)


@db_async
def get_podcast_status(group_id: int) -> int:
    return _get_setting(group_id, "podcast_mode_status", 0)


@db_async
def set_podcast_status(group_id: int, status: int):
    set_podcast_mode.sync(group_id, status)


@db_async
def activate_panic(group_id: int, activated_by: int, chat_permissions_json: str = None) -> bool:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT panic_active FROM group_settings WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if row and row[0] == 1:
            return False

        cursor.execute("""
            SELECT COALESCE(lock_media, 0), COALESCE(lock_links, 0), COALESCE(lock_stickers, 0),
                   COALESCE(captcha_status, 0), COALESCE(captcha_mode, 1), COALESCE(captcha_time, 60),
                   COALESCE(antispam, 0), COALESCE(antispam_delete, 0), COALESCE(antiflood_msgs, 10),
                   COALESCE(antiflood_time, 15), COALESCE(antiflood_action, 'kick')
            FROM group_settings WHERE group_id = ?
        """, (group_id,))
        prev = cursor.fetchone()
        prev = prev or (0, 0, 0, 0, 1, 60, 0, 0, 10, 15, 'kick')

        cursor.execute("""
            INSERT INTO panic_snapshots (
                group_id, lock_media, lock_links, lock_stickers, captcha_status, captcha_mode,
                captcha_time, antispam, antispam_delete, antiflood_msgs, antiflood_time,
                antiflood_action, chat_permissions_json, activated_by, activated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET
                lock_media = excluded.lock_media, lock_links = excluded.lock_links,
                lock_stickers = excluded.lock_stickers, captcha_status = excluded.captcha_status,
                captcha_mode = excluded.captcha_mode, captcha_time = excluded.captcha_time,
                antispam = excluded.antispam, antispam_delete = excluded.antispam_delete,
                antiflood_msgs = excluded.antiflood_msgs, antiflood_time = excluded.antiflood_time,
                antiflood_action = excluded.antiflood_action,
                chat_permissions_json = excluded.chat_permissions_json,
                activated_by = excluded.activated_by, activated_at = CURRENT_TIMESTAMP
        """, (group_id, *prev, chat_permissions_json, activated_by))

        cursor.execute("""
            INSERT INTO group_settings (
                group_id, panic_active, lock_media, lock_links, lock_stickers,
                captcha_status, captcha_mode, captcha_time,
                antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action
            ) VALUES (?, 1, 1, 1, 1, 1, 1, 30, 1, 1, 3, 10, 'mute')
            ON CONFLICT(group_id) DO UPDATE SET
                panic_active = 1, lock_media = 1, lock_links = 1, lock_stickers = 1,
                captcha_status = 1, captcha_mode = 1, captcha_time = 30,
                antispam = 1, antispam_delete = 1, antiflood_msgs = 3, antiflood_time = 10,
                antiflood_action = 'mute'
        """, (group_id,))
        conn.commit()
        return True


@db_async
def deactivate_panic(group_id: int) -> dict:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
                   antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action,
                   chat_permissions_json
            FROM panic_snapshots WHERE group_id = ?
        """, (group_id,))
        snap = cursor.fetchone()

        if snap is None:
            cursor.execute("SELECT panic_active FROM group_settings WHERE group_id = ?", (group_id,))
            status_row = cursor.fetchone()
            if not status_row or status_row[0] != 1:
                # Pánico no activo y sin snapshot: no se pisa la configuración actual.
                cursor.execute("UPDATE group_settings SET panic_active = 0 WHERE group_id = ?", (group_id,))
                conn.commit()
                return {"chat_permissions_json": None}

        if snap:
            (lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
             antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action,
             chat_permissions_json) = snap
        else:
            (lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
             antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action,
             chat_permissions_json) = (0, 0, 0, 0, 1, 60, 0, 0, 10, 15, 'kick', None)

        cursor.execute("""
            UPDATE group_settings SET
                panic_active = 0, lock_media = ?, lock_links = ?, lock_stickers = ?,
                captcha_status = ?, captcha_mode = ?, captcha_time = ?,
                antispam = ?, antispam_delete = ?, antiflood_msgs = ?, antiflood_time = ?,
                antiflood_action = ?
            WHERE group_id = ?
        """, (lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
              antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action, group_id))
        cursor.execute("DELETE FROM panic_snapshots WHERE group_id = ?", (group_id,))
        conn.commit()

        return {"chat_permissions_json": chat_permissions_json}


@db_async
def get_podcast_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT podcast_mode_status, podcast_duck_volume, noise_shield_status FROM group_settings WHERE group_id = ?",
                (group_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "status": row[0] if row[0] is not None else 0,
                    "duck_volume": row[1] if row[1] is not None else 500,
                    "noise_shield": row[2] if row[2] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {"status": 0, "duck_volume": 500, "noise_shield": 1}


@db_async
def set_podcast_mode(group_id: int, status: int):
    _upsert_setting(group_id, "podcast_mode_status", status)


@db_async
def set_podcast_duck_volume(group_id: int, volume: int):
    volume = max(0, min(10000, _as_int(volume, 500)))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, podcast_duck_volume) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET podcast_duck_volume = excluded.podcast_duck_volume
        """, (group_id, volume))
        conn.commit()


@db_async
def set_noise_shield_status(group_id: int, status: int):
    _upsert_setting(group_id, "noise_shield_status", status)


@db_async
def flag_userbot(user_id: int, group_id: int, reason: str = "Patrón sospechoso de Userbot"):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO flagged_userbots (user_id, group_id, reason, flagged_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                reason = excluded.reason,
                flagged_at = CURRENT_TIMESTAMP
        """, (user_id, group_id, reason))
        conn.commit()


@db_async
def is_userbot_flagged(user_id: int, group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM flagged_userbots WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        return cursor.fetchone() is not None


@db_async
def purge_flagged_userbot_record(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM flagged_userbots WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()


@db_async
def get_community_live_telemetry(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vip_mic_passes WHERE group_id = ? AND expires_at > datetime('now')", (group_id,))
        vip_active = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM speaker_queue WHERE group_id = ? AND status = 'waiting'", (group_id,))
        speakers_in_queue = cursor.fetchone()[0]

        cursor.execute("""
            SELECT panic_active, screen_shield_status, podcast_mode_status, autolower, night_mode_status
            FROM group_settings WHERE group_id = ?
        """, (group_id,))
        row = cursor.fetchone() or (0, 1, 0, 1, 0)

        cursor.execute("SELECT status FROM bot_clones WHERE group_id = ? AND status = 'active'", (group_id,))
        clone_row = cursor.fetchone()

        cursor.execute("SELECT status FROM owner_sessions WHERE group_id = ? AND status = 'active'", (group_id,))
        session_row = cursor.fetchone()

        return {
            "vip_passes_active": vip_active,
            "speakers_in_queue": speakers_in_queue,
            "panic_active": row[0],
            "shield_status": row[1],
            "podcast_status": row[2],
            "autolower_status": row[3],
            "night_mode_status": row[4],
            "has_active_clone": clone_row is not None,
            "has_active_sentinel": session_row is not None
        }


@db_async
def get_channel_live_telemetry(channel_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM channel_subscriptions
            WHERE channel_id = ? AND status = 'active' AND expires_at > datetime('now')
        """, (channel_id,))
        active_subs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM channel_plans WHERE channel_id = ? AND status = 'active'", (channel_id,))
        active_plans = cursor.fetchone()[0]

        cursor.execute("SELECT tips_enabled, tips_amount, tips_target_channel FROM group_settings WHERE group_id = ?", (channel_id,))
        tips_row = cursor.fetchone() or (0, 10, "")

        return {
            "active_subscribers": active_subs,
            "active_plans": active_plans,
            "tips_enabled": tips_row[0],
            "tips_amount": tips_row[1],
            "tips_target": tips_row[2]
        }


@db_async
def get_channel_settings(channel_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT sub_price, grace_days, auto_kick, notify_renewal, custom_welcome
                FROM channel_settings WHERE channel_id = ?
            """, (channel_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "sub_price": row[0] if row[0] is not None else 0,
                    "grace_days": row[1] if row[1] is not None else 1,
                    "auto_kick": row[2] if row[2] is not None else 1,
                    "notify_renewal": row[3] if row[3] is not None else 1,
                    "custom_welcome": row[4] or ""
                }
        except sqlite3.OperationalError:
            pass
        return {"sub_price": 0, "grace_days": 1, "auto_kick": 1, "notify_renewal": 1, "custom_welcome": ""}


@db_async
def set_channel_settings(channel_id: int, field: str, value):
    valid_fields = ["sub_price", "grace_days", "auto_kick", "notify_renewal", "custom_welcome"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO channel_settings (channel_id, {field}) VALUES (?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET {field} = excluded.{field}
        """, (channel_id, value))
        conn.commit()


def _sanitize_target_link(target_link: str = None) -> str:
    if target_link is None:
        return None
    clean = str(target_link).strip()
    return clean if clean else None


PLAN_NAME_MAX = 64
PLAN_PROMO_MAX = 1000
PLAN_PRICE_RANGE = (1, 10000)      # una factura XTR admite de 1 a 10 000 Stars
PLAN_DAYS_RANGE = (1, 365)
PLAN_MEDIA_TYPES = {"photo", "video", "animation"}


@db_async
def create_channel_plan(
    channel_id: int,
    plan_name: str,
    duration_days: int,
    stars_price: int,
    promo_text: str = None,
    media_id: str = None,
    media_type: str = None,
    target_link: str = None
) -> int:
    """
    Inserta un plan en `channel_plans` con status='active' dentro de BEGIN IMMEDIATE.

    La firma conserva el orden posicional histórico (… promo_text, media_id, media_type, target_link)
    porque el asistente de planes del bot (handlers/user_private.py) la llama así; la Mini App usa
    argumentos con nombre. Los rangos se validan aquí también: ningún llamador puede crear un plan
    imposible de cobrar (Stars fuera de 1-10 000 o duración fuera de 1-365 días).
    """
    name = (plan_name or "").strip()
    days = _as_int(duration_days, 0)
    price = _as_int(stars_price, 0)
    if not name or len(name) > PLAN_NAME_MAX:
        raise ValueError(f"plan_name: entre 1 y {PLAN_NAME_MAX} caracteres.")
    if not PLAN_DAYS_RANGE[0] <= days <= PLAN_DAYS_RANGE[1]:
        raise ValueError(f"duration_days: entre {PLAN_DAYS_RANGE[0]} y {PLAN_DAYS_RANGE[1]} días.")
    if not PLAN_PRICE_RANGE[0] <= price <= PLAN_PRICE_RANGE[1]:
        raise ValueError(f"stars_price: entre {PLAN_PRICE_RANGE[0]} y {PLAN_PRICE_RANGE[1]} Stars.")
    promo = (promo_text or "").strip() or None
    if promo and len(promo) > PLAN_PROMO_MAX:
        raise ValueError(f"promo_text: máximo {PLAN_PROMO_MAX} caracteres.")
    if media_type is not None and media_type not in PLAN_MEDIA_TYPES:
        raise ValueError("media_type: debe ser photo, video o animation.")
    if (media_id is None) != (media_type is None):
        raise ValueError("media_id y media_type van juntos.")

    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channel_plans (
                channel_id, plan_name, duration_days, stars_price, status,
                promo_text, media_id, media_type, target_link
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """, (
            int(channel_id), name, days, price,
            promo, media_id, media_type, _sanitize_target_link(target_link)
        ))
        return cursor.lastrowid


@db_async
def get_channel_plan(plan_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT plan_id, channel_id, plan_name, duration_days, stars_price, status,
                   promo_text, media_id, media_type, created_at, target_link,
                   broadcast_chat_id, broadcast_interval_hours, next_broadcast_at, broadcast_enabled,
                   last_broadcast_at, broadcast_failures, broadcast_lang
            FROM channel_plans WHERE plan_id = ?
        """, (plan_id,))
        row = cursor.fetchone()
        if row:
            return {
                "plan_id": row[0],
                "channel_id": row[1],
                "plan_name": row[2],
                "duration_days": row[3],
                "stars_price": row[4],
                "status": row[5],
                "promo_text": row[6],
                "media_id": row[7],
                "media_type": row[8],
                "created_at": row[9],
                "target_link": row[10],
                "broadcast_chat_id": row[11],
                "broadcast_interval_hours": row[12],
                "next_broadcast_at": row[13],
                "broadcast_enabled": row[14] or 0,
                "last_broadcast_at": row[15],
                "broadcast_failures": row[16] or 0,
                "broadcast_lang": _clean_broadcast_lang(row[17]),
            }
        return None


@db_async
def get_channel_plans(channel_id: int, only_active: bool = True) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if only_active:
            cursor.execute("""
                SELECT plan_id, plan_name, duration_days, stars_price, status, created_at,
                       promo_text, media_id, media_type, target_link,
                       broadcast_chat_id, broadcast_interval_hours, next_broadcast_at, broadcast_enabled
                FROM channel_plans WHERE channel_id = ? AND status = 'active'
                ORDER BY duration_days ASC
            """, (channel_id,))
        else:
            cursor.execute("""
                SELECT plan_id, plan_name, duration_days, stars_price, status, created_at,
                       promo_text, media_id, media_type, target_link,
                       broadcast_chat_id, broadcast_interval_hours, next_broadcast_at, broadcast_enabled
                FROM channel_plans WHERE channel_id = ?
                ORDER BY status ASC, duration_days ASC
            """, (channel_id,))
        return cursor.fetchall()


@db_async
def set_channel_plan_status(plan_id: int, status: str):
    if status not in ["active", "paused", "archived"]:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE channel_plans SET status = ? WHERE plan_id = ?", (status, plan_id))
        conn.commit()


@db_async
def toggle_channel_plan_status(plan_id: int) -> str:
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM channel_plans WHERE plan_id = ?", (plan_id,))
        row = cursor.fetchone()
        if not row:
            return "active"
        new_status = "paused" if row[0] == "active" else "active"
        cursor.execute("UPDATE channel_plans SET status = ? WHERE plan_id = ?", (new_status, plan_id))
        conn.commit()
        return new_status


@db_async
def delete_channel_plan(plan_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channel_plans WHERE plan_id = ?", (plan_id,))
        conn.commit()


# ==========================================
# ⏰ v9 · PROGRAMADOR RECURRENTE DE PLANES (6 / 12 / 24 / 48 h)
# ==========================================
# Contrato del programador:
#   • last_broadcast_at = último envío REAL confirmado por Telegram (jamás se toca en un fallo).
#   • next_broadcast_at = last_broadcast_at + intervalo. Un fallo transitorio (red, FloodWait, 5xx) solo
#     programa un REINTENTO corto; cuando el envío por fin sale, el siguiente vuelve a medirse desde ese
#     envío real. Así un corte de red nunca "reinicia" ni alarga el ciclo de 12 h.
#   • Solo los errores permanentes (bot expulsado, sin permisos, chat borrado) cuentan hacia la
#     desactivación automática, tras PLAN_BROADCAST_MAX_FAILURES intentos consecutivos.
PLAN_BROADCAST_INTERVALS = (6, 12, 24, 48)
PLAN_BROADCAST_MAX_FAILURES = 3
PLAN_BROADCAST_RETRY_MINUTES = (5, 15, 30, 60)


def _plan_retry_minutes(failures: int) -> int:
    idx = max(0, min(int(failures or 0), len(PLAN_BROADCAST_RETRY_MINUTES) - 1))
    return PLAN_BROADCAST_RETRY_MINUTES[idx]


def _clean_broadcast_lang(lang) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "es"


@db_async
def set_channel_plan_broadcast_config(plan_id: int, chat_id: int, interval_hours: int, lang: str = None) -> bool:
    """
    Activa (o cambia) la difusión recurrente del plan. interval_hours ∈ PLAN_BROADCAST_INTERVALS; 0 la
    desactiva conservando el historial. Si el plan ya se difundió alguna vez, el próximo envío se ancla a
    ese envío real (cambiar 12 h → 24 h no publica de inmediato ni reinicia el reloj); si nunca se difundió,
    el primer envío automático sale tras un intervalo completo ("Difundir" publica al instante).
    """
    try:
        hours = int(interval_hours)
    except (TypeError, ValueError):
        return False
    if hours == 0:
        disable_channel_plan_broadcast.sync(plan_id)
        return True
    if hours not in PLAN_BROADCAST_INTERVALS:
        return False
    modifier = _sql_modifier(hours, "hours")
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE channel_plans
            SET broadcast_chat_id = ?,
                broadcast_interval_hours = ?,
                broadcast_enabled = 1,
                broadcast_failures = 0,
                broadcast_lang = COALESCE(?, broadcast_lang, 'es'),
                next_broadcast_at = CASE
                    WHEN last_broadcast_at IS NOT NULL AND datetime(last_broadcast_at, ?) > datetime('now')
                        THEN datetime(last_broadcast_at, ?)
                    WHEN last_broadcast_at IS NOT NULL THEN datetime('now', '+1 minutes')
                    ELSE datetime('now', ?)
                END
            WHERE plan_id = ?
        """, (int(chat_id), hours, _clean_broadcast_lang(lang) if lang else None,
              modifier, modifier, modifier, int(plan_id)))
        return cursor.rowcount > 0


@db_async
def disable_channel_plan_broadcast(plan_id: int):
    with _write_transaction() as conn:
        conn.execute("UPDATE channel_plans SET broadcast_enabled = 0 WHERE plan_id = ?", (int(plan_id),))


@db_async
def mark_channel_plan_broadcasted(plan_id: int):
    """Envío REAL confirmado: ancla el reloj (last_broadcast_at = ahora) y programa el siguiente."""
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT broadcast_interval_hours, status, broadcast_enabled FROM channel_plans WHERE plan_id = ?", (int(plan_id),))
        row = cursor.fetchone()
        if not row:
            return
        interval_hours = _as_int(row[0], 0)
        keep_schedule = bool(row[2]) and row[1] == "active" and interval_hours in PLAN_BROADCAST_INTERVALS
        if keep_schedule:
            cursor.execute("""
                UPDATE channel_plans
                SET last_broadcast_at = datetime('now'), broadcast_failures = 0,
                    next_broadcast_at = datetime('now', ?)
                WHERE plan_id = ?
            """, (_sql_modifier(interval_hours, "hours"), int(plan_id)))
        else:
            cursor.execute("""
                UPDATE channel_plans
                SET last_broadcast_at = datetime('now'), broadcast_failures = 0,
                    broadcast_enabled = CASE WHEN status = 'active' THEN broadcast_enabled ELSE 0 END
                WHERE plan_id = ?
            """, (int(plan_id),))


@db_async
def register_channel_plan_broadcast_failure(plan_id: int, permanent: bool = False, retry_after_seconds: int = 0) -> bool:
    """
    Fallo de envío. NO toca last_broadcast_at: solo programa un reintento corto (backoff 5/15/30/60 min o
    el retry_after de Telegram). Devuelve True si la difusión quedó desactivada (fallos permanentes
    consecutivos ≥ PLAN_BROADCAST_MAX_FAILURES).
    """
    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT broadcast_failures FROM channel_plans WHERE plan_id = ?", (int(plan_id),))
        row = cursor.fetchone()
        if not row:
            return True
        failures = _as_int(row[0], 0) + (1 if permanent else 0)
        if permanent and failures >= PLAN_BROADCAST_MAX_FAILURES:
            cursor.execute("UPDATE channel_plans SET broadcast_enabled = 0, broadcast_failures = ? WHERE plan_id = ?",
                           (failures, int(plan_id)))
            return True
        wait_seconds = max(int(retry_after_seconds or 0) + 5, _plan_retry_minutes(failures) * 60)
        cursor.execute("""
            UPDATE channel_plans
            SET broadcast_failures = ?, next_broadcast_at = datetime('now', ?)
            WHERE plan_id = ?
        """, (failures, _sql_modifier(wait_seconds, "seconds"), int(plan_id)))
        return False


@db_async
def get_due_channel_plan_broadcasts(limit: int = 25) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT plan_id, channel_id, plan_name, duration_days, stars_price,
                   promo_text, media_id, media_type, target_link,
                   broadcast_chat_id, broadcast_interval_hours, next_broadcast_at,
                   last_broadcast_at, broadcast_failures, broadcast_lang
            FROM channel_plans
            WHERE broadcast_enabled = 1
              AND status = 'active'
              AND broadcast_chat_id IS NOT NULL
              AND next_broadcast_at IS NOT NULL
              AND next_broadcast_at <= datetime('now')
            ORDER BY next_broadcast_at ASC
            LIMIT ?
        """, (max(1, int(limit)),))
        rows = cursor.fetchall()
        return [
            {
                "plan_id": r[0], "channel_id": r[1], "plan_name": r[2], "duration_days": r[3], "stars_price": r[4],
                "promo_text": r[5], "media_id": r[6], "media_type": r[7], "target_link": r[8],
                "broadcast_chat_id": r[9], "broadcast_interval_hours": r[10], "next_broadcast_at": r[11],
                "last_broadcast_at": r[12], "broadcast_failures": r[13] or 0,
                "broadcast_lang": _clean_broadcast_lang(r[14]),
            }
            for r in rows
        ]


@db_async
def record_channel_subscription(channel_id: int, user_id: int, plan_id: int, stars_paid: int, duration_days: int, invite_link: str = None):
    modifier = _sql_modifier(duration_days, "days")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channel_subscriptions (
                channel_id, user_id, plan_id, stars_paid, invite_link,
                subscribed_at, expires_at, status
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, datetime('now', ?), 'active')
            ON CONFLICT(channel_id, user_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                stars_paid = COALESCE(channel_subscriptions.stars_paid, 0) + excluded.stars_paid,
                invite_link = COALESCE(excluded.invite_link, channel_subscriptions.invite_link),
                expires_at = datetime(
                    CASE WHEN channel_subscriptions.expires_at > datetime('now')
                         THEN channel_subscriptions.expires_at ELSE datetime('now') END,
                    ?
                ),
                status = 'active',
                last_warned_at = NULL
        """, (channel_id, user_id, plan_id, stars_paid, invite_link, modifier, modifier))
        conn.commit()


@db_async
def get_channel_subscription(channel_id: int, user_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, plan_id, stars_paid, invite_link, subscribed_at, expires_at, status, last_warned_at,
                   datetime('now') > expires_at AS is_expired
            FROM channel_subscriptions WHERE channel_id = ? AND user_id = ?
        """, (channel_id, user_id))
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0], "plan_id": row[1], "stars_paid": row[2], "invite_link": row[3],
                "subscribed_at": row[4], "expires_at": row[5], "status": row[6], "last_warned_at": row[7],
                "is_expired": bool(row[8])
            }
        return None


@db_async
def get_expiring_channel_subscriptions(hours_ahead: int = 48) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # LEFT JOIN: los canales sin fila en channel_settings también se auditan con los defaults.
        cursor.execute("""
            SELECT s.channel_id, s.user_id, s.expires_at, s.stars_paid, COALESCE(c.grace_days, 1)
            FROM channel_subscriptions s
            LEFT JOIN channel_settings c ON s.channel_id = c.channel_id
            WHERE s.status = 'active'
              AND s.expires_at > datetime('now')
              AND s.expires_at <= datetime('now', ?)
              AND (s.last_warned_at IS NULL OR s.last_warned_at < datetime('now', '-20 hours'))
        """, (_sql_modifier(hours_ahead, "hours"),))
        return cursor.fetchall()


@db_async
def get_expired_channel_subscriptions() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.channel_id, s.user_id, s.expires_at, COALESCE(c.grace_days, 1), COALESCE(c.auto_kick, 1)
            FROM channel_subscriptions s
            LEFT JOIN channel_settings c ON s.channel_id = c.channel_id
            WHERE s.status IN ('active', 'grace')
              AND datetime('now') > datetime(s.expires_at, '+' || COALESCE(c.grace_days, 1) || ' days')
        """)
        return cursor.fetchall()


@db_async
def mark_subscription_warned(channel_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE channel_subscriptions SET last_warned_at = CURRENT_TIMESTAMP
            WHERE channel_id = ? AND user_id = ?
        """, (channel_id, user_id))
        conn.commit()


@db_async
def update_subscription_status(channel_id: int, user_id: int, status: str):
    valid_statuses = ["active", "grace", "expired", "kicked"]
    if status not in valid_statuses:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE channel_subscriptions SET status = ?
            WHERE channel_id = ? AND user_id = ?
        """, (status, channel_id, user_id))
        conn.commit()


@db_async
def get_active_subscribers_count(channel_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM channel_subscriptions
            WHERE channel_id = ? AND status = 'active' AND expires_at > datetime('now')
        """, (channel_id,))
        row = cursor.fetchone()
        return row[0] if row else 0


@db_async
def record_chat_activity(group_id: int, user_id: int, full_name: str, username: str, is_reply: bool = False, is_admin: bool = False):
    month_key = datetime.now(timezone.utc).strftime("%b '%y")
    reply_inc = 1 if is_reply else 0
    admin_flag = 1 if is_admin else 0
    with _write_transaction() as conn:
        cursor = conn.cursor()
        # ¿Es la primera actividad del usuario en el mes en curso? (para MAU real)
        cursor.execute("""
            SELECT strftime('%Y-%m', last_active) = strftime('%Y-%m', 'now')
            FROM chat_user_activity WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        prev = cursor.fetchone()
        new_monthly_user = 1 if (prev is None or not prev[0]) else 0

        cursor.execute("""
            INSERT INTO chat_user_activity (group_id, user_id, full_name, username, message_count, reply_count, is_admin, last_active)
            VALUES (?, ?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id, user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username,
                message_count = COALESCE(chat_user_activity.message_count, 0) + 1,
                reply_count = COALESCE(chat_user_activity.reply_count, 0) + excluded.reply_count,
                is_admin = excluded.is_admin,
                last_active = CURRENT_TIMESTAMP
        """, (group_id, user_id, full_name, username or "", reply_inc, admin_flag))

        cursor.execute("""
            INSERT INTO chat_monthly_metrics (group_id, month_key, total_messages, total_users)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(group_id, month_key) DO UPDATE SET
                total_messages = COALESCE(chat_monthly_metrics.total_messages, 0) + 1,
                total_users = COALESCE(chat_monthly_metrics.total_users, 0) + ?
        """, (group_id, month_key, max(1, new_monthly_user), new_monthly_user))

        # v9 · Actividad diaria de administradores (base del informe periódico del canal de registro)
        if admin_flag:
            cursor.execute("""
                INSERT INTO admin_activity_daily (group_id, user_id, day, full_name, username, messages, replies)
                VALUES (?, ?, date('now'), ?, ?, 1, ?)
                ON CONFLICT(group_id, user_id, day) DO UPDATE SET
                    full_name = excluded.full_name,
                    username = excluded.username,
                    messages = admin_activity_daily.messages + 1,
                    replies = admin_activity_daily.replies + excluded.replies
            """, (group_id, user_id, full_name, username or "", reply_inc))
        conn.commit()


@db_async
def add_user_reputation_xp(
    group_id: int,
    user_id: int,
    full_name: str,
    username: str,
    base_xp: int = 10,
    cooldown_seconds: int = 45
) -> dict:
    rep_cfg = _read_reputation_settings(group_id)
    if rep_cfg["enabled"] != 1:
        return {"awarded": False, "xp": 0, "level": 1, "leveled_up": False, "reason": "disabled"}

    effective_xp = max(1, int(base_xp * rep_cfg["multiplier"]))

    with _write_transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT xp, level, CAST((julianday('now') - julianday(last_xp_at)) * 86400 AS INTEGER)
            FROM chat_user_reputation WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        row = cursor.fetchone()

        if row:
            current_xp, current_level, elapsed_sec = row
            current_xp = current_xp or 0
            current_level = current_level or 1
            if elapsed_sec is not None and elapsed_sec < cooldown_seconds:
                return {"awarded": False, "xp": current_xp, "level": current_level, "leveled_up": False, "reason": "cooldown"}
        else:
            current_xp, current_level = 0, 1

        new_xp = current_xp + effective_xp
        new_level = int((new_xp / 100) ** 0.5) + 1
        leveled_up = new_level > current_level
        exists = row is not None

        if exists:
            cursor.execute("""
                UPDATE chat_user_reputation SET
                    full_name = ?, username = ?,
                    xp = ?, level = ?, last_xp_at = CURRENT_TIMESTAMP
                WHERE group_id = ? AND user_id = ?
            """, (full_name, username or "", new_xp, new_level, group_id, user_id))
        else:
            cursor.execute("""
                INSERT INTO chat_user_reputation (group_id, user_id, full_name, username, xp, level, last_xp_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (group_id, user_id, full_name, username or "", new_xp, new_level))
        conn.commit()

        return {
            "awarded": True, "xp": new_xp, "level": new_level,
            "leveled_up": leveled_up, "gained_xp": effective_xp
        }


@db_async
def get_user_reputation(group_id: int, user_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT xp, level FROM chat_user_reputation
            WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        row = cursor.fetchone()
        if not row:
            return {"xp": 0, "level": 1, "rank": 0}

        cursor.execute("""
            SELECT COUNT(*) + 1 FROM chat_user_reputation
            WHERE group_id = ? AND xp > ?
        """, (group_id, row[0]))
        rank = cursor.fetchone()[0]

        return {"xp": row[0], "level": row[1], "rank": rank}


@db_async
def get_top_reputation(group_id: int, limit: int = 10) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, full_name, username, xp, level
            FROM chat_user_reputation
            WHERE group_id = ?
            ORDER BY xp DESC LIMIT ?
        """, (group_id, limit))
        rows = cursor.fetchall()
        return [
            {
                "user_id": r[0], "name": r[1] or f"User {r[0]}",
                "username": f"@{r[2]}" if r[2] else "", "xp": r[3], "level": r[4]
            }
            for r in rows
        ]


@db_async
def record_hourly_chat_activity(group_id: int, dt: datetime = None):
    now = dt or datetime.now()
    day_of_week = now.isoweekday()
    hour_of_day = now.hour
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_hourly_activity (group_id, day_of_week, hour_of_day, message_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(group_id, day_of_week, hour_of_day) DO UPDATE SET
                message_count = COALESCE(chat_hourly_activity.message_count, 0) + 1
        """, (group_id, day_of_week, hour_of_day))
        conn.commit()


@db_async
def get_chat_heatmap_matrix(group_id: int) -> dict:
    matrix = {day: {hour: 0 for hour in range(24)} for day in range(1, 8)}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT day_of_week, hour_of_day, message_count
            FROM chat_hourly_activity
            WHERE group_id = ?
        """, (group_id,))
        for day, hour, count in cursor.fetchall():
            if day in matrix and hour in matrix[day]:
                matrix[day][hour] = count
    return {
        "group_id": group_id, "matrix": matrix,
        "days": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    }


# ==========================================
# 📊 ANALÍTICA, DASHBOARDS Y TELEMETRÍA (FASTAPI)
# ==========================================
TIER_LABELS = {"pro": "PRO ⭐", "ultra_pro": "ULTRA PRO 💎", "free": "Free"}


def normalize_tier(tier) -> str:
    """'ULTRA_PRO' / 'ultrapro' / 'ultra' → 'ultra_pro'; 'pro' → 'pro'; lo demás → 'free'."""
    value = str(tier or "free").strip().lower().replace("-", "_").replace(" ", "_")
    if value in ("ultra_pro", "ultrapro", "ultra"):
        return "ultra_pro"
    return "pro" if value == "pro" else "free"


def describe_license(tier, expires_at=None, source: str = "community") -> dict:
    """Bloque `plan` del dashboard: misma forma para comunidad y operador (1:1 con la Mini App)."""
    norm = normalize_tier(tier)
    return {
        "tier": norm,
        "name": TIER_LABELS[norm],
        "label": TIER_LABELS[norm],
        "status": "active" if norm != "free" else "empty",
        "expires_at": expires_at if norm != "free" else None,
        "source": source if norm != "free" else None,
    }


@db_async
def get_chat_dashboard_data(chat_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Licencia VIGENTE (caducidad aplicada y comunidades de super-admins incluidas): antes se leía
        # approved_groups.tier en bruto y el panel mostraba "Ultra_pro", licencias vencidas como activas
        # o el plan vacío en comunidades blindadas cuyo dueño es un arquitecto.
        tier = normalize_tier(get_group_tier.sync(chat_id))
        # v13: vencimiento y origen reales (pago con Stars o concesión del Arquitecto).
        license_info = get_effective_license.sync(chat_id)
        expires_at = license_info.get("expires_at") if license_info.get("tier") == tier else None

        cursor.execute("""
            SELECT log_channel_id, spam_detection_mode, timezone, chat_language,
                   active_modules_count, captcha_status, autolower, screen_shield_status, lock_links
            FROM group_settings WHERE group_id = ?
        """, (chat_id,))
        row = cursor.fetchone()

        log_id = row[0] if row and row[0] else None
        spam_mode = row[1] if row and row[1] else "smart"
        tz = row[2] if row and row[2] else "Bogota (UTC-05)"
        lang = row[3] if row and row[3] else "ES"
        active_mods = row[4] if row and row[4] is not None else 11

        captcha_active = bool(row[5]) if row and row[5] is not None else False
        autolower_active = bool(row[6]) if row and row[6] is not None else True
        shield_active = bool(row[7]) if row and row[7] is not None else True
        linklock_active = bool(row[8]) if row and row[8] is not None else False

        return {
            "chat_id": str(chat_id),
            "plan": describe_license(tier, expires_at, "community"),
            "log_channel": {"enabled": log_id is not None, "channel_id": log_id},
            "modules": {"active": active_mods, "total": 91},
            "protection": {"enabled": True, "spam_mode": spam_mode, "timezone": tz, "language": lang},
            "switches": {"captcha": captcha_active, "autolower": autolower_active, "shield": shield_active, "linklock": linklock_active},
            "modules_errors": [],
            "footer_metrics": {}
        }


@db_async
def get_chat_timeseries_stats(chat_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT month_key, total_messages, total_users
            FROM chat_monthly_metrics WHERE group_id = ?
            ORDER BY rowid DESC LIMIT 12
        """, (chat_id,))
        rows = cursor.fetchall()

        if not rows:
            now = datetime.now(timezone.utc)
            months = []
            year, month = now.year, now.month
            for _ in range(5):
                months.append(datetime(year, month, 1).strftime("%b '%y"))
                month -= 1
                if month == 0:
                    month, year = 12, year - 1
            months.reverse()
            return {"months": months, "mau": [0, 0, 0, 0, 0], "messages": [0, 0, 0, 0, 0], "messages_per_user": [0, 0, 0, 0, 0]}

        months = [r[0] for r in reversed(rows)]
        messages = [r[1] or 0 for r in reversed(rows)]
        mau = [r[2] or 0 for r in reversed(rows)]
        msgs_per_user = [round(m / max(1, u), 1) for m, u in zip(messages, mau)]

        return {"months": months, "mau": mau, "messages": messages, "messages_per_user": msgs_per_user}


@db_async
def get_chat_top_users(chat_id: int, limit: int = 10) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, full_name, username, message_count
            FROM chat_user_activity WHERE group_id = ?
            ORDER BY message_count DESC LIMIT ?
        """, (chat_id, limit))
        rows = cursor.fetchall()

        res = []
        for idx, r in enumerate(rows, start=1):
            msg_count = r[3] or 0
            act_level = 4 if msg_count > 300 else (3 if msg_count > 150 else (2 if msg_count > 50 else 1))
            res.append({
                "rank": idx,
                "name": r[1] or f"User {r[0]}",
                "badge": f"@{r[2]}" if r[2] else "",
                "activity_level": act_level,
                "messages": msg_count
            })
        return res


@db_async
def get_chat_admin_stats(chat_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT full_name, username, message_count, reply_count
                FROM chat_user_activity WHERE group_id = ? AND is_admin = 1
                ORDER BY message_count DESC
            """, (chat_id,))
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return []

        return [
            {
                "name": r[0] or f"Admin {r[1]}",
                "role": "Administrator",
                "messages": r[2],
                "replies": r[3],
                "actions": 0
            }
            for r in rows
        ]


@db_async
def update_chat_operational_settings(chat_id: int, settings: dict):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        updates = []
        params = []

        if "spam_mode" in settings:
            updates.append("spam_detection_mode = ?")
            params.append(settings["spam_mode"])
        if "timezone" in settings:
            updates.append("timezone = ?")
            params.append(settings["timezone"])
        if "language" in settings:
            updates.append("chat_language = ?")
            params.append(settings["language"])
        if "log_channel_id" in settings:
            updates.append("log_channel_id = ?")
            params.append(settings["log_channel_id"])

        if "captcha" in settings:
            updates.append("captcha_status = ?")
            params.append(1 if settings["captcha"] else 0)
        if "autolower" in settings:
            updates.append("autolower = ?")
            params.append(1 if settings["autolower"] else 0)
        if "shield" in settings:
            updates.append("screen_shield_status = ?")
            params.append(1 if settings["shield"] else 0)
        if "linklock" in settings:
            updates.append("lock_links = ?")
            params.append(1 if settings["linklock"] else 0)

        cursor.execute("""
            INSERT INTO group_settings (group_id) VALUES (?)
            ON CONFLICT(group_id) DO NOTHING
        """, (chat_id,))
        if "linklock" in settings:
            # v15 · El interruptor de enlaces de la Mini App gobierna también los vectores de enlaces de la matriz.
            _push_legacy_lock_to_matrix(conn, chat_id, "lock_links", 1 if settings["linklock"] else 0)

        if updates:
            params.append(chat_id)
            query = f"UPDATE group_settings SET {', '.join(updates)} WHERE group_id = ?"
            cursor.execute(query, tuple(params))
        conn.commit()


# ==========================================
# 🎛️ v8.3 · CONFIGURACIÓN OPERATIVA COMPLETA (paridad 1:1 Mini App ↔ bot)
# ==========================================
# Contrato de datos: cada campo de la API apunta a la MISMA columna de `group_settings` que leen los
# handlers del bot (get_captcha_config, get_autolower_status, get_mic_vip_price, get_tips_config, …).
# Así un cambio hecho desde la Mini App rige el comportamiento del bot sin tablas paralelas.
DEFAULT_TIP_PRESETS = (15, 50, 100, 250, 500)
TIP_MAX_STARS = 10000
WELCOME_MAX = 1000
WELCOME_VARIABLES = ("name", "username", "title")
CAPTCHA_MODE_CODES = {"button": 1, "math": 2}          # captcha_mode en group_settings (1 = botón)
CAPTCHA_MODE_NAMES = {v: k for k, v in CAPTCHA_MODE_CODES.items()}
CAPTCHA_TIMEOUT_RANGE = (30, 300)
AUTOLOWER_PCT_RANGE = (1, 10)
STARS_PRICE_RANGE = (1, 10000)
ANTIFLOOD_RATE_RANGE = (3, 50)
ANTIFLOOD_WINDOW_RANGE = (5, 120)
_WELCOME_VAR_RE = re.compile(r"\{([^{}]*)\}")
# Agradecimiento de propinas (PRO / ULTRA PRO): mismas variables que renderiza payments.send_tip_thanks.
TIPS_THANKS_MAX = 3000
TIPS_THANKS_VARIABLES = ("stars", "mention", "user_name", "community")
_TIPS_THANKS_VAR_ALIASES = ("amount", "name")      # alias heredados que el bot también sustituye
# Etiquetas que Telegram admite con parse_mode=HTML; cualquier otra hace fallar el envío.
_TG_HTML_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre",
    "a", "tg-spoiler", "span", "blockquote", "tg-emoji",
}
_TG_HTML_ATTRS = {"a": {"href"}, "span": {"class"}, "code": {"class"}, "tg-emoji": {"emoji-id"}, "blockquote": {"expandable"}}


class ChatConfigError(ValueError):
    """Errores de validación por campo: {campo: mensaje}."""

    def __init__(self, errors: dict):
        self.errors = dict(errors)
        first = next(iter(self.errors.items()))
        super().__init__(f"{first[0]}: {first[1]}")


# campo de la API → (sección, columna, tipo)
CHAT_CONFIG_FIELDS = {
    "captcha_enabled":         ("aduana",    "captcha_status",       "bool"),
    "captcha_mode":            ("aduana",    "captcha_mode",         "captcha_mode"),
    "captcha_timeout":         ("aduana",    "captcha_time",         "captcha_timeout"),
    "custom_welcome":          ("aduana",    "captcha_text",         "welcome"),
    "autolower_enabled":       ("acoustic",  "autolower",            "bool"),
    "autolower_pct":           ("acoustic",  "autolower_pct",        "autolower_pct"),
    "shield_enabled":          ("acoustic",  "screen_shield_status", "bool"),
    "micvip_price":            ("acoustic",  "mic_vip_price",        "stars"),
    "speaker_price":           ("acoustic",  "speaker_queue_price",  "stars"),
    "tips_enabled":            ("tips",      "tips_enabled",         "bool"),
    "tips_presets":            ("tips",      "tips_presets",         "tip_presets"),
    "custom_tips_allowed":     ("tips",      "tips_custom_allowed",  "bool"),
    "tips_thanks_text":        ("tips",      "tips_thanks_text",     "tips_thanks"),
    "linklock_enabled":        ("perimeter", "lock_links",           "bool"),
    "antiflood_enabled":       ("perimeter", "antispam",             "bool"),
    "antiflood_rate":          ("perimeter", "antiflood_msgs",       "antiflood_rate"),
    "antiflood_window":        ("perimeter", "antiflood_time",       "antiflood_window"),
    "service_cleaner_enabled": ("perimeter", "service_msgs_mode",    "bool"),
}
CHAT_CONFIG_SECTIONS = ("aduana", "acoustic", "tips", "perimeter")

CHAT_CONFIG_DEFAULTS = {
    "captcha_enabled": False, "captcha_mode": "button", "captcha_timeout": 60, "custom_welcome": "",
    "autolower_enabled": True, "autolower_pct": 2, "shield_enabled": True, "micvip_price": 50, "speaker_price": 25,
    "tips_enabled": False, "tips_presets": list(DEFAULT_TIP_PRESETS), "custom_tips_allowed": True,
    "tips_thanks_text": "",
    "linklock_enabled": False, "antiflood_enabled": False, "antiflood_rate": 10, "antiflood_window": 15,
    "service_cleaner_enabled": True,
}


def _parse_tip_presets(raw) -> list:
    """Lee los presets guardados (JSON); ante cualquier dato corrupto devuelve los presets por defecto."""
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
        clean = sorted({int(v) for v in values if 1 <= int(v) <= TIP_MAX_STARS})
        return clean[:6] if clean else list(DEFAULT_TIP_PRESETS)
    except (TypeError, ValueError):
        return list(DEFAULT_TIP_PRESETS)


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("1", "true", "on", "yes", "si", "sí"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("0", "false", "off", "no"):
        return False
    raise ValueError("debe ser verdadero o falso.")


def _coerce_int_range(value, lo: int, hi: int, unit: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"debe ser un número entero entre {lo} y {hi}{unit}.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"debe ser un número entero entre {lo} y {hi}{unit}.")
    if not lo <= number <= hi:
        raise ValueError(f"debe estar entre {lo} y {hi}{unit}.")
    return number


def _coerce_tip_presets(value) -> list:
    if isinstance(value, str):
        parts = [p for p in re.split(r"[\s,;]+", value.strip()) if p]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError("usa una lista de montos separados por coma (ej. 15, 50, 100).")
    if not 1 <= len(parts) <= 6:
        raise ValueError("indica entre 1 y 6 montos.")
    amounts = set()
    for part in parts:
        amounts.add(_coerce_int_range(part, 1, TIP_MAX_STARS, " Stars"))
    return sorted(amounts)


def _coerce_welcome(value) -> str:
    text = "" if value is None else str(value).replace("\r\n", "\n").strip()
    if len(text) > WELCOME_MAX:
        raise ValueError(f"máximo {WELCOME_MAX} caracteres.")
    unknown = sorted({v for v in _WELCOME_VAR_RE.findall(text) if v not in WELCOME_VARIABLES})
    if unknown:
        allowed = ", ".join("{" + v + "}" for v in WELCOME_VARIABLES)
        raise ValueError(f"variable desconocida {{{unknown[0]}}}; usa solo {allowed}.")
    return text


class _TelegramHTMLCheck(HTMLParser):
    """Comprueba que el texto solo use etiquetas HTML de Telegram, bien anidadas y cerradas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.error = None

    def handle_starttag(self, tag, attrs):
        if self.error:
            return
        if tag not in _TG_HTML_TAGS:
            self.error = f"etiqueta <{tag}> no admitida por Telegram."
            return
        allowed = _TG_HTML_ATTRS.get(tag, set())
        for name, attr_value in attrs:
            if name not in allowed:
                self.error = f"atributo '{name}' no admitido en <{tag}>."
                return
            if tag == "a" and name == "href":
                href = (attr_value or "").strip().lower()
                if not href.startswith(("http://", "https://", "tg://")):
                    self.error = "los enlaces <a href> deben usar http(s):// o tg://."
                    return
            if tag == "span" and name == "class" and attr_value != "tg-spoiler":
                self.error = "<span> solo admite class=\"tg-spoiler\"."
                return
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.error = self.error or f"etiqueta <{tag}/> no admitida por Telegram."

    def handle_endtag(self, tag):
        if self.error:
            return
        if not self.stack or self.stack[-1] != tag:
            self.error = f"cierre </{tag}> sin abrir o mal anidado."
            return
        self.stack.pop()


def _coerce_tips_thanks(value) -> str:
    """
    Agradecimiento de propinas: hasta 3000 caracteres, HTML de Telegram válido y solo las
    variables {stars}, {mention}, {user_name} y {community}. "" o None restaura el texto oficial.
    """
    text = "" if value is None else str(value).replace("\r\n", "\n").strip()
    if not text:
        return ""
    if len(text) > TIPS_THANKS_MAX:
        raise ValueError(f"máximo {TIPS_THANKS_MAX} caracteres.")
    accepted = set(TIPS_THANKS_VARIABLES) | set(_TIPS_THANKS_VAR_ALIASES)
    unknown = sorted({v for v in _WELCOME_VAR_RE.findall(text) if v not in accepted})
    if unknown:
        allowed = ", ".join("{" + v + "}" for v in TIPS_THANKS_VARIABLES)
        raise ValueError(f"variable desconocida {{{unknown[0]}}}; usa solo {allowed}.")
    checker = _TelegramHTMLCheck()
    try:
        checker.feed(text)
        checker.close()
    except Exception:
        raise ValueError("el formato HTML no es válido.")
    if checker.error:
        raise ValueError(checker.error)
    if checker.stack:
        raise ValueError(f"falta cerrar la etiqueta <{checker.stack[-1]}>.")
    # Un '<' o '&' suelto (p. ej. "1 < 2") haría fallar parse_mode=HTML: se exige escaparlo.
    if re.search(r"<(?![a-zA-Z/])", text) or re.search(r"&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)", text):
        raise ValueError("escapa los símbolos sueltos: usa &lt; para '<' y &amp; para '&'.")
    return text


def validate_chat_configuration(payload: dict) -> dict:
    """
    Valida y normaliza una configuración (parcial o completa). Acepta el árbol por secciones
    ({"aduana": {...}, "tips": {...}}) o los campos en plano. Devuelve {campo: valor_api}.
    Lanza ChatConfigError con TODOS los campos inválidos; los desconocidos también son error
    (un typo nunca debe "guardarse" en silencio sin efecto).
    """
    if not isinstance(payload, dict):
        raise ChatConfigError({"configuration": "se esperaba un objeto JSON."})
    flat = {}
    for key, value in payload.items():
        if key in CHAT_CONFIG_SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value

    clean, errors = {}, {}
    for field, value in flat.items():
        spec = CHAT_CONFIG_FIELDS.get(field)
        if spec is None:
            errors[field] = "campo desconocido."
            continue
        kind = spec[2]
        try:
            if kind == "bool":
                clean[field] = _coerce_bool(value)
            elif kind == "captcha_mode":
                mode = str(value or "").strip().lower()
                if mode not in CAPTCHA_MODE_CODES:
                    raise ValueError("usa 'button' o 'math'.")
                clean[field] = mode
            elif kind == "captcha_timeout":
                clean[field] = _coerce_int_range(value, *CAPTCHA_TIMEOUT_RANGE, " s")
            elif kind == "welcome":
                clean[field] = _coerce_welcome(value)
            elif kind == "tips_thanks":
                clean[field] = _coerce_tips_thanks(value)
            elif kind == "autolower_pct":
                clean[field] = _coerce_int_range(value, *AUTOLOWER_PCT_RANGE, " %")
            elif kind == "stars":
                clean[field] = _coerce_int_range(value, *STARS_PRICE_RANGE, " Stars")
            elif kind == "tip_presets":
                clean[field] = _coerce_tip_presets(value)
            elif kind == "antiflood_rate":
                clean[field] = _coerce_int_range(value, *ANTIFLOOD_RATE_RANGE, " mensajes")
            elif kind == "antiflood_window":
                clean[field] = _coerce_int_range(value, *ANTIFLOOD_WINDOW_RANGE, " s")
        except ValueError as exc:
            errors[field] = str(exc)
    if errors:
        raise ChatConfigError(errors)
    if not clean:
        raise ChatConfigError({"configuration": "no hay ningún campo que guardar."})
    return clean


def _to_db_value(field: str, value):
    kind = CHAT_CONFIG_FIELDS[field][2]
    if kind == "bool":
        return 1 if value else 0
    if kind == "captcha_mode":
        return CAPTCHA_MODE_CODES[value]
    if kind == "tip_presets":
        return json.dumps(value)
    if kind in ("welcome", "tips_thanks"):
        return value or None          # "" → NULL: el bot vuelve al texto oficial
    return int(value)


def _from_db_value(field: str, raw):
    default = CHAT_CONFIG_DEFAULTS[field]
    if raw is None:
        return list(default) if isinstance(default, list) else default
    kind = CHAT_CONFIG_FIELDS[field][2]
    try:
        if kind == "bool":
            return bool(int(raw))
        if kind == "captcha_mode":
            return CAPTCHA_MODE_NAMES.get(int(raw), "button")
        if kind == "tip_presets":
            return _parse_tip_presets(raw)
        if kind in ("welcome", "tips_thanks"):
            return str(raw)
        return int(raw)
    except (TypeError, ValueError):
        return list(default) if isinstance(default, list) else default


def _read_chat_configuration(conn, chat_id: int) -> dict:
    columns = [spec[1] for spec in CHAT_CONFIG_FIELDS.values()]
    row = conn.execute(
        f"SELECT {', '.join(_ident(c) for c in columns)}, config_updated_at, config_updated_by "
        "FROM group_settings WHERE group_id = ?", (int(chat_id),)
    ).fetchone()
    tree = {section: {} for section in CHAT_CONFIG_SECTIONS}
    for index, (field, (section, _column, _kind)) in enumerate(CHAT_CONFIG_FIELDS.items()):
        raw = row[index] if row else None
        tree[section][field] = _from_db_value(field, raw)
    # Precio de MicVIP: el bot cobra mic_vip_custom_price si existe (get_mic_vip_custom_config).
    if row:
        custom = conn.execute(
            "SELECT mic_vip_custom_price FROM group_settings WHERE group_id = ?", (int(chat_id),)
        ).fetchone()
        if custom and custom[0] is not None and int(custom[0]) > 0:
            tree["acoustic"]["micvip_price"] = int(custom[0])
    # v15 · Matriz multimedia (30 vectores) para la Mini App. ui.renderChatConfiguration ignora secciones que
    # no estén en su CONFIG_LAYOUT, así que añadirla no altera la consola actual.
    try:
        media_state = _media_locks_state_sync(conn, int(chat_id))
        tree["media_locks"] = {
            "rules": media_state["rules"],
            "long_video_max_seconds": media_state["long_video_max_seconds"],
            "floor": sorted(media_state["floor"]),
            "keys": list(MEDIA_LOCK_KEYS),
            "actions": list(MEDIA_LOCK_ACTIONS),
        }
    except sqlite3.OperationalError:
        pass
    tree["meta"] = {
        "chat_id": str(chat_id),
        "updated_at": row[len(columns)] if row else None,
        "updated_by": row[len(columns) + 1] if row else None,
        "welcome_variables": list(WELCOME_VARIABLES),
        "tips_thanks_variables": list(TIPS_THANKS_VARIABLES),
        "tips_thanks_max": TIPS_THANKS_MAX,
    }
    return tree


@db_async
def get_chat_full_configuration(chat_id: int) -> dict:
    """Árbol completo {aduana, acoustic, tips, perimeter, meta} con los valores que usa el bot."""
    with get_db_connection() as conn:
        try:
            return _read_chat_configuration(conn, chat_id)
        except sqlite3.OperationalError:
            tree = {section: {} for section in CHAT_CONFIG_SECTIONS}
            for field, (section, _c, _k) in CHAT_CONFIG_FIELDS.items():
                default = CHAT_CONFIG_DEFAULTS[field]
                tree[section][field] = list(default) if isinstance(default, list) else default
            tree["meta"] = {"chat_id": str(chat_id), "updated_at": None, "updated_by": None,
                            "welcome_variables": list(WELCOME_VARIABLES),
                            "tips_thanks_variables": list(TIPS_THANKS_VARIABLES),
                            "tips_thanks_max": TIPS_THANKS_MAX}
            return tree


@db_async
def update_chat_full_configuration(chat_id: int, config_dict: dict, updated_by: int = None) -> dict:
    """
    Valida y persiste en lote dentro de UNA transacción BEGIN IMMEDIATE (todo o nada) y devuelve el
    árbol resultante. El precio de MicVIP se escribe también en mic_vip_custom_price, igual que
    set_mic_vip_price, para que el bot cobre exactamente lo configurado.
    """
    # v15 · La matriz multimedia viaja en su propia sección y se valida aparte (estructura de reglas por vector).
    config_dict = dict(config_dict) if isinstance(config_dict, dict) else config_dict
    media_payload = config_dict.pop("media_locks", None) if isinstance(config_dict, dict) else None
    media_rules, media_seconds = validate_media_locks_payload(media_payload) if media_payload is not None else ({}, None)
    if media_payload is not None and not config_dict:
        clean = {}
    else:
        clean = validate_chat_configuration(config_dict)
    assignments, params = [], []
    for field, value in clean.items():
        column = _ident(CHAT_CONFIG_FIELDS[field][1])
        assignments.append(f"{column} = ?")
        params.append(_to_db_value(field, value))
        if field == "micvip_price":
            assignments.append("mic_vip_custom_price = ?")
            params.append(int(value))
    assignments.append("config_updated_at = ?")
    params.append(int(datetime.now(timezone.utc).timestamp()))
    assignments.append("config_updated_by = ?")
    params.append(int(updated_by) if updated_by is not None else None)

    with _write_transaction() as conn:
        conn.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (int(chat_id),))
        if "linklock_enabled" in clean:
            _push_legacy_lock_to_matrix(conn, int(chat_id), "lock_links", 1 if clean["linklock_enabled"] else 0)
        conn.execute(f"UPDATE group_settings SET {', '.join(assignments)} WHERE group_id = ?", (*params, int(chat_id)))
        if media_rules:
            _apply_media_rules_sync(conn, int(chat_id), media_rules)
        if media_seconds is not None:
            conn.execute("UPDATE group_settings SET long_video_max_seconds = ? WHERE group_id = ?", (media_seconds, int(chat_id)))
        return _read_chat_configuration(conn, chat_id)


@db_async
def get_autolower_pct(group_id: int) -> int:
    """Porcentaje de atenuación de AutoLower (1-10 %) configurado desde la Mini App."""
    value = _as_int(_get_setting(group_id, "autolower_pct", 2), 2)
    return max(AUTOLOWER_PCT_RANGE[0], min(AUTOLOWER_PCT_RANGE[1], value))


@db_async
def get_user_global_stats(user_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_groups WHERE user_id = ?", (user_id,))
        total_chats = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(s.id) FROM channel_subscriptions s
            JOIN user_groups ug ON s.channel_id = ug.group_id
            WHERE ug.user_id = ? AND s.status = 'active' AND s.expires_at > datetime('now')
        """, (user_id,))
        vip_subs = cursor.fetchone()[0]

        cursor.execute("""
            SELECT SUM(s.stars_paid) FROM channel_subscriptions s
            JOIN user_groups ug ON s.channel_id = ug.group_id
            WHERE ug.user_id = ?
        """, (user_id,))
        rev_stars = cursor.fetchone()[0] or 0

        return {
            "subscribers": vip_subs,
            "revenue_stars": rev_stars,
            "verified": total_chats,
            "expelled": 0,
            "purges": 0,
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


@db_async
def get_user_subscribers_audit(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.user_id, COALESCE(p.plan_name, 'Plan eliminado'), s.stars_paid,
                   CAST((julianday(s.expires_at) - julianday('now')) AS INTEGER) as days_left,
                   u.username
            FROM channel_subscriptions s
            LEFT JOIN channel_plans p ON s.plan_id = p.plan_id
            JOIN user_groups ug ON s.channel_id = ug.group_id
            LEFT JOIN users u ON s.user_id = u.user_id
            WHERE ug.user_id = ? AND s.status = 'active'
            ORDER BY s.expires_at ASC
        """, (user_id,))
        rows = cursor.fetchall()
        return [
            {
                "user_id": r[0],
                "username": r[4] if r[4] else str(r[0]),
                "plan_name": r[1],
                "price": r[2],
                "days_left": max(0, r[3]) if r[3] is not None else 0
            }
            for r in rows
        ]


# ==========================================
# 💳 PAGOS Y BÓVEDA CRIPTOGRÁFICA (.BUNKER)
# ==========================================
@db_async
def mark_payment_processed(charge_id: str, user_id: int, payload: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO processed_payments (charge_id, user_id, payload) VALUES (?, ?, ?)",
                (charge_id, user_id, payload)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            if conn.in_transaction:
                conn.rollback()
            return False


BACKUP_SECRET_SALT = os.getenv("BACKUP_SECRET_SALT", "bunker-secret-vault-2026")
if "BACKUP_SECRET_SALT" not in os.environ:
    logger.warning("⚠️ [DB] BACKUP_SECRET_SALT no definido; se usa la sal por defecto (configúrala en Railway).")

# Columnas de estado en tiempo de ejecución que NO deben viajar entre grupos en un backup.
_IMPORT_EXCLUDED_COLUMNS = {"group_id", "panic_active", "night_mode_status", "purge_last_free_scan", "tips_return_link"}
_IMPORT_ALLOWED_TYPES = (int, float, str, type(None))


@db_async
def export_group_configuration(group_id: int) -> str:
    with get_db_connection() as conn:
        # row_factory a nivel de CURSOR: asignarlo a la conexión compartida del hilo
        # contaminaría todas las consultas posteriores de ese hilo.
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
        settings_row = cursor.fetchone()
        settings_dict = dict(settings_row) if settings_row else {}
        settings_dict.pop("group_id", None)
        cursor.execute("SELECT days, start_time, end_time, status FROM vc_schedules WHERE group_id = ?", (group_id,))
        sched_row = cursor.fetchone()
        sched_dict = dict(sched_row) if sched_row else {}
        cursor.execute("SELECT target_value FROM group_tip_targets WHERE group_id = ?", (group_id,))
        targets = [r[0] for r in cursor.fetchall()]

    payload = {
        "version": "6.0", "exported_at": datetime.now().isoformat(),
        "source_group_id": group_id, "settings": settings_dict,
        "vc_schedule": sched_dict, "tip_targets": targets,
        "reputation_enabled": _read_reputation_settings(group_id)["enabled"],
        "reputation_xp_multiplier": _read_reputation_settings(group_id)["multiplier"],
    }
    raw_data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(BACKUP_SECRET_SALT.encode("utf-8"), raw_data.encode("utf-8"), hashlib.sha256).hexdigest()
    return json.dumps({"payload": payload, "signature": signature}, indent=2, ensure_ascii=False)


@db_async
def import_group_configuration(target_group_id: int, backup_json: str) -> tuple[bool, str]:
    try:
        package = json.loads(backup_json)
        if not isinstance(package, dict):
            return False, "Estructura de paquete inválida."
        payload = package.get("payload")
        received_sig = package.get("signature")
        if not isinstance(payload, dict) or not isinstance(received_sig, str) or not received_sig:
            return False, "Estructura de paquete inválida."
        raw_data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_sig = hmac.new(BACKUP_SECRET_SALT.encode("utf-8"), raw_data.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, received_sig):
            return False, "Firma digital no válida."

        settings = payload.get("settings") or {}
        if not isinstance(settings, dict):
            return False, "Bloque de ajustes inválido."
        vc_schedule = payload.get("vc_schedule") or {}
        tip_targets = payload.get("tip_targets") or []

        with _write_transaction() as conn:
            cursor = conn.cursor()
            existing_cols = _table_columns(cursor, "group_settings")
            clean_cols = [
                c for c in settings.keys()
                if isinstance(c, str)
                and _IDENT_RE.match(c)
                and c in existing_cols
                and c not in _IMPORT_EXCLUDED_COLUMNS
                and isinstance(settings[c], _IMPORT_ALLOWED_TYPES)
            ]
            cursor.execute("INSERT INTO group_settings (group_id) VALUES (?) ON CONFLICT(group_id) DO NOTHING", (target_group_id,))
            if clean_cols:
                placeholders = ", ".join([f"{col} = ?" for col in clean_cols])
                values = [settings[col] for col in clean_cols]
                values.append(target_group_id)
                cursor.execute(f"UPDATE group_settings SET {placeholders} WHERE group_id = ?", tuple(values))

            if isinstance(vc_schedule, dict) and vc_schedule:
                cursor.execute("""
                    INSERT INTO vc_schedules (group_id, days, start_time, end_time, status) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        days = excluded.days,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        status = excluded.status
                """, (
                    target_group_id,
                    str(vc_schedule.get("days") or "1,2,3,4,5,6,7"),
                    str(vc_schedule.get("start_time") or "20:00"),
                    str(vc_schedule.get("end_time") or "23:00"),
                    _as_int(vc_schedule.get("status"), 0),
                ))

            if isinstance(tip_targets, list):
                cursor.executemany(
                    "INSERT OR IGNORE INTO group_tip_targets (group_id, target_value, is_active) VALUES (?, ?, 1)",
                    [(target_group_id, str(t).strip()) for t in tip_targets if isinstance(t, (str, int)) and str(t).strip()],
                )
            conn.commit()
        return True, "Configuración importada con éxito."
    except Exception as ex:
        logger.exception("❌ [DB] Falló import_group_configuration")
        return False, f"Error durante la restauración: {ex}"


# ==========================================================
# 🎮 ANUNCIOS RECURRENTES DE GAMIFICACIÓN INTERACTIVA
# ==========================================================
GAMI_INTERVAL_MIN_MINUTES = 30
GAMI_INTERVAL_MAX_MINUTES = 10080          # 7 días
GAMI_DEFAULT_INTERVAL_MINUTES = 360        # 6 horas
GAMI_TEXT_MAX_CHARS = 3500
GAMI_BUTTON_TEXT_MAX_CHARS = 40
GAMI_URL_MAX_CHARS = 512
GAMI_AUTODEL_MAX_SECONDS = 86400
GAMI_MAX_CONSECUTIVE_FAILURES = 5
GAMI_LEASE_MINUTES = 10                    # Reserva de envío: evita duplicados si el proceso se reinicia a mitad
GAMI_MEDIA_TYPES = {"photo", "video", "animation"}
GAMI_CARD_LANGS = {"es", "en", "both"}
GAMI_SIGNATURE = "🛡️ <i>Cloud Media Management</i>"

_GAMI_COLUMNS = (
    "chat_id", "status", "interval_minutes", "text_es", "text_en", "media_id", "media_type",
    "btn_text_es", "btn_text_en", "btn_url", "card_lang", "auto_delete_after",
    "last_sent_at", "next_send_at", "last_message_id", "fail_count", "created_by", "updated_at"
)
_GAMI_EDITABLE_FIELDS = {
    "status", "interval_minutes", "text_es", "text_en", "media_id", "media_type",
    "btn_text_es", "btn_text_en", "btn_url", "card_lang", "auto_delete_after", "created_by"
}

GAMI_DEFAULT_TEXTS = {
    "es": (
        "🎮 <b>¡Gana XP y sube de rango!</b>\n\n"
        "Cada mensaje respetuoso en la comunidad suma experiencia. Desbloquea niveles, "
        "escala en la tabla de honor y presume tu rango.\n\n"
        "• <code>/rank</code> — consulta tu nivel\n"
        "• <code>/top</code> — tabla de líderes"
    ),
    "en": (
        "🎮 <b>Earn XP and rank up!</b>\n\n"
        "Every respectful message in the community earns experience. Unlock levels, "
        "climb the leaderboard and show off your rank.\n\n"
        "• <code>/rank</code> — check your level\n"
        "• <code>/top</code> — leaderboard"
    ),
}
GAMI_DEFAULT_BUTTON_TEXTS = {"es": "🚀 Participar", "en": "🚀 Join in"}

_GAMI_TME_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_GAMI_TME_PATH_RE = re.compile(r"^[A-Za-z0-9_+\-/=?&.%]{1,256}$")
_GAMI_HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,63}$")


def normalize_announcement_url(raw) -> str | None:
    """
    Valida y normaliza el destino del botón del anuncio. Acepta:
      • @usuario / usuario de grupo o canal público           → https://t.me/usuario
      • t.me/..., telegram.me/... (invitaciones +hash, joinchat, mensajes /c/...) → https://t.me/...
      • URLs web http(s) con dominio válido (sin credenciales ni espacios)
    Rechaza esquemas peligrosos (javascript:, data:, file:, tg:...), IPs locales y textos inválidos.
    Devuelve la URL lista para un InlineKeyboardButton o None si no es segura.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value or len(value) > GAMI_URL_MAX_CHARS or any(ch.isspace() for ch in value):
        return None

    if value.startswith("@"):
        username = value[1:]
        return f"https://t.me/{username}" if _GAMI_TME_USERNAME_RE.match(username) else None

    lowered = value.lower()
    for prefix in ("https://", "http://"):
        if lowered.startswith(prefix):
            break
    else:
        if lowered.startswith(("t.me/", "telegram.me/", "www.t.me/")):
            value = "https://" + value
            lowered = value.lower()
        elif _GAMI_TME_USERNAME_RE.match(value):
            return f"https://t.me/{value}"
        elif "://" in value or lowered.startswith(("javascript:", "data:", "file:", "tg:", "mailto:")):
            return None
        else:
            value = "https://" + value
            lowered = value.lower()

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.username or parsed.password or "@" in parsed.netloc:
        return None
    host = (parsed.hostname or "").lower()

    if host in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
        path = parsed.path.lstrip("/")
        full_path = path + (f"?{parsed.query}" if parsed.query else "")
        if not path or not _GAMI_TME_PATH_RE.match(full_path):
            return None
        return f"https://t.me/{full_path}"

    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return None
    if not _GAMI_HOST_RE.match(host):
        return None   # Se exige un dominio público (sin IPs ni nombres internos)
    if parsed.port is not None and parsed.port not in (80, 443):
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "", parsed.query, parsed.fragment))


def normalize_announcement_button_text(raw) -> str | None:
    """Texto del botón: 1–40 caracteres, una sola línea."""
    if raw is None:
        return None
    value = " ".join(str(raw).split())
    if not value:
        return None
    return value[:GAMI_BUTTON_TEXT_MAX_CHARS]


def _gami_defaults(chat_id: int) -> dict:
    return {
        "chat_id": chat_id, "status": 0, "interval_minutes": GAMI_DEFAULT_INTERVAL_MINUTES,
        "text_es": None, "text_en": None, "media_id": None, "media_type": None,
        "btn_text_es": None, "btn_text_en": None, "btn_url": None, "card_lang": "es",
        "auto_delete_after": 0, "last_sent_at": None, "next_send_at": None,
        "last_message_id": None, "fail_count": 0, "created_by": None, "updated_at": None,
        "exists": False,
    }


def _gami_row_to_dict(row) -> dict:
    data = dict(zip(_GAMI_COLUMNS, row))
    base = _gami_defaults(data["chat_id"])
    for key, value in data.items():
        if value is not None:
            base[key] = value
    base["exists"] = True
    return base


def _gami_validate(field: str, value):
    """Normaliza y valida cada campo editable. Lanza ValueError si el valor no es aceptable."""
    if field == "status":
        return 1 if int(value) == 1 else 0
    if field == "interval_minutes":
        return max(GAMI_INTERVAL_MIN_MINUTES, min(GAMI_INTERVAL_MAX_MINUTES, int(value)))
    if field in ("text_es", "text_en"):
        if value is None:
            return None
        text = str(value).strip()
        if len(text) > GAMI_TEXT_MAX_CHARS:
            raise ValueError("text_too_long")
        return text or None
    if field == "media_id":
        return str(value).strip() if value else None
    if field == "media_type":
        if value is None:
            return None
        media_type = str(value).strip().lower()
        if media_type not in GAMI_MEDIA_TYPES:
            raise ValueError("invalid_media_type")
        return media_type
    if field in ("btn_text_es", "btn_text_en"):
        return normalize_announcement_button_text(value)
    if field == "btn_url":
        if value is None:
            return None
        normalized = normalize_announcement_url(value)
        if not normalized:
            raise ValueError("invalid_url")
        return normalized
    if field == "card_lang":
        lang = str(value or "es").strip().lower()
        if lang not in GAMI_CARD_LANGS:
            raise ValueError("invalid_lang")
        return lang
    if field == "auto_delete_after":
        return max(0, min(GAMI_AUTODEL_MAX_SECONDS, int(value or 0)))
    if field == "created_by":
        return int(value) if value else None
    raise ValueError("invalid_field")


@db_async
def get_gamification_announcement(chat_id: int) -> dict:
    """Configuración del anuncio de gamificación (con valores por defecto si aún no existe)."""
    with get_db_connection() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_GAMI_COLUMNS)} FROM gamification_announcements WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
    return _gami_row_to_dict(row) if row else _gami_defaults(chat_id)


@db_async
def set_gamification_announcement_field(chat_id: int, field: str, value) -> bool:
    """
    Guarda un campo del anuncio. Devuelve False si el campo o el valor no son válidos.
    • Activar el anuncio lo programa para el siguiente ciclo del worker (≈1 min) y reinicia los fallos.
    • Cambiar la frecuencia recalcula el próximo envío desde el último envío realizado.
    """
    if field not in _GAMI_EDITABLE_FIELDS:
        return False
    try:
        clean_value = _gami_validate(field, value)
    except (TypeError, ValueError):
        return False

    column = _ident(field)
    with _write_transaction() as conn:
        conn.execute(
            f"INSERT INTO gamification_announcements (chat_id, {column}, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(chat_id) DO UPDATE SET {column} = excluded.{column}, updated_at = CURRENT_TIMESTAMP",
            (chat_id, clean_value)
        )
        if field == "status" and clean_value == 1:
            conn.execute(
                "UPDATE gamification_announcements SET next_send_at = datetime('now', '+1 minutes'), fail_count = 0 "
                "WHERE chat_id = ?",
                (chat_id,)
            )
        elif field == "interval_minutes":
            conn.execute(
                "UPDATE gamification_announcements SET next_send_at = "
                "datetime(COALESCE(last_sent_at, datetime('now')), ?) WHERE chat_id = ?",
                (_sql_modifier(clean_value, "minutes"), chat_id)
            )
        conn.commit()
    return True


@db_async
def set_gamification_announcement_media(chat_id: int, media_id: str | None, media_type: str | None) -> bool:
    """Guarda (o elimina, con None) el multimedia del anuncio en una sola operación atómica."""
    if media_id and (media_type or "").lower() not in GAMI_MEDIA_TYPES:
        return False
    clean_id = str(media_id).strip() if media_id else None
    clean_type = media_type.lower() if (clean_id and media_type) else None
    with _write_transaction() as conn:
        conn.execute(
            "INSERT INTO gamification_announcements (chat_id, media_id, media_type, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(chat_id) DO UPDATE SET media_id = excluded.media_id, media_type = excluded.media_type, "
            "updated_at = CURRENT_TIMESTAMP",
            (chat_id, clean_id, clean_type)
        )
        conn.commit()
    return True


@db_async
def clear_gamification_announcement_button(chat_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE gamification_announcements SET btn_text_es = NULL, btn_text_en = NULL, btn_url = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,)
        )
        conn.commit()


@db_async
def schedule_gamification_announcement_now(chat_id: int) -> bool:
    """Adelanta el próximo envío al siguiente ciclo del worker (solo si el anuncio está activo)."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE gamification_announcements SET next_send_at = datetime('now'), fail_count = 0 "
            "WHERE chat_id = ? AND status = 1",
            (chat_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


@db_async
def reset_gamification_announcement(chat_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM gamification_announcements WHERE chat_id = ?", (chat_id,))
        conn.commit()


@db_async
def get_due_gamification_announcements(limit: int = 20) -> list:
    """
    Reserva y devuelve los anuncios activos cuyo envío ya venció. Cada fila reservada se aplaza
    GAMI_LEASE_MINUTES dentro de la misma transacción (BEGIN IMMEDIATE): aunque el worker se
    reinicie a mitad de ciclo o coexistan dos procesos, ninguna tarjeta se publica dos veces.
    """
    limit = max(1, min(100, int(limit)))
    with _write_transaction() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_GAMI_COLUMNS)} FROM gamification_announcements "
            "WHERE status = 1 AND (next_send_at IS NULL OR next_send_at <= datetime('now')) "
            "ORDER BY COALESCE(next_send_at, '1970-01-01') ASC LIMIT ?",
            (limit,)
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE gamification_announcements SET next_send_at = datetime('now', ?) WHERE chat_id = ?",
                [(_sql_modifier(GAMI_LEASE_MINUTES, "minutes"), row[0]) for row in rows]
            )
        conn.commit()
    return [_gami_row_to_dict(row) for row in rows]


@db_async
def mark_gamification_announcement_sent(chat_id: int, message_id: int | None, interval_minutes: int) -> None:
    interval = max(GAMI_INTERVAL_MIN_MINUTES, min(GAMI_INTERVAL_MAX_MINUTES, int(interval_minutes)))
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE gamification_announcements SET last_sent_at = datetime('now'), "
            "next_send_at = datetime('now', ?), last_message_id = ?, fail_count = 0 WHERE chat_id = ?",
            (_sql_modifier(interval, "minutes"), message_id, chat_id)
        )
        conn.commit()


@db_async
def reschedule_gamification_announcement(chat_id: int, minutes: int) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE gamification_announcements SET next_send_at = datetime('now', ?) WHERE chat_id = ?",
            (_sql_modifier(max(1, int(minutes)), "minutes"), chat_id)
        )
        conn.commit()


@db_async
def register_gamification_announcement_failure(chat_id: int, permanent: bool = False) -> bool:
    """
    Registra un fallo de envío. Devuelve True si el anuncio quedó desactivado (fallo permanente
    o GAMI_MAX_CONSECUTIVE_FAILURES seguidos). Los fallos transitorios se reintentan con espera
    exponencial (15 min, 30 min, 60 min… hasta 6 h).
    """
    with _write_transaction() as conn:
        row = conn.execute(
            "SELECT COALESCE(fail_count, 0) FROM gamification_announcements WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        if not row:
            return False
        failures = int(row[0]) + 1
        disable = permanent or failures >= GAMI_MAX_CONSECUTIVE_FAILURES
        if disable:
            conn.execute(
                "UPDATE gamification_announcements SET status = 0, fail_count = ?, next_send_at = NULL WHERE chat_id = ?",
                (failures, chat_id)
            )
        else:
            backoff = min(360, 15 * (2 ** (failures - 1)))
            conn.execute(
                "UPDATE gamification_announcements SET fail_count = ?, next_send_at = datetime('now', ?) WHERE chat_id = ?",
                (failures, _sql_modifier(backoff, "minutes"), chat_id)
            )
        conn.commit()
    return disable


def _gami_pick(primary, secondary):
    return primary if primary else secondary


def compose_gamification_card(cfg: dict) -> dict:
    """
    Construye el contenido final de la tarjeta (independiente de Aiogram):
      text, button_text, button_url, media_id, media_type.
    Idioma gobernado por card_lang: "es", "en" o "both" (bilingüe en una sola tarjeta).
    Si falta el texto del idioma pedido se usa el otro; si no hay ninguno, la plantilla oficial.
    La firma institucional se añade siempre que el texto no la incluya.
    """
    card_lang = (cfg.get("card_lang") or "es").lower()
    text_es = (cfg.get("text_es") or "").strip()
    text_en = (cfg.get("text_en") or "").strip()

    if card_lang == "both":
        part_es = text_es or GAMI_DEFAULT_TEXTS["es"]
        part_en = text_en or GAMI_DEFAULT_TEXTS["en"]
        text = part_es if part_es == part_en else f"🇪🇸 {part_es}\n\n➖➖➖➖➖\n\n🇬🇧 {part_en}"
    elif card_lang == "en":
        text = _gami_pick(text_en, text_es) or GAMI_DEFAULT_TEXTS["en"]
    else:
        text = _gami_pick(text_es, text_en) or GAMI_DEFAULT_TEXTS["es"]

    if "Cloud Media Management" not in text:
        text = f"{text}\n\n{GAMI_SIGNATURE}"

    button_url = cfg.get("btn_url") or None
    button_text = None
    if button_url:
        btn_es = (cfg.get("btn_text_es") or "").strip()
        btn_en = (cfg.get("btn_text_en") or "").strip()
        if card_lang == "both":
            if btn_es and btn_en and btn_es != btn_en:
                button_text = f"{btn_es} | {btn_en}"
            else:
                button_text = btn_es or btn_en or f"{GAMI_DEFAULT_BUTTON_TEXTS['es']} | {GAMI_DEFAULT_BUTTON_TEXTS['en']}"
        elif card_lang == "en":
            button_text = _gami_pick(btn_en, btn_es) or GAMI_DEFAULT_BUTTON_TEXTS["en"]
        else:
            button_text = _gami_pick(btn_es, btn_en) or GAMI_DEFAULT_BUTTON_TEXTS["es"]
        button_text = button_text[:64]

    media_id = cfg.get("media_id") or None
    media_type = (cfg.get("media_type") or "").lower() or None
    if media_type not in GAMI_MEDIA_TYPES:
        media_id, media_type = None, None

    return {
        "text": text,
        "button_text": button_text,
        "button_url": button_url,
        "media_id": media_id,
        "media_type": media_type,
    }


# ==========================================
# 🛡️ v9 · BLOQUEOS DEL SISTEMA DE SEGURIDAD (ANALÍTICA EN VIVO)
# ==========================================
SECURITY_SOURCES = ("captcha", "antiflood", "antispam", "ai_guardian", "raid_shield")
SECURITY_BLOCK_ACTIONS = ("decline", "kick", "ban", "mute", "delete")
SECURITY_EXPEL_ACTIONS = ("decline", "kick", "ban")
SECURITY_PASS_ACTION = "verified"            # captcha superado: no es un bloqueo, alimenta la tasa de acierto
SECURITY_RETENTION_DAYS = 90
_SECURITY_PRUNE_EVERY = 500
_security_insert_counter = 0


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


@db_async
def record_security_event(group_id: int, user_id, source: str, action: str, is_bot: bool = False) -> bool:
    """
    Registra un bloqueo/expulsión automática. source ∈ SECURITY_SOURCES; action ∈ SECURITY_BLOCK_ACTIONS o
    'verified'. Valores desconocidos se descartan (devuelve False) para que ningún llamador ensucie la métrica.
    """
    global _security_insert_counter
    src = str(source or "").strip().lower()
    act = str(action or "").strip().lower()
    if src not in SECURITY_SOURCES or (act not in SECURITY_BLOCK_ACTIONS and act != SECURITY_PASS_ACTION):
        return False
    with _write_transaction() as conn:
        conn.execute(
            "INSERT INTO security_events (group_id, user_id, is_bot, source, action, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(group_id), int(user_id) if user_id else None, 1 if is_bot else 0, src, act, _now_ts()),
        )
        _security_insert_counter += 1
        if _security_insert_counter % _SECURITY_PRUNE_EVERY == 0:
            conn.execute("DELETE FROM security_events WHERE created_at < ?",
                         (_now_ts() - SECURITY_RETENTION_DAYS * 86400,))
    return True


def _security_summary_sync(conn, group_id: int, since_ts: int) -> dict:
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in SECURITY_BLOCK_ACTIONS)
    by_source = {src: {"total": 0, "expelled": 0, "users": 0, "bots": 0} for src in SECURITY_SOURCES}
    by_action = {act: 0 for act in SECURITY_BLOCK_ACTIONS}
    total = expelled = users = bots = 0
    cursor.execute(f"""
        SELECT source, action, is_bot, COUNT(*) FROM security_events
        WHERE group_id = ? AND created_at >= ? AND action IN ({placeholders})
        GROUP BY source, action, is_bot
    """, (int(group_id), int(since_ts), *SECURITY_BLOCK_ACTIONS))
    for src, act, is_bot, count in cursor.fetchall():
        count = int(count or 0)
        bucket = by_source.setdefault(src, {"total": 0, "expelled": 0, "users": 0, "bots": 0})
        bucket["total"] += count
        by_action[act] = by_action.get(act, 0) + count
        total += count
        if act in SECURITY_EXPEL_ACTIONS:
            bucket["expelled"] += count
            expelled += count
        if is_bot:
            bucket["bots"] += count
            bots += count
        else:
            bucket["users"] += count
            users += count
    cursor.execute(f"""
        SELECT COUNT(DISTINCT user_id) FROM security_events
        WHERE group_id = ? AND created_at >= ? AND action IN ({placeholders}) AND user_id IS NOT NULL
    """, (int(group_id), int(since_ts), *SECURITY_BLOCK_ACTIONS))
    unique_targets = int((cursor.fetchone() or [0])[0] or 0)
    cursor.execute("""
        SELECT action, COUNT(*) FROM security_events
        WHERE group_id = ? AND created_at >= ? AND source = 'captcha' GROUP BY action
    """, (int(group_id), int(since_ts)))
    captcha_counts = {a: int(c or 0) for a, c in cursor.fetchall()}
    verified = captcha_counts.get(SECURITY_PASS_ACTION, 0)
    failed = sum(v for k, v in captcha_counts.items() if k != SECURITY_PASS_ACTION)
    attempts = verified + failed
    return {
        "total": total, "expelled": expelled, "restricted": by_action.get("mute", 0),
        "purged": by_action.get("delete", 0), "users": users, "bots": bots,
        "unique_targets": unique_targets, "by_source": by_source, "by_action": by_action,
        "captcha": {"verified": verified, "failed": failed,
                    "pass_rate": round(verified * 100.0 / attempts, 1) if attempts else None},
    }


@db_async
def get_security_block_stats(group_id: int, days: int = 30) -> dict:
    """
    Bloque `security` de la analítica en vivo: totales (hoy / 7 d / 30 d), usuarios vs bots, desglose por
    módulo (captcha, antiflood, antispam, ai_guardian, raid_shield) y por acción, serie diaria de 30 días
    y estado del Escudo Anti-Raid.
    """
    days = max(1, min(int(days or 30), SECURITY_RETENTION_DAYS))
    now = _now_ts()
    since = now - days * 86400
    today_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    placeholders = ",".join("?" for _ in SECURITY_BLOCK_ACTIONS)
    with get_db_connection() as conn:
        summary = _security_summary_sync(conn, group_id, since)
        cursor = conn.cursor()

        def _count(from_ts: int) -> int:
            cursor.execute(f"SELECT COUNT(*) FROM security_events WHERE group_id = ? AND created_at >= ? AND action IN ({placeholders})",
                           (int(group_id), int(from_ts), *SECURITY_BLOCK_ACTIONS))
            return int((cursor.fetchone() or [0])[0] or 0)

        today = _count(today_start)
        last_7d = _count(now - 7 * 86400)
        cursor.execute(f"""
            SELECT date(created_at, 'unixepoch') AS d, COUNT(*) FROM security_events
            WHERE group_id = ? AND created_at >= ? AND action IN ({placeholders})
            GROUP BY d
        """, (int(group_id), int(since), *SECURITY_BLOCK_ACTIONS))
        per_day = {d: int(c) for d, c in cursor.fetchall()}
        shield = _raid_shield_state_sync(conn, group_id)
    start_day = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    labels = [(start_day + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "window_days": days,
        **summary,
        "today": today,
        "last_7d": last_7d,
        "last_30d": summary["total"],
        "daily": {"labels": labels, "blocked": [per_day.get(d, 0) for d in labels]},
        "shield": shield,
    }


# ==========================================
# 👮 v9 · ACCIONES DE MODERACIÓN DE ADMINISTRADORES
# ==========================================
ADMIN_MOD_ACTIONS = ("ban", "kick", "mute", "unban", "unmute", "promote", "demote")


@db_async
def record_admin_mod_action(group_id: int, admin_id: int, admin_name: str, target_id, action: str) -> bool:
    act = str(action or "").strip().lower()
    if act not in ADMIN_MOD_ACTIONS or not admin_id:
        return False
    with _write_transaction() as conn:
        conn.execute(
            "INSERT INTO admin_mod_actions (group_id, admin_id, admin_name, target_id, action, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(group_id), int(admin_id), (admin_name or "")[:64], int(target_id) if target_id else None, act, _now_ts()),
        )
        conn.execute("DELETE FROM admin_mod_actions WHERE group_id = ? AND created_at < ?",
                     (int(group_id), _now_ts() - SECURITY_RETENTION_DAYS * 86400))
    return True


@db_async
def get_admin_performance(group_id: int, since_ts: int) -> dict:
    """Actividad y moderación de cada administrador desde since_ts (base del informe del canal de registro)."""
    since_ts = int(since_ts)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, MAX(full_name), MAX(username), SUM(messages), SUM(replies)
            FROM admin_activity_daily
            WHERE group_id = ? AND day >= date(?, 'unixepoch')
            GROUP BY user_id
        """, (int(group_id), since_ts))
        admins: dict = {}
        for uid, name, uname, msgs, reps in cursor.fetchall():
            admins[int(uid)] = {"user_id": int(uid), "name": name or f"Admin {uid}", "username": uname or "",
                                "messages": int(msgs or 0), "replies": int(reps or 0), "actions": {}, "actions_total": 0}
        cursor.execute("""
            SELECT admin_id, MAX(admin_name), action, COUNT(*) FROM admin_mod_actions
            WHERE group_id = ? AND created_at >= ? GROUP BY admin_id, action
        """, (int(group_id), since_ts))
        for uid, name, act, count in cursor.fetchall():
            entry = admins.setdefault(int(uid), {"user_id": int(uid), "name": name or f"Admin {uid}", "username": "",
                                                  "messages": 0, "replies": 0, "actions": {}, "actions_total": 0})
            entry["actions"][act] = int(count or 0)
            entry["actions_total"] += int(count or 0)
        cursor.execute("""
            SELECT user_id, full_name, username FROM chat_user_activity WHERE group_id = ? AND is_admin = 1
        """, (int(group_id),))
        known = cursor.fetchall()
        inactive = [{"user_id": int(uid), "name": name or f"Admin {uid}", "username": uname or ""}
                    for uid, name, uname in known if int(uid) not in admins]
        security = _security_summary_sync(conn, group_id, since_ts)
        cursor.execute("SELECT COUNT(*) FROM security_events WHERE group_id = ? AND created_at >= ? AND source = 'raid_shield'",
                       (int(group_id), since_ts))
        raid_actions = int((cursor.fetchone() or [0])[0] or 0)
        shield = _raid_shield_state_sync(conn, group_id)
    ranking = sorted(admins.values(), key=lambda a: (a["actions_total"] * 3 + a["messages"] + a["replies"]), reverse=True)
    return {"since": since_ts, "admins": ranking, "inactive": inactive, "security": security,
            "raid": {"actions": raid_actions, **shield}}


# ==========================================
# 🧾 v9 · INFORME PERIÓDICO DEL CANAL DE REGISTRO
# ==========================================
LOG_REPORT_METRICS = (
    "admin_messages", "admin_replies", "admin_mod_actions", "top_admin",
    "inactive_admins", "security_blocks", "captcha_summary", "raid_shield",
)
LOG_REPORT_DEFAULT_METRICS = ("admin_messages", "admin_mod_actions", "top_admin", "security_blocks")
LOG_REPORT_INTERVALS = (24, 72, 168)
LOG_REPORT_MAX_FAILURES = 3
LOG_REPORT_RETRY_MINUTES = (5, 15, 30, 60)


def _log_report_row_to_dict(group_id: int, row) -> dict:
    if not row:
        return {"group_id": int(group_id), "enabled": False, "interval_hours": 24,
                "metrics": list(LOG_REPORT_DEFAULT_METRICS), "lang": "es",
                "last_sent_at": None, "next_at": None, "failures": 0}
    enabled, interval, metrics_raw, lang, anchor, last_sent, retry_at, failures = row
    try:
        metrics = [m for m in json.loads(metrics_raw or "[]") if m in LOG_REPORT_METRICS]
    except (TypeError, ValueError):
        metrics = list(LOG_REPORT_DEFAULT_METRICS)
    interval = int(interval or 24)
    base = last_sent or anchor
    next_at = None
    if enabled and base:
        next_at = int(base) + interval * 3600
        if retry_at and int(retry_at) > next_at:
            next_at = int(retry_at)
    return {"group_id": int(group_id), "enabled": bool(enabled), "interval_hours": interval,
            "metrics": metrics, "lang": _clean_broadcast_lang(lang),
            "last_sent_at": int(last_sent) if last_sent else None, "next_at": next_at,
            "failures": int(failures or 0)}


def _read_log_report_row(conn, group_id: int):
    return conn.execute(
        "SELECT enabled, interval_hours, metrics, lang, anchor_at, last_sent_at, retry_at, failures "
        "FROM log_report_config WHERE group_id = ?", (int(group_id),)).fetchone()


@db_async
def get_log_report_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cfg = _log_report_row_to_dict(group_id, _read_log_report_row(conn, group_id))
        log_row = conn.execute("SELECT log_channel_id FROM group_settings WHERE group_id = ?", (int(group_id),)).fetchone()
    cfg["log_channel_id"] = (log_row[0] if log_row and log_row[0] else None)
    cfg["available_metrics"] = list(LOG_REPORT_METRICS)
    cfg["available_intervals"] = list(LOG_REPORT_INTERVALS)
    return cfg


def validate_log_report_payload(payload) -> dict:
    """Valida forma y rangos. Errores → ValueError('campo: motivo')."""
    if not isinstance(payload, dict):
        raise ValueError("log_report: se esperaba un objeto JSON.")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("enabled: debe ser true o false.")
    try:
        interval = int(payload.get("interval_hours", 24))
    except (TypeError, ValueError):
        interval = -1
    if interval not in LOG_REPORT_INTERVALS:
        raise ValueError(f"interval_hours: valores permitidos {', '.join(map(str, LOG_REPORT_INTERVALS))}.")
    metrics = payload.get("metrics", list(LOG_REPORT_DEFAULT_METRICS))
    if not isinstance(metrics, list) or any(not isinstance(m, str) for m in metrics):
        raise ValueError("metrics: se esperaba una lista de métricas.")
    unknown = [m for m in metrics if m not in LOG_REPORT_METRICS]
    if unknown:
        raise ValueError(f"metrics: métrica desconocida '{unknown[0]}'.")
    metrics = [m for m in LOG_REPORT_METRICS if m in set(metrics)]   # orden canónico, sin duplicados
    if enabled and not metrics:
        raise ValueError("metrics: elige al menos una métrica para activar el informe.")
    return {"enabled": enabled, "interval_hours": interval, "metrics": metrics,
            "lang": _clean_broadcast_lang(payload.get("lang"))}


@db_async
def save_log_report_config(group_id: int, payload: dict, updated_by: int = None) -> dict:
    """
    Persiste la configuración (BEGIN IMMEDIATE). Activar por primera vez ancla el reloj en "ahora": el
    primer informe sale tras un intervalo completo (para uno inmediato está "Enviar ahora"). Cambiar el
    intervalo NO reinicia el reloj: el próximo envío se mide desde el último informe REAL.
    """
    data = validate_log_report_payload(payload)
    now = _now_ts()
    with _write_transaction() as conn:
        prev = _read_log_report_row(conn, group_id)
        was_enabled = bool(prev[0]) if prev else False
        anchor = prev[4] if prev else None
        if data["enabled"] and not was_enabled:
            anchor = now
        conn.execute("""
            INSERT INTO log_report_config (group_id, enabled, interval_hours, metrics, lang, anchor_at,
                                           failures, retry_at, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                enabled = excluded.enabled, interval_hours = excluded.interval_hours,
                metrics = excluded.metrics, lang = excluded.lang, anchor_at = excluded.anchor_at,
                failures = 0, retry_at = NULL, updated_by = excluded.updated_by, updated_at = excluded.updated_at
        """, (int(group_id), 1 if data["enabled"] else 0, data["interval_hours"], json.dumps(data["metrics"]),
              data["lang"], anchor, int(updated_by) if updated_by else None, now))
    return get_log_report_config.sync(group_id)


@db_async
def get_due_log_reports(limit: int = 20) -> list:
    """Informes vencidos con canal de registro configurado. Mide desde el último envío real (o el ancla)."""
    now = _now_ts()
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT r.group_id, r.enabled, r.interval_hours, r.metrics, r.lang, r.anchor_at, r.last_sent_at,
                   r.retry_at, r.failures, s.log_channel_id
            FROM log_report_config r
            JOIN group_settings s ON s.group_id = r.group_id
            WHERE r.enabled = 1 AND s.log_channel_id IS NOT NULL AND s.log_channel_id != ''
              AND COALESCE(r.last_sent_at, r.anchor_at, 0) + r.interval_hours * 3600 <= ?
              AND (r.retry_at IS NULL OR r.retry_at <= ?)
            ORDER BY COALESCE(r.last_sent_at, r.anchor_at, 0) ASC
            LIMIT ?
        """, (now, now, max(1, int(limit)))).fetchall()
    result = []
    for r in rows:
        cfg = _log_report_row_to_dict(r[0], r[1:9])
        cfg["log_channel_id"] = r[9]
        result.append(cfg)
    return result


@db_async
def mark_log_report_result(group_id: int, ok: bool, permanent: bool = False, retry_after_seconds: int = 0) -> bool:
    """Éxito → last_sent_at = ahora. Fallo → reintento corto sin mover el reloj. True si quedó desactivado."""
    now = _now_ts()
    with _write_transaction() as conn:
        row = conn.execute("SELECT failures FROM log_report_config WHERE group_id = ?", (int(group_id),)).fetchone()
        if not row:
            return True
        if ok:
            conn.execute("UPDATE log_report_config SET last_sent_at = ?, failures = 0, retry_at = NULL WHERE group_id = ?",
                         (now, int(group_id)))
            return False
        failures = int(row[0] or 0) + (1 if permanent else 0)
        if permanent and failures >= LOG_REPORT_MAX_FAILURES:
            conn.execute("UPDATE log_report_config SET enabled = 0, failures = ?, retry_at = NULL WHERE group_id = ?",
                         (failures, int(group_id)))
            return True
        idx = max(0, min(failures, len(LOG_REPORT_RETRY_MINUTES) - 1))
        wait = max(int(retry_after_seconds or 0) + 5, LOG_REPORT_RETRY_MINUTES[idx] * 60)
        conn.execute("UPDATE log_report_config SET failures = ?, retry_at = ? WHERE group_id = ?",
                     (failures, now + wait, int(group_id)))
        return False


# ==========================================
# 🚨 v9 · ESCUDO ANTI-RAID / ANTI-REPORTES (ESTADO PERSISTIDO)
# ==========================================
def _raid_shield_state_sync(conn, group_id: int) -> dict:
    row = conn.execute(
        "SELECT active_until, level, reason, joins_in_window, triggered_at, triggers_total FROM raid_shield_state WHERE group_id = ?",
        (int(group_id),)).fetchone()
    now = _now_ts()
    if not row:
        return {"active": False, "active_until": None, "level": 0, "reason": None,
                "joins_in_window": 0, "triggered_at": None, "triggers_total": 0}
    active_until = int(row[0] or 0)
    active = active_until > now
    return {"active": active, "active_until": active_until if active else None,
            "level": int(row[1] or 0) if active else 0, "reason": row[2] if active else None,
            "joins_in_window": int(row[3] or 0), "triggered_at": int(row[4]) if row[4] else None,
            "triggers_total": int(row[5] or 0)}


@db_async
def get_raid_shield_state(group_id: int) -> dict:
    with get_db_connection() as conn:
        return _raid_shield_state_sync(conn, group_id)


@db_async
def activate_raid_shield(group_id: int, duration_seconds: int, level: int, reason: str, joins_in_window: int = 0) -> dict:
    """Activa o prolonga el escudo. Solo una activación nueva (no una prolongación) suma a triggers_total."""
    now = _now_ts()
    until = now + max(60, int(duration_seconds))
    with _write_transaction() as conn:
        prev = _raid_shield_state_sync(conn, group_id)
        new_activation = not prev["active"]
        conn.execute("""
            INSERT INTO raid_shield_state (group_id, active_until, level, reason, joins_in_window, triggered_at, triggers_total)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(group_id) DO UPDATE SET
                active_until = MAX(raid_shield_state.active_until, excluded.active_until),
                level = MAX(CASE WHEN raid_shield_state.active_until > ? THEN raid_shield_state.level ELSE 0 END, excluded.level),
                reason = excluded.reason,
                joins_in_window = excluded.joins_in_window,
                triggered_at = CASE WHEN raid_shield_state.active_until > ? THEN raid_shield_state.triggered_at ELSE excluded.triggered_at END,
                triggers_total = raid_shield_state.triggers_total + ?
        """, (int(group_id), until, int(level), (reason or "")[:120], int(joins_in_window), now,
              now, now, 1 if new_activation else 0))
        state = _raid_shield_state_sync(conn, group_id)
    state["new_activation"] = new_activation
    return state


@db_async
def clear_raid_shield(group_id: int) -> None:
    with _write_transaction() as conn:
        conn.execute("UPDATE raid_shield_state SET active_until = 0, level = 0 WHERE group_id = ?", (int(group_id),))


@db_async
def get_active_raid_shields() -> dict:
    """{group_id: active_until} de los escudos vigentes (lo usa la caché en memoria de raid_shield.py)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT group_id, active_until FROM raid_shield_state WHERE active_until > ?",
                            (_now_ts(),)).fetchall()
    return {int(g): int(u) for g, u in rows}


# ==========================================================================================
# 👑 v13 · THE BUNKER ARCHITECT COMMAND MATRIX (Matriz del Arquitecto Supremo)
# ==========================================================================================
# Cuatro tablas exclusivas del Arquitecto:
#   • architect_delegates        → administradores delegados por comunidad con 6 permisos granulares.
#   • architect_license_grants   → licencias PRO / ULTRA PRO concedidas sin pasarela (temporales o permanentes).
#                                  Se SUPERPONEN a approved_groups: nunca la sobrescriben, así una licencia pagada
#                                  con Stars y una concesión conviven y revocar una no destruye la otra.
#   • architect_awards           → premios otorgados (XP, niveles, MicVIP, membresías) + estado del anuncio.
#   • architect_audit            → bitácora inmutable de cada acción del Arquitecto.
# Todas las marcas de tiempo son epoch UTC (INTEGER): comparables sin ambigüedad de zona horaria.

DELEGATE_PERMISSIONS = (
    "can_manage_captcha",
    "can_manage_locks",
    "can_manage_antispam",
    "can_manage_broadcast",
    "can_view_analytics",
    "can_manage_sentinel",
)
ARCHITECT_LICENSE_TIERS = ("pro", "ultra_pro")
ARCHITECT_LICENSE_SCOPES = ("chat", "user")
ARCHITECT_LICENSE_DURATIONS = (7, 30, 90, 365, 0)          # 0 = permanente / indefinida
ARCHITECT_AWARD_TYPES = ("xp", "level", "micvip", "membership")
_TIER_RANK = {"free": 0, "pro": 1, "ultra_pro": 2}
_AUDIT_RETENTION_ROWS = 5000


def _init_architect_matrix_schema(cursor) -> None:
    """DDL idempotente de la Matriz. Se invoca desde init_db() y, por seguridad, en el primer uso."""
    perm_cols = ", ".join(f"{p} INTEGER NOT NULL DEFAULT 0" for p in DELEGATE_PERMISSIONS)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS architect_delegates (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            {perm_cols},
            granted_by INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    _ensure_columns(cursor, "architect_delegates", [(p, "INTEGER NOT NULL DEFAULT 0") for p in DELEGATE_PERMISSIONS])
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_architect_delegates_user ON architect_delegates (user_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS architect_license_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL CHECK (scope IN ('chat', 'user')),
            target_id INTEGER NOT NULL,
            tier TEXT NOT NULL CHECK (tier IN ('pro', 'ultra_pro')),
            status TEXT NOT NULL DEFAULT 'active',
            granted_by INTEGER,
            granted_at INTEGER NOT NULL,
            expires_at INTEGER,
            ended_at INTEGER,
            ended_by INTEGER,
            note TEXT
        )
    """)
    # Una sola concesión activa por objetivo (índice parcial): conceder de nuevo REEMPLAZA a la anterior.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_architect_license_active
        ON architect_license_grants (scope, target_id) WHERE status = 'active'
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_architect_license_expiry ON architect_license_grants (status, expires_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS architect_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            award_type TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            detail TEXT,
            granted_by INTEGER,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            announced INTEGER NOT NULL DEFAULT 0,
            tag_applied INTEGER NOT NULL DEFAULT 0,
            tag_title TEXT,
            tag_cleared INTEGER NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_architect_awards_chat ON architect_awards (chat_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_architect_awards_tags ON architect_awards (tag_applied, tag_cleared, expires_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS architect_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            chat_id INTEGER,
            target_id INTEGER,
            detail TEXT,
            created_at INTEGER NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_architect_audit_time ON architect_audit (created_at)")


_ARCHITECT_SCHEMA_READY = False
_ARCHITECT_SCHEMA_LOCK = threading.Lock()


def _ensure_architect_schema(conn) -> None:
    """Garantiza el esquema aunque init_db() de una versión anterior ya se hubiera ejecutado."""
    global _ARCHITECT_SCHEMA_READY
    if _ARCHITECT_SCHEMA_READY:
        return
    with _ARCHITECT_SCHEMA_LOCK:
        if _ARCHITECT_SCHEMA_READY:
            return
        _init_architect_matrix_schema(conn.cursor())
        if conn.in_transaction:
            conn.commit()
        _ARCHITECT_SCHEMA_READY = True


def _validate_perm(perm: str) -> str:
    if perm not in DELEGATE_PERMISSIONS:
        raise ValueError(f"Permiso de delegado no permitido: {perm!r}")
    return perm


def _delegate_row_to_dict(row) -> dict:
    data = {"chat_id": row[0], "user_id": row[1], "user_name": row[2] or ""}
    for idx, perm in enumerate(DELEGATE_PERMISSIONS):
        data[perm] = int(row[3 + idx] or 0)
    tail = 3 + len(DELEGATE_PERMISSIONS)
    data.update({"granted_by": row[tail], "created_at": row[tail + 1], "updated_at": row[tail + 2]})
    data["active_count"] = sum(data[p] for p in DELEGATE_PERMISSIONS)
    return data


_DELEGATE_SELECT = "SELECT chat_id, user_id, user_name, " + ", ".join(DELEGATE_PERMISSIONS) + \
                   ", granted_by, created_at, updated_at FROM architect_delegates"


# ---------- 🛡️ Administradores delegados ----------
@db_async
def upsert_architect_delegate(chat_id: int, user_id: int, user_name: str = "", granted_by: int = None,
                              permissions: dict = None) -> dict:
    """Crea (todo en 🔴) o actualiza un delegado. Devuelve el registro resultante."""
    now = _now_ts()
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        conn.execute("""
            INSERT INTO architect_delegates (chat_id, user_id, user_name, granted_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                user_name = COALESCE(NULLIF(excluded.user_name, ''), architect_delegates.user_name),
                updated_at = excluded.updated_at
        """, (int(chat_id), int(user_id), (user_name or "")[:64], granted_by, now, now))
        for perm, value in (permissions or {}).items():
            conn.execute(f"UPDATE architect_delegates SET {_ident(_validate_perm(perm))} = ? WHERE chat_id = ? AND user_id = ?",
                         (1 if value else 0, int(chat_id), int(user_id)))
        row = conn.execute(_DELEGATE_SELECT + " WHERE chat_id = ? AND user_id = ?", (int(chat_id), int(user_id))).fetchone()
    return _delegate_row_to_dict(row)


@db_async
def get_architect_delegate(chat_id: int, user_id: int) -> dict | None:
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        row = conn.execute(_DELEGATE_SELECT + " WHERE chat_id = ? AND user_id = ?", (int(chat_id), int(user_id))).fetchone()
    return _delegate_row_to_dict(row) if row else None


@db_async
def toggle_architect_delegate_permission(chat_id: int, user_id: int, perm: str) -> dict | None:
    """Invierte un permiso de forma atómica (BEGIN IMMEDIATE). None si el delegado no existe."""
    col = _ident(_validate_perm(perm))
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute(f"UPDATE architect_delegates SET {col} = 1 - {col}, updated_at = ? WHERE chat_id = ? AND user_id = ?",
                           (_now_ts(), int(chat_id), int(user_id)))
        if cur.rowcount == 0:
            return None
        row = conn.execute(_DELEGATE_SELECT + " WHERE chat_id = ? AND user_id = ?", (int(chat_id), int(user_id))).fetchone()
    return _delegate_row_to_dict(row)


@db_async
def set_all_architect_delegate_permissions(chat_id: int, user_id: int, value: bool) -> dict | None:
    assignments = ", ".join(f"{p} = ?" for p in DELEGATE_PERMISSIONS)
    flag = 1 if value else 0
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute(f"UPDATE architect_delegates SET {assignments}, updated_at = ? WHERE chat_id = ? AND user_id = ?",
                           (*([flag] * len(DELEGATE_PERMISSIONS)), _now_ts(), int(chat_id), int(user_id)))
        if cur.rowcount == 0:
            return None
        row = conn.execute(_DELEGATE_SELECT + " WHERE chat_id = ? AND user_id = ?", (int(chat_id), int(user_id))).fetchone()
    return _delegate_row_to_dict(row)


@db_async
def remove_architect_delegate(chat_id: int, user_id: int) -> bool:
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute("DELETE FROM architect_delegates WHERE chat_id = ? AND user_id = ?", (int(chat_id), int(user_id)))
        return cur.rowcount > 0


@db_async
def list_architect_delegates(chat_id: int) -> list:
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        rows = conn.execute(_DELEGATE_SELECT + " WHERE chat_id = ? ORDER BY created_at", (int(chat_id),)).fetchall()
    return [_delegate_row_to_dict(r) for r in rows]


@db_async
def get_delegated_chats(user_id: int) -> list:
    """Comunidades donde el usuario es delegado con AL MENOS un permiso activo: [{chat_id, title, chat_type, perms}]."""
    any_perm = " OR ".join(f"d.{p} = 1" for p in DELEGATE_PERMISSIONS)
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        rows = conn.execute(f"""
            SELECT d.chat_id,
                   (SELECT MAX(group_name) FROM user_groups ug WHERE ug.group_id = d.chat_id),
                   (SELECT MAX(chat_type) FROM user_groups ug WHERE ug.group_id = d.chat_id),
                   {", ".join("d." + p for p in DELEGATE_PERMISSIONS)}
            FROM architect_delegates d
            WHERE d.user_id = ? AND ({any_perm})
        """, (int(user_id),)).fetchall()
    result = []
    for row in rows:
        perms = {p: int(row[3 + i] or 0) for i, p in enumerate(DELEGATE_PERMISSIONS)}
        result.append({"chat_id": row[0], "title": row[1] or f"Chat {row[0]}", "chat_type": row[2] or "supergroup", "perms": perms})
    return result


@db_async
def has_delegate_permission(chat_id: int, user_id: int, perm: str = "any") -> bool:
    """perm='any' → al menos un permiso activo. Consulta indexada por PK (barata en cada botón)."""
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        if perm == "any":
            cond = " OR ".join(f"{p} = 1" for p in DELEGATE_PERMISSIONS)
        else:
            cond = f"{_ident(_validate_perm(perm))} = 1"
        row = conn.execute(f"SELECT 1 FROM architect_delegates WHERE chat_id = ? AND user_id = ? AND ({cond})",
                           (int(chat_id), int(user_id))).fetchone()
    return row is not None


# ---------- 💎 Licencias concedidas por el Arquitecto ----------
def _grant_row_to_dict(row) -> dict:
    keys = ("id", "scope", "target_id", "tier", "status", "granted_by", "granted_at", "expires_at", "ended_at", "ended_by", "note")
    data = dict(zip(keys, row))
    data["permanent"] = data["expires_at"] is None
    return data


_GRANT_SELECT = ("SELECT id, scope, target_id, tier, status, granted_by, granted_at, expires_at, ended_at, ended_by, note "
                 "FROM architect_license_grants")


def _architect_best_tier_sync(conn, scope: str, target_ids) -> tuple:
    """Mejor concesión ACTIVA y no vencida entre varios objetivos → (tier, expires_at|None, grant_id) o ('free', None, None)."""
    ids = [int(t) for t in target_ids if t is not None]
    if not ids:
        return ("free", None, None)
    marks = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT tier, expires_at, id FROM architect_license_grants "
            f"WHERE scope = ? AND status = 'active' AND target_id IN ({marks}) "
            f"AND (expires_at IS NULL OR expires_at > ?)",
            (scope, *ids, _now_ts()),
        ).fetchall()
    except sqlite3.OperationalError:          # esquema aún no creado (arranque en frío de una versión anterior)
        return ("free", None, None)
    best = ("free", None, None)
    for tier, expires_at, grant_id in rows:
        current = (_TIER_RANK.get(tier, 0), float("inf") if expires_at is None else expires_at)
        incumbent = (_TIER_RANK.get(best[0], 0), float("inf") if best[1] is None else best[1])
        if current > incumbent:
            best = (tier, expires_at, grant_id)
    return best


def _max_tier(*tiers) -> str:
    return max((normalize_tier(t) for t in tiers), key=lambda t: _TIER_RANK[t])


@db_async
def grant_architect_license(scope: str, target_id: int, tier: str, duration_days: int, granted_by: int,
                            note: str = None) -> dict:
    """
    Concede (o reemplaza) la licencia del objetivo. duration_days=0 → permanente.
    La concesión activa previa del mismo objetivo pasa a 'replaced' en la misma transacción.
    """
    if scope not in ARCHITECT_LICENSE_SCOPES:
        raise ValueError("scope: debe ser 'chat' o 'user'")
    tier = normalize_tier(tier)
    if tier not in ARCHITECT_LICENSE_TIERS:
        raise ValueError("tier: debe ser 'pro' o 'ultra_pro'")
    days = int(duration_days)
    if days not in ARCHITECT_LICENSE_DURATIONS:
        raise ValueError("duration_days: usa 7, 30, 90, 365 o 0 (permanente)")
    now = _now_ts()
    expires_at = None if days == 0 else now + days * 86400
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        conn.execute("""
            UPDATE architect_license_grants SET status = 'replaced', ended_at = ?, ended_by = ?
            WHERE scope = ? AND target_id = ? AND status = 'active'
        """, (now, granted_by, scope, int(target_id)))
        cur = conn.execute("""
            INSERT INTO architect_license_grants (scope, target_id, tier, status, granted_by, granted_at, expires_at, note)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
        """, (scope, int(target_id), tier, granted_by, now, expires_at, (note or "")[:200] or None))
        row = conn.execute(_GRANT_SELECT + " WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _grant_row_to_dict(row)


@db_async
def revoke_architect_license(scope: str, target_id: int, revoked_by: int) -> bool:
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute("""
            UPDATE architect_license_grants SET status = 'revoked', ended_at = ?, ended_by = ?
            WHERE scope = ? AND target_id = ? AND status = 'active'
        """, (_now_ts(), revoked_by, scope, int(target_id)))
        return cur.rowcount > 0


@db_async
def get_architect_license(scope: str, target_id: int) -> dict | None:
    """Concesión activa y NO vencida del objetivo (None si no hay)."""
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        row = conn.execute(_GRANT_SELECT + " WHERE scope = ? AND target_id = ? AND status = 'active' "
                                           "AND (expires_at IS NULL OR expires_at > ?)",
                           (scope, int(target_id), _now_ts())).fetchone()
    return _grant_row_to_dict(row) if row else None


@db_async
def list_architect_licenses(limit: int = 10, offset: int = 0) -> tuple:
    """(concesiones activas vigentes paginadas, total)."""
    now = _now_ts()
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM architect_license_grants WHERE status = 'active' "
                             "AND (expires_at IS NULL OR expires_at > ?)", (now,)).fetchone()[0]
        rows = conn.execute(_GRANT_SELECT + " WHERE status = 'active' AND (expires_at IS NULL OR expires_at > ?) "
                                            "ORDER BY COALESCE(expires_at, 9e18) ASC LIMIT ? OFFSET ?",
                            (now, int(limit), int(offset))).fetchall()
    return [_grant_row_to_dict(r) for r in rows], int(total)


@db_async
def expire_architect_licenses() -> list:
    """
    Cierra las concesiones vencidas (status → 'expired') y las devuelve. La reversión a 'free' es inmediata en
    get_group_tier / get_user_global_tier porque ya filtran por expires_at; esto deja el historial coherente
    y permite notificar al Arquitecto.
    """
    now = _now_ts()
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        rows = conn.execute(_GRANT_SELECT + " WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                            (now,)).fetchall()
        if rows:
            conn.execute("UPDATE architect_license_grants SET status = 'expired', ended_at = ? "
                         "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?", (now, now))
    return [_grant_row_to_dict(r) for r in rows]


@db_async
def get_effective_license(chat_id: int) -> dict:
    """Licencia vigente de la comunidad con su ORIGEN y vencimiento reales (para el dashboard y la Matriz)."""
    with get_db_connection() as conn:
        base_tier = "free"
        base_exp = None
        row = conn.execute("SELECT tier, expires_at FROM approved_groups WHERE group_id = ?", (int(chat_id),)).fetchone()
        if row:
            base_tier, base_exp = row[0] or "free", row[1]
            if base_exp:
                expired = conn.execute("SELECT datetime('now') > ?", (base_exp,)).fetchone()[0]
                if expired:
                    base_tier, base_exp = "free", None
        owners = [r[0] for r in conn.execute("SELECT user_id FROM user_groups WHERE group_id = ?", (int(chat_id),)).fetchall()]
        chat_tier, chat_exp, chat_grant = _architect_best_tier_sync(conn, "chat", [chat_id])
        user_tier, user_exp, user_grant = _architect_best_tier_sync(conn, "user", owners)
    candidates = [
        (normalize_tier(base_tier), base_exp, "payment" if normalize_tier(base_tier) != "free" else None, None),
        (chat_tier, datetime.fromtimestamp(chat_exp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if chat_exp else None,
         "architect", chat_grant),
        (user_tier, datetime.fromtimestamp(user_exp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if user_exp else None,
         "architect_user", user_grant),
    ]
    best = max(candidates, key=lambda c: _TIER_RANK[c[0]])
    if any(is_super_admin(o) for o in owners) or is_super_admin(chat_id):
        best = ("ultra_pro", None, "architect", best[3])
    return {"tier": best[0], "expires_at": best[1] if best[0] != "free" else None,
            "source": best[2] if best[0] != "free" else None, "grant_id": best[3],
            "base_tier": normalize_tier(base_tier)}


# ---------- 🏆 Premios ----------
def _level_for_xp(xp: int) -> int:
    return int((max(0, int(xp)) / 100) ** 0.5) + 1


def _xp_for_level(level: int) -> int:
    return ((max(1, int(level)) - 1) ** 2) * 100


@db_async
def architect_grant_xp(group_id: int, user_id: int, full_name: str = "", username: str = "",
                       xp: int = 0, levels: int = 0) -> dict:
    """
    Inyección directa de reputación (sin enfriamiento ni multiplicador). xp suma experiencia; levels sube N
    niveles completos (lleva la XP al umbral del nivel destino si hace falta). Devuelve antes/después.
    """
    xp, levels = max(0, int(xp or 0)), max(0, int(levels or 0))
    if not xp and not levels:
        raise ValueError("Indica xp o levels mayores que cero.")
    if xp > 1_000_000 or levels > 100:
        raise ValueError("Bonificación fuera de rango (máx. 1 000 000 XP o 100 niveles).")
    with _write_transaction() as conn:
        row = conn.execute("SELECT xp, level FROM chat_user_reputation WHERE group_id = ? AND user_id = ?",
                           (int(group_id), int(user_id))).fetchone()
        old_xp = int(row[0] or 0) if row else 0
        old_level = int(row[1] or 1) if row else 1
        new_xp = old_xp + xp
        if levels:
            new_xp = max(new_xp, _xp_for_level(_level_for_xp(old_xp) + levels))
        new_level = _level_for_xp(new_xp)
        if row:
            conn.execute("""
                UPDATE chat_user_reputation SET xp = ?, level = ?,
                    full_name = COALESCE(NULLIF(?, ''), full_name), username = COALESCE(NULLIF(?, ''), username)
                WHERE group_id = ? AND user_id = ?
            """, (new_xp, new_level, full_name or "", username or "", int(group_id), int(user_id)))
        else:
            conn.execute("""
                INSERT INTO chat_user_reputation (group_id, user_id, full_name, username, xp, level, last_xp_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (int(group_id), int(user_id), full_name or "", username or "", new_xp, new_level))
    return {"old_xp": old_xp, "old_level": old_level, "xp": new_xp, "level": new_level,
            "gained_xp": new_xp - old_xp, "leveled_up": new_level > old_level}


@db_async
def record_architect_award(chat_id: int, user_id: int, user_name: str, award_type: str, amount: int,
                           detail: str = "", granted_by: int = None, expires_at: int = None,
                           tag_applied: bool = False, tag_title: str = None) -> int:
    if award_type not in ARCHITECT_AWARD_TYPES:
        raise ValueError(f"award_type no permitido: {award_type!r}")
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute("""
            INSERT INTO architect_awards (chat_id, user_id, user_name, award_type, amount, detail, granted_by,
                                          created_at, expires_at, tag_applied, tag_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(chat_id), int(user_id), (user_name or "")[:64], award_type, int(amount), (detail or "")[:300],
              granted_by, _now_ts(), expires_at, 1 if tag_applied else 0, tag_title))
        return int(cur.lastrowid)


@db_async
def get_architect_award(award_id: int) -> dict | None:
    keys = ("id", "chat_id", "user_id", "user_name", "award_type", "amount", "detail", "granted_by",
            "created_at", "expires_at", "announced", "tag_applied", "tag_title", "tag_cleared")
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        row = conn.execute(f"SELECT {', '.join(keys)} FROM architect_awards WHERE id = ?", (int(award_id),)).fetchone()
    return dict(zip(keys, row)) if row else None


@db_async
def mark_architect_award_announced(award_id: int) -> bool:
    """Marca el anuncio como emitido UNA sola vez (devuelve False si ya estaba marcado: evita duplicados)."""
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute("UPDATE architect_awards SET announced = 1 WHERE id = ? AND announced = 0", (int(award_id),))
        return cur.rowcount > 0


@db_async
def unmark_architect_award_announced(award_id: int) -> None:
    with _write_transaction() as conn:
        conn.execute("UPDATE architect_awards SET announced = 0 WHERE id = ?", (int(award_id),))


@db_async
def get_expired_architect_vip_tags(limit: int = 50) -> list:
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        rows = conn.execute("""
            SELECT id, chat_id, user_id, tag_title FROM architect_awards
            WHERE award_type = 'micvip' AND tag_applied = 1 AND tag_cleared = 0 AND expires_at IS NOT NULL AND expires_at <= ?
            ORDER BY expires_at LIMIT ?
        """, (_now_ts(), int(limit))).fetchall()
    return [{"id": r[0], "chat_id": r[1], "user_id": r[2], "tag_title": r[3]} for r in rows]


@db_async
def mark_architect_vip_tag_cleared(award_id: int) -> None:
    with _write_transaction() as conn:
        conn.execute("UPDATE architect_awards SET tag_cleared = 1 WHERE id = ?", (int(award_id),))


# ---------- 🗂️ Directorio y auditoría ----------
@db_async
def list_registered_chats(kind: str = "all", limit: int = 8, offset: int = 0) -> tuple:
    """Comunidades conocidas por el bot (una por chat). kind: 'all' | 'group' | 'channel'. → (filas, total)."""
    where = ""
    if kind == "channel":
        where = "HAVING MAX(chat_type) = 'channel'"
    elif kind == "group":
        where = "HAVING MAX(COALESCE(chat_type, 'supergroup')) != 'channel'"
    with get_db_connection() as conn:
        base = f"SELECT group_id, MAX(group_name), MAX(COALESCE(chat_type, 'supergroup')) FROM user_groups GROUP BY group_id {where}"
        total = conn.execute(f"SELECT COUNT(*) FROM ({base})").fetchone()[0]
        rows = conn.execute(f"{base} ORDER BY LOWER(MAX(group_name)) LIMIT ? OFFSET ?", (int(limit), int(offset))).fetchall()
    return [{"chat_id": r[0], "title": r[1] or f"Chat {r[0]}", "chat_type": r[2]} for r in rows], int(total)


@db_async
def find_known_user(query: str) -> dict | None:
    """Resuelve @alias o nombre exacto contra los registros locales (users, reputación, actividad)."""
    value = (query or "").strip().lstrip("@").lower()
    if not value:
        return None
    with get_db_connection() as conn:
        for sql in (
            "SELECT user_id, full_name, username FROM users WHERE LOWER(username) = ? LIMIT 1",
            "SELECT user_id, full_name, username FROM chat_user_reputation WHERE LOWER(username) = ? ORDER BY xp DESC LIMIT 1",
            "SELECT user_id, full_name, username FROM chat_user_activity WHERE LOWER(username) = ? ORDER BY last_active DESC LIMIT 1",
        ):
            try:
                row = conn.execute(sql, (value,)).fetchone()
            except sqlite3.OperationalError:
                continue
            if row:
                return {"user_id": int(row[0]), "full_name": row[1] or "", "username": row[2] or ""}
    return None


@db_async
def record_architect_audit(actor_id: int, action: str, chat_id: int = None, target_id: int = None, detail: str = "") -> None:
    with _write_transaction() as conn:
        _ensure_architect_schema(conn)
        cur = conn.execute("""
            INSERT INTO architect_audit (actor_id, action, chat_id, target_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(actor_id), str(action)[:48], chat_id, target_id, (detail or "")[:300], _now_ts()))
        if cur.lastrowid and cur.lastrowid % 500 == 0:
            conn.execute("DELETE FROM architect_audit WHERE id <= ?", (cur.lastrowid - _AUDIT_RETENTION_ROWS,))


@db_async
def get_architect_audit(limit: int = 15) -> list:
    keys = ("id", "actor_id", "action", "chat_id", "target_id", "detail", "created_at")
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        rows = conn.execute(f"SELECT {', '.join(keys)} FROM architect_audit ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(zip(keys, r)) for r in rows]


@db_async
def get_architect_matrix_summary() -> dict:
    now = _now_ts()
    with get_db_connection() as conn:
        _ensure_architect_schema(conn)
        any_perm = " OR ".join(f"{p} = 1" for p in DELEGATE_PERMISSIONS)
        delegates = conn.execute(f"SELECT COUNT(*) FROM architect_delegates WHERE {any_perm}").fetchone()[0]
        licenses = conn.execute("SELECT COUNT(*) FROM architect_license_grants WHERE status = 'active' "
                                "AND (expires_at IS NULL OR expires_at > ?)", (now,)).fetchone()[0]
        awards = conn.execute("SELECT COUNT(*) FROM architect_awards WHERE created_at > ?", (now - 30 * 86400,)).fetchone()[0]
        chats = conn.execute("SELECT COUNT(DISTINCT group_id) FROM user_groups").fetchone()[0]
    return {"delegates": int(delegates), "licenses": int(licenses), "awards_30d": int(awards), "chats": int(chats)}


# Inicialización del esquema al importar (al final, para que todas las funciones —incluida la Matriz— existan).
try:
    init_db()
except Exception:
    logger.exception("❌ [DB] Falló init_db()")
    raise