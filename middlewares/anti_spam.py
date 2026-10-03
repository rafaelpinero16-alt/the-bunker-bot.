"""
anti_spam.py — The Bunker OS (Aiogram 3.x)

Middleware de inspección perimetral y seguridad global.
Motor Híbrido: Detección proactiva CAS (Combot Anti-Spam) + Blacklist alfanumérica + Inmunidad táctica.
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

from assistant import active_sentinels, set_participant_mic
from database.database import (
    add_user_strike,
    add_warning,
    ban_user,
    get_blacklist,
    get_or_create_user,
    get_session_by_group,
    get_warns_config,
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


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


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
                    # CAS API devuelve "offenses" (entero) o estructura de resultado
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

    # Para términos cortos alfanuméricos (ej. 'cp', 'kill', 'pedo'), exigir coincidencia de palabra completa
    if clean_word.isalnum() and len(clean_word) <= 4:
        pattern = r'\b' + re.escape(clean_word) + r'\b'
        return bool(re.search(pattern, text))

    return clean_word in text


class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        # 1. Permitir flujo inmediato en chats privados (DMs del bot y clones)
        if event.chat and event.chat.type == "private":
            return await handler(event, data)

        # 2. Control sobre canales externos usados como remitente (Telegram Premium channel posting)
        if event.sender_chat and event.sender_chat.id != event.chat.id:
            text_content = (event.text or event.caption or "").lower()
            if text_content:
                try:
                    blacklist = await get_blacklist()
                except Exception:
                    blacklist = []
                for b_word in blacklist:
                    if matches_blacklisted_term(b_word, text_content):
                        try:
                            await event.delete()
                            await event.bot.ban_chat_sender_chat(event.chat.id, event.sender_chat.id)
                            logger.warning(f"🛡️ [Anti-Spam] Canal emisor {event.sender_chat.id} bloqueado por Blacklist en {event.chat.id}.")
                        except Exception:
                            pass
                        return

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

        # 6. 🚨 Inspección y purga de términos prohibidos de la Blacklist
        text_content = (event.text or event.caption or "").lower()
        if text_content:
            try:
                blacklist = await get_blacklist()
            except Exception:
                blacklist = []

            threat_found = False
            for b_word in blacklist:
                if matches_blacklisted_term(b_word, text_content):
                    threat_found = True
                    break

            if threat_found:
                try:
                    await event.delete()
                except Exception as e:
                    logger.warning(f"Aviso al purgar mensaje prohibido en grupo {chat_id}: {e}")

                clean_name = html.escape(event.from_user.full_name or "Usuario")
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
                        reason="filter"
                    )
                    if outcome.get("status") in ("warned", "sanctioned", "immune"):
                        return
                except Exception as ladder_err:
                    logger.debug(f"Aviso delegando a ladder de warns: {ladder_err}")

                # 🛟 Fallback autónomo en caso de que no cargue la escalera externa
                try:
                    strikes = await add_user_strike(chat_id, user_id, "Término en Lista Negra")
                except Exception:
                    strikes = await add_warning(user_id)

                warns_cfg = await get_warns_config(chat_id)
                limit = warns_cfg.get("limit", 3)
                action = warns_cfg.get("action", "mute")

                if strikes >= limit:
                    action_desc = "sancionado / sanctioned"
                    try:
                        await set_participant_mic(chat_id=chat_id, user_id=user_id, muted=True, volume=0)
                    except Exception:
                        pass

                    try:
                        if action == "ban":
                            await ban_user(user_id)
                            await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                            action_desc = "bloqueado permanentemente / permanently banned"
                        elif action == "kick":
                            await event.bot.ban_chat_member(
                                chat_id=chat_id,
                                user_id=user_id,
                                until_date=int(time.time() + 35)
                            )
                            await event.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                            action_desc = "expulsado temporalmente / kicked"
                        elif action == "mute":
                            await event.bot.restrict_chat_member(
                                chat_id=chat_id,
                                user_id=user_id,
                                permissions=ChatPermissions(can_send_messages=False)
                            )
                            action_desc = "silenciado en chat y voz / muted"
                    except Exception as e:
                        logger.error(f"Error aplicando castigo por Blacklist: {e}")

                    await reset_user_strikes(chat_id, user_id)

                    warning_text = (
                        f"🚫 <b>Protocolo de Sanción Ejecutado / Enforcement Active</b>\n\n"
                        f"{user_mention} ha sido {action_desc} tras acumular "
                        f"<b>{strikes}/{limit}</b> advertencias por contenido prohibido.\n\n"
                        f"🇺🇸 <i>Strike limit reached. Automated perimeter sanctions applied across chat and audio streams.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                else:
                    warning_text = (
                        f"⚠️ <b>Advertencia Táctica / Security Strike ({strikes}/{limit})</b>\n\n"
                        f"{user_mention}, tu mensaje contenía términos prohibidos y ha sido purgado.\n"
                        f"Al acumular {limit} advertencias recibirás una sanción automática.\n\n"
                        f"🇺🇸 <i>Prohibited keywords purged. Reaching {limit} strikes triggers automated expulsion or mute.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )

                try:
                    warning_msg = await event.bot.send_message(
                        chat_id=chat_id,
                        text=warning_text,
                        parse_mode="HTML"
                    )

                    async def auto_delete_notice(msg: Message):
                        await asyncio.sleep(20)
                        try:
                            await msg.delete()
                        except Exception:
                            pass

                    del_task = asyncio.create_task(auto_delete_notice(warning_msg))
                    _BG_TASKS.add(del_task)
                    del_task.add_done_callback(_BG_TASKS.discard)
                except Exception:
                    pass

                return

        return await handler(event, data)