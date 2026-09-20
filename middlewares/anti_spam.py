import asyncio
import time
import re
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, ChatPermissions
from database.database import (
    get_blacklist, 
    get_or_create_user, 
    add_warning, 
    ban_user, 
    is_whitelisted,
    get_warns_config,
    get_session_by_group
)
from assistant import set_participant_mic, active_sentinels

logger = logging.getLogger("anti_spam_middleware")


async def is_immune(event: Message) -> bool:
    """Verifica si el emisor cuenta con inmunidad absoluta (Whitelist, Creador, Admin o Centinela)."""
    user_id = event.from_user.id
    if await is_whitelisted(user_id):
        return True

    username = (event.from_user.username or "").lower()
    if username == "alphacentinel":
        return True

    if event.chat.type in ("group", "supergroup"):
        chat_id = event.chat.id
        
        # Validar si corresponde a un Centinela Dedicado activo en memoria
        sentinel_info = active_sentinels.get(chat_id)
        if sentinel_info and sentinel_info.get("user_id") == user_id:
            return True

        # Validar sesión registrada en la base de datos
        session_row = await get_session_by_group(chat_id)
        if session_row and session_row[0] == user_id:
            return True

        # Validar jerarquía de administrador o creador en Telegram
        try:
            member = await event.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ["creator", "administrator"]:
                return True
        except Exception:
            pass

    return False


def matches_blacklisted_term(word: str, text: str) -> bool:
    """Verifica si un término prohibido está presente evitando falsos positivos en subcadenas cortas."""
    clean_word = word.strip().lower()
    if not clean_word:
        return False

    # Para términos cortos alfanuméricos (ej. 'cp', 'kill', 'gore'), usar límite de palabras
    if clean_word.isalnum() and len(clean_word) <= 4:
        pattern = r'\b' + re.escape(clean_word) + r'\b'
        return bool(re.search(pattern, text))
    
    return clean_word in text


class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        # Si el evento no proviene de un usuario real, permitir el flujo
        if not event.from_user:
            return await handler(event, data)

        # 🛡️ Inmunidad Táctica de Administradores, Aliados y Centinelas
        if await is_immune(event):
            return await handler(event, data)

        user_id = event.from_user.id

        # 🔍 Verificación de erradicación previa (Baneado global)
        user_data = await get_or_create_user(
            user_id=user_id, 
            username=event.from_user.username or "", 
            full_name=event.from_user.full_name
        )
        is_banned = user_data[2] if user_data else 0
        
        if is_banned:
            try:
                await event.delete()
            except Exception:
                pass
            return

        # 🚨 Detección y purga de contenido prohibido en la Blacklist
        text_content = (event.text or event.caption or "").lower()
        if text_content:
            blacklist = await get_blacklist()
            threat_found = False

            for b_word in blacklist:
                if matches_blacklisted_term(b_word, text_content):
                    threat_found = True
                    break

            if threat_found:
                try:
                    await event.delete()
                except Exception as e:
                    logger.warning(f"Error al purgar mensaje prohibido en grupo {event.chat.id}: {e}")

                warnings = await add_warning(user_id)
                chat_id = event.chat.id
                is_group = event.chat.type in ("group", "supergroup")

                # Consulta de parámetros de advertencias configurados en la comunidad
                warns_cfg = await get_warns_config(chat_id) if is_group else {"limit": 3, "action": "mute"}
                limit = warns_cfg["limit"]
                action = warns_cfg["action"]

                # Umbral de faltas alcanzado: Ejecución de la sanción perimetral
                if warnings >= limit:
                    if is_group:
                        try:
                            # 🎙️ Sincronización acústica con el Centinela en videochats
                            try:
                                await set_participant_mic(chat_id=chat_id, user_id=user_id, muted=True, volume=0)
                            except Exception as mic_err:
                                logger.warning(f"Aviso Centinela al silenciar por Blacklist: {mic_err}")

                            if action == "ban":
                                await ban_user(user_id)
                                await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                                action_desc = "erradicado permanentemente / permanently banned"
                            elif action == "kick":
                                await event.bot.ban_chat_member(
                                    chat_id=chat_id, 
                                    user_id=user_id, 
                                    until_date=int(time.time() + 35)
                                )
                                await event.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                                action_desc = "expulsado temporalmente / kicked"
                            elif action == "mute":
                                await event.bot.restrict_chat_member(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    permissions=ChatPermissions(can_send_messages=False)
                                )
                                action_desc = "silenciado en chat y voz / muted"
                            else:
                                action_desc = "sancionado / sanctioned"
                        except Exception as e:
                            logger.error(f"Error al aplicar castigo de Blacklist: {e}")
                            action_desc = "sancionado / sanctioned"
                    else:
                        action_desc = "registrado / logged"

                    warning_text = (
                        f"🚫 <b>Protocolo de Sanción Ejecutado / Enforcement Active</b>\n\n"
                        f"El usuario <b>{event.from_user.full_name}</b> ha sido {action_desc} "
                        f"por acumular <b>{warnings}/{limit}</b> advertencias tácticas.\n\n"
                        f"🇺🇸 <i>Strike limit reached. Automated perimeter sanctions applied across chat and audio streams.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                else:
                    warning_text = (
                        f"⚠️ <b>Advertencia Táctica / Security Strike ({warnings}/{limit})</b>\n\n"
                        f"<b>{event.from_user.full_name}</b>, se ha detectado y purgado contenido prohibido en tu mensaje. "
                        f"Al acumular {limit} advertencias serás sancionado automáticamente.\n\n"
                        f"🇺🇸 <i>Prohibited keywords detected and purged. Reaching {limit} strikes triggers automated expulsion or voice mute.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                
                try:
                    warning_msg = await event.answer(warning_text, parse_mode="HTML")
                except Exception:
                    return

                # Purga automática de la advertencia tras 25 segundos para evitar saturación
                async def delayed_delete(msg):
                    await asyncio.sleep(25)
                    try:
                        await msg.delete()
                    except Exception:
                        pass

                asyncio.create_task(delayed_delete(warning_msg))
                return

        return await handler(event, data)