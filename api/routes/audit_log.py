"""
audit_log.py — The Bunker OS · v41 · API del Canal de Registro en tiempo real (Mini App / Dashboard web)

    GET  /api/chat/{chat_id}/audit-log            Canal vinculado, estado de salud y vectores (activado / silenciado)
    POST /api/chat/{chat_id}/audit-log            {log_channel_id?: "@alias" | "-100…" | "", vectors?: {clave: bool}}
    POST /api/chat/{chat_id}/audit-log/test       Publica una tarjeta de prueba (enfriamiento 30 s)
    GET  /api/chat/{chat_id}/micvip/active        Pases MicVIP vigentes con compra, importe, aplicación del 100 % y expiración

Seguridad: initData firmado / JWT de sesión (require_user) + dueño legítimo (assert_owner), igual que el resto de
paneles de la Mini App. El canal se valida EN VIVO con la misma función que POST /settings (`validate_log_channel`):
el operador debe administrar el canal de registro y el bot debe poder publicar en él. Errores 422 "campo: motivo".
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("bunker.audit_log_api")

TEST_COOLDOWN_S = 30.0
_LAST_TEST: Dict[int, float] = {}

# Vectores (categorías del Log Channel Hub) con sus textos EN / ES: una sola definición para bot, API y Mini App.
from handlers.logger_hub import AUDIT_VECTORS  # noqa: E402

VECTOR_KEYS = tuple(v[0] for v in AUDIT_VECTORS)

RequireUser = Callable[[Optional[str], Optional[str]], int]
AssertOwner = Callable[[int, int], Awaitable[None]]
BotGetter = Callable[[], Any]
ValidateLogChannel = Callable[[int, int, Any], Awaitable[str]]
HealthCheck = Callable[[Any, str], Awaitable[Any]]


class AuditApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def parse_chat_id(raw: Any) -> int:
    text = str(raw).strip()
    if not text.lstrip("-").isdigit() or len(text) > 21 or int(text) == 0:
        raise AuditApiError(400, "chat_id inválido.")
    return int(text)


def validate_vectors(raw: Any) -> Dict[str, bool]:
    """{clave: bool} con claves conocidas; todo o nada (422 'vectors_<clave>: motivo')."""
    if not isinstance(raw, dict) or not raw:
        raise AuditApiError(422, "vectors: se esperaba un objeto {vector: true|false}.")
    out: Dict[str, bool] = {}
    for key, value in raw.items():
        if key not in VECTOR_KEYS:
            raise AuditApiError(422, f"vectors: vector desconocido ({str(key)[:32]}).")
        if not isinstance(value, bool):
            raise AuditApiError(422, f"vectors: el valor de {key} debe ser true o false.")
        out[key] = value
    return out


def public_vectors(disabled: List[str]) -> List[Dict[str, Any]]:
    off = set(disabled or [])
    return [{"key": key, "icon": icon, "label_en": en, "label_es": es, "enabled": key not in off}
            for key, icon, en, es in AUDIT_VECTORS]


def build_audit_log_router(require_user: RequireUser, assert_owner: AssertOwner, get_bot: BotGetter,
                           validate_log_channel: ValidateLogChannel,
                           check_health: Optional[HealthCheck] = None) -> Any:
    from fastapi import APIRouter, Body, Header, HTTPException  # type: ignore[import-not-found,reportMissingImports]
    import database.database as db
    from handlers.logger_hub import dispatch_audit_log, forget_audit_target

    router = APIRouter()

    async def owner_of(chat_id: str, init_data: Optional[str], authorization: Optional[str]) -> Tuple[int, int]:
        user_id = require_user(init_data, authorization)
        cid = parse_chat_id(chat_id)
        await assert_owner(int(user_id), cid)
        return cid, int(user_id)

    async def guarded(action: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
        try:
            return await action()
        except AuditApiError as err:
            raise HTTPException(status_code=err.status, detail=err.detail)
        except HTTPException:
            raise
        except Exception:
            logger.error("❌ [Audit API] Error inesperado", exc_info=True)
            raise HTTPException(status_code=500, detail="Error interno del canal de registro.")

    async def snapshot(cid: int) -> Dict[str, Any]:
        target = await db.get_audit_log_target(cid)
        log_id = target.get("log_channel_id") or None
        health: Any = None
        if log_id and check_health is not None:
            try:
                health = await check_health(get_bot(), str(log_id))
            except Exception as ex:
                logger.debug(f"[Audit API] Salud del canal {log_id} no disponible: {ex}")
        return {"log_channel_id": log_id, "health": health, "vectors": public_vectors(target.get("disabled") or [])}

    @router.get("/chat/{chat_id}/audit-log")
    async def api_get_audit_log(chat_id: str, x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            return {"status": "success", "chat_id": str(cid), "audit_log": await snapshot(cid)}
        return await guarded(run)

    @router.post("/chat/{chat_id}/audit-log")
    async def api_save_audit_log(chat_id: str, payload: dict = Body(...),
                                 x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                 authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            body = payload.get("audit_log", payload) if isinstance(payload, dict) else None
            if not isinstance(body, dict) or not ({"log_channel_id", "vectors"} & set(body)):
                raise AuditApiError(422, "audit_log: envía log_channel_id y/o vectors.")
            vectors = validate_vectors(body["vectors"]) if "vectors" in body else None
            changed: List[str] = []
            if "log_channel_id" in body:
                value = await validate_log_channel(uid, cid, body.get("log_channel_id"))   # 422 log_channel_id: …
                await db.update_chat_operational_settings(cid, {"log_channel_id": value})
                forget_audit_target(cid, value or None)
                changed.append("log_channel_id")
            if vectors is not None:
                current = set((await db.get_audit_log_target(cid)).get("disabled") or [])
                for key, enabled in vectors.items():
                    (current.discard if enabled else current.add)(key)
                await db.set_audit_log_disabled(cid, sorted(current))
                forget_audit_target(cid)
                changed.append("vectors")
            state = await snapshot(cid)
            if state["log_channel_id"]:
                on = [v["key"] for v in state["vectors"] if v["enabled"]]
                off = [v["key"] for v in state["vectors"] if not v["enabled"]]
                await dispatch_audit_log(get_bot(), cid, "log_vectors_changed", actor_id=uid,
                                         detail="Mini App / Dashboard",
                                         extra={("Active vectors", "Vectores activos"): ", ".join(on) or "—",
                                                ("Muted vectors", "Vectores silenciados"): ", ".join(off) or "—"})
            return {"status": "success", "chat_id": str(cid), "changed": changed, "audit_log": state}
        return await guarded(run)

    @router.post("/chat/{chat_id}/audit-log/test")
    async def api_test_audit_log(chat_id: str, x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                 authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            now = time.monotonic()
            last = _LAST_TEST.get(cid)
            if last is not None and now - last < TEST_COOLDOWN_S:
                raise AuditApiError(429, f"Espera {int(TEST_COOLDOWN_S - (now - last)) + 1} s para otra prueba.")
            target = await db.get_audit_log_target(cid)
            if not target.get("log_channel_id"):
                raise AuditApiError(409, "log_channel_id: vincula primero un canal de registro.")
            _LAST_TEST[cid] = now
            forget_audit_target(cid, target.get("log_channel_id"))
            queued = await dispatch_audit_log(get_bot(), cid, "log_channel_test", actor_id=uid,
                                              detail="Real-time delivery check / Comprobación de entrega en tiempo real")
            return {"status": "success", "chat_id": str(cid), "queued": bool(queued)}
        return await guarded(run)

    @router.get("/chat/{chat_id}/micvip/active")
    async def api_micvip_active(chat_id: str, x_telegram_init_data: str = Header(None, alias="x-telegram-init-data"),
                                authorization: str = Header(None)):
        async def run() -> Dict[str, Any]:
            cid, _uid = await owner_of(chat_id, x_telegram_init_data, authorization)
            passes = await db.micvip_active_passes(cid, 200)
            return {"status": "success", "chat_id": str(cid), "server_time": int(time.time()), "passes": passes,
                    "summary": {"active": len(passes),
                                "volume_confirmed": sum(1 for p in passes if p.get("sentinel_applied_at")),
                                "stars_active": sum(int(p.get("last_amount_stars") or 0) for p in passes)}}
        return await guarded(run)

    return router
