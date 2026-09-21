import os
import time
import asyncio
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, ChatPermissions
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import CommandStart, CommandObject
from aiogram.exceptions import TelegramBadRequest
from database.database import (
    get_or_create_user, get_user_groups, 
    get_group_tier, get_user_global_tier, 
    get_antispam_filter, set_antispam_filter, 
    get_antiflood_config, set_antiflood_config,
    get_antispam_delete, set_antispam_delete, 
    get_captcha_status, set_captcha_status,
    get_captcha_config, set_captcha_config,
    get_lock_status, set_lock_status,
    get_warns_config, set_warns_config,
    add_to_blacklist, get_blacklist,
    add_to_whitelist, is_whitelisted,
    get_autolower_status, set_autolower_status,
    register_bot_clone, save_owner_session,
    get_owner_session, revoke_owner_session,
    get_vc_schedule, set_vc_schedule
)
from assistant import (
    register_or_update_sentinel, disconnect_sentinel
)

router = Router()

ADMIN_GROUP_ID = -1004351489258
WEBAPP_URL = "https://thebunkerapp.netlify.app"

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
# Carga IDs desde variables de entorno o define los IDs de Rafa y Javi
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
# IDs de respaldo directo de los arquitectos
SUPER_ADMIN_IDS.update([5876356778, 6291929381])

def is_super_admin(user_id: int) -> bool:
    """Verifica si el usuario es uno de los dueños supremos con inmunidad total."""
    return user_id in SUPER_ADMIN_IDS

async def get_effective_group_tier(group_id: int, user_id: int) -> str:
    """Otorga ULTRA PRO automático e ilimitado a los Arquitectos."""
    if is_super_admin(user_id):
        return "ultra_pro"
    return await get_group_tier(group_id)

# ==========================================
# 🧠 ESTADOS DE EDICIÓN CONVERSACIONAL EN PRIVADO
# ==========================================
CAPTCHA_STATES = {}
CLONE_STATES = {}
SENTINEL_STATES = {}
VC_SCHED_STATES = {}
DB_REG_STATES = {}
MOD_TARGET_STATES = {}
MIC_VIP_STATES = {}
MIC_TAG_STATES = {}
GROUP_MIC_PRICE = {}
GROUP_VIP_TAG = {}  # Etiqueta personalizada para el comando /mic_vip (por defecto VIP 24/7)

FILTER_MAP = {
    "tglinks": "tg_links", "fwdchan": "fwd_channels", "fwdusr": "fwd_users",
    "fwdgrp": "fwd_groups", "fwdbot": "fwd_bots", "quotes": "quotes", "weblinks": "web_links"
}

# ==========================================
# 🌐 DICCIONARIO BILINGÜE CON IDENTIDAD DE MARCA
# ==========================================
TEXTS = {
    "en": {
        "owner_only_alert": "⛔ Access Denied: This command center is strictly restricted to the community Owner.",
        "welcome": (
            "🏴‍☠️ <b>Welcome to the Inner Circle, {name}.</b>\n\n"
            "I am <b>The Bunker Bot</b>, the architectural security core designed by <b>Master Tom</b>. Within this domain, you wield absolute authority to forge order out of chaos.\n\n"
            "Add me to your group as an administrator to deploy elite anti-spam, alphanumeric customs, and absolute control.\n\n"
            "Select an option below to audit tactical modules or access the Command Center.\n\n"
            "🛡️ <i>Powered by <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add": "➕ Add to a Group",
        "btn_settings": "⚙️ Group Settings",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ My ID & Status",
        "btn_support": "🆘 Support",
        "btn_info": "ℹ️ Information",
        "btn_how_works": "📖 How The Bunker Works",
        "settings_main": (
            "🛡️ <b>Tactical Community Command</b>\n\n"
            "Take total perimeter control over your community. From this console you can:\n\n"
            "• 🤖 Configure the <b>Alphanumeric Captcha</b> checkpoint.\n"
            "• 🔒 Manage granular <b>Content Locks</b> and Anti-Spam shields.\n"
            "• 🎙️ Deploy your <b>Dedicated Sentinel</b> and acoustic moderations.\n"
            "• 💰 Activate <b>Telegram Stars Monetization</b> for live voice chats with custom VIP tags.\n\n"
            "<i>Select the community below you wish to audit and shield:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "support_main": (
            "🆘 <b>Official Tactical Support</b>\n\n"
            "For direct assistance or elite passes, contact our Chief Architect:\n\n"
            "👤 <b>Contact:</b> @therealonetom\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "id_status": (
            "🔍 <b>Tactical Identity Telemetry:</b>\n\n"
            "• <b>User ID:</b> <code>{id}</code>\n"
            "• <b>Alias:</b> @{username}\n"
            "• <b>Operational Rank:</b> {rank}\n"
            "• <b>Status:</b> Active 🟢\n\n"
            "<i>© Cloud Media Management</i>"
        ),
        "info_main": (
            "ℹ️ <b>System Core Architecture</b>\n\n"
            "<b>The Bunker Bot</b>\n"
            "• <b>Version:</b> 5.2 (Elite Core - Ultra Pro Shield)\n"
            "• <b>Architect:</b> Master Tom\n"
            "• <b>Tactical Focus:</b> Multi-Sentinel Architecture, Native VIP Badging, and Absolute Security.\n\n"
            "🛡️ <i>Developed and supported by <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>How The Bunker Bot Works — Plan Breakdown</b>\n\n"
            "Discover the operational capabilities unlocked at each clearance level:\n\n"
            "🆓 <b>BASIC Plan (Free Forever):</b>\n"
            "• Essential group security and baseline anti-spam.\n"
            "• Standard one-tap verification captcha.\n"
            "• 3 daily uses limit for remote moderation commands.\n\n"
            "⭐ <b>PRO Plan ($5 / 500 Stars):</b>\n"
            "• ⚡ <b>Unlimited bot command usage:</b> Zero daily caps.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Routine cleanup of service logs and chat clutter.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and challenge timeouts.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Strict filtering against forwarded posts, bots, and links.\n\n"
            "💎 <b>ULTRA PRO ($8 / 800 Stars):</b>\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own @BotFather token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as an isolated 24/7 moderator (100% anti-ban protection).\n"
            "• 🏷️ <b>Native VIP Tag Editor:</b> Assign automated, immovable custom titles upon tipping Stars.\n"
            "• 🔇 <b>AutoLower Acoustic Shield:</b> Mutes unverified speakers down to 2% in milliseconds.\n"
            "• 💰 <b>Direct Stars Monetization (/mic_vip):</b> 100% of revenue flows straight to your balance.\n"
            "• 🗓️ <b>Weekly VC Scheduler:</b> Automated voice chat open/close schedules and stream refresh.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Security Matrix:</b> {group_name}\n\nSelect a tactical module to alter community parameters.",
        "pay_pro_title": (
            "⭐ <b>PRO Plan Subscription — {group_name} ($5 / 500 Stars)</b>\n\n"
            "Upgrade your community to elite operational status:\n\n"
            "• ⚡ <b>Unlimited Bot Commands:</b> Bypass the 3 daily uses limit.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Unlimited service logs and chat clutter cleanup.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and timeout parameters.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Advanced shielding against channels, bots, and links.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>ULTRA PRO Subscription — {group_name} ($8 / 800 Stars)</b>\n\n"
            "Total command, decentralized automation, and high-tier monetization for your community:\n\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own @BotFather token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as a 24/7 voice mod (isolated node).\n"
            "• 🏷️ <b>Native VIP Tag Assignment:</b> Immovable badges (VIP 24/7) on Stars tips.\n"
            "• 🔇 <b>Smart AutoLower Radar:</b> Unverified mics get dialed down to 2% in milliseconds.\n"
            "• 💰 <b>Telegram Stars Monetization (/mic_vip):</b> 100% of pass revenue flows directly into your balance.\n"
            "• 🗓️ <b>Weekly VC Scheduler:</b> Automated voice chat open and close cron.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Back to Main Menu",
        "btn_back_settings": "🔙 Back to Groups",
        "btn_back_group": "🔙 Group Panel",
        "btn_back_captcha": "🔙 Back to Captcha",
        "btn_back_antispam": "🔙 Back to Anti-Spam",
        "btn_back_antiflood": "🔙 Back to Anti-Flood",
        "mod_main": (
            "🛡️ <b>Tactical Moderation Matrix</b>\n\n"
            "Direct remote command console for <b>{group_name}</b>:\n\n"
            "• 🚫 <b>Ban (/ban):</b> Expulsion with time selector.\n"
            "• 👢 <b>Kick (/kick):</b> Immediate expulsion without permanent ban.\n"
            "• 🔇 <b>Mute (/mute):</b> Voice and message restriction with timeout.\n"
            "• 🔊 <b>Unmute (/unmute):</b> Immediate restoration of privileges.\n"
            "• ⚪ <b>Whitelist:</b> Total immunity for trusted allies.\n"
            "• ⚫ <b>Blacklist:</b> Banned keywords with automatic purging.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "eco_main": (
            "📡 <b>Radar & Ecosystem Control</b>\n\n"
            "Real-time telemetry and Voice Sentinel supervision for <b>{group_name}</b>:\n\n"
            "• 📡 <b>Radar Ecosistema:</b> Live ecosystem report and network latency.\n"
            "• 🎥 <b>/cams:</b> Stream quality audit & continuous audiovisual optimization.\n"
            "• ⚙️ <b>/autolower:</b> Voice chat volume moderation (2% vs 100%).\n"
            "• 🗓️ <b>/vcsched:</b> Automated Voice Chat opening/closing cron.\n"
            "• 🎙️ <b>/mic_vip:</b> VIP Microphone 24h pass pricing in Stars & Custom Tag.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        
        "btn_mod": "🛡️ Moderation Matrix",
        "btn_eco": "📡 Radar & Ecosystem",
        "btn_antispam": "🛡️ Anti-Spam",
        "btn_antiflood": "🌊 Anti-Flood",
        "btn_captcha": "🤖 Captcha Protection",
        "btn_locks": "🔒 Locks",
        "btn_warns": "⚠️ Warns",
        "btn_delmsgs": "🗑️ Delete Messages",
        "btn_clone": "🧬 Clone & Sentinel",

        "captcha_main_title": (
            "🤖 <b>Captcha Protection (Captcha Pro)</b>\n\n"
            "When active, incoming recruits are restricted until they solve an alphanumeric challenge via Direct Message.\n\n"
            "• <b>Status:</b> {status_text}\n"
            "• <b>Alphanumeric Mode:</b> {mode_text}\n"
            "• <b>Time Limit:</b> {time_text}s\n"
            "• <b>Punishment:</b> {action_text}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "captcha_action_title": (
            "⚖️ <b>Punishment Configuration</b>\n\n"
            "Select the sanction applied if the recruit fails or time expires:\n\n"
            "• <b>Active Punishment:</b> {mode_name} 🟢\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antispam_main_title": (
            "✉️ <b>Granular Anti-Spam Matrix</b>\n\n"
            "Inspect and shield your network against unauthorized transmissions. Active layers:\n"
            "• 📘 <b>Telegram Links:</b> {st_tg}\n"
            "• 📥 <b>Forwards Shield:</b> {st_fwd}\n"
            "• 💭 <b>Quotes Filter:</b> {st_q}\n"
            "• 🔗 <b>Internet Links:</b> {st_web}\n"
            "• 🗑️ <b>Delete Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Locks & Restrictions</b>\n\n"
            "Restrict specific content types to enforce absolute discipline:\n\n"
            "• <b>Media (Photos/Videos):</b> {media}\n"
            "• <b>Stickers & GIFs:</b> {stickers}\n"
            "• <b>Web Links:</b> {links}\n"
            "• <b>Bot Commands:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Tactical Warnings (Warns Matrix)</b>\n\n"
            "Configure the strike threshold and automated punishment for rule infractions.\n\n"
            "• <b>Strike Limit:</b> {limit} warnings\n"
            "• <b>Automated Punishment:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Advanced Message Purge Center</b>\n\n"
            "Control automated cleanup of service logs, main chat clutter, and command invocations.\n"
            "• <b>Tier Status:</b> {tier_display}\n"
            "• <b>Privilege / Quota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Anti-Flood Shield</b>\n\n<b>Threshold:</b> {msgs} msgs in {time}s.\n<b>Active Action:</b> {action}\n<b>Delete Messages:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Forwards Shield Configuration</b>\n\nControl what forwarded transmissions are blocked in the community:",
        
        "clone_main_title": (
            "🧬 <b>Clone & Dedicated Sentinel Architecture</b>\n\n"
            "Configure custom instances and mod accounts for <b>{group_name}</b>:\n\n"
            "• <b>License:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Dedicated Sentinel:</b> {sentinel_status}\n\n"
            "💡 <i>Isolated sandbox grid for total trust, anti-ban protection, and 24/7 voice chat lockdown. All Stars revenue stays 100% yours!</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),

        "af_msgs": "📄 Messages Threshold",
        "af_time": "⏱️ Time Window",
        "af_off": "❌ Disable",
        "af_warn": "⚠️ Warn",
        "af_kick": "👢 Kick",
        "af_mute": "🔇 Mute",
        "af_ban": "🚫 Ban",
        "af_del_msgs": "🗑️ Delete Messages",

        "fwd_chan": "📢 Channels",
        "fwd_usr": "👤 Users",
        "fwd_grp": "👥 Groups",
        "fwd_bot": "🤖 Bots",

        "btn_cfg_action": "⚙️ Configure",
        "btn_add_word": "➕ Register in DB",
        "btn_contact_support": "💬 Contact Support",
        "btn_back_mod": "🔙 Moderation",
        "btn_back_eco": "🔙 Ecosystem"
    },
    "es": {
        "owner_only_alert": "⛔ Acceso Denegado: Esta consola táctica está reservada única y exclusivamente para el Dueño de la comunidad.",
        "welcome": (
            "🏴‍☠️ <b>Bienvenido al Círculo Interno, {name}.</b>\n\n"
            "Soy <b>The Bunker Bot</b>, el núcleo arquitectónico de seguridad diseñado por <b>Master Tom</b>. Dentro de este dominio, posees autoridad absoluta para forjar el orden a partir del caos.\n\n"
            "Añádeme a tu grupo como administrador para desplegar el protocolo de élite de antispam, aduana alfanumérica y control absoluto.\n\n"
            "Selecciona una opción abajo para auditar los módulos tácticos o acceder al Command Center.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add": "➕ Añadir a un Grupo",
        "btn_settings": "⚙️ Configuración de Grupos",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ Mi ID y Estado",
        "btn_support": "🆘 Soporte",
        "btn_info": "ℹ️ Información",
        "btn_how_works": "📖 ¿Cómo funciona el Búnker?",
        "settings_main": (
            "🛡️ <b>Centro de Mando de Comunidades</b>\n\n"
            "Toma el control perimetral total de tu comunidad. Desde esta consola podrás:\n\n"
            "• 🤖 Configurar la <b>Aduana Captcha</b> alfanumérica.\n"
            "• 🔒 Establecer <b>Cerraduras</b> de contenido y filtros Anti-Spam.\n"
            "• 🎙️ Desplegar tu <b>Centinela Dedicado</b> y atenuación acústica.\n"
            "• 💰 Activar la <b>Monetización con Telegram Stars</b> con etiquetas VIP personalizadas.\n\n"
            "<i>Selecciona abajo la comunidad que deseas auditar y blindar:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "support_main": (
            "🆘 <b>Soporte Táctico Oficial</b>\n\n"
            "Para asistencia directa o pases de élite, contacta a nuestro Arquitecto Jefe:\n\n"
            "👤 <b>Contacto Directo:</b> @therealonetom\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "id_status": (
            "🔍 <b>Telemetría de Identidad Táctica:</b>\n\n"
            "• <b>User ID:</b> <code>{id}</code>\n"
            "• <b>Alias:</b> @{username}\n"
            "• <b>Rango Operativo:</b> {rank}\n"
            "• <b>Estado:</b> Activo 🟢\n\n"
            "<i>© Cloud Media Management</i>"
        ),
        "info_main": (
            "ℹ️ <b>Núcleo del Sistema</b>\n\n"
            "<b>The Bunker Bot</b>\n"
            "• <b>Versión:</b> 5.2 (Elite Core - Ultra Pro Shield)\n"
            "• <b>Arquitecto:</b> Master Tom\n"
            "• <b>Enfoque Táctico:</b> Arquitectura Multi-Centinela, Etiquetas Nativas VIP y Seguridad Absoluta.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>¿Cómo Funciona The Bunker Bot? — Niveles de Acceso</b>\n\n"
            "Conoce las herramientas y facultades de cada nivel operativo:\n\n"
            "🆓 <b>Plan BÁSICO (Gratis de por vida):</b>\n"
            "• Seguridad esencial para grupos y escudo anti-spam básico.\n"
            "• Verificación de identidad con botón estándar de un solo toque.\n"
            "• Límite de 3 usos diarios en comandos de moderación remota.\n\n"
            "⭐ <b>Plan PRO ($5 / 500 Stars):</b>\n"
            "• ⚡ <b>Comandos de bot ilimitados:</b> Sin topes diarios.\n"
            "• 🗑️ <b>Purga Automatizada:</b> Limpieza automática de mensajes de servicio y clutter.\n"
            "• 🤖 <b>Aduana Captcha Pro:</b> Mensaje de bienvenida y tiempos 100% personalizados.\n"
            "• 🛡️ <b>Anti-Spam Granular Total:</b> Bloqueo selectivo de canales, bots, citas y enlaces.\n\n"
            "💎 <b>Plan ULTRA PRO ($8 / 800 Stars):</b>\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Despliega tu réplica con tu propio token de @BotFather.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Tu cuenta secundaria como asistente 24/7 en llamadas (nodo aislado antiban).\n"
            "• 🏷️ <b>Editor Nativo de Etiquetas VIP:</b> Asignación de rangos inamovibles (VIP 24/7) automáticos por propinas.\n"
            "• 🔇 <b>Radar AutoLower Inteligente:</b> Micrófonos no autorizados al 2% en milisegundos.\n"
            "• 💰 <b>Monetización Stars (/mic_vip):</b> El 100% de las Stars recaudadas entran directo a tu balance.\n"
            "• 🗓️ <b>Programador VC Semanal:</b> Apertura y cierre autónomo de videochats según cronograma.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Matriz de Seguridad:</b> {group_name}\n\nSelecciona un módulo para alterar los parámetros de la comunidad.",
        "pay_pro_title": (
            "⭐ <b>Suscripción Plan PRO — {group_name} ($5 / 500 Stars)</b>\n\n"
            "Eleva tu comunidad a un estándar profesional de alta seguridad:\n\n"
            "• ⚡ <b>Comandos de Bot Ilimitados:</b> Sin tope diario de 3 usos.\n"
            "• 🗑️ <b>Purga Automatizada:</b> Limpieza ilimitada de mensajes de servicio y chat.\n"
            "• 🤖 <b>Aduana Captcha Pro Avanzada:</b> Mensajes y tiempos personalizados.\n"
            "• 🛡️ <b>Anti-Spam Granular Total:</b> Bloqueo selectivo de canales, bots y enlaces.\n\n"
            "<i>Selecciona tu pasarela preferida para activar al instante:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>Suscripción ULTRA PRO — {group_name} ($8 / 800 Stars)</b>\n\n"
            "Poder absoluto, automatización descentralizada y monetización para tu comunidad:\n\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Despliega tu réplica con tu propio token de @BotFather.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Tu cuenta secundaria como asistente 24/7 en llamadas (nodo aislado antiban).\n"
            "• 🏷️ <b>Etiquetas Nativas VIP:</b> Asignación de rangos inamovibles al recibir propinas de Stars.\n"
            "• 🔇 <b>Radar AutoLower Inteligente:</b> Micrófonos no autorizados al 2% en milisegundos.\n"
            "• 💰 <b>Monetización Stars (/mic_vip):</b> El 100% de las Stars recaudadas entran directo a tu balance.\n"
            "• 🗓️ <b>Programador VC Semanal:</b> Apertura y cierre autónomo de videochats según cronograma.\n\n"
            "<i>Selecciona tu pasarela preferida para activar al instante:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Volver al Menú Principal",
        "btn_back_settings": "🔙 Volver a Grupos",
        "btn_back_group": "🔙 Panel del Grupo",
        "btn_back_captcha": "🔙 Volver a Captcha",
        "btn_back_antispam": "🔙 Volver a Anti-Spam",
        "btn_back_antiflood": "🔙 Volver a Anti-Flood",
        "mod_main": (
            "🛡️ <b>Matriz de Moderación Táctica</b>\n\n"
            "Consola de control y contramedidas remotas para <b>{group_name}</b>:\n\n"
            "• 🚫 <b>Baneo (/ban):</b> Expulsión con selector de tiempo.\n"
            "• 👢 <b>Expulsión (/kick):</b> Expulsión inmediata sin bloqueo definitivo.\n"
            "• 🔇 <b>Silencio (/mute):</b> Restricción de voz y chat con tiempo programado.\n"
            "• 🔊 <b>Restaurar (/unmute):</b> Liberación inmediata de facultades.\n"
            "• ⚪ <b>Lista Blanca (Whitelist):</b> Inmunidad táctica para aliados.\n"
            "• ⚫ <b>Lista Negra (Blacklist):</b> Purga automática de términos prohibidos.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "eco_main": (
            "📡 <b>Radar y Control del Ecosistema</b>\n\n"
            "Telemetría en tiempo real y supervisión del Centinela de Voz en <b>{group_name}</b>:\n\n"
            "• 📡 <b>Radar Ecosistema:</b> Reporte en vivo del ecosistema y latencia de red.\n"
            "• 🎥 <b>/cams:</b> Calidad de video y optimización preventiva de llamadas.\n"
            "• ⚙️ <b>/autolower:</b> Control de atenuación de micrófonos en llamadas.\n"
            "• 🗓️ <b>/vcsched:</b> Cronograma de apertura/cierre automático de videochats.\n"
            "• 🎙️ <b>/mic_vip:</b> Tarifa en Stars y Etiqueta VIP para pases de micrófono de 24h.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        
        "btn_mod": "🛡️ Matriz de Moderación",
        "btn_eco": "📡 Radar y Ecosistema",
        "btn_antispam": "🛡️ Anti-Spam",
        "btn_antiflood": "🌊 Anti-Flood",
        "btn_captcha": "🤖 Captcha Protection",
        "btn_locks": "🔒 Cerraduras",
        "btn_warns": "⚠️ Advertencias",
        "btn_delmsgs": "🗑️ Borrar Mensajes",
        "btn_clone": "🧬 Clon & Centinela",

        "captcha_main_title": (
            "🤖 <b>Protección Captcha (Captcha Pro)</b>\n\n"
            "Al estar activo, los nuevos reclutas son confinados y reciben un desafío alfanumérico por Mensaje Privado (DM).\n\n"
            "• <b>Estado:</b> {status_text}\n"
            "• <b>Modo Alfanumérico:</b> {mode_text}\n"
            "• <b>Tiempo Límite:</b> {time_text}s\n"
            "• <b>Castigo Configurado:</b> {action_text}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "captcha_action_title": (
            "⚖️ <b>Configuración de Castigo</b>\n\n"
            "Selecciona la sanción aplicada si el recluta falla o expira el tiempo:\n\n"
            "• <b>Castigo Activo:</b> {mode_name} 🟢\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antispam_main_title": (
            "✉️ <b>Matriz Anti-Spam Granular</b>\n\n"
            "Inspecciona y blinda tu comunidad contra transmisiones no autorizadas. Capas activas:\n"
            "• 📘 <b>Enlaces Telegram:</b> {st_tg}\n"
            "• 📥 <b>Escudo de Reenvíos:</b> {st_fwd}\n"
            "• 💭 <b>Filtro de Citas:</b> {st_q}\n"
            "• 🔗 <b>Enlaces Internet:</b> {st_web}\n"
            "• 🗑️ <b>Borrado de Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Panel de Cerraduras (Locks)</b>\n\n"
            "Restringe el envío de contenido específico para mantener el orden absoluto en la comunidad:\n\n"
            "• <b>Multimedia (Fotos/Videos):</b> {media}\n"
            "• <b>Stickers y GIFs:</b> {stickers}\n"
            "• <b>Enlaces Web:</b> {links}\n"
            "• <b>Comandos de Bots:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Matriz de Advertencias (Warns)</b>\n\n"
            "Establece el umbral máximo de faltas y el castigo automático aplicado al alcanzarlas.\n\n"
            "• <b>Límite de Strikes:</b> {limit} advertencias\n"
            "• <b>Castigo Automático:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Centro Avanzado de Purga de Mensajes</b>\n\n"
            "Controla la limpieza automatizada de registros de servicio, contaminación visual del chat principal e invocaciones de comandos.\n"
            "• <b>Nivel del Plan:</b> {tier_display}\n"
            "• <b>Privilegio / Cuota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Escudo Anti-Flood</b>\n\n<b>Umbral:</b> {msgs} mensajes en {time}s.\n<b>Acción Activa:</b> {action}\n<b>Borrar Mensajes:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Configuración del Escudo de Reenvíos</b>\n\nControla qué tipos de reenvíos están bloqueados en la comunidad:",
        
        "clone_main_title": (
            "🧬 <b>Arquitectura de Clonación & Centinela Dedicado</b>\n\n"
            "Configura instancias y asistentes exclusivos para <b>{group_name}</b>:\n\n"
            "• <b>Licencia:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Centinela Dedicado:</b> {sentinel_status}\n\n"
            "💡 <i>Infraestructura aislada para máxima confianza, blindaje antiban y control 24/7 en videochats. ¡Las Stars recaudadas quedan 100% en tu saldo!</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),

        "af_msgs": "📄 Umbral Mensajes",
        "af_time": "⏱️ Ventana de Tiempo",
        "af_off": "❌ Desactivar",
        "af_warn": "⚠️ Advertir",
        "af_kick": "👢 Expulsar",
        "af_mute": "🔇 Silenciar",
        "af_ban": "🚫 Bloquear",
        "af_del_msgs": "🗑️ Borrar Mensajes",

        "fwd_chan": "📢 Canales",
        "fwd_usr": "👤 Usuarios",
        "fwd_grp": "👥 Grupos",
        "fwd_bot": "🤖 Bots",

        "btn_cfg_action": "⚙️ Configurar",
        "btn_add_word": "➕ Registrar Término / Usuario",
        "btn_contact_support": "💬 Contactar Soporte",
        "btn_back_mod": "🔙 Moderación",
        "btn_back_eco": "🔙 Ecosistema"
    }
}

# ==========================================
# 🔍 FILTRO DINÁMICO DE GRUPOS ACTIVOS (CON INMUNIDAD)
# ==========================================
async def get_active_user_groups(bot: Bot, user_id: int) -> list:
    """Devuelve los grupos gestionados. Los SuperAdmins tienen bypass de propiedad."""
    raw_groups = await get_user_groups(user_id)
    if not raw_groups:
        return []
        
    if is_super_admin(user_id):
        return raw_groups

    bot_info = await bot.get_me()
    bot_id = bot_info.id
    
    async def check_ownership(g_id, g_name):
        try:
            bot_member = await bot.get_chat_member(chat_id=g_id, user_id=bot_id)
            if bot_member.status in ["administrator", "creator"]:
                user_member = await bot.get_chat_member(chat_id=g_id, user_id=user_id)
                if user_member.status == "creator":
                    return (g_id, g_name)
        except Exception:
            return None
        return None

    results = await asyncio.gather(*(check_ownership(g_id, g_name) for g_id, g_name in raw_groups))
    return [res for res in results if res is not None]

# ==========================================
# 🚀 VALIDADORES DE PERMISOS (CON INMUNIDAD TOTAL)
# ==========================================
async def verify_admin_privileges(callback: CallbackQuery, bot: Bot, group_id: int) -> bool:
    """Garantiza acceso al Dueño o bypass total para los Arquitectos."""
    if is_super_admin(callback.from_user.id):
        return True
    lang = "es" if callback.from_user.language_code and callback.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=callback.from_user.id)
        if member.status == "creator":
            return True
    except Exception:
        pass
    await callback.answer(t["owner_only_alert"], show_alert=True)
    return False

async def verify_admin_privileges_msg(message: Message, bot: Bot, group_id: int) -> bool:
    """Garantiza acceso vía mensaje con bypass total para los Arquitectos."""
    if is_super_admin(message.from_user.id):
        return True
    lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=message.from_user.id)
        if member.status == "creator":
            return True
    except Exception:
        pass
    await message.answer(t["owner_only_alert"])
    return False

# ==========================================
# 🎛️ GENERADOR DE TECLADOS INLINE
# ==========================================
def get_main_keyboard(bot_username: str, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    add_url = f"https://t.me/{bot_username}?startgroup=true&admin=restrict_members+ban_users+delete_messages+pin_messages+manage_video_chats+promote_members"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_add"], url=add_url)],
        [InlineKeyboardButton(text=t["btn_settings"], callback_data=f"menu_settings_{lang}")],
        [InlineKeyboardButton(text=t["btn_saas"], web_app=WebAppInfo(url=WEBAPP_URL))],
        [
            InlineKeyboardButton(text=t["btn_id"], callback_data=f"menu_id_{lang}"),
            InlineKeyboardButton(text="🇪🇸 ES / 🇬🇧 EN", callback_data=f"lang_{'es' if lang == 'en' else 'en'}")
        ],
        [
            InlineKeyboardButton(text=t["btn_support"], callback_data=f"menu_support_{lang}"),
            InlineKeyboardButton(text=t["btn_info"], callback_data=f"menu_info_{lang}")
        ]
    ])

def get_simple_back_keyboard(lang: str, target: str = "main"):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_{target}_{lang}")]])

def get_info_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_how_works"], callback_data=f"menu_infohow_{lang}")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])

def get_support_keyboard(lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_contact_support"], url="https://t.me/m/RGx4ohGTMTk5")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
    ])

def get_groups_keyboard(groups: list, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    if not groups:
        no_groups_text = "⚠️ No hay comunidades activas vinculadas" if lang == "es" else "⚠️ No active communities linked"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=no_groups_text, callback_data="noop")],
            [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
        ])
    kb = [[InlineKeyboardButton(text=g_name, callback_data=f"gpanel_{g_id}_{lang}")] for g_id, g_name in groups]
    kb.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_group_panel_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ PRO", callback_data=f"pay_pro_{group_id}_{lang}"), InlineKeyboardButton(text="💎 ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_mod"], callback_data=f"menu_mod_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_eco"], callback_data=f"menu_eco_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_antispam"], callback_data=f"gset_antispam_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_antiflood"], callback_data=f"gset_antiflood_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_captcha"], callback_data=f"gset_captcha_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_locks"], callback_data=f"gset_locks_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_warns"], callback_data=f"gset_warns_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_delmsgs"], callback_data=f"gset_delmsgs_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_clone"], callback_data=f"gset_clone_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=f"🌐 {'English' if lang == 'es' else 'Español'}", callback_data=f"langpanel_{group_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_settings"], callback_data=f"menu_settings_{lang}")
        ]
    ])

def get_payment_keyboard(group_id: int, lang: str, tier_level: str = "pro"):
    """Teclado de pago blindado: Centinela Maestro eliminado de vistas públicas."""
    t = TEXTS.get(lang, TEXTS["es"])
    stars_price = "500 XTR" if tier_level == "pro" else "800 XTR"
    stars_label = f"⭐ Pagar con Stars ({stars_price})" if lang == "es" else f"⭐ Pay with Stars ({stars_price})"
    
    keyboard_rows = [
        [InlineKeyboardButton(text=stars_label, callback_data=f"inv_{tier_level}_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="💳 PayPal", url="https://paypal.me/Felipecosmic"),
            InlineKeyboardButton(text="🟡 Binance Pay", url="https://app.binance.com/uni-qr/request-to-pay?billOrderId=452404659499556864&billType=request_a_payment")
        ]
    ]

    # En ULTRA se ofrece la configuración de clon y centinela propio sin exponer la cuenta maestra
    if tier_level == "ultra":
        btn_text = "🧬 Configurar Clon & Centinela Propio" if lang == "es" else "🧬 Setup Own Clone & Sentinel"
        keyboard_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"gset_clone_{group_id}_{lang}")])

    keyboard_rows.append([InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

async def get_captcha_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_captcha_config(group_id)
    st = cfg["status"]
    st_btn = f"🛡️ Captcha: {'🟢' if st == 1 else '🔴'}"
    st_cb = f"togcap_off_{group_id}_{lang}" if st == 1 else f"togcap_on_{group_id}_{lang}"
    
    mode_st = cfg["mode"]
    mode_btn = f"🔤 Alphanumeric: {'🟢' if mode_st == 1 else '🔴'}" if lang == "en" else f"🔤 Alfanumérico: {'🟢' if mode_st == 1 else '🔴'}"
    mode_cb = f"togmode_off_{group_id}_{lang}" if mode_st == 1 else f"togmode_on_{group_id}_{lang}"

    time_label = f"⏱️ Time: {cfg['time']}s" if lang == "en" else f"⏱️ Tiempo: {cfg['time']}s"
    action_label = f"⚖️ Punishment: {cfg['action'].upper()}" if lang == "en" else f"⚖️ Castigo: {cfg['action'].upper()}"
    srv_label = f"🗑️ Service Msg: {'🟢' if cfg['service_del'] == 1 else '🔴'}" if lang == "en" else f"🗑️ Serv. Msg: {'🟢' if cfg['service_del'] == 1 else '🔴'}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_btn, callback_data=st_cb)],
        [InlineKeyboardButton(text=mode_btn, callback_data=mode_cb)],
        [
            InlineKeyboardButton(text=time_label, callback_data=f"cap_set_time_{group_id}_{lang}"),
            InlineKeyboardButton(text=action_label, callback_data=f"cap_set_action_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="✍️ Custom Text" if lang == "en" else "✍️ Mensaje", callback_data=f"cap_set_text_{group_id}_{lang}"),
            InlineKeyboardButton(text=srv_label, callback_data=f"cap_set_srvdel_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

def get_captcha_time_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱️ 30s", callback_data=f"capval_time_30_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️ 1m", callback_data=f"capval_time_60_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⏱️ 3m", callback_data=f"capval_time_180_{group_id}_{lang}"),
            InlineKeyboardButton(text="⏱️ 5m", callback_data=f"capval_time_300_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver a Captcha"), callback_data=f"gset_captcha_{group_id}_{lang}")]
    ])

async def get_captcha_action_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_captcha_config(group_id)
    current_action = cfg["action"]
    
    kick_status = "🟢" if current_action == "kick" else "🔴"
    mute_status = "🟢" if current_action == "mute" else "🔴"

    kick_text = f"👢 Kick {kick_status}" if lang == "en" else f"👢 Expulsar {kick_status}"
    mute_text = f"🔇 Mute {mute_status}" if lang == "en" else f"🔇 Silenciar {mute_status}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=kick_text, callback_data=f"capval_action_kick_{group_id}_{lang}"),
            InlineKeyboardButton(text=mute_text, callback_data=f"capval_action_mute_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver a Captcha"), callback_data=f"gset_captcha_{group_id}_{lang}")]
    ])

async def get_antispam_text(group_id: int, lang: str) -> str:
    t = TEXTS.get(lang, TEXTS["es"])
    st_tg = "🟢" if await get_antispam_filter(group_id, "tg_links") == 1 else "🔴"
    st_fwd = "🟢" if await get_antispam_filter(group_id, "forwards") == 1 else "🔴"
    st_q = "🟢" if await get_antispam_filter(group_id, "quotes") == 1 else "🔴"
    st_web = "🟢" if await get_antispam_filter(group_id, "web_links") == 1 else "🔴"
    del_st = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    return t["antispam_main_title"].format(st_tg=st_tg, st_fwd=st_fwd, st_q=st_q, st_web=st_web, st_del=del_st)

async def get_antispam_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    st_tg = "🟢" if await get_antispam_filter(group_id, "tg_links") == 1 else "🔴"
    st_fwd = "🟢" if await get_antispam_filter(group_id, "forwards") == 1 else "🔴"
    st_q = "🟢" if await get_antispam_filter(group_id, "quotes") == 1 else "🔴"
    st_web = "🟢" if await get_antispam_filter(group_id, "web_links") == 1 else "🔴"
    del_st = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"

    tg_lbl = "📘 Telegram Links" if lang == "en" else "📘 Enlaces Telegram"
    fwd_lbl = "📥 Forwards Shield" if lang == "en" else "📥 Escudo Reenvíos"
    q_lbl = "💭 Quotes Filter" if lang == "en" else "💭 Filtro Citas"
    web_lbl = "🔗 Internet Links" if lang == "en" else "🔗 Enlaces Internet"
    purge_lbl = "🗑️ Delete Spam" if lang == "en" else "🗑️ Borrar Spam"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{tg_lbl} {st_tg}", callback_data=f"astog_tglinks_{group_id}_{lang}"), InlineKeyboardButton(text=f"{fwd_lbl} {st_fwd}", callback_data=f"as_fwd_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{q_lbl} {st_q}", callback_data=f"astog_quotes_{group_id}_{lang}"), InlineKeyboardButton(text=f"{web_lbl} {st_web}", callback_data=f"astog_weblinks_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{purge_lbl} {del_st}", callback_data=f"as_togdel_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

async def get_forwards_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    st_chan = "🟢" if await get_antispam_filter(group_id, "fwd_channels") == 1 else "🔴"
    st_usr = "🟢" if await get_antispam_filter(group_id, "fwd_users") == 1 else "🔴"
    st_grp = "🟢" if await get_antispam_filter(group_id, "fwd_groups") == 1 else "🔴"
    st_bot = "🟢" if await get_antispam_filter(group_id, "fwd_bots") == 1 else "🔴"

    chan_lbl = "📢 Channels" if lang == "en" else "📢 Canales"
    usr_lbl = "👤 Users" if lang == "en" else "👤 Usuarios"
    grp_lbl = "👥 Groups" if lang == "en" else "👥 Grupos"
    bot_lbl = "🤖 Bots" if lang == "en" else "🤖 Bots"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{chan_lbl} {st_chan}", callback_data=f"astog_fwdchan_{group_id}_{lang}"), InlineKeyboardButton(text=f"{usr_lbl} {st_usr}", callback_data=f"astog_fwdusr_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{grp_lbl} {st_grp}", callback_data=f"astog_fwdgrp_{group_id}_{lang}"), InlineKeyboardButton(text=f"{bot_lbl} {st_bot}", callback_data=f"astog_fwdbot_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t.get("btn_back_antispam", "🔙 Volver a Anti-Spam"), callback_data=f"gset_antispam_{group_id}_{lang}")]
    ])

async def get_locks_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    media = "🟢" if await get_lock_status(group_id, "lock_media") == 1 else "🔴"
    stickers = "🟢" if await get_lock_status(group_id, "lock_stickers") == 1 else "🔴"
    links = "🟢" if await get_lock_status(group_id, "lock_links") == 1 else "🔴"
    commands = "🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "🔴"

    media_lbl = "🖼️ Media (Photos/Videos)" if lang == "en" else "🖼️ Multimedia (Fotos/Videos)"
    stickers_lbl = "🎨 Stickers & GIFs" if lang == "en" else "🎨 Stickers y GIFs"
    links_lbl = "🔗 Web Links" if lang == "en" else "🔗 Enlaces Web"
    cmds_lbl = "⚙️ Bot Commands" if lang == "en" else "⚙️ Comandos de Bots"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{media_lbl} {media}", callback_data=f"toglock_media_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{stickers_lbl} {stickers}", callback_data=f"toglock_stickers_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{links_lbl} {links}", callback_data=f"toglock_links_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{cmds_lbl} {commands}", callback_data=f"toglock_commands_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

async def get_warns_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    cfg = await get_warns_config(group_id)
    limit = cfg["limit"]
    action = cfg["action"].upper()

    limit_lbl = f"🔢 Strike Limit: {limit}" if lang == "en" else f"🔢 Límite: {limit} Faltas"
    action_lbl = f"⚖️ Punishment: {action}" if lang == "en" else f"⚖️ Castigo: {action}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=limit_lbl, callback_data=f"warnset_limit_{group_id}_{lang}"),
            InlineKeyboardButton(text=action_lbl, callback_data=f"warnset_action_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

async def get_delmsgs_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_group_tier(group_id)
    cfg = await get_captcha_config(group_id)
    
    srv_del = "🟢" if cfg["service_del"] == 1 else "🔴"
    main_chat = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    block_cmds = "🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "🔴"

    if tier == "free":
        tier_text = "⭐ Tier: FREE (3 purges/day)" if lang == "en" else "⭐ Plan: BÁSICO (3 purgas/día)"
        pro_btn = "⭐ Mejorar a PRO" if lang == "es" else "⭐ Upgrade PRO"
        ultra_btn = "💎 Mejorar a ULTRA" if lang == "es" else "💎 Upgrade ULTRA"
        
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{'🗑️ Service Msgs' if lang == 'en' else '🗑️ Msgs de Servicio'} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [
                InlineKeyboardButton(text=pro_btn, callback_data=f"pay_pro_{group_id}_{lang}"),
                InlineKeyboardButton(text=ultra_btn, callback_data=f"pay_ultra_{group_id}_{lang}")
            ],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        ])
    else:
        tier_text = f"⭐ Tier: {tier.upper()} (Advanced Unlocked)" if lang == "en" else f"⭐ Plan: {tier.upper()} (Avanzado Desbloqueado)"
        purge_lbl = "🗑️ Purge Service Msgs" if lang == "en" else "🗑️ Purgar Mensajes de Servicio"
        main_chat_lbl = "🧹 Main Chat Cleanup" if lang == "en" else "🧹 Limpieza Chat Principal"
        cmd_block_lbl = "🛡️ Block Cmds to Regulars" if lang == "en" else "🛡️ Bloquear Cmds a Regulares"

        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{purge_lbl} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{main_chat_lbl} {main_chat}", callback_data=f"delmsgs_main_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{cmd_block_lbl} {block_cmds}", callback_data=f"delmsgs_cmds_{group_id}_{lang}")],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        ])

def get_antiflood_keyboard(group_id: int, lang: str, cfg: dict):
    t = TEXTS.get(lang, TEXTS["es"])
    del_st = "🟢" if cfg["delete"] == 1 else "🔴"
    curr_act = cfg["action"]

    def act_badge(key: str, label: str):
        return f"{label} {'🟢' if curr_act == key else ''}".strip()

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{t['af_msgs']}: {cfg['msgs']}", callback_data=f"afset_msgs_{group_id}_{lang}"),
            InlineKeyboardButton(text=f"{t['af_time']}: {cfg['time']}s", callback_data=f"afset_time_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=act_badge("off", t["af_off"]), callback_data=f"afact_off_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("warn", t["af_warn"]), callback_data=f"afact_warn_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=act_badge("kick", t["af_kick"]), callback_data=f"afact_kick_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("mute", t["af_mute"]), callback_data=f"afact_mute_{group_id}_{lang}"),
            InlineKeyboardButton(text=act_badge("ban", t["af_ban"]), callback_data=f"afact_ban_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=f"{t['af_del_msgs']} {del_st}", callback_data=f"afact_togdel_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

def get_antiflood_number_keyboard(group_id: int, lang: str, mode: str):
    t = TEXTS.get(lang, TEXTS["es"])
    numbers = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
    kb = []
    row = []
    for num in numbers:
        row.append(InlineKeyboardButton(text=str(num), callback_data=f"afval_{mode}_{num}_{group_id}_{lang}"))
        if len(row) == 4:
            kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton(text=t.get("btn_back_antiflood", "🔙 Volver a Anti-Flood"), callback_data=f"gset_antiflood_{group_id}_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_mod_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 /ban", callback_data=f"cmd_ban_{group_id}_{lang}"), InlineKeyboardButton(text="👢 /kick", callback_data=f"cmd_kick_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🔇 /mute", callback_data=f"cmd_mute_{group_id}_{lang}"), InlineKeyboardButton(text="🔊 /unmute", callback_data=f"cmd_unmute_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚪ Whitelist", callback_data=f"cmd_wl_{group_id}_{lang}"), InlineKeyboardButton(text="⚫ Blacklist", callback_data=f"cmd_bl_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

def get_time_selection_keyboard(action_name: str, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ 1 Min", callback_data=f"time_{action_name}_1m_{group_id}_{lang}"), InlineKeyboardButton(text="⏱️ 10 Min", callback_data=f"time_{action_name}_10m_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⏰ 1 Hora" if lang == "es" else "⏰ 1 Hour", callback_data=f"time_{action_name}_1h_{group_id}_{lang}"), InlineKeyboardButton(text="⏰ 24 Horas" if lang == "es" else "⏰ 24 Hours", callback_data=f"time_{action_name}_24h_{group_id}_{lang}")],
        [InlineKeyboardButton(text="📅 30 Días" if lang == "es" else "📅 30 Days", callback_data=f"time_{action_name}_30d_{group_id}_{lang}"), InlineKeyboardButton(text="♾️ Permanente" if lang == "es" else "♾️ Permanent", callback_data=f"time_{action_name}_perm_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
    ])

def get_eco_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Radar Ecosistema", callback_data=f"radar_eco_{group_id}"), InlineKeyboardButton(text="🎥 /cams", callback_data=f"cmd_cams_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚙️ /autolower", callback_data=f"cmd_autolower_{group_id}_{lang}"), InlineKeyboardButton(text="🗓️ Programador VC", callback_data=f"vcsched_menu_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="🎙️ /mic_vip", callback_data=f"cmd_mic_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️ Etiqueta VIP", callback_data=f"cmd_mictag_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

async def get_clone_keyboard(group_id: int, user_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    if tier == "ultra_pro":
        session_info = await get_owner_session(user_id, group_id)
        has_sentinel = session_info is not None
        
        sentinel_btn_text = "🔄 Actualizar Centinela (Sesión)" if has_sentinel else "🎙️ Conectar Centinela Propio"
        if lang == "en":
            sentinel_btn_text = "🔄 Update Sentinel (Session)" if has_sentinel else "🎙️ Connect Own Sentinel"

        kb = [
            [InlineKeyboardButton(text="🔑 Conectar Token @BotFather" if lang == "es" else "🔑 Connect @BotFather Token", callback_data=f"clone_token_{group_id}_{lang}")],
            [InlineKeyboardButton(text=sentinel_btn_text, callback_data=f"clone_sentinel_{group_id}_{lang}")]
        ]

        if has_sentinel:
            disc_text = "🛑 Desconectar Centinela" if lang == "es" else "🛑 Disconnect Sentinel"
            kb.append([InlineKeyboardButton(text=disc_text, callback_data=f"clone_discsentinel_{group_id}_{lang}")])

        kb.append([InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Unlock with ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        ])
    # ==========================================
# 🚀 ENRUTAMIENTO Y MANEJADORES EN PRIVADO
# ==========================================
@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, bot: Bot, command: CommandObject):
    lang = "es" 
    t = TEXTS.get(lang, TEXTS["es"])
    
    await get_or_create_user(message.from_user.id, message.from_user.username or "Sin username", message.from_user.full_name)

    if command.args and command.args.startswith("gset_"):
        try:
            group_id = int(command.args.split("_")[1])
            if not await verify_admin_privileges_msg(message, bot, group_id):
                return
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad"
            await message.answer(t["group_panel_title"].format(group_name=g_name), reply_markup=get_group_panel_keyboard(group_id, lang), parse_mode="HTML")
            return
        except Exception:
            pass

    await message.answer(t["welcome"].format(name=message.from_user.full_name), reply_markup=get_main_keyboard((await bot.get_me()).username, lang), parse_mode="HTML")

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()

@router.message(F.chat.type == "private")
async def handle_private_inputs(message: Message, bot: Bot):
    user_id = message.from_user.id
    text_input = (message.text or "").strip()

    if user_id in CAPTCHA_STATES:
        group_id = CAPTCHA_STATES.pop(user_id)
        lang = "es"
        await set_captcha_config(group_id, "captcha_text", text_input)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver al Menú de Aduana", callback_data=f"gset_captcha_{group_id}_{lang}")]
        ])
        await message.answer(
            f"✅ <b>¡Mensaje personalizado de Captcha guardado con éxito!</b>\n\n<i>{text_input}</i>\n\n🛡️ <i>Cloud Media Management</i>",
            reply_markup=back_kb,
            parse_mode="HTML"
        )
        return

    if user_id in CLONE_STATES:
        group_id = CLONE_STATES.pop(user_id)
        token = text_input
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Panel de Clonador", callback_data=f"gset_clone_{group_id}_es")]
        ])
        if ":" in token and len(token) > 30:
            await register_bot_clone(user_id, group_id, token, "")
            await message.answer(
                "✅ <b>¡Token recibido y verificado correctamente!</b>\nInstancia de réplica conectada a la base de datos de The Bunker.\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "❌ <b>Token inválido.</b> Asegúrate de copiar el token completo generado por @BotFather.\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if user_id in SENTINEL_STATES:
        group_id = SENTINEL_STATES.pop(user_id)
        session_str = text_input
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver al Panel", callback_data=f"gset_clone_{group_id}_es")]
        ])

        if len(session_str) > 50:
            status_msg = await message.answer("🔄 <b>Verificando credenciales y conectando Centinela...</b>", parse_mode="HTML")
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                await message.answer(
                    "💎 <b>¡Centinela Propio Conectado con Éxito!</b>\n\n"
                    "• <b>Comunidad:</b> Blindada con tu propia cuenta\n"
                    "• <b>Radar de Transmisiones:</b> Activo 24/7 en la nube\n"
                    "• <b>Aislamiento Total:</b> Operando sin riesgo de baneo global\n\n"
                    "<i>Asegúrate de haber añadido tu cuenta al grupo con permiso de Administrar Videollamadas.</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            else:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                await message.answer(
                    "❌ <b>Error al inicializar la sesión:</b>\n\n"
                    "La cadena de sesión ingresada no es válida o fue revocada. Genera una nueva String Session de Pyrogram e inténtalo nuevamente.\n\n"
                    "🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
        else:
            await message.answer(
                "❌ <b>Formato no reconocido:</b> Envía una String Session de Pyrogram válida.\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if user_id in VC_SCHED_STATES:
        sched_data = VC_SCHED_STATES.pop(user_id)
        group_id = sched_data["group_id"]
        lang = sched_data["lang"]
        mode = sched_data["mode"]
        
        current_sched = await get_vc_schedule(group_id)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver al Programador VC", callback_data=f"vcsched_menu_{group_id}_{lang}")]
        ])

        if mode == "times":
            if "-" in text_input and len(text_input.split("-")) == 2:
                parts = text_input.split("-")
                start = parts[0].strip()
                end = parts[1].strip()
                await set_vc_schedule(group_id, current_sched["days"], start, end, current_sched["status"])
                await message.answer(
                    f"✅ <b>¡Horario Actualizado!</b>\n\n• Inicio: <code>{start}</code>\n• Cierre: <code>{end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    "⚠️ Formato incorrecto. Usa el formato HH:MM-HH:MM (Ejemplo: <code>20:00-23:30</code>).\n\n🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            return

    if user_id in DB_REG_STATES:
        data = DB_REG_STATES.pop(user_id)
        reg_type = data["type"]
        group_id = data["group_id"]
        lang = data["lang"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver a Moderación", callback_data=f"menu_mod_{group_id}_{lang}")]
        ])

        if reg_type == "wl":
            target_id = None
            if text_input.isdigit():
                target_id = int(text_input)
            elif text_input.startswith("@"):
                try:
                    c = await bot.get_chat(text_input)
                    target_id = c.id
                except Exception:
                    target_id = None
            
            if target_id:
                await add_to_whitelist(target_id)
                await message.answer(
                    f"✅ <b>¡Usuario registrado en la Whitelist con éxito!</b>\n\n"
                    f"• <b>Identificador:</b> <code>{target_id}</code>\n"
                    f"• <b>Inmunidad Táctica:</b> ACTIVA 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    "⚠️ <b>Identificador no válido.</b> Envía la ID numérica del usuario a autorizar.\n\n🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
        else:
            word = text_input.lower()
            await add_to_blacklist(word)
            await message.answer(
                f"✅ <b>¡Término clasificado registrado en la Blacklist con éxito!</b>\n\n"
                f"• <b>Término Prohibido:</b> <code>{word}</code>\n"
                f"• <b>Protocolo:</b> Purga automática y advertencias tácticas activas 🔴\n\n"
                f"🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if user_id in MOD_TARGET_STATES:
        st = MOD_TARGET_STATES.pop(user_id)
        action = st["action"]
        group_id = st["group_id"]
        duration = st["duration"]
        dur_label = st["dur_label"]
        lang = st["lang"]

        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver a Moderación", callback_data=f"menu_mod_{group_id}_{lang}")]
        ])

        target_id = None
        if text_input.isdigit():
            target_id = int(text_input)
        elif text_input.startswith("@"):
            try:
                c = await bot.get_chat(text_input)
                target_id = c.id
            except Exception:
                target_id = None

        if not target_id:
            await message.answer(
                "⚠️ <b>Identificador inválido.</b> Envía la ID numérica del usuario a sancionar.\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
            return

        try:
            if action == "ban":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until if until > 0 else None)
                await message.answer(
                    f"🚫 <b>¡Directiva de Baneo Ejecutada Remotamente!</b>\n\n"
                    f"• <b>Usuario ID:</b> <code>{target_id}</code>\n"
                    f"• <b>Duración:</b> {dur_label}\n"
                    f"• <b>Estado:</b> Erradicado de la comunidad 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            elif action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=target_id)
                await message.answer(
                    f"👢 <b>¡Directiva de Expulsión Ejecutada Remotamente!</b>\n\n"
                    f"• <b>Usuario ID:</b> <code>{target_id}</code>\n"
                    f"• <b>Estado:</b> Expulsado de la comunidad 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            elif action == "mute":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=target_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until if until > 0 else None
                )
                await message.answer(
                    f"🔇 <b>¡Directiva de Silencio Ejecutada Remotamente!</b>\n\n"
                    f"• <b>Usuario ID:</b> <code>{target_id}</code>\n"
                    f"• <b>Duración:</b> {dur_label}\n"
                    f"• <b>Estado:</b> Micrófono y texto restringidos 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            elif action == "unmute":
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=target_id,
                    permissions=ChatPermissions(
                        can_send_messages=True, can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                )
                await message.answer(
                    f"🔊 <b>¡Permisos de Voz y Chat Restaurados!</b>\n\n"
                    f"• <b>Usuario ID:</b> <code>{target_id}</code>\n"
                    f"• <b>Estado:</b> Conectado con facultades completas 🟢\n\n"
                    f"🛡️ <i>Cloud Media Management</i>",
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
        except Exception as ex:
            await message.answer(
                f"❌ <b>Error al ejecutar directiva:</b>\n<code>{ex}</code>\n\n"
                f"Asegúrate de que el bot tenga permisos de administrador en la comunidad seleccionada.",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if user_id in MIC_VIP_STATES:
        data = MIC_VIP_STATES.pop(user_id)
        group_id = data["group_id"]
        lang = data["lang"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            price_val = int(text_input)
            GROUP_MIC_PRICE[group_id] = price_val
            await message.answer(
                f"⭐ <b>¡Tarifa VIP de Micrófono Actualizada!</b>\n\n"
                f"• <b>Comunidad ID:</b> <code>{group_id}</code>\n"
                f"• <b>Precio por Pase 24h:</b> <code>{price_val} Stars (XTR)</code> 🟢\n\n"
                f"🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "⚠️ Ingresa un número entero positivo de Stars (por ejemplo: 50).\n\n🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if user_id in MIC_TAG_STATES:
        data = MIC_TAG_STATES.pop(user_id)
        group_id = data["group_id"]
        lang = data["lang"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        
        # Validación de longitud: Telegram permite hasta 16 caracteres para custom_title de admin
        if 1 <= len(text_input) <= 16:
            GROUP_VIP_TAG[group_id] = text_input
            await message.answer(
                f"🏷️ <b>¡Etiqueta VIP Actualizada con Éxito!</b>\n\n"
                f"• <b>Comunidad ID:</b> <code>{group_id}</code>\n"
                f"• <b>Etiqueta Nativa Asignada:</b> <code>{text_input}</code> 🟢\n\n"
                f"<i>Al recibir propina de Stars o ejecutar /mic_vip, el usuario recibirá este título inamovible de forma automática.</i>\n\n"
                f"🛡️ <i>Cloud Media Management</i>",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "⚠️ La etiqueta debe tener entre 1 y 16 caracteres (límite oficial de Telegram para títulos de administrador).\n\n"
                "Ejemplo: <code>VIP 24/7</code> o <code>VIP Gold</code>.",
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

@router.callback_query(F.data.startswith("menu_") | F.data.startswith("lang_") | F.data.startswith("langpanel_") | F.data.startswith("gpanel_") | F.data.startswith("cmd_") | F.data.startswith("pay_") | F.data.startswith("time_") | F.data.startswith("clone_") | F.data.startswith("alset_") | F.data.startswith("micval_") | F.data.startswith("reg_") | F.data.startswith("vcsched_"))
async def process_menu_navigation(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    
    # Limpieza preventiva de estados si el usuario navega a otra sección
    for state_dict in [CAPTCHA_STATES, CLONE_STATES, SENTINEL_STATES, VC_SCHED_STATES, DB_REG_STATES, MOD_TARGET_STATES, MIC_VIP_STATES, MIC_TAG_STATES]:
        state_dict.pop(callback.from_user.id, None)

    data = callback.data.split("_")
    action = data[0] 
    lang = data[-1] if len(data) > 1 and data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None

    if action == "lang":
        lang = data[1]
        text = TEXTS.get(lang, TEXTS["es"])["welcome"].format(name=callback.from_user.full_name)
        keyboard = get_main_keyboard((await bot.get_me()).username, lang)

    elif action == "langpanel":
        group_id = int(data[1])
        lang = data[2]
        t = TEXTS.get(lang, TEXTS["es"])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad"
        text = t["group_panel_title"].format(group_name=g_name)
        keyboard = get_group_panel_keyboard(group_id, lang)
        
    elif action == "menu":
        target = data[1]
        if target == "main":
            text = t["welcome"].format(name=callback.from_user.full_name)
            keyboard = get_main_keyboard((await bot.get_me()).username, lang)
        elif target == "settings":
            active_groups = await get_active_user_groups(bot, callback.from_user.id)
            text, keyboard = t["settings_main"], get_groups_keyboard(active_groups, lang)
        elif target == "support":
            text, keyboard = t["support_main"], get_support_keyboard(lang)
        elif target == "info":
            text, keyboard = t["info_main"], get_info_keyboard(lang)
        elif target == "infohow":
            text, keyboard = t["info_how_main"], InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_info_{lang}")]
            ])
        elif target == "id":
            user, tier_db = callback.from_user, await get_user_global_tier(callback.from_user.id)
            if is_super_admin(user.id):
                rank_str = "Arquitecto Supremo (Inmunidad Total) ⚡"
            else:
                rank_str = "Comandante ULTRA 💎" if tier_db == "ultra_pro" else ("Comandante PRO ⭐" if tier_db == "pro" else "Comandante (Free)")
            text, keyboard = t["id_status"].format(id=user.id, username=user.username or "N/A", rank=rank_str), get_simple_back_keyboard(lang)
        elif target in ["mod", "eco"]:
            group_id = int(data[2])
            if not await verify_admin_privileges(callback, bot, group_id):
                return
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad"
            text = t[f"{target}_main"].format(group_name=g_name)
            keyboard = get_mod_keyboard(group_id, lang) if target == "mod" else get_eco_keyboard(group_id, lang)
            
    elif action == "pay":
        tier_level = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad"
        text = t[f"pay_{tier_level}_title"].format(group_name=g_name)
        keyboard = get_payment_keyboard(group_id, lang, tier_level=tier_level)

    elif action == "vcsched":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            sched_lock_text = (
                "🗓️ <b>Programador Automático de Videochats (ULTRA PRO)</b>\n\n"
                "Apertura y cierre automático de tus transmisiones según los días y horas que elijas, con optimización continua en segundo plano para evitar cámaras congeladas.\n\n"
                "🔒 <i>Esta función es de automatización avanzada y está disponible exclusivamente en el nivel ULTRA PRO.</i>\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                "🗓️ <b>Automated VC Scheduler (ULTRA PRO)</b>\n\n"
                "Automated opening and closing of your community live streams on the days and hours you set, including continuous background refresh to prevent frozen camera feeds.\n\n"
                "🔒 <i>This automated module is exclusively unlocked at the ULTRA PRO tier.</i>\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
            try:
                await callback.message.edit_text(sched_lock_text, reply_markup=keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                pass
            return

        if sub == "menu":
            sched = await get_vc_schedule(group_id)
            st_badge = "🟢 ACTIVADO" if sched["status"] == 1 else "🔴 DESACTIVADO"
            text = (
                "🗓️ <b>Programador de Videochats (ULTRA PRO)</b>\n\n"
                "Configura la apertura y cierre automático de tus salas de voz:\n\n"
                f"• <b>Estado:</b> {st_badge}\n"
                f"• <b>Días Activos:</b> <code>{sched['days']}</code>\n"
                f"• <b>Horario:</b> <code>{sched['start_time']} - {sched['end_time']}</code>\n\n"
                "Selecciona una opción para modificar los parámetros:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            toggle_text = "🔴 Desactivar Cronograma" if sched["status"] == 1 else "🟢 Activar Cronograma"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=toggle_text, callback_data=f"vcsched_toggle_{group_id}_{lang}")],
                [InlineKeyboardButton(text="⏰ Modificar Horario (HH:MM-HH:MM)", callback_data=f"vcsched_timeprompt_{group_id}_{lang}")],
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub == "toggle":
            sched = await get_vc_schedule(group_id)
            new_st = 0 if sched["status"] == 1 else 1
            await set_vc_schedule(group_id, sched["days"], sched["start_time"], sched["end_time"], new_st)
            
            sched = await get_vc_schedule(group_id)
            st_badge = "🟢 ACTIVADO" if sched["status"] == 1 else "🔴 DESACTIVADO"
            text = (
                "🗓️ <b>Programador de Videochats (ULTRA PRO)</b>\n\n"
                "Configura la apertura y cierre automático de tus salas de voz:\n\n"
                f"• <b>Estado:</b> {st_badge}\n"
                f"• <b>Días Activos:</b> <code>{sched['days']}</code>\n"
                f"• <b>Horario:</b> <code>{sched['start_time']} - {sched['end_time']}</code>\n\n"
                "Selecciona una opción para modificar los parámetros:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            toggle_text = "🔴 Desactivar Cronograma" if sched["status"] == 1 else "🟢 Activar Cronograma"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=toggle_text, callback_data=f"vcsched_toggle_{group_id}_{lang}")],
                [InlineKeyboardButton(text="⏰ Modificar Horario (HH:MM-HH:MM)", callback_data=f"vcsched_timeprompt_{group_id}_{lang}")],
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub == "timeprompt":
            VC_SCHED_STATES[callback.from_user.id] = {"group_id": group_id, "lang": lang, "mode": "times"}
            await callback.message.answer(
                "⏰ <b>Configuración de Horario VC</b>\n\n"
                "Envía a este chat privado el intervalo de apertura y cierre en formato 24h (ejemplo: <code>20:00-23:30</code>):\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return

    elif action == "clone":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        if sub == "token":
            CLONE_STATES[callback.from_user.id] = group_id
            await callback.message.answer(
                "🔑 <b>Conexión de Clon — Token de BotFather</b>\n\n"
                "Envía a este chat privado el <b>HTTP API Token</b> de tu bot generado en @BotFather.\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return
        elif sub == "sentinel":
            SENTINEL_STATES[callback.from_user.id] = group_id
            await callback.message.answer(
                "🎙️ <b>Conexión de Centinela Propio (Videochats 24/7)</b>\n\n"
                "Para vincular tu propia cuenta y blindar tus salas de voz sin intermediarios:\n\n"
                "Envía a este chat privado tu <b>StringSession de Pyrogram</b> generada con tu cuenta.\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return
        elif sub == "discsentinel":
            await disconnect_sentinel(group_id)
            await revoke_owner_session(callback.from_user.id, group_id)
            await callback.answer("🛑 Centinela propio desconectado con éxito.", show_alert=True)
            
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad"
            status = "Operativo 🟢" if tier == "ultra_pro" else "Bloqueado 🔴"
            sentinel_status = "Desconectado 🔴"
            
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang)

    elif action == "cmd":
        sub_cmd = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        if sub_cmd == "wl":
            text = (
                "⚪ <b>Directiva: Lista Blanca Táctica (Whitelist)</b>\n\n"
                "Las identidades registradas reciben <b>Inmunidad Absoluta</b>. Ningún filtro anti-spam, cerradura o captcha los confinará.\n\n"
                "• <b>Inmunidad Táctica:</b> ACTIVA 🟢\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Registrar Usuario en BD", callback_data=f"reg_wl_{group_id}_{lang}")],
                [InlineKeyboardButton(text="🔙 Volver al Panel", callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "bl":
            text = (
                "⚫ <b>Directiva: Lista Negra Global (Blacklist)</b>\n\n"
                "Glosario clasificado de términos prohibidos. Cualquier coincidencia en el chat activará purga inmediata y advertencias tácticas.\n\n"
                "• <b>Protocolo:</b> Purga automática y faltas (Warns) 🔴\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Registrar Término en BD", callback_data=f"reg_bl_{group_id}_{lang}")],
                [InlineKeyboardButton(text="🔙 Volver al Panel", callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "cams":
            text = (
                "📹 <b>Supervisión y Control de Cámaras & Videochats</b>\n\n"
                "Auditoría en vivo de estabilidad de transmisiones:\n\n"
                "• <b>Calidad de Video:</b> Alta Fidelidad y fluidez continua 🟢\n"
                "• <b>Prioridad de Transmisión:</b> Óptima (Sin cortes ni sobrecargas)\n"
                "• <b>Optimización Audiovisual Continua:</b> Activa en segundo plano para evitar cámaras congeladas y pantallas negras.\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "autolower":
            curr_al = await get_autolower_status(group_id)
            status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
            text = (
                "⚙️ <b>Control Remoto: AutoLower de Videollamada</b>\n\n"
                "El Centinela atenúa el micrófono de los miembros no autorizados para mantener la sala en orden absoluto:\n\n"
                f"• <b>Estado Actual:</b> {status_str}\n\n"
                "Selecciona una directiva para cambiar el comportamiento en vivo:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🟢 Activar AutoLower (2%)", callback_data=f"alset_1_{group_id}_{lang}"),
                    InlineKeyboardButton(text="🔴 Desactivar (Libre)", callback_data=f"alset_0_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mic":
            curr_price = GROUP_MIC_PRICE.get(group_id, 50)
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            text = (
                "🎙️ <b>Pase VIP de Micrófono (Monetización en Stars)</b>\n\n"
                "Permite a los miembros desbloquear su voz al 100% continuo durante 24h pagando Telegram Stars (XTR).\n\n"
                "💡 <i>Ventaja Clave:</i> Al desplegar tu propio <b>Bot Clone</b> y <b>Centinela Dedicado</b>, el 100% de las Stars recaudadas por pases VIP van <b>directas a la cuenta de tu bot</b>, monetizando tu comunidad de forma totalmente automatizada.\n\n"
                f"• <b>Tarifa Actual:</b> <code>{curr_price} Stars (XTR)</code> 🟢\n"
                f"• <b>Etiqueta Asignada:</b> <code>{curr_tag}</code> (Nativa e inamovible)\n"
                "• <b>Duración del Pase:</b> 24 Horas automáticas\n\n"
                "Selecciona la tarifa de cobro o personaliza la etiqueta:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text="✍️ Tarifa Personalizada", callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text=f"🏷️ Etiqueta: {curr_tag}", callback_data=f"cmd_mictag_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mictag":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                await callback.answer("💎 La edición de etiqueta nativa requiere nivel ULTRA PRO.", show_alert=True)
                return
            
            MIC_TAG_STATES[callback.from_user.id] = {"group_id": group_id, "lang": lang}
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            await callback.message.answer(
                "🏷️ <b>Editor de Etiqueta VIP Nativa (ULTRA PRO)</b>\n\n"
                f"Etiqueta actual: <code>{curr_tag}</code>\n\n"
                "Envía en este chat privado el texto que deseas asignar automáticamente como título de administrador (máximo 16 caracteres).\n\n"
                "<i>Ejemplo: VIP 24/7, VIP Elite, Sponsor</i>\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return
        elif sub_cmd in ["ban", "mute"]:
            text = f"⚡ <b>Directiva de Moderación: /{sub_cmd}</b>\n\nSelecciona la duración de la directiva sobre el usuario:"
            keyboard = get_time_selection_keyboard(sub_cmd, group_id, lang)
        elif sub_cmd in ["kick", "unmute"]:
            MOD_TARGET_STATES[callback.from_user.id] = {
                "action": sub_cmd, "group_id": group_id, "duration": 0, 
                "dur_label": "Inmediato", "lang": lang
            }
            await callback.message.answer(
                f"🎯 <b>Configuración de Objetivo — /{sub_cmd.upper()}</b>\n\n"
                f"Envía a este chat privado el <b>@usuario</b> o la <b>ID numérica</b> del miembro a ejecutar:\n\n"
                f"🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return

    elif action == "reg":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        DB_REG_STATES[callback.from_user.id] = {"type": sub, "group_id": group_id, "lang": lang}
        target_name = "la ID del usuario para Whitelist" if sub == "wl" else "el término prohibido para Blacklist"
        await callback.message.answer(
            f"📝 <b>Registro en Base de Datos</b>\n\n"
            f"Envía en este chat privado {target_name}:\n\n"
            f"🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )
        return

    elif action == "alset":
        new_st = int(data[1])
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        await set_autolower_status(group_id, new_st)
        await callback.answer("AutoLower actualizado 🟢" if new_st == 1 else "AutoLower desactivado 🔴")
        curr_al = await get_autolower_status(group_id)
        status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
        text = (
            "⚙️ <b>Control Remoto: AutoLower de Videollamada</b>\n\n"
            "El Centinela atenúa el micrófono de los miembros no autorizados para mantener la sala en orden absoluto:\n\n"
            f"• <b>Estado Actual:</b> {status_str}\n\n"
            "Selecciona una directiva para cambiar el comportamiento en vivo:\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Activar AutoLower (2%)", callback_data=f"alset_1_{group_id}_{lang}"),
                InlineKeyboardButton(text="🔴 Desactivar (Libre)", callback_data=f"alset_0_{group_id}_{lang}")
            ],
            [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
        ])

    elif action == "micval":
        sub_val = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        if sub_val == "custom":
            MIC_VIP_STATES[callback.from_user.id] = {"group_id": group_id, "lang": lang}
            await callback.message.answer(
                "⭐ <b>Tarifa Personalizada de Stars</b>\n\n"
                "Envía en este chat privado el número de Stars por pase VIP de 24h (ejemplo: 75).\n\n"
                "<i>Recuerda que con tu Bot Clone las Stars ingresan de forma directa a tu balance.</i>\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
            return
        else:
            price_int = int(sub_val)
            GROUP_MIC_PRICE[group_id] = price_int
            await callback.answer(f"Tarifa configurada a {price_int} Stars ⭐", show_alert=True)
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            text = (
                "🎙️ <b>Pase VIP de Micrófono (Monetización en Stars)</b>\n\n"
                "Permite a los miembros desbloquear su voz al 100% continuo durante 24h pagando Telegram Stars (XTR).\n\n"
                "💡 <i>Ventaja Clave:</i> Al desplegar tu propio <b>Bot Clone</b> y <b>Centinela Dedicado</b>, el 100% de las Stars recaudadas por pases VIP van <b>directas a la cuenta de tu bot</b>, monetizando tu comunidad de forma totalmente automatizada.\n\n"
                f"• <b>Tarifa Actual:</b> <code>{price_int} Stars (XTR)</code> 🟢\n"
                f"• <b>Etiqueta Asignada:</b> <code>{curr_tag}</code> (Nativa e inamovible)\n"
                "• <b>Duración del Pase:</b> 24 Horas automáticas\n\n"
                "Selecciona la tarifa de cobro o personaliza la etiqueta:\n\n"
                "🛡️ <i>Cloud Media Management</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text="✍️ Tarifa Personalizada", callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text=f"🏷️ Etiqueta: {curr_tag}", callback_data=f"cmd_mictag_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text="🔙 Volver a Ecosistema", callback_data=f"menu_eco_{group_id}_{lang}")]
            ])

    elif action == "time":
        sub_cmd = data[1]
        dur_str = data[2]
        group_id = int(data[3])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        dur_map = {
            "1m": (60, "1 Minuto"),
            "10m": (600, "10 Minutos"),
            "1h": (3600, "1 Hora"),
            "24h": (86400, "24 Horas"),
            "30d": (2592000, "30 Días"),
            "perm": (0, "Permanente")
        }
        sec, label = dur_map.get(dur_str, (0, "Permanente"))
        MOD_TARGET_STATES[callback.from_user.id] = {
            "action": sub_cmd, "group_id": group_id, "duration": sec, 
            "dur_label": label, "lang": lang
        }
        await callback.message.answer(
            f"🎯 <b>Configuración de Objetivo — /{sub_cmd.upper()} ({label})</b>\n\n"
            f"Envía a este chat privado el <b>@usuario</b> o la <b>ID numérica</b> del recluta a sancionar:\n\n"
            f"🛡️ <i>Cloud Media Management</i>",
            parse_mode="HTML"
        )
        return

    elif action == "gpanel":
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad"
        text = t["group_panel_title"].format(group_name=g_name)
        keyboard = get_group_panel_keyboard(group_id, lang)

    if text and keyboard:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(
    F.data.startswith("gset_") | F.data.startswith("astog_") | F.data.startswith("as_") | 
    F.data.startswith("togcap_") | F.data.startswith("togmode_") | F.data.startswith("cap_set_") | 
    F.data.startswith("capval_") | F.data.startswith("afset_") | F.data.startswith("afval_") | 
    F.data.startswith("afact_") | F.data.startswith("toglock_") | F.data.startswith("warnset_") | 
    F.data.startswith("delmsgs_")
)
async def cb_group_modules_interceptor(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    data = callback.data.split("_")
    action = data[0]
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    # Manejo de retorno directo desde enlaces tipo gset_{group_id}
    if action == "gset" and len(data) == 2 and data[1].lstrip("-").isdigit():
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad"
        await callback.message.edit_text(
            t["group_panel_title"].format(group_name=g_name), 
            reply_markup=get_group_panel_keyboard(group_id, lang), 
            parse_mode="HTML"
        )
        return

    try:
        group_id = int(data[-2])
    except (ValueError, IndexError):
        return

    if not await verify_admin_privileges(callback, bot, group_id): 
        return

    if action == "gset":
        module = data[1]
        if module == "captcha":
            cfg = await get_captcha_config(group_id)
            st_text = "🟢" if cfg["status"] == 1 else "🔴"
            mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
            time_text = str(cfg["time"])
            action_text = cfg["action"].upper()
            await callback.message.edit_text(
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=time_text, action_text=action_text), 
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "locks":
            media = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_media") == 1 else "Permitido 🔴"
            stickers = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_stickers") == 1 else "Permitido 🔴"
            links = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_links") == 1 else "Permitido 🔴"
            commands = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "Permitido 🔴"
            await callback.message.edit_text(
                t["locks_main_title"].format(media=media, stickers=stickers, links=links, commands=commands),
                reply_markup=await get_locks_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "warns":
            cfg = await get_warns_config(group_id)
            await callback.message.edit_text(
                t["warns_main_title"].format(limit=cfg["limit"], action=cfg["action"].upper()),
                reply_markup=await get_warns_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "delmsgs":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            tier_display = tier.upper()
            quota_desc = "3 purgas de servicio diarias (Plan Básico)" if tier == "free" else "Purga automatizada ilimitada (PRO / ULTRA)"
            if lang == "en":
                quota_desc = "3 service purges daily (Free Plan)" if tier == "free" else "Advanced automated monthly purge (PRO / ULTRA)"

            await callback.message.edit_text(
                t["delmsgs_main_title"].format(tier_display=tier_display, quota_desc=quota_desc),
                reply_markup=await get_delmsgs_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif module == "antispam": 
            await callback.message.edit_text(
                await get_antispam_text(group_id, lang), 
                reply_markup=await get_antispam_keyboard(group_id, lang), 
                parse_mode="HTML"
            )
        elif module == "antiflood":
            cfg = await get_antiflood_config(group_id)
            delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
            await callback.message.edit_text(
                t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st), 
                reply_markup=get_antiflood_keyboard(group_id, lang, cfg), 
                parse_mode="HTML"
            )
        elif module == "clone":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad"
            status = "Operativo 🟢" if tier == "ultra_pro" else "Bloqueado 🔴"
            
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            
            await callback.message.edit_text(
                t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status),
                reply_markup=await get_clone_keyboard(group_id, callback.from_user.id, lang),
                parse_mode="HTML"
            )

    elif action == "astog":
        filter_str = data[1]
        real_filter = FILTER_MAP.get(filter_str)
        if real_filter:
            current = await get_antispam_filter(group_id, real_filter)
            await set_antispam_filter(group_id, real_filter, 0 if current == 1 else 1)
            try:
                if filter_str.startswith("fwd"):
                    await callback.message.edit_reply_markup(reply_markup=await get_forwards_keyboard(group_id, lang))
                else:
                    await callback.message.edit_text(
                        await get_antispam_text(group_id, lang),
                        reply_markup=await get_antispam_keyboard(group_id, lang),
                        parse_mode="HTML"
                    )
            except TelegramBadRequest:
                pass

    elif action == "as":
        sub = data[1]
        if sub == "fwd": 
            await callback.message.edit_text(t["forwards_panel"], reply_markup=await get_forwards_keyboard(group_id, lang), parse_mode="HTML")
        elif sub == "togdel":
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
            try:
                await callback.message.edit_text(
                    await get_antispam_text(group_id, lang),
                    reply_markup=await get_antispam_keyboard(group_id, lang),
                    parse_mode="HTML"
                )
            except TelegramBadRequest:
                pass

    elif action == "delmsgs":
        sub = data[1]
        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if sub == "tier":
            info = "ℹ️ Plan Básico: Límite de 3 purgas diarias." if tier == "free" else f"⭐ Plan {tier.upper()}: Cuota mensual automatizada."
            await callback.answer(info, show_alert=True)
            return
        elif sub == "main":
            if tier == "free":
                await callback.answer("⚠️ La limpieza del chat principal requiere nivel PRO o ULTRA.", show_alert=True)
                return
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
        elif sub == "cmds":
            if tier == "free":
                await callback.answer("⚠️ El bloqueo de comandos a regulares requiere nivel PRO o ULTRA.", show_alert=True)
                return
            current_cmd = await get_lock_status(group_id, "lock_commands")
            await set_lock_status(group_id, "lock_commands", 0 if current_cmd == 1 else 1)
        
        try:
            await callback.message.edit_reply_markup(reply_markup=await get_delmsgs_keyboard(group_id, lang))
        except TelegramBadRequest:
            pass

    elif action == "toglock":
        lock_type = data[1]
        lock_key = f"lock_{lock_type}"
        current = await get_lock_status(group_id, lock_key)
        await set_lock_status(group_id, lock_key, 0 if current == 1 else 1)

        media = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_media") == 1 else "Permitido 🔴"
        stickers = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_stickers") == 1 else "Permitido 🔴"
        links = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_links") == 1 else "Permitido 🔴"
        commands = "Bloqueado 🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "Permitido 🔴"

        try:
            await callback.message.edit_text(
                t["locks_main_title"].format(media=media, stickers=stickers, links=links, commands=commands),
                reply_markup=await get_locks_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "warnset":
        sub = data[1]
        cfg = await get_warns_config(group_id)
        if sub == "limit":
            limits = [3, 4, 5]
            curr_limit = cfg["limit"]
            new_limit = limits[(limits.index(curr_limit) + 1) % len(limits)] if curr_limit in limits else 3
            await set_warns_config(group_id, "warns_limit", new_limit)
        elif sub == "action":
            actions = ["mute", "kick", "ban"]
            curr = cfg["action"]
            next_act = actions[(actions.index(curr) + 1) % len(actions)] if curr in actions else "mute"
            await set_warns_config(group_id, "warns_action", next_act)
        
        updated_cfg = await get_warns_config(group_id)
        try:
            await callback.message.edit_text(
                t["warns_main_title"].format(limit=updated_cfg["limit"], action=updated_cfg["action"].upper()),
                reply_markup=await get_warns_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "togcap":
        await set_captcha_status(group_id, 1 if data[1] == "on" else 0)
        cfg = await get_captcha_config(group_id)
        st_text = "🟢" if cfg["status"] == 1 else "🔴"
        mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
        try:
            await callback.message.edit_text(
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg["time"]), action_text=cfg["action"].upper()), 
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "togmode":
        await set_captcha_config(group_id, "captcha_mode", 1 if data[1] == "on" else 0)
        cfg = await get_captcha_config(group_id)
        st_text = "🟢" if cfg["status"] == 1 else "🔴"
        mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
        try:
            await callback.message.edit_text(
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg["time"]), action_text=cfg["action"].upper()), 
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "cap_set":
        sub = data[2]
        if sub == "time":
            await callback.message.edit_text(
                "⏱️ <b>Configuración de Tiempo Límite</b>\n\nSelecciona el tiempo máximo que tiene el recluta para resolver el desafío:",
                reply_markup=get_captcha_time_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif sub == "action":
            cfg = await get_captcha_config(group_id)
            await callback.message.edit_text(
                t["captcha_action_title"].format(mode_name=cfg["action"].upper()),
                reply_markup=await get_captcha_action_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        elif sub == "text":
            tier_check = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier_check not in ["pro", "ultra_pro"]:
                upsell_text = (
                    "⭐ <b>Aduana Captcha Pro — Mensaje Personalizado</b>\n\n"
                    "Personalizar el mensaje de bienvenida y las instrucciones de la aduana alfanumérica requiere el nivel <b>PRO</b> o <b>ULTRA PRO</b>.\n\n"
                    "<i>¡Haz que tus nuevos miembros lean tus propias reglas, bienvenida y enlaces desde el primer contacto!</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                ) if lang == "es" else (
                    "⭐ <b>Captcha Pro — Custom Welcome Message</b>\n\n"
                    "Customizing the welcome copy and challenge instructions requires the <b>PRO</b> or <b>ULTRA PRO</b> tier.\n\n"
                    "<i>Greet your recruits with your own custom branding, community rules, and verified links!</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="⭐ PRO", callback_data=f"pay_pro_{group_id}_{lang}"),
                        InlineKeyboardButton(text="💎 ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")
                    ],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_captcha_{group_id}_{lang}")]
                ])
                try:
                    await callback.message.edit_text(upsell_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

            CAPTCHA_STATES[callback.from_user.id] = group_id
            await callback.message.answer(
                "✍️ <b>Editor de Captcha — Mensaje Personalizado</b>\n\n"
                "Envía en este chat privado el mensaje que se enviará al usuario al ingresar.\n\n"
                "🛡️ <i>Cloud Media Management</i>",
                parse_mode="HTML"
            )
        elif sub == "srvdel":
            cfg = await get_captcha_config(group_id)
            await set_captcha_config(group_id, "captcha_service_del", 0 if cfg["service_del"] == 1 else 1)
            cfg_updated = await get_captcha_config(group_id)
            
            if callback.message.text and ("Service" in callback.message.text or "Purga" in callback.message.text or "Purge" in callback.message.text or "Centro" in callback.message.text):
                tier = await get_effective_group_tier(group_id, callback.from_user.id)
                quota_desc = "3 purgas de servicio diarias (Plan Básico)" if tier == "free" else "Purga automatizada ilimitada (PRO / ULTRA)"
                try:
                    await callback.message.edit_text(
                        t["delmsgs_main_title"].format(tier_display=tier.upper(), quota_desc=quota_desc),
                        reply_markup=await get_delmsgs_keyboard(group_id, lang),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    pass
            else:
                st_text = "🟢" if cfg_updated["status"] == 1 else "🔴"
                mode_text = "🟢" if cfg_updated["mode"] == 1 else "🔴"
                try:
                    await callback.message.edit_text(
                        t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg_updated["time"]), action_text=cfg_updated["action"].upper()), 
                        reply_markup=await get_captcha_keyboard(group_id, lang),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    pass

    elif action == "capval":
        sub_type = data[1]
        val = data[2]
        if sub_type == "time":
            await set_captcha_config(group_id, "captcha_time", int(val))
        elif sub_type == "action":
            await set_captcha_config(group_id, "captcha_action", val)
        
        cfg = await get_captcha_config(group_id)
        st_text = "🟢" if cfg["status"] == 1 else "🔴"
        mode_text = "🟢" if cfg["mode"] == 1 else "🔴"
        try:
            await callback.message.edit_text(
                t["captcha_main_title"].format(status_text=st_text, mode_text=mode_text, time_text=str(cfg["time"]), action_text=cfg["action"].upper()),
                reply_markup=await get_captcha_keyboard(group_id, lang),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    elif action == "afset": 
        sub_mode = data[1]
        prompt = f"<b>{t['af_msgs']}</b>\nSelecciona el límite numérico:" if sub_mode == "msgs" else f"<b>{t['af_time']}</b>\nSelecciona la ventana en segundos:"
        await callback.message.edit_text(prompt, reply_markup=get_antiflood_number_keyboard(group_id, lang, sub_mode), parse_mode="HTML")

    elif action == "afval":
        sub_mode = data[1]
        val = int(data[2])
        await set_antiflood_config(group_id, "antiflood_msgs" if sub_mode == "msgs" else "antiflood_time", val)
        cfg = await get_antiflood_config(group_id)
        delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
        await callback.message.edit_text(
            t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st), 
            reply_markup=get_antiflood_keyboard(group_id, lang, cfg), 
            parse_mode="HTML"
        )

    elif action == "afact":
        sub_act = data[1]
        if sub_act == "togdel":
            cfg = await get_antiflood_config(group_id)
            await set_antiflood_config(group_id, "antiflood_delete", 0 if cfg["delete"] == 1 else 1)
        else:
            await set_antiflood_config(group_id, "antiflood_action", sub_act)
        
        cfg = await get_antiflood_config(group_id)
        delete_st = "🟢" if cfg["delete"] == 1 else "🔴"
        await callback.message.edit_text(
            t["antiflood_main_title"].format(msgs=cfg["msgs"], time=cfg["time"], action=cfg["action"].upper(), delete_st=delete_st), 
            reply_markup=get_antiflood_keyboard(group_id, lang, cfg), 
            parse_mode="HTML"
        )