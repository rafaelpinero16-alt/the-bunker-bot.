"""
anti_spam.py — The Bunker OS (Aiogram 3.x)

Middleware de inspección perimetral y seguridad global.
Motor Híbrido: Detección proactiva CAS (Combot Anti-Spam) + Blacklist alfanumérica + 
               Escudo Semántico en el Borde (Fase 2: Mistral AI Guardián) + Inmunidad táctica.
v37 · Anti-Rogue: en grupos, el bot ignora EN SILENCIO los comandos de quien no tiene rango para invocarlo
      (o enviados en grupos que el bot no aprobó). Avisos de respaldo en el idioma de servicio del grupo.
The Bunker Command OS © 2026 — Cloud Media Management
"""
import asyncio
import html
import logging
import os
import re
import time
from typing import Optional

import aiohttp
from aiogram import BaseMiddleware
from aiogram.types import ChatPermissions, Message

from assistant import active_sentinels, set_participant_mic, semantic_scan_content
from database.database import (
    add_user_strike,
    get_lock_status,
    is_group_approved,
    add_warning,
    ban_user,
    get_blacklist,
    get_or_create_user,
    get_session_by_group,
    get_warns_config,
    get_group_tier,
    get_ai_sentinel_config,
    is_vip_mic_active,
    is_whitelisted,
    reset_user_strikes,
)

logger = logging.getLogger("anti_spam_middleware")

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS Y SERVICIO
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

SERVICE_ACCOUNT_IDS = {777000, 1087968824, 136817688}

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


# ==========================================
# 🛡️ v37 · ANTI-ROGUE (BLOQUEO DE INVOCACIÓN EXTERNA)
# ==========================================
# Protege al bot de reportes masivos: nadie sin rango puede hacerle responder en un grupo.
#   • Grupos NO aprobados por el bot → se ignora todo comando dirigido al bot (salvo creador / «Añadir admins»).
#   • Comunidades aprobadas:
#       - creador, Arquitecto o administrador con «Añadir administradores» → cualquier comando;
#       - cualquier otro administrador → solo los comandos de moderación (MODERATOR_COMMANDS);
#       - miembros → solo los comandos públicos (PUBLIC_MEMBER_COMMANDS).
#   • Lo demás se descarta sin responder (y se borra si la comunidad tiene /lock_commands activo).
# Los comandos dirigidos explícitamente a OTRO bot (/cmd@otrobot) no se tocan. Las listas se amplían por entorno
# (ANTI_ROGUE_PUBLIC_COMMANDS / ANTI_ROGUE_MODERATOR_COMMANDS, separados por comas).
def _env_commands(name: str) -> set[str]:
    return {c.strip().lstrip("/").lower() for c in os.getenv(name, "").split(",") if c.strip()}


PUBLIC_MEMBER_COMMANDS: frozenset[str] = frozenset({
    "micvip", "mic_vip", "rank", "xp", "nivel", "top", "leaderboard", "ranking",
    "speakers", "lang", "language", "idioma",
} | _env_commands("ANTI_ROGUE_PUBLIC_COMMANDS"))
MODERATOR_COMMANDS: frozenset[str] = frozenset({
    "warn", "unwarn", "resetwarns", "warns", "ban", "unban", "tban", "kick", "mute", "unmute", "tmute",
    "del", "delete", "purge", "pin", "unpin", "report", "reload", "settings", "matrix", "panel", "info", "id",
} | _env_commands("ANTI_ROGUE_MODERATOR_COMMANDS"))
_COMMAND_RE = re.compile(r"^[/!]([A-Za-z0-9_]{1,64})(?:@([A-Za-z0-9_]{3,64}))?(?:\s|$)")
_ROGUE_RIGHTS_TTL = 120.0
_ROGUE_APPROVED_TTL = 300.0
_ROGUE_RIGHTS_CACHE: dict[tuple[int, int], tuple[str, float]] = {}
_ROGUE_APPROVED_CACHE: dict[int, tuple[bool, float]] = {}
_ROGUE_BOT_USERNAMES: dict[int, str] = {}

RIGHTS_FULL, RIGHTS_MODERATOR, RIGHTS_NONE = "full", "moderator", "none"


async def _rogue_bot_username(bot) -> str:
    cached = _ROGUE_BOT_USERNAMES.get(bot.id)
    if cached is not None:
        return cached
    try:
        username = ((await bot.get_me()).username or "").lower()
    except Exception:
        return ""
    _ROGUE_BOT_USERNAMES[bot.id] = username
    return username


async def sender_rights(bot, chat_id: int, user_id: int) -> str:
    """'full' (creador / Arquitecto / admin con can_promote_members) · 'moderator' (otro admin) · 'none'."""
    if is_super_admin(user_id) or user_id in SERVICE_ACCOUNT_IDS:
        return RIGHTS_FULL
    key = (int(chat_id), int(user_id))
    now = time.monotonic()
    cached = _ROGUE_RIGHTS_CACHE.get(key)
    if cached and now - cached[1] < _ROGUE_RIGHTS_TTL:
        return cached[0]
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=user_id), timeout=5)
        status = str(getattr(member.status, "value", member.status) or "")
        if status == "creator" or (status == "administrator" and bool(getattr(member, "can_promote_members", False))):
            rights = RIGHTS_FULL
        elif status == "administrator":
            rights = RIGHTS_MODERATOR
        else:
            rights = RIGHTS_NONE
    except Exception:
        return RIGHTS_NONE   # fallo cerrado y sin caché: sin verificación no se ejecutan comandos privilegiados
    if len(_ROGUE_RIGHTS_CACHE) > 20000:
        _ROGUE_RIGHTS_CACHE.clear()
    _ROGUE_RIGHTS_CACHE[key] = (rights, now)
    return rights


async def sender_can_invoke(bot, chat_id: int, user_id: int) -> bool:
    """Compatibilidad v37: True para creador, Arquitecto o administrador con «Añadir administradores»."""
    return await sender_rights(bot, chat_id, user_id) == RIGHTS_FULL


def forget_sender_rights(chat_id: int, user_id: Optional[int] = None) -> None:
    """Invalida la caché de rangos (cambios de administrador)."""
    if user_id is None:
        for key in [k for k in _ROGUE_RIGHTS_CACHE if k[0] == int(chat_id)]:
            _ROGUE_RIGHTS_CACHE.pop(key, None)
    else:
        _ROGUE_RIGHTS_CACHE.pop((int(chat_id), int(user_id)), None)


async def _rogue_chat_approved(chat_id: int) -> bool:
    now = time.monotonic()
    cached = _ROGUE_APPROVED_CACHE.get(chat_id)
    if cached and now - cached[1] < _ROGUE_APPROVED_TTL:
        return cached[0]
    try:
        approved = bool(await is_group_approved(chat_id))
    except Exception:
        return cached[0] if cached else False
    if len(_ROGUE_APPROVED_CACHE) > 20000:
        _ROGUE_APPROVED_CACHE.clear()
    _ROGUE_APPROVED_CACHE[chat_id] = (approved, now)
    return approved


def forget_chat_approval(chat_id: int) -> None:
    _ROGUE_APPROVED_CACHE.pop(int(chat_id), None)


async def anti_rogue_should_drop(event: Message) -> bool:
    """True si el comando debe ignorarse en silencio (Anti-Rogue)."""
    match = _COMMAND_RE.match(event.text or event.caption or "")
    if not match:
        return False
    command, target = match.group(1).lower(), (match.group(2) or "").lower()
    if target and target != await _rogue_bot_username(event.bot):
        return False   # comando para otro bot: no es asunto nuestro
    chat_id = event.chat.id

    if event.sender_chat is not None:
        # Admin anónimo del propio grupo: en una comunidad aprobada pasa (los handlers exigen al Dueño y le explican
        # cómo desactivar el anonimato). Un canal externo nunca invoca al bot.
        return not (event.sender_chat.id == chat_id and await _rogue_chat_approved(chat_id))

    user = event.from_user
    if user is None or user.is_bot:
        return True
    rights = await sender_rights(event.bot, chat_id, user.id)
    if rights == RIGHTS_FULL:
        return False
    if not await _rogue_chat_approved(chat_id):
        return True
    if rights == RIGHTS_MODERATOR and command in MODERATOR_COMMANDS:
        return False
    return command not in PUBLIC_MEMBER_COMMANDS


async def _rogue_cleanup(event: Message) -> None:
    """Borrado silencioso opcional: solo si la comunidad tiene la cerradura de comandos activa."""
    try:
        if await get_lock_status(event.chat.id, "lock_commands") == 1:
            await event.delete()
    except Exception:
        pass


class AntiRogueMiddleware(BaseMiddleware):
    """Uso opcional como middleware independiente (dp.message.outer_middleware(AntiRogueMiddleware()))."""

    async def __call__(self, handler, event: Message, data: dict):
        if event.chat and event.chat.type in ("group", "supergroup") and await anti_rogue_should_drop(event):
            _spawn(_rogue_cleanup(event))
            return None
        return await handler(event, data)


# ==========================================
# 🌐 v37 · AVISOS DE RESPALDO EN EL IDIOMA DE SERVICIO DEL GRUPO
# ==========================================
_FALLBACK_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "sanction": ("🚫 <b>Enforcement active</b>\n\n{mention} was {action} after reaching <b>{strikes}/{limit}</b> warnings.\n"
                     "• <b>Violation:</b> <code>{reason}</code>\n\n🛡️ <i>Cloud Media Management</i>"),
        "strike": ("⚠️ <b>Security strike ({strikes}/{limit})</b>\n\n{mention}, your message was removed for a perimeter "
                   "violation.\n• <b>Violation:</b> <code>{reason}</code>\n\nReaching {limit} warnings triggers an automatic "
                   "sanction.\n\n🛡️ <i>Cloud Media Management</i>"),
        "ban": "permanently banned", "kick": "kicked", "mute": "muted in chat and voice", "default": "sanctioned",
        "user": "User",
    },
    "es": {
        "sanction": ("🚫 <b>Protocolo de sanción ejecutado</b>\n\n{mention} fue {action} tras acumular <b>{strikes}/{limit}</b> "
                     "advertencias.\n• <b>Infracción:</b> <code>{reason}</code>\n\n🛡️ <i>Cloud Media Management</i>"),
        "strike": ("⚠️ <b>Advertencia táctica ({strikes}/{limit})</b>\n\n{mention}, tu mensaje fue purgado por violación "
                   "perimetral.\n• <b>Infracción:</b> <code>{reason}</code>\n\nAl acumular {limit} advertencias recibirás una "
                   "sanción automática.\n\n🛡️ <i>Cloud Media Management</i>"),
        "ban": "bloqueado permanentemente", "kick": "expulsado temporalmente", "mute": "silenciado en chat y voz",
        "default": "sancionado", "user": "Usuario",
    },
}


async def _service_lang_ttl(chat_id: int) -> tuple[str, int]:
    """(idioma, TTL) de servicio de la comunidad; inglés y 20 s si handlers.groups no está disponible."""
    try:
        from handlers.groups import group_lang, service_ttl
        return await group_lang(chat_id), await service_ttl(chat_id, 20)
    except Exception:
        return "en", 20


# ==========================================
# 🌐 NÚCLEO ANTISPAM GLOBAL (CAS - Combot Anti-Spam)
# ==========================================
CAS_API_URL = "https://api.cas.chat/check"
_CAS_CACHE: dict = {}      # user_id -> (is_spammer: bool, timestamp: float)
_CACHE_TTL = 3600          # 1 hora de persistencia en caché


async def check_global_cas_spam(user_id: int) -> bool:
    """
    Consulta la API global de Combot Anti-Spam (CAS) de forma asíncrona y no bloqueante.
    Retorna True si el usuario tiene antecedentes globales de spam o estafa.
    """
    if user_id <= 0 or user_id in SERVICE_ACCOUNT_IDS or is_super_admin(user_id):
        return False

    now = time.time()
    cached = _CAS_CACHE.get(user_id)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    # Limpieza preventiva de caché para evitar fugas de memoria
    if len(_CAS_CACHE) > 5000:
        expired = [uid for uid, (_, ts) in _CAS_CACHE.items() if now - ts > _CACHE_TTL]
        for uid in expired:
            _CAS_CACHE.pop(uid, None)

    timeout = aiohttp.ClientTimeout(total=2.5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(CAS_API_URL, params={"user_id": user_id}) as response:
                if response.status == 200:
                    data = await response.json()
                    res = data.get("result", {})
                    is_offender = bool(
                        data.get("ok") and (res.get("offenses") or res.get("offense") or bool(res))
                    )
                    _CAS_CACHE[user_id] = (is_offender, now)
                    if is_offender:
                        logger.warning(f"🚨 [CAS Global] ¡Spammer detectado por base de datos global! User ID: {user_id}")
                    return is_offender
    except Exception as e:
        logger.debug(f"Aviso CAS consultando user={user_id}: {e}")

    _CAS_CACHE[user_id] = (False, now)
    return False


async def is_immune(event: Message) -> bool:
    """Verifica si el emisor cuenta con inmunidad táctica (Arquitecto, Whitelist, Creador, Admin o Centinela)."""
    # 1. Mensajes enviados como el propio canal o administrador anónimo
    if event.sender_chat and event.sender_chat.id == event.chat.id:
        return True

    if not event.from_user:
        return False

    user_id = event.from_user.id

    # 2. Inmunidad Suprema de Arquitectos y cuentas de servicio
    if is_super_admin(user_id) or user_id in SERVICE_ACCOUNT_IDS or (event.bot and user_id == event.bot.id):
        return True

    # 3. Inmunidad de Lista Blanca (Whitelist)
    try:
        if await is_whitelisted(user_id):
            return True
    except Exception:
        pass

    # 4. Inmunidad de Centinela Maestro
    username = (event.from_user.username or "").lower()
    if username == "alphacentinel":
        return True

    if event.chat.type in ("group", "supergroup"):
        chat_id = event.chat.id

        # 5. Centinela Dedicado activo en memoria
        sentinel_info = active_sentinels.get(chat_id)
        if sentinel_info and sentinel_info.get("user_id") == user_id:
            return True

        # 6. Sesión registrada en la base de datos
        try:
            session_row = await get_session_by_group(chat_id)
            if session_row and session_row[0] == user_id:
                return True
        except Exception:
            pass

        # 7. Administradores y creadores del grupo
        try:
            member = await event.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ("creator", "administrator"):
                return True
        except Exception:
            pass

    return False


def matches_blacklisted_term(word: str, text: str) -> bool:
    """Detecta términos prohibidos usando límites de palabra para evitar falsos positivos."""
    clean_word = word.strip().lower()
    if not clean_word:
        return False

    if clean_word.isalnum() and len(clean_word) <= 4:
        pattern = r'\b' + re.escape(clean_word) + r'\b'
        return bool(re.search(pattern, text))

    return clean_word in text


class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        # 1. Permitir flujo inmediato en chats privados (DMs del bot y clones)
        if event.chat and event.chat.type == "private":
            return await handler(event, data)

        # 1b. 🛡️ v37 · Anti-Rogue: comandos sin rango suficiente o en grupos ajenos → ignorados en silencio.
        if event.chat and event.chat.type in ("group", "supergroup") and await anti_rogue_should_drop(event):
            logger.debug(f"🛡️ [Anti-Rogue] Comando ignorado en {event.chat.id} "
                         f"(emisor={getattr(event.from_user, 'id', None)} sender_chat={getattr(event.sender_chat, 'id', None)}).")
            _spawn(_rogue_cleanup(event))
            return None

        # 2. Control sobre canales externos usados como remitente (Telegram Premium channel posting)
        if event.sender_chat and event.sender_chat.id != event.chat.id:
            text_content = (event.text or event.caption or "").lower()
            if text_content:
                try:
                    blacklist = await get_blacklist()
                except Exception:
                    blacklist = []

                channel_threat = False
                for b_word in blacklist:
                    if matches_blacklisted_term(b_word, text_content):
                        channel_threat = True
                        break

                if not channel_threat:
                    # Escaneo semántico heurístico para canales externos
                    semantic_check = await semantic_scan_content(text_content)
                    if semantic_check.get("flagged"):
                        channel_threat = True

                if channel_threat:
                    try:
                        await event.delete()
                        await event.bot.ban_chat_sender_chat(event.chat.id, event.sender_chat.id)
                        logger.warning(f"🛡️ [Anti-Spam] Canal emisor {event.sender_chat.id} bloqueado por violación perimetral en {event.chat.id}.")
                    except Exception:
                        pass
                    return

            # 🛡️ v23 · Matriz multimedia también para mensajes "como canal": su from_user es el bot de servicio
            # (is_bot=True) y group_security_matrix los ignoraba, así que spoilers, enlaces camuflados, botones inline,
            # reenvíos o películas publicados como canal evadían los 30 vectores. Las publicaciones automáticas del
            # canal vinculado (is_automatic_forward) son del propio dueño y no se tocan.
            if event.chat.type in ("group", "supergroup") and not getattr(event, "is_automatic_forward", False):
                try:
                    from handlers import media_locks
                    if await media_locks.inspect_sender_chat(event.bot, event):
                        return
                except Exception as ml_err:
                    logger.debug(f"Aviso inspeccionando canal emisor en {event.chat.id}: {ml_err}")

        # 3. 🛡️ Inmunidad Táctica para Administradores, Aliados y Centinelas
        if await is_immune(event):
            return await handler(event, data)

        if not event.from_user or event.from_user.is_bot:
            return await handler(event, data)

        user_id = event.from_user.id
        chat_id = event.chat.id

        # 4. 🌐 Verificación contra Base de Datos Global Anti-Spam (CAS)
        if await check_global_cas_spam(user_id):
            try:
                await event.delete()
                await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await ban_user(user_id)
                logger.warning(f"🛡️ [Anti-Spam Global] Spammer global CAS {user_id} expulsado de {chat_id}.")
            except Exception as cas_err:
                logger.error(f"❌ Error expulsando spammer global CAS ({chat_id}/{user_id}): {cas_err}")
            return

        # 5. 🔍 Verificación preventiva de bloqueo en base de datos local
        try:
            user_data = await get_or_create_user(
                user_id=user_id,
                username=event.from_user.username or "",
                full_name=event.from_user.full_name or ""
            )
            is_banned = user_data[2] if user_data else 0

            if is_banned:
                try:
                    await event.delete()
                    await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                except Exception:
                    pass
                return
        except Exception as db_err:
            logger.debug(f"Aviso verificando usuario local {user_id}: {db_err}")

        # 6. 🚨 Inspección Blacklist Alfanumérica + Escudo Semántico en el Borde (Fase 2)
        text_content = (event.text or event.caption or "").lower()
        if text_content:
            try:
                blacklist = await get_blacklist()
            except Exception:
                blacklist = []

            threat_found = False
            threat_reason = "Término en Lista Negra"

            # A) Cotejo rápido por lista negra local
            for b_word in blacklist:
                if matches_blacklisted_term(b_word, text_content):
                    threat_found = True
                    break

            # B) Escudo Semántico de IA (Detección de Leetspeak, Phishing y Violaciones Evasivas)
            if not threat_found:
                try:
                    ai_cfg = await get_ai_sentinel_config(chat_id)
                    tier = (await get_group_tier(chat_id) or "free").lower()
                    is_ultra = tier in ("ultra_pro", "ultra")

                    if is_ultra or ai_cfg.get("guardian_status") == 1:
                        semantic_res = await semantic_scan_content(
                            text_content, 
                            custom_prompt=ai_cfg.get("custom_prompt", "")
                        )
                        if semantic_res.get("flagged"):
                            threat_found = True
                            threat_reason = semantic_res.get("reason", "Infracción Semántica Detectada")
                except Exception as sem_err:
                    logger.debug(f"Aviso en análisis semántico: {sem_err}")

            if threat_found:
                try:
                    await event.delete()
                except Exception as e:
                    logger.warning(f"Aviso al purgar mensaje prohibido en grupo {chat_id}: {e}")

                fb_lang, fb_ttl = await _service_lang_ttl(chat_id)
                fbt = _FALLBACK_TEXTS.get(fb_lang, _FALLBACK_TEXTS["en"])
                clean_name = html.escape(event.from_user.full_name or fbt["user"])
                user_mention = f"<a href='tg://user?id={user_id}'>{clean_name}</a>"

                # 🎯 Delegación preferente a la escalera unificada de sanciones
                try:
                    from handlers.groups import enforce_warn_ladder
                    clean_username = event.from_user.username or ""
                    outcome = await enforce_warn_ladder(
                        bot=event.bot,
                        chat_id=chat_id,
                        target_id=user_id,
                        target_username=clean_username,
                        target_mention=user_mention,
                        reply_to=None,
                        reason=threat_reason
                    )
                    if outcome.get("status") in ("warned", "sanctioned", "immune"):
                        return
                except Exception as ladder_err:
                    logger.debug(f"Aviso delegando a ladder de warns: {ladder_err}")

                # 🛟 Fallback autónomo en caso de que la escalera externa no responda
                try:
                    strikes = await add_user_strike(chat_id, user_id, threat_reason)
                except Exception:
                    strikes = await add_warning(user_id)

                warns_cfg = await get_warns_config(chat_id)
                limit = warns_cfg.get("limit", 3)
                action = warns_cfg.get("action", "mute")

                if strikes >= limit:
                    action_desc = fbt["default"]
                    try:
                        await set_participant_mic(chat_id=chat_id, user_id=user_id, muted=True, volume=0)
                    except Exception:
                        pass

                    try:
                        if action == "ban":
                            await ban_user(user_id)
                            await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                            action_desc = fbt["ban"]
                        elif action == "kick":
                            await event.bot.ban_chat_member(
                                chat_id=chat_id,
                                user_id=user_id,
                                until_date=int(time.time() + 35)
                            )
                            await event.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                            action_desc = fbt["kick"]
                        elif action == "mute":
                            await event.bot.restrict_chat_member(
                                chat_id=chat_id,
                                user_id=user_id,
                                permissions=ChatPermissions(can_send_messages=False)
                            )
                            action_desc = fbt["mute"]
                    except Exception as e:
                        logger.error(f"Error aplicando castigo por infracción perimetral: {e}")

                    await reset_user_strikes(chat_id, user_id)

                    warning_text = fbt["sanction"].format(mention=user_mention, action=action_desc, strikes=strikes,
                                                          limit=limit, reason=html.escape(threat_reason))
                else:
                    warning_text = fbt["strike"].format(mention=user_mention, strikes=strikes, limit=limit,
                                                        reason=html.escape(threat_reason))

                try:
                    warning_msg = await event.bot.send_message(
                        chat_id=chat_id,
                        text=warning_text,
                        parse_mode="HTML"
                    )

                    async def auto_delete_notice(msg: Message):
                        await asyncio.sleep(fb_ttl)
                        try:
                            await msg.delete()
                        except Exception:
                            pass

                    _spawn(auto_delete_notice(warning_msg))
                except Exception:
                    pass

                return

        return await handler(event, data)