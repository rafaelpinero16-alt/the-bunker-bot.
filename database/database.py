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
from datetime import datetime, time

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
            ("mic_vip_custom_text", "TEXT"),
            ("free_badge_status", "INTEGER DEFAULT 0"),
            ("free_badge_title", "TEXT DEFAULT 'VIP Free 🎙️'"),
            ("vip_mic_badge_title", "TEXT DEFAULT 'Pase VIP 24h 🎙️'"),
            ("autolower_custom_text", "TEXT"),
            ("autolower_custom_media_id", "TEXT"),
            ("autolower_custom_media_type", "TEXT"),
            ("reset_notice_custom_text", "TEXT"),
            ("reset_notice_custom_media_id", "TEXT"),
            ("reset_notice_custom_media_type", "TEXT"),
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
            ("log_channel_id", "TEXT"),
            ("spam_detection_mode", "TEXT DEFAULT 'smart'"),
            ("timezone", "TEXT DEFAULT 'Bogota (UTC-05)'"),
            ("chat_language", "TEXT DEFAULT 'ES'"),
            ("active_modules_count", "INTEGER DEFAULT 11")
        ]

        for col_name, col_def in settings_columns:
            try:
                cursor.execute(f"ALTER TABLE group_settings ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass 
        
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
                PRIMARY KEY (user_id, group_id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE user_groups ADD COLUMN chat_type TEXT DEFAULT 'supergroup'")
        except sqlite3.OperationalError:
            pass

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

        try:
            cursor.execute("ALTER TABLE owner_sessions ADD COLUMN last_error TEXT")
        except sqlite3.OperationalError:
            pass

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
        for col_name, col_def in channel_plan_cols:
            try:
                cursor.execute(f"ALTER TABLE channel_plans ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass

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
        for word in banned_words:
            cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (word.lower().strip(),))
        conn.commit()


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


def update_user_topic(user_id: int, topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET topic_id = ? WHERE user_id = ?", (topic_id, user_id))
        conn.commit()


def get_user_by_topic(topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE topic_id = ?", (topic_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def add_warning(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]


def reset_warnings(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = 0 WHERE user_id = ?", (user_id,))
        conn.commit()


def ban_user(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


def get_blacklist():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM blacklist")
        return [row[0] for row in cursor.fetchall()]


def add_to_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (word.lower().strip(),))
        conn.commit()


def remove_from_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE word = ?", (word.lower().strip(),))
        conn.commit()


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


def get_user_groups(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups 
            WHERE user_id = ? AND (chat_type = 'supergroup' OR chat_type = 'group' OR chat_type IS NULL)
        """, (user_id,))
        return cursor.fetchall()


def get_user_channels(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups 
            WHERE user_id = ? AND chat_type = 'channel'
        """, (user_id,))
        return cursor.fetchall()


def set_autolower_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, autolower) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET autolower = excluded.autolower
        """, (group_id, status))
        conn.commit()


def get_autolower_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT autolower FROM group_settings WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        return row[0] if row else 1


def set_antispam_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, antispam) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET antispam = excluded.antispam
        """, (group_id, status))
        conn.commit()


def get_antispam_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT antispam FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0


def set_captcha_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, captcha_status) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET captcha_status = excluded.captcha_status
        """, (group_id, status))
        conn.commit()


def get_captcha_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT captcha_status FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


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


def get_user_strikes(group_id: int, user_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT strikes FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        row = cursor.fetchone()
        return row[0] if row else 0


def reset_user_strikes(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        conn.commit()


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


def get_mic_vip_price(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT mic_vip_price FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 50
        except sqlite3.OperationalError:
            return 50


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


def get_speaker_price(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT speaker_queue_price FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 25
        except sqlite3.OperationalError:
            return 25


def set_speaker_price(group_id: int, price: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, speaker_queue_price) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET speaker_queue_price = excluded.speaker_queue_price
        """, (group_id, price))
        conn.commit()


def add_to_speaker_queue(group_id: int, user_id: int, full_name: str, username: str, stars_paid: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO speaker_queue (group_id, user_id, full_name, username, stars_paid, status)
            VALUES (?, ?, ?, ?, ?, 'waiting')
        """, (group_id, user_id, full_name, username, stars_paid))
        conn.commit()
        return cursor.lastrowid


def get_speaker_queue(group_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, full_name, username, stars_paid, created_at FROM speaker_queue
            WHERE group_id = ? AND status = 'waiting'
            ORDER BY stars_paid DESC, created_at ASC
        """, (group_id,))
        return cursor.fetchall()


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


def remove_from_speaker_queue(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM speaker_queue WHERE group_id = ? AND user_id = ? AND status = 'waiting'",
            (group_id, user_id)
        )
        conn.commit()


def clear_speaker_queue(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM speaker_queue WHERE group_id = ? AND status = 'waiting'", (group_id,))
        conn.commit()


def get_vc_monitor_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT vc_enabled FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 1
        except sqlite3.OperationalError:
            return 1


def set_vc_monitor_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, vc_enabled) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET vc_enabled = excluded.vc_enabled
        """, (group_id, status))
        conn.commit()


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


def set_sentinel_payload_config(group_id: int, field: str, value):
    valid_fields = [
        "sentinel_payload_enabled", "sentinel_payload_text", 
        "sentinel_payload_media_id", "sentinel_payload_media_type", 
        "sentinel_payload_auto_delete"
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


def get_ai_sentinel_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT ai_guardian_status, ai_copilot_status, ai_custom_prompt
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "guardian_status": row[0] if row[0] is not None else 0,
                    "copilot_status": row[1] if row[1] is not None else 0,
                    "custom_prompt": row[2] if row[2] is not None else ""
                }
        except sqlite3.OperationalError:
            pass
        return {"guardian_status": 0, "copilot_status": 0, "custom_prompt": ""}


def set_ai_sentinel_config(group_id: int, field: str, value):
    valid_fields = ["ai_guardian_status", "ai_copilot_status", "ai_custom_prompt"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


def get_ghost_purge_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT purge_action, purge_last_free_scan, purge_schedule_status, purge_schedule_time, purge_schedule_days
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "action": row[0] if row[0] else "ban",
                    "last_free_scan": row[1],
                    "schedule_status": row[2] if row[2] is not None else 0,
                    "schedule_time": row[3] if row[3] else "03:00",
                    "schedule_days": row[4] if row[4] else "1,2,3,4,5,6,7"
                }
        except sqlite3.OperationalError:
            pass
        return {"action": "ban", "last_free_scan": None, "schedule_status": 0, "schedule_time": "03:00", "schedule_days": "1,2,3,4,5,6,7"}


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


def check_can_free_purge(group_id: int) -> bool:
    cfg = get_ghost_purge_config(group_id)
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


def update_ghost_purge_scan_time(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, purge_last_free_scan) VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET purge_last_free_scan = CURRENT_TIMESTAMP
        """, (group_id,))
        conn.commit()


def get_all_active_purge_schedules() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT group_id, purge_schedule_days, purge_schedule_time, purge_action FROM group_settings WHERE purge_schedule_status = 1")
            return cursor.fetchall()
        except sqlite3.OperationalError:
            return []


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


def activate_universal_night_mode(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, lock_commands 
            FROM group_settings WHERE group_id = ?
        """, (group_id,))
        prev = cursor.fetchone() or (0, 0, 0, 0)

        cursor.execute("""
            INSERT INTO night_snapshots (group_id, lock_media, lock_links, lock_stickers, lock_commands, activated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET
                lock_media = excluded.lock_media,
                lock_links = excluded.lock_links,
                lock_stickers = excluded.lock_stickers,
                lock_commands = excluded.lock_commands,
                activated_at = CURRENT_TIMESTAMP
        """, (group_id, *prev))

        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 1, 1, 1, 1, 1)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 1,
                lock_media = 1,
                lock_links = 1,
                lock_stickers = 1,
                lock_commands = 1
        """, (group_id,))
        conn.commit()
        return True


def deactivate_universal_night_mode(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, lock_commands 
            FROM night_snapshots WHERE group_id = ?
        """, (group_id,))
        snap = cursor.fetchone() or (0, 0, 0, 0)

        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 0, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 0,
                lock_media = excluded.lock_media,
                lock_links = excluded.lock_links,
                lock_stickers = excluded.lock_stickers,
                lock_commands = excluded.lock_commands
        """, (group_id, *snap))
        cursor.execute("DELETE FROM night_snapshots WHERE group_id = ?", (group_id,))
        conn.commit()
        return True


def is_night_mode_time(start_str: str, end_str: str) -> bool:
    try:
        now = datetime.now().time()
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


def get_antispam_delete(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT antispam_delete FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


def set_antispam_delete(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, antispam_delete) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET antispam_delete = excluded.antispam_delete
        """, (group_id, status))
        conn.commit()


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


def get_service_msgs_mode(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT service_msgs_mode FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 1
        except sqlite3.OperationalError:
            return 1


def set_service_msgs_mode(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, service_msgs_mode) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET service_msgs_mode = excluded.service_msgs_mode
        """, (group_id, status))
        conn.commit()
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
from datetime import datetime, time

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
            ("mic_vip_custom_text", "TEXT"),
            ("free_badge_status", "INTEGER DEFAULT 0"),
            ("free_badge_title", "TEXT DEFAULT 'VIP Free 🎙️'"),
            ("vip_mic_badge_title", "TEXT DEFAULT 'Pase VIP 24h 🎙️'"),
            ("autolower_custom_text", "TEXT"),
            ("autolower_custom_media_id", "TEXT"),
            ("autolower_custom_media_type", "TEXT"),
            ("reset_notice_custom_text", "TEXT"),
            ("reset_notice_custom_media_id", "TEXT"),
            ("reset_notice_custom_media_type", "TEXT"),
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
            ("log_channel_id", "TEXT"),
            ("spam_detection_mode", "TEXT DEFAULT 'smart'"),
            ("timezone", "TEXT DEFAULT 'Bogota (UTC-05)'"),
            ("chat_language", "TEXT DEFAULT 'ES'"),
            ("active_modules_count", "INTEGER DEFAULT 11")
        ]

        for col_name, col_def in settings_columns:
            try:
                cursor.execute(f"ALTER TABLE group_settings ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass 
        
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
                PRIMARY KEY (user_id, group_id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE user_groups ADD COLUMN chat_type TEXT DEFAULT 'supergroup'")
        except sqlite3.OperationalError:
            pass

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

        try:
            cursor.execute("ALTER TABLE owner_sessions ADD COLUMN last_error TEXT")
        except sqlite3.OperationalError:
            pass

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
        for col_name, col_def in channel_plan_cols:
            try:
                cursor.execute(f"ALTER TABLE channel_plans ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass

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
        for word in banned_words:
            cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (word.lower().strip(),))
        conn.commit()


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


def update_user_topic(user_id: int, topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET topic_id = ? WHERE user_id = ?", (topic_id, user_id))
        conn.commit()


def get_user_by_topic(topic_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE topic_id = ?", (topic_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def add_warning(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]


def reset_warnings(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warnings = 0 WHERE user_id = ?", (user_id,))
        conn.commit()


def ban_user(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


def get_blacklist():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM blacklist")
        return [row[0] for row in cursor.fetchall()]


def add_to_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO blacklist (word) VALUES (?)", (word.lower().strip(),))
        conn.commit()


def remove_from_blacklist(word: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE word = ?", (word.lower().strip(),))
        conn.commit()


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


def get_user_groups(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups 
            WHERE user_id = ? AND (chat_type = 'supergroup' OR chat_type = 'group' OR chat_type IS NULL)
        """, (user_id,))
        return cursor.fetchall()


def get_user_channels(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_id, group_name FROM user_groups 
            WHERE user_id = ? AND chat_type = 'channel'
        """, (user_id,))
        return cursor.fetchall()


def set_autolower_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, autolower) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET autolower = excluded.autolower
        """, (group_id, status))
        conn.commit()


def get_autolower_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT autolower FROM group_settings WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        return row[0] if row else 1


def set_antispam_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, antispam) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET antispam = excluded.antispam
        """, (group_id, status))
        conn.commit()


def get_antispam_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT antispam FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0


def set_captcha_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, captcha_status) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET captcha_status = excluded.captcha_status
        """, (group_id, status))
        conn.commit()


def get_captcha_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT captcha_status FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


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


def get_user_strikes(group_id: int, user_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT strikes FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        row = cursor.fetchone()
        return row[0] if row else 0


def reset_user_strikes(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_strikes WHERE group_id = ? AND user_id = ?", (group_id, user_id))
        conn.commit()


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


def get_mic_vip_price(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT mic_vip_price FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 50
        except sqlite3.OperationalError:
            return 50


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


def get_speaker_price(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT speaker_queue_price FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 25
        except sqlite3.OperationalError:
            return 25


def set_speaker_price(group_id: int, price: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, speaker_queue_price) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET speaker_queue_price = excluded.speaker_queue_price
        """, (group_id, price))
        conn.commit()


def add_to_speaker_queue(group_id: int, user_id: int, full_name: str, username: str, stars_paid: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO speaker_queue (group_id, user_id, full_name, username, stars_paid, status)
            VALUES (?, ?, ?, ?, ?, 'waiting')
        """, (group_id, user_id, full_name, username, stars_paid))
        conn.commit()
        return cursor.lastrowid


def get_speaker_queue(group_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, full_name, username, stars_paid, created_at FROM speaker_queue
            WHERE group_id = ? AND status = 'waiting'
            ORDER BY stars_paid DESC, created_at ASC
        """, (group_id,))
        return cursor.fetchall()


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


def remove_from_speaker_queue(group_id: int, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM speaker_queue WHERE group_id = ? AND user_id = ? AND status = 'waiting'",
            (group_id, user_id)
        )
        conn.commit()


def clear_speaker_queue(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM speaker_queue WHERE group_id = ? AND status = 'waiting'", (group_id,))
        conn.commit()


def get_vc_monitor_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT vc_enabled FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 1
        except sqlite3.OperationalError:
            return 1


def set_vc_monitor_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, vc_enabled) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET vc_enabled = excluded.vc_enabled
        """, (group_id, status))
        conn.commit()


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


def set_sentinel_payload_config(group_id: int, field: str, value):
    valid_fields = [
        "sentinel_payload_enabled", "sentinel_payload_text", 
        "sentinel_payload_media_id", "sentinel_payload_media_type", 
        "sentinel_payload_auto_delete"
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


def get_ai_sentinel_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT ai_guardian_status, ai_copilot_status, ai_custom_prompt
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "guardian_status": row[0] if row[0] is not None else 0,
                    "copilot_status": row[1] if row[1] is not None else 0,
                    "custom_prompt": row[2] if row[2] is not None else ""
                }
        except sqlite3.OperationalError:
            pass
        return {"guardian_status": 0, "copilot_status": 0, "custom_prompt": ""}


def set_ai_sentinel_config(group_id: int, field: str, value):
    valid_fields = ["ai_guardian_status", "ai_copilot_status", "ai_custom_prompt"]
    if field not in valid_fields:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
        conn.commit()


def get_ghost_purge_config(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT purge_action, purge_last_free_scan, purge_schedule_status, purge_schedule_time, purge_schedule_days
                FROM group_settings WHERE group_id = ?
            """, (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "action": row[0] if row[0] else "ban",
                    "last_free_scan": row[1],
                    "schedule_status": row[2] if row[2] is not None else 0,
                    "schedule_time": row[3] if row[3] else "03:00",
                    "schedule_days": row[4] if row[4] else "1,2,3,4,5,6,7"
                }
        except sqlite3.OperationalError:
            pass
        return {"action": "ban", "last_free_scan": None, "schedule_status": 0, "schedule_time": "03:00", "schedule_days": "1,2,3,4,5,6,7"}


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


def check_can_free_purge(group_id: int) -> bool:
    cfg = get_ghost_purge_config(group_id)
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


def update_ghost_purge_scan_time(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, purge_last_free_scan) VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET purge_last_free_scan = CURRENT_TIMESTAMP
        """, (group_id,))
        conn.commit()


def get_all_active_purge_schedules() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT group_id, purge_schedule_days, purge_schedule_time, purge_action FROM group_settings WHERE purge_schedule_status = 1")
            return cursor.fetchall()
        except sqlite3.OperationalError:
            return []


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


def activate_universal_night_mode(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, lock_commands 
            FROM group_settings WHERE group_id = ?
        """, (group_id,))
        prev = cursor.fetchone() or (0, 0, 0, 0)

        cursor.execute("""
            INSERT INTO night_snapshots (group_id, lock_media, lock_links, lock_stickers, lock_commands, activated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_id) DO UPDATE SET
                lock_media = excluded.lock_media,
                lock_links = excluded.lock_links,
                lock_stickers = excluded.lock_stickers,
                lock_commands = excluded.lock_commands,
                activated_at = CURRENT_TIMESTAMP
        """, (group_id, *prev))

        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 1, 1, 1, 1, 1)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 1,
                lock_media = 1,
                lock_links = 1,
                lock_stickers = 1,
                lock_commands = 1
        """, (group_id,))
        conn.commit()
        return True


def deactivate_universal_night_mode(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, lock_commands 
            FROM night_snapshots WHERE group_id = ?
        """, (group_id,))
        snap = cursor.fetchone() or (0, 0, 0, 0)

        cursor.execute("""
            INSERT INTO group_settings (group_id, night_mode_status, lock_media, lock_links, lock_stickers, lock_commands)
            VALUES (?, 0, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                night_mode_status = 0,
                lock_media = excluded.lock_media,
                lock_links = excluded.lock_links,
                lock_stickers = excluded.lock_stickers,
                lock_commands = excluded.lock_commands
        """, (group_id, *snap))
        cursor.execute("DELETE FROM night_snapshots WHERE group_id = ?", (group_id,))
        conn.commit()
        return True


def is_night_mode_time(start_str: str, end_str: str) -> bool:
    try:
        now = datetime.now().time()
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


def get_antispam_delete(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT antispam_delete FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


def set_antispam_delete(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, antispam_delete) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET antispam_delete = excluded.antispam_delete
        """, (group_id, status))
        conn.commit()


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


def get_service_msgs_mode(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT service_msgs_mode FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 1
        except sqlite3.OperationalError:
            return 1


def set_service_msgs_mode(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, service_msgs_mode) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET service_msgs_mode = excluded.service_msgs_mode
        """, (group_id, status))
        conn.commit()        