"""
crud_fase3.py — The Bunker OS · v40 · Fase 3 · Capa de acceso a datos

Operaciones tipadas sobre las tablas de database/models_fase3.py, reutilizando el núcleo de database/database.py:
la MISMA conexión por hilo en modo WAL, BEGIN IMMEDIATE para escrituras con lectura previa (_write_transaction) y
el decorador @db_async (la función síncrona corre en asyncio.to_thread y queda en `.sync`). Así la Fase 3 nunca
bloquea el event loop de aiogram / FastAPI y comparte el control de concurrencia del resto del sistema.

El esquema se crea solo la primera vez que se usa (y main.py lo fuerza al arrancar con ensure_schema()).
Las consultas a tablas de otras fases (security_events, ban_appeals, chat_user_activity…) toleran que no existan.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import calendar
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from database.database import _write_transaction, db_async, get_db_connection
from database.models_fase3 import (
    ADMIN_POINTS,
    AUTOMOD_DEFAULTS,
    AUTOMOD_INFRACTION_ACTIONS,
    AUTOMOD_LIMITS,
    AUTOMOD_RULES,
    EXCERPT_MAX,
    FALSE_POSITIVE_PENALTY,
    HISTORY_MAX_ITEMS,
    MASS_ACTION_MAX_USERS,
    MASS_ACTIONS,
    MASS_JOB_STATUSES,
    REASON_MAX,
    SANCTION_ACTIONS,
    SANCTION_SOURCES,
    AdminStatsRow,
    AutomodConfig,
    BanState,
    ClearReport,
    HistoryItem,
    InfractionRow,
    LeaderboardEntry,
    MassJobResult,
    MassJobRow,
    SanctionRow,
    UserHistory,
    UserProfile,
    UserSearchHit,
    UserSummary,
    ensure_fase3_schema,
)

logger = logging.getLogger("bunker.crud_fase3")

INFRACTION_RETENTION_DAYS = 90
MASS_JOB_RETENTION_DAYS = 30
MASS_JOB_RESULTS_MAX = MASS_ACTION_MAX_USERS

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


class Fase3ValidationError(ValueError):
    """Dato rechazado por la validación de la Fase 3. El mensaje tiene la forma 'campo: motivo'."""


def _now() -> int:
    return int(time.time())


def _clip(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value)).strip()
    if not text:
        return None
    return text[:limit]


def _ensure_schema_sync() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with _write_transaction() as conn:
            ensure_fase3_schema(conn)
        _SCHEMA_READY = True


@db_async
def ensure_schema() -> bool:
    """Crea el esquema de la Fase 3 si falta (main.py lo invoca al arrancar). Idempotente."""
    _ensure_schema_sync()
    return True


# ==========================================
# ⚖️ LIBRO DE SANCIONES + ADMINSTATS
# ==========================================
_SANCTION_COLS = ("id", "group_id", "user_id", "user_name", "admin_id", "admin_name", "action", "source", "reason",
                  "duration_minutes", "until_ts", "global_ban_set", "points_awarded", "created_at", "reverted_at",
                  "reverted_by", "revert_reason", "cleared_at")
_STATS_COLS = ("group_id", "admin_id", "admin_name", "points", "actions", "bans", "kicks", "mutes",
               "false_positives", "last_action_at")


def _opt_int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _sanction_row(row: Sequence[Any]) -> SanctionRow:
    d = dict(zip(_SANCTION_COLS, row))
    return SanctionRow(
        id=int(d["id"]), group_id=int(d["group_id"]), user_id=int(d["user_id"]), user_name=d["user_name"],
        admin_id=_opt_int(d["admin_id"]), admin_name=d["admin_name"], action=str(d["action"]),
        source=str(d["source"]), reason=d["reason"], duration_minutes=_opt_int(d["duration_minutes"]),
        until_ts=_opt_int(d["until_ts"]), global_ban_set=bool(d["global_ban_set"]),
        points_awarded=int(d["points_awarded"] or 0), created_at=int(d["created_at"]),
        reverted_at=_opt_int(d["reverted_at"]), reverted_by=_opt_int(d["reverted_by"]),
        revert_reason=d["revert_reason"], cleared_at=_opt_int(d["cleared_at"]),
    )


def _stats_row(row: Sequence[Any]) -> AdminStatsRow:
    d = dict(zip(_STATS_COLS, row))
    return AdminStatsRow(
        group_id=int(d["group_id"]), admin_id=int(d["admin_id"]), admin_name=d["admin_name"],
        points=int(d["points"] or 0), actions=int(d["actions"] or 0), bans=int(d["bans"] or 0),
        kicks=int(d["kicks"] or 0), mutes=int(d["mutes"] or 0), false_positives=int(d["false_positives"] or 0),
        last_action_at=_opt_int(d["last_action_at"]),
    )


def _select_sanction(conn: sqlite3.Connection, sanction_id: int) -> Optional[SanctionRow]:
    row = conn.execute(f"SELECT {', '.join(_SANCTION_COLS)} FROM f3_sanctions WHERE id = ?",
                       (int(sanction_id),)).fetchone()
    return _sanction_row(row) if row else None


@db_async
def record_sanction(group_id: int, user_id: int, action: str, source: str, *,
                    admin_id: Optional[int] = None, admin_name: Optional[str] = None,
                    user_name: Optional[str] = None, reason: Optional[str] = None,
                    duration_minutes: Optional[int] = None, until_ts: Optional[int] = None,
                    global_ban_set: bool = False) -> int:
    """
    Anota una sanción ejecutada por el bot y devuelve su id (el del botón «Reportar falso positivo»).
    Las sanciones MANUALES suman puntos al administrador en f3_admin_stats (ADMIN_POINTS).
    """
    if action not in SANCTION_ACTIONS:
        raise Fase3ValidationError(f"action: acción no soportada ({action!r})")
    if source not in SANCTION_SOURCES:
        raise Fase3ValidationError(f"source: origen no soportado ({source!r})")
    _ensure_schema_sync()
    now = _now()
    awarded = ADMIN_POINTS.get(action, 0) if (source == "manual" and admin_id) else 0
    with _write_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO f3_sanctions (group_id, user_id, user_name, admin_id, admin_name, action, source, reason, "
            "duration_minutes, until_ts, global_ban_set, points_awarded, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(group_id), int(user_id), _clip(user_name, 64), _opt_int(admin_id), _clip(admin_name, 64), action,
             source, _clip(reason, REASON_MAX), _opt_int(duration_minutes), _opt_int(until_ts),
             1 if global_ban_set else 0, awarded, now),
        )
        sanction_id = int(cur.lastrowid or 0)
        if source == "manual" and admin_id:
            column = {"ban": "bans", "kick": "kicks", "mute": "mutes"}[action]
            conn.execute(
                f"INSERT INTO f3_admin_stats (group_id, admin_id, admin_name, points, actions, {column}, last_action_at) "
                f"VALUES (?, ?, ?, ?, 1, 1, ?) "
                f"ON CONFLICT(group_id, admin_id) DO UPDATE SET points = points + excluded.points, "
                f"actions = actions + 1, {column} = {column} + 1, last_action_at = excluded.last_action_at, "
                f"admin_name = COALESCE(excluded.admin_name, admin_name)",
                (int(group_id), int(admin_id), _clip(admin_name, 64), awarded, now),
            )
    return sanction_id


@db_async
def get_sanction(sanction_id: int) -> Optional[SanctionRow]:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        return _select_sanction(conn, sanction_id)


@db_async
def claim_false_positive(sanction_id: int, reverted_by: int, reason: Optional[str] = None
                         ) -> Tuple[str, Optional[SanctionRow], Optional[AdminStatsRow]]:
    """
    Reclamo ATÓMICO de la reversión (BEGIN IMMEDIATE): marca la sanción como revertida y, si fue manual, resta al
    administrador lo que sumó + FALSE_POSITIVE_PENALTY. Dos pulsaciones simultáneas nunca penalizan dos veces.
    → ("ok" | "missing" | "already", sanción, estadísticas del admin tras la penalización)
    Si la reversión en Telegram falla, el llamador DEBE invocar release_false_positive() para deshacerlo.
    """
    _ensure_schema_sync()
    now = _now()
    with _write_transaction() as conn:
        current = _select_sanction(conn, sanction_id)
        if current is None:
            return "missing", None, None
        if current["reverted_at"] is not None:
            return "already", current, None
        conn.execute("UPDATE f3_sanctions SET reverted_at = ?, reverted_by = ?, revert_reason = ? WHERE id = ?",
                     (now, int(reverted_by), _clip(reason, REASON_MAX) or "false_positive", int(sanction_id)))
        stats: Optional[AdminStatsRow] = None
        if current["source"] == "manual" and current["admin_id"]:
            penalty = int(current["points_awarded"]) + FALSE_POSITIVE_PENALTY
            conn.execute(
                "INSERT INTO f3_admin_stats (group_id, admin_id, admin_name, points, false_positives, last_action_at) "
                "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(group_id, admin_id) DO UPDATE SET "
                "points = points - ?, false_positives = false_positives + 1",
                (current["group_id"], current["admin_id"], current["admin_name"], -penalty, now, penalty),
            )
            row = conn.execute(f"SELECT {', '.join(_STATS_COLS)} FROM f3_admin_stats WHERE group_id = ? AND admin_id = ?",
                               (current["group_id"], current["admin_id"])).fetchone()
            stats = _stats_row(row) if row else None
        if current["source"] == "automod":
            # La evidencia que llevó a la escalada deja de contar para futuras escaladas.
            conn.execute("UPDATE f3_automod_infractions SET cleared_at = ? WHERE group_id = ? AND user_id = ? "
                         "AND cleared_at IS NULL AND created_at <= ?",
                         (now, current["group_id"], current["user_id"], current["created_at"] + 5))
        updated = _select_sanction(conn, sanction_id)
    return "ok", updated, stats


@db_async
def release_false_positive(sanction_id: int) -> bool:
    """Deshace claim_false_positive() cuando Telegram no permitió revertir (devuelve los puntos al administrador)."""
    _ensure_schema_sync()
    with _write_transaction() as conn:
        current = _select_sanction(conn, sanction_id)
        if current is None or current["reverted_at"] is None:
            return False
        conn.execute("UPDATE f3_sanctions SET reverted_at = NULL, reverted_by = NULL, revert_reason = NULL WHERE id = ?",
                     (int(sanction_id),))
        if current["source"] == "manual" and current["admin_id"]:
            penalty = int(current["points_awarded"]) + FALSE_POSITIVE_PENALTY
            conn.execute("UPDATE f3_admin_stats SET points = points + ?, false_positives = MAX(0, false_positives - 1) "
                         "WHERE group_id = ? AND admin_id = ?", (penalty, current["group_id"], current["admin_id"]))
    return True


@db_async
def get_admin_stats(group_id: int, admin_id: int) -> Optional[AdminStatsRow]:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {', '.join(_STATS_COLS)} FROM f3_admin_stats WHERE group_id = ? AND admin_id = ?",
                           (int(group_id), int(admin_id))).fetchone()
    return _stats_row(row) if row else None


@db_async
def admin_leaderboard(group_id: int, days: int = 30, limit: int = 20) -> List[LeaderboardEntry]:
    """Ranking por puntos acumulados; desempate por precisión y actividad dentro de la ventana."""
    _ensure_schema_sync()
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 100))
    since = _now() - days * 86400
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT {', '.join(_STATS_COLS)} FROM f3_admin_stats WHERE group_id = ?",
                            (int(group_id),)).fetchall()
        window = dict(conn.execute(
            "SELECT admin_id, COUNT(*) FROM f3_sanctions WHERE group_id = ? AND source = 'manual' "
            "AND admin_id IS NOT NULL AND created_at >= ? GROUP BY admin_id", (int(group_id), since)).fetchall())
    entries: List[LeaderboardEntry] = []
    for raw in rows:
        s = _stats_row(raw)
        accuracy = 1.0 if s["actions"] <= 0 else max(0.0, 1.0 - s["false_positives"] / float(s["actions"]))
        entries.append(LeaderboardEntry(
            rank=0, admin_id=s["admin_id"], admin_name=s["admin_name"], points=s["points"], actions=s["actions"],
            bans=s["bans"], kicks=s["kicks"], mutes=s["mutes"], false_positives=s["false_positives"],
            accuracy=round(accuracy, 4), window_actions=int(window.get(s["admin_id"], 0) or 0),
            last_action_at=s["last_action_at"],
        ))
    entries.sort(key=lambda e: (e["points"], e["accuracy"], e["window_actions"]), reverse=True)
    for index, entry in enumerate(entries[:limit], start=1):
        entry["rank"] = index
    return entries[:limit]


# ==========================================
# 🤖 AUTO-MOD: INFRACCIONES Y CONFIGURACIÓN
# ==========================================
_INFRACTION_COLS = ("id", "group_id", "user_id", "rule", "evidence", "excerpt", "message_id", "action_taken",
                    "created_at", "cleared_at")


def _infraction_row(row: Sequence[Any]) -> InfractionRow:
    d = dict(zip(_INFRACTION_COLS, row))
    return InfractionRow(
        id=int(d["id"]), group_id=int(d["group_id"]), user_id=int(d["user_id"]), rule=str(d["rule"]),
        evidence=d["evidence"], excerpt=d["excerpt"], message_id=_opt_int(d["message_id"]),
        action_taken=str(d["action_taken"]), created_at=int(d["created_at"]), cleared_at=_opt_int(d["cleared_at"]),
    )


@db_async
def record_infraction_and_count(group_id: int, user_id: int, rule: str, *, evidence: Optional[str],
                                excerpt: Optional[str], message_id: Optional[int], action_taken: str,
                                window_hours: int) -> Tuple[int, int]:
    """
    Registra la infracción y cuenta, EN LA MISMA transacción, las vigentes en la ventana: dos mensajes simultáneos
    del mismo usuario no pueden saltarse el umbral de escalada. → (id de la infracción, total en la ventana)
    """
    if rule not in AUTOMOD_RULES:
        raise Fase3ValidationError(f"rule: regla desconocida ({rule!r})")
    _ensure_schema_sync()
    now = _now()
    since = now - max(1, int(window_hours)) * 3600
    with _write_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO f3_automod_infractions (group_id, user_id, rule, evidence, excerpt, message_id, action_taken, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(group_id), int(user_id), rule, _clip(evidence, 200), _clip(excerpt, EXCERPT_MAX),
             _opt_int(message_id), str(action_taken)[:16], now),
        )
        infraction_id = int(cur.lastrowid or 0)
        count_row = conn.execute(
            "SELECT COUNT(*) FROM f3_automod_infractions WHERE group_id = ? AND user_id = ? AND cleared_at IS NULL "
            "AND created_at >= ?", (int(group_id), int(user_id), since)).fetchone()
        if infraction_id % 500 == 0:   # higiene periódica sin tarea aparte
            conn.execute("DELETE FROM f3_automod_infractions WHERE created_at < ?",
                         (now - INFRACTION_RETENTION_DAYS * 86400,))
    return infraction_id, int(count_row[0] if count_row else 0)


@db_async
def recent_infractions(group_id: int, user_id: int, window_hours: int = 24, limit: int = 10) -> List[InfractionRow]:
    """Evidencias vigentes (no limpiadas) dentro de la ventana, de la más reciente a la más antigua."""
    _ensure_schema_sync()
    since = _now() - max(1, int(window_hours)) * 3600
    with get_db_connection() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_INFRACTION_COLS)} FROM f3_automod_infractions WHERE group_id = ? AND user_id = ? "
            f"AND cleared_at IS NULL AND created_at >= ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(group_id), int(user_id), since, max(1, min(int(limit), 50)))).fetchall()
    return [_infraction_row(r) for r in rows]


@db_async
def clear_infractions(group_id: int, user_id: int, ids: Optional[Iterable[int]] = None) -> int:
    _ensure_schema_sync()
    now = _now()
    with _write_transaction() as conn:
        if ids is None:
            cur = conn.execute("UPDATE f3_automod_infractions SET cleared_at = ? WHERE group_id = ? AND user_id = ? "
                               "AND cleared_at IS NULL", (now, int(group_id), int(user_id)))
            return int(cur.rowcount or 0)
        id_list = [int(i) for i in ids][:500]
        if not id_list:
            return 0
        marks = ",".join("?" for _ in id_list)
        cur = conn.execute(f"UPDATE f3_automod_infractions SET cleared_at = ? WHERE group_id = ? AND user_id = ? "
                           f"AND cleared_at IS NULL AND id IN ({marks})", (now, int(group_id), int(user_id), *id_list))
        return int(cur.rowcount or 0)


_CONFIG_COLS = ("enabled", "rule_phishing", "rule_scam", "rule_invite_spam", "rule_mention_flood", "rule_char_flood",
                "rule_repeat_flood", "rule_obfuscation", "escalation_threshold", "escalation_window_hours",
                "infraction_action", "mute_minutes", "updated_at", "updated_by")


def _config_from(group_id: int, values: Dict[str, Any]) -> AutomodConfig:
    return AutomodConfig(
        group_id=int(group_id), enabled=bool(values["enabled"]),
        rules={rule: bool(values[f"rule_{rule}"]) for rule in AUTOMOD_RULES},
        escalation_threshold=int(values["escalation_threshold"]),
        escalation_window_hours=int(values["escalation_window_hours"]),
        infraction_action=str(values["infraction_action"]), mute_minutes=int(values["mute_minutes"]),
        updated_at=_opt_int(values.get("updated_at")), updated_by=_opt_int(values.get("updated_by")),
    )


def _load_config_values(conn: sqlite3.Connection, group_id: int) -> Dict[str, Any]:
    row = conn.execute(f"SELECT {', '.join(_CONFIG_COLS)} FROM f3_automod_config WHERE group_id = ?",
                       (int(group_id),)).fetchone()
    values: Dict[str, Any] = dict(AUTOMOD_DEFAULTS)
    values.update({"updated_at": None, "updated_by": None})
    if row:
        values.update(dict(zip(_CONFIG_COLS, row)))
    return values


@db_async
def get_automod_config(group_id: int) -> AutomodConfig:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        values = _load_config_values(conn, group_id)
    return _config_from(group_id, values)


def _bool_field(name: str, value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str) and value.strip().lower() in ("1", "0", "true", "false", "on", "off"):
        return 1 if value.strip().lower() in ("1", "true", "on") else 0
    raise Fase3ValidationError(f"{name}: se esperaba un booleano")


def validate_automod_patch(patch: Any) -> Dict[str, Any]:
    """Valida un parche de configuración Auto-Mod (todo o nada) → columnas a escribir."""
    if not isinstance(patch, dict) or not patch:
        raise Fase3ValidationError("automod: se esperaba un objeto con al menos un campo")
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key == "enabled":
            out["enabled"] = _bool_field(key, value)
        elif key == "rules":
            if not isinstance(value, dict):
                raise Fase3ValidationError("rules: se esperaba un objeto {regla: booleano}")
            for rule, flag in value.items():
                if rule not in AUTOMOD_RULES:
                    raise Fase3ValidationError(f"rules.{rule}: regla desconocida")
                out[f"rule_{rule}"] = _bool_field(f"rules.{rule}", flag)
        elif key in AUTOMOD_LIMITS:
            lo, hi = AUTOMOD_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise Fase3ValidationError(f"{key}: se esperaba un entero")
            try:
                number = int(str(value).strip())
            except ValueError:
                raise Fase3ValidationError(f"{key}: se esperaba un entero")
            if not lo <= number <= hi:
                raise Fase3ValidationError(f"{key}: fuera de rango ({lo}–{hi})")
            out[key] = number
        elif key == "infraction_action":
            if value not in AUTOMOD_INFRACTION_ACTIONS:
                raise Fase3ValidationError(f"infraction_action: usa {' / '.join(AUTOMOD_INFRACTION_ACTIONS)}")
            out[key] = str(value)
        else:
            raise Fase3ValidationError(f"{key}: campo desconocido")
    return out


@db_async
def save_automod_config(group_id: int, patch: Dict[str, Any], updated_by: Optional[int]) -> AutomodConfig:
    columns = validate_automod_patch(patch)
    _ensure_schema_sync()
    now = _now()
    with _write_transaction() as conn:
        values = _load_config_values(conn, group_id)
        values.update(columns)
        values["updated_at"], values["updated_by"] = now, _opt_int(updated_by)
        names = list(_CONFIG_COLS)
        conn.execute(
            f"INSERT INTO f3_automod_config (group_id, {', '.join(names)}) VALUES (?, {', '.join('?' for _ in names)}) "
            f"ON CONFLICT(group_id) DO UPDATE SET {', '.join(f'{n} = excluded.{n}' for n in names)}",
            (int(group_id), *[values[n] for n in names]),
        )
    return _config_from(group_id, values)


# ==========================================
# 💸 APELACIONES: ESTADO DEL BANEO Y LIMPIEZA DEL HISTORIAL
# ==========================================
@db_async
def ban_state(group_id: int, user_id: int, since_ts: int = 0) -> BanState:
    """
    Evidencia en base de datos de que el usuario sigue baneado (pre-checkout de apelaciones):
      • has_ban_record: hay una sanción de baneo f3 vigente o una apelación ofertada / pendiente de pago.
      • reverted_after_appeal: el último baneo f3 se revirtió (falso positivo) después de since_ts.
    """
    _ensure_schema_sync()
    has_record = False
    reverted_after = False
    global_banned = False
    with get_db_connection() as conn:
        last_ban = conn.execute(
            "SELECT reverted_at, cleared_at FROM f3_sanctions WHERE group_id = ? AND user_id = ? AND action = 'ban' "
            "ORDER BY created_at DESC, id DESC LIMIT 1", (int(group_id), int(user_id))).fetchone()
        if last_ban is not None:
            reverted_at, cleared_at = last_ban
            if reverted_at is None and cleared_at is None:
                has_record = True
            elif reverted_at is not None and int(reverted_at) >= int(since_ts or 0):
                reverted_after = True
        try:
            appeal = conn.execute(
                "SELECT COUNT(*) FROM ban_appeals WHERE group_id = ? AND user_id = ? AND status IN ('offered', 'dm_failed')",
                (int(group_id), int(user_id))).fetchone()
            has_record = has_record or bool(appeal and appeal[0])
        except sqlite3.OperationalError:
            pass
        try:
            flag = conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
            global_banned = bool(flag and flag[0])
        except sqlite3.OperationalError:
            pass
    return BanState(has_ban_record=has_record, reverted_after_appeal=reverted_after, global_banned=global_banned)


@db_async
def clear_negative_history(group_id: int, user_id: int) -> ClearReport:
    """
    Limpia el historial NEGATIVO del usuario en esta comunidad tras una apelación pagada (atómico):
    strikes de la escalera, infracciones Auto-Mod, sanciones vigentes del libro f3 (marcadas como limpiadas, la
    evidencia se conserva para auditoría) y penalización pendiente de la lista negra Anti-Raid.
    La marca global users.is_banned la gestiona ban_appeals.reinstate (solo si la puso esta comunidad).
    """
    _ensure_schema_sync()
    now = _now()
    report = ClearReport(strikes_reset=False, infractions_cleared=0, sanctions_cleared=0, raid_released=False)
    with _write_transaction() as conn:
        try:
            cur = conn.execute("DELETE FROM user_strikes WHERE group_id = ? AND user_id = ?", (int(group_id), int(user_id)))
            report["strikes_reset"] = bool(cur.rowcount)
        except sqlite3.OperationalError:
            pass
        cur = conn.execute("UPDATE f3_automod_infractions SET cleared_at = ? WHERE group_id = ? AND user_id = ? "
                           "AND cleared_at IS NULL", (now, int(group_id), int(user_id)))
        report["infractions_cleared"] = int(cur.rowcount or 0)
        cur = conn.execute("UPDATE f3_sanctions SET cleared_at = ? WHERE group_id = ? AND user_id = ? "
                           "AND cleared_at IS NULL AND reverted_at IS NULL", (now, int(group_id), int(user_id)))
        report["sanctions_cleared"] = int(cur.rowcount or 0)
        try:
            cur = conn.execute("UPDATE raid_blacklist SET status = 'released', resolved_at = ? "
                               "WHERE group_id = ? AND user_id = ? AND status = 'penalized'",
                               (now, int(group_id), int(user_id)))
            report["raid_released"] = bool(cur.rowcount)
        except sqlite3.OperationalError:
            pass
    return report


# ==========================================
# 🗂️ HISTORIAL DE UN USUARIO (Mini App)
# ==========================================
def _safe_rows(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def _item(ts: Any, kind: str, action: str, *, source: Optional[str] = None, reason: Optional[str] = None,
          actor_id: Any = None, actor_name: Optional[str] = None, detail: Optional[str] = None,
          sanction_id: Optional[int] = None, reverted: bool = False, cleared: bool = False) -> HistoryItem:
    return HistoryItem(ts=int(ts or 0), kind=kind, action=str(action), source=source, reason=reason,
                       actor_id=_opt_int(actor_id), actor_name=actor_name, detail=detail, sanction_id=sanction_id,
                       reverted=reverted, cleared=cleared)


def _sqlite_ts(value: Any) -> int:
    """'YYYY-MM-DD HH:MM:SS' (UTC de SQLite) o epoch → epoch."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return int(calendar.timegm(time.strptime(text[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return 0


@db_async
def user_history(group_id: int, user_id: int, limit: int = 100, window_hours: int = 24) -> UserHistory:
    """Perfil, resumen y línea de tiempo (más reciente primero) de un usuario en la comunidad."""
    _ensure_schema_sync()
    gid, uid = int(group_id), int(user_id)
    limit = max(1, min(int(limit), HISTORY_MAX_ITEMS))
    since_window = _now() - max(1, int(window_hours)) * 3600
    items: List[HistoryItem] = []
    with get_db_connection() as conn:
        activity = _safe_rows(conn, "SELECT full_name, username, message_count, last_active FROM chat_user_activity "
                                    "WHERE group_id = ? AND user_id = ?", (gid, uid))
        reputation = _safe_rows(conn, "SELECT xp, level, full_name, username FROM chat_user_reputation "
                                      "WHERE group_id = ? AND user_id = ?", (gid, uid))
        user_row = _safe_rows(conn, "SELECT full_name, username, is_banned FROM users WHERE user_id = ?", (uid,))
        strikes_row = _safe_rows(conn, "SELECT strikes, last_strike_at, last_reason FROM user_strikes "
                                       "WHERE group_id = ? AND user_id = ?", (gid, uid))

        for r in _safe_rows(conn, f"SELECT {', '.join(_SANCTION_COLS)} FROM f3_sanctions WHERE group_id = ? AND user_id = ? "
                                  f"ORDER BY created_at DESC LIMIT ?", (gid, uid, limit)):
            s = _sanction_row(r)
            detail = None
            if s["duration_minutes"]:
                detail = f"{s['duration_minutes']} min"
            elif s["action"] in ("ban", "mute"):
                detail = "permanent"
            items.append(_item(s["created_at"], "sanction", s["action"], source=s["source"], reason=s["reason"],
                               actor_id=s["admin_id"], actor_name=s["admin_name"], detail=detail, sanction_id=s["id"],
                               reverted=s["reverted_at"] is not None, cleared=s["cleared_at"] is not None))
            if s["reverted_at"] is not None:
                items.append(_item(s["reverted_at"], "sanction", "false_positive", source="owner",
                                   reason=s["revert_reason"], actor_id=s["reverted_by"], sanction_id=s["id"]))
        for r in _safe_rows(conn, f"SELECT {', '.join(_INFRACTION_COLS)} FROM f3_automod_infractions "
                                  f"WHERE group_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?", (gid, uid, limit)):
            inf = _infraction_row(r)
            items.append(_item(inf["created_at"], "infraction", inf["rule"], source="automod", reason=inf["evidence"],
                               detail=inf["excerpt"], cleared=inf["cleared_at"] is not None))
        for ts, admin_id, admin_name, action in _safe_rows(
                conn, "SELECT created_at, admin_id, admin_name, action FROM admin_mod_actions "
                      "WHERE group_id = ? AND target_id = ? ORDER BY created_at DESC LIMIT ?", (gid, uid, limit)):
            items.append(_item(ts, "admin_action", action, source="telegram", actor_id=admin_id, actor_name=admin_name))
        for ts, source, action in _safe_rows(
                conn, "SELECT created_at, source, action FROM security_events WHERE group_id = ? AND user_id = ? "
                      "ORDER BY created_at DESC LIMIT ?", (gid, uid, limit)):
            items.append(_item(ts, "security", action, source=source))
        appeals = _safe_rows(conn, "SELECT created_at, status, price_stars, reason_en, paid_at, reinstated_at FROM ban_appeals "
                                   "WHERE group_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?", (gid, uid, limit))
        for created_at, status, price, reason_en, paid_at, reinstated_at in appeals:
            items.append(_item(created_at, "appeal", "offered", source="ban_appeals", reason=reason_en,
                               detail=f"{int(price or 0)} Stars · {status}"))
            if paid_at:
                items.append(_item(paid_at, "appeal", "paid", source="ban_appeals", detail=f"{int(price or 0)} Stars"))
            if reinstated_at:
                items.append(_item(reinstated_at, "appeal", "reinstated", source="ban_appeals"))
        infractions_window_row = _safe_rows(conn, "SELECT COUNT(*) FROM f3_automod_infractions WHERE group_id = ? "
                                                  "AND user_id = ? AND cleared_at IS NULL AND created_at >= ?",
                                            (gid, uid, since_window))
        sanction_counts = _safe_rows(conn, "SELECT COUNT(*), SUM(CASE WHEN reverted_at IS NULL AND cleared_at IS NULL "
                                           "AND (until_ts IS NULL OR until_ts > ?) AND action != 'kick' THEN 1 ELSE 0 END), "
                                           "SUM(CASE WHEN reverted_at IS NOT NULL THEN 1 ELSE 0 END) FROM f3_sanctions "
                                           "WHERE group_id = ? AND user_id = ?", (_now(), gid, uid))

    if strikes_row and strikes_row[0][0]:
        items.append(_item(_sqlite_ts(strikes_row[0][1]), "strikes", "warn", source="warn_ladder",
                           reason=strikes_row[0][2], detail=f"{int(strikes_row[0][0])} strike(s)"))
    items.sort(key=lambda i: i["ts"], reverse=True)

    full_name = (activity[0][0] if activity else None) or (reputation[0][2] if reputation else None) \
        or (user_row[0][0] if user_row else None)
    username = (activity[0][1] if activity else None) or (reputation[0][3] if reputation else None) \
        or (user_row[0][1] if user_row else None)
    profile = UserProfile(
        user_id=uid, full_name=full_name, username=username,
        message_count=int(activity[0][2] or 0) if activity else 0,
        last_active=str(activity[0][3]) if activity and activity[0][3] is not None else None,
        xp=int(reputation[0][0] or 0) if reputation else 0, level=int(reputation[0][1] or 1) if reputation else 1,
        global_banned=bool(user_row[0][2]) if user_row else False,
    )
    counts = sanction_counts[0] if sanction_counts else (0, 0, 0)
    summary = UserSummary(
        strikes=int(strikes_row[0][0] or 0) if strikes_row else 0,
        infractions_window=int(infractions_window_row[0][0] or 0) if infractions_window_row else 0,
        sanctions_total=int(counts[0] or 0), active_sanctions=int(counts[1] or 0), reverted_sanctions=int(counts[2] or 0),
        appeals_total=len(appeals), appeals_paid=sum(1 for a in appeals if a[4]),
    )
    return UserHistory(group_id=gid, profile=profile, summary=summary, items=items[:limit])


@db_async
def search_users(group_id: int, query: str, limit: int = 20) -> List[UserSearchHit]:
    """Búsqueda en el padrón de actividad: id exacto, @alias o fragmento del nombre."""
    q = str(query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    cols = "user_id, full_name, username, message_count, last_active"
    with get_db_connection() as conn:
        if q.lstrip("-").isdigit():
            rows = _safe_rows(conn, f"SELECT {cols} FROM chat_user_activity WHERE group_id = ? AND user_id = ?",
                              (int(group_id), int(q)))
        else:
            needle = q.lstrip("@").replace("%", "").replace("_", r"\_")[:64]
            rows = _safe_rows(conn, f"SELECT {cols} FROM chat_user_activity WHERE group_id = ? AND "
                                    f"(username LIKE ? ESCAPE '\\' OR full_name LIKE ? ESCAPE '\\') "
                                    f"ORDER BY message_count DESC LIMIT ?",
                              (int(group_id), f"{needle}%", f"%{needle}%", limit))
    return [UserSearchHit(user_id=int(r[0]), full_name=r[1], username=r[2], message_count=int(r[3] or 0),
                          last_active=str(r[4]) if r[4] is not None else None) for r in rows]


# ==========================================
# 📦 GESTIÓN MASIVA (POST /mass-action)
# ==========================================
_JOB_COLS = ("id", "group_id", "requested_by", "action", "reason", "duration_minutes", "total", "done", "ok",
             "skipped", "failed", "status", "results", "error", "created_at", "updated_at", "finished_at")


def _job_row(row: Sequence[Any]) -> MassJobRow:
    d = dict(zip(_JOB_COLS, row))
    try:
        parsed = json.loads(d["results"] or "[]")
    except (TypeError, ValueError):
        parsed = []
    results: List[MassJobResult] = []
    for entry in parsed if isinstance(parsed, list) else []:
        if isinstance(entry, dict) and "user_id" in entry:
            results.append(MassJobResult(user_id=int(entry["user_id"]), status=str(entry.get("status", "")),
                                         code=entry.get("code"), detail=entry.get("detail")))
    return MassJobRow(
        id=str(d["id"]), group_id=int(d["group_id"]), requested_by=int(d["requested_by"]), action=str(d["action"]),
        reason=d["reason"], duration_minutes=_opt_int(d["duration_minutes"]), total=int(d["total"] or 0),
        done=int(d["done"] or 0), ok=int(d["ok"] or 0), skipped=int(d["skipped"] or 0), failed=int(d["failed"] or 0),
        status=str(d["status"]), results=results, error=d["error"], created_at=int(d["created_at"]),
        updated_at=int(d["updated_at"]), finished_at=_opt_int(d["finished_at"]),
    )


def normalize_user_ids(raw: Any) -> List[int]:
    """Lista de ids positivos, sin duplicados y en el orden recibido (máx. MASS_ACTION_MAX_USERS)."""
    if not isinstance(raw, list) or not raw:
        raise Fase3ValidationError("user_ids: se esperaba una lista no vacía de ids numéricos")
    seen: set[int] = set()
    out: List[int] = []
    for value in raw:
        if isinstance(value, bool):
            raise Fase3ValidationError("user_ids: valor no numérico")
        text = str(value).strip()
        if not text.isdigit() or len(text) > 20:
            raise Fase3ValidationError(f"user_ids: id no válido ({text[:24]!r})")
        uid = int(text)
        if uid <= 0:
            raise Fase3ValidationError("user_ids: los ids deben ser positivos")
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    if len(out) > MASS_ACTION_MAX_USERS:
        raise Fase3ValidationError(f"user_ids: máximo {MASS_ACTION_MAX_USERS} usuarios por operación")
    return out


@db_async
def create_mass_job(group_id: int, requested_by: int, action: str, user_ids: List[int],
                    reason: Optional[str] = None, duration_minutes: Optional[int] = None) -> MassJobRow:
    if action not in MASS_ACTIONS:
        raise Fase3ValidationError(f"action: usa {' / '.join(MASS_ACTIONS)}")
    ids = normalize_user_ids(list(user_ids))
    _ensure_schema_sync()
    now = _now()
    job_id = secrets.token_urlsafe(9)
    with _write_transaction() as conn:
        running = conn.execute("SELECT COUNT(*) FROM f3_mass_jobs WHERE group_id = ? AND status IN ('queued', 'running')",
                               (int(group_id),)).fetchone()
        if running and int(running[0]) > 0:
            raise Fase3ValidationError("mass_action: ya hay una operación masiva en curso en esta comunidad")
        conn.execute(
            "INSERT INTO f3_mass_jobs (id, group_id, requested_by, action, reason, duration_minutes, user_ids, total, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, int(group_id), int(requested_by), action, _clip(reason, REASON_MAX), _opt_int(duration_minutes),
             json.dumps(ids), len(ids), now, now),
        )
        conn.execute("DELETE FROM f3_mass_jobs WHERE created_at < ? AND status NOT IN ('queued', 'running')",
                     (now - MASS_JOB_RETENTION_DAYS * 86400,))
        row = conn.execute(f"SELECT {', '.join(_JOB_COLS)} FROM f3_mass_jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_row(row)


@db_async
def update_mass_job(job_id: str, *, status: Optional[str] = None, done: Optional[int] = None,
                    ok: Optional[int] = None, skipped: Optional[int] = None, failed: Optional[int] = None,
                    results: Optional[List[MassJobResult]] = None, error: Optional[str] = None) -> None:
    if status is not None and status not in MASS_JOB_STATUSES:
        raise Fase3ValidationError(f"status: estado no válido ({status!r})")
    _ensure_schema_sync()
    sets: List[str] = ["updated_at = ?"]
    params: List[Any] = [_now()]
    for name, value in (("status", status), ("done", done), ("ok", ok), ("skipped", skipped), ("failed", failed),
                        ("error", _clip(error, 300))):
        if value is not None:
            sets.append(f"{name} = ?")
            params.append(value)
    if results is not None:
        sets.append("results = ?")
        params.append(json.dumps(list(results)[-MASS_JOB_RESULTS_MAX:]))
    if status in ("done", "failed", "interrupted"):
        sets.append("finished_at = ?")
        params.append(_now())
    params.append(str(job_id))
    with _write_transaction() as conn:
        conn.execute(f"UPDATE f3_mass_jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))


@db_async
def get_mass_job(group_id: int, job_id: str) -> Optional[MassJobRow]:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {', '.join(_JOB_COLS)} FROM f3_mass_jobs WHERE id = ? AND group_id = ?",
                           (str(job_id), int(group_id))).fetchone()
    return _job_row(row) if row else None


@db_async
def get_mass_job_user_ids(job_id: str) -> List[int]:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_ids FROM f3_mass_jobs WHERE id = ?", (str(job_id),)).fetchone()
    try:
        return [int(x) for x in json.loads(row[0])] if row else []
    except (TypeError, ValueError):
        return []


@db_async
def list_mass_jobs(group_id: int, limit: int = 10) -> List[MassJobRow]:
    _ensure_schema_sync()
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT {', '.join(_JOB_COLS)} FROM f3_mass_jobs WHERE group_id = ? "
                            f"ORDER BY created_at DESC LIMIT ?", (int(group_id), max(1, min(int(limit), 50)))).fetchall()
    return [_job_row(r) for r in rows]


@db_async
def mark_interrupted_jobs() -> int:
    """Al arrancar: los trabajos que quedaron en curso (reinicio / redeploy) pasan a 'interrupted'."""
    _ensure_schema_sync()
    now = _now()
    with _write_transaction() as conn:
        cur = conn.execute("UPDATE f3_mass_jobs SET status = 'interrupted', error = 'process restarted', "
                           "updated_at = ?, finished_at = ? WHERE status IN ('queued', 'running')", (now, now))
        return int(cur.rowcount or 0)
