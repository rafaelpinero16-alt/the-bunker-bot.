import asyncio
import logging
import random
import os
import time
from datetime import datetime
from pyrogram import Client
from pyrogram.enums import ChatMembersFilter, ChatType
from pyrogram.errors import (
    FloodWait, RPCError, Unauthorized,
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    PhoneNumberInvalid, PasswordHashInvalid
)
from pyrogram.raw.types import PeerUser, InputPeerUser, InputGroupCall, DataJSON
from pyrogram.raw.functions.channels import GetFullChannel
from pyrogram.raw.functions.messages import GetFullChat
from pyrogram.raw.functions.phone import (
    EditGroupCallParticipant,
    GetGroupParticipants,
    JoinGroupCall,
    CreateGroupCall,
    DiscardGroupCall
)
from database.database import (
    is_vip_mic_active, is_group_approved, 
    get_autolower_status, is_whitelisted,
    get_all_active_sessions, get_session_by_group,
    get_all_active_vc_schedules, update_vc_call_status
)

logger = logging.getLogger("assistant_radar")

# Credenciales maestras de respaldo
DEFAULT_API_ID = 37074591
DEFAULT_API_HASH = "66c86c8b4f08a0c142749b204f673d81"

MASTER_SESSION = os.getenv("MASTER_SESSION", "").strip()

# Centinela global: si existe MASTER_SESSION corre en memoria; si no, usa archivo local
if MASTER_SESSION:
    assistant_app = Client(
        "assistant_session",
        session_string=MASTER_SESSION,
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )
else:
    assistant_app = Client("assistant_session", api_id=DEFAULT_API_ID, api_hash=DEFAULT_API_HASH)

_global_bot = None
_default_my_id = None

# Pool de Centinelas activos: {group_id: {"client": Client, "task": Task, "user_id": int}}
active_sentinels = {}
admin_caches = {}  
ADMIN_CACHE_TTL = 300

# Buffer de autenticación en memoria: {user_id: {"client": Client, "phone": str, "phone_code_hash": str, "group_id": int, "ts": float}}
pending_auth_sessions = {}

# ==========================================
# 🌐 DICCIONARIO BILINGÜE DEL VIDEOCHAT
# ==========================================
RADAR_TEXTS = {
    "combined": (
        "🔇 <b>The Bunker Bot: Atenuación Acústica Activa (AutoLower)</b>\n\n"
        "El volumen de <b>{user_name}</b> ha sido reducido automáticamente al <b>2%</b> por no contar con un pase de voz o Modo Free activo en la sala.\n\n"
        "💡 <b>¿Quieres hablar sin restricciones?</b>\n"
        "Para subir tu volumen al 100% y hablar libremente durante 24 horas continuas en la transmisión, adquiere tu <b>Pase VIP de Micrófono</b> usando el comando <code>/micvip</code> en este chat.\n\n"
        "🇺🇸 <b>AutoLower Acoustic Shield Active:</b>\n"
        "<i>{user_name}’s mic volume got dialed down to <b>2%</b> because you're rolling without an active voice pass. Want to speak freely for 24 hours? Grab your <b>VIP Mic Pass</b> typing <code>/micvip</code> right here!</i>\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "es": (
        "🔇 <b>The Bunker Bot: Atenuación Acústica Activa (AutoLower)</b>\n\n"
        "El volumen de <b>{user_name}</b> ha sido reducido automáticamente al <b>2%</b> por no contar con pase VIP ni autorización en la sala.\n\n"
        "💡 <b>¿Quieres hablar sin restricciones?</b>\n"
        "Para subir tu volumen al 100% y hablar libremente durante 24 horas continuas, adquiere tu <b>Pase VIP de Micrófono</b> usando el comando <code>/micvip</code>.\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "en": (
        "🔇 <b>The Bunker Bot: AutoLower Acoustic Shield Active</b>\n\n"
        "<b>{user_name}</b>’s mic volume got automatically dialed down to <b>2%</b> because you're rolling without an active voice pass in the live stream.\n\n"
        "💡 <b>Want to speak freely?</b>\n"
        "To boost your volume straight back up to 100% for a full 24 hours, grab your <b>VIP Mic Pass</b> typing <code>/micvip</code>!\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
}

OPTIMIZATION_TEXT = (
    "🔄 <b>Protocolo de Optimización Audiovisual — The Bunker</b>\n\n"
    "Estamos realizando una optimización de rutina en segundo plano para refrescar cámaras, purgar la transmisión y garantizar máxima fluidez sin retrasos.\n\n"
    "⚡ <i>La sala se reiniciará en 3 segundos y se abrirá limpia de inmediato. Los pases VIP y Modo Free se mantendrán activos al reconectarse.</i>\n\n"
    "🇺🇸 <i>Giving the live stream a quick background refresh to clear video lag and keep camera feeds smooth. Reopening fresh in 3 seconds! VIP passes stay active.</i>\n\n"
    "🛡️ <i>Cloud Media Management</i>"
)

VC_SCHED_MESSAGES = {
    "start": (
        "📡 <b>Apertura Programada — The Bunker</b>\n\n"
        "El videochat de la comunidad ha sido abierto automáticamente según el cronograma ULTRA PRO.\n\n"
        "🇺🇸 <i>The community voice chat has automatically kicked off according to the ULTRA PRO schedule!</i>\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    ),
    "end": (
        "📡 <b>Cierre Programado — The Bunker</b>\n\n"
        "El ciclo programado de videochat ha concluido. La sala ha sido cerrada de forma ordenada.\n\n"
        "🇺🇸 <i>The scheduled voice chat session has wrapped up. The room has been closed out smoothly.</i>\n\n"
        "🛡️ <i>Cloud Media Management</i>"
    )
}


# ==============================================================================
# 🔐 MOTOR DE AUTENTICACIÓN NATIVA (PHONE LOGIN SIN STRINGSESSION MANUAL)
# ==============================================================================

async def start_phone_auth(user_id: int, group_id: int, phone_number: str) -> dict:
    """
    Paso 1: Inicia la conexión con Pyrogram en memoria y solicita el código oficial de Telegram.
    """
    await cancel_phone_auth(user_id)
    
    clean_phone = phone_number.replace(" ", "").replace("-", "").strip()
    if not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    client = Client(
        f"auth_temp_{user_id}_{group_id}",
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )

    try:
        await client.connect()
        sent_code = await client.send_code(clean_phone)
        pending_auth_sessions[user_id] = {
            "client": client,
            "phone": clean_phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "group_id": group_id,
            "ts": time.time()
        }
        return {"status": "ok", "phone": clean_phone}
    except PhoneNumberInvalid:
        if client.is_connected:
            await client.disconnect()
        return {"status": "error", "message": "invalid_phone"}
    except FloodWait as fw:
        if client.is_connected:
            await client.disconnect()
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        if client.is_connected:
            await client.disconnect()
        logger.error(f"Error al enviar código a {clean_phone}: {e}")
        return {"status": "error", "message": str(e)}


async def verify_phone_code(user_id: int, code: str) -> dict:
    """
    Paso 2: Valida el código de 5 dígitos ingresado por el usuario.
    Si la cuenta tiene contraseña en la nube, solicita el 2FA.
    """
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]
    clean_code = code.strip().replace(" ", "").replace("-", "")

    try:
        await client.sign_in(
            phone_number=auth_data["phone"],
            phone_code_hash=auth_data["phone_code_hash"],
            phone_code=clean_code
        )
        session_str = await client.export_session_string()
        group_id = auth_data["group_id"]
        
        await cancel_phone_auth(user_id)
        return {"status": "success", "session_string": session_str, "group_id": group_id}

    except SessionPasswordNeeded:
        return {"status": "2fa_required"}
    except (PhoneCodeInvalid, PhoneCodeExpired):
        return {"status": "error", "message": "invalid_code"}
    except FloodWait as fw:
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        logger.error(f"Error al verificar código para {user_id}: {e}")
        return {"status": "error", "message": str(e)}


async def verify_2fa_password(user_id: int, password: str) -> dict:
    """
    Paso 3: Valida la contraseña de Verificación en Dos Pasos (2FA) si está configurada.
    """
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]

    try:
        await client.check_password(password=password.strip())
        session_str = await client.export_session_string()
        group_id = auth_data["group_id"]

        await cancel_phone_auth(user_id)
        return {"status": "success", "session_string": session_str, "group_id": group_id}

    except PasswordHashInvalid:
        return {"status": "error", "message": "invalid_password"}
    except FloodWait as fw:
        return {"status": "error", "message": f"flood_wait_{fw.value}"}
    except Exception as e:
        logger.error(f"Error al validar contraseña 2FA para {user_id}: {e}")
        return {"status": "error", "message": str(e)}


async def cancel_phone_auth(user_id: int):
    """
    Limpia de forma segura los clientes temporales de login y libera memoria.
    """
    if user_id in pending_auth_sessions:
        auth_data = pending_auth_sessions.pop(user_id)
        client: Client = auth_data.get("client")
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass


# ==============================================================================
# 📡 RADAR Y GESTIÓN DE LLAMADAS
# ==============================================================================

async def _refresh_admin_cache(client: Client, chat_id: int, bot_client_id: int):
    """Refresca la caché de administradores reconociendo 'owner', 'creator' y 'administrator'."""
    try:
        if not await is_group_approved(chat_id):
            admin_caches[chat_id] = {'admins': {bot_client_id} if bot_client_id else set(), 'ts': asyncio.get_event_loop().time()}
            return

        new_admins = set()
        async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
            status_val = str(getattr(member.status, "value", member.status)).lower()
            if status_val in ["creator", "owner", "administrator"] and member.user and not member.user.is_bot:
                new_admins.add(member.user.id)
                
        if bot_client_id:
            new_admins.add(bot_client_id)
            
        admin_caches[chat_id] = {'admins': new_admins, 'ts': asyncio.get_event_loop().time()}
    except Exception as e:
        logger.debug(f"Aviso actualizando admin cache en chat {chat_id}: {e}")


async def monitor_single_group(chat_id: int, peer, client: Client, bot_client_id: int):
    """Bucle de radar aislado con optimización preventiva cada 3.5h y moderación acústica inteligente."""
    alerted_users = set()
    current_call = None
    last_channel_check = 0
    is_joined_audio = False
    permission_warned = False
    call_start_time = 0

    while True:
        if not client.is_connected:
            await asyncio.sleep(5)
            continue

        try:
            current_time = asyncio.get_event_loop().time()
            cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': 0})
            
            if current_time - cache_info['ts'] > ADMIN_CACHE_TTL:
                await _refresh_admin_cache(client, chat_id, bot_client_id)
                cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': current_time})

            if not current_call or (current_time - last_channel_check > 45):
                try:
                    full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                    raw_call = full_chat_res.full_chat.call
                except Exception:
                    try:
                        full_chat_res = await client.invoke(GetFullChat(chat_id=peer.chat_id))
                        raw_call = full_chat_res.full_chat.call
                    except Exception:
                        raw_call = None

                if raw_call:
                    new_call_id = raw_call.id
                    if not current_call or current_call.id != new_call_id:
                        call_start_time = asyncio.get_event_loop().time()
                    current_call = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
                    permission_warned = False
                else:
                    current_call = None
                    is_joined_audio = False
                    call_start_time = 0

                last_channel_check = current_time

            # 🛡️ MANTENIMIENTO PREVENTIVO (3.5 horas / 12600 segundos)
            if current_call and call_start_time > 0:
                if (asyncio.get_event_loop().time() - call_start_time) >= 12600:
                    logger.info(f"🔄 [Optimización Audiovisual] Reinicio preventivo en grupo {chat_id} (Transmisión > 3.5h).")
                    if _global_bot:
                        try:
                            notice = await _global_bot.send_message(
                                chat_id=chat_id,
                                text=OPTIMIZATION_TEXT,
                                parse_mode="HTML"
                            )
                            async def auto_del_watchdog(m):
                                await asyncio.sleep(20)
                                try:
                                    await m.delete()
                                except Exception:
                                    pass
                            asyncio.create_task(auto_del_watchdog(notice))
                        except Exception:
                            pass

                    try:
                        await client.invoke(DiscardGroupCall(call=current_call))
                    except Exception as disc_err:
                        logger.warning(f"Aviso al cerrar llamada previa: {disc_err}")

                    await asyncio.sleep(3.0)

                    try:
                        await client.invoke(CreateGroupCall(peer=peer, random_id=random.randint(100000, 999999)))
                    except Exception as create_err:
                        logger.warning(f"Aviso al reiniciar llamada: {create_err}")

                    current_call = None
                    is_joined_audio = False
                    call_start_time = 0
                    continue

            if current_call:
                autolower_enabled = await get_autolower_status(chat_id)
                if autolower_enabled != 1:
                    await asyncio.sleep(10)
                    continue

                if not is_joined_audio:
                    try:
                        my_peer = await client.resolve_peer(bot_client_id)
                        await client.invoke(
                            JoinGroupCall(call=current_call, join_as=my_peer, muted=True, video_stopped=True, params=DataJSON(data="{}"))
                        )
                        is_joined_audio = True
                    except Exception as join_err:
                        err_text = str(join_err).upper()
                        if "ALREADY_PARTICIPATED" in err_text or "DUPLICATE" in err_text:
                            is_joined_audio = True
                        elif "GROUPCALL_FORBIDDEN" in err_text:
                            await asyncio.sleep(25)
                            continue

                res = await client.invoke(
                    GetGroupParticipants(call=current_call, ids=[], sources=[], offset="", limit=100)
                )
                
                participants = res.participants
                users_map = {u.id: u for u in res.users} if getattr(res, 'users', None) else {}
                active_users = set()

                for p in participants:
                    if getattr(p, "left", False):
                        continue
                    peer_user = getattr(p, "peer", None)
                    if not isinstance(peer_user, PeerUser):
                        continue

                    u_id = peer_user.user_id
                    active_users.add(u_id)

                    # 🛡️ BYPASS ABSOLUTO: Creadores, Administradores, Whitelist y Pases VIP (Modo Free)
                    if u_id == bot_client_id or u_id in cache_info['admins']:
                        continue
                    if await is_whitelisted(u_id):
                        continue
                    if await is_vip_mic_active(u_id, chat_id):
                        continue

                    vol = p.volume if getattr(p, "volume", None) is not None else 10000
                    is_muted = getattr(p, "muted", True)

                    # Si el usuario no autorizado intenta hablar o sube su volumen, atenuar al 2%
                    if (not is_muted) or vol > 200:
                        user_obj = users_map.get(u_id)
                        try:
                            if user_obj and getattr(user_obj, "access_hash", None):
                                p_peer = InputPeerUser(user_id=u_id, access_hash=user_obj.access_hash)
                            else:
                                p_peer = await client.resolve_peer(u_id)

                            await client.invoke(
                                EditGroupCallParticipant(call=current_call, participant=p_peer, muted=True, volume=200)
                            )
                        except Exception as e:
                            err_msg = str(e).upper()
                            if "GROUPCALL_FORBIDDEN" in err_msg:
                                if not permission_warned:
                                    permission_warned = True
                                await asyncio.sleep(25)
                                break
                            elif "GROUPCALL_INVALID" in err_msg or "CALL_ALREADY_ENDED" in err_msg:
                                current_call = None
                                is_joined_audio = False
                                break
                            continue

                        if u_id not in alerted_users:
                            alerted_users.add(u_id)
                            user_name = f"@{user_obj.username}" if (user_obj and getattr(user_obj, "username", None)) else f"ID {u_id}"
                            if _global_bot:
                                try:
                                    sent_msg = await _global_bot.send_message(
                                        chat_id=chat_id, 
                                        text=RADAR_TEXTS["combined"].format(user_name=user_name), 
                                        parse_mode="HTML"
                                    )
                                    async def auto_delete_notice(m):
                                        await asyncio.sleep(40)
                                        try:
                                            await m.delete()
                                        except Exception:
                                            pass
                                    asyncio.create_task(auto_delete_notice(sent_msg))
                                except Exception:
                                    pass

                alerted_users.intersection_update(active_users)

        except FloodWait as fw:
            await asyncio.sleep(fw.value + 2)
        except Exception as e:
            err_str = str(e).upper()
            if "GROUPCALL_INVALID" in err_str or "CALL_ALREADY_ENDED" in err_str:
                current_call = None
                is_joined_audio = False
            await asyncio.sleep(5)

        await asyncio.sleep(3)


async def vc_scheduler_loop():
    """Bucle de fondo para apertura y cierre programado de videochats (Ultra Pro)."""
    logger.info("🗓️ [Programador VC] Sistema de programación semanal iniciado.")
    while True:
        try:
            now = datetime.now()
            current_day_str = str(now.isoweekday())
            current_time_str = now.strftime("%H:%M")

            schedules = await get_all_active_vc_schedules()
            for row in schedules:
                group_id, days_allowed, start_time, end_time, status, call_active = row[0], row[1], row[2], row[3], row[4], row[5]
                
                allowed_days_list = [d.strip() for d in days_allowed.split(",")]
                if current_day_str not in allowed_days_list:
                    continue

                sentinel_data = active_sentinels.get(group_id)
                client = sentinel_data["client"] if sentinel_data else assistant_app
                if not client or not client.is_connected:
                    continue

                try:
                    peer = await client.resolve_peer(group_id)
                except Exception:
                    continue

                if current_time_str == start_time and call_active == 0:
                    try:
                        await client.invoke(CreateGroupCall(peer=peer, random_id=random.randint(100000, 999999)))
                        await update_vc_call_status(group_id, 1)
                        logger.info(f"📡 [Programador VC] Videochat abierto automáticamente en grupo {group_id}")
                        if _global_bot:
                            await _global_bot.send_message(
                                chat_id=group_id,
                                text=VC_SCHED_MESSAGES["start"],
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.error(f"Error abriendo videochat en grupo {group_id}: {e}")

                elif current_time_str == end_time and call_active == 1:
                    try:
                        full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                        raw_call = full_chat_res.full_chat.call
                        if raw_call:
                            call_obj = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
                            await client.invoke(DiscardGroupCall(call=call_obj))
                        await update_vc_call_status(group_id, 0)
                        logger.info(f"📡 [Programador VC] Videochat cerrado automáticamente en grupo {group_id}")
                        if _global_bot:
                            await _global_bot.send_message(
                                chat_id=group_id,
                                text=VC_SCHED_MESSAGES["end"],
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.error(f"Error cerrando videochat en grupo {group_id}: {e}")

        except Exception as e:
            logger.error(f"Error en bucle de programación VC: {e}")

        await asyncio.sleep(50)


async def launch_sentinel_instance(user_id: int, group_id: int, session_string: str, api_id: int = None, api_hash: str = None):
    """Inicia un cliente de Pyrogram dedicado para una comunidad específica."""
    client_api_id = api_id if api_id else DEFAULT_API_ID
    client_api_hash = api_hash if api_hash else DEFAULT_API_HASH
    
    session_client = Client(
        f"sentinel_{user_id}_{group_id}",
        session_string=session_string,
        api_id=client_api_id,
        api_hash=client_api_hash,
        in_memory=True
    )
    
    try:
        await session_client.start()
        me = await session_client.get_me()
        peer = await session_client.resolve_peer(group_id)
        
        task = asyncio.create_task(monitor_single_group(group_id, peer, session_client, me.id))
        active_sentinels[group_id] = {
            "client": session_client,
            "task": task,
            "user_id": user_id
        }
        logger.info(f"💎 [Centinela Propio Conectado] Comunidad {group_id} protegida por @{me.username or me.id}")
        return True
    except Unauthorized:
        logger.warning(f"⚠️ [Error de Sesión] La sesión del usuario {user_id} para el grupo {group_id} fue revocada.")
        return False
    except Exception as e:
        logger.error(f"⚠️ [Error al iniciar Centinela Propio] Grupo {group_id}: {e}")
        return False


async def register_or_update_sentinel(user_id: int, group_id: int, session_string: str, api_id: int = None, api_hash: str = None):
    """Permite conectar o sustituir un Centinela en tiempo real desde el bot privado."""
    await disconnect_sentinel(group_id)
    return await launch_sentinel_instance(user_id, group_id, session_string, api_id, api_hash)


async def disconnect_sentinel(group_id: int):
    """Detiene y desconecta de forma limpia un Centinela asignado protegiendo al Maestro."""
    if group_id in active_sentinels:
        sentinel_info = active_sentinels.pop(group_id)
        try:
            sentinel_info["task"].cancel()
        except Exception:
            pass
        try:
            client = sentinel_info["client"]
            if client != assistant_app and client.is_connected:
                await client.stop()
        except Exception:
            pass
        logger.info(f"🛑 [Centinela Desconectado] Grupo {group_id} liberado.")


async def load_all_sentinels():
    """Lee de la base de datos todas las sesiones activas y arranca sus clientes."""
    sessions = await get_all_active_sessions()
    for row in sessions:
        u_id, g_id, s_str, a_id, a_hash = row[0], row[1], row[2], row[3], row[4]
        if g_id not in active_sentinels:
            await launch_sentinel_instance(u_id, g_id, s_str, a_id, a_hash)


async def radar_master_loop():
    """Supervisa el centinela por defecto en las comunidades que no tienen centinela propio."""
    while True:
        try:
            if assistant_app.is_connected:
                async for dialog in assistant_app.get_dialogs(limit=100):
                    chat = dialog.chat
                    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                        chat_id = chat.id
                        if chat_id not in active_sentinels:
                            try:
                                peer = await assistant_app.resolve_peer(chat_id)
                                task = asyncio.create_task(monitor_single_group(chat_id, peer, assistant_app, _default_my_id))
                                active_sentinels[chat_id] = {
                                    "client": assistant_app,
                                    "task": task,
                                    "user_id": 0
                                }
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Aviso en radar master loop: {e}")
            
        await asyncio.sleep(45)


async def init_assistant_master():
    """Punto de arranque que levanta el centinela maestro, el programador y el pool multi-sesión."""
    global _default_my_id
    try:
        if not assistant_app.is_connected:
            await assistant_app.start()
        me = await assistant_app.get_me()
        _default_my_id = me.id
        logger.info(f"🤖 [Centinela Maestro Activo] Online como: @{me.username or me.first_name}")
    except Exception as e:
        logger.warning(f"⚠️ [Aviso Centinela Maestro]: {e}")

    await load_all_sentinels()
    asyncio.create_task(radar_master_loop())
    asyncio.create_task(vc_scheduler_loop())


def start_voice_radar(bot):
    """Punto de entrada llamado desde main.py."""
    global _global_bot
    _global_bot = bot
    asyncio.create_task(init_assistant_master())


async def close_all_sentinels():
    """Detiene ordenadamente todos los clientes activos al apagar el servidor."""
    for group_id in list(active_sentinels.keys()):
        await disconnect_sentinel(group_id)
    if assistant_app.is_connected:
        try:
            await assistant_app.stop()
        except Exception:
            pass


async def set_participant_mic(chat_id: int, user_id: int, muted: bool, volume: int = 10000) -> bool:
    """Restaura o silencia participantes usando el Centinela asignado a la comunidad."""
    sentinel_data = active_sentinels.get(chat_id)
    client = sentinel_data["client"] if sentinel_data else assistant_app

    if not client or not client.is_connected:
        return False

    try:
        peer = await client.resolve_peer(chat_id)
        try:
            full_chat_res = await client.invoke(GetFullChannel(channel=peer))
        except Exception:
            full_chat_res = await client.invoke(GetFullChat(chat_id=peer.chat_id))
            
        raw_call = full_chat_res.full_chat.call
        if not raw_call:
            return False

        call = InputGroupCall(id=raw_call.id, access_hash=raw_call.access_hash)
        participant_peer = await client.resolve_peer(user_id)
        await client.invoke(
            EditGroupCallParticipant(call=call, participant=participant_peer, muted=muted, volume=volume)
        )
        return True
    except Exception as e:
        logger.warning(f"Aviso en set_participant_mic para grupo {chat_id}: {e}")
        return False