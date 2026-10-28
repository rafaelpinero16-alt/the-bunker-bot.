"""
telegram_ops.py — The Bunker OS · v40 · Fase 3 · Operaciones de moderación contra Telegram

Capa compartida por Auto-Mod, la reversión de falsos positivos y la Gestión Masiva de la Mini App:
  • classify_telegram_error(): aiogram 3 ya no tiene clases por error concreto (las de aiogram 2 como
    UserIsAnAdministratorOfTheChat o ChatNotFound desaparecieron); llegan como TelegramBadRequest /
    TelegramForbiddenError con el texto de la Bot API. Aquí se traducen a un código estable (ErrorKind).
  • candidate_bots(): el bot del evento primero, luego el clon de la comunidad y el bot maestro (sin duplicados).
  • ban / unban / restrict / restore: cada llamada con timeout, reintento único ante RetryAfter y resultado tipado
    (OpResult) en lugar de excepciones: los llamadores deciden sin try/except repetidos.
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Literal, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import ChatPermissions

logger = logging.getLogger("bunker.telegram_ops")

TG_TIMEOUT_S = 10.0
RETRY_AFTER_CAP_S = 30.0

ErrorKind = Literal[
    "user_is_admin",        # USER_IS_ADMIN / "user is an administrator of the chat" / "can't remove chat owner"
    "chat_not_found",       # "chat not found"
    "user_not_found",       # "user not found" / PARTICIPANT_ID_INVALID / USER_ID_INVALID
    "not_enough_rights",    # "not enough rights" / CHAT_ADMIN_REQUIRED / "need administrator rights"
    "bot_not_member",       # Forbidden: bot was kicked / is not a member
    "not_banned",           # unban de alguien que no estaba baneado (informativo)
    "retry_after",
    "network",
    "timeout",
    "unknown",
]

SERVICE_ACCOUNT_IDS = frozenset({777000, 1087968824, 136817688})

_MARKERS: tuple[tuple[str, ErrorKind], ...] = (
    ("user is an administrator of the chat", "user_is_admin"),
    ("user_is_admin", "user_is_admin"),
    ("can't remove chat owner", "user_is_admin"),
    ("can't restrict self", "user_is_admin"),
    ("user is an administrator", "user_is_admin"),
    ("chat not found", "chat_not_found"),
    ("chat_id_invalid", "chat_not_found"),
    ("group chat was upgraded", "chat_not_found"),
    ("user not found", "user_not_found"),
    ("participant_id_invalid", "user_not_found"),
    ("user_id_invalid", "user_not_found"),
    ("member not found", "user_not_found"),
    ("not enough rights", "not_enough_rights"),
    ("chat_admin_required", "not_enough_rights"),
    ("have no rights", "not_enough_rights"),
    ("need administrator rights", "not_enough_rights"),
    ("method is available only for supergroups", "not_enough_rights"),
    ("bot was kicked", "bot_not_member"),
    ("bot is not a member", "bot_not_member"),
    ("chat_write_forbidden", "bot_not_member"),
)


def classify_telegram_error(exc: BaseException) -> ErrorKind:
    """Código estable para un error de la Bot API (las clases específicas de aiogram 2 no existen en aiogram 3)."""
    if isinstance(exc, TelegramRetryAfter):
        return "retry_after"
    if isinstance(exc, TelegramNetworkError):
        return "network"
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    text = str(getattr(exc, "message", "") or exc).lower()
    for marker, kind in _MARKERS:
        if marker in text:
            return kind
    if isinstance(exc, TelegramNotFound):
        return "chat_not_found"
    if isinstance(exc, TelegramForbiddenError):
        return "bot_not_member"
    return "unknown"


@dataclass(frozen=True)
class OpResult:
    ok: bool
    code: Optional[ErrorKind] = None
    detail: Optional[str] = None
    bot: Optional[Bot] = None

    @property
    def retryable_with_other_bot(self) -> bool:
        return self.code in ("not_enough_rights", "bot_not_member", "chat_not_found", "network", "timeout")


# ==========================================
# 🤖 BOTS CANDIDATOS (evento → clon → maestro)
# ==========================================
BotResolver = Callable[[int], Awaitable[Optional[Bot]]]
_extra_resolver: Optional[BotResolver] = None


def register_bot_resolver(resolver: Optional[BotResolver]) -> None:
    """main.py puede registrar _clone_bot_for_chat (si no, se reutiliza el resolutor de ban_appeals)."""
    global _extra_resolver
    _extra_resolver = resolver


def _ban_appeals_module() -> Any:
    name = "handlers.ban_appeals" if importlib.util.find_spec("handlers.ban_appeals") is not None else "ban_appeals"
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _master_bot() -> Optional[Bot]:
    try:
        from handlers.logger_hub import master_bot as _hub_master
        bot = _hub_master()
        if bot is not None:
            return bot
    except Exception:
        pass
    for module_name in ("__main__", "main"):
        module = sys.modules.get(module_name)
        candidate = getattr(module, "master_bot_instance", None) if module is not None else None
        if candidate is not None:
            return candidate
    return None


async def candidate_bots(group_id: int, preferred: Optional[Bot] = None) -> List[Bot]:
    """Bots que pueden actuar sobre la comunidad, sin duplicados: el del evento, el clon y el maestro."""
    out: List[Bot] = []

    def add(bot: Any) -> None:
        if bot is not None and all(bot is not b and getattr(bot, "id", None) != getattr(b, "id", -1) for b in out):
            out.append(bot)

    add(preferred)
    resolver = _extra_resolver
    if resolver is not None:
        try:
            add(await resolver(int(group_id)))
        except Exception as ex:
            logger.debug(f"[TelegramOps] Resolutor de clon falló para {group_id}: {ex}")
    else:
        appeals = _ban_appeals_module()
        finder = getattr(appeals, "_candidate_bots", None) if appeals is not None else None
        if callable(finder):
            try:
                for bot in await finder(int(group_id)):
                    add(bot)
            except Exception as ex:
                logger.debug(f"[TelegramOps] Candidatos de ban_appeals no disponibles para {group_id}: {ex}")
    add(_master_bot())
    return out


# ==========================================
# 🔧 LLAMADAS PROTEGIDAS
# ==========================================
async def _call(bot: Bot, factory: Callable[[Bot], Awaitable[Any]]) -> OpResult:
    for attempt in (1, 2):
        try:
            await asyncio.wait_for(factory(bot), timeout=TG_TIMEOUT_S)
            return OpResult(ok=True, bot=bot)
        except TelegramRetryAfter as ex:
            if attempt == 2:
                return OpResult(ok=False, code="retry_after", detail=str(ex)[:200], bot=bot)
            await asyncio.sleep(min(RETRY_AFTER_CAP_S, float(getattr(ex, "retry_after", 1) or 1)) + 0.25)
        except asyncio.TimeoutError:
            return OpResult(ok=False, code="timeout", detail="timeout", bot=bot)
        except TelegramAPIError as ex:
            return OpResult(ok=False, code=classify_telegram_error(ex), detail=str(ex)[:200], bot=bot)
        except asyncio.CancelledError:
            raise
        except Exception as ex:   # sesión HTTP cerrada, etc.: nunca se propaga al handler
            return OpResult(ok=False, code="unknown", detail=str(ex)[:200], bot=bot)
    return OpResult(ok=False, code="unknown", bot=bot)


async def run_with_fallback(group_id: int, preferred: Optional[Bot],
                            factory: Callable[[Bot], Awaitable[Any]]) -> OpResult:
    """Ejecuta la operación con el primer bot capaz; si uno no tiene permisos, prueba con el siguiente."""
    last = OpResult(ok=False, code="unknown", detail="no bot available")
    for bot in await candidate_bots(group_id, preferred):
        result = await _call(bot, factory)
        if result.ok or not result.retryable_with_other_bot:
            return result
        last = result
    return last


async def ban_member(group_id: int, user_id: int, *, preferred: Optional[Bot] = None,
                     until_ts: Optional[int] = None) -> OpResult:
    """Baneo (permanente si until_ts es None). Las cuentas de servicio de Telegram nunca son objetivo."""
    if int(user_id) in SERVICE_ACCOUNT_IDS:
        return OpResult(ok=False, code="user_is_admin", detail="service account")
    return await run_with_fallback(group_id, preferred, lambda b: b.ban_chat_member(
        chat_id=int(group_id), user_id=int(user_id), until_date=int(until_ts) if until_ts else None))


async def unban_member(group_id: int, user_id: int, *, preferred: Optional[Bot] = None) -> OpResult:
    return await run_with_fallback(group_id, preferred, lambda b: b.unban_chat_member(
        chat_id=int(group_id), user_id=int(user_id), only_if_banned=True))


MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False,
)
_FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
)


async def mute_member(group_id: int, user_id: int, *, preferred: Optional[Bot] = None,
                      minutes: Optional[int] = None) -> OpResult:
    until = int(time.time()) + int(minutes) * 60 if minutes else None
    return await run_with_fallback(group_id, preferred, lambda b: b.restrict_chat_member(
        chat_id=int(group_id), user_id=int(user_id), permissions=MUTED_PERMISSIONS, until_date=until))


async def default_permissions(bot: Bot, group_id: int) -> ChatPermissions:
    """Permisos generales del grupo (levantar un silencio no debe saltarse un Raid Lockdown ni las restricciones)."""
    try:
        chat = await asyncio.wait_for(bot.get_chat(int(group_id)), timeout=TG_TIMEOUT_S)
        if chat.permissions is not None:
            return chat.permissions
    except Exception:
        pass
    return _FULL_PERMISSIONS


async def unmute_member(group_id: int, user_id: int, *, preferred: Optional[Bot] = None) -> OpResult:
    async def factory(bot: Bot) -> Any:
        permissions = await default_permissions(bot, group_id)
        return await bot.restrict_chat_member(chat_id=int(group_id), user_id=int(user_id), permissions=permissions)
    return await run_with_fallback(group_id, preferred, factory)


async def member_status(group_id: int, user_id: int, *, preferred: Optional[Bot] = None) -> str:
    """'creator' | 'administrator' | 'member' | 'restricted' | 'left' | 'kicked' | '' (no se pudo consultar)."""
    for bot in await candidate_bots(group_id, preferred):
        try:
            member = await asyncio.wait_for(bot.get_chat_member(int(group_id), int(user_id)), timeout=TG_TIMEOUT_S)
        except Exception:
            continue
        return str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", "")) or "")
    return ""


async def is_protected_target(group_id: int, user_id: int, *, preferred: Optional[Bot] = None) -> Optional[str]:
    """Motivo por el que NO se puede sancionar (cuenta de servicio, Arquitecto, admin/creador) o None."""
    if int(user_id) in SERVICE_ACCOUNT_IDS:
        return "service_account"
    try:
        from handlers.moderation import is_super_admin
        if is_super_admin(int(user_id)):
            return "architect"
    except Exception:
        pass
    status = await member_status(group_id, user_id, preferred=preferred)
    if status in ("creator", "administrator"):
        return "user_is_admin"
    return None
