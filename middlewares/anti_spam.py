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
    """Verifica si el emisor cuenta con inmunidad táctica (Whitelist, Creador, Admin o Centinela)."""
    if not event.from_user:
        return False

    user_id = event.from_user.id
    if await is_whitelisted(user_id):
        return True

    username = (event.from_user.username or "").lower()
    if username == "alphacentinel":
        return True

    if event.chat.type in ("group", "supergroup"):
        chat_id = event.chat.id
        
        # 1. Validar si corresponde a un Centinela Dedicado activo en memoria
        sentinel_info = active_sentinels.get(chat_id)
        if sentinel_info and sentinel_info.get("user_id") == user_id:
            return True

        # 2. Validar sesión registrada en la base de datos
        session_row = await get_session_by_group(chat_id)
        if session_row and session_row[0] == user_id:
            return True

        # 3. Validar facultades de administración o Creador en el grupo
        try:
            member = await event.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ["creator", "administrator"]:
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
        # 1. Permitir flujo inmediato si no hay usuario o si ocurre en chat privado (Clones / Maestro en DM)
        if not event.from_user or (event.chat and event.chat.type == "private"):
            return await handler(event, data)

        # 2. 🛡️ Inmunidad Táctica para Administradores, Aliados y Centinelas en Grupos
        if await is_immune(event):
            return await handler(event, data)

        user_id = event.from_user.id

        # 🔍 Verificación preventiva de bloqueo global en base de datos
        try:
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
        except Exception as db_err:
            logger.debug(f"Aviso middleware al verificar estado del usuario {user_id}: {db_err}")

        # 🚨 Inspección y purga de términos prohibidos de la Blacklist
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
                    logger.warning(f"Aviso al purgar mensaje prohibido en grupo {event.chat.id}: {e}")

                warnings = await add_warning(user_id)
                chat_id = event.chat.id
                is_group = event.chat.type in ("group", "supergroup")

                # Obtener configuración de faltas perimetrales del grupo
                warns_cfg = await get_warns_config(chat_id) if is_group else {"limit": 3, "action": "mute"}
                limit = warns_cfg["limit"]
                action = warns_cfg["action"]

                # Mención táctica directa en HTML para etiquetar al infractor
                user_mention = event.from_user.mention_html()

                # Umbral de faltas alcanzado: Ejecución de sanción perimetral
                if warnings >= limit:
                    action_desc = "sancionado / sanctioned"
                    if is_group:
                        try:
                            # 🎙️ Sincronización acústica: apagar micrófono en sala de voz activa
                            try:
                                await set_participant_mic(chat_id=chat_id, user_id=user_id, muted=True, volume=0)
                            except Exception as mic_err:
                                logger.warning(f"Aviso Centinela al silenciar por Blacklist: {mic_err}")

                            if action == "ban":
                                await ban_user(user_id)
                                await event.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                                action_desc = "bloqueado permanentemente del grupo / permanently banned"
                            elif action == "kick":
                                await event.bot.ban_chat_member(
                                    chat_id=chat_id, 
                                    user_id=user_id, 
                                    until_date=int(time.time() + 35)
                                )
                                await event.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                                action_desc = "expulsado temporalmente de la comunidad / kicked"
                            elif action == "mute":
                                await event.bot.restrict_chat_member(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    permissions=ChatPermissions(can_send_messages=False)
                                )
                                action_desc = "silenciado en chat y transmisión de voz / muted"
                        except Exception as e:
                            logger.error(f"Error al aplicar sanción de Blacklist: {e}")

                    warning_text = (
                        f"🚫 <b>Protocolo de Sanción Ejecutado / Enforcement Active</b>\n\n"
                        f"{user_mention} ha sido {action_desc} tras acumular "
                        f"<b>{warnings}/{limit}</b> advertencias por contenido prohibido.\n\n"
                        f"🇺🇸 <i>Strike limit reached. Automated perimeter sanctions applied across chat and audio streams.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                else:
                    warning_text = (
                        f"⚠️ <b>Advertencia Táctica / Security Strike ({warnings}/{limit})</b>\n\n"
                        f"{user_mention}, tu mensaje contenía términos prohibidos y ha sido purgado. "
                        f"Al acumular {limit} advertencias recibirás una sanción automática.\n\n"
                        f"🇺🇸 <i>Prohibited keywords purged. Reaching {limit} strikes triggers automated expulsion or mute.</i>\n\n"
                        f"🛡️ <i>Cloud Media Management</i>"
                    )
                
                try:
                    warning_msg = await event.answer(warning_text, parse_mode="HTML")
                    
                    async def auto_delete_notice(msg):
                        await asyncio.sleep(20)
                        try:
                            await msg.delete()
                        except Exception:
                            pass

                    asyncio.create_task(auto_delete_notice(warning_msg))
                except Exception:
                    pass

                return

        return await handler(event, data)