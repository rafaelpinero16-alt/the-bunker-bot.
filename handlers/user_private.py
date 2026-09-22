import os
import sys
import time
import asyncio
import logging
from aiogram import Router, F, Bot, BaseMiddleware
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
    register_bot_clone, get_bot_clone, get_db_connection,
    save_owner_session, get_owner_session, revoke_owner_session,
    get_vc_schedule, set_vc_schedule,
    # 🚨 ULTRA PRO — Panel de Élite (Botón de Pánico / Escudo Antinota / Podcast)
    # NOTA: si en tu database.py estos helpers ya existen con otro nombre,
    # ajusta ÚNICAMENTE esta lista de imports; el resto del módulo no depende
    # de la implementación interna (misma firma que get/set_autolower_status).
    get_panic_status, set_panic_status,
    get_shield_status, set_shield_status,
    get_podcast_status, set_podcast_status,
)
from assistant import (
    register_or_update_sentinel, disconnect_sentinel,
    start_phone_auth, verify_phone_code, verify_2fa_password, cancel_phone_auth,
    # 🎥🎙️ Motor del Centinela (MTProto) para Escudo Antinota y Podcast/Ducking
    engage_screen_shield, disengage_screen_shield,
    engage_podcast_ducking, disengage_podcast_ducking
)
from groups import (
    # 🚨 Bloqueo/levantamiento de emergencia y cola de Speakers pagados
    execute_raid_lockdown, lift_raid_lockdown,
    add_speaker_to_queue, pop_next_speaker, clear_speaker_queue, get_speaker_queue
)

router = Router()


class CallbackAutoAnswerMiddleware(BaseMiddleware):
    """
    Telegram admite UNA sola respuesta por callback_query.

    Antes, los handlers principales llamaban `callback.answer()` al inicio y, después, varias ramas
    intentaban `callback.answer(texto, show_alert=True)`: esa segunda respuesta fallaba y las alertas
    (owner_only_alert, clone_disc, mic_alert_set, planes PRO/ULTRA...) nunca se mostraban.

    Ahora los handlers NO responden al inicio: si una rama necesita alerta, esa es su respuesta única;
    y este middleware garantiza, al terminar (con o sin error), que el spinner del botón se libere.
    Si la query ya fue respondida, el segundo intento se descarta en silencio.
    Es agnóstico al bot: usa el CallbackQuery vinculado a la instancia que recibió el update.
    """
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        try:
            return await handler(event, data)
        finally:
            try:
                await event.answer()
            except Exception:
                pass


router.callback_query.middleware(CallbackAutoAnswerMiddleware())

ADMIN_GROUP_ID = -1004351489258
WEBAPP_URL = "https://thebunkerapp.netlify.app"

# ==========================================
# 👑 LISTA BLANCA DE ARQUITECTOS (INMUNIDAD TOTAL)
# ==========================================
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
SUPER_ADMIN_IDS = {int(x.strip()) for x in RAW_ADMINS.split(",") if x.strip().isdigit()}
SUPER_ADMIN_IDS.update([8269470905, 1738976493])

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS

# ==========================================
# 🧬 IDENTIDAD MAESTRO / CLON (por bot.id)
# ==========================================
MASTER_BOT_ID = 0

def set_master_bot_id(bot_id: int) -> None:
    """Fija el ID del Bot Maestro (lo invoca main.py al arrancar)."""
    global MASTER_BOT_ID
    MASTER_BOT_ID = int(bot_id)

MASTER_BOT_USERNAME = ""

def set_master_bot_username(username: str) -> None:
    """
    Fija el @username del Bot Maestro (lo invoca main.py al arrancar, tras get_me()).

    Se usa para construir deep-links (t.me/<usuario>?start=sub_...) que garantizan
    que la facturación de suscripciones PRO/ULTRA PRO SIEMPRE se cobre a través del
    Maestro, incluso cuando el comando se ejecuta desde un Bot Clon. Ver payments.py.
    """
    global MASTER_BOT_USERNAME
    MASTER_BOT_USERNAME = (username or "").lstrip("@")

def get_master_bot_username() -> str:
    return MASTER_BOT_USERNAME

def _resolve_master_bot_id() -> int:
    if MASTER_BOT_ID:
        return MASTER_BOT_ID
    # Respaldo: el ID del bot es el prefijo numérico del token de BotFather
    head = os.getenv("BOT_TOKEN", "").split(":", 1)[0].strip()
    return int(head) if head.isdigit() else 0

def is_clone_bot(bot: Bot) -> bool:
    """True si la instancia ejecutora NO es el Bot Maestro (comparación por bot.id)."""
    master_id = _resolve_master_bot_id()
    return bool(master_id) and bot.id != master_id

def _call_clone_trigger(name: str, token: str) -> None:
    """
    Invoca trigger_dynamic_clone / trigger_disconnect_clone del proceso que YA está en ejecución.

    ⚠️ No usar `from main import ...`: al lanzar `python main.py` el archivo corre como `__main__`;
    `import main` lo re-ejecuta como un módulo distinto, con un Dispatcher NUEVO y VACÍO (sin routers)
    y otro `active_clone_tasks`. Los clones creados desde el panel quedaban escuchando ese Dispatcher
    vacío → `Update is not handled` en cada callback.
    """
    for mod_name in ("__main__", "main"):
        mod = sys.modules.get(mod_name)
        fn = getattr(mod, name, None) if mod else None
        if callable(fn):
            fn(token)
            return
    logging.error(f"❌ [Clones] No se encontró {name} en __main__/main; token no procesado.")

async def get_effective_group_tier(group_id: int, user_id: int) -> str:
    if is_super_admin(user_id):
        return "ultra_pro"
    return await get_group_tier(group_id)

async def revoke_bot_clone_db(user_id: int, group_id: int):
    """Marca como revocado y desconectado el bot clon en la base de datos."""
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = '' WHERE user_id = ? AND group_id = ?", (user_id, group_id))
            conn.commit()
    await asyncio.to_thread(_sync)

# ==========================================
# 🧠 ESTADOS DE EDICIÓN CONVERSACIONAL AISLADOS POR BOT
# ==========================================
CAPTCHA_STATES = {}
CLONE_STATES = {}
SENTINEL_PHONE_STATES = {}
SENTINEL_CODE_STATES = {}
SENTINEL_2FA_STATES = {}
VC_SCHED_STATES = {}
DB_REG_STATES = {}
MOD_TARGET_STATES = {}
MIC_VIP_STATES = {}
MIC_TAG_STATES = {}
GROUP_MIC_PRICE = {}
GROUP_VIP_TAG = {}
# 🌟 ULTRA PRO — Podcast/Ducking y Cola de Speakers pagados
PODCAST_DUCK_STATES = {}
SPEAKER_PRICE_STATES = {}
GROUP_DUCK_LEVEL = {}
GROUP_SPEAKER_PRICE = {}

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
            "• <b>Version:</b> 5.5 (Multi-Sentinel & Native Phone Auth Core)\n"
            "• <b>Architect:</b> Master Tom\n"
            "• <b>Tactical Focus:</b> Multi-Sentinel Architecture, Native VIP Badging, and Absolute Security.\n\n"
            "🛡️ <i>Developed and supported by <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>How The Bunker Bot Works — The Master Guide</b>\n\n"
            "Follow this operational path to bring your Bunker online at full strength, "
            "from the first install to full Telegram Stars monetization:\n\n"
            "1️⃣ <b>Deployment: Add the Bot &amp; Grant Admin Rights</b>\n"
            "• Add <b>@Alphacentinel</b> (or your own Bot Clone) to your group/supergroup using the 'Add to a Group' button.\n"
            "• Promote it to <b>Administrator</b> with, at minimum: delete messages, restrict members, manage video chats, and pin messages. Without these, the Sentinel cannot run the Alphanumeric Captcha checkpoint or the AutoLower Radar.\n"
            "• Once inside, run /pro or /ultra directly in the group to open the Command Center in this private chat.\n\n"
            "2️⃣ <b>Your Own Bot Clone (ULTRA PRO 💎)</b>\n"
            "• Message <b>@BotFather</b>, create a fresh bot with /newbot, and copy the token it hands you.\n"
            "• From the ULTRA PRO panel, tap 'Setup Clone' and paste that token: your private replica gets linked exclusively to your community, running in parallel to the Master Bot without interfering with it.\n"
            "• Every Star your Clone collects through VIP mic passes (/micvip) lands 100% in YOUR balance — the platform never takes a cut of those passes.\n\n"
            "3️⃣ <b>Dedicated Sentinel: Phone Number &amp; 2FA Linking</b>\n"
            "• In the same ULTRA PRO panel, choose 'Link Sentinel' and enter your phone number with the international code (e.g. +1...).\n"
            "• Telegram sends a verification code: type it exactly as received in this private chat. If your account has Two-Factor Authentication (2FA), the bot will also ask for your password — both travel encrypted and are <b>never stored as plain text</b>; only the resulting StringSession is kept, and strictly in volatile memory.\n"
            "• This spins up your isolated, anti-ban node: every Sentinel runs on its own session, sharing no IP or fingerprint with the rest of the network, shielding your main account from association bans.\n"
            "• With the Sentinel active, the <b>AutoLower Radar</b> engages automatically: any mic that opens in the voice chat without an active VIP pass or explicit authorization gets dialed down to <b>2% volume in milliseconds</b> — no human moderator needs to be watching.\n\n"
            "4️⃣ <b>Telegram Stars Monetization (/micvip)</b>\n"
            "• Set your own Stars price for the 24-hour VIP Microphone Pass from the Economy panel.\n"
            "• When a member pays, the Sentinel instantly restores their volume to 100% and auto-assigns an <b>immovable admin title</b> (e.g. \"VIP 24/7\"), fully customizable by you from the ULTRA PRO panel.\n"
            "• Two clean, independent revenue streams: platform subscriptions (PRO/ULTRA PRO) are always billed through the Master Bot, while every VIP mic pass flows 100% into YOUR Clone.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Security Matrix:</b> {group_name}\n\nSelect a tactical module to alter community parameters.",
        "pay_pro_title": (
            "⭐ <b>PRO Plan Subscription — {group_name} (300 Stars)</b>\n\n"
            "Upgrade your community to elite operational status:\n\n"
            "• ⚡ <b>Unlimited Bot Commands:</b> Bypass the 3 daily uses limit.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Unlimited service logs and chat clutter cleanup.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and timeout parameters.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Advanced shielding against channels, bots, and links.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>ULTRA PRO Subscription — {group_name} (600 Stars)</b>\n\n"
            "Total command, decentralized automation, and high-tier monetization for your community:\n\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own @BotFather token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as a 24/7 voice mod via phone.\n"
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

        "botfather_guide": (
            "🔑 <b>How to Connect Your Bot Clone — Step by Step</b>\n\n"
            "Follow these simple instructions to link your custom bot instance:\n\n"
            "1️⃣ Open official Telegram bot: @BotFather.\n"
            "2️⃣ Send the command <code>/newbot</code>.\n"
            "3️⃣ Choose a display name for your bot (e.g. <i>Nexus Security</i>).\n"
            "4️⃣ Choose a unique username ending in <code>bot</code> (e.g. <i>NexusSecurityBot</i>).\n"
            "5️⃣ @BotFather will send you an <b>HTTP API Token</b> (a long string like <code>7123456789:AAFn_...</code>).\n"
            "6️⃣ <b>Copy that token and paste it right here in this chat.</b>\n\n"
            "⚠️ <i>Never share your token publicly. We store it securely in your private Bunker grid.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "sentinel_phone_guide": (
            "🎙️ <b>Connect Dedicated Sentinel — Phone Login</b>\n\n"
            "Link your secondary/burner account as a 24/7 voice moderator without needing any code or string sessions:\n\n"
            "1️⃣ Send your <b>phone number with international country code</b> (e.g. <code>+12025550143</code> or <code>+573001234567</code>).\n"
            "2️⃣ Telegram will send an official 5-digit login code directly to your Telegram app.\n"
            "3️⃣ Send the code here to complete the connection.\n\n"
            "💡 <i>Tip: We recommend using a secondary account as sentinel to keep your main personal profile clean.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        
        "captcha_saved": "✅ <b>Captcha custom message saved successfully!</b>\n\n<i>{text_input}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "token_verifying": "🔄 <b>Verifying bot token with Telegram servers...</b>",
        "token_success": "✅ <b>Token received and verified successfully!</b>\nReplica instance connected to The Bunker database.\n\n🛡️ <i>Cloud Media Management</i>",
        "token_error": "❌ <b>Invalid bot token.</b> Make sure the bot exists, the token is copied properly from @BotFather, and it hasn't been revoked.\n\n🛡️ <i>Cloud Media Management</i>",
        "phone_requesting": "🔄 <b>Requesting official Telegram code...</b>\nPlease wait a few seconds.",
        "phone_sent": "📩 <b>Official Code Sent!</b>\n\nTelegram sent an access code to your official app associated with number <code>{phone}</code>.\n\n<b>Enter the numerical code received below:</b>\n<i>(Example: 49281)</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "phone_error": "❌ <b>Error sending code:</b>\n\n{reason}\n\nMake sure to include your country code (e.g. <code>+573001234567</code>).",
        "code_verifying": "🔄 <b>Verifying code and authorizing Sentinel...</b>",
        "sentinel_success": "💎 <b>Own Sentinel Connected Successfully!</b>\n\n• <b>Community:</b> Shielded with your account\n• <b>Stream Radar:</b> Active 24/7 in the cloud\n• <b>Total Isolation:</b> Operating without global ban risk\n\n<i>Make sure you added your account to the group with Manage Video Chats permission.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "sentinel_error": "❌ <b>Error initializing session.</b> Try again from the menu.",
        "twofa_required": "🔐 <b>Two-Step Verification (2FA) Required</b>\n\nYour Telegram account has a cloud password enabled.\n\n<b>Enter your two-step verification password:</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "code_invalid": "❌ <b>Invalid or expired code.</b>\nVerify the code received in your official Telegram app and try again.",
        "twofa_verifying": "🔄 <b>Validating 2FA password...</b>",
        "twofa_invalid": "❌ <b>Incorrect 2FA password.</b>\nVerify your key and try again.",
        "sched_updated": "✅ <b>Schedule Updated!</b>\n\n• Start: <code>{start}</code>\n• End: <code>{end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "sched_err": "⚠️ Invalid format. Use HH:MM-HH:MM (Example: <code>20:00-23:30</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_success": "✅ <b>User successfully whitelisted!</b>\n\n• <b>ID:</b> <code>{target_id}</code>\n• <b>Tactical Immunity:</b> ACTIVE 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "id_err": "⚠️ <b>Invalid ID.</b> Send the numeric ID of the user.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_success": "✅ <b>Classified term blacklisted!</b>\n\n• <b>Forbidden Term:</b> <code>{word}</code>\n• <b>Protocol:</b> Auto-purge and tactical warns ACTIVE 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_ban": "🚫 <b>Ban Directive Executed!</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Duration:</b> {dur_label}\n• <b>Status:</b> Eradicated 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_kick": "👢 <b>Kick Directive Executed!</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Status:</b> Kicked 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_mute": "🔇 <b>Mute Directive Executed!</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Duration:</b> {dur_label}\n• <b>Status:</b> Mic & text restricted 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_unmute": "🔊 <b>Privileges Restored!</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Status:</b> Full access 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_err": "❌ <b>Error executing directive:</b>\n<code>{ex}</code>\n\nMake sure the bot has admin permissions in the selected community.",
        "mic_updated": "⭐ <b>VIP Mic Rate Updated!</b>\n\n• <b>Community ID:</b> <code>{group_id}</code>\n• <b>24h Pass Price:</b> <code>{price_val} Stars (XTR)</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_err": "⚠️ Enter a positive integer for Stars (e.g. 50).\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_updated": "🏷️ <b>VIP Tag Updated Successfully!</b>\n\n• <b>Community ID:</b> <code>{group_id}</code>\n• <b>Native Tag:</b> <code>{text_input}</code> 🟢\n\n<i>This immovable title is auto-assigned on Stars tip.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_err": "⚠️ Tag must be between 1 and 16 characters (Telegram official admin title limit).\n\nExample: <code>VIP 24/7</code> or <code>VIP Gold</code>.",
        "mod_id_err": "⚠️ <b>Invalid Target.</b> Send a valid numerical User ID or @username.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_cancel_ret": "❌ Cancel & Return",
        "btn_retry": "🔄 Retry",
        "op_canceled": "Operation canceled and memory cleared.",
        "sentinel_disc": "🛑 Own Sentinel disconnected successfully.",
        "clone_disc": "🛑 Bot Clone disconnected successfully.",
        "tag_pro_req": "💎 Native tag editing requires ULTRA PRO tier.",
        "al_updated_1": "AutoLower updated 🟢",
        "al_updated_0": "AutoLower disabled 🔴",
        "mic_alert_set": "Rate set to {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directive: Tactical Whitelist</b>\n\nRegistered identities receive <b>Absolute Immunity</b>. No anti-spam filters, locks, or captchas will bind them.\n\n• <b>Tactical Immunity:</b> ACTIVE 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directive: Global Blacklist</b>\n\nClassified glossary of forbidden terms. Any match in the chat triggers auto-purge and tactical warnings.\n\n• <b>Protocol:</b> Auto-purge and Warns 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Camera & Video Chat Supervision</b>\n\nLive stream stability audit:\n\n• <b>Video Quality:</b> High Fidelity & continuous flow 🟢\n• <b>Transmission Priority:</b> Optimal (No drops/overloads)\n• <b>Continuous AV Optimization:</b> Active in background to prevent frozen cameras and black screens.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Remote Control: Video Chat AutoLower</b>\n\nThe Sentinel dims unauthorized members' microphones to maintain absolute room order:\n\n• <b>Current Status:</b> {status_str}\n\nSelect a directive to change live behavior:\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>VIP Microphone Pass (Stars Monetization)</b>\n\nAllows members to unlock their voice at 100% continuously for 24h by paying Telegram Stars (XTR).\n\n💡 <i>Key Advantage:</i> By deploying your own <b>Bot Clone</b> and <b>Dedicated Sentinel</b>, 100% of the Stars collected from VIP passes go <b>directly to your bot's account</b>.\n\n• <b>Current Rate:</b> <code>{curr_price} Stars (XTR)</code> 🟢\n• <b>Assigned Tag:</b> <code>{curr_tag}</code> (Native & immovable)\n• <b>Pass Duration:</b> 24 automated hours\n\nSelect the billing rate or customize the tag:\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Native VIP Tag Editor (ULTRA PRO)</b>\n\nCurrent tag: <code>{curr_tag}</code>\n\nSend in this private chat the text you want to auto-assign as an admin title (max 16 characters).\n\n<i>Example: VIP 24/7, Elite, Sponsor</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "mod_ask_time": "⚡ <b>Moderation Directive: /{sub_cmd}</b>\n\nSelect the duration of the directive for the user:",
        "mod_ask_target": "🎯 <b>Target Configuration — /{sub_cmd_upper}</b>\n\nSend the <b>@username</b> or <b>numeric ID</b> of the target in this private chat:\n\n🛡️ <i>Cloud Media Management</i>",
        "reg_ask": "📝 <b>Database Registration</b>\n\nSend {target_name} in this private chat:\n\n🛡️ <i>Cloud Media Management</i>",
        "reg_ask_wl": "the user ID for Whitelist",
        "reg_ask_bl": "the forbidden term for Blacklist",
        "vcsched_prompt": "⏰ <b>VC Schedule Configuration</b>\n\nSend in this private chat the opening and closing interval in 24h format (example: <code>20:00-23:30</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_custom_prompt": "⭐ <b>Custom Stars Rate</b>\n\nSend in this private chat the number of Stars per 24h VIP pass (example: 75).\n\n<i>Remember that with your Bot Clone, Stars go directly to your balance.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "vcsched_main": "🗓️ <b>Voice Chat Scheduler (ULTRA PRO)</b>\n\nConfigure the automated opening and closing of your voice rooms:\n\n• <b>Status:</b> {st_badge}\n• <b>Active Days:</b> <code>{days}</code>\n• <b>Schedule:</b> <code>{start} - {end}</code>\n\nSelect an option to modify parameters:\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_add_wl": "➕ Register User to DB",
        "btn_add_bl": "➕ Register Term to DB",
        "btn_al_1": "🟢 Activate AutoLower (2%)",
        "btn_al_0": "🔴 Disable (Free)",
        "btn_custom_rate": "✍️ Custom Rate",
        "btn_mictag": "🏷️ Tag: {curr_tag}",
        "btn_sched_mod": "⏰ Modify Schedule (HH:MM-HH:MM)",
        "btn_sched_off": "🔴 Disable Schedule",
        "btn_sched_on": "🟢 Enable Schedule",
        
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
        "btn_back_eco": "🔙 Ecosystem",

        # ==========================================
        # 💎 ULTRA PRO — ELITE TOOLS PANEL
        # ==========================================
        "ultra_tools_main": (
            "💎 <b>ULTRA PRO Elite Tools — {group_name}</b>\n\n"
            "Direct panel control over your highest-tier automation modules, no group text commands required:\n\n"
            "🚨 <b>Panic Button:</b> Instant emergency raid lockdown.\n"
            "🎥 <b>Screen-Share Shield:</b> Screen-sharing moderation.\n"
            "🎙️ <b>Podcast Mode:</b> Dynamic volume & noise-gate ducking.\n"
            "🌟 <b>Speakers Queue:</b> Paid priority mic queue (/speakers).\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_ultra_tools": "💎 ULTRA Elite Tools",
        "ultra_lock_generic": (
            "🔒 <i>This module is an advanced ULTRA PRO automation capability and is available exclusively at that tier.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        # --- Panic Button / Raid Lockdown ---
        "panic_menu": (
            "🚨 <b>Panic Button — Raid Lockdown</b>\n\n"
            "Instantly locks the entire community in an emergency: default permissions are dropped to zero and invite links are revoked, freezing any raid or mass-spam attack in progress.\n\n"
            "• <b>Current Status:</b> {status_str}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "panic_confirm": (
            "⚠️ <b>Confirm Emergency Lockdown</b>\n\n"
            "This will immediately silence and restrict <b>all</b> members in the group until you manually lift the lockdown. Use this only during an active raid or attack.\n\n"
            "Do you want to proceed?"
        ),
        "panic_activated": "🚨 <b>RAID LOCKDOWN ACTIVE</b>\n\nThe community has been locked down. Tap below to lift it once the threat has passed.\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_deactivated": "🟢 <b>Lockdown lifted.</b>\n\nNormal permissions have been restored to the community.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_panic_activate": "🚨 Activate Lockdown",
        "btn_panic_confirm": "✅ Confirm Lockdown",
        "btn_panic_deactivate": "🟢 Lift Lockdown",
        # --- Screen-Sharing Shield ---
        "shield_menu": (
            "🎥 <b>Screen-Share Shield</b>\n\n"
            "The Sentinel monitors active screen shares in the voice chat and automatically moderates unauthorized or inappropriate broadcasts.\n\n"
            "• <b>Current Status:</b> {status_str}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_updated_1": "🎥 Screen-Share Shield activated 🟢",
        "shield_updated_0": "🎥 Screen-Share Shield disabled 🔴",
        "btn_shield_1": "🟢 Activate Shield",
        "btn_shield_0": "🔴 Disable Shield",
        # --- Podcast Mode & Audio Ducking ---
        "podcast_menu": (
            "🎙️ <b>Podcast Mode & Audio Ducking</b>\n\n"
            "When a designated speaker talks, background mics are automatically dimmed (\"ducked\") to keep the show clean, plus a noise-gate shield against background noise.\n\n"
            "• <b>Current Status:</b> {status_str}\n"
            "• <b>Ducking Level:</b> <code>{duck_level}%</code> (background volume while a speaker talks)\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_updated_1": "🎙️ Podcast Mode activated 🟢",
        "podcast_updated_0": "🎙️ Podcast Mode disabled 🔴",
        "btn_podcast_1": "🟢 Activate Podcast Mode",
        "btn_podcast_0": "🔴 Disable",
        "btn_duck_level": "🎚️ Ducking Level: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Custom Ducking Level</b>\n\nSend in this private chat a number from 1 to 90 for the background mic volume percentage while a speaker talks (example: 15).\n\n🛡️ <i>Cloud Media Management</i>",
        "duck_updated": "🎚️ <b>Ducking level updated!</b>\n\n• <b>Community ID:</b> <code>{group_id}</code>\n• <b>New Level:</b> <code>{duck_level}%</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "duck_err": "⚠️ Enter a whole number between 1 and 90.\n\n🛡️ <i>Cloud Media Management</i>",
        # --- Paid Speakers Queue (/speakers) ---
        "speakers_menu": (
            "🌟 <b>Paid Speakers Queue (/speakers)</b>\n\n"
            "Members can pay Stars to skip the line and get priority in the mic queue for the voice chat.\n\n"
            "• <b>Current Status:</b> {status_str}\n"
            "• <b>Priority Pass Rate:</b> <code>{price} Stars (XTR)</code>\n"
            "• <b>Members in Queue:</b> <code>{queue_count}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "speakers_updated_1": "🌟 Speakers Queue activated 🟢",
        "speakers_updated_0": "🌟 Speakers Queue disabled 🔴",
        "btn_speakers_1": "🟢 Activate Queue",
        "btn_speakers_0": "🔴 Disable Queue",
        "btn_speakers_price": "⭐ Priority Rate: {price} Stars",
        "btn_speakers_clear": "🧹 Clear Queue",
        "speakers_price_prompt": "⭐ <b>Custom Priority Rate</b>\n\nSend in this private chat the number of Stars to skip the speaker line (example: 30).\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_price_updated": "⭐ <b>Priority Rate Updated!</b>\n\n• <b>Community ID:</b> <code>{group_id}</code>\n• <b>New Rate:</b> <code>{price} Stars (XTR)</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_price_err": "⚠️ Enter a positive integer for Stars (e.g. 30).\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_cleared": "🧹 Speakers queue cleared 🟢"
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
            "• <b>Versión:</b> 5.5 (Multi-Centinela y Autenticación Telefónica Nativa)\n"
            "• <b>Arquitecto:</b> Master Tom\n"
            "• <b>Enfoque Táctico:</b> Arquitectura Multi-Centinela, Etiquetas Nativas VIP y Seguridad Absoluta.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "info_how_main": (
            "📖 <b>¿Cómo Funciona The Bunker Bot? — Guía Maestra</b>\n\n"
            "Sigue esta ruta operativa para desplegar tu Búnker a máxima capacidad, "
            "desde la instalación inicial hasta la monetización total con Telegram Stars:\n\n"
            "1️⃣ <b>Despliegue: Añade el Bot y Otorga Administración</b>\n"
            "• Agrega a <b>@Alphacentinel</b> (o tu propio Bot Clon) a tu grupo/supergrupo con el botón 'Añadir a un Grupo'.\n"
            "• Promuévelo a <b>Administrador</b> con, como mínimo: eliminar mensajes, restringir miembros, gestionar videollamadas y fijar mensajes. Sin estos permisos, el Centinela no podrá operar la Aduana Captcha alfanumérica ni el Radar AutoLower.\n"
            "• Una vez dentro, ejecuta /pro o /ultra directamente en el grupo para abrir el Centro de Mando en este chat privado.\n\n"
            "2️⃣ <b>Tu Propio Bot Clon (ULTRA PRO 💎)</b>\n"
            "• Escribe a <b>@BotFather</b>, crea un bot nuevo con /newbot y copia el token que te entrega.\n"
            "• Desde el panel ULTRA PRO, pulsa 'Configurar Clon' y pega ese token: tu réplica privada queda enlazada en exclusiva a tu comunidad, corriendo en paralelo al Bot Maestro sin interferir con él.\n"
            "• Cada Star que tu Clon recauda por pases VIP de micrófono (/micvip) entra al <b>100%</b> a TU balance — la plataforma nunca retiene comisión sobre esos pases.\n\n"
            "3️⃣ <b>Centinela Dedicado: Vinculación por Teléfono y 2FA</b>\n"
            "• En el mismo panel ULTRA PRO, elige 'Vincular Centinela' e ingresa tu número telefónico con el indicativo internacional (ej. +57...).\n"
            "• Telegram te enviará un código de verificación: escríbelo tal cual en este chat privado. Si tu cuenta tiene Verificación en Dos Pasos (2FA), el bot también te pedirá tu contraseña — ambos datos viajan cifrados y <b>nunca se guardan en texto plano</b>; solo se conserva la StringSession resultante, y siempre en memoria volátil.\n"
            "• Esto activa tu nodo aislado antiban: cada Centinela corre con su propia sesión, sin compartir IP ni huella con el resto de la red, blindando tu cuenta principal contra baneos por asociación.\n"
            "• Con el Centinela activo se habilita automáticamente el <b>Radar AutoLower</b>: cualquier micrófono que se abra en el videochat sin un pase VIP activo o autorización explícita es atenuado al <b>2% de volumen en milisegundos</b>, sin que un moderador humano tenga que estar presente.\n\n"
            "4️⃣ <b>Monetización con Telegram Stars (/micvip)</b>\n"
            "• Define tu propio precio en Stars para el Pase VIP de Micrófono de 24 horas desde el panel de Economía.\n"
            "• Cuando un miembro paga, el Centinela le restaura el volumen al 100% de inmediato y le asigna automáticamente una <b>etiqueta de administrador inamovible</b> (por ejemplo, \"VIP 24/7\"), totalmente personalizable por ti desde el panel ULTRA PRO.\n"
            "• Dos flujos de ingreso limpios e independientes: las suscripciones de plataforma (PRO/ULTRA PRO) se facturan siempre a través del Bot Maestro, mientras que cada pase VIP de micrófono fluye 100% hacia TU Clon.\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Matriz de Seguridad:</b> {group_name}\n\nSelecciona un módulo para alterar los parámetros de la comunidad.",
        "pay_pro_title": (
            "⭐ <b>Suscripción Plan PRO — {group_name} (300 Stars)</b>\n\n"
            "Eleva tu comunidad a un estándar profesional de alta seguridad:\n\n"
            "• ⚡ <b>Comandos de Bot Ilimitados:</b> Sin tope diario de 3 usos.\n"
            "• 🗑️ <b>Purga Automatizada:</b> Limpieza ilimitada de mensajes de servicio y chat.\n"
            "• 🤖 <b>Aduana Captcha Pro Avanzada:</b> Mensajes y tiempos personalizados.\n"
            "• 🛡️ <b>Anti-Spam Granular Total:</b> Bloqueo selectivo de canales, bots y enlaces.\n\n"
            "<i>Selecciona tu pasarela preferida para activar al instante:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>Suscripción ULTRA PRO — {group_name} (600 Stars)</b>\n\n"
            "Poder absoluto, automatización descentralizada y monetización para tu comunidad:\n\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Despliega tu réplica con tu propio token de @BotFather.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Tu cuenta secundaria como asistente 24/7 en llamadas vía teléfono.\n"
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

        "botfather_guide": (
            "🔑 <b>Guía Paso a Paso: Cómo Conectar tu Bot Clon</b>\n\n"
            "Sigue estas sencillas instrucciones para desplegar tu propio bot:\n\n"
            "1️⃣ Entra al bot oficial de Telegram: @BotFather.\n"
            "2️⃣ Envía el comando <code>/newbot</code>.\n"
            "3️⃣ Asigna un nombre a tu bot (ejemplo: <i>Comunidad Segura</i>).\n"
            "4️⃣ Asigna un alias único terminado en <code>bot</code> (ejemplo: <i>ComunidadSeguraBot</i>).\n"
            "5️⃣ @BotFather te responderá con tu <b>HTTP API Token</b> (un código largo como <code>7123456789:AAFn_...</code>).\n"
            "6️⃣ <b>Copia ese token y pégalo directamente en este chat.</b>\n\n"
            "⚠️ <i>Nunca compartas tu token con extraños. Se almacena cifrado en tu grid privado.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "sentinel_phone_guide": (
            "🎙️ <b>Guía Paso a Paso: Conexión de Centinela Propio</b>\n\n"
            "Vincula tu cuenta secundaria como asistente 24/7 en llamadas de manera totalmente automática:\n\n"
            "1️⃣ Envía a este chat tu <b>número de teléfono con código de país</b> (ejemplo: <code>+573001234567</code> o <code>+34600123456</code>).\n"
            "2️⃣ Telegram te enviará un código oficial de 5 dígitos en tu app de Telegram.\n"
            "3️⃣ Ingresa el código en este chat para completar la sincronización.\n\n"
            "💡 <i>Recomendación táctica: Usa una cuenta secundaria o número de respaldo para aislar tu cuenta personal de cualquier reporte.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        
        "captcha_saved": "✅ <b>¡Mensaje personalizado de Captcha guardado con éxito!</b>\n\n<i>{text_input}</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "token_verifying": "🔄 <b>Verificando token de bot con los servidores de Telegram...</b>",
        "token_success": "✅ <b>¡Token recibido y verificado correctamente!</b>\nInstancia de réplica conectada a la base de datos de The Bunker.\n\n🛡️ <i>Cloud Media Management</i>",
        "token_error": "❌ <b>Token de bot inválido.</b> Asegúrate de que el bot exista en @BotFather, que hayas copiado el token completo y que no haya sido revocado.\n\n🛡️ <i>Cloud Media Management</i>",
        "phone_requesting": "🔄 <b>Solicitando código oficial de Telegram...</b>\nPor favor espera unos segundos.",
        "phone_sent": "📩 <b>¡Código Oficial Enviado!</b>\n\nTelegram ha enviado un código de acceso a tu app oficial asociada al número <code>{phone}</code>.\n\n<b>Escribe el código numérico recibido a continuación:</b>\n<i>(Ejemplo: 49281)</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "phone_error": "❌ <b>Error al enviar código:</b>\n\n{reason}\n\nAsegúrate de incluir el código de país (ejemplo: <code>+573001234567</code>).",
        "code_verifying": "🔄 <b>Verificando código y autorizando Centinela...</b>",
        "sentinel_success": "💎 <b>¡Centinela Propio Conectado con Éxito!</b>\n\n• <b>Comunidad:</b> Blindada con tu propia cuenta\n• <b>Radar de Transmisiones:</b> Activo 24/7 en la nube\n• <b>Aislamiento Total:</b> Operando sin riesgo de baneo global\n\n<i>Asegúrate de haber añadido tu cuenta al grupo con permiso de Administrar Videollamadas.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "sentinel_error": "❌ <b>Error al inicializar la sesión.</b> Inténtalo nuevamente desde el menú.",
        "clone_disc": "🛑 <b>Bot Clon desconectado con éxito.</b>\nLa instancia ha sido detenida y liberada del clúster.",
        "twofa_required": "🔐 <b>Verificación en Dos Pasos (2FA) Requerida</b>\n\nTu cuenta de Telegram tiene activada una contraseña en la nube.\n\n<b>Ingresa tu contraseña de verificación en dos pasos:</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "code_invalid": "❌ <b>Código inválido o expirado.</b>\nVerifica el código recibido en tu aplicación oficial de Telegram e inténtalo nuevamente.",
        "twofa_verifying": "🔄 <b>Validando contraseña 2FA...</b>",
        "twofa_invalid": "❌ <b>Contraseña de 2FA incorrecta.</b>\nVerifica tu clave e inténtalo nuevamente.",
        "sched_updated": "✅ <b>¡Horario Actualizado!</b>\n\n• Inicio: <code>{start}</code>\n• Cierre: <code>{end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "sched_err": "⚠️ Formato incorrecto. Usa el formato HH:MM-HH:MM (Ejemplo: <code>20:00-23:30</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "wl_success": "✅ <b>¡Usuario registrado en la Whitelist con éxito!</b>\n\n• <b>Identificador:</b> <code>{target_id}</code>\n• <b>Inmunidad Táctica:</b> ACTIVA 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "id_err": "⚠️ <b>Identificador no válido.</b> Envía la ID numérica del usuario a autorizar.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_success": "✅ <b>¡Término clasificado registrado en la Blacklist con éxito!</b>\n\n• <b>Término Prohibido:</b> <code>{word}</code>\n• <b>Protocolo:</b> Purga automática y advertencias tácticas activas 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_ban": "🚫 <b>¡Directiva de Baneo Ejecutada Remotamente!</b>\n\n• <b>Usuario ID:</b> <code>{target_id}</code>\n• <b>Duración:</b> {dur_label}\n• <b>Estado:</b> Erradicado de la comunidad 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_kick": "👢 <b>¡Directiva de Expulsión Ejecutada Remotamente!</b>\n\n• <b>Usuario ID:</b> <code>{target_id}</code>\n• <b>Estado:</b> Expulsado de la comunidad 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_mute": "🔇 <b>¡Directiva de Silencio Ejecutada Remotamente!</b>\n\n• <b>Usuario ID:</b> <code>{target_id}</code>\n• <b>Duración:</b> {dur_label}\n• <b>Estado:</b> Micrófono y texto restringidos 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_unmute": "🔊 <b>¡Permisos de Voz y Chat Restaurados!</b>\n\n• <b>Usuario ID:</b> <code>{target_id}</code>\n• <b>Estado:</b> Conectado con facultades completas 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "dir_err": "❌ <b>Error al ejecutar directiva:</b>\n<code>{ex}</code>\n\nAsegúrate de que el bot tenga permisos de administrador en la comunidad seleccionada.",
        "mic_updated": "⭐ <b>¡Tarifa VIP de Micrófono Actualizada!</b>\n\n• <b>Comunidad ID:</b> <code>{group_id}</code>\n• <b>Precio por Pase 24h:</b> <code>{price_val} Stars (XTR)</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_err": "⚠️ Ingresa un número entero positivo de Stars (por ejemplo: 50).\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_updated": "🏷️ <b>¡Etiqueta VIP Actualizada con Éxito!</b>\n\n• <b>Comunidad ID:</b> <code>{group_id}</code>\n• <b>Etiqueta Nativa Asignada:</b> <code>{text_input}</code> 🟢\n\n<i>Al recibir propina de Stars o ejecutar /mic_vip, el usuario recibirá este título inamovible de forma automática.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_err": "⚠️ La etiqueta debe tener entre 1 y 16 caracteres (límite oficial de Telegram para títulos de administrador).\n\nEjemplo: <code>VIP 24/7</code> o <code>VIP Gold</code>.",
        "mod_id_err": "⚠️ <b>Objetivo inválido.</b> Envía una ID numérica válida o un @usuario.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_cancel_ret": "❌ Cancelar y Volver",
        "btn_retry": "🔄 Reintentar",
        "op_canceled": "Operación cancelada y memoria liberada.",
        "sentinel_disc": "🛑 Centinela propio desconectado con éxito.",
        "tag_pro_req": "💎 La edición de etiqueta nativa requiere nivel ULTRA PRO.",
        "al_updated_1": "AutoLower actualizado 🟢",
        "al_updated_0": "AutoLower desactivado 🔴",
        "mic_alert_set": "Tarifa configurada a {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directiva: Lista Blanca Táctica (Whitelist)</b>\n\nLas identidades registradas reciben <b>Inmunidad Absoluta</b>. Ningún filtro anti-spam, cerradura o captcha los confinará.\n\n• <b>Inmunidad Táctica:</b> ACTIVA 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directiva: Lista Negra Global (Blacklist)</b>\n\nGlosario clasificado de términos prohibidos. Cualquier coincidencia en el chat activará purga inmediata y advertencias tácticas.\n\n• <b>Protocolo:</b> Purga automática y faltas (Warns) 🔴\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Supervisión y Control de Cámaras & Videochats</b>\n\nAuditoría en vivo de estabilidad de transmisiones:\n\n• <b>Calidad de Video:</b> Alta Fidelidad y fluidez continua 🟢\n• <b>Prioridad de Transmisión:</b> Óptima (Sin cortes ni sobrecargas)\n• <b>Optimización Audiovisual Continua:</b> Activa en segundo plano para evitar cámaras congeladas y pantallas negras.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Control Remoto: AutoLower de Videollamada</b>\n\nEl Centinela atenúa el micrófono de los miembros no autorizados para mantener la sala en orden absoluto:\n\n• <b>Estado Actual:</b> {status_str}\n\nSelecciona una directiva para cambiar el comportamiento en vivo:\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>Pase VIP de Micrófono (Monetización en Stars)</b>\n\nPermite a los miembros desbloquear su voz al 100% continuo durante 24h pagando Telegram Stars (XTR).\n\n💡 <i>Ventaja Clave:</i> Al desplegar tu propio <b>Bot Clone</b> y <b>Centinela Dedicado</b>, el 100% de las Stars recaudadas por pases VIP van <b>directas a la cuenta de tu bot</b>, monetizando tu comunidad de forma totalmente automatizada.\n\n• <b>Tarifa Actual:</b> <code>{curr_price} Stars (XTR)</code> 🟢\n• <b>Etiqueta Asignada:</b> <code>{curr_tag}</code> (Nativa e inamovible)\n• <b>Duración del Pase:</b> 24 Horas automáticas\n\nSelecciona la tarifa de cobro o personaliza la etiqueta:\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Editor de Etiqueta VIP Nativa (ULTRA PRO)</b>\n\nEtiqueta actual: <code>{curr_tag}</code>\n\nEnvía en este chat privado el texto que deseas asignar automáticamente como título de administrador (máximo 16 caracteres).\n\n<i>Ejemplo: VIP 24/7, VIP Elite, Sponsor</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "mod_ask_time": "⚡ <b>Directiva de Moderación: /{sub_cmd}</b>\n\nSelecciona la duración de la directiva sobre el usuario:",
        "mod_ask_target": "🎯 <b>Configuración de Objetivo — /{sub_cmd_upper}</b>\n\nEnvía a este chat privado el <b>@usuario</b> o la <b>ID numérica</b> del miembro a ejecutar:\n\n🛡️ <i>Cloud Media Management</i>",
        "reg_ask": "📝 <b>Registro en Base de Datos</b>\n\nEnvía en este chat privado {target_name}:\n\n🛡️ <i>Cloud Media Management</i>",
        "reg_ask_wl": "la ID del usuario para Whitelist",
        "reg_ask_bl": "el término prohibido para Blacklist",
        "vcsched_prompt": "⏰ <b>Configuración de Horario VC</b>\n\nEnvía a este chat privado el intervalo de apertura y cierre en formato 24h (ejemplo: <code>20:00-23:30</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_custom_prompt": "⭐ <b>Tarifa Personalizada de Stars</b>\n\nEnvía en este chat privado el número de Stars por pase VIP de 24h (ejemplo: 75).\n\n<i>Recuerda que con tu Bot Clone las Stars ingresan de forma directa a tu balance.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "vcsched_main": "🗓️ <b>Programador de Videochats (ULTRA PRO)</b>\n\nConfigura la apertura y cierre automático de tus salas de voz:\n\n• <b>Estado:</b> {st_badge}\n• <b>Días Activos:</b> <code>{days}</code>\n• <b>Horario:</b> <code>{start} - {end}</code>\n\nSelecciona una opción para modificar los parámetros:\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_add_wl": "➕ Registrar Usuario en BD",
        "btn_add_bl": "➕ Registrar Término en BD",
        "btn_al_1": "🟢 Activar AutoLower (2%)",
        "btn_al_0": "🔴 Desactivar (Libre)",
        "btn_custom_rate": "✍️ Tarifa Personalizada",
        "btn_mictag": "🏷️ Etiqueta: {curr_tag}",
        "btn_sched_mod": "⏰ Modificar Horario (HH:MM-HH:MM)",
        "btn_sched_off": "🔴 Desactivar Cronograma",
        "btn_sched_on": "🟢 Activar Cronograma",

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
        "btn_back_eco": "🔙 Ecosistema",

        # ==========================================
        # 💎 ULTRA PRO — PANEL DE HERRAMIENTAS DE ÉLITE
        # ==========================================
        "ultra_tools_main": (
            "💎 <b>Herramientas de Élite ULTRA PRO — {group_name}</b>\n\n"
            "Control directo desde el panel sobre tus módulos de automatización de mayor nivel, sin depender de comandos de texto en el grupo:\n\n"
            "🚨 <b>Botón de Pánico:</b> Bloqueo total de emergencia ante raids.\n"
            "🎥 <b>Escudo Antinota:</b> Moderación de pantalla compartida.\n"
            "🎙️ <b>Modo Podcast:</b> Volumen dinámico y escudo antirruido.\n"
            "🌟 <b>Cola de Speakers:</b> Cola de micrófono con prioridad pagada (/speakers).\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_ultra_tools": "💎 Herramientas ULTRA",
        "ultra_lock_generic": (
            "🔒 <i>Este módulo es una capacidad avanzada de automatización y está disponible exclusivamente en el nivel ULTRA PRO.</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        # --- Botón de Pánico / Raid Lockdown ---
        "panic_menu": (
            "🚨 <b>Botón de Pánico — Bloqueo de Emergencia</b>\n\n"
            "Bloquea instantáneamente toda la comunidad ante una emergencia: los permisos por defecto se llevan a cero y los enlaces de invitación quedan revocados, congelando cualquier raid o ataque de spam masivo en curso.\n\n"
            "• <b>Estado Actual:</b> {status_str}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "panic_confirm": (
            "⚠️ <b>Confirmar Bloqueo de Emergencia</b>\n\n"
            "Esto silenciará y restringirá de inmediato a <b>todos</b> los miembros del grupo hasta que levantes el bloqueo manualmente. Úsalo únicamente durante un raid o ataque activo.\n\n"
            "¿Deseas continuar?"
        ),
        "panic_activated": "🚨 <b>BLOQUEO DE EMERGENCIA ACTIVO</b>\n\nLa comunidad ha quedado bloqueada. Toca abajo para levantarlo una vez que la amenaza haya pasado.\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_deactivated": "🟢 <b>Bloqueo levantado.</b>\n\nSe restauraron los permisos normales de la comunidad.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_panic_activate": "🚨 Activar Bloqueo",
        "btn_panic_confirm": "✅ Confirmar Bloqueo",
        "btn_panic_deactivate": "🟢 Levantar Bloqueo",
        # --- Escudo Antinota / Screen-Sharing Shield ---
        "shield_menu": (
            "🎥 <b>Escudo Antinota (Pantalla Compartida)</b>\n\n"
            "El Centinela supervisa las transmisiones de pantalla activas en el videochat y modera automáticamente difusiones no autorizadas o inapropiadas.\n\n"
            "• <b>Estado Actual:</b> {status_str}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "shield_updated_1": "🎥 Escudo Antinota activado 🟢",
        "shield_updated_0": "🎥 Escudo Antinota desactivado 🔴",
        "btn_shield_1": "🟢 Activar Escudo",
        "btn_shield_0": "🔴 Desactivar Escudo",
        # --- Modo Podcast & Audio Ducking ---
        "podcast_menu": (
            "🎙️ <b>Modo Podcast & Audio Ducking</b>\n\n"
            "Cuando un orador designado habla, los micrófonos de fondo se atenúan automáticamente (\"ducking\") para mantener la transmisión limpia, además de un escudo antirruido de fondo.\n\n"
            "• <b>Estado Actual:</b> {status_str}\n"
            "• <b>Nivel de Ducking:</b> <code>{duck_level}%</code> (volumen de fondo mientras habla un orador)\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "podcast_updated_1": "🎙️ Modo Podcast activado 🟢",
        "podcast_updated_0": "🎙️ Modo Podcast desactivado 🔴",
        "btn_podcast_1": "🟢 Activar Modo Podcast",
        "btn_podcast_0": "🔴 Desactivar",
        "btn_duck_level": "🎚️ Nivel de Ducking: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Nivel de Ducking Personalizado</b>\n\nEnvía en este chat privado un número del 1 al 90 para el porcentaje de volumen de los micrófonos de fondo mientras habla un orador (ejemplo: 15).\n\n🛡️ <i>Cloud Media Management</i>",
        "duck_updated": "🎚️ <b>¡Nivel de Ducking actualizado!</b>\n\n• <b>ID Comunidad:</b> <code>{group_id}</code>\n• <b>Nuevo Nivel:</b> <code>{duck_level}%</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "duck_err": "⚠️ Ingresa un número entero entre 1 y 90.\n\n🛡️ <i>Cloud Media Management</i>",
        # --- Cola de Speakers pagada (/speakers) ---
        "speakers_menu": (
            "🌟 <b>Cola de Speakers Pagada (/speakers)</b>\n\n"
            "Los miembros pueden pagar con Stars para saltarse la fila y obtener prioridad en la cola del micrófono en el videochat.\n\n"
            "• <b>Estado Actual:</b> {status_str}\n"
            "• <b>Tarifa de Prioridad:</b> <code>{price} Stars (XTR)</code>\n"
            "• <b>Miembros en Cola:</b> <code>{queue_count}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "speakers_updated_1": "🌟 Cola de Speakers activada 🟢",
        "speakers_updated_0": "🌟 Cola de Speakers desactivada 🔴",
        "btn_speakers_1": "🟢 Activar Cola",
        "btn_speakers_0": "🔴 Desactivar Cola",
        "btn_speakers_price": "⭐ Tarifa Prioridad: {price} Stars",
        "btn_speakers_clear": "🧹 Vaciar Cola",
        "speakers_price_prompt": "⭐ <b>Tarifa de Prioridad Personalizada</b>\n\nEnvía en este chat privado el número de Stars para saltar la fila de oradores (ejemplo: 30).\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_price_updated": "⭐ <b>¡Tarifa de Prioridad Actualizada!</b>\n\n• <b>ID Comunidad:</b> <code>{group_id}</code>\n• <b>Nueva Tarifa:</b> <code>{price} Stars (XTR)</code> 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_price_err": "⚠️ Ingresa un entero positivo de Stars (ej. 30).\n\n🛡️ <i>Cloud Media Management</i>",
        "speakers_cleared": "🧹 Cola de speakers vaciada 🟢"
    }
}

async def get_active_user_groups(bot: Bot, user_id: int) -> list:
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

async def verify_admin_privileges(callback: CallbackQuery, bot: Bot, group_id: int) -> bool:
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

def get_main_keyboard(bot_username: str, lang: str, is_clone: bool = False):
    """
    Teclado principal oficial.
    - Maestro (is_clone=False): 7 botones, incluido ⚡ Command Center (WebApp).
    - Clon (is_clone=True): oculta ÚNICAMENTE ⚡ Command Center.
    """
    t = TEXTS.get(lang, TEXTS["es"])
    add_url = f"https://t.me/{bot_username}?startgroup=true&admin=restrict_members+ban_users+delete_messages+pin_messages+manage_video_chats+promote_members"

    rows = [
        [InlineKeyboardButton(text=t["btn_add"], url=add_url)],
        [InlineKeyboardButton(text=t["btn_settings"], callback_data=f"menu_settings_{lang}")],
    ]
    if not is_clone:
        rows.append([InlineKeyboardButton(text=t["btn_saas"], web_app=WebAppInfo(url=WEBAPP_URL))])
    rows.append([
        InlineKeyboardButton(text=t["btn_id"], callback_data=f"menu_id_{lang}"),
        InlineKeyboardButton(text="🇪🇸 ES / 🇬🇧 EN", callback_data=f"lang_{'es' if lang == 'en' else 'en'}")
    ])
    rows.append([
        InlineKeyboardButton(text=t["btn_support"], callback_data=f"menu_support_{lang}"),
        InlineKeyboardButton(text=t["btn_info"], callback_data=f"menu_info_{lang}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def send_official_welcome(bot: Bot, chat_id: int, user, bot_username: str = None) -> None:
    """Bienvenida oficial única para Maestro y Clones (fuente de verdad compartida)."""
    if not bot_username:
        bot_username = (await bot.get_me()).username or "BunkerBot"
    lang = "es" if user and user.language_code and user.language_code.startswith("es") else "en"
    t = TEXTS.get(lang, TEXTS["es"])
    name = user.full_name if user else "Comandante"
    await bot.send_message(
        chat_id=chat_id,
        text=t["welcome"].format(name=name),
        reply_markup=get_main_keyboard(bot_username, lang, is_clone=is_clone_bot(bot)),
        parse_mode="HTML"
    )

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
        [InlineKeyboardButton(text=t["btn_ultra_tools"], callback_data=f"menu_ultra_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=f"🌐 {'English' if lang == 'es' else 'Español'}", callback_data=f"langpanel_{group_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_settings"], callback_data=f"menu_settings_{lang}")
        ]
    ])

def get_ultra_tools_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 " + ("Botón de Pánico" if lang == "es" else "Panic Button"), callback_data=f"panic_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎥 " + ("Escudo Antinota" if lang == "es" else "Screen-Share Shield"), callback_data=f"shield_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎙️ " + ("Modo Podcast" if lang == "es" else "Podcast Mode"), callback_data=f"podcast_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🌟 " + ("Gestión de Speakers" if lang == "es" else "Speakers Management"), callback_data=f"speakers_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

def build_ultra_lock_view(group_id: int, lang: str, feature_title: str):
    """
    Vista de muro de pago reutilizable para cualquier submódulo ULTRA PRO
    de este panel (Pánico / Escudo / Podcast / Speakers). Sigue el mismo
    patrón visual que ya usan autolower, vcsched y clone en este archivo.
    """
    t = TEXTS.get(lang, TEXTS["es"])
    lock_text = f"{feature_title}\n\n{t['ultra_lock_generic']}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])
    return lock_text, keyboard

def get_panic_keyboard(group_id: int, lang: str, status: int):
    t = TEXTS.get(lang, TEXTS["es"])
    action_btn = (
        InlineKeyboardButton(text=t["btn_panic_deactivate"], callback_data=f"panic_deactivate_{group_id}_{lang}")
        if status == 1 else
        InlineKeyboardButton(text=t["btn_panic_activate"], callback_data=f"panic_confirm_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [action_btn],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])

def get_panic_confirm_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_panic_confirm"], callback_data=f"panic_activate_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"panic_menu_{group_id}_{lang}")]
    ])

def get_shield_keyboard(group_id: int, lang: str, status: int):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_shield_1"], callback_data=f"shield_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_shield_0"], callback_data=f"shield_toggle_0_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])

def get_podcast_keyboard(group_id: int, lang: str, status: int, duck_level: int):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_podcast_1"], callback_data=f"podcast_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_podcast_0"], callback_data=f"podcast_toggle_0_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="10%", callback_data=f"podcast_duckval_10_{group_id}_{lang}"),
            InlineKeyboardButton(text="20%", callback_data=f"podcast_duckval_20_{group_id}_{lang}"),
            InlineKeyboardButton(text="30%", callback_data=f"podcast_duckval_30_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_duck_level"].format(duck_level=duck_level), callback_data=f"podcast_duckset_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])

def get_speakers_keyboard(group_id: int, lang: str, status: int, price: int):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_speakers_1"], callback_data=f"speakers_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_speakers_0"], callback_data=f"speakers_toggle_0_{group_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⭐ 20", callback_data=f"speakers_priceval_20_{group_id}_{lang}"),
            InlineKeyboardButton(text="⭐ 30", callback_data=f"speakers_priceval_30_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_speakers_price"].format(price=price), callback_data=f"speakers_priceset_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_speakers_clear"], callback_data=f"speakers_clear_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])

def get_payment_keyboard(group_id: int, lang: str, tier_level: str = "pro"):
    t = TEXTS.get(lang, TEXTS["es"])
    stars_price = "300 XTR" if tier_level == "pro" else "600 XTR"
    stars_label = f"⭐ Pagar con Stars ({stars_price})" if lang == "es" else f"⭐ Pay with Stars ({stars_price})"
    
    keyboard_rows = [
        [InlineKeyboardButton(text=stars_label, callback_data=f"inv_{tier_level}_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="💳 PayPal", url="https://paypal.me/Felipecosmic"),
            InlineKeyboardButton(text="🟡 Binance Pay", url="https://app.binance.com/uni-qr/request-to-pay?billOrderId=452404659499556864&billType=request_a_payment")
        ]
    ]

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
        [InlineKeyboardButton(text=t["btn_add_wl"], callback_data=f"cmd_wl_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_add_bl"], callback_data=f"cmd_bl_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

def get_time_selection_keyboard(action_name: str, group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    min1 = "1 Minuto" if lang == "es" else "1 Minute"
    min10 = "10 Minutos" if lang == "es" else "10 Minutes"
    h1 = "1 Hora" if lang == "es" else "1 Hour"
    h24 = "24 Horas" if lang == "es" else "24 Hours"
    d30 = "30 Días" if lang == "es" else "30 Days"
    perm = "Permanente" if lang == "es" else "Permanent"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏱️ {min1}", callback_data=f"time_{action_name}_1m_{group_id}_{lang}"), InlineKeyboardButton(text=f"⏱️ {min10}", callback_data=f"time_{action_name}_10m_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"⏰ {h1}", callback_data=f"time_{action_name}_1h_{group_id}_{lang}"), InlineKeyboardButton(text=f"⏰ {h24}", callback_data=f"time_{action_name}_24h_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"📅 {d30}", callback_data=f"time_{action_name}_30d_{group_id}_{lang}"), InlineKeyboardButton(text=f"♾️ {perm}", callback_data=f"time_{action_name}_perm_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
    ])

def get_eco_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Radar Ecosistema" if lang == "es" else "📡 Radar Ecosystem", callback_data=f"radar_eco_{group_id}"), InlineKeyboardButton(text="🎥 /cams", callback_data=f"cmd_cams_{group_id}_{lang}")],
        [InlineKeyboardButton(text="⚙️ /autolower", callback_data=f"cmd_autolower_{group_id}_{lang}"), InlineKeyboardButton(text="🗓️ Programador VC" if lang == "es" else "🗓️ VC Scheduler", callback_data=f"vcsched_menu_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text="🎙️ /mic_vip", callback_data=f"cmd_mic_{group_id}_{lang}"),
            InlineKeyboardButton(text="🏷️ Etiqueta VIP" if lang == "es" else "🏷️ VIP Tag", callback_data=f"cmd_mictag_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])

async def get_clone_keyboard(group_id: int, user_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    if tier == "ultra_pro":
        clone_info = await get_bot_clone(user_id, group_id)
        has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])

        session_info = await get_owner_session(user_id, group_id)
        has_sentinel = session_info is not None
        
        token_btn_text = "🔄 Actualizar Token @BotFather" if has_clone else "🔑 Conectar Token @BotFather"
        if lang == "en":
            token_btn_text = "🔄 Update @BotFather Token" if has_clone else "🔑 Connect @BotFather Token"

        sentinel_btn_text = "🔄 Actualizar Centinela (Teléfono) 🟢" if has_sentinel else "🎙️ Conectar Centinela Propio (Teléfono) 🔴"
        if lang == "en":
            sentinel_btn_text = "🔄 Update Sentinel (Phone) 🟢" if has_sentinel else "🎙️ Connect Own Sentinel (Phone) 🔴"

        kb = [
            [InlineKeyboardButton(text=token_btn_text, callback_data=f"clone_token_{group_id}_{lang}")],
            [InlineKeyboardButton(text=sentinel_btn_text, callback_data=f"clone_phone_{group_id}_{lang}")]
        ]

        if has_clone:
            disc_clone_text = "🛑 Desconectar Bot Clon" if lang == "es" else "🛑 Disconnect Bot Clone"
            kb.append([InlineKeyboardButton(text=disc_clone_text, callback_data=f"clone_discbot_{group_id}_{lang}")])

        if has_sentinel:
            disc_text = "🛑 Desconectar Centinela" if lang == "es" else "🛑 Disconnect Sentinel"
            kb.append([InlineKeyboardButton(text=disc_text, callback_data=f"clone_discsentinel_{group_id}_{lang}")])

        kb.append([InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
        )
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
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "BunkerBot"
        role = "CLON" if is_clone_bot(bot) else "MAESTRO"
        logging.info(f"🚀 [cmd_start INICIO] {role} | Bot ID: {bot_info.id} (@{bot_username}) | Usuario: {message.from_user.id}")
        
        lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
        t = TEXTS.get(lang, TEXTS["es"])
        
        try:
            await get_or_create_user(message.from_user.id, message.from_user.username or "Sin username", message.from_user.full_name)
        except Exception as db_ex:
            logging.error(f"❌ [cmd_start DB Error]: {db_ex}")

        # Deep link para configurar grupos directamente desde el botón en comunidad
        if command.args and command.args.startswith("gset_"):
            try:
                group_id = int(command.args.split("_")[1])
                if not await verify_admin_privileges_msg(message, bot, group_id):
                    return
                try:
                    g_name = (await bot.get_chat(group_id)).title
                except Exception:
                    g_name = "Comunidad" if lang == "es" else "Community"
                await message.answer(t["group_panel_title"].format(group_name=g_name), reply_markup=get_group_panel_keyboard(group_id, lang), parse_mode="HTML")
                return
            except Exception as g_ex:
                logging.error(f"❌ [cmd_start Deeplink Error]: {g_ex}")

        # 👑 RESPUESTA UNIVERSAL CLON/MAESTRO (misma bienvenida; el clon oculta solo ⚡ Command Center)
        await send_official_welcome(bot, message.chat.id, message.from_user, bot_username)
        logging.info(f"✅ [cmd_start ÉXITO] Matriz completa desplegada en @{bot_username} para {message.from_user.id}.")
    except Exception as e:
        logging.error(f"❌ [cmd_start ERROR CRÍTICO]: {e}", exc_info=True)

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    # La respuesta la emite CallbackAutoAnswerMiddleware
    return

@router.message(F.chat.type == "private")
async def handle_private_inputs(message: Message, bot: Bot):
    user_id = message.from_user.id
    text_input = (message.text or "").strip()
    
    lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
    t = TEXTS.get(lang, TEXTS["es"])

    if (bot.id, user_id) in CAPTCHA_STATES:
        group_id = CAPTCHA_STATES.pop((bot.id, user_id))
        await set_captcha_config(group_id, "captcha_text", text_input)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t.get("btn_back_captcha", "🔙 Volver"), callback_data=f"gset_captcha_{group_id}_{lang}")]
        ])
        await message.answer(
            t["captcha_saved"].format(text_input=text_input),
            reply_markup=back_kb,
            parse_mode="HTML"
        )
        return

    # 🔑 CAPTURA DEL TOKEN DE BOTFATHER (CON VERIFICACIÓN API Y ACTIVACIÓN DINÁMICA)
    if (bot.id, user_id) in CLONE_STATES:
        state_data = CLONE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        token = text_input
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
        ])

        if ":" in token and len(token) > 30:
            status_msg = await message.answer(t["token_verifying"], parse_mode="HTML")
            
            test_bot = Bot(token=token)
            try:
                bot_info = await test_bot.get_me()
                await test_bot.session.close()

                bot_username = bot_info.username or ""

                old_clone = await get_bot_clone(user_id, group_id)
                if old_clone and old_clone[0] and old_clone[0] != token:
                    try:
                        _call_clone_trigger("trigger_disconnect_clone", old_clone[0])
                    except Exception:
                        pass

                await register_bot_clone(user_id, group_id, token, bot_username)

                try:
                    _call_clone_trigger("trigger_dynamic_clone", token)
                except Exception:
                    pass

                try:
                    await status_msg.delete()
                except Exception:
                    pass

                success_text = (
                    f"✅ <b>¡Instancia de Réplica Conectada y Activa!</b>\n\n"
                    f"• <b>Bot Clon:</b> @{bot_username}\n"
                    f"• <b>Estado:</b> Operativo 🟢\n"
                    f"• <b>Comunidad:</b> Blindada con tu propia réplica\n\n"
                    f"<i>¡Listo! Ya puedes abrir @{bot_username} y presionar /start. Responderá con toda la interfaz de The Bunker.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                ) if lang == "es" else (
                    f"✅ <b>Replica Instance Connected and Active!</b>\n\n"
                    f"• <b>Clone Bot:</b> @{bot_username}\n"
                    f"• <b>Status:</b> Operational 🟢\n"
                    f"• <b>Community:</b> Shielded with your own replica\n\n"
                    f"<i>All set! You can now open @{bot_username} and press /start. It will respond with the full Bunker interface.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                )

                await message.answer(success_text, reply_markup=back_kb, parse_mode="HTML")
            except Exception:
                try:
                    await test_bot.session.close()
                except Exception:
                    pass
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
        else:
            await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
        return

    # 📱 PASO 1: CAPTURA DE NÚMERO DE TELÉFONO PARA CENTINELA
    if (bot.id, user_id) in SENTINEL_PHONE_STATES:
        state_data = SENTINEL_PHONE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]

        status_msg = await message.answer(t["phone_requesting"], parse_mode="HTML")

        res = await start_phone_auth(user_id, group_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "ok":
            SENTINEL_CODE_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            await message.answer(
                t["phone_sent"].format(phone=res['phone']),
                reply_markup=cancel_kb,
                parse_mode="HTML"
            )
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            error_reason = "Número telefónico no válido." if res.get("message") == "invalid_phone" else f"Telegram: {res.get('message')}"
            await message.answer(
                t["phone_error"].format(reason=error_reason),
                reply_markup=cancel_kb,
                parse_mode="HTML"
            )
        return

    # 📩 PASO 2: VERIFICACIÓN DEL CÓDIGO TELEGRÁFICO
    if (bot.id, user_id) in SENTINEL_CODE_STATES:
        state_data = SENTINEL_CODE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]

        status_msg = await message.answer(t["code_verifying"], parse_mode="HTML")

        res = await verify_phone_code(user_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "success":
            session_str = res["session_string"]
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")

        elif res["status"] == "2fa_required":
            SENTINEL_2FA_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            await message.answer(t["twofa_required"], reply_markup=cancel_kb, parse_mode="HTML")
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            await message.answer(t["code_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
        return

    # 🔐 PASO 3: VERIFICACIÓN DE CONTRASEÑA 2FA
    if (bot.id, user_id) in SENTINEL_2FA_STATES:
        state_data = SENTINEL_2FA_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]

        status_msg = await message.answer(t["twofa_verifying"], parse_mode="HTML")

        res = await verify_2fa_password(user_id, text_input)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if res["status"] == "success":
            session_str = res["session_string"]
            connected = await register_or_update_sentinel(user_id, group_id, session_str)
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            await message.answer(t["twofa_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
        return

    if (bot.id, user_id) in VC_SCHED_STATES:
        sched_data = VC_SCHED_STATES.pop((bot.id, user_id))
        group_id = sched_data["group_id"]
        mode = sched_data["mode"]
        
        current_sched = await get_vc_schedule(group_id)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"vcsched_menu_{group_id}_{lang}")]
        ])

        if mode == "times":
            if "-" in text_input and len(text_input.split("-")) == 2:
                parts = text_input.split("-")
                start = parts[0].strip()
                end = parts[1].strip()
                await set_vc_schedule(group_id, current_sched["days"], start, end, current_sched["status"])
                await message.answer(
                    t["sched_updated"].format(start=start, end=end),
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            else:
                await message.answer(t["sched_err"], reply_markup=back_kb, parse_mode="HTML")
            return

    if (bot.id, user_id) in DB_REG_STATES:
        data = DB_REG_STATES.pop((bot.id, user_id))
        reg_type = data["type"]
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
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
                    t["wl_success"].format(target_id=target_id),
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            else:
                await message.answer(t["id_err"], reply_markup=back_kb, parse_mode="HTML")
        else:
            word = text_input.lower()
            await add_to_blacklist(word)
            await message.answer(
                t["bl_success"].format(word=word),
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        return

    if (bot.id, user_id) in MOD_TARGET_STATES:
        st = MOD_TARGET_STATES.pop((bot.id, user_id))
        action = st["action"]
        group_id = st["group_id"]
        duration = st["duration"]
        dur_label = st["dur_label"]

        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
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
            await message.answer(t["mod_id_err"], reply_markup=back_kb, parse_mode="HTML")
            return

        try:
            if action == "ban":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until if until > 0 else None)
                await message.answer(
                    t["dir_ban"].format(target_id=target_id, dur_label=dur_label),
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            elif action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=target_id)
                await message.answer(
                    t["dir_kick"].format(target_id=target_id),
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
                    t["dir_mute"].format(target_id=target_id, dur_label=dur_label),
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
                    t["dir_unmute"].format(target_id=target_id),
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
        except Exception as ex:
            await message.answer(t["dir_err"].format(ex=ex), reply_markup=back_kb, parse_mode="HTML")
        return

    if (bot.id, user_id) in MIC_VIP_STATES:
        data = MIC_VIP_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            price_val = int(text_input)
            GROUP_MIC_PRICE[group_id] = price_val
            await message.answer(
                t["mic_updated"].format(group_id=group_id, price_val=price_val),
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(t["mic_err"], reply_markup=back_kb, parse_mode="HTML")
        return

    if (bot.id, user_id) in MIC_TAG_STATES:
        data = MIC_TAG_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        
        if 1 <= len(text_input) <= 16:
            GROUP_VIP_TAG[group_id] = text_input
            await message.answer(
                t["tag_updated"].format(group_id=group_id, text_input=text_input),
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(t["tag_err"], reply_markup=back_kb, parse_mode="HTML")
        return

    if (bot.id, user_id) in PODCAST_DUCK_STATES:
        data = PODCAST_DUCK_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"podcast_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and 1 <= int(text_input) <= 90:
            duck_level = int(text_input)
            GROUP_DUCK_LEVEL[group_id] = duck_level
            await disengage_podcast_ducking(group_id)
            await engage_podcast_ducking(group_id, duck_level)
            await message.answer(
                t["duck_updated"].format(group_id=group_id, duck_level=duck_level),
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(t["duck_err"], reply_markup=back_kb, parse_mode="HTML")
        return

    if (bot.id, user_id) in SPEAKER_PRICE_STATES:
        data = SPEAKER_PRICE_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"speakers_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            price_val = int(text_input)
            GROUP_SPEAKER_PRICE[group_id] = price_val
            await message.answer(
                t["speakers_price_updated"].format(group_id=group_id, price=price_val),
                reply_markup=back_kb,
                parse_mode="HTML"
            )
        else:
            await message.answer(t["speakers_price_err"], reply_markup=back_kb, parse_mode="HTML")
        return

@router.callback_query(F.data.startswith("menu_") | F.data.startswith("lang_") | F.data.startswith("langpanel_") | F.data.startswith("gpanel_") | F.data.startswith("cmd_") | F.data.startswith("pay_") | F.data.startswith("time_") | F.data.startswith("clone_") | F.data.startswith("alset_") | F.data.startswith("micval_") | F.data.startswith("reg_") | F.data.startswith("vcsched_"))
async def process_menu_navigation(callback: CallbackQuery, bot: Bot):
    for state_dict in [CAPTCHA_STATES, CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, SENTINEL_2FA_STATES, VC_SCHED_STATES, DB_REG_STATES, MOD_TARGET_STATES, MIC_VIP_STATES, MIC_TAG_STATES, PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES]:
        state_dict.pop((bot.id, callback.from_user.id), None)
    await cancel_phone_auth(callback.from_user.id)

    data = callback.data.split("_")
    action = data[0] 
    lang = data[-1] if len(data) > 1 and data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None

    if action == "lang":
        lang = data[1]
        text = TEXTS.get(lang, TEXTS["es"])["welcome"].format(name=callback.from_user.full_name)
        keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot))

    elif action == "langpanel":
        group_id = int(data[1])
        lang = data[2]
        t = TEXTS.get(lang, TEXTS["es"])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        text = t["group_panel_title"].format(group_name=g_name)
        keyboard = get_group_panel_keyboard(group_id, lang)
        
    elif action == "menu":
        target = data[1]
        if target == "main":
            text = t["welcome"].format(name=callback.from_user.full_name)
            keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot))
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
                rank_str = "Arquitecto Supremo (Inmunidad Total) ⚡" if lang == "es" else "Supreme Architect (Total Immunity) ⚡"
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
                g_name = "Comunidad" if lang == "es" else "Community"
            text = t[f"{target}_main"].format(group_name=g_name)
            keyboard = get_mod_keyboard(group_id, lang) if target == "mod" else get_eco_keyboard(group_id, lang)
        elif target == "ultra":
            group_id = int(data[2])
            if not await verify_admin_privileges(callback, bot, group_id):
                return
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            text = t["ultra_tools_main"].format(group_name=g_name)
            keyboard = get_ultra_tools_keyboard(group_id, lang)
            
    elif action == "pay":
        tier_level = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
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

        if sub == "menu" or sub == "toggle":
            if sub == "toggle":
                sched = await get_vc_schedule(group_id)
                new_st = 0 if sched["status"] == 1 else 1
                await set_vc_schedule(group_id, sched["days"], sched["start_time"], sched["end_time"], new_st)
            
            sched = await get_vc_schedule(group_id)
            st_badge = "🟢 ACTIVADO" if sched["status"] == 1 else "🔴 DESACTIVADO"
            if lang == "en":
                st_badge = "🟢 ACTIVE" if sched["status"] == 1 else "🔴 DISABLED"

            text = t["vcsched_main"].format(st_badge=st_badge, days=sched['days'], start=sched['start_time'], end=sched['end_time'])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_sched_off"] if sched["status"] == 1 else t["btn_sched_on"], callback_data=f"vcsched_toggle_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_sched_mod"], callback_data=f"vcsched_timeprompt_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub == "timeprompt":
            VC_SCHED_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang, "mode": "times"}
            await callback.message.answer(t["vcsched_prompt"], parse_mode="HTML")
            return

    elif action == "clone":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        # 🔒 Blindaje por licencia: Clonación de Bot y Centinela Dedicado son
        # exclusivos ULTRA PRO. El teclado ya oculta estos botones para Free/PRO,
        # pero se revalida aquí para no depender únicamente de la UI.
        if sub in ("token", "phone"):
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                clone_lock_text = (
                    "🧬 <b>Clonación de Bot & Centinela Dedicado (ULTRA PRO)</b>\n\n"
                    "Desplegar tu propio Bot Clon bajo token de @BotFather y vincular un Centinela Dedicado vía número de teléfono son capacidades exclusivas del nivel ULTRA PRO.\n\n"
                    "🔒 <i>Actualiza tu licencia para desbloquear infraestructura aislada, blindaje antiban y monetización directa en Stars.</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                ) if lang == "es" else (
                    "🧬 <b>Bot Cloning & Dedicated Sentinel (ULTRA PRO)</b>\n\n"
                    "Deploying your own Bot Clone under a @BotFather token and linking a Dedicated Sentinel via phone number are exclusive ULTRA PRO capabilities.\n\n"
                    "🔒 <i>Upgrade your license to unlock isolated infrastructure, anti-ban protection, and direct Stars monetization.</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gset_clone_{group_id}_{lang}")]
                ])
                try:
                    await callback.message.edit_text(clone_lock_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

        if sub == "token":
            CLONE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            await callback.message.answer(t["botfather_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            return

        elif sub == "phone":
            SENTINEL_PHONE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            await callback.message.answer(t["sentinel_phone_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            return

        elif sub == "cancel":
            for d in [CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, SENTINEL_2FA_STATES]:
                d.pop((bot.id, callback.from_user.id), None)
            await cancel_phone_auth(callback.from_user.id)
            await callback.answer(t["op_canceled"], show_alert=False)

            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])

            if tier == "ultra_pro":
                if has_clone:
                    c_user = f"@{clone_info[1]}" if clone_info[1] else ""
                    status = f"Operativo ({c_user}) 🟢" if lang == "es" else f"Operational ({c_user}) 🟢"
                else:
                    status = "No Configurado 🔴" if lang == "es" else "Not Configured 🔴"
            else:
                status = "Bloqueado 🔴" if lang == "es" else "Locked 🔴"
            
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            if lang == "en":
                sentinel_status = "Connected 🟢" if session_info else "Not Configured 🔴"
            
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang)

        elif sub == "discbot":
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            if clone_info and clone_info[0]:
                try:
                    _call_clone_trigger("trigger_disconnect_clone", clone_info[0])
                except Exception:
                    pass

            await revoke_bot_clone_db(callback.from_user.id, group_id)
            await callback.answer(t["clone_disc"], show_alert=True)

            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            status = "Desconectado 🔴" if lang == "es" else "Disconnected 🔴"

            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            if lang == "en":
                sentinel_status = "Connected 🟢" if session_info else "Not Configured 🔴"

            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang)

        elif sub == "discsentinel":
            await disconnect_sentinel(group_id)
            await revoke_owner_session(callback.from_user.id, group_id)
            await callback.answer(t["sentinel_disc"], show_alert=True)
            
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            try:
                g_name = (await bot.get_chat(group_id)).title
            except Exception:
                g_name = "Comunidad" if lang == "es" else "Community"
            
            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
            if has_clone:
                c_user = f"@{clone_info[1]}" if clone_info[1] else ""
                status = f"Operativo ({c_user}) 🟢" if lang == "es" else f"Operational ({c_user}) 🟢"
            else:
                status = "No Configurado 🔴" if lang == "es" else "Not Configured 🔴"

            sentinel_status = "Desconectado 🔴" if lang == "es" else "Disconnected 🔴"
            
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang)

    elif action == "cmd":
        sub_cmd = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        if sub_cmd == "wl":
            text = t["wl_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_add_wl"], callback_data=f"reg_wl_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "bl":
            text = t["bl_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_add_bl"], callback_data=f"reg_bl_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_mod"], callback_data=f"menu_mod_{group_id}_{lang}")]
            ])
        elif sub_cmd == "cams":
            text = t["cams_menu"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "autolower":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                al_lock_text = (
                    "🔇 <b>Radar de Transmisiones / AutoLower (ULTRA PRO)</b>\n\n"
                    "La atenuación acústica automática de micrófonos no autorizados durante las transmisiones en vivo es una capacidad avanzada del Centinela.\n\n"
                    "🔒 <i>Este módulo de radar está disponible exclusivamente en el nivel ULTRA PRO.</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                ) if lang == "es" else (
                    "🔇 <b>Stream Radar / AutoLower (ULTRA PRO)</b>\n\n"
                    "Automatic acoustic dimming of unauthorized microphones during live voice chats is an advanced Sentinel capability.\n\n"
                    "🔒 <i>This radar module is available exclusively at the ULTRA PRO tier.</i>\n\n"
                    "🛡️ <i>Cloud Media Management</i>"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
                ])
                try:
                    await callback.message.edit_text(al_lock_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

            curr_al = await get_autolower_status(group_id)
            status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
            if lang == "en":
                status_str = "🟢 ACTIVE (2% for unauthorized)" if curr_al == 1 else "🔴 DISABLED (Free Mics)"
            text = t["al_menu"].format(status_str=status_str)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=t["btn_al_1"], callback_data=f"alset_1_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_al_0"], callback_data=f"alset_0_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mic":
            curr_price = GROUP_MIC_PRICE.get(group_id, 50)
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            text = t["mic_menu"].format(curr_price=curr_price, curr_tag=curr_tag)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_custom_rate"], callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mictag":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                await callback.answer(t["tag_pro_req"], show_alert=True)
                return
            
            MIC_TAG_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            await callback.message.answer(t["tag_menu_prompt"].format(curr_tag=curr_tag), parse_mode="HTML")
            return
        elif sub_cmd in ["ban", "mute"]:
            text = t["mod_ask_time"].format(sub_cmd=sub_cmd)
            keyboard = get_time_selection_keyboard(sub_cmd, group_id, lang)
        elif sub_cmd in ["kick", "unmute"]:
            MOD_TARGET_STATES[(bot.id, callback.from_user.id)] = {
                "action": sub_cmd, "group_id": group_id, "duration": 0, 
                "dur_label": "Inmediato" if lang == "es" else "Immediate", "lang": lang
            }
            await callback.message.answer(t["mod_ask_target"].format(sub_cmd_upper=sub_cmd.upper()), parse_mode="HTML")
            return

    elif action == "reg":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        DB_REG_STATES[(bot.id, callback.from_user.id)] = {"type": sub, "group_id": group_id, "lang": lang}
        target_name = t["reg_ask_wl"] if sub == "wl" else t["reg_ask_bl"]
        await callback.message.answer(t["reg_ask"].format(target_name=target_name), parse_mode="HTML")
        return

    elif action == "alset":
        new_st = int(data[1])
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            await callback.answer(
                "🔒 El Radar AutoLower requiere licencia ULTRA PRO." if lang == "es" else "🔒 AutoLower Radar requires the ULTRA PRO license.",
                show_alert=True
            )
            return
        await set_autolower_status(group_id, new_st)
        await callback.answer(t["al_updated_1"] if new_st == 1 else t["al_updated_0"])
        curr_al = await get_autolower_status(group_id)
        status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
        if lang == "en":
            status_str = "🟢 ACTIVE (2% for unauthorized)" if curr_al == 1 else "🔴 DISABLED (Free Mics)"
        text = t["al_menu"].format(status_str=status_str)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_al_1"], callback_data=f"alset_1_{group_id}_{lang}"),
                InlineKeyboardButton(text=t["btn_al_0"], callback_data=f"alset_0_{group_id}_{lang}")
            ],
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])

    elif action == "micval":
        sub_val = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        if sub_val == "custom":
            MIC_VIP_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            await callback.message.answer(t["mic_custom_prompt"], parse_mode="HTML")
            return
        else:
            price_int = int(sub_val)
            GROUP_MIC_PRICE[group_id] = price_int
            await callback.answer(t["mic_alert_set"].format(price_int=price_int), show_alert=True)
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            text = t["mic_menu"].format(curr_price=price_int, curr_tag=curr_tag)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"micval_25_{group_id}_{lang}"),
                    InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"micval_50_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text="⭐ 100 Stars", callback_data=f"micval_100_{group_id}_{lang}"),
                    InlineKeyboardButton(text=t["btn_custom_rate"], callback_data=f"micval_custom_{group_id}_{lang}")
                ],
                [
                    InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")
                ],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])

    elif action == "time":
        sub_cmd = data[1]
        dur_str = data[2]
        group_id = int(data[3])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        
        dur_map = {
            "1m": (60, "1 Minuto" if lang == "es" else "1 Minute"),
            "10m": (600, "10 Minutos" if lang == "es" else "10 Minutes"),
            "1h": (3600, "1 Hora" if lang == "es" else "1 Hour"),
            "24h": (86400, "24 Horas" if lang == "es" else "24 Hours"),
            "30d": (2592000, "30 Días" if lang == "es" else "30 Days"),
            "perm": (0, "Permanente" if lang == "es" else "Permanent")
        }
        sec, label = dur_map.get(dur_str, (0, "Permanente" if lang == "es" else "Permanent"))
        MOD_TARGET_STATES[(bot.id, callback.from_user.id)] = {
            "action": sub_cmd, "group_id": group_id, "duration": sec, 
            "dur_label": label, "lang": lang
        }
        await callback.message.answer(t["mod_ask_target"].format(sub_cmd_upper=f"{sub_cmd.upper()} ({label})"), parse_mode="HTML")
        return

    elif action == "gpanel":
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
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
    data = callback.data.split("_")
    action = data[0]

    # 🩹 FIX: los prefijos de un solo token (gset, astog, togcap, capval, afset...)
    # quedan correctamente aislados en data[0]. Pero "cap_set_*" es un prefijo
    # COMPUESTO por dos tokens ("cap" + "set"): con action = data[0], quedaba
    # reducido a "cap" y el `elif action == "cap_set":` de más abajo nunca
    # coincidía, dejando mudos los sub-botones de tiempo límite, castigo,
    # mensaje personalizado y borrado de servicio del Captcha. Se normaliza
    # aquí, antes de cualquier despacho, para que el resto de los índices
    # (data[1]=="set", data[2]==sub, data[-2]==group_id, data[-1]==lang) seguidos
    # más abajo permanezcan exactamente iguales a como ya estaban escritos.
    if callback.data.startswith("cap_set_"):
        action = "cap_set"

    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    if action == "gset" and len(data) == 2 and data[1].lstrip("-").isdigit():
        group_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
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
            media_lbl = "Bloqueado 🟢" if lang == "es" else "Locked 🟢"
            media_free = "Permitido 🔴" if lang == "es" else "Allowed 🔴"
            media = media_lbl if await get_lock_status(group_id, "lock_media") == 1 else media_free
            stickers = media_lbl if await get_lock_status(group_id, "lock_stickers") == 1 else media_free
            links = media_lbl if await get_lock_status(group_id, "lock_links") == 1 else media_free
            commands = media_lbl if await get_lock_status(group_id, "lock_commands") == 1 else media_free
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
                g_name = "Comunidad" if lang == "es" else "Community"

            clone_info = await get_bot_clone(callback.from_user.id, group_id)
            has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])

            if tier == "ultra_pro":
                if has_clone:
                    c_user = f"@{clone_info[1]}" if clone_info[1] else ""
                    status = f"Operativo ({c_user}) 🟢" if lang == "es" else f"Operational ({c_user}) 🟢"
                else:
                    status = "No Configurado 🔴" if lang == "es" else "Not Configured 🔴"
            else:
                status = "Bloqueado 🔴" if lang == "es" else "Locked 🔴"
            
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            if lang == "en":
                sentinel_status = "Connected 🟢" if session_info else "Not Configured 🔴"
            
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
            if lang == "en":
                info = "ℹ️ Free Tier: 3 daily purges limit." if tier == "free" else f"⭐ Tier {tier.upper()}: Automated monthly quota."
            await callback.answer(info, show_alert=True)
            return
        elif sub == "main":
            if tier == "free":
                err_msg = "⚠️ La limpieza del chat principal requiere nivel PRO o ULTRA." if lang == "es" else "⚠️ Main chat cleanup requires PRO or ULTRA tier."
                await callback.answer(err_msg, show_alert=True)
                return
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
        elif sub == "cmds":
            if tier == "free":
                err_msg = "⚠️ El bloqueo de comandos a regulares requiere nivel PRO o ULTRA." if lang == "es" else "⚠️ Command blocking to regulars requires PRO or ULTRA tier."
                await callback.answer(err_msg, show_alert=True)
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

        media_lbl = "Bloqueado 🟢" if lang == "es" else "Locked 🟢"
        media_free = "Permitido 🔴" if lang == "es" else "Allowed 🔴"
        media = media_lbl if await get_lock_status(group_id, "lock_media") == 1 else media_free
        stickers = media_lbl if await get_lock_status(group_id, "lock_stickers") == 1 else media_free
        links = media_lbl if await get_lock_status(group_id, "lock_links") == 1 else media_free
        commands = media_lbl if await get_lock_status(group_id, "lock_commands") == 1 else media_free

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

    elif action == "togcap" or action == "togmode":
        if action == "togcap":
            await set_captcha_status(group_id, 1 if data[1] == "on" else 0)
        else:
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
            prompt = "⏱️ <b>Configuración de Tiempo Límite</b>\n\nSelecciona el tiempo máximo que tiene el recluta para resolver el desafío:" if lang == "es" else "⏱️ <b>Time Limit Configuration</b>\n\nSelect the maximum time the recruit has to solve the challenge:"
            await callback.message.edit_text(prompt, reply_markup=get_captcha_time_keyboard(group_id, lang), parse_mode="HTML")
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

            CAPTCHA_STATES[(bot.id, callback.from_user.id)] = group_id
            prompt = "✍️ <b>Editor de Captcha — Mensaje Personalizado</b>\n\nEnvía en este chat privado el mensaje que se enviará al usuario al ingresar.\n\n🛡️ <i>Cloud Media Management</i>" if lang == "es" else "✍️ <b>Captcha Editor — Custom Message</b>\n\nSend in this private chat the message that will be sent to the user upon joining.\n\n🛡️ <i>Cloud Media Management</i>"
            await callback.message.answer(prompt, parse_mode="HTML")
        elif sub == "srvdel":
            cfg = await get_captcha_config(group_id)
            await set_captcha_config(group_id, "captcha_service_del", 0 if cfg["service_del"] == 1 else 1)
            cfg_updated = await get_captcha_config(group_id)
            
            if callback.message.text and ("Service" in callback.message.text or "Purga" in callback.message.text or "Purge" in callback.message.text or "Centro" in callback.message.text):
                tier = await get_effective_group_tier(group_id, callback.from_user.id)
                quota_desc = "3 purgas de servicio diarias (Plan Básico)" if tier == "free" else "Purga automatizada ilimitada (PRO / ULTRA)"
                if lang == "en":
                    quota_desc = "3 service purges daily (Free Plan)" if tier == "free" else "Advanced automated monthly purge (PRO / ULTRA)"
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
        prompt = f"<b>{t['af_msgs']}</b>\nSelecciona el límite numérico:" if lang == "es" else f"<b>{t['af_msgs']}</b>\nSelect the numerical limit:"
        if sub_mode != "msgs":
            prompt = f"<b>{t['af_time']}</b>\nSelecciona la ventana en segundos:" if lang == "es" else f"<b>{t['af_time']}</b>\nSelect the window in seconds:"
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

# ==========================================
# 💎 ULTRA PRO — HERRAMIENTAS DE ÉLITE
# (Botón de Pánico, Escudo Antinota, Modo Podcast/Ducking, Cola de Speakers)
#
# Dispatcher independiente, igual en espíritu al de "gset_/astog_/..." más
# arriba: aislado del árbol principal de process_menu_navigation para no
# tocar su regex ni su lógica y así blindar los menús ya existentes
# (Captcha, Cerraduras, Antispam, etc.) contra cualquier regresión.
# ==========================================
@router.callback_query(
    F.data.startswith("panic_") | F.data.startswith("shield_") |
    F.data.startswith("podcast_") | F.data.startswith("speakers_")
)
async def cb_ultra_tools_dispatch(callback: CallbackQuery, bot: Bot):
    for state_dict in [PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES]:
        state_dict.pop((bot.id, callback.from_user.id), None)

    data = callback.data.split("_")
    module = data[0]           # panic | shield | podcast | speakers
    sub = data[1]               # menu | toggle | confirm | activate | ...
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None

    # ------------------------------------------------------------
    # 🚨 BOTÓN DE PÁNICO / RAID LOCKDOWN
    # ------------------------------------------------------------
    if module == "panic":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🚨 <b>Botón de Pánico</b>" if lang == "es" else "🚨 <b>Panic Button</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_panic_status(group_id)
            status_str = "🚨 BLOQUEADO" if status == 1 else "🟢 Normal"
            if lang == "en":
                status_str = "🚨 LOCKED DOWN" if status == 1 else "🟢 Normal"
            text = t["panic_menu"].format(status_str=status_str)
            keyboard = get_panic_keyboard(group_id, lang, status)
        elif sub == "confirm":
            text = t["panic_confirm"]
            keyboard = get_panic_confirm_keyboard(group_id, lang)
        elif sub == "activate":
            await set_panic_status(group_id, 1)
            try:
                await execute_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo al ejecutar el lockdown en {group_id}: {ex}")
            text = t["panic_activated"]
            keyboard = get_panic_keyboard(group_id, lang, 1)
        elif sub == "deactivate":
            await set_panic_status(group_id, 0)
            try:
                await lift_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo al levantar el lockdown en {group_id}: {ex}")
            text = t["panic_deactivated"]
            keyboard = get_panic_keyboard(group_id, lang, 0)

    # ------------------------------------------------------------
    # 🎥 ESCUDO ANTINOTA / SCREEN-SHARING SHIELD
    # ------------------------------------------------------------
    elif module == "shield":
        if sub == "toggle":
            new_status = int(data[2])
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🎥 <b>Escudo Antinota</b>" if lang == "es" else "🎥 <b>Screen-Share Shield</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_shield_status(group_id)
            status_str = ("🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO") if lang == "es" else ("🟢 ACTIVE" if status == 1 else "🔴 DISABLED")
            text = t["shield_menu"].format(status_str=status_str)
            keyboard = get_shield_keyboard(group_id, lang, status)
        elif sub == "toggle":
            await set_shield_status(group_id, new_status)
            try:
                if new_status == 1:
                    await engage_screen_shield(group_id)
                else:
                    await disengage_screen_shield(group_id)
            except Exception as ex:
                logging.error(f"❌ [Shield] Fallo al aplicar el escudo en {group_id}: {ex}")
            await callback.answer(t["shield_updated_1"] if new_status == 1 else t["shield_updated_0"])
            status = await get_shield_status(group_id)
            status_str = ("🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO") if lang == "es" else ("🟢 ACTIVE" if status == 1 else "🔴 DISABLED")
            text = t["shield_menu"].format(status_str=status_str)
            keyboard = get_shield_keyboard(group_id, lang, status)

    # ------------------------------------------------------------
    # 🎙️ MODO PODCAST & AUDIO DUCKING
    # ------------------------------------------------------------
    elif module == "podcast":
        if sub in ("toggle", "duckval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🎙️ <b>Modo Podcast & Audio Ducking</b>" if lang == "es" else "🎙️ <b>Podcast Mode & Audio Ducking</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_podcast_status(group_id)
            duck_level = GROUP_DUCK_LEVEL.get(group_id, 20)
            status_str = ("🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO") if lang == "es" else ("🟢 ACTIVE" if status == 1 else "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level)
        elif sub == "toggle":
            new_status = int(val)
            await set_podcast_status(group_id, new_status)
            duck_level = GROUP_DUCK_LEVEL.get(group_id, 20)
            try:
                if new_status == 1:
                    await engage_podcast_ducking(group_id, duck_level)
                else:
                    await disengage_podcast_ducking(group_id)
            except Exception as ex:
                logging.error(f"❌ [Podcast] Fallo al aplicar el ducking en {group_id}: {ex}")
            await callback.answer(t["podcast_updated_1"] if new_status == 1 else t["podcast_updated_0"])
            status = await get_podcast_status(group_id)
            status_str = ("🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO") if lang == "es" else ("🟢 ACTIVE" if status == 1 else "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level)
        elif sub == "duckval":
            duck_level = int(val)
            GROUP_DUCK_LEVEL[group_id] = duck_level
            status = await get_podcast_status(group_id)
            if status == 1:
                try:
                    await disengage_podcast_ducking(group_id)
                    await engage_podcast_ducking(group_id, duck_level)
                except Exception as ex:
                    logging.error(f"❌ [Podcast] Fallo al actualizar el ducking en {group_id}: {ex}")
            await callback.answer(t["duck_updated"].format(group_id=group_id, duck_level=duck_level), show_alert=True)
            status_str = ("🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO") if lang == "es" else ("🟢 ACTIVE" if status == 1 else "🔴 DISABLED")
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level)
        elif sub == "duckset":
            PODCAST_DUCK_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            await callback.message.answer(t["duck_custom_prompt"], parse_mode="HTML")
            return

    # ------------------------------------------------------------
    # 🌟 COLA DE SPEAKERS PAGADA (/speakers)
    # ------------------------------------------------------------
    elif module == "speakers":
        if sub in ("toggle", "priceval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🌟 <b>Cola de Speakers Pagada</b>" if lang == "es" else "🌟 <b>Paid Speakers Queue</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = "🟢 ACTIVA" if lang == "es" else "🟢 ACTIVE"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price)
        elif sub == "toggle":
            new_status = int(val)
            await callback.answer(t["speakers_updated_1"] if new_status == 1 else t["speakers_updated_0"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = ("🟢 ACTIVADA" if new_status == 1 else "🔴 DESACTIVADA") if lang == "es" else ("🟢 ACTIVE" if new_status == 1 else "🔴 DISABLED")
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, new_status, price)
        elif sub == "priceval":
            price = int(val)
            GROUP_SPEAKER_PRICE[group_id] = price
            await callback.answer(t["speakers_price_updated"].format(group_id=group_id, price=price), show_alert=True)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = "🟢 ACTIVA" if lang == "es" else "🟢 ACTIVE"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price)
        elif sub == "priceset":
            SPEAKER_PRICE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            await callback.message.answer(t["speakers_price_prompt"], parse_mode="HTML")
            return
        elif sub == "clear":
            try:
                await clear_speaker_queue(group_id)
            except Exception as ex:
                logging.error(f"❌ [Speakers] Fallo al vaciar la cola en {group_id}: {ex}")
            await callback.answer(t["speakers_cleared"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            status_str = "🟢 ACTIVA" if lang == "es" else "🟢 ACTIVE"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=0)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price)

    if text and keyboard:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")