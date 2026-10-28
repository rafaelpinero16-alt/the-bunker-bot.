"""
models_fase3.py — The Bunker OS · v40 · Fase 3 (Monetización, Auto-Mod y Dashboard Avanzado)

Modelo de datos de la Fase 3. Este módulo NO abre conexiones: solo define el esquema (aditivo e idempotente), las
constantes de negocio y los tipos de fila (TypedDict) que comparten crud_fase3.py, los handlers y la API de la Mini App.

Tablas (prefijo f3_, nunca colisionan con las existentes):
  • f3_sanctions ............ libro de sanciones ejecutadas por el bot (manual / automod / masiva) con su reversión.
  • f3_admin_stats .......... puntuación acumulada de cada administrador (AdminStats del leaderboard).
  • f3_automod_infractions .. infracciones detectadas por el motor Auto-Mod (evidencia de la escalada automática).
  • f3_automod_config ....... configuración Auto-Mod por comunidad.
  • f3_mass_jobs ............ trabajos de gestión masiva (POST /mass-action) con progreso persistente.

Compatibilidad: CREATE TABLE IF NOT EXISTS + columnas añadidas con DEFAULT. Ninguna tabla anterior se modifica.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Final, List, Literal, Optional, Tuple, TypedDict

SCHEMA_VERSION: Final[int] = 40

# ==========================================
# 🎯 CONSTANTES DE NEGOCIO
# ==========================================
SanctionAction = Literal["ban", "kick", "mute"]
SanctionSource = Literal["manual", "automod", "mass", "blacklist"]
MassAction = Literal["ban", "unban"]
MassJobStatus = Literal["queued", "running", "done", "failed", "interrupted"]
AutomodRule = Literal["phishing", "scam", "invite_spam", "mention_flood", "char_flood", "repeat_flood", "obfuscation"]
AutomodInfractionAction = Literal["delete", "mute"]

SANCTION_ACTIONS: Final[Tuple[str, ...]] = ("ban", "kick", "mute")
SANCTION_SOURCES: Final[Tuple[str, ...]] = ("manual", "automod", "mass", "blacklist")
MASS_ACTIONS: Final[Tuple[str, ...]] = ("ban", "unban")
MASS_JOB_STATUSES: Final[Tuple[str, ...]] = ("queued", "running", "done", "failed", "interrupted")
AUTOMOD_RULES: Final[Tuple[str, ...]] = ("phishing", "scam", "invite_spam", "mention_flood", "char_flood",
                                         "repeat_flood", "obfuscation")
AUTOMOD_INFRACTION_ACTIONS: Final[Tuple[str, ...]] = ("delete", "mute")

# Puntos del leaderboard: cada sanción manual suma; un falso positivo resta lo sumado + la penalización.
ADMIN_POINTS: Final[Dict[str, int]] = {"ban": 3, "kick": 2, "mute": 1}
FALSE_POSITIVE_PENALTY: Final[int] = 5

# Auto-Mod: valores por defecto y límites aceptados desde la API / el bot.
AUTOMOD_DEFAULTS: Final[Dict[str, Any]] = {
    "enabled": 0,                 # opt-in: el dueño lo activa (/automod on o la Mini App)
    "rule_phishing": 1,
    "rule_scam": 1,
    "rule_invite_spam": 1,
    "rule_mention_flood": 1,
    "rule_char_flood": 1,
    "rule_repeat_flood": 1,
    "rule_obfuscation": 1,
    "escalation_threshold": 3,    # infracciones automáticas…
    "escalation_window_hours": 24,  # …dentro de esta ventana → baneo permanente
    "infraction_action": "delete",
    "mute_minutes": 30,
}
AUTOMOD_LIMITS: Final[Dict[str, Tuple[int, int]]] = {
    "escalation_threshold": (2, 10),
    "escalation_window_hours": (1, 168),
    "mute_minutes": (1, 10080),
}

MASS_ACTION_MAX_USERS: Final[int] = 200
HISTORY_MAX_ITEMS: Final[int] = 200
EXCERPT_MAX: Final[int] = 300
REASON_MAX: Final[int] = 300


# ==========================================
# 🧾 TIPOS DE FILA (Pylance / Pyright)
# ==========================================
class SanctionRow(TypedDict):
    id: int
    group_id: int
    user_id: int
    user_name: Optional[str]
    admin_id: Optional[int]
    admin_name: Optional[str]
    action: str
    source: str
    reason: Optional[str]
    duration_minutes: Optional[int]
    until_ts: Optional[int]
    global_ban_set: bool
    points_awarded: int
    created_at: int
    reverted_at: Optional[int]
    reverted_by: Optional[int]
    revert_reason: Optional[str]
    cleared_at: Optional[int]


class AdminStatsRow(TypedDict):
    group_id: int
    admin_id: int
    admin_name: Optional[str]
    points: int
    actions: int
    bans: int
    kicks: int
    mutes: int
    false_positives: int
    last_action_at: Optional[int]


class LeaderboardEntry(TypedDict):
    rank: int
    admin_id: int
    admin_name: Optional[str]
    points: int
    actions: int
    bans: int
    kicks: int
    mutes: int
    false_positives: int
    accuracy: float               # 1 - false_positives / actions (1.0 sin acciones)
    window_actions: int           # sanciones dentro de la ventana pedida
    last_action_at: Optional[int]


class InfractionRow(TypedDict):
    id: int
    group_id: int
    user_id: int
    rule: str
    evidence: Optional[str]
    excerpt: Optional[str]
    message_id: Optional[int]
    action_taken: str
    created_at: int
    cleared_at: Optional[int]


class AutomodConfig(TypedDict):
    group_id: int
    enabled: bool
    rules: Dict[str, bool]
    escalation_threshold: int
    escalation_window_hours: int
    infraction_action: str
    mute_minutes: int
    updated_at: Optional[int]
    updated_by: Optional[int]


class MassJobResult(TypedDict):
    user_id: int
    status: str                   # ok | skipped | failed
    code: Optional[str]           # telegram_ops.ErrorKind o motivo del salto
    detail: Optional[str]


class MassJobRow(TypedDict):
    id: str
    group_id: int
    requested_by: int
    action: str
    reason: Optional[str]
    duration_minutes: Optional[int]
    total: int
    done: int
    ok: int
    skipped: int
    failed: int
    status: str
    results: List[MassJobResult]
    error: Optional[str]
    created_at: int
    updated_at: int
    finished_at: Optional[int]


class HistoryItem(TypedDict):
    ts: int
    kind: str                     # sanction | infraction | admin_action | security | appeal | strikes
    action: str
    source: Optional[str]
    reason: Optional[str]
    actor_id: Optional[int]
    actor_name: Optional[str]
    detail: Optional[str]
    sanction_id: Optional[int]
    reverted: bool
    cleared: bool


class UserProfile(TypedDict):
    user_id: int
    full_name: Optional[str]
    username: Optional[str]
    message_count: int
    last_active: Optional[str]
    xp: int
    level: int
    global_banned: bool


class UserSummary(TypedDict):
    strikes: int
    infractions_window: int
    sanctions_total: int
    active_sanctions: int
    reverted_sanctions: int
    appeals_total: int
    appeals_paid: int


class UserHistory(TypedDict):
    group_id: int
    profile: UserProfile
    summary: UserSummary
    items: List[HistoryItem]


class ClearReport(TypedDict):
    strikes_reset: bool
    infractions_cleared: int
    sanctions_cleared: int
    raid_released: bool


class BanState(TypedDict):
    has_ban_record: bool          # existe una sanción de baneo vigente en el libro f3 o una apelación abierta
    reverted_after_appeal: bool   # el baneo ya se revirtió (falso positivo) después de crearse la apelación
    global_banned: bool


class UserSearchHit(TypedDict):
    user_id: int
    full_name: Optional[str]
    username: Optional[str]
    message_count: int
    last_active: Optional[str]


# ==========================================
# 🗄️ ESQUEMA (aditivo, idempotente)
# ==========================================
_TABLES: Final[Tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS f3_sanctions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        user_name TEXT,
        admin_id INTEGER,
        admin_name TEXT,
        action TEXT NOT NULL,
        source TEXT NOT NULL,
        reason TEXT,
        duration_minutes INTEGER,
        until_ts INTEGER,
        global_ban_set INTEGER NOT NULL DEFAULT 0,
        points_awarded INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        reverted_at INTEGER,
        reverted_by INTEGER,
        revert_reason TEXT,
        cleared_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS f3_admin_stats (
        group_id INTEGER NOT NULL,
        admin_id INTEGER NOT NULL,
        admin_name TEXT,
        points INTEGER NOT NULL DEFAULT 0,
        actions INTEGER NOT NULL DEFAULT 0,
        bans INTEGER NOT NULL DEFAULT 0,
        kicks INTEGER NOT NULL DEFAULT 0,
        mutes INTEGER NOT NULL DEFAULT 0,
        false_positives INTEGER NOT NULL DEFAULT 0,
        last_action_at INTEGER,
        PRIMARY KEY (group_id, admin_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS f3_automod_infractions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rule TEXT NOT NULL,
        evidence TEXT,
        excerpt TEXT,
        message_id INTEGER,
        action_taken TEXT NOT NULL DEFAULT 'delete',
        created_at INTEGER NOT NULL,
        cleared_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS f3_automod_config (
        group_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        rule_phishing INTEGER NOT NULL DEFAULT 1,
        rule_scam INTEGER NOT NULL DEFAULT 1,
        rule_invite_spam INTEGER NOT NULL DEFAULT 1,
        rule_mention_flood INTEGER NOT NULL DEFAULT 1,
        rule_char_flood INTEGER NOT NULL DEFAULT 1,
        rule_repeat_flood INTEGER NOT NULL DEFAULT 1,
        rule_obfuscation INTEGER NOT NULL DEFAULT 1,
        escalation_threshold INTEGER NOT NULL DEFAULT 3,
        escalation_window_hours INTEGER NOT NULL DEFAULT 24,
        infraction_action TEXT NOT NULL DEFAULT 'delete',
        mute_minutes INTEGER NOT NULL DEFAULT 30,
        updated_at INTEGER,
        updated_by INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS f3_mass_jobs (
        id TEXT PRIMARY KEY,
        group_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL,
        action TEXT NOT NULL,
        reason TEXT,
        duration_minutes INTEGER,
        user_ids TEXT NOT NULL,
        total INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        ok INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        results TEXT NOT NULL DEFAULT '[]',
        error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        finished_at INTEGER
    )
    """,
)

_INDEXES: Final[Tuple[str, ...]] = (
    "CREATE INDEX IF NOT EXISTS idx_f3_sanctions_user ON f3_sanctions (group_id, user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_f3_sanctions_admin ON f3_sanctions (group_id, admin_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_f3_infractions_user ON f3_automod_infractions (group_id, user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_f3_mass_jobs_group ON f3_mass_jobs (group_id, created_at)",
)

# Columnas que versiones futuras podrían añadir: (tabla, columna, definición). Hoy vacía; se deja el mecanismo.
_ADDED_COLUMNS: Final[Tuple[Tuple[str, str, str], ...]] = ()


def ensure_fase3_schema(conn: sqlite3.Connection) -> None:
    """Crea (si faltan) las tablas e índices de la Fase 3. Idempotente; el llamador gestiona la transacción."""
    for ddl in _TABLES:
        conn.execute(ddl)
    for ddl in _INDEXES:
        conn.execute(ddl)
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
