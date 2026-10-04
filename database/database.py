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
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import urllib.parse
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
    conn = sqlite3.connect(DB_PATH, timeout=_DB_TIMEOUT_SECONDS)
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
            ("next_broadcast_at", "TIMESTAMP"), ("broadcast_enabled", "INTEGER DEFAULT 0")
        ])
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_plans_broadcast ON channel_plans (broadcast_enabled, next_broadcast_at)")
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
    with get_db_connection() as conn:
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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {lock_name}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {lock_name} = excluded.{lock_name}
        """, (group_id, status))
        conn.commit()


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
                return {
                    "vc_text": row[0], "vc_media_id": row[1], "vc_media_type": row[2], "vc_btn": row[3], "vc_btn_url": row[4], "vc_autodel": row[5] if row[5] is not None else 30, "vc_enabled": row[6] if row[6] is not None else 1,
                    "micvip_text": row[7], "micvip_media_id": row[8], "micvip_media_type": row[9], "micvip_btn": row[10], "micvip_btn_url": row[11], "micvip_autodel": row[12] if row[12] is not None else 30, "micvip_enabled": row[13] if row[13] is not None else 1,
                    "reset_text": row[14], "reset_media_id": row[15], "reset_media_type": row[16], "reset_btn": row[17], "reset_btn_url": row[18], "reset_autodel": row[19] if row[19] is not None else 20, "reset_enabled": row[20] if row[20] is not None else 1,
                    "sched_start_text": row[21], "sched_start_media_id": row[22], "sched_start_media_type": row[23], "sched_start_btn": row[24], "sched_start_btn_url": row[25], "sched_start_autodel": row[26] if row[26] is not None else 0, "sched_enabled": row[27] if row[27] is not None else 1,
                    "vc_welcome_text": row[28], "vc_welcome_media_id": row[29], "vc_welcome_media_type": row[30], "vc_welcome_btn": row[31], "vc_welcome_btn_url": row[32], "vc_welcome_autodel": row[33] if row[33] is not None else 0, "vc_welcome_enabled": row[34] if row[34] is not None else 1
                }
        except sqlite3.OperationalError:
            pass
        return {
            "vc_text": None, "vc_media_id": None, "vc_media_type": None, "vc_btn": None, "vc_btn_url": None, "vc_autodel": 30, "vc_enabled": 1,
            "micvip_text": None, "micvip_media_id": None, "micvip_media_type": None, "micvip_btn": None, "micvip_btn_url": None, "micvip_autodel": 30, "micvip_enabled": 1,
            "reset_text": None, "reset_media_id": None, "reset_media_type": None, "reset_btn": None, "reset_btn_url": None, "reset_autodel": 20, "reset_enabled": 1,
            "sched_start_text": None, "sched_start_media_id": None, "sched_start_media_type": None, "sched_start_btn": None, "sched_start_btn_url": None, "sched_start_autodel": 0, "sched_enabled": 1,
            "vc_welcome_text": None, "vc_welcome_media_id": None, "vc_welcome_media_type": None, "vc_welcome_btn": None, "vc_welcome_btn_url": None, "vc_welcome_autodel": 0, "vc_welcome_enabled": 1
        }


@db_async
def set_sentinel_service_message(group_id: int, field: str, value):
    valid = [
        "vc_join_custom_text", "vc_join_custom_media_id", "vc_join_custom_media_type", "vc_join_btn_text", "vc_join_btn_url", "vc_join_autodel_seconds", "vc_join_enabled",
        "mic_vip_custom_text", "mic_vip_custom_media_id", "mic_vip_custom_media_type", "mic_vip_btn_text", "mic_vip_btn_url", "mic_vip_autodel_seconds", "mic_vip_enabled",
        "reset_notice_custom_text", "reset_notice_custom_media_id", "reset_notice_custom_media_type", "reset_notice_btn_text", "reset_notice_btn_url", "reset_notice_autodel_seconds", "reset_notice_enabled",
        "vc_sched_start_custom_text", "vc_sched_start_custom_media_id", "vc_sched_start_custom_media_type", "vc_sched_start_btn_text", "vc_sched_start_btn_url", "vc_sched_start_autodel_seconds", "vc_sched_start_enabled",
        "vc_welcome_custom_text", "vc_welcome_custom_media_id", "vc_welcome_custom_media_type", "vc_welcome_btn_text", "vc_welcome_btn_url", "vc_welcome_autodel_seconds", "vc_welcome_enabled"
    ]
    # Alias heredado: la clave pública del getter es "sched_enabled", la columna real es vc_sched_start_enabled.
    if field in ("vc_sched_enabled", "sched_enabled"):
        field = "vc_sched_start_enabled"
    if field not in valid:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


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
                       tips_custom_text, tips_media_id, tips_media_type
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
                }
        except sqlite3.OperationalError:
            pass
        return {
            "enabled": 0, "amount": 10, "target_channel": "",
            "tips_custom_text": None, "tips_media_id": None, "tips_media_type": None
        }


@db_async
def set_tips_config(group_id: int, field: str, value):
    valid_fields = [
        "tips_enabled", "tips_amount", "tips_target_channel",
        "tips_custom_text", "tips_media_id", "tips_media_type"
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
    if is_super_admin(group_id):
        return "ultra_pro"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id FROM user_groups WHERE group_id = ?", (group_id,))
            owners = cursor.fetchall()
            for (uid,) in owners:
                if is_super_admin(uid):
                    return "ultra_pro"
        except sqlite3.OperationalError:
            pass

        cursor.execute("SELECT tier, expires_at FROM approved_groups WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if not row:
            return "free"
        tier, expires_at = row
        if expires_at:
            cursor.execute("SELECT datetime('now') > ?", (expires_at,))
            if cursor.fetchone()[0]:
                cursor.execute("UPDATE approved_groups SET tier = 'free', expires_at = NULL WHERE group_id = ?", (group_id,))
                conn.commit()
                tier = "free"
        return tier


@db_async
def get_user_global_tier(user_id: int) -> str:
    if is_super_admin(user_id):
        return "ultra_pro"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT a.tier FROM approved_groups a
                JOIN user_groups u ON a.group_id = u.group_id
                WHERE u.user_id = ? AND (a.expires_at IS NULL OR a.expires_at > datetime('now'))
            """, (user_id,))
            rows = cursor.fetchall()
            if not rows: return "free"
            tiers = [r[0] for r in rows]
            if "ultra_pro" in tiers: return "ultra_pro"
            if "pro" in tiers: return "pro"
        except sqlite3.OperationalError:
            pass
        return "free"


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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channel_plans (
                channel_id, plan_name, duration_days, stars_price, status,
                promo_text, media_id, media_type, target_link
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """, (
            channel_id, (plan_name or "").strip(), duration_days, stars_price,
            promo_text, media_id, media_type, _sanitize_target_link(target_link)
        ))
        conn.commit()
        return cursor.lastrowid


@db_async
def get_channel_plan(plan_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT plan_id, channel_id, plan_name, duration_days, stars_price, status,
                   promo_text, media_id, media_type, created_at, target_link,
                   broadcast_chat_id, broadcast_interval_hours, next_broadcast_at, broadcast_enabled
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
                "broadcast_enabled": row[14] or 0
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


@db_async
def set_channel_plan_broadcast_config(plan_id: int, chat_id: int, interval_hours: int) -> bool:
    if not isinstance(interval_hours, int) or interval_hours <= 0:
        return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE channel_plans
            SET broadcast_chat_id = ?,
                broadcast_interval_hours = ?,
                broadcast_enabled = 1,
                next_broadcast_at = datetime('now', ?)
            WHERE plan_id = ?
        """, (chat_id, interval_hours, _sql_modifier(interval_hours, "hours"), plan_id))
        conn.commit()
        return cursor.rowcount > 0


@db_async
def disable_channel_plan_broadcast(plan_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE channel_plans SET broadcast_enabled = 0 WHERE plan_id = ?", (plan_id,))
        conn.commit()


@db_async
def mark_channel_plan_broadcasted(plan_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT broadcast_interval_hours, status FROM channel_plans WHERE plan_id = ?", (plan_id,))
        row = cursor.fetchone()
        if not row or not row[0] or row[1] != "active":
            cursor.execute("UPDATE channel_plans SET broadcast_enabled = 0 WHERE plan_id = ?", (plan_id,))
            conn.commit()
            return

        interval_hours = _as_int(row[0], 0)
        if interval_hours <= 0:
            cursor.execute("UPDATE channel_plans SET broadcast_enabled = 0 WHERE plan_id = ?", (plan_id,))
            conn.commit()
            return
        cursor.execute("""
            UPDATE channel_plans
            SET next_broadcast_at = datetime('now', ?)
            WHERE plan_id = ?
        """, (_sql_modifier(interval_hours, "hours"), plan_id))
        conn.commit()


@db_async
def get_due_channel_plan_broadcasts() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT plan_id, channel_id, plan_name, duration_days, stars_price,
                   promo_text, media_id, media_type, target_link,
                   broadcast_chat_id, broadcast_interval_hours, next_broadcast_at
            FROM channel_plans
            WHERE broadcast_enabled = 1
              AND status = 'active'
              AND broadcast_chat_id IS NOT NULL
              AND next_broadcast_at IS NOT NULL
              AND next_broadcast_at <= datetime('now')
            ORDER BY next_broadcast_at ASC
        """)
        rows = cursor.fetchall()
        return [
            {
                "plan_id": r[0], "channel_id": r[1], "plan_name": r[2], "duration_days": r[3], "stars_price": r[4],
                "promo_text": r[5], "media_id": r[6], "media_type": r[7], "target_link": r[8],
                "broadcast_chat_id": r[9], "broadcast_interval_hours": r[10], "next_broadcast_at": r[11]
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
@db_async
def get_chat_dashboard_data(chat_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tier FROM approved_groups WHERE group_id = ?", (chat_id,))
        row = cursor.fetchone()
        tier = row[0] if row and row[0] else "free"

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
            "plan": {"name": tier.capitalize(), "status": "active" if tier != "free" else "empty"},
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

        if updates:
            params.append(chat_id)
            query = f"UPDATE group_settings SET {', '.join(updates)} WHERE group_id = ?"
            cursor.execute(query, tuple(params))
        conn.commit()


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
_IMPORT_EXCLUDED_COLUMNS = {"group_id", "panic_active", "night_mode_status", "purge_last_free_scan"}
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


try:
    init_db()
except Exception:
    logger.exception("❌ [DB] Falló init_db()")
    raise