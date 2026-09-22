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
    PhoneNumberInvalid, PasswordHashInvalid, PeerIdInvalid,
    AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan
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
    get_all_active_vc_schedules, update_vc_call_status,
    get_radar_config, revoke_owner_session
)

# Excepciones que indican que una StringSession ya no es válida en Telegram
# (revocada desde otro dispositivo, cuenta desactivada/baneada, etc.). Se agrupan
# aquí porque en TODOS los puntos donde se detectan se aplica la misma receta:
# purgar la sesión en BD (revoke_owner_session) y dejar de reintentar en bucle.
FATAL_SESSION_ERRORS = (Unauthorized, AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan)

logger = logging.getLogger("assistant_radar")

# Credenciales maestras de respaldo
DEFAULT_API_ID = 37074591
DEFAULT_API_HASH = "66c86c8b4f08a0c142749b204f673d81"

MASTER_SESSION = os.getenv("MASTER_SESSION", "").strip()

# ==============================================================================
# 🔐 CENTINELA MAESTRO: EXCLUSIVAMENTE StringSession EN MEMORIA (SIN ARCHIVOS)
# ==============================================================================
# Railway (y cualquier plataforma de contenedores efímeros) NO garantiza disco
# persistente entre despliegues/réplicas. Depender de un archivo `.session`
# local (SQLite) es la causa raíz típica de "conflictos de sesión": dos
# procesos abriendo el mismo archivo, o un archivo huérfano de un despliegue
# anterior que ya no coincide con las claves vigentes en Telegram. Por eso el
# Centinela Maestro NUNCA cae a un Client con sesión en disco: si no hay
# MASTER_SESSION configurado, simplemente permanece inactivo (in_memory=True
# siempre, `session_string=None` no crea nada en disco). Los Centinelas
# propios por comunidad (owner_sessions en BD) no se ven afectados por esto.
if MASTER_SESSION:
    assistant_app = Client(
        "assistant_session",
        session_string=MASTER_SESSION,
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )
else:
    assistant_app = None
    logger.warning(
        "⚠️ [MASTER_SESSION no configurado] El Centinela Maestro global quedará inactivo "
        "(no se usará ningún archivo .session local como respaldo). Los Centinelas propios "
        "por comunidad (sesiones guardadas en BD) siguen funcionando con normalidad."
    )

_global_bot = None
_default_my_id = None

# Pool de Centinelas activos: {group_id: {"client": Client, "task": Task, "user_id": int}}
active_sentinels = {}
admin_caches = {}  
ADMIN_CACHE_TTL = 300

# Buffer de autenticación en memoria: {user_id: {"client": Client, "phone": str, "phone_code_hash": str, "group_id": int, "ts": float}}
pending_auth_sessions = {}

# --- Circuit breaker de GROUPCALL_FORBIDDEN (Fallo #1) ---
# {group_id: int} conteo de fallos consecutivos al unirse/editar el videochat
_forbidden_strikes = {}
# {group_id: float} timestamp (loop time) hasta el cual se pausan los intentos en ese grupo
_autolower_cooldowns = {}
FORBIDDEN_STRIKE_LIMIT = 3
FORBIDDEN_COOLDOWN_SECONDS = 900  # 15 minutos de enfriamiento tras 3 fallos consecutivos

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

# ==========================================
# 🎛️ DESPACHO DINÁMICO DE AVISOS PERSONALIZABLES (Req. #2 — Matriz del Centinela)
# ==========================================
async def _dispatch_radar_notice(chat_id: int, text: str, media_id: str = None,
                                  media_type: str = None, auto_delete_after: int = None):
    """
    Envía un aviso del Centinela (AutoLower o pre-reinicio de 3.5h) respetando
    la personalización guardada en BD para ese group_id. Si la comunidad
    configuró multimedia propia (foto/video/gif), se envía como caption;
    si no, se envía como texto plano — siempre con fallback seguro si algo falla.
    """
    if not _global_bot:
        return None

    try:
        if media_id and media_type == "video":
            sent = await _global_bot.send_video(chat_id=chat_id, video=media_id, caption=text, parse_mode="HTML")
        elif media_id and media_type == "animation":
            sent = await _global_bot.send_animation(chat_id=chat_id, animation=media_id, caption=text, parse_mode="HTML")
        elif media_id and media_type == "photo":
            sent = await _global_bot.send_photo(chat_id=chat_id, photo=media_id, caption=text, parse_mode="HTML")
        else:
            sent = await _global_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Aviso al despachar notificación personalizada del Centinela en {chat_id}: {e}")
        try:
            sent = await _global_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            return None

    if auto_delete_after and sent:
        async def _watchdog(m):
            await asyncio.sleep(auto_delete_after)
            try:
                await m.delete()
            except Exception:
                pass
        asyncio.create_task(_watchdog(sent))

    return sent


def _resolve_autolower_text(custom_text: str, user_name: str) -> str:
    """Usa el texto personalizado de la comunidad si existe; si no, el default bilingüe de fábrica."""
    template = custom_text if custom_text else RADAR_TEXTS["combined"]
    try:
        return template.format(user_name=user_name)
    except (KeyError, IndexError):
        return template


def _resolve_reset_text(custom_text: str) -> str:
    """Usa el aviso previo al reinicio de 3.5h personalizado si existe; si no, el default de fábrica."""
    return custom_text if custom_text else OPTIMIZATION_TEXT


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
# 🔐 MOTOR DE AUTENTICACIÓN NATIVA (PHONE LOGIN CON RECONEXIÓN BLINDADA)
# ==============================================================================

async def start_phone_auth(user_id: int, group_id: int, phone_number: str) -> dict:
    await cancel_phone_auth(user_id)
    
    clean_phone = phone_number.replace(" ", "").replace("-", "").strip()
    if not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    # Nombre de sesión único en memoria para evitar colisiones de base de datos volátil
    client = Client(
        f"auth_temp_{user_id}_{group_id}_{int(time.time())}",
        api_id=DEFAULT_API_ID,
        api_hash=DEFAULT_API_HASH,
        in_memory=True
    )

    try:
        if not await _ensure_connected(client):
            return {"status": "error", "message": "connection_lost"}
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
            try:
                await client.disconnect()
            except Exception:
                pass
        logger.exception(f"🔥 [CRITICAL Auth Error] Falló start_phone_auth para {clean_phone}: {e}")
        return {"status": "error", "message": str(e)}


async def verify_phone_code(user_id: int, code: str) -> dict:
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]
    clean_code = code.strip().replace(" ", "").replace("-", "")

    try:
        if not await _ensure_connected(client):
            return {"status": "error", "message": "connection_lost"}
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
    auth_data = pending_auth_sessions.get(user_id)
    if not auth_data:
        return {"status": "error", "message": "session_expired"}

    client: Client = auth_data["client"]

    try:
        if not await _ensure_connected(client):
            return {"status": "error", "message": "connection_lost"}
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

def _register_forbidden_strike(chat_id: int, action: str):
    """
    Lleva la cuenta de fallos consecutivos [400 GROUPCALL_FORBIDDEN] por grupo. Al
    superar FORBIDDEN_STRIKE_LIMIT, activa un cooldown que detiene por completo los
    intentos de unirse/silenciar en ESE grupo durante FORBIDDEN_COOLDOWN_SECONDS, en
    vez de seguir invocando la RPC cada pocos segundos (causa raíz del flood de logs).
    Esto casi siempre significa que la cuenta del Centinela NO es administradora con
    el permiso "Gestionar videollamadas" habilitado en ese grupo/canal.
    """
    strikes = _forbidden_strikes.get(chat_id, 0) + 1
    if strikes >= FORBIDDEN_STRIKE_LIMIT:
        _autolower_cooldowns[chat_id] = asyncio.get_event_loop().time() + FORBIDDEN_COOLDOWN_SECONDS
        _forbidden_strikes[chat_id] = 0
        logger.warning(
            f"🚫 [GROUPCALL_FORBIDDEN] Grupo {chat_id}: la cuenta del Centinela no tiene permisos "
            f"suficientes para {action}. Verifica que sea ADMIN con 'Gestionar videollamadas' "
            f"habilitado. AutoLower pausado {FORBIDDEN_COOLDOWN_SECONDS // 60} min en este grupo."
        )
    else:
        _forbidden_strikes[chat_id] = strikes


async def _verify_active_membership(client: Client, chat_id: int) -> bool:
    """Confirma que la cuenta del Centinela sigue siendo miembro activo del grupo (no LEFT/KICKED/BANNED)."""
    try:
        member = await client.get_chat_member(chat_id, "me")
        status_val = str(getattr(member.status, "value", member.status)).lower()
        return status_val not in ("left", "banned", "kicked")
    except Exception as e:
        logger.debug(f"No se pudo verificar membresía activa en {chat_id}: {e}")
        return False


async def _ensure_connected(client: Client, retries: int = 3, delay: float = 1.5) -> bool:
    """
    Garantiza una conexión TCP viva antes de operaciones sensibles (sign_in,
    check_password). En Railway los sockets pueden caerse en cualquier momento
    del flujo de verificación; reintentar unas pocas veces con backoff corto
    evita perder el progreso de un 2FA/código por una caída transitoria.
    """
    for attempt in range(retries):
        if client.is_connected:
            return True
        try:
            await client.connect()
            return True
        except Exception as e:
            logger.debug(f"Reintento de conexión ({attempt + 1}/{retries}) falló: {e}")
            await asyncio.sleep(delay)
    return client.is_connected


async def _refresh_admin_cache(client: Client, chat_id: int, bot_client_id: int):
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


async def monitor_single_group(chat_id: int, peer, client: Client, bot_client_id: int, user_id: int = 0):
    """Bucle de radar aislado con tolerancia a PeerIdInvalid y mantenimiento preventivo."""
    alerted_users = set()
    current_call = None
    last_channel_check = 0
    is_joined_audio = False
    permission_warned = False
    call_start_time = 0

    while True:
        if not client.is_connected:
            try:
                await client.start()
                logger.info(f"🔄 [Centinela Reconectado] Sesión restablecida para el grupo {chat_id}.")
            except FATAL_SESSION_ERRORS as auth_err:
                # La sesión ya no es válida ante Telegram (revocada, cuenta desactivada/baneada, etc.).
                # Reintentar aquí sólo produciría el mismo error en bucle para siempre: se purga en BD
                # y se termina esta tarea de forma limpia en vez de seguir spameando logs.
                logger.error(f"🔒 [Sesión Inválida] Centinela del grupo {chat_id} desautorizado: {auth_err}")
                if user_id:
                    try:
                        await revoke_owner_session(user_id, chat_id, reason=str(auth_err))
                    except Exception:
                        pass
                active_sentinels.pop(chat_id, None)
                return
            except Exception as reconnect_err:
                logger.warning(f"⚠️ [Centinela Desconectado] Grupo {chat_id} sin conexión, reintentando en 5s: {reconnect_err}")
                await asyncio.sleep(5)
                continue

        try:
            current_time = asyncio.get_event_loop().time()
            cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': 0})
            
            if current_time - cache_info['ts'] > ADMIN_CACHE_TTL:
                await _refresh_admin_cache(client, chat_id, bot_client_id)
                cache_info = admin_caches.get(chat_id, {'admins': set(), 'ts': current_time})

                # 🛡️ Membresía obligatoria: si la cuenta ya no pertenece al grupo (expulsada,
                # salió manualmente, etc.), seguir monitoreando sólo produciría PeerIdInvalid
                # y GROUPCALL_FORBIDDEN en bucle. Se corta la tarea de forma limpia.
                if not await _verify_active_membership(client, chat_id):
                    logger.warning(f"🚪 [Membresía Perdida] El Centinela ya no pertenece al grupo {chat_id}. Deteniendo monitor.")
                    active_sentinels.pop(chat_id, None)
                    return

            if not current_call or (current_time - last_channel_check > 45):
                try:
                    full_chat_res = await client.invoke(GetFullChannel(channel=peer))
                    raw_call = full_chat_res.full_chat.call
                except PeerIdInvalid:
                    logger.warning(f"⚠️ [Peer ID Inválido] El chat {chat_id} no está disponible en esta sesión. Pausando monitor.")
                    await asyncio.sleep(300)
                    continue
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

            # 🛡️ MANTENIMIENTO PREVENTIVO — INAMOVIBLE: 3.5 horas / 12600 segundos.
            if current_call and call_start_time > 0:
                if (asyncio.get_event_loop().time() - call_start_time) >= 12600:
                    logger.info(f"🔄 [Optimización Audiovisual] Reinicio preventivo en grupo {chat_id} (Transmisión > 3.5h).")
                    if _global_bot:
                        try:
                            radar_cfg = await get_radar_config(chat_id)
                            reset_text = _resolve_reset_text(radar_cfg.get("reset_text"))
                            await _dispatch_radar_notice(
                                chat_id=chat_id,
                                text=reset_text,
                                media_id=radar_cfg.get("reset_media_id"),
                                media_type=radar_cfg.get("reset_media_type"),
                                auto_delete_after=20
                            )
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

                # 🛑 Circuit breaker: si este grupo viene fallando por falta de permisos,
                # no se reintenta unirse/silenciar hasta que expire el cooldown. Esto es lo
                # que corta el bucle de [400 GROUPCALL_FORBIDDEN] repetido en los logs.
                cooldown_until = _autolower_cooldowns.get(chat_id, 0)
                if current_time < cooldown_until:
                    await asyncio.sleep(15)
                    continue

                if not is_joined_audio:
                    try:
                        my_peer = await client.resolve_peer(bot_client_id)
                        await client.invoke(
                            JoinGroupCall(call=current_call, join_as=my_peer, muted=True, video_stopped=True, params=DataJSON(data="{}"))
                        )
                        is_joined_audio = True
                        _forbidden_strikes[chat_id] = 0
                    except Exception as join_err:
                        err_text = str(join_err).upper()
                        if "ALREADY_PARTICIPATED" in err_text or "DUPLICATE" in err_text:
                            is_joined_audio = True
                        elif "GROUPCALL_FORBIDDEN" in err_text:
                            _register_forbidden_strike(chat_id, "unirse al videochat (JoinGroupCall)")
                            await asyncio.sleep(25)
                            continue
                        else:
                            await asyncio.sleep(5)
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

                    if u_id == bot_client_id or u_id in cache_info['admins']:
                        continue
                    if await is_whitelisted(u_id):
                        continue
                    if await is_vip_mic_active(u_id, chat_id):
                        continue

                    vol = p.volume if getattr(p, "volume", None) is not None else 10000
                    is_muted = getattr(p, "muted", True)

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
                            _forbidden_strikes[chat_id] = 0
                        except Exception as e:
                            err_msg = str(e).upper()
                            if "GROUPCALL_FORBIDDEN" in err_msg:
                                if not permission_warned:
                                    permission_warned = True
                                _register_forbidden_strike(chat_id, "silenciar un participante (EditGroupCallParticipant)")
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
                                    radar_cfg = await get_radar_config(chat_id)
                                    autolower_text = _resolve_autolower_text(radar_cfg.get("autolower_text"), user_name)
                                    await _dispatch_radar_notice(
                                        chat_id=chat_id,
                                        text=autolower_text,
                                        media_id=radar_cfg.get("autolower_media_id"),
                                        media_type=radar_cfg.get("autolower_media_type"),
                                        auto_delete_after=40
                                    )
                                except Exception:
                                    pass

                # 🔁 Validación de unión activa: si nos creíamos unidos pero Telegram ya no nos
                # lista como participantes (call recreado, expulsión silenciosa del canal de voz,
                # etc.), forzamos un rejoin limpio en el próximo ciclo en vez de seguir invocando
                # EditGroupCallParticipant "a ciegas" sobre una llamada de la que ya no formamos parte.
                if is_joined_audio and bot_client_id not in active_users:
                    logger.info(f"🔁 [Rejoin Requerido] Centinela ausente de la llamada en {chat_id}; se reincorporará.")
                    is_joined_audio = False

                alerted_users.intersection_update(active_users)

        except FloodWait as fw:
            await asyncio.sleep(fw.value + 2)
        except PeerIdInvalid:
            logger.warning(f"⚠️ [Peer ID Inválido] El chat {chat_id} generó error de peer. Silenciando aviso y esperando reconexión.")
            await asyncio.sleep(60)
        except Exception as e:
            err_str = str(e).upper()
            if "GROUPCALL_INVALID" in err_str or "CALL_ALREADY_ENDED" in err_str:
                current_call = None
                is_joined_audio = False
            await asyncio.sleep(5)

        await asyncio.sleep(3)


async def vc_scheduler_loop():
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

        # 📥 Precarga obligatoria del chat: puebla la caché de peers de Pyrogram y es la
        # forma más confiable de detectar PeerIdInvalid de forma temprana y controlada,
        # antes de que aparezca de golpe en medio del bucle de monitoreo.
        try:
            await session_client.get_chat(group_id)
        except PeerIdInvalid:
            logger.warning(f"⚠️ [Peer ID Inválido al Precargar] El grupo {group_id} no es accesible con esta sesión.")
            await session_client.stop()
            return False
        except Exception as chat_err:
            logger.debug(f"Aviso precargando chat {group_id}: {chat_err}")

        # 👤 Membresía obligatoria: get_chat() puede tener éxito con datos cacheados por
        # Telegram incluso si la cuenta ya no pertenece al grupo. Se verifica explícitamente
        # antes de lanzar el bucle de monitoreo para no arrancar un Centinela "fantasma".
        if not await _verify_active_membership(session_client, group_id):
            logger.warning(f"⚠️ [Membresía Inválida] La cuenta ya no pertenece al grupo {group_id}. Abortando lanzamiento del Centinela.")
            await session_client.stop()
            return False

        peer = await session_client.resolve_peer(group_id)

        task = asyncio.create_task(monitor_single_group(group_id, peer, session_client, me.id, user_id))
        active_sentinels[group_id] = {
            "client": session_client,
            "task": task,
            "user_id": user_id
        }
        logger.info(f"💎 [Centinela Propio Conectado] Comunidad {group_id} protegida por @{me.username or me.id}")
        return True
    except FATAL_SESSION_ERRORS as auth_err:
        # Sesión muerta ante Telegram: se purga en BD para que load_all_sentinels() no
        # vuelva a intentarla en cada reinicio del proceso (evita el "conflicto de sesiones").
        logger.warning(f"⚠️ [Sesión Inválida] La sesión del usuario {user_id} para el grupo {group_id} fue revocada: {auth_err}")
        try:
            await revoke_owner_session(user_id, group_id, reason=str(auth_err))
        except Exception:
            pass
        return False
    except PeerIdInvalid:
        logger.warning(f"⚠️ [Peer ID Inválido al Iniciar] El grupo {group_id} no es accesible con esta sesión.")
        return False
    except Exception as e:
        logger.error(f"⚠️ [Error al iniciar Centinela Propio] Grupo {group_id}: {e}")
        return False


async def register_or_update_sentinel(user_id: int, group_id: int, session_string: str, api_id: int = None, api_hash: str = None):
    await disconnect_sentinel(group_id)
    return await launch_sentinel_instance(user_id, group_id, session_string, api_id, api_hash)


async def disconnect_sentinel(group_id: int):
    if group_id in active_sentinels:
        sentinel_info = active_sentinels.pop(group_id)
        try:
            sentinel_info["task"].cancel()
        except Exception:
            pass
        try:
            client = sentinel_info["client"]
            if client and client != assistant_app and client.is_connected:
                await client.stop()
        except Exception:
            pass
        logger.info(f"🛑 [Centinela Desconectado] Grupo {group_id} liberado.")

    # Limpieza del estado en memoria asociado a ese grupo, para no acumular
    # entradas huérfanas indefinidamente a medida que rotan sesiones/clones.
    admin_caches.pop(group_id, None)
    _forbidden_strikes.pop(group_id, None)
    _autolower_cooldowns.pop(group_id, None)


async def load_all_sentinels():
    sessions = await get_all_active_sessions()
    for row in sessions:
        u_id, g_id, s_str, a_id, a_hash = row[0], row[1], row[2], row[3], row[4]
        if g_id not in active_sentinels:
            try:
                await launch_sentinel_instance(u_id, g_id, s_str, a_id, a_hash)
            except PeerIdInvalid:
                logger.debug(f"Saltando grupo inválido {g_id} al cargar sesiones.")
            except Exception:
                pass


async def radar_master_loop():
    while True:
        try:
            if assistant_app and assistant_app.is_connected:
                async for dialog in assistant_app.get_dialogs(limit=100):
                    chat = dialog.chat
                    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                        chat_id = chat.id
                        if chat_id not in active_sentinels:
                            try:
                                # Precarga defensiva: evita que un PeerIdInvalid aparezca recién
                                # dentro de monitor_single_group en vez de aquí, donde es más barato.
                                try:
                                    await assistant_app.get_chat(chat_id)
                                except Exception:
                                    pass
                                peer = await assistant_app.resolve_peer(chat_id)
                                task = asyncio.create_task(monitor_single_group(chat_id, peer, assistant_app, _default_my_id))
                                active_sentinels[chat_id] = {
                                    "client": assistant_app,
                                    "task": task,
                                    "user_id": 0
                                }
                            except PeerIdInvalid:
                                continue
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Aviso en radar master loop: {e}")
            
        await asyncio.sleep(45)


async def pending_auth_cleanup_loop():
    TTL_SECONDS = 600
    while True:
        try:
            now = time.time()
            expired = [uid for uid, data in pending_auth_sessions.items() if now - data.get("ts", now) > TTL_SECONDS]
            for uid in expired:
                logger.info(f"🧹 [Auth Expirada] Liberando sesión temporal abandonada del usuario {uid}.")
                await cancel_phone_auth(uid)
        except Exception as e:
            logger.debug(f"Aviso en limpieza de sesiones pendientes: {e}")
        await asyncio.sleep(120)


async def init_assistant_master():
    global _default_my_id
    if assistant_app is None:
        logger.warning("⚠️ [Centinela Maestro Inactivo] Sin MASTER_SESSION; operando sólo con Centinelas propios por comunidad.")
    else:
        try:
            if not assistant_app.is_connected:
                await assistant_app.start()
            me = await assistant_app.get_me()
            _default_my_id = me.id
            logger.info(f"🤖 [Centinela Maestro Activo] Online como: @{me.username or me.first_name}")
        except FATAL_SESSION_ERRORS as auth_err:
            logger.error(f"🔒 [MASTER_SESSION Inválida] {auth_err}. Genera y configura una StringSession nueva en Railway.")
        except Exception as e:
            logger.warning(f"⚠️ [Aviso Centinela Maestro]: {e}")

    await load_all_sentinels()
    asyncio.create_task(radar_master_loop())
    asyncio.create_task(vc_scheduler_loop())
    asyncio.create_task(pending_auth_cleanup_loop())


def start_voice_radar(bot):
    global _global_bot
    _global_bot = bot
    asyncio.create_task(init_assistant_master())


async def close_all_sentinels():
    for group_id in list(active_sentinels.keys()):
        await disconnect_sentinel(group_id)
    if assistant_app and assistant_app.is_connected:
        try:
            await assistant_app.stop()
        except Exception:
            pass


async def set_participant_mic(chat_id: int, user_id: int, muted: bool, volume: int = 10000) -> bool:
    sentinel_data = active_sentinels.get(chat_id)
    client = sentinel_data["client"] if sentinel_data else assistant_app

    if not client or not client.is_connected:
        return False

    try:
        try:
            await client.get_chat(chat_id)
        except PeerIdInvalid:
            logger.warning(f"⚠️ [Peer ID Inválido] set_participant_mic no puede resolver {chat_id}.")
            return False
        except Exception:
            pass

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