"""
database.py — The Bunker OS (Sqlite3 Async-Wrapper Pattern)
Núcleo relacional de persistencia, canales, telemetría y seguridad perimetral.
The Bunker Command OS © 2026 — Cloud Media Management
"""
import asyncio
import functools
import sqlite3
import os
import contextlib
import threading
import logging
import re
import json
import hmac
import hashlib
from datetime import datetime

DB_PATH = "database/bot_data.db"

# Thread-local storage para reutilización de conexiones por hilo bajo alta concurrencia
_thread_local = threading.local()

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


@contextlib.contextmanager
def get_db_connection():
    """
    Genera y reutiliza conexiones SQLite por hilo optimizadas con WAL y modo concurrente.
    Evita la saturación del ThreadPoolExecutor reutilizando la conexión en hilos de trabajo.
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        _thread_local.conn = conn
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise


logger = logging.getLogger("database")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def db_async(fn):
    """Ejecuta la función SQLite síncrona en un hilo. La versión síncrona queda en `fn.sync` para usos internos."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    wrapper.sync = fn
    return wrapper


def _ident(name: str) -> str:
    """Valida un nombre de columna/tabla antes de interpolarlo en SQL."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Identificador SQL no permitido: {name!r}")
    return name


def _ensure_columns(cursor, table: str, columns) -> None:
    """Migración idempotente: añade solo las columnas que aún no existen."""
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({_ident(table)})")}
    for col_name, col_def in columns:
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {_ident(col_name)} {col_def}")


def _upsert_setting(group_id: int, column: str, value) -> None:
    """Guarda una columna de group_settings (crea la fila si no existe)."""
    column = _ident(column)
    with get_db_connection() as conn:
        conn.execute(
            f"INSERT INTO group_settings (group_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT(group_id) DO UPDATE SET {column} = excluded.{column}",
            (group_id, value),
        )
        conn.commit()


def _get_setting(group_id: int, column: str, default=0):
    """Lee una columna de group_settings; devuelve `default` si falta la fila, el valor es NULL o la columna no existe."""
    column = _ident(column)
    with get_db_connection() as conn:
        try:
            row = conn.execute(f"SELECT {column} FROM group_settings WHERE group_id = ?", (group_id,)).fetchone()
        except sqlite3.OperationalError:
            return default
    return row[0] if row and row[0] is not None else default


def init_db():
    """Inicializa el esquema relacional, canales, telemetría y migraciones dinámicas para The Bunker OS."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with get_db_connection() as conn:
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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS command_usage (
                group_id INTEGER, 
                command TEXT, 
                usage_date TEXT, 
                count INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, command, usage_date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vip_mic_passes (
                user_id INTEGER, 
                group_id INTEGER, 
                expires_at TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vip_mic_passes ON vip_mic_passes (user_id, group_id, expires_at)")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id INTEGER, 
                group_id INTEGER, 
                group_name TEXT,
                chat_type TEXT DEFAULT 'supergroup',
                PRIMARY KEY (user_id, group_id)
            )
        """)
        _ensure_columns(cursor, "user_groups", [("chat_type", "TEXT DEFAULT 'supergroup'")])

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_tip_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                target_value TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, target_value)
            )
        """)
        _ensure_columns(cursor, "group_tip_targets", [("is_active", "INTEGER DEFAULT 1")])

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_clones (
                user_id INTEGER, 
                group_id INTEGER, 
                bot_token TEXT UNIQUE, 
                bot_username TEXT,
                status TEXT DEFAULT 'active', 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS owner_sessions (
                user_id INTEGER,
                group_id INTEGER,
                session_string TEXT NOT NULL,
                phone_number TEXT,
                api_id INTEGER,
                api_hash TEXT,
                status TEXT DEFAULT 'active',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vc_schedules (
                group_id INTEGER PRIMARY KEY,
                days TEXT DEFAULT '1,2,3,4,5,6,7',
                start_time TEXT DEFAULT '20:00',
                end_time TEXT DEFAULT '23:00',
                status INTEGER DEFAULT 0,
                call_active INTEGER DEFAULT 0
            )
        """)

        _ensure_columns(cursor, "owner_sessions", [("last_error", "TEXT")])

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS panic_snapshots (
                group_id INTEGER PRIMARY KEY,
                lock_media INTEGER DEFAULT 0,
                lock_links INTEGER DEFAULT 0,
                lock_stickers INTEGER DEFAULT 0,
                captcha_status INTEGER DEFAULT 0,
                captcha_mode INTEGER DEFAULT 1,
                captcha_time INTEGER DEFAULT 60,
                antispam INTEGER DEFAULT 0,
                antispam_delete INTEGER DEFAULT 0,
                antiflood_msgs INTEGER DEFAULT 10,
                antiflood_time INTEGER DEFAULT 15,
                antiflood_action TEXT DEFAULT 'kick',
                chat_permissions_json TEXT,
                activated_by INTEGER,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS night_snapshots (
                group_id INTEGER PRIMARY KEY,
                lock_media INTEGER DEFAULT 0,
                lock_links INTEGER DEFAULT 0,
                lock_stickers INTEGER DEFAULT 0,
                lock_commands INTEGER DEFAULT 0,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_strikes (
                group_id INTEGER,
                user_id INTEGER,
                strikes INTEGER DEFAULT 0,
                last_strike_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_reason TEXT DEFAULT 'Regla violada',
                PRIMARY KEY (group_id, user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_strikes ON user_strikes (group_id, user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS speaker_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                stars_paid INTEGER DEFAULT 0,
                status TEXT DEFAULT 'waiting',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_speaker_queue_group ON speaker_queue (group_id, status)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_tips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                stars_amount INTEGER,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_tips ON group_tips (group_id, user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flagged_userbots (
                user_id INTEGER,
                group_id INTEGER,
                reason TEXT,
                flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_flagged_userbots ON flagged_userbots (group_id, user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_settings (
                channel_id INTEGER PRIMARY KEY,
                sub_price INTEGER DEFAULT 0,
                grace_days INTEGER DEFAULT 1,
                auto_kick INTEGER DEFAULT 1,
                notify_renewal INTEGER DEFAULT 1,
                custom_welcome TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_plans (
                plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                plan_name TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                stars_price INTEGER NOT NULL,
                status TEXT DEFAULT 'active',
                promo_text TEXT,
                media_id TEXT,
                media_type TEXT,
                target_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_plans_channel ON channel_plans (channel_id, status)")

        channel_plan_cols = [
            ("status", "TEXT DEFAULT 'active'"),
            ("promo_text", "TEXT"),
            ("media_id", "TEXT"),
            ("media_type", "TEXT"),
            ("target_link", "TEXT"),
            ("broadcast_chat_id", "INTEGER"),
            ("broadcast_interval_hours", "INTEGER"),
            ("next_broadcast_at", "TIMESTAMP"),
            ("broadcast_enabled", "INTEGER DEFAULT 0")
        ]
        _ensure_columns(cursor, "channel_plans", channel_plan_cols)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_plans_broadcast "
            "ON channel_plans (broadcast_enabled, next_broadcast_at)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                plan_id INTEGER,
                stars_paid INTEGER NOT NULL,
                invite_link TEXT,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active',
                last_warned_at TIMESTAMP,
                FOREIGN KEY(plan_id) REFERENCES channel_plans(plan_id),
                UNIQUE(channel_id, user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_subs_audit ON channel_subscriptions (status, expires_at)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_owner_registry (
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                title      TEXT,
                updated_at INTEGER,
                PRIMARY KEY (user_id, channel_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state_store (
                bot_id     INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                kind       TEXT    NOT NULL,
                payload    TEXT,
                updated_at INTEGER,
                PRIMARY KEY (bot_id, user_id, kind)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_user_activity (
                group_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                message_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_user_activity ON chat_user_activity (group_id, message_count DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_monthly_metrics (
                group_id INTEGER,
                month_key TEXT,
                total_messages INTEGER DEFAULT 0,
                total_users INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, month_key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_user_reputation (
                group_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                last_xp_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reputation_xp ON chat_user_reputation (group_id, xp DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_hourly_activity (
                group_id INTEGER,
                day_of_week INTEGER,
                hour_of_day INTEGER,
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, day_of_week, hour_of_day)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
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
        else:
            cursor.execute(
                "INSERT INTO users (user_id, username, full_name, topic_id, warnings, is_banned) VALUES (?, ?, ?, NULL, 0, 0)", 
                (user_id, username, full_name)
            )
            conn.commit()
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
        cursor.execute("UPDATE users SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]


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
        cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (word.lower().strip(),))
        conn.commit()


@db_async
def remove_from_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE word = ?", (word.lower().strip(),))
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
def add_user_strike(group_id: int, user_id: int, reason: str = "Infracción de reglas") -> int:
    with get_db_connection() as conn:
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
    if lock_name not in valid_locks:
        return 0
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
    if lock_name not in valid_locks:
        return
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
            cursor.execute(f"""
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
    _upsert_setting(group_id, field, value)


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
    with get_db_connection() as conn:
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
    import secrets
    token = secrets.token_urlsafe(32)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO web_sessions (token, user_id, expires_at)
            VALUES (?, ?, datetime('now', '+5 minutes'))
        """, (token, user_id))
        conn.commit()
    return token


@db_async
def get_user_by_web_session(token: str) -> int:
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
    _upsert_setting(group_id, col_name, value)


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
    if field not in valid:
        return
    _upsert_setting(group_id, field, value)


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


_AI_SENTINEL_FIELDS = {
    "ai_guardian_status", "ai_copilot_status", "ai_custom_prompt",
    "ai_response_mode", "ai_response_chance", "ai_personality_tone"
}


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


@db_async
def set_ai_sentinel_config(group_id: int, field: str, value):
    if field not in _AI_SENTINEL_FIELDS:
        raise ValueError(f"Campo de IA no permitido: {field!r}")
    _upsert_setting(group_id, field, value)


@db_async
def save_ai_chat_context(chat_id: int, user_id: int, role: str, content: str, max_history: int = 12):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ai_chat_context (chat_id, user_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (chat_id, user_id, role, content.strip()))

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
        cursor.execute("""
            SELECT role, content FROM ai_chat_context 
            WHERE chat_id = ? 
            ORDER BY created_at ASC, id ASC LIMIT ?
        """, (chat_id, limit))
        return [{"role": r[0], "content": r[1]} for r in cursor.fetchall()]


@db_async
def clear_ai_chat_context(chat_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ai_chat_context WHERE chat_id = ?", (chat_id,))
        conn.commit()


@db_async
def get_reputation_settings(group_id: int) -> dict:
    return {
        "enabled": int(_get_setting(group_id, "reputation_enabled", 1) or 1),
        "multiplier": float(_get_setting(group_id, "reputation_xp_multiplier", 1.0) or 1.0)
    }


@db_async
def set_reputation_setting(group_id: int, field: str, value):
    if field not in ("reputation_enabled", "reputation_xp_multiplier"):
        raise ValueError(f"Campo de reputación no permitido: {field!r}")
    _upsert_setting(group_id, field, value)


@db_async
def add_user_reputation_xp(
    group_id: int, 
    user_id: int, 
    full_name: str, 
    username: str, 
    base_xp: int = 10, 
    cooldown_seconds: int = 45
) -> dict:
    rep_cfg = get_reputation_settings.sync(group_id)
    if rep_cfg["enabled"] != 1:
        return {"awarded": False, "xp": 0, "level": 1, "leveled_up": False, "reason": "disabled"}

    effective_xp = max(1, int(base_xp * rep_cfg["multiplier"]))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT xp, level, CAST((julianday('now') - julianday(last_xp_at)) * 86400 AS INTEGER)
            FROM chat_user_reputation WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        row = cursor.fetchone()

        if row:
            current_xp, current_level, elapsed_sec = row
            if elapsed_sec is not None and elapsed_sec < cooldown_seconds:
                return {"awarded": False, "xp": current_xp, "level": current_level, "leveled_up": False, "reason": "cooldown"}
        else:
            current_xp, current_level = 0, 1

        new_xp = current_xp + effective_xp
        new_level = int((new_xp / 100) ** 0.5) + 1
        leveled_up = new_level > current_level

        cursor.execute("""
            INSERT INTO chat_user_reputation (group_id, user_id, full_name, username, xp, level, last_xp_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id, user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username,
                xp = ?,
                level = ?,
                last_xp_at = CURRENT_TIMESTAMP
        """, (group_id, user_id, full_name, username or "", new_xp, new_level, new_xp, new_level))
        conn.commit()

        return {
            "awarded": True,
            "xp": new_xp,
            "level": new_level,
            "leveled_up": leveled_up,
            "gained_xp": effective_xp
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
                "user_id": r[0],
                "name": r[1] or f"User {r[0]}",
                "username": f"@{r[2]}" if r[2] else "",
                "xp": r[3],
                "level": r[4]
            }
            for r in rows
        ]


@db_async
def record_hourly_chat_activity(group_id: int, dt: datetime = None):
    now = dt or datetime.now()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_hourly_activity (group_id, day_of_week, hour_of_day, message_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(group_id, day_of_week, hour_of_day) DO UPDATE SET
                message_count = message_count + 1
        """, (group_id, now.isoweekday(), now.hour))
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
        "group_id": group_id,
        "matrix": matrix,
        "days": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    }


@db_async
def get_chat_dashboard_data(chat_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tier FROM approved_groups WHERE group_id = ?", (chat_id,))
        row = cursor.fetchone()
        tier = row[0] if row else "free"

    with get_db_connection() as conn:
        cursor = conn.cursor()
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
        
        captcha_active = bool(row[5]) if row and row[5] is not None else True
        autolower_active = bool(row[6]) if row and row[6] is not None else True
        shield_active = bool(row[7]) if row and row[7] is not None else True
        linklock_active = bool(row[8]) if row and row[8] is not None else False

        return {
            "chat_id": str(chat_id),
            "plan": {
                "name": tier.capitalize(),
                "status": "active" if tier != "free" else "empty"
            },
            "log_channel": {
                "enabled": log_id is not None,
                "channel_id": log_id
            },
            "modules": {
                "active": active_mods,
                "total": 91
            },
            "protection": {
                "enabled": True,
                "spam_mode": spam_mode,
                "timezone": tz,
                "language": lang
            },
            "switches": {
                "captcha": captcha_active,
                "autolower": autolower_active,
                "shield": shield_active,
                "linklock": linklock_active
            },
            "modules_errors": [],
            "footer_metrics": {
                "filters_active": 3,
                "filters_triggered": 0,
                "triggers_active": 0,
                "triggers_triggered": 0,
                "reputation_enabled": True,
                "reputation_issued": 0,
                "manual_moderation_triggered": 0
            }
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
            months = ["May '26", "Jun '26", "Jul '26", "Aug '26", "Sep '26"]
            return {"months": months, "mau": [0, 0, 0, 0, 0], "messages": [0, 0, 0, 0, 0], "messages_per_user": [0, 0, 0, 0, 0]}

        months = [r[0] for r in reversed(rows)]
        messages = [r[1] for r in reversed(rows)]
        mau = [r[2] for r in reversed(rows)]
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
            act_level = 4 if r[3] > 300 else (3 if r[3] > 150 else (2 if r[3] > 50 else 1))
            res.append({
                "rank": idx,
                "name": r[1] or f"User {r[0]}",
                "badge": f"@{r[2]}" if r[2] else "",
                "activity_level": act_level,
                "messages": r[3]
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

        if "stars_price" in settings or "duration_days" in settings or "target_link" in settings:
            price = settings.get("stars_price", 150)
            days = settings.get("duration_days", 30)
            link = settings.get("target_link", "")
            cursor.execute("""
                SELECT plan_id FROM channel_plans 
                WHERE channel_id = ? AND status = 'active' 
                ORDER BY plan_id ASC LIMIT 1
            """, (chat_id,))
            existing_p = cursor.fetchone()
            if existing_p:
                cursor.execute("""
                    UPDATE channel_plans 
                    SET duration_days = ?, stars_price = ?, target_link = ? 
                    WHERE plan_id = ?
                """, (days, price, link, existing_p[0]))
            else:
                cursor.execute("""
                    INSERT INTO channel_plans (channel_id, plan_name, duration_days, stars_price, target_link, status)
                    VALUES (?, 'Acceso VIP', ?, ?, ?, 'active')
                """, (chat_id, days, price, link))

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
            SELECT s.user_id, p.plan_name, s.stars_paid, 
                   CAST((julianday(s.expires_at) - julianday('now')) AS INTEGER) as days_left,
                   u.username
            FROM channel_subscriptions s
            JOIN channel_plans p ON s.plan_id = p.plan_id
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
            return False


BACKUP_SECRET_SALT = os.getenv("BACKUP_SECRET_SALT", "bunker-secret-vault-2026")


@db_async
def export_group_configuration(group_id: int) -> str:
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

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
        "version": "6.0",
        "exported_at": datetime.now().isoformat(),
        "source_group_id": group_id,
        "settings": settings_dict,
        "vc_schedule": sched_dict,
        "tip_targets": targets,
        "reputation_enabled": int(_get_setting(group_id, "reputation_enabled", 1) or 1),
        "reputation_xp_multiplier": float(_get_setting(group_id, "reputation_xp_multiplier", 1.0) or 1.0),
    }

    raw_data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(BACKUP_SECRET_SALT.encode("utf-8"), raw_data.encode("utf-8"), hashlib.sha256).hexdigest()

    package = {
        "payload": payload,
        "signature": signature
    }
    return json.dumps(package, indent=2, ensure_ascii=False)


@db_async
def import_group_configuration(target_group_id: int, backup_json: str) -> tuple[bool, str]:
    try:
        package = json.loads(backup_json)
        payload = package.get("payload")
        received_sig = package.get("signature")

        if not payload or not received_sig:
            return False, "Estructura de paquete inválida."

        raw_data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_sig = hmac.new(BACKUP_SECRET_SALT.encode("utf-8"), raw_data.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, received_sig):
            return False, "Firma digital no válida. El archivo ha sido manipulado."

        settings = payload.get("settings", {})
        sched = payload.get("vc_schedule", {})
        targets = payload.get("tip_targets", [])

        with get_db_connection() as conn:
            cursor = conn.cursor()

            if settings:
                clean_cols = [c for c in settings.keys() if _IDENT_RE.match(c)]
                if clean_cols:
                    placeholders = ", ".join([f"{col} = ?" for col in clean_cols])
                    values = [settings[col] for col in clean_cols]
                    values.append(target_group_id)

                    cursor.execute("""
                        INSERT INTO group_settings (group_id) VALUES (?)
                        ON CONFLICT(group_id) DO NOTHING
                    """, (target_group_id,))

                    cursor.execute(f"UPDATE group_settings SET {placeholders} WHERE group_id = ?", tuple(values))

            if sched:
                cursor.execute("""
                    INSERT INTO vc_schedules (group_id, days, start_time, end_time, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        days = excluded.days,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        status = excluded.status
                """, (target_group_id, sched.get("days", "1,2,3,4,5,6,7"), sched.get("start_time", "20:00"), sched.get("end_time", "23:00"), sched.get("status", 0)))

            for target_val in targets:
                cursor.execute("""
                    INSERT OR IGNORE INTO group_tip_targets (group_id, target_value, is_active)
                    VALUES (?, ?, 1)
                """, (target_group_id, target_val))

            conn.commit()

        return True, "Configuración importada y verificada con éxito."
    except Exception as ex:
        return False, f"Error durante la restauración: {ex}"


try:
    init_db()
except Exception:
    logger.exception("❌ [DB] Falló init_db(); la base de datos puede estar incompleta")
    raise