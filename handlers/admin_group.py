"""
admin_group.py — Gestor de comandos y administración de grupos en The Bunker OS (Aiogram 3.x)
Fase 5: Sistema Criptográfico de Respaldo y Migración (Backup & Restore con HMAC-SHA256).
v37: inglés por defecto + idioma/TTL de servicio por grupo, Anti-Rogue silencioso en comandos del Dueño y
     /reload sin choque con el /reload del Centinela (Pyrogram).
v38.1: tipado estricto (Pylance/Pyright): todo valor Optional de aiogram (callback.data, callback.message,
       message.from_user, reply_to_message.text/caption, File.file_path) se protege antes de usarse.
v39: Log Channel Hub — /warn, /resetwarns, /delvip, /night, respaldos y paneles del Dueño se auditan en el canal de
     registro; /warn pasa manual=True a la escalera (sin métrica de sistema ni oferta de apelación pagada).
v42: Sincronía con assistant.py v42 —
     • El Centinela Maestro se lee EN VIVO (assistant.assistant_app): antes se importaba por valor y, si la v42 lo
       desactivaba en caliente (MASTER_SESSION revocada/duplicada), /reload seguía usando un cliente ya detenido.
     • /delvip ya no degrada a nadie que sea administrador por derecho propio: desde la v28 el pase MicVIP usa la
       etiqueta nativa (sin ascenso). Solo se degrada a quien ESTE bot ascendió en versiones antiguas
       (vip_badge_promotions) y la etiqueta MicVIP vence en el acto para que el barrido la retire.
     • /delvip y los paneles de AutoLower muestran el porcentaje REAL configurado (antes siempre 2 %).
The Bunker Command OS © 2026 — Cloud Media Management
"""
import os
import io
import sys
import inspect
import asyncio
import time
import html
import logging
import re
from typing import Any, Callable, Optional
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, BufferedInputFile
)
from aiogram.filters import Command, CommandObject
import database.database as _db_module
# 🧾 v39 · Log Channel Hub: auditoría de comandos del Dueño y de moderación manual en el canal de registro.
from handlers.logger_hub import dispatch_audit_log, forget_audit_target, mark_audit_handled
from database.database import (
    set_autolower_status, 
    get_autolower_status, 
    revoke_vip_mic, 
    register_user_group,
    get_podcast_status, set_podcast_status,
    get_screen_shield_status, set_screen_shield_status,
    add_user_strike, get_user_strikes, reset_user_strikes, get_warns_config,
    get_night_mode_config, activate_universal_night_mode, deactivate_universal_night_mode,
    approve_group, is_group_approved, get_group_tier, get_session_by_group,
    set_vc_monitor_status, get_vc_monitor_status,
    export_group_configuration, import_group_configuration
)
import assistant as _assistant_live   # v42 · lectura en vivo de assistant_app (puede pasar a None en caliente)
from assistant import (
    set_participant_mic,
    engage_podcast_ducking, disengage_podcast_ducking,
    engage_screen_shield, disengage_screen_shield,
    active_sentinels, register_or_update_sentinel, admin_caches,
    get_assistant_bot_id, monitor_single_group
)

logger = logging.getLogger("admin_group_handler")
router = Router()

_db_reset_warnings = getattr(_db_module, "reset_warnings", None)

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://the-bunker-bot-bunkerminiapp.vercel.app/").strip()

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


def _master_client():
    """v42 · Centinela Maestro ACTUAL (None si nunca existió o la v42 lo desactivó por sesión inválida)."""
    return getattr(_assistant_live, "assistant_app", None)


async def _autolower_pct(chat_id: int) -> int:
    """Porcentaje real de AutoLower (1-10 %) configurado por el Dueño; 2 % si no se puede leer."""
    try:
        from database.database import get_autolower_pct
        return max(1, min(10, int(await get_autolower_pct(int(chat_id)) or 2)))
    except Exception:
        return 2


def _sync_micvip_cleanup(chat_id: int, user_id: int) -> bool:
    """
    v42 · Tras /delvip: vence la etiqueta MicVIP nativa en el acto (el barrido de etiquetas la retira) y devuelve
    True si ESTE bot ascendió al usuario en una versión anterior a la v28 (fila en vip_badge_promotions), que es el
    único caso en que procede degradarlo. Se ejecuta en un hilo (asyncio.to_thread): nunca bloquea el event loop.
    """
    legacy = False
    with _db_module.get_db_connection() as conn:
        try:
            conn.execute("UPDATE micvip_tags SET expires_at = ? WHERE group_id = ? AND user_id = ? "
                         "AND tag_status != 'expired'", (int(time.time()), int(chat_id), int(user_id)))
        except Exception as ex:
            logger.debug(f"[delvip] Etiqueta MicVIP no vencida ({chat_id}/{user_id}): {ex}")
        try:
            row = conn.execute("SELECT 1 FROM vip_badge_promotions WHERE group_id = ? AND user_id = ?",
                               (int(chat_id), int(user_id))).fetchone()
            legacy = row is not None
        except Exception:
            legacy = False
    return legacy


# ==========================================================
# 🧾 v39 · LOG CHANNEL HUB (AUDITORÍA EN SEGUNDO PLANO)
# ==========================================================
def _audit(bot: Optional[Bot], chat_id: int, event_type: str, **fields: Any) -> None:
    """Publica el evento en el canal de registro sin bloquear el handler (vía _spawn); nunca lanza."""
    _spawn(dispatch_audit_log(bot, chat_id, event_type, **fields))


def _tag_name(target_tag: Optional[str]) -> Optional[str]:
    """Nombre legible a partir de la etiqueta HTML que devuelve resolve_target (para la tarjeta de auditoría)."""
    clean = html.unescape(re.sub(r"<[^>]+>", "", str(target_tag or ""))).strip()
    return clean or None


# v38 · regla canónica de idioma (database.resolve_lang) y autodetección centralizada.
# v38.1 · Tipos declarados ANTES del try: asignar None a un símbolo importado es un error de tipado en Pylance.
_LANG_AUTODETECT: bool
_resolve_lang: Optional[Callable[..., str]]
try:
    from database.database import LANG_AUTODETECT as _DB_LANG_AUTODETECT, resolve_lang as _db_resolve_lang
    _LANG_AUTODETECT = bool(_DB_LANG_AUTODETECT)
    _resolve_lang = _db_resolve_lang
except ImportError:  # pragma: no cover - base de datos anterior a v38
    _LANG_AUTODETECT = os.getenv("BUNKER_LANG_AUTODETECT", "0").strip() == "1"
    _resolve_lang = None


def get_lang(lang_code: Optional[str]) -> str:
    """Inglés por defecto; el español por language_code solo con BUNKER_LANG_AUTODETECT=1 (regla canónica v38)."""
    if _resolve_lang is not None:
        return _resolve_lang(None, lang_code)
    if _LANG_AUTODETECT and lang_code and str(lang_code).lower().startswith("es"):
        return "es"
    return "en"


async def _svc_lang(chat_id: int) -> str:
    """v37 · Idioma de servicio del grupo (/lang o panel «Language & TTL»). Inglés si no hay preferencia."""
    try:
        from handlers.groups import group_lang
        lang = await group_lang(int(chat_id))
    except Exception:
        return "en"
    return lang if lang in ("en", "es") else "en"


async def _svc_ttl(chat_id: int, fallback: int, minimum: int = 0) -> int:
    """v37 · Segundos de auto-borrado fijados por el administrador; `minimum` protege paneles con botones."""
    try:
        from handlers.groups import service_ttl
        ttl = int(await service_ttl(int(chat_id), fallback))
    except Exception:
        ttl = fallback
    return max(minimum, ttl)


def _localize_report(report, lang: str) -> str:
    """v38 · Informe de import_group_configuration (inglés) traducido al idioma de servicio del grupo."""
    try:
        from database.database import localize_import_report
        return localize_import_report(report, lang)
    except Exception:
        return str(report or "")


async def _ignore(message: Message) -> None:
    """v37 · Anti-Rogue: sin respuesta pública; solo se retira el comando."""
    try:
        await message.delete()
    except Exception:
        pass


def _is_sentinel_account(chat_id: int, user_id: int) -> bool:
    """
    v37 · ¿El emisor es la cuenta Centinela (Maestro o dedicada) de este grupo? Su /reload lo procesa el cliente
    Pyrogram (assistant.sentinel_reload_handler); el bot no debe tratarlo como un /reload de administrador.
    """
    if not user_id:
        return False
    clients = [_master_client(), (active_sentinels.get(chat_id) or {}).get("client")]
    for client in clients:
        me = getattr(client, "me", None)
        if me is not None and getattr(me, "id", None) == int(user_id):
            return True
    return False


async def is_user_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el miembro cuenta con facultades de creador, administrador o Arquitecto."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


async def is_user_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Verifica si el miembro posee el rango máximo de Creador (Owner) o Arquitecto."""
    if is_super_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status == "creator"
    except Exception:
        return False


async def auto_delete_msg(msg: Message, delay: int = 12):
    """Auto-destrucción simple de un mensaje de respuesta."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


async def auto_delete_pair(cmd_msg: Message, bot_msg: Message, delay: int = 12):
    """Auto-destrucción dual estricta para mantener el chat limpio y sin contaminación visual."""
    await asyncio.sleep(delay)
    try:
        await cmd_msg.delete()
    except Exception:
        pass
    try:
        await bot_msg.delete()
    except Exception:
        pass


# ==========================================================
# 🌐 DICCIONARIO BILINGÜE (MENSAJES DE SERVICIO Y ALERTAS)
# ==========================================================
TEXTS = {
    "en": {
        "owner_only": "⛔ <b>Access Denied:</b> This protocol is restricted exclusively to the community Owner.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_protected": "🛡️ <b>Action Denied:</b> Target user has Architect status or is an active Administrator.\n\n🛡️ <i>Cloud Media Management</i>",
        "denied_short": "⛔ Access denied.",
        "invalid_button": "⚠️ Invalid button.",
        "download_failed": "The file could not be downloaded: {error}",
        "community": "Community",
        "sentinel_master_standby": "Master Sentinel on standby ({error})",
        "sentinel_reconnect_failed": "Reconnection failed ⚠️",
        "sentinel_hint": "🎙️ <i>Dedicated Sentinel: send</i> <code>/reload</code> <i>from the Sentinel account in this group to activate voice moderation.</i>\n\n",
        "reload_success": (
            "🔄 <b>The Bunker Ecosystem Synchronized</b>\n\n"
            "• <b>Community:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>License Tier:</b> <code>{tier}</code>\n"
            "• <b>Acoustic Sentinel:</b> 🟢 <code>{sentinel_status}</code>\n"
            "• <b>Live Voice Radar:</b> 🟢 <code>ACTIVE (24/7 Monitoring)</code>\n"
            "• <b>Admin Permissions:</b> <code>Cache Refreshed 🔄</code>\n\n"
            "<i>The community has been indexed, approved, and reconnected successfully to your Command Center.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "settings_title": (
            "🛡️ <b>Control Center:</b> {title}\n\n"
            "Configure alphanumeric customs, content locks, purge limits, and voice sentinels.\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "btn_open_miniapp": "🌐 Open Command Center",
        "btn_open_pv": "⚙️ Open in DMs",
        "autolower_panel": (
            "🎛️ <b>Radar Console: AutoLower Acoustic Shield</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change microphone attenuation in real-time:\n\n"
            "🛡️️ <i>Cloud Media Management</i>"
        ),
        "autolower_on_btn": "✅ Enable AutoLower ({pct}%)",
        "autolower_off_btn": "❌ Disable AutoLower (Free)",
        "status_active": "🟢 ACTIVE (Mics dialed down to {pct}% for unverified users)",
        "status_inactive": "🔴 DEACTIVATED (Open Mics at 100%)",
        "podcast_panel": (
            "🎙️ <b>Radar Console: Podcast Mode (Ducking)</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change dynamic audio ducking:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_on_btn": "✅ Enable Podcast",
        "podcast_off_btn": "❌ Disable Podcast",
        "status_active_pod": "🟢 ACTIVE (Dynamic Ducking enabled)",
        "status_inactive_pod": "🔴 DEACTIVATED",
        "shield_panel": (
            "🎥 <b>Radar Console: Screen Shield (Antinota)</b>\n\n"
            "• <b>Active State:</b> {status}\n\n"
            "Select an action to change screen-sharing restrictions:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_on_btn": "✅ Enable Shield",
        "shield_off_btn": "❌ Disable Shield",
        "status_active_shield": "🟢 ACTIVE (Screen sharing blocked)",
        "status_inactive_shield": "🔴 DEACTIVATED",
        "night_on_msg": "🌙 <b>Universal Night Mode:</b> 🟢 ACTIVATED. Perimeter restrictions applied.",
        "night_off_msg": "☀️ <b>Universal Night Mode:</b> 🔴 DEACTIVATED. Standard permissions restored.",
        "vip_revoked": "✅ VIP Pass revoked for {target_tag}. Mic volume reset to {pct}%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Target required. Reply to a user, mention them or provide their ID.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_warn": "⚠️ Target required. Reply to a message or use: <code>/warn [@user or ID] [reason]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Warning Issued:</b> {target_tag} has received a formal strike ({current}/{limit}).\n• <b>Reason:</b> {reason}\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_punished": "⚖️ <b>Threshold Reached:</b> {target_tag} reached {limit}/{limit} strikes.\n• <b>Automated Action:</b> {action_name} executed.\n\n🛡️ <i>Cloud Media Management</i>",
        "warns_reset_done": "✅ All strikes have been cleared for {target_tag}. Full voice and text permissions restored.\n\n🛡️ <i>Cloud Media Management</i>",
        "backup_export_caption": (
            "🔐 <b>The Bunker OS — Cryptographic Perimeter Backup</b>\n\n"
            "• <b>Community:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>Digital Signature:</b> HMAC-SHA256 🛡️\n"
            "• <b>Version:</b> <code>v6.0</code>\n\n"
            "<i>This configuration file contains all perimeter rules, captcha configs, content locks, "
            "schedules, and tip destinations.\n\n"
            "To migrate or restore this configuration in another community, reply to this file with:</i> "
            "<code>/importsettings</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_export_error": "❌ Error exporting cryptographic backup: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "backup_import_needed": (
            "⚠️ <b>Backup File Required:</b>\n\n"
            "Reply to a <code>.bunker</code> backup file with <code>/importsettings</code> or attach it directly "
            "to restore and migrate community security settings.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_success": (
            "✅ <b>Perimeter Configuration Restored Successfully!</b>\n\n"
            "• <b>Target Community:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>Integrity Check:</b> Cryptographically Verified (HMAC Valid) 🟢\n"
            "• <b>Status:</b> All perimeter locks, captcha, and schedules applied instantly.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_failed": (
            "❌ <b>Backup Restoration Failed:</b>\n\n"
            "{error}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
    },
    "es": {
        "owner_only": "⛔ <b>Acceso denegado:</b> Este protocolo está reservado única y exclusivamente para el Dueño de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_protected": "🛡️ <b>Acción Denegada:</b> El usuario objetivo cuenta con inmunidad de Arquitecto o Rango de Administrador.\n\n🛡️ <i>Cloud Media Management</i>",
        "denied_short": "⛔ Acceso denegado.",
        "invalid_button": "⚠️ Botón inválido.",
        "download_failed": "No se pudo descargar el archivo: {error}",
        "community": "Comunidad",
        "sentinel_master_standby": "Centinela Maestro en espera ({error})",
        "sentinel_reconnect_failed": "Fallo en la reconexión ⚠️",
        "sentinel_hint": "🎙️ <i>Centinela dedicado: envía</i> <code>/reload</code> <i>desde la cuenta Centinela en este grupo para activar la moderación de voz.</i>\n\n",
        "reload_success": (
            "🔄 <b>Ecosistema The Bunker Sincronizado</b>\n\n"
            "• <b>Comunidad:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>Nivel de Licencia:</b> <code>{tier}</code>\n"
            "• <b>Centinela Acústico:</b> 🟢 <code>{sentinel_status}</code>\n"
            "• <b>Radar de Voz:</b> 🟢 <code>ACTIVO (Supervisión 24/7)</code>\n"
            "• <b>Permisos de Administrador:</b> <code>Caché Actualizado 🔄</code>\n\n"
            "<i>El grupo ha sido indexado, aprobado y reconectado con éxito a tu Command Center.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "settings_title": (
            "🛡️ <b>Panel de Configuración:</b> {title}\n\n"
            "Configura aduana alfanumérica, cerraduras, purgas y centinelas de voz.\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "btn_open_miniapp": "🌐 Abrir Command Center",
        "btn_open_pv": "⚙️ Abrir en Privado",
        "autolower_panel": (
            "🎛️ <b>Panel de Control: Radar AutoLower</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para alterar la atenuación de micrófonos en vivo:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "autolower_on_btn": "✅ Activar AutoLower ({pct}%)",
        "autolower_off_btn": "❌ Desactivar AutoLower (Libre)",
        "status_active": "🟢 ACTIVO (Reduciendo a {pct}% a no autorizados)",
        "status_inactive": "🔴 DESACTIVADO (Micrófonos Libres al 100%)",
        "podcast_panel": (
            "🎙️ <b>Panel de Control: Modo Podcast (Ducking)</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para alterar la atenuación dinámica:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_on_btn": "✅ Activar Podcast",
        "podcast_off_btn": "❌ Desactivar Podcast",
        "status_active_pod": "🟢 ACTIVO (Atenuación dinámica en vivo)",
        "status_inactive_pod": "🔴 DESACTIVADO",
        "shield_panel": (
            "🎥 <b>Panel de Control: Escudo Antinota</b>\n\n"
            "• <b>Estado Actual:</b> {status}\n\n"
            "Selecciona una directiva para el corte de transmisiones de pantalla:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_on_btn": "✅ Activar Escudo",
        "shield_off_btn": "❌ Desactivar Escudo",
        "status_active_shield": "🟢 ACTIVO (Corte de pantalla a no autorizados)",
        "status_inactive_shield": "🔴 DESACTIVADO",
        "night_on_msg": "🌙 <b>Modo Nocturno Universal:</b> 🟢 ACTIVADO. Restricciones perimetrales aplicadas.",
        "night_off_msg": "☀️ <b>Modo Nocturno Universal:</b> 🔴 DESACTIVADO. Permisos previos restaurados.",
        "vip_revoked": "✅ Pase VIP revocado para {target_tag}. Micrófono restablecido al {pct}%.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_vip": "⚠️ Objetivo requerido. Responde a un usuario, menciónalo con @ o pasa su ID.\n\n🛡️ <i>Cloud Media Management</i>",
        "target_needed_warn": "⚠️ Objetivo requerido. Responde a un mensaje o usa: <code>/warn [@usuario o ID] [motivo]</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_issued": "⚠️ <b>Advertencia Registrada:</b> {target_tag} ha acumulado una falta formal ({current}/{limit}).\n• <b>Motivo:</b> {reason}\n\n🛡️ <i>Cloud Media Management</i>",
        "warn_punished": "⚖️ <b>Límite de Faltas Alcanzado:</b> {target_tag} sumó {limit}/{limit} faltas.\n• <b>Castigo Automático:</b> Se aplicó {action_name} de inmediato.\n\n🛡️ <i>Cloud Media Management</i>",
        "warns_reset_done": "✅ Todas las advertencias han sido restablecidas a cero para {target_tag}. Permisos y voz restablecidos al 100%.\n\n🛡️ <i>Cloud Media Management</i>",
        "backup_export_caption": (
            "🔐 <b>The Bunker OS — Respaldo Criptográfico Perimetral</b>\n\n"
            "• <b>Comunidad:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>Firma Digital:</b> HMAC-SHA256 🛡️\n"
            "• <b>Versión:</b> <code>v6.0</code>\n\n"
            "<i>Este paquete contiene todas las cerraduras perimetrales, captcha, filtros, "
            "horarios de modo nocturno y destinos de propinas.\n\n"
            "Para restaurar o migrar estos ajustes a otra comunidad, responde a este archivo con:</i> "
            "<code>/importsettings</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_export_error": "❌ Error generando el respaldo criptográfico: {error}\n\n🛡️ <i>Cloud Media Management</i>",
        "backup_import_needed": (
            "⚠️ <b>Archivo de Respaldo Requerido:</b>\n\n"
            "Responde a un archivo <code>.bunker</code> con <code>/importsettings</code> o adjúntalo directamente "
            "con el comando para restaurar y migrar la seguridad de la comunidad.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_success": (
            "✅ <b>¡Configuración Perimetral Restaurada con Éxito!</b>\n\n"
            "• <b>Comunidad Destino:</b> <code>{title}</code> (<code>{chat_id}</code>)\n"
            "• <b>Verificación de Integridad:</b> Autenticación Criptográfica (HMAC Válido) 🟢\n"
            "• <b>Estado:</b> Cerraduras, captcha, filtros y directivas aplicados al instante.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "backup_import_failed": (
            "❌ <b>Fallo al Restaurar Respaldo:</b>\n\n"
            "{error}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
    }
}


# ==========================================================
# 🎯 RESOLUCIÓN TÁCTICA DE USUARIO Y ETIQUETA (@ O ENLACE)
# ==========================================================
async def resolve_target(message: Message, command: CommandObject, bot: Bot):
    """
    Identifica al objetivo por respuesta directa, mención @ o ID numérica,
    generando la etiqueta de mención directa con formato HTML protegido.
    """
    user_obj = None
    target_id = None

    if message.reply_to_message and message.reply_to_message.from_user:
        user_obj = message.reply_to_message.from_user
        target_id = user_obj.id
    elif command and command.args:
        arg = command.args.split()[0].strip()
        if arg.isdigit():
            target_id = int(arg)
            try:
                member = await bot.get_chat_member(chat_id=message.chat.id, user_id=target_id)
                user_obj = member.user
            except Exception:
                user_obj = None
        elif arg.startswith("@"):
            try:
                chat_info = await bot.get_chat(arg)
                target_id = chat_info.id
                user_obj = chat_info
            except Exception:
                return None, html.escape(arg)

    if user_obj:
        if getattr(user_obj, "username", None):
            tag = f"@{user_obj.username}"
        else:
            name = getattr(user_obj, "full_name", getattr(user_obj, "first_name", "User"))
            tag = f'<a href="tg://user?id={user_obj.id}">{html.escape(name)}</a>'
        return target_id, tag
    elif target_id:
        return target_id, f'<a href="tg://user?id={target_id}">ID: {target_id}</a>'

    return None, None


# ==========================================================
# 🔐 FASE 5: RESPALDO Y MIGRACIÓN CRIPTOGRÁFICA (/exportsettings, /importsettings)
# ==========================================================
@router.message(Command("exportsettings", "backup", "respaldo", "exportar"))
async def cmd_export_settings(message: Message, bot: Bot):
    """Genera y despacha el archivo criptográfico .bunker firmado con HMAC-SHA256."""
    if message.chat.type == "private":
        return

    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    lang = await _svc_lang(chat_id)
    t = TEXTS[lang]

    if not await is_user_creator(bot, chat_id, user_id):
        await _ignore(message)   # v37 · Anti-Rogue: sin aviso público
        return

    try:
        package_json_str = await export_group_configuration(chat_id)
        chat_title = message.chat.title or f"Chat {chat_id}"
        clean_filename = f"bunker_backup_{abs(chat_id)}_{int(time.time())}.bunker"

        file_data = BufferedInputFile(package_json_str.encode("utf-8"), filename=clean_filename)
        _audit(bot, chat_id, "settings_exported", actor=message.from_user, chat_title=message.chat.title,
               detail=clean_filename)   # 🧾 v39
        caption_text = t["backup_export_caption"].format(
            title=html.escape(chat_title),
            chat_id=chat_id
        )

        # Entrega preferente al DM del Creador para no exponer los ajustes en el chat
        try:
            await bot.send_document(
                chat_id=user_id,
                document=file_data,
                caption=caption_text,
                parse_mode="HTML"
            )
            confirm_text = (
                "🔐 <b>Respaldo criptográfico enviado a tus mensajes privados con éxito.</b>\n\n🛡️ <i>Cloud Media Management</i>"
                if lang == "es" else
                "🔐 <b>Cryptographic backup successfully sent to your private DMs.</b>\n\n🛡️ <i>Cloud Media Management</i>"
            )
            confirm = await message.reply(confirm_text, parse_mode="HTML")
            _spawn(auto_delete_pair(message, confirm, await _svc_ttl(message.chat.id, 15)))
        except Exception:
            # Fallback al chat del grupo si el Creador no ha iniciado chat con el bot
            sent_doc = await message.reply_document(
                document=file_data,
                caption=caption_text,
                parse_mode="HTML"
            )
            _spawn(auto_delete_pair(message, sent_doc, 60))

    except Exception as ex:
        logger.error(f"Error en /exportsettings ({chat_id}): {ex}")
        err_msg = await message.reply(t["backup_export_error"].format(error=html.escape(str(ex))), parse_mode="HTML")
        _spawn(auto_delete_pair(message, err_msg, await _svc_ttl(message.chat.id, 12)))


@router.message(Command("importsettings", "restore", "restaurar", "importar"))
async def cmd_import_settings(message: Message, command: CommandObject, bot: Bot):
    """Valida e importa la configuración perimetral verificando la firma HMAC."""
    if message.chat.type == "private":
        return

    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]

    if not await is_user_creator(bot, chat_id, user_id):
        await _ignore(message)   # v37 · Anti-Rogue: sin aviso público
        return

    doc = None
    json_content = None

    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
    elif message.document:
        doc = message.document
    elif message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        candidate = message.reply_to_message.text or message.reply_to_message.caption or ""
        if "payload" in candidate and "signature" in candidate:
            json_content = candidate
    elif command and command.args:
        candidate = command.args.strip()
        if "payload" in candidate and "signature" in candidate:
            json_content = candidate

    if doc:
        try:
            file_obj = await bot.get_file(doc.file_id)
            if not file_obj.file_path:
                raise ValueError("file path unavailable")
            downloaded = await bot.download_file(file_obj.file_path)
            if downloaded is None:
                raise ValueError("empty download")
            json_content = downloaded.read().decode("utf-8")
        except Exception as dl_err:
            logger.error(f"Error descargando archivo de backup: {dl_err}")
            msg = await message.reply(t["backup_import_failed"].format(
                error=html.escape(t["download_failed"].format(error=dl_err))), parse_mode="HTML")
            _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 12)))
            return

    if not json_content:
        msg = await message.reply(t["backup_import_needed"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 20, 20)))
        return

    try:
        success, report = await import_group_configuration(chat_id, json_content)
        chat_title = message.chat.title or f"Chat {chat_id}"

        if success:
            admin_caches.pop(chat_id, None)
            forget_audit_target(chat_id)   # 🧾 v39 · el respaldo puede traer otro canal de registro
            _audit(bot, chat_id, "settings_imported", actor=message.from_user, chat_title=chat_title)   # 🧾 v39

            # Notificar al WebSocket de la sala (Live Radar)
            try:
                main_mod = sys.modules.get("main")
                if main_mod:
                    fn = getattr(main_mod, "emit_radar_event", None)
                    if callable(fn):
                        _spawn(fn(chat_id, "backup_restored", {"chat_id": chat_id}))
            except Exception:
                pass

            succ_msg = await message.reply(
                t["backup_import_success"].format(title=html.escape(chat_title), chat_id=chat_id),
                parse_mode="HTML"
            )
            _spawn(auto_delete_pair(message, succ_msg, await _svc_ttl(message.chat.id, 30, 20)))
        else:
            # 🧾 v39 · Un intento de restauración rechazado (firma HMAC inválida, formato…) es un evento de seguridad.
            _audit(bot, chat_id, "settings_import_failed", actor=message.from_user, chat_title=chat_title,
                   reason=_localize_report(report, lang)[:300])
            fail_msg = await message.reply(
                t["backup_import_failed"].format(error=html.escape(_localize_report(report, lang))),
                parse_mode="HTML"
            )
            _spawn(auto_delete_pair(message, fail_msg, await _svc_ttl(message.chat.id, 15)))

    except Exception as ex:
        logger.error(f"Error procesando import_group_configuration en {chat_id}: {ex}")
        err_msg = await message.reply(t["backup_import_failed"].format(error=html.escape(str(ex))), parse_mode="HTML")
        _spawn(auto_delete_pair(message, err_msg, await _svc_ttl(message.chat.id, 12)))


# ==========================================================
# 🔄 COMANDO DE RECARGA, INDEXACIÓN Y RECONEXIÓN TOTAL (/reload)
# ==========================================================
@router.message(Command("reload"))
async def cmd_reload_group(message: Message, bot: Bot):
    """
    Fuerza la recarga del perímetro y sincroniza la administración en el grupo.
    v37 · Si quien lo envía es la cuenta Centinela, el bot no interviene: ese /reload lo procesa el cliente
    Pyrogram (assistant.sentinel_reload_handler), que publica su propia tarjeta de estado. Así no hay dos
    respuestas, ni se registra la cuenta Centinela como operador, ni se reinicia dos veces el monitor de voz.
    """
    if message.chat.type == "private":
        return

    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    if _is_sentinel_account(chat_id, user_id):
        return

    lang = await _svc_lang(chat_id)
    t = TEXTS[lang]

    if not await is_user_admin(bot, chat_id, user_id):
        await _ignore(message)
        return

    chat_title = message.chat.title or t["community"]
    chat_type = message.chat.type

    await register_user_group(
        user_id=user_id,
        group_id=chat_id,
        group_name=chat_title,
        chat_type=chat_type
    )

    current_tier = await get_group_tier(chat_id) or "free"
    if not await is_group_approved(chat_id):
        await approve_group(chat_id, tier=current_tier)

    await set_vc_monitor_status(chat_id, 1)
    admin_caches.pop(chat_id, None)
    _refresh_v37_caches(chat_id)

    sentinel_status = "Centinela Activo 🟢" if lang == "es" else "Active Sentinel 🟢"
    session_row = None
    try:
        session_row = await get_session_by_group(chat_id)
        if session_row:
            u_id, s_str, a_id, a_hash = session_row[0], session_row[1], session_row[2], session_row[3]
            connected = await register_or_update_sentinel(u_id, chat_id, s_str, a_id, a_hash)
            if connected:
                sentinel_status = "Centinela Dedicado Reconectado 💎" if lang == "es" else "Dedicated Sentinel Reconnected 💎"
            else:
                sentinel_status = "Error en Sesión Dedicada ⚠️" if lang == "es" else "Dedicated Session Error ⚠️"
        elif (_master_client() is not None) and getattr(_master_client(), "is_connected", False):
            sentinel_status = await _reload_master_monitor(chat_id, lang, t)
        else:
            sentinel_status = "Modo Pasivo (Sin Sesión MTProto)" if lang == "es" else "Passive Mode (No MTProto Session)"
    except Exception as ex:
        logger.error(f"❌ Error reconectando Centinela en reload ({chat_id}): {ex}")
        sentinel_status = t["sentinel_reconnect_failed"]

    tier_label = current_tier.upper()
    if tier_label == "PRO":
        tier_label = "PRO ⭐"
    elif tier_label in ["ULTRA_PRO", "ULTRAPRO"]:
        tier_label = "ULTRA PRO 💎"
    else:
        tier_label = "BÁSICO (Free)" if lang == "es" else "BASIC (Free)"

    bot_info = await bot.get_me()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_pv"], url=f"https://t.me/{bot_info.username}?start=gset_{chat_id}")],
        [InlineKeyboardButton(text=t["btn_open_miniapp"], url=f"{WEBAPP_URL}?chat_id={chat_id}")]
    ])

    report_text = t["reload_success"].format(
        title=html.escape(chat_title),
        chat_id=chat_id,
        tier=tier_label,
        sentinel_status=html.escape(sentinel_status)
    )
    if session_row:
        # v37 · Recordatorio: el Centinela dedicado se activa con SU propio /reload dentro del grupo.
        report_text = report_text.replace("🛡️ <i>Cloud Media Management</i>",
                                          t["sentinel_hint"] + "🛡️ <i>Cloud Media Management</i>", 1)

    report = await message.answer(report_text, reply_markup=kb, parse_mode="HTML")
    try:
        await message.delete()
    except Exception:
        pass
    # v37 · El informe ya no queda fijo en el chat: respeta el TTL de servicio (mínimo 30 s para usar los botones).
    _spawn(auto_delete_msg(report, await _svc_ttl(chat_id, 60, 30)))


def _refresh_v37_caches(chat_id: int) -> None:
    """v37 · /reload también relee Anti-Raid, idioma/TTL de servicio y los permisos de videochat del Centinela."""
    try:
        from handlers import groups as _groups
        for name in ("invalidate_raid_cfg", "invalidate_service_prefs"):
            fn = getattr(_groups, name, None)
            if callable(fn):
                fn(chat_id)
    except Exception as ex:
        logger.debug(f"[Reload] Cachés de groups no invalidadas en {chat_id}: {ex}")
    try:
        import assistant as _assistant
        for fn_name in ("_invalidate_call_rights", "invalidate_ai_sentinel_cache"):
            fn = getattr(_assistant, fn_name, None)
            if callable(fn):
                fn(chat_id)
        # v42 · mismo alcance que el /reload del Centinela: enfriamiento, strikes y reintento de unión del radar.
        for store_name in ("_SVC_PREFS_CACHE_A", "_autolower_cooldowns", "_forbidden_strikes", "_last_join_attempt"):
            cache = getattr(_assistant, store_name, None)
            if isinstance(cache, dict):
                cache.pop(chat_id, None)
    except Exception as ex:
        logger.debug(f"[Reload] Cachés del Centinela no invalidadas en {chat_id}: {ex}")
    try:
        from middlewares.anti_spam import forget_chat_approval, forget_sender_rights
        forget_chat_approval(chat_id)      # /reload puede aprobar el grupo: el Anti-Rogue lo ve al instante
        forget_sender_rights(chat_id)      # y relee los rangos de administración
    except Exception as ex:
        logger.debug(f"[Reload] Cachés del Anti-Rogue no invalidadas en {chat_id}: {ex}")


async def _reload_master_monitor(chat_id: int, lang: str, t: dict) -> str:
    """
    v37 · Monitor del Centinela Maestro. Usa assistant._ensure_monitor_for (candado por grupo, no reinicia un monitor
    sano ni desplaza a un Centinela dedicado). Sin esa función (assistant anterior a v37) se conserva el método v36.
    """
    import assistant as _assistant
    master = _master_client()
    if master is None:
        return t["sentinel_master_standby"].format(error="no MTProto session")
    ensure = getattr(_assistant, "_ensure_monitor_for", None)
    if callable(ensure):
        outcome = await ensure(master, chat_id)
        labels = {
            "running": ("Centinela Maestro Activo 🤖", "Master Sentinel Active 🤖"),
            "started": ("Centinela Maestro Reconectado 🤖", "Master Sentinel Reconnected 🤖"),
            "other": ("Centinela Dedicado Activo 💎", "Dedicated Sentinel Active 💎"),
        }
        if outcome in labels:
            return labels[outcome][0 if lang == "es" else 1]
        return t["sentinel_master_standby"].format(error=outcome)
    try:
        peer = await master.resolve_peer(chat_id)
        if chat_id in active_sentinels:
            old_info = active_sentinels.pop(chat_id)
            try:
                old_info["task"].cancel()
            except Exception:
                pass
        master_bot_id = get_assistant_bot_id()
        task = asyncio.create_task(monitor_single_group(chat_id, peer, master, master_bot_id))
        active_sentinels[chat_id] = {"client": master, "task": task, "user_id": 0}
        return "Centinela Maestro Reconectado 🤖" if lang == "es" else "Master Sentinel Reconnected 🤖"
    except Exception as master_err:
        return t["sentinel_master_standby"].format(error=str(master_err)[:80])


# ==========================================================
# ⚙️ COMANDO DE ENLACE A CONFIGURACIÓN Y MATRIX (/settings, /matrix)
# ==========================================================
@router.message(Command("settings", "matrix", "panel"))
async def cmd_settings_group(message: Message, bot: Bot):
    """Entrega acceso directo al panel DM y al Command Center de la Mini App."""
    if message.chat.type == "private": 
        return
    
    user_id = message.from_user.id if message.from_user else 0
    if not await is_user_admin(bot, message.chat.id, user_id):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    await register_user_group(
        user_id=user_id, 
        group_id=message.chat.id, 
        group_name=message.chat.title or "Community",
        chat_type=message.chat.type
    )
    bot_info = await bot.get_me()
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_open_miniapp"], url=f"{WEBAPP_URL}?chat_id={message.chat.id}")],
        [InlineKeyboardButton(text=t["btn_open_pv"], url=f"https://t.me/{bot_info.username}?start=gset_{message.chat.id}")]
    ])
    
    msg = await message.answer(t["settings_title"].format(title=html.escape(message.chat.title or t["community"])), reply_markup=kb, parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 15)))


# ==========================================================
# 🎛️ CONTROLES RÁPIDOS DE AUDIO Y TRANSMISIÓN (BLINDADOS)
# ==========================================================
@router.message(Command("autolower"))
async def cmd_autolower_config(message: Message, command: CommandObject, bot: Bot):
    """Muestra el panel interactivo de atenuación acústica al creador del grupo."""
    if message.chat.type == "private":
        return

    if not await is_user_creator(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    chat_id = message.chat.id
    current_status = await get_autolower_status(chat_id)
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]
    pct = await _autolower_pct(chat_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["autolower_on_btn"].format(pct=pct), callback_data=f"gautolower_on_{chat_id}_{lang}"),
            InlineKeyboardButton(text=t["autolower_off_btn"], callback_data=f"gautolower_off_{chat_id}_{lang}")
        ]
    ])
    status_text = t["status_active"].format(pct=pct) if current_status == 1 else t["status_inactive"]
    msg = await message.answer(t["autolower_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 20, 20)))


@router.callback_query(F.data.startswith("gautolower_"))
async def process_autolower_callback(callback: CallbackQuery, bot: Bot):
    data_parts = (callback.data or "").split("_")
    if len(data_parts) < 3:
        await callback.answer()
        return

    action = data_parts[1]
    try:
        group_id = int(data_parts[2])
    except (ValueError, IndexError):
        await callback.answer(TEXTS["en"]["invalid_button"], show_alert=True)
        return
    lang = data_parts[3] if len(data_parts) > 3 and data_parts[3] in TEXTS else await _svc_lang(group_id)
    t = TEXTS.get(lang, TEXTS["en"])

    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer(t["denied_short"], show_alert=True)
        return

    await callback.answer()

    new_status = 1 if action == "on" else 0
    await set_autolower_status(group_id, new_status)
    _audit(bot, group_id, "autolower_on" if new_status else "autolower_off", actor=callback.from_user)   # 🧾 v39
    
    status_text = t["status_active"].format(pct=await _autolower_pct(group_id)) if new_status == 1 else t["status_inactive"]
    panel_msg = callback.message
    if isinstance(panel_msg, Message):   # un mensaje inaccesible (muy antiguo) no se puede editar
        try:
            await panel_msg.edit_text(t["autolower_panel"].format(status=status_text), reply_markup=panel_msg.reply_markup, parse_mode="HTML")
        except Exception:
            pass


@router.message(Command("podcast"))
async def cmd_podcast_config(message: Message, bot: Bot):
    """Muestra el panel interactivo del Modo Podcast (Ducking) al creador del grupo."""
    if message.chat.type == "private":
        return

    if not await is_user_creator(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    chat_id = message.chat.id
    current_status = await get_podcast_status(chat_id)
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["podcast_on_btn"], callback_data=f"gpodcast_on_{chat_id}_{lang}"),
            InlineKeyboardButton(text=t["podcast_off_btn"], callback_data=f"gpodcast_off_{chat_id}_{lang}")
        ]
    ])
    status_text = t["status_active_pod"] if current_status == 1 else t["status_inactive_pod"]
    msg = await message.answer(t["podcast_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 20, 20)))


@router.callback_query(F.data.startswith("gpodcast_"))
async def process_podcast_callback(callback: CallbackQuery, bot: Bot):
    data_parts = (callback.data or "").split("_")
    if len(data_parts) < 3:
        await callback.answer()
        return

    action = data_parts[1]
    try:
        group_id = int(data_parts[2])
    except (ValueError, IndexError):
        await callback.answer(TEXTS["en"]["invalid_button"], show_alert=True)
        return
    lang = data_parts[3] if len(data_parts) > 3 and data_parts[3] in TEXTS else await _svc_lang(group_id)
    t = TEXTS.get(lang, TEXTS["en"])

    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer(t["denied_short"], show_alert=True)
        return

    await callback.answer()

    new_status = 1 if action == "on" else 0
    await set_podcast_status(group_id, new_status)
    _audit(bot, group_id, "podcast_on" if new_status else "podcast_off", actor=callback.from_user)   # 🧾 v39
    
    if new_status == 1:
        await engage_podcast_ducking(group_id)
    else:
        await disengage_podcast_ducking(group_id)

    status_text = t["status_active_pod"] if new_status == 1 else t["status_inactive_pod"]
    panel_msg = callback.message
    if isinstance(panel_msg, Message):   # un mensaje inaccesible (muy antiguo) no se puede editar
        try:
            await panel_msg.edit_text(t["podcast_panel"].format(status=status_text), reply_markup=panel_msg.reply_markup, parse_mode="HTML")
        except Exception:
            pass


@router.message(Command("shield"))
async def cmd_shield_config(message: Message, bot: Bot):
    """Muestra el panel interactivo del Escudo Antinota al creador del grupo."""
    if message.chat.type == "private":
        return

    if not await is_user_creator(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        try: 
            await message.delete()
        except Exception: 
            pass
        return

    chat_id = message.chat.id
    current_status = await get_screen_shield_status(chat_id)
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["shield_on_btn"], callback_data=f"gshield_on_{chat_id}_{lang}"),
            InlineKeyboardButton(text=t["shield_off_btn"], callback_data=f"gshield_off_{chat_id}_{lang}")
        ]
    ])
    status_text = t["status_active_shield"] if current_status == 1 else t["status_inactive_shield"]
    msg = await message.answer(t["shield_panel"].format(status=status_text), reply_markup=keyboard, parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 20, 20)))


@router.callback_query(F.data.startswith("gshield_"))
async def process_shield_callback(callback: CallbackQuery, bot: Bot):
    data_parts = (callback.data or "").split("_")
    if len(data_parts) < 3:
        await callback.answer()
        return

    action = data_parts[1]
    try:
        group_id = int(data_parts[2])
    except (ValueError, IndexError):
        await callback.answer(TEXTS["en"]["invalid_button"], show_alert=True)
        return
    lang = data_parts[3] if len(data_parts) > 3 and data_parts[3] in TEXTS else await _svc_lang(group_id)
    t = TEXTS.get(lang, TEXTS["en"])

    if not await is_user_creator(bot, group_id, callback.from_user.id):
        await callback.answer(t["denied_short"], show_alert=True)
        return

    await callback.answer()

    new_status = 1 if action == "on" else 0
    await set_screen_shield_status(group_id, new_status)
    _audit(bot, group_id, "screenshield_on" if new_status else "screenshield_off", actor=callback.from_user)   # 🧾 v39

    if new_status == 1:
        await engage_screen_shield(group_id)
    else:
        await disengage_screen_shield(group_id)
    
    status_text = t["status_active_shield"] if new_status == 1 else t["status_inactive_shield"]
    panel_msg = callback.message
    if isinstance(panel_msg, Message):   # un mensaje inaccesible (muy antiguo) no se puede editar
        try:
            await panel_msg.edit_text(t["shield_panel"].format(status=status_text), reply_markup=panel_msg.reply_markup, parse_mode="HTML")
        except Exception:
            pass


# ==========================================================
# 🌙 BLINDAJE RÁPIDO: MODO NOCTURNO UNIVERSAL (/night)
# ==========================================================
@router.message(Command("night"))
async def cmd_toggle_night(message: Message, bot: Bot):
    """Activa o desactiva en un solo paso el Modo Nocturno Universal con snapshot."""
    if message.chat.type == "private":
        return
        
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await _ignore(message)   # v37 · Anti-Rogue: sin aviso público
        return

    chat_id = message.chat.id
    cfg = await get_night_mode_config(chat_id)
    if cfg["status"] == 0:
        await activate_universal_night_mode(chat_id)
        _audit(bot, chat_id, "night_on", actor=message.from_user)    # 🧾 v39
        msg = await message.reply(t["night_on_msg"], parse_mode="HTML")
    else:
        await deactivate_universal_night_mode(chat_id)
        _audit(bot, chat_id, "night_off", actor=message.from_user)   # 🧾 v39
        msg = await message.reply(t["night_off_msg"], parse_mode="HTML")

    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 12)))


# ==========================================================
# ⚠️ MATRIZ DE ADVERTENCIAS CENTRALIZADA (/warn, /resetwarns)
# ==========================================================
@router.message(Command("warn"))
async def cmd_warn_user(message: Message, command: CommandObject, bot: Bot):
    """Aplica una advertencia formal al usuario y ejecuta castigo automático si alcanza el límite."""
    if message.chat.type == "private":
        return

    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]

    if not await is_user_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id or target_tag is None:
        msg = await message.reply(t["target_needed_warn"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 10)))
        return

    if is_super_admin(target_id) or await is_user_admin(bot, message.chat.id, target_id):
        msg = await message.reply(t["target_protected"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 8)))
        return

    reason = "Violación de normas perimetrales" if lang == "es" else "Perimeter rules violation"
    if message.reply_to_message:
        if command and command.args:
            reason = command.args.strip()
    elif command and command.args:
        args_parts = command.args.split(maxsplit=1)
        if len(args_parts) > 1:
            reason = args_parts[1].strip()

    try:
        from handlers.groups import enforce_warn_ladder
        clean_username = target_tag.replace("@", "") if target_tag.startswith("@") else ""
        outcome = await enforce_warn_ladder(
            bot=bot,
            chat_id=message.chat.id,
            target_id=target_id,
            target_username=clean_username,
            target_mention=target_tag,
            reply_to=message,
            reason=reason,
            # v39 · advertencia MANUAL: el administrador queda en la auditoría y, como decisión humana, no cuenta como
            # bloqueo del sistema ni genera oferta de apelación pagada (antes un motivo libre se trataba como automático).
            actor=message.from_user,
            manual=True
        )
        if outcome.get("status") in ("warned", "sanctioned", "immune"):
            try:
                await message.delete()
            except Exception:
                pass
            return
    except Exception as e:
        logger.debug(f"Aviso ejecutando ladder de warns: {e}")

    cfg = await get_warns_config(message.chat.id)
    limit = cfg.get("limit", 3)
    action = cfg.get("action", "mute")
    current_strikes = await add_user_strike(message.chat.id, target_id, reason)
    # 🧾 v39 · Ruta de respaldo (sin escalera disponible): la auditoría se registra aquí.
    audit_fields: dict[str, Any] = {"user_id": target_id, "user_name": _tag_name(target_tag), "actor": message.from_user,
                                    "reason": reason, "strikes": current_strikes, "limit": limit}

    if current_strikes >= limit:
        await reset_user_strikes(message.chat.id, target_id)
        if _db_reset_warnings:
            try:
                res = _db_reset_warnings(target_id)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass
        try:
            if action == "ban":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
            elif action == "kick":
                await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
            elif action == "mute":
                await bot.restrict_chat_member(
                    chat_id=message.chat.id, 
                    user_id=target_id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            try:
                await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=0)
            except Exception:
                pass
            for kind in ("removal", "restrict"):
                mark_audit_handled(f"{message.chat.id}:{kind}:{target_id}", 30.0)
            _audit(bot, message.chat.id, "sanction", action=action, **audit_fields)   # 🧾 v39
            msg = await message.reply(t["warn_punished"].format(target_tag=target_tag, limit=limit, action_name=action.upper()), parse_mode="HTML")
        except Exception as ex:
            msg = await message.reply(f"❌ Error: {ex}", parse_mode="HTML")
    else:
        try:
            vol = int(10000 * max(0, limit - current_strikes) / max(1, limit))
            await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=False, volume=vol)
        except Exception:
            pass
        _audit(bot, message.chat.id, "warn", action="warn", **audit_fields)   # 🧾 v39
        msg = await message.reply(t["warn_issued"].format(target_tag=target_tag, current=current_strikes, limit=limit, reason=reason), parse_mode="HTML")

    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 12)))


@router.message(Command("resetwarns"))
async def cmd_reset_warns(message: Message, command: CommandObject, bot: Bot):
    """Limpia a cero el historial de faltas de un miembro y restaura permisos completos de texto y voz."""
    if message.chat.type == "private":
        return

    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]

    if not await is_user_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id or target_tag is None:
        msg = await message.reply(t["target_needed_warn"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 10)))
        return

    await reset_user_strikes(message.chat.id, target_id)
    if _db_reset_warnings:
        try:
            res = _db_reset_warnings(target_id)
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass
    # 🧾 v39 · Auditoría: historial de advertencias a cero (la liberación de permisos que sigue no genera otra tarjeta).
    mark_audit_handled(f"{message.chat.id}:unrestrict:{target_id}", 30.0)
    _audit(bot, message.chat.id, "warns_reset", user_id=target_id, user_name=_tag_name(target_tag),
           actor=message.from_user)

    try:
        await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=False, volume=10000)
    except Exception:
        pass

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
    except Exception as e:
        logger.debug(f"Aviso restaurando permisos de chat para {target_id}: {e}")

    msg = await message.reply(t["warns_reset_done"].format(target_tag=target_tag), parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 12)))


# ==========================================================
# 🛑 REVOCACIÓN DE PASE VIP (EXCLUSIVO DUEÑO)
# ==========================================
@router.message(Command("delvip"))
async def cmd_remove_vip(message: Message, command: CommandObject, bot: Bot):
    """Revoca el pase VIP, vence su etiqueta nativa y devuelve el micrófono al porcentaje de AutoLower configurado."""
    if message.chat.type == "private": 
        return
        
    lang = await _svc_lang(message.chat.id)
    t = TEXTS[lang]

    if not await is_user_creator(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await _ignore(message)   # v37 · Anti-Rogue: sin aviso público
        return

    target_id, target_tag = await resolve_target(message, command, bot)
    if not target_id or target_tag is None:
        msg = await message.reply(t["target_needed_vip"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 10)))
        return

    if is_super_admin(target_id):
        msg = await message.reply(t["target_protected"], parse_mode="HTML")
        _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 8)))
        return
        
    await revoke_vip_mic(target_id, message.chat.id)
    _audit(bot, message.chat.id, "vip_revoked", user_id=target_id, user_name=_tag_name(target_tag),
           actor=message.from_user)   # 🧾 v39

    pct = await _autolower_pct(message.chat.id)
    try:
        await set_participant_mic(chat_id=message.chat.id, user_id=target_id, muted=True, volume=pct * 100)
    except Exception as e:
        logger.warning(f"Aviso Centinela al revocar VIP en grupo {message.chat.id}: {e}")

    # v42 · Solo se degrada a quien ESTE bot ascendió por el pase en versiones anteriores a la v28. Un administrador
    #       real conserva sus permisos aunque tuviera pase VIP.
    try:
        legacy_promoted = await asyncio.to_thread(_sync_micvip_cleanup, message.chat.id, target_id)
    except Exception as cleanup_err:
        logger.debug(f"[delvip] Limpieza MicVIP fallida ({message.chat.id}/{target_id}): {cleanup_err}")
        legacy_promoted = False
    if legacy_promoted:
        try:
            await bot.promote_chat_member(
                chat_id=message.chat.id, user_id=target_id,
                can_manage_chat=False, can_change_info=False, can_delete_messages=False,
                can_invite_users=False, can_restrict_members=False, can_pin_messages=False,
                can_promote_members=False, can_manage_video_chats=False
            )
            micvip_legacy_resolve = getattr(_db_module, "micvip_legacy_resolve", None)
            if callable(micvip_legacy_resolve):
                await micvip_legacy_resolve(message.chat.id, target_id, True)
        except Exception as demote_err:
            logger.debug(f"[delvip] Degradación del ascenso antiguo fallida ({target_id}): {demote_err}")

    msg = await message.answer(t["vip_revoked"].format(target_tag=target_tag, pct=pct), parse_mode="HTML")
    _spawn(auto_delete_pair(message, msg, await _svc_ttl(message.chat.id, 12)))