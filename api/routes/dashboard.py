"""
dashboard.py — The Bunker OS · v40 · Fase 3 · API del Dashboard de Moderación (Mini App)

Montado por main.py en api_router (prefijo /api) con las MISMAS dependencias de seguridad que el resto de la Mini App:
`require_user(init_data, authorization)` (initData firmado o sesión web) y `assert_owner(user_id, chat_id)` (dueño
legítimo; 403 si no). Todas las rutas son exclusivas del Dueño.

    GET  /api/chat/{chat_id}/users/search?q=…                      Buscar en el padrón (id, @alias, nombre)
    GET  /api/chat/{chat_id}/users/{user_id}/history               Perfil + resumen + línea de tiempo
    GET  /api/chat/{chat_id}/admin-leaderboard?days=30             Leaderboard de administradores (AdminStats)
    POST /api/chat/{chat_id}/mass-action                           Ban / unban masivo en segundo plano → 202 + job
    GET  /api/chat/{chat_id}/mass-action/{job_id}                  Progreso del trabajo
    GET  /api/chat/{chat_id}/mass-actions                          Últimos trabajos
    GET  /api/chat/{chat_id}/automod      ·  POST (… {automod: {…}}) Configuración Auto-Mod
    POST /api/chat/{chat_id}/sanctions/{sanction_id}/false-positive Revertir una sanción (mismo servicio que el botón)

La Gestión Masiva corre en `logger_hub._AUDIT_BG_TASKS` (spawn_audit_task): la respuesta HTTP sale de inmediato y el
event loop sigue libre; el progreso se persiste en f3_mass_jobs (sobrevive a la desconexión de la Mini App y, tras un
reinicio, el trabajo queda como 'interrupted').
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from database import crud_fase3 as crud
from database.models_fase3 import (ADMIN_POINTS, AUTOMOD_INFRACTION_ACTIONS, AUTOMOD_LIMITS, AUTOMOD_RULES,
                                   FALSE_POSITIVE_PENALTY, MASS_ACTION_MAX_USERS, MassJobResult, MassJobRow)

logger = logging.getLogger("bunker.dashboard_api")

MASS_ACTION_PACE_S = 0.12            # ~8 acciones/s: muy por debajo de los límites de la Bot API
MASS_PROGRESS_EVERY = 10             # persistir el progreso cada N usuarios (y al final)
FATAL_CODES = frozenset({"chat_not_found", "bot_not_member", "not_enough_rights"})
MAX_DURATION_MINUTES = 525600        # 365 días

RequireUser = Callable[[Optional[str], Optional[str]], int]
AssertOwner = Callable[[int, int], Awaitable[None]]
BotGetter = Callable[[], Any]
ChatBotResolver = Callable[[int], Awaitable[Any]]


class DashboardError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


# ==========================================
# ✅ VALIDACIÓN DE ENTRADA
# ==========================================
def parse_chat_id(raw: Any) -> int:
    text = str(raw).strip()
    if not text.startswith("-") or not text[1:].isdigit() or len(text) > 21:
        raise DashboardError(400, "chat_id inválido: debe ser el id numérico (negativo) de un grupo.")
    return int(text)


def parse_positive_id(raw: Any, field: str = "user_id") -> int:
    text = str(raw).strip()
    if not text.isdigit() or len(text) > 20 or int(text) <= 0:
        raise DashboardError(400, f"{field} inválido: debe ser un id numérico positivo.")
    return int(text)


def _bounded_int(raw: Any, default: int, lo: int, hi: int, field: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise DashboardError(422, f"{field}: se esperaba un entero")
    if not lo <= value <= hi:
        raise DashboardError(422, f"{field}: fuera de rango ({lo}–{hi})")
    return value


def validate_mass_payload(payload: Any) -> Tuple[str, List[int], Optional[str], Optional[int]]:
    """{action: ban|unban, user_ids: [...], reason?: str, duration_minutes?: int} → valores normalizados."""
    if not isinstance(payload, dict):
        raise DashboardError(422, "body: se esperaba un objeto JSON")
    action = str(payload.get("action") or "").strip().lower()
    if action not in ("ban", "unban"):
        raise DashboardError(422, "action: usa 'ban' o 'unban'")
    try:
        user_ids = crud.normalize_user_ids(payload.get("user_ids"))
    except crud.Fase3ValidationError as err:
        raise DashboardError(422, str(err))
    reason_raw = payload.get("reason")
    reason = str(reason_raw).strip()[:300] if isinstance(reason_raw, str) and reason_raw.strip() else None
    duration: Optional[int] = None
    if action == "ban" and payload.get("duration_minutes") not in (None, "", 0, "0"):
        duration = _bounded_int(payload.get("duration_minutes"), 0, 1, MAX_DURATION_MINUTES, "duration_minutes")
    return action, user_ids, reason, duration


# ==========================================
# 📦 EJECUTOR DE LA GESTIÓN MASIVA
# ==========================================
async def _pick_bot(chat_id: int, get_bot: BotGetter, resolve_chat_bot: Optional[ChatBotResolver]) -> Any:
    if resolve_chat_bot is not None:
        with contextlib.suppress(Exception):
            clone = await resolve_chat_bot(int(chat_id))
            if clone is not None:
                return clone
    return get_bot()


async def run_mass_job(job: MassJobRow, user_ids: List[int], get_bot: BotGetter,
                       resolve_chat_bot: Optional[ChatBotResolver] = None) -> None:
    """Ejecuta el trabajo usuario a usuario con ritmo, protección de administradores y aborto ante errores fatales."""
    from handlers import telegram_ops
    from handlers.logger_hub import dispatch_audit_log, mark_audit_handled

    chat_id, action, job_id = job["group_id"], job["action"], job["id"]
    results: List[MassJobResult] = []
    ok = skipped = failed = 0
    fatal: Optional[str] = None
    try:
        bot = await _pick_bot(chat_id, get_bot, resolve_chat_bot)
        if bot is None:
            await crud.update_mass_job(job_id, status="failed", error="master bot not available")
            return
        await crud.update_mass_job(job_id, status="running")
        until_ts = int(time.time()) + int(job["duration_minutes"]) * 60 if job["duration_minutes"] else None
        for index, uid in enumerate(user_ids, start=1):
            entry = MassJobResult(user_id=uid, status="ok", code=None, detail=None)
            if fatal is not None:
                entry["status"] = "skipped"
                entry["code"] = "aborted"
                entry["detail"] = fatal
                skipped += 1
            elif action == "ban":
                protected = await telegram_ops.is_protected_target(chat_id, uid, preferred=bot)
                if protected:
                    entry["status"] = "skipped"
                    entry["code"] = protected
                    skipped += 1
                else:
                    mark_audit_handled(f"{chat_id}:removal:{uid}", 60.0)
                    res = await telegram_ops.ban_member(chat_id, uid, preferred=bot, until_ts=until_ts)
                    if res.ok:
                        ok += 1
                        with contextlib.suppress(Exception):
                            await crud.record_sanction(chat_id, uid, "ban", "mass", admin_id=job["requested_by"],
                                                       reason=job["reason"] or "Mass action (Mini App)",
                                                       duration_minutes=job["duration_minutes"], until_ts=until_ts)
                        with contextlib.suppress(Exception):
                            from handlers.groups import registry_forget
                            await registry_forget(chat_id, uid)
                    elif res.code == "user_is_admin":   # ascendido entre la comprobación y el baneo
                        entry["status"] = "skipped"
                        entry["code"] = res.code
                        entry["detail"] = res.detail
                        skipped += 1
                    else:
                        entry["status"] = "failed"
                        entry["code"] = res.code
                        entry["detail"] = res.detail
                        failed += 1
                        if res.code in FATAL_CODES:
                            fatal = f"{res.code}: {res.detail or ''}"[:200]
            else:
                mark_audit_handled(f"{chat_id}:unban:{uid}", 60.0)
                res = await telegram_ops.unban_member(chat_id, uid, preferred=bot)
                if res.ok:
                    ok += 1
                    with contextlib.suppress(Exception):
                        import database.database as db
                        await db.lift_global_ban_if_origin(uid, chat_id)
                else:
                    entry["status"] = "failed"
                    entry["code"] = res.code
                    entry["detail"] = res.detail
                    failed += 1
                    if res.code in FATAL_CODES:
                        fatal = f"{res.code}: {res.detail or ''}"[:200]
            results.append(entry)
            if index % MASS_PROGRESS_EVERY == 0:
                await crud.update_mass_job(job_id, done=index, ok=ok, skipped=skipped, failed=failed, results=results)
            await asyncio.sleep(MASS_ACTION_PACE_S)
        final_status = "failed" if fatal is not None and ok == 0 else "done"
        await crud.update_mass_job(job_id, status=final_status, done=len(user_ids), ok=ok, skipped=skipped,
                                   failed=failed, results=results, error=fatal)
        failures = [str(r["user_id"]) for r in results if r["status"] == "failed"][:15]
        await dispatch_audit_log(
            bot, chat_id, "mass_action", actor_id=job["requested_by"], action=action, reason=job["reason"],
            duration_minutes=job["duration_minutes"] if action == "ban" else None,
            count=len(user_ids), detail=f"job {job_id} · Mini App",
            extra={("Applied", "Aplicadas"): ok, ("Skipped (admins / protected)", "Omitidos (admins / protegidos)"): skipped,
                   ("Failed", "Fallidas"): failed, ("Failed IDs", "IDs fallidos"): ", ".join(failures) or None,
                   ("Aborted", "Abortado"): fatal},
        )
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await crud.update_mass_job(job_id, status="interrupted", done=len(results), ok=ok, skipped=skipped,
                                       failed=failed, results=results, error="cancelled")
        raise
    except Exception as ex:
        logger.error(f"❌ [Gestión Masiva] Trabajo {job_id} en {chat_id} falló: {ex}", exc_info=True)
        with contextlib.suppress(Exception):
            await crud.update_mass_job(job_id, status="failed", done=len(results), ok=ok, skipped=skipped,
                                       failed=failed, results=results, error=str(ex)[:300])


def public_job(job: MassJobRow) -> Dict[str, Any]:
    progress = 0.0 if job["total"] <= 0 else round(job["done"] / job["total"], 4)
    return {**job, "progress": progress, "finished": job["status"] in ("done", "failed", "interrupted")}


# ==========================================
# 🌐 ROUTER
# ==========================================
def build_dashboard_router(require_user: RequireUser, assert_owner: AssertOwner, get_bot: BotGetter,
                           resolve_chat_bot: Optional[ChatBotResolver] = None) -> Any:
    from fastapi import APIRouter, Body, Header, HTTPException, Query  # type: ignore[import-not-found,reportMissingImports]

    router = APIRouter()

    async def owner_of(chat_id: str, init_data: Optional[str], authorization: Optional[str]) -> Tuple[int, int]:
        user_id = require_user(init_data, authorization)
        cid = parse_chat_id(chat_id)
        await assert_owner(int(user_id), cid)
        return cid, int(user_id)

    async def guarded(action: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
        try:
            return await action()
        except DashboardError as err:
            raise HTTPException(status_code=err.status, detail=err.detail)
        except crud.Fase3ValidationError as err:
            raise HTTPException(status_code=422, detail=str(err))
        except HTTPException:
            raise
        except Exception:
            logger.error("❌ [Dashboard API] Error inesperado", exc_info=True)
            raise HTTPException(status_code=500, detail="Error interno del dashboard de moderación.")

    @router.get("/chat/{chat_id}/users/search")
    async def api_search_users(chat_id: str, q: str = Query("", max_length=64),
                               x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                               authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            hits = await crud.search_users(cid, q, 20)
            return {"status": "success", "chat_id": str(cid), "query": q, "results": hits}
        return await guarded(run)

    @router.get("/chat/{chat_id}/users/{user_id}/history")
    async def api_user_history(chat_id: str, user_id: str, limit: Optional[int] = Query(None),
                               window_hours: Optional[int] = Query(None),
                               x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                               authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            target = parse_positive_id(user_id, "user_id")
            history = await crud.user_history(cid, target, _bounded_int(limit, 100, 1, 200, "limit"),
                                              _bounded_int(window_hours, 24, 1, 168, "window_hours"))
            member_status = ""
            if not history["profile"]["full_name"]:
                bot = get_bot()
                if bot is not None:
                    with contextlib.suppress(Exception):
                        member = await asyncio.wait_for(bot.get_chat_member(cid, target), timeout=4)
                        history["profile"]["full_name"] = member.user.full_name
                        history["profile"]["username"] = member.user.username
                        member_status = str(getattr(member.status, "value", member.status) or "")
            return {"status": "success", "chat_id": str(cid), "member_status": member_status or None, **history}
        return await guarded(run)

    @router.get("/chat/{chat_id}/admin-leaderboard")
    async def api_admin_leaderboard(chat_id: str, days: Optional[int] = Query(None), limit: Optional[int] = Query(None),
                                    x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                    authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            window = _bounded_int(days, 30, 1, 365, "days")
            board = await crud.admin_leaderboard(cid, window, _bounded_int(limit, 20, 1, 100, "limit"))
            return {"status": "success", "chat_id": str(cid), "days": window, "leaderboard": board,
                    "scoring": {**ADMIN_POINTS, "false_positive_penalty": FALSE_POSITIVE_PENALTY}}
        return await guarded(run)

    @router.post("/chat/{chat_id}/mass-action", status_code=202)
    async def api_mass_action(chat_id: str, payload: dict = Body(...),
                              x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                              authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            from handlers.logger_hub import spawn_audit_task
            cid, uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            action, user_ids, reason, duration = validate_mass_payload(payload)
            if get_bot() is None:
                raise DashboardError(503, "El bot maestro no está disponible en este momento.")
            try:
                job = await crud.create_mass_job(cid, uid, action, user_ids, reason, duration)
            except crud.Fase3ValidationError as err:
                status = 409 if str(err).startswith("mass_action:") else 422
                raise DashboardError(status, str(err))
            spawn_audit_task(run_mass_job(job, user_ids, get_bot, resolve_chat_bot), name=f"mass_action:{job['id']}")
            return {"status": "accepted", "chat_id": str(cid), "job": public_job(job),
                    "limits": {"max_users": MASS_ACTION_MAX_USERS}}
        return await guarded(run)

    @router.get("/chat/{chat_id}/mass-action/{job_id}")
    async def api_mass_action_status(chat_id: str, job_id: str,
                                     x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                     authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            clean = str(job_id).strip()
            if not clean or len(clean) > 32 or not all(c.isalnum() or c in "-_" for c in clean):
                raise DashboardError(400, "job_id inválido.")
            job = await crud.get_mass_job(cid, clean)
            if job is None:
                raise DashboardError(404, "Trabajo no encontrado en esta comunidad.")
            return {"status": "success", "chat_id": str(cid), "job": public_job(job)}
        return await guarded(run)

    @router.get("/chat/{chat_id}/mass-actions")
    async def api_mass_actions(chat_id: str, x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                               authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            jobs = await crud.list_mass_jobs(cid, 10)
            return {"status": "success", "chat_id": str(cid), "jobs": [public_job(j) for j in jobs]}
        return await guarded(run)

    @router.get("/chat/{chat_id}/automod")
    async def api_get_automod(chat_id: str, x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                              authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            from handlers import automod
            return {"status": "success", "chat_id": str(cid), "automod": await automod.get_config(cid),
                    "options": {"rules": list(AUTOMOD_RULES), "infraction_actions": list(AUTOMOD_INFRACTION_ACTIONS),
                                "limits": {k: list(v) for k, v in AUTOMOD_LIMITS.items()}}}
        return await guarded(run)

    @router.post("/chat/{chat_id}/automod")
    async def api_save_automod(chat_id: str, payload: dict = Body(...),
                               x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                               authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            patch = payload.get("automod") if isinstance(payload, dict) else None
            from handlers import automod
            cfg = await automod.save_config(cid, patch if isinstance(patch, dict) else {}, uid, bot=get_bot())
            return {"status": "success", "chat_id": str(cid), "automod": cfg}
        return await guarded(run)

    @router.post("/chat/{chat_id}/sanctions/{sanction_id}/false-positive")
    async def api_false_positive(chat_id: str, sanction_id: str,
                                 x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                 authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            sid = parse_positive_id(sanction_id, "sanction_id")
            sanction = await crud.get_sanction(sid)
            if sanction is None or sanction["group_id"] != cid:
                raise DashboardError(404, "Sanción no encontrada en esta comunidad.")
            from handlers.moderation import revert_sanction
            outcome = await revert_sanction(sid, uid, bot=await _pick_bot(cid, get_bot, resolve_chat_bot),
                                            owner_verified=True, origin="miniapp")
            if outcome.status == "already":
                raise DashboardError(409, "La sanción ya estaba revertida.")
            if outcome.status == "telegram_failed":
                raise DashboardError(502, f"Telegram rechazó la reversión ({outcome.code}).")
            if outcome.status != "ok":
                raise DashboardError(404, "Sanción no encontrada.")
            return {"status": "success", "chat_id": str(cid), "sanction": outcome.sanction, "admin_stats": outcome.stats}
        return await guarded(run)

    return router
