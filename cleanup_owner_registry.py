"""
cleanup_owner_registry.py — The Bunker OS · Limpieza del registro de dueños (Regla de Oro v12)

Retira del registro de propiedad a los usuarios que NO son el creador real de la comunidad o canal
(administradores comunes que quedaron guardados como "dueños" en versiones anteriores, cuando se registraba
a quien añadía el bot).

Qué revisa (con verificación EN VIVO contra Telegram):
  • user_groups              → filas de no-creadores: se eliminan. El creador real se añade si faltaba.
  • channel_owner_registry   → filas de no-creadores: se eliminan.
  • bot_clones               → clones activos de no-creadores: solo se informan; con --revoke-clones se revocan.

Garantías:
  • Por defecto es SIMULACIÓN: no escribe nada, solo muestra y guarda el informe.
  • Con --apply hace antes una copia completa de la base (API de backup de SQLite) y además guarda cada fila
    retirada en la tabla owner_cleanup_backup (JSON), para poder restaurarla.
  • Nunca toca a los Arquitectos (SUPER_ADMIN_IDS / ADMIN_IDS).
  • Si Telegram no puede confirmar el rango (error de red, bot expulsado del chat, creador oculto), la fila
    se CONSERVA y se marca como "sin verificar". Solo se retira a quien Telegram confirma que no es el creador.
  • Si un chat se quedaría sin ninguna fila en user_groups y no se conoce a su creador, se reasigna a la
    custodia del Arquitecto en lugar de borrarlo (para que la auditoría de sincronización lo siga viendo).

Uso (desde la raíz del proyecto, con BOT_TOKEN en el entorno o en .env):
    python cleanup_owner_registry.py                      # simulación + informe CSV
    python cleanup_owner_registry.py --apply              # aplica la limpieza (con copia de seguridad)
    python cleanup_owner_registry.py --apply --revoke-clones
    python cleanup_owner_registry.py --db ruta/al/archivo.db --chat -1001234567890

Restaurar: detén el bot y reemplaza el archivo de base por la copia `*.pre-owner-cleanup-<fecha>.db`, o
reinserta filas concretas desde la tabla owner_cleanup_backup.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s", stream=sys.stdout)
log = logging.getLogger("owner_cleanup")

ARCHITECT_FALLBACK_ID = 8269470905
DEFAULT_SUPER_ADMINS = {8269470905, 1738976493}

# Resultado de la consulta de rango
CREATOR = "creator"
UNKNOWN = ""          # Telegram no pudo confirmarlo → la fila se conserva
NOT_MEMBER = "left"   # Telegram confirma que no está en el chat

_NOT_MEMBER_HINTS = ("user not found", "participant_id_invalid", "user_id_invalid", "member not found")


# ==========================================
# 🗄️ BASE DE DATOS
# ==========================================
def resolve_db_path(cli_path: Optional[str]) -> str:
    if cli_path:
        return cli_path
    for env_name in ("BUNKER_DB_PATH", "DB_PATH", "DATABASE_PATH"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    try:
        import database.database as _db  # type: ignore[import-not-found]
        for name in ("DB_PATH", "DB_FILE", "DATABASE_PATH", "DB_NAME"):
            value = getattr(_db, name, None)
            if isinstance(value, str) and value:
                return value
    except Exception as ex:
        log.debug(f"No se pudo importar database.database: {ex}")
    return os.getenv("MEMBER_REGISTRY_DB", "database/bot_data.db")


def connect(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise SystemExit(f"❌ No existe la base de datos: {path}  (usa --db para indicar la ruta)")
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, name: str) -> List[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")').fetchall()]


def backup_database(conn: sqlite3.Connection, db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base, ext = os.path.splitext(db_path)
    target = f"{base}.pre-owner-cleanup-{stamp}{ext or '.db'}"
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
    return target


def ensure_backup_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS owner_cleanup_backup ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_table TEXT NOT NULL, "
        "row_json TEXT NOT NULL, "
        "reason TEXT, "
        "removed_at INTEGER NOT NULL)"
    )


def stash_row(conn: sqlite3.Connection, table: str, row: sqlite3.Row, reason: str) -> None:
    conn.execute(
        "INSERT INTO owner_cleanup_backup (source_table, row_json, reason, removed_at) VALUES (?, ?, ?, ?)",
        (table, json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False, default=str), reason, int(time.time())),
    )


def load_super_admins() -> Set[int]:
    ids = set(DEFAULT_SUPER_ADMINS)
    for raw in os.getenv("ADMIN_IDS", "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            ids.add(int(raw))
    return ids


# ==========================================
# 📡 TELEGRAM
# ==========================================
class TelegramProbe:
    """Consulta rangos y creadores con concurrencia acotada, reintentos ante flood-wait y caché."""

    def __init__(self, bot, concurrency: int = 5):
        self.bot = bot
        self.sem = asyncio.Semaphore(concurrency)
        self._status: Dict[Tuple[int, int], str] = {}
        self._creator: Dict[int, Optional[int]] = {}

    async def _call(self, func, *args, **kwargs):
        from aiogram.exceptions import TelegramRetryAfter
        for attempt in range(4):
            try:
                async with self.sem:
                    return await asyncio.wait_for(func(*args, **kwargs), timeout=10)
            except TelegramRetryAfter as ex:
                wait = min(float(ex.retry_after), 60.0) + 1.0
                log.warning(f"⏳ Flood-wait de Telegram: esperando {wait:.0f}s…")
                await asyncio.sleep(wait)
        raise RuntimeError("flood-wait persistente")

    async def member_status(self, chat_id: int, user_id: int) -> str:
        key = (chat_id, user_id)
        if key in self._status:
            return self._status[key]
        try:
            member = await self._call(self.bot.get_chat_member, chat_id=chat_id, user_id=user_id)
            status = str(getattr(member.status, "value", member.status) or "")
        except Exception as ex:
            text = str(ex).lower()
            status = NOT_MEMBER if any(h in text for h in _NOT_MEMBER_HINTS) else UNKNOWN
            if status == UNKNOWN:
                log.debug(f"Rango no verificable ({chat_id}/{user_id}): {ex}")
        self._status[key] = status
        return status

    async def creator_of(self, chat_id: int) -> Optional[int]:
        if chat_id in self._creator:
            return self._creator[chat_id]
        creator = None
        try:
            admins = await self._call(self.bot.get_chat_administrators, chat_id=chat_id)
            for adm in admins:
                if str(getattr(adm.status, "value", adm.status) or "") == CREATOR and adm.user and not adm.user.is_bot:
                    creator = int(adm.user.id)
                    break
        except Exception as ex:
            log.debug(f"Creador no verificable en {chat_id}: {ex}")
        self._creator[chat_id] = creator
        return creator


# ==========================================
# 🧹 LIMPIEZA
# ==========================================
class Report:
    FIELDS = ["table", "chat_id", "user_id", "status", "action", "detail"]

    def __init__(self):
        self.rows: List[dict] = []

    def add(self, table: str, chat_id: int, user_id: Optional[int], status: str, action: str, detail: str = "") -> None:
        self.rows.append({"table": table, "chat_id": chat_id, "user_id": user_id if user_id is not None else "",
                          "status": status or "sin verificar", "action": action, "detail": detail})

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r["action"] == action)

    def write_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)


async def clean_user_groups(conn, probe: TelegramProbe, supers: Set[int], report: Report, apply: bool,
                            only_chat: Optional[int]) -> None:
    table = "user_groups"
    if not table_exists(conn, table):
        log.info("ℹ️ Tabla user_groups no encontrada; se omite.")
        return
    cols = table_columns(conn, table)
    query = f'SELECT rowid AS _rid, * FROM "{table}"'
    params: tuple = ()
    if only_chat is not None:
        query += " WHERE group_id = ?"
        params = (only_chat,)
    rows = conn.execute(query, params).fetchall()

    by_chat: Dict[int, List[sqlite3.Row]] = {}
    for row in rows:
        if row["group_id"] is None or row["user_id"] is None:
            continue
        by_chat.setdefault(int(row["group_id"]), []).append(row)
    log.info(f"🔎 user_groups: {len(rows)} fila(s) en {len(by_chat)} chat(s).")

    async def process_chat(chat_id: int, chat_rows: List[sqlite3.Row]) -> List[tuple]:
        """Devuelve operaciones a aplicar: ('delete', row, reason) | ('insert_owner', template_row, user_id)."""
        ops: List[tuple] = []
        creator = await probe.creator_of(chat_id)
        statuses = await asyncio.gather(*(probe.member_status(chat_id, int(r["user_id"])) for r in chat_rows))
        keep_count = 0
        for row, status in zip(chat_rows, statuses):
            uid = int(row["user_id"])
            if uid in supers:
                report.add(table, chat_id, uid, status, "conservar", "Arquitecto")
                keep_count += 1
            elif status == CREATOR or (creator is not None and uid == creator):
                report.add(table, chat_id, uid, CREATOR, "conservar", "creador verificado")
                keep_count += 1
            elif status == UNKNOWN:
                report.add(table, chat_id, uid, status, "conservar", "Telegram no pudo confirmar el rango")
                keep_count += 1
            else:
                report.add(table, chat_id, uid, status, "retirar", "no es el creador")
                ops.append(("delete", row, f"rango confirmado '{status}' en {chat_id}"))

        registered = {int(r["user_id"]) for r in chat_rows}
        if creator is not None and creator not in registered:
            report.add(table, chat_id, creator, CREATOR, "añadir", "creador real ausente del registro")
            ops.append(("insert_owner", chat_rows[0], creator))
            keep_count += 1
        if keep_count == 0 and ops:
            report.add(table, chat_id, ARCHITECT_FALLBACK_ID, "", "añadir",
                       "creador desconocido: custodia del Arquitecto para no perder el chat")
            ops.append(("insert_owner", chat_rows[0], ARCHITECT_FALLBACK_ID))
        if creator is None and not ops:
            log.debug(f"Chat {chat_id}: sin cambios (creador no verificable).")
        return ops

    results = await asyncio.gather(*(process_chat(c, r) for c, r in by_chat.items()))
    if not apply:
        return
    with conn:
        for ops in results:
            for op in ops:
                if op[0] == "delete":
                    _, row, reason = op
                    stash_row(conn, table, row, reason)
                    conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (row["_rid"],))
                else:
                    _, template, new_uid = op
                    exists = conn.execute(f'SELECT 1 FROM "{table}" WHERE user_id = ? AND group_id = ? LIMIT 1',
                                          (new_uid, template["group_id"])).fetchone()
                    if exists:
                        continue
                    data = {c: template[c] for c in cols if c in template.keys()}
                    data["user_id"] = new_uid
                    for auto_col in ("id",):
                        data.pop(auto_col, None)
                    names = ", ".join(f'"{c}"' for c in data)
                    marks = ", ".join("?" for _ in data)
                    conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', tuple(data.values()))


async def clean_channel_registry(conn, probe: TelegramProbe, supers: Set[int], report: Report, apply: bool,
                                 only_chat: Optional[int]) -> None:
    table = "channel_owner_registry"
    if not table_exists(conn, table):
        log.info("ℹ️ Tabla channel_owner_registry no encontrada; se omite.")
        return
    query = f'SELECT rowid AS _rid, * FROM "{table}"'
    params: tuple = ()
    if only_chat is not None:
        query += " WHERE channel_id = ?"
        params = (only_chat,)
    rows = conn.execute(query, params).fetchall()
    log.info(f"🔎 channel_owner_registry: {len(rows)} fila(s).")

    async def check(row) -> Optional[tuple]:
        chat_id, uid = int(row["channel_id"]), int(row["user_id"])
        if uid in supers:
            report.add(table, chat_id, uid, "", "conservar", "Arquitecto")
            return None
        status = await probe.member_status(chat_id, uid)
        if status == CREATOR:
            report.add(table, chat_id, uid, status, "conservar", "creador verificado")
            return None
        if status == UNKNOWN:
            report.add(table, chat_id, uid, status, "conservar", "Telegram no pudo confirmar el rango")
            return None
        report.add(table, chat_id, uid, status, "retirar", "no es el creador")
        return (row, f"rango confirmado '{status}' en {chat_id}")

    to_delete = [r for r in await asyncio.gather(*(check(row) for row in rows)) if r]
    if apply and to_delete:
        with conn:
            for row, reason in to_delete:
                stash_row(conn, table, row, reason)
                conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (row["_rid"],))


async def review_clones(conn, probe: TelegramProbe, supers: Set[int], report: Report, apply: bool,
                        revoke: bool, only_chat: Optional[int]) -> None:
    table = "bot_clones"
    if not table_exists(conn, table):
        log.info("ℹ️ Tabla bot_clones no encontrada; se omite.")
        return
    query = f"SELECT rowid AS _rid, * FROM \"{table}\" WHERE status != 'revoked'"
    params: tuple = ()
    if only_chat is not None:
        query += " AND group_id = ?"
        params = (only_chat,)
    rows = conn.execute(query, params).fetchall()
    log.info(f"🔎 bot_clones activos: {len(rows)}.")

    async def check(row) -> Optional[tuple]:
        chat_id, uid = int(row["group_id"]), int(row["user_id"])
        if uid in supers:
            report.add(table, chat_id, uid, "", "conservar", "Arquitecto")
            return None
        status = await probe.member_status(chat_id, uid)
        if status in (CREATOR, UNKNOWN):
            report.add(table, chat_id, uid, status, "conservar",
                       "creador verificado" if status == CREATOR else "Telegram no pudo confirmar el rango")
            return None
        report.add(table, chat_id, uid, status, "revocar" if revoke else "revisar",
                   "clon de un no-creador" + ("" if revoke else " (usa --revoke-clones para revocarlo)"))
        return (row, f"clon de no-creador ('{status}') en {chat_id}")

    flagged = [r for r in await asyncio.gather(*(check(row) for row in rows)) if r]
    if apply and revoke and flagged:
        with conn:
            for row, reason in flagged:
                stash_row(conn, table, row, reason)
                conn.execute(f"UPDATE \"{table}\" SET status = 'revoked', bot_token = '' WHERE rowid = ?", (row["_rid"],))


# ==========================================
# ▶️ ENTRADA
# ==========================================
async def run(args) -> int:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        log.error("❌ BOT_TOKEN no está definido (entorno o .env).")
        return 2
    try:
        from aiogram import Bot
    except ImportError:
        log.error("❌ aiogram no está instalado en este entorno.")
        return 2

    db_path = resolve_db_path(args.db)
    log.info(f"🗄️ Base de datos: {db_path}")
    conn = connect(db_path)
    supers = load_super_admins()
    report = Report()

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        log.info(f"🤖 Verificando como @{me.username} · modo {'APLICAR' if args.apply else 'SIMULACIÓN'}")
        if args.apply:
            backup_path = backup_database(conn, db_path)
            log.info(f"💾 Copia de seguridad completa: {backup_path}")
            with conn:
                ensure_backup_table(conn)

        probe = TelegramProbe(bot, concurrency=args.concurrency)
        await clean_user_groups(conn, probe, supers, report, args.apply, args.chat)
        await clean_channel_registry(conn, probe, supers, report, args.apply, args.chat)
        await review_clones(conn, probe, supers, report, args.apply, args.revoke_clones, args.chat)
    finally:
        await bot.session.close()
        conn.close()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = args.report or f"owner_cleanup_report_{stamp}.csv"
    report.write_csv(csv_path)

    retired = report.count("retirar")
    added = report.count("añadir")
    unverified = sum(1 for r in report.rows if r["status"] == "sin verificar")
    clones = report.count("revocar") + report.count("revisar")
    log.info("──────────────── RESUMEN ────────────────")
    log.info(f"Filas revisadas:        {len(report.rows)}")
    log.info(f"Dueños retirados:       {retired}{'' if args.apply else ' (simulación)'}")
    log.info(f"Creadores añadidos:     {added}{'' if args.apply else ' (simulación)'}")
    log.info(f"Clones de no-creadores: {clones}{' revocados' if (args.apply and args.revoke_clones) else ' (solo informe)'}")
    log.info(f"Sin verificar (se conservan): {unverified}")
    log.info(f"Informe CSV: {csv_path}")
    if not args.apply:
        log.info("ℹ️ Nada se modificó. Revisa el informe y ejecuta de nuevo con --apply.")
    elif args.revoke_clones and clones:
        log.info("ℹ️ Reinicia el bot para detener el polling de los clones revocados.")
    return 0


def parse_args(argv: Optional[Iterable[str]] = None):
    parser = argparse.ArgumentParser(description="Limpia del registro de dueños a quienes no son el creador real.")
    parser.add_argument("--apply", action="store_true", help="Aplica los cambios (por defecto solo simula).")
    parser.add_argument("--revoke-clones", action="store_true", help="Revoca también los bots clon de no-creadores.")
    parser.add_argument("--db", help="Ruta del archivo SQLite (por defecto se detecta).")
    parser.add_argument("--chat", type=int, help="Limita la revisión a un solo chat (ID negativo).")
    parser.add_argument("--concurrency", type=int, default=5, help="Consultas simultáneas a Telegram (1-10).")
    parser.add_argument("--report", help="Ruta del informe CSV.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.concurrency = max(1, min(10, args.concurrency))
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
