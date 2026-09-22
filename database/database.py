import asyncio
import functools
import sqlite3
import os
import contextlib
from datetime import datetime

DB_PATH = "database/bot_data.db"


@contextlib.contextmanager
def get_db_connection():
    """
    Genera una conexión SQLite optimizada contra colisiones y bloqueos de concurrencia.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Inicializa el esquema relacional y ejecuta migraciones de columnas dinámicas."""
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
        
        # Migraciones dinámicas de columnas perimetrales y Modo Free
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
            ("lock_media", "INTEGER DEFAULT 0"),
            ("lock_stickers", "INTEGER DEFAULT 0"),
            ("lock_links", "INTEGER DEFAULT 0"),
            ("lock_commands", "INTEGER DEFAULT 0"),
            ("mic_vip_price", "INTEGER DEFAULT 50"),
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
            ("speaker_queue_price", "INTEGER DEFAULT 25")
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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id INTEGER, 
                group_id INTEGER, 
                group_name TEXT,
                PRIMARY KEY (user_id, group_id)
            )
        """)

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

        try:
            cursor.execute("ALTER TABLE owner_sessions ADD COLUMN last_error TEXT")
        except sqlite3.OperationalError:
            pass

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


def register_user_group(user_id: int, group_id: int, group_name: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_groups (user_id, group_id, group_name) 
            VALUES (?, ?, ?) 
            ON CONFLICT(user_id, group_id) DO UPDATE SET group_name = excluded.group_name
        """, (user_id, group_id, group_name))
        conn.commit()


def get_user_groups(user_id: int) -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, group_name FROM user_groups WHERE user_id = ?", (user_id,))
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
            cursor.execute("SELECT warns_limit, warns_action FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "limit": row[0] if row[0] is not None else 3,
                    "action": row[1] if row[1] is not None else "mute"
                }
        except sqlite3.OperationalError:
            pass
        return {"limit": 3, "action": "mute"}


def set_warns_config(group_id: int, field: str, value):
    if field not in ["warns_limit", "warns_action"]: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO group_settings (group_id, {field}) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET {field} = excluded.{field}
        """, (group_id, value))
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
            INSERT INTO group_settings (group_id, mic_vip_price) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET mic_vip_price = excluded.mic_vip_price
        """, (group_id, price))
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
            cursor.execute("SELECT vip_mic_badge_title FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            title = row[0] if row and row[0] else "Pase VIP 24h 🎙️"
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
            INSERT INTO group_settings (group_id, vip_mic_badge_title) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET vip_mic_badge_title = excluded.vip_mic_badge_title
        """, (group_id, clean_title))
        conn.commit()


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


def add_to_whitelist(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,))
        conn.commit()


def remove_from_whitelist(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        conn.commit()


def is_whitelisted(user_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None


def approve_group(group_id: int, tier: str = "free", duration_days: int = 30):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if tier in ["pro", "ultra_pro"]:
            cursor.execute(f"""
                INSERT INTO approved_groups (group_id, tier, expires_at) 
                VALUES (?, ?, datetime('now', '+{duration_days} days')) 
                ON CONFLICT(group_id) DO UPDATE SET 
                    tier = excluded.tier,
                    expires_at = datetime('now', '+{duration_days} days')
            """, (group_id, tier))
        else:
            cursor.execute("""
                INSERT INTO approved_groups (group_id, tier, expires_at) 
                VALUES (?, 'free', NULL) 
                ON CONFLICT(group_id) DO UPDATE SET 
                    tier = 'free',
                    expires_at = NULL
            """, (group_id,))
        conn.commit()


def is_group_approved(group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM approved_groups WHERE group_id = ?", (group_id,))
        return cursor.fetchone() is not None


def get_group_tier(group_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
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


def get_user_global_tier(user_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
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
        return "free"


def check_command_limit(group_id: int, command: str, max_uses: int = 3) -> bool:
    tier = get_group_tier(group_id)
    if tier in ["pro", "ultra_pro"]: 
        return True
        
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db_connection() as conn:
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


def grant_vip_mic(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vip_mic_passes (user_id, group_id, expires_at) 
            VALUES (?, ?, datetime('now', '+24 hours'))
            ON CONFLICT(user_id, group_id) DO UPDATE SET expires_at = datetime('now', '+24 hours')
        """, (user_id, group_id))
        conn.commit()


def is_vip_mic_active(user_id: int, group_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM vip_mic_passes WHERE user_id = ? AND group_id = ? AND expires_at > datetime('now')", (user_id, group_id))
        return cursor.fetchone() is not None


def revoke_vip_mic(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vip_mic_passes WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()


def register_bot_clone(user_id: int, group_id: int, bot_token: str, bot_username: str = ""):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bot_clones SET status = 'revoked', bot_token = NULL WHERE bot_token = ? AND (user_id != ? OR group_id != ?)", 
            (bot_token, user_id, group_id)
        )
        cursor.execute("""
            INSERT INTO bot_clones (user_id, group_id, bot_token, bot_username, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(user_id, group_id) DO UPDATE SET 
                bot_token = excluded.bot_token,
                bot_username = excluded.bot_username,
                status = 'active'
        """, (user_id, group_id, bot_token, bot_username))
        conn.commit()


def get_bot_clone(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bot_token, bot_username, status FROM bot_clones WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        return cursor.fetchone()


def revoke_bot_clone(user_id: int, group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = NULL WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()


def get_all_active_clones():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, group_id, bot_token, bot_username FROM bot_clones WHERE status = 'active'")
        return cursor.fetchall()


def get_all_active_clone_tokens() -> list:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT bot_token FROM bot_clones WHERE status = 'active' AND bot_token IS NOT NULL AND bot_token != ''")
        return [row[0] for row in cursor.fetchall() if row[0]]


def save_owner_session(user_id: int, group_id: int, session_string: str, phone_number: str = None, api_id: int = None, api_hash: str = None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO owner_sessions (user_id, group_id, session_string, phone_number, api_id, api_hash, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, group_id) DO UPDATE SET 
                session_string = excluded.session_string,
                phone_number = excluded.phone_number,
                api_id = excluded.api_id,
                api_hash = excluded.api_hash,
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, group_id, session_string, phone_number, api_id, api_hash))
        conn.commit()


def get_owner_session(user_id: int, group_id: int = None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if group_id is not None:
            cursor.execute("SELECT session_string, api_id, api_hash FROM owner_sessions WHERE user_id = ? AND group_id = ? AND status = 'active'", (user_id, group_id))
        else:
            cursor.execute("SELECT session_string, api_id, api_hash FROM owner_sessions WHERE user_id = ? AND status = 'active'", (user_id,))
        return cursor.fetchone()


def get_session_by_group(group_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, session_string, api_id, api_hash FROM owner_sessions WHERE group_id = ? AND status = 'active'", (group_id,))
        return cursor.fetchone()


def get_all_active_sessions():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, group_id, session_string, api_id, api_hash FROM owner_sessions WHERE status = 'active'")
        return cursor.fetchall()


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


def update_vc_call_status(group_id: int, call_active: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE vc_schedules SET call_active = ? WHERE group_id = ?", (call_active, group_id))
        conn.commit()


def get_all_active_vc_schedules():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, days, start_time, end_time, status, call_active FROM vc_schedules WHERE status = 1")
        return cursor.fetchall()


# ==========================================
# 🛡️ BOTÓN DE PÁNICO & HELPERS DE COMPATIBILIDAD
# ==========================================
def get_panic_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT panic_active FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0


def set_panic_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, panic_active) VALUES (?, ?) 
            ON CONFLICT(group_id) DO UPDATE SET panic_active = excluded.panic_active
        """, (group_id, status))
        conn.commit()


# 🔗 Aliases agregados para empalmar perfectamente con las importaciones de user_private.py
def get_shield_status(group_id: int) -> int:
    return get_screen_shield_status(group_id)

def set_shield_status(group_id: int, status: int):
    set_screen_shield_status(group_id, status)

def get_podcast_status(group_id: int) -> int:
    cfg = get_podcast_config(group_id)
    return cfg.get("status", 0)

def set_podcast_status(group_id: int, status: int):
    set_podcast_mode(group_id, status)


def activate_panic(group_id: int, activated_by: int, chat_permissions_json: str = None) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT panic_active FROM group_settings WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if row and row[0] == 1:
            return False

        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
                   antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action
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


def deactivate_panic(group_id: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lock_media, lock_links, lock_stickers, captcha_status, captcha_mode, captcha_time,
                   antispam, antispam_delete, antiflood_msgs, antiflood_time, antiflood_action,
                   chat_permissions_json
            FROM panic_snapshots WHERE group_id = ?
        """, (group_id,))
        snap = cursor.fetchone()

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


def get_screen_shield_status(group_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT screen_shield_status FROM group_settings WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 1
        except sqlite3.OperationalError:
            return 1


def set_screen_shield_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, screen_shield_status) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET screen_shield_status = excluded.screen_shield_status
        """, (group_id, status))
        conn.commit()


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


def set_podcast_mode(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, podcast_mode_status) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET podcast_mode_status = excluded.podcast_mode_status
        """, (group_id, status))
        conn.commit()


def set_podcast_duck_volume(group_id: int, volume: int):
    volume = max(0, min(10000, volume))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, podcast_duck_volume) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET podcast_duck_volume = excluded.podcast_duck_volume
        """, (group_id, volume))
        conn.commit()


def set_noise_shield_status(group_id: int, status: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO group_settings (group_id, noise_shield_status) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET noise_shield_status = excluded.noise_shield_status
        """, (group_id, status))
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


def _make_async(sync_fn):
    @functools.wraps(sync_fn)
    async def _async_wrapper(*args, **kwargs):
        return await asyncio.to_thread(sync_fn, *args, **kwargs)
    return _async_wrapper


_ASYNC_WRAPPED_FUNCTIONS = [
    "get_or_create_user",
    "update_user_topic",
    "get_user_by_topic",
    "add_warning",
    "ban_user",
    "get_blacklist",
    "add_to_blacklist",
    "remove_from_blacklist",
    "register_user_group",
    "get_user_groups",
    "set_autolower_status",
    "get_autolower_status",
    "set_antispam_status",
    "get_antispam_status",
    "set_captcha_status",
    "get_captcha_status",
    "get_captcha_config",
    "set_captcha_config",
    "get_warns_config",
    "set_warns_config",
    "get_lock_status",
    "set_lock_status",
    "get_mic_vip_price",
    "set_mic_vip_price",
    "get_free_badge_config",
    "set_free_badge_config",
    "get_vip_badge_title",
    "set_vip_badge_title",
    "get_radar_config",
    "set_radar_config",
    "get_antispam_filter",
    "set_antispam_filter",
    "get_antispam_delete",
    "set_antispam_delete",
    "get_antiflood_config",
    "set_antiflood_config",
    "add_to_whitelist",
    "remove_from_whitelist",
    "is_whitelisted",
    "approve_group",
    "is_group_approved",
    "get_group_tier",
    "get_user_global_tier",
    "check_command_limit",
    "grant_vip_mic",
    "is_vip_mic_active",
    "revoke_vip_mic",
    "register_bot_clone",
    "get_bot_clone",
    "revoke_bot_clone",
    "get_all_active_clones",
    "get_all_active_clone_tokens",
    "save_owner_session",
    "get_owner_session",
    "get_session_by_group",
    "get_all_active_sessions",
    "revoke_owner_session",
    "get_vc_schedule",
    "set_vc_schedule",
    "update_vc_call_status",
    "get_all_active_vc_schedules",

    # --- FASE "THE BUNKER OS" ---
    "get_panic_status",
    "set_panic_status",
    "get_shield_status",
    "set_shield_status",
    "get_podcast_status",
    "set_podcast_status",
    "activate_panic",
    "deactivate_panic",
    "get_screen_shield_status",
    "set_screen_shield_status",
    "get_podcast_config",
    "set_podcast_mode",
    "set_podcast_duck_volume",
    "set_noise_shield_status",
    "get_speaker_price",
    "set_speaker_price",
    "add_to_speaker_queue",
    "get_speaker_queue",
    "pop_next_speaker",
    "remove_from_speaker_queue",
    "clear_speaker_queue",
]

for _fn_name in _ASYNC_WRAPPED_FUNCTIONS:
    if _fn_name in globals():
        globals()[_fn_name] = _make_async(globals()[_fn_name])

del _fn_name